# Ch 29 — Capstone 攻堅實況：限時、外化、費曼

> **目標**：把 Ch 28 讀懂的東西，用一場**慢動作重播的限時攻堅**串起來。示範一次完整、有碼表、全程外化的冷啟動——追一條真實路徑（一個 `SELECT count(*) FROM t WHERE x > 10` 怎麼被 executor 跑出來），從偵察→假設→data flow→收斂，每一步都畫圖、記假設、費曼複述。這章是全課方法論的**活體示範**：不是教新招，是把所有招式在你眼前打一遍。

> **目標codebase**：PostgreSQL `REL_17_2`（`6304632`）。

## 為什麼需要這個？

Ch 28 給了你結論（火山模型、PlanState 樹、pull chain）。但結論不是重點——**得到結論的過程才是你要學的技能。** 真實攻堅裡沒有人先告訴你答案；你面對的是一堆檔案和一個問題，得靠一套動作把自己從「看不懂」推到「懂了」。

這一章我把那套動作**慢動作**演給你看。我會刻意暴露：哪裡我先猜錯、哪裡 indirection 把我騙了、哪裡我停下來畫圖、哪裡我對著空氣把剛讀懂的東西講一遍確認自己沒騙自己。**這些「外化」動作——寫下來、畫出來、講出來——不是花招，是把讀碼從「腦內模糊」變成「紙上可檢驗」的核心技術**（`reading_code` Ch 35 reading journal、Ch 36 費曼測試）。

規則：**碼表 90 分鐘**，一條路徑，全程外化。開始。

## 攻堅任務界定（00:00–00:05）

第一個動作永遠是**把任務講清楚**。含糊的目標會讓你到處亂讀。

我寫在筆記本第一行（真的寫下來）：

```
【任務】SELECT count(*) FROM t WHERE x > 10 這個 query，
        executor 是怎麼把它「跑出來」的？
【範圍】只追 executor（src/backend/executor/）。
        parser/planner/storage 一律標「已知存在、不讀」。
【止步線】追到 executor 呼叫 storage 的那一刻（table access method 邊界）就停。
【交付】一張 pull chain 圖 + 一段能講給人聽的費曼摘要。
【碼表】90 分鐘。
```

**為什麼先寫這個？** 因為 PostgreSQL 大到你隨時會被 rabbit hole 吸走（「欸這個 MVCC 好像很有趣」）。這行字是你的錨——每次想岔開，回頭看它：「這跟 count(*) 怎麼跑出來有關嗎？沒有，記進 TODO，繼續。」

## 偵察與定位（00:05–00:20）

**外化動作 1：先猜，把假設寫下來（哪怕會錯）。**

我對 PostgreSQL 零背景。但我不是零工具——我有 Ch 28 之前六個 codebase 的 pattern 庫。我先寫下**盲猜的假設**：

```
【假設 H1】executor 有個入口函式，名字大概帶 Exec/Run/Main。
【假設 H2】query 執行大概是某種迴圈「產生一列列結果」。
【假設 H3】不同運算（scan、count）大概各有各的處理函式。
```

寫下來的好處：等下讀 code 時，我是在**驗證/推翻具體假設**，不是漫無目的地讀。這比「打開檔案從頭看」高效一個檔次。

**動作 2：偵察目錄，用檔名建地圖。**

```bash
$ ls src/backend/executor/ | grep -E "Main|node(Seq|Agg)"
execMain.c
nodeAgg.c
nodeSeqscan.c
```

`execMain.c` 命中 H1。`nodeSeqscan.c`/`nodeAgg.c` 命中 H3（一種運算一個檔）。**兩個假設在讀任何一行 code 前就先被檔名證實了**——這是誠實命名的專案給冷讀者的禮物。

**動作 3：`rg` 找入口，驗證 H1、H2。**

```bash
$ rg -n "ExecutorRun|ExecutePlan|ExecProcNode\(" src/backend/executor/execMain.c | head
310:standard_ExecutorRun(QueryDesc *queryDesc, ...
365:		ExecutePlan(estate, ...
1465:			slot = ExecProcNode(ps);
1609:ExecutePlan(EState *estate, PlanState *planstate, ...
1665:		slot = ExecProcNode(planstate);
```

`ExecutePlan` + 反覆出現的 `ExecProcNode(...)` → H2 也命中（某種迴圈產列）。**15 分鐘，三個盲猜假設全部驗證，入口鎖定。** 更新筆記：

