# Ch 9 — 記憶體階層

> **目標**：完整掌握 CUDA 記憶體階層的六種記憶體類型，理解各層的物理位置、延遲、頻寬、scope 與生命週期，能在撰寫 kernel 時做出正確的記憶體選擇，並避免 register spilling 等隱性效能殺手。
> **環境**：T4（Turing, sm_75），CUDA 12.x，Ubuntu 22.04 / WSL2。硬體數字皆以 T4 為基準，其他架構可能不同。

---

## 為什麼這章不能跳過

很多人寫了幾個月 CUDA 之後，效能始終卡在某個瓶頸，用 `nvprof` 一看，Global Memory 存取佔了 80% 的執行時間。他們已經讀過 warp、block、thread，但沒有人告訴他們：**GPU 的速度上限，幾乎完全由你選用哪一層記憶體決定。**

記憶體延遲差距在 GPU 上比 CPU 更極端——register 是 1 cycle，global memory 是 400–800 cycles，差了三個數量級。這不是調優細節，是架構設計的核心。理解記憶體階層，就是理解為什麼 CUDA 程式快或慢的根本原因。

---

## 先建立直覺：記憶體金字塔

在看任何細節之前，先把這張圖刻進腦子裡：

```
          ┌─────────────────────────────────────────────────────┐
          │                   Register File                     │
          │  大小: 64K × 32-bit / SM (256 KB/SM)               │
          │  延遲: ~1 cycle                                     │
          │  頻寬: 極高（off-chip 不需搬運）                    │
          │  Scope: 單一 thread                                 │
          │  生命週期: kernel 執行中（thread 存活期間）         │
          └─────────────────────┬───────────────────────────────┘
                                │
          ┌─────────────────────▼───────────────────────────────┐
          │          Shared Memory / L1 Unified Cache           │
          │  大小: 96 KB / SM（shared + L1 共用）               │
          │  延遲: ~20–30 cycles                                │
          │  頻寬: ~TB/s 量級（on-chip）                        │
          │  Scope: 同一 block 內所有 thread                    │
          │  生命週期: block 存活期間                           │
          │  可配置: 64KB shared + 32KB L1 或 32KB + 64KB       │
          └─────────────────────┬───────────────────────────────┘
                                │
          ┌─────────────────────▼───────────────────────────────┐
          │                   L2 Cache                          │
          │  大小: 4 MB（整顆 GPU 共享）                        │
          │  延遲: ~200 cycles                                  │
          │  頻寬: ~GB/s–TB/s 量級（chip 內部互連）            │
          │  Scope: 全 GPU 所有 SM、所有 thread                 │
          │  生命週期: GPU context 存活期間                     │
          └─────────────────────┬───────────────────────────────┘
                                │
          ┌─────────────────────▼───────────────────────────────┐
          │              Global Memory (GDDR6)                  │
          │  大小: 16 GB                                        │
          │  延遲: ~400–800 cycles（無 L2 hit）                 │
          │  頻寬: 320 GB/s（峰值理論值）                       │
          │  Scope: 全 GPU + host（cudaMemcpy）                 │
          │  生命週期: 手動 cudaMalloc / cudaFree               │
          └─────────────────────────────────────────────────────┘

  ▲ 速度快、容量小、越靠近運算單元                              ▼ 速度慢、容量大、離得遠
```

這個金字塔不是抽象概念。Register 在 SM 的 register file 裡，Shared Memory 在 SM 的 SRAM 裡，L2 在 GPU die 上但遠離 SM，GDDR6 在 die 外面，透過 256-bit 記憶體匯流排連接。物理距離決定延遲。

---

## Register（寄存器）

### 基本特性

Register 是速度最快的記憶體，也是最透明的——你通常不需要思考它的存在，compiler 自動幫你把 local variable 放進去。

```cuda
__global__ void add(float *a, float *b, float *c, int n) {
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    // idx 在 register 裡
    // a[idx], b[idx] 的值讀出來後也在 register 裡
    if (idx < n)
        c[idx] = a[idx] + b[idx];  // 整個運算在 register 之間完成
}
```

