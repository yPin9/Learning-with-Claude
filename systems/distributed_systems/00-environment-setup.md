# Ch 0 — 環境搭建與確定性模擬器

> **目標**：把 Go 環境架好，並親手寫出一個**確定性網路模擬器（deterministic network simulator）**——它能注入延遲、丟包、網路分區（partition）、當機（crash），而且同一個 seed 跑出來的結果**逐位元組相同**。這是全課動手的骨架：之後每一個 Raft、quorum、PBFT 練習都跑在它上面，任何 bug 都能重現。

> **環境**：Go 1.18+（本課用 1.18.1 在 WSL2 / Linux x86-64 實測）。程式碼不依賴任何第三方套件，純標準庫。Windows 原生 Go 也能跑，但本課所有輸出以 WSL 為準。

## 為什麼需要這個？

分散式系統最難的不是演算法，是**復現 bug**。

想像你寫了一個 Raft。它在你電腦上跑一萬次都對，上線後某天在一個特定的網路分區時序下選出了兩個 leader，丟了一筆已經回覆客戶端「成功」的寫入。你想重現這個 bug——但它依賴真實網路的延遲抖動、goroutine 的排程順序、作業系統的時脈。這些每次都不一樣。**你抓不回那個時序，就修不了那個 bug。**

真實世界怎麼解？FoundationDB 團隊給了最漂亮的答案：他們不在真實網路上測，而是**把整個分散式系統跑在一個確定性模擬器裡**，模擬器控制所有的時間、所有的訊息順序、所有的失敗注入。同一個 seed → 同一個 world line。他們花了兩年只寫模擬器，然後才寫資料庫——上線後幾乎沒有一致性 bug。

我們這門課走同一條路。在寫任何共識演算法之前，先把這個模擬器造出來。它不用像 FoundationDB 那樣完整，但要有靈魂：**確定性 + 失敗注入**。

> 對比你熟悉的東西：這跟你在 `observability_tools` 學的 `rr`（record-replay debugger）是同一個哲學——把不確定性關進籠子，讓 bug 可重現。差別是 `rr` 錄真實執行，我們則是從一開始就讓執行變確定。

## 先建立直覺

先想清楚：一個分散式系統，本質上是**一群節點 + 它們之間傳的訊息**。節點會對「收到訊息」和「時間流逝」這兩件事做反應。除此之外，節點之間沒有共享記憶體、沒有全域時鐘、沒有神的視角。

```
        真實世界（不可重現）              我們的模擬器（可重現）
    ┌─────────────────────────┐     ┌──────────────────────────────┐
    │  Node A ──TCP──> Node B  │     │   所有節點在同一個 process     │
    │    ↑ 真實網路延遲抖動     │     │   訊息進一個「事件佇列」        │
    │    ↑ OS 排程隨機          │     │   一個排程器按 (時間,序號)      │
    │    ↑ 真實時脈             │     │   決定誰先收到 → 全程確定       │
    └─────────────────────────┘     └──────────────────────────────┘
       跑兩次 = 兩個結果                 同 seed 跑兩次 = 同一個結果
```

關鍵設計：**時間不是真實時間，是一個整數計數器**。訊息不是「立刻送達」，而是「排程在未來某個邏輯時刻送達」。一個中央排程器（scheduler）按 `(送達時刻, 序號)` 排序處理所有事件——序號是同一時刻內的 tiebreaker，保證順序不靠 map 迭代這種不確定來源決定。

只要排程順序確定、隨機數來源是固定 seed 的偽隨機，整個世界就是確定的。

## 第一步：Go 環境

```bash
# WSL / Linux
$ go version
go version go1.18.1 linux/amd64
```

沒有的話（Debian/Ubuntu 系）：

```bash
$ sudo apt update && sudo apt install -y golang-go
# 或裝官方最新版：https://go.dev/dl/
```

建一個模組：

