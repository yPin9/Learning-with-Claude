# Ch 10 — HTTP/1.1 → HTTP/2 → HTTP/3

> 目標：搞懂 HTTP 三代演進的設計動機，知道每代解決了前代什麼問題、引入什麼新問題。

## HTTP/1.0 (1996)：最早的 HTTP

- 每 request 開新 TCP 連線
- 連線送完關掉
- 一個 page 30 個 image → 30 次 TCP 握手

效率極差。

## HTTP/1.1 (1997)：keep-alive

主要改進：

1. **Persistent connection**：連線保持，能重用
2. **Pipelining**（理論上）：多 request 連續送，不等 response。但**幾乎沒實作好**，瀏覽器多禁用
3. **Host header**：1 個 IP 能跑多個 domain

```http
GET /path HTTP/1.1
Host: example.com
User-Agent: Mozilla/5.0
Accept: text/html

```

```http
HTTP/1.1 200 OK
Content-Type: text/html
Content-Length: 1234

<html>...</html>
```

文字協定，**人類可讀**。

## HTTP/1.1 的問題

### 1. Head-of-Line blocking

雖然能 keep-alive，但**一個連線同時只能跑一個 request**：

```
 Connection 1: [Request A][等 response A][Request B][等 response B]
```

若 response A 慢，B 跟著等。

瀏覽器解：開**多個連線**（最多 6 個 / domain）。

### 2. Header 重複

每個 request 都送 user-agent / cookies / accept 等 → 累計 KB 級 overhead，影響高頻小 request。

### 3. Server push 沒有

server 不能主動送資源給 client。

## HTTP/2 (2015)：multiplexing + binary

關鍵改進：

### 1. Binary protocol

不再是文字，是 binary frame。**parser 簡單、出錯少**。

### 2. Multiplexing

**1 個連線跑多個並行 stream**：

```
 Connection 1: [Req A][Req B][Req C] 並行
                   ↓     ↓     ↓
                 Resp A Resp B Resp C  混合回來
```

不再有 application 層的 head-of-line blocking。

### 3. Header compression (HPACK)

重複 header 用「索引」表示，省頻寬。

### 4. Server Push

server 主動送 client 即將要的資源（如 page 的 CSS）。

實際上 server push 用得少（瀏覽器 cache 邏輯複雜），現在多數人 disable。

### 5. Stream prioritization

可以告訴 server「先送 CSS 再送 image」。

## HTTP/2 的問題：**TCP 層的 head-of-line blocking**

HTTP/2 在 application 層解了 HOL blocking，但**TCP 層**的 HOL 還在：

```
 TCP: packet A1, A2, B1, A3, B2, B3 ...
 如果 A2 丟掉 → TCP 等 A2 retransmit → B 們也卡
```

雖然 HTTP/2 streams 邏輯獨立，但都跑在同一個 TCP 連線 → 一個 packet drop 影響全部。

## HTTP/3 (2022)：QUIC

換掉 TCP，用 **QUIC**（UDP 上實作）：

### 1. 沒 TCP HOL blocking

QUIC 知道「**哪個 stream 屬於哪個 packet**」，A stream 的 packet drop 不影響 B stream。

### 2. 連線建立更快

TCP + TLS 共需要 2 RTT。QUIC 1 RTT（甚至 0-RTT 重連）。

### 3. 連線遷移

設備 IP 改變（從 WiFi 換 4G）→ TCP 連線斷。QUIC 有 connection ID，**不依賴 IP**，能無縫切換。

### 4. 內建 TLS 1.3

不需要分開握手。

### 缺點

- 跑在 user space → CPU 開銷較大
- 中間設備 / firewall 對 UDP 處理不一致
- 部署較新（2022 RFC，普及還在進行）

## 演進對照表

| 版本 | 年份 | 底層 | 連線 | 並行 | 加密 |
|---|---|---|---|---|---|
| HTTP/1.0 | 1996 | TCP | 1 req/連線 | 否 | 可選 |
| HTTP/1.1 | 1997 | TCP | keep-alive | 否（理論 pipeline） | 可選 |
| HTTP/2 | 2015 | TCP | 1 連線多 stream | 是 | **強制（瀏覽器強制）** |
| HTTP/3 | 2022 | QUIC (UDP) | 1 連線多 stream | 是，無 TCP HOL | 強制 |

## HTTP request / response 結構

不論版本，邏輯結構：

### Request

