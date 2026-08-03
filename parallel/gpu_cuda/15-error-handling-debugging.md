# Ch 15 — 錯誤處理與除錯：compute-sanitizer / cuda-gdb

> **目標**：搞懂 CUDA 錯誤為什麼「難捉」；建立系統性的錯誤偵測習慣（`CUDA_CHECK` 巨集 + compute-sanitizer）；學會用 kernel 內 `printf` 和 `cuda-gdb` 在 GPU 上設中斷點。

> **環境**：CUDA 12.x, Colab T4 (sm_75)。除非特別標注，所有 kernel 輸出均為「Colab 預期行為，未在本機實測」。

---

CUDA 有一個讓初學者吃苦頭的特性：**它不會主動喊痛**。一個有越界存取的 kernel，很多時候靜悄悄地「成功」執行，回傳正常的 `cudaSuccess`，然後你拿到的是滿是垃圾的結果。這不是 bug，這是 GPU 的設計取捨——GPU 的錯誤處理機制是為了高吞吐而設計的，不是為了診斷友善度。

本章的目的是讓你學會在錯誤發生的當下抓住它，而不是讓它悄悄地蔓延成後面的神秘 NaN。

---

## 一、CUDA 錯誤的兩種模式

### 同步錯誤（Synchronous Error）

**發生時機**：API 呼叫立刻能檢測到的問題。  
**例子**：
- `cudaMalloc` 傳入 size=0 或超過顯示記憶體上限
- `cudaMemcpy` 的 size 超過分配的範圍（在 CPU 端有對應 pointer 的情況）
- kernel launch 的 grid 或 block 維度超過硬體限制

這類錯誤**當下**就能偵測到，呼叫的 CUDA function 會直接回傳非 `cudaSuccess` 的錯誤碼。

```
API 呼叫
  │
  └─ 回傳 cudaError_t  ← 立刻有值，可以直接 check
```

### 異步錯誤（Asynchronous Error）

**發生時機**：kernel 被放上 GPU 執行時，CPU 可能已經繼續往下跑了。  
**例子**：
- kernel 內越界存取 global memory（指標超出範圍）
- 非對齊的 atomic 操作
- kernel 內的 `assert` 失敗（CUDA 有 device-side assert）
- Stack overflow（遞迴太深）

這類錯誤**不**會在 launch 時回傳，因為 launch 本身成功了——你只是把工作排入 GPU 的 queue。錯誤在 kernel 實際執行時才觸發，但 CPU 不會立刻知道。

```
cudaLaunchKernel(...)  → 回傳 cudaSuccess（launch 本身 OK）
CPU 繼續執行...
    │
    └─ GPU 非同步跑 kernel → 越界存取 → 錯誤卡在 stream 裡
                                           ↑
                             只有下次「同步點」才會冒出來
```

**關鍵點**：異步錯誤在下一個**同步點**才會浮出水面。同步點可以是：
- `cudaDeviceSynchronize()`
- `cudaMemcpy`（預設帶隱含同步）
- `cudaStreamSynchronize(stream)`

這就是為什麼你寫的 code 在 launch 後立刻 check 錯誤，卻看到前一個 kernel 的錯誤——錯誤碼卡在系統裡，等到下次同步才被取出。

---

## 二、cudaGetLastError vs cudaPeekAtLastError

CUDA runtime 維護一個 per-thread 的「上次錯誤」狀態。你需要理解這兩個函數的差異：

### `cudaGetLastError()`

```c
cudaError_t err = cudaGetLastError();
```

- 回傳上次記錄的錯誤
- **會清除**錯誤狀態（reset 為 `cudaSuccess`）
- 適合：「我想知道目前有沒有積累的錯誤，然後清掉它繼續跑」

### `cudaPeekAtLastError()`

```c
cudaError_t err = cudaPeekAtLastError();
```

- 回傳上次記錄的錯誤
- **不清除**錯誤狀態
- 適合：你想在多個地方 check 同一個錯誤、或你只想查看而不想清

### 陷阱：為什麼「check after launch」沒用

