# Ch 7 — GPU 架構總覽

> **目標**：建立 GPU 硬體層次的完整心智模型——從 GigaThread Engine 到 SM 內部的 processing block，理解軟體抽象（grid/block/warp/thread）如何映射到實體硬體，並掌握 T4（Turing sm_75）的精確規格。
>
> **環境**：任何有 T4 或 Turing-class GPU 的機器；概念部分不需要 GPU，`nvidia-smi -q` 可驗證部分數字。

---

## 為什麼需要這章

前六章我們一直在「用」GPU——寫 kernel、啟動 grid、測量記憶體頻寬。但我們一直迴避一個問題：這些抽象底下跑的是什麼機器？

這個問題不是學術的。當你發現某個 kernel 跑得比預期慢 3 倍，你需要知道是 warp divergence 還是 shared memory bank conflict 還是 register spill——而這三個詞在你沒有 SM 架構的概念時都是廢話。

這章給你硬體地圖。數字是真實的 T4 數字，不是示意圖的「N 個」。

---

## 先建立直覺

### GPU 不是「很多個 CPU core」

常見的誤解：GPU 有 2560 個 CUDA core，所以它是有 2560 個「小 CPU」的機器。

這是錯的。正確的比喻是：

```
CPU（4-32 core）                   GPU（T4：40 SM）
─────────────────────────         ─────────────────────────
每 core 有獨立 control logic       一個 SM 有 4 個 warp scheduler
每 core 有大 cache                 每 SM 共享 96KB L1/Shared
core 之間可獨立執行不同指令         一個 SM 的 processing block
分支預測 + OOO 執行                 內 16 個 thread 同時走同一指令
適合「深度」任務（少分支+複雜邏輯）  適合「廣度」任務（大量獨立資料）
```

真正的差別在 **SIMT（Single Instruction Multiple Threads）**：一條指令由一群 thread 同時執行，每個 thread 操作自己的資料。這不是 SIMD（同一 register 打包多個資料），而是同一條指令廣播給多條獨立的執行流。

### 全域架構 ASCII 圖

```
                    ┌─────────────────────────────────┐
                    │          T4 GPU                  │
                    │                                  │
                    │  ┌─────────────────────────┐    │
                    │  │   GigaThread Engine      │    │
                    │  │  （block → SM 分派器）   │    │
                    │  └──────────┬──────────────┘    │
                    │             │ dispatch blocks    │
                    │    ┌────────┴────────────────┐  │
                    │    │   40 個 SM               │  │
                    │    │  ┌─────┐  ┌─────┐       │  │
                    │    │  │ SM0 │  │ SM1 │  ...  │  │
                    │    │  └─────┘  └─────┘       │  │
                    │    └─────────────────────────┘  │
                    │                                  │
                    │  L2 Cache：4MB（全 GPU 共享）    │
                    │  GDDR6：16GB, 320 GB/s           │
                    └─────────────────────────────────┘
```

### 單一 SM 內部（T4 sm_75）

```
┌─────────────────────────────────────────────────────────┐
│                    SM（Streaming Multiprocessor）        │
│                                                          │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│  │  Processing  │ │  Processing  │ │  Processing  │ │  Processing  │
│  │  Block 0     │ │  Block 1     │ │  Block 2     │ │  Block 3     │
│  │              │ │              │ │              │ │              │
│  │ warp sched   │ │ warp sched   │ │ warp sched   │ │ warp sched   │
│  │ dispatch u.  │ │ dispatch u.  │ │ dispatch u.  │ │ dispatch u.  │
│  │ 16 FP32 core │ │ 16 FP32 core │ │ 16 FP32 core │ │ 16 FP32 core │
│  │ 16 INT32 core│ │ 16 INT32 core│ │ 16 INT32 core│ │ 16 INT32 core│
│  │ 2 Tensor Core│ │ 2 Tensor Core│ │ 2 Tensor Core│ │ 2 Tensor Core│
│  └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘
│                                                          │
│  Register File：64K × 32-bit（每 SM 共享，分配給各 warp）│
│  L1 / Shared Memory：96KB unified（可設定切割比例）      │
│                                                          │
│  最多 32 active warp（= 1024 thread）                    │
│  最多 16 active thread block                             │
└─────────────────────────────────────────────────────────┘
```

