# Ch 9 — VDBE：bytecode 虛擬機（對照 Lua VM）

> **目標**：搞懂 SQLite 最反直覺、也最漂亮的設計——**SQL 不是被直接執行的，是被編譯成一串 VDBE bytecode，再由 `sqlite3VdbeExec` 這個巨型 `switch` 一條一條直譯**。讀通它的 dispatch loop 和三個代表性 opcode（`OpenRead` / `Column` / `ResultRow`），並用真跑的 `EXPLAIN` 看見這串 bytecode。把它和 Lua 的 register VM（Part 1 Ch 4）擺在一起——這是「同一個 pattern 換皮」的高光時刻。

> **目標codebase**：SQLite `version-3.47.2`（commit `262de1b`）

## 為什麼需要這個？

大多數人以為資料庫執行 SQL 是這樣：parse 成一棵樹，然後遞迴走訪這棵樹求值（tree-walking interpreter）。**SQLite 不是這樣**。它多了一層看似多餘的步驟：把 parse tree 先編譯成一種**類組合語言的 bytecode**，叫 VDBE（Virtual DataBase Engine）程式，然後才由一個 VM 去跑那串 bytecode。

為什麼要多這一層？這正是本章要回答的、也是一個可遷移的核心洞見：

- **解耦編譯與執行**。「怎麼查（query plan）」在 codegen 期一次決定、凝結成 bytecode；執行期只管「照 bytecode 跑」，不用再碰 SQL 語意。這讓 query planner（`where.c`）能獨立演化，執行器（`vdbe.c`）保持穩定。
- **可攜與可檢視**。bytecode 是資料，你能 `EXPLAIN` 把它 dump 出來看、能存起來（prepared statement）、能跨呼叫重用。tree-walking 做不到這麼乾淨。
- **這是編譯器的思路搬進資料庫**：SQL → parse tree → **bytecode（IR）** → VM 執行。和你的 compiler 課群、和 Lua、和 CPython 的 `ceval.c` 是**同一個 pattern**：「編譯到 bytecode + VM 直譯」。認出這個 pattern，你讀第三個 bytecode VM（CPython，Ch 23）時就是「哦，又是這套」。

## 先建立直覺

先看一條最簡單的 SQL 被編譯成什麼。這是本課**真跑**的輸出——用自己 build 的 3.47.2 shell（build 過程見 Ch 8），對一個 `id` 是 primary key 的表下 `EXPLAIN`：

```
$ sqlite3 :memory:
sqlite> CREATE TABLE t(id INTEGER PRIMARY KEY, x TEXT);
sqlite> INSERT INTO t VALUES(1,'a'),(2,'b'),(3,'c');
sqlite> .explain on
sqlite> EXPLAIN SELECT x FROM t WHERE id=2;
addr  opcode         p1    p2    p3    p4    p5  comment
----  -------------  ----  ----  ----  ----  --  -------------
0     Init           0     7     0           0   Start at 7
1     OpenRead       0     2     0     2     0   root=2 iDb=0; t
2     Integer        2     1     0           0   r[1]=2
3     SeekRowid      0     6     1           0   intkey=r[1]
4     Column         0     1     2           0   r[2]= cursor 0 column 1
5     ResultRow      2     1     0           0   output=r[2]
6     Halt           0     0     0           0
7     Transaction    0     0     1     0     1   usesStmtJournal=0
8     Goto           0     1     0           0
```

**這就是那條 SELECT 的「機器碼」。** 讀一遍你就懂它在幹嘛：

```
  0  Init        跳到 7 開始（先做 setup）
  7  Transaction 開一個讀交易
  8  Goto        跳回 1，開始幹活
  1  OpenRead    打開表 t 的 B-tree（root page 2），用 cursor 0 讀
  2  Integer     把常數 2 放進暫存器 r[1]（這是 WHERE id=2 的 2）
  3  SeekRowid   用 cursor 0 在 B-tree 裡找 rowid == r[1]（==2）的那列；找不到就跳 6
  4  Column      從 cursor 0 當前那列，取第 1 欄（x）放進 r[2]
  5  ResultRow   把 r[2] 當作一列結果吐給呼叫者
  6  Halt        結束
```

