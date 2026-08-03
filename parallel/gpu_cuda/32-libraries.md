# Ch 32 — CUDA 生態函式庫：cuBLAS、cuDNN、CUB、cuSPARSE/cuFFT/cuRAND

> **目標**：掌握 cuBLAS 的 handle/workspace 模式、`cublasSgemm` 的 column-major 陷阱、Tensor Core 路徑（`cublasGemmEx`）；了解 cuDNN 的卷積/pooling/norm API；理解 CUB 的 device-level 原語（`DeviceReduce`、`BlockScan`）；對 cuSPARSE/cuFFT/cuRAND 有夠用的心智圖，知道什麼時候用、什麼時候換手寫 kernel。

> **環境**：CUDA 12.x, Colab T4 (sm_75)。程式輸出均為「Colab 預期，未在本機實測」，附 Colab 執行步驟。效能數字標注「文獻/官方數字」或「理論預期，實測請驗證」。

---

## 為什麼要用函式庫而不是自己寫 kernel？

一個常見的學習節奏：學完 Ch 17 shared memory tiling 之後，自己寫了一個 tiled GEMM，效能大約是 cuBLAS 的 1/5 到 1/3。接下來有兩條路：

**路 A**：繼續優化，補 Ch 38 GEMM 深挖——swizzle layout、pipeline prefetch、Tensor Core 整合——幾週後也許能跑到 cuBLAS 的 85%。

**路 B**：直接叫 cuBLAS，把省下的工程時間花在模型創新。

路 B 對絕大多數工程師是正確答案。NVIDIA 的函式庫團隊用的是 CUTLASS + autotuner，對每個 GPU 架構手調 kernel，你幾乎打不贏。但你必須知道這些函式庫的使用方式、陷阱、以及什麼時候它們不夠用。

---

## cuBLAS

### Handle 模式：為什麼需要 `cublasHandle_t`

cuBLAS 不是一組靜態函式，每次呼叫都是無狀態的——它維護一個 **handle**，裡面存了：

- 目前使用的 CUDA stream（決定 kernel 在哪條 stream 上執行）
- workspace 指標（大型 GEMM 可能需要暫存記憶體）
- 數學模式（是否允許 Tensor Core 近似運算）
- 指標模式（標量在 host 還是 device 上）

```cpp
#include <cublas_v2.h>

cublasHandle_t handle;
cublasCreate(&handle);                        // 建立 handle（昂貴，只做一次）

// 綁定到特定 stream（重要：否則跑在 default stream）
cudaStream_t stream;
cudaStreamCreate(&stream);
cublasSetStream(handle, stream);

// … 呼叫 cuBLAS 函式 …

cublasDestroy(handle);                        // 釋放
cudaStreamDestroy(stream);
```

`cublasCreate` 很貴（初始化內部狀態、JIT 部分 kernel），**不要在迴圈裡建立/銷毀 handle**。生產程式碼裡 handle 是模組層級的物件，整個應用的生命週期內只建立一次。

---

### cublasSgemm 與 column-major 陷阱

`cublasSgemm` 計算：

```
C = alpha * op(A) * op(B) + beta * C
```

完整簽名（CUDA 12 cuBLAS API）：

```cpp
cublasStatus_t cublasSgemm(
    cublasHandle_t handle,
    cublasOperation_t transa,   // op(A)：CUBLAS_OP_N（不轉置）或 CUBLAS_OP_T（轉置）
    cublasOperation_t transb,   // op(B)
    int m,                      // op(A) 的列數，C 的列數
    int n,                      // op(B) 的行數，C 的行數
    int k,                      // op(A) 的行數，op(B) 的列數
    const float *alpha,         // 標量 alpha（host 或 device 指標，視指標模式而定）
    const float *A,             // device 上的 A
    int lda,                    // A 的 leading dimension（column-major 下是「每欄有幾行」）
    const float *B,             // device 上的 B
    int ldb,                    // B 的 leading dimension
    const float *beta,          // 標量 beta
    float *C,                   // device 上的 C（輸入/輸出）
    int ldc                     // C 的 leading dimension
);
```

### Column-major 陷阱（這裡最容易踩錯）

