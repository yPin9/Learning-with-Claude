# Ch 13 — 記憶體管理：cudaMalloc / Unified Memory / Pinned Memory

> **目標**：搞清楚 CUDA 的三種記憶體模型——顯式搬移（`cudaMalloc` + `cudaMemcpy`）、Unified Memory（`cudaMallocManaged`）、Pinned Memory（`cudaMallocHost`）——每種適用的場景、底層機制、以及最關鍵的效能直覺：PCIe 傳輸是真正的瓶頸，你怎麼管理記憶體直接決定程式跑多快。

> **環境**：CUDA 12.x, Colab T4 (sm_75)

---

## 為什麼記憶體管理是 CUDA 效能的核心問題？

Ch 12 的 `vector_add` 有一個我們沒深究的地方：`cudaMalloc` 和 `cudaMemcpy` 到底做了什麼？資料是怎麼到 GPU 上的？

答案是：通過 PCIe 匯流排（Peripheral Component Interconnect Express）。

**T4 的數字（來源：NVIDIA T4 Datasheet + PCIe spec）：**

| 路徑 | 頻寬 |
|------|------|
| T4 GDDR6 顯示記憶體（GPU ↔ 顯存） | 300 GB/s（實測峰值約 312 GB/s） |
| PCIe Gen 3 x16（CPU ↔ GPU） | 雙向各 ~16 GB/s（理論峰值 15.75 GB/s） |
| CPU DDR4（主記憶體頻寬，以 3200 MHz 雙通道為例） | ~50 GB/s |

**差距是 300 vs 16 = 約 19 倍。** 你的 kernel 的理論峰值是 300 GB/s，但每次你需要搬資料過去，瓶頸就從 300 變成 16。這是為什麼記憶體管理不只是「API 呼叫順序」的問題——它直接影響你程式能跑多快。

PCIe 的開銷有兩個組成：**延遲**（latency，一次傳輸約 6–10 µs，包含 driver overhead）和**頻寬**（bandwidth，16 GB/s 就是上限）。小資料傳輸被 latency 主宰；大資料傳輸被 bandwidth 主宰。兩者都是瓶頸，只是在不同的資料大小下哪個更明顯。

CUDA 提供三種不同的記憶體模型，每種都在「方便性」和「控制精度」之間做出不同的取捨：

---

## 模型一：顯式搬移（cudaMalloc + cudaMemcpy）

這是最基礎的模型，也是你理解其他兩種模型的基礎。程式設計師明確控制「什麼資料、什麼時間點、往哪個方向搬」。

### API

```cuda
// 分配 GPU 記憶體
cudaError_t cudaMalloc(void **devPtr, size_t size);

// 釋放 GPU 記憶體
cudaError_t cudaFree(void *devPtr);

// 搬移資料
cudaError_t cudaMemcpy(void *dst, const void *src,
                        size_t count, cudaMemcpyKind kind);
// kind 的四個選項：
//   cudaMemcpyHostToDevice   H2D：主記憶體 → 顯示記憶體（最常用）
//   cudaMemcpyDeviceToHost   D2H：顯示記憶體 → 主記憶體（拿回結果）
//   cudaMemcpyDeviceToDevice D2D：GPU 顯存內部搬移（不過 PCIe，走顯存頻寬）
//   cudaMemcpyHostToHost     H2H：主記憶體內部（就是 memcpy，少用）
```

`cudaMemcpy` 是**同步的**（在 default stream 上）：它會等到搬移完成才回傳。這也是為什麼在 Ch 12 的 `vector_add` 中，`cudaMemcpy(D2H)` 之前不需要額外 `cudaDeviceSynchronize()`——`cudaMemcpy` 本身已經隱含同步（它會等 default stream 上所有未完成的 kernel 跑完）。

**非同步版本**：

```cuda
// 異步版本：立刻回傳，搬移在背景進行
cudaError_t cudaMemcpyAsync(void *dst, const void *src,
                             size_t count, cudaMemcpyKind kind,
                             cudaStream_t stream);
// 必須配合 pinned memory 使用（見模型三）才有效果
// Ch 23 streams 章節詳細介紹
```

### `cudaMemcpy` 各方向的底層路徑

