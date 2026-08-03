# Ch 23 — Streams 與異步：Overlap 計算與傳輸

> **目標**：理解 CUDA stream 是什麼、同 stream 序列 / 不同 stream 可並發的規則；掌握 pinned memory 對 overlap 的必要性；能用多 stream 讓 H2D 傳輸、kernel 計算、D2H 傳輸三件事流水線重疊；用 event 精確計時與跨 stream 建立相依。

> **環境**：CUDA 12.x, Colab T4 (sm_75)。程式輸出均為「Colab 預期行為，未在本機實測」，附 Colab 執行步驟。

---

你現在會寫一個跑得很快的 kernel。但整個程式的 wall time 不只是 kernel 時間——還有把資料從 CPU 搬到 GPU（H2D），以及把結果搬回 CPU（D2H）。如果這三件事是串列執行的，你浪費了一半的時間讓 GPU 閒著等傳輸，或讓傳輸等 kernel 跑完。

Stream 就是解決這個問題的工具。它讓你把 GPU 的「計算引擎」和「DMA 引擎」同時餵滿。

---

## 一、Stream 是什麼：命令佇列

**CUDA stream** 是一個命令佇列（command queue）。你把操作（kernel launch、`cudaMemcpy*`、event record/wait 等）推進這個佇列，GPU 按順序執行。

關鍵規則：

```
同一個 stream 內：嚴格序列，前一個完成才執行下一個
不同 stream 之間：沒有預設的同步，可以並行（由硬體排程）
```

ASCII 時間軸：

```
No streams（全部串列）：

時間 ──────────────────────────────────────────────────────────▶
[   H2D A   ][   Kernel A   ][   D2H A   ][   H2D B   ][   Kernel B   ][   D2H B   ]

兩個 stream（pipeline overlap）：

Stream 1: [  H2D A  ][  Kernel A  ][  D2H A  ]
Stream 2:            [  H2D B  ][  Kernel B  ][  D2H B  ]

                     ↑
              H2D B 在 Kernel A 執行時同時進行
              Kernel B 在 D2H A 進行時同時計算

Wall time 縮短接近一半
```

這個 overlap 是由 GPU 硬體的獨立引擎實現的：
- **SM（compute engine）**：跑 kernel
- **DMA engine（copy engine）**：做 `cudaMemcpyAsync`，T4 有 1 個 H2D + 1 個 D2H 的 DMA 引擎

三件事能同時跑是因為它們用的是不同的硬體資源。

---

## 二、Default Stream：一個陷阱

### Legacy default stream

最常見的寫法：

```cuda
cudaMemcpy(d_a, h_a, size, cudaMemcpyHostToDevice);
myKernel<<<grid, block>>>(d_a, d_b);
cudaMemcpy(h_b, d_b, size, cudaMemcpyDeviceToHost);
```

這些操作全部進入 **default stream**（也叫 stream 0 / null stream）。Default stream 有個特殊語意：**它和所有其他 stream 都有隱式同步點**——你放一個操作進 default stream，GPU 會等所有其他 stream 的未完成工作全部結束，才開始這個操作；這個操作完成前，其他 stream 也不能開始。

換句話說，default stream 是「全局屏障」，天生序列化一切，無法 overlap。

### Per-thread default stream（CUDA 12 的好選擇）

CUDA 12 提供了 `--default-stream per-thread` 編譯選項（或 `#define CUDA_API_PER_THREAD_DEFAULT_STREAM`），讓每個 CPU 執行緒有自己獨立的 default stream，消除了全局屏障。多執行緒 CPU 程式裡每個執行緒的 GPU 操作不會互相序列化。

本章的 stream 範例都用**明確建立的 stream**，跳過 default stream 的歷史包袱。

---

## 三、建立與使用 Stream

```cuda
// 建立
cudaStream_t stream1, stream2;
cudaStreamCreate(&stream1);
cudaStreamCreate(&stream2);

// 在指定 stream 上做 kernel launch
myKernel<<<grid, block, 0, stream1>>>(args);
//                       ^  ^^^^^^^
//                       |  指定 stream
//                       shared memory 大小（0 = 不用動態 shared memory）

// 非同步記憶體複製（必須用 cudaMemcpyAsync，不能用 cudaMemcpy）
cudaMemcpyAsync(d_a, h_a, size, cudaMemcpyHostToDevice, stream1);
cudaMemcpyAsync(h_b, d_b, size, cudaMemcpyDeviceToHost, stream2);

// 等待一個 stream 完成
cudaStreamSynchronize(stream1);

// 等待所有 stream 完成（包括 default stream）
cudaDeviceSynchronize();

// 釋放
cudaStreamDestroy(stream1);
cudaStreamDestroy(stream2);
```

