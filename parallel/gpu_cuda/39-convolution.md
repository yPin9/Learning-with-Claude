# Ch 39 — 卷積的 GPU 實作

> **目標**：理解 GPU 上卷積的三條主要實作路線（im2col+GEMM、implicit GEMM、Winograd），知道各路線的取捨邊界，以及為什麼 cuDNN 需要 heuristic 而不是單一演算法。
> **環境**：CUDA 12.x, Colab T4 (sm\_75)
> **前置**：[Ch 38 — GEMM 深度解析](./38-gemm-deep-dive.md)（im2col 化約成 matmul 的關鍵步驟在此展開）

---

## 39.1 卷積在 DL 中的角色

ResNet-50 有 49 層卷積，VGG-16 有 13 層，EfficientNet 系列更是把卷積堆到極致。即使 Transformer 架構崛起，卷積仍然是影像/視訊/音訊模型的基本積木——因為它的歸納偏置（inductive bias）對空間局部性的利用，是全連接層和 self-attention 難以取代的。

標準 2D 卷積的輸入輸出規格（NCHW 格式）：

```
輸入 X：[N, C_in,  H,    W   ]
濾波 W：[C_out, C_in, kH,  kW  ]
輸出 Y：[N, C_out, H_out, W_out]

H_out = (H + 2*pad - kH) / stride + 1
W_out = (W + 2*pad - kW) / stride + 1
```

**算術量（FLOPs）：**

```
FLOPs = 2 × N × C_out × H_out × W_out × C_in × kH × kW
```

factor 2 是因為每個 MAC（multiply-accumulate）算 2 個 FLOP。以 ResNet-50 第一層（3×224×224 輸入，64 個 7×7 kernel，stride=2）為例：

```
H_out = W_out = (224 + 3 - 7) / 2 + 1 = 112
FLOPs ≈ 2 × 1 × 64 × 112 × 112 × 3 × 7 × 7 ≈ 236M
```

一個 batch 就快 2.4 億個 FLOP。整個 ResNet-50 前向約 4 GFLOPs。問題很清楚：**卷積是訓練/推論的效能關鍵路徑。**

---

## 39.2 直接卷積（Direct Convolution）的計算模式

最直覺的實作就是暴力展開五個迴圈：

```c
// 偽碼，NCHW layout
for (int n = 0; n < N; n++)
  for (int co = 0; co < C_out; co++)
    for (int oh = 0; oh < H_out; oh++)
      for (int ow = 0; ow < W_out; ow++) {
        float acc = 0;
        for (int ci = 0; ci < C_in; ci++)
          for (int kh = 0; kh < kH; kh++)
            for (int kw = 0; kw < kW; kw++)
              acc += X[n][ci][oh*stride+kh][ow*stride+kw]
                   * W[co][ci][kh][kw];
        Y[n][co][oh][ow] = acc;
      }
```

**問題：** 這個迴圈結構對 GPU 不友好。

- 最內層 `(ci, kh, kw)` 的 reduce 是序列的
- 輸出位置 `(n, co, oh, ow)` 才是可以平行化的維度
- 記憶體存取對 `X` 不規則（stride 跳躍），對 `W` 反覆讀同一份

GPU 需要「把問題轉換成大矩陣乘法」才能發揮 Tensor Core 的威力。以下三條路線都是在解這個轉換問題。

**Register 壓力問題：** 即使是 direct conv 的 CUDA kernel，若每個 thread 負責一個輸出位置，內層 `(ci, kh, kw)` 三重迴圈 3×3×C\_in 次累加都在一個 register 上，看似沒問題——但 C\_in=512 時需要 512×9=4608 次 global memory load（X 每次 cache miss 機率高），而 W 的 9×512 個元素若全部 preload 進 register 需要 4608 個 register，遠超過每個 thread 256 register 的硬上限。實際的 direct conv CUDA kernel 需要 tiling 策略，跟 GEMM 一樣複雜——與其重新發明輪子，不如直接化約成 GEMM 用成熟的 cuBLAS。

---

## 39.3 三條路線詳解

### 39.3.1 路線一：im2col + GEMM

**直覺：把卷積化約成矩陣乘法。**

im2col（image to column）的核心想法：把每個輸出 patch 所需的輸入資料「展開」成一列，讓 `(oh, ow)` 索引走過整個特徵圖。

