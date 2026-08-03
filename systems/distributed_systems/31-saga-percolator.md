# Ch 31 — Saga 與 Percolator

> **目標**：2PC（[Ch 30](./30-distributed-transactions-2pc-3pc.md)）用「阻塞 + 鎖」換強一致的原子提交，很多現代系統不願付這個代價。本章看兩條現代路線。**Saga**：把長交易拆成一串本地交易 + 補償動作，放棄隔離性換可用性，微服務的主力。**Percolator**（Google）：在 BigTable 上用快照隔離 + 2PC + primary lock 做出可容錯的跨行交易，TiDB 用它。順帶把 **MVCC + 時間戳排序** 與 Spanner TrueTime 的外部一致串起來（接 [Ch 39](./39-google-spanner.md)）。最後給你一張 2PC vs Saga vs Percolator 的取捨表。

> **環境**：Go 1.18.1, WSL2 / Linux x86-64。本章 code 少，Saga 有小示範。

## 為什麼需要這個？

上一章的結論很沉重：2PC 能保證跨節點原子提交，但代價是**阻塞**（coordinator 一掛，prepared 的 participant 卡死）和**鎖**（prepared 期間鎖著資源，拖累所有想碰它的交易）。在兩個現實場景下，這個代價高到不可接受：

1. **微服務架構**。一個下單流程橫跨訂單服務、庫存服務、支付服務、物流服務——四個**獨立部署、獨立資料庫、甚至不同團隊維護**的服務。要它們跑一次 2PC？意味著支付服務得對訂單服務的 coordinator 開放 prepared 態、鎖著用戶餘額等一個外部 coordinator 的指令。這在服務自治、鬆耦合的微服務哲學下是災難——**沒有人想讓自己的資料庫被別的服務鎖著**。

2. **大規模、長時間的交易**。Google 要對整個網頁索引做增量更新，一筆更新可能碰上百萬行、跨越大量機器、持續數秒到數分鐘。2PC 那種「prepared 期間全程持鎖」會把系統的並發度壓垮——鎖持有時間越長，衝突越多，吞吐越低。

這兩個場景催生了兩條不同的解法。**Saga** 針對第一個：放棄「跨服務的原子性與隔離性」這個奢求，改用「一連串能各自 commit 的本地交易 + 出錯時的補償」，換取服務自治和高可用。**Percolator** 針對第二個：不放棄原子性，但用 MVCC（多版本）避免讀寫互鎖、用一個巧妙的 primary lock 讓 2PC 的決定「一次原子生效」，並把狀態存在容錯的 BigTable 上——於是它既有 2PC 的原子性，又沒有傳統 2PC 的阻塞。

理解這兩條路，你就理解了現代分散式交易的兩大流派：**要嘛放棄一致性換簡單與可用（Saga），要嘛用更精巧的機制在容錯儲存上重建一致性（Percolator/Spanner）**。

## 先建立直覺

先把兩條路的形狀對照出來：

```
   2PC（強一致，阻塞）           Saga（弱一致，補償）              Percolator（強一致，不阻塞）
   ┌──────────────┐            ┌──────────────────┐             ┌──────────────────┐
   │ prepare 全部  │            │ T1 commit         │             │ 2PC，但：          │
   │ 鎖住 → 等決定  │            │ T2 commit         │             │ - MVCC 讀不擋寫    │
   │ 一起 commit   │            │ T3 失敗！          │             │ - primary lock 讓  │
   │              │            │ → C2、C1 補償(反向) │             │   決定原子生效     │
   │ 全程持鎖      │            │ 每步立刻放鎖        │             │ - 狀態存 BigTable │
   │ coord 掛=卡死 │            │ 無跨服務隔離        │             │   (Paxos複製,容錯)│
   └──────────────┘            └──────────────────┘             └──────────────────┘
```

- **Saga** 的直覺是「**能反悔的一連串小步驟**」。它不追求「全體同時 commit」，而是一步一步做、每步立刻 commit（立刻放鎖），如果走到一半失敗，就**反向執行補償動作**把已做的抵銷掉。補償不是資料庫 rollback（那需要沒 commit），而是**語意上的反向操作**：扣了款就退款、訂了位就取消。

