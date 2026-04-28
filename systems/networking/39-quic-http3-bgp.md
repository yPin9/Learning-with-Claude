# Ch 39 — QUIC / HTTP/3 / BGP

> 目標：認識 3 個現代網路的關鍵技術 — QUIC、HTTP/3、BGP，每個基本原理 + 為什麼重要。

## QUIC

「**Quick UDP Internet Connections**」 — Google 2012 起設計，2021 RFC 9000 標準化。

跑在 UDP 上，但**自己實作**所有 TCP 該有的：

- 可靠 / 順序
- 擁塞控制
- 加密（內建 TLS 1.3）
- 多 stream multiplexing

### 為什麼不直接用 TCP

1. **TCP 演算法在 OS kernel** — 升級慢
2. **TCP 中間設備兼容** — router / firewall 對 TCP option 處理保守，限制創新
3. **TCP head-of-line blocking** — 一個 packet 丟 → 全 stream 卡
4. **TCP + TLS 兩次握手** — 多一個 RTT

QUIC 在 user space，**創新自由**：

- 0-RTT 重連
- 連線遷移（WiFi → 4G 不斷線）
- 多 stream 互不阻塞
- 整合 TLS

### QUIC 握手

```
 0-RTT (有 cache):
   client ──── data + handshake ──────► server

 1-RTT (新連線):
   client ──── ClientHello ───────────► server
   client ◄─── ServerHello + cert ─────  server
   client ──── data ───────────────────► server
```

vs TCP+TLS 1.2 的 4-RTT，QUIC 1-RTT 快很多。

### QUIC 的代價

- CPU 較重（user-space 加密）
- 中間設備對 UDP 處理不一致
- 部署較新（2022 年起標準）

## HTTP/3

= **HTTP over QUIC**。

HTTP/2 的 application-layer 設計都保留：

- multiplexing
- header compression (但用 QPACK 不是 HPACK)
- server push（少用）

差別：底層從 TCP 換 QUIC。

### HTTP/3 部署

server 端：nginx / Caddy / Cloudflare 開支援。Browser 端：Chrome / Firefox 自動協商。

協商：先試 HTTP/3，失敗 fallback HTTP/2。

```bash
# curl 試 HTTP/3
curl --http3 -I https://www.google.com
```

```nginx
# nginx 1.25+ 開 HTTP/3
server {
    listen 443 quic reuseport;
    listen 443 ssl http2;
    
    add_header Alt-Svc 'h3=":443"; ma=86400';
    ...
}
```

`Alt-Svc` header 告訴 browser「**我支援 HTTP/3**」。

### HTTP/3 採用率

2025 年：

- ~30% web 流量用 HTTP/3
- Cloudflare / Google / Facebook 都支援
- 慢慢取代 HTTP/2

## BGP（Border Gateway Protocol）

「**Internet 的路由協定**」 — 把全球路由連起來。

### Internet 結構

```
 ┌────────────────────┐
 │  ISP A (AS 12345)  │ ────┐
 └────────────────────┘     │
                            │
                       ┌────┴─────┐
                       │  IXP /    │  ← Internet Exchange Point
                       │  Backbone │
                       └────┬─────┘
                            │
 ┌────────────────────┐     │
 │  ISP B (AS 67890)  │ ────┘
 └────────────────────┘
```

每個 ISP / 大企業是「**Autonomous System (AS)**」，有 AS number。

AS 之間用 **BGP** 交換路由：

「**我能到 192.0.2.0/24 這個網段，路徑是 AS 12345 → AS 67890**」

每台 router 的 BGP table 上百萬條 route。

### BGP 是 Internet 的「政治」

ISP 之間的關係：

- **Transit**：付錢給 upstream（小 ISP 付給大 ISP）
- **Peering**：對等交換（大 ISP 之間）
- **Customer**：你付 ISP 錢

BGP 路由 announce 跟商業關係綁定。「**對方願意把我的 traffic 送到哪**」是商業 + 技術問題。