看出幾件事：

- **VDBE 是暫存器機器（register machine），不是堆疊機器**。`r[1]`、`r[2]` 是暫存器（SQLite 叫 "memory cell"），opcode 直接指名操作哪個暫存器。這和 **Lua VM 一樣是 register-based**（對照 Part 1 Ch 4），也和 Python 的**堆疊機器**（Ch 23）成對比。
- **cursor 是第一級概念**。`OpenRead` 開一個 cursor（編號 0），之後 `SeekRowid`/`Column`/`Next` 都對這個 cursor 操作。cursor 就是「指向 B-tree 某一列的指標」——這是 VDBE 和 B-tree 層的接縫。
- **控制流靠跳轉**。`Init`→`Transaction`→`Goto`→回主體，`SeekRowid` 找不到會跳過。這就是為什麼 opcode 的 `p2` 常常是「跳到哪」——它是 jump target。

VDBE 程式的心智模型：

```
     一串 VdbeOp（bytecode）           一組暫存器 aMem[]（Mem 陣列）
   ┌───────────────────────┐        ┌──────────────────────┐
   │ [0] Init      p2=7     │        │ r[1] = 2  (int)      │
   │ [1] OpenRead  p1=0 ... │        │ r[2] = "b" (text)    │
   │ [2] Integer   p2=1     │        │ ...                  │
   │ [3] SeekRowid p1=0 ... │        └──────────────────────┘
   │ ...                    │        ┌──────────────────────┐
   └───────────────────────┘        │ apCsr[0] → B-tree cursor │
        pc（程式計數器）指著現在跑哪條   └──────────────────────┘
```

`sqlite3VdbeExec` 就是「拿著 pc，取出當前 opcode，`switch` 到對應的 case 執行，然後 pc++（或跳轉）」——一個經典的 fetch-decode-execute 迴圈。

## 核心：dispatch loop 長什麼樣

打開 `src/vdbe.c`，`sqlite3VdbeExec` 從第 813 行開始。開頭先把常用欄位拉成 local 變數（讀 VM 的第一個慣例：**把熱路徑會反覆存取的欄位快取到暫存器變數**）：

```c
int sqlite3VdbeExec(
  Vdbe *p                    /* The VDBE */
){
  Op *aOp = p->aOp;          /* Copy of p->aOp */
  Op *pOp = aOp;             /* Current operation */
  ...
  int rc = SQLITE_OK;        /* Value to return */
  sqlite3 *db = p->db;       /* The database */
  ...
  Mem *aMem = p->aMem;       /* Copy of p->aMem */   ← 暫存器陣列
  Mem *pIn1 = 0;             /* 1st input operand */
  Mem *pIn2 = 0;             /* 2nd input operand */
  Mem *pIn3 = 0;             /* 3rd input operand */
  Mem *pOut = 0;             /* Output operand */
```
（`src/vdbe.c:813-838`，v3.47.2）

`aOp` 是 bytecode 陣列，`aMem` 是暫存器陣列。`pIn1/pIn2/pIn3/pOut` 是「當前指令的輸入/輸出運算元」——很多 opcode 開頭前這些會被自動指到 `aMem[pOp->p1]` 等（靠開頭一段 flag-decode 邏輯，這裡先不展開）。

dispatch loop 本體，簡單到你可能不信：

```c
  for(pOp=&aOp[p->pc]; 1; pOp++){
    /* Errors are detected by individual opcodes, with an immediate
    ** jumps to abort_due_to_error. */
    ...
    switch( pOp->opcode ){
```
（`src/vdbe.c:898,981`，v3.47.2）

就是 **`for(pOp=&aOp[p->pc]; 1; pOp++)` + 一個 `switch(pOp->opcode)`**。`pOp++` 讓程式計數器自動前進到下一條；要跳轉的 opcode 則自己改 `pOp` 再 `continue`/`break`。整個 `switch` 有一百多個 `case`，一路到第 9 千行——這就是 SQLite 那個「傳說中的巨型 switch」。

**跳轉怎麼實作**？看 `OP_Goto`（vdbe.c:1030），它跳到 `jump_to_p2`：

