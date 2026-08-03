# Ch 42 — 低精度運算：FP16/BF16/INT8/FP8 的工程取捨

> **目標**：徹底搞清楚每種低精度格式的位元結構、適用場景與陷阱；能從零推導量化公式；能在 CUDA 程式碼中正確使用 `__half` 與 INT8 GEMM，並知道 loss scaling 為什麼不可省。
>
> **環境**：CUDA 12.x，Ampere（A100）以上為 FP8/TF32 示範；FP16/BF16/INT8 在 Turing（T4）以上即可。

---

## 為什麼低精度快

兩個獨立的原因，必須分清楚。

**原因一：記憶體頻寬減半。**
FP32 每個元素 4 bytes，FP16/BF16 2 bytes，INT8 1 byte。A100 的 HBM2e 頻寬是 2 TB/s。矩陣乘法在記憶體受限的情況下（小 batch、大 activation），把資料量砍半就直接砍掉一半的等待時間。對推理場景尤其明顯——batch size = 1 的 token generation 是純頻寬瓶頸。

**原因二：Tensor Core 吞吐翻倍（或更多）。**
A100 的 Tensor Core 數字：
- FP32（標準）：19.5 TFLOPS
- TF32：156 TFLOPS（8×）
- FP16/BF16：312 TFLOPS（16×）
- INT8：624 TOPS（32×）
- FP8：1248 TOPS（64×）

Tensor Core 的設計就是專門吃低精度的 4×4 或 16×16 小矩陣磚（tile），硬體上就比 CUDA Core 快。這在 Ch 30 已經細談過；本章重點是「格式選錯會出什麼事」。

兩個原因加在一起，FP16 訓練在 A100 上比 FP32 快 2-4× 是常態，不是廣告數字。

---

## 浮點格式深挖

先看位元佈局，把所有格式攤開對比：

```
格式      符號  指數  尾數   最大有限值        典型用途
──────────────────────────────────────────────────────
FP32       1    8    23    ~3.4×10^38       完整精度訓練
TF32       1    8    10    ~3.4×10^38       A100+ Tensor Core 內部
FP16       1    5    10      65504          混精度訓練
BF16       1    8     7    ~3.4×10^38       大模型訓練/推理
INT8       1    -     7         127         推理量化
FP8 E4M3   1    4     3         448         FP8 前向 pass
FP8 E5M2   1    5     2       57344         FP8 梯度
```

### FP16 的致命弱點：動態範圍

FP16 只有 5 個指數位，最大值 65504，最小正規數 ~6×10^-5。這個範圍聽起來夠用，但深度學習的梯度分佈往往尾巴很長——特別是初始化後幾個 step，梯度值可以小到 1e-7 甚至更小，直接 underflow 成零。Underflow 在 FP16 裡是靜默的，不會拋例外，你的模型就這樣悄悄停止學習。

```
FP16 可表示範圍：
指數位 5 bit → bias = 15
最小正規數：2^(-14) ≈ 6.1×10^-5
最大值：(2 - 2^-10) × 2^15 = 65504

  ←── underflow 區 ──→|←── 正常表示 ──→|← overflow →
 0         6×10^-5                    65504
```

### BF16：為什麼比 FP16 更適合訓練

BF16（Brain Float 16）的設計哲學很明確：把 FP32 的指數位完整保留（8 bit），只砍尾數（7 bit vs 23 bit）。

```
FP32：[S 1bit][E 8bit][M 23bit]  → 最大 ~3.4×10^38
BF16：[S 1bit][E 8bit][M  7bit]  → 最大 ~3.4×10^38  ← 同 FP32！
FP16：[S 1bit][E 5bit][M 10bit]  → 最大       65504  ← 縮水 10^33 倍
```

BF16 犧牲的是精度（7 bit 尾數 → 約 1% 相對誤差），但保住了 range。訓練時 overflow 幾乎不會發生，gradients 也不容易 underflow。實務上 LLaMA、GPT-4 這類大模型的訓練幾乎都用 BF16，不用 FP16。

### TF32：A100 的隱形格式

