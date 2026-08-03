# Ch 44 — 可觀測性與除錯

> **目標**：搞懂為什麼分散式系統的除錯是另一頭猛獸（沒有全域快照、因果散在多節點、log 各說各話），然後掌握四件武器：**分散式追蹤（distributed tracing）**（Dapper/OpenTelemetry 的 trace id + span，接 Ch 5 因果）、**結構化日誌 + 關聯 id（correlation id）**、**metrics（RED/USE）**、以及**一致的全域快照**——用 Chandy-Lamport 演算法在不停系統的情況下照出一張全域狀態。動手：在 `dsim` 上實作 Chandy-Lamport 快照，用「總金額守恆」驗證抓到的全域狀態真的一致。

> **環境**：Go 1.18.1, WSL2 / Linux x86-64。純標準庫。所有輸出以 WSL 實測為準。

## 為什麼需要這個？

Ch 43 教你怎麼**測**分散式系統——大面積掃 bug、判一致性違反。但測試只告訴你「有問題」。生產環境出事時，你要回答的是「問題**在哪**」，而這是完全不同的一件事。

單機除錯你有一堆奢侈品：一個 debugger 可以停住整個程式、看完整的 stack trace、印出此刻所有變數的值。你有**全域快照**——按下暫停，整個世界的狀態攤在你面前。

分散式系統沒有這些。想像一個請求：client 打到 API gateway、gateway 呼叫 auth 服務、auth 查 Redis、gateway 再呼叫 order 服務、order 寫進三個 Raft 節點、其中一個節點又去問 inventory 服務……這個請求的「stack trace」橫跨七八台機器、每台有自己的時鐘、自己的 log 檔。它變慢了，慢在哪一跳？某台機器上出現一個錯誤，是誰觸發的？

三個結構性困難：

1. **沒有全域快照**：你**不能**同時停住所有節點看它們的狀態。你想按暫停，但每台機器各跑各的，等你連上第二台，第一台的狀態早就變了。連「此刻系統的全域狀態是什麼」這個問題都不是 trivially 可回答的（這正是 Chandy-Lamport 要解的）。
2. **因果散在多節點**：一件事的「前因後果」不在一個 log 檔裡，它散在七台機器的七個 log 檔，每個檔用自己的本地時鐘打時間戳。你要把它們拼回一條因果鏈，但實體時鐘會騙你（Ch 4）——A 機的 log 說 12:00:03、B 機說 12:00:01，不代表 B 的事件真的先發生。
3. **log 各說各話**：每台機器的 log 是它的**局部視角**。node1 說「我把 leader 讓給 node2」，node2 說「我沒收到任何 leader 讓位訊息」——兩邊都沒說謊，只是各自看到世界的一半。

> 如果你對「為什麼實體時鐘不能用來排序跨機器事件」還沒把握，回看 [Ch 4 實體時鐘的謊言](./04-physical-clocks.md) 與 [Ch 5 Lamport 邏輯時鐘](./05-lamport-clocks.md)。本章的追蹤與快照都建在「因果 ≠ 時間」這個地基上。

可觀測性（observability）就是為了對抗這三個困難而生的工程實踐：**在系統各處埋下足夠的訊號，讓你能事後重建出跨節點的因果與狀態**。

## 先建立直覺

可觀測性有三根支柱，各補一塊：

```
   一個變慢/出錯的跨服務請求
   ┌─────────────────────────────────────────────────────┐
   │ client → gateway → auth → redis                       │
   │                  ↘ order → raft{n1,n2,n3} → inventory  │
   └─────────────────────────────────────────────────────┘

   Traces（追蹤）：這一個請求走過哪些跳、每跳花多久
       └─ 回答「慢在哪一跳、誰呼叫了誰」——一條請求的因果鏈

   Logs（日誌）：每個節點在每個時刻發生了什麼事（帶 correlation id）
       └─ 回答「那一跳的內部，具體發生了什麼」——事件的細節

   Metrics（指標）：整體的量化趨勢（RED/USE）
       └─ 回答「系統整體健不健康、哪個服務在冒煙」——聚合的統計

   ─────────────────────────────────────────────────────

   全域快照（Chandy-Lamport）：不停系統，照一張「一致的」全域狀態
       └─ 回答「此刻整個系統的狀態是什麼」——單機 debugger 的「按暫停」
```

