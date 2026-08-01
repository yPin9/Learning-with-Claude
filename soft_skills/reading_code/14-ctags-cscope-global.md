# Ch 14 — ctags / cscope / GNU global：離線索引三巨頭

> **目標**：把三大離線程式碼索引工具的原理、分工與極限吃透。你會知道 ctags 給你「定義在哪」、cscope 給你「誰呼叫誰」的九種問答、GNU global 給你「腳本友善、可產 HTML」的第三條路；三者在同一個查詢（「誰呼叫 `aeMain`」）上真跑對照，看清各自的答案格式與盲點。讀完你有能力在一個沒網路、沒 LSP、幾百萬行的 C 專案裡，用秒級指令回答讀碼最核心的幾個問題。

> **環境**：WSL2 Ubuntu 22.04。Universal Ctags 5.9.0、cscope 15.9、GNU global/gtags 6.6.7。沙包 `~/reading_code_lab/redis`（redis 7.4.0），已預建 `tags`、`cscope.out`、`compile_commands.json`。本章所有輸出皆為當場真跑照抄。

## 為什麼還要離線索引？clangd 不是更準嗎？

Ch 13 我們談了 LSP/clangd：它真的編譯你的 code，答案語意精準——`read` 是哪個 `read`、巨集展開後長什麼樣，它都知道。那為什麼不全用它？

因為 LSP 有三個致命的實務成本：

1. **要能編譯**：clangd 沒有 `compile_commands.json` 就半殘。你接手的 legacy 專案可能根本 build 不起來（缺 header、要特定 cross toolchain、要一台已經退役的機器）。ctags/cscope/global **完全不編譯**，指到目錄就索引。
2. **啟動慢、記憶體重**：clangd 在幾百萬行的 codebase（Linux kernel、Chromium）上，開一個檔要背景 index 幾分鐘、吃幾 GB RAM。ctags 索引整個 redis src 只要 **0.115 秒**（等下真跑給你看）。
3. **不好腳本化**：LSP 是一個跟編輯器對話的 JSON-RPC server，你要「批次查 500 個函式各被誰呼叫、輸出成 CSV」時，它幫不上忙。cscope 的 `-L` line mode 和 global 的 `--result=grep` 就是為此而生。

所以真實的讀碼工作流是**分層**的：離線索引當你的粗地圖與批次查詢引擎（快、離線、可腳本），需要語意精準時（同名、多載、巨集真相）才叫 clangd 上場。這章講前者的三巨頭。

先建立一張心智圖——三者的核心分工：

```
  問題                              最適工具         為什麼
 ┌──────────────────────────┐
 │ 這符號「定義」在哪？     │ →  ctags        純語法掃定義，最輕最快
 │ 「誰呼叫」它 / 它呼叫誰？│ →  cscope       C 專用、九種交叉查詢最全
 │ 要腳本化 / 產 HTML 導覽？│ →  GNU global   輸出格式最乾淨、多語言
 └──────────────────────────┘
```

三者不是三選一，實務常常**同時建、各取所長**。

## ctags：定義索引，純語法、極輕

### 原理：它不懂語意，它認「定義長什麼樣」

ctags 的工作只有一件：掃過每個檔案，用**語法 pattern**（不是完整 parser，更不是編譯器）認出「這裡定義了一個符號」——函式、struct、巨集、typedef、enum 成員——把「符號名 → 檔案 → 行的搜尋 pattern」記進一張排序表 `tags`。

關鍵字是**純語法**。它不知道作用域、不知道型別、不知道兩個同名 `main` 哪個是真的。它只認得「`int main(...) {` 這個形狀是一個函式定義」。這是它的力量（快、不用編譯、任何殘缺 code 都能掃）也是它的極限（同名不分辨、不會反查呼叫者）。

### 真跑：看 tags 檔的結構

沙包已預建 `tags`。先看它的體積與長相：

