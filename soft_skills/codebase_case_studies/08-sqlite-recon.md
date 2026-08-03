# Ch 8 — SQLite 偵察：分層架構

> **目標**：用 `reading_code` 的 60 分鐘偵察 SOP（Ch 5）拿下 SQLite 的整體地圖：認出它教科書級的分層（tokenizer → parser → code generator → VDBE → B-tree → pager → OS/VFS），搞清楚 `sqlite3.c` amalgamation 單檔和 `src/` 分檔的關係，並在真 source 裡 `rg`/`wc` 印證每一層在哪。

> **目標codebase**：SQLite `version-3.47.2`（commit `262de1b`）

## 為什麼需要這個？

你剛在 Part 1 讀完 Lua——2 萬行、一個人設計、乾淨到像教材。SQLite 是另一種硬：它是**地表被讀最多次的 C 專案之一**，被塞進每一支手機、每一個瀏覽器、每一台飛機娛樂系統。它的 `src/` 有 15 萬行 C，amalgamation 更是把整個引擎壓成一個 26 萬行的 `sqlite3.c`。

但這裡有個好消息：SQLite 的架構**乾淨到官方自己畫了一張分層圖**（[sqlite.org/arch.html](https://www.sqlite.org/arch.html)）。這是讀碼的送分題——`reading_code` Ch 5 教你偵察時第一件事就是「找官方的自述文件」，SQLite 把功課做好了。我們這章的任務不是重新發明地圖，而是**拿官方的分層圖當假設，去 source 裡逐層驗證**：每一層真的存在嗎？在哪個檔？入口 function 叫什麼？

這正是偵察的本質：**不是讀懂全部，是在 60 分鐘內建出一張「哪一層在哪裡」的地圖**，之後要深挖哪條路徑，你知道從哪個檔開刀。

## 先建立直覺

SQLite 把「執行一條 SQL」拆成兩個大階段：**編譯**（把 SQL 文字變成 bytecode）和**執行**（跑那串 bytecode，最後落到磁碟）。中間夾著一個 bytecode 虛擬機（VDBE），這是它和其他資料庫最不一樣的地方——它不是直接對 parse tree 求值，而是像編譯器一樣先產生一種「機器碼」，再用一個 VM 直譯。

先把這張分層圖刻進腦子（改編自官方 arch.html，但每一格我們等一下都會在 source 裡驗證）：

```
             SQL 文字  "SELECT x FROM t WHERE id=?"
                  │
   ┌──────────────┼─────────────────────────────────┐
   │  編譯期 (compile)                                │
   │              ▼                                   │
   │      ┌───────────────┐   src/tokenize.c          │
   │      │ Tokenizer     │   sqlite3GetToken()       │
   │      └───────┬───────┘   文字 → token            │
   │              ▼                                   │
   │      ┌───────────────┐   src/parse.y (lemon)     │
   │      │ Parser        │   → 生成 parse.c          │
   │      └───────┬───────┘   token → parse tree      │
   │              ▼                                   │
   │      ┌───────────────┐   src/build.c select.c    │
   │      │ Code Generator│   where.c expr.c ...       │
   │      └───────┬───────┘   parse tree → VDBE bytecode│
   └──────────────┼─────────────────────────────────┘
                  ▼  一串 VdbeOp（opcode + p1..p5）
   ┌──────────────┼─────────────────────────────────┐
   │  執行期 (runtime)                                │
   │      ┌───────────────┐   src/vdbe.c              │
   │      │ VDBE          │   sqlite3VdbeExec() 巨型 switch│
   │      └───────┬───────┘   直譯 bytecode           │
   │              ▼                                   │
   │      ┌───────────────┐   src/btree.c             │
   │      │ B-tree        │   table btree + index btree│
   │      └───────┬───────┘   邏輯上的鍵值樹          │
   │              ▼                                   │
   │      ┌───────────────┐   src/pager.c pcache.c    │
   │      │ Pager / Cache │   page cache + 交易 + WAL │
   │      └───────┬───────┘   把「頁」交易化          │
   │              ▼                                   │
   │      ┌───────────────┐   src/os_unix.c os_win.c  │
   │      │ OS / VFS      │   xRead/xWrite（函式指標）│
   │      └───────┬───────┘   可插拔的作業系統後端    │
   └──────────────┼─────────────────────────────────┘
                  ▼
              磁碟上的 .db 檔（一堆 4KB 的頁）
```

上半是「SQL → bytecode」的編譯器，下半是「bytecode → 磁碟」的儲存引擎。**兩半用 VDBE bytecode 這個中介表示（IR）解耦**——這是本 Part 最重要的 pattern，Ch 9 專門講。

## 偵察第一步：clone 完，先數家底

`reading_code` Ch 5 的偵察 SOP 第一動：**別急著讀 code，先量規模**。用 `wc` 和 `ls` 建立「這專案多大、檔案怎麼分」的體感。以下都是在真 clone 上跑出來的：

```bash
$ cd /tmp/rd_sqlite
$ ls src/*.c | wc -l ; ls src/*.h | wc -l
127
26
$ cat src/*.c src/*.h | wc -l
214900
```

127 個 `.c`、26 個 `.h`、合計約 21 萬行。這比 Lua 大一個數量級。但**別被 21 萬行嚇到**——`reading_code` Ch 11 的鐵律是「你永遠只需要讀其中幾條路徑」。我們的地圖告訴我們，一條 `SELECT` 的生命週期只穿過大約 6 個檔。

接著量各層核心檔的大小，體感一下重量分布：

```bash
$ wc -l src/vdbe.c src/btree.c src/pager.c src/build.c src/where.c \
        src/parse.y src/os_unix.c src/vdbeInt.h src/vdbe.h src/sqlite.h.in
   9217 src/vdbe.c        ← VDBE 直譯器（巨型 switch）
  11491 src/btree.c       ← B-tree（最大的單檔）
   7809 src/pager.c       ← pager + journal
   5798 src/build.c       ← codegen（產 VDBE）
   7483 src/where.c       ← query planner（WHERE 子句）
   2056 src/parse.y       ← lemon 文法
   8266 src/os_unix.c     ← Unix VFS
    732 src/vdbeInt.h     ← Vdbe/Mem 內部型別
    428 src/vdbe.h        ← VdbeOp 對外型別
  10892 src/sqlite.h.in   ← 公開 API 定義（模板）
```

光看行數就有觀點：`btree.c` 最大（11K 行）——B-tree 的分裂、合併、overflow 頁處理是 SQLite 最複雜的機制。`vdbe.c`（9K 行）是那個巨型 switch。`where.c`（7.5K 行）整個是 query planner，這說明「決定用哪個 index」在 SQLite 裡是一等公民、複雜到要獨立一個大檔。

## 偵察第二步：認出每一層的入口 function

有了地圖，偵察 SOP 的下一動是**替每一層找到「入口」**——那個「進到這一層時第一個被呼叫的 function」。找到入口，你就有了下鑽的錨點。用 `rg` 逐層驗證我們地圖上的假設：

**Tokenizer 入口**——把 SQL 字串切成 token：

```bash
$ rg -n "int sqlite3GetToken|int sqlite3RunParser" src/tokenize.c
273:int sqlite3GetToken(const unsigned char *z, int *tokenType){
599:int sqlite3RunParser(Parse *pParse, const char *zSql){
```

`sqlite3RunParser`（tokenize.c:599）是整個編譯期的大門：它拿 SQL 字串 `zSql`，內部迴圈呼叫 `sqlite3GetToken` 一個一個切 token，餵給 parser。注意一個反直覺的地方：**tokenizer 和 parser 的驅動迴圈都在 `tokenize.c`**，`parse.c`（parser）是被它 call 的，不是反過來。這是 lemon 生成 parser 的慣例——parser 是被「push」token 進去的。

**Parser：`parse.y` 是文法，不是 C**。這是這章第一個「你會被騙到」的地方。你 `rg` 找 parser 的 C code，會發現 `src/` 底下**沒有 `parse.c`**：

```bash
$ ls src/parse.c
ls: cannot access 'src/parse.c': No such file or directory
```

`parse.c` 是 **build 時生成的**——由 SQLite 自帶的 lemon parser generator（`tool/lemon.c`）吃 `src/parse.y` 這個 LALR(1) 文法檔生出來。你 build 一次就會看到它冒出來（在 build 目錄，本課實測 6145 行）。`parse.y` 才是「真相之源」，長這樣：

```
163  cmd ::= BEGIN transtype(Y) trans_opt.  {sqlite3BeginTransaction(pParse, Y);}
172  cmd ::= COMMIT|END(X) trans_opt.   {sqlite3EndTransaction(pParse,@X);}
189  cmd ::= create_table create_table_args.
```
（`src/parse.y:163,172,189`，v3.47.2）

每條文法規則後面的 `{...}` 是 **reduce 時執行的 C 動作**，直接呼叫 `build.c`／`select.c` 裡的 codegen function。這就是 parser 和 codegen 的接縫：**parser 認出文法結構，當場叫 codegen 幹活**。`reading_code` Ch 22（讀懂巨集與 metaprogramming）講的「code 是生成出來的，去讀生成器的輸入」，這裡就是活例子——讀 parser 別讀 `parse.c`，讀 `parse.y`。

**Code generator 入口**——把 parse tree 變成 VDBE bytecode。`build.c`、`select.c`、`insert.c`、`delete.c`、`where.c`、`expr.c` 分工：一個 `SELECT` 走 `sqlite3Select()`（select.c），WHERE 子句的 index 選擇走 `where.c`。我們先記一個關鍵錨點——codegen 產出 bytecode 時，統一透過這個 API：

```bash
$ rg -n "^int sqlite3VdbeAddOp2\(|^int sqlite3VdbeAddOp3\(" src/vdbeaux.c
269:int sqlite3VdbeAddOp2(Vdbe *p, int op, int p1, int p2){
272:int sqlite3VdbeAddOp3(Vdbe *p, int op, int p1, int p2, int p3){
```

`sqlite3VdbeAddOp*`（vdbeaux.c）是「往正在編譯的程式裡 append 一條 bytecode」的動作。全 codebase `rg 'sqlite3VdbeAddOp'` 會有上千個命中——每一個都是 codegen 在「寫一行 bytecode」。這是你之後追「這條 SQL 產生了哪些 opcode」的線頭。

**VDBE 執行入口**——本 Part 的主角：

```bash
$ rg -n "^int sqlite3VdbeExec\(" src/vdbe.c
813:int sqlite3VdbeExec(
```

`sqlite3VdbeExec`（vdbe.c:813）就是那個 9 千行檔案裡的巨型 `switch`，一條一條直譯 bytecode。Ch 9 整章讀它。

**B-tree、pager、VFS 入口**——執行期往磁碟走的三層：

```bash
$ rg -n "int sqlite3BtreeCursor\(|int sqlite3BtreeTableMoveto\(" src/btree.c
4723:int sqlite3BtreeCursor(
5727:int sqlite3BtreeTableMoveto(
$ rg -n "^int sqlite3PagerGet\(" src/pager.c
5707:int sqlite3PagerGet(
$ rg -n "int sqlite3OsRead\(" src/os.c
88:int sqlite3OsRead(sqlite3_file *id, void *pBuf, int amt, i64 offset){
```

`sqlite3OsRead`（os.c:88）是「呼叫作業系統讀一段位元組」的抽象入口，它內部只有一行——透過函式指標分派到真正的 VFS 實作（`unixRead` / `winRead`）。Ch 10 深挖 btree/pager，Ch 12 講 VFS 這個可插拔後端的 pattern。

**兩階段 API：偵察一個函式庫的正確入口。** 上面我們用 `rg` 找到七層的內部入口，但你怎麼知道從哪開始追？答案是**先找對外的公開 API**——它是使用者（也是你）進入這個引擎的門。SQLite 的執行分兩階段，兩個 API：

```bash
$ rg -n "^int sqlite3_prepare_v2\(|^int sqlite3_step\(" src/prepare.c src/vdbeapi.c
src/prepare.c:941:int sqlite3_prepare_v2(
src/vdbeapi.c:896:int sqlite3_step(
```

`sqlite3_prepare_v2`（prepare.c:941）把 SQL **文字編譯成 bytecode**（跑上半：tokenize→parse→codegen），產出一個 `sqlite3_stmt`；`sqlite3_step`（vdbeapi.c:896）**跑那串 bytecode**（跑下半：VDBE→btree→pager→VFS）。看 `prepare_v2` 的本體，它幾乎沒做事，只是轉呼叫：

```c
int sqlite3_prepare_v2(sqlite3 *db, const char *zSql, ...){
  int rc;
  rc = sqlite3LockAndPrepare(db,zSql,nBytes,SQLITE_PREPARE_SAVESQL,0, ppStmt,pzTail);
  ...
}
```
（`src/prepare.c:941-955`，v3.47.2）

**偵察慣例**：公開 API 常常是薄封裝（加鎖、設 flag、轉呼叫內部的 `sqlite3LockAndPrepare`）。追一個函式庫，從公開 API 進去、順著它轉呼叫的第一個內部 function 往下，就找到真正幹活的地方。這和「找 `main`」是兩種不同的偵察起手式——`reading_code` Ch 6 的重點。這也是為什麼「編譯/執行兩階段」是理解 SQLite 的第一個結構性事實：`prepare` 一次、`step` 多次，正是 prepared statement（編一次跑多次）為什麼快的根源。

到這裡，偵察的核心產物——**一張「每層在哪個檔、入口 function 叫什麼」的地圖**——就建好了。整理成表：

| 層 | 檔案 | 入口 function | 幹嘛 |
|---|---|---|---|
| Tokenizer | `src/tokenize.c` | `sqlite3RunParser` / `sqlite3GetToken` | 文字 → token |
| Parser | `src/parse.y`（生成 `parse.c`） | lemon 生成的 `sqlite3Parser` | token → parse tree |
| Code gen | `src/build.c` `select.c` `where.c` `expr.c` | `sqlite3Select` 等，靠 `sqlite3VdbeAddOp*` | parse tree → VDBE bytecode |
| VDBE | `src/vdbe.c` | `sqlite3VdbeExec` | 直譯 bytecode |
| B-tree | `src/btree.c` | `sqlite3BtreeCursor` / `...TableMoveto` | 鍵值樹的查找/走訪 |
| Pager | `src/pager.c` `pcache.c` | `sqlite3PagerGet` | 頁快取 + 交易 |
| VFS | `src/os_unix.c` `os_win.c` | `sqlite3OsRead`（→ `unixRead`） | 真正碰磁碟 |

## 偵察第三步：60 分鐘怎麼分配

`reading_code` Ch 5 的偵察不是「隨便逛」，是有時間預算的攻堅。把我們做過的動作排成一份可複製的 60 分鐘 SOP（你之後對 nginx、git、CPython 都照這個節奏）：

```
   0–5 分   量家底     ls | wc、cloc/wc -l 核心檔，建立「多大、怎麼分」的體感
   5–15 分  讀自述     找官方 arch 文件 + 每個檔開頭的檔案級註解（SQLite 註解極好）
  15–30 分  找入口     rg 公開 API + 各層入口 function，畫「哪層在哪檔」的表
  30–50 分  追一條線   挑一條最簡單的路徑（一條 SELECT），順著入口下鑽一層層
  50–60 分  外化       把地圖畫成圖、記下「我還不懂的三個問題」（留給機制章）
```

**關鍵是「追一條線」那 20 分鐘**：不要試圖讀懂每一層的全部，挑一條具體輸入（`SELECT x FROM t WHERE id=?`）順著追。你會自然穿過 `prepare`→`RunParser`→codegen→`VdbeExec`→`btree`→`pager`→`os`，每層停留一兩分鐘確認「入口對不對、往下呼叫誰」即可。這條線就是 Ch 9–10 要深挖的骨架、也是練習 B 的任務。**偵察的產物是地圖 + 一條追過的線 + 一串待答問題，不是「讀完」。**

一個實用的偵察技巧：讀每個檔**開頭的檔案級註解**。SQLite 的檔頭註解品質高到可以當文件——`vdbe.c` 開頭有整段講 VDBE 設計哲學、`btree.c` 開頭講 B-tree 頁佈局、`pager.c` 開頭講 pager 狀態機。偵察時花 30 秒讀檔頭，勝過瞎讀 300 行 code。這是「先讀作者留的路標」，`reading_code` 反覆強調的省時法。

## 底層機制：amalgamation（`sqlite3.c` 單檔）vs `src/` 分檔

這是讀 SQLite 一定會撞到的問題：網路上、apt 裝的、你專案裡 vendored 的 SQLite，通常是**一個叫 `sqlite3.c` 的巨檔**，不是這 127 個分檔。到底哪個才是「真的」？

答案：**兩個都是同一份 code，`sqlite3.c` 是把 `src/*.c` 串接壓平成一個檔的產物**。SQLite 把這個過程叫 **amalgamation（融合）**。生成它的工具是 `tool/mksqlite3c.tcl`，開頭的註解說得很清楚：

```
# To build a single huge source file holding all of SQLite ... first do
#      make target_source
# The make target above moves all of the source code files into
# a subdirectory named "tsrc". ... There are a few generated C code files
# that are also added ... For example, the "parse.c" and "parse.h" files
# ... are derived from "parse.y" using lemon.
```
（`tool/mksqlite3c.tcl` 開頭註解，v3.47.2）

也就是：build 系統先把 `src/*.c` 加上幾個**生成檔**（`parse.c`、`opcodes.h`、`keywordhash.h`…）集中到 `tsrc/`，再由 tcl 腳本串成一個 `sqlite3.c`。本課實測 build 出來的 amalgamation：

```bash
$ wc -l bld/sqlite3.c
262689 bld/sqlite3.c        ← 26 萬行、9.2 MB 的單檔
```

為什麼要這樣搞？三個理由，官方文件講過：

1. **編譯器最佳化**：整個引擎在一個 translation unit 裡，編譯器能跨「檔案」inline、常數傳播，實測比分檔編譯快 5%~10%。這是 amalgamation 的**主要動機**。
2. **部署零摩擦**：使用者只要把 `sqlite3.c` + `sqlite3.h` 兩個檔丟進專案，`#include` 就完事。不用管 127 個檔的相依和 build 順序。
3. **可稽核**：整個引擎一個檔，`sha3sum` 一下就能驗完整性。

但這對**讀碼**是雙面刃：

- **讀原始碼要讀 `src/`**（分檔），檔名有意義、函式歸屬清楚、行號穩定。本課全程引用 `src/`。
- **讀 vendored 進別人專案的 SQLite 要讀 `sqlite3.c`**（單檔），這時 grep 一個 function 名可能命中幾百個 `static` helper，且 26 萬行沒有檔案邊界幫你定位——這正是 `reading_code` Ch 11「收斂」技巧的硬考驗。

**踩雷預告**：如果你在別人專案裡 `rg 'sqlite3VdbeExec'` 只找到 `sqlite3.c`，別以為 source 就長這樣、只有一個檔。去 sqlite.org 抓對應版本的 `src/` tarball，或 clone git repo，你才看得到真正的檔案分層。教材永遠引用 `src/` 就是這個原因。

## 對比與取捨

| 讀 SQLite 的方式 | 適合 | 代價 |
|---|---|---|
| 讀 `src/`（分檔） | 讀懂架構、學設計、追路徑 | 要 clone git repo 或抓 src tarball |
| 讀 `sqlite3.c`（amalgamation） | 貴專案 vendored 的就這個、debug 現場 | 26 萬行單檔，無檔案邊界輔助定位 |
| 讀 `parse.y`（文法） | 理解 SQL 語法怎麼被認 | 不是 C，要懂一點 lemon 語法 |
| 讀 `parse.c`（生成的） | 幾乎永遠不該讀 | 機器生成、難讀、且你改了會被覆蓋 |

## 踩雷集錦

1. **以為 parser 的 code 在 `parse.c`，去讀它**：`parse.c` 是 lemon 從 `parse.y` 生成的機器碼，難讀且改了會被覆蓋。**要讀 parser 邏輯，讀 `src/parse.y` 的文法規則和它的 `{}` 動作**。`src/` 底下根本沒有 `parse.c`（`ls src/parse.c` 直接 not found），它只在 build 目錄出現——這本身就是「這是生成檔」的信號。
2. **把 amalgamation 當成 SQLite 的「原始」樣貌**：`sqlite3.c` 是編譯部署用的產物，不是給人讀的原始碼。看到 26 萬行的單檔別以為 SQLite 真的寫成一坨——去 `src/` 看，它其實是乾淨分層的 127 個檔。
3. **偵察時想從 `main()` 開始讀**：SQLite 是**函式庫**，沒有你以為的那種 `main()`（`sqlite3.exe` 那個 shell 的 `main` 在 `shell.c`，但那是工具不是引擎）。引擎的「入口」是 API：`sqlite3_prepare_v2`（編譯 SQL）和 `sqlite3_step`（跑一步 VDBE）。偵察一個函式庫，入口是**公開 API**，不是 `main`——`reading_code` Ch 6 的重點。
4. **看到 21 萬行就想全讀**：`btree.c` 一個檔 11K 行，你不可能也不需要逐行讀。偵察的產物是「地圖 + 每層入口」，不是「讀完」。要深挖時，順著入口 function 下鑽該條路徑即可。
5. **在 amalgamation 裡追行號對照教材**：教材引用 `src/vdbe.c:813`，但 amalgamation 把所有檔串起來後 `sqlite3VdbeExec` 的行號完全不同（在 8 萬多行處）。**行號只在同一種形態（`src/` 分檔）內有意義**——這回扣 Ch 0「釘死版本」的紀律：不只版本要對齊，連「讀哪種形態」都要對齊。

## 進階：再往深一層

- **`sqlite.h.in` 為什麼是 `.in`**：公開 API 定義在 `src/sqlite.h.in`（模板），build 時經過處理（填入版本號等）生成真正的 `sqlite3.h`。`rg` 一個 API 如 `sqlite3_prepare_v2` 的**宣告**要去 `sqlite.h.in`。這是又一個「生成檔 vs 模板」的例子。
- **用 `bytecode` 虛擬表偷看 VDBE**：SQLite 有個 `bytecode(...)` 的 table-valued function（build 時 `-DSQLITE_ENABLE_BYTECODE_VTAB`，本課的 build 有開），能用 SQL 查一條 prepared statement 的 bytecode。這是把「讀 VDBE」變成「查 SQL」的神器，Ch 9 會用到。
- **官方架構文件對照法**：把 [arch.html](https://www.sqlite.org/arch.html) 那張圖印出來，逐格 `rg` 找對應檔案。任何官方文件都可能過時或簡化，`reading_code` 的鐵律是「文件是假設、code 是真相」——但 SQLite 的文件品質高到你可以把它當很可靠的假設，驗證起來省事。

## 本章重點整理

- SQLite 是**教科書級分層**：編譯期（tokenizer → parser → codegen）產出 VDBE bytecode，執行期（VDBE → B-tree → pager → VFS）跑 bytecode 落磁碟。兩半用 **bytecode 這個 IR 解耦**。
- 偵察 SOP 的產物是**地圖 + 每層入口 function**，不是「讀完」。我們用 `wc`/`rg` 驗證了七層各自的檔案與入口。
- **parser 讀 `parse.y`（文法）不讀 `parse.c`（生成檔）**；`src/` 底下根本沒有 `parse.c`。
- **amalgamation（`sqlite3.c`）是 `src/*.c` + 生成檔串平壓成的單檔**，為了編譯最佳化和零摩擦部署。讀原始碼讀 `src/`，讀 vendored 版才碰 `sqlite3.c`。
- SQLite 是函式庫，偵察入口是**公開 API**（`sqlite3_prepare_v2` / `sqlite3_step`），不是 `main`。

## 自我檢核

- [ ] 我能默畫出 SQLite 的七層分層圖，並說出每層在哪個檔、入口 function 叫什麼
- [ ] 我能解釋為什麼 parser 的 code 要讀 `parse.y` 而不是 `parse.c`
- [ ] 我能解釋 amalgamation 是什麼、為什麼存在、讀碼時該讀 `src/` 還是 `sqlite3.c`
- [ ] 我知道 SQLite 沒有引擎層級的 `main()`，偵察一個函式庫要從公開 API 入手
- [ ] 我在自己 clone 的 3.47.2 上跑過 `wc -l src/vdbe.c`，數字和教材對得上

## 延伸閱讀

- **[SQLite Architecture](https://www.sqlite.org/arch.html)**（官方架構文件）
  - **讀哪裡**：整頁那張分層圖 + 每格的一段說明。這是本章地圖的權威來源，把它當「可靠的假設」，然後照本章方法逐格 `rg` 驗證。
  - **前提**：無，這是高層導覽。
- **[The SQLite Amalgamation](https://www.sqlite.org/amalgamation.html)**（官方）
  - **讀哪裡**：整頁。講清楚 amalgamation 為什麼存在、編譯最佳化的量化收益、和 `src/` 的關係。讀完你就不會再把 26 萬行單檔當成 SQLite 的原貌。
  - **前提**：讀完本章「底層機制」節。
- **`reading_code` Ch 5「第一次接觸：60 分鐘偵察」與 Ch 11「從 50 萬行收斂到你要改的 200 行」**
  - **讀哪裡**：Ch 5 的偵察 checklist、Ch 11 的收斂技巧。本章就是這兩章在 SQLite 上的實戰；回頭對照你會發現「量家底 → 找官方自述 → 逐層找入口」是可複製的 SOP，不是 SQLite 專屬。
  - **前提**：無。

偵察地圖建好了。下一章我們鑽進最核心的那一層——VDBE：SQL 不是被直接執行的，是被編譯成一種 bytecode，再由 `sqlite3VdbeExec` 這個巨型 switch 一條一條直譯。這是本 Part 的靈魂，也是「三個 VM 橫向對照」（Lua / SQLite / CPython）的第二站。

→ [Ch 9 VDBE：bytecode 虛擬機（對照 Lua VM）](./09-sqlite-vdbe.md)
