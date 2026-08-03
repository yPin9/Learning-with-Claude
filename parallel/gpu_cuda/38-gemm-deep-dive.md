# Ch 38 — GEMM 深挖：DL 的核心運算

> **目標**：從 Ch17 基線出發，逐步加入 register tiling、shared memory 雙層分塊、double buffering，理解如何把 GEMM 從 memory-bound 推向 compute-bound，最終逼近 cuBLAS。
> **環境**：CUDA 12.x, Colab T4 (sm_75)

---

## 38.1 為什麼 GEMM 主宰深度學習

深度學習訓練與推理的計算圖，核心幾乎全是矩陣乘法（GEMM，General Matrix-Matrix Multiplication）：

**全連接層（Fully Connected / Linear）**

```
輸出 Y = X @ W^T + b
X: (batch, in_features)
W: (out_features, in_features)
Y: (batch, out_features)
```

直接就是一個 GEMM。

**卷積層（Convolution）via im2col**

卷積把輸入特徵圖展開成 Toeplitz 矩陣（im2col），再做矩陣乘法：

```
im2col:  (batch * H_out * W_out, Kh * Kw * C_in)
weight:  (C_out, Kh * Kw * C_in)
output:  (batch * H_out * W_out, C_out)   ← 又是 GEMM
```

這就是為什麼 cuDNN 內部大量呼叫 cuBLAS GEMM。

**Attention（Transformer）**

```
Q @ K^T / sqrt(d_k)   ← GEMM
softmax(...)           ← element-wise
Att @ V               ← GEMM
```

一個 Multi-Head Attention 包含至少 3 個 GEMM（QK、AttnV、輸出投影），加上 QKV 的 input projection 又是 3 個。

**結論**：優化 GEMM = 優化整個 DL stack。這不是「有空再說」的事——它決定你模型能不能在 24hr 內收斂。

---

## 38.2 從 Ch17 基線往上爬

[Ch17 shared memory tiling](./17-shared-memory-tiling.md) 的方案：

```
每個 thread block 計算 C 的 BM×BN 子矩陣
每個 thread 負責 1 個輸出元素 C[row][col]

loop over K:
    把 A[BM × BK] 載入 shared memory
    把 B[BK × BN] 載入 shared memory
    __syncthreads()
    for k in range(BK):
        c += As[threadRow][k] * Bs[k][threadCol]   ← 每次讀 2 個 SMEM 值，做 1 FMA
    __syncthreads()
```

**瓶頸在哪？**

以 BM=BN=32、BK=32 為例：

| 指標 | 數字 |
|------|------|
| 每個 thread 每次 K-step 做的 FMA | 1 個 |
| 每 FMA 需要的 SMEM 讀取 | 2 次（As + Bs） |
| 算術強度（FLOPs / SMEM access） | ~1 FMA / 2 reads |

SMEM 頻寬雖然遠高於 GMEM，但仍有上限（T4 約 160 B/cycle per SM）。當每個 FMA 都要從 SMEM 讀，SMEM 就成了瓶頸。

[Ch02 Roofline 分析](./02-parallelism-ceilings.md) 告訴我們：要讓 kernel 落在 compute-bound 區域，需要把算術強度推高到超過 roofline 轉折點（T4 約 65 FLOP/Byte）。

實際量測 Ch17 方案（文獻資料，條件：M=N=K=4096，T4）：

```
Ch17 tiled matmul: ~8.5 TFLOPs
T4 peak:           ~65 TFLOPs（FP32）
cuBLAS:            ~55 TFLOPs
```

差距：6.5× 以上。問題出在每個 thread 輸出太少、暫存器使用率低。

---

## 38.3 Register Blocking / Thread Tiling 直覺

核心思路：讓每個 thread 負責更多輸出，把載入的資料留在暫存器裡複用。

### ASCII 圖：TM×TN 切割