```
輸入 patch (3×3 kernel, C_in=2):
  原本需要在 X 裡跳來跳去讀 3×3×2=18 個值

im2col 把它攤平成一個 row vector [length = kH × kW × C_in = 18]
每個輸出位置 (oh, ow) 對應一個 row

展開後得到：
  col_matrix: (H_out × W_out) × (kH × kW × C_in)
  weight_mat: C_out × (kH × kW × C_in)

卷積 = weight_mat × col_matrix^T
     = [C_out × (kH×kW×C_in)] × [(kH×kW×C_in) × (H_out×W_out)]
     = [C_out × H_out×W_out]     → reshape 回 [C_out, H_out, W_out]
```

**ASCII 圖解：**

```
X [C_in, H, W]             col_matrix [(H_out×W_out), (kH×kW×C_in)]
┌─────────────┐            ┌──────────────────────────────────────┐
│  patch@(0,0)│            │ x00 x01 x02 x10 x11 x12 ... (patch) │
│  patch@(0,1)│   im2col   │ x01 x02 x03 x11 x12 x13 ...         │
│  patch@(0,2)│  ───────►  │ ...                                  │
│  ...        │            │ 每 row 是一個輸出位置展開的 patch     │
└─────────────┘            └──────────────────────────────────────┘
                                           │
                           GEMM (cuBLAS)   │
                                           ▼
                           Y_mat [C_out, H_out×W_out]
```

**取捨：**

| 優點 | 缺點 |
|------|------|
| 直接複用高度優化的 cuBLAS | 展開後記憶體 = kH×kW×C_in 倍（3×3 就 9 倍） |
| 實作簡單，Caffe 早期採用 | 記憶體頻寬成為瓶頸，不適合大特徵圖 |
| 支援任意 kernel size | im2col 本身也有 kernel launch overhead |

**為什麼不選 im2col（現代框架的主力不是它）？** 記憶體放大係數是硬傷。以 VGG-16 conv3（256 channels，3×3 kernel，56×56 feature map）為例，col\_matrix 的大小是 `(56×56) × (9×256) ≈ 72M floats ≈ 288 MB`（原輸入只有 `256×56×56×4 ≈ 3 MB`）。訓練時 batch size 8 就需要 2 GB 額外空間——這是 cuDNN 轉向 implicit GEMM 的直接原因。

im2col 的另一個問題是 **bandwidth 效率**：展開過程本身需要把 X 的資料 copy 到 col\_matrix，這個 copy kernel 花費 time 不算在 GEMM 內，是純粹的額外 overhead。即使 col\_matrix 能完全 cache，這筆 time 依然存在。現代 cuBLAS 的 GEMM kernel 達到 70-80% peak FLOPS，而 im2col 整體（copy + GEMM）通常只達 50-60%，剩下的都被 copy kernel 吃掉。

---

### 39.3.2 路線二：Implicit GEMM（cuDNN 主力）

**直覺：做矩陣乘法，但不真的展開 col\_matrix；在 GEMM inner loop 裡動態算索引。**

im2col 的 col\_matrix 有大量重疊的元素（相鄰 patch 共用大部分資料）。Implicit GEMM 的想法：

```
GEMM inner loop 每次需要 col_matrix[row][k] 時，
不從記憶體讀（因為沒有 col_matrix），
而是即時算出對應的 X 座標：

  k → (ci, kh, kw)        via integer decomposition
  row → (oh, ow)           via integer decomposition
  X_idx = (ci, oh*stride + kh, ow*stride + kw)
  col_matrix[row][k] = X[ci][oh*stride+kh][ow*stride+kw]
```

**記憶體節省：** 完全不需要額外的 col\_matrix，輸入 X 就地讀取。

**代價：** 每次存取 X 都要做整數除法/取模計算索引，比直接記憶體讀取多幾個算術指令。但現代 GPU 上整數運算便宜，加上 shared memory tiling 可以讓這個代價攤薄。

cuDNN 的 implicit GEMM kernel 還額外做了：

- **NHWC 布局優先**：讓 `C_in` 維度連續，比 NCHW 對 Tensor Core 更友好
- **Warp-level tiling**：每個 warp 負責輸出矩陣的一個 tile，用 `wmma` 指令呼叫 Tensor Core
- **Double buffering**：計算與下一輪 global memory load 重疊

