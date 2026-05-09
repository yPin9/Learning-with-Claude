# Ch 12 — GPT 架構：decoder-only 怎麼生成文字

> 目標：把 Part 2 學到的所有零件串起來，完整實作一個可訓練、可生成的 GPT 模型。

## GPT 家族的架構選擇

原始 Transformer 論文（Vaswani 2017）有 encoder 和 decoder 兩個部分。後來發現：

- **Encoder-only**（BERT）：適合分類、問答、NLI，不擅長生成
- **Encoder-Decoder**（T5）：適合 seq2seq，翻譯、摘要
- **Decoder-only**（GPT）：適合生成，而且「什麼任務都能生成」

GPT / Llama / Mistral / Qwen 全是 decoder-only。理由很實際：只需要維護一種架構，訓練目標是 next-token prediction，scaling 起來效果驚人。

## Decoder-only 的核心：因果自注意力

唯一的架構要求：每個位置**只能看到自己和之前的位置**，不能看到未來。這透過下三角因果遮罩實現（Ch 9 介紹過了）。

## 完整 GPT 實作

```python
import torch
import torch.nn as nn
import torch.nn.functional as F
import math

class CausalSelfAttention(nn.Module):
    def __init__(self, d_model, num_heads, max_seq_len, dropout=0.1):
        super().__init__()
        assert d_model % num_heads == 0
        self.num_heads = num_heads
        self.d_head    = d_model // num_heads

        self.qkv  = nn.Linear(d_model, 3 * d_model, bias=False)
        self.proj = nn.Linear(d_model, d_model, bias=False)
        self.drop = nn.Dropout(dropout)

        # 預先建立因果遮罩（register_buffer：不是參數，但會隨模型 save/load）
        self.register_buffer(
            "mask",
            torch.tril(torch.ones(max_seq_len, max_seq_len))
        )

    def forward(self, x):
        B, T, C = x.shape
        H, D = self.num_heads, self.d_head

        # QKV 一次計算，再切開
        q, k, v = self.qkv(x).split(C, dim=2)
        q = q.view(B, T, H, D).transpose(1, 2)  # [B, H, T, D]
        k = k.view(B, T, H, D).transpose(1, 2)
        v = v.view(B, T, H, D).transpose(1, 2)

        scores = q @ k.transpose(-2, -1) / math.sqrt(D)
        scores = scores.masked_fill(self.mask[:T, :T] == 0, float('-inf'))
        weights = self.drop(F.softmax(scores, dim=-1))

        out = (weights @ v).transpose(1, 2).contiguous().view(B, T, C)
        return self.proj(out)


class GPTBlock(nn.Module):
    def __init__(self, d_model, num_heads, max_seq_len, dropout=0.1):
        super().__init__()
        self.ln1  = nn.LayerNorm(d_model)
        self.ln2  = nn.LayerNorm(d_model)
        self.attn = CausalSelfAttention(d_model, num_heads, max_seq_len, dropout)
        self.ff   = nn.Sequential(
            nn.Linear(d_model, 4 * d_model),
            nn.GELU(),
            nn.Linear(4 * d_model, d_model),
            nn.Dropout(dropout),
        )

    def forward(self, x):
        x = x + self.attn(self.ln1(x))
        x = x + self.ff(self.ln2(x))
        return x


class GPT(nn.Module):
    def __init__(self, vocab_size, d_model, num_heads, num_layers, max_seq_len, dropout=0.1):
        super().__init__()
        self.max_seq_len = max_seq_len
        self.tok_emb = nn.Embedding(vocab_size, d_model)
        self.pos_emb = nn.Embedding(max_seq_len, d_model)
        self.drop    = nn.Dropout(dropout)
        self.blocks  = nn.ModuleList([
            GPTBlock(d_model, num_heads, max_seq_len, dropout)
            for _ in range(num_layers)
        ])
        self.ln_f    = nn.LayerNorm(d_model)
        self.head    = nn.Linear(d_model, vocab_size, bias=False)

        # Tied embedding
        self.head.weight = self.tok_emb.weight

        # 初始化（按 GPT-2 的方式）
        self.apply(self._init_weights)

    def _init_weights(self, module):
        if isinstance(module, nn.Linear):
            nn.init.normal_(module.weight, mean=0.0, std=0.02)
            if module.bias is not None:
                nn.init.zeros_(module.bias)
        elif isinstance(module, nn.Embedding):
            nn.init.normal_(module.weight, mean=0.0, std=0.02)

    def forward(self, idx, targets=None):
        B, T = idx.shape
        assert T <= self.max_seq_len

        pos = torch.arange(T, device=idx.device)
        x   = self.drop(self.tok_emb(idx) + self.pos_emb(pos))

        for block in self.blocks:
            x = block(x)
        x = self.ln_f(x)
        logits = self.head(x)  # [B, T, vocab_size]

        if targets is not None:
            # 計算 cross-entropy loss（把 [B,T,V] 和 [B,T] 拉平）
            loss = F.cross_entropy(logits.view(-1, logits.size(-1)), targets.view(-1))
            return logits, loss
        return logits

    @torch.no_grad()
    def generate(self, idx, max_new_tokens, temperature=1.0, top_k=None):
        for _ in range(max_new_tokens):
            # 如果序列超過 max_seq_len，只取最後一段
            idx_cond = idx if idx.size(1) <= self.max_seq_len else idx[:, -self.max_seq_len:]
            logits = self(idx_cond)
            logits = logits[:, -1, :] / temperature  # 只取最後一個位置

            if top_k is not None:
                v, _ = torch.topk(logits, top_k)
                logits[logits < v[:, [-1]]] = float('-inf')

            probs     = F.softmax(logits, dim=-1)
            next_tok  = torch.multinomial(probs, num_samples=1)
            idx       = torch.cat([idx, next_tok], dim=1)
        return idx
```

