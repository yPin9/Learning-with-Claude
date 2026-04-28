# Ch 3 — Browser 安全模型

> 目標：完整搞懂 Same-Origin Policy / CORS / CSP / SRI / cookie 屬性 — 這些是 browser 端安全的全部基礎。

## Same-Origin Policy (SOP)

Browser 的核心安全機制：「**A origin 的 JS 不能讀 B origin 的東西**」。

### Origin 定義

Origin = **scheme + host + port**：

| URL A | URL B | Same Origin? |
|---|---|---|
| http://example.com | https://example.com | ❌ scheme 不同 |
| http://example.com | http://api.example.com | ❌ host 不同 |
| http://example.com:80 | http://example.com:8080 | ❌ port 不同 |
| http://example.com/page1 | http://example.com/page2 | ✅ |
| http://example.com | http://example.com:80 | ✅（80 是 default） |

### SOP 限制什麼

A origin 的 JS：
- ❌ 不能讀 B origin 的 DOM
- ❌ 不能讀 B origin 的 cookie
- ❌ 不能讀 B origin 的 fetch / XHR response
- ✅ 但**能送 request** 到 B origin（response 不能讀）
- ✅ 能 embed B origin 的 image / script / iframe（但不能讀內容）

「**送 request 但不能讀 response**」是 CSRF 的根源。

## CORS（Cross-Origin Resource Sharing）

SOP 太嚴 → 現代 web 需要跨 origin 取資料（前端 SPA call 不同 domain 的 API）。

CORS 是 server **明確說「我允許這個 origin 讀我的資料」**的機制。

### 簡單 request

GET / HEAD / POST + 限制 headers + content-type 限制 → 簡單 request。

```
client (https://app.example.com): fetch('https://api.example.com/data')

browser → server:
  GET /data HTTP/1.1
  Origin: https://app.example.com

server → browser:
  HTTP/1.1 200 OK
  Access-Control-Allow-Origin: https://app.example.com
  Content-Type: application/json
  
  {...data...}

browser: Origin 在 ACAO 裡 → 讓 JS 讀
```

server 沒回 ACAO → browser 不讓 JS 讀（即使資料已經到）。

### Preflight request

複雜 request（PUT / DELETE / 自訂 header / non-simple content type）→ browser 先送 OPTIONS preflight：

```
browser → server:
  OPTIONS /data HTTP/1.1
  Origin: https://app.example.com
  Access-Control-Request-Method: PUT
  Access-Control-Request-Headers: Authorization

server → browser:
  HTTP/1.1 204 No Content
  Access-Control-Allow-Origin: https://app.example.com
  Access-Control-Allow-Methods: GET, POST, PUT, DELETE
  Access-Control-Allow-Headers: Authorization
  Access-Control-Max-Age: 86400

browser: OK，送真 request
browser → server:
  PUT /data HTTP/1.1
  Origin: ...
  Authorization: Bearer ...
  ...
```

## CORS 安全坑

### 1. `Access-Control-Allow-Origin: *`

「**任何 origin 都能讀**」 → 對 public API OK，對 sensitive 資料**災難**。

```
server: Access-Control-Allow-Origin: *
attacker.com 的 JS: fetch('https://victim.com/api/me') → 讀到 victim 資料？
```

不對 — `*` 不能配 `Access-Control-Allow-Credentials: true`（cookie 不會送）。

但如果**API 用 token 在 header**而不是 cookie → `*` + token 一起就 leak 了。

### 2. Origin reflection

```
server: 收到 Origin: evil.com → 直接 echo 回去
        Access-Control-Allow-Origin: evil.com
        Access-Control-Allow-Credentials: true
```

任何 origin 都能讀！**致命錯誤**。

### 3. Wildcard 子域

```
ACAO: *.example.com
```

但 `*.example.com` 不是合法 wildcard。某些實作把它當 `evil.example.com` 也允許 → 攻擊者註冊 `attacker.example.com.evil.com` → 規則繞過。

## Cookie SameSite

Cookie 預設「**同 domain 自動帶**」 — 即使是 evil.com 的 JS 送 request 到 victim.com，victim.com 的 cookie 自動帶上 → CSRF。

`SameSite` 屬性：

| 值 | 行為 |
|---|---|
| `Strict` | 跨站完全不帶 cookie |
| `Lax`（現代 default） | top-level navigation 才帶（GET 跟著 link click 算） |
| `None` | 一律帶（必須配 Secure） |

**現代 browser 預設 Lax**（Chrome 80+）。

但仍有 case：

- 老站 explicit 設 `None`
- IE / Safari 不一樣
- top-level GET（href click）還是帶 → 某些 GET 不該改 state，但常見有

## Content Security Policy (CSP)

「**告訴 browser 哪些 source 的 JS / CSS / image 才能 load**」。對抗 XSS 神器。

```
Content-Security-Policy: default-src 'self'; script-src 'self' https://cdn.example.com; style-src 'self' 'unsafe-inline'
```

讀法：

- `default-src 'self'` — 預設只 load 自己 origin 的 resource
- `script-src 'self' https://cdn.example.com` — JS 只能從自己 + CDN
- `style-src 'self' 'unsafe-inline'` — CSS 自己 + inline（`unsafe-inline` 不好）

### CSP 防 XSS 的原理

XSS 的 payload `<script>alert(1)</script>` → CSP 不允許 inline script → browser block。

```
Refused to execute inline script because it violates the following Content Security Policy directive: "script-src 'self'"
```

### CSP 常見 directive

