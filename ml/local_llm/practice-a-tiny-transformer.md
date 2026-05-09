# 練習 A — 從頭實作 tiny Transformer 並訓練它

> 目標：用純 PyTorch 實作一個完整的 GPT 模型，訓練在唐詩語料上，讓它能生成像樣的中文詩句。

## 任務規格

| 項目 | 規格 |
|------|------|
| 模型 | Character-level GPT（decoder-only） |
| 語料 | 唐詩三百首（或自選中文文本） |
| vocab | 字元級（unique chars in 語料） |
| d_model | 128 |
| num_heads | 4 |
| num_layers | 4 |
| max_seq_len | 128 |
| 訓練目標 | loss < 1.5（大約能生成有意義的詩句片段） |
| 硬體要求 | CPU 可跑，約 20–30 分鐘 |

## 語料準備

下載唐詩語料（純文字，每首一行或連續文字都可以）：

```python
# 方法一：用 requests 下載（若有網路）
# 網路上有很多開源唐詩資料集，搜尋 "唐詩三百首 txt"

# 方法二：用內建的一小段測試（確保流程通了再換大語料）
POEM_SAMPLE = """
床前明月光疑是地上霜舉頭望明月低頭思故鄉
春眠不覺曉處處聞啼鳥夜來風雨聲花落知多少
紅豆生南國春來發幾枝願君多採擷此物最相思
白日依山盡黃河入海流欲窮千里目更上一層樓
"""
```

## 期望輸出範例

訓練前（隨機）：
```
輸入：床
輸出：床觀脫脫昨覺春滿入入觀鄉桌桌低
```

訓練 2000 步後：
```
輸入：床
輸出：床前明月光疑是地上霜舉頭望明
```

訓練 5000 步後（如果語料夠大）：
```
輸入：春
輸出：春眠不覺曉處處聞啼鳥夜來風雨
```

## 實作步驟

### Step 1：資料預處理

```python
import torch

# 讀取語料
with open("poems.txt", encoding="utf-8") as f:
    text = f.read()

# 去除非中文字符（標點、英文等）
import re
text = re.sub(r'[^一-鿿]', '', text)
print(f"語料長度：{len(text)} 個字")

# 建立字元到整數的映射
chars = sorted(set(text))
vocab_size = len(chars)
print(f"vocab size: {vocab_size}")

stoi = {c: i for i, c in enumerate(chars)}
itos = {i: c for i, c in enumerate(chars)}
encode = lambda s: [stoi[c] for c in s]
decode = lambda l: ''.join([itos[i] for i in l])

# 轉成 tensor
data = torch.tensor(encode(text), dtype=torch.long)
print(f"data shape: {data.shape}")

# 切 train / val
n = int(0.9 * len(data))
train_data = data[:n]
val_data   = data[n:]
```

### Step 2：資料取樣函數

```python
def get_batch(data, block_size=128, batch_size=32):
    ix = torch.randint(len(data) - block_size, (batch_size,))
    x  = torch.stack([data[i:i+block_size]   for i in ix])
    y  = torch.stack([data[i+1:i+block_size+1] for i in ix])
    return x, y
```

### Step 3：建立模型

貼入 Ch 12 的完整 `GPT` 類別（`CausalSelfAttention`、`GPTBlock`、`GPT`），然後：

```python
model = GPT(
    vocab_size  = vocab_size,
    d_model     = 128,
    num_heads   = 4,
    num_layers  = 4,
    max_seq_len = 128,
    dropout     = 0.1,
)
total_params = sum(p.numel() for p in model.parameters())
print(f"參數量：{total_params:,}")
```

### Step 4：訓練迴圈

