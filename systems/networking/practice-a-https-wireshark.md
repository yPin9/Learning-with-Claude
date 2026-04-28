# 練習 A — 用 Wireshark 看完整 HTTPS 請求

> 目標：把 Ch 1-12 學的東西串起來：抓一個 `https://example.com` 的 packet flow，標出 DNS / ARP / TCP / TLS / HTTP 各階段。

## 任務規格

| # | 任務 | 驗收 |
|---|---|---|
| 1 | 用 Wireshark 抓一次 `curl https://example.com` 的所有 packet | 看到 ARP / DNS / TCP 三次握手 / TLS 握手 / HTTP request/response / TCP 揮手 |
| 2 | 標出每個階段是哪些 packet | 用 Wireshark filter / 註解 |
| 3 | 算 timing：每階段花多少 ms | DNS 多久？TCP 握手多久？TLS 多久？|
| 4 | 寫報告 | 600+ 字，含 packet diagram |

## 環境準備

```bash
# 確保 Wireshark 跑得起來（Ch 0 已裝）
wireshark --version

# 清 ARP cache（讓你能看到 ARP）
sudo ip neigh flush all

# 清 DNS cache
sudo systemd-resolve --flush-caches  # 或 systemctl restart systemd-resolved
```

## 步驟

### Step 1：起 Wireshark capture

```
 1. 開 Wireshark
 2. 選你的網卡（通常 wlan0 / eth0）
 3. 開始抓
 4. 立刻在另一個 terminal 跑：

    curl https://example.com -o /dev/null

 5. 等 1 秒，停止抓
```

### Step 2：用 filter 找 example.com 的 packet

Wireshark filter 語法：

```
ip.addr == 93.184.216.34
```

或：

```
host example.com    # tcpdump 風格
```

或更廣：

```
dns or (ip.addr == <example IP>)
```

把 example.com 相關 packet 全找出來。

### Step 3：每個階段標註

按時間順序，packet 應該長這樣：

```
1. DNS query A example.com  (UDP 53)
2. DNS response A 93.184.216.34
3. (可能 ARP)
4. TCP SYN
5. TCP SYN-ACK
6. TCP ACK                          ← 三次握手完成
7. TLS Client Hello
8. TLS Server Hello, Certificate
9. TLS Client Key Exchange
10. TLS Finished                    ← TLS 握手完成
11. HTTP/1.1 GET / (in TLS)
12. HTTP/1.1 200 OK (in TLS)
13. TCP FIN
14. TCP FIN-ACK
15. TCP ACK                          ← 揮手
```

每個階段在 Wireshark 標註（右鍵 → Set Reference Time / Mark Packet）。

### Step 4：算時間

Wireshark 顯示每 packet 的時間。算：

| 階段 | 開始 | 結束 | 時長 |
|---|---|---|---|
| DNS | DNS query | DNS response | ? ms |
| TCP 握手 | SYN | ACK (3rd) | ? ms |
| TLS 握手 | Client Hello | TLS Finished | ? ms |
| HTTP | GET | 200 OK | ? ms |
| 整體 | DNS query | 200 OK | ? ms |

用 wireshark 的「Time delta from previous」column 看每 packet 之間時間。

### Step 5：寫報告

```markdown
# Practice A 報告：example.com HTTPS 完整流程

## 環境
- 客戶端：（你的 Linux / Mac / VM）
- 網路：（家裡 WiFi / 4G / VPS）
- 工具：Wireshark X.Y

## Packet 時序圖
（畫一個簡化的 flow diagram，用 ASCII 或截圖）

## 各階段分析

### DNS（packet N - M）
- 查詢類型：A
- DNS server：?
- 用時：?
- 觀察：?

### ARP（如果有）
- 查 192.168.1.1 的 MAC
- 用時：?

### TCP 三次握手（packet N - M）
- SYN seq=?
- SYN-ACK seq=?, ack=?
- ACK ack=?
- 用時：?
- 觀察：(window size? options?)

### TLS 握手（packet N - M）
- 版本：TLS 1.2 / 1.3
- Cipher：?
- 憑證 issuer：?
- 用時：?
- 觀察：(SNI 看得到嗎？)

### HTTP request/response
- Method：GET
- Status：200
- Content-Length：?
- HTTP 版本：1.1 / 2 / 3
- 用時：?

### TCP 揮手
- FIN packet 數量：?
- 用時：?

## 整體時序
- 全程：? ms
- DNS 占比：?%
- TCP 占比：?%
- TLS 占比：?%
- HTTP 占比：?%

## 學到什麼
- ?
```

