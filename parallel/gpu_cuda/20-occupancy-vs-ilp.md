# Ch 20 — Occupancy vs ILP：破解「越滿越好」的迷思

> **目標**：理解 occupancy 不是越高越好，掌握 ILP（Instruction-Level Parallelism）作為藏延遲的替代路徑，學會 thread coarsening 技巧，並能在 memory-bound kernel 中正確取捨。
>
> **環境**：CUDA 12.x, Colab T4 (sm_75)，`nvcc --ptxas-options=-v`，Nsight Compute 2024+

---

## 為什麼需要這章（回連 Ch 11）

[Ch 11](./11-occupancy.md) 介紹了 occupancy——SM 上實際活躍 warp 數相對於硬體上限的比例。我們學到的結論是「occupancy 太低不行」，CUDA Occupancy Calculator 也整天催我們把 occupancy 推高。

但問題來了：**occupancy 高真的一定好嗎？**

實務中常見這樣的情況：你把 thread block size 從 128 調到 256，occupancy 從 50% 升到 75%，但 kernel 變慢了。或者你用 thread coarsening 讓 occupancy 掉到 25%，結果 throughput 反而衝上去。

這章要解釋為什麼。不是靠直覺，是靠數字。

---

## 先建立直覺：GPU 藏延遲的兩條路

### GPU 的延遲問題

訪問 global memory 的延遲大約是 **~400–800 cycles**（T4 約 500 cycles）。在這段等待期間，執行 global memory load 的 warp 必須等 scoreboard 清空才能繼續往下跑。如果整個 SM 只有一個 warp 在跑，這個 warp 一發 load 就卡 500 cycles，吞吐率直接崩。

GPU 的解法：**同時讓多個 warp in-flight**，scheduler 在某個 warp 等記憶體時切到另一個 warp 繼續跑。這是 GPU throughput 架構的核心設計，完全不同於 CPU 的亂序執行（OOO）+ 分支預測路線。

### 第一條路：高 Occupancy

傳統思維：

```
occupancy 高 → active warp 多 → scheduler 有更多 warp 可切換 → 延遲被藏住
```

這個邏輯沒錯，但它是充分條件的「一種」實現方式，不是唯一方式。

### 第二條路：ILP（Instruction-Level Parallelism）

換個角度：scheduler 關心的不是「有幾個 warp」，而是「有幾個 **in-flight 操作**」。只要 scoreboard 裡塞滿足夠多的 pending 操作，scheduler 就能藏住延遲——就算只有少數幾個 warp。

ILP 的路徑：

```
每個 thread 發出更多獨立指令（e.g., 多個 load）
→ 每個 warp 在 scoreboard 裡占更多位置
→ 即使 warp 數量少，in-flight 操作總數仍然夠多
→ 延遲被藏住
```

這兩條路的本質差異：

| 路徑 | 手段 | 代價 |
|------|------|------|
| 高 Occupancy | 增加 warp 數量 | 每 warp 可用 register 減少 |
| ILP | 每 warp 發更多獨立指令 | register 用量增加 → occupancy 下降 |

兩者存在**直接衝突**：register 預算是固定的，你用在塞更多 warp 上，就無法用在讓每個 warp 算更多東西上。

---

## Little's Law：把問題變成數字

### 公式

Little's Law 來自排隊論：

```
L = λ × W
```

- `L`：系統中平均存在的請求數（in-flight operations）
- `λ`：單位時間到達率（throughput，ops/cycle）
- `W`：每個請求的平均等待時間（latency，cycles）

翻譯成 CUDA：

```
需要的 in-flight ops = 每 cycle 能發出的 ops 數 × 記憶體延遲（cycles）
```

### T4 的具體數字

T4 (sm_75) 的規格：

- 每個 SM 有 4 個 warp scheduler，每 cycle 每個 scheduler 可以發出一條指令 → **4 ops/cycle**
- Global memory 延遲：**~500 cycles**
- 需要的 in-flight ops：`4 × 500 = 2000 ops`

現在算每個 warp 貢獻多少 in-flight ops：

- 每個 warp 32 個 thread
- 如果每個 thread 有 1 個獨立 load → **每 warp 32 in-flight ops**
- 需要的 warp 數：`2000 / 32 = 62.5 個 active warp`