T4 每個 SM 有 64K 個 32-bit register（256 KB），每個 thread 最多使用 255 個（硬體限制）。32-bit float、int 各佔一個 register；64-bit double 佔兩個。

存取延遲約 1 cycle——這是 GPU 上最快的操作，沒有之一。

### Register Spilling：你不知道的效能殺手

當你的 kernel 太複雜，每個 thread 需要的 register 超過 compiler 認為合理的上限時，compiler 會把溢出的 register 搬到 **local memory（本質上是 global memory）**，這個現象叫做 **register spilling**。

問題在於：local memory 走的是 global memory 路徑，延遲從 1 cycle 跳到 400–800 cycles。

你要怎麼知道發生了 spilling？用 `ptxas` 看編譯資訊：

```bash
nvcc -Xptxas -v -arch=sm_75 my_kernel.cu
```

輸出範例：
```
ptxas info    : Used 24 registers, 0 bytes smem, 0 bytes cmem[0]
# 正常
ptxas info    : Used 63 registers, 128 bytes lmem, 0 bytes smem
# 128 bytes lmem = local memory = spilling 了
```

`lmem` 非零就是 spilling 的訊號。

### 控制 Register 用量

兩種方式：

**方式一：`-maxrregcount`（編譯時全域限制）**
```bash
nvcc -maxrregcount=32 -arch=sm_75 my_kernel.cu
```

強制每個 thread 最多用 32 個 register。副作用是 compiler 為了符合限制，可能把更多東西推進 local memory。

**方式二：`__launch_bounds__`（per-kernel 精確控制）**
```cuda
// 告訴 compiler：這個 kernel 每個 block 最多 256 threads，
// 每個 SM 至少要能同時跑 2 個 block
__global__ void __launch_bounds__(256, 2)
my_kernel(float *a, float *b, float *c) {
    // compiler 會根據這個 hint 決定 register 預算
}
```

`__launch_bounds__(maxThreadsPerBlock, minBlocksPerMultiprocessor)` 給 compiler 更精確的資訊，讓它在 occupancy 和 spilling 之間取得更好的平衡。Ch 11 會深入討論 occupancy 與 register 的關係。

---

## Shared Memory（共享記憶體）

### 基本特性

Shared memory 是同一 block 內所有 thread 共享的 on-chip SRAM，延遲約 20–30 cycles。這比 global memory 快了 20–40 倍，但比 register 慢 20 倍。

用 `__shared__` 宣告：

```cuda
__global__ void tiled_matmul(float *A, float *B, float *C, int N) {
    __shared__ float tileA[16][16];  // 靜態宣告
    __shared__ float tileB[16][16];

    int row = blockIdx.y * 16 + threadIdx.y;
    int col = blockIdx.x * 16 + threadIdx.x;

    // 每個 thread 搬一個元素進 shared memory
    tileA[threadIdx.y][threadIdx.x] = A[row * N + threadIdx.x];
    tileB[threadIdx.y][threadIdx.x] = B[threadIdx.y * N + col];
    __syncthreads();  // 等所有 thread 都搬完

    float sum = 0.0f;
    for (int k = 0; k < 16; k++)
        sum += tileA[threadIdx.y][k] * tileB[k][threadIdx.x];

    C[row * N + col] = sum;
}
```

這是 tile 式矩陣乘法的骨架：把 global memory 的一塊搬到 shared memory，讓 block 內所有 thread 重複存取時都走快速路徑。

### 動態 Shared Memory 宣告

靜態宣告在編譯時就確定大小；動態宣告允許在 launch 時決定：

```cuda
// kernel 裡宣告
__global__ void dynamic_smem_kernel(float *data, int n) {
    extern __shared__ float smem[];  // 大小在 launch 時指定
    // ...
}

// host 端 launch
int smem_size = 1024 * sizeof(float);
dynamic_smem_kernel<<<grid, block, smem_size>>>(data, n);
```

動態宣告適合在不同 tile size 時複用同一個 kernel。

### Bank Conflict（銀行衝突）

Shared memory 內部分成 32 個 bank，每個 bank 寬度 4 bytes（Turing 架構）。若同一個 warp 內有多個 thread 在同一個 cycle 存取**同一個 bank 的不同地址**，就會發生 bank conflict——存取被串行化，效能下降。

