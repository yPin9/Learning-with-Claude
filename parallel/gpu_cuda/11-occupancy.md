# Ch 11 — 佔用率（Occupancy）

> **目標**：理解 SM 佔用率的定義、三大限制因子、計算方法，並建立「高 occupancy ≠ 高效能」的正確認知。
> **環境**：NVIDIA T4（Turing sm_75），CUDA 12.x，WSL2 + nvcc。

---

## 為什麼需要這章

上一章 [Ch 10 warp 與 SIMT 執行](./10-warp-simt-execution.md) 說明了 warp 是 GPU 排程的基本單位。當一個 warp 等待記憶體時，排程器（warp scheduler）會切換到另一個 ready warp，用計算掩蓋延遲。

但這個機制有個前提：**SM 上要有足夠多的 active warp 可以切換**。如果 SM 上只有少數幾個 warp，一旦全部卡在記憶體存取，排程器無牌可打，SM 就閒置。

佔用率（occupancy）量化的就是「SM 上實際能住多少 warp」這件事。搞懂它，才能判斷 kernel 的 warp 切換效率，以及調參數時的方向。

---

## 先建立直覺

把 SM 的 warp slot 想成 32 個格子：

```
T4 SM — 32 個 warp slots
┌──┬──┬──┬──┬──┬──┬──┬──┐
│W0│W1│W2│W3│W4│W5│W6│W7│  ← 8 個 active warp
├──┼──┼──┼──┼──┼──┼──┼──┤
│W8│W9│··│··│··│··│··│··│
├──┼──┼──┼──┼──┼──┼──┼──┤
│  │  │  │  │  │  │  │  │  ← 空格子 = 資源用完沒空間
├──┼──┼──┼──┼──┼──┼──┼──┤
│  │  │  │  │  │  │  │  │
└──┴──┴──┴──┴──┴──┴──┴──┘
```

occupancy = 填滿的格子數 / 32

格子為什麼填不滿？因為每個 warp 背後的 thread 需要佔用 SM 上的實體資源（register、shared memory），資源耗盡了就無法再放更多 warp。

---

## 核心定義

$$\text{occupancy} = \frac{\text{SM 上實際 active warp 數}}{\text{SM 最大 warp 數}} = \frac{\text{active\_warps\_per\_SM}}{32}$$

T4（sm_75）硬性規格：

| 資源 | 上限 |
|------|------|
| Active warp / SM | 32（= 1024 active thread） |
| Thread block / SM | 16 |
| Register / SM | 65536 個 32-bit register（64K） |
| Shared memory / SM | 96KB（可設定 32KB 或 64KB 給 shared） |
| Register / thread | 最多 255 個 |

**active warp** 的定義：已分配到 SM、隨時可排程的 warp，包含正在等待記憶體的 warp。不是「正在執行」，而是「住在 SM 上」。

---

## 三大限制因子

決定一個 SM 能住多少 block 的限制因子有三個，最緊的那個說了算。

### 限制一：Register 用量

每個 SM 有 64K 個 register，由同一個 SM 上所有 active thread 共享。

```
每 block 耗用的 register = blockDim × r
SM 能放的 block 數       = floor(65536 / (blockDim × r))
                           （再取 min 16，不超過 block 上限）
active warp 數           = block 數 × (blockDim / 32)
                           （再取 min 32，不超過 warp 上限）
```

### 限制二：Shared memory 用量

每個 SM 的 shared memory 容量可設定為 32KB 或 64KB（Turing 預設 32KB shared + 64KB L1）。

```
每 block 耗用的 shared memory = S bytes
SM 能放的 block 數            = floor(shared_per_SM / S)
                               （再取 min 16）
```

如果 kernel 沒用 shared memory（S = 0），這個限制不存在。

### 限制三：Block 數上限（硬性）

Turing（sm_75）每個 SM 最多同時住 **16 個 thread block**，這是硬體電路的限制，無法繞過。

### 三限制一起看

```
實際 active block 數 = min(
    floor(65536 / (blockDim × r)),   ← register bound
    floor(shared_per_SM / S),         ← shared memory bound（S=0 時忽略）
    16                                ← block 數硬性上限
)

active warp 數 = min(
    active_block 數 × (blockDim / 32),
    32                                ← warp 數硬性上限
)

occupancy = active warp 數 / 32
```