```
$ wc -l tags
11171 tags

$ head -8 tags
!_TAG_FILE_FORMAT	2	/extended format; --format=1 will not append ;" to lines/
!_TAG_FILE_SORTED	1	/0=unsorted, 1=sorted, 2=foldcase/
!_TAG_OUTPUT_EXCMD	mixed	/number, pattern, mixed, or combineV2/
!_TAG_OUTPUT_FILESEP	slash	/slash or backslash/
!_TAG_OUTPUT_MODE	u-ctags	/u-ctags or e-ctags/
!_TAG_PATTERN_LENGTH_LIMIT	96	/0 for no limit/
!_TAG_PROC_CWD	/home/ypp/reading_code_lab/redis/	//
!_TAG_PROGRAM_AUTHOR	Universal Ctags Team	//
```

`!_TAG_FILE_SORTED	1` 是關鍵：tags 檔**已排序**，所以查詢可以做二分搜尋，這是它秒回的原因。11171 個符號，一個 1.3 MB 的純文字檔。

### readtags：不開編輯器也能查

編輯器（vim `Ctrl+]`、Emacs、VS Code ctags 外掛）背後查的就是這張表。但我們可以用 `readtags` 直接在命令列查，看清楚它給什麼：

```
$ readtags -t tags aeMain
aeMain	src/ae.c	/^void aeMain(aeEventLoop *eventLoop) {$/
```

三欄：**符號名、檔案、跳轉 pattern**（用一段 regex `/^...$/` 而非行號——這樣即使檔案改了幾行，只要那行本身沒變，還跳得到，比純行號 robust）。

同名的殘酷現實——查 `main`：

```
$ readtags -t tags main | head -6
main	src/crc64.c	/^int main(int argc, char *argv[]) {$/
main	src/localtime.c	/^int main(void) {$/
main	src/mt19937-64.c	/^int main(void)$/
main	src/redis-benchmark.c	/^int main(int argc, char **argv) {$/
main	src/redis-cli.c	/^int main(int argc, char **argv) {$/
main	src/server.c	/^int main(int argc, char **argv) {$/
```

六個 `main`（redis 全專案有八個，這裡只列 6 個是因為這份 tags 只索引了 `src/`）。ctags **全給你、不排序哪個對**——它沒有語意，分不出伺服器入口與自測 harness。判斷是你的事。這就是 Ch 0 說的「ctags 純語法、不懂哪個才是真的」。

### 進階：擠出更多欄位（kind / signature / typeref）

預設 tags 只有三欄，但 ctags 能吐更多。`--fields=+iaSKt` 開啟繼承/存取/簽章/kind/typeref：

```
$ ctags --fields=+iaSKt -R --languages=C,C++ -f tags.rich src/ae.c src/server.c
$ grep -P '^aeMain\t' tags.rich
aeMain	src/ae.c	/^void aeMain(aeEventLoop *eventLoop) {$/;"	function	typeref:typename:void	signature:(aeEventLoop * eventLoop)
```

現在多了 `function`（kind）、`typeref:typename:void`（回傳型別）、`signature:(aeEventLoop * eventLoop)`（參數）。這些欄位讓工具能做更聰明的過濾——例如「只列 struct 成員」「只列回傳 `int` 的函式」。struct/變數也認得型別：

```
$ grep 'typeref:' tags.rich | head -3
ARG_TYPE_STR	src/server.c	/^const char *ARG_TYPE_STR[] = {$/;"	variable	typeref:typename:const char * []
ClientsPeakMemInput	src/server.c	/^size_t ClientsPeakMemInput[...] = {0};$/;"	variable	typeref:typename:size_t[]
InitServerLast	src/server.c	/^void InitServerLast(void) {$/;"	function	typeref:typename:void	signature:(void)
```

### 建索引有多快

```
$ time ctags -R --languages=C,C++ -f /tmp/tags_time src/
real	0m0.115s
```

**115 毫秒**掃完十萬行 C。這個數字是 ctags 存在的理由：你可以每次 `git pull` 後無腦重建，成本可忽略。

## cscope：C 讀碼的九種問答

### 原理：不只索引定義，還索引「使用」

