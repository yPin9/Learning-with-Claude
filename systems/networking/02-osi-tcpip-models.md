# Ch 2 — OSI 與 TCP/IP 模型

> 目標：搞清楚 7 層 OSI 跟 4 層 TCP/IP 模型差在哪、為什麼這分層、實務上怎麼用。

## 為什麼要分層

網路太複雜，任何人都做不完所有事。**分層** 讓不同人專注不同問題：

- 寫網頁的人 → 用 HTTP，不必管 packet 怎麼到 server
- 寫 OS 的人 → 實作 TCP / IP，不必管網路卡怎麼接收電訊號
- 做網卡的人 → 處理電訊號，不必管 TCP 怎麼運作

**每層只看自己負責的事**，跟上下層用標準介面溝通。

## OSI 7 層模型

1984 年 ISO 提出的「**理想中**」分層：

```
 7. Application  │ HTTP / SMTP / FTP / DNS / SSH         （程式跟使用者互動）
 6. Presentation │ TLS / 編碼 / 壓縮 / 加密              （資料表示）
 5. Session      │ 建立 / 維持 / 終止 session            （連線管理）
 4. Transport    │ TCP / UDP                             （端到端傳輸）
 3. Network      │ IP / ICMP / 路由                      （跨網段傳輸）
 2. Data Link    │ Ethernet / WiFi / ARP                 （同網段傳輸）
 1. Physical     │ 電 / 光 / 電磁波 / 網線               （物理訊號）
```

**現實**：很少人嚴格按這 7 層分。Session / Presentation 在實務中常常跟 Application 混在一起。

OSI 模型主要用在**教學跟術語對齊**。

## TCP/IP 4 層模型

實際 Internet 用的模型：

```
 4. Application  │ HTTP / SMTP / FTP / DNS / SSH / TLS
 3. Transport    │ TCP / UDP
 2. Internet     │ IP / ICMP
 1. Link         │ Ethernet / WiFi / ARP / Physical
```

把 OSI 的 7 層壓成 4 層：

- OSI 的 5/6/7 → TCP/IP 的 Application
- OSI 的 4 → Transport
- OSI 的 3 → Internet
- OSI 的 1/2 → Link

**現實中 TCP/IP 模型更實用**，因為它跟實際 protocol 對應好。

## 兩個模型對照

```
   OSI                    TCP/IP                 例
 ┌─────────────┐         ┌──────────────┐
 │ 7 App       │         │              │       HTTP, SSH
 │ 6 Present.  │ ──────► │ 4 App        │       TLS（也算 6）
 │ 5 Session   │         │              │       
 ├─────────────┤         ├──────────────┤
 │ 4 Transport │ ──────► │ 3 Transport  │       TCP, UDP
 ├─────────────┤         ├──────────────┤
 │ 3 Network   │ ──────► │ 2 Internet   │       IP, ICMP
 ├─────────────┤         ├──────────────┤
 │ 2 Data Link │         │              │       Ethernet, ARP
 │             │ ──────► │ 1 Link       │       
 │ 1 Physical  │         │              │       電 / 光
 └─────────────┘         └──────────────┘
```

實務上講「**第幾層**」常常是 OSI 的編號（特別是企業網路），但概念是 TCP/IP。

## 封裝（Encapsulation）

每往下一層，**加上自己的 header（包裝）**：

```
                                    Application data
                                          │
                                          ▼ (App data)
                              ┌─ App data ─┐
                              │  HTTP header │
                                          │
                                          ▼ (with HTTP header)
                              ┌─ TCP header ─┬─ HTTP + data ─┐
                                          │
                                          ▼ (with TCP header)
                              ┌─ IP header ─┬─ TCP + HTTP + data ─┐
                                          │
                                          ▼ (with IP header)
                              ┌─ Eth header ─┬─ IP + TCP + HTTP + data ─┬─ Eth trailer ─┐
                                          │
                                          ▼
                                       到網路上
```

每層 header 是「**這層的元資料**」：

| 層 | header 含 |
|---|---|
| Ethernet | 來源 MAC、目標 MAC、type |
| IP | 來源 IP、目標 IP、TTL、protocol |
| TCP | 來源 port、目標 port、seq、ack、flag |
| HTTP | method、URL、headers |

**反方向（接收）**：每層**剝掉自己的 header**，往上交。

## 一個 HTTP 請求的封裝

實際看一個 `curl https://example.com` 的 packet：

