# 練習 A — Vector Clock + 因果一致訊息層

> **目標**：把 Part 1 學的東西變成一個能跑的東西。在 dsim 上實作一個**因果遞交（causal delivery）** 的訊息層：亂序到達的廣播訊息，先用 vector clock 判斷「它的因果前置是否都到齊」，沒到齊就緩衝（buffer），等前置到了再遞交（deliver）給應用層。驗收標準：**任何 `a→b`（a 因果先於 b）的兩則訊息，在每個節點上 a 都先於 b 被遞交**——就算它們亂序到達也一樣。

> **環境**：Go 1.18.1, WSL2/Linux x86-64。跑在 Ch 0 的 `dsim` 上。

> 先修：[Ch 6 Vector Clock](./06-vector-clocks.md)（規則與因果判定）、[Ch 7 全序廣播](./07-total-order-broadcast.md)（因果序 vs 全序）、[Ch 0 dsim](./00-environment-setup.md)（模擬器 API）。

## 為什麼做這個練習

Ch 6 我們用 vector clock 事後判定兩個事件的因果關係。但真實系統要的不是「事後判定」，是「運行時保證」——保證應用層**永遠不會先看到一則訊息、才看到它因果依賴的前置訊息**。

一個具體場景：社群軟體的訊息串。使用者 A 發了「我請客！」（post），使用者 B 看到後回「太好了！」（reply）。reply 因果依賴 post。現在這兩則訊息廣播到第三個使用者 C 的裝置——如果網路亂序，reply 先到，C 的畫面就會顯示「太好了！」出現在「我請客！」之前，讀起來莫名其妙。因果遞交就是為了杜絕這個：**C 的裝置收到 reply 時，發現它依賴的 post 還沒到，就把 reply 藏起來（buffer），等 post 到了才一起顯示**。

這個機制是**因果一致性（causal consistency）** 的實作核心，也是 Ch 7 提過的「因果全序廣播」的一半（只保因果序，不保無因果訊息之間的全序）。COPS、Bayou、Redis CRDT 這些系統都建在它上面。做完這個練習，你就把 vector clock 從「一個判斷工具」變成「一個真正的訊息中介層」。

## 任務規格

實作一個 `causalNode`，掛在 `dsim` 上，行為如下：

**輸入**：一組廣播訊息。每則訊息由某個節點 `broadcast` 出去，送給所有其他節點。訊息帶著寄件者送出時的 vector clock。訊息在網路上**可能亂序到達、延遲、甚至因分區而遲到**（用 dsim 的 `SetLatency` / `Partition` 注入）。

**輸出**：每個節點對應用層的 `deliver` 序列。這個序列必須**尊重因果**。

**因果遞交規則**（Birman-Schiper-Stephenson 演算法）：節點 i 收到來自 j、帶 vector clock `m.vc` 的訊息 m 時，判斷 m **可遞交（deliverable）** 的條件是：

```
  (1) m.vc[j] == VC_i[j] + 1
      「這是我等的、來自 j 的下一則訊息」（沒有跳號，j 的訊息按序）

  (2) 對所有 k != j:  m.vc[k] <= VC_i[k]
      「寄件者 j 送出 m 之前看過的所有因果前置，我 i 也都已經遞交過了」
```

兩條都滿足才 deliver；否則 buffer。deliver 時更新 `VC_i[j] = m.vc[j]`（收下 j 的這格進度）。每次 deliver 後要**重掃 buffer**——因為一次遞交可能讓 buffer 裡某則訊息的前置終於到齊，解鎖它。

**驗收**：對每個節點，任何 `a→b`（vector clock 判 a 因果先於 b）的兩則訊息，a 的 deliver 序必須早於 b。就算 a、b 亂序到達也不能違反。

> **為什麼是這兩條規則**？條件 (1) 保證「來自同一個寄件者的訊息按序遞交」（j 的第 5 則不會在第 4 則前遞交）。條件 (2) 保證「跨寄件者的因果依賴被尊重」（j 送 m 前若先遞交過 k 的某訊息，那個依賴會反映在 `m.vc[k]` 上，我必須也已遞交它）。兩條合起來，恰好等價於「m 的所有 happens-before 前置都已遞交」。

## 分段步驟

