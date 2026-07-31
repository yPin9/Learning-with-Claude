# Ch 9 — DNS

> **目標**：把 DNS（域名系統）講透——域名怎麼一層層解析成 IP（根→TLD→權威伺服器的遞迴查詢）、各種記錄類型（A/AAAA/CNAME/MX/TXT/NS）、快取與 TTL、DNS 為什麼是「網際網路的電話簿」也是「最常見的故障源」、以及 DNS 的隱私問題（DoH/DoT）。這是 Ch 1 旅程的第一步（拿到 IP），也是 debug「網站打不開」的首要嫌疑。

> **環境**：Linux（dig 是主角工具，Ch 15 深入）。

## 為什麼 DNS 是「最常見的故障源」？

每次上網的第一步都是 DNS——你打 `example.com`，電腦要先問「這個域名的 IP 是多少」才能連線（Ch 1 旅程步驟 1）。DNS 是「網際網路的電話簿」：把人類好記的域名翻譯成電腦用的 IP 位址。

工程界有句名言：「**It's always DNS**」（出問題十之八九是 DNS）。為什麼？因為 DNS 是分散式的、有多層快取、有 TTL 延遲、設定容易出錯——一個記錄改錯、快取沒過期、TTL 設太長，都會造成「為什麼網站連不上」「為什麼改了 DNS 還沒生效」。理解 DNS 的解析流程和快取機制，你才能 debug 這些問題，也才懂為什麼 DNS 是 debug 網路時的第一個嫌疑犯。

## 先建立直覺:問路的接力

```
DNS 解析 = 問路，一層層問到答案

  你問：「www.example.com 的 IP 是多少？」
  但沒有「一個」伺服器知道全世界所有域名
  → 分層詢問（像問路）：
        │
  1. 問「根伺服器」（root）：
     「.com 的伺服器在哪？」
     → 根：「去問 .com 的 TLD 伺服器，在這裡」
        │
  2. 問「.com TLD 伺服器」：
     「example.com 的伺服器在哪？」
     → TLD：「去問 example.com 的權威伺服器，在這裡」
        │
  3. 問「example.com 權威伺服器」：
     「www.example.com 的 IP 是多少？」
     → 權威：「93.184.216.34」
        │
  → 從「根」開始，一層層問到「權威伺服器」
    每層只知道「下一層去哪問」（像 Ch 4 的逐跳）
    域名的階層（www.example.com）對應這個查詢階層
```

關鍵心智：沒有單一伺服器知道全世界所有域名。DNS 是**分層的**——從根伺服器開始，一層層問（根→.com TLD→example.com 權威伺服器），每層告訴你「下一層去哪問」，直到權威伺服器給出 IP。域名的點分結構（`www.example.com`）正對應這個查詢階層（由右往左：根→com→example→www）。

> DNS 通常用 UDP（Ch 7）——查詢小、要快、易重試，UDP 省掉 TCP 握手延遲。如果對 UDP 為什麼適合 DNS 不熟，回看 [Ch 7](./07-udp-vs-tcp.md)。大回應或區域傳送才用 TCP。

## DNS 解析的完整流程

```
完整的 DNS 解析（你打 www.example.com）：

  你的電腦
    │ 1. 問「遞迴解析器」（通常是 ISP 或 8.8.8.8/1.1.1.1）
    ▼
  遞迴解析器（recursive resolver）
    │ 先查快取，沒有就代替你去問：
    │
    │ 2. 問根伺服器(.)：「.com 在哪？」
    │    ← 「.com TLD 伺服器在 x.x.x.x」
    │ 3. 問 .com TLD：「example.com 在哪？」
    │    ← 「example.com 權威伺服器在 y.y.y.y」（NS 記錄）
    │ 4. 問 example.com 權威伺服器：「www 的 IP？」
    │    ← 「93.184.216.34」（A 記錄）
    │
    │ 5. 快取結果（依 TTL），回給你
    ▼
  你的電腦拿到 93.184.216.34 → 開始連線（Ch 6）
        │
  → 「遞迴解析器」替你跑完整個遞迴查詢
    你只問一次，它跑根→TLD→權威三步
    並快取結果（下次同域名直接給，不用再跑）
```

