# Ch 16 — 同步：__syncthreads / cooperative groups / grid 同步

> **目標**：理解 GPU 上「同步」的意義、層次與代價；搞清楚 `__syncthreads` 的正確用法和致命陷阱；理解 atomic 操作的保證與代價；學會 warp-level sync（Volta+ 必備）；了解 cooperative groups 的 grid-wide sync 是什麼、何時需要。

> **環境**：CUDA 12.x, Colab T4 (sm_75)。程式輸出均為「Colab 預期行為，未在本機實測」，附 Colab 執行步驟。

---

CPU 的同步問題讓你已經很熟悉：mutex、condition variable、memory barrier。GPU 上的同步看起來像是同一個問題，但其實底層機制完全不同——差異來自 GPU 的執行模型（SIMT、lockstep warp、block 之間無硬體同步）。

搞清楚這些差異，你才能寫出正確的 kernel，也才能理解為什麼某些「看起來合理」的同步方式在 GPU 上根本不能用。

---

## 一、為什麼 GPU 需要同步？—— Race Condition 解剖

先看 CPU 上的 race condition 長什麼樣：

```
Thread A                Thread B
─────────────────────────────────────
read x (= 0)
                        read x (= 0)
x = x + 1
                        x = x + 1
write x (= 1)
                        write x (= 1)   ← 結果應該是 2，但是 1
```

GPU 的 race condition 形狀一樣，但速度快 1000 倍，而且有更多「隱藏」的來源。

### GPU 上的典型 race：shared memory

```c
__global__ void histogram_naive(int *data, int *hist, int n) {
    int tid = blockIdx.x * blockDim.x + threadIdx.x;
    if (tid < n) {
        hist[data[tid]]++;  // 多個 thread 同時讀-改-寫 hist[x]
    }
    // ↑ 這是全局記憶體的 race，稍後討論
}

// 更容易出問題的：shared memory 沒有 __syncthreads
__global__ void shared_race(int *out) {
    __shared__ int smem[256];
    int tid = threadIdx.x;

    smem[tid] = tid;          // 每個 thread 寫自己那格
    // 沒有 __syncthreads

    // 想讀右邊鄰居的值
    if (tid < 255) {
        out[tid] = smem[tid + 1];  // ← smem[tid+1] 被 thread tid+1 寫了嗎？不知道！
    }
}
```

問題出在哪？**同一個 block 裡的 thread 不是真正同步執行的**。雖然同一個 warp（32 個 thread）是 lockstep，但不同 warp 的排程是非確定的。Thread 0（warp 0）可能比 thread 33（warp 1）早 50 個 cycle 執行完 `smem[tid] = tid`，也可能晚——沒有保證。

```
Warp 0  (threads 0-31):  smem[0..31] = 0..31   ← 排在前面
                                                   ↑ 有沒有 warp 1 的結果？不知道
Warp 1  (threads 32-63): smem[32..63] = 32..63  ← 排在後面
                          ↑ warp 0 需要讀 smem[32]，但 warp 1 可能還沒寫
```

這就是為什麼需要 `__syncthreads()`。

---

## 二、__syncthreads()：block 內的 barrier

### 語義

```c
__syncthreads();
```

**保證**：block 內的所有 thread 都到達這個點之後，才有任何 thread 繼續執行後面的指令。同時也是一個 **memory fence**——確保在 barrier 之前的所有 shared memory 寫入，對 barrier 之後的所有 thread 可見。

這兩個保證加在一起，讓 shared memory 的 producer-consumer pattern 變得安全：

```c
__global__ void shared_correct(int *out) {
    __shared__ int smem[256];
    int tid = threadIdx.x;

    smem[tid] = tid;           // Phase 1：每個 thread 寫自己那格

    __syncthreads();           // ← Barrier：所有 thread 都寫完才繼續

    if (tid < 255) {
        out[tid] = smem[tid + 1];  // Phase 2：現在讀鄰居是安全的
    }
}
```