TF32 不是你手動選的格式——它是 A100 Tensor Core 在執行 FP32 matmul 時的內部計算格式。硬體把 FP32 輸入的尾數截短到 10 bit（等於 FP16 的尾數），用 FP32 的指數位（8 bit），算完再輸出 FP32。你的程式碼不變，但 matmul 快 8 倍。

代價：相對誤差從 FP32 的 ~1.2×10^-7 變成 ~1.2×10^-4。對大多數 DNN 訓練可以接受；數值敏感的科學計算要小心。

```cpp
// 關閉 TF32（預設是開的）
cudaDevAttrTfloat32MatMulAllowed
// 或透過 cuBLAS：
cublasSetMathMode(handle, CUBLAS_MATH_DISALLOW_REDUCED_PRECISION_REDUCTION);
```

---

## 混精度訓練（Mixed Precision Training）

### 三個核心組件

**1. FP16 計算（forward + backward）**
所有 matmul、conv 用 FP16，利用 Tensor Core 的速度。

**2. FP32 master weights**
把模型參數存一份 FP32 拷貝。每次 optimizer step 用 FP32 更新，再 cast 回 FP16 給下一個 forward pass。這是因為 weight update 量（learning_rate × gradient）往往很小，在 FP16 精度下會直接被捨去。

```
┌─────────────────────────────────────────────────────┐
│  FP16 forward  →  FP16 loss  →  loss scaling        │
│                                    ↓                 │
│  FP16 backward (scaled gradients)                    │
│                                    ↓                 │
│  unscale + clip  →  FP32 master weights update       │
│                                    ↓                 │
│  cast FP32 weights → FP16  →  next forward          │
└─────────────────────────────────────────────────────┘
```

**3. FP32 accumulation**
BN（Batch Normalization）的統計量（running_mean、running_var）、softmax 的 exp sum、reduction 操作——這些要保持 FP32，不然數值誤差會累積。

### Loss Scaling：為什麼非做不可

FP16 的梯度下限是 ~6×10^-5。反向傳播到深層，梯度值可以輕易小於這個數，直接變成零（gradients vanish 的 hardware 版本）。

解法：在算 backward 之前，把 loss 乘上一個大係數 $S$（通常 $S = 2^{10}$ 到 $2^{15}$），讓梯度整體放大 $S$ 倍，進入 FP16 的可表示範圍。Update 之前再除回來。

```
原始梯度 g ≈ 1×10^-6  → FP16 underflow → 0  ← 訓練死掉

scaled loss = loss × S, S = 2^12 = 4096
scaled 梯度 g' = g × S ≈ 4×10^-3  → FP16 OK
optimizer update: weight -= lr × (g' / S) = lr × g   ← 數學等價
```

### 動態 Loss Scaling

靜態 $S$ 不夠好——$S$ 太大會 overflow，太小會 underflow。

動態策略（PyTorch `GradScaler` 的邏輯）：
- 每 N 步（預設 2000）若沒有 inf/nan 出現：$S \leftarrow S \times 2$（growth）
- 若偵測到 inf/nan：$S \leftarrow S / 2$，**跳過這個 step**（backoff）

```python
from torch.cuda.amp import GradScaler, autocast

scaler = GradScaler()  # 預設 init_scale=65536

for batch in dataloader:
    optimizer.zero_grad()
    with autocast():          # 自動選 FP16/BF16
        loss = model(batch)
    scaler.scale(loss).backward()
    scaler.unscale_(optimizer)        # 梯度除回來
    torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
    scaler.step(optimizer)    # 若有 inf/nan 則 skip
    scaler.update()           # 調整 scale factor
```

### CUDA 層的 `__half` 用法

```cuda
#include <cuda_fp16.h>

__global__ void fp16_add(const __half* a, const __half* b, __half* c, int n) {
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx < n) {
        // __half 支援基本算術運算子（CUDA 9+）
        c[idx] = a[idx] + b[idx];
    }
}

// 使用 half2 向量化：一個指令處理兩個 FP16
__global__ void fp16_add_vectorized(const half2* a, const half2* b, half2* c, int n) {
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx < n / 2) {
        c[idx] = __hadd2(a[idx], b[idx]);  // 兩路並行
    }
}

// Host 端：__float2half / __half2float
float val_f32 = 3.14f;
__half val_f16 = __float2half(val_f32);
float back = __half2float(val_f16);
```