T4 每個 SM 最多 **32 個 warp**（2048 threads，每 warp 64 threads 的說法是錯的；warp size 恆為 32，所以是 2048/32 = 64 warp，但 sm_75 Turing warp scheduler 每 SM 上限通常文件寫 32 active warp per scheduler × 4 schedulers = 實際依 register/shared memory 限制）。

實際上 T4 每 SM 最多 1024 threads（依 block 配置），假設 occupancy 100%（32 warp）：

```
32 warp × 32 ops/warp × 1 load/thread = 1024 in-flight ops
```

距離 2000 ops 目標還有一半缺口。怎麼補？

**讓每個 thread 發出 2 個獨立 load**：

```
32 warp × 32 thread/warp × 2 independent loads/thread = 2048 in-flight ops
```

剛好超過 2000 的目標。這就是 ILP 的貢獻。

### 重要推論

只要每個 thread 能提供足夠的獨立指令，**即使 occupancy 降到 50%，16 warp 也能藏住延遲**——前提是每個 thread 有 4 個獨立 load。

```
16 warp × 32 thread/warp × 4 independent loads/thread = 2048 in-flight ops ✓
```

occupancy 減半，但 ILP 加倍，效果等價。

---

## Volkov 的洞見：實測數據說話

### GTC 2010 的發現

Vasily Volkov 在 "Better Performance at Lower Occupancy"（GTC 2010，slides 公開於 developer.nvidia.com）中，在 Fermi GPU（sm_20）上做了以下實驗：

取 DGEMM（雙精度矩陣乘法）kernel：

- **標準版**：每個 thread 算一個輸出，occupancy 高
- **Coarsened 版**：每個 thread 算多個輸出（thread coarsening），每個 thread 持有更多 register 中的中間值，能發出更多獨立指令

結果（引自 Volkov GTC 2010 slides）：

> 在 Fermi 上，thread coarsening 讓每個 SM 的 active warp 數從約 100% occupancy 降到 25% occupancy，但 kernel 的 memory bandwidth 利用率從 ~60% 升到 **接近 90%+**。

核心洞見（Volkov 原話的意思）：

> "The key metric is not occupancy but **the number of independent memory operations in flight**."

以及：

> "Occupancy is a proxy for latency hiding, but ILP is an equally valid and often more powerful proxy."

Volkov 的同一系列工作還包括 Volkov & Demmel, "LU, QR and Cholesky Factorizations using Vector Capabilities of GPUs"（SC 2008），更早論證了在 GPU 上 ILP 的重要性。

### 現代 GPU 的情況

Turing（sm_75）和 Ampere（sm_80/86）的 warp scheduler 設計延續了同樣的原則。Nsight Compute 的 "Warp State Statistics" 面板會顯示 `warp_wait_instrs`——如果你的 kernel 主要是因為 memory 而 stall（而不是 compute），那 ILP 優化就有空間。

---

## Thread Coarsening 實作

### 概念

Thread coarsening（線程粗化）：原本 N 個 element 由 N 個 thread 各算一個，改成 N/K 個 thread 每個算 K 個 element。

效果：
- 每個 thread 需要 K 倍的 register 存放 K 個中間結果
- 每個 thread 發出 K 個獨立 load 指令
- K 個 load 在 scoreboard 中可以並行 pending → K 倍 ILP
- 每個 block 需要的 thread 減少 → register 用量反而升高 per thread → occupancy 下降

### 標準版：每 thread 1 element

```cuda
// 每個 thread 只處理 1 個 element
__global__ void vector_add_v1(
    const float* __restrict__ A,
    const float* __restrict__ B,
    float* __restrict__ C,
    int N
) {
    int tid = blockIdx.x * blockDim.x + threadIdx.x;
    if (tid < N) {
        C[tid] = A[tid] + B[tid];  // 1 load A, 1 load B, 1 store C
    }
}

// 呼叫端
void launch_v1(float* A, float* B, float* C, int N) {
    int threads = 256;
    int blocks = (N + threads - 1) / threads;
    vector_add_v1<<<blocks, threads>>>(A, B, C, N);
}
```

在 scoreboard 裡，每個 warp 只有 2 個 pending load（A 和 B）。

