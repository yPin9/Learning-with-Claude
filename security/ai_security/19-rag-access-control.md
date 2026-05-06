# Ch 19 — RAG 存取控制設計

> 目標：把「不同使用者只能看不同文件」這個需求在 retrieval 層正確實作，理解三種設計模式的取捨，以及為何不能靠 LLM 指令來做存取控制。

---

## 核心問題

RAG 知識庫裡通常有多個機密等級的文件：

```
知識庫
├── 公開文件（所有員工可讀）
├── 部門內部文件（只有該部門可讀）
├── HR 機密文件（只有 HR + 高階主管）
└── 董事會文件（只有指定名單）
```

使用者 A 問「公司的薪資結構是什麼？」，RAG 系統如果沒有存取控制，
向量搜尋會把 HR 機密文件的片段撈回來，然後 LLM 如實回答。

**根本原因：向量相似度搜尋本身不認識「權限」，它只認識「語義距離」。**

---

## 為何不能靠 LLM 的指令做存取控制

```
錯誤做法：
  System prompt: "你只能回答 {user.department} 的相關問題，
                  不能透露其他部門的資訊。"

問題：
  1. Prompt injection 可以覆蓋這個指令
  2. LLM 看到了禁止的內容，只是「被指示不說」，
     不是真的沒看到——這不是存取控制
  3. RAG 召回的 context 裡已經包含機密片段
```

存取控制必須在 retrieval 層做，讓 LLM 根本看不到沒有權限的文件。

---

## 三種設計模式

```
模式一：多 Collection 隔離
  [HR 文件] --> hr_collection
  [RD 文件] --> rd_collection
  查詢時只搜對應 collection

模式二：Metadata Filter
  所有文件在同一個 collection
  每筆帶 metadata: {dept, level}
  查詢時加 where filter

模式三：混合（分 collection + metadata filter）
  [機密] --> secure_collection (帶 metadata level)
  [公開] --> public_collection
  查詢時依機密等級選 collection，再加 metadata filter
```

| 模式 | 隔離強度 | 維護複雜度 | 適合場景 |
|---|---|---|---|
| 多 Collection | 強（資料完全物理分開） | 高（collection 數量爆炸） | 固定少量租戶、高機密等級 |
| Metadata Filter | 中（資料共存，邏輯隔離） | 低（一個 collection 管理） | 部門多但機密等級較少 |
| 混合 | 強 | 中 | 機密等級分明 + 租戶多的場景 |

Metadata filter 的風險：如果 filter 邏輯寫錯（bug / race condition），
所有人就能看到所有文件。多 Collection 模式的 bug 影響範圍更小。

---

## ChromaDB Metadata Filter 完整範例

### 入庫時帶 Metadata

```python
import chromadb
from chromadb.config import Settings

client = chromadb.HttpClient(
    host="chroma.internal",
    port=8000,
    settings=Settings(
        chroma_client_auth_provider="chromadb.auth.token_authn.TokenAuthClientProvider",
        chroma_client_auth_credentials="static-token",
    )
)

collection = client.get_or_create_collection("company_docs")

# HR 機密文件入庫
collection.add(
    documents=[
        "2025 年全公司薪資調幅為 5%，主管級以上另有股票選擇權。",
        "績效評等 A 的員工年終為 4 個月。",
    ],
    metadatas=[
        {"department": "hr", "level": "confidential", "doc_id": "hr-001"},
        {"department": "hr", "level": "confidential", "doc_id": "hr-002"},
    ],
    ids=["hr-001", "hr-002"]
)

# RD 部門文件入庫
collection.add(
    documents=[
        "API v2 的設計規範詳見 Confluence 頁面 RD-2025-001。",
    ],
    metadatas=[
        {"department": "rd", "level": "internal", "doc_id": "rd-001"},
    ],
    ids=["rd-001"]
)
```

### 查詢時加 Filter

