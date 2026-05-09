# Ch 8 — Embedding：把詞變成向量

> 目標：理解 embedding 為什麼必要、embedding 表存在哪、以及語義如何被編碼進向量空間。

## 電腦不懂文字，只懂數字

模型的輸入是 token id（整數），但整數沒有意義——「3」和「4」的差距和「貓」和「狗」的差距完全無關。

Embedding 層做的事：把整數 id 對應到一個**有意義的向量**。

```
token id → embedding 向量
   3     →  [0.12, -0.45, 0.88, ..., 0.03]  (d_model 維)
```

這個映射是**可學習的**——訓練過程中，向量的值會自動調整，讓語意相近的詞在向量空間裡靠近。

## Embedding 表是一個查表操作

```python
import torch
import torch.nn as nn

vocab_size = 1000    # 字典裡有 1000 個 token
d_model    = 64      # 每個 token 用 64 維向量表示

embedding = nn.Embedding(vocab_size, d_model)
# 這是一個 [1000, 64] 的矩陣

# 查詢：給 token id，拿向量
token_ids = torch.tensor([3, 7, 3, 15])
vecs = embedding(token_ids)
print(vecs.shape)  # [4, 64]
print(vecs[0])     # token id=3 的向量
print(vecs[2])     # token id=3 的向量（和 vecs[0] 完全相同）
```

同一個 token id，不管出現在序列哪個位置，拿到的向量都一樣。位置資訊是另外加的（Ch 10 會講）。

## 語義被編碼在向量空間裡

訓練完成後，embedding 向量有一個有名的性質：

```
向量("國王") - 向量("男人") + 向量("女人") ≈ 向量("女王")
```

這不是人工設計的，是訓練過程自然湧現的。模型發現「描述皇室地位」可以用某個方向表示，「描述性別」可以用另一個方向，這樣預測下一個 token 的 loss 最低。

類似的例子：
- 「台北」「東京」「巴黎」的向量都靠近「首都」的方向
- 「跑」「跳」「走」的向量都在動詞的語義區
- 正面情感詞（「美好」「快樂」）和負面情感詞（「糟糕」「悲傷」）分布在不同區域

## Embedding 的維度怎麼選

| 模型 | d_model | 參數 |
|------|---------|------|
| GPT-2 small | 768 | 117M |
| GPT-2 large | 1280 | 774M |
| Llama 3 8B | 4096 | 8B |
| Llama 3 70B | 8192 | 70B |

維度越大，能表達的語義越細緻，但參數量也更大。embedding 矩陣的參數量是 `vocab_size × d_model`——對 Llama 3 來說是 128000 × 4096 ≈ 5 億個參數，光 embedding 就佔了總參數的 6%。

## 兩個 embedding：token embedding + positional embedding

標準 GPT 模型有兩個 embedding 加在一起：

```python
class InputEmbedding(nn.Module):
    def __init__(self, vocab_size, d_model, max_seq_len):
        super().__init__()
        self.token_emb = nn.Embedding(vocab_size, d_model)
        self.pos_emb   = nn.Embedding(max_seq_len, d_model)

    def forward(self, token_ids):
        # token_ids: [batch, seq_len]
        seq_len = token_ids.shape[1]
        positions = torch.arange(seq_len, device=token_ids.device)

        tok = self.token_emb(token_ids)   # [batch, seq_len, d_model]
        pos = self.pos_emb(positions)     # [seq_len, d_model]
        return tok + pos                   # 廣播加法
```

位置 embedding 告訴模型「這個 token 在序列的第幾個位置」。現代 LLM 大多改用 RoPE（旋轉位置編碼）取代這種固定的位置 embedding，Ch 10 會細講。

## Tied Embedding（重用 embedding）

LLM 的最後一層（LM Head）把 d_model 維向量投影回 vocab_size：

```
hidden_state [batch, seq, d_model] → logits [batch, seq, vocab_size]
```

這個投影矩陣的形狀也是 `[vocab_size, d_model]`，和 token embedding 完全相同。

很多模型（GPT-2、Llama）會讓這兩個矩陣共享同一份權重（tied embedding），節省參數，而且實驗上效果更好——因為「什麼是好的輸出 token」和「什麼是好的輸入表示」在某種程度上是對稱的。

## 動手練習

訓練一個最小的語言模型，觀察 embedding 向量如何在訓練中變化：

```python
import torch
import torch.nn as nn

vocab_size = 20
d_model    = 4  # 故意很小，方便觀察

emb = nn.Embedding(vocab_size, d_model)
# 看初始化狀態
print("初始 embedding for token 3:", emb.weight[3].data)

# 做一次假的訓練步驟
optimizer = torch.optim.Adam(emb.parameters(), lr=0.1)
fake_input  = torch.tensor([3, 5, 7])
fake_target = torch.tensor([5, 7, 2])  # 假設這是正確的下一個 token

logits = emb(fake_input) @ emb.weight.T  # 簡化的 LM head
loss   = nn.CrossEntropyLoss()(logits, fake_target)
loss.backward()
optimizer.step()

print("一步之後 embedding for token 3:", emb.weight[3].data)
# 數值有改變——梯度更新了 embedding 表
```

## 自我檢核

- [ ] 能解釋 embedding 為什麼不用 one-hot encoding
- [ ] 知道 token embedding 和 positional embedding 的用途
- [ ] 理解 tied embedding 是什麼，為什麼用
- [ ] 跑過上面的練習，觀察到 embedding 在訓練中更新

→ [Ch 9 Attention：讓模型看全句](./09-attention.md)