- **Percolator** 的直覺是「**2PC 但沒有痛點**」。它保留 2PC 的兩階段，但用三個手術刀把痛點切掉：MVCC 讓讀不必等寫（沒有讀寫互鎖）、primary lock 讓「交易是否 commit」這件事縮成單一一個 cell 的原子寫入（不再有「決定已做未傳達」的窗口）、把所有鎖和資料存在 Paxos 複製的 BigTable 上（單機掛了不丟狀態，不阻塞）。

## Saga：拆長交易 + 補償

### 核心結構

一個 Saga 是一串本地交易 `T1, T2, ..., Tn`，每個 `Ti` 配一個**補償動作** `Ci`。正常時順向執行 `T1→T2→...→Tn`，每個 `Ti` 在自己的服務裡是一筆**獨立、立刻 commit** 的本地交易。若某個 `Tk` 失敗，就**反向**執行已完成步驟的補償 `C(k-1)→...→C1`，把做過的事一件件抵銷。

```
   順向（成功）:  T1 ─► T2 ─► T3 ─► ... ─► Tn   ✔ 全部 commit
   
   順向（Tk 失敗）:
        T1 ─► T2 ─► ... ─► T(k-1) ─► Tk �’‘失敗
                                       │
        C1 ◄─ C2 ◄─ ... ◄─ C(k-1) ◄────┘   反向補償，回到「彷彿沒發生」
```

補償的關鍵性質：`Ci` 必須是 `Ti` 的**語意逆操作**，而且要能在 `Ti` 已經 commit 之後執行（因為 Saga 每步都真的 commit 了，不能靠 DB rollback）。例如 `T2=扣款100`、`C2=退款100`。

### 真跑：Saga 成功 vs 失敗補償

我寫了一個下單 Saga（三步：扣庫存、扣款、訂物流），跑一次全成功、一次第三步失敗觸發補償。完整程式碼在附錄，這是真跑輸出（WSL, Go 1.18.1）：

```
-- Case 1：全部成功（forward-only）--
  T1 扣庫存: stock=4
  T2 扣款: balance=200
  T3 訂物流: booked=true
  結果 ok=true  最終: stock=4 balance=200 booked=true

-- Case 2：第 3 步「訂物流」失敗 -> 反向補償 T2、T1 --
  T1 扣庫存: stock=4
  T2 扣款: balance=200
  !! step "訂物流" 失敗 -> 觸發補償
  C2 補償: 退款 balance=300
  C1 補償: 回補庫存 stock=5
  結果 ok=false  最終: stock=5 balance=300 booked=false (回到起點)
```

Case 2 是重點：第三步失敗後，**反向**執行 `C2`（退款，balance 300 回來）、`C1`（回補庫存，stock 5 回來），最終回到起點。注意補償是**反向順序**——後做的先補償，因為後面的操作可能依賴前面的。

### Saga 的代價：放棄隔離性

Saga 最大的取捨在 Case 2 裡藏著一句話：**中途曾出現 `stock=4, balance=200` 的中間狀態，而這段期間別的交易讀得到它。** 這是 Saga 和 2PC 最本質的差別——**Saga 沒有隔離性（isolation，ACID 的 I）**。

2PC 期間資源被鎖住，外界看不到中間狀態，交易對外是原子的。Saga 每一步立刻 commit、立刻放鎖，所以：

- **髒讀（dirty read）**：`T2` 扣完款、`T3` 還沒跑時，另一個交易可能讀到「已扣款」的中間狀態，並基於它做決定——但這筆交易其實可能整個被補償掉。
- **補償不是時光倒流**：退款把 balance 加回去，但如果中間有別的交易看到了那個「已扣款」狀態、觸發了某個副作用（比如發了封「餘額不足」通知），補償無法收回那封通知。

所以 Saga 的正確心智是：**它保證的是「最終要嘛全做、要嘛全補償（原子性的弱化版）」，但完全不保證過程中的隔離性。** 用 Saga 就要接受業務邏輯得容忍中間狀態被看到。實務上會加一些補丁（semantic lock：在資料上標「pending」旗標讓別人知道這是中間態；commutative updates：讓操作可交換順序），但這些是應用層的工，不是協定給你的。

