# Ch 4 — 網路層：IP 與 ICMP

> **目標**：理解網路層——IP 封包的結構、IP 怎麼做「跨網段的轉送」（路由的基本原理）、TTL 怎麼防止封包無限繞圈、封包分片（fragmentation）和 MTU 的關係、以及 ICMP（ping/traceroute 背後的協定）。這是 Ch 1 旅程「封包穿越網路」的核心——封包怎麼從你家一跳跳到地球另一端。

> **環境**：Linux（ip route / ping / traceroute）。實驗可用 netns 建多網段拓樸。

## 為什麼網路層是網際網路的核心？

連結層（Ch 3）只能在「同一個區網」內送封包。但網際網路是無數個區網連起來的——你的封包要從台北的家，經過 ISP、海底電纜、美國的骨幹網路，到達某個資料中心。這個「跨越無數網段、找到全球任一台機器」的能力，就是網路層（IP）提供的。

IP 是網際網路的「**那個共同協定**」——不管底下是光纖、WiFi、4G，不管上面跑 TCP 還 UDP，全世界的封包都用 IP 定址和轉送。理解 IP 回答了核心問題：封包怎麼知道往哪走（路由）？為什麼 ping 能測連通、traceroute 能看路徑（ICMP + TTL）？為什麼有時大檔案傳輸會詭異地卡住（MTU/分片問題）？這章是整個網路的中樞。

## 先建立直覺:每個路由器只知道「下一站」

```
封包跨網段傳輸：接力賽，每站只管下一棒

  你的封包要去 93.184.216.34（地球另一端）
        │
  你的電腦：「不是我這網段的，交給閘道（路由器）」
        ↓
  你家路由器：「我不知道完整路徑，但我知道往這個方向送」
        ↓ 送給 ISP 的路由器
  ISP 路由器：「往這個方向送」
        ↓
  ... 經過十幾個路由器，每個只決定「下一跳」往哪 ...
        ↓
  目標網段的路由器：「這個 IP 是我這網段的，直接送到」
        ↓
  93.184.216.34（到達）
        │
  關鍵：沒有任何一個路由器知道「完整路徑」
    每個只知道「往這個目標，我的下一跳是誰」
    → 像接力賽，每棒只管交給下一棒
    → 這叫「逐跳轉送」（hop-by-hop forwarding）
```

關鍵心智：封包跨網段傳輸是**逐跳轉送**——沒有任何路由器知道完整路徑，每個路由器只根據自己的路由表決定「往這個目標，下一跳交給誰」。封包像接力棒，一站站傳遞。每一跳，封包的 IP 標頭（來源/目標 IP）不變，但連結層的 MAC 會換（換成下一跳的 MAC，Ch 3）。

> 網路層在連結層（Ch 3）之上。封包在每一跳之間，靠連結層（MAC/ARP）實際送到「下一個路由器」，而 IP 層決定「下一個路由器是誰」。如果對 MAC/ARP 不熟，回看 [Ch 3](./03-link-layer-ethernet-arp.md)。

## IP 封包結構

```
IPv4 封包標頭（20 bytes，關鍵欄位）：

  ┌────────┬────────┬──────────────────┐
  │版本│長度│  服務類型 │    總長度          │
  ├────────┴────────┼──────┬───────────┤
  │   識別碼(ID)     │旗標│  分片偏移      │ ← 分片用
  ├────────┬────────┼──────┴───────────┤
  │  TTL   │ 協定   │      標頭校驗和     │ ← TTL/上層協定
  ├────────┴────────┴──────────────────┤
  │            來源 IP 位址 (4 bytes)     │ ← 誰送的
  ├──────────────────────────────────────┤
  │            目標 IP 位址 (4 bytes)     │ ← 送給誰
  └──────────────────────────────────────┘
            接著是 payload（TCP/UDP 區段）
        │
  關鍵欄位：
    來源/目標 IP：網路層定址（全球，跨網段）
    TTL：存活時間（每經一個路由器減 1，到 0 就丟棄）
    協定：payload 裝什麼（6=TCP, 17=UDP, 1=ICMP）
    ID/旗標/偏移：分片重組用
    總長度：整個封包大小（含 payload）
```