```
cudaMemcpyHostToDevice (H2D):
  host DRAM → (CPU PCIe controller) → PCIe 匯流排 → (GPU PCIe controller) → GPU GDDR6

cudaMemcpyDeviceToHost (D2H):
  GPU GDDR6 → (GPU PCIe controller) → PCIe 匯流排 → (CPU PCIe controller) → host DRAM

cudaMemcpyDeviceToDevice (D2D):
  GPU GDDR6 → GPU L2 cache → GPU GDDR6（全程在 GPU 上，走 300 GB/s 的頻寬）
  注意：如果是 NVLink 連接的多 GPU，D2D 可以走 NVLink（更高頻寬），Ch 34 討論
```

### 完整範例：顯式搬移的生命週期

```cuda
// 檔案：explicit_transfer.cu
// 在 Colab 跑：
//   %%writefile explicit_transfer.cu
//   !nvcc -arch=sm_75 -O2 explicit_transfer.cu -o explicit_transfer && ./explicit_transfer

#include <stdio.h>
#include <cuda_runtime.h>

#define CUDA_CHECK(call)                                                    \
    do {                                                                    \
        cudaError_t err = (call);                                           \
        if (err != cudaSuccess) {                                           \
            fprintf(stderr, "CUDA error at %s:%d — %s\n",                  \
                    __FILE__, __LINE__, cudaGetErrorString(err));           \
            exit(1);                                                        \
        }                                                                   \
    } while (0)

__global__ void scale(float *data, float factor, int n) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) data[i] *= factor;
}

int main(void) {
    const int N     = 1 << 22;  // 4M floats = 16 MB
    const size_t sz = N * sizeof(float);

    // ① 分配 host 記憶體（普通的 malloc）
    float *h_data = (float *)malloc(sz);
    for (int i = 0; i < N; i++) h_data[i] = (float)i;

    // ② 分配 device 記憶體
    float *d_data;
    CUDA_CHECK(cudaMalloc(&d_data, sz));

    // ③ H2D 搬移（計時）
    cudaEvent_t t0, t1;
    CUDA_CHECK(cudaEventCreate(&t0));
    CUDA_CHECK(cudaEventCreate(&t1));

    CUDA_CHECK(cudaEventRecord(t0));
    CUDA_CHECK(cudaMemcpy(d_data, h_data, sz, cudaMemcpyHostToDevice));
    CUDA_CHECK(cudaEventRecord(t1));
    CUDA_CHECK(cudaEventSynchronize(t1));
    float h2d_ms;
    CUDA_CHECK(cudaEventElapsedTime(&h2d_ms, t0, t1));

    // ④ 跑 kernel
    int blockSize = 256;
    int gridSize  = (N + blockSize - 1) / blockSize;
    CUDA_CHECK(cudaEventRecord(t0));
    scale<<<gridSize, blockSize>>>(d_data, 2.0f, N);
    CUDA_CHECK(cudaEventRecord(t1));
    CUDA_CHECK(cudaEventSynchronize(t1));
    float kernel_ms;
    CUDA_CHECK(cudaEventElapsedTime(&kernel_ms, t0, t1));

    // ⑤ D2H 搬移
    CUDA_CHECK(cudaEventRecord(t0));
    CUDA_CHECK(cudaMemcpy(h_data, d_data, sz, cudaMemcpyDeviceToHost));
    CUDA_CHECK(cudaEventRecord(t1));
    CUDA_CHECK(cudaEventSynchronize(t1));
    float d2h_ms;
    CUDA_CHECK(cudaEventElapsedTime(&d2h_ms, t0, t1));

    printf("Data: %.1f MB\n", sz / 1e6);
    printf("H2D:    %6.2f ms  →  %5.1f GB/s\n", h2d_ms, sz / h2d_ms / 1e6);
    printf("kernel: %6.2f ms  →  %5.1f GB/s (peak memory BW)\n",
           kernel_ms, 2.0 * sz / kernel_ms / 1e6);  // 讀 + 寫
    printf("D2H:    %6.2f ms  →  %5.1f GB/s\n", d2h_ms, sz / d2h_ms / 1e6);

    CUDA_CHECK(cudaEventDestroy(t0));
    CUDA_CHECK(cudaEventDestroy(t1));
    free(h_data);
    CUDA_CHECK(cudaFree(d_data));
    return 0;
}
```

