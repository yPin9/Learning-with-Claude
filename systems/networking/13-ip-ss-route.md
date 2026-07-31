# Ch 13 — ip / ss / route

> **目標**：掌握 Linux 網路設定與觀測的核心工具 iproute2——`ip`（管介面/位址/路由/鄰居）、`ss`（看 socket/連線，取代 netstat）、路由表的查詢與操作。這些是你在 Linux 上「看網路狀態、改網路設定」的主力工具，把前面學的概念（介面/IP/路由/連線狀態）落到實際命令。Part 4 工具章的第一站，也是 debug 的基礎武器。

> **環境**：Linux（iproute2，現代 distro 內建）。對照舊的 net-tools（ifconfig/netstat）會標注。

## 為什麼從 ip/ss 開始？

前面學了一堆概念——介面、IP、路由、TCP 連線狀態。但「怎麼在 Linux 上看到和操作它們」？答案是 iproute2 工具集。`ip` 是設定和查看網路的瑞士刀（介面、IP、路由、ARP），`ss` 是看連線狀態的工具。

這兩個工具是你 debug 網路的起點——「我的 IP 是什麼」「路由往哪走」「哪些 port 在監聽」「這個連線是什麼狀態」，全靠它們。Ch 0 提過要用 iproute2 不用 net-tools，這章把 `ip` 和 `ss` 的常用功能講透，並對照舊工具（你看舊教學會遇到 ifconfig/netstat）。掌握它們，前面的概念就有了「看得見、摸得著」的工具支撐。

## 先建立直覺:ip 是網路設定的總管

```
iproute2 工具集（一個 ip 命令管很多事）：

  ip 的子命令（ip <物件> <動作>）：
    ip link    —— 網路介面（網卡）：up/down、MAC、MTU
    ip addr    —— IP 位址：看/加/刪 IP
    ip route   —— 路由表：封包往哪送（Ch 4）
    ip neigh   —— ARP 鄰居表（Ch 3）
    ip netns   —— network namespace（Ch 0/20）
        │
  ss —— socket/連線狀態（取代 netstat）：
    哪些 port 在監聽、有哪些連線、各是什麼狀態（Ch 6）
        │
  → ip 管「設定」（介面/位址/路由），ss 看「連線」
    這兩個覆蓋日常網路操作的 90%
```

關鍵心智：`ip` 是「網路設定總管」——用 `ip <物件> <動作>` 的格式管理介面（link）、位址（addr）、路由（route）、鄰居（neigh）、namespace（netns）。`ss` 看 socket/連線狀態。這兩個工具覆蓋你日常「看網路狀態、改網路設定」的大部分需求。

> 這章的命令對應前面的概念：`ip link`（介面，Ch 3）、`ip addr`（IP，Ch 5）、`ip route`（路由，Ch 4）、`ip neigh`（ARP，Ch 3）、`ss`（TCP 連線狀態，Ch 6）。把那些章的概念和這裡的工具對照著看。

## ip:介面、位址、路由

```bash
# === ip link：網路介面（網卡）===
ip link                          # 列出所有介面
# 1: lo: <LOOPBACK,UP> ...         loopback（本機回環，127.0.0.1）
# 2: eth0: <BROADCAST,UP> ... mtu 1500 ... link/ether aa:bb:...   實體網卡
ip link show eth0                # 看特定介面
ip link set eth0 up              # 啟用介面（down 是停用）
ip link set eth0 mtu 1400        # 改 MTU（Ch 4 的 MTU）

# === ip addr：IP 位址 ===
ip addr                          # 看所有介面的 IP（最常用，可簡寫 ip a）
# 2: eth0: ... inet 192.168.1.100/24 ...   你的 IP/遮罩（Ch 5）
ip addr add 192.168.1.200/24 dev eth0    # 加一個 IP（一張網卡可多 IP）
ip addr del 192.168.1.200/24 dev eth0    # 刪

# === ip route：路由表 ===
ip route                         # 看路由表（簡寫 ip r）
# default via 192.168.1.1 dev eth0       預設路由（其他都交給閘道，Ch 4）
# 192.168.1.0/24 dev eth0 ...            本網段直接送
ip route get 8.8.8.8             # 查「這個 IP 的封包往哪送」（debug 路由）
ip route add 10.0.0.0/8 via 192.168.1.254    # 加一條路由
ip route del 10.0.0.0/8          # 刪

# === ip neigh：ARP 鄰居表（Ch 3）===
ip neigh                         # 看 IP→MAC 對照（簡寫 ip n）
```

