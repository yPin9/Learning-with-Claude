# Ch 36 — 跨平台 GPU 程式設計：OpenCL、SYCL、HIP、OpenACC

> **目標**：理解四個主要的跨平台 GPU 程式設計方案（OpenCL、SYCL/oneAPI、HIP/ROCm、OpenACC）的核心設計哲學和 API 形狀；能說清楚各方案的生態現況；理解為什麼 CUDA 生態仍然主導，以及什麼情況下值得選擇可攜方案。

> **環境**：CUDA 12.x, Colab T4 (sm_75)。本章程式碼範例中，CUDA 版本為本課程主線（可在 Colab 跑）；OpenCL / SYCL / HIP 版本僅為對照說明，需要對應硬體和工具鏈（在 Colab 無法跑）。

---

## 為什麼會有這些替代方案？

CUDA 從 2007 年推出，專屬 NVIDIA GPU。如果你的程式碼依賴 CUDA，它在 AMD GPU（Radeon/Instinct）或 Intel GPU（Xe/Arc）上完全跑不了。這對以下群體是真實痛點：

- 超算中心：同時有 NVIDIA 和 AMD 節點（Frontier 超算用 AMD MI250X）
- 學術研究者：實驗室有各種 GPU，想寫一份程式碼到處跑
- 雲端用戶：AMD GPU 通常每小時更便宜，想保留切換選項
- Intel 的 Ponte Vecchio / Xe HPC：Intel 在推自己的 GPU 計算平台

這些需求催生了四個方向的可攜方案，但它們各自有不同的取捨。

---

## OpenCL：最老的開放標準，生態最弱

### 設計哲學

OpenCL（Open Computing Language）由 Khronos Group 制定，目標是「一份程式碼，跑遍所有計算設備」——CPU、GPU、FPGA、DSP 全包。這個廣度是它最大的問題：為了支援所有設備，API 設計極其通用，使用起來非常囉嗦。

### API 形狀

```c
// OpenCL 版本的 vector add（對比 CUDA 版本）
// 一個簡單的 kernel 需要：
// 1. 查詢 platform 和 device
// 2. 建立 context
// 3. 建立 command queue
// 4. 編譯 kernel source（字串！runtime JIT）
// 5. 建立 buffer
// 6. 設定 kernel 引數
// 7. enqueue kernel
// 8. 等待完成
// 9. 讀取結果
// 10. 釋放所有資源

const char *kernel_src = R"(
__kernel void vector_add(__global float *a, __global float *b, __global float *c, int n) {
    int i = get_global_id(0);
    if(i < n) c[i] = a[i] + b[i];
}
)";

cl_platform_id platform;
cl_device_id device;
cl_context context;
cl_command_queue queue;
cl_program program;
cl_kernel kernel;
cl_mem d_a, d_b, d_c;

clGetPlatformIDs(1, &platform, NULL);
clGetDeviceIDs(platform, CL_DEVICE_TYPE_GPU, 1, &device, NULL);
context = clCreateContext(NULL, 1, &device, NULL, NULL, NULL);
queue   = clCreateCommandQueue(context, device, 0, NULL);
program = clCreateProgramWithSource(context, 1, &kernel_src, NULL, NULL);
clBuildProgram(program, 1, &device, NULL, NULL, NULL);
kernel  = clCreateKernel(program, "vector_add", NULL);

d_a = clCreateBuffer(context, CL_MEM_READ_ONLY,  N*sizeof(float), NULL, NULL);
d_b = clCreateBuffer(context, CL_MEM_READ_ONLY,  N*sizeof(float), NULL, NULL);
d_c = clCreateBuffer(context, CL_MEM_WRITE_ONLY, N*sizeof(float), NULL, NULL);
clEnqueueWriteBuffer(queue, d_a, CL_TRUE, 0, N*sizeof(float), h_a, 0, NULL, NULL);
clEnqueueWriteBuffer(queue, d_b, CL_TRUE, 0, N*sizeof(float), h_b, 0, NULL, NULL);

clSetKernelArg(kernel, 0, sizeof(cl_mem), &d_a);
clSetKernelArg(kernel, 1, sizeof(cl_mem), &d_b);
clSetKernelArg(kernel, 2, sizeof(cl_mem), &d_c);
clSetKernelArg(kernel, 3, sizeof(int),    &N);

size_t global_size = N;
clEnqueueNDRangeKernel(queue, kernel, 1, NULL, &global_size, NULL, 0, NULL, NULL);
clFinish(queue);

clEnqueueReadBuffer(queue, d_c, CL_TRUE, 0, N*sizeof(float), h_c, 0, NULL, NULL);
// ... 釋放所有資源 ...
```

