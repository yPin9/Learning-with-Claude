# Ch 21 — Warp Divergence 消除：讓 32 個 thread 再次齊步走

> **目標**：理解 warp divergence 的三大來源，掌握四種消除技巧（邊界對齊、branchless、資料重排、warp vote functions），並避開 Volta+ Independent Thread Scheduling 帶來的新陷阱。
>
> **環境**：CUDA 12.x, Colab T4 (sm\_75)
>
> **前置**：[Ch 10 warp 與 SIMT 執行](./10-warp-simt-execution.md)、[Ch 11 occupancy](./11-occupancy.md)、[Ch 18 memory coalescing](./18-memory-coalescing.md)

---

## 為什麼需要這章

Ch 10 介紹了 warp divergence 的存在，這章教怎麼消。

SIMT 的代價很清楚：一個 warp（32 個 thread）遇到不同的 branch 時，硬體要把兩條路徑**序列化執行**，部分 thread 在跑 path A 的時候，另一批 thread 的執行單元閒置（masked out），反之亦然。最壞情況下，32 個 thread 走 32 條不同的路，throughput 降成 1/32。

現實中最常見的情況是分成兩路：一半走 A，一半走 B。這樣的 warp 執行時間是無 divergence 的**兩倍**，等效 occupancy 減半。

這件事值得整整一章：因為 divergence 幾乎出現在每一個非玩具 kernel 裡，而且消除方式比「把 if 改掉」複雜得多。

---

## 先建立直覺：SIMT 執行模型

GPU 的 SIMT（Single Instruction, Multiple Threads）模型：warp 內 32 個 thread 共用一個 program counter，每個 cycle 執行同一條指令。遇到分支時，硬體用 **predicate mask**（32-bit register）記錄哪些 thread 的條件為 true，然後：

1. 執行 path A（mask 套上 true 的 thread），false 的 thread 閒置
2. 執行 path B（mask 套上 false 的 thread），true 的 thread 閒置
3. 兩路都跑完，warp 在 join point 重新 converge

```
Warp（32 threads）遇到 if (condition):

Thread：  T0  T1  T2  T3  T4  T5  ...  T30 T31
Cond：     T   F   T   F   T   F  ...   T   F

時間軸：
  Cycle 1-N:   [T0  --  T2  --  T4  --  ...  T30  -- ] 跑 path A
                (奇數 thread 被 mask，ALU 閒置)
  Cycle N+1-M: [--  T1  --  T3  --  T5  ...  --   T31] 跑 path B
                (偶數 thread 被 mask，ALU 閒置)
  Cycle M+1:   所有 thread reconverge，繼續往下

實際執行時間 = path_A_cycles + path_B_cycles
理想執行時間 = max(path_A_cycles, path_B_cycles)
浪費 = min(path_A_cycles, path_B_cycles)
```

---

## Divergence 的三大來源

### 來源 A：threadIdx 的奇偶或餘數分支

```cpp
// 最典型的 divergence：同一 warp 內奇偶分叉
__global__ void bad_kernel(float* data) {
    int tid = threadIdx.x;
    if (tid % 2 == 0) {      // warp 0: T0 走這裡，T1 走 else，T2 走這裡...
        data[tid] *= 2.0f;
    } else {
        data[tid] += 1.0f;
    }
}
```

warp 0 的 T0, T2, T4...T30 走 if，T1, T3...T31 走 else，完美的 intra-warp divergence。

### 來源 B：資料相依分支

```cpp
__global__ void threshold_kernel(float* data, float* out, float threshold, int N) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < N && data[i] > threshold) {   // 每個 thread 的資料值不同
        out[i] = process(data[i]);         // 只有部分 thread 走這裡
    } else {
        out[i] = 0.0f;
    }
}
```

如果資料分佈不規律，同一 warp 內的 thread 看到的 `data[i]` 各不相同，有人大於 threshold 有人小於，就會 diverge。這種 divergence 很難從 code 層面直接看出，要看實際資料。

### 來源 C：邊界條件

```cpp
__global__ void add_kernel(float* a, float* b, float* c, int N) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < N) {        // 最後一個 block 的 tail 部分 thread 這條件為 false
        c[i] = a[i] + b[i];
    }
}
```

當 N 不是 blockDim.x 的倍數，最後一個 block 的某些 thread 會因 `i >= N` 而走不同路。通常只有最後一個 warp 受影響，但如果 N 很小（整個問題只有幾個 block），影響就顯著。

