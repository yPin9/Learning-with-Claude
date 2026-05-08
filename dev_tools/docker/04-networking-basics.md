# Ch 4 — 網路基礎

> 目標：理解 Docker 各種網路模式的機制和適用場景，能建自訂 bridge network 讓容器用名稱互相通訊，搞清楚 port mapping 的精確語義。

---

## Docker 網路類型總覽

```
Host Machine
+----------------------------------------------------------+
|                                                          |
|  docker0 (bridge 172.17.0.1)    mynet (bridge 172.18.0.1)|
|  +---------------------------+  +----------------------+ |
|  | ctn A      ctn B          |  | ctn C      ctn D     | |
|  | 172.17.0.2 172.17.0.3    |  | 172.18.0.2 172.18.0.3| |
|  +---------------------------+  +----------------------+ |
|                                                          |
|  (host mode): 容器直接用 host network stack              |
|  (none): 容器只有 loopback，沒有外網                     |
+----------------------------------------------------------+
```

| 模式 | 建立方式 | 隔離程度 | 典型用途 |
|------|---------|---------|---------|
| bridge | 預設 / `--network mynet` | 中，靠 iptables NAT | 一般服務，預設選擇 |
| host | `--network host` | 無，共用 host network | 效能敏感、需要大量 port |
| none | `--network none` | 最強，無任何網路 | 純計算、安全沙盒 |
| overlay | Docker Swarm / K8s | 跨主機 | 多主機叢集（Ch 26） |
| macvlan | `--driver macvlan` | 容器有獨立 MAC/IP | 需要直接在 L2 網路出現 |

---

## Bridge 網路詳解

### 預設 bridge（docker0）

Docker 安裝時自動建立一個名為 `docker0` 的虛擬 bridge，IP 通常是 `172.17.0.1`。每個容器分配一個 `172.17.0.x` 的 IP，透過 veth pair（Virtual Ethernet pair）連到 docker0。

```bash
# 在 host 上看網路介面
ip addr show docker0
```

```
3: docker0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue state UP
    inet 172.17.0.1/24 brd 172.17.0.255 scope global docker0
```

```bash
# 跑一個容器後，看 veth
docker run -d --name test nginx:1.25-alpine
ip addr show | grep veth
```

```
5: veth3a2f1b0@if4: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 ...
```

每個容器在 host 上有一張 `vethXXXXXX` 介面，容器內的 `eth0` 就是這個 veth pair 的另一端。

### 預設 bridge 的限制

預設 docker0 bridge **不支援容器名稱 DNS 解析**。要讓容器 A ping 容器 B，只能用 IP，而 IP 每次重啟可能不同。這是一個設計缺陷，用自訂 bridge 解決。

### 自訂 Bridge（推薦做法）

```bash
docker network create mynet
```

自訂 bridge 自動提供容器間的 DNS：同一個 network 裡的容器可以用**容器名稱**互相連線。

```bash
docker network ls
```

```
NETWORK ID     NAME      DRIVER    SCOPE
b7f2a3c4d5e6   bridge    bridge    local    <- 預設 docker0
8a1b2c3d4e5f   host      host      local
3c4d5e6f7a8b   mynet     bridge    local    <- 剛建的
d9e0f1a2b3c4   none      null      local
```

---

## Host 網路模式

```bash
docker run --network host nginx:1.25-alpine
```

容器直接用 host 的 network stack，沒有 veth，沒有 NAT，沒有 port mapping。  
`-p` 在 host 模式下沒有意義——port 80 在容器裡就是 host 的 port 80。

**優點：** 效能最好，沒有 NAT 開銷，適合需要打大量 port 或對延遲敏感的服務。  
**缺點：** 隔離最差，容器佔用的 port 直接占用 host port，port 衝突是常態。Linux 專屬，macOS / Windows 上的 Docker Desktop 因為有 VM 層，host 模式行為不同。

---

## None 網路模式

```bash
docker run --network none alpine ping 8.8.8.8
```

```
ping: bad address '8.8.8.8'
```

容器只有 loopback（`lo`），無法訪問外部。適合：
- 純計算任務（不需要網路）
- 安全沙盒（確保容器不能外連）
- 搭配 `--cap-drop NET_ADMIN` 做進一步鎖定

---

## Port Mapping 精確語義

```bash
docker run -p 8080:80 nginx:1.25-alpine
```

`8080:80` = `<host port>:<container port>`。背後是 iptables DNAT 規則：

```
host:8080 -> DNAT -> 容器 IP:80
```

```bash
# 只綁 127.0.0.1，外部無法存取
docker run -p 127.0.0.1:8080:80 nginx:1.25-alpine

# 綁所有介面（預設行為）
docker run -p 0.0.0.0:8080:80 nginx:1.25-alpine

# 隨機 host port
docker run -p 80 nginx:1.25-alpine   # 系統分配，用 docker ps 看
```

**安全提醒**：預設的 `0.0.0.0` 綁定代表這個 port 對所有網路介面開放，包含外部網路。如果只需要本機存取，明確加 `127.0.0.1:`。

---

## 完整範例：兩個容器用名稱互通

```bash
# 建自訂 network
docker network create mynet

# 跑一個 nginx，加入 mynet
docker run -d --name web --network mynet nginx:1.25-alpine

# 跑一個 alpine，加入 mynet，互動模式
docker run -it --name client --network mynet alpine sh
```

在 client 容器裡：

```sh
# 用容器名稱直接 curl
curl http://web
```

```html
<!DOCTYPE html>
<html>
<head>
<title>Welcome to nginx!</title>
...
```

DNS 解析成功：`web` 這個名稱解析到 `web` 容器的 IP。這個 DNS 是 Docker 的內建 embedded DNS server（`127.0.0.11`）。

```sh
# 在容器裡確認 DNS 設定
cat /etc/resolv.conf
```

```
nameserver 127.0.0.11
options ndots:0
```

---

## 查看網路細節

```bash
docker network inspect mynet
```

```json
[{
  "Name": "mynet",
  "Driver": "bridge",
  "IPAM": {
    "Config": [{"Subnet": "172.18.0.0/16", "Gateway": "172.18.0.1"}]
  },
  "Containers": {
    "a3f1...": {
      "Name": "web",
      "IPv4Address": "172.18.0.2/16"
    },
    "b4e2...": {
      "Name": "client",
      "IPv4Address": "172.18.0.3/16"
    }
  }
}]
```

**跨 network 容器無法直接通訊**，這是 bridge network 提供的隔離。如果要讓一個容器同時在兩個 network，用 `docker network connect`：

```bash
docker network connect mynet existing-container
```

---

## 清理

```bash
docker stop web client
docker rm web client
docker network rm mynet
```

---

## 自我檢核

- [ ] 能說明 bridge / host / none 三種模式的差異
- [ ] 知道預設 docker0 和自訂 bridge 的差別（DNS 解析）
- [ ] 能建 mynet，跑兩個容器，用名稱互相 curl
- [ ] 理解 `-p 127.0.0.1:8080:80` 和 `-p 8080:80` 的安全差異
- [ ] 用 `docker network inspect` 看到容器的 IP 分配

下一章進底層：容器隔離的真實機制是 Linux namespace，用 `unshare` 手刻一個容器。

→ [Ch 5 Linux Namespace](./05-linux-namespace.md)
