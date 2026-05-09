# Ch 21 — Loss 曲線解讀：如何判斷訓練是否健康

> 目標：看到 loss 曲線就能診斷訓練狀態，知道什麼時候該調 lr、什麼時候是 bug。

## 健康的訓練曲線長什麼樣

```
Loss
 │
 ▓▓
 │ ▓▓▓
 │    ▓▓▓▓
 │        ▓▓▓▓▓▓
 │              ▓▓▓▓▓▓▓▓
 └──────────────────────────→ Steps
```

特徵：
1. **初始 loss ≈ log(vocab_size)**（隨機猜測的 baseline）
2. **前幾步下降最快**（模型學到最顯著的模式）
3. **後期下降變緩**（曲線呈對數形狀）
4. **曲線抖動但整體下降**（隨機 batch 導致的正常抖動）

## 七種常見的訓練問題

### 問題一：loss 不動（卡在初始值）

```
Loss = 5.5, 5.5, 5.5, 5.5 ...（一直不動）
```

**可能原因**：
- Learning rate 太低（`lr=1e-10`）
- 梯度沒有流（`requires_grad=False`、`detach()` 位置錯誤）
- `optimizer.step()` 沒有被呼叫

**診斷**：
```python
# 檢查梯度是否存在
loss.backward()
for name, p in model.named_parameters():
    if p.grad is None:
        print(f"{name}: 無梯度！")
    elif p.grad.abs().max() < 1e-10:
        print(f"{name}: 梯度極小 {p.grad.abs().max():.2e}")
```

### 問題二：loss 爆炸（變成 NaN 或 inf）

```
Loss = 3.2, 2.8, 15.4, NaN, NaN ...
```

**可能原因**：
- Learning rate 太高
- 沒有梯度裁剪
- 輸入資料有 NaN

**診斷**：
```python
# 找出第一個出現 NaN 的地方
for name, p in model.named_parameters():
    if torch.isnan(p).any():
        print(f"{name} 有 NaN！")
    if p.grad is not None and torch.isnan(p.grad).any():
        print(f"{name}.grad 有 NaN！")
```

**解法**：降低 lr，確保 gradient clipping（`max_norm=1.0`）。

### 問題三：loss 下降後反彈

```
Loss: 5.5 → 3.0 → 1.8 → 2.5 → 3.1 ...（下降後升高）
```

**可能原因**：
- Learning rate 太高，在 minimum 附近震盪
- 過擬合（訓練 loss 繼續降，但 validation loss 升高）

**解法**：降低 lr，或使用 lr schedule（Ch 22）。

### 問題四：train loss 降，val loss 不降（過擬合）

```
step 1000: train=1.5, val=1.6  ← 正常
step 3000: train=0.8, val=1.7  ← 開始過擬合
step 5000: train=0.3, val=2.5  ← 嚴重過擬合
```

**解法**：早停、增加 dropout、增加 weight decay、或準備更多資料。

### 問題五：loss 下降太慢

```
Loss: 5.5 → 5.3 → 5.1 → 4.9 ...（100步後還在 4.9）
```

**可能原因**：Learning rate 太低。

**解法**：提高 lr（試試 10x），觀察是否加速。

### 問題六：兩段損失（loss 卡住後突然跳降）

```
Loss: 3.5, 3.5, 3.5, 3.5, 1.2, 0.8, 0.7 ...（突然跳降）
```

正常現象，叫做「grokking」或「loss spike recovery」。模型正在學習某種整體性的結構，突破之前需要在某個「臨界狀態」維持一段時間。

### 問題七：loss 在 log(vocab_size) 以上

初始值應該接近 `log(vocab_size)`，如果明顯高於這個值：

```python
import math
vocab_size = 50257
baseline = math.log(vocab_size)
print(f"baseline: {baseline:.3f}")  # 10.82

# 如果初始 loss = 15.0，說明初始化有問題
```

**可能原因**：模型初始化不正確（應該用接近 0 的小數值）。

## 用 Matplotlib 畫訓練曲線

```python
import matplotlib.pyplot as plt

train_losses = []  # 每步的 train loss
val_losses   = []  # 每 eval_every 步的 val loss
val_steps    = []

# 訓練迴圈裡收集...
# train_losses.append(loss.item())
# val_losses.append(val_loss); val_steps.append(step)

def plot_losses(train_losses, val_losses, val_steps, smooth=50):
    fig, ax = plt.subplots(figsize=(10, 5))

    # 平滑 train loss（移動平均）
    if len(train_losses) >= smooth:
        smoothed = [
            sum(train_losses[max(0,i-smooth):i+1]) / min(i+1, smooth)
            for i in range(len(train_losses))
        ]
        ax.plot(smoothed, alpha=0.8, label="train loss（平滑）", color="blue")
    ax.plot(train_losses, alpha=0.2, color="blue")

    ax.plot(val_steps, val_losses, "o-", label="val loss", color="red")

    ax.set_xlabel("Steps")
    ax.set_ylabel("Loss")
    ax.legend()
    ax.set_title("訓練曲線")
    plt.tight_layout()
    plt.savefig("training_curve.png")
    print("訓練曲線已儲存")
```

## 快速健康檢查 checklist

訓練開始後第一步就確認：

```python
# 1. 初始 loss 是否合理
assert abs(initial_loss - math.log(vocab_size)) < 1.0, \
    f"初始 loss 異常：{initial_loss:.3f}（應接近 {math.log(vocab_size):.3f}）"

# 2. 第一步後 loss 是否有下降
assert loss_after_1_step < initial_loss, \
    "第一步後 loss 沒有下降，梯度可能有問題"

# 3. 確認無 NaN
assert not torch.isnan(loss), "loss 是 NaN"
```

## 動手練習

故意製造每種問題，然後修復：

```python
import torch, math
import torch.nn as nn

# 基準模型
model = nn.Linear(10, 100)
x = torch.randn(32, 10)
y = torch.randint(0, 100, (32,))
criterion = nn.CrossEntropyLoss()

# 問題 1：lr 太低
opt = torch.optim.SGD(model.parameters(), lr=1e-10)
for _ in range(5):
    loss = criterion(model(x), y)
    opt.zero_grad(); loss.backward(); opt.step()
    print(f"lr=1e-10: {loss.item():.4f}")  # 幾乎不動

# 問題 2：lr 太高
opt = torch.optim.SGD(model.parameters(), lr=100)
for _ in range(5):
    loss = criterion(model(x), y)
    opt.zero_grad(); loss.backward(); opt.step()
    print(f"lr=100: {loss.item():.4f}")   # 爆炸
```

## 自我檢核

- [ ] 能從 loss 曲線診斷「lr 太高」和「lr 太低」
- [ ] 知道 NaN loss 最常見的三個原因
- [ ] 理解 train/val loss 分裂代表什麼
- [ ] 跑過故意破壞訓練的練習，看到各種異常

→ [Ch 22 學習率排程：warmup / cosine decay](./22-lr-schedule.md)
