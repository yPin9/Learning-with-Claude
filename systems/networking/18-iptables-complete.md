# Ch 18 — iptables 完整

> **目標**：把 iptables 講透——netfilter 框架（封包在 kernel 怎麼被處理）、五個 chain（封包經過的檢查點）、表（filter/nat/mangle）、規則的組成、做防火牆和 NAT（Ch 8 的實作）、以及 DROP vs REJECT 的選擇。iptables 是 Linux 防火牆的經典工具，也是理解 VPN（Ch 23）、容器網路（Ch 37）、NAT（Ch 8）的關鍵。雖然 nftables（Ch 19）是新標準，但 iptables 仍無所不在。

> **環境**：Linux（iptables）。實驗用 netns 安全測試（弄壞了刪掉重來）。

## 為什麼要懂 iptables？

防火牆決定「哪些封包能進、能出、能轉發」——這是伺服器安全的第一道防線（Ch 35 會用它加固 VPS）。iptables 是 Linux 控制封包流的經典工具，它不只做防火牆，還做 NAT（Ch 8 的 MASQUERADE 就是 iptables）、流量改寫、轉發控制。

理解 iptables 回答了核心問題：封包進入 Linux 後怎麼被處理？防火牆規則怎麼決定 accept/drop？NAT 怎麼實作？為什麼有時「明明服務開了卻連不上」（防火牆擋了）？這些是 Part 8（VPS 安全）、Part 6（VPN 的封包轉發）、Part 9（容器網路）的基礎。雖然 nftables（Ch 19）是新標準，但 iptables 在現存系統、Docker、無數教學裡無所不在——必須懂。

## 先建立直覺:封包通過的安檢站

```
netfilter：封包在 Linux kernel 裡經過的「安檢站」

  封包進入 Linux → 經過一系列「檢查點」（chain）
  每個檢查點有一串「規則」（rule）
  封包逐條比對規則 → 符合就執行動作（ACCEPT/DROP/...）
        │
  五個檢查點（chain），對應封包的不同階段：
        │
  封包進來 ──▶ PREROUTING ──▶ [路由決策]
                                  │
              這是要給「本機」的？     是給「別人」的（轉發）？
                  ▼                        ▼
              INPUT ──▶ 本機程式         FORWARD
                          │                  │
                        本機程式發出         │
                          ▼                  │
                       OUTPUT ──▶ POSTROUTING ◀──┘ ──▶ 封包出去
        │
  → 封包經過哪些 chain，看它是「進本機」「出本機」還是「路過（轉發）」
    在對應的 chain 設規則 = 控制那類封包
```

關鍵心智：netfilter 是封包在 kernel 裡經過的「安檢站」系統。封包經過一系列 **chain**（檢查點），每個 chain 有一串**規則**，封包逐條比對，符合就執行動作。五個 chain 對應封包的不同階段：給本機的走 INPUT、本機發出的走 OUTPUT、路過（轉發）的走 FORWARD，加上路由前的 PREROUTING 和路由後的 POSTROUTING。

> iptables 操作的是 Ch 8 的 NAT、Ch 4 的封包轉發。MASQUERADE（Ch 8 讓內網上網的動作）就是 iptables 的 nat 表規則。如果對 NAT 不熟，回看 [Ch 8](./08-nat-explained.md)。

## 五個 chain 與三個表

```
iptables 的核心結構：表（table）× 鏈（chain）

  三個常用的表（每個表管不同的事）：
    filter ── 防火牆（accept/drop，最常用）
    nat    ── 位址轉換（NAT/MASQUERADE/port forwarding，Ch 8）
    mangle ── 改封包欄位（TTL/TOS 等，進階）
        │
  五個鏈（chain，封包經過的點）：
    PREROUTING  ── 剛進來，路由決策前（nat/mangle 用）
    INPUT       ── 要給本機的（filter 防火牆主場）
    FORWARD     ── 路過/轉發的（路由器/VPN/容器用）
    OUTPUT      ── 本機發出的
    POSTROUTING ── 要出去了，路由後（nat MASQUERADE 用）
        │
  常見組合：
    filter 表的 INPUT 鏈 → 「誰能連我」（伺服器防火牆主場）
    filter 表的 FORWARD 鏈 → 「能轉發什麼」（路由器/VPN）
    nat 表的 POSTROUTING 鏈 → MASQUERADE（讓內網上網，Ch 8）
    nat 表的 PREROUTING 鏈 → port forwarding（外網連內網服務）
```

