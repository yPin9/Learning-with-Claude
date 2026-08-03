# Ch 19 — Multi-Paxos

> **目標**：把 single-decree Paxos（Ch 18，只決定一個值）擴成 Multi-Paxos——對**一整本 log**（一連串值 / 指令）達成共識。理解 distinguished proposer（穩定 leader）如何省掉 Phase 1、讓穩態下每個值只要一趟訊息。然後直面工程界那句名言——「Paxos 難懂、難落地」——把它到底難在哪講清楚（論文只講單值、multi-Paxos 細節留白、成員變更 / 日誌壓縮全沒說）。這就是 Raft（Ch 20）被發明出來的動機。順帶一句話帶過 Flexible Paxos 和 EPaxos。

## 為什麼需要這個？

Ch 18 的 single-decree Paxos 解決了「對一個值達成共識」。但你去看真實系統——一個 KV store、一個複製狀態機——它們要決定的**從來不是一個值**，而是**一長串操作**：`SET x=1`、`SET y=2`、`DELETE x`、`SET x=5`……而且所有副本必須以**完全相同的順序**執行這串操作，狀態才會一致。

這正是 Ch 15 講過的**全序廣播 ≡ 共識**：讓一群機器對「一整串訊息的順序」達成一致。single-decree Paxos 只給了我們「決定第 i 個位置放什麼」的能力，我們需要把它重複無數次，串成一本 log。

```
   single-decree Paxos：決定「一個」值
        └─► Multi-Paxos：對「log 的每個格子」各跑一次 Paxos，串成全序 log

   log index:   0        1        2        3        4     ...
              ┌─────┬─────────┬─────────┬─────────┬─────────┐
   決定值:     │SET  │  SET    │ DELETE  │  SET    │  ...    │
              │x=1  │  y=2    │   x     │  x=5    │         │
              └──┬──┴────┬────┴────┬────┴────┬────┴─────────┘
                 │       │         │         │
              Paxos    Paxos     Paxos     Paxos
              實例#0   實例#1    實例#2    實例#3
                 └───── 每個格子都是一次獨立的 single-decree Paxos ──────┘
```

最笨的做法：每個 log index 開一個完全獨立的 single-decree Paxos 實例。這**能動、safety 正確**，但**慢到不可接受**——每個值都要跑完整兩階段（2 個 RTT），而且多 proposer 競爭時每個實例都可能活鎖。Multi-Paxos 的全部價值，就是把這個「能動但慢」優化成「快且實用」。核心手段只有一個：**選一個穩定的 leader，省掉重複的 Phase 1。**

## 先建立直覺：Phase 1 其實可以攤提

回想 single-decree Paxos 的兩階段：

- **Phase 1（Prepare/Promise）**：proposer 用編號 n 佔坑，打聽「有沒有值已被接受」。
- **Phase 2（Accept/Accepted）**：正式提議值。

關鍵洞察：**Phase 1 的作用是「用編號 n 取得對 acceptor 的領導權，並打聽舊值」——它跟具體要決定哪個值無關。** 那如果一個 proposer 想連續決定 log 上 100 個格子，它需要為每個格子都跑一次 Phase 1 嗎？

不需要。它可以用**一次** Phase 1，取得對**所有** log index 的領導權（Prepare 一個編號 n，宣告「從現在起，所有 index 我都用 n 當家」），然後對每個 index 只跑 Phase 2。這就是 Multi-Paxos 的核心優化：

```
   single-decree（每個值兩階段）：
     值0: [Phase1][Phase2]  值1: [Phase1][Phase2]  值2: [Phase1][Phase2]
          2 RTT              2 RTT                  2 RTT

   Multi-Paxos（穩定 leader 攤提 Phase1）：
     [Phase1 一次搞定所有 index 的領導權]
        └─► 值0:[Phase2]  值1:[Phase2]  值2:[Phase2]  值3:[Phase2] ...
             1 RTT         1 RTT         1 RTT         1 RTT
```

Phase 1 從「每個值一次」攤提成「每個 leader 任期一次」。穩態下——leader 穩定、沒人挑戰它——**每個值只要 1 個 RTT（Phase 2）就能決定**。這就是 Multi-Paxos 實用的原因。

## distinguished proposer：把 leader 選出來

要省掉 Phase 1，前提是**只有一個 proposer 在提案**——否則兩個 proposer 各自 Prepare 不同編號，互相蓋掉對方的領導權，又回到 single-decree 的活鎖。所以 Multi-Paxos 需要一個 **distinguished proposer**（特出提議者），俗稱 **leader**：

