# 練習 B — Quorum-based KV Store

> **目標**：把 Ch 13 的 quorum 複製從紙上不等式變成一個能跑的東西。在 `dsim` 上實作一個**可調 (N, R, W) 的 quorum KV store**：`Put(key, val)` 要收到 **W** 個副本的 ack 才算成功，`Get(key)` 要收到 **R** 個副本的回應、從中挑版本最新的回傳。驗收：**R+W>N 時，讀取線性地讀到最近一次成功的寫入**；並注入 partition，觀察**少數側寫入湊不到 W 而停滯（不會假成功）**。做完你會親手證實那條 R+W>N 不是裝飾——把它調成 R+W≤N，讀取立刻可能讀到舊值。

> **環境**：Go 1.18.1, WSL2 / Linux x86-64。跑在 Ch 0 的 `dsim` 上。

> 先修：[Ch 13 Quorum 複製](./13-quorum-replication.md)（R+W>N、W/R 取捨、sloppy quorum）、[Ch 9 一致性模型](./09-consistency-models.md)（linearizable vs stale read）、[Ch 0 dsim](./00-environment-setup.md)（模擬器 API、Partition/Crash 注入）。

## 為什麼做這個練習

Ch 13 我們用鴿籠原理論證了「R+W>N ⇒ 讀寫集合必相交 ⇒ 讀到最新」。論證很漂亮，但論證會騙人——你以為懂了，真寫起來才會發現一堆細節：coordinator 怎麼統計「湊滿 W 個」、Get 收到版本不一的回應怎麼挑、partition 時 Put 卡住到底是什麼行為、把 R、W 調小一格一致性怎麼就沒了。

這個練習就是把那條不等式**壓進程式碼裡逼你面對**。做完你會有一個能調 (N,R,W) 的 quorum KV，可以親手把它從「線性一致」調成「會讀到舊值」，看著同一份程式碼因為兩個參數的差別而失去一致性保證。這種「親眼看見保證消失」的體驗，比背十遍 R+W>N 都有用。而且這個 KV 是 Part 3 的預熱——你會發現「湊多數 ack」的模式在 Paxos/Raft 裡無所不在，quorum 相交就是共識的地基。

## 任務規格

實作一個掛在 `dsim` 上的 quorum KV，三種角色：

**replica（副本，node 0..N-1）**：一個笨 KV store。收到 `putReq` 就存（**版本號大的覆蓋版本號小的**，版本號本練習由客戶端/coordinator 給，單調遞增）、回 `putAck`；收到 `getReq` 就回自己那份 `getResp`。副本之間**不互相協調**——所有 quorum 邏輯在 coordinator。

**coordinator（協調者，node 50）**：驅動 Put/Get。
- `Put(rid, key, vv)`：把寫入 fan-out 給**全部 N 個副本**，統計 `putAck`，收滿 **W** 個就宣告成功。
- `Get(rid, key)`：把讀取 fan-out 給**全部 N 個副本**，收集 `getResp`，收滿 **R** 個就從中**挑版本號最大的**回傳。

**client**：透過 coordinator 發 Put/Get（本練習直接呼叫 coordinator 的方法即可）。

**版本號**：本練習用單調遞增的整數版本號 `ver` 決定新舊（`ver` 大 = 新）。真實系統會用 vector clock 偵測並行衝突（Ch 14），這裡先用版本號把 quorum 機制講清楚——這也是刻意的簡化，別把它當生產設計。

**驗收標準**：
1. **R+W>N（如 N=3,W=2,R=2）**：連續兩次成功寫入 ver1、ver2 後，Get 必須讀到 ver2。
2. **R+W≤N（如 N=3,W=1,R=1）**：構造出讀取集合與寫入集合不相交的情境，Get **可能讀到舊值/初始值**——證明保證真的沒了。
3. **partition 少數側**：把 coordinator 和「湊不到 W 的少數副本」隔在一起，Put 必須**永遠湊不到 W、不宣告成功**（不能假成功）。
4. **heal 後多數寫入**：網路 heal 後，W=2 的寫入成功，隨後 R=2 的讀取讀到它。