```c
// 看起來像在 check kernel 錯誤，其實沒有
myKernel<<<128, 256>>>(d_ptr, n);
cudaError_t err = cudaGetLastError();  // ← 只 check 了 launch 本身
if (err != cudaSuccess) { ... }
// kernel 可能在 GPU 上跑出 OOB，但這裡看不到
```

要正確抓到 kernel 的異步錯誤，必須強制同步：

```c
myKernel<<<128, 256>>>(d_ptr, n);
cudaDeviceSynchronize();               // ← 等 kernel 跑完
cudaError_t err = cudaGetLastError();  // ← 這時才有 kernel 的錯誤
```

---

## 三、CUDA_CHECK 巨集

每次呼叫 CUDA API 都要 check 錯誤，但又不想把 code 淹在 `if (err != cudaSuccess)` 裡。標準做法是定義一個巨集：

```c
#include <cuda_runtime.h>
#include <stdio.h>
#include <stdlib.h>

// 基礎版：check API 呼叫
#define CUDA_CHECK(call)                                                    \
    do {                                                                    \
        cudaError_t _err = (call);                                          \
        if (_err != cudaSuccess) {                                          \
            fprintf(stderr, "CUDA error at %s:%d — %s\n",                  \
                    __FILE__, __LINE__, cudaGetErrorString(_err));          \
            exit(EXIT_FAILURE);                                             \
        }                                                                   \
    } while (0)

// 用於 kernel launch 之後：先 peek launch error，再同步，再 check kernel error
#define CUDA_CHECK_KERNEL()                                                 \
    do {                                                                    \
        cudaError_t _err = cudaPeekAtLastError();                           \
        if (_err != cudaSuccess) {                                          \
            fprintf(stderr, "Kernel launch error at %s:%d — %s\n",         \
                    __FILE__, __LINE__, cudaGetErrorString(_err));          \
            exit(EXIT_FAILURE);                                             \
        }                                                                   \
        CUDA_CHECK(cudaDeviceSynchronize());                                \
    } while (0)
```

使用範例：

```c
float *d_data;
CUDA_CHECK(cudaMalloc(&d_data, 1024 * sizeof(float)));

myKernel<<<64, 256>>>(d_data, 1024);
CUDA_CHECK_KERNEL();   // 同時 check launch 和 kernel 執行

CUDA_CHECK(cudaMemcpy(h_data, d_data, 1024 * sizeof(float),
                      cudaMemcpyDeviceToHost));
```

**為什麼用 `do { ... } while (0)`？**  
讓巨集展開後行為像一個 statement，避免在 `if/else` 裡展開出現語義問題。這是 C 的慣用法。

### 生產環境的 check

在 performance-critical 的生產 code 裡，可以用 `NDEBUG` 把 check 關掉（但記得先在 debug build 用 compute-sanitizer 驗過）：

```c
#ifdef NDEBUG
  #define CUDA_CHECK(call) (call)
  #define CUDA_CHECK_KERNEL() (void)cudaDeviceSynchronize()
#else
  // 上面完整版
#endif
```

---

## 四、cudaDeviceSynchronize() 的正確用法

`cudaDeviceSynchronize()` 阻塞 CPU，直到**所有 stream**上的所有 GPU 工作完成。它的成本不低——強制把 CPU/GPU pipeline 沖乾淨。

**在哪裡加**：
- 每個 kernel launch 之後的錯誤 check（debug 模式）
- Memcpy 之前（如果用 non-default stream 的異步 copy）
- Profiling 時確保計時準確

**在哪裡不要隨便加**：
- 在多 stream 的重疊計算/傳輸之間（加了就取消重疊，第 23 章 streams 會詳細說）
- 在 kernel 內部（沒有 device-side synchronize 可以跨 block，這是 Ch 16 的主題）

---

## 五、compute-sanitizer：GPU 上的 AddressSanitizer

`compute-sanitizer` 是 NVIDIA 的 GPU 工具，CUDA 11 之後取代舊的 `cuda-memcheck`。它用工具化（instrumentation）的方式讓 GPU 在每次記憶體存取時做額外檢查。

```
compute-sanitizer [--tool TOOL] ./your_cuda_binary
```

### 四個工具模式

