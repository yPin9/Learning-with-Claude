# Ch 14 — 衝突解決與 CRDT 入門

> **目標**：正面處理上一章刻意迴避的問題——當兩個副本收到**並行、衝突**的寫入，該怎麼辦？先看最誘人也最危險的 **LWW（last-write-wins，最後寫入者勝）** 為什麼會靜默丟資料（它的命門是時鐘）；再用 **version vector（版本向量）** 偵測並**保留**衝突（siblings，並存的分身）而非亂丟一個。然後進入本章重頭戲 **CRDT（Conflict-free Replicated Data Type，無衝突複製資料型別）**：state-based（CvRDT，靠 join semilattice 的數學保證收斂）vs op-based，並手刻 **G-Counter、PN-Counter、OR-Set**。最後在 Go 上跑起來，親眼看兩個副本各自操作後 merge，**無論合併順序都收斂到同一個值**——這就是交換律、結合律、冪等性的威力。

> **環境**：Go 1.18.1, WSL2 / Linux x86-64。CRDT 的 demo 是純資料結構，不需要 dsim。

上一章 quorum 用版本號決定「哪個值最新」，但這是在敷衍——當兩個寫入**真的並行**（誰也不因果先於誰），「最新」根本沒有客觀答案。這一章我們把這個坑挖到底：怎麼**偵測**並行衝突（version vector）、怎麼**不丟資料地保留**衝突、以及一整族「設計成無論怎麼並行都不會衝突」的資料結構（CRDT）。這是最終一致系統從「會壞」走向「可信」的關鍵一步。

## 為什麼需要這個？

無主複製（Ch 13）和多主複製都允許多個副本獨立接受寫入。於是必然出現這個場景：

```
   初始 x = "a"
   副本 A 收到 write(x, "b")   ┐ 這兩個寫入之間沒有因果關係
   副本 B 收到 write(x, "c")   ┘ （A、B 沒讀到對方）→ 真並行衝突

   副本互相複製後：A 有 "b"，B 有 "c"，到底 x 該是什麼？
```

這不是「誰晚誰贏」能解決的——兩個寫入**並行**（Ch 5 的 happened-before 意義下互不先於對方），根本沒有「晚」這個客觀概念。你有幾種選擇：

1. **硬選一個（LWW）**：用某種規則（時間戳、節點 ID）挑一個贏，另一個**默默丟掉**。簡單，但會靜默丟資料。
2. **偵測到衝突，保留兩個（siblings）**：告訴應用層「這裡有衝突，"b" 和 "c" 都在，你決定怎麼合」。不丟資料，但把難題丟給應用層。
3. **設計成根本不會衝突（CRDT）**：把資料型別設計成「任何並行操作都能自動、確定地合併」。應用層完全不用管衝突。

歷史脈絡：Dynamo（2007）選了 2（vector clock 偵測 + siblings）；早期 Cassandra 選了 1（LWW，付出過丟資料的代價）；而學術界在 2011 年由 Shapiro 等人形式化了 3——CRDT，給出「怎麼設計出自動收斂的資料型別」的數學框架。今天協同編輯（Google Docs、Figma）、分散式資料庫（Riak、Redis CRDT）、本地優先軟體（local-first）全建立在 CRDT 上。這章是理解它們的入口。

## LWW 的致命傷：它把正確性押在時鐘上

LWW 是最誘人的方案：每個寫入帶一個時間戳，合併時**留時間戳大的、丟時間戳小的**。一行邏輯，收斂到單一值，皆大歡喜——直到你想起 Ch 4，**時鐘會說謊**。

```
   真實發生順序：使用者先寫 "b"，稍後才寫 "c"（"c" 才是他要的）
   但兩個寫入打在時鐘不同步的兩個節點上：

   節點 A（時鐘快 5 秒）: write(x,"b")  帶時間戳 t=100
   節點 B（時鐘慢）:      write(x,"c")  帶時間戳 t=97   ← 其實較晚發生

   LWW 合併：t=100 > t=97 → 留 "b" 丟 "c"
                                    ▲
                        使用者最後寫的 "c" 被靜默丟棄！
                        沒有錯誤、沒有告警，資料就這樣沒了
```

