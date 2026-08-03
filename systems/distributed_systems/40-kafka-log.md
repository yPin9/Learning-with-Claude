# Ch 40 — Kafka：日誌即系統核心

> **目標**：理解 Kafka 這個系統背後那個看似平凡、實則深刻的論點——**「一條只能在尾端追加的日誌（append-only log），是分散式系統最核心的資料結構」**。這不是 Kafka 特有的把戲，而是把你前面學的 [Ch 7](./07-total-order-broadcast.md) 全序廣播、[Ch 25](./25-replicated-state-machine.md) 複製狀態機（RSM）、[Ch 12](./12-primary-backup-replication.md) 主從複製全部串起來的一個統一視角。我們會親手寫一個極簡 append-only log 並在 WSL 真跑，然後看 Kafka 怎麼在這個抽象上蓋出 partition、offset、consumer group、ISR 複製、以及在 [Ch 3](./03-rpc-and-message-semantics.md) 說「exactly-once 是迷思」的世界裡，Kafka 怎麼在應用層近似 exactly-once。

> **環境**：Go 1.18.1, WSL2 / Linux x86-64。本章的 log/compaction demo 是純標準庫、真跑過的。

## 為什麼需要這個？

先講 Kafka 出現前的世界。要在服務之間傳資料，你有兩條老路，各有死穴：

- **傳統訊息佇列（RabbitMQ、ActiveMQ 那類）**：訊息被消費後就從佇列裡消失。這帶來兩個問題。第一，**只能被消費一次**——如果你有三個下游系統（帳務、分析、稽核）都想吃同一串訂單事件，你得複製三份佇列或搞複雜的 fan-out。第二，**訊息消失就沒了**——想回放歷史、想讓新上線的服務重跑過去一週的資料？做不到，佇列裡早空了。
- **直接 RPC 點對點**：A 服務要通知 B、C、D，就直接呼叫它們。結果是 N×N 的義大利麵——每加一個下游，上游都要改 code；任何一個下游掛了，上游可能被拖垮或阻塞。

LinkedIn 在 2010 年前後被這件事逼瘋。他們有幾百個服務要交換資料，點對點的連線數爆炸，資料管線（data pipeline）成了維運噩夢。Jay Kreps 和團隊退一步問：**這些系統交換的資料，本質上是什麼？** 答案是——**一串「發生過的事」，按時間排好序**。使用者登入、下單、付款、退貨……這些都是**事件（event）**，而事件天生有順序。