```
所有 thread 的 Phase 1:   smem[0]=0, smem[1]=1, ..., smem[255]=255
                                                ↑
                              __syncthreads() 等到所有人都做完
                                                ↓
所有 thread 的 Phase 2:   out[0] = smem[1] = 1
                           out[1] = smem[2] = 2
                           ...                  （現在是安全的）
```

### 成本

`__syncthreads` 不是免費的。它讓整個 block 的所有 warp 等到最後一個 warp 到達才繼續。如果你的 kernel 裡有 warp divergence（部分 warp 在 if 分支），最慢的 warp 決定 barrier 的延遲。

---

## 三、__syncthreads 的致命陷阱：Divergent Branch

**這是初學者最容易犯的錯誤**，也是 Ch 15 的 `compute-sanitizer --tool synccheck` 能偵測到的。

### 為什麼不能放在 divergent if 裡

```c
// ✗ 錯誤：__syncthreads 在 if 裡，只有部分 thread 會到達
__global__ void bad_kernel(float *out, int n) {
    __shared__ float smem[256];
    int tid = threadIdx.x;

    smem[tid] = (float)tid;

    if (tid < 128) {
        __syncthreads();     // ← 只有前 128 個 thread 到這裡
        out[tid] = smem[255 - tid];
    }
    // thread 128-255 從未到達 __syncthreads
    // → 行為未定義，可能 deadlock 或 silent corruption
}
```

CUDA 規格說：**如果 `__syncthreads` 不是 block 內所有 thread 都會執行到的，行為是未定義的（undefined behavior）**。

### 正確的寫法

```c
// ✓ 正確：先 sync，再做有條件的操作
__global__ void good_kernel(float *out, int n) {
    __shared__ float smem[256];
    int tid = threadIdx.x;

    smem[tid] = (float)tid;

    __syncthreads();         // ← 所有 thread 都到達

    if (tid < 128) {         // 條件在 sync 之後
        out[tid] = smem[255 - tid];
    }
}
```

### 另一個常見陷阱：early return + __syncthreads

```c
// ✗ 危險：early return 讓部分 thread 沒走到 sync
__global__ void bad_early_return(float *out, int n) {
    int tid = threadIdx.x;
    __shared__ float smem[256];

    if (tid >= n) return;   // ← thread 提前 return，就沒到 __syncthreads

    smem[tid] = compute(tid);
    __syncthreads();         // ← 如果上面有 thread return，這裡少了人
    out[tid] = smem[255-tid];
}

// ✓ 改成條件賦值，不 early return
__global__ void safe_kernel(float *out, int n) {
    int tid = threadIdx.x;
    __shared__ float smem[256];

    smem[tid] = (tid < n) ? compute(tid) : 0.0f;  // 所有 thread 都寫
    __syncthreads();
    if (tid < n) {
        out[tid] = smem[255-tid];
    }
}
```

---

## 四、Global Memory 的 Race：Atomic 操作

`__syncthreads` 只管 block 內。不同 block 之間，對 global memory 的競爭存取需要用 **atomic 操作**。

### 什麼是 atomic

一個 atomic 操作在硬體層級保證「讀-改-寫」這三個步驟是不可分割的。沒有任何其他 thread 能在這三步之間插入。

```c
// 非 atomic（race condition）
hist[data[tid]]++;
// 等同於：int tmp = hist[x]; tmp++; hist[x] = tmp;
// 多個 thread 可能都讀到同一個舊值，然後各自加 1，最後只有一次 +1 的效果

// atomic（安全）
atomicAdd(&hist[data[tid]], 1);
// 硬體保證：讀-加-寫是原子的，任何 thread 看到的都是前一個 atomic 完成後的結果
```

### CUDA 常用 atomic 操作