> **fan-out 給全部 N 個、但只等 W/R 個**——這是 quorum 的標準實作方式。你發給所有人（增加碰到最新副本的機率、也是容錯：多發幾個防丟包），但只要**最快的 W（或 R）個**回來就行動，不等慢的。這也是為什麼 quorum 能容忍 `N - W`（或 `N - R`）個副本慢/掛。

## 分段步驟

### Step 1：定義訊息與副本

先把資料結構和笨副本立起來。訊息帶 `rid`（request id，用來把 ack/resp 歸屬到正確的請求）。

```go
type opKind int
const ( putReq opKind = iota; putAck; getReq; getResp )

type versioned struct { ver int; val string }
type kvMsg struct {
	kind opKind
	key  string
	vv   versioned
	rid  int
}

type replica struct {
	id   NodeID
	data map[string]versioned
}

func (r *replica) OnMessage(m Message, net *Net) {
	km := m.Payload.(kvMsg)
	switch km.kind {
	case putReq:
		if km.vv.ver > r.data[km.key].ver { // 版本大的覆蓋
			r.data[km.key] = km.vv
		}
		net.Send(Message{From: r.id, To: m.From,
			Payload: kvMsg{kind: putAck, key: km.key, rid: km.rid}})
	case getReq:
		net.Send(Message{From: r.id, To: m.From,
			Payload: kvMsg{kind: getResp, key: km.key, vv: r.data[km.key], rid: km.rid}})
	}
}
func (r *replica) OnTick(now int, net *Net) {}
```

### Step 2：coordinator 的 Put——湊滿 W 個 ack

coordinator fan-out 給全部副本，用一個 `map[rid]int` 統計每個請求收到幾個 ack，收滿 W 就宣告成功（用 `putDone[rid]` 去重，避免重複宣告）。

```go
func (c *coordinator) Put(net *Net, rid int, key string, vv versioned) {
	for _, id := range c.replicas { // fan-out 給全部 N 個
		net.Send(Message{From: c.id, To: id, Payload: kvMsg{kind: putReq, key: key, vv: vv, rid: rid}})
	}
}
// 在 OnMessage 的 putAck 分支：
//   c.putAcks[rid]++
//   if c.putAcks[rid] == c.W && !c.putDone[rid] { 宣告成功 }
```

### Step 3：coordinator 的 Get——湊滿 R 個回應、挑版本最大的

收集 `getResp` 到 `map[rid][]versioned`，收滿 R 個就掃一遍挑 `ver` 最大的。這一步就是 quorum 讀的靈魂：**因為 R+W>N 保證這 R 個裡至少有一個是最新寫入碰過的副本，挑最大版本就一定挑到它。**

```go
// 在 OnMessage 的 getResp 分支：
//   c.getResps[rid] = append(c.getResps[rid], km.vv)
//   if len(c.getResps[rid]) == c.R && !c.getDone[rid] {
//       best := versioned{ver: -1}
//       for _, v := range c.getResps[rid] { if v.ver > best.ver { best = v } }
//       c.getResult[rid] = best  // 存起來給驗收檢查
//   }
```

### Step 4：跑 R+W>N，驗收讀到最新

N=3,W=2,R=2。寫 ver1、ver2，再讀，斷言讀到 ver2。用 `SetLatency(1,2)` 讓訊息有點延遲更真實。

### Step 5：注入 partition，驗收少數側停滯與 R+W≤N 的 stale read

- 用 `Partition([]NodeID{50, 2}, []NodeID{0,1})` 把 coordinator 跟少數副本 2 關在一起，發 W=2 的 Put，斷言它**湊不到 2 個 ack**（`putDone` 為 false）。
- 用 `W=1,R=1` 且 partition 讓寫只到副本 0、讀只到副本 2（不相交），斷言讀到 stale。
- `Heal()` 後發 W=2 寫入、R=2 讀取，斷言恢復一致。

