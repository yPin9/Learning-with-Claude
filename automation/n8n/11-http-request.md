# Ch 11 — HTTP Request：呼叫任意 REST API

> 目標：能用 HTTP Request node 呼叫任何 REST API，包含設定各種認證方式和處理分頁。

## HTTP Request Node 是 n8n 的萬能工具

n8n 有 400+ 內建 node，但沒有你需要的 node 時，HTTP Request node 可以呼叫任何有 REST API 的服務。你用它的頻率可能比任何其他 node 都高。

---

## 基本設定

```
Method:          GET / POST / PUT / PATCH / DELETE
URL:             https://api.example.com/endpoint
Authentication:  [下方說明]
Headers:         [+ Add Header]
Body:            [視 Method 而定]
```

### GET 請求（帶 Query Params）

```
Method: GET
URL:    https://api.github.com/search/repositories

Query Parameters:
  q:        language:javascript stars:>1000
  sort:     stars
  per_page: 10
```

Query Params 可以在 Parameters tab 點「Add Query Parameter」，n8n 自動幫你 URL encode。

### POST 請求（JSON Body）

```
Method: POST
URL:    https://api.example.com/users

Body Content Type: JSON
Body:
{
  "name": "{{ $json.name }}",
  "email": "{{ $json.email }}"
}
```

Body 可以用 Expression 動態帶前一個 node 的資料。

---

## Authentication（認證）

### 無認證（None）

公開 API，如 wttr.in、httpbin.org。

### Header Auth

最常用的方式，很多 API 用 `Authorization: Bearer <token>` 或自訂 header：

在 n8n 建 credential：
```
Type: Header Auth
Name: X-API-Key       （或 Authorization）
Value: your-api-key   （或 Bearer your-token）
```

然後在 HTTP Request node 選這個 credential。

### API Key（Query Param）

有些 API 要求把 key 帶在 URL 後面：

```
Type: Query Auth
Name: api_key
Value: your-api-key
```

n8n 會自動把 `?api_key=your-api-key` 加到 URL。

### Basic Auth

```
Type: Basic Auth
Username: your-username
Password: your-password
```

### OAuth2

OAuth2 流程比較複雜，Ch 16 專門說。

---

## 處理 Response

### Response 格式

n8n 預設把 response body 解析成 JSON，輸出為 item 的 `json` 欄位。

如果 API 回傳 XML 或純文字，在 Options 設定：

```
Response Format: Text / XML / Binary
```

### 取得 Status Code

有時你需要根據 HTTP status code 做判斷：

```
Options:
  Response: ✅ Include Response Headers and Status
```

勾選後，輸出 item 的 json 會包含：

```json
{
  "statusCode": 200,
  "headers": { "content-type": "application/json", ... },
  "body": { ... }
}
```

---

## 分頁（Pagination）

很多 API 一次只回傳 100 筆，用分頁讓你取得全部資料。

### n8n 內建 Pagination 支援

在 Options → Pagination 開啟：

```
Pagination Mode: Response Contains Next URL
Next URL Expression: {{ $response.body.next_url }}
Max Pages: 10
```

常見分頁 API 回傳：

```json
{
  "data": [...],
  "next": "https://api.example.com/items?page=2",
  "has_more": true
}
```

設定：

```
Pagination Mode: Response Contains Next URL
Next URL: {{ $response.body.next }}
Limit Pages: ✅
Max Pages: 50
```

### Cursor-based Pagination

```
Pagination Mode: Update a Parameter
Parameter Location: Query Parameter
Parameter Name: cursor
Value: {{ $response.body.next_cursor }}
Continue Until: {{ $response.body.has_more }} is false
```

---

## 範例：呼叫 OpenAI API

建一個 credential（Header Auth）：

```
Name:  Authorization
Value: Bearer sk-your-openai-key
```

HTTP Request node：

```
Method: POST
URL:    https://api.openai.com/v1/chat/completions
Auth:   (選上面建的 credential)

Body (JSON):
{
  "model": "gpt-4o-mini",
  "messages": [
    { "role": "user", "content": "{{ $json.prompt }}" }
  ],
  "max_tokens": 500
}
```

取得回應文字：

```
{{ $json.choices[0].message.content }}
```

---

## 踩雷

**SSL 憑證錯誤**

開發環境或自簽憑證的 API：Options → SSL → 關閉 SSL Verification（生產環境別這樣做）。

**timeout**

預設 10 秒。長跑的 API 在 Options → Timeout 調大（單位 ms）。

**response 太大**

Options → Max Response Size 可以限制，避免把整個大型 response 都載入記憶體。

---

## 自我檢核

- [ ] 能發 GET 請求並帶 Query Parameters
- [ ] 能發 POST 請求並帶動態 JSON body
- [ ] 能設定 Header Auth 認證
- [ ] 能開啟 Pagination 自動翻頁
- [ ] 知道如何取得 response status code

→ [Ch 12 Webhook — 接收外部觸發、回應結果](./12-webhook.md)
