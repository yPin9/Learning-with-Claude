# Ch 30 — 分散式交易：2PC / 3PC

> **目標**：搞懂**原子提交（atomic commit）**問題——一筆交易橫跨多個節點，要嘛全部 commit、要嘛全部 abort，不能有人 commit 有人 abort。我們把 **2PC（兩階段提交）** 逐步拆開，親手在 `dsim` 上跑一次正常提交、再讓 coordinator 在關鍵時刻 crash，**看 participant 怎麼永久卡死**——這就是「2PC 是阻塞協定」的鐵證。接著看 **3PC** 想怎麼救、為什麼在網路分區下仍不安全。最後釐清一件常被搞混的事：**原子提交不等於共識**。

> **環境**：Go 1.18.1, WSL2 / Linux x86-64。純標準庫。

## 為什麼需要這個？

到目前為止我們處理的都是**一個資料項的複製**：Raft 把一串命令複製到多個節點、quorum 把一個 key 寫到多數。但真實系統常常要**同時改動多個不同節點上的資料，而且要一起成功或一起失敗**。

經典例子：轉帳。從 A 帳戶扣 100 元、往 B 帳戶加 100 元。如果 A、B 在不同的資料庫分片（[Ch 27](./27-sharding-partitioning.md)）上——扣款成功、加款卻失敗，錢就憑空消失了；反過來就是憑空多出錢。這兩個操作必須是**原子的**：一起發生，或一起不發生，中間狀態對外不可見。

單機資料庫用交易（transaction）加 WAL（write-ahead log）就能保證原子性——一個 crash recovery 就搞定。但跨節點時，難處回到 [Ch 1](./01-why-distributed-is-hard.md) 的核心：**部分失敗**。你發指令叫 A 節點「準備扣款」，它可能：成功並回覆、成功但回覆丟了、根本沒收到、收到但它自己 crash 了。你**無法確定它到底處於哪個狀態**。在這種不確定下，怎麼還能保證「全體一起 commit 或一起 abort」？

這就是**原子提交問題（atomic commitment problem）**。它跟共識（consensus，[Ch 15](./15-consensus-problem.md)）長得很像，但要求不同——這個微妙的差異，是本章最重要的一課，我們最後會講透。

歷史上第一個廣泛使用的解法是 **2PC（Two-Phase Commit）**，1978 年 Jim Gray 提出，至今仍是 XA 交易、分散式資料庫、微服務 saga 的底層基礎。它簡單、直覺、大部分時候能用——但它有一個致命缺陷：**coordinator 在錯的時間 crash，會讓整個交易永久卡死**。我們會親手把這個卡死跑出來。

## 先建立直覺

原子提交的核心矛盾：**每個 participant 只知道自己願不願意 commit，但沒有人能片面決定全體要不要 commit。** 需要一個匯總機制。

2PC 的心智圖像是**婚禮**：牧師（coordinator）分別問新郎、新娘（participants）「你願意嗎？」——這是第一階段（voting）。只有**兩人都說「我願意」**，牧師才宣布「我宣布你們結為夫妻」——這是第二階段（decision）。任何一方說不，婚禮取消。

```
        階段一：投票（voting / prepare）
   Coordinator ──"你 prepare 好了嗎?"──► Participant A
   Coordinator ──"你 prepare 好了嗎?"──► Participant B
        A ──"YES（我鎖好資源了）"──► Coordinator
        B ──"YES"──► Coordinator

        階段二：決定（decision / commit）
   收齊全 YES → Coordinator 寫下「COMMIT」決定
   Coordinator ──"GLOBAL COMMIT"──► A, B
        A、B 收到 → 真正 commit
```

關鍵在階段一那個 **"YES"** 的含意：participant 說 YES 不是「我 commit 了」，而是「**我保證我能 commit，我已經把資源鎖好、undo/redo log 寫好，只要你叫我 commit 我一定做得到**」。這是一個承諾，不能反悔。這個「已承諾但還沒 commit」的中間態，叫做 **prepared**——它是 2PC 的靈魂，也是它的死穴。

為什麼是死穴？因為一個 participant 進了 prepared 態，它就**把命運交給了 coordinator**：它鎖著資源、不能自己決定 commit（萬一別人投了 NO？）、也不能自己決定 abort（萬一 coordinator 已經告訴別人 commit 了？）。它只能**等 coordinator 告訴它結果**。如果 coordinator 這時候 crash 了、而且遲遲不恢復——這個 participant 就**永遠卡在 prepared 態，鎖著資源動彈不得**。這就是「2PC 是阻塞協定（blocking protocol）」的全部意思。我們等一下會親眼看到。

## 2PC 逐步拆解

把兩個階段拆成 coordinator 和 participant 各自的狀態機。