---

## 軟體層次 → 硬體層次的映射

這是整章最重要的對照表。把它背起來。

```
軟體概念           對應硬體                   備註
────────────       ──────────────────────     ─────────────────────────────
grid               整個 GPU                   一次 kernel launch 的工作總量
block              SM（一個 block 住在一個 SM）block 不會跨 SM 執行
warp（32 thread）  processing block 的執行單位 warp scheduler 的排程單位
thread             一個 CUDA core 的一次計算   不是「一個 core 專屬一個 thread」

grid → block 分派  GigaThread Engine           host → device 的 work distribution
block → SM 分配    SM 的 block scheduler        SM 內的 block 資源分配
warp scheduling    warp scheduler（每 SM 4 個）zero-overhead latency hiding
```

關鍵理解：**一個 block 的生命週期從頭到尾在同一個 SM 上**。這就是為什麼 `__syncthreads()` 有意義——它只需要在單一 SM 內同步，不需要跨 SM。

### 為什麼 warp 是 32？

這不是任意選的。每個 processing block 有 16 個 FP32 core，但一個 warp 是 32 個 thread。這代表一個 warp 的執行需要 2 個 cycle 才能把 32 個 thread 全發出去。這個 2-cycle issue 的設計在 Turing 之前一直不變；Ampere 還是 32。

如果你改寫歷史，讓 warp 變成 64，scheduling 邏輯會變複雜，register file 壓力上升，且每個 warp 發生 divergence 的機率更高。32 是歷史演進的甜蜜點。

---

## GigaThread Engine：block 如何到達 SM

### 這個元件做什麼

GigaThread Engine（巨執行緒引擎）是 GPU 的全域 work distributor。當你呼叫 `<<<grid, block>>>` 啟動一個 kernel，GigaThread Engine 做這些事：

1. 確認 grid 中有多少 block（比如 `gridDim.x * gridDim.y * gridDim.z`）
2. 掃描所有 SM，找出哪些 SM 還有足夠資源（register、shared memory、block slot）承接新 block
3. 把 block 派給有空間的 SM

這個過程是動態的。如果 GPU 有 40 個 SM，但你的 grid 只有 10 個 block，那只有 10 個 SM 會有工作，其他 30 個閒置。如果你有 400 個 block，GigaThread 會先填滿所有 SM，然後等某個 SM 的 block 完成後再補下一個。

### SM 之間沒有通訊

這是 GPU 程式設計的基本約束：**SM 之間無法直接通訊**。不同 SM 上的 block 要同步，唯一的辦法是讓 kernel 結束（所有 block 都完成），然後在 host 端或另一次 kernel launch 時匯聚結果。

這個限制不是設計缺陷，是刻意的。去掉 SM 間通訊讓 GigaThread 可以任意排程，讓你的程式碼在 40 SM 的 T4 和 80 SM 的 A100 上都能跑（只是速度不同），不需要修改。

---

## SIMT 執行模型

### SIMT vs SIMD

兩者都在「平行執行同一指令」，但層次不同：

```
CPU SIMD（AVX-512 為例）          GPU SIMT
──────────────────────────         ──────────────────────────
__m512 a = _mm512_load_ps(ptr);   // 每個 thread 有自己的 ptr
一個 register，16 個 float         每個 thread 有獨立的 register file
程式設計師顯式打包資料              CUDA 自動管理資料佈局
分支：所有 lane 同步走              分支：diverge 的 thread 被 mask 掉
無法表達不同 thread 有不同邏輯流    可以（但有效能代價，見 Ch 10）
```

SIMT 的關鍵優勢：你寫的是「一個 thread 的邏輯」，不是「一個 vector operation 的邏輯」。CUDA 幫你把 32 個 thread 打包成一個 warp，在硬體上同步執行。

### Warp divergence 的代價

當 warp 裡的 thread 走不同 branch：

```
// 假設 warp 裡 thread 0-15 走 if，thread 16-31 走 else
if (threadIdx.x < 16) {
    do_A();   // 只有 0-15 執行，16-31 被 mask（閒置）
} else {
    do_B();   // 只有 16-31 執行，0-15 被 mask（閒置）
}
// 兩個 branch 串行執行，效率砍半
```