前三根支柱（traces/logs/metrics，業界俗稱「可觀測性三支柱」）是**事後重建**：埋訊號、出事後把訊號拼回真相。全域快照是另一種東西——它是**主動照相**，用一個巧妙的演算法在分散式系統上模擬出單機 debugger 的「按暫停看全狀態」。四件武器對應四種問題，不能互相取代。

## 分散式追蹤：把一個請求的因果鏈串起來

問題：一個請求橫跨七台機器，你怎麼知道「gateway 上這筆 log」和「inventory 上那筆 log」是**同一個請求**引發的？

答案簡單到近乎作弊：**給每個請求一個全域唯一的 trace id，讓它跟著請求傳遍所有服務**。這就是 Google Dapper（2010 論文）奠定、後來被 OpenTelemetry 標準化的分散式追蹤。

核心兩個概念：

- **trace**：一整個請求的生命週期，有一個唯一的 `trace_id`。
- **span**：這個 trace 裡的一個工作單元（一次服務呼叫、一次 DB 查詢），有自己的 `span_id`，並記錄 `parent_span_id`——指向呼叫它的那個 span。

```
trace_id = abc123（一個請求，貫穿全程）

  span: gateway 處理請求          [span_id=1, parent=root]  120ms ├──────────────┤
    span: 呼叫 auth 服務          [span_id=2, parent=1]      15ms   ├─┤
      span: auth 查 redis         [span_id=3, parent=2]       2ms    ├┤
    span: 呼叫 order 服務         [span_id=4, parent=1]      90ms      ├──────────┤
      span: order 寫 raft         [span_id=5, parent=4]      70ms       ├────────┤ ← 慢在這
        span: raft 問 inventory   [span_id=6, parent=5]      50ms         ├─────┤

一眼看穿：120ms 裡 90ms 花在 order，order 的 90ms 裡 70ms 花在 raft 寫入。
瓶頸定位到 span_id=5。這在沒有 trace 的世界裡，你要人肉對七個 log 檔的時間戳。
```

span 的 `parent_span_id` 構成一棵**因果樹**——這不是巧合，它正是 Ch 5 的因果關係（happens-before）的工程化身。「span A 是 span B 的 parent」就是「A 因果地先於 B」。分散式追蹤本質上是**把 Lamport 的因果偏序，變成一個你能在 UI 上點開來看的火焰圖**。

實作上，trace id 怎麼「跟著請求跑」？靠**context propagation**：每次跨服務呼叫（HTTP header、gRPC metadata），把 `trace_id` + 當前 `span_id` 塞進去帶走。收到的服務讀出來，以它為 parent 開新 span。OpenTelemetry 的 `traceparent` header 就是這個標準格式（W3C Trace Context）。

追蹤的代價與取捨：

- **取樣（sampling）**：每個請求都完整追蹤，資料量會爆（Dapper 論文提到全量追蹤的儲存成本不可行）。實務上取樣——例如只留 1% 的 trace，或「出錯的請求全留、成功的抽樣」（tail-based sampling）。取捨是：取樣率低省成本，但罕見 bug 的 trace 可能剛好沒被抽到。
- **埋點成本**：每個服務都要正確傳播 context、開關 span。漏一個服務沒埋，因果鏈就在那裡斷掉，火焰圖出現一段「黑洞」。

## 結構化日誌與關聯 id

追蹤告訴你「請求走過哪些跳、每跳多久」，但**那一跳內部具體發生了什麼**——為什麼 raft 寫入花了 70ms？是選舉逾時重試了？是某個 follower 慢？——這要靠日誌。

分散式系統的日誌有兩條鐵律：

**鐵律一：結構化（structured）**。別印 `"user 42 failed to write key foo after 3 retries"` 這種人類句子。印**機器可查詢的結構**：

