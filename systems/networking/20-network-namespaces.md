# Ch 20 — network namespace

> **目標**：把 network namespace（netns）講透——它是什麼（Linux 把網路 stack 隔離成多份的機制）、底層怎麼運作、怎麼用它建虛擬主機、它和容器（Docker）隔離的關係。Ch 0 玩過 netns，這章深入它的原理和進階用法。netns 是容器網路的底層（Ch 37）、VPN 測試的工具、本課所有網路實驗的基礎——理解它，你就懂了「容器網路隔離」到底是什麼。

> **環境**：Linux（ip netns）。需 root。

## 為什麼 netns 是現代網路的核心？

容器（Docker/Kubernetes）的網路隔離——每個容器有自己的 IP、自己的網路介面、互不干擾——這是怎麼做到的？答案是 **network namespace**。它是 Linux kernel 的機制，把「網路 stack」（介面、路由表、防火牆規則、socket）隔離成多份，每份像一台獨立的虛擬主機。

理解 netns 回答了核心問題：容器網路隔離的底層是什麼？怎麼在一台機器上建出多台虛擬主機做實驗？VPN 怎麼測試兩端？這章把 Ch 0 玩過的 netns 深入到原理層，讓你不只「會用」還「懂它是什麼」。它是 Part 5（Linux 網路機制）的核心，連接 Ch 22（用 veth 連 netns）和 Ch 37（容器網路）。

## 先建立直覺:每個 netns 是一台虛擬主機

```
network namespace = 把網路 stack 隔離成多份

  正常：一台 Linux 有「一套」網路 stack
    [介面列表][路由表][防火牆規則][socket/port][ARP表][conntrack]
        │
  netns：把這套「複製」成多個獨立的命名空間
    netns A: [自己的介面][自己的路由][自己的防火牆][自己的port]
    netns B: [自己的介面][自己的路由][自己的防火牆][自己的port]
    host:    [原本的一套]
        │
  每個 netns 完全隔離：
    - A 的介面 B 看不到
    - A 和 B 都能監聽 port 80（不衝突，各自獨立）
    - A 的路由/防火牆改動不影響 B 或 host
        │
  → 每個 netns 像一台獨立的虛擬主機
    這就是容器網路隔離的底層（Docker 每個容器一個 netns）
```

關鍵心智：netns 把 Linux 的「網路 stack」（介面、路由表、防火牆、socket/port、ARP、conntrack）隔離成多份，每份完全獨立——像一台獨立的虛擬主機。兩個 netns 都能用 port 80（不衝突）、互相看不到對方的介面、改動互不影響。這就是 Docker 容器網路隔離的底層。

> Ch 0 玩過 netns 的基本操作（add/exec/del）。本章深入原理和進階用法。netns 隔離的就是前面學的東西——介面（Ch 3）、路由（Ch 4）、防火牆（Ch 18）、socket（Ch 13）。如果對這些不熟，先回看對應章節。

## netns 的底層:隔離了什麼

```bash
# netns 隔離的是「整個網路 stack」，逐項驗證
sudo ip netns add demo

# 1. 介面隔離（demo 只有 lo，看不到 host 的 eth0）
sudo ip netns exec demo ip link
# 只有 lo（host 的 eth0/wlan0 它都看不到）

# 2. 路由表隔離（demo 有自己的空路由表）
sudo ip netns exec demo ip route
# （空的，和 host 的路由表完全分開）

# 3. 防火牆隔離（demo 有自己的 iptables/nftables 規則）
sudo ip netns exec demo iptables -L
# （空的，獨立於 host）

# 4. socket/port 隔離（demo 能用 host 已佔用的 port）
sudo ip netns exec demo ip link set lo up
# demo 裡能監聽 port 80，即使 host 的 80 被佔用（各自獨立）

# 5. conntrack 隔離（連線追蹤也是獨立的，Ch 8）

sudo ip netns del demo
```

```
netns 的實作（kernel 層）：

  每個 process 有一個「network namespace 指標」
    指向它所屬的 netns（那套網路 stack）
        │
  ip netns add demo：建一個新的 netns（一套全新的空網路 stack）
  ip netns exec demo <cmd>：
    在那個 netns 裡執行 cmd（cmd 的 process 指向 demo netns）
    → cmd 看到的是 demo 的介面/路由/防火牆，不是 host 的
        │
  netns 存在 /var/run/netns/ （ip netns 建立的）
  也可以是「匿名的」（容器用的，綁在 process 上，process 死就沒了）
        │
  → netns 是「per-process 的網路 stack 視圖」
    換 netns = 換一套網路環境
```