### 階段一：prepare / vote

```
Coordinator                          Participant
   │ state=Collecting                   │ state=Init
   │──────── Prepare ──────────────────►│
   │                                    │ 檢查：我能 commit 嗎？
   │                                    │ 能 → 鎖資源、寫 undo/redo log
   │                                    │      state=Prepared（危險態！）
   │◄─────── VoteYes ───────────────────│
   │                                    │
   │  或者：不能 → state=Aborted        │
   │◄─────── VoteNo ────────────────────│（一票 NO 就注定全體 abort）
```

### 階段二：decision / commit

```
Coordinator                          Participant
   │ 收齊全部 YES？                      │ state=Prepared（等待中）
   │  是 → 寫下 COMMIT 決定（持久化！）  │
   │  這一刻決定就無法反悔                │
   │──────── GlobalCommit ─────────────►│
   │                                    │ state=Committed，放鎖
   │                                    │
   │ 有任一 NO 或超時？                  │
   │  → 寫下 ABORT 決定                  │
   │──────── GlobalAbort ──────────────►│
   │                                    │ state=Aborted，放鎖
```

有兩個**持久化寫入點**是原子性的關鍵，crash recovery 全靠它們：

1. **participant 投 YES 前，先把 prepared 狀態寫進本地 log**。這樣它 crash 重啟後，知道自己在一筆未決交易裡，會主動去問 coordinator 結果。
2. **coordinator 收齊全票、送出 GlobalCommit 前，先把 COMMIT 決定寫進本地 log**。這是**決定點（commit point）**——一旦寫下，這筆交易就注定 commit，即使 coordinator 立刻 crash，重啟後也會依 log 重送 GlobalCommit。

這兩個持久化點對應單機交易的 WAL：先寫意圖再行動，crash 後靠 log 重放。

## 底層機制：為什麼 2PC 是阻塞協定

現在來看那個死穴。問題出在一個特定的 crash 時序：**coordinator 收齊了全部 YES、寫下 COMMIT 決定、但在把 GlobalCommit 送出去之前就 crash 了**。

```
   時刻 t：coordinator 已收齊 YES，剛寫下「COMMIT」決定
           所有 participant 都在 PREPARED 態，鎖著資源等指令
                          │
   時刻 t+1：coordinator *** CRASH ***（GlobalCommit 一封都沒送出）
                          │
                          ▼
   participant 們的困境：
   ┌────────────────────────────────────────────────┐
   │ 我在 PREPARED。coordinator 不回我。               │
   │                                                  │
   │ 我能自己 commit 嗎？                             │
   │   不行！萬一有別的 participant 投了 NO，          │
   │   coordinator 的決定其實是 ABORT，我 commit 就錯了│
   │                                                  │
   │ 我能自己 abort 嗎？                              │
   │   不行！萬一 coordinator 已經決定 COMMIT、        │
   │   甚至已經告訴某個 participant commit 了，        │
   │   我 abort 就破壞原子性了                         │
   │                                                  │
   │ 我問其他 participant？                           │
   │   他們也都在 PREPARED，跟我一樣不知道 → 集體卡死  │
   │                                                  │
   │ 結論：我只能等 coordinator 復活。無限等。         │
   └────────────────────────────────────────────────┘
```

這就是阻塞的本質：**在 prepared 態，participant 失去了自主決定權，命運完全綁在 coordinator 上。coordinator 在決定點前後 crash 且不恢復，participant 就永遠卡死、資源永遠鎖著。**

問其他 participant 有沒有用？在這個特定時序下沒有——因為**所有** participant 都在 prepared、都不知道 coordinator 的決定。有一種情況有用：如果某個 participant 已經收到了 GlobalCommit/GlobalAbort（coordinator crash 前送出了一部分），那它就能告訴其他人結果。但「coordinator 一封都還沒送出就 crash」這個時序，就是無解——只能乾等 coordinator 帶著它持久化的決定 log 復活。

這不是實作 bug，是 2PC 協定本身的性質。單一 coordinator 就是單點故障（single point of failure），它一倒，卡在 prepared 的交易就懸著。這也是為什麼**2PC 不容錯**——後面會接到怎麼補救。

## 真跑：2PC 的正常提交與阻塞

我在 `dsim` 上實作了 2PC（`twopc.go`），一個 coordinator + 三個 participant。第一個 demo 正常提交，第二個 demo 讓 coordinator 在寫下 COMMIT 決定後、送出 GlobalCommit 前 crash，看 participant 卡多久。完整程式碼在附錄。

真跑（WSL, Go 1.18.1，`go run .`）：