**為什麼 implicit GEMM 比 im2col 好但不是唯一？** 計算到記憶體的比例（arithmetic intensity）有其上限。當 kernel size 是 3×3 時，每個輸出需要 `9 × C_in` 次乘加，對 `9 × C_in` 次記憶體存取——arithmetic intensity 只有 `2×C_out` 左右（假設 weight reuse）。3×3 kernel 的 arithmetic intensity 仍然有限，Winograd 可以更進一步減少乘法數量。

**Implicit GEMM 與 Ch 38 GEMM 的關係：** Ch 38 中 GEMM 的 tiling 策略（BM×BK×BN 的 shared memory tile）完整適用於 implicit GEMM——差別只在 A 矩陣（對應 col\_matrix）的「讀取」是動態計算索引而非直接 pointer 存取。cuDNN 的 implicit GEMM kernel 可以看成「A 矩陣 loader 被替換成 im2col 索引計算」的 GEMM kernel 變體。這就是為什麼 GEMM 章節（Ch 38）是本章的直接前置。

---

### 39.3.3 路線三：Winograd（乘法節省）

**直覺：用更少的乘法換更多的加法，因為加法比乘法便宜（在純 ALU 上成立）。**

Winograd 的核心是 Cook-Toom 算法的有限域版本。對 3×3 kernel 最有效的是 F(2×2, 3×3)：用 **16 次乘法**算出 **2×2 的輸出 tile**，而直接卷積需要 `4 × 9 = 36` 次乘法。**文獻/理論節省比：36/16 = 2.25x**（條件：純乘法計數，不含 transform overhead；實測受記憶體頻寬和並行度影響，實際加速通常 1.5-2x）。

**F(2×2, 3×3) 的 transform 流程：**

```
輸入 tile d：4×4 patch（因為輸出 2×2，kernel 3×3，需要 2+3-1=4）
filter g：3×3

步驟一：filter transform（只做一次，訓練時每 epoch 做，推論時提前算好）
  G_hat = G × g × G^T    （4×4 matrix，g 是 3×3 filter）

  G = [ 1    0    0  ]
      [1/2  1/2  1/2 ]   (4×3)
      [1/2 -1/2  1/2 ]
      [ 0    0    1  ]

步驟二：input transform（每個 4×4 tile 都做）
  D_hat = B^T × d × B    （4×4 matrix）

  B^T = [ 1   0  -1   0 ]
        [ 0   1   1   0 ]   (4×4)
        [ 0  -1   1   0 ]
        [ 0   0  -1   1 ]   ← 注意：教材常見的 B^T 版本

步驟三：pointwise 乘法（這裡才用到 16 次乘法）
  M = G_hat ⊙ D_hat      （elementwise multiply，4×4）

步驟四：output transform
  Y_tile = A^T × M × A   （2×2 output tile）

  A^T = [ 1   1   1   0 ]
        [ 0   1  -1  -1 ]   (2×4)
```

**整張圖：**

```
3×3 filter g         4×4 input tile d
     │                      │
  G transform            B^T transform
     │                      │
  4×4 G_hat             4×4 D_hat
          \               /
           elementwise ⊙（16 次乘法）
                   │
                 4×4 M
                   │
              A^T transform
                   │
              2×2 output Y
```

**取捨：**

| 優點 | 缺點 |
|------|------|
| 文獻理論乘法節省 2.25x（3×3 kernel） | 只對小 kernel 有效（3×3 最佳，5×5 效果銳減） |
| Transform 矩陣全是加法和 1/2 縮放 | FP16 精度損失（transform 含 1/2，累積誤差） |
| 對 stride=1 最有效 | Stride>1 需要修改 transform，優勢消失 |
| cuDNN 對 3×3/stride=1/FP32 預設啟用 | 大 batch 的 FP16 訓練可能需要降回 FP32 |

**FP16 精度問題的根本原因：** G 矩陣含 1/2 係數，在 FP16 下 1/2 可以精確表示，但 transform 是連鎖運算——4×4 的 G\_hat 每個元素是多個 `1/2 × filter_weight` 的線性組合，後續 elementwise 乘法再放大，output transform 又一次線性組合。每一步都有 rounding，最終誤差比 direct convolution 大 3-5 個 ULP（unit of least precision）。BN（Batch Norm）對此很敏感。

---

### 39.3.4 路線四：FFT 卷積（大 kernel）

