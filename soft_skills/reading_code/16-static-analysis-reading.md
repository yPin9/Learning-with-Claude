# Ch 16 — 靜態分析輔助讀碼：把警告當導航

> **目標**：把靜態分析工具的用途從「找 bug」擴充到「導航讀碼」。核心洞見：**分析器標出的可疑處，往往正是這段 code 最微妙、最值得你優先精讀的地方。** 你會用 cppcheck、clang-tidy / scan-build 在 redis 真檔上跑出真實警告，並示範「順著一條警告讀進去、把周圍機制搞懂」的方法；再用 cflow → dot → PNG 與 doxygen 產呼叫圖/被呼叫圖，把靜態呼叫關係畫成地圖。

> **環境**：WSL2 Ubuntu 22.04。cppcheck 2.7、clang-tidy-14、scan-build-14（clang-analyzer 14）、doxygen 1.9.1、graphviz(dot) 2.43.0、cflow 1.7。沙包 `~/reading_code_lab/redis`（redis 7.4.0，含 `compile_commands.json`）。本章 cppcheck / clang-tidy / scan-build / cflow / doxygen 全部**真跑照抄**；哪些是理論會明講。

## 反直覺的前提：警告是免費的讀碼線索

大家都把 linter / 靜態分析器當「上 CI、擋爛 code」的守門員。這章要你換一個用途：**讀碼時，先讓分析器跑一遍，把它標紅的地方當成「這裡有玄機，優先看」的導航標記。**

為什麼這招有效？因為靜態分析器標紅的，通常是這幾類：

- **邊界模糊處**：陣列索引在檢查前就用了、可能的 null 解參、整數溢位——這些正是原作者「腦子裡有個 invariant 但沒寫出來」的地方。搞懂警告，等於逼自己把那個隱藏 invariant 講清楚。
- **控制流的死角**：分析器沿著某條路徑走，發現「如果走這條，`earliest` 會是 NULL」。它幫你**枚舉了你肉眼會漏的路徑**。
- **意圖與寫法的落差**：`const` 沒加、`.c` 被 `#include`、可疑的型別轉換——這些是「作者這樣寫是有原因，還是手滑」的問號，值得停下來想。

換句話說，分析器幫你做了**注意力分配**：一個十萬行的檔，你不可能每行等重讀。警告清單是一份「這裡先看」的優先序，由一個不會累、把所有路徑都走過一遍的機器產生。這是它相對人眼的結構性優勢。

```
   傳統用法                    讀碼用法（本章）
   分析器 → 警告 → 修掉        分析器 → 警告 → 「這裡先讀」→ 搞懂隱藏 invariant
   （守門）                    （導航 + 逼出理解）
```

一個關鍵心態：**警告不一定是真 bug**（誤報很常見），但它幾乎一定是「值得你花注意力的一段」。讀碼時你要的不是「這是不是 bug」，而是「這段在幹嘛、作者在防什麼」——警告把你精準帶到那裡。

## 工具地圖：五類分析器各給什麼線索

| 工具 | 類型 | 要能編譯嗎 | 給你的讀碼線索 |
|---|---|---|---|
| **cppcheck** | 獨立靜態分析 | 否（自帶 parser） | 快速掃出可疑 pattern：邊界、const、未初始化 |
| **clang-tidy** | LLVM 靜態檢查 | 是（要 compile db） | bugprone/analyzer 檢查、可自訂 check 集 |
| **scan-build / clang analyzer** | 路徑敏感分析 | 是（包住 build） | **沿路徑推理**：null 解參、洩漏，附完整觸發路徑 |
| **cflow** | 呼叫關係抽取 | 否 | 靜態呼叫樹 → 畫成呼叫圖 |
| **doxygen (+dot)** | 文件 + 圖 | 否 | call graph / caller graph / include graph |

前三個給「可疑處」，後兩個給「結構圖」。讀碼時前者導航到點、後者給你全局地圖。

## cppcheck：最快的第一掃

cppcheck 自帶 parser、**不需要能編譯**，指到檔案就掃，是「拿到陌生 C 檔第一個跑」的低成本選擇。真跑在 redis 的 `util.c`：

