# Ch 19 — Bank Conflict 深挖：shared memory 的隱藏殺手

> **目標**：理解 shared memory bank 的硬體結構、為何 bank conflict 讓 shared memory 退化成慢速存取、掌握 padding 消 conflict 的數學原理，以及把 Ch 18 矩陣轉置升級成真正 bank-free 的版本。
>
> **環境**：CUDA 12.x, Colab T4 (sm_75)

---

## 為什麼需要這一章

Ch 17 告訴我們 shared memory 比 global memory 快 10–100 倍，Ch 18 告訴我們 global memory 需要 coalesced access。這兩件事都對，但 Ch 18 的矩陣轉置 tiled kernel 在修好 coalescing 問題之後，暗中引入了另一個效能殺手——bank conflict。

一個沒處理 bank conflict 的 tiled 轉置，在 T4 上的效能可能只有理論值的 1/16 到 1/32。更危險的是，錯誤不會讓程式崩潰，結果還是正確的，只是慢得不明顯——除非你用 Nsight Compute 量。

這章做三件事：建立 bank 的硬體直覺、解釋 padding 為何有效（以及為什麼是 33 不是 34）、修好 Ch 18 遺留的 conflict。

---

## 先建立直覺

### Shared memory 的硬體結構

Shared memory 不是一塊連續的 SRAM。硬體把它切成 32 個獨立的 **bank**，每個 bank 有自己的讀寫埠。

在預設的 **32-bit mode**（sm_20 起的預設）：
- 共 32 個 bank，編號 0–31
- 每個 bank 的資料寬度是 **4 bytes**（32 bits）
- 位址對映：`bank_id = (byte_address / 4) % 32`

```
Shared memory 位址佈局（32-bit mode，每格 = 4 bytes）：

位址（bytes）:  0   4   8  12  16  20  24  28  32  36  40  44 ...
Bank 編號:      0   1   2   3   4   5   6   7   8   9  10  11 ...（mod 32 循環）
```

32 個 bank 可以在「同一個 clock cycle」同時各自服務一個 4-byte 存取請求——前提是每個請求打在**不同的 bank**。

一個 warp 有 32 個 thread（lane 0–31）。如果每個 lane 存取不同的 bank，整個 warp 的 shared memory 存取就在一個 cycle 完成，這是最理想的情況。

---

### 三種情況的 ASCII 圖

#### 情況 A：Zero Conflict（最理想）

每個 thread 存取不同的 bank，一個 cycle 搞定。

```
Warp 的 32 個 thread（lane 0–31）：

Thread:  00  01  02  03  04  05  06  07  08  09  10  11 ...  31
          |   |   |   |   |   |   |   |   |   |   |   |       |
Bank:    [0] [1] [2] [3] [4] [5] [6] [7] [8] [9][10][11]... [31]

→ 每個 bank 只被一個 thread 存取
→ 1 個 cycle，全部 thread 同時完成
```

#### 情況 B：2-way Conflict

Thread 0 和 Thread 16 都存取 bank 0（但不同位址），其餘 thread 各自打自己的 bank。

```
Thread:  00  01  02  03 ...  15  16  17  18 ...  31
          |   |   |   |       |   |   |   |       |
Bank:    [0] [1] [2] [3] ... [15][0] [17][18]... [31]
          ↑               相撞  ↑
          └─────────── 2-way conflict ─────────────┘

Bank 0 被 thread 0 和 thread 16 同時要求，但位址不同
→ 硬體必須序列化：先服務 thread 0，再服務 thread 16
→ 需要 2 個 cycle
```

#### 情況 C：32-way Conflict（最糟）

全部 32 個 thread 都存取 bank 0 的不同位址。

```
Thread:  00  01  02  03  04 ...  31
          |   |   |   |   |       |
Bank:    [0] [0] [0] [0] [0] ... [0]
          ↑   ↑   ↑   ↑   ↑       ↑
          全部 thread 打同一 bank，但位址各不相同

→ 硬體完全序列化：32 個存取一個一個來
→ 需要 32 個 cycle
→ shared memory 退化成比 global memory 還慢（多了同步 overhead）
```