| 模式 | 偵測目標 | 開銷 |
|------|----------|------|
| `memcheck`（預設） | 越界 / 未對齊 / 使用未初始化記憶體 | ~5–15x 慢 |
| `racecheck` | shared memory 的 RAW/WAR/WAW race condition | ~10–50x 慢 |
| `synccheck` | `__syncthreads` 用法錯誤（divergent branch 裡的 sync） | ~2–5x 慢 |
| `initcheck` | 讀到未初始化的 device memory | ~5–10x 慢 |

### 5.1 memcheck — 抓越界存取

```c
// 故意寫一個越界的 kernel
__global__ void oob_kernel(float *arr, int n) {
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    // n = 1024，但我們的 grid/block 算出 idx 可能超過 n
    arr[idx] = 3.14f;  // idx >= n 時越界
}

int main() {
    float *d_arr;
    cudaMalloc(&d_arr, 1024 * sizeof(float));
    // 故意 launch 超出範圍的 thread 數
    oob_kernel<<<16, 256>>>(d_arr, 1024);  // 16*256=4096 threads, 但只分配 1024
    cudaDeviceSynchronize();
    return 0;
}
```

不用 sanitizer：執行靜悄悄，回傳 `cudaSuccess`（但你寫了別人的記憶體）。

用 sanitizer：

```bash
compute-sanitizer --tool memcheck ./oob
```

預期輸出（Colab 預期行為，未在本機實測）：

```
========= COMPUTE-SANITIZER
========= Invalid __global__ write of size 4 bytes
=========     at 0x000000b0 in oob_kernel(float*, int)
=========     by thread (0,0,0) in block (4,0,0)
=========     Address 0x7f8a2c001000 is out of bounds
=========     Allocated 4096 bytes at 0x7f8a2be01000
...
=========  ERROR SUMMARY: 3072 errors
```

它告訴你：哪個 thread（block+threadIdx）、哪個地址、出界多少。

### 5.2 racecheck — 抓 shared memory race

```c
// 沒有 __syncthreads 保護的 shared memory 讀寫
__global__ void race_kernel(float *out) {
    __shared__ float smem[256];
    int tid = threadIdx.x;

    smem[tid] = (float)tid;
    // 忘記 __syncthreads()
    // 讀別的 thread 寫進去的值——classic RAW race
    if (tid > 0) {
        out[tid] = smem[tid - 1] + smem[tid];
    }
}
```

```bash
compute-sanitizer --tool racecheck ./race
```

預期輸出：

```
========= COMPUTE-SANITIZER
========= ERROR: Race reported between Read access at 0x... in race_kernel
=========        and Write access at 0x... in race_kernel
=========        Both accesses refer to shared memory location smem[0]
```

加上 `__syncthreads()` 之後 race 消失——Ch 16 會用這個例子演示。

### 5.3 synccheck — 抓 divergent branch 裡的 __syncthreads

`__syncthreads` 要求 block 內所有 active thread 都到達這個 barrier。如果你把它放在只有部分 thread 會走到的分支裡，就是未定義行為（有可能 deadlock，有可能靜悄悄地跑出錯誤結果）。

```c
// 錯誤範例：__syncthreads 在 if 裡
__global__ void bad_sync_kernel(float *out, int n) {
    int tid = threadIdx.x;
    __shared__ float smem[256];

    smem[tid] = tid;

    if (tid < n / 2) {
        __syncthreads();  // ← 只有一半的 thread 會到達這裡！
        out[tid] = smem[tid + n/2];
    }
}
```

```bash
compute-sanitizer --tool synccheck ./bad_sync
```

預期輸出：

```
========= COMPUTE-SANITIZER
========= ERROR: Barrier mismatch detected
=========     __syncthreads() called by some, but not all threads in block
=========     at bad_sync_kernel
```

### 5.4 initcheck — 抓未初始化的 device memory

```c
float *d_arr;
cudaMalloc(&d_arr, 1024 * sizeof(float));
// 故意不 cudaMemset 或初始化就讀
read_kernel<<<4, 256>>>(d_arr, 1024);
```

```bash
compute-sanitizer --tool initcheck ./uninit
```

