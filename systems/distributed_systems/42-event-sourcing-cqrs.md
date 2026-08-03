# Ch 42 — 事件溯源 / CQRS

> **目標**：把 [Ch 25](./25-replicated-state-machine.md) 複製狀態機（RSM）那個「狀態 = 從初始狀態依序套用一串確定性命令」的核心思想，搬到**應用層架構**上——這就是**事件溯源（event sourcing）**：不儲存「當前狀態」，而是儲存「導致這個狀態的所有事件」，狀態隨時能靠重放（fold）事件流重建。再看它常見的搭檔 **CQRS（Command Query Responsibility Segregation，命令查詢職責分離）**：把「寫」和「讀」拆成兩個模型，讀模型是事件流投影出的、最終一致（[Ch 9](./09-consistency-models.md)）的視圖。我們會用一段真跑的 Go 展示 fold、時間旅行、與快照（接 [Ch 23](./23-raft-membership-snapshot.md)），並誠實列出這套架構的代價——它不是銀彈，用錯地方會把簡單問題搞到極複雜。

> **環境**：Go 1.18.1, WSL2 / Linux x86-64。本章的 fold/snapshot demo 是純標準庫、真跑過的。

## 為什麼需要這個？

先看主流做法的盲點。一般 CRUD 系統，你有一張 `accounts` 表，`balance` 欄位存當前餘額。使用者存款，你 `UPDATE accounts SET balance = balance + 100`。乾淨俐落——**但你剛剛毀滅了資訊**。

`UPDATE` 是一個破壞性操作：新值蓋掉舊值，**中間發生了什麼永遠消失了**。這帶來一連串你可能沒意識到的損失：

- **審計（audit）**：餘額從 500 變成 300，是誰、什麼時候、為什麼？你的表裡沒有答案。金融、醫療、任何受監管的領域，這是硬需求。你被迫另外搭一套 audit log，然後祈禱它跟主表不會不一致。
- **時間旅行**：「上週二下午 3 點這個帳戶餘額是多少？」CRUD 表答不出來——它只有「現在」。
- **除錯**：線上出現一個詭異的狀態，你想知道它怎麼走到這一步的。CRUD 表只給你終點，不給你路徑。
- **衍生新視圖**：業務方突然要「按每小時統計交易額」。你的表沒存歷史交易，只存了餘額，這個報表你生不出來——資訊在 `UPDATE` 那一刻就沒了。

問題的根源是一個被大家默認、卻沒必要的前提：**「資料庫應該存當前狀態」**。事件溯源翻轉這個前提：

> **當前狀態不是要儲存的東西，而是要計算出來的東西。** 真正該儲存的、唯一的真相，是「發生過的所有事件」——那個 append-only、不可變的事件日誌。當前狀態只是「把事件流從頭 fold 一遍」的結果。

你會發現這跟 [Ch 40](./40-kafka-log.md) Kafka「log 是核心資料結構」、[Ch 25](./25-replicated-state-machine.md) RSM「狀態 = 命令序列的確定性結果」是**同一個洞見在不同層次的展開**。事件溯源就是把這個洞見拿來當應用資料模型。

## 先建立直覺

一句話對照兩種世界觀：

```
   傳統 CRUD：            事件溯源：
   儲存「狀態」            儲存「事件」，狀態是算出來的

   accounts 表           事件日誌（append-only，不可變）
   ┌────┬─────────┐     ┌──────────────────────────┐
   │ id │ balance │     │ Opened{owner:Alice}       │ ← 過去發生的事
   ├────┼─────────┤     │ Deposited{amount:100}     │   全部保留、不刪不改
   │ 1  │  120    │     │ Withdrawn{amount:30}      │
   └────┴─────────┘     │ Deposited{amount:50}      │
     只有「現在」         └──────────────────────────┘
     歷史已被 UPDATE 毀滅          │ fold（從頭依序套用）
                                  ▼
                          balance = 0+100-30+50 = 120  ← 「現在」是算出來的
```

核心公式，記住它：

