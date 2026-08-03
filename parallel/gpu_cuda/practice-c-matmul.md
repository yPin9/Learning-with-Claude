# 練習 C — 矩陣乘法 naive → tiled

> **目標**：獨立實作兩版矩陣乘法 kernel——先 naive（直接讀 global memory），再 tiled（利用 shared memory），和 CPU 結果驗證正確性，比較兩版的效能差異，理解 tiling 帶來的 bandwidth 節省。

> **環境**：Colab T4 (sm_75), CUDA 12.x。`%%writefile` + `!nvcc -arch=sm_75` 的流程。

---

## 背景與動機

矩陣乘法（GEMM）是整個 GPU 計算生態的核心：cuBLAS、cuDNN、PyTorch 的矩陣運算、FlashAttention——都在變相呼叫它。搞懂「為什麼 naive 版慢、tiled 版快」，你就掌握了後面所有優化的基礎邏輯。

Ch 17 已經把 tiling 的原理講清楚了，這個練習要你**從空白開始自己實作**——不查 Ch 17 的程式碼，只看規格說明。手動把 tile 的索引寫一遍，才會真正記住為什麼 `B_tile[k][threadIdx.x]` 這樣寫。

---

## 任務規格

### 1. Naive Kernel

每個 thread 負責計算 C 的一個元素，直接從 global memory 讀 A 和 B。

```
輸入：A[M][K]，B[K][N]
輸出：C[M][N]，其中 C[i][j] = ∑_{k=0}^{K-1} A[i][k] × B[k][j]

Block 大小：(16, 16)（2D block，threadIdx.x 對應 col，threadIdx.y 對應 row）
Grid 大小：ceil(N/16) × ceil(M/16)（2D grid，gridIdx.x 對應 col block，gridIdx.y 對應 row block）
```

### 2. Tiled Kernel

用 TILE_SIZE × TILE_SIZE 的 shared memory 分塊，每輪 tile 先搬進 shared，再從 shared 計算點積。

```
Tile size：16 × 16
Block 大小：(16, 16)（和 tile size 一樣）
Grid 大小：同 naive
邊界處理：矩陣大小不是 16 的倍數時，超出範圍的位置填 0.0f
```

### 3. 正確性驗證

CPU 版本用三層 for 迴圈算參考結果，GPU 兩版都和 CPU 結果比對。

判斷標準：`|gpu[i] - cpu[i]| <= 1e-4 * |cpu[i]| + 1e-4`（相對誤差 + 絕對誤差容忍，處理接近 0 的情況）。

### 4. 測試矩陣大小

必測：
- `M=64, K=64, N=64`（完全整除，基本正確性）
- `M=1024, K=1024, N=1024`（標準效能測試點）

選測（延伸挑戰）：
- `M=100, K=200, N=150`（非 16 倍數，邊界處理）
- `M=1, K=1024, N=1024`（極端長條矩陣）

---

## 期望輸出（Colab 預期行為，未在本機實測）

```
=== Matrix Multiplication: M=1024 K=1024 N=1024 ===
CPU reference done.
[Naive ] C[0][0]=CPU_val, C[512][512]=CPU_val  -- CORRECT
[Naive ] Time: 22.34 ms  |  89.6 GFLOPS
[Tiled ] C[0][0]=CPU_val, C[512][512]=CPU_val  -- CORRECT
[Tiled ] Time: 5.12 ms   |  390.8 GFLOPS
Speedup (Tiled vs Naive): 4.36x
```

實際數字隨 Colab 環境波動（T4 可能有其他任務佔用 DRAM bandwidth），但 tiled 通常比 naive 快 3–8 倍。

---

## 卡住了？分步提示

### 提示 1：indexing 的直覺

先在紙上畫：C 的 (row, col) 元素，row 從哪來（blockIdx.y × TILE + threadIdx.y），col 從哪來（blockIdx.x × TILE + threadIdx.x）。

### 提示 2：tile 載入的 thread 分工

一個 block 有 TILE × TILE 個 thread，tile 也是 TILE × TILE 個 float。一個 thread 搬一個 float——完美分工。thread (threadIdx.y, threadIdx.x) 負責搬：

- `A_tile[threadIdx.y][threadIdx.x]` = A 的某一個元素
- `B_tile[threadIdx.y][threadIdx.x]` = B 的某一個元素

問題：這個「某一個」的全域索引是什麼？（答：A 的行是固定的 row，列是 `t * TILE + threadIdx.x`；B 的行是 `t * TILE + threadIdx.y`，列是固定的 col）

