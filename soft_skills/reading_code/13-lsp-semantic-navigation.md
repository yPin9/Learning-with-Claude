# Ch 13 — LSP 與語意導航

> **目標**：跨過上一章文字工具的天花板。學完你會懂 LSP（Language Server Protocol）這套協定的底層——它其實是編輯器與一個「懂編譯的後台程式」之間的 JSON-RPC 對話；懂 clangd 怎麼用真正的 AST + index 做到「精準」跳轉，而不是像 ctags 那樣純文字猜；能分辨 go-to-definition / find-references / call-hierarchy / rename / hover 各自在解什麼問題；並且知道**什麼時候語意工具會救你的命**——同名符號、巨集展開後、C++ 多載——這些正是文字工具全軍覆沒的地方。

> **環境**：WSL2 Ubuntu 22.04，clangd 14（`clangd-14`）、Python 3.10（本章用它手動驅動 LSP 協定，把「編輯器背後發生什麼」攤開給你看）。沙包 `~/reading_code_lab/redis`（redis 7.4.0），**已預建 `compile_commands.json`**（275 個編譯單元，見 Ch 0）。本章所有 clangd 輸出——`--check`、go-to-definition、find-references、hover——都是真的用 clangd-14 對 redis 跑出來照抄的。互動式功能（在編輯器裡按鍵）會明確標注「編輯器內操作」。

## 為什麼需要語意工具？先看文字工具死在哪

上一章結尾留了一個問題：`free` 這個名字，在 redis 裡到底是誰？用純文字工具（ctags）查它的定義，得到這個：

```
$ readtags -t tags free
free  src/adlist.h    /^    void (*free)(void *ptr);$/
free  src/module.c    /^        moduleTypeFreeFunc free;$/
free  src/redismodule.h /^    RedisModuleTypeFreeFunc free;$/
free  src/server.h    /^    moduleTypeFreeFunc free;$/
free  src/zmalloc.c   /^#define free(/
```

**五個「free」**：一個是 linked list struct 裡的函式指標欄位（`void (*free)(void *)`）、三個是不同 struct 裡的型別欄位、一個是 `zmalloc.c` 裡的 macro 重定義。ctags 是純語法的——它掃到五個叫 `free` 的東西，全列出來，**它無法告訴你「游標下這一個 `free` 是哪一個」**。你在 `adlist.c` 的 `list->free(node->value)` 這行按「跳到定義」，ctags 會丟一個選單讓你自己猜五選一。

這就是文字/純語法工具的**根本天花板**：它們不懂**作用域**（scope）和**型別**（type）。`list->free` 裡的 `free` 明明只可能是 `list` 這個 struct 的欄位，但 ctags 不知道 `list` 是什麼型別，所以它不能推斷。

語意工具（clangd）懂。因為它做的事跟 ctags 完全不同層次：**它真的把你的 code 編譯成 AST**，知道 `list` 的宣告型別是 `list *`，知道 `list` struct 有個叫 `free` 的欄位，於是 `list->free` 的 `free` 精準指向那唯一一個定義——不猜，是算出來的。

## LSP 是什麼？一個協定，解耦編輯器與語言智慧

在 LSP 出現之前，每個編輯器要支援每個語言的智慧功能（跳轉、補全、重構），都得各自實作一遍——`M×N` 的災難（M 個編輯器 × N 個語言）。微軟 2016 年提出 **Language Server Protocol** 把這件事拆開：

- **Language Server**（語言伺服器，如 clangd、rust-analyzer、pyright）：一個獨立行程，懂某個語言的一切語意。
- **Client**（編輯器：VS Code / Neovim / Emacs / …）：只要會講 LSP 這套協定，就能用上任何 language server 的智慧。

`M×N` 變成 `M+N`。這就是為什麼今天 VS Code、Neovim、Emacs 用 clangd 得到的「跳到定義」品質**完全一樣**——它們背後跑的是同一個 clangd 行程，只是各自用 LSP 跟它對話。**理解這點很重要**：本章教的不是某個編輯器的功能，是所有現代編輯器共享的底層機制。

### 協定長什麼樣：JSON-RPC over stdio

LSP 的本體樸素得驚人：**編輯器與 language server 透過 stdin/stdout 互丟 JSON-RPC 訊息**。每則訊息是一個 HTTP 風格的 header（`Content-Length: N`）加一段 JSON body。核心方法（method）就那幾個，名字直白：