**直覺：時域卷積 = 頻域逐點乘法，O(HW log HW) 優於 O(HW × kH × kW)。**

```
X_freq = FFT(zero_padded_X)        # (N, C_in, H', W') 頻域
W_freq = FFT(zero_padded_W)        # (C_out, C_in, H', W') 頻域
Y_freq = Σ_ci X_freq[:,ci] * W_freq[:,ci]   # 頻域逐點乘
Y      = IFFT(Y_freq)
```

**取捨：**

| 優點 | 缺點 |
|------|------|
| kernel size 越大越划算（7×7、11×11） | stride>1 無效（需要先做再 downsample） |
| 可以批次處理多個 kernel | Zero-padding overhead（要補到 2 的冪次或特定大小） |
| cuFFT 高度優化 | 複數乘法的記憶體開銷大 |
| | 小 kernel（3×3）FFT overhead 遠大於節省 |

**為什麼現代 CNN 不常用 FFT？** AlexNet 後，業界大量轉向 3×3 kernel（VGG 開始），FFT 的甜蜜點消失。Depthwise Separable Conv（MobileNet）更讓 kernel 縮小到 3×3/5×5。FFT 只在特定場景（音訊卷積、大感受野語義分割）仍有優勢。

**FFT 的 breakeven point（理論分析）：**

```
Direct conv FLOPs  = 2 × H_out × W_out × C_in × kH × kW  (per output channel)
FFT conv FLOPs     ≈ 2 × 5 × H' × W' × log2(H'×W') + 2×H'×W'  (FFT + pointwise)
其中 H' = next_power_of_2(H + kH - 1)

breakeven: kH × kW ≈ 5 × log2(H'×W') / (H_out×W_out / H'×W')

對 H=W=56（VGG conv3）：H'=W'=64，log2(64×64)=12，breakeven kH×kW ≈ 60 → kH≈8
```

3×3 的 9 遠小於 60，所以 FFT 在 56×56 特徵圖上對 3×3 kernel **完全沒有優勢**。只有 kernel 大到 8×8 以上才開始划算。

---

## 39.4 四條路線取捨對比表

| 維度 | Direct Conv | im2col + GEMM | Implicit GEMM | Winograd F(2×2,3×3) | FFT Conv |
|------|-------------|---------------|---------------|----------------------|----------|
| **kernel size** | 任意 | 任意 | 任意 | 3×3 最佳 | 大 kernel（7×7+） |
| **stride** | 任意 | 任意 | 任意 | stride=1 最佳 | stride=1 | 
| **dtype** | FP32/FP16 | FP32/FP16 | FP32/FP16/INT8 | FP32 較安全；FP16 需驗精度 | FP32（複數精度需求） |
| **額外記憶體** | 低 | 高（kH×kW×C_in 倍） | 低（workspace 小） | 中（transform buffer） | 高（複數頻域 buffer） |
| **實作難度** | 低 | 低（cuBLAS） | 高（cuDNN 負責） | 高（transform 矩陣推導） | 中（cuFFT） |
| **Tensor Core 相容** | 否 | 是 | 是 | 部分（FP32 elementwise 不用 TC） | 否 |
| **適用場景** | 教學/prototype | 訓練框架早期 | cuDNN 主力（通用） | 推論、3×3/stride=1/FP32 | 音訊/大感受野 |

**cuDNN 的預設策略（CUDA 12.x）：**
- 3×3, stride=1, FP32, 小 batch：Winograd
- 3×3, stride=1, FP16：Implicit GEMM（避免 Winograd FP16 精度問題）或 Winograd（若精度允許）
- 大 kernel、大 C\_in：Implicit GEMM
- 使用者可強制用 `cudnnFindConvolutionForwardAlgorithm` 自行 benchmark

---

## 39.5 cuDNN 為什麼用 Heuristic 選演算法

cuDNN 面對的參數空間非常大：

```
N × C_in × H × W × C_out × kH × kW × stride × dilation × dtype × 記憶體對齊
```

這個空間有數十億個組合。沒有一個演算法在所有組合下都最優——這是 cuDNN 必須用 heuristic 的根本原因。

**cuDNN 的選擇機制（從 cuDNN 5 開始）：**

1. **靜態 heuristic**：cuDNN 內建一張規則表，根據（kernel size, dtype, batch size, channel 數量等）直接查表選演算法。這是 `cudnnGetConvolutionForwardAlgorithm` 做的事。