### Step 1：定義訊息與節點狀態

先把資料結構立起來。訊息 `bcast` 帶寄件者、vector clock、內容、一個全域唯一編號（驗收用）。節點維護自己的 `vc`、一個 `buffer` 存暫緩的訊息。

```go
const N = 3

type VC [N]int

type bcast struct {
	id   int    // 全域唯一訊息編號
	src  NodeID // 寄件者
	vc   VC     // 寄件者送出時的向量
	body string
}

type causalNode struct {
	id     NodeID
	peers  []NodeID
	vc     VC      // 已遞交訊息累積的 VC
	buffer []bcast // 因果前置未到齊、暫緩
	// ... 加上你需要的觀察/記錄欄位
}
```

### Step 2：實作 `deliverable` 判定

把規格裡那兩條規則翻成 code。這是整個練習的核心，寫錯這裡全盤皆錯。

```go
func (n *causalNode) deliverable(m bcast) bool {
	j := m.src
	if m.vc[j] != n.vc[j]+1 { // 規則 (1)：j 的下一則、無跳號
		return false
	}
	for k := 0; k < N; k++ { // 規則 (2)：其他所有節點的因果前置都到齊
		if NodeID(k) == j {
			continue
		}
		if m.vc[k] > n.vc[k] {
			return false
		}
	}
	return true
}
```

### Step 3：實作 `broadcast`（在 `OnTick` 裡按腳本觸發）

廣播是一次本地事件：自己那格 +1，把當前 vector clock 蓋在訊息上，送給所有 peer。廣播者自己也「立即遞交」自己的訊息（它當然看得到自己發的）。

```go
func (n *causalNode) OnTick(now int, net *Net) {
	body, ok := n.script[now] // script: now -> 要廣播的內容
	if !ok {
		return
	}
	n.vc[n.id]++      // 廣播是本地事件
	stamp := n.vc     // 蓋在訊息上的向量
	// 廣播者本地立即遞交自己的訊息（記錄用）
	// ... 記錄 deliver ...
	for _, p := range n.peers {
		net.Send(Message{From: n.id, To: p, Payload: bcast{src: n.id, vc: stamp, body: body}})
	}
}
```

### Step 4：實作 `OnMessage`——收到訊息時 deliver 或 buffer

收到訊息，判斷可不可遞交：可以就 deliver 並 drain buffer；不可以就丟進 buffer 等。

```go
func (n *causalNode) OnMessage(m Message, net *Net) {
	msg := m.Payload.(bcast)
	if n.deliverable(msg) {
		n.deliver(msg, net.Now())
		n.tryDrain(net.Now()) // 這次遞交可能解鎖 buffer 裡的
	} else {
		n.buffer = append(n.buffer, msg) // 前置沒到齊，緩衝
	}
}

func (n *causalNode) deliver(m bcast, now int) {
	n.vc[m.src] = m.vc[m.src] // 收下寄件者那格進度
	// ... 記錄這次 deliver ...
}
```

### Step 5：實作 `tryDrain`——遞交後重掃 buffer

一次 deliver 可能讓 buffer 裡某則的前置到齊。反覆掃 buffer，只要找到可遞交的就遞交、然後重新掃（因為它又可能解鎖下一則），直到掃一輪都沒進展。

```go
func (n *causalNode) tryDrain(now int) {
	for {
		progress := false
		for i := 0; i < len(n.buffer); i++ {
			if n.deliverable(n.buffer[i]) {
				m := n.buffer[i]
				n.buffer = append(n.buffer[:i], n.buffer[i+1:]...)
				n.deliver(m, now)
				progress = true
				break // 改動了 buffer，重掃
			}
		}
		if !progress {
			return
		}
	}
}
```

### Step 6：注入亂序、跑、驗收

用 `SetLatency(1, 8)` 造大延遲抖動讓訊息自然亂序；或用 `Partition` + `Heal` 做對抗性注入，**保證**某節點先收到因果後繼再收到前置。跑完後檢查：對每個節點，任何 `a→b` 的兩則訊息，a 的遞交序都早於 b。

---

## 卡點提示

