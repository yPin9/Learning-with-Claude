# Ch 24 — 進階啟動：Dynamic Parallelism、Persistent Kernel、Grid 大小策略

> **目標**：理解 dynamic parallelism（kernel 內 launch kernel）的用途與成本；掌握 persistent kernel（grid-stride persistent thread）的設計思路；量化 launch overhead 的數量級；知道怎麼用 `cudaOccupancyMaxActiveBlocksPerMultiprocessor` 科學地選 grid 大小。

> **環境**：CUDA 12.x, Colab T4 (sm_75)。程式輸出均為「Colab 預期行為，未在本機實測」，附 Colab 執行步驟。

---

到目前為止，每次 launch 都是 CPU 決定 grid 大小、block 大小，然後 GPU 執行。這個模型夠用，但有兩個痛點：

1. **工作量動態變化**：有些演算法（樹遍歷、BFS、adaptive refinement）不知道下一層要多少 thread，只有 kernel 跑起來後才知道。每次都回 CPU 決定太慢。

2. **Launch overhead 不可忽視**：每次 kernel launch 有 ~5–10 μs 的 CPU 端 overhead。如果你的 kernel 很小（submillisecond），launch overhead 佔主導。

這一章的三個工具分別對應這兩個問題：dynamic parallelism 解決動態工作量，persistent kernel 消除多次 launch，grid 大小策略讓你用正確的 occupancy 目標選 launch config。

---

## 一、Launch Overhead 的真實數量級

先量化問題再談解法。

### 什麼構成 Launch Overhead

CPU 呼叫 `myKernel<<<grid, block>>>(args)` 後，CUDA runtime 要做：

1. 參數驗證（grid/block 大小合法？）
2. 把 launch 命令序列化進 stream 的命令緩衝區
3. 透過 UVM driver 通知 GPU 有新工作
4. GPU 的 GPC 排程器看到新 launch，為每個 block 分配 SM

這個流程的 CPU 端佔大約 **5–10 μs**（不包含 GPU 端實際執行）。GPU 端的 launch latency 另外有幾個 μs。

### 實際問題

```
你的 kernel 跑 0.1 ms（100 μs），launch overhead 是 10 μs：
  overhead / total = 10 / (100 + 10) ≈ 9%  → 還可接受

你的 kernel 跑 0.01 ms（10 μs），launch overhead 是 10 μs：
  overhead / total = 10 / (10 + 10) ≈ 50%  → 很嚴重

你有 1000 個小 kernel，每個跑 5 μs：
  compute: 1000 * 5 μs = 5 ms
  launch:  1000 * 10 μs = 10 ms
  總計: 15 ms，但真正有效計算只有 5 ms（33%）
```

這就是 [Ch 35](./35-cuda-graphs.md) CUDA Graphs 要解決的問題：把 1000 個 launch「錄製」起來，replay 只需 1 次 launch 的 overhead。

---

## 二、Dynamic Parallelism：Kernel 內 Launch Kernel

### 概念

Dynamic parallelism（CDP, CUDA Dynamic Parallelism）讓 **device code** 能呼叫 kernel launch API：

```cuda
// 這在 device kernel 裡面是合法的（CUDA 5.0+, sm_35+）
__global__ void parent_kernel(int level, int *data, int n) {
    if (threadIdx.x == 0 && level < MAX_DEPTH) {
        int new_n = compute_new_size(data, n);
        child_kernel<<<grid_size(new_n), 256>>>(data, new_n, level + 1);
    }
}
```

GPU 不需要把工作量資訊傳回 CPU，直接在 GPU 端決定下一輪的 grid 大小。

### 典型用途

**樹遍歷（tree traversal）**：
```
BVH（Bounding Volume Hierarchy）光線追蹤，
每個節點的子節點數量不固定，
傳統方法：BFS 每層回 CPU 一次，N 層就有 N 次 launch + 等待
CDP 方法：parent kernel 直接 launch child，GPU 側全部完成

QuickSort：
partition 完後，子陣列大小才知道，
child kernel 的 grid 大小只有做完 partition 才知道
```

