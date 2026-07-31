# Ch 3 — 連結層：Ethernet 與 ARP

> **目標**：理解網路最底層的「同網段傳輸」——MAC 位址是什麼、Ethernet 訊框的結構、交換器（switch）怎麼靠 MAC 轉送、以及 ARP 怎麼把 IP 位址翻譯成 MAC 位址（這是 Ch 1 旅程「步驟 2b」的細節）。這層解決「同一條網線/WiFi 上的鄰居怎麼互相找到」，是上層 IP 通訊的物理基礎，也是 ARP 欺騙等攻擊的所在。

> **環境**：Linux（ip neigh / arping）。實驗可在本機或 netns（Ch 0）做。

## 為什麼要懂連結層？

你可能想：我都用 IP 位址了，幹嘛還要懂 MAC？因為 IP 封包最終要透過**實體網路**送出，而實體網路（Ethernet/WiFi）不認 IP，只認 MAC 位址。當你的電腦要送封包給同網段的鄰居（或送給路由器），它必須先知道對方的 MAC——這就是 ARP 的工作。

理解連結層回答了一堆「為什麼」：為什麼同網段機器能直接通、跨網段要透過路由器？為什麼換了網卡 MAC 就變？ARP 欺騙（中間人攻擊）怎麼運作？為什麼公共 WiFi 危險？這層是網路的物理基礎，也是很多資安問題的根源。Ch 1 旅程裡「用 ARP 問出路由器 MAC」那一步，這章講透。

## 先建立直覺:社區內送信 vs 跨城市

```
MAC 位址 vs IP 位址（兩種定址的分工）：

  IP 位址：像「完整郵寄地址」（城市/街道/門牌）
    全球唯一定位，跨網路有意義
    93.184.216.34 在地球上是唯一的目標
        │
  MAC 位址：像「這棟樓裡的房號」
    只在「同一個區網」（同一棟樓）內有意義
    aa:bb:cc:dd:ee:ff 是某張網卡的硬體編號
        │
  送信的真相：
    跨城市（跨網段）：交給「郵局/轉運站」（路由器）
      你不需要知道對方城市的門牌，只要交給郵局
    同一棟樓（同網段）：直接送到對方房號（MAC）
        │
  → 封包在「同網段」內靠 MAC 送（連結層）
    要「跨網段」就交給路由器（網路層接手，Ch 4）
    ARP 負責「IP → MAC」的翻譯（這棟樓裡哪個房號是這個人）
```

關鍵心智：**IP 是全球定址（跨網路），MAC 是區網內定址（同網段）**。封包在同一個區網內傳輸，靠的是 MAC 位址（連結層）。要送到不同網段，就交給路由器（Ch 4）。而 ARP 的工作是「把 IP 翻譯成 MAC」——當你知道對方 IP，要在同網段找到它，得先用 ARP 問出它的 MAC。

> 連結層是 Ch 2 分層模型的最底層（TCP/IP 四層的連結層）。如果對分層還不熟，回看 [Ch 2 — OSI 與 TCP/IP 模型](./02-osi-tcpip-models.md)。MAC 是這層的定址方式。

## MAC 位址與 Ethernet 訊框

```bash
# 看你網卡的 MAC 位址
ip link
# 2: eth0: <...> link/ether aa:bb:cc:dd:ee:ff ...
#                            ↑ 這就是 MAC 位址（48 bit，6 組十六進位）

# MAC 位址結構：48 bit = 6 bytes
#   aa:bb:cc : dd:ee:ff
#   └前 3 bytes┘ └後 3 bytes┘
#   OUI（廠商代碼）  廠商自己分配的序號
#   （aa:bb:cc 能查出網卡廠商，如 Intel/Realtek）
```

```
Ethernet 訊框（frame）結構：

  ┌──────────┬──────────┬──────┬─────────────┬─────┐
  │ 目標 MAC  │ 來源 MAC  │ 類型 │   資料(payload) │ CRC │
  │ (6 bytes)│ (6 bytes)│(2B)  │  (46-1500B)  │(4B) │
  └──────────┴──────────┴──────┴─────────────┴─────┘
       │          │        │         │          │
    送給誰      誰送的   裝什麼     IP封包     錯誤檢查
                       (0x0800=IPv4 在這裡
                        0x0806=ARP)
        │
  關鍵欄位：
    目標/來源 MAC：連結層的定址（這個區網內送給誰）
    類型(EtherType)：裡面裝的是什麼（IPv4? ARP? IPv6?）
    payload：上層的資料（通常是一個 IP 封包，Ch 4）
    CRC：訊框校驗（傳輸出錯就丟棄）
        │
  MTU = payload 最大 1500 bytes（Ethernet 的限制，Ch 4 分片會用到）
```

