# Ch 4 — 網路層：IP / ICMP / 路由

> 目標：搞懂 IP 怎麼跨網段傳輸、路由表怎麼決定下一跳、ICMP 在裡面扮演什麼角色。

## 網路層的工作

鏈結層只能在同網段內。**跨網段** 就是網路層（IP）的事。

```
 host A           router 1          router 2          host B
 (10.0.0.10)      (邊界)            (邊界)            (192.168.1.20)
      │              │                  │                 │
      ├──同網段─────►│                  │                 │
      │              ├───跨網段────────►│                 │
      │              │                  ├───跨網段───────►│
      │              │                  │                 │
```

每段是不同 broadcast domain，IP 提供「**邏輯位址**」+「**路由**」貫穿。

## IPv4 packet 結構

```
 ┌────────────────────────────────────────────────────────┐
 │ Version (4 bit) │ IHL (4)  │ ToS (8)  │ Length (16)   │
 ├────────────────────────────────────────────────────────┤
 │ Identification (16)        │ Flags (3)│ Frag offset(13)│
 ├────────────────────────────────────────────────────────┤
 │ TTL (8)         │ Protocol (8) │ Header checksum (16) │
 ├────────────────────────────────────────────────────────┤
 │ Source IP (32)                                         │
 ├────────────────────────────────────────────────────────┤
 │ Destination IP (32)                                    │
 ├────────────────────────────────────────────────────────┤
 │ Options (0-40 bytes)                                   │
 ├────────────────────────────────────────────────────────┤
 │ Payload (IP 上層資料，TCP/UDP/ICMP 等)                 │
 └────────────────────────────────────────────────────────┘
```

關鍵欄位：

- **TTL** (Time To Live)：經過 1 個路由器 -1，到 0 就丟掉。防止無限循環
- **Protocol**：1=ICMP, 6=TCP, 17=UDP
- **Source / Dst IP**：32-bit IPv4 位址

## IP 路由

每台設備有**路由表**：

```bash
ip route
# default via 192.168.1.1 dev wlan0
# 192.168.1.0/24 dev wlan0 proto kernel scope link src 192.168.1.10
# 10.0.0.0/8 via 10.10.10.1 dev tun0
```

讀法：

| 條目 | 意義 |
|---|---|
| `default via 192.168.1.1` | 不知道怎麼走的全送 192.168.1.1 |
| `192.168.1.0/24 dev wlan0` | 192.168.1.x 從 wlan0 直接出（同網段） |
| `10.0.0.0/8 via 10.10.10.1 dev tun0` | 10.x.x.x 經 tun0、下一跳 10.10.10.1 |

OS 收到要送的 packet：

1. 查路由表
2. 找最具體（longest prefix match）的條目
3. 決定 outgoing interface + next hop
4. ARP 找 next hop 的 MAC
5. 包成 frame 送出

## 路由的具體例

你要 ping `93.184.216.34`：

```bash
ip route get 93.184.216.34
# 93.184.216.34 via 192.168.1.1 dev wlan0 src 192.168.1.10
```

意思：

- **dst**: 93.184.216.34
- **next hop**: 192.168.1.1（你的家用路由器）
- **outgoing interface**: wlan0
- **src**: 192.168.1.10（你的本機 IP）

OS 包個 IP packet（dst=93.184.216.34），ARP 問「192.168.1.1 的 MAC」，包個 Ethernet frame（dst MAC = 路由器 MAC）送出。

路由器收到後：

1. 看 IP packet 的 dst（93.184.216.34）
2. 查自己的路由表
3. 決定下一跳（可能是 ISP 的設備）
4. 重新包 frame、改 src/dst MAC、送出
5. **TTL -1**

每跳重複，最終到 server。

## TTL 的功能

防止 packet 無限循環。

預設值：

- Linux: 64
- Windows: 128
- macOS: 64

每台路由器轉發時 TTL -1。到 0 → 丟掉、回 ICMP TIME EXCEEDED。

**`traceroute` 利用這個 trick** — 故意送 TTL=1, 2, 3... 收 ICMP TIME EXCEEDED 看每一跳是誰。Ch 16 詳細。

## ICMP（Internet Control Message Protocol）

IP 層的「**控制訊息**」協定。常見 ICMP 訊息：

| Type | Code | 名稱 | 用途 |
|---|---|---|---|
| 8 | 0 | Echo Request | ping 用 |
| 0 | 0 | Echo Reply | ping 回 |
| 3 | 0 | Net Unreachable | 路由不到 |
| 3 | 1 | Host Unreachable | 主機不通 |
| 3 | 3 | Port Unreachable | UDP 沒程式 listen |
| 3 | 4 | Fragmentation Needed | MTU 太大 |
| 11 | 0 | TTL Expired | TTL 到 0 |
| 5 | 0 | Redirect | 路由通知 |