```c
jump_to_p2:
  ...
  pOp = &aOp[pOp->p2 - 1];
```
（`src/vdbe.c:1153,1156`，v3.47.2）

注意那個 **`- 1`**：因為迴圈尾端 `pOp++` 會再加一，所以這裡先減一，加完剛好落在 `aOp[p2]`。這是讀 dispatch loop 常見的「off-by-one 陷阱」——你以為 bug，其實是配合 `for` 的 `pOp++`。`reading_code` Ch 24（讀懂狀態機與事件驅動）講的「迴圈的 increment 和跳轉互相補償」，這就是活例子。

> **失敗是教材**：第一次讀這個 loop，你會盯著 `switch` 找「pc 在哪裡 ++」，找不到——因為它不叫 `pc`，是 `pOp++`（指標本身就是程式計數器）。`p->pc` 只在進出這個 function 時同步一下。把「pc = 指向 bytecode 的指標」這個心智模型建起來，整個 loop 就通了。

## 讀三個代表性 opcode

隨便挑一百多個 opcode 讀不完，我們只讀剛才那條 SELECT 用到的三個關鍵動作。

### OP_OpenRead：打開一個 B-tree cursor

```c
case OP_OpenRead:            /* ncycle */
case OP_OpenWrite:
  ...
  nField = 0;
  pKeyInfo = 0;
  p2 = (u32)pOp->p2;          /* p2 = 表的 root page 號 */
  iDb = pOp->p3;              /* p3 = 哪個 database（attach 的） */
  ...
  pDb = &db->aDb[iDb];
  pX = pDb->pBt;              /* 這個 db 的 B-tree handle */
  assert( pX!=0 );
```
（`src/vdbe.c:4319-4340`，v3.47.2）

`OpenRead` 把「表 t」對應到它的 B-tree root page（`p2`，我們 EXPLAIN 裡是 `2`），在 cursor slot `p1`（`0`）上開一個 B-tree cursor（後面會呼叫 `sqlite3BtreeCursor`，Ch 10 深挖）。**這是 VDBE 層跨進 B-tree 層的接縫**：從此之後 cursor 0 就代表「指向表 t 某列的指標」。

注意 `OP_OpenRead` 和 `OP_OpenWrite` **共用同一段 case**（fall-through 沒有 `break`）——讀多了你會發現 SQLite 大量用「兩三個相近 opcode 落到同一塊 code，用 `pOp->opcode==...` 或 flag 區分細節」。這是**用 opcode 表達意圖、用共用 code 減少重複**的慣例。

### OP_Column：從當前列取出一欄

```c
case OP_Column: {            /* ncycle */
  u32 p2;            /* column number to retrieve */
  VdbeCursor *pC;    /* The VDBE cursor */
  BtCursor *pCrsr;   /* The B-Tree cursor corresponding to pC */
  u32 *aOffset;      /* aOffset[i] is offset to start of data for i-th column */
  ...
  Mem *pDest;        /* Where to write the extracted value */
  ...
  pC = p->apCsr[pOp->p1];    /* p1 = 哪個 cursor */
  p2 = (u32)pOp->p2;         /* p2 = 第幾欄 */
```
（`src/vdbe.c:2930-2949`，v3.47.2）

`OP_Column` 是整個 VDBE 最熱、也最複雜的 opcode 之一（在 EXPLAIN 裡 `Column p1=0 p2=1 p3=2` 意思是「從 cursor 0 的當前列，取第 1 欄，放進 r[2]」）。它要做的事：找到 cursor 當前指向的那筆 record，解析 record 的 header（SQLite 的 record 是一種變長編碼），算出第 `p2` 欄的位元組 offset，把值解出來放進 `aMem[p3]`。

**這裡藏著一個效能設計**：`aOffset[]` 快取「每一欄在 record 裡的 offset」，避免同一列取多欄時重複解析 header。這種「把上一次算過的中間結果 cache 在 cursor 上」的手法，讀 VM 熱路徑時到處都是——**熱 opcode 的複雜度往往來自快取/避免重算，不是核心邏輯本身**。第一次讀 `OP_Column` 會被那幾百行嚇到，其實核心動作就一句「解析 record、取第 p2 欄」，其餘都是各種 fast path 和快取。