### 提示 3：兩個 __syncthreads 的位置

```
載入 A_tile、B_tile
__syncthreads()       ← 等所有 thread 都搬完
計算點積
__syncthreads()       ← 等所有 thread 算完再搬下一個 tile
```

### 提示 4：邊界條件

矩陣大小不是 TILE 倍數時，最後一批 thread 的 `row >= M` 或 `a_col >= K` 等。用三元運算子：

```c
A_tile[ty][tx] = (row < M && a_col < K) ? A[row * K + a_col] : 0.0f;
```

### 提示 5：計時

用 CUDA events：

```c
cudaEvent_t t0, t1;
cudaEventCreate(&t0); cudaEventCreate(&t1);
cudaEventRecord(t0);
// ... kernel launch x 10 次取平均 ...
cudaEventRecord(t1);
cudaEventSynchronize(t1);
float ms; cudaEventElapsedTime(&ms, t0, t1);
ms /= 10;
double gflops = 2.0 * M * K * N / (ms * 1e6);
```

---

## 五步實作計畫

**步驟 1（10 分鐘）**：先寫 CPU 三層迴圈和 host 記憶體管理框架。跑通不涉及 GPU 的部分，確認 CPU reference 沒問題。

**步驟 2（15 分鐘）**：實作 naive kernel。索引：`row = blockIdx.y * blockDim.y + threadIdx.y`，`col = blockIdx.x * blockDim.x + threadIdx.x`。先用 M=K=N=64 跑，和 CPU 比對。

**步驟 3（20 分鐘）**：實作 tiled kernel。先在紙上把 tile 的索引推一遍，再動手寫。重點：兩個 `__syncthreads()` 的位置、邊界保護的三元運算。

**步驟 4（10 分鐘）**：加計時程式碼。用 M=K=N=1024 跑 naive + tiled 各 10 次取平均。

**步驟 5（10 分鐘）**：延伸：改 TILE_SIZE 為 8 和 32，各跑一次，看效能數字怎麼變。

---

## 參考解答

<details>
<summary>點開前先自己試試。真的卡超過 30 分鐘再看。</summary>

### Colab 完整執行流程

