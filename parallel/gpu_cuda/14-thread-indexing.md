# Ch 14 — Thread 階層與索引映射：1D/2D/3D Grid

> **目標**：掌握 CUDA thread 階層的三個維度（thread, block, grid），學會把 1D/2D/3D 的資料結構（陣列、矩陣、影像、3D 體素）對應到對應維度的 grid/block，並理解 block size 為什麼常選 128 或 256（而不是 100 或 512），以及 grid-stride loop 在超大資料時的正確用法。

> **環境**：CUDA 12.x, Colab T4 (sm_75)

---

## 為什麼索引映射值得單獨一章？

Ch 12 給的 global index 公式 `int i = blockIdx.x * blockDim.x + threadIdx.x` 對 1D 問題夠用。但現實世界的資料大量是 2D 的：矩陣運算、影像處理、卷積、物理模擬（網格）。把一個 2D 問題硬壓進 1D index 可以做，但：

1. **可讀性變差**：`row = i / width`、`col = i % width` 散落在 kernel 裡，邏輯不清晰。
2. **優化機會損失**：2D grid/block 讓 row-major 存取更自然地 coalesced（Ch 18 詳挖），用 1D 攤平後需要額外思考。
3. **3D 問題更麻煩**：3D 體素渲染、CNN 的 activation tensor（N×C×H×W），用 1D 寫出 `n = i / (C*H*W)`、`c = (i / (H*W)) % C`...這是在製造 bug。

CUDA 提供 3D 的 grid 和 block——學會用正確的維度映射正確的資料是工程基本功。

---

## 先建立直覺：Thread 階層全貌

```
CUDA Thread 階層（3 層）

Grid（整個 kernel launch）
│  gridDim.x × gridDim.y × gridDim.z 個 Block
│
├── Block(0,0)           Block(1,0)           Block(2,0)
│   ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│   │ blockDim.x × blockDim.y × blockDim.z 個 Thread │ ...
│   │ T(0,0)  T(1,0)  │  │ T(0,0)  T(1,0)  │  │ ...
│   │ T(0,1)  T(1,1)  │  │ T(0,1)  T(1,1)  │  │
│   └─────────────────┘  └─────────────────┘  └─────────────────┘
├── Block(0,1)  ...
└── ...
```

**硬體限制（T4 / Turing）：**

| 參數 | 限制 | 說明 |
|------|------|------|
| `blockDim.x × blockDim.y × blockDim.z` | ≤ 1024 | 每個 block 的 thread 總數 |
| `blockDim.x` | ≤ 1024 | 單一維度 |
| `blockDim.y` | ≤ 1024 | 單一維度 |
| `blockDim.z` | ≤ 64  | z 維度特別受限 |
| `gridDim.x`  | ≤ 2^31 - 1 | 幾乎無限 |
| `gridDim.y`  | ≤ 65535 | |
| `gridDim.z`  | ≤ 65535 | |

這些數字來自 CUDA C++ Programming Guide Appendix K（Compute Capability 7.5，即 Turing/T4）。

---

## 1D Grid：最簡單的情況

Ch 12 已經涵蓋，這裡簡短回顧。

```
1D Grid, 1D Block：
  gridDim = (gridDim.x, 1, 1)
  blockDim = (blockDim.x, 1, 1)

global index：
  int i = blockIdx.x * blockDim.x + threadIdx.x;
```

適用：一維陣列、向量運算（vector add、saxpy、elementwise activation）。

---

## 2D Grid：矩陣與影像的天然映射

### 為什麼用 2D？

假設你要對一張 1920×1080 的影像做每像素的處理。你可以：

**方案 A（1D）**：把影像攤平成 1920×1080 = 2,073,600 個元素的 1D 陣列，算 `row = i / 1920`、`col = i % 1920`。

**方案 B（2D）**：用 2D grid，直接讓 `(col, row) = (blockIdx.x * blockDim.x + threadIdx.x, blockIdx.y * blockDim.y + threadIdx.y)`。

方案 B 的 index 計算更直觀，kernel 邏輯更清晰，而且 coalescing 行為更容易推理（相同 row 的 thread 有相鄰的 col → 相鄰的記憶體位址 → coalesced）。

### 2D 索引公式

```
2D Grid, 2D Block：
  gridDim  = (num_blocks_x, num_blocks_y, 1)
  blockDim = (block_x, block_y, 1)

在 kernel 內：
  int col = blockIdx.x * blockDim.x + threadIdx.x;   // x 方向
  int row = blockIdx.y * blockDim.y + threadIdx.y;   // y 方向
```

視覺化（假設矩陣 8×8，block size 4×4）：