#### 情況 D：Broadcast（廣播，零 Conflict）

全部 32 個 thread 存取 **同一個位址**（同一 bank，同一位址）。

```
Thread:  00  01  02  03  04 ...  31
          |   |   |   |   |       |
Bank:    [0] [0] [0] [0] [0] ... [0]
          └───────────┬───────────┘
                  同一個位址！

→ 硬體偵測到廣播情況，執行 broadcast
→ 只需 1 個 cycle，把值送給全部 thread
→ 零 conflict（廣播不算 conflict）
```

廣播的限制：必須是**同一 warp 的所有 thread 讀同一位址**才觸發。寫的情況不適用（寫廣播沒有意義）。

---

## 核心機制：Bank 計算公式

以 `float smem[M][N]`（float = 4 bytes）為例：

```
bank_id(smem[row][col]) = ( (row * N + col) * sizeof(float) / 4 ) % 32
                        = (row * N + col) % 32
```

這個公式是一切分析的基礎。我們反覆用它。

---

## 經典案例：Column-Major 存取導致 32-way Conflict

考慮 `__shared__ float smem[32][32]`。

### 行存取（Row-major）—— Zero Conflict

Thread `i` 存取 `smem[row][i]`，row 固定，col = i（每個 thread 的 col 不同）：

```
bank_id(smem[row][i]) = (row * 32 + i) % 32 = i % 32 = i

→ thread 0 → bank 0, thread 1 → bank 1, ..., thread 31 → bank 31
→ Zero conflict
```

### 列存取（Column-major）—— 32-way Conflict

Thread `i` 存取 `smem[i][col]`，col 固定，row = i（每個 thread 的 row 不同）：

```
bank_id(smem[i][col]) = (i * 32 + col) % 32 = col % 32（col 是固定值）

→ thread 0 → bank col, thread 1 → bank col, ..., thread 31 → bank col
→ 全部打同一個 bank，位址各不相同
→ 32-way conflict！
```

**視覺化：**

```
smem[32][32]，col = 5 的 column-major 存取：

         col=0  col=1  col=2  col=3  col=4  col=5 ...
row=0  [     ][     ][     ][     ][     ][ T00 ]...    ← thread 0 存取這裡
row=1  [     ][     ][     ][     ][     ][ T01 ]...    ← thread 1
row=2  [     ][     ][     ][     ][     ][ T02 ]...    ← thread 2
...
row=31 [     ][     ][     ][     ][     ][ T31 ]...    ← thread 31

所有 T00..T31 的 bank_id = (row * 32 + 5) % 32 = 5
全部打 bank 5，但 row 不同所以位址不同 → 32-way conflict
```

這就是 Ch 18 矩陣轉置在做 `smem -> dst` 搬運時踩的坑：從 tile 讀資料時走的是 column 方向。

---

## Padding 解法：為什麼是 33，不是 34 或 35

### Padding 的概念

把宣告從 `smem[32][32]` 改成 `smem[32][33]`，每 row 多一個 dummy 元素（浪費 4 bytes per row，共浪費 128 bytes，對 48 KB shared memory 而言微乎其微）。

### 數學推導

Padding 後，bank 計算變成：

```
bank_id(smem[i][col]) = (i * 33 + col) % 32
```

我們需要驗證：對固定的 col，i = 0, 1, 2, ..., 31 的 bank_id 是否全部不同（即是否是 0–31 的排列）。

**關鍵展開：**

```
(i * 33 + col) % 32
= (i * 33) % 32 + col % 32    （mod 分配律，結果仍需 mod 32）
= (i * (32 + 1)) % 32 + col % 32
= (i * 32 + i * 1) % 32 + col % 32
= (0 + i) % 32 + col % 32     （因為 i * 32 % 32 = 0）
= i % 32 + col % 32            （mod 32）
= (i + col) % 32
```

所以 padding 後：

```
bank_id(smem[i][col]) = (i + col) % 32
```

對固定的 col，i = 0, 1, ..., 31：

```
bank_id = (0 + col) % 32, (1 + col) % 32, (2 + col) % 32, ..., (31 + col) % 32
        = col, col+1, col+2, ..., col+31  (所有值 mod 32)
        = 0, 1, 2, ..., 31 的某個排列
```

