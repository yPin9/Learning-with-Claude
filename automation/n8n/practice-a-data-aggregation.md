# 練習 A — 資料聚合 Workflow

> 目標：把 Ch 5–10 的核心概念拼起來：多來源 HTTP 請求 → 資料整形 → 條件過濾 → 錯誤處理 → 輸出到 Google Sheets。

## 任務規格

你是一個小型技術 blog 的站長。每天你想知道今天的 **GitHub Trending** 有哪些新出現的 JavaScript 專案，並把清單存到 Google Sheets，同時排除 star 數低於 500 的專案。

**你必須建的 workflow：**

```
[Schedule Trigger: 每天 09:00]
         │
[HTTP Request: 抓 GitHub Trending API（見下方）]
         │
[Code Node: 解析資料，產生多個 item]
         │
[If Node: filter star >= 500]
  true ──┼──▶ [Edit Fields: 整形輸出欄位]
         │              │
         │    [Google Sheets: 寫入]
  false ──────▶ [丟棄（No Operation）]
         │
[Error Trigger Workflow: 若主 workflow 失敗，通知 Telegram]
```

---

## 資料來源說明

由於 GitHub Trending 沒有官方 API，使用這個第三方 API（免費，不需要 key）：

```
GET https://gh-trending-api.waverider.workers.dev/api/repositories?since=daily&language=JavaScript
```

這個 API 回傳今日 JavaScript trending repos，結構如下：

```json
[
  {
    "author": "microsoft",
    "name": "TypeScript",
    "url": "https://github.com/microsoft/TypeScript",
    "description": "TypeScript is a superset of JavaScript...",
    "stars": 100245,
    "forks": 12345,
    "currentPeriodStars": 1200,
    "language": "TypeScript",
    "builtBy": [ { "username": "...", "href": "..." } ]
  },
  ...
]
```

---

## 期望輸出

Google Sheets 裡每一列代表一個專案：

| date | repo | description | stars | today_stars | url |
|---|---|---|---|---|---|
| 2026-05-08 | microsoft/TypeScript | TypeScript is... | 100245 | 1200 | https://... |
| ... | ... | ... | ... | ... | ... |

只有 `currentPeriodStars`（今日新增 star 計算）>= 500 的專案才寫入。

---

## 實作步驟

### Step 1：設定 Schedule Trigger

每天 09:00 觸發，時區 Asia/Taipei。

### Step 2：HTTP Request Node

```
Method: GET
URL:    https://gh-trending-api.waverider.workers.dev/api/repositories?since=daily&language=JavaScript
```

API 直接回傳 JSON 陣列。**注意**：HTTP Request node 預設把整個 response 包成一個 item，你需要在下一步把陣列展開成多個 item。

### Step 3：Code Node（展開陣列 + 整形）

```javascript
// HTTP Request 的輸出是一個 item，其 json 是陣列
const repos = $input.first().json;

// 今天日期（台灣時區）
const today = new Date().toLocaleDateString("zh-TW", {
  timeZone: "Asia/Taipei",
  year: "numeric", month: "2-digit", day: "2-digit"
});

return repos.map(repo => ({
  json: {
    date:         today,
    repo:         `${repo.author}/${repo.name}`,
    description:  repo.description || "",
    stars:        repo.stars,
    today_stars:  repo.currentPeriodStars,
    url:          repo.url
  }
}));
```

### Step 4：If Node（過濾）

```
Value 1: {{ $json.today_stars }}
Operation: Greater Than or Equal
Value 2: 500
```

true 輸出繼續，false 接 No Operation node（或不接任何東西，item 自動丟棄）。

### Step 5：Edit Fields（確保欄位型別正確）

```
date:        {{ $json.date }}        （字串）
repo:        {{ $json.repo }}        （字串）
description: {{ $json.description }} （字串）
stars:       {{ $json.stars }}       （數字）
today_stars: {{ $json.today_stars }} （數字）
url:         {{ $json.url }}         （字串）
```

### Step 6：Google Sheets Node

你需要先在 n8n 建立 Google Sheets credential（OAuth2），步驟：

1. n8n 側欄 → Credentials → New → Google Sheets OAuth2
2. 依照設定精靈在 Google Cloud Console 建一個 OAuth app
3. 回到 n8n 完成授權

node 設定：
```
Resource:   Spreadsheet
Operation:  Append or Update Row
Document:   [選你的試算表]
Sheet:      Sheet1
Columns:    date, repo, description, stars, today_stars, url
```

### Step 7：建立錯誤通知 Workflow

新建一個 workflow（命名為「錯誤通知」）：

```
[Error Trigger]
      │
[Telegram: 
  Text: "⚠️ Workflow 失敗\n名稱：{{ $json.workflow.name }}\n錯誤：{{ $json.execution.error.message }}\n時間：{{ new Date().toLocaleString('zh-TW') }}"
]
```

啟用這個 workflow，然後回到主 workflow 的 Settings → Error Workflow，選這個通知 workflow。

---

## 完整參考解答

**先自己做完再看！**

<details>
<summary>展開參考實作</summary>

主要注意幾點：

1. HTTP Request 回傳的 body 可能是 JSON 字串而非直接是物件，如果 Code Node 裡 `$input.first().json` 取到的是字串而不是陣列，要先 `JSON.parse()`：

```javascript
let repos = $input.first().json;
if (typeof repos === 'string') repos = JSON.parse(repos);
```

2. Google Sheets 的欄位順序要和試算表的標題列順序一致。如果試算表是空的，Google Sheets node 的 Operation 改用「Append Row」，它會自動用 key 當標題。

3. 如果 API 掛了或回傳格式不同，Code Node 會拋錯。可以在 HTTP Request node 的 Settings 開 Continue On Fail，然後在 Code Node 開頭加：

```javascript
if ($input.first().json.error) {
  throw new Error("API 呼叫失敗：" + $input.first().json.error.message);
}
```

這樣錯誤訊息更清楚。

</details>

---

## 測試用例

1. **正常情況**：手動 Execute，確認 Google Sheets 有新增資料，且全部都是 today_stars >= 500 的專案。

2. **API 超時**：暫時把 URL 改成一個不存在的網址，確認主 workflow 失敗後，你的 Telegram 收到通知。

3. **全部被過濾**：把 If Node 的條件改成 today_stars >= 99999（一定沒有），確認 workflow 跑完但 Google Sheets 沒有新增資料（不是錯誤，只是 0 筆）。

---

## 自我檢核

- [ ] HTTP Request 的 response 陣列展開成多個 item 的方式
- [ ] If Node 過濾後，false 的 item 確實沒有進 Google Sheets
- [ ] 觸發錯誤時，Telegram 收到了正確的通知訊息
- [ ] 知道 Error Trigger workflow 需要「啟用」才會生效

恭喜完成 Part 2 的練習。接下來 Part 3 開始整合各種外部服務。

→ [Ch 11 HTTP Request — 呼叫任意 REST API](./11-http-request.md)
