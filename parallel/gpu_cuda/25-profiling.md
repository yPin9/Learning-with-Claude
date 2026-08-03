# Ch 25 — Profiling：Nsight Systems / Nsight Compute / Roofline 判讀

> **目標**：掌握 CUDA profiling 的標準工作流（先 nsys 找瓶頸，再 ncu 深挖）；能看懂 Nsight Compute 的關鍵 metric（occupancy、memory throughput、compute throughput、warp stall reason）；理解 roofline chart 並用它判斷 kernel 是 memory-bound 還是 compute-bound；知道常用的 ncu 指令。

> **環境**：CUDA 12.x, Colab T4 (sm_75)。Nsight 數字均為「Colab 預期行為，未在本機實測」，附 Colab 執行步驟。

---

你現在有了一整套優化工具：coalescing（[Ch 18](./18-memory-coalescing.md)）、bank conflict 消除（[Ch 19](./19-bank-conflict.md)）、occupancy 調整（[Ch 20](./20-occupancy-vs-ilp.md)）、divergence 消除（[Ch 21](./21-warp-divergence.md)）、reduction 優化（[Ch 22](./22-atomics-reduction.md)）、stream overlap（[Ch 23](./23-streams-async.md)）。但不知道**從哪裡下手**。

Profiling 解決的就是這個問題：先找到瓶頸在哪，再針對性地優化。「猜測並優化」幾乎都是在浪費時間；「量測並優化」才是正確流程。

---

## 一、工具分工：nsys vs. ncu

NVIDIA 的現代 profiling 工具分兩層：

```
[Nsight Systems (nsys)]   ← 全局視角
   ↓ 找到慢的 kernel
[Nsight Compute (ncu)]    ← 單 kernel 深挖
```

### Nsight Systems（nsys）— 時間軸，找瓶頸

nsys 錄製整個程式的**時間軸**，你能看到：
- 每個 CUDA kernel 佔多少時間
- H2D / D2H 傳輸的時間
- 哪個 kernel 最佔時間（排行榜）
- Stream overlap 是否真的發生（[Ch 23](./23-streams-async.md) 提到的驗證）
- CPU 和 GPU 活動的對應關係

nsys 的 overhead 很低（通常 < 5%），適合整個程式跑完後快速診斷。

### Nsight Compute（ncu）— Metric 深挖，找原因

ncu 針對**單一 kernel** 做深度分析，能告訴你：
- Memory throughput vs. 理論峰值（是不是 memory-bound？）
- Compute throughput（是不是 compute-bound？）
- Warp stall 原因（thread 在等什麼？）
- Occupancy（實際 vs. 理論上限）
- Roofline 位置

ncu 的 overhead 極高（10x–100x）——它要重放 kernel 多次來蒐集不同的 hardware counter，不要在整個程式上用，只在「nsys 找到的慢 kernel」上用。

---

## 二、工作流：兩階段 Profiling

### 第一階段：nsys 找瓶頸 kernel

```bash
# 錄製（Colab 預期行為，未在本機實測）
nsys profile \
    --trace=cuda,nvtx \
    --stats=true \
    -o my_app \
    ./my_cuda_binary

# 或 Python 程式
nsys profile --trace=cuda -o my_app python my_script.py
```

產生 `my_app.nsys-rep`。用 Nsight Systems GUI 打開（也可以在 Colab 輸出 txt 報告）：

```bash
# 在 Colab 輸出純文字摘要（不需要 GUI）
nsys stats my_app.nsys-rep
```

文字輸出範例（Colab 預期）：

```
CUDA Kernel Statistics:
 Time(%)  Total Time (ns)  Instances  Avg (ns)  Name
 ───────────────────────────────────────────────────
   72.3%      123,456,789       1000   123,456  reduce_smem_shfl
   18.1%       30,987,654        500    61,975  matmul_tiled
    5.2%        8,901,234        100    89,012  memset_kernel
    4.4%        7,654,321         50   153,086  [CUDA memcpy HtoD]
```

**看什麼**：哪個 kernel 的 Total Time 最大，那就是第一個要深挖的目標。

### 第二階段：ncu 深挖目標 kernel