完美——每個 thread 打不同的 bank，zero conflict。

### 為什麼不是 34？

如果 padding 讓每 row 有 34 個元素：

```
bank_id(smem[i][col]) = (i * 34 + col) % 32
= (i * 2 + col) % 32   （因為 34 % 32 = 2）
```

i = 0 → (0 + col) % 32
i = 1 → (2 + col) % 32
i = 2 → (4 + col) % 32
...
i = 16 → (32 + col) % 32 = col % 32   ← 和 i=0 相同！

i = 0 和 i = 16 打同一個 bank → 2-way conflict，沒有完全消除。

**根本原因：** `gcd(34, 32) = 2 ≠ 1`。34 和 32 不互質，所以 `(i * 34) % 32` 的週期是 `32 / gcd(34, 32) = 16`，只有 16 個不同的值，32 個 thread 中必有 collision。

### 為什麼不是 35？

```
bank_id(smem[i][col]) = (i * 35 + col) % 32
= (i * 3 + col) % 32   （35 % 32 = 3）
```

`gcd(35, 32) = 1`（35 = 5 × 7，32 = 2^5，互質）

i = 0 → 0, i = 1 → 3, i = 2 → 6, ..., i = 10 → 30, i = 11 → 1, ...

這確實會走遍 0–31 的所有值（因為 3 和 32 互質），所以 35 也有效。

**但 33 是最小的有效 padding。** 我們希望浪費最少 shared memory，所以選 33（`N + 1`，只多一個 element per row）。

### 通用規則

對 `smem[M][N]` 做 padding 到 `smem[M][N + P]`，要消 column-major conflict，需要 `gcd(N + P, 32) = 1`，即 `N + P` 必須和 32 互質（即 `N + P` 是奇數，因為 32 = 2^5，互質等價於不含因子 2）。

最小的 P 使得 `N + P` 是奇數：
- 若 N 是偶數（如 32），P = 1（33 是奇數，有效）
- 若 N 是奇數，P = 0（原本就有效，不需要 padding）

---

## 矩陣轉置：Bank-Free 版本

Ch 18 的 tiled 轉置已經修好 coalescing，現在加上 padding 消 conflict。