```
GET /path HTTP/1.1
Host: example.com
User-Agent: curl/7.81.0
Accept: */*
Cookie: session=abc123

[optional body]
```

3 部分：start line + headers + body。

### Response

```
HTTP/1.1 200 OK
Date: Mon, 28 Apr 2025 12:00:00 GMT
Server: nginx/1.20
Content-Type: text/html
Content-Length: 1234
Set-Cookie: session=abc123; HttpOnly

<html>...</html>
```

3 部分：status line + headers + body。

## 常見 method

| Method | 用途 | Body | Idempotent |
|---|---|---|---|
| GET | 取 | 沒 | ✓ |
| HEAD | 同 GET 但只回 header | 沒 | ✓ |
| POST | 建 / submit | 有 | ✗ |
| PUT | 完整更新 | 有 | ✓ |
| PATCH | 部份更新 | 有 | 通常 ✗ |
| DELETE | 刪 | 沒 | ✓ |
| OPTIONS | CORS preflight | 沒 | ✓ |

「**Idempotent**」 = 多次執行結果同一次。GET / PUT / DELETE 應 idempotent；POST 不。

## 常見 status code

| Code | 類別 | 例 |
|---|---|---|
| 1xx | informational | 100 Continue |
| 2xx | success | 200 OK, 201 Created, 204 No Content |
| 3xx | redirect | 301 Permanent, 302 Found, 304 Not Modified |
| 4xx | client error | 400 Bad Req, 401 Unauthorized, 403 Forbidden, 404 Not Found, 429 Too Many Reqs |
| 5xx | server error | 500 Internal, 502 Bad Gateway, 503 Unavailable, 504 Gateway Timeout |

debug 看 status code 就知道大概錯在哪：4xx 你的問題、5xx server 的問題。

## 一個常見誤解：「HTTP/2 比 HTTP/1.1 永遠快」

**部分對**。多數場景快，但：

- 小頁面（少 request）差不多
- 高 packet loss 環境，HTTP/2 因 TCP HOL 反而慢

HTTP/3 才真正在所有場景優於 1.1。

## 一個常見誤解：「HTTPS = HTTP over TLS」

**完整版對**。`https://` URL 表示 HTTP 走在 TLS 上：

- HTTP/1.1 over TLS = HTTPS
- HTTP/2 over TLS = HTTPS（瀏覽器強制 HTTP/2 走 TLS）
- HTTP/3 over QUIC（QUIC 自帶 TLS）= HTTPS

## 一個常見誤解：「HTTP/3 完全取代 HTTP/2」

**錯**。瀏覽器 / server 同時支援多版本，協商用哪個。

實務上：

- HTTP/1.1: ~5% of web
- HTTP/2: ~50%
- HTTP/3: ~30% 且增長

HTTP/1.1 還在因為老 server / API client 沒升。

## 動手練習

**1. curl 看 HTTP 版本**

```bash
curl -v https://example.com 2>&1 | grep "HTTP/"
# > GET / HTTP/2
```

**2. 強制 HTTP/1.1**

```bash
curl --http1.1 -v https://example.com 2>&1 | grep "HTTP/"
# > GET / HTTP/1.1
```

**3. 強制 HTTP/3**

```bash
# 需要 curl 支援 HTTP/3（多數系統 default 沒有）
curl --http3 -v https://www.cloudflare.com
```

**4. 觀察 HTTP/1.1 keep-alive**

```bash
sudo tcpdump -nn -i any 'port 80' &
# 連幾次同 server
curl http://example.com http://example.com http://example.com
```

看 TCP 連線是否被重用。

**5. 看 status codes**

```bash
curl -o /dev/null -s -w "%{http_code}\n" https://example.com    # 200
curl -o /dev/null -s -w "%{http_code}\n" https://example.com/x  # 404
curl -o /dev/null -s -w "%{http_code}\n" https://expired.badssl.com  # 視 cert 而定
```

## 自我檢核

- [ ] HTTP/1.1 → 2 → 3 三代主要改進記得
- [ ] 知道 HTTP/2 的 multiplexing 跟 HTTP/1.1 keep-alive 差別
- [ ] 知道 HTTP/3 為什麼用 QUIC
- [ ] 6+ 種 method 用途
- [ ] 5xx vs 4xx 的差別
- [ ] curl 用 --http1.1 / --http2 / --http3 試過

下一章看 TLS / HTTPS — 加密層怎麼運作。

→ [Ch 11 TLS / HTTPS](./11-tls-https.md)