> **MTU（Maximum Transmission Unit）= 1500 bytes 是 Ethernet 的根本限制，影響整個網路**。Ethernet 訊框的 payload 最大 1500 bytes——這意味著一個 IP 封包最多裝 1500 bytes 的資料，超過就要**分片**（fragmentation，Ch 4）。這個 1500 的數字到處影響網路：TCP 的 MSS（最大區段大小）= MTU - IP標頭 - TCP標頭 ≈ 1460、VPN 會因為多包一層而需要降低 MTU（Ch 23，否則封包過大被丟）、「MTU 黑洞」是經典的詭異網路問題（大封包不通小封包通）。為什麼是 1500？歷史原因（早期乙太網路的權衡，平衡傳輸效率和錯誤率）。MAC 位址的前 3 bytes 是 **OUI**（廠商代碼，能查出網卡是 Intel/Realtek 等），後 3 bytes 是廠商分配的序號——理論上全球唯一，但能軟體偽造（`ip link set eth0 address ...`）。EtherType 欄位告訴接收方「payload 裝的是 IPv4 還是 ARP」，這是連結層的「分用」（demultiplexing）。

## ARP:把 IP 翻譯成 MAC

ARP（位址解析協定，Address Resolution Protocol）解決「我知道 IP，怎麼找到它的 MAC」：

```
ARP 的運作（你要送封包給 192.168.1.1，但不知道它的 MAC）：

  1. 廣播詢問（ARP request）：
     你 → 整個區網廣播：「誰是 192.168.1.1？告訴我你的 MAC」
     （目標 MAC = ff:ff:ff:ff:ff:ff = 廣播，所有人都收到）
        │
  2. 目標回應（ARP reply）：
     192.168.1.1 → 你（單播）：「我是 192.168.1.1，我的 MAC 是 aa:bb...」
     （其他機器收到 request 但不是自己，忽略）
        │
  3. 你記住（ARP cache）：
     把「192.168.1.1 → aa:bb...」存進 ARP 快取
     之後送給它就直接用這個 MAC（不用每次都問）
        │
  → ARP = 區網內的「廣播問路」
    廣播問、單播答、快取結果
```

```bash
# 看你的 ARP 快取（IP → MAC 的對照表）
ip neigh
# 192.168.1.1 dev eth0 lladdr aa:bb:cc:dd:ee:ff REACHABLE
#   ↑ IP        ↑介面            ↑ MAC            ↑狀態
# 192.168.1.50 dev eth0 lladdr 11:22:33:44:55:66 STALE
#   （這些是最近通訊過的鄰居，IP→MAC 對照）

# 主動 ARP 查詢一個 IP（arping）
sudo arping -c 3 192.168.1.1
# ARP reply 192.168.1.1 is-at aa:bb:cc:dd:ee:ff

# 看 ARP 封包（抓 ARP 流量）
sudo tcpdump -i any -n arp
# ARP, Request who-has 192.168.1.1 tell 192.168.1.100   ← 廣播問
# ARP, Reply 192.168.1.1 is-at aa:bb:cc:dd:ee:ff         ← 單播答

# 清空 ARP 快取（強制重新 ARP）
sudo ip neigh flush all
```

> **ARP 是「廣播問路」——它揭示了同網段機器其實能聽到彼此的廣播**。當你要送封包給同網段的某 IP 但不知道它的 MAC，你的機器**對整個區網廣播**（目標 MAC = 全 f，所有機器都收到）問「誰是這個 IP？」，目標機器**單播回應**它的 MAC，你快取起來下次直接用。這個機制有兩個重要含義：(1) **同網段是「廣播域」**——機器能互相廣播，這是為什麼公共 WiFi 上你「看得到」其他人的存在（ARP 廣播）；(2) **ARP 沒有驗證**——任何機器都能聲稱「我是 192.168.1.1」，這就是 **ARP 欺騙/中間人攻擊**的基礎（後述「故意弄壞」）。`ip neigh`（鄰居表，就是 ARP 快取）是 debug「同網段連不上」的第一個工具——如果鄰居的 MAC 是 `FAILED` 或查不到，就是連結層（ARP）出問題，根本到不了 IP 層。

## 交換器:靠 MAC 轉送

連結層的「轉送設備」是交換器（switch），它和路由器（Ch 4）不同：

```
交換器（switch）怎麼運作：

  交換器有很多 port，每個 port 接一台機器/設備
  它維護一張「MAC 表」：哪個 MAC 在哪個 port
        │
  收到一個訊框（目標 MAC = X）：
    查 MAC 表 → X 在 port 3 → 只從 port 3 送出
    （不像 hub 那樣往所有 port 廣播，更有效率＋更安全）
        │
  MAC 表怎麼建立（自我學習）：
    收到來自 port 5 的訊框（來源 MAC = Y）
    → 學到「Y 在 port 5」，記進 MAC 表
        │
  目標 MAC 不在表裡？→ 往所有 port 廣播（flooding）
    （第一次通訊或廣播訊框）
        │
  → 交換器是「連結層（L2）設備」：只看 MAC，不看 IP
    路由器是「網路層（L3）設備」：看 IP 決定路由（Ch 4）
```

