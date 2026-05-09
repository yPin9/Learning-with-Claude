# Ch 12 — Webhook：接收外部觸發、回應結果

> 目標：能讓外部服務（GitHub、Stripe、表單）主動呼叫你的 n8n workflow，並回傳自訂的 HTTP 回應。

## Webhook 的角色

到目前為止，都是 n8n 主動去呼叫外部 API。Webhook 反過來：**外部服務主動呼叫 n8n**。

```
外部服務                    n8n
   │                         │
   │  POST /webhook/my-hook  │
   │────────────────────────▶│
   │                         │ 觸發 workflow
   │                         │ 執行各個 node
   │                         │
   │  HTTP 200 OK            │
   │◀────────────────────────│
```

典型場景：

- GitHub push → 觸發 n8n → 自動 deploy
- Stripe 付款成功 → 觸發 n8n → 寄確認信 + 建訂單記錄
- 自製表單送出 → 觸發 n8n → 存到資料庫 + 通知業務員

---

## Webhook Node 設定

### 基本設定

```
HTTP Method:  POST（或 GET，視外部服務要求）
Path:         order-created    ← 自訂路徑
```

n8n 自動生成完整 URL：

```
生產用：  http://your-domain:5678/webhook/order-created
測試用：  http://localhost:5678/webhook-test/order-created
```

### 接收資料的位置

Webhook 收到的請求，輸出 item 包含：

```json
{
  "headers": {
    "content-type":  "application/json",
    "x-github-event": "push"
  },
  "params":   {},
  "query":    { "source": "email-campaign" },
  "body":     { "order_id": "123", "amount": 500 }
}
```

取 body 裡的欄位：`{{ $json.body.order_id }}`
取 header：`{{ $json.headers["x-github-event"] }}`
取 query param：`{{ $json.query.source }}`

---

## 回應 Webhook 呼叫者

### 預設行為

預設 Webhook node 在 workflow **開始執行後立即**回傳 `200 OK`（空 body），不等 workflow 跑完。

適合大多數場景：你不需要讓呼叫者等待結果。

### 自訂回應：Respond to Webhook Node

如果你需要讓呼叫者得到你計算後的結果（例如：表單送出 → 驗證 → 回傳是否成功），用 **Respond to Webhook** node。

先在 Webhook node 設定：

```
Respond: Using 'Respond to Webhook' Node
```

然後在 workflow 的適當位置接上 Respond to Webhook node：

```
Response Code:  200
Response Body:
{
  "success": true,
  "message": "訂單 #{{ $json.order_id }} 已收到"
}
```

直到這個 node 執行到，Webhook 的 HTTP 連線才會回應呼叫者。

**注意**：這個模式下，呼叫者會一直等到 Respond to Webhook node 執行，如果 workflow 很慢，呼叫者可能 timeout。

---

## Webhook 安全：驗證請求來源

公開的 Webhook URL 任何人都可以打。要驗證請求確實來自你信任的服務：

### 方法一：Webhook 內建 Authentication

在 Webhook node 的 Authentication 選：

```
Authentication: Header Auth
Header Name:    X-Webhook-Secret
Header Value:   your-secret-token
```

n8n 會自動比對 header，不符合直接拒絕（回傳 401）。

### 方法二：HMAC 簽章驗證（GitHub / Stripe 用）

GitHub 和 Stripe 會在 header 帶簽章，你需要自己驗。

```javascript
// Code Node
const crypto = require('crypto');
const secret  = 'your-webhook-secret';
const payload = JSON.stringify($input.first().json.body);
const sigHeader = $input.first().json.headers['x-hub-signature-256'];

const expectedSig = 'sha256=' + crypto
  .createHmac('sha256', secret)
  .update(payload)
  .digest('hex');

if (sigHeader !== expectedSig) {
  throw new Error('Webhook 簽章驗證失敗');
}

return $input.all();
```

---

## 測試 Webhook

本地測試流程：

1. 在 Webhook node 點 **Listen for Test Event**（node 進入監聽模式）
2. 打開終端機，用 curl 發一個請求：

```bash
curl -X POST http://localhost:5678/webhook-test/order-created \
  -H "Content-Type: application/json" \
  -d '{"order_id": "123", "amount": 500, "status": "paid"}'
```

3. n8n 收到後，Webhook node 顯示輸入資料，你可以繼續設定後面的 node

**生產模式**：workflow 啟用後，改打 `/webhook/order-created`（去掉 `-test`）。

---

## 對外暴露 Webhook（本地開發）

本地的 `localhost:5678` 外部打不到。用 ngrok：

```bash
ngrok http 5678
# → https://abc123.ngrok-free.app
```

Webhook URL 就是 `https://abc123.ngrok-free.app/webhook/order-created`。

把這個 URL 填入 GitHub / Stripe 的 webhook 設定頁面。

---

## 自我檢核

- [ ] 能設定一個 POST Webhook 並取得 body 資料
- [ ] 知道測試 URL 和生產 URL 的差異
- [ ] 能用 Respond to Webhook node 回傳自訂 response
- [ ] 知道 HMAC 簽章驗證的用途

→ [Ch 13 資料庫整合 — Postgres / MySQL / SQLite](./13-database.md)