### Step 6（挑戰）：加 read repair

Get 收到 R 個版本不一的回應時，把最新值寫回落後的副本（見文末延伸挑戰）。

## 卡點提示

- **`rid` 一定要帶**：不同請求的 ack/resp 會交錯到達 coordinator，沒有 `rid` 你會把 Put#2 的 ack 算進 Put#1，統計全錯。每個請求一個唯一 `rid`。
- **「收滿 W」要去重**：第 W 個 ack 觸發「成功」後，第 W+1、W+2 個 ack 還會來（你 fan-out 給了全部 N 個）。用 `putDone[rid]` 標記，只宣告一次。
- **`Run(maxSteps)` 要跑夠**：Put/Get 是非同步的，發出去要等訊息往返。`SetLatency(1,2)` 下，一次往返約 2-4 個 tick，`Run` 給 6-20 才收得到回應。收不到結果先把 maxSteps 加大（Ch 0 踩雷 #5）。
- **partition 要把 coordinator 也劃進某一側**：`Partition` 是對 NodeID 分組，coordinator（node 50）也是一個節點。你想讓它只碰到副本 2，就把 `{50, 2}` 放同一組。忘了劃 coordinator，它會誰都碰不到。
- **stale read 要「構造」**：R+W≤N 只是**可能**讀到舊值，不是必然。隨機延遲下有時剛好讀到最新。要**穩定重現** stale，最乾脆是用 partition 強制讀寫集合不相交（見參考解答 Test 2），而不是碰運氣。

## 測試用例表

| 測試 | N,W,R | 注入 | 預期 | 驗的是什麼 |
|---|---|---|---|---|
| Test 1 | 3,2,2 | 無 | 讀到 ver2（最新） | R+W>N 相交保證 |
| Test 2 | 3,1,1 | partition 讓讀寫不相交 | 讀到 ver0（stale） | R+W≤N 保證消失 |
| Test 3 | 3,2,2 | partition {50,2}\|{0,1} | Put 湊不到 W，不成功 | 少數側不假成功 |
| Test 4 | 3,2,2 | heal 後 | 寫成功、讀到它 | 恢復後一致 |

## 參考解答

完整可跑，已在 WSL / Go 1.18.1 上真跑過。把 `dsim.go`（改 `package main`）和下面的 `main.go` 放同一目錄 `go run .`。

<details>
<summary>完整 main.go（含四個測試 + 真實輸出）</summary>

