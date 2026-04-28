# Ch 2 — Web 架構速覽

> 目標：建立現代 web app 的全景，知道攻擊面分佈在哪裡。

## 經典 web app 架構

```
              ┌─────────────────────┐
              │     Browser         │  ← 用戶
              │  (HTML/CSS/JS)      │
              └──────────┬──────────┘
                         │ HTTP / HTTPS
              ┌──────────┴──────────┐
              │   CDN / WAF         │  ← 邊緣防禦 + 加速
              │   (Cloudflare)      │
              └──────────┬──────────┘
                         │
              ┌──────────┴──────────┐
              │  Load Balancer      │  ← 流量分配
              │  (nginx / ELB)      │
              └──────────┬──────────┘
                         │
              ┌──────────┴──────────┐
              │  Web Server         │  ← reverse proxy
              │  (nginx / Apache)   │
              └──────────┬──────────┘
                         │
              ┌──────────┴──────────┐
              │  App Server         │  ← business logic
              │  (Node/Python/Go)   │
              └──────┬─────┬────────┘
                     │     │
            ┌────────┘     └─────────┐
            ▼                        ▼
       ┌─────────┐              ┌─────────┐
       │   DB    │              │  Cache  │
       │ Postgres│              │  Redis  │
       └─────────┘              └─────────┘
```

每層都有自己的攻擊面。

## 主要架構模式

### 1. Server-Side Rendering (SSR)

server 產生完整 HTML，browser 只 render。

例：傳統 PHP / Rails / Django app

```
client → server: GET /users/1
server → DB: SELECT * FROM users WHERE id=1
server: 嵌入資料到 HTML template
client ← server: <html>...with user data...</html>
```

特點：
- HTML 含完整內容（SEO 友善）
- 攻擊面集中在 server-side
- 老但穩定

### 2. Single Page Application (SPA)

server 給 1 頁 HTML + JS bundle，後續純 JS API call。

例：React / Vue / Angular app

```
client → server: GET /
server → client: <html>...只有 1 個 <div>...</html> + JS
JS → API: GET /api/users/1
API → JS: {id: 1, name: ...}
JS: 用 React 等 render
```

特點：
- 前端 client heavy
- 後端是 REST / GraphQL API
- 攻擊面在前端（XSS）+ API（IDOR / auth）
- SEO 較難（需 SSR + hydration）

### 3. Server Components / Hybrid

新架構（Next.js / Remix）— SSR + 部分互動 client-side。

折中。

## API 風格

### REST

最常見。每個 resource 一個 URL：

```
GET    /api/users          → 列 users
GET    /api/users/123      → 看 user 123
POST   /api/users          → 建 user
PUT    /api/users/123      → 更新 user 123
DELETE /api/users/123      → 刪 user 123
```

優點：標準、易懂  
缺點：over-fetching（拿太多 / 太少資料）

### GraphQL

client 自己描述要什麼資料：

```graphql
query {
  user(id: 123) {
    name
    email
    posts {
      title
    }
  }
}
```

優點：精準取資料、減少 round-trip  
缺點：複雜、攻擊面新（Ch 21 講 GraphQL 安全）

### gRPC

binary protocol（Protobuf）+ HTTP/2，主要服務間通信。對外 API 較少。

## Cookie / Session 認證流程

```
1. user 登入：
   client: POST /login {user, pass}
   server: 驗證 → 產生 session ID（隨機）
   server: 存 session_id → user_id 對應到 Redis
   server: Set-Cookie: session=abc123; HttpOnly

2. 後續 request：
   client: GET /profile  (帶 Cookie: session=abc123)
   server: 從 Redis 查 abc123 → user_id 5
   server: 取 user 5 的資料 → 回應
```

**安全要點**：

- session ID 必須**夠長 + 隨機**（128+ bit entropy）
- 過期 / logout 要 server 端 invalidate
- cookie 必加 HttpOnly + Secure + SameSite

## JWT 認證流程

```
1. user 登入：
   client: POST /login {user, pass}
   server: 驗證 → 產 JWT (header + payload + signature)
   server: 回 JWT (or set cookie)

2. 後續 request：
   client: GET /profile (Authorization: Bearer eyJ...)
   server: 驗 signature → 信 payload 中的 user_id
   server: 回應
```

JWT 結構：

