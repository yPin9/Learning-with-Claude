# Ch 19 — RAG:向量 / hybrid / rerank

> 目標:不只「會 embed + 查」。把 RAG 的真實形狀建起來——chunking、hybrid search、reranking、metadata filtering、evaluation。

## RAG 的真正問題

**Retrieval-Augmented Generation** 概念很簡單:

```
Query → 檢索相關資料 → 塞進 prompt → LLM 回答
```

**教學 demo 一小時能跑起來。Production 能 work 要好幾週**。為什麼?

因為「檢索相關資料」這一步,**很難做對**。

---

## RAG 的架構 vs 你以為的架構

**你以為**:

```
User query
    ↓
Embed
    ↓
Vector DB (cosine similarity)
    ↓
Top 5
    ↓
塞進 prompt
```

**Production**:

```
User query
    ↓
Query rewrite / expansion         ← 第 1 坑
    ↓
Hybrid search (vector + keyword)   ← 第 2 坑
    ↓
Metadata filter                    ← 第 3 坑
    ↓
Rerank (cross-encoder)             ← 第 4 坑
    ↓
Dedup / diversify                  ← 第 5 坑
    ↓
Format context for LLM             ← 第 6 坑
    ↓
LLM with citation enabled          ← 第 7 坑
    ↓
Fact-check / validate
    ↓
最終答案
```

每個坑都有細節。這章一個個過。

---

## 1. Chunking 策略

文件切 chunk 餵 embedder。Chunk 怎麼切,決定你能不能檢索對。

### Naive:固定長度切

```python
def chunk_fixed(text, size=500, overlap=50):
    chunks = []
    for i in range(0, len(text), size - overlap):
        chunks.append(text[i:i + size])
    return chunks
```

**問題**:
- 切到段落中間,破壞語意
- 一個 idea 被切成兩半,各自缺 context

### 更好:結構化切

依據文件結構:

```python
def chunk_markdown(text):
    # 按 H1/H2/H3 切
    import re
    sections = re.split(r'\n#+\s+', text)
    return sections

def chunk_code(text, language):
    # 按 function / class 切
    # 用 tree-sitter 之類的 parser
    ...
```

**原則**:

- Markdown:按 heading
- Code:按 function / class
- PDF:按 section(若有 toc)/ 段落
- 長 prose:按句子,**pack 到 target 大小但不超**

### Overlap

連續 chunk 有一點重疊,讓跨 chunk 的概念不至於缺 context。通常 **10–20%** overlap。

### Chunk size

- **太小**(<100 tokens):缺 context,單獨看不懂
- **太大**(>1500 tokens):embed 了平均掉重點,且 retrieval 粒度太粗
- **甜蜜點:200–800 tokens**,視內容

---

## 2. Embedding 模型

選擇:

### 商用 embedding API

- **Voyage AI**(Anthropic 推薦搭 Claude)
- **OpenAI text-embedding-3**
- **Cohere embed**

使用方便、效果好,要付 API 費。

### 開源

- **BGE / BGE-M3**(智源)
- **Sentence Transformers** 家族
- **E5**

自己跑,省 API 費,但要 GPU 或 slow CPU inference。

### 怎麼選

- **原型 / 小規模**:Voyage 或 OpenAI,直接用
- **大規模、cost sensitive**:開源 + 自架
- **多語言**:BGE-M3 或 Cohere multilingual

### Embedding dimension

通常 768 / 1024 / 1536 / 3072。越大越精(marginally),越慢越貴儲存。1024–1536 是好平衡。

---

## 3. Vector DB

存 embedding 的 DB。選擇:

| DB | 特點 |
|---|---|
| **Qdrant** | 開源、性能好、有 hybrid 原生支援 |
| **Weaviate** | 開源、feature 豐富、schema-first |
| **Pinecone** | SaaS、最早、穩定 |
| **Milvus** | 開源、大規模 |
| **Chroma** | 輕量、embedded、適合小專案 |
| **pgvector** | PostgreSQL 插件,適合已有 Postgres |
| **LanceDB** | 新派、embedded、serverless 友好 |

**建議**:

- 小專案 / embedded:Chroma / LanceDB
- 中大型:Qdrant / Weaviate
- 已有 Postgres、不想新增 infra:pgvector
- 企業 SaaS:Pinecone

---