最糟糕的情況：32 個 thread 全打同一個 bank，效能降為 1/32。

詳細分析和避免方法留到 Ch 13（shared memory bank conflict）。這裡只需要記住：shared memory 不是一塊整體記憶體，它有內部結構，存取模式很重要。

### Shared Memory 與 L1 的配置

Turing（T4）的每個 SM 有 96 KB 的 unified L1/shared memory。可以選擇兩種配置：

```cuda
// 設定 kernel 使用 64KB shared + 32KB L1
cudaFuncSetAttribute(
    my_kernel,
    cudaFuncAttributePreferredSharedMemoryCarveout,
    64  // 64KB for shared memory
);
```

如果你的 kernel 大量使用 shared memory，選 64KB shared + 32KB L1。
如果你的 kernel 依賴 L1 cache 來加速 global memory 存取，選 32KB shared + 64KB L1。

---

## L1 Cache

L1 cache 和 shared memory 共用那 96 KB 的 unified cache。與 CPU 的 L1 不同，GPU L1 對程式員是透明的——你不需要顯式操作，compiler 和硬體自動 cache global memory 的讀取。

Cache line 大小是 128 bytes（= 32 個 float）。這個數字很重要：當你從 global memory 讀一個 float，硬體實際搬動的是包含它的整個 128-byte cache line。

這直接連到 **memory coalescing**（記憶體合併存取）的概念：如果一個 warp 的 32 個 thread 讀的地址連續且對齊，它們共享同一次（或少數幾次）128-byte transaction；如果地址散亂，就需要多次 transaction，頻寬浪費掉了。

Ch 14 會深入 coalescing 的分析方法和最佳化技巧。這裡先記住：**L1 cache line = 128 bytes = coalescing 的粒度**。

---

## L2 Cache

L2 cache 是全 GPU 所有 SM 共享的一層快取，T4 上是 4 MB。延遲約 200 cycles，比 global memory 好了 2–4 倍。

L2 的存在有兩個重要意涵：

1. **Cross-SM 資料共享**：如果 SM A 和 SM B 都需要存取 global memory 的同一塊資料，L2 能讓第二次存取命中快取，不需要再打 DRAM。

2. **不同 kernel 之間的資料重用**：如果你有兩個連續 kernel，第一個 kernel 寫的資料可能還在 L2 裡，第二個 kernel 讀時就能命中。

不過 4 MB 對 16 GB 的 global memory 來說命中率取決於 working set 大小。如果你的 kernel 的工作集遠超過 4 MB，L2 help 有限。

A100 把 L2 擴大到 40 MB（H100 50 MB），這是 HBM 架構和更大晶片帶來的好處。T4 的 4 MB 是這個指標上的相對弱點。

---

## Global Memory

### 基本特性

Global memory 是 GPU 上容量最大、也最慢的記憶體。T4 有 16 GB GDDR6，理論頻寬 320 GB/s，但延遲高達 400–800 cycles（未命中 L2 時）。

這是用 `cudaMalloc` 分配的記憶體，host 和 device 都能存取（透過 PCIe 或 NVLink），生命週期由程式員手動管理。

### GDDR6 vs HBM

T4 使用 GDDR6，記憶體匯流排 256-bit。A100 使用 HBM2e，匯流排等效頻寬高達 2 TB/s，是 T4 的 6 倍以上。這個差距在 memory-bound kernel 上會直接體現在執行時間上。

| 規格 | T4 (Turing) | A100 (Ampere) |
|------|-------------|---------------|
| 記憶體類型 | GDDR6 | HBM2e |
| 容量 | 16 GB | 80 GB |
| 頻寬 | 320 GB/s | 2,000 GB/s |
| 位寬 | 256-bit | 5120-bit（等效）|

---

## Constant Memory（常數記憶體）

### 基本特性

Constant memory 是一塊 64 KB 的唯讀記憶體，有獨立的 constant cache（每個 SM 一個）。

```cuda
// 宣告在 kernel 外（file scope）
__constant__ float kernel_weights[256];

// host 端填值
cudaMemcpyToSymbol(kernel_weights, host_weights, 256 * sizeof(float));

// kernel 裡直接讀
__global__ void apply_filter(float *data, float *out, int n) {
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx < n)
        out[idx] = data[idx] * kernel_weights[idx % 256];
}
```