這不是 bug，是 SIMT 的代價。Ch 10 會深挖；這裡先記住：**同一 warp 裡的 thread 走同一路徑是好事**。

---

## T4 為什麼是 40 SM 而不是更多

### 面積與功耗的硬約束

T4 是資料中心推論卡，TDP 70W（不需要額外電源接頭）。Turing GPU 家族的頂端 TU102（RTX 2080 Ti 用）有 72 SM，但 TDP 250W。

T4 用的是 TU104 die 的裁切版（TU104 完整是 48 SM，T4 裁到 40 SM）。選 40 而非 48 是 yield 和功耗的取捨：

- 更多 SM 代表更大 die，良率下降（缺陷密度固定，die 越大越容易中招）
- 推論工作負載有大量 INT8 計算，Turing 的 INT8 Tensor Core 效率比 FP32 高得多
- 70W TDP 對資料中心是黃金規格（可以密集堆卡，不需要特殊散熱）

**「為什麼不直接做更多 SM？」的答案永遠是：die size 不是免費的，功耗不是免費的，良率不是免費的。**

### 每個 SM 是完整的 pipeline

每個 SM 有自己完整的 register file、warp scheduler、L1/Shared Memory、Tensor Core。這不像 CPU 共享大 L3 cache 的設計——SM 是相對自給自足的計算單元，代價是每個 SM 面積大。

---

## Volta/Turing 前後的差異：獨立 thread scheduling

### Pascal 及之前：warp-level lock-step

Pascal（sm_60/61）及之前，整個 warp 的 thread 共享一個 PC（Program Counter）。這代表：

- 沒有辦法讓 warp 內的 thread 在不同時間點等待
- Cooperative 操作（比如 warp-level vote）只能用特定 intrinsic
- `__syncwarp()` 這個概念不存在，因為 warp 本來就是 lock-step

### Volta/Turing 之後：每 thread 有自己的 PC

從 Volta（sm_70）開始，每個 thread 有獨立的 PC 和 call stack。這讓：

- `__syncwarp(mask)` 成為必要的（因為 thread 可能真的在不同時間點）
- 細粒度 warp-level primitive 成為可能（`__ballot_sync`, `__shfl_sync` 的 mask 參數）
- diverged thread 可以在不同時間點重新 converge

這個改變對正確性有影響：如果你在 Volta+ 上用舊的 warp-shuffle 寫法（沒有 `_sync` 版本），可能在某些執行模式下得到錯誤結果。細節在 [Ch 10 warp 與 SIMT 執行](./10-warp-simt-execution.md)。

---

## Register File 與資源分配

### 64K register 是如何分配的

每個 SM 有 64K 個 32-bit register。這個 register file 是 SM 上所有 active warp 共享的。

例子：

```
thread 用 32 個 register
一個 warp = 32 thread
一個 warp 消耗 32 × 32 = 1024 register

SM 有 64K = 65536 register
理論上可支援 65536 / 1024 = 64 個 warp

但 SM 上限是 32 active warp（T4 的 sm_75 限制）
所以 register 不是 bottleneck（在 32 reg/thread 的情況下）
```

當你的 kernel 用了 128 個 register/thread：

```
128 × 32 = 4096 register/warp
65536 / 4096 = 16 warp
→ occupancy 從 100%（32/32）降到 50%（16/32）
```

用 `nvcc --ptxas-options=-v` 看你的 kernel 實際用了多少 register。

### Shared Memory 的設定

T4 每個 SM 有 96KB unified L1/Shared Memory，可以在 kernel 啟動前設定：

```cpp
// 設成 64KB shared + 32KB L1
cudaFuncSetAttribute(myKernel,
    cudaFuncAttributePreferredSharedMemoryCarveout,
    cudaSharedmemCarveoutMaxShared);  // = 64KB

// 或設成 32KB shared + 64KB L1
cudaFuncSetAttribute(myKernel,
    cudaFuncAttributePreferredSharedMemoryCarveout,
    cudaSharedmemCarveoutMaxL1);      // = 32KB
```

