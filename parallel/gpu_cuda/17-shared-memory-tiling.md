# Ch 17 — Shared Memory 與 Tiling：手動 cache

> **目標**：理解 shared memory 在記憶體階層中的地位；學會靜態與動態 shared 宣告；掌握 tiling 的核心思想；用 tiled 矩陣乘法這個標準範例把概念落地；認識 bank conflict（下一章深挖）。

> **環境**：CUDA 12.x, Colab T4 (sm_75)。程式輸出均為「Colab 預期行為，未在本機實測」，附 Colab 執行步驟。

---

如果你已經看過 [Ch 9 記憶體階層](./09-memory-hierarchy.md)，你知道 GPU 的記憶體有多少個層次，以及 shared memory 在其中的位置：它是 on-chip 的，延遲只有 ~20–30 cycles，比 global memory 的 ~500 cycles 快 20 倍，頻寬比 global memory 高 10 倍以上。

但 shared memory 不會自動幫你用。它不像 CPU 的 L1 cache 那樣透明地工作。你要**明確地把資料搬進去**，然後**讓 thread 從那裡讀**。這就是 tiling（分塊）的本質。

這一章是課程效能優化部分的起點，也是接下來 Ch 18（coalescing）、Ch 19（bank conflict）能建立在其上的地基。

---

## 一、Shared Memory 的位置與特性

先重建一下記憶：

```
GPU 記憶體階層（延遲從小到大）：

  Register File    ← 每個 thread 自己的，0 cycle（最快）
       │
  ─────┼─────── On-chip（per SM）──────────────────────────────
       │
  L1 Cache /
  Shared Memory   ← 同一 block 的 thread 共享，~20-30 cycles
       │             程式設計師可控制的！（不像 CPU L1 是透明的）
  ─────┼─────── Off-chip ────────────────────────────────────
       │
  L2 Cache        ← 所有 SM 共享，~200 cycles
       │
  DRAM (HBM)      ← Global Memory，~500 cycles
```

**關鍵認知**：L1 Cache 和 Shared Memory **共用同一塊 on-chip SRAM**。在 Turing（T4, sm_75）上，這塊 SRAM 有 96 KB per SM。你可以設定多少給 L1、多少給 shared：

```c
// 把大部分給 shared（prefers larger shared over L1）
cudaFuncSetAttribute(myKernel,
    cudaFuncAttributePreferredSharedMemoryCarveout, 100); // 100% → 全給 shared
```

T4 最大 shared memory per block 是 64 KB，per SM 是 96 KB（有些設定可到 96 KB per block on Turing）。

---

## 二、宣告 Shared Memory：靜態 vs 動態

### 靜態宣告（編譯期已知大小）

```c
__global__ void static_shared_demo(float *data, float *out) {
    __shared__ float smem[256];   // 大小是常數
    int tid = threadIdx.x;

    smem[tid] = data[blockIdx.x * blockDim.x + tid];
    __syncthreads();

    out[blockIdx.x * blockDim.x + tid] = smem[255 - tid];
}
```

`__shared__` 告訴編譯器：這個陣列放在 shared memory，所有 block 的 thread 共用同一份（但不同 block 各自有一份）。

靜態宣告在編譯時就確定大小，編譯器能做更好的 register 配置。

### 動態宣告（runtime 決定大小）

有時你不知道 block 要處理多少資料，或你想讓同一個 kernel 支援不同的 tile size：

```c
// extern __shared__ 宣告一個大小不定的陣列
__global__ void dynamic_shared_demo(float *data, float *out, int n) {
    extern __shared__ float smem[];   // 大小在 launch 時決定
    int tid = threadIdx.x;

    if (tid < n) smem[tid] = data[blockIdx.x * n + tid];
    __syncthreads();

    if (tid < n) out[blockIdx.x * n + tid] = smem[n - 1 - tid];
}

// Launch 時的第三個角括號內指定 shared memory 大小（bytes）
int main() {
    int tile = 64;
    dynamic_shared_demo<<<grid, block, tile * sizeof(float)>>>(d_in, d_out, tile);
    //                                ↑
    //                                這是 shared memory 的 bytes 數
}
```

**多個動態 shared 陣列**：只能宣告一個 `extern __shared__` 陣列，然後手動切割：

