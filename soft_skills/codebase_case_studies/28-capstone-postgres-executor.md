# Ch 28 — Capstone：冷讀 PostgreSQL executor

> **目標**：第一次面對一個你**完全沒背景**的大型 C 專案（PostgreSQL，百萬行級），用 `reading_code` 的 SOP 從外圍偵察一路收斂到 executor，讀懂它的「火山模型」：一棵 PlanState 節點樹、每次 `ExecProcNode` 吐一個 tuple 的 iterator 模型。並和你 Part 2 讀過的 SQLite VDBE 對照——同樣是「執行一個 query」，兩種截然不同的架構。

> **目標codebase**：PostgreSQL `REL_17_2`（`6304632`）。executor 在 `src/backend/executor/`。這是全課最大的目標，也是畢業硬指標。

## 為什麼需要這個？

前面六個 Part 你讀的 codebase，都有一個共同點：**你多少有點背景或前面章節鋪過。** Lua 你在 compiler 課群碰過語言 runtime、SQLite 官方架構文件先看過、CPython 你天天用。PostgreSQL 不一樣——它百萬行、模組多到列不完、術語自成一國（tuple、slot、TupleTableSlot、EState、ExprContext、planstate…），而且這章之前我們**刻意沒有給你任何 PostgreSQL 的鋪墊**。

這就是重點。**冷讀（cold read）一個零背景的大專案，是這門課要證明你會的終極技能。** 你職涯裡最痛的時刻——onboarding 一個五百萬行的 legacy 系統、審一個沒看過的大依賴有沒有洞、接手一個離職團隊的系統——全是冷讀。這一章我們示範：面對這種龐然大物，**不慌，用 SOP 把它收斂到你真正要讀的兩百行。**

我們的收斂目標：**「一個 SELECT 怎麼被跑出來？」** 這條路徑會帶你穿過 executor 的核心——火山模型。

## 先建立直覺：不要試圖讀懂 PostgreSQL

先講一件反直覺的事。**你不可能讀懂整個 PostgreSQL，也不需要。** 它有 parser、rewriter、planner/optimizer、executor、storage（heap/index/TOAST）、WAL、buffer manager、MVCC、replication、background workers…… 每一塊都是一個人可以研究好幾年的題目。

冷讀的第一紀律（`reading_code` Ch 11「收斂到你要的 200 行」）：**先劃出你要攻的那一小塊，其餘全部標記為「已知存在、刻意不讀」。** 我們只攻 executor，而且只攻 executor 裡「怎麼跑出一個 SELECT」這一條路徑。

```
   PostgreSQL 的處理管線（一個 SELECT 的一生）：

   SQL text
     │  parser        ← 不讀（Part 2 SQLite 已練過 tokenize/parse 概念）
     ▼
   parse tree
     │  rewriter       ← 不讀
     ▼
   query tree
     │  planner        ← 不讀（optimizer 是另一個宇宙）
     ▼
   Plan tree           ← 一棵「計畫節點」樹（SeqScan、Agg、HashJoin…）
     │  ★ executor ★   ← 【我們只攻這裡】
     ▼
   tuples out
```

executor 拿到 planner 產好的 **Plan tree**，把它「跑起來」，一列一列吐出結果。我們要搞懂的就是這個「跑」的機制。

## 偵察：60 分鐘從外圍摸到 executor

照 `reading_code` Ch 5 的偵察 SOP，我們不打開任何檔案內容，先用目錄結構和檔名建立地圖。

**步驟 1：規模體檢與目錄。**

```bash
$ cd ~/cbcs/postgres
$ ls src/backend/
access/  bootstrap/  catalog/  commands/  executor/  jit/  lib/
libpq/   main/  nodes/  optimizer/  parser/  partitioning/  po/
port/  postmaster/  regex/  replication/  rewrite/  snowball/
statistics/  storage/  tcop/  tsearch/  utils/
```