會報哪個 thread 讀到了未初始化的 byte。

### 5.5 Colab 執行步驟

```python
# Colab cell — 寫程式碼
%%writefile oob.cu
// ... kernel code ...

# Colab cell — 編譯
!nvcc -arch=sm_75 -g -G oob.cu -o oob
# -g 開 host debug，-G 開 device debug（compute-sanitizer 需要）

# Colab cell — 用 memcheck 跑
!compute-sanitizer --tool memcheck ./oob

# 或直接用 racecheck
!compute-sanitizer --tool racecheck ./oob
```

**注意**：`-G` 會關掉 GPU 優化（所有 `nvcc` 優化 flag 都被 override），kernel 會變很慢。只在 debug 時開。

---

## 六、kernel 內 printf 除錯

GPU 的 `printf` 從 Fermi（CUDA 2.0）開始就支援了。它不是全功能的 printf——有限制，但在「我想知道某個 thread 看到什麼值」的場景非常有用。

```c
__global__ void debug_kernel(float *arr, int n) {
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx < n) {
        // 只讓 block 0 的前 4 個 thread 印
        if (blockIdx.x == 0 && threadIdx.x < 4) {
            printf("[block %d, thread %d] arr[%d] = %f\n",
                   blockIdx.x, threadIdx.x, idx, arr[idx]);
        }
    }
}
```

### printf 的機制與限制

GPU 的 `printf` 工作原理：output 先寫進一個 device-side circular buffer（預設 1 MB），kernel 結束後 CUDA runtime 把 buffer 刷到 stdout。

**限制**：

| 限制 | 說明 |
|------|------|
| buffer 大小 | 預設 1 MB，滿了後面的輸出會被丟棄 |
| 順序不保證 | 多個 warp 的 printf 輸出順序是 warp scheduler 決定的，不是 thread ID 順序 |
| 支援的格式化 | 不支援 `%p`（device pointer）、有些 floating point format string 的細節行為不同 |
| 效能 | 每次 printf 都要寫 buffer，大量 printf 會讓 kernel 慢 100x+ |

調大 buffer：

```c
cudaDeviceSetLimit(cudaLimitPrintfFifoSize, 16 * 1024 * 1024); // 16 MB
```

### 只印你需要的

```c
// 錯誤做法：每個 thread 都印 → 幾千行輸出，buffer 爆掉
__global__ void bad_debug(float *arr, int n) {
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    printf("thread %d: arr[%d]=%f\n", idx, idx, arr[idx]);  // 千萬別這樣
}

// 正確做法：限制印出條件
__global__ void good_debug(float *arr, int n) {
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx == 0 || (idx == n/2) || (idx == n-1)) {  // 只印邊界
        printf("[%d] arr[%d]=%f\n", idx, idx, arr[idx]);
    }
}
```

---

## 七、cuda-gdb 基本流程

`cuda-gdb` 是 NVIDIA 版的 GDB，可以在 kernel 裡設中斷點、印 thread 的暫存器和 shared memory。

### 編譯（debug 模式）

```bash
nvcc -arch=sm_75 -g -G -o my_kernel my_kernel.cu
#                   ↑  ↑
#                   │  └── device-side debug info（沒有這個 cuda-gdb 看不到 source）
#                   └───── host-side debug info
```

### 基本 cuda-gdb 指令

```bash
cuda-gdb ./my_kernel
```

```gdb
(cuda-gdb) break my_kernel.cu:25    # 在第 25 行設 breakpoint
(cuda-gdb) run                      # 開始執行

# 到達 breakpoint 後：
(cuda-gdb) cuda thread (0,0,0)      # 切換到 block(0,0), thread(0)
(cuda-gdb) info cuda threads        # 列出所有 CUDA thread 狀態
(cuda-gdb) print threadIdx.x        # 印 built-in 變數
(cuda-gdb) print *arr@8             # 印 arr[0..7]（device pointer）
(cuda-gdb) next                     # 單步
(cuda-gdb) continue
```

### thread 切換語法

