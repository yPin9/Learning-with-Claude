# Ch 30 Tensor Core

## 前置知識
- Ch 29：FFMA 吞吐量（sm_75 每 SM 64 FLOPs/cycle）、指令延遲與排程
- 線性代數：矩陣乘法 D = A × B + C 的定義

---

## 為什麼 FP32 FFMA 滿足不了深度學習

深度學習的計算核心是 GEMM（General Matrix Multiply，廣義矩陣乘）。訓練期間前向傳播是矩陣乘、反向傳播的梯度計算也是矩陣乘，GEMM 佔深度學習訓練總計算量的 ~80%。

問題在於 FP32 FFMA 的吞吐跟得上嗎？

sm_75（Turing，Tesla T4）每個 SM 有 64 個 FP32 core，每 cycle 能執行 64 條 FFMA，也就是 128 FLOPs/cycle/SM。T4 的 40 個 SM、1.59 GHz boost clock，算出來的 FP32 峰值是 **~8.1 TFLOPS**。

聽起來不少，但 GPT-2 訓練一步就需要 10+ PFLOPs。用 FP32 FFMA 跑深度學習，硬體是瓶頸，不是模型。

**更關鍵的問題是：深度學習需要 FP32 嗎？**

不需要。FP16 混合精度訓練已經是標準做法——前向/反向用 FP16 計算，梯度累加用 FP32，最終模型精度和純 FP32 相當。推論更激進，INT8 量化已在業界大規模部署，精度損失在可接受範圍內。

NVIDIA 的答案是 Tensor Core：一塊專用硬體，把 GEMM 的 **tile 矩陣乘累加（MMA，Matrix Multiply-Accumulate）** 一次做完，吞吐比 FFMA 高出一個數量級。

---

## MMA 操作的語義

Tensor Core 做的事用一個公式說清楚：

```
D = A × B + C
A: m × k 矩陣
B: k × n 矩陣
C: m × n 矩陣（累加器輸入）
D: m × n 矩陣（輸出）
```

WMMA API 最常用的 shape 是 **16 × 16 × 16**：A(16×16) × B(16×16) + C(16×16) = D(16×16)。整個操作由 **一個 warp（32 個 thread）** 共同執行，稱為 warp-collective operation。

這 16×16×16 = 4096 次 multiply-add，一個 warp-collective `mma_sync` 呼叫就完成。用 FFMA 做同樣的事需要 4096 條 FFMA 指令，即使完美排程也要 64 個 cycle（4096 / 64 FLOPs per cycle）。Tensor Core 做到的是：硬體接手這 4096 次 MMA，整體 latency 和吞吐都遠優於 FFMA 路徑。

### 吞吐對比（sm_75，T4）

sm_75 每個 SM 有 **2 個 SMSP（Sub-Multiprocessor）**，每個 SMSP 有 4 個 Tensor Core，所以每個 SM 共 **8 個 Tensor Core**。每個 Tensor Core 每 cycle 能做 64 FLOPs，8 個合計 **512 FLOPs/cycle/SM**。

對比 FFMA 的 128 FLOPs/cycle/SM，Tensor Core 是 **4 倍吞吐**。換算到整機：

| 路徑 | T4 峰值 |
|------|---------|
| FP32 FFMA | ~8.1 TFLOPS |
| FP16 Tensor Core | ~65 TOPS |
| INT8 Tensor Core | ~130 TOPS |

這就是為什麼生產環境的推論服務不跑 FP32。

---

## Tensor Core 硬體架構

### SM → SMSP → Tensor Core 的層次

Turing（sm_75）的 SM 內部分成 **4 個 SMSP**（Sub-Multiprocessor，有時文件叫 partition）。每個 SMSP 有：
- 16 個 FP32/INT32 core
- 2 個 Tensor Core（4×4 硬體 MMA 單元）

整個 SM 合計：4 SMSP × 2 TC = **8 個 Tensor Core per SM**。這個數字在 Jia et al. arXiv 1903.07486（「Dissecting the NVIDIA Turing T4 GPU via Microbenchmarking」）和 NVIDIA Turing Architecture Whitepaper 裡都有明確記載。

### 4×4 硬體 MMA 單元

每個 Tensor Core 硬體上做的是 **4×4×4 FMA**：A(4×4) × B(4×4) + C(4×4) = D(4×4)，一次操作 4×4×4 = 64 次乘加，即 64 FLOPs/cycle。這就是每個 TC 每 cycle 64 FLOPs 的來源。

