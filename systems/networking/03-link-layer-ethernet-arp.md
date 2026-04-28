# Ch 3 — 鏈結層：Ethernet / ARP

> 目標：搞清楚同網段內封包怎麼傳，包括 MAC、Ethernet 幀結構、ARP 怎麼運作。

## 鏈結層的工作

「**同一個網段內，把 frame 從一個網卡送到另一個網卡**」。

關鍵詞：

- **網段（network segment / broadcast domain）**：物理 / 邏輯上同一個 LAN，能直接互相廣播
- **frame**：鏈結層的傳輸單位
- **MAC**（Media Access Control）：網卡的硬體位址

跨網段（要經過路由器）= 網路層（IP）的工作，下一章。

## MAC 位址

每張網卡有一個唯一的 **MAC**（48-bit）：

```
 aa:bb:cc:dd:ee:ff
 │     │  │
 │     │  └── 序號（廠商分配）
 │     └───── 廠商代碼（IEEE OUI）
 └──────────── 第 2 個 bit = 0/1（unicast/multicast）, 第 1 個 bit = 0/1（globally/locally administered）
```

看你的 MAC：

```bash
ip link show
# 2: enp0s3: <BROADCAST,MULTICAST,UP> ...
#     link/ether aa:bb:cc:dd:ee:ff brd ff:ff:ff:ff:ff:ff
```

`ff:ff:ff:ff:ff:ff` = broadcast（送給網段所有人）。

**MAC 是硬體出廠就燒進去的**，但現代 OS 能改（spoofing）：

```bash
sudo ip link set dev eth0 address aa:11:22:33:44:55
```

## Ethernet frame 結構

```
 ┌─────────────────────────────────────────────────────────────┐
 │ Preamble (8 bytes) │ 物理層用，bit-level 同步             │
 ├─────────────────────────────────────────────────────────────┤
 │ Dst MAC (6)        │ 目標 MAC                              │
 ├─────────────────────────────────────────────────────────────┤
 │ Src MAC (6)        │ 來源 MAC                              │
 ├─────────────────────────────────────────────────────────────┤
 │ Type (2)           │ 0x0800=IPv4, 0x86DD=IPv6, 0x0806=ARP │
 ├─────────────────────────────────────────────────────────────┤
 │ Payload (46-1500)  │ 上層資料（IP packet 等）              │
 ├─────────────────────────────────────────────────────────────┤
 │ FCS (4)            │ CRC 校驗                              │
 └─────────────────────────────────────────────────────────────┘
```

**MTU**（Maximum Transmission Unit）= payload 上限 = 1500 bytes（標準 Ethernet）。

特殊：

- **Jumbo frame**: MTU 9000（高速網路）
- **PPPoE**: MTU 1492（少 8 byte 給 PPPoE header）
- **VPN**: MTU 1420 左右（少 80 byte 給 VPN encap）

MTU 不對齊 → packet 被丟（或 fragment）→ 連線斷斷續續。**Ch 16 traceroute** 會講 MTU 探測。

## ARP（Address Resolution Protocol）

問題：你只知道對方的 IP，怎麼知道對方的 MAC？

ARP 解決：**廣播問所有人**「誰是這個 IP？」對應的人回答自己的 MAC。

### ARP request

```
 ┌─────────────────────────────────────────────────────────────┐
 │ Eth dst: ff:ff:ff:ff:ff:ff (broadcast)                      │
 │ Eth src: aa:bb:cc:dd:ee:ff (你)                             │
 │ Eth type: 0x0806 (ARP)                                      │
 ├─────────────────────────────────────────────────────────────┤
 │ ARP op: 1 (request)                                         │
 │ Sender MAC: aa:bb:cc:dd:ee:ff                               │
 │ Sender IP: 192.168.1.10                                     │
 │ Target MAC: 00:00:00:00:00:00 (unknown)                     │
 │ Target IP: 192.168.1.1                                      │
 └─────────────────────────────────────────────────────────────┘
```

整個網段都收到。**只有 IP 是 192.168.1.1 的設備會回**。

### ARP reply

```
 ┌─────────────────────────────────────────────────────────────┐
 │ Eth dst: aa:bb:cc:dd:ee:ff (你)                             │
 │ Eth src: 11:22:33:44:55:66 (路由器)                         │
 │ Eth type: 0x0806 (ARP)                                      │
 ├─────────────────────────────────────────────────────────────┤
 │ ARP op: 2 (reply)                                           │
 │ Sender MAC: 11:22:33:44:55:66                               │
 │ Sender IP: 192.168.1.1                                      │
 │ Target MAC: aa:bb:cc:dd:ee:ff                               │
 │ Target IP: 192.168.1.10                                     │
 └─────────────────────────────────────────────────────────────┘
```