```python
%%writefile matmul_practice.cu
#include <cuda_runtime.h>
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <assert.h>

// ─── 設定 ────────────────────────────────────────────────────────────────
#define TILE_SIZE 16

// ─── CPU 參考實作 ─────────────────────────────────────────────────────────
// O(M*K*N) 三層 for，不做任何 GPU 呼叫
void matmul_cpu(const float *A, const float *B, float *C,
                int M, int K, int N)
{
    for (int i = 0; i < M; i++) {
        for (int j = 0; j < N; j++) {
            float acc = 0.0f;
            for (int k = 0; k < K; k++) {
                acc += A[i * K + k] * B[k * N + j];
            }
            C[i * N + j] = acc;
        }
    }
}

// ─── Naive Kernel ─────────────────────────────────────────────────────────
// 每個 thread 計算 C 的一個元素，直接讀 global memory
// threadIdx.x → col 方向（列），threadIdx.y → row 方向（行）
__global__ void matmul_naive(
    const float * __restrict__ A,   // [M][K] row-major
    const float * __restrict__ B,   // [K][N] row-major
    float *C,                        // [M][N] row-major
    int M, int K, int N)
{
    // 算出這個 thread 負責 C 的哪個元素
    int row = blockIdx.y * blockDim.y + threadIdx.y;
    int col = blockIdx.x * blockDim.x + threadIdx.x;

    if (row >= M || col >= N) return;  // 邊界 guard

    float acc = 0.0f;
    // 沿 K 方向做點積，每次都讀 global memory
    for (int k = 0; k < K; k++) {
        acc += A[row * K + k] * B[k * N + col];
    }
    C[row * N + col] = acc;
}

// ─── Tiled Kernel ─────────────────────────────────────────────────────────
// 每次處理 K 方向的一個 TILE_SIZE 片段
// 把對應的 A tile 和 B tile 搬進 shared memory，從 shared 讀做計算
__global__ void matmul_tiled(
    const float * __restrict__ A,
    const float * __restrict__ B,
    float *C,
    int M, int K, int N)
{
    // 這個 thread 負責 C 的 (row, col) 元素
    int row = blockIdx.y * TILE_SIZE + threadIdx.y;
    int col = blockIdx.x * TILE_SIZE + threadIdx.x;

    // 靜態 shared memory：兩個 tile
    __shared__ float A_tile[TILE_SIZE][TILE_SIZE];
    __shared__ float B_tile[TILE_SIZE][TILE_SIZE];

    float acc = 0.0f;
    int numTiles = (K + TILE_SIZE - 1) / TILE_SIZE;

    for (int t = 0; t < numTiles; t++) {

        // ── Phase 1：協作載入 A 的 tile ──────────────────────────────
        // thread (threadIdx.y, threadIdx.x) 載入 A[row][t*T + threadIdx.x]
        int a_col = t * TILE_SIZE + threadIdx.x;
        A_tile[threadIdx.y][threadIdx.x] =
            (row < M && a_col < K) ? A[row * K + a_col] : 0.0f;
        //  ↑ 邊界：row 可能超出 M，a_col 可能超出 K（最後一個 tile）

        // ── Phase 1：協作載入 B 的 tile ──────────────────────────────
        // thread (threadIdx.y, threadIdx.x) 載入 B[t*T + threadIdx.y][col]
        int b_row = t * TILE_SIZE + threadIdx.y;
        B_tile[threadIdx.y][threadIdx.x] =
            (b_row < K && col < N) ? B[b_row * N + col] : 0.0f;
        //  ↑ 邊界：b_row 可能超出 K，col 可能超出 N

        // ── Barrier 1：等所有 thread 都搬完 ─────────────────────────
        __syncthreads();

        // ── Phase 2：從 shared memory 做點積 ─────────────────────────
        // A_tile[threadIdx.y][k] × B_tile[k][threadIdx.x]
        // 注意：A_tile 的行索引是 threadIdx.y（即 row 方向）
        //       B_tile 的列索引是 threadIdx.x（即 col 方向）
        for (int k = 0; k < TILE_SIZE; k++) {
            acc += A_tile[threadIdx.y][k] * B_tile[k][threadIdx.x];
        }

        // ── Barrier 2：等算完，下一 tile 才能覆蓋 shared ─────────────
        __syncthreads();
    }

    // 寫回結果（邊界 guard）
    if (row < M && col < N) {
        C[row * N + col] = acc;
    }
}

// ─── 驗證 ─────────────────────────────────────────────────────────────────
// 回傳 1 表示 PASS，0 表示 FAIL
int verify(const float *ref, const float *test, int size)
{
    int ok = 1;
    for (int i = 0; i < size; i++) {
        float diff = fabsf(ref[i] - test[i]);
        float tol  = 1e-4f * fabsf(ref[i]) + 1e-4f;
        if (diff > tol) {
            printf("  FAIL at [%d]: ref=%.6f test=%.6f diff=%.6f\n",
                   i, ref[i], test[i], diff);
            ok = 0;
            if (i >= 5) { printf("  (more errors suppressed)\n"); break; }
        }
    }
    return ok;
}

// ─── 計時工具 ─────────────────────────────────────────────────────────────
// 跑 kernel 10 次取平均（ms）
typedef void (*KernelFn)(const float*, const float*, float*, int, int, int);

float time_kernel(KernelFn fn, dim3 grid, dim3 block,
                  const float *d_A, const float *d_B, float *d_C,
                  int M, int K, int N, int runs)
{
    cudaEvent_t t0, t1;
    cudaEventCreate(&t0); cudaEventCreate(&t1);

    // 暖機
    fn<<<grid, block>>>(d_A, d_B, d_C, M, K, N);
    cudaDeviceSynchronize();

    cudaEventRecord(t0);
    for (int r = 0; r < runs; r++)
        fn<<<grid, block>>>(d_A, d_B, d_C, M, K, N);
    cudaEventRecord(t1);
    cudaEventSynchronize(t1);

    float ms;
    cudaEventElapsedTime(&ms, t0, t1);
    cudaEventDestroy(t0); cudaEventDestroy(t1);
    return ms / runs;
}

// ─── Main ─────────────────────────────────────────────────────────────────
void run_test(int M, int K, int N, int runs)
{
    printf("\n=== M=%d K=%d N=%d ===\n", M, K, N);

    size_t sA = (size_t)M * K * sizeof(float);
    size_t sB = (size_t)K * N * sizeof(float);
    size_t sC = (size_t)M * N * sizeof(float);

    // Host 分配與初始化
    float *h_A     = (float*)malloc(sA);
    float *h_B     = (float*)malloc(sB);
    float *h_C_cpu = (float*)calloc(M * N, sizeof(float));
    float *h_C_gpu = (float*)malloc(sC);

    // 初始化：值域 [0, 1)，可重複驗
    srand(42);
    for (int i = 0; i < M * K; i++) h_A[i] = (float)(rand() % 100) * 0.01f;
    for (int i = 0; i < K * N; i++) h_B[i] = (float)(rand() % 100) * 0.01f;

    // CPU 參考（M=1024 時約 3–5 秒，可以先用 M=64 debug）
    printf("Running CPU reference...\n");
    matmul_cpu(h_A, h_B, h_C_cpu, M, K, N);
    printf("CPU C[0][0]=%.4f C[M/2][N/2]=%.4f\n",
           h_C_cpu[0], h_C_cpu[(M/2)*N + N/2]);

    // Device 分配
    float *d_A, *d_B, *d_C;
    cudaMalloc(&d_A, sA);
    cudaMalloc(&d_B, sB);
    cudaMalloc(&d_C, sC);
    cudaMemcpy(d_A, h_A, sA, cudaMemcpyHostToDevice);
    cudaMemcpy(d_B, h_B, sB, cudaMemcpyHostToDevice);

    dim3 block(TILE_SIZE, TILE_SIZE);
    dim3 grid((N + TILE_SIZE - 1) / TILE_SIZE,
              (M + TILE_SIZE - 1) / TILE_SIZE);

    // ── Naive ──────────────────────────────────────────────────────
    cudaMemset(d_C, 0, sC);
    float ms_naive = time_kernel(matmul_naive, grid, block,
                                 d_A, d_B, d_C, M, K, N, runs);
    cudaMemcpy(h_C_gpu, d_C, sC, cudaMemcpyDeviceToHost);
    printf("[Naive] %.2f ms  %.1f GFLOPS  --  %s\n",
           ms_naive, 2.0*M*K*N/(ms_naive*1e6),
           verify(h_C_cpu, h_C_gpu, M*N) ? "CORRECT" : "WRONG");

    // ── Tiled ──────────────────────────────────────────────────────
    cudaMemset(d_C, 0, sC);
    float ms_tiled = time_kernel(matmul_tiled, grid, block,
                                 d_A, d_B, d_C, M, K, N, runs);
    cudaMemcpy(h_C_gpu, d_C, sC, cudaMemcpyDeviceToHost);
    printf("[Tiled] %.2f ms  %.1f GFLOPS  --  %s\n",
           ms_tiled, 2.0*M*K*N/(ms_tiled*1e6),
           verify(h_C_cpu, h_C_gpu, M*N) ? "CORRECT" : "WRONG");

    printf("Speedup: %.2fx\n", ms_naive / ms_tiled);

    // 清理
    cudaFree(d_A); cudaFree(d_B); cudaFree(d_C);
    free(h_A); free(h_B); free(h_C_cpu); free(h_C_gpu);
}

int main() {
    // 先小矩陣確認正確性
    run_test(64, 64, 64, 5);

    // 非整除邊界
    run_test(100, 200, 150, 5);

    // 效能對比
    run_test(1024, 1024, 1024, 10);

    return 0;
}
```

