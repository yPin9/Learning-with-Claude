# Ch 22 — Atomics 與 Reduction 優化：warp shuffle 收尾

> **目標**：搞清楚 atomic 操作的語意與真實成本；理解 parallel reduction 的樹狀結構；掌握 `__shfl_down_sync` warp-level primitive；組合 shared memory reduction + warp shuffle 成一個高效核心；知道為什麼「每 block 一個 atomicAdd」是最常見的正確選擇。

> **環境**：CUDA 12.x, Colab T4 (sm_75)。程式輸出均為「Colab 預期行為，未在本機實測」，附 Colab 執行步驟。

---

如果你已經讀過 [Ch 16 同步](./16-synchronization.md) 和 [Ch 17 shared memory 與 tiling](./17-shared-memory-tiling.md)，你知道 `__syncthreads` 能讓 block 內的 thread 在繼續前先對齊。但有一類問題 `__syncthreads` 幫不了你：**多個 thread 要同時寫同一個記憶體位置**。

典型場景：把一個大陣列的所有元素加起來。最後那個總和只有一個位置，但每個 thread 算完自己那段後都想寫進去。這就是 **reduction**（歸約），也是 CUDA 優化最經典的示範題——[練習 D](./practice-d-reduction.md) 會帶你把它從最爛的版本一路改到逼近硬體極限。

這一章先把工具講清楚：atomic 操作、parallel reduction 的樹狀結構、warp shuffle。

---

## 一、Atomic 操作：從語意開始

### 什麼是「原子」

「原子」（atomic）的意思是這個操作**不可分割**——從其他 thread 的角度看，這個操作要麼還沒發生、要麼已經全部完成，不會有中間狀態。

最直覺的反例：

```c
// 這段 C++ 在 GPU 上是 race condition
// （多個 thread 同時跑，結果不確定）
count += 1;  // 等價於：讀 count，加 1，寫回 count
             // 三個步驟之間其他 thread 可能已經改了 count
```

`atomicAdd` 把這三步變成一個不可分割的硬體操作：

```cuda
// 原子地把 *addr += val，並回傳舊值
int old = atomicAdd(int *addr, int val);
```

### CUDA 的 atomic 函式家族

| 函式 | 操作 | 支援型別 |
|------|------|----------|
| `atomicAdd` | `*addr += val` | int, uint, ulong, float, double（sm_60+）, half2（sm_70+） |
| `atomicSub` | `*addr -= val` | int, uint |
| `atomicExch` | `*addr = val`，回傳舊值 | int, uint, ulong, float |
| `atomicMin` / `atomicMax` | 取最小值/最大值 | int, uint, ulong（float 需要 CAS 包裝） |
| `atomicAnd` / `atomicOr` / `atomicXor` | 位元操作 | int, uint |
| `atomicCAS` | compare-and-swap（下文解釋） | int, uint, ulong, ull |
| `atomicInc` / `atomicDec` | 帶上界的遞增/遞減 | uint |

`float atomicAdd` 在 sm_20+ 就有，`double atomicAdd` 要 sm_60+（P100 以後）。

### atomicCAS：萬用武器

`atomicCAS`（Compare-And-Swap）是所有 lock-free 演算法的基礎：

```cuda
// 如果 *addr == compare，把 *addr 換成 val，回傳舊值
int atomicCAS(int *addr, int compare, int val);
```

用 CAS 實作 float atomicMax（官方沒有直接支援 float atomicMax）：

```cuda
__device__ float atomicMaxFloat(float *addr, float val) {
    // 把 float 位元強制轉成 int 做 CAS
    // 正數的 IEEE 754 浮點數在 int 解讀下大小順序一致，所以可以這樣做
    int *addr_as_int = (int *)addr;
    int old = *addr_as_int;
    int assumed;
    do {
        assumed = old;
        // 只有在 val > *addr 時才更新
        old = atomicCAS(addr_as_int, assumed,
                        __float_as_int(fmaxf(val, __int_as_float(assumed))));
        // 如果 CAS 失敗（*addr 被別人改了），重試
    } while (assumed != old);
    return __int_as_float(old);
}
```