```gdb
# 指定 block(1,0,0), thread(3,0,0)
(cuda-gdb) cuda block 1 thread 3

# 或用完整格式
(cuda-gdb) cuda block (1,0,0) thread (3,0,0)

# 切換到不同 block
(cuda-gdb) cuda block 2
```

### 印 shared memory

```gdb
# 在 kernel 內 shared array smem 的 breakpoint 後
(cuda-gdb) print smem[0]@16   # 印 smem[0..15]
```

### 條件中斷

```gdb
# 只在特定 thread 條件下觸發
(cuda-gdb) break my_kernel.cu:30 if threadIdx.x == 127
```

### Colab 的限制

Colab 沒有 interactive terminal，`cuda-gdb` 無法直接互動使用。替代方案：
1. 用 compute-sanitizer 自動偵測常見錯誤
2. 用 kernel 內 `printf` 印出診斷訊息
3. 如果你有本地 GPU 環境（WSL + CUDA Toolkit），可以完整跑 cuda-gdb

---

## 八、錯誤處理的完整樣板

把上面所有東西整合成一個你可以直接用的樣板：

```c
// cuda_utils.h — 複製到你的專案
#pragma once
#include <cuda_runtime.h>
#include <stdio.h>
#include <stdlib.h>

// ── API check ────────────────────────────────────────────────────────────
#define CUDA_CHECK(call)                                                     \
    do {                                                                     \
        cudaError_t _e = (call);                                             \
        if (_e != cudaSuccess) {                                             \
            fprintf(stderr, "[CUDA] %s:%d  %s\n",                           \
                    __FILE__, __LINE__, cudaGetErrorString(_e));             \
            exit(EXIT_FAILURE);                                              \
        }                                                                    \
    } while (0)

// ── Kernel check（launch + sync）────────────────────────────────────────
#define CUDA_CHECK_KERNEL()                                                  \
    do {                                                                     \
        cudaError_t _e = cudaPeekAtLastError();                              \
        if (_e != cudaSuccess) {                                             \
            fprintf(stderr, "[Launch] %s:%d  %s\n",                         \
                    __FILE__, __LINE__, cudaGetErrorString(_e));             \
            exit(EXIT_FAILURE);                                              \
        }                                                                    \
        _e = cudaDeviceSynchronize();                                        \
        if (_e != cudaSuccess) {                                             \
            fprintf(stderr, "[Sync] %s:%d  %s\n",                           \
                    __FILE__, __LINE__, cudaGetErrorString(_e));             \
            exit(EXIT_FAILURE);                                              \
        }                                                                    \
    } while (0)

// ── Timer（CUDA events）─────────────────────────────────────────────────
// 用法：
//   CUDA_TIMER_START(t1);
//   myKernel<<<g, b>>>(args);
//   float ms = CUDA_TIMER_STOP(t1);
#define CUDA_TIMER_DECLARE(name)                                             \
    cudaEvent_t name##_start, name##_stop

#define CUDA_TIMER_START(name)                                               \
    do {                                                                     \
        CUDA_CHECK(cudaEventCreate(&name##_start));                          \
        CUDA_CHECK(cudaEventCreate(&name##_stop));                           \
        CUDA_CHECK(cudaEventRecord(name##_start));                           \
    } while (0)

#define CUDA_TIMER_STOP(name, ms_out)                                        \
    do {                                                                     \
        CUDA_CHECK(cudaEventRecord(name##_stop));                            \
        CUDA_CHECK(cudaEventSynchronize(name##_stop));                       \
        CUDA_CHECK(cudaEventElapsedTime(&(ms_out), name##_start,             \
                                        name##_stop));                       \
        CUDA_CHECK(cudaEventDestroy(name##_start));                          \
        CUDA_CHECK(cudaEventDestroy(name##_stop));                           \
    } while (0)
```

使用：

