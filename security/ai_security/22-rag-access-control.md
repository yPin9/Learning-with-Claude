# Ch 22 — RAG 存取控制設計

> **目標**：能設計 per-document ACL for RAG，讓不同使用者只能搜到有權限的文件，理解 metadata filtering 的安全實作與限制。
>
> **環境**：Python 3.11, LangChain 0.3.x, Ollama + llama3.2:3b, ChromaDB, Ubuntu 22.04

---

## 為什麼需要這個？

你的 RAG chatbot 連接公司所有部門的文件：HR 的薪資結構、財務的營收報告、法務的合約條款。一個行銷部的實習生問「公司 CEO 的年薪是多少？」——RAG 幫他從 HR 文件裡找到答案，LLM 很開心地告訴他。

問題出在哪裡？**大多數 RAG 系統沒有存取控制**。向量搜索只看 cosine similarity，不看「這個使用者有沒有權限看這份文件」。Ch 3 的 RAG pipeline 裡，任何 query 都能搜到所有 chunk——零 ACL（Access Control List，存取控制清單）。

在傳統系統裡，HR 系統有 RBAC、檔案伺服器有 permission bits、SharePoint 有 document permission。但文件進了向量 DB 之後，這些權限資訊全部消失。RAG 的 ACL 是一個需要從頭設計的問題。

---

## 先建立直覺

想像一個公司圖書館，每本書封面有一張標籤寫「誰可以借」。

```
沒有 ACL 的 RAG（現狀）：
  ┌─────────────────────────────────────┐
  │  Vector DB                          │
  │                                     │
  │  [HR 薪資文件]  [財務報告]  [合約]  │
  │                                     │
  │  任何 query → cosine similarity     │
  │  → 最相似的 k 筆 → 全部回傳        │
  └─────────────────────────────────────┘
  行銷實習生問薪資 → 拿到 HR 機密 ← 問題

有 ACL 的 RAG（目標）：
  ┌─────────────────────────────────────┐
  │  Vector DB                          │
  │                                     │
  │  [HR 薪資文件]     → allowed: hr    │
  │  [財務報告]        → allowed: fin   │
  │  [員工手冊]        → allowed: all   │
  │                                     │
  │  query + user_group=marketing       │
  │  → 只搜 allowed 包含 marketing 的  │
  │  → 只拿到員工手冊                   │
  └─────────────────────────────────────┘
  行銷實習生問薪資 → 搜不到 HR 文件 ← 正確
```

---

## 核心概念：Per-Document ACL 設計

### 範例一：ChromaDB 的 metadata filter 實作

最直接的做法：在每個 document chunk 的 metadata 裡加 `allowed_groups`，query 時帶上使用者的 group 做 filter。

```python
# rag_acl_basic.py — ChromaDB metadata filter ACL
import chromadb
from chromadb.utils import embedding_functions

# 用 Ollama 的 embedding
ollama_ef = embedding_functions.OllamaEmbeddingFunction(
    model_name="llama3.2",
    url="http://localhost:11434/api/embed",
)

client = chromadb.Client()
collection = client.create_collection(
    name="company_docs",
    embedding_function=ollama_ef,
)

# === Ingest: 給每個文件加 ACL metadata ===
documents = [
    {
        "id": "hr_001",
        "text": "CEO 年薪為 500 萬，含股票選擇權 1000 張",
        "metadata": {
            "source": "hr_salary.pdf",
            "department": "hr",
            "allowed_groups": "hr,executive",  # 只有 HR 和高管能看
        },
    },
    {
        "id": "fin_001",
        "text": "Q3 營收 2.3 億，年增 15%，淨利率 12%",
        "metadata": {
            "source": "q3_report.pdf",
            "department": "finance",
            "allowed_groups": "finance,executive",
        },
    },
    {
        "id": "pub_001",
        "text": "員工旅遊補助每年 5000 元，需填寫申請表",
        "metadata": {
            "source": "employee_handbook.pdf",
            "department": "hr",
            "allowed_groups": "all",  # 全員可看
        },
    },
]

collection.add(
    ids=[d["id"] for d in documents],
    documents=[d["text"] for d in documents],
    metadatas=[d["metadata"] for d in documents],
)

# === Query: 帶使用者 group 做 filter ===
def query_with_acl(question: str, user_group: str, n_results: int = 5):
    """帶 ACL 的 RAG query"""
    # ChromaDB 的 $or filter
    results = collection.query(
        query_texts=[question],
        n_results=n_results,
        where={
            "$or": [
                {"allowed_groups": {"$contains": user_group}},
                {"allowed_groups": {"$contains": "all"}},
            ]
        },
    )
    return results

# 行銷部員工查薪資
print("=== 行銷部員工 ===")
results = query_with_acl("CEO 年薪多少？", user_group="marketing")
print(f"  找到 {len(results['documents'][0])} 筆")
for doc in results["documents"][0]:
    print(f"  → {doc[:60]}")
# 預期：只拿到員工旅遊補助（allowed: all），拿不到 HR 薪資文件

print()

# HR 員工查薪資
print("=== HR 員工 ===")
results = query_with_acl("CEO 年薪多少？", user_group="hr")
print(f"  找到 {len(results['documents'][0])} 筆")
for doc in results["documents"][0]:
    print(f"  → {doc[:60]}")
# 預期：拿到 CEO 薪資文件 + 員工旅遊補助
```