CAS loop 的 do-while 是標準模式：讀舊值、算新值、嘗試寫入，失敗就重試。

---

## 二、Atomic 的真實成本

Atomic 操作**不是免費的**。成本分兩個層次：

### 2.1 硬體序列化

global memory 上的 atomic 操作，最終要送到 L2 cache 的 atomic 單元（AMU）處理。多個請求打到**同一個 cache line**，AMU 必須序列化它們：

```
32 threads 都做 atomicAdd 到同一個位址：

Thread 0:  ──[read]──[add]──[write]──▶
Thread 1:              ──[等待]──────[read]──[add]──[write]──▶
Thread 2:                            ──[等待]──────────────...
...
Thread 31: 要等前面 31 個都完成才能做
```

32 個 thread 的延遲**疊加**。在 T4 上，global memory atomic 的吞吐是 **1 per ~600 cycles per SM**——相較於正常 global memory 存取已經夠慢，atomic 更是瓶頸。

### 2.2 Contention（競爭）

Contention 是指同時間爭同一位址的 thread 數量。Contention 越高，序列化越嚴重：

```
低 contention：每個 thread 打不同位址 → 並行
高 contention：所有 thread 打同一位址 → 全序列
```

所以 atomic 的黃金規則：**減少 contention**，方法就是先在 shared memory 做 local reduction，最後只讓每個 block 貢獻一個 atomic。

---

## 三、Parallel Reduction 的樹狀結構

Reduction 問題：把 N 個數加起來。

### 3.1 為什麼不能每個 thread 都 atomicAdd

最直覺的做法：

```cuda
// 最爛的做法（別這樣）
__global__ void naive_reduction(float *g_in, float *g_out, int n) {
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx < n)
        atomicAdd(g_out, g_in[idx]);  // N 個 thread 全打同一個位址
}
```

N = 1M 時，1M 個 atomic 序列化，吞吐完全崩潰。

### 3.2 樹狀 reduction 直覺

正確的做法是利用 **associativity（結合律）** 把加法組織成樹：

```
輸入：[a, b, c, d, e, f, g, h]

第 1 輪：
  thread 0: a + b → s[0]
  thread 2: c + d → s[2]
  thread 4: e + f → s[4]
  thread 6: g + h → s[6]
  [s[0], ?, s[2], ?, s[4], ?, s[6], ?]

第 2 輪：
  thread 0: s[0] + s[2] → s[0]
  thread 4: s[4] + s[6] → s[4]
  [s[0], ?, ?, ?, s[4], ?, ?, ?]

第 3 輪：
  thread 0: s[0] + s[4] → s[0]
  [s[0], ?, ?, ?, ?, ?, ?, ?]

最終答案在 s[0]
```

每一輪做的工作量是上一輪的一半，總共 log₂(blockDim) 輪。一個 block 內 256 thread 只需要 8 輪。

---

## 四、Shared Memory Reduction 實作

把樹狀結構用 shared memory 實現：

```cuda
// 基本版（有 bank conflict，Ch 19 解釋；這裡先看邏輯）
__global__ void reduce_smem(float *g_in, float *g_out, int n) {
    extern __shared__ float smem[];  // 大小 = blockDim.x * sizeof(float)

    unsigned int tid  = threadIdx.x;
    unsigned int gid  = blockIdx.x * blockDim.x + threadIdx.x;

    // 把 global memory 的資料搬進 shared memory
    smem[tid] = (gid < n) ? g_in[gid] : 0.0f;
    __syncthreads();

    // 樹狀 reduction：stride 從 1 倍 blockDim/2 開始，每輪減半
    for (unsigned int s = blockDim.x / 2; s > 0; s >>= 1) {
        if (tid < s) {
            smem[tid] += smem[tid + s];
        }
        __syncthreads();  // 每輪結束必須同步，確保下一輪讀到的是這一輪寫完的值
    }

    // block 的 reduction 結果在 smem[0]，用 atomicAdd 收到全域輸出
    if (tid == 0) {
        atomicAdd(g_out, smem[0]);
    }
}

// 啟動：
// int threads = 256;
// int blocks  = (n + threads - 1) / threads;
// reduce_smem<<<blocks, threads, threads * sizeof(float)>>>(d_in, d_out, n);
```