> 系統選出一個節點當 leader，**只有它**發起提案。其他節點把客戶端請求轉發給 leader。leader 用一個編號 n 跑一次 Phase 1 取得所有 index 的領導權，之後對每個新值只跑 Phase 2。

這裡有幾件事要想清楚：

### leader 是怎麼選出來的？

**用 Paxos 本身，或一個更輕的機制。** 常見做法：每個節點有個 timeout，逾時沒聽到 leader 心跳就自己嘗試當 leader（用一個更大的編號跑 Phase 1）。誰先成功 Prepare 到多數，誰就是 leader。

**注意這裡的循環**：選 leader 需要達成共識（大家同意誰是 leader），而共識又要靠 leader 來高效跑——這不是雞生蛋悖論，因為**選 leader 只影響 liveness，不影響 safety**。就算同時冒出兩個「自稱 leader」的節點，Paxos 的 safety 骨架（P2c + 兩多數相交，Ch 18）**照樣保證只決定一個值**——最壞情況只是兩個 leader 互搶、暫時無法推進（活鎖），等其中一個放棄，系統就恢復。**safety 從不依賴 leader 唯一，只有 liveness 依賴。** 這是 Paxos 家族的定海神針。

### leader 出問題怎麼辦？

- **leader 當機**：其他節點 timeout 後選新 leader。新 leader 用**更大的編號**跑一次 Phase 1——這次 Phase 1 有實質作用了：它要**打聽舊 leader 留下的殘局**（哪些 index 已經決定、哪些提了一半沒完成），把沒完成的補完。這正是 single-decree 的 P2c 在發揮作用：新 leader 從 Promise 回應裡繼承舊 leader 已接受的值，絕不覆蓋已決定的東西。
- **兩個 leader 並存（腦裂的自稱）**：如上，safety 不受影響，只是暫時互搶。編號大的最終勝出。

## 底層機制：穩態下一個值的完整流程

畫出穩定 leader 下決定一個值的完整訊息流，對比 single-decree：

```
   穩定 leader L（已透過先前一次 Phase 1 取得編號 n 的領導權）
   客戶端請求 "SET x=5" 到達，L 決定放進 log index=42：

   L ── Accept(n, idx=42, "SET x=5") ──►  Acceptor A0, A1, A2（多數）
   L ◄─ Accepted(n, idx=42) ───────────  A0, A1 回覆（湊到多數）
   │
   └─► index 42 決定為 "SET x=5"，回覆客戶端。全程 1 個 RTT。

   對比 single-decree 要 2 個 RTT（Prepare + Accept）。
   Phase 1 去哪了？攤提在「L 當選那一刻的那一次 Phase 1」裡了。
```

幾個 Multi-Paxos 特有的工程細節，論文（Lamport）幾乎沒講清楚，但落地時全會咬人：

1. **log 是有洞的（holes）**：訊息亂序 / 丟失讓某些 index 先決定、某些後決定，log 中間會出現「洞」（index 5、7 決定了，6 還沒）。狀態機必須**按序執行**，所以 index 6 沒補上前，7 不能執行。leader 要主動填洞（對空洞 index 提一個 no-op）。

2. **哪些已經 chosen 了？**：leader 換人時，新 leader 要搞清楚「到哪個 index 為止已經 chosen」。這需要一輪查詢（對每個 index 問 acceptor），論文對此語焉不詳。

3. **firstUnchosenIndex / commit 通知**：acceptor 怎麼知道某個 index 已被多數接受（chosen）？leader 要額外通知。實務中常在下一個 Accept 訊息裡捎帶「我已知 chosen 到 index X」。

4. **日誌無限增長**：log 會一直長，不能無限存。要做 **snapshot（快照）**——把狀態機當前狀態存成快照，丟掉快照點之前的 log。論文完全沒提這個。

看到問題了嗎？**這四件事，每一件都是真實系統必須解決的，而 Paxos 原論文一件都沒好好講。** 這直接引出下一節。

### 一個 log 有洞的具體畫面

「log 有洞」聽起來抽象，畫出來就懂了。leader L 對 index 5、6、7 連發三個 Accept，但 index 6 的 Accepted 回應在網路裡丟了：

```
   log index:   ... │  5   │  6   │  7   │
                     ├──────┼──────┼──────┤
   已 chosen?        │ 是   │ 否!  │ 是   │   ← index 6 是個「洞」
   狀態機執行到:      │  ▲                     ← 卡在 5，不能跳過 6 執行 7
                       └─ 只能執行到這裡

   後果：index 7 明明已 chosen（"DELETE x"），但因為 6 還沒定，
        狀態機不敢執行 7——萬一 6 是 "SET x=99" 呢？順序錯了狀態就錯。
   解法：leader 發現 6 是洞，主動對 index 6 補一個 Accept（重試，
        或若真沒人提過就填一個 no-op），把洞補上，狀態機才能往前走。
```

