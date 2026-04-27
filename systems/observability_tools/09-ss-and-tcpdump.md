# Ch 9 — ss / tcpdump — 網路觀察

> 目標：知道網路問題從 ss / tcpdump 哪一個切入、各自看什麼、常見場景的標準動作。

## ss 是什麼

`ss` = **Socket Statistics**。看 kernel 的 TCP / UDP / UNIX socket 表。取代老的 `netstat`（更快、更詳細）。

跟 lsof 互補：

- **lsof -i**：「誰擁有這 socket」（PID + 程式名）
- **ss**：「socket 在 TCP 哪個狀態」（cwnd, retransmit, RTT, ...）

debug 連線問題 ss 比 lsof 強，找擁有者 lsof 比 ss 強。新版 ss 也能印 PID（`-p`）兩個合一。

## 基本用法

```bash
ss                       # 所有
ss -t                    # TCP only
ss -u                    # UDP
ss -x                    # Unix domain
ss -l                    # LISTEN only
ss -a                    # 所有狀態（含 LISTEN + TIME-WAIT）
ss -n                    # 不解析 DNS / port name
ss -p                    # 顯示 PID（要 sudo）
ss -e                    # 詳細（含 inode, uid）
ss -i                    # TCP 內部 info（cwnd, rtt, retransmit...）
ss -m                    # memory 用量
ss -s                    # 一行 summary
```

最常用組合：

```bash
sudo ss -tnlp           # 所有 listening TCP，含 PID
sudo ss -tnp            # 所有 active TCP，含 PID
sudo ss -tnpi           # 加上 TCP 內部 info
```

## 輸出範例

```bash
sudo ss -tnlp
# State  Recv-Q Send-Q Local Address:Port Peer Address:Port  Process
# LISTEN 0      128    0.0.0.0:22         0.0.0.0:*          users:(("sshd",pid=1234,fd=3))
# LISTEN 0      511    127.0.0.1:6379     0.0.0.0:*          users:(("redis-server",pid=5678,fd=6))
```

```bash
sudo ss -tnp
# State Recv-Q Send-Q Local:Port Peer:Port              Process
# ESTAB 0      0      10.0.0.5:443  10.0.0.9:54321     users:(("nginx",pid=...,fd=12))
```

`Recv-Q` / `Send-Q` 重要：

- LISTEN 狀態：Recv-Q = backlog 中已 ACK 等 accept 的數量、Send-Q = backlog 上限
- 其他狀態：Recv-Q = 收到但 process 沒讀的、Send-Q = 送出但對方沒 ACK 的

**Recv-Q 一直增加 = process 不讀 socket = bug**。

## TCP 詳細狀態

```bash
sudo ss -tnpi
# ESTAB 0 0 10.0.0.5:443 10.0.0.9:54321 ...
#  cubic wscale:7,7 rto:204 rtt:0.5/0.25 mss:1448 cwnd:10 bytes_sent:1234 ...
```

`-i` 出來：

- `cubic` — congestion control algorithm
- `rto`：retransmission timeout (ms)
- `rtt`：round trip time / mdev
- `mss`：max segment size
- `cwnd`：congestion window
- `bytes_sent` / `bytes_acked`
- `retrans`：重傳次數（**這個高 = 網路爛**）

## 篩狀態

```bash
ss state established
ss state time-wait
ss state syn-sent
ss state '( established or syn-sent )'
ss '( dport = :22 or sport = :22 )'    # ssh 相關
ss dst 10.0.0.5/24                      # 連到此網段的
ss '( dport >= :1024 )'
```

## 一行 summary

```bash
ss -s
# Total: 234 (kernel 0)
# TCP:   42 (estab 12, closed 28, orphaned 0, synrecv 0, timewait 28/0)
# Transport Total IP IPv6
# *         0     -  -
# RAW       0     0  0
# UDP       8     6  2
# TCP       14    11 3
# INET      22    17 5
```

快速看「一台機器 TCP 健康嗎」：太多 timewait → port 不夠用、太多 syn-recv → 被 SYN flood / 連線爆量。

## tcpdump 是什麼

封包層級觀察。看 wire 上實際走了什麼 byte。

跟 ss 比：

- **ss** 看 kernel 已建立的 connection state
- **tcpdump** 看流動中的 packet — 包含建立失敗、retransmit、不知道是哪個 socket 的

debug「對方收得到嗎、我送了嗎」用 tcpdump。debug「我這邊 socket 狀態」用 ss。

## 基本用法

```bash
sudo tcpdump -i any                      # 所有 interface
sudo tcpdump -i eth0
sudo tcpdump -nn                         # 不解 DNS / port name
sudo tcpdump -c 10                       # 抓 10 個就停
sudo tcpdump -w out.pcap                 # 寫檔（之後 wireshark 看）
sudo tcpdump -r out.pcap                 # 讀檔
sudo tcpdump -A                          # 印 ASCII payload
sudo tcpdump -X                          # 印 hex + ASCII
sudo tcpdump -s 0                        # 抓完整 packet（預設只抓 header）
sudo tcpdump -v / -vv / -vvv             # verbose
sudo tcpdump -e                          # 印 Ethernet header
```

最常用：

```bash
sudo tcpdump -nn -i any -c 50 'host 10.0.0.5 and port 443'
sudo tcpdump -nn -i any -w /tmp/c.pcap 'host 10.0.0.5'
```

## BPF filter 語法

`tcpdump 'expression'` 用 BPF 編譯成 kernel filter，**未通過的 packet kernel 直接 drop，不傳給 userspace** —— 高 traffic 時必須用 filter，不然 tcpdump 自己就先爆。