對比等效的 CUDA（Ch 12 第一個 kernel），CUDA 版本大約 10 行，OpenCL 版本大約 50 行。

### 為什麼大多數人不用 OpenCL

1. **Kernel 是字串**：OpenCL kernel 在 runtime 才編譯（JIT），你在程式碼裡把 kernel 寫成字串，沒有編譯期型別檢查，錯了只有跑起來才知道
2. **NVIDIA 不積極支援**：NVIDIA 的 OpenCL 驅動版本舊（停在 OpenCL 1.2 / 3.0 早期），沒有動力優化，效能落後 CUDA 30–50%（文獻數字，視 workload 而異）
3. **沒有 cuBLAS / cuDNN 等效**：OpenCL 生態沒有深度學習用的高性能函式庫
4. **樣板程式碼太多**：每個專案都要重複同樣的初始化流程

OpenCL 2023 年之後幾乎沒有新的 DL 框架採用。Khronos 自己也在推動 Vulkan Compute（更現代的 API）作為繼承者，但 Vulkan Compute 的 DL 生態同樣薄弱。

---

## SYCL：單源 C++ 的跨平台方案

### 設計哲學

SYCL 也是 Khronos 標準，但設計比 OpenCL 現代很多：**單源（single-source）C++**——host 程式碼和 kernel 程式碼寫在同一個 `.cpp` 或 `.sycl` 檔案裡，一起用標準 C++ 編譯器（搭配 SYCL 編譯器前端）處理。不再有字串 kernel。

Intel 的 **oneAPI** 是目前最活躍的 SYCL 實作，主打 Intel Xe GPU（Arc、Ponte Vecchio）。

### API 形狀

```cpp
// SYCL 版本的 vector add（用 DPC++，Intel oneAPI 的 SYCL 編譯器）
#include <CL/sycl.hpp>

int main() {
    sycl::queue q;   // 預設選擇 default device（可以是 GPU / CPU）

    float *h_a = new float[N], *h_b = new float[N], *h_c = new float[N];
    // ... 填資料 ...

    // USM（Unified Shared Memory）模式：類似 CUDA managed memory
    float *d_a = sycl::malloc_device<float>(N, q);
    float *d_b = sycl::malloc_device<float>(N, q);
    float *d_c = sycl::malloc_device<float>(N, q);

    q.memcpy(d_a, h_a, N*sizeof(float));
    q.memcpy(d_b, h_b, N*sizeof(float));
    q.wait();

    // Kernel：用 parallel_for + lambda
    q.parallel_for(sycl::range<1>(N), [=](sycl::id<1> id) {
        int i = id[0];
        d_c[i] = d_a[i] + d_b[i];
    }).wait();

    q.memcpy(h_c, d_c, N*sizeof(float));
    q.wait();

    sycl::free(d_a, q);
    sycl::free(d_b, q);
    sycl::free(d_c, q);
}
```

相比 OpenCL 乾淨很多，接近 CUDA 的風格。

### SYCL 的現況

**優點**：
- 真正的 C++ 整合，有編譯期型別檢查
- oneAPI 在 Intel GPU 上有官方最佳化支援
- AdaptiveCpp（前 hipSYCL）支援 AMD 和 NVIDIA GPU