cuBLAS 繼承自 BLAS，使用 **column-major（Fortran 慣例）** 儲存：矩陣的第一個 index 是 row，但記憶體裡沿欄方向連續存放。

C/C++ 程式設計師的直覺是 row-major：`A[i][j]` 的 `i` 是 row，記憶體是「一列一列」放。

兩種佈局對同一塊記憶體的詮釋完全不同：

```
Row-major C[3][4]（C 慣例）：
  記憶體：[a00 a01 a02 a03 | a10 a11 a12 a13 | a20 a21 a22 a23]
  LDA（stride 到下一 row）= 4

Column-major（cuBLAS 慣例）同一塊記憶體詮釋為 C[4][3]：
  「欄」在連續，第一欄 = a00,a01,a02,a03；第二欄 = a10,a11,...
  LDA（stride 到下一欄）= 4
```

**實用 trick**：利用數學上的等式

```
(A B)ᵀ = Bᵀ Aᵀ
```

如果你在 C++ 裡用 row-major 存 `A(M×K)` 和 `B(K×N)`，想算 `C = A * B`（C 也 row-major），可以換個角度：

cuBLAS 看到的記憶體佈局，把 row-major `A(M×K)` 當成 column-major `Aᵀ(K×M)`（已被「轉置」），把 row-major `B(K×N)` 當成 column-major `Bᵀ(N×K)`。

所以你只要叫：

```cpp
// 目標：C(M×N) = A(M×K) * B(K×N)，全部 row-major
// 等價：Cᵀ(N×M) = Bᵀ(N×K) * Aᵀ(K×M)，全部 column-major
// cuBLAS 看到的就是後者，交換 A/B 的參數位置即可

cublasSgemm(
    handle,
    CUBLAS_OP_N, CUBLAS_OP_N,
    N, M, K,          // 注意：n, m, k 的順序（對應 Cᵀ 是 N×M）
    &alpha,
    B_device, N,      // B 當 A 傳（leading dim = N，原本 B 的 row 寬）
    A_device, K,      // A 當 B 傳（leading dim = K，原本 A 的 row 寬）
    &beta,
    C_device, N       // C 的 leading dim = N
);
```

這個 trick 避免你在 CPU 端做真正的轉置操作。原理是：cuBLAS 計算 `Bᵀ * Aᵀ`（column-major），結果放到 `Cᵀ`（column-major），而 `Cᵀ`（column-major）在記憶體裡正好等於 `C`（row-major）。

**踩雷清單（下面還有系統整理，這裡先給最關鍵的）**：`lda` 不是矩陣的 M 或 K，而是「記憶體中下一欄的起點距離當前欄起點幾個元素」。不 padding 的矩陣：column-major 下 lda = 行數（M），row-major 下 lda = 欄數（N）。

---

### 完整範例