這是最基本的實作。但它有嚴重的安全限制——後面踩雷集錦會逐一拆解。

---

## 底層機制：Pre-filtering vs Post-filtering

Metadata filter 在 ANN（Approximate Nearest Neighbor，近似最近鄰）搜尋中的套用時機，直接影響效能和正確性。

```
方案 A: Pre-filtering（先過濾再搜）

  全部 10,000 個 chunk
       │
       ▼ metadata filter (allowed_groups contains user_group)
  過濾後 2,000 個 chunk
       │
       ▼ ANN search (cosine similarity)
  Top-k 結果

  優點：安全——被過濾掉的 chunk 完全不參與搜索
  缺點：過濾後的 chunk 數量少，ANN index 效率下降
       如果用戶權限很窄，可能只剩幾十個 chunk，
       ANN 退化成暴力搜索

方案 B: Post-filtering（先搜再過濾）

  全部 10,000 個 chunk
       │
       ▼ ANN search (cosine similarity)
  Top-100 候選結果
       │
       ▼ metadata filter (allowed_groups contains user_group)
  Top-k 結果（從 100 裡過濾）

  優點：ANN 在完整 index 上搜，效率最高
  缺點：⚠ 洩漏資訊！
       「找到 100 筆，你能看的只有 3 筆」
       → 使用者知道有 97 筆他不能看的文件存在
       → 如果 post-filter 後不足 k 筆，
         使用者知道他的 query 命中了受限文件

方案 C: 混合（大多數生產系統的做法）

  全部 10,000 個 chunk
       │
       ▼ metadata pre-filter（粗篩）
  候選集 3,000 個 chunk
       │
       ▼ ANN search
  Top-k 結果
       │
       ▼ 嚴格的 ACL 檢查（確認每筆結果的權限）
  最終結果

ChromaDB 的實作：Pre-filtering（where clause 在 ANN 之前套用）
Weaviate 的實作：Pre-filtering（filter 在向量搜索之前）
Pinecone 的實作：Post-filtering（先搜再 filter，有上述洩漏風險）
```

---

## 進一步用法：LangChain + Auth Middleware 整合

### 範例二：用 FastAPI + LangChain 做 Auth-aware RAG

```python
# rag_acl_fastapi.py — 核心邏輯（省略 import 和 boilerplate）
@app.post("/query", response_model=QueryResponse)
def query_rag(request: QueryRequest, user: dict = Depends(get_current_user)):
    # Step 1: 從 JWT 取使用者 groups，建 metadata filter
    group_filters = [
        {"allowed_groups": {"$contains": g}} for g in user["groups"]
    ]
    group_filters.append({"allowed_groups": {"$contains": "all"}})

    # Step 2: 用 filter 做 retrieval
    retriever = vectorstore.as_retriever(
        search_kwargs={"k": 5, "filter": {"$or": group_filters}},
    )
    retrieved_docs = retriever.invoke(request.question)

    # Step 3: Audit log（誰查了什麼、撈到哪些文件）
    audit_logger.info(
        f"user={user['user_id']} | groups={user['groups']} | "
        f"query={request.question} | "
        f"retrieved={[d.metadata.get('source', '?') for d in retrieved_docs]}"
    )

    # Step 4: 生成回答
    context = "\n\n".join(d.page_content for d in retrieved_docs)
    answer = (prompt | llm | StrOutputParser()).invoke(
        {"context": context, "question": request.question}
    )
    return QueryResponse(answer=answer, sources=[
        d.metadata.get("source", "unknown") for d in retrieved_docs
    ])
```

關鍵：`get_current_user` 從 JWT token 解析 `user_id` 和 `groups`（生產環境用 OIDC provider），FastAPI 的 `Depends` 確保每個 request 都經過身分驗證。

### 更嚴格的設計：PostgreSQL + pgvector 的 Row-Level Security

如果你需要 database-level 的 ACL（不是 application-level 的 metadata filter），可以用 PostgreSQL + pgvector extension：

