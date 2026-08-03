# Ch 9 — source/sink/sanitizer 思維

> **目標**：Ch 7 給了 taint 的理論定義，這章把它變成你在真實 codebase 上動手的操作語言。你會學會在一個陌生專案裡**系統性列舉 source 與 sink**、**畫出 candidate flow**、判斷「這條有沒有有效 sanitizer」——這是後面所有工具（Semgrep taint mode、CodeQL global taint）都在自動化的那件事，先用腦袋跑一遍，才知道工具在幫你算什麼、又替你省了什麼。

在 `reading_code` Ch 32 我們教過「找漏洞式讀碼」：盯著一個危險操作往回追它的輸入是不是攻擊者可控。那是一種**直覺**。這章要把直覺形式化成三個名詞——**source（污染源）、sink（危險匯點）、sanitizer（消毒器）**——因為一旦你能用這三個詞描述一個 codebase，你就能把它寫成 query，讓機器一次掃完幾百萬行。手讀是把這套邏輯跑一條路徑；審計是把它跑遍整個 repo。差別只在誰來走圖。

## 三個名詞，一句話定義漏洞

先把定義釘死，全課通用：

- **source（污染源）**：攻擊者能控制其值的資料進入程式的地方。network `recv`、讀檔、環境變數、`argv`、HTTP 參數、反序列化的輸入、IPC/RPC 收到的訊息……只要「值由外部決定」，就是 source。
- **sink（危險匯點）**：把資料交給一個「若資料是惡意的就會出事」的操作。`memcpy` 的長度、`system()` 的命令字串、SQL query 的拼接、`open()` 的路徑、`pickle.loads` 的 bytes……sink 的危險是**類型相關**的：同一份污染資料流到 `memcpy` 是記憶體安全問題，流到 SQL 是注入，流到 `open` 是路徑穿越。
- **sanitizer（消毒器）**：在 source 到 sink 的路徑上，把污染資料變成「對這個 sink 安全」的操作。長度檢查（對 `memcpy`）、`shlex.quote`（對 shell）、參數化查詢（對 SQL）、路徑正規化 + 白名單（對 `open`）。

於是漏洞的定義收斂成一句話，值得你背下來：

> **一條從 source 到 sink、中間沒有對該 sink 有效的 sanitizer 的資料流，就是一個 candidate bug（候選漏洞）。**

「candidate」這個詞是刻意的。這條 flow 存在不代表可利用——path 可能不可達、source 可能其實不可控、sanitizer 可能藏在你沒看到的地方。從 candidate 到確認 bug 的過濾，就是 Ch 12 的 triage。這章先教你**把 candidate 列出來**。

## trust boundary：污染從哪裡開始

source/sink 之所以成立，背後是一個更根本的概念：**trust boundary（信任邊界）**。程式裡的資料分兩種——程式自己產生/控制的（可信），與外部餵進來的（不可信）。trust boundary 就是這兩者的交界線，**source 永遠長在 trust boundary 上**。

```
   外部世界（不可信）          │   程式內部（一旦進來就開始追）
                             │
   network / 攻擊者 ─────────►│ recv(fd, buf, ...)      ← source
   檔案 / 使用者 ────────────►│ fread / read            ← source
   環境 / launcher ─────────►│ getenv("PATH")          ← source
                             │        │
                    trust boundary    ▼  taint 沿 dataflow 傳播
                             │   len = parse(buf)
                             │        │
                             │   memcpy(dst, src, len) ← sink
```

畫 trust boundary 的價值在於：**它幫你決定「追蹤從哪裡起算」**。如果一個值在 boundary 內側被完全重新產生（例如程式自己 `snprintf` 出一個常數），它就不是 source，追它是浪費時間。反過來，最常見的漏報就是**把 boundary 畫太靠內**——你以為某個 struct 欄位是內部狀態，其實它在幾層呼叫之外是從網路 unmarshalling 出來的。boundary 判斷錯，整條 flow 就漏。

trust boundary 不只一條。一個系統裡有很多層：kernel/user 是一條、process/process 是一條、特權/非特權是一條、你的服務/你呼叫的第三方 library 也是一條。**每跨一條邊界，就有一批新的 source**。審計特權程式（setuid、driver、broker）時，「跨特權邊界的 source」是含金量最高的一批。

## 怎麼在真實 codebase 上列舉 source

列舉不是漫無目的地讀，是**按 source 的種類逐項掃**。給你一份可操作的清單，拿到專案就照著 grep：