```
N = 100, blockDim.x = 32:
  Block 0 (i = 0..31)：全數 < N，無 divergence
  Block 1 (i = 32..63)：全數 < N，無 divergence
  Block 2 (i = 64..95)：全數 < N，無 divergence
  Block 3 (i = 96..127)：
    Warp 0 (i = 96..127)：i = 100..127 的 thread 走 else
    → 最後一個 warp diverge，前 4 個 thread 做事，後 28 個閒置
```

---

## 消除技巧 1：讓分支對齊 Warp 邊界

核心原則：**同一 warp 的 32 個 thread 要走同一條路**。只要分支邊界對齊 warp 邊界，divergence 就消了。

### 壞 code

```cpp
__global__ void bad_even_odd(float* data, int N) {
    int tid = threadIdx.x;
    int i = blockIdx.x * blockDim.x + tid;
    if (i % 2 == 0) {          // global index 奇偶分叉
        data[i] = do_even(data[i]);
    } else {
        data[i] = do_odd(data[i]);
    }
}
```

warp 0 處理 global i = 0..31，奇偶交錯，每個 warp 都 diverge。

### 好 code

```cpp
// 方案 A：問題重排，讓 even element 和 odd element 分開存放
//         → kernel 本身不用 branch
__global__ void good_separate(float* even_data, float* odd_data, int half_N) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < half_N) {
        even_data[i] = do_even(even_data[i]);
        // odd_data 由另一個 kernel launch 處理
    }
}

// 方案 B：若必須在同一 kernel，讓前半 block 走 A，後半走 B
//         → warp 整體對齊（前面 blockDim.x/2 / 32 個 warp 走 A，其餘走 B）
__global__ void good_warp_aligned(float* data, int N) {
    int tid = threadIdx.x;
    int i = blockIdx.x * blockDim.x + tid;
    // 以 blockDim.x/2 為邊界，而非奇偶
    // 假設 blockDim.x = 64：warp 0 (tid 0-31) 全走 A，warp 1 (tid 32-63) 全走 B
    if (tid < blockDim.x / 2) {
        data[i] = do_even(data[i]);
    } else {
        data[i] = do_odd(data[i]);
    }
}
```

`good_warp_aligned` 中，warp 0（tid 0-31）全部 < blockDim.x/2，走 path A，無 divergence。warp 1（tid 32-63）全部 >= blockDim.x/2，走 path B，無 divergence。

**原則**：任何分支條件，只要確保分支邊界是 32 的倍數（對齊 warp 大小），就能做到 warp-uniform branch。

### 實際應用：Reduction 的 interleaved vs block 分割

```cpp
// 壞：interleaved reduction（標準教科書版本）
// 第一輪：stride=1，tid 0 加 tid+1，tid 2 加 tid+3...
// 每個 warp 內奇偶 thread 行為不同 → divergence
for (int s = 1; s < blockDim.x; s *= 2) {
    if (tid % (2 * s) == 0) {   // 典型 divergence
        sdata[tid] += sdata[tid + s];
    }
    __syncthreads();
}

// 好：sequential（block）reduction
// 第一輪：tid 0..N/2-1 做事，tid N/2..N-1 閒置
// → 每個 warp 要麼全做事，要麼全閒置（只有邊界 warp 可能 diverge）
for (int s = blockDim.x / 2; s > 0; s >>= 1) {
    if (tid < s) {              // 前半 thread 做事，後半不做
        sdata[tid] += sdata[tid + s];
    }
    __syncthreads();
}
```

後者的 divergence 只發生在 `s < 32`（最後幾輪），前者每一輪都 diverge。這個 reduction 的完整優化（用 `__shfl_down_sync` 消掉剩餘 divergence）在 Ch 22 深挖。

---

## 消除技巧 2：Branchless / Predication

思路：把 `if-else` 改成算術運算，讓 GPU 兩路都算，不用跳轉。

### 壞 code

```cpp
__global__ void clamp_kernel(float* data, float lo, float hi, int N) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < N) {
        if (data[i] < lo) {         // branch 1
            data[i] = lo;
        } else if (data[i] > hi) {  // branch 2
            data[i] = hi;
        }
        // else: 不動
    }
}
```

三路分支，若資料分佈不規律，同一 warp 內的 thread 可能走三條不同的路。

### 好 code

```cpp
__global__ void clamp_kernel_branchless(float* data, float lo, float hi, int N) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < N) {
        float val = data[i];
        val = fmaxf(val, lo);   // 無 branch：兩個值都算，取大的
        val = fminf(val, hi);   // 無 branch：兩個值都算，取小的
        data[i] = val;
    }
}
```

