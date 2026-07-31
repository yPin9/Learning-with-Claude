# Ch 22 — bridge 與 veth

> **目標**：把虛擬網路的最後兩塊拼上——veth pair（虛擬網線，連接 netns）、bridge（虛擬交換器，匯集多個介面）。然後**動手建一個完整的虛擬網路**：多個 netns（虛擬主機）+ bridge（虛擬交換器）+ NAT（出外網），這正是 Docker 網路的完整底層。Ch 20-22 學的 netns/veth/bridge + Ch 18 的 iptables，組合起來就是容器網路——這章把它們拼成一個能跑的拓樸。

> **環境**：Linux（ip link / bridge）。需 root。實驗在本機建虛擬網路（不影響實體網路）。

## 為什麼 veth 和 bridge 是最後一塊拼圖？

Ch 20 說單獨的 netns 是「網路孤島」，要 veth（虛擬網線）連起來。但如果有很多 netns（多個容器）要互通，總不能每兩個都拉一條 veth（N 個就要 N² 條線）。需要一個「虛擬交換器」把它們都接上——這就是 **bridge**。

veth + bridge 是虛擬網路的最後兩塊：veth 是「虛擬網線」、bridge 是「虛擬交換器」（Ch 3 的交換器的軟體版）。有了它們，加上 netns（虛擬主機，Ch 20）和 iptables NAT（出外網，Ch 18），你能在一台機器上建出**完整的網路拓樸**——這正是 Docker 做的。這章把前面學的全部拼起來，動手建一個能跑的虛擬網路，讓你徹底理解「容器網路」不是魔法。

## 先建立直覺:虛擬網線與虛擬交換器

```
veth + bridge = 虛擬版的「網線」和「交換器」

  veth pair（虛擬網線）：
    一對相連的虛擬介面（veth0 ←→ veth1）
    從一端進的封包，從另一端出（像網線兩端）
    用途：連接兩個 netns，或 netns 連 bridge
        │
  bridge（虛擬交換器）：
    軟體版的 L2 交換器（Ch 3）
    把多個介面「橋接」在一起（插在同一個交換器上）
    自我學習 MAC、按 MAC 轉送（和實體交換器一樣）
        │
  組合成完整拓樸（Docker 的做法）：
        ┌─netns1─┐  ┌─netns2─┐  ┌─netns3─┐
        │ veth   │  │ veth   │  │ veth   │
        └───┬────┘  └───┬────┘  └───┬────┘
            └───────────┼───────────┘
                   ┌────┴────┐
                   │ bridge  │ （虛擬交換器，docker0）
                   └────┬────┘
                        │ iptables NAT（Ch 18）
                        ▼
                     實體網卡 → 外網
        │
  → 多個 netns 用 veth 接到 bridge，bridge 用 NAT 出網
    = 一台機器上的完整虛擬網路 = 容器網路
```

關鍵心智：**veth** 是虛擬網線（一對相連的介面，一端進另一端出），**bridge** 是虛擬交換器（Ch 3 交換器的軟體版，匯集多個介面）。組合：多個 netns（虛擬主機）各用 veth 接到 bridge（虛擬交換器），bridge 用 iptables NAT（Ch 18）出外網——這就是 Docker 容器網路的完整拓樸。

> 這章綜合 netns（Ch 20，虛擬主機）、Ch 3（交換器概念）、Ch 18（NAT）。bridge 是 Ch 3 交換器的軟體版，做的事一樣（學 MAC、按 MAC 轉送）。如果對交換器/NAT 不熟，回看 [Ch 3](./03-link-layer-ethernet-arp.md) 和 [Ch 18](./18-iptables-complete.md)。

## veth pair:虛擬網線

```bash
# 建一對 veth（虛擬網線的兩端）
sudo ip link add veth0 type veth peer name veth1
ip link show type veth
# veth0@veth1 和 veth1@veth0 —— 一對相連的介面

# 它們相連：從 veth0 進的封包從 veth1 出（反之亦然）
# 把兩端放到不同地方：
# - 兩端都在 host：連接 host 的兩個部分
# - 一端在 netns：連接 netns 和 host（Ch 20 預覽過）
# - 一端在 netns A、一端在 netns B：直接連兩個 netns

# 範例：直接連兩個 netns（點對點）
sudo ip netns add nsA
sudo ip netns add nsB
sudo ip link add vethA type veth peer name vethB
sudo ip link set vethA netns nsA          # vethA 端放進 nsA
sudo ip link set vethB netns nsB          # vethB 端放進 nsB
# 設 IP
sudo ip netns exec nsA ip addr add 10.1.1.1/24 dev vethA
sudo ip netns exec nsA ip link set vethA up
sudo ip netns exec nsA ip link set lo up
sudo ip netns exec nsB ip addr add 10.1.1.2/24 dev vethB
sudo ip netns exec nsB ip link set vethB up
sudo ip netns exec nsB ip link set lo up
# 測試：nsA ↔ nsB 直接通（透過虛擬網線）
sudo ip netns exec nsA ping -c2 10.1.1.2
# 清理
sudo ip netns del nsA; sudo ip netns del nsB
```