```bash
# 蒐集完整 metric set（會很慢，只對一個 kernel 用）
ncu --set full \
    --kernel-name reduce_smem_shfl \
    -o my_kernel \
    ./my_cuda_binary

# 只看特定 metric（快一些）
ncu --metrics \
    sm__throughput.avg.pct_of_peak_sustained_elapsed,\
    l1tex__t_bytes_pipe_lsu_mem_global_op_ld.sum.per_second,\
    gpu__compute_memory_throughput.avg.pct_of_peak_sustained_elapsed,\
    smsp__warps_issue_stalled_long_scoreboard.avg.pct_of_issue_slots \
    --kernel-name reduce_smem_shfl \
    ./my_cuda_binary
```

---

## 三、關鍵 Metric 逐一解讀

### 3.1 Achieved Occupancy

```
Achieved Occupancy: 62.4%
Theoretical Occupancy: 75.0%
```

**Achieved occupancy**：在這次執行中，SM 上平均有幾個 active warp / 最大支援的 warp 數。

**Theoretical occupancy**：根據 kernel 的 register 和 shared memory 用量算出的上限（[Ch 11](./11-occupancy.md) 講的那個）。

如果 achieved 遠低於 theoretical：
- Warp divergence 讓部分 warp 停頓（[Ch 21](./21-warp-divergence.md)）
- Unbalanced workload（有的 block 提早結束）

如果 theoretical 本來就低（例如 37.5%）：
- Register 或 shared memory 用太多，每 SM 只能放少數 block
- 可能需要 `__launch_bounds__` 或減少 register 使用

**重要提醒**：Occupancy 不是越高越好（[Ch 20](./20-occupancy-vs-ilp.md) 講過）。如果 kernel 是 compute-bound + 高 ILP，50% occupancy 也能跑滿。

### 3.2 Memory Throughput

```
L1/TEX Cache Throughput:   87.3% of peak
L2 Cache Throughput:       42.1% of peak
DRAM Throughput:           91.2% of peak
```

**T4 的 DRAM 峰值**：~320 GB/s（HBM2），但實測有效頻寬約 250–280 GB/s。

**看什麼**：
- DRAM Throughput 接近 100% → kernel 是 memory-bound，瓶頸在 global memory bandwidth
- DRAM Throughput 很低但 Compute 很低 → 可能 warp stall，找 stall reason
- L2 高但 DRAM 低 → L2 cache 命中率很好，kernel 可能 cache-friendly

### 3.3 Compute Throughput

```
SM [%] Throughput:  34.5%
FP32 Active Cycles: 28.1%
```

`SM [%] Throughput`（也叫 `sm__throughput.avg.pct_of_peak_sustained_elapsed`）：SM 在這個 kernel 執行期間的整體利用率。

FP32 throughput 低而 memory throughput 高 → 典型的 memory-bound kernel。

### 3.4 Warp Stall Reason

這是找優化方向最有用的 metric。Warp 沒在執行的原因（Stall Reason）：

```
Stall - Long Scoreboard:   45.3%  ← 等 global memory 讀取（L2/DRAM miss）
Stall - Short Scoreboard:   8.2%  ← 等 shared memory 或 L1 miss
Stall - Synchronization:   12.1%  ← 等 __syncthreads
Stall - No Instruction:    18.7%  ← 沒有 instruction 可以 issue（ILP 不足？）
Stall - Memory Throttle:    6.4%  ← L2/DRAM 進出口堵塞
Stall - Wait:               9.3%  ← 等依賴前一條指令的結果
```

**解讀**：
- `Long Scoreboard` 高 → global memory 存取是瓶頸，想辦法提高 coalescing（[Ch 18](./18-memory-coalescing.md)）、用 shared memory tiling（[Ch 17](./17-shared-memory-tiling.md)）、或增加 occupancy 讓更多 warp 覆蓋 latency
- `Synchronization` 高 → `__syncthreads` 頻繁，考慮 warp shuffle 取代（[Ch 22](./22-atomics-reduction.md)）
- `No Instruction` 高 → 編譯器 ILP 不夠，或 kernel 本來就很短，嘗試 loop unrolling

---

## 四、Roofline Chart：判斷 Memory-Bound vs. Compute-Bound

Roofline model（[Ch 2](./02-parallelism-ceilings.md) 介紹過概念）在 ncu 裡有視覺化工具。

### Roofline 的坐標系