```bash
$ mkdir -p ~/ds/dsim && cd ~/ds/dsim
$ go mod init dsim
go: creating new go.mod: module dsim
```

Go 的模組系統很單純：`go.mod` 標記模組根，`go build ./...` 編譯全部，`go test ./...` 跑測試。本課不需要更複雜的東西。

> Go 語法你會邊用邊學。這門課不是 Go 教學，但共識演算法用 Go 的 goroutine/channel 表達最自然（這也是 MIT 6.5840、etcd、CockroachDB 都用 Go 的原因）。你有 C/C++/Rust 底子，Go 對你幾乎沒有學習曲線——它就是「有 GC、有 channel、語法更簡單的 C」。

## 第二步：模擬器的核心型別

先定義「節點」和「訊息」這兩個最基本的抽象。

```go
// dsim.go — 節點與訊息
package dsim

type NodeID int

// Message 是節點之間交換的東西。Payload 放任何具體訊息型別。
type Message struct {
	From, To NodeID
	Payload  any
}

// Node 是任何會對「收到訊息」和「時脈 tick」做反應的東西。
// handler 透過傳入的 *Net 發新訊息（不要把 net 存起來在排程之外用，
// 那會破壞確定性）。
type Node interface {
	OnMessage(m Message, net *Net)
	OnTick(now int, net *Net)
}
```

兩個 callback 對應直覺裡的兩件事：

- `OnMessage`：收到一則訊息時觸發。節點在這裡更新自己的狀態、可能發出新訊息。
- `OnTick`：每個邏輯時刻觸發一次。這是「時間流逝」的入口——選舉逾時（election timeout）、心跳（heartbeat）這類「過了多久就該做某事」的邏輯都靠它。

> **為什麼不用真的 goroutine + channel？** 你可能會想：Go 有 channel，每個節點開一個 goroutine，channel 當網路，不是更自然嗎？問題正是確定性。goroutine 的排程順序由 Go runtime 決定，你控制不了；`select` 從多個 channel 讀取時選哪個也是隨機的。跑兩次就是兩個順序。我們要的是**單執行緒、事件驅動**——所有並發都是「模擬」出來的，不是真的並發，這樣才能重現。真正的並發我們會在 Ch 3 討論 RPC 時再碰。

## 第三步：事件佇列——確定性的來源

訊息不立刻送達，而是排進一個依 `(送達時刻, 序號)` 排序的優先佇列（priority queue）。Go 標準庫的 `container/heap` 剛好。

```go
// dsim.go（續）— 事件與優先佇列
import "container/heap"

type event struct {
	at  int // 邏輯送達時刻
	seq int // tiebreaker：同一時刻內按進佇列順序，保證確定
	m   Message
}

type eventQueue []event

func (q eventQueue) Len() int { return len(q) }
func (q eventQueue) Less(i, j int) bool {
	if q[i].at != q[j].at {
		return q[i].at < q[j].at
	}
	return q[i].seq < q[j].seq // 同時刻：先進先出
}
func (q eventQueue) Swap(i, j int) { q[i], q[j] = q[j], q[i] }
func (q *eventQueue) Push(x any)   { *q = append(*q, x.(event)) }
func (q *eventQueue) Pop() any {
	old := *q
	n := len(old)
	e := old[n-1]
	*q = old[:n-1]
	return e
}
```

`seq` 這個欄位是整個確定性的關鍵。兩則訊息若排在同一個邏輯時刻送達，誰先被處理？如果用 map 迭代或真實時間決定，那就不確定了。我們用一個單調遞增的 `seq`——先呼叫 `Send` 的先送達。**這是我們刻意做的決定：把所有 tiebreak 都釘死成確定的。**

## 第四步：Net——網路與失敗注入

現在把所有東西組起來，並把失敗注入（fault injection）掛在網路上。