```bash
# 看完整的遞迴查詢過程（dig +trace）
dig +trace www.example.com
# .                  ... NS a.root-servers.net.   ← 根伺服器
# com.               ... NS a.gtld-servers.net.   ← .com TLD
# example.com.       ... NS a.iana-servers.net.   ← 權威伺服器
# www.example.com.   ... A 93.184.216.34          ← 最終答案
#   → 親眼看到「根→TLD→權威」三層查詢

# 一般查詢（用你設定的解析器，通常已快取）
dig www.example.com +short
# 93.184.216.34

# 看你用的是哪個 DNS 解析器
cat /etc/resolv.conf
# nameserver 192.168.1.1   或   nameserver 8.8.8.8
```

> **「遞迴解析器」替你跑完整個根→TLD→權威的查詢，並快取結果——這是 DNS 效率的關鍵**。你的電腦不會自己跑遍根→TLD→權威（那太慢），而是問一個**遞迴解析器**（通常是 ISP 提供的，或公共的 8.8.8.8/1.1.1.1），它替你跑完整個遞迴查詢，並**快取**結果。下次你（或同網路其他人）問同一個域名，解析器直接從快取給，不用再跑根→TLD→權威——這讓 DNS 能撐住全球海量查詢（根伺服器不會被每個查詢打爆，因為大多被快取擋下了）。`dig +trace` 讓你親眼看到完整的三層查詢（它繞過快取，從根開始問起），這是理解 DNS 階層的最佳工具。`/etc/resolv.conf` 是你的電腦設定「問哪個解析器」的地方。理解這個分工——你的電腦問解析器、解析器跑遞迴並快取——你就懂了 DNS 的架構，也懂了快取為什麼是 debug DNS 的核心（下節）。

## DNS 記錄類型

```
常見 DNS 記錄類型（每種記錄存不同資訊）：

  A      域名 → IPv4 位址     example.com → 93.184.216.34
  AAAA   域名 → IPv6 位址     example.com → 2606:2800:...
  CNAME  域名 → 另一個域名    www → example.com（別名，轉跳查詢）
  MX     郵件伺服器           example.com 的郵件去哪（含優先級）
  TXT    任意文字             SPF/DKIM(郵件驗證)、域名所有權驗證
  NS     權威伺服器           example.com 的權威 DNS 是誰
  SOA    區域起始             區域的元資料（序號、刷新時間）
  PTR    IP → 域名（反解）    93.184.216.34 → example.com
```

```bash
# 查各種記錄
dig example.com A +short          # IPv4
dig example.com AAAA +short       # IPv6
dig example.com MX +short         # 郵件伺服器
dig example.com TXT +short        # TXT（SPF/驗證）
dig example.com NS +short         # 權威伺服器
dig www.github.com CNAME +short   # CNAME（很多 CDN 用 CNAME）

# 反解（IP → 域名）
dig -x 8.8.8.8 +short             # dns.google

# 指定問哪個解析器
dig @1.1.1.1 example.com          # 問 Cloudflare 的 1.1.1.1
dig @8.8.8.8 example.com          # 問 Google 的 8.8.8.8
```

> **CNAME（別名）和 A（位址）的區別是 DNS 設定最常見的混淆點**。**A 記錄**直接把域名指向 IP（`example.com → 93.184.216.34`）。**CNAME** 把域名指向**另一個域名**（`www.example.com → example.com`），解析時要再查那個域名的 A 記錄——這是「別名」。CNAME 常用於 CDN（你的 `www` CNAME 到 CDN 的域名，CDN 再依使用者位置回不同 IP）。關鍵限制：**根域名（如 `example.com` 本身）不能用 CNAME**（技術原因：根域名必須有 SOA/NS 記錄，CNAME 會衝突）——這是設定網域時的常見坑（想把根域名 CNAME 到某服務卻不行，要用 A 記錄或 CNAME flattening）。其他重要記錄：**MX**（郵件去哪，設錯就收不到信）、**TXT**（SPF/DKIM 防偽造郵件、域名所有權驗證——Google/AWS 常要你加 TXT 證明你擁有域名）、**NS**（誰是這個域名的權威 DNS，改 NS = 換 DNS 託管商）。理解這些記錄類型，你才能設定網站、郵件、CDN——這是 Part 8 部署服務（Ch 36）的前置知識。

