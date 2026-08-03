# Ch 12 — 第一個 Kernel：Host/Device、Launch Config、Indexing

> **目標**：寫出你的第一個能在 GPU 上跑的函式（kernel），搞清楚 `__global__`/`__device__`/`__host__` 三個修飾詞各自的意思、用 `<<<gridDim, blockDim>>>` 啟動 kernel、用 `threadIdx`/`blockIdx` 算出 global thread index，並且理解 kernel launch 是異步的——這是最後 40 章所有 CUDA 程式的語言基礎。

> **環境**：CUDA 12.x, Colab T4 (sm_75)

---

## 為什麼要從這裡開始？

Part 2（Ch 7–11）打下了 GPU 的硬體概念：SM、warp、記憶體階層、occupancy。現在要把那些名詞對應到實際程式碼。

CUDA 的設計有一個核心思想：**host（CPU）負責指揮，device（GPU）負責執行**。你在 CPU 上的 `main()` 呼叫 CUDA API，分配 GPU 記憶體、把資料搬進去、告訴 GPU「從這個函式開始跑、用這麼多執行緒」——這個「指揮」動作就是 kernel launch。

在學任何 kernel 優化技巧（Ch 18–25）之前，你得先能把一個 kernel 跑起來。這章是地基。

---

## 先建立直覺

### CPU 函式 vs GPU Kernel

CPU 程式裡，你呼叫 `foo(array, n)`，一個函式實例跑、處理 n 個元素、回傳。

GPU 的思路是：**一份函式體、千萬個執行緒同時執行**。每個執行緒只負責一小塊工作（通常一個元素），它靠自己的 thread index 知道「我該處理第幾個」。

```
CPU 思維：
  foo(array, 1024):
    for i in 0..1024:
      array[i] = array[i] * 2

GPU 思維：
  啟動 1024 個執行緒，每個執行緒：
    i = 我的 global index
    array[i] = array[i] * 2
```

這不是魔法——每個執行緒確實有自己的 PC（program counter）、register 組，只是它們跑的是同一份 kernel 程式碼。差別在於每個執行緒的 `threadIdx.x`、`blockIdx.x` 不同，所以走到不同的資料。

### 執行緒的三層組織（快速回顧，詳細在 Ch 14）

```
Grid（整個 kernel launch）
├── Block 0
│   ├── Thread 0
│   ├── Thread 1
│   └── ... (最多 1024 個)
├── Block 1
│   ├── Thread 0
│   └── ...
└── ...
```

一個 `<<<gridDim, blockDim>>>` 啟動一個 Grid，Grid 裡有 gridDim 個 Block，每個 Block 有 blockDim 個 Thread。（這裡先用 1D，Ch 14 展開 2D/3D。）

Block 內的執行緒共享 shared memory、可以 `__syncthreads()` 同步（Ch 16/17）。不同 Block 之間沒有直接通訊機制——這是 CUDA 可以 scale 到任意 GPU 規模的設計代價，也是其優勢。

---

## 三個函式修飾詞：`__global__`、`__device__`、`__host__`

CUDA 擴展了 C++ 的語法，用三個修飾詞告訴 nvcc 這個函式在哪裡執行、從哪裡呼叫：

| 修飾詞 | 在哪裡執行 | 從哪裡呼叫 | 說明 |
|--------|-----------|-----------|------|
| `__global__` | device（GPU） | host（CPU） | kernel 入口；回傳型別必須是 `void` |
| `__device__` | device（GPU） | device（GPU） | GPU-only helper function |
| `__host__`   | host（CPU）  | host（CPU）  | 和不加修飾詞一樣，通常省略 |
| `__host__ __device__` | host 和 device 各編一份 | 兩邊都能呼叫 | 數學 utility 常用這個 |

### 規則與限制

