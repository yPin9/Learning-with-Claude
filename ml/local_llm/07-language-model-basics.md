# Ch 7 — 語言模型是什麼：next-token prediction

> 目標：理解語言模型的數學定義，以及「預測下一個 token」如何成為通用的智能訓練方式。

## 語言模型的定義

語言模型是一個**給文字序列賦予機率**的系統：

```
P("今天天氣很好") = ?
P("今天氣天很好") = ?  ← 這個機率應該更低
```

用條件機率展開：

```
P(w₁, w₂, ..., wₙ) = P(w₁) × P(w₂|w₁) × P(w₃|w₁,w₂) × ...
                    = ∏ P(wᵢ | w₁,...,wᵢ₋₁)
```

每個 token 的出現機率，取決於它前面所有 token。訓練語言模型，就是學習這些條件機率。

## Next-Token Prediction 為什麼有用

「預測下一個字」看起來很簡單，但要做好它，模型必須學到：

- **語法**：「我在吃」之後接名詞而非動詞
- **語意**：「巴黎是法國的」之後接「首都」而非「海洋」
- **知識**：「愛因斯坦是」之後接「物理學家」
- **推理**：「如果明天下雨，我就」之後接邏輯上一致的後文

這些全都是為了預測下一個 token 而被迫學習的副產品。這個發現——一個自監督任務湧現出廣泛能力——是現代 LLM 成功的核心。

## 訓練資料是免費的

和分類任務需要人工標注不同，next-token prediction 的訓練資料**就是文字本身**：

```
輸入序列：["今天", "天氣", "很"]
目標：    ["天氣", "很",   "好"]
```

網路上所有的文字都可以直接拿來用，這就是為什麼 LLM 能用幾兆個 token 訓練——訓練資料本質上是無限的。

## Token vs 字

中文一個字在 GPT-2 的 tokenizer 裡約對應 2 個 token（因為是用 UTF-8 bytes 編碼的），英文的常見單字通常是 1 個 token：

```python
import tiktoken
enc = tiktoken.get_encoding("gpt2")

# 英文單字
print(enc.encode("hello"))      # [31373]        ← 1 token
print(enc.encode("running"))    # [20270]         ← 1 token
print(enc.encode("unbelievable"))  # [403, 2197, 18222]  ← 3 tokens

# 中文
print(enc.encode("你好"))       # [19526, 254, 22755, 238]  ← 4 tokens
print(enc.encode("台灣"))       # [32573, 243, 37955]       ← 3 tokens
```

Vocab size（字典大小）決定了 softmax 輸出的維度。GPT-2 是 50257，Llama 3 是 128000。

## 生成文字：採樣策略

訓練好模型後，生成文字是反覆採樣的過程：

```python
def generate(model, tokenizer, prompt, max_new_tokens=50, temperature=1.0):
    tokens = tokenizer.encode(prompt)
    tokens = torch.tensor(tokens).unsqueeze(0)  # [1, seq_len]

    for _ in range(max_new_tokens):
        with torch.no_grad():
            logits = model(tokens)          # [1, seq_len, vocab_size]
        next_logits = logits[0, -1, :]      # 最後一個位置的 logits

        # Temperature scaling：控制隨機程度
        next_logits = next_logits / temperature

        probs = torch.softmax(next_logits, dim=-1)
        next_token = torch.multinomial(probs, 1)  # 採樣

        tokens = torch.cat([tokens, next_token.unsqueeze(0)], dim=1)

    return tokenizer.decode(tokens[0].tolist())
```

**Temperature** 控制隨機程度：
- `temperature=0.0`：永遠選最高機率（貪婪解碼，輸出確定）
- `temperature=1.0`：按機率採樣（原始分布）
- `temperature=2.0`：更隨機，可能出現奇怪的組合

## Top-k 和 Top-p 採樣

光用 temperature 還不夠，實務上常加 top-k 或 top-p：

```python
# Top-k：只從機率最高的 k 個 token 裡採樣
def top_k_sample(logits, k=50):
    values, indices = torch.topk(logits, k)
    logits_filtered = torch.full_like(logits, float('-inf'))
    logits_filtered.scatter_(0, indices, values)
    return torch.softmax(logits_filtered, dim=-1)

# Top-p（nucleus sampling）：從累積機率達到 p 的最小 token 集合裡採樣
# 比 top-k 更動態，是目前最常用的策略
```

Ollama 預設用 top-p=0.9、temperature=0.8，這個組合對大多數用途夠用。

## 語言模型不是「理解」文字

一個重要的認知校正：語言模型不「理解」文字的意思，它學到的是 token 序列的統計規律。這不是貶低，是事實。

這個區別在工程上很重要：

- 模型可以輸出聽起來很有道理的謊言（因為它只管「像什麼」，不管「是否為真」）
- 提示工程的本質是在利用統計規律
- fine-tuning 是在修改統計規律

## 動手練習

用 tiktoken 探索 tokenization 的行為，找出「貴」的 token：

```python
import tiktoken
enc = tiktoken.get_encoding("gpt2")

words = ["AI", "人工智慧", "transformer", "台灣", "semiconductor", "半導體"]
for w in words:
    ids = enc.encode(w)
    print(f"{w!r:20s} → {len(ids)} tokens: {ids}")

# 思考：為什麼中文比英文貴？這對模型有什麼影響？
```

## 自我檢核

- [ ] 能用條件機率寫出語言模型的數學定義
- [ ] 知道為什麼 next-token prediction 能湧現出廣泛能力
- [ ] 理解 temperature 對生成結果的影響
- [ ] 跑過 tokenization 實驗，感受中英文的 token 差距

→ [Ch 8 Embedding：把詞變成向量](./08-embeddings.md)