2. **Workspace-based auto-tuning**：`cudnnFindConvolutionForwardAlgorithm` 會在真實 GPU 上跑所有候選演算法，各測幾次取最快的。PyTorch 的 `torch.backends.cudnn.benchmark = True` 就是開啟這個模式。

3. **代價：** auto-tuning 本身需要時間（幾百 ms 到幾秒），且結果 GPU 型號相依。換一張卡，之前的 benchmark 結果可能完全不同。

**為什麼 benchmark mode 值得開？**

```
# 情境：固定 input shape 反覆訓練（大多數 CNN 訓練都是這樣）
torch.backends.cudnn.benchmark = True

第一個 batch：cuDNN 測所有演算法，選最快的，快取結果
第二個 batch 起：直接用快取的最佳演算法

# 結果：訓練速度通常提升 10-30%（視 network 和 GPU 而定）
```

**什麼時候不要開 benchmark mode？**

- 輸入 shape 每個 batch 都不同（NLP 的變長 sequence、動態解析度的影像）
- 每次都重新 benchmark，反而比靜態 heuristic 慢
- 需要 deterministic 結果（`torch.use_deterministic_algorithms(True)` 時）

---

## 39.6 動手：cuDNN Benchmark Mode 實測

以下在 Colab T4 上驗證 benchmark mode 的效果。

```python
import torch
import torch.nn as nn
import time

device = torch.device("cuda")

# 模擬 VGG-16 style 卷積：256 channels in/out, 3×3, 56×56 feature map
conv = nn.Conv2d(256, 256, kernel_size=3, padding=1, bias=False).to(device)
x = torch.randn(16, 256, 56, 56, device=device)  # batch=16

def benchmark_conv(use_benchmark: bool, n_warmup=5, n_iter=20):
    torch.backends.cudnn.benchmark = use_benchmark
    # warmup
    for _ in range(n_warmup):
        _ = conv(x)
    torch.cuda.synchronize()

    t0 = time.perf_counter()
    for _ in range(n_iter):
        y = conv(x)
    torch.cuda.synchronize()
    t1 = time.perf_counter()
    return (t1 - t0) / n_iter * 1000  # ms per iteration

# 第一次呼叫 benchmark=True 時 cuDNN 會跑 auto-tuning（需要額外時間）
ms_no_bench  = benchmark_conv(False)
ms_with_bench = benchmark_conv(True)

print(f"Without benchmark: {ms_no_bench:.2f} ms/iter")
print(f"With benchmark:    {ms_with_bench:.2f} ms/iter")
print(f"Speedup:           {ms_no_bench / ms_with_bench:.2f}x")
# （Colab 預期，未在本機實測）
```

預期輸出（Colab T4 參考值，未在本機實測）：

```
Without benchmark: 8.3 ms/iter
With benchmark:    6.1 ms/iter
Speedup:           1.36x
```

---

**查詢 cuDNN 選了哪個演算法：**

```python
import ctypes

# 直接用 cuDNN Python binding（需要 cudnn-python 或 pycudnn）
# 比較簡單的方式：用 PyTorch 的 benchmark 結果反查

torch.backends.cudnn.benchmark = True
torch.backends.cudnn.verbose = True  # PyTorch 不暴露 algorithm ID，但可用 NVTX profiling

# 或用 Nsight Systems profiling：
# nsys profile python train.py
# 在 timeline 找 cudnn::engines::* 的 kernel 名稱就能看出是哪條路線
```

---

**直接比較 Winograd vs GEMM 路線（強制指定）：**

```python
# PyTorch 不直接暴露 cudnn algorithm 選擇 API
# 但可以透過 F.conv2d 的底層 cudnn call 間接測試
# 更直接的方式：用 cuDNN Python wrapper

import ctypes
# 以下使用 PyTorch 的 _C._cudnn API（非公開 API，僅供研究）
# 生產環境請用 cudnnFindConvolutionForwardAlgorithm

# 實務上：torch.backends.cudnn.allow_tf32 = False 可以測精度差異
torch.backends.cudnn.allow_tf32 = False  # FP32 精度模式

# 測試 Winograd 是否影響精度
conv_fp32 = nn.Conv2d(64, 64, 3, padding=1).cuda().float()
conv_fp16 = conv_fp32.half()

x_fp32 = torch.randn(4, 64, 28, 28, device='cuda')
x_fp16 = x_fp32.half()

y_fp32 = conv_fp32(x_fp32)
y_fp16 = conv_fp16(x_fp16).float()

max_diff = (y_fp32 - y_fp16).abs().max()
print(f"FP32 vs FP16 max diff: {max_diff:.6f}")
# （Colab 預期，未在本機實測）
# Winograd 路線下 FP16 的 max_diff 通常比 FP32 大 5-10x
```