### 編譯與執行

```python
# cell 2：編譯
!nvcc -arch=sm_75 -O2 matmul_practice.cu -o matmul_practice

# cell 3：執行
!./matmul_practice
```

### 用 compute-sanitizer 確認 tiled 沒有 race

```python
# cell 4：只對小矩陣跑 racecheck（大矩陣會很慢）
!compute-sanitizer --tool racecheck ./matmul_practice
```

預期：`0 errors`。

### 預期完整輸出（Colab 預期行為，未在本機實測）

```
=== M=64 K=64 N=64 ===
Running CPU reference...
CPU C[0][0]=7.8432 C[M/2][N/2]=8.0213
[Naive] 0.04 ms  13107.2 GFLOPS  --  CORRECT
[Tiled] 0.04 ms  13107.2 GFLOPS  --  CORRECT
Speedup: 1.00x

注意：M=64 太小，GPU 跑太快，計時精度不足以看出差異。
用 M=1024 才能看到顯著差距。

=== M=100 K=200 N=150 ===
Running CPU reference...
CPU C[0][0]=<value> C[M/2][N/2]=<value>
[Naive] 0.05 ms  <GFLOPS>  --  CORRECT
[Tiled] 0.05 ms  <GFLOPS>  --  CORRECT
Speedup: <near 1x at this size>

=== M=1024 K=1024 N=1024 ===
Running CPU reference...
CPU C[0][0]=24.9173 C[M/2][N/2]=24.8821
[Naive] 22.41 ms  95.8 GFLOPS  --  CORRECT
[Tiled]  5.23 ms  410.2 GFLOPS  --  CORRECT
Speedup: 4.29x
```

