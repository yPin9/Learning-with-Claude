# Ch 10 — 錯誤處理：Error Trigger、Retry、Continue On Fail

> 目標：讓 workflow 在出錯時不是靜靜死掉，而是能通知你、自動重試、或繼續跑剩下的 item。

## n8n 的預設錯誤行為

一個 node 失敗（HTTP 超時、API 回錯、Code Node 丟 exception），預設行為是：

1. 整個 workflow **停止執行**
2. 在 Execution Log 標記為 **Error**
3. 什麼通知都沒有

這表示你每天的排程 workflow 如果悄悄出錯，你要主動去查 Execution Log 才知道。生產環境不能這樣。

---

## 三個層級的錯誤處理

```
層級 1：Node 層級   → Continue On Fail（這個 node 失敗，繼續往下跑）
層級 2：Workflow 層級 → Error Trigger（整個 workflow 失敗，觸發另一個 workflow）
層級 3：Node 層級   → Retry On Fail（這個 node 失敗，自動重試）
```

---

## Continue On Fail（Node 設定）

在任何 node 的 Settings tab，打開 **Continue On Fail**。

```
Continue On Fail: ✅
```

這個 node 失敗時，n8n 不停止整個 workflow，而是讓那個 item 帶著錯誤資訊繼續往下傳：

```json
{
  "json": {},
  "error": {
    "message": "Request failed with status code 429",
    "name": "NodeApiError"
  }
}
```

後續的 node 可以用 If Node 檢查是否有 `error` 欄位，決定要不要走錯誤處理路徑：

```
If: {{ $json.error }} is not empty → 走錯誤處理
```

**何時用**：API 有時會失敗但不是致命的（例如某筆資料查不到），你想記錄錯誤但繼續處理其他 item。

---

## Retry On Fail（Node 設定）

同樣在 Settings tab：

```
Retry On Fail: ✅
Max Tries: 3
Wait Between Tries: 1000 (ms)
```

node 失敗後，n8n 自動重試最多 3 次，每次間隔 1 秒。全部重試都失敗後，才真正算失敗。

**何時用**：打的 API 有偶爾超時或 rate limit，自動重試通常能解決。

---

## Error Trigger：失敗通知 Workflow

這是生產環境**最重要**的機制。建一個專門的「失敗通知」workflow：

```
[Error Trigger]
      │
[Send Telegram: "workflow {{$json.workflow.name}} 失敗了！\n錯誤：{{$json.execution.error.message}}"]
```

**Error Trigger** 在其他 workflow **失敗**時觸發，傳來的 item 包含失敗的 workflow 資訊：

```json
{
  "workflow": {
    "id": "abc123",
    "name": "每日天氣報告"
  },
  "execution": {
    "id": "42",
    "url": "https://localhost:5678/workflow/abc123/executions/42",
    "retryOf": null,
    "error": {
      "message": "Request failed with status code 503",
      "stack": "..."
    },
    "lastNodeExecuted": "HTTP Request"
  }
}
```

### 設定方式

1. **新建一個 workflow**，加入 Error Trigger node
2. 接上通知 node（Telegram、Email、Slack）
3. 填好通知訊息
4. 啟用這個 workflow

然後在**每一個你想監控的 workflow** 的設定（頂部選單 → Settings → Error Workflow）裡，選這個通知 workflow。

---

## 完整的錯誤處理架構

```
主 Workflow：
[Schedule] → [HTTP Request] → [Process Data] → [Send Report]
                ↓（失敗）
          ← Error Trigger 通知 workflow 接管

錯誤通知 Workflow：
[Error Trigger]
      │
      ├─▶ [Telegram: 通知你]
      └─▶ [Google Sheets: 寫入錯誤日誌]
```

---

## 在 Code Node 裡手動拋錯誤

如果你想在 Code Node 裡基於業務邏輯觸發錯誤（而不只是語法錯誤）：

```javascript
const data = $input.first().json;

if (!data.required_field) {
  throw new Error(`缺少必要欄位 required_field，item id: ${data.id}`);
}

return [{ json: data }];
```

`throw new Error(...)` 會讓這個 node 標記為失敗，觸發 Retry / Continue On Fail / Error Trigger 的機制。

---

## 踩雷

**設了 Error Trigger 但沒收到通知**

最常見的原因：Error Trigger workflow **沒有啟用**，或目標 workflow 的 Settings → Error Workflow **沒有選到**。

**Continue On Fail 後，後面的 node 因為 input 是空的而出錯**

用 If Node 先檢查 `$json.error` 是否存在，有就走錯誤處理路徑，沒有才繼續主路徑。

---

## 自我檢核

- [ ] 能在 Settings tab 開啟 Continue On Fail 和 Retry On Fail
- [ ] 知道 Error Trigger workflow 的設定方式（兩步：建 workflow + 設 Error Workflow）
- [ ] 知道失敗通知的 item 裡有哪些欄位
- [ ] 能在 Code Node 用 `throw new Error()` 觸發失敗

Part 2 結束。接下來是第一個練習，把 Ch 5–10 學到的東西拼起來。

→ [練習 A：資料聚合 Workflow](./practice-a-data-aggregation.md)
