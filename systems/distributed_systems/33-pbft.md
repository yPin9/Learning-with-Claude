# Ch 33 — PBFT：第一個實用的非同步 BFT

> **目標**：從零推導 PBFT（Practical Byzantine Fault Tolerance，Castro-Liskov 1999）的三階段協定——pre-prepare、prepare、commit——理解每個階段在防哪種拜占庭攻擊，推導 2f+1 quorum 的交集論證，最後在 dsim 上跑出「正常路徑所有節點達成一致」和「拜占庭 primary 嘗試 equivocation 但被協定擋下」兩個場景。

> **環境**：Go 1.18.1, WSL2/Linux x86-64。程式碼跑在 [Ch 0 的 dsim 模擬器](./00-environment-setup.md)上。

## 為什麼需要這個？

Ch 32 確立了 BFT 共識在部分同步網路下需要 3f+1 個節點，但沒有給出一個實際能跑的非同步演算法。OM(m) 在同步假設下可行，但（1）通訊複雜度 O(n^m) 爆炸、（2）同步假設過強——真實資料中心網路是部分同步的（GST 之後收斂，但你不知道 GST 什麼時候到）。

1999 年，Miguel Castro 和 Barbara Liskov 在 OSDI 發表了 **PBFT（Practical Byzantine Fault Tolerance）**。這是第一個宣稱「實用」的非同步 BFT 協定：

- 部分同步假設（不需要知道訊息延遲上界）
- O(n²) 通訊複雜度——對小 n 可接受
- 在當時 200MHz 的機器上跑出幾千 req/s 的吞吐量

PBFT 在之後 20 年成為學術和工程上的基準。Hyperledger Fabric 的早期版本用它，Tendermint 和 HotStuff 都從它演化而來。理解 PBFT 的「每個階段在防什麼」，是讀懂任何現代 BFT 協定的前提。

> 若你還沒讀 Ch 32，**先讀它**——本章假設你知道「為什麼需要 3f+1」和「equivocation 是什麼」。

## 先建立直覺：為什麼需要三個階段

Paxos/Raft 只有兩階段（prepare/accept 或 vote/replicate），為什麼 PBFT 要三個？

答案藏在 crash-fault 與 Byzantine-fault 的差異裡。

在 Paxos 中，你需要兩個階段來處理「多個 proposer 競爭同一個序號」的問題（Phase 1 佔坑，Phase 2 正式提案）。拜占庭環境多了一個威脅：**primary 可以對不同 replica 說不同的話（equivocation）**——比如對 replica 1 說「序號 5 對應值 A」，對 replica 2 說「序號 5 對應值 B」。

對付這個，必須讓 replica 在接受之前先**廣播自己的準備票，讓大家都知道大家知道的是什麼**。這就是 prepare 階段。然後在 commit 之前再確認一輪，防止 view change 時的跨輪次不一致。

三個階段一句話概括：

```
Pre-Prepare  →  排序（sequencing）：primary 給序號
Prepare      →  一輪廣播讓大家確認同一 view 內對序號的共識
Commit       →  跨 view 鎖定：即便 primary 換了，已 prepared 的值不丟
```

## 設定：角色與假設

**假設**：
- N = 3f+1 個節點，最多 f 個可以是拜占庭的（包括 primary）
- 部分同步網路（最終訊息會到，但時間不確定）
- 節點有公鑰/私鑰，訊息帶簽章（叛徒無法偽造他人簽名）
- 所有請求有明確的 client + 唯一序號

**角色**：
- **Primary（主節點）**：每個 view 有一個 primary，負責把 client 請求排序並發 Pre-Prepare。`primary = view mod N`。
- **Replica（副本）**：剩下的 3f 個節點。正常模式下驗證 primary 的排序，廣播自己的 Prepare/Commit 投票。
- **Client**：傳送請求，收集 f+1 個相同的回覆才確認成功（因為最多 f 個回覆可能來自拜占庭節點）。

## 三階段協定：正常路徑

### Pre-Prepare：primary 排序

客戶端把請求 `(op, timestamp, client)` 傳給 primary。Primary 指派一個**序號（sequence number）** n 和當前 **view v**，對所有 replica 廣播：

```
Pre-Prepare(v, n, digest, sig_primary)
```

`digest` 是 client 請求的雜湊（防止傳輸整個大型請求，同時形成不可篡改的綁定）。

