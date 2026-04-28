# Ch 1 — HTTP 完整版

> 目標：把 HTTP 講透 — request/response 結構、method、status code、headers、cookies、session。後面每個 OWASP vuln 都建立在 HTTP 上。

## 為什麼 HTTP 是 web 安全的基礎

每個 web 攻擊本質上都是「**送一個特殊的 HTTP request 讓 server 做不該做的事**」：

- SQL injection — 修改 request 的 query parameter
- XSS — 把 payload 塞 request body / URL，讓 server 嵌入 response
- CSRF — 偷別人 cookie 發 request
- SSRF — 騙 server 發 request 到 internal

懂 HTTP 才能做攻擊 / 防禦。

## HTTP request 結構

```
GET /search?q=hello HTTP/1.1
Host: example.com
User-Agent: Mozilla/5.0
Accept: text/html
Cookie: session=abc123
Authorization: Bearer eyJhbGc...

(body, 可能空)
```

3 部分：

1. **Request line**：`<METHOD> <PATH> <HTTP-VERSION>`
2. **Headers**：name: value 多行
3. **Body**（可選）：POST/PUT 才有

## HTTP method

| Method | 用途 | Idempotent | 有 body |
|---|---|---|---|
| GET | 取資源 | ✓ | 不該有（雖然技術可以） |
| POST | 建/送 | ✗ | ✓ |
| PUT | 完整更新 | ✓ | ✓ |
| PATCH | 部份更新 | 通常 ✗ | ✓ |
| DELETE | 刪 | ✓ | 通常無 |
| HEAD | 同 GET 但只 header | ✓ | ✗ |
| OPTIONS | CORS preflight | ✓ | ✗ |

**安全相關陷阱**：

- **POST 比 GET 安全？** 部分對。GET 會被 log（URL 含 sensitive data 不行）；但 POST 一樣能被 MITM 看（除非 HTTPS）
- **Method 過濾不足**：API 只 allow GET 但 POST 也回應 → bypass auth check 常見
- **方法切換攻擊**：DELETE 沒做 auth 但 POST 做 → 用 `X-HTTP-Method-Override: DELETE` header bypass

## HTTP response

```
HTTP/1.1 200 OK
Content-Type: text/html
Content-Length: 1234
Set-Cookie: session=abc123; HttpOnly; Secure

<html>...</html>
```

## Status code 分類

| 範圍 | 類別 | 例 |
|---|---|---|
| 1xx | informational | 100 Continue |
| 2xx | success | 200 OK, 201 Created, 204 No Content |
| 3xx | redirect | 301, 302, 304 Not Modified |
| 4xx | client error | 400, 401, 403, 404, 429 |
| 5xx | server error | 500, 502, 503, 504 |

**安全相關 status code**：

- **401 Unauthorized**：沒登入
- **403 Forbidden**：登入了但無權
- **429 Too Many Requests**：rate limit
- **500 Internal Error**：server bug → 可能 leak info（stack trace / SQL 錯誤）

## Headers 完整

### Request headers（重要的）

| Header | 用途 | 安全相關 |
|---|---|---|
| `Host` | 哪個 domain | Host header injection |
| `User-Agent` | client 軟體 | UA-based attack |
| `Cookie` | session / state | session theft / CSRF |
| `Authorization` | 認證 token | token leak / replay |
| `Referer` | 來源頁 | CSRF / 隱私 leak |
| `X-Forwarded-For` | proxy chain 的 client IP | spoof IP |
| `Content-Type` | body 格式 | content-type confusion |

### Response headers（安全相關）

| Header | 用途 |
|---|---|
| `Set-Cookie` | 設 cookie，含 flags |
| `Content-Type` | response 格式 |
| `Strict-Transport-Security` (HSTS) | 強制 HTTPS |
| `Content-Security-Policy` (CSP) | 限 JS / resource source |
| `X-Frame-Options` | 防 clickjacking |
| `X-Content-Type-Options` | 禁 MIME sniffing |
| `Referrer-Policy` | 控 referer 洩漏 |
| `Permissions-Policy` | 限 browser API |
| `Access-Control-Allow-*` | CORS |

**production 必加全套 security header**。Ch 3 詳細。

## Cookie 完整

```
Set-Cookie: session=abc123; Path=/; Domain=example.com; Expires=...; HttpOnly; Secure; SameSite=Lax
```

### 屬性

| 屬性 | 意義 |
|---|---|
| `Path` | 哪個 path 才送 |
| `Domain` | 哪個 domain 才送 |
| `Expires` / `Max-Age` | 過期時間 |
| `HttpOnly` | JS 不能讀（防 XSS 偷 cookie） |
| `Secure` | 只 HTTPS 送 |
| `SameSite` | CSRF 防禦（Strict / Lax / None） |