```
$ cppcheck --enable=warning,style --inline-suppr --quiet src/util.c
src/util.c:562:53: style: Array index 'curr_char_idx' is used before limits check. [arrayIndexThenCheck]
    while ((-1 != (char_type = base_16_char_type(src[curr_char_idx]))) &&
                                                    ^
src/util.c:556:17: style: Variable 'ascii_to_dec' can be declared with const [constVariable]
    static char ascii_to_dec[] = {'0', 'a' - 10, 'A' - 10};
                ^
src/util.c:728:19: style: Variable 'powers_of_ten' can be declared with const [constVariable]
    ...
nofile:0:0: information: Too many #ifdef configurations - cppcheck only checks 12 configurations. ...
```

四筆結果，價值天差地別。`constVariable` 是無傷大雅的風格提示。但第一筆——`arrayIndexThenCheck`——是一條**值得立刻讀進去**的線索。

### 順著警告讀碼：`arrayIndexThenCheck` 這條

警告說：`curr_char_idx` 這個索引在「界限檢查之前」就被拿去存取了。打開 `util.c:555` 附近看：

```c
int string2ul_base16_async_signal_safe(const char *src, size_t slen, unsigned long *result_output) {
    ...
    size_t curr_char_idx = 0;
    ...
    while ((-1 != (char_type = base_16_char_type(src[curr_char_idx]))) &&   // ← 先讀 src[curr_char_idx]
            curr_char_idx < slen) {                                          // ← 才檢查 idx < slen
```

`while` 條件用了 `&&`，C 的短路求值**從左到右**：它**先**算 `base_16_char_type(src[curr_char_idx])`（讀了 `src[idx]`），**才**算 `curr_char_idx < slen`（界限檢查）。cppcheck 精準地指出：讀取發生在檢查之前。

現在進入讀碼的關鍵動作——**問「作者為什麼敢這樣寫？」**。三種可能：

1. **真 bug**：如果 `src` 不保證有 null terminator 且 `slen` 可能為 0，第一次迭代就越界讀。
2. **有隱藏 invariant**：也許呼叫端保證 `src` 一定以某個非 base16 字元（如 `\0`）結尾，`base_16_char_type('\0')` 回 -1，短路的第一項就先讓迴圈停下，永遠讀不到越界位置。函式名 `async_signal_safe` 暗示它在 signal handler 裡用、對輸入格式有嚴格假設。
3. **效能取捨**：作者刻意把便宜的字元分類放前面、界限檢查放後面。

**你不必當場斷定是哪一種**——重點是這條警告逼你把這個函式的**輸入契約**（`src` 是否 null-terminated、`slen` 的語意、呼叫端怎麼保證）讀清楚。這就是「警告當導航」：它把你帶到一個 invariant 藏得很深的地方，而你為了回答「這是不是 bug」，被迫真正讀懂了這段。若你在做漏洞審計（Ch 32），這正是一個要追呼叫端、確認契約是否真的成立的點。

> `Too many #ifdef configurations` 那條也是線索：util.c 有很多 `#ifdef` 分支，cppcheck 只檢查了 12 種組合。`--force` 可全查（慢）。它提醒你這檔的條件編譯很複雜——讀的時候要注意「你看到的是哪個 config 下的樣子」（呼應 Ch 22 讀巨集）。

### 把警告變成一份「閱讀 TODO 清單」

`--template=gcc` 讓 cppcheck 吐出 `file:line:col: warning: ...` 的 gcc 格式，可直接餵進編輯器的 quickfix list，變成「逐點跳去讀」的待辦。跨幾個檔一次掃（真實輸出）：

```
$ cppcheck --enable=warning --template=gcc --quiet src/sds.c src/dict.c src/ae.c
src/ae.c:257:20: warning: Possible null pointer dereference: earliest [nullPointer]
src/dict.c:1748:18: warning: %ld in format string (no. 1) requires 'long' but the argument type is 'unsigned long'. [invalidPrintfArgType_sint]
src/dict.c:1748:18: warning: %ld ... requires 'long' but the argument type is 'unsigned long'.
```

兩件事值得注意。第一，`ae.c:257 earliest` 這條——**cppcheck 獨立地標到了跟 clang-tidy / scan-build 同一個 null 解參點**。三個原理不同的分析器指向同一行，這種**交叉印證**大幅提高「這裡真的值得看」的可信度（雖然仍可能三者都被同一個保守假設騙）。第二，`dict.c:1748` 的 `%ld` 格式字串與 `unsigned long` 引數不符——這是**平台相依的潛在 bug**（在 `long` 與 `unsigned long` 表示不同時出錯），也是一條把你帶去讀「這段在印什麼、格式對不對」的線索。把這份清單當作你精讀這幾個檔的入口點，比從第一行順讀高效得多。

