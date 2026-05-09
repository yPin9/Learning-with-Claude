# Ch 10 — Multi-head Attention + 位置編碼

> 目標：理解為什麼需要多個 attention head，以及 RoPE 如何把位置資訊注入向量。

## 單頭 attention 的問題

Ch 9 的 attention 一次只能學一種「注意什麼」的模式。但語言裡同時存在多種關係：

- 語法依存（動詞注意主語）
- 指代消解（代詞注意所指詞）
- 語義相似（同義詞互相注意）

讓模型同時學多種模式，答案是 **Multi-head Attention（MHA）**。

## Multi-head Attention

把 d_model 維分成 `num_heads` 份，每份跑獨立的 attention，再拼起來：

```
d_head = d_model / num_heads

head_i = Attention(Q·Wqᵢ, K·Wkᵢ, V·Wvᵢ)   每個 head 用各自的投影矩陣

MultiHead = concat(head_1, ..., head_h) · Wo
```

```python
import torch
import torch.nn as nn
import torch.nn.functional as F
import math

class MultiHeadAttention(nn.Module):
    def __init__(self, d_model, num_heads):
        super().__init__()
        assert d_model % num_heads == 0
        self.d_model    = d_model
        self.num_heads  = num_heads
        self.d_head     = d_model // num_heads

        self.Wq = nn.Linear(d_model, d_model, bias=False)
        self.Wk = nn.Linear(d_model, d_model, bias=False)
        self.Wv = nn.Linear(d_model, d_model, bias=False)
        self.Wo = nn.Linear(d_model, d_model, bias=False)

    def forward(self, x, mask=None):
        B, T, C = x.shape  # batch, seq_len, d_model

        # 投影，然後切成多個 head
        Q = self.Wq(x).view(B, T, self.num_heads, self.d_head).transpose(1, 2)
        K = self.Wk(x).view(B, T, self.num_heads, self.d_head).transpose(1, 2)
        V = self.Wv(x).view(B, T, self.num_heads, self.d_head).transpose(1, 2)
        # Q, K, V: [B, num_heads, T, d_head]

        # Scaled dot-product attention
        scores = Q @ K.transpose(-2, -1) / math.sqrt(self.d_head)
        if mask is not None:
            scores = scores.masked_fill(mask == 0, float('-inf'))
        weights = F.softmax(scores, dim=-1)

        out = weights @ V   # [B, num_heads, T, d_head]

        # 合併所有 head
        out = out.transpose(1, 2).contiguous().view(B, T, C)
        return self.Wo(out)

# 測試
mha = MultiHeadAttention(d_model=64, num_heads=8)
x   = torch.randn(2, 10, 64)   # batch=2, seq=10, d_model=64
out = mha(x)
print(out.shape)  # [2, 10, 64]
```

## 位置編碼：為什麼需要它

Attention 本身是**順序無關的**——把序列打亂，output 的值也只是對應行被交換，模型看不出順序。

但語言的意思強烈依賴順序：「狗咬人」和「人咬狗」意思截然不同。

位置編碼（Positional Encoding）把位置資訊注入表示。

## 方法一：絕對位置 Embedding（GPT-2）

最直覺：每個位置有一個可學習的向量：

```python
pos_emb = nn.Embedding(max_seq_len, d_model)
x = token_emb(ids) + pos_emb(positions)
```

缺點：只能處理 `max_seq_len` 以內的序列，超過就無法外推。

## 方法二：Sinusoidal 位置編碼（原版 Transformer）

用固定的正弦/餘弦函數，不同頻率對應不同維度：

```python
import torch
import math

def sinusoidal_pe(max_len, d_model):
    pe = torch.zeros(max_len, d_model)
    pos = torch.arange(max_len).unsqueeze(1)  # [max_len, 1]
    div = torch.exp(torch.arange(0, d_model, 2) * -(math.log(10000.0) / d_model))
    pe[:, 0::2] = torch.sin(pos * div)  # 偶數維度用 sin
    pe[:, 1::2] = torch.cos(pos * div)  # 奇數維度用 cos
    return pe  # [max_len, d_model]
```

優點：可以外推（雖然效果不一定好），不需要學習。

## 方法三：RoPE（旋轉位置編碼）——現代 LLM 標配

Llama / Mistral / Qwen 全都用 RoPE。核心想法：不是把位置資訊加到向量上，而是**旋轉** Q 和 K 向量，讓內積自然包含相對位置資訊。

數學原理：對 2D 向量做旋轉：

```
[q_0, q_1] 旋轉 θ_pos 角度 → [q_0·cos(θ) - q_1·sin(θ), q_0·sin(θ) + q_1·cos(θ)]
```

對 d_model 維的向量，兩兩一組各自旋轉，不同維度用不同頻率：

```python
def apply_rotary_emb(x, cos, sin):
    # x: [B, num_heads, T, d_head]
    x1 = x[..., :x.shape[-1]//2]   # 前半維度
    x2 = x[..., x.shape[-1]//2:]   # 後半維度
    rotated = torch.cat([-x2, x1], dim=-1)
    return x * cos + rotated * sin
```

RoPE 的優點：
- 相對位置關係自然出現（Q·K 的結果只依賴相對位移，不依賴絕對位置）
- 可以外推到比訓練時更長的序列（搭配 YaRN 等技術）
- 不增加額外參數

## Llama 3 的超參數作為參考

| 超參數 | 8B 版本 | 備注 |
|--------|---------|------|
| d_model | 4096 | hidden size |
| num_heads | 32 | attention heads |
| d_head | 128 | = 4096/32 |
| num_layers | 32 | Transformer block 層數 |
| GQA kv_heads | 8 | Grouped Query Attention |

**Grouped Query Attention（GQA）**：Llama 3 用的優化——K/V 只有 8 個 head，Q 有 32 個，4 個 Q head 共用 1 個 K/V head，大幅減少推論時的 KV cache 記憶體。

## 動手練習

改造 Ch 9 的單頭 attention，加上因果遮罩，確認形狀正確：

```python
mha = MultiHeadAttention(d_model=32, num_heads=4)
x = torch.randn(1, 6, 32)

# 生成因果遮罩
T = x.shape[1]
mask = torch.tril(torch.ones(T, T)).unsqueeze(0).unsqueeze(0)  # [1,1,T,T]

out = mha(x, mask=mask)
print(out.shape)  # [1, 6, 32]

# 確認：移掉遮罩，輸出不一樣（因為看到了未來的 token）
out_nomask = mha(x, mask=None)
print(torch.allclose(out, out_nomask))  # False
```

## 自我檢核

- [ ] 能解釋為什麼需要多個 head
- [ ] 知道絕對位置 embedding 和 RoPE 的根本差異
- [ ] 理解 GQA 為什麼省記憶體
- [ ] 跑過 MHA，驗證因果遮罩改變了輸出

→ [Ch 11 Transformer Block：FFN / Residual / LayerNorm](./11-transformer-block.md)