```
Block 負責的 C 子矩陣 (BM × BN)：

         BN (= 64)
    ┌────────────────────────────────────┐
    │  t00  t01  t02  t03  t04 ...      │  ← thread 0,1,2,3,...
BM  │  .    .    .    .    .            │
(64)│  .    .    .    .    .            │
    │  tN0  tN1  ...                    │
    └────────────────────────────────────┘

每個 thread 拿到 TM×TN (= 8×8) 的輸出區塊：

         TN = 8
    ┌────────────┐
    │ c[0][0..7] │  ← threadResults[0..7]
 TM │ c[1][0..7] │  ← threadResults[8..15]
 =8 │ ...        │
    │ c[7][0..7] │  ← threadResults[56..63]
    └────────────┘

Block 內 thread 數 = (BM/TM) × (BN/TN) = 8 × 8 = 64 threads
```

**Block 維度說明：**
- `BM = 64`：block 在 M 維負責的行數
- `BN = 64`：block 在 N 維負責的列數
- `BK = 8`：每次從 K 維載入的深度（越大 GMEM transaction 效率越高，但 SMEM 佔用越多）
- `TM = TN = 8`：每個 thread 負責的輸出行/列數

選 BM=BN=64、BK=8 而非更大 BK 的原因：BK×(BM+BN) × sizeof(float) 是 SMEM 消耗。BK=8 時約 8×(64+64)×4 = 4 KB，遠低於 T4 每 SM 96 KB 上限，留給 double buffering（需 2×）有充裕空間。

### 算術強度提升

```
原本（1 output/thread）：
  每 K-step：載入 1 個 As + 1 個 Bs → 做 1 FMA
  算術強度 ≈ 1/2

TM=TN=8（64 outputs/thread）：
  每 K-step：
    載入 regA[TM=8]（從 As 讀 8 個）
    載入 regB[TN=8]（從 Bs 讀 8 個）
    做外積：8×8 = 64 個 FMA

  算術強度 = 64 FMA / 16 reads = 4 FMA/read（提升 8×）
```

這才是關鍵：每次從 SMEM 把一行 A 和一行 B 讀進暫存器，就產生 TM×TN 個 FMA，讓「讀進來的值」被最大化複用。

---

## 38.4 雙層分塊架構

把 SMEM tiling（Ch17）與 register tiling 疊在一起：

```
層級 1（SMEM tiling）：
  把 A、B 的子矩陣切成 BM×BK、BK×BN 的 tiles
  載入 GMEM → SMEM（coalesced 存取）

層級 2（Register tiling）：
  在 SMEM tile 內，每個 thread 把自己負責的
  A 行（regA[TM]）、B 列（regB[TN]）
  拉進暫存器，做 TM×TN 外積

全域 loop（K 維分段）：
  for bkIdx in range(K / BK):
    Load GMEM tile → As, Bs
    __syncthreads()
    for k in range(BK):
      load regA[0..TM-1] = As[threadRow*TM .. ][k]
      load regB[0..TN-1] = Bs[k][threadCol*TN ..]
      for tm in range(TM):
        for tn in range(TN):
          threadResults[tm*TN + tn] += regA[tm] * regB[tn]
    __syncthreads()
```

---

## 38.5 Double Buffering（回連 Ch31 cp.async）

[Ch31 modern features](./31-modern-features.md) 介紹了 `cp.async` / `__pipeline_memcpy_async`，讓我們在計算當前 tile 時，**非同步預取**下一個 tile。

**問題**：Ch38.4 的版本中，每次 K-step 的流程是：

```
load tile K   → 等待 → compute tile K → load tile K+1 → 等待 → compute...
              ↑ 延遲暴露
```

**Double buffering 方案**：

```
兩組 SMEM buffer: As[2][BM*BK], Bs[2][BK*BN]

iter 0:
  sync load tile 0 → buffer[0]
  async issue tile 1 → buffer[1]（不等）
  compute buffer[0]
  wait tile 1
iter 1:
  async issue tile 2 → buffer[0]（不等）
  compute buffer[1]
  wait tile 2
...

時間線：
  GMEM load tile 1 ████████
  compute tile 0            ████████
                            ← 重疊 →
```

條件：tile 的 GMEM 延遲 < 計算 tile 的時間。對 BK=8、TM=TN=8：
- 計算量：BM/TM × BN/TN threads × BK × TM × TN FMA = 64 × 8 × 64 = 32768 FMA/block
- GMEM load：(BM×BK + BK×BN) × 4 bytes = (64×8 + 8×64) × 4 = 4096 bytes