```
ip 對照舊的 net-tools（看舊教學會遇到）：

  舊（net-tools，過時）        新（iproute2，用這個）
  ifconfig                  →  ip addr
  ifconfig eth0 up          →  ip link set eth0 up
  route -n                  →  ip route
  route add ...             →  ip route add ...
  arp -a                    →  ip neigh
  netstat -tlnp             →  ss -tlnp
        │
  為什麼換：net-tools 不再維護、不支援現代功能
    （多 IP、policy routing、命名空間...）
```

> **`ip route get <IP>` 是 debug 路由的金鑰——它告訴你「封包實際會往哪送」**。很多網路問題是路由問題（封包送錯方向）。`ip route get 8.8.8.8` 直接告訴你「要連這個 IP，封包會從哪個介面、經過哪個閘道送出」——這比讀整個路由表自己推斷快得多。如果輸出的 `via` 是錯的閘道、或 `dev` 是錯的介面，就找到問題了。`ip` 的子命令設計很一致（`ip <物件> show/add/del/set`），記住物件（link/addr/route/neigh）就能推出操作。注意 `ip` 改的設定是**暫時的**（重開機失效）——永久設定要寫進 distro 的網路設定檔（Netplan/NetworkManager/systemd-networkd，依 distro）。對照舊工具：你會在老教學、老腳本看到 `ifconfig`/`route`/`netstat`，但它們過時了（net-tools 不再維護，不支援多 IP、policy routing 等現代功能）——學會 iproute2 的對照，看舊資料時能翻譯，自己寫一律用新的。

## ss:看連線與監聽

`ss`（socket statistics）取代 netstat，看連線狀態和監聽的 port：

```bash
# === 最常用的 ss 組合 ===
ss -tlnp                         # TCP + Listening + 數字 + 程式（看「開了哪些服務」）
# State   Local Address:Port   Process
# LISTEN  0.0.0.0:22            users:(("sshd",pid=...))     SSH 在聽
# LISTEN  127.0.0.1:5432        users:(("postgres",...))     postgres 只聽本機
#   -t TCP, -l listening, -n 數字(不解析域名), -p 程式

ss -tan                          # 所有 TCP 連線（含狀態，Ch 6）
# ESTAB / TIME-WAIT / LISTEN / SYN-SENT...

ss -tunlp                        # TCP + UDP + listening + 數字 + 程式（完整的「開了什麼」）
ss -s                            # 連線統計摘要（各狀態總數）

# === 過濾（ss 的強大之處）===
ss -tan state established        # 只看已建立的連線
ss -tan '( dport = :443 or sport = :443 )'   # 只看 443 相關
ss -tnp dst 8.8.8.8              # 連到 8.8.8.8 的連線
ss -tlnp 'sport = :80'           # 監聽 80 的

# === debug 用 ===
ss -tanp | grep :443             # 找 443 的所有連線和程式
ss -ti                           # TCP 內部資訊（cwnd/rtt，Ch 6 的壅塞控制）
```

```
ss 的關鍵 flags（記住這幾個組合）：
  -t  TCP
  -u  UDP
  -l  只看 listening（監聽的 port）
  -n  數字（不解析 port/域名，快且清楚）
  -p  顯示程式（哪個 process 開的，需 root 才完整）
  -a  所有（含 listening 和非 listening）

  常用組合：
    ss -tlnp   ← 「開了哪些 TCP 服務」（最常用！debug 服務沒起來）
    ss -tan    ← 「所有 TCP 連線的狀態」（debug 連線問題）
    ss -tunlp  ← 「開了哪些 TCP/UDP 服務」（完整）
```

> **`ss -tlnp` 是「這台機器開了哪些服務」的標準命令——debug「連不上服務」的第一步**。當「服務連不上」時，先在伺服器上 `ss -tlnp`——它列出所有監聽的 TCP port 和對應的程式。常見診斷：**服務根本沒監聽**（你的服務沒啟動，列表裡沒有它）→ 啟動服務（Ch 31）；**監聽在 `127.0.0.1` 而非 `0.0.0.0`**（只聽本機，外部連不進）→ 這是超常見的坑！`127.0.0.1:5432` 表示 postgres 只接受本機連線，外部 `connection refused`；要對外服務必須聽 `0.0.0.0`（所有介面）或具體的對外 IP（Ch 5/35 的安全考量——只聽本機是安全的預設，要對外才改）。`ss -tan` 看連線狀態（對照 Ch 6 的 TCP 狀態機，debug TIME_WAIT/CLOSE_WAIT 堆積）。`ss` 的**過濾語法**很強大（`state established`、`dport = :443`）——比 `netstat | grep` 精準。`ss -s` 給連線統計摘要（快速看系統有多少連線、各狀態）。記住 `ss -tlnp`（開了什麼）和 `ss -tan`（連線狀態）這兩個，覆蓋大部分 debug 需求。

