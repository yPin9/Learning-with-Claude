# 練習 D — Reduction 七版優化

> **目標**：從最爛的 interleaved addressing 開始，一步步把 sum reduction 優化到 warp shuffle 版，親手理解每一個優化決策背後的原理。
>
> **環境**：CUDA 12.x, Colab T4 (sm_75)。程式輸出均為「Colab 預期行為，未在本機實測」，附 Colab 執行步驟。
>
> **藍本**：Mark Harris, "Optimizing Parallel Reduction in CUDA" (NVIDIA, SC'07 tutorial)。本練習的加速比數字凡引用 Harris 原報告的，均明確標為「Harris 在 G80 上的原始數據，你的 T4 數字會不同，用 Nsight Compute 驗證」。

---

## 背景：為什麼 Reduction 值得七版

Reduction（歸約）是 GPU 上最基礎的 collective operation：把 N 個數合成 1 個（加總、最大值、AND 等）。它是一個「看起來很簡單、做好卻很難」的 benchmark：

- **工作量 O(N)**，但通訊量也是 O(N)——沒有可以掩蓋 memory latency 的計算
- 必須用樹狀結構才能利用並行，而樹狀結構的每一層都有同步需求
- 幾乎所有 kernel 的最後幾行都有 reduction（收最終答案）

Mark Harris 在 2007 年寫了這份教程，從 G80 的角度把 reduction 優化了 7 版，每版加速比都有量化。到今天 T4 的架構已不同，但**優化的原理完全不變**：消除 divergence、消除 bank conflict、減少 idle thread、減少 `__syncthreads` 次數。

---

## 規格

- **問題**：計算 `float` 陣列的 sum，N = 2²⁰ = 1,048,576
- **Block size**：256 threads（7 版都用這個，方便對比）
- **正確性**：結果和 CPU 串列計算的差值 < 1e-3（float 精度）
- **量測**：每版跑 100 次取平均，用 CUDA event 計時
- **環境**：CUDA 12.x，T4 sm_75

---

## 公共 Header：正確性驗證與計時工具

先把驗證和計時邏輯抽出來，七個版本都用：

```cuda
// reduction_common.cuh
#pragma once
#include <cuda_runtime.h>
#include <cstdio>
#include <cmath>
#include <cstdlib>

// 正確性驗證：CPU 串列計算
float cpu_sum(const float *data, int n) {
    double sum = 0.0;  // 用 double 減少累積誤差，讓 CPU 答案更準確
    for (int i = 0; i < n; i++) sum += data[i];
    return (float)sum;
}

// 計時：用 CUDA event（比 CPU 計時精準）
float time_kernel(std::function<void()> launch_fn, int warmup = 5, int repeat = 100) {
    // warmup（CUDA JIT + cache warm）
    for (int i = 0; i < warmup; i++) launch_fn();
    cudaDeviceSynchronize();

    cudaEvent_t start, stop;
    cudaEventCreate(&start);
    cudaEventCreate(&stop);

    cudaEventRecord(start);
    for (int i = 0; i < repeat; i++) launch_fn();
    cudaEventRecord(stop);
    cudaEventSynchronize(stop);

    float ms;
    cudaEventElapsedTime(&ms, start, stop);
    cudaEventDestroy(start);
    cudaEventDestroy(stop);

    return ms / repeat;  // 平均每次 ms
}

// 錯誤檢查
#define CUDA_CHECK(call) do { \
    cudaError_t err = (call); \
    if (err != cudaSuccess) { \
        fprintf(stderr, "CUDA error at %s:%d: %s\n", \
                __FILE__, __LINE__, cudaGetErrorString(err)); \
        exit(1); \
    } \
} while (0)
```

---

## 測試用例與初始化

在 Colab 執行這段確認你的環境和 CPU 答案：

```cuda
// test_setup.cu  （Colab 預期行為，未在本機實測）
#include "reduction_common.cuh"

int main() {
    // 顯示 GPU 資訊
    cudaDeviceProp prop;
    cudaGetDeviceProperties(&prop, 0);
    printf("GPU: %s (sm_%d%d)\n", prop.name,
           prop.major, prop.minor);
    printf("SMs: %d, Global Mem: %.1f GB\n",
           prop.multiProcessorCount,
           prop.totalGlobalMem / 1e9);
    printf("Peak BW: %.1f GB/s\n\n",
           2.0 * prop.memoryClockRate * 1e3
           * prop.memoryBusWidth / 8 / 1e9);

    // 初始化資料
    const int N = 1 << 20;  // 1M
    float *h_data = new float[N];
    srand(42);
    for (int i = 0; i < N; i++)
        h_data[i] = (float)rand() / RAND_MAX;  // [0, 1] 均勻分布

    float expected = cpu_sum(h_data, N);
    printf("N = %d, CPU sum = %.4f\n", N, expected);
    printf("Expected ~%.0f (uniform [0,1])\n\n", N * 0.5f);

    delete[] h_data;
    return 0;
}
```

預期輸出（Colab T4 預期，未在本機實測）：
```
GPU: Tesla T4 (sm_75)
SMs: 40, Global Mem: 15.8 GB
Peak BW: 320.0 GB/s

N = 1048576, CPU sum = 524358.1250
Expected ~524288 (uniform [0,1])
```

---

## V1：Interleaved Addressing（有 Divergence + Bank Conflict）

### 問題是什麼

這是「想到就能寫出來」的版本，也是兩個問題的完整示範。

```cuda
// v1_interleaved.cu
__global__ void reduce_v1(float *g_idata, float *g_odata, unsigned int n) {
    extern __shared__ float smem[];

    unsigned int tid = threadIdx.x;
    unsigned int i   = blockIdx.x * blockDim.x + threadIdx.x;

    smem[tid] = (i < n) ? g_idata[i] : 0.0f;
    __syncthreads();

    // Interleaved addressing：stride 從 1 開始，每輪翻倍
    for (unsigned int s = 1; s < blockDim.x; s *= 2) {
        if (tid % (2 * s) == 0) {      // 問題1：% 運算 → warp divergence
            smem[tid] += smem[tid + s]; // 問題2：stride 是 s，同一 warp 的 thread
                                        //        存取 bank 0, 2, 4, 8... → bank conflict
        }
        __syncthreads();
    }

    if (tid == 0) g_odata[blockIdx.x] = smem[0];
}
```

**問題 1：Warp Divergence**（[Ch 21](./21-warp-divergence.md) 講過）

第 1 輪（s=1）：`if (tid % 2 == 0)`
- Warp 0 的 32 個 thread：tid 0, 1, 2, ..., 31
- tid 0, 2, 4, ..., 30 執行，tid 1, 3, 5, ..., 31 等待
- 一個 warp 裡有兩個分支 → divergence，吞吐減半

第 2 輪（s=2）：`if (tid % 4 == 0)`
- 只有 tid 0, 4, 8, ..., 28 執行（8 個 thread）
- 一個 warp 25% 利用率

每一輪 divergence 越來越嚴重。

**問題 2：Bank Conflict**（[Ch 19](./19-bank-conflict.md) 講過）

第 1 輪（s=1）：thread 0 存取 smem[0] 和 smem[1]，thread 2 存取 smem[2] 和 smem[3]
- Shared memory 有 32 個 bank，連續 4-byte 的元素在不同 bank
- `smem[0]` 在 bank 0，`smem[1]` 在 bank 1，`smem[2]` 在 bank 2...
- 這一輪其實沒有 bank conflict（每個 thread 存取相鄰的 bank）

第 2 輪（s=2）：thread 0 存取 smem[0] 和 smem[2]，thread 4 存取 smem[4] 和 smem[6]
- smem[0] 在 bank 0，smem[2] 在 bank 2，smem[4] 在 bank 4...
- 這輪也沒有衝突

等等，Harris 說 V1 有 bank conflict——這需要更仔細分析。問題不是同一輪的 thread 互相衝突，而是 `s` 成長到讓 **stride = warpSize / 2** 時：
第 5 輪（s=16）：thread 0 存取 smem[0] 和 smem[16]，兩個都在 bank 0 和 bank 16
- Bank 0 和 Bank 16 是不同的 bank，沒有衝突

實際上 V1 的 bank conflict 影響在某些分析框架下取決於 bank count 和 stride 的關係。Harris 的 G80 有 16 bank，而現代 GPU（包括 T4）有 32 bank——bank conflict 的嚴重程度不同。**主要問題還是 divergence**。

### 卡住了？

<details>
<summary>提示：為什麼 `tid % (2*s) == 0` 會造成 divergence？</summary>

在同一個 warp（32 個 thread）裡，不同 thread 的 tid 值不同。`tid % (2*s)` 的結果不一樣，所以 `if` 的條件對某些 thread 為真、對某些為假。SIMT 的本質是同一 warp 所有 thread 同步執行，條件不符的 thread 必須空轉等待（或被 predicated off）。

</summary>
</details>

### 啟動方式

```cuda
int blocks  = (N + 255) / 256;
int threads = 256;
// g_odata 大小需要 blocks 個 float（每個 block 的部分和）
// 最後再做一次 reduction 把 blocks 個部分和加起來，
// 或者用 atomicAdd 收到單一位置

// 法一：atomicAdd（簡單）
float *d_out;
cudaMalloc(&d_out, sizeof(float));
cudaMemset(d_out, 0, sizeof(float));
// 注意：這個版本的 g_odata 收的是每個 block 的結果
// 需要在 kernel 結束後再做一次 reduce，或改用 atomicAdd 版本

// 法二：兩階段 reduce
float *d_partial;
cudaMalloc(&d_partial, blocks * sizeof(float));
reduce_v1<<<blocks, threads, threads * sizeof(float)>>>(d_in, d_partial, N);
// 再對 d_partial 跑一次 reduce（blocks 通常遠小於 N）
```

---

## V2：消除 Divergence（還有 Bank Conflict）

### 改了什麼

把 `tid % (2*s) == 0` 的 interleaved 模式，換成讓**低索引的 thread 做加法，高索引的閒著**。關鍵是條件只依賴 `tid < s`，而不是 `tid % 2*s == 0`：

```cuda
__global__ void reduce_v2(float *g_idata, float *g_odata, unsigned int n) {
    extern __shared__ float smem[];

    unsigned int tid = threadIdx.x;
    unsigned int i   = blockIdx.x * blockDim.x + threadIdx.x;

    smem[tid] = (i < n) ? g_idata[i] : 0.0f;
    __syncthreads();

    for (unsigned int s = 1; s < blockDim.x; s *= 2) {
        // 改動：計算這個 thread 要存取的 index
        int index = 2 * s * tid;     // tid 0 → smem[0] + smem[s]
                                      // tid 1 → smem[2s] + smem[3s]
                                      // ...
        if (index < blockDim.x) {
            smem[index] += smem[index + s];
        }
        __syncthreads();
    }

    if (tid == 0) g_odata[blockIdx.x] = smem[0];
}
```

**消除了什麼 divergence**：

第 1 輪（s=1，index = 2*tid）：
- tid 0, 1, 2, ..., 127 的 index = 0, 2, 4, ..., 254（全部 < 256，全部執行）
- tid 128, 129, ..., 255 的 index = 256, 258, ..., 510（>= 256，不執行）
- Warp 0（tid 0–31）：全部執行，無 divergence ✓
- Warp 1（tid 32–63）：全部執行，無 divergence ✓
- Warp 4（tid 128–159）：前半執行後半不執行... 等等，128–159 的 index 全部 >= 256，全部不執行
- **整個 warp 要麼全跑、要麼全不跑**，無 divergence ✓

**但 bank conflict 還在**：

第 1 輪（s=1）：thread 0 存取 smem[0] 和 smem[1]，thread 1 存取 smem[2] 和 smem[3]
- 2-way interleaved，每次讀兩個相鄰元素，沒有 bank conflict

第 2 輪（s=2）：thread 0 存取 smem[0] 和 smem[2]，thread 1 存取 smem[4] 和 smem[6]
- stride = 2，對 32-bank GPU：smem[0] 在 bank 0，smem[2] 在 bank 2，無衝突

第 3 輪（s=4）：thread 0 存取 smem[0] 和 smem[4]，thread 1 存取 smem[8] 和 smem[12]
- stride = 4，無衝突

對 32-bank GPU，stride 要等於 32 才有 2-way conflict，stride = 64 才有 2-way（64 % 32 = 0）。

Harris 的原報告是在 16-bank 的 G80 上測的，那時 stride=16 就有 2-way conflict。T4 有 32 bank，V2 的 bank conflict 影響小得多。**但 V3 的 sequential addressing 仍然更乾淨，是正確的優化方向。**

（Harris 在 G80 上：V1 → V2 約 1.3x 加速，主要來自消除 divergence。你的 T4 數字用 Nsight 驗證。）

---

## V3：Sequential Addressing（消除 Bank Conflict）

### 改了什麼

核心思路：把 stride 從小到大（V1/V2 的做法）改成**從大到小**。

```
初始：[a0, a1, a2, a3, a4, a5, a6, a7]  （8 個元素示意）

V3 第 1 輪（s = blockDim/2 = 4）：
  thread 0: smem[0] += smem[4]  → a0+a4
  thread 1: smem[1] += smem[5]  → a1+a5
  thread 2: smem[2] += smem[6]  → a2+a6
  thread 3: smem[3] += smem[7]  → a3+a7
  [a0+a4, a1+a5, a2+a6, a3+a7, ...]  （只用前半）

第 2 輪（s = 2）：
  thread 0: smem[0] += smem[2]  → a0+a4+a2+a6
  thread 1: smem[1] += smem[3]  → a1+a5+a3+a7
  [a0+a4+a2+a6, a1+a5+a3+a7, ...]

第 3 輪（s = 1）：
  thread 0: smem[0] += smem[1]  → 全部的和
```

存取樣式：每輪都是 **連續的 thread 存取連續的 shared memory bank**，perfect coalescing（對 shared memory 而言），無 bank conflict。

```cuda
__global__ void reduce_v3(float *g_idata, float *g_odata, unsigned int n) {
    extern __shared__ float smem[];

    unsigned int tid = threadIdx.x;
    unsigned int i   = blockIdx.x * blockDim.x + threadIdx.x;

    smem[tid] = (i < n) ? g_idata[i] : 0.0f;
    __syncthreads();

    // Sequential addressing：stride 從 blockDim/2 開始往下
    for (unsigned int s = blockDim.x / 2; s > 0; s >>= 1) {
        if (tid < s) {
            smem[tid] += smem[tid + s];  // 連續 thread 存取連續位址，無 bank conflict
        }
        __syncthreads();
    }

    if (tid == 0) g_odata[blockIdx.x] = smem[0];
}
```

**存取分析（s = blockDim/2 = 128 時）**：
- thread 0 存取 smem[0]（bank 0）和 smem[128]（bank 0 + 128 % 32 = bank 0）
- 注意：smem[0] 和 smem[128] 都在 bank 0！這是 2-way bank conflict！

等等，讓我仔細算：T4 的 shared memory bank 以 4 bytes（一個 float）為單位。`smem[i]` 在 bank `i % 32`：
- smem[0] → bank 0，smem[128] → bank 128 % 32 = bank 0 → **2-way conflict！**

這是 V3 的一個微妙問題：**當 s 是 32 的倍數時，有 2-way bank conflict**。但這只出現在最開始的幾輪（s = 128, 64），之後（s = 32, 16, ...）就沒有衝突了。

Harris 的原始報告中 V3 也確實存在這個問題，但他的 G80 只有 16-bank，`s = 64, 32` 時有衝突，而 T4 是 `s = 128, 64` 時有衝突。整體影響仍然比 V2 小，因為只有少數幾輪有衝突。

（Harris 在 G80 上：V2 → V3 約 1.2x 加速。你的 T4 數字用 Nsight 驗證。）

---

## V4：First Add During Load（解決一半 Thread 閒置）

### 問題：第一輪就有一半 thread 閒著

V3 的第一輪：`s = blockDim/2 = 128`，只有 tid 0–127 的 128 個 thread 做加法，tid 128–255 的 128 個 thread 在 `__syncthreads` 之後就什麼都不做了。

換句話說，你啟動了 256 個 thread，但第一輪只有 128 個在工作——浪費了 50% 的 thread。

### 解法：在 load 的時候就做第一次相加

把 grid 大小砍半，每個 block 處理 `2 * blockDim` 個元素，每個 thread 在 load 進 shared memory 時就順便把兩個元素加起來：

```cuda
__global__ void reduce_v4(float *g_idata, float *g_odata, unsigned int n) {
    extern __shared__ float smem[];

    unsigned int tid = threadIdx.x;
    // 注意：gridDim.x 已是 N / (2 * blockDim.x)
    // 每個 block 負責 2 * blockDim 個元素
    unsigned int i = blockIdx.x * (blockDim.x * 2) + threadIdx.x;

    // First add during load：每個 thread 讀兩個元素，直接相加存進 smem
    float val = (i < n) ? g_idata[i] : 0.0f;
    if (i + blockDim.x < n) val += g_idata[i + blockDim.x];
    smem[tid] = val;
    __syncthreads();

    // 從 blockDim/2 開始（因為 load 時已做了第一次 reduce）
    for (unsigned int s = blockDim.x / 2; s > 0; s >>= 1) {
        if (tid < s) {
            smem[tid] += smem[tid + s];
        }
        __syncthreads();
    }

    if (tid == 0) g_odata[blockIdx.x] = smem[0];
}

// 啟動時 blocks 要砍半：
// int blocks = (N + (threads * 2) - 1) / (threads * 2);
// reduce_v4<<<blocks, threads, threads * sizeof(float)>>>(d_in, d_partial, N);
```

**效果**：
- Grid 大小砍半（N / (2 * 256) 個 block）→ 佔用更少 SM 資源
- 每個 thread 做的有效工作翻倍
- 第一輪不再有 50% 閒置

（Harris 在 G80 上：V3 → V4 約 1.3–1.5x 加速。你的 T4 數字用 Nsight 驗證。）

### 卡住了？

<details>
<summary>提示：為什麼 blocks 要砍半？</summary>

V3 用 `blocks = N / 256`，每個 block 的 thread 負責 1 個元素。V4 每個 thread 在 load 時負責 2 個元素，所以同樣 256 個 thread 的 block 能處理 512 個元素，只需要 `N / 512 = N / (256 * 2)` 個 block。

如果不砍半，你的 block 會讀到超出陣列範圍的記憶體（需要更嚴格的 bounds check）或得到錯誤結果。

</details>

---

## V5：Unroll Last Warp（不用 `__syncthreads` 收尾）

### 問題：最後幾輪的 `__syncthreads` 是浪費

當 `s <= 32`，active thread 數量 ≤ 32，剛好一個 warp。

在 Volta 之前（Maxwell、Pascal），同一個 warp 的 32 個 thread 保證 lockstep 執行——不需要 `__syncthreads` 就能保證一致性。可以把最後幾輪展開，去掉 `__syncthreads`。

但 Volta 引入了 **independent thread scheduling**（[Ch 10](./10-warp-simt-execution.md) 講過）：warp 內的 thread 不再保證 lockstep。所以 Volta+ 必須用 `__syncwarp()` 替代舊的 `volatile` 技巧。

```cuda
// V5 的 warp unroll 函式（Volta+ 正確寫法）
__device__ void warpReduce(volatile float *smem, unsigned int tid) {
    // 注意：這裡用 __syncwarp() 而不是舊的 volatile 技巧
    // volatile 在 Volta+ 不能保證跨 thread 的可見性
    if (blockDim.x >= 64) { smem[tid] += smem[tid + 32]; __syncwarp(); }
    if (blockDim.x >= 32) { smem[tid] += smem[tid + 16]; __syncwarp(); }
    if (blockDim.x >= 16) { smem[tid] += smem[tid +  8]; __syncwarp(); }
    if (blockDim.x >= 8)  { smem[tid] += smem[tid +  4]; __syncwarp(); }
    if (blockDim.x >= 4)  { smem[tid] += smem[tid +  2]; __syncwarp(); }
    if (blockDim.x >= 2)  { smem[tid] += smem[tid +  1]; __syncwarp(); }
}

__global__ void reduce_v5(float *g_idata, float *g_odata, unsigned int n) {
    extern __shared__ float smem[];

    unsigned int tid = threadIdx.x;
    unsigned int i   = blockIdx.x * (blockDim.x * 2) + threadIdx.x;

    float val = (i < n) ? g_idata[i] : 0.0f;
    if (i + blockDim.x < n) val += g_idata[i + blockDim.x];
    smem[tid] = val;
    __syncthreads();

    // 主 loop：只跑到 s = warpSize（32）
    for (unsigned int s = blockDim.x / 2; s > 32; s >>= 1) {
        if (tid < s) smem[tid] += smem[tid + s];
        __syncthreads();
    }

    // 最後一個 warp 展開，用 __syncwarp() 替代 __syncthreads()
    if (tid < 32) warpReduce(smem, tid);

    if (tid == 0) g_odata[blockIdx.x] = smem[0];
}
```

**關鍵細節**：

為什麼 `warpReduce` 用 `volatile float *smem`？這是 Volta 之前的寫法——告訴編譯器不要 cache 這個值（直接讀寫記憶體，讓其他 thread 看到最新值）。但在 Volta+，`volatile` 對 shared memory 的語意不夠強，**必須加 `__syncwarp()`**。

如果你的目標是 T4（sm_75, Turing = Volta 架構的後繼），`__syncwarp()` 是必要的。移除 `__syncwarp()` 在 Turing+ 上是 undefined behavior，有機率得到錯誤結果。

（Harris 在 G80 上：V4 → V5 約 1.1–1.2x 加速。T4 的效果可能更明顯，因為消除了更多的 warp-level sync barrier。你的數字用 Nsight 驗證。）

---

## V6：完全展開（Template + `#pragma unroll`）

### 改了什麼

主 loop 的 `for (s = blockDim/2; s > 32; s >>= 1)` 在運行期每次都要計算 `s`、比較、跳轉。如果 blockDim 在編譯期已知（透過 template），編譯器可以把整個 loop 展開，消除 branch 和 loop overhead。

```cuda
template <unsigned int blockSize>
__device__ void warpReduceT(volatile float *smem, unsigned int tid) {
    // blockSize 在編譯期已知，if 條件可以在編譯期消除
    if (blockSize >= 64) { smem[tid] += smem[tid + 32]; __syncwarp(); }
    if (blockSize >= 32) { smem[tid] += smem[tid + 16]; __syncwarp(); }
    if (blockSize >= 16) { smem[tid] += smem[tid +  8]; __syncwarp(); }
    if (blockSize >= 8)  { smem[tid] += smem[tid +  4]; __syncwarp(); }
    if (blockSize >= 4)  { smem[tid] += smem[tid +  2]; __syncwarp(); }
    if (blockSize >= 2)  { smem[tid] += smem[tid +  1]; __syncwarp(); }
}

template <unsigned int blockSize>
__global__ void reduce_v6(float *g_idata, float *g_odata, unsigned int n) {
    extern __shared__ float smem[];

    unsigned int tid = threadIdx.x;
    unsigned int i   = blockIdx.x * (blockSize * 2) + threadIdx.x;

    float val = (i < n) ? g_idata[i] : 0.0f;
    if (i + blockSize < n) val += g_idata[i + blockSize];
    smem[tid] = val;
    __syncthreads();

    // 主 loop 完全展開（blockSize 在編譯期已知）
    if (blockSize >= 512) {
        if (tid < 256) smem[tid] += smem[tid + 256];
        __syncthreads();
    }
    if (blockSize >= 256) {
        if (tid < 128) smem[tid] += smem[tid + 128];
        __syncthreads();
    }
    if (blockSize >= 128) {
        if (tid <  64) smem[tid] += smem[tid +  64];
        __syncthreads();
    }

    // 最後一個 warp 展開
    if (tid < 32) warpReduceT<blockSize>(smem, tid);

    if (tid == 0) g_odata[blockIdx.x] = smem[0];
}

// 啟動（必須用 switch 選 template 實例化）：
void launch_reduce_v6(float *d_in, float *d_out, int N, int threads) {
    int blocks = (N + threads * 2 - 1) / (threads * 2);
    int smem   = threads * sizeof(float);
    switch (threads) {
        case 512: reduce_v6<512><<<blocks, 512, smem>>>(d_in, d_out, N); break;
        case 256: reduce_v6<256><<<blocks, 256, smem>>>(d_in, d_out, N); break;
        case 128: reduce_v6<128><<<blocks, 128, smem>>>(d_in, d_out, N); break;
        case  64: reduce_v6< 64><<<blocks,  64, smem>>>(d_in, d_out, N); break;
        case  32: reduce_v6< 32><<<blocks,  32, smem>>>(d_in, d_out, N); break;
    }
}
```

**編譯器會做什麼**：

當 `blockSize = 256` 時，`if (blockSize >= 512)` 是編譯期常數 `false`，整個 if body 被丟棄。`if (blockSize >= 256)` 是 `true`，只保留這個 branch，loop 消失，只剩下 log₂(256) = 8 條展開的 smem 加法指令。

（Harris 在 G80 上：V5 → V6 約 1.1x 加速，主要是消除 loop overhead 和 branch prediction miss。T4 的效果用 Nsight 驗證。）

---

## V7：Multiple Elements per Thread + Warp Shuffle（最終版）

### 改了什麼（兩個獨立改進）

**改進 A：Multiple elements per thread（Grid-stride load）**

V4 讓每個 thread 在 load 時處理 2 個元素（砍 blocks 一半）。繼續推廣：讓每個 thread 在 load 時處理更多元素，減少 blocks 數量（減少 launch overhead 和 block synchronization overhead），同時讓每個 thread 有更多 ILP。

```cuda
// Grid-stride load：每個 thread 處理 ELEMENTS_PER_THREAD 個元素
// 在 load 時直接累加到 val
```

**改進 B：Warp Shuffle 完全取代最後的 Shared Memory Reduction**

V5/V6 仍然使用 shared memory 存最後 32 個值，再用 warp-level 展開。最終版用 `__shfl_down_sync`（[Ch 22](./22-atomics-reduction.md) 詳細介紹）完全替代：

```cuda
__device__ float warp_reduce_sum(float val) {
    // 5 次 shuffle 完成 32-thread reduction，不讀寫 shared memory
    for (int offset = warpSize / 2; offset > 0; offset >>= 1)
        val += __shfl_down_sync(0xffffffff, val, offset);
    return val;
}

template <unsigned int blockSize>
__global__ void reduce_v7(const float *g_idata, float *g_odata,
                           unsigned int n) {
    // 第一部分：grid-stride load，每個 thread 處理多個元素
    float val = 0.0f;
    for (unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
         i < n;
         i += gridDim.x * blockDim.x) {
        val += g_idata[i];
    }

    // 第二部分：block-level reduce
    // 先用 shared memory 把 blockDim 個 val 收到 warpSize 個
    extern __shared__ float smem[];
    unsigned int tid     = threadIdx.x;
    unsigned int lane    = tid % warpSize;   // [0, 31]
    unsigned int warp_id = tid / warpSize;   // [0, blockDim/32 - 1]

    // Warp-level reduce（不需要 shared memory）
    val = warp_reduce_sum(val);

    // 每個 warp 的 lane 0 把結果寫進 shared memory
    if (lane == 0) smem[warp_id] = val;
    __syncthreads();

    // 第一個 warp 把所有 warp 的結果收起來
    // （每個 block 有 blockSize / 32 個 warp）
    val = (tid < blockSize / warpSize) ? smem[tid] : 0.0f;
    if (warp_id == 0) val = warp_reduce_sum(val);

    // Block 的結果用 atomicAdd 收到全局輸出
    if (tid == 0) atomicAdd(g_odata, val);
}

// 啟動 V7：
void launch_reduce_v7(const float *d_in, float *d_out, int N) {
    int threads = 256;
    // Grid-stride：blocks 數量可以比 N/threads 少很多
    // 選一個能填滿 GPU 的數量（cudaOccupancyMaxActiveBlocksPerMultiprocessor）
    int blocks_per_sm;
    cudaOccupancyMaxActiveBlocksPerMultiprocessor(
        &blocks_per_sm, reduce_v7<256>, threads,
        (threads / 32) * sizeof(float)  // shared memory = warp 數 * float
    );
    cudaDeviceProp prop;
    cudaGetDeviceProperties(&prop, 0);
    int blocks = blocks_per_sm * prop.multiProcessorCount;
    // 也可以直接用個合理的數字，例如 blocks = min(gridDim, N/threads)

    // smem 大小只需要存 warp 數個 float（不是 blockDim 個）
    int smem = (threads / 32) * sizeof(float);  // = 8 * 4 = 32 bytes（256 threads）

    reduce_v7<256><<<blocks, threads, smem>>>(d_in, d_out, N);
    cudaDeviceSynchronize();
}
```

**V7 的優點整理**：
1. Grid-stride loop 讓 blocks 數量最小化，每個 SM 的 block 切換 overhead 降低
2. Warp shuffle 做第一層 reduce（每個 warp 的 32 個 thread）：零 shared memory，5 次 shuffle
3. Shared memory 只存 `blockSize / 32` 個 float（256 threads → 8 個 float），遠少於 V1-V6 的 blockDim 個
4. Warp 0 再做一次 shuffle 收最後幾個 warp 的結果
5. AtomicAdd 做最終收斂（每個 block 一次，contention 極低）

### 卡住了？

<details>
<summary>提示：grid-stride loop 的 stride 是多少？</summary>

`stride = gridDim.x * blockDim.x`，也就是整個 grid 的 thread 總數。

thread `tid` 在全局中的位置是 `gid = blockIdx.x * blockDim.x + tid`，它負責 `g_idata[gid]`、`g_idata[gid + stride]`、`g_idata[gid + 2*stride]`... 直到超出陣列範圍。

這樣無論 N 多大，只要 blocks 數夠大讓 GPU 保持 busy，就不需要 blocks 和 N 嚴格對應。

</details>

<details>
<summary>提示：為什麼 smem 只需要 blockSize/32 個 float？</summary>

V1-V6 的 shared memory 存的是「每個 thread 的中間結果」（blockDim 個 float）。V7 先用 warp shuffle 把每個 warp（32 個 thread）的結果歸到 lane 0（1 個 float），再存進 smem。一個 block 有 blockDim / 32 個 warp，所以 smem 只需要存 blockDim/32 個 float。

256 threads = 8 warps → smem 只需要 8 個 float = 32 bytes，比 V6 的 256 * 4 = 1 KB 少得多。更小的 smem 佔用讓 SM 能同時承載更多 block（更高 occupancy），或釋放資源給其他 kernel。

</details>

---

## 完整測試驅動 + 七版對比

```cuda
// reduction_benchmark.cu  （Colab 預期行為，未在本機實測）
#include <cuda_runtime.h>
#include <cstdio>
#include <cmath>
#include <functional>

// [把上面 7 個版本的 kernel 都 include 進來]

double cpu_sum_double(const float *data, int n) {
    double sum = 0.0;
    for (int i = 0; i < n; i++) sum += data[i];
    return sum;
}

float time_ms(std::function<void()> fn, int warmup=5, int runs=100) {
    for (int i = 0; i < warmup; i++) fn();
    cudaDeviceSynchronize();
    cudaEvent_t s, e;
    cudaEventCreate(&s); cudaEventCreate(&e);
    cudaEventRecord(s);
    for (int i = 0; i < runs; i++) fn();
    cudaEventRecord(e);
    cudaEventSynchronize(e);
    float ms; cudaEventElapsedTime(&ms, s, e);
    cudaEventDestroy(s); cudaEventDestroy(e);
    return ms / runs;
}

int main() {
    const int N       = 1 << 20;  // 1M
    const int THREADS = 256;

    // --- Host setup ---
    float *h_data = new float[N];
    srand(42);
    for (int i = 0; i < N; i++) h_data[i] = (float)rand() / RAND_MAX;
    double cpu_answer = cpu_sum_double(h_data, N);

    // --- Device setup ---
    float *d_in, *d_out, *d_partial;
    cudaMalloc(&d_in,      N * sizeof(float));
    cudaMalloc(&d_out,     sizeof(float));
    cudaMalloc(&d_partial, ((N + THREADS*2 - 1) / (THREADS*2)) * sizeof(float));
    cudaMemcpy(d_in, h_data, N * sizeof(float), cudaMemcpyHostToDevice);

    printf("%-10s %8s %8s %8s %8s\n",
           "Version", "Time(us)", "Speedup", "BW(GB/s)", "Correct");
    printf("%-10s %8.1f %8s %8s %8s\n",
           "V0(CPU)", cpu_sum_double(h_data, N) > 0 ? 1.0f : 0.0f,
           "-", "-", "yes");

    // 輔助 lambda：驗證結果
    auto verify = [&](const char *name, float result, float t_us) {
        bool ok = fabs(result - cpu_answer) / cpu_answer < 1e-3;
        float bw_gbs = (float)N * sizeof(float) / (t_us * 1e-6) / 1e9;
        static float v1_time = t_us;
        printf("%-10s %8.1f %8.2fx %8.1f %8s\n",
               name, t_us, v1_time / t_us, bw_gbs, ok ? "yes" : "NO!");
    };

    // V1
    {
        int blocks = (N + THREADS - 1) / THREADS;
        cudaMemset(d_out, 0, sizeof(float));
        // 兩階段：先 reduce 到 d_partial，再 reduce d_partial
        auto fn = [&]() {
            cudaMemset(d_out, 0, sizeof(float));
            reduce_v1<<<blocks, THREADS, THREADS*sizeof(float)>>>(d_in, d_partial, N);
            reduce_v1<<<1, THREADS, THREADS*sizeof(float)>>>(d_partial, d_out, blocks);
        };
        float t = time_ms(fn) * 1000;  // convert to μs
        float result;
        fn(); cudaMemcpy(&result, d_out, sizeof(float), cudaMemcpyDeviceToHost);
        verify("V1", result, t);
    }

    // V2-V6 類似（省略，結構相同）...

    // V7
    {
        cudaMemset(d_out, 0, sizeof(float));
        auto fn = [&]() {
            cudaMemset(d_out, 0, sizeof(float));
            launch_reduce_v7(d_in, d_out, N);
        };
        float t = time_ms(fn) * 1000;
        float result;
        cudaMemcpy(&result, d_out, sizeof(float), cudaMemcpyDeviceToHost);
        verify("V7", result, t);
    }

    cudaFree(d_in); cudaFree(d_out); cudaFree(d_partial);
    delete[] h_data;
    return 0;
}
```

預期輸出（Colab T4 預期行為，未在本機實測）：

```
Version     Time(us)  Speedup  BW(GB/s)  Correct
V1           850.2      1.00x     4.9      yes
V2           652.1      1.30x     6.4      yes
V3           485.3      1.75x     8.6      yes
V4           312.4      2.72x    13.4      yes
V5           278.9      3.05x    14.9      yes
V6           261.7      3.25x    16.0      yes
V7           198.3      4.29x    21.1      yes
```

（T4 的 DRAM peak = 320 GB/s；這個 kernel 的最高 bandwidth 理論上限由算術強度決定。Sum reduction 的算術強度極低 = 1 FLOP / 4 bytes，所以 memory-bound 且絕對達不到 compute peak。但 21 GB/s 只有 peak 的 6.6%——主因是 shared memory reduction 的多輪 sync overhead，而非 global memory 頻寬不夠。V7 的 warp shuffle 版本還有進一步優化空間，例如用 `cp.async`（sm_80+）做 prefetch。）

---

## 延伸挑戰

1. **V7 的 AtomicAdd 到底貢獻多少 overhead**？用 `ncu --metrics l1tex__t_sectors_pipe_lsu_mem_global_op_atom.sum` 量 V7 的 atomic 操作次數，驗證確實只有 `gridDim.x` 次。

2. **消除 AtomicAdd**：把 V7 改成兩階段 reduction（不用 atomic），比較 wall time。atomic 版本更快還是兩階段更快？為什麼？

3. **不同 block size 的影響**：固定 N = 1M，改變 threads = 64 / 128 / 256 / 512，跑 V7，畫出 bandwidth 和 time 的關係。

4. **Double precision**：把 V7 改成 `double` 版本。T4 的 FP64 throughput只有 FP32 的 1/32（約 2 GFLOPS），你的 double reduction 比 float 版本慢幾倍？

5. **CUB 對比**：用 `cub::DeviceReduce::Sum` 跑同樣的 N = 1M，時間是多少？比 V7 快多少倍？試著分析 CUB 為什麼更快（hint：Nsight Compute 看 V7 的 stall reason，再看 CUB 的）。

---

## 自我檢核

完成以下問題才算真正吃透這個練習：

1. V1 的兩個問題（divergence 和 bank conflict）各出現在 for loop 的哪一輪？用具體的 tid 值說明。
2. V3 中 `s >>= 1`（stride 每輪右移 1 位）等價於什麼數學操作？為什麼 sequential addressing 比 interleaved 更不容易造成 bank conflict？
3. V4 把 blocks 砍半後，global memory 讀取次數有沒有減少？load 進 shared memory 的 float 個數有沒有減少？
4. 為什麼 V5 在 `warpReduce` 裡用 `__syncwarp()` 而不是像 V1-V4 那樣用 `__syncthreads()`？在 Volta 之前能不能直接去掉同步？
5. V7 的 `smem` 大小只有 `blockSize / warpSize` 個 float，比 V1-V6 小很多。這個縮小除了省 shared memory 空間，還對 occupancy 有什麼影響（見 [Ch 11](./11-occupancy.md) / [Ch 19](./19-bank-conflict.md)）？
6. 如果你要在 sm_80（A100）上跑，V7 還能怎麼改進？（hint：`__reduce_add_sync`，[Ch 22](./22-atomics-reduction.md) 提到了）

---

## 參考解答

<details>
<summary>Q1：V1 的 divergence 和 bank conflict</summary>

**Divergence**：在 `if (tid % (2*s) == 0)` 中，以第一輪 s=1 為例，warp 0（tid 0–31）中，tid 0, 2, 4, ..., 30 執行（條件為真），tid 1, 3, 5, ..., 31 不執行（條件為假）。同一 warp 兩種 path，divergence。

**Bank Conflict（在 16-bank 的 G80 上，Harris 的原始分析）**：以 s=16（G80 的半個 bank 數）為例，thread 0 存取 smem[0] 和 smem[16]，都對應 bank 0（0 % 16 = 0，16 % 16 = 0）→ 2-way bank conflict。T4 有 32 bank，s=32 時才有 2-way conflict。

</details>

<details>
<summary>Q3：V4 的 global memory 讀取次數</summary>

不變。V4 用了 N/(2*256) 個 block，每個 block 每個 thread 讀 2 個元素，總計還是 N 個 float = N * 4 bytes 的 global memory 讀取。load 進 shared memory 的 float 個數也是 blockDim = 256 個（每個 thread load 了 2 個但相加後存 1 個進 smem）。V4 的加速來自「第一次 reduction 在 load 時做掉了，shared memory 裡已經是 N/2 個元素的 reduction 結果」，不是讀取量減少。

</details>

<details>
<summary>Q5：smem 縮小對 occupancy 的影響</summary>

Shared memory 是 SM 的有限資源（T4 每 SM 96 KB）。V1-V6 每個 block 用 blockDim * sizeof(float) = 256 * 4 = 1024 bytes = 1 KB。V7 每個 block 用 (blockDim/32) * 4 = 8 * 4 = 32 bytes。

資源限制下的最大活躍 block 數：
- V6：96 KB / 1 KB = 96 block/SM（但還要考慮 register 和 warp 限制）
- V7：96 KB / 32 bytes = 3072 block/SM（shared memory 不再是瓶頸）

V7 的 occupancy 主要被 register 數量限制，而不是 shared memory，讓編譯器有更多自由度分配 register（不需要為了節省 smem 而 spill）。

</details>

---

→ [Ch 26 編譯流程](./26-compilation-pipeline.md)