```go
// dsim.go（續）— 模擬網路
import "math/rand"

// Net 是模擬網路。所有失敗注入都住在這裡。
type Net struct {
	rng     *rand.Rand
	nodes   map[NodeID]Node
	q       eventQueue
	now     int
	seq     int
	// 失敗注入
	part    map[NodeID]int  // 分區群組；不同群組的節點無法通訊
	crashed map[NodeID]bool // 當機節點不收不發
	drop    float64         // 每則訊息的丟包機率
	minLat  int
	maxLat  int
	// 可觀測性
	Delivered int
	Dropped   int
}

func NewNet(seed int64) *Net {
	return &Net{
		rng:     rand.New(rand.NewSource(seed)), // 固定 seed = 確定的隨機
		nodes:   map[NodeID]Node{},
		part:    map[NodeID]int{},
		crashed: map[NodeID]bool{},
		minLat:  1,
		maxLat:  1,
	}
}

func (n *Net) Add(id NodeID, node Node) { n.nodes[id] = node }
```

注意 `rng` 是 `rand.New(rand.NewSource(seed))`——**不是**全域的 `rand.Intn`。全域的 rand 有共享狀態，多處呼叫順序會影響結果；每個 Net 自己一個 rng，seed 固定，隨機序列就固定。這是一個容易踩的雷，後面「踩雷集錦」會再強調。

失敗注入的旋鈕（knobs）：

```go
// dsim.go（續）— 失敗注入 API
func (n *Net) SetLatency(min, max int) { n.minLat, n.maxLat = min, max }
func (n *Net) SetDropRate(p float64)   { n.drop = p }

// Partition 把每個列出的群組放進各自的孤島。不同孤島的節點
// 在 Heal 之前無法交換訊息。
func (n *Net) Partition(groups ...[]NodeID) {
	n.part = map[NodeID]int{}
	for gi, g := range groups {
		for _, id := range g {
			n.part[id] = gi
		}
	}
}
func (n *Net) Heal()             { n.part = map[NodeID]int{} }
func (n *Net) Crash(id NodeID)   { n.crashed[id] = true }
func (n *Net) Restart(id NodeID) { n.crashed[id] = false }

func (n *Net) reachable(from, to NodeID) bool {
	if n.crashed[from] || n.crashed[to] {
		return false
	}
	if len(n.part) == 0 {
		return true // 沒設分區 = 全連通
	}
	return n.part[from] == n.part[to] // 同群組才能通
}
```

這五個 API——`SetLatency` / `SetDropRate` / `Partition` / `Crash` / `Restart`——就是我們注入分散式失敗的全部工具。看起來少，但足以重現 CAP 定理裡的網路分區、Raft 的 leader 當機、quorum 的少數派隔離。**分散式系統的失敗模型本質上就這幾種**（下一章 Ch 2 會嚴謹地分類），我們把它們全做成可控旋鈕。

## 底層機制：Send 與 Run 怎麼運作

這是模擬器的心臟。

```go
// dsim.go（續）— 傳送與主迴圈

// Send 由節點呼叫來發訊息。它「不」立刻送達：排程在 now+latency，
// 可能被丟包，並在送出時就過濾分區/當機狀態（送達時會再檢查一次）。
func (n *Net) Send(m Message) {
	if !n.reachable(m.From, m.To) {
		n.Dropped++
		return
	}
	if n.drop > 0 && n.rng.Float64() < n.drop {
		n.Dropped++
		return
	}
	lat := n.minLat
	if n.maxLat > n.minLat {
		lat += n.rng.Intn(n.maxLat - n.minLat + 1)
	}
	n.seq++
	heap.Push(&n.q, event{at: n.now + lat, seq: n.seq, m: m})
}

// Run 把邏輯時間推進到 maxSteps。每一步：(1) 送達所有排在當前時刻的
// 訊息，送達時「再檢查」一次可達性（分區可能在飛行途中發生）；
// (2) tick 每個活著的節點。回傳最終邏輯時間。
func (n *Net) Run(maxSteps int) int {
	for n.now < maxSteps {
		n.now++
		for len(n.q) > 0 && n.q[0].at == n.now {
			e := heap.Pop(&n.q).(event)
			if !n.reachable(e.m.From, e.m.To) {
				n.Dropped++ // 分區在訊息飛行途中發生 → 送達時丟掉
				continue
			}
			if node, ok := n.nodes[e.m.To]; ok {
				n.Delivered++
				node.OnMessage(e.m, n)
			}
		}
		for id, node := range n.nodes {
			if !n.crashed[id] {
				node.OnTick(n.now, n)
			}
		}
	}
	return n.now
}

func (n *Net) Now() int { return n.now }
```