---

## 四、Pinned Memory：Overlap 的前提

`cudaMemcpyAsync` 的「Async」是相對 CPU 來說的——這個函式呼叫後立刻返回，GPU 在背景做複製，CPU 可以去做別的事。

但有一個嚴苛條件：**host 的記憶體必須是 pinned（page-locked）memory**。

### 為什麼？

普通的 `malloc` 分配的記憶體可能被 OS 換頁到 swap。DMA 引擎做複製時需要一個穩定的實體位址——如果頁面被 OS 移走了，DMA 就讀錯了。所以 CUDA 要求 DMA 操作的 host 記憶體必須「釘住」，不讓 OS 換頁。

如果你用普通 `malloc` 配的記憶體呼叫 `cudaMemcpyAsync`，CUDA 會**退化成同步的複製**（內部自動用一個 pinned 暫存區做中轉），破壞 overlap。

### 怎麼分配 Pinned Memory

```cuda
float *h_a;
cudaMallocHost(&h_a, size);        // 方法一：CUDA 的 pinned alloc
// 等價於
cudaHostAlloc(&h_a, size, cudaHostAllocDefault);  // 方法二：有額外 flag 可設

// 釋放
cudaFreeHost(h_a);
```

代價：pinned memory 的分配和釋放比 `malloc` 慢（需要 OS 介入鎖定頁面）；而且過度使用會讓系統整體可用記憶體減少，影響其他程序。**只在需要 overlap 的 buffer 上用 pinned，不要全部都 pinned**。

---

## 五、三 Stream Pipeline：完整範例

把一個大陣列分成幾塊，每塊依序做 H2D → kernel → D2H，不同塊的不同階段可以重疊：

```cuda
#include <cuda_runtime.h>
#include <cassert>

// 把陣列每個元素乘以 2
__global__ void scale(float *data, int n, float factor) {
    int gid = blockIdx.x * blockDim.x + threadIdx.x;
    if (gid < n) data[gid] *= factor;
}

void pipeline_demo(int N, int NUM_STREAMS) {
    // 每段的大小
    int chunk = (N + NUM_STREAMS - 1) / NUM_STREAMS;
    size_t bytes_chunk = chunk * sizeof(float);

    // Pinned host memory（必須 pinned 才能 overlap）
    float *h_in, *h_out;
    cudaMallocHost(&h_in,  N * sizeof(float));
    cudaMallocHost(&h_out, N * sizeof(float));

    // 初始化
    for (int i = 0; i < N; i++) h_in[i] = (float)i;

    // Device memory：每個 stream 有自己的 buffer（避免 kernel 之間競爭）
    float **d_buf = new float*[NUM_STREAMS];
    for (int s = 0; s < NUM_STREAMS; s++)
        cudaMalloc(&d_buf[s], bytes_chunk);

    // 建立 stream
    cudaStream_t *streams = new cudaStream_t[NUM_STREAMS];
    for (int s = 0; s < NUM_STREAMS; s++)
        cudaStreamCreate(&streams[s]);

    int threads = 256;

    // 分塊流水線
    for (int s = 0; s < NUM_STREAMS; s++) {
        int offset = s * chunk;
        int this_chunk = min(chunk, N - offset);
        if (this_chunk <= 0) break;
        size_t this_bytes = this_chunk * sizeof(float);

        int blocks = (this_chunk + threads - 1) / threads;

        // H2D（異步，pinned memory）
        cudaMemcpyAsync(d_buf[s], h_in + offset,
                        this_bytes, cudaMemcpyHostToDevice, streams[s]);

        // Kernel（同一 stream，在 H2D 完成後才執行）
        scale<<<blocks, threads, 0, streams[s]>>>(d_buf[s], this_chunk, 2.0f);

        // D2H（同一 stream，在 kernel 完成後才執行）
        cudaMemcpyAsync(h_out + offset, d_buf[s],
                        this_bytes, cudaMemcpyDeviceToHost, streams[s]);
    }

    // 等所有 stream 完成
    cudaDeviceSynchronize();

    // 驗證
    for (int i = 0; i < N; i++)
        assert(h_out[i] == h_in[i] * 2.0f);

    // 釋放
    for (int s = 0; s < NUM_STREAMS; s++) {
        cudaStreamDestroy(streams[s]);
        cudaFree(d_buf[s]);
    }
    cudaFreeHost(h_in);
    cudaFreeHost(h_out);
    delete[] streams;
    delete[] d_buf;
}
```