| Directive | 控制 |
|---|---|
| `default-src` | fallback |
| `script-src` | JS source |
| `style-src` | CSS source |
| `img-src` | image source |
| `connect-src` | XHR / fetch / WebSocket destination |
| `font-src` | font |
| `frame-src` | iframe |
| `frame-ancestors` | 誰能 iframe 我（取代 X-Frame-Options） |
| `form-action` | form submit destination |
| `upgrade-insecure-requests` | HTTP → HTTPS |
| `report-uri` / `report-to` | violation 報告到哪 |

### CSP 弱 source

- `'unsafe-inline'` — 允許 inline script / style（**XSS 大開門**）
- `'unsafe-eval'` — 允許 eval()
- `*` — 任何 source
- `data:` — data URI（攻擊者能塞 base64 payload）

**理想 CSP**：

```
script-src 'self' 'nonce-RANDOM_NONCE';
```

每次 server 產生隨機 nonce、CSP 內含、HTML 中 `<script nonce="RANDOM_NONCE">`。攻擊者注入的 inline script 沒 nonce → block。

## Subresource Integrity (SRI)

從 CDN load JS / CSS 時，怕 CDN 被攻擊改檔案。SRI 加 hash 驗證：

```html
<script src="https://cdn.example.com/lib.js"
        integrity="sha384-abc123..."
        crossorigin="anonymous"></script>
```

browser 下載後算 hash → 不對 → 不執行。

## X-Frame-Options（已被 frame-ancestors 取代）

防 clickjacking：

```
X-Frame-Options: DENY
X-Frame-Options: SAMEORIGIN
X-Frame-Options: ALLOW-FROM https://example.com
```

新 site 用 CSP `frame-ancestors`：

```
Content-Security-Policy: frame-ancestors 'none';
```

## 完整安全 header 範本

```
# 強制 HTTPS
Strict-Transport-Security: max-age=63072000; includeSubDomains; preload

# CSP
Content-Security-Policy: default-src 'self'; script-src 'self' 'nonce-XXX'; ...

# 防 clickjacking
X-Frame-Options: DENY

# 禁 MIME sniffing
X-Content-Type-Options: nosniff

# Referrer 控制
Referrer-Policy: strict-origin-when-cross-origin

# Permissions
Permissions-Policy: camera=(), microphone=(), geolocation=()

# Cookie
Set-Cookie: session=...; HttpOnly; Secure; SameSite=Strict
```

production 必加全套。

## 一個常見誤解：「CORS 防駭」

**錯**。CORS **不是 server 防駭機制**，是 browser 強制的「**讓 server 控制 cross-origin 讀取**」機制。

非 browser client（curl / Python script / 攻擊者直接用 socket）**不受 CORS 限制**。CORS 只防「**用戶在某 site 被 trick 用瀏覽器讀別站**」。

## 一個常見誤解：「CSP 設了就 100% 防 XSS」

**部分對**。CSP 強，但：

- 弱配置（`unsafe-inline` / `*`）= 沒效
- DOM XSS（攻擊改 DOM）CSP 防不到（除非 `Trusted Types`）
- 老 browser 不支援

CSP 是 **defense in depth** 的一層，不是唯一防線。**先寫安全 code，CSP 是 backup**。

## 一個常見誤解：「CORS 是 SOP 的替代」

**錯**。CORS 是 SOP 的**例外機制**。SOP 是預設規則，CORS 是「**server 告訴 browser 這 case 可放行**」。

沒 CORS → SOP 強制阻擋。

## 動手練習

**1. 看真實 site 的 CSP**

```bash
curl -I https://google.com | grep -i content-security
curl -I https://github.com | grep -i content-security
curl -I https://your-site.com | grep -i content-security
```

對比有沒有 CSP，寫得多嚴。

**2. 故意違反 CSP**

開 Browser dev tools → Console。對一個有 CSP 的 site 跑：

```javascript
eval("alert('xss')")
```

看 console 是否有 CSP violation 訊息。

**3. CORS preflight**

```bash
# 對 Juice Shop API 送 preflight
curl -X OPTIONS http://localhost:3000/api/Users \
  -H "Origin: https://evil.com" \
  -H "Access-Control-Request-Method: POST" -v
```

看 ACAO header 怎麼回。

**4. 看 Cookie 屬性**

Browser dev tools → Application → Cookies → 訪問各 site，看 cookie 哪些 flag 設了。

**5. 寫個簡單 vulnerable HTML**

```html
<!DOCTYPE html>
<html>
<head><title>test</title></head>
<body>
<h1>welcome <span id="name"></span></h1>
<script>
const params = new URLSearchParams(location.search);
document.getElementById('name').innerHTML = params.get('name');  // XSS 漏洞
</script>
</body>
</html>
```

存 `index.html`，跑 `python3 -m http.server`，訪問 `http://localhost:8000?name=<img src=x onerror=alert(1)>`。

XSS 觸發。

加 CSP meta tag 看會不會 block：

```html
<meta http-equiv="Content-Security-Policy" content="script-src 'self'">
```

## 自我檢核

- [ ] Same Origin 完整定義（scheme + host + port）
- [ ] SOP 限制什麼（read 不能 / send 能）
- [ ] CORS preflight 流程
- [ ] CORS 3 個常見錯（`*` / reflection / wildcard）
- [ ] Cookie SameSite 3 個值
- [ ] CSP 主要 directive
- [ ] 知道 SRI / X-Frame-Options 用途
- [ ] 自己寫過 vulnerable HTML 試 CSP

Part 1 結束。下一個 Part 進 OWASP Top 10。

→ [Ch 4 A01 Broken Access Control](./04-a01-broken-access-control.md)
