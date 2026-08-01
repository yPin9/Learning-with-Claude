# 練習 A — 偵察與架構地圖

> **目標**：把 Part 2（Ch 5–11）學到的整套攻堅 SOP 綜合成一次實戰演練。你會拿一個從沒看過的中型 C 專案，**限時 60 分鐘**，只靠工具與方法，產出一份能讓「另一個工程師照著就能上手」的偵察報告。這不是讀懂全部——是用最少的閱讀，換取最多的結構理解。時限是重點：偵察是速度技能，逼自己收斂。

## 背景與動機

前面十一章你分開學了每一招：`cloc` 體檢、找 entry point、畫架構地圖、追 data flow、假設驅動。分開練會，合起來用是另一回事——真實情境下沒有人告訴你「現在該用哪一招」，你得自己排程：先幹嘛、再幹嘛、什麼時候該停。

這個練習模擬最常見的真實任務：**你被指派接手／貢獻／稽核一個你完全沒背景的專案，一小時後要跟人報告「這東西怎麼運作」。** 逆向工程師拿到一顆陌生 binary 時做的事——找 entry、抓主迴圈、認出關鍵資料結構、畫出模組邊界——你要在 source 層面複製一遍，而且更快，因為你有符號、有註解、有目錄名這些 binary 沒有的線索。

偵察報告的價值不在完整，在**正確的骨架**。一份好的 60 分鐘偵察，抵得過三天沒方法的亂讀。

## 任務規格

### 選擇偵察對象

二選一，clone 下來（淺 clone 就夠，偵察不需要歷史）：

```bash
cd ~/reading_code_lab

# 選項 A：curl —— HTTP/傳輸協定客戶端，C，lib 十二萬行、CLI 一萬五
git clone --depth 1 https://github.com/curl/curl.git

# 選項 B：sqlite —— 嵌入式資料庫引擎，C，模組化 src + amalgamation
git clone --depth 1 https://github.com/sqlite/sqlite.git
```

建議第一次做選 **curl**：它有清楚的 `lib/`（函式庫）vs `src/`（CLI）分界、多協定的模組化結構、明確的 public API 邊界，非常適合練「認出架構」。sqlite 更硬（大量 amalgamation、VDBE 虛擬機），適合你想加難度時再打。**本參考解答以 curl 8.22.0-DEV 實跑示範。**

### 你要產出的六件東西

計時開始，60 分鐘內完成一份 markdown 報告，含以下六節。**每一節都要附你實際跑的指令**——報告要能被複現，不能只有結論。

1. **規模體檢**：`cloc` 輸出。全樹 + 你判斷的核心目錄各一份。用數字回答：主語言是什麼？多大？註解勤不勤？有沒有大量非 code（測試資料、產生檔）？
2. **目錄地圖**：頂層目錄各是幹嘛的（一句話）。標出「核心邏輯在哪個目錄」——這是你接下來所有精力該投的地方。
3. **進入點**：所有 `main`（或等價 entry）。分辨哪個是「真正的主程式」、哪些是工具／測試 harness。追出主程式呼叫的第一層關鍵函式。
4. **核心資料結構**：找出貫穿整個系統的 1–3 個「上帝結構」（god struct）——那個被到處傳遞、裝著所有狀態的 struct。列出它最關鍵的幾個欄位。
5. **架構 ASCII 圖**：一張圖，畫出「資料從進入點流到哪、經過哪些層、關鍵結構在哪」。不要畫全部——畫主幹。
6. **五個「怎麼運作」問答**：自問自答五個關於這專案運作方式的具體問題（不是「它是什麼」，是「它怎麼做到 X」）。每題附你怎麼查到答案的指令。

### 驗收標準

- [ ] 六節齊全，每節有**真實指令 + 真實輸出片段**（不是憑印象寫）
- [ ] 架構圖能讓沒看過這專案的人在 30 秒內知道「資料主幹怎麼走」
- [ ] 進入點那節正確分辨了「真 main」與「假 main」
- [ ] 至少一個問答題的答案是你**一開始猜錯、查了才修正**的（證明你在驗證假設，不是腦補）
- [ ] 全程 ≤ 60 分鐘（超時也要記錄你花了多久、卡在哪）

## 期望輸出範例

一份合格報告的骨架長這樣（節錄，完整版在參考解答）：