T4 GMEM 頻寬 ~300 GB/s → 4096 bytes ≈ 13.6 ns。BK=8 的計算在 T4 Tensor Core 環境下可以覆蓋這個延遲。

---

## 38.6 完整優化版 Kernel 骨架

```cuda
// 常數定義（必須全部在編譯時確定，才能放進 shared memory 靜態陣列）
#define BM 64    // block tile M 維大小
#define BN 64    // block tile N 維大小
#define BK 8     // block tile K 維大小（影響 SMEM 佔用與 cp.async 效率）
#define TM 8     // 每個 thread 負責 M 維 TM 行
#define TN 8     // 每個 thread 負責 N 維 TN 列
// block 內 thread 數 = (BM/TM) * (BN/TN) = 8 * 8 = 64

__global__ void sgemm_register_tiled(
    int M, int N, int K,
    float alpha,
    const float* __restrict__ A,  // (M, K) row-major
    const float* __restrict__ B,  // (K, N) row-major
    float beta,
    float* __restrict__ C         // (M, N) row-major
) {
    // ── 位置計算 ──────────────────────────────────────────────────
    // block 在 C 矩陣中的起始行、列
    const int cRow = blockIdx.y * BM;  // block 負責的 C 起始行
    const int cCol = blockIdx.x * BN;  // block 負責的 C 起始列

    // thread 在 block 內的序號（0..63），以及其在 TM×TN 子塊中的位置
    const int threadIdx1D = threadIdx.y * blockDim.x + threadIdx.x;
    // block 用 (BM/TM, BN/TN) = (8, 8) 的 thread 排列
    const int threadRow = threadIdx1D / (BN / TN);  // 0..7：負責哪組 TM 行
    const int threadCol = threadIdx1D % (BN / TN);  // 0..7：負責哪組 TN 列

    // ── Shared memory（double buffer：2 組）────────────────────────
    // As: 2 × BM × BK，Bs: 2 × BK × BN
    __shared__ float As[2][BM * BK];  // 2 * 64 * 8 * 4B = 4096 B
    __shared__ float Bs[2][BK * BN];  // 2 * 8  * 64 * 4B = 4096 B
    // 總 SMEM：8192 B / block，遠低於 T4 96 KB/SM 上限

    // ── Register 暫存區 ───────────────────────────────────────────
    float threadResults[TM * TN] = {0.0f};  // 64 個輸出（在暫存器中）
    float regA[TM];   // 當前 k-step 的 A 行向量（8 個 float）
    float regB[TN];   // 當前 k-step 的 B 列向量（8 個 float）

    // ── 各 thread 負責載入 GMEM → SMEM 的位置 ────────────────────
    // 每個 thread 負責搬 A tile 中的哪幾個 float？
    // BM*BK = 512 個 float，64 threads 每人搬 8 個
    const int innerRowA = threadIdx1D / BK;   // 0..7 → A tile 的行
    const int innerColA = threadIdx1D % BK;   // 0..7 → A tile 的列

    // B tile：BK*BN = 512 個 float，每人搬 8 個
    const int innerRowB = threadIdx1D / BN;   // 0..0 → B tile 的行（BK=8, BN=64 → stride=64）
    const int innerColB = threadIdx1D % BN;   // 0..63 → B tile 的列

    // 每人搬幾行？BM*BK / (BK * numThreads) = 64/64 = 1 行
    // 實際上 innerRowA 步長 = numThreads/BK = 64/8 = 8，所以每人恰好 1 行
    // （若 tile 更大可能需 loop，這裡省略）

    // ── 預取 tile 0 到 buffer[0] ──────────────────────────────────
    int buf = 0;  // 當前使用的 buffer index
    // 同步載入 tile 0（bkIdx=0）
    As[buf][innerRowA * BK + innerColA] =
        A[(cRow + innerRowA) * K + (0 * BK + innerColA)];
    Bs[buf][innerRowB * BN + innerColB] =
        B[(0 * BK + innerRowB) * N + (cCol + innerColB)];
    __syncthreads();

    // ── 主 K-loop ────────────────────────────────────────────────
    for (int bkIdx = 0; bkIdx < K / BK - 1; bkIdx++) {
        // 非同步預取下一個 tile 到另一個 buffer
        int nextBuf = 1 - buf;
        int nextBk = bkIdx + 1;
        // 這裡用同步載入示意；真正的 cp.async 見下方說明
        As[nextBuf][innerRowA * BK + innerColA] =
            A[(cRow + innerRowA) * K + (nextBk * BK + innerColA)];
        Bs[nextBuf][innerRowB * BN + innerColB] =
            B[(nextBk * BK + innerRowB) * N + (cCol + innerColB)];

        // 計算當前 buffer（buf）的 tile
        for (int k = 0; k < BK; k++) {
            // 從 SMEM 載入 regA[TM]（A tile 的第 k 列中，屬於本 thread 的 TM 行）
            for (int tm = 0; tm < TM; tm++) {
                regA[tm] = As[buf][(threadRow * TM + tm) * BK + k];
                // threadRow * TM：本 thread 在 A tile 中的起始行
            }
            // 從 SMEM 載入 regB[TN]（B tile 的第 k 行中，屬於本 thread 的 TN 列）
            for (int tn = 0; tn < TN; tn++) {
                regB[tn] = Bs[buf][k * BN + (threadCol * TN + tn)];
                // threadCol * TN：本 thread 在 B tile 中的起始列
            }
            // 外積：TM × TN = 64 個 FMA
            // 索引公式：threadResults[tm * TN + tn]（row-major 展平）
            for (int tm = 0; tm < TM; tm++) {
                for (int tn = 0; tn < TN; tn++) {
                    threadResults[tm * TN + tn] += regA[tm] * regB[tn];
                }
            }
        }

        __syncthreads();  // 確保 nextBuf 載入完成後再切換
        buf = nextBuf;
    }

    // 最後一個 tile（已在 buf 中）：只計算，不再預取
    for (int k = 0; k < BK; k++) {
        for (int tm = 0; tm < TM; tm++) {
            regA[tm] = As[buf][(threadRow * TM + tm) * BK + k];
        }
        for (int tn = 0; tn < TN; tn++) {
            regB[tn] = Bs[buf][k * BN + (threadCol * TN + tn)];
        }
        for (int tm = 0; tm < TM; tm++) {
            for (int tn = 0; tn < TN; tn++) {
                threadResults[tm * TN + tn] += regA[tm] * regB[tn];
            }
        }
    }

    // ── 寫回 C ───────────────────────────────────────────────────
    for (int tm = 0; tm < TM; tm++) {
        for (int tn = 0; tn < TN; tn++) {
            int cGlobalRow = cRow + threadRow * TM + tm;
            int cGlobalCol = cCol + threadCol * TN + tn;
            C[cGlobalRow * N + cGlobalCol] =
                alpha * threadResults[tm * TN + tn]
                + beta * C[cGlobalRow * N + cGlobalCol];
        }
    }
}
```

