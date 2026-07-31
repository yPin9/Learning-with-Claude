# Ch 0 — 環境搭建

> **目標**：把整門課要用的工具一次裝齊、確認你能抓封包（這是本課的核心手法）、建立一個能安全做網路實驗的環境。讀完你有一個「弄壞了刪掉重來」的實驗場，以及對接下來會用到的每個工具的初步認識。

> **環境**：Ubuntu 22.04+ / Debian 12+（其他 distro 套件名略異）。需要 sudo。部分實驗（抓封包、建 netns）需要 root 權限。

## 為什麼環境要先搞定？

這門課的核心信條是「**每個協定都抓封包看**」——不是讀 RFC 想像 TCP 握手長怎樣，而是用 tcpdump 真的把那三個封包抓出來看。要做到這個，你需要能抓封包的權限、看得懂封包的工具、以及一個能隨意建虛擬網路又不怕弄壞的環境。

這章把這些一次備齊。值得花時間做對——後面每一章都依賴這裡裝的工具。特別是 network namespace（netns），它讓你在**一台機器上**模擬出多台主機、路由器、NAT，是本課做實驗的主力，弄壞了 `ip netns del` 刪掉就好，不會影響你的真實網路。

## 先建立直覺:你需要三種環境

```
本課的三種實驗環境，各有用途：

  1. 本機（你的 Linux 桌機 / 筆電 / WSL2）
     用途：裝工具、抓自己的封包、跑大部分指令
     限制：不能亂改路由/防火牆（會斷你自己的網）
        │
  2. network namespace（netns，在本機裡）
     用途：建虛擬網路拓樸（多主機/路由/NAT/VPN）
     優勢：完全隔離，弄壞了刪掉重來，不影響本機網路
     ★ 本課做實驗的主力
        │
  3. VPS（雲端伺服器，Part 8 才需要）
     用途：真實公網 IP、架 VPN/服務、體驗「暴露在公網」
     成本：~$5/月，Part 8 開始建議買一台
        │
  → Part 1-7 主要用本機 + netns
    Part 8 開始用 VPS
```

關鍵心智：你不需要一堆實體機器或複雜的虛擬機來學網路。**network namespace** 能在一台 Linux 上變出整個虛擬網路——多個「主機」、它們之間的「網線」、「路由器」、「NAT」，全部隔離、可拋棄。這是現代學網路（和容器網路的底層，Ch 20）的關鍵工具。

## 安裝工具包

一次裝齊整門課需要的工具：

```bash
# 更新套件索引
sudo apt update

# 核心網路工具（Part 4 會深入每一個）
sudo apt install -y \
    iproute2 \          # ip / ss / tc（現代網路設定的核心，取代 ifconfig/netstat）
    tcpdump \           # 命令列抓封包（本課的主角工具）
    wireshark \         # 圖形化封包分析（裝時選「允許非 root 抓包」要謹慎，後述）
    dnsutils \          # dig / nslookup（DNS 查詢，Ch 15）
    traceroute mtr \    # 路徑追蹤（Ch 16）
    nmap ncat \         # 連接埠掃描 / netcat（Ch 17）
    curl wget \         # HTTP 客戶端（Ch 10/17）
    iptables nftables \ # 防火牆（Ch 18/19）
    net-tools \         # 舊工具（ifconfig/netstat，看舊教學會用到，但我們用 ip/ss）
    iputils-ping \      # ping
    socat \             # 萬用 socket 工具（進階）
    build-essential     # gcc 等（少數章節有 C 範例）

# VPN 工具（Part 6）
sudo apt install -y wireguard openvpn

# 驗證關鍵工具裝好了
ip -V                  # ip 版本
tcpdump --version      # tcpdump 版本
dig -v                 # dig 版本
```

```bash
# 確認你的網路介面（後面常用）
ip link                # 列出所有網路介面（lo, eth0/ens33, wlan0...）
ip addr                # 介面 + IP 位址（取代舊的 ifconfig）
ip route               # 路由表（封包往哪送）
```

