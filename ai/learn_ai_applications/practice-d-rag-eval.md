# Practice D — RAG + eval pipeline

> 目標:搭一個完整的 RAG 系統 + eval pipeline。不是 demo 程度——是「能跑、能改、能驗證」的工程版。

## 題目:你的個人筆記 Q&A

把你自己的 markdown 筆記(任何內容都行——如果沒有就用 Wikipedia 幾百篇文章 dump)當知識庫,做一個問答系統。能回答 query + 引用來源。

然後建 eval,證明它 work。

---

## Part 1: RAG 系統

### Step 1:準備資料

至少 100 份 markdown 文件。來源選:

- 你的個人 note repo
- Wikipedia dump 的 sample
- 開源文件(如某框架的 docs)
- 公司內部 wiki(如果能取得)

放在 `./data/*.md`。

### Step 2:Chunking + Embedding

```python
# rag/ingest.py
from pathlib import Path
import json

def chunk_markdown(text: str, target_size=500, overlap=50):
    """按 heading 切,超過 target_size 再細切。"""
    # 粗略實作:按 H2 切,太長的 paragraph 切
    import re
    sections = re.split(r'\n(?=##\s)', text)
    chunks = []
    for section in sections:
        if len(section) <= target_size:
            chunks.append(section)
        else:
            # 按段落切
            paras = section.split("\n\n")
            buf = ""
            for p in paras:
                if len(buf) + len(p) < target_size:
                    buf += "\n\n" + p
                else:
                    if buf: chunks.append(buf)
                    buf = p
            if buf: chunks.append(buf)
    return [c for c in chunks if c.strip()]

def ingest(data_dir="./data"):
    chunks = []
    for md_file in Path(data_dir).rglob("*.md"):
        text = md_file.read_text(encoding="utf-8")
        for i, chunk in enumerate(chunk_markdown(text)):
            chunks.append({
                "id": f"{md_file.name}-{i}",
                "content": chunk,
                "source": str(md_file),
                "source_title": md_file.stem,
            })
    return chunks

if __name__ == "__main__":
    chunks = ingest()
    print(f"Total chunks: {len(chunks)}")
    Path("chunks.json").write_text(json.dumps(chunks, indent=2))
```

### Step 3:Embed + store

選一個 embedding 方式:

**Voyage(推薦,Anthropic 合作)**:

```python
import voyageai
vo = voyageai.Client()

def embed_batch(texts):
    result = vo.embed(texts, model="voyage-3")
    return result.embeddings
```

**Cohere / OpenAI / 開源** 也 OK。

**Vector DB**:用 Chroma(簡單):

```python
import chromadb
client = chromadb.PersistentClient(path="./chroma_db")
collection = client.get_or_create_collection("notes")

for chunk in chunks:
    collection.add(
        ids=[chunk["id"]],
        documents=[chunk["content"]],
        embeddings=[embed_one(chunk["content"])],
        metadatas=[{"source": chunk["source"], "title": chunk["source_title"]}]
    )
```

### Step 4:Retrieval

```python
# rag/retrieve.py
def retrieve(query, k=5):
    query_emb = embed_one(query)
    results = collection.query(query_embeddings=[query_emb], n_results=k)
    return [
        {
            "content": doc,
            "source": meta["source"],
            "title": meta["title"],
            "score": score,
        }
        for doc, meta, score in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0]
        )
    ]
```

### Step 5:Hybrid search

加 BM25:

```python
# pip install rank_bm25
from rank_bm25 import BM25Okapi

texts = [c["content"] for c in chunks]
bm25 = BM25Okapi([t.split() for t in texts])

def hybrid_retrieve(query, k=5):
    # Vector
    vec_results = retrieve(query, k=20)
    vec_ids = [r["id"] for r in vec_results]

    # BM25
    bm_scores = bm25.get_scores(query.split())
    bm_top = sorted(enumerate(bm_scores), key=lambda x: -x[1])[:20]
    bm_ids = [chunks[i]["id"] for i, _ in bm_top]

    # RRF
    scores = {}
    for rank, cid in enumerate(vec_ids):
        scores[cid] = scores.get(cid, 0) + 1 / (60 + rank)
    for rank, cid in enumerate(bm_ids):
        scores[cid] = scores.get(cid, 0) + 1 / (60 + rank)
    top = sorted(scores.items(), key=lambda x: -x[1])[:k]
    # 查回 full info
    ...
```