```c
extern __shared__ char smem_raw[];

float *fa = (float *)smem_raw;             // 從 offset 0 開始
int   *ia = (int *)(smem_raw + n * sizeof(float));  // 接在 float 陣列後面
```

注意對齊問題：如果 `float` 之後放 `int`，上面剛好對齊。如果混用不同型別，要手動確保對齊（`sizeof(double)` = 8 bytes 的陣列後面接 4 bytes 的陣列可能需要填充）。

---

## 三、Tiling 的核心思想

### 問題：Global Memory 的慢

矩陣乘法 C = A × B（A 是 M×K，B 是 K×N，C 是 M×N）。

Naive 做法：每個 thread 算 C 的一個元素，對 K 做迴圈：

```
thread (row, col) 算 C[row][col]:
  for k in 0..K:
      C[row][col] += A[row][k] * B[k][col]
```

這樣每個 thread 讀 K 個 A 元素 + K 個 B 元素 = 2K 次 global memory 讀取。整個 kernel 讀 M×K + K×N 個 float，但實際上 A 的每一行被所有算那一行 C 的 thread 讀了 N 次；B 的每一列被所有算那一列 C 的 thread 讀了 M 次。

**重複讀取率**（Arithmetic Intensity 的倒數那面）：每個 A 元素被讀 N 次，每個 B 元素被讀 M 次。這些重複讀取全部打到慢速的 global memory。

### Tiling 的思路

把 C 的計算切成 TILE_SIZE × TILE_SIZE 的小塊，每次只算 C 的一小塊。算這個小塊需要的 A 和 B 子矩陣（也叫 tile），先從 global memory 搬到 shared memory，然後 block 內的 thread 從 shared memory 讀——快速、重複使用。

```
矩陣 A（M×K），矩陣 B（K×N），輸出 C（M×N）
                                        ←──── N ────→
                        ↑                ┌──────────────┐
                        M                │              │  C
                        ↓                └──────────────┘

把 C 切成 T×T 的小塊：

         A（行的一片）    B（列的一片）
        ←─ T ─→         ←─ T ─→
    ↑   ┌───────┐        ┌───────┐
    T   │ tile  │   ×    │ tile  │   →  C 的 T×T 小塊
    ↓   └───────┘        └───────┘

一個 block 負責算 C 的一個 T×T 小塊：
  1. 把 A 的一個 T×T tile 搬進 shared memory（一個 block 的 thread 合力做）
  2. 把 B 的一個 T×T tile 搬進 shared memory（同上）
  3. __syncthreads()
  4. 所有 thread 從 shared memory 讀 A_tile 和 B_tile 做點積
  5. __syncthreads()
  6. 移到 K 方向的下一個 tile，回到步驟 1
```

這樣 A_tile 和 B_tile 各搬進 shared memory 一次，block 內的 T² 個 thread 各讀 T 次——總共省了多少 global memory 讀？

- Naive：每個 thread 讀 2K 次 global memory
- Tiled（tile size T）：搬入 shared 只要 2T 次 global memory per tile，對 K/T 個 tile 做：(K/T) × 2T = 2K 次 global memory 讀，**但現在 T² 個 thread 共用**這 2K 次讀取，等效每個 thread 只 2K/T² 次（或說 bandwidth 利用率提高 T 倍）

---

## 四、Tiled 矩陣乘法：完整 Kernel

這是全課最重要的程式碼，仔細看每一行：

