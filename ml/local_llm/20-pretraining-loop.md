# Ch 20 — Pre-training loop：DataLoader / checkpointing

> 目標：寫出一個生產等級的訓練迴圈，包含 checkpoint 儲存、繼續訓練、訓練中斷恢復。

## 訓練迴圈的幾個層次

Ch 12 的練習有一個最簡訓練迴圈。實際上需要更多東西：

```
最簡版：zero_grad → forward → loss → backward → step
實際需要：
  ✓ 分離 train/val 評估
  ✓ 定期儲存 checkpoint
  ✓ 梯度裁剪（gradient clipping）
  ✓ 可以從 checkpoint 繼續訓練
  ✓ 訓練統計（loss、throughput）
  ✓ 記憶體效率（gradient accumulation）
```

## 完整 DataLoader 設定

```python
import torch
from torch.utils.data import Dataset, DataLoader

class TokenDataset(Dataset):
    """把打包好的 token ids 轉成 (input, target) pairs"""
    def __init__(self, token_ids, block_size):
        self.data = token_ids
        self.block_size = block_size

    def __len__(self):
        return len(self.data) - self.block_size

    def __getitem__(self, idx):
        x = self.data[idx : idx + self.block_size]
        y = self.data[idx + 1 : idx + self.block_size + 1]
        return x, y

# 建立 DataLoader
def make_dataloader(token_ids, block_size=256, batch_size=32, shuffle=True):
    dataset = TokenDataset(token_ids, block_size)
    return DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=shuffle,
        pin_memory=True,  # CPU 訓練時 False 就好
        num_workers=0,    # Windows 用 0，否則 multiprocessing 問題
    )
```

## Checkpoint：儲存和恢復

Checkpoint 儲存的內容必須包含**能完整恢復訓練狀態**的所有東西：

```python
import os

def save_checkpoint(model, optimizer, step, loss, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    torch.save({
        'step': step,
        'model_state_dict': model.state_dict(),
        'optimizer_state_dict': optimizer.state_dict(),
        'loss': loss,
    }, path)
    print(f"Checkpoint 儲存：{path}（step {step}）")

def load_checkpoint(model, optimizer, path, device='cpu'):
    if not os.path.exists(path):
        return 0  # 從頭開始
    ckpt = torch.load(path, map_location=device)
    model.load_state_dict(ckpt['model_state_dict'])
    optimizer.load_state_dict(ckpt['optimizer_state_dict'])
    step = ckpt['step']
    print(f"從 checkpoint 恢復：step {step}，loss {ckpt['loss']:.4f}")
    return step
```

## 完整訓練迴圈

```python
import time

def train(
    model,
    train_loader,
    val_loader,
    optimizer,
    max_steps=10000,
    eval_every=500,
    save_every=1000,
    checkpoint_dir="checkpoints",
    resume_from=None,
):
    device = 'cpu'
    model.to(device)

    # 從 checkpoint 恢復
    start_step = 0
    if resume_from:
        start_step = load_checkpoint(model, optimizer, resume_from, device)

    model.train()
    train_iter = iter(train_loader)
    running_loss = 0.0
    t0 = time.time()

    for step in range(start_step, max_steps):
        # 取下一個 batch（循環 DataLoader）
        try:
            x, y = next(train_iter)
        except StopIteration:
            train_iter = iter(train_loader)
            x, y = next(train_iter)

        x, y = x.to(device), y.to(device)

        # 前向 + 計算 loss
        optimizer.zero_grad(set_to_none=True)  # 比 zero_grad() 更省記憶體
        logits, loss = model(x, y)

        # 反向傳播
        loss.backward()

        # 梯度裁剪（防止梯度爆炸，LLM 訓練必備）
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)

        optimizer.step()
        running_loss += loss.item()

        # 定期評估
        if (step + 1) % eval_every == 0:
            val_loss = evaluate(model, val_loader, device)
            avg_train = running_loss / eval_every
            elapsed = time.time() - t0
            tokens_per_sec = eval_every * x.shape[0] * x.shape[1] / elapsed

            print(f"step {step+1:6d} | "
                  f"train_loss: {avg_train:.4f} | "
                  f"val_loss: {val_loss:.4f} | "
                  f"throughput: {tokens_per_sec:.0f} tok/s")

            running_loss = 0.0
            t0 = time.time()
            model.train()

        # 定期存 checkpoint
        if (step + 1) % save_every == 0:
            path = os.path.join(checkpoint_dir, f"ckpt_{step+1:06d}.pt")
            save_checkpoint(model, optimizer, step + 1, loss.item(), path)

@torch.no_grad()
def evaluate(model, loader, device, num_batches=50):
    model.eval()
    losses = []
    for i, (x, y) in enumerate(loader):
        if i >= num_batches:
            break
        x, y = x.to(device), y.to(device)
        _, loss = model(x, y)
        losses.append(loss.item())
    return sum(losses) / len(losses)
```

## Gradient Accumulation（CPU 必備技巧）

記憶體不夠時，用 gradient accumulation 模擬大 batch：

```python
accum_steps = 4  # 累積 4 個 batch 才更新一次
# 等效 batch_size = batch_size × accum_steps

for step in range(max_steps):
    total_loss = 0
    for micro_step in range(accum_steps):
        x, y = next(train_iter)
        logits, loss = model(x, y)
        loss = loss / accum_steps  # 縮放 loss
        loss.backward()
        total_loss += loss.item()

    torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
    optimizer.step()
    optimizer.zero_grad(set_to_none=True)
```

## 訓練速度監控

```python
import time

class Throughput:
    def __init__(self):
        self.start = time.time()
        self.tokens = 0

    def update(self, num_tokens):
        self.tokens += num_tokens

    def report(self):
        elapsed = time.time() - self.start
        tps = self.tokens / elapsed
        self.start = time.time()
        self.tokens = 0
        return tps
```

CPU 訓練的典型速度（取決於模型大小和 batch size）：
- 小模型（<1M 參數）：1000–5000 tok/s
- 中模型（~10M 參數）：100–500 tok/s
- 大模型（~100M 參數）：10–50 tok/s

## 動手練習

把以上元件組裝成一個完整的訓練腳本，加上以下功能：

```python
# 在訓練時，按 Ctrl+C 能優雅地儲存 checkpoint 再退出
import signal

def handle_interrupt(sig, frame):
    print("\n訓練中斷，儲存 checkpoint...")
    save_checkpoint(model, optimizer, current_step, current_loss, "checkpoints/interrupted.pt")
    exit(0)

signal.signal(signal.SIGINT, handle_interrupt)
```

## 自我檢核

- [ ] 理解 `set_to_none=True` 比 `zero_grad()` 省哪裡
- [ ] 能解釋梯度裁剪的作用
- [ ] 寫出能從 checkpoint 恢復訓練的完整流程
- [ ] 理解 gradient accumulation 等效大 batch 的原理

→ [Ch 21 Loss 曲線解讀：如何判斷訓練是否健康](./21-loss-diagnostics.md)
