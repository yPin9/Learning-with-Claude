# Ch 14 — tcpdump / Wireshark

> 目標：從網路協定角度精通 packet capture：tcpdump filter 語法、Wireshark 強大功能、production 抓包標準動作。

## 兩個工具的角色

| 工具 | 場景 |
|---|---|
| `tcpdump` | command line、SSH 進 server 直接抓、輕量 |
| `Wireshark` | GUI、深度分析、跨層拆解、協定 decoding |

**標準工作流**：

1. SSH 進 server
2. tcpdump 抓 → 寫 `.pcap` 檔
3. scp 下載到本機
4. Wireshark 開來分析

## tcpdump 基本

```bash
sudo tcpdump -nn -i any -c 50
```

| flag | 意義 |
|---|---|
| `-n` | 不解析 DNS |
| `-nn` | 不解析 DNS + 不解析 port name |
| `-i <if>` | 指定 interface（`any` 抓所有）|
| `-c N` | 抓 N 個就停 |
| `-w file.pcap` | 寫檔（之後 Wireshark 開）|
| `-r file.pcap` | 讀檔 |
| `-A` | 印 ASCII payload |
| `-X` | 印 hex + ASCII |
| `-s 0` | 抓完整 packet（預設 262144，極少 truncate）|
| `-v` `-vv` `-vvv` | verbose 增加 |
| `-e` | 印 Ethernet header |
| `-tttt` | 完整 timestamp |

## tcpdump filter 語法

filter 編譯成 BPF（kernel 過濾）→ 不通過的 packet 直接 drop，**production 高 traffic 一定要 filter**。

```bash
# Host
sudo tcpdump 'host 8.8.8.8'
sudo tcpdump 'src host 8.8.8.8'
sudo tcpdump 'dst host 8.8.8.8'

# Port
sudo tcpdump 'port 80'
sudo tcpdump 'src port 443'
sudo tcpdump 'dst port 22'
sudo tcpdump 'tcp port 80'
sudo tcpdump 'udp port 53'

# Net
sudo tcpdump 'net 192.168.1.0/24'

# Protocol
sudo tcpdump 'icmp'
sudo tcpdump 'arp'
sudo tcpdump 'tcp'

# Combine
sudo tcpdump 'host 8.8.8.8 and port 53'
sudo tcpdump 'tcp port 80 or tcp port 443'
sudo tcpdump 'src host 1.1.1.1 and not port 22'

# TCP flags
sudo tcpdump 'tcp[tcpflags] & tcp-syn != 0'   # SYN packets
sudo tcpdump 'tcp[tcpflags] & tcp-rst != 0'   # RST packets
sudo tcpdump 'tcp[13] = 0x12'                  # SYN+ACK (二進制)
```

## 讀 tcpdump 輸出

```
12:34:56.123456 IP 192.168.1.10.54321 > 93.184.216.34.443: Flags [S], seq 1234567890, win 65535, options [...], length 0
```

讀法：

- `12:34:56.123456` — timestamp
- `IP` — protocol（IPv4）
- `192.168.1.10.54321 > 93.184.216.34.443` — `src.port > dst.port`（**port 用 `.` 分隔**）
- `Flags [S]` — TCP flags
  - S = SYN
  - F = FIN
  - R = RST
  - P = PSH
  - . = ACK
  - U = URG
- `seq 1234567890` — sequence
- `win 65535` — window
- `options [...]` — TCP options（mss / wscale / sack / ...）
- `length 0` — payload size

完整 TCP handshake 看起來：

```
12:00:00.001 IP 1.2.3.4.50000 > 5.6.7.8.443: Flags [S], seq 100, win 65535
12:00:00.085 IP 5.6.7.8.443 > 1.2.3.4.50000: Flags [S.], seq 200, ack 101, win 65535
12:00:00.086 IP 1.2.3.4.50000 > 5.6.7.8.443: Flags [.], ack 201, win 65535
```

`[S]` SYN, `[S.]` SYN+ACK, `[.]` ACK = 三次握手完成。

## tcpdump 寫檔 + Wireshark 分析

```bash
# Server 端
sudo tcpdump -nn -i any -w capture.pcap 'host 1.2.3.4'

# 跑 5 秒，Ctrl-C 停

# 下載到本機
scp user@server:capture.pcap ~/

# 開 Wireshark
wireshark capture.pcap
```

## Wireshark 強大功能

### 1. Protocol Hierarchy

`Statistics → Protocol Hierarchy` — 看 capture 中各 protocol 比例。

哪個 protocol 占最多 byte？常用於性能分析。

### 2. Conversations / Endpoints

`Statistics → Conversations` — 看哪兩個 IP 通信最多、傳了多少 byte。

### 3. Flow Graph

`Statistics → Flow Graph` — 把 packet 變成「對話順序圖」。debug TCP 握手 / TLS 握手 / 應用層交互極佳。

### 4. Follow Stream

右鍵 packet → `Follow → TCP Stream` / `HTTP Stream` — 把整個 stream 內容拼起來顯示。

HTTPS 因為加密看不到內容，HTTP 完整看得到。

### 5. Decrypt TLS

如果你有 server 的 TLS private key，或 client 的 SSL key log file，Wireshark 能解密 TLS：