### 16×16×16 tile 如何映射到 4×4 硬體

WMMA API 的 16×16×16 tile 是軟體抽象，硬體實際執行的是一連串 4×4 操作。映射關係如下：

一個 16×16×16 矩陣乘，沿著 K 維度看：
- K=16 分成 4 個 k=4 的切片
- M=16 分成 4 個 m=4 的切片
- N=16 分成 4 個 n=4 的切片

理論上需要 4×4×4 = 64 個 4×4 硬體操作才能完成完整的 16×16×16。實際上一個 SM 的 8 個 TC 並行執行，可以在 **~8 個 cycle** 內完成（64 ops / 8 TC = 8 cycle，假設完美流水）。加上 pipeline 排程和指令發射 overhead，Tensor Core 的 `mma.sync` 指令在 PTX 文件中標注的 latency 約為 **16 個 clock cycle**，對應 throughput 為每 8 個 cycle 一個 m16n8k8 指令（兩個 SM partition 並行）。

這個 cycle 估算的重點不在精確數字，而在理解：16 cycle latency 遠小於用 FFMA 的 64 cycle，且 throughput 更高，因為多個 TC 並行、pipeline 深度更友善。

---

## Fragment 內部結構：thread 與 element 的映射

WMMA 的 fragment 是 warp 32 個 thread 共同持有一個矩陣 tile 的方式。雖然 CUDA 官方文件明確說映射是 implementation-defined，但 PTX ISA 文件針對特定 shape 有具體描述，值得理解。

### 16×16 accumulator 的配置（FP32）

以 `wmma::accumulator` 16×16 FP32 為例，fragment 的每個 thread 持有 **8 個 float**（`frag.num_elements == 8`），32 個 thread 合計 256 個 float，對應 16×16 = 256 個矩陣元素。

具體分配：每個 thread 負責 **2 行 × 4 列** 的子區域。但「哪 2 行哪 4 列」的選擇，在 Turing 和 Ampere 之間不同，compiler 不保證。你可以觀察到：`frag.x[0]` 到 `frag.x[7]` 是連續的 float，但它們在矩陣中的位置需要查對應架構的 PTX 文件才能確定。

### 為什麼不應該直接索引 `frag.x[i]`

假設你在 Turing 上實測出 `frag.x[0]` 對應矩陣 (row=0, col=0)，把這個假設硬編進程式碼，然後搬到 Ampere 跑——結果可能是 silent wrong result，不會 crash，更難 debug。

唯一安全的跨架構做法：
1. 需要填特定位置的值 → 用 `fill_fragment` 填全體，或透過 `store_matrix_sync` + `load_matrix_sync` 繞道
2. 需要讀取特定位置的值 → `store_matrix_sync` 輸出到 shared memory，再正常索引
3. 對整個 fragment 做 element-wise op（例如 ReLU）→ 用 `frag.x[i]` 遍歷全部元素，不需要知道座標映射，這是合法用法

```cpp
// 合法：element-wise ReLU（不需要知道 i 對應哪個座標）
for (int i = 0; i < acc_frag.num_elements; i++) {
    acc_frag.x[i] = max(acc_frag.x[i], 0.0f);
}

// 危險：假設 x[0] 是 (row=0, col=0)——跨架構不保證
// float top_left = acc_frag.x[0];  // 不要這樣做
```

---

## Turing Tensor Core 支援的資料型別

Turing (sm_75) 支援：

| 輸入 | 累加 | 說明 |
|------|------|------|
| FP16 | FP32 | 混合精度訓練標準組合 |
| FP16 | FP16 | 精度較低，速度相同，通常不優先 |
| INT8 | INT32 | 推論量化 |
| INT4 | INT32 | 極端量化，精度風險高 |

**Turing 不支援：**
- BF16（Brain Float 16）：sm_80（Ampere）才有
- TF32（TensorFloat-32）：sm_80（Ampere）才有
- FP64 Tensor Core：sm_80（Ampere）才有

看到文章說 Turing 支援 TF32 就直接懷疑——TF32 是 Ampere 推出的，Turing 沒有。

---

## INT8 Tensor Core 的用法

INT8 的 tile shape 和 FP16 不同：Turing INT8 Tensor Core 的 native shape 是 **8×8×32**（m=8, n=8, k=32），k 維度要更大才能打滿 INT8 吞吐。WMMA API 對應如下：

