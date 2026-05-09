# Ch 33 — RAG 基礎：向量資料庫 + 檢索增強生成

> 目標：理解 RAG 解決的問題，並用 Ollama 本地 embedding 模型建立一個完整的 RAG 管線。

## LLM 的知識局限

LLM 只知道訓練截止日之前的資訊，也不知道你的私有文件。

解決方案有三種：

| 方案 | 成本 | 實時性 | 適用 |
|------|------|--------|------|
| Fine-tuning | 高（重訓練） | 差（需重訓） | 知識是固定且大量的 |
| RAG | 低 | 好（即時更新） | 知識會更新，或有隱私要求 |
| Long Context | 中 | 好 | 文件量少，能塞進 context |

**RAG（Retrieval-Augmented Generation）**是最常用的解法：在提問時，先從文件庫檢索相關片段，塞進 prompt 一起發給 LLM。

## RAG 管線

```
[離線準備]
文件 → 切片（chunking）→ Embedding → 向量資料庫

[線上查詢]
問題 → Embedding → 向量相似度搜尋 → 相關片段
         ↓
    Prompt = "根據以下資料回答：{相關片段}\n\n問題：{問題}"
         ↓
      LLM 生成回答
```

## Step 1：切片（Chunking）

文件不能整段丟進去（太長），需要切成合適大小的片段：

```python
def chunk_text(text, chunk_size=500, overlap=50):
    """
    chunk_size: 每個片段的字元數
    overlap: 相鄰片段的重疊字元數（保持上下文連貫）
    """
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        if chunk:
            chunks.append(chunk)
        start += chunk_size - overlap
    return chunks

# 測試
long_text = "A" * 2000
chunks = chunk_text(long_text, chunk_size=500, overlap=50)
print(f"切成 {len(chunks)} 個片段")
print(f"片段大小：{[len(c) for c in chunks]}")
```

## Step 2：Embedding

把每個片段轉成向量。用 Ollama 的 `nomic-embed-text` 模型（本地，不需要付費 API）：

```bash
# 下載 embedding 模型
ollama pull nomic-embed-text
```

```python
import requests
import numpy as np

def embed(text):
    """用 Ollama 本地模型把文字轉成向量"""
    resp = requests.post("http://localhost:11434/api/embeddings", json={
        "model": "nomic-embed-text",
        "prompt": text,
    })
    return np.array(resp.json()["embedding"])

# 測試
vec = embed("Transformer 架構的核心是注意力機制")
print(f"向量維度：{len(vec)}")  # nomic-embed-text 輸出 768 維
```

## Step 3：建立向量資料庫

最簡單的方案：用 `numpy` 做暴力搜索（小規模夠用）：

```python
import numpy as np, json, os

class SimpleVectorDB:
    def __init__(self, db_path="vector_db.json"):
        self.db_path = db_path
        self.chunks  = []
        self.vectors = []
        if os.path.exists(db_path):
            self.load()

    def add(self, text, vector=None):
        if vector is None:
            vector = embed(text)
        self.chunks.append(text)
        self.vectors.append(vector.tolist())

    def search(self, query, top_k=3):
        if not self.vectors:
            return []
        q_vec = embed(query)
        # 餘弦相似度
        vecs = np.array(self.vectors)
        scores = vecs @ q_vec / (np.linalg.norm(vecs, axis=1) * np.linalg.norm(q_vec) + 1e-10)
        top_idx = np.argsort(scores)[-top_k:][::-1]
        return [(self.chunks[i], float(scores[i])) for i in top_idx]

    def save(self):
        with open(self.db_path, 'w', encoding='utf-8') as f:
            json.dump({"chunks": self.chunks, "vectors": self.vectors}, f)

    def load(self):
        with open(self.db_path, encoding='utf-8') as f:
            data = json.load(f)
        self.chunks  = data["chunks"]
        self.vectors = data["vectors"]
```

## Step 4：建立文件索引