注意：`__syncthreads` 在 for loop 裡面**每一輪都要呼叫**。少了就是 race condition，多的 thread 在下一輪會讀到還沒被更新的值。

現在每個 block 只做**一個** `atomicAdd`——blocks 個數最多幾千，contention 從 N 降到 blocks。

---

## 五、Warp-Level Primitives：不用 Shared Memory 的 Reduction

從 Volta（sm_70）開始，NVIDIA 引入了 **warp shuffle**（`__shfl_*`），讓 warp 內的 thread 能直接交換 register 值，**完全不需要 shared memory**，延遲更低。

### 5.1 `__shfl_down_sync`

```cuda
// 把 lane (tid - delta) 的 var 送給自己
// mask: 哪些 lane 參與這次 shuffle
// width: 把 warp 分成多個邏輯群組（預設 32，即整個 warp）
T __shfl_down_sync(unsigned mask, T var, unsigned int delta,
                   int width = warpSize);
```

直覺：每個 lane 讀取「比自己高 delta 號 lane」的值：

```
delta = 1:
lane 0 ← lane 1 的值
lane 1 ← lane 2 的值
...
lane 30 ← lane 31 的值
lane 31 ← 未定義（出界，mask 要排除）

delta = 2:
lane 0 ← lane 2 的值
lane 1 ← lane 3 的值
...
```

用 `__shfl_down_sync` 做 warp reduction（把 32 個 thread 的值加起來）：

```cuda
__device__ float warp_reduce_sum(float val) {
    // 0xffffffff = 所有 32 個 lane 都參與
    for (int offset = warpSize / 2; offset > 0; offset >>= 1) {
        val += __shfl_down_sync(0xffffffff, val, offset);
    }
    // 執行完後，lane 0 持有 32 個 lane 的總和
    return val;
}
```

圖解（8 個 lane 示意，實際 warp 32 個）：

```
初始：[a0, a1, a2, a3, a4, a5, a6, a7]

offset=4: 每個 lane 加上右邊 4 位的值
  lane 0: a0 + a4
  lane 1: a1 + a5
  lane 2: a2 + a6
  lane 3: a3 + a7
  [a0+a4, a1+a5, a2+a6, a3+a7, a4, a5, a6, a7]

offset=2:
  lane 0: (a0+a4) + (a2+a6)
  lane 1: (a1+a5) + (a3+a7)
  [a0+a4+a2+a6, a1+a5+a3+a7, ...]

offset=1:
  lane 0: 全部 8 個的和
  [a0+a1+a2+...+a7, ...]

結果在 lane 0
```

5 次 shuffle（log₂ 32 = 5）就完成 warp 內 32 個 thread 的 reduction，**不讀寫 shared memory**，不需要 `__syncthreads`（warp 內天生同步）。

### 5.2 Mask 參數要對

Volta+ 強制要求 mask 參數指定哪些 lane 參與。這是因為 Volta 的 independent thread scheduling（[Ch 10](./10-warp-simt-execution.md) 講過），warp 內的 thread 不再保證 lockstep。

```cuda
// 錯誤：Volta+ 可能有問題（只適合 Maxwell/Pascal）
val += __shfl_down_sync(0xffffffff, val, offset);
//     ^^^^^^^^^^^
// 如果有些 lane 因為 divergence 不在這個 shuffle，
// 必須在 mask 裡去掉它們
```

對於沒有 divergence 的情況（整個 warp 都跑同一條路），`0xffffffff` 是正確的。
如果有 divergence（例如 boundary 處只有部分 lane 有效），你需要計算正確的 mask，或者用 `__activemask()` 取得當前活躍 lane。

### 5.3 Ampere+ 的 `__reduce_add_sync`

sm_80+（A100 等）引入了單一指令做整個 warp 的 reduction：

