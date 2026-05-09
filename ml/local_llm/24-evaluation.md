# Ch 24 — 評估：perplexity / 生成品質怎麼量

> 目標：能對訓練好的語言模型做有意義的評估，不只看 training loss。

## 自動評估指標

### Perplexity（困惑度）

Perplexity 是 validation loss 的指數形式，直覺上是「平均每個位置模型有幾個同等可能的選擇」：

```python
import torch
import math

@torch.no_grad()
def compute_perplexity(model, data, block_size=256, batch_size=16):
    model.eval()
    total_loss = 0
    total_tokens = 0

    for i in range(0, len(data) - block_size, block_size):
        x = data[i:i+block_size].unsqueeze(0)
        y = data[i+1:i+block_size+1].unsqueeze(0)
        _, loss = model(x, y)
        total_loss += loss.item() * block_size
        total_tokens += block_size

    avg_loss = total_loss / total_tokens
    ppl = math.exp(avg_loss)
    return ppl

ppl = compute_perplexity(model, val_data)
print(f"Perplexity: {ppl:.2f}")
```

**參考值**：
- 隨機猜測：≈ vocab_size
- 訓練幾百步的小模型：50–200
- GPT-2 124M（WebText）：29.4
- Llama 3 8B：~8–12（取決於評測集）

### BLEU Score（生成翻譯品質）

BLEU 量的是生成文字和參考答案之間的 n-gram 重疊，常用在翻譯和摘要：

```python
from nltk.translate.bleu_score import sentence_bleu, SmoothingFunction

reference = [list("床前明月光疑是地上霜")]
hypothesis = list("床前明月光疑似地上霜")  # 一字之差

score = sentence_bleu(
    reference,
    hypothesis,
    smoothing_function=SmoothingFunction().method1
)
print(f"BLEU: {score:.4f}")  # 約 0.6–0.8
```

BLEU 的問題：只看詞彙重疊，不理解意思。現在更多用 ROUGE-L 或 BERTScore。

## 語言模型的特定評估

### 生成多樣性

同一個 prompt 生成多次，看輸出是否多樣（不是每次都一樣）：

```python
def evaluate_diversity(model, tokenizer, prompt, n=5, temperature=0.8):
    outputs = []
    for _ in range(n):
        out = model.generate(prompt, max_new_tokens=50, temperature=temperature)
        text = tokenizer.decode(out[0].tolist())
        outputs.append(text)

    # 用集合大小量多樣性
    unique_outputs = len(set(outputs))
    print(f"生成 {n} 次，有 {unique_outputs} 個不同輸出")
    for i, o in enumerate(outputs):
        print(f"  [{i+1}] {o[:60]}")
```

### 重複性檢測

生成文字中出現重複片段，是模型崩潰（degeneration）的訊號：

```python
def repetition_rate(text, n=3):
    """計算 n-gram 重複率"""
    words = list(text)
    ngrams = [tuple(words[i:i+n]) for i in range(len(words)-n+1)]
    if not ngrams:
        return 0
    return 1 - len(set(ngrams)) / len(ngrams)

# 健康生成
good = "春眠不覺曉處處聞啼鳥夜來風雨聲"
bad  = "的的的的的的的的的的的的的"
print(f"好的輸出：重複率 {repetition_rate(good):.3f}")
print(f"崩潰輸出：重複率 {repetition_rate(bad):.3f}")
```

## 人工評估：不可或缺

自動指標都有盲點，最終還是要人看：

**生成品質評估表**（評分 1–5）：

| 面向 | 問題 |
|------|------|
| 流暢度 | 讀起來是否自然？有沒有明顯的語法錯誤？ |
| 相關性 | 輸出是否回應了 prompt？ |
| 準確性 | 如果涉及事實，是否正確？ |
| 多樣性 | 輸出是否有創意，還是在重複訓練資料？ |

對自己訓練的小模型，可以設計 10 個測試 prompt，每個生成 3 次，人工評分。

## 標準測試集

如果你的模型要和其他模型比較，用標準測試集：

### HellaSwag（常識推理）

```python
# 給定一個場景，選擇最合理的後續句子
# 例：「他把魚從水裡撈出來，然後...」
# A) 把魚放回水裡  B) 把魚放進籃子  C) 開始游泳  D) 打了個噴嚏

# 用語言模型計算每個選項的 log likelihood
def evaluate_hellaswag(model, tokenizer, question, choices):
    scores = []
    for choice in choices:
        text = question + " " + choice
        ids = tokenizer.encode(text)
        x = torch.tensor(ids[:-1]).unsqueeze(0)
        y = torch.tensor(ids[1:]).unsqueeze(0)
        _, loss = model(x, y)
        scores.append(-loss.item())  # 負 loss = log likelihood
    return scores.index(max(scores))  # 最高分的選項
```

### 繁體中文評估（自製）

針對繁體中文能力，可以出以下類型的題目：
1. 填空（「春眠____覺曉」）
2. 完形（給上半段詩，預測下半段）
3. 繁簡辨別（是否會混用簡體字）

## 完整評估報告模板

```python
def full_evaluation(model, tokenizer, val_data, test_prompts):
    print("=" * 50)
    print("模型評估報告")
    print("=" * 50)

    # 1. Perplexity
    ppl = compute_perplexity(model, val_data)
    print(f"\n[自動指標]")
    print(f"  Perplexity: {ppl:.2f}")

    # 2. 生成樣本
    print(f"\n[生成樣本]")
    for prompt_text, prompt_ids in test_prompts:
        out = model.generate(prompt_ids, max_new_tokens=40, temperature=0.7, top_k=20)
        generated = tokenizer.decode(out[0].tolist())
        rep = repetition_rate(generated)
        print(f"  [{prompt_text}] → {generated[:60]}")
        print(f"         重複率: {rep:.3f}")

    print("\n[人工評估] 請依據上方生成樣本評分（1-5）")
```

## 動手練習

對 Practice A 訓練的唐詩模型做完整評估：

```python
# 評估你的唐詩生成模型
test_prompts = ["床", "春", "白", "明", "山"]
print("=== 唐詩生成模型評估 ===")
for c in test_prompts:
    if c in stoi:
        prompt = torch.tensor([[stoi[c]]])
        out = model.generate(prompt, max_new_tokens=20, temperature=0.7)
        text = decode(out[0].tolist())
        print(f"  [{c}] {text}")
        print(f"      重複率: {repetition_rate(text, n=2):.3f}")

ppl = compute_perplexity(model, val_data)
print(f"\nPerplexity: {ppl:.2f}")
```

## 自我檢核

- [ ] 能手寫 perplexity 的計算（cross entropy → exp）
- [ ] 理解為什麼 perplexity 不夠，還需要人工評估
- [ ] 跑過重複率檢測，能判斷模型有沒有退化
- [ ] 對自己訓練的模型計算過 perplexity

→ [練習 C：訓練一個 character-level 語言模型（金庸語料）](./practice-c-char-lm.md)