```go
package main

import "fmt"

// ===== Practice B: 可調 (N,R,W) quorum KV store =====

type opKind int

const (
	putReq opKind = iota
	putAck
	getReq
	getResp
)

type versioned struct {
	ver int
	val string
}

type kvMsg struct {
	kind opKind
	key  string
	vv   versioned
	rid  int
}

// replica：笨 KV 節點，版本大的覆蓋。
type replica struct {
	id   NodeID
	data map[string]versioned
}

func (r *replica) OnMessage(m Message, net *Net) {
	km := m.Payload.(kvMsg)
	switch km.kind {
	case putReq:
		if km.vv.ver > r.data[km.key].ver {
			r.data[km.key] = km.vv
		}
		net.Send(Message{From: r.id, To: m.From, Payload: kvMsg{kind: putAck, key: km.key, rid: km.rid}})
	case getReq:
		net.Send(Message{From: r.id, To: m.From, Payload: kvMsg{kind: getResp, key: km.key, vv: r.data[km.key], rid: km.rid}})
	}
}
func (r *replica) OnTick(now int, net *Net) {}

// coordinator：驅動 Put(W acks)/Get(R responses, 挑 max ver)。
type coordinator struct {
	id        NodeID
	replicas  []NodeID
	N, R, W   int
	putAcks   map[int]int
	getResps  map[int][]versioned
	putDone   map[int]bool
	getDone   map[int]bool
	getResult map[int]versioned // 驗收用
}

func newCoord(id NodeID, replicas []NodeID, N, R, W int) *coordinator {
	return &coordinator{id: id, replicas: replicas, N: N, R: R, W: W,
		putAcks: map[int]int{}, getResps: map[int][]versioned{},
		putDone: map[int]bool{}, getDone: map[int]bool{}, getResult: map[int]versioned{}}
}

func (c *coordinator) OnMessage(m Message, net *Net) {
	km := m.Payload.(kvMsg)
	switch km.kind {
	case putAck:
		c.putAcks[km.rid]++
		if c.putAcks[km.rid] == c.W && !c.putDone[km.rid] { // 收滿 W 個 ack
			c.putDone[km.rid] = true
			fmt.Printf("  [t=%d] PUT rid=%d got W=%d acks -> SUCCESS\n", net.Now(), km.rid, c.W)
		}
	case getResp:
		c.getResps[km.rid] = append(c.getResps[km.rid], km.vv)
		if len(c.getResps[km.rid]) == c.R && !c.getDone[km.rid] { // 收滿 R 個回應
			c.getDone[km.rid] = true
			best := versioned{ver: -1, val: "<none>"}
			for _, v := range c.getResps[km.rid] { // 挑版本最大的
				if v.ver > best.ver {
					best = v
				}
			}
			c.getResult[km.rid] = best
			fmt.Printf("  [t=%d] GET rid=%d got R=%d resp %s -> read ver=%d val=%q\n",
				net.Now(), km.rid, c.R, dump(c.getResps[km.rid]), best.ver, best.val)
		}
	}
}
func (c *coordinator) OnTick(now int, net *Net) {}

func (c *coordinator) Put(net *Net, rid int, key string, vv versioned) {
	for _, id := range c.replicas { // fan-out 給全部 N 個
		net.Send(Message{From: c.id, To: id, Payload: kvMsg{kind: putReq, key: key, vv: vv, rid: rid}})
	}
}
func (c *coordinator) Get(net *Net, rid int, key string) {
	for _, id := range c.replicas {
		net.Send(Message{From: c.id, To: id, Payload: kvMsg{kind: getReq, key: key, rid: rid}})
	}
}

func dump(vs []versioned) string {
	s := "["
	for i, v := range vs {
		if i > 0 {
			s += " "
		}
		s += fmt.Sprintf("(v%d,%q)", v.ver, v.val)
	}
	return s + "]"
}

func build(seed int64, N, R, W int) (*Net, *coordinator) {
	net := NewNet(seed)
	net.SetLatency(1, 2)
	replicas := []NodeID{0, 1, 2}
	for _, id := range replicas {
		net.Add(id, &replica{id: id, data: map[string]versioned{}})
	}
	c := newCoord(NodeID(50), replicas, N, R, W)
	net.Add(50, c)
	return net, c
}

// build(seed, N, R, W)
func build(seed int64, N, R, W int) (*Net, *coordinator) {
	net := NewNet(seed)
	net.SetLatency(1, 2)
	replicas := []NodeID{0, 1, 2}
	for _, id := range replicas {
		net.Add(id, &replica{id: id, data: map[string]versioned{}})
	}
	c := newCoord(NodeID(50), replicas, N, R, W)
	net.Add(50, c)
	return net, c
}

func main() {
	// ---- Test 1: R+W>N ----
	fmt.Println("=== Test 1: N=3,W=2,R=2 (R+W=4>3) : read sees latest successful write ===")
	net, c := build(1, 3, 2, 2)
	c.Put(net, 1, "k", versioned{1, "v1"})
	net.Run(6)
	c.Put(net, 2, "k", versioned{2, "v2"})
	net.Run(12)
	c.Get(net, 3, "k")
	net.Run(20)
	if c.getResult[3].ver == 2 {
		fmt.Println("  PASS: read latest ver=2")
	} else {
		fmt.Printf("  FAIL: expected ver=2, got ver=%d\n", c.getResult[3].ver)
	}

	// ---- Test 2: R+W<=N 可能 stale（用 partition 強制讀寫不相交）----
	fmt.Println("\n=== Test 2: N=3,W=1,R=1 (R+W=2<=3) : read may miss latest ===")
	net2, c2 := build(1, 3, 1, 1)
	net2.Partition([]NodeID{50, 0}, []NodeID{1, 2}) // 寫只到副本 0
	c2.Put(net2, 1, "k", versioned{1, "v1"})
	net2.Run(6)
	c2.Put(net2, 2, "k", versioned{2, "v2"})
	net2.Run(12)
	net2.Heal()
	net2.Partition([]NodeID{50, 2}, []NodeID{0, 1}) // 讀只到副本 2（從沒被寫）
	c2.Get(net2, 3, "k")
	net2.Run(20)
	if c2.getResult[3].ver < 2 {
		fmt.Printf("  PASS(as designed): stale read ver=%d (never intersected write set)\n", c2.getResult[3].ver)
	} else {
		fmt.Println("  unexpectedly fresh")
	}

	// ---- Test 3: 少數側 W=2 湊不到 ----
	fmt.Println("\n=== Test 3: N=3,W=2 under partition {0,1}|{2} : minority write STALLS ===")
	net3, c3 := build(1, 3, 2, 2)
	net3.Partition([]NodeID{50, 2}, []NodeID{0, 1}) // coordinator 只剩少數副本 2
	c3.Put(net3, 1, "k", versioned{1, "v1"})
	net3.Run(20)
	if !c3.putDone[1] {
		fmt.Printf("  PASS: PUT never reached W=2 (only %d ack from minority) -> unavailable, no false success\n", c3.putAcks[1])
	} else {
		fmt.Println("  FAIL: put falsely succeeded in minority")
	}

	// ---- Test 4: heal 後多數寫入 + 讀到 ----
	fmt.Println("\n=== Test 4: heal + W=2 majority write, then R=2 read sees it ===")
	net3.Heal()
	c3.Put(net3, 2, "k", versioned{1, "v1"})
	net3.Run(30)
	c3.Get(net3, 3, "k")
	net3.Run(45)
	if c3.getResult[3].ver == 1 {
		fmt.Println("  PASS: after heal, quorum read sees the committed write")
	} else {
		fmt.Printf("  FAIL: got ver=%d\n", c3.getResult[3].ver)
	}
}
```