**`__global__` kernel 的限制：**
- 回傳值必須是 `void`——輸出靠指標或全域記憶體帶回來。
- 不能是成員函式（member function）——CUDA kernel 沒有 `this` 指標。
- 遞迴受限：計算 capability < 3.5 完全不支援，3.5+ 支援但有深度限制。
- 不能有 C++ 虛函式（virtual function）。

**`__device__` 函式的限制：**
- 不能取位址（function pointer 在 device 上非常受限）。
- 會被 inline 進呼叫它的 kernel——nvcc 預設積極 inline，你幾乎不會看到 device function 的 call stack。

**`__host__ __device__` 的使用場景：**
數學輔助函式（clamp、lerp、sigmoid），或者你想在 CPU 測試又在 GPU 用的函式。nvcc 會替你產生兩份機器碼。

```cuda
// __device__ helper：只能在 GPU 呼叫
__device__ float clampf(float x, float lo, float hi) {
    return fminf(fmaxf(x, lo), hi);
}

// __global__ kernel：從 CPU launch、在 GPU 執行
__global__ void scale_kernel(float *d_out, const float *d_in, float alpha, int n) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) {
        d_out[i] = clampf(d_in[i] * alpha, 0.0f, 1.0f);
    }
}

// __host__ __device__：兩邊都能跑
__host__ __device__ float lerp(float a, float b, float t) {
    return a + t * (b - a);
}
```

---

## Kernel Launch 語法

```cuda
kernel_name<<<gridDim, blockDim, sharedMemBytes, stream>>>(arg1, arg2, ...);
```

- **gridDim**：Grid 的形狀，型別 `dim3`（最多 3D）。告訴 GPU 啟動多少個 Block。
- **blockDim**：Block 的形狀，型別 `dim3`（最多 3D，總數 ≤ 1024）。告訴 GPU 每個 Block 有多少執行緒。
- **sharedMemBytes**（可省，預設 0）：動態分配的 shared memory 大小（位元組），Ch 17 會用到。
- **stream**（可省，預設 `0` = default stream）：指定在哪個 CUDA stream 執行，Ch 23 覆蓋。

最簡單的 launch：

```cuda
my_kernel<<<4, 256>>>(d_ptr, n);
// 4 個 Block，每 Block 256 個 Thread，共 1024 個執行緒
```

### `dim3` 是什麼？

`dim3` 是 CUDA 定義的結構體，有三個欄位 `x`, `y`, `z`，預設都是 1：

```cuda
dim3 gridDim(4);        // (4, 1, 1) — 1D grid
dim3 blockDim(256);     // (256, 1, 1) — 1D block
dim3 gridDim2D(32, 16); // (32, 16, 1) — 2D grid，32*16=512 個 Block
dim3 blockDim2D(16, 16);// (16, 16, 1) — 每 Block 256 個 Thread

my_kernel<<<gridDim, blockDim>>>(args);
my_kernel2D<<<gridDim2D, blockDim2D>>>(args); // Ch 14 展開
```

直接用整數 `<<<4, 256>>>` 也行——nvcc 會隱式轉換成 `dim3(4,1,1)` 和 `dim3(256,1,1)`。

---

## 內建變數：`threadIdx`、`blockIdx`、`blockDim`、`gridDim`

每個 thread 在執行 kernel 時，自動有四個唯讀的內建變數可以用（型別都是 `dim3`，有 `.x`、`.y`、`.z`）：

| 變數 | 意義 | 值的範圍（1D 例） |
|------|------|-----------------|
| `threadIdx.x` | 在 Block 內的 thread 編號 | `[0, blockDim.x)` |
| `blockIdx.x`  | Block 的編號             | `[0, gridDim.x)` |
| `blockDim.x`  | Block 的大小             | 每個 thread 相同 |
| `gridDim.x`   | Grid 的 Block 數         | 每個 thread 相同 |

**Global thread index 公式（1D）：**

```cuda
int i = blockIdx.x * blockDim.x + threadIdx.x;
```

視覺化：假設 `blockDim.x = 4`，`gridDim.x = 3`（共 12 個 thread）：