| LSP method | 對應功能 | 你在編輯器按的鍵（常見） |
|---|---|---|
| `initialize` | 握手，交換能力 | （啟動時自動） |
| `textDocument/didOpen` | 告訴 server「我打開了這檔」 | （開檔時自動） |
| `textDocument/definition` | 跳到定義 | `gd` / F12 |
| `textDocument/references` | 找所有引用 | `gr` / Shift+F12 |
| `textDocument/hover` | 顯示型別/文件 | `K` / 滑鼠懸停 |
| `textDocument/rename` | 語意重新命名 | `<leader>rn` / F2 |
| `callHierarchy/incomingCalls` | 誰呼叫我 | （call hierarchy 面板） |
| `typeHierarchy/supertypes` | 型別繼承鏈 | （type hierarchy 面板） |

**關鍵洞察**：你在編輯器按的每一個「跳轉」鍵，本質上就是編輯器把游標的 `{檔案 URI, 行, 欄}` 包成一則 `textDocument/definition` JSON 丟給 clangd，clangd 算完回一則 `{檔案 URI, 行, 欄}` 的 JSON，編輯器據此跳過去。就這麼回事。下面我們**手動**扮演編輯器，把這對話跑一遍給你看。

### 握手：能力協商

對話的第一步 `initialize` 不只是打招呼——它是**能力協商**（capability negotiation）。client 送出「我支援哪些功能」，server 回「我能提供哪些功能」，雙方取交集。我實跑一個最小 `initialize`，看 clangd 回的原始 bytes 與它宣告的能力（真實輸出）：

```
$ python3 -c '<送一個 initialize，印回應>'
RAW HEADER BYTES: b'Content-Length: 1855\r\n\r\n'
server advertises capabilities keys: ['astProvider', 'callHierarchyProvider',
  'clangdInlayHintsProvider', 'codeActionProvider', 'compilationDatabase',
  'completionProvider', 'declarationProvider', 'definitionProvider',
  'documentFormattingProvider', 'documentHighlightProvider', ...]
```

看到那個 `Content-Length: 1855\r\n\r\n` 了嗎——這就是 LSP 的傳輸層本體，一個長度前綴加 CRLF 分隔，跟 HTTP 同源但更精簡。回應 body 裡 clangd 列出它「是什麼 provider」：`definitionProvider`（能跳定義）、`callHierarchyProvider`（能建呼叫樹）、`compilationDatabase`（懂 compile_commands.json）……**編輯器據此決定「哪些鍵要啟用」**——如果某個 server 沒宣告 `renameProvider`，編輯器就不會給你 rename 鍵。這就是為什麼同一個編輯器接不同語言的 server，可用功能會不一樣：功能是 server 宣告出來的，不是編輯器寫死的。下面我們**手動**扮演編輯器，把跳轉那段對話跑完。

## 把 clangd 攤開：手動驅動 LSP 協定

先確認 clangd 能正確載入 redis 的編譯設定。`clangd --check` 是最快的健檢——它模擬「打開這個檔並跑一遍所有語意分析」，不需要編輯器：

```
$ clangd-14 --check=src/ae.c 2>&1 | tail -12
I[..] Loaded compilation database from /home/ypp/reading_code_lab/redis/compile_commands.json
I[..] Compile command from CDB is: /usr/bin/cc -pedantic -DREDIS_STATIC= -std=gnu11 ... -c -o ae.o ... src/ae.c
I[..] Building preamble...
I[..] Indexing headers...
I[..] Building AST...
I[..] Indexing AST...
I[..] Testing features at each token (may be slow in large files)
I[..] All checks completed, 0 errors
```

這幾行洩漏了 clangd 的全部工作流程：**找到 compile command（知道用什麼旗標編這檔）→ 建 preamble（預先處理標頭，加速）→ 索引標頭 → 建 AST → 索引 AST → 在每個 token 測試語意功能**。「0 errors」代表它成功把 `ae.c` 當成一個真正的編譯單元分析了。對比一下：ctags 建索引不需要 compile command，因為它根本不編譯；clangd 需要，因為它**真的在做編譯前端**（Ch 0 一再強調的「clangd 要跳轉，先要能編譯」，這裡看到全貌）。

注意那行 compile command 裡的旗標——`-std=gnu11 -DUSE_JEMALLOC -I../deps/jemalloc/include ...`——這些**直接決定 clangd 看到的是哪個世界**。`-DUSE_JEMALLOC` 讓它把 `free` 解析成 `je_free`；換個 `-D` 它就解析成別的。這是 clangd 相對純語法工具最深的差別：**它看的是「這個 build 設定下的 code」，不是「檔案裡的原始文字」**。同一份 source 在不同 `-D` 下，clangd 給的跳轉可以不同——因為真實編譯出來的程式本來就不同。ctags/grep 對 `#ifdef` 一無所知，把所有分支都當存在，這在重度條件編譯的專案（kernel、跨平台 code）裡會嚴重誤導。