一眼掃過去，`executor/`、`optimizer/`、`parser/`、`storage/` 這幾個名字直接對應上面那條管線。**這是冷讀的好消息：PostgreSQL 的目錄名誠實。** 你不用讀 code 就知道 executor 在 `src/backend/executor/`。

**步驟 2：進 executor 目錄，用檔名建立子地圖。**

```bash
$ ls src/backend/executor/ | head -30
execAmi.c        execAsync.c      execCurrent.c    execdebug.h
execExpr.c       execExprInterp.c execGrouping.c   execIndexing.c
execJunk.c       execMain.c       execParallel.c   execPartition.c
execProcnode.c   execReplication.c execScan.c      execSRF.c
execTuples.c     execUtils.c      functions.c      nodeAgg.c
nodeAppend.c     nodeBitmapAnd.c  nodeBitmapHeapscan.c ...
nodeSeqscan.c    nodeHashjoin.c   nodeIndexscan.c  nodeSort.c ...
```

檔名有兩類：`exec*.c`（框架/骨幹）和 `node*.c`（每種 plan 節點的實作）。這個命名慣例**自己就是地圖**：

- `execMain.c` — 名字帶 Main，大概率是入口/主流程。
- `execProcnode.c` — Procnode = process node，聽起來是「處理一個節點」的分派中樞。
- `node<X>.c` — 一種 plan 節點一個檔。`nodeSeqscan.c`=循序掃描、`nodeAgg.c`=聚合、`nodeHashjoin.c`=雜湊 join。

**冷讀技巧：檔名的動詞/名詞就是你的初版 call graph。** 我們押注「入口在 `execMain.c`，分派在 `execProcnode.c`，具體幹活在各 `node*.c`」——押注要用讀 code 驗證，但這個假設讓我們知道先開哪三個檔。

**步驟 3：`rg` 找入口，驗證假設。**

```bash
$ rg -n "standard_ExecutorRun|ExecutePlan" src/backend/executor/execMain.c
310:standard_ExecutorRun(QueryDesc *queryDesc, ...
365:		ExecutePlan(estate, ...
1609:ExecutePlan(EState *estate, PlanState *planstate, ...
```

有了。`standard_ExecutorRun` → `ExecutePlan` 是主流程。假設成立。60 分鐘的偵察到此，我們的地圖是：

```
   標準流程：ExecutorStart（建 state）→ ExecutorRun（跑）→ ExecutorEnd（收）
                                            │
                        standard_ExecutorRun (execMain.c:310)
                                            │
                                  ExecutePlan (execMain.c:1609)
                                            │
                              ┌─────────────┴─────────────┐
                              │  火山模型主迴圈：反覆呼叫  │
                              │  ExecProcNode(planstate)   │
                              └───────────────────────────┘
```

## 核心一：火山模型的主迴圈

打開 `execMain.c` 的 `ExecutePlan`，直奔它的主迴圈（`execMain.c:1646`）：

```c
/* src/backend/executor/execMain.c:1646 (REL_17_2) */
	for (;;)
	{
		/* Reset the per-output-tuple exprcontext */
		ResetPerTupleExprContext(estate);

		/*
		 * Execute the plan and obtain a tuple
		 */
		slot = ExecProcNode(planstate);

		/*
		 * if the tuple is null, then we assume there is nothing more to
		 * process so we just end the loop...
		 */
		if (TupIsNull(slot))
			break;
		...
		if (sendTuples)
		{
			if (!dest->receiveSlot(slot, dest))
				break;
		}
		...
	}
```

**這就是火山模型（Volcano / iterator model）的心臟，讀一次記一輩子：**

1. 呼叫 `ExecProcNode(planstate)` 向「計畫樹的根節點」要**一個** tuple（裝在 `slot` 裡）。
2. 拿到就送出去（`dest->receiveSlot`——傳給 client）。
3. 拿到 `NULL`（`TupIsNull`）代表沒了，結束。
4. 回圈，要下一個。

