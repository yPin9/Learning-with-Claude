# Ch 25 — AI 工具鏈實戰：RAG、Memory、Tool Calling

> 目標：讓 AI Agent 能記住對話歷史、查詢你自己的知識庫，以及精確控制 Tool Calling 的流程。

## Memory：讓 Agent 記住對話

預設 AI Agent 每次都是「全忘」的，不記得之前說過什麼。加入 Memory node 讓它有記憶。

### 類型

| Memory Node | 說明 |
|---|---|
| **Simple Memory** | 存在記憶體，重啟就清空。適合測試 |
| **Postgres Chat Memory** | 存到 Postgres，持久化。生產推薦 |
| **Redis Chat Memory** | 存到 Redis，適合高頻對話 |
| **Window Buffer Memory** | 只記最近 N 輪對話（節省 token）|

### 設定 Postgres Chat Memory

先建 n8n Postgres credential（同 Ch 13）。

n8n 會自動建 `n8n_chat_histories` table（如果不存在）。

在 AI Agent node 的 Memory 區，加入 **Postgres Chat Memory**：

```
Session ID: {{ $json.session_id }}   ← 用 session ID 區分不同對話
Window Size: 10                       ← 記住最近 10 輪
```

Session ID 用什麼值？看你的場景：

- Telegram chatbot：用 `{{ $json.message.chat.id }}`（Telegram Chat ID）
- 每個使用者一個 session：用 `{{ $json.user_id }}`
- 匿名對話：用 UUID（`{{ $('Webhook').first().json.headers['x-session-id'] }}`）

---

## RAG：查詢你的知識庫

RAG（Retrieval-Augmented Generation）讓 LLM 在回答前先搜尋你的資料，不再只靠訓練資料。

```
使用者問題
    │
[嵌入模型：把問題向量化]
    │
[向量資料庫：找到最相近的 N 筆文件]
    │
[把文件塞進 prompt 給 LLM]
    │
LLM 根據實際資料回答
```

### 建立向量資料庫（Qdrant + n8n）

n8n 支援多種向量資料庫：Qdrant、Pinecone、Weaviate、Supabase Vector。

用 Qdrant（可以 Self-host）：

```bash
# docker-compose.yml 加入 Qdrant
qdrant:
  image: qdrant/qdrant
  ports:
    - "6333:6333"
  volumes:
    - qdrant_data:/qdrant/storage
```

### 文件入庫 Workflow

```
[HTTP Request: 取得文件]
       │
[Code: 分割文件（每 500 字一塊）]
       │
[Embeddings OpenAI: 文字轉向量]
       │
[Qdrant Vector Store: 存入向量 + 原始文字]
```

Code Node（文件分割）：

```javascript
const text = $input.first().json.content;
const chunkSize = 500;
const overlap   = 50;
const chunks    = [];

for (let i = 0; i < text.length; i += chunkSize - overlap) {
  chunks.push({
    json: {
      text:     text.slice(i, i + chunkSize),
      source:   $input.first().json.source,
      chunk_id: chunks.length
    }
  });
}

return chunks;
```

### RAG 查詢 Workflow

```
[使用者問題]
      │
[Vector Store Tool（Qdrant）: 搜尋相關文件]
      │
[AI Agent: 根據搜尋結果回答問題]
```

在 AI Agent 的 Tools 加入 **Vector Store Tool**：

```
Name: search_knowledge_base
Description: 在知識庫裡搜尋相關資訊，輸入搜尋查詢
Vector Store: Qdrant
Collection: my-docs
```

---

## 進階 Tool Calling 控制

### 強制使用特定工具

有時你想讓 LLM **必須**呼叫某個工具（不是「可能會用」）：

在 AI Agent node 的 **Tool Choice** 設定：

```
Tool Choice: Specific Tool
Tool: get_weather
```

LLM 第一步一定先呼叫 get_weather，再根據結果繼續推理。

### 結構化輸出（Structured Output）

讓 LLM 輸出固定格式的 JSON：

在 AI Agent node → Output Schema：

```json
{
  "type": "object",
  "properties": {
    "summary":     { "type": "string" },
    "sentiment":   { "type": "string", "enum": ["positive", "neutral", "negative"] },
    "key_points":  { "type": "array", "items": { "type": "string" } },
    "confidence":  { "type": "number" }
  },
  "required": ["summary", "sentiment", "key_points"]
}
```

後續 node 可以直接存取 `$json.sentiment`，不需要再 parse LLM 回傳的文字。

---

## 完整範例：企業內部問答 Bot

```
[Telegram Trigger]
        │
[Postgres Chat Memory（Session = chat id）]
        │
[AI Agent]
  System: 你是公司內部知識助手，只根據知識庫回答，不猜測。
  Model:  claude-haiku-4-5
  Memory: Postgres Chat Memory
  Tools:
    - search_knowledge_base（Qdrant RAG）
    - get_current_time（Code Tool：return new Date()）
        │
[Telegram: 發送 Agent 的回答]
```

這個 bot 能：
- 記住這次對話的上下文（Postgres Memory）
- 查詢公司的 Wiki / FAQ 文件（Qdrant RAG）
- 知道當前時間（Code Tool）
- 用繁體中文回答

---

## Token 成本控制

AI Agent 是貴的。幾個省錢策略：

**用便宜的模型做工具選擇，只在最後生成時用貴的**：不直接在 n8n 裡設，改用 LangChain 的 Router（n8n 的 LangChain Node 支援）。

**限制 Memory Window Size**：設 10 而不是 100，每個請求少帶 1000 tokens。

**工具描述精準**：描述不清會讓 LLM 多猜幾次，多耗 tokens。

**用 Anthropic 的 Claude Haiku 而不是 Opus**：Haiku 便宜 20 倍，對大多數 agent 任務夠用。

---

## 自我檢核

- [ ] 能設定 Postgres Chat Memory 並用 Session ID 區分不同對話
- [ ] 知道 RAG 的流程（問題向量化 → 搜尋 → 注入 prompt）
- [ ] 能在 AI Agent 加入 Vector Store Tool 做知識庫查詢
- [ ] 知道 Structured Output 的用途

恭喜完成 Part 6！做 Final Project 把全部整合起來。

→ [Final Project：個人自動化中樞](./final-project-automation-hub.md)