這個「必須按序執行、洞沒補上就卡住」的紀律，是複製狀態機（Ch 25）的鐵律，也是 Paxos 論文完全沒教、但每個實作都得自己處理的細節。

### 一個 leader 交接的具體畫面

leader 換人時「繼承殘局」也值得畫出來。舊 leader L（編號 n=5）在 index 8 提了 "SET y=3" 給 A0、A1 接受（還沒到多數 A2），然後 L 當機：

```
   L 當機前，index 8 的狀態：
     A0: accepted(n=5, "SET y=3")   A1: accepted(n=5, "SET y=3")   A2: 空
     → "SET y=3" 只有 2 票，在 3-acceptor 下剛好是多數 → 其實已 chosen！
       （但沒人來得及宣告，因為 L 死了）

   新 leader L'（編號 n=6）上任，對 index 8 跑 Phase 1（Prepare n=6）：
     A0、A1 回 Promise，帶上「我接受過 (n=5, 'SET y=3')」
     → L' 被 P2c 逼著，index 8 必須提 "SET y=3"（不能提別的）
     → L' 補完 index 8，"SET y=3" 正式確立，狀態機繼續。
```

看到了嗎——**這正是 single-decree 的 P2c（Ch 18）在 multi-Paxos 裡的作用**：leader 交接時，新 leader 靠 Phase 1 打聽出舊 leader 的殘局，被 P2c 逼著繼承任何「可能已 chosen」的值，絕不覆蓋。Multi-Paxos 的 safety 沒有新東西，全建立在 single-decree 的骨架上——它只是把「決定一個值」重複用在每個 index 上，並在 leader 交接時靠 P2c 把殘局接乾淨。

## 為什麼工程界說「Paxos 難懂、難落地」

這是本章的靈魂，也是理解 Raft 為什麼存在的鑰匙。Paxos 在學術上無懈可擊、在工程上惡名昭彰，原因是一組**教學與規格的空白**：

### 1. 論文只把 single-decree 講清楚

Lamport 的《Paxos Made Simple》漂亮地推導了「決定一個值」。但真實系統要的是 log，而從 single-decree 到 multi-Paxos 的那一大步——leader 選舉細節、log 結構、填洞、commit 追蹤——論文用了不到一頁草草帶過。**最難的工程部分恰恰是留白最多的部分。**

### 2. multi-Paxos 沒有「標準版本」

因為論文留白，每家公司（Google Chubby、微軟、各資料庫）都自己摸索出一套 multi-Paxos，細節各不相同、且大多沒公開。Google 的工程師 Chandra 在《Paxos Made Live》這篇論文裡明白寫道：從「Paxos 演算法」到「能跑的 Paxos 系統」之間，有一道**巨大的鴻溝**，他們填了無數論文沒提的坑（磁碟故障、成員變更、master lease、log 壓縮）。**「Paxos 理論正確」和「Paxos 能上線」是兩回事。**

### 3. 協定本身反直覺，難以建立心智模型

Ch 18 你已經體會過——Paxos 的每一步都在防某個競爭情境，proposer 常提出不是自己想要的值，沒有一個「直觀的故事」串起來。學生能證明它對，卻說不出「它在做什麼」。這種「能證明但沒直覺」的特性，讓它**極難教、極難 debug、極難擴展**。

### 4. 成員變更、日誌壓縮全靠自己發明

叢集要加減機器（成員變更）、log 要壓縮（snapshot）——這兩件生產系統的必需品，Paxos 論文**完全沒有規範**。每個實作者都得自己設計，而這兩件事又極易出 safety bug（成員變更沒做對就腦裂）。

Ongaro 和 Ousterhout（Raft 的作者）做過一個著名的實驗：讓學生分別學 Paxos 和 Raft，考試測理解程度。結果 Raft 顯著勝出。他們把這寫進 Raft 論文的標題——《In Search of an **Understandable** Consensus Algorithm》。**Raft 不是比 Paxos 更快或更安全，它的賣點就是「可理解」**——它把 Multi-Paxos 那些留白的部分（leader 選舉、log 複製、成員變更、snapshot）全部規範清楚，並刻意設計成有直覺的故事（強 leader、log 只從 leader 流向 follower）。

