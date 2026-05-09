# Ch 22 — 學習率排程：warmup / cosine decay

> 目標：理解為什麼固定 learning rate 不夠好，以及 warmup + cosine decay 怎麼工作。

## 為什麼 learning rate 需要動態調整

固定 lr 的問題：

- **太大**：在最優點附近震盪，收不到低點
- **太小**：收斂極慢，等到天荒地老

理想情況：**訓練初期大步走（探索），後期小步走（精修）**。

## 學習率排程的三個階段

```
lr
 │         ╭────╮
 │        ╱      ╲
 │       ╱        ╲
 │      ╱          ╲
 │─────╱            ╲────────
 └─────────────────────────→ steps
   warmup  peak   cosine decay
```

### 1. Warmup（暖機）

訓練最初幾百步，lr 從 0 線性增加到峰值。

為什麼需要 warmup：
- 模型初始化是隨機的，梯度方向不穩定
- 一開始就用大 lr，早期的錯誤更新會把模型推到很差的地方
- Warmup 讓模型先「定向」，再開始真正學習

### 2. 峰值 lr

訓練的主力階段。

### 3. Cosine Decay

學習率按餘弦曲線從峰值衰減到最小值（通常是峰值的 1/10）。

```python
import math

def cosine_lr_with_warmup(
    current_step,
    warmup_steps,
    total_steps,
    max_lr,
    min_lr,
):
    # Warmup 階段：線性增加
    if current_step < warmup_steps:
        return max_lr * current_step / warmup_steps

    # Decay 階段：cosine 衰減
    progress = (current_step - warmup_steps) / (total_steps - warmup_steps)
    cosine_decay = 0.5 * (1 + math.cos(math.pi * progress))
    return min_lr + (max_lr - min_lr) * cosine_decay

# 範例：總共訓練 10000 步，warmup 500 步
max_lr = 3e-4
min_lr = 3e-5
steps = list(range(10000))
lrs = [cosine_lr_with_warmup(s, 500, 10000, max_lr, min_lr) for s in steps]

# 用 matplotlib 畫出來
import matplotlib.pyplot as plt
plt.plot(steps, lrs)
plt.xlabel("Steps"); plt.ylabel("Learning Rate")
plt.title("Warmup + Cosine Decay")
plt.savefig("lr_schedule.png")
```

## 在 PyTorch 裡實作

最乾淨的方式是用 `LambdaLR`：

```python
import torch

def get_lr_scheduler(optimizer, warmup_steps, total_steps, min_lr_ratio=0.1):
    def lr_lambda(step):
        if step < warmup_steps:
            return step / warmup_steps
        progress = (step - warmup_steps) / (total_steps - warmup_steps)
        return min_lr_ratio + (1 - min_lr_ratio) * 0.5 * (1 + math.cos(math.pi * progress))

    return torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda)

# 使用方式
model = ...
optimizer = torch.optim.AdamW(model.parameters(), lr=3e-4)
scheduler = get_lr_scheduler(optimizer, warmup_steps=100, total_steps=5000)

for step in range(5000):
    optimizer.zero_grad()
    loss = ...
    loss.backward()
    optimizer.step()
    scheduler.step()  # ← 每步更新 lr

    if step % 500 == 0:
        current_lr = optimizer.param_groups[0]['lr']
        print(f"step {step}: lr={current_lr:.2e}")
```

## 常用 lr 超參數

| 模型大小 | 峰值 lr | warmup steps | 備注 |
|---------|---------|-------------|------|
| ~1M 參數（小模型） | 1e-3 | 100–500 | 小模型可以用更大 lr |
| ~100M 參數（GPT-2 規模） | 3e-4 | 1000–2000 | GPT-2 的設定 |
| ~7B 參數（Llama 規模） | 3e-4 | 2000 | Llama 的設定 |

**min_lr**：通常設為 max_lr 的 1/10，讓訓練後期還有微小的更新能力。

## 其他常見排程

### Linear Decay

比 cosine 更激進，訓練末期 lr 降到非常小：

```python
# PyTorch 內建
scheduler = torch.optim.lr_scheduler.LinearLR(
    optimizer, start_factor=1.0, end_factor=0.1, total_iters=total_steps
)
```

### StepLR

每隔 N 步，把 lr 乘以一個因子（比較老舊的做法）：

```python
scheduler = torch.optim.lr_scheduler.StepLR(
    optimizer, step_size=1000, gamma=0.5
)
```

### ReduceLROnPlateau

當 val loss 不再改善時，自動降 lr：

```python
scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
    optimizer, mode='min', patience=3, factor=0.5
)
# 每次 eval 後呼叫
scheduler.step(val_loss)
```

## warmup steps 怎麼選

**規則**：`warmup_steps ≈ total_steps / 20`，即訓練前 5% 的步數。

但不同規模的模型差很多：
- 10K 步的小模型：warmup 500 步
- 1M 步的大模型（如 Llama）：warmup 2000 步

warmup 太短：訓練初期不穩定。
warmup 太長：浪費步數在低 lr 上。

## 動手練習

比較有無 lr schedule 的訓練效果：

```python
import torch
import torch.nn as nn
import math

torch.manual_seed(42)

def make_model():
    return nn.Sequential(nn.Linear(20, 128), nn.ReLU(), nn.Linear(128, 10))

def train_with_schedule(use_schedule, steps=1000):
    model = make_model()
    optimizer = torch.optim.AdamW(model.parameters(), lr=3e-4)
    if use_schedule:
        scheduler = get_lr_scheduler(optimizer, warmup_steps=50, total_steps=steps)

    X = torch.randn(512, 20)
    y = torch.randint(0, 10, (512,))
    losses = []

    for step in range(steps):
        optimizer.zero_grad()
        loss = nn.CrossEntropyLoss()(model(X), y)
        loss.backward()
        optimizer.step()
        if use_schedule:
            scheduler.step()
        losses.append(loss.item())

    return losses

losses_fixed    = train_with_schedule(False)
losses_schedule = train_with_schedule(True)

import matplotlib.pyplot as plt
plt.plot(losses_fixed,    label="固定 lr", alpha=0.7)
plt.plot(losses_schedule, label="Warmup+Cosine", alpha=0.7)
plt.legend(); plt.savefig("lr_comparison.png")
print(f"固定 lr 最終 loss:   {losses_fixed[-1]:.4f}")
print(f"有排程最終 loss: {losses_schedule[-1]:.4f}")
```

## 自我檢核

- [ ] 能解釋 warmup 的必要性
- [ ] 用 `LambdaLR` 實作 warmup + cosine decay
- [ ] 知道 warmup_steps 大約是總步數的幾分之一
- [ ] 跑過比較實驗，看到有排程的訓練效果更好

→ [Ch 23 分散式訓練概念：DDP / gradient accumulation](./23-distributed-training.md)