**Adaptive refinement（自適應細化）**：物理模擬、流體模擬裡，只在高梯度區域加密網格，哪些區域需要加密只有 kernel 算完才知道。

### 完整範例：GPU QuickSort（簡化版）

```cuda
// 在 sm_35+ 編譯：-rdc=true
#include <cuda_runtime.h>
#include <device_launch_parameters.h>

__device__ int partition(int *arr, int low, int high) {
    int pivot = arr[high];
    int i = low - 1;
    for (int j = low; j < high; j++) {
        if (arr[j] <= pivot) {
            i++;
            // swap
            int tmp = arr[i]; arr[i] = arr[j]; arr[j] = tmp;
        }
    }
    int tmp = arr[i+1]; arr[i+1] = arr[high]; arr[high] = tmp;
    return i + 1;
}

__global__ void gpu_quicksort(int *arr, int low, int high) {
    if (low < high) {
        // 只用 1 個 thread 做 partition（簡化示範，真實版更複雜）
        if (threadIdx.x == 0) {
            int pivot_idx = partition(arr, low, high);

            // 左半邊夠大才值得 launch（避免無限遞迴的微小 launch）
            if (pivot_idx - 1 - low > 16) {
                gpu_quicksort<<<1, 1>>>(arr, low, pivot_idx - 1);
            }
            // 右半邊
            if (high - (pivot_idx + 1) > 16) {
                gpu_quicksort<<<1, 1>>>(arr, pivot_idx + 1, high);
            }
        }
    }
}

// CPU 端呼叫：
// gpu_quicksort<<<1, 1>>>(d_arr, 0, N - 1);
// cudaDeviceSynchronize();
```

**編譯要求**：`-rdc=true`（relocatable device code，支援 device-side globals 和 CDP），並且 link 時要加 `-lcudadevrt`：

```bash
nvcc -rdc=true -arch=sm_75 -o quicksort quicksort.cu -lcudadevrt
```

### CDP 的成本：為什麼不要隨便用

CDP 有幾個代價：

**1. Launch overhead 更高**：device-side launch 的 overhead 比 host-side 更大——GPU 的排程器要在執行 kernel 的同時處理新的 launch 請求，overhead 可達 **數十 μs**，比 CPU 端的 5–10 μs 更慢。

**2. 記憶體 overhead**：每個 device launch 需要維護一個內部佇列，佔用 device memory。

**3. 巢狀深度有限制**：T4 最多 24 層巢狀（`sm_75`），實務上超過幾層就很慢了。

**4. CUDA 12 的改版（CDP2）**：CUDA 12 引入了 CDP2，透過更有效率的 runtime 路徑降低 overhead；需要加 `-DCUDA_ENABLE_CDP2=1` 且 sm_90+。

**結論**：CDP 適合「工作量真的無法在 CPU 端預知」的演算法。如果你能在 CPU 端估算工作量，就不要用 CDP——host-side launch + cudaMemcpy 回傳計數通常更快。

---

## 三、Persistent Kernel：消除多次 Launch

### 問題：反覆 Launch 的 Producer-Consumer

有些場景需要反覆跑同一個 kernel，直到某個條件達成（例如 BFS 的每一層，或 iterative solver 的每一步）。傳統做法：

```cuda
// 每一輪都有 launch overhead
while (!converged) {
    iteration_kernel<<<grid, block>>>(d_state);
    cudaDeviceSynchronize();  // 等完才能決定 converged（或另一個 check kernel）
    check_convergence<<<1, 1>>>(d_state, d_converged);
    cudaMemcpy(&converged, d_converged, sizeof(bool), D2H);
}
```

每一輪 = 1 launch overhead + 1 sync + 1 D2H。如果需要 1000 輪，光是 overhead 就 10 ms。

### Persistent Kernel 的思路

**把 kernel 的 main loop 放到 kernel 裡面**，kernel 本身不結束，直到工作全部做完：