**這個階段防什麼？**
Pre-Prepare 建立了「序號到請求的映射」。Replica 收到後驗證：
1. Signature 是否是合法的 primary 簽的
2. 同一個 (v, n) 之前是否已經接受過不同 digest 的 Pre-Prepare（若是，拒絕——primary 在 equivocate）

若驗證通過，replica 接受這個 Pre-Prepare，把它加入自己的 log，並廣播 Prepare。

**注意**：Pre-Prepare 本身只保證「primary 說序號 n 對應 digest d」，但 primary 可能同時對不同 replica 說不同的 digest。Prepare 階段就是把這個欺騙行為曝光。

### Prepare：replica 廣播，防止同 view 內的 equivocation

每個 replica（包括 primary 本身）廣播：

```
Prepare(v, n, digest, i, sig_i)
```

其中 `i` 是自己的 ID。

每個節點**收集 2f 個 Prepare 訊息**（加上自己的 Pre-Prepare 接受，共 2f+1 個來源）。當且僅當它拿到**同一個 (v, n, digest) 的 2f 個 Prepare**，它才算 **prepared** 這個 (v, n, digest)。

**這個階段防什麼？**

關鍵不變式：**在同一個 view v 裡，不可能有兩個不同的 digest 分別達到 prepared 狀態**。

為什麼？假設 `(v, n, d1)` 和 `(v, n, d2)` 都 prepared（d1 ≠ d2）。各自需要 2f 個 Prepare，總共 4f 個投票。但總節點數只有 3f+1，扣掉最多 f 個拜占庭節點，誠實節點只有 2f+1 個。用鴿巢原理：4f 個投票 from 最多 3f+1 個節點，某個誠實節點必須**同時**投了 d1 和 d2——但誠實節點只會投它驗證通過的那個 digest，不會同時投兩個。矛盾。

所以 prepared 是在 **同一 view 內的誠實認可**。但如果 primary 換了（view change），一個在舊 view 裡已 prepared 但還沒 committed 的值，在新 view 裡要怎麼保證不被覆蓋？這是 commit 階段要解決的問題。

### Commit：跨 view 鎖定

達到 prepared 狀態後，replica 廣播：

```
Commit(v, n, digest, i, sig_i)
```

當節點收到 **2f+1 個** `Commit(v, n, digest, *)` 訊息（含自己的），它就 **committed** 這個 (v, n, digest)，執行請求，回覆 client。

**為什麼是 2f+1 而非 2f？**

Commit quorum 需要足夠大，使得即使 primary 換了（view change），新 primary 在重建狀態時能從存活節點中找到至少一個已 committed 的節點，並確保那個 committed 的值在新 view 裡不被改變。

具體：在 3f+1 個節點裡，view change 時最多 f 個節點可能無回應（崩潰或拜占庭）。新 primary 能聯絡到的至少 2f+1 個節點。這 2f+1 個裡面最多 f 個是拜占庭，誠實的有 f+1 個。若之前有人 committed，Commit quorum 有 2f+1 個，其中最多 f 個拜占庭，誠實的有 f+1 個——與新 primary 聯絡到的 f+1 個誠實節點必然有重疊（確切地說至少 1 個）。這 1 個誠實節點知道舊的 committed 值，view change 機制讓新 primary 必須繼承這個值，不能覆蓋。

```
完整三階段流程（N=4，f=1，正常路徑）：

Client                 Primary(0)         R1          R2          R3
  │                        │               │            │           │
  │─── Request(op) ───────►│               │            │           │
  │                        │               │            │           │
  │                   ─── PRE-PREPARE ──►  │            │           │
  │                        │── PP(0,1,d) ──►│            │           │
  │                        │── PP(0,1,d) ──────────────►│           │
  │                        │── PP(0,1,d) ──────────────────────────►│
  │                        │               │            │           │
  │                        │  ◄── PREPARE ──────────────────────────┤
  │                        │               │◄─ P(0,1,d)─────────────┤
  │                        │  ◄─────────── │◄─ P(0,1,d)─────────────┤
  │                        │               │ ◄──────── P(0,1,d) ────┤
  │                       [每個節點收到 2f=2 個 Prepare → PREPARED]
  │                        │               │            │           │
  │                        │── COMMIT ──────────────────────────────►
  │                        │               │──────── Commit ────────►
  │                        │◄──────────────│◄──────── Commit ───────►
  │                        │               │ ◄───────── Commit ──────┤
  │                       [每個節點收到 2f+1=3 個 Commit → COMMITTED]
  │                        │               │            │           │
  │◄── Reply(result) ──────│               │            │           │
  │◄────────────────────────────────────── │            │           │
```