ctags 只記「定義在哪」。cscope 更進一步——它建的是**交叉引用資料庫** `cscope.out`：不只哪裡定義了 `aeMain`，還記下**每一處用到 `aeMain` 的地方**，以及那個用法是「呼叫」「賦值」還是單純提及。這讓它能回答讀碼真正卡人的問題：**「誰呼叫了這個函式？」**（反向）。

cscope 對 C 的支援最深，因為它認得 C 的呼叫語法 `foo(...)`。它把讀碼需求歸納成**九種查詢**，這是它最值錢的設計。TUI（直接跑 `cscope -d`）有九個輸入框；我們用 `-L -N` line mode（適合腳本、也適合貼進教材）逐一真跑。

九種查詢對應的 `-N` 數字：

| `-N` | 查什麼 | 讀碼用途 |
|---|---|---|
| `-0` | 找這個 C **符號**（所有出現） | 全覽一個符號的蹤跡 |
| `-1` | 找**全域定義** | 跳到定義（同 ctags） |
| `-2` | 找此函式**呼叫的函式**（callee） | 往下追：它做了什麼 |
| `-3` | 找**呼叫此函式的函式**（caller） | 往上追：誰用它 |
| `-4` | 找**文字字串** | 定位 log 訊息、錯誤字串 |
| `-5` | **修改**文字（互動） | 批次改名（少用） |
| `-6` | 找 **egrep pattern** | regex 搜尋 |
| `-7` | 找**檔案** | 依名找檔 |
| `-8` | 找 **#include 此檔的檔案** | 依賴反查 |
| `-9` | 找對此符號的**賦值** | data flow 起點 |

### 真跑：同一個 `aeMain`，四種角度

沙包已預建 `cscope.out`（含 deps/hiredis，所以下面會看到 hiredis 的結果）。

**`-0` 全部出現**：

```
$ cscope -d -L -0 aeMain
src/ae.h <global> 107 void aeMain(aeEventLoop *eventLoop);
deps/hiredis/examples/example-ae.c main 59 aeMain(loop);
src/ae.c aeMain 474 void aeMain(aeEventLoop *eventLoop) {
src/redis-benchmark.c benchmark 970 if (!config.num_threads) aeMain(config.el);
src/redis-benchmark.c execBenchmarkThread 1010 aeMain(thread->el);
src/redis-benchmark.c main 1829 else aeMain(config.el);
src/server.c main 7251 aeMain(server.el);
```

輸出四欄：**檔案、所在函式、行號、原始行**。注意「所在函式」欄——`server.c main 7251` 告訴你這個呼叫發生在 `main` 裡面，這是 ctags 給不了的上下文。

**`-1` 只要定義**：

```
$ cscope -d -L -1 aeMain
src/ae.c aeMain 474 void aeMain(aeEventLoop *eventLoop) {
```

**`-2` 它呼叫了誰**（callee）：

```
$ cscope -d -L -2 aeMain
src/ae.c aeProcessEvents 477 aeProcessEvents(eventLoop, AE_ALL_EVENTS|
```

`aeMain` 的函式體裡就呼叫了 `aeProcessEvents`——這正是事件迴圈的核心，Ch 6 的心臟。

**`-3` 誰呼叫它**（caller，讀碼最需要的反查）：

```
$ cscope -d -L -3 aeMain
deps/hiredis/examples/example-ae.c main 59 aeMain(loop);
src/redis-benchmark.c benchmark 970 if (!config.num_threads) aeMain(config.el);
src/redis-benchmark.c execBenchmarkThread 1010 aeMain(thread->el);
src/redis-benchmark.c main 1829 else aeMain(config.el);
src/server.c main 7251 aeMain(server.el);
```

`-3` 過濾掉了定義本身與 header 宣告，只留**真正的呼叫點**。伺服器的心臟是 `server.c:7251` 的 `aeMain(server.el)`——你剛用一條指令從十萬行定位到系統核心循環。

### 其餘查詢：字串、pattern、include、賦值