預期輸出（Colab T4，未在本機實測；Colab 選 GPU runtime 用 nvcc 編譯可驗證）：

```
Data: 16.8 MB
H2D:      1.12 ms  →   14.9 GB/s
kernel:   0.04 ms  →  920.1 GB/s
D2H:      1.18 ms  →   14.2 GB/s
```

這組數字把問題說得很清楚：**kernel 跑了 0.04 ms，資料搬移花了 2.3 ms**——傳輸時間是計算時間的 57 倍。對一個記憶體密集型的 kernel 來說，你的效能優化預算有 95% 要花在「減少搬移次數」或「讓搬移和計算 overlap」，而不是優化 kernel 本身。

---

## 模型二：Unified Memory（統一記憶體）

### 動機：顯式搬移很煩

顯式搬移的缺點是你要自己管理兩套指標（`h_data` 和 `d_data`），記得在正確時機搬正確的資料。對複雜的資料結構（linked list、tree、C++ class with pointers）這幾乎不可能做得好——你需要深拷貝整個指標圖。

Unified Memory 的想法：**host 和 device 共用一個指標，誰需要這個資料，硬體/驅動就自動把它搬過去**。

### API

```cuda
// 分配 Unified Memory：回傳一個 host 和 device 都可以存取的指標
cudaError_t cudaMallocManaged(void **devPtr, size_t size,
                               unsigned int flags = cudaMemAttachGlobal);

// 釋放（和 cudaFree 一樣）
cudaError_t cudaFree(void *devPtr);

// 讓驅動預先把資料遷移到指定 device，避免 page fault 造成的 latency
cudaError_t cudaMemPrefetchAsync(const void *devPtr, size_t count,
                                  int dstDevice,     // 目標設備，cudaCpuDeviceId 代表 CPU
                                  cudaStream_t stream = 0);
```

### 底層機制：Page Migration 與 Page Fault

Unified Memory 使用 **CUDA 的頁面遷移引擎（Page Migration Engine）**，這個機制在 Pascal（GP100, sm_60）架構之後才完整支援；在更早的架構（如 Kepler/Maxwell）上是假的 UM——每次 kernel launch 前驅動就把所有 UM 全搬到 GPU，kernel 結束後全搬回來，沒有按需分配。

**現代（Pascal+）的真實 UM 流程：**

```
初始狀態：
  頁面住在 CPU DRAM，GPU 的 page table 有對映但標記為 invalid

GPU kernel 存取這個頁面：
  ↓ GPU MMU 觸發 page fault（GPU-side page fault）
  ↓ GPU Page Fault Handler 通知 CPU 驅動
  ↓ CPU 驅動把這個 4KB 頁面透過 PCIe 搬到 GPU 顯存
  ↓ 更新 GPU page table：標記為 valid
  ↓ 重新執行觸發 fault 的記憶體指令

GPU 跑完，CPU 存取同一頁面：
  ↓ CPU 的 page table 現在標記為 invalid（因為頁面搬到 GPU 了）
  ↓ CPU MMU 觸發 page fault
  ↓ CPU 驅動把頁面搬回 CPU DRAM
  ↓ 更新兩邊 page table
```

**T4 的架構**：T4 是 Turing（sm_75），支援完整的 page fault on the GPU 側。一個 page fault 的服務時間約 10–50 µs——比 PCIe 搬 4 KB 資料的延遲（~6 µs）高出許多，因為還有驅動和 OS 介入的 overhead。

### 範例：用 UM 改寫 vector add