```
   current_state = fold(apply, initial_state, event_stream)

   （跟函數式的 reduce/foldl 一模一樣：
     從初始狀態出發，依序把每個事件「套用」上去，累積出當前狀態）
```

這個公式的威力在於它**保留了計算過程的每一步**。因為事件流還在，你隨時可以：只 fold 前 N 個事件 → 得到歷史某時刻的狀態（時間旅行）；用不同的 `apply` 函數重新 fold → 得到一個全新的視圖（衍生報表）；把 fold 結果快取起來 → 就是「當前狀態」的物化。**狀態成了事件流的一個「視圖」，而不是唯一真相。**

## 動手：event sourcing 的 fold、時間旅行、快照

把上面的直覺變成能跑的 code。帳戶狀態不直接儲存，而是三種事件 fold 出來的。

```go
type Event interface{ apply(*Account) }

type Opened struct{ Owner string }
type Deposited struct{ Amount int }
type Withdrawn struct{ Amount int }

func (e Opened) apply(a *Account)    { a.Owner = e.Owner; a.Open = true }
func (e Deposited) apply(a *Account) { a.Balance += e.Amount }
func (e Withdrawn) apply(a *Account) { a.Balance -= e.Amount }

// fold：從初始狀態依序套用事件流，重建當前狀態。
// 這就是 event sourcing 的核心：state = fold(apply, initial, events)。
func fold(events []Event) Account {
	var a Account
	for _, e := range events {
		e.apply(&a)
	}
	return a
}

// snapshot + tail：不必每次從頭 fold。存一個中途快照，
// 之後只 fold 快照之後的事件。這對應 Ch 23 Raft 的 log snapshot。
func foldFrom(snap Account, tail []Event) Account {
	for _, e := range tail {
		e.apply(&snap)
	}
	return snap
}
```

`main` 裡：唯一真相是那條 append-only 的事件日誌；當前狀態靠 `fold` 算；只 fold 前 2 個事件做時間旅行；再驗證「從快照續 fold」的結果跟「從頭 full fold」完全一致。真跑（WSL, Go 1.18.1）：

```
== 從完整事件流 fold 出當前狀態 ==
  owner=Alice balance=120 open=true

== 時間旅行：只套用前 2 個事件（開戶後、剛存 100）==
  owner=Alice balance=100

== snapshot + tail：從 balance=70 的快照續 fold ==
  owner=Alice balance=120
  與 full fold 一致？ true
```

三件事在輸出裡驗證了事件溯源的三個核心能力：

1. **當前狀態是算出來的**（`balance=120` 由 `0+100-30+50` fold 而來，沒有任何地方「儲存」了 120）。
2. **時間旅行免費**（只 fold 前 2 個事件就得到歷史狀態 `balance=100`，因為過去的事件都還在）。
3. **快照與全量重放結果一致**（`與 full fold 一致？ true`）——這保證了快照這個優化是**安全**的：從中途快照續 fold 跟從頭 fold 得到同一個狀態。這正是 [Ch 23](./23-raft-membership-snapshot.md) Raft snapshot 的核心正確性條件——快照必須等價於「重放到那一點的結果」。

## 快照：解決「事件流無限長」的問題

事件溯源有個顯而易見的效能問題：一個活躍帳戶累積幾百萬個事件，每次要當前餘額都從頭 fold 幾百萬次？太慢。解法跟 [Ch 23](./23-raft-membership-snapshot.md) Raft 一模一樣——**快照（snapshot）**：

```
   事件流：  e0 e1 e2 ... e999 [snapshot@1000] e1000 e1001 ... e1234
                                     │
                    定期把「fold 到此的狀態」存成快照
                                     │
   讀當前狀態 ⇒ 載入最近快照(fold 到 e999 的結果) + 只 fold e1000..e1234
             ⇒ 從 fold 一百萬次降到 fold 幾十次
```