這是 LWW 的命門：**它把「哪個寫入該勝」的裁決權，交給了不可信的實體時鐘。** 時鐘偏差幾毫秒到幾秒都很正常（Ch 4），於是「真正較晚的寫入」可能帶著較小的時間戳被丟掉。更糟的是**靜默**——沒有任何跡象，你事後翻資料才發現寫入不見了。

Cassandra 早期用 wall-clock LWW，在時鐘不同步的叢集裡就出過「寫入神秘消失」的事故。LWW 不是不能用，而是**只在你能接受「偶爾靜默丟一個並行寫入」時才能用**（例如快取、可重算的衍生資料）。對訂單、餘額這種資料，LWW 是災難。

還有一個更根本的問題：即使時鐘完美同步，LWW **仍然會丟資料**——因為兩個真並行的寫入，本來就不該由「時間」來裁決誰勝。丟掉其中一個，就是丟掉了使用者真的做過的一次操作。時鐘只是讓這件壞事變得不可預測而已。

## Version Vector：偵測並保留衝突

要不丟資料，第一步是**偵測**——分清楚「一個寫入因果地覆蓋另一個」（那可以安全取代）和「兩個真並行」（那不能亂丟）。這正是 **vector clock / version vector（Ch 6）** 的看家本領。

> 若對 vector clock 的 happened-before 判定不熟，強烈回看 [Ch 6](./06-vector-clocks.md)。這裡直接用它的結論：兩個版本向量可比大小（一個每維都 ≥ 另一個）= 因果有序；不可比 = 並行。

version vector 給每個 key 的每個版本附一個向量 `[A:_, B:_, C:_]`，記錄「這個版本見過各節點的多少次更新」。合併兩個版本時：

```
   版本1: [A:2, B:1]   版本2: [A:2, B:1]  → 相等，同一個，隨便留一個
   版本1: [A:2, B:1]   版本2: [A:3, B:1]  → 版本2 每維 ≥ 版本1 → 版本2 因果較新，取代版本1 ✓
   版本1: [A:2, B:1]   版本2: [A:1, B:2]  → 不可比！(A維1<2 但 B維2>1) → 並行衝突！
                                             兩個都保留成 siblings
```

第三種情況——**不可比 = 並行 = 真衝突**——是關鍵。version vector 不會像 LWW 那樣硬選一個丟掉，而是**兩個版本並存**，稱為 **siblings（並存分身）**。下次讀取時，把兩個 siblings 一起交給應用層或客戶端：「x 現在有兩個並行版本 "b" 和 "c"，你要合併成什麼？」客戶端合併後寫回，新版本的向量涵蓋兩個 siblings（每維取 max 再自增），衝突就此消解。

```
   讀到 siblings {"b"[A:2,B:1], "c"[A:1,B:2]}
        │
        └─> 應用層合併（例如購物車：取聯集 {b,c}）
            寫回，新版本向量 [A:2,B:2]（涵蓋兩個 sibling）→ 衝突消解
```

Dynamo 的購物車就是這樣：兩個並行的「加入購物車」不會互相覆蓋（那會讓商品神秘消失），而是保留成 siblings，讀取時取聯集——寧可多留一個已刪除的商品，也不要丟掉使用者加的商品。**version vector 把「丟資料」換成了「把合併決策留給懂語意的應用層」。** 代價：應用層得寫合併邏輯，且 siblings 會累積（需要修剪）。有沒有辦法連應用層都不用管合併？有——CRDT。

## CRDT：設計成根本不會衝突

CRDT 的野心是：**把資料型別設計成，任何並行操作都能自動、確定地合併，永遠收斂到同一個值，完全不需要協調、也不需要應用層裁決衝突。** 「conflict-free」不是「不會發生並行」，而是「並行了也沒有需要人來解的衝突——合併規則本身保證了正確結果」。