### OP_ResultRow：把一列吐給呼叫者

```c
case OP_ResultRow: {
  assert( p->nResColumn==pOp->p2 );
  ...
  p->pResultRow = &aMem[pOp->p1];    /* 結果從 r[p1] 開始，共 p2 欄 */
  ...
  p->pc = (int)(pOp - aOp) + 1;      /* 記住下次從哪繼續 */
  rc = SQLITE_ROW;
```
（`src/vdbe.c:1712,1718,1739-1740`，v3.47.2）

`OP_ResultRow` 幹一件關鍵的事：**它讓 `sqlite3VdbeExec` 回傳 `SQLITE_ROW` 給呼叫者**（也就是 `sqlite3_step`），把 `p->pResultRow` 指向這一列的暫存器。這解釋了 SQLite 對外的 API 為什麼是「一步一列」：

```
你的程式
  ├─ sqlite3_step(stmt)  ─┐
  │                       │ 進入 sqlite3VdbeExec，跑到 OP_ResultRow
  │                       │ → 回傳 SQLITE_ROW，函式暫停在這裡
  │  sqlite3_column_*() ← 讀 p->pResultRow 那幾個暫存器
  │                       │
  ├─ sqlite3_step(stmt)  ─┘ 再次進入，從 p->pc 繼續（下一列）
  ...
```

**`OP_ResultRow` 是 coroutine 式的暫停點**——VM 跑到這裡就 return，下次 `step` 從 `p->pc` 接著跑。這是「用 bytecode VM 實作 iterator」的漂亮手法：不需要真的協程，靠「記住 pc、return、下次從 pc 繼續」就模擬出「產生一列就暫停」。

## 底層機制：opcode 的名字從哪來，`bytecode()` 怎麼偷看

你 `rg 'case OP_Column'` 找得到，但 `OP_Column` 這個常數定義在哪？答案：**`opcodes.h`，這是 build 時生成的**。

```bash
$ head -12 bld/opcodes.h
/* Automatically generated.  Do not edit */
/* See the tool/mkopcodeh.tcl script for details */
#define OP_Savepoint       0
#define OP_AutoCommit      1
#define OP_Transaction     2
...
#define OP_Init            8 /* jump0, synopsis: Start at P2 */
#define OP_Goto            9 /* jump */
```
（build 生成的 `opcodes.h` 開頭，v3.47.2）

`tool/mkopcodeh.tcl` 掃 `vdbe.c` 裡所有 `case OP_Xxx:`，自動編號、生成 `opcodes.h`（`#define`）和 `opcodes.c`（給 EXPLAIN 印名字用的字串表）。**opcode 的「真相之源」是 `vdbe.c` 的 `case` 標籤本身**——你想加一個 opcode，就在 `vdbe.c` 加個 `case`，跑 build，編號和名字自動生成。這又是一個「讀 code 要讀生成器的輸入，不是生成的輸出」的例子（回扣 Ch 8 的 `parse.c`）。

想在**不 build shell** 的情況下看某條 SQL 的 bytecode？除了 `.explain on` + `EXPLAIN`，還有 `bytecode()` 虛擬表（build 要開 `-DSQLITE_ENABLE_BYTECODE_VTAB`，本課的 build 有開）：

```sql
SELECT addr, opcode, p1, p2, p3 FROM bytecode('SELECT x FROM t WHERE id=2');
```

它把 prepared statement 的 bytecode 當成一張可查的表——把「讀 VDBE」變成「寫 SQL」。這是偵察 VDBE 行為的利器。

## 對比與取捨：三個 VM 擺一起

這是本 Part 和 Part 1、Part 5 呼應的重點。三個都是「編譯到 bytecode + VM 直譯」，但選擇不同：