```json
{"ts":"...","level":"error","service":"order","trace_id":"abc123",
 "span_id":"5","node":"raft-n2","event":"write_failed","key":"foo","retries":3,"reason":"leader_lost"}
```

為什麼？因為你要**跨七台機器查詢**。結構化日誌能被 grep/查詢引擎（Loki、Elasticsearch）用欄位過濾：「給我所有 `trace_id=abc123` 的 log，按時間排序」——瞬間把散在七台機器的這個請求的所有 log 拼回一條線。非結構化的句子做不到這件事，你只能人肉 grep 關鍵字。

**鐵律二：帶 correlation id（關聯 id）**。上面那個 `trace_id` 就是關聯 id——它是把「散在多節點的 log 拼回同一個請求」的縫合線。沒有它，node1 的 log 和 node2 的 log 就是兩堆互不相干的句子；有了它，你一個查詢就把同一個請求在所有節點的足跡撈齊。

這兩條律其實是**把追蹤和日誌縫在一起**：日誌帶上 trace 的 `trace_id`/`span_id`，你就能從火焰圖上某個慢 span 一鍵跳到那個 span 產生的所有 log。這是現代可觀測性平台（Grafana、Datadog）的核心體驗——traces 和 logs 靠 correlation id 互相導航。

> 這也呼應 Ch 5：**log 的本地時間戳不可靠**（Ch 4 時鐘會騙人），但 correlation id + 因果關係（span 的 parent）可靠。排序跨節點的 log，靠因果（trace 樹）而非牆上時鐘。

## Metrics：系統整體的體溫

traces 和 logs 是「單一請求」尺度的。但你還需要「整個系統健不健康」的鳥瞰——這是 metrics（指標）：把海量事件聚合成少數幾個數字，畫成隨時間變化的曲線。

兩個經典的指標框架，各管一頭：

- **RED**（給**服務/請求**看）：**R**ate（每秒請求數）、**E**rrors（每秒錯誤數/錯誤率）、**D**uration（延遲分布，看 p50/p99）。回答「這個服務對外表現如何」。order 服務的 p99 延遲從 50ms 跳到 500ms，RED 立刻讓你看到。
- **USE**（給**資源**看）：**U**tilization（使用率）、**S**aturation（飽和度/排隊長度）、**E**rrors（錯誤數）。回答「這個資源（CPU、磁碟、連線池）是不是瓶頸」。Raft 節點的 disk 飽和度飆高，USE 讓你看到寫入慢的根因在磁碟。

metrics 的關鍵性質是**廉價的聚合**——它不記錄每個事件的細節（那是 logs 的事），只記錄統計量（計數、直方圖），所以能長期保存、快速查詢、設告警。取捨：metrics 告訴你「p99 延遲爆了」，但**不告訴你是哪個請求、為什麼**——那要跳回 traces 和 logs。

**三支柱的協作流程**，這是實務除錯的標準動作：

```
1. Metrics 告警：order 服務 p99 延遲從 50ms 飆到 800ms（RED 的 D）
        ↓ 「哪些請求慢？」
2. Traces 定位：撈出慢請求的 trace，火焰圖顯示 90% 時間卡在 raft 寫入 span
        ↓ 「raft 寫入內部發生什麼？」
3. Logs 追因：查那個 span_id 的 log，看到 "leader_lost, re-election, retries=3"
        ↓ 根因：raft 節點頻繁重選舉
4. （若要看全域狀態）快照：照一張快照確認此刻 raft 叢集的 leader/term 狀態
```

Metrics 發現異常 → Traces 定位瓶頸 → Logs 挖出根因。缺一根支柱，這條鏈就斷在某處。

## 底層機制：Chandy-Lamport 一致全域快照

前三支柱是「事後重建」。但有時你想要單機 debugger 那種**主動照相**：此刻，整個分散式系統的全域狀態是什麼？