```python
def index_documents(docs, db):
    """把文件切片並 embed"""
    for doc in docs:
        chunks = chunk_text(doc, chunk_size=500, overlap=50)
        for chunk in chunks:
            vec = embed(chunk)
            db.add(chunk, vec)
    db.save()
    print(f"索引完成：{len(db.chunks)} 個片段")

# 示範：索引一些 AI 相關文章
docs = [
    """Transformer 是 Google 在 2017 年提出的神經網路架構。
    核心機制是自注意力（Self-Attention），讓模型能直接看到序列中所有位置。
    原始論文叫做「Attention Is All You Need」。""",

    """LoRA（Low-Rank Adaptation）是微調大型語言模型的高效方法。
    它在原始預訓練矩陣旁邊加入低秩分解矩陣，只訓練這些少量參數。
    記憶體需求比全量微調小很多，適合消費級 GPU。""",
]

db = SimpleVectorDB()
index_documents(docs, db)
```

## Step 5：RAG 查詢

```python
def rag_chat(question, db, model="qwen2.5:7b", top_k=3):
    # 1. 檢索相關片段
    results = db.search(question, top_k=top_k)

    if not results:
        context = "（沒有找到相關資料）"
    else:
        context_parts = []
        for i, (chunk, score) in enumerate(results):
            context_parts.append(f"[{i+1}] {chunk}")
        context = "\n\n".join(context_parts)

    # 2. 建立帶有 context 的 prompt
    prompt = f"""根據以下資料回答問題。如果資料不足，請說明。

資料：
{context}

問題：{question}

回答："""

    # 3. 呼叫 LLM
    resp = requests.post("http://localhost:11434/api/generate", json={
        "model": model,
        "prompt": prompt,
        "stream": False,
    })
    return resp.json()["response"]

# 測試
answer = rag_chat("LoRA 是什麼？為什麼比全量微調省記憶體？", db)
print(answer)
```

## 用 ChromaDB 做更大規模的向量搜尋

```bash
pip install chromadb
```

```python
import chromadb

client = chromadb.Client()
collection = client.create_collection("my_docs")

# 加入文件
collection.add(
    documents=["LoRA 是...", "Transformer 是..."],
    ids=["doc1", "doc2"],
    # 如果不提供 embeddings，ChromaDB 會用自己的模型
)

# 查詢
results = collection.query(
    query_texts=["什麼是低秩分解"],
    n_results=3,
)
print(results["documents"])
```

## RAG 的常見問題

**問題一：檢索到的片段不相關**

→ 改善切片策略（加大 overlap、用段落邊界切）
→ 換更好的 embedding 模型
→ 增加 top_k 再讓 LLM 過濾

**問題二：LLM 忽略 context，回答自己知道的**

→ 在 prompt 裡加強指示：「只根據提供的資料回答，不要使用其他知識」
→ 使用溫度較低的設定（更確定性）

**問題三：Context 太長超過模型 context window**

→ 減少 top_k，或縮小 chunk_size
→ 用 reranker 二次篩選

## 動手練習

建立一個針對「ML 概念」的本地知識庫：

1. 把 Ch 1–12 的課程內容存成 txt 檔
2. 切片、embed，存進 `SimpleVectorDB`
3. 問以下問題，觀察 RAG 是否能從課程內容回答：

```python
questions = [
    "什麼是梯度消失問題？",
    "LayerNorm 和 BatchNorm 的差別是什麼？",
    "為什麼 Transformer 不用 RNN？",
]

for q in questions:
    print(f"\n問：{q}")
    print(f"答：{rag_chat(q, db)[:300]}")
```

## 自我檢核

- [ ] 理解 RAG 和 fine-tuning 各自適用的場景
- [ ] 成功安裝 `nomic-embed-text` 並用 Ollama 取得向量
- [ ] 建立了一個能搜索的 `SimpleVectorDB`
- [ ] 跑過 RAG 問答，回答明顯引用了 context 中的資訊

→ [Ch 34 全棧回顧：raw text → 部署完成的 chatbot](./34-full-stack-review.md)