```cuda
// Grid-stride persistent thread pattern
__global__ void persistent_worker(WorkQueue *queue, int *done) {
    // 每個 thread 持續從 queue 拿工作，直到 queue 空了
    while (!(*done)) {
        // 從 work queue 拿一個任務（atomic 保護）
        int task_id = atomicAdd(&queue->head, 1);
        if (task_id >= queue->total_tasks) break;

        // 做這個任務
        process_task(queue, task_id);

        // 如果是最後一個 task，標記 done
        if (atomicAdd(&queue->completed, 1) + 1 == queue->total_tasks) {
            *done = 1;
        }
    }
}
```

但 persistent kernel 的本質挑戰是：**你必須精確選 grid 大小**，讓 kernel 能「持久」佔用 GPU，又不能太大讓後續的工作沒地方跑。

### Grid-Stride Persistent Thread

最實用的 persistent pattern，配合 CUB 的 `GridBarrier` 或 cooperative groups 的 `grid.sync()`：

```cuda
#include <cooperative_groups.h>
namespace cg = cooperative_groups;

// 注意：cooperative_groups::grid_group::sync() 需要 cooperative launch
// 且 grid 大小不能超過 GPU 所有 SM 能同時承載的 block 數
__global__ void iterative_solver(float *data, int n, int max_iter) {
    cg::grid_group grid = cg::this_grid();

    for (int iter = 0; iter < max_iter; iter++) {
        // 每個 thread 處理自己的元素（grid-stride）
        for (int i = blockIdx.x * blockDim.x + threadIdx.x;
             i < n;
             i += gridDim.x * blockDim.x) {
            data[i] = compute_new_value(data[i], iter);
        }

        // 全 grid 同步：等所有 thread 都完成這一輪，才開始下一輪
        // 這是 grid.sync() 的唯一合法使用場景（cooperative launch）
        grid.sync();

        // 如果需要 convergence check，可以在這裡做（所有 thread 都看同一份 data）
    }
}

// 需要用 cooperative launch
void launch_solver(float *d_data, int n, int max_iter) {
    int threads = 256;
    int blocks;
    // 查最大能同時活躍的 block 數（下一節講）
    cudaOccupancyMaxActiveBlocksPerMultiprocessor(&blocks,
        iterative_solver, threads, 0);
    int device;
    cudaGetDevice(&device);
    cudaDeviceProp prop;
    cudaGetDeviceProperties(&prop, device);
    blocks *= prop.multiProcessorCount;

    // Cooperative launch（跟普通 launch 不一樣的 API）
    void *args[] = {&d_data, &n, &max_iter};
    cudaLaunchCooperativeKernel((void*)iterative_solver,
                                 blocks, threads, args, 0, nullptr);
    cudaDeviceSynchronize();
}
```

`grid.sync()`（cooperative groups 的 grid-wide barrier）讓所有 block 的所有 thread 同步，**但有硬性限制**：grid 大小不能超過 GPU 目前能同時承載的 block 總數（否則有 deadlock 風險——等待同步的 block 佔了 SM 位置，讓後來的 block 沒地方上，永遠等不到同步點）。

---

## 四、Grid 大小怎麼選

### 錯誤的直覺：越多 block 越好

很多人直覺是 `blocks = (N + threads - 1) / threads`——一個 thread 對應一個元素。這在 N 很大（例如 N = 10M，threads = 256，blocks = 39063）時其實是對的，因為 SM 會輪流執行 block，資源利用率高。

但在 persistent kernel 或需要 grid.sync 的場景，你需要精確算出「GPU 能同時承載幾個 block」。

### `cudaOccupancyMaxActiveBlocksPerMultiprocessor`

```cuda
int blocks_per_sm;
cudaOccupancyMaxActiveBlocksPerMultiprocessor(
    &blocks_per_sm,      // 輸出：每個 SM 最多能同時活躍幾個 block
    myKernel,            // 目標 kernel function pointer
    256,                 // blockDim
    0                    // dynamic shared memory size
);

// T4 有 40 個 SM
int device;
cudaGetDevice(&device);
cudaDeviceProp prop;
cudaGetDeviceProperties(&prop, device);
int total_blocks = blocks_per_sm * prop.multiProcessorCount;

// 以這個大小 launch：恰好填滿 GPU，不多不少
myKernel<<<total_blocks, 256>>>(args);
```