```
矩陣（row-major，C 的 array layout）：

      col →
      0    1    2    3    4    5    6    7
row  ┌────┬────┬────┬────┬────┬────┬────┬────┐
 ↓ 0 │ 00 │ 01 │ 02 │ 03 │ 04 │ 05 │ 06 │ 07 │
   1 │ 10 │ 11 │ 12 │ 13 │ 14 │ 15 │ 16 │ 17 │
   2 │ 20 │ 21 │ 22 │ 23 │ 24 │ 25 │ 26 │ 27 │
   3 │ 30 │ 31 │ 32 │ 33 │ 34 │ 35 │ 36 │ 37 │
   4 │ 40 │ 41 │ 42 │ 43 │ 44 │ 45 │ 46 │ 47 │
   5 │ 50 │ 51 │ 52 │ 53 │ 54 │ 55 │ 56 │ 57 │
   6 │ 60 │ 61 │ 62 │ 63 │ 64 │ 65 │ 66 │ 67 │
   7 │ 70 │ 71 │ 72 │ 73 │ 74 │ 75 │ 76 │ 77 │
     └────┴────┴────┴────┴────┴────┴────┴────┘

gridDim = (2, 2), blockDim = (4, 4)：

Block(0,0)          Block(1,0)
covers (col 0-3, row 0-3)  covers (col 4-7, row 0-3)

Block(0,1)          Block(1,1)
covers (col 0-3, row 4-7)  covers (col 4-7, row 4-7)

每個 block 的 thread(threadIdx.x, threadIdx.y) 映射：
  col = blockIdx.x * 4 + threadIdx.x
  row = blockIdx.y * 4 + threadIdx.y
```

注意：CUDA 的 threadIdx.x 對應「比較快速變化的維度」——在 block 內，同一個 warp 裡的 32 個 thread 有相同的 `threadIdx.y` 但 `threadIdx.x` 連續（從 0 到 31）。這意味著 x 方向（col 方向）是 warp 內的並排維度，存取 row-major 矩陣時，同一個 warp 的 thread 存取同一行的連續元素——天然 coalesced。

### 線性記憶體位址計算

GPU 記憶體是 1D 的——`cudaMalloc` 分配的是一塊連續位址。2D 矩陣儲存在 1D 記憶體中（row-major，和 C 的 `A[row][col]` 一樣）：

```
元素 (row, col) 在 1D 記憶體中的位置：
  linear_index = row * width + col

在 kernel 中存取矩陣元素：
  data[row * width + col]
```

這個對應關係非常重要：

```cuda
__global__ void matrix_scale(float *data, float factor, int rows, int cols) {
    int col = blockIdx.x * blockDim.x + threadIdx.x;
    int row = blockIdx.y * blockDim.y + threadIdx.y;

    if (col < cols && row < rows) {
        // row * cols + col：把 2D index 轉成 1D 線性位址
        data[row * cols + col] *= factor;
    }
}
```

---

## 核心範例 1：矩陣加法

```cuda
// 檔案：matrix_add.cu
// 在 Colab 跑：
//   %%writefile matrix_add.cu
//   !nvcc -arch=sm_75 -O2 matrix_add.cu -o matrix_add && ./matrix_add

#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <cuda_runtime.h>

#define CUDA_CHECK(call) \
    do { cudaError_t e=(call); if(e!=cudaSuccess){ \
         fprintf(stderr,"CUDA error %s:%d %s\n",__FILE__,__LINE__,cudaGetErrorString(e)); \
         exit(1); } } while(0)

// C = A + B，A/B/C 都是 rows × cols 的矩陣（row-major）
__global__ void matrix_add(const float *A, const float *B, float *C,
                            int rows, int cols) {
    int col = blockIdx.x * blockDim.x + threadIdx.x;
    int row = blockIdx.y * blockDim.y + threadIdx.y;

    // 邊界檢查：rows 和 cols 不一定是 blockDim 的整數倍
    if (row < rows && col < cols) {
        int idx = row * cols + col;
        C[idx] = A[idx] + B[idx];
    }
}

int main(void) {
    const int ROWS = 1023;  // 故意選非 16 倍數，測試邊界檢查
    const int COLS = 997;
    const size_t sz = (size_t)ROWS * COLS * sizeof(float);

    float *h_A = (float *)malloc(sz);
    float *h_B = (float *)malloc(sz);
    float *h_C = (float *)malloc(sz);
    for (int i = 0; i < ROWS * COLS; i++) {
        h_A[i] = (float)i;
        h_B[i] = (float)(ROWS * COLS - i);
    }

    float *d_A, *d_B, *d_C;
    CUDA_CHECK(cudaMalloc(&d_A, sz));
    CUDA_CHECK(cudaMalloc(&d_B, sz));
    CUDA_CHECK(cudaMalloc(&d_C, sz));
    CUDA_CHECK(cudaMemcpy(d_A, h_A, sz, cudaMemcpyHostToDevice));
    CUDA_CHECK(cudaMemcpy(d_B, h_B, sz, cudaMemcpyHostToDevice));

    // 2D block：16×16 = 256 個 thread（warp size 32 的整數倍，細節見下節）
    dim3 blockDim(16, 16);
    // Ceiling division：確保覆蓋所有 rows 和 cols
    dim3 gridDim((COLS + blockDim.x - 1) / blockDim.x,
                 (ROWS + blockDim.y - 1) / blockDim.y);

    printf("Matrix %dx%d, block %dx%d, grid %dx%d\n",
           ROWS, COLS, blockDim.y, blockDim.x, gridDim.y, gridDim.x);

    matrix_add<<<gridDim, blockDim>>>(d_A, d_B, d_C, ROWS, COLS);
    CUDA_CHECK(cudaDeviceSynchronize());
    CUDA_CHECK(cudaGetLastError());
    CUDA_CHECK(cudaMemcpy(h_C, d_C, sz, cudaMemcpyDeviceToHost));

    // 驗證
    bool correct = true;
    float expected_sum = (float)(ROWS * COLS);  // A[i] + B[i] = i + (N-i) = N
    for (int i = 0; i < ROWS * COLS && correct; i++) {
        if (fabsf(h_C[i] - expected_sum) > 1e-3f) {
            printf("MISMATCH at i=%d: got %f, expected %f\n",
                   i, h_C[i], expected_sum);
            correct = false;
        }
    }
    printf("Result: %s\n", correct ? "CORRECT" : "WRONG");

    free(h_A); free(h_B); free(h_C);
    CUDA_CHECK(cudaFree(d_A));
    CUDA_CHECK(cudaFree(d_B));
    CUDA_CHECK(cudaFree(d_C));
    return 0;
}
```