```c
#include "cuda_utils.h"

int main() {
    const int N = 1 << 20;
    float *d_a, *d_b, *d_c;

    CUDA_CHECK(cudaMalloc(&d_a, N * sizeof(float)));
    CUDA_CHECK(cudaMalloc(&d_b, N * sizeof(float)));
    CUDA_CHECK(cudaMalloc(&d_c, N * sizeof(float)));

    CUDA_TIMER_DECLARE(ker);
    CUDA_TIMER_START(ker);

    addKernel<<<(N + 255) / 256, 256>>>(d_a, d_b, d_c, N);
    CUDA_CHECK_KERNEL();

    float ms;
    CUDA_TIMER_STOP(ker, ms);
    printf("Kernel time: %.3f ms\n", ms);

    CUDA_CHECK(cudaFree(d_a));
    CUDA_CHECK(cudaFree(d_b));
    CUDA_CHECK(cudaFree(d_c));
    return 0;
}
```

---

## 九、踩雷集錦

### 雷 1：launch 成功 ≠ kernel 正確

```c
badKernel<<<128, 256>>>(ptr, n);
if (cudaGetLastError() == cudaSuccess) {
    printf("OK!\n");  // ← 這個 OK 沒有意義，kernel 可能根本還沒跑
}
```

沒有 `cudaDeviceSynchronize()`，你只 check 了 launch 排進 queue 這個動作，不是 kernel 的執行結果。

### 雷 2：異步錯誤黏在下一個 API 呼叫上

```c
badKernel<<<...>>>(ptr);
cudaError_t e = someOtherCudaApiCall();  // e 可能是 badKernel 的錯誤！
```

這在 debug 時很迷惑人。「為什麼 `cudaMemcpy` 回傳了 kernel error？」因為那個 error 積在 stream 裡，`cudaMemcpy` 是第一個同步點，把它沖出來了。

### 雷 3：忘記 -G flag 讓 compute-sanitizer 看不到 source

```bash
nvcc -arch=sm_75 mykernel.cu -o mykernel
compute-sanitizer ./mykernel
# 只看到 offset，看不到 source line number
```

需要加 `-g -G` 才能看到 source 對應的行號。

### 雷 4：printf 輸出順序不是 thread 順序

```c
// 不要假設 thread 0 一定第一個印
// GPU 的 warp 執行順序是 scheduler 決定的
__global__ void k() {
    printf("I am thread %d\n", threadIdx.x);  // 輸出順序無法預測
}
```

如果你要診斷執行順序問題，用 atomic 計數器比 printf 更可靠。

### 雷 5：compute-sanitizer 的 false negative

compute-sanitizer 不能抓到所有錯誤。特別是：
- 越界但落在同一個 allocation 的其他合法位置上（指標算術錯誤但剛好沒出界）
- 邏輯錯誤（如同步問題造成的 race，但恰好每次都跑出「對」的答案）

compute-sanitizer 是必要條件，不是充分條件。

---

## 十、動手練習

### 練習 15.1：故意觸發各種 CUDA 錯誤

```c
// Colab 預期行為，未在本機實測
%%writefile debug_practice.cu
#include <cuda_runtime.h>
#include <stdio.h>

// 故意越界的 kernel
__global__ void oob_kernel(float *arr, int n) {
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    // 邊界：只有 [0, n) 是合法的，但我們 launch 了更多 thread
    arr[idx] = (float)idx;  // idx >= n 時越界
}

// 故意 race 的 kernel
__global__ void race_kernel(int *smem_result) {
    __shared__ int val;
    if (threadIdx.x == 0) val = 0;
    // 沒有 __syncthreads，其他 thread 不知道 val 是否初始化
    atomicAdd(&val, 1);
    __syncthreads();  // 這個 sync 放對了——先讓所有 thread 加完
    if (threadIdx.x == 0) *smem_result = val;
}

int main() {
    // 測試 1：越界（用 memcheck 可以抓到）
    float *d_arr;
    cudaMalloc(&d_arr, 64 * sizeof(float));
    oob_kernel<<<1, 256>>>(d_arr, 64);   // 256 threads 但只分 64 個 float
    cudaDeviceSynchronize();
    cudaError_t e = cudaGetLastError();
    printf("OOB kernel error: %s\n", cudaGetErrorString(e));

    // 測試 2：race（用 racecheck 可以抓到）
    int *d_result;
    cudaMalloc(&d_result, sizeof(int));
    race_kernel<<<1, 256>>>(d_result);
    cudaDeviceSynchronize();

    cudaFree(d_arr);
    cudaFree(d_result);
    return 0;
}
```