## Quorum 交集論證

PBFT 的安全性來自兩個 quorum 的交集。

**Prepared quorum**：大小 2f+1（需要 2f 個 Prepare + 自己的 Pre-Prepare 接受 = 2f+1 個確認）。
**Committed quorum**：大小 2f+1（需要 2f+1 個 Commit）。

兩個大小 2f+1 的 quorum，在 3f+1 個節點中的最小交集：

```
|Q1 ∩ Q2| ≥ |Q1| + |Q2| - N = (2f+1) + (2f+1) - (3f+1) = f+1
```

交集有 f+1 個節點，其中最多 f 個是拜占庭，所以至少有 **1 個誠實節點**同時在兩個 quorum 裡。

這個誠實節點的「見證」作用：
- 它在 prepared 時看到了 (v, n, digest)
- 它在 committed 時也看到了 (v, n, digest)
- 它在 view change 時能向新 primary 提供準確的狀態

一個誠實節點不會同時支持矛盾的值，所以它的見證能防止新 primary 用不同的值「重寫歷史」。

## View Change：primary 是拜占庭時換主

如果 replica 一段時間沒收到來自 primary 的進展（timeout），它懷疑 primary 是拜占庭的，發起 **View Change**。

協定：
1. Replica 廣播 `ViewChange(v+1, log_info, sig)`，其中 `log_info` 包含它自己的 prepared 狀態（哪些 (n, digest) 已 prepared）。
2. 新 primary（`(v+1) mod N`）收到 2f+1 個 ViewChange 訊息後，廣播 `NewView(v+1, V, O)`，其中：
   - V 是它收到的 2f+1 個 ViewChange 訊息集合（可以驗證）
   - O 是新 view 中要重新跑 Pre-Prepare 的序號集（包含舊 view 中已 prepared 但未 committed 的請求）
3. 其他節點驗證 NewView，重建對應 view 的狀態，繼續正常處理。

**View change 的關鍵安全保證**：若某個請求在舊 view 裡已 committed（2f+1 個 Commit），那麼在 2f+1 個 ViewChange 訊息裡，至少有 f+1 個誠實節點報告了該請求的 prepared 狀態。新 primary 必須在 NewView 中包含這個請求，不能刪掉它。

## 實作：dsim 上的簡化 PBFT

以下程式碼在 dsim 模擬器上實作了簡化版 PBFT（省略 view change 和 checkpoint，聚焦在正常路徑 + equivocation 防禦）。

程式碼完整跑過兩個場景：
1. **正常路徑**：誠實 primary，4 個節點全部 decide 同一個值
2. **Equivocation**：拜占庭 primary 對不同 replica 說不同的值，協定擋下，誠實節點無法達到 commit quorum，沒有節點做出錯誤決定