```cuda
// 檔案：unified_memory.cu
// 在 Colab 跑：
//   %%writefile unified_memory.cu
//   !nvcc -arch=sm_75 -O2 unified_memory.cu -o unified_memory && ./unified_memory

#include <stdio.h>
#include <cuda_runtime.h>

#define CUDA_CHECK(call) \
    do { cudaError_t e=(call); if(e!=cudaSuccess){ \
         fprintf(stderr,"CUDA error %s:%d %s\n",__FILE__,__LINE__,cudaGetErrorString(e)); \
         exit(1); } } while(0)

__global__ void vector_add(float *a, float *b, float *c, int n) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) c[i] = a[i] + b[i];
}

int main(void) {
    const int N     = 1 << 20;
    const size_t sz = N * sizeof(float);

    // UM：一個指標，host 和 device 都可以用
    float *a, *b, *c;
    CUDA_CHECK(cudaMallocManaged(&a, sz));
    CUDA_CHECK(cudaMallocManaged(&b, sz));
    CUDA_CHECK(cudaMallocManaged(&c, sz));

    // CPU 直接初始化（不需要 h_ 版本）
    for (int i = 0; i < N; i++) {
        a[i] = 1.0f;
        b[i] = 2.0f;
    }

    // Prefetch：主動把資料搬到 GPU，避免 kernel 執行時的 page fault
    // 如果省略這步，kernel 會用 page fault 按需遷移，每個 fault 約 10-50 µs
    int device;
    CUDA_CHECK(cudaGetDevice(&device));
    CUDA_CHECK(cudaMemPrefetchAsync(a, sz, device));
    CUDA_CHECK(cudaMemPrefetchAsync(b, sz, device));
    CUDA_CHECK(cudaMemPrefetchAsync(c, sz, device));

    int blockSize = 256;
    int gridSize  = (N + blockSize - 1) / blockSize;
    vector_add<<<gridSize, blockSize>>>(a, b, c, N);
    CUDA_CHECK(cudaDeviceSynchronize());

    // Prefetch 結果回 CPU，CPU 直接存取
    CUDA_CHECK(cudaMemPrefetchAsync(c, sz, cudaCpuDeviceId));
    CUDA_CHECK(cudaDeviceSynchronize());  // 等 prefetch 完成

    // CPU 直接讀結果，不需要 cudaMemcpy
    bool correct = true;
    for (int i = 0; i < N && correct; i++) {
        if (fabsf(c[i] - 3.0f) > 1e-5f) { correct = false; }
    }
    printf("Result: %s\n", correct ? "CORRECT" : "WRONG");

    CUDA_CHECK(cudaFree(a));
    CUDA_CHECK(cudaFree(b));
    CUDA_CHECK(cudaFree(c));
    return 0;
}
```

預期輸出（Colab T4，未在本機實測；Colab 選 GPU runtime 用 nvcc 編譯可驗證）：

```
Result: CORRECT
```

### 沒有 Prefetch 的後果

把 `cudaMemPrefetchAsync` 三行刪掉，程式仍然正確，但效能會掉。Kernel 執行時，GPU 存取到還在 CPU 的頁面，每個頁面都觸發一次 page fault，每次 fault 的開銷是正常 PCIe 傳輸的 3–10 倍。

用 Nsight Systems（Ch 25）分析時，你會看到 kernel 執行時間中有大量的 `HtoD Page Faults` 事件。這是 UM 在沒有 prefetch 時的代價。

### UM 適用場景

- **快速原型**：不想寫兩套指標，先把邏輯跑對，之後再改成顯式搬移。
- **複雜資料結構**：帶指標的 struct、C++ 物件，顯式搬移幾乎做不到。
- **已知存取模式 + Prefetch**：如果你知道 kernel 要用哪些資料，配合 `cudaMemPrefetchAsync` 可以接近顯式搬移的效能。

UM 不適合的場景：
- **效能敏感的生產程式碼**：page fault overhead 很難完全避免，除非你精確 prefetch 所有資料。
- **多 GPU 場景**：多 GPU 的 UM 行為更複雜，Ch 34 討論。

---

## 模型三：Pinned（Page-Locked）Memory

### 為什麼 cudaMemcpy 有時很慢？

普通的 `malloc` 分配的記憶體是可分頁的（pageable）。OS 可以隨時把這個記憶體的物理頁面換出到磁碟（swap）。當 CUDA 嘗試透過 PCIe 搬這段記憶體到 GPU 時，它需要先確保這段記憶體在搬移過程中不會被 OS 換出——所以 CUDA 驅動會在搬移前把資料複製到一塊臨時的 pinned 緩衝區，再從那裡搬到 GPU。