CRDT 有兩大流派，先建立心智圖像：

```
   state-based (CvRDT)              op-based (CmRDT)
   ┌──────────────┐                ┌──────────────┐
   │ 副本傳「整個  │                │ 副本傳「操作」 │
   │  狀態」給對方 │                │ (inc, add...) │
   │  對方 merge   │                │ 對方 apply    │
   └──────────────┘                └──────────────┘
   merge 必須：                     操作必須：
   交換律+結合律+冪等               對並行操作可交換
   (join semilattice)              (需可靠廣播、不重複)
   容忍訊息重複/亂序/丟(只要最終到) 訊息不能丟/重(要求嚴格)
```

**state-based（CvRDT）** 傳整個狀態，靠 merge 函數的三個代數性質保證收斂。**op-based（CmRDT）** 傳操作本身，靠操作之間可交換保證收斂，但對訊息傳遞要求更嚴（不能丟、不能重複、通常要因果序遞送）。state-based 對網路更寬容（重傳整個狀態總是安全的），我們下面重點講它——因為它的收斂保證是**純數學的**，最漂亮。

### 收斂的數學：join semilattice

state-based CRDT 的收斂保證來自一個代數結構：**join semilattice（併半格）**。不必被術語嚇到，它就是三個條件：

merge 函數（把兩個狀態合併成一個）必須滿足：

1. **交換律（commutative）**：`merge(a, b) = merge(b, a)`——誰先誰後合併結果一樣。
2. **結合律（associative）**：`merge(merge(a,b), c) = merge(a, merge(b,c))`——怎麼分組合併結果一樣。
3. **冪等性（idempotent）**：`merge(a, a) = a`——同一個狀態合併多次等於合併一次。

這三條合起來為什麼保證收斂？因為它們讓「合併」變成一個**與順序、重複、分組都無關**的操作。分散式網路會**亂序**送達（→交換律罩住）、**分批**送達（→結合律罩住）、**重複**送達（→冪等性罩住）。只要每個副本最終收到了同一組更新（不管以什麼順序、分幾次、重複幾遍），三條性質保證它們**必然算出同一個結果**。這就是 **strong eventual consistency（強最終一致，Ch 9）**：不只「最終收斂」，而是「收到同一組更新的副本狀態必然相同」——收斂到什麼是確定的，不靠時序運氣。

這是 CRDT 相對於 LWW/siblings 的質變：**它把「會不會收斂、收斂到哪」從『看時鐘、看運氣、看應用層』升級成『數學保證』。**

## 手刻 CRDT：G-Counter、PN-Counter、OR-Set

理論落地。三個經典 CRDT，難度遞增。

**G-Counter（grow-only counter，只增計數器）**：最簡單的 CRDT。想統計「一個跨副本的計數器」（如網頁總瀏覽數），但每個副本各自 +1，怎麼合併不重複計、不漏計？

關鍵洞見：**別存一個總數，存「每個副本各貢獻了多少」的向量。** 副本 i 只增自己那格 `p[i]`，總值 = 所有格加總。merge = **每格取 max**：

```
   副本 A 狀態: {A:5, B:0}   （A 貢獻 5）
   副本 B 狀態: {A:0, B:7}   （B 貢獻 7）
   merge（每格取 max）: {A:5, B:7}  →  總值 = 5+7 = 12  ✓
```

為什麼「每格取 max」滿足 semilattice？因為 max 本身就交換（max(a,b)=max(b,a)）、結合、冪等（max(a,a)=a）。副本 i 只單調遞增自己那格，所以 max 永遠不會丟失任何副本的貢獻——它只會保留「見過的最大值」。這就是 G-Counter 收斂的全部原理。程式碼：