```go
// pbft_demo.go  （與修改過 package main 的 dsim.go 放同目錄）
package main

import (
	"fmt"
	"sort"
)

// N=4 個節點，f=1，容忍 1 個拜占庭節點
const (
	pbftN = 4
	pbftF = 1
)

type PBFTMsgType int

const (
	MsgPrePrepare PBFTMsgType = iota
	MsgPrepare
	MsgCommit
)

type PBFTMsg struct {
	MType  PBFTMsgType
	View   int
	SeqNo  int
	Digest string // 代表 "值" 的識別符（真實實作是 crypto hash）
	From   NodeID
}

// PBFTNode 是誠實 replica 的狀態
type PBFTNode struct {
	id        NodeID
	prepares  map[string][]NodeID // digest -> 送了 Prepare 的節點清單
	commits   map[string][]NodeID // digest -> 送了 Commit 的節點清單
	prepared  map[string]bool     // 已達到 prepared 狀態的 digest
	committed map[string]bool     // 已達到 committed 狀態的 digest
	decided   string
	eventLog  []string
}

func newPBFTNode(id NodeID) *PBFTNode {
	return &PBFTNode{
		id:        id,
		prepares:  make(map[string][]NodeID),
		commits:   make(map[string][]NodeID),
		prepared:  make(map[string]bool),
		committed: make(map[string]bool),
	}
}

func appendIfAbsent(s []NodeID, id NodeID) []NodeID {
	for _, x := range s {
		if x == id {
			return s
		}
	}
	return append(s, id)
}

func (p *PBFTNode) OnMessage(m Message, net *Net) {
	msg, ok := m.Payload.(PBFTMsg)
	if !ok {
		return
	}
	switch msg.MType {
	case MsgPrePrepare:
		// 接受 pre-prepare，廣播 Prepare
		line := fmt.Sprintf("[t=%02d] node%d RECV PrePrepare v=%d seq=%d digest=%q",
			net.Now(), p.id, msg.View, msg.SeqNo, msg.Digest)
		p.eventLog = append(p.eventLog, line)

		for i := NodeID(0); i < pbftN; i++ {
			if i != p.id {
				net.Send(Message{
					From: p.id, To: i,
					Payload: PBFTMsg{
						MType: MsgPrepare, View: msg.View, SeqNo: msg.SeqNo,
						Digest: msg.Digest, From: p.id,
					},
				})
			}
		}

	case MsgPrepare:
		d := msg.Digest
		p.prepares[d] = appendIfAbsent(p.prepares[d], msg.From)
		// Prepared 條件：收到 2f 個 Prepare（本節點接受 PrePrepare 算第 2f+1 個確認）
		if !p.prepared[d] && len(p.prepares[d]) >= 2*pbftF {
			p.prepared[d] = true
			line := fmt.Sprintf("[t=%02d] node%d PREPARED digest=%q  prepare-quorum=%d/%d",
				net.Now(), p.id, d, len(p.prepares[d]), pbftN-1)
			p.eventLog = append(p.eventLog, line)

			// 廣播 Commit
			for i := NodeID(0); i < pbftN; i++ {
				if i != p.id {
					net.Send(Message{
						From: p.id, To: i,
						Payload: PBFTMsg{
							MType: MsgCommit, View: msg.View, SeqNo: msg.SeqNo,
							Digest: d, From: p.id,
						},
					})
				}
			}
		}

	case MsgCommit:
		d := msg.Digest
		p.commits[d] = appendIfAbsent(p.commits[d], msg.From)
		// Committed 條件：收到 2f+1 個 Commit
		if !p.committed[d] && len(p.commits[d]) >= 2*pbftF+1 {
			p.committed[d] = true
			if p.decided == "" {
				p.decided = d
			}
			line := fmt.Sprintf("[t=%02d] node%d COMMITTED digest=%q  commit-quorum=%d/%d  DECIDED=%q",
				net.Now(), p.id, d, len(p.commits[d]), pbftN, p.decided)
			p.eventLog = append(p.eventLog, line)
		}
	}
}

func (p *PBFTNode) OnTick(_ int, _ *Net) {}

// ─── Scenario 1：誠實 Primary ────────────────────────────────────────────────

type HonestPrimary struct {
	id      NodeID
	sent    bool
	replica *PBFTNode // primary 自己也是 replica
}

func (h *HonestPrimary) OnMessage(m Message, net *Net) {
	h.replica.OnMessage(m, net)
}
func (h *HonestPrimary) OnTick(now int, net *Net) {
	if now == 2 && !h.sent {
		h.sent = true
		fmt.Printf("[t=%02d] HONEST primary%d: PrePrepare digest=%q → broadcast to all\n",
			now, h.id, "agreed-value")
		for i := NodeID(1); i < pbftN; i++ {
			net.Send(Message{
				From: h.id, To: i,
				Payload: PBFTMsg{
					MType: MsgPrePrepare, View: 0, SeqNo: 1,
					Digest: "agreed-value", From: h.id,
				},
			})
		}
	}
}

// ─── Scenario 2：拜占庭 Primary（equivocation）────────────────────────────────

type ByzantinePrimary struct {
	id   NodeID
	sent bool
}

func (b *ByzantinePrimary) OnMessage(_ Message, _ *Net) {}
func (b *ByzantinePrimary) OnTick(now int, net *Net) {
	if now == 2 && !b.sent {
		b.sent = true
		fmt.Printf("[t=%02d] BYZANTINE primary%d: EQUIVOCATE => value-A to {1,3}, value-B to {2}\n",
			now, b.id)
		// 對不同 replica 說不同的值 (equivocation)
		net.Send(Message{From: b.id, To: 1, Payload: PBFTMsg{
			MType: MsgPrePrepare, View: 0, SeqNo: 1, Digest: "value-A", From: b.id}})
		net.Send(Message{From: b.id, To: 2, Payload: PBFTMsg{
			MType: MsgPrePrepare, View: 0, SeqNo: 1, Digest: "value-B", From: b.id}})
		net.Send(Message{From: b.id, To: 3, Payload: PBFTMsg{
			MType: MsgPrePrepare, View: 0, SeqNo: 1, Digest: "value-A", From: b.id}})
	}
}

// ─── 輸出與驗證 ───────────────────────────────────────────────────────────────

func printResults(nodes []*PBFTNode) {
	fmt.Println("\n── Event log ──")
	for _, r := range nodes {
		for _, line := range r.eventLog {
			fmt.Println(" ", line)
		}
	}
	fmt.Println("\n── Final state ──")
	decisions := map[string]bool{}
	for _, r := range nodes {
		fmt.Printf("  node%d: decided=%q  prepares=%v  commits=%v\n",
			r.id, r.decided, r.prepares, r.commits)
		if r.decided != "" {
			decisions[r.decided] = true
		}
	}
	vals := []string{}
	for k := range decisions {
		vals = append(vals, k)
	}
	sort.Strings(vals)
	fmt.Println()
	switch len(vals) {
	case 0:
		fmt.Println("  RESULT: no decision (byzantine quorum not met — PROTECTED)")
	case 1:
		fmt.Printf("  RESULT: AGREEMENT on %q\n", vals[0])
	default:
		fmt.Printf("  RESULT: SPLIT-BRAIN on %v  ← BUG!\n", vals)
	}
}

func scenario1() {
	fmt.Println("╔══════════════════════════════════════════════════════════╗")
	fmt.Println("║  Scenario 1: Normal path (honest primary)                ║")
	fmt.Println("╚══════════════════════════════════════════════════════════╝")
	net := NewNet(42)
	r0 := newPBFTNode(0)
	hp := &HonestPrimary{id: 0, replica: r0}
	r1 := newPBFTNode(1)
	r2 := newPBFTNode(2)
	r3 := newPBFTNode(3)
	net.Add(0, hp)
	net.Add(1, r1)
	net.Add(2, r2)
	net.Add(3, r3)
	net.Run(30)
	printResults([]*PBFTNode{r0, r1, r2, r3})
}

func scenario2() {
	fmt.Println("\n╔══════════════════════════════════════════════════════════╗")
	fmt.Println("║  Scenario 2: Byzantine primary equivocates               ║")
	fmt.Println("╚══════════════════════════════════════════════════════════╝")
	net := NewNet(42)
	bp := &ByzantinePrimary{id: 0}
	r1 := newPBFTNode(1)
	r2 := newPBFTNode(2)
	r3 := newPBFTNode(3)
	net.Add(0, bp)
	net.Add(1, r1)
	net.Add(2, r2)
	net.Add(3, r3)
	net.Run(60)
	printResults([]*PBFTNode{r1, r2, r3})
}

func main() {
	scenario1()
	scenario2()
}
```