## clang-tidy：借用 compile db 的深檢查

clang-tidy 是 LLVM 家的檢查器，**要 `compile_commands.json`**（沙包已備）。它能開 `clang-analyzer-*`（路徑敏感）與 `bugprone-*`（可疑 pattern）等 check 集。真跑在 `ae.c`：

```
$ clang-tidy-14 -p . --checks='-*,clang-analyzer-*,bugprone-*' src/ae.c
2546 warnings generated.
ae.c:35:15: warning: suspicious #include of file with '.c' extension [bugprone-suspicious-include]
    #include "ae_epoll.c"
              ^
ae.c:257:20: warning: Access to field 'when' results in a dereference of a null pointer (loaded from variable 'earliest') [clang-analyzer-core.NullDereference]
    return (now >= earliest->when) ? 0 : earliest->when - now;
                   ^
ae.c:476:5: note: Loop condition is true.  Entering loop body
    while (!eventLoop->stop) {
    ...
ae.c:353:9: note: Assuming the condition is true
    if (eventLoop->maxfd != -1 ||
ae.c:359:13: note: Assuming field 'beforesleep' is equal to NULL
        if (eventLoop->beforesleep != NULL && (flags & AE_CALL_BEFORE_SLEEP))
    ...
```

兩類線索：

- **`bugprone-suspicious-include`**：`ae.c` 竟然 `#include "ae_epoll.c"`（一個 `.c` 檔！）。這對新手是震撼，對讀碼是**重要架構線索**：redis 用「編譯期選一個 backend `.c` include 進來」的 pattern 挑事件多工實作（epoll / kqueue / select）。警告把你導到 redis 事件層的可移植性設計核心——你順著它就懂了 `ae_epoll.c` / `ae_kqueue.c` / `ae_select.c` 為什麼不各自獨立編譯。

- **`clang-analyzer-core.NullDereference`**：這是**路徑敏感**分析的展示。它不只說「這裡可能 null 解參」，還用一連串 `note:` **把觸發路徑演給你看**：進入 `aeProcessEvents` 的迴圈 → 某些條件成立 → 假設 `beforesleep == NULL` → …一路推到 `earliest` 為 NULL 時 `earliest->when` 解參。這串 note 就是一條**現成的閱讀路徑**——你想理解 `usUntilEarliestTimer` 的邊界行為，跟著這串 note 走一遍，比自己盲讀省事得多。（實務上這多半是誤報：`earliest` 為 NULL 時另一條分支已 return，但分析器的保守假設仍幫你把那條路徑攤開了。）

## scan-build：把觸發路徑變成可點的報告

`scan-build` 是 clang static analyzer 的驅動器：它**包住你的 build 命令**，對每個編譯單元跑路徑分析，產出 HTML 報告。真跑（單檔）：

```
$ scan-build-14 -o /tmp/scanout --status-bugs \
    clang-14 -c src/ae.c -o /tmp/ae.o -I src -I deps/hiredis -I deps/linenoise -I deps/lua/src
scan-build: Using '/usr/lib/llvm-14/bin/clang' for static analysis
scan-build: Analysis run complete.
scan-build: 1 bug found.
scan-build: Run 'scan-view /tmp/scanout/2026-08-01-...' to examine bug reports.
```

找到 1 個 bug，產出 HTML：

```
$ ls /tmp/scanout/2026-08-01-024404-51600-1
index.html  report-d71110.html  scanview.css  sorttable.js
$ grep -io 'dereference of a null pointer[^<]*' report-*.html | head -1
dereference of a null pointer (loaded from variable 'earliest')
```

同一個 `earliest` null-deref，但 scan-build 的殺手鐧是 **HTML 報告把觸發路徑逐步高亮在原始碼上**（`scan-view` 或直接瀏覽器開 `index.html`）——每一步 note 對應一行 code，滑鼠點過去像看 debugger 的 backtrace，但是**靜態、不用真跑**。對讀「一條複雜路徑到底怎麼走到某狀態」，這份互動報告是很強的閱讀輔助。