預期輸出（Colab T4，未在本機實測；Colab 選 GPU runtime 用 nvcc 編譯可驗證）：

```
Matrix 1023x997, block 16x16, grid 64x63
Result: CORRECT
```

注意：我故意把矩陣大小設成奇數（1023×997），確認邊界檢查是否正確——最右邊和最底下那排 block 都有超出邊界的 thread，需要 `if (row < rows && col < cols)` 才能安全。

---

## 核心範例 2：影像灰階化（RGB → Grayscale）

這個範例更接近實際的影像處理工作：把 RGB 影像（每像素 3 個 uint8_t）轉成灰階（每像素 1 個 uint8_t）。

```cuda
// 檔案：grayscale.cu
// 在 Colab 跑：
//   %%writefile grayscale.cu
//   !nvcc -arch=sm_75 -O2 grayscale.cu -o grayscale && ./grayscale

#include <stdio.h>
#include <stdlib.h>
#include <cuda_runtime.h>

#define CUDA_CHECK(call) \
    do { cudaError_t e=(call); if(e!=cudaSuccess){ \
         fprintf(stderr,"CUDA error %s:%d %s\n",__FILE__,__LINE__,cudaGetErrorString(e)); \
         exit(1); } } while(0)

// Luma 係數（BT.601 標準，適合 sRGB 影像）
// Y = 0.299*R + 0.587*G + 0.114*B
// 這三個數字加起來 = 1.0，對人眼的感知亮度最準確
// 不選 (R+G+B)/3 是因為人眼對綠色最敏感、對藍色最不敏感
__global__ void rgb_to_gray(const unsigned char *rgb,  // 輸入：[width * height * 3]
                             unsigned char *gray,        // 輸出：[width * height]
                             int width, int height) {
    int col = blockIdx.x * blockDim.x + threadIdx.x;
    int row = blockIdx.y * blockDim.y + threadIdx.y;

    if (col < width && row < height) {
        int pixel_idx = row * width + col;
        int rgb_idx   = pixel_idx * 3;  // R, G, B 連續儲存

        unsigned char r = rgb[rgb_idx + 0];
        unsigned char g = rgb[rgb_idx + 1];
        unsigned char b = rgb[rgb_idx + 2];

        // 用 float 計算，最後轉回 uint8
        float y = 0.299f * r + 0.587f * g + 0.114f * b;
        gray[pixel_idx] = (unsigned char)(y + 0.5f);  // 四捨五入
    }
}

int main(void) {
    const int W = 1920, H = 1080;  // Full HD
    const size_t rgb_sz  = (size_t)W * H * 3;
    const size_t gray_sz = (size_t)W * H;

    unsigned char *h_rgb  = (unsigned char *)malloc(rgb_sz);
    unsigned char *h_gray = (unsigned char *)malloc(gray_sz);

    // 用漸層填充模擬一張影像
    for (int row = 0; row < H; row++) {
        for (int col = 0; col < W; col++) {
            int idx = (row * W + col) * 3;
            h_rgb[idx+0] = (unsigned char)(col * 255 / W);     // R
            h_rgb[idx+1] = (unsigned char)(row * 255 / H);     // G
            h_rgb[idx+2] = 128;                                  // B constant
        }
    }

    unsigned char *d_rgb, *d_gray;
    CUDA_CHECK(cudaMalloc(&d_rgb,  rgb_sz));
    CUDA_CHECK(cudaMalloc(&d_gray, gray_sz));
    CUDA_CHECK(cudaMemcpy(d_rgb, h_rgb, rgb_sz, cudaMemcpyHostToDevice));

    // 2D launch：block 16×16，grid covering W×H
    dim3 blockDim(16, 16);   // 256 threads per block
    dim3 gridDim((W + 15) / 16, (H + 15) / 16);  // (120, 68)

    cudaEvent_t t0, t1;
    CUDA_CHECK(cudaEventCreate(&t0));
    CUDA_CHECK(cudaEventCreate(&t1));
    CUDA_CHECK(cudaEventRecord(t0));

    rgb_to_gray<<<gridDim, blockDim>>>(d_rgb, d_gray, W, H);

    CUDA_CHECK(cudaEventRecord(t1));
    CUDA_CHECK(cudaEventSynchronize(t1));
    float ms;
    CUDA_CHECK(cudaEventElapsedTime(&ms, t0, t1));

    CUDA_CHECK(cudaGetLastError());
    CUDA_CHECK(cudaMemcpy(h_gray, d_gray, gray_sz, cudaMemcpyDeviceToHost));

    // 簡單驗證：左上角像素 (R=0, G=0, B=128) 的灰階值
    // Y = 0.299*0 + 0.587*0 + 0.114*128 = 14.592 ≈ 15
    printf("Pixel (0,0): R=%d G=%d B=%d → gray=%d (expected ~15)\n",
           h_rgb[0], h_rgb[1], h_rgb[2], h_gray[0]);
    printf("Kernel time: %.3f ms  (%.1f MP/s)\n",
           ms, (float)(W * H) / ms / 1000.0f);

    free(h_rgb); free(h_gray);
    CUDA_CHECK(cudaFree(d_rgb));
    CUDA_CHECK(cudaFree(d_gray));
    CUDA_CHECK(cudaEventDestroy(t0));
    CUDA_CHECK(cudaEventDestroy(t1));
    return 0;
}
```