真跑輸出（WSL, Go 1.18.1）：

```
╔══════════════════════════════════════════════════════════╗
║  Scenario 1: Normal path (honest primary)                ║
╚══════════════════════════════════════════════════════════╝
[t=02] HONEST primary0: PrePrepare digest="agreed-value" → broadcast to all

── Event log ──
  [t=04] node0 PREPARED digest="agreed-value"  prepare-quorum=2/3
  [t=05] node0 COMMITTED digest="agreed-value"  commit-quorum=3/4  DECIDED="agreed-value"
  [t=03] node1 RECV PrePrepare v=0 seq=1 digest="agreed-value"
  [t=04] node1 PREPARED digest="agreed-value"  prepare-quorum=2/3
  [t=05] node1 COMMITTED digest="agreed-value"  commit-quorum=3/4  DECIDED="agreed-value"
  [t=03] node2 RECV PrePrepare v=0 seq=1 digest="agreed-value"
  [t=04] node2 PREPARED digest="agreed-value"  prepare-quorum=2/3
  [t=05] node2 COMMITTED digest="agreed-value"  commit-quorum=3/4  DECIDED="agreed-value"
  [t=03] node3 RECV PrePrepare v=0 seq=1 digest="agreed-value"
  [t=04] node3 PREPARED digest="agreed-value"  prepare-quorum=2/3
  [t=05] node3 COMMITTED digest="agreed-value"  commit-quorum=3/4  DECIDED="agreed-value"

── Final state ──
  node0: decided="agreed-value"  prepares=map[agreed-value:[1 2 3]]  commits=map[agreed-value:[3 1 2]]
  node1: decided="agreed-value"  prepares=map[agreed-value:[2 3]]    commits=map[agreed-value:[0 3 2]]
  node2: decided="agreed-value"  prepares=map[agreed-value:[1 3]]    commits=map[agreed-value:[0 3 1]]
  node3: decided="agreed-value"  prepares=map[agreed-value:[1 2]]    commits=map[agreed-value:[0 1 2]]

  RESULT: AGREEMENT on "agreed-value"

╔══════════════════════════════════════════════════════════╗
║  Scenario 2: Byzantine primary equivocates               ║
╚══════════════════════════════════════════════════════════╝
[t=02] BYZANTINE primary0: EQUIVOCATE => value-A to {1,3}, value-B to {2}

── Event log ──
  [t=03] node1 RECV PrePrepare v=0 seq=1 digest="value-A"
  [t=03] node2 RECV PrePrepare v=0 seq=1 digest="value-B"
  [t=04] node2 PREPARED digest="value-A"  prepare-quorum=2/3
  [t=03] node3 RECV PrePrepare v=0 seq=1 digest="value-A"

── Final state ──
  node1: decided=""  prepares=map[value-A:[3] value-B:[2]]  commits=map[value-A:[2]]
  node2: decided=""  prepares=map[value-A:[1 3]]            commits=map[]
  node3: decided=""  prepares=map[value-A:[1] value-B:[2]]  commits=map[value-A:[2]]

  RESULT: no decision (byzantine quorum not met — PROTECTED)
```