```
Peak FP32 Throughput
│ (T4: 65.1 TFLOPS)
│
│               /‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾ Compute-bound ceiling
Performance    /
(GFLOP/s)    /          ● 你的 kernel（compute-bound）
            /
           /  ● 你的 kernel（memory-bound）
          /
─────────/──────────────────────────────── Arithmetic Intensity
        Ridge point                       (FLOP / byte)
(= peak TFLOPS / peak GB/s
 = 65100 / 320 ≈ 203 FLOP/byte for T4)
```

**Arithmetic Intensity（算術強度）**：你的 kernel 每讀 1 byte 的 global memory 做幾個 FLOP。

```
算術強度 = FLOP 數 / global memory 存取 byte 數

例：向量加法 C[i] = A[i] + B[i]
  讀 A[i] 和 B[i] → 2 * 4 bytes = 8 bytes
  做 1 次加法 → 1 FLOP
  算術強度 = 1/8 = 0.125 FLOP/byte  （非常 memory-bound）

例：矩陣乘法（naive）N x N
  FLOP = 2N³，memory access = 2N² * 4 bytes (A 和 B 各一份)
  強度 = 2N³ / (8N²) = N/4  （N = 1024 → 強度 = 256 FLOP/byte，compute-bound）
```

### 在 ncu 裡看 Roofline

```bash
# ncu 的 roofline 需要特定 metric set
ncu --set roofline \
    --kernel-name my_kernel \
    -o roofline_report \
    ./my_binary

# 打開 Nsight Compute GUI，File → Open → 選 .ncu-rep
# 或在 ncu 指令列顯示：
ncu --import roofline_report.ncu-rep --page roofline
```

你的 kernel 會作為一個點標在圖上。點在斜線（memory roof）下方 = memory-bound；點在水平線（compute roof）下方但已超過 ridge point = compute-bound。

### 判斷流程

```
算一下你的 kernel 的算術強度：
  強度 < 203 FLOP/byte（T4 ridge point）？
     → 很可能 memory-bound
     → 看 DRAM Throughput（應該高）
     → 優化方向：減少 global memory 存取（tiling、fusion）

  強度 > 203 FLOP/byte？
     → 很可能 compute-bound
     → 看 FP32 throughput（應該高）
     → 優化方向：更多 ILP、Tensor Core、向量化

  強度 > 203 但兩個 throughput 都低？
     → Warp stall 是主因（看 stall reason）
     → 不是 bandwidth 問題也不是 ALU 問題
```

---

## 五、常用 ncu 指令

```bash
# 看所有可用 metric 的列表
ncu --list-metrics

# 快速看概覽（常用）
ncu --set default ./binary

# 完整 metric（慢，但全面）
ncu --set full ./binary

# 只蒐集 roofline 相關 metric
ncu --set roofline ./binary

# 只分析特定 kernel（名稱）
ncu --kernel-name my_kernel_name ./binary

# 只分析第幾次 launch（避免 warmup 污染）
ncu --launch-skip 1 --launch-count 1 ./binary

# 輸出成檔案供 GUI 使用
ncu -o output_report ./binary
# 產生 output_report.ncu-rep

# 在 terminal 顯示 summary（不需要 GUI）
ncu --print-summary per-kernel ./binary

# 看特定 metric 的值
ncu --metrics \
    "sm__throughput.avg.pct_of_peak_sustained_elapsed,\
     l1tex__t_bytes_pipe_lsu_mem_global_op_ld.sum.per_second,\
     smsp__warps_issue_stalled_long_scoreboard.avg.pct_of_issue_slots" \
    ./binary
```

**常用 metric 名稱速查**：

| 我想看 | Metric 名稱 |
|--------|------------|
| SM 整體利用率 | `sm__throughput.avg.pct_of_peak_sustained_elapsed` |
| Global memory read bandwidth | `l1tex__t_bytes_pipe_lsu_mem_global_op_ld.sum.per_second` |
| DRAM read bandwidth | `dram__bytes_read.sum.per_second` |
| FP32 FLOP rate | `smsp__sass_thread_inst_executed_op_ffma_pred_on.sum.per_cycle_elapsed` |
| Achieved occupancy | `sm__warps_active.avg.pct_of_peak_sustained_active` |
| Long scoreboard stall | `smsp__warps_issue_stalled_long_scoreboard.avg.pct_of_issue_slots` |
| Sync stall | `smsp__warps_issue_stalled_barrier.avg.pct_of_issue_slots` |

