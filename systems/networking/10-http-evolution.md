# Ch 10 — HTTP 演進（1.0 到 3）

> **目標**：理解 HTTP 的演進——HTTP/1.0/1.1 的請求-回應模型與它的效能瓶頸（隊頭阻塞、連線數限制）、HTTP/2 怎麼用多路複用解決、HTTP/3 為什麼放棄 TCP 改用 QUIC。理解這個演進就理解了「網頁為什麼越來越快」背後的協定創新。HTTP 是你每天用最多的應用層協定，這章把它從「會用」挖到「懂它為什麼這樣設計」。

> **環境**：Linux（curl --http1.1/--http2、nc）。HTTP/3 需要支援的 curl。

## 為什麼 HTTP 值得講「演進」？

HTTP 是 web 的協定——每個網頁、每個 API 都用它。但 HTTP 不是一成不變的，它經歷了三次大改版（1.1→2→3），每次都為了解決前一版的效能瓶頸。理解這個演進，比死記「HTTP 是什麼」有價值得多——你會看到工程師怎麼一步步攻克「網頁載入慢」的問題。

更重要的是，HTTP 的演進是前面所學的綜合應用：HTTP/1.1 的隊頭阻塞、HTTP/2 在 TCP 上的多路複用、HTTP/3 改用 QUIC（UDP）避開 TCP 的隊頭阻塞（Ch 7）——這把 TCP/UDP/隊頭阻塞的知識串起來，落到「真實的網頁怎麼變快」。這章也是理解現代 web 效能（為什麼用 CDN、為什麼合併資源、為什麼 HTTP/2 後不用 sharding）的基礎。

## 先建立直覺:HTTP 是「請求-回應」的對話

```
HTTP 的核心：請求-回應（request-response）

  客戶端（瀏覽器/curl）          伺服器
    │                              │
    │── 請求（request）──────────▶│  GET /index.html HTTP/1.1
    │   方法 路徑 版本             │  Host: example.com
    │   標頭（headers）            │  （我要這個資源）
    │                              │
    │◀──── 回應（response）───────│  HTTP/1.1 200 OK
    │   狀態碼 標頭 內容           │  Content-Type: text/html
    │                              │  <html>...</html>
    │                              │  （這是你要的資源）
        │
  → HTTP 就是「我要這個」「給你這個」的對話
    建立在 TCP（Ch 6）之上（或 HTTP/3 的 QUIC/UDP）
    HTTPS = HTTP + TLS 加密（Ch 11）
        │
  HTTP 是「無狀態」的：每個請求獨立
    （伺服器不記得你上一個請求 → 用 cookie/session 補狀態）
```

關鍵心智：HTTP 是「請求-回應」的對話——客戶端送請求（方法+路徑+標頭），伺服器回回應（狀態碼+標頭+內容）。它是**無狀態**的（每個請求獨立，伺服器不記得你上次問什麼，靠 cookie/session 補狀態）。HTTP 建立在 TCP 之上（HTTP/3 改用 QUIC/UDP），HTTPS 是 HTTP + TLS 加密。

> HTTP 跑在 TCP（Ch 6）之上，HTTP/3 跑在 QUIC/UDP（Ch 7）之上。HTTP 的演進核心是「對抗 TCP 的隊頭阻塞」——如果對隊頭阻塞不熟，回看 [Ch 7](./07-udp-vs-tcp.md)。

## HTTP 請求與回應的結構

```bash
# 用 nc 手打一個 HTTP/1.1 請求（看 HTTP 是純文字）
printf 'GET / HTTP/1.1\r\nHost: example.com\r\nConnection: close\r\n\r\n' | nc example.com 80
# HTTP/1.1 200 OK                    ← 狀態行
# Content-Type: text/html           ← 回應標頭
# Content-Length: 1256
# ...
#                                    ← 空行分隔標頭和內容
# <html>...                         ← 回應內容（body）
#   → HTTP/1.1 是純文字協定！你能手打請求
```