**Kernel launch 參數：**

```cuda
dim3 gridDim(N / BN, M / BM);    // 每個 block 負責 BM×BN 的輸出
dim3 blockDim(BN / TN, BM / TM); // = (8, 8) = 64 threads/block
// M=N=K=4096 時：grid = (64, 64) = 4096 blocks
sgemm_register_tiled<<<gridDim, blockDim>>>(M, N, K, 1.0f, A, B, 0.0f, C);
```

### 關於真正的 cp.async（sm_80+）

上面的 double buffering 用同步載入模擬「概念」。要在 Ampere（sm_80+）上啟用真正的非同步預取：

```cuda
// 需要 #include <cuda/pipeline>，或直接用 PTX
// sm_75（T4）不支援 cp.async，改用 __ldg + 手動 pipeline
// sm_80+（A100）可用：
#include <cuda/pipeline>
auto pipe = cuda::make_pipeline();
cuda::pipeline_shared_state<cuda::thread_scope_block, 2> shared_state;
pipe.producer_acquire();
cuda::memcpy_async(As[nextBuf], src_ptr, sizeof(As[0]), pipe);
pipe.producer_commit();
// ... 計算 ...
pipe.consumer_wait();
// 注意：T4（sm_75）上此功能不可用，Colab T4 需改回同步版本
// （Colab 預期，未在本機實測）
```