主迴圈的執行流程，畫成圖：

```
Run(maxSteps):
  while now < maxSteps:
    now++                          ← 邏輯時間前進一格
    ┌─────────────────────────────────────────────┐
    │ 送達階段：pop 所有 at==now 的事件            │
    │   for each event:                            │
    │     reachable(from,to)? ──No──> Dropped++    │  ← 飛行途中被分區/當機攔截
    │       │Yes                                   │
    │       └─> node.OnMessage(...)  ← 可能 Send 新訊息（排到未來）│
    └─────────────────────────────────────────────┘
    ┌─────────────────────────────────────────────┐
    │ tick 階段：對每個活著的節點 OnTick(now)      │  ← 逾時、心跳在這觸發
    └─────────────────────────────────────────────┘
```

有兩個設計決定值得說清楚：

1. **可達性檢查做兩次**（send 時 + 送達時）。為什麼？因為訊息是「飛行中」的——你在 `now=5` 送出、排程 `now=8` 送達，但 `now=6` 時你 `Partition` 了。這則訊息「已經在路上」但目標已經隔離了。真實網路裡它會被丟棄（路由不到），所以我們在送達時再檢查一次。這模擬了「訊息在傳輸途中因分區而丟失」——一個非常真實、也非常會咬人的場景。

2. **tick 階段用 `for id, node := range n.nodes`，這是 map 迭代！** 等等，map 迭代在 Go 裡是隨機順序的，這不是破壞確定性了嗎？——是的，這是一個**故意留下的、需要你注意的細節**。只要 `OnTick` 裡節點的行為不依賴「哪個節點先 tick」（它們只是各自檢查自己的逾時、發自己的訊息，而訊息都進佇列按 seq 排序），最終結果仍然確定。但如果你的節點邏輯在 tick 裡直接改別人的狀態（絕對不該這樣做），這裡就會出問題。我們在踩雷集錦會回來談。

## 跑起來：三個驗證測試

模擬器寫完了，怎麼確定它對？寫測試。這三個測試分別驗證「訊息會流動」「分區真的隔離」「同 seed 真的確定」。