| 函數 | 操作 | 支援型別 |
|------|------|----------|
| `atomicAdd(addr, val)` | `*addr += val`，回傳舊值 | int, uint, float, double（sm_60+）, long long |
| `atomicSub(addr, val)` | `*addr -= val`，回傳舊值 | int, uint |
| `atomicMax(addr, val)` | `*addr = max(*addr, val)` | int, uint, long long |
| `atomicMin(addr, val)` | `*addr = min(*addr, val)` | int, uint, long long |
| `atomicCAS(addr, compare, val)` | `if (*addr==compare) *addr=val`，回傳舊值 | int, uint, long long |
| `atomicExch(addr, val)` | `swap(*addr, val)`，回傳舊值 | int, uint, float, long long |
| `atomicAnd/Or/Xor` | 位元操作 | int, uint, long long |

### Atomic 的成本

Atomic 不便宜。多個 thread 對**同一個地址**做 atomic 會產生串行化（serialization）——硬體讓它們一個一個來：

```
thread 0: atomicAdd(&x, 1)  → 硬體鎖定 x
thread 1: atomicAdd(&x, 1)  → 等 thread 0 完成
thread 2: atomicAdd(&x, 1)  → 等 thread 1 完成
...
```

最壞情況下（全部 thread 打同一個地址），你把 GPU 的平行度降為 1。

**緩解策略**：
1. **先在 shared memory 做局部 atomic，再把 block 的結果 atomic 到 global**（Ch 22 reduction 優化的核心技術）
2. 讓不同 thread 打不同地址（資料設計）
3. `atomicAdd` 的 warp-level 版本（`__reduce_add_sync`，sm_80+）

---

## 五、__syncwarp()：Volta 之後的必要知識

在 Volta（Turing 及之後，包含 T4）架構之前，同一個 warp 的 32 個 thread 總是 lockstep——你可以假設同 warp 的 thread 始終在同一個指令位置。Volta 之後，**independent thread scheduling** 打破了這個假設。

### 什麼是 Independent Thread Scheduling

Volta 之前：
```
warp 分岔（if/else）時：
  active mask = 0xFFFF0000  → 前 16 個 thread 跑 if 分支
  所有 thread 等 if 完成
  active mask = 0x0000FFFF  → 後 16 個 thread 跑 else 分支
  回到 converged 狀態
```

Volta 之後：
```
每個 thread 有自己的 PC（program counter）
同一個 warp 的 thread 可以在不同指令位置
「implicit warp synchronization」不再保證
```

這意味著：以前你可以依賴「同 warp 的 thread 做完讀就會做寫，不需要 sync」，Volta 之後不行了。

### __syncwarp() 的用法

```c
// Volta+ 正確的 warp shuffle 用法
__global__ void warp_reduce_correct(float *data, float *result) {
    int tid = threadIdx.x;
    float val = data[blockIdx.x * blockDim.x + tid];

    // Warp-level tree reduction
    for (int offset = 16; offset > 0; offset /= 2) {
        // Kepler~Pascal：可以不加 __syncwarp，因為隱含 lockstep
        // Volta+：必須加，因為 independent thread scheduling
        __syncwarp();
        val += __shfl_down_sync(0xFFFFFFFF, val, offset);
        // __shfl_down_sync 本身帶 mask，但 __syncwarp 確保讀到的是最新寫入
    }

    if (tid % 32 == 0) {
        result[blockIdx.x] = val;
    }
}
```

`__syncwarp(mask)` 的語義：等 mask 裡指定的所有 thread 都到達這裡，然後繼續。Mask 通常是 `0xFFFFFFFF`（所有 32 個 thread）。

### __shfl_down_sync 和 mask

Volta 之後，warp shuffle 指令（`__shfl_sync`, `__shfl_up_sync`, `__shfl_down_sync`, `__shfl_xor_sync`）都需要一個 mask 參數：

```c
// 新版（Volta+）
float val_from_src = __shfl_down_sync(
    0xFFFFFFFF,    // mask：哪些 thread 參與這次 shuffle
    val,           // 要傳遞的值
    8              // offset：從 tid+8 的 thread 取值
);

// 舊版（Pascal 及以前，仍可用但 Volta 上有隱患）
float val = __shfl_down(val, 8);  // 沒有 mask，Volta 上可能拿到舊值
```

為了可移植性和正確性，Volta+ 環境下**一律用 `_sync` 結尾的版本**。

