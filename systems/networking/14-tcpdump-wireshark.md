# Ch 14 — tcpdump 與 Wireshark

> **目標**：把本課的核心手法「抓封包」講到能獨立 debug——tcpdump 的過濾語法（BPF filter）、怎麼讀 tcpdump 輸出、抓到檔案用 Wireshark 深入分析、以及「抓封包 debug」的實戰流程。前面每章都用了一點 tcpdump，這章系統化它，讓你能對任何網路問題「抓下來看」。這是 debug 網路的終極武器。

> **環境**：Linux（tcpdump + Wireshark）。tcpdump 需 root（Ch 0）。

## 為什麼抓封包是終極武器？

前面每一章都用 tcpdump 看了點東西——看 TCP 握手、看 TLS 協商、看 ARP。但抓封包不只是「看協定長怎樣」，它是 debug 網路問題的**終極手段**。當所有上層工具都說不出問題在哪時，抓封包讓你看到**線上實際發生了什麼**——封包有沒有送出去、對方有沒有回、回了什麼、在哪一步斷掉。

「應該發生什麼」（讀文件、看設定）和「實際發生什麼」（抓封包）之間的差距，就是 bug 藏身的地方。tcpdump 和 Wireshark 讓你看到實際——這是排查疑難網路問題（連線莫名斷、TLS 失敗、效能問題）無可替代的能力。這章把前面零散用的 tcpdump 系統化，讓你能獨立地「抓下來、讀懂、定位問題」。

## 先建立直覺:tcpdump 是網路的監視錄影機

```
tcpdump = 在網卡上裝一台「封包錄影機」

  正常情況：封包進出網卡，你看不到
        │
  tcpdump：在網卡上「側錄」經過的封包
    每個經過的封包都被印出來（或存檔）
    你能看到：誰送給誰、什麼協定、什麼內容、什麼時間
        │
  關鍵能力：
    1. 過濾（filter）：只看你關心的（不然雜訊淹沒你）
    2. 讀輸出：從一行文字看出封包的關鍵資訊
    3. 存檔（-w）：抓下來用 Wireshark 深入分析
        │
  tcpdump（命令列，快速側錄/遠端）
  Wireshark（圖形化，深入分析/解碼）
    → 常見流程：tcpdump 在伺服器抓存檔，
      拉回本機用 Wireshark 分析
```

關鍵心智：tcpdump 是「網路監視錄影機」——側錄網卡上經過的封包。三個關鍵能力：**過濾**（只看關心的，否則雜訊淹沒）、**讀輸出**（從一行看出封包關鍵資訊）、**存檔**（-w 抓下來用 Wireshark 深入）。tcpdump 適合命令列/遠端/快速側錄，Wireshark 適合圖形化深入分析。

> tcpdump 抓的是 Ch 2 的封裝結構——你會看到連結層（MAC）、網路層（IP）、傳輸層（TCP/UDP）的資訊。抓封包需要 root/CAP_NET_RAW（Ch 0）。如果還沒設定好抓包權限，回看 [Ch 0](./00-environment-setup.md)。

## tcpdump 的過濾語法（BPF）

過濾是 tcpdump 的命脈——網卡上流量很多，不過濾你會被淹沒：

```bash
# === 基本過濾（BPF filter 語法）===
sudo tcpdump -i any host 8.8.8.8           # 和 8.8.8.8 相關的封包
sudo tcpdump -i any port 443               # port 443 的（HTTPS）
sudo tcpdump -i any src 192.168.1.100      # 來源是這個 IP
sudo tcpdump -i any dst port 53            # 目標 port 53（DNS 查詢）
sudo tcpdump -i any tcp                     # 只看 TCP
sudo tcpdump -i any icmp                    # 只看 ICMP（ping）
sudo tcpdump -i any arp                     # 只看 ARP（Ch 3）

# === 組合（and/or/not）===
sudo tcpdump -i any 'host 8.8.8.8 and port 443'
sudo tcpdump -i any 'tcp and (port 80 or port 443)'
sudo tcpdump -i any 'host example.com and not port 22'   # 排除 SSH（不然看自己的 ssh）

# === 進階：看特定 TCP flag（如只看 SYN）===
sudo tcpdump -i any 'tcp[tcpflags] & tcp-syn != 0'        # 只看含 SYN 的（連線建立）
sudo tcpdump -i any 'tcp[tcpflags] & (tcp-syn|tcp-fin) != 0'   # SYN 或 FIN

# === 網段過濾 ===
sudo tcpdump -i any net 192.168.1.0/24     # 整個網段的流量
```