```bash
# 看 IP 封包的細節（tcpdump -v 顯示 TTL、ID、長度等）
sudo tcpdump -i any -n -v -c 1 icmp &
ping -c 1 8.8.8.8 > /dev/null
# IP (tos 0x0, ttl 64, id 12345, ... proto ICMP (1), length 84)
#     192.168.1.100 > 8.8.8.8: ICMP echo request ...
#         ↑ttl=64  ↑proto=ICMP  ↑來源>目標
sudo pkill tcpdump
```

> **IP 標頭的「協定」欄位是分層的關鍵——它告訴接收方 payload 裝的是 TCP、UDP 還是 ICMP**。就像 Ethernet 的 EtherType 說「裡面是 IP 還 ARP」（Ch 3），IP 標頭的「協定」欄位（6=TCP、17=UDP、1=ICMP）告訴接收方的 IP 層「拆完我之後，把 payload 交給哪個上層協定處理」。這是封裝/解封裝（Ch 2）的具體實現——每層都有個欄位指明「上層是誰」。**來源/目標 IP** 是網路層定址（全球唯一、跨網段，和 MAC 的區網內定址不同，Ch 3）——這兩個 IP 在整個旅程**不變**（每一跳都是同樣的來源/目標 IP），變的是外層的 MAC（每跳換成下一跳的）。理解這點：IP 提供「端到端的全球定址」，連結層提供「每一跳的實際傳送」。

## 路由:封包往哪送

路由（routing）是網路層的核心——決定封包的下一跳：

```bash
# 看你的路由表（封包往哪送的規則）
ip route
# default via 192.168.1.1 dev eth0           ← 預設路由（其他都交給閘道）
# 192.168.1.0/24 dev eth0 ... src 192.168.1.100   ← 本網段直接送（不經閘道）

# 路由決策：對一個目標 IP，查「下一跳」是誰
ip route get 8.8.8.8
# 8.8.8.8 via 192.168.1.1 dev eth0 ...        ← 不在本網段 → 交給閘道
ip route get 192.168.1.50
# 192.168.1.50 dev eth0 ...                   ← 在本網段 → 直接送（無 via）
```

```
路由決策的邏輯（你的電腦對每個封包做的事）：

  目標 IP = X
        │
  1. X 在「本網段」嗎？（查路由表的網段規則）
     是 → 直接送（用 ARP 找 X 的 MAC，Ch 3）
        │
  2. 不在本網段 → 找「最符合的路由規則」
     有更精確的規則嗎？（如 10.0.0.0/8 走某路由）
        │
  3. 都沒有 → 用「預設路由」（default via 閘道）
     交給閘道（你家路由器），讓它繼續轉送
        │
  → 規則：最長前綴匹配（longest prefix match）
    越精確（網段越小）的規則優先
    default（0.0.0.0/0）是最不精確的，最後才用
```

> **路由的核心規則是「最長前綴匹配」（longest prefix match）——越精確的規則優先**。一個目標 IP 可能符合多條路由規則（如 `10.1.2.0/24`、`10.0.0.0/8`、`0.0.0.0/0` 都包含 `10.1.2.5`），路由器選**最精確**的那條（網段最小、前綴最長的，這裡是 `/24`）。`default`（`0.0.0.0/0`，包含所有 IP）是最不精確的，所以是「其他都不符合時」的後備——「不在我認識的任何網段，就交給閘道」。這就是為什麼家用電腦的路由表通常只有兩條：本網段（直接送）和 default（其他都交給路由器）。`ip route get <IP>` 是 debug 路由的利器——它告訴你「這個封包實際會往哪送」，是排查「為什麼連不到某 IP」的關鍵（可能路由錯了，封包送錯方向）。Ch 5（CIDR）會深入「網段」和前綴的數學。

## TTL:防止封包無限繞圈

```
TTL（Time To Live，存活時間）的作用：

  問題：如果路由表設錯，封包可能繞圈（A→B→A→B...無限）
    沒有機制阻止 → 封包永遠在網路上繞，塞爆網路
        │
  TTL 的解法：
    每個封包有個 TTL 值（如 64）
    每經過一個路由器，TTL 減 1
    TTL 變成 0 → 路由器丟棄這個封包，並回一個 ICMP 錯誤
        │
  → TTL 是封包的「壽命上限」（最多經過幾個路由器）
    防止路由迴圈造成封包永遠繞圈
        │
  巧妙的副作用：traceroute 利用 TTL 看路徑（後述）
```