```sql
-- 建立 embedding 表
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE documents (
    id SERIAL PRIMARY KEY,
    content TEXT,
    embedding vector(4096),
    department TEXT,
    allowed_groups TEXT[]  -- PostgreSQL 原生 array
);

-- 建立 RLS（Row-Level Security）policy
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;

CREATE POLICY doc_access ON documents
    FOR SELECT
    USING (
        'all' = ANY(allowed_groups)
        OR current_setting('app.user_group') = ANY(allowed_groups)
    );

-- 建立角色
CREATE ROLE hr_user;
CREATE ROLE marketing_user;

GRANT SELECT ON documents TO hr_user, marketing_user;

-- 查詢時設定 user group
SET app.user_group = 'marketing';
SELECT content, 1 - (embedding <=> '[0.1, 0.2, ...]'::vector) AS similarity
FROM documents
ORDER BY embedding <=> '[0.1, 0.2, ...]'::vector
LIMIT 5;
-- RLS 自動過濾：行銷部看不到 HR 文件
```

RLS 的優勢：ACL 在 database engine 層級執行，即使 application code 有 bug 也不會洩漏。缺點是實作複雜度高，且 pgvector 的 ANN index（HNSW/IVFFlat）和 RLS 的交互效能需要測試。

---

## 對比與取捨

| 方案 | 實作複雜度 | 安全強度 | 效能影響 | 適用場景 |
|------|-----------|---------|---------|---------|
| **Metadata filter（ChromaDB）** | 低 | 中（application-level） | 低（pre-filter） | PoC、小團隊 |
| **獨立 DB 分離（每部門一個 collection）** | 中 | 高（物理隔離） | 無（各查各的） | 部門數少且固定 |
| **Row-Level Security（pgvector）** | 高 | 最高（database-level） | 中（RLS + ANN 交互） | 合規要求嚴格的企業 |
| **Weaviate Multi-tenancy** | 中 | 高（tenant 隔離） | 低（原生支援） | 多租戶 SaaS |

選擇建議：

- **概念驗證、快速上線**：Metadata filter，但接受它不是真正的 ACL
- **部門數少（<10）且權限不重疊**：獨立 collection 物理隔離
- **合規要求、需要 audit trail**：PostgreSQL + pgvector + RLS
- **SaaS 產品、每個客戶的資料要完全隔離**：Weaviate Multi-tenancy

---

## 踩雷集錦

**1. ChromaDB 的 metadata filter 不是真正的 ACL**

Metadata filter 是 application-level 的邏輯。如果攻擊者繞過你的 FastAPI 直接連 ChromaDB（Ch 20 說過，ChromaDB 沒有 auth），`where` clause 根本不存在——他拿到所有資料。Metadata filter 是「禮貌的請求」，不是「強制的門禁」。

**2. Metadata 可以被使用者（或攻擊者）修改**

如果你的 ingest pipeline 接受使用者上傳文件，使用者可以自己決定 metadata。惡意使用者上傳文件時把 `allowed_groups` 設成 `all`，就繞過了 ACL。metadata 的寫入端必須由受信任的後端控制，不能讓使用者直接指定。

**3. Embedding 本身不受 ACL 保護**

即使你用 metadata filter 限制了 query 結果，embedding 向量本身仍然存在 vector DB 裡。如果攻擊者能直接讀取 vector DB（Ch 20 的 ChromaDB 無 auth 場景），他可以拿到所有 embedding，然後用 embedding inversion 技術反推原始文本。ACL 保護的是 retrieval 路徑，不是 storage 層。

**4. Post-filtering 洩漏文件存在的事實**

```
使用者 query 返回：「找到 10 筆相關結果，其中 3 筆你有權限查看」

攻擊者知道了：
  - 有 7 筆他不能看的相關文件存在
  - 這些文件和他的 query 高度相關
  - 如果 query 是「公司裁員計畫」→ 攻擊者知道公司有裁員相關的機密文件
```

防禦：永遠用 pre-filtering，不要在 response 裡洩漏被過濾的筆數。如果 pre-filter 後結果不足 k 筆，就回傳不足的數量，不要補充說「有更多結果但你沒權限」。

**5. Group membership 同步是另一個問題**

使用者的部門/群組可能隨時變動（調部門、離職）。如果 JWT token 的 groups claim 是登入時簽發的，使用者調部門後 token 裡的 groups 還是舊的——他仍然能看到前部門的文件。Token 的有效期必須夠短，或用 real-time group membership check（每次 query 都去 IdP 查）。

---

## 進階

### 稽核：記錄誰查了什麼

