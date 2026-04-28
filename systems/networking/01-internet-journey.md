# Ch 1 — 你按 enter 後發生什麼

> 目標：建立整門課的 mental map。從瀏覽器輸入 URL 到看到網頁，這背後 10+ 個步驟，每個都對應一個章節。

## 場景

你打開瀏覽器，輸入 `https://example.com`，按 enter。

幾百毫秒後看到網頁。

中間發生了**至少 10 個獨立步驟**，跨 4 個網路層、3-5 個協定、可能 5-10 台中間機器。本章把這條路一次走完。

## 完整旅程

```
 你按 enter
      │
      ▼
 ┌────────────────────────────────────┐
 │ 1. URL 解析 (browser)              │  瀏覽器拆 URL：scheme + domain + port + path
 └─────────────┬──────────────────────┘
               ▼
 ┌────────────────────────────────────┐
 │ 2. DNS 查詢                        │  domain → IP（Ch 9 詳細）
 └─────────────┬──────────────────────┘
               ▼
 ┌────────────────────────────────────┐
 │ 3. 路由表查詢                      │  「這個 IP 該從哪個 interface 出去」（Ch 4）
 └─────────────┬──────────────────────┘
               ▼
 ┌────────────────────────────────────┐
 │ 4. ARP（local network 才有）       │  找下一跳的 MAC 位址（Ch 3）
 └─────────────┬──────────────────────┘
               ▼
 ┌────────────────────────────────────┐
 │ 5. TCP 三次握手                    │  建立連線（Ch 6）
 └─────────────┬──────────────────────┘
               ▼
 ┌────────────────────────────────────┐
 │ 6. TLS 握手                        │  協商加密 + 驗證憑證（Ch 11）
 └─────────────┬──────────────────────┘
               ▼
 ┌────────────────────────────────────┐
 │ 7. HTTP 請求                       │  GET / 等等（Ch 10）
 └─────────────┬──────────────────────┘
               ▼
 ┌────────────────────────────────────┐
 │ 8. server 處理                     │  網頁伺服器產生 response
 └─────────────┬──────────────────────┘
               ▼
 ┌────────────────────────────────────┐
 │ 9. response 回來（同樣的反向路徑） │
 └─────────────┬──────────────────────┘
               ▼
 ┌────────────────────────────────────┐
 │ 10. 瀏覽器 render                  │  HTML / CSS / JS
 └────────────────────────────────────┘
```

每個步驟都可能慢、可能壞。本課接下來的 39 章拆開每個。

## 每個步驟的細節速覽

### 1. URL 解析

`https://example.com:443/path?query` 拆成：

| 部份 | 例 | 用途 |
|---|---|---|
| scheme | `https` | 用什麼協定（決定 port、是否加密） |
| host | `example.com` | DNS 要查的 domain |
| port | `443`（HTTPS 預設） | TCP port |
| path | `/path` | server 上的資源 |
| query | `?query` | 額外參數 |

瀏覽器內部處理，不上網路。

### 2. DNS 查詢

瀏覽器要把 `example.com` 變成 IP（如 `93.184.216.34`）。

```
 ┌─────────┐  你的 DNS 設定（如 8.8.8.8）
 │ browser │ ────────────────────►  ┌──────────────┐
 └─────────┘  「example.com 的 IP」 │  DNS resolver │
                                    └──────┬────────┘
                                           │
                              （遞迴查詢）─┴────► root → .com → example.com
                                           │
 ┌─────────┐                              ▼
 │ browser │ ◄─────  93.184.216.34   ┌──────────────┐
 └─────────┘                          │  DNS resolver │
                                      └──────────────┘
```

**Ch 9 詳細**。如果 DNS 失敗 → 連線錯。

### 3. 路由表

OS 查路由表決定「**這個 IP 該從哪個網卡出去**」：

```bash
ip route get 93.184.216.34
# 93.184.216.34 via 192.168.1.1 dev wlan0 src 192.168.1.10
```

意思：「往 93.184.216.34 走 wlan0、下一跳是 192.168.1.1（你的路由器）」。

**Ch 4 詳細**。

### 4. ARP

但 OS 只知道下一跳是 IP `192.168.1.1`，不知道對應的 MAC（網卡硬體位址）。

ARP（Address Resolution Protocol）解決：

```
本機 → 廣播：「誰是 192.168.1.1？告訴我你的 MAC」
路由器 → 「我是！MAC = aa:bb:cc:dd:ee:ff」
```

之後 OS 把 packet 包成 ethernet frame，目標 MAC = aa:bb:cc:dd:ee:ff。

**Ch 3 詳細**。

### 5. TCP 三次握手

OS 跟對方 server 建立 TCP 連線：

```
 client                    server
   │                         │
   ├───── SYN ──────────────►│   "我想連線"
   │                         │
   │◄──── SYN, ACK ──────────┤   "好，你也說好嗎"
   │                         │
   ├───── ACK ──────────────►│   "好"
   │                         │
   │  =連線建立=             │
```

這就是「**TCP 三次握手**」。**Ch 6 詳細**。

### 6. TLS 握手

如果是 HTTPS（不是純 HTTP），TCP 連好後再做 TLS 握手：