```
$ cscope -d -L -4 "Ready to accept"        # -4 找文字
src/server.c <unknown> 7224   serverLog(LL_NOTICE,"Ready to accept connections %s", ...);
src/server.c <unknown> 7229   redisCommunicateSystemd("STATUS=Ready to accept connections\n");
...

$ cscope -d -L -6 "ae(Create|Delete)FileEvent"   # -6 egrep pattern
src/ae.c <unknown> 143 int aeCreateFileEvent(aeEventLoop *eventLoop, int fd, int mask,
src/ae.c <unknown> 163 void aeDeleteFileEvent(aeEventLoop *eventLoop, int fd, int mask)

$ cscope -d -L -8 "ae.h"                    # -8 誰 #include 了 ae.h
src/ae.c <global> 12 #include "ae.h"
src/connection.h <global> 18 #include "ae.h"
src/redis-benchmark.c <global> 26 #include "ae.h"
src/server.h <global> 51 #include "ae.h"
...
```

`-8` 特別有用：你想改 `ae.h` 的某個 struct，先用它看**衝擊面**——哪些檔案會受影響。`-9`（賦值）是 data flow 追蹤的起點（Ch 8），查「誰改了 `server.el`」；此例在預建庫裡沒有直接命中，因為賦值多以 `server.el = aeCreateEventLoop(...)` 出現在 struct 成員上，cscope 的賦值偵測對 `a.b` 形式較保守——這正是它「半語意」的邊界。

### 建 cscope.out 的正規做法與速度

```
$ find ~/reading_code_lab/redis/src -name '*.c' -o -name '*.h' > cscope.files
$ time cscope -b -q -k
real	0m0.165s
```

`-b` 只建庫不進 TUI、`-q` 建反向索引（讓 `-3` 快）、`-k` 「kernel mode」不搜尋 `/usr/include`。**165 毫秒**。`cscope.files` 明列要索引的檔，比 `-R` 遞迴更可控（能排除 test、third-party）。

## GNU global（gtags）：腳本友善、可產 HTML

### 定位：cscope 的近親，但輸出更乾淨、能導覽

GNU global 做的事跟 cscope 高度重疊（都建交叉引用），差別在**工程整合**：

- 輸出格式乾淨、欄位固定，`--result=grep` 直接吐 `file:line:text`，接 `awk`/`fzf`/editor 零摩擦。
- 支援更多語言（不只 C；透過 ctags/pygments plugin 認 Go/Rust/Python…）。
- 內建 `htags` 產**可點擊的 HTML 交叉引用網站**，適合分享給團隊或離線瀏覽。
- 建索引不需要能編譯，也不需要外部 parser（內建 C parser）。

### 真跑：建 GTAGS（快到誇張）

```
$ gtags
$ time gtags
real	0m0.303s
$ ls -la GTAGS GRTAGS GPATH
-rw-r--r-- 1 ypp ypp  344064 ... GPATH
-rw-r--r-- 1 ypp ypp 5160960 ... GRTAGS
-rw-r--r-- 1 ypp ypp 1564672 ... GTAGS
```

三個檔：`GTAGS`（定義）、`GRTAGS`（引用/reference）、`GPATH`（路徑映射）。**303 毫秒**建完。

### 查詢：定義、引用、腳本格式、補全

```
$ global -x aeMain                     # -x 定義，帶行號與原始行
aeMain            474 src/ae.c         void aeMain(aeEventLoop *eventLoop) {

$ global -rx aeMain                    # -r 引用（references / 呼叫者+宣告）
aeMain             59 deps/hiredis/examples/example-ae.c     aeMain(loop);
aeMain            107 src/ae.h         void aeMain(aeEventLoop *eventLoop);
aeMain            970 src/redis-benchmark.c     if (!config.num_threads) aeMain(config.el);
aeMain           1010 src/redis-benchmark.c     aeMain(thread->el);
aeMain           1829 src/redis-benchmark.c         else aeMain(config.el);
aeMain           7251 src/server.c         aeMain(server.el);

$ global --result=grep aeMain          # 腳本友善格式：file:line:text
src/ae.c:474:void aeMain(aeEventLoop *eventLoop) {
```

`--result=grep` 這個格式是 global 相對 cscope 的甜點：任何吃 grep 輸出的工具（編輯器的 quickfix、`fzf`、CI script）都能直接消化。