```
【已確認】入口 standard_ExecutorRun → ExecutePlan (execMain.c:1609)
【下一步】讀 ExecutePlan 的迴圈，看它怎麼「產一列」
```

## 讀主迴圈：第一次外化成圖（00:20–00:35）

打開 `execMain.c:1646` 的迴圈，讀完立刻**把它畫成圖**（不是抄 code，是抽象成機制）：

```c
/* execMain.c:1646 (REL_17_2) 節選 */
	for (;;)
	{
		slot = ExecProcNode(planstate);   /* 要一個 tuple */
		if (TupIsNull(slot))
			break;                         /* NULL = 沒了，收工 */
		...
		dest->receiveSlot(slot, dest);     /* 送給 client */
	}
```

我在筆記本畫（真的畫，ASCII 或手繪都行）：

```
   ExecutePlan:
   ┌──────────────────────────────┐
   │  loop:                        │
   │    slot = ExecProcNode(根節點)│ ← 跟根要「一個」tuple
   │    if slot == NULL: break     │ ← 拿到 NULL 就結束
   │    送 slot 給 client          │
   │  goto loop                    │
   └──────────────────────────────┘
```

**費曼複述 #1（對空氣講，或寫成一句白話）：**

> 「執行一個 query，就是反覆跟計畫樹的根節點要一個 tuple，要到就送出去，要到 NULL 就結束。根節點多複雜，這個迴圈不管。」

講得順。**講得順代表這一小塊懂了。** 講不順的地方就是沒懂——這是費曼測試的用途：它是懂沒懂的**即時檢測器**，不是形式。

## 被 indirection 騙一次（00:35–00:50）

現在的問題：`ExecProcNode(planstate)` 到底幹嘛？**我帶著 SQLite VDBE 的直覺盲猜**：

```
【假設 H4】ExecProcNode 大概是個大 switch，按節點種類分派（像 VDBE 按 opcode）。
```

跳進 `ExecProcNode` 的定義（LSP 跳定義，或 `rg -n "^ExecProcNode\b|ExecProcNode\(PlanState"）：

```c
/* src/include/executor/executor.h:269 (REL_17_2) */
static inline TupleTableSlot *
ExecProcNode(PlanState *node)
{
	if (node->chgParam != NULL)
		ExecReScan(node);
	return node->ExecProcNode(node);   /* ← 不是 switch！是函式指標！ */
}
```

**H4 被推翻。停下來，把踩雷寫進筆記（這一步很重要——踩雷是教材）：**

```
【推翻 H4】ExecProcNode 不是 switch，是 node->ExecProcNode(node) 函式指標。
【教訓】我被 SQLite 的 VDBE 直覺騙了。PG 不是線性 bytecode + opcode switch，
        是「每個節點自帶處理函式指標」的樹。indirection 又騙我一次。
【新問題】那 SeqScan 的邏輯在哪？函式指標指向誰？誰設定它？
```

**這就是真實攻堅的樣子：假設會被推翻，而推翻的瞬間是學最多的瞬間。** 如果我沒把 H4 寫下來，推翻時我只會有一種模糊的「啊搞錯了」的感覺；寫下來，我得到一條可複用的教訓（「PG executor 是函式指標樹，不是 opcode switch」）。

**動作：找誰設定那個函式指標。** `rg` 反向查：

```bash
$ rg -n "ExecProcNode =" src/backend/executor/execProcnode.c | head
433:	node->ExecProcNodeReal = function;
434:	node->ExecProcNode = ExecProcNodeFirst;
```

再找 SeqScan 的處理函式怎麼掛上去——讀 `nodeSeqscan.c`，`ExecInitSeqScan` 把 `ExecSeqScan` 設成該節點的處理函式。順藤摸到那個「真正的 switch」（`ExecInitNode`，`execProcnode.c`），它**只在建樹時跑一次**、把對的函式指標塞進每個節點。更新心智模型：

```
【修正模型】
  建樹時（一次）：ExecInitNode 的 switch → 每個節點塞好 ExecProcNode 函式指標
  執行時（每 tuple）：ExecProcNode 純函式指標呼叫，沿樹 pull，不碰 switch