```cpp
#include <mma.h>
using namespace nvcuda;

// INT8 tile shape: 8×8×32
wmma::fragment<wmma::matrix_a,    8, 8, 32, int8_t, wmma::row_major> a_frag;
wmma::fragment<wmma::matrix_b,    8, 8, 32, int8_t, wmma::col_major> b_frag;
wmma::fragment<wmma::accumulator, 8, 8, 32, int32_t>                 c_frag;

wmma::fill_fragment(c_frag, 0);
wmma::load_matrix_sync(a_frag, a_ptr, 32);  // ldm = 32 elements (int8_t)
wmma::load_matrix_sync(b_frag, b_ptr, 32);
wmma::mma_sync(c_frag, a_frag, b_frag, c_frag);
wmma::store_matrix_sync(d_ptr, c_frag, 8, wmma::mem_row_major);
```

INT8 的 k=32 遠大於 FP16 的 k=16，因為 INT8 每個元素 1 byte，要填滿 Tensor Core 的輸入寬度需要更多元素。

### 量化的概念：scale factor

把浮點模型量化成 INT8 需要每個 tensor 的 **scale factor**：`INT8_value = round(FP32_value / scale)`，scale 在 calibration 階段由 activation 最大絕對值決定（`scale = max_abs / 127`）。

INT8 GEMM 的輸出是 INT32 累加器，需要 requantize 才能輸出：`FP32_output = INT32_result × (scale_A × scale_B) / scale_output`。TensorRT 和 PyTorch INT8 推論都在框架內處理這步——手寫 INT8 kernel 則不能跳過。

---

## Fragment：Tensor Core 的資料容器

WMMA API 用 `wmma::fragment` 作為資料容器，代表矩陣 tile 在 warp 32 個 thread 之間的分散儲存。模板參數依序是矩陣角色、tile shape (m,n,k)、元素型別、記憶體佈局：

```cpp
#include <mma.h>
using namespace nvcuda;

wmma::fragment<wmma::matrix_a,    16,16,16, half, wmma::row_major> a_frag;
wmma::fragment<wmma::matrix_b,    16,16,16, half, wmma::col_major> b_frag;
wmma::fragment<wmma::accumulator, 16,16,16, float>                 c_frag, d_frag;
```

fragment 是 abstract container（thread→element 映射 implementation-defined，前面章節已分析）。唯一跨架構安全的操作是 `load/store/fill/mma`——不要依賴 `frag.x[i]` 的座標語義。

---

## WMMA API 完整流程

以下是 WMMA 的標準使用順序：

```cpp
// 1. 初始化累加器（清零）
wmma::fill_fragment(c_frag, 0.0f);

// 2. 從記憶體載入 A、B（ldm 是 leading dimension，單位是 elements）
wmma::load_matrix_sync(a_frag, a_ptr, 16);  // ldm = 16 elements
wmma::load_matrix_sync(b_frag, b_ptr, 16);

// 3. 執行 MMA（所有 32 個 thread 必須同時呼叫）
wmma::mma_sync(d_frag, a_frag, b_frag, c_frag);

// 4. 儲存結果
wmma::store_matrix_sync(d_ptr, d_frag, 16, wmma::mem_row_major);
```

`load_matrix_sync` 的第三個參數 `ldm` 是 leading dimension（stride），單位是元素數，不是 bytes。FP16 陣列 `ldm = 16` 代表兩行之間差 16 個 `half` = 32 bytes。這是常見錯誤來源，後面踩雷區再細說。

---

## 完整的 WMMA GEMM Kernel

以下是能跑的簡化版 WMMA GEMM，展示 API 的完整使用方式：