---

## FP8 訓練

FP8 是 Hopper（H100）帶進主流的格式，有兩種子類型，用在不同地方，原因是前向和反向對格式的需求根本不同。

### E4M3 用於前向 pass（activation、weights）

- 4 個指數位、3 個尾數位
- 最大有限值：±448
- 沒有 Inf（把那個特殊位元的空間用來擴展可表示範圍）
- 精度較高：2^-3 = 12.5% 相對誤差上界

前向 pass 需要的是精度——activation 的小差異直接影響 loss，weights 的小差異直接影響模型品質。E4M3 犧牲範圍（max 448，比 FP16 的 65504 小很多）換精度，因為 weights 和 activations 的分佈通常比較窄，用 scale 因子調整就夠。

### E5M2 用於反向 pass（梯度）

- 5 個指數位、2 個尾數位
- 最大有限值：±57344（比 E4M3 寬 128 倍）
- 有 Inf、有 NaN
- 精度較低：2^-2 = 25% 相對誤差上界

梯度的分佈比 activation 寬很多，outlier 值（很大的梯度）在不穩定訓練初期常見。E5M2 的寬範圍讓梯度不容易 overflow，代價是精度降到 25%——但梯度本來就有噪音，這個精度損失 SGD 還吃得下去。

```
E4M3 精度 vs E5M2 範圍的取捨：

          精度 ←────────────────────→ 範圍
 E4M3 ████████░░░░░░░░░░░░░░░░░  (3M bits → 12.5% err, max 448)
 E5M2 ████░░░░░░░░░░░░░░░░░░░░░  (2M bits → 25% err,  max 57344)
```

### FP8 GEMM with Scaling（Transformer Engine 風格）

FP8 實作必須搭配 per-tensor 或 per-channel scale，否則 max 448 根本放不下一般的 activation 值：

```cuda
// Transformer Engine 的概念（簡化版）
// 實際應使用 te::fp8_gemm 或 cublasFp8Gemm

// 量化：FP32 → E4M3
__device__ __nv_fp8_e4m3 quantize_e4m3(float x, float scale_inv) {
    return __nv_cvt_float_to_fp8(x * scale_inv, __NV_SATFINITE, __NV_E4M3);
}

// scale 計算：amax(activation) / max_e4m3_val
float amax = compute_amax(activation);          // reduce max |x|
float scale = 448.0f / (amax + 1e-12f);        // 避免除零
float scale_inv = 1.0f / scale;                 // 存起來給 dequant 用

// GEMM 完成後 dequant：
// output_fp32 = output_fp8 × scale_A × scale_B
```

---

## 量化（Quantization）

量化是把浮點數壓縮到整數（通常 INT8）的過程，主要用於推理加速。訓練後量化（PTQ, Post-Training Quantization）最常見；量化感知訓練（QAT, Quantization-Aware Training）更精確但成本更高。

### 對稱量化（Symmetric Quantization）

假設量化到 INT8（範圍 [-127, 127]，捨棄 -128 讓正負對稱）：

```
scale = max(|x|) / 127
x_q  = round(x / scale)        ← clip 到 [-127, 127]
x_dq = x_q × scale             ← dequant（近似還原）
```

誤差：`|x - x_dq| ≤ scale / 2`，即最大一個 LSB 的一半。

對稱量化的 zero_point = 0，INT8 GEMM 實作最簡單，推薦用於 weights（通常分佈關於 0 對稱）。

### 非對稱量化（Asymmetric Quantization）

當數值分佈不對稱（例如 ReLU 後的 activation，全是正數），對稱量化浪費了負數那半的表示空間：

```
scale      = (max(x) - min(x)) / 255          ← 量化到 [0, 255]（UINT8）
zero_point = round(-min(x) / scale)
x_q        = round(x / scale) + zero_point    ← clip 到 [0, 255]
x_dq       = (x_q - zero_point) × scale       ← dequant
```