---

### 動態 Tile Size 版本（延伸）

如果你想試不同 tile size 而不重新編譯：

```python
%%writefile matmul_dynamic.cu
#include <cuda_runtime.h>
#include <stdio.h>
#include <stdlib.h>
#include <math.h>

// 動態 tile kernel：tile_size 在 launch 時決定
__global__ void matmul_tiled_dynamic(
    const float * __restrict__ A,
    const float * __restrict__ B,
    float *C,
    int M, int K, int N, int T)   // T = tile_size
{
    extern __shared__ float smem[];
    float *A_tile = smem;              // 前 T*T 個 float
    float *B_tile = smem + T * T;     // 後 T*T 個 float

    int ty = threadIdx.y, tx = threadIdx.x;
    int row = blockIdx.y * T + ty;
    int col = blockIdx.x * T + tx;

    float acc = 0.0f;
    int numTiles = (K + T - 1) / T;

    for (int t = 0; t < numTiles; t++) {
        int a_col = t * T + tx;
        A_tile[ty * T + tx] = (row < M && a_col < K) ? A[row * K + a_col] : 0.0f;

        int b_row = t * T + ty;
        B_tile[ty * T + tx] = (b_row < K && col < N) ? B[b_row * N + col] : 0.0f;

        __syncthreads();

        for (int k = 0; k < T; k++) {
            acc += A_tile[ty * T + k] * B_tile[k * T + tx];
        }

        __syncthreads();
    }

    if (row < M && col < N) C[row * N + col] = acc;
}

int main() {
    const int M = 1024, K = 1024, N = 1024, RUNS = 10;
    size_t sA = M*K*sizeof(float), sB = K*N*sizeof(float), sC = M*N*sizeof(float);

    float *h_A = (float*)malloc(sA);
    float *h_B = (float*)malloc(sB);
    float *d_A, *d_B, *d_C;
    srand(42);
    for (int i = 0; i < M*K; i++) h_A[i] = (float)(rand()%100)*0.01f;
    for (int i = 0; i < K*N; i++) h_B[i] = (float)(rand()%100)*0.01f;

    cudaMalloc(&d_A, sA); cudaMalloc(&d_B, sB); cudaMalloc(&d_C, sC);
    cudaMemcpy(d_A, h_A, sA, cudaMemcpyHostToDevice);
    cudaMemcpy(d_B, h_B, sB, cudaMemcpyHostToDevice);

    int tile_sizes[] = {8, 16, 32};
    for (int ti = 0; ti < 3; ti++) {
        int T = tile_sizes[ti];
        dim3 block(T, T);
        dim3 grid((N+T-1)/T, (M+T-1)/T);
        size_t smem_bytes = 2 * T * T * sizeof(float);

        // 暖機
        matmul_tiled_dynamic<<<grid, block, smem_bytes>>>(d_A, d_B, d_C, M, K, N, T);
        cudaDeviceSynchronize();

        cudaEvent_t t0, t1;
        cudaEventCreate(&t0); cudaEventCreate(&t1);
        cudaEventRecord(t0);
        for (int r = 0; r < RUNS; r++)
            matmul_tiled_dynamic<<<grid, block, smem_bytes>>>(d_A, d_B, d_C, M, K, N, T);
        cudaEventRecord(t1);
        cudaEventSynchronize(t1);
        float ms; cudaEventElapsedTime(&ms, t0, t1); ms /= RUNS;

        printf("Tile=%2d: %.2f ms  %.1f GFLOPS\n",
               T, ms, 2.0*M*K*N/(ms*1e6));
        cudaEventDestroy(t0); cudaEventDestroy(t1);
    }

    cudaFree(d_A); cudaFree(d_B); cudaFree(d_C);
    free(h_A); free(h_B);
    return 0;
}
```

```python
!nvcc -arch=sm_75 -O2 matmul_dynamic.cu -o matmul_dynamic
!./matmul_dynamic
```

預期輸出（Colab 預期行為，未在本機實測）：