```bash
# 看封包的 TTL（每經一跳減 1）
ping -c 1 8.8.8.8
# 64 bytes from 8.8.8.8: ... ttl=115 ...
#   回來的 TTL=115 → 8.8.8.8 送出時是 128，經過 13 跳到你（128-115=13）
#   （不同 OS 初始 TTL 不同：Linux 64, Windows 128）

# 故意設小 TTL 看封包「壽命不夠」
ping -c 1 -t 1 8.8.8.8         # TTL=1：只能走一跳
# From 192.168.1.1: Time to live exceeded   ← 第一個路由器就把它丟了（TTL 用完）
#   → 這正是 traceroute 的原理！
```

> **TTL 防止路由迴圈，而 traceroute 巧妙地「濫用」它來看路徑**。TTL 的本意是安全機制——防止設錯的路由讓封包無限繞圈（每跳減 1，到 0 丟棄）。但 traceroute（Ch 16）發現了它的妙用：**故意送 TTL=1 的封包**，第一個路由器把它丟棄並回 ICMP「TTL exceeded」錯誤（暴露了第一個路由器的 IP）；再送 TTL=2，第二個路由器回應（暴露第二跳）……依此類推，逐步「點亮」整條路徑上的每個路由器。這是工程上「利用協定副作用」的經典案例。TTL 還能反推距離——`ping` 回來的 TTL 是 `初始值 - 跳數`，初始值通常是 64（Linux）或 128（Windows），所以 `ttl=115` 大概是 Windows 主機經過 13 跳（128-115）。理解 TTL，你就懂了 traceroute 的魔法（Ch 16 深入）和「為什麼封包不會永遠繞圈」。

## ICMP:網路層的「信差」

ICMP（網際控制訊息協定）是 IP 的「錯誤回報和診斷」夥伴：

```bash
# ping 用的就是 ICMP echo request/reply
sudo tcpdump -i any -n icmp &
ping -c 2 8.8.8.8
# ICMP echo request, ...        ← ping 送出（type 8）
# ICMP echo reply, ...          ← 對方回應（type 0）
sudo pkill tcpdump

# ICMP 的主要類型（不只 ping）
# type 0/8：  echo reply/request（ping）
# type 3：    destination unreachable（目標不可達）
#   code 1：host unreachable, code 3：port unreachable...
# type 11：   time exceeded（TTL 用完，traceroute 用）
# type 5：    redirect（路由重導向）

# 觀察 ICMP 錯誤：連一個沒開的 port（UDP）
nc -u -z 8.8.8.8 1 2>&1        # 可能收到 ICMP port unreachable
```

```
ICMP 是 IP 的「診斷與錯誤回報」協定：

  IP 本身「盡力而為」（best-effort）：送出去不保證到、出錯不通知
  ICMP 補上「回報機制」：
    封包到不了目標 → 路由器回 ICMP「destination unreachable」
    TTL 用完 → 回 ICMP「time exceeded」（traceroute 用）
    要測試連通 → echo request/reply（ping）
        │
  → ICMP 不傳「資料」，傳「關於網路的訊息」
    它是 IP 的夥伴，幫忙診斷和回報
        │
  注意：很多防火牆擋 ICMP（Ch 18）
    → 所以有時 ping 不通但服務正常（ICMP 被擋，TCP 沒擋）
```

> **ICMP 是 IP 的「診斷夥伴」，但常被防火牆封鎖——這造成「ping 不通但服務正常」的困惑**。IP 本身是「盡力而為」（best-effort）——送出去不保證到達、丟了也不通知。ICMP 補上回報機制：目標不可達、TTL 用完、需要分片但不允許等錯誤，都靠 ICMP 回報。ping（echo request/reply）和 traceroute（利用 TTL exceeded）都建立在 ICMP 上。但**很多防火牆預設封鎖 ICMP**（怕被用來掃描/攻擊，Ch 18）——後果是 `ping 不通` 不代表機器掛了，可能只是 ICMP 被擋而 TCP 服務（如 web）正常。這是 debug 的重要陷阱：別只靠 ping 判斷「機器活著嗎」，要用 `nc`/`curl` 測實際的 TCP 服務（Ch 2 的分層排查）。更隱蔽的是封鎖 ICMP 會破壞 **PMTUD**（路徑 MTU 發現，下節）——導致「小封包通、大封包不通」的詭異問題（因為「需要分片」的 ICMP 訊息被擋了）。