## 4. Hybrid Search

**純 vector search 不夠**。vector 擅長語意相似,但遇到:

- 精確字串(product ID、人名)
- 罕見詞
- 技術術語

常常遺漏。BM25 / keyword search 擅長這些。

**Hybrid = vector + keyword,結果合併**。

### 合併策略:Reciprocal Rank Fusion(RRF)

```python
def rrf(rankings, k=60):
    # rankings: list of lists, each sublist is a ranked result list
    scores = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking):
            scores[doc_id] = scores.get(doc_id, 0) + 1 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: -x[1])
```

很簡單:排名越前分數越高,多個 ranking 累加。

### 權重版

```python
final_score = 0.7 * vector_score + 0.3 * bm25_score
```

Vector DB(Qdrant、Weaviate)通常有原生 hybrid 支援,省你自寫。

---

## 5. Metadata Filtering

Vector DB 通常支援 `where` filter:

```python
results = collection.query(
    query_embeddings=[embed(user_query)],
    n_results=10,
    where={
        "doc_type": "policy",
        "date": {"$gte": "2024-01-01"},
        "department": {"$in": ["HR", "Finance"]}
    }
)
```

**作用**:過濾不該返的內容(錯部門、過期 policy、不同 tenant 的資料...)

**設計**:

- 上傳時就在 metadata 中記錄所有可能 filter 的欄位
- Query 時根據 user context 自動加 filter

### Multi-tenant 必備

SaaS 場景:每個客戶自己的資料,不能互串。**必定**在 metadata 加 `tenant_id`,每次 query 必帶。

**一個 bug = 資料外洩**。Defense in depth:

- DB 層 filter
- Embedding 層隔離(per-tenant index)
- Application 層 re-check

---

## 6. Reranking

Vector search top 50 結果,**不一定前 5 最相關**。Vector similarity 是近似,有噪音。

**Reranker = 更精確但更慢的第二階段**。通常是個 cross-encoder(LLM-style)。

```python
from sentence_transformers import CrossEncoder

model = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')

def rerank(query, docs, top_k=5):
    pairs = [(query, d) for d in docs]
    scores = model.predict(pairs)
    ranked = sorted(zip(docs, scores), key=lambda x: -x[1])
    return [d for d, _ in ranked[:top_k]]
```

### 流程

```
Vector search → 50 候選
     ↓
Rerank → top 5
     ↓
給 LLM
```

**Cost**:多一次模型 call,但只做 top 50 不是全量 → 可接受。

**Impact**:一般能把 RAG recall 提升 20–50%。**值得做**。

### 商用 reranker

- **Voyage AI rerank**
- **Cohere rerank**

比自架開源品質好,API 便宜。

---

## 7. Query Rewriting / Expansion

使用者的 query 通常寫得爛。**用 LLM 先重寫再 retrieve**:

```python
def rewrite_query(user_query, history):
    prompt = f"""Given the chat history and the user's latest query, rewrite the query to be self-contained and searchable.

History:
{history}

User: {user_query}

Rewritten query:"""
    return llm(prompt)
```

用途:

- **解 coreference**:「他昨天說什麼?」→「John 昨天說什麼?」
- **擴充**:「怎麼設?」→「怎麼設定 OAuth in App X?」
- **翻譯**:中文 query 翻英文 retrieve(若資料是英文)

### HyDE(Hypothetical Document Embedding)

先讓 LLM **假想一個答案文件**,然後對這文件 embed 去查(不是查 query)。理論:假想文件的 embedding 跟真實文件的 embedding 更接近。

```python
def hyde(query):
    hypothetical = llm(f"Write a detailed paragraph that would answer: {query}")
    return embed(hypothetical)
```

**實測效果 mixed**。某些 domain 有效,某些不如直接 query。做 eval 驗證。

---

## 8. 給 LLM 時的 context 組織

Retrieved 5 段文件,怎麼塞進 prompt?

### 壞格式

```
Here's some context:
{doc1}
{doc2}
{doc3}

Question: {query}
```

Claude 不知道哪段是哪個來源,沒法 cite。

### 好格式

```
<context>
<document id="doc1" source="policy_v2.md" section="Vacation">
{doc1 content}
</document>
<document id="doc2" source="hr_handbook.pdf" page="12">
{doc2 content}
</document>
...
</context>

<question>
{query}
</question>

<instructions>
Answer the question using ONLY the context above. Cite sources as [doc1], [doc2] etc.
If the context doesn't contain the answer, say "I don't know".
</instructions>
```