### Coarsened 版：每 thread 4 elements

```cuda
// 每個 thread 處理 COARSE_FACTOR 個 element
#define COARSE_FACTOR 4

__global__ void vector_add_v2(
    const float* __restrict__ A,
    const float* __restrict__ B,
    float* __restrict__ C,
    int N
) {
    // 注意：grid size 要對應縮小 COARSE_FACTOR 倍
    int base = (blockIdx.x * blockDim.x + threadIdx.x) * COARSE_FACTOR;

    // 4 個獨立 load A（可以在 scoreboard 中同時 pending）
    float a0, a1, a2, a3;
    float b0, b1, b2, b3;

    // 先發出所有 load，讓它們同時在飛
    if (base + 0 < N) a0 = A[base + 0];
    if (base + 1 < N) a1 = A[base + 1];
    if (base + 2 < N) a2 = A[base + 2];
    if (base + 3 < N) a3 = A[base + 3];

    if (base + 0 < N) b0 = B[base + 0];
    if (base + 1 < N) b1 = B[base + 1];
    if (base + 2 < N) b2 = B[base + 2];
    if (base + 3 < N) b3 = B[base + 3];

    // compute（等 load 完成後）
    if (base + 0 < N) C[base + 0] = a0 + b0;
    if (base + 1 < N) C[base + 1] = a1 + b1;
    if (base + 2 < N) C[base + 2] = a2 + b2;
    if (base + 3 < N) C[base + 3] = a3 + b3;
}

// 呼叫端：注意 grid size 要縮小 COARSE_FACTOR 倍
void launch_v2(float* A, float* B, float* C, int N) {
    int threads = 256;
    // 每個 thread 算 COARSE_FACTOR 個 element，所以 block 數要除以 COARSE_FACTOR
    int blocks = (N + threads * COARSE_FACTOR - 1) / (threads * COARSE_FACTOR);
    vector_add_v2<<<blocks, threads>>>(A, B, C, N);
}
```

**關鍵：ILP 的前提是 load 指令之間必須獨立**。上面的寫法先把所有 load 都發出去，讓它們同時在 scoreboard 中 pending，再做 compute。如果你寫成「load a0 → compute a0+b0 → load a1 → compute a1+b1」，就是串行的，沒有 ILP。

### 為什麼 load 能同時 pending

NVIDIA GPU 的 scoreboard 追蹤每條指令的 source/destination register。`a0 = A[base+0]` 和 `a1 = A[base+1]` 的目標 register 不同、source 也不同（只是 base 不同，base 是常數偏移），所以 scoreboard 判定它們互不相依，可以同時發出。

實際上現代 nvcc 在看到這樣的模式時會盡量排程成這種交錯形式，但明確地先寫 load 再寫 compute 仍然是最可靠的方式。

### 效能預期

在 memory-bound kernel（如 vector add）上，理論上 4× ILP 可以在較低 occupancy 下達到與高 occupancy 相當甚至更好的 bandwidth 利用率。

**理論預期，實測請在 Colab 用 Nsight Compute 驗證**（實際提升受 L2 cache、TLP/ILP 比例、N 大小等影響）。

---

## Register Pressure vs Occupancy：工具分析

### 查看 Register 用量

```bash
nvcc -arch=sm_75 -O2 --ptxas-options=-v your_kernel.cu -o kernel
```

輸出類似：

```
ptxas info    : Used 32 registers, 512 bytes smem, 360 bytes cmem[0]
```

`Used 32 registers` 是每個 thread 用的 register 數。T4 (sm_75) 每個 SM 有 65536 個 32-bit register。

計算 occupancy：

```
每個 thread 32 registers
每個 block 256 threads → 每個 block 8192 registers
65536 / 8192 = 8 個 block per SM（register 角度）
T4 每 SM 最多 16 block（硬體限制）
實際限制由 register：8 block × 256 threads = 2048 threads → 100% occupancy（如果 shared memory 不是瓶頸）
```

Coarsened 版每個 thread 用 ~56 registers（8 個 float + loop overhead）：

```
每個 block 256 threads → 每個 block 14336 registers
65536 / 14336 ≈ 4 個 block per SM
4 × 256 = 1024 threads → 50% occupancy
```

occupancy 掉了一半，但每個 thread 有 4× ILP。