## 分片與 MTU:大封包怎麼辦

```
封包分片（fragmentation）：封包比 MTU 大怎麼辦

  MTU = 1500（Ethernet 的 payload 上限，Ch 3）
  如果一個 IP 封包要傳 4000 bytes 的資料？
        │
  分片：把大封包切成多個小片（每片 ≤ MTU）
    4000 bytes → 切成 3 片（1480 + 1480 + 1040）
    每片有相同的 ID、不同的偏移（offset）
    接收方靠 ID + offset 重組
        │
  問題：分片有成本和風險
    - 任一片丟失 → 整個封包要重傳
    - 路由器分片消耗資源
    - 有安全風險（分片攻擊）
        │
  現代做法：避免分片（用 PMTUD 找路徑最小 MTU）
    TCP 用 MSS 協商，一開始就不送超過 MTU 的（Ch 6）
    IPv6 乾脆禁止路由器分片（Ch 38）
```

```bash
# 看你的介面 MTU
ip link | grep mtu
# eth0: ... mtu 1500 ...

# 測試 MTU：送不可分片的大封包，看哪個大小開始不通
ping -c 1 -M do -s 1472 8.8.8.8    # -M do：禁止分片，-s 1472：資料大小
#   1472 + 8(ICMP標頭) + 20(IP標頭) = 1500（剛好 MTU）→ 通
ping -c 1 -M do -s 1473 8.8.8.8    # 1473 → 總長 1501 > MTU
#   "Message too long" 或 "Frag needed" → 超過 MTU，不可分片就失敗
#   → 這是測 path MTU 的方法
```

> **分片是「封包太大」的補救，但現代網路盡量避免它——MTU 問題是最詭異的網路 bug 之一**。當 IP 封包超過 MTU（1500），要切成多片傳輸，接收方靠 ID+偏移重組。但分片有成本（任一片丟了整個要重傳）和風險，所以現代做法是**一開始就別送超過 MTU 的封包**——TCP 用 **MSS 協商**（Ch 6，握手時雙方告知「我能收的最大區段」），PMTUD（路徑 MTU 發現）找出整條路徑的最小 MTU。問題來了：**PMTUD 依賴 ICMP**（路由器用 ICMP「需要分片」訊息告知封包太大），如果路徑上有防火牆擋 ICMP（上節），PMTUD 就失效——造成經典的 **MTU 黑洞**：小封包通（不超 MTU）、大封包不通（超了但「需要分片」的 ICMP 被擋，發送方不知道要縮小，封包默默被丟）。症狀是「SSH 能連上但 `ls` 大目錄就卡住」「網頁標頭載入但內容卡住」。這在 **VPN 場景特別常見**（Ch 23，VPN 多包一層使有效 MTU 變小）——所以 VPN 設定常要手動調小 MTU。理解 MTU/分片/PMTUD，你才能 debug 這類「時通時不通」的鬼問題。

## 故意弄壞:MTU 黑洞

```bash
# 在 netns 模擬 MTU 不匹配造成的問題（Ch 0 的 netns）
# 概念演示：把介面 MTU 設小，看大封包怎麼失敗
sudo ip netns add mtutest
sudo ip netns exec mtutest ip link set lo up
sudo ip netns exec mtutest ip link set lo mtu 1000   # 把 lo 的 MTU 設成 1000

# 在 netns 裡送大封包
sudo ip netns exec mtutest ping -c1 -M do -s 1100 127.0.0.1
# ping: local error: message too long, mtu=1000
#   → 1100 bytes 資料超過 1000 MTU，禁止分片 → 失敗
#   這就是「MTU 太小但封包太大」的症狀

sudo ip netns del mtutest

# 真實世界的 MTU 黑洞 debug：
# 症狀：能連線（小封包 OK）但傳大資料就卡（大封包被默默丟）
# 排查：ping -M do -s <遞增> 找出哪個大小開始失敗 = path MTU
# 解法：降低 MTU（介面或 TCP MSS clamping，Ch 18）
```