```
=== Demo 1：2PC 正常提交 ===
  coordinator: 開始 txn100，發 PREPARE 給 3 個 participant
  participant1: 收到 prepare -> 投 YES，進入 PREPARED（資源已鎖）
  participant2: 收到 prepare -> 投 YES，進入 PREPARED（資源已鎖）
  participant3: 收到 prepare -> 投 YES，進入 PREPARED（資源已鎖）
  coordinator: 收齊全部 YES -> 決定 GLOBAL COMMIT（已寫入決定 log）
  participant2: 收到 GLOBAL COMMIT -> committed
  participant3: 收到 GLOBAL COMMIT -> committed
  participant1: 收到 GLOBAL COMMIT -> committed
  --- 結果：coordinator=committed p1=committed p2=committed p3=committed ---
```

正常路徑乾淨俐落：三個 participant 都投 YES 進 prepared、coordinator 收齊後決定 commit、全體 committed。原子性成立。

現在關鍵的第二個 demo：

```
=== Demo 2：coordinator 在關鍵時刻 crash -> participant 永久阻塞 ===
  coordinator: 開始 txn200，發 PREPARE 給 3 個 participant
  participant1: 收到 prepare -> 投 YES，進入 PREPARED（資源已鎖）
  participant2: 收到 prepare -> 投 YES，進入 PREPARED（資源已鎖）
  participant3: 收到 prepare -> 投 YES，進入 PREPARED（資源已鎖）
  coordinator: 收齊全部 YES -> 決定 GLOBAL COMMIT（已寫入決定 log）
  coordinator: *** 在送出 GLOBAL COMMIT 之前 CRASH ***
  --- 100+ tick 後結果：coordinator=CRASHED p1=PREPARED(卡住) p2=PREPARED(卡住) p3=PREPARED(卡住) ---
  --- 3/3 個 participant 卡在 PREPARED，資源鎖著、無法 commit 也無法 abort ---
```

**這就是阻塞。** coordinator 已經做出了 COMMIT 決定（它自己知道這筆交易該 commit），但一封 GlobalCommit 都沒送出就掛了。三個 participant 全部卡在 prepared——`twopc.go` 裡它們的 `OnTick` 會週期性發 `DecisionReq` 去問 coordinator 結果（這叫 termination protocol），但 coordinator 已 crash，**這些詢問永遠沒有回音**。跑了 100 多個 tick，三個 participant 依然全部卡在 prepared，資源鎖著，既不能 commit（怕別人 abort）也不能 abort（怕 coordinator 其實已決定 commit）。

這個災難的殺傷力在生產環境是實打實的：那三個 participant 鎖著的 row / 資源，**所有想碰它們的其他交易全部被阻塞**，一筆卡死的分散式交易可以連鎖拖垮一整片。這是 DBA 的惡夢，也是為什麼「2PC 的 coordinator 必須做到高可用」——不然一次 coordinator 當機就是一場事故。

> 注意 demo 裡我讓 crash 發生在「決定已寫下、GlobalCommit 未送出」這個最刁鑽的點。如果 coordinator 在**送出決定之前**（還在收票時）crash，participant 們卡住後可以有一個逃生門：等夠久沒等到決定就一起 abort（因為決定還沒做，abort 是安全的）。真正無解的是「決定已做、未傳達」這個窗口——我們精準地打在這裡。

## 3PC：想解阻塞，但沒真的解

2PC 的阻塞根源是：participant 在 prepared 態，無法從「其他 participant 也在 prepared」這件事推斷出 coordinator 的決定。**3PC（Three-Phase Commit）** 想補這個洞：在 prepare 和 commit 之間插入一個 **pre-commit** 階段。

```
   2PC:  prepare ──────────────► commit
   3PC:  prepare ──► pre-commit ──► commit
                     └── 新增這一階段
```

pre-commit 的用意：coordinator 收齊 YES 後，**先廣播一輪 pre-commit（意思是「大家準備好，我要 commit 了」），收到多數 ack 後才真正廣播 commit**。這樣多了一個資訊：如果一個 participant 進了 pre-commit 態，它就知道「**所有人都投了 YES**」（不然 coordinator 不會發 pre-commit）。於是 coordinator crash 時，participant 們可以協調出決定：

- 有人在 pre-commit 態 → 大家都投過 YES → 安全地一起 commit
- 沒人在 pre-commit 態，都還在 prepared → coordinator 還沒決定 commit → 安全地一起 abort

在**只有節點會 crash、網路可靠（同步網路假設）** 的模型下，3PC 確實是**非阻塞**的：coordinator 掛了，剩下的 participant 靠 pre-commit 態就能自行終結交易，不用乾等。

### 為什麼 3PC 在網路分區下仍不安全

但 3PC 的非阻塞是**建立在同步網路假設上的**——它假設「超時 = 節點死了」。一旦有**網路分區**，這個假設就崩了，3PC 會直接**違反原子性**（比阻塞更糟）：