## TTL 與快取:「為什麼改了 DNS 還沒生效」

```
DNS 快取與 TTL（這是「DNS 故障」的核心）：

  每個 DNS 記錄有 TTL（存活時間，如 3600 秒）
  解析器快取這個記錄，TTL 內都用快取的值
        │
  你改了 DNS 記錄（如把 A 記錄指向新 IP）：
    新的查詢 → 拿到新 IP ✓
    但「已經快取舊值」的解析器 → TTL 過期前還給舊 IP！
        │
  → 這就是「改了 DNS 還沒生效」的原因
    全球各地的解析器快取在不同時間過期
    要等「最長 TTL」過去，所有快取才更新完
        │
  多層快取（每層都可能快取，每層都要過期）：
    瀏覽器快取 → OS 快取 → 遞迴解析器快取 → ...
        │
  最佳實踐：要改 DNS 前，「先」把 TTL 調短（如改成 300 秒）
    等舊的長 TTL 過期後再改記錄 → 切換快（新值快速生效）
```

```bash
# 看記錄的 TTL（dig 輸出的數字）
dig example.com
# example.com.   3600   IN   A   93.184.216.34
#                ↑ TTL（這個記錄還會被快取 3600 秒）

# 連續查兩次看 TTL 倒數（快取中）
dig example.com +noall +answer
dig example.com +noall +answer    # TTL 數字變小了（快取在倒數）

# 清本機 DNS 快取（systemd-resolved）
sudo systemd-resolve --flush-caches   # 或 resolvectl flush-caches
# 繞過快取直接問權威（debug 用）
dig @$(dig example.com NS +short | head -1) example.com
```

> **TTL 和快取是「為什麼改了 DNS 還沒生效」的答案，也是 DNS 切換的關鍵技巧**。每個 DNS 記錄有 **TTL**（如 3600 秒），解析器在 TTL 內都用快取的值。所以當你改 DNS 記錄（如網站搬到新 IP），**新查詢**拿到新 IP，但**已經快取舊值**的解析器在 TTL 過期前還回舊 IP——全球各地的快取在不同時間過期，造成「有些人看到新站、有些人還看到舊站」的過渡期。這就是「DNS 生效要等一段時間」的真相（不是真的「傳播」，是各地快取逐步過期）。**最佳實踐**：要改 DNS 前，**先把 TTL 調短**（如從 3600 改成 300），等舊的長 TTL 過期（所有快取都更新成「短 TTL」的記錄）後，再改實際記錄——這樣切換時所有快取最多 300 秒就更新，切得快。改完穩定後再把 TTL 調回長的（減少查詢量）。這是網站搬遷、災難切換的標準操作。還要注意**多層快取**——瀏覽器、OS（systemd-resolved）、遞迴解析器都可能快取，debug 時要逐層清（`resolvectl flush-caches`）或繞過快取直接問權威伺服器。「It's always DNS」很大程度就是因為這些快取層讓問題難以捉摸。

## DNS 的隱私問題:DoH 與 DoT

```
傳統 DNS 的隱私問題：明文！

  傳統 DNS 查詢是「明文 UDP」（沒加密）：
    你問「example.com 的 IP」→ 整個網路路徑上的人都看得到
    ISP、同網段的人（Ch 3 ARP）、中間設備都知道你在查什麼域名
        │
  問題：
    1. 隱私：你訪問的每個網站，ISP/中間人都看得到（即使你用 HTTPS，
       DNS 查詢還是洩漏了你要去哪個域名）
    2. 竄改：中間人能偽造 DNS 回應（DNS 投毒/劫持）
        │
  解法：加密 DNS
    DoT（DNS over TLS）：DNS 查詢走 TLS（port 853）
    DoH（DNS over HTTPS）：DNS 查詢偽裝成 HTTPS 流量（port 443）
        │
  → DoH/DoT 讓 DNS 查詢加密，ISP/中間人看不到你查什麼
    DoH 還難以封鎖（混在一般 HTTPS 流量裡）
    → 這也是翻牆對抗 DNS 污染的手段（Ch 31）
```