1. **`deliverable` 的規則 (1) 是嚴格的 `== VC[j]+1`，不是 `<=`**。若寫成 `m.vc[j] <= n.vc[j]+1`，你會允許「跳號遞交」——j 的第 3 則在第 2 則前遞交，因果就破了。必須恰好是「下一則」。若 `m.vc[j] <= n.vc[j]`，代表這則是**重複**（已遞交過），該丟棄而非緩衝（本練習的簡化場景無重複，但真實系統要處理）。

2. **deliver 後一定要 `tryDrain`，而且 drain 要重掃到「一輪無進展」為止**。常見 bug 是 drain 只掃一遍 buffer 就停——但遞交 buffer 裡的 A 可能解鎖 buffer 裡的 B，你得再掃一輪才會撈到 B。用「有進展就重掃」的迴圈，別用單次 for。

3. **廣播者要不要 deliver 自己的訊息？要**。節點 broadcast 後，自己那格已經 +1，它的 vc 就代表「我已遞交自己這則」。若你不記錄廣播者自己的遞交，驗收時廣播者的因果鏈會缺一環。多數實作讓廣播 = 本地立即遞交 + 送給 peer。

4. **buffer 用 slice 刪除元素時的索引陷阱**。`append(buffer[:i], buffer[i+1:]...)` 刪掉第 i 個後，後面元素前移，若你在同一輪 for 繼續 `i++` 會跳過一個。最穩的作法是刪除後 `break` 跳出重掃（如 Step 5），別在刪除後繼續同一輪迴圈。

5. **注入亂序卻沒真的亂序**：如果你只設 `SetLatency(1, 8)` 但 seed 剛好讓訊息都按序到，就看不到 buffer 行為。要**保證**看到緩衝，用 partition 做對抗性注入：先隔離接收者、讓因果鏈在別處完成，heal 後手動先送因果後繼、再送前置（見下方參考解答場景 2）。

## 測試用例表

| 場景 | 注入方式 | 預期行為 | 驗收點 |
|---|---|---|---|
| 基準（無亂序） | `SetLatency(1,1)` | 訊息按序到，無緩衝 | 因果安全 ✓，buffer 殘留 0 |
| 自然亂序 | `SetLatency(1,8)` 大抖動 | 部分節點可能緩衝 | 因果安全 ✓，最終 buffer 清空 |
| 對抗性亂序 | partition + heal，手動先送 reply 再送 post | 接收者**必須** buffer reply | reply 先到卻後遞交，post 先遞交 |
| 因果鏈斷點 | 讓 post 永遠不到某節點 | 該節點 reply 永遠卡在 buffer | buffer 殘留 > 0（暴露「前置遺失 = 永久卡住」） |

最關鍵的是**對抗性亂序**那列：它是唯一能證明你的 buffer 邏輯真的在運作的測試。基準測試就算你 buffer 寫錯也會過（因為根本沒觸發緩衝）。

## 參考解答

<details>
<summary>完整可跑的參考解答（在 dsim 上真跑，含自然亂序與對抗性亂序兩場景 + 因果安全自動驗收）</summary>

把下面存成 `main.go`，跟 `dsim.go`（把 `package dsim` 改成 `package main`）放同一個 `package main` 目錄，`go run .` 即可。