時間軸（以 4 個 stream 為例，箭頭表示 同一 stream 內的序列相依）：

```
時間 ──────────────────────────────────────────────────────────▶

Stream 0: [H2D 0]→[Kernel 0]→[D2H 0]
Stream 1:      [H2D 1]→[Kernel 1]→[D2H 1]
Stream 2:           [H2D 2]→[Kernel 2]→[D2H 2]
Stream 3:                [H2D 3]→[Kernel 3]→[D2H 3]

          ↑ DMA copy engine:  H2D 和 D2H 用不同引擎，可以再疊
          ↑ Compute engine:   不同 stream 的 kernel 如果 SM 有空間可以並發
```

**注意**：多個 stream 的 kernel 並發（concurrent kernel execution）需要 SM 資源夠用。如果你的 kernel 已經佔滿所有 SM，第二個 stream 的 kernel 就得等——但 DMA 還是可以跑，H2D/D2H overlap 依然有效。

---

## 六、Event：精確計時與跨 Stream 相依

### 6.1 用 Event 計時

`cudaEvent_t` 是 GPU 時間戳記：

```cuda
cudaEvent_t start, stop;
cudaEventCreate(&start);
cudaEventCreate(&stop);

// 在 stream1 上記錄開始時間
cudaEventRecord(start, stream1);

// 做一些工作
myKernel<<<grid, block, 0, stream1>>>(args);

// 記錄結束時間
cudaEventRecord(stop, stream1);

// 等 stop event 完成（block CPU 直到 stop event 被 GPU 記錄）
cudaEventSynchronize(stop);

// 計算毫秒差
float ms;
cudaEventElapsedTime(&ms, start, stop);
printf("Kernel time: %.3f ms\n", ms);

cudaEventDestroy(start);
cudaEventDestroy(stop);
```

`cudaEventRecord(event, stream)` 的語意：在 stream 裡插入一個「計時點」，當 GPU 執行到這裡，就記錄 GPU 時間。CPU 的 `cudaEventSynchronize` 等到這個 event 被記錄才返回。

這比 `cudaDeviceSynchronize()` + CPU 計時精準——因為它是 GPU 自己的時鐘，不受 CPU-GPU 通訊延遲影響。

### 6.2 用 Event 做跨 Stream 相依

有時你需要「stream B 等 stream A 的某個操作完成後才繼續」，但不想序列化整個 stream：

```cuda
cudaEvent_t h2d_done;
cudaEventCreate(&h2d_done);

// Stream A 做 H2D
cudaMemcpyAsync(d_a, h_a, size, cudaMemcpyHostToDevice, streamA);
// Stream A 在 H2D 完成後插入 event
cudaEventRecord(h2d_done, streamA);

// Stream B 等待這個 event（不阻塞 CPU，只阻塞 GPU 的 Stream B）
cudaStreamWaitEvent(streamB, h2d_done, 0);  // 第三個參數保留，填 0
// Stream B 從這裡開始的操作都要等 h2d_done
cudaMemcpyAsync(d_b, h_b, size2, cudaMemcpyHostToDevice, streamB);
myKernel<<<grid, block, 0, streamB>>>(d_a, d_b, d_c);
```

`cudaStreamWaitEvent` 是純 GPU 端的同步——它不讓 CPU 等待，只讓 GPU 的 Stream B 排隊等到 `h2d_done` 被觸發後才繼續。

這讓你能表達精細的 DAG（有向無環圖）相依關係，而不是粗糙的「等所有 stream」。

---

## 七、Stream 的常見模式

### 模式一：計算/傳輸 Pipeline（最常見）

上面的三 stream pipeline 就是這個模式。適合：資料太大不能一次全傳，或者傳輸和計算時間接近，overlap 收益明顯。

### 模式二：Concurrent Kernels

不同的 kernel 打到不同的 stream，讓 GPU 同時跑：