### 場景分析

**Scenario 1**：Primary 對所有人說同樣的 `agreed-value`。每個 replica 在 t=3 收到 PrePrepare，廣播 Prepare。在 t=4，每個節點收到 2 個（2f=2）Prepare，進入 prepared，廣播 Commit。t=5 每個節點收到 3 個（2f+1=3）Commit，決定。4 個節點全部 decided="agreed-value"。

**Scenario 2**：拜占庭 primary 對 1、3 說 value-A，對 2 說 value-B。
- Node 1 收到 PrePrepare(value-A)，廣播 Prepare(value-A)
- Node 2 收到 PrePrepare(value-B)，廣播 Prepare(value-B)
- Node 3 收到 PrePrepare(value-A)，廣播 Prepare(value-A)

Prepare 投票分布：
- value-A 得到 node 1、node 3 的 Prepare（2 票）
- value-B 得到 node 2 的 Prepare（1 票）

Node 1 的 prepares：{value-A: [3], value-B: [2]} — value-A 只有 1 票 Prepare（Prepare quorum 需要 2f=2 票），不夠 prepared
Node 2 的 prepares：{value-A: [1, 3]} — 恰好 2 票，node 2 **prepared value-A**（注意：node 2 自己接受的是 value-B，但收到其他節點的 Prepare value-A 達到門檻）
Node 3 的 prepares：{value-A: [1], value-B: [2]} — value-A 只有 1 票，不夠

結果：value-A 在系統中有 node 2 prepared，但 value-B 沒有任何節點 prepared（只有 1 票）。Value-A 的 commits 只有 node 1 和 node 3 各收到 1 個 commit（來自 node 2），分別是 1 票——遠低於 commit quorum 要求的 2f+1=3 票。沒有任何節點 committed，沒有決定。系統進入需要 view change 的狀態。

**核心觀察**：拜占庭 primary 的 equivocation 被 prepare quorum 的要求攔截。因為 value-A 和 value-B 各自得到的 Prepare 票數加起來不足以讓任何 digest 在「多數誠實節點都認可」的前提下推進到 commit，故沒有錯誤的決定發生。Safety 維持。

## Checkpoint 與 log 清理

實際系統中，log 不能無限增長。PBFT 用 **checkpoint** 清理舊的 log：

- 每執行 K 個請求（通常 K = 100），節點廣播 `Checkpoint(n, state_digest)` 其中 n 是序號、state_digest 是當前應用狀態的 hash。
- 收到 2f+1 個相同 Checkpoint → 這是一個**穩定 checkpoint（stable checkpoint）**。
- 穩定 checkpoint 之前的所有 log 可以安全丟棄。

Checkpoint 同時作為 view change 的錨點：view change 訊息帶著最新 stable checkpoint 的資訊，防止新 primary 需要處理任意古老的 log。

## 底層機制：為什麼三個階段缺一不可

```
威脅矩陣：每個階段防什麼

                     Pre-Prepare     Prepare     Commit
                         │               │           │
拜占庭 primary           │               │           │
  equivocation           │  部分防      │  主要防   │           
  (對不同 replica         │（接受前驗證  │（2f Prepare│
   說不同值）             │ 是否已接受   │quorum曝光  │
                          │ 不同 digest）│ 矛盾）    │
                                                     │
跨 view 不一致            │              │  部分防   │  主要防
(舊 view prepared但        │              │           │（2f+1 Commit
 未 committed，新          │              │           │ quorum 確保
 view 覆蓋)               │              │           │ 跨 view 安全)

結論：去掉 Prepare，拜占庭 primary 可以讓不同 replica prepared 不同值。
      去掉 Commit，view change 時新 primary 可以覆蓋已 prepared 的值。
      二者缺一不可。
```