### 廣播機制（Broadcast）

Constant memory 的核心優勢是廣播（broadcast）：

- **整個 warp 讀同一個地址** → 只需 1 次存取，結果廣播給 32 個 thread → 延遲等同 register
- **warp 內各 thread 讀不同地址** → 32 次串行存取 → 延遲等同 global memory

這個特性讓 constant memory 非常適合：
- 所有 thread 共用的係數、lookup table
- kernel 啟動參數（CUDA runtime 本來就把 kernel 參數放進 constant memory）

如果你的 filter weights、物理常數、轉換矩陣所有 thread 都讀同一份，constant memory 是最佳選擇。如果每個 thread 讀不同位置，用 global memory 反而更快（至少不會比 constant memory 更差）。

---

## Local Memory（本地記憶體）

「Local memory」這個名字有誤導性——它聽起來很快，但它實際存在 **global memory** 上。

Local memory 是 thread 私有的，出現在以下兩種情況：

1. **Register spilling**：thread 使用的 register 超過限制，compiler 把溢出的部分放到 local memory
2. **超大 local array**：在 kernel 內宣告過大的 local array，compiler 判斷無法放入 register，自動搬到 local memory

```cuda
__global__ void bad_kernel() {
    int big_array[1024];  // 1024 個 int = 4096 bytes
    // compiler 幾乎必然把這個 array 放到 local memory（即 global memory）
    // 存取延遲 400-800 cycles
}
```

Local memory 有 L1/L2 cache 緩解，但這不能根本解決問題——如果存取模式不好，cache 效果有限。

正確的處理方式：

- 縮小 kernel 邏輯，讓 register 壓力下降
- 把大型資料結構改放 shared memory（如果 block 內需要共享的話）
- 用 `__launch_bounds__` 給 compiler 更多資訊

---

## 一張大整理表

這是本章最重要的參考表，把所有記憶體類型的關鍵屬性並排：

| 類型 | 大小 | 延遲 | 頻寬量級 | Scope | 生命週期 | 宣告方式 | 備註 |
|------|------|------|----------|-------|----------|----------|------|
| Register | 64K reg/SM（256 KB） | ~1 cycle | 極高（on-chip） | 單 thread | thread 存活期 | 無（compiler 自動） | 溢出 → local memory |
| Shared Memory | 96 KB/SM（含 L1） | ~20–30 cycles | ~TB/s 量級 | 同一 block | block 存活期 | `__shared__` | 32 bank，需防 conflict |
| L1 Cache | 96 KB/SM（與 shared 共用） | ~20–30 cycles | ~TB/s 量級 | 同一 SM | 自動管理 | 無（自動 cache） | 128-byte cache line |
| L2 Cache | 4 MB（全 GPU 共用） | ~200 cycles | ~GB/s–TB/s | 全 GPU | 自動管理 | 無（自動 cache） | Cross-SM 唯一共享快取 |
| Global Memory | 16 GB（T4） | ~400–800 cycles | 320 GB/s（T4 峰值） | 全 GPU + host | 手動管理 | `cudaMalloc` | Coalescing 決定實際頻寬 |
| Constant Memory | 64 KB | ~1 cycle（廣播）/ ~400–800 cycles（各異） | 視命中情況 | 全 GPU（唯讀） | kernel 存活期 | `__constant__` | 全 warp 同地址時最快 |
| Texture Memory | 視資料大小 | ~數十 cycles（cache hit） | 視命中情況 | 全 GPU（唯讀） | 手動管理 | `cudaBindTexture` / tex2D | 支援 2D locality，硬體插值 |
| Local Memory | 理論無上限（DRAM） | ~400–800 cycles | 320 GB/s（與 global 共用） | 單 thread | thread 存活期 | 無（compiler 自動 spill） | 名為 local，實為 global |

---

## Texture Memory（材質記憶體）