```cuda
// 如果每個 kernel 只用 50% 的 SM
kernelA<<<gridA, blockA, 0, stream1>>>(args_a);
kernelB<<<gridB, blockB, 0, stream2>>>(args_b);
// T4 的 concurrent kernel 上限在 Nsight Systems 可以看到
```

**有效的條件**：每個 kernel 的 SM 佔用率 < 100%。如果 kernelA 已經把 T4 的 40 個 SM 全部佔滿，kernelB 就只能等。

### 模式三：圖（CUDA Graphs，預告）

Stream 的 overhead 在 kernel 很小很多的情況下很明顯（每次 `cudaMemcpyAsync` / kernel launch 都有 ~5-10μs CPU overhead）。CUDA Graphs 讓你把一整個 stream 的操作「錄製」起來，之後一鍵 replay，消除重複的 launch overhead。[Ch 35](./35-cuda-graphs.md) 詳細介紹。

---

## 八、底層機制：T4 的 Copy Engine

T4（Turing, sm_75）有：
- **2 個 DMA（copy）engine**：一個專跑 H2D，一個專跑 D2H
- 因此 H2D 和 D2H 可以同時跑

這意味著理論上三件事（H2D + Kernel + D2H）能完全 overlap：

```
DMA H2D:  [   chunk 0   ][   chunk 1   ][   chunk 2   ]
Kernel:             [  chunk 0  ][  chunk 1  ][  chunk 2  ]
DMA D2H:                     [ chunk 0 ][ chunk 1 ][ chunk 2 ]
```

但實際 overlap 效果受以下因素影響：
1. **Chunk 大小**：太小則 kernel 很快結束，D2H 和 H2D 之間沒有足夠的時間差
2. **PCIe 頻寬 vs. GPU 計算強度**：如果 kernel 跑 1ms 但 H2D 只要 0.1ms，三件事很難真正疊
3. **SM 資源**：多個 stream 的 kernel 是否真的能同時在 SM 上執行

Nsight Systems（下一章 [Ch 25](./25-profiling.md)）的時間軸視圖能直接告訴你 overlap 是否真的發生。

---

## 九、對比取捨

| 同步方式 | 粒度 | CPU 是否阻塞 | 適用情境 |
|----------|------|-------------|----------|
| `cudaDeviceSynchronize` | 全局 | 是 | 程式結束、全部完成後驗證 |
| `cudaStreamSynchronize(stream)` | 單一 stream | 是 | 等特定 stream 完成 |
| `cudaEventSynchronize(event)` | 單一 event | 是 | 精確計時、等特定操作 |
| `cudaStreamWaitEvent(stream, event)` | GPU 端 | 否 | 跨 stream 建立相依（不阻塞 CPU） |
| `cudaMemcpy`（非 Async） | 操作 | 是 | 簡單場景，不需要 overlap |
| `cudaMemcpyAsync` + pinned | 操作 | 否（CPU 立即返回） | 需要 overlap 的場景 |

---

## 十、踩雷

**1. 用 `cudaMemcpy`（非 Async）誤以為能 overlap**

`cudaMemcpy` 會等傳輸完成才返回，CPU 被阻塞，根本沒有機會把下一個 stream 的操作推進去。永遠要用 `cudaMemcpyAsync`。

**2. Host memory 沒有 pinned，Async 退化成同步**

```cuda
float *h_data = (float*)malloc(size);  // 普通 malloc
// 這個 Async 會退化成同步複製（CUDA 內部中轉 pinned buffer）
cudaMemcpyAsync(d_data, h_data, size, cudaMemcpyHostToDevice, stream);
```

用 `cudaMallocHost` 或 `cudaHostAlloc` 才能真正 async。

**3. 在 stream 裡插入的 Event 忘了 Synchronize 就用 ElapsedTime**

```cuda
cudaEventRecord(stop, stream);
// 沒有 cudaEventSynchronize(stop)
float ms;
cudaEventElapsedTime(&ms, start, stop);  // 可能讀到垃圾值
```

`cudaEventElapsedTime` 需要兩個 event 都已完成。必須先 `cudaEventSynchronize(stop)`。

**4. 多個 stream 的 kernel 用了同一塊 device memory（沒有 isolation）**

```cuda
// stream1 寫 d_buf，stream2 也讀 d_buf，但沒有同步相依
// 結果：race condition，輸出亂
myKernelWrite<<<grid, block, 0, stream1>>>(d_buf);
myKernelRead <<<grid, block, 0, stream2>>>(d_buf);  // 危險！
```