**關鍵洞察：`ExecutePlan` 不知道底下的 plan 有多複雜。** 不管底下是三層 join、兩層 aggregate 還是單純掃一張表，它只做一件事：反覆跟根節點要一個 tuple，直到 NULL。複雜度全被封裝在 `ExecProcNode` 背後。**這種「跟根要一個、拉一個、複雜度封裝在下面」的模型，就叫火山模型/pull-based iterator。**

## 核心二：ExecProcNode 是什麼？（被騙一次的地方）

你會想：`ExecProcNode` 一定是個大 switch，按節點種類分派吧（就像 SQLite VDBE 按 opcode 分派）？**去讀一下，你會被騙一次。**

```c
/* src/include/executor/executor.h:269 (REL_17_2) */
static inline TupleTableSlot *
ExecProcNode(PlanState *node)
{
	if (node->chgParam != NULL) /* something changed? */
		ExecReScan(node);		/* let ReScan handle this */

	return node->ExecProcNode(node);
}
```

**它不是 switch。它是 `node->ExecProcNode(node)`——一個函式指標呼叫。** 每個 PlanState 節點自己帶著一個 `ExecProcNode` 函式指標，指向「這種節點怎麼吐下一個 tuple」的實作。這是 **C 的手工 vtable / 虛擬分派**：不同節點（SeqScan、Agg、HashJoin）在初始化時把各自的處理函式塞進這個欄位。

看 `PlanState` struct 就懂了（`src/include/nodes/execnodes.h:1115`）：

```c
/* src/include/nodes/execnodes.h:1115 (REL_17_2) —— 節選 */
typedef struct PlanState
{
	pg_node_attr(abstract)
	NodeTag		type;
	Plan	   *plan;			/* associated Plan node */
	EState	   *state;			/* ... one EState for the whole ... */
	ExecProcNodeMtd ExecProcNode;	/* function to return next tuple */
	ExecProcNodeMtd ExecProcNodeReal;	/* actual function ... */
	...
	struct PlanState *lefttree; /* input plan tree(s) */
	struct PlanState *righttree;
	...
} PlanState;
```

兩個欄位是理解整個 executor 的鑰匙：

- **`ExecProcNode`（函式指標）**：「叫我吐下一個 tuple」的方法。這是節點的「拉」介面。
- **`lefttree` / `righttree`**：子節點。**PlanState 是一棵樹**，每個節點有 0、1 或 2 個子節點（scan 沒有子、agg 有一個 outer 子、join 有 left+right 兩個子）。

所以真正的心智模型是這樣：

```
   PlanState 是一棵樹，每個節點自帶「吐一個 tuple」的函式指標。
   火山模型 = 從根節點 pull，根節點再向它的子節點 pull，一路遞迴向下。

   範例：SELECT count(*) FROM t WHERE x > 10 的 Plan tree

         Aggregate          ← ExecProcNode = ExecAgg
             │                 「給我一個聚合結果」
             │ lefttree
             ▼
         SeqScan (t, x>10)  ← ExecProcNode = ExecSeqScan
                             「給我下一個符合 x>10 的 tuple」

   ExecutePlan 跟 Aggregate 要一個 tuple
     → ExecAgg 為了算 count，反覆跟 SeqScan 要 tuple（子節點 pull）
       → ExecSeqScan 每次從 heap 拉一列、過濾 x>10、吐一個
     → 子節點吐完（NULL），ExecAgg 把累計的 count 包成一個 tuple 吐出
```

**「一開始你以為 ExecProcNode 是 switch，讀下去發現是函式指標樹」——這正是 `reading_code` 一再強調的：indirection 會騙你。** 函式指標分派讓你在 `execProcnode.c` 裡找不到「SeqScan 的邏輯」——它在 `nodeSeqscan.c`。要跟著函式指標跳，不是跟著 switch。

## 核心三：那 switch 在哪？在初始化，不在執行

那個你以為的大 switch 確實存在——但它在**建樹**的時候跑，不在**執行**的時候跑。`ExecInitNode`（`execProcnode.c:142`）：