```cuda
// 直接把 warp 內所有 lane 的 mask 指定位的值加總
// 結果廣播到所有 lane
unsigned int __reduce_add_sync(unsigned mask, unsigned int value);
// 也有 __reduce_min_sync, __reduce_max_sync, __reduce_and_sync, ...
```

這是一條機器指令，比 5 次 `__shfl_down_sync` 更快。但 T4 是 Turing（sm_75），不支援。Colab 如果分到 A100，可以用。

---

## 六、組合拳：Shared Memory + Warp Shuffle

最常見的高效 reduction 模式：shared memory 做 block-level 的前期工作，warp shuffle 做最後 32-thread 的收尾。

```cuda
__device__ float warp_reduce_sum(float val) {
    for (int offset = 16; offset > 0; offset >>= 1)
        val += __shfl_down_sync(0xffffffff, val, offset);
    return val;
}

__global__ void reduce_smem_shfl(const float *g_in, float *g_out, int n) {
    // 每個 block 用 shared memory 先做粗歸約
    extern __shared__ float smem[];
    unsigned int tid = threadIdx.x;
    unsigned int gid = blockIdx.x * blockDim.x + threadIdx.x;

    smem[tid] = (gid < n) ? g_in[gid] : 0.0f;
    __syncthreads();

    // 第一階段：shared memory 把 blockDim 個元素歸約到 warpSize 個
    for (unsigned int s = blockDim.x / 2; s >= warpSize; s >>= 1) {
        if (tid < s) smem[tid] += smem[tid + s];
        __syncthreads();
    }

    // 第二階段：最後 32 個元素用 warp shuffle 收尾（不需要 shared memory）
    float val = (tid < warpSize) ? smem[tid] : 0.0f;
    if (tid < warpSize) {
        val = warp_reduce_sum(val);
    }

    // 只有 lane 0 做 atomicAdd
    if (tid == 0) {
        atomicAdd(g_out, val);
    }
}
```

這個版本比純 shared memory 版少了最後 5 輪的 `__syncthreads`，因為 warp shuffle 不需要顯式同步。

---

## 七、為什麼「每 block 一個 atomicAdd」通常就夠了

你可能會想：能不能徹底消除 atomicAdd，用兩層 reduction（block → global 不用 atomic）？

技術上可以，但不值得：

```
做法一：block reduction + atomicAdd（標準做法）
  - 每個 block 做 log(blockDim) 輪 reduction
  - 最後 1 個 atomicAdd per block
  - blocks 數量通常是幾千以下，contention 低，atomicAdd 幾乎不成瓶頸

做法二：兩階段 reduction（沒有 atomicAdd）
  - 第一次 launch：N 個 block，每個 block 輸出 1 個值到暫時陣列
  - 第二次 launch：把暫時陣列再 reduce 一次
  - 多一次 kernel launch（~5-10μs overhead，[Ch 24](./24-advanced-launch.md) 會量）
  - 多一次 H2D/D2H 或 global memory 往返

做法三：CUB 的 DeviceReduce（實際生產）
  - 底層就是類似做法二，但用了 CUDA graphs 和 persistent kernel 消除 overhead
  - 如果你需要最大效能，[Ch 32](./32-libraries.md) 介紹 CUB
```

結論：對絕大多數 workload，「shared memory reduce + 一個 atomicAdd」的成本足夠低，extra 的 atomicAdd 相比 global memory bandwidth 根本不是瓶頸。

---

## 八、底層機制：Atomic 到底怎麼實作

**在 global memory 上的 atomic**：請求送到 L2 cache 的 ROP（Render Output Unit，歷史原因）/ 現代叫 atomic 處理單元。它維護一個序列佇列，serializes conflicting requests to the same cache line。

**在 shared memory 上的 atomic（`__shared__` + atomic）**：CUDA 12 之前，shared memory 沒有硬體 atomic，是靠 L1 的 lock 機制模擬的——很慢。CUDA 12+ 和 sm_90 開始有 native shared memory atomic，但 T4（sm_75）還是走 L1 lock。