```
   時序：coordinator 已發 pre-commit，只有 A 收到，B、C 還在 prepared
        然後網路分區：{A} | {B, C}，coordinator 也掛了
                          │
   A 這側：我在 pre-commit → 依 3PC 規則，超時後我 COMMIT
   B、C 這側：我們都在 prepared、彼此問也沒人 pre-commit
             → 依 3PC 規則，超時後我們一起 ABORT
                          │
                          ▼
   A commit 了，B、C abort 了 → 原子性被破壞！同一筆交易有人成有人敗
```

問題出在 3PC 把「超時」當成「節點死亡」的確鑿證據——但分區下，超時可能只是**對方還活著、只是你們之間斷了**。A 以為 B、C 死了所以自己 commit，B、C 以為 coordinator 和 A 死了所以自己 abort，兩邊各自做了不相容的決定。**這正是 FLP（[Ch 16](./16-flp-impossibility.md)）和 CAP（[Ch 10](./10-cap-theorem.md)）的必然結果**：非同步網路（會分區）裡，你無法既保證安全（原子性）又保證活性（非阻塞）。3PC 選了活性，就得在分區時犧牲安全。

所以誠實的結論是：**3PC 在真實世界（會分區的非同步網路）並沒有解決 2PC 的問題，反而用「可能違反原子性」換「不阻塞」，通常更糟**。這也是為什麼 3PC 在實務上幾乎沒人用——大家寧可用 2PC 忍受阻塞（至少不會丟一致性），或者根本換掉這個架構（見下一章）。

## 原子提交 ≠ 共識：最重要的一課

2PC 看起來很像共識（一群節點對「commit 還是 abort」達成一致），但它們的**成功條件不同**，這個差異是理解為什麼 2PC 不容錯的鑰匙：

```
   共識（Consensus，如 Raft）           原子提交（Atomic Commit，如 2PC）
   ┌──────────────────────────┐        ┌──────────────────────────┐
   │ 目標：對「某個值」達成一致  │        │ 目標：對「commit/abort」一致│
   │ 成功條件：多數（majority）  │        │ 成功條件：全票（unanimous） │
   │   同意即可                  │        │   任一 participant 投 NO    │
   │                            │        │   或不可達 → 必須 ABORT     │
   │ 容錯：少數節點掛掉也能繼續  │        │ 不容錯：一個 participant 掛  │
   │   （只要多數活著）          │        │   在 prepared 就卡住全體    │
   └──────────────────────────┘        └──────────────────────────┘
```

關鍵差異在**「全票」vs「多數」**：

- **共識要多數同意**，所以天生容錯——5 節點掛 2 個，剩 3 個是多數，照樣推進。
- **原子提交要全票同意**——只要有一個 participant 投 NO 或無法聯繫，結果就必須是 abort（不能片面 commit，那會破壞原子性）。這個「一票否決」的性質，讓它**無法用多數來容錯**：你不能說「5 個 participant 有 3 個投 YES 就 commit」，因為另外 2 個可能真的無法 commit，強行 commit 就資料不一致了。

這就是 2PC 不容錯的根本原因，也點出了正確的救法：**別讓 2PC 的決定狀態綁在單一 coordinator 上——把 coordinator 本身變成容錯的**。具體做法：用一個 Raft group 來扮演 coordinator，2PC 的每個關鍵決定（收到誰的票、最終 commit/abort 決定）都寫進 Raft log 複製到多數。這樣 coordinator 掛掉一台，Raft 重選出新 leader、從複製的 log 讀出交易狀態、繼續推進——阻塞消失了。

**Google Percolator（[Ch 31](./31-saga-percolator.md)）和 Spanner（[Ch 39](./39-google-spanner.md)）走的正是這條路**：2PC 負責跨分片的原子提交，但每個分片的狀態、以及 coordinator 的決定，都用 Paxos/Raft 複製，所以單機 crash 不會讓交易卡死。2PC 和共識不是二選一，而是**分工**：共識讓每個參與方（含 coordinator）本身高可用，2PC 在這些高可用的參與方之上做跨分片原子提交。這是現代分散式資料庫交易層的標準架構。

## 對比與取捨

| 面向 | 2PC | 3PC | 2PC over Raft（Percolator/Spanner） |
|---|---|---|---|
| 階段數 | 2 | 3 | 2（+ 每方內部 Raft） |
| coordinator crash | **阻塞**（卡死 prepared） | 非阻塞（同步網路下） | 不阻塞（Raft 重選 coordinator） |
| 網路分區下 | 阻塞但保原子性 | **可能違反原子性** | 保原子性（少數側停等） |
| 訊息輪數（正常） | 2 輪 | 3 輪 | 2 輪 + Raft 複製延遲 |
| 容錯 | 無（單點） | 部分（僅 crash，非分區） | 有（多數活著即可） |
| 實務採用 | 廣泛（XA、DB） | 幾乎無人用 | 現代分散式 DB 主流 |