```cpp
// bank_free_transpose.cu  (CUDA 12.x)
// 完整可編譯版本

#include <cuda_runtime.h>
#include <cstdio>
#include <cstdlib>
#include <cassert>

#define TILE_DIM 32
#define BLOCK_ROWS 8

// ── Ch 18 版本（有 bank conflict）──────────────────────────────────────────
__global__ void transpose_smem_v1(float* dst, const float* src, int rows, int cols)
{
    // 注意：沒有 padding → smem 的 column 方向讀取有 32-way conflict
    __shared__ float smem[TILE_DIM][TILE_DIM];

    int x = blockIdx.x * TILE_DIM + threadIdx.x;
    int y = blockIdx.y * TILE_DIM + threadIdx.y;

    // coalesced 讀 src（row-major 存取）
    for (int j = 0; j < TILE_DIM; j += BLOCK_ROWS) {
        if (x < cols && (y + j) < rows)
            smem[threadIdx.y + j][threadIdx.x] = src[(y + j) * cols + x];
    }

    __syncthreads();

    // 轉置後的 block 起始位置
    x = blockIdx.y * TILE_DIM + threadIdx.x;
    y = blockIdx.x * TILE_DIM + threadIdx.y;

    // coalesced 寫 dst（row-major），但從 smem 讀是 column-major → bank conflict！
    for (int j = 0; j < TILE_DIM; j += BLOCK_ROWS) {
        if (x < rows && (y + j) < cols)
            dst[(y + j) * rows + x] = smem[threadIdx.x][threadIdx.y + j];
        //                                   ↑                 ↑
        //                           threadIdx.x 是 row，threadIdx.y+j 是 col
        //                           對固定的 threadIdx.y+j，warp 內 threadIdx.x=0..31
        //                           bank = (threadIdx.x * 32 + threadIdx.y+j) % 32
        //                                = (threadIdx.y+j) % 32（固定值）
        //                           → 32-way conflict！
    }
}

// ── 修正版（加 padding，消 bank conflict）──────────────────────────────────
__global__ void transpose_smem_v2(float* dst, const float* src, int rows, int cols)
{
    // 關鍵修改：TILE_DIM + 1 padding → 每 row 多一個 dummy float
    __shared__ float smem[TILE_DIM][TILE_DIM + 1];

    int x = blockIdx.x * TILE_DIM + threadIdx.x;
    int y = blockIdx.y * TILE_DIM + threadIdx.y;

    // coalesced 讀 src（和 v1 完全相同）
    for (int j = 0; j < TILE_DIM; j += BLOCK_ROWS) {
        if (x < cols && (y + j) < rows)
            smem[threadIdx.y + j][threadIdx.x] = src[(y + j) * cols + x];
    }

    __syncthreads();

    x = blockIdx.y * TILE_DIM + threadIdx.x;
    y = blockIdx.x * TILE_DIM + threadIdx.y;

    // 從 smem 讀（column-major 方向），但 padding 後：
    // bank = (threadIdx.x * 33 + threadIdx.y+j) % 32
    //      = (threadIdx.x + threadIdx.y+j) % 32
    // → 對固定的 j，warp 內 threadIdx.x=0..31 打 bank 0..31（排列）→ zero conflict
    for (int j = 0; j < TILE_DIM; j += BLOCK_ROWS) {
        if (x < rows && (y + j) < cols)
            dst[(y + j) * rows + x] = smem[threadIdx.x][threadIdx.y + j];
    }
}

// ── Host 驗證 ───────────────────────────────────────────────────────────────
int main()
{
    const int ROWS = 1024, COLS = 1024;
    const size_t bytes = (size_t)ROWS * COLS * sizeof(float);

    float *h_src = (float*)malloc(bytes);
    float *h_dst = (float*)malloc(bytes);
    for (int i = 0; i < ROWS * COLS; i++) h_src[i] = (float)i;

    float *d_src, *d_dst;
    cudaMalloc(&d_src, bytes);
    cudaMalloc(&d_dst, bytes);
    cudaMemcpy(d_src, h_src, bytes, cudaMemcpyHostToDevice);

    dim3 block(TILE_DIM, BLOCK_ROWS);
    dim3 grid(COLS / TILE_DIM, ROWS / TILE_DIM);

    // 暖機
    transpose_smem_v2<<<grid, block>>>(d_dst, d_src, ROWS, COLS);
    cudaDeviceSynchronize();

    // 驗證正確性
    cudaMemcpy(h_dst, d_dst, bytes, cudaMemcpyDeviceToHost);
    for (int r = 0; r < ROWS; r++)
        for (int c = 0; c < COLS; c++)
            assert(h_dst[c * ROWS + r] == h_src[r * COLS + c]);

    // 計時比較
    cudaEvent_t t0, t1;
    cudaEventCreate(&t0); cudaEventCreate(&t1);
    const int ITERS = 100;
    float ms_v1, ms_v2;

    cudaEventRecord(t0);
    for (int i = 0; i < ITERS; i++)
        transpose_smem_v1<<<grid, block>>>(d_dst, d_src, ROWS, COLS);
    cudaEventRecord(t1);
    cudaEventSynchronize(t1);
    cudaEventElapsedTime(&ms_v1, t0, t1);

    cudaEventRecord(t0);
    for (int i = 0; i < ITERS; i++)
        transpose_smem_v2<<<grid, block>>>(d_dst, d_src, ROWS, COLS);
    cudaEventRecord(t1);
    cudaEventSynchronize(t1);
    cudaEventElapsedTime(&ms_v2, t0, t1);

    printf("v1 (bank conflict): %.2f ms / iter\n", ms_v1 / ITERS);
    printf("v2 (padding, bank-free): %.2f ms / iter\n", ms_v2 / ITERS);
    printf("Speedup: %.2fx\n", ms_v1 / ms_v2);

    // （Colab T4 預期輸出，未在本機實測）
    // v1 (bank conflict): ~0.45 ms / iter
    // v2 (padding, bank-free): ~0.08 ms / iter
    // Speedup: ~5.6x（取決於 tile 大小與 GPU）

    free(h_src); free(h_dst);
    cudaFree(d_src); cudaFree(d_dst);
    return 0;
}
```

