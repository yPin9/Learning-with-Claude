# Ch 16 — traceroute / mtr / ping

> 目標：精通網路路徑診斷工具，能找出延遲、丟包、路由問題在哪一跳。

## ping：最簡單但最常用

測「對方 alive 嗎、RTT 多少」。Ch 4 講過 ICMP 的原理。

```bash
ping example.com
ping -c 5 example.com           # 送 5 個就停
ping -c 5 -i 0.2 example.com   # 0.2 秒一個（fast）
ping -c 5 -s 1500 example.com  # 大 packet
ping -c 5 -t 1 example.com     # TTL=1
ping -c 5 -W 2 example.com     # timeout 2 秒
ping -c 5 -I eth0 example.com  # 指定 source interface
```

輸出：

```
PING example.com (93.184.216.34) 56(84) bytes of data.
64 bytes from 93.184.216.34: icmp_seq=1 ttl=49 time=180 ms
64 bytes from 93.184.216.34: icmp_seq=2 ttl=49 time=181 ms
64 bytes from 93.184.216.34: icmp_seq=3 ttl=49 time=180 ms

--- example.com ping statistics ---
3 packets transmitted, 3 received, 0% packet loss, time 2003ms
rtt min/avg/max/mdev = 180.0/180.3/181.0/0.4 ms
```

關鍵：

- `time=180 ms` — RTT
- `ttl=49` — server 預設 TTL 64，扣到 49 = 經過 15 跳
- `0% packet loss` — 沒丟
- `mdev=0.4` — 標準差（穩定）

## ping 不到 ≠ 對方掛了

可能：

- 對方 firewall 擋 ICMP（很多公司 server 擋）
- 中間 router 限速 ICMP
- IPv4 vs IPv6 問題（用 `ping6` 確認）

只能說「**ICMP echo 不通**」，不能說「**TCP / UDP 也不通**」。

```bash
# 試 TCP
nc -zv example.com 443
# 或 curl
curl -I https://example.com
```

## traceroute

「**packet 經過哪些 router**」 — 利用 TTL trick：

1. 送 TTL=1 的 packet → 第 1 跳路由器 TTL 用完 → 回 ICMP TIME EXCEEDED → 知道第 1 跳是誰
2. 送 TTL=2 → 第 2 跳回 → 知道第 2 跳
3. ...
4. 直到送到目的地（回 ICMP ECHO REPLY 或 ICMP DEST UNREACHABLE）

```bash
traceroute example.com
traceroute -n example.com           # 不解 DNS（快很多）
traceroute -I example.com           # 用 ICMP（預設 UDP）
traceroute -T -p 443 example.com    # 用 TCP SYN（穿過某些 firewall）
traceroute -m 30 example.com        # 最多 30 跳
```

輸出：

```
traceroute to example.com (93.184.216.34), 30 hops max, 60 byte packets
 1  192.168.1.1 (192.168.1.1)  1.234 ms  1.123 ms  1.456 ms
 2  10.10.10.1 (10.10.10.1)    5.678 ms  5.123 ms  5.789 ms
 3  * * *
 4  61.222.x.x (...)            12.345 ms  12.123 ms  12.456 ms
...
15  93.184.216.34 (93.184.216.34) 180.123 ms  180.456 ms  180.789 ms
```

每行 = 1 跳：

- 跳號 + IP + RTT × 3（送 3 packet 取樣）
- `* * *` — 該跳 router 不回 ICMP（但下一跳能繼續）

## traceroute 為什麼用 UDP

預設 UDP（送到隨機高 port，期待對方回 ICMP DEST UNREACHABLE）。

但**很多 firewall 擋 UDP**，看不到中間跳。改用：

```bash
traceroute -I example.com       # ICMP（同 ping）
traceroute -T -p 443 example.com   # TCP SYN to port 443
```

TCP 穿透性最高（HTTPS port 多數開）。

## mtr：traceroute + ping 的組合

`mtr` (My TraceRoute) — **持續送 packet 到每跳，統計丟包率**：

```bash
mtr example.com
mtr -n example.com           # 不解 DNS
mtr -r -c 100 example.com    # 跑 100 cycle 後印 report
mtr -T -P 443 example.com    # TCP
```

輸出（互動式）：

```
                                Packets               Pings
 Host                         Loss%   Snt   Last   Avg  Best  Wrst StDev
 1. 192.168.1.1                0.0%    50    1.2   1.1   1.0   2.5   0.3
 2. 10.10.10.1                 0.0%    50    5.6   5.5   5.0  10.0   0.8
 3. ???                       100.0%    50    0.0   0.0   0.0   0.0   0.0
 4. 61.222.x.x                10.0%    50   25.4  26.0  20.0  35.0   2.5
 5. 93.184.216.34              0.0%    50  180.1 181.0 180.0 185.0   1.0
```

**丟包定位神器**。看到第 4 跳 `Loss% 10.0`，剩下都 0% → 第 4 跳路由器有問題。

## 解讀 traceroute / mtr

### 場景 1：第 N 跳完全 `* * *`

