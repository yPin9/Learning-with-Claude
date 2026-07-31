# Ch 15 — dig / nslookup

> **目標**：把 DNS 查詢工具 dig 用熟——查各種記錄、`+trace` 看完整遞迴、`+short`/`+noall +answer` 控制輸出、指定解析器對照、反解、debug DNS 問題。Ch 9 講了 DNS 原理，這章把它落到「怎麼用工具查和 debug」。dig 是 DNS 問題的手術刀，掌握它你能精準診斷「It's always DNS」的各種狀況。

> **環境**：Linux（dig，來自 dnsutils/bind-utils）。nslookup 是較舊的替代。

## 為什麼 dig 是 DNS 的手術刀？

Ch 9 說 DNS 是「最常見的故障源」。當你懷疑 DNS 出問題時，需要一個能精準查詢、看到查詢每一步、對照不同解析器的工具——這就是 **dig**（Domain Information Groper）。它比 `nslookup`（較舊）更強大、輸出更清楚，是 DNS debug 的標準工具。

dig 讓你回答所有 DNS 問題：這個域名解析到什麼？哪個解析器給的？完整的遞迴查詢經過哪些伺服器？TTL 還剩多久？權威伺服器怎麼說（繞過快取）？這些是 debug DNS 污染、快取問題、設定錯誤的關鍵。這章把 Ch 9 的原理變成手上的工具操作。

## 先建立直覺:dig 是「問 DNS 並看完整回答」

```
dig 的輸出結構（一次查詢的完整資訊）：

  dig example.com
        │
  ;; QUESTION SECTION:      ← 你問了什麼
  ;example.com.  IN  A
        │
  ;; ANSWER SECTION:        ← 答案（你要的）
  example.com.  3600  IN  A  93.184.216.34
                └TTL┘        └─IP─┘
        │
  ;; AUTHORITY SECTION:     ← 誰是權威（可選）
  ;; ADDITIONAL SECTION:    ← 額外資訊
        │
  ;; Query time: 23 msec    ← 查詢花多久
  ;; SERVER: 192.168.1.1    ← 問了哪個解析器
        │
  → dig 給你「完整的 DNS 回答」，不只 IP
    TTL、用哪個解析器、查多久——都是 debug 線索
```

關鍵心智：dig 給你「完整的 DNS 回答」——不只 IP，還有 TTL（快取多久）、用哪個解析器、查詢花多久。這些都是 debug 線索。相比之下 `ping` 只給你「能不能通」，dig 給你「DNS 怎麼回答的全貌」。

> dig 是 Ch 9（DNS）的工具版。如果對 DNS 的階層查詢、記錄類型、TTL/快取不熟，回看 [Ch 9](./09-dns.md)。這章假設你懂 DNS 原理，專注「怎麼用 dig 查和 debug」。

## dig 的核心用法

```bash
# === 基本查詢 ===
dig example.com                  # 完整輸出（A 記錄）
dig example.com +short           # 只要答案（93.184.216.34）—— 腳本常用
dig example.com +noall +answer   # 只要 ANSWER section（乾淨）

# === 查不同記錄類型（Ch 9）===
dig example.com A +short         # IPv4
dig example.com AAAA +short      # IPv6
dig example.com MX +short        # 郵件伺服器
dig example.com TXT +short       # TXT（SPF/驗證）
dig example.com NS +short        # 權威伺服器
dig example.com CNAME +short     # 別名
dig example.com ANY              # 所有記錄（很多伺服器已禁用 ANY）

# === 指定解析器（對照不同 DNS）===
dig @8.8.8.8 example.com +short  # 問 Google
dig @1.1.1.1 example.com +short  # 問 Cloudflare
dig @192.168.1.1 example.com     # 問你的路由器

# === 反解（IP → 域名）===
dig -x 8.8.8.8 +short            # dns.google

# === 看完整遞迴（Ch 9 的階層查詢）===
dig +trace example.com           # 從根→TLD→權威，看每一層
```

> **`dig +short`（腳本用）和 `dig +trace`（看遞迴）是兩個最該記住的用法**。`+short` 只輸出答案（如 `93.184.216.34`），適合腳本裡用（`ip=$(dig +short example.com)`）。`+trace` 顯示完整的遞迴查詢——從根伺服器→.com TLD→權威伺服器，親眼看 Ch 9 的階層查詢（它繞過快取，自己從根問起，所以能看到完整路徑）。`@解析器` 指定問哪個 DNS——這是 **debug DNS 污染/快取的關鍵**：`dig @8.8.8.8 example.com` vs `dig @你的解析器 example.com`，如果給不同答案，就有問題（污染、快取不一致、或地理性的 CDN 差異）。記住查記錄類型的語法（`dig <域名> <類型>`），這是設定/驗證 DNS 記錄（Ch 36 部署網站時設 A/MX/TXT）的基本操作。`dig` 比 `nslookup` 好（輸出清楚、功能多、適合腳本）——除非在沒有 dig 的環境，否則用 dig。