```c
/* src/backend/executor/execProcnode.c —— 節選 (REL_17_2) */
	switch (nodeTag(node))
	{
		case T_Result:
			result = (PlanState *) ExecInitResult((Result *) node, estate, eflags);
			break;
		...
		case T_SeqScan:
			result = (PlanState *) ExecInitSeqScan((SeqScan *) node, estate, eflags);
			break;
		...
		case T_Agg:
			result = (PlanState *) ExecInitAgg((Agg *) node, estate, eflags);
			break;
		...
	}
```

**這個 switch 每種節點只跑一次**（初始化時），把 Plan 節點轉成 PlanState 節點，順便把對的函式指標塞進 `ExecProcNode` 欄位。看 `ExecInitSeqScan` 會發現它做 `ExecInitScanTupleSlot` 之類的準備，並透過 `ExecInitScan`/`ExecAssignScanProjectionInfo` 把節點的 `ExecProcNode` 設成 `ExecSeqScan`。

**這是一個關鍵的架構取捨，也是和 SQLite VDBE 最大的分野：**

- **VDBE**：把整個 query「攤平」成一串線性 bytecode，執行時一條一條跑，dispatch 在**每條指令**都發生（switch on opcode，每個 tuple 跑很多次）。
- **PostgreSQL executor**：把 query 表達成一棵**樹**，dispatch（選對函式）在**建樹時一次搞定**，執行時是節點間的函式指標呼叫（每個 tuple 遞迴穿過樹一次）。

一個是「線性指令流 + 每步 dispatch」，一個是「節點樹 + 一次 dispatch 定型 + pull 遍歷」。**這是兩種 query 執行模型的根本差異，記住它，這是這章最值錢的一句。**

## 底層機制：一個 SeqScan 節點怎麼吐 tuple

跟著函式指標往下鑽一層。`SeqScan` 節點的 `ExecProcNode` 指向 `ExecSeqScan`（`nodeSeqscan.c:108`）：

```c
/* src/backend/executor/nodeSeqscan.c:108 (REL_17_2) */
static TupleTableSlot *
ExecSeqScan(PlanState *pstate)
{
	SeqScanState *node = castNode(SeqScanState, pstate);

	return ExecScan(&node->ss,
					(ExecScanAccessMtd) SeqNext,
					(ExecScanRecheckMtd) SeqRecheck);
}
```

**又一層函式指標！** `ExecSeqScan` 幾乎什麼都不做——把工作丟給共用的 `ExecScan`，並傳入兩個回呼：`SeqNext`（怎麼拿下一列）和 `SeqRecheck`（怎麼重驗）。這是 executor 的另一個 pattern：**掃描的骨架（qual 過濾、投影）共用在 `ExecScan`，只有「怎麼拿下一列」因存取方法而異，用回呼注入。** IndexScan、TidScan 也都走同一個 `ExecScan`，只是傳不同的 `*Next` 回呼。

`ExecScan` 的核心迴圈（`execScan.c:193`）：

```c
/* src/backend/executor/execScan.c:193 (REL_17_2) —— 節選 */
	for (;;)
	{
		TupleTableSlot *slot;

		slot = ExecScanFetch(node, accessMtd, recheckMtd);  /* 呼叫 SeqNext */

		if (TupIsNull(slot))       /* 沒了 → 回 NULL 給上層 */
		{
			...
			return slot;
		}

		econtext->ecxt_scantuple = slot;
		/* check that the current tuple satisfies the qual-clause */
		if (qual == NULL || ExecQual(qual, econtext))
			...return（過關的 tuple，做投影後回傳）
		/* 沒過 qual → 迴圈，抓下一個 */
	}
```

一路到底，`SeqNext` 真正跟 storage 要資料（`nodeSeqscan.c:50`）：