```go
package main

import (
	"fmt"
	"sort"
)

const N = 3

type VC [N]int

func (a VC) String() string { return fmt.Sprintf("[%d %d %d]", a[0], a[1], a[2]) }

func leq(a, b VC) bool {
	for i := 0; i < N; i++ {
		if a[i] > b[i] {
			return false
		}
	}
	return true
}
func happensBefore(a, b VC) bool { return leq(a, b) && a != b }

// bcast: 一則因果廣播訊息。vc 是寄件者送出時、遞增自己那格後的向量。
type bcast struct {
	id   int
	src  NodeID
	vc   VC
	body string
}

// record: 一次遞交的完整記錄，供全域驗收檢查因果安全。
type record struct {
	node  NodeID
	msgID int
	vc    VC
	order int // 全域遞交發生序（跨節點遞增計數）
	body  string
}

type causalNode struct {
	id        NodeID
	peers     []NodeID
	vc        VC
	nextMsgID *int
	buffer    []bcast
	rec       *[]record
	ordSeq    *int
	trace     *[]string
	script    map[int]string // now -> 要廣播的內容
}

func (n *causalNode) deliver(m bcast, now int) {
	n.vc[m.src] = m.vc[m.src] // 收下寄件者那格進度
	*n.ordSeq++
	*n.rec = append(*n.rec, record{n.id, m.id, m.vc, *n.ordSeq, m.body})
	*n.trace = append(*n.trace, fmt.Sprintf("t=%-2d N%d DELIVER m%d \"%s\" (from N%d %s) -> vc=%s",
		now, n.id, m.id, m.body, m.src, m.vc, n.vc))
}

// deliverable: Birman-Schiper-Stephenson 規則。
func (n *causalNode) deliverable(m bcast) bool {
	j := m.src
	if m.vc[j] != n.vc[j]+1 { // (1) j 的下一則、無跳號
		return false
	}
	for k := 0; k < N; k++ { // (2) 其他所有節點的因果前置都到齊
		if NodeID(k) == j {
			continue
		}
		if m.vc[k] > n.vc[k] {
			return false
		}
	}
	return true
}

// tryDrain: 反覆掃 buffer 到一輪無進展（一次遞交可能解鎖下一則）。
func (n *causalNode) tryDrain(now int) {
	for {
		progress := false
		for i := 0; i < len(n.buffer); i++ {
			if n.deliverable(n.buffer[i]) {
				m := n.buffer[i]
				n.buffer = append(n.buffer[:i], n.buffer[i+1:]...)
				n.deliver(m, now)
				progress = true
				break
			}
		}
		if !progress {
			return
		}
	}
}

func (n *causalNode) OnTick(now int, net *Net) {
	body, ok := n.script[now]
	if !ok {
		return
	}
	n.vc[n.id]++ // 廣播是本地事件
	id := *n.nextMsgID
	*n.nextMsgID++
	stamp := n.vc
	*n.ordSeq++ // 廣播者本地立即遞交自己的訊息
	*n.rec = append(*n.rec, record{n.id, id, stamp, *n.ordSeq, body})
	*n.trace = append(*n.trace, fmt.Sprintf("t=%-2d N%d BCAST   m%d \"%s\" %s", now, n.id, id, body, stamp))
	for _, p := range n.peers {
		net.Send(Message{From: n.id, To: p, Payload: bcast{id: id, src: n.id, vc: stamp, body: body}})
	}
}

func (n *causalNode) OnMessage(m Message, net *Net) {
	msg := m.Payload.(bcast)
	if n.deliverable(msg) {
		n.deliver(msg, net.Now())
		n.tryDrain(net.Now())
	} else {
		n.buffer = append(n.buffer, msg) // 前置沒到齊，緩衝
		*n.trace = append(*n.trace, fmt.Sprintf("t=%-2d N%d BUFFER  m%d \"%s\" (from N%d %s; local vc=%s, 因果前置未到)",
			net.Now(), n.id, msg.id, msg.body, msg.src, msg.vc, n.vc))
	}
}

// checkCausalSafety: 對每個節點，任何 a->b 的兩則訊息，a 遞交序須早於 b。
func checkCausalSafety(recs []record) (bool, string) {
	byNode := map[NodeID][]record{}
	for _, r := range recs {
		byNode[r.node] = append(byNode[r.node], r)
	}
	for node, rs := range byNode {
		for i := 0; i < len(rs); i++ {
			for j := 0; j < len(rs); j++ {
				if i == j {
					continue
				}
				if happensBefore(rs[i].vc, rs[j].vc) && rs[i].order > rs[j].order {
					return false, fmt.Sprintf("N%d 違反因果: m%d(%s) 應早於 m%d(%s) 但遞交序反了",
						node, rs[i].msgID, rs[i].vc, rs[j].msgID, rs[j].vc)
				}
			}
		}
	}
	return true, ""
}

func buildScenario(seed int64, scripts map[NodeID]map[int]string) (*Net, []*causalNode, *[]string, *[]record) {
	net := NewNet(seed)
	var trace []string
	var recs []record
	nextID := 0
	ordSeq := 0
	peersOf := func(self NodeID) []NodeID {
		var ps []NodeID
		for i := NodeID(0); i < N; i++ {
			if i != self {
				ps = append(ps, i)
			}
		}
		return ps
	}
	var nodes []*causalNode
	for id := NodeID(0); id < N; id++ {
		n := &causalNode{
			id: id, peers: peersOf(id), nextMsgID: &nextID,
			rec: &recs, ordSeq: &ordSeq, trace: &trace, script: scripts[id],
		}
		net.Add(id, n)
		nodes = append(nodes, n)
	}
	return net, nodes, &trace, &recs
}

func main() {
	// ===== 場景 1：高延遲抖動下的自然亂序 =====
	fmt.Println("========== 場景 1：高延遲抖動 ==========")
	net, nodes, trace, recs := buildScenario(11, map[NodeID]map[int]string{
		0: {1: "post"},
		1: {9: "reply-to-post"},
	})
	net.SetLatency(1, 8)
	net.Run(40)
	for _, line := range *trace {
		fmt.Println(line)
	}
	ok, why := checkCausalSafety(*recs)
	fmt.Printf("因果安全驗收: %v %s\n", ok, why)
	for _, nd := range nodes {
		fmt.Printf("  N%d final vc=%s buffer殘留=%d\n", nd.id, nd.vc, len(nd.buffer))
	}

	// ===== 場景 2：對抗性注入（保證 reply 先於 post 到達 N2）=====
	fmt.Println("\n========== 場景 2：對抗性亂序（reply 先到）==========")
	net2, nodes2, trace2, recs2 := buildScenario(3, map[NodeID]map[int]string{
		0: {1: "post"},
		1: {6: "reply-to-post"},
	})
	net2.Partition([]NodeID{0, 1}, []NodeID{2})
	net2.Run(9) // N0 廣播 post、N1 收到並廣播 reply，都到不了 N2
	net2.Heal()
	net2.SetLatency(1, 1)
	// 先送因果後繼 reply，讓它先到 N2；再送因果前置 post
	net2.Send(Message{From: 1, To: 2, Payload: bcast{id: 1, src: 1, vc: VC{1, 1, 0}, body: "reply-to-post"}})
	net2.Run(3)
	net2.Send(Message{From: 0, To: 2, Payload: bcast{id: 0, src: 0, vc: VC{1, 0, 0}, body: "post"}})
	net2.Run(20)
	for _, line := range *trace2 {
		fmt.Println(line)
	}
	ok2, why2 := checkCausalSafety(*recs2)
	fmt.Printf("因果安全驗收: %v %s\n", ok2, why2)

	var n2recs []record
	for _, r := range *recs2 {
		if r.node == 2 {
			n2recs = append(n2recs, r)
		}
	}
	sort.Slice(n2recs, func(i, j int) bool { return n2recs[i].order < n2recs[j].order })
	fmt.Print("N2 實際遞交順序: ")
	for _, r := range n2recs {
		fmt.Printf("%q ", r.body)
	}
	fmt.Println()
	for _, nd := range nodes2 {
		fmt.Printf("  N%d final vc=%s buffer殘留=%d\n", nd.id, nd.vc, len(nd.buffer))
	}
}
```