## 故意弄壞:127.0.0.1 vs 0.0.0.0 的監聽陷阱

```bash
# 體會「監聽位址」造成的「服務連不上」（超常見的坑）

# 啟動一個只聽 127.0.0.1 的服務
python3 -m http.server 8000 --bind 127.0.0.1 &
SERVER_PID=$!
sleep 1

ss -tlnp | grep 8000
# LISTEN 127.0.0.1:8000 ...    ← 只聽本機！

# 本機連得上
curl -sI http://127.0.0.1:8000 | head -1     # HTTP/1.0 200 OK ✓
# 但「對外的 IP」連不上（即使是同一台機器的對外 IP）
MY_IP=$(ip route get 1.1.1.1 | grep -oP 'src \K\S+')
curl -sI http://$MY_IP:8000 --max-time 3 | head -1     # 失敗（refused）✗
#   → 只聽 127.0.0.1，外部（含自己的對外 IP）連不上
kill $SERVER_PID

# 對比：聽 0.0.0.0（所有介面，對外開放）
python3 -m http.server 8001 --bind 0.0.0.0 &
SERVER_PID=$!
sleep 1
ss -tlnp | grep 8001
# LISTEN 0.0.0.0:8001 ...      ← 聽所有介面
curl -sI http://$MY_IP:8001 --max-time 3 | head -1     # HTTP/1.0 200 OK ✓
kill $SERVER_PID
```

> **「服務在本機連得上、外部連不上」十之八九是監聽在 `127.0.0.1` 而非 `0.0.0.0`——這是最常見的部署坑**。`ss -tlnp` 的 Local Address 欄是關鍵：`127.0.0.1:8000`（只聽 loopback，**只有本機程式能連**）vs `0.0.0.0:8000`（聽所有介面，**外部能連**）vs `192.168.1.100:8000`（只聽特定 IP）。很多服務**預設聽 127.0.0.1**（安全的預設——不主動暴露到網路），所以你在伺服器上 `curl localhost` 成功，但從外部或用對外 IP 連就 `connection refused`。修法是改服務設定讓它聽 `0.0.0.0`（如資料庫的 `bind_address`、應用的 `--host 0.0.0.0`）。但要小心安全——聽 `0.0.0.0` = 對全世界開放（配合防火牆限制來源，Ch 18/35）。這個「localhost 通、外部不通」的症狀是部署服務（Ch 36）最常見的困惑，記住先 `ss -tlnp` 看監聽位址。反過來，**資料庫等敏感服務應該只聽 127.0.0.1**（或內網 IP），不該暴露到公網——很多資料外洩就是因為 MongoDB/Redis/Elasticsearch 不小心聽了 0.0.0.0 又沒防火牆（Ch 35）。

## 進階:policy routing 與多路由表

```bash
# Linux 支援「多張路由表」和「策略路由」（進階，VPN/多網卡用）
ip rule                          # 路由規則（決定用哪張路由表）
ip route show table main         # 主路由表
ip route show table all          # 所有路由表

# 場景：VPN 想「某些流量走 VPN、某些走原本」
# → 用 policy routing：按來源/標記決定用哪張路由表
# WireGuard（Ch 24）等 VPN 會用到這個

# 看完整的網路狀態（一次看全部）
ip -br addr                      # 簡潔版（-br = brief，一行一介面）
ip -br link
ip -s link show eth0             # 介面統計（收發封包數、錯誤、丟棄）
#   RX/TX errors/dropped → 硬體或驅動問題的線索
```

