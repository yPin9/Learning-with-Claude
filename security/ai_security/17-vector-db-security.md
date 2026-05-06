# Ch 17 — 向量資料庫安全

> 目標：理解三大向量資料庫（Vector Database）的安全特性差異，掌握生產環境的認證、TLS、RBAC 配置，以及向量資料本身的隱私風險。

---

## 三大向量 DB 快速對比

```
[開發者 local]  --> ChromaDB (in-process / local HTTP)
[企業自架生產]  --> Milvus   (分散式，Kubernetes 友好)
[雲端快速上線]  --> Pinecone (SaaS，API key 鑑權)
```

| 面向 | ChromaDB | Milvus | Pinecone |
|---|---|---|---|
| 部署模式 | in-process / HTTP server | 自架（Docker / K8s） | 全託管 SaaS |
| 認證機制 | Static token（需手動啟用） | 內建 auth + LDAP | API key / 組織 SSO |
| TLS 支援 | 需在 reverse proxy 做 | 原生支援（單向 / mTLS） | 雲端預設啟用 |
| RBAC | 無 | 有（collection / database 層） | 無，靠 namespace 邏輯隔離 |
| 適合場景 | 本地開發、PoC | 大規模自架生產 | 快速上線、不想維運 |
| 主要安全風險 | 預設無 auth 直接暴露 | 設定複雜、RBAC 容易沒開 | API key 洩漏、namespace 邊界靠應用層 |

---

## 共同攻擊面

### 1. 未授權存取（Unauthenticated Access）

向量 DB 的預設設定通常為了開發方便，auth 是關閉的。
上生產如果沒改設定，任何人可以：

- 列出所有 collection
- 讀取所有 embedding 與 metadata
- 刪除整個索引

ChromaDB HTTP server 預設監聽 `0.0.0.0:8000`，零認證。
Milvus 預設也不啟用認證，`milvus.yaml` 裡要明確設 `authorizationEnabled: true`。

### 2. namespace / collection 隔離不足

多租戶（Multi-tenant）場景最常踩的坑：

```
錯誤做法
  所有租戶的文件全進同一個 collection
  查詢時只靠 LLM 的 system prompt 說「只回答屬於這個公司的資料」

正確做法
  在 retrieval 層就用 metadata filter 或獨立 collection 強制隔離
  LLM 根本看不到的東西，才是真正的隔離
```

LLM 的指令不是存取控制，只是建議。

### 3. 查詢結果洩漏（Result Leakage）

向量相似度搜尋沒有「你無權看這筆」的概念，它只回「最相似的 top-k」。
如果沒有在 retrieval 層套存取控制，A 使用者的查詢可能拿到 B 的文件片段。

---

## Milvus 安全設定

### 啟用認證

編輯 `milvus.yaml`（或透過 Helm values 覆蓋）：

```yaml
common:
  security:
    authorizationEnabled: true

# 預設帳號 root，密碼 Milvus，部署完第一件事就是改掉
```

### 啟用 TLS

```yaml
tls:
  serverPemPath: /path/to/server.pem
  serverKeyPath: /path/to/server.key
  caPemPath:     /path/to/ca.pem
  tlsMode: 1    # 1 = 單向 TLS，2 = 雙向 mTLS
```

### RBAC 設定流程

```
建立 role --> 授予 collection 操作權限 --> 建立 user --> 把 user 綁到 role
```

```python
from pymilvus import connections, utility, Role

connections.connect(
    host="milvus.internal",
    port="19530",
    user="root",
    password="YOUR_STRONG_PASSWORD",
    secure=True,
    server_pem_path="ca.pem",
)

# 建立唯讀角色
role = Role("readonly_role")
role.create()

# 授予指定 collection 的 Query 與 Search 權限
role.grant("Collection", "knowledge_base", "Query")
role.grant("Collection", "knowledge_base", "Search")

# 建立應用帳號並綁定角色
utility.create_user("app_service", "app-password-strong")
role.add_user("app_service")
```

應用層的 LangChain / LlamaIndex 連線帳號只需 Search 權限，
不要用 root 帳號跑應用。

---

## Pinecone 安全設定

### API Key 管理

Pinecone 只靠 API key 鑑權，key 洩漏等於全資料庫洩漏：

```python
import os
from pinecone import Pinecone

# 永遠從環境變數讀，不要 hardcode
pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
```