```bash
# 用 DoH 查詢（Cloudflare 的 DoH endpoint）
curl -s -H 'accept: application/dns-json' \
  'https://1.1.1.1/dns-query?name=example.com&type=A'
# {"Answer":[{"name":"example.com","type":1,"data":"93.184.216.34"}...]}
#   → DNS 查詢走 HTTPS，加密，看起來就像一般網頁請求

# 看你的系統有沒有用加密 DNS
resolvectl status              # 看 DNS 設定，是否啟用 DoT
```

> **傳統 DNS 是明文的——即使你用 HTTPS，DNS 查詢仍洩漏「你要去哪個域名」**。這是常被忽略的隱私漏洞：你用 HTTPS（Ch 11）加密了和網站的通訊內容，但**連線前的 DNS 查詢是明文**——ISP、同網段的人（ARP 監聽，Ch 3）、路徑上的中間設備，都看得到你查了 `example.com`（雖然看不到你在上面做什麼，但知道你去了哪）。更糟的是中間人能**偽造 DNS 回應**（DNS 投毒/劫持，把你導到假網站）。解法是**加密 DNS**：**DoT**（DNS over TLS，走 port 853）和 **DoH**（DNS over HTTPS，偽裝成一般 HTTPS 流量走 443）。它們加密 DNS 查詢，ISP/中間人看不到你查什麼。**DoH 還特別難封鎖**——因為它混在一般 HTTPS 流量裡，封鎖者難以區分「這是 DoH 還是正常網頁」（不像 DoT 有專屬 port 853 易封鎖）。這使 DoH 成為**翻牆對抗 DNS 污染**的手段（Ch 31——GFW 會污染 DNS，回假 IP，DoH 繞過它）。瀏覽器（Firefox/Chrome）現在預設或可選 DoH。理解 DNS 的明文問題，你就懂了為什麼隱私倡議者推 DoH/DoT，以及它在審查對抗中的角色。

## 故意弄壞:DNS 故障的各種樣貌

```bash
# 體會「DNS 故障」的不同症狀（debug 的常見情境）

# 1. DNS 解析不了 → 域名連不上，但 IP 直連可以
curl -sI https://example.com --max-time 5          # 用域名
curl -sI https://93.184.216.34 --resolve example.com:443:93.184.216.34 --max-time 5
#   如果第一個失敗、第二個成功 → 問題在 DNS（解析不了），不是連線

# 2. 解析到「錯的」IP（DNS 污染/劫持/快取舊值）
dig example.com +short                              # 看解析到什麼
dig @1.1.1.1 example.com +short                     # 問另一個解析器對照
#   如果兩個解析器給「不同」IP → 可能 DNS 污染或快取不一致

# 3. 解析器掛了 → 所有域名都解析不了
cat /etc/resolv.conf                                # 看用哪個解析器
dig @8.8.8.8 example.com +short                     # 換個解析器試
#   如果換解析器就好 → 原本的解析器有問題

# 4. 慢：DNS 查詢很慢 → 網頁載入「卡在一開始」
dig example.com | grep "Query time"
# ;; Query time: 523 msec     ← 太慢（正常應 <50ms，除非沒快取）
```

> **debug「網站打不開」時，DNS 是第一個嫌疑犯——用「域名 vs IP 直連」快速定位是不是 DNS 問題**。最有用的 debug 技巧：`curl https://example.com`（用域名）失敗，但 `curl --resolve example.com:443:<IP>`（手動指定 IP，繞過 DNS）成功——這就確認**問題在 DNS**（解析失敗或解析到錯的 IP），不是連線本身。然後進一步：`dig example.com` vs `dig @1.1.1.1 example.com`（問不同解析器對照）——如果給不同 IP，可能是 **DNS 污染**（中間人/GFW 給假 IP，Ch 31）或快取不一致；如果你的解析器解析不了但 `@8.8.8.8` 可以，是**你的解析器掛了**（換一個就好）。`dig` 的 `Query time` 看 DNS 是否慢（正常 <50ms，太慢造成「網頁卡在一開始」）。這些是「It's always DNS」的實戰排查——記住先用「域名 vs IP 直連」二分，快速判斷問題在不在 DNS。這是 Ch 16/練習 B 的 debug 技能的重要部分。

