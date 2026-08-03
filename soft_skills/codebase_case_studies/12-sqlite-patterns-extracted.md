# Ch 12 — 萃取 pattern：VM 分派 / pager / amalgamation

> **目標**：把 Part 2 讀到的 SQLite 設計 idiom 結晶成**可遷移的 pattern 卡片**——每張卡片講清楚它的 **beacon（怎麼一眼認出）**、**在 SQLite 的哪裡**、**可遷移到哪**。這是 `reading_code` 全課的核心產物：讀完一個 codebase，你帶走的不是「SQLite 的細節」，是「下次在別的專案看到同樣形狀，能一眼 chunk 成一個已知概念」的能力。

> **目標codebase**：SQLite `version-3.47.2`（commit `262de1b`）

## 為什麼需要這個？

`reading_code` 開宗明義：讀碼是速度技能，速度來自 **pattern 庫**。你剛花四章逐行讀 SQLite——但如果讀完就散了，下次遇到另一個資料庫、另一個 VM，你還是從頭推。**萃取章的任務是把「這次讀到的」壓縮成「下次能認出的」。**

方法照本課的訓練協定（Part 0 Ch 2）：**先合上前四章，自己憑記憶寫下「我在 SQLite 認出了哪些 pattern」，再看這章對答案**。你自己說得出的才進長期記憶；看我列的只是補漏。

每張卡片有固定三欄，這是 pattern 卡片的模板（你之後每讀完一個 codebase 都該產一疊）：

- **beacon**：一眼認出這個 pattern 的視覺/結構信號（Felienne Hermans 的「beacon」概念——專家靠這些線索瞬間 chunk）。
- **在哪**：SQLite 裡的真實檔案/function（你能 `rg` 過去驗證）。
- **可遷移**：這個 pattern 在別的專案（尤其本課其他 Part）長什麼樣、你會在哪再遇到。

## Pattern 卡 1：編譯到 bytecode + VM 直譯

**一句話**：不對 AST/parse tree 直接求值，而是先編譯成一種扁平的 bytecode（IR），再用一個 VM 迴圈直譯它。

**beacon**（怎麼一眼認出）：
- 有一個**巨型 `switch`**（幾百個 case），每個 case 是一個 opcode。
- 有一個 **dispatch loop**：`for(;;){ switch(op){...} }`，配一個程式計數器（pc / `pOp`）。
- 有一組 **register 或 stack**（值的暫存區），opcode 對它們操作。
- 有一個工具能把「源碼/查詢」dump 成 opcode 列表（SQLite 的 `EXPLAIN`）。

**在哪**：`src/vdbe.c` 的 `sqlite3VdbeExec`（vdbe.c:813），dispatch loop 是 `for(pOp=&aOp[p->pc]; 1; pOp++)` + `switch(pOp->opcode)`（vdbe.c:898,981）。bytecode 由 `build.c`/`where.c` 透過 `sqlite3VdbeAddOp*`（vdbeaux.c:269）產生。用 `.explain on; EXPLAIN SELECT...` 真跑可見（Ch 9）。

**可遷移**（你會在哪再遇到）：
- **Lua**（Part 1 Ch 4）：`lvm.c` 的 `luaV_execute`，也是 register VM——和 VDBE 是**同一種機器**，只是 Lua 用 32-bit 打包指令、VDBE 用 `VdbeOp` struct。
- **CPython**（Part 5 Ch 23）：`ceval.c` 的 eval loop，是 **stack machine**（對比 VDBE 的 register machine）——Ch 27 專門三方對照。
- 任何語言 runtime、regex 引擎（很多把 pattern 編成 bytecode）、資料庫執行器、shader/GPU 命令緩衝——看到「編譯 + 巨型 switch 直譯」就是這張卡。

**遷移時要問的五個問題**（你的 VM 讀碼 checklist）：(1) 指令陣列在哪、pc 怎麼前進？(2) register 還是 stack？(3) dispatch 是 `switch` 還是 computed goto？(4) 跳轉怎麼實作（那個 `-1` off-by-one）？(5) 熱 opcode 的體積是核心邏輯還是快取/fast path？

## Pattern 卡 2：pager / page-cache 抽象

**一句話**：在「檔案是一串固定大小的頁」之上墊一層，提供「給我第 N 頁」的抽象，內部管快取（在記憶體就直接回、不在就讀盤）和交易化，讓上層完全不管 I/O 和持久性細節。