---

## 算例 1：Register Bound

**條件**：blockDim = 256 thread/block，每個 thread 用 r 個 register，shared memory = 0。

每個 block = 256 / 32 = **8 warp**。

**Case A：r = 64**

```
每 block 用 register = 256 × 64 = 16384
SM 能放 block 數     = floor(65536 / 16384) = 4 blocks
                       min(4, 16) = 4
active warp 數       = 4 × 8 = 32 warp
                       min(32, 32) = 32

occupancy = 32 / 32 = 100%
```

register 剛好用完，warp slots 全滿，理想情況。

**Case B：r = 128**

```
每 block 用 register = 256 × 128 = 32768
SM 能放 block 數     = floor(65536 / 32768) = 2 blocks
                       min(2, 16) = 2
active warp 數       = 2 × 8 = 16 warp
                       min(16, 32) = 16

occupancy = 16 / 32 = 50%
```

每個 thread 多用一倍 register，SM 只能住兩個 block，occupancy 腰斬。

**結論**：register 用量對 occupancy 的影響是線性的。用 `nvcc --ptxas-options=-v` 可以看編譯器幫每個 kernel 分配了幾個 register。

---

## 算例 2：Shared Memory Bound

**條件**：blockDim = 256（8 warp/block），每 thread 32 register（遠低於 register bound），shared memory carveout 設為 32KB。

**Case A：每 block 用 16KB shared memory**

```
SM shared memory     = 32KB = 32768 bytes
每 block shared      = 16384 bytes
SM 能放 block 數     = floor(32768 / 16384) = 2 blocks
                       min(2, 16) = 2
register check：     = floor(65536 / (256 × 32)) = floor(65536 / 8192) = 8 blocks → 不是瓶頸
active warp 數       = 2 × 8 = 16 warp

occupancy = 16 / 32 = 50%
```

**Case B：每 block 用 8KB shared memory**

```
SM 能放 block 數     = floor(32768 / 8192) = 4 blocks
                       min(4, 16) = 4
active warp 數       = 4 × 8 = 32 warp

occupancy = 32 / 32 = 100%
```

把 shared memory 用量從 16KB 減半，occupancy 從 50% 升回 100%。

**Case C：把 carveout 改為 64KB shared**

如果用 `cudaFuncSetAttribute` 把 shared memory carveout 設為 64KB：

```c
cudaFuncSetAttribute(myKernel,
    cudaFuncAttributePreferredSharedMemoryCarveout, 67); // ~64KB
```

Case A 的條件下（每 block 16KB）：

```
SM shared memory     = 64KB = 65536 bytes
SM 能放 block 數     = floor(65536 / 16384) = 4 blocks
active warp 數       = 4 × 8 = 32 warp

occupancy = 32 / 32 = 100%
```

多給 shared memory 空間，同一個 kernel 的 occupancy 從 50% 升到 100%。代價是 L1 cache 從 64KB 縮到 32KB。

---

## 算例 3：Block Count Bound

這個算例說明「block 太小」也是個陷阱。

**條件**：blockDim = 32（= 1 warp/block），每 thread 32 register，shared memory = 0。

```
每 block 用 register = 32 × 32 = 1024
register bound：     = floor(65536 / 1024) = 64 blocks
block 數硬性上限：   = 16 blocks

取 min(64, 16) = 16 blocks
active warp 數       = 16 × 1 = 16 warp

occupancy = 16 / 32 = 50%
```

register 明明夠用，但 block 數上限（16）把我們卡死了。SM 上只有 16 warp，剩下 16 個 warp slot 永遠空著。

解法：把 blockDim 從 32 改成 64（2 warp/block）：

```
每 block 用 register = 64 × 32 = 2048
register bound：     = floor(65536 / 2048) = 32 blocks → 取 min(32, 16) = 16 blocks
active warp 數       = 16 × 2 = 32 warp

occupancy = 32 / 32 = 100%
```