> **veth pair 是「虛擬網線」——兩端相連，從一端進的封包從另一端出，用來連接 netns**。`ip link add veth0 type veth peer name veth1` 建一對相連的虛擬介面。它們像一條網線的兩端——封包進 veth0 就從 veth1 出。關鍵用法是**把兩端分到不同的網路命名空間**：一端放進 netns（`ip link set vethA netns nsA`）、一端留在 host 或另一個 netns，就把它們「連起來」了。上面的例子直接用 veth 連兩個 netns（點對點，nsA ↔ nsB）——這是最簡單的「兩台虛擬主機直連」。但點對點只適合兩台——如果有很多 netns 要互通，需要 bridge（下節）把它們都接上（不然每兩個都要一條 veth）。veth 是容器網路的基本構件——Docker 為每個容器建一對 veth，一端在容器的 netns、一端接到 docker0 bridge。理解 veth，你看 `ip link` 時那些 `veth1234@if5` 的奇怪介面就懂了——它們是容器的虛擬網線的 host 端。

## bridge:虛擬交換器

```bash
# 建一個 bridge（虛擬交換器）
sudo ip link add br0 type bridge
sudo ip link set br0 up

# 建多個 netns，各用 veth 接到 bridge
for ns in ns1 ns2 ns3; do
    sudo ip netns add $ns
    # 建 veth：一端給 netns，一端接 bridge
    sudo ip link add veth-$ns type veth peer name veth-$ns-br
    sudo ip link set veth-$ns netns $ns               # netns 端
    sudo ip link set veth-$ns-br master br0           # bridge 端（接到 br0）
    sudo ip link set veth-$ns-br up
done

# 給每個 netns 設 IP（同網段，透過 bridge 互通）
i=1
for ns in ns1 ns2 ns3; do
    sudo ip netns exec $ns ip addr add 10.2.2.$i/24 dev veth-$ns
    sudo ip netns exec $ns ip link set veth-$ns up
    sudo ip netns exec $ns ip link set lo up
    i=$((i+1))
done

# 測試：所有 netns 透過 bridge 互通（像插在同一個交換器上）
sudo ip netns exec ns1 ping -c2 10.2.2.2          # ns1 → ns2
sudo ip netns exec ns1 ping -c2 10.2.2.3          # ns1 → ns3

# 看 bridge 的 MAC 學習表（和實體交換器一樣，Ch 3）
bridge fdb show br0                                 # forwarding database（MAC 表）

# 清理
for ns in ns1 ns2 ns3; do sudo ip netns del $ns; done
sudo ip link del br0
```

> **bridge 是「虛擬交換器」——把多個 veth 接上去，它們就像插在同一個交換器上，互相能通**。`ip link add br0 type bridge` 建一個虛擬交換器，`ip link set <veth> master br0` 把 veth 接到它（像把網線插進交換器的 port）。接到同一個 bridge 的所有介面，就像插在同一個實體交換器（Ch 3）——它們在同一個 L2 網段，能直接互通（bridge 學 MAC、按 MAC 轉送，`bridge fdb show` 看它的 MAC 學習表，和 Ch 3 的交換器一模一樣）。這解決了「多個 netns 互通」的問題——不用每兩個拉 veth（N² 條線），而是每個 netns 一條 veth 接到 bridge（N 條線），bridge 負責它們之間的轉送。**這正是 Docker 的 `docker0` bridge**——Docker 建一個 bridge，每個容器一條 veth 接上去，容器們就在同一個虛擬網段互通。bridge + veth + netns 是容器網路的三大件。理解這個拓樸（多 netns + veth + bridge），你看 Docker 網路（`docker network inspect`）就認得出 bridge、看 `ip link` 就認得出那些 veth。

## 動手建完整的虛擬網路（容器網路的全貌）

