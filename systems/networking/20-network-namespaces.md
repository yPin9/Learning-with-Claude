# Ch 20 — network namespaces

> 目標：搞懂 Linux network namespace 是什麼、怎麼建、是容器網路 / VPN 的核心。

## namespace 是什麼

Linux 的「**虛擬資源隔離**」機制。每個 namespace 內看到的資源是獨立的：

| Namespace | 隔離 |
|---|---|
| **net** | 網路（interface, 路由, firewall） |
| pid | process ID |
| mnt | mount point |
| uts | hostname |
| ipc | IPC |
| user | user ID |
| cgroup | cgroup |
| time | system clock |

容器（docker, podman, ...）= 一堆 namespace + cgroup 組合而成。

本章專注 **network namespace**（簡稱 `netns`）。

## network namespace

**每個 netns 有自己獨立的**：

- network interface（除了 `lo`，除非自建）
- 路由表
- iptables / nftables 規則
- TCP / UDP socket
- ARP table
- ...

兩個 netns 之間**完全隔離** — 一個的 ping 看不到另一個。

要互通 → 用 `veth pair` 或 bridge（Ch 22）連起來。

## 建立 / 操作 netns

```bash
# 建
sudo ip netns add ns1

# 列
sudo ip netns list
# ns1

# 在 ns1 內跑命令
sudo ip netns exec ns1 ip a       # 看 ns1 的 interface
sudo ip netns exec ns1 ip route   # 看 ns1 的路由
sudo ip netns exec ns1 ping 8.8.8.8   # 從 ns1 ping（會失敗，沒 interface）

# 進 shell（在 ns1 內）
sudo ip netns exec ns1 bash
# 在這 shell 裡，所有命令都在 ns1 內

# 刪
sudo ip netns delete ns1
```

新建的 netns 只有 `lo` interface（且 down）。

## 把 interface 移進 netns

物理 / 虛擬網卡都能移：

```bash
# 看現有
ip link

# 把 eth1 移進 ns1
sudo ip link set eth1 netns ns1

# 確認
ip link    # 主 namespace 看不到 eth1 了
sudo ip netns exec ns1 ip link   # ns1 內看得到

# 把它 up + 設 IP
sudo ip netns exec ns1 ip link set eth1 up
sudo ip netns exec ns1 ip addr add 192.168.10.1/24 dev eth1
```

**移進 netns 的 interface，主 namespace 完全看不到**。

## veth pair（virtual ethernet pair）

兩端虛擬「網線」 — 一端在一個 netns、另一端在另一個 netns，互通：

```bash
# 建 veth pair
sudo ip link add veth0 type veth peer name veth1

# 看
ip link
# veth0@veth1: ...
# veth1@veth0: ...

# 把 veth1 移到 ns1
sudo ip link set veth1 netns ns1

# 主 namespace 設 veth0 IP
sudo ip addr add 10.0.0.1/24 dev veth0
sudo ip link set veth0 up

# ns1 設 veth1 IP
sudo ip netns exec ns1 ip addr add 10.0.0.2/24 dev veth1
sudo ip netns exec ns1 ip link set veth1 up
sudo ip netns exec ns1 ip link set lo up

# 互通測試
ping -c 1 10.0.0.2                       # 主 → ns1
sudo ip netns exec ns1 ping -c 1 10.0.0.1   # ns1 → 主
```

成功！兩個 netns 互通了。

**這就是 docker 容器跟 host 互通的核心機制**（多加一個 bridge，Ch 22）。

## 讓 ns1 上網

ns1 預設沒 default route，沒辦法上網。需要：

1. NAT 主 namespace 的 packet（已經有 internet 連線）
2. ns1 設 default route 指向 veth0

```bash
# 主 namespace 開 IP forward
sudo sysctl -w net.ipv4.ip_forward=1

# 加 NAT（讓 ns1 出去的 packet 用主機 IP）
sudo iptables -t nat -A POSTROUTING -s 10.0.0.0/24 -o eth0 -j MASQUERADE

# ns1 設 default route
sudo ip netns exec ns1 ip route add default via 10.0.0.1

# 測試
sudo ip netns exec ns1 ping -c 3 8.8.8.8
sudo ip netns exec ns1 curl -s https://example.com | head
```