### `__launch_bounds__`

告訴編譯器目標 block size，讓它控制 register 分配：

```cuda
// 告訴編譯器：block size 最多 256 threads，每 SM 最少 4 個 block
__global__
__launch_bounds__(256, 4)   // maxThreadsPerBlock, minBlocksPerMultiprocessor
void my_kernel(...) {
    // ...
}
```

`minBlocksPerMultiprocessor = 4` 會讓 nvcc 知道最少需要 4 個 block per SM 的 occupancy，從而控制 register 用量不超過 `65536 / (4 × 256) = 64` 個 register per thread。

如果實際需要的 register 超過這個預算，nvcc 會做 **register spill**（溢出到 local memory，等同 L1/L2 的 global memory），代價非常高。

### Nsight Compute 查看 Occupancy

```bash
ncu --metrics sm__warps_active.avg.pct_of_peak_sustained_active \
    --metrics l1tex__t_bytes.sum.per_second \
    ./kernel
```

或直接用 GUI 的 "Occupancy" section，它會列出：

- `Achieved Occupancy`：實際達到的 occupancy
- `Theoretical Occupancy`：理論上根據 register/smem 能達到的最大值
- 瓶頸原因（register? shared memory? block size?）

---

## Memory-bound vs Compute-bound：判斷先行

### 為什麼要判斷

ILP/coarsening 的邏輯建立在「kernel 是 memory-bound」的前提上。如果 kernel 是 compute-bound，那管線已經被算術指令填滿了，降 occupancy 只是讓 warp 變少，沒有任何好處。

### Arithmetic Intensity（算術強度）

```
AI = FLOP / Bytes read from memory
```

以 vector add 為例：

- 讀 A + 讀 B + 寫 C = 3 × N × 4 bytes（float32）= 12N bytes
- 計算：N 次加法 = N FLOP
- AI = N / (12N) = **0.083 FLOP/Byte**

T4 的規格：

- Peak FP32 throughput：~8.1 TFLOPS
- Peak Memory Bandwidth：~300 GB/s
- Ridge point（山脊點）：8100 GFLOPS / 300 GB/s = **27 FLOP/Byte**

vector add 的 AI = 0.083，遠低於 27，**嚴重 memory-bound**。ILP 有效。

矩陣乘法（GEMM）的 AI = O(N)（N 是矩陣維度），通常 >> 27，**compute-bound**。ILP 對吞吐率幫助有限。

### Roofline Model 簡述

Roofline model（Williams et al., SC 2009）把 kernel 畫在 AI vs Performance 的圖上：

```
Performance
(TFLOPS)
    |         /‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾  ← compute bound（受 FP 峰值限制）
    |        /
    |       /  ← memory bound
    |      /    （受 bandwidth 限制）
    |_____/___________________________
                        AI (FLOP/Byte)
    ridge point →  27 FLOP/Byte（T4）
```

你的 kernel 在左邊（AI < 27）：memory-bound，優化 bandwidth → ILP/coarsening 有效。
在右邊（AI > 27）：compute-bound，優化 FLOP 效率 → 算術指令排程、tensor core、減少 warp divergence。

### Nsight Compute 量測

```bash
ncu --metrics sm__throughput.avg.pct_of_peak_sustained_elapsed \
    --metrics gpu__dram_throughput.avg.pct_of_peak_sustained_elapsed \
    ./kernel
```

- `sm__throughput` 高（> 70%）、`dram_throughput` 低 → compute-bound
- `dram_throughput` 高（> 70%）、`sm__throughput` 低 → memory-bound
- 兩者都低 → latency-bound（需要更多 TLP 或 ILP）

---

## 對比取捨表格

| 情境 | 優先策略 | 理由 |
|------|----------|------|
| Memory-bound，每 thread 1 load，低 occupancy | 提高 occupancy（增加 block/SM） | 靠 TLP 藏延遲 |
| Memory-bound，已達高 occupancy，仍 latency-bound | Thread coarsening（增加 ILP） | TLP 已到頂，改用 ILP |
| Compute-bound | 優化算術效率（tensor core/減少 warp divergence） | 延遲不是瓶頸 |
| Register 是 occupancy 瓶頸 | 考慮 coarsening 或 `__launch_bounds__` | register 反正要用，不如用在 ILP |
| Shared memory 是 occupancy 瓶頸 | 減少 smem 用量（改 register tiling） | 讓更多 block 能上 SM |
| 超高 occupancy 但 cache miss rate 高 | 適度降 block size 改善 locality | 有時 cache 效果比 TLP 更關鍵 |