```

## 追 data flow：沿函式指標往下鑽（00:50–01:10）

現在追我們的具體 query。`count(*)` 的 Plan tree 是 Agg 在上、SeqScan 在下。根是 Agg，所以 `ExecProcNode(根)` 呼到 `ExecAgg`。

**外化動作：邊追邊畫 pull chain，每一跳標「這裡發生什麼」。**

`ExecAgg`（`nodeAgg.c:2158`）→ `agg_retrieve_direct` → `fetch_input_tuple`（`nodeAgg.c:547`），關鍵一行：

```c
/* src/backend/executor/nodeAgg.c:547 節選 (REL_17_2) */
	else
		slot = ExecProcNode(outerPlanState(aggstate));  /* ← Agg 向子節點 pull！ */
```

**這一行是整個火山模型的靈魂。** Agg 為了算 count，在自己內部 `ExecProcNode(子節點)`——它是子節點的**消費者**，反覆 pull 直到子節點回 NULL，然後把累計的 count 包成一個 tuple 吐出。

費曼複述 #2（對空氣講）：

> 「Agg 節點被上面 pull 的時候，它自己會去 pull 下面的 SeqScan，一列一列吃進來累加，吃到 NULL 為止，然後才吐出一個 count 結果。所以 count(*) 是『掃完才出結果』，不是邊掃邊出。」

順。繼續往下：子節點 `ExecProcNode` 指向 `ExecSeqScan`（`nodeSeqscan.c:108`）→ 丟給共用的 `ExecScan`（`execScan.c:193`）+ 回呼 `SeqNext` → `SeqNext`（`nodeSeqscan.c:50`）呼 `table_scan_getnextslot`。

**到 `table_scan_getnextslot` —— 止步線到了。** 這是 executor/storage 邊界，任務界定說到這停。我不追進去（但記進 TODO：「storage 層 heap scan + MVCC 可見性，未讀」）。

**完整 pull chain 圖（攻堅的主交付物之一）：**

```
   ExecutePlan (execMain.c:1646)
   │  「跟根要一個 tuple，要到 NULL 為止」
   └─► ExecProcNode(root=Agg)               [函式指標]
       └─► ExecAgg (nodeAgg.c:2158)
           │  「我得吃光子節點才能吐 count」
           └─► ExecProcNode(outer=SeqScan)  [Agg 向下 pull]
               └─► ExecSeqScan (nodeSeqscan.c:108)
                   └─► ExecScan (execScan.c:193)  [共用骨架：過濾 x>10、投影]
                       │  「過 qual 的才回，沒過的抓下一個」
                       └─► SeqNext (nodeSeqscan.c:50)
                           └─► table_scan_getnextslot(...)
                               ▲── 止步線：executor/storage 邊界