這看起來不可能——你不能同時停住所有節點。就算你能，「同時」在分散式系統裡根本沒有良好定義（沒有全域時鐘，Ch 4）。而且系統裡的狀態不只在節點上，**還有在通道上飛行的訊息**（in-flight messages）——一筆已送出、還沒送達的轉帳，它的錢既不在 sender 也不在 receiver 手上，它在**通道**上。一張漏掉 in-flight 訊息的快照是不一致的（會憑空少錢）。

Chandy 和 Lamport（1985，就是 Lamport！）給了一個漂亮的演算法：**不停系統，照一張一致的全域快照**。核心道具是一個特殊訊息——**marker（標記）**。

一致性的定義：快照抓到的全域狀態，必須是一個系統**可能真的經歷過**的狀態（即使它可能從未在任何「牆上時刻」真正同時成立）。對我們的轉帳系統，這等價於一個可驗證的不變式：**Σ(各節點快照餘額) + Σ(快照到的 in-flight 金額) = 總金額**。錢不能憑空多或少。

演算法規則（每個節點）：

```
規則 1（發起 / 收到第一個 marker）：
    立刻記錄自己的本地狀態（餘額快照）
    往所有 out-channel 送出 marker
    開始「錄」所有 in-channel 上的訊息

規則 2（已在錄，某 in-channel 的 marker 到了）：
    停止錄這條 channel —— marker 之後的訊息不算在這次快照裡

規則 3（正在錄，某 in-channel 的 marker 還沒到，來了一筆普通訊息）：
    這筆訊息是「快照時還在飛的 in-flight」，記進這條 channel 的狀態

收齊所有 in-channel 的 marker → 本地快照完成
```

marker 的精妙之處：它像一道**沿著每條通道推進的鋒面**，把「快照前」和「快照後」的訊息一刀切開。marker **之前**到達的普通訊息屬於快照（是 in-flight），marker **之後**到達的不屬於。因為 FIFO 通道保證 marker 不會超車它前面的訊息，這一刀切得乾淨。

畫成圖（node0 發起）：

```
時間 →
node0: [記錄餘額80] ══marker══> node1, node2         開始錄 in-channel(1),(2)
                                                     ↑ 途中若收到 1→0 或 2→0 的普通訊息
                                                       且該 channel 的 marker 未到 = in-flight
node1: ...收到 marker══> [記錄餘額105] ══marker══> ...  標記 channel(0) 為空（marker 前無 in-flight）
node2: ...收到 marker══> [記錄餘額105] ══marker══> ...

當每個節點都收齊「所有 in-channel 的 marker」→ 全域快照完成
全域狀態 = Σ本地餘額 + Σ各 channel 記錄到的 in-flight
```

在 `dsim` 上實作。三個節點初始各 100 元（總額 300），互相轉帳讓錢在通道上飛，然後**在錢還在飛的時候**發起快照，最後驗證守恆。關鍵程式碼：

```go
// 規則 1：收到第一個 marker（或發起）—— 記錄本地狀態、往外送 marker、開始錄
func (a *account) beginRecording(net *Net) {
	if a.recording { return }
	a.recording = true
	a.snapBal = a.bal                      // 記錄本地餘額
	for _, p := range a.peers {            // 往所有 out-channel 送 marker
		if p != a.id {
			net.Send(Message{From: a.id, To: p, Payload: Marker{By: a.id}})
		}
	}
}

func (a *account) OnMessage(m Message, net *Net) {
	switch msg := m.Payload.(type) {
	case Transfer:
		a.bal += msg.Amount
		// 規則 3：正在錄、且這條 channel 的 marker 未到 -> 這筆是 in-flight，記進 channel 狀態
		if a.recording && !a.markerSeen[m.From] {
			a.channelMsgs[m.From] += msg.Amount
		}
	case Marker:
		if !a.recording {
			a.beginRecording(net)          // 規則 1：第一次見 marker
			a.markerSeen[m.From] = true
			a.channelMsgs[m.From] = 0       // 收到 marker 的這條 channel：其上無 in-flight
		} else {
			a.markerSeen[m.From] = true     // 規則 2：停止錄這條 channel
		}
		a.maybeFinish()                    // 收齊所有 in-channel 的 marker 就完成
	}
}
```