```
tcpdump 常用選項（搭配 filter）：
  -i any       所有介面（或指定 eth0/lo）
  -n           不解析域名/port（數字，快且清楚）
  -nn          連 port 也不解析（純數字）
  -v / -vv     詳細（顯示 TTL/長度/選項等，Ch 4）
  -e           顯示連結層（MAC，Ch 3）
  -c N         抓 N 個就停
  -w file.pcap 存檔（用 Wireshark 開）
  -r file.pcap 讀檔（分析存的封包）
  -A           顯示 ASCII 內容（看明文 payload）
  -X           顯示 hex + ASCII
        │
  最常用組合：sudo tcpdump -i any -nn -v <filter>
```

> **過濾（BPF filter）是 tcpdump 的命脈——不過濾你會被流量淹沒，看不出問題**。網卡上一直有流量（背景的 ARP、DNS、各種連線），不過濾的話 tcpdump 輸出滾得飛快，你根本看不到關心的封包。BPF（Berkeley Packet Filter）語法很直觀：`host X`（和 X 相關）、`port N`、`src`/`dst`（方向）、`tcp`/`udp`/`icmp`/`arp`（協定）、`net X/N`（網段），用 `and`/`or`/`not` 組合。一個常見技巧：`not port 22`（排除你自己的 SSH 連線，否則你 ssh 進伺服器抓包，會看到一堆自己的 ssh 流量干擾）。進階能過濾 TCP flag（`tcp[tcpflags] & tcp-syn != 0` 只看 SYN，用於觀察連線建立）。BPF 過濾在**kernel 層**完成（高效，不符合的封包根本不複製到 tcpdump），所以即使高流量也能精準抓。記住核心組合 `sudo tcpdump -i any -nn -v <filter>`（所有介面、純數字、詳細）——這是你 debug 的起手式。下節學怎麼讀它的輸出。

## 讀 tcpdump 輸出

```bash
# 抓 TCP 看輸出格式
sudo tcpdump -i any -nn 'tcp port 443' -c 5
```

```
tcpdump 一行輸出的解讀：

  10:30:45.123456 IP 192.168.1.100.54321 > 93.184.216.34.443: Flags [S], seq 1000, win 64240, length 0
  └─時間戳────┘    └─來源IP─────┘.port  └─目標IP─────┘.port  └flags┘ └seq─┘ └視窗─┘  └長度┘
        │
  逐欄解讀：
    時間戳：    封包的時間（精確到微秒）
    IP：        網路層協定
    來源 > 目標：192.168.1.100:54321 → 93.184.216.34:443
    Flags：     TCP flags（S=SYN, .=ACK, P=PUSH, F=FIN, R=RST）
    seq/ack：   序號/確認號（Ch 6）
    win：       視窗大小（流量控制，Ch 6）
    length：    payload 長度（0 = 只有標頭，如純握手/ACK）
        │
  一次 TCP 握手在 tcpdump 看起來：
    Flags [S]  ── SYN（連線請求）
    Flags [S.] ── SYN-ACK（S + ACK）
    Flags [.]  ── ACK（純確認）
    Flags [P.] ── PUSH+ACK（帶資料）
    Flags [F.] ── FIN+ACK（關閉）
    Flags [R]  ── RST（重置/拒絕，Ch 6）
```