`fmaxf`/`fminf` 在 GPU 上是單條指令（FMNMX），無需分支。

### 三元運算子與 predication

```cpp
// 編譯器對三元運算子（?:）可能產生 predicated instruction 而非 branch
float result = (data[i] > 0.0f) ? data[i] : -data[i];  // abs value
// 等價：result = fabsf(data[i]);   ← 更直接

// 整數選擇（branchless select pattern）
int a = 10, b = 20;
int cond = (x > 0);          // 0 或 1
int result = a * cond + b * (1 - cond);   // cond=1 → a，cond=0 → b
// 注意：這種寫法兩路都算，代價是兩次乘法 + 一次加法
// 只在 branch 代價 >> 多餘算術代價時才值得
```

### 重要澄清：predication ≠ 消除 divergence

這是最容易誤解的地方。

Predicated execution 的意思是：**兩條路徑的指令都發射，用 predicate register 決定哪些 thread 的結果要寫回**。它消除的是 branch 指令本身的 overhead（flush pipeline、update PC），**不是**消除兩路都跑的代價。

```
Branch 執行模型（有 divergence）：
  先跑 path A 的指令序列（被 mask 的 thread 等待）
  再跑 path B 的指令序列（先跑的 thread 等待）
  代價 = |path_A| + |path_B|

Predicated 執行模型：
  每條指令都發射，但 mask=0 的 thread 結果丟棄
  代價也是 |path_A| + |path_B|（兩路都跑）
  但消除了 branch 本身的 pipeline stall
```

所以 branchless 技巧的效益是：
- **有效**：path 很短（幾條指令），branch overhead 相對大
- **無效甚至更慢**：path 很長，兩路都算的代價遠大於 branch 的 pipeline stall
- **特別有效**：simple arithmetic 替代 conditional（`fmaxf`、`fminf`、`fabsf`）

### Compiler Hint

```cpp
// CUDA 沒有直接的 __builtin_expect，但可用 __builtin_expect 在 device code
// 更常用的是 __assume（給 NVCC 優化提示）
__device__ void example(int x) {
    __builtin_expect(x > 0, 1);   // 提示 compiler x 幾乎總是 > 0
    // → compiler 可能把 if (x > 0) 的 else 路徑視為 cold path
}
```

這類 hint 對 GPU 的效果不如 CPU 顯著，因為 GPU 不做 branch prediction，但可能影響 compiler 的指令排程。

---

## 消除技巧 3：資料重排（Stream Compaction）

當 divergence 來自資料（來源 B），最根本的解法是**重排資料**，讓走同一條路的 element 聚在一起，由同一個 warp 處理。

### 概念

```
原始資料（random 分佈，threshold 決定走哪路）：
  Index:   0    1    2    3    4    5    6    7
  Value:  12   87   34   91   23   78   45   66
  >50:     F    T    F    T    F    T    F    T
  Warp 0 處理 index 0-7（假設 warp 大小縮到 8 示意）：4個走 F，4個走 T → diverge

重排後（partition by condition）：
  F group: [12, 34, 23, 45, ...]  → warp 處理這些，全走 false path
  T group: [87, 91, 78, 66, ...]  → warp 處理這些，全走 true path
  → 無 divergence
```

### 用 CUB 做 Stream Compaction

```cpp
#include <cub/cub.cuh>
#include <thrust/device_vector.h>

// 把 data 中大於 threshold 的元素壓縮到前面
void compact_and_process(float* d_data, int N, float threshold) {
    // Step 1: partition（把 active element 收集到前面）
    thrust::device_vector<float> d_output(N);
    auto end = thrust::partition(
        thrust::device,
        thrust::device_pointer_cast(d_data),
        thrust::device_pointer_cast(d_data + N),
        [threshold] __device__ (float x) { return x > threshold; }
    );
    int n_active = end - thrust::device_pointer_cast(d_data);

    // Step 2: 用 divergence-free kernel 處理 active 部分
    // 這個 kernel 的 thread 全部走同一路（因為資料已保證 > threshold）
    process_active_kernel<<<(n_active + 255) / 256, 256>>>(
        thrust::raw_pointer_cast(d_output.data()), n_active
    );
}
```

### 代價分析

Stream compaction 本身需要一個 pass（複雜度 O(N)），要值得做，kernel 本身的 divergence 代價必須夠大。

何時值得：
- Kernel 本身很複雜（每個 element 做大量工作），divergence 造成的浪費很大
- N 很大（compaction 的 overhead 被分攤）
- Divergence 很嚴重（50% 以上的 thread 走不同路）