實務上你會 `scan-build make`（包住整個 build）一次掃全專案，得到一份全庫的可疑點地圖。這裡用單檔是為了輸出乾淨可貼。

> **未實測部分**：整專案 `scan-build make` 我沒跑（會編譯全 redis、耗時且輸出龐雜），但單檔驗證了 scan-build 的機制與報告格式。整專案用法只是把 `clang-14 -c ...` 換成 `make`，原理相同。

## cflow → dot → PNG：把呼叫關係畫成圖

警告導航到「點」，呼叫圖給你「面」。`cflow`（Ch 0、Ch 9 見過）靜態抽取呼叫關係。它本身不畫圖，但我們可以把它的縮排樹轉成 graphviz 的 `dot`，再渲染成 PNG。

先看 cflow 對事件迴圈心臟 `aeMain` 往下三層：

```
$ cflow -m aeMain --depth=4 src/ae.c
aeMain() <void aeMain (aeEventLoop *eventLoop) at src/ae.c:474>:
    aeProcessEvents() <int aeProcessEvents (aeEventLoop *eventLoop, int flags) at src/ae.c:342>:
        usUntilEarliestTimer() <int64_t usUntilEarliestTimer (...) at src/ae.c:245>:
            getMonotonicUs()
        aeApiPoll()
        processTimeEvents() <int processTimeEvents (...) at src/ae.c:261>:
            getMonotonicUs()
            zfree()
```

用一小段 awk 把「縮排層級」轉成 dot 的邊（父→子）：

```bash
cflow -m aeMain --depth=4 src/ae.c | awk '
function name(l,  s){ s=l; sub(/\(.*/,"",s); gsub(/[ \t]/,"",s); return s }
{ match($0,/^ */); ind=RLENGTH/4; n=name($0); stack[ind]=n
  if(ind>0) printf "  \"%s\" -> \"%s\";\n", stack[ind-1], n }
BEGIN{ print "digraph callgraph { rankdir=LR; node[shape=box,fontname=monospace];" }
END{ print "}" }' > aemain.dot
```

產出的 dot（真實輸出）：

```
digraph callgraph {
  rankdir=LR; node[shape=box,fontname=monospace];
  "aeMain" -> "aeProcessEvents";
  "aeProcessEvents" -> "usUntilEarliestTimer";
  "usUntilEarliestTimer" -> "getMonotonicUs";
  "aeProcessEvents" -> "aeApiPoll";
  "aeProcessEvents" -> "processTimeEvents";
  "processTimeEvents" -> "getMonotonicUs";
  "processTimeEvents" -> "zfree";
}
```

渲染成 PNG：

```
$ dot -Tpng aemain.dot -o aemain.png
$ file aemain.png
aemain.png: PNG image data, 848 x 203, 8-bit/color RGBA, non-interlaced
```

一張 848×203 的呼叫圖出爐。這就是 Ch 0 說的「cflow 是架構地圖的原料」的完整落地：**cflow 抽關係 → awk 轉 dot → graphviz 渲染**。你可以對任何入口函式做這個 pipeline，得到一張「這個函式往下呼叫了什麼」的地圖，貼進你的讀碼筆記（Ch 35 外化理解）。

cflow 也能反查——`-r`（reverse）列「誰呼叫了某函式」：

```
$ cflow -r --depth=2 -m aeProcessEvents src/*.c | head -4
ACLAddAllowedFirstArg() <...>:
    ACLSetSelector() <...>:
ACLAddCommandCategory() <...>:
    RM_AddACLCategory() <...>
```

（reverse 模式對整個 src 掃，會列出大量呼叫關係——適合找「這個底層函式被哪些上層用到」。）

## doxygen + dot：一鍵產 call / caller / include 圖

doxygen 不只產 API 文件，配 graphviz 還能自動畫**呼叫圖（call graph）**、**被呼叫圖（caller graph）**、**include 圖**。對讀碼的價值：一次跑完，每個函式都有一張「它呼叫誰 / 誰呼叫它」的圖，滑鼠點就跳。

真跑（scoped 到 `ae.c` 以求快）：