現在真正扮演編輯器。我寫一小段 Python，透過 stdin/stdout 跟 clangd 講 LSP，問它「`server.c:7251` 那個 `aeMain` 的定義在哪」。核心對話（簡化）：

```python
# 1. 握手
send({"method":"initialize", "params":{"rootUri":"file://.../redis", ...}})
send({"method":"initialized"})
# 2. 打開 server.c
send({"method":"textDocument/didOpen",
      "params":{"textDocument":{"uri":"file://.../src/server.c","text":<全文>}}})
# 3. 問：line 7250 (0-indexed), char 6 這個符號的定義在哪？
send({"id":2, "method":"textDocument/definition",
      "params":{"textDocument":{"uri":".../server.c"},
                "position":{"line":7250,"character":6}}})
```

clangd 回的 JSON（實跑，照抄）：

```json
=== definition result ===
[
  {
    "uri": "file:///home/ypp/reading_code_lab/redis/src/ae.c",
    "range": {
      "start": { "line": 473, "character": 5 },
      "end":   { "line": 473, "character": 11 }
    }
  }
]
```

`server.c` 第 7251 行的 `aeMain` 呼叫，精準解析到 **`ae.c` 第 474 行（0-indexed 473）第 5–11 欄**——正是 `void aeMain(...)` 的定義位置。這就是你在 VS Code 按 F12、Neovim 按 `gd` 時，背後那則 JSON 往返。**編輯器只是把這個 JSON 結果轉成「開檔跳行」的動作**，協定本身你剛剛親眼看到了。

## 語意功能逐個看（真實輸出）

### go-to-definition：不是找名字，是解析符號

上面已經示範。重點在「精準」：clangd 回的是**唯一一個**位置，因為它從 AST 知道游標下的 `aeMain` 綁定到哪個宣告。同名的區域變數、別的檔的 static 函式、macro 裡的同名 token——它一個都不會混進來。這是跟 ctags「丟一堆同名候選」的本質差別。

### declaration vs definition：clangd 分得清

C 把「宣告」（`.h` 裡的原型）和「定義」（`.c` 裡的本體）分開，這對讀碼是個小陷阱：你要的是哪個？clangd 兩個都懂且分得開。`aeMain` 在 redis 裡：

```
$ rg -n "aeMain" src/ae.h src/ae.c
src/ae.h:107:void aeMain(aeEventLoop *eventLoop);      ← 宣告（declaration）
src/ae.c:474:void aeMain(aeEventLoop *eventLoop) {     ← 定義（definition）
```

LSP 有兩個不同 method：`textDocument/definition`（跳到本體 `ae.c:474`）和 `textDocument/declaration`（跳到原型 `ae.h:107`）。上面能力協商時 clangd 宣告了 `declarationProvider` 和 `definitionProvider` 兩者。編輯器通常把「跳定義」綁到 definition，但也給你「跳宣告」的選項。ctags 對這個區分是**模糊的**——它把宣告和定義都當 tag，跳轉時給你兩個候選讓你猜。clangd 精確知道哪個是原型、哪個是本體。讀不熟的 C API 時這很有用：想看「這函式怎麼用」跳宣告（看原型與註解），想看「它怎麼實作」跳定義。

### find-references：反向找所有引用

問 `aeMain` 的所有引用（`textDocument/references`），clangd 實跑回傳：

```
=== references to aeMain: count = 6
  ae.c:474            ← 定義本身
  ae.h:107            ← 宣告
  redis-benchmark.c:970
  redis-benchmark.c:1010
  redis-benchmark.c:1829
  server.c:7251       ← redis server 主迴圈那一行
```

六個引用，跨四個檔，含定義、宣告、與三處呼叫。這跟 `rg -w aeMain` 的差別在哪？表面上結果可能很像——因為 `aeMain` 這名字夠獨特。但如果符號名很常見（像 `free`、`init`、`read`），`rg` 會把所有同名的東西全撈進來，clangd 只給**真正指向這同一個符號**的引用。**符號越普通，find-references 相對 grep 的優勢越大。**

### hover：游標下是什麼型別

`textDocument/hover` 回傳游標下符號的型別與文件。對 `aeMain` 的定義位置 hover（實跑）：

```
=== hover ===
function aeMain

→ void
Parameters:
- aeEventLoop * eventLoop

void aeMain(aeEventLoop *eventLoop)
```

clangd 從 AST 直接吐出完整簽章：回傳 `void`、參數 `aeEventLoop *eventLoop`。讀陌生 code 時 hover 是最高頻的動作——滑過任何變數就知道它的型別，滑過任何函式就知道它的簽章，**不用跳去定義就能繼續讀**。這對「精讀一段但不想被跳轉打斷」的閱讀模式（Ch 4）是核心工具。