```
# curl 偵察報告 (8.22.0-DEV, 60min)

## 1. 規模體檢
- 全樹 C 186k 行 / lib 117k / src(CLI) 15.6k
- 註解:code ≈ 1:4，算勤勞
- XML 118k 行、Markdown 61k 行 → 大量文件與測試資料，不是核心

## 2. 目錄地圖
lib/    ← 核心！libcurl 本體，137 個 .c
src/    ← curl CLI 工具，包在 libcurl 外
include/curl/ ← public API 標頭
...

## 3. 進入點
- 真 main: src/tool_main.c:143 → operate()
- 假 main: src/curlinfo.c（診斷工具）
- lib 沒有 main，入口是 public API curl_easy_perform()

## 4. 核心結構
struct Curl_easy   ← 上帝結構，一次傳輸的所有狀態
struct connectdata ← 一條連線
struct UserDefined ← 使用者設的所有選項

## 5. 架構圖
（見下）

## 6. 五問
Q: 一個 http:// URL 怎麼被分派到 HTTP 實作的？
A: URL scheme → Curl_get_scheme() → struct Curl_scheme 表 → .run ...
```

## 如果你卡住了

三個方向，卡住時照順序試：

1. **找不到「核心目錄」在哪**：別用讀的，用數的。`cloc` 逐目錄跑一遍，行數最多、又不是測試／文件的那個目錄，九成是核心。curl 就是 `lib/`；很多專案是 `src/` 或 `core/`。目錄名 `test`/`docs`/`examples`/`third_party`/`vendor` 直接跳過。

2. **找到一堆 `main` 分不清哪個是真的**：真正的主程式通常在名字最「中性」的檔案（`main.c` / `tool_main.c` / `server.c`），而且它會呼叫一個叫 `operate` / `run` / `serve` / `main_loop` 之類的核心函式。工具／測試的 `main` 通常在名字有 `test` / `bench` / `info` / 單一功能名的檔案，且被 `#ifdef` 或獨立 build。用 `rg -n "int main" src` 全列出來，逐一看檔名就有直覺。

3. **找不到「上帝結構」**：從 entry point 往下追第一層函式的參數。那個被幾乎每個核心函式都當第一個參數傳來傳去的 struct，就是它。curl 是 `struct Curl_easy *data`；redis 是 `client *c` 和全域 `server`；很多專案叫 `context` / `ctx` / `state` / `session`。`rg -n "struct Curl_easy \*" lib | wc -l` 數它出現幾次，數字大得嚇人就對了。

## 實作步驟建議

把 60 分鐘切成五段，每段有明確產出，時間到就往下走（別在一節鑽太深，偵察階段深度是敵人）：

### Step 1（0–10 min）規模體檢 + 目錄地圖

```bash
cd ~/reading_code_lab/curl
cloc --quiet .                    # 全樹體檢
ls -d */                          # 頂層目錄
cloc --quiet lib/ ; cloc --quiet src/   # 核心目錄各別體檢
```

看行數分布判斷核心在哪，一句話標注每個頂層目錄。**不要打開任何 .c 檔。**

### Step 2（10–25 min）進入點 + 第一層

```bash
rg -n "int main" src lib          # 全部 entry
sed -n '143,210p' src/tool_main.c # 讀真 main 的 body（只讀 main！）
```

從 main 讀出它呼叫的第一層關鍵函式（通常 3–6 個），記下來。這是你的「主幹起點」。

### Step 3（25–40 min）核心資料結構

先找「上帝結構」定義在哪個 header，再讀它的欄位（只讀 struct 定義，別讀用到它的函式）：

```bash
rg -n "struct Curl_easy \{" lib/urldata.h
sed -n '1217,1290p' lib/urldata.h
```

列出 3 個最關鍵結構 + 每個最重要的幾個欄位。

### Step 4（40–52 min）畫架構圖 + 挖分派機制

現在你有：入口、第一層函式、核心結構。把它們接成一條主幹畫成 ASCII。畫的過程你會發現缺口（「http 怎麼變成實際的 HTTP code？」），這缺口就是你第 6 節的問題來源。用 `rg` 挖答案。

### Step 5（52–60 min）五問收尾 + 檢查