```c
/* src/backend/executor/nodeSeqscan.c:50 (REL_17_2) —— 節選 */
SeqNext(SeqScanState *node)
{
	...
	if (scandesc == NULL)
	{
		scandesc = table_beginscan(node->ss.ss_currentRelation, ...);
		node->ss.ss_currentScanDesc = scandesc;
	}
	/* get the next tuple from the table */
	if (table_scan_getnextslot(scandesc, direction, slot))
		return slot;
	return NULL;
}
```

`table_scan_getnextslot` 是 storage 層（table access method）的介面——**這裡就是 executor 和 storage 的邊界。** 我們的攻堅到此打住：再往下是 heap access method、buffer manager、MVCC 可見性判斷，屬於 storage 那個宇宙，這條路徑上我們刻意不讀（標記「已知存在」）。

完整的 pull chain：

```
   ExecutePlan (execMain.c:1646)
     └─ ExecProcNode(root)                    [執行時函式指標]
         └─ ExecAgg (nodeAgg.c:2158)          [Agg 節點]
             └─ ExecProcNode(outer)           [向子節點 pull]
                 └─ ExecSeqScan (nodeSeqscan.c:108)
                     └─ ExecScan (execScan.c:193)   [共用掃描骨架]
                         └─ SeqNext (nodeSeqscan.c:50)
                             └─ table_scan_getnextslot(...)  [storage 邊界，止步]
```

## 核心四：Aggregate 節點——pull 模型下怎麼「吃完才吐」

SeqScan 是「拉一個吐一個」，但 Aggregate（`count(*)`）不一樣——它得**先把子節點所有 tuple 吃完，才能吐出一個結果**。看火山模型怎麼優雅處理這種「阻塞式」節點（`nodeAgg.c:2158`）：

```c
/* src/backend/executor/nodeAgg.c:2158 (REL_17_2) —— 節選 */
ExecAgg(PlanState *pstate)
{
	AggState   *node = castNode(AggState, pstate);
	TupleTableSlot *result = NULL;
	...
	if (!node->agg_done)
	{
		switch (node->phase->aggstrategy)
		{
			case AGG_HASHED:  ...
			case AGG_PLAIN:
			case AGG_SORTED:
				result = agg_retrieve_direct(node);  /* 這裡面 pull 子節點 */
				break;
		}
		if (!TupIsNull(result))
			return result;
	}
	return NULL;
}
```

`agg_retrieve_direct`（`nodeAgg.c:2194`）內部呼叫 `fetch_input_tuple`（`nodeAgg.c:547`），後者對子節點做 `ExecProcNode(outerPlanState(aggstate))`——**Agg 在自己的邏輯裡把子節點的 tuple 一個一個 pull 進來累計**，全部吃完才把聚合結果包成一個 slot 回傳。

**這揭示火山模型一個漂亮的性質：pull 介面統一，但每個節點內部可以是 streaming（SeqScan：拉一個吐一個）或 blocking（Agg/Sort：吃完才吐）。** 上層完全不用管——它只是 `ExecProcNode` 要下一個。這種「統一介面、各自實作」正是這個 pattern 可組合的原因：任意節點可以插在任意節點下面，因為它們都遵守「被 pull 就吐一個或 NULL」的契約。

## 對比與取捨：VDBE vs 火山模型（重要對照）

這是本章的核心對照。同樣是「執行一個 query」，SQLite 和 PostgreSQL 選了兩條路：

| 面向 | **SQLite VDBE**（Part 2 Ch 9）| **PostgreSQL executor**（本章）|
|---|---|---|
| query 表達成 | 一串線性 bytecode（`VdbeOp` 陣列）| 一棵 PlanState 節點樹 |
| 執行機制 | `for(pOp=...)` 走指令 + `switch(opcode)` | pull：`ExecProcNode` 遞迴穿樹 |
| dispatch 時機 | 每條指令都 dispatch（每 tuple 多次）| 建樹時一次定型（函式指標）|
| dispatch 方式 | switch on opcode | 函式指標（手工 vtable）|
| 一個 tuple 的成本 | 跑一段 bytecode | 遞迴穿過樹一次 |
| 值/tuple 載體 | `Mem` cell 陣列 | `TupleTableSlot` |
| 適合的規模 | 嵌入式、單機、小到中查詢 | 大型、複雜計畫、並行執行 |
| 為什麼這樣選 | 緊湊、可攜、可 EXPLAIN 成線性 | 可組合、易加新節點型別、支援並行 |
| 心智模型 | 「編譯成組合語言再跑」| 「組一台由零件（節點）搭的機器再開動」|