```
   Paxos                              Raft
   ┌────────────────────┐           ┌────────────────────┐
   │ single-decree 嚴謹  │           │ 一開始就是 log 導向 │
   │ multi-Paxos 留白    │  ──────►  │ leader 選舉規範清楚 │
   │ 選舉/填洞/壓縮自理   │  Raft 的  │ log 複製規範清楚    │
   │ 反直覺、難教        │  動機     │ 成員變更/snapshot 有 │
   │ 但 safety 骨架極穩   │           │ 強 leader、有直覺    │
   └────────────────────┘           └────────────────────┘
      「能證明但難落地」                「好懂且可落地」
```

**這就是 Ch 20 開始講 Raft 的全部理由。** Raft 和 Multi-Paxos 的 safety 本質相同（都靠 quorum 相交 + 已提交的不被覆蓋），但 Raft 把工程留白填滿、把心智模型理順了。學過 Paxos 的 P2c，你會發現 Raft 的 safety 規則似曾相識——因為它們防的是同一件事。

## 一句話帶過：Flexible Paxos 與 EPaxos

兩個值得知道名字的變體，深入留給進階：

- **Flexible Paxos（FPaxos）**：一個漂亮的觀察——Paxos 不需要 Phase 1 的 quorum 和 Phase 2 的 quorum 都是「多數」，只需要**任意 Phase 1 quorum 和任意 Phase 2 quorum 相交**即可。於是你可以讓 Phase 2 quorum 更小（更快、更容錯讀寫），代價是 Phase 1 quorum 更大（leader 換人更貴）。它鬆綁了「必須多數」這個過度嚴格的要求。

- **EPaxos（Egalitarian Paxos）**：**去掉固定 leader**，讓任何節點都能提案，靠追蹤指令間的依賴關係，讓無衝突的指令能並行決定（不必全序，只需依賴序）。它在地理分散、衝突少的場景下延遲更低，代價是協定複雜度更高。它挑戰了「Multi-Paxos 必須有單一 leader」這個假設。

這兩個都在告訴你：Paxos 是一個**協定家族**，Ch 18-19 講的是最經典的成員，但它的設計空間遠比一個協定大。

## 對比與取捨

| 面向 | single-decree Paxos | Multi-Paxos（穩定 leader） |
|---|---|---|
| 決定什麼 | 一個值 | 一整本 log |
| 穩態每個值的成本 | 2 RTT（Phase 1 + 2） | **1 RTT**（只 Phase 2） |
| leader | 無（任何 proposer 都能提） | 有 distinguished proposer |
| 活鎖風險 | 高（proposer 互搶） | 低（單 leader，除非 leader 頻繁換） |
| 論文規範完整度 | 高（Paxos Made Simple 講清楚） | **低**（留白多，是難落地主因） |
| safety 依賴 leader 唯一嗎？ | — | **不**（只有 liveness 依賴） |

## 踩雷集錦

1. **「Multi-Paxos 就是把 single-decree 跑很多次，沒別的」**：概念上對，但這個「沒別的」藏著全部的工程地獄——填洞、commit 追蹤、leader 交接時繼承殘局、snapshot。真跑很多次 single-decree 是「能動但慢且不完整」，Multi-Paxos 的價值全在那些論文沒講的優化和補完裡。

2. **「省掉 Phase 1 會破壞 safety」**：不會。Phase 1 只在「leader 當選那一刻」跑一次就夠了——它取得的領導權對所有 index 有效。只要 leader 沒換，後續每個值直接 Phase 2 是安全的，因為沒有別的 proposer 在搶編號。**safety 從沒被省掉，被省掉的只是「重複佇取領導權」的冗餘。** leader 一換，新 leader 立刻補一次 Phase 1。

3. **「有了 leader，就不會腦裂 / 不會有兩個 leader」**：會有兩個「自稱 leader」的節點並存（舊 leader 網路分區、新 leader 已選出）。但這**不會腦裂**——Paxos 的 safety 骨架保證就算兩個 leader 都在提案，也只決定一個值。兩個 leader 只傷 liveness（互搶、變慢），不傷 safety。把「單 leader」當成 safety 的前提，是危險的誤解。

4. **「Paxos 難懂是因為讀者笨 / 論文寫得爛」**：都不完全是。single-decree 的《Paxos Made Simple》其實寫得很清楚。難的是（a）協定本身反直覺（為 safety 犧牲了直覺），（b）multi-Paxos 的工程部分**論文根本沒寫**。Raft 的貢獻不是「把 Paxos 講得更清楚」，是「重新設計一個一開始就把工程部分規範好、且有直覺的協定」。