```bash
# 看當前規則
sudo iptables -L -n -v               # filter 表（預設），-n 數字 -v 詳細
sudo iptables -t nat -L -n -v        # nat 表
sudo iptables -S                     # 以「命令形式」列出（方便複製/理解）

# 規則的計數器（-v 顯示）：看每條規則「命中幾次」（debug 用）
# pkts bytes target ... → 這條規則匹配了多少封包
```

> **「表 × 鏈」的組合決定一條規則「在封包旅程的哪個點、做什麼類型的事」——這是 iptables 的核心心智模型**。**表**決定「做什麼類型的事」：`filter`（防火牆 accept/drop）、`nat`（位址轉換）、`mangle`（改封包）。**鏈**決定「在封包旅程的哪個點」：INPUT（給本機的）、OUTPUT（本機發的）、FORWARD（路過的）、PRE/POSTROUTING（路由前後）。組合起來：要做「伺服器防火牆」（控制誰能連我）→ `filter` 表的 `INPUT` 鏈；要做「NAT 讓內網上網」（Ch 8）→ `nat` 表的 `POSTROUTING` 鏈（MASQUERADE）；要做「port forwarding」（外網連內網服務）→ `nat` 表的 `PREROUTING` 鏈。理解這個二維結構（表 × 鏈），你看任何 iptables 規則就知道「它在哪個點做什麼」。`iptables -L -n -v` 看規則，`-v` 的計數器（每條規則命中幾次）是 debug 利器——能看出「封包有沒有命中這條規則」。

## filter 表:做防火牆

```bash
# === 基本防火牆規則（filter 表的 INPUT 鏈）===
# 規則的組成：-A <鏈> <匹配條件> -j <動作>

# 允許特定 port（如 SSH/HTTP）
sudo iptables -A INPUT -p tcp --dport 22 -j ACCEPT    # 允許 SSH
sudo iptables -A INPUT -p tcp --dport 80 -j ACCEPT    # 允許 HTTP
sudo iptables -A INPUT -p tcp --dport 443 -j ACCEPT   # 允許 HTTPS

# 允許特定來源 IP
sudo iptables -A INPUT -s 192.168.1.0/24 -j ACCEPT    # 允許整個內網
sudo iptables -A INPUT -s 1.2.3.4 -p tcp --dport 22 -j ACCEPT  # 只允許這 IP 連 SSH

# 允許已建立的連線（重要！否則回應封包被擋）
sudo iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
# 允許 loopback（本機自己）
sudo iptables -A INPUT -i lo -j ACCEPT

# 預設策略：拒絕其他（白名單模式，安全）
sudo iptables -P INPUT DROP          # 預設 DROP（沒被明確允許的都丟）

# === 動作（-j target）===
# ACCEPT：放行
# DROP：靜默丟棄（不回應 → 對方 timeout，Ch 6）
# REJECT：拒絕並回應（回 RST/ICMP → 對方 refused，Ch 6）
# LOG：記錄（debug，不終止比對）
```

```
一個典型的伺服器防火牆規則集（白名單模式）：

  1. 允許 loopback（本機自己）：-i lo -j ACCEPT
  2. 允許已建立的連線：-m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
  3. 允許 SSH（管理用）：-p tcp --dport 22 -j ACCEPT
  4. 允許 HTTP/HTTPS：-p tcp --dport 80,443 -j ACCEPT
  5. 預設拒絕其他：-P INPUT DROP
        │
  → 白名單：只開明確需要的，其他全擋（Ch 35 安全原則）
    順序重要！規則由上而下比對，第一個符合的生效
    （所以「允許已建立連線」要放前面）
```

