# Ch 24 — AI Agent Node：把 LLM 接進 Workflow

> 目標：能在 n8n workflow 裡讓 LLM 自主決定要呼叫哪些工具、取得資料，完成任務並回傳結果。

## AI Agent 和純 LLM 呼叫的差異

**純 LLM 呼叫**（Ch 11 用 HTTP Request 打 OpenAI API）：

```
你給 prompt → LLM 回答 → 結束
```

**AI Agent**：

```
你給任務 → LLM 思考 → 呼叫工具 → 看結果 → 再思考 → 再呼叫工具 → ... → 給出最終答案
```

Agent 可以主動使用工具（查資料庫、搜尋網路、發 email），不只是生成文字。n8n 的 AI Agent node 讓你把任意 n8n node 作為工具給 LLM 使用。

---

## AI Agent Node 的結構

```
[AI Agent Node]
      │
      ├── Chat Model：選 LLM（OpenAI / Claude / Ollama）
      ├── Memory：對話記憶（可選）
      └── Tools：LLM 可以呼叫的工具清單
             ├── HTTP Request Tool
             ├── Code Tool
             ├── Postgres Tool
             └── 任意 n8n node
```

---

## 設定 Chat Model Credential

### OpenAI

1. 取得 OpenAI API key（platform.openai.com → API Keys）
2. n8n Credentials → New → OpenAI API → 填入 key
3. AI Agent node → Chat Model → 選 OpenAI Chat Model → 選 `gpt-4o-mini`

### Claude（Anthropic）

1. 取得 Anthropic API key（console.anthropic.com）
2. n8n Credentials → New → Anthropic API
3. AI Agent node → Chat Model → Anthropic Chat Model → 選 `claude-haiku-4-5-20251001`

### Ollama（本地模型，免費）

如果你在本機或同一台 VPS 上跑 Ollama：

```bash
ollama pull llama3.2
ollama serve   # 監聽 localhost:11434
```

n8n 的 Ollama Chat Model credential：
```
Base URL: http://localhost:11434  # 或 Docker 網路名
Model: llama3.2
```

---

## 建立第一個 AI Agent

範例：一個能查天氣並決定要不要帶傘的 Agent。

### Step 1：AI Agent Node 基本設定

```
Chat Model: OpenAI Chat Model（gpt-4o）
System Message:
  你是一個天氣助手。根據使用者的城市，查詢當地天氣，
  然後給出是否需要帶傘的建議，用繁體中文回答。
```

### Step 2：加入工具

在 AI Agent node 的 Tools 區加入 **HTTP Request Tool**：

```
Name:        get_weather
Description: 查詢指定城市的當前天氣，輸入城市名稱（英文）
URL:         https://wttr.in/{{ city }}?format=j1
Method:      GET
```

`Description` 告訴 LLM 這個工具能做什麼、什麼時候用它。LLM 會依此決定要不要呼叫。

### Step 3：觸發 Agent

用 Webhook 接收使用者訊息：

```
Webhook → AI Agent（Input: {{ $json.body.message }}） → Respond to Webhook（result）
```

測試：

```bash
curl -X POST http://localhost:5678/webhook-test/ask-weather \
  -H "Content-Type: application/json" \
  -d '{"message": "台北今天需要帶傘嗎？"}'
```

Agent 會：
1. 判斷要查台北天氣
2. 呼叫 `get_weather` 工具（參數：`Taipei`）
3. 看到天氣資料（降雨機率 70%）
4. 回答：「台北今天降雨機率偏高，建議帶傘。」

---

## 工具種類

### HTTP Request Tool

最常用。讓 Agent 能打任意 REST API。

```
Name:        search_web
Description: 用 DuckDuckGo 搜尋網路，輸入搜尋查詢
URL:         https://api.duckduckgo.com/?q={{ query }}&format=json
```

### Code Tool

讓 Agent 執行 JavaScript：

```
Name:        calculate
Description: 執行數學計算，輸入 JavaScript 表達式
```

```javascript
// Tool 的 Code：
const expression = $json.expression;
try {
  const result = eval(expression);   // 注意：生產環境要用 vm 沙箱
  return [{ json: { result: String(result) } }];
} catch (e) {
  return [{ json: { error: e.message } }];
}
```

### n8n Tool Node

把任意 n8n node 包成工具（n8n 1.x 新功能）。例如 Google Sheets Tool：

```
Name: get_inventory
Description: 查詢庫存，輸入產品名稱
（底層是 Google Sheets node）
```

---

## Agent 的限制

**不能做無限迴圈**：n8n 的 AI Agent 預設最多執行 10 次工具呼叫（可在 Max Iterations 設定）。超過就停止並回傳目前的答案。

**工具描述要精確**：LLM 只靠 `description` 來決定要不要用這個工具。描述不夠清楚，LLM 可能在不對的時機呼叫（或根本不呼叫）。

**Token 成本**：每次工具呼叫都需要 LLM 推理，會耗費 token。Agent 比單次 LLM 呼叫貴得多。

---

## 自我檢核

- [ ] 能設定 OpenAI 或 Claude 的 Chat Model credential
- [ ] 知道 AI Agent 和純 LLM 呼叫的核心差異（工具 + 多步推理）
- [ ] 能為 HTTP Request Tool 寫一個有效的 Name 和 Description
- [ ] 知道 Max Iterations 的用途

→ [Ch 25 AI 工具鏈實戰 — RAG、Memory、Tool Calling](./25-ai-toolchain.md)