> **讀懂 tcpdump 的 Flags 欄是 debug TCP 的核心——它直接告訴你連線在哪個階段、有沒有異常**。tcpdump 用簡寫表示 TCP flags：`[S]`=SYN、`[S.]`=SYN-ACK、`[.]`=純 ACK、`[P.]`=PUSH+ACK（帶資料）、`[F.]`=FIN+ACK（關閉）、`[R]`=RST（重置）。看 flags 序列就能讀出連線發生什麼：正常握手是 `[S]`→`[S.]`→`[.]`（Ch 6）；如果你只看到 `[S]` 重複（client 一直送 SYN 沒人回 SYN-ACK）= 對方沒回應（服務沒開/防火牆 DROP）；看到 `[R]`（RST）= 連線被拒絕或重置（服務沒開回 RST，或連線異常終止）。`length` 欄看是否帶資料（0=純控制封包）。`seq`/`ack` 看資料流動和重傳（Ch 6）。配合時間戳能看延遲（SYN 和 SYN-ACK 之間的時間 = RTT）。這個「從 flags 序列讀出連線故事」的能力是 debug 連線問題的核心——你能一眼看出「卡在握手」「被 RST」「正常傳輸但慢」。練到後面，看 tcpdump 輸出就像讀連線的劇本。

## tcpdump 存檔 + Wireshark 分析

```bash
# === 抓到檔案，用 Wireshark 深入分析 ===
# 在伺服器抓（命令列）：
sudo tcpdump -i any -w ~/netlab/captures/debug.pcap 'host example.com' -c 100
#   抓 100 個封包到檔案

# 拉回本機分析（如果在遠端伺服器抓）：
# scp user@server:~/netlab/captures/debug.pcap .

# 用 Wireshark 開：
wireshark ~/netlab/captures/debug.pcap

# === Wireshark 的殺手功能 ===
# 1. 顯示過濾（比 tcpdump 的 BPF 更強）：
#    tcp.flags.syn==1          只看 SYN
#    tls.handshake.type==1     只看 ClientHello
#    http.request              只看 HTTP 請求
#    tcp.analysis.retransmission  只看重傳（debug 丟包！）
#
# 2. Follow Stream（右鍵 > Follow > TCP Stream）：
#    把一個連線的所有封包串成「對話」，看完整的請求-回應
#
# 3. Statistics 選單：
#    > Conversations：所有連線的統計
#    > Flow Graph：時序圖（看連線的完整流程，像 Ch 1 的旅程圖）
#    > TCP Stream Graphs：視窗/重傳/吞吐量圖（debug 效能）
#
# 4. Expert Information：自動標出異常（重傳、亂序、RST...）
```

> **「tcpdump 抓 + Wireshark 分析」是標準的疑難排查流程——尤其遠端伺服器**。tcpdump 適合在伺服器上**抓**（命令列、輕量、能 SSH 進去抓），Wireshark 適合**分析**（圖形化、強大的解碼和統計）。標準流程：在伺服器 `tcpdump -w debug.pcap <filter>` 抓存檔，`scp` 拉回本機，用 Wireshark 開。Wireshark 的殺手功能：**顯示過濾**（比 BPF 更強，能過濾應用層如 `http.request`、`tls.handshake.type==1`，特別是 `tcp.analysis.retransmission` 直接抓出重傳，debug 丟包神器）；**Follow Stream**（把一個連線的封包串成「對話」，看完整請求-回應）；**Statistics > Flow Graph**（時序圖，視覺化整個連線流程，像 Ch 1 的旅程圖）；**Expert Information**（自動標出重傳/亂序/RST 等異常）。對效能問題，**TCP Stream Graphs** 能畫出視窗大小、吞吐量、重傳的時序圖（看 Ch 6 的流量/壅塞控制實際表現）。記住分工：**tcpdump 抓（哪裡都能用）、Wireshark 析（功能強大）**。這個組合能解開幾乎任何網路疑難。

## 抓封包 debug 的實戰流程

