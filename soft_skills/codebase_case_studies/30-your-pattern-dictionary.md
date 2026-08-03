# Ch 30 — 你的 pattern 字典：六個 codebase 的 idiom 收斂成一張表

> **目標**：把六個 codebase（Lua / SQLite / nginx / git / CPython / PostgreSQL）萃取出的所有設計 pattern，收斂成一張**可查表**。每個 pattern 給你三樣東西：beacon（一眼認出它的信號）、在哪些 codebase 見過（真檔案佐證）、可遷移到哪。這是全課的畢業證書——你帶走的不是六個專案的知識，是一個「一眼認出」的 chunk 庫。

> **目標codebase**：全部六個。引用散落各處，行號都對應 Ch 0 釘死的 tag（Lua `v5.4.7`、SQLite `version-3.47.2`、nginx `release-1.26.2`、git `v2.47.1`、CPython `v3.13.1`、PostgreSQL `REL_17_2`）。

## 為什麼需要這個？

`reading_code` Ch 1 講過 chunking：專家讀得快，不是讀得更用力，是把一大段 code **一眼 chunk 成一個已知概念**。看到二十行的 `for` 迴圈配一個 tagged struct，新手逐行讀，專家瞄一眼說「reference-counted object pool」然後跳過——因為他腦裡有這個 chunk。

**這門課從頭到尾在做一件事：擴充你的 chunk 庫。** 六個 Part、六個萃取章（Ch 7、12、17、21、26），每個都往你腦裡塞了幾個 pattern。但散在六章裡的 pattern 不好用——你需要一張**索引**，把它們並排，讓「這個 pattern 在別的地方也見過」的連結顯形。那個「在別處也見過」正是複利的來源：一個 pattern 在越多 codebase 出現，你越確信它是真 pattern（不是某專案的特例），遷移信心就越強。

這一章就是那張索引。**它不是總結——它是工具。** 讀完你該把它印出來/存成筆記，下次攻堅陌生 code 時，對照 beacon 一欄快速歸類眼前的 code 屬於哪個 pattern。

## 怎麼用這張字典

每個 pattern 卡片三欄，對應攻堅時的三個問題：

```
   ┌─ beacon ──────────── 「我怎麼一眼認出它？」
   │                       → 攻堅時掃 code 找這些信號
   │
   ├─ 在哪見過 ─────────── 「它是真 pattern 還是特例？」
   │                       → 出現的 codebase 越多越可信（真檔案佐證）
   │
   └─ 可遷移到 ─────────── 「認出它之後我知道什麼？」
                           → 它通常伴隨的問題、變體、下一步該找什麼
```

**攻堅時的動作**：掃到一段陌生 code → 對照 beacon 欄 → 命中某 pattern → 立刻套用「可遷移到」欄的先驗知識（這個 pattern 通常怎麼組織、坑在哪、配套機制是什麼）。從「逐行讀懂」變成「認出來 + 驗證細節」。

## Pattern 卡片

### 1. bytecode VM dispatch loop（bytecode 虛擬機分派迴圈）

**beacon**：一個超長函式，內含 `for(;;)` 或 `while(1)`，body 是一個巨大的 `switch(opcode)` 或 `goto *表[opcode]`，每個 case 是一條「指令」。函式名常帶 `Exec`/`eval`/`execute`/`vm`/`interp`。

**在哪見過**：
- Lua `luaV_execute`（`lvm.c:1151`）——computed goto / switch 可切
- SQLite `sqlite3VdbeExec`（`vdbe.c:813`）——`for(pOp=...)` + `switch(pOp->opcode)`（`vdbe.c:981`）
- CPython `_PyEval_EvalFrameDefault`（`ceval.c:682`）——computed goto，`DISPATCH()` macro
- jq `jq_next`（`src/execute.c`）——`while(1)` + `switch(opcode)`
- **PostgreSQL 表達式求值** `ExecInterpExpr`（`execExprInterp.c`）——連 PG 內部都藏一個

**可遷移到**：任何「先編譯成中間表示、再解釋執行」的系統——正則引擎、模板引擎、序列化格式解碼、遊戲腳本、shader 直譯器。認出它之後，馬上該問三題（Ch 27 的模板）：register 還是 stack？怎麼 dispatch？值怎麼表示？三題答完，這個 VM 的個性就定了。