```cpp
// Colab 執行步驟：
// !apt-get install -y nvcc
// 用下面的 %%cuda magic cell 或 .cu 檔案
// 編譯：nvcc -o cublas_sgemm cublas_sgemm.cu -lcublas

#include <cublas_v2.h>
#include <cuda_runtime.h>
#include <cstdio>
#include <cstdlib>

#define CHECK_CUDA(x) do { cudaError_t e = (x);            \
    if(e != cudaSuccess) {                                   \
        fprintf(stderr, "CUDA error: %s at %s:%d\n",        \
                cudaGetErrorString(e), __FILE__, __LINE__);  \
        exit(1);                                             \
    } } while(0)

#define CHECK_CUBLAS(x) do { cublasStatus_t e = (x);       \
    if(e != CUBLAS_STATUS_SUCCESS) {                        \
        fprintf(stderr, "cuBLAS error %d at %s:%d\n",      \
                (int)e, __FILE__, __LINE__);                 \
        exit(1);                                             \
    } } while(0)

int main() {
    const int M = 1024, N = 1024, K = 1024;

    // 在 host 建立測試資料
    float *h_A = (float*)malloc(M * K * sizeof(float));
    float *h_B = (float*)malloc(K * N * sizeof(float));
    float *h_C = (float*)malloc(M * N * sizeof(float));
    for(int i = 0; i < M*K; i++) h_A[i] = 1.0f;
    for(int i = 0; i < K*N; i++) h_B[i] = 1.0f;
    for(int i = 0; i < M*N; i++) h_C[i] = 0.0f;

    float *d_A, *d_B, *d_C;
    CHECK_CUDA(cudaMalloc(&d_A, M * K * sizeof(float)));
    CHECK_CUDA(cudaMalloc(&d_B, K * N * sizeof(float)));
    CHECK_CUDA(cudaMalloc(&d_C, M * N * sizeof(float)));
    CHECK_CUDA(cudaMemcpy(d_A, h_A, M*K*sizeof(float), cudaMemcpyHostToDevice));
    CHECK_CUDA(cudaMemcpy(d_B, h_B, K*N*sizeof(float), cudaMemcpyHostToDevice));
    CHECK_CUDA(cudaMemcpy(d_C, h_C, M*N*sizeof(float), cudaMemcpyHostToDevice));

    cublasHandle_t handle;
    CHECK_CUBLAS(cublasCreate(&handle));

    float alpha = 1.0f, beta = 0.0f;

    // Row-major A(M×K) * B(K×N) = C(M×N)
    // 用 column-major trick：呼叫 B * A（在 cuBLAS 視角）
    CHECK_CUBLAS(cublasSgemm(
        handle,
        CUBLAS_OP_N, CUBLAS_OP_N,
        N, M, K,       // n=N, m=M, k=K（注意順序）
        &alpha,
        d_B, N,        // B 當 A，lda=N
        d_A, K,        // A 當 B，ldb=K
        &beta,
        d_C, N         // ldc=N
    ));
    CHECK_CUDA(cudaDeviceSynchronize());

    CHECK_CUDA(cudaMemcpy(h_C, d_C, M*N*sizeof(float), cudaMemcpyDeviceToHost));
    printf("C[0][0] = %.1f  (expected = %d)\n", h_C[0], K);
    // 預期輸出：C[0][0] = 1024.0  (expected = 1024)
    // （Colab 預期，未在本機實測）

    CHECK_CUBLAS(cublasDestroy(handle));
    cudaFree(d_A); cudaFree(d_B); cudaFree(d_C);
    free(h_A); free(h_B); free(h_C);
    return 0;
}
```

---

### Tensor Core 路徑：cublasGemmEx

回連 [Ch 30 Tensor Core](./30-tensor-core.md)：Tensor Core 需要 FP16 輸入（sm_75 起，INT8 在 sm_75 也支援）。`cublasSgemm` 只走 FP32 FFMA 路徑，要啟動 Tensor Core 要用 `cublasGemmEx` 或 `cublasHgemm`。

```cpp
#include <cuda_fp16.h>

// 假設 d_A_fp16, d_B_fp16, d_C_fp32 已準備好

cublasGemmEx(
    handle,
    CUBLAS_OP_N, CUBLAS_OP_N,
    N, M, K,
    &alpha,                        // float
    d_B_fp16, CUDA_R_16F, N,      // FP16 輸入
    d_A_fp16, CUDA_R_16F, K,      // FP16 輸入
    &beta,                         // float
    d_C_fp32, CUDA_R_32F, N,      // FP32 累加輸出
    CUBLAS_COMPUTE_32F,            // 計算類型：FP32 累加（精度較高）
    CUBLAS_GEMM_DEFAULT_TENSOR_OP  // 允許 Tensor Core
);
```

`CUBLAS_GEMM_DEFAULT_TENSOR_OP` 讓 cuBLAS 自動選擇最快的 kernel，通常就是走 Tensor Core。要查看 Tensor Core 是否真的被用到，在 Nsight Compute 裡看 `sm__pipe_tensor_op_hmma_cycles_active` 計數器（文獻方法，本機未實測）。

**`CUBLAS_COMPUTE_32F_FAST_TF32`**（Ampere 及後起）：讓累加也用 TF32，比 COMPUTE_32F 快，但累加精度只有 10-bit 尾數，適合精度不敏感的推論場景。

---

### Workspace 模式

部分 cuBLAS 函式（尤其是大 GEMM）需要額外的暫存記憶體（workspace）。現代 cuBLAS（CUDA 12）用 `cublasSetWorkspace` 讓你提供一塊固定的 workspace 而非每次呼叫時動態 allocate：