**beacon**：
- 一個 `GetPage(n)` 式的入口（SQLite: `sqlite3PagerGet`），上層要資料只透過它。
- 「頁號 → byte offset」的換算（SQLite: `(pgno-1)*pageSize`，pager.c:3037）。
- 一個 **cache 命中/未命中** 的分岔：hit 直接回、miss 才碰底層 I/O。
- 一個把「一批頁修改」變成**原子交易**的機制（journal / WAL / undo log）。
- 一個 pager **狀態機**（`eState`：OPEN→READER→WRITER…）。

**在哪**：`src/pager.c`。入口 `sqlite3PagerGet`（pager.c:5707，但真邏輯在函式指標 `xGet`→`getPageNormal`）、讀盤 `readDbPage`（pager.c:3018）、狀態欄位 `Pager.eState`/`eLock`。快取實作在 `pcache.c`/`pcache1.c`。B-tree（`btree.c`）完全透過 `sqlite3PagerGet` 拿頁，不碰 I/O。

**可遷移**：
- **作業系統的 page cache / buffer cache**：kernel 把磁碟區塊快取在記憶體，同一個「頁號→快取或讀盤」的分岔。你的 `kernel_internals` 課的 page cache 就是這張卡的 OS 版。
- **任何 DBMS 的 buffer pool**：PostgreSQL（Part 6 Capstone）的 `shared_buffers`、MySQL 的 buffer pool——都是「pager 抽象 + LRU 淘汰 + 交易日誌」。
- **mmap-based storage、LSM-tree 的 block cache、瀏覽器的 HTTP cache**——凡是「固定單位 + 快取層 + 命中/未命中」都是這張卡的變體。

**遷移時要問**：快取單位多大？淘汰策略（LRU/clock）？dirty page 何時寫回？怎麼保證 crash 一致性（undo 舊值 vs redo/WAL 新值）？

## Pattern 卡 3：可插拔後端（VFS / 函式指標介面）

**一句話**：把「會因平台/環境而異」的一組操作，抽象成一個**函式指標 struct**（vtable），核心邏輯只呼叫這些指標，換平台/換實作就換一份 struct，核心碼一行不改。

**beacon**：
- 一個全是**函式指標的 struct**（`int (*xRead)(...); int (*xWrite)(...);` 一長排）。
- 一個「薄封裝」入口：本體只有 `return obj->pMethods->xFoo(...)` 一句（SQLite: `sqlite3OsRead`，os.c:88）。
- 同一組介面有**多份實作**，用檔名/平台區分（`os_unix.c` 的 `unixRead` vs `os_win.c` 的 `winRead`）。
- 一個註冊/選擇機制（`sqlite3_vfs_register`）。

**在哪**：`struct sqlite3_vfs`（sqlite.h.in，一排 `xOpen`/`xDelete`/`xRandomness`…）和 `struct sqlite3_io_methods`（sqlite.h.in，`xRead`/`xWrite`/`xSync`/`xLock`…）。薄封裝 `sqlite3OsRead`（os.c:88）本體就一句 `return id->pMethods->xRead(...)`。Unix 實作在 `os_unix.c`（`unixRead`，os_unix.c:3347）、Windows 在 `os_win.c`。page cache 也用同招（`sqlite3_pcache_methods2`）。

**可遷移**：
- **這就是 C 版的多型/依賴反轉**。C++ 的虛函式表、Rust 的 trait object、Go 的 interface——底層都是這個函式指標 vtable。看到「一 struct 的函式指標」你就認出「這是 C 在做多型」。
- **Linux VFS**（`file_operations` 結構）、驅動框架、`fuse`——kernel 到處是這張卡（`reading_code` Ch 23 讀 indirection 的主戰場）。
- SQLite 的 VFS 讓它能跑在記憶體、加密層、網路檔案系統之上——**任何「可換後端」的需求**（storage backend、transport、allocator）都用它。

**遷移時的陷阱**（也是讀碼陷阱）：函式指標**切斷直線閱讀**。讀到 `obj->pMethods->xRead(...)` 你不知道跳去哪——必須先找出「這個 obj 的 pMethods 在哪被設定成哪份實作」（`rg` 那個 struct 的 initializer）。這是 `reading_code` Ch 23 的核心技巧，SQLite 的 `sqlite3PagerGet`→`xGet` 和 `sqlite3OsRead`→`xRead` 是兩個活例子。