```
用抓封包 debug 一個問題的系統流程：

  1. 確定「在哪抓」：
     問題在本機↔伺服器之間 → 兩端都可抓，先抓本機端
     懷疑中間環節 → 可能要在路由器/多點抓
        │
  2. 設好過濾，重現問題：
     tcpdump -w trace.pcap '<精準的 filter>'
     然後「重現問題」（觸發那個失敗的操作）
        │
  3. 分析：對照「預期」vs「實際」
     封包有送出去嗎？（沒有 → 本機問題，路由/防火牆）
     對方有回嗎？（沒回 → 對方問題或網路 DROP）
     回了什麼？（RST → 拒絕；正常但慢 → 效能/重傳）
        │
  4. 定位：問題在哪一步斷掉
     DNS 沒解析？TCP 握手沒完成？TLS 失敗？應用層錯？
     → 對應到 Ch 2 的分層排查
        │
  → 抓封包讓你看到「實際」，和「預期」對照找出 bug
```

```bash
# 實戰範例：debug「連某服務很慢」
# 1. 抓
sudo tcpdump -i any -w slow.pcap 'host slow-server.com' &
# 2. 重現（觸發那個慢操作）
curl -s https://slow-server.com > /dev/null
sudo pkill tcpdump
# 3. 用 Wireshark 分析時序
#    看哪一步慢：DNS 慢？TCP 握手慢（RTT 大）？TLS 慢？還是伺服器處理慢？
#    Flow Graph + 時間戳 = 一眼看出延遲在哪個階段
```

> **抓封包 debug 的精髓是「對照預期 vs 實際，定位問題斷在哪一步」**。系統流程：(1) 決定**在哪抓**（先抓離你近的一端）；(2) 設好**精準過濾**後**重現問題**（觸發那個失敗操作）；(3) **對照分析**——封包有送出嗎（沒有=本機問題：路由/防火牆擋了出向）？對方有回嗎（沒回=對方問題或網路 DROP）？回了什麼（RST=拒絕、正常但慢=效能/重傳）？(4) **定位**問題斷在哪一步（DNS/TCP/TLS/應用層，對應 Ch 2 分層排查）。經典應用是 debug **「連線慢」**——抓下來看 Wireshark 的 Flow Graph 和時間戳，立刻看出延遲在哪個階段：DNS 解析慢（Ch 9）？TCP 握手 RTT 大（網路延遲）？TLS 協商慢（Ch 11）？還是「TCP 握手很快但伺服器很久才回應」（伺服器處理慢，不是網路問題）？這種「定位延遲在哪個階段」是抓封包獨有的能力——上層工具只能告訴你「總共慢」，封包能告訴你「慢在哪一步」。練習 B 會用這個流程 debug 一系列真實問題。

## 故意弄壞:抓自己的流量看協定

```bash
# 最好的學習：抓各種操作的封包，對照前面學的協定

# 1. 抓 DNS（Ch 9）—— 看查詢和回應
sudo tcpdump -i any -nn 'port 53' -c 4 &
dig example.com > /dev/null
sudo pkill tcpdump
#   A? example.com / A 93.184.216.34

# 2. 抓 ARP（Ch 3）—— 看廣播問路
sudo tcpdump -i any -nn arp -c 4 &
ip neigh flush all; ping -c1 $(ip route | grep default | grep -oP 'via \K\S+') > /dev/null
sudo pkill tcpdump
#   ARP, Request who-has ... / ARP, Reply ... is-at ...

# 3. 抓完整 TCP 握手（Ch 6）
sudo tcpdump -i any -nn 'tcp port 443 and host example.com' -c 6 &
curl -sI https://example.com > /dev/null
sudo pkill tcpdump
#   [S] / [S.] / [.] / ...

# 4. 抓 ping 的 ICMP（Ch 4）
sudo tcpdump -i any -nn icmp -c 4 &
ping -c2 8.8.8.8 > /dev/null
sudo pkill tcpdump
#   ICMP echo request / reply
```

> 這節的價值是「**用抓封包複習整個 Part 2-3**」——把每個協定的封包親手抓出來，對照前面學的。抓 DNS（看 query/response，Ch 9）、抓 ARP（看廣播問路，Ch 3）、抓 TCP 握手（看 SYN/SYN-ACK/ACK，Ch 6）、抓 ICMP（看 ping，Ch 4）。每抓一個，對照那一章的說明，你會發現「協定文字」和「實際封包」終於對上了。這種「協定 ↔ 封包」的連結是把網路從「背概念」變成「真正理解」的關鍵，也是本課從 Ch 0 就強調的核心手法。建議花時間把前面每個協定都抓一遍——這比重讀章節有效得多。tcpdump 是你「看見網路」的眼睛，練熟它，後面 VPN（Ch 24 抓 WireGuard 封包）、翻牆（Ch 31 分析流量特徵）、VPS debug 都靠它。

