# Ch 13 — ip / ss / route

> 目標：學會現代 Linux 網路命令的核心三件套：`ip`（看狀態 + 改設定）、`ss`（socket）、`route`（路由表）。

## 為什麼要會這些

過去用 `ifconfig` / `netstat` / `route`。現在被 `ip` / `ss` 取代（更快、更現代）。

很多教學 / 文章還用舊命令，但**新系統 default 沒裝舊工具**。會新版才能在所有 Linux 通用。

## ip 命令

`ip` 是 iproute2 套件的核心命令。功能涵蓋：interface / address / route / link / tunnel ...

### ip address

```bash
# 看所有 interface 的 IP
ip a               # 同 ip addr show
ip -4 a            # 只看 IPv4
ip -6 a            # 只看 IPv6
ip a show eth0     # 特定 interface

# 加 IP（暫時，重開機消失）
sudo ip addr add 192.168.1.100/24 dev eth0

# 刪 IP
sudo ip addr del 192.168.1.100/24 dev eth0
```

輸出範例：

```
2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP group default qlen 1000
    link/ether aa:bb:cc:dd:ee:ff brd ff:ff:ff:ff:ff:ff
    inet 192.168.1.10/24 brd 192.168.1.255 scope global dynamic eth0
       valid_lft 86392sec preferred_lft 86392sec
    inet6 fe80::abcd:1234:5678:9012/64 scope link
       valid_lft forever preferred_lft forever
```

讀法：
- `2:` interface index
- `eth0:` 名字
- `<UP, LOWER_UP>` flags（連線狀態）
- `mtu 1500`
- `link/ether ...` MAC
- `inet 192.168.1.10/24` IPv4
- `inet6 fe80::...` IPv6 link-local

### ip link

```bash
# 看所有 link
ip link

# Up / down 一個 interface
sudo ip link set eth0 up
sudo ip link set eth0 down

# 改 MAC
sudo ip link set dev eth0 address aa:11:22:33:44:55

# 改 MTU
sudo ip link set dev eth0 mtu 9000
```

### ip route

```bash
# 看路由表
ip route               # 同 ip r
ip route show table all   # 所有 table（含特殊）

# 看某 IP 怎麼走
ip route get 8.8.8.8

# 加路由
sudo ip route add 10.0.0.0/24 via 192.168.1.1 dev eth0

# 改 default route
sudo ip route del default
sudo ip route add default via 192.168.1.1
```

輸出範例：

```
default via 192.168.1.1 dev eth0 proto dhcp metric 100
192.168.1.0/24 dev eth0 proto kernel scope link src 192.168.1.10 metric 100
10.0.0.0/8 via 10.10.10.1 dev tun0
```

### ip neigh（ARP table）

```bash
ip neigh                          # 看 ARP / NDP cache
sudo ip neigh flush all           # 清 cache
sudo ip neigh add 192.168.1.50 lladdr aa:bb:cc:dd:ee:ff dev eth0  # 手動加
```

### ip netns（network namespaces，Ch 20）

```bash
sudo ip netns add ns1
sudo ip netns list
sudo ip netns exec ns1 ip a
```

### ip rule（policy routing，進階）

「**根據 source IP / mark 用不同路由表**」。VPN / multi-homed 用。

```bash
ip rule list
sudo ip rule add from 192.168.1.10 table 100
```

99% 場景用不到，知道有就好。

## ss（Socket Statistics）

取代 `netstat`。看 kernel socket。Ch 9 of observability_tools 詳細，這裡網路角度補充。

### 基本

```bash
ss                # 全部 socket
ss -t             # TCP
ss -u             # UDP
ss -x             # Unix
ss -l             # listen
ss -a             # all states
ss -n             # 不解析 DNS / port name
ss -p             # 顯示 PID（需 sudo）
ss -e             # 詳細（含 inode, uid）
ss -i             # TCP info（cwnd, rtt）
ss -m             # memory 用量
ss -s             # 總結
```

最常用：

```bash
sudo ss -tnlp           # listening TCP + PID
sudo ss -tnp            # active TCP + PID
sudo ss -tnpi           # 加 TCP info
ss state established
ss state time-wait | wc -l
```

### TCP 內部 info