**結論**：在 shared memory 上做 atomic 比 global memory 快，但比 shared memory reduction（非 atomic）慢。所以 reduction kernel 裡的 `smem[tid] += smem[tid+s]` 要的是普通讀寫（有 `__syncthreads` 保護），不是 atomic。

---

## 九、對比取捨

| 方法 | 適用場景 | 延遲 | Contention 風險 |
|------|----------|------|-----------------|
| 裸 `atomicAdd` 全局 | 快速 prototype，N 很小 | 高（序列化） | 極高 |
| shared memory reduction + 1 atomic/block | **99% 的情況** | 中 | 低 |
| warp shuffle-only（適合 blockDim=32） | warp 大小的 reduction | 最低 | 無 |
| shared memory + warp shuffle 收尾 | 標準高效做法 | 低 | 低 |
| CUB DeviceReduce | 生產環境，需要最大效能 | 最低 | 無 |
| `__reduce_add_sync`（sm_80+） | Ampere+ kernel，warp-level | 最低 | 無 |

---

## 十、踩雷

**1. for loop 裡面忘記 `__syncthreads`**

```cuda
// 錯誤
for (int s = blockDim.x / 2; s > 0; s >>= 1) {
    if (tid < s) smem[tid] += smem[tid + s];
    // 忘了 __syncthreads()！
}
// 下一輪讀的可能是這一輪還沒寫完的舊值
```

`__syncthreads` 在 reduction loop 裡是必須的，不是可選的。

**2. `__shfl_down_sync` 的 mask 用 `0xffffffff` 但有 divergence**

如果 warp 裡有部分 thread 因為邊界條件（`if (tid < n)`）不執行，mask 要精確：

```cuda
// 計算實際活躍的 mask（Volta+）
unsigned mask = __ballot_sync(0xffffffff, condition);
val = __shfl_down_sync(mask, val, offset);
```

`__ballot_sync` 回傳目前 warp 中有多少 lane 的 condition 為真，以位元圖表示。

**3. 把 smem[0] 當 block 的結果，但沒保護只讓 tid==0 寫 atomicAdd**

```cuda
// 錯誤：多個 thread 都做 atomicAdd
if (tid < blockDim.x)   // 寫錯了，應該是 tid == 0
    atomicAdd(g_out, smem[0]);
```

這樣每個 thread 都把 smem[0] 加進去，結果是正確值的 blockDim 倍。

**4. Shared memory 大小沒傳進去（動態宣告）**

```cuda
extern __shared__ float smem[];
// launch 時忘了第三個參數
reduce<<<blocks, threads>>>(in, out, n);
// 應該是
reduce<<<blocks, threads, threads * sizeof(float)>>>(in, out, n);
```

忘了傳 shared memory 大小，smem 的實際大小是 0，存取是 undefined behavior。

**5. 把 warp-level reduction 的結果誤認為在所有 lane 都有效**

`__shfl_down_sync` 做完後，**只有 lane 0 有正確的總和**（其他 lane 持有部分和）。取結果必須只讓 lane 0 使用：

```cuda
float val = warp_reduce_sum(input);
// 只有 threadIdx.x % warpSize == 0 的 thread 有完整結果
if (threadIdx.x == 0) atomicAdd(g_out, val);  // 正確
```

---

## 十一、進階：CUB 的 BlockReduce

如果你不想從頭寫，CUB 提供了 `cub::BlockReduce`：

```cuda
#include <cub/cub.cuh>

__global__ void reduce_with_cub(float *g_in, float *g_out, int n) {
    typedef cub::BlockReduce<float, 256> BlockReduce;
    __shared__ typename BlockReduce::TempStorage temp_storage;

    int gid = blockIdx.x * blockDim.x + threadIdx.x;
    float val = (gid < n) ? g_in[gid] : 0.0f;

    float block_sum = BlockReduce(temp_storage).Sum(val);

    if (threadIdx.x == 0)
        atomicAdd(g_out, block_sum);
}
```

