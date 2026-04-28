# Ch 22 — bridge / veth pair

> 目標：搞懂 Linux bridge 是什麼、跟 veth pair 配合怎麼建出 Docker bridge 等價的網路。

## bridge 是什麼

**虛擬 switch** — 讓多個 interface 在 L2 互通。

跟物理 switch 一樣：

- 學 MAC ↔ port 對應
- 收到 frame 就轉發到對的 port（沒學就 broadcast）
- 同 bridge 上的設備在 L2 互通

```
 ┌─────────────────────────────┐
 │       bridge br0            │
 ├─────────────────────────────┤
 │   port 1   port 2   port 3   │
 └─────┬───────┬───────┬───────┘
       │       │       │
     veth1   veth2   eth0 (物理)
       │       │
     ns1     ns2
```

3 個 interface 接到 bridge，互相能通。

## docker bridge 的全貌

docker 預設網路 `bridge` mode：

```
 ┌──────────────────────────────────────────────────┐
 │              host (主 namespace)                 │
 │                                                  │
 │   eth0 (公網)                                    │
 │      │                                            │
 │      │ NAT (iptables MASQUERADE)                 │
 │      │                                            │
 │   docker0 (bridge, 172.17.0.1)                   │
 │      ├──── veth-X ────┐                          │
 │      │                │ namespace 1              │
 │      │              eth0 (容器 A, 172.17.0.2)    │
 │      │                                            │
 │      └──── veth-Y ────┐                          │
 │                       │ namespace 2              │
 │                     eth0 (容器 B, 172.17.0.3)    │
 │                                                  │
 └──────────────────────────────────────────────────┘
```

每個容器有獨立 netns，veth pair 一端在 netns 內、一端接 docker0 bridge。NAT 讓容器能上外網。

## 自己手動建 docker bridge 等價

### Step 1：建 bridge

```bash
sudo ip link add name br0 type bridge
sudo ip link set br0 up
sudo ip addr add 192.168.99.1/24 dev br0
```

### Step 2：建兩個 netns + veth pair

```bash
# ns1
sudo ip netns add ns1
sudo ip link add veth1-host type veth peer name veth1-ns
sudo ip link set veth1-ns netns ns1

sudo ip netns exec ns1 ip link set lo up
sudo ip netns exec ns1 ip link set veth1-ns up
sudo ip netns exec ns1 ip addr add 192.168.99.10/24 dev veth1-ns
sudo ip netns exec ns1 ip route add default via 192.168.99.1

sudo ip link set veth1-host master br0
sudo ip link set veth1-host up

# ns2 (同模式)
sudo ip netns add ns2
sudo ip link add veth2-host type veth peer name veth2-ns
sudo ip link set veth2-ns netns ns2

sudo ip netns exec ns2 ip link set lo up
sudo ip netns exec ns2 ip link set veth2-ns up
sudo ip netns exec ns2 ip addr add 192.168.99.20/24 dev veth2-ns
sudo ip netns exec ns2 ip route add default via 192.168.99.1

sudo ip link set veth2-host master br0
sudo ip link set veth2-host up
```

### Step 3：開 IP forward + NAT

```bash
sudo sysctl -w net.ipv4.ip_forward=1
sudo iptables -t nat -A POSTROUTING -s 192.168.99.0/24 -o eth0 -j MASQUERADE
```

### Step 4：驗證

```bash
# ns1 ping ns2 (透過 bridge)
sudo ip netns exec ns1 ping -c 3 192.168.99.20

# ns1 ping host (透過 bridge gateway)
sudo ip netns exec ns1 ping -c 3 192.168.99.1

# ns1 上網 (透過 NAT)
sudo ip netns exec ns1 ping -c 3 8.8.8.8
sudo ip netns exec ns1 curl -s https://example.com | head
```

成功！你建了一個跟 docker bridge 等價的網路 stack。

## 不同的 docker network mode

| Mode | 機制 |
|---|---|
| `bridge` | 上面那套 |
| `host` | container 用 host 的 netns（無隔離） |
| `none` | container 沒網路 interface |
| `container:X` | 跟 X container 共用 netns |
| `overlay` | 跨 host bridge（k8s / swarm 用） |
| `macvlan` | container 直接拿 LAN IP（沒 NAT） |

`bridge` 是預設，最常用。其他特殊場景用。

## bridge 進階：VLAN

bridge 能配 VLAN tagging：

```bash
sudo ip link add link eth0 name eth0.10 type vlan id 10
sudo ip link set eth0.10 master br0
```

「**eth0 上 VLAN 10 的 frame，送進 br0**」。企業網路常用。

## bridge 跟 STP

bridge 預設關 STP（Spanning Tree Protocol）。物理 switch 用 STP 防 loop。

虛擬 bridge 通常不需要（拓撲是設計好的，沒物理 cable loop）。

```bash
sudo ip link set dev br0 type bridge stp_state 1   # 開 STP
```

## 觀察 bridge

```bash
# 列 bridge
sudo bridge link               # 看哪些 interface 在 bridge 上
sudo bridge fdb                # 看 forwarding database (MAC ↔ port)

# 看單一 bridge
ip a show br0
sudo bridge link show master br0
```

## 一個常見誤解：「bridge 是物理 switch 模擬」

**部分對**。功能相似，但 Linux bridge 是純軟體，不需要實體 switch。

效能：高速網路下 software bridge 有 overhead（vs 硬體 switch）。但對多數場景夠快。

## 一個常見誤解：「veth pair 跟 bridge 是同一個東西」

**錯**。

- **veth pair**：兩端的「線」，連兩個 namespace
- **bridge**：「switch」，多個 interface 互通

veth pair 連到 bridge → 該 namespace 加入 bridge 的 L2 廣播域。

## 一個常見誤解：「container 互通要走 bridge」

**錯**。container 互通有多種方式：

- 同 docker bridge：透過 bridge L2
- overlay network：跨 host
- container_mode：共用 netns
- 直接 socket（跨 host 走外網）

bridge 只是其中一種。

## 動手練習

**1. 自己建 docker bridge 等價**

按本章 4 步，建 br0 + ns1 + ns2，互通 + 上網。

**完成後刪除**：

```bash
sudo ip link delete br0
sudo ip netns delete ns1
sudo ip netns delete ns2
```

**2. 觀察真 docker bridge**

```bash
docker run -d --rm --name web nginx
docker ps

ip a show docker0      # docker bridge
sudo bridge link
sudo bridge fdb show

# container 內
docker exec web ip a
docker exec web ip route
```

對比你手建的。

**3. veth + bridge 跨 namespace ping**

兩個 netns，各掛 veth 到同 bridge。互 ping。看 tcpdump on bridge：

```bash
sudo tcpdump -nn -i br0
```

看 ARP + ICMP。

**4. 看 docker network**

```bash
docker network ls
docker network inspect bridge
```

對比你的手建。

## 自我檢核

- [ ] 知道 bridge 是虛擬 switch
- [ ] veth pair vs bridge 區別
- [ ] 自己建過 docker bridge 等價的網路
- [ ] 知道 docker 6+ 種 network mode
- [ ] 用 `bridge link` / `bridge fdb` 觀察過

Part 5 結束。下個 Part 進 VPN 全解。

→ [Ch 23 VPN 全景](./23-vpn-overview.md)