---

## 39.7 踩雷

### 雷 1：NCHW vs NHWC 選錯 layout 損失 20-30% 效能

cuDNN 從 7.x 開始對 NHWC + Tensor Core 有專門優化。用 NCHW 在 T4/A100 上跑 FP16 可能比 NHWC 慢 20-30%。

```python
# 糟糕：PyTorch 預設 NCHW
x = torch.randn(16, 3, 224, 224, device='cuda').half()

# 好：明確轉 NHWC (channels_last)
x = x.to(memory_format=torch.channels_last)
model = model.to(memory_format=torch.channels_last)
```

**根本原因：** Tensor Core 的 WMMA 指令需要 K 維度（reduction 維度）連續。NHWC 下 C\_in 連續，對 implicit GEMM 的 inner loop 更友好。

---

### 雷 2：benchmark mode 在 shape 變化時越跑越慢

```python
# 踩雷場景：每個 batch 的 feature map 大小不同
torch.backends.cudnn.benchmark = True
for h, w in [(224, 224), (256, 256), (192, 192), ...]:  # 每次不同 shape
    x = torch.randn(16, 64, h, w, device='cuda')
    y = conv(x)
    # cuDNN 每次都 benchmark 新 shape → 訓練速度反而比 benchmark=False 慢 5x
```

**解法：** shape 不固定時關掉 benchmark mode，或把 shape normalize 到固定幾種。

---

### 雷 3：Winograd 的 FP16 精度炸掉 BatchNorm 收斂

症狀：訓練 loss 在幾個 epoch 後突然 NaN 或不收斂，FP32 訓練正常。

**原因：** Winograd transform 的累積 rounding error 使卷積輸出有小偏差，BatchNorm 計算 variance 時把這些偏差放大（因為 BN 除以 std，接近 0 的 std 時浮動誤差會炸）。

**解法：**

```python
# 方法一：關掉 Winograd（cuDNN 不直接暴露，但關掉 allow_tf32 有時有效）
# 方法二：改用 AMP 的 FP32 accumulation
from torch.cuda.amp import autocast
with autocast(dtype=torch.float16, enabled=True):
    y = model(x)  # 計算在 FP16，accumulation 在 FP32

# 方法三：強制 FP32 精度模式
torch.backends.cudnn.allow_tf32 = True  # TF32 ≠ FP16，較安全
```

---

### 雷 4：im2col workspace 記憶體沒算進去導致 OOM

im2col 需要 `(H_out × W_out × batch) × (kH × kW × C_in) × sizeof(dtype)` 的 workspace。

以 batch=32, C\_in=512, kH=kW=3, H\_out=W\_out=28 為例：

```
workspace = 32 × (28×28) × (9×512) × 4 bytes
           = 32 × 784 × 4608 × 4
           ≈ 463 MB
```

單 layer 就快 500 MB。cuDNN 預設用 workspace，但 PyTorch 的 `max_split_size_mb` 需要相應調整。

**解法：** 用 `torch.cuda.memory_allocated()` 和 `torch.cuda.memory_reserved()` 監控，或切換到 implicit GEMM（workspace 小得多）。

---

### 雷 5：dilated convolution 遇到 Winograd 退化成 direct

Atrous/Dilated Conv（語義分割常用，dilation > 1）讓 Winograd 的 tile 不再連續，transform 無效。cuDNN 會自動退回 direct conv，但**使用者不會收到警告**。

```python
# 這個 conv 看起來正常，但 dilation=2 讓 cuDNN 退回 direct conv
conv = nn.Conv2d(256, 256, 3, padding=2, dilation=2)
# 效能比你預期的低，原因是 Winograd 失效
```

**偵測方式：** 用 Nsight Systems profiling，看 kernel 名稱是否包含 `winograd`。

---

## 39.8 動手練習

