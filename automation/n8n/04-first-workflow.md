# Ch 4 — 第一個 Workflow：每天早上自動抓天氣發通知

> 目標：建一個完整可跑的 workflow，每天早上 8 點抓台北天氣，把溫度和天氣狀況發到 Telegram。

## 這個 Workflow 做什麼

```
[Schedule Trigger]
  每天 08:00
       │
       ▼
[HTTP Request]
  GET wttr.in/Taipei?format=j1
  （免費天氣 API，不需要 key）
       │
       ▼
[Code]
  從 JSON 取出溫度、天氣描述
  組成一段文字
       │
       ▼
[Telegram]
  把文字發到你的頻道
```

這個 workflow 覆蓋了 n8n 最常見的四種操作：定時觸發、HTTP 請求、資料處理、發送通知。

---

## Step 1：設定 Schedule Trigger

新建 workflow，新增 **Schedule Trigger** node。

Parameters 設定：

```
Trigger Interval: Days
Days Between Triggers: 1
Trigger at Hour: 8
Trigger at Minute: 0
```

這表示每天 08:00 觸發一次。

> Schedule Trigger **只有在 workflow 啟用後才會真正依時間觸發**。手動測試時，直接按「Execute Workflow」就能手動跑一次，不用等到 08:00。

---

## Step 2：呼叫天氣 API

接上 **HTTP Request** node。

`wttr.in` 是一個免費的天氣查詢服務，不需要 API key：

```
Method: GET
URL:    https://wttr.in/Taipei?format=j1
```

按 **Test step** 看輸出，你應該會看到類似這樣的 JSON：

```json
{
  "current_condition": [
    {
      "FeelsLikeC": "28",
      "humidity": "85",
      "temp_C": "30",
      "weatherDesc": [
        { "value": "Partly cloudy" }
      ]
    }
  ],
  "weather": [
    {
      "date": "2026-05-08",
      "maxtempC": "33",
      "mintempC": "27"
    }
  ]
}
```

---

## Step 3：用 Code Node 整理資料

接上 **Code** node，切換到 JavaScript 模式。

```javascript
// 取得上一個 node 傳來的第一個 item 的 JSON 資料
const data = $input.first().json;

const current = data.current_condition[0];
const today   = data.weather[0];

const temp        = current.temp_C;
const feelsLike   = current.FeelsLikeC;
const humidity    = current.humidity;
const description = current.weatherDesc[0].value;
const maxTemp     = today.maxtempC;
const minTemp     = today.mintempC;

const message = `🌤 台北天氣日報
今日天氣：${description}
現在溫度：${temp}°C（體感 ${feelsLike}°C）
今日區間：${minTemp}°C ~ ${maxTemp}°C
濕度：${humidity}%`;

// 回傳新的 item 陣列
return [{ json: { message } }];
```

按 **Test step**，輸出應該是：

```json
{
  "message": "🌤 台北天氣日報\n今日天氣：Partly cloudy\n現在溫度：30°C（體感 28°C）\n今日區間：27°C ~ 33°C\n濕度：85%"
}
```

---

## Step 4：設定 Telegram

### 先拿到 Telegram Bot Token

1. 在 Telegram 搜尋 **@BotFather**
2. 傳 `/newbot`
3. 依序填 bot 名稱和 username（username 必須以 `bot` 結尾）
4. BotFather 會給你一串 token，格式類似 `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`

### 拿到你的 Chat ID

1. 搜尋 **@userinfobot**
2. 傳任何一則訊息
3. 它會回覆你的 User ID，記下來

或者訂閱你自己的 bot 後，打開瀏覽器訪問：
```
https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
```
發一則訊息給 bot，然後重新打開這個 URL，從 `message.chat.id` 取得 ID。

### 設定 Telegram Node

接上 **Telegram** node：

```
Credential: 點「Create New」→ 填入 Bot Token
Resource:   Message
Operation:  Send Message
Chat ID:    你的 Chat ID（負數是群組，正數是個人）
Text:       {{ $json.message }}
```

`{{ $json.message }}` 是 n8n 的 Expression 語法，取上一個 node 輸出的 `message` 欄位。

---

## Step 5：執行測試

點頂部 **Execute Workflow**，整條 workflow 從頭跑一次。

如果一切正常，你的 Telegram 會收到天氣訊息。

**如果某個 node 出錯**：點那個 node 的紅色邊框，右側會顯示錯誤訊息。常見問題：

| 錯誤 | 原因 |
|---|---|
| `401 Unauthorized` | Telegram token 填錯 |
| `400 Bad Request: chat not found` | Chat ID 填錯，或你還沒對 bot 傳過訊息 |
| `Cannot read property of undefined` | Code node 裡取的路徑不對，檢查 JSON 結構 |

---

## Step 6：啟用 Workflow

測試沒問題後，點頂部的啟用開關（Inactive → Active）。

明天早上 08:00 它會自動跑。你可以在 Executions 頁面看到執行記錄。

---

## 常見誤解

**「我啟用 workflow 了，但還是沒收到訊息」**

確認：
1. workflow 的啟用開關是 Active（綠色）
2. Docker container 還在跑（`docker compose ps`）
3. Schedule Trigger 的時間設定正確
4. Execution Log 裡有沒有失敗的紀錄

**「Code Node 改了，但執行結果好像沒變」**

按 Ctrl+S 存檔再跑。n8n 不會自動儲存。

---

## 動手延伸

把 Telegram 換成 Email 或 Discord 也很簡單，node 名稱分別是 `Send Email` 和 `Discord`，操作邏輯一樣：填 credential + Chat ID / To 地址 + 訊息內容。

## 自我檢核

- [ ] 能設定 Schedule Trigger 的觸發時間
- [ ] 知道 `$input.first().json` 是什麼意思
- [ ] 能在 Code Node 裡讀取前一個 node 的輸出並產生新的 item
- [ ] 能在 Telegram Node 裡用 Expression 引用前一個 node 的欄位
- [ ] 知道 workflow 啟用後才會自動排程，手動測試用「Execute Workflow」

Part 1 到這裡結束。接下來進入核心概念，搞清楚 n8n 到底怎麼傳資料。

→ [Ch 5 Data Model — Item、JSON 結構、Binary Data](./05-data-model.md)