```
普通（pageable）記憶體的 H2D 搬移路徑：
  h_data（pageable, DRAM 某處）
    → [CPU memcpy] → 驅動分配的 pinned 臨時緩衝區（固定在 DRAM）
    → [DMA, PCIe] → GPU GDDR6

多一次 CPU memcpy！
```

**Pinned（page-locked）memory** 是直接分配在物理上鎖定的記憶體——OS 不會換出，DMA 可以直接存取：

```
Pinned 記憶體的 H2D 搬移路徑：
  h_data（pinned, DRAM 固定位置）
    → [DMA, PCIe] → GPU GDDR6

少一次 memcpy，PCIe 傳輸效率更高（接近理論值）
```

### API

```cuda
// 分配 pinned（page-locked）host 記憶體
cudaError_t cudaMallocHost(void **ptr, size_t size);
// 等價寫法（老 API）：cudaHostAlloc(ptr, size, cudaHostAllocDefault);

// 釋放 pinned 記憶體（不能用 free()！）
cudaError_t cudaFreeHost(void *ptr);
```

### 效能差距

```cuda
// 在 Colab T4 上量測（16 MB 資料，預期輸出，未在本機實測）：
// Pageable H2D：   ~12 GB/s（受限於 CPU memcpy 到 staging buffer 的頻寬）
// Pinned H2D：     ~15 GB/s（接近 PCIe Gen 3 x16 的理論上限 15.75 GB/s）
// 提升約 25%
```

提升幅度取決於 CPU 記憶體頻寬（越高則 staging 成本越低），以及 PCIe 連線的實際規格。

### 真正重要的是：允許異步傳輸

Pinned memory 還有一個更重要的用途：它是 `cudaMemcpyAsync` 真正能異步工作的前提。

```cuda
// 這樣寫不是真的異步（pageable memory 的 cudaMemcpyAsync 會退化成同步）：
float *h_data = (float *)malloc(sz);
cudaMemcpyAsync(d_data, h_data, sz, cudaMemcpyHostToDevice, stream);
// 上面這行實際上是同步的！CUDA 驅動看到 pageable 指標，沒辦法讓 DMA 異步跑

// 這樣才是真正的異步：
float *h_pinned;
cudaMallocHost(&h_pinned, sz);
cudaMemcpyAsync(d_data, h_pinned, sz, cudaMemcpyHostToDevice, stream);
// DMA 引擎可以在背景跑，CPU 立刻回傳，可以做其他事
```

異步傳輸 + 多 stream 讓你可以把「資料傳輸」和「kernel 計算」做 overlap：一批資料在傳輸的同時，GPU 在跑另一批資料的 kernel。這是 Ch 23 的核心技巧。

### Pinned Memory 的代價

- **OS 記憶體壓力**：pinned 記憶體不能被換出，佔用物理記憶體。如果分配太多，OS 沒有記憶體換出策略，整體系統效能下降。
- **分配和釋放較慢**：`cudaMallocHost` 比 `malloc` 慢，因為它需要和 OS 協調鎖定頁面。避免在 hot path 上反覆 alloc/free。
- **不是每個場合都需要**：同步的 `cudaMemcpy`（非 async）從普通 malloc 記憶體也能跑，只是比 pinned 稍慢一點。只有在需要 async overlap 的情況下，pinned 才是必要的。

---

## 三種模型對比

```
記憶體管理三角形：方便性 × 控制精度 × 效能

          方便性
             ▲
             │   Unified Memory
             │   (cudaMallocManaged)
             │
             │
方便性↑      │──────────────────────────────►
效能↑        │                              效能
控制↑        │   Explicit             Pinned
             │   (cudaMalloc+       (cudaMallocHost)
             │    cudaMemcpy)
             │
             ▼
          控制精度
```