```cpp
void *d_workspace;
size_t workspace_size = 128 * 1024 * 1024;   // 128 MiB（常見建議值）
cudaMalloc(&d_workspace, workspace_size);
cublasSetWorkspace(handle, d_workspace, workspace_size);
```

沒有設 workspace 時，cuBLAS 內部可能走 `cudaMalloc`，在迭代訓練迴圈裡累積延遲。設一次、用整個訓練過程，是生產程式碼的標準做法。

---

## cuDNN

cuDNN 是 NVIDIA 官方的深度學習原語庫，覆蓋：

- **卷積**（前向、反向權重、反向輸入）
- **Pooling**（max、avg）
- **Normalization**（BatchNorm、LayerNorm、Instance Norm）
- **Activation**（ReLU、sigmoid、tanh）
- **RNN/LSTM/GRU**

### Handle 與 Tensor Descriptor 模式

cuDNN 比 cuBLAS 更複雜：每個 tensor 需要一個「descriptor」說明其形狀、資料型別、記憶體佈局。

```c
#include <cudnn.h>

cudnnHandle_t cudnn_handle;
cudnnCreate(&cudnn_handle);

// 描述輸入 tensor：NCHW 格式（batch=1, channels=3, H=224, W=224）
cudnnTensorDescriptor_t input_desc;
cudnnCreateTensorDescriptor(&input_desc);
cudnnSetTensor4dDescriptor(
    input_desc,
    CUDNN_TENSOR_NCHW,   // 記憶體佈局
    CUDNN_DATA_FLOAT,    // 資料型別
    1, 3, 224, 224       // N, C, H, W
);

// 描述卷積核心：64 個 3×3 filter，輸入 3 channels
cudnnFilterDescriptor_t filter_desc;
cudnnCreateFilterDescriptor(&filter_desc);
cudnnSetFilter4dDescriptor(
    filter_desc,
    CUDNN_DATA_FLOAT,
    CUDNN_TENSOR_NCHW,
    64, 3, 3, 3          // 輸出 channels, 輸入 channels, fH, fW
);

// 描述卷積操作：padding=1, stride=1, dilation=1
cudnnConvolutionDescriptor_t conv_desc;
cudnnCreateConvolutionDescriptor(&conv_desc);
cudnnSetConvolution2dDescriptor(
    conv_desc,
    1, 1,                // padding H, padding W
    1, 1,                // stride H, stride W
    1, 1,                // dilation H, dilation W
    CUDNN_CROSS_CORRELATION,
    CUDNN_DATA_FLOAT
);
```

### 找最快演算法（autotuning）

cuDNN 對同一個卷積提供多種實作（GEMM 展開、Winograd、FFT 等），你要先「問」它哪個最快：

```c
// 找最快的前向卷積演算法
cudnnConvolutionFwdAlgoPerf_t algo_perf;
int returned_algo_count;
cudnnFindConvolutionForwardAlgorithm(
    cudnn_handle,
    input_desc, filter_desc, conv_desc, output_desc,
    1, &returned_algo_count, &algo_perf
);
cudnnConvolutionFwdAlgo_t fwd_algo = algo_perf.algo;

// 查詢需要多大 workspace
size_t workspace_bytes;
cudnnGetConvolutionForwardWorkspaceSize(
    cudnn_handle,
    input_desc, filter_desc, conv_desc, output_desc,
    fwd_algo, &workspace_bytes
);
void *d_workspace;
cudaMalloc(&d_workspace, workspace_bytes);

// 執行卷積
float alpha = 1.0f, beta = 0.0f;
cudnnConvolutionForward(
    cudnn_handle,
    &alpha,
    input_desc, d_input,
    filter_desc, d_filter,
    conv_desc, fwd_algo,
    d_workspace, workspace_bytes,
    &beta,
    output_desc, d_output
);
```

cuDNN 的 autotuning 結果因 GPU 型號和 tensor 大小而異，生產環境通常在第一次執行後快取演算法選擇，後續直接用。PyTorch 裡 `torch.backends.cudnn.benchmark = True` 做的就是這件事。

---