### 2. tagged union（帶標籤的聯合體，值內嵌）

**beacon**：一個 `struct` 裡有一個 `union` + 一個小整數/enum 當「現在是哪一種」的 tag。tag 名常叫 `type`/`tt`/`kind`/`tag`/`flags`。取值前先看 tag、再讀 union 對應成員。

**在哪見過**：
- Lua `TValue` = `Value value_` union + `lu_byte tt_` tag（`lobject.h:49`、`lobject.h:65`）
- SQLite `Mem`（`sqlite3_value`）= `union MemValue u` + `u16 flags`（`vdbeInt.h:225`）——肥版，多了變長 blob 欄位
- PostgreSQL `Node`/`NodeTag`——每個節點開頭一個 `NodeTag type`，`castNode(AggState, ...)` 就是查 tag 後轉型

**可遷移到**：任何「一個值可以是好幾種型別之一」的地方——AST 節點、JSON 值、動態語言的值、協定訊息、編譯器 IR。認出它 → 知道「小值內嵌、複製便宜、大值多半用指標成員指向堆上物件」，也知道所有操作都得先 switch/if on tag。對照 CPython 的 boxed object（pattern 3）是它的反面。

### 3. boxed object + refcount（堆上物件 + 引用計數）

**beacon**：每個值都是堆上物件的指標，物件開頭有 `refcnt`/`ob_refcnt` 欄位 + 一個 `type` 指標。到處是 `INCREF`/`DECREF`/`retain`/`release` 成對出現。「載入一個值」= 複製指標 + refcount 加一。

**在哪見過**：
- CPython `PyObject` = `ob_refcnt` + `ob_type`（`object.h:163`）；`Py_INCREF` 做 `op->ob_refcnt++`（`object.h:837`）；`LOAD_FAST` 之後必 `Py_INCREF(value)`（`bytecodes.c:234`）
- （對照）Lua/SQLite 不用這招——它們小值內嵌，只有 GC 物件上堆

**可遷移到**：Objective-C/Swift 的 ARC、COM 的 `AddRef`/`Release`、glib GObject、任何「共享所有權 + 手動或半自動生命週期」的 C/C++ 系統。認出它 → 知道要盯「INCREF/DECREF 配不配對」（洩漏/UAF 的根源），也知道**純 refcount 收不了循環引用**，所以配套一定有一個循環 GC（見 pattern 8）。

### 4. arena / pool allocator（區域 / 池式配置器）

**beacon**：一個 `create_pool`/`arena_new` 建一大塊，之後所有小配置從裡面切（`palloc`/`alloc_from`），**沒有對應的 free**——一次 `destroy_pool`/`arena_free` 整批釋放。生命週期綁在某個作用域（一個 request、一個 query、一個 parse）。

**在哪見過**：
- nginx `ngx_pool_t`（`ngx_palloc.h:57`）+ `ngx_palloc`/`ngx_pnalloc`/`ngx_pcalloc`（`ngx_palloc.h:79`）+ `ngx_destroy_pool`——每個 request 一個 pool，request 結束整個 destroy
- PostgreSQL `MemoryContext`——per-query / per-tuple context，`ResetPerTupleExprContext`（`execMain.c:1649`）每 tuple 整批清
- Lua 的 `luaM_*` 記憶體層 + GC（近親：集中管理生命週期）

**可遷移到**：任何「一批物件同生共死」的場景——編譯器一個 pass、web 一個 request、遊戲一個 frame、解析一份文件。認出它 → 知道「別找配對的 free，找那個整批釋放點」，也知道這是避免碎片化 + 避免漏 free 的經典手法。beacon 反模式：如果你在 pool 裡找每個 object 的 free，你誤解了這個 pattern。

### 5. reactor / event loop（反應器 / 事件迴圈）

**beacon**：一個主迴圈 `for(;;)` 呼叫某個 `wait_for_events`（`epoll_wait`/`kqueue`/`poll`），拿回一批就緒的 fd，逐個呼叫其 handler（callback / 函式指標）。非阻塞 IO、狀態機式的連線處理。

**在哪見過**：
- nginx `ngx_process_events_and_timers`（`ngx_event.c:195`）→ 透過 `ngx_event_actions`（`ngx_event.c:44`，函式指標表）分派到 `ngx_epoll_process_events`（`ngx_epoll_module.c:784`）
- 這是 nginx 高並發的心臟；Node.js libuv、Redis ae、任何 async runtime 同構

