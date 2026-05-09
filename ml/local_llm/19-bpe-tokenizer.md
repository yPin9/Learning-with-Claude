# Ch 19 — 自製 BPE Tokenizer

> 目標：理解 BPE 演算法的核心邏輯，並用 Python 從頭實作一個可以跑的中文 BPE tokenizer。

## 為什麼不用現成 tokenizer

Ch 7 提過，GPT-2 的 tokenizer 對中文「不友善」——每個中文字要 2 個 token，效率是英文的一半。

原因：GPT-2 的 BPE 是從英文語料建立的，中文字元在訓練時太罕見，沒有被合併成高頻組合。

自製 tokenizer 的好處：
- 針對你的語料最佳化（中文詩歌 tokenizer 和技術文件 tokenizer 應該不一樣）
- 理解底層機制（Llama 的 tokenizer 和 tiktoken 都是 BPE 的變體）

## BPE 演算法的直覺

BPE（Byte Pair Encoding）原本是資料壓縮演算法，NLP 裡用來做 tokenization：

**訓練過程**：
1. 從最小單位開始（字元或 bytes）
2. 統計所有相鄰 pair 的出現頻率
3. 把最高頻的 pair 合併成一個新 token
4. 重複 2-3，直到達到目標 vocab size

**範例**：語料是 `"低頭思故鄉 低頭望明月 低頭"`

```
初始（字元級）：
  {'低': 3, '頭': 3, '思': 1, '故': 1, '鄉': 1, '望': 1, '明': 1, '月': 1}
  pair 頻率：('低', '頭'): 3  ← 最高頻

合併 ('低', '頭') → '低頭'：
  {'低頭': 3, '思': 1, '故': 1, ...}
  pair 頻率：... 沒有出現 >=2 次的新 pair

結果：vocab 多了 '低頭' 這個 token
```

## 從頭實作 BPE

```python
from collections import defaultdict
import re

def get_pairs(vocab):
    """統計 vocab 中所有相鄰 pair 的頻率"""
    pairs = defaultdict(int)
    for word, freq in vocab.items():
        symbols = word.split()
        for i in range(len(symbols) - 1):
            pairs[(symbols[i], symbols[i+1])] += freq
    return pairs

def merge_vocab(pair, vocab):
    """把最高頻的 pair 合併，更新 vocab"""
    new_vocab = {}
    bigram = ' '.join(pair)
    replacement = ''.join(pair)
    for word in vocab:
        new_word = word.replace(bigram, replacement)
        new_vocab[new_word] = vocab[word]
    return new_vocab

def train_bpe(text, num_merges=100):
    # 把文字轉成字元序列（每個字元之間加空格）
    # 每個詞尾加 </w> 標記（原始 BPE 做法）
    words = text.split()
    vocab = defaultdict(int)
    for word in words:
        # 把字元拆開，中文每個字一個位置
        spaced = ' '.join(list(word)) + ' </w>'
        vocab[spaced] += 1

    merges = []
    for i in range(num_merges):
        pairs = get_pairs(vocab)
        if not pairs:
            break
        best = max(pairs, key=pairs.get)
        vocab = merge_vocab(best, vocab)
        merges.append(best)
        if i < 10 or i % 20 == 0:
            print(f"合併 {i+1}: {best[0]+best[1]!r} (頻率 {pairs[best]})")

    return vocab, merges

# 測試
text = "低頭思故鄉 低頭望明月 低頭 床前明月光 明月幾時有"
vocab, merges = train_bpe(text, num_merges=20)
print("\n最終 vocab 片段：")
for word, freq in sorted(vocab.items(), key=lambda x: -x[1])[:10]:
    print(f"  {word!r}: {freq}")
```

## 用 sentencepiece 訓練繁體中文 tokenizer

實際使用時，用 Google 的 `sentencepiece`（Llama 系列用的就是它）：

```python
import sentencepiece as spm
import os

# 準備訓練語料（一個大 txt 檔，每行一個句子）
with open("corpus.txt", "w", encoding="utf-8") as f:
    poems = [
        "春眠不覺曉處處聞啼鳥夜來風雨聲花落知多少",
        "床前明月光疑是地上霜舉頭望明月低頭思故鄉",
        # ... 更多文字
    ]
    f.write('\n'.join(poems))

# 訓練 tokenizer
spm.SentencePieceTrainer.train(
    input="corpus.txt",
    model_prefix="my_tokenizer",
    vocab_size=4000,          # 建議 4000–8000 for 中文
    character_coverage=0.9995, # 覆蓋多少比例的字元
    model_type="bpe",         # 用 BPE 演算法
    pad_id=0, unk_id=1, bos_id=2, eos_id=3,
)
```

## 使用訓練好的 tokenizer

```python
sp = spm.SentencePieceProcessor()
sp.load("my_tokenizer.model")

# Encode
text = "床前明月光"
ids = sp.encode(text, out_type=int)
print(f"encode: {ids}")

# Decode
print(f"decode: {sp.decode(ids)}")

# 查看切分方式
pieces = sp.encode(text, out_type=str)
print(f"pieces: {pieces}")
# 如果語料夠多，'明月' 這種高頻二字組合會被合成一個 token
```

## 中文 tokenizer 的 vocab size 怎麼選

| Vocab Size | 說明 |
|-----------|------|
| 1000–2000 | 字元級，每字是 1 token，冗餘 |
| 4000–8000 | 常用組合被合併，中文約 1–1.5 token/字 |
| 30000–50000 | 現代 LLM 中文 tokenizer，效率最好 |
| 100000+ | 多語言 tokenizer（Llama 3 用 128k） |

從頭訓練小模型用 4000–8000 就夠；如果要接 Llama 這類現成模型，直接用它的 tokenizer。

## tokenizer 的特殊 token

| Token | 說明 |
|-------|------|
| `<pad>` | 填充，讓 batch 內序列等長 |
| `<unk>` | 未知 token（vocab 沒有的字元） |
| `<bos>` / `<s>` | 序列開始 |
| `<eos>` / `</s>` | 序列結束 |
| `<|im_start|>` | ChatML 格式的對話開始（Qwen 等用） |

## 動手練習

用你的詩詞語料訓練一個小型 BPE tokenizer，比較它和 GPT-2 tokenizer 的效率：

```python
import tiktoken

# 用 GPT-2 tokenizer 算中文的 tokens/字
enc = tiktoken.get_encoding("gpt2")
text = "床前明月光疑是地上霜舉頭望明月低頭思故鄉"
gpt2_tokens = enc.encode(text)
print(f"GPT-2: {len(text)} 字，{len(gpt2_tokens)} tokens，比率 {len(gpt2_tokens)/len(text):.2f}")

# 用你自訓的 sentencepiece（如果訓練完了）
# sp_tokens = sp.encode(text)
# print(f"自訓: {len(text)} 字，{len(sp_tokens)} tokens，比率 {len(sp_tokens)/len(text):.2f}")
```

## 自我檢核

- [ ] 能用一段話解釋 BPE 的訓練過程
- [ ] 跑過手寫 BPE 的程式碼，看到 pair 合併過程
- [ ] 理解為什麼中文需要更大 vocab size 才能達到好的 token 效率
- [ ] 知道 `<bos>` / `<eos>` / `<pad>` 各自的用途

→ [Ch 20 Pre-training loop：DataLoader / checkpointing](./20-pretraining-loop.md)