真跑（WSL, Go 1.18.1）：

```
$ go run .
========== 場景 1：高延遲抖動 ==========
t=1  N0 BCAST   m0 "post" [1 0 0]
t=2  N1 DELIVER m0 "post" (from N0 [1 0 0]) -> vc=[1 0 0]
t=9  N2 DELIVER m0 "post" (from N0 [1 0 0]) -> vc=[1 0 0]
t=9  N1 BCAST   m1 "reply-to-post" [1 1 0]
t=14 N2 DELIVER m1 "reply-to-post" (from N1 [1 1 0]) -> vc=[1 1 0]
t=15 N0 DELIVER m1 "reply-to-post" (from N1 [1 1 0]) -> vc=[1 1 0]
因果安全驗收: true 
  N0 final vc=[1 1 0] buffer殘留=0
  N1 final vc=[1 1 0] buffer殘留=0
  N2 final vc=[1 1 0] buffer殘留=0

========== 場景 2：對抗性亂序（reply 先到）==========
t=1  N0 BCAST   m0 "post" [1 0 0]
t=2  N1 DELIVER m0 "post" (from N0 [1 0 0]) -> vc=[1 0 0]
t=6  N1 BCAST   m1 "reply-to-post" [1 1 0]
t=7  N0 DELIVER m1 "reply-to-post" (from N1 [1 1 0]) -> vc=[1 1 0]
t=10 N2 BUFFER  m1 "reply-to-post" (from N1 [1 1 0]; local vc=[0 0 0], 因果前置未到)
t=10 N2 DELIVER m0 "post" (from N0 [1 0 0]) -> vc=[1 0 0]
t=10 N2 DELIVER m1 "reply-to-post" (from N1 [1 1 0]) -> vc=[1 1 0]
因果安全驗收: true 
N2 實際遞交順序: "post" "reply-to-post" 
  N0 final vc=[1 1 0] buffer殘留=0
  N1 final vc=[1 1 0] buffer殘留=0
  N2 final vc=[1 1 0] buffer殘留=0
```