```bash
# 建一個「完整的」虛擬網路：netns + bridge + NAT 出外網（= Docker 網路）
# 這個拓樸讓虛擬主機既能互通、又能上外網

# 1. 建 bridge（虛擬交換器），給它一個 IP 當「閘道」
sudo ip link add br0 type bridge
sudo ip addr add 10.3.3.254/24 dev br0       # bridge 當虛擬閘道
sudo ip link set br0 up

# 2. 建 netns + veth 接到 bridge
sudo ip netns add container1
sudo ip link add veth-c1 type veth peer name veth-c1-br
sudo ip link set veth-c1 netns container1
sudo ip link set veth-c1-br master br0
sudo ip link set veth-c1-br up

# 3. netns 內設 IP + 預設路由（指向 bridge 的 IP 當閘道）
sudo ip netns exec container1 ip addr add 10.3.3.1/24 dev veth-c1
sudo ip netns exec container1 ip link set veth-c1 up
sudo ip netns exec container1 ip link set lo up
sudo ip netns exec container1 ip route add default via 10.3.3.254   # 閘道=bridge

# 4. 在 host 開啟轉發 + NAT（讓 netns 上外網，Ch 18）
sudo sysctl -w net.ipv4.ip_forward=1
sudo iptables -t nat -A POSTROUTING -s 10.3.3.0/24 -o eth0 -j MASQUERADE
# （eth0 換成你的實體網卡名）

# 5. 測試：container1 能上外網了！
sudo ip netns exec container1 ping -c2 8.8.8.8       # 透過 NAT 出網
# DNS（要 netns 有 resolv.conf，或直接測 IP）

# 清理
sudo ip netns del container1
sudo ip link del br0
sudo iptables -t nat -D POSTROUTING -s 10.3.3.0/24 -o eth0 -j MASQUERADE
```

```
這個拓樸 = Docker 的 bridge 網路：

  container1 (netns, 10.3.3.1)
       │ veth
  ┌────┴─────────────────────┐
  │  br0 (bridge, 10.3.3.254)│ ← 虛擬交換器 + 虛擬閘道（= docker0）
  └────┬─────────────────────┘
       │ iptables MASQUERADE (NAT, Ch 18/8)
       ▼
    eth0 → 外網
        │
  對照 Docker：
    docker0 = br0（bridge）
    容器的 eth0 = netns 裡的 veth
    Docker 自動做的 NAT = 你手動的 MASQUERADE
        │
  → 你剛剛「手工建了一個 Docker 網路」！
```

> **你剛剛手工建的「netns + bridge + NAT」拓樸，就是 Docker 的 bridge 網路——Docker 不是魔法，是這些 Linux 原語的自動化組合**。這個完整拓樸把 Part 5（和 Ch 8）學的全部串起來：**netns**（Ch 20，虛擬主機/容器）+ **veth**（虛擬網線）+ **bridge**（虛擬交換器，= Docker 的 `docker0`）+ **預設路由**（指向 bridge 當閘道）+ **iptables MASQUERADE**（Ch 8/18，NAT 出外網）。對照 Docker：`docker0` 就是你的 `br0`、容器裡的 `eth0` 就是 netns 裡的 veth、Docker 自動做的 NAT 就是你手動的 MASQUERADE。**Docker 啟動容器時，底層就是執行類似這些命令**（建 netns、建 veth 接到 docker0、設 IP 和路由、加 NAT 規則）。所以當你理解了這個手工拓樸，Docker 網路就完全不神祕了——它是這些 Linux 網路原語的自動化封裝。這也是 debug 容器網路問題的基礎（Ch 37）——容器連不上外網？檢查 NAT（MASQUERADE）和 ip_forward；容器間不通？檢查 bridge 和 veth；容器 DNS 不通？檢查 resolv.conf（Ch 20 的陷阱）。Ch 37 會深入容器網路的各種模式，但底層就是這章建的東西。

## 故意弄壞:拓樸的常見問題

```bash
# 建拓樸時的常見錯誤（debug 容器網路的根源）

# 問題 1：忘了開 ip_forward → netns 上不了外網
# （封包到了 host 但 host 不轉發，Ch 0/18）
cat /proc/sys/net/ipv4/ip_forward    # 0 = 沒開，netns 出不去
# sudo sysctl -w net.ipv4.ip_forward=1

# 問題 2：忘了 NAT（MASQUERADE）→ 封包出得去回不來
# netns 的私有 IP（10.3.3.x）在外網沒有意義，回應找不到路回來
# → 沒 NAT 就是「ping 8.8.8.8 出得去但收不到回應」

# 問題 3：netns 沒設預設路由 → 只能到 bridge 網段，出不了
# sudo ip netns exec c1 ip route   # 沒 default → 去不了外網

# 問題 4：veth 沒 up（兩端都要 up）
# host 端和 netns 端的 veth 都要 ip link set up

# 問題 5：bridge 沒 up 或 veth 沒接到 bridge（master）
# bridge fdb show 看 MAC 學習，ip link 看 veth 的 master

# debug 工具：在每個環節抓封包看「斷在哪」
# sudo tcpdump -i br0          # bridge 上看得到封包嗎？
# sudo tcpdump -i eth0         # 實體網卡看得到 NAT 後的封包嗎？
```