| 特性 | Explicit (`cudaMalloc`) | Unified (`cudaMallocManaged`) | Pinned (`cudaMallocHost`) |
|------|------------------------|-------------------------------|---------------------------|
| **程式複雜度** | 高（雙指標） | 低（單指標） | 中（只是 host 端的分配方式） |
| **傳輸時機控制** | 完全由程式設計師決定 | 自動（按需 page fault）或 prefetch | 手動（配合 cudaMemcpyAsync） |
| **異步傳輸支援** | 需要搭配 pinned memory | 部分支援（prefetch 是 async） | 完整支援 |
| **複雜資料結構** | 困難（需要深拷貝） | 原生支援 | 不相關（只是 host 端） |
| **效能（大資料）** | 最高（可精確控制） | 可能有 page fault overhead | 接近理論 PCIe 上限 |
| **記憶體壓力** | GPU 顯存 + CPU DRAM 各一份 | 實際上只有一份（遷移中） | 鎖定 CPU 物理記憶體 |
| **適用場景** | 效能敏感、生產程式碼 | 原型、複雜資料結構 | 配合 async 做 CPU-GPU overlap |

---

## 底層更深一層：`cudaMalloc` 分配了什麼？

`cudaMalloc` 分配的是 **GPU 顯示記憶體（video memory, VRAM）**，也就是 T4 上的 16 GB GDDR6。這塊記憶體只有 GPU 的 SM 能直接存取（透過 global memory 路徑）。

有一個你可能沒注意到的細節：`cudaMalloc` 回傳的指標是 **CUDA virtual address**，不是真正的 GPU 物理位址。CUDA 有自己的虛擬記憶體管理層（CUDA VMM），讓你在不同的 allocator API（`cudaMalloc`、`cuMemCreate`/`cuMemMap`、unified memory）之間有統一的位址空間。

CUDA 12.x 引入了更底層的 Virtual Memory Management API（`cuMemCreate`、`cuMemMap`、`cuMemAddressReserve`），允許你做「分配一次物理記憶體、map 到多個虛擬位址」這類操作，對大型語言模型 KV cache 的動態管理很有用。這個 API 超出本章範圍，但你知道有這層抽象存在。

---

## 踩雷清單

### 雷 1：忘記 `cudaMemcpy H2D`，kernel 用的是未初始化的 GPU 記憶體

```cuda
float *d_a;
CUDA_CHECK(cudaMalloc(&d_a, sz));
// 忘記 cudaMemcpy(d_a, h_a, sz, cudaMemcpyHostToDevice);
my_kernel<<<g, b>>>(d_a, n);
// d_a 的內容是 0 或垃圾值（cudaMalloc 不保證清零）
```

`cudaMalloc` 不清零記憶體（和 `malloc` 一樣）。如果你需要清零，用 `cudaMemset(d_a, 0, sz)`。或者忘記 H2D 的後果——kernel 用的是 GPU 記憶體的初始值（通常是 0 或上次使用的殘留值），輸出是錯的但不一定立刻崩潰，特別難 debug。

### 雷 2：用 `free()` 釋放 `cudaMallocHost` 分配的記憶體

```cuda
float *h_pinned;
cudaMallocHost(&h_pinned, sz);
free(h_pinned);  // 錯！應該用 cudaFreeHost
```

`free()` 不知道這塊記憶體是 pinned 的，不會通知 OS 解鎖頁面，導致記憶體洩漏甚至更嚴重的系統行為。一定要用 `cudaFreeHost`。

### 雷 3：分配過多 Pinned Memory 導致系統不穩定

```cuda
// 想要「所有 host 記憶體都用 pinned，傳輸最快」
// → 別這樣做
float *huge_pinned;
cudaMallocHost(&huge_pinned, 64ULL * 1024 * 1024 * 1024);  // 64 GB？
// 如果你的系統只有 32 GB RAM，這會讓 OS 完全沒有可換出的頁面
// 系統會開始 OOM kill 進程，或整個卡死
```

Pinned memory 是稀有資源。實際工程中，通常只對「傳輸緩衝區」使用 pinned，而不是所有 host 記憶體。一個合理的 pinned 緩衝區大小是幾十到幾百 MB，不是 GB 級別。

### 雷 4：在 UM 程式中忘記 `cudaDeviceSynchronize()` 就在 CPU 讀結果

```cuda
cudaMallocManaged(&c, sz);
vector_add<<<g, b>>>(a, b, c, N);
// 忘記同步
printf("%f\n", c[0]);  // kernel 可能還沒跑完，c[0] 是初始化的值
```