可能：

- 該 router 不回 ICMP（policy）
- 該 router 過載
- 對自己 IP 沒 reverse DNS

**看下一跳是否正常**。如果下一跳正常，這跳只是「**靜默轉發**」，沒問題。

### 場景 2：某跳之後全部 `* * *`

該跳是真的斷了。有可能：

- router 真的 down
- 路徑黑洞
- ISP 內部問題（你看不到）

對策：換條路（VPN / 不同 ISP）。

### 場景 3：某跳 RTT 突然飆高

例：

```
 5. router-A   25 ms
 6. router-B   120 ms      ← 這跳延遲爆增
 7. router-C   125 ms
 8. server     130 ms
```

第 6 跳延遲增加，後面跳也都帶這延遲（疊加）。**第 6 跳是瓶頸**。

可能原因：跨國 / 海底電纜 / overload。

### 場景 4：某跳 loss% 高

```
 3. routerX  loss% 30   ← 30% 丟包
 4. routerY  loss% 0    ← 但下一跳 0% loss？
```

奇怪？因為 mtr 對「中間跳」的 loss 計算可能有偏差（router 處理 ICMP 的優先順序低）。

**看「最終目的地」的 loss% 才準**。如果 destination loss% 0，前面跳的 loss% 是 router 計策，不是真丟。

## ping vs traceroute vs mtr

| 工具 | 用途 |
|---|---|
| ping | 對方 alive？RTT？ |
| traceroute | 路由經過誰？哪跳卡？ |
| mtr | 持續監控、找丟包跳 |

debug 順序：

1. `ping` — 對方通嗎
2. `traceroute` — 路徑哪段斷
3. `mtr -r -c 100` — 持續觀察哪段丟包

## MTU 探測

「**Path MTU**」 — 整條路徑的最小 MTU。如果 packet 大於最小 MTU，路徑會出問題。

```bash
# 設定 don't fragment + 大 packet
ping -M do -s 1472 -c 1 example.com
# 1472 + 28 (ICMP+IP header) = 1500，正好 Ethernet MTU

# 試大一點
ping -M do -s 2000 -c 1 example.com
# From ... Frag needed and DF set (mtu = 1500)
```

二分搜尋找最小 MTU：

```bash
for size in 1500 1480 1472 1460 1450 1420; do
    echo "=== size $size ==="
    ping -M do -s $((size - 28)) -c 1 -W 2 example.com
done
```

VPN 連線常用：找 VPN 的最佳 MTU，避免 fragmentation。

## 一個常見誤解：「traceroute 走的路徑就是反向也走」

**錯**。網路常**不對稱**（forward 跟 backward 走不同路）。

你看的 traceroute 是「**你 → 對方**」的路徑。對方回你可能走完全不同路。

要看反向：在對方 server 跑 traceroute 對你（前提是你能 SSH 進去）。

## 一個常見誤解：「丟包率 1% 就糟」

**部分對**。對 TCP：

- 1% 丟包 → throughput 會慢 50-90%（TCP 擁塞控制）
- 5% 丟包 → 幾乎無法用

對 UDP / 即時應用：

- 1% 可接受
- 5%+ 開始有感

「**TCP 對丟包極敏感**」。

## 一個常見誤解：「RTT 高 = 網路爛」

**部分對**。RTT 高可能是物理距離（光速限制）。台灣 → 美國光纖往返 ~150ms 是物理極限。

「**慢但穩定**」 vs 「**有時快有時慢**」 — 後者更壞（jitter 大）。

## 動手練習

**1. ping 各種對象**

```bash
ping -c 3 127.0.0.1            # localhost (~0.05ms)
ping -c 3 192.168.1.1          # router (~1ms)
ping -c 3 8.8.8.8              # google (~10-30ms)
ping -c 3 example.com          # 跨國 (~150ms)
```

**2. traceroute 比較**

```bash
traceroute -n 8.8.8.8                # UDP
traceroute -n -I 8.8.8.8             # ICMP
traceroute -n -T -p 443 example.com  # TCP
```

對比結果。

**3. mtr 找問題**

```bash
mtr -r -c 50 example.com
```

把 report 看一遍。哪跳 RTT 增加、哪跳 loss%。

**4. MTU 探測**

```bash
for size in 1472 1452 1420 1380; do
    echo "=== $size ==="
    ping -M do -s $size -c 1 example.com
done
```

**5. 對 VPS 跑 mtr**

```bash
mtr -r -c 30 <YOUR_VPS_IP>
```

看到的是你 → VPS 的路徑質量。

## 自我檢核

- [ ] ping 不通不代表掛掉（可能擋 ICMP）
- [ ] 知道 traceroute 用 TTL trick
- [ ] 用 mtr 找過丟包跳
- [ ] 知道 traceroute UDP vs ICMP vs TCP 差別
- [ ] MTU 探測做過至少 1 次
- [ ] 知道網路路徑常不對稱

下一章看 nmap / netcat / curl 進階。

→ [Ch 17 nmap / netcat / curl 進階](./17-nmap-netcat-curl.md)