常用 expression：

```
host 10.0.0.5
src host 10.0.0.5 and dst host 10.0.0.9
port 443
src port 80
tcp port 22
udp port 53
net 10.0.0.0/24
icmp
arp
not arp
tcp[tcpflags] & tcp-syn != 0
```

組合用 `and` / `or` / `not`：

```bash
sudo tcpdump -nn 'tcp port 80 and (host 10.0.0.5 or host 10.0.0.6)'
```

## 輸出解讀

```
12:34:56.123456 IP 10.0.0.5.443 > 10.0.0.9.54321: Flags [S.], seq 1, ack 2, win 65535, options [...], length 0
```

讀法：

- `12:34:56.123456` — timestamp
- `IP` — protocol
- `10.0.0.5.443 > 10.0.0.9.54321` — from → to（**`.443` 是 port 不是子網**）
- `Flags [S.]` — TCP flag：S=SYN, F=FIN, R=RST, P=PSH, .=ACK
- `seq 1, ack 2` — sequence / ack number
- `win 65535` — receive window
- `length 0` — payload 大小

`[S.]` 同時 SYN + ACK = handshake 第二步。完整 3-way:

```
[S]   client → server
[S.]  server → client
[.]   client → server (pure ACK)
```

之後就傳 data。

## 一個經典場景：「為什麼連不上」

step 1：client 上看封包出去沒：

```bash
sudo tcpdump -nn -i any "host SERVER and port 8080"
```

如果**只看到 SYN 沒回應** → 對方 firewall block 或 service 沒起。  
如果**看到 RST** → service 沒 listen 或被防火牆 reject。  
如果**正常 handshake 後 client 馬上 RST** → 對方 close、或 client 收到不期望的東西。

step 2：server 上看：

```bash
sudo tcpdump -nn -i any "host CLIENT and port 8080"
```

對方 SYN 有沒有到。

step 3：server 上看 ss：

```bash
sudo ss -tnlp | grep 8080
```

service 真的在 listen 嗎？對的 IP？（`0.0.0.0` 跟 `127.0.0.1` 差很多）

四個檢查能定位 90% 的「連不上」。

## 一個經典場景：「TCP 慢」

```bash
sudo ss -tni dst 10.0.0.5
```

看 `rtt` 大不大、`retrans` 高不高、`cwnd` 卡在小數字嗎。

```bash
sudo tcpdump -nn -i any -c 100 "host 10.0.0.5"
```

看是不是大量 retransmit、或 window 被打很小（`win 0`）。

## 一個經典場景：「TIME_WAIT 太多」

```bash
ss state time-wait | wc -l
# 30000
```

太多會耗光 port 範圍。`net.ipv4.ip_local_port_range` 預設只 28000 個 port。修法：

- 應用面用 connection pool，少創新連線
- `net.ipv4.tcp_tw_reuse=1` （sysctl）
- 對端用 keep-alive

## 一個常見踩雷：tcpdump 在 WSL / container 看不到

WSL2 有自己的 vNIC，`-i any` 看不到 host 的 traffic。container 看不到 host network namespace 的 packet（除非 `--network host`）。

## 一個常見踩雷：lsof / ss 結果跟 tcpdump 對不上

socket 的 close → TIME_WAIT 期間，lsof 已經看不到（因為 fd 已 close）但 ss 還列出（kernel 還在 hold）。

```bash
ss -tan state time-wait | head
```

正常現象。等 60s（`tcp_fin_timeout`）就消失。

## 一個常見踩雷：tcpdump 印 truncated

```
12:34:56 IP 10.0.0.5.443 > 10.0.0.9.54321: ... [|tcp]
```

`[|tcp]` 表示 packet 只抓了 header，payload truncated。預設 snaplen 262144 已經很大，但極大 packet 仍可能截斷。

```bash
sudo tcpdump -s 0 ...    # 抓整個
```

## 動手練習

**1. ss / lsof 對照**

```bash
sudo ss -tnlp | grep -i ssh
sudo lsof -nP -i tcp:22
```

兩邊都該看到 sshd。

**2. 製造一個 ESTABLISHED**

```bash
# terminal 1
nc -l 8888

# terminal 2
nc localhost 8888

# terminal 3
sudo ss -tnpi | grep 8888
```

看 cwnd / mss / rtt。

**3. tcpdump 看 DNS 查詢**

```bash
sudo tcpdump -nn -i any -c 5 'port 53'
```

打開瀏覽器或 `nslookup google.com`，看 query / response。

**4. tcpdump 抓 HTTP**

```bash
sudo tcpdump -nn -A -i any -c 50 'port 80'
```

另開 terminal `curl http://example.com`。可以看到明文 HTTP（HTTPS 看不到內容）。

**5. 模擬連不上**

```bash
# 連個沒人的 port
nc -nv 127.0.0.1 12345 &
sudo tcpdump -nn -i lo -c 5 'port 12345'
```

看到 SYN 出去、RST 回來。經典「Connection refused」。

## 自我檢核

- [ ] 知道 ss / lsof / tcpdump 各自最強場景
- [ ] `ss -tnlp` `ss -tnpi` `ss state ...` 用得順
- [ ] 看得懂 ss 的 Recv-Q / Send-Q 跟 retrans
- [ ] `tcpdump 'host X and port Y'` 寫得出來
- [ ] 看得懂 tcpdump flag `[S]` `[S.]` `[.]` `[F.]` `[R.]`
- [ ] 「連不上」的 4 步驟 standard procedure 記得

下一章看 sysstat 家族 — 系統層的觀察工具。

→ [Ch 10 sysstat 家族](./10-sysstat-family.md)