Texture memory 是另一種唯讀快取路徑，有獨立的 texture cache。它的特殊之處在於對 **2D 空間局部性（spatial locality）** 的最佳化：在 2D 圖像存取中，(x, y) 和 (x+1, y)、(x, y+1) 的地址不相鄰（在 row-major 佈局下），但 texture cache 的組織方式讓這些「2D 鄰居」仍能有效命中快取。

此外，texture memory 提供硬體加速的 **雙線性插值（bilinear interpolation）** 和邊界處理（clamp / wrap / mirror），在影像處理和需要插值的場景很有用。

```cuda
// 使用 CUDA texture object（現代 API）
cudaTextureDesc texDesc = {};
texDesc.filterMode = cudaFilterModeLinear;  // 開啟線性插值
texDesc.readMode = cudaReadModeElementType;

cudaTextureObject_t texObj;
cudaCreateTextureObject(&texObj, &resDesc, &texDesc, nullptr);

// kernel 內讀取
__global__ void sample_kernel(cudaTextureObject_t tex, float *out, int w, int h) {
    float u = (threadIdx.x + 0.5f) / w;
    float v = (blockIdx.x + 0.5f) / h;
    out[blockIdx.x * w + threadIdx.x] = tex2D<float>(tex, u, v);
}
```

---

## Register Spilling 深挖

Register spilling 是效能分析初學者最常忽略的問題，因為它不會報錯，程式結果完全正確，只是莫名地慢。

### 為什麼會發生

Compiler 決定 register 分配時，它既要讓 kernel 跑得快（多用 register，減少記憶體存取），又要控制 register 用量（讓更多 warp 同時 in-flight，提高 occupancy）。當一個 thread 的 live variable 太多，compiler 沒有足夠的 register，就把部分 variable「spill」到 local memory。

### 如何診斷

步驟一：編譯時看 `-v` 輸出中的 `lmem`。

步驟二：用 Nsight Compute 分析：
```bash
ncu --metrics l1tex__data_pipe_lsu_wavefronts_mem_local_op_ld.sum \
    ./my_program
```
Local memory load 次數很高 → spilling 在傷害你。

步驟三：在 `cuobjdump` 的 PTX 輸出裡找 `.local` 操作。

### 為什麼影響這麼大

假設你有一個 kernel，每個 thread 在 register 裡做 100 個浮點運算，平均延遲 100 cycles。如果 spilling 讓其中 10 個 register 變成 local memory 存取，每次存取 400 cycles，extra cost 就是 10 × 400 = 4000 cycles，是原本運算時間的 40 倍。

更糟的是：spilling 的存取模式通常對 cache 不友善（因為它是 compiler 強迫的，不是程式員設計的），L1 命中率不高，實際延遲常態偏向 800 cycles。

### 解法優先順序

1. 拆分 kernel（把一個大 kernel 拆成兩個小 kernel，中間用 global memory 傳遞）
2. `__launch_bounds__` 給 compiler 更精確的 register 預算提示
3. `-maxrregcount` 全域限制（但可能反過來增加 spilling）
4. 手動重寫邏輯，減少 live variable 的數量

---

## 踩雷

**踩雷一：以為 `__shared__` 宣告後自動同步**

shared memory 宣告完之後，不代表所有 thread 都已經寫完資料。你需要 `__syncthreads()` 來確保 block 內所有 thread 都執行到這個屏障點，才能安全讀取其他 thread 寫的資料。漏掉 `__syncthreads()` 會出現 data race，結果不可預期。

**踩雷二：把 local memory 誤以為是 on-chip 的**

「local」這個名字讓人誤以為它很快。實際上 local memory 就是 global memory 的一個區域，延遲相同。用 `-v` 確認 `lmem = 0` 才算安全。

**踩雷三：constant memory 用在非廣播場景**

Constant memory 只在整個 warp 讀同一地址時有廣播加速。如果 kernel 的存取模式是 `constant_array[threadIdx.x]`（每個 thread 不同地址），效能等同或差於 global memory（串行存取）。這種情況應該改用 global memory 或 shared memory。

**踩雷四：不知道 shared memory 和 L1 共用 96 KB**

有人以為 shared memory 是額外的 96 KB，L1 又是另外的 96 KB。實際上它們共用一塊。你給 shared memory 分配越多，留給 L1 的就越少。要根據 kernel 特性決定最佳比例。

