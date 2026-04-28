# Ch 6 — TCP 完整解析

> 目標：把 TCP 三次握手、四次揮手、流量控制、擁塞控制全講清楚。看完能解釋為什麼 `TIME_WAIT` 多 / 為什麼網路慢 / 為什麼 ssh 卡。

## TCP 是什麼

**Transmission Control Protocol** — 在 IP 上提供：

1. **可靠傳輸**：保證送到、保證順序、保證不重複
2. **流量控制**：避免 sender 把 receiver 淹沒
3. **擁塞控制**：避免把網路淹沒
4. **連線狀態**：開 / 關 / 進行中

代價：複雜、有 overhead、有延遲（vs UDP）。

## TCP segment 結構

```
 ┌─────────────────────────────────────────────────────────┐
 │ Source Port (16) │ Dest Port (16)                       │
 ├─────────────────────────────────────────────────────────┤
 │ Sequence Number (32)                                    │
 ├─────────────────────────────────────────────────────────┤
 │ Acknowledgment Number (32)                              │
 ├─────────────────────────────────────────────────────────┤
 │ Data Offset (4) │ Reserved (3) │ Flags (9) │ Window (16) │
 ├─────────────────────────────────────────────────────────┤
 │ Checksum (16)   │ Urgent Pointer (16)                   │
 ├─────────────────────────────────────────────────────────┤
 │ Options (0-40 bytes)                                    │
 ├─────────────────────────────────────────────────────────┤
 │ Payload (application data)                              │
 └─────────────────────────────────────────────────────────┘
```

關鍵欄位：

- **Port**：來源 / 目標 port（區分同 IP 上不同程式）
- **Seq Num**：這 segment 第一個 byte 在 stream 中的編號
- **Ack Num**：確認對方下一個 byte 的位置
- **Flags**：SYN / ACK / FIN / RST / PSH / URG
- **Window**：我能再收多少 byte（流控）

## 三次握手（建立連線）

```
 client                              server
   │                                   │
   ├──── SYN, seq=100 ────────────────►│   1. 我想連，我的初始 seq=100
   │                                   │
   │◄─── SYN, ACK, seq=200, ack=101 ───┤   2. 好，我的 seq=200，confirm 你 seq=101
   │                                   │
   ├──── ACK, seq=101, ack=201 ───────►│   3. 好，confirm 你 seq=201
   │                                   │
   │  = 連線建立，雙方各自開始 stream =│
```

每端維持自己的 sequence number。SYN 跟 FIN 各算 1 byte（雖然不傳 data）。

**連線狀態變化**：

```
 client: CLOSED → SYN-SENT → ESTABLISHED
 server: LISTEN → SYN-RCVD → ESTABLISHED
```

server `LISTEN` 狀態是程式 call 了 `listen()`，OS 等 SYN。

## 為什麼是 3 次不是 2 次

「**雙方都確認對方收到自己的 seq**」需要 3 次。

如果只 2 次（client 送 SYN，server 回 SYN+ACK，沒第 3 次 ACK）：

- client 不知道 server 收到 client 的 ACK 了沒
- 可能 server 的 SYN+ACK 丟掉，client 沒收到，但 server 已經以為連線成立 → 不一致

3 次握手讓**雙方都確認對方狀態**。是「**可靠連線建立**」的最少次數。

## SYN flood 攻擊

攻擊者大量送 SYN（不送第 3 次 ACK）→ server 半開連線（SYN-RCVD 狀態）佔資源 → 真正用戶連不上。

防禦：

- **SYN cookies**：server 不存 SYN-RCVD 狀態，用 cookie 編碼到 seq num
- **rate limit**：限制 SYN 速率
- **firewall** 過濾異常源 IP

## 資料傳輸

握手後，雙方獨立 stream 傳資料：

```
 client                              server
   │                                   │
   ├── PSH, ACK, seq=101, len=100 ────►│   送 100 byte
   │                                   │
   │◄─ ACK, ack=201 ───────────────────┤   confirm 你 seq=201
   │                                   │
   ├── PSH, ACK, seq=201, len=50 ─────►│   送 50 byte
   │                                   │
   │◄─ ACK, ack=251 ───────────────────┤
   │                                   │
   │◄─ PSH, ACK, seq=200, len=80 ──────┤   server 也送資料
   │                                   │
   ├── ACK, ack=280 ──────────────────►│
```