### call-hierarchy：呼叫關係樹

`callHierarchy/incomingCalls`（誰呼叫我）與 `outgoingCalls`（我呼叫誰）建出**可展開的呼叫樹**。這比 Ch 9 的 cflow 靜態呼叫圖更精準——它懂函式指標、懂多載、懂 overload 到底綁到哪個。**編輯器內操作**：把游標放在函式上開 call hierarchy 面板，一層層展開「誰呼叫這個 → 誰呼叫那個」，追一條呼叫鏈往上溯源。這是追「這個函式最終是被哪個入口觸發的」（Ch 8 data flow、Ch 11 收斂到改動點）的利器。

### completion：補全也是讀碼工具

`textDocument/completion` 通常被當「寫 code 的便利」，但讀碼時它是探索 API 的利器。在一個陌生 struct 指標後打 `->`，clangd 列出**這個型別所有可用的欄位與方法**——等於免費給你這個 struct 的成員清單，不用去翻定義。在一個陌生命名空間/前綴後打字，它列出所有以此開頭的符號。**讀陌生函式庫時，「打前綴看補全」是最快知道「這個模組提供什麼」的方法**。而且 clangd 的補全是語意的：它只列**在當前作用域與型別下合法**的候選，不像純文字補全把所有出現過的字都塞給你。

### type-hierarchy 與 rename

- **type-hierarchy**（`typeHierarchy/supertypes` / `subtypes`）：C++ 的類別繼承鏈——「這個類別的父類是誰、有哪些子類覆寫了這個虛擬方法」。C 專案用不太到，C++ 讀繼承體系時必備。**編輯器內操作**。
- **rename**（`textDocument/rename`）：**語意層級**的重新命名。跟「文字 find-replace」的天差地別在於：它只改**真正指向這個符號**的地方。你把 struct 欄位 `free` 改名成 `destructor`，clangd 只動那些真的是「這個欄位」的引用，不會誤傷別的檔裡同名的 libc `free` 或別的 struct 的 `free`。**這是文字 replace 永遠做不到的正確性**——也是為什麼重構要用 LSP rename 而不是 `sed`。

## 何時語意工具救你的命

日常掃讀用 ripgrep 就夠。但下面這四種情況，文字工具會直接誤導你，只有語意工具給對答案：

1. **同名符號**：就是開頭 `free` 那個例子。redis 有五個 `free`、無數個 `init`/`read`/`len`。在 `list->free` 上按跳轉，ctags 給你五選一的選單，clangd 直接帶你到唯一正確的那個。符號越普通，這個差距越致命。**更狠的一個真例**：redis 的事件迴圈有可抽換後端（epoll/kqueue/select/evport），每個後端一個檔，各自定義一個 `static int aeApiPoll(...)`：
   ```
   $ grep -rl "static int aeApiPoll" src/
   src/ae_epoll.c  src/ae_kqueue.c  src/ae_select.c  src/ae_evport.c
   ```
   **四個同名 `aeApiPoll`，但它們是 `static`——各自只在自己的 TU 內可見。** grep/ctags 看到四個一模一樣的名字無從分辨；但 `ae.c` 用 `#include "ae_epoll.c"` 這種手法只把其中一個編進來，clangd 從編譯設定知道**當前這個 build 實際用的是哪一個 `aeApiPoll`**，跳轉直接到對的那個。這是「static 作用域 + 條件編譯」的雙重迷宮，只有真懂編譯的工具能走出來。

2. **巨集展開後的真相**：C 的 macro 在文字層面是隱形的。redis `zmalloc.c` 真的有這幾行（實跑）：
   ```
   $ rg -n "#define (free|malloc)\b" src/zmalloc.c
   src/zmalloc.c:55:#define malloc(size) tc_malloc(size)   ← 用 tcmalloc 時
   src/zmalloc.c:58:#define free(ptr) tc_free(ptr)
   src/zmalloc.c:61:#define malloc(size) je_malloc(size)   ← 用 jemalloc 時
   src/zmalloc.c:64:#define free(ptr) je_free(ptr)
   ```
   同一個 `free`，依 build 時選哪個 allocator，**實際展開成 `tc_free` 或 `je_free`**。你用 grep 搜 `free`，看到的是展開**前**的文字，完全不知道跑起來到底呼叫誰。clangd 在 AST 層看的是**展開後**的真實呼叫——hover 一個被 macro 包住的 token，它告訴你它真正綁到 `je_free` 還是 `tc_free`。讀重度 macro 的 code（kernel、redis 的 `zmalloc`）時，這是唯一可靠的還原真相的方法（Ch 22 專講 macro）。clangd 甚至提供 macro expansion 的 hover：把游標放 macro 上，它把展開結果攤給你看。

