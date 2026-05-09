# Ch 23 — 分散式訓練概念：DDP / gradient accumulation

> 目標：理解多 GPU 訓練的主要策略，即使你現在沒有 GPU，這些知識在看論文和工程文件時都用得到。

## 為什麼需要分散式訓練

一張 A100 80GB GPU 的訓練速度和記憶體，對訓練 7B 參數的模型來說已經是極限（FP16 需要 14GB 存參數，梯度、優化器狀態還要乘以 3–6 倍）。

訓練 Llama 3 70B 需要幾百張 H100，幾個月的時間。

分散式訓練的目標：**要麼塞進更多 batch（加速），要麼把模型拆開到多個設備上（解決記憶體問題）**。

## 方法一：Data Parallel（DP / DDP）

最常用，每個 GPU 有完整的模型副本，但處理不同的資料：

```
GPU 0: 模型副本 + batch 0-31   → 各自算 loss 和梯度
GPU 1: 模型副本 + batch 32-63  → 各自算 loss 和梯度
GPU 2: 模型副本 + batch 64-95  → 各自算 loss 和梯度
GPU 3: 模型副本 + batch 96-127 → 各自算 loss 和梯度
                  ↓
          AllReduce（平均梯度）
                  ↓
          所有 GPU 同步更新
```

PyTorch 的 DDP（DistributedDataParallel）比舊版 DP 效率好很多：

```python
import torch
import torch.nn as nn
from torch.nn.parallel import DistributedDataParallel as DDP
import torch.distributed as dist

def setup(rank, world_size):
    dist.init_process_group("nccl", rank=rank, world_size=world_size)

def train_ddp(rank, world_size, model, dataset):
    setup(rank, world_size)
    model = model.to(rank)
    model = DDP(model, device_ids=[rank])

    # 每個進程只看到 1/world_size 的資料
    sampler = DistributedSampler(dataset, num_replicas=world_size, rank=rank)
    loader  = DataLoader(dataset, sampler=sampler, batch_size=32)

    for x, y in loader:
        x, y = x.to(rank), y.to(rank)
        loss = model(x, y)
        loss.backward()
        optimizer.step()
        optimizer.zero_grad()

# 啟動方式
# torchrun --nproc_per_node=4 train.py
```

等效 batch_size = 32 × 4 = 128（4 個 GPU），訓練時間接近 1/4。

## 方法二：Tensor Parallel（模型橫向切）

當單一層的權重太大，放不進一張卡，就要切層內的張量：

```
Linear 層的矩陣 W [4096, 4096]，切成 4 份：
GPU 0: W[:, 0:1024]
GPU 1: W[:, 1024:2048]
GPU 2: W[:, 2048:3072]
GPU 3: W[:, 3072:4096]

每個 GPU 算一部分，最後 AllGather 合併結果
```

這是訓練 70B+ 模型的必要技術，Megatron-LM 是最知名的實作。

## 方法三：Pipeline Parallel（模型縱向切）

把不同層放到不同 GPU：

```
GPU 0: Layer 1–8   → 輸出送給 GPU 1
GPU 1: Layer 9–16  → 輸出送給 GPU 2
GPU 2: Layer 17–24 → 輸出送給 GPU 3
GPU 3: Layer 25–32 → 計算 loss，反向傳播
```

問題：後面的 GPU 要等前面算完，效率低（"pipeline bubble"）。需要 micro-batching 技術來填充。

## 方法四：ZeRO（Zero Redundancy Optimizer）

DeepSpeed 的核心技術，消除 DDP 中各 GPU 儲存相同優化器狀態的冗餘：

| ZeRO Stage | 切分什麼 | 記憶體節省 |
|-----------|---------|---------|
| Stage 1 | 優化器狀態 | 4x |
| Stage 2 | 優化器狀態 + 梯度 | 8x |
| Stage 3 | 優化器狀態 + 梯度 + 參數 | 64x |

ZeRO Stage 3 理論上讓 N 張 GPU 訓練 N 倍大的模型——但通信成本也更高。

## Gradient Accumulation：單卡的「偽多 GPU」

Ch 20 提過，gradient accumulation 讓你用小 batch 模擬大 batch 的效果：

```python
# 4 個 micro-batch 的梯度累積 = 等效 4x batch_size
accum_steps = 4

for step in range(total_steps):
    for micro_step in range(accum_steps):
        x, y = get_batch()
        loss = model(x, y) / accum_steps  # 縮放！
        loss.backward()

    # 梯度裁剪（要在 step() 前）
    torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
    optimizer.step()
    optimizer.zero_grad()
    scheduler.step()
```

**這是你在 CPU 上最實用的技術**：記憶體不夠增大 batch_size 時，改用 gradient accumulation。

## 現代 LLM 訓練的典型組合

| 規模 | 典型配置 |
|------|---------|
| 1B 模型 | 數十張 A100，DDP |
| 7B 模型 | 64–128 張 A100，DDP + ZeRO-2 |
| 70B 模型 | 512 張 A100/H100，TP + PP + DDP + ZeRO |
| GPT-4 規模 | 未公開，估計數千張 A100 |

## 不用 GPU 的你現在要記住什麼

1. **Gradient Accumulation 你今天就能用**：CPU 訓練記憶體不夠時，`accum_steps=4` 或更高。

2. **DDP 等你有多 GPU 再說**：`torchrun --nproc_per_node=N` 一行搞定。

3. **ZeRO 是 DeepSpeed 提供的**：`pip install deepspeed`，有配置文件就能用，不用手寫。

4. **Fine-tuning 用不到這些**：LoRA fine-tune 一個 7B 模型，單卡 16GB VRAM 就夠，不需要分散式。

## 動手練習

理解 gradient accumulation 的數學等效性：

```python
import torch
import torch.nn as nn

torch.manual_seed(0)
model = nn.Linear(10, 5)
X = torch.randn(8, 10)
y = torch.randint(0, 5, (8,))

# 方法 A：一次 batch_size=8
criterion = nn.CrossEntropyLoss()
loss_a = criterion(model(X), y)
loss_a.backward()
grad_a = model.weight.grad.clone()
model.zero_grad()

# 方法 B：兩個 micro-batch，各 4 筆，累積梯度
for i in [0, 4]:
    loss_b = criterion(model(X[i:i+4]), y[i:i+4]) / 2  # 除以 accum_steps
    loss_b.backward()
grad_b = model.weight.grad.clone()

# 驗證：梯度應該相同（允許浮點誤差）
print(torch.allclose(grad_a, grad_b, atol=1e-6))  # True
```

## 自我檢核

- [ ] 能畫出 DDP 的通信模式（各 GPU 計算梯度 → AllReduce → 同步更新）
- [ ] 理解 ZeRO Stage 3 節省了什麼
- [ ] 知道 gradient accumulation 等效大 batch 的數學原因（gradient 是線性的）
- [ ] 跑過 gradient accumulation 等效驗證

→ [Ch 24 評估：perplexity / 生成品質怎麼量](./24-evaluation.md)