ICMP 用 IP packet 載送，但 protocol = 1（不是 TCP/UDP）。

## ping 的實作

`ping example.com`：

1. DNS → IP
2. 包 ICMP Echo Request（type=8）+ IP header（dst=server IP, protocol=1）
3. 路由送出
4. server 收到 → 回 ICMP Echo Reply（type=0）
5. 計算 round trip time

```bash
ping -c 3 example.com
# PING example.com (93.184.216.34) 56(84) bytes of data.
# 64 bytes from 93.184.216.34: icmp_seq=1 ttl=49 time=180 ms
# 64 bytes from 93.184.216.34: icmp_seq=2 ttl=49 time=181 ms
# 64 bytes from 93.184.216.34: icmp_seq=3 ttl=49 time=180 ms
```

`ttl=49`：server 預設 TTL 64，被中間 15 個路由器扣到 49 → 你跟 server 隔 15 跳。

## fragmentation（IP 分片）

如果 IP packet 太大（> MTU），中間路由器會**分片**：

```
 原始 packet: 3000 bytes
 第一片: bytes 0-1480 (含 IP header, more=1)
 第二片: bytes 1480-2960 (more=1)
 第三片: bytes 2960-3000 (more=0)
```

接收端拼回。

**問題**：

- fragmentation 慢（每片都要 header）
- 中間設備可能丟掉某一片 → 整個 packet 失效
- NAT / firewall 對 fragment 處理不完整

**現代設計**：用 **Path MTU Discovery** 找最小 MTU、avoid fragmentation。**TCP 自動做**，UDP 程式自己負責。

設定 packet 「**不要 fragment**」（DF flag）→ 太大 → 路由器回 ICMP Frag Needed → 你縮小再送。

## 一個常見誤解：「IP 保證送達」

**錯**。**IP 是 best-effort**，不保證：

- 送達（packet 可能被丟）
- 順序（後送的可能先到）
- 不重複（同 packet 可能被重送）

**TCP 在 IP 上面提供保證**。

## 一個常見誤解：「TTL 是時間」

**錯**。雖然名字是「Time」，但 TTL 是「**跳數**」。

最初設計可能想用時間，最終實作用跳數。

## 一個常見誤解：「ping 通就一切正常」

**部分對**。ping 通表示：

- 路由 OK
- 對方主機 alive
- ICMP 沒被 firewall 擋

但 **TCP 80 / 443 ping 通不代表能用**：

- TCP port 沒 listen
- HTTP server 在但回 500
- 應用層邏輯壞

**「ping 通」只說明網路層 OK**。

## 動手練習

**1. 看自己的路由表**

```bash
ip route
ip route get 8.8.8.8
ip route get example.com  # 注意：要 IP，先 dig
```

對應到本章說的「default route + LAN route」。

**2. ping 不同對象**

```bash
ping -c 3 127.0.0.1            # 自己
ping -c 3 192.168.1.1          # router
ping -c 3 8.8.8.8              # Google DNS
ping -c 3 example.com          # public site
```

對比 RTT。RTT 增加表示距離越遠。

**3. tcpdump 看 ICMP**

```bash
sudo tcpdump -nn -i any icmp &
ping -c 3 8.8.8.8
```

看 echo request / echo reply 對。

**4. 故意 TTL=1**

```bash
ping -t 1 -c 1 8.8.8.8
# From 192.168.1.1 icmp_seq=1 Time to live exceeded
```

只到一跳就 TTL 用完，路由器回 TIME EXCEEDED。

**5. 模擬 fragmentation**

```bash
# 大 packet、不 fragment
ping -M do -s 2000 -c 1 8.8.8.8
# From ... Frag needed and DF set (mtu = 1500)
```

中間路由器回 Frag Needed，要你縮小。

## 自我檢核

- [ ] 知道 IP 是 best-effort、不保證送達
- [ ] 講得出 IP packet 主要欄位（src/dst, TTL, protocol）
- [ ] 知道路由表怎麼讀、`default via X` 意義
- [ ] ICMP 5+ 種訊息的用途記得
- [ ] 知道 traceroute 用 TTL trick
- [ ] 用 tcpdump 抓過 ICMP
- [ ] 模擬過 TTL expired / Frag needed

下一章詳細看 IP 位址 — 32-bit 怎麼分類、subnet / CIDR、私有位址。

→ [Ch 5 IP 位址、subnet、CIDR](./05-ip-addressing-cidr.md)