記憶體密集型 kernel（矩陣乘法）通常要 64KB shared；對 L1 hit rate 依賴大的 kernel 要 64KB L1。

---

## 對比取捨（表格）

| 設計選擇 | T4（Turing sm_75）的選擇 | 為什麼這樣選 |
|----------|--------------------------|-------------|
| SM 數量 | 40 | TDP 70W 限制；TU104 die 裁切；推論用途不需要 72 SM |
| Warp size | 32 | 歷史延續 + 2-cycle issue pipeline 設計；大了 divergence 代價更高 |
| Processing block 數/SM | 4 | 4 個 warp scheduler 允許同時隱藏多種 latency（memory + compute） |
| Register file 大小 | 64K/SM | 支援 32 warp × 64 reg/thread；更大需要更多面積 |
| L1/Shared unified | 96KB | 靈活性；Volta 引入 unified design，Turing 繼承並擴大 |
| Thread scheduling | 獨立 PC/thread | Volta 引入；正確性更好，代價是更多 per-thread state |
| L2 cache | 4MB 全共享 | 推論模型 weight 的工作集通常 < 4MB；訓練卡（A100）用 40MB |

---

## 踩雷

**1. 以為 2560 個 CUDA core 能同時跑 2560 個不同指令**

不行。同一個 processing block 的 16 個 FP32 core 在同一 cycle 只能執行同一條指令（同一 warp 的 16 個 thread）。「2560 個 core 同時算」是行銷語言，正確說法是「2560 路 SIMT」。

**2. 忘記 warp 是 32 但 processing block 只有 16 FP32 core**

一個 warp 分兩個 cycle 發出：第一個 cycle 服務 thread 0-15，第二個 cycle 服務 thread 16-31。這是為什麼 warp 的 `threadIdx.x` 分組對 bank conflict（見 [Ch 19 bank conflict 深挖](./19-bank-conflict.md)）很重要。

**3. 以為 block 可以跨 SM 執行**

不行。一個 block 從開始到結束都在同一個 SM 上。`__syncthreads()` 是 SM 內同步，不是 GPU 全局同步。要做跨 SM 同步，只有 kernel 結束這一條路（或 CUDA 12 的 `cooperative_groups::grid.sync()`，但需要特殊啟動方式）。

**4. 誤以為 register spill 只是慢一點**

Register spill 是把 register 的內容寫到 local memory（實際上是全局記憶體，有 L1/L2 cache），延遲從 4-5 cycle 跳到 200+ cycle。一個大量 spill 的 kernel 會比預期慢 10 倍以上。用 Nsight Compute 看 `l1tex__t_sectors_pipe_lsu_mem_local_op_ld.sum` 確認。

**5. 不了解 GigaThread 的工作分配對 occupancy 的影響**

如果你的 block 需要的 shared memory 讓每個 SM 只能跑 1 個 block，那 SM 的利用率取決於那個 block 能不能塞滿 SM 的 warp 上限（32）。一個 1024-thread block 剛好用完 32 warp；一個 64-thread block 只用 2 warp，其他 30 個 warp slot 空著。Grid 的 block 大小選擇會直接影響 GigaThread 能做多好的 packing。

---

## 進階

### Warp 的 zero-overhead scheduling

每個 SM 的 4 個 warp scheduler 在 **每個 cycle** 可以各發出一條指令（給不同 warp）。這是 GPU 隱藏 latency 的核心機制：當 warp A 在等記憶體回傳，warp scheduler 切換到 warp B、C、D 繼續計算。這個切換是 zero-overhead 的（不像 CPU context switch 需要儲存/恢復大量狀態），因為所有 warp 的 register 同時住在 SM 的 register file 裡。

這個機制叫 **latency hiding**，是 GPU 效能模型的基礎。Ch 11 會定量分析需要多少 active warp 才能完全隱藏記憶體延遲。

### Tensor Core 在架構中的位置

每個 processing block 有 2 個 Tensor Core（每個 SM 共 8 個）。Tensor Core 執行 4×4×4 的矩阵乘累加（D = A×B + C），在 FP16 精度下一個 cycle 完成 64 次 FMA（fused multiply-add）。