非對稱量化的 zero_point 不為 0，GEMM 時需要額外的 zero_point 補償項，計算稍複雜。

### Per-Tensor vs Per-Channel

**Per-tensor**：整個 tensor 共用一個 scale 和 zero_point。
- 優點：overhead 最小，GEMM 實作最快
- 缺點：若 tensor 內不同 channel 的數值範圍差很大，量化誤差大

**Per-channel（又稱 per-output-channel）**：每個 output channel 各自算一套 scale/zero_point。
- 優點：對 weights 而言，不同 filter 的數值範圍可以差 10×，per-channel 讓每個 filter 都用滿量化範圍
- 缺點：activation 不能做 per-channel（會讓 GEMM 矩陣乘法的並行性爆炸）

實務規則：**weights 用 per-channel，activations 用 per-tensor。**

```python
# PyTorch 量化示範：觀察 scale/zero_point
import torch
x = torch.randn(4, 4)

# 對稱 per-tensor
scale_sym = x.abs().max().item() / 127
x_q_sym = torch.quantize_per_tensor(x, scale=scale_sym, zero_point=0,
                                     dtype=torch.qint8)
print(x_q_sym.int_repr())   # INT8 值

# 非對稱 per-tensor
xmin, xmax = x.min().item(), x.max().item()
scale_asym = (xmax - xmin) / 255
zp = round(-xmin / scale_asym)
x_q_asym = torch.quantize_per_tensor(x, scale=scale_asym, zero_point=zp,
                                      dtype=torch.quint8)
```

### PTQ vs QAT

**PTQ（Post-Training Quantization）**：
1. 訓練完後，用一小份 calibration data（幾百個 sample）統計 activation 的 min/max（或百分位數）
2. 計算 scale/zero_point，直接固化到模型裡
3. 優點：快，不需要重訓練。缺點：精度損失大，對 outlier 敏感

**QAT（Quantization-Aware Training）**：
1. 訓練過程中插入「fake quantize」節點：forward 時模擬量化誤差，backward 時用 Straight-Through Estimator（STE）近似
2. 讓模型學著在量化誤差下仍然準確
3. 優點：精度最好（接近浮點），缺點：訓練時間多 20-30%

---

## INT8 GEMM：為什麼 Accumulate 要在 INT32

INT8 矩陣乘法的核心問題：兩個 INT8 相乘得到 INT16，K 個 INT16 累加（K 可以是 4096）得到 INT32。如果 accumulate 也用 INT8，會直接 overflow。

```
A[M×K] INT8,  B[K×N] INT8
C[M×N] = A @ B     ← accumulate 必須是 INT32！
C_fp32 = C.to(float32) × scale_A × scale_B   ← dequant 還原
```

CUDA 裡使用 `cublasGemmEx` 或 `cutlass`：

```cuda
#include <cublas_v2.h>

cublasHandle_t handle;
cublasCreate(&handle);

// INT8 input, INT32 accumulation, FP32 output
cublasGemmEx(
    handle,
    CUBLAS_OP_N, CUBLAS_OP_N,
    N, M, K,
    &alpha,                          // FP32 scale (= scale_A × scale_B)
    B_int8, CUDA_R_8I, N,
    A_int8, CUDA_R_8I, K,
    &beta,
    C_fp32, CUDA_R_32F, N,
    CUBLAS_COMPUTE_32I,              // INT32 accumulate
    CUBLAS_GEMM_DEFAULT
);
```

這裡的 `alpha = scale_A × scale_B`，`beta = 0`，cuBLAS 幫你做 dequant 融合進輸出。

自己寫 INT8 accumulate kernel：

```cuda
__global__ void int8_gemm_accumulate(
    const int8_t* A, const int8_t* B, int32_t* C,
    int M, int N, int K)
{
    int row = blockIdx.y * blockDim.y + threadIdx.y;
    int col = blockIdx.x * blockDim.x + threadIdx.x;
    if (row >= M || col >= N) return;

    int32_t acc = 0;
    for (int k = 0; k < K; ++k) {
        // int8 × int8 → int16，累加到 int32，不會 overflow
        acc += (int32_t)A[row * K + k] * (int32_t)B[k * N + col];
    }
    C[row * N + col] = acc;
}
```