```
Block 0             Block 1             Block 2
┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│ T0  T1  T2  T3│   │ T0  T1  T2  T3│   │ T0  T1  T2  T3│
│  0   1   2   3│   │  4   5   6   7│   │  8   9  10  11│
└──────────────┘   └──────────────┘   └──────────────┘

global i = blockIdx.x * 4 + threadIdx.x
Block 0: 0*4+0=0,  0*4+1=1,  0*4+2=2,  0*4+3=3
Block 1: 1*4+0=4,  1*4+1=5,  1*4+2=6,  1*4+3=7
Block 2: 2*4+0=8,  2*4+1=9,  2*4+2=10, 2*4+3=11
```

這個公式是 CUDA 程式裡最常出現的一行，你會在接下來 30 章反覆看到它。

---

## 核心範例：Vector Add Kernel

用 vector add（向量加法）作為第一個 kernel，因為它夠簡單（每個 thread 獨立，無 data dependency），能讓你專注在 launch 機制，而不是算法。

### 完整程式（含錯誤處理、計時）

```cuda
// 檔案：vector_add.cu
// 功能：C = A + B，用 CUDA kernel 平行計算
//
// 在 Colab 跑：
//   %%writefile vector_add.cu
//   （把這段貼進 code cell）
// 然後：
//   !nvcc -arch=sm_75 -O2 vector_add.cu -o vector_add && ./vector_add

#include <stdio.h>
#include <stdlib.h>
#include <cuda_runtime.h>

// ─── 錯誤檢查巨集 ────────────────────────────────────────────────────────────
// 每個 CUDA API 都回傳 cudaError_t；這個巨集讓錯誤立刻爆出來附上檔名和行號。
// 生產程式碼也該這樣做——不然一個 cudaMalloc 失敗，kernel 跑在 nullptr 上，
// 輸出全是垃圾、沒有任何報錯訊息，debug 起來極其痛苦。
#define CUDA_CHECK(call)                                                    \
    do {                                                                    \
        cudaError_t err = (call);                                           \
        if (err != cudaSuccess) {                                           \
            fprintf(stderr, "CUDA error at %s:%d — %s\n",                  \
                    __FILE__, __LINE__, cudaGetErrorString(err));           \
            exit(1);                                                        \
        }                                                                   \
    } while (0)

// ─── Kernel ──────────────────────────────────────────────────────────────────
// __global__：在 GPU 執行、從 CPU launch
// d_ 前綴是慣例，代表「device pointer」；h_ 代表「host pointer」
__global__ void vector_add(const float *d_a, const float *d_b,
                            float *d_c, int n) {
    // 每個 thread 算出自己的 global index
    int i = blockIdx.x * blockDim.x + threadIdx.x;

    // 邊界檢查：因為 n 不一定是 blockDim 的整數倍，
    // 最後一個 block 的部分 thread 會超出範圍。
    // 忘記這行 → 越界寫入 → undefined behavior，輸出可能看起來「幾乎對」
    // 但偶爾會踩到別人的記憶體。
    if (i < n) {
        d_c[i] = d_a[i] + d_b[i];
    }
}

// ─── Main（Host 端） ──────────────────────────────────────────────────────────
int main(void) {
    const int N = 1 << 20;            // 1M 個 float，= 4 MB
    const size_t bytes = N * sizeof(float);

    // 1. 分配 host 記憶體並初始化
    float *h_a = (float *)malloc(bytes);
    float *h_b = (float *)malloc(bytes);
    float *h_c = (float *)malloc(bytes);  // 接收結果

    for (int i = 0; i < N; i++) {
        h_a[i] = (float)i;
        h_b[i] = (float)(N - i);
    }

    // 2. 分配 device 記憶體
    float *d_a, *d_b, *d_c;
    CUDA_CHECK(cudaMalloc(&d_a, bytes));
    CUDA_CHECK(cudaMalloc(&d_b, bytes));
    CUDA_CHECK(cudaMalloc(&d_c, bytes));

    // 3. 把資料從 host 搬到 device（H2D）
    CUDA_CHECK(cudaMemcpy(d_a, h_a, bytes, cudaMemcpyHostToDevice));
    CUDA_CHECK(cudaMemcpy(d_b, h_b, bytes, cudaMemcpyHostToDevice));

    // 4. Launch kernel
    // blockDim = 256（warp size 32 的整數倍，常見的保守選擇，Ch 11 解釋為何）
    // gridDim  = ceil(N / 256)，確保所有元素都被覆蓋
    int blockSize = 256;
    int gridSize  = (N + blockSize - 1) / blockSize;  // ceiling division
    printf("Launch: grid=%d blocks, block=%d threads, total=%d threads\n",
           gridSize, blockSize, gridSize * blockSize);

    vector_add<<<gridSize, blockSize>>>(d_a, d_b, d_c, N);

    // 5. 等待 kernel 完成（kernel launch 是異步的！）
    // 省掉這行 → 可能在 kernel 還沒跑完就把結果 cudaMemcpy 回來 → 結果錯誤
    // cudaDeviceSynchronize 的開銷不小，只在需要精確計時或偵錯時呼叫
    CUDA_CHECK(cudaDeviceSynchronize());

    // kernel 的錯誤（non-synchronous error）要在 synchronize 之後才能抓到
    CUDA_CHECK(cudaGetLastError());

    // 6. 把結果搬回 host（D2H）
    CUDA_CHECK(cudaMemcpy(h_c, d_c, bytes, cudaMemcpyDeviceToHost));

    // 7. 驗證（CPU 端）
    bool correct = true;
    for (int i = 0; i < N; i++) {
        float expected = h_a[i] + h_b[i];
        if (fabsf(h_c[i] - expected) > 1e-5f) {
            printf("MISMATCH at i=%d: got %f, expected %f\n",
                   i, h_c[i], expected);
            correct = false;
            break;
        }
    }
    printf("Result: %s\n", correct ? "CORRECT" : "WRONG");

    // 8. 釋放記憶體
    free(h_a); free(h_b); free(h_c);
    CUDA_CHECK(cudaFree(d_a));
    CUDA_CHECK(cudaFree(d_b));
    CUDA_CHECK(cudaFree(d_c));

    return 0;
}
```