把畫圖時冒出的疑問整理成五個「怎麼運作」問題，逐一用一兩條指令查證作答。**至少留一題是你猜錯的**，誠實記錄「我原本以為 X，查了發現是 Y」。

## 完整參考解答

**先自己做完再看。** 偵察的價值在你親手排程、親手撞牆、親手修正假設的過程；看完解答再去做，你只是在抄一份地圖，練不到「自己畫地圖」這件事本身。

以下是我對 **curl 8.22.0-DEV**（commit `5275734`）實跑一遍的完整偵察報告，所有指令與輸出都是真的。

<details>
<summary>點開完整偵察報告（curl，實跑）</summary>

---

### curl 偵察報告 — 8.22.0-DEV，實測耗時約 55 分鐘

#### 1. 規模體檢

全樹：

```
$ cloc --quiet .
Language                             files          blank        comment           code
---------------------------------------------------------------------------------------
C                                      757          29619          42641         186693
XML                                   2060          11681            107         118911
Markdown                               926          21532              0          61742
Perl                                    87           3183           5447          22528
C/C++ Header                           257           3815          11662          15911
m4                                      23           1745           1681          13878
Python                                  52           1556           2118          12261
...
```

**讀出來的三件事：**
- 主語言 C，**18.7 萬行**，這是一個中型專案（比 redis 十萬行大一些，比 kernel 小兩個數量級）。
- C 註解 42641 / code 186693 ≈ **1:4.4**，勤勞，讀起來有旁白。
- **警訊：XML 11.9 萬行、Markdown 6.1 萬行。** 這兩個加起來比 C 還多。這不是核心邏輯——是文件（每個選項一個 `.md`）與測試資料（`tests/data/` 大量 XML）。偵察時**要能一眼把這些排除**，否則你會被 18 萬行 C 之外的 20 萬行雜訊淹死。

核心目錄各別體檢，確認「核心在 lib/」：

```
$ cloc --quiet lib/
C                                      196          17114          21278         117096
C/C++ Header                           190           2596           8036          10282
$ cloc --quiet src/
C                                       43           1807           2868          15615
C/C++ Header                            45            237           1226           1598
```

`lib/` 11.7 萬行 C，`src/` 只有 1.56 萬行。結論很清楚：**libcurl（lib/）是本體，src/ 只是包在外面的 CLI 殼。** 我的精力 90% 該放 `lib/`。

#### 2. 目錄地圖

```
$ ls -d */
CMake/  LICENSES/  docs/  include/  lib/  m4/  projects/  scripts/  src/  tests/
```

| 目錄 | 是什麼 | 偵察優先級 |
|---|---|---|
| **`lib/`** | **libcurl 本體，137 個 .c——核心邏輯全在這** | ★★★ 主戰場 |
| `include/curl/` | public API 標頭（`curl.h`、`easy.h`…），對外契約 | ★★ 讀 API 邊界用 |
| `src/` | `curl` CLI 工具，把 libcurl 包成命令列程式 | ★★ entry point 在這 |
| `tests/` | 測試套件 + 大量 XML 測資（那 11 萬行 XML 的來源） | ✗ 跳過 |
| `docs/` | 文件（那 6 萬行 markdown） | ✗ 跳過（但 `docs/INTERNALS` 值得回頭看） |
| `m4/` `CMake/` `projects/` `scripts/` | build 系統、autoconf 巨集、IDE 專案檔、輔助腳本 | ✗ 跳過（Ch 21 才碰 build） |
| `LICENSES/` | 授權 | ✗ |

`lib/` 內部再看一眼分層（子目錄透露架構）：

```
$ ls -d lib/*/
lib/curlx/  lib/vauth/  lib/vquic/  lib/vssh/  lib/vtls/
```

`v` 前綴是 curl 的慣例，代表「可抽換後端的抽象層」：`vtls`=TLS（OpenSSL/GnuTLS/…可選）、`vquic`=QUIC/HTTP3、`vssh`=SSH（libssh2/libssh）、`vauth`=認證（NTLM/Kerberos/…）。**光看目錄名我就知道 curl 是外掛式後端架構**——這在偵察階段是超高價值的一眼。

#### 3. 進入點