預期輸出（Colab T4，未在本機實測；Colab 選 GPU runtime 用 nvcc 編譯可驗證）：

```
Pixel (0,0): R=0 G=0 B=128 → gray=15 (expected ~15)
Kernel time: 0.128 ms  (16196.6 MP/s)
```

1920×1080 = ~200 萬像素，0.128 ms → 每秒 160 億像素。

---

## 3D Grid：體素、CNN Feature Map

3D grid 用在三維資料——體素（voxel）、音頻（time×freq×channel）、或 CNN 的 activation tensor（batch×channel×height×width 其中三維）。

```cuda
__global__ void volume_scale(float *volume,
                              int D, int H, int W,   // Depth × Height × Width
                              float factor) {
    int x = blockIdx.x * blockDim.x + threadIdx.x;  // width
    int y = blockIdx.y * blockDim.y + threadIdx.y;  // height
    int z = blockIdx.z * blockDim.z + threadIdx.z;  // depth

    if (x < W && y < H && z < D) {
        // row-major: element (z, y, x) at linear index z*H*W + y*W + x
        volume[z * H * W + y * W + x] *= factor;
    }
}

// Launch 3D kernel：
// 假設 64×64×64 的 volume，block 8×8×8（= 512 threads，注意 z≤64 限制）
dim3 blockDim3(8, 8, 8);  // 512 threads per block，≤ 1024 ✓
dim3 gridDim3(
    (W + 7) / 8,   // ceil(64/8) = 8
    (H + 7) / 8,   // = 8
    (D + 7) / 8    // = 8
);
// 共 8*8*8 = 512 個 block，每個 8*8*8 = 512 個 thread，共 512*512 = 262144 threads
volume_scale<<<gridDim3, blockDim3>>>(d_vol, D, H, W, 2.0f);
```

3D grid 在實際 CUDA 程式中並不常見——大多數框架（PyTorch 的 CUDA extension、cuDNN）都把高維度資料手動攤平成 2D 或 1D，然後在 kernel 內部計算 multi-dimensional index。原因是 gridDim.z 的上限 65535 比 gridDim.x 的 2^31 小很多，大資料容易超限。不過對中小規模的 3D 問題，3D grid 讓程式碼更可讀。

---

## Block Size 為何常選 128 或 256？

這是 CUDA 程式設計中最常被問到的問題之一。

### 必要條件：warp size 32 的整數倍

Warp 是 CUDA 的基本排程單位（Ch 8/10 詳細說明），大小固定 **32 個 thread**。如果 `blockDim.x`（或 1D 情況的 blockDim.x）不是 32 的整數倍，最後一個 warp 只有部分 thread 是 active 的，其餘的 thread slot 佔用 register 但不做工作——這叫 **partial warp**，是純浪費。

所以 blockSize 的候選集合是：32, 64, 96, 128, 160, 192, 224, 256, 288, ..., 1024。

### 為什麼不選 32？

32 = 剛好 1 個 warp。Block 太小的問題：
- **調度器 overhead**：每個 block 的 launch 和 retire 有固定 overhead，block 數多了 overhead 放大。
- **低 occupancy**：SM 同時維持多個 block 來隱藏 latency（Ch 11），block 太小（每個 block 的 thread 少）意味著 SM 需要維持更多 block，但 block 數受 shared memory 和 register 的總量限制。

### 為什麼不選 512 或 1024？

Block 越大：
- **Shared memory 競爭**：每個 block 的 shared memory 是固定分配的（Ch 17），大 block 可能超出 SM 的 shared memory 容量（T4 每 SM 48 KB），導致同時能 resident 的 block 數減少。
- **Register pressure**：大 block 不一定用更多 register，但如果你的 kernel register 用量高，block 裡 thread 越多 → SM 需要的 register 越多 → resident block 數越少（低 occupancy，Ch 11 詳述）。

### 128 和 256 的理由