預期輸出（Colab T4，未在本機實測；Colab 選 GPU runtime 用 nvcc 編譯可驗證）：

```
Launch: grid=4096 blocks, block=256 threads, total=1048576 threads
Result: CORRECT
```

### 關鍵數字解析

`N = 1 << 20 = 1,048,576`，`blockSize = 256`，`gridSize = 1048576 / 256 = 4096`。T4 有 40 個 SM，每 SM 最多同時有 32 個 block（取決於資源），所以 4096 個 block 在 T4 上需要多輪排程（4096 / (40 × ~16) ≈ 6.4 輪）——這正是 GPU 的 latency hiding 設計：block 多到讓硬體永遠有工作可做。

---

## Grid-Stride Loop：處理超大資料的慣用寫法

上面的 kernel 假設「我啟動足夠的 thread 覆蓋所有元素」。但如果 N 是 1 億、10 億呢？啟動 10 億個 thread 不合理，且 gridDim.x 的上限是 2^31 - 1，但實際上你不需要——GPU 的 SM 也沒那麼多，啟動超量 thread 只是浪費。

**Grid-stride loop** 的思路：啟動固定數量的 thread（通常等於 SM 數 × 某個 block 數量的倍數），每個 thread 處理多個元素，步長等於整個 grid 的大小：