Metric 名稱在不同 CUDA 版本略有變化；用 `ncu --list-metrics | grep keyword` 確認你的版本的正確名稱。

---

## 六、Colab 完整 Profiling 工作流

（Colab 預期行為，未在本機實測）

```python
# Step 1：在 Colab 安裝 Nsight（T4 的 Colab 通常已預裝）
# !apt-get install -y nvidia-nsight

# Step 2：寫你的 CUDA 程式到 .cu 檔
# %%writefile my_kernel.cu
# #include <cuda_runtime.h>
# ...

# Step 3：編譯
# !nvcc -O2 -arch=sm_75 -o my_binary my_kernel.cu

# Step 4：用 nsys 跑（產生時間軸）
# !nsys profile --trace=cuda --stats=true -o nsys_report ./my_binary

# Step 5：看 nsys 文字摘要（找最慢的 kernel）
# !nsys stats nsys_report.nsys-rep 2>&1 | head -50

# Step 6：對最慢的 kernel 用 ncu（指定 kernel name）
# !ncu --set default --kernel-name your_kernel_name \
#      -o ncu_report ./my_binary

# Step 7：下載 .ncu-rep 到本機，用 Nsight Compute GUI 打開
# from google.colab import files
# files.download('ncu_report.ncu-rep')

# 或直接在 terminal 看文字輸出：
# !ncu --print-summary per-kernel ./my_binary
```

---

## 七、實例：診斷 Reduction Kernel

用 Ch 22 的 `reduce_smem`（純 shared memory 版，尚未加 warp shuffle）做診斷示範：

**預期的 ncu 輸出**（Colab T4 預期行為，未在本機實測）：

```
Section: GPU Speed Of Light Throughput
─────────────────────────────────────────────────────────────────
Metric Name                    Metric Value   Metric Unit
─────────────────────────────────────────────────────────────────
DRAM Throughput                       8.4 %          %
L1/TEX Cache Throughput              45.3 %          %
SM [%] Throughput                    22.1 %          %
FP32 Throughput                       3.2 %          %
─────────────────────────────────────────────────────────────────

Section: Warp State Statistics
─────────────────────────────────────────────────────────────────
Stall - Synchronization             52.3 %          %   ← 主要瓶頸！
Stall - Long Scoreboard             18.7 %          %
Stall - No Instruction               9.1 %          %
─────────────────────────────────────────────────────────────────

Achieved Occupancy                   81.2 %          %
```

**解讀**：

1. DRAM Throughput 只有 8.4%：reduction kernel 對 global memory 的存取本來就少（每個 thread 只讀一次，寫一次 atomic），不是 memory-bound。
2. Sync Stall 52.3%：這是主要瓶頸。每輪 reduction 都要 `__syncthreads`，thread 大量等待。
3. FP32 Throughput 只有 3.2%：計算量很少（reduction 主要是加法），compute 不是瓶頸。
4. 優化方向：減少 `__syncthreads` 次數 → 用 warp shuffle 替代最後幾輪（[Ch 22](./22-atomics-reduction.md) / [練習 D](./practice-d-reduction.md)）。

---

## 八、踩雷

**1. 用 ncu 對整個程式而不是特定 kernel**

ncu 會把每個 kernel 都重放多次蒐集 counter，整個程式可能需要幾分鐘到幾十分鐘。永遠用 `--kernel-name` 鎖定目標。

**2. Warmup 污染 profiling 結果**

第一次跑的 kernel 通常因為 JIT compile（[Ch 26](./26-compilation-pipeline.md) 的 PTX JIT）或 cache cold start 而比較慢。用 `--launch-skip 1 --launch-count 1` 跳過第一次。

**3. 只看 Occupancy，忽略 Stall Reason**

高 Occupancy 不等於快。如果 Stall - Long Scoreboard 是 80%，表示 thread 都在等 global memory，增加 occupancy 沒用——你需要改善存取樣式。

**4. 混淆算術強度的分子分母**

算術強度 = FLOP / **global memory byte**，不是 total memory access。如果你有 shared memory access，那不算在分母裡（shared memory access 不消耗 DRAM 頻寬）。

**5. Roofline 的 peak 數字用錯**

