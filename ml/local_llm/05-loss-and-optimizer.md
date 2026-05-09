# Ch 5 — 損失函數與優化器：Adam 在做什麼

> 目標：理解 cross-entropy loss 為什麼是語言模型的標配，以及 Adam 怎麼比普通梯度下降聰明。

## 損失函數是「距離的定義」

訓練時我們需要一個數值告訴模型「現在的輸出有多爛」。這個數值就是 loss。

loss 的選擇**取決於任務**，不是隨便選的。用錯 loss，梯度方向會錯，網路學不到正確東西。

## MSE（均方誤差）：回歸任務

```python
import torch
import torch.nn as nn

criterion = nn.MSELoss()
pred  = torch.tensor([2.5, 0.0, 2.0])
true  = torch.tensor([3.0, -0.5, 2.0])
loss  = criterion(pred, true)
# ((2.5-3.0)² + (0.0-(-0.5))² + (2.0-2.0)²) / 3 = 0.1667
print(loss)
```

MSE 用在預測連續數值——房價、溫度、股價。語言模型**不用** MSE。

## Cross-Entropy Loss：分類與語言模型

語言模型在做的是 vocab 大小的多分類：「下一個 token 是 50257 個選項中的哪一個？」

Cross-entropy loss 量的是預測機率分布和真實分布之間的差距：

```
L = -log(p_correct)
```

`p_correct` 是模型給正確答案的機率。如果模型很確定正確答案，p 接近 1，`-log(1) = 0`，loss 很低。如果模型亂猜，p 接近 1/50257，loss 很高。

```python
# 示範：vocab size = 5，batch size = 2
logits = torch.tensor([
    [2.0, 1.0, 0.1, -1.0, -2.0],   # 第一個 token，模型猜測
    [0.5, 0.5, 3.0,  0.5,  0.5],   # 第二個 token
])
targets = torch.tensor([0, 2])  # 正確答案分別是第 0 和第 2 個

criterion = nn.CrossEntropyLoss()
loss = criterion(logits, targets)
print(loss)

# CrossEntropyLoss 內建 softmax，logits 直接餵進去就好
# 不用先手動 softmax
```

## Perplexity：語言模型的評估指標

訓練時看 loss，比較模型時常用 perplexity（困惑度）：

```
perplexity = exp(cross_entropy_loss)
```

```python
import math
loss_value = 3.5  # 假設 loss 是 3.5
perplexity  = math.exp(loss_value)
print(f"perplexity: {perplexity:.1f}")  # ≈ 33.1
```

直覺：perplexity = 33 大約等於「模型每次預測時，心裡有 33 個同等可能的選項」。越低越好。GPT-2 在 WebText 上約 29，Llama 3 在同類測試集上可以低到 5–10。

## 隨機梯度下降（SGD）：最基本的優化器

```python
optimizer = torch.optim.SGD(model.parameters(), lr=0.01)

# 每次更新：w ← w - lr × gradient
# 簡單但有問題：
# 1. 所有參數用同一個 lr
# 2. 在窄谷裡會震盪
# 3. 需要仔細調 lr
```

## Adam：實務上幾乎都用這個

Adam（Adaptive Moment Estimation）對每個參數**自適應**地調整學習率，有兩個關鍵機制：

**動量（Momentum）**：記住過去梯度的方向，不讓每次更新完全被當前 batch 主導：
```
m_t = β₁ m_{t-1} + (1-β₁) g_t    # β₁=0.9，加權平均過去梯度
```

**自適應學習率（RMSProp）**：記住過去梯度的大小，對小梯度的參數放大學習率：
```
v_t = β₂ v_{t-1} + (1-β₂) g_t²   # β₂=0.999
```

最終更新：
```
w_t = w_{t-1} - lr × m_t / (√v_t + ε)
```

```python
optimizer = torch.optim.Adam(
    model.parameters(),
    lr=3e-4,      # LLM 訓練的經典起點
    betas=(0.9, 0.999),  # 預設值，幾乎不用改
    eps=1e-8,
    weight_decay=0.01  # L2 正則化，防過擬合
)
```

## AdamW：更常用的變體

`AdamW` 是 Adam + 正確的 weight decay 實作。原版 Adam 的 weight decay 和自適應學習率混在一起，AdamW 把它們分開，效果更好。LLM 訓練標配 AdamW。

```python
optimizer = torch.optim.AdamW(
    model.parameters(),
    lr=3e-4,
    weight_decay=0.1  # Llama 系列用這個值
)
```

## 不同 loss 的比較

| 任務 | Loss | PyTorch 類別 |
|------|------|-------------|
| 迴歸 | MSE | `nn.MSELoss` |
| 二元分類 | Binary Cross-Entropy | `nn.BCEWithLogitsLoss` |
| 多類分類 / LLM | Cross-Entropy | `nn.CrossEntropyLoss` |
| 生成模型（進階） | NLL Loss | `nn.NLLLoss` |

## 動手練習

觀察不同優化器的訓練曲線差異：

```python
import torch
import torch.nn as nn
import matplotlib.pyplot as plt

def train_with_optimizer(opt_class, **kwargs):
    torch.manual_seed(42)
    model = nn.Sequential(nn.Linear(10, 32), nn.ReLU(), nn.Linear(32, 1))
    X = torch.randn(500, 10)
    y = torch.randn(500, 1)
    optimizer = opt_class(model.parameters(), **kwargs)
    criterion = nn.MSELoss()
    losses = []
    for _ in range(100):
        optimizer.zero_grad()
        loss = criterion(model(X), y)
        loss.backward()
        optimizer.step()
        losses.append(loss.item())
    return losses

sgd_losses  = train_with_optimizer(torch.optim.SGD,  lr=0.01)
adam_losses = train_with_optimizer(torch.optim.Adam, lr=0.001)

plt.plot(sgd_losses,  label="SGD")
plt.plot(adam_losses, label="Adam")
plt.legend(); plt.xlabel("Epoch"); plt.ylabel("Loss")
plt.savefig("optimizer_comparison.png")
```

## 自我檢核

- [ ] 能解釋為什麼語言模型用 cross-entropy 而非 MSE
- [ ] 知道 Adam 的兩個核心機制（動量 + 自適應 lr）
- [ ] 知道 AdamW 和 Adam 的差異
- [ ] 跑過優化器比較實驗，看到 Adam 收斂更快

→ [Ch 6 過擬合防治：dropout / layernorm / 早停](./06-regularization.md)