```cuda
__global__ void vector_add_stride(const float *d_a, const float *d_b,
                                   float *d_c, int n) {
    // grid stride = 整個 grid 的 thread 數 = gridDim.x * blockDim.x
    int stride = gridDim.x * blockDim.x;

    // 從 i 開始，每次跳一個 grid stride，直到超過 n
    for (int i = blockIdx.x * blockDim.x + threadIdx.x; i < n; i += stride) {
        d_c[i] = d_a[i] + d_b[i];
    }
    // 注意：迴圈本身處理了邊界，不需要額外的 if(i<n)
}

// Launch：固定 gridSize，不管 N 多大
int blockSize = 256;
int gridSize  = 128;   // 選一個「理性的」數量，例如 SM 數 * 4
// T4 有 40 SM，128 個 block 讓每個 SM 有 3.2 個 block → 不算太少
vector_add_stride<<<gridSize, blockSize>>>(d_a, d_b, d_c, N);
```

Grid-stride loop 的優點：
1. **N 可以任意大**：不受 gridDim 上限限制。
2. **更好的 cache 行為**：同一個 thread 連續處理 stride 分開的元素，在某些模式下比細粒度 launch 更有利。
3. **彈性的 occupancy 控制**：你可以精確控制 gridSize，不讓 GPU 排程器喘不過氣。
4. **標準 CUDA 慣用法**：NVIDIA 官方文件和大量 production kernel 都用這個模式。

何時用原始版（1 thread per element）、何時用 stride loop？Ch 20 會有詳細討論。這章先讓你知道兩種都存在。

---

## 底層機制：Kernel Launch 是異步的

這是初學者最常踩的坑，值得單獨說清楚。

當你執行：
```cuda
my_kernel<<<grid, block>>>(args);
printf("Kernel launched!\n");
```

`printf` 幾乎必定在 kernel 跑完之前就執行了。`<<<>>>` 語法是把 kernel 排入 GPU 的 command queue，CPU 立刻繼續往下跑（異步）。

```
時間軸：

CPU:  [main→] [cudaMemcpy H2D] [kernel<<<>>>] [printf] [cudaMemcpy D2H...]
GPU:                           [......kernel 跑..........][Done]
                                      ↑                       ↑
                               CPU 繼續往下走              CPU 到這裡才拿到正確資料
```

這個設計是刻意的——讓 CPU 可以做其他事、準備下一個 kernel、讀取先前已完成的結果，實現 CPU-GPU overlap（Ch 23 會深挖）。

### 強制同步的三種方法

```cuda
// 方法 1：等待「這個 device 上所有 stream」的所有工作完成
cudaDeviceSynchronize();

// 方法 2：等待特定 stream 完成（Ch 23 stream 章節會用到）
cudaStreamSynchronize(stream);

// 方法 3：cudaMemcpy 預設是同步的（等前面的 kernel 跑完再搬）
// 也就是說，cudaMemcpy(D2H, ...) 本身就隱含了同步
// 但這個保證只針對 default stream；有多個 stream 時要小心
cudaMemcpy(h_c, d_c, bytes, cudaMemcpyDeviceToHost);  // 隱含同步
```

### 異步 kernel 的錯誤如何捓？

`<<<>>>` launch 本身幾乎不回傳錯誤（除了 launch configuration 本身錯誤，如 blockSize > 1024）。Kernel 執行期間的錯誤（如越界存取）是 **asynchronous error**，要在下一個同步點才能抓到：

```cuda
my_kernel<<<grid, block>>>(args);
// 下面這行如果拿掉，可能看不到 kernel 的執行期錯誤
CUDA_CHECK(cudaDeviceSynchronize());
CUDA_CHECK(cudaGetLastError());  // 抓 async error
```

`cudaGetLastError()` 會清除 last error 狀態；`cudaPeekAtLastError()` 則只查不清除。

---

## 完整範例 2：帶計時的版本

實際工程中你會想知道 kernel 跑了多久。CUDA 提供兩種計時方式：