---

## 六、Cooperative Groups：把同步參數化

Cooperative Groups 是 CUDA 9 引入的框架，把「哪些 thread 要同步」這件事從隱含（`__syncthreads` 預設同步整個 block）變成顯式參數。

### 動機

`__syncthreads` 只能同步整個 block。如果你只想同步 block 的一半 thread（例如做 warp-level reduction），用它就太重了。Cooperative Groups 讓你按需劃定同步組。

### 常用型別

```c
#include <cooperative_groups.h>
namespace cg = cooperative_groups;

__global__ void cg_demo(float *data, float *result) {
    // 1. Block-level group（等同於 __syncthreads）
    cg::thread_block block = cg::this_thread_block();
    block.sync();     // 等同於 __syncthreads()

    // 2. Warp-level group
    cg::thread_block_tile<32> warp = cg::tiled_partition<32>(block);
    warp.sync();      // 等同於 __syncwarp(0xFFFFFFFF)

    // warp shuffle 可以直接用 warp 物件
    float val = data[threadIdx.x];
    val += warp.shfl_down(val, 16);
    val += warp.shfl_down(val, 8);
    val += warp.shfl_down(val, 4);
    val += warp.shfl_down(val, 2);
    val += warp.shfl_down(val, 1);

    if (warp.thread_rank() == 0) {
        atomicAdd(result, val);
    }
}
```

### Grid-Wide Sync（cooperative launch）

`__syncthreads` 只能同步一個 block。如果你要讓整個 grid 的所有 thread 到一個 barrier 再繼續，需要 cooperative launch：

```c
#include <cooperative_groups.h>
namespace cg = cooperative_groups;

// kernel 本身和一般 kernel 一樣寫
__global__ void grid_sync_kernel(float *data, int n) {
    cg::grid_group grid = cg::this_grid();

    // Phase 1：所有 thread 各做自己的部分
    int tid = blockIdx.x * blockDim.x + threadIdx.x;
    if (tid < n) data[tid] = compute_phase1(data[tid]);

    grid.sync();     // ← 等整個 grid 的所有 block 都到這裡

    // Phase 2：現在可以安全讀其他 block 的結果
    if (tid < n) data[tid] = compute_phase2(data, tid, n);
}

// 啟動方式不同！不能用 <<<...>>>
int main() {
    float *d_data;
    cudaMalloc(&d_data, n * sizeof(float));

    int block_size = 256;
    int grid_size = (n + block_size - 1) / block_size;

    // cooperative launch：必須保證 SM 數量夠放所有 block
    void *args[] = { &d_data, &n };
    cudaLaunchCooperativeKernel(
        (void*)grid_sync_kernel,
        grid_size, block_size,
        args
    );
    cudaDeviceSynchronize();
    // ...
}
```

**注意事項**：
1. `grid.sync()` 需要 SM 能同時容納所有 block（否則先啟動的 block 在 grid.sync() 等，後面的 block 進不來 → deadlock）
2. T4 有 40 個 SM，每個 SM 最多 16 個 block → 最多 640 個 block 可以 cooperative launch
3. 可以用 `cudaOccupancyMaxActiveBlocksPerMultiprocessor` 查每個 SM 能放幾個 block

---

## 七、Memory Fence：__threadfence()

`__syncthreads` 是 barrier（等人）+ fence（確保記憶體可見）的組合。但有時候你只需要 fence，不需要 barrier。

### 記憶體可見性問題

```
Thread A 在 block 0:
  global_data[0] = 42;
  flag[0] = 1;          // 「我做完了」

Thread B 在 block 1（輪詢 flag）:
  while (flag[0] == 0) ;  // 等 flag
  int val = global_data[0];  // ← 可能讀到 0！不是 42！
```

問題：GPU 的 L1/L2 cache 和 DRAM 的一致性（coherence）不像 CPU 那麼強。Thread A 寫 `global_data[0] = 42` 之後，如果沒有 fence，這個寫入可能還在 Thread A 的 L1 cache 裡，Thread B 看不到。