**關鍵正確性條件**（demo 裡驗證過的）：快照必須嚴格等於「從頭 fold 到那個位置的結果」，否則你的優化會悄悄算出錯的狀態。而且——**事件日誌本身通常不刪**（那是唯一真相，也是審計/時間旅行的來源），快照只是加速讀取的快取。這跟 Kafka 的 compaction（[Ch 40](./40-kafka-log.md)）不同：compaction 會丟掉舊事件，事件溯源的快照則是「另存」狀態、保留完整事件流。要不要真的截斷舊事件是一個獨立的、要謹慎的決定（截了就失去那段歷史）。

## CQRS：把讀和寫拆成兩個模型

事件溯源常跟 **CQRS** 一起出現。CQRS 的想法很單純：**寫入用的模型和讀取用的模型，沒理由是同一個。**

傳統系統用一個模型（同一張表、同一個物件）同時服務讀和寫，這強迫兩邊妥協：寫要正規化（避免不一致），讀要反正規化（避免 join）——一個模型很難同時擅長。CQRS 把它們分開：

```
   命令端（Command / 寫）              查詢端（Query / 讀）
   ────────────────────              ───────────────────
   接收命令（意圖）                    服務讀請求
   驗證業務規則                        從「讀模型」直接回答
   產生事件、append 到事件日誌          （預先算好、為查詢優化的視圖）
        │                                  ▲
        │ 事件流                            │ 投影（projection）
        └──────────────────────────────────┘
              事件日誌是兩端之間的橋
              寫端 append 事件 ⇒ 投影器消費事件 ⇒ 更新讀模型
```

具體流程：使用者發一個**命令（command）**如「存款 100」（命令是**意圖**，可能被拒絕）；命令端驗證規則、產生**事件**「已存款 100」（事件是**既成事實**，不可拒絕）、append 到事件日誌。然後一個或多個**投影器（projector）** 消費這條事件流，各自維護一個為特定查詢優化的**讀模型（read model）**——一個給「查餘額」用的簡單 KV、一個給「月報表」用的聚合表、一個給「全文搜尋」用的 Elasticsearch 索引……同一條事件流，投影出任意多個讀視圖。

**這裡藏著一個必須正視的取捨：讀模型是最終一致的（eventually consistent，[Ch 9](./09-consistency-models.md)）。** 事件 append 之後，投影器需要一點時間去消費它、更新讀模型。在那個窗口裡，讀模型還是舊的：

```
   使用者存款 100 ⇒ 事件已 append（寫成功）
        │
        ▼ 但投影器還沒消費這個事件（幾毫秒到幾秒）
   使用者立刻查餘額 ⇒ 讀模型還是舊值！（read-your-writes 被違反）
```

這是 CQRS 最常咬人的地方，也是它跟事件溯源搭配時**最大的心智負擔**。你必須在設計時就決定：哪些讀能容忍陳舊（大部分報表、列表可以）、哪些不能（剛下單後看訂單狀態）。不能容忍的地方，要嘛在寫端同步回傳結果（不走讀模型）、要嘛讓 client 帶著它剛寫入的版本號等讀模型追上。**「寫完立刻讀到舊值」在 CQRS 裡不是 bug，是架構的固有性質**——這正是 [Ch 9](./09-consistency-models.md) 一致性光譜的直接後果，你在架構層選了最終一致，就得處理它。

## 底層機制：與 Kafka 的關聯

事件溯源需要一個「append-only、不可變、可重播、可被多方消費」的事件日誌——這**恰好就是 Kafka（[Ch 40](./40-kafka-log.md)）**。兩者是天作之合：

```
   事件溯源架構常見的實體對應：

   事件日誌      ⇒  Kafka topic（append-only、保留、可重播）
   聚合根的事件流 ⇒  用聚合 ID 當 key ⇒ 同一實體的事件落同一 partition（保序！）
   投影器        ⇒  Kafka consumer group（各自維護一個讀模型）
   重建讀模型    ⇒  新 consumer 從 offset 0 重播整條 topic
   快照          ⇒  可用 compacted topic 存每個聚合的最新狀態
```