## 用 dig debug DNS 問題

```bash
# === debug 場景 1：域名解析不了 ===
dig example.com
# 看 ANSWER SECTION 有沒有答案
# status: NOERROR + 有 ANSWER → 正常
# status: NXDOMAIN → 域名不存在（打錯？沒註冊？）
# status: SERVFAIL → 解析器出錯（DNSSEC 失敗？上游問題？）
# 沒回應/timeout → 解析器不通

# === debug 場景 2：解析到「錯的」IP（污染/快取舊值）===
dig example.com +short                # 你的解析器給的
dig @1.1.1.1 example.com +short       # 對照公共解析器
dig @8.8.8.8 example.com +short       # 再對照一個
# 三個不同 → 可能污染（Ch 31）或快取不一致
# 繞過快取直接問權威：
AUTH=$(dig example.com NS +short | head -1)
dig @"$AUTH" example.com +short       # 權威伺服器的「真實」答案

# === debug 場景 3：改了 DNS 還沒生效（TTL/快取，Ch 9）===
dig example.com | grep -A1 'ANSWER SECTION' 
# 看 TTL 數字 —— 還要等這麼多秒快取才過期
dig @權威伺服器 example.com           # 權威已是新值，但快取還舊 → 等 TTL

# === debug 場景 4：DNS 很慢 ===
dig example.com | grep 'Query time'
# ;; Query time: 523 msec    ← 太慢（正常 <50ms）→ 解析器慢或網路問題
```

```
dig 的 status 碼（debug 的關鍵信號）：
  NOERROR    正常（有答案就成功）
  NXDOMAIN   域名不存在（打錯/沒註冊/被刪）
  SERVFAIL   解析器出錯（DNSSEC 驗證失敗、上游掛了）
  REFUSED    解析器拒絕（你沒權限用這個解析器）
        │
  → status 直接告訴你問題類型
    NXDOMAIN = 域名問題，SERVFAIL = 解析器問題
```

> **dig 的 `status` 碼和「對照不同解析器」是 debug DNS 的兩大利器**。`status` 直接分類問題：**NOERROR**（正常）、**NXDOMAIN**（域名不存在——打錯、沒註冊、或被刪）、**SERVFAIL**（解析器出錯——DNSSEC 驗證失敗或上游問題）、**REFUSED**（拒絕——你沒權限用這解析器）。看到 NXDOMAIN 查域名拼寫/註冊狀態，看到 SERVFAIL 換個解析器試（可能是解析器的問題）。**對照不同解析器**（`dig @8.8.8.8` vs `dig @1.1.1.1` vs 你的）是診斷污染和快取的金鑰——答案不一致就有問題。**繞過快取問權威**（先 `dig NS` 找權威伺服器，再 `dig @權威`）能看到「真實的、最新的」答案，對照你的解析器是否在給舊快取——這是 debug「改了 DNS 沒生效」（Ch 9）的關鍵。`Query time` 看 DNS 是否慢（影響「網頁載入卡在一開始」）。這些把 Ch 9 的原理變成可操作的診斷——`dig` 是你診斷「It's always DNS」的手術刀。

## 故意弄壞:看 DNS 污染的樣貌

```bash
# 對照不同解析器，理解「DNS 污染」怎麼被偵測（Ch 31 翻牆相關）

# 正常域名：各解析器應該一致（或只是 CDN 的地理差異）
for dns in 8.8.8.8 1.1.1.1 9.9.9.9; do
    echo -n "@$dns: "
    dig @$dns example.com +short | head -1
done
# 通常一致（或 CDN 給不同但都「對」的 IP）

# 被污染的域名（在某些網路環境）：
# 某些解析器會給「假 IP」（污染）
# 對照公共解析器（8.8.8.8）vs 本地解析器，假 IP 會不一致
# 用 DoH 繞過（Ch 9）：
curl -s 'https://1.1.1.1/dns-query?name=example.com&type=A' \
    -H 'accept: application/dns-json' | grep -o '"data":"[^"]*"'
# DoH 加密，難被污染 → 對照明文 dig 的結果

# 看一個域名有沒有 DNSSEC（防污染的機制）
dig example.com +dnssec | grep -i rrsig
# 有 RRSIG = 有 DNSSEC 簽名（能驗證真偽，防污染）
```