```bash
# Colab 執行步驟
!nvcc -arch=sm_75 -g -G debug_practice.cu -o debug_practice

# 先不用 sanitizer 看（可能靜悄悄成功）
!./debug_practice

# 再用 memcheck
!compute-sanitizer --tool memcheck ./debug_practice

# 再用 racecheck
!compute-sanitizer --tool racecheck ./debug_practice
```

觀察：沒有 sanitizer 時 OOB 是否出現 error？compute-sanitizer 多給了什麼資訊？

---

## 本章重點

1. **CUDA 錯誤分兩類**：同步（API 立刻回傳）和異步（kernel 內，下次同步才冒出來）
2. **CUDA_CHECK 巨集**是最基礎的防線，每個 API 呼叫都要 wrap
3. **kernel 後面必須有 cudaDeviceSynchronize() 才能抓 kernel 錯誤**，光 check launch 回傳值不夠
4. **compute-sanitizer** 有四種模式，memcheck 是日常、racecheck 和 synccheck 是專項工具
5. **kernel printf** 限制多，只用於局部診斷；避免讓每個 thread 都印
6. **cuda-gdb** 功能完整但需要本地 GPU 環境；Colab 替代方案是 sanitizer + printf

---

## 自我檢核

1. 解釋為什麼 `cudaGetLastError()` 在 kernel launch 之後立刻呼叫，無法抓到 kernel 的越界存取？
2. `cudaGetLastError()` 和 `cudaPeekAtLastError()` 的差異？各適合什麼場景？
3. `compute-sanitizer --tool racecheck` 能偵測到什麼？`synccheck` 偵測到什麼？這兩者有重疊嗎？
4. 為什麼 kernel 內的 `printf` 不保證輸出順序？
5. 編譯 CUDA 程式時，`-G` 和 `-g` 分別開啟什麼？只加 `-g` 不加 `-G`，cuda-gdb 能看到 device source 嗎？

---

## 延伸閱讀

1. **[compute-sanitizer 官方文件](https://docs.nvidia.com/cuda/compute-sanitizer/index.html)**  
   讀哪裡：Supported Tools 章節（memcheck/racecheck/synccheck/initcheck 各章）  
   學什麼：每個工具的輸出格式、suppression（忽略特定錯誤）、multi-process 模式  
   前提：本章看完即可

2. **[CUDA C++ Programming Guide — Error Handling](https://docs.nvidia.com/cuda/cuda-c-programming-guide/index.html#error-handling)**  
   讀哪裡：Programming Interface → Error Handling 小節  
   學什麼：cudaError_t 完整列舉、異步錯誤的完整行為規格  
   前提：本章

3. **[cuda-gdb 官方文件](https://docs.nvidia.com/cuda/cuda-gdb/index.html)**  
   讀哪裡：Getting Started + Kernel Debugging + Memory Inspection  
   學什麼：完整 thread/block 切換指令、hardware watchpoint、conditional breakpoint  
   前提：會用 GDB，有本地 GPU 環境

4. **[CUDA C++ Best Practices Guide — Debugging](https://docs.nvidia.com/cuda/cuda-c-best-practices-guide/index.html#debugging-cuda-code)**  
   讀哪裡：Debugging CUDA Code 章節  
   學什麼：CUDA_LAUNCH_BLOCKING 環境變數強制同步執行（debug 用）、device assertion  
   前提：本章

5. **[NVIDIA Developer Blog: CUDA Pro Tip — Pinpoint Failure with Compute Sanitizer](https://developer.nvidia.com/blog/cuda-pro-tip-pinpoint-failure-with-compute-sanitizer/)**  
   讀哪裡：全文（短，10 分鐘）  
   學什麼：compute-sanitizer 的實際 workflow，比文件更接地氣的使用例子  
   前提：本章

---

> 錯誤處理是工程素養，不是可選項。每一個沒被 check 的 CUDA API 呼叫都是一個定時炸彈——你不知道它什麼時候會讓你的模型安靜地輸出垃圾。

→ [Ch 16 同步：__syncthreads / cooperative groups / grid 同步](./16-synchronization.md)