ACL 只管「能不能看」，稽核管「看了什麼」。在合規場景（GDPR、SOC 2）裡，稽核日誌和 ACL 同等重要。每筆 audit entry 需記錄：`timestamp`、`user_id`、`user_groups`、`query`、`retrieved_document_ids`、`response_length`（不要 log response 全文——可能包含敏感資訊）。寫到 append-only 的 JSONL 檔（不能被修改或刪除）。

事件發生時（例如有人凌晨 3 點大量 query 薪資問題），稽核日誌讓你追溯：誰、問了什麼、拿到哪些文件、回答多長（異常長 = 可能洩漏大量資料）。

### 動態權限：基於文件內容的分級

靜態的 `allowed_groups` 適合部門數少的場景。複雜場景需要自動分級 pipeline：Raw PDF → PII Detection（Ch 21 Presidio，有 PII 的標 confidential）→ Keyword Classifier（財務/薪資/合約 → restricted）→ Auto-tag Metadata → Embed + Store。這樣新文件 ingest 時自動取得正確的 ACL 標籤，不需要人工設定。

---

## 動手練習

1. **基本 ACL 實作**：在 ChromaDB 裡建立三個部門的文件（HR、finance、public），用 metadata filter 實作 per-group ACL。驗證行銷部員工看不到 HR 文件。

2. **ACL bypass 測試**：在練習 1 的基礎上，用 Python 直接連 ChromaDB（不經過 FastAPI），不帶 metadata filter 做 query。驗證你能拿到所有文件——這就是 metadata filter 不是真正 ACL 的證明。

3. **Audit log 分析**：在 FastAPI 的 RAG endpoint 加入 audit logging，做 20 次不同 query，然後分析 audit log：哪個使用者查了最多？哪些文件被查最多次？有沒有異常模式？

4. **Post-filtering 洩漏測試**：故意用 post-filtering 實作（先搜全部再過濾），觀察返回結果的數量是否洩漏了受限文件的存在。

---

## 重點整理

- 大多數 RAG 系統沒有 ACL——任何 query 都能搜到所有文件，這是一個嚴重的設計缺陷。
- Per-document ACL 的核心設計：ingest 時加 `allowed_groups` metadata，query 時用使用者的 group 做 filter。
- Metadata filter 是 application-level 的控制，不是 database-level 的 ACL——繞過 application 層就形同虛設。
- Pre-filtering 比 post-filtering 安全——post-filtering 會洩漏受限文件存在的事實。
- PostgreSQL + pgvector + Row-Level Security 是目前最強的 database-level ACL 方案。
- 稽核日誌和 ACL 同等重要：ACL 管「能不能看」，稽核管「看了什麼」。
- Metadata 的寫入端必須由受信任的後端控制——如果使用者能自訂 metadata，ACL 形同虛設。
- Group membership 同步問題：JWT token 的 groups claim 可能過時，需要短效期或 real-time check。

---

## 自我檢核

- 解釋為什麼 ChromaDB 的 metadata filter 不是真正的 ACL。攻擊者如何繞過它？
- Pre-filtering 和 post-filtering 的安全差異是什麼？舉一個 post-filtering 洩漏資訊的具體場景。
- 如果你要設計一個符合 GDPR 的 RAG ACL 系統，你會選哪個方案？為什麼？
- 稽核日誌裡應該記錄哪些欄位？為什麼不應該記錄 LLM 的完整 response？
- Metadata 寫入端為什麼必須由後端控制？如果使用者能自訂 metadata 會發生什麼？

---

## 延伸閱讀

### 官方文件

- **[ChromaDB Filtering Documentation](https://docs.trychroma.com/guides#filtering-by-metadata)**
  - **讀哪裡**：Where Filters 段落，理解 `$contains`、`$and`、`$or` 的語法
  - **學什麼**：ChromaDB 的 metadata filter 能力和限制

- **[Weaviate Multi-tenancy](https://weaviate.io/developers/weaviate/manage-data/multi-tenancy)**
  - **讀哪裡**：整頁，理解 tenant isolation 的設計
  - **學什麼**：database-level 的多租戶隔離如何實作

### 技術文章

- **"Access Control in RAG Systems"** — LangChain Blog
  - **讀哪裡**：architecture section，理解 metadata filter 和 custom retriever 的整合
  - **學什麼**：LangChain 生態裡的 ACL 最佳實踐

### 資料庫

- **[PostgreSQL Row Level Security](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)**
  - **讀哪裡**：CREATE POLICY 的語法和範例
  - **學什麼**：database-level 的強制存取控制如何實作

---

→ [練習 C — AI 系統威脅建模文件](./practice-c-threat-modeling.md)