| | **Lua VM**（Part 1 Ch 4） | **SQLite VDBE**（本章） | **CPython**（Part 5 Ch 23） |
|---|---|---|---|
| 檔案 / 入口 | `lvm.c` `luaV_execute` | `vdbe.c` `sqlite3VdbeExec` | `ceval.c` `_PyEval_EvalFrameDefault` |
| 暫存器 vs 堆疊 | **register**（TValue 陣列） | **register**（Mem 陣列） | **堆疊**（value stack） |
| dispatch | computed goto（有的話）/ switch | 純 `switch(pOp->opcode)` + `pOp++` | computed goto（`DISPATCH()`） |
| 指令編碼 | 32-bit 打包（OpCode+ABC） | `VdbeOp` struct（opcode+p1..p5+p4） | 變長 bytecode（`opcode arg`） |
| bytecode 從哪來 | `lcode.c` 編譯 Lua 源碼 | `build.c`/`where.c` 編譯 SQL | `compile.c` 編譯 Python |
| 領域特化 | 通用語言 | **cursor / record 是一等公民** | 通用語言（物件協定） |

**可遷移的洞見**：認出「這是 bytecode VM 的 dispatch loop」之後，你在任何一個 VM 裡都會問同一組問題——(1) 指令陣列在哪、pc 怎麼前進？(2) register 還是 stack？(3) dispatch 是 switch 還是 computed goto？(4) 跳轉怎麼實作（那個 off-by-one）？(5) 熱 opcode 的複雜度是核心邏輯還是快取？這五個問題就是你的「VM 讀碼 checklist」，三個 VM 通用。

## 踩雷集錦

1. **以為 SQLite 直接對 parse tree 求值（tree-walking）**：錯。SQLite 多了一層 bytecode。`EXPLAIN` 之所以能 dump 出一串 opcode，正是因為執行的是 bytecode 不是 tree。認清這層，你才懂 prepared statement（編一次、跑多次）為什麼快。
2. **在 dispatch loop 裡找 `pc++`**：找不到，因為程式計數器是 `pOp`（指標），前進靠 `for(...; pOp++)`。跳轉的 opcode 用 `pOp = &aOp[p2 - 1]`，那個 `-1` 是配合迴圈尾的 `pOp++`，不是 bug。
3. **被 `OP_Column` 的幾百行嚇到以為它很難**：它的核心動作就是「解析 record header、取第 p2 欄」，其餘全是 fast path 和 offset 快取（`aOffset[]`）。熱 opcode 的體積來自最佳化，不是邏輯複雜。讀熱路徑要先抓「核心那一句」，把最佳化分支當雜訊略過。
4. **以為 `OP_ResultRow` 會把所有列一次算完**：不。它跑到就 `return SQLITE_ROW`，函式暫停，下次 `sqlite3_step` 從 `p->pc` 繼續。SQLite 的「一步一列」正是靠這個「記 pc、return、續跑」的 coroutine 式手法。理解它，你才懂為什麼查大表不會一次吃爆記憶體。
5. **想去 `opcodes.h` 改 opcode 定義**：那是生成檔（`tool/mkopcodeh.tcl` 從 `vdbe.c` 的 `case` 標籤生的）。opcode 的真相之源是 `vdbe.c` 的 `case OP_Xxx:`，改那裡、重 build，`opcodes.h` 自動更新。

## 進階：再往深一層

- **`OP_SeekRowid` 怎麼變成一次 B-tree 二分查找**：我們 EXPLAIN 裡的 `SeekRowid`（`src/vdbe.c:5426` 的 `case OP_SeekRowid`）內部呼叫 `sqlite3BtreeTableMoveto`——那是 B-tree 的核心查找。Ch 10 會從這裡接下去，一路追到 pager 讀 page。這條線（`OP_Column`/`OP_SeekRowid` → `sqlite3Btree*` → `sqlite3PagerGet`）就是練習 B 要你追的路徑。
- **`p4` 的多型**：`VdbeOp` 的 `p4` 是個 union（`p4.i` / `p4.z` / `p4.pKeyInfo`...，見 `src/vdbe.h:54` 的 `struct VdbeOp`），配一個 `p4type` tag 決定當前是哪種。這是 C 裡「tagged union 實作多型參數」的教科書範例，`reading_code` Ch 23（讀懂 indirection）的實料。
- **為什麼 SQLite 選純 `switch` 而非 computed goto**：可攜性。computed goto 是 GCC/Clang 擴充，SQLite 要能在任何 C 編譯器上編（它跑在無數奇怪平台），所以堅持標準 `switch`。這是「可攜性 > 極致效能」的工程取捨——和 Lua/CPython 敢用 computed goto 的定位不同。