**踩雷五：對 global memory 做 byte-by-byte 存取**

```cuda
// 危險：非 coalesced 存取
char *data = ...;
char val = data[threadIdx.x * 100];  // stride = 100 bytes
```

GPU 每次 global memory transaction 是 128 bytes，如果 warp 內 32 個 thread 的地址散亂，就需要最多 32 次 transaction，有效頻寬只剩 1/32。Ch 14 會系統性地解決這個問題。

---

## 進階

### Persistent L2 Cache（Ampere 及之後）

CUDA 11.2 在 A100 引入了 L2 cache persistence：你可以把某塊 global memory 資料標記為「優先留在 L2 裡」，讓後續 kernel 重複使用時更可能命中。T4 沒有這個功能，但這是你往 A100 遷移時要了解的新工具。

```cuda
// A100 專屬，T4 不支援
cudaStreamAttrValue attr;
attr.accessPolicyWindow.base_ptr = data;
attr.accessPolicyWindow.num_bytes = data_size;
attr.accessPolicyWindow.hitRatio = 0.6f;
attr.accessPolicyWindow.hitProp = cudaAccessPropertyPersisting;
attr.accessPolicyWindow.missProp = cudaAccessPropertyStreaming;
cudaStreamSetAttribute(stream, cudaStreamAttributeAccessPolicyWindow, &attr);
```

### Warp-Level Shuffle（Register 層級通訊）

Turing 提供 warp shuffle 指令（`__shfl_sync`），讓 warp 內的 thread 直接交換 register 值，不需要經過 shared memory：

```cuda
__global__ void warp_reduce(float *data, float *result) {
    float val = data[threadIdx.x];
    // warp 內 reduction，完全在 register 層進行
    for (int offset = 16; offset > 0; offset >>= 1)
        val += __shfl_down_sync(0xffffffff, val, offset);
    if (threadIdx.x == 0)
        result[blockIdx.x] = val;
}
```

這比透過 shared memory 做 reduction 更快，因為不需要 `__syncthreads()`，也不佔用 shared memory。

### Unified Memory（統一記憶體）

`cudaMallocManaged` 分配的 Unified Memory 讓 CPU 和 GPU 可以直接存取同一塊記憶體，CUDA runtime 自動搬移資料。背後依賴 page migration，實際效能取決於 access pattern——如果 GPU 大量存取的 page 還在 CPU 記憶體上，page fault 的代價很高。Unified Memory 適合開發快速驗證，不適合效能關鍵路徑。

---

## 動手練習

### 練習 1：觀察 Register Spilling

寫一個故意使用大量 local variable 的 kernel，然後觀察 `ptxas -v` 的 `lmem` 輸出。逐步減少 variable 數量，找到 spilling 消失的臨界點。

```cuda
__global__ void many_vars_kernel(float *out) {
    float v0 = threadIdx.x, v1 = v0 + 1, v2 = v1 + 1; /* ... 一直到 v63 */
    // 依序累加，防止 compiler 最佳化掉
    out[threadIdx.x] = v0 + v1 + v2; /* + ... */
}
```

```bash
nvcc -Xptxas -v -arch=sm_75 spilling_test.cu -o spilling_test
# 觀察 lmem 數字
```

### 練習 2：Shared Memory Tile 效能對比

實作兩個版本的向量點積：一個直接從 global memory 讀，另一個先把資料 tile 到 shared memory。用 `cudaEventElapsedTime` 計時，比較兩者在不同資料大小下的差距。

```cuda
// 版本 A：直接讀 global memory
__global__ void dot_global(float *a, float *b, float *c, int n);

// 版本 B：先搬到 shared memory
__global__ void dot_shared(float *a, float *b, float *c, int n) {
    __shared__ float tile_a[256], tile_b[256];
    // ... 填資料，__syncthreads()，計算
}
```

### 練習 3：Constant Memory vs Global Memory

把一個有 256 個係數的 FIR filter 實作兩個版本：係數放 `__constant__`，係數放 `float *`（global memory）。用 Nsight Compute 測量 L1/L2 命中率和執行時間差異。預期 constant 版本在所有 thread 都讀同一個係數時快很多。