```
HTTP 請求的結構：
  GET /path HTTP/1.1          ← 請求行（方法 路徑 版本）
  Host: example.com          ← 標頭（key: value）
  User-Agent: curl/7.x
  Accept: text/html
  （空行）                    ← 標頭和 body 的分隔
  [body，如 POST 的資料]

  常見方法：GET（取）POST（送）PUT（更新）DELETE（刪）HEAD（只要標頭）

HTTP 回應的結構：
  HTTP/1.1 200 OK            ← 狀態行（版本 狀態碼 訊息）
  Content-Type: text/html   ← 回應標頭
  Content-Length: 1256
  （空行）
  <html>...                 ← body（內容）

  狀態碼分類：
    2xx 成功（200 OK, 201 Created, 204 No Content）
    3xx 重導向（301 永久, 302 暫時, 304 Not Modified=用快取）
    4xx 客戶端錯誤（400 Bad Request, 401 未認證, 403 禁止, 404 找不到）
    5xx 伺服器錯誤（500 內部錯誤, 502 Bad Gateway, 503 服務不可用）
```

> **HTTP/1.1 是純文字協定——你能用 nc 手打請求，這是它易於 debug 但效率不高的根源**。HTTP/1.1 的請求/回應是人類可讀的文字（`GET /path HTTP/1.1`、`HTTP/1.1 200 OK`），你能用 `nc` 手打請求看回應。這個「文字化」設計讓 HTTP 極易 debug（curl/nc/瀏覽器開發者工具都能直接看）和擴展（加個新標頭就行），是 HTTP 成功的原因之一。但純文字也有代價——標頭重複又冗長（每個請求都帶一堆相同的 User-Agent/Accept/Cookie），HTTP/2 因此引入標頭壓縮（後述）。**狀態碼**是 HTTP 的語言：2xx 成功、3xx 重導向（301 永久搬家/302 暫時/304 用你的快取）、4xx 你的錯（404 找不到/403 禁止/401 要登入）、5xx 伺服器的錯（500 程式爆了/502 上游壞了/503 過載）。記住這些分類，看到狀態碼就知道問題大概在哪邊（4xx 查你的請求，5xx 查伺服器）。**方法**（GET 取/POST 送/PUT 更新/DELETE 刪）對應 RESTful API 的語意。理解 HTTP 的文字結構，你就能 debug 任何 web 問題。

## HTTP/1.1 的瓶頸

```
HTTP/1.1 的效能問題（為什麼需要 HTTP/2）：

  問題 1：一個連線一次只能處理一個請求-回應
    要載入網頁的 100 個資源（圖/CSS/JS）→ 要排隊？
        │
  HTTP/1.1 的部分解法：
    - keep-alive：連線重用（不用每個請求重新 TCP 握手）✓
    - 但同一連線仍是「一個請求完才能下一個」（隊頭阻塞）
        │
  瀏覽器的 workaround：開多個 TCP 連線（通常 6 個/域名）
    → 6 個資源能並行，但：
      - 每個連線都要 TCP 握手 + 慢啟動（Ch 6）
      - 只有 6 個並行，第 7 個要等
      - 開發者搞「domain sharding」（拆多個域名繞過 6 個限制）
        │
  問題 2：標頭冗長且重複
    每個請求都帶一堆相同的標頭（cookie/user-agent...）→ 浪費頻寬
        │
  → 這些問題在「網頁有上百個資源」的現代 web 變嚴重
    HTTP/2 來解決
```

```bash
# 看 HTTP/1.1 的 keep-alive（連線重用）
curl -v --http1.1 https://example.com 2>&1 | grep -i 'connection\|http/'
# 強制每次新連線 vs keep-alive 的差別（多個請求時 keep-alive 快很多）

# 觀察一個現代網頁有多少資源（為什麼 HTTP/1.1 的 6 連線不夠）
# 瀏覽器開發者工具 Network 面板 → 看一個網頁載入幾十上百個資源
```

> **HTTP/1.1 的核心瓶頸是「一個連線一次一個請求」（應用層隊頭阻塞），瀏覽器只能用「開多個連線」勉強繞過**。HTTP/1.1 加了 **keep-alive**（連線重用，不用每個請求重新 TCP 握手），是一大進步。但同一連線仍是「一個請求-回應完成才能下一個」——這是**應用層的隊頭阻塞**（一個慢請求卡住後面的）。瀏覽器的 workaround 是**對每個域名開多個 TCP 連線**（通常 6 個），讓 6 個資源並行。但這有問題：每個連線都要 TCP 握手+慢啟動（Ch 6，浪費）、只有 6 個並行（現代網頁有上百個資源，第 7 個就要等）、開發者被迫搞 **domain sharding**（把資源拆到多個子域名，繞過 6 連線限制——醜陋的 hack）。加上**標頭冗長重複**（每個請求帶一堆相同的 cookie/user-agent，浪費頻寬）。這些問題在「網頁越來越複雜（上百資源）」的現代變嚴重——一個網頁載入慢，很大程度是這些連線管理的開銷。HTTP/2 就是來根治這些的。