```
$ rg -n "int main" src lib
src/tool_main.c:143:int main(int argc, char *argv[])
src/tool_main.c:87:static int main_checkfds(void)   ← 不是 entry，是輔助函式
src/curlinfo.c:273:int main(int argc, const char **argv)
src/tool_easysrc.c:51:  "int main(int argc, char *argv[])",  ← 這是字串常數，不是 code！
```

分辨：
- **真 main：`src/tool_main.c:143`。** 檔名 `tool_main` 最中性，就是 CLI 主程式。
- **假 main：`src/curlinfo.c:273`** 是獨立的診斷／版本資訊工具，不是主流程。
- **陷阱：`src/tool_easysrc.c:51` 那個「main」是雙引號包起來的字串**——它是 `curl --libcurl` 功能拿來產生範例 C code 用的樣板文字，不是真的 `main` 函式。`rg` 是純文字工具，不懂這差別（Ch 12 會講）。這正是「grep 會被同名字串騙」的活教材。

追真 main 的第一層（只讀 main body，`sed -n '143,210p' src/tool_main.c`）：

```
tool_init_stderr();
win32_init();               // 平台初始化
main_checkfds();            // 檢查 stdin/out/err fd 沒被關
memory_tracking_init();     // debug build 的記憶體追蹤
result = globalconf_init(); //  ← 初始化全域設定
  result = operate(argc, argv);  //  ★ 真正幹活的在這裡
globalconf_free();
```

**主幹起點鎖定：`operate()`（src/tool_operate.c:2395）。** main 幾乎什麼都不做，只是初始化 + 呼叫 `operate`。這是典型的「薄 main」——真邏輯永遠在 main 呼叫的那個核心函式裡。

lib 沒有 main（它是函式庫）。它的「入口」是 **public API**：

```
$ rg -n "curl_easy_perform" lib/easy.c
813: * curl_easy_perform() is the external interface that performs a blocking
816:CURLcode curl_easy_perform(CURL *curl)
```

所以整個系統有兩個入口視角：CLI 走 `main → operate`，程式庫使用者走 `curl_easy_init → curl_easy_setopt → curl_easy_perform`。而 `operate` 底層其實也是去呼叫這套 easy API——**CLI 是 libcurl 的第一號 client。**

#### 4. 核心資料結構

從 easy API 往下追，`rg -n "struct Curl_easy \{" lib/urldata.h` → 讀 struct 定義：

**上帝結構 `struct Curl_easy`（lib/urldata.h:1217）** —— 一個 easy handle，代表「一次傳輸」的全部狀態。關鍵欄位：

```c
struct Curl_easy {
  uint32_t magic;              // CURLEASY_MAGIC_NUMBER，防呆：偵測 user 把 easy/multi handle 傳錯
  CURLMstate mstate;           // 這個傳輸的狀態機狀態
  CURLcode result;             // 上次結果
  struct connectdata *conn;    // ← 這次傳輸用的連線
  struct Curl_multi *multi;    // 所屬 multi handle
  struct SingleRequest req;    // 單一請求的狀態
  struct UserDefined set;      // ← 使用者透過 setopt 設的所有選項
  struct UrlState state;       // 動態狀態（URL 解析、重定向計數…）
  struct Progress progress;    // 進度計量
  struct PureInfo info;        // 統計/回報資訊
};
```

另外兩個關鍵結構（在同一個 `urldata.h`，這檔案就是 curl 的「資料模型聖經」）：
- **`struct connectdata`（:261）** —— 一條實體連線（可跨多次請求重用）。裡面有 `const struct Curl_scheme *scheme`，就是這條連線用哪個協定。
- **`struct UserDefined`（:889）** —— `curl_easy_setopt()` 設的每個選項都存這（URL、header、逾時、認證…）。

**認出 `Curl_easy` 是上帝結構的方法**——它幾乎是每個核心 lib 函式的第一個參數（慣例命名為 `data`）。你讀任何一個 lib 函式，第一個參數 `data` 就裝著這次傳輸的一切，這是你的錨點。

#### 5. 架構 ASCII 圖