T4 整顆 GPU 的 Tensor Core 峰值：8 Tensor Core/SM × 40 SM × 64 FMA/cycle × 585 MHz ≈ 65 TFLOPS（FP16）。這是 T4 被選為推論卡的主因，遠超過 FP32 的 8.1 TFLOPS。

### MIG（Multi-Instance GPU）與架構的關係

Ampere 及之後的 GPU（A100、A30）支援 MIG，把 GPU 的 SM 群組分割成獨立的 GPU 實例。這個功能建立在「SM 之間沒有共享狀態」的設計上——正因為每個 SM 自給自足，才能把一組 SM 隔離出來給一個租戶。T4 不支援 MIG，但理解 SM 的獨立性有助於理解 MIG 為什麼可行。

---

## 動手練習

**練習 1：驗證 T4 規格**

```bash
nvidia-smi -q | grep -E "SM|Memory|L2|Multiprocessor"
# 看 Multiprocessors 是不是 40
```

用 CUDA Device Query 列出精確規格：

```cpp
// device_query.cu
#include <cuda_runtime.h>
#include <stdio.h>

int main() {
    int dev = 0;
    cudaDeviceProp prop;
    cudaGetDeviceProperties(&prop, dev);

    printf("Name: %s\n", prop.name);
    printf("SM count: %d\n", prop.multiProcessorCount);
    printf("Max threads/block: %d\n", prop.maxThreadsPerBlock);
    printf("Max threads/SM: %d\n", prop.maxThreadsPerMultiProcessor);
    printf("Warp size: %d\n", prop.warpSize);
    printf("Shared mem/SM: %zu KB\n", prop.sharedMemPerMultiprocessor / 1024);
    printf("L2 cache size: %d MB\n", prop.l2CacheSize / 1024 / 1024);
    printf("Global mem: %zu GB\n", prop.totalGlobalMem / 1024 / 1024 / 1024);
    printf("Memory bandwidth: %.1f GB/s\n",
        2.0 * prop.memoryClockRate * (prop.memoryBusWidth / 8) / 1.0e6);
    return 0;
}
```

```bash
nvcc -o device_query device_query.cu && ./device_query
```

預期輸出（T4）：SM count = 40，warp size = 32，shared mem/SM = 96 KB，L2 = 4MB，memory bandwidth ≈ 300 GB/s。

**練習 2：觀察 GigaThread 的 block 分派**

```cpp
// block_mapping.cu：每個 block 印出自己在哪個 SM
#include <cuda_runtime.h>
#include <stdio.h>

__device__ uint get_smid() {
    uint smid;
    asm volatile("mov.u32 %0, %%smid;" : "=r"(smid));
    return smid;
}

__global__ void show_mapping() {
    if (threadIdx.x == 0) {
        printf("Block (%d,%d) -> SM %d\n",
            blockIdx.x, blockIdx.y, get_smid());
    }
}

int main() {
    // 啟動 80 個 block，看 GigaThread 如何分配給 40 SM
    show_mapping<<<80, 32>>>();
    cudaDeviceSynchronize();
    return 0;
}
```

觀察：80 個 block 是否平均分散到 40 個 SM（每 SM 2 個）？還是有不均勻的情況？

**練習 3：register 數量對 occupancy 的影響**

```bash
# 編譯並看 register 使用量
nvcc -O2 --ptxas-options=-v your_kernel.cu 2>&1 | grep "Used registers"

# 或用 Nsight Compute
ncu --metrics sm__warps_active.avg.pct_of_peak_sustained_active \
    --metrics launch__registers_per_thread \
    ./your_binary
```

嘗試用 `__launch_bounds__(128, 4)` 限制 register 上限，觀察 occupancy 的變化。

---

## 本章重點

1. **T4 = 40 SM，每 SM 64 FP32 core**（4 個 processing block × 16），共 2560。數字背後是功耗和 die size 的約束，不是任意選的。

2. **SIMT**：一條指令廣播給一個 warp（32 thread），每個 thread 操作自己的資料和 register。不是 SIMD，不是「2560 個獨立 CPU」。

3. **軟體層次 → 硬體層次**：grid → GPU，block → SM（由 GigaThread Engine 分派），warp → processing block 的執行單位，thread → CUDA core 的一次計算。