這個 API 是 CUDA occupancy calculator 的程式化版本——它根據 kernel 的 register 使用量、shared memory 使用量、以及 SM 的資源限制，算出 per-SM 的最大活躍 block 數。`total_blocks` 就是「佔滿整個 GPU 且不超額」的魔法數字。

### 懶人公式 vs. 精確計算

| 情境 | 建議 |
|------|------|
| 一般 embarrassingly parallel | `(N + threads - 1) / threads` 就好，GPU 會自動排隊 |
| Occupancy 敏感（低 reg/smem 的 kernel） | 用 `cudaOccupancyMaxActiveBlocksPerMultiprocessor` |
| Persistent kernel / cooperative launch | 必須用上面 API 算出的 `total_blocks` |
| 微調 block 大小 | 試 128/256/512，用 Nsight Compute 看 achieved occupancy |

### 用 `__launch_bounds__` 給編譯器 hint

```cuda
// 告訴 nvcc：這個 kernel 最多 256 thread/block，至少 2 block/SM
__global__ __launch_bounds__(256, 2)
void my_kernel(float *data) { ... }
```

`__launch_bounds__(maxThreadsPerBlock, minBlocksPerSM)` 讓編譯器知道你的 launch config 上限，它可以更激進地 spill registers 到 local memory（如果 reg 用量太高），確保 minBlocksPerSM 能達到。代價是 register spill 可能讓 kernel 變慢——需要 profile 驗證。

---

## 五、對比取捨

| 技術 | 適用場景 | 主要成本 | 不適合的場景 |
|------|----------|----------|-------------|
| 普通多次 launch | 大多數情況 | ~10 μs/launch，可接受 | kernel 極小（< 10 μs）且次數多 |
| Dynamic parallelism（CDP） | 工作量 CPU 端未知，樹狀/遞迴 | 更高的 device-side overhead | 工作量 CPU 端能算的 |
| Persistent kernel + grid.sync | Iterative solver，固定輪次 | 複雜性高，grid 大小有限制 | 工作量不規則，偶爾 launch |
| CUDA Graphs（Ch 35） | 固定拓撲反覆執行 | 錄製成本 | 每輪 graph 結構變化 |

---

## 六、踩雷

**1. CDP 忘了 `-rdc=true` 和 `-lcudadevrt`**

Device-side kernel launch 需要 relocatable device code。沒加這個 flag 會在 link 時失敗（找不到 `__cudaLaunchDevice` 等符號）。

**2. 用 `grid.sync()` 但 grid 比 GPU 容量大**

```cuda
// 錯誤：N 太大，blocks 超過 GPU 同時能承載的數量
myKernel<<<(N/256 + 1), 256>>>(args);  // 可能 deadlock
// 正確：用 cudaOccupancyMaxActiveBlocksPerMultiprocessor 算
```

Deadlock 的原因：grid.sync 要等所有 block 都到達 barrier，但後面排隊的 block 因為前面佔著 SM 而無法進入，形成環形等待。

**3. Persistent kernel 的 work queue 沒有 atomic 保護**

多個 thread 同時從 work queue 拿任務，如果不用 atomic：

```cuda
// 競態：兩個 thread 可能拿到同一個 task_id
int task_id = queue->head++;  // 非 atomic，有 race condition
// 正確：
int task_id = atomicAdd(&queue->head, 1);
```

**4. CDP 的 child kernel 完成前 parent 就結束了**

```cuda
__global__ void parent() {
    if (threadIdx.x == 0)
        child<<<1, 1>>>();
    // parent 結束時，CUDA runtime 會等所有 child 完成
    // 但如果 parent 做了 __syncthreads()，child 還沒結束，
    // 而 parent 的其他 thread 在等 sync，可能造成混淆
}
```

Parent kernel 結束前，CUDA 確保所有 child kernel 完成（隱式 cudaDeviceSynchronize）。但如果你在 `__syncthreads` 之後才 launch child，要注意 parent 所有 thread 都已通過 sync 點。

**5. `cudaOccupancyMaxActiveBlocksPerMultiprocessor` 算的是「上限」而非「必然」**