## HTTP/2:多路複用

```
HTTP/2 的核心創新：多路複用（multiplexing）

  HTTP/1.1：一個連線一次一個請求（排隊）
  HTTP/2：一個連線「同時」處理多個請求！
        │
  怎麼做到：把資料切成「frame」，每個 frame 標記屬於哪個「stream」
    一個 TCP 連線上，多個 stream 的 frame 交錯傳輸
    請求 1 的 frame、請求 2 的 frame、請求 1 的... 混在一起傳
    接收方按 stream ID 重組
        │
  好處：
    - 一個連線搞定所有請求（不用開 6 個，不用 sharding）
    - 沒有應用層隊頭阻塞（請求並行，慢的不卡快的）
    - 標頭壓縮（HPACK）：重複的標頭只傳一次
    - Server Push（伺服器主動推資源，後來較少用）
        │
  但 HTTP/2 還有個隱藏問題：
    它跑在「一個 TCP 連線」上，TCP 仍保證有序
    → TCP 層的封包丟失，會卡住「所有」stream（TCP 隊頭阻塞！）
    → 應用層解決了，但 TCP 層的隊頭阻塞還在 → HTTP/3 來解決
```

```bash
# 用 HTTP/2 連線（curl --http2）
curl -v --http2 https://example.com 2>&1 | grep -i 'http/\|using'
# * Using HTTP2, server supports multiplexing
# > GET / HTTP/2
#   → 一個連線多路複用

# 看一個網站支援哪些 HTTP 版本
curl -sI --http2 https://www.cloudflare.com | head -1   # HTTP/2 200
curl -sI --http3 https://www.cloudflare.com | head -1   # HTTP/3 200（如果 curl 支援）
```

> **HTTP/2 用「多路複用」根治了應用層隊頭阻塞，但暴露了「TCP 層隊頭阻塞」這個更深的問題**。HTTP/2 的核心是**多路複用**——把資料切成 frame，每個 frame 標記屬於哪個 **stream**（請求），一個 TCP 連線上多個 stream 的 frame 交錯傳輸，接收方按 stream ID 重組。這讓「一個連線同時處理多個請求」成真——不用開 6 個連線、不用 domain sharding（HTTP/2 後這些 hack 反而有害，該移除）、慢請求不卡快請求。加上 **HPACK 標頭壓縮**（重複標頭只傳一次，省頻寬）。**但 HTTP/2 有個隱藏的致命傷**：它跑在「一個 TCP 連線」上，而 TCP 保證有序交付（Ch 6）——所以如果 **TCP 層某個封包丟了**，TCP 會卡住等它重傳，這會卡住**該連線上所有 stream**（即使其他 stream 的資料已經到了）。這就是 **TCP 層的隊頭阻塞**——HTTP/2 解決了應用層的隊頭阻塞，卻把問題推到了它無法控制的 TCP 層。在丟包的網路（行動網路）上，HTTP/2 的單連線反而可能比 HTTP/1.1 的多連線更糟（多連線時一個連線丟包不影響其他）。這個「TCP 隊頭阻塞」是 HTTP/3 改用 QUIC 的根本動機。

## HTTP/3:放棄 TCP，改用 QUIC

```
HTTP/3 = HTTP over QUIC（QUIC 在 UDP 上，Ch 7/39）

  HTTP/2 的問題：TCP 層隊頭阻塞（單 TCP 連線，丟包卡所有 stream）
        │
  HTTP/3 的解法：不用 TCP，用 QUIC（建在 UDP 上）
    QUIC 有「獨立的 stream」—— 每個 stream 自己管順序
    → 一個 stream 丟包，「只」卡那個 stream，不影響其他
    → 真正解決隊頭阻塞（連 TCP 層的都解決了）
        │
  QUIC 的其他好處（Ch 39 詳述）：
    - 握手快：QUIC 把傳輸握手 + TLS 握手合併（1-RTT，甚至 0-RTT）
      （對比 TCP 握手 + TLS 握手 = 2-3 RTT）
    - 連線遷移：換網路（WiFi→4G）連線不斷（QUIC 用連線 ID 不綁 IP）
    - 在用戶空間：更新快，不受中間設備干擾
        │
  → HTTP/3 是「為了解決 TCP 的根本限制」而生
    它的代價：UDP 在某些網路被限速/封鎖，要 fallback 到 HTTP/2
```

