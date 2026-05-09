# Ch 14 — 通訊整合：Email / Telegram / Slack / Discord

> 目標：能讓 workflow 透過四種常見通訊管道發送訊息和通知。

## 為什麼通訊整合是核心場景

自動化 workflow 的終點，很多時候就是「通知某人」。不管是：

- 每日報表 → 發 Email
- 監控警報 → 發 Telegram
- CI/CD 狀態 → 發 Slack
- 遊戲/社群機器人 → 發 Discord

這章覆蓋四種最常用的通訊管道。

---

## Email：Send Email Node

### Credential 設定（SMTP）

```
Host:     smtp.gmail.com（Gmail）/ smtp.mailgun.org（Mailgun）
Port:     587（TLS）/ 465（SSL）
Secure:   TLS（587 用 STARTTLS）
User:     your@gmail.com
Password: Gmail App Password（不是帳號密碼！）
```

**Gmail 特別注意**：Gmail 要求你建立「應用程式密碼」（App Password），不能用一般帳號密碼。去 Google 帳號安全性 → 兩步驟驗證 → 應用程式密碼 → 建立。

### Node 設定

```
To:         {{ $json.email }}
From Email: no-reply@yourdomain.com
Subject:    訂單 #{{ $json.order_id }} 確認
Email Type: HTML（或 Text）
HTML:
  <h2>感謝您的訂購！</h2>
  <p>訂單編號：<strong>{{ $json.order_id }}</strong></p>
  <p>金額：{{ $json.amount }} 元</p>
```

HTML 支援完整 HTML 語法，包含樣式。

### 帶附件

在 Node 的 Attachments 設定：

```
Attachment Type: Binary
Binary Property: data   ← 前一個 node 輸出的 binary 欄位名稱
File Name: report.pdf
```

---

## Telegram：Telegram Node

Ch 4 用過。補充幾個常用功能：

### 傳送圖片或檔案

```
Resource:   Photo / Document / Audio / Video
Operation:  Send Photo
Chat ID:    {{ $json.chat_id }}
Binary Data: ✅
Binary Property: image
```

### 按鈕（Inline Keyboard）

```
Resource:    Message
Operation:   Send Message
Text:        你要選哪個選項？
Reply Markup:
  Keyboard Type: Inline Keyboard
  Buttons:
    Row 1:
      - Text: ✅ 確認   Callback: confirm
      - Text: ❌ 取消   Callback: cancel
```

要接收按鈕的點擊，用 **Telegram Trigger**（選 Callback Query）。

### 傳送 Markdown

```
Text: *粗體* _斜體_ `程式碼`
Parse Mode: Markdown
```

---

## Slack：Slack Node

### Credential 設定（OAuth2）

1. 去 https://api.slack.com/apps 建一個 App
2. OAuth & Permissions → Bot Token Scopes → 加 `chat:write`、`channels:read`
3. Install App to Workspace → 取得 Bot User OAuth Token
4. 在 n8n 建 Slack credential，填入 token

### 發送訊息

```
Resource:  Message
Operation: Post
Channel:   #general（或 Channel ID）
Text:      :rocket: 部署成功！版本：{{ $json.version }}
```

### 帶 Block 排版

Slack 的 Block Kit 可以做出更豐富的訊息格式：

```
Blocks:
[
  {
    "type": "section",
    "text": { "type": "mrkdwn", "text": "*部署結果*" }
  },
  {
    "type": "section",
    "fields": [
      { "type": "mrkdwn", "text": "*版本*\n{{ $json.version }}" },
      { "type": "mrkdwn", "text": "*狀態*\n✅ 成功" }
    ]
  }
]
```

---

## Discord：Discord Node

### Credential 設定（Webhook）

Discord 有兩種接入方式：Bot Token 或 Webhook URL。Webhook 最簡單：

1. Discord 伺服器 → 頻道設定 → 整合 → Webhook → 新增 Webhook
2. 複製 Webhook URL
3. n8n Credential → Discord → Webhook → 填入 URL

### 發送訊息

```
Resource:         Message
Operation:        Send
Content:          部署完成 🚀 版本：{{ $json.version }}
```

### 帶 Embed

```
Embeds:
  Title: 部署報告
  Description: 版本 {{ $json.version }} 成功部署
  Color: #00ff00  ← 綠色
  Fields:
    - Name: 時間   Value: {{ new Date().toLocaleString('zh-TW') }}
    - Name: 環境   Value: Production
```

---

## 多頻道通知策略

同一個 workflow 需要同時發多個管道：

```
[Error detected]
       │
       ├──▶ [Telegram: 即時告警]
       ├──▶ [Slack: 團隊討論頻道]
       └──▶ [Email: 存檔記錄]
```

在 n8n，同一個 node 可以接多條輸出線（fanout），三個 node 並行執行。

---

## 自我檢核

- [ ] 能設定 Gmail SMTP credential 並發送 HTML email
- [ ] 知道 Telegram 的 parse_mode 差別（Markdown vs HTML）
- [ ] 能設定 Slack Bot Token credential 並發到指定頻道
- [ ] 能設定 Discord Webhook 並發 embed 訊息
- [ ] 知道 fanout 模式（一個 node 輸出到多個並行 node）

→ [Ch 15 雲端文件 — Google Sheets、Notion、Airtable](./15-cloud-docs.md)