```cpp
#include <cuda_fp16.h>
#include <mma.h>
using namespace nvcuda;

#define WMMA_M 16
#define WMMA_N 16
#define WMMA_K 16

// 每個 warp 負責輸出矩陣的一個 16×16 tile
// A: [M, K] row-major, FP16
// B: [K, N] col-major, FP16
// C: [M, N] row-major, FP32
__global__ void wmma_gemm(const half* __restrict__ A,
                           const half* __restrict__ B,
                           float*      __restrict__ C,
                           int M, int N, int K) {
    // 計算這個 warp 負責的 output tile 座標
    // blockDim.x = 128 (4 warp per block), blockDim.y = 1
    int warp_id  = (threadIdx.x + threadIdx.y * blockDim.x) / 32;
    int warp_row = blockIdx.y * (blockDim.y)     + threadIdx.y;
    int warp_col = blockIdx.x * (blockDim.x / 32) + warp_id % (blockDim.x / 32);

    // 邊界檢查：超出矩陣範圍的 warp 直接返回
    if (warp_row * WMMA_M >= M || warp_col * WMMA_N >= N) return;

    wmma::fragment<wmma::matrix_a,    WMMA_M, WMMA_N, WMMA_K, half, wmma::row_major> a_frag;
    wmma::fragment<wmma::matrix_b,    WMMA_M, WMMA_N, WMMA_K, half, wmma::col_major> b_frag;
    wmma::fragment<wmma::accumulator, WMMA_M, WMMA_N, WMMA_K, float> acc_frag;

    wmma::fill_fragment(acc_frag, 0.0f);

    // 沿 K 維度累加，每次處理 WMMA_K 列/行
    for (int k = 0; k < K; k += WMMA_K) {
        const half* a_tile = A + warp_row * WMMA_M * K + k;          // A[warp_row*16][k]
        const half* b_tile = B + k * N + warp_col * WMMA_N;          // B[k][warp_col*16]

        // ldm = K（A 的 leading dimension）和 N（B 的 leading dimension）
        wmma::load_matrix_sync(a_frag, a_tile, K);
        wmma::load_matrix_sync(b_frag, b_tile, N);

        // MMA：acc_frag = a_frag * b_frag + acc_frag
        wmma::mma_sync(acc_frag, a_frag, b_frag, acc_frag);
    }

    // 寫回輸出
    float* c_tile = C + warp_row * WMMA_M * N + warp_col * WMMA_N;
    wmma::store_matrix_sync(c_tile, acc_frag, N, wmma::mem_row_major);
}
```

這個 kernel 的限制很明顯：A、B、C 都從 global memory 讀寫，沒有 shared memory tiling。意味著每個 warp 都在打 DRAM，L2 bandwidth 會成為瓶頸，Tensor Core 大部分時間在等記憶體。真實高效能 GEMM（cuBLAS 實作的那種）必須加 shared memory tiling，Part 7 會完整實作。

---

## 精度的取捨

### FP16 輸入 + FP32 累加

這是訓練場景的標準組合。

FP16 的動態範圍是 6.1×10⁻⁵ 到 6.6×10⁴，有效位數約 3.3 位十進位。如果累加器也用 FP16，做幾百次累加後誤差會快速累積——FP16 的「間距」在數值較大時變很大，小數值會被直接捨去。

用 FP32 累加的好處：FP32 動態範圍是 FP16 的 6.5 萬倍，累加幾千次都不會爆。混合精度訓練的標準做法是：
- 矩陣乘的輸入用 FP16（節省記憶體、利用 Tensor Core）
- 累加器和 loss scaling 用 FP32（保留數值穩定性）
- 權重更新（optimizer step）用 FP32 master weight，然後 cast 成 FP16 存起來

### INT8 推論

INT8 的每個元素只佔 1 byte（FP16 的一半），Tensor Core 吞吐翻倍到 ~130 TOPS。代價是需要量化（quantization）——把浮點權重和激活值對應到 INT8 的 [-128, 127] 範圍。量化需要 calibration（用代表性資料跑一遍，統計 activation 的分布範圍），過程有額外工程成本。

INT8 的精度損失對推論通常可接受（1% 以內的精度下降），但不適合訓練。

### INT4

INT4 是 Turing 支援的最激進量化，每個元素 4 bits。Tensor Core 吞吐再翻倍，但精度風險很高——只有 16 個離散值，activation 分布稍微偏一點就可能有明顯精度損失。需要特殊的量化方案和更仔細的 calibration。

---

## PTX 層：mma.sync 指令的完整語法

WMMA API 最終被 `ptxas` 降成 PTX 的 `mma.sync` 指令。Turing (sm_75) 的 native PTX tensor core shape 是 **m16n8k8**（注意：k=8，不是 k=16；k=16 是 Ampere 的 m16n8k16）。

FP16 → FP32 累加的 PTX 語法：

```ptx
// A: 2 × .b32（packed .f16x2），B: 1 × .b32（packed .f16x2）
// C/D: 4 × .f32（FP32 累加器）
mma.sync.aligned.m16n8k8.row.col.f32.f16.f16.f32
    {%f0, %f1, %f2, %f3},   // D: 4 個 f32 輸出
    {%r0, %r1},              // A: 2 個 b32，每個 packed 兩個 f16
    {%r2},                   // B: 1 個 b32，packed 兩個 f16
    {%f4, %f5, %f6, %f7};   // C: 4 個 f32 輸入（累加器）
```