**讀輸出**：場景 2 是關鍵。看 N2 那三行——`t=10` N2 先收到 `reply`（m1），判定不可遞交（本地 vc 還是 `[0 0 0]`，reply 的 vc `[1 1 0]` 要求 N0 那格已有 1，但 N2 還沒收過 post），於是 **BUFFER**。同一時刻 post（m0）到了，可遞交，**DELIVER**；deliver 後 `tryDrain` 重掃 buffer，發現 reply 現在可遞交了（N2 的 vc 已變 `[1 0 0]`），撈出來 **DELIVER**。最終 N2 的實際遞交順序是 `"post" "reply-to-post"`——**雖然 reply 先到，卻後遞交**，因果被守住。兩個場景的 `因果安全驗收: true` 和 `buffer殘留=0` 證明機制正確。

</details>

## 延伸挑戰

1. **GC 舊 VC entry（緩衝與記憶體）**：目前 buffer 裡的訊息一旦前置遺失（例如場景表最後一列，post 永遠不到），reply 會**永久卡在 buffer**，記憶體只增不減。加一個機制：追蹤「所有節點都已遞交到的最小 vector clock」（穩定向量，stable VC），把所有節點都已遞交的訊息從各種暫存結構裡清掉。這是真實系統（如 COPS）必須處理的「因果依賴的垃圾回收」。想想：一則訊息什麼時候可以安全地從系統裡徹底忘掉？

2. **偵測「前置永遠不會來」**：因果遞交的黑暗面是「一則遺失的前置會讓所有依賴它的訊息永久卡住」。加一個 timeout：一則訊息在 buffer 裡待超過 T 個 tick 還遞交不出去，就報「疑似因果依賴遺失」。這對照 Ch 7 的教訓——需要「等齊前置」的協定，容不下真正的訊息遺失，得靠重傳補救。

3. **加入節點動態成員**：目前 `N` 寫死成 3。如果節點會加入/離開，vector clock 的維度就會變。想想怎麼處理「新節點的 vc 該從哪個維度開始」，以及這對照 Ch 6 踩雷 1（VC 會腐爛/膨脹）——這正是 dotted version vector 要解決的問題。

## 本練習重點整理

- 因果遞交 = 用 vector clock 在**運行時**保證「應用層永不先看到因果後繼、才看到前置」，是因果一致性的實作核心。
- 判定規則兩條（BSS 演算法）：來自 j 的訊息，`m.vc[j] == vc[j]+1`（j 的下一則）且對其他所有 k、`m.vc[k] <= vc[k]`（前置都到齊）。
- 收到不可遞交的訊息就 **buffer**；每次 deliver 後要 **drain 並重掃到一輪無進展**，因為一次遞交可能連鎖解鎖多則。
- 驗收靠一個獨立的因果安全檢查：任何 `a→b` 的兩則，a 的遞交序須早於 b——真跑兩場景（自然亂序 + 對抗性亂序）都通過。
- 黑暗面：**前置遺失會讓依賴它的訊息永久卡在 buffer**，真實系統要靠重傳、timeout、與穩定向量 GC 對抗（延伸挑戰）。

Part 1 到此完整收尾。你已經有了：判斷因果的工具（Lamport/vector clock）、以及一個把因果變成運行時保證的訊息層。接下來 Part 2 換個角度——先不管「順序」，問「為什麼要複製資料，以及複製帶來的一致性代價」。

→ [Ch 8 為什麼複製](./08-why-replicate.md)