- **128**（= 4 個 warp）：在 register-heavy kernel 中是不錯的中間值，保持至少 4 個 warp per SM 用於 latency hiding。
- **256**（= 8 個 warp）：最常見的預設值。T4 SM 最多 resident 32 個 warp（上限），256 threads/block 要讓 SM 跑滿需要 4 個 block（32 warp / 8 warp per block = 4 blocks）——通常資源夠用的情況下可以辦到。
- **512**（= 16 個 warp）：較少用，主要在 kernel 沒什麼 register 壓力、shared memory 用量也少的時候。

**2D block 的選擇**：`dim3(16, 16)` = 256 threads（最常見）、`dim3(32, 8)` = 256 threads（x 方向 32 剛好等於 warp size，對 coalescing 更友善）。

```
常見的 2D block size 選擇：

blockDim = (32, 8):  warp 剛好對齊 x 方向，256 threads
  Warp 0: T(0,0), T(1,0), ..., T(31,0)  ← 同一 row 的 32 個 thread，col 連續
  Warp 1: T(0,1), T(1,1), ..., T(31,1)  ← 下一 row

blockDim = (16, 16): 方形 block，256 threads
  Warp 0: T(0,0), T(1,0), ..., T(15,0), T(0,1), T(1,1), ..., T(15,1)
  （注意：warp 跨 row 0 和 row 1 各 16 個 thread）
```

對 row-major 矩陣存取，`(32, 8)` 比 `(16, 16)` 有更好的 coalescing——因為 warp 內的 32 個 thread 對應同一行 32 個連續的 column，一次 cache line 取 32×4 bytes = 128 bytes = 剛好一條 cache line（T4 的 L1/L2 cache line 是 128 bytes）。

---

## 邊界檢查：什麼情況下可以省略？

邊界檢查 `if (row < rows && col < cols)` 的必要性：

```
矩陣 5×5，block 4×4：

gridDim = (ceil(5/4), ceil(5/4)) = (2, 2)，共 4 個 block
每個 block 有 16 個 thread，共 64 個 thread
但矩陣只有 25 個有效元素

Block(1,1) 的 thread 映射：
  T(0,0) → (col=4, row=4) ✓
  T(1,0) → (col=5, row=4) ✗ 超出 col 邊界
  T(0,1) → (col=4, row=5) ✗ 超出 row 邊界
  T(1,1) → (col=5, row=5) ✗ 兩邊都超
  ...（剩下 12 個 thread 全超出範圍）
```

如果省略邊界檢查，超出範圍的 thread 會存取 `d_C[5]` 或更遠的位址，踩到相鄰分配的記憶體，或者（更危險）踩到 d_A、d_B 的範圍。輸出在小矩陣上可能看起來對，但大矩陣或特定 layout 下會靜默踩壞資料。

**什麼時候可以省略邊界檢查？**

只有當你能 100% 保證 N 是 blockDim 的整數倍時——例如你在 launch 前把 N pad 到最近的 blockDim 倍數，並把 padding 部分初始化成 0（或無害值）。這是 padding 換省略邊界檢查的技巧，在性能敏感的 kernel 中常見（省掉分支可能讓 warp 執行更整齊）。

---

## Grid-Stride Loop 的 2D 版本

1D grid-stride loop 在 Ch 12 介紹過。2D 情況類似，但步長要在 x 和 y 兩個方向各自跳：

```cuda
__global__ void matrix_scale_stride(float *data, float factor, int rows, int cols) {
    // x 方向的 stride
    int stride_x = gridDim.x * blockDim.x;
    // y 方向的 stride
    int stride_y = gridDim.y * blockDim.y;

    for (int row = blockIdx.y * blockDim.y + threadIdx.y; row < rows; row += stride_y) {
        for (int col = blockIdx.x * blockDim.x + threadIdx.x; col < cols; col += stride_x) {
            data[row * cols + col] *= factor;
        }
    }
    // 雙層迴圈：外層跳 y 方向，內層跳 x 方向
    // 邊界由迴圈條件 (row < rows && col < cols) 處理，不需要額外 if
}
```

2D grid-stride loop 在超大矩陣（如 100K×100K）時很有用，但實際上大矩陣通常拆成 tiles 用 shared memory 優化（Ch 17 的 tiling），不會用這種裸存取方式。

---

## 完整範例 3：轉置矩陣（Naive 版，Ch 17 會優化）

矩陣轉置是一個很好的索引練習：讀 A[row][col]，寫 B[col][row]。