| source 種類 | C/C++ 常見長相 | web/腳本常見長相 |
|---|---|---|
| network | `recv` `recvfrom` `read(sockfd)` `SSL_read` | HTTP handler 的 `request.params` `req.body` |
| 檔案 | `fread` `read` `mmap` `getline` | `open(path).read()`、上傳檔內容 |
| 環境/啟動 | `getenv` `argv` `main(argc,argv)` | `os.environ` `process.env` `sys.argv` |
| IPC/RPC | `recvmsg`、pipe read、shared mem、ioctl 的 `arg` | gRPC/JSON-RPC handler 參數 |
| 反序列化 | 自寫 parser 讀 buffer | `pickle.loads` `readObject` `unserialize` `JSON.parse`（信任誤用）|
| 資料庫/快取回讀 | — | 從 DB 讀回的欄位（stored XSS/second-order injection 的起點）|

實務手法是：用 `reading_code` Ch 6「找 entry point」的技巧先定位程式入口（`main`、event loop、request dispatcher），再從入口往內，把每個「值來自外部」的地方標成 source。以 redis 為例，`networking.c` 的 `readQueryFromClient` 把 socket 資料讀進 client query buffer，接著 RESP protocol parser（`processMultibulkBuffer` 一類）從 buffer 解出命令與參數——**那個 query buffer 就是 taint 的起點**，之後所有從它解出來的 `argv[]` 都帶污染。（函式名以你 clone 的版本為準，redis 各版本有重構。）

一個關鍵心法：**source 要列到「值進來的第一手」，不是列到你順眼的地方**。很多人把 `argv[2]` 當 source，卻沒意識到它前面 `processMultibulkBuffer` 才是把網路 bytes 變成 `argv` 的那一步。列太淺會讓你之後寫 query 時 source 對不上真正的輸入面。

## 怎麼列舉 sink

sink 的列舉相反——**按危險操作的種類掃**，而且要跨過「顯性 sink」去找「隱性 sink」。顯性的好找：

- **記憶體**：`memcpy` `memmove` `strcpy` `strcat` `sprintf` `alloca`、以及**任何用污染值當索引/長度的陣列存取**。長度與索引才是重點，不是函式名本身。
- **命令執行**：`system` `popen` `exec*` `Runtime.exec` `child_process.exec` `os.system`。
- **注入類**：SQL 字串拼接、`eval`、模板渲染、LDAP/XPath 查詢。
- **檔案/路徑**：`open` `fopen` `unlink` `rename`——污染流進路徑參數 = path traversal。

隱性 sink 是分水嶺，也是踩雷重災區：

- **framework 包裝過的 sink**：ORM 底層還是拼 SQL；logging 框架 `logger.info(userInput)` 若後端會解析格式（Log4Shell 就是 `${jndi:...}` 被 lookup），logging 本身就成了 sink。你看到的是無害的 `log.info`，真正的 sink 藏在框架內部。
- **間接 sink**：污染值不直接進危險函式，而是**改變控制流或設定**——例如污染一個 size 欄位，稍後別處拿它做 `malloc` 再 `memcpy`。source 與 sink 中間隔了狀態，肉眼順讀很容易斷線。
- **反序列化即 sink**：`pickle.loads(untrusted)` 本身就是 sink，因為反序列化過程會實例化/呼叫任意物件。這類 sink 沒有「危險參數」，整個呼叫就是危險的。

## 具體示範：標一段 C code

看一段解析網路封包長度再拷貝的典型 code（風格取自真實 parser，簡化過）：

```c
// 從 socket 讀 header + payload
ssize_t n = recv(fd, hdr, sizeof(hdr), 0);       // (1) SOURCE：hdr 由網路控制
if (n < (ssize_t)sizeof(hdr)) return -1;

uint32_t len = ntohl(*(uint32_t *)hdr);          // (2) len 帶污染（從 hdr 解出）

char *payload = malloc(len);                     // (3) 污染值控制配置大小
if (!payload) return -1;

recv(fd, payload, len, 0);                        // (4) 再讀 len bytes 進 payload

char buf[256];
memcpy(buf, payload, len);                        // (5) SINK：len 未對 256 檢查 → 溢位
```

畫出 candidate flow：

```
(1) recv → hdr        [source]
        │
(2) ntohl(hdr) → len  [taint 傳到 len]
        │
        ├─(3) malloc(len)      [間接 sink：巨大配置 / 整數問題]
        │
(5) memcpy(buf, payload, len)  [sink：len 未被 min(len,256) 這類 sanitizer 約束]
```