```
$ doxygen -g Doxyfile           # 產預設設定檔
# 關鍵設定（用 sed 或編輯器改）：
#   INPUT            = .../src/ae.c
#   EXTRACT_ALL      = YES        # 連沒 doc 註解的也抽
#   HAVE_DOT         = YES        # 啟用 graphviz
#   CALL_GRAPH       = YES        # 產呼叫圖
#   CALLER_GRAPH     = YES        # 產被呼叫圖
#   DOT_IMAGE_FORMAT = png
$ doxygen Doxyfile
Running dot for graph 2/4
Running dot for graph 3/4
Running dot for graph 4/4
finished...
```

產出的圖檔（真實輸出）：

```
$ find html -name "*cgraph*" -o -name "*icgraph*" | head
html/ae_8c_..._cgraph.dot     ← call graph（它呼叫誰）
html/ae_8c_..._cgraph.png
html/ae_8c_..._icgraph.dot    ← inverse call graph（誰呼叫它）
html/ae_8c_..._icgraph.png
html/ae_8c__incl.png          ← include 圖
```

看一張 caller graph 的 dot 內容（`aeProcessEvents` 的被呼叫圖）：

```
$ head -12 html/ae_8c_..._icgraph.dot
digraph "aeProcessEvents"
{
  rankdir="RL";
  Node1 [label="aeProcessEvents", fillcolor="grey75", style="filled", ...];
  Node1 -> Node2 [dir="back", color="midnightblue", ...];
  Node2 [label="aeMain", ..., URL="$ae_8c.html#...", ...];
}
```

`aeProcessEvents ← aeMain`：被 `aeMain` 呼叫，箭頭 `dir="back"` 表反向。`URL="..."` 讓 HTML 裡的節點可點擊跳轉。用瀏覽器開 `html/index.html`，你就有一份**每個函式都附呼叫圖與被呼叫圖、且互相超連結**的離線導覽——比手畫地圖省力，比純讀 code 直觀。

doxygen 同時產 **include 圖**（`ae_8c__incl.png`），對「這個檔的依賴長什麼樣」一目了然。看它的 dot（真實輸出，節略）：

```
$ head -12 html/ae_8c__incl.dot
digraph "/home/ypp/reading_code_lab/redis/src/ae.c"
{
  Node1 [label="ae.c", fillcolor="grey75", style="filled", ...];
  Node1 -> Node2 ...   Node2 [label="ae.h", ...];
  Node1 -> Node3 ...   Node3 [label="anet.h", ...];
  Node1 -> Node4 ...   Node4 [label="redisassert.h", ...];
```

`ae.c → ae.h / anet.h / redisassert.h`。include 圖是讀碼時判斷「這個檔屬於哪一層、依賴誰」的快速線索——依賴少的檔通常是底層 leaf（好懂、好抽出來單獨讀），依賴 `server.h` 的檔通常是與核心糾纏的上層（要連著讀）。這跟 Ch 7 建架構地圖直接呼應：**依賴圖就是模組分層的骨架。**

**取捨**：doxygen 全庫跑很慢、產大量檔，且它的呼叫圖是**靜態語法層**的（跟 cflow 一樣，看不穿函式指標/虛擬呼叫——呼應 Ch 23 讀 indirection）。適合對「一個模組」產圖精讀，不適合無腦全庫。

## 對比與取捨

| 工具 | 強項 | 弱項 | 讀碼時機 |
|---|---|---|---|
| **cppcheck** | 快、不用編譯、pattern 直觀 | 較淺、`#ifdef` 覆蓋有限 | 陌生 C 檔第一掃 |
| **clang-tidy** | check 可選、bugprone 實用 | 要 compile db、噪音多 | 有 compile db 時的深掃 |
| **scan-build** | **路徑敏感**、HTML 路徑報告 | 慢、誤報、要包住 build | 追一條複雜路徑的行為 |
| **cflow** | 輕、可轉 dot、雙向 | 純語法、看不穿函式指標 | 對入口函式快速產呼叫圖 |
| **doxygen+dot** | 全模組 call/caller/include 圖、可點 | 慢、產物多、靜態層 | 精讀一個模組時產全套圖 |

**分工**：可疑點導航用 cppcheck（快）→ clang-tidy / scan-build（深、附路徑）；結構全貌用 cflow（點狀、輕）→ doxygen（模組、全套）。警告帶你到點，圖給你面，兩者互補。

## 踩雷集錦