```cuda
// 檔案：transpose_naive.cu
// 在 Colab 跑：
//   %%writefile transpose_naive.cu
//   !nvcc -arch=sm_75 -O2 transpose_naive.cu -o transpose_naive && ./transpose_naive

#include <stdio.h>
#include <stdlib.h>
#include <cuda_runtime.h>

#define CUDA_CHECK(call) \
    do { cudaError_t e=(call); if(e!=cudaSuccess){ \
         fprintf(stderr,"CUDA error %s:%d %s\n",__FILE__,__LINE__,cudaGetErrorString(e)); \
         exit(1); } } while(0)

// Naive 轉置：A 是 rows×cols 的矩陣，B 是 cols×rows 的轉置結果
// 讀 A 是 coalesced（同一 row 連續），寫 B 是 strided（不 coalesced）
// 這個版本在大矩陣時是 memory-bound 且有 coalescing 問題
// Ch 17 的 tiled 版本用 shared memory 解決這個問題
__global__ void transpose_naive(const float *A, float *B, int rows, int cols) {
    int col = blockIdx.x * blockDim.x + threadIdx.x;  // A 的 col
    int row = blockIdx.y * blockDim.y + threadIdx.y;  // A 的 row

    if (col < cols && row < rows) {
        // 讀 A[row][col]（row-major，連續 → coalesced）
        float val = A[row * cols + col];
        // 寫 B[col][row]（B 的 row 是 col，col 是 row → strided，不 coalesced）
        B[col * rows + row] = val;
    }
}

int main(void) {
    const int ROWS = 1024, COLS = 512;
    const size_t sz_A = (size_t)ROWS * COLS * sizeof(float);
    const size_t sz_B = (size_t)COLS * ROWS * sizeof(float);  // 轉置後 COLS×ROWS

    float *h_A = (float *)malloc(sz_A);
    float *h_B = (float *)malloc(sz_B);
    for (int i = 0; i < ROWS * COLS; i++) h_A[i] = (float)i;

    float *d_A, *d_B;
    CUDA_CHECK(cudaMalloc(&d_A, sz_A));
    CUDA_CHECK(cudaMalloc(&d_B, sz_B));
    CUDA_CHECK(cudaMemcpy(d_A, h_A, sz_A, cudaMemcpyHostToDevice));

    // Launch config：block 32×8（256 threads），x 方向對齊 warp
    dim3 blockDim(32, 8);
    dim3 gridDim((COLS + 31) / 32, (ROWS + 7) / 8);

    cudaEvent_t t0, t1;
    CUDA_CHECK(cudaEventCreate(&t0));
    CUDA_CHECK(cudaEventCreate(&t1));
    CUDA_CHECK(cudaEventRecord(t0));
    transpose_naive<<<gridDim, blockDim>>>(d_A, d_B, ROWS, COLS);
    CUDA_CHECK(cudaEventRecord(t1));
    CUDA_CHECK(cudaEventSynchronize(t1));
    float ms;
    CUDA_CHECK(cudaEventElapsedTime(&ms, t0, t1));
    CUDA_CHECK(cudaGetLastError());

    CUDA_CHECK(cudaMemcpy(h_B, d_B, sz_B, cudaMemcpyDeviceToHost));

    // 驗證：B[col][row] 應等於 A[row][col]
    bool correct = true;
    for (int r = 0; r < ROWS && correct; r++) {
        for (int c = 0; c < COLS && correct; c++) {
            float expected = h_A[r * COLS + c];
            float got      = h_B[c * ROWS + r];
            if (fabsf(got - expected) > 1e-6f) {
                printf("MISMATCH at (%d,%d): got %f expected %f\n", r, c, got, expected);
                correct = false;
            }
        }
    }
    printf("Result: %s\n", correct ? "CORRECT" : "WRONG");
    printf("Naive transpose: %.3f ms\n", ms);
    printf("(Ch 17 tiled version will be ~3-5x faster on this size)\n");

    free(h_A); free(h_B);
    CUDA_CHECK(cudaFree(d_A));
    CUDA_CHECK(cudaFree(d_B));
    CUDA_CHECK(cudaEventDestroy(t0));
    CUDA_CHECK(cudaEventDestroy(t1));
    return 0;
}
```

預期輸出（Colab T4，未在本機實測；Colab 選 GPU runtime 用 nvcc 編譯可驗證）：

```
Result: CORRECT
Naive transpose: 0.047 ms
(Ch 17 tiled version will be ~3-5x faster on this size)
```

這個 naive 版本的效能問題在於寫入 B 時的 strided 存取（`B[col * rows + row]`）：同一個 warp 的 thread，`col` 相同、`row` 不同，所以寫入的位址間距 = `rows * sizeof(float)` = 4 KB，完全不 coalesced——每個 warp 需要 32 次不同的 cache line 存取。Ch 17 用 shared memory tiling 解決這個問題。

---

## 踩雷清單

### 雷 1：row/col 搞反，x/y 混淆

```cuda
// 常見錯誤：把 x 當 row、y 當 col
int row = blockIdx.x * blockDim.x + threadIdx.x;  // 錯！row 應該對應 y
int col = blockIdx.y * blockDim.y + threadIdx.y;  // 錯！col 應該對應 x

// 正確：
int col = blockIdx.x * blockDim.x + threadIdx.x;  // x 方向 → column
int row = blockIdx.y * blockDim.y + threadIdx.y;  // y 方向 → row
```

這個錯誤對方形矩陣不影響正確性（結果是轉置的矩陣），但對非方形矩陣會出錯，而且嚴重影響 coalescing 效能（warp 變成在不同 row 而非同一 row 存取，造成 strided access）。

### 雷 2：線性 index 公式算錯