有沒有 sanitizer？第 (5) 步前沒有任何 `if (len > sizeof(buf))` 的檢查——**這就是一條完整的 candidate bug（stack buffer overflow）**。修法（= sanitizer）是在 memcpy 前 clamp：`if (len > sizeof(buf)) return -1;`。注意第 (1) 步後那個 `if (n < sizeof(hdr))` **不是**對這個 sink 的 sanitizer——它檢查的是讀了幾 bytes，跟 `len` 的值域無關。**「路徑上有個 if」不等於「有有效 sanitizer」**，這是下一節的重點。

## 具體示範：標一段 web code

```python
@app.route("/download")
def download():
    name = request.args.get("file")           # SOURCE：HTTP 參數
    path = os.path.join("/var/data", name)     # name 汙染 path
    return open(path).read()                   # SINK：path traversal
```

candidate flow：`request.args → name → os.path.join → open`。sanitizer？沒有。`name="../../etc/passwd"` 會讓路徑逃出 `/var/data`（`os.path.join` 遇到 `..` 不會幫你關進去，甚至遇到絕對路徑會整個丟掉前綴，這是常見誤解）。有效 sanitizer 是：正規化後檢查 prefix——`p = os.path.realpath(path); if not p.startswith("/var/data/"): abort(403)`。

## 什麼叫「有效」的 sanitizer

sanitizer 的判定是三者裡最容易出錯的，因為**「有做檢查」跟「檢查對這個 sink 有效」是兩件事**。判一個 sanitizer 是否有效，問三個問題：

1. **它針對的是這個 sink 的危險維度嗎？** 對 `memcpy` 有效的是長度/邊界檢查；對 shell 有效的是 quoting/allowlist；對 SQL 有效的是參數化。用 HTML escape 去防 SQL injection 是無效 sanitizer——防錯了維度。
2. **它在路徑上、且無法繞過嗎？** sanitizer 必須在 source 到 sink 的**每一條**路徑上都執行。只要有一個 branch 繞過它到 sink，等於沒有。
3. **它真的消掉污染，還是只檢查了無關屬性？** 上面 C 例的 `if (n < sizeof(hdr))` 檢查的是「讀夠了嗎」，不是「len 安全嗎」——無效。

工具（Semgrep/CodeQL）把 sanitizer 建模成「taint 傳到這裡就停」的節點。**你把一個無關檢查誤標成 sanitizer，工具就會漏報整條真 flow**——這是自寫 query 時漏報的頭號來源，Ch 12 和 Part 4 會反覆碰到。

## 踩雷集錦

**錯誤直覺：「source 就是 `main` 的 `argv` 和幾個 `recv`，列幾個就夠了。」**
正確認識：source 面通常比你想的大得多。反序列化輸入、從 DB 讀回的值（second-order）、config 檔、環境變數、甚至第三方 library callback 傳給你的參數都可能是 source。source 列太窄 = 攻擊面漏一整塊，後面 query 掃得再乾淨也沒用。列 source 要按「種類清單」逐項掃，不能憑印象。

**錯誤直覺：「sink 就是那幾個危險函式，grep `system`/`strcpy` 就抓完了。」**
正確認識：顯性 sink 只是冰山一角。framework 包裝的 sink（ORM、logging、模板引擎）、間接 sink（污染 size 欄位、污染設定）、反序列化這種「整個呼叫即 sink」的形態，grep 函式名全抓不到。真正產出 CVE 的常是這些隱性 sink。

**錯誤直覺：「路徑上看到一個 `if` 檢查/一個 `sanitize()` 呼叫，這條就安全了。」**
正確認識：三個陷阱同時存在——(a) 檢查的可能是無關維度（檢查長度但 sink 怕的是字元）；(b) 可能有 branch 繞過它；(c) 名字叫 `sanitize` 不代表它對**你這個** sink 有效。把「看到消毒」當「已消毒」是誤標 sanitizer，直接導致工具漏報。判 sanitizer 一律走三問。

**錯誤直覺：「trust boundary 就是網路那條，內部資料都可信。」**
正確認識：一個系統有多層 boundary（特權、process、library）。而且「內部」資料可能是幾層之外從外部 unmarshalling 來的——boundary 畫太靠內，source 就漏。特別在特權程式裡，跨特權邊界的 source 含金量最高，卻最容易被當成「自己人給的資料」放過。

