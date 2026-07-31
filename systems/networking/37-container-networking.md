# Ch 37 — 容器網路

> **目標**：理解容器（Docker）網路——它怎麼建立在 Ch 20-22 的 netns/veth/bridge 之上、Docker 的網路模式（bridge/host/none/overlay）、容器怎麼上外網（NAT）、port mapping、容器間通訊、以及 Kubernetes 網路的概念。Ch 22 你手工建了「容器網路」，這章看 Docker 怎麼自動化它，並補完容器網路的全貌。這是 Part 9 進階速覽的第一站，串起前面的虛擬網路知識。

> **環境**：Linux + Docker（選裝）。概念為主，串 Ch 20-22。

## 為什麼要懂容器網路？

容器（Docker/Kubernetes）是現代部署的主流——你的服務（Ch 36）很可能跑在容器裡。但容器網路常是黑盒子：容器怎麼有自己的 IP？怎麼上外網？怎麼互相通訊？port mapping（`-p 8080:80`）做什麼？這些不懂，容器網路出問題時你束手無策。

好消息是：Ch 22 你已經**手工建了容器網路**（netns + veth + bridge + NAT）。Docker 只是把這些自動化。理解容器網路就是「看 Docker 怎麼用 Ch 20-22 的 Linux 原語」。這章把那些底層和 Docker 的抽象連起來，讓容器網路從黑盒子變透明。這也是進階速覽——快速補完現代部署的網路知識。

## 先建立直覺:Docker 自動化了你手建的拓樸

```
Docker 網路 = Ch 22 你手建的拓樸的自動化：

  你手建的（Ch 22）：
    netns（虛擬主機）+ veth（網線）+ bridge + NAT
        │
  Docker 自動做的（一模一樣！）：
    每個容器 → 一個 netns（Ch 20）
    容器的 eth0 → veth（一端在容器、一端接 docker0）（Ch 22）
    docker0 → bridge（虛擬交換器）（Ch 22）
    iptables MASQUERADE → 讓容器上外網（Ch 18/8）
        │
  → Docker 不是發明新東西，是「自動化」Linux 網路原語
    docker run 時，背後執行類似 Ch 22 的那些命令
        │
  所以理解容器網路 = 理解 Ch 20-22 + Docker 的封裝
```

關鍵心智：Docker 網路就是 **Ch 22 你手建的拓樸的自動化**——每個容器一個 netns（Ch 20）、容器的 eth0 是 veth（Ch 22，接到 docker0 bridge）、docker0 是 bridge（Ch 22）、iptables MASQUERADE 讓容器上外網（Ch 18/8）。Docker 不是發明新東西，是自動化這些 Linux 原語。

> 容器網路完全建立在 Ch 20（netns）、Ch 22（veth/bridge）、Ch 18（iptables NAT）之上。如果這些不熟，先回看——特別是 [Ch 22](./22-bridge-veth.md)（你手建了容器網路）。這章是那個手建拓樸的 Docker 版。

## Docker 的網路模式

```
Docker 的網路模式（docker run --network=...）：

  1. bridge（預設）：
     容器接到 docker0 bridge（Ch 22）
     有自己的 IP（172.17.x.x），透過 NAT 上外網
     容器間透過 bridge 互通
        │
  2. host：
     容器「共享宿主的網路」（不隔離網路 namespace）
     容器直接用宿主的 IP 和 port（沒有自己的網路 stack）
     快（無 NAT/bridge 開銷）但無隔離（port 衝突）
        │
  3. none：
     容器沒有網路（只有 lo）
     完全隔離（需要時自己配）
        │
  4. overlay（多主機）：
     跨多台主機的容器網路（Kubernetes/Swarm 用）
     用 VXLAN 等把多主機的容器連成一個虛擬網路
        │
  → bridge（預設，單機隔離）最常用
    host（高效能無隔離）、none（完全隔離）、overlay（跨主機）按需
```

```bash
# 看 Docker 的網路（對照 Ch 22）
docker network ls                  # 列出網路（bridge/host/none）
ip link show docker0               # docker0 bridge（Ch 22 的 br0！）
sudo iptables -t nat -L POSTROUTING -n | grep MASQUERADE  # Docker 的 NAT（Ch 18）

# 跑一個容器，看它的網路
docker run -d --name web nginx
docker exec web ip addr            # 容器的 eth0（172.17.x.x，是 veth）
ip link | grep veth                # host 端看到 veth（接到 docker0）

# 進容器的 netns（Ch 20 的 nsenter）
# PID=$(docker inspect -f '{{.State.Pid}}' web)
# sudo nsenter -t $PID -n ip addr  # 鑽進容器的網路看
```