```
eyJhbGc...  .  eyJzdWI...  .  SflKxw...
   ↑              ↑              ↑
 header         payload      signature
 base64         base64       (HMAC or RSA sign)
```

header 含算法（`alg: HS256` / `RS256` / `none`）。  
payload 含 claims（user_id / exp / iss / ...）。  
signature = sign(header.payload, secret)

**安全坑**：

- payload base64 可被 client 看到（**不是加密**）
- alg=none 攻擊（server 沒 verify）
- HS256 secret 弱（暴力破解）
- key confusion（HS256 用 RSA public key 簽）

## OAuth / SSO

第三方登入（用 Google 登入 my-app）：

```
1. user 點 "Login with Google"
2. my-app redirect → Google
3. user 在 Google 登入
4. Google redirect 回 my-app（帶 authorization code）
5. my-app server → Google: 換 access token
6. my-app: 用 access token 取 user info
7. my-app 建 session
```

OAuth 2.0 flow 多種（Authorization Code / Implicit / PKCE / Client Credentials），各有適用。

## 攻擊面分佈

```
                   攻擊面分佈
                   
 Browser:          XSS / clickjacking / 本地 storage 偷
 ─────
 HTTPS:            cert 配置 / TLS 弱
 ─────
 CDN/WAF:          bypass / cache poisoning
 ─────
 LoadBalancer:     IP 露 / Host header injection
 ─────
 Web Server:       directory traversal / log injection
 ─────
 App Server:       SQL injection / SSRF / RCE / business logic
 ─────
 DB:               default cred / 暴露
 Cache:            poisoning
```

每章後面拆每個攻擊面。

## 常見 web framework

| Framework | 語言 | 特色 |
|---|---|---|
| Express / Fastify | Node.js | 輕量 |
| Django / Flask | Python | 老牌 |
| Rails | Ruby | DSL 強 |
| Spring | Java | 企業 |
| Laravel | PHP | 友善 |
| ASP.NET | C# | Microsoft |
| Next.js | React/Node | 全棧 |

每個 framework 有預設安全機制（CSRF token、XSS escape、SQL prepared statement 等）。**用 framework default 就避開 80% 漏洞**。

## 一個常見誤解：「SPA 比 SSR 安全」

**錯**。各有問題：

- SPA：DOM XSS / 前端 secret 洩 / API 暴露
- SSR：traditional XSS / SQL injection / template injection

SPA 把攻擊面從 server 移到「前端 + API」，不是「**消滅了**」。

## 一個常見誤解：「JWT 比 session 安全」

**錯**。JWT 有自己的安全問題（前一章講過）。

「**正確使用**」的 session 跟 JWT 都安全。**錯誤使用**的 JWT 比 session 還危險（沒辦法 revoke、簽章弱）。

## 一個常見誤解：「用 framework 就自動安全」

**部分對**。framework 防 80% 常見漏洞（XSS escape、CSRF token、prepared statement），但**業務邏輯漏洞 framework 救不了**：

- IDOR
- Broken Access Control
- 業務流程繞過

**Insecure Design (A04)** 整個就是「**framework 不能救你**」。

## 動手練習

**1. 拆 Juice Shop 架構**

訪問 Juice Shop，用 browser dev tools / Wappalyzer 看：

- 用什麼 framework？（Angular SPA + Express API）
- API endpoint 列表？
- 認證機制？（JWT / session）

**2. 看不同類型 API**

```bash
# Juice Shop REST API
curl http://localhost:3000/api/Products

# GraphQL playground（如果有）
# 訪問 /graphql
```

**3. 解碼 JWT**

```bash
# 從 Juice Shop 登入後拿 JWT
# 用 jwt.io 解碼看 payload
```

**4. 用 curl 模擬 OAuth 流程**

研究 GitHub OAuth doc，用 curl 模擬登入流程：

- redirect 拿 code
- 換 access token
- 用 token 取 user info

## 自我檢核

- [ ] 經典 web app 架構畫得出
- [ ] SSR vs SPA 攻擊面差異清楚
- [ ] REST / GraphQL / gRPC 各自定位
- [ ] Session vs JWT 認證流程清楚
- [ ] OAuth 基本流程
- [ ] 知道 framework 不能救業務邏輯漏洞

下一章看 Browser 安全模型 — Same-Origin / CORS / CSP 完整版。

→ [Ch 3 Browser 安全模型](./03-browser-security-model.md)