## Pattern 卡 4：防禦式 C（assert / testcase / self-healing）

**一句話**：用一套 build 時可切換的巨集，把「開發者的假設」「危險邊界」「防禦分支」全部外化成 code，開發/測試時嚴格驗證、release 時蒸發成零開銷，同時保留對惡意輸入的真防線。

**beacon**：
- **超高密度的 `assert`**（SQLite: 每 20 幾行一個），function 開頭一排前置條件 assert。
- `testcase(X)` 這種「求值但不做事」的覆蓋率標記。
- `ALWAYS(X)`/`NEVER(X)`/`likely()`/`unlikely()` 這類「分支意圖 + 防禦」巨集。
- 對外部輸入（檔案/網路）的 `return 錯誤碼`（SQLite: `SQLITE_CORRUPT_BKPT`），和「內部必真、外部可壞」的複合檢查（`assert( X || CORRUPT_DB )`）。

**在哪**：巨集定義全在 `src/sqliteInt.h`（`assert`/`testcase` 第 477、`ALWAYS`/`NEVER` 第 535、`SQLITE_CORRUPT_BKPT` 第 4600、`CORRUPT_DB` 第 4344）。使用點遍布 `vdbe.c`/`btree.c`（真 clone 數：6318 assert、881 testcase、185 ALWAYS、133 NEVER）。

**可遷移**：
- **Linux kernel**：`BUG_ON`/`WARN_ON`（debug 檢查）vs 錯誤返回（真防線）、`likely()`/`unlikely()`（分支意圖）。
- **Chromium**：`DCHECK`（debug-only）vs `CHECK`（永遠在）——和 SQLite `assert` vs `CORRUPT_BKPT` 精確對應。
- **OpenSSL、libpng、任何處理不可信輸入的 C**：都有「意圖標註 vs 真防線」的分層。
- **讀碼判讀框架（可直接搬）**：看到任何檢查巨集，問兩題——「release 會不會消失？」「它是文件還是防線？」。這兩題把所有這類巨集分類完畢。

## Pattern 卡 5：amalgamation build（分檔開發、單檔部署）

**一句話**：開發時分成乾淨的多檔（好維護、好讀），build 時用工具把它們（加上生成檔）串接壓平成一個巨檔（好編譯最佳化、好部署）。**「讀的形態」和「用的形態」刻意不同。**

**beacon**：
- 一個「把 N 個 source 串成 1 個」的**生成工具**（SQLite: `tool/mksqlite3c.tcl`）。
- 部署物是**一兩個巨檔**（`sqlite3.c` 26 萬行 + `sqlite3.h`），但 repo 裡是幾百個小檔。
- 大量 `static`（單一 translation unit 裡靠 static 隔離命名）。
- 有一批**生成的 source**（SQLite: `parse.c`、`opcodes.h`、`keywordhash.h`）不在 repo、build 時才冒出來。

**在哪**：`tool/mksqlite3c.tcl`（串接工具，開頭註解說明流程）。生成物 `sqlite3.c`（本課 build 出 262689 行）。生成的中間檔：`parse.c`（lemon 從 `parse.y` 生）、`opcodes.h`（`mkopcodeh.tcl` 從 `vdbe.c` 的 `case` 生）。

**可遷移**：
- **任何 vendored 進你專案的 SQLite**——你 `rg` 只看到 `sqlite3.c` 別以為那就是原貌，去找 `src/` 分檔讀。
- **header-only 函式庫**（C++ 的 `stb_*.h`、`nlohmann/json`）：同一哲學——開發可能分檔、發佈壓成單 header。
- **webpack/rollup/esbuild 的 bundle**：前端把幾百個模組打包成一個 `bundle.js`——**這就是 JS 世界的 amalgamation**，同樣「讀 source、debug 用 sourcemap 對回原檔」。
- **讀碼判讀（可搬）**：拿到一個巨檔先問「這是手寫的還是生成/打包的？」。若是生成的，**去讀生成器的輸入**（`parse.y` 而非 `parse.c`、模組源碼而非 bundle）——這是 `reading_code` Ch 22 的鐵律，適用所有生成碼。