3. **C++ 多載（overload）**：`foo(int)` 和 `foo(const string&)` 是兩個不同函式共用一個名字。文字工具眼中它們是同一個字，跳轉必然搞混。clangd 從**呼叫點的引數型別**推斷你呼叫的是哪一個多載，精準跳到對應定義。C++ 讀碼沒有語意工具幾乎寸步難行（Ch 26 專講 C++ 複雜性）。

4. **模板/泛型實例化**：C++ template、以及 auto 型別推導後的真實型別，純文字完全無從得知。hover 一個 `auto x = ...` clangd 告訴你 `x` 實際被推導成什麼型別。這是讀現代 C++ 的基本生存需求。

**一句話原則**：**掃讀、跨語言、找字串、找 log 訊息 → ripgrep（快、通用）；需要「這個符號到底是哪個」的精準 → clangd（懂型別與作用域）。** 兩者互補，這條線從 Ch 0 貫穿到現在。

## 一個真實攻堅場景：把語意工具當逆向探針

上面是功能羅列，看個把它們串起來的實戰。假設你要搞懂 redis 的事件迴圈，從 Ch 0 已知「心臟是 `server.c:7251` 的 `aeMain`」，你的攻堅動線：

1. **hover `aeMain`** → 立刻知道簽章 `void aeMain(aeEventLoop *eventLoop)`，參數是 event loop。不用開檔。
2. **go-to-definition** → 跳到 `ae.c:474`，看到 `while (!eventLoop->stop) { aeProcessEvents(...); }`。心臟是個 stop 旗標控制的無限迴圈。
3. **hover `eventLoop->stop`** → 確認 `stop` 是個 int 旗標。於是問：誰把 stop 設 1？
4. **find-references `aeStop`**（設 stop 的函式）→ 一條指令找出所有「喊停」的地方，就知道 redis 怎麼優雅關閉。
5. **call-hierarchy incoming on `aeProcessEvents`** → 展開「誰呼叫這個迴圈核心」，反向確認只有 `aeMain` 這一條主路徑。

整個過程你**沒有一次是用文字搜尋**——因為每一步都要「這個符號的精確定義/引用」，而 `aeMain`/`aeProcessEvents`/`stop` 這些若用 grep 可能撞到同名 log 訊息、註解、別檔的區域變數。這就是把 clangd 當**逆向探針**：hover 探型別、definition 探實作、references 探影響面、call-hierarchy 探控制流。跟拿 IDA 逆 binary 時 X-refs（cross-references）的用法一模一樣——只是你在 source 上做，而且精準度來自真編譯而非啟發式。

## 診斷與 code action：語意工具的另一半

LSP 不只導航，還有 `textDocument/publishDiagnostics`（server 主動推送的錯誤/警告）與 `textDocument/codeAction`（「快速修復」建議）。讀碼時這兩個常被忽略，但很有用：

- **診斷當「理解檢查器」**：clangd 對你打開的檔即時標出可疑處——未使用的變數、可能的空指標解參、型別不符。讀陌生 code 時這些紅線**幫你標出作者可能的意圖或 bug**。找漏洞式讀碼（Ch 32）裡，clangd 的診斷是免費的第一層 sink 提示。
- **code action 洩漏語意**：把游標放在某個符號上請求 code action，clangd 可能提議「加上缺的 include」「展開 auto 型別」「把 macro 展開」。這些提議本身就透露了 clangd 對這段 code 的語意理解——即使你不套用，看它「想幫你改什麼」也是一種讀懂 code 的線索。

> 但記住踩雷 4：clangd 的診斷用它自己的 Clang + 你給的旗標，跟你的實際 build 可能有出入，會有誤報。診斷是**提示**不是判決。

## clangd 的底層：AST + index 怎麼配合

clangd 的精準來自兩個資料結構的配合：

- **AST（抽象語法樹）**：針對**你當前打開的檔**，clangd 用 Clang 前端把它編成完整 AST，含每個符號的型別、作用域綁定。這給你「當前檔內」的精準——go-to-definition 在同檔內、hover、當前檔的診斷，全靠這個。AST 精確但貴，所以只對「開著的檔 + 其 preamble」建。
- **index（跨檔索引）**：針對**整個專案**，clangd 背景掃描所有編譯單元，建一個「符號 → 定義位置 / 所有引用位置」的索引（`--background-index`）。這給你「跨檔」的能力——find-references 找到別的檔的引用、跳到定義檔在別處的符號，靠的是 index。index 比 AST 粗（不重新編譯每個查詢），但涵蓋全專案。