```
三代 HTTP 對照：

  特性          HTTP/1.1      HTTP/2         HTTP/3
  傳輸層        TCP           TCP            QUIC(UDP)
  多路複用      ✗(開多連線)   ✓(單連線)      ✓(單連線)
  應用層隊頭阻塞 有            無             無
  TCP層隊頭阻塞 有            有(致命傷)     無(QUIC獨立stream)
  標頭壓縮      ✗             ✓(HPACK)       ✓(QPACK)
  握手          TCP+TLS慢     TCP+TLS        QUIC合併(快)
  連線遷移      ✗             ✗              ✓
  格式          文字          二進位         二進位
```

> **HTTP/3 放棄 TCP 改用 QUIC，根治了 TCP 層隊頭阻塞——這是傳輸層二十年最大的變革**。HTTP/2 的 TCP 隊頭阻塞無法在 TCP 內解決（TCP 的有序保證是 kernel 行為），所以 HTTP/3 做了激進的決定：**不用 TCP，改用 QUIC**（建在 UDP 上，Ch 7/39）。QUIC 有「**獨立的 stream**」——每個 stream 自己管順序，一個 stream 丟包只卡那個 stream，不影響其他。這真正解決了隊頭阻塞（連 TCP 層的都解決了）。QUIC 還順帶解決了 TCP 的其他老問題：**握手快**（傳輸+TLS 握手合併成 1-RTT，甚至重連 0-RTT，對比 TCP+TLS 的 2-3 RTT，這對行動網路的高延遲影響巨大）、**連線遷移**（換 WiFi 到 4G 連線不斷，因為 QUIC 用連線 ID 不綁 IP，對比 TCP 連線綁 IP，換網路就斷）。代價：UDP 在某些網路被限速或封鎖（防火牆/中間設備對 UDP 不友善），所以要能 fallback 回 HTTP/2。HTTP/3 已被主流瀏覽器和 CDN（Cloudflare/Google）廣泛採用。這個演進的脈絡——HTTP/1.1（應用層隊頭阻塞）→ HTTP/2（解決應用層但暴露 TCP 層）→ HTTP/3（用 QUIC 根治）——完美展示了工程怎麼層層攻克問題。Ch 39 會深入 QUIC 的細節。

## 故意弄壞:看 HTTP 狀態碼診斷問題

```bash
# 用狀態碼診斷不同的問題
curl -sI https://example.com/nonexistent | head -1
# HTTP/2 404      ← 找不到（你的路徑錯）

curl -sI https://httpbin.org/status/500 | head -1
# HTTP/2 500      ← 伺服器錯誤（伺服器程式爆了）

curl -sI https://httpbin.org/status/301 -L | grep -i 'http/\|location'
# 301 + Location: ...   ← 重導向（資源搬家了，-L 跟隨）

# 看完整的請求-回應交握（-v 詳細）
curl -v https://example.com 2>&1 | grep -E '^[<>]'
# > GET / HTTP/2          ← 你送的請求（>）
# > host: example.com
# < HTTP/2 200            ← 伺服器的回應（<）
# < content-type: text/html
#   → 完整看到請求和回應的標頭

# 502/504 的常見場景（反向代理 debug，Ch 36）
# 502 Bad Gateway：nginx 連不上後端（後端掛了/沒啟動）
# 504 Gateway Timeout：後端太慢，nginx 等到 timeout
```

> **HTTP 狀態碼是診斷 web 問題的第一手線索——4xx 查你的請求，5xx 查伺服器**。狀態碼直接告訴你問題的大方向：**404**（找不到）→ 你的路徑/URL 錯，或資源真的不存在；**403**（禁止）→ 權限問題；**401**（未認證）→ 要登入/token；**301/302**（重導向）→ 資源搬家了（用 `curl -L` 跟隨）；**500**（內部錯誤）→ 伺服器程式爆了（查伺服器 log）；**502**（Bad Gateway）→ 反向代理（nginx）連不上後端（後端掛了/沒啟動，Ch 36 常見）；**503**（服務不可用）→ 過載或維護中；**504**（Gateway Timeout）→ 後端太慢，代理等到 timeout。`curl -v` 看完整的請求-回應標頭（`>` 是你送的、`<` 是伺服器回的），是 debug web 問題的核心工具。記住「4xx 是客戶端（你）的問題、5xx 是伺服器的問題」這個分界——它立刻把你的排查方向縮小一半。Ch 36 部署 nginx 時，502/504 是最常遇到的（反向代理連後端的問題）。