### orchestration vs choreography

Saga 有兩種協調方式：

- **orchestration（編排）**：一個中央 orchestrator（saga 執行器）明確地一步步呼叫各服務、記錄進度、失敗時發起補償。邏輯集中、好追蹤、好除錯，但 orchestrator 是個要維護的元件。
- **choreography（編舞）**：沒有中央協調者，各服務靠事件（event）互相觸發——訂單服務發「訂單已建」事件，庫存服務訂閱它、扣完庫存發「庫存已扣」事件……失敗事件反向觸發補償。鬆耦合、無單點，但流程散在各處，難追蹤（哪個事件觸發哪個？一個環節壞了很難 debug）。

小流程用 choreography 的鬆耦合，複雜流程用 orchestration 的可觀測性。這跟微服務「事件驅動 vs 顯式編排」的老爭論是同一個。

## Percolator：2PC + 快照隔離 + primary lock

Google 的 Percolator（2010）是為了增量維護網頁索引而生。它建在 **BigTable**（Paxos 複製、容錯的 KV 儲存）之上，要在上面做**跨行的原子交易**，且不能有傳統 2PC 的阻塞。它的三個核心手法：

### 手法一：快照隔離（Snapshot Isolation）+ MVCC

Percolator 給每筆交易兩個時間戳（從一個全域的授時服務 **TSO / timestamp oracle** 拿）：開始時拿 `start_ts`、提交時拿 `commit_ts`。每個資料 cell 存**多版本**（MVCC），版本以 commit_ts 為 key：

```
   key "bal:A"  →  { commit_ts=10 : 300,  commit_ts=25 : 200,  commit_ts=40 : 150 }
                     └── 一個 key 存多個時間版本
```

一筆 `start_ts=30` 的交易讀 `bal:A`，就讀「commit_ts ≤ 30 的最大版本」= commit_ts=25 的 200。這叫**快照讀**：交易看到的是它開始那一刻的一致快照，**讀永遠不會被寫阻塞**（讀舊版本就好，寫的是新版本）。這一刀切掉了 2PC 「prepared 持鎖擋讀」的痛點——MVCC 下讀寫不互斥。

### 手法二：primary lock 讓 commit 原子生效

Percolator 仍是 2PC（prewrite 階段 = prepare，commit 階段 = 決定），但它解決了 2PC「決定已做、未傳達」的原子性窗口。做法：交易的所有寫入裡**選一個 cell 當 primary**，其餘是 secondary。

- **Prewrite（階段一）**：對每個要寫的 cell 放一個 lock，並寫入資料的 pending 版本。secondary 的 lock 裡記著「我的 primary 是哪個 cell」。
- **Commit（階段二）**：**只對 primary cell 做一個原子操作**——把 primary 的 lock 清掉、寫入 commit 記錄。**這一個原子寫入，就是整筆交易的 commit point。** primary 一旦 commit，整筆交易就算 commit 了，即使 client 此刻 crash。secondary 的 lock 之後可以慢慢（lazily）清理。

關鍵在**如何判斷一筆交易到底 commit 了沒**：任何後來的交易碰到一個還鎖著的 secondary，就順著它記錄的 primary 指標，**去看 primary 到底 commit 了沒**——

```
   後來的交易碰到 secondary 的殘留 lock：
      ├─ 順指標查 primary
      ├─ primary 已 commit → 這筆交易成功了 → 幫忙 roll-forward，補上 secondary 的 commit
      └─ primary 還鎖著/沒 commit → 這筆交易失敗/未完成 → 清掉 lock（roll-back）
```

這就是 Percolator 取代 2PC 阻塞的關鍵：**「交易 commit 了沒」被縮成「primary 這一個 cell commit 了沒」這個原子事實**，任何人都能自己去 primary 查明真相並幫忙善後（roll-forward 或 roll-back）。不再需要一個活著的 coordinator 來告知決定——**決定寫在 primary cell 裡，而 primary 存在 Paxos 複製的 BigTable 上，永遠查得到、不會因單機 crash 而失聯**。這就是為什麼 Percolator 不阻塞。