4. **SM 之間不通訊**：這個限制讓 GigaThread 可以自由排程，讓程式碼在不同 SM 數的 GPU 上都能跑。

5. **資源三角**：register（64K/SM）、shared memory（96KB/SM）、block slot（16/SM）決定了每個 SM 能同時承接多少 block 和 warp——這是 occupancy 的真正來源。

6. **Volta/Turing 引入獨立 thread scheduling**：每個 thread 有自己的 PC，讓細粒度 warp primitive（`_sync` 版本）成為必要。Ch 10 深挖。

---

## 自我檢核（主動回憶）

不要回頭翻，先試著回答：

1. T4 有幾個 SM？每個 SM 有幾個 FP32 CUDA core？每個 SM 有幾個 processing block？
2. 一個 warp 有幾個 thread？一個 processing block 有幾個 FP32 core？這兩個數字的差異代表什麼？
3. GigaThread Engine 的職責是什麼？它何時會決定把 block 派給哪個 SM？
4. 為什麼 `__syncthreads()` 不能跨 SM 同步？
5. T4 的 L1/Shared Memory 總量是多少？可以怎麼切？
6. 一個 kernel 如果用了 128 個 register/thread，一個 SM 最多能同時跑幾個 warp？
7. Turing 和 Pascal 在 thread scheduling 上的核心差異是什麼？
8. 為什麼 SM 的數量受到 die size 和功耗的限制？T4 用 TU104 die 的幾個 SM？

---

## 延伸閱讀

1. **NVIDIA Turing Architecture Whitepaper**（[https://www.nvidia.com/content/dam/en-zz/Solutions/design-visualization/technologies/turing-architecture/NVIDIA-Turing-Architecture-Whitepaper.pdf](https://www.nvidia.com/content/dam/en-zz/Solutions/design-visualization/technologies/turing-architecture/NVIDIA-Turing-Architecture-Whitepaper.pdf)）
   — 讀 SM 架構圖那節（Section 2: SM Architecture）。看 processing block 的示意圖，對照本章的數字。前提：看得懂英文技術文件。

2. **CUDA C++ Programming Guide, Appendix: Compute Capabilities（sm_75 那頁）**（[https://docs.nvidia.com/cuda/cuda-c-programming-guide/index.html#compute-capabilities](https://docs.nvidia.com/cuda/cuda-c-programming-guide/index.html#compute-capabilities)）
   — 查 T4 所有精確數字的一手來源。每次看到不確定的數字都來這裡查，不要背錯版本。前提：知道 sm_75 是 Turing 的 compute capability。

3. **《Programming Massively Parallel Processors》第 4 章**（Kirk & Hwu，第四版）
   — GPU 架構的教科書入門，圖解清晰，適合在本章之後系統化鞏固。前提：英文閱讀無礙。

4. **"Dissecting the NVidia Turing T4 GPU via Microbenchmarking"**（Jia et al., arXiv 1903.07486，[https://arxiv.org/abs/1903.07486](https://arxiv.org/abs/1903.07486)）
   — 用微基準測試（microbenchmark）逆向工程 T4 的 cache 層次、latency、throughput，揭露 whitepaper 不會告訴你的細節。前提：看完本章。

5. **NVIDIA GTC 2018: Turing Architecture slides**（可在 GTC 官網或 YouTube 搜尋「GTC 2018 Turing」）
   — Turing 發布時 NVIDIA 工程師的第一手簡報，包含 Tensor Core 二代和 RT Core 的歷史背景，以及為什麼 Turing 相比 Volta 做了哪些權衡。前提：看過 [Ch 1](./01-cpu-vs-gpu-philosophy.md) 的 GPU 世代脈絡。

---

## 銜接

本章建立了 T4 的硬體地圖，知道了「有什麼」。下一章進去 SM 內部，把每個元件拆開來看：warp scheduler 怎麼決定發哪個 warp、register file 怎麼分配、Tensor Core 的矩陣乘法週期是怎麼算的。

這一章是「地圖」，下一章是「街景」。

→ [Ch 8 — SM 剖析：warp scheduler、register file、Tensor Core](./08-sm-anatomy.md)