```bash
# Firefox / Chrome 開 SSL key log
SSLKEYLOGFILE=/tmp/sslkey.log firefox
```

Wireshark → Preferences → Protocols → TLS → (Pre)-Master-Secret log filename → 選那個檔案。

之後 capture 中的 TLS 內容自動解密。**debug HTTPS 應用程式神器**。

### 6. Filter

Wireshark filter 比 tcpdump 更豐富：

```
http
http.request.method == "POST"
http.response.code == 500
tls.handshake.type == 1     # ClientHello
ip.addr == 192.168.1.10
tcp.port == 443
tcp.flags.syn == 1
tcp.analysis.retransmission
```

`tcp.analysis.*` 是 Wireshark 自動分析（retrans / out of order / dup ACK），**debug TCP 必用**。

## tcpdump 進階：「儲存 + 輪轉」

長時間抓會撐爆 disk：

```bash
sudo tcpdump -nn -i any -w 'capture-%Y%m%d-%H%M%S.pcap' -G 60 -W 100 'port 80'
```

- `-G 60`：每 60 秒換新檔
- `-W 100`：保留 100 個檔（rolling）
- `-w 'capture-%Y...'`：檔名 timestamp

production debugging 標準動作。

## tshark：tcpdump 的進化版

tshark = Wireshark 的 command line 版本。比 tcpdump 強：

```bash
# 同 tcpdump
tshark -i eth0 -f "host 8.8.8.8"

# 但能做 protocol 解析
tshark -i eth0 -f "port 80" -Y "http.request" -T fields -e http.request.method -e http.host -e http.request.uri
# GET   example.com   /
# POST  api.com       /v1/login
```

`-Y` filter（Wireshark 語法）、`-T fields -e` 抽取特定欄位。

**寫 script 解析 packet 用 tshark**，比 tcpdump 強多。

## 一個常見場景：debug 為什麼某 connection 慢

```bash
sudo tcpdump -nn -i any -w slow.pcap 'host SLOW_SERVER'

# 用慢的程式跑一下
curl https://SLOW_SERVER

# 停 tcpdump
# 開 Wireshark
wireshark slow.pcap
```

Wireshark 看：

- DNS 多久（first response time）
- TCP 握手 RTT
- TLS 握手有 retrans 嗎？
- HTTP response time
- 任何 retrans / out-of-order packet

哪段慢一目了然。

## 一個常見場景：抓 SSH connection

SSH 是加密的看不到內容，但能看握手：

```bash
sudo tcpdump -nn -i any 'tcp port 22'
```

看 banner exchange (SSH 版本字串明文)、之後就是加密。

## 一個常見場景：找誰在送大流量

```bash
sudo tcpdump -nn -i any -w big.pcap -c 10000

wireshark big.pcap
# Statistics → Conversations
# 排序 byte，看誰最多
```

## 一個常見踩雷：「抓不到任何 packet」

可能：

- 沒選對 interface（用 `tcpdump -D` 看可用 interface）
- filter 寫錯
- 流量其實在 docker / VM 內，host 抓不到
- promiscuous mode 沒開

```bash
# 看可用 interface
tcpdump -D
ip link

# 故意抓所有
sudo tcpdump -nn -i any -c 5
```

## 一個常見踩雷：「Linux 能抓到 outgoing 但抓不到 incoming」

少見但有 case — 通常 firewall 在 outgoing 之前 process incoming，看 SYN-ACK 的時候已經被 NAT 改寫。

對策：在更接近真實 wire 的點抓（如 host 而不是 container）。

## 動手練習

**1. tcpdump 看 ping**

```bash
sudo tcpdump -nn -i any 'icmp' &
ping -c 3 8.8.8.8
```

數 packet 數量。

**2. tcpdump 寫檔 + Wireshark 開**

```bash
sudo tcpdump -nn -i any -w /tmp/dns.pcap -c 20 'port 53' &
dig example.com
wait

wireshark /tmp/dns.pcap
```

**3. 用 BPF filter 抓 SYN**

```bash
sudo tcpdump -nn -i any 'tcp[tcpflags] & tcp-syn != 0 and tcp[tcpflags] & tcp-ack == 0'
```

只看 SYN（不含 SYN-ACK）—— 純粹的 connection initiation。

**4. tshark 抽 HTTP**

```bash
sudo tshark -i any -f "tcp port 80" -Y "http.request" \
    -T fields -e ip.src -e http.host -e http.request.method -e http.request.uri
# 同時開瀏覽器訪問些 HTTP（非 HTTPS）site
```

**5. Wireshark 看 retrans**

```bash
sudo tcpdump -nn -i any -w cap.pcap -c 1000

# 開 Wireshark
# Filter: tcp.analysis.retransmission
# 看有多少 retrans packet
```

## 自我檢核

- [ ] tcpdump filter 語法熟練（host / port / proto / and/or）
- [ ] 看得懂 tcpdump 輸出（src.port > dst.port: Flags [...]）
- [ ] 用過 `-w` 寫檔，scp 下載，Wireshark 開
- [ ] Wireshark 5+ 個強功能用過
- [ ] tshark 抽 HTTP fields 寫過
- [ ] 知道 SSL key log 解密 HTTPS

下一章看 dig / nslookup — DNS debug 神器。

→ [Ch 15 dig / nslookup](./15-dig-nslookup.md)
