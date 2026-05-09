# Ch 18 — 資料管線：語料清洗 + tokenization 流程

> 目標：從原始文字到可以餵進模型的 token 張量，走完完整的資料預處理流程。

## 語料品質決定模型品質

「垃圾進，垃圾出」在 LLM 訓練上特別準。OpenAI 訓練 GPT-4 的成本幾億美元，其中相當大一部分花在資料清洗上，而不是模型本身。

一個典型的中文語料管線：

```
原始資料（網路爬蟲、書籍、百科）
   │
   ▼
語言過濾（保留繁體/簡體中文，去掉其他語言）
   │
   ▼
內容過濾（去掉廣告、垃圾、低品質文字）
   │
   ▼
去重複（文本級別的去重）
   │
   ▼
正規化（統一標點、去除多餘空白）
   │
   ▼
Tokenization（切成 token ids）
   │
   ▼
打包成訓練格式（固定長度 chunk）
```

## 最小可用的清洗流程

```python
import re
import unicodedata

def clean_text(text: str) -> str:
    # 1. Unicode 正規化（統一全形/半形、相似字元）
    text = unicodedata.normalize("NFKC", text)

    # 2. 去除控制字元（保留換行）
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)

    # 3. 統一空白（多個空白合一、去掉行首行尾空白）
    text = re.sub(r'[ \t]+', ' ', text)
    text = '\n'.join(line.strip() for line in text.split('\n'))

    # 4. 去掉重複的換行（超過兩個合成一個段落分隔）
    text = re.sub(r'\n{3,}', '\n\n', text)

    # 5. 去掉過短的行（垃圾通常很短）
    lines = [l for l in text.split('\n') if len(l) > 10]
    text = '\n'.join(lines)

    return text.strip()

# 測試
raw = "  今天　天氣   很好！\n\n\n\n明天可能下雨。\n\n短\n"
print(repr(clean_text(raw)))
```

## 語言過濾

```python
def is_chinese(text: str, threshold=0.3) -> bool:
    """至少 threshold 比例的字元是中文"""
    total = len(text)
    if total == 0:
        return False
    chinese = sum(1 for c in text if '一' <= c <= '鿿')
    return chinese / total >= threshold

# 測試
print(is_chinese("今天天氣很好"))        # True
print(is_chinese("Hello World"))         # False
print(is_chinese("Today 今天 good"))     # True（混合，閾值 0.3 通過）
```

## 文本去重

訓練資料裡的重複內容會讓模型過擬合常見模式。最簡單的方法是 MinHash：

```python
# pip install datasketch
from datasketch import MinHash, MinHashLSH

def get_minhash(text, num_perm=128):
    m = MinHash(num_perm=num_perm)
    for word in text.split():
        m.update(word.encode('utf-8'))
    return m

def deduplicate(texts, threshold=0.8):
    lsh = MinHashLSH(threshold=threshold, num_perm=128)
    unique_texts = []
    for i, text in enumerate(texts):
        m = get_minhash(text)
        result = lsh.query(m)
        if not result:  # 沒有找到相似的
            lsh.insert(str(i), m)
            unique_texts.append(text)
    return unique_texts
```

對小語料（< 10 萬篇），用 set 存 hash 就夠了：

```python
def simple_dedup(texts):
    seen = set()
    result = []
    for t in texts:
        h = hash(t[:200])  # 用前 200 字的 hash 判重
        if h not in seen:
            seen.add(h)
            result.append(t)
    return result
```

## 打包成訓練格式

訓練 GPT 時，資料格式是把所有文本串接起來，用特殊 token 分隔，然後切成固定長度的 chunk：

```python
import torch

def pack_texts(texts, tokenizer_fn, chunk_size=1024, eos_token_id=0):
    """
    tokenizer_fn: 把一段文字轉成 token id list 的函數
    chunk_size: 每個訓練樣本的長度
    """
    # 1. 把所有文本轉成 token ids，用 eos 分隔
    all_ids = []
    for text in texts:
        ids = tokenizer_fn(text)
        all_ids.extend(ids)
        all_ids.append(eos_token_id)  # 文本結束標記

    # 2. 切成固定長度 chunk
    chunks = []
    for i in range(0, len(all_ids) - chunk_size, chunk_size):
        chunk = all_ids[i:i + chunk_size]
        chunks.append(chunk)

    return torch.tensor(chunks, dtype=torch.long)  # [num_chunks, chunk_size]

# 示範（用 character-level tokenizer）
texts = ["春眠不覺曉", "床前明月光", "白日依山盡"]
all_chars = sorted(set(''.join(texts)))
stoi = {c: i+1 for i, c in enumerate(all_chars)}  # 0 預留給 EOS
tokenize = lambda s: [stoi.get(c, 0) for c in s]

data = pack_texts(texts, tokenize, chunk_size=8, eos_token_id=0)
print(data.shape)   # [num_chunks, 8]
print(data[:3])
```

## 用 Hugging Face datasets 處理大語料

```python
from datasets import load_dataset, Dataset
import os

# 從本地 txt 檔案建立 dataset
def load_txt_files(folder):
    texts = []
    for fname in os.listdir(folder):
        if fname.endswith('.txt'):
            with open(os.path.join(folder, fname), encoding='utf-8') as f:
                texts.append({"text": f.read()})
    return Dataset.from_list(texts)

# 用 map 批次清洗
dataset = load_txt_files("./raw_texts")
dataset = dataset.map(lambda x: {"text": clean_text(x["text"])})
dataset = dataset.filter(lambda x: len(x["text"]) > 100)

print(f"清洗後：{len(dataset)} 筆")
```

## 動手練習

建立一個完整的小型語料管線：

```python
# 1. 準備 3 個不同品質的文本（一個正常、一個重複、一個垃圾）
texts = [
    "春眠不覺曉，處處聞啼鳥。夜來風雨聲，花落知多少。",
    "春眠不覺曉，處處聞啼鳥。夜來風雨聲，花落知多少。",  # 重複
    "aaa bbb 廣告 click here 短",  # 垃圾
]

# 2. 清洗 + 去重 + 語言過濾
cleaned = [clean_text(t) for t in texts]
cleaned = [t for t in cleaned if is_chinese(t)]
cleaned = simple_dedup(cleaned)

print(f"原始：{len(texts)} 筆，清洗後：{len(cleaned)} 筆")
# 期望：原始 3，清洗後 1（重複移除、垃圾過濾）
```

## 自我檢核

- [ ] 能寫出至少 5 個文本清洗步驟
- [ ] 知道為什麼要對訓練資料去重複
- [ ] 理解「打包成固定長度 chunk」為什麼要加 EOS token
- [ ] 跑過練習，確認重複和垃圾被正確過濾

→ [Ch 19 自製 BPE Tokenizer](./19-bpe-tokenizer.md)