```go
// demo_test.go
package dsim

import "testing"

// pinger 把一個計數器來回彈，每跳一次 +1。
type pinger struct {
	id, peer NodeID
	count    int
	start    bool
}

func (p *pinger) OnMessage(m Message, net *Net) {
	p.count = m.Payload.(int) + 1
	net.Send(Message{From: p.id, To: p.peer, Payload: p.count})
}
func (p *pinger) OnTick(now int, net *Net) {
	if p.start && now == 1 { // 只有 a 在 now=1 起手
		net.Send(Message{From: p.id, To: p.peer, Payload: 0})
	}
}

func TestPingPong(t *testing.T) {
	net := NewNet(42)
	a := &pinger{id: 0, peer: 1, start: true}
	b := &pinger{id: 1, peer: 0}
	net.Add(0, a); net.Add(1, b)
	net.Run(20)
	if a.count == 0 && b.count == 0 {
		t.Fatal("no messages flowed")
	}
	t.Logf("after 20 steps: a=%d b=%d delivered=%d", a.count, b.count, net.Delivered)
}

func TestPartitionIsolates(t *testing.T) {
	net := NewNet(1)
	a := &pinger{id: 0, peer: 1, start: true}
	b := &pinger{id: 1, peer: 0}
	net.Add(0, a); net.Add(1, b)
	net.Partition([]NodeID{0}, []NodeID{1}) // 各自一座孤島
	net.Send(Message{From: 0, To: 1, Payload: 0})
	net.Run(20)
	if net.Delivered != 0 {
		t.Fatalf("partition leaked: delivered=%d", net.Delivered)
	}
	net.Heal()
	net.Send(Message{From: 0, To: 1, Payload: 0}) // heal 後重新注入
	net.Run(40)
	if net.Delivered == 0 {
		t.Fatal("heal did not restore connectivity")
	}
	t.Logf("after heal: delivered=%d a=%d b=%d", net.Delivered, a.count, b.count)
}

func TestDeterministic(t *testing.T) {
	run := func() int {
		net := NewNet(7)
		net.SetLatency(1, 5)
		net.SetDropRate(0.1)
		a := &pinger{id: 0, peer: 1, start: true}
		b := &pinger{id: 1, peer: 0}
		net.Add(0, a); net.Add(1, b)
		net.Run(100)
		return a.count + b.count*1000 + net.Dropped*1000000
	}
	if run() != run() {
		t.Fatal("same seed produced different runs — not deterministic")
	}
	t.Logf("reproducible fingerprint: %d", run())
}
```

真跑（WSL, Go 1.18.1）：

```
$ go test -v ./...
=== RUN   TestPingPong
    demo_test.go:28: after 20 steps: a=18 b=19 delivered=19
--- PASS: TestPingPong (0.00s)
=== RUN   TestPartitionIsolates
    demo_test.go:44: after heal: delivered=20 a=20 b=19
--- PASS: TestPartitionIsolates (0.00s)
=== RUN   TestDeterministic
    demo_test.go:59: reproducible fingerprint: 1003004
--- PASS: TestDeterministic (0.00s)
PASS
ok  	dsim	0.002s
```

三個都綠。`TestPartitionIsolates` 尤其重要：它證明 `delivered==0` during partition——**訊息真的被隔離了，沒有洩漏**。這正是 CAP 定理裡「P」的模擬。而 `TestDeterministic` 印出的 `1003004` 這個 fingerprint，你在你機器上跑應該拿到**一模一樣**的數字（同 seed、同延遲/丟包設定），因為隨機源是釘死的。

> 這裡藏著一個「失敗是教學素材」的例子：`TestPartitionIsolates` 我第一版寫錯過——ping-pong 的唯一觸發在 `now==1`，被分區丟掉後就沒有任何訊息在飛，heal 之後自然也沒東西可送，測試誤報「heal 沒恢復連通」。這不是模擬器的 bug，是測試的 bug：**你不能靠一個已經死掉的訊息鏈去驗證 heal**。修法是 heal 後重新 `Send` 一則。分散式測試裡這種「我以為在測 A，其實測到 B」的陷阱極多，Ch 43 會專門講。

## 對比與取捨

| 測試方式 | 確定性 | 能注入失敗 | 貼近真實 | 適用 |
|---|---|---|---|---|
| 真實多機部署 | 無 | 難（要真的斷網） | 最高 | 上線前最終驗收 |
| 真 goroutine + channel | 無（排程隨機） | 中 | 中 | 快速原型 |
| **本課確定性模擬器** | **完全** | **容易（旋鈕）** | 中（單執行緒近似） | **學習/開發共識演算法** |
| Jepsen（真系統 + nemesis） | 無 | 容易 | 高 | 驗證真實資料庫（Ch 43） |

我們的模擬器不是要取代 Jepsen 或真實部署——它是**開發階段**的工具。你先在確定性環境把演算法邏輯磨對，再拿去真實環境驗收。兩者互補。

## 踩雷集錦