- Tag 結構化
- Metadata 明示(source、section)
- 明確 instruction「只用 context」
- **告訴 Claude 可以說不知道**——降低 hallucination

### Anthropic Citations API

前 Ch 10 提過。用 document block + citations enabled,Claude 會自動在回應中 inline 引用 offset。**比 prompt 求 citation 可靠**。

---

## 9. Evaluation

你怎麼知道你的 RAG 好?

### 面向 1:Retrieval quality

- **Recall@k**:相關文件在 top k 內的比例
- **MRR(Mean Reciprocal Rank)**:第一個相關文件的排名倒數平均
- **NDCG**:排名相關性的加權

建測試集:一堆 (query, 相關的 doc_ids) pairs。

### 面向 2:End-to-end quality

- **Faithfulness**:答案內容都來自 context 嗎?(沒 hallucinate?)
- **Answer relevance**:答案有回答問題嗎?
- **Context relevance**:retrieval 給的 context 真的相關嗎?

用 **LLM-as-judge**:另一個 LLM 給答案打分。

```python
def judge_faithfulness(answer, context):
    prompt = f"""Given the context and the answer, rate 1-5:
    5 = answer is fully supported by context
    1 = answer contradicts or fabricates

Context: {context}
Answer: {answer}

Score and brief reason:"""
    return llm(prompt)
```

### 工具

- **Ragas**:RAG-specific eval library,有標準 metrics
- **Braintrust / Langfuse**:觀測 + eval 整合

Ch 20 會細講 eval。

---

## 10. 何時該用 RAG,何時不該

### ✓ 該用 RAG

- 知識庫 > context window(100k+ 文件)
- 知識會**更新**(LLM 訓練快照 stale)
- 需要 **citation**(醫療、法律、公司內部)
- 每個 query 只需要**一小部分**資料

### ✗ 不該用 RAG

- 知識庫小(< 50k tokens) → 全部塞 context + caching
- 答案需要**跨很多文件**綜合(RAG 取 top k 漏內容)
- 低延遲需求(retrieval 那步會多 100–500ms)

### 替代方案:Long context

Sonnet 4.6 1M context → 把整個知識庫塞進去。用 prompt caching:

```python
client.messages.create(
    system=[
        {"type": "text", "text": "You're a Q&A assistant."},
        {"type": "text", "text": ENTIRE_KB_CONTENT, "cache_control": {"type": "ephemeral"}}
    ],
    ...
)
```

Cache 讀取只 10% 價錢,每次 query cheap。

**適用**:知識量中等(10k–500k tokens),不會天天更新。**省去 RAG 系統複雜度**。

### Hybrid:Long context + RAG routing

大知識庫先 RAG 收斂到 50k,然後長 context 方式用完。兩者結合的折衷。

---

## 11. 向量 DB 不是唯一答案

某些場景下,傳統檢索比向量還強:

- **精確 SKU / 產品編號 / 時間**:直接 SQL / Elasticsearch
- **Code search**:tree-sitter AST + grep 更準
- **已知 schema 的資料**:DB query > RAG

**不要硬套 RAG**。先問「我的 query 有沒有結構?」

---

## RAG 的 checklist

Production RAG 該有:

- [ ] Structured chunking(不是純固定長度)
- [ ] Hybrid search(vector + keyword)
- [ ] Metadata filtering(安全 + 相關性)
- [ ] Reranker
- [ ] Query rewriting(多輪對話必要)
- [ ] Context format 有 tag + metadata
- [ ] Citations(LLM 回應能標來源)
- [ ] Retrieval eval + end-to-end eval
- [ ] Monitoring(retrieval latency、hit rate)

缺任何一個都是債。

---

## 自我檢核

- [ ] Naive chunk + vector search 為什麼 production 不夠?
- [ ] Hybrid search 解決什麼問題?
- [ ] Rerank 為什麼放在 vector search 之後不是取代它?
- [ ] Multi-tenant RAG 為什麼危險?怎麼防?
- [ ] 什麼時候該用 long context 取代 RAG?

→ [Ch 20 Evaluation:沒 eval 的 LLM app 是玩具](./20-eval.md)