```
  curl CLI                                    libcurl 使用者程式
     │                                              │
  main (tool_main.c:143)                    curl_easy_setopt(URL,...)
     │  init + 解析參數                             │
     ▼                                              ▼
  operate() ──────────────┐              ┌── struct Curl_easy (上帝結構)
  (tool_operate.c)        │              │     ├─ set   : UserDefined  (使用者選項)
     把 CLI 參數翻成       └──setopt──────┤     ├─ state : UrlState    (動態狀態)
     easy handle 設定                     │     └─ conn  : connectdata (連線)
     ▼                                    │
  curl_easy_perform() (easy.c:816) ◀──────┘
     │  阻塞式跑完一次傳輸
     ▼
  內部轉成 multi 介面：multi_runsingle() (multi.c:2715)
     │  跑狀態機 (mstate)：解析URL→連線→握手→送請求→收回應
     ▼
  依 URL scheme 分派協定實作
     │
     ▼
  Curl_get_scheme("http") → struct Curl_scheme Curl_scheme_http (protocol.c:132)
     │  .run → struct Curl_protocol (該協定的 do/done/connect 回呼)
     ▼
  ┌──────────┬──────────┬──────────┬─────────┐
  http.c    ftp.c     imap.c    file.c   ...  ← 各協定實作
  (5103行)  (4519)    (2329)
     │
     ▼  底層 I/O 走「連線過濾器鏈」(connection filters, cfilters.c)
  cf-socket → vtls(TLS) → vquic(QUIC) ...   ← 可堆疊的 I/O 層
```

一句話總結主幹：**參數 → 灌進 `Curl_easy` → `perform` → 內部 multi 狀態機 → 依 scheme 分派到協定實作 → 底層過濾器鏈做 I/O。**

#### 6. 五個「怎麼運作」問答

**Q1：一個 `http://` URL 怎麼被分派到 HTTP 的實作？**
我原本猜（錯）：以為有個 `Curl_handler` 表，像 redis 那種函式指標陣列。查了發現這版 curl 的名字不一樣：

```
$ rg -n "Curl_handler" lib          # ← 完全沒有結果！
$ rg -n -e "handler" lib/urldata.h
323:  const struct Curl_scheme *scheme; /* Connection's protocol handler */
```

原來 8.22 版重構成 `struct Curl_scheme`（舊教材/舊記憶會害你找錯符號名——這就是「讀碼是逆向」：以當前 source 為準，不信記憶）。真正機制：

```
$ rg -n "struct Curl_scheme Curl_scheme_" lib/protocol.c | head
lib/protocol.c:132:const struct Curl_scheme Curl_scheme_http = {
lib/protocol.c:146:const struct Curl_scheme Curl_scheme_https = {
...
$ sed -n '243,251p' lib/protocol.h
struct Curl_scheme {
  const char *name;                 // "http"
  const struct Curl_protocol *run;  // ← 真正的實作回呼集合
  curl_prot_t protocol; curl_prot_t family; uint32_t flags; uint16_t defport;
};
```

答：URL 的 scheme 字串（"http"）丟給 `Curl_get_scheme()` 查表，拿到 `Curl_scheme_http`，它的 `.run` 指向該協定的實作。`http` 和 `https` 的 `.run` 都指向同一個 `&Curl_protocol_http`，差別在 flags（`https` 多了 `PROTOPT_SSL | PROTOPT_ALPN`）和預設埠。

**Q2：為什麼阻塞式的 `curl_easy_perform` 底層要繞去 multi 介面？**

```
$ rg -n "curl_multi_perform|multi_runsingle" lib/multi.c | head
2715:static CURLMcode multi_runsingle(...)
2958:CURLMcode curl_multi_perform(...)
```

答：curl 只維護一套非同步狀態機引擎（multi）。`easy_perform` 不是另一套實作，而是「建一個臨時 multi、把自己加進去、阻塞地跑到完成」的薄包裝。**一套引擎兩個門面**——這解釋了為何 `Curl_easy` 裡有 `multi` 和 `multi_easy` 兩個欄位。

**Q3：可抽換的 TLS 後端（OpenSSL vs GnuTLS）怎麼做到的？**
`lib/vtls/` 目錄名已經暗示。答：`vtls/vtls.c` 定義一組抽象介面，各後端（`openssl.c`、`gnutls.c`…）實作同一組回呼，build 時選一個。這是經典的 vtable 式 indirection（Ch 23 主題）。偵察階段光看目錄結構就能斷定，不必讀 code。

**Q4：這 18 萬行 C 裡，哪幾個檔案最該優先讀？**