```go
type GCounter struct {
    id int
    p  map[int]int // 每個副本 id -> 它貢獻的量
}
func (g *GCounter) Inc(n int)  { g.p[g.id] += n } // 只增自己那格
func (g *GCounter) Value() int { s := 0; for _, v := range g.p { s += v }; return s }
func (g *GCounter) Merge(o *GCounter) {
    for id, v := range o.p {
        if v > g.p[id] { g.p[id] = v } // 每格取 max
    }
}
```

**PN-Counter（增減計數器）**：G-Counter 只能增。要能減怎麼辦？如果直接讓格子能減，就破壞了「單調遞增 + max」的收斂保證（max 會丟掉減操作）。聰明的解：**用兩個 G-Counter**，一個記所有增（P）、一個記所有減（N），值 = P.Value() - N.Value()：

```go
type PNCounter struct { pos, neg *GCounter }
func (c *PNCounter) Inc(n int)  { c.pos.Inc(n) }
func (c *PNCounter) Dec(n int)  { c.neg.Inc(n) } // 「減」= 往 neg 這個增計數器 +n
func (c *PNCounter) Value() int { return c.pos.Value() - c.neg.Value() }
func (c *PNCounter) Merge(o *PNCounter) { c.pos.Merge(o.pos); c.neg.Merge(o.neg) }
```

兩個 G-Counter 各自單調遞增、各自 max 合併，都收斂，相減自然也收斂。這種「用兩個單調結構模擬一個可增可減結構」是 CRDT 設計的經典套路。

**OR-Set（Observed-Remove Set，可增可刪集合）**：集合的 CRDT 難在「並行的 add 和 remove 同一元素」——該留還該刪？樸素的做法（存一個 set，add 就加、remove 就刪）會出問題：若副本 A remove(x) 而副本 B 同時 add(x)，合併後 x 該在嗎？語意上「並行的 add 應該贏」（你不能刪掉一個你還沒看到的 add）。

OR-Set 的解法：**每次 add 給元素附一個唯一 tag（如 UUID 或 (副本,計數) 對）**。remove 只能刪掉「它當時觀察到的那些 tag」，刪不掉它沒見過的 tag。元素「在集合裡」的定義是：**存在至少一個沒被移除的 add-tag**。

```
   副本A: add(x) 產生 tag x@a1    → x 的活 tag: {x@a1}
   副本A: remove(x) 移除它見過的  → 移除 {x@a1}，x 的活 tag: {} → x 不在
   副本B（並行，沒見過 remove）: add(x) 產生 tag x@b1 → x 的活 tag: {x@b1}
   merge：A 移除了 x@a1，但 B 的 x@b1 是新 tag、A 的 remove 沒見過刪不掉
          → x 的活 tag: {x@b1} 非空 → x 「在」集合裡  ✓ (並行 add 贏)
```

OR-Set 的收斂靠「add 集合」和「remove 集合（tombstone）」兩個只增的集合（G-Set），元素在不在看「有沒有活著的 add-tag」。這正是協同編輯、購物車、分散式集合的基礎。它比計數器複雜，核心 demo 我們聚焦跑 G/PN-Counter，OR-Set 的原理理解到位即可（延伸閱讀有完整規格）。

## 底層機制：跑起來看收斂

在 Go 上把 G-Counter、PN-Counter 跑起來，驗證三個代數性質真的讓它收斂。實測輸出（WSL, Go 1.18.1）：

情境一：兩個副本各自 increment 後雙向 merge：

```
=== G-Counter: two replicas increment concurrently, then merge ===
before merge: A.value=5 state=map[0:5] | B.value=7 state=map[1:7]
after A<-B, B<-A: A.value=12 | B.value=12  (converge to 12=3+2+7)
```

A 各自 +3、+2（本地看到 5），B +7（本地看到 7）——兩邊在 merge 前對「總值」有分歧（5 vs 7）。雙向 merge 後**兩邊都收斂到 12**（=3+2+7），沒有任何貢獻遺失或重複計。

情境二：驗證交換律、結合律、冪等性——三個副本各 +10/+20/+30，用**不同的合併順序**、以及**重複合併**：