## 怎麼用這五張卡：一次 beacon-scan 演練

卡片不是拿來背的，是拿來**掃**的。示範一次：假設你冷讀一個從沒看過的嵌入式資料庫（就當是 LMDB 或某個 CTF 題裡的自製 DB），限時 15 分鐘，只用這五張卡的 beacon 去問問題。你的動作是一連串 `rg`：

```bash
# 卡1 bytecode VM？找巨型 switch / dispatch loop
$ rg -c "case OP_|switch.*opcode|for\(;;\)" *.c
# 卡2 pager？找「給我第 N 頁」式的入口 + 頁號→offset 換算
$ rg -n "PagerGet|GetPage|page.*offset|pgno.*pageSize" *.c
# 卡3 可插拔後端？找全是函式指標的 struct + 薄封裝入口
$ rg -n "struct.*\{" *.h -A20 | rg "\(\*x?[A-Z]\w+\)\("
# 卡4 防禦式 C？數 assert 密度、找 debug-only 巨集
$ rg -c "assert\(|CHECK\(|BUG_ON" *.c
# 卡5 生成/打包？找超大單檔或生成工具
$ ls -S *.c | head -1 ; rg -l "Automatically generated|DO NOT EDIT" .
```

**每個命中都是一個假設，不是結論**——`reading_code` Ch 10。找到巨型 switch，你的假設是「這裡有 bytecode VM」，然後順著 `switch` 上面找 dispatch loop、找 register/stack、驗證。15 分鐘後你對這個陌生 DB 的架構已有一張草圖：它有沒有 VM？儲存怎麼抽象？後端可不可插？防禦文化如何？——**這就是 pattern 庫的複利：認出五個 pattern 讓你冷讀速度快一個檔次**。沒有卡片的人得從第一行慢慢推，有卡片的人直接跳到「驗證假設」。

**卡片模板（你之後每讀完一個 codebase 都產一疊）**：

```
┌─ Pattern 名 ──────────────────────────────────┐
│ 一句話：這個 pattern 在解決什麼                  │
│ beacon：一眼認出的 2–4 個視覺/結構信號（最重要） │
│ 在哪：真實檔案:function（能 rg 驗證）            │
│ 可遷移：在別的專案長怎樣 + 我會在哪再遇到        │
│ 遷移時要問：套用前該確認的 3–5 個問題           │
└──────────────────────────────────────────────┘
```

最重要的是 **beacon 欄**——記名字沒用，記「怎麼一眼認出」才有用。

## 底層機制：這五張卡怎麼咬合成 SQLite

單看每張卡是孤立技巧，但它們在 SQLite 裡是一個**協調的整體**——這也是「讀一個成熟 codebase」比「學五個 pattern」多的東西：

```
   一條 SQL 的旅程，五張卡各司其職：

   SQL 文字
     │  ┌─────────────────────────────────────────────┐
     │  │ 卡5 amalgamation：你讀的是 src/ 分檔，          │
     │  │      但使用者編的是 sqlite3.c 單檔              │
     │  └─────────────────────────────────────────────┘
     ▼
   parser (parse.y，生成 parse.c) ── 卡5：讀文法不讀生成檔
     │
     ▼  codegen 產出 bytecode
   ┌─────────────────────────────┐
   │ 卡1 bytecode VM：sqlite3VdbeExec │  ← 執行核心
   │   巨型 switch 直譯 opcode        │
   └──────────┬──────────────────┘
     opcode 要資料 ▼
   ┌─────────────────────────────┐
   │ 卡2 pager：sqlite3PagerGet    │  ← 快取 + 交易
   │   cache hit 直接回 / miss 讀盤 │
   └──────────┬──────────────────┘
     真碰 I/O ▼
   ┌─────────────────────────────┐
   │ 卡3 VFS：sqlite3OsRead →       │  ← 可插拔後端
   │   函式指標 → unixRead / winRead │
   └─────────────────────────────┘

   ┌─────────────────────────────────────────────────┐
   │ 卡4 防禦式 C：貫穿以上每一層——每個 function 開頭 │
   │      的 assert、每個邊界的 testcase、每個對壞檔的  │
   │      CORRUPT_BKPT，是縫住整棟建築的鋼筋            │
   └─────────────────────────────────────────────────┘
```