### 每個修飾符的含義

**`aligned`**：指定所有 warp 內 thread 的 operand 必須對齊。具體說，FP16 packed 成 `.f16x2` 放進 `.b32` 暫存器，這個打包本身要求資料在記憶體中 2-byte 對齊（FP16 的自然對齊）。`aligned` 是讓 assembler 產生更優化的 load path 的提示，對 sm_75 是必要修飾符。

**`m16n8k8`**：tile 維度。m=16（A/D 的行數），n=8（B/D 的列數），k=8（A 的列數 / B 的行數，inner dimension）。注意這是 PTX 層的 native shape，不是 WMMA API 的 16×16×16——WMMA API 的一個呼叫對應多個 PTX `mma.sync`。

**`row.col`**：A 是 row-major，B 是 col-major。「A 行列怎麼存在暫存器裡」由 compiler 的打包順序決定，這裡的 `.row` 和 `.col` 是給 assembler 知道如何從暫存器重建矩陣座標用的，不影響使用者在 WMMA API 層的選擇（WMMA API 已經在 `load_matrix_sync` 時處理好了）。

**`f32.f16.f16.f32`**：四個型別依序是 **D type, A type, B type, C type**。D 和 C 是 FP32，A 和 B 是 FP16。

**FP16 packed in .b32**：FP16 是 16-bit，`.b32` 是 32-bit，所以每個 `.b32` 暫存器存 **2 個 FP16**（`.f16x2`）。A 的 2 個 `.b32` 代表 4 個 FP16 值，對應一個 m16n8k8 tile 裡 A 的子集。

operand 的打包方式：
- **A（2 × b32）**：FP16 元素 packed 成 `.f16x2` 放進 `.b32` 暫存器，每個 b32 存兩個 f16。整個 A tile 在 warp 的 32 個 thread 之間分散存放
- **B（1 × b32）**：同樣 `.f16x2` 打包
- **C/D（4 × f32）**：每個 f32 是獨立的 FP32 累加器元素

一個 16×16×16 的 WMMA API 呼叫，`ptxas` 會展開成多個 m16n8k8 的 `mma.sync`——因為 16×16×16 = 2 個 m16n8k8（k 方向）× 2 個 n 方向 tile，共 4 個 m16n8k8 指令。這個展開由 compiler 自動處理，不需要手動操作 PTX。

---

## 為什麼 B 是 col-major？

WMMA 宣告 B 時用的是 `wmma::col_major`，初次遇到會覺得反直覺——矩陣不是通常都 row-major 嗎？這背後有明確的數學理由。

計算 C[i,j] = Σₖ A[i,k] × B[k,j] 時，對固定的 i 和 j，inner dimension k 是求和的方向。

**B 按 col-major 存的意義**：col-major 下，B 的記憶體佈局是「同一列的元素連續」。B 的第 j 列是 B[0,j], B[1,j], ..., B[K-1,j]，也就是所有 k 值對應的 B[k,j]。

當 Tensor Core 計算 C 的第 j 列時，它需要依序讀取 B[0,j], B[1,j], ..., B[K-1,j]。B 按 col-major 存，這些元素在記憶體中**連續**，一次 cache line fetch 就能取到多個需要的元素。

反之，如果 B 按 row-major 存（B[k,0], B[k,1], ..., B[k,N-1] 連續），讀取固定 j 的一整列 B[0,j], B[1,j], ... 需要跨 stride 存取，每個 B[k,j] 之間差 N 個元素，完全沒有空間局部性（spatial locality）。

**一句話總結**：做 A×B 時，A 沿 K 方向（row-major 下 K 是連續的），B 也需要沿 K 方向連續，所以 B 要 col-major。這是為了讓 inner dimension 的存取方向在兩個 operand 上都是連續的。

cuBLAS 的 GEMM 接口也因此有 `transa` / `transb` 參數，實際上很多時候傳的是「轉置後當 row-major」，效果等同於原矩陣的 col-major。

---

## Roofline 分析：Tensor Core 的計算瓶頸

### Arithmetic Intensity

Arithmetic Intensity（算術強度，AI）定義為：每 byte 資料搬移能做的 FLOPs。