**結論**：blockDim 太小時，block count 硬性上限（16）比 register bound 先踢到。blockDim 至少要讓 `SM_max_warp / max_block_per_SM = 32/16 = 2 warp/block = 64 thread/block`，才有機會達到 100%。

---

## 底層機制：誰在追蹤 occupancy

SM 內部有一個 **warp scheduler**（T4 每個 SM 有 4 個），它只能排程「active warp」。Active warp 的狀態可以是：

- **eligible**：指令發射條件滿足，可以馬上執行
- **stalled**：等待記憶體、barrier、依賴

Occupancy 高的好處是：即使大量 warp stalled，eligible warp 還是夠多，排程器能持續發射指令，把計算延遲藏進記憶體等待時間裡。

Occupancy 低時，eligible warp 少，排程器常常找不到可發射的指令，SM 閒置（SM active cycles 低，Nsight Compute 可測量）。

---

## 對比取捨

| 情境 | Occupancy | 原因 | 可能的調整方向 |
|------|-----------|------|---------------|
| r = 64，blockDim = 256 | 100% | register 剛好 | 維持 |
| r = 128，blockDim = 256 | 50% | register bound | 降低 r（減少局部變數、強制 `-maxrregcount`） |
| 每 block 16KB shared，32KB carveout | 50% | shared memory bound | 減少 shared memory 用量，或增加 carveout |
| blockDim = 32 | 50% | block count bound | 增大 blockDim（至少 64） |
| blockDim = 512，r = 32 | 64% | warp 數上限 | 本就無法更高（4×16=64>32，取 32，但 2 blocks × 16 = 32，實際 100%）|

**注意**：shared memory carveout 影響 L1 cache 大小，是個雙向取捨，不是純粹「共享記憶體越多越好」。

---

## 使用 CUDA Occupancy API

不要手算，用 API 讓驅動幫你算：

```c
#include <cuda_runtime.h>
#include <stdio.h>

__global__ void myKernel(float* data) {
    // ... kernel body
}

int main() {
    int blockSize = 256;
    size_t dynamicSharedMem = 0;  // 每 block 動態 shared memory bytes
    int numBlocksPerSM = 0;

    cudaOccupancyMaxActiveBlocksPerMultiprocessor(
        &numBlocksPerSM,
        myKernel,
        blockSize,
        dynamicSharedMem
    );

    // T4 每 SM 最大 warp = 32
    int maxWarpsPerSM = 32;
    int activeWarps = numBlocksPerSM * (blockSize / 32);
    float occupancy = (float)activeWarps / maxWarpsPerSM;

    printf("Active blocks/SM: %d\n", numBlocksPerSM);
    printf("Active warps/SM:  %d\n", activeWarps);
    printf("Occupancy:        %.1f%%\n", occupancy * 100.0f);

    return 0;
}
```

**自動找最佳 blockDim**（讓驅動在多個 blockSize 中選最高 occupancy 的那個）：

```c
int blockSize;
int minGridSize;  // 達到最大 occupancy 所需的最小 grid size

cudaOccupancyMaxPotentialBlockSize(
    &minGridSize,
    &blockSize,
    myKernel,
    0,    // dynamicSharedMem
    0     // blockSizeLimit（0 = 不限制）
);

printf("Suggested blockSize: %d\n", blockSize);
printf("Min grid size for full occupancy: %d\n", minGridSize);
```

`cudaOccupancyMaxPotentialBlockSize` 會枚舉各種 blockDim，找出使 occupancy 最大的設定。實際上你應該把它的建議當參考起點，再用 Nsight Compute 量實際效能。

**查 register 用量**（編譯時加這個 flag）：

```bash
nvcc -O2 --ptxas-options=-v mykernel.cu -o mykernel
# 輸出範例：
# ptxas info    : Used 32 registers, 4096 bytes smem, 360 bytes cmem[0]
```

這個 `32 registers` 就是前面算例裡的 r。

---

## 踩雷

**1. 靜態 shared memory 沒算進去**

`cudaOccupancyMaxActiveBlocksPerMultiprocessor` 的第四個參數只接受**動態** shared memory（`__shared__` 裡用 `extern` 宣告的那種）。Kernel 內直接宣告的靜態 shared memory（`__shared__ float buf[1024]`）驅動會自己算，不需要你傳。但如果你誤把靜態 + 動態全加進去傳入，會算出過低的 occupancy。