## CUB：設備級並行原語

CUB（CUDA UnBound）是 Thrust 的底層，現在和 Thrust 一起收進 **CCCL**（CUDA Core Compute Libraries）。CUB 提供三層級的原語：

| 層級 | 代表 API | 用途 |
|------|----------|------|
| Thread level | `cub::ThreadLoad` | 快取控制 load/store |
| Block level | `cub::BlockScan`, `cub::BlockReduce` | 整個 block 協作 |
| Device level | `cub::DeviceReduce`, `cub::DeviceSort` | 全局操作，可直接從 host 呼叫 |

### DeviceReduce：對比 Ch 22 手寫 reduction

回連 [Ch 22 atomics and reduction](./22-atomics-reduction.md)：我們手寫的 reduction kernel 需要自己處理 warp shuffle、block-level 合併、多 pass。CUB 的 `DeviceReduce` 一行搞定：

```cpp
#include <cub/cub.cuh>

int n = 1 << 24;    // 16M 個 float
float *d_in, *d_out;
cudaMalloc(&d_in,  n * sizeof(float));
cudaMalloc(&d_out, sizeof(float));
// （填資料略）

// 第一次呼叫：查詢需要多少暫存空間
void *d_temp = nullptr;
size_t temp_bytes = 0;
cub::DeviceReduce::Sum(d_temp, temp_bytes, d_in, d_out, n);

// 分配暫存空間後真正執行
cudaMalloc(&d_temp, temp_bytes);
cub::DeviceReduce::Sum(d_temp, temp_bytes, d_in, d_out, n);
cudaDeviceSynchronize();

// 結果在 d_out（device 指標）
float result;
cudaMemcpy(&result, d_out, sizeof(float), cudaMemcpyDeviceToHost);
printf("Sum = %.2f\n", result);
// （Colab 預期，未在本機實測）
```

「先查暫存空間大小、再 allocate、再執行」是 CUB 一貫的模式，幾乎所有 Device-level API 都這樣用。原因：CUB 的 reduction 是 two-pass（第一 pass 把每個 SM 的結果寫到暫存，第二 pass 合併），暫存大小取決於 n 和 GPU。

### BlockScan：block 內的 prefix sum

```cpp
__global__ void exclusive_scan_kernel(int *d_in, int *d_out, int n) {
    // BlockScan 的模板參數：資料型別、block 大小
    typedef cub::BlockScan<int, 256> BlockScan;

    // 宣告 shared memory（CUB 需要）
    __shared__ typename BlockScan::TempStorage temp_storage;

    int tid = threadIdx.x;
    int val = (tid < n) ? d_in[tid] : 0;

    int result;
    BlockScan(temp_storage).ExclusiveSum(val, result);

    if(tid < n) d_out[tid] = result;
}
```

BlockScan 在 shared memory 裡做 Kogge-Stone 或 Hillis-Steele scan，比你手寫的實作更妥善地處理 warp 邊界和 bank conflict（Ch 19）。

---

## cuSPARSE / cuFFT / cuRAND 概覽

這三個庫你不需要天天用，但要知道它們存在和大致 API 形狀。

### cuSPARSE（稀疏矩陣）

針對稀疏矩陣（大部分元素為 0）的 BLAS 操作。主要格式：
- **CSR**（Compressed Sparse Row）：最常見
- **COO**（Coordinate List）
- **BSR**（Block Sparse Row）：GNN 常用

現代 cuSPARSE（CUDA 12）的 API 入口是通用的：

```cpp
cusparseHandle_t sp_handle;
cusparseCreate(&sp_handle);

// 建立稀疏矩陣的 descriptor（以 CSR 為例）
cusparseSpMatDescr_t mat_desc;
cusparseCreateCsr(
    &mat_desc, num_rows, num_cols, nnz,
    d_csr_offsets, d_csr_cols, d_csr_values,
    CUSPARSE_INDEX_32I, CUSPARSE_INDEX_32I,
    CUSPARSE_INDEX_BASE_ZERO, CUDA_R_32F
);
```

SpMM（稀疏 × 稠密）是現代 GNN 的核心，cuSPARSE 的 SpMM 在高稀疏度下比 cuBLAS GEMM 快（理論預期，實際取決於稀疏模式）。