---

## 38.7 Roofline 分析：從 memory-bound 到 compute-bound

[Ch02 Roofline](./02-parallelism-ceilings.md) 建立了框架，這裡具體計算各版本落點。

**T4 硬體規格（sm_75）：**

| 指標 | 數值 |
|------|------|
| FP32 峰值算力 | ~65.1 TFLOPs（Tensor Core） / ~8.1 TFLOPs（FP32 CUDA Core） |
| GMEM 頻寬 | ~300 GB/s |
| Roofline 轉折點（CUDA Core FP32） | 8.1 × 10¹² / (300 × 10⁹) ≈ 27 FLOP/Byte |

**各版本算術強度（M=N=K=N，忽略邊界）：**

```
Naive（無 SMEM tiling）：
  GMEM 讀取：O(N³) bytes（B 反覆讀）
  算術強度：O(1) FLOP/Byte → memory-bound，嚴重

Ch17 SMEM tiling（BM=BN=BK=32）：
  每個元素從 GMEM 讀 1 次 → 2N³ bytes 讀取（A、B 各一遍）
  算術強度 = 2N³ FLOPs / (2N³ × 4 bytes) × (N / BK) ≈ BK/4 ≈ 8 FLOP/Byte
  仍在 memory-bound 區（< 27）

Register tiling（BM=BN=64, BK=8, TM=TN=8）：
  GMEM 讀取不變：2N³ bytes（tiling 本身不改 GMEM 次數）
  但減少 SMEM 壓力，讓 SMEM 頻寬不成為次要瓶頸
  有效算術強度：約 16-20 FLOP/Byte（逼近轉折點）

Double buffering：
  GMEM 延遲隱藏 → 提升 utilization，不改算術強度
  讓 SM 的實際 GMEM 頻寬利用率從 ~60% 提升到 ~85%
```

**視覺化：**

```
TFLOPs
  65 │                                         ● Tensor Core peak
     │                                    ╱
  20 │                              ╱    ● cuBLAS (~55 TFLOPs)
     │                        ╱   ● register tiling (~16 TFLOPs)
   8 │              ╱ ←轉折點  ● Ch17 (~8.5 TFLOPs)
     │      ╱
   1 │╱
     └────────────────────────────── FLOP/Byte
         1    8   27                65
              ↑轉折（CUDA Core FP32）
```

數字來源：Simon Boehm blog（文獻資料，條件：M=N=K=4096，A100；T4 上值會不同）。

---

## 38.8 為什麼能逼近 cuBLAS——差距從哪裡來

Simon Boehm 的 CUDA matmul 最終版達到 cuBLAS 的 ~95%。差距主要在：

### 我們的實作 vs cuBLAS 的差異

**1. Tensor Core 使用（[Ch30](./30-tensor-core.md) 已介紹）**

cuBLAS 在 sm_75+ 自動使用 Tensor Core（WMMA/MMA PTX），可讓 FP16/BF16 GEMM 達到 8× 以上的 FLOP 提升。我們的 FP32 kernel 不走 Tensor Core。

**2. Vectorized load（float4）**

cuBLAS 用 `float4` 指令一次搬 16 bytes，減少 load 指令數，更高效地對齊 128-bit transaction：

```cuda
// 我們的版本（效率較低）：
As[idx] = A[row * K + col];  // 單個 float load

// 高效版本（需確保 16-byte 對齊）：
float4 tmp = reinterpret_cast<const float4*>(&A[row * K + col])[0];
reinterpret_cast<float4*>(&As[idx])[0] = tmp;
```

**3. 更細的 tile 形狀調優**

cuBLAS 對不同 SM 架構有手工調優的 tile 大小（透過自動調優資料庫）。沒有一個尺寸對所有 N 都最優。

**4. Bank conflict 消除**

SMEM 有 32 個 bank（每 4 bytes 一個），不當的存取模式會導致 bank conflict，序列化原本可平行的讀寫。cuBLAS 會在 tile 儲存上加 padding：

```cuda
// 加一個 padding column 來錯開 bank
__shared__ float As[BM][BK + 1];  // +1 讓每行偏移 1 個 bank
```