**缺點**：
- 在 NVIDIA GPU 上，SYCL 透過 AdaptiveCpp 或 triSYCL 跑，效能不如原生 CUDA（沒有 Tensor Core 等專屬路徑）
- 深度學習函式庫生態稀薄（沒有等效的 cuDNN；Intel 的 oneDNN 在 Intel GPU 上可用）
- 工具鏈分裂：Intel DPC++、AdaptiveCpp、Codeplay ComputeCpp 各自為政

**適用場景**：你主要跑 Intel GPU（Xe HPC / Ponte Vecchio），同時想保留在 CPU 上 debug 的能力（SYCL 的 CPU device 是合法的 fallback）。

---

## HIP：AMD 的「接近一對一 CUDA 映射」

### 設計哲學

HIP（Heterogeneous-Compute Interface for Portability）是 AMD ROCm 平台的程式設計介面。它的設計目標非常明確：**API 盡量和 CUDA 相同**，讓 CUDA 程式碼以最小的修改量跑在 AMD GPU 上。

HIP 的核心詞彙和 CUDA 幾乎一比一對應：

| CUDA | HIP |
|------|-----|
| `cudaMalloc` | `hipMalloc` |
| `cudaMemcpy` | `hipMemcpy` |
| `__global__` | `__global__`（相同）|
| `threadIdx.x` | `threadIdx.x`（相同）|
| `cudaStream_t` | `hipStream_t` |
| `cublasHandle_t` | `rocblas_handle` |
| `ncclAllReduce` | `rccl_allreduce` |

### hipify：自動轉換工具

AMD 提供 `hipify` 工具，把 CUDA 程式碼自動轉換成 HIP：

```bash
# hipify-perl：把 CUDA 關鍵字替換成 HIP 對應物
hipify-perl my_cuda_kernel.cu > my_hip_kernel.hip

# hipify-clang：更聰明，能解析 C++ AST
hipify-clang my_cuda_kernel.cu -- -I/usr/local/cuda/include
```

大多數情況下，轉換後的程式碼可以直接用 `hipcc`（AMD 的編譯器）編譯。

### HIP 的雙面性：NVIDIA 也跑得了

HIP 有一個有趣的設計：在 NVIDIA GPU 上，`hipcc` 把 HIP 程式碼編譯成 CUDA（實際上就是把 `hip*` 前綴替換回 `cuda*`，然後呼叫 `nvcc`）。理論上，同一份 HIP 程式碼在 NVIDIA 和 AMD GPU 上都能跑：

```bash
# 在 AMD GPU 上編譯
hipcc -o prog prog.hip --offload-arch=gfx90a   # AMD MI250X

# 在 NVIDIA GPU 上編譯
hipcc -o prog prog.hip --offload-arch=sm_80    # A100
# hipcc 在 NVIDIA 平台上會轉換回 nvcc 路徑
```

### HIP 的侷限

1. **`hipify` 只轉換 CUDA API，不轉換 CUDA 庫**：`cuBLAS` 映射到 `rocBLAS`，但不是自動轉換的（hipify 不處理 cuDNN 到 MIOpen 的轉換）
2. **效能差異**：AMD MI250X / MI300X 的 rocBLAS 在某些 GEMM 大小上比 cuBLAS 慢（有時也更快），視 workload 而定
3. **生態落後**：ROCm 的工具鏈（profiler、debugger）比 NVIDIA 成熟度低，Nsight 沒有等效工具（有 ROCProfiler，但文件和易用性差距明顯）
4. **驅動穩定性**：在 AMD GPU 上跑深度學習仍然偶有奇怪的 bug，尤其是 ROCm 版本升級時

### 為什麼 AMD 在資料中心市場在追趕

Frontier（Oak Ridge）是全球排名前幾的超算，用 AMD MI250X，所以 HIP 在 HPC 社群有真實的使用案例。MI300X 的 HBM 容量（最大 192 GB）是 H100 SXM（80 GB）的兩倍多，吸引了部分需要超大模型的客戶。但論生態（框架、函式庫、工具），仍落後 NVIDIA。

---

## OpenACC：Pragma 導向，給科學家用的

### 設計哲學