### cuFFT（快速傅立葉轉換）

```cpp
cufftHandle fft_plan;
cufftPlan1d(&fft_plan, N, CUFFT_C2C, batch);   // N 點複數-to-複數 FFT
cufftExecC2C(fft_plan, d_in, d_out, CUFFT_FORWARD);
cufftDestroy(fft_plan);
```

cuFFT 實作 Cooley-Tukey FFT，N 是 2 的冪最快，其他大小也支援但慢。卷積神經網路的早期推論曾走 FFT 路徑（頻域乘法比空域卷積快），但 Winograd 演算法和 Tensor Core 興起後，cuFFT 在 DL 推論中地位下降。信號處理、MRI 重建、電漿模擬仍是主戰場。

### cuRAND（GPU 亂數）

兩種使用模式：

```cpp
// 模式 A：host API，在 GPU 產生一批亂數到 device 記憶體
curandGenerator_t gen;
curandCreateGenerator(&gen, CURAND_RNG_PSEUDO_MT19937);
curandSetPseudoRandomGeneratorSeed(gen, 12345ULL);
curandGenerateUniform(gen, d_rand_array, n);   // 均勻分布 [0,1)
curandDestroyGenerator(gen);

// 模式 B：device API，在 kernel 內每個 thread 自行產生亂數
#include <curand_kernel.h>
__global__ void kernel(unsigned long long seed) {
    curandState state;
    curand_init(seed, threadIdx.x, 0, &state);   // 初始化 RNG 狀態
    float r = curand_uniform(&state);            // 產生一個 [0,1) 的 float
    // 注意：curand_init 很貴，RNG state 應存在 device 記憶體，不要每次 kernel 都初始化
}
```

模式 A 適合「一次批量生成」（蒙地卡羅、dropout mask batch）；模式 B 適合 kernel 內部動態生成（adaptive sampling）。

---

## 踩雷清單

**錯誤直覺 1：lda 是矩陣的 M（行數）。**
正確：`lda` 是記憶體中同欄相鄰兩個元素的距離（以元素計）。Column-major 下，lda 是 M（行數）；row-major 下，lda 是 N（欄數）。如果矩陣有 padding，lda 可能更大。傳錯 lda 不會報錯，但結果是錯的，而且很難 debug（數值看起來差不多對，但仔細檢查就不對）。

**錯誤直覺 2：cublasSgemm 的 (m, n, k) 順序和數學上的 M×K, K×N 一樣。**
正確：cuBLAS 的 `m` 是 op(A) 的 **行數**（row count），`n` 是 op(B) 的 **欄數**（column count）。在 column-major 視角，「行」對應記憶體的連續方向。用 row-major trick 時，你傳的 `(N, M, K)`，不是 `(M, N, K)`，因為你把 B 當 A 傳。

**錯誤直覺 3：cuBLAS handle 是 thread-safe 的，可以多 thread 共用。**
正確：同一個 handle 在同一時刻只能被一個 thread 使用。如果你有多個 CPU thread 各自呼叫 cuBLAS，每個 thread 要有自己的 handle。（CUDA stream 也是一樣的邏輯。）

**錯誤直覺 4：呼叫 cublasSgemm 後，結果立刻可用。**
正確：`cublasSgemm` 是非同步的（除非 stream 是 default stream 且你接著做 host-side 讀取），它只是把 kernel 提交到 stream。需要 `cudaStreamSynchronize(stream)` 或 `cudaDeviceSynchronize()` 後才能安全讀取結果。

**錯誤直覺 5：alpha/beta 指標必須在 host 上。**
正確：預設模式（`CUBLAS_POINTER_MODE_HOST`）下是 host 指標。如果設為 `CUBLAS_POINTER_MODE_DEVICE`，則 alpha/beta 必須是 device 指標。混用會讓數值變成垃圾——而且不會報錯，只有結果是錯的。

---

## 進階：先量測，再考慮手寫

實際工程流程應該是：

1. 用 cuBLAS / cuDNN 先跑通，量測吞吐
2. 如果吞吐已達到 Roofline 分析（Ch 25）預期，停手
3. 如果有 kernel fusion 需求（連續幾個操作要合併避免多次讀寫 global memory），才考慮手寫——參考 Ch 38–41