1. **im2col 手工實作**：用 NumPy 寫一個 `im2col` 函數（輸入是 `[C_in, H, W]` 的 array，輸出是 `[H_out×W_out, kH×kW×C_in]` 的矩陣），用 `np.matmul` 做完卷積後與 `scipy.signal.correlate2d` 對比輸出，確認完全一致。

2. **Winograd F(2,3) 1D 驗證**：用 Python 手工推導 1D Winograd F(2,3)（4 次乘法），對一個長度 4 的輸入和長度 3 的 filter，驗算 transform→elementwise multiply→output transform 與直接卷積結果一致（FP32 精度下誤差 < 1e-6）。

3. **cuDNN benchmark 效果測量**：在 Colab T4 上寫一個迴圈，對 10 種不同的 `(C_in, C_out, kH, kW, H, W)` 組合，分別測量 benchmark=False 和 benchmark=True 的 throughput，畫出加速比 bar chart，觀察哪些 shape 最受益。

4. **NHWC vs NCHW 效能對比**：寫一個 ResNet-style block（3×3 conv + BN + ReLU × 2），分別在 NCHW 和 channels\_last（NHWC）格式下，用 FP16 跑 100 次前向，對比 throughput（GFLOPS）和記憶體用量。

---

## 39.9 本章重點

- 卷積是 DL 的核心計算，算術量正比於 `N × C_out × H_out × W_out × C_in × kH × kW`。
- 直接五重迴圈對 GPU 不友好；需要轉換成矩陣運算才能用 Tensor Core。
- **im2col + GEMM**：最簡單，但記憶體展開係數 = kH×kW，大 channel 數時 OOM。
- **Implicit GEMM**：cuDNN 現代主力，不實際展開 col\_matrix，動態算索引，省記憶體。
- **Winograd F(2×2,3×3)**：理論節省乘法 2.25x（文獻值），3×3/stride=1/FP32 的最佳選擇；FP16 精度需特別注意。
- **FFT Conv**：大 kernel（7×7+）和 stride=1 的場景；現代 CNN 多 3×3 故使用率低。
- cuDNN 用 heuristic + workspace-based auto-tuning 選演算法；`torch.backends.cudnn.benchmark = True` 在固定 shape 場景通常提升 10-30%。
- NHWC layout 對 Tensor Core implicit GEMM 更友好，FP16 訓練建議轉 channels\_last。

---

## 39.10 自我檢核

1. im2col 把 `[N, C_in, H, W]` 的輸入展開後，col\_matrix 的 shape 是什麼？記憶體放大幾倍？（以 3×3 kernel 為例）

2. Implicit GEMM 比 im2col 省記憶體的代價是什麼？為什麼在現代 GPU 這個代價可以接受？

3. Winograd F(2×2, 3×3) 的 16 次乘法對應哪個步驟？Transform 矩陣（B^T, G, A^T）只用加法和常數縮放，為什麼這比直接乘法「便宜」？

4. 為什麼 cuDNN 不能用一個演算法打遍所有場景？列出至少 3 個影響演算法選擇的維度。

5. `torch.backends.cudnn.benchmark = True` 在什麼情況下反而讓訓練變慢？如何偵測這個問題？

---

## 39.11 延伸閱讀

- **Chetlur et al., "cuDNN: Efficient Primitives for Deep Learning," arXiv 2014**（cudnn/implicit GEMM 原始論文，解釋 workspace-based 設計決策）
- **Lavin & Gray, "Fast Algorithms for Convolutional Neural Networks," CVPR 2016**（Winograd 應用於 CNN 的關鍵論文，F(2×2,3×3) transform 矩陣的推導來源）
- **Anderson et al., "Optimal DNN Primitive Selection with Partitioned Boolean Quadratic Programming," arXiv 2019**（cuDNN heuristic 的系統性優化框架）
- **NVIDIA cuDNN Developer Guide**（官方文檔，cudnnFindConvolutionForwardAlgorithm API 詳細說明：https://docs.nvidia.com/deeplearning/cudnn/developer-guide/）
- **PyTorch `torch.backends.cudnn` 文檔**（benchmark/deterministic/allow\_tf32 的相互影響：https://pytorch.org/docs/stable/backends.html）
- **Shi et al., "A Survey of CPU-GPU Heterogeneous Computing Practices"**（包含 im2col vs implicit GEMM 的 memory footprint 詳細對比）

---

→ [Ch 40 — Softmax 與 LayerNorm 的數值穩定實作](./40-softmax-layernorm.md)
