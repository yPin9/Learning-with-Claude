# Ch 6 — Trigger 全覽：Schedule / Webhook / Manual / App

> 目標：知道四大類 Trigger 各自的使用場景，能設定任意觸發條件啟動 workflow。

## Trigger 是什麼

每個 workflow 必須從一個 **Trigger Node** 開始。Trigger 決定「什麼時候跑」。沒有 Trigger，workflow 永遠不會自動執行。

```
[Trigger] → [Node A] → [Node B] → ...
```

n8n 的 Trigger 分四類：

| 類型 | 觸發方式 | 典型場景 |
|---|---|---|
| Manual | 你手動按 | 測試、一次性任務 |
| Schedule | 定時 | 每天報表、定期同步 |
| Webhook | 收到外部 HTTP 請求 | 接收第三方通知、表單 |
| App Trigger | 某個服務的事件 | GitHub push、Gmail 新信 |

---

## Manual Trigger

最簡單的 Trigger，只有一個用途：讓你在 Canvas 上手動測試。

```
[Manual Trigger] → ...
```

它不會自動觸發。你按「Execute Workflow」的時候，等同於 Manual Trigger 啟動了一次。

**什麼時候用**：開發、除錯、一次性任務（不需要自動觸發的流程）。

---

## Schedule Trigger

依照時間間隔或 Cron 表達式觸發。

### 簡單模式

在 Parameters 選觸發間隔：

| 設定 | 意義 |
|---|---|
| Minutes | 每 N 分鐘 |
| Hours | 每 N 小時 |
| Days | 每 N 天（可指定幾點） |
| Weeks | 每週幾（可指定幾點） |
| Months | 每月幾號（可指定幾點） |

### Cron 表達式

選「Custom (Cron)」可以用完整 Cron 語法：

```
┌────────── 分鐘 (0-59)
│ ┌──────── 小時 (0-23)
│ │ ┌────── 日 (1-31)
│ │ │ ┌──── 月 (1-12)
│ │ │ │ ┌── 星期幾 (0-6，0=週日)
│ │ │ │ │
* * * * *
```

常用範例：

| Cron | 意義 |
|---|---|
| `0 8 * * *` | 每天早上 8:00 |
| `0 9 * * 1` | 每週一早上 9:00 |
| `*/15 * * * *` | 每 15 分鐘 |
| `0 0 1 * *` | 每月 1 號午夜 |
| `0 8,12,18 * * *` | 每天 8:00、12:00、18:00 |

**注意時區**：n8n 的 Schedule Trigger 時區由環境變數 `GENERIC_TIMEZONE` 決定。如果 Ch 2 的 Docker Compose 設了 `Asia/Taipei`，這裡的時間就是台灣時間。

---

## Webhook Trigger

接收 HTTP 請求，當成觸發器。這讓 n8n 可以被外部服務主動呼叫。

### 設定

```
HTTP Method: POST（或 GET，視你的需求）
Path:        my-webhook   ← 自訂路徑，n8n 自動產生完整 URL
```

完整 URL 格式：
```
http://localhost:5678/webhook/my-webhook      ← 測試用
http://localhost:5678/webhook-test/my-webhook ← 只在「Listen for Test Event」模式啟用
```

### 兩種使用模式

**測試模式**（開發時用）：
1. 點 Webhook node 的 **Listen for Test Event**
2. 用 curl 或 Postman 發一個請求
3. n8n 接到後，你可以看到輸出資料

```bash
curl -X POST http://localhost:5678/webhook-test/my-webhook \
  -H "Content-Type: application/json" \
  -d '{"name": "Alice", "action": "signup"}'
```

**生產模式**：workflow 啟用後，webhook URL（不帶 `-test`）才會真正監聽。

### 輸出結構

Webhook 收到的請求，輸出 item 的 json 包含：

```json
{
  "headers": { "content-type": "application/json", ... },
  "params":  {},
  "query":   { "foo": "bar" },
  "body":    { "name": "Alice", "action": "signup" }
}
```

取 body 裡的 name：`{{ $json.body.name }}`

---

## App Trigger

n8n 有許多服務的專屬 Trigger node，讓你不用自己設 webhook，直接用 OAuth 認證就能監聽事件。

常用的 App Trigger：

| Trigger Node | 觸發事件 |
|---|---|
| **GitHub Trigger** | Push、PR、Issue 等 |
| **Gmail Trigger** | 收到新郵件（符合篩選條件）|
| **Google Sheets Trigger** | 試算表被修改 |
| **Slack Trigger** | 收到訊息 |
| **Typeform Trigger** | 有人填完表單 |
| **Stripe Trigger** | 付款成功 / 退款等 |

### 以 GitHub Trigger 為例

```
Events: push
Repository: owner/repo
```

設定後，每次有人 push 到那個 repo，workflow 就觸發，input item 包含 push 的詳細資訊（commit hash、作者、修改的檔案等）。

---

## 一個 Workflow 只能有一個起點

n8n 的 workflow **只能有一個 Trigger**（起點）。如果你需要「兩種觸發方式都能啟動同一流程」，有兩個做法：

1. 建兩個 workflow，用「Execute Workflow」node 呼叫同一個子 workflow
2. 用 Webhook + 自己在外部決定什麼時候呼叫

---

## 踩雷

**App Trigger 不跑**：大多數 App Trigger 是長輪詢或 webhook 模式，需要 workflow **啟用**，不是按「Execute Workflow」。

**Schedule Trigger 啟用後一直沒跑**：確認 Docker container 還在跑、時區設定對、n8n 服務沒有重啟後漏掉觸發。

**Webhook 沒反應**：確認請求打到了 production URL（沒帶 `-test`）、workflow 是啟用狀態。

---

## 自我檢核

- [ ] 知道 Manual / Schedule / Webhook / App Trigger 各自的使用場景
- [ ] 能寫 Cron 表達式設定「每週一早上 9:00」
- [ ] 知道 webhook 的測試 URL 和生產 URL 差在哪
- [ ] 知道 App Trigger 需要 workflow 啟用才會監聽

→ [Ch 7 條件分支 — If Node、Switch Node](./07-conditionals.md)