cuBLAS `cublasSgemm` 在 T4（sm_75，FP32）上的 FP32 峰值大約是 80–90% 的硬體上限（文獻/官方數字）。你的手寫 tiled GEMM（Ch 17）如果沒做 double buffering 和 Tensor Core，達到 cuBLAS 的 50% 已經很好。

---

## 動手練習

**Colab 執行步驟：**
1. 開 Colab，Runtime → Change runtime type → GPU (T4)
2. `!pip install nvidia-cuda-runtime-cu12` 或用 `%%cuda` magic（需 `!pip install nvcc4jupyter`）

練習 A：寫一個函式 `matmul_cublas(float* A, float* B, float* C, int M, int N, int K)`，用 row-major 輸入正確呼叫 `cublasSgemm`，並驗證 `C[i][j]` 等於手算的結果。

練習 B：改用 `cublasGemmEx`（FP16 輸入、FP32 累加），比較和 `cublasSgemm` 的結果差異（精度損失）。

練習 C：用 `cub::DeviceReduce::Sum` 對 1M 個全 1 的 float 陣列做求和，驗證結果等於 1048576。比較和 [Ch 22](./22-atomics-reduction.md) 手寫 reduction 的程式碼行數差異。

---

## 本章重點

- cuBLAS 用 column-major；row-major 的 C/C++ 呼叫者要交換 A/B 位置並調整 (N, M, K) 順序
- `lda` 是記憶體 stride（element 數），不是矩陣維度
- Tensor Core 路徑走 `cublasGemmEx` + FP16 輸入 + `CUBLAS_GEMM_DEFAULT_TENSOR_OP`
- Handle 只建一次；workspace 設一次；兩者都是 stream 綁定的
- CUB 的 Device-level API 模式：先查 temp_bytes → allocate → 執行
- cuDNN 的卷積需要先 autotuning，找最快演算法，再執行

## 自我檢核（主動回憶）

1. `cublasSgemm` 的 column-major trick：row-major `C = A*B` 如何對應到 cuBLAS 呼叫的參數？寫出 (m, n, k, A, lda, B, ldb) 的對應。
2. `cub::DeviceReduce::Sum` 為什麼要呼叫兩次？第一次做什麼？
3. cuBLAS handle 和 CUDA stream 的關係？
4. `CUBLAS_POINTER_MODE_HOST` vs `DEVICE` 差在哪？什麼情況下用 DEVICE 模式比較好？
5. cuDNN `cudnnFindConvolutionForwardAlgorithm` 的目的是什麼？PyTorch 裡哪個設定對應這個行為？

## 延伸閱讀

1. **cuBLAS 官方文件** — [docs.nvidia.com/cuda/cublas](https://docs.nvidia.com/cuda/cublas/index.html)：`cublasSgemm`、`cublasGemmEx` 的完整參數說明，特別看 Pointer Mode 和 Math Mode 章節
2. **Lei Mao's Blog: cuBLAS GEMM Column-Major and Row-Major** — [leimao.github.io](https://leimao.github.io/blog/cuBLAS-Transpose-Column-Major-Relationship/)：對 column-major trick 有最清晰的數學推導
3. **CUB 官方文件** — [nvidia.github.io/cccl/cub](https://nvidia.github.io/cccl/cub/)：Device/Block/Warp level 原語全覽，包含 BlockScan 的演算法變體
4. **cuDNN 開發者指南** — [docs.nvidia.com/deeplearning/cudnn](https://docs.nvidia.com/deeplearning/cudnn/developer-guide/index.html)：Tensor Descriptor、算法選擇、workspace 管理的完整說明
5. **NVIDIA CCCL GitHub** — [github.com/NVIDIA/cccl](https://github.com/NVIDIA/cccl)：Thrust + CUB + libcu++ 合併後的單一 repo，看 examples/ 目錄下有大量可直接跑的範例

---

掌握生態函式庫之後，下一個問題是：什麼時候用 STL 風格的高階 API 比低階 cuBLAS 更適合？

→ [Ch 33 Thrust](./33-thrust.md)