```
┌──────────────────────────────────────────────────────────────────┐
│ Ethernet header (14 bytes)                                       │
│   - dst MAC: aa:bb:cc:dd:ee:ff (路由器)                          │
│   - src MAC: 11:22:33:44:55:66 (你)                              │
│   - type: 0x0800 (IP)                                            │
├──────────────────────────────────────────────────────────────────┤
│ IP header (20 bytes)                                             │
│   - dst IP: 93.184.216.34 (example.com)                          │
│   - src IP: 192.168.1.10 (你)                                    │
│   - protocol: 6 (TCP)                                            │
│   - TTL: 64                                                      │
├──────────────────────────────────────────────────────────────────┤
│ TCP header (20 bytes)                                            │
│   - src port: 54321                                              │
│   - dst port: 443                                                │
│   - flags: ACK, PSH                                              │
├──────────────────────────────────────────────────────────────────┤
│ TLS record (5 bytes header + encrypted payload)                  │
├──────────────────────────────────────────────────────────────────┤
│ HTTP/2 frame (encrypted in TLS)                                  │
│   - method: GET                                                  │
│   - path: /                                                      │
└──────────────────────────────────────────────────────────────────┘
```

每層 header 都有自己的「目的」 — TCP 不關心 IP 的目標，IP 不關心 Ethernet 的 MAC。每層 abstract。

## 「同層通訊」

**邏輯上**：你的 HTTP layer 直接跟 server 的 HTTP layer 對話。
**實際上**：訊息往下封裝、過網路、上封裝。

```
  你的 HTTP  ─────邏輯上───►  server 的 HTTP
       │                              ▲
       ▼                              │
   你的 TCP                       server TCP
       │                              ▲
       ▼                              │
   你的 IP                        server IP
       │                              ▲
       ▼                              │
   你的網卡 ─── 真實網路 ───►  server 網卡
```

每層**「以為」**自己在跟對端同層直接溝通。這個 abstraction 是分層的核心。

## 一個常見誤解：「OSI 模型是事實」

**錯**。OSI 是 1980s 的「**理想模型**」，但 ISO 自己推的 OSI protocol stack（X.25 系列）失敗了。

實際 Internet 用的是**TCP/IP**（1970s DARPA 的設計）。OSI 模型保留下來是**教學**用，幫助人們對齊術語。

## 一個常見誤解：「TLS 是第 6 層」

**部分對**。TLS 在 OSI 模型勉強對到 Presentation（6）。但實務上講 TLS 通常算 Application（4，TCP/IP）。

「TLS 是第 7 層」「TLS 是第 6 層」「TLS 是第 5 層」 — 都有人講。**別執著於分類**。

## 一個常見誤解：「分層必須嚴格遵守」

**錯**。實務有大量「跨層」設計：

- TCP fast open 把 application 資料放進 SYN packet
- HTTP/2 / 3 跟下層 TLS / QUIC 緊密綁定
- HTTPS 重設 SNI 攔截 — 路由器看 TLS 內容
- DPI 防火牆 — 根據應用層內容做網路層決策

「分層」是**參考框架**，不是法律。

## 動手練習

**1. 看一個 packet 的所有層**

```bash
sudo tcpdump -nn -X -i any 'host example.com' -c 1
```

`-X` 印 hex + ASCII。看到的是 Ethernet header + IP header + TCP header + payload。

對照本章 header 結構解析。

**2. wireshark 看 packet 樹狀**

```bash
sudo wireshark
# capture interface, filter "host example.com"
# 開 browser 訪問 https://example.com
# 點任一個 packet → 看左下「分層」面板
```

Wireshark 自動把 packet 拆成 Ethernet → IP → TCP → TLS → HTTP/2 樹狀，每層 header 點開看。

**3. 數 header 大小**

對 `curl https://example.com` 的 GET 請求：

- Ethernet header: ? bytes
- IP header: ? bytes
- TCP header: ? bytes
- TLS overhead: ? bytes
- HTTP payload: ? bytes
- 總共: ? bytes

正常 small request 約 100-200 bytes overhead + payload。

**4. 找個「跨層」例子**

研究 TCP Fast Open（TFO）：它怎麼把 application data 塞進 SYN packet？這違反分層原則嗎？

## 自我檢核

- [ ] OSI 7 層每層名字 + 例 protocol 講得出
- [ ] TCP/IP 4 層對應到 OSI 哪些層
- [ ] 「封裝」概念清楚（每層加 header）
- [ ] 知道實務上分層不是嚴格遵守
- [ ] 用 wireshark 看過 packet 的分層

下一章正式進鏈結層 — Ethernet 與 ARP，從最底層往上爬。

→ [Ch 3 鏈結層：Ethernet / ARP](./03-link-layer-ethernet-arp.md)