```
=== Commutativity/associativity/idempotence: any merge order converges ===
order1=60 order2=60 idempotent(dup merge)=60  (all must equal 60)
```

- `order1`：(x←y)←z
- `order2`：先 (z←y) 再 x←z（不同分組）
- `idempotent`：x←y、x←z、**再 x←z 一次**（重複合併同一狀態）

三者全部收斂到 60（=10+20+30）。**合併順序不同、重複合併，結果都一樣**——這就是 semilattice 三性質在跑起來的樣子。這正是 CRDT 敢說「網路愛怎麼亂序、重複、分批送都沒關係」的底氣。

情境三：PN-Counter，並行的 inc 和 dec：

```
=== PN-Counter: concurrent inc and dec, merge converges ===
before merge: A.value=3 B.value=3
after merge:  A.value=6 B.value=6  (converge to 6 = (5+4)-(2+1))
```

A 做 +5/-2（本地 3），B 做 +4/-1（本地 3）。merge 後兩邊收斂到 6 = (5+4)-(2+1)——增和減分別在兩個 G-Counter 裡各自收斂，相減得到正確結果。**沒有任何一次增或減被丟掉**，對比 LWW 「並行寫互相覆蓋」的丟資料，天壤之別。

## 對比與取捨

| 衝突解決策略 | 會丟資料? | 誰裁決 | 依賴 | 代價 |
|---|---|---|---|---|
| LWW（時間戳） | **會（靜默）** | 時鐘 | wall-clock（不可信） | 時鐘偏差 → 神秘丟寫入 |
| Version vector + siblings | 不會 | 應用層（合併 siblings） | vector clock | 應用層要寫合併邏輯、siblings 累積 |
| CRDT | 不會 | 資料型別本身（數學保證） | merge 滿足 semilattice | 型別受限、metadata 膨脹 |

| CRDT 流派 | 傳什麼 | 收斂條件 | 對網路要求 | 適用 |
|---|---|---|---|---|
| state-based (CvRDT) | 整個狀態 | merge 交換+結合+冪等 | 寬鬆（可丟/重/亂序，最終到即可） | 反熵 gossip、狀態不大 |
| op-based (CmRDT) | 操作 | 並行操作可交換 | 嚴格（不丟、不重、常需因果序） | 狀態大、只想傳增量 |

## 踩雷集錦

1. **「LWW 只要時鐘同步好就沒問題」——兩層錯。** 第一層：時鐘**永遠**同步不完美（Ch 4），NTP 也有毫秒到秒級偏差，偏差期間的並行寫就會被錯誤裁決、靜默丟失。第二層更根本：**即使時鐘完美，兩個真並行的寫入本來就不該由『時間』裁決誰勝**——丟掉任何一個都是丟掉使用者真做過的操作。LWW 的「勝負」對並行寫入在語意上就是任意的。能接受「偶爾任意丟一個並行寫」才能用 LWW。

2. **「vector clock 判定並行後，隨便留一個 sibling 就好」——那你又退化成 LWW 了。** version vector 的價值就在**偵測到並行後保留兩個**，交給應用層合併。如果偵測到衝突卻還是硬選一個丟，等於白費力氣做偵測，資料照丟。偵測衝突和保留衝突是配套的，缺一不可。

3. **「CRDT 能自動解決所有衝突，什麼資料都能用」——CRDT 有語意代價。** CRDT 的「自動合併」是靠**限定資料型別的語意**換來的：G-Counter 只能增、OR-Set 的「並行 add 勝」是它選定的語意。有些應用的衝突**本質上需要人來決定**（例如「兩人並行改了同一句話成不同內容」），CRDT 只能給你一個「確定但未必符合意圖」的合併結果（如兩句都留、或按某規則交錯）。CRDT 保證**收斂**，不保證**收斂到使用者想要的**。選 CRDT 前先確認你的資料語意能被某個 CRDT 型別表達。