**Pre-Prepare 不夠**：若只有 Pre-Prepare 和 Commit（跳過 Prepare），拜占庭 primary 對 replica 1 說值 A、replica 2 說值 B，1 直接 commit A、2 直接 commit B，沒有任何「廣播讓大家看到大家看到什麼」的步驟，split-brain 發生。

**兩階段（Pre-Prepare + Prepare，沒有 Commit）**：在同一 view 裡 Prepare quorum 防了 equivocation——但若 primary 崩潰觸發 view change，新 view 的新 primary 不知道舊 view 裡誰已 prepared，可能指派同一序號給不同請求。沒有 Commit 的持久化跨 view，safety 在 view change 時被破壞。

**三個階段都需要**：Pre-Prepare 排序 → Prepare 在 view 內確認一致 → Commit 跨 view 鎖定。

## 對比與取捨

| 特性 | Paxos（crash-fault） | PBFT |
|------|---------------------|------|
| 故障模型 | Crash-stop | Byzantine |
| 節點數下界（容忍 f） | 2f+1 | 3f+1 |
| 通訊複雜度（正常路徑） | O(n)（Multi-Paxos） | O(n²)（每個 replica 廣播給所有人） |
| 通訊複雜度（view change） | O(n²) | O(n²) |
| 需要密碼學簽章 | 不需要 | 是（防偽造） |
| view change 觸發條件 | leader 崩潰（timeout） | primary 崩潰或拜占庭（timeout） |
| 適用節點規模 | 任意（實務 3–7） | 小（n ≤ 20 才實用） |
| 知名用途 | etcd、CockroachDB | Hyperledger Fabric v0.6、學術基準 |

## 踩雷集錦

1. **以為 Prepare quorum 是 f+1 票，不是 2f 票**。混淆了 crash-fault 的多數（f+1 of 2f+1）和 BFT 的多數（2f+1 of 3f+1）。記法：PBFT 的所有 quorum 都是 **2f+1**（包含 prepare phase，加上 PP 接受 = 2f+1 個確認）。

2. **Client 只等 1 個 Reply 就確認**。在 BFT 環境，最多 f 個 replica 可能是拜占庭的，它們可以對 client 說任何 Reply。Client 必須等到 **f+1 個相同的 Reply** 才能確信那是正確結果——因為 f+1 個裡最多 f 個拜占庭，至少 1 個誠實。

3. **誤認為 view change 能修復已被破壞的 safety**。View change 保證的是「已 committed 的值不被覆蓋」，但如果系統中有超過 f 個拜占庭節點，quorum 假設本來就錯了，view change 本身也可能被拜占庭節點破壞。N = 3f+1 是前提，不是結論。

4. **把 `digest` 當成隨便用個字串**。在真實實作中 digest 是密碼學 hash（SHA-256 等），保證：(a) 不同請求有不同 digest（碰撞抵抗），(b) 拜占庭節點無法找到「和合法 digest 相同但內容不同的請求」（preimage resistance）。用字串比較的玩具實作在真實部署下毫無安全性。

5. **以為 PBFT 的 O(n²) 通訊是因為設計不好**。不是。O(n²) 是「每個節點廣播給所有其他節點」這個操作的自然代價，而且這是 BFT 的下界：你必須讓每個 replica 都知道「其他所有 replica 知道什麼」，才能對付 equivocation。在 3f+1 節點下，這至少需要 O(n²) 訊息。HotStuff（Ch 34）用 threshold signature 把 O(n²) 壓到 O(n)，但那是密碼學技巧，不是改掉了這個資訊論下界。

## 進階：再往深一層

### Viewstamped Replication（VR）與 PBFT 的關係

Liskov 和 Cowling 2012 年發表的 Viewstamped Replication Revisited 和 PBFT 有非常相似的結構——事實上 VR 是 Paxos 的同時期發現（1988），而 PBFT 是 VR 在 Byzantine 環境下的延伸。理解兩者的 view change 機制能讓你看出「crash-fault view change」和「BFT view change」的差異在哪裡。

### PBFT 在 Hyperledger Fabric 的失敗案例