---

## 格式取捨對比表

| 格式 | 速度提升 | 精度 | 動態範圍 | 適用場景 | 主要風險 |
|------|----------|------|----------|----------|----------|
| FP32 | 1× | 最高 | 最寬 | 數值敏感計算、BN 統計 | 慢、顯存大 |
| TF32 | 8× | 高 | 最寬 | A100+ matmul 透明加速 | 精度略降 |
| BF16 | 16× | 中 | 最寬（同 FP32）| 大模型訓練 | 精度比 FP16 低 |
| FP16 | 16× | 中高 | 窄（max 65504）| 混精度訓練（配 loss scaling）| Overflow/underflow |
| FP8 E4M3 | 64× | 較高 | 很窄（max 448）| H100+ 前向 pass | 需 per-tensor scale |
| FP8 E5M2 | 64× | 較低 | 中（max 57344）| H100+ 梯度 | 精度損失大 |
| INT8 | 32× | 低 | 整數（127）| 推理量化 | 需 calibration |

---

## 踩雷

**1. FP16 overflow 的靜默性**
FP16 overflow 不是 Python exception，是 `inf` 或 `nan` 靜默流入 loss，你的 loss 忽然變 nan 卻不知道為什麼。解法：加 `torch.autograd.set_detect_anomaly(True)` 定位，或用 GradScaler。`loss.item()` 是你的第一道防線，每 step 都要 check。

**2. Batch Normalization 統計必須 FP32**
BN 的 `running_mean`、`running_var` 是小量長期累加，FP16 精度會讓統計量慢慢漂移。PyTorch 的 `autocast` 預設會把 BN 的統計量保在 FP32——但如果你自己手寫 BN，記得手動 cast：
```python
# 錯誤：
running_mean = running_mean.half()  # 別這樣做

# 正確：running_mean 保持 FP32，只有 x 是 FP16
```

**3. 量化 Outlier 問題（LLM.int8() 背後的動機）**
大型語言模型的 activation 分佈有顯著 outlier——少數幾個 channel 的數值比其他 channel 大 100 倍以上。用 per-tensor INT8 量化，scale 被這些 outlier 拉大，其他 channel 的精度嚴重損失。解法：SmoothQuant（把 scale 從 activation 搬移到 weights）或 bitsandbytes 的 LLM.int8()（outlier 保在 FP16，其他 INT8）。

**4. 量化 vs 蒸餾的選擇誤區**
量化壓縮的是「數值精度」，蒸餾壓縮的是「模型結構」（student 學 teacher 的軟 label）。兩件事不互斥——可以先蒸餾出小模型，再對小模型做量化。但如果你的目標是「大模型快速推理」而不是「縮小模型」，蒸餾沒有直接幫助，量化才是主角。

**5. FP8 需要 H100，在 A100 上沒有**
FP8 是 Hopper 架構才有的 Tensor Core 指令，A100 沒有硬體 FP8 GEMM 支援。在 A100 上用 `torch.float8_e4m3fn` 會回退到軟體模擬，比 FP16 還慢。確認目標硬體再選格式，別因為論文用 FP8 就在 A100 cluster 上跑 FP8。

---

## 動手練習

**練習 1：觀察 GradScaler 的動態行為**
```python
import torch
import torch.nn as nn
from torch.cuda.amp import GradScaler, autocast

model = nn.Linear(1024, 1024).cuda()
optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
scaler = GradScaler(init_scale=2.0)  # 故意從很小的 scale 開始

for step in range(100):
    x = torch.randn(32, 1024, device='cuda')
    with autocast():
        y = model(x)
        loss = y.sum()
    scaler.scale(loss).backward()
    scaler.step(optimizer)
    scaler.update()
    if step % 10 == 0:
        print(f"step {step:3d}, scale = {scaler.get_scale():.1f}")
```
觀察 scale 如何指數增長，直到穩定或遇到 inf/nan 後縮回。