其他好用查詢：

```
$ global -f src/ae.c | head -3         # -f 列出某檔所有定義（像迷你 outline）
aeCreateEventLoop   46 src/ae.c         aeEventLoop *aeCreateEventLoop(int setsize) {
aeGetSetSize       81 src/ae.c         int aeGetSetSize(aeEventLoop *eventLoop) {
aeSetDontWait      90 src/ae.c         void aeSetDontWait(aeEventLoop *eventLoop, int noWait) {

$ global -c ae | head -4               # -c 前綴補全（做 shell 補全用）
aeApiAddEvent
aeApiAssociate
aeApiCreate
aeApiDelEvent
```

### htags：一鍵產 HTML 交叉引用網站

```
$ htags -h -F                          # -h 加函式列表、-F 加檔案框架
$ ls HTML | head
D  FILEMAP  I  J  R  S  defines  defines.html  files  files.html  ...
```

`HTML/` 目錄是一個完整的靜態網站：每個符號都是超連結，點 `aeMain` 跳到定義，反查引用也是連結。這在**沒有 IDE 的環境**（遠端伺服器、只能瀏覽器）或**要給別人一份可導覽 snapshot** 時非常實用。

## 同一問題三工具對照：「誰呼叫 aeMain」

把三巨頭放在同一個查詢上，一眼看清分工：

| 工具 | 指令 | 回答「誰呼叫 aeMain」？ | 輸出上下文 |
|---|---|---|---|
| **ctags** | `readtags -t tags aeMain` | ✗ **不能**（只給定義） | 符號/檔/pattern |
| **cscope** | `cscope -d -L -3 aeMain` | ✓ 精準（`-3` 專查 caller） | 檔/**所在函式**/行號/原始行 |
| **global** | `global -rx aeMain` | ✓ 但混入宣告 | 符號/行號/檔/原始行 |

三個重點：

1. **ctags 根本沒有「反查呼叫者」這個功能**。它是定義索引，就這樣。想反查，得用 cscope 或 global。
2. **cscope `-3` 最精準**——它區分「呼叫」「宣告」「定義」，只回真正的呼叫點，還告訴你呼叫發生在哪個函式裡（`server.c main`）。這是 C 讀碼的殺手級能力。
3. **global `-rx` 給的是 references**（引用），會把 header 的宣告 `ae.h:107` 也算進去，語意上比 cscope `-3` 粗一點，但格式更乾淨、更好接腳本。

實務建議：**純 C 專案、要反查呼叫者、用 TUI 探索** → cscope。**要腳本化 / 多語言 / 產 HTML 分享** → global。**只要跳定義** → ctags 最輕。

## 索引更新自動化：別讓索引比 code 舊

索引是**快照**。你 `git pull` 或切 branch 後 code 變了，`tags`/`cscope.out`/`GTAGS` 還是舊的，跳轉會跳到錯行號——這是 Ch 0 踩雷第 5 條。三種自動化層次：

### 1. git hook（最實用）

在 `.git/hooks/post-checkout` 與 `post-merge` 放一個重建腳本，切 branch / pull 後自動更新。因為重建只要毫秒級，成本可忽略：

```bash
#!/bin/sh
# .git/hooks/post-checkout 與 post-merge 都指向這個
cd "$(git rev-parse --show-toplevel)" || exit 0
ctags -R --languages=C,C++ -f tags src/ 2>/dev/null
( find src -name '*.[ch]' > cscope.files && cscope -b -q -k ) 2>/dev/null
gtags 2>/dev/null
```

（`post-checkout` 會收到三個參數，切 branch 時第三個為 `1`；簡單起見這裡無條件重建，反正快。）

### 2. 編輯器外掛：gutentags

Neovim/Vim 的 **vim-gutentags** 幫你把上面這件事自動化：偵測 project root、背景重建 tags（甚至支援 gtags backend），存檔後增量更新。你不用寫 hook，裝外掛設好 `g:gutentags_project_root` 即可。原理仍是背後跑 ctags/gtags。

### 3. 增量更新