何時不值得：
- Kernel 很簡單（一兩條指令），branchless 更直接
- N 很小，compaction 的 kernel launch overhead 佔比大
- 資料本來就半整齊（只有邊界少數 element 走不同路）

---

## 消除技巧 4：Warp Vote Functions

有時候我們不想重排資料，但想在 kernel 裡**快速偵測整個 warp 的 branch 情況**，然後根據結果選擇執行路徑。這就是 warp vote functions 的用途。

### 四個核心函數

```cpp
// 1. __all_sync(mask, predicate)
//    若 mask 內所有 active thread 的 predicate 都為 true → 回傳非零
//    有任何 thread 為 false → 回傳 0
unsigned int mask = 0xffffffff;
int all_active = __all_sync(mask, i < N);
// 若整個 warp 的 i 都 < N，可以省掉邊界檢查的 branch

// 2. __any_sync(mask, predicate)
//    若 mask 內任一 active thread 的 predicate 為 true → 回傳非零
int any_needs_special = __any_sync(mask, data[i] < 0);
// 若 warp 內無一 thread 需要特殊處理，直接走快速路徑

// 3. __ballot_sync(mask, predicate)
//    回傳 32-bit 整數，bit k 為 thread k 的 predicate 結果
//    每個 thread 拿到的是相同的 32-bit 值（warp 全員共享）
unsigned int ballot = __ballot_sync(mask, data[i] > threshold);
int active_count = __popc(ballot);  // 多少個 thread 的條件為 true

// 4. __activemask()
//    回傳目前 converged 的 active thread mask
//    用於確認 divergence 狀態或動態計算 mask
unsigned int active = __activemask();
```

### mask 參數的意義（Volta+ 必讀）

Pascal 及更早的架構，warp 裡的 thread 在 divergence 後一定在 join point 自動 reconverge（lock-step 執行）。Volta 引入了 **Independent Thread Scheduling**：diverged thread 不再自動 reconverge，可以 interleave 執行。

這帶來一個問題：如果你在 diverged context 裡呼叫 `__ballot_sync(0xffffffff, pred)`，有些 thread 可能還沒到達這行（它們在跑別的路徑），用 `0xffffffff` 假裝所有 32 個 thread 都在這裡是**錯誤的**。

```cpp
// 危險：在 diverged context 裡用 0xffffffff
__global__ void dangerous(int* data) {
    int tid = threadIdx.x;
    if (tid < 16) {
        // 只有前 16 個 thread 在這裡
        // 用 0xffffffff 是錯的：後 16 個 thread 不在這裡
        unsigned int ballot = __ballot_sync(0xffffffff, data[tid] > 0);  // ← 錯
    }
}

// 正確：用 __activemask() 取得目前 converged 的 thread mask
__global__ void safe(int* data) {
    int tid = threadIdx.x;
    if (tid < 16) {
        unsigned int mask = __activemask();  // 只包含目前在這條路徑上的 thread
        unsigned int ballot = __ballot_sync(mask, data[tid] > 0);  // ← 正確
    }
}
```

### 實際應用：快速路徑優化

```cpp
__global__ void adaptive_kernel(float* data, float* out, float threshold, int N) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    float val = (i < N) ? data[i] : 0.0f;

    // 查這個 warp 是否所有 thread 都在邊界內
    unsigned int mask = __activemask();
    int all_in_bounds = __all_sync(mask, i < N);

    // 查這個 warp 是否有任何 element 超過 threshold
    int any_exceeds = __any_sync(mask, val > threshold);

    if (all_in_bounds && !any_exceeds) {
        // 整個 warp 都不需要特殊處理 → 走簡單快速路徑
        out[i] = val * 0.5f;
    } else {
        // 有邊界或有超標 element → 走完整路徑
        if (i < N) {
            out[i] = (val > threshold) ? expensive_process(val) : val * 0.5f;
        }
    }
    // 注意：外層 if-else 本身也可能 diverge（warp 間行為不同），
    // 但 warp 內部的 if/else 由於 __all_sync/__any_sync 的結果對 warp 全員一致，
    // 保證 warp 整體走同一條外層路徑
}
```

### 用 `__ballot_sync` 計算 prefix sum（lane mask）

```cpp
// 計算每個 thread 在 warp 內「自己之前有多少個 active thread」
// 用於 warp-level stream compaction
__device__ int warp_prefix_count(int predicate) {
    unsigned int ballot = __ballot_sync(0xffffffff, predicate);
    int lane = threadIdx.x & 31;                   // 在 warp 內的位置（0-31）
    unsigned int lane_mask = (1u << lane) - 1;     // 比自己小的 lane 的 mask
    return __popc(ballot & lane_mask);             // 數有多少個比自己小的 lane 也是 active
}
```

