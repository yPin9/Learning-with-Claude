# Ch 16 — traceroute / mtr / ping

> **目標**：掌握路徑與連通診斷工具——ping（測連通與延遲）、traceroute（看封包經過哪些路由器，Ch 4 的 TTL 妙用）、mtr（traceroute + ping 的即時版，找丟包點）。Ch 4 講了 TTL/ICMP 原理，這章把它落到「怎麼診斷網路慢、丟包、不通在哪一段」。這些是 debug「連線品質問題」的核心工具。

> **環境**：Linux（ping/traceroute/mtr）。部分需 root 或特定權限。

## 為什麼需要路徑診斷工具？

「網站很慢」「連線時好時壞」「某個服務連不上」——這些問題往往不在你的機器，而在**中間的某段網路**。封包要經過十幾個路由器才到目標（Ch 4 的逐跳轉送），任何一段出問題（某路由器壅塞、某段線路丟包）都會影響你，但你看不到是哪一段。

ping/traceroute/mtr 讓你看到路徑——ping 測「能不能通、多快」，traceroute 看「經過哪些路由器」，mtr 持續監測「哪一跳在丟包」。它們把 Ch 4 的 TTL/ICMP 原理變成診斷工具。當問題在網路中間時，這些是唯一能定位「問題在哪一段」的工具。

## ping:測連通與延遲

```bash
# 基本：測連通和延遲（RTT）
ping example.com
# 64 bytes from 93.184.216.34: icmp_seq=1 ttl=56 time=12.3 ms
#   icmp_seq：序號（看有沒有丟包，序號跳號 = 丟了）
#   ttl=56：回來的 TTL（Ch 4，反推經過幾跳）
#   time=12.3 ms：往返時間（RTT，延遲）

# 常用選項
ping -c 4 example.com            # 送 4 個就停（-c count）
ping -i 0.2 example.com          # 每 0.2 秒一個（-i interval，更密集）
ping -s 1472 example.com         # 指定封包大小（測 MTU，Ch 4）
ping -M do -s 1472 example.com   # 禁止分片（找 path MTU，Ch 4）
ping6 example.com                # IPv6 ping

# 看統計（Ctrl-C 後）
# 4 packets transmitted, 4 received, 0% packet loss   ← 丟包率！
# rtt min/avg/max/mdev = 11.8/12.3/13.1/0.5 ms        ← 延遲統計
#   mdev（抖動 jitter）大 = 延遲不穩定（影響即時應用）
```

> **ping 的三個關鍵指標：丟包率、RTT（延遲）、mdev（抖動）——它們各指向不同的網路問題**。**丟包率**（packet loss）：0% 正常，持續丟包代表線路品質差或壅塞（即使能連，體驗也差）。**RTT**（往返延遲）：同國通常 <50ms、跨國 100-300ms——RTT 高影響所有互動（網頁、SSH、遊戲）。**mdev/抖動**（jitter）：延遲的波動，抖動大代表網路不穩定，對即時應用（語音/視訊/遊戲）特別有害（即使平均延遲低，忽快忽慢也卡）。記住 ping 用 ICMP（Ch 4），而**很多伺服器/防火牆擋 ICMP**（Ch 4）——所以 **ping 不通不代表服務掛了**（可能只是 ICMP 被擋，TCP 服務正常），要用 `nc`/`curl` 測實際服務（Ch 17）。`ping -M do -s <大小>` 測 path MTU（Ch 4 的 MTU 黑洞 debug）。ping 是最基本的「機器活著嗎、網路通嗎、多快」的工具，但記住它的 ICMP 限制。

## traceroute:看封包的路徑

```bash
# traceroute：看封包經過哪些路由器（Ch 4 的 TTL 妙用）
traceroute example.com
#  1  192.168.1.1 (router)        1.2 ms      ← 第 1 跳：你的路由器
#  2  10.x.x.x (ISP)              5.3 ms      ← 第 2 跳：ISP
#  3  ...                         12.1 ms
#  ...
#  12 93.184.216.34               45.2 ms     ← 到達目標
#   每一行是一跳（一個路由器），顯示它的 IP 和延遲

# 怎麼運作（Ch 4）：送 TTL=1,2,3... 的封包
#   TTL=1 → 第 1 個路由器回 ICMP「TTL exceeded」（暴露它的 IP）
#   TTL=2 → 第 2 個路由器回應... 逐步點亮路徑

# 選項
traceroute -n example.com        # 不解析域名（-n，快）
traceroute -I example.com        # 用 ICMP（預設用 UDP，有些路徑要 -I）
traceroute -T -p 443 example.com # 用 TCP SYN 到 443（繞過擋 UDP/ICMP 的防火牆）

# 讀 traceroute：
#  * * *  → 那一跳沒回應（路由器不回 ICMP，或防火牆擋）—— 常見，不一定是問題
#  延遲突然暴增 → 那一段可能壅塞或繞遠路
#  停在某跳之後全是 * → 可能那裡斷了（但也可能只是後面都不回 ICMP）
```