### BGP hijacking

惡意 / 誤配 ISP 宣告「**我擁有 X.X.X.0/24**」（明明不是它的）→ 全球流量被導去那 ISP。

歷史事件：

- 2008 巴基斯坦 ISP 想 block YouTube → 配錯 BGP → 全球 YouTube 流量被路由到巴基斯坦
- 2018 BGP hijack 偷加密貨幣

防禦：

- **RPKI**（Resource Public Key Infrastructure）— 數位簽名 BGP route
- **BGPsec**（next-gen BGP 含驗證）

部署慢，仍部分風險。

### 你不會直接用 BGP（除非 ISP / 大企業）

Internet 跑 BGP，但**個人 / 小企業看不到**。學 BGP 為了：

- 理解 Internet 怎麼運作
- 看 traceroute 為什麼經過特定路徑
- 了解 DDoS / hijacking 等網路安全議題

## 看 BGP 路由

```bash
# 用 BGP looking glass（線上工具）
# https://www.he.net/cgi-bin/lg.cgi
# https://bgp.he.net/

# 或本機 traceroute 看 AS
mtr --aslookup example.com
# 顯示每跳的 AS number
```

## 一個常見誤解：「QUIC 一定比 TCP 快」

**部分對**。對小 request、低 packet loss 環境，QUIC 接近 TCP。差距在：

- 高 packet loss 環境（無 head-of-line blocking）→ QUIC 顯著快
- mobile（連線遷移）→ QUIC 顯著好
- 0-RTT 重連 → QUIC 快

**多數場景 QUIC > TCP**，但 CPU 用量略多。

## 一個常見誤解：「BGP 是程式設計問題」

**部分對**。實際 BGP 主要是**配置 + 政策**，不是寫 code。

ISP 工程師寫 BGP route policy（哪些 prefix 接受 / 拒絕 / 改 attribute），不寫 BGP 演算法本身。

## 一個常見誤解：「HTTP/3 還沒成熟，先別用」

**錯**。Google / Cloudflare / Facebook 大量 production 用了 5+ 年。

對 web app：開 HTTP/3 通常**只有好處**（自動 fallback 安全）。

## 動手練習

**1. 試 HTTP/3**

```bash
# curl 編譯支援 HTTP/3 才能用
curl --http3 -I https://www.google.com
curl --http3 -I https://www.cloudflare.com
```

如果你 curl 沒 HTTP/3：

```bash
# Chrome
# Open chrome://flags
# Enable "Experimental QUIC protocol"
# 訪問 https://www.cloudflare.com，看 Network panel 的 protocol
```

**2. 看你 ISP 的 AS**

```bash
mtr --aslookup -n example.com
# 或線上：https://bgp.he.net/   你的 IP
```

**3. 看 BGP 全景**

```
https://bgp.he.net/dns/example.com
```

看 example.com 的 IP 屬於哪個 AS、AS 的 peering 關係。

**4. 開 nginx HTTP/3**

```nginx
listen 443 quic reuseport;
listen 443 ssl http2;
add_header Alt-Svc 'h3=":443"; ma=86400';
```

需要 nginx 1.25+。

```bash
# 從外面 test
curl --http3-only https://your-domain.com
```

**5. 看 RPKI 驗證**

```
https://www.cloudflare.com/learning/security/glossary/what-is-bgp-hijacking/
```

讀 BGP hijacking 案例。

## 自我檢核

- [ ] 知道 QUIC 為什麼跑 UDP
- [ ] HTTP/3 = HTTP over QUIC，跟 HTTP/2 差別
- [ ] BGP 是 Internet 路由協定
- [ ] AS / Peering / Transit 概念清楚
- [ ] 知道 BGP hijacking 風險
- [ ] 看過自己網路的 AS

Part 9 結束。最後 Final Project — 整合所有 part 的完整部署。

→ [Final Project：完整 VPS 部署](./final-project-complete-deployment.md)