---

## 踩雷

### 踩雷 1：盲目 coarsening 導致 register spill

COARSE_FACTOR 設 8 以上，每個 thread 需要的 register 可能超過硬體上限（T4 每 thread 最多 255 registers），nvcc 會自動 spill 到 local memory（實際是 L1/L2/DRAM），每次 spill load 都是一次 global memory access，代價遠超過 occupancy 下降帶來的損失。

**解法**：用 `--ptxas-options=-v` 確認 register 用量在可接受範圍。

### 踩雷 2：coarsening 沒有調整 grid size

標準版 `blocks = (N + 255) / 256`，coarsened 版 (`COARSE_FACTOR=4`) 必須改成 `blocks = (N + 255 * 4) / (256 * 4)`。如果忘了調整，grid 中的 thread 總數不變，每個 thread 還是只算 1 個 element（後三個是越界存取），coarsening 沒有任何效果，還多了 if-bound check 的 overhead。

### 踩雷 3：`__launch_bounds__` 設太嚴導致 spill

`__launch_bounds__(256, 8)` 要求每 SM 至少 8 個 block，register 預算被壓到 `65536 / (8 × 256) = 32` 個。如果 kernel 實際需要 48 個 register，nvcc 被逼 spill，結果反而更慢。

**解法**：先量 register 用量，再設 `minBlocksPerMultiprocessor`，不要瞎猜。

### 踩雷 4：忽略 L2 cache 效果

高 occupancy 有時不只是幫助藏延遲——更多 warp 同時存取不同 cache line 可能增加 L2 hit rate（temporal locality 效果）。某些情況下，降 occupancy 雖然 ILP 夠，但 L2 miss rate 升高，實際頻寬反而下降。

**解法**：用 Nsight Compute 的 `l2_read_hit_rate` 指標確認 L2 cache 行為。

### 踩雷 5：compute-bound kernel 硬套 ILP 邏輯

向量點積（dot product accumulation）是 compute-bound 的典型——每個 FLOP 需要的 register 和指令都很多。如果你在這類 kernel 上做 coarsening 降 occupancy，只是讓 SM 上的 warp 更少，算術管線 utilization 反而下降。

**判斷**：先跑 Nsight Compute 的 Roofline Analysis，確定是 memory-bound 再考慮 ILP。

---

## 進階：`__launch_bounds__` 完整 Workflow

推薦的迭代流程：

```
1. 量 baseline：ncu --metrics achieved_occupancy,sm__throughput,dram_throughput kernel
2. 確認是 memory-bound（dram_throughput 高）
3. 查 register 用量（--ptxas-options=-v）
4. 如果 register 是 occupancy 瓶頸 → 試 thread coarsening
5. 每次 coarsening 後重新量三個指標
6. 找到 throughput 最高點，停止
7. 如果擔心 spill，加 __launch_bounds__ 限制
```

Nsight Compute 的 "Memory Workload Analysis" 和 "Warp State Statistics" 是最直接的診斷工具：

- "Warp State Statistics" → `Stall No Instruction`（warp 沒指令可發）說明 TLP 不足
- "Stall Long Scoreboard"（等 global memory）說明需要更多 in-flight ops → ILP 的機會

---

## 動手練習

1. 在 Colab T4 上實作 `vector_add_v1`（COARSE_FACTOR=1）和 `vector_add_v2`（COARSE_FACTOR=4），N = 128M floats。
   - 用 `nvcc --ptxas-options=-v` 比較兩個 kernel 的 register 用量
   - 用 `cudaEventRecord` 計時，比較 GB/s
   - 用 Nsight Compute 確認 achieved occupancy 和 dram_throughput

2. 把 `vector_add_v2` 的 COARSE_FACTOR 從 1 掃到 8，畫出 throughput 曲線，找出最佳點和開始 spill 的點。