注意：

- **每方各自的 seq num**（client 從 101 開始計，server 從 200）
- **每送資料就更新 seq**
- **每收資料就回 ACK**
- 資料 + ACK 可以**捎帶**（piggyback）

## 重傳機制

如果 sender 送了沒收到 ACK（一定時間內）→ 重送：

- **timeout**：RTO（Retransmission Timeout），動態計算（基於 RTT 平均）
- **fast retransmit**：收到 3 個重複 ACK → 立刻重送（不等 timeout）

```
 sender                              receiver
   │                                   │
   ├── seq=100, len=100 ──────────────►│   送 1
   │                                   │
   ├── seq=200, len=100 ──────────────►│   送 2 （第 1 個丟掉）
   │                                   │
   │◄─ ACK, ack=100 ───────────────────┤   ack=100（期待 100，沒收）
   │                                   │
   ├── seq=300, len=100 ──────────────►│   送 3
   │                                   │
   │◄─ ACK, ack=100 ───────────────────┤   重複 ACK
   │                                   │
   ├── seq=400, len=100 ──────────────►│   送 4
   │                                   │
   │◄─ ACK, ack=100 ───────────────────┤   重複 ACK 3 次
   │                                   │
   ├── seq=100, len=100 (重送) ───────►│   fast retransmit
   │                                   │
   │◄─ ACK, ack=500 ───────────────────┤   全部收到
```

## 流量控制（Flow Control）

receiver 用 **Window** 欄位告訴 sender「我還能收多少」：

```
 receiver buffer:
 ┌──────────┬──────────┬──────────┐
 │  已 read  │  收 unread │   free   │
 │  (1024)  │   (2048)   │  (5120)  │
 └──────────┴──────────┴──────────┘
                              ↑
                          window=5120
```

window=0 → sender 暫停送。

當 receiver 處理 unread → free 增大 → window 增大 → sender 繼續送。

**程式 call `read()` 越快 → window 維持大 → 吞吐量高**。

## 擁塞控制（Congestion Control）

不同於流量控制（保護 receiver），擁塞控制保護**網路**。

核心邏輯：「**送了沒回，可能網路塞了，慢一點**」。

主要演算法：

| 演算法 | 特性 |
|---|---|
| Tahoe | 1988 年最早 |
| Reno | 改進，至今基礎 |
| **CUBIC** | Linux 預設，激進但公平 |
| **BBR** | Google 提出，更新，按頻寬-延遲 product 控制 |

最簡化邏輯（CUBIC 風格）：

```
 1. 慢啟動 (slow start)：送 1 → 2 → 4 → 8 → 16 ... 指數增長
 2. 達到 ssthresh → 改線性增長
 3. 偵測丟包 → 視情況減半 / 重啟慢啟動
```

新版 Linux 用 BBR：

```bash
sysctl net.ipv4.tcp_congestion_control
# net.ipv4.tcp_congestion_control = cubic
sudo sysctl -w net.ipv4.tcp_congestion_control=bbr
```

BBR 在跨國 / 不穩網路通常比 CUBIC 好 20-50%。

## 四次揮手（關閉連線）

```
 client                              server
   │                                   │
   ├── FIN, seq=500 ──────────────────►│   1. 我說完了
   │                                   │
   │◄─ ACK, ack=501 ───────────────────┤   2. 收到（半關閉，server 還能送）
   │                                   │
   │◄─ FIN, seq=900 ───────────────────┤   3. 我也說完了
   │                                   │
   ├── ACK, ack=901 ──────────────────►│   4. 收到
   │                                   │
   │  = 連線關閉 =                     │
```

為什麼 4 次：TCP 是**雙向**的。每方關閉自己的方向，所以兩個 FIN + 兩個 ACK = 4 次。

簡化：可以「捎帶」 → server 第 2 步直接 ACK + FIN 一起送 → 變 3 次（少見）。

## TIME_WAIT 狀態

主動關閉方（先送 FIN 的）會進入 **TIME_WAIT** 狀態，持續 **2 × MSL**（Maximum Segment Lifetime，通常 60-120 秒）：