**可遷移到**：任何高並發 IO 系統——web server、代理、資料庫連線層、訊息佇列、遊戲伺服器。認出它 → 知道「單執行緒也能扛海量連線」的祕密在此，也知道要找「連線在哪個狀態機狀態、handler 怎麼註冊」。配套常見 pattern 4（每連線一個 pool）和 pattern 7（handler pipeline）。

### 6. content addressing / DAG（內容定址 / 有向無環圖）

**beacon**：物件的「名字」是它內容的雜湊（SHA-1/SHA-256）。存取靠 `oid`/`hash`/`digest`。物件之間用雜湊互相指涉，形成不可變的 DAG（改一個物件 → 雜湊變 → 所有指向它的物件也得變）。去重天然發生（同內容同雜湊）。

**在哪見過**：
- git `struct object_id`（`hash.h`）+ `struct object { ... struct object_id oid; ... }`（`object.h:158`）；`lookup_object`/`parse_object` 全靠 oid 定址
- 這是 git 的整個世界觀：blob/tree/commit 都是 content-addressed，commit DAG 就是歷史

**可遷移到**：Docker layer（也是 content-addressed）、IPFS、Nix/Bazel 的 build cache、任何去重儲存、區塊鏈、Merkle tree 驗證。認出它 → 知道「物件不可變、改一處要重建一條鏈、去重免費、可驗證完整性」這一整套性質全都自動成立。

### 7. plugin / handler pipeline（外掛 / 處理器管線）

**beacon**：一個請求/資料流依序穿過一串階段（phase/stage/filter），每階段一個 handler（函式指標），handler 可以「處理、放行、短路」。handler 註冊進一張表或串成 list。新功能靠「加一個 handler」而非改核心。

**在哪見過**：
- nginx HTTP phases——`NGX_HTTP_CONTENT_PHASE` 等（`ngx_http_core_module.h:125`）、`ngx_http_phase_handler_t`（`ngx_http_core_module.h:130`）；一個 request 穿過 phase 陣列
- PostgreSQL executor 的節點樹某種意義上也是（每節點一個 `ExecProcNode` 函式指標，Ch 28）
- SQLite 的 VFS 層、CPython 的 codec/import hook 也是同族

**可遷移到**：middleware（Express/Django）、編譯器 pass pipeline、圖形 render pipeline、網路封包處理鏈、任何「可插拔階段」架構。認出它 → 知道「找 phase 列表/handler 註冊點，就懂了整個控制流骨架」，也知道加功能該加 handler 不該改核心。

### 8. refcount + cyclic GC（引用計數 + 循環回收）

**beacon**：主要靠 refcount 管生命週期（pattern 3），但另外有一個獨立的 GC 子系統專門處理循環引用。物件除了 refcount，還掛在一條 GC 追蹤鏈上（額外的 `gc_next`/`gc_prev`）。GC 週期性/增量地掃這條鏈找不可達的環。

**在哪見過**：
- CPython——`PyObject` 的 refcount（pattern 3）+ 循環 GC：`PyGC_Head` 有 `_gc_next`（`pycore_gc.h:17`），GC 物件前面藏一個 `PyGC_Head`（`_Py_AS_GC`，`pycore_gc.h:28`）
- Lua——不用 refcount，用純增量 GC：`allgc` 串所有可回收物件（`lstate.h:276`），`luaC_step`（`lgc.c:1690`）增量推進。是「另一種 GC 策略」的對照

**可遷移到**：任何有循環引用可能的託管記憶體系統。認出 refcount（pattern 3）→ 立刻該問「循環怎麼收？」→ 找那個配套的循環 GC。認出增量/分代 GC → 該問「三色標記怎麼推進、write barrier 在哪」（Lua Ch 6）。**這張卡的價值：看到 pattern 3 就知道去找 pattern 8。**

### 9. command dispatch table（命令分派表）

**beacon**：一個 `struct { const char *name; fn handler; flags; }` 的**陣列**，程式啟動時按第一個引數（子命令名）查表、呼對應 handler。加子命令 = 表裡加一列。