```c
// tiled_matmul.cu
// 假設 M, N, K 是 TILE_SIZE 的整數倍（邊界處理另外討論）
#define TILE_SIZE 16

__global__ void matmul_tiled(
    const float * __restrict__ A,    // [M][K]
    const float * __restrict__ B,    // [K][N]
    float *C,                        // [M][N]
    int M, int K, int N)
{
    // 每個 block 算 C 的一個 TILE_SIZE × TILE_SIZE 子矩陣
    // blockIdx.y → C 的哪一個行塊（row block）
    // blockIdx.x → C 的哪一個列塊（col block）
    int row = blockIdx.y * TILE_SIZE + threadIdx.y;  // C 的 row 索引
    int col = blockIdx.x * TILE_SIZE + threadIdx.x;  // C 的 col 索引

    // 這個 thread 負責累積 C[row][col] 的值
    float sum = 0.0f;

    // Shared memory：存放目前正在處理的 A tile 和 B tile
    __shared__ float A_tile[TILE_SIZE][TILE_SIZE];
    __shared__ float B_tile[TILE_SIZE][TILE_SIZE];

    // 沿 K 方向逐 tile 計算
    // numTiles = K / TILE_SIZE（假設整除）
    int numTiles = (K + TILE_SIZE - 1) / TILE_SIZE;

    for (int t = 0; t < numTiles; t++) {

        // ── Phase 1：協作載入 tile ──────────────────────────────────
        // 所有 thread 合力把 A 的 tile t 搬進 A_tile
        // A[row][t*TILE_SIZE + threadIdx.x]
        int a_col = t * TILE_SIZE + threadIdx.x;
        A_tile[threadIdx.y][threadIdx.x] =
            (row < M && a_col < K) ? A[row * K + a_col] : 0.0f;

        // 所有 thread 合力把 B 的 tile t 搬進 B_tile
        // B[t*TILE_SIZE + threadIdx.y][col]
        int b_row = t * TILE_SIZE + threadIdx.y;
        B_tile[threadIdx.y][threadIdx.x] =
            (b_row < K && col < N) ? B[b_row * N + col] : 0.0f;

        // ── Phase 2：等所有人都搬完 ────────────────────────────────
        __syncthreads();

        // ── Phase 3：從 shared memory 算點積 ───────────────────────
        for (int k = 0; k < TILE_SIZE; k++) {
            sum += A_tile[threadIdx.y][k] * B_tile[k][threadIdx.x];
        }

        // ── Phase 4：等計算完，才能開始載入下一個 tile ───────────
        __syncthreads();
    }

    // 寫回 C
    if (row < M && col < N) {
        C[row * N + col] = sum;
    }
}
```

### 為什麼需要**兩個** __syncthreads？

第一個（Phase 2 結束後）：確保 tile 完全搬進 shared memory 才開始計算。如果沒有，可能有 thread 讀到還沒載入的位置。

第二個（Phase 3 結束後）：確保計算用完這個 tile 的資料，才開始載入下一個 tile（同一個 shared memory 位置會被覆蓋）。如果沒有，可能有 thread 還在用 A_tile[0][0]，另一個 thread 已經在載入下一個 tile 覆蓋它了。

少了任何一個 `__syncthreads()` → silent data corruption，而且不一定每次都能重現（因為 race condition 的結果取決於 warp 排程）。

### Tile 載入的視覺化

```
K=32, TILE_SIZE=16, 所以 numTiles=2

t=0:  A_tile = A[row_block*T : (row_block+1)*T][0 : T]
      B_tile = B[0 : T][col_block*T : (col_block+1)*T]
      ↓ sync ↓
      sum += A_tile × B_tile（點積第 0..15 項）
      ↓ sync ↓

t=1:  A_tile = A[row_block*T : (row_block+1)*T][T : 2T]
      B_tile = B[T : 2T][col_block*T : (col_block+1)*T]
      ↓ sync ↓
      sum += A_tile × B_tile（點積第 16..31 項）
      ↓ sync ↓

寫回：C[row][col] = sum（累積了完整的點積）
```

### 索引映射（不要搞混）

這是 tiled matmul 最容易出錯的地方，把它畫清楚：

```
A 是 row-major [M][K]，A[i][j] = A_flat[i * K + j]

thread (threadIdx.y, threadIdx.x) 在 tile t 時載入：
  A_tile[threadIdx.y][threadIdx.x]
    = A[ row ][ t*TILE_SIZE + threadIdx.x ]
    = A_flat[ row * K + t*TILE_SIZE + threadIdx.x ]
         ↑
         row = blockIdx.y * TILE_SIZE + threadIdx.y

B 是 row-major [K][N]，B[i][j] = B_flat[i * N + j]

thread (threadIdx.y, threadIdx.x) 在 tile t 時載入：
  B_tile[threadIdx.y][threadIdx.x]
    = B[ t*TILE_SIZE + threadIdx.y ][ col ]
    = B_flat[ (t*TILE_SIZE + threadIdx.y) * N + col ]
                                              ↑
                                              col = blockIdx.x * TILE_SIZE + threadIdx.x
```