4. **「CRDT 沒有 metadata 成本，就是普通資料結構」——metadata 會膨脹，且刪不乾淨。** G-Counter 要為**每個曾經寫過的副本**存一格（副本多就大）；OR-Set 要為每個 add 存 tag、remove 要存 tombstone（墓碑），**tombstone 不能隨便刪**（刪早了會讓已刪除的元素「復活」）。真實 CRDT 系統花大量工夫在「安全地垃圾回收 metadata」——這是 CRDT 工程化最麻煩的部分，論文常輕描淡寫、生產系統血淚斑斑。

5. **「op-based CRDT 跟 state-based 一樣寬容，隨便傳操作就好」——op-based 對傳遞層要求嚴得多。** state-based 傳整個狀態，重傳、亂序、重複都安全（冪等 + 交換罩住）。但 op-based 傳「操作」（如 inc、add），**同一個操作 apply 兩次結果就錯了**（計數器多加一次），所以它要求傳遞層**不重複、不遺失、常常還要因果序遞送**——這等於把難題推回到「可靠因果廣播」（Ch 7）。別以為 op-based 更輕就更簡單，它的複雜度轉移到了訊息層。

## 進階：再往深一層

- **δ-CRDT（delta CRDT）**：state-based 的痛是「每次同步傳整個狀態」，狀態大時很浪費。δ-CRDT 只傳「自上次同步以來的狀態增量（delta）」，兼顧 state-based 的網路寬容和 op-based 的低頻寬。Riak、Redis 的 CRDT 實作都往這方向走。想真正把 CRDT 用在生產，δ-CRDT 是必修。

- **協同編輯：RGA / sequence CRDT**：本章的計數器/集合是無序的，但協同編輯（Google Docs、Figma）要的是**有序序列**的 CRDT——並行插入同一位置的字元怎麼定序且收斂？這是 **RGA（Replicated Growable Array）**、**Logoot/LSEQ**、以及 Yjs/Automerge 這些函式庫的核心。它給每個字元一個稠密、可比大小的「位置識別符」，讓並行插入有確定順序。這是 CRDT 最有商業價值的分支。

- **CRDT vs OT（Operational Transformation）**：協同編輯的另一條老路是 OT（Google Docs 早期用的），它用「轉換操作」而非「設計無衝突型別」來合併並行編輯。OT 需要中央伺服器且轉換函數極難寫對（出過無數 bug）；CRDT 去中心化、正確性靠數學但 metadata 較重。兩派之爭是分散式協作領域的經典辯論，Kleppmann 有一系列文章對比，值得一讀。

- **CRDT 的一致性上限**：CRDT 提供 strong eventual consistency，但它**不能**提供需要全域協調的保證——例如「銀行帳戶餘額不得為負」這種**不變量（invariant）**，CRDT 做不到（兩個副本並行提款可能各自看起來合法、合併後超支）。這呼應 Ch 9：CRDT 停在「因果 + 收斂」這一級，要維護跨副本的強不變量，仍得回到共識（Part 3）。知道 CRDT 的邊界，才不會拿它去解它解不了的問題。

## 本章重點整理

- **並行衝突無法用「時間」裁決**：兩個並行寫入（Ch 5 意義下互不先於對方）沒有客觀的「較晚」，硬選一個就是丟資料。
- **LWW（last-write-wins）**：用時間戳留大丟小，簡單但**把正確性押在不可信的時鐘上**，會靜默丟失並行寫入。只在能容忍「偶爾任意丟一個並行寫」時可用。
- **Version vector + siblings**：用 vector clock 偵測「因果有序（可安全取代）」vs「並行（真衝突）」，並行時**保留兩個 siblings 交給應用層合併**，不丟資料。Dynamo 購物車的做法。
- **CRDT**：設計成「任何並行操作都自動、確定地合併」的資料型別，應用層完全不用管衝突。
- **收斂的數學（state-based / CvRDT）**：merge 滿足**交換律 + 結合律 + 冪等性**（join semilattice），就能容忍網路的亂序、分批、重複，保證收到同組更新的副本**必然收斂到同一值**（strong eventual consistency）。
- **經典 CRDT**：G-Counter（每副本一格、merge 取 max）、PN-Counter（兩個 G-Counter 相減模擬增減）、OR-Set（每個 add 附唯一 tag、remove 只刪見過的 tag、並行 add 勝）。
- **CRDT 的邊界**：保證收斂不保證「收斂到你想要的」；metadata（tombstone）會膨脹且難回收；維護強不變量（如餘額非負）仍需共識。