---

## Reduction 裡的 Divergence（伏筆 Ch 22）

Reduction 是 CUDA 最常見的 pattern，也是 divergence 最常造成問題的地方。這裡先點出問題，Ch 22 用 `__shfl_down_sync` 給出完整解。

### Naive Reduction 的 Divergence 問題

```cpp
// 標準 interleaved reduction（教科書版，有嚴重 divergence）
__global__ void reduce_bad(float* g_data, float* g_out, int N) {
    extern __shared__ float sdata[];
    int tid = threadIdx.x;
    sdata[tid] = g_data[blockIdx.x * blockDim.x + tid];
    __syncthreads();

    for (int s = 1; s < blockDim.x; s *= 2) {
        if (tid % (2 * s) == 0) {    // 每一輪，active thread 越來越少
            sdata[tid] += sdata[tid + s];
        }
        __syncthreads();
    }
    // 問題：
    // 第1輪：blockDim.x/2 個 thread active（50% diverge）
    // 第2輪：blockDim.x/4 個 thread active（75% idle）
    // 第3輪：blockDim.x/8 個 thread active（87.5% idle）
    // 最後幾輪在最後一個 warp 內：32 個 thread 只有 1-2 個做事
}
```

### 部分改善（技巧 1 的應用）

```cpp
// Sequential（block）reduction：改善 divergence，但最後幾輪還是有問題
for (int s = blockDim.x / 2; s > 32; s >>= 1) {
    if (tid < s) {
        sdata[tid] += sdata[tid + s];
    }
    __syncthreads();
}
// 當 s <= 32，我們在單個 warp 內操作，可以用 warp-level primitive 消掉剩餘 divergence

// 完整解法在 Ch 22：用 __shfl_down_sync 做 warp-level reduction
// 不需要 shared memory，不需要 __syncthreads，完全 divergence-free
if (tid < 32) {
    // Ch 22 會展開這部分：
    val = __shfl_down_sync(0xffffffff, val, 16);  // ... 等
}
```

**Ch 22 要解決的核心問題**：`__shfl_down_sync` 讓 warp 內的 thread 直接交換 register 值，不需要 shared memory 也不需要 branch，是消除 reduction 最後幾輪 divergence 的標準解。

---

## 邊界條件的 Divergence 最佳化

### 原始問題

```cpp
// 常見寫法：只有最後一個 block 的 tail warp 有 divergence
__global__ void kernel(float* data, int N) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < N) {        // 最後一個 block 的某個 warp 會 diverge
        data[i] = process(data[i]);
    }
}
```

### 優化 1：Padding

```cpp
// 把 N padding 到 warp size（32）或 block size 的倍數
// 額外分配的空間填 0 或 identity element，讓 kernel 可以安全處理

int N_padded = ((N + 31) / 32) * 32;   // 對齊到 32 的倍數
cudaMalloc(&d_data, N_padded * sizeof(float));
cudaMemset(d_data + N, 0, (N_padded - N) * sizeof(float));

// kernel 不需要邊界檢查（假設 padding 的 element 是安全的）
__global__ void kernel_padded(float* data, int N_padded) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    // 無 if (i < N) → 無 divergence
    // 但 padding element 的計算結果會被寫入（需確保不影響正確性）
    data[i] = process(data[i]);
}
```

適用於：process 有 identity element（如 reduction 的 0、乘法的 1），可以安全處理 padding。

### 優化 2：分段處理

```cpp
// 前段：整 block 整 warp，無邊界 divergence
int N_full = (N / blockDim.x) * blockDim.x;
kernel_no_bounds<<<N_full / blockDim.x, blockDim.x>>>(data, N_full);

// 後段：只有少數 element，用小 kernel 或 CPU 處理
if (N > N_full) {
    kernel_tail<<<1, N - N_full>>>(data + N_full, N - N_full);
    // tail kernel 可以是 divergence-free（block 只有 tail 大小，不超過一個 warp）
}
```

### 邊界 divergence 的實際影響

當 N 很大（數百萬），只有最後一個 warp 有 divergence，影響可以忽略不計：

```
N = 1,000,000, blockDim.x = 256（8 個 warp/block）：
  Block 數 = ceil(1,000,000 / 256) = 3907 blocks
  其中 3906 個 block 完全無 divergence
  最後一個 block 最多 8 個 warp，其中最多 1 個 warp diverge
  divergence 的影響 = 1 warp / (3907 * 8 warp) ≈ 0.003%
```