> **Docker 的 bridge 模式（預設）就是 Ch 22 的拓樸，host 模式則完全共享宿主網路——理解兩者的取捨**。Docker 的網路模式：**bridge**（預設）——容器接到 `docker0` bridge（就是 Ch 22 的 br0！），有自己的 IP（172.17.x.x，是 veth），透過 NAT 上外網（Ch 18 MASQUERADE），容器間透過 bridge 互通。這提供隔離（每個容器獨立網路 stack）。**host** 模式——容器**共享宿主的網路 namespace**（不隔離），直接用宿主的 IP 和 port——快（無 NAT/bridge 開銷）但**無隔離**（容器的 port 就是宿主的 port，會衝突，且失去隔離的安全性）。**none**——容器沒網路（只有 lo，完全隔離，需要時自己配）。**overlay**——跨多主機的容器網路（Kubernetes/Swarm，用 VXLAN 把多主機的容器連成虛擬網路）。觀察：`docker0`（Ch 22 的 bridge）、`iptables nat`（Docker 的 MASQUERADE）、容器的 veth——全是 Ch 20-22 的東西。用 `nsenter`（Ch 20）能鑽進容器的 netns 看它的網路。理解這些模式，你選對的（一般用 bridge 隔離、高效能需求用 host、跨主機用 overlay），也能 debug 容器網路問題。

## port mapping 與容器上外網

```
容器怎麼上外網 + port mapping：

  容器上外網（出向）：
    容器（172.17.x.x，私有 IP）→ docker0 → MASQUERADE（NAT，Ch 8/18）
    → 用宿主 IP 出網（和 Ch 22 你建的一樣）
        │
  外網連容器（入向）—— port mapping：
    容器在內部 netns，外網連不進來（Ch 8 的 NAT 單向性）
    docker run -p 8080:80：
      把「宿主的 8080」映射到「容器的 80」
      = iptables DNAT（Ch 8 的 port forwarding！）
    → 外網連宿主:8080 → DNAT 轉給容器:80
        │
  → 容器上外網 = MASQUERADE（出）
    外網連容器 = port mapping = DNAT（入）
    這正是 Ch 8 的 NAT 雙向（MASQUERADE + port forwarding）
```

```bash
# port mapping（-p 宿主port:容器port）
docker run -d -p 8080:80 nginx
# 外網連 宿主IP:8080 → 容器的 80（nginx）

# 看 port mapping 的本質（iptables DNAT，Ch 8）
sudo iptables -t nat -L DOCKER -n
# DNAT ... tcp dpt:8080 to:172.17.0.2:80   ← 就是 Ch 8 的 port forwarding！

# 容器間通訊（同 bridge 網路）
docker network create mynet              # 建自訂網路（比預設 bridge 好，有 DNS）
docker run -d --name db --network mynet postgres
docker run -d --name app --network mynet myapp
# app 能用「db」這個名字連到 db 容器（Docker 的內建 DNS）
# docker exec app ping db                # 用容器名互通（自訂網路有 DNS 解析）
```

> **port mapping（`-p 8080:80`）就是 Ch 8 的 port forwarding（DNAT）——容器網路的入向和出向都是 Ch 8 的 NAT**。容器**上外網**（出向）靠 MASQUERADE（Ch 8/18，和 Ch 22 你建的一樣）——容器的私有 IP 透過 NAT 用宿主 IP 出網。但**外網連容器**（入向）有 Ch 8 的問題——容器在內部 netns，NAT 的單向性讓外網連不進來。**port mapping** 解決——`docker run -p 8080:80` 把「宿主的 8080」映射到「容器的 80」，這**就是 Ch 8 的 port forwarding（DNAT）**！`iptables -t nat -L DOCKER` 能看到 Docker 自動加的 DNAT 規則（`dpt:8080 to:172.17.0.2:80`）——和你手設 port forwarding 一模一樣。所以容器網路的入向（port mapping=DNAT）和出向（MASQUERADE）都是 Ch 8 的 NAT 雙向。**容器間通訊**——用**自訂網路**（`docker network create`，比預設 bridge 好）容器能用**名字**互連（`app` 連 `db`，Docker 內建 DNS 解析容器名）——這比用 IP 好（IP 會變，名字穩定）。理解這些，你就懂了 `-p` 在做什麼、容器為什麼能上網、容器間怎麼互通——全是前面學的 NAT/bridge/DNS 的應用。Docker 把它們自動化，但底層沒有魔法。