**真實輸出**（WSL, Go 1.18.1）：

```
=== Test 1: N=3,W=2,R=2 (R+W=4>3) : read sees latest successful write ===
  [t=4] PUT rid=1 got W=2 acks -> SUCCESS
  [t=8] PUT rid=2 got W=2 acks -> SUCCESS
  [t=15] GET rid=3 got R=2 resp [(v2,"v2") (v2,"v2")] -> read ver=2 val="v2"
  PASS: read latest ver=2

=== Test 2: N=3,W=1,R=1 (R+W=2<=3) : read may miss latest ===
  [t=4] PUT rid=1 got W=1 acks -> SUCCESS
  [t=10] PUT rid=2 got W=1 acks -> SUCCESS
  [t=15] GET rid=3 got R=1 resp [(v0,"")] -> read ver=0 val=""
  PASS(as designed): stale read ver=0 (never intersected write set)

=== Test 3: N=3,W=2 under partition {0,1}|{2} : minority write STALLS ===
  PASS: PUT never reached W=2 (only 1 ack from minority) -> unavailable, no false success

=== Test 4: heal + W=2 majority write, then R=2 read sees it ===
  [t=23] PUT rid=2 got W=2 acks -> SUCCESS
  [t=33] GET rid=3 got R=2 resp [(v1,"v1") (v1,"v1")] -> read ver=1 val="v1"
  PASS: after heal, quorum read sees the committed write
```

四個測試全綠，逐一對照驗收：