```
$ wc -l lib/*.c src/*.c | sort -rn | head -6
   5103 lib/http.c
   4519 lib/ftp.c
   4267 lib/multi.c      ← 狀態機引擎，主幹核心
   3213 src/tool_getparam.c
   3000 lib/http2.c
   2964 lib/setopt.c
```

答：`multi.c`（引擎）、`http.c`（最常用協定）、`url.c`（URL 解析與連線建立）是主幹三巨頭。行數不等於重要，但主幹上的大檔通常兩者兼具。

**Q5：一次傳輸的狀態機大概有哪些狀態？**
`Curl_easy.mstate` 型別是 `CURLMstate`（enum）。狀態是一串 `MSTATE_*`（如 CONNECT、RESOLVING、CONNECTING、DO、PERFORMING、DONE），而 `multi_runsingle` 就是這個狀態機的 tick 函式，每次呼叫推進一格。想深讀 curl，讀懂這個 enum 加 `multi_runsingle` 的 switch 就掌握了主幹（Ch 24 狀態機主題的完美案例）。

---

**偵察結束。** 我沒讀懂 curl 任何一個協定的細節，但我現在能對任何人講清楚：curl 怎麼分層、資料怎麼流、核心引擎在哪、該從哪個檔開始深讀。這就是 60 分鐘偵察的產出——**一張能導航的地圖，不是一本讀完的書。**

</details>

## 測試用例

自評你的報告品質，逐項對照：

| 檢查點 | 不合格 | 合格 | 優秀 |
|---|---|---|---|
| 規模體檢 | 只貼 cloc 全樹 | 分核心/非核心目錄 | 從數字讀出「哪些是雜訊該排除」 |
| 目錄地圖 | 列目錄名 | 每個一句話用途 | 從子目錄命名推斷出架構（如 curl 的 `v*` 後端） |
| 進入點 | 找到一個 main | 分辨真/假 main | 抓到「字串裡的假 main」這類陷阱 |
| 核心結構 | 隨便挑個 struct | 找到真正的上帝結構 | 用「出現次數/第一參數慣例」佐證它是上帝結構 |
| 架構圖 | 方框亂連 | 主幹清楚 | 標出關鍵檔名+行號，可直接跳去讀 |
| 五問 | 五個「它是什麼」 | 五個「怎麼運作」 | 至少一題誠實記錄「猜錯→修正」 |

## 延伸挑戰

- **換靶再打一次**：對 sqlite 做同樣的 60 分鐘偵察。你會發現它的架構完全不同（amalgamation、VDBE bytecode 虛擬機、B-tree 儲存層），上帝結構是 `sqlite3` 和 `Vdbe`。同一套 SOP 打不同架構，才知道方法是通用的。
- **限時加壓**：把時限砍到 30 分鐘。你會被迫放棄「讀 struct 欄位」這種細節，只留最粗的骨架。體會「時間預算如何決定深度」。
- **盲測驗收**：把你的架構圖給一個沒看過 curl 的朋友，問他「一個 https 請求大概怎麼跑」。他答得出來 = 你的圖合格；答不出來 = 你畫太細或漏了主幹。
- **回頭對照官方**：curl 有 `docs/INTERNALS.md`。**做完偵察再看它**，比對你猜對了多少、漏了什麼。這是校準你偵察準確度的最好方式——偵察的目標是逼近這份官方 internals，但用一小時逆向出來，而不是等它餵給你。

## 自我檢核

- [ ] 我能在不看報告的情況下，說出「60 分鐘偵察的五個步驟與各步驟時間預算」
- [ ] 我知道為什麼「偵察階段深度是敵人」——為什麼在一節鑽太深會害了整體
- [ ] 我能解釋怎麼用 `cloc` 的數字快速判斷「核心目錄在哪、哪些是雜訊」
- [ ] 我抓到過至少一次「grep 被同名字串／字串常數騙」的情況，並知道為何語意工具（Ch 13）能避開
- [ ] 我的偵察至少有一題假設是「猜錯→查證→修正」的，而不是全部腦補正確

做完這個練習，你就有了一套能套到任何陌生 C 專案的冷啟動 SOP。接下來 Part 3 把偵察時用到的每一把工具往深裡打——先從最基礎、也最被低估的文字搜尋開始：ripgrep 的藝術。

→ [Ch 12 grep/ripgrep 的藝術](./12-grep-ripgrep-art.md)