## Kubernetes 網路概念（速覽）

```
Kubernetes 網路（概念速覽，雲原生的網路）：

  K8s 的網路模型（比 Docker 複雜）：
        │
  1. Pod 網路：
     每個 Pod（一組容器）有自己的 IP
     Pod 之間能直接通（扁平網路，無 NAT）
        │
  2. Service：
     一組 Pod 的「穩定入口」（Pod 會生滅，IP 會變）
     Service 有固定的 virtual IP，負載平衡到後面的 Pod
        │
  3. Ingress：
     從外部進來的 HTTP 路由（像 nginx reverse proxy，Ch 36）
        │
  4. CNI（容器網路介面）：
     插件化的網路實作（Calico/Flannel/Cilium）
     Cilium 用 eBPF（接 bpf 課）做高效能網路
        │
  → K8s 網路是「容器網路的規模化」
    底層還是 netns/veth/路由/iptables(或eBPF)
    但加了 Service/Ingress 等抽象來管理大規模
```

> **Kubernetes 網路是「容器網路的規模化」——底層還是 netns/veth/路由，但加了 Service/Ingress 抽象來管理大規模**。K8s 網路比 Docker 複雜，但概念是容器網路的延伸：**Pod 網路**（每個 Pod 有自己的 IP，Pod 間扁平直連無 NAT——比 Docker 的 NAT 模型更直接）、**Service**（一組 Pod 的穩定入口——因為 Pod 會生滅、IP 會變，Service 提供固定的 virtual IP 並負載平衡到後面的 Pod，解決「後端動態變化」的問題）、**Ingress**（外部 HTTP 路由進來，像 nginx reverse proxy 的 K8s 版，Ch 36）、**CNI**（容器網路介面——插件化的網路實作，如 Calico/Flannel/Cilium，其中 **Cilium 用 eBPF** 做高效能網路，接 bpf 課）。這些抽象（Service/Ingress）是為了管理**大規模、動態**的容器（幾百個 Pod 不斷生滅）。但**底層還是 Ch 20-22 的東西**——netns、veth、路由、iptables（或 eBPF 取代 iptables）。理解這個，你看 K8s 網路時知道「它在 Linux 網路原語之上加了什麼抽象、為了解決什麼問題」。本課不深入 K8s（那是另一個大主題），但這個速覽讓你看到「容器網路怎麼規模化」，以及它和你學的底層的關係。重點認知：**再複雜的雲原生網路，底層都是你學過的 Linux 網路原語**——這是理解任何網路系統的鑰匙。

## 故意弄壞:debug 容器網路

```bash
# 容器網路問題的 debug（綜合 Ch 20-22 的方法）

# 1. 容器連不上外網 → 檢查 NAT/forward（Ch 22 的清單）
docker exec web ping -c1 8.8.8.8
# 不通 → 檢查：
cat /proc/sys/net/ipv4/ip_forward          # 1 嗎？
sudo iptables -t nat -L POSTROUTING -n | grep MASQUERADE  # 有 NAT 嗎？

# 2. 容器 DNS 不通（Ch 20 的容器 DNS 陷阱）
docker exec web cat /etc/resolv.conf        # DNS 設定對嗎？
docker exec web nslookup google.com         # 解析得了嗎？

# 3. port mapping 不通 → 檢查 DNAT（Ch 8）
docker run -d -p 8080:80 nginx
curl -I http://localhost:8080               # 通嗎？
sudo iptables -t nat -L DOCKER -n           # DNAT 規則在嗎？

# 4. 容器間不通 → 檢查是否同網路
docker network inspect bridge               # 看哪些容器在這網路

# 5. 鑽進容器網路看（Ch 20 nsenter）
PID=$(docker inspect -f '{{.State.Pid}}' web)
sudo nsenter -t $PID -n ip addr             # 容器的網路
sudo nsenter -t $PID -n ip route            # 容器的路由

# → 容器網路 debug = Ch 20-22 的方法 + Docker 的工具
```