```

## 動態驗證假設（01:10–01:20）

紙上讀懂了，但**讀碼鐵律是不信二手描述，包括不信自己的推理**。能 build 能跑的目標，就用 debugger 驗一下（`reading_code` Ch 18 debugger-driven reading）。

> 註：PostgreSQL 的 build（`./configure && make`）與起 server、attach gdb 到 backend process 需要完整環境，本課作者環境為 Windows，這段**未實測**，以下是理論預期的驗證步驟——你在 WSL 有 build 起來的話該這樣做：

```gdb
# 在跑起來的 postgres backend 上（找到 backend PID 後 gdb -p PID）
(gdb) break ExecAgg
(gdb) break ExecSeqScan
(gdb) continue
# 然後在 psql 執行：SELECT count(*) FROM t WHERE x > 10;
# 預期：先斷在 ExecAgg（根），continue 後斷在 ExecSeqScan（Agg 向下 pull 的證據）
(gdb) bt        # 預期看到 ExecSeqScan ← ExecProcNode ← fetch_input_tuple ← ExecAgg
```

**這個 backtrace 若如預期，就在執行時鐵證了紙上推理的 pull chain**：`ExecSeqScan` 的呼叫者鏈往上正是 `ExecAgg`——「Agg 向下 pull SeqScan」不再是我的猜測，是 gdb 印出來的事實。**能動態驗證的假設，一定要驗**；不能驗的（本例環境限制）誠實標「未實測，理論預期」，並寫清楚該怎麼驗。這是誠實讀碼的底線。

## 收斂與費曼總複述（01:20–01:30）

碼表 90 分鐘到。**最後動作：合上所有檔案，對著空氣把整條路徑講一遍（終極費曼測試）。** 講不順的地方就是還沒真懂、得回去補的地方。

我的費曼總複述（實際會講出聲，這裡寫成文字）：

> 「PostgreSQL 執行 `SELECT count(*) FROM t WHERE x > 10`，走的是火山模型。planner 先把 query 變成一棵計畫樹：上面是 Agg 節點（算 count），下面掛一個 SeqScan 節點（掃表、過濾 `x > 10`）。
>
> executor 的主迴圈 `ExecutePlan` 很笨——它只會反覆跟樹的根節點要一個 tuple，要到 NULL 就結束。根是 Agg，所以它呼 `ExecAgg`。
>
> 但 count 得先看完所有列才能算，所以 `ExecAgg` 在自己內部反過來去 pull 子節點：`ExecProcNode(SeqScan)`。SeqScan 每次從表拉一列、丟掉不滿足 `x > 10` 的，把滿足的吐上去給 Agg。Agg 一列一列累加，SeqScan 吐 NULL（掃完）時，Agg 才把累計的數字包成一個 tuple 吐給主迴圈。主迴圈把它送給 client，再要下一個時 Agg 回 NULL，結束。
>
> 關鍵機制有三個：一，`ExecProcNode` 不是 switch，是每個節點自帶的函式指標——真正的 switch 在建樹時（`ExecInitNode`）跑一次就定好了。二，pull 介面統一（大家都是『被叫就吐一個或 NULL』），所以 streaming 的 SeqScan 和 blocking 的 Agg 能自由組合。三，executor 碰到 `table_scan_getnextslot` 就把球交給 storage 層，那是另一個子系統。」

**這段講得順，代表攻堅成功。** 任何講到卡住的分句，都對應一段沒讀透的 code——那就是下一次攻堅的起點。

## 底層機制：這場攻堅用到的每一個動作

把剛才 90 分鐘拆解成可複製的動作清單。**這才是這章要你帶走的——不是 PostgreSQL 的知識，是這套動作。**

```
   ┌─ 00:00 任務界定 ────── 寫下任務/範圍/止步線/碼表（防 rabbit hole）
   │
   ├─ 00:05 先猜後讀 ────── 寫下盲猜假設 H1-H3，再讀 code 去驗
   │        │
   │        └─ 用檔名建地圖（誠實命名的專案是禮物）
   │        └─ rg 找入口，驗證假設
   │
   ├─ 00:20 讀一塊就外化 ── 讀懂主迴圈 → 立刻畫成圖 → 費曼複述 #1
   │
   ├─ 00:35 被 indirection 騙 ── H4 被推翻 → 把踩雷寫成教訓（推翻=學最多）
   │        │
   │        └─ 反向 rg「誰設定這個函式指標」找到真相
   │
   ├─ 00:50 追 data flow ── 沿函式指標往下鑽，每跳標「這裡發生什麼」→ 費曼 #2
   │        │
   │        └─ 在止步線停（executor/storage 邊界），rabbit hole 記 TODO
   │
   ├─ 01:10 動態驗證 ────── 能跑就 gdb 下斷點看 backtrace，驗證紙上推理
   │                        不能跑就誠實標「未實測」+ 寫清楚該怎麼驗
   │
   └─ 01:20 費曼總複述 ──── 合上檔案，對空氣講完整條路徑，卡住處=沒懂處
```

四個動作是這套方法的骨幹，全程貫穿：

1. **先猜後讀**：讀前寫假設，把「讀」變成「驗證/推翻」，方向感來自此。
2. **讀一塊就外化**：每讀懂一小塊，立刻畫圖 + 費曼複述，不囤積「以為懂了」。
3. **踩雷寫成教訓**：假設被推翻時停下記錄——這是可遷移知識的來源（「PG 是函式指標樹」下次直接用）。
4. **止步線紀律**：碰到子系統邊界就停、記 TODO，不被吸進 rabbit hole。

## 外化工具包：這場攻堅產出的四份紙上證據

「外化」不是抽象口號——它產出**具體、可保存、可回看**的東西。這場 90 分鐘攻堅在我筆記本上留下四份紙上證據，每份都有明確用途。把它們列出來，你就知道下次攻堅該產出什麼：

**證據 1：任務界定卡（00:00 寫的那五行）。** 用途：全程的錨。每次想岔開，回看它一眼——「這跟目標有關嗎？沒有 → 記 TODO，繼續」。這五行擋掉了 PostgreSQL 裡無數個誘人的 rabbit hole。

**證據 2：假設帳本（H1-H4 的清單）。**
```
   H1 入口帶 Exec/Run/Main  ✓ 命中 (standard_ExecutorRun)
   H2 某種迴圈產一列列結果   ✓ 命中 (ExecutePlan for(;;))
   H3 不同運算各有處理函式   ✓ 命中 (node*.c 一運算一檔)
   H4 ExecProcNode 是 switch  ✗ 推翻 → 是函式指標 → 教訓：PG 是函式指標樹