一句話總結取捨：2PC 簡單但阻塞、3PC 想解阻塞卻在分區時丟原子性（沒人要），真正的解是**把 coordinator 用共識做成高可用**，接受它多一點延遲換容錯。

## 踩雷集錦

1. **以為 participant 投 YES 就等於 commit 了**——錯。YES 只是**承諾「我能 commit」**，真正 commit 要等第二階段的 GlobalCommit。這中間的 prepared 態，資源已鎖但交易未定，是 2PC 最危險的視窗。搞混這個，你就不理解為什麼 coordinator crash 會卡死——正是因為 participant 停在「已承諾、未執行」的懸空態。

2. **以為 2PC 的阻塞是實作沒寫好、加個 timeout 就能救**——不行。participant 在 prepared 態設 timeout 後**不能安全地自行決定**：自己 commit 怕別人 NO，自己 abort 怕 coordinator 已 commit。timeout 到了它也只能繼續問、繼續等。阻塞是協定的性質，不是實作缺陷。真正的救法是換掉單點 coordinator（用共識做成容錯），不是加 timeout。

3. **以為 3PC 解決了 2PC 的問題**——這是教科書級的誤解。3PC 只在「節點會 crash、但網路可靠（同步）」的假想模型下非阻塞；真實網路會分區，3PC 在分區下會讓一邊 commit 一邊 abort，**違反原子性**，比 2PC 的阻塞更糟。這是 CAP 的直接後果，不是能繞過的。

4. **把原子提交當成共識、以為多數同意就能 commit**——致命混淆。原子提交要**全票**：一個 participant 投 NO 或聯繫不上，就必須全體 abort，不能靠多數硬 commit。這正是 2PC 無法像 Raft 那樣「多數容錯」的原因。理解「全票 vs 多數」才理解為什麼 2PC 天生不容錯。

5. **coordinator 沒把 COMMIT 決定持久化就送 GlobalCommit**——如果 coordinator 送出部分 GlobalCommit 後 crash，重啟時如果它沒有持久化的決定 log，它就不知道自己剛才決定了 commit，可能重啟後誤發 abort——已經 commit 的 participant 和被誤導 abort 的 participant 就不一致了。**決定必須先落盤再送出**（就像 WAL），這是 crash recovery 的命根。

6. **prepared 態鎖住的資源忘了它會拖垮別人**——一個卡在 prepared 的交易，鎖著的 row 會阻塞所有想碰那些 row 的其他交易。單一卡死交易能連鎖癱瘓一大片。這是為什麼 2PC 的 coordinator 高可用不是「nice to have」而是「must have」，以及為什麼很多系統寧可避開分散式交易（用 saga，見下章）。

## 進階：再往深一層

- **presumed abort / presumed commit 優化**：標準 2PC 每筆交易都要 coordinator 和 participant 各寫多次 log、交換多則訊息。實務的 XA 用 **presumed abort**：如果 coordinator 重啟後查不到某交易的決定 log，就「推定它是 abort」——這樣 abort 的交易根本不用寫 log（省掉大量 IO），只有 commit 才需完整記錄。這是把「常見情況（大多數查詢無交易）優化到零成本」的典型手法。

- **2PC 的 heuristic decision（啟發式決定）**：真實 XA 實作（如資料庫的 XA driver）在 participant 卡太久時，允許 DBA **手動**強制它 commit 或 abort（heuristic outcome），解開鎖。代價是這可能和 coordinator 最終的決定衝突，造成不一致——XA 規範專門為此定義了 `XA_HEURCOM` / `XA_HEURRB` 等錯誤碼上報。這是「阻塞太痛，寧可用手動介入換活性、承擔可能不一致」的工程妥協，也再次印證 2PC 阻塞的現實痛感。

- **為什麼 2PC 仍到處都在用**：講了這麼多缺點，2PC 依然是 XA、PostgreSQL 的 `PREPARE TRANSACTION`、以及無數企業系統的基礎。原因很現實：在 coordinator 做得夠可靠（或用共識加持）、交易夠短、分區夠罕見的環境裡，2PC 的簡單性勝過它的風險。它的阻塞是「小機率、但發生就很痛」，而很多系統選擇用運維手段（coordinator HA、監控卡死交易）壓住這個小機率，換取協定的簡單。工程從來不是「用最完美的協定」，而是「在你的失敗模型下選最划算的」。

## 本章重點整理