> **traceroute 用 Ch 4 的 TTL 妙用「點亮」路徑，但讀它要小心——`* * *` 不一定是問題**。traceroute 送遞增 TTL 的封包（TTL=1,2,3…），每個路由器在 TTL 用完時回 ICMP「TTL exceeded」暴露自己的 IP，從而「點亮」整條路徑（Ch 4）。讀 traceroute 的陷阱：**`* * *`（某跳沒回應）很常見且不一定是問題**——很多路由器設定「不回 ICMP TTL exceeded」（安全/效能考量），所以那一跳顯示 `*`，但封包**還是穿過了它**（後面的跳有回應就證明）。只有「某跳之後**全部**都是 `*` 直到結束」才可能是真的斷在那裡（但也可能只是後面的路由器都不回 ICMP）。**延遲突然暴增**的那一跳可能是壅塞點或繞遠路（如封包繞到國外再回來）。注意 traceroute 預設用 UDP（有些防火牆擋），`-I`（ICMP）或 `-T -p 443`（TCP SYN 到 443，偽裝成正常連線）能繞過某些過濾——當預設 traceroute 卡住時換這些試。traceroute 適合「一次性看路徑」，但要持續監測丟包點，mtr 更好（下節）。

## mtr:traceroute + ping 的即時版

```bash
# mtr：持續監測每一跳的延遲和丟包（traceroute + ping 結合）
mtr example.com
# 即時更新的表格：
# Host                  Loss%   Snt   Last   Avg  Best  Wrst StDev
# 1. 192.168.1.1         0.0%    10    1.2   1.3   1.1   1.5   0.1
# 2. 10.x.x.x            0.0%    10    5.3   5.5   5.1   6.0   0.3
# 3. ...                 0.0%    10   12.1  12.3  ...
# 8. some-router        20.0%    10   45.2  48.1  ...   ← 這一跳開始丟包！
# ...
#   Loss%：每一跳的丟包率（找出「哪一跳開始丟」）
#   Avg/Best/Wrst：延遲統計

mtr -r -c 100 example.com        # 報告模式（跑 100 次後出報告，-r report）
mtr -n example.com               # 不解析域名（快）
mtr -T -P 443 example.com        # 用 TCP 到 443（繞過 ICMP/UDP 過濾）
```

```
怎麼用 mtr 找「丟包在哪一段」：

  正常：所有跳 Loss% = 0
        │
  情況 A：從第 N 跳開始都丟包
    → 第 N 跳之後的網路有問題（那一段品質差）
        │
  情況 B：只有「中間某一跳」丟包，但「最後一跳」不丟
    → 那個中間路由器「不回 ICMP」但「正常轉發」
    → 不是真的丟包！（路由器限制 ICMP 回應，但封包有過）
    → 看「最後一跳（目標）」的 Loss% 才是真的丟包率
        │
  → 關鍵：看「目標那一跳」的 Loss%
    中間跳的 Loss% 可能是「路由器不回 ICMP」的假象
```

> **mtr 是找「丟包在哪一段」的最佳工具，但要看「目標跳」的丟包率，別被中間跳的假象騙**。mtr 結合 traceroute（看路徑）和 ping（持續測每跳的延遲/丟包），即時更新表格——你能看到**每一跳的丟包率**，定位「網路品質從哪裡開始變差」。但有個關鍵陷阱：**中間某跳的 Loss% 高，不一定代表真的丟包**——很多路由器「限制回 ICMP 的速率」（防止被 traceroute/ping 轟炸），所以對 mtr 的探測回應不全，顯示假的高 Loss%，但它**正常轉發**經過的封包。判斷真假：**看「目標那一跳（最後一行）」的 Loss%**——如果中間跳丟包但最後一跳 0%，那中間的是假象（封包都到了目標）；如果**從第 N 跳開始一路到目標都丟包**，那才是真的（第 N 跳那段網路有問題）。所以讀 mtr 的原則：關注「丟包是否一路延續到目標」，而非單看某中間跳。`mtr -r -c 100`（跑 100 次出報告）適合留證據（回報給 ISP「你們這段在丟包」）。`-T -P 443` 用 TCP 繞過 ICMP 限制，數據更可信。mtr 是診斷「連線時好時壞、丟包」這類最惱人問題的利器。

## 故意弄壞:診斷不同的網路問題

```bash
# 用 ping/traceroute/mtr 診斷不同症狀

# 症狀 1：完全不通
ping -c2 target.com
#   100% loss + traceroute 停在某跳 → 路由斷了或目標掛了
#   但記得：可能只是 ICMP 被擋，用 nc 測 TCP（Ch 17）

# 症狀 2：通但很慢
ping -c4 target.com               # RTT 高嗎？
traceroute target.com             # 哪一跳開始延遲暴增？
#   → 定位延遲在哪一段（你的網路？ISP？跨國段？目標附近？）

# 症狀 3：時好時壞（最惱人）
mtr -c 100 target.com             # 持續監測，找「哪一跳間歇丟包」
#   看目標跳的 Loss% —— 持續丟包 = 那段品質差

# 症狀 4：只有大封包有問題（MTU，Ch 4）
ping -c2 -M do -s 1472 target.com  # 大封包通嗎？
ping -c2 -s 100 target.com         # 小封包通嗎？
#   小通大不通 → MTU 黑洞（Ch 4）

# 綜合判斷：本機問題 vs 中間網路 vs 目標問題
traceroute target.com
#   第 1 跳（你的路由器）就不通 → 本機/區網問題
#   中間某段延遲暴增/丟包 → ISP 或跨國段問題（你無能為力，回報 ISP）
#   只有最後幾跳有問題 → 目標附近或目標本身問題
```