```cuda
// 方式 A：CUDA events（GPU-side，精度比 CPU timer 高，且不受 CPU-GPU 同步影響）
cudaEvent_t start, stop;
CUDA_CHECK(cudaEventCreate(&start));
CUDA_CHECK(cudaEventCreate(&stop));

CUDA_CHECK(cudaEventRecord(start));          // 在 default stream 插一個 event
vector_add<<<gridSize, blockSize>>>(d_a, d_b, d_c, N);
CUDA_CHECK(cudaEventRecord(stop));
CUDA_CHECK(cudaEventSynchronize(stop));      // 等 stop event 完成

float ms = 0.0f;
CUDA_CHECK(cudaEventElapsedTime(&ms, start, stop));
printf("Kernel time: %.3f ms\n", ms);

CUDA_CHECK(cudaEventDestroy(start));
CUDA_CHECK(cudaEventDestroy(stop));
```

```cuda
// 方式 B：CPU clock（簡單，但要先 cudaDeviceSynchronize 才準確）
#include <time.h>
struct timespec t0, t1;
clock_gettime(CLOCK_MONOTONIC, &t0);
vector_add<<<gridSize, blockSize>>>(d_a, d_b, d_c, N);
cudaDeviceSynchronize();
clock_gettime(CLOCK_MONOTONIC, &t1);
double ms = (t1.tv_sec - t0.tv_sec) * 1000.0
          + (t1.tv_nsec - t0.tv_nsec) / 1e6;
```

優先用 CUDA events——它繞過 PCIe 來回、直接在 GPU 時鐘上量，是 Nsight 也用的方式。

---

## 踩雷清單

### 雷 1：忘記 `cudaDeviceSynchronize()`，拿到空的結果

```cuda
my_kernel<<<grid, block>>>(d_out, d_in, n);
cudaMemcpy(h_out, d_out, bytes, cudaMemcpyDeviceToHost);  // ← 等等，這行安全嗎？
```

答：在 default stream 上，`cudaMemcpy` 會等前面的 kernel——所以這個特殊情況是安全的。但如果你把 kernel 放在非 default stream，`cudaMemcpy` 就不會等，你拿回的是未初始化的資料。養成好習慣：在需要結果的地方放明確的同步點。

### 雷 2：忘記邊界檢查 `if (i < n)`

```cuda
__global__ void bad_kernel(float *d_c, int n) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    // 假設 n=1000, blockSize=256, gridSize=4
    // 第 4 個 block 的 thread 16-255（index 1016-1255）都超出範圍
    d_c[i] = 0.0f;  // 越界寫入！
}
```

後果：越界寫入 GPU 記憶體，如果那塊記憶體屬於同一個 `cudaMalloc` 分配的範圍（你的 d_b 或 d_a）就會默默踩壞；如果超出整個 GPU 記憶體（不太可能，GPU 有 MMU）可能觸發硬體錯誤。兩種情況輸出都可能看起來「幾乎對」，bug 很難重現。

用 `compute-sanitizer --tool memcheck ./program`（Ch 15 詳細介紹）可以精確定位這類越界。

### 雷 3：`blockSize` 不是 warp size 的倍數

```cuda
my_kernel<<<gridSize, 100>>>(args);  // 100 不是 32 的倍數
```

每個 block 有 100 個 thread，但 warp 大小是 32，所以第 4 個 warp 只有 4 個 active thread（100 = 3×32 + 4）。剩下的 28 個 thread slot 浪費掉，佔用 register 卻不工作。通常選 128 或 256 或 512（32 的倍數，Ch 11 解釋選 256 的理由）。

### 雷 4：`blockSize` 超過 1024

```cuda
my_kernel<<<gridSize, 1025>>>(args);
// cudaLaunchKernel 回傳 cudaErrorInvalidConfiguration
// 如果你有 CUDA_CHECK 包著 <<<>>>，其實 <<<>>> 本身不直接回傳錯誤
// 要用 cudaGetLastError() 才能抓到
```

解決：用 `CUDA_CHECK(cudaGetLastError())` 在 launch 後立刻檢查。