- **原子提交**：一筆跨多節點的交易，要嘛全體 commit、要嘛全體 abort，中間狀態對外不可見。難處在部分失敗——你無法確定遠端 participant 的真實狀態。
- **2PC** 兩階段：階段一投票（prepare→vote），participant 投 YES 進入 **prepared** 態（已鎖資源、已承諾、未執行）；階段二決定（coordinator 收齊全票才 GlobalCommit）。
- **2PC 是阻塞協定**：coordinator 在「已寫下決定、未送出」的窗口 crash 且不恢復，所有 prepared 的 participant 永久卡死——既不能自己 commit（怕別人 NO）也不能自己 abort（怕已決定 commit）。我們在 dsim 上真跑出這個卡死。
- **3PC** 加 pre-commit 階段，在**同步網路**下非阻塞；但在**網路分區**下會讓一邊 commit 一邊 abort、**違反原子性**，這是 CAP 的必然，所以實務幾乎無人用。
- **原子提交 ≠ 共識**：原子提交要**全票**（一票否決即 abort），所以無法用多數容錯；共識要**多數**，天生容錯。這是 2PC 不容錯的根本原因。
- 正解是**把 coordinator 用共識（Raft/Paxos）做成高可用**，2PC 在高可用的參與方之上做跨分片原子提交——這是 Percolator、Spanner 的架構。

## 自我檢核

- [ ] 我能解釋「原子提交」問題，以及為什麼它比「複製一個值」更難（部分失敗下無法確定遠端狀態）
- [ ] 不看文章，我能畫出 2PC 的兩個階段、說出 participant 投 YES 的**確切含意**（承諾能 commit，不是已 commit）
- [ ] 我能說出 2PC 阻塞的**確切時序**（coordinator 決定已寫、GlobalCommit 未送出時 crash），以及為什麼 prepared 的 participant 此時既不能自己 commit 也不能自己 abort
- [ ] 我能解釋 3PC 的 pre-commit 想解什麼、為什麼它在同步網路下有效、在網路分區下卻會違反原子性
- [ ] 我能說清楚「原子提交要全票、共識要多數」這個差異，以及它為什麼決定了 2PC 不容錯而 Raft 容錯
- [ ] 我能說出把 coordinator 用 Raft 複製後，為什麼 coordinator crash 不再造成阻塞

## 延伸閱讀

### 原始論文與教材