```cuda
// 錯誤：width 和 height 搞反
int idx = col * height + row;  // 這是 column-major！

// 正確（row-major，C 風格）：
int idx = row * width + col;
```

Column-major（Fortran 風格）和 row-major（C 風格）是老生常談的 bug 來源。CUDA 的 global memory 和 CPU 的 C array 都是 row-major。只有當你和 BLAS/Fortran 函式庫交互時才需要注意 column-major（那些 API 通常有 `CUBLAS_OP_T` 之類的轉置參數）。

### 雷 3：2D block 的 x/y 維度乘積超過 1024

```cuda
dim3 blockDim(32, 32);  // 32 * 32 = 1024 ✓（剛好在上限）
dim3 blockDim(33, 32);  // 33 * 32 = 1056 ✗ 超過 1024！
// cudaGetLastError() 會回傳 cudaErrorInvalidConfiguration
```

常見情況：你想把 2D block 從 `(16, 16)` 改成 `(32, 32)`（為了讓 x 方向對齊 warp），但忘了 1024 的上限。`(32, 32)` 恰好是上限，而且 z 方向為 1。

### 雷 4：gridDim 的 ceiling division 公式寫錯

```cuda
// 錯誤：整除除法
int gridX = cols / blockDim.x;  // 如果 cols=1000, blockDim.x=256 → gridX=3
                                  // 但 3*256=768 < 1000！少掉了 232 個 column

// 正確：ceiling division
int gridX = (cols + blockDim.x - 1) / blockDim.x;  // (1000 + 255) / 256 = 4 ✓
```

這個 bug 在方形矩陣、且大小恰好是 blockDim 整數倍時不會出現，所以測試時很容易漏掉。養成習慣：永遠用 ceiling division。

### 雷 5：2D block 內 thread 的 warp 組成

```cuda
// 假設 blockDim = (16, 16)，問：warp 0 的 32 個 thread 是哪些？
// 答：threadIdx.x 的快軸先走
//   T(0,0), T(1,0), ..., T(15,0), T(0,1), T(1,1), ..., T(15,1)
//   即 threadIdx.y=0 的 16 個 + threadIdx.y=1 的 16 個

// 所以 warp 0 的 col = blockIdx.x*16 + {0,1,...,15,0,1,...,15}
// 存取矩陣時，這個 warp 在兩個不同的 row 各取 16 個連續元素
// → 需要 2 條 cache line，不是 1 條（比 blockDim.x=32 時差一點）
```

這不是「錯誤」，但你需要理解 CUDA 的 thread 編號方式（x 軸是 fast axis）才能正確推理 coalescing 行為。Ch 18 會有完整分析。

---

## 進階：攤平多維 tensor 的工程實踐

在 PyTorch 的 CUDA extension 或 cuDNN 等函式庫中，你會看到高維 tensor（如 N×C×H×W）幾乎都被攤平成 1D 或 2D 來 launch：

```cuda
// 常見的 batch × spatial 攤平方式：
// 把 (batch, spatial elements) 映射到 (grid_y, grid_x)

// N×H×W 的 activation map，每個 batch 獨立處理
dim3 blockDim(256, 1);
dim3 gridDim((H * W + 255) / 256, N);  // x 方向覆蓋 spatial，y 方向覆蓋 batch

__global__ void batch_relu(float *act, int HW, int N) {
    int spatial_idx = blockIdx.x * blockDim.x + threadIdx.x;
    int batch_idx   = blockIdx.y;
    if (spatial_idx < HW) {
        float v = act[batch_idx * HW + spatial_idx];
        act[batch_idx * HW + spatial_idx] = fmaxf(v, 0.0f);
    }
}
```

這種「y = batch，x = spatial」的慣用法在 ML kernel 中極為常見——因為 batch 和 spatial 的獨立性天然對應到 grid 的兩個維度，又避免了複雜的 3D/4D index 計算。

---

## 動手練習

1. **跑 matrix_add.cu**：在 Colab 上跑，確認 CORRECT。改 ROWS=1024, COLS=1024（整除 16），再改 ROWS=1, COLS=1（最小邊界情況），確認都對。

2. **嘗試不同的 2D block size**：把 `blockDim(16, 16)` 改成 `blockDim(32, 8)` 和 `blockDim(8, 32)`，用 CUDA events 計時（矩陣選 4096×4096），比較三種的核心執行時間。預測哪個最快，看結果是否符合預期（提示：x 方向的 warp 對齊）。

3. **測試邊界**：把 matrix_add 的邊界檢查 `if (row < rows && col < cols)` 刪掉，跑 ROWS=1000, COLS=1000（非 16 倍數）。用 `compute-sanitizer --tool memcheck ./matrix_add` 跑，觀察它報告什麼。

4. **影像灰階化**：跑 grayscale.cu，用 `printf` 多印幾個像素驗證 luma 公式是否正確。把 `blockDim(16, 16)` 改成 `blockDim(32, 8)`，比較時間。

5. **寫一個 2D 版的 grid-stride loop**：把 `matrix_scale_stride` 的 kernel 填完整，launch 時固定 gridDim = (8, 8)，用 ROWS=COLS=4096 測試正確性。

---

## 本章重點