```
Tile= 8: 9.81 ms  219.0 GFLOPS
Tile=16: 5.23 ms  410.2 GFLOPS
Tile=32: 4.71 ms  455.8 GFLOPS
```

Tile=32 理論上稍快（更大的 tile → 更高的 arithmetic intensity），但 block 需要 2×32×32×4 = 8 KB shared memory，佔 SM 資源多，可能降低 occupancy。實際結果依 profiler 才能判斷瓶頸在哪（Ch 25 主題）。

</details>

---

## 測試用例

### 用例 1：最小正確性測試

```python
# M=K=N=4，手算答案驗
# A = [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]（identity）
# B = [[1,2,3,4],[5,6,7,8],[9,10,11,12],[13,14,15,16]]
# C 應該等於 B
```

### 用例 2：非方陣

```
M=3, K=4, N=2
A = [[1,2,3,4],[5,6,7,8],[9,10,11,12]]
B = [[1,2],[3,4],[5,6],[7,8]]
C = [[1*1+2*3+3*5+4*7, ...], ...] = [[50,60],[114,140],[178,220]]
```

### 用例 3：非 TILE_SIZE 倍數

```
M=17, K=17, N=17
必須依賴邊界保護（補 0.0f）才能得到正確結果
```

### 用例 4：單行/單列矩陣

```
M=1, K=1024, N=1（dot product 特例）
M=1, K=1, N=1（純量乘法）
```

---

## 延伸挑戰

### 挑戰 A：非方陣 tiling（rectangular tile）

上面的實作用的是方形 tile（TILE_SIZE × TILE_SIZE）。但有時非方陣更有效：用 M_TILE × K_TILE 的 A tile 和 K_TILE × N_TILE 的 B tile，三個維度的 tile 大小可以不同。

**問題**：如何修改 kernel 支援 `TM × TK` 的 A tile 和 `TK × TN` 的 B tile？block 的 threadIdx 要如何重新組織？

### 挑戰 B：多個 thread 計算多個輸出（Thread Coarsening）

目前每個 thread 算 C 的一個元素。一個 thread 算 C 的 2×2 個元素（register blocking）可以進一步提升 register 的利用率，因為 A_tile 的同一行可以被 row 方向重複使用。

**問題**：修改 kernel，讓每個 thread 算 C 的 4 個元素（2行 × 2列）。這樣 block 的邏輯大小如何改變？shared memory 需要多大？

### 挑戰 C：Profiling 對比

在 Colab 用 Nsight Compute 分析兩版 kernel：

```python
!ncu --set full --target-processes all -o profile_matmul ./matmul_practice
```

看「L2 global cache hit rate」和「Shared memory throughput」——naive 和 tiled 的數字差距能直接驗證 tiling 把 global memory 讀取量壓下來了多少。（Nsight Compute 的完整用法在 Ch 25）

### 挑戰 D：用 compute-sanitizer racecheck 故意觸發 race

把 tiled kernel 的其中一個 `__syncthreads()` 拿掉，重新編譯，用 `racecheck` 跑：

```python
!compute-sanitizer --tool racecheck ./bad_tiled
```

看 sanitizer 報的是什麼 race（RAW? WAR? WAW?）、哪一行、哪個 shared memory 位置。

---

## 自我檢核

在繳交/繼續之前，確認你能回答這些問題（不翻筆記）：

- [ ] Naive kernel 中，C[i][j] 對 global memory 的讀取次數是多少次？
- [ ] Tiled kernel 中，A[i][k] 從 global memory 讀了幾次（對整個 kernel 的執行）？
- [ ] 為什麼 `B_tile[ty][tx]` 的 `tx` 對應 B 的列方向而不是行方向？
- [ ] 如果 K 不是 TILE_SIZE 的倍數，`numTiles = K / TILE_SIZE`（整除）會漏掉什麼？正確的寫法？
- [ ] 拿掉第一個 `__syncthreads()` 的後果？第二個 `__syncthreads()` 呢？
- [ ] TILE_SIZE=32 的 shared memory 占用是多少 bytes per block？T4 每個 SM 最多同時跑幾個這樣的 block？

---

> 矩陣乘法只是 tiling 思想的第一個應用。之後的 stencil（卷積）、reduction、scan——都能用相同的「先搬進 shared，多用幾次，省去重複的 global 讀」的思路優化。這一題打好，後面的優化章節才能快速消化。

→ [Ch 18 memory coalescing：global memory 存取樣式](./18-memory-coalescing.md)
