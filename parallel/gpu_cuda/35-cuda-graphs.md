# Ch 35 — CUDA Graphs：把一串 Kernel 錄成 DAG 一次提交

> **目標**：理解 launch overhead 如何在小 kernel 迭代中累積（回連 Ch 24）；掌握 stream capture 建立 graph 的完整 API（`cudaStreamBeginCapture` / `cudaStreamEndCapture` / `cudaGraphInstantiate` / `cudaGraphLaunch`）；理解 graph 對 DL 訓練 step 的實際加速原因；了解 graph update 和動態需求。

> **環境**：CUDA 12.x, Colab T4 (sm_75)。程式輸出均為「Colab 預期，未在本機實測」，附 Colab 執行步驟。效能數字標「文獻/官方數字」或「理論預期，實測請驗證」。

---

## 問題回顧：Launch Overhead 的真實代價

[Ch 24](./24-advanced-launch.md) 量化了 launch overhead：每次 kernel launch 有 **5–10 μs 的 CPU 端 overhead**（CPU 序列化 launch 命令、通知 GPU 驅動、GPU 端排程器分配 SM）。

一個 kernel 跑 1 ms，overhead 是 1%，可以接受。

問題在於深度學習訓練的一個 step：

```
一個典型 transformer forward pass（GPT-2 small，簡化）：
  - embedding lookup              → 1 kernel, ~2 μs
  - 12 層 × 每層：
    - layer norm                  → 1 kernel, ~5 μs
    - QKV projection (cublasSgemm) → 1 kernel, ~50 μs
    - attention softmax           → 3 kernels, ~15 μs
    - attention output projection  → 1 kernel, ~50 μs
    - FFN layer 1                 → 1 kernel, ~50 μs
    - FFN activation (GELU)       → 1 kernel, ~5 μs
    - FFN layer 2                 → 1 kernel, ~50 μs
  - 共 ~96 個 kernel，加上 backward 約 200+ 個

launch overhead: 200 kernels × 10 μs = 2 ms
GPU compute 時間（假設）: 20 ms
overhead 佔比: 2/(20+2) ≈ 9%  → 已經很可觀
```

如果 batch size 小（推論場景）或模型層多（深度 ResNet），overhead 比例更高。

**根本原因**：每次 launch 都需要 CPU 和 GPU 之間的協調——CPU 往命令緩衝區寫 launch 命令，GPU 的 GPC 排程器讀取、分配 SM。這個協調有最小 overhead，不論 kernel 多短。

CUDA Graphs 的答案：把這 200+ 次 launch 的「結構」錄下來，replay 只需要 **1 次提交**，GPU 直接從錄好的 graph 執行，CPU 不需要在每個 kernel 之間插手。

---

## Graph 的核心概念：把工作描述成 DAG

一個 CUDA Graph 是一個 **DAG（有向無環圖）**：

- **節點（node）**：一個操作——kernel launch、memcpy、memset、event record、child graph、host function、等等
- **邊（edge）**：依賴關係（「A 完成後才能執行 B」）

```
┌─────────┐      ┌─────────┐      ┌─────────┐
│ memcpy  │ ───> │ kernel A│ ───> │ kernel B│
│ H2D     │      │         │  ┌─> │         │
└─────────┘      └─────────┘  │   └─────────┘
                               │
                 ┌─────────┐   │   ┌─────────┐
                 │ kernel C│ ──┘   │ memcpy  │
                 │（並行）  │ ────> │ D2H     │
                 └─────────┘       └─────────┘
```

DAG 表達了：memcpy H2D 完成後，kernel A 和 kernel C 可以並行跑；A 和 C 都完成後，kernel B 才能跑；B 完成後，memcpy D2H。

傳統 stream 方式每次 iteration 都要 CPU 重新提交這 5 個操作。Graph 方式：建立 graph 一次，之後每次 iteration 只是 `cudaGraphLaunch(exec, stream)` 一次呼叫。

---

## Stream Capture：最方便的 Graph 建立方式

CUDA Graphs 有兩種建立方式：

1. **Explicit API**：手動用 `cudaGraphCreate`、`cudaGraphAddKernelNode` 等一個個節點加進去
2. **Stream Capture**：在 stream 上「錄製」正常的 CUDA 呼叫，自動建 graph

Stream Capture 幾乎是唯一你會用的方式，因為它讓你把現有程式碼包上兩行就變成 graph，不需要重寫。