Hyperledger Fabric v0.6 用了 PBFT，但後來 v1.0 改成了 Kafka + CFT。原因是 PBFT 在 Fabric 的使用場景下有幾個問題：(1) O(n²) 通訊在節點數超過 20 後吞吐量嚴重下降；(2) view change 的複雜度讓實作 bug 頻出；(3) 使用方實際上並不需要 BFT——聯盟鏈裡的成員都是已知的、受法律約束的組織，用 CFT 就夠了。這個案例說明「不是所有分散式系統都需要 BFT」。

### 主動複製 vs. 被動複製

PBFT 是**主動複製（active replication）**：所有 replica 都執行請求，客戶端收集多份回覆。另一種是**被動複製**：只有 primary 執行，結果廣播給 replica。被動複製在 crash-fault 場景更高效，但在 BFT 場景下被動複製不安全——一個拜占庭 primary 可以對 replica 廣播假的執行結果。PBFT 的主動複製是「讓誠實 replica 能獨立驗證請求結果」的前提。

## 本章重點整理

- **PBFT 三階段**：Pre-Prepare（primary 排序）→ Prepare（同 view 確認一致，防 equivocation）→ Commit（跨 view 鎖定，防 view change 覆蓋）
- **Quorum = 2f+1**：在 3f+1 個節點中，任兩個 2f+1 的 quorum 交集有 f+1 個節點，其中至少 1 個誠實——這是 safety 的信息論基礎
- **Equivocation 被 Prepare 擋下**：拜占庭 primary 對不同 replica 說不同值，Prepare quorum 要求每個 digest 得到 2f 票 Prepare，讓矛盾值無法同時達到 prepared 狀態
- **View change 繼承 committed 值**：2f+1 Commit quorum 確保至少有 f+1 個誠實節點知道已決定的值，view change 時新 primary 無法抹除它
- **O(n²) 通訊**：PBFT 正常路徑每個 replica 廣播給所有人，是 BFT 的固有代價；HotStuff 用門限簽章壓到 O(n)
- **節點規模限制**：PBFT 在 n > 20 後實用性大幅下降，這是推動現代 BFT（Ch 34）的動力

## 自我檢核

- [ ] 我能不看筆記講出 PBFT 三個階段各防的是什麼攻擊
- [ ] 我能從「鴿巢原理」說明為什麼同一 view 內不能有兩個不同 digest 同時 prepared
- [ ] 我能推導 quorum 交集大小（(2f+1)+(2f+1)-(3f+1)=f+1），並解釋那個 f+1 裡為什麼至少有 1 個誠實節點
- [ ] 我知道 view change 的觸發條件和它要保護什麼不變式
- [ ] 我能解釋為什麼 client 需要等 f+1 個相同 Reply 才算成功

## 延伸閱讀

1. **Castro & Liskov (1999)「Practical Byzantine Fault Tolerance」** — *OSDI 1999*
   - **讀哪裡**：§1（Introduction）說清楚了「為什麼之前的 BFT 不實用」；§3.1–§3.3（Normal Case Operation + View Change + Garbage Collection）是演算法核心，本章已把它講完；§4（Optimizations）值得看 Digest 的角色說明
   - **學什麼**：原始論文的 correctness argument（Appendix A）——特別是 Prepare quorum 的 Lemma 3 和 Safety theorem
   - **前提**：讀完本章即可，論文的數學讀起來會非常有共鳴

2. **Arun et al. (2022)「Dissecting BFT Consensus: In Sadness, Anger, and Hope」** — *VLDB*
   - **讀哪裡**：§3（Background）+ §5（PBFT revisited）
   - **學什麼**：對 PBFT 的現代視角——它的設計假設在什麼場景下成立/失效，以及後繼協定怎麼修它
   - **前提**：讀完 Ch 33 + Ch 34

3. **Lamport (2001)「Paxos Made Simple」**（SIGACT News）+ **Oki & Liskov (1988)「Viewstamped Replication」**（PODC）
   - **讀哪裡**：兩篇都不長。先讀 Paxos Made Simple（11 頁）回顧 crash-fault；再讀 VR 的 §3–§4，對比 view change 機制
   - **學什麼**：PBFT 是「VR + 拜占庭」的產物。對比之後 three-phase / quorum 的用途會更清晰

---

三個階段現在你清楚了。PBFT 是對的，但 O(n²) 讓它在大規模場景力不從心。下一章看十年後的答案。

→ [Ch 34 現代 BFT：HotStuff 與 Tendermint](./34-modern-bft-hotstuff.md)