## 動手練習

1. 看遞迴查詢：`dig +trace www.example.com`，看根→TLD→權威三層，理解 DNS 階層

2. 查各種記錄：對一個真實網域查 A/AAAA/MX/TXT/NS/CNAME，理解每種記錄存什麼

3. 觀察快取 TTL：連續 `dig` 同域名兩次，看 TTL 倒數（快取在計時）

4. 比較解析器：`dig @8.8.8.8` vs `dig @1.1.1.1` vs 你預設的解析器，看是否一致、誰比較快

5. 跑「故意弄壞」：用 `--resolve` 模擬 DNS vs IP 直連，理解怎麼判斷問題在不在 DNS

## 本章重點整理

- DNS 是分層的「電話簿」：從根→TLD→權威伺服器一層層查，域名的點分結構對應查詢階層
- 遞迴解析器替你跑完整個遞迴查詢並快取結果；`/etc/resolv.conf` 設定問哪個解析器；DNS 通常用 UDP（省握手）
- 記錄類型：A（IPv4）、AAAA（IPv6）、CNAME（別名，根域名不能用）、MX（郵件）、TXT（SPF/驗證）、NS（權威）
- TTL+快取是「改了 DNS 沒生效」的原因——切換前先調短 TTL；多層快取要逐層清
- 傳統 DNS 明文（洩漏你查什麼）→ DoH/DoT 加密；DoH 難封鎖（混在 HTTPS 裡），是翻牆對抗 DNS 污染的手段
- debug 網路第一嫌疑是 DNS：用「域名 vs IP 直連」二分快速定位

## 自我檢核

- [ ] 能解釋 DNS 解析的完整流程（你→解析器→根→TLD→權威）
- [ ] 知道遞迴解析器的角色和快取的重要性
- [ ] 能說出 A/CNAME/MX/TXT/NS 各存什麼，CNAME 的限制
- [ ] 理解 TTL+快取為什麼造成「改了 DNS 沒生效」，以及切換技巧
- [ ] 會用「域名 vs IP 直連」判斷問題在不在 DNS

## 延伸閱讀

### 書籍

- **《DNS and BIND》— Ch 1-4** — Cricket Liu & Paul Albitz（O'Reilly）
  - **讀哪幾章**：Ch 1-2（DNS 架構與運作）、Ch 4（記錄類型）
  - **這本書的定位**：DNS 的權威巨著，把階層查詢和記錄講到極致
  - **前提**：Ch 7（UDP）

### 文章

- **[How DNS works (comic)](https://howdns.works/)** — DNSimple
  - **這篇說什麼**：用漫畫講 DNS 解析流程，極其易懂
  - **讀哪裡**：整個漫畫
  - **為什麼值得讀**：把 DNS 階層查詢視覺化，新手最友善的入門

- **[Julia Evans 的 DNS 文章](https://jvns.ca/categories/dns/)** — Julia Evans
  - **這篇說什麼**：一系列 DNS debug、dig 用法、DNS 怪問題的文章
  - **為什麼值得讀**：把 DNS debug 講得最實用，是「It's always DNS」的實戰指南

### 官方文件

- **[RFC 1034/1035 — Domain Names](https://www.rfc-editor.org/rfc/rfc1034)** — IETF
  - **讀哪裡**：RFC 1034 的概念與機制（階層、遞迴、快取）
  - **為什麼值得讀**：DNS 的原始定義，理解設計哲學

下一章進入 HTTP——從 1.0 到 3 的演進，看網頁背後的協定怎麼從「一個請求一個連線」進化到多路複用和 QUIC。

→ [Ch 10 HTTP 演進（1.0 到 3）](./10-http-evolution.md)