T4 的 FP32 peak 是 65.1 TFLOPS（不是 INT8 或 TF32 的數字）；DRAM bandwidth 是 ~320 GB/s（理論值），實測有效頻寬約 250–280 GB/s。Roofline 的 ridge point 用 DRAM 峰值算，但你的 kernel 能拿到的實際 bandwidth 比 peak 低——比對時要有合理的期望。

---

## 九、進階：NVTX Markers 讓 nsys 更好讀

```cuda
#include <nvtx3/nvToolsExt.h>

// 用 marker 標記你程式的各個階段
nvtxRangePushA("H2D Transfer");
cudaMemcpy(d_data, h_data, size, cudaMemcpyHostToDevice);
nvtxRangePop();

nvtxRangePushA("Main Kernel");
myKernel<<<grid, block>>>(d_data, d_out, n);
nvtxRangePop();

nvtxRangePushA("D2H Transfer");
cudaMemcpy(h_out, d_out, size, cudaMemcpyDeviceToHost);
nvtxRangePop();
```

在 nsys 時間軸上，這些 marker 會顯示為彩色標記，讓你一眼看出哪個階段對應哪段 GPU 活動，而不是看一堆 kernel 名稱猜測。

編譯時加 `-lnvToolsExt` 或 CUDA 12 的 nvtx3 header-only 版本：
```bash
nvcc -I/path/to/nvtx3 -arch=sm_75 -o binary my.cu
```

---

## 本章重點

- 工作流：nsys 看全局時間軸找最慢 kernel → ncu 深挖那個 kernel
- nsys overhead 低（< 5%），ncu overhead 極高（10x+），用途不同
- 關鍵 metric：achieved occupancy、DRAM throughput、FP32 throughput、warp stall reason
- Roofline：算術強度低 = memory-bound，高 = compute-bound；T4 的 ridge point ≈ 203 FLOP/byte
- Stall reason 是診斷優化方向的最直接線索：Long Scoreboard = global memory latency；Synchronization = `__syncthreads` 過多
- 常用指令：`nsys profile --stats`、`ncu --set full`、`ncu --kernel-name`

---

## 自我檢核

1. nsys 和 ncu 各自適合哪個問題？為什麼不直接對整個程式用 ncu？
2. Stall - Long Scoreboard 達 70% 意味著什麼？你的第一個優化動作是什麼？
3. T4 的 ridge point 是多少 FLOP/byte？你的向量加法 kernel（1 FLOP / 8 bytes）落在哪個區間？
4. Achieved Occupancy = 50% 但 DRAM Throughput = 95%，你的優化方向是什麼（不要再提高 occupancy）？
5. 你的 reduction kernel 顯示 Sync Stall = 55%，下一步具體要做什麼改動？

---

## 延伸閱讀

1. **Nsight Compute 官方文件：Kernel Profiling Guide** — [官方](https://docs.nvidia.com/nsight-compute/ProfilingGuide/index.html)
   - Metric 名稱對照表、Roofline model 解釋、每個 section 的解讀指引。這是 ncu 使用的一手文件。

2. **Nsight Systems 官方文件：User Guide** — [官方](https://docs.nvidia.com/nsight-systems/UserGuide/index.html)
   - nsys CLI 的完整選項；NVTX 整合；如何在 Colab 上使用。

3. **NVIDIA Developer Blog, "Using the NVIDIA Nsight Developer Tools"** — [連結](https://developer.nvidia.com/blog/using-nvidia-nsight-developer-tools/)
   - nsys + ncu 協作的實際工作流範例，有截圖。入門讀這篇比直接看文件更快。

4. **NVIDIA Developer Blog, "Roofline Performance Model for Efficient GPU Kernels"** — [連結](https://developer.nvidia.com/blog/roofline-performance-model-for-efficient-gpu-kernels/)
   - 如何在 ncu 裡看 Roofline，算術強度怎麼算，memory-bound vs compute-bound 的決策樹。

5. **Mark Harris, "CUDA Pro Tip: Nvprof is Your Handy Universal GPU Profiler"** — NVIDIA Developer Blog
   - 歷史資料（nvprof 已被 ncu/nsys 取代），但 metric 分析邏輯和 stall reason 的解讀方法依然適用。

---

→ [練習 D：reduction 七版優化](./practice-d-reduction.md)