### 完整流程

```cpp
// Step 1：建立 capture stream（不能是 default stream）
cudaStream_t capture_stream;
cudaStreamCreate(&capture_stream);

// Step 2：開始錄製
cudaStreamBeginCapture(capture_stream, cudaStreamCaptureModeGlobal);
// 從這行起，capture_stream 上的所有操作都被「記錄」而不是「執行」

// Step 3：在 capture stream 上做你想錄的事
kernel_A<<<grid, block, 0, capture_stream>>>(args_A);
kernel_B<<<grid, block, 0, capture_stream>>>(args_B);
cudaMemcpyAsync(dst, src, size, cudaMemcpyDeviceToDevice, capture_stream);
kernel_C<<<grid, block, 0, capture_stream>>>(args_C);
// 這些操作都沒有真正執行，只是被錄下來

// Step 4：結束錄製，取得 cudaGraph_t
cudaGraph_t graph;
cudaStreamEndCapture(capture_stream, &graph);

// Step 5：Instantiate（編譯/最佳化 graph）
cudaGraphExec_t graph_exec;
cudaGraphInstantiate(&graph_exec, graph, nullptr, nullptr, 0);
// graph 可以銷毀了（graph_exec 是可執行的實例）
cudaGraphDestroy(graph);

// Step 6：Replay（可以多次）
for(int iter = 0; iter < num_iterations; iter++) {
    // 更新輸入資料（但不重新錄製）
    // 注意：如果 kernel 讀指標指向的資料，只要指標不變就沒問題
    cudaGraphLaunch(graph_exec, replay_stream);
    cudaStreamSynchronize(replay_stream);
}

// Step 7：清理
cudaGraphExecDestroy(graph_exec);
cudaStreamDestroy(capture_stream);
```

---

## 實際範例：1000 個小 Kernel 的迭代

```cpp
// Colab 執行步驟：
// Runtime → Change runtime type → GPU
// !nvcc -o cuda_graphs cuda_graphs.cu && ./cuda_graphs

#include <cuda_runtime.h>
#include <cstdio>
#include <chrono>

__global__ void small_kernel(float *data, float val, int n) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if(i < n) data[i] += val;
}

int main() {
    const int N = 1024;
    const int NUM_KERNELS = 200;    // 模擬一個 training step 的 kernel 數量
    const int NUM_ITERS = 1000;

    float *d_data;
    cudaMalloc(&d_data, N * sizeof(float));
    cudaMemset(d_data, 0, N * sizeof(float));

    cudaStream_t stream;
    cudaStreamCreate(&stream);

    // ── 方式 A：傳統逐一 launch ──
    auto t0 = std::chrono::high_resolution_clock::now();
    for(int iter = 0; iter < NUM_ITERS; iter++) {
        for(int k = 0; k < NUM_KERNELS; k++) {
            small_kernel<<<(N+127)/128, 128, 0, stream>>>(d_data, 0.001f, N);
        }
        cudaStreamSynchronize(stream);
    }
    auto t1 = std::chrono::high_resolution_clock::now();
    double ms_baseline = std::chrono::duration<double,std::milli>(t1-t0).count();

    // ── 方式 B：CUDA Graph ──
    cudaStream_t capture_stream;
    cudaStreamCreate(&capture_stream);

    // 錄製一個 step（NUM_KERNELS 個 kernel）
    cudaStreamBeginCapture(capture_stream, cudaStreamCaptureModeGlobal);
    for(int k = 0; k < NUM_KERNELS; k++) {
        small_kernel<<<(N+127)/128, 128, 0, capture_stream>>>(d_data, 0.001f, N);
    }
    cudaGraph_t graph;
    cudaStreamEndCapture(capture_stream, &graph);

    cudaGraphExec_t graph_exec;
    cudaGraphInstantiate(&graph_exec, graph, nullptr, nullptr, 0);
    cudaGraphDestroy(graph);

    auto t2 = std::chrono::high_resolution_clock::now();
    for(int iter = 0; iter < NUM_ITERS; iter++) {
        cudaGraphLaunch(graph_exec, stream);
        cudaStreamSynchronize(stream);
    }
    auto t3 = std::chrono::high_resolution_clock::now();
    double ms_graph = std::chrono::duration<double,std::milli>(t3-t2).count();

    printf("Baseline (no graph): %.2f ms for %d iters\n", ms_baseline, NUM_ITERS);
    printf("CUDA Graph:          %.2f ms for %d iters\n", ms_graph, NUM_ITERS);
    printf("Speedup: %.2fx\n", ms_baseline / ms_graph);
    // 預期輸出（T4, Colab）：
    //   Baseline: ~2000 ms
    //   CUDA Graph: ~200 ms
    //   Speedup: ~10x（kernel 計算量極小時 overhead 主導，graph 節省顯著）
    // （Colab 預期，未在本機實測）

    cudaGraphExecDestroy(graph_exec);
    cudaStreamDestroy(capture_stream);
    cudaStreamDestroy(stream);
    cudaFree(d_data);
    return 0;
}
```