unicast 回給你，告訴你它的 MAC。

### ARP cache

問完之後，OS 把答案存到 **ARP cache** 一段時間（通常 60 秒）：

```bash
ip neigh
# 192.168.1.1 dev wlan0 lladdr 11:22:33:44:55:66 REACHABLE
# 192.168.1.20 dev wlan0 lladdr 22:33:44:55:66:77 STALE
```

下次同一個 IP，**直接從 cache 拿**，不再廣播。

清 cache：

```bash
sudo ip neigh flush all
```

## 觀察 ARP 流量

```bash
# 終端 1
sudo tcpdump -nn -i eth0 arp

# 終端 2
sudo ip neigh flush all
ping -c 1 192.168.1.1
```

第一個 ping 之前，會看到 ARP request + reply。

## ARP spoofing（攻擊）

ARP 沒驗證機制 — **任何人聲稱是某個 IP，網段所有人都信**。

攻擊：

1. 攻擊者廣播：「我是 192.168.1.1（路由器）」
2. 受害者更新 ARP cache：「路由器 MAC = 攻擊者 MAC」
3. 受害者所有出網 packet 都送到攻擊者
4. 攻擊者轉發到真路由器（man-in-the-middle）

工具：`arpspoof`、`ettercap`、`bettercap`。

防禦：

- **靜態 ARP entry**（手動設）
- **DHCP snooping + ARP inspection**（企業 switch 功能）
- **HTTPS / TLS**（即使被 MITM 也加密）

## 鏈結層 vs 網路層

關鍵差別：

| 維度 | 鏈結層（Ethernet） | 網路層（IP） |
|---|---|---|
| 範圍 | 同網段 | 跨網段 |
| 位址 | MAC（48-bit） | IP（32 / 128-bit） |
| 範圍 | 1 hop | 多 hop |
| 廣播 | 物理可廣播 | 不能（除非 multicast） |
| 路由 | 沒有路由概念 | 有路由表 |

**MAC 是「在這條線上」的位址，IP 是「在 Internet 上」的位址**。

## 一個常見踩雷：MAC 跨網段就消失

```
 你 → 路由器 → 中間路由器 → 中間路由器 → server
```

從你發出去的 frame 中，**只有「你 ↔ 你的路由器」這一段用得到 MAC**。

中間每一段都重新封 frame、重新 ARP、重新 set 來源/目標 MAC。

**MAC 不傳遞跨網段**。

## 一個常見踩雷：「ping 不到 = MAC 找不到」

不一定。ping 不到可能：

- ARP fail（local 範圍內）
- 路由錯（IP 層）
- firewall 擋
- 對方 down

確認 ARP：

```bash
ip neigh show 192.168.1.1
```

如果是 `INCOMPLETE` 或 `FAILED` → 就是 ARP 問題。其他狀態 → 看更上層。

## 一個常見踩雷：兩個設備同 MAC

幾乎不可能，但 spoofing 或廠商 bug 會。

症狀：兩設備互相搶網路、隨機斷線。

debug：

```bash
arping -I eth0 192.168.1.1
# 如果回兩個 reply，就是有 MAC 衝突
```

## 動手練習

**1. 看自己的 ARP cache**

```bash
ip neigh
```

通常包含：你的 router、近期通信過的 device。

**2. 觀察 ARP request / reply**

```bash
sudo tcpdump -nn -i any arp &
sudo ip neigh flush all
ping -c 1 -W 1 192.168.1.1   # 改成你的 router IP
```

看 tcpdump 的輸出。

**3. 改自己的 MAC**

```bash
sudo ip link set dev eth0 down
sudo ip link set dev eth0 address aa:11:22:33:44:55
sudo ip link set dev eth0 up
```

**測試完改回來**。多數網路把 spoofed MAC 當異常處理。

**4. 找你能直接 ARP 到的 device**

```bash
# 掃同網段的 ARP
sudo arp-scan --localnet
```

數一下家裡 / 辦公室 LAN 有多少設備。

## 自我檢核

- [ ] 知道 MAC 是 48-bit、6 byte 的硬體位址
- [ ] 講得出 Ethernet frame 結構
- [ ] 知道 MTU 是 1500、特殊網路會少
- [ ] 完整講出 ARP request → reply 流程
- [ ] 知道 ARP spoofing 的原理
- [ ] 用 tcpdump 抓過 ARP 封包

下一章看網路層 — IP / 路由 / ICMP，跨網段的傳輸。

→ [Ch 4 網路層：IP / ICMP / 路由](./04-network-layer-ip-icmp.md)