> **netns 隔離的是「整個網路 stack」——介面、路由、防火牆、port、conntrack 全部獨立——這就是它能當「虛擬主機」的原因**。每個 process 在 kernel 裡有個「network namespace 指標」，指向它所屬的網路 stack。`ip netns exec demo <cmd>` 讓 cmd 的 process 指向 demo 這個 netns——於是 cmd 看到的介面、路由、防火牆都是 demo 的（不是 host 的）。隔離是**全面的**：兩個 netns 能各自監聽 port 80（socket 隔離）、有各自的路由表和防火牆規則、各自的 ARP 和 conntrack（Ch 8）。`ip netns` 建的 netns 是「命名的」（存在 `/var/run/netns/`，可持久），而**容器用的是「匿名的」**（綁在容器的 process 上，process 死 netns 就消失）——這是 Docker 容器「刪掉就乾淨」的原因之一。理解 netns 是「per-process 的網路 stack 視圖」，你就懂了容器網路隔離的本質——它不是虛擬機（沒有獨立 kernel），而是**共享一個 kernel 但有獨立的網路 stack 視圖**（輕量、快速）。這是容器比虛擬機輕的關鍵（Ch 32 會對比）。

## 進入 netns 的多種方式

```bash
# 方式 1：ip netns exec（最常用）
sudo ip netns add ns1
sudo ip netns exec ns1 ip addr        # 在 ns1 裡執行命令
sudo ip netns exec ns1 bash           # 開一個在 ns1 裡的 shell（互動）

# 方式 2：nsenter（進入「某 process」的 netns，debug 容器常用）
# 找一個容器的 PID，進入它的 netns
# sudo nsenter -t <PID> -n ip addr    # -n = network namespace
#   這是 debug Docker 容器網路的關鍵技巧！

# 方式 3：看一個 process 在哪個 netns
ls -l /proc/<PID>/ns/net              # 指向的 netns（inode 號相同 = 同一個 netns）

# 列出所有命名的 netns
ip netns list

# 看 netns 的 process（哪些 process 在這個 netns）
sudo ip netns pids ns1

sudo ip netns del ns1
```

> **`nsenter -t <PID> -n` 進入某個 process 的 netns——這是 debug Docker 容器網路的關鍵技巧**。`ip netns exec` 只能進入「命名的」netns（`ip netns` 建的），但**容器用的是匿名 netns**（綁在容器 process 上）——所以你不能用 `ip netns exec` 進容器的網路。解法是 `nsenter -t <容器PID> -n <命令>`——它進入「那個 process 所在的 netns」執行命令。這讓你能「鑽進容器的網路環境」看它的介面、路由、連線（`nsenter -t <PID> -n ss -tlnp` 看容器在聽什麼）——這是 debug 容器網路問題（容器連不上、DNS 不通）的核心技巧（Ch 37）。`ls -l /proc/<PID>/ns/net` 看一個 process 屬於哪個 netns（inode 號相同 = 同一個 netns，所以能判斷哪些 process 共享網路）。理解這些「進入 netns」的方式，你就有了在虛擬網路和容器之間穿梭、debug 的能力。Docker 其實底層就是 `ip netns` + `veth`（Ch 22）+ `iptables`（Ch 18）的組合——理解 netns，Docker 網路就不神秘了。

## netns 之間怎麼通訊（預告 Ch 22）

```
單獨的 netns 是「孤島」—— 怎麼讓它們互通？

  剛建的 netns 只有 lo（loopback），和外界完全隔離
  要讓它通訊，需要「虛擬網線」把它連到別處
        │
  veth pair（虛擬乙太網對，Ch 22）：
    一對相連的虛擬介面，像一條網線的兩端
    一端放 netns A、一端放 host（或 netns B）
    → A 透過這條「虛擬網線」和對方通訊
        │
  完整的虛擬網路（Ch 22 會建）：
    多個 netns（虛擬主機）
    + veth（虛擬網線）連接
    + bridge（虛擬交換器）匯集
    + iptables MASQUERADE（Ch 18）讓它們上外網
    = 在一台機器上建出完整的網路拓樸
        │
  → 這正是 Docker 做的：
    每個容器一個 netns + veth 連到 docker0 bridge + NAT 出網
```