**兩種模型沒有優劣，是尺度取捨。** SQLite 是嵌入式、要小要可攜，把 query 攤平成 bytecode 最省；PostgreSQL 要處理任意複雜的計畫、要能並行、要方便加新的節點型別（新 join 演算法、新掃描方式），節點樹 + 函式指標分派最好擴充——加一種節點只要寫一個 `node*.c` + 在 `ExecInitNode` 加一個 case。**你讀 code 時的判斷：看到「線性指令 + switch on opcode」是 bytecode VM 派；看到「節點樹 + 每節點自帶處理函式指標 + pull」是火山模型派。** 一眼歸類。

## 踩雷集錦

1. **想把整個 PostgreSQL 讀懂。** 死路。它百萬行、十幾個子系統，每個都能研究好幾年。冷讀的第一步是**劃界**：只攻 executor 的一條路徑，其餘標「已知存在、不讀」。不劃界必崩潰——這正是 `reading_code` Ch 11 收斂技巧的實戰。

2. **以為 `ExecProcNode` 是 switch，在 `execProcnode.c` 裡找節點邏輯。** 它是 `node->ExecProcNode(node)` 函式指標。SeqScan 的邏輯在 `nodeSeqscan.c`，不在 `execProcnode.c`。要跟著函式指標跳（LSP 的「跳到 `ExecSeqScan`」，或 `rg` `node->ExecProcNode =` 找誰設定它）。被 indirection 騙一次很正常。

3. **以為 switch（`ExecInitNode`）在執行時每 tuple 跑。** 不。那個 switch 在**建樹/初始化**時每種節點跑一次，把函式指標定好。執行時完全不碰它——執行是純函式指標遍歷。分清「init 時期」和「run 時期」是讀 executor 的關鍵。

4. **以為 Aggregate 也是拉一個吐一個。** Agg/Sort 是阻塞式：先把子節點吃光才吐第一個結果。火山模型的美就在於：blocking 和 streaming 節點共用同一個 pull 介面，上層無感。搞混會讓你以為 `count(*)` 是邊掃邊出——其實是掃完才出。

5. **在 `ExecScan` 裡找不到「怎麼讀 heap」而困惑。** `ExecScan` 只管骨架（qual、投影），「怎麼拿下一列」是回呼（`SeqNext`）注入的，真正碰 storage 在 `table_scan_getnextslot`——那是 executor/storage 的邊界，是另一個子系統。認出邊界、在邊界止步，是冷讀不迷路的關鍵。

## 進階：再往深一層

- **並行執行（parallel query）**：`nodeGather.c`、`execParallel.c`。火山模型怎麼擴展到多 worker？Gather 節點在樹上收集多個 worker 的 tuple。這是節點樹模型比 VDBE 更容易擴展的鐵證——加並行只是加一種節點。
- **表達式求值（expression evaluation）**：`WHERE x > 10` 的 `x > 10` 怎麼算？看 `execExpr.c` + `execExprInterp.c`。有趣的是——**PostgreSQL 的表達式求值自己又是一個小 bytecode VM**（`ExprState` 編譯成 `ExprEvalStep` 步驟，`ExecInterpExpr` 用 computed goto 分派）。所以 PG 內部同時有「節點樹火山模型」和「表達式 bytecode VM」兩種執行模型並存——你這章學的兩個 pattern 在同一個專案裡都能看到。
- **JIT**：`src/backend/jit/` 用 LLVM 把熱路徑（表達式求值、tuple deforming）編成機器碼。你的 compiler 課群直覺在這裡用得上。