真跑（WSL, Go 1.18.1，`go run .`，seed=11，latency 2-4）：

```
=== Chandy-Lamport 一致全域快照（三節點互相轉帳，總額守恆）===
  初始：node0=100 node1=100 node2=100，總額=300

  -- 錢還在通道上飛時，node0 發起快照 --
  node0: 開始快照，記錄本地餘額=80，往所有 peer 送 marker
  node1: 開始快照，記錄本地餘額=105，往所有 peer 送 marker
  node2: 開始快照，記錄本地餘額=105，往所有 peer 送 marker
  node2: 快照完成。本地餘額=105，記錄到 in-flight=0
  node1: 快照完成。本地餘額=105，記錄到 in-flight=0
  node0: 快照完成。本地餘額=80，記錄到 in-flight=10

  -- 驗證一致性：Σ(快照本地餘額) + Σ(in-flight) 應等於總額 --
  node0 快照餘額=80，記錄到 in-flight=10
  node1 快照餘額=105，記錄到 in-flight=0
  node2 快照餘額=105，記錄到 in-flight=0

  Σ本地餘額=290 + Σin-flight=10 = 300（總額=300）
  一致：快照抓到的全域狀態滿足守恆——這是一張真正一致的全域快照。
```

看懂這張快照為什麼一致：node0 送出了 20 給 node1（餘額掉到 80），node1 收到那 20、又送 15 給 node2（100+20-15=105），node2 收到 15、又送 10 給 node0（100+15-10=105）。而 node2→node0 的那 10 元，在快照發起時**還在通道上飛**——它被 node0 的 in-flight 記錄抓到了（`in-flight=10`）。於是 `290 + 10 = 300`，一分不多一分不少。

**這裡的關鍵**：node0 記錄餘額時是 80（已扣掉送出的 20），而那筆 in-flight 的 10 元被通道狀態抓到——**沒有這個 in-flight 記錄，快照就會少 10 元、變成不一致**。Chandy-Lamport 的全部價值就在於它**不漏掉通道上飛行的訊息**。這正是「分散式全域狀態 ≠ 各節點本地狀態的簡單加總」——你必須把通道也快照進去。

這個演算法不是玩具。它是 Flink 的 checkpoint 機制（exactly-once 流處理的基石）、分散式死鎖偵測、穩定屬性偵測的底層引擎。你剛親手實作的，是真實流處理系統每隔幾秒就在做的事。

## 對比與取捨

| 武器 | 尺度 | 回答什麼問題 | 主要成本 | 對應章 |
|---|---|---|---|---|
| Traces | 單一請求 | 慢在哪一跳、誰呼叫誰 | 埋點 + 取樣儲存 | 因果 Ch 5 |
| Logs | 單一事件 | 那一跳內部發生什麼 | 儲存量大、要結構化 | Ch 4/5 |
| Metrics | 聚合統計 | 系統整體健不健康 | 低（廉價聚合）| — |
| 全域快照 | 全域瞬間 | 此刻全域狀態是什麼 | 演算法複雜、需 FIFO 通道 | Ch 4/5 |

三支柱互補，不能互相取代：metrics 便宜但沒細節、logs 有細節但量大、traces 有因果但要埋點取樣。全域快照是另一維度——前三者是被動重建歷史，快照是主動照當下。實務上前三者是日常除錯主力，快照用在流處理 checkpoint、死鎖偵測這種「需要一致全域視圖」的特定場景。

## 踩雷集錦

1. **用 log 的本地時間戳排序跨節點事件**：這是最經典的陷阱。A 機 log 說 12:00:03、B 機說 12:00:01，你就以為 B 的事件先發生——錯。兩台機器的時鐘可能差幾百毫秒甚至幾秒（Ch 4）。**排序跨節點事件要靠因果**（trace 的 parent 關係、Lamport 時鐘），不是牆上時鐘。時間戳只在「同一台機器內」可靠。