> **「允許已建立的連線」（conntrack ESTABLISHED）是防火牆規則最容易漏卻最關鍵的一條**。防火牆是**有狀態的**（stateful）——它追蹤連線狀態（用 conntrack，Ch 8 的連線追蹤）。當你連出去（如 `curl` 一個網站），回應封包要能進來——但回應封包的目標 port 是隨機的（不是 80/443），如果你只開了 80/443 的 INPUT，回應會被擋（你連得出去但收不到回應）。解法是 `-m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT`——「已經建立的連線的回應封包，放行」。這條規則讓「本機主動發起的連線」的回應能回來，而「外部主動發起的新連線」仍受其他規則控制。這是**有狀態防火牆**的精髓——你不用為每個出向連線開對應的入向規則，conntrack 自動追蹤。**規則順序也很關鍵**——iptables 由上而下比對，第一個符合的生效（所以 ESTABLISHED 要放前面，常用規則放前面效率高）。**預設策略 DROP**（`-P INPUT DROP`）配合明確的 ACCEPT 規則 = 白名單模式（只開需要的，Ch 35 的安全原則）。漏了 ESTABLISHED 這條，是新手設防火牆「設完就斷網」的頭號原因。

## DROP vs REJECT

```
DROP vs REJECT（兩種「拒絕」，效果不同）：

  DROP：靜默丟棄（不回應任何東西）
    對方：等到 timeout（Ch 6）—— 不知道發生什麼
    優點：隱蔽（掃描者不知道 port 存不存在/被擋）
    缺點：對方要等 timeout（慢），合法使用者體驗差
        │
  REJECT：拒絕並回應（回 RST 或 ICMP unreachable）
    對方：立刻收到「connection refused」（Ch 6）
    優點：對方立刻知道（快），符合「禮貌」
    缺點：暴露「這裡有東西在擋」（給掃描者資訊）
        │
  → 對「公網的攻擊面」用 DROP（隱蔽，讓掃描者浪費時間）
    對「內網/已知來源」用 REJECT（快速回應，友善）
    這對應 Ch 6 的 timeout vs refused，和練習 B 的問題 3
```

```bash
# DROP（靜默，對外用）
sudo iptables -A INPUT -p tcp --dport 23 -j DROP        # telnet：靜默丟棄

# REJECT（回應，內網用）
sudo iptables -A INPUT -s 192.168.1.0/24 -p tcp --dport 3306 -j REJECT  # 內網連 MySQL：明確拒絕

# 驗證 DROP vs REJECT 的差別（對應練習 B）
# DROP 的 port → nc timeout
# REJECT 的 port → nc refused（快）
```

> **DROP（靜默，造成 timeout）vs REJECT（回應，造成 refused）是有意的安全選擇——這正是練習 B 問題 3 的根源**。**DROP** 靜默丟棄封包，對方等到 timeout（Ch 6）——這對**公網攻擊面**有利（掃描者不知道 port 是「被擋」還是「主機不存在」，浪費他的時間，nmap 顯示 filtered，Ch 17）。**REJECT** 主動回應拒絕（RST 或 ICMP unreachable），對方立刻收到 refused——這對**內網/合法使用者**友善（不用等 timeout，立刻知道）。所以慣例：**公網用 DROP**（隱蔽防禦），**內網用 REJECT**（快速友善）。這完美對應 Ch 6 的「refused vs timeout」和練習 B 問題 3——當你 debug「為什麼連線 timeout」，可能就是對方防火牆 DROP 了你；「為什麼 refused」可能是 REJECT 或服務沒開。理解這個選擇，你既能正確設防火牆，也能反推「對方為什麼這樣回應我」。

## nat 表:NAT 與 port forwarding

```bash
# === MASQUERADE：讓內網上網（Ch 8 的核心，VPN 也用）===
# 把來源 IP 改成出口介面的 IP（內網 → 路由器公網 IP）
sudo iptables -t nat -A POSTROUTING -s 192.168.1.0/24 -o eth0 -j MASQUERADE
# 配合開啟 IP forwarding（Ch 0）：
sudo sysctl net.ipv4.ip_forward=1
# → 內網機器現在能透過這台機器上網（這台當路由器/NAT）
# → 這正是 VPN（Ch 23）和容器（Ch 37）讓流量出網的機制

# === DNAT / port forwarding：外網連內網服務（Ch 8）===
# 把連到「本機公網 IP:80」的封包轉給「內網 192.168.1.10:80」
sudo iptables -t nat -A PREROUTING -p tcp --dport 80 -j DNAT --to-destination 192.168.1.10:80
# → 外網連這台的 80 → 轉給內網的 web 伺服器

# === SNAT：固定來源 IP（MASQUERADE 的靜態版）===
sudo iptables -t nat -A POSTROUTING -s 192.168.1.0/24 -j SNAT --to-source 1.2.3.4
# MASQUERADE 自動用出口 IP，SNAT 指定固定 IP（適合固定公網 IP）

# === MSS clamping：解決 VPN 的 MTU 問題（Ch 4）===
sudo iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
# 強制把 TCP MSS 改小，避免 MTU 黑洞（Ch 4，VPN 常用）
```