兩者合作：你在 `server.c` 對 `aeMain` 按跳轉，clangd 從**當前檔 AST** 確認「游標下這個 token 綁定到符號 `aeMain`」，再從**專案 index** 查到「`aeMain` 的定義在 `ae.c:474`」。**AST 管精準綁定，index 管跨檔位置**——這個分工是所有 LSP 語意功能的引擎。這也解釋了為什麼 clangd「剛打開大專案時前幾秒跳轉不準」：背景 index 還沒建完，這時只有 AST 可用，跨檔查詢會漏。

### preamble：為什麼你打字時它能即時回應

一個關鍵的效能設計是 **preamble**（前導區）。C/C++ 一個檔開頭往往 `#include` 一大堆標頭，每次重新分析都要把這幾萬行標頭重編一遍，慢到不可用。clangd 的招是：把「檔案開頭那段連續的 `#include` 與 macro 定義」單獨編一次，快取成 preamble。之後你在函式體裡打字改 code，clangd **只重編 preamble 之後的部分**，前面那坨標頭直接復用快取。`--check` 輸出裡的 `Building preamble...` 就是這一步。這是為什麼 clangd 能在你邊打字邊給即時診斷與補全——它沒有每次都從頭編。

代價：如果你改的是**檔案開頭的 `#include`**（讓 preamble 失效），clangd 得重建 preamble，這時會有一下卡頓。理解這點你就知道「為什麼改函式內部很順、改 include 會頓一下」。

### 增量同步：編輯器怎麼告訴 clangd「我改了字」

你在編輯器打字時，編輯器不會每次都把整個檔重送給 clangd（太浪費）。LSP 定義了 `textDocument/didChange`，可以只送**增量**（哪一段 range 換成什麼字）。clangd 據此更新它記憶中的檔案內容、重跑（preamble 之後的）分析、把新的診斷用 `textDocument/publishDiagnostics` **主動推**回編輯器（這是少數 server → client 主動通知的訊息，不是 request/response）。這條 didChange → publishDiagnostics 的即時循環，就是你看到「打錯字馬上出紅線」的底層。**理解它是「編輯器記憶體內容 vs clangd 記憶體內容的同步」**，你就懂為什麼有時檔案在磁碟上還沒存、clangd 卻已經知道你改了——因為同步走的是編輯器記憶體，不是磁碟。

## 對比與取捨

| 能力 | ripgrep | ctags | cscope/global | **clangd (LSP)** |
|---|---|---|---|---|
| 懂作用域/型別 | 否 | 否 | 否 | **是** |
| 需要能編譯 | 否 | 否 | 否 | **是（要 compile_commands.json）** |
| 同名符號精準跳轉 | 否（全中） | 否（選單） | 否 | **是（唯一）** |
| 反查引用 | 半（文字 `-w`） | 否 | 是（純文字級） | **是（語意級）** |
| 巨集展開後語意 | 否 | 否 | 否 | **是** |
| C++ 多載/模板 | 否 | 否 | 否 | **是** |
| 語意重新命名 | 否 | 否 | 否 | **是** |
| 啟動/資源成本 | 極低 | 低 | 低 | **高（要 index、吃記憶體）** |
| 跨語言通用 | **是** | 多語言 | C/C++ 為主 | 單語言（每語言一個 server） |

**取捨的本質是「精準 vs 成本」**。clangd 給你最精準的答案，代價是：要能編譯（compile_commands.json）、啟動慢、大專案吃記憶體、超大 C 專案（如整個 kernel）建 index 可能很久甚至跑不動。所以實戰是**分層**：ripgrep 秒級定位大概位置 → ctags/cscope 建骨架 → **需要精準時才動用 clangd**。不要企圖全程只靠 clangd（重、慢），也不要在需要「這個 `free` 是哪個」時還在硬用 grep（會被騙）。

## 踩雷集錦

1. **錯誤直覺：「clangd 裝好就會跳轉」**。正確認識：**沒有 `compile_commands.json`，clangd 退化成單檔模式**，跨檔跳轉全失效、很多符號解析不了。`--check` 若印 `Failed to find compilation database` 就是這問題。這是 clangd 第一大坑（Ch 0 已警告，這裡重申因為它太常見）。

2. **錯誤直覺：「剛開檔就跳轉不準是 clangd 壞了」**。正確認識：**背景 index 還沒建完**。大專案開檔後前幾十秒，跨檔功能（find-references、跳到別檔的定義）會不完整。等 index 建好（狀態列通常有提示）再用。跟工具品質無關，是資料還沒就緒。

3. **錯誤直覺：「compile_commands.json 建一次就永遠有效」**。正確認識：它是**快照**。你新增檔案、改了 build 旗標、`git pull` 後，舊的 compilation database 對不上新 code，clangd 會對新檔一無所知。改動 build 後要重建（`bear -- make`）。