```
用途：把「讀」變成「驗證/推翻」。每條假設的 ✓/✗ 都是進度。**✗ 那條最值錢**——推翻換來的「PG 是函式指標樹」是可遷移教訓，下次遇到類似結構直接用。

**證據 3：pull chain 圖**（前面那張 ExecutePlan→…→storage 邊界）。用途：把腦內的控制流「釘」在紙上，講費曼時照著講、事後回看時秒懂當時的理解。**圖是抽象，不是抄 code**——畫圖的過程本身逼你把細節壓縮成機制。

**證據 4：TODO / rabbit hole 池。**
```
   TODO：table_scan_getnextslot 之下——heap access method + MVCC 可見性，未讀
   TODO：ExprContext / TupleTableSlot 的生命週期，這次跳過
   TODO：ExecInitNode 全表 40+ 種節點，只看了 SeqScan/Agg
```
用途：忍住沒追的坑不會消失、不會變成「欸我是不是漏了什麼」的焦慮——它們排隊等下一場攻堅。**這份池是你下次沒靈感時的攻堅目標庫。**

**四份證據 = 一份 reading journal**（`reading_code` Ch 35）。這場攻堅結束，我有一份可存檔、三個月後還看得懂、能拿去改進 SOP 的完整記錄。**沒有這四份產出的攻堅，等於沒發生**——爽感會蒸發，可遷移知識不會沉澱。

## 對比與取捨：外化 vs 純腦讀

| 面向 | 純腦內讀（多數人預設）| 全程外化（本章）|
|---|---|---|
| 假設 | 模糊、事後才發現錯 | 寫下來，錯了立刻知道、變教訓 |
| 進度感 | 「讀了很多但說不清懂什麼」 | 每張圖/每段費曼 = 一塊確定懂了的 |
| rabbit hole | 常被吸走、忘了在幹嘛 | 任務界定 + TODO 擋住 |
| 懂沒懂 | 自我感覺，常騙自己 | 費曼測試即時檢測，騙不了 |
| 事後複用 | 過幾天全忘 | journal + pull chain 圖可回看 |
| 速度 | 感覺快，實則反覆迷路 | 感覺慢，實則直線收斂 |

**外化的成本是「感覺慢」——你得停下來寫、畫、講。但它換來的是直線收斂與不自欺。** 高手看起來讀得快，不是因為他們跳過外化，是因為外化已經內化成習慣、快到你看不見。新手要刻意、外顯地做，做久了才會內化。

## 踩雷集錦

1. **不寫任務界定就開讀。** 最大的雷。沒有錨，你會在 PostgreSQL 這種龐然大物裡被任何有趣的東西吸走，兩小時後發現讀了一堆跟目標無關的 MVCC。第一個動作永遠是寫下任務/範圍/止步線。

2. **囤積假設不驗證。** 「我覺得 ExecProcNode 是 switch」放在腦裡不寫、不驗，等到很後面才發現全盤錯，前面的推理全崩。假設要**寫下來、儘早驗**——早推翻早止血。

3. **費曼測試當成形式跳過。** 「我懂了啦不用講」——然後講的時候卡住。費曼不是儀式，是懂沒懂的**唯一可靠檢測器**。每讀懂一塊就講一遍，卡住立刻回去補。

4. **能驗卻不驗，或不能驗卻假裝驗。** 能 build 能跑的目標，紙上推理一定要用 gdb 動態驗（backtrace 是鐵證）。不能跑的，誠實標「未實測、理論預期」並寫清楚驗法——**絕不編一個「我跑過了」的假輸出**。這是讀碼誠信的底線。

5. **不設止步線，追進每個子系統。** 追 SeqScan 追到 `table_scan_getnextslot`，手癢想追進 heap access method、buffer manager、MVCC……然後這場 90 分鐘攻堅變成三天，還是沒回答原本的問題。**邊界要止步、rabbit hole 記 TODO 改天再開一場攻堅。**

## 進階：再往深一層

- **多開幾場攻堅，換不同路徑**：追一個 JOIN（`nodeHashjoin.c` 的 `ExecHashJoin`，它是個 `for(;;)` + `switch(node->hj_JoinState)` 的狀態機——HJ_BUILD_HASHTABLE → HJ_NEED_NEW_OUTER…，和 SeqScan 的簡單 pull 是不同節奏的好對照）。同一套動作、不同路徑，練到動作變肌肉記憶。
- **把 journal 累積成個人 wiki**：每場攻堅的任務界定、pull chain 圖、費曼摘要、TODO 存下來。三個月後你會有一份「我攻過哪些系統、學到哪些可遷移 pattern」的私人資產——這正是 Ch 31 要建的訓練系統。
- **對比 SQLite 同一個 query 的攻堅**：拿 `SELECT count(*) FROM t WHERE x > 10` 在 SQLite 用 `.explain` 印出 VDBE bytecode，走一遍「線性 bytecode + opcode」的執行；和本章的「節點樹 pull」並排，親手體會 Ch 28 那張 VDBE vs 火山模型對照表的兩端。

## 本章重點整理

- **這章教的是動作，不是知識**：任務界定 → 先猜後讀 → 讀一塊就外化 → 踩雷寫成教訓 → 止步線紀律 → 費曼總複述。
- **外化（寫假設、畫圖、講費曼）是把讀碼從腦內模糊變成紙上可檢驗的核心技術**，成本是「感覺慢」，回報是直線收斂與不自欺。
- **假設要寫下來儘早驗**——被推翻的瞬間是學最多的瞬間（H4「以為是 switch」被推翻，換來「PG 是函式指標樹」這條可遷移教訓）。
- **費曼測試是懂沒懂的即時檢測器**，不是儀式；卡住的分句精準對應沒讀透的 code。
- **止步線紀律防 rabbit hole**：碰子系統邊界就停、記 TODO，一場攻堅只回答一個問題。
- **能動態驗證就用 gdb 驗**（backtrace 是鐵證），不能驗就誠實標「未實測」——絕不編假輸出。

## 自我檢核

- [ ] 我能複述這場攻堅的六個動作階段，並說出每個階段的外化產物（假設清單/圖/費曼/教訓/TODO）
- [ ] 我能解釋「先猜後讀」為什麼比「打開檔案從頭看」高效
- [ ] 我能說出 H4 被推翻時我學到的可遷移教訓，以及它下次怎麼幫我
- [ ] 我能不看教材，把 count(*) 的 pull chain 從 ExecutePlan 講到 storage 邊界
- [ ] 我理解為什麼「感覺慢」的外化其實是直線收斂，而「感覺快」的純腦讀常在迷路

## 延伸閱讀

- **`reading_code` Ch 35「reading journal」與 Ch 36「費曼測試」**（本 repo）
  - **讀哪裡**：兩章都讀。本章的外化動作全建立在它們上；讀完回頭看這場攻堅，你會認出每個動作對應哪個技巧
  - **前提**：無
- **`reading_code` Ch 18「debugger-driven reading」**（本 repo）
  - **讀哪裡**：整章。本章 01:10 的 gdb 驗證段就是它的實戰；學會用 backtrace 把紙上推理釘死成執行事實
  - **前提**：會基本 gdb
- **PostgreSQL `src/backend/executor/README`**（官方）
  - **讀哪裡**：讀完本章的攻堅後對照。你會發現自己**冷讀推出來的模型**和官方 README 描述的一致——這是攻堅成功的最好驗證（不是先看答案再讀，是讀完再核對）
  - **前提**：讀過 Ch 28、本章
- **John Ousterhout,《A Philosophy of Software Design》第 2 章「The Nature of Complexity」**
  - **讀哪裡**：complexity 如何透過 indirection/抽象被封裝與被隱藏。本章「被函式指標騙一次」正是這種封裝的雙面刃——理解它，你下次會更快識破 indirection
  - **前提**：無

你已經看完一場完整的限時冷啟動攻堅，也把方法論的每個動作看清楚了。攻過六個 codebase、加上這場 capstone，你腦裡累積了一大堆 pattern。下一章我們把它們**全部收斂成一張可查表**——你的 pattern 字典，這門課的畢業證書。

→ [Ch 30 你的 pattern 字典：六個 codebase 的 idiom 收斂成一張表](./30-your-pattern-dictionary.md)