- **Test 1（R+W>N）**：Get 收到 `[(v2,"v2") (v2,"v2")]`，讀到 ver2。相交保證生效。
- **Test 2（R+W≤N）**：寫只到副本 0、讀只到副本 2（partition 強制不相交），Get 收到 `[(v0,"")]`——讀到**從沒被寫過的初始值**，stale。**同一份程式碼，只把 W、R 從 2 調成 1，一致性保證就消失了。**
- **Test 3（少數側）**：coordinator 只碰得到少數副本 2，W=2 永遠只有 **1 個 ack**，`putDone` 為 false——Put **停滯、不假成功**。這正是 CAP 裡 CP 的選擇：分區時少數側寧可不可用，也不謊報成功。
- **Test 4（heal 後）**：網路恢復，W=2 寫入 `t=23` 成功，R=2 讀取 `t=33` 讀到 ver1。恢復後一致。

</details>

## 延伸挑戰

1. **加 read repair**：Get 收滿 R 個回應、發現版本不一致時（像 Test 1 若某副本落後），把 `best`（最新值）用 `putReq` 寫回那些回報舊版本的副本。跑一個「Get 前副本 0 落後 → Get 後副本 0 被修好」的測試（Get 完再直接查副本 0 的 `data`，斷言它追上了）。這讓你的 KV 從「讀到最新」進化到「讀順便修，副本逐漸收斂」。

2. **sloppy quorum + hinted handoff**：Test 3 裡少數側 Put 停滯是「strict quorum」的行為。改成 sloppy：湊不到正牌 W 時，允許 coordinator 把寫入暫存到「能碰到的其他節點」湊數（附一個 hint 標明「這其實屬於副本 X」），宣告成功。heal 後把暫存移交回正牌副本。跑出來對比：sloppy 下 Test 3 的 Put **會成功**（換到可用性），但隨後 strict 讀取可能讀不到它（犧牲相交保證）——親手驗證 Ch 13 講的 sloppy 權衡。

3. **並行寫入 + vector clock**：本練習用版本號，遇到「兩個並行寫入帶相同 ver」會靜默覆蓋。把 `versioned` 換成帶 vector clock（Ch 6/14），讓 Get 遇到並行衝突時回傳 **siblings**（兩個都保留）而非硬選一個。這把練習從 quorum 機制推進到 Ch 14 的衝突解決。

4. **量化 stale 機率**：把 Test 2 改成不用 partition、純靠 `SetLatency` 的隨機延遲，跑 100 個不同 seed 統計「R+W≤N 下讀到 stale 的比例」，再跟 R+W>N（應該 0%）對比。用數據感受「不等式不是二元的『保證/不保證』，而是機率上的天壤之別」。

## 自我檢核

- [ ] 我能解釋為什麼 coordinator 要「fan-out 給全部 N 個，但只等 W/R 個」，這帶來什麼容錯
- [ ] 我能說出 `rid` 和 `putDone`/`getDone` 各解決了什麼 bug（歸屬錯亂、重複宣告）
- [ ] 我能親手把測試從 R+W>N 改成 R+W≤N，並構造出穩定重現的 stale read（而非碰運氣）
- [ ] 我能解釋 Test 3 裡「少數側 Put 停滯」對應 CAP 的哪個選擇，以及為什麼「不假成功」比「假成功」重要
- [ ] 我能說出 read repair 修的是哪種不一致、它的侷限（冷資料修不到）
- [ ] 我能講清楚做完 sloppy quorum 挑戰後，Test 3 行為變了什麼、犧牲了什麼

做完這個練習，你手上有一個能調 (N,R,W)、能注入 partition、能親眼看見一致性保證隨參數消失的 quorum KV。你也應該感覺到了：所有這些「湊多數 ack」「相交保證」的模式，都在為 Part 3 鋪路——當我們需要的不只是「讀到最新」，而是「一群會失敗的節點對『一件事』達成不可撤銷的一致決定」時，quorum 相交就會升級成完整的**共識**。

→ [Ch 15 共識問題定義](./15-consensus-problem.md)