N 小的時候（整個 grid 只有幾個 block）才需要認真處理邊界 divergence。

---

## Divergence 與 Memory Coalescing 的交互

這兩個問題是**獨立的**，不要混淆。

```
Divergence：warp 內 thread 走不同的 control path（影響 ALU 效率）
Uncoalesced：warp 的 memory access 不連續（影響 memory bandwidth）

四種組合：
1. Divergence-free + Coalesced：理想，最快
2. Divergence-free + Uncoalesced：memory-bound 問題，跟 divergence 無關
3. Diverged + Coalesced：ALU 效率低，但每次 memory 都是 coalesced
4. Diverged + Uncoalesced：最壞情況，兩個問題疊加
```

Diverged warp 在跑某條路徑時，masked-out thread 不發出 memory request，所以 active thread 的 memory access 可能是 coalesced 的（只要 active thread 的 address 連續）。

常見誤解：「有 divergence 一定有 uncoalesced access」—— 這是錯的。

---

## 對比取捨表格

| 技巧 | 適用情況 | 代價 | 限制 |
|------|----------|------|------|
| 對齊 warp 邊界 | threadIdx 相關分支 | 低（改 index 邏輯） | 需要問題結構允許重排 |
| Branchless/Predication | 兩路都很短 | 低（多幾條算術指令） | 兩路都長時反效果 |
| 資料重排（stream compaction） | 資料相依分支、kernel 複雜 | 高（額外一個 pass） | N 要夠大才值得 |
| Warp Vote Functions | 需要 warp 層級決策 | 極低（單條指令） | 需要 Volta+ 注意 mask |
| Padding | 邊界條件 divergence | 低（多用一點記憶體） | 需要 identity element |
| 分段 kernel | 邊界條件、N 不整除 | 低（多一次 launch） | 兩個 kernel 要維護 |

---

## 踩雷

### 陷阱 1：在 Diverged 路徑裡放 `__syncthreads()`

```cpp
// 嚴重錯誤：__syncthreads() 必須 convergent call
// 所有 thread 必須都到達 __syncthreads()，否則 deadlock 或 undefined behavior
__global__ void bad_sync(float* data, int N) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < N) {
        data[i] = process(data[i]);
        __syncthreads();    // ← 若有 thread 因 i >= N 而沒走到這裡，deadlock！
    }
    // 正確寫法：__syncthreads() 放在 if 外面
}

// 正確
__global__ void good_sync(float* data, int N) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < N) {
        data[i] = process(data[i]);
    }
    __syncthreads();    // ← 所有 thread 都到達這裡
}
```

這個規則適用於所有 block-level barrier：`__syncthreads()`、`__syncthreads_count()`、`__syncthreads_and()`、`__syncthreads_or()`。

### 陷阱 2：Volta+ 的 Independent Thread Scheduling 改變了 reconverge 行為

Pascal 及更早：warp diverge 之後，會在最近的 post-dominator join point 自動 reconverge。這讓以下 pattern 能工作：

```cpp
// Pascal 上能工作（不安全！）
if (tid < 16) {
    // path A：tid 0-15 在這裡
    shared_data[tid] = compute_A(tid);
}
// Pascal 上：tid 16-31 在 path B 跑完後，自動 reconverge 到這裡
// 所以 shared_data[0-15] 有值（path A 寫的）
use(shared_data[tid % 16]);    // Pascal 可能正確，但不保證
```

Volta+ 的 Independent Thread Scheduling 讓各 thread 有自己的 PC 和 execution state，diverged thread 的 reconverge 時機不再有保證。上面的 code 在 Volta+ 上可能出錯。

**解法**：在需要 convergence 的地方，明確用 `__syncwarp()` 或 `__syncthreads()`。

### 陷阱 3：`__ballot_sync` 的 mask 傳錯

```cpp
// 在 diverged context 裡，不能假設所有 32 個 thread 都在這裡
if (some_condition) {
    // 這裡只有部分 thread
    unsigned int ballot = __ballot_sync(0xffffffff, pred);  // ← 錯誤
    // 應該：
    unsigned int mask = __activemask();
    unsigned int ballot = __ballot_sync(mask, pred);         // ← 正確
}
```

Volta+ 上用錯誤的 mask 是 undefined behavior，在 compute-sanitizer 下會報錯。

### 陷阱 4：Predication ≠ 消除 Divergence