### __threadfence() 的語義

```c
// __threadfence() 確保：
// 在 __threadfence() 之前的所有寫入，對其他 thread（透過 L2）可見
// 然後才執行 __threadfence() 之後的指令

global_data[0] = 42;
__threadfence();          // flush 到 L2，讓其他 block 看得到
flag[0] = 1;
```

### fence 的層次

| 函數 | 範圍 | 用途 |
|------|------|------|
| `__threadfence_block()` | 同一 block 的 shared memory | 和 `__syncthreads` 配套，很少單獨用 |
| `__threadfence()` | 整個 GPU（透過 L2） | block 間通訊 |
| `__threadfence_system()` | 包含 host + peer GPU | unified memory 跨裝置通訊 |

### 什麼時候需要 __threadfence

主要場景是你在 kernel 內實作某種「lock-free producer-consumer」或「atomic flag 通知」。但**實務上應該優先考慮 cooperative groups grid sync**——它更高層次、更不容易出錯。`__threadfence` 屬於低層次的工具，用錯了很難 debug。

---

## 八、同步方式的對比表

```
同步範圍:
  warp (32 threads)     → __syncwarp() 或 cg::tiled_partition<32>.sync()
  block (所有 threads)  → __syncthreads() 或 cg::thread_block.sync()
  grid (所有 blocks)    → cg::grid.sync()（需 cooperative launch）
  任意跨 kernel         → cudaDeviceSynchronize()（CPU 端）
```

| 機制 | 範圍 | 成本 | 適用場景 |
|------|------|------|----------|
| `__syncthreads()` | Block | 低（block 內等待） | Tiled 計算、shared memory 讀寫保護 |
| `__syncwarp()` | Warp | 最低 | Warp shuffle 之前（Volta+）|
| `atomicAdd` 等 | Global/Shared | 中（序列化 conflict） | Histogram、全局 counter |
| `cg::grid.sync()` | Grid | 高（所有 block 等） | 需要跨 block 的全局 barrier |
| `cudaDeviceSynchronize()` | 整個 GPU | 最高（CPU 等 GPU） | 跨 kernel 的正確性保證、計時 |

---

## 九、實作範例：正確的 shared memory prefix sum（scan）

把上面的概念合在一起，看一個「需要兩次 sync」的例子——block 級 prefix sum：

```c
// Blelloch work-efficient scan（block 內）
// 假設 n == blockDim.x 且 n 是 2 的冪次
__global__ void block_scan(int *data, int *out, int n) {
    extern __shared__ int smem[];  // 動態 shared，大小 = n * sizeof(int)
    int tid = threadIdx.x;

    // 載入
    smem[tid] = (tid < n) ? data[blockIdx.x * n + tid] : 0;
    __syncthreads();

    // Upsweep（reduce phase）：建立 partial sum 樹
    for (int stride = 1; stride < n; stride *= 2) {
        int idx = (tid + 1) * stride * 2 - 1;
        if (idx < n) {
            smem[idx] += smem[idx - stride];
        }
        __syncthreads();  // ← 每層都要 sync，不然下一層讀到的是舊值
    }

    // 清除最後一個元素（exclusive scan 的起點）
    if (tid == 0) smem[n - 1] = 0;
    __syncthreads();

    // Downsweep：填入前綴結果
    for (int stride = n / 2; stride >= 1; stride /= 2) {
        int idx = (tid + 1) * stride * 2 - 1;
        if (idx < n) {
            int tmp = smem[idx - stride];
            smem[idx - stride] = smem[idx];
            smem[idx] = smem[idx] + tmp;
        }
        __syncthreads();  // ← 同理
    }

    // 寫回
    if (tid < n) out[blockIdx.x * n + tid] = smem[tid];
}
```

**每一個 `__syncthreads()`都有必要**：每一層的計算依賴上一層的結果，沒有 sync 就 race。少了任何一個，結果就爛了。

---

## 十、踩雷集錦

### 雷 1：if-return 裡的 __syncthreads