> **iptables 的 nat 表是 Ch 8 NAT 的實作——MASQUERADE 是 VPN 和容器讓流量出網的關鍵動作**。`iptables -t nat -A POSTROUTING ... -j MASQUERADE` 就是 Ch 8 講的 NAT——把內網封包的來源 IP 改成出口介面的 IP，讓內網能共用公網 IP 上網。這條規則（配合 `ip_forward=1`，Ch 0）讓一台 Linux 變成 NAT 路由器——**這正是 WireGuard VPN（Ch 24）讓 VPN 客戶端的流量從伺服器出網、Docker（Ch 37）讓容器上網的機制**。`DNAT`（PREROUTING）做 port forwarding（外網連內網服務，Ch 8）。`SNAT` 是 MASQUERADE 的靜態版（指定固定來源 IP）。還有一個 VPN 必備的技巧 **MSS clamping**（`TCPMSS --clamp-mss-to-pmtu`）——它強制把 TCP 的 MSS 改小，解決 VPN 多包一層造成的 MTU 黑洞（Ch 4，VPN 場景超常見）。這些 nat 規則是 Part 6（VPN）和 Part 9（容器網路）的底層——當你架 WireGuard 後發現「VPN 連上了但上不了網」，十之八九是少了 MASQUERADE 或 ip_forward；「傳大檔案卡住」可能要 MSS clamping。理解 iptables 的 nat 表，你就掌握了 VPN/容器網路的封包轉發核心。

## 故意弄壞:在 netns 安全測試防火牆

```bash
# 在 netns 測試防火牆規則（弄壞了刪掉重來，不影響本機，Ch 0）
sudo ip netns add fwtest
sudo ip netns exec fwtest ip link set lo up

# 在 netns 裡設規則並驗證
# 1. 預設策略 DROP 會擋掉一切（包括 loopback！常見錯誤）
sudo ip netns exec fwtest iptables -P INPUT DROP
sudo ip netns exec fwtest ping -c1 127.0.0.1   # 不通！（連 loopback 都被 DROP）
# → 教訓：設 -P INPUT DROP 前，務必先允許 loopback 和 ESTABLISHED！

# 2. 補上必要的允許
sudo ip netns exec fwtest iptables -A INPUT -i lo -j ACCEPT
sudo ip netns exec fwtest ping -c1 127.0.0.1   # 通了

sudo ip netns del fwtest

# 真實世界的災難：遠端設 -P INPUT DROP 但忘了允許 SSH → 把自己鎖在外面！
# 防範：
#   1. 先 ACCEPT SSH/loopback/ESTABLISHED，「最後」才設 -P DROP
#   2. 用 iptables-apply（設定後若沒確認自動回滾，防鎖死）
#   3. 在 console/救援模式有後路
```

> **設 `-P INPUT DROP` 前沒先允許 SSH/loopback/ESTABLISHED，會把自己鎖在伺服器外——這是運維的經典災難**。`-P INPUT DROP`（預設拒絕）很安全，但它擋掉**一切**沒被明確允許的——包括你的 SSH 連線和 loopback（本機自己）！如果你 SSH 到遠端伺服器，設了 `-P INPUT DROP` 但忘了先 `ACCEPT` SSH（port 22），下一個封包開始你就斷線了，而且**再也連不進去**（SSH 被自己的規則擋），只能透過雲商的 console/救援模式搶救。防範鐵律：**先把該允許的規則（loopback、ESTABLISHED、SSH）加好，「最後」才設 `-P DROP`**。更安全的是用 `iptables-apply`（設定後給你 N 秒確認，沒確認就自動回滾恢復原規則，防止鎖死）。在 netns 裡測試（弄壞了刪掉重來，不影響本機）是學防火牆的安全方式。另一個常見坑：iptables 規則**重開機會消失**（要用 `iptables-save`/`netfilter-persistent` 持久化）——很多人設好防火牆，重開機後規則沒了（伺服器裸奔）。Ch 35（VPS 安全）會講正確的防火牆持久化。記住：**改遠端防火牆要極其小心，永遠留後路**。