### 雷 5：把 device pointer 傳給 host 用（或反之）

```cuda
float *d_a;
cudaMalloc(&d_a, bytes);
printf("%f\n", d_a[0]);  // Segmentation fault！d_a 是 GPU 記憶體位址
```

host 不能直接解引用 device pointer（除非 Unified Memory，Ch 13 解釋）。這個錯誤在 compute-sanitizer 下立刻報 `CUDA_ERROR_INVALID_VALUE`；沒有 sanitizer 的情況下是 segfault 或無聲的垃圾值。

---

## 進階：多個 `<<<>>>` 連發

你可以在 host 端連續 launch 多個 kernel，它們在 default stream 上是序列執行（前一個跑完才跑下一個）：

```cuda
kernel_A<<<g, b>>>(d_temp, d_in, n);   // 先跑 A
kernel_B<<<g, b>>>(d_out, d_temp, n);  // A 完成後跑 B（default stream 保證序列）
cudaDeviceSynchronize();
```

如果想讓它們並發（overlap），就要用不同的 stream——這是 Ch 23 的主題。

### Launch Configuration 的計算策略

問「我該用多大的 blockSize 和 gridSize？」是個常見問題，詳細的分析在 Ch 11（occupancy）和 Ch 20（ILP vs occupancy）。這章先給你工程上的起點：

```cuda
// CUDA 提供一個 API 讓你查詢「什麼 blockSize 能讓 occupancy 最高」
int blockSize;   // 建議的 block size
int minGridSize; // 達到最高 occupancy 所需的最小 grid size
CUDA_CHECK(cudaOccupancyMaxPotentialBlockSize(
    &minGridSize, &blockSize,
    vector_add,  // 你的 kernel 函式指標
    0,           // dynamic shared memory bytes（這裡沒有）
    0            // 最大 block size（0 = 不限制，讓 API 決定）
));
int gridSize = (N + blockSize - 1) / blockSize;
printf("Suggested: blockSize=%d, minGridSize=%d\n", blockSize, minGridSize);
```

這個 API 在 runtime 查詢你的 kernel 的 register 使用量，計算出讓 SM occupancy 最高的 block size。實際用處：你不需要手動調 blockSize，先讓這個 API 幫你定個基線，再根據 profiling 微調。

---

## 動手練習

1. **編譯跑起來**：把上面的 `vector_add.cu` 貼進 Colab，確認輸出 `CORRECT`。改 `N` 為奇數（例如 `1000003`），再確認。
2. **故意製造 bug**：
   - 把 `if (i < n)` 那行刪掉，用 `compute-sanitizer --tool memcheck ./vector_add` 跑，看它報什麼。
   - 把 `cudaDeviceSynchronize()` 刪掉，把 `cudaMemcpy(D2H)` 移到 launch 後立刻跑，多跑幾次，看結果是否穩定（在 default stream 下應該還是對的，思考為什麼）。
3. **計時**：加入 CUDA events 計時，量出 `vector_add` kernel 的時間。改變 N（1K, 100K, 1M, 10M），畫出時間 vs N 的關係。
4. **改成 grid-stride loop**：把 kernel 改成 `vector_add_stride`，固定 `gridSize=128`，跑 `N = 1 << 28`（256M），確認結果正確。

---

## 本章重點

- `__global__` 是從 CPU launch 在 GPU 執行的 kernel，回傳必須 `void`；`__device__` 是 GPU-only helper；`__host__ __device__` 兩邊都編一份。
- Launch 語法 `kernel<<<gridDim, blockDim, shmem, stream>>>(...)`，最常用的是前兩個參數。
- Global thread index 公式（1D）：`int i = blockIdx.x * blockDim.x + threadIdx.x`。
- 邊界檢查 `if (i < n)` 是必要的——`blockDim` 不一定整除 `n`。
- Kernel launch **異步**：CPU 繼續跑；需要結果前要 `cudaDeviceSynchronize()`（或 `cudaMemcpy` 在 default stream 上的隱含同步）。
- Grid-stride loop 讓一個 thread 處理多個元素，比「1 thread per element」更彈性且更標準。
- `CUDA_CHECK` 巨集是必要的工程紀律——每個 CUDA API 呼叫都要檢查。