ns1 變成「**獨立網路 namespace 但能上網**」 — 跟 docker / VM 的網路模型一樣。

## 實用：用 netns 隔離 VPN

把 VPN 跑在獨立 netns，**只有指定程式走 VPN**，其他走主網路：

```bash
# 建 netns
sudo ip netns add vpn
sudo ip netns exec vpn ip link set lo up

# (在 netns 內配 VPN，例如 WireGuard)
sudo ip netns exec vpn wg-quick up wg0

# 在 netns 內跑特定程式
sudo ip netns exec vpn firefox    # 這個 Firefox 走 VPN
firefox &                          # 主 namespace Firefox 走原網路
```

進階用法：torrent / 翻牆 / 隔離 traffic 都這套。

## 常見場景：debug docker 網路

docker container 跑 在自己的 netns。要 debug：

```bash
# 找 container 的 PID
docker inspect <container> | grep Pid
# "Pid": 12345

# 進它的 netns
sudo nsenter -t 12345 -n bash

# 現在你的 shell 在 container 的 net namespace
ip a
ip route
ss -tnlp
```

或：

```bash
# 用 docker network ns 名字
sudo ip netns list   # docker 的 netns 通常 mounted 不同地方
sudo ls /var/run/docker/netns/
```

不同 docker 版本路徑不同，**`nsenter -t PID -n`** 通用。

## 一個常見誤解：「netns 完全 sandbox」

**部分對**。netns 隔離網路資源，但**不隔離**：

- file system（要加 mnt namespace）
- process（要加 pid namespace）
- 用戶 ID（要加 user namespace）

完整 container = 多個 namespace 組合。

## 一個常見誤解：「netns 是 docker 才有的」

**錯**。netns 是 **Linux kernel 原生功能**，docker 是其中一個應用。

你能用 `ip netns` 命令直接玩 netns，不需要 docker。

## 一個常見誤解：「netns 內的 process 在主 namespace 看不到」

**部分對**。`ip netns exec` 跑的 process 在 netns 內看不到主 namespace 的 interface，但**主 namespace 的 ps -ef 看得到這個 process**（PID 共享，除非加 pid namespace）。

```bash
# Terminal A：跑 process 在 netns
sudo ip netns exec ns1 sleep 100

# Terminal B：看
ps aux | grep sleep
# root  12345  sleep 100   ← 看得到！
```

## 動手練習

**1. 建一個 netns**

```bash
sudo ip netns add lab
sudo ip netns exec lab ip a   # 只有 lo（down）
sudo ip netns exec lab ip link set lo up
sudo ip netns exec lab ping 127.0.0.1   # OK
sudo ip netns exec lab ping 8.8.8.8     # 失敗（沒 interface）
```

**2. veth pair 連兩 netns**

按本章流程，建 ns1 + ns2，veth pair 連起來，互 ping。

**3. 讓 ns1 上網**

按本章 NAT 流程，讓 ns1 能 ping 8.8.8.8 / curl example.com。

**4. 看 docker 的 netns**

```bash
docker run -d --rm nginx
PID=$(docker inspect $(docker ps -q | head -1) | grep '"Pid"' | grep -oP '\d+')
sudo nsenter -t $PID -n ip a
sudo nsenter -t $PID -n ss -tnlp
```

看 nginx container 內的網路設定。

**5. 隔離 VPN 用 netns**

如果你已經會配 WireGuard（Ch 24）：

```bash
sudo ip netns add vpn
sudo ip netns exec vpn ip link set lo up
# 在 vpn netns 內跑 wg-quick
# 在 vpn netns 內 curl 看出口 IP
sudo ip netns exec vpn curl ifconfig.me   # VPN exit IP
curl ifconfig.me                           # 你的真實 IP
```

兩個不同！

## 自我檢核

- [ ] 知道 namespace 是什麼，種類有哪些
- [ ] 用 `ip netns` 命令操作過 netns
- [ ] 用 veth pair 連過兩個 netns
- [ ] 用 NAT 讓 netns 上網
- [ ] 用 nsenter 進過 docker container 的 netns
- [ ] 知道 netns 是 docker / k8s 網路的根基

下一章看 tun/tap interface — VPN 的核心元件。

→ [Ch 21 tun/tap interface](./21-tun-tap.md)