> **交換器（L2）只看 MAC、路由器（L3）只看 IP——這是連結層和網路層設備的根本區別**。交換器工作在連結層，它維護「MAC → 哪個 port」的表（透過觀察封包的來源 MAC 自我學習），收到訊框就查表從對應 port 送出。它**不懂 IP**——對它來說封包只是「有來源/目標 MAC 的訊框」。路由器工作在網路層，它看 **IP 位址**查路由表決定往哪個網段送（Ch 4）。一個典型網路：同網段機器透過交換器互連（L2），不同網段透過路由器互連（L3）。這解釋了 Ch 2 的分層——交換器是 L2 設備、路由器是 L3 設備、L4/L7 負載平衡看更上層。交換器比舊的 **hub**（集線器，往所有 port 廣播，所有人都聽得到）更有效率也更安全（只送給目標 port）——但交換器仍可能被 MAC flooding 攻擊（灌爆 MAC 表逼它退化成 hub 廣播模式）。

## 故意弄壞:ARP 欺騙（中間人攻擊原理）

ARP 沒有驗證，這是經典的中間人攻擊基礎——理解它才能防它：

```
ARP 欺騙（ARP spoofing / 中間人攻擊）原理：

  正常：受害者要送資料給路由器(192.168.1.1)
    受害者 ARP 快取：192.168.1.1 → 路由器的真 MAC
        │
  攻擊者（同網段）持續發假 ARP reply：
    「192.168.1.1 is-at 攻擊者的MAC」（騙受害者）
    「受害者IP is-at 攻擊者的MAC」（騙路由器）
        │
  結果：
    受害者把要給路由器的封包，送到攻擊者的 MAC
    攻擊者轉發給真路由器（受害者不知道）
    → 所有流量經過攻擊者（中間人，能竊聽/竄改）
        │
  → 這就是為什麼「同網段」不可信、為什麼公共 WiFi 危險
    也是為什麼要 HTTPS（Ch 11）：即使被中間人，內容也加密
```

```bash
# 觀察 ARP 欺騙的痕跡（不實際攻擊，只觀察）
# 看 ARP 快取有沒有異常（同一 MAC 對應多個 IP = 可疑）
ip neigh | sort

# 偵測 ARP 異常：如果路由器的 MAC 突然變了 → 可能被欺騙
# 正常情況路由器 MAC 應該穩定
arping -c 1 192.168.1.1        # 確認路由器當前 MAC，和平常比對

# 防禦：靜態 ARP（綁死關鍵 IP→MAC，不接受 ARP 更新）
# sudo ip neigh add 192.168.1.1 lladdr <真MAC> dev eth0 nud permanent
#   （把閘道的 MAC 設成靜態，攻擊者的假 ARP 就無效）
```

> **ARP 欺騙是「同網段不可信」的根本原因，也是 HTTPS 為何不可或缺的論證**。ARP 協定設計於信任的年代——它**沒有任何驗證**，任何機器都能發 ARP reply 聲稱「某 IP 是我的 MAC」。攻擊者在同網段持續發假 ARP，騙受害者把「給路由器的封包」送到攻擊者，攻擊者再轉發給真路由器——形成**中間人**，能竊聽和竄改所有經過的流量。這就是為什麼**公共 WiFi 危險**（同網段有不認識的人）、為什麼**同網段不該被信任**。防禦：靜態 ARP（綁死關鍵 IP→MAC）、交換器的 Dynamic ARP Inspection、或從根本上——**用 HTTPS（Ch 11）**：即使流量被中間人攔截，TLS 加密讓攻擊者看不到也改不了內容（除非他能偽造憑證，而憑證有 CA 驗證）。ARP 欺騙的存在是「為什麼我們需要端到端加密」的最佳教材——你無法信任底層網路，所以要在上層加密。

## 進階:鄰居狀態機與 IPv6 的 NDP

```bash
# ip neigh 的狀態（ARP 快取的生命週期）
ip neigh
# REACHABLE：最近確認可達（信任）
# STALE：    快取存在但有點舊（下次用時會驗證）
# DELAY/PROBE：正在驗證中
# FAILED：   ARP 失敗（鄰居不回應 → 連結層不通！）
#   → FAILED 是「同網段連不上」的明確信號（Ch 16 debug）

# IPv6 沒有 ARP，用 NDP（鄰居發現協定，Ch 38）
# NDP 用 ICMPv6 做類似 ARP 的事（但有改進）
ip -6 neigh                    # IPv6 的鄰居表
```