- dev / staging / prod 各自用不同 API key
- CI/CD 用 Secrets Manager 注入，不要放進 `.env` 提交進 repo
- 定期 rotate，舊 key 立刻在 Pinecone 後台 revoke

### Namespace 做租戶隔離

Pinecone 沒有 collection-level RBAC，namespace 是現有的最細邊界：

```python
index = pc.Index("prod-index")

# 租戶 A 入庫
index.upsert(
    vectors=[
        {"id": "doc1", "values": [0.1, 0.2, ...], "metadata": {"dept": "hr"}}
    ],
    namespace="tenant_acme"
)

# 租戶 A 查詢，namespace 鎖定，拿不到其他租戶的資料
results = index.query(
    vector=query_embedding,
    top_k=5,
    namespace="tenant_acme",   # 這個值必須由後端決定，不能讓前端自由傳
    include_metadata=True
)
```

關鍵限制：namespace 隔離完全靠應用層控制。
Pinecone 本身不做「這個 API key 只能用這些 namespace」的細粒度控制。
一旦 API key 洩漏，攻擊者可以查詢任意 namespace。

---

## ChromaDB 生產配置

**核心原則：生產環境不能跑 in-process 模式。**

in-process 下，ChromaDB 就是應用程式 Python 程序裡的一個物件，
沒有網路邊界，沒有認證，也無法多個 worker 共享。

### 啟用 HTTP Server + Auth Token

```bash
# 用環境變數啟用 token 認證
export CHROMA_SERVER_AUTHN_CREDENTIALS="s3cr3t-static-token"
export CHROMA_SERVER_AUTHN_PROVIDER="chromadb.auth.token_authn.TokenAuthenticationServerProvider"

chroma run --host 127.0.0.1 --port 8000
# 只監聽 localhost，TLS 由前面的 Nginx / Caddy 負責
```

```python
import chromadb
from chromadb.config import Settings

client = chromadb.HttpClient(
    host="chroma.internal",
    port=8000,
    settings=Settings(
        chroma_client_auth_provider="chromadb.auth.token_authn.TokenAuthClientProvider",
        chroma_client_auth_credentials="s3cr3t-static-token",
    )
)
```

ChromaDB 目前沒有內建 RBAC，所有客戶端拿同一個 token，
如果需要細粒度控制要在應用層自己做，或換用 Milvus。

---

## 向量資料的備份與加密

### Embedding 是衍生資料，但有隱私風險

很多人以為「存的是向量，又看不懂，應該安全」——這是錯的。

```
原文：「病患張三的 HIV 檢測結果為陽性。」
  --> text-embedding-3-large
  --> [0.023, -0.141, 0.887, ...]  (1536 維)

攻擊者可以：
  1. 對已知文字做 embedding，比較餘弦相似度，推測原文類型
  2. 用 Vec2Text（2023 年學術攻擊）從 embedding 高品質還原短文本
  3. 用 embedding 做成員推理（membership inference）：
     某筆資料有沒有在你的 RAG 知識庫裡
```

2023 年 Vec2Text 論文已實證可從 OpenAI `text-embedding-ada-002` 還原原文，
尤其對短句效果好。不要把存 embedding 等同於已去識別化。

### 備份與加密策略

| 資料類型 | 備份方式 | 靜態加密 |
|---|---|---|
| Embedding 向量 | 定期 snapshot + 異地複製 | AES-256，由儲存層或 KMS 管理 |
| Metadata | 與向量一起備份 | 同上，metadata 往往含有 PII |
| 原始文件 | 獨立備份，依機密等級分層 | 高機密用 HSM / KMS 管理金鑰 |

Milvus 官方提供 `milvus-backup` CLI 工具。
Pinecone 沒有原生 export API（截至 2024），備份依賴廠商的 SLA 或自行批次讀出。

---

## 自我檢核

- [ ] 能說出三大向量 DB 的部署模式與核心安全差異
- [ ] 知道 Milvus 啟用認證、TLS、RBAC 各自在哪裡設
- [ ] 能寫出帶認證的 Milvus 連線與最小權限 RBAC 設定
- [ ] 理解 Pinecone namespace 隔離的邊界在哪裡、限制是什麼
- [ ] 能解釋為何 in-process ChromaDB 不能進生產
- [ ] 知道 Vec2Text 攻擊的存在：embedding 不等於去識別化

下一步是在資料進入向量 DB 之前就要做的事——把 PII 在 indexing time 和 query time 各擋一次。

→ [Ch 18 Data Masking 實作](./18-data-masking.md)