2. **log 沒帶 correlation id**：每筆 log 都是一座孤島，出事時你只能人肉 grep 關鍵字、猜哪幾筆是同一個請求的。**每筆 log 都帶 trace_id**，這是把散在多節點的 log 拼回請求的唯一縫合線。這條沒做，可觀測性平台的「一鍵撈齊整個請求」就廢了。

3. **以為 100% 全量追蹤可行**：每個請求都完整存 trace，儲存和頻寬會爆（Dapper 論文明說全量不可行）。要取樣。但別無腦均勻取樣——**出錯的請求要全留**（tail-based sampling），不然最需要看的那些 trace 剛好被抽掉，你會在該有資料的時候兩手空空。

4. **快照漏掉 in-flight 訊息**：只記錄各節點的本地狀態、不記通道上飛行的訊息——這樣的快照**不一致**（會憑空少錢/少事件）。分散式全域狀態 = 節點狀態 + 通道狀態，兩者缺一不可。這是 Chandy-Lamport 存在的全部理由，我們的 demo 裡那 10 元 in-flight 就是明證。

5. **Chandy-Lamport 假設 FIFO 通道，你的網路不是**：演算法的正確性依賴「marker 不會超車它前面的訊息」（FIFO）。若你的通道會亂序（UDP、某些訊息中介），marker 可能比它該切開的訊息先到，快照就切錯了（呼應 Ch 3「exactly-once 是迷思」——真實網路會亂序、重複）。用在非 FIFO 通道上要額外處理（例如通道用 TCP 保 FIFO，或改用其他快照演算法）。

6. **metrics 當 logs 用**：想從 metrics 反推「是哪個請求出錯」——做不到。metrics 是聚合統計，它丟掉了個別事件的身分。看到 p99 爆了要跳回 traces/logs 查個案，別想在 metrics 儀表板上找到單一請求的根因。

## 進階：再往深一層

- **exemplars（範例點）**：metrics 和 traces 的縫合技術。一個延遲直方圖的某個高延遲桶，掛上一個「造成這個桶的具體 trace_id」當範例——你在 metrics 儀表板看到 p99 尖峰，直接點進去看那個慢請求的 trace。這是 metrics→traces 導航的關鍵一環，OpenMetrics/Prometheus 已標準化。

- **Flink 的 checkpoint = Chandy-Lamport 的工業化**：Flink 的 exactly-once 流處理，底層就是 Chandy-Lamport 的變體（Asynchronous Barrier Snapshotting）。barrier 就是我們的 marker，沿著資料流推進，把運算子的狀態快照下來。你剛實作的 demo 是它的教科書骨架。去讀 Carbone et al. 的 ABS 論文看它怎麼處理有環的資料流、怎麼和 exactly-once 縫起來。

- **eBPF / 無埋點追蹤**：傳統追蹤要在每個服務手動埋點。eBPF 能在 kernel 層攔截 syscall/網路事件，**不改應用碼**就重建出服務間的呼叫關係（你的 `bpf` 課有這塊）。取捨：無侵入但看不到應用語意（只知道「A 呼叫了 B」，不知道「這是一個下單請求」）。

- **因果剖析（causal profiling）**：Coz 這類工具問一個反直覺的問題——「如果我把這段程式碼加速 X%，整體會快多少？」在分散式系統裡，因果剖析能告訴你「加速哪個 span 對端到端延遲影響最大」，避免你優化了一個不在關鍵路徑上的東西。這比單純看「哪個 span 最慢」更準（最慢的 span 未必在關鍵路徑上）。

## 本章重點整理