OpenACC（Open Accelerators）採用 **pragma** 方式：在現有的 C/C++/Fortran 程式碼裡加上 `#pragma acc` 指示詞，編譯器自動產生 GPU 程式碼。目標用戶是「我有一個 Fortran 科學計算程式，我不想從頭重寫，只想讓它跑在 GPU 上」。

```c
// 原本的 C 迴圈
for(int i = 0; i < N; i++) {
    c[i] = a[i] + b[i];
}

// 加上 OpenACC pragma，讓編譯器自動 offload 到 GPU
#pragma acc kernels
for(int i = 0; i < N; i++) {
    c[i] = a[i] + b[i];
}

// 或者更細緻的控制
#pragma acc parallel loop
for(int i = 0; i < N; i++) {
    c[i] = a[i] + b[i];
}
```

### OpenACC 的現況

- **最大優點**：現有 Fortran/C 程式碼幾乎不需要修改（加幾行 pragma 就能跑 GPU）
- **NVIDIA 支援最好**：NVIDIA 的 HPC 編譯器（`nvc`, `nvc++`）對 OpenACC 支援最完整
- **適用場景**：氣象模型、流體力學模擬、海洋模型等大型 Fortran legacy 程式碼
- **侷限**：效能不如手寫 CUDA（pragma 只能表達粗粒度並行，沒辦法描述 shared memory tiling、warp-level 操作）；DL 領域幾乎不用

OpenMP 5.0 也加了 GPU offload 的 target pragma（`#pragma omp target`），和 OpenACC 類似。兩者在 HPC 社群共存，但在 DL 研究社群幾乎不存在。

---

## 各方案對比表

| | OpenCL | SYCL/oneAPI | HIP/ROCm | OpenACC | CUDA |
|--|--|--|--|--|--|
| 語言 | C（kernel 是字串）| C++（單源）| C++（接近 CUDA）| Pragma | C++（.cu）|
| 目標硬體 | 全（GPU/CPU/FPGA）| 全（重點 Intel GPU）| AMD + NVIDIA | NVIDIA 主（AMD 部分）| NVIDIA 專屬 |
| NVIDIA GPU 效能 | -30~50% | -10~30% | ≈ CUDA（透過轉換）| -20~40% | 基準（100%）|
| AMD GPU 支援 | 有 | 有 | 原生 | 有（NVIDIA 編譯器）| 無 |
| DL 函式庫生態 | 幾乎無 | 有限（oneDNN）| rocBLAS/MIOpen | 幾乎無 | cuBLAS/cuDNN/Thrust/NCCL 全套 |
| 學習曲線 | 陡（API 囉嗦）| 中（現代 C++）| 低（幾乎等於 CUDA）| 低（只加 pragma）| 中 |
| 工具鏈成熟度 | 低 | 中 | 中 | 中 | 高 |
| 適用場景 | 少數需要 FPGA/DSP | Intel GPU / 科研多平台 | AMD 超算、想保留 NVIDIA 備援 | Fortran 科學計算 legacy | 深度學習、高效能計算主流 |

---

## 為什麼 CUDA 生態仍然主導

市場現況（2025 年）：

1. **函式庫生態的飛輪**：cuBLAS、cuDNN、TensorRT、Triton 都深度整合 CUDA。PyTorch、TensorFlow、JAX 的高效能路徑都從 CUDA 開始。換到其他平台，這些函式庫要換（rocBLAS 效能接近但生態工具少，MIOpen 比 cuDNN API 介面差異大）

2. **Nsight Profiler 是業界最成熟的工具**：Nsight Systems + Nsight Compute 的功能遠超 ROCm Profiler 和 Intel VTune（GPU 部分）。搞效能最佳化時工具成熟度直接影響工程師效率

3. **Tensor Core / H100 / Hopper 架構的護城河**：FP8 計算、transformer engine、NVLink 4.0、HBM3 的組合，NVIDIA H100 在 DL training 的效能目前無替代品

4. **先發優勢和慣性**：大量 CUDA 程式碼累積了十幾年，工程師會 CUDA 是隱性技能要求，這個網絡效應自我強化

