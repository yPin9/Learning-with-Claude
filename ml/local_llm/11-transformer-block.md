# Ch 11 — Transformer Block：FFN / Residual / LayerNorm

> 目標：把 MHA、FFN、殘差連接、LayerNorm 組裝成一個完整的 Transformer Block。

## Block 的全貌

一個 Transformer Block 只有兩個子層：

```
input x
   │
   ├─ LayerNorm(x) → Multi-Head Attention → + x  ← 殘差連接
   │                                        │
   └─────────────────────────────────────── │
                                            │
                                     LayerNorm → FFN → + x  ← 殘差連接
                                                         │
                                                       output
```

這是 Pre-Norm 版本（現代 LLM 的標準）。N 個這樣的 block 堆疊，就是整個 Transformer 的主體。

## FFN：兩層線性 + 激活函數

Feed-Forward Network（FFN）每個位置獨立計算，沒有跨位置的互動（那是 attention 的工作）：

```
FFN(x) = activation(x · W₁ + b₁) · W₂ + b₂
```

```python
import torch.nn as nn
import torch.nn.functional as F

class FeedForward(nn.Module):
    def __init__(self, d_model, expansion_factor=4):
        super().__init__()
        d_ff = d_model * expansion_factor  # 通常是 4x
        self.w1 = nn.Linear(d_model, d_ff, bias=False)
        self.w2 = nn.Linear(d_ff, d_model, bias=False)

    def forward(self, x):
        return self.w2(F.gelu(self.w1(x)))
```

FFN 的參數量：`2 × d_model × d_ff`。對 Llama 3 8B 來說，d_model=4096，d_ff=14336，一層 FFN 就有 2 × 4096 × 14336 ≈ 1.17 億參數。32 層加起來，FFN 佔了整個模型參數的大頭。

## SwiGLU：現代 LLM 的 FFN 變體

Llama 用的不是普通 FFN，而是 SwiGLU：

```python
class SwiGLUFeedForward(nn.Module):
    def __init__(self, d_model, d_ff):
        super().__init__()
        self.w1 = nn.Linear(d_model, d_ff, bias=False)
        self.w2 = nn.Linear(d_ff, d_model, bias=False)
        self.w3 = nn.Linear(d_model, d_ff, bias=False)

    def forward(self, x):
        # gate 機制：用 w3 的輸出控制 w1 輸出的通過量
        return self.w2(F.silu(self.w1(x)) * self.w3(x))
```

SwiGLU 有個 gate 分支，實驗上效果比 GELU FFN 好一點。

## 殘差連接：深層網路能跑起來的關鍵

殘差連接（Residual Connection）讓梯度直接流回淺層，解決了深層網路的梯度消失問題：

```python
# 有殘差連接
output = x + sublayer(LayerNorm(x))

# 沒有殘差連接
output = sublayer(LayerNorm(x))
# 這樣梯度必須穿過每一層才能回到第一層，100層就消失光了
```

直覺：殘差連接讓每一層只需要學「怎麼在現有表示上做修正」，而不是「從頭建立表示」。就算某些層什麼都不做（輸出接近 0），整個模型也能正常運作。

## 組裝完整的 Block

```python
import torch
import torch.nn as nn

class TransformerBlock(nn.Module):
    def __init__(self, d_model, num_heads, expansion_factor=4, dropout=0.1):
        super().__init__()
        self.norm1  = nn.LayerNorm(d_model)
        self.norm2  = nn.LayerNorm(d_model)
        self.attn   = MultiHeadAttention(d_model, num_heads)  # 來自 Ch 10
        self.ff     = FeedForward(d_model, expansion_factor)
        self.drop   = nn.Dropout(dropout)

    def forward(self, x, mask=None):
        # Attention 子層 + 殘差
        x = x + self.drop(self.attn(self.norm1(x), mask))
        # FFN 子層 + 殘差
        x = x + self.drop(self.ff(self.norm2(x)))
        return x
```

## RMSNorm：Llama 用的 LayerNorm 變體

標準 LayerNorm 計算均值和方差：

```
LayerNorm(x) = (x - mean) / std × γ + β
```

RMSNorm 只計算 RMS（均方根），省去減均值的步驟，速度更快，效果幾乎一樣：

```python
class RMSNorm(nn.Module):
    def __init__(self, d_model, eps=1e-6):
        super().__init__()
        self.gamma = nn.Parameter(torch.ones(d_model))
        self.eps   = eps

    def forward(self, x):
        rms = x.pow(2).mean(-1, keepdim=True).add(self.eps).sqrt()
        return self.gamma * x / rms
```

## 堆疊多個 Block

```python
class TransformerModel(nn.Module):
    def __init__(self, vocab_size, d_model, num_heads, num_layers, max_seq_len):
        super().__init__()
        self.token_emb = nn.Embedding(vocab_size, d_model)
        self.pos_emb   = nn.Embedding(max_seq_len, d_model)
        self.blocks    = nn.ModuleList([
            TransformerBlock(d_model, num_heads)
            for _ in range(num_layers)
        ])
        self.norm_final = nn.LayerNorm(d_model)
        self.lm_head   = nn.Linear(d_model, vocab_size, bias=False)

    def forward(self, token_ids, mask=None):
        B, T = token_ids.shape
        pos = torch.arange(T, device=token_ids.device)

        x = self.token_emb(token_ids) + self.pos_emb(pos)

        for block in self.blocks:
            x = block(x, mask)

        x = self.norm_final(x)
        logits = self.lm_head(x)   # [B, T, vocab_size]
        return logits

# GPT-2 small 等級的模型
model = TransformerModel(
    vocab_size=50257,
    d_model=768,
    num_heads=12,
    num_layers=12,
    max_seq_len=1024
)

total = sum(p.numel() for p in model.parameters())
print(f"參數量：{total/1e6:.1f}M")  # 約 117M，符合 GPT-2 small
```

## 動手練習

查看 block 的計算圖：

```python
import torch
from torchinfo import summary  # pip install torchinfo

model = TransformerModel(vocab_size=1000, d_model=64, num_heads=4, num_layers=2, max_seq_len=128)
ids = torch.randint(0, 1000, (1, 32))
summary(model, input_data=ids)
```

接著故意把 `num_layers=0`（沒有 Transformer Block），看模型退化成什麼。

## 自我檢核

- [ ] 能背出 Pre-Norm Transformer Block 的結構（Norm → Attn → Residual → Norm → FFN → Residual）
- [ ] 理解殘差連接為什麼讓深層訓練變可能
- [ ] 知道 SwiGLU 和 RMSNorm 是什麼，哪些模型用了
- [ ] 跑過 `TransformerModel`，看到參數量約 117M

→ [Ch 12 GPT 架構：decoder-only 怎麼生成文字](./12-gpt-architecture.md)
