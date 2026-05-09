# Ch 6 — 過擬合防治：dropout / layernorm / 早停

> 目標：理解過擬合是怎麼發生的，掌握 Transformer 裡三個最重要的防治工具。

## 過擬合：把雜訊背起來了

過擬合（overfitting）的症狀：訓練 loss 持續下降，但驗證 loss 停滯甚至上升。

```
epoch 1:  train_loss=0.8,  val_loss=0.85   ← 正常
epoch 10: train_loss=0.3,  val_loss=0.35   ← 正常
epoch 30: train_loss=0.05, val_loss=0.60   ← 過擬合
epoch 50: train_loss=0.01, val_loss=0.95   ← 嚴重過擬合
```

模型把訓練資料的噪聲記起來了，但不能泛化到沒見過的資料。語言模型特別容易發生在資料量小的 fine-tuning 階段。

## 早停（Early Stopping）：最直接的防治

監控驗證 loss，一旦它不再改善就停止訓練：

```python
best_val_loss = float('inf')
patience = 5          # 連續 5 次沒改善就停
patience_counter = 0

for epoch in range(1000):
    train_one_epoch(model, train_loader)
    val_loss = evaluate(model, val_loader)

    if val_loss < best_val_loss:
        best_val_loss = val_loss
        patience_counter = 0
        torch.save(model.state_dict(), "best_model.pt")  # 存最佳版本
    else:
        patience_counter += 1
        if patience_counter >= patience:
            print(f"Early stopping at epoch {epoch}")
            break

# 載入最佳模型
model.load_state_dict(torch.load("best_model.pt"))
```

## Dropout：訓練時隨機關掉神經元

Dropout 在訓練時，每次前向傳播隨機把一些神經元的輸出設為 0：

```python
import torch.nn as nn

dropout = nn.Dropout(p=0.1)  # 10% 的神經元被關掉

x = torch.ones(4, 8)
print(dropout(x))
# tensor([[1.1111, 0.0000, 1.1111, 1.1111, 0.0000, 1.1111, 1.1111, 1.1111],
#         ...])
# 注意：沒被關掉的值被放大了 (×1/(1-p))，確保期望值不變
```

為什麼有用：強迫網路不依賴單一神經元，學習更 robust 的特徵。

**重要**：推論時 dropout 要關掉（`model.eval()` 自動處理）：

```python
model.train()   # dropout 開啟
y = model(x)    # 每次結果不同

model.eval()    # dropout 關閉
with torch.no_grad():
    y = model(x)  # 結果確定
```

Transformer 中 dropout 通常用在 attention weights 和 FFN 後面，p = 0.1 是預設。

## Layer Normalization：讓每層的輸入穩定

BatchNorm 是 CNN 的標配，但 Transformer 用 LayerNorm。差別在正規化的維度：

```
BatchNorm:  對一個 batch 內的同一個特徵做正規化（跨 batch）
LayerNorm:  對單一樣本內的所有特徵做正規化（不跨 batch）
```

為什麼 Transformer 用 LayerNorm：序列長度可變，batch size 可以是 1，BatchNorm 會失效。

```python
import torch
import torch.nn as nn

# 示範 LayerNorm
x = torch.tensor([[1.0, 2.0, 3.0, 4.0]])  # shape [1, 4]
ln = nn.LayerNorm(4)

out = ln(x)
print(out)
# 對 [1,2,3,4] 做正規化：
# mean = 2.5, std ≈ 1.12
# output ≈ [-1.34, -0.45, 0.45, 1.34]
print(out.mean())   # ≈ 0
print(out.std())    # ≈ 1
```

LayerNorm 讓每一層的輸入保持標準化，防止梯度爆炸或消失，是深層 Transformer 能訓練的關鍵。

## Pre-Norm vs Post-Norm

原始 Transformer 論文用 Post-Norm（LayerNorm 在 residual 之後），但現代 LLM（包括 GPT-2 之後）幾乎都改用 Pre-Norm（LayerNorm 在子層之前）：

```
Post-Norm（原版）: output = LayerNorm(x + sublayer(x))
Pre-Norm（現代）:  output = x + sublayer(LayerNorm(x))
```

Pre-Norm 訓練更穩定，不需要學習率 warmup 也能跑，深層模型尤其明顯。

## Weight Decay：懲罰大參數

在 AdamW 裡設定 `weight_decay`，等效於 L2 正則化：在 loss 上加一個懲罰項 `λ||W||²`，讓參數值不要太大：

```python
optimizer = torch.optim.AdamW(
    model.parameters(),
    lr=3e-4,
    weight_decay=0.1  # 訓練 LLM 時的常見值
)
```

weight_decay 通常只作用在權重矩陣，不作用在 bias 和 LayerNorm 的參數。實作時要把參數分組：

```python
decay_params     = [p for n, p in model.named_parameters() if 'bias' not in n]
no_decay_params  = [p for n, p in model.named_parameters() if 'bias' in n]

optimizer = torch.optim.AdamW([
    {'params': decay_params,    'weight_decay': 0.1},
    {'params': no_decay_params, 'weight_decay': 0.0},
], lr=3e-4)
```

## 防治工具的使用場景

| 場景 | 建議工具 |
|------|---------|
| 資料量小的 fine-tuning | 早停 + 低 dropout |
| 從頭訓練深層模型 | LayerNorm + weight decay |
| 模型很大但資料不夠多 | 增加 dropout（p=0.1→0.2） |
| 訓練很穩但驗證不收斂 | 降低 lr、增加 weight decay |

## 動手練習

製造一個過擬合場景，再用早停解決：

```python
import torch, torch.nn as nn
from torch.utils.data import TensorDataset, DataLoader

torch.manual_seed(0)
# 只有 50 筆訓練資料（容易過擬合）
X_train = torch.randn(50, 20)
y_train = (X_train[:, 0] > 0).float().unsqueeze(1)
X_val   = torch.randn(200, 20)
y_val   = (X_val[:, 0] > 0).float().unsqueeze(1)

model = nn.Sequential(
    nn.Linear(20, 128), nn.ReLU(),
    nn.Linear(128, 128), nn.ReLU(),
    nn.Linear(128, 1), nn.Sigmoid()
)
optimizer = torch.optim.Adam(model.parameters(), lr=0.01)
criterion = nn.BCELoss()

# 訓練 200 epochs，觀察 train vs val loss 的分裂
# 再加 Dropout(0.3) 在中間層，看差異
```

## 自我檢核

- [ ] 能用 training/val loss 曲線判斷是否過擬合
- [ ] 知道 dropout 在訓練和推論時的行為不同
- [ ] 能解釋 LayerNorm 做了什麼，以及為什麼 Transformer 不用 BatchNorm
- [ ] 知道 weight decay 作用在哪些參數上

→ [Ch 7 語言模型是什麼：next-token prediction](./07-language-model-basics.md)