4. **錯誤直覺：「clangd 的診斷（紅線）就是編譯器的錯誤」**。正確認識：clangd 用它自己的 Clang 版本 + 你給的旗標分析，**可能跟你實際的 gcc build 有出入**——尤其專案用 gcc 特有 extension、或旗標裡有 clang 不認得的東西時，會冒出「假紅線」（誤報）。讀碼時把 clangd 診斷當參考，不是判決；真正的編譯錯誤以你的 build 為準。

5. **錯誤直覺：「LSP rename 跟 find-replace 一樣，只是自動化」**。正確認識：rename 是**語意**的，只改真正指向該符號的引用；find-replace 是**文字**的，會誤傷同名的無關符號。反過來，若你真想改「所有叫這名字的文字」（例如改 log 訊息），那才用文字 replace。用錯工具會改壞 code。

## 進階：再往深一層

- **`clangd --check` 當 CI 健檢**：`--check=<file>` 不需編輯器就能驗證「這個檔的語意分析是否乾淨、compile command 是否正確」。可以寫進腳本，對整個專案的關鍵檔跑一遍，快速發現「哪些檔 clangd 分析不了」（通常是 compile_commands.json 缺項）。
- **`.clangd` 設定檔**：專案根放一個 `.clangd` YAML，可以加額外編譯旗標、抑制特定誤報診斷、指定 index 策略。讀第三方專案時，用它擺平「clangd 對這專案的旗標水土不服」。
- **clangd 的 index 格式與 `clangd-indexer`**：clangd 可以離線預建整個專案的 index（`clangd-indexer`），把「開檔才建 index」的延遲前置掉。對超大專案（想在 kernel 上用 clangd）這是讓它可用的關鍵。
- **LSP 之上的其他 client**：除了編輯器，`ccls`（另一個 C/C++ language server）、以及 `lsp-mode`（Emacs）、`nvim-lspconfig`（Neovim）都走同一套協定。學會協定本身（本章手動驅動那段），你就能 debug 任何 client 的問題——抓 LSP 通訊 log 看那些 JSON 往返，是排查「跳轉為何失效」的終極手段。具體來說：VS Code 裝 `clangd` 擴充、Neovim 用內建 LSP client 配 `nvim-lspconfig` 的 `clangd` 設定、Emacs 用 `lsp-mode` 或 `eglot`——**三者背後跑的是同一個 `clangd` 二進位，用同一套 JSON-RPC 對話**，所以跳轉品質完全一致。你換編輯器不會失去語意能力，因為能力在 server 不在編輯器。這也是為什麼本章刻意不綁編輯器：學協定與 clangd，換皮不換骨。
- **抓 LSP 通訊 log 除錯**：clangd 加 `--log=verbose` 會把收到/送出的每則 LSP 訊息記下來；編輯器端通常也有「顯示 LSP log」的指令。跳轉突然失效時，看 log 裡那則 `textDocument/definition` request 有沒有送出、clangd 回了什麼（空陣列 = 它找不到、error = 設定壞了）。這是把黑箱打開的萬用手段。
- **SemanticTokens 與語意高亮**：LSP 還有 `textDocument/semanticTokens`——編輯器據此把「這個識別字是型別/變數/參數/巨集」用不同顏色標出。語意高亮跟純語法高亮的差別，跟本章主題完全同構：一個懂型別，一個只認 pattern。

## 動手練習

在 redis 沙包做（需要 `compile_commands.json`，Ch 0 已建）：

1. **`--check` 健檢**：對 `src/server.c`、`src/dict.c` 各跑 `clangd-14 --check=<file>`，看它印出的 compile command 與「0 errors」。挑一個沒有 compile_commands.json 涵蓋的檔（例如某個 `deps/` 下的檔）跑，看它怎麼退化。
2. **同名符號實測**：對 `src/adlist.c` 裡 `list->free(...)` 那行的 `free`，先用 `readtags -t tags free` 看 ctags 給幾個候選（五個），再在支援 clangd 的編輯器裡對同一個 `free` 按 go-to-definition，確認它只帶你到 `adlist.h` 的欄位定義。**親眼對比純語法 vs 語意。**
3. **find-references vs `rg -w`**：對 `aeMain` 分別用編輯器的 find-references 和 `rg -w aeMain src`，比對結果。再挑一個超常見的名字（如 `dictAdd` 或某個 `init`）做同樣對比，觀察名字越常見時兩者差距如何拉大。
4. **hover 讀型別**：打開 `src/server.c`，對幾個看不懂型別的區域變數 hover，用它給的型別資訊幫你讀懂一段你原本卡住的 code。體會「不跳轉就繼續讀」。
5. **（進階）手動 LSP**：仿本章的 Python 片段，自己寫一個最小 client，送 `initialize` + `didOpen` + `definition`，把 clangd 回的 JSON 印出來。跑通這個，你就真的懂 LSP 了——所有編輯器整合都只是這段對話的包裝。