```python
def rag_query(user_query: str, user_dept: str, user_level: str, query_embedding: list):
    """
    user_dept:  使用者所屬部門（從 JWT claims 取得）
    user_level: 使用者可存取的最高機密等級
    """

    # 機密等級對應數值，用來做 >=  比較
    level_map = {"public": 0, "internal": 1, "confidential": 2, "secret": 3}
    user_level_num = level_map.get(user_level, 0)

    # 只有同部門且機密等級 <= 使用者權限的文件
    # ChromaDB where 語法：$and, $eq, $in
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=5,
        where={
            "$and": [
                {"department": {"$eq": user_dept}},
                # ChromaDB 不直接支援 <=，用 $in 枚舉可存取等級
                {"level": {"$in": [k for k, v in level_map.items() if v <= user_level_num]}}
            ]
        },
        include=["documents", "metadatas", "distances"]
    )
    return results
```

ChromaDB `where` 支援的運算子：`$eq`、`$ne`、`$gt`、`$gte`、`$lt`、`$lte`、`$in`、`$nin`、`$and`、`$or`。

---

## Token-Based 存取控制整合

生產環境通常用 JWT（JSON Web Token）帶使用者的 claims：

```python
import jwt  # pip install PyJWT
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer

bearer_scheme = HTTPBearer()

def get_current_user(token = Depends(bearer_scheme)):
    try:
        payload = jwt.decode(
            token.credentials,
            key="YOUR_JWT_SECRET",
            algorithms=["HS256"]
        )
        return {
            "user_id":   payload["sub"],
            "department": payload["dept"],    # 如 "hr"
            "level":     payload["level"],    # 如 "confidential"
        }
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")

# FastAPI endpoint
from fastapi import FastAPI
app = FastAPI()

@app.post("/query")
def query_endpoint(body: dict, user = Depends(get_current_user)):
    query_embedding = embed(body["question"])  # 你的 embedding function
    results = rag_query(
        user_query=body["question"],
        user_dept=user["department"],
        user_level=user["level"],
        query_embedding=query_embedding,
    )
    context = "\n".join(results["documents"][0])
    answer  = llm_generate(body["question"], context)  # 你的 LLM 呼叫
    return {"answer": answer}
```

存取控制的決策點在 middleware 層，不在 LLM 那一層。

---

## 設計時的常見錯誤

### 錯誤 1：Filter 在應用層，但 embedding 查詢先做

```python
# 錯誤順序（先查再過濾，over-retrieval）
all_results = collection.query(query_embeddings=[emb], n_results=100)
filtered = [r for r in all_results if r["dept"] == user.dept]  # 資料已經拿到了

# 正確：filter 在向量 DB 查詢時就帶進去
results = collection.query(
    query_embeddings=[emb],
    n_results=5,
    where={"department": {"$eq": user.dept}}
)
```

### 錯誤 2：Metadata 由客戶端傳入

```python
# 錯誤：前端可以偽造 dept 參數
@app.post("/query")
def query_endpoint(body: dict):
    dept = body["dept"]  # 攻擊者傳 "hr" 就能看 HR 文件
    ...

# 正確：dept 從已驗證的 JWT claims 取，不接受客戶端傳入
```

### 錯誤 3：忘記對 Hybrid Search 的 reranker 也做 filter

如果 pipeline 有 reranker（重排序），要確認 reranker 只看到已過濾的 candidates，
不能先拿全部文件 rerank 再過濾（一樣是 over-retrieval 問題）。

---

## 自我檢核

- [ ] 能清楚說明為什麼 LLM 的 system prompt 指令不等於存取控制
- [ ] 能比較三種設計模式的隔離強度與維護成本
- [ ] 能寫出 ChromaDB 帶 `$and` + `$in` 的 metadata filter 查詢
- [ ] 理解 JWT claims 如何在 middleware 層決定查詢 filter
- [ ] 知道「先查再過濾」vs「查詢時帶 filter」的差異與風險

存取控制設計完之後，下一個話題是組織層面的——如何用標準框架評估整個 AI 系統的風險。

→ [Ch 20 NIST AI RMF](./20-nist-ai-rmf.md)