加速比取決於「kernel 計算時間 vs launch overhead」的比值。如果每個 kernel 跑 1 ms，launch overhead 10 μs 只佔 1%，graph 幾乎不加速。如果每個 kernel 跑 10 μs，overhead 佔 50%，graph 能帶來 ~2x 加速（理論預期）。

---

## 為何 DL Training Step 特別適合 Graph

DL 訓練的關鍵特性：

1. **迭代結構固定**：每個 training step 的 kernel 序列和依賴關係完全相同（同一個模型架構），只有輸入資料和梯度的值在變，指標（記憶體位址）不變
2. **Kernel 數量多，個別計算量小**：layer norm、bias add、GELU activation 等 kernel 跑幾十微秒
3. **大量迭代**：一個模型訓練幾萬到幾億個 step

這三個特性讓 CUDA Graphs 效果很好：graph 只需要建立一次，之後重複 replay 幾萬次，每次 replay 節省的 overhead 累積起來很可觀。

PyTorch 從 1.10 開始支援 `torch.cuda.CUDAGraph`：

```python
import torch

# torch.cuda.CUDAGraph 封裝了 stream capture
g = torch.cuda.CUDAGraph()
s = torch.cuda.Stream()

# 預備：在 capture 前跑一次「warmup」
with torch.cuda.stream(s):
    for _ in range(3):
        y = model(x)

# 錄製
with torch.cuda.graph(g, stream=s):
    y = model(x)

# Replay（x 的記憶體位址不能改，只能改 x 的值）
x.copy_(new_input)
g.replay()
output = y  # y 的值已更新
```

PyTorch 2.x 的 `torch.compile` 底層用 CUDA Graphs（配合 `cudagraphs` backend）作為效能優化的一個重要手段。

---

## 限制：哪些操作不能錄進 Graph

不是所有 CUDA 操作都能被 stream capture：

| 不能 capture 的操作 | 原因 |
|---------------------|------|
| `cudaMalloc` / `cudaFree` | 記憶體分配是一次性的，graph 設計上要求指標固定 |
| `cuDNN autotuning`（`cudnnFindAlgorithm*`） | 需要實際執行多個 kernel 來 benchmark |
| `cudaStreamSynchronize`（在 capture stream 外的 stream）| 打破 DAG 結構 |
| `cudaMemcpy`（同步版本，無 stream） | 必須用 `cudaMemcpyAsync` |
| Host function（部分限制）| 限制取決於 CUDA 版本 |
| `printf` 在 kernel 裡 | 可以錄，但 replay 時 printf buffer 可能不正確 |

違反這些限制，`cudaStreamBeginCapture` 到 `cudaStreamEndCapture` 之間的操作會導致 `CUDA_ERROR_STREAM_CAPTURE_UNSUPPORTED`。

---

## Graph Update：不重新錄製就改變 Kernel 參數

一個常見需求：每次 iteration 的 kernel 參數（例如 learning rate）會變，但 graph 結構不變。重新 capture 和 instantiate 代價很高（需要幾 ms 的 JIT 時間），graph update 提供了更輕量的方式：

```cpp
// 修改 graph 中某個 kernel node 的參數
cudaKernelNodeParams params = {};
// 從 graph_exec 裡找到 node 的 handle
cudaGraphNode_t node;
// ... 用 cudaGraphGetNodes 或 cudaGraphNodeFindInClone 找到 node ...

params.func           = (void*)small_kernel;
params.gridDim        = dim3((N+127)/128);
params.blockDim       = dim3(128);
params.sharedMemBytes = 0;
void *new_args[]      = {&d_data, &new_val, &N};
params.kernelParams   = new_args;
params.extra          = nullptr;

// 更新 graph_exec 中的 node（不重新 instantiate）
cudaGraphExecKernelNodeSetParams(graph_exec, node, &params);
```