**2. Register 對齊到 warp 粒度**

實際上，register 分配以 warp 為單位取整。如果 nvcc 回報每 thread 用 33 register，實際分配的是 ceil(33/某個對齊) × 32，通常是 40 或 48，視 CUDA 版本和 SM 架構而定。所以你自己手算時，r 要取**實際對齊後的值**，不是編譯器回報的裸數字。用 API 比手算準確。

**3. 以為 `--maxrregcount` 一定幫助**

強制限制 register 數（`-maxrregcount=32`）確實能提升 occupancy，但 compiler 會把多出來的 register 需求 spill 到 local memory（= L2 cache 的一塊，存取慢 10 倍以上）。occupancy 上去，但 kernel 反而更慢。

**4. Block 太小忘了 block count bound**

blockDim = 32（1 warp/block），不管 register 多便宜，SM 最多 16 block = 16 warp = 50% occupancy。這是 Turing 架構的硬性上限，不能靠調參數解開。

**5. 混淆 theoretical occupancy 與 achieved occupancy**

`cudaOccupancyMaxActiveBlocksPerMultiprocessor` 算的是**理論上限**，假設 SM 上的 block 都已填滿。實際跑的 grid 如果沒有足夠多的 block，SM 不一定能填滿。Nsight Compute 的 `Achieved Warp Occupancy` 才是真實值。

---

## 進階：高 Occupancy ≠ 高效能

這是最容易犯的直覺錯誤。高 occupancy 是**隱藏延遲的必要條件之一**，不是充分條件。

**高 occupancy 理論上的好處**：
- 更多 active warp → 排程器更有機會在一個 warp 等記憶體時切換到另一個
- 理論上 SM 利用率更高

**但以下情況高 occupancy 反而有害**：

1. **L1 cache 爭用（cache thrashing）**：warp 多 → 同一個 SM 的 working set 更大 → L1 hit rate 降低，反而要更多次 L2/DRAM 存取。

2. **Register spilling 壓力**：為了塞更多 warp 而強制限制 register，導致 spill 到 local memory，存取延遲大幅上升。

3. **Shared memory 不夠用**：為了塞更多 block 而減少每 block 的 shared memory，tile 變小，演算法效率降低（矩陣乘法的典型例子）。

4. **Compute-bound kernel**：如果 kernel 是純計算密集型（很少記憶體存取），occupancy 提升不帶來額外好處，因為排程器本來就不需要切換。

**現實數字**：根據 NVIDIA 官方教材和實測，大多數 kernel 在 **50%-75% occupancy** 時效能最佳。矩陣乘法（cuBLAS 用大 tile）的 occupancy 往往只有 25%-50%，但效能接近理論峰值。

**判斷方法**：
- 在 Nsight Compute 看 `Memory Throughput`：如果記憶體頻寬已接近硬體上限，提升 occupancy 無用。
- 看 `Warp State Statistics`：`stall_long_sb`（等 L2/DRAM）多的話才值得提升 occupancy。
- 看 `Compute Throughput`：如果計算吞吐已滿，記憶體延遲不是瓶頸。

我們會在 Ch 20 效能調優裡系統性地拆解這個問題。

---

## 動手練習

**練習 1：查 kernel 的 register 用量**

```bash
# 寫一個測試 kernel，用不同的 -maxrregcount 值編譯
nvcc -O2 --ptxas-options=-v kernel.cu -o kernel_unrestricted
nvcc -O2 --ptxas-options=-v -maxrregcount=32 kernel.cu -o kernel_r32

# 比較兩個版本的 register 數和 local memory 用量
# local memory > 0 代表發生了 register spilling
```

**練習 2：用 API 計算 occupancy**

```c
// 寫兩個 kernel：一個每 thread 用 64 register（手動宣告大量局部陣列），
// 一個每 thread 用 32 register
// 用 cudaOccupancyMaxActiveBlocksPerMultiprocessor 分別計算
// 驗證是否和算例 1 的手算結果吻合
```

**練習 3：shared memory carveout 實驗**