> **「對照不同解析器的答案」是偵測 DNS 污染的方法，這是 Ch 31 翻牆對抗的基礎**。DNS 污染（GFW 等的手段，Ch 31）是「對某些域名回假 IP」——你查 `某被封域名`，污染的解析器（或路徑上的污染）回一個錯的 IP，讓你連到錯的地方或連不上。偵測方法就是**對照**：`dig @8.8.8.8` vs `dig @你的解析器`，如果差異大（不是 CDN 的合理地理差異，而是明顯的假 IP），就是污染。對抗手段：**DoH/DoT**（Ch 9，加密 DNS，污染者看不到也改不了你查什麼）——`curl https://1.1.1.1/dns-query` 用 DoH 查，對照明文 dig 的結果。**DNSSEC**（`dig +dnssec` 看 RRSIG）是另一道防線——它用簽名讓你驗證 DNS 回應的真偽（污染的假回應沒有正確簽名，能被識破）。這些是 Ch 31（翻牆生態）的前置——DNS 污染是審查的第一道手段，DoH/DNSSEC 是對抗它的工具。理解用 dig 偵測污染，你就懂了「為什麼翻牆要先解決 DNS」。注意：本課討論這些是為了理解網路審查的技術原理（教育目的），實際使用須遵守當地法律。

## 動手練習

1. 基本查詢：對一個真實域名查 A/AAAA/MX/TXT/NS，用 `+short` 和完整輸出對照

2. 看遞迴：`dig +trace example.com`，對照 Ch 9 的階層查詢，看根→TLD→權威

3. 對照解析器：`dig @8.8.8.8` vs `dig @1.1.1.1` vs 你的解析器，看是否一致、誰快（Query time）

4. 看 TTL：連續 dig 同域名，看 TTL 倒數（快取計時）

5. 跑「故意弄壞」：對照多個解析器的答案，理解怎麼偵測污染；用 DoH 對照明文查詢

## 本章重點整理

- dig 給「完整的 DNS 回答」：答案 + TTL + 用哪個解析器 + 查詢時間，都是 debug 線索
- 核心用法：`+short`（腳本）、`+trace`（看遞迴）、`@解析器`（對照）、`-x`（反解）、`<域名> <類型>`（查記錄）
- status 碼分類問題：NOERROR（正常）、NXDOMAIN（域名不存在）、SERVFAIL（解析器錯）、REFUSED（拒絕）
- debug 利器：對照不同解析器（偵測污染/快取）、繞過快取問權威（看真實值）、Query time（看慢）
- 對照解析器答案是偵測 DNS 污染的方法；DoH/DNSSEC 是對抗手段（Ch 31 翻牆前置）

## 自我檢核

- [ ] 能用 dig 查各種記錄類型，控制輸出（+short/+noall +answer）
- [ ] 會用 `+trace` 看完整遞迴，`@解析器` 對照不同 DNS
- [ ] 知道 status 碼（NXDOMAIN/SERVFAIL）各代表什麼問題
- [ ] 能用 dig debug「解析不了」「解析到錯 IP」「改了沒生效」
- [ ] 理解怎麼用對照解析器偵測 DNS 污染，DoH/DNSSEC 的作用

## 延伸閱讀

### 官方文件

- **[dig man page](https://linux.die.net/man/1/dig)** — ISC BIND
  - **讀哪裡**：QUERY OPTIONS（所有 +options）
  - **為什麼值得讀**：dig 所有選項的權威，`+trace`/`+dnssec`/`+short` 的完整說明

### 文章

- **[How to use dig](https://www.digitalocean.com/community/tutorials/how-to-use-dig)** — DigitalOcean
  - **這篇說什麼**：dig 的實用範例集，從基礎到 debug
  - **讀哪裡**：整篇
  - **為什麼值得讀**：本章用法的擴充，例子多

- **[Julia Evans 的 dig/DNS debug](https://jvns.ca/blog/2021/12/15/some-ways-dns-can-break/)** — Julia Evans
  - **這篇說什麼**：DNS 各種壞掉的方式和怎麼用 dig 診斷
  - **為什麼值得讀**：把 dig debug 講得最實用

### 書籍

- **《DNS and BIND》— Ch 12 (Troubleshooting)** — Liu & Albitz
  - **讀哪幾章**：Ch 12（用 dig/nslookup debug DNS）
  - **這本書的定位**：DNS 權威，debug 章把 dig 的診斷用法講透

下一章看路徑診斷工具——traceroute/mtr/ping，把 Ch 4 的 TTL/ICMP 知識落到「怎麼看封包經過哪些路由器、哪裡丟包」。

→ [Ch 16 traceroute / mtr / ping](./16-traceroute-mtr-ping.md)