> **本課全程用 `ip`/`ss`（iproute2），不用 `ifconfig`/`netstat`（net-tools）**。你會在舊教學看到 `ifconfig`（看 IP）、`netstat`（看連線）、`route`（看路由）——這些是 net-tools 套件的工具，**已過時且不再積極維護**（很多新功能不支援，如多 IP、policy routing）。現代 Linux 用 **iproute2**：`ip addr`（取代 ifconfig）、`ss`（取代 netstat）、`ip route`（取代 route）。它們更強大、輸出更一致、支援所有現代功能。我們裝 net-tools 只是讓你看舊教學時認得，但所有實作都用 iproute2。記住對照：`ifconfig`→`ip addr`、`netstat -tlnp`→`ss -tlnp`、`route -n`→`ip route`。

## 讓 tcpdump 能抓封包

抓封包需要特殊權限，先確認你能抓：

```bash
# 抓封包需要 root（或特定 capability）
sudo tcpdump -i any -c 5
#   -i any：所有介面
#   -c 5：抓 5 個封包就停
# 應該看到一些封包飛過（即使「沒在做什麼」，背景也有流量）

# 如果想不用每次 sudo（給 tcpdump CAP_NET_RAW capability）
sudo setcap cap_net_raw,cap_net_admin=eip $(which tcpdump)
# 之後一般使用者也能抓（謹慎：等於開放抓包權限）

# Wireshark 讓非 root 抓包（安裝時的選項）
sudo dpkg-reconfigure wireshark-common   # 選 Yes
sudo usermod -aG wireshark "$USER"        # 把自己加入 wireshark 群組
# 重新登入後生效（群組在登入時載入）
```

```
抓封包為什麼需要 root / 特殊權限：

  一般程式只能看「自己的」socket 收發的資料
  抓封包要看「網卡上所有經過的封包」（含別人的）
    → 這是 promiscuous mode + raw socket
    → kernel 視為敏感操作（能竊聽同網段流量）
    → 需要 CAP_NET_RAW capability（root 有，或單獨授予）
        │
  → 所以 tcpdump 預設要 sudo
    setcap 能精準授予「抓包」這一個能力（不用整個 root）
```

> **抓封包需要 root 是因為它能看「網卡上所有封包」，不只你自己的**。一般程式透過 socket 只能看到「送給自己」的資料。tcpdump 要看的是**網卡上經過的所有封包**（包括同網段其他機器的，如果在 promiscuous mode）——這是潛在的竊聽能力，所以 kernel 要求 `CAP_NET_RAW` capability（root 有）。`setcap` 能只授予 tcpdump 這**一個**能力（不用每次 sudo，也不用給整個 root），是比較精準的做法。在共用機器上要謹慎——能抓包等於能看到同網段的明文流量（這也是為什麼 HTTPS 重要，Ch 11）。

## 玩第一個 network namespace

netns 是本課實驗主力，先玩一個最簡單的：

```bash
# 建立一個 network namespace（一個隔離的「虛擬主機」）
sudo ip netns add test1
sudo ip netns list                    # 列出：test1

# 在這個 netns 裡執行命令（它有自己獨立的網路 stack）
sudo ip netns exec test1 ip link      # 只看到 lo（loopback），完全隔離！
#   這個 netns 和你的本機網路完全分開
#   它自己的介面、路由表、防火牆規則，互不影響

# 把 netns 的 lo 拉起來，ping 自己
sudo ip netns exec test1 ip link set lo up
sudo ip netns exec test1 ping -c 2 127.0.0.1   # 通（loopback）

# 刪掉（弄壞了就這樣重來）
sudo ip netns del test1
```