UM 指標的 CPU 存取不會自動等 kernel——你還是需要 `cudaDeviceSynchronize()` 或 prefetch 回 CPU（`cudaMemPrefetchAsync(c, sz, cudaCpuDeviceId)` + 同步）。

### 雷 5：`cudaMemcpyAsync` + pageable memory，以為是異步

```cuda
float *h_data = (float *)malloc(sz);  // pageable！
cudaMemcpyAsync(d_data, h_data, sz, cudaMemcpyHostToDevice, stream);
// 這實際上是同步的——CUDA 驅動發現 pageable，退化成同步 copy
// 你的 overlap 設計完全失效，但程式不報錯，只是慢
```

用 `cudaPointerGetAttributes()` 可以檢查一個指標是否是 pinned：

```cuda
cudaPointerAttributes attr;
cudaPointerGetAttributes(&attr, h_data);
// attr.type == cudaMemoryTypeHost → regular malloc（pageable）
// attr.type == cudaMemoryTypeUnregistered → 不屬於 CUDA 管理的記憶體
```

---

## 進階：`cudaHostRegister` — 把現有 Pageable Memory 變成 Pinned

如果你有一塊 `malloc` 分配的記憶體，不想重新分配，可以用 `cudaHostRegister` 把它 pin 住：

```cuda
void *ptr = malloc(sz);
// 把這塊 pageable 記憶體鎖定（pin）
CUDA_CHECK(cudaHostRegister(ptr, sz, cudaHostRegisterPortable));
// 現在 cudaMemcpyAsync 對這個 ptr 可以真正異步

// 解除鎖定（必須在 free 之前）
CUDA_CHECK(cudaHostUnregister(ptr));
free(ptr);
```

`cudaHostRegisterPortable` 讓這塊 pinned memory 對所有 CUDA context（包含多 GPU）都可見。`cudaHostRegisterMapped` 則讓這塊記憶體可以直接被 GPU kernel 存取（零拷貝存取，效能取決於 PCIe 頻寬，通常比搬到顯存慢，但可以省去搬移步驟，適合只讀一次的資料）。

---

## 動手練習

1. **量化傳輸頻寬**：在 Colab 跑 `explicit_transfer.cu`，改變 `N`（1 KB、1 MB、16 MB、256 MB），記錄 H2D/D2H 頻寬，畫出「傳輸大小 vs 頻寬」曲線。觀察小資料時頻寬很低（latency dominated）和大資料時接近平台值（bandwidth dominated）的轉折點在哪裡。

2. **UM vs 顯式搬移的速度對比**：寫一個測試，相同的 N 和 kernel，比較有/無 `cudaMemPrefetchAsync` 的 UM 和顯式搬移的總時間（傳輸 + kernel）。

3. **Pinned vs Pageable 的 H2D 頻寬差距**：分別用 `malloc` 和 `cudaMallocHost` 分配 host 端緩衝區，各做 10 次 `cudaMemcpy H2D`，取平均值。驗證 pinned 版確實更快。

4. **UM 的 page fault 觀察**：把 `unified_memory.cu` 的所有 `cudaMemPrefetchAsync` 刪掉，用 Nsight Systems（`nsys profile ./unified_memory`）跑，在 timeline 上找 "Migration HtoD" 事件，觀察它分散在 kernel 執行期間。

---

## 本章重點

- GPU 記憶體（300 GB/s）和 PCIe（16 GB/s）之間存在 19 倍的頻寬差距，這是 CUDA 程式設計的核心效能問題。
- **顯式搬移**（`cudaMalloc` + `cudaMemcpy`）：最高控制精度，適合效能敏感的生產程式碼。每次搬移都過 PCIe，合理的設計是「搬一次、算多次」（減少傳輸次數）。
- **Unified Memory**（`cudaMallocManaged`）：單一指標，page fault 自動遷移。開發方便，但不搭配 `cudaMemPrefetchAsync` 時有 page fault overhead。
- **Pinned Memory**（`cudaMallocHost`）：鎖定物理頁面，允許 DMA 直接存取，比 pageable 快 ~25%，且是 `cudaMemcpyAsync` 真正異步的前提。
- `cudaMemcpyAsync` + pageable memory 不是真的異步——必須搭配 pinned memory 才有效。
- 所有 CUDA API 呼叫都要 `CUDA_CHECK`；`cudaMallocHost` 分配的記憶體要用 `cudaFreeHost` 釋放。