**什麼情況下值得考慮跨平台方案**：
- 你的程式碼需要在 Frontier、LUMI 等 AMD GPU 超算上跑（HIP 幾乎是必選）
- 你在 Intel Xe GPU 上開發（SYCL/oneAPI 是原生方案）
- 你的 Fortran legacy 程式碼量化很大，不想重寫（OpenACC）
- 你從事的是 GPU 無關的學術研究，需要寫一份程式碼發給不同背景的評審者跑

---

## 踩雷清單

**錯誤直覺 1：hipify 可以把 CUDA 程式碼完整轉換成在 AMD GPU 上高效跑的 HIP 程式碼。**
正確：hipify 只處理 CUDA API 名稱替換，不處理：(a) cuBLAS → rocBLAS 的 API 差異（函式簽名有細微不同）；(b) 效能調優（CUDA 的 block size / shared memory 配置對 AMD CDNA 架構不一定最優）；(c) 部分 CUDA 特性在 AMD 上沒有對應（如部分 PTX intrinsic）。hipify 給你一個「起點」，不是「終點」。

**錯誤直覺 2：SYCL 在 NVIDIA GPU 上可以直接取代 CUDA，效能相當。**
正確：SYCL 在 NVIDIA GPU 上需要透過 AdaptiveCpp 等工具轉換，效能通常落後 CUDA 10–30%（理論預期，視 workload；官方文件未提供系統性比較）。SYCL 沒有辦法直接使用 cuBLAS Tensor Core 路徑，也沒有 Nsight Compute 的最佳化指導。

**錯誤直覺 3：OpenCL 是跨平台 GPU 計算的未來。**
正確：OpenCL 的最後一個大版本 3.0 在 2020 年發布，但在 DL 社群已基本沉寂。Khronos 自己的力氣更多在 Vulkan Compute 和 SYCL 上。OpenCL 的「到處跑」優勢被 SYCL（更現代的 API）和 HIP（更好的 NVIDIA 相容性）分食。

**錯誤直覺 4：用 OpenACC 加幾個 pragma，效能就能接近手寫 CUDA。**
正確：OpenACC 的 pragma 只能描述迴圈層級的並行，編譯器沒有辦法自動發現 shared memory tiling（Ch 17）、warp shuffle（Ch 22）、bank conflict 避免（Ch 19）等優化機會。典型情況下，OpenACC 的效能是精調 CUDA kernel 的 30–60%（理論預期）。OpenACC 的價值是「以很低的代價比 CPU 快 3–10x」，不是「接近手寫 CUDA」。

**錯誤直覺 5：AMD GPU 和 NVIDIA GPU 的 warp / wavefront 大小相同。**
正確：NVIDIA GPU 的 warp size 是 32 thread。AMD GCN/CDNA 架構的 wavefront size 是 **64 thread**（RDNA 2/3 引入了 Wave32 模式，但 CDNA HPC 卡預設是 64）。CUDA 程式碼裡很多 hard-coded 的 `32`（warp shuffle mask、bank conflict 假設等）到 AMD GPU 上要仔細審查。hipify 不會幫你改這些。

---

## 進階：可攜抽象層（portability layer）

除了上述四個方案，還有些中間層工具：

**Kokkos**（Sandia National Labs）：C++ 抽象層，同一份程式碼可以跑 CUDA、HIP、OpenMP。核心是 `Kokkos::parallel_for`、`Kokkos::View`（多維陣列）。適合科學計算，DL 領域少用。

**RAJA**（Lawrence Livermore）：類似 Kokkos，更強調 memory 模型。

**ArrayFire**：C++ 函式庫，提供類似 MATLAB 的 array 操作，後端可以是 CUDA 或 OpenCL。

這些工具的共同問題：提供了語法上的可攜性，但在各個目標平台上的「效能可攜性」（performance portability）仍然難以保證。GEMM 在 NVIDIA A100 上最優的 tile 配置，在 AMD MI250X 上可能是次優的。

---

## 進階補充：HIP 的函式庫對照表