## 動手練習

1. 看現有規則：`iptables -L -n -v` 和 `iptables -t nat -L -n -v`，理解表和鏈的結構

2. 在 netns 設防火牆：建白名單規則集（loopback+ESTABLISHED+特定 port+預設 DROP），驗證效果

3. DROP vs REJECT：對兩個 port 分別設 DROP 和 REJECT，用 `nc -zv` 看 timeout vs refused 的差別

4. NAT 實驗（Ch 22 後）：在 netns 拓樸設 MASQUERADE，看內網封包的來源 IP 被改寫

5. 跑「故意弄壞」：在 netns 體驗 `-P DROP` 擋掉 loopback，理解為什麼要先允許再設預設策略

## 本章重點整理

- netfilter 是封包在 kernel 的「安檢站」；iptables 用「表 × 鏈」控制：表（filter/nat/mangle）決定做什麼，鏈（INPUT/OUTPUT/FORWARD/PRE/POSTROUTING）決定在哪個點
- filter 表做防火牆：規則 `-A 鏈 匹配 -j 動作`；白名單模式（明確 ACCEPT + 預設 DROP）；規則由上而下比對
- 「允許 ESTABLISHED 連線」是最關鍵卻最易漏的規則（有狀態防火牆，讓出向連線的回應能回來）
- DROP（靜默/timeout，公網用隱蔽）vs REJECT（回應/refused，內網用友善）——對應 Ch 6 的 timeout vs refused
- nat 表是 Ch 8 NAT 的實作：MASQUERADE（讓內網/VPN/容器上網）、DNAT（port forwarding）、MSS clamping（解 VPN MTU）
- 設 `-P DROP` 前先允許 SSH/loopback/ESTABLISHED，否則鎖死自己；規則重開機會消失要持久化

## 自我檢核

- [ ] 能解釋封包經過哪些 chain，以及表和鏈的二維結構
- [ ] 會寫白名單防火牆規則，知道為什麼「允許 ESTABLISHED」關鍵
- [ ] 知道 DROP 和 REJECT 的差別，何時用哪個
- [ ] 理解 MASQUERADE 是 NAT/VPN/容器讓流量出網的機制
- [ ] 知道設防火牆怎麼避免把自己鎖死，規則要持久化

## 延伸閱讀

### 官方文件

- **[iptables(8) man page](https://man7.org/linux/man-pages/man8/iptables.8.html)** — netfilter
  - **讀哪裡**：TARGETS、MATCH EXTENSIONS（conntrack 等）
  - **為什麼值得讀**：iptables 所有選項的權威

### 文章

- **[A Deep Dive into Iptables and Netfilter Architecture](https://www.digitalocean.com/community/tutorials/a-deep-dive-into-iptables-and-netfilter-architecture)** — DigitalOcean
  - **這篇說什麼**：netfilter 框架、表/鏈、封包流的完整圖解
  - **讀哪裡**：整篇
  - **為什麼值得讀**：本章「封包經過哪些 chain」的權威視覺化版

- **[iptables 防火牆設定教學](https://www.frozentux.net/iptables-tutorial/iptables-tutorial.html)** — Oskar Andreasson
  - **這篇說什麼**：iptables 的完整教學（雖老但經典）
  - **為什麼值得讀**：把每個表/鏈/動作講到極致

### 書籍

- **《Linux Firewalls》— Michael Rash（No Starch）**
  - **讀哪幾章**：iptables 基礎與進階那幾章
  - **這本書的定位**：Linux 防火牆的權威，含攻擊偵測等進階

下一章看 iptables 的現代繼任者 nftables——更統一、更高效的語法，理解為什麼要取代 iptables，以及怎麼用。

→ [Ch 19 nftables](./19-nftables.md)