## 動手練習

1. 練過濾：用各種 BPF filter（host/port/tcp/and/or/not）抓不同流量，熟悉過濾語法

2. 讀輸出：抓一次 TCP 連線，逐行讀懂 Flags/seq/ack/win/length，畫出連線的 flags 序列

3. 抓存檔 + Wireshark：`tcpdump -w` 抓一次 HTTPS，用 Wireshark 開，玩 Follow Stream 和 Flow Graph

4. 抓重傳：下載大檔案時抓，用 Wireshark 的 `tcp.analysis.retransmission` 過濾看有沒有重傳

5. 跑「故意弄壞」：把 DNS/ARP/TCP/ICMP 各抓一遍，對照 Ch 3/4/6/9 的協定說明

## 本章重點整理

- tcpdump 是「封包錄影機」——側錄網卡流量；過濾（BPF）、讀輸出、存檔是三個核心能力
- BPF 過濾是命脈：host/port/src/dst/tcp/udp/icmp/arp + and/or/not；核心組合 `tcpdump -i any -nn -v <filter>`
- 讀 Flags 欄是 debug TCP 的核心：[S]/[S.]/[.]/[P.]/[F.]/[R]，從序列讀出連線故事
- 標準流程：tcpdump 抓存檔（-w）+ Wireshark 分析（Follow Stream/Flow Graph/顯示過濾/Expert Info）
- 抓封包 debug：對照預期 vs 實際、定位問題斷在哪一步（DNS/TCP/TLS/應用層）；特別擅長「定位延遲在哪階段」

## 自我檢核

- [ ] 能寫出常見的 BPF 過濾（特定主機/port/協定的組合）
- [ ] 能讀懂 tcpdump 輸出的每一欄，特別是 Flags 序列
- [ ] 會用 tcpdump 存檔 + Wireshark 分析（Follow Stream/Flow Graph）
- [ ] 知道抓封包 debug 的系統流程（在哪抓、重現、對照、定位）
- [ ] 能用抓封包定位「連線慢」是慢在哪個階段

## 延伸閱讀

### 必讀資源

- **[tcpdump 完整教學](https://danielmiessler.com/study/tcpdump/)** — Daniel Miessler
  - **這篇說什麼**：tcpdump 的所有常用選項和 filter，從基礎到進階
  - **讀哪裡**：整篇（放手邊查）
  - **為什麼值得讀**：最完整的 tcpdump 速查，filter 範例豐富

- **[Wireshark 官方教學](https://www.wireshark.org/docs/wsug_html_chunked/)** — Wireshark User's Guide
  - **讀哪裡**：Display Filters、Following Streams、Statistics 那幾章
  - **為什麼值得讀**：Wireshark 功能的權威；顯示過濾語法和統計功能

### 文章

- **[Julia Evans 的 tcpdump zine](https://jvns.ca/blog/2016/03/16/tcpdump-is-amazing/)** — Julia Evans
  - **這篇說什麼**：用易懂的方式講 tcpdump 為什麼強大、怎麼用
  - **為什麼值得讀**：把抓封包的價值講得最生動

### 書籍

- **《Practical Packet Analysis》— Chris Sanders（No Starch, 3rd ed）**
  - **讀哪幾章**：Ch 4-6（Wireshark 的進階功能）、Ch 8-9（真實案例分析）
  - **這本書的定位**：封包分析的實戰權威，大量真實 debug 案例
  - **前提**：本章 + Part 2-3

下一章專注 DNS 工具——dig 的進階用法，把 Ch 9 的 DNS 知識落到「怎麼用工具查和 debug DNS」。

→ [Ch 15 dig / nslookup](./15-dig-nslookup.md)