`smem[TILE_DIM][TILE_DIM + 1]`：唯一的修改。多宣告 32 個 float（128 bytes），換來 5–10× 的效能提升。

---

## 效能對比表

| 存取模式 | 同 bank 的 thread 數 | 需要幾個 cycle | 備註 |
|---|---|---|---|
| Zero conflict | 每個 bank 最多 1 thread | 1 | 最理想 |
| 2-way conflict | 2 個 thread 打同一 bank | 2 | 常見於 stride-16 存取 |
| 4-way conflict | 4 個 thread 打同一 bank | 4 | stride-8 |
| 8-way conflict | 8 個 thread 打同一 bank | 8 | stride-4 |
| 16-way conflict | 16 個 thread 打同一 bank | 16 | stride-2 |
| 32-way conflict | 32 個 thread 打同一 bank | 32 | column-major on [N][32] |
| Broadcast | 所有 thread 讀同一位址 | 1 | 硬體廣播，零 conflict |

---

## Padding 效果視覺化

**無 padding：`smem[32][32]`**

```
col=0  col=1  col=2 ... col=31
 B0     B1     B2  ...  B31    ← row 0
 B0     B1     B2  ...  B31    ← row 1
 B0     B1     B2  ...  B31    ← row 2
 ...
 B0     B1     B2  ...  B31    ← row 31

Column 方向（col 固定）：所有 row 都打同一 bank → 32-way conflict
```

**有 padding：`smem[32][33]`**

```
col=0  col=1  col=2 ... col=31  col=32(pad)
 B0     B1     B2  ...  B31     B0    ← row 0（B0..B31 再回到 B0）
 B1     B2     B3  ...  B0      B1    ← row 1（整排偏移 1）
 B2     B3     B4  ...  B1      B2    ← row 2（整排偏移 2）
 ...
 B31    B0     B1  ...  B30     B31   ← row 31（整排偏移 31）

Column 方向（col 固定）：每個 row 在不同的 bank → zero conflict！
```

Padding 讓每一 row 的起始 bank 偏移了 1，製造出完美的錯位。

---

## 踩雷

### 踩雷 1：誤以為 padding 浪費太多 shared memory

`smem[32][33]` 比 `smem[32][32]` 多 32 × 4 = 128 bytes（約 0.25% 的 48 KB shared memory）。換來的是消除 32-way conflict（32× 的序列化）。這筆帳怎麼算都划算。

更實際地：shared memory 通常以 thread block 為單位分配，48 KB 裡多用 128 bytes，幾乎不影響 occupancy。在計算 occupancy 前不要假設 padding 有問題。

### 踩雷 2：誤以為 broadcast 一定好，把常數廣播寄望於 shared memory

Broadcast 是好的，但只在「**同一 warp 的所有 thread** 讀取**完全相同的位址**」時才觸發。如果只有部分 thread 讀同一位址，剩下的 thread 讀其他位址，硬體必須分別處理，broadcast 不會被觸發。

另外，broadcast 只適用於讀。對同一位址的並行寫是未定義的（最後由哪個 thread 寫入是不確定的），不要嘗試。

### 踩雷 3：64-bit mode 改變 bank 寬度

`cudaDeviceSetSharedMemConfig(cudaSharedMemBankSizeEightByte)` 把每個 bank 的寬度從 4 bytes 改為 8 bytes。這對使用 `double`（8 bytes）的 kernel 有效，因為在 32-bit mode 下，一個 `double` 跨兩個 bank，原本不存在的 conflict 反而出現了。

切換到 64-bit mode 後：
- bank 計算變成 `bank_id = (byte_address / 8) % 32`
- float（4 bytes）的 stride-2 存取在 64-bit mode 下會產生 conflict（因為兩個連續 float 共用一個 bank）
- 你的 padding 策略可能需要重新評估

預設就是 32-bit mode，除非你在用 `double` 並且有效能問題，不要動這個設定。