## 動手練習

1. 手打 HTTP：用 `nc example.com 80` 手打 `GET / HTTP/1.1`，看純文字的請求-回應

2. 比較版本：`curl --http1.1` vs `--http2` vs `--http3`（如果支援）連同一網站，看版本協商

3. 看狀態碼：用 httpbin.org/status/<code> 觸發各種狀態碼，理解 2xx/3xx/4xx/5xx

4. 看請求-回應：`curl -v` 一個網站，找出請求標頭（>）和回應標頭（<）

5. 觀察多路複用：瀏覽器開發者工具 Network 面板，看 HTTP/2 網站怎麼在一個連線載入多個資源

## 本章重點整理

- HTTP 是「請求-回應」的無狀態對話（方法+路徑+標頭 → 狀態碼+標頭+內容）；HTTP/1.1 是純文字易 debug
- 狀態碼：2xx 成功、3xx 重導向、4xx 客戶端錯誤、5xx 伺服器錯誤——4xx 查請求、5xx 查伺服器
- HTTP/1.1 瓶頸：一連線一次一請求（應用層隊頭阻塞），瀏覽器靠開 6 個連線+domain sharding 勉強繞過
- HTTP/2 多路複用根治應用層隊頭阻塞（單連線多 stream），但暴露 TCP 層隊頭阻塞（單 TCP，丟包卡所有 stream）
- HTTP/3 用 QUIC（UDP）根治 TCP 隊頭阻塞，順帶快握手+連線遷移——傳輸層二十年最大變革

## 自我檢核

- [ ] 能說出 HTTP 請求/回應的結構和常見狀態碼分類
- [ ] 理解 HTTP/1.1 的隊頭阻塞和瀏覽器的多連線 workaround
- [ ] 能解釋 HTTP/2 的多路複用，以及它為什麼還有 TCP 層隊頭阻塞
- [ ] 知道 HTTP/3 為什麼改用 QUIC，解決了什麼
- [ ] 會用狀態碼和 curl -v 診斷 web 問題

## 延伸閱讀

### 必讀資源

- **[HTTP/3 explained](https://http3-explained.haxx.se/)** — Daniel Stenberg（curl 作者，免費線上書）
  - **讀哪裡**：why-http3、why-quic 那幾章；HTTP/1.1→2→3 的演進
  - **這本書的定位**：HTTP/3 和 QUIC 的權威解釋，curl 作者寫的，本章演進脈絡的完整版
  - **前提**：Ch 6-7

- **[High Performance Browser Networking — HTTP 章](https://hpbn.co/)** — Ilya Grigorik（免費線上）
  - **讀哪裡**：HTTP/1.X、HTTP/2 那幾章
  - **為什麼值得讀**：把 HTTP 的效能問題和優化講到極致，含真實的延遲分析

### 文章

- **[HTTP/2 與 HTTP/3 的隊頭阻塞](https://www.cloudflare.com/learning/performance/http2-vs-http1.1/)** — Cloudflare
  - **這篇說什麼**：HTTP/1.1 vs HTTP/2 的效能對比，多路複用的圖解
  - **為什麼值得讀**：本章「多路複用」和「TCP 層隊頭阻塞」的視覺化

### 官方文件

- **[RFC 9110/9112/9113/9114 — HTTP](https://www.rfc-editor.org/rfc/rfc9110)** — IETF
  - **讀哪裡**：RFC 9110（HTTP 語意：方法/狀態碼/標頭）；9112=HTTP/1.1、9113=HTTP/2、9114=HTTP/3
  - **為什麼值得讀**：HTTP 的權威定義（2022 重新整理版）；查狀態碼/方法的確切語意

下一章把 HTTPS 的 S 講透——TLS 怎麼在 TCP 上建立加密通道、憑證和 CA 怎麼防止中間人、TLS 1.3 怎麼變快。這是現代網路安全的基石。

→ [Ch 11 TLS 與 HTTPS](./11-tls-https.md)