```
network namespace 是什麼：

  正常情況：一台 Linux 有「一套」網路 stack
    一個介面列表、一個路由表、一套防火牆規則、一套 socket
        │
  network namespace：把網路 stack「複製」出獨立的一套
    每個 netns 有自己的：
      - 網路介面（互相看不到）
      - 路由表
      - 防火牆規則（iptables/nftables）
      - socket / port（兩個 netns 都能用 port 80，不衝突）
        │
  → 像在一台機器裡開出多台「虛擬主機」
    用 veth（虛擬網線，Ch 22）把它們連起來 = 虛擬網路
    這就是 Docker 容器網路隔離的底層機制（Ch 20/37）
```

> **network namespace 是「在一台機器裡變出多台虛擬主機」的 kernel 機制——它是本課實驗的主力，也是容器網路的底層**。正常一台 Linux 有「一套」網路 stack（介面、路由表、防火牆、port）。netns 把這套複製成多個獨立的——每個 netns 像一台獨立主機，有自己的介面和路由，互相隔離（兩個 netns 都能監聽 port 80 不衝突）。用 `veth`（虛擬網線，Ch 22）連接它們，就能在一台機器上建出任意網路拓樸——多主機、路由器、NAT、VPN 兩端，全部隔離、可拋棄。這正是 Docker 容器網路隔離的底層機制（Ch 20/37 深入）。Part 5 開始你會大量用它建實驗網路。現在記住三個命令：`ip netns add`（建）、`ip netns exec <name> <cmd>`（在裡面執行）、`ip netns del`（刪）。

## 建一個課程實驗目錄

```bash
# 建一個放實驗腳本、抓的封包、設定檔的目錄
mkdir -p ~/netlab/{captures,configs,scripts}
cd ~/netlab

# captures：放 tcpdump 抓的 .pcap 檔（用 Wireshark 開）
# configs：放 WireGuard/nginx 等設定檔
# scripts：放建 netns 拓樸的腳本

echo "netlab ready at ~/netlab"
```

## 故意弄壞:體會 netns 的隔離

```bash
# 證明 netns 真的隔離（弄壞 netns 不影響本機）
sudo ip netns add broken
sudo ip netns exec broken ip route add default via 1.2.3.4   # 在 netns 裡加一條「壞」路由
sudo ip netns exec broken ip route                            # netns 的路由表有這條壞路由
ip route                                                       # 你「本機」的路由表完全沒受影響！
#   → netns 的設定改動，完全不影響本機
#   這就是為什麼能放心在 netns 裡亂搞

sudo ip netns del broken   # 刪掉，壞路由跟著消失
```

這驗證了 netns 的隔離性——你在 netns 裡做任何危險操作（改路由、設防火牆、弄壞網路），都**不影響本機**。這是為什麼本課敢讓你「故意弄壞」——在 netns 裡弄壞是安全的，`ip netns del` 一刪就乾淨。對比：如果直接在本機改路由表，可能瞬間斷掉你的 SSH 連線或網路。

## 進階:確認核心功能可用

某些實驗需要 kernel 功能，先確認：

```bash
# 確認 IP forwarding 能開（路由/NAT/VPN 需要，Ch 8/23）
cat /proc/sys/net/ipv4/ip_forward          # 預設 0（關閉）
sudo sysctl net.ipv4.ip_forward=1           # 暫時開啟（重開機失效）
# 永久開：寫進 /etc/sysctl.conf 或 /etc/sysctl.d/

# 確認 WireGuard 核心模組可用（Ch 24）
sudo modprobe wireguard && echo "WireGuard kernel module OK"
lsmod | grep wireguard

# 確認你的 kernel 版本（部分功能版本相關）
uname -r                                     # 5.6+ 內建 WireGuard

# 確認 tun/tap 裝置可用（VPN 需要，Ch 21）
ls /dev/net/tun && echo "tun/tap available"
```