**在哪見過**：
- git `struct cmd_struct`（`git.c:32`）+ `commands[]` 陣列（`git.c:506`），如 `{ "add", cmd_add, RUN_SETUP | NEED_WORK_TREE }`；`get_builtin`（`git.c:653`）查表、`run_builtin`（`git.c:444`）呼叫。flags 欄位還編碼了「這命令要不要 setup、要不要 work tree」的元資料
- CPython 的 method table（`PyMethodDef[]`）同構——name → C 函式

**可遷移到**：任何多子命令 CLI（docker、kubectl、cargo）、REPL 命令、協定的 opcode handler 表、任何「字串/整數 → 處理函式」的分派。認出它 → 知道「要找某子命令的實作，先在這張表找它的 handler 名，再跳過去」，這是讀多命令工具的最快入口（Ch 20 讀 git 子命令用的正是這招）。

### 10. iterator / 火山模型（pull-based iterator）

**beacon**：一棵/一串節點，每個節點提供一個 `next()`/`ExecProcNode`/`getNext` 方法，「被叫就吐一個元素或 NULL（結束）」。上層節點在自己的 next 裡呼叫子節點的 next（往下 pull）。統一介面下，有的節點 streaming（拉一個吐一個）、有的 blocking（吃完才吐）。

**在哪見過**：
- PostgreSQL executor——`ExecProcNode`（函式指標，`executor.h:269`）沿 PlanState 樹 pull；`ExecutePlan` 的 `for(;;) slot = ExecProcNode(planstate)`（`execMain.c:1665`）；Agg 在內部 `ExecProcNode(outerPlanState(aggstate))` 向下 pull（`nodeAgg.c:547`）——Ch 28
- Python 的 generator / iterator protocol（`__next__`）是語言層的同一個 pattern

**可遷移到**：所有 query engine、串流處理框架（Spark/Flink 的 operator）、Unix pipe 的概念、LINQ、RxJS、任何「可組合的資料轉換管線」。認出它 → 知道「找每個節點的 next、看誰 pull 誰，就懂了整個資料流」，也知道 streaming vs blocking 節點靠統一介面自由組合（Ch 28 的核心洞察）。

### 11. pager / buffer pool（分頁器 / 緩衝池）

**beacon**：磁碟被切成固定大小的 page，記憶體裡有一個快取（buffer pool / page cache）持有部分 page。存取資料前先 `getPage(pgno)`（命中回記憶體、未命中從磁碟讀入 + 可能淘汰別人）。有 pin/unpin、dirty flag、eviction 策略。

**在哪見過**：
- SQLite pager——`sqlite3PagerGet(pPager, pgno, &pPg, ...)`（`pager.c:2458`）；B-tree（`btree.c`）建在 pager 之上，把 page 當節點（Ch 10）
- PostgreSQL 的 shared buffers（buffer manager，`storage/buffer/`）同構——本課劃界外，但認出 pattern 就知道它在那

**可遷移到**：所有資料庫儲存引擎、檔案系統的 page cache、mmap 式資料結構、任何「資料太大放不進記憶體、分頁快取」的系統。認出它 → 知道「找 page 大小、eviction 策略、dirty/pin 機制」，也知道上層資料結構（B-tree、heap）都建在 page 抽象之上，不直接碰磁碟。

### 12. macro-based dispatch switching（巨集切換分派機制）

**beacon**：同一份 case body，靠**重新定義幾個 macro**（`vmcase`/`TARGET`/`vmbreak`）在「switch」和「computed goto」之間切換。你以為看到 `case`，展開後可能是 label `L_xxx:`。常配一個 `#if` 判斷編譯器支不支援 label-as-value。

**在哪見過**：
- Lua——`vmdispatch`/`vmcase`/`vmbreak` 三個 macro（`lvm.c:1146` 是 switch 版，`ljumptab.h` 是 goto 版）
- CPython——`TARGET(op)` / `DISPATCH_GOTO()` 在 `ceval_macros.h:73` 依 `USE_COMPUTED_GOTOS` 切換

**可遷移到**：任何要「一份 code、兩種效能/可攜取捨」的地方——這也是一個**讀碼陷阱 pattern**：看到 `vmcase`/`TARGET` 這種名字要警覺「這不是普通 case」。認出它 → 不會被 indirection 騙、知道要把 macro 兩種展開都看過才算讀懂 dispatch（Ch 27 的高光）。這張卡教你的是**識破偽裝的能力**，不只是一個結構。