1. **用全域 `rand` 而不是 `Net` 自己的 rng**：很多人以為「反正都是 rand，設個 `rand.Seed(42)` 就好」。錯。全域 rand 是共享狀態，只要有任何其他 goroutine 或程式碼路徑碰它，你的序列就變了，確定性就沒了。**每個模擬 world 一個獨立 `*rand.Rand`**，這是不可退讓的。

2. **在 `OnTick` 裡直接改別的節點的狀態**：例如 `otherNode.state = ...`。這相當於「共享記憶體」，徹底違背分散式系統的前提——節點之間**只能靠訊息**溝通。而且它會讓 map 迭代順序影響結果，破壞確定性。規則：節點只能改自己的欄位，跟別人互動一律走 `net.Send`。

3. **以為 `Send` 是同步送達**：`Send` 只是「排程」，訊息要到未來某個 tick 才送達，且可能永遠不送達（丟包/分區）。寫演算法時**永遠不能假設訊息會到、會即時到、會只到一次**。這不是模擬器的限制，這就是真實網路——Ch 3 會把這件事講透。

4. **分區後忘記飛行中的訊息**：`Partition` 之後，你以為兩邊完全斷了，但**分區前已經送出、還在佇列裡的訊息**呢？我們的設計是在送達時再檢查一次 `reachable`，所以它們會被丟掉。但如果你自己改模擬器忘了這個檢查，就會出現「分區了訊息還是漏過去」的幽靈 bug。

5. **`maxSteps` 給太小**：`Run(20)` 只跑 20 個邏輯時刻。如果你的協定需要好幾輪往返（Raft 選舉可能要 10+ 個 timeout 週期），20 步根本不夠，你會看到「怎麼永遠選不出 leader」，其實只是時間不夠。跑不出結果時，先把 `maxSteps` 加大再說。

## 進階：再往深一層

這個模擬器是**教學等級**的最小骨架。真實的確定性模擬（如 FoundationDB 的 `flow`、TigerBeetle 的 VOPR、madsim）還會做這些，你有興趣可以自己加：

- **確定性的時間逾時**：真實系統用真實時鐘設 timeout，模擬器裡應該用邏輯時刻。我們已經這樣做了（`OnTick(now)`），但真實框架會讓節點「註冊一個在 now+T 觸發的 timer」而不是每 tick 檢查，更高效。
- **訊息重排與重複**：我們的網路會延遲、會丟，但不會「亂序送達同一對節點之間的訊息」（因為 seq 保 FIFO）也不會重複。真實 UDP 會亂序、會重複。把 `Send` 改成「同一對節點間也可能亂序」能逼出更多 bug——這正是 Ch 3「exactly-once 是迷思」的伏筆。
- **swarm testing / 隨機失敗排程**：讓 nemesis（搗亂者）在隨機時刻自動注入 partition/crash/heal，而不是你手寫。跑幾千個 seed，任何一個 seed 觸發 assertion 失敗，你都能靠那個 seed 精準重現。這是 Ch 43 的核心。

```go
// 進階示範：一個極簡 nemesis，隨機在某些時刻製造/修復分區
type nemesis struct {
	net      *Net
	nodes    []NodeID
	nextFlip int
}

func (nm *nemesis) maybeChaos(now int) {
	if now < nm.nextFlip {
		return
	}
	if len(nm.net.part) == 0 {
		// 隨機把節點切成兩半
		half := len(nm.nodes) / 2
		nm.net.Partition(nm.nodes[:half], nm.nodes[half:])
	} else {
		nm.net.Heal()
	}
	nm.nextFlip = now + 5 + nm.net.rng.Intn(10) // 下次搗亂的時刻，仍走 net.rng 保確定
}
```

（這段是示意骨架，`nm.net.rng` 是未匯出欄位，實際用要放在 `dsim` 套件內或加一個匯出的 `Chaos` 方法；重點是**連搗亂都要走 `net.rng` 才能重現**。）

## 動手練習