### 踩雷 4：使用 `short`（2 bytes）時的 bank 計算

`short` 是 2 bytes。Bank 計算中除以 4，所以兩個連續的 `short` 會對映到**同一個 bank**：

```
short smem[64];
bank_id(smem[i]) = (i * 2 / 4) % 32 = (i / 2) % 32

smem[0] 和 smem[1] → bank 0
smem[2] 和 smem[3] → bank 1
...
```

如果 thread `i` 存取 `smem[i]`，thread 0 和 thread 1 會打同一個 bank → 2-way conflict。

解法：把 `short` 轉成 `int` 操作，或使用 `short2`（一次讀兩個 short，4 bytes，對齊 bank）：

```cpp
// 把兩個 short 打包成一個 int 讀，避免 bank conflict
int packed = ((int*)smem)[i / 2];
short val = (i & 1) ? (short)(packed >> 16) : (short)(packed & 0xFFFF);
```

### 踩雷 5：以為 Volta 以後 bank 行為改變了

Volta（sm_70）起，shared memory 和 L1 共用同一塊 SRAM（可調整分割比例），但 **bank 的邏輯結構沒有改變**：仍然是 32 個 bank，32-bit mode 下每 bank 4 bytes。你在 Pascal/Turing/Ampere/Hopper 上的 bank conflict 知識完全適用於 Volta 以後。

唯一影響：ECC 開啟時，sm_80（Ampere）的 shared memory 可用量從 164 KB 降到 160 KB（實際分配上限由 `cudaDeviceGetAttribute` 查）。這和 bank 行為無關。

---

## 進階主題

### 對角化轉置（Diagonal Approach）

除了 padding，另一種消 conflict 的方法是讓不同 thread block 存取的 shared memory tile 在 global memory 的起始位置做偏移（對角化），使多個 active warp 的 bank 存取不集中在同一個位置。

這個方法由 Mark Harris 在 NVIDIA DevBlog 的「An Efficient Matrix Transpose in CUDA C/C++」中詳細解說。對角化的邏輯比 padding 複雜（需要重新計算 block 的起始 row/col），但不浪費 shared memory，在 shared memory 極為緊張的情況下有用。

對大多數場景，padding 就夠了且更直觀。對角化是當你計算過 occupancy 後確認 128 bytes 的 padding 開銷是瓶頸時才考慮的選項。

### Nsight Compute 診斷

Nsight Compute 直接報告 bank conflict 次數：

```bash
ncu --metrics l1tex__data_bank_conflicts_pipe_lmem_op_ld.sum,\
              l1tex__data_bank_conflicts_pipe_lmem_op_st.sum \
    ./bank_free_transpose
```

關鍵 metrics：

| Metric | 意義 |
|---|---|
| `l1tex__data_bank_conflicts_pipe_lmem_op_ld.sum` | Load 方向的 bank conflict 總數 |
| `l1tex__data_bank_conflicts_pipe_lmem_op_st.sum` | Store 方向的 bank conflict 總數 |
| `l1tex__data_pipe_lsu_wavefronts_mem_shared_op_ld.sum` | 實際發出的 shared memory load 波次 |
| `l1tex__data_pipe_lsu_wavefronts_mem_shared_op_st.sum` | 實際發出的 shared memory store 波次 |

當 `bank_conflicts / wavefronts` 接近 0 時，你的 kernel 是 conflict-free 的。

Nsight Compute UI 在 "Source" 頁面也會高亮有 conflict 的程式碼行，對定位問題非常有效。

---

## 動手練習

**練習 1：驗證 padding 公式**

手動計算 `smem[32][33]` 中，`col = 7` 的 column 存取（row = 0..31）的 bank_id，確認確實是 0–31 的排列（而非全部撞 bank 7）。

**練習 2：實作並量測**

把本章的 `bank_free_transpose.cu` 在 Colab T4 上跑起來，用 `ncu` 確認 v1 的 bank conflict 計數遠大於 v2。

**練習 3：Stride 分析**