> 這個實驗示範 MTU 不匹配的症狀——當封包大小超過路徑上某段的 MTU 且不允許分片，封包就傳不過去。真實的 MTU 黑洞更陰險：它是「**有時通有時不通**」（小封包通、大封包不通），且因為 ICMP 被擋，發送方收不到「封包太大」的回報，只看到「連線莫名卡住」。debug 方法是用 `ping -M do -s <大小>` 二分搜尋找出 path MTU，解法是降低 MTU 或在防火牆做 MSS clamping（Ch 18，強制改小 TCP 的 MSS）。這是 VPN（Ch 23）和某些網路環境的常見坑——記住「莫名其妙傳大檔卡住」要想到 MTU。

## 動手練習

1. 看 IP 封包：`tcpdump -v` 抓 ping 封包，找出 TTL、ID、協定欄位、總長度

2. 路由決策：`ip route` 看路由表，`ip route get <各種IP>`（本網段/外網/8.8.8.8），理解最長前綴匹配

3. TTL 實驗：`ping -t 1`、`ping -t 2`... 遞增 TTL，看每次封包「死」在第幾跳（traceroute 的原理）

4. ICMP 觀察：`tcpdump icmp` 抓 ping，看 echo request/reply；試 ping 一個被防火牆擋 ICMP 的主機（不通但服務正常）

5. 跑「MTU 黑洞」：用 `ping -M do -s <遞增>` 找出你到某主機的 path MTU，理解分片和 MTU 的關係

## 本章重點整理

- 網路層（IP）提供「跨網段的全球定址與轉送」；封包是逐跳轉送（每個路由器只決定下一跳，無人知道完整路徑）
- IP 標頭：來源/目標 IP（全程不變）、TTL（防迴圈）、協定欄位（指明上層 TCP/UDP/ICMP）
- 路由決策用「最長前綴匹配」：越精確的規則優先，default（0.0.0.0/0）最後用；`ip route get` 查實際路徑
- TTL 每跳減 1 到 0 丟棄（防迴圈），traceroute 巧妙利用它看路徑
- ICMP 是 IP 的診斷夥伴（ping/traceroute），但常被防火牆擋（造成 ping 不通但服務正常、MTU 黑洞）

## 自我檢核

- [ ] 能解釋「逐跳轉送」——為什麼沒有路由器知道完整路徑
- [ ] 知道路由的最長前綴匹配規則，會用 `ip route get` 看封包往哪送
- [ ] 能解釋 TTL 的作用，以及 traceroute 怎麼利用它
- [ ] 知道 ICMP 是什麼，為什麼「ping 不通」不代表服務掛了
- [ ] 理解 MTU/分片/PMTUD，能 debug MTU 黑洞（大封包不通）

## 延伸閱讀

### 書籍

- **《TCP/IP Illustrated, Volume 1》— Ch 5 (IP), Ch 8 (ICMP)** — Stevens & Fall
  - **讀哪幾章**：Ch 5（IP 封包與轉送）、Ch 8（ICMP）、Ch 10（分片）
  - **這本書的定位**：IP/ICMP 機制的權威；分片那章解釋 MTU 問題的根源
  - **前提**：Ch 2-3

### 文章

- **[Path MTU Discovery 與 MTU 黑洞](https://blog.cloudflare.com/path-mtu-discovery-in-practice/)** — Cloudflare
  - **這篇說什麼**：PMTUD 怎麼運作、為什麼會失效（ICMP 被擋）、真實的 MTU 黑洞案例
  - **讀哪裡**：整篇
  - **為什麼值得讀**：本章「MTU 黑洞」的權威實戰版，理解最詭異的網路問題之一

- **[How routing works](https://www.practicalnetworking.net/series/packet-traveling/packet-traveling/)** — Practical Networking
  - **這篇說什麼**：一個封包跨多個網段的完整旅程，每跳 MAC 怎麼換、IP 怎麼不變
  - **為什麼值得讀**：把「逐跳轉送」+「IP 不變 MAC 變」用圖講透

### 官方文件

- **[RFC 791 — Internet Protocol](https://www.rfc-editor.org/rfc/rfc791)** — IETF（1981）
  - **讀哪裡**：Section 3.1（標頭格式）、Section 3.2（分片）
  - **為什麼值得讀**：IPv4 的原始定義，至今在跑；對照本章的 IP 標頭圖

下一章深入 IP 位址本身——CIDR、子網、私有 IP，把「網段」的數學講清楚，這是設定任何網路的基礎。

→ [Ch 5 IP 定址與 CIDR](./05-ip-addressing-cidr.md)