## 完整參考

**做完再看！**

<details>
<summary>典型結果範本</summary>

```markdown
# Practice A 報告

## 環境
- Mac M1, macOS Sonoma
- 家裡 WiFi 100Mbps
- Wireshark 4.0

## Packet 時序圖

```
時間（ms）  Action
   0       curl 啟動
   1       DNS query A example.com 送出
  15       DNS response 收到（93.184.216.34）
  16       TCP SYN 送出
  85       TCP SYN-ACK 收到（RTT = 69ms）
  85       TCP ACK 送出
  86       TLS ClientHello 送出
 158       TLS ServerHello + Cert 收到
 160       TLS ClientKeyExchange 送出
 232       TLS Finished 收到
 233       HTTP/1.1 GET / 送出（在 TLS 內）
 305       HTTP/1.1 200 OK 收到
 307       TCP FIN 送出
 380       TCP FIN-ACK 收到
```

## 各階段時長

| 階段 | 用時 | 占比 |
|---|---|---|
| DNS | 14 ms | 4% |
| TCP 握手 | 69 ms | 18% |
| TLS 握手 | 146 ms | 39% |
| HTTP | 72 ms | 19% |
| TCP 揮手 | 73 ms | 19% |
| **總計** | **374 ms** | 100% |

## 觀察

- TLS 握手最慢（2 RTT），因為是 TLS 1.2
- 整體跟 RTT 成正比 — 跨國連線會放大
- DNS 最快因為 ISP 有 cache

## 我學到什麼
- HTTPS 連線 80% 時間在 TCP + TLS 握手
- 重用連線（keep-alive / connection pool）能省很多
- TLS 1.3 應該能省一個 RTT
```

</details>

## 進階挑戰

**A. 對比 TLS 1.2 vs 1.3 的握手時間**：找個明確 1.3 的 server（cloudflare）跟明確 1.2 的（自己架），看時長差。

**B. 對比不同地理位置**：連台灣 server vs 連美國 server，看 TCP 握手 RTT 差。

**C. 對比 HTTP/1.1 vs HTTP/2**：用 `curl --http1.1` 跟 `curl --http2`，看是否有差。

**D. 抓 HTTP/3**：用 Chrome 連 cloudflare，filter UDP 443，看 QUIC packet（內容加密看不到細節，但能看 packet 數量 / 時序）。

**E. 抓自己的 SSH 流量**：`ssh root@<VPS> 'echo hi'`，看 SSH 握手 packet。

## 常見錯誤

| 症狀 | 原因 |
|---|---|
| 抓不到 ARP | 已經 cache 了 → 先 flush |
| 抓不到 DNS | DNS server 用 systemd-resolved 走 stub → filter 改 |
| TLS 握手只看到加密 | 正常，握手後資料看不到（除非有 SSL key log）|
| Wireshark 看不到 interface | 沒給權限，sudo 或加 group |

## 自我檢核

- [ ] 完整抓到一次 HTTPS 流程
- [ ] 標註各階段 packet
- [ ] 算清楚各階段 timing
- [ ] 寫了 600+ 字報告
- [ ] 知道 HTTPS 80% 時間花在握手

下個 Part 進工具完整指南。

→ [Ch 13 ip / ss / route](./13-ip-ss-route.md)