global 支援 `global -u`（update）只重建變動檔的索引，比全建更省；大 codebase（kernel）值得用。ctags 沒有原生增量，但因為全建太快，通常不需要。

## 對比與取捨

| 面向 | ctags | cscope | GNU global | clangd (LSP，對照) |
|---|---|---|---|---|
| 懂語意 | 否（純語法） | 半（認呼叫語法） | 半 | **是**（真編譯） |
| 要能編譯 | 否 | 否 | 否 | **是** |
| 反查呼叫者 | **不能** | ✓ 最強（`-3`） | ✓（references） | ✓ 精準 |
| 建索引速度（redis src） | 0.115s | 0.165s | 0.303s | 分鐘級（背景） |
| 腳本友善 | 中（readtags） | 中（`-L`） | **高**（`--result=grep`） | 低（JSON-RPC） |
| 多語言 | 廣 | C/C++ 為主 | 廣（plugin） | 依 language server |
| 產 HTML 導覽 | 否 | 否 | **是**（htags） | 否 |
| 同名分辨 | 否 | 否 | 否 | **是**（作用域） |

**選型決策樹**：離線 / build 不起來 / 超大 codebase / 要批次腳本查 → 三巨頭。要跳定義最輕 → ctags。要反查呼叫者且是 C → cscope。要腳本化或分享 HTML → global。需要「同名/多載/巨集展開後的真相」→ 這是三巨頭的天花板，換 clangd。

## 踩雷集錦

1. **以為 ctags 能反查呼叫者**。錯誤直覺：「ctags 建了索引，應該什麼都能查」。正確認識：ctags **只索引定義**，沒有「誰呼叫」的資料。反查是 cscope/global 的事。分不清這條，你會浪費時間在 ctags 上找它根本沒有的功能。

2. **cscope `-2`/`-3` 的方向搞反**。`-2` 是「這函式**呼叫**誰」（往下、callee），`-3` 是「誰**呼叫**這函式」（往上、caller）。記法：`-3` 找「上游三代祖宗」。搞反了你追 data flow 會整個朝錯方向。

3. **索引沒更新就跳到錯行**。切 branch / pull 後不重建，`tags` 還指著舊行號，跳轉跳到不相干的地方，然後懷疑工具壞了。正確認識：索引是快照，**大改動後必重建**（或裝 git hook / gutentags 自動化）。

4. **cscope 把 `/usr/include` 也吞進去**。不加 `-k`（kernel mode）時，cscope 會去搜系統標頭，索引膨脹、查詢混入一堆 libc 符號。純專案內查詢一律加 `-k`，並用 `cscope.files` 明列範圍。

5. **global `-r`（references）當成 cscope `-3`（caller）**。`global -rx` 給的是所有引用，**包含 header 的宣告**（例如 `ae.h:107`），不是純呼叫點。要「只有呼叫者」的精準度，cscope `-3` 更合適；global 勝在格式與多語言。

6. **在 Windows 原生硬裝**。cscope/global 在原生 Windows 上要嘛缺、要嘛半殘（Ch 0 已警告）。一律 WSL。

## 進階：再往深一層

- **cscope 的 TUI 值得會**：`cscope -d`（讀既有 `cscope.out`）進互動介面，九個輸入框對應九種查詢，`Tab` 在結果與輸入框間切換、`Ctrl+d` 離開。探索期（還不知道要查什麼）用 TUI 比背 `-N` 順手；確定要什麼、要腳本化時才用 `-L -N`。

- **編輯器整合的真相**：vim 的 `:cscope`（`cs find c aeMain` 查 caller）、`Ctrl+]`（ctags 跳定義）、`gtags.vim`，背後都是本章這三支 CLI。IDE 的「Find Usages」在沒有 LSP 時，也常 fallback 到 cscope/global。理解 CLI 才能在 IDE 失靈時手動救場。

- **global 的 plugin parser**：`gtags` 預設用內建 C/C++/Java parser；設 `GTAGSLABEL=pygments` 或 `new-ctags` 可讓它借 ctags/pygments 認幾十種語言。這是它在 polyglot 專案勝過 cscope 的地方。