> **建虛擬網路的五個常見錯誤（ip_forward、NAT、預設路由、veth up、bridge 連接）正是 debug 容器網路的清單**。當虛擬網路（或容器）「連不上外網」，按這個清單查：(1) **ip_forward 沒開**（host 不轉發封包，Ch 0/18）——`sysctl net.ipv4.ip_forward`；(2) **沒 NAT**（私有 IP 出得去回不來，因為外網不認識 10.3.3.x）——檢查 MASQUERADE 規則；(3) **netns 沒預設路由**（只能到 bridge 網段）——`ip netns exec ip route` 看有沒有 default；(4) **veth 沒 up**（兩端都要 up）；(5) **bridge 沒連好**（veth 沒設 master 或 bridge 沒 up）。debug 技巧：**在每個環節抓封包看「斷在哪」**（Ch 14）——`tcpdump -i br0`（封包有到 bridge 嗎）、`tcpdump -i eth0`（NAT 後的封包有出去嗎、回應有回來嗎），逐段定位封包消失的地方。這個清單和 debug 方法直接適用於真實的容器網路問題（Ch 37）——Docker 容器「沒網路」八成是這五個之一（雖然 Docker 通常自動處理，但出錯時要會查）。這完成了 Part 5 的目標：你不只會用容器，還理解了容器網路的完整底層，能在它出問題時 debug。

## 動手練習

1. veth 連兩個 netns：建兩個 netns 用 veth 直連，測試互通，理解虛擬網線

2. bridge 連多個 netns：建 bridge + 3 個 netns，全部接上，測試它們互通（像插同一交換器）

3. 建完整拓樸：跟著「動手建完整虛擬網路」建 netns+bridge+NAT，讓 netns 上外網

4. 對照 Docker：如果有 Docker，`ip link` 看 docker0 和 veth、`iptables -t nat -L` 看 Docker 的 NAT 規則，對照你手建的

5. 跑「故意弄壞」：故意漏掉 ip_forward/NAT/預設路由，看 netns 上不了網，逐一修復

## 本章重點整理

- veth pair 是虛擬網線（兩端相連），連接 netns；bridge 是虛擬交換器（Ch 3 的軟體版），匯集多個介面
- 多個 netns 各用 veth 接到 bridge，就在同一虛擬網段互通（解決 N² 條線的問題）= Docker 的 docker0
- 完整拓樸 = netns（虛擬主機）+ veth（網線）+ bridge（交換器）+ 預設路由 + iptables NAT（出外網）
- 這個手工拓樸就是 Docker 的 bridge 網路——Docker 是這些 Linux 原語的自動化組合，不是魔法
- debug 清單：ip_forward、NAT（MASQUERADE）、預設路由、veth up、bridge 連接——容器網路問題的根源

## 自我檢核

- [ ] 能解釋 veth（虛擬網線）和 bridge（虛擬交換器）各是什麼
- [ ] 會用 veth + bridge 連接多個 netns 成一個虛擬網段
- [ ] 能手工建出「netns + bridge + NAT 出外網」的完整拓樸
- [ ] 理解這個拓樸和 Docker bridge 網路的對應關係
- [ ] 知道虛擬網路「連不上外網」的五個常見原因和 debug 方法

## 延伸閱讀

### 必讀文章

- **[Container Networking from Scratch](https://labs.iximiuz.com/tutorials/container-networking-from-scratch)** — iximiuz
  - **這篇說什麼**：從零用 netns/veth/bridge/NAT 手工建出容器網路，每步互動
  - **讀哪裡**：整篇，跟著做
  - **為什麼值得讀**：本章「完整拓樸」的互動深入版，把容器網路徹底拆解

- **[Linux bridge 與 veth 詳解](https://hechao.li/2017/12/13/linux-bridge-part1/)** — Hechao Li
  - **這篇說什麼**：bridge 和 veth 的原理、MAC 學習、轉送
  - **為什麼值得讀**：把 bridge 的底層（和 Ch 3 交換器的關係）講透

### 官方文件

- **[veth(4)](https://man7.org/linux/man-pages/man4/veth.4.html)** + **[bridge(8)](https://man7.org/linux/man-pages/man8/bridge.8.html)** — Linux man-pages
  - **讀哪裡**：veth 的配對機制、bridge 命令的 fdb（MAC 表）
  - **為什麼值得讀**：veth/bridge 的權威定義

### 書籍 / 影片

- **[Container Networking (Liz Rice 演講)](https://www.youtube.com/watch?v=6v_BDHIgOY8)**
  - **這篇說什麼**：現場用 Linux 原語建容器網路
  - **為什麼值得讀**：把 netns→veth→bridge→容器網路的完整鏈條演示一遍

Part 5（防火牆與 Linux 網路機制）到此完成——你掌握了防火牆（iptables/nftables）和虛擬網路（netns/tun-tap/bridge/veth），這是 VPN 和容器網路的完整底層。下一章進入 Part 6（VPN），先建立 VPN 的總覽和原理。

→ [Ch 23 VPN 總覽與原理](./23-vpn-overview.md)