> **容器網路的 debug 就是 Ch 20-22 的方法——這證明了「理解底層就能 debug 任何容器網路問題」**。容器網路出問題時，用前面學的方法和工具排查：(1) **容器連不上外網** → 檢查 ip_forward 和 MASQUERADE（Ch 22 的清單，和你手建拓樸時一樣）；(2) **容器 DNS 不通** → 檢查容器的 resolv.conf（Ch 20 的容器 DNS 陷阱——這是容器網路最常見的問題之一）；(3) **port mapping 不通** → 檢查 Docker 的 DNAT 規則（Ch 8）；(4) **容器間不通** → 檢查是否同網路；(5) **深入** → 用 `nsenter`（Ch 20）鑽進容器的 netns 看它的介面/路由/連線。`docker network inspect` 看網路詳情。這些 debug 方法**完全是 Ch 20-22 的延伸**——因為容器網路就是那些 Linux 原語。這呼應了本課的核心理念：**理解底層讓你能 debug 任何上層抽象**。當別人面對「容器連不上」束手無策（把 Docker 當黑盒子），你知道去查 ip_forward、NAT、resolv.conf、路由——因為你知道容器網路是什麼。這是「理解 vs 會用」的根本差別。Part 9 的容器網路速覽到此——你看到了現代部署的網路底層，以及它和全課知識的連結。

## 動手練習

1. 對照手建：跑一個 Docker 容器，找出 docker0（bridge）、容器的 veth、NAT 規則，對照 Ch 22

2. 網路模式：用 `--network host` vs 預設 bridge 跑容器，對比它們的網路（host 共享宿主）

3. port mapping：`docker run -p 8080:80`，用 iptables 看 DNAT 規則（Ch 8）

4. 容器間 DNS：建自訂網路，跑兩個容器，用名字互連（Docker 內建 DNS）

5. 跑「故意弄壞」：用 nsenter 鑽進容器網路看，debug 一個容器網路問題

## 本章重點整理

- Docker 網路 = Ch 22 手建拓樸的自動化：容器=netns、eth0=veth、docker0=bridge、MASQUERADE=NAT
- 網路模式：bridge（預設，隔離）、host（共享宿主網路，快但無隔離）、none（無網路）、overlay（跨主機）
- 容器上外網=MASQUERADE（出）；port mapping（-p 8080:80）=Ch 8 的 DNAT/port forwarding（入）
- K8s 網路是容器網路的規模化（Pod 網路/Service/Ingress/CNI），底層還是 netns/veth/路由（或 eBPF）
- 容器網路 debug = Ch 20-22 的方法（ip_forward/NAT/resolv.conf/nsenter）——理解底層就能 debug 任何抽象

## 自我檢核

- [ ] 能說出 Docker 網路怎麼對應 Ch 20-22 的 netns/veth/bridge/NAT
- [ ] 知道 Docker 網路模式（bridge/host/none/overlay）的差別
- [ ] 理解 port mapping 就是 Ch 8 的 DNAT
- [ ] 知道 K8s 網路的概念（Pod/Service/Ingress）和它和底層的關係
- [ ] 能用 Ch 20-22 的方法 debug 容器網路問題

## 延伸閱讀

### 文章

- **[Docker networking 詳解](https://docs.docker.com/network/)** — Docker 官方
  - **讀哪裡**：bridge/host/overlay 那幾節
  - **為什麼值得讀**：Docker 網路模式的權威

- **[Container Networking from Scratch](https://labs.iximiuz.com/tutorials/container-networking-from-scratch)** — iximiuz
  - **這篇說什麼**：手工建容器網路，對照 Docker
  - **為什麼值得讀**：把 Ch 22 和 Docker 的連結講透（本章的延伸）

### 書籍

- **《Container Networking》— Michael Hausenblas（O'Reilly, 免費）**
  - **讀哪幾章**：單機網路、K8s 網路那幾章
  - **這本書的定位**：容器網路的完整指南，從 Docker 到 K8s

下一章看 IPv6——位址耗盡的長期解法，理解它怎麼解決 NAT 的問題、為什麼普及緩慢、以及它和 IPv4 的差異。

→ [Ch 38 IPv6](./38-ipv6.md)