更簡單的 CUDA 12.x 方式：`cudaGraphExecUpdate`，對比舊 graph 和新 graph 的差異，只更新變動的部分：

```cpp
cudaGraphExecUpdateResult update_result;
cudaGraphNode_t error_node;
cudaGraphExecUpdate(graph_exec, new_graph, &error_node, &update_result);
// 如果 update_result 是 cudaGraphExecUpdateSuccess，直接 replay 就行
// 如果更新失敗（圖結構改變），要重新 instantiate
```

`cudaGraphExecUpdate` 的代價遠低於 `cudaGraphInstantiate`（幾十到幾百 μs vs 幾 ms）。

---

## 踩雷清單

**錯誤直覺 1：可以在 capture 期間用 default stream。**
正確：`cudaStreamBeginCapture` 不能用 default stream（`cudaStreamLegacy`）。Default stream 有隱式全局同步語義，打破了 capture 的假設。你必須建一個 non-blocking stream（`cudaStreamCreateWithFlags(&s, cudaStreamNonBlocking)`）或普通 stream（`cudaStreamCreate(&s)`）。

**錯誤直覺 2：Graph replay 後不需要 sync，輸出已在 d_out 裡。**
正確：`cudaGraphLaunch` 是非同步的，只是把 graph 提交到 stream。結果可用要等 `cudaStreamSynchronize(stream)`。

**錯誤直覺 3：每個 iteration 的 x 可以是不同的記憶體位址。**
正確：Graph capture 時記錄的是 **指標的值（記憶體位址）**，不是指標的名字。Replay 時 kernel 讀的是同一個位址。你必須用 `tensor.copy_(new_data)` 原地更新，不能換一個新的指標。這也是 PyTorch CUDAGraph 需要固定輸出 tensor 的原因。

**錯誤直覺 4：Graph 一定比沒有 Graph 快。**
正確：如果你的 kernel 計算時間遠大於 launch overhead（幾百 ms 的大型 GEMM），Graph 帶來的加速接近 0。只有 launch overhead 佔顯著比例（小 kernel + 多 kernel 的場景）才值得用 Graph。

**錯誤直覺 5：`cudaGraphInstantiate` 很輕量，可以每次 step 重新 instantiate。**
正確：`cudaGraphInstantiate` 需要 JIT 編譯和內部分配，通常花 1–10 ms（視 graph 大小）。在 step 間重新 instantiate 會把你節省的 launch overhead 全部吃回去。Instantiate 應該只做一次（或在 graph 結構真的改變時）。

---

## 進階：顯式 Graph 建立 API

Stream capture 不能捕捉「分支」（if-else 取決於 GPU 計算結果的路徑）。這種情況要用顯式 API：

```cpp
// 建立空 graph
cudaGraph_t graph;
cudaGraphCreate(&graph, 0);

// 加入 kernel node
cudaKernelNodeParams params_A = {};
params_A.func = (void*)kernel_A;
params_A.gridDim = dim3(N/128);
params_A.blockDim = dim3(128);
void *args_A[] = {&d_data, &N};
params_A.kernelParams = args_A;

cudaGraphNode_t node_A, node_B;
cudaGraphAddKernelNode(&node_A, graph, nullptr, 0, &params_A);   // 無依賴

// node_B 依賴 node_A
cudaGraphAddKernelNode(&node_B, graph, &node_A, 1, &params_B);

// 加入 memcpy node
cudaGraphNode_t node_copy;
cudaMemcpy3DParms copy_params = {};
// ... 填 copy_params ...
cudaGraphAddMemcpyNode(&node_copy, graph, &node_B, 1, &copy_params);

// instantiate 和 launch 同前
```

顯式 API 讓你完全控制 DAG 結構，但寫起來繁瑣。通常只在 stream capture 有限制的場景（複雜分支、動態並行）才用。

---

## 動手練習

**Colab 執行步驟：**
1. Runtime → Change runtime type → GPU (T4)
2. 新建 `.cu` 檔，`!nvcc -O2 -o prog prog.cu && ./prog`
3. CUDA 12.x 的 Colab 環境支援 CUDA Graphs