**錯誤直覺：「我把 source 和 sink 標出來、中間連得上，就是 bug。」**
正確認識：那只是 **candidate**。這條 flow 可能 path 不可達、source 其實被上游固定、sanitizer 藏在別處。從 candidate 到確認要走 triage（Ch 12）。反過來也別因為「沒馬上看到完整 flow」就丟掉——間接 sink 的 flow 常是斷開的，需要跨函式接（那正是 CodeQL global taint 的活）。

## 進階延伸

- **source/sink 的「污染型別」**：進階的 taint 系統不是「污染/乾淨」二元，而是帶標籤（tainted-as-shell、tainted-as-sql）。同一份污染對不同 sink 危險性不同，標籤讓你精準匹配 sanitizer（Ch 23 CodeQL flow state 就是這個）。
- **implicit flow（隱式流）**：污染透過控制流而非資料流傳播——`if (secret) x=1 else x=0`，x 沒被直接賦污染值卻洩漏了 secret。多數審計工具**不追** implicit flow（會爆炸），知道這個盲點在哪很重要。
- **source/sink 的方向性**：本章都在追「source → sink」（injection 類）。有些漏洞要反過來追「sink → source」（例如資訊洩漏：敏感資料 source 流到輸出 sink）。同一套框架，換個 source/sink 定義就是不同 bug 類別。

## 本章重點整理

- 漏洞 = **一條 source → sink、中間無有效 sanitizer 的資料流**（candidate bug）。這句是全課的操作定義。
- **source** 長在 **trust boundary** 上，按「種類清單」（network/檔案/環境/IPC/反序列化/DB 回讀）逐項列舉，要列到「值進來的第一手」。
- **sink** 按危險操作種類列，重點是抓**隱性 sink**（framework 包裝、間接 sink、反序列化即 sink），而非只 grep 危險函式名。
- **sanitizer 有效性**走三問：對的維度？路徑上不可繞？真消污染還是檢查了無關屬性？誤標 sanitizer 是漏報頭號來源。
- 手讀是把這套邏輯跑一條路徑，審計是用工具跑遍整個 repo——同一套語言，差在誰走圖。

## 自我檢核

- 不看上文，寫出 source/sink/sanitizer 各自的一句話定義，以及漏洞的那句操作定義。
- 給你一個陌生 C 服務，你會怎麼系統性列出它的 source？列出你會 grep 的至少 5 種 source 種類與對應函式。
- 「路徑上有個 `if (len < 100)`」——你怎麼判斷它是不是對某個 memcpy sink 的有效 sanitizer？走一遍三問。
- 舉三種 grep 危險函式名抓不到的隱性 sink，各說為什麼漏。
- trust boundary 畫太靠內會造成漏報還是誤報？舉一個「內部資料其實是 source」的情境。
- 你標出的 source→sink flow 為什麼只是 candidate 而非確認 bug？從 candidate 到確認缺哪一步？

## 延伸閱讀

- **OWASP，*Source and Sink* / *Data Flow Analysis* 相關頁面**——把 source/sink/sanitizer 的定義與 web 場景對齊。讀「哪些是 source、哪些是 sanitizer」的清單部分。前提：本章。適合把腦中模型跟業界術語校準。
- **`reading_code` Ch 32《漏洞獵殺式讀碼》**——本章的直覺前身。回頭讀它「盯著危險操作往回追」的手法，你會發現那就是「從 sink 反向找 source」的 demand-driven 版本。前提：無，兩章互為表裡。
- **CWE-20《Improper Input Validation》與其子節點**——sanitizer 缺失的官方分類體系。瀏覽它怎麼把「輸入沒驗好」細分成各種 CWE，建立「一個 sink 對應哪些 sanitizer 缺失」的對照。前提：本章。Ch 11 會大量用 CWE 編號。
- **FlowDroid 的 SourcesAndSinks.txt 機制**（PLDI 2014，見 Ch 5 延伸）——看一個真實 taint 工具怎麼**用外部檔案定義 source/sink 清單**，這正是你手工列舉清單的自動化版本，直通 Ch 11 的 sink 目錄與 Ch 23 的 models-as-data。前提：Ch 5。

你現在會對「一條 flow」下判斷了。但真實審計不是拿到 codebase 就開始標 flow——你得先決定**審哪裡**。下一章教你在動手前先建攻擊面地圖、用 offensive 視角選 target，把有限的時間押在最可能出 bug 的地方。

→ [Ch 10 攻擊面建模與 target 選擇](./10-attack-surface-modeling.md)