## 試跑一個 nano 版本

```python
# nanoGPT 等級：幾萬個參數，CPU 上幾秒能跑
model = GPT(
    vocab_size  = 256,   # character-level，ASCII 範圍
    d_model     = 64,
    num_heads   = 4,
    num_layers  = 4,
    max_seq_len = 128,
)

total = sum(p.numel() for p in model.parameters())
print(f"參數量：{total:,}")  # 約 200K

# 前向傳播測試
ids     = torch.randint(0, 256, (2, 32))
targets = torch.randint(0, 256, (2, 32))
logits, loss = model(ids, targets)
print(f"logits: {logits.shape}")  # [2, 32, 256]
print(f"loss:   {loss.item():.4f}")  # 應該接近 log(256) ≈ 5.54（隨機初始化）

# 生成測試
prompt = torch.zeros(1, 1, dtype=torch.long)  # 以 token 0 開始
output = model.generate(prompt, max_new_tokens=20, temperature=0.8)
print(output.shape)  # [1, 21]
```

## 為什麼 loss 初始應該接近 log(vocab_size)

隨機初始化時，模型對所有 token 的機率接近均勻分布（1/vocab_size），cross-entropy 是 -log(1/vocab_size) = log(vocab_size)。

- vocab_size=256：初始 loss ≈ 5.54
- vocab_size=50257：初始 loss ≈ 10.82

如果初始 loss 遠低於這個值，說明初始化有問題（模型過早偏向某些 token）。

## 常見的配置表

| 名稱 | d_model | num_heads | num_layers | 參數量 |
|------|---------|-----------|------------|--------|
| nanoGPT | 64 | 4 | 4 | ~200K |
| GPT-2 small | 768 | 12 | 12 | 117M |
| GPT-2 medium | 1024 | 16 | 24 | 345M |
| Llama 3 8B | 4096 | 32 | 32 | 8B |

## 動手練習

跑一個完整的 character-level 訓練（10 分鐘的 preview）：

```python
# 用一小段中文文本測試
text   = "春眠不覺曉處處聞啼鳥夜來風雨聲花落知多少" * 100
chars  = sorted(set(text))
stoi   = {c: i for i, c in enumerate(chars)}
itos   = {i: c for i, c in enumerate(chars)}
encode = lambda s: [stoi[c] for c in s]
decode = lambda l: ''.join([itos[i] for i in l])

data    = torch.tensor(encode(text), dtype=torch.long)
model   = GPT(vocab_size=len(chars), d_model=32, num_heads=2, num_layers=2, max_seq_len=64)
optim   = torch.optim.AdamW(model.parameters(), lr=1e-3)

for step in range(500):
    i       = torch.randint(0, len(data) - 65, (8,))
    x       = torch.stack([data[j:j+64] for j in i])
    y       = torch.stack([data[j+1:j+65] for j in i])
    _, loss = model(x, y)
    optim.zero_grad(); loss.backward(); optim.step()
    if step % 100 == 0:
        print(f"step {step}: loss={loss.item():.3f}")

# 生成
prompt = torch.tensor([[stoi['春']]])
out    = model.generate(prompt, max_new_tokens=30, temperature=0.8)
print(decode(out[0].tolist()))
```

## 自我檢核

- [ ] 能解釋 decoder-only 和 encoder-decoder 的使用場景差異
- [ ] 理解為什麼初始 loss 應該接近 log(vocab_size)
- [ ] 跑過 nanoGPT 的 character-level 訓練，看到 loss 下降
- [ ] 能讀懂完整 `GPT` 類別的每一行

現在你有一個完整的、可訓練的 GPT 實作了。下一步：把 Part 1-2 學到的東西拼成一個實際的練習。

→ [練習 A：用純 PyTorch 從頭實作 tiny Transformer](./practice-a-tiny-transformer.md)