---

## 自我檢核

- 說出 `__global__`、`__device__`、`__host__` 各自在哪裡執行、從哪裡呼叫，各有什麼限制。
- 給定 `N=1000`、`blockSize=256`，算出 `gridSize`，並說明第 4 個 block 有幾個 thread 超出範圍。
- 解釋「kernel launch 是異步的」的含義，說出兩種強制同步的方法。
- 說明為什麼 `blockSize` 要選 32 的倍數（提示：warp size）。
- Grid-stride loop 跟「1 thread per element」的 kernel 相比，在什麼情況下更合適？

---

## 延伸閱讀

1. **CUDA C++ Programming Guide — Ch 2: Programming Model**
   - 讀哪裡：[docs.nvidia.com/cuda/cuda-c-programming-guide/](https://docs.nvidia.com/cuda/cuda-c-programming-guide/#programming-model) → "Programming Model" 一節
   - 學什麼：這章是官方對 thread hierarchy（thread, block, grid, warp）和 memory hierarchy 的權威定義；本章的所有術語都來自這裡。
   - 前提：知道 C 基礎即可。

2. **Mark Harris, "An Even Easier Introduction to CUDA" (NVIDIA Developer Blog, 2017)**
   - 讀哪裡：[developer.nvidia.com/blog/even-easier-introduction-cuda/](https://developer.nvidia.com/blog/even-easier-introduction-cuda/)
   - 學什麼：和本章同一個入門例子，但用 Unified Memory（`cudaMallocManaged`）寫法，和 Ch 13 對照讀很有收穫；文章末尾有 Nsight 的入門截圖。
   - 前提：本章讀完即可。

3. **《Programming Massively Parallel Processors》(4th ed.) — Ch 2: Heterogeneous Data Parallel Computing**
   - 讀哪裡：Ch 2.1–2.5（第一個 kernel 範例到 thread execution model）
   - 學什麼：和本章幾乎相同的內容，但 Hwu/Kirk 的解說更細、有更多圖，特別是「warp 是如何從 block 映射到 SM」的說明是本書做得最好的部分。
   - 前提：同本章。

4. **CUDA C++ Programming Guide — Appendix F: C++ Language Extensions**
   - 讀哪裡：[docs.nvidia.com/cuda/cuda-c-programming-guide/#c-language-extensions](https://docs.nvidia.com/cuda/cuda-c-programming-guide/#c-language-extensions)
   - 學什麼：`__global__`、`__device__`、`__host__` 的完整限制清單（函式指標、虛函式、Lambda in kernel...），以及所有內建變數（`threadIdx`, `blockIdx`, ...）的完整說明。當你遇到「這樣寫 nvcc 幹嘛報這個錯」，這是查的地方。
   - 前提：本章讀完。

5. **cudaOccupancyMaxPotentialBlockSize API 文件**
   - 讀哪裡：[docs.nvidia.com/cuda/cuda-runtime-api/group__CUDART__HIGHLEVEL.html](https://docs.nvidia.com/cuda/cuda-runtime-api/group__CUDART__HIGHLEVEL.html)
   - 學什麼：怎麼讓 runtime 幫你決定最佳 block size；搭配 Ch 11 occupancy 概念一起看。
   - 前提：本章 + Ch 11。

---

Ch 12 把 kernel 跑起來了，但程式裡的資料是怎麼到 GPU 的、又怎麼拿回來？`cudaMalloc`/`cudaMemcpy` 背後發生什麼、為什麼這是瓶頸、有沒有更好的方法？

→ [Ch 13 記憶體管理：cudaMalloc / Unified Memory / Pinned Memory](./13-memory-management.md)