CUB 的 `BlockReduce` 在內部根據 sm 版本自動選擇最好的策略（shared memory / warp shuffle），比你手寫的版本通常快一些，可讀性高。

---

## 十二、動手練習

在 Colab 上執行以下實驗（Colab 預期行為，未在本機實測）：

```python
# Colab 驗證步驟

# 1. 安裝 pycuda（或直接用 nvcc）
# !pip install pycuda

# 2. 寫一個 Python wrapper 驗證 reduce_smem 的正確性
import numpy as np

N = 1 << 20  # 1M 個 float
data = np.ones(N, dtype=np.float32)  # 全 1，總和應為 N

# 用你的 kernel 計算，結果存到 result
# 驗證：
assert abs(result[0] - N) < 1e-3, f"Wrong: got {result[0]}, expected {N}"
print(f"Correct! Sum = {result[0]}")

# 3. 用 Nsight Compute 比較不同版本：
# !ncu --metrics l1tex__t_sectors_pipe_lsu_mem_global_op_atom.sum \
#      ./your_reduction_binary
# 這個 metric 告訴你實際發生了幾次 global atomic，
# 驗證確實只有 gridDim.x 次
```

---

## 本章重點

- Atomic 操作語意：不可分割的讀-改-寫，硬體序列化
- 高 contention（全部 thread 打同一位址）是效能殺手
- Parallel reduction = 樹狀結構，log(N) 輪，每輪工作量減半
- `__shfl_down_sync` 做 warp-level reduction：不用 shared memory，5 次 shuffle 搞定 32 個 thread
- mask 參數在 Volta+ 是真的重要，divergent warp 要算正確的 mask
- 標準組合：shared memory 做粗 reduce → warp shuffle 收最後 32 個 → 1 個 atomicAdd/block
- 每 block 一個 atomicAdd 的 contention 通常不成問題

---

## 自我檢核

1. 說明 `atomicAdd` 為什麼不能跟普通 `+=` 互換，哪個 CUDA 模型特性讓後者成為 race condition？
2. 256 個 thread 的 block 做樹狀 reduction，需要幾輪 for loop、幾次 `__syncthreads`？
3. `__shfl_down_sync(0xffffffff, val, 16)` 讓 lane 0 得到什麼值（假設輸入是 lane 編號）？
4. 為什麼 warp shuffle reduction 不需要 `__syncthreads`，而 shared memory reduction 需要？
5. 如果 blockDim = 128，使用 shared memory + warp shuffle 組合拳，shared memory reduction 做幾輪後交棒給 warp shuffle？

---

## 延伸閱讀

1. **Mark Harris, "Optimizing Parallel Reduction in CUDA"** — [PDF](https://developer.download.nvidia.com/assets/cuda/files/reduction.pdf)
   - 練習 D 的完整藍本。每一版的程式碼、profiling 數字（G80 上的原始數據）、分析都在這裡。必讀。

2. **CUDA C++ Programming Guide, Chapter 7.6: Warp Shuffle Functions** — [官方](https://docs.nvidia.com/cuda/cuda-c-programming-guide/index.html#warp-shuffle-functions)
   - `__shfl_*` 全家族的語意、約束、以及 Volta+ 獨立執行緒排程對 mask 的影響，權威定義。

3. **NVIDIA Developer Blog, "Faster Parallel Reductions on Kepler"** — 2014
   - 介紹 warp shuffle 在 Kepler 上的威力；概念到今天依然適用，語法差異（Kepler 沒有 mask 參數）注意一下。

4. **CUB 文件：`cub::BlockReduce`, `cub::DeviceReduce`** — [官方](https://nvlabs.github.io/cub/)
   - 如果你要在生產環境用 reduction，這是起點。了解 API 也幫助你理解最優實作長什麼樣。

5. **CUDA C++ Programming Guide, Chapter 7.7: Warp Vote Functions** — `__any_sync`, `__all_sync`, `__ballot_sync`
   - `__ballot_sync` 是算 divergent mask 的工具，跟 `__shfl_*` 一起用。

---

→ [Ch 23 streams 與異步](./23-streams-async.md)