我們在技巧 2 已經說過，但值得再強調一次：

```cpp
// 你把 if-else 改成 ?:，NVCC 可能產生 predicated instruction
float result = (x > 0) ? heavy_compute_A(x) : heavy_compute_B(x);
// 但兩個 heavy_compute 都會被執行（只是結果被 mask 掉）
// 若 heavy_compute_A 和 heavy_compute_B 都很貴，這比 branch 更慢
```

Predication 只有在兩路的指令數都很少（<10 條）的情況下才有益。長路徑的 divergence 要用對齊或資料重排，不是 predication。

### 陷阱 5：過度 Branchless 導致多餘計算

```cpp
// 原始 code：有 short-circuit，絕大多數情況走快速路徑
if (unlikely_condition(data[i])) {
    out[i] = very_expensive_fallback(data[i]);
} else {
    out[i] = fast_path(data[i]);
}

// 誤以為 branchless 更好：
float fast = fast_path(data[i]);
float slow = very_expensive_fallback(data[i]);   // 99% 的情況都在浪費算
int cond = unlikely_condition(data[i]);
out[i] = cond ? slow : fast;
// → 平均代價從 fast_path_cost 變成 fast_path_cost + slow_path_cost
// 完全反效果
```

只有在 branch 頻繁造成 divergence（兩路都有顯著比例的 thread 走）時，branchless 才有意義。

---

## 進階：Nsight 診斷 Warp Divergence

### Nsight Compute 的關鍵指標

1. **`sm__sass_average_branch_targets_threads_uniform.pct`**（Branch Efficiency）
   - 100%：所有 branch 都是 warp-uniform（無 divergence）
   - 50%：一半的 branch 有 divergence

2. **`smsp__thread_inst_executed_vs_pred_on_pct`**（Warp Execution Efficiency）
   - 100%：所有執行的 warp 都是滿員（32 active thread）
   - 低值表示 divergence 嚴重

3. **`l1tex__t_sectors_pipe_lsu_mem_local_op_ld.sum`**：若 divergence 造成 register spilling，local memory 使用量會上升

### Nsight Systems + Compute 的工作流程

```bash
# 1. 先用 Nsight Systems 找到 kernel 的熱點
nsys profile --output profile.nsys-rep ./your_app

# 2. 對熱點 kernel 用 Nsight Compute 深挖
ncu --set full --kernel-name your_kernel_name ./your_app
# 看 "Warp State Statistics" 和 "Branch Statistics" 頁面

# Colab 環境：
# !ncu --metrics smsp__thread_inst_executed_vs_pred_on_pct.avg \
#              sm__sass_average_branch_targets_threads_uniform.pct.avg \
#       python your_script.py
```

### Independent Thread Scheduling（Volta+）的深層影響

Volta 的 ITS 讓 GPU 可以在 warp 內的不同 thread 之間做 fine-grained interleaving，提升 latency hiding 能力。但這也讓一些舊的 warp-level idiom 變得 unsafe：

```cpp
// 舊式 warp-level reduction（Pascal 及以前可用）
__device__ float warp_reduce_old(float val) {
    for (int offset = 16; offset > 0; offset >>= 1) {
        val += __shfl_down_sync(0xffffffff, val, offset);
        // Pascal：warp 整體在這裡，0xffffffff 是正確的
    }
    return val;
}

// Volta+ 如果在 diverged context 呼叫上面的函數，0xffffffff 就可能錯
// 正確寫法：傳入 mask，讓呼叫者決定
__device__ float warp_reduce_safe(unsigned int mask, float val) {
    for (int offset = 16; offset > 0; offset >>= 1) {
        val += __shfl_down_sync(mask, val, offset);
    }
    return val;
}
```

CUDA 12.x 的所有 warp intrinsic（`__shfl_*_sync`、`__ballot_sync`、`__any_sync`、`__all_sync`）都要求傳入正確的 mask，這是 Volta ITS 帶來的必要設計。

---

## 動手練習

以下練習在 Colab T4（sm_75）上執行。

### 練習 1：診斷現有 kernel 的 divergence

```cpp
// 給定這個 kernel，找出 divergence 在哪裡，並用 Nsight 確認
__global__ void mystery_kernel(int* data, int* out, int N) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= N) return;

    int x = data[i];
    if (x % 3 == 0) {
        out[i] = x * 2;
    } else if (x % 3 == 1) {
        out[i] = x + 100;
    } else {
        out[i] = x - 50;
    }
}
// 問：有幾處 divergence？如何消除？
```

### 練習 2：實作 Warp-Aligned 分支