1. **把警告一律當 bug**。錯誤直覺：「分析器報了就是錯」。正確認識：靜態分析**誤報率不低**（尤其 clang-analyzer 的 null-deref 常被保守假設觸發）。讀碼時警告的價值是「這裡值得看」，不是「這裡一定錯」。別急著改，先讀懂作者的 invariant。

2. **clang-tidy 沒 compile db 就半殘或報一堆 include 錯**。它跟 clangd 一樣要 `compile_commands.json`（Ch 0）。用 `-p <目錄>` 指到 compile db，否則它不知道 `-I`/`-D`，分析結果不可信。cppcheck 沒這問題（自帶 parser）。

3. **被 clang-tidy 的 `N warnings generated` 嚇到**。`ae.c` 一跑 2546 warnings——那多半是它把 `#include` 進來的 header 也算。用 `--checks` 精選 check 集、`-header-filter` 限制範圍，別被總數迷惑，看**你 checks 過濾後真正列出的那幾條**。

4. **doxygen 沒 `HAVE_DOT=YES` 或沒裝 graphviz，圖是空的**。呼叫圖要 graphviz 的 `dot`。設定漏了 `HAVE_DOT`/`CALL_GRAPH`/`CALLER_GRAPH`，doxygen 靜靜產文件但沒圖，你會以為它不會畫。

5. **cflow / doxygen 的呼叫圖看不穿 indirection**。兩者都是純語法：`callback(x)` 這種透過函式指標的呼叫，圖上斷掉。看到某函式「沒有呼叫者」別急著結論它是死碼——它可能是被塞進某個 vtable / 命令表、由函式指標呼叫（redis 的命令表正是如此，Ch 23 詳談）。

6. **在整專案上盲跑 scan-build/doxygen 等半天**。全 redis `scan-build make` 或 doxygen 全庫要跑很久、產一堆東西。讀碼時先 scope 到你關心的檔/模組（本章都這麼做），要全庫掃再另外排時間。

## 進階：再往深一層

- **把警告接進讀碼工作流**：`cppcheck --template=gcc`（輸出成 gcc 格式）或 clang-tidy 的輸出都能塞進編輯器 quickfix list（vim `:cf`、VS Code Problems），變成「一個一個跳到可疑點讀」的清單。等於用分析器產生你的閱讀 TODO。

- **IWYU（include-what-you-use）**：clang 家的另一工具，指出「你 include 了但沒用到 / 用到了但靠傳遞 include 的 header」。讀碼用途：**看一個檔真正依賴什麼**——它的 include 清單常有歷史包袱，IWYU 幫你分出「真依賴 vs 陳年殘留」。本機**未安裝**，理論預期輸出為每個 `.h` 建議 add/remove。

- **CodeQL / semgrep**：比本章工具高一階的「可查詢的靜態分析」——你用類 SQL（CodeQL）或 pattern（semgrep）**主動問**「有沒有 taint 從 `recv` 流到 `system` 而沒過濾」。這是把「警告導航」升級成「假設驅動的漏洞查詢」（Ch 10、Ch 32）。本章工具是現成規則，CodeQL/semgrep 是你自己寫規則。

- **編譯器警告本身就是分析器**：`gcc -Wall -Wextra`、`-fanalyzer`（GCC 10+ 內建路徑敏感分析）也會標出可疑處。讀碼時把陌生專案用高 `-W` 等級編一次，warning 清單就是免費導航——前提是它能編譯。`-fanalyzer` 的輸出跟 scan-build 一樣會**編號事件、逐步演路徑**。真跑一個洩漏範例（gcc 11.4）：

  ```
  $ gcc -fanalyzer -c leak.c -o leak.o
  leak.c:5:23: warning: leak of ‘p’ [CWE-401] [-Wanalyzer-malloc-leak]
      5 |     if (n < 0) return -1;   /* early return: p leaked */
        |                       ^
    ‘f’: events 1-4
      |    4 |     int *p = malloc(n * sizeof(int));
      |      |              (1) allocated here
      |    5 |     if (n < 0) return -1;
      |      |        (early return: 'p' leaked)
  ```

  它連 CWE 編號（`CWE-401` memory leak）都標了，`events 1-4` 就是觸發路徑。對「能編譯」的專案，`gcc -fanalyzer` 是零額外安裝的靜態分析導航。