1. 把 `TestDeterministic` 的 seed 從 7 改成別的數字，確認 fingerprint 變了（不同 seed = 不同 world），但同一個 seed 跑兩次仍相同。
2. 在 `TestPingPong` 裡加一行 `net.SetDropRate(0.5)`，觀察 `delivered` 掉了多少、ping-pong 是否還能持續（提示：丟一次就斷鏈了，因為這個 pinger 沒有重送機制——這正是 Ch 3 要解決的問題）。
3. 故意在 `Run` 的送達階段**移除**第二次 `reachable` 檢查，重跑 `TestPartitionIsolates`，看它怎麼從 PASS 變 FAIL（分區洩漏）。這讓你親眼看到那個檢查在防什麼。

## 本章重點整理

- 分散式系統最難的是**復現 bug**；解法是把不確定性關進籠子——確定性模擬。
- 模擬器三支柱：**邏輯時間（整數計數器）**、**事件佇列（按 (時刻,seq) 排序）**、**固定 seed 的隨機源**。三者到位，同 seed → 同結果。
- 失敗注入做成旋鈕：延遲、丟包、分區、當機。這幾種涵蓋了分散式失敗的主要型態（Ch 2 嚴謹分類）。
- 節點之間**只能靠訊息**溝通，不能共享記憶體——這是分散式的第一原則，也是模擬器強制你遵守的紀律。

## 自我檢核

- [ ] 我能解釋「為什麼確定性模擬對開發共識演算法這麼重要」，而不只是「因為課這樣排」
- [ ] 我知道模擬器的確定性來自哪三個設計（時間、事件排序、隨機源），少一個會怎樣
- [ ] 我能說出「為什麼可達性要在送達時再檢查一次」對應真實網路的什麼場景
- [ ] 不看程式碼，我能說出節點之間唯一的溝通方式是什麼，以及為什麼不能共享記憶體

## 延伸閱讀

### 部落格 / 技術文章

- **[Testing Distributed Systems w/ Deterministic Simulation](https://www.youtube.com/watch?v=4fFDFbi3toc)** — Will Wilson（FoundationDB, Strange Loop 2014）
  - **這篇說什麼**：FoundationDB 怎麼用確定性模擬做到「上線幾乎零一致性 bug」。本章的整個設計哲學就來自這場 talk
  - **讀哪裡**：整場 40 分鐘都值得，重點在前 20 分鐘講「為什麼真實測試抓不到 bug」
  - **為什麼值得看**：這是「確定性模擬」這個流派的奠基演講，講者是 FoundationDB 核心工程師

- **[Deterministic Simulation Testing](https://sled.rs/simulation.html)** — Tyler Neely（sled 資料庫作者）
  - **這篇說什麼**：用 Rust 做確定性模擬的實作細節，補充本章沒展開的「怎麼把 timeout、IO 都變確定」
  - **讀哪裡**：整頁不長；「Simulation」和「Faults」兩節與本章最相關
  - **前提知識**：讀得懂本章的事件迴圈即可

### 官方文件 / 原始碼

- **[`container/heap` 官方文件](https://pkg.go.dev/container/heap)**
  - **讀哪裡**：整頁 + 範例；本章的 `eventQueue` 就是照這個介面實作的
  - **注意**：`heap.Interface` 要你實作 `Len/Less/Swap/Push/Pop` 五個方法，少一個編不過

- **[madsim](https://github.com/madsim-rs/madsim)** — Rust 的生產級確定性模擬框架
  - **這是什麼**：本章模擬器的「成年版」，被 RisingWave 等真實資料庫用來測分散式邏輯
  - **讀哪裡**：README + `madsim/src/sim/net` 目錄，看真實框架怎麼處理我們簡化掉的部分（真 async、tokio 整合）

準備好骨架了。下一章我們先不碰演算法，而是問一個更基本的問題：**分散式系統到底難在哪裡？** 為什麼「一群電腦」不能當成「一台更大的電腦」來用？

→ [Ch 1 為什麼分散式這麼難](./01-why-distributed-is-hard.md)