要麼用 event 建立相依，要麼讓每個 stream 有自己的 buffer。

**5. `cudaStreamWaitEvent` 的第三個參數不是 0**

CUDA 12 裡這個參數是 flag，目前只支援 0。傳其他值行為未定義。

---

## 十一、量化 Overlap 效益

在 Colab 上用 Nsight Systems 看 overlap（Colab 預期行為，未在本機實測）：

```bash
# 在 Colab 的 terminal 執行
nsys profile --trace=cuda,nvtx -o my_pipeline ./your_binary

# 或用 Python API：
# import subprocess
# subprocess.run(["nsys", "profile", "--trace=cuda", "-o", "out", "python", "your_script.py"])

# 產生 my_pipeline.nsys-rep，下載後用 Nsight Systems GUI 打開
# 查看 CUDA HW → 時間軸上 "CUDA HW - Copy" 和 "CUDA HW - Kernel" 是否真的重疊
```

如果 overlap 有效，你會看到：

```
CUDA HW  Copy (H2D): ─────────────────────────────────
CUDA HW  Kernels:             ─────────────────────────────────
CUDA HW  Copy (D2H):                     ─────────────────────────────
```

如果沒有 overlap，三條都是序列的。這是診斷 pinned memory 問題最直接的方法。

---

## 本章重點

- Stream = 命令佇列，同 stream 序列，不同 stream 可並發
- Default stream 有全局隱式同步語意，要 overlap 必須用明確建立的 stream
- `cudaMemcpyAsync` + pinned memory 才能真正讓 DMA 和 kernel 並行
- T4 有獨立的 H2D 和 D2H DMA engine，三件事（H2D + Kernel + D2H）能同時跑
- Event 是 GPU 時間戳：`cudaEventRecord` 插入時間點，`cudaEventElapsedTime` 算差，`cudaStreamWaitEvent` 建立 GPU 端跨 stream 相依
- Overlap 收益大小取決於 chunk size 和計算/傳輸比例，用 Nsight Systems 驗證

---

## 自我檢核

1. 為什麼 `cudaMemcpy`（非 Async）無法和 kernel 重疊？從 CPU 執行流程解釋。
2. Default stream 的「全局屏障」語意具體是什麼？舉例說明它如何打斷 overlap。
3. 你的 T4 有幾個 DMA engine？這決定了哪些操作可以真正並行？
4. `cudaStreamWaitEvent` 和 `cudaEventSynchronize` 的差別是什麼？各適合哪種情況？
5. 如果你的 kernel 在 stream1 已經跑滿所有 40 個 SM，stream2 的 kernel 能和它並行嗎？H2D/D2H 能和它並行嗎？

---

## 延伸閱讀

1. **NVIDIA Developer Blog, "How to Overlap Data Transfers in CUDA C/C++"** — [連結](https://developer.nvidia.com/blog/how-overlap-data-transfers-cuda-cc/)
   - Stream 和 pinned memory overlap 的官方教程，有完整程式碼和 Nsight Visual Profiler 截圖驗證。本章的主要參考。

2. **CUDA C++ Programming Guide, Chapter 3.2.6: Asynchronous Concurrent Execution** — [官方](https://docs.nvidia.com/cuda/cuda-c-programming-guide/index.html#asynchronous-concurrent-execution)
   - Stream、concurrent kernel、default stream 語意的規範定義。Legacy default stream vs per-thread default stream 的差異在這裡說清楚。

3. **CUDA C++ Programming Guide, Chapter 3.2.5.2: CUDA Events** — [官方](https://docs.nvidia.com/cuda/cuda-c-programming-guide/index.html#events)
   - Event 的完整 API 語意；`cudaEventCreate` 的 flag（`cudaEventDisableTiming` 讓 event 只做同步不計時，overhead 更低）。

4. **Nsight Systems 官方文件：CUDA trace** — [官方](https://docs.nvidia.com/nsight-systems/)
   - 怎麼看時間軸、確認 overlap 是否發生。CUDA HW timeline 是關鍵視圖。

5. **CUDA C++ Best Practices Guide, Chapter 9: Memory Optimizations** — [官方](https://docs.nvidia.com/cuda/cuda-c-best-practices-guide/index.html#memory-optimizations)
   - Pinned memory 的使用建議、overhead 分析、什麼情況下值得用。

---

→ [Ch 24 進階啟動](./24-advanced-launch.md)