5. **「Multi-Paxos 和 Raft 是完全不同的東西」**：它們的 **safety 本質相同**（quorum 相交 + 已 chosen/committed 的值不被後來的 leader 覆蓋）。差別在工程包裝與心智模型：Raft 強制「log 只從 leader 流向 follower」「leader 必須有最新 log 才能當選」，把 Multi-Paxos 的自由度砍掉換取可理解性。學透一個，另一個會快很多。

## 進階：再往深一層

- **《Paxos Made Live》** — Chandra, Griffith, Redman（Google, PODC 2007）：Google 工程師寫「把 Paxos 變成 Chubby 這個生產系統」踩過的所有坑——磁碟故障導致 acceptor 失憶、master lease、成員變更、log 壓縮、以及測試的困難。這是「Paxos 難落地」最有說服力的第一手證詞，讀完你會徹底理解為什麼 Raft 要存在。

- **Multi-Paxos 的 leader lease 與線性一致讀**：穩定 leader 讓「讀」也能優化——leader 若持有一個時間租約（lease），期間它確信自己是唯一 leader，就能**不經 Paxos 直接回覆讀請求**（因為沒人能在它租約內改狀態）。這是 Ch 26 線性一致讀的伏筆，也是 lease 依賴時鐘假設、會被時鐘跳變咬到的地方（呼應 Ch 17 的「繞過 FLP 的代價」）。

- **Vertical Paxos / Stoppable Paxos 做成員變更**：Lamport 後來專門寫了論文補「Paxos 怎麼安全地改成員」——因為原論文沒講，而這件事極易出 safety bug（新舊成員集的 quorum 不相交就腦裂）。對比 Raft 的 joint consensus（Ch 23），你會看到兩個家族對「同一個難題」的不同解法。

- **Flexible Paxos 的 quorum 相交本質**：FPaxos 揭示了 Paxos safety 的最小需求其實只是「Phase 1 quorum 與 Phase 2 quorum 相交」，而非「兩者都是多數」。這個觀察讓你重新理解 Ch 18 的「兩多數相交」——多數只是「保證相交」的一種充分條件，不是必要條件。值得讀原論文的那張 quorum 相交示意圖。

## 本章重點整理

- Multi-Paxos = 對 **log 的每個格子**各跑一次 single-decree Paxos，串成全序 log（呼應共識 ≡ 全序廣播）。
- 核心優化：選一個 **distinguished proposer（leader）**，用一次 Phase 1 取得所有 index 的領導權，之後每個值只跑 Phase 2 → **穩態 1 RTT**。
- leader 選舉的循環不是悖論：**選 leader 只影響 liveness，不影響 safety**。兩個「自稱 leader」並存也不腦裂（Paxos safety 骨架保證），只會暫時互搶變慢。
- 工程留白是「Paxos 難落地」的真因：**填洞、commit 追蹤、leader 交接繼承殘局、snapshot、成員變更**——論文全沒好好講，每家自己發明。
- 「Paxos 難懂」= 反直覺（為 safety 犧牲直覺）+ multi-Paxos 規範留白。**Raft 的賣點是「可理解」**——把留白填滿、心智模型理順，safety 本質和 Multi-Paxos 相同。
- Flexible Paxos（鬆綁「必須多數」為「quorum 相交即可」）、EPaxos（去 leader、靠依賴關係並行）是同家族的重要變體。

## 自我檢核

- [ ] 我能解釋 Multi-Paxos 如何用「一次 Phase 1」攤提掉所有後續值的 Phase 1，以及為什麼這安全
- [ ] 我能說明「選 leader 需要共識、共識又靠 leader」為什麼不是悖論（safety vs liveness）
- [ ] 我能說出穩態下決定一個值的訊息流（幾個 RTT、Phase 1 去哪了）
- [ ] 我能列出至少三個「Paxos 論文沒講、但生產系統必須解決」的工程問題
- [ ] 我能講清楚「Paxos 難懂難落地」的真正原因，以及 Raft 到底改善了什麼（不是更快、不是更安全）
- [ ] 我能說明「兩個 leader 並存為什麼不會腦裂」

Paxos 這條線走完了——我們有了 safety 無懈可擊、但工程上留白重重、反直覺的共識家族。下一章開始，我們轉向 Raft：同樣的 safety 骨架，但把 leader 選舉、log 複製、成員變更全部規範清楚、且設計成有直覺的故事。先從它最顯眼的部分——leader election 開始。

→ [Ch 20 Raft ①：Leader Election](./20-raft-leader-election.md)