**「同一實體的事件用實體 ID 當 key」** 這件事至關重要——回想 [Ch 40](./40-kafka-log.md)，Kafka 只保證單 partition 內有序。事件溯源絕對依賴「同一個帳戶的事件按正確順序 fold」，所以同一聚合根的所有事件必須落同一 partition，這就要求用聚合 ID 當 key。搞錯這點，事件亂序 fold，你的狀態就錯了。

而 [Ch 40](./40-kafka-log.md) 提到「重播歷史、新服務追上狀態」的能力，在事件溯源裡就是**重建讀模型**：想加一個全新的讀視圖？起一個新投影器，從 offset 0 重播整條事件流，它就能把整個歷史投影成新視圖。想修一個投影 bug（讀模型算錯了）？把讀模型清掉、從頭重播、重建。**這是事件溯源最迷人的能力之一——讀模型是可拋棄、可重建的，因為真相在事件流裡。**

## 對比與取捨

| 面向 | 傳統 CRUD | 事件溯源 + CQRS |
|---|---|---|
| 儲存的東西 | 當前狀態（破壞性 UPDATE） | 事件流（append-only，狀態是算出來的） |
| 審計 / 歷史 | 要另外搭，易不一致 | 天生免費（事件流就是完整歷史） |
| 時間旅行 | 做不到 | fold 到任意點即可 |
| 衍生新視圖 | 資訊可能已丟失 | 重播事件流投影出來 |
| 讀寫效能 | 一個模型兩邊妥協 | 各自優化（讀模型為查詢而生） |
| 一致性 | 通常讀寫強一致 | 讀模型**最終一致**（要處理） |
| 複雜度 | 低 | **高**（事件版本演進、最終一致、重播、投影管理） |
| 適合 | 大部分一般業務 | 審計剛需、複雜領域、需多視圖 |

這張表的最後兩列是重點：**事件溯源不是免費升級，它用「顯著更高的複雜度」換「審計/歷史/多視圖/時間旅行」**。如果你的領域不真的需要那些能力，這筆交易是虧的——你會為了用不上的好處，扛下一堆最終一致、事件版本管理的麻煩。

## 踩雷集錦

1. **「事件溯源到處都能用，是更高級的架構」→ 最大的陷阱**。它有明確的複雜度成本，用在**不需要審計/歷史/多視圖**的簡單 CRUD 上是災難——你把一個 `UPDATE balance` 能解決的事，變成事件定義、投影器、讀模型同步、最終一致處理的一整套。**先問「我真的需要完整歷史和時間旅行嗎」，答案是否，就別上事件溯源。** 這是這套架構被濫用最多、也最痛的地方。

2. **「事件跟命令是一回事」→ 概念混淆會讓設計崩壞**。**命令（command）是意圖，可以被拒絕**（「請存款 100」——餘額不足時可拒）；**事件（event）是既成事實，不可拒絕、不可撤銷**（「已存款 100」——它已經發生了，你只能再產生一個「已退款」事件來補償，不能刪掉它）。事件必須用**過去式**命名（`Deposited` 不是 `Deposit`）。把兩者混為一談，你會寫出「可以刪除事件」這種違反事件溯源根本的東西。

3. **「事件的結構以後可以隨便改」→ 事件版本演進是最被低估的長期痛」**。事件是不可變的、且要永久保留——那三年前用舊格式寫的事件，你今天改了事件結構後還 fold 得動嗎？事件溯源系統**必須**從第一天就規劃事件的版本演進（schema evolution）：加欄位要有預設值、改語意要用新事件型別（upcasting）、絕不能假設所有事件都是最新格式。這件事在 demo 階段看不到，上線幾年後會變成主要維護負擔。

4. **「讀模型最終一致沒關係，反正會追上」→ 沒設計好會出用戶可見的 bug**。「存完款立刻看餘額還是舊的」對使用者是明確的錯誤感受。你不能只是接受最終一致，要**主動設計**哪些操作需要 read-your-writes（例如寫端直接回傳結果、或 client 等讀模型追上某個版本），哪些能忍受陳舊。把最終一致當「反正會好」而不處理，就是把架構的複雜度推給使用者體驗買單。