> 對照上一章 2PC 的死穴：那裡的問題是「決定只在 coordinator 記憶體/本地 log 裡，coordinator 掛了就查不到」。Percolator 把決定寫進**容錯儲存的一個原子 cell**，於是「查決定」這件事變得任何人隨時可做——這正是 [Ch 30](./30-distributed-transactions-2pc-3pc.md) 結尾說的「把 coordinator 的決定用共識複製」的具體實現。

### 手法三：狀態全在容錯儲存

lock、資料、commit 記錄全部存在 BigTable（Paxos 複製）。沒有一個 in-memory 的 coordinator 是單點。client（發起交易的那個）就算跑到一半 crash，它的交易狀態完整地留在 BigTable 裡，被後來的交易讀到並善後。**這是 Percolator 和傳統 2PC 最根本的架構差異：把易失的協調狀態，換成持久、容錯、可被任何人查詢的儲存狀態。**

TiDB 的交易層直接實作了 Percolator（叫 Two-Phase Commit 但語意是 Percolator）。代價是：每筆交易要跟 TSO 要時間戳（TSO 成為潛在瓶頸，得做成高可用 + 批次授時）、寫入放大（每個 cell 要寫 lock 再寫 commit）、以及依賴一個全域授時的中心點。

## MVCC + 時間戳排序，通向 Spanner

Percolator 的 MVCC + 全域時間戳，其實是通往**外部一致（external consistency / linearizability）** 的半步。它有快照隔離，但快照隔離**不是** serializable——它有 write skew 這種異常（兩筆交易各讀一致快照、各寫不同 row，合起來違反了某個跨 row 的約束）。而且它的時間戳來自單一 TSO，是個邏輯上的中心點。

Google Spanner（[Ch 39](./39-google-spanner.md)）把這件事推到極致：它也用 MVCC + 時間戳排序 + 2PC，但時間戳來自 **TrueTime**——一套用 GPS + 原子鐘讓每台機器的時鐘誤差有**已知上界**（`TT.now()` 回傳一個區間 `[earliest, latest]`）的硬體時間服務。有了帶界誤差的真實時間，Spanner 能做到 **commit wait**：交易拿到 commit_ts 後，故意等到「TrueTime 確定這個時刻已過去」才對外可見。這保證了**外部一致**：如果交易 T1 在真實時間上先於 T2 完成，那所有觀察者看到的順序也一定是 T1 在 T2 前——不只是某個邏輯順序，而是符合真實牆上時鐘的順序。

一句話串起這條線：**Percolator 用邏輯時間戳（TSO）+ MVCC 做到快照隔離的容錯交易；Spanner 用物理時間戳（TrueTime）+ MVCC + commit wait 把它升級到外部一致的可序列化交易。** 時間戳從邏輯走向物理，是這條演化線的主軸——這也是為什麼 [Ch 4 實體時鐘的謊言](./04-physical-clocks.md) 那一章的鋪陳，會在 Spanner 這裡收成。

### 為什麼快照隔離不夠：write skew 具象化

快照隔離很強，但**不是** serializable，這件事值得用一個具體例子釘死，因為它會咬人。經典的 **write skew**：醫院規定「任何時刻至少要有一位醫生值班」。此刻 Alice 和 Bob 兩位醫生都在值班，兩人各自想請假：

```
   不變量：值班醫生數 ≥ 1（目前 Alice、Bob 都在值班 = 2 人）

   交易 T1（Alice 請假）        交易 T2（Bob 請假）      兩者同時、各讀一致快照
   ─────────────────────       ─────────────────────
   讀快照：值班數=2  ✓ 可請     讀快照：值班數=2  ✓ 可請   ← 各自看到「還有另一人」
   寫：Alice 下班               寫：Bob 下班              ← 各寫不同的 row，不衝突！
   commit ✓                    commit ✓
                    │                    │
                    ▼                    ▼
        結果：Alice、Bob 都下班了，值班數=0 —— 不變量被破壞！
```