```cpp
// 把這個 kernel 改成無 divergence 版本
// 要求：不改變 output（結果要正確）
__global__ void interleave_kernel(float* a, float* b, float* out, int N) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < N) {
        if (i % 2 == 0) {
            out[i] = a[i] * b[i];
        } else {
            out[i] = a[i] + b[i];
        }
    }
}
// 提示：考慮問題能否用兩個 kernel 分別處理 even/odd element
```

### 練習 3：用 `__ballot_sync` 實作 warp 層級的統計

```cpp
// 實作：對每個 warp 統計「data[i] > 0」的 thread 數量
// 結果存入 warp_counts[warp_id]
__global__ void count_positive(float* data, int* warp_counts, int N) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    int warp_id = i / 32;
    float val = (i < N) ? data[i] : 0.0f;

    // TODO: 用 __ballot_sync 實作
    // 注意 mask 的正確用法
}
```

---

## 本章重點

1. Warp divergence 的根本原因是 SIMT：32 個 thread 共用 PC，branch 讓硬體序列化兩路，代價是 path_A + path_B 的時間。

2. Divergence 三大來源：threadIdx 奇偶分叉、資料相依分支、邊界條件。各有不同的消除策略。

3. 消除技巧四種：
   - **對齊 warp 邊界**：讓分支條件是 warp 整體的屬性
   - **Branchless**：算術替代 branch，只對短路徑有效
   - **資料重排**：stream compaction，有額外 pass 代價
   - **Warp Vote Functions**：`__ballot_sync`/`__any_sync`/`__all_sync`，做 warp 層級決策

4. Predication ≠ 消除 divergence：兩路都算，只是用 mask 決定寫回；只對短路徑有益。

5. Volta+ ITS 改變了 warp reconverge 行為，所有 warp intrinsic 必須傳正確的 mask。

6. `__syncthreads()` 必須在所有 thread 都能到達的地方呼叫，不能放在 diverged branch 內。

7. Reduction 的 divergence 問題由 `__shfl_down_sync` 在 Ch 22 完整解決。

---

## 自我檢核

1. 給定 `if (threadIdx.x % 4 == 0)` 這個條件，一個 32-thread 的 warp 裡有幾個不同的執行路徑？如何改寫讓 divergence 消失？

2. `__ballot_sync(0xffffffff, x > 0)` 和 `__ballot_sync(__activemask(), x > 0)` 有什麼差異？什麼情況下兩者等價？什麼情況下必須用後者？

3. 把 `if (a) { X; } else { Y; }` 改成 branchless `val = a ? X_result : Y_result`，在什麼條件下這個改法會讓 kernel 變慢？

4. 為什麼 `__syncthreads()` 不能放在 diverged branch 的內部？這條規則在 Volta+ ITS 下有沒有例外？

5. Reduction kernel 從 interleaved 改成 sequential（block）分割後，哪幾輪的 divergence 消失了？哪幾輪還在？Ch 22 用什麼工具解決剩下的 divergence？

---

## 延伸閱讀

1. **CUDA C++ Programming Guide, "Control Flow" 章**
   — 官方對 warp divergence、predication、branch efficiency 的完整說明。
   https://docs.nvidia.com/cuda/cuda-c-programming-guide/index.html#control-flow-instructions

2. **CUDA C++ Best Practices Guide, "Control Flow" 章**
   — 實務消除 divergence 的建議，包括 branch efficiency 的量測方式。
   https://docs.nvidia.com/cuda/cuda-c-best-practices-guide/index.html#control-flow

3. **Mark Harris, "Optimizing Parallel Reduction in CUDA"** — NVIDIA Technical Report
   — Reduction divergence 問題的經典分析，從 naive interleaved 到 warp-synchronous 的完整演進。是理解 Ch 22 之前必讀的背景材料。
   https://developer.download.nvidia.com/assets/cuda/files/reduction.pdf

4. **"Inside Volta" — NVIDIA Developer Blog**
   — Independent Thread Scheduling 的設計動機與影響，解釋為何 Volta 打破了舊的 warp reconverge 假設。
   https://developer.nvidia.com/blog/inside-volta/

5. **CUB Device-Level Primitives — `cub::DevicePartition`**
   — 高效的 stream compaction 實作，用於把走同一條路的 element 聚集在一起，是資料重排技巧的生產級工具。
   https://nvidia.github.io/cccl/cub/api/structcub_1_1DevicePartition.html

---

→ [Ch 22 Atomics 與 Reduction 優化](./22-atomics-reduction.md)