---

## 本章重點

1. **記憶體延遲跨越三個數量級**：register ~1 cycle，shared memory ~20–30 cycles，global memory ~400–800 cycles。選錯記憶體類型，效能可以差 400 倍。

2. **Register spilling 是隱性殺手**：沒有報錯、結果正確，但效能急劇下降。用 `ptxas -v` 的 `lmem` 欄位診斷，用 `__launch_bounds__` 或 kernel 拆分解決。

3. **Shared memory 是高效能 kernel 的核心工具**：block 內 thread 通訊、tile 式資料重用都靠它。20–30 cycles 的延遲和 global memory 的 400–800 cycles 差距，在 tile 式算法中能帶來量級的加速。

4. **L2 是全 GPU 共享的唯一快取層**：4 MB（T4），cross-SM 資料共享的唯一緩衝。Working set 超過 4 MB 時命中率驟降。

5. **Constant memory 的廣播機制**：全 warp 讀同地址時接近 register 速度，各 thread 讀不同地址時退化為 global memory 串行存取。

6. **L1/Shared memory 共用 96 KB**（Turing）：配置比例要根據 kernel 的記憶體存取特性決定。

---

## 自我檢核

完成本章後，你應該能夠回答以下問題：

- [ ] 記憶體金字塔六層的延遲數字，你能從快到慢說出大概值嗎？
- [ ] 什麼是 register spilling？怎麼用 `ptxas -v` 偵測？
- [ ] `__launch_bounds__(256, 2)` 的兩個數字分別代表什麼？
- [ ] Shared memory 的 32 個 bank 是什麼意思？所有 thread 讀同一地址是 bank conflict 嗎？
- [ ] Constant memory 的廣播條件是什麼？什麼情況下用它反而更慢？
- [ ] L1 cache line 是多大？為什麼這個數字和 coalescing 有關？
- [ ] Local memory 為什麼慢？它在哪裡？
- [ ] T4 的 L2 是多大？L2 太小會有什麼後果？

---

## 延伸閱讀

1. **CUDA C++ Programming Guide §5 — Memory Model**：官方對所有記憶體類型的定義和使用規則，§5.3 Device Memory Accesses 詳細說明 coalescing 規則。

2. **《Programming Massively Parallel Processors》Ch 5 — Memory Performance Considerations**（Kirk & Hwu）：系統性介紹 shared memory tile 算法，包含 matrix multiplication 的完整推導，是理解記憶體階層最清晰的教科書章節。

3. **"Dissecting the NVIDIA Turing T4 GPU via Microbenchmarking"**（arXiv 1903.07486）：透過 microbenchmark 實測 T4 各層記憶體延遲、頻寬和快取大小，是本章數字的重要來源之一，值得對照書上的理論數字和實測結果。

4. **"Dissecting GPU Memory Hierarchy through Microbenchmarking"**（arXiv 1509.02308, Mei & Chu）：更早也更全面的 GPU 記憶體延遲實測，涵蓋多種 NVIDIA 架構，可以看各代 GPU 記憶體架構的演進趨勢。

5. **NVIDIA Nsight Compute 官方文件 — Memory Workload Analysis**：學會用 profiler 找記憶體瓶頸。重點看「Memory Throughput」、「L1/L2 Hit Rate」、「Shared Memory Bank Conflicts」這幾個 section。工具在手，數字說話，猜測不如量測。

---

## 銜接

本章建立了記憶體階層的完整框架。接下來的幾章都會持續依賴這個框架：

- **Ch 10（warp 與 SIMT 執行）**：warp 的執行模型如何決定記憶體存取的實際行為（divergence 和 coalescing 的前置知識）
- **Ch 11（occupancy 與 register）**：register 用量如何影響 warp occupancy，以及 occupancy 和效能的實際關係
- **Ch 13（shared memory bank conflict）**：深入 32-bank 結構，系統性分析和避免 bank conflict
- **Ch 14（memory coalescing）**：global memory 存取的合併規則，128-byte transaction 的實際含義和最佳化方法

---

→ [Ch 10 — warp 與 SIMT 執行](./10-warp-simt-execution.md)