## 一張總覽表（可查索引）

| # | Pattern | 一眼 beacon | 主要見於（真檔案）| 遷移到 |
|---|---|---|---|---|
| 1 | bytecode VM dispatch | 巨 switch/goto on opcode + `for(;;)` | Lua `lvm.c:1151`、SQLite `vdbe.c:813`、CPython `ceval.c:682` | 正則/模板/腳本引擎 |
| 2 | tagged union | union + tag byte，取值先看 tag | Lua `TValue`、SQLite `Mem`、PG `Node` | AST/JSON/IR/協定 |
| 3 | boxed object + refcount | 物件開頭 refcnt+type，滿地 INCREF | CPython `object.h:163/837` | ARC/COM/GObject |
| 4 | arena / pool | create → 一堆 alloc → 一次 destroy，無配對 free | nginx `ngx_pool_t`、PG `MemoryContext` | 編譯 pass/request/frame |
| 5 | reactor / event loop | `for(;;)` + `epoll_wait` + handler 分派 | nginx `ngx_event.c:195` | async runtime/proxy/MQ |
| 6 | content addressing / DAG | 名字=內容雜湊，物件互指成不可變 DAG | git `object.h:158`+`object_id` | Docker/IPFS/build cache |
| 7 | handler pipeline | 依序穿過 phase，每階段一 handler | nginx HTTP phases、PG 節點樹 | middleware/render pipeline |
| 8 | refcount + cyclic GC | refcount + 額外 GC 追蹤鏈收循環 | CPython `PyGC_Head`、Lua `luaC_step` | 任何託管記憶體 |
| 9 | command dispatch table | `{name, fn, flags}[]` 陣列 + 查表呼叫 | git `commands[]` `git.c:506` | 多命令 CLI/REPL |
| 10 | iterator / 火山模型 | 節點 `next()` 吐一個或 NULL，上向下 pull | PG `ExecProcNode` `executor.h:269` | query engine/串流 |
| 11 | pager / buffer pool | `getPage(pgno)`、pin/dirty/eviction | SQLite `pager.c:2458`、PG buffer mgr | 儲存引擎/page cache |
| 12 | macro dispatch switching | `vmcase`/`TARGET` 重定義切 switch↔goto | Lua `ljumptab.h`、CPython `ceval_macros.h:73` | 一份 code 兩種取捨（+ 陷阱警覺）|

## 底層機制：pattern 之間怎麼互相召喚

字典的隱藏價值不在單張卡片，在**卡片之間的連結**。認出一個 pattern，常常就該去找它的「配套 pattern」。畫出這張召喚圖：

```
   認出 pattern 3（boxed+refcount）
        └──► 必問「循環怎麼收？」──► 去找 pattern 8（cyclic GC）

   認出 pattern 1（bytecode VM）
        └──► 必問「值怎麼存？」────► 去找 pattern 2 或 3（值表示）
        └──► 必問「怎麼 dispatch？」► 留意 pattern 12（macro 偽裝）

   認出 pattern 5（event loop）
        └──► 常配 ────────────────► pattern 4（per-conn pool）
        └──► 常配 ────────────────► pattern 7（handler pipeline）

   認出 pattern 11（pager）
        └──► 上面常建 ────────────► B-tree / heap（page 當節點）

   認出 pattern 10（火山模型）
        └──► 每節點是 ────────────► pattern 7（handler=函式指標）的變體
```

**這張圖是進階讀碼者和新手的分野。** 新手認出一個 pattern 就停了；老手認出一個 pattern，立刻預期它的鄰居在哪、該去確認什麼。攻堅速度的複利在此：你不是認一個 pattern，是啟動一整組先驗知識和「接下來去哪找」的導航。

## 攻堅劇本：beacon 命中後 30 秒的快速驗證動作

字典要能在攻堅時真的加速，每張卡得配一組「命中後立刻 rg 什麼來確認/填細節」的動作。把最常用的幾個做成劇本——攻堅時對照著跑：