**練習 2：量化誤差 vs per-tensor/per-channel**
```python
import torch

# 模擬 weights 有不同 channel 的數值範圍
weights = torch.zeros(8, 16)
weights[0, :] = 100.0    # 第 0 個 filter：大值
weights[1:, :] = torch.randn(7, 16) * 0.1  # 其他 filter：小值

# Per-tensor 量化
amax = weights.abs().max().item()
scale_pt = amax / 127
w_q_pt = (weights / scale_pt).round().clamp(-127, 127)
w_dq_pt = w_q_pt * scale_pt

# Per-channel 量化
amax_pc = weights.abs().max(dim=1, keepdim=True).values
scale_pc = amax_pc / 127
w_q_pc = (weights / scale_pc).round().clamp(-127, 127)
w_dq_pc = w_q_pc * scale_pc

err_pt = (weights - w_dq_pt).abs().mean().item()
err_pc = (weights - w_dq_pc).abs().mean().item()
print(f"Per-tensor 量化誤差: {err_pt:.4f}")
print(f"Per-channel 量化誤差: {err_pc:.4f}")
# Per-channel 誤差遠小於 per-tensor
```

**練習 3：INT8 GEMM 正確性驗證**
用 `torch.int8` 手動做矩陣乘法，在 INT32 累加，最後 dequant，對比 FP32 結果的誤差。

---

## 本章重點

- 低精度快的兩個原因：頻寬減半、Tensor Core 吞吐翻倍，兩個效益可以疊加
- BF16 比 FP16 更適合訓練：同 FP32 指數位（8 bit），動態範圍不縮水
- FP16 最大只到 65504，梯度 underflow 是真實問題，不是理論顧慮
- Loss scaling 的本質：把梯度乘 S 放大到 FP16 可表示範圍，update 前除回來，數學等價
- 動態 loss scaling：每 N 步無 inf/nan 就 ×2，有就 ÷2 並跳過這個 step
- FP8 E4M3 給前向（精度優先），E5M2 給反向（範圍優先）
- 量化：對稱量化 `scale = max|x|/127`；非對稱量化有 zero_point；weights per-channel，activations per-tensor
- INT8 GEMM 必須在 INT32 累加，防 overflow；dequant = 乘回 scale_A × scale_B
- BN 統計量永遠保 FP32；FP8 需要 H100；LLM outlier 問題要用 SmoothQuant 或 LLM.int8() 處理

---

## 自我檢核

1. FP16 和 BF16 各有幾個指數位？最大有限值各是多少？
2. 為什麼 BF16 在訓練上比 FP16 更穩定？（從位元結構回答）
3. Loss scaling 解決的是什麼問題？scale factor S 乘在哪裡、何時除回來？
4. 動態 loss scaling 的 growth 和 backoff 觸發條件各是什麼？
5. FP8 E4M3 為什麼用於前向、E5M2 用於反向？從數值特性解釋
6. 對稱量化的 scale 公式？非對稱量化的 scale 和 zero_point 公式？
7. Per-channel 量化為什麼比 per-tensor 好？為什麼 activation 不能 per-channel？
8. INT8 GEMM 的 accumulate 為什麼要用 INT32？如果用 INT8 會發生什麼？

---

## 延伸閱讀

- **NVIDIA Mixed Precision Training 文件**（developer.nvidia.com/automatic-mixed-precision）：官方 AMP API 說明，含 GradScaler 參數詳解與各層 FP16 支援矩陣
- **FP8 Formats for Deep Learning**（arXiv:2209.05433）：OCP FP8 規格的原始論文，詳述 E4M3/E5M2 的設計取捨、NaN/Inf 編碼選擇，以及 FP8 訓練配合 per-tensor scaling 的實驗結果
- **Micikevicius et al., "Mixed Precision Training"**（ICLR 2018）：混精度訓練與 loss scaling 的奠基論文，FP16 master weights 架構從這裡來
- **bitsandbytes 程式庫**（github.com/bitsandbytes-foundation/bitsandbytes）：LLM.int8() 和 QLoRA 的實作，解決 LLM outlier 量化問題的工程方案，含 CUDA kernel 源碼

---

→ [Ch 43 PyTorch 底層：自訂 CUDA Kernel 與 Extension](./43-pytorch-custom-kernel.md)