> **`ip neigh` 的狀態是 debug 連結層的關鍵信號，特別是 `FAILED`**。ARP 快取有生命週期：`REACHABLE`（最近確認可達）→ `STALE`（有點舊，下次用會驗證）→ `FAILED`（ARP 問了沒人回）。當你看到某鄰居 `FAILED`，代表**連結層根本不通**——你的機器廣播問「誰是這個 IP」但沒人回應，封包連 MAC 都填不上，更別說送到 IP 層。這是「同網段機器連不上」的明確診斷（可能對方關機、IP 設錯、或不在同網段）。IPv6（Ch 38）廢除了 ARP，改用 **NDP**（鄰居發現協定，基於 ICMPv6）做同樣的事但有安全改進（如 SEND）。理解鄰居狀態機，你 debug「ping 不到同網段機器」時就知道先看 `ip neigh`——如果是 FAILED，問題在連結層，不用往上查 IP/TCP。

## 動手練習

1. 看你的 MAC 和鄰居：`ip link`（你的 MAC）、`ip neigh`（鄰居 IP→MAC 表），用 OUI 查網卡廠商

2. 抓 ARP 封包：`sudo tcpdump -i any arp`，然後 `ping` 一個沒通訊過的同網段 IP（或 `ip neigh flush all` 後 ping 閘道），看 ARP request/reply

3. 主動 ARP：`sudo arping -c 3 <閘道IP>`，看回應的 MAC，和 `ip neigh` 比對

4. 觀察狀態機：`ip neigh flush all` 清快取，立刻 `ip neigh`（空/STALE），ping 閘道後再看（REACHABLE）

5. 理解 ARP 欺騙：讀「故意弄壞」那節，在 netns 拓樸裡（Ch 22 後）能實際模擬中間人

## 本章重點整理

- 連結層解決「同網段傳輸」：用 MAC 位址（48 bit，只在同區網有意義），IP 是跨網路定址
- Ethernet 訊框：目標/來源 MAC + EtherType（裝什麼）+ payload（IP 封包）+ CRC；MTU=1500 影響整個網路
- ARP 把 IP 翻譯成 MAC：廣播問、單播答、快取結果（ip neigh）；同網段是廣播域
- 交換器（L2）只看 MAC 轉送、路由器（L3）看 IP 路由——連結層 vs 網路層設備的根本區別
- ARP 無驗證 → ARP 欺騙（中間人攻擊）→ 同網段不可信 → 需要 HTTPS 端到端加密

## 自我檢核

- [ ] 能解釋 MAC 和 IP 的區別，以及為什麼需要兩種定址
- [ ] 知道 ARP 怎麼把 IP 翻譯成 MAC（廣播問、單播答、快取）
- [ ] 能說出交換器（L2）和路由器（L3）的根本區別
- [ ] 理解 ARP 欺騙的原理，以及它為什麼證明「需要 HTTPS」
- [ ] 知道 `ip neigh` 的 FAILED 狀態代表連結層不通

## 延伸閱讀

### 書籍

- **《TCP/IP Illustrated, Volume 1》— Ch 4 (ARP)** — Stevens & Fall
  - **讀哪幾章**：Ch 4（ARP 完整機制，含 gratuitous ARP、proxy ARP）；Ch 3（連結層/Ethernet）
  - **這本書的定位**：ARP 機制的權威；用真實封包講 ARP 的每種情況
  - **前提**：Ch 2

### 文章

- **[How ARP works](https://www.practicalnetworking.net/series/arp/traditional-arp/)** — Practical Networking
  - **這篇說什麼**：ARP 的完整圖解，含跨網段時 ARP 怎麼運作（問的是閘道的 MAC 不是目標的）
  - **讀哪裡**：traditional ARP 那篇
  - **為什麼值得讀**：把「同網段 ARP」和「跨網段時 ARP 問閘道」的關鍵差別講透

- **[ARP spoofing 詳解](https://www.veracode.com/security/arp-spoofing)** — Veracode
  - **這篇說什麼**：ARP 欺騙攻擊的原理和防禦
  - **為什麼值得讀**：本章「故意弄壞」的延伸，理解中間人攻擊

### 官方文件

- **[RFC 826 — Address Resolution Protocol](https://www.rfc-editor.org/rfc/rfc826)** — IETF（1982）
  - **讀哪裡**：整篇（很短），ARP 的原始定義
  - **為什麼值得讀**：ARP 至今幾乎沒變，這篇 1982 的 RFC 就是現在在跑的；體會「協定簡單到四十年不用改」

下一章往上一層——網路層的 IP 和 ICMP，看封包怎麼「跨網段」靠 IP 位址和路由送到全世界。

→ [Ch 4 網路層：IP 與 ICMP](./04-network-layer-ip-icmp.md)