對一個 M×N×K 的 GEMM：
- FLOPs = 2MNK（每個輸出元素需要 K 次 multiply 和 K 次 add）
- 記憶體搬移（假設完全沒有 cache）= A 的大小 + B 的大小 + C/D 的大小
  - FP16 GEMM：(MK + KN) × 2 bytes + MN × 4 bytes（輸出 FP32）

對 M = N = K = 4096 的方矩陣（FP16 in, FP32 out）：
- FLOPs = 2 × 4096³ ≈ 1.37 × 10¹¹
- 資料搬移 = (4096² + 4096²) × 2 + 4096² × 4 = 2 × 2 × 16M + 4 × 16M ≈ 268 MB
- Arithmetic Intensity ≈ 1.37 × 10¹¹ / (268 × 10⁶) ≈ **512 FLOPs/byte**

### T4 的 Roofline

T4 的關鍵數字：
- FP16 Tensor Core 峰值：**65 TFLOPS**（=65 × 10¹² FLOPs/s）
- DRAM bandwidth：**~300 GB/s**（=3 × 10¹¹ bytes/s）
- Roofline 的 ridge point：65 × 10¹² / 3 × 10¹¹ = **約 217 FLOPs/byte**

compute-bound vs memory-bound 的分界線在 217 FLOPs/byte。上面算出 M=N=K=4096 的 AI ≈ 512，理論上應該是 **compute-bound**。

### 這個簡單 WMMA Kernel 為什麼是 memory-bound

問題在於「假設完全沒有 cache」和實際行為不符——是正確的，但這個 kernel 真正的問題是：**沒有 shared memory tiling，每個 warp 獨自從 global memory 讀 A 和 B 的 tile，沒有複用**。

對一個 warp 計算輸出的某個 16×16 tile，它讀了 A 的 16×K 個元素和 B 的 K×16 個元素。相鄰的 warp 計算相鄰的輸出 tile，A 的同一批元素被重複讀取（因為不同 warp 不共享 shared memory）。實際有效的資料複用率幾乎是 1——等同於每個輸出元素都重新讀一遍 A 和 B，有效 AI 崩到接近 1 FLOPs/byte。

這就是為什麼測出來的吞吐遠低於 65 TOPS，DRAM 才是真正的瓶頸。

### 需要多大的矩陣才能是 compute-bound

有了 shared memory tiling 後，block 內多個 warp 共享同一塊 A/B tile，有效 AI 隨 block tile 大小增大。典型高效能 GEMM（block tile 128×128×16）的有效 AI 約 128 FLOPs/byte，仍低於 ridge point 217 FLOPs/byte——代表即使加了 shared memory tiling，T4 的 FP16 GEMM 仍略微 memory-bound。要真正壓到 65 TOPS 峰值需要 double buffering 搭配 prefetch，讓計算與記憶體搬移完全重疊。

---

## 對比取捨

| 指標 | FFMA (FP32) | Tensor Core FP16→FP32 | Tensor Core INT8 |
|------|-------------|----------------------|------------------|
| 吞吐（T4 整機） | ~8.1 TFLOPS | ~65 TOPS | ~130 TOPS |
| 精度 | 完整 FP32 | FP16 input, FP32 accum | INT8（需量化）|
| 最小操作單位 | 1 FMA（1 thread） | 16×16×16 tile（1 warp）| 8×8×32 tile（1 warp）|
| 適用場景 | 通用科學計算 | DL 訓練 / 推論 | DL 推論 |
| API 複雜度 | 低（直接計算） | 中（WMMA API）| 高（需量化 pipeline）|
| sm_75 (Turing) 支援 | 是 | 是 | 是 |
| BF16 / TF32 | N/A | 否（sm_80+ 才有）| 否 |

---

## 踩雷

### 1. load_matrix_sync 的指標必須 16-byte 對齊

`wmma::load_matrix_sync` 的指標（global 或 shared memory 都算）必須 **16-byte 對齊**，否則是 undefined behavior，在某些 GPU 上會 silent 產生錯誤結果，在某些上會 crash。

確保對齊的方法：
- Global memory：用 `cudaMalloc` 分配的記憶體預設是 256-byte 對齊，沒問題
- Shared memory：宣告時加 `__align__(16)`

```cpp
__shared__ __align__(16) half a_smem[16][16];
```

### 2. ldm 的單位是 elements，不是 bytes