```bash
ss -tan state time-wait | wc -l
# 30000  (在繁忙 web server 上常見)
```

為什麼有 TIME_WAIT：

1. 確保最後 ACK 送達（如果丟，對方會重送 FIN，你能再回）
2. 讓殘留的舊 packet 在網路上死光，避免新連線收到舊 packet

副作用：

- 佔用 src port → ephemeral port range 不夠用
- web server 大量短連線時會 hit limit

調 sysctl：

```bash
# 縮短 TIME_WAIT
sysctl net.ipv4.tcp_fin_timeout=30

# 重用 TIME_WAIT 的 port
sysctl net.ipv4.tcp_tw_reuse=1
```

## 觀察 TCP 狀態

```bash
ss -tan
# State      Recv-Q Send-Q  Local           Peer
# LISTEN     0      128     0.0.0.0:22      0.0.0.0:*
# ESTAB      0      0       192.168.1.10:54321  93.184.216.34:443
# TIME-WAIT  0      0       192.168.1.10:54320  93.184.216.34:443
# CLOSE-WAIT 0      0       192.168.1.10:54319  93.184.216.34:443
```

11 個 TCP 狀態：CLOSED, LISTEN, SYN-SENT, SYN-RCVD, ESTABLISHED, FIN-WAIT-1, FIN-WAIT-2, CLOSE-WAIT, CLOSING, LAST-ACK, TIME-WAIT。

正常流程懂這些就好。**異常狀態多 = bug**。

## 一個常見誤解：「TCP 比 UDP 永遠慢」

**部分對**。TCP overhead（握手、確認、流控）增加延遲，但**正常情況下吞吐量更高**（因為擁塞控制 + 大 window）。

UDP 在低延遲、可容忍丟包場景（影片、遊戲、DNS）勝出。**選擇看場景**。

## 一個常見誤解：「3 次握手後資料就無延遲」

**錯**。即使連線建好，第一次 send 還要：

- 等 send buffer flush
- 等 ACK 才能送下一波（除非 window 大）
- 慢啟動限制初始速率

**TCP 達到「最佳速率」需要幾百 ms 或幾 s**。

## 一個常見誤解：「TIME_WAIT 是 bug」

**錯**。TIME_WAIT 是設計需要的。

太多 TIME_WAIT 是**症狀**：你的 server 大量短連線。改用 connection pool / keep-alive 比改 sysctl 好。

## 一個常見誤解：「seq num 從 0 開始」

**錯**。每個連線的初始 seq 是**隨機**的（OS 用一個演算法產生）。

歷史原因：防止舊連線的 packet 影響新連線（如果 seq 都從 0 開始，舊新混淆容易）。

## 動手練習

**1. 看 TCP 三次握手**

```bash
sudo tcpdump -nn -i any 'host example.com and tcp port 443' &
curl https://example.com
```

找前 3 個 packet：SYN, SYN-ACK, ACK。

**2. 觀察 TCP 狀態**

```bash
ss -tan
ss -tan state established
ss -tan state time-wait
```

**3. 故意製造 TIME_WAIT**

```bash
for i in {1..100}; do curl -s -o /dev/null https://example.com; done
ss -tan state time-wait | wc -l
```

立刻會看到很多。

**4. 試 BBR**

```bash
# 改 cong control
sudo sysctl -w net.ipv4.tcp_congestion_control=bbr
ss -tin   # 看當前連線用什麼 algo
```

**5. 跑 iperf 測 TCP 吞吐**

```bash
# server 端
iperf3 -s

# client 端
iperf3 -c <server-IP>
```

看吞吐量。換 cong control 重測比較。

## 自我檢核

- [ ] 講得出 3 次握手的 seq / ack 變化
- [ ] 知道為什麼是 3 次不是 2 次
- [ ] TCP segment 5 個關鍵欄位記得
- [ ] 流控 vs 擁塞控制的差別
- [ ] 知道 TIME_WAIT 為什麼存在、副作用
- [ ] 看過 11 個 TCP 狀態
- [ ] 跑過 tcpdump 看握手

下一章看 UDP 跟 TCP 的選擇 — 不是 TCP 永遠對。

→ [Ch 7 UDP 與 TCP 的選擇](./07-udp-vs-tcp.md)