問題出在：兩筆交易各自讀的是**開始那一刻的一致快照**（都看到 2 人值班），各自的判斷「還有別人、我可以走」在**自己的快照裡都成立**；它們寫的是**不同的 row**（Alice 那筆 vs Bob 那筆），所以沒有寫寫衝突、快照隔離不會擋。合起來卻違反了跨 row 的不變量。**serializable 會擋下其中一筆（因為任何序列化順序下，後走的那個都會看到只剩 1 人而不能走），但快照隔離不會。** 這就是為什麼「有 MVCC 快照」≠「交易完全隔離」——Percolator 給的是快照隔離，要 serializable 得再加機制（Spanner 用鎖 + commit wait、PostgreSQL 用 SSI）。

### TCC：介於 Saga 和 2PC 之間

還有一條中間路線值得知道：**TCC（Try-Confirm-Cancel）**。每個服務提供三個介面——**Try**（預留資源，像 prepared 但只鎖住預留的那份，不鎖全表）、**Confirm**（確認、真正生效）、**Cancel**（釋放預留）：

```
   正常：  Try(全部) ──► 都成功 ──► Confirm(全部)
   失敗：  Try(部分失敗) ──► Cancel(已 Try 的)   ← 類似 Saga 的補償，但補的是「預留」而非「已生效的操作」
```

TCC 比 Saga 多了「預留」這層：Try 階段扣的是「凍結額度」而非真的扣款，這段期間資源對別人標記為 pending，減少了 Saga「中間狀態外露」的問題（別人看得到「這筆錢被凍結了」而不是看到一個會被補償掉的假餘額）。它又比 2PC 少了全域鎖（只鎖預留的那份、每個服務自主管理）。金融與電商的分散式交易框架（如 Seata 的 TCC 模式）常用它，是「用應用層的預留語意，換一點 Saga 沒有的隔離」的折衷。代價是每個操作都得設計三個介面、且 Confirm/Cancel 必須冪等。

## 對比與取捨

| 面向 | 2PC | Saga | Percolator |
|---|---|---|---|
| 原子性 | 有（強） | 有（弱：最終全做或全補償） | 有（強） |
| **隔離性** | 有（prepared 持鎖） | **無**（中間狀態外露） | 快照隔離（MVCC，非 serializable） |
| coordinator crash | **阻塞** | 不阻塞（saga log 重放續跑） | 不阻塞（決定存容錯儲存） |
| 鎖持有時間 | 整筆交易期間 | 每步極短（立刻放） | prewrite 到 commit（MVCC 讀不受阻） |
| 適用場景 | 同信任域、短交易、少分區 | 微服務、長流程、可容忍無隔離 | 大規模容錯資料庫（TiDB） |
| 複雜度落點 | 協定簡單、運維難（coord HA） | 應用層寫補償邏輯 | 儲存層複雜（TSO、lock 清理） |
| 代表 | XA、PostgreSQL 2PC | 微服務 saga（Axon、Temporal） | Percolator、TiDB |

怎麼選？

- **同一個團隊、同信任域、交易短、分區罕見** → 2PC 夠用，簡單性勝出，用運維手段（coordinator 高可用）壓住阻塞風險。
- **跨服務、跨團隊、要服務自治、能接受「中間狀態被看到 + 補償」** → Saga。這是微服務的預設答案。
- **要在容錯儲存上做大規模、強一致（快照隔離起跳）的跨行交易** → Percolator 這一路。你通常不會自己實作，而是用已經內建它的資料庫（TiDB、CockroachDB 走類似路線）。

## 踩雷集錦

1. **以為 Saga 的補償等於 rollback**——不是。rollback 是資料庫對**未 commit** 交易的內部回滾（沒人看得到）；補償是對**已 commit** 的本地交易做**語意上的反向操作**（退款、取消），過程中的中間狀態別人早就看到了、副作用可能已經發生。補償無法讓時光倒流，只能「盡量抵銷」。設計補償邏輯要假設「中間狀態已被觀察」。

2. **以為 Saga 有隔離性**——Saga 明確地**放棄** isolation。你不能假設「交易跑到一半時外界看不到」。若業務邏輯依賴隔離（例如「扣款和確認訂單必須對外原子」），Saga 直接不適用，或你得在應用層自己補 semantic lock。搞錯這點會寫出「補償跑完了但用戶已經收到錯誤通知/多發了貨」的 bug。