```cpp
wmma::load_matrix_sync(a_frag, a_ptr, 16);  // ldm = 16 elements
```

這裡的 `16` 是 16 個元素，對 FP16 陣列代表 16 × 2 bytes = 32 bytes 的 stride。如果你傳的是 `32`（以為是 bytes），矩陣 tile 會從錯誤位置讀資料，計算結果直接爆掉。

### 3. mma_sync 必須 warp-collective

`wmma::mma_sync` 要求 warp 內所有 32 個 thread **必須同時執行這條指令**。如果用 if 讓部分 thread 跳過，可能產生：
- 錯誤計算結果
- 程式 hang 住（某些 driver 版本）

```cpp
// 錯誤：只有部分 thread 進入這個 if
if (threadIdx.x < 16) {
    wmma::mma_sync(d_frag, a_frag, b_frag, c_frag);  // UB
}

// 正確：確保整個 warp 都呼叫
wmma::mma_sync(d_frag, a_frag, b_frag, c_frag);
```

最安全的做法：在 kernel 最外層做邊界檢查，確保進入計算路徑的 warp 整個完整，不用 if 在 mma_sync 前後分叉。

### 4. BF16 / TF32 在 Turing (sm_75) 不存在

BF16 和 TF32 是 Ampere (sm_80) 才引入的資料型別。如果你的程式碼：
- 設 `--arch=sm_75` 但嘗試用 `wmma::precision::tf32` fragment → 編譯錯誤
- 看到文章說「Turing 的 Tensor Core 支援 TF32」→ 文章有誤

### 5. fragment.x[i] 的 i 和矩陣座標無固定關係

`frag.x[i]` 讓你直接讀寫 fragment 的元素，但 `i` 對應哪個矩陣位置是 implementation-defined，Turing 和 Ampere 的映射不同。

如果你需要「把矩陣第 3 行第 5 列的值塞進 fragment」，用 `load_matrix_sync` 搭配正確的指標算術，不要嘗試用 `frag.x[i]` 自己推算。只有當你需要對整個 fragment 做 element-wise 操作（例如 activation function）且不在意跨架構移植性時，才考慮用 `frag.x[i]`。

---

---

## 動手練習

### 練習 A：用 Nsight Compute 確認 Tensor Core Utilization

Nsight Compute (ncu) 有 `sm__inst_executed_pipe_tensor` 計數器，可以直接量到 Tensor Core 的 warp instruction 執行數。

```bash
# 跑 kernel 並收集 Tensor Core 相關 counter
ncu --metrics sm__inst_executed_pipe_tensor,\
sm__inst_executed_pipe_fma,\
sm__cycles_active,\
sm__warps_active \
./wmma_gemm_binary

# 或用 --set full 取全部 metric，再用 --csv 輸出方便 grep
ncu --set full --csv ./wmma_gemm_binary | grep tensor
```

要觀察的數字：
- `sm__inst_executed_pipe_tensor`：Tensor Core pipe 執行的 warp instruction 數
- `sm__inst_executed_pipe_fma`：FP32 FMA pipe 的 warp instruction 數（正確的 WMMA kernel 這個應該接近 0）
- `sm__cycles_active` vs `sm__warps_active`：可以算出 warp 佔用率

如果 `sm__inst_executed_pipe_tensor` 是 0，代表沒有真正跑到 Tensor Core——最常見原因是 compile target 錯（沒有 `-arch=sm_75`）或 fragment 宣告有誤。

在 Google Colab 上（Tesla T4）可以直接執行 `ncu`，需要 `!apt install nvidia-cuda-toolkit` 或用 `/usr/local/cuda/bin/ncu`。

### 練習 B：比較 FFMA GEMM vs WMMA GEMM 效能

在 Colab 的 T4 上，用 PyTorch 對比 FP32 vs FP16 的 4096×4096×4096 GEMM（M=N=K=4096）：

```python
import torch, time
M, N, K = 4096, 4096, 4096
A32 = torch.randn(M, K, dtype=torch.float32, device='cuda')
B32 = torch.randn(K, N, dtype=torch.float32, device='cuda')
A16, B16 = A32.half(), B32.half()

# FP32 FFMA：關閉 TF32 強制走 FP32 pipe
torch.backends.cuda.matmul.allow_tf32 = False
torch.cuda.synchronize(); t0 = time.perf_counter()
for _ in range(100): torch.mm(A32, B32)
torch.cuda.synchronize(); t_fp32 = (time.perf_counter()-t0)/100

# FP16 Tensor Core
torch.cuda.synchronize(); t0 = time.perf_counter()
for _ in range(100): torch.mm(A16, B16)
torch.cuda.synchronize(); t_fp16 = (time.perf_counter()-t0)/100

flops = 2*M*N*K
print(f"FP32: {t_fp32*1e3:.2f} ms  {flops/t_fp32/1e12:.1f} TFLOPS")
print(f"FP16: {t_fp16*1e3:.2f} ms  {flops/t_fp16/1e12:.1f} TFLOPS")
```