> **`ip_forward`（IP 轉發）是路由、NAT、VPN 的開關，預設關閉**。一台 Linux 預設**不會**幫別人轉發封包（收到目標不是自己的封包就丟棄）——它是「主機」不是「路由器」。`sysctl net.ipv4.ip_forward=1` 打開轉發，讓它能當路由器（轉發封包）。Part 5 的路由實驗、Ch 8 的 NAT、Ch 23 的 VPN 都需要它。注意 `sysctl` 設的是**暫時**的（重開機失效），要永久得寫進 `/etc/sysctl.d/`。記住這個開關——很多「VPN/路由設好了但封包不通」的問題，根因就是忘了開 ip_forward。

## 動手練習

1. 裝齊工具：跑安裝命令，逐一驗證（`ip -V`、`tcpdump --version`、`dig -v`、`wg --version`）

2. 抓第一個封包：`sudo tcpdump -i any -c 10`，看背景有什麼流量（ARP、DNS、可能的 mDNS）

3. 認識你的網路：`ip addr`（你的 IP）、`ip route`（預設閘道）、`ip link`（介面），記下你的主要介面名

4. 玩 netns：建一個 netns、在裡面 `ip link` 看隔離、ping loopback、刪掉。重複幾次熟悉三個命令

5. 跑「故意弄壞」：在 netns 裡加壞路由，確認本機不受影響，理解隔離性

## 本章重點整理

- 本課核心手法是「抓封包看」——環境要能 tcpdump（需 root/CAP_NET_RAW，或 setcap 精準授權）
- 全程用 iproute2（ip/ss）不用過時的 net-tools（ifconfig/netstat）；記住對照表
- network namespace 是實驗主力：一台機器變多台虛擬主機，隔離、可拋棄，是容器網路的底層
- 三種環境：本機（裝工具/抓包）、netns（建虛擬網路）、VPS（Part 8 真實公網）
- ip_forward 預設關閉，是路由/NAT/VPN 的開關——很多「不通」問題的根因

## 自我檢核

- [ ] 工具裝齊，能跑 tcpdump 抓到封包
- [ ] 知道為什麼抓封包需要 root，setcap 的作用
- [ ] 能建立、進入、刪除一個 network namespace
- [ ] 理解 netns 的隔離性，以及為什麼它適合做網路實驗
- [ ] 知道 iproute2 對應 net-tools 的命令（ip addr / ss / ip route）

## 延伸閱讀

### 文章

- **[Network namespaces 介紹](https://blog.scottlowe.org/2013/09/04/introducing-linux-network-namespaces/)** — Scott Lowe
  - **這篇說什麼**：用 veth + netns 從零建一個雙主機網路，每步都有圖
  - **讀哪裡**：整篇（短），跟著做一遍
  - **為什麼值得讀**：把 netns 的概念和操作講得最清楚，是 Ch 20/22 的前導

- **[A tcpdump Tutorial with Examples](https://danielmiessler.com/study/tcpdump/)** — Daniel Miessler
  - **這篇說什麼**：tcpdump 的常用選項和 filter 範例大全
  - **讀哪裡**：開頭的基本用法，filter 那節留待 Ch 14
  - **為什麼值得讀**：放手邊查 tcpdump 命令的好參考

### 官方文件

- **[iproute2 官方 wiki](https://wiki.linuxfoundation.org/networking/iproute2)** — Linux Foundation
  - **讀哪裡**：ip 命令的子命令總覽
  - **為什麼值得讀**：iproute2 各命令的權威來源；遇到 ip 命令語法問題的仲裁

### 書籍

- **《TCP/IP Illustrated, Volume 1》— Ch 1 (Introduction)** — Stevens & Fall
  - **讀哪幾章**：Ch 1（網路架構總覽，為整門課鋪墊）
  - **這本書的定位**：本課協定部分的聖經，現在讀 Ch 1 建立全局觀
  - **前提**：無

下一章我們跟著「一個封包」走完它從你按 Enter 到伺服器回應的完整旅程，建立整門課的全局地圖。

→ [Ch 1 一個封包的旅程](./01-internet-journey.md)