```bash
sudo ss -tnpi
# ESTAB 0 0 192.168.1.10:54321 93.184.216.34:443 ...
#  cubic wscale:7,7 rto:204 rtt:0.5/0.25 mss:1448 cwnd:10
```

| 欄位 | 意義 |
|---|---|
| cubic | congestion algo |
| wscale:7,7 | 視窗縮放 factor |
| rto | retransmission timeout (ms) |
| rtt | round trip time / mdev |
| mss | max segment size |
| cwnd | congestion window |
| retrans | 重傳次數 |

`retrans` 高 = 網路爛 / packet drop。

## route（傳統，已被 ip route 取代）

```bash
route -n           # 看路由（純數字）
sudo route add -net 10.0.0.0/24 gw 192.168.1.1
```

**新系統建議用 `ip route`**，但老 doc / script 還寫 `route`。

## ifconfig（傳統，已被 ip addr 取代）

```bash
ifconfig            # 所有 interface
ifconfig eth0
sudo ifconfig eth0 up
sudo ifconfig eth0 192.168.1.100/24
```

新系統不裝 ifconfig（要 `apt install net-tools`）。**用 `ip` 替代**。

## netstat（傳統，已被 ss 取代）

```bash
netstat -tnlp           # listening TCP
netstat -an
netstat -r              # 路由表
```

**新系統不裝**，用 `ss` 替代。

## 一個常見場景：找誰開著某 port

```bash
# 找開 80 port 的程式
sudo ss -tnlp | grep ':80'
# LISTEN 0 511 *:80 *:* users:(("nginx",pid=1234,fd=6))
```

## 一個常見場景：看自己的網路全貌

```bash
# IP / MAC
ip a

# 路由
ip route

# 開放的 port
sudo ss -tnlp

# ARP
ip neigh
```

## 一個常見踩雷：「ip addr add」加完重開機消失

`ip` 命令是**runtime 變更**，不持久。重開機後消失。

要持久要修 distro 的網路配置：

- Ubuntu (Netplan): `/etc/netplan/*.yaml`
- Debian: `/etc/network/interfaces`
- Fedora / RHEL: `/etc/sysconfig/network-scripts/ifcfg-eth0` 或 NetworkManager
- 通用：systemd-networkd

## 一個常見踩雷：「`ifconfig eth0` 看到 IP 但 ping 不通」

可能：

- firewall 擋（Ch 18 iptables）
- 路由錯（沒 default route）
- ARP fail
- 對方不應

依序 check：

```bash
ip route                         # 有 default？
ip neigh                         # ARP 有嗎？
sudo iptables -L                 # firewall 規則
ping -c 3 192.168.1.1            # 先 ping local
```

## 動手練習

**1. 看你的網路全貌**

```bash
ip a
ip route
sudo ss -tnlp
ip neigh
```

每個輸出解釋每行意義。

**2. 加一個臨時 IP**

```bash
sudo ip addr add 192.168.1.200/24 dev eth0
ip a    # 多一個 IP

# 用 alias IP ping / connect
ping -I 192.168.1.200 192.168.1.1

# 移除
sudo ip addr del 192.168.1.200/24 dev eth0
```

**3. 改路由**

```bash
ip route get 8.8.8.8
# default via 192.168.1.1 dev wlan0

# 刪 default 看會怎樣（先記錄原值！）
ORIG=$(ip route | grep default)
sudo ip route del default
ping -c 1 8.8.8.8        # 失敗
sudo ip route add $ORIG  # 復原
```

**4. ss vs netstat**

```bash
sudo ss -tnlp
sudo netstat -tnlp 2>/dev/null    # 如果裝了
```

對比輸出。ss 通常快很多。

**5. 看 TCP 內部 info**

```bash
# 開個連線
curl https://example.com &

sudo ss -tnpi
```

看 cwnd / rtt / retrans 等內部狀態。

## 自我檢核

- [ ] `ip a` / `ip link` / `ip route` / `ip neigh` 各自用途清楚
- [ ] 知道 ip / ss 取代 ifconfig / netstat
- [ ] 用 ss 找出開某 port 的程式
- [ ] ip 命令是 runtime 變更，不持久
- [ ] 看 ss -tnpi 的 TCP 內部 info

下一章看 tcpdump / Wireshark — 從 packet 角度看網路。

→ [Ch 14 tcpdump / Wireshark](./14-tcpdump-wireshark.md)