- **卡 1（VM）解耦編譯與執行**——query planner 能獨立演化。
- **卡 2（pager）解耦邏輯與儲存**——B-tree 不管 I/O。
- **卡 3（VFS）解耦儲存與平台**——換 OS/加密/記憶體不改核心。
- **卡 5（amalgamation）解耦開發形態與部署形態**——分檔開發、單檔部署。
- **卡 4（防禦式 C）不是解耦，是黏合**——用測試文化保證上面四層的每個接縫都不會在邊界情況崩掉。

**這才是重點**：成熟 codebase 的美不在單個 pattern，在**pattern 之間乾淨的接縫**。VDBE 只透過 `sqlite3PagerGet` 碰 pager、pager 只透過 `sqlite3OsRead` 碰平台——每個接縫都是一個窄介面（一個 function 或一個函式指標 struct）。讀懂接縫在哪，你就讀懂了架構。

## 對比與取捨：這些 pattern 的代價

pattern 不是免費的，`reading_code` 教你讀碼也要讀出取捨：

| pattern | 買到什麼 | 代價 |
|---|---|---|
| bytecode VM | 編譯/執行解耦、可 EXPLAIN、prepared statement 重用 | 多一層編譯、直譯比原生碼慢（換 JIT 才追回） |
| pager 抽象 | I/O 細節封裝、統一交易 | 多一層間接、cache 管理複雜度 |
| VFS 函式指標 | 可插拔、跨平台 | 間接呼叫成本、閱讀被切斷（要追指標） |
| 防禦式 C | 極高可靠性、假設外化 | 視覺雜訊（每 20 行一 assert）、寫起來囉嗦 |
| amalgamation | 編譯最佳化、零摩擦部署 | 讀 vendored 版時 26 萬行單檔難定位 |

**SQLite 對每個代價都給了答案**：VM 慢 → 它的場景（嵌入式、少量資料）不在乎那點慢；間接成本 → 現代 CPU 分支預測吃得下；防禦雜訊 → 學會把 assert 當文件讀就變路標；單檔難讀 → 讀原始碼去 `src/`。**讀碼時看懂「作者知道代價、且判斷值得」，比看懂 pattern 本身更高一層。**

## 踩雷集錦

1. **讀完就走、不產卡片**：這是萃取章存在的意義。逐行讀過 SQLite 但沒把 pattern 結晶成「beacon + 在哪 + 可遷移」，下次遇到 PostgreSQL 的 buffer pool 你還是從頭推。**pattern 要外化成卡片才進長期記憶**（`reading_code` Ch 35）。
2. **背 pattern 的名字，不記 beacon**：「哦這是 pager pattern」——但下次你認不出來，因為你記的是名字不是**辨識信號**。卡片最重要的是 beacon 那欄：「一 struct 全函式指標 + 薄封裝入口」才是你真正要 chunk 的視覺線索。
3. **以為 pattern 是普世最佳解**：VFS 的函式指標間接在極致效能場景（如高頻交易）可能不值得。pattern 是**針對特定取捨的解**，抄之前先問「我的取捨和 SQLite 一樣嗎」。SQLite 選可攜性 > 極致效能，你的專案未必。
4. **只認出孤立 pattern、看不到接縫**：真正的架構理解在「VDBE 怎麼接 pager、pager 怎麼接 VFS」的**窄介面**上。只會說「這裡有個 VM、那裡有個 cache」而講不出它們怎麼咬合，等於沒讀懂架構。
5. **把「beacon」當成硬規則**：beacon 是啟發式線索，不是充要條件。看到巨型 switch 不一定是 bytecode VM（也可能是協定 parser 的狀態機）。beacon 幫你**快速形成假設**，還是要順著驗證（`reading_code` Ch 10 假設驅動）。

## 進階：再往深一層