- 分散式除錯的三個結構性困難：**沒有全域快照、因果散在多節點、log 各說各話**。單機 debugger 的奢侈品（暫停、全狀態、stack trace）全都沒了。
- **可觀測性三支柱**：Traces（一個請求走過哪些跳、因果鏈）、Logs（單一事件的細節、要結構化 + 帶 correlation id）、Metrics（聚合統計、RED 看服務 / USE 看資源）。三者靠 correlation id 互相導航，缺一根除錯鏈就斷。
- **分散式追蹤 = Lamport 因果偏序的工程化身**：trace id 跟著請求跑（context propagation），span 的 parent 關係構成因果樹。排序跨節點事件靠因果，不靠牆上時鐘（Ch 4/5）。
- **Chandy-Lamport 快照**：用 marker 當「沿通道推進的鋒面」，不停系統照一張一致的全域快照。關鍵是**不漏掉 in-flight 訊息**——全域狀態 = 節點狀態 + 通道狀態。動手驗證了守恆（290+10=300）。
- 它不是玩具：Flink checkpoint、死鎖偵測都建在它上面。但它假設 FIFO 通道。

## 自我檢核

- [ ] 我能說出分散式除錯相對單機除錯，失去了哪三樣奢侈品，各對應本章哪個工具去補。
- [ ] 我能解釋 trace 的 span parent 關係為什麼本質上就是 Ch 5 的 happens-before 因果關係。
- [ ] 我能說出為什麼「用 log 的本地時間戳排序跨節點事件」是錯的，正確該靠什麼。
- [ ] 不看內文，我能複述 metrics→traces→logs 的除錯協作流程（誰發現異常、誰定位、誰追因）。
- [ ] 我能解釋 Chandy-Lamport 的 marker 在做什麼（它切開了什麼），以及為什麼快照必須包含 in-flight 訊息。
- [ ] 我能說出為什麼我們 demo 裡那 10 元 in-flight 是快照一致性的關鍵——漏掉它會怎樣。

## 延伸閱讀

### 論文

- **[Dapper, a Large-Scale Distributed Systems Tracing Infrastructure](https://research.google/pubs/pub36356/)** — Sigelman et al., Google（2010）
  - **說什麼**：分散式追蹤的奠基論文，trace/span/context propagation/取樣全部出自這裡。
  - **讀哪裡**：Section 2（trace 與 span 模型）、Section 3（取樣）最核心。
  - **前提**：懂 Ch 5 因果關係。

- **[Distributed Snapshots: Determining Global States of Distributed Systems](https://lamport.azurewebsites.net/pubs/chandy.pdf)** — Chandy & Lamport（1985）
  - **說什麼**：本章 marker 演算法的原始論文。短、優雅、必讀。
  - **讀哪裡**：全文只有幾頁，演算法與正確性證明都值得逐段讀。
  - **前提**：懂 FIFO 通道、happens-before。你剛實作的 demo 就是它。

- **[Lightweight Asynchronous Snapshots for Distributed Dataflows (Flink ABS)](https://arxiv.org/abs/1506.08603)** — Carbone et al.（2015）
  - **說什麼**：Chandy-Lamport 在 Flink 的工業化，怎麼和 exactly-once 流處理縫起來。
  - **讀哪裡**：Section 3（ABS 演算法）看它和本章 marker 的對應。
  - **前提**：先讀懂 Chandy-Lamport 原始論文。

### 官方文件 / 標準

- **[OpenTelemetry 文件](https://opentelemetry.io/docs/)**
  - **讀哪裡**：Traces 與 Context Propagation 兩節，看 trace id 標準（W3C `traceparent`）長什麼樣、怎麼跨服務傳。
  - **注意**：這是現在業界的事實標準，traces/metrics/logs 三支柱都在它的規範裡。

### 部落格

- **[The RED Method](https://grafana.com/blog/2018/08/02/the-red-method-how-to-instrument-your-services/)** — Tom Wilkie（Grafana）
  - **說什麼**：RED（Rate/Errors/Duration）指標框架怎麼用，配 USE 一起看。
  - **讀哪裡**：整篇不長；重點在「為什麼這三個指標對服務就夠」。

我們現在能測、能觀測、能除錯分散式系統了。最後一章把整門課的血淚濃縮成一份 checklist——那些一再重演、幾乎每個分散式系統都踩過的設計陷阱與反模式，每一條都連回它對應的章。這是你未來設計系統時該貼在牆上的東西。

→ [Ch 45 設計陷阱與反模式](./45-design-pitfalls.md)