注意 `threadIdx.y` 和 `threadIdx.x` 分別對應 B 的哪個維度：threadIdx.y 對應 B 的行（row），threadIdx.x 對應 B 的列（col）。這樣才能讓同一個 warp（連續 threadIdx.x）讀 B 的連續列——coalesced access（Ch 18 會深挖）。

---

## 五、Host 端程式碼：完整驗證

```c
// 完整程式（含驗證）
%%writefile tiled_matmul.cu
#include <cuda_runtime.h>
#include <stdio.h>
#include <stdlib.h>
#include <math.h>

#define TILE_SIZE 16

// ── CPU 參考實作 ──────────────────────────────────────────────────────
void matmul_cpu(const float *A, const float *B, float *C, int M, int K, int N) {
    for (int i = 0; i < M; i++) {
        for (int j = 0; j < N; j++) {
            float sum = 0.0f;
            for (int k = 0; k < K; k++) {
                sum += A[i * K + k] * B[k * N + j];
            }
            C[i * N + j] = sum;
        }
    }
}

// ── GPU Naive kernel ──────────────────────────────────────────────────
__global__ void matmul_naive(
    const float *A, const float *B, float *C,
    int M, int K, int N)
{
    int row = blockIdx.y * blockDim.y + threadIdx.y;
    int col = blockIdx.x * blockDim.x + threadIdx.x;
    if (row < M && col < N) {
        float sum = 0.0f;
        for (int k = 0; k < K; k++) {
            sum += A[row * K + k] * B[k * N + col];
        }
        C[row * N + col] = sum;
    }
}

// ── GPU Tiled kernel ──────────────────────────────────────────────────
__global__ void matmul_tiled(
    const float * __restrict__ A,
    const float * __restrict__ B,
    float *C,
    int M, int K, int N)
{
    int row = blockIdx.y * TILE_SIZE + threadIdx.y;
    int col = blockIdx.x * TILE_SIZE + threadIdx.x;

    __shared__ float A_tile[TILE_SIZE][TILE_SIZE];
    __shared__ float B_tile[TILE_SIZE][TILE_SIZE];

    float sum = 0.0f;
    int numTiles = (K + TILE_SIZE - 1) / TILE_SIZE;

    for (int t = 0; t < numTiles; t++) {
        // 載入 A tile（邊界保護：超出範圍補 0）
        int a_col = t * TILE_SIZE + threadIdx.x;
        A_tile[threadIdx.y][threadIdx.x] =
            (row < M && a_col < K) ? A[row * K + a_col] : 0.0f;

        // 載入 B tile
        int b_row = t * TILE_SIZE + threadIdx.y;
        B_tile[threadIdx.y][threadIdx.x] =
            (b_row < K && col < N) ? B[b_row * N + col] : 0.0f;

        __syncthreads();   // 等 tile 載入完成

        // 從 shared memory 做點積
        for (int k = 0; k < TILE_SIZE; k++) {
            sum += A_tile[threadIdx.y][k] * B_tile[k][threadIdx.x];
        }

        __syncthreads();   // 等計算完再載入下一 tile
    }

    if (row < M && col < N) {
        C[row * N + col] = sum;
    }
}

// ── 驗證工具 ──────────────────────────────────────────────────────────
int verify(const float *ref, const float *test, int size, float tol) {
    for (int i = 0; i < size; i++) {
        float diff = fabsf(ref[i] - test[i]);
        if (diff > tol * fabsf(ref[i]) + tol) {
            printf("Mismatch at [%d]: ref=%f test=%f diff=%f\n",
                   i, ref[i], test[i], diff);
            return 0;
        }
    }
    return 1;
}

int main() {
    // 用 64×64 矩陣（TILE_SIZE=16 的倍數）
    const int M = 64, K = 64, N = 64;
    size_t sA = M * K * sizeof(float);
    size_t sB = K * N * sizeof(float);
    size_t sC = M * N * sizeof(float);

    // 分配 host 記憶體
    float *h_A = (float*)malloc(sA);
    float *h_B = (float*)malloc(sB);
    float *h_C_cpu  = (float*)calloc(M * N, sizeof(float));
    float *h_C_naive= (float*)calloc(M * N, sizeof(float));
    float *h_C_tiled= (float*)calloc(M * N, sizeof(float));

    // 初始化：簡單的值（方便肉眼驗）
    for (int i = 0; i < M * K; i++) h_A[i] = (float)(i % 7);
    for (int i = 0; i < K * N; i++) h_B[i] = (float)(i % 5);

    // CPU 參考
    matmul_cpu(h_A, h_B, h_C_cpu, M, K, N);
    printf("CPU result C[0][0] = %f\n", h_C_cpu[0]);

    // 分配 device 記憶體
    float *d_A, *d_B, *d_C;
    cudaMalloc(&d_A, sA);
    cudaMalloc(&d_B, sB);
    cudaMalloc(&d_C, sC);

    cudaMemcpy(d_A, h_A, sA, cudaMemcpyHostToDevice);
    cudaMemcpy(d_B, h_B, sB, cudaMemcpyHostToDevice);

    dim3 block(TILE_SIZE, TILE_SIZE);
    dim3 grid((N + TILE_SIZE - 1) / TILE_SIZE,
              (M + TILE_SIZE - 1) / TILE_SIZE);

    // Naive
    cudaMemset(d_C, 0, sC);
    matmul_naive<<<grid, block>>>(d_A, d_B, d_C, M, K, N);
    cudaDeviceSynchronize();
    cudaMemcpy(h_C_naive, d_C, sC, cudaMemcpyDeviceToHost);
    printf("Naive GPU matches CPU: %s\n",
           verify(h_C_cpu, h_C_naive, M * N, 1e-4f) ? "YES" : "NO");

    // Tiled
    cudaMemset(d_C, 0, sC);
    matmul_tiled<<<grid, block>>>(d_A, d_B, d_C, M, K, N);
    cudaDeviceSynchronize();
    cudaMemcpy(h_C_tiled, d_C, sC, cudaMemcpyDeviceToHost);
    printf("Tiled GPU matches CPU: %s\n",
           verify(h_C_cpu, h_C_tiled, M * N, 1e-4f) ? "YES" : "NO");

    // 清理
    cudaFree(d_A); cudaFree(d_B); cudaFree(d_C);
    free(h_A); free(h_B);
    free(h_C_cpu); free(h_C_naive); free(h_C_tiled);
    return 0;
}
```