---

## 自我檢核

- T4 的 GPU 顯存頻寬是多少？PCIe Gen 3 x16 的頻寬是多少？兩者差幾倍？
- 說明 Unified Memory 的 page fault 流程（GPU 存取尚在 CPU 的頁面時發生什麼）。
- `cudaMemcpyAsync` 在什麼條件下才是真正異步的？
- Pinned memory 有什麼副作用？為什麼不把所有 host 記憶體都 pin 住？
- 一個程式要對同一份資料跑 100 次 kernel，應該用哪種記憶體模型？（提示：只搬一次 H2D 和 D2H）

---

## 延伸閱讀

1. **CUDA C++ Programming Guide — Ch 3.2: CUDA Runtime — Memory Management**
   - 讀哪裡：[docs.nvidia.com/cuda/cuda-c-programming-guide/#memory-management](https://docs.nvidia.com/cuda/cuda-c-programming-guide/#memory-management)
   - 學什麼：所有記憶體 API 的完整說明，包含 virtual memory management（`cuMemCreate` 等）和 pool allocator（`cudaMallocAsync`）——CUDA 12 的新特性，用 memory pool 降低 alloc 開銷。
   - 前提：本章讀完。

2. **Mark Harris, "Unified Memory for CUDA Beginners" (NVIDIA Developer Blog, 2017)**
   - 讀哪裡：[developer.nvidia.com/blog/unified-memory-cuda-beginners/](https://developer.nvidia.com/blog/unified-memory-cuda-beginners/)
   - 學什麼：UM 的設計動機、page migration 的圖解說明、prefetch 和 `cudaMemAdvise` 的效能技巧（`cudaMemAdvise` 是 prefetch 之外的另一個 hint 機制，本章沒涵蓋）。
   - 前提：本章讀完。

3. **CUDA C++ Best Practices Guide — Ch 9: Memory Optimizations**
   - 讀哪裡：[docs.nvidia.com/cuda/cuda-c-best-practices-guide/#memory-optimizations](https://docs.nvidia.com/cuda/cuda-c-best-practices-guide/#memory-optimizations)
   - 學什麼：「最小化 host-device 資料傳輸」「batch 小量傳輸成一次大量傳輸」「用 pinned memory 做 overlap」這些工程建議的詳細說明，附帶 profiling 數字。搭配 Ch 23（streams）和 Ch 25（profiling）的預備知識效果最好。
   - 前提：本章 + Ch 23（可以先讀本章，回頭看 Ch 23 後再讀這份）。

4. **NVIDIA T4 Datasheet**
   - 讀哪裡：[images.nvidia.com/content/technologies/volta/pdf/turing-t4-datasheet.pdf](https://images.nvidia.com/content/technologies/volta/pdf/turing-t4-datasheet.pdf)（或搜尋 "NVIDIA T4 datasheet"）
   - 學什麼：T4 的記憶體規格（16 GB GDDR6、300 GB/s）、PCIe Gen 3 x16 規格、功耗。讓本章引用的數字有來源可查。
   - 前提：隨時可讀。

5. **《Programming Massively Parallel Processors》(4th ed.) — Ch 3: Scalable Parallel Execution + Ch 4: Memory and Data Locality**
   - 讀哪裡：Ch 3.1（cuda memory model overview），Ch 4.1–4.3（global memory, coalescence 初探）
   - 學什麼：PMPP 對 global memory 的完整介紹，包含 memory coalescing 的圖解（Ch 18 的前導）；比 CUDA Guide 的說明更有教學感。
   - 前提：本章讀完，Ch 14 前後讀均可。

---

資料搬移的問題解決了，但你的 kernel 怎麼知道自己該處理哪個元素？1D 陣列很直覺，但實際工作中大量是 2D 矩陣、3D 體素、影像——thread index 的映射變複雜了。

→ [Ch 14 Thread 階層與索引映射：1D/2D/3D Grid](./14-thread-indexing.md)