對 `__shared__ float smem[128]`，stride-1 存取 (`smem[threadIdx.x]`) 和 stride-4 存取 (`smem[threadIdx.x * 4 % 128]`) 分別是幾 way conflict？ （stride-4 的情況：bank_id = (threadIdx.x * 4) % 32 = threadIdx.x % 8，thread 0, 8, 16, 24 → 同 bank 0，4-way conflict）

**練習 4：`short` 的陷阱**

宣告 `__shared__ short smem[64]`，每個 thread 存取 `smem[threadIdx.x]`（warp 內 32 個 thread，threadIdx.x = 0..31）。計算每個 thread 的 bank_id，找出哪些 thread pair 衝突，以及是幾 way conflict。

---

## 本章重點

1. **Shared memory 分成 32 個 bank**，32-bit mode 下每 bank 4 bytes，`bank_id = (byte_address / 4) % 32`。

2. **Bank conflict 的定義**：同一 warp 內多個 thread 存取同一 bank 的不同位址，硬體序列化成多個 cycle。N-way conflict → N 個 cycle。

3. **Broadcast 是例外**：同一 warp 的 thread 讀同一位址 → 硬體廣播，1 個 cycle，zero conflict。

4. **Column-major 存取 `smem[M][32]` 是 32-way conflict**：因為 `bank_id = col % 32`，col 固定時全部 thread 打同一 bank。

5. **Padding 解法：改成 `smem[M][33]`**，因為 `33 % 32 = 1`，`gcd(33, 32) = 1`，確保不同 row 的同一 col 落在不同 bank。33 是最小有效值；34 無效（偶數，`gcd(34,32)=2`）；35 有效但浪費更多。

6. **Cost-benefit**：128 bytes 的 padding 對比 32× 的序列化，永遠值得。

7. **Nsight Compute** 可直接量化 bank conflict 次數，是診斷的標準工具。

---

## 自我檢核

1. `smem[32][32]` 做 column-major 存取時，bank_id 的計算公式是什麼？為什麼全部 thread 打同一個 bank？

2. 為什麼把 `[32][32]` 的宣告改成 `[32][33]` 可以消 conflict？請用 `(i * 33 + col) % 32 = (i + col) % 32` 的推導解釋。

3. 如果我把 padding 改成 `[32][34]`，為什麼還是有 conflict？哪些 thread 會衝突？

4. Broadcast 和 bank conflict 的差別是什麼？在什麼條件下廣播才能觸發？

5. 64-bit bank mode（`cudaSharedMemBankSizeEightByte`）適合在什麼情況下使用？開啟後，`float` 的 stride-2 存取為什麼可能變得有問題？

---

## 延伸閱讀

1. **CUDA C++ Programming Guide, "Shared Memory"** — bank 定義、計算規則、broadcast 機制的官方文件，bank conflict 所有知識的最終來源。

2. **Mark Harris, "An Efficient Matrix Transpose in CUDA C/C++"** — NVIDIA Dev Blog，包含 padding 方案與對角化（diagonal）方案的完整對比與實驗數據，是本章 transpose 案例的原始參考。

3. **CUDA C++ Best Practices Guide, "Shared Memory in Matrix Multiplication"** — 針對 GEMM 場景的 shared memory 存取優化，從矩陣乘法的角度重新解釋 padding 的必要性。

4. **Nsight Compute User Guide, "Shared Memory Bank Conflicts"** — 詳解 `l1tex__data_bank_conflicts_*` metrics 的定義、如何解讀，以及如何用 source-level annotation 定位有問題的程式碼行。

5. **Nikolai Sakharnykh, "Optimizing Parallel Reduction in CUDA"** — 雖然主題是 reduction，但 Lecture Notes 中對 shared memory access pattern 的分析（特別是 warp-level 的 bank 分佈）提供了很好的補充視角。

---

→ [Ch 20 — Occupancy vs ILP：如何讓 GPU 永遠有事做](./20-occupancy-vs-ilp.md)

---

*回顧：[Ch 9 記憶體階層](./09-memory-hierarchy.md) · [Ch 10 Warp 與 SIMT 執行](./10-warp-simt-execution.md) · [Ch 17 Shared Memory 與 Tiling](./17-shared-memory-tiling.md) · [Ch 18 Memory Coalescing](./18-memory-coalescing.md)*