```python
optimizer = torch.optim.AdamW(model.parameters(), lr=3e-4, weight_decay=0.1)

@torch.no_grad()
def estimate_loss(eval_iters=50):
    model.eval()
    losses = {}
    for split, data in [('train', train_data), ('val', val_data)]:
        L = []
        for _ in range(eval_iters):
            x, y = get_batch(data)
            _, loss = model(x, y)
            L.append(loss.item())
        losses[split] = sum(L) / len(L)
    model.train()
    return losses

max_iters = 5000
eval_every = 500

for step in range(max_iters):
    x, y = get_batch(train_data)
    optimizer.zero_grad()
    _, loss = model(x, y)
    loss.backward()
    # 梯度裁剪，防止梯度爆炸
    torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
    optimizer.step()

    if step % eval_every == 0:
        losses = estimate_loss()
        print(f"step {step:4d} | train_loss: {losses['train']:.4f} | val_loss: {losses['val']:.4f}")
```

### Step 5：生成並評估

```python
def generate_text(prompt_char, max_new=50, temperature=0.8, top_k=20):
    model.eval()
    prompt = torch.tensor([[stoi[c] for c in prompt_char]], dtype=torch.long)
    output = model.generate(prompt, max_new_tokens=max_new, temperature=temperature, top_k=top_k)
    return decode(output[0].tolist())

# 測試幾個起始字
for c in ["床", "春", "白", "紅"]:
    if c in stoi:
        print(f"[{c}] → {generate_text(c)[:40]}")
```

## 完整參考解答

**寫完再看！** 先跑自己的版本，讓 loss 降到 1.5 以下，再來對照。

<details>
<summary>點開參考實作（含超參數和除錯提示）</summary>