> **ping/traceroute/mtr 的綜合運用能定位「問題在你、在中間、還是在目標」——這決定你能不能解、該找誰**。診斷流程：先 `ping`（通不通、多慢、丟不丟包），再 `traceroute`（路徑哪裡異常），持續性問題用 `mtr`（哪一跳間歇丟包）。關鍵是**定位責任段**：如果 traceroute **第 1 跳（你的路由器）就不通**——是你的本機/區網問題（你能修：檢查網線/WiFi/路由器）；**中間某段（ISP/跨國）延遲暴增或丟包**——是中間網路問題（你無能為力，只能回報 ISP 或等它恢復，附上 mtr 報告當證據）；**只有最後幾跳/目標有問題**——是目標伺服器附近或目標本身的問題（連絡服務方）。這個「定位責任段」很重要——它告訴你「這問題是不是我能解的」。很多「網站慢」其實是中間某段跨國線路壅塞（尤其晚上尖峰），你改本機設定沒用。配合 Ch 4 的 MTU 診斷（小封包通大封包不通=MTU 黑洞）。這些工具加上 Ch 14 的抓封包，組成 debug 網路的完整武器庫，練習 B 會綜合運用。

## 動手練習

1. ping 基礎：ping 國內外不同網站，比較 RTT、丟包率、抖動（mdev），理解延遲和距離的關係

2. traceroute 看路徑：traceroute 一個國外網站，數經過幾跳、看哪裡延遲暴增（跨國段）、理解 `*` 的意義

3. mtr 找丟包：`mtr -c 50` 一個網站，看每跳 Loss%，特別看目標跳，理解中間跳丟包的真假

4. MTU 診斷：`ping -M do -s` 遞增大小找 path MTU，對照 Ch 4

5. 跑「故意弄壞」：對一個慢/不穩的連線，用三個工具綜合判斷問題在你/中間/目標

## 本章重點整理

- ping 測連通/延遲/丟包：關鍵指標 RTT（延遲）、packet loss（丟包率）、mdev（抖動）；ICMP 常被擋（ping 不通≠服務掛）
- traceroute 用 TTL 妙用點亮路徑；`* * *` 常見且不一定是問題（路由器不回 ICMP）；只有「之後全 *」才可能真斷
- mtr 是找丟包點的利器：看每跳 Loss%，但要看「目標跳」的（中間跳丟包可能是 ICMP 限速假象）
- 綜合運用定位責任段：第 1 跳問題=你的、中間段=ISP/跨國、最後幾跳=目標——決定你能不能解
- `-T -p 443`/`-I` 繞過擋 UDP/ICMP 的過濾；小封包通大封包不通=MTU 黑洞（Ch 4）

## 自我檢核

- [ ] 能用 ping 的三個指標（RTT/丟包/抖動）判斷網路品質
- [ ] 理解 traceroute 怎麼運作，會讀它的輸出（特別是 `*` 的意義）
- [ ] 會用 mtr 找丟包點，知道為什麼要看「目標跳」的 Loss%
- [ ] 能綜合三個工具定位問題在你/中間/目標
- [ ] 知道 ICMP 被擋的影響，何時改用 TCP 模式（-T）

## 延伸閱讀

### 文章

- **[How traceroute works](https://www.cloudflare.com/learning/network-layer/what-is-traceroute/)** — Cloudflare
  - **這篇說什麼**：traceroute 的 TTL 原理和輸出解讀
  - **讀哪裡**：整篇
  - **為什麼值得讀**：本章 traceroute 那節的視覺化版

- **[mtr 教學與解讀](https://www.linode.com/docs/guides/diagnosing-network-issues-with-mtr/)** — Linode
  - **這篇說什麼**：用 mtr 診斷網路問題，特別是怎麼正確解讀丟包
  - **讀哪裡**：解讀那節（中間跳丟包的真假）
  - **為什麼值得讀**：本章「mtr 找丟包」的權威版，破除「中間跳丟包」的誤解

### 書籍

- **《TCP/IP Illustrated, Volume 1》— Ch 8 (ICMP)** — Stevens & Fall
  - **讀哪幾章**：Ch 8（ICMP，含 traceroute/ping 的底層）
  - **這本書的定位**：traceroute/ping 底層 ICMP 機制的權威
  - **前提**：Ch 4

下一章是工具章的最後一站——nmap/netcat/curl，掃描端口、萬用 TCP 工具、HTTP 客戶端，這些是探測和測試服務的利器。

→ [Ch 17 nmap / netcat / curl](./17-nmap-netcat-curl.md)