最常見的 bug。「我覺得超出範圍的 thread 應該提早 return」——但這讓它們繞過 `__syncthreads`，造成 deadlock 或 silent corruption：

```c
// ✗
__global__ void bad(int *data, int n) {
    int tid = threadIdx.x;
    if (tid >= n) return;      // 只要有 thread 在這裡 return...
    __shared__ int s[256];
    s[tid] = data[tid];
    __syncthreads();           // ...這裡就少人，行為未定義
    data[tid] = s[255 - tid];
}
```

### 雷 2：迴圈內的 __syncthreads 只被部分 thread 執行

```c
// ✗ 看起來像條件外，實際上迴圈次數依 tid 不同
__global__ void bad_loop(int *data) {
    int limit = threadIdx.x % 4;  // 各 thread 的 limit 不同！
    for (int i = 0; i < limit; i++) {
        __syncthreads();  // ← 不同 thread 進來的次數不一樣 → 行為未定義
    }
}
```

### 雷 3：以為 atomicAdd 是「免費」的

```c
// 大家都打同一個地址：效能崩潰
__global__ void naive_sum(int *data, int *total, int n) {
    int tid = blockIdx.x * blockDim.x + threadIdx.x;
    if (tid < n) atomicAdd(total, data[tid]);
}
// 1024 個 thread 同時打 total → 1024 個 atomic 序列化 → 比 CPU 還慢
```

### 雷 4：在 Volta 上用舊 warp shuffle 不加 _sync

```c
// 在 sm_70+（Volta+）上可能讀到舊值
float v = __shfl_down(val, 8);   // 沒有 mask，Volta 上有 independent scheduling

// 改成
float v = __shfl_down_sync(0xFFFFFFFF, val, 8);
```

### 雷 5：把 cooperative grid sync 當普通 kernel 啟動

```c
// ✗ 用 <<< >>> 啟動有 grid.sync() 的 kernel：undefined behavior
grid_sync_kernel<<<grid_size, block_size>>>(data, n);
// 應該用
cudaLaunchCooperativeKernel((void*)grid_sync_kernel, grid_size, block_size, args);
```

---

## 十一、動手練習

### 練習 16.1：觀察沒有 __syncthreads 的後果

```c
// Colab 預期行為，未在本機實測
%%writefile sync_demo.cu
#include <cuda_runtime.h>
#include <stdio.h>

// 版本 A：沒有 __syncthreads（故意的）
__global__ void without_sync(float *out, int n) {
    __shared__ float smem[256];
    int tid = threadIdx.x;
    smem[tid] = (float)(255 - tid);  // 寫入「反轉」的值
    // 沒有 sync
    if (tid < n) out[tid] = smem[255 - tid];  // 想讀反轉後的位置
}

// 版本 B：有 __syncthreads（正確）
__global__ void with_sync(float *out, int n) {
    __shared__ float smem[256];
    int tid = threadIdx.x;
    smem[tid] = (float)(255 - tid);
    __syncthreads();
    if (tid < n) out[tid] = smem[255 - tid];  // 現在保證讀到正確值
}

int main() {
    const int N = 256;
    float h_a[N], h_b[N];
    float *d_a, *d_b;
    cudaMalloc(&d_a, N * sizeof(float));
    cudaMalloc(&d_b, N * sizeof(float));

    without_sync<<<1, N>>>(d_a, N);
    cudaMemcpy(h_a, d_a, N * sizeof(float), cudaMemcpyDeviceToHost);

    with_sync<<<1, N>>>(d_b, N);
    cudaMemcpy(h_b, d_b, N * sizeof(float), cudaMemcpyDeviceToHost);

    printf("Without sync: out[0]=%f out[128]=%f out[255]=%f\n",
           h_a[0], h_a[128], h_a[255]);
    printf("With sync:    out[0]=%f out[128]=%f out[255]=%f\n",
           h_b[0], h_b[128], h_b[255]);
    // 期望：with_sync 的 out[0] = smem[255] = 255-255 = 0
    //        with_sync 的 out[255] = smem[0] = 255-0 = 255

    cudaFree(d_a); cudaFree(d_b);
    return 0;
}
```