hipify 只處理 CUDA runtime / driver API，不處理高階函式庫的轉換。完整的函式庫對應如下：

| NVIDIA (CUDA) | AMD (ROCm) | 備注 |
|---------------|------------|------|
| cuBLAS | rocBLAS | GEMM 覆蓋度高，但某些特殊 op 缺失 |
| cuDNN | MIOpen | 卷積/RNN/BN 支援，但 API 差異較大，需手動移植 |
| cuSPARSE | rocSPARSE | 稀疏 BLAS，大部分 op 對應 |
| cuFFT | rocFFT | 較完整 |
| cuRAND | rocRAND | 較完整 |
| NCCL | RCCL | NCCL fork，API 幾乎相同 |
| TensorRT | — | AMD 無直接對應（有 MIGraphX，但生態差距大）|
| Nsight Systems | ROCm Profiler (rocprof) | 基本功能有，但易用性差距明顯 |
| Nsight Compute | rocm-bandwidth-test + mi profiler | 功能分散，較難用 |

**MIOpen vs cuDNN**：這是移植成本最高的部分。MIOpen 的 API 設計和 cuDNN 有結構性差異（比如 tensor descriptor 的建立方式、卷積 find algorithm 的語義），不是純粹的 rename，需要仔細對照文件。

---

## 動手練習

練習 A（Colab）：在 Colab T4 上，用 CUDA 寫 vector add，量測時間。然後想像你要把它移植到 AMD GPU：列出哪些程式碼需要修改、哪些不用修改（不需要真的跑，只需要分析）。