**5. Warp-level scheduling 最佳化**

cuBLAS 的 warp 分配方式讓同一 warp 的 thread 存取的 SMEM 範圍有更好的 locality，與硬體 scheduler 協同。

**差距量化（文獻，A100，M=N=K=4096）：**

| 版本 | TFLOPs | 佔 cuBLAS |
|------|--------|-----------|
| Naive | 0.3 | ~0.5% |
| SMEM tiling | 8.5 | ~15% |
| Register tiling | 16 | ~29% |
| + float4 load | 20 | ~36% |
| + vectorized + padding | 45 | ~82% |
| CUTLASS（最優配置） | ~55 | ~100% |
| cuBLAS | 55 | 100% |

---

## 38.9 踩雷清單

### 雷 1：threadResults 索引搞錯

最常見的 bug：外積的 flatten 索引寫錯。

```cuda
// 錯誤：把 tm 和 tn 的維度弄反，或用加法代替乘法
threadResults[tm + tn] += ...;  // 錯！tm 和 tn 會 alias

// 正確：row-major flatten（TN 是每行的寬度）
threadResults[tm * TN + tn] += regA[tm] * regB[tn];
```

### 雷 2：SMEM bank conflict

TM=TN=8 時，如果 BK=8，`As[threadRow * TM + tm][k]` 的多個 thread 可能踩同一個 bank。調試方式：用 `ncu --metrics l1tex__data_bank_conflicts_pipe_lsu_mem_shared_op_ld.sum` 量。

解法：在 As 的第二維加 padding（+1 或 +2）：
```cuda
__shared__ float As[BM][BK + 1];  // 每行多 1 個 float 的 padding
```

### 雷 3：邊界未處理

上面的骨架假設 M、N、K 皆為 BM、BN、BK 的整數倍。實際用於任意大小需要：

```cuda
// load 時加邊界守衛
float val = 0.0f;
if ((cRow + innerRowA) < M && (bkIdx * BK + innerColA) < K)
    val = A[(cRow + innerRowA) * K + (bkIdx * BK + innerColA)];
As[buf][innerRowA * BK + innerColA] = val;
```

忘記邊界守衛 → out-of-bounds read → undefined behavior，非常難 debug（有時 GPU 不 segfault，只是算出 NaN）。

### 雷 4：double buffering 下的 __syncthreads 位置

**錯誤方式**：在 nextBuf 的 store 完成「之前」就開始讀 nextBuf 計算。

正確流程：

```
store nextBuf → __syncthreads() → 切換 buf = nextBuf → 讀 buf 計算
```

缺少 `__syncthreads()` 會導致 warp 之間 race condition，結果不確定性極高，但在小矩陣上可能「僥倖正確」，只在大矩陣時出錯。

### 雷 5：sm_75（T4）不支援 cp.async

T4 是 sm_75，`cp.async` 是 sm_80（Ampere）才引入的 ISA extension。Colab 免費 T4 上要模擬 double buffering，只能用同步 load + 手動交替 buffer。

```cuda
// sm_75 上 __pipeline_memcpy_async 會 compile error 或 runtime error
// 解法：根據 __CUDA_ARCH__ 條件編譯
#if __CUDA_ARCH__ >= 800
    cuda::memcpy_async(...);
#else
    As[nextBuf][...] = A[...];  // fallback 同步
#endif
```

---

## 38.10 動手練習

**練習 A：TM=TN=4 baseline**

先從 `TM=TN=4`（每 thread 16 個輸出）開始實作，確認輸出正確性（與 cublasSgemm 對比），再調高到 `TM=TN=8`，觀察效能變化。用 `nvprof` 或 `ncu` 比較兩者的 register usage 和 achieved occupancy。

**練習 B：Bank conflict 診斷**

把 `BK=8` 改成 `BK=16`，用 Nsight Compute 的 shared memory 分析頁面，找出 bank conflict 的數量，再加 padding 修復，量測修復前後的 SMEM 效率差距。（Colab 預期，未在本機實測）

**練習 C：float4 向量化 load**

把 A、B 的 GMEM→SMEM load 改成 `float4`（需 4-float 對齊）。量測 GMEM 讀取的 transaction 數是否減少到原來的 1/4。