- **[Notes on Data Base Operating Systems](https://jimgray.azurewebsites.net/papers/dbos.pdf)** — Jim Gray（1978）
  - **這篇說什麼**：2PC 的原始出處（Section 5.8「Two-Phase Commit」）。分散式交易的奠基文獻
  - **讀哪裡**：搜「Two-Phase Commit」那節即可，不長；看它怎麼從單機交易推廣到分散式
  - **前提**：懂單機交易與 WAL 的概念

- **《Designing Data-Intensive Applications》第 9 章「Consistency and Consensus」** — Martin Kleppmann（2017）
  - **這篇說什麼**：本課主參考書。「Distributed Transactions and Consensus」一節把 2PC 的阻塞、2PC 與共識的關係講得極清楚，跟本章互補
  - **讀哪裡**：「Atomic Commit and Two-Phase Commit (2PC)」與「Distributed Transactions in Practice」兩小節
  - **前提**：無，這本就是寫給工程師的

- **[Consensus on Transaction Commit](https://www.microsoft.com/en-us/research/publication/consensus-on-transaction-commit/)** — Jim Gray & Leslie Lamport（2006）
  - **這篇說什麼**：由 2PC 的發明者和 Paxos 的發明者合寫，把原子提交和共識的關係講到底，提出 **Paxos Commit**（用 Paxos 讓 coordinator 容錯）——正是本章「把 coordinator 用共識做成高可用」的原始論文
  - **讀哪裡**：Section 2（2PC 回顧）、Section 4（Paxos Commit）是核心
  - **前提**：先懂 Paxos（[Ch 18](./18-paxos-single-decree.md)）與本章的 2PC

### 工程視角

- **[It’s Time to Move on from Two Phase Commit](https://dbmsmusings.blogspot.com/2019/01/its-time-to-move-on-from-two-phase.html)** — Daniel Abadi
  - **這篇說什麼**：資料庫學者論證為什麼現代系統該避開傳統 2PC，以及有哪些替代（決定論式交易、Calvin 等）。對本章「2PC 缺陷」的實務延伸
  - **讀哪裡**：整篇，尤其「availability」與「latency」兩段對 2PC 缺點的量化
  - **前提**：讀完本章即可

---

## 附錄：完整可跑程式碼

把 `dsim/dsim.go` 的 `package dsim` 改成 `package main` 複製到同目錄，跟下面的 `twopc.go`、`main.go` 放一起 `go run .`（做法見 [Ch 0](./00-environment-setup.md)）。上面貼的輸出就是這份程式碼在 WSL Go 1.18.1 真跑出來的。

<details>
<summary>點開 twopc.go（2PC coordinator + participant 狀態機）</summary>

```go
// twopc.go
package main

// 兩階段提交（2PC）：一個 coordinator 協調多個 participant，
// 要嘛全體 commit，要嘛全體 abort。展示它為什麼是阻塞協定。

// ---- 訊息 ----

type Prepare struct{ txn int }      // coordinator -> participant：階段一
type VoteYes struct{ txn int }      // participant -> coordinator：我準備好了
type VoteNo struct{ txn int }       // participant -> coordinator：我要 abort
type GlobalCommit struct{ txn int } // coordinator -> participant：階段二，提交
type GlobalAbort struct{ txn int }  // coordinator -> participant：階段二，中止
type DecisionReq struct{ txn int }  // participant -> coordinator：結果是什麼？（termination protocol）

type pState int

const (
	pInit     pState = iota // 還沒收到 prepare
	pPrepared               // 已投 yes，鎖住資源，等最終決定（危險態！）
	pCommitted
	pAborted
)

func (s pState) String() string {
	switch s {
	case pInit:
		return "init"
	case pPrepared:
		return "PREPARED(卡住)"
	case pCommitted:
		return "committed"
	default:
		return "aborted"
	}
}

// ---- participant ----

type participant struct {
	id       NodeID
	coord    NodeID
	state    pState
	txn      int
	willVote bool // 這個 participant 會投 yes 還是 no（測試用）
	askedAt  int  // 卡在 prepared 時週期性去問 coordinator
	log      func(format string, args ...interface{})
}

func (p *participant) OnMessage(m Message, net *Net) {
	switch msg := m.Payload.(type) {
	case Prepare:
		p.txn = msg.txn
		if p.willVote {
			p.state = pPrepared // 鎖資源、寫 undo/redo log、進入危險態
			if p.log != nil {
				p.log("participant%d: 收到 prepare -> 投 YES，進入 PREPARED（資源已鎖）", p.id)
			}
			net.Send(Message{From: p.id, To: p.coord, Payload: VoteYes{txn: msg.txn}})
		} else {
			p.state = pAborted
			net.Send(Message{From: p.id, To: p.coord, Payload: VoteNo{txn: msg.txn}})
		}
	case GlobalCommit:
		if p.state == pPrepared {
			p.state = pCommitted
			if p.log != nil {
				p.log("participant%d: 收到 GLOBAL COMMIT -> committed", p.id)
			}
		}
	case GlobalAbort:
		if p.state != pCommitted {
			p.state = pAborted
			if p.log != nil {
				p.log("participant%d: 收到 GLOBAL ABORT -> aborted", p.id)
			}
		}
	}
}

func (p *participant) OnTick(now int, net *Net) {
	// termination protocol：卡在 prepared 就週期性問 coordinator 結果。
	// coordinator 若已 crash，這個問永遠沒有回音 —— 這就是阻塞。
	if p.state == pPrepared && now-p.askedAt >= 10 {
		p.askedAt = now
		net.Send(Message{From: p.id, To: p.coord, Payload: DecisionReq{txn: p.txn}})
	}
}

// ---- coordinator ----

type cState int

const (
	cIdle cState = iota
	cCollecting
	cCommitted
	cAborted
)

type coordinator struct {
	id              NodeID
	parts           []NodeID
	state           cState
	txn             int
	votes           map[NodeID]bool // 收到 yes 的 participant
	gotNo           bool
	decision        int  // 0=未定, 1=commit, -1=abort
	crashAfterVotes bool // 測試：收齊票、寫下 commit 決定後、送出前就 crash
	crashed         bool
	log             func(format string, args ...interface{})
}

func (c *coordinator) start(txn int, net *Net) {
	c.state = cCollecting
	c.txn = txn
	c.votes = map[NodeID]bool{}
	c.gotNo = false
	if c.log != nil {
		c.log("coordinator: 開始 txn%d，發 PREPARE 給 %d 個 participant", txn, len(c.parts))
	}
	for _, p := range c.parts {
		net.Send(Message{From: c.id, To: p, Payload: Prepare{txn: txn}})
	}
}

func (c *coordinator) OnMessage(m Message, net *Net) {
	if c.crashed {
		return
	}
	switch msg := m.Payload.(type) {
	case VoteYes:
		if c.state != cCollecting {
			return
		}
		c.votes[m.From] = true
		if len(c.votes) == len(c.parts) && !c.gotNo {
			// 全票 yes -> 決定 commit（決定點：先落盤再送出）
			c.decision = 1
			c.state = cCommitted
			if c.log != nil {
				c.log("coordinator: 收齊全部 YES -> 決定 GLOBAL COMMIT（已寫入決定 log）")
			}
			if c.crashAfterVotes {
				// 關鍵時刻 crash：決定已定，但一封 GlobalCommit 都還沒送出。
				c.crashed = true
				net.Crash(c.id)
				if c.log != nil {
					c.log("coordinator: *** 在送出 GLOBAL COMMIT 之前 CRASH ***")
				}
				return
			}
			for _, p := range c.parts {
				net.Send(Message{From: c.id, To: p, Payload: GlobalCommit{txn: c.txn}})
			}
		}
	case VoteNo:
		if c.state != cCollecting {
			return
		}
		c.gotNo = true
		c.decision = -1
		c.state = cAborted
		if c.log != nil {
			c.log("coordinator: 收到 NO -> 決定 GLOBAL ABORT")
		}
		for _, p := range c.parts {
			net.Send(Message{From: c.id, To: p, Payload: GlobalAbort{txn: c.txn}})
		}
	case DecisionReq:
		// participant 卡住來問結果。coordinator 活著就能回答（crash 了就回不了）。
		if c.decision == 1 {
			net.Send(Message{From: c.id, To: m.From, Payload: GlobalCommit{txn: msg.txn}})
		} else if c.decision == -1 {
			net.Send(Message{From: c.id, To: m.From, Payload: GlobalAbort{txn: msg.txn}})
		}
	}
}

func (c *coordinator) OnTick(now int, net *Net) {}
```

</details>

<details>
<summary>點開 main.go（driver：正常提交 + 阻塞）</summary>

```go
// main.go
package main

import "fmt"

func main() {
	fmt.Println("=== Demo 1：2PC 正常提交 ===")
	demoNormalCommit()
	fmt.Println()
	fmt.Println("=== Demo 2：coordinator 在關鍵時刻 crash -> participant 永久阻塞 ===")
	demoBlocking()
}

func buildTxn(net *Net, nParts int, votes []bool, crashAfter bool) (*coordinator, []*participant) {
	coordID := NodeID(0)
	parts := make([]NodeID, nParts)
	for i := 0; i < nParts; i++ {
		parts[i] = NodeID(i + 1)
	}
	c := &coordinator{id: coordID, parts: parts, crashAfterVotes: crashAfter}
	c.log = func(format string, args ...interface{}) { fmt.Printf("  %s\n", fmt.Sprintf(format, args...)) }
	net.Add(coordID, c)
	ps := make([]*participant, nParts)
	for i := 0; i < nParts; i++ {
		p := &participant{id: parts[i], coord: coordID, willVote: votes[i]}
		p.log = func(format string, args ...interface{}) { fmt.Printf("  %s\n", fmt.Sprintf(format, args...)) }
		ps[i] = p
		net.Add(parts[i], p)
	}
	return c, ps
}

func demoNormalCommit() {
	net := NewNet(1)
	net.SetLatency(1, 2)
	c, ps := buildTxn(net, 3, []bool{true, true, true}, false)
	net.Run(2)
	c.start(100, net)
	net.Run(60)
	fmt.Printf("  --- 結果：coordinator=%v ", cStateStr(c.state))
	for _, p := range ps {
		fmt.Printf("p%d=%v ", p.id, p.state)
	}
	fmt.Println("---")
}

func demoBlocking() {
	net := NewNet(1)
	net.SetLatency(1, 2)
	c, ps := buildTxn(net, 3, []bool{true, true, true}, true) // crashAfterVotes=true
	net.Run(2)
	c.start(200, net)
	net.Run(120) // 跑很久，看 participant 卡多久
	fmt.Printf("  --- 100+ tick 後結果：coordinator=CRASHED ")
	for _, p := range ps {
		fmt.Printf("p%d=%v ", p.id, p.state)
	}
	fmt.Println("---")
	blocked := 0
	for _, p := range ps {
		if p.state == pPrepared {
			blocked++
		}
	}
	fmt.Printf("  --- %d/%d 個 participant 卡在 PREPARED，資源鎖著、無法 commit 也無法 abort ---\n", blocked, len(ps))
}

func cStateStr(s cState) string {
	switch s {
	case cIdle:
		return "idle"
	case cCollecting:
		return "collecting"
	case cCommitted:
		return "committed"
	default:
		return "aborted"
	}
}
```

</details>

2PC 讓我們付出「阻塞」和「鎖」的代價換強一致的原子提交。但很多現代系統——尤其微服務——根本不願付這個代價：它們寧可放棄跨服務的強隔離，換更高的可用性。下一章看兩條現代路線：**Saga**（拆長交易 + 補償）和 **Percolator**（2PC + 快照隔離 + 共識加持）。

→ [Ch 31 Saga 與 Percolator](./31-saga-percolator.md)