## 本章重點整理

- **冷讀大專案的第一紀律是劃界**：只攻 executor 的一條路徑（「一個 SELECT 怎麼跑出來」），其餘標「已知存在、不讀」。
- **偵察靠誠實的目錄名與檔名慣例**：`exec*.c`（框架）vs `node*.c`（一種節點一檔），檔名的動詞/名詞就是初版 call graph。
- **火山模型 = PlanState 節點樹 + pull 介面**：`ExecutePlan` 反覆 `ExecProcNode(根)` 要一個 tuple；每個節點自帶函式指標，向子節點遞迴 pull。
- **dispatch 在建樹時一次定型（`ExecInitNode` 的 switch 設好函式指標），執行時是純函式指標遍歷**——不要在執行路徑找 switch。
- **和 SQLite VDBE 的根本對照**：VDBE 是「線性 bytecode + 每步 switch dispatch」，PG executor 是「節點樹 + 一次定型 + pull 遍歷」。兩種 query 執行模型，尺度取捨不同。
- **統一 pull 介面讓 streaming（SeqScan）和 blocking（Agg）節點自由組合**——這是火山模型可組合、易擴充的根源。

## 自我檢核

- [ ] 我能不看教材，畫出 `ExecutePlan → ExecProcNode → ExecSeqScan → ExecScan → SeqNext` 的 pull chain
- [ ] 我能解釋為什麼 `ExecProcNode` 是函式指標而不是 switch，以及「真的 switch」在哪（`ExecInitNode`）、何時跑（建樹時）
- [ ] 我能說清楚火山模型和 SQLite VDBE 的根本差異（節點樹 pull vs 線性 bytecode dispatch）
- [ ] 我能解釋為什麼 Agg 節點能在同一個 pull 介面下「吃完才吐」而上層無感
- [ ] 我能指出這條路徑上哪裡是「executor/storage 邊界」（`table_scan_getnextslot`），並說明為什麼在那止步

## 延伸閱讀

- **PostgreSQL 官方原始碼 README**：`src/backend/executor/README`
  - **讀哪裡**：整份。這是 PG 核心開發者寫給讀 executor 的人的自述，講清楚 PlanState 樹、ExecInitNode/ExecProcNode/ExecEndNode 三階段、ExprContext 的角色。**冷讀任何大專案，先找子系統目錄裡的 README——PostgreSQL 幾乎每個目錄都有一份。**
  - **前提**：讀過本章，帶著問題去對照
- **《The Design and Implementation of Modern Column-Oriented Database Systems》/ Graefe「Volcano」原始論文**
  - **讀哪裡**：Volcano iterator model 的原始定義（Graefe 1994）；理解「open/next/close」三方法的火山模型從哪來，PG 的 Init/Proc/End 正是它的變體
  - **前提**：讀過本章的 pull chain
- **[PostgreSQL 官方文件 — Overview of PostgreSQL Internals](https://www.postgresql.org/docs/17/overview.html)**
  - **讀哪裡**：「The Path of a Query」一節；把本章劃界外的 parser/rewriter/planner 補一個高層概念，理解 executor 拿到的 Plan tree 是誰產的
  - **前提**：無，這是高層導覽
- **`reading_code` Ch 11「收斂到你要改的 200 行」**（本 repo）
  - **讀哪裡**：整章。本章的「劃界」就是它的實戰；讀完回頭對照，你會更懂為什麼冷讀大專案第一動作是收斂而非展開
  - **前提**：無

你已經冷讀了 PostgreSQL 的 executor——一個零背景的百萬行專案，被你收斂到一條 pull chain 讀懂了。下一章我們把整個過程**慢動作重播一次**，示範一場完整的限時冷啟動攻堅：全程外化（畫圖、記假設、費曼複述），把前面所有技巧串成一套可複製的動作。

→ [Ch 29 Capstone 攻堅實況：限時、外化、費曼](./29-capstone-attack-live.md)