**練習 D：邊界處理**

讓 kernel 支援任意 M、N、K（不限 tile 倍數）。測試 M=N=K=4097 的正確性。

---

## 38.11 本章重點

- **GEMM 是 DL 核心**：全連接、im2col 卷積、Attention 全部化約為 matmul，GEMM 效能決定模型訓練速度上限。
- **Ch17 瓶頸**：每 thread 只算 1 個輸出 → SMEM 讀寫成瓶頸，算術強度低。
- **Register tiling（TM×TN）**：每個 thread 負責 TM×TN 個輸出，把 SMEM 資料複用 TM（或 TN）次，算術強度從 ~1 FMA/2 reads 提升到 ~4+ FMA/read。
- **雙層分塊**：SMEM tiling 減少 GMEM 流量；register tiling 減少 SMEM 壓力；兩者疊加才能在 roofline 上往右上角移動。
- **Double buffering**：讓 GMEM 載入與 tile 計算重疊，隱藏記憶體延遲；T4（sm_75）無原生 cp.async，需手動模擬。
- **外積索引**：`threadResults[tm * TN + tn] += regA[tm] * regB[tn]` 是核心操作，索引必須用 TN 做行寬 flatten。
- **與 cuBLAS 差距**：主要在 Tensor Core（Ch30）、float4 load、bank conflict 消除、tile 自動調優，我們的純 FP32 版本理論上可達 cuBLAS FP32 的 ~80-90%（文獻資料，條件依機器而異）。

---

## 38.12 自我檢核

1. `TM=TN=8, BK=8` 時，每個 K-step 的外積做幾次 FMA？SMEM 讀了幾個 float？算術強度（FMA/read）是多少？
2. `threadResults[tm * TN + tn]` 的索引為什麼不能寫成 `threadResults[tm + tn]`？會造成什麼後果？
3. Double buffering 需要兩組 SMEM buffer，SMEM 消耗加倍。以 `BM=BN=64, BK=8` 計算，double buffering 版本消耗多少 SMEM？T4 的 96 KB SMEM 理論上最多可同時住幾個 block？
4. 為什麼 Bank conflict 在 `BK=8` 時特別容易發生？padding 的原理是什麼？
5. cuBLAS 在相同 FP32 條件下比我們的 register tiling 版本快，主要差距不在算法，而在哪三個實作細節？

---

## 38.13 延伸閱讀

- **Simon Boehm, "How to Optimize a CUDA Matmul Kernel for cuBLAS-like Performance"** (2022)  
  https://siboehm.com/articles/22/CUDA-MMM  
  本章技術內容的主要參考。從 naive 到 register tiling + vectorized load 的 9 個版本演進，附完整 benchmark 資料。

- **CUTLASS 文件與源碼**  
  https://github.com/NVIDIA/cutlass  
  NVIDIA 官方 CUDA C++ GEMM template library。架構分 Prologue/Mainloop/Epilogue，tile 設計比本章更細（warp-level 甚至 instruction-level tile），是研究生產級 GEMM 的最佳材料。

- **Nsight Compute User Guide — Shared Memory**  
  https://docs.nvidia.com/nsight-compute/ProfilingGuide/index.html#memory-chart  
  Bank conflict 診斷的官方工具說明。

- **Lei Mao, "CUTLASS Tutorial"**  
  https://leimao.github.io/blog/CUTLASS-Thread-Block-Level-GEMM/  
  用視覺化圖解 CUTLASS 的 thread block 級 GEMM，補充本章 ASCII 圖無法展示的細節。

- **NVIDIA "Matrix Multiplication Background User's Guide"**  
  https://docs.nvidia.com/deeplearning/performance/dl-performance-matrix-multiplication/  
  針對 DL 場景的 GEMM 效能指南，說明 cuDNN 如何選擇 algorithm。

- **前一章**：[Ch 37 Triton：用 Python 寫高效 GPU kernel](./37-triton.md)  
  Triton 的 tiled matmul 本質上是本章概念的 Python DSL 版本，對照閱讀可加深理解。

---

→ [Ch 39 卷積：從 im2col 到 Winograd](./39-convolution.md)