### Colab 執行步驟

```python
# cell 1：寫檔案（上面的 %%writefile 已完成）

# cell 2：編譯
!nvcc -arch=sm_75 -O2 tiled_matmul.cu -o tiled_matmul

# cell 3：執行
!./tiled_matmul
```

預期輸出（Colab 預期行為，未在本機實測）：

```
CPU result C[0][0] = 2184.000000
Naive GPU matches CPU: YES
Tiled GPU matches CPU: YES
```

---

## 六、效能分析：為什麼 tiling 快（理論）

讓我們用具體數字算一下 T4 上的理論效益。

**T4 規格**（參考 Ch 9 / [架構 whitepaper](https://www.nvidia.com/content/dam/en-zz/Solutions/design-visualization/t4-tensor-core/nvidia-t4-turing-architecture-whitepaper.pdf)）：
- Global memory bandwidth：~320 GB/s
- Shared memory bandwidth：~1.6 TB/s (per SM)
- CUDA core throughput：8.1 TFLOPS（FP32）

**計算密度（Arithmetic Intensity）比較**：

```
N=1024 的方陣乘法（M=K=N=1024）

總浮點運算量：2 × N³ = 2 × 2^30 ≈ 2.1 GFLOPs

Naive（每次從 global 讀）：
  讀取量 = 2 × N³ float = 2 × 4GB ← 是的，每個 element 被讀 N 次
  Arithmetic Intensity = 2.1 GFLOPs / (8 GB) = 0.26 FLOP/byte
  → Roofline：memory bound，頻寬限制 320 GB/s → 83 GFLOPS 上限

Tiled（TILE_SIZE=16）：
  讀取量 = (N/T)² tiles × 2 × T² float × tile（搬進 shared 一次）
         = 2 × N² float = 2 × 4 MB
  ← N=1024, T=16: 每個 A、B 元素只從 global 讀一次！
  Arithmetic Intensity = 2.1 GFLOPs / (8 MB) = 263 FLOP/byte
  → Roofline：compute bound，Tensor Core 路徑下限制更高
```

這就是 tiling 的威力：把 arithmetic intensity 從 0.26 提升到 263——提升了 **1000 倍**。當然實際效能沒有 1000 倍差距（還有其他瓶頸），但理論上已經從 memory bound 翻到 compute bound，這才是讓後續優化有意義的前提。

---

## 七、Bank Conflict 初探（伏筆）

Shared memory 雖然快，但不是完全無代價。它被組織成 **32 個 bank**（T4 是 32-bit bank），每個 bank 寬 4 bytes。如果同一個 warp 的多個 thread 同時存取同一個 bank，就會產生 bank conflict——請求被序列化，吞吐量下降。

以我們的 tiled kernel 為例：

```c
// B_tile 的存取模式
sum += A_tile[threadIdx.y][k] * B_tile[k][threadIdx.x];
```

`B_tile[k][threadIdx.x]`：同一個 warp 的 thread（threadIdx.x 連續），在同一列（k 固定），讀不同行（threadIdx.x 不同）。相鄰 thread 讀相鄰 float——**bank 連續，沒有 conflict**。

`A_tile[threadIdx.y][k]`：同一個 warp 的 thread 有相同的 threadIdx.y（同一行），讀同一個 `k`——這是**廣播（broadcast）**，不是 conflict（32 個 thread 讀同一個地址，硬體廣播一次）。

所以這個 kernel 的 shared memory 存取本身沒有 bank conflict——但如果你把 A 和 B 的索引搞錯，bank conflict 就會悄悄出現。Ch 19 會把這件事挖透徹。

---

## 八、動態 Tile Size：讓 kernel 更有彈性

不要把 TILE_SIZE 寫死成 16。`extern __shared__` 讓你在 runtime 決定：

```c
// tile_size 由呼叫者決定
__global__ void matmul_dynamic_tile(
    const float *A, const float *B, float *C,
    int M, int K, int N, int tile_size)
{
    extern __shared__ float smem[];
    float *A_tile = smem;
    float *B_tile = smem + tile_size * tile_size;

    int ty = threadIdx.y, tx = threadIdx.x;
    int row = blockIdx.y * tile_size + ty;
    int col = blockIdx.x * tile_size + tx;

    float sum = 0.0f;
    int numTiles = (K + tile_size - 1) / tile_size;

    for (int t = 0; t < numTiles; t++) {
        int a_col = t * tile_size + tx;
        A_tile[ty * tile_size + tx] =
            (row < M && a_col < K) ? A[row * K + a_col] : 0.0f;

        int b_row = t * tile_size + ty;
        B_tile[ty * tile_size + tx] =
            (b_row < K && col < N) ? B[b_row * N + col] : 0.0f;

        __syncthreads();

        for (int k = 0; k < tile_size; k++) {
            sum += A_tile[ty * tile_size + k] * B_tile[k * tile_size + tx];
        }

        __syncthreads();
    }

    if (row < M && col < N) C[row * N + col] = sum;
}

// launch：
int tile = 32;
size_t smem_size = 2 * tile * tile * sizeof(float);
dim3 blk(tile, tile);
dim3 grd((N+tile-1)/tile, (M+tile-1)/tile);
matmul_dynamic_tile<<<grd, blk, smem_size>>>(d_A, d_B, d_C, M, K, N, tile);
```

**Tile size 的取捨**：
- 太小（如 4×4）：shared memory 利用率低，每次 tile 的 global memory 開銷相對高
- 太大（如 32×32）：shared memory 用量 = 2 × 32 × 32 × 4 = 8 KB per block，佔 SM 資源多，block 數目受限（影響 occupancy）
- 通常 16×16 或 32×32 是甜蜜點，要用 Nsight Compute profiler 確認（Ch 25）

---

## 九、踩雷集錦

### 雷 1：忘記第二個 __syncthreads，覆蓋還在使用的 tile

```c
for (int t = 0; t < numTiles; t++) {
    // 載入 tile t
    A_tile[ty][tx] = ...;
    B_tile[ty][tx] = ...;
    __syncthreads();   // 第一個：等載入完

    for (int k = 0; k < TILE_SIZE; k++)
        sum += A_tile[ty][k] * B_tile[k][tx];

    // ← 忘記第二個 __syncthreads
    // 下一輪 t+1 的載入會覆蓋 A_tile，但有 thread 還在讀它！
}
```

### 雷 2：邊界條件漏掉導致讀到垃圾或越界

```c
// ✗ 沒有邊界保護，矩陣大小不是 TILE_SIZE 倍數時越界
A_tile[ty][tx] = A[row * K + t * TILE_SIZE + tx];  // row 可能 >= M

// ✓ 邊界保護：用 0 填充（不影響加法結果）
A_tile[ty][tx] = (row < M && a_col < K) ? A[row * K + a_col] : 0.0f;
```

### 雷 3：2D block 的 row/col 和 threadIdx.x/y 搞反

```c
// threadIdx.x 對應 col（連續方向），threadIdx.y 對應 row
// 搞反後：存取 B 時 threadIdx.y 連續但對應非連續位置 → uncoalesced

// ✗ 錯誤：row 用 threadIdx.x，col 用 threadIdx.y
int row = blockIdx.x * TILE_SIZE + threadIdx.x;  // threadIdx.x 應該是 col
int col = blockIdx.y * TILE_SIZE + threadIdx.y;  // 這樣 B 的存取 uncoalesced
```

### 雷 4：動態 shared memory 的型別對齊問題

```c
extern __shared__ char smem[];
float *A = (float*)smem;
// B 從哪裡開始？
double *B = (double*)(smem + tile * tile * sizeof(float));
// 如果 tile*tile*sizeof(float) 不是 8 的倍數，double 對齊出問題
// 安全做法：
double *B = (double*)((char*)smem + ALIGN_UP(tile*tile*sizeof(float), 8));
```

### 雷 5：tile_size > blockDim.x（或 y）

`TILE_SIZE = 32`，但你 launch `block(16, 16)`——每個 thread 要填一格 tile，但 block 只有 16×16 = 256 個 thread，tile 需要 32×32 = 1024 格。你要麼每個 thread 填多格（需要改 kernel），要麼確保 `TILE_SIZE == blockDim.x == blockDim.y`。

---

## 十、動手練習：觀察 naive 和 tiled 的差異

```python
# Colab cell — 計時對比
%%writefile timing_matmul.cu
#include <cuda_runtime.h>
#include <stdio.h>
#include <stdlib.h>

#define TILE_SIZE 16
// 把上面的 matmul_naive 和 matmul_tiled 貼進來

int main() {
    const int M = 1024, K = 1024, N = 1024;
    // ... 分配、初始化 ...

    cudaEvent_t start, stop;
    cudaEventCreate(&start); cudaEventCreate(&stop);

    dim3 block(TILE_SIZE, TILE_SIZE);
    dim3 grid((N+TILE_SIZE-1)/TILE_SIZE, (M+TILE_SIZE-1)/TILE_SIZE);

    // 暖機
    matmul_naive<<<grid,block>>>(d_A, d_B, d_C, M, K, N);
    cudaDeviceSynchronize();

    // Naive 計時
    cudaEventRecord(start);
    for (int r = 0; r < 10; r++)
        matmul_naive<<<grid,block>>>(d_A, d_B, d_C, M, K, N);
    cudaEventRecord(stop);
    cudaEventSynchronize(stop);
    float ms_naive; cudaEventElapsedTime(&ms_naive, start, stop);
    ms_naive /= 10;

    // Tiled 計時
    cudaEventRecord(start);
    for (int r = 0; r < 10; r++)
        matmul_tiled<<<grid,block>>>(d_A, d_B, d_C, M, K, N);
    cudaEventRecord(stop);
    cudaEventSynchronize(stop);
    float ms_tiled; cudaEventElapsedTime(&ms_tiled, start, stop);
    ms_tiled /= 10;

    double flops = 2.0 * M * K * N;
    printf("Naive: %.2f ms, %.2f GFLOPS\n", ms_naive, flops/ms_naive/1e6);
    printf("Tiled: %.2f ms, %.2f GFLOPS\n", ms_tiled, flops/ms_tiled/1e6);
    printf("Speedup: %.2fx\n", ms_naive / ms_tiled);
    return 0;
}
```

預期輸出（Colab T4，Colab 預期行為，未在本機實測）：

```
Naive: ~22 ms, ~97 GFLOPS
Tiled: ~5 ms, ~429 GFLOPS
Speedup: ~4.4x
```

實際數字會依 Colab 分配到的 T4 狀態而異。Tiled 快 4–8 倍是合理預期。

---

## 本章重點

1. **Shared memory** 是 on-chip SRAM，~20 cycles 延遲（比 global 的 ~500 cycles 快 20 倍），但要程式設計師手動管理
2. **靜態 `__shared__`** 編譯期定大小；**動態 `extern __shared__`** 在 launch 第三個角括號指定大小（bytes）
3. **Tiling 的本質**：把重複使用的資料搬進 shared memory，讓 global memory 的讀取次數從 O(N³) 降到 O(N²)，Arithmetic Intensity 提升 T 倍（T = tile size）
4. **兩個 `__syncthreads()` 都是必要的**：第一個等載入完才算，第二個等算完才覆蓋
5. **Bank conflict** 是 shared memory 的下一個優化目標（Ch 19），現在的 tiled matmul 存取模式恰好沒有 conflict，但搞錯索引順序就會有

---

## 自我檢核

1. Shared memory 和 L1 cache 的差異是什麼？為什麼 shared 需要程式設計師手動管理？
2. `__shared__ float A[16][16]` 和 `extern __shared__ float A[]` 有什麼不同？launch 時如何傳大小給後者？
3. Tiled matmul 中，如果拿掉第二個 `__syncthreads()`，什麼情況下會出錯？（給一個具體例子）
4. TILE_SIZE=16 vs TILE_SIZE=32 各有什麼取捨？為什麼不永遠用 32？
5. 為什麼 `B_tile[k][threadIdx.x]` 這個存取模式沒有 bank conflict？（提示：同一個 warp 的 threadIdx.x 是連續的）

---

## 延伸閱讀

1. **[Mark Harris, "Using Shared Memory in CUDA C/C++"](https://developer.nvidia.com/blog/using-shared-memory-cuda-cc/)**  
   讀哪裡：全文（含靜態/動態宣告、bank conflict 入門）  
   學什麼：Shared memory 的精確宣告語法、bank 分配規則、偵測 conflict 的方法  
   前提：本章

2. **《Programming Massively Parallel Processors》4th ed. Ch 5**（Kirk, Hwu, El Hajj）  
   讀哪裡：Ch 5「Memory Architecture and Data Locality」（tiling 技術最詳盡的教科書推導）  
   學什麼：Tiling 的 formal analysis、memory bandwidth 節省量的數學推導、帶非方陣的 tile 變形  
   前提：Ch 9 記憶體階層 + 本章

3. **[CUDA C++ Programming Guide — Shared Memory](https://docs.nvidia.com/cuda/cuda-c-programming-guide/index.html#shared-memory)**  
   讀哪裡：Shared Memory 章節（含 bank 結構圖、conflict 分析）  
   學什麼：精確的 bank 定義、broadcast 規則、`cudaFuncSetAttribute` 調整 shared/L1 比例  
   前提：本章

4. **[Simon Boehm, "How to Optimize a CUDA Matmul Kernel"](https://siboehm.com/articles/22/CUDA-MMM)**  
   讀哪裡：從 naive 到「Kernel 3: Shared Memory Cache-Blocking」那一節  
   學什麼：tiling 實戰的完整 profiling 數字，用 ncu 看 L2 cache hit rate 的變化  
   前提：本章，最好也有 Ch 25 Nsight 基礎

5. **[CUDA C++ Best Practices Guide — Shared Memory](https://docs.nvidia.com/cuda/cuda-c-best-practices-guide/index.html#shared-memory-and-memory-banks)**  
   讀哪裡：Shared Memory 章節，Avoiding Bank Conflicts 小節  
   學什麼：Bank conflict 如何出現、如何用 padding 消除、偶發性 conflict vs 系統性 conflict 的差異  
   前提：本章 + Ch 19 bank conflict

---

> Tiling 是 GPU 效能優化的第一個「主動武器」。Naive kernel 是受害者（被 memory bandwidth 限制）；Tiled kernel 是主動選擇資料放在哪裡的那個。這是思維轉換的起點，後面的 coalescing、占用率、warp divergence 都是在同一個框架上繼續深挖。

→ [練習 C：矩陣乘法 naive → tiled](./practice-c-matmul.md)