```c
// 同一個 kernel，每 block 用 16KB shared memory
// 分別設定 carveout = 32KB 和 64KB
// 用 cudaOccupancyMaxActiveBlocksPerMultiprocessor 計算各自的 occupancy
// 再用 Nsight Compute 量實際的 L1 hit rate，觀察 carveout 的取捨
cudaFuncSetAttribute(myKernel,
    cudaFuncAttributePreferredSharedMemoryCarveout,
    33);  // ~32KB，百分比 0-100 of 96KB
```

---

## 本章重點

- Occupancy = SM 上 active warp 數 / 32（T4），量化 warp 排程的可用空間。
- 三大限制因子：register 用量、shared memory 用量、block 數硬性上限（16/SM on Turing）。
- 計算順序：對每個因子算出 SM 能住幾個 block，取最小值，再換算成 warp 數。
- blockDim 太小（< 64）時，block count 上限（16）本身就把 occupancy 壓到 50%。
- 用 `cudaOccupancyMaxActiveBlocksPerMultiprocessor` 取代手算，用 `cudaOccupancyMaxPotentialBlockSize` 自動搜尋最佳 blockDim。
- 高 occupancy 是延遲隱藏的必要條件，不是效能的充分條件。最佳點通常在 50-75%，不是 100%。

---

## 自我檢核

1. T4 的 SM 最大 active warp 數是多少？如果 blockDim = 128，需要幾個 block 才能填滿 SM 的 warp slots？

2. 一個 kernel 的 blockDim = 256，每 thread 用 80 register。T4 上的 occupancy 是多少？（提示：先算每 block 用多少 register，再算 SM 能放幾個 block。）

3. 為什麼 blockDim = 32 在 T4 上理論 occupancy 最多只有 50%，即使 register 用量很低？

4. `cudaOccupancyMaxActiveBlocksPerMultiprocessor` 的第四個參數填的是什麼 shared memory？靜態還是動態？

5. 矩陣乘法用大 tile（高 register 用量 + 大量 shared memory）導致 occupancy 只有 25%，但效能仍接近峰值。請解釋原因。

---

## 延伸閱讀

1. **CUDA C++ Programming Guide §Maximize Utilization / §Occupancy Calculator**
   NVIDIA 官方規格來源，Appendix 的 Compute Capabilities 7.5 表格有 T4 的所有限制數字。

2. **NVIDIA Occupancy Calculator（Excel 工具，官方下載）**
   互動式試算表，輸入 blockDim、register 數、shared memory 數後自動計算三個限制因子和最終 occupancy，有圖表輔助理解。適合不想手算時用。

3. **《Programming Massively Parallel Processors》4th Ed. Ch 5 §Memory as a Limiting Factor to Parallelism**
   Kirk & Hwu 的教科書，§5.3 開始用 tiled matrix multiplication 當例子，完整示範 occupancy 計算和 tile size 取捨。

4. **Nsight Compute §SM Throughput / Warp State Statistics**
   在真實 GPU 上量 achieved occupancy 和 warp stall 原因的標準工具。`stall_long_sb` 代表等 L2/DRAM，這時候提高 occupancy 才有意義；`stall_selected`（沒有 eligible warp）是 occupancy 太低的直接症狀。

5. **"Dissecting the NVidia Turing T4 GPU via Microbenchmarking"（arXiv 1903.07486）**
   §4 量化了不同 occupancy 下的 warp 排程效果和記憶體延遲隱藏能力，是驗證「高 occupancy 不一定有用」這個論點的實測資料。

---

## 銜接

前一章 [Ch 10 — Warp 與 SIMT 執行](./10-warp-simt-execution.md) 說明了 warp 是排程的基本單位，warp 切換如何掩蓋延遲。本章說明了「SM 能住幾個 warp」的計算方式和限制。

下一步是把這個知識**動手驗證**：

→ [練習 B — 手算 Occupancy](./practice-b-occupancy.md)

需要深挖效能調優（為什麼高 occupancy 不夠）的，等到 Ch 20 效能調優再回來看。背景資源是 [Ch 8 SM 剖析](./08-sm-anatomy.md)，那章說明了 96KB unified L1/shared 的硬體結構和 carveout 設定方式。