3. **補償動作本身失敗怎麼辦**——這是 Saga 最容易被忽略的黑暗角落。`C2` 退款也可能失敗（支付服務掛了）。所以補償必須**可重試、冪等（idempotent）**——重複執行退款不能退兩次。Saga 執行器要持久化進度、無限重試補償直到成功（或人工介入）。沒把補償做成冪等可重試，一次補償失敗就留下一筆不一致的爛帳。

4. **以為 Percolator 消除了 2PC**——沒有。Percolator **就是** 2PC（prewrite=prepare、commit=決定），它只是用 MVCC + primary lock + 容錯儲存把 2PC 的三個痛點（讀寫互鎖、決定原子性窗口、coordinator 單點）解掉。理解「它是強化版 2PC 而非替代品」，才理解它為什麼還是繼承 2PC 的兩階段結構和寫入放大。

5. **以為快照隔離 = 可序列化**——快照隔離（Percolator 的隔離級別）**不是** serializable，它有 **write skew** 異常：兩筆交易各自讀一致快照、各寫不同的 row，單獨看都合法，合起來卻違反了一個跨 row 的不變量（經典例子：兩個醫生各看到「還有另一個人值班」的快照，於是各自請假，結果沒人值班）。要 serializable 得加額外機制（SSI、或 Spanner 的 commit wait + 鎖）。別把「有 MVCC 快照」當成「交易完全隔離」。

6. **忽略 TSO / TrueTime 的中心點與依賴**——Percolator 依賴一個全域授時（TSO），Spanner 依賴 TrueTime 硬體。TSO 是邏輯中心點，得做成高可用 + 批次授時否則成瓶頸；TrueTime 需要 GPS/原子鐘基礎設施，一般公司沒有（這是 Spanner 難被完全複製的原因之一）。用這類系統前要清楚它們的隱含依賴，不是「免費的強一致」。

## 進階：再往深一層

- **Saga log 的持久化與 recovery**：生產級 Saga 執行器（Temporal、Axon、Cadence）的核心其實是**把 saga 的執行進度持久化成一個 log**——執行到哪一步、每步的結果、是否進入補償。執行器 crash 重啟後，從 log 恢復繼續跑（續向前或續補償）。這跟 2PC coordinator 持久化決定 log 是同一個哲學：**協調狀態必須落盤才能 crash recovery**。Temporal 甚至把整個工作流當成可重放的事件溯源（接 [Ch 42](./42-event-sourcing-cqrs.md)）。

- **CockroachDB 的做法（Parallel Commits）**：CockroachDB 也是 MVCC + 分散式交易，但它用一個叫 **Parallel Commits** 的優化——把 2PC 的兩階段在正常情況下折疊成「一個網路往返」：交易記錄和所有寫入並行提交，只要都成功，交易就 commit，不需要等一個獨立的第二階段。它和 Percolator 都在攻擊「2PC 兩輪往返的延遲」，思路不同但目標一致：在保住原子性的前提下把延遲壓到最低。值得對照讀，看同一個問題的兩種現代解法。

## 本章重點整理

- 2PC 的「阻塞 + 全程鎖」在微服務和大規模長交易下代價太高，催生兩條現代路線。
- **Saga**：長交易拆成一串本地交易 `T1..Tn` + 補償 `C1..Cn`，順向執行、失敗時反向補償。**放棄隔離性**（中間狀態外露、補償是語意反向而非 rollback）換服務自治與可用性，是微服務主力。補償必須冪等可重試。
- **Percolator**（Google/TiDB）：**就是強化版 2PC**——用 MVCC（讀不擋寫、快照隔離）+ primary lock（把「交易 commit 沒」縮成單一 cell 的原子事實，任何人可查、可幫忙善後）+ 容錯儲存（狀態存 Paxos 複製的 BigTable），一併解掉 2PC 的讀寫互鎖、決定原子性窗口、coordinator 單點三個痛點，於是**不阻塞**。
- 快照隔離**不是** serializable（有 write skew）；Percolator 用邏輯時間戳（TSO），Spanner 用物理時間戳（TrueTime）+ commit wait 升級到**外部一致**。時間戳從邏輯走向物理，是這條演化線的主軸（[Ch 39](./39-google-spanner.md) 收成）。
- 選型：同信任域短交易用 2PC；跨服務可容忍無隔離用 Saga；要容錯儲存上的強一致跨行交易用 Percolator 這一路（通常靠現成資料庫）。