## 自我檢核

- [ ] 我能用一個時鐘偏差的例子，說清楚 LWW 為什麼會**靜默**丟資料，並指出即使時鐘完美它仍有的根本問題
- [ ] 給我兩個 version vector，我能判斷它們是「因果有序（誰取代誰）」還是「並行（保留 siblings）」
- [ ] 不看筆記，我能寫出 state-based CRDT 收斂需要的三個代數性質，並說出每一個分別罩住網路的什麼行為（亂序/分批/重複）
- [ ] 我能解釋為什麼 G-Counter 要「每副本存一格 + merge 取 max」，而不是直接存一個總數
- [ ] 我能說清楚 PN-Counter 為什麼用兩個 G-Counter，而不是讓格子能減
- [ ] 我能用「並行 add 勝」講清楚 OR-Set 的 tag 機制解決了什麼
- [ ] 我能說出 CRDT 的邊界：它保證什麼、不保證什麼、什麼問題該回頭找共識

## 延伸閱讀

- **[A comprehensive study of Convergent and Commutative Replicated Data Types](https://inria.hal.science/inria-00555588/document)** — Shapiro, Preguiça, Baquero, Zawirski, INRIA RR-7506（2011）
  - **這篇說什麼**：CRDT 的奠基論文，形式化定義 CvRDT（state-based）與 CmRDT（op-based）、證明 semilattice 收斂、並給出 G-Counter/PN-Counter/OR-Set 等一整族規格
  - **讀哪裡**：Section 2（兩種 CRDT 與收斂定理）建立框架；Section 3 的各個 counter/set 規格對照本章實作
  - **前提**：讀懂本章 semilattice 三性質與 vector clock；形式化證明可略讀，抓「為什麼三性質保證收斂」的直覺

- **[CRDTs: The Hard Parts](https://martin.kleppmann.com/2020/07/06/crdt-hard-parts-hydra.html)** — Martin Kleppmann（2020, talk + 文字）
  - **這篇說什麼**：CRDT 工程化真正難的地方——metadata/tombstone 的垃圾回收、序列 CRDT（協同編輯）的位置識別符、與 OT 的對比
  - **讀哪裡**：整場都好，重點在「為什麼 metadata 難清」與序列 CRDT 那段，直接對應本章踩雷 #4 與進階節
  - **為什麼值得看**：作者是 Automerge（生產級 CRDT 函式庫）作者，把論文不談的工程血淚講透

- **[Dynamo 論文 Section 4.4 "Data Versioning"](https://www.allthingsdistributed.com/files/amazon-dynamo-sosp2007.pdf)** — DeCandia et al., SOSP（2007）
  - **這節說什麼**：真實系統怎麼用 vector clock 偵測衝突、保留 siblings、購物車怎麼在讀取時合併
  - **讀哪裡**：4.4 一節即可，配本章 version vector 節看「理論怎麼落到工程」
  - **前提**：讀懂本章 version vector 與 Ch 6；這是 siblings 機制的權威實例

我們把「多副本各說各話」從「會靜默丟資料」一路做到「數學保證收斂」。但 CRDT 有它的天花板——它撐不起「餘額不得為負」這種需要全域協調的強不變量。要跨副本對「一件事」達成真正的一致決定，我們需要一個全新的、更強的工具。Part 3 開始，我們正面進攻分散式系統的皇冠——**共識（consensus）**。

→ [練習 B：Quorum-based KV Store](./practice-b-quorum-kv-store.md)
