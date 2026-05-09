# Ch 20 — 執行控制：Wait、Respond to Webhook、並行執行

> 目標：控制 workflow 的執行時序，包含暫停等待、對外回應、以及讓多條支路並行或合流。

## Wait Node：暫停執行

Wait node 讓 workflow **暫停**，等條件滿足後再繼續。有三種模式：

### 模式 1：固定等待時間

```
Resume: After Time Interval
Wait Amount: 30
Wait Unit: Seconds
```

workflow 停在這個 node，30 秒後自動繼續。

**典型場景**：發出一個非同步任務後，等 30 秒再去輪詢結果。

### 模式 2：等到指定時間

```
Resume: At Specific Date/Time
Date/Time: {{ $json.scheduled_at }}
```

workflow 停到指定的 datetime 才繼續。

**典型場景**：使用者排程了一個任務要在「明天早上 9 點」執行。

### 模式 3：Webhook 喚醒

```
Resume: By Webhook
Webhook Suffix: my-resume-hook
```

Wait node 暫停後，會提供一個喚醒 URL（`/webhook/my-resume-hook`）。外部打這個 URL 才繼續執行。

**典型場景**：

```
[送出審核申請]
     │
[Wait: By Webhook]   ← 等待審核員點擊「批准/拒絕」按鈕
     │（審核員打了 /webhook/resume?decision=approved）
[根據 decision 繼續]
```

這是人工審核流程的核心模式。

---

## Respond to Webhook：主動回應

Ch 12 提過，這裡補充時序控制的用法。

常見模式：Webhook 接到請求 → 做一些處理 → 回傳結果：

```
[Webhook]
     │
[Respond to Webhook: 202 Accepted]  ← 立刻告訴呼叫者「收到了，在處理」
     │
[開始做耗時的處理...]
     │
[完成後發 Telegram 通知（不需要透過 Webhook 回傳）]
```

等等，上面那樣**不對**。Respond to Webhook 執行後，後面的 node 還是會跑，但 HTTP 連線已經關閉了。

如果你需要「先回 202，再非同步執行後續邏輯」，正確做法是：

1. Respond to Webhook 回 202
2. 用 Execute Workflow（Fire and Forget）觸發另一個 workflow 繼續做事

```
[Webhook]
     │
[Respond to Webhook: 202]
     │
[Execute Workflow (Fire & Forget): 繼續處理 workflow]
```

---

## 並行執行（Fanout）

一個 node 的輸出接到多個下游 node，它們**並行執行**：

```
[HTTP Request: 取得資料]
       │
       ├──▶ [Telegram: 發即時通知]
       ├──▶ [Postgres: 存入資料庫]
       └──▶ [Google Sheets: 更新試算表]
```

n8n 把這三個 node 並行啟動，不等其中任何一個完成才跑下一個。

**注意**：並行的三條路如果需要合流（最後匯成一個 item），用 **Merge** node 的 Append 模式。

---

## 控制並行度（Rate Limiting）

如果你有 100 個 items，但 API 每秒只接受 5 個請求：

```
[100 items]
     │
[Split In Batches: size=5]
     │
[Wait: 1 second]   ← 每批之間等 1 秒
     │
[HTTP Request]
```

或者 Loop Over Items + Wait 達到循序控速：

```
[100 items]
     │
[Loop Over Items]
     │
[HTTP Request]
     │
[Wait: 200ms]  ← 每個 item 之後等 200ms（約 5 req/s）
     │
(回到 Loop)
```

---

## 執行超時

長時間運行的 workflow（輪詢、等待）預設沒有超時限制。如果你想設一個最大執行時間，在 workflow Settings 裡設定：

```
Workflow Settings → Execution:
  Timeout: 3600   （秒，0 = 無限制）
```

超時後 workflow 標記為失敗，Error Trigger 會被觸發。

---

## 完整範例：人工審核流程

```
[Webhook: 收到申請]
         │
[Postgres: 儲存申請]
         │
[Telegram: 通知審核員 + 附帶喚醒 URL]
   "新申請 #{{ $json.id }} 等待審核
    批准：{{ $json.resumeUrl }}&decision=approved
    拒絕：{{ $json.resumeUrl }}&decision=rejected"
         │
[Wait: By Webhook, suffix={{ $json.id }}]   ← 暫停，等審核員點連結
         │
[If: {{ $json.query.decision }} == approved]
  true  ──▶ [發申請通過 Email]
  false ──▶ [發申請拒絕 Email]
         │
[Postgres: 更新申請狀態]
```

Wait By Webhook 會提供一個 `resumeUrl`，帶在 `$json.resumeUrl`。審核員點連結後，workflow 從 Wait node 後繼續，`$json.query` 包含連結裡帶的 query params（如 `decision=approved`）。

---

## 自我檢核

- [ ] 知道 Wait node 的三種模式（固定時間、指定時間、Webhook 喚醒）
- [ ] 能設計「收到 Webhook 立刻回 202，非同步繼續處理」的 workflow
- [ ] 知道並行 fanout 的節點結構
- [ ] 能用 Split In Batches + Wait 做 rate limiting

Part 4 結束，做練習 C 測試 Code Node 技能。

→ [練習 C：Code Node 挑戰](./practice-c-code-node-challenge.md)