```python
import torch
import torch.nn as nn
import torch.nn.functional as F
import math
import re

# ===== 資料 =====
POEM = """
床前明月光疑是地上霜舉頭望明月低頭思故鄉春眠不覺曉處處聞啼鳥
夜來風雨聲花落知多少紅豆生南國春來發幾枝願君多採擷此物最相思
白日依山盡黃河入海流欲窮千里目更上一層樓鋤禾日當午汗滴禾下土
誰知盤中餐粒粒皆辛苦故人西辭黃鶴樓煙花三月下揚州孤帆遠影碧空盡
唯見長江天際流朝辭白帝彩雲間千里江陵一日還兩岸猿聲啼不住輕舟已過萬重山
""" * 50  # 重複讓語料夠長

text = re.sub(r'[^一-鿿]', '', POEM)
chars = sorted(set(text))
stoi = {c: i for i, c in enumerate(chars)}
itos = {i: c for i, c in enumerate(chars)}
encode = lambda s: [stoi[c] for c in s if c in stoi]
decode = lambda l: ''.join(itos.get(i, '?') for i in l)

data = torch.tensor(encode(text), dtype=torch.long)
n = int(0.9 * len(data))
train_data, val_data = data[:n], data[n:]

# ===== 模型（與 Ch 12 相同） =====
class CausalSelfAttention(nn.Module):
    def __init__(self, d_model, num_heads, max_seq_len, dropout=0.1):
        super().__init__()
        self.num_heads = num_heads
        self.d_head    = d_model // num_heads
        self.qkv  = nn.Linear(d_model, 3 * d_model, bias=False)
        self.proj = nn.Linear(d_model, d_model, bias=False)
        self.drop = nn.Dropout(dropout)
        self.register_buffer("mask", torch.tril(torch.ones(max_seq_len, max_seq_len)))

    def forward(self, x):
        B, T, C = x.shape; H, D = self.num_heads, self.d_head
        q, k, v = self.qkv(x).split(C, dim=2)
        q = q.view(B,T,H,D).transpose(1,2); k = k.view(B,T,H,D).transpose(1,2); v = v.view(B,T,H,D).transpose(1,2)
        scores = q @ k.transpose(-2,-1) / math.sqrt(D)
        scores = scores.masked_fill(self.mask[:T,:T]==0, float('-inf'))
        out = (self.drop(F.softmax(scores,dim=-1)) @ v).transpose(1,2).contiguous().view(B,T,C)
        return self.proj(out)

class GPTBlock(nn.Module):
    def __init__(self, d_model, num_heads, max_seq_len, dropout=0.1):
        super().__init__()
        self.ln1  = nn.LayerNorm(d_model); self.ln2 = nn.LayerNorm(d_model)
        self.attn = CausalSelfAttention(d_model, num_heads, max_seq_len, dropout)
        self.ff   = nn.Sequential(nn.Linear(d_model, 4*d_model), nn.GELU(), nn.Linear(4*d_model, d_model), nn.Dropout(dropout))
    def forward(self, x): return x + self.ff(self.ln2(x + self.attn(self.ln1(x))))

class GPT(nn.Module):
    def __init__(self, vocab_size, d_model=128, num_heads=4, num_layers=4, max_seq_len=128, dropout=0.1):
        super().__init__()
        self.max_seq_len = max_seq_len
        self.tok_emb = nn.Embedding(vocab_size, d_model)
        self.pos_emb = nn.Embedding(max_seq_len, d_model)
        self.drop    = nn.Dropout(dropout)
        self.blocks  = nn.ModuleList([GPTBlock(d_model, num_heads, max_seq_len, dropout) for _ in range(num_layers)])
        self.ln_f    = nn.LayerNorm(d_model)
        self.head    = nn.Linear(d_model, vocab_size, bias=False)
        self.head.weight = self.tok_emb.weight
        self.apply(lambda m: nn.init.normal_(m.weight, 0, 0.02) if isinstance(m, (nn.Linear, nn.Embedding)) else None)

    def forward(self, idx, targets=None):
        B, T = idx.shape
        x = self.drop(self.tok_emb(idx) + self.pos_emb(torch.arange(T, device=idx.device)))
        for b in self.blocks: x = b(x)
        logits = self.head(self.ln_f(x))
        loss = F.cross_entropy(logits.view(-1, logits.size(-1)), targets.view(-1)) if targets is not None else None
        return logits, loss

    @torch.no_grad()
    def generate(self, idx, max_new_tokens, temperature=1.0, top_k=None):
        for _ in range(max_new_tokens):
            idx_c = idx[:, -self.max_seq_len:]
            logits, _ = self(idx_c)
            logits = logits[:, -1, :] / temperature
            if top_k:
                v, _ = torch.topk(logits, top_k)
                logits[logits < v[:, [-1]]] = float('-inf')
            idx = torch.cat([idx, torch.multinomial(F.softmax(logits,-1), 1)], dim=1)
        return idx

# ===== 訓練 =====
torch.manual_seed(42)
model = GPT(len(chars))
opt   = torch.optim.AdamW(model.parameters(), lr=3e-4, weight_decay=0.1)

def get_batch(data, bs=32, bl=128):
    ix = torch.randint(len(data)-bl, (bs,))
    return torch.stack([data[i:i+bl] for i in ix]), torch.stack([data[i+1:i+bl+1] for i in ix])

for step in range(3000):
    x, y = get_batch(train_data)
    _, loss = model(x, y)
    opt.zero_grad(); loss.backward()
    torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
    opt.step()
    if step % 500 == 0: print(f"step {step}: loss={loss.item():.3f}")

# 生成
for c in ["床", "春", "白"]:
    if c in stoi:
        out = model.generate(torch.tensor([[stoi[c]]]), max_new_tokens=40, temperature=0.7, top_k=15)
        print(f"[{c}] {decode(out[0].tolist())}")
```

**除錯提示**：
- loss 卡在 log(vocab_size) 不動 → 檢查梯度是否有流（`loss.backward()` 前 print `loss.item()`）
- 生成重複字元 → temperature 調高（0.8→1.2），或增大 top_k
- OOM → 減小 batch_size 或 d_model

</details>

## 測試用例

| 條件 | 期望 |
|------|------|
| step 0，loss | 接近 `log(vocab_size)`，通常 3.5–4.5 |
| step 500，loss | 降到 2.5 以下 |
| step 3000，loss | 降到 1.5 以下 |
| 生成 20 字 | 至少有幾個正確的詩句片段出現 |

## 自我檢核

- [ ] 語料清洗後只剩中文字，vocab_size 正確
- [ ] 模型參數量計算正確（約 500K–1M）
- [ ] 訓練 3000 步，loss 降到 1.5 以下
- [ ] 生成的文字有詩句的感覺，不是亂碼

→ [Ch 13 模型格式：safetensors / GGUF 是什麼](./13-model-formats.md)