5. **「快照可以隨便存、事件可以隨便刪」→ 會毀掉唯一真相**。快照是讀取加速的**快取**，不是真相——它必須嚴格等於「fold 到該點的結果」，存錯了會算出錯狀態。而事件日誌是唯一真相，**刪事件 = 刪歷史 = 放棄審計和時間旅行**。若真要截斷舊事件（合規、成本），必須清楚知道你正在永久失去那段歷史，且截斷點之後的所有狀態重建都依賴那個截斷點的快照絕對正確。

## 進階：再往深一層

- **event sourcing 就是 RSM 的應用層版**。回頭看 [Ch 25](./25-replicated-state-machine.md)：RSM 說「只要所有副本從同一初始狀態、按同一順序、套用同一串**確定性**命令，就會到同一狀態」。事件溯源是同一個定理的應用層用法——把「命令」換成「事件」、把「多副本一致」換成「狀態可從事件流重建」。**這也意味著事件的 `apply` 必須是確定性的**（不能在 apply 裡呼叫 `time.Now()`、`rand()`、打外部 API），否則重播會得到不同結果——這正是 [Ch 25](./25-replicated-state-machine.md) 反覆強調的「命令必須確定性」在事件溯源裡的翻版。很多事件溯源的詭異 bug，根源都是某個 `apply` 不小心引入了非確定性。

- **Percolator / CDC 也在做類似的事**。[Ch 31](./31-saga-percolator.md) 的 Percolator、以及 CDC（Change Data Capture，把資料庫的變更當事件流輸出）都體現了「把狀態變化建模成事件流」的思想。CDC 尤其是「不改造既有 CRUD 系統，卻想要事件流」的務實路徑——Debezium 這類工具讀資料庫的 WAL（write-ahead log，本身就是一種事件日誌！），把每個 `INSERT/UPDATE/DELETE` 變成事件推進 Kafka。**你會發現「日誌 → 事件 → 投影」這個模式無所不在**：資料庫的 WAL、Raft 的 log、Kafka 的 topic、事件溯源的事件流——都是同一個 [Ch 40](./40-kafka-log.md) 「log 是核心資料結構」的化身。

- **和 Saga 的關聯**：事件溯源的世界裡，跨多個聚合根/服務的一致性通常不用 [Ch 30](./30-distributed-transactions-2pc-3pc.md) 的 2PC（太重、鎖太久），而用 [Ch 31](./31-saga-percolator.md) 的 **Saga**——一連串本地交易 + 補償事件。事件溯源天然適合 Saga：每一步都是產生事件，失敗時就產生**補償事件**（`OrderCancelled` 補償 `OrderPlaced`）。因為事件不可撤銷，Saga 的「補償」不是回滾（rollback），而是「再發一個相反的事件」——這跟事件溯源「事件是既成事實，只能補償不能刪」的哲學完全契合。

## 本章重點整理

- 事件溯源翻轉「資料庫存當前狀態」的預設：**唯一真相是 append-only、不可變的事件流，當前狀態是 `fold(apply, initial, events)` 算出來的**。這是 [Ch 25](./25-replicated-state-machine.md) RSM 與 [Ch 40](./40-kafka-log.md) 「log 是核心」在應用層的展開。
- 它免費換來**審計、時間旅行、衍生新視圖、可重建的除錯路徑**——因為每一步都被事件流保留，沒有被 `UPDATE` 毀滅。
- **快照**（接 [Ch 23](./23-raft-membership-snapshot.md)）解決「事件流無限長、每次從頭 fold 太慢」，前提是快照嚴格等於「fold 到該點的結果」；事件日誌本身通常不刪（是真相）。
- **CQRS** 把讀寫拆成兩個模型：寫端 append 事件、投影器消費事件維護多個為查詢優化的**讀模型**。讀模型是**最終一致的**（[Ch 9](./09-consistency-models.md)），「寫完立刻讀到舊值」是固有性質、要主動設計處理，不是 bug。
- 與 **Kafka（[Ch 40](./40-kafka-log.md)）** 天作之合：事件流=topic、同聚合 ID 當 key 保序、投影器=consumer group、重建讀模型=從 offset 0 重播。
- **代價**：顯著更高的複雜度——事件版本演進、最終一致心智負擔、命令 vs 事件的概念紀律、`apply` 必須確定性。**不需要審計/歷史/多視圖的簡單 CRUD 千萬別上事件溯源**，那是虧本交易。