### Step 6:Rerank

```python
# pip install sentence-transformers
from sentence_transformers import CrossEncoder
reranker = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')

def rerank(query, candidates, top_k=5):
    pairs = [(query, c["content"]) for c in candidates]
    scores = reranker.predict(pairs)
    paired = sorted(zip(candidates, scores), key=lambda x: -x[1])
    return [c for c, _ in paired[:top_k]]
```

### Step 7:LLM 生成 + citation

```python
# rag/answer.py
from anthropic import Anthropic
client = Anthropic()

def answer(query):
    candidates = hybrid_retrieve(query, k=20)
    top = rerank(query, candidates, top_k=5)

    context_str = "\n\n".join([
        f'<document id="{i}" source="{d["title"]}">\n{d["content"]}\n</document>'
        for i, d in enumerate(top)
    ])

    system = """You are a Q&A assistant. Answer ONLY using the provided context.
If the context doesn't contain the answer, say "I don't have that information".
Cite sources inline as [doc_id]. Do not make up information."""

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=system,
        messages=[{
            "role": "user",
            "content": f"<context>\n{context_str}\n</context>\n\nQuestion: {query}"
        }],
    )
    return {
        "answer": resp.content[0].text,
        "sources": [{"id": i, "title": d["title"], "source": d["source"]} for i, d in enumerate(top)],
    }
```

---

## Part 2: Eval pipeline

### Step 1:建 golden set

準備 30 筆 (query, expected_answer_or_keywords, correct_source_ids):

```json
// golden/qa.json
[
    {
        "query": "What is the capital of France?",
        "expected_keywords": ["Paris"],
        "must_contain_source": ["france-wiki"],
        "category": "factual"
    },
    {
        "query": "Explain quantum entanglement",
        "expected_keywords": ["particles", "correlated"],
        "must_contain_source": ["quantum-mechanics"],
        "category": "explanation"
    },
    {
        "query": "Who is the current CEO of Mars?",
        "expected_keywords": ["I don't have"],   # 測 hallucination 抵抗
        "category": "unknown"
    },
    ...
]
```

**品類**:

- Factual(明確事實)
- Explanation(概念解釋)
- Multi-hop(需要 combine 多 source)
- Unknown(期待 "I don't know")
- Edge(極短、極模糊、跨語言)

### Step 2:Retrieval eval

```python
# eval/retrieval.py
import json

def eval_retrieval():
    golden = json.loads(Path("golden/qa.json").read_text())
    recall_at_5 = 0
    mrr_sum = 0

    for case in golden:
        if "must_contain_source" not in case: continue
        retrieved = hybrid_retrieve(case["query"], k=5)
        retrieved_sources = [r["source"] for r in retrieved]
        needed = set(case["must_contain_source"])
        hit_sources = set(retrieved_sources) & needed
        if hit_sources:
            recall_at_5 += 1
            for rank, s in enumerate(retrieved_sources, start=1):
                if s in needed:
                    mrr_sum += 1 / rank
                    break

    n = len([c for c in golden if "must_contain_source" in c])
    return {"recall@5": recall_at_5/n, "mrr": mrr_sum/n}
```

### Step 3:End-to-end eval

```python
# eval/e2e.py
def eval_e2e():
    golden = json.loads(Path("golden/qa.json").read_text())
    results = []
    for case in golden:
        result = answer(case["query"])
        checks = {}
        if "expected_keywords" in case:
            checks["has_keywords"] = all(
                k.lower() in result["answer"].lower()
                for k in case["expected_keywords"]
            )
        if "must_not_contain" in case:
            checks["no_forbidden"] = not any(
                k.lower() in result["answer"].lower()
                for k in case["must_not_contain"]
            )
        results.append({
            "query": case["query"],
            "category": case["category"],
            "passed": all(checks.values()),
            "checks": checks,
            "answer": result["answer"][:200],
        })

    # 統計
    by_category = {}
    for r in results:
        by_category.setdefault(r["category"], []).append(r["passed"])

    for cat, passes in by_category.items():
        print(f"{cat}: {sum(passes)}/{len(passes)}")

    return results
```