> **`ip -s link`（介面統計）能看出硬體層的問題——RX/TX 的 errors/dropped 是線索**。`ip -s link show eth0` 顯示介面的收發統計：封包數、bytes、以及 **errors**（錯誤）和 **dropped**（丟棄）。如果 errors/dropped 持續增加，可能是硬體問題（網線/網卡）、驅動問題、或緩衝區滿（流量太大處理不過來）——這是「網路時好時壞、丟包」的底層線索，比應用層 debug 更深。`ip -br`（brief 簡潔模式）適合快速概覽。**policy routing**（策略路由，`ip rule` + 多路由表）是進階主題——它讓你「按來源/標記決定走哪張路由表」，VPN（Ch 24，某些流量走 VPN 某些不走）、多網卡（不同流量走不同網卡）會用到。一般 debug 用不到，但知道它存在——當你看到 VPN 的複雜路由設定時（`ip rule` 裡一堆規則），就知道那是 policy routing。這些進階功能正是 iproute2 取代 net-tools 的原因（net-tools 根本不支援）。

## 動手練習

1. 認識你的網路：`ip addr`（你的 IP）、`ip route`（路由）、`ip link`（介面）、`ip neigh`（鄰居），對照 Ch 3-5 的概念

2. 路由查詢：`ip route get` 各種目標 IP（本網段/外網/8.8.8.8），看封包往哪送

3. 看服務：`ss -tlnp` 看你機器開了哪些服務，注意每個是聽 127.0.0.1 還 0.0.0.0

4. 看連線狀態：`ss -tan` 看當前 TCP 連線，`ss -s` 看統計摘要，對照 Ch 6 狀態機

5. 跑「故意弄壞」：用 python http.server 分別聽 127.0.0.1 和 0.0.0.0，驗證外部連得上連不上的差別

## 本章重點整理

- iproute2 是現代 Linux 網路工具：`ip`（介面/位址/路由/鄰居/netns）+ `ss`（連線狀態），取代過時的 net-tools
- `ip <物件> <動作>`：link（介面）、addr（IP）、route（路由）、neigh（ARP）；`ip route get <IP>` 是 debug 路由的金鑰
- `ss -tlnp`（開了哪些服務）和 `ss -tan`（連線狀態）是 debug 的主力；ss 過濾語法強大
- 監聽 127.0.0.1（只本機）vs 0.0.0.0（對外）是「localhost 通、外部不通」的常見坑——也是安全考量
- `ip -s link` 的 errors/dropped 是硬體層問題線索；policy routing（ip rule + 多表）是 VPN/多網卡的進階

## 自我檢核

- [ ] 能用 `ip` 看和改介面、IP、路由，知道對應的舊 net-tools 命令
- [ ] 會用 `ip route get` debug「封包往哪送」
- [ ] 能用 `ss -tlnp` 看服務、`ss -tan` 看連線狀態
- [ ] 知道 127.0.0.1 vs 0.0.0.0 監聽的差別，能 debug「localhost 通外部不通」
- [ ] 知道 `ip -s link` 的統計能看硬體層問題

## 延伸閱讀

### 官方文件

- **[ip(8) man page](https://man7.org/linux/man-pages/man8/ip.8.html)** + **[ss(8)](https://man7.org/linux/man-pages/man8/ss.8.html)** — Linux man-pages
  - **讀哪裡**：ip 的各子命令（ip-link/ip-address/ip-route 各有獨立 man page）、ss 的過濾語法
  - **為什麼值得讀**：iproute2 的權威；ss 的過濾語法（`state`/`dport`）這裡最完整

### 文章

- **[A tour of the ip command](https://www.redhat.com/sysadmin/ip-command)** — Red Hat
  - **這篇說什麼**：ip 命令各子命令的實用範例
  - **讀哪裡**：整篇
  - **為什麼值得讀**：把 ip 的常用操作整理成易查的形式

- **[ss vs netstat](https://www.cyberciti.biz/files/ss.html)** — nixCraft
  - **這篇說什麼**：ss 取代 netstat 的完整對照和過濾範例
  - **為什麼值得讀**：本章 ss 那節的擴充，含更多過濾技巧

### 書籍

- **《Linux 系統管理技術手冊》(UNIX and Linux System Administration Handbook) — 網路章** — Nemeth 等
  - **讀哪幾章**：TCP/IP 網路那章（含 ip/ss 的實務用法）
  - **這本書的定位**：系統管理的權威，把網路工具放進運維脈絡

下一章是工具章的重頭戲——tcpdump 與 Wireshark，把「抓封包」這個本課核心手法講透，這是 debug 網路的終極武器。

→ [Ch 14 tcpdump 與 Wireshark](./14-tcpdump-wireshark.md)