```
 client                    server
   │                         │
   ├── ClientHello ─────────►│   "我支援這些加密 / 隨機數"
   │                         │
   │◄── ServerHello, Cert ───┤   "用這個加密 / 我的憑證"
   │                         │
   ├── KeyExchange, Finished►│   "金鑰交換 / 完成"
   │                         │
   │◄── Finished ────────────┤   "完成"
   │                         │
   │  = 加密管道建立 =       │
```

**Ch 11 詳細**。

### 7. HTTP 請求

加密管道建好後，瀏覽器送 HTTP 請求：

```
GET /path HTTP/2
Host: example.com
User-Agent: Mozilla/5.0 ...
Accept: text/html
```

server 回應：

```
HTTP/2 200 OK
Content-Type: text/html
Content-Length: 1256

<html>...</html>
```

**Ch 10 詳細**。

### 8-10. server 處理 + 回程 + render

server 跑 nginx / Apache / 自家 code 產 HTML。

response 回來走相同網路路徑（reverse direction）。

瀏覽器 render HTML → 解析 CSS / JS → 可能再發更多請求載 image / API → 最終顯示。

## 一個請求 = 多少 packet

簡化估算：

| 階段 | packet 數 |
|---|---|
| DNS | 2-4 |
| ARP | 2 |
| TCP 三次握手 | 3 |
| TLS 握手 | 4-8 |
| HTTP request | 1-3 |
| HTTP response | 5-50（看 HTML 大小） |
| TCP ACK | 多個 |
| 連線結束 | 4 |

**一個簡單頁面 50-200 個 packet，每個經過你 → 路由器 → ISP → 骨幹 → CDN → server**。

複雜頁面（含 30 個 image / 5 個 API）：1000+ packet。

## 各層 happy path

```
 Application   │ HTTP / HTTPS / DNS / SMTP / SSH
              │
 Transport    │ TCP / UDP
              │
 Network      │ IP / ICMP / IPSec
              │
 Link         │ Ethernet / WiFi / ARP
              │
 Physical     │ 電 / 光 / 電磁波
```

下一章正式拆 OSI / TCP/IP 模型。

## 哪裡會慢 / 壞

每個步驟都可能：

| 步驟 | 慢的原因 | 壞的徵狀 |
|---|---|---|
| DNS | resolver 慢、DNS over Tor | NXDOMAIN, timeout |
| 路由 | ISP 路徑長 | high latency |
| TCP 握手 | RTT 大 | 連線慢 |
| TLS | 憑證鏈長、舊 client | TLS handshake fail |
| HTTP | server 慢、payload 大 | 504, slow load |
| 路由器 | 過載、QoS | packet drop, jitter |

整個課就是教你**每個步驟怎麼觀察、怎麼診斷、怎麼修**。

## 一個常見誤解：「網路就是 HTTP」

**錯**。HTTP 只是應用層之一。整個網路 stack 7 層，HTTP 只佔最上層。

DNS、TCP、IP、Ethernet、ARP — 每個都獨立、都可能壞、都有自己的工具去 debug。

**只懂 HTTP 不懂下層 = 連 `Connection refused` 都不會 debug**。

## 一個常見誤解：「IP 就到了 server」

**錯**。一個 packet 從你出發到 server，**經過 5-15 台路由器**。每台都做：

1. 看 packet 的 dst IP
2. 查自己的路由表
3. 決定下一跳
4. 把 packet 轉發

**`traceroute` 看得到這條路**。Ch 16 詳細。

## 一個常見誤解：「HTTPS 比 HTTP 慢很多」

**部分對**。TLS 握手多 1-2 個 RTT。但**現代 TLS 1.3 + connection reuse + HTTP/3 把這個差距壓到接近 0**。

**「HTTPS 慢」是 2010 年的觀念**。現在 HTTPS 比 HTTP 還快（因為 HTTP/3 是 HTTPS only）。

## 動手練習

**1. 觀察一個請求**

```bash
# 開 tcpdump 抓所有對 example.com 的 packet
sudo tcpdump -nn -i any 'host example.com'

# 另一個 terminal
curl -v https://example.com
```

數一下 packet。對應到 10 個步驟看看。

**2. 拆 URL**

寫一個 URL：`https://user:pass@example.com:8080/path/to?q=1#frag`

拆成 7 部分：scheme / userinfo / host / port / path / query / fragment。

每部分功能是什麼？

**3. 故意打錯 domain**

```bash
curl https://example.fake-domain-xxx.com
```

看錯誤訊息。對到 10 步驟哪一步壞了？

**4. 觀察 traceroute**

```bash
traceroute -n example.com
# 或
mtr -n example.com
```

數有幾跳。每跳是什麼？（Ch 16 細解，現在直觀感受。）

## 自我檢核

- [ ] 講得出按 enter 後 10 個步驟
- [ ] 每個步驟對應到「課程哪一章」
- [ ] 知道 HTTP / TCP / IP / Ethernet 是不同層
- [ ] 知道一個請求是 50-200 packets
- [ ] 「IP 就到 server」這誤解破除

下一章看 OSI vs TCP/IP 模型 — 把整個網路 stack 結構化。

→ [Ch 2 OSI 與 TCP/IP 模型](./02-osi-tcpip-models.md)