```bash
# 預覽：用 veth 連 netns 和 host（Ch 22 詳述）
sudo ip netns add ns1
# 建一對 veth（虛擬網線的兩端）
sudo ip link add veth0 type veth peer name veth1
# 一端放進 ns1
sudo ip link set veth1 netns ns1
# 設定 IP
sudo ip addr add 10.0.0.1/24 dev veth0
sudo ip link set veth0 up
sudo ip netns exec ns1 ip addr add 10.0.0.2/24 dev veth1
sudo ip netns exec ns1 ip link set veth1 up
sudo ip netns exec ns1 ip link set lo up
# 測試連通（host ↔ ns1）
ping -c2 10.0.0.2                          # host 連 ns1
sudo ip netns exec ns1 ping -c2 10.0.0.1   # ns1 連 host
# 清理
sudo ip netns del ns1
```

> **單獨的 netns 是「網路孤島」，要用 veth（虛擬網線）連起來才能通訊——這是 Ch 22 的主題，也是 Docker 網路的核心**。剛建的 netns 只有 lo（和外界完全隔離），要讓它通訊需要「虛擬網線」。**veth pair**（虛擬乙太網對，Ch 22）是一對相連的虛擬介面——像一條網線的兩端，封包從一端進、另一端出。把 veth 一端放進 netns、一端留在 host（或另一個 netns），netns 就能透過這條「虛擬網線」和外界通訊。完整的虛擬網路（Ch 22 會建）是：多個 netns（虛擬主機）+ veth（虛擬網線）+ bridge（虛擬交換器，匯集多個 veth）+ iptables MASQUERADE（Ch 18，讓它們上外網）。**這正是 Docker 做的**——每個容器一個 netns、用 veth 連到 `docker0` bridge、透過 NAT 出網（Ch 37）。所以理解 netns + veth + bridge + iptables 這四件事，你就理解了 Docker 網路的全部底層。上面的預覽展示了「veth 連 host 和 netns」的最小例子，Ch 22 會建更完整的拓樸（多 netns + bridge + NAT，模擬真實網路）。

## 故意弄壞:netns 的隔離與陷阱

```bash
# 驗證隔離性 + 常見陷阱
sudo ip netns add isolated

# 陷阱 1：新 netns 的 lo 是 down 的（忘了 up 連 loopback 都不通）
sudo ip netns exec isolated ping -c1 127.0.0.1
# connect: Network is unreachable —— lo 沒 up！
sudo ip netns exec isolated ip link set lo up
sudo ip netns exec isolated ping -c1 127.0.0.1   # 現在通了
#   → 教訓：新 netns 記得先 ip link set lo up

# 陷阱 2：netns 裡沒有 DNS 設定（/etc/resolv.conf 可能不同）
# netns 用的是 /etc/netns/<name>/resolv.conf（如果有）或 host 的
# → 容器 DNS 問題常源於此（Ch 37）

# 陷阱 3：忘記 netns 裡沒有預設路由（出不了網）
sudo ip netns exec isolated ip route   # 空的 → 連 veth 連到的網段外都去不了
# → 要 ip netns exec isolated ip route add default via <閘道>

sudo ip netns del isolated

# 隔離驗證：netns 裡的危險操作不影響 host
sudo ip netns add danger
sudo ip netns exec danger iptables -P INPUT DROP   # 在 netns 裡封鎖一切
# host 的 iptables 完全不受影響（隔離）
sudo iptables -L INPUT -n | head -1                # host 的 policy 沒變
sudo ip netns del danger
```