練習 A：實作上面的範例（200 個 small_kernel，1000 次 iteration），量測有無 Graph 的時間差。試著調整 N 和 kernel 的計算量（加更多 math），觀察 speedup 如何隨計算量變化。

練習 B：用 Graph capture 錄製 `cublasSgemm` + 一個後處理 kernel，replay 1000 次，比較和直接 launch 的時間。

練習 C：嘗試在 capture 期間呼叫 `cudaMalloc`，觀察錯誤訊息（理解限制）。

---

## 本章重點

- CUDA Graphs 把一串 kernel/memcpy 錄成 DAG，replay 只需 1 次提交，消除 per-kernel 的 CPU 端 launch overhead
- 建立流程：`StreamBeginCapture` → 正常 CUDA 呼叫 → `StreamEndCapture` → `GraphInstantiate` → `GraphLaunch`（多次）
- Graph exec 的指標必須固定；只能更新值，不能換位址
- DL training step 是最適合的場景：大量小 kernel、固定結構、大量 iteration
- `cudaGraphExecUpdate` 比重新 instantiate 便宜得多
- 不是所有操作能 capture：`cudaMalloc`、同步 memcpy、default stream 均不行

## 自我檢核（主動回憶）

1. 為什麼 CUDA Graphs 對 DL training step 有效，但對單個大型 GEMM 效果很有限？
2. Graph capture 後，如果要改變 kernel 的某個純量參數（例如 learning rate），應該怎麼做？（有兩種方式）
3. 為什麼 capture 不能用 default stream？
4. `cudaGraphInstantiate` 做了什麼？為什麼不能每次 step 都 instantiate？
5. `cudaGraphExecUpdate` 和 `cudaGraphInstantiate` 的主要差異？

## 延伸閱讀

1. **CUDA Programming Guide: CUDA Graphs** — [docs.nvidia.com/cuda/cuda-programming-guide/.../cuda-graphs](https://docs.nvidia.com/cuda/cuda-programming-guide/04-special-topics/cuda-graphs.html)：官方最完整的說明，包含 capture mode、顯式 API、update、動態 graph 等
2. **NVIDIA Dev Blog: Constructing CUDA Graphs with Dynamic Parameters** — [developer.nvidia.com/blog](https://developer.nvidia.com/blog/constructing-cuda-graphs-with-dynamic-parameters)：`cudaGraphExecUpdate` 的實際範例，說明如何在不重新 instantiate 的情況下改變參數
3. **PyTorch CUDA Graphs** — [pytorch.org/docs/stable/notes/cuda.html#cuda-graphs](https://pytorch.org/docs/stable/notes/cuda.html#cuda-graphs)：PyTorch 對 CUDAGraph 的封裝和限制說明，包含 warmup 要求
4. **CUDA Graphs OLCF Slides** — olcf.ornl.gov（2021）：清晰的 DAG 圖解和 API 逐步說明，適合作為入門補充
5. **Modal GPU Glossary: CUDA Graph** — [modal.com/gpu-glossary/host-software/cuda-graph](https://modal.com/gpu-glossary/host-software/cuda-graph)：簡潔的概念說明，適合快速複習

---

CUDA Graphs 是 NVIDIA 生態獨有的功能。如果需要在 AMD GPU 或 Intel GPU 上跑同樣的程式碼，要用什麼替代方案？

## 補充：Graph 的 Debug 技巧

當 Graph 的結果不正確，典型的 debug 步驟：

1. **先把 Graph 關掉，確認結果**：把 `cudaGraphLaunch` 換回逐一 launch，如果結果正確，問題出在 Graph 的指標固定假設（你在 replay 時換了指標）。

2. **確認 capture 期間沒有漏 stream**：如果你在 capture 期間呼叫了 cuBLAS，要確保 cuBLAS 的 handle 用的 stream 就是 capture stream（`cublasSetStream(handle, capture_stream)`）。如果 cuBLAS 用的是 default stream，那些操作不會被 capture 進 graph。

3. **`cudaStreamGetCaptureInfo`**：在 capture 期間呼叫，可以查詢 stream 的 capture 狀態（`cudaStreamCaptureStatus`），確認 capture 已啟動。

4. **用 Nsight Systems 看 graph replay**：Nsight Systems 的 timeline 會把 `cudaGraphLaunch` 顯示為單一事件，但可以展開看每個 node 的執行時間。

---

→ [Ch 36 跨平台](./36-cross-platform.md)