## 本章重點整理

- LSP 把「編輯器 × 語言」的 `M×N` 問題拆成 `M+N`：一個 language server 服務所有會講 LSP 的編輯器。VS Code/Neovim/Emacs 用 clangd 得到的智慧**完全相同**。
- 協定本體樸素：**JSON-RPC over stdio**，核心方法 `textDocument/definition`、`references`、`hover`、`rename` 等。你按的每個跳轉鍵 = 一則 JSON 往返。
- clangd 的精準來自 **AST（當前檔的型別/作用域綁定）+ index（全專案的符號位置）** 的配合。
- 語意 vs ctags 的根本差別：**懂作用域與型別 vs 純文字猜**。同名符號、巨集展開後、C++ 多載——文字工具全滅，語意工具給唯一正確答案。
- 代價是「要能編譯 + 重 + index 慢」。實戰分層：ripgrep 定位 → ctags/cscope 骨架 → clangd 精準。
- clangd 要跳轉，先要 `compile_commands.json`；它是快照，改 build 後要重建。

## 自我檢核

- [ ] 不看筆記，我能說出 LSP 是什麼、解決了什麼 `M×N` 問題、傳輸層長什麼樣（JSON-RPC over stdio）
- [ ] 我能解釋「在 `list->free` 上跳轉，為何 ctags 給選單而 clangd 給唯一答案」——牽涉作用域與型別
- [ ] 我能講出 clangd 的 AST 與 index 各自負責什麼、為何剛開大專案時跨檔跳轉會暫時不準
- [ ] 我能舉出至少三種「文字工具會誤導、只有語意工具對」的情況（同名/巨集/多載/模板）
- [ ] 我知道為何 LSP rename 比 find-replace 正確，以及反過來何時才該用文字 replace
- [ ] 我理解語意工具的成本，以及「ripgrep → ctags → clangd」的分層使用策略

## 延伸閱讀

每條說清楚讀哪、學什麼、關聯。

### 官方文件 / 規格

- **[Language Server Protocol Specification](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/)**
  - **讀哪裡**：先讀 "Base Protocol"（訊息格式、JSON-RPC）與 "Language Features" 裡 `textDocument/definition`、`references`、`hover`、`rename` 四節。其餘當字典查。
  - **學到什麼**：本章手動驅動那段的權威依據——每個 method 的精確 request/response 結構。讀完你能自己寫 client。
  - **關聯**：直接對應本章「協定長什麼樣」與「語意功能逐個看」。

- **[clangd 官方文件（clangd.llvm.org）](https://clangd.llvm.org/)**
  - **讀哪裡**："Features"（各語意功能）、"Configuration"（`.clangd`、compile_commands.json）、"Design → Compile commands / Indexing"。
  - **學到什麼**：clangd 的 AST + index 架構、背景索引行為、如何用 `.clangd` 調教。本章「底層」那節的延伸。
  - **前提**：懂編譯旗標（`-I`、`-D`、`-std`）會更有感。

### 技術文章

- **[Nadav Rotem / clangd design docs — "How clangd works"](https://clangd.llvm.org/design/)**
  - **讀哪裡**：preamble、AST 快取、dynamic vs static index 的設計取捨。
  - **學到什麼**：為何 clangd 能在你打字時即時回應（preamble 快取）、為何大專案 index 慢。理解它的效能特性才知道何時它會吃力。
  - **關聯**：補強本章「AST + index 怎麼配合」與踩雷 2（index 未建完）。

### 對照閱讀

- **[Universal Ctags 文件 — "How to use ctags with your editor"](https://docs.ctags.io/en/latest/)**
  - **讀哪裡**：ctags 如何產生 tag、tag 檔格式。
  - **學到什麼**：**反著讀**——理解 ctags 的純語法本質（它為何給不出唯一答案），正是理解 clangd 語意優勢的最佳對照組。本章開頭 `free` 五選一的例子就出自這個對比。
  - **關聯**：接下一章 Ch 14，那章把 ctags/cscope/global 這些純語法索引工具講透。

語意工具給你最高的精準度，但它重、要能編譯、超大 C 專案下會吃力。當你需要的是「快速建立全專案的符號與呼叫骨架、且不介意純語法級的精度」時，有一組更輕、更老牌、對 C 專案極其順手的工具——下一章我們把 ctags、cscope、GNU global 這三把純語法索引利器講到底。

→ [Ch 14 ctags / cscope / GNU global](./14-ctags-cscope-global.md)