預期：FP16 Tensor Core 比 FP32 FFMA 快 **3~8 倍**。如果兩者速度相近，確認 `allow_tf32 = False` 有生效，或版本太舊導致 PyTorch 沒走 WMMA 路徑。

### 練習 C：比較不同 tile 大小的效能差異

Turing 合法的 WMMA shape 除了 16×16×16，還有 32×8×16 和 8×32×16。替換 fragment 宣告的 template 參數，重新跑 kernel 並用 `ncu` 比較 occupancy：

```cpp
// 32×8×16 shape（sm_75 支援）
wmma::fragment<wmma::matrix_a,    32, 8, 16, half, wmma::row_major> a_frag;
wmma::fragment<wmma::matrix_b,    32, 8, 16, half, wmma::col_major> b_frag;
wmma::fragment<wmma::accumulator, 32, 8, 16, float>                 c_frag;
```

觀察重點：32×8×16 的 accumulator 比 16×16×16 大（32×8=256 vs 16×16=256 相同，但分布不同），寄存器壓力影響 SM occupancy；16×16×16 在多數 memory-bound 場景是較好的平衡點。

---

## 跨章連結

- 前一章：[Ch 29 指令層級真相](./29-instruction-level.md)——FFMA 的吞吐和延遲，是 Tensor Core 的對比基準
- 下一章：[Ch 31 現代特性](./31-modern-features.md)——`cp.async`、TMA，這些記憶體機制是高效能 Tensor Core GEMM 的基礎設施

Part 7 會實作完整的 shared memory tiling + Tensor Core GEMM，才能真正壓到 65 TOPS 的硬體峰值。

---

## 延伸閱讀

1. **NVIDIA Turing Architecture Whitepaper**——官方 Tensor Core 硬體架構說明，附 die shot 和 SM 結構圖；8 TC/SM 的數字可在此確認
2. **Jia et al., "Dissecting the NVIDIA Turing T4 GPU via Microbenchmarking"（arXiv 1903.07486）**——獨立研究者對 T4 微架構的實測分析，包含 Tensor Core 吞吐的測量方法；Turing TC 拓撲結構（8 TC/SM = 4 SMSP × 2 TC）的第二個來源
3. **"Programming Tensor Cores in CUDA 9"（NVIDIA Developer Blog）**——WMMA API 原始設計說明，仍然是最清楚的入門文件
4. **CUTLASS: Fast Linear Algebra in CUDA（NVIDIA GitHub）**——工業級 GEMM 的 shared memory tiling 策略，看懂之後你會理解 cuBLAS 在做什麼
5. **PTX ISA Reference，§9.7.14 Matrix Fragments**——mma.sync 的完整 operand 打包規格，fragment 的 thread→element 映射在這裡有 per-shape 的官方說明

---

> **本章要帶走的三件事**
> 1. Tensor Core 是 warp-collective 的 MMA 硬體，sm_75 的拓撲是 4 SMSP × 2 TC = 8 TC/SM（Jia et al. arXiv 1903.07486 + NVIDIA Turing Whitepaper），每 TC 4×4 硬體 MMA，16×16×16 WMMA tile 展開成多個 4×4 操作；T4 FP16 峰值 65 TOPS vs FP32 FFMA 8.1 TFLOPS
> 2. WMMA API 的 fragment 是 abstract container（thread→element 映射是 implementation-defined），B 用 col-major 是因為 inner dimension k 連續存取才有空間局部性；INT8 用 8×8×32 tile，需要 scale factor 做 quantize/requantize
> 3. 沒有 shared memory tiling 的簡單 WMMA kernel 是 memory-bound（有效 AI ≈ 1 FLOPs/byte，遠低於 ridge point 217 FLOPs/byte），用 Nsight Compute 的 `sm__inst_executed_pipe_tensor` 可驗證 Tensor Core 是否真的在跑