於是他們做了一個決定：不要 N×N 的點對點，而是中間放一條**共享的、只能追加的、按順序記錄所有事件的日誌**。所有生產者往這條 log 追加，所有消費者從這條 log 按自己的進度讀。這就是 Kafka。而 Jay Kreps 後來寫的那篇 [The Log](https://engineering.linkedin.com/distributed-systems/log-what-every-software-engineer-should-know-about-real-time-datas-unifying-abstraction) 把這個洞察講成一個更大的論點：**日誌不只是 Kafka 的實作細節，它是分散式系統的統一抽象。**

## 先建立直覺

先把「log」這個詞從你熟悉的東西裡剝出來。它不是 `printf` 出來給人看的那種 log。它是一個**資料結構**：

```
   append-only log（一個 partition）：

   offset:   0      1      2       3          4
           ┌──────┬──────┬───────┬──────────┬────────┐
           │login │view  │login  │purchase  │logout  │  ...尾端可繼續追加
           └──────┴──────┴───────┴──────────┴────────┘
             ▲                                   ▲
        producer 只能從                     consumer 各自記
        這一端追加（append）               一個 offset 游標往後讀

   性質：
   - 只能在尾端追加，不能插入中間、不能改已寫入的（immutable）
   - 每筆有一個永久且唯一的 offset（它在 log 裡的位置）
   - 讀取靠 offset，可從任意位置開始（能重播歷史）
```

這個結構的威力在於它同時是三樣東西：

1. **一個全序（total order）**：offset 給了所有事件一個明確的先後——這正是 [Ch 7](./07-total-order-broadcast.md) 全序廣播想達成的（把訊息在所有節點上排出同一個順序）。log 天生就是全序的載體。
2. **一份權威的歷史**：log 從不刪改，它是「發生過什麼」的唯一真相。誰想知道系統怎麼走到現在的狀態，重放 log 即可。
3. **一個解耦層**：producer 不知道有誰在讀、讀到哪；consumer 不知道誰在寫。兩邊只透過「往 log 寫 / 從 log 讀」互動，時間上、空間上都解耦。

一旦你看懂「log 就是全序 + 歷史 + 解耦」，Kafka 的所有設計都是這個核心的自然推論。

## 動手：一個能跑的極簡 append-only log

概念講再多不如跑一次。我們寫一個最小的 log，只有 `Append(回傳 offset)` 和 `Read(offset)`，再加一個「各自持有 offset 游標」的 consumer——這正是 Kafka consumer 跟傳統佇列最不同的地方。

```go
// Log 是一個 append-only、單一 partition 的日誌。
// offset 從 0 開始單調遞增，是這則紀錄在 log 裡永久且唯一的位置。
type Log struct {
	mu      sync.Mutex
	records []Record
}

func (l *Log) Append(r Record) int64 {
	l.mu.Lock()
	defer l.mu.Unlock()
	off := int64(len(l.records))
	l.records = append(l.records, r) // 只在尾端追加
	return off
}

func (l *Log) Read(off int64) (Record, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	if off < 0 || off >= int64(len(l.records)) {
		return Record{}, errors.New("offset out of range")
	}
	return l.records[off], nil // 靠 offset 隨機讀
}

// Consumer 自己持有 committedOffset——「我讀到哪了」是 consumer 的狀態，
// 不是 log 的狀態。這是 Kafka 跟傳統 message queue 最大的不同。
type Consumer struct {
	name      string
	committed int64
}

func (c *Consumer) Poll(l *Log, max int) []Record {
	var out []Record
	hw := l.HighWatermark()
	for c.committed < hw && len(out) < max {
		r, _ := l.Read(c.committed)
		out = append(out, r)
		c.committed++
	}
	return out
}
```

`main` 裡讓一個 producer 追加五筆事件，再讓兩個獨立的 consumer group（`analytics` 和 `audit`）各自消費同一份 log。真跑（WSL, Go 1.18.1）：

```
== Producer append ==
  offset=0  key=user:1  value=login
  offset=1  key=user:1  value=view=home
  offset=2  key=user:2  value=login
  offset=3  key=user:1  value=purchase=42
  offset=4  key=user:2  value=logout
high watermark = 5

== analytics group 消費（一次讀 3 筆）==
  analytics 讀到 key=user:1  value=login
  analytics 讀到 key=user:1  value=view=home
  analytics 讀到 key=user:2  value=login
  analytics committed offset = 3

== audit group 從頭全讀 ==
  audit 讀到 key=user:1  value=login
  ...（略）...
  audit committed offset = 5

== analytics group 續讀剩下的 ==
  analytics 讀到 key=user:1  value=purchase=42
  analytics 讀到 key=user:2  value=logout
  analytics committed offset = 5

== replay：新 consumer 從 offset 0 重播歷史 ==
  replay 讀到 key=user:1  value=login
  ...（重讀整段歷史，五筆全到）...
```

三件事在這個輸出裡看得一清二楚，而它們正是 Kafka 的靈魂：

1. **offset 由 log 分配、單調遞增、producer 不能自選**。這保證了全序。
2. **`analytics` 讀到 offset 3、`audit` 讀到 offset 5，兩者互不干擾**——因為 offset 是**consumer 的狀態**，不是 log 的狀態。同一份 log 可被任意多方各自消費。傳統佇列做不到這件事。
3. **`replay` 從 offset 0 重讀了整段歷史**——因為 log 不刪改，過去永遠可回放。新上線的服務能把過去所有事件重跑一遍，追上狀態。

這三點就是「log 一次寫、多方讀、可重播」，是 Kafka 相對傳統佇列的全部優勢的來源。

## Partition：把一條 log 切成多條以擴展

一條 log 有個天花板：它是全序的，所以寫入本質上是**序列化**的——所有寫都得排到同一條 log 尾端，一台機器扛。要擴展吞吐，Kafka 把一個 **topic** 切成多個 **partition**，每個 partition 是一條**獨立的 log**：

```
   topic "orders" 切成 3 個 partition：

   partition 0: ┌──┬──┬──┬──┐   ← 各自一條 append-only log
   partition 1: ┌──┬──┬──┬──┐   ← 各自的 offset 空間（都從 0 起算）
   partition 2: ┌──┬──┬──┐      ← 分散在不同 broker，可並行寫

   訊息落哪個 partition？由 key 決定：
     partition = hash(key) mod num_partitions
     ⇒ 同一個 key（例如同一個 user）的所有事件落同一 partition
     ⇒ 同 key 事件保持順序，不同 key 之間不保證順序
```

這是一個關鍵取捨，**必須記牢**：**Kafka 只保證「單一 partition 內的順序」，不保證跨 partition 的順序。** 全域全序（所有事件一條線）擴展不了；Kafka 用「按 key 分片」換吞吐——只要你在乎順序的事件共用一個 key（同一個帳戶的所有操作、同一個訂單的所有狀態變化），它們就落同一 partition、保持順序。跨 key 的順序你本來就不該依賴。這正是 [Ch 27](./27-sharding-partitioning.md) 分片思想在訊息系統上的體現。

## ISR 複製：leader/follower 與持久性取捨

一條 partition 的 log 只存一份就沒有容錯——broker 掛了資料就沒了。Kafka 把每個 partition 複製到多個 broker，這是 [Ch 12](./12-primary-backup-replication.md) 主從複製的直接應用：

```
   partition 0 的複製（replication factor = 3）：

     broker 1: [leader]    ← 所有讀寫都走 leader
     broker 2: [follower]  ┐ 從 leader 拉（fetch）log，跟上進度
     broker 3: [follower]  ┘

   ISR（In-Sync Replicas，同步副本集）：
     = leader + 所有「跟得夠緊」的 follower（落後在門檻內）
     落後太多的 follower 會被踢出 ISR，追上了再加回
```

**ISR** 是理解 Kafka 持久性的鑰匙。leader 不會等**所有**副本、也不是不等，而是等 **ISR 裡的副本**。跟得上的算數，掉隊的先剔除——這避免了「一個慢副本拖垮整個寫入」。搭配 producer 的 `acks` 設定，你就有一條完整的持久性光譜：

```
   producer acks 設定 → 持久性 vs 延遲的旋鈕：

   acks=0    producer 送出就算成功，不等任何確認
             最快、最不可靠。broker 還沒收到就當機 ⇒ 資料無聲丟失

   acks=1    等 leader 寫入本地 log 就算成功，不等 follower
             中間值。leader 寫了但還沒複製給 follower 就掛 ⇒ 可能丟

   acks=all  等 ISR 裡「所有」副本都寫入才算成功
             最慢、最可靠。搭配 min.insync.replicas 保證多份落地
```

`acks=all` 加上 `min.insync.replicas=2`（要求 ISR 至少 2 份才接受寫入）是生產環境要強持久性時的標準組合：一筆寫入回覆成功，代表它已在至少兩個 broker 的 log 上。這時就算 leader 立刻掛，follower 裡還有一份，新 leader 從那份接手，資料不丟。**這本質上就是 quorum（[Ch 13](./13-quorum-replication.md)）——只是 Kafka 用 ISR 動態調整 quorum 成員，而不是固定的 W+R>N。**

> **這對應你學過的 CAP 取捨**：`acks=all` + `min.insync.replicas` 是選一致性/持久性；ISR 全掉到不足時，Kafka 寧可**拒絕寫入**（犧牲可用性）也不讓資料只落一份。這是 [Ch 10](./10-cap-theorem.md) 的 CP 選擇，可以透過設定調到偏 AP（`acks=1` 甚至 `acks=0`）。

## 底層機制：Kafka 怎麼在應用層近似 exactly-once

[Ch 3](./03-rpc-and-message-semantics.md) 花了整章論證「網路上的 exactly-once 是迷思」——你只能有 at-most-once（可能丟）或 at-least-once（可能重複），真正的 exactly-once 送達在有故障的網路上不存在。Kafka 卻宣稱支援 **exactly-once semantics（EOS）**。它在說謊嗎？沒有——它玩的是文字遊戲，而且玩得很聰明：**它不追求「訊息只送達一次」（那不可能），而是追求「效果只發生一次」（effectively-once）**。方法有兩層：

**第一層：冪等 producer（idempotent producer）**。at-least-once 的問題是 producer 送出後沒收到 ack，於是重送，導致 broker 上出現重複。Kafka 給每個 producer 一個 **PID（producer ID）**，並給每個 partition 的每則訊息一個**單調遞增的序號（sequence number）**：

```
   冪等 producer 去重：

   producer(PID=7) 送 partition 0：
     seq=0 (login)   → broker 收到，記錄「PID=7 在 p0 的最高 seq = 0」
     seq=1 (view)    → 收到，最高 seq = 1
     seq=1 (view)    → ★ 重送！broker 一看 seq=1 ≤ 已記錄的 1 ⇒ 丟棄，不重複寫

   broker 端只認「比目前最高 seq 剛好大 1」的訊息，重複與亂序都擋掉
```

這把 at-least-once 的重送在 broker 端**去重**，達到「單一 producer 對單一 partition」的無重複寫入。注意這只解決 producer 重送造成的重複，範圍限單一 partition 的 producer session。

**第二層：交易（transactions）**。真實的 EOS 場景是「consume → process → produce」：從 topic A 讀、處理、寫到 topic B，並更新 A 的 consumer offset。這中間任何一步失敗重試都可能造成重複處理。Kafka 的交易讓你把「寫 topic B 的訊息」和「提交 A 的 offset」包成一個**原子單位**：

```
   Kafka 交易（read-process-write 原子化）：

   beginTransaction()
     從 topic A 讀 offset 5 的訊息、處理
     produce 結果到 topic B（帶交易標記）
     sendOffsetsToTransaction(A: offset=6)   ← offset 提交也進交易
   commitTransaction()   ← 要嘛「B 的寫入 + A 的 offset 前移」全成功
                            要嘛全 abort，不會出現「處理了但 offset 沒動」

   consumer 端設 isolation.level=read_committed
     ⇒ 只讀到已 commit 的交易訊息，abort 的訊息看不到
```

**關鍵洞察**：Kafka 的 EOS 不是靠「保證訊息只送一次」（那違反 [Ch 3](./03-rpc-and-message-semantics.md)），而是靠**去重（冪等）+ 原子提交（交易）**，把重複的**效果**消掉。訊息在網路上可能還是傳了不只一次，但落到 log 上、被下游看到的**效果**只有一次。這跟 [Ch 3](./03-rpc-and-message-semantics.md) 說的「用冪等操作 + 去重把 at-least-once 變成 effectively-once」是同一招，Kafka 只是把它做進了系統層。**理解這點，你就不會被「Kafka 打破了 exactly-once 迷思」這種行銷話術唬住——它沒有打破迷思，它繞過了迷思。**

## Log compaction：把事件流壓成最新狀態

log 只追加不刪，會無限長。Kafka 有兩種回收策略。一種是按時間/大小刪舊（retention），簡單。另一種更有趣：**log compaction**——對同一個 key，只保留**最後一筆**值，前面的舊值清掉：

```go
// 對同一個 key，只保留最後一筆值。value=="" 是 tombstone（刪除標記）。
func compact(in []rec) []rec {
	last := map[string]rec{}
	order := []string{}
	for _, r := range in {
		if _, seen := last[r.key]; !seen {
			order = append(order, r.key)
		}
		last[r.key] = r // 同 key 後蓋前
	}
	var out []rec
	for _, k := range order {
		if last[k].value == "" {
			continue // tombstone：compaction 後該 key 消失
		}
		out = append(out, last[k])
	}
	return out
}
```

真跑（WSL, Go 1.18.1）：

```
== compaction 前（完整事件日誌，6 筆）==
  off=0 user:1  name=Alice
  off=1 user:2  name=Bob
  off=2 user:1  name=Alice2
  off=3 user:3  name=Carol
  off=4 user:2  <tombstone>
  off=5 user:1  name=Alice3

== compaction 後（每個 key 只留最後值，tombstone 消失）==
  off=5 user:1  name=Alice3
  off=3 user:3  name=Carol
```

compaction 的意義很深：**一個「事件日誌」compact 後就變成一份「最新狀態快照」**。`user:1` 經歷 Alice → Alice2 → Alice3，compact 後只剩 Alice3；`user:2` 被 tombstone 刪掉，compact 後消失。這讓一個 compacted topic 可以當**KV store 的變更日誌**用——它同時保有「完整歷史」（compaction 前）和「當前狀態」（compact 後每 key 最新值）。Kafka 自己就用 compacted topic 存 consumer offset 和內部 metadata。這也直接連到下一章的事件溯源（[Ch 42](./42-event-sourcing-cqrs.md)）：狀態就是事件流的 fold。

## 對比與取捨

| 面向 | 傳統訊息佇列（RabbitMQ 等） | Kafka（log-based） |
|---|---|---|
| 訊息消費後 | 從佇列刪除 | 保留（按 retention 或 compaction） |
| 多方消費同資料 | 難（要複製佇列） | 天生支援（各 consumer group 各記 offset） |
| 重播歷史 | 不能 | 能（從任意 offset 重讀） |
| 順序保證 | 通常單佇列內 | 單 partition 內 |
| 擴展寫入 | 較難 | partition 水平切分 |
| 消費進度 | broker 記（誰讀過） | consumer 記（offset 是 consumer 狀態） |
| 適合場景 | 任務佇列、RPC 解耦 | 事件流、資料管線、event sourcing |

不是誰取代誰。要「一個任務被一個 worker 做掉」的工作佇列，RabbitMQ 那類更直接；要「一串事件被多方各自消費、可重播、當資料管線骨幹」，Kafka 的 log 模型才對。**選錯會很痛**：拿 Kafka 當 RPC 用會嫌它繞、當工作佇列用要自己處理很多；拿 RabbitMQ 當事件流骨幹會發現沒法重播、沒法多方消費。

## 踩雷集錦

1. **「Kafka 保證訊息全域有序」→ 錯，只保證單 partition 內有序**。跨 partition 沒有順序保證。如果你把相關事件（同一訂單的建立、付款、出貨）用不同 key 或無 key 隨機分散到不同 partition，consumer 可能看到「出貨」在「付款」前——因為它們在不同 log 上。要順序，就讓相關事件共用一個 key。這是新手最常翻車的地方。

2. **「acks=1 就很安全了」→ 有一個丟資料的窗口**。`acks=1` 只等 leader 寫入本地，不等 follower 複製。leader 寫了、還沒複製出去就當機，新 leader 從沒收到那筆的 follower 選出，那筆**無聲消失**。要強持久性必須 `acks=all` + `min.insync.replicas≥2`。`acks=1` 的「成功」是 leader 的一面之詞。

3. **「Kafka 支援 exactly-once，所以我不用管重複了」→ 危險的誤解**。Kafka 的 EOS 有**明確邊界**：它涵蓋 Kafka 內部的 read-process-write（consume → produce 到另一個 topic → 提交 offset 原子化）。一旦你的處理有**外部副作用**（打 API、寫外部 DB、寄 email），那些副作用**不在 Kafka 交易裡**，重試照樣會重複執行。EOS 不是萬能護身符，它保護的是 Kafka 邊界內的效果。外部副作用還是得自己做冪等。

4. **「offset 是 broker 記的，consumer 不用管」→ 反了**。offset 是 **consumer 的狀態**（存在 `__consumer_offsets` 這個 compacted topic 裡）。你什麼時候提交 offset、提交在處理之前還是之後，直接決定你是 at-least-once（先處理後提交，崩潰會重處理）還是 at-most-once（先提交後處理，崩潰會漏處理）。搞錯提交時機是重複/遺漏的頭號來源。

5. **「compaction 會即時去重」→ 不會，它是背景批次**。compaction 由背景執行緒週期性做，不是寫入時即時的。所以 compacted topic 在任一瞬間**可能同時存在同一 key 的多個舊值**（還沒被 compact 掉）。consumer 讀 compacted topic 要能接受「同一 key 讀到多個版本、以最後讀到的為準」，不能假設每 key 只有一筆。

## 進階：再往深一層

- **log 為什麼快？靠順序 IO + 零拷貝（zero-copy）**。append-only 意味著寫入永遠是**順序寫**磁碟——即使是機械硬碟，順序寫也能到數百 MB/s，遠快於隨機寫。讀取時 Kafka 用 `sendfile` 系統呼叫做 zero-copy：資料從 page cache 直接進網卡，不經過使用者空間拷貝。「只追加」這個限制不是妥協，正是效能的來源。這跟 [Ch 25](./25-replicated-state-machine.md) RSM 的 log、跟 LSM-tree 資料庫的 WAL 是同一個「順序寫 log 最快」的洞見。

- **Kafka 怎麼自己選 leader？從 ZooKeeper 到 KRaft**。partition 的 leader 選舉、broker 成員管理，Kafka 早期靠 ZooKeeper（[Ch 41](./41-etcd-zookeeper.md)）——這正呼應本課「不要自己實作共識，用協調服務」的建議。但 ZooKeeper 是外部依賴、且 metadata 規模大時成為瓶頸。Kafka 自 2.8 起推 **KRaft** 模式：把 metadata 本身存成一條 Kafka log，用內建的 Raft（[Ch 20-23](./20-raft-leader-election.md)）複製，移除 ZooKeeper 依賴。這是一個漂亮的自舉——**Kafka 用「log + Raft」來管理 Kafka 自己**，把本課 Part 3 的共識和本章的 log 抽象疊在一起。

- **「The Log」的大論點**：Jay Kreps 那篇文章的野心遠超 Kafka。他論證：資料庫的複製（複製 log）、狀態機複製（[Ch 25](./25-replicated-state-machine.md)）、資料整合（把各系統的變更當事件流）、串流處理——**本質上都是「log + 對 log 做 fold」**。資料庫是「log 的一個查詢視圖」；一個服務的狀態是「它消費的 log 的 fold」。這個統一視角把「複製」「整合」「串流」看成同一件事的不同面向。讀完本課再回頭讀那篇，會有「原來前面學的全連起來了」的感覺。

## 本章重點整理

- **核心論點**：append-only log 是分散式系統的核心資料結構，因為它同時是**全序**（[Ch 7](./07-total-order-broadcast.md)）、**權威歷史**、和**解耦層**。Kafka 是這個論點的工程化身。
- **offset 是 consumer 的狀態，不是 log 的狀態**——這讓同一份 log 能被任意多方各自消費、各自重播。這是 Kafka 相對傳統佇列的根本優勢。
- **partition** 把一條 log 切成多條以擴展吞吐；代價是**只保證單 partition 內順序**，跨 partition 無序。相關事件用同一 key 落同一 partition 來保序。
- **ISR + acks** 是持久性旋鈕：`acks=0/1/all` 從最快最不可靠到最慢最可靠；`acks=all` + `min.insync.replicas` 本質上是動態成員的 quorum（[Ch 13](./13-quorum-replication.md)）。
- Kafka 的 **exactly-once** 不打破 [Ch 3](./03-rpc-and-message-semantics.md) 的迷思——它靠**冪等 producer（去重）+ 交易（原子提交）**把重複的**效果**消掉，達到 effectively-once，且**只在 Kafka 邊界內**有效，外部副作用要自己做冪等。
- **log compaction** 把事件流壓成每 key 最新值，讓 log 同時是「歷史」和「當前狀態快照」，直接連到 [Ch 42](./42-event-sourcing-cqrs.md) 的事件溯源。

## 自我檢核

- [ ] 不看筆記，我能說出「offset 是誰的狀態」，以及為什麼這讓 Kafka 能被多方消費、可重播
- [ ] 我能解釋 Kafka 的順序保證邊界（單 partition 內 vs 跨 partition），以及怎麼讓相關事件保序
- [ ] 我能講清楚 acks=0/1/all 各自的持久性取捨，以及 `acks=all` 何時仍可能拒絕寫入
- [ ] 我能說明 Kafka 的 exactly-once 為什麼「沒有打破 [Ch 3](./03-rpc-and-message-semantics.md) 的迷思」，它到底保證了什麼、邊界在哪
- [ ] 我能解釋 log compaction 怎麼把「事件流」變成「狀態快照」，以及它為什麼是背景批次而非即時

## 延伸閱讀

### 奠基文章 / 官方文件

- **[The Log: What every software engineer should know about real-time data's unifying abstraction](https://engineering.linkedin.com/distributed-systems/log-what-every-software-engineer-should-know-about-real-time-datas-unifying-abstraction)** — Jay Kreps（LinkedIn, 2013）
  - **讀哪裡**：整篇都值得，重點在「The Log and databases」與「Data integration」兩節，是本章大論點的原始出處
  - **學什麼**：為什麼「複製、資料整合、串流處理本質上都是 log + fold」——把本課前面所有東西串成一個視角
  - **前提**：讀懂本章的 log 抽象與 [Ch 25](./25-replicated-state-machine.md) RSM

- **[Kafka Documentation — Design](https://kafka.apache.org/documentation/#design)** — Apache Kafka 官方
  - **讀哪裡**：Design 章的「Persistence」「Replication」「Message Delivery Semantics」三節
  - **學什麼**：ISR、acks、exactly-once 的權威定義與邊界，本章持久性與 EOS 段落的出處
  - **注意**：官方對 EOS 的描述很小心地限定範圍，對照本章踩雷 #3 讀

### 深入原理

- **[Exactly-Once Semantics Are Possible: Here's How Kafka Does It](https://www.confluent.io/blog/exactly-once-semantics-are-possible-heres-how-apache-kafka-does-it/)** — Confluent
  - **讀哪裡**：冪等 producer 的 PID+sequence number 去重、交易協定的部分
  - **學什麼**：本章「近似 exactly-once」機制的實作細節，理解它怎麼在 at-least-once 之上做去重
  - **前提**：[Ch 3](./03-rpc-and-message-semantics.md) 的 exactly-once 迷思

- **《Designing Data-Intensive Applications》第 11 章（Stream Processing）** — Martin Kleppmann
  - **讀哪裡**：「Partitioned Logs」一節，把 log 抽象講得極清楚，並連到 CDC（change data capture）與事件溯源
  - **學什麼**：log 抽象在更大圖景（串流、資料整合）裡的位置，本章與 [Ch 42](./42-event-sourcing-cqrs.md) 的橋樑

Kafka 展示了「一條 log 能當整個系統的骨幹」。下一章我們看另一類專門的系統——當你需要的不是搬大量資料，而是讓一群節點**對少量關鍵狀態（誰是 leader、鎖歸誰、設定是什麼）達成一致**時，該用的協調服務 etcd 與 ZooKeeper。

→ [Ch 41 etcd / ZooKeeper](./41-etcd-zookeeper.md)