## 自我檢核

- [ ] 不看筆記，我能寫出事件溯源的核心公式，並解釋「當前狀態是算出來的，不是存起來的」
- [ ] 我能說出事件溯源相對 CRUD 免費換來的四個能力，以及它們為什麼在 CRUD 裡做不到
- [ ] 我能區分**命令**與**事件**（意圖可拒 vs 既成事實不可撤），並說明事件為什麼要用過去式命名
- [ ] 我能解釋 CQRS 的讀模型為什麼最終一致、「寫完讀到舊值」為什麼是固有性質而非 bug、怎麼設計處理
- [ ] 我能說出事件溯源與 [Ch 25](./25-replicated-state-machine.md) RSM 的關係，以及為什麼 `apply` 必須是確定性的
- [ ] 我能講清楚事件溯源怎麼對應到 Kafka 的實體，以及為什麼同聚合的事件要用同一個 key

## 延伸閱讀

### 奠基文章

- **[Event Sourcing](https://martinfowler.com/eaaDev/EventSourcing.html)** 與 **[CQRS](https://martinfowler.com/bliki/CQRS.html)** — Martin Fowler
  - **讀哪裡**：兩篇都不長，Event Sourcing 那篇的「building state」與 CQRS 那篇對「何時該用/不該用」的警告
  - **學什麼**：這兩個模式的權威定義，以及 Fowler 對「別過度使用」的明確提醒——對照本章踩雷 #1
  - **注意**：Fowler 自己就強調 CQRS 只在複雜領域划算，多數系統不該用

- **《Designing Data-Intensive Applications》第 11 章** — Martin Kleppmann
  - **讀哪裡**：「Event Sourcing and Change Data Capture」一節，把事件溯源、CDC、log 抽象串成一個統一圖景
  - **學什麼**：本章「event sourcing 是 log 抽象的應用層版」這個大論點的最佳來源，也連回 [Ch 40](./40-kafka-log.md)
  - **前提**：讀懂本章 fold 與 [Ch 40](./40-kafka-log.md) 的 log

### 實作與陷阱

- **[Versioning in an Event Sourced System](https://leanpub.com/esversioning)** — Greg Young
  - **讀哪裡**：事件 schema 演進、upcasting 的章節（免費線上版）
  - **學什麼**：本章踩雷 #3（事件版本演進）的深入處理，這是事件溯源上線後最大的長期痛，值得專門讀
  - **前提**：理解事件不可變、要永久保留

- **[The Log（Ch 40 延伸讀那篇）](https://engineering.linkedin.com/distributed-systems/log-what-every-software-engineer-should-know-about-real-time-datas-unifying-abstraction)** — Jay Kreps
  - **讀哪裡**：「Data integration」與「The relationship to databases」兩節
  - **學什麼**：從更高視角看「事件溯源、CDC、資料庫複製本質同源」，把本 Part（Ch 39-42）的系統剖析收束成一個統一視角
  - **前提**：[Ch 40](./40-kafka-log.md) 讀過

事件溯源把「狀態即事件流的 fold」這個貫穿全課的洞見，落到了應用架構層。本 Part 到此把前面 38 章的原理都對應到了真實系統——Spanner 的時鐘、Kafka 的 log、etcd 的共識服務、事件溯源的日誌。下一章我們轉向工程實務：這些系統怎麼**測試**？分散式 bug 只在特定失敗時序下現形，你怎麼把它逼出來？

→ [Ch 43 測試分散式系統](./43-testing-distributed-systems.md)