| 疑似 pattern | 命中後 30 秒該做 | 確認/填的細節 |
|---|---|---|
| bytecode VM（1）| `rg "for\s*\(;;\)|while\s*\(1\)" 找主迴圈；`rg "switch.*op|goto \*"` 看 dispatch | register/stack？（看 `OP_ADD` 帶不帶位置 operand）；switch/goto？（Ch 27 三欄）|
| tagged union（2）| `rg "union|enum.*(kind|type|tag)"` 找值的定義 | tag 欄叫什麼？哪些值內嵌、哪些指向堆上？|
| refcount（3）| `rg -i "incref|decref|refcnt|retain|release"` | INCREF/DECREF 配對嗎？→ 接著找循環 GC（8）|
| arena/pool（4）| `rg "create_pool|arena|_palloc|MemoryContext"`；找**沒有配對 free** 的 alloc | 整批釋放點在哪？生命週期綁哪個作用域？|
| event loop（5）| `rg "epoll_wait|kqueue|poll\(|for\s*\(;;\)"` 找主迴圈 | handler 怎麼註冊？連線狀態機在哪？→ 常配 4、7 |
| content addressing（6）| `rg -i "sha1|sha256|object_id|oid|hash.*object"` | 名字=內容雜湊？物件不可變？→ DAG 怎麼連 |
| handler pipeline（7）| `rg -i "phase|stage|filter|handler.*\[\]|middleware"` | phase 列表在哪？handler 怎麼串/註冊？|
| command table（9）| `rg "struct.*\{.*char.*name.*\}|commands\[\]"` 找 `{name, fn}[]` | 查表函式是誰？加子命令改哪？|
| iterator/火山（10）| `rg -i "next\(|ExecProcNode|getNext|__next__"` | 誰 pull 誰？streaming 還是 blocking 節點？|
| pager/buffer（11）| `rg -i "getpage|pin|dirty|evict|buffer.*pool|page.*cache"` | page 大小？eviction 策略？上面建了什麼結構？|

**這張劇本把字典從「知識」升級成「可執行動作」。** 攻堅時你不是「想起 pattern 3 的定義」，是「疑似 refcount → 手指自動 `rg incref` → 30 秒確認 → 順手找循環 GC」。動作化之後才真的快。**你該給自己的每張卡都補一行這種「命中後動作」。**

## 對比與取捨：字典是加速器，不是替代品

| 用法 | 好 | 壞 |
|---|---|---|
| 對照 beacon 快速歸類眼前 code | ✓ 從逐行讀變成「認出+驗證」 | |
| 把「可遷移到」欄當先驗，導航下一步 | ✓ 知道去哪找配套機制 | |
| **憑 beacon 就斷定，不驗細節** | | ✗ pattern 只是假設，得回 source 確認變體 |
| **看到像的就硬套 pattern** | | ✗ 有些 code 是四不像/反模式，強套會誤讀 |

**字典給你的是假設，不是結論。** beacon 命中 → 你有一個「這大概是 X pattern」的高品質假設 → 回 source 驗證它的具體變體（register 還是 stack？哪種 GC？pool 的生命週期綁哪個作用域？）。**永遠是「pattern 導航 + source 驗證」雙管**，不是「認出就跳過」。這正是全課反覆講的：pattern 讓你快，但不能取代讀 code 本身。

## 踩雷集錦

1. **把 beacon 當結論而非假設。** 看到 `for(;;) switch(opcode)` 就斷定「是 VM」然後跳過——但它可能是 register VM 也可能是 stack VM、可能 computed goto 也可能純 switch。beacon 命中只是假設成立，具體個性還得回 source 填三欄（Ch 27 模板）。

2. **硬把相似 code 套進最近的 pattern。** 有些 code 是多個 pattern 的混合，或根本是反模式。強行歸類會讓你帶著錯誤先驗去讀，越讀越歪。認不出來就老實逐行讀——字典是加速器，不是萬能分類器。

3. **只記單張卡、不記卡片間的召喚關係。** 認出 refcount 卻不知道去找循環 GC，認出 event loop 卻不預期 per-conn pool——你只用了字典一半的價值。pattern 的鄰居關係（上面那張召喚圖）才是老手的導航系統。

4. **以為 pattern 庫是背出來的。** 這十二張卡你現在「看得懂」，但要變成「一眼認出」得靠**反覆在真 code 上撞見它們**（Ch 31 的持續訓練）。背卡片沒用，得在新 codebase 裡親手認出「啊這又是 arena allocator」才會進長期記憶。

5. **把這張表當成「pattern 的全集」。** 這只是六個 codebase 萃取的十二個。還有海量 pattern 沒收（actor model、CoW、WAL、lock-free、SSA…）。字典的用法是**持續擴充**——每攻一個新 codebase 就加幾張卡，不是把這十二張當終點。

## 進階：再往深一層

- **給每張卡加「反例/變體」欄**：同一個 pattern 在不同 codebase 有變體（Lua 的 tagged union 瘦、SQLite 的肥）。記下變體讓你的假設更精準。進階版字典每張卡有「典型形」+「你見過的變體」。
- **把召喚圖擴成完整的 pattern 關係網**：哪些 pattern 常共存（event loop + pool + pipeline）、哪些互斥（tagged union vs boxed object 是兩種世界觀）、哪些是彼此的配套（refcount ↔ cyclic GC）。這張網越密，你的導航越強。
- **建立「beacon → 快速驗證」的 checklist**：每個 pattern 配一組「命中後 30 秒內該 rg 什麼來確認」的動作。例如認出疑似 pager → `rg "getPage|pin|dirty|evict"` 秒驗。把字典從「知識」升級成「可執行的攻堅劇本」。

## 本章重點整理

- **這張字典是全課的畢業證書**：你帶走的不是六個專案的知識，是十二個「一眼認出」的 chunk + 它們的 beacon、佐證、遷移範圍。
- **每張卡三欄對應攻堅三問**：beacon（怎麼認出）、在哪見過（真檔案佐證，出現越多越可信）、可遷移到（認出後知道什麼）。
- **pattern 之間會互相召喚**：認出 refcount → 找循環 GC；認出 VM → 問值表示 + dispatch；認出 event loop → 預期 pool + pipeline。這張召喚網是老手的導航系統。
- **字典給假設不給結論**：beacon 命中 → 高品質假設 → 回 source 驗證具體變體。永遠「pattern 導航 + source 驗證」雙管。
- **pattern 庫靠撞見而非背誦擴充**：這十二張是起點不是終點，每攻一個新 codebase 就加幾張卡。

## 自我檢核

- [ ] 我能不看表，說出至少八個 pattern 的 beacon（一眼認出的信號）
- [ ] 我能對每個 pattern 舉出至少一個本課的真檔案佐證（而不只是名字）
- [ ] 我能畫出 pattern 召喚圖的至少三條邊（如 refcount → cyclic GC）
- [ ] 我理解 beacon 命中只是假設，會回 source 驗證具體變體，不憑 beacon 跳過
- [ ] 我知道這十二張卡是起點，並打算每攻一個新 codebase 就往字典加卡

## 延伸閱讀

- **《The Programmer's Brain》** — Felienne Hermans（Manning, 2021）第 1–4 章
  - **讀哪裡**：chunking、beacon、long-term memory 的認知科學。這章的「beacon」欄直接來自這本書；讀完你會懂為什麼「在真 code 撞見」比「背卡片」更能建立 chunk
  - **前提**：無
- **《Design Patterns: Elements of Reusable Object-Oriented Software》**（GoF, 1994）
  - **讀哪裡**：不是逐章讀，是當「pattern 命名 + 意圖」的參考。本課的 pattern 多半在系統/C 層（GoF 偏 OO），但「命名一個結構讓你能 chunk 它」的精神相同；對照看能擴充你的字典
  - **前提**：無
- **《The Architecture of Open Source Applications》/ 500 Lines**（[aosabook.org](https://aosabook.org/)）
  - **讀哪裡**：找本課六個目標或相近系統的專章（如 web server、VM、資料庫）。頂尖工程師親口說「我用了哪個 pattern、為什麼」——是你字典每張卡的權威補充與變體來源
  - **前提**：無
- **本課六個萃取章**（Ch 7、12、17、21、26）
  - **讀哪裡**：回頭重讀。這章是它們的索引；帶著「召喚網」的視角重讀，你會看到單章沒點破的 pattern 連結
  - **前提**：讀過對應 Part

你手上有了一張 pattern 字典——這門課的畢業證書。但字典會過期：不用就忘、不擴充就停滯。最後一章，我們談畢業之後怎麼讓這個 pattern 庫**繼續長大**，把「限時攻堅陌生 codebase」變成一輩子的訓練習慣。

→ [Ch 31 打造持續讀碼的訓練習慣](./31-sustained-reading-practice.md)