- **把這五張卡加進你的總 pattern 字典**：Ch 30 會把六個 codebase 的所有卡片收斂成一張總表。現在就開一個檔（或筆記），把這五張以「beacon / 在哪 / 可遷移」格式記下——之後 nginx（reactor、memory pool、plugin pipeline）、git（content addressing、DAG）、CPython（refcount、object protocol）的卡片會陸續加入，最後你有一本自己的「一眼認出」字典。
- **反向練習——拿卡片去掃第七個專案**：找一個你沒讀過的嵌入式資料庫（如 LMDB、DuckDB），限時 30 分鐘，只用這五張卡的 beacon 去掃：它有 bytecode VM 嗎？pager 抽象在哪？後端可插拔嗎？防禦式慣例長怎樣？你會驚訝於「認出 pattern」讓冷讀速度快多少——這就是 pattern 庫的複利。
- **pattern 的反例也是資訊**：DuckDB 是向量化執行、不用 bytecode VM（用 push-based pipeline）；LMDB 用 mmap、幾乎沒有傳統 pager cache。當一個專案**沒有**某張卡，那個「沒有」本身告訴你它的設計取捨（DuckDB 為 OLAP 犧牲了 SQLite 的簡單性換吞吐）。**認出 pattern 的缺席和認出它的存在同樣有價值。**

## 本章重點整理

- Part 2 的五張可遷移 pattern 卡：**bytecode VM 直譯**、**pager/page-cache 抽象**、**VFS 可插拔後端（函式指標 vtable）**、**防禦式 C（assert/testcase/self-healing）**、**amalgamation build**。
- 每張卡的價值在三欄：**beacon（一眼認出的信號）** > 在哪 > 可遷移。記 beacon，不要只記名字。
- 五張卡在 SQLite 裡**咬合成一個整體**：VM/pager/VFS 各解耦一層，接縫都是窄介面（一個 function 或一個函式指標 struct）；防禦式 C 是黏合每個接縫的鋼筋。**讀懂接縫 = 讀懂架構。**
- 每個 pattern 都有代價，SQLite 對每個代價都做了「知道且值得」的判斷。讀出取捨比讀出 pattern 高一層。
- pattern 庫是複利：這五張卡會在 nginx/git/CPython/PostgreSQL 反覆再遇到（尤其 bytecode VM 三方對照、pager→buffer pool、函式指標多型）。萃取要外化成卡片才生效。

## 自我檢核

- [ ] 我能不看教材，說出五張卡的名字，並對每張講出它的 beacon（一眼認出的信號）
- [ ] 我能對每張卡指出它在 SQLite 的真實 function/檔案，並 `rg` 驗證
- [ ] 我能對每張卡舉出至少一個「我會在別處再遇到它」的例子（本課其他 Part 或我自己領域）
- [ ] 我能畫出五張卡在 SQLite 裡怎麼咬合，並指出至少三個「窄介面接縫」在哪
- [ ] 我開始建自己的 pattern 字典檔，把這五張以「beacon/在哪/可遷移」格式存進去了

## 延伸閱讀

- **《The Programmer's Brain》第 2–3 章（chunking、beacon）** — Felienne Hermans
  - **讀哪裡**：beacon 那節。它從認知科學解釋「為什麼卡片的 beacon 欄是最重要的」——專家讀碼快是因為靠 beacon 瞬間 chunk。讀完你會更認真對待每張卡的 beacon 欄，而不是只記 pattern 名。
  - **前提**：無。
- **[The Architecture of Open Source Applications — SQLite](https://aosabook.org/en/v1/sqlite.html)**（Grover & Hipp）
  - **讀哪裡**：整章。SQLite 作者群親自導讀架構，和本章五張卡高度重疊——拿它對照你自己萃取的卡片，看有沒有漏掉的 pattern（如它對 test suite 的著墨呼應卡 4）。
  - **前提**：讀完 Part 2 前四章。
- **`reading_code` Ch 35「外化理解」與本課 Ch 30「你的 pattern 字典」**
  - **讀哪裡**：Ch 35 教怎麼把理解外化成筆記/圖/卡片；Ch 30 是六個 codebase 卡片的總收斂。本章的五張卡是那張總表的第一批貨——現在建立記卡片的習慣，到 Ch 30 你會有一疊現成的。
  - **前提**：無。

Part 2 讀完了：你有了 SQLite 的地圖、追過 text→disk 的完整路徑、讀懂了它的防禦式風格、萃取了五張 pattern 卡。最後用一次限時攻堅把這一切串起來——練習 B 要你親手追一條 `SELECT x FROM t WHERE id=?` 從文字到磁碟讀取的完整路徑，能用 `EXPLAIN` 和 shell 真跑就真跑。這是把「讀過」變成「會攻」的臨門一腳。

→ [練習 B：追一條 SQL 從 text 到 disk read](./practice-b-sqlite-trace-a-query.md)