**安全 cookie 應該**：

```
Set-Cookie: session=...; HttpOnly; Secure; SameSite=Strict
```

少一個 flag 就有風險：

- 沒 `HttpOnly` → XSS 能偷 cookie
- 沒 `Secure` → HTTP 連線時 cookie 明文傳
- 沒 `SameSite` → 跨站 request 自動帶 cookie → CSRF

## Session 機制

兩種主流：

### 1. Server-side session

```
client login → server 產生 session ID → 存 server (Redis/DB) → set cookie
client 後續 request → 帶 cookie → server 用 ID 查 user
```

### 2. JWT (JSON Web Token)

```
client login → server 產 JWT (含 user info + 簽章) → 給 client
client 後續 request → 帶 JWT → server 驗簽
```

**不需要 server 存 session**（stateless）。但 JWT 有自己的安全坑（簽章弱、算法切換、refresh token 設計），Ch 12 詳細。

## 觀察 HTTP

```bash
# curl verbose
curl -v https://example.com

# curl 看完整 headers
curl -I https://example.com    # HEAD only
curl -i https://example.com    # GET + headers

# tcpdump (HTTP 明文)
sudo tcpdump -nn -i any -A 'port 80'

# Wireshark filter
http
http.request
http.response.code == 500
```

## 一個常見誤解：「HTTPS 完全保密」

部分對。HTTPS 加密：

- request body
- request URL（含 query string）
- response body

HTTPS **不**加密：

- DNS 查詢（除非 DoH / DoT）
- SNI（連哪個 domain，2024 才開始有 ECH）
- IP / port / 連線時間 / 流量大小

ISP / 中間設備能看你**連了哪個 site，多大流量**，但看不到內容。

## 一個常見誤解：「POST request 送 sensitive data 比 GET 安全」

部分對。差別：

- GET：URL 中含資料 → 被 server log / browser history / referer 洩
- POST：資料在 body → 不被 log，但 MITM 一樣看

兩者**都需要 HTTPS** 才安全。「POST > GET」只在 logging 場景成立。

## 一個常見誤解：「同 domain = 同 origin」

**錯**。Origin = scheme + host + port：

- `http://example.com` ≠ `https://example.com` (scheme 不同)
- `example.com:80` ≠ `example.com:8080` (port 不同)
- `app.example.com` ≠ `api.example.com` (host 不同)

Same Origin Policy 嚴格按 origin。Ch 3 詳細。

## 一個常見誤解：「JWT 比 session ID 安全」

**錯**。JWT 有自己的問題：

- 簽章弱（HS256 share secret 洩漏）
- alg=none 攻擊（如果 server 不檢查）
- 沒辦法 revoke（除非加 blacklist）
- 容易塞 sensitive 資料進去（base64 不是加密）

各有適用場景，**選錯反而更不安全**。

## 動手練習

**1. curl 看 HTTP**

```bash
curl -v https://www.google.com 2>&1 | head -50
```

對應到本章 request / response 結構。

**2. Burp Proxy 攔 request**

開 Burp Proxy → browser 設 proxy → 訪問任何 site → Burp 看 HTTP history。

修改 request，重發（Send to Repeater）。

**3. 觀察自己的 cookie**

```bash
# 在 Browser dev tools → Application → Cookies
# 看常用 site 有哪些 cookie，哪些 flag 設了
```

**4. 看安全 header（或缺）**

```bash
curl -I https://example.com
curl -I https://github.com
curl -I https://your-favorite-site.com
```

對比有哪些 security header。多數沒設全。

**5. 改 method 試試**

```bash
# 對 Juice Shop 用不同 method
curl -X DELETE http://localhost:3000/api/users/1
curl -X PATCH http://localhost:3000/api/users/1 -d '{}'
curl -X OPTIONS http://localhost:3000/
```

看 server 怎麼回應。

## 自我檢核

- [ ] HTTP request / response 結構畫得出
- [ ] 7+ 個 method 用途記得
- [ ] 主要 status code 5xx / 4xx 含意
- [ ] Cookie 5 個 flag 各自意義（特別 HttpOnly / Secure / SameSite）
- [ ] Server-side session vs JWT 差別
- [ ] Same Origin 完整定義（scheme + host + port）
- [ ] 用 curl + Burp 各看過 HTTP

下一章看 web 架構 — 攻擊面在哪。

→ [Ch 2 Web 架構速覽](./02-web-architecture.md)