## 本章重點整理

- SQLite **不做 tree-walking**：SQL 先被 `build.c`/`where.c` 編譯成 **VDBE bytecode**，再由 `sqlite3VdbeExec`（`vdbe.c:813`）這個巨型 `switch` 一條條直譯。
- dispatch loop = **`for(pOp=&aOp[p->pc]; 1; pOp++)` + `switch(pOp->opcode)`**；程式計數器是指標 `pOp`；跳轉用 `pOp=&aOp[p2-1]`（`-1` 補償迴圈尾的 `pOp++`）。
- VDBE 是 **register machine**（`aMem[]` 暫存器 + cursor），和 Lua 同類、和 CPython 堆疊機成對比。**cursor 是它的領域特化**——`OpenRead`/`Column`/`ResultRow` 都繞著 cursor 轉。
- `OP_ResultRow` 用「記 pc、`return SQLITE_ROW`、下次續跑」實作「一步一列」的 iterator——這是 `sqlite3_step` 語意的來源。
- opcode 定義（`opcodes.h`）是 build 生成的；真相之源是 `vdbe.c` 的 `case OP_Xxx:`。用 `EXPLAIN` 或 `bytecode()` 虛擬表可以真跑看見 bytecode。

## 自我檢核

- [ ] 我能對著空氣解釋「為什麼 SQLite 要先編譯成 bytecode 再執行」的三個理由
- [ ] 我能默寫 dispatch loop 的骨架（`for(...pOp++)` + `switch`），並解釋 pc 為什麼是 `pOp`、跳轉為什麼 `-1`
- [ ] 我能讀懂本章那段 EXPLAIN 輸出的每一行 opcode 在幹嘛
- [ ] 我能說出 `OP_ResultRow` 如何實作「一步一列」，以及它和 `sqlite3_step` 的關係
- [ ] 我能把 VDBE 和 Lua VM、CPython 用「register vs stack / dispatch 方式」對照起來
- [ ] 我在自己 build 的 3.47.2 shell 上跑過 `.explain on; EXPLAIN SELECT ...`，看到了真實 bytecode

## 延伸閱讀

- **[The Virtual Database Engine of SQLite](https://www.sqlite.org/vdbe.html) 與 [SQLite Opcodes](https://www.sqlite.org/opcode.html)**（官方）
  - **讀哪裡**：vdbe.html 全頁（VDBE 的設計哲學）；opcode.html 當字典——讀 EXPLAIN 時遇到不認得的 opcode 就查它。這兩份是讀 `vdbe.c` 最好的伴讀。
  - **前提**：讀完本章。
- **`vdbe.c` 開頭的大段檔案註解（`src/vdbe.c` 前 ~100 行）**
  - **讀哪裡**：檔案開頭 SQLite 自己寫的設計說明。SQLite 的檔案級註解品質極高，讀 code 前先讀它自述，事半功倍——這也是 `reading_code` 教的「先讀 code 作者留的路標」。
  - **前提**：無。
- **本課 Part 1 Ch 4「register-based VM 與 dispatch loop」與 Part 5 Ch 23「ceval.c」**
  - **讀哪裡**：Ch 4 讀 Lua 的 `luaV_execute`、Ch 23 讀 CPython 的 eval loop。三章一起讀，你會親眼看到「同一個 bytecode VM pattern」在三個 codebase 裡怎麼換皮。Ch 27 專門做這個三方對照。
  - **前提**：讀完本章。

VDBE 是「怎麼跑 bytecode」，但 bytecode 最終要把資料從磁碟撈上來。下一章鑽進執行期的下半——B-tree 怎麼把「一堆 4KB 的頁」組織成可查找的鍵值樹，pager 怎麼把「頁」交易化並在 crash 後還能恢復。這是 `OP_SeekRowid`/`OP_Column` 底下真正碰資料的那一層。

→ [Ch 10 B-tree 與 pager](./10-sqlite-btree-pager.md)