- CUDA thread 有三層：thread（有 `threadIdx`）→ block（有 `blockIdx`）→ grid，三個維度都最多到 3D。
- 2D 索引公式：`col = blockIdx.x * blockDim.x + threadIdx.x`，`row = blockIdx.y * blockDim.y + threadIdx.y`，線性 index = `row * width + col`（row-major）。
- 邊界檢查 `if (row < rows && col < cols)` 在 N/M 不整除 blockDim 時是必要的——永遠寫。
- Block size 選 32 的整數倍；128/256 是最常見選擇；2D block 常用 `(16, 16)` 或 `(32, 8)`。
- `threadIdx.x` 是 fast axis——warp 內的 32 個 thread 有相同 `threadIdx.y` 但不同 `threadIdx.x`，x 方向的存取影響 coalescing。
- 3D grid 在體素等真 3D 問題上可讀性好，但 gridDim.z ≤ 65535；高維 tensor 通常攤平成 2D 或 1D 再用 kernel 內部計算反算 index。

---

## 自我檢核

- 給定矩陣 1000×1000，`blockDim=(16,16)`，算出 `gridDim`，說明哪些 block 的 thread 有超出邊界的情況。
- 一個 `blockDim=(32, 8)` 的 block，warp 0 的 32 個 thread 對應的 `(threadIdx.x, threadIdx.y)` 分別是什麼？（提示：x 是 fast axis）
- 為什麼 `dim3 blockDim(16, 16)` 比 `dim3 blockDim(33, 31)` 好？（說出兩個理由）
- `B[col * rows + row]` 和 `B[row * cols + col]` 有什麼區別？對一個非方形矩陣，這兩種各自代表什麼佈局？
- Grid-stride loop 的 2D 版本，其 stride_x 和 stride_y 各自等於什麼？

---

## 延伸閱讀

1. **CUDA C++ Programming Guide — Ch 2.2: Thread Hierarchy**
   - 讀哪裡：[docs.nvidia.com/cuda/cuda-c-programming-guide/#thread-hierarchy](https://docs.nvidia.com/cuda/cuda-c-programming-guide/#thread-hierarchy)
   - 學什麼：`dim3`、gridDim/blockDim 的完整說明和硬體限制表（Appendix K 的 Compute Capability 表），以及 CUDA 對 thread 線性化的規則（warp 怎麼從 block 的 thread 組成）。
   - 前提：本章讀完即可。

2. **CUDA C++ Best Practices Guide — Ch 10.2: Execution Configuration**
   - 讀哪裡：[docs.nvidia.com/cuda/cuda-c-best-practices-guide/#execution-configuration](https://docs.nvidia.com/cuda/cuda-c-best-practices-guide/#execution-configuration)
   - 學什麼：「block size 選多大」的官方建議，包含 occupancy 和 block size 的取捨說明；搭配本章的 block size 討論和 Ch 11 的 occupancy 概念讀效果最好。
   - 前提：本章 + Ch 11。

3. **《Programming Massively Parallel Processors》(4th ed.) — Ch 3.2–3.3: Multidimensional Grid and Data Layout**
   - 讀哪裡：Ch 3.2（2D thread mapping）、Ch 3.3（linearization）
   - 學什麼：PMPP 對 2D/3D grid 映射的說明有更多圖解，特別是 block 如何 tile 矩陣的視覺化；Ch 3.3 解釋 thread linearization（warp 從 block 的 fast-axis 組成）。
   - 前提：本章同等程度。

4. **CUDA C++ Programming Guide — Appendix K: Compute Capabilities（Sections K.1 - K.3）**
   - 讀哪裡：[docs.nvidia.com/cuda/cuda-c-programming-guide/#features-and-technical-specifications](https://docs.nvidia.com/cuda/cuda-c-programming-guide/#features-and-technical-specifications)
   - 學什麼：各架構（Kepler 到 Hopper）的 gridDim/blockDim 硬體上限、最大 thread 數、warp 大小——確認本章引用的 T4（Turing, sm_75）數字來源。
   - 前提：隨時可查。

5. **Mark Harris, "CUDA Pro Tip: Write Flexible Kernels with Grid-Stride Loops" (NVIDIA Developer Blog, 2013)**
   - 讀哪裡：[developer.nvidia.com/blog/cuda-pro-tip-write-flexible-kernels-with-grid-stride-loops/](https://developer.nvidia.com/blog/cuda-pro-tip-write-flexible-kernels-with-grid-stride-loops/)
   - 學什麼：Grid-stride loop 的完整理由（除了處理大資料，還讓 kernel 在 CPU 上用 1×1 launch 測試、方便 debug）；附帶 warp divergence 和 occupancy 的關聯說明。
   - 前提：本章讀完即可。

---

你現在能寫出 1D/2D kernel、正確計算索引、選合適的 block size。但 kernel 執行出錯時怎麼辦？CUDA 的錯誤模型比 CPU 複雜——有同步錯誤、異步錯誤、記憶體越界，有各自的偵測和除錯工具。

→ [Ch 15 錯誤處理與除錯：compute-sanitizer / cuda-gdb](./15-error-handling-debugging.md)