```bash
%%writefile run.sh
nvcc -arch=sm_75 sync_demo.cu -o sync_demo
./sync_demo
compute-sanitizer --tool racecheck ./sync_demo
```

```python
!bash run.sh
```

---

## 本章重點

1. **GPU race condition** 的來源：不同 warp 對 shared/global memory 的讀寫沒有保證的相對順序
2. **`__syncthreads()`** = block-level barrier + memory fence，必須所有 thread 都到達（不能在 divergent if 裡）
3. **Atomic 操作** 保證讀-改-寫不可分割，代價是同一地址的競爭者被序列化
4. **Volta+ 的 independent thread scheduling** 讓「同 warp 自動 lockstep」的假設失效，warp shuffle 前需要 `__syncwarp()`
5. **Cooperative groups** 提供更精細的同步抽象；`grid.sync()` 需要 cooperative launch
6. **`__threadfence()`** 只是 memory fence，不等人；需要等人時用 barrier

---

## 自我檢核

1. 解釋為什麼同一個 warp 的 thread 不需要 `__syncthreads` 就能安全共享 shared memory（Pascal 及以前）——以及為什麼 Volta+ 這個假設不再成立？
2. 下面的 code 有什麼問題？如何修正？
   ```c
   if (threadIdx.x < 64) { __shared__ int s; s = 0; __syncthreads(); }
   ```
3. `atomicAdd` 對 shared memory 和 global memory 都有效嗎？代價有何不同？
4. `__threadfence()` 和 `__syncthreads()` 的差別是什麼？各適合什麼場景？
5. 為什麼 `cg::grid.sync()` 需要 cooperative launch，不能用普通的 `<<<>>>` 啟動？

---

## 延伸閱讀

1. **[CUDA C++ Programming Guide — Synchronization Functions](https://docs.nvidia.com/cuda/cuda-c-programming-guide/index.html#synchronization-functions)**  
   讀哪裡：`__syncthreads`、`__syncwarp`、Memory Fence Functions 三小節  
   學什麼：函數的精確語義、對不同 memory space 的可見性保證  
   前提：本章

2. **[CUDA C++ Programming Guide — Cooperative Groups](https://docs.nvidia.com/cuda/cuda-c-programming-guide/index.html#cooperative-groups)**  
   讀哪裡：Introduction、Thread Groups、Grid Synchronization  
   學什麼：完整的 cooperative groups API、coalesced groups（動態組合 thread）  
   前提：本章

3. **[NVIDIA Volta Architecture Whitepaper](https://images.nvidia.com/content/volta-architecture/pdf/volta-architecture-whitepaper.pdf)**  
   讀哪裡：Independent Thread Scheduling 章節  
   學什麼：為什麼 Volta 打破了 implicit warp synchronization，新的 sub-warp mask 機制  
   前提：Ch 10 warp SIMT

4. **[CUDA C++ Programming Guide — Atomic Functions](https://docs.nvidia.com/cuda/cuda-c-programming-guide/index.html#atomic-functions)**  
   讀哪裡：Atomic Functions 章節，注意各函數對 compute capability 的要求  
   學什麼：雙精度 atomic（sm_60+）、long long atomic 的特殊行為  
   前提：本章

5. **[Mark Harris, "Optimizing Parallel Reduction in CUDA"](https://developer.download.nvidia.com/assets/cuda/files/reduction.pdf)**  
   讀哪裡：Reduction #6 和 #7（warp unrolling、template unrolling）  
   學什麼：如何把 atomic + shared memory sync 結合做出高效 reduction，是 Ch 22 的藍本  
   前提：本章 + Ch 17 shared memory

---

> Block 內的同步很直觀，但 Volta 之後的 warp-level 細節、以及 grid 級的 cooperative launch，是讓 senior CUDA 工程師和初學者拉開差距的地方。

→ [Ch 17 shared memory 與 tiling：手動 cache](./17-shared-memory-tiling.md)