這個 API 算的是資源上限（register + shared memory + warp 數）。實際 occupancy 可能因為其他因素（例如 warp divergence 讓部分 warp 停頓）而低於上限。Nsight Compute 的 `achieved_occupancy` 才是真實值。

---

## 七、進階：動態並行的 CUDA 12 改版（CDP2）

CUDA 12 引入的 CDP2 主要差異：

- 不再需要 `-rdc=true`（CDP1 的要求），編譯更簡單
- Device-side launch 路徑更輕量，overhead 降低
- 需要 `--enable-cdp` 編譯選項且 sm_90+（Hopper）

T4（sm_75）不支援 CDP2，但概念相同。未來如果在 H100 上開發，CDP2 是更好的選擇。

---

## 本章重點

- Launch overhead 約 5–10 μs，kernel 極小時佔主導，是 CUDA Graphs 的動機
- Dynamic parallelism：device 端可以 launch kernel，適合工作量 CPU 端不可知的場景；device-side launch overhead 比 host-side 更高
- Persistent kernel + cooperative launch：kernel 自己跑 iteration loop，用 `grid.sync()` 做 barrier；grid 大小必須不超過 GPU 容量
- `cudaOccupancyMaxActiveBlocksPerMultiprocessor` 算出每 SM 最大活躍 block 數，乘以 SM 數得到「恰好填滿 GPU」的 grid 大小
- `__launch_bounds__` 給編譯器 hint，可以影響 register 分配策略

---

## 自我檢核

1. 為什麼 device-side kernel launch（CDP）的 overhead 比 host-side launch 更高？
2. `grid.sync()` 的 deadlock 條件是什麼？怎麼保證不發生？
3. T4 有 40 個 SM，每 SM 最大 warps = 32，每 warp = 32 threads，最大 2048 threads/SM。如果你的 kernel 用 256 threads/block 且 active blocks/SM = 8，`total_blocks` 是多少？
4. `__launch_bounds__(256, 2)` 的兩個參數各自的意義是什麼？設小了 `minBlocksPerSM` 有什麼後果？
5. 有一個 kernel 跑 500μs，你需要反覆執行 200 次。Persistent kernel 和每次 launch + sync 各自的估算總時間是多少（假設 persistent kernel 版本的 convergence check 在 GPU 端完成，不需要 D2H）？

---

## 延伸閱讀

1. **CUDA C++ Programming Guide, Chapter 3.2.7: CUDA Dynamic Parallelism** — [官方](https://docs.nvidia.com/cuda/cuda-c-programming-guide/index.html#cuda-dynamic-parallelism)
   - CDP 的完整規範：巢狀深度、記憶體 visibility、同步語意。先修 Ch 23 streams。

2. **NVIDIA Developer Blog, "Introduction to CUDA Dynamic Parallelism"** — [連結](https://developer.nvidia.com/blog/introduction-cuda-dynamic-parallelism/)
   - QuickSort 和 Mandelbrot 的完整 CDP 範例，有 profiling 對比。

3. **CUDA C++ Programming Guide, Chapter 7.26: Cooperative Groups** — [官方](https://docs.nvidia.com/cuda/cuda-c-programming-guide/index.html#cooperative-groups)
   - `grid.sync()` 的語意、cooperative launch API 的完整規範。注意 Grid Group 的限制。

4. **CUDA C++ Programming Guide, Chapter 7.8: `__launch_bounds__`** — [官方](https://docs.nvidia.com/cuda/cuda-c-programming-guide/index.html#launch-bounds)
   - `__launch_bounds__` 怎麼影響 ptxas 的 register 分配；什麼情況下用，用了一定要 profile 驗證。

5. **CUDA C++ Best Practices Guide, Chapter 10.3: Occupancy** — [官方](https://docs.nvidia.com/cuda/cuda-c-best-practices-guide/index.html#occupancy)
   - `cudaOccupancyMaxActiveBlocksPerMultiprocessor` 的用法說明，以及 occupancy 不是越高越好（Ch 11 / Ch 20 講過）的再次提醒。

---

→ [Ch 25 profiling](./25-profiling.md)