- **路徑報告當靜態 debugger**：scan-build 的 HTML 把「怎麼走到 bug 狀態」逐行攤開，本質是一次**靜態的執行路徑重建**。當你想理解「什麼條件下會走到這個 error 分支」卻懶得架 gdb（Ch 18）時，這份報告常常就夠了。

## 動手練習

1. **順著警告讀懂一個函式**：對 `src/util.c` 跑 `cppcheck --enable=warning,style`，挑 `arrayIndexThenCheck` 那條，讀 `string2ul_base16_async_signal_safe` 的呼叫端（用 Ch 14 的 `cscope -3` 或 `global -rx` 找誰呼叫它），判斷那個「先讀後檢查」到底安不安全、作者靠什麼 invariant。寫下你的結論。

2. **compile db 深掃**：對 `src/networking.c` 跑 `clang-tidy-14 -p . --checks='-*,clang-analyzer-*,bugprone-*'`，挑一條 `clang-analyzer-*` 警告，跟著它的 `note:` 路徑逐步讀 code，判斷是真 bug 還是誤報。

3. **產一張呼叫圖**：用本章的 `cflow | awk | dot` pipeline，對 `initServer`（redis 啟動核心）產一張 depth=3 的呼叫圖 PNG，貼進你的讀碼筆記。

4. **doxygen 模組圖**：把 doxygen 的 `INPUT` 指到 `src/t_string.c`（string 命令實作），開 `CALL_GRAPH`/`CALLER_GRAPH`，產圖後用瀏覽器開 `html/`，找 `setGenericCommand` 的呼叫圖與被呼叫圖。

5. **對照 indirection 盲點**：在 doxygen 的圖裡找一個「沒有呼叫者」的命令實作函式（如 `getCommand`），再用 `rg 'getCommand'` 找到它其實被塞進 redis 的命令表。體會靜態圖為什麼漏掉這條——為 Ch 23 暖身。

## 本章重點整理

- 核心心法：**警告是免費的讀碼導航**——分析器標紅處往往是 invariant 藏最深、最值得優先精讀的地方；讀碼時問的不是「這是不是 bug」，而是「作者在防什麼」。
- **cppcheck**：快、不用編譯、第一掃；`arrayIndexThenCheck` 這種警告把你導到「短路求值 + 隱藏輸入契約」的微妙處。
- **clang-tidy**：要 compile db，`bugprone-*`/`clang-analyzer-*` 實用；能揭露 `#include "*.c"` 這種架構 pattern。
- **scan-build / clang-analyzer**：**路徑敏感**，用一串 `note:` 把觸發路徑攤開，HTML 報告像靜態 debugger——追複雜路徑行為的利器。
- **cflow → dot → PNG** 與 **doxygen + graphviz** 把靜態呼叫關係畫成圖（call / caller / include）；本章完整真跑了 pipeline。
- 天花板：cflow / doxygen 是純語法圖，**看不穿函式指標/虛擬呼叫**（Ch 23）；警告有誤報，別當判決。

## 自我檢核

- [ ] 能說出「為什麼靜態分析警告是好的讀碼導航」的三個理由嗎？
- [ ] cppcheck 相對 clang-tidy 的關鍵差別（要不要 compile db）是什麼？
- [ ] scan-build 的 HTML 路徑報告為什麼像「靜態 debugger」？它省了什麼？
- [ ] 給你一條 `arrayIndexThenCheck` 警告，你的讀碼動作會是什麼（不是「立刻改」）？
- [ ] cflow/doxygen 的呼叫圖有什麼結構性盲點？看到「無呼叫者」的函式該懷疑什麼？
- [ ] 面試官問「你怎麼用工具幫自己讀懂陌生 code」，你能把「警告導航 + 呼叫圖」講成一套方法嗎？

到這裡，Part 3 的「符號 / 結構 / 語意 / 分析」四類靜態工具你都握在手裡了。但 code 還有一個維度是所有這些工具都看不到的：**時間**——這行是誰、哪個 commit、為了什麼加的？下一章我們把 git 當考古工具，從版本歷史裡挖出「這段 code 的來歷與意圖」，那往往是理解「為什麼長這樣」的最後一塊拼圖。

→ [Ch 17 git 當考古工具](./17-git-as-archaeology.md)