- **索引大 codebase 的策略**：Linux kernel（~30M 行）建 GTAGS 約幾十秒、cscope.out 幾 GB。技巧：只索引你關心的子系統（`find drivers/net -name '*.c' > cscope.files`），或用 global 的 `-u` 增量。全建整個 kernel 通常沒必要。

- **與 ripgrep 的分工**（回扣 Ch 12）：rg 找「任意文字/log 訊息/跨語言」，索引工具找「符號的定義與呼叫關係」。典型工作流：rg 先定位大概位置 → cscope `-3` 反查呼叫鏈 → clangd 確認語意。三層各司其職。

## 動手練習

1. **三工具對照同一查詢**：對 `processTimeEvents`（redis 事件迴圈的計時器處理），分別用 `readtags -t tags processTimeEvents`、`cscope -d -L -3 processTimeEvents`、`global -rx processTimeEvents`。列出三者答案的差異，並解釋為什麼 ctags 答不出「誰呼叫」。

2. **反查衝擊面**：用 `cscope -d -L -8 "server.h"` 找出所有 `#include "server.h"` 的檔。這個數字（很大）告訴你什麼？如果你要改 `server.h` 的一個 struct 欄位，這代表什麼工程風險？

3. **建自己的索引**：對 redis src 從零建 `tags`（`ctags -R --fields=+iaSKt`）、`cscope.out`（`cscope -b -q -k`）、`GTAGS`（`gtags`），各自計時，跟本章數字對照。

4. **裝 git hook 自動化**：把本章的 `post-merge` 腳本放進 `.git/hooks/`，`chmod +x`，然後 `git pull`（或 `git checkout` 換 branch），確認索引自動重建。

5. **產 HTML 導覽**：`htags -h -F` 後，用瀏覽器（或 `python3 -m http.server` 在 WSL 起服務）開 `HTML/index.html`，點一個函式名，體會「可點擊的離線交叉引用」。

## 本章重點整理

- 三巨頭都**離線、不編譯、秒級建索引**，是 LSP 之外的另一條讀碼主幹，尤其適合 build 不起來、超大、要腳本化的場景。
- **ctags**：純語法的**定義索引**，最輕最快（0.115s）；`readtags` 查；**不能反查呼叫者**、不分辨同名。
- **cscope**：C 讀碼最強，**九種查詢**，`-2` callee / `-3` caller 是殺手級；輸出帶「所在函式」上下文；建庫加 `-k -q`。
- **GNU global**：cscope 的近親，勝在**輸出乾淨（`--result=grep`）、多語言、htags 產 HTML**；`-x` 定義、`-rx` 引用。
- 同一查詢「誰呼叫 aeMain」：ctags 答不出、cscope `-3` 最精準、global `-rx` 混入宣告但最好腳本化。
- 索引是快照，**大改動後必重建**；用 git hook（post-checkout/post-merge）或 gutentags 自動化。

## 自我檢核

- [ ] 不看筆記，能說出 ctags / cscope / global 三者的一句話核心分工嗎？
- [ ] cscope `-2` 和 `-3` 哪個是 caller、哪個是 callee？搞反會怎樣？
- [ ] 為什麼 ctags 對「誰呼叫 aeMain」束手無策，而 cscope 一條指令搞定？
- [ ] `global -rx` 和 `cscope -3` 給的東西差在哪？各自何時用？
- [ ] 這三個工具相對 clangd 的三大優勢（不編譯、快、可腳本）與一大共同天花板（不分辨同名/作用域）是什麼？
- [ ] 索引為什麼會「過期」？兩種自動更新方式是什麼？

搞定離線索引，你已經能秒答「定義在哪、誰呼叫誰」。但這些工具的天花板是**語意**——它們認不出「這個 `zmalloc` 是真的呼叫還是註解裡的字」「這段結構是不是一個 for 迴圈包 switch」。下一章換一種武器：**tree-sitter**，直接對著語法樹（AST）做結構化查詢，不再被空白、換行、格式騙。

→ [Ch 15 tree-sitter 與結構化查詢](./15-tree-sitter-structural-query.md)