3. 實作 element-wise sigmoid（`1/(1+exp(-x))`）的 coarsened 版，這是 compute-bound kernel。確認 ILP 對它是否有幫助（根據 Nsight Compute roofline 判斷）。

4. 用 Nsight Compute 的 Roofline Analysis，把你的 `vector_add_v1` 和 `v2` 畫在同一張 roofline 圖上，確認它們都在 memory-bound 區域且 v2 更接近峰值頻寬。

---

## 本章重點

- Occupancy 不是越高越好，它只是藏延遲（latency hiding）的一種手段。
- 另一種手段是 ILP：每個 thread 發出更多獨立指令，讓 scoreboard 塞滿 in-flight ops。
- Little's Law 給出定量需求：`in-flight ops = bandwidth × latency`。T4 需要約 2000 個 in-flight ops；32 warp × 4 independent loads/thread 可以達到。
- Volkov GTC 2010 實測驗證：在 Fermi 上 occupancy 從 100% 降到 25%，靠 ILP 讓 bandwidth 利用率從 ~60% 升到 ~90%+（Volkov, "Better Performance at Lower Occupancy", GTC 2010）。
- Thread coarsening 是 ILP 的主要實作手段：每個 thread 算多個 element，多個 load 並行 pending。
- 代價是 register 增加 → occupancy 下降 → 有 spill 風險。要用 `--ptxas-options=-v` 確認。
- Grid size 計算在 coarsening 後必須除以 COARSE_FACTOR，這是最常見的 bug。
- 只有 memory-bound kernel 才值得考慮 ILP 優化；compute-bound kernel 用 roofline 先確認再動手。

---

## 自我檢核

1. Little's Law 的三個變數（L, λ, W）對應 CUDA 的什麼？在 T4 上如果 global memory 延遲 500 cycles、每 cycle 發 4 ops、每 warp 只有 1 個 independent load，需要幾個 active warp 才能完全藏住延遲？

2. Thread coarsening 把每個 thread 算的 element 從 1 改成 4，register 用量大約增加幾倍？occupancy 大約變成幾分之幾？（假設 register 是 occupancy 的唯一瓶頸）

3. `vector_add_v2` 裡的 `base` 計算和 grid size 有什麼關係？如果 N = 1024，COARSE_FACTOR = 4，blockDim = 256，blocks 應該是多少？

4. 如何用 Nsight Compute 判斷一個 kernel 是 memory-bound 還是 compute-bound？說出至少兩個具體 metric。

5. `__launch_bounds__(256, 8)` 的兩個參數各是什麼意思？如果實際 kernel 需要 48 個 register，這個設定會發生什麼事？

---

## 延伸閱讀

1. **Vasily Volkov, "Better Performance at Lower Occupancy", GTC 2010** — developer.nvidia.com（slides 可在 NVIDIA GTC 2010 archive 找到）。ILP vs occupancy 的開山論文，必讀。

2. **CUDA C++ Best Practices Guide, "Occupancy" 章 + "Thread and Block Heuristics"** — docs.nvidia.com。官方對 occupancy 的定量建議，包含 CUDA Occupancy Calculator 的使用說明。

3. **Vasily Volkov & James W. Demmel, "LU, QR and Cholesky Factorizations using Vector Capabilities of GPUs"** (SC 2008) — 比 GTC 2010 更早的論文，從線性代數 kernel 的角度論證 ILP 的重要性。

4. **Nsight Compute Roofline Analysis documentation** — docs.nvidia.com/nsight-compute。如何用 `ncu --section roofline` 量測 AI 並把 kernel 定位在 roofline 圖上，找出真正的瓶頸。

5. **Samuel Williams, Andrew Waterman, David Patterson, "Roofline: An Insightful Visual Performance Model for Multicore Architectures"** (CACM 2009) — Roofline model 的原始論文，雖然針對多核 CPU，但分析框架完全適用於 GPU。

---

→ [Ch 21 warp divergence 消除](./21-warp-divergence.md)

[回 Ch 9 記憶體階層](./09-memory-hierarchy.md) | [回 Ch 10 warp 與 SIMT 執行](./10-warp-simt-execution.md) | [回 Ch 11 occupancy](./11-occupancy.md) | [回 Ch 17 shared memory 與 tiling](./17-shared-memory-tiling.md)