練習 B（思考）：閱讀 [AMD ROCm 文件](https://rocmdocs.amd.com) 的 HIP programming guide。比較 `hipBlockIdx_x` 和 `blockIdx.x` 的語義，找出至少 3 個 CUDA 和 HIP 不相容的地方（提示：wavefront size、某些 warp intrinsic 的名稱、atomic 操作的精確語義）。

練習 C（思考）：如果你的目標是「寫一份訓練 ResNet-50 的程式碼，同時在 NVIDIA A100 和 AMD MI300X 上跑，效能損失低於 20%」，你會選哪個方案？列出你的理由和風險。

練習 D（Colab）：用 SYCL/DPC++ 的 [Intel DevCloud](https://console.intel.com/devcloud/) 免費環境，跑官方的 vector add 範例，比較和 CUDA 版本的語法差異。（不需要跑完整評測，只需要編譯通過）

---

## 本章重點

- OpenCL：最老、最可攜、生態最弱；kernel 是字串，NVIDIA 支援差；DL 基本不用
- SYCL/oneAPI：單源 C++，Intel 主推，在 Intel GPU 上合理；在 NVIDIA GPU 上效能落後
- HIP/ROCm：最接近 CUDA 的 API，hipify 自動轉換，AMD 超算的事實標準；不含函式庫層的轉換
- OpenACC：Pragma 導向，適合 Fortran legacy，效能有限，幾乎不用在 DL
- CUDA 主導的原因：函式庫生態、工具成熟度、先發優勢、Tensor Core 護城河
- AMD wavefront = 64 thread（CDNA），不等於 CUDA warp = 32 thread

## 自我檢核（主動回憶）

1. 為什麼 OpenCL 的「到處跑」優勢沒有讓它成為 DL 領域的主流？至少說 3 個原因。
2. hipify 能把什麼轉換掉？不能把什麼轉換掉？
3. SYCL 的「單源」指的是什麼？相比 OpenCL 解決了什麼問題？
4. AMD CDNA 架構的 wavefront size 是多少？為什麼這對 CUDA 移植很重要？
5. 什麼場景下值得選 HIP 而不是 CUDA？

## 延伸閱讀

1. **AMD ROCm HIP 程式設計指南** — [rocmdocs.amd.com/en/latest/how-to/hip-porting-guide](https://rocmdocs.amd.com/en/latest/how-to/programming_guide.html)：官方 HIP API 說明，包含和 CUDA 的差異對照表，hipify 的使用說明
2. **Futhark Blog: Comparing OpenCL, CUDA, and HIP** — [futhark-lang.org/blog/2024-07-17-opencl-cuda-hip](https://futhark-lang.org/blog/2024-07-17-opencl-cuda-hip.html)：實測三個平台的效能差異，有實際數字，比「理論比較」更有說服力
3. **Intel oneAPI / SYCL 入門** — [intel.com/content/www/us/en/developer/articles/technical/oneapi-a-viable-alternative-to-cuda-lock-in](https://www.intel.com/content/www/us/en/developer/articles/technical/oneapi-a-viable-alternative-to-cuda-lock-in)：Intel 視角的 SYCL vs CUDA 分析，有助於理解 oneAPI 的定位
4. **GPU Compute Platforms 2026 比較** — [orchestrator.dev/blog/2026-05-24-gpu-compute-platforms-comparison](https://orchestrator.dev/blog/2026-05-24-gpu-compute-platforms-comparison)：2026 年的 CUDA vs ROCm vs Vulkan vs Metal 生態比較，反映最新現況
5. **Kokkos 文件** — [kokkos.org/kokkos-core-wiki](https://kokkos.org/kokkos-core-wiki/)：如果你需要真正的效能可攜性（不同 GPU 架構），了解 Kokkos 的抽象層設計是值得的

---

跨平台是一條艱難的路。有沒有一種方式，讓寫 GPU kernel 的門檻更低，同時不放棄效能？

## 補充：各平台的編譯器指令速查

| 平台 | 來源副檔名 | 編譯器 | 基本編譯指令 |
|------|-----------|--------|-------------|
| CUDA / NVIDIA | `.cu` | `nvcc` | `nvcc -O3 -arch=sm_75 foo.cu -o foo` |
| HIP / AMD | `.hip` 或 `.cu` | `hipcc` | `hipcc -O3 --offload-arch=gfx90a foo.hip -o foo` |
| SYCL / Intel DPC++ | `.cpp` | `dpcpp` / `icpx` | `icpx -O3 -fsycl foo.cpp -o foo` |
| OpenCL | `.cl`（kernel）+ `.c`（host）| `gcc` + OpenCL headers | `gcc foo.c -lOpenCL -o foo` |
| OpenACC / NVIDIA | `.c` | `nvc` | `nvc -O3 -acc=gpu -Minfo=accel foo.c -o foo` |

HIP 在 NVIDIA 平台上（`hipcc` 內部轉 `nvcc`）：

```bash
# 設定 HIP_PLATFORM=nvidia，讓 hipcc 用 nvcc 後端
HIP_PLATFORM=nvidia hipcc -O3 --offload-arch=sm_75 foo.hip -o foo
```

---

## 補充：Metal（Apple Silicon）和 Vulkan Compute

如果你的目標是 Apple Silicon（M-series），情況完全不同：

**Metal / Metal Performance Shaders（MPS）**：Apple 的 GPU 程式設計 API，有 PyTorch 的 `mps` device 後端。Metal Shading Language 在語法上接近 C++11，但 threading model 和 CUDA 差異大。Apple Silicon 的 GPU 是 unified memory 架構（CPU 和 GPU 共享記憶體），不需要 H2D/D2H copy，是設計上的根本差異。

**Vulkan Compute**：Khronos 的現代跨平台 API，設計目標是 graphics + compute 共存。Shader 用 SPIR-V（二進位格式），可以從 GLSL / HLSL / SLANG 編譯。Vulkan Compute 在 Android 上是唯一選擇（OpenCL 在 Android 上被 deprecate），在桌面 GPU 計算上比 OpenCL 更現代。但 DL 函式庫生態同樣稀薄，主要用在遊戲引擎和手機 ML 推論（via NNAPI / Vulkan ML）。

這個課程以 NVIDIA CUDA 為主線，Metal 和 Vulkan Compute 只提到概念層。如果你有 Apple Silicon 開發需求，Apple 的 Metal best practices 和 PyTorch MPS backend 文件是起點。

---

→ [Ch 37 Triton](./37-triton.md)