### Step 4:LLM-as-judge faithfulness

```python
def judge_faithfulness(query, answer, context):
    prompt = f"""Given the CONTEXT and the ANSWER to a QUERY, classify:

FAITHFUL: Answer is fully supported by context
PARTIAL: Answer mostly supported, but has 1-2 unsupported claims
HALLUCINATED: Answer has major unsupported or contradicting claims

QUERY: {query}

CONTEXT:
{context}

ANSWER:
{answer}

Output JSON: {{"verdict": "FAITHFUL|PARTIAL|HALLUCINATED", "issues": ["..."]}}"""
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )
    return json.loads(resp.content[0].text)
```

跑 golden set,看 hallucination 率 < 10%。

### Step 5:把 eval 跑成 CI

`.github/workflows/eval.yml`:

```yaml
name: Eval
on: [pull_request]
jobs:
  retrieval:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - run: pip install -r requirements.txt
      - run: python eval/retrieval.py
        env:
          VOYAGE_API_KEY: ${{ secrets.VOYAGE_API_KEY }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}

  e2e:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - run: pip install -r requirements.txt
      - run: python eval/e2e.py
        env:
          VOYAGE_API_KEY: ${{ secrets.VOYAGE_API_KEY }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

---

## Part 3: 實驗與比較

做幾組比較,用 eval 量化:

### A/B 1: Chunking strategy

- 方案 A:固定 500 字切
- 方案 B:按 heading 切

跑兩次 eval,比 recall@5。

### A/B 2: Retrieval 方式

- 純 vector
- Vector + BM25(RRF)
- + Rerank

三次 eval,比較。

### A/B 3: LLM model

- Haiku 4.5
- Sonnet 4.6
- Opus 4.7

同 query 三個 model,cost vs quality。

**這就是 iteration**:建好 eval 後,改架構、跑 eval、看數字。

---

## 加分挑戰

### 1. Query rewriting

多輪對話時先重寫 query:

```python
def rewrite(query, history):
    # 用 Haiku 快速重寫
    ...
```

### 2. Metadata filter

按 source / 日期 filter:

```python
hybrid_retrieve(query, k=5, filter={"source_type": "tech-blog"})
```

### 3. Observability

用 Langfuse / 自己 log:記錄每個 query 的 retrieval、rerank、LLM call 時間和 cost。

### 4. Citation 強化

用 Anthropic Citations API(Ch 10)得到結構化 citation offset。

### 5. Streaming

做成 FastAPI endpoint,streaming 回前端。

---

## 驗收 checklist

- [ ] 100+ docs ingested
- [ ] Hybrid search(vector + BM25)
- [ ] Reranker
- [ ] 30 筆 golden qa.json
- [ ] Retrieval eval(recall@5、mrr)跑得動
- [ ] E2E eval 跑得動
- [ ] LLM-as-judge faithfulness 跑得動
- [ ] 至少跑一組 A/B 比較,有數據結論
- [ ] CI pipeline 設定好(eval on PR)

---

## 反思問題

1. 哪個 retrieval strategy 對你的資料最好?為什麼?
2. Hallucination 集中在哪類 query?
3. Reranker 真的幫上忙嗎?多少 uplift?
4. 如果換成 long context(整個 KB 塞 1M context),會 work 嗎?cost 差多少?
5. 你如果要給團隊用,還少哪些 production feature?

---

## 這 Practice 訓練到的能力

- 完整 RAG pipeline
- Eval pipeline 設計
- Metric-driven iteration
- A/B 對比架構
- CI 整合

做完這個,你就有**真正的 RAG 工程經驗**,不是 tutorial level。
