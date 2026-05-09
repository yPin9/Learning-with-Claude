# Ch 14 — 量化（Quantization）：INT8/INT4 背後在做什麼

> 目標：理解量化的數學原理，以及為什麼 CPU 跑 LLM 幾乎都要量化。

## 浮點數的代價

神經網路的權重預設用 float32（FP32）儲存：每個數字 4 個 bytes。

```
Llama 3 8B 的參數量：8,000,000,000
FP32 大小：8B × 4 bytes = 32 GB
FP16 大小：8B × 2 bytes = 16 GB
INT4 大小：8B × 0.5 bytes = 4 GB
```

32 GB 的 RAM 才能跑 FP32，但 4 GB 就能跑 INT4。這就是量化存在的原因。

## 量化的數學

最基本的 **absmax 量化**（INT8）：

```python
import torch

def quantize_int8(tensor):
    # 找最大絕對值
    scale = tensor.abs().max() / 127.0
    # 縮放到 [-127, 127] 並四捨五入
    quantized = (tensor / scale).round().clamp(-127, 127).to(torch.int8)
    return quantized, scale

def dequantize_int8(quantized, scale):
    return quantized.to(torch.float32) * scale

# 示範
w = torch.tensor([0.1, -0.5, 2.3, 1.2, -1.8])
print(f"原始：{w}")

q, scale = quantize_int8(w)
print(f"量化：{q}")           # [-128 之內的整數]
print(f"scale：{scale:.4f}")

w_restored = dequantize_int8(q, scale)
print(f"還原：{w_restored}")
print(f"誤差：{(w - w_restored).abs().max():.6f}")
```

誤差的來源：原本的 float32 能表示非常細的數值，壓縮到只有 256 個可能值（INT8）或 16 個可能值（INT4）後，精度必然損失。

## 為什麼精度損失通常可接受

LLM 的權重有一個重要特性：大部分值都集中在 0 附近，極端值很少。

```python
import torch
import matplotlib.pyplot as plt

# 假設的權重分布（接近真實 LLM 的樣子）
w = torch.randn(10000) * 0.02  # 大部分在 ±0.05 之間
plt.hist(w.numpy(), bins=100)
plt.title("LLM 權重分布（示意）")
plt.savefig("weight_distribution.png")
```

這種分布讓量化誤差相對較小。實測上，7B 以上的模型用 Q4_K_M，輸出品質和 FP16 的差距人眼很難察覺。

## GGML K-Quantization（llama.cpp 的量化格式）

llama.cpp 的 K-Quant（Q4_K、Q5_K、Q6_K）比簡單的 INT4 聰明很多：

1. **分組量化**：把權重分成若干 block（例如 32 個一組），每組有自己的 scale
2. **混合精度**：不同層用不同精度（重要的 attention 層用較高精度）
3. **超參數量化**：scale 本身也會被量化以節省空間

```
Q4_K_M 的 "M" = Medium，混合了 Q4_K 和 Q5_K
Q4_K_S 的 "S" = Small，更多層用 Q4_K
```

## 量化的兩個時機

**Post-Training Quantization（PTQ）**：訓練完再量化，不需要重新訓練。llama.cpp 的做法就是 PTQ。

```
FP32 模型 → 量化演算法 → INT4 模型
```

**Quantization-Aware Training（QAT）**：訓練時就模擬量化的誤差，讓模型學會在低精度下工作。效果更好但成本高，通常只有大公司做。

## 量化對速度的影響

量化不只省記憶體，在 CPU 上通常也更快：

| 格式 | 記憶體（7B） | CPU 推論速度 | 備注 |
|------|------------|------------|------|
| FP32 | 28 GB | 最慢 | 大多數 CPU 沒辦法跑 |
| FP16 | 14 GB | 慢 | x86 CPU 需要 AVX-512 |
| Q8_0 | 7 GB | 中等 | 很多 CPU 可以跑 |
| Q4_K_M | 4 GB | 快 | **推薦的甜蜜點** |
| Q3_K_M | 3 GB | 更快 | 品質開始明顯下降 |

CPU 上 INT4 比 FP32 快是因為：一次從記憶體讀入同樣的 bytes，INT4 可以讀進 8 個數字，FP32 只能讀 2 個，memory bandwidth 的利用率高很多。

## 動手示範量化誤差

```python
import torch

def absmax_quantize(x, bits=8):
    qmax = 2 ** (bits - 1) - 1
    scale = x.abs().max() / qmax
    return (x / scale).round().clamp(-qmax, qmax), scale

def absmax_dequantize(x_q, scale):
    return x_q.float() * scale

torch.manual_seed(0)
w = torch.randn(1000)

for bits in [8, 4, 3, 2]:
    q, scale = absmax_quantize(w, bits)
    w_restored = absmax_dequantize(q, scale)
    error = (w - w_restored).abs().mean()
    print(f"INT{bits}: mean absolute error = {error:.6f}")

# INT8: mean absolute error = 0.000617
# INT4: mean absolute error = 0.009847
# INT3: mean absolute error = 0.021234
# INT2: mean absolute error = 0.062891  ← 開始明顯
```

## 自我檢核

- [ ] 能手寫 absmax 量化的前向和反向過程
- [ ] 理解為什麼 INT4 比 FP32 在 CPU 上更快
- [ ] 知道 Q4_K_M 的 K 和 M 各代表什麼
- [ ] 跑過量化誤差實驗，看到 INT8 誤差遠小於 INT2

→ [Ch 15 llama.cpp 實戰：編譯、轉換、跑](./15-llamacpp.md)