> **新 netns 的三個常見陷阱（lo 沒 up、沒 DNS、沒預設路由）正是容器網路問題的根源**。新建的 netns 是「乾淨的空白網路」——它有三個東西要你補上：(1) **lo 預設是 down 的**——連 `ping 127.0.0.1` 都不通（"Network is unreachable"），要 `ip link set lo up`（這是新手建 netns 第一個踩的坑）；(2) **沒有 DNS 設定**——netns 用 `/etc/netns/<name>/resolv.conf`（如果有）否則可能沒有，造成「IP 能通但域名解析不了」（這正是**容器 DNS 問題**的根源，Ch 37）；(3) **沒有預設路由**——只能到 veth 直連的網段，出不了更遠（要 `ip route add default via <閘道>`）。這三個正是 Docker 容器網路問題的常見來源——「容器連不上外網」往往是缺預設路由或 NAT、「容器 DNS 不通」是 resolv.conf 問題。好消息是 `ip netns` 和 Docker 會幫你處理大部分，但理解這些底層，你才能 debug 當它們出錯時。隔離性的好處：netns 裡的危險操作（封鎖防火牆、改路由、弄壞網路）**完全不影響 host**——這是為什麼 netns 是學網路的安全沙盒（弄壞了 `ip netns del` 刪掉重來）。這也是容器安全隔離的一部分（容器搞壞自己的網路不影響主機）。

## 動手練習

1. 驗證隔離：建 netns，逐項驗證介面/路由/防火牆/port 都和 host 隔離

2. 進入 netns：用 `ip netns exec` 開 shell、用 `ls /proc/<PID>/ns/net` 看 process 的 netns

3. veth 連接：跟著「預覽」建 veth 連 host 和 netns，測試雙向 ping（為 Ch 22 暖身）

4. nsenter（有 Docker 的話）：找一個容器的 PID，用 `nsenter -t <PID> -n ip addr` 進入它的網路看

5. 跑「故意弄壞」：體驗 lo 沒 up、沒預設路由的問題，理解容器網路問題的根源

## 本章重點整理

- netns 把整個網路 stack（介面/路由/防火牆/port/conntrack）隔離成多份，每份像獨立虛擬主機——容器網路隔離的底層
- 隔離全面：兩個 netns 能各用 port 80、互看不到介面、改動互不影響；共享 kernel 但獨立網路視圖（比 VM 輕）
- 進入 netns：`ip netns exec`（命名的）、`nsenter -t <PID> -n`（進 process 的 netns，debug 容器的關鍵）
- 單獨 netns 是孤島，要 veth（虛擬網線，Ch 22）連起來通訊；Docker = netns + veth + bridge + iptables
- 新 netns 三陷阱（lo 沒 up、沒 DNS、沒預設路由）= 容器網路問題的根源；隔離讓危險操作不影響 host

## 自我檢核

- [ ] 能解釋 netns 隔離了什麼，為什麼像「虛擬主機」
- [ ] 知道 netns 和容器隔離的關係，以及為什麼比 VM 輕
- [ ] 會用 ip netns exec 和 nsenter 進入 netns
- [ ] 理解單獨 netns 是孤島，要 veth 連接（預告 Ch 22）
- [ ] 知道新 netns 的常見陷阱，以及它們和容器網路問題的關係

## 延伸閱讀

### 文章

- **[Introducing Linux Network Namespaces](https://blog.scottlowe.org/2013/09/04/introducing-linux-network-namespaces/)** — Scott Lowe
  - **這篇說什麼**：用 veth + netns 從零建虛擬網路，每步有圖
  - **讀哪裡**：整篇，跟著做
  - **為什麼值得讀**：netns 操作的最佳實作教學，連到 Ch 22

- **[Container Networking from Scratch](https://www.youtube.com/watch?v=6v_BDHIgOY8)** — Liz Rice（演講）
  - **這篇說什麼**：現場用 netns/veth/bridge 從零建出容器網路
  - **為什麼值得讀**：把 netns→容器網路的連結講得最清楚（連 Ch 37）

### 官方文件

- **[network_namespaces(7)](https://man7.org/linux/man-pages/man7/network_namespaces.7.html)** — Linux man-pages
  - **讀哪裡**：整篇
  - **為什麼值得讀**：netns 的權威定義，隔離了什麼的官方說明

### 書籍

- **《Linux Kernel Networking》— Rami Rosen（namespace 章）**
  - **讀哪幾章**：network namespace 那章
  - **這本書的定位**：把 netns 的 kernel 實作講到底

下一章看 tun/tap 裝置——VPN 怎麼把封包「抓進」用戶空間加密再送出，這是所有 VPN（Ch 23-26）的底層機制。

→ [Ch 21 tun/tap 裝置](./21-tun-tap.md)
