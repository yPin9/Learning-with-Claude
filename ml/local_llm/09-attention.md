# Ch 9 — Attention：讓模型看全句

> 目標：從零推導出 scaled dot-product attention，搞清楚 Q/K/V 分別在做什麼。

## 為什麼需要 Attention

在 RNN 時代，處理序列的方式是一個 token 一個 token 依序處理，用隱藏狀態傳遞「記憶」。

問題：序列很長時，早期 token 的資訊在傳遞中逐漸衰減。「那本書——我三年前在圖書館借的、主題是關於心理學的、作者是日本人——**它**讓我改變了對人際關係的看法」，這句話裡「它」指「那本書」，RNN 很難在這麼長的距離後還記得。

Attention 的解法：讓每個 token 直接「看」序列中所有其他位置，距離不是問題。

## 一個直覺的類比

假設你在查字典，想查「蘋果」這個詞的意思：

1. 你手上有一個**查詢（Query）**：「蘋果」
2. 字典裡每個詞條都有一個**鍵（Key）**用來比對
3. 每個詞條有對應的**值（Value）**，也就是定義

Attention 就是：用 Query 和所有 Key 比對，算相似度（attention score），再用相似度加權平均所有 Value。

## Scaled Dot-Product Attention 推導

給定輸入序列 X（形狀 `[seq_len, d_model]`），做三個線性投影：

```
Q = X · Wq    # Queries  [seq_len, d_k]
K = X · Wk    # Keys     [seq_len, d_k]
V = X · Wv    # Values   [seq_len, d_v]
```

計算 attention：

```
scores   = Q · Kᵀ / √d_k        # [seq_len, seq_len]
weights  = softmax(scores)        # [seq_len, seq_len]，每行和為 1
output   = weights · V            # [seq_len, d_v]
```

步驟解釋：
- `Q · Kᵀ`：每個 query 和所有 key 做內積，得到相似度分數
- `/ √d_k`：縮放，防止維度太高時內積值太大，導致 softmax 梯度消失
- `softmax`：把分數轉成機率（attention weight）
- `weights · V`：加權平均所有 value

## 用 PyTorch 寫出來

```python
import torch
import torch.nn as nn
import torch.nn.functional as F
import math

def attention(Q, K, V, mask=None):
    """
    Q, K: [batch, seq, d_k]
    V:    [batch, seq, d_v]
    """
    d_k = Q.shape[-1]

    # 計算相似度分數
    scores = Q @ K.transpose(-2, -1) / math.sqrt(d_k)
    # scores: [batch, seq, seq]

    # 因果遮罩（causal mask）：不讓 token 看到未來的 token
    if mask is not None:
        scores = scores.masked_fill(mask == 0, float('-inf'))

    # softmax 得到 attention weight
    weights = F.softmax(scores, dim=-1)

    # 加權平均 V
    output = weights @ V
    return output, weights

# 測試
batch, seq_len, d_k = 2, 5, 64
Q = torch.randn(batch, seq_len, d_k)
K = torch.randn(batch, seq_len, d_k)
V = torch.randn(batch, seq_len, d_k)

out, w = attention(Q, K, V)
print(out.shape)   # [2, 5, 64]
print(w.shape)     # [2, 5, 5]  ← 每個 token 對其他 token 的注意力
print(w[0, 0])     # 第一個 token 對所有 token 的注意力（和為 1）
```

## 因果遮罩（Causal Mask）

GPT 是 decoder-only 模型，訓練時預測下一個 token，不能讓 token 看到「未來」的 token（否則等於直接抄答案）：

```python
# 下三角遮罩：第 i 個位置只能看到第 0..i 個位置
seq_len = 5
mask = torch.tril(torch.ones(seq_len, seq_len))
print(mask)
# tensor([[1., 0., 0., 0., 0.],
#         [1., 1., 0., 0., 0.],
#         [1., 1., 1., 0., 0.],
#         [1., 1., 1., 1., 0.],
#         [1., 1., 1., 1., 1.]])
```

被遮住的位置（值為 0）在 `masked_fill` 後變成 `-inf`，softmax 後對應的 attention weight 趨近 0，等效於「看不到」。

## 為什麼 `/ √d_k` 很重要

不縮放的話，d_k 很大時內積值會很大，softmax 輸出趨近 one-hot——幾乎所有注意力集中在一個位置，梯度消失，訓練不動。

```python
# 示範：d_k=1 vs d_k=64 的 softmax 行為
import torch

q = torch.randn(64)
k = torch.randn(64, 64)

scores_unscaled = q @ k.T           # max 可能到 30+
scores_scaled   = q @ k.T / 8.0    # 除以 √64=8

print("unscaled:", torch.softmax(scores_unscaled, dim=0).max().item())
# 可能接近 1.0，即注意力集中在一個位置
print("scaled:  ", torch.softmax(scores_scaled,   dim=0).max().item())
# 較分散
```

## Self-Attention vs Cross-Attention

**Self-attention**：Q、K、V 都來自同一個序列（Transformer encoder 和 GPT 用這個）。

**Cross-attention**：Q 來自一個序列，K/V 來自另一個序列（Transformer encoder-decoder 用這個，例如機器翻譯）。

GPT 只用 self-attention。

## Attention 的計算複雜度

標準 attention 的複雜度是 O(seq_len²)，這是現代 LLM 的主要瓶頸——序列長度翻倍，計算量變四倍。FlashAttention（Ch 31）用 kernel fusion 解決了記憶體瓶頸，但數學上仍然是 O(n²)。

## 動手練習

視覺化 attention weight：

```python
import torch
import torch.nn.functional as F
import math
import matplotlib.pyplot as plt

# 造一個短句的 embedding（用隨機值示意）
seq = ["台", "灣", "是", "美", "麗", "的"]
seq_len = len(seq)
d_k = 16

torch.manual_seed(42)
Q = torch.randn(seq_len, d_k)
K = torch.randn(seq_len, d_k)

scores = Q @ K.T / math.sqrt(d_k)
weights = F.softmax(scores, dim=-1)

plt.figure(figsize=(6,5))
plt.imshow(weights.detach().numpy(), cmap='Blues')
plt.xticks(range(seq_len), seq)
plt.yticks(range(seq_len), seq)
plt.colorbar()
plt.title("Attention Weights（隨機初始化，僅示意）")
plt.savefig("attention_weights.png")
```

## 自我檢核

- [ ] 能從 QKV 角度解釋 attention 做了什麼
- [ ] 知道 `/ √d_k` 的原因
- [ ] 理解因果遮罩的作用和實作方式
- [ ] 手寫過 `attention()` 函數並跑通

→ [Ch 10 Multi-head Attention + 位置編碼](./10-multihead-attention-and-pe.md)
