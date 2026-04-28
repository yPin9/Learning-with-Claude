# Ch 7 — UDP 與 TCP 的選擇

> 目標：搞懂 UDP 跟 TCP 的本質差別，知道什麼時候該用哪個。新時代 QUIC 在 UDP 上重建 TCP 也是這章背景。

## UDP 是什麼

**User Datagram Protocol** — 在 IP 上加最小必要資訊：

```
 ┌──────────────────────────────────────────────┐
 │ Source Port (16) │ Dest Port (16)            │
 ├──────────────────────────────────────────────┤
 │ Length (16)      │ Checksum (16)             │
 ├──────────────────────────────────────────────┤
 │ Payload                                       │
 └──────────────────────────────────────────────┘
```

只有 **8 byte header**。

UDP 提供：
- src/dst port（區分程式）
- 簡單 checksum
- **沒了**

UDP 不提供：
- 連線（沒 handshake）
- 可靠性（沒重傳）
- 順序（沒 sequence num）
- 流控
- 擁塞控制

**UDP = IP + port，幾乎沒加東西**。

## TCP vs UDP 對照

| 特性 | TCP | UDP |
|---|---|---|
| 連線 | 有（3 次握手） | 沒（直接送） |
| 可靠 | 有（重傳） | 沒 |
| 順序 | 保證 | 不保證 |
| 流控 | 有 | 沒 |
| 擁塞控制 | 有 | 沒 |
| Header 大小 | 20+ byte | 8 byte |
| 速度 | 慢（overhead 大） | 快 |
| 一次送的大小 | 任意（會分段） | 通常 < MTU |
| 適合 | 檔案 / web / SSH | DNS / 遊戲 / 影片 |

## 何時用 UDP

### 1. 一問一答（短）

DNS 查詢：「這個 domain 的 IP？」 + 回「IP 是 X」。一個 packet 一來一回，TCP 握手浪費。

### 2. 即時性 > 可靠性

影片串流：丟 1 frame 沒事，重傳那 frame 反而**讓你聽到/看到延遲版**。直接放棄。

遊戲：玩家位置每 16ms 送一次。舊位置已沒意義，重傳浪費。

### 3. Multicast / Broadcast

TCP 點對點，沒辦法 1 對多。UDP 能。

### 4. 自己重新實作可靠性

QUIC（HTTP/3 底層）就是「在 UDP 上重建 TCP」。為什麼？因為 OS kernel 改 TCP 演算法太慢，UDP 讓你在 user space 自由做。

## 何時用 TCP

### 1. 檔案傳輸 / Web

需要保證每個 byte 都到、順序對。FTP / HTTP / SSH 都用 TCP。

### 2. 大量資料

TCP 流控 + 擁塞控制讓大檔傳輸不會塞網路。UDP 自己做這個太累。

### 3. 雙向互動

SSH 互動：每個按鍵需要可靠傳。

## DNS 在 UDP 跟 TCP

DNS 預設用 **UDP port 53**。原因：

- query 通常 < 100 byte
- 一問一答
- 應用層自己 retry 1 次

**特殊**：

- query 太大（> 512 byte，DNSSEC 簽章會超過）→ fall back 到 TCP
- zone transfer（DNS 主從同步）→ 一定 TCP

```bash
# UDP 查詢（預設）
dig example.com

# 強制 TCP
dig +tcp example.com
```

## QUIC：UDP 上的新 TCP

HTTP/3 不再用 TCP，改用 **QUIC** (Quick UDP Internet Connections)：

- 跑在 UDP 上
- 自己實作可靠 / 順序 / 擁塞控制
- 整合 TLS（連線建立 + 加密一起做）
- 多 stream 在 1 個連線內，互不阻塞（解決 HTTP/2 head-of-line blocking）

**為什麼 UDP 不直接用 TCP**：

1. TCP 演算法在 OS kernel，更新慢
2. TCP 跟 TLS 兩次握手累
3. 中間設備（路由器、firewall）對 TCP option 處理保守，限制創新
4. UDP 自由

QUIC 是「**TCP 的精神繼承者**」，但跑在 UDP 上。Ch 39 進階速覽。

## 一個常見誤解：「UDP 就是不可靠」

**部分對**。UDP **協定本身不可靠**，但**應用可以建可靠性在它上面**。

QUIC、自家遊戲協定、DTLS 都是「**UDP + 自家可靠機制**」。

## 一個常見誤解：「UDP 永遠快」

**錯**。UDP 沒擁塞控制 → 大量送會塞網路 → 對自己 / 別人都壞。

「**正常用法的 UDP**」快；「**亂送的 UDP**」可能比 TCP 還慢（因為 packet drop）。

## 一個常見誤解：「TCP 連線永遠 alive」

**錯**。TCP 連線可以**死掉但雙方都不知道**：

- 中間網路 down 一段時間
- 對方主機被拔電源
- NAT 表 timeout

「**dead connection**」直到下次有人想送 packet 才發現。

解決：應用層 keepalive（每幾秒送個 ping）或 SO_KEEPALIVE socket option。

## 一個常見誤解：「sendto() 後 UDP 一定在網路上了」

**部分對**。OS buffer 可能還沒送出去。對方收到的時候已經是「**最終狀態**」 — 但**送出之前**有一段時間在你的 buffer / 網卡 queue / 路由器 queue / ...。

UDP「**送出 ≠ 已送達**」。要回信才知。

## 動手練習

**1. UDP 應用看一看**

```bash
# DNS 用 UDP
sudo tcpdump -nn -i any 'udp port 53' &
dig example.com

# NTP 用 UDP
sudo tcpdump -nn -i any 'udp port 123' &
ntpdate -q pool.ntp.org
```

**2. TCP vs UDP 速度測試**

```bash
# server: TCP
iperf3 -s

# client: TCP
iperf3 -c <server>

# UDP（要指定 bandwidth）
iperf3 -c <server> -u -b 100M
```

對比 throughput / loss rate。

**3. 故意 UDP 丟包**

```bash
# server
iperf3 -s

# client（限頻寬讓網路塞）
iperf3 -c <server> -u -b 1G
```

`-b 1G` 比網路能 handle 多 → 大量 drop。看 lost rate。

**4. 看 QUIC traffic**

```bash
sudo tcpdump -nn -i any 'udp port 443' &
curl --http3 https://www.google.com -o /dev/null
```

(curl 要 HTTP/3 編譯版，多數系統沒有，用 Chrome 觀察就好)

**5. 寫個 UDP echo server**

```python
# server.py
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(('0.0.0.0', 9999))
while True:
    data, addr = s.recvfrom(1024)
    print(f"from {addr}: {data}")
    s.sendto(data, addr)
```

```python
# client.py
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.sendto(b"hello", ('127.0.0.1', 9999))
print(s.recvfrom(1024))
```

跑起來。再用 tcpdump 看 packet。

## 自我檢核

- [ ] UDP header 8 byte 內容講得出
- [ ] TCP vs UDP 對照表 5+ 項記得
- [ ] 知道何時該用 UDP（DNS / 遊戲 / 影片 / multicast）
- [ ] 知道 QUIC 是什麼、為什麼基於 UDP
- [ ] 寫過 UDP echo server / client
- [ ] 跑過 iperf3 對比 TCP / UDP

下一章看 NAT — 為什麼家用網路只有 1 個公網 IP 卻能多裝置上網。

→ [Ch 8 NAT 完整解析](./08-nat-explained.md)