## 自我檢核

- [ ] 我能解釋 Saga 的補償**為什麼不是 rollback**，以及「放棄隔離性」在實務上會造成什麼具體問題
- [ ] 我能說出 Saga 補償失敗時的正確處理（冪等 + 可重試 + 持久化進度），以及為什麼補償要**反向**執行
- [ ] 我能解釋 Percolator 的三個手法（MVCC 快照隔離、primary lock、容錯儲存）各解掉 2PC 的哪個痛點
- [ ] 我能說清楚 Percolator「靠 primary cell 判斷交易是否 commit」如何取代 2PC 的阻塞——為什麼任何人都能查明並善後
- [ ] 我能說出快照隔離為什麼不是 serializable（舉一個 write skew 例子）
- [ ] 我能串起「邏輯時間戳（TSO）→ 物理時間戳（TrueTime）+ commit wait → 外部一致」這條演化線，並說出它接到 Ch 39 的哪裡

## 延伸閱讀

### 原始論文

- **[Sagas](https://www.cs.cornell.edu/andru/cs711/2002fa/reading/sagas.pdf)** — Garcia-Molina & Salem, SIGMOD（1987）
  - **這篇說什麼**：Saga 的原始論文，早在 1987 就為「長時間交易持鎖太久」提出拆分 + 補償的想法，比微服務早了三十年
  - **讀哪裡**：Section 1–3（動機與定義）；當年是為單機長交易設計，看它怎麼被微服務時代重新發現
  - **前提**：懂 ACID 交易即可

- **[Large-scale Incremental Processing Using Distributed Transactions and Notifications (Percolator)](https://research.google/pubs/pub36726/)** — Peng & Dabek, OSDI（2010）
  - **這篇說什麼**：Percolator 完整設計。本章的 primary lock、快照隔離、lock 清理全出自這裡
  - **讀哪裡**：Section 2（Design）是核心，尤其 2.2「Transactions」的 prewrite/commit 偽碼與 lock 表格式
  - **前提**：先懂本章的 2PC（[Ch 30](./30-distributed-transactions-2pc-3pc.md)）與 MVCC 概念

- **[Spanner: Google’s Globally-Distributed Database](https://research.google/pubs/pub39966/)** — Corbett et al., OSDI（2012）
  - **這篇說什麼**：TrueTime + commit wait 如何做到外部一致。本章「時間戳從邏輯到物理」那條線的終點，[Ch 39](./39-google-spanner.md) 會細講
  - **讀哪裡**：Section 3（TrueTime）與 Section 4.1.2（commit wait）
  - **前提**：本章 + [Ch 4 實體時鐘](./04-physical-clocks.md)

### 工程視角

- **《Designing Data-Intensive Applications》第 7 章「Transactions」** — Martin Kleppmann
  - **讀哪裡**：「Weak Isolation Levels」一節把快照隔離、write skew、lost update 講到骨子裡，補齊本章對隔離級別的細節
  - **前提**：無

- **[Pattern: Saga](https://microservices.io/patterns/data/saga.html)** — Chris Richardson（microservices.io）
  - **這篇說什麼**：Saga 在微服務落地的 orchestration vs choreography、補償設計的實務指南
  - **讀哪裡**：整頁 + 它連到的 choreography/orchestration 兩個子模式頁
  - **前提**：無，工程導向

---

## 附錄：Saga 示範完整程式碼

本章的 Saga 示範是純本地邏輯（不需 dsim），凸顯執行/補償流程與「無隔離」。`go run .` 即可，上面的輸出就是它在 WSL Go 1.18.1 真跑出來的。

<details>
<summary>點開 main.go（Saga 執行器 + 補償）</summary>

```go
// main.go
package main

import "fmt"

// Saga：長交易拆成一串本地交易 T1..Tn，每個配一個補償動作 C1..Cn。
// 順向執行；某步失敗 -> 反向依序執行已完成步驟的補償。

type step struct {
	name       string
	do         func(*ledger) error // 本地交易
	compensate func(*ledger)       // 補償動作（語意上的反向，非 DB rollback）
}

type ledger struct {
	stock   int
	balance int
	booked  bool
	trace   []string
}

func (l *ledger) log(s string) { l.trace = append(l.trace, s) }

// 執行 saga：failAt 指定第幾步（0-based）故意失敗，-1 = 全成功。
func runSaga(l *ledger, steps []step, failAt int) bool {
	done := []int{}
	for i, s := range steps {
		var err error
		if i == failAt {
			err = fmt.Errorf("step %q 失敗", s.name)
		} else {
			err = s.do(l)
		}
		if err != nil {
			l.log(fmt.Sprintf("  !! %s -> 觸發補償", err))
			for j := len(done) - 1; j >= 0; j-- { // 反向補償已完成步驟
				steps[done[j]].compensate(l)
			}
			return false
		}
		done = append(done, i)
	}
	return true
}

func newOrderSaga() []step {
	return []step{
		{
			name:       "扣庫存",
			do:         func(l *ledger) error { l.stock--; l.log("  T1 扣庫存: stock=" + itoa(l.stock)); return nil },
			compensate: func(l *ledger) { l.stock++; l.log("  C1 補償: 回補庫存 stock=" + itoa(l.stock)) },
		},
		{
			name:       "扣款",
			do:         func(l *ledger) error { l.balance -= 100; l.log("  T2 扣款: balance=" + itoa(l.balance)); return nil },
			compensate: func(l *ledger) { l.balance += 100; l.log("  C2 補償: 退款 balance=" + itoa(l.balance)) },
		},
		{
			name:       "訂物流",
			do:         func(l *ledger) error { l.booked = true; l.log("  T3 訂物流: booked=true"); return nil },
			compensate: func(l *ledger) { l.booked = false; l.log("  C3 補償: 取消物流 booked=false") },
		},
	}
}

func main() {
	fmt.Println("=== Demo：Saga 成功 vs 中途失敗觸發補償 ===")

	fmt.Println("\n-- Case 1：全部成功（forward-only）--")
	l1 := &ledger{stock: 5, balance: 300}
	ok := runSaga(l1, newOrderSaga(), -1)
	for _, t := range l1.trace {
		fmt.Println(t)
	}
	fmt.Printf("  結果 ok=%v  最終: stock=%d balance=%d booked=%v\n", ok, l1.stock, l1.balance, l1.booked)

	fmt.Println("\n-- Case 2：第 3 步「訂物流」失敗 -> 反向補償 T2、T1 --")
	l2 := &ledger{stock: 5, balance: 300}
	ok = runSaga(l2, newOrderSaga(), 2)
	for _, t := range l2.trace {
		fmt.Println(t)
	}
	fmt.Printf("  結果 ok=%v  最終: stock=%d balance=%d booked=%v (回到起點)\n", ok, l2.stock, l2.balance, l2.booked)
	fmt.Println("\n  注意：Case 2 中途曾出現 stock=4、balance=200 的中間狀態，")
	fmt.Println("  這段期間別的交易讀得到它 —— Saga 放棄隔離性（no isolation）。")
}

func itoa(n int) string { // 避免 import strconv，範例自足
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	b := []byte{}
	for n > 0 {
		b = append([]byte{byte('0' + n%10)}, b...)
		n /= 10
	}
	if neg {
		b = append([]byte{'-'}, b...)
	}
	return string(b)
}
```

</details>

Part 4 的原理與系統建構到此。你手上有了 RSM、KV、分片、一致性雜湊、成員偵測、分散式交易——足夠拼出一個真實的容錯分片儲存了。練習 D 就是把這些縫起來：在 dsim 上建一個**分片 KV + shard controller**，做 config 變更下不丟資料、不重複服務的 shard 遷移。

→ [練習 D：分片 KV + Shard Controller](./practice-d-sharded-kv.md)
