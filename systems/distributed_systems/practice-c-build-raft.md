# 練習 C — 手刻 Raft

> **目標**：在 Ch 0 的確定性模擬器 `dsim` 上，從零手刻一個能跑的 Raft——leader election、log replication、crash 容錯、network partition 容錯全部到位。跑綠三個測試：選舉、複製、分區。這是整門課最重的動手練習，也是分散式系統的成年禮。

> **環境**：Go 1.18.1, WSL2 / Linux x86-64。純標準庫，不依賴任何第三方套件。所有輸出以 WSL 實測為準。

## 背景與動機

你讀完了 Ch 20–24：Raft 的 leader election、log replication、safety、membership，以及它跟 Paxos/VR/Zab 的比較。你「懂」Raft 了。但這是幻覺。

Diego Ongaro 寫 Raft 論文的動機，就是因為 Paxos「理論上懂了、實作起來完全不會」。他把可理解性（understandability）當第一設計目標——而衡量「你真的懂沒」的唯一標準，是**你能不能把它寫出來、而且在有人搗亂時它還是對的**。

讀協定和刻協定，中間隔著一條鴻溝。讀的時候你的大腦自動補全所有邊界情況：「leader 掛了就重選嘛」「log 不一致就往回退嘛」。刻的時候，這些「嘛」全部變成你必須親手處理、少一個就 split-brain 或丟資料的具體分支。Raft 論文的 Figure 2 是一張你以為看得懂、真寫才知道每一行都在防一個具體災難的規格表。

為什麼跑在 `dsim` 上，而不是真的開五個 process 用 gRPC 串起來？因為 Raft 的 bug 幾乎全部藏在**時序**裡：兩個 candidate 同時逾時、一則 AppendEntries 在分區發生的瞬間還在飛行、舊 leader 帶著過期 term 復活。真網路上這些時序每次都不一樣，你抓到一次 bug 卻重現不了，就修不掉。`dsim` 是確定性的：**同一個 seed 跑出逐位元組相同的世界線**。你的 Raft 若在 seed=3 選出兩個 leader，你可以用 seed=3 無限次重現那個災難，一路 print 到看見它為什麼發生。

> 若對模擬器的 API（`Node` 介面、`OnTick`、`Send`、`Partition`/`Crash`）不熟，回看 [Ch 0](./00-environment-setup.md)。這個練習的每一行都建在那上面。

## 任務規格

實作一個 `Raft` 型別，它實作 `dsim.Node` 介面（`OnMessage` + `OnTick`），支援 **3 或 5 個節點**的叢集。節點之間**只能靠 `net.Send` 溝通**，不共享記憶體、不在 `OnTick` 裡改別人狀態（這是 Ch 0 的鐵律）。

### RPC（兩種，照 Raft 論文 Figure 2）

**RequestVote**（candidate 拉票）：

| 欄位 | 意義 |
|---|---|
| `Term` | candidate 的 term |
| `CandidateID` | 拉票者 |
| `LastLogIndex` / `LastLogTerm` | candidate 最後一格 log 的 index 與 term（給選舉限制用） |

回覆 `RequestVoteReply{ Term, VoteGranted }`。

**AppendEntries**（leader 複製 log ＋ 當心跳）：

| 欄位 | 意義 |
|---|---|
| `Term` | leader 的 term |
| `LeaderID` | 誰是 leader |
| `PrevLogIndex` / `PrevLogTerm` | 新 entry 之前那一格的 index/term（一致性檢查用） |
| `Entries` | 要附加的 log entry（心跳時為空） |
| `LeaderCommit` | leader 的 commitIndex |

回覆 `AppendEntriesReply{ Term, Success, MatchIndex, ConflictIndex }`。空的 `Entries` 就是心跳。

### 正確性要件（驗收條件）

1. **選舉安全（Election Safety）**：任一時刻、同一個 term 內**至多一個 leader**。測試在多個節點同時競選、殺 leader 重選後，仍要成立。
2. **已提交不丟失 + 最終一致**：一旦某 entry 被 commit（多數派持有），它**永遠不會**從任何節點的 log 消失，且所有節點最終 apply 出**完全相同**的命令序列（同順序、同內容）。
3. **crash 後可恢復服務**：leader 當機後，剩下的多數派要能重選出新 leader 並繼續接受、commit 新命令。新 term 必須嚴格大於舊 term。
4. **分區容錯**：切成「少數派 + 多數派」後，**少數派側不能 commit 任何新 entry**（沒有多數就沒有 commit），**多數派側能繼續 commit**；`Heal()` 之後少數派要追上、全體收斂，且分區前已 commit 的 entry 一個都不能少。

### 你要提供的公開方法

- `NewRaft(id NodeID, peers []NodeID) *Raft`：`peers` 包含自己。
- `Propose(cmd interface{}, net *Net) bool`：客戶端在 leader 上提交命令；非 leader 回 `false`。
- 觀測用欄位：`role`、`currentTerm`、`commitIndex`、`applied`（已 apply 的命令序列）。

## 期望輸出範例

跑通後，三個測試印出的東西大致長這樣（實際 leader 是誰依 seed 而定）：

```
[TestElection] leader=0 term=1
[TestElection] after crash of 0: new leader=1 term=2  (delivered=236 dropped=25)
[TestElection] PASS
---
[TestReplication] leader=0, all 5 nodes applied identical sequence: [set x=1 set y=2 set z=3 del x set y=9]
[TestReplication] PASS
---
[TestPartition] partitioned: minority=[0 1] (leader 0 stuck at commit=2), majority=[2 3 4] (leader 2 advanced to commit=3)
[TestPartition] after heal: leader=2 commitIndex=3, converged log[1..3]=a b c-in-majority
[TestPartition] applied on node0=[a b c-in-majority]
[TestPartition] PASS
```

三件事一眼看穿：選舉選出唯一 leader、殺掉後重選且 term 遞增；五個節點 apply 出一模一樣的序列；分區時少數派 leader **卡在 commit=2 動不了**，多數派推進到 commit=3，heal 後少數派追上、a/b 這兩筆分區前 committed 的一個沒掉。

## 如果你卡住了

五個最會咬人的地方，以及方向（先自己撞牆再看）：

1. **randomized election timeout 怎麼設**：如果所有節點的選舉逾時都一樣長，它們會**同時**逾時、同時競選、瓜分選票（split vote）、沒人拿到多數，然後同時再逾時……無限迴圈，term 一路飆高卻選不出 leader。解法是讓逾時**錯開**。在 `dsim` 裡要保持確定性，別用 wall-clock 隨機——用每個節點自己 seed 的 PRNG，或直接用 node id 錯開 base（`15 + id*4 + 小隨機`）。關鍵：逾時要**遠大於一次廣播往返**（心跳週期 + 網路延遲），否則 leader 的心跳還沒到、follower 就先逾時造反了。

2. **nextIndex 回溯**：leader 不知道每個 follower 的 log 落後多少。它樂觀地從自己的 `lastIndex+1` 開始送，follower 若發現 `PrevLog` 對不上就回 `Success=false`，leader 就把該 follower 的 `nextIndex` 往回退再試。一次退一格會很慢（log 差 1000 格就要 1000 個往返）；讓 follower 在回覆裡帶一個 `ConflictIndex`（衝突 term 的第一格），leader 一次跳過整個 term，快很多。

3. **為什麼只 commit「當前 term」的 entry**：這是 Raft 論文 Figure 8 的著名陷阱。leader 不能因為「某個舊 term 的 entry 已被多數派複製」就 commit 它——因為那個 entry 可能之後被另一個 leader 覆蓋掉，造成「已 commit 卻消失」。安全規則：leader 只有在**當前 term 的某個 entry 達到多數**時，才順帶把它前面的 entry 一起 commit。你的 `advanceCommit` 裡那個 `if log[n].Term != currentTerm { continue }` 就是這條命。拿掉它，TestPartition 遲早會丟資料。

4. **term 落後的 leader 要退位**：舊 leader 被分進少數派、還一直發心跳。等分區 heal，它收到來自新 leader（更高 term）的訊息——這一刻它必須**立刻變回 follower**、採用新 term。任何 RPC 或 RPC 回覆只要看到 `msg.Term > currentTerm`，就 `stepDown`。這是 Raft「term 是邏輯時鐘」的核心：更高 term 永遠壓倒一切。漏了這條，你會有兩個 leader 打架。

5. **怎麼在 `OnTick(now)` 裡做 timeout**：`dsim` 沒有真時鐘，`now` 是邏輯計數器。你在成為 follower/candidate、或收到合法心跳/投票時，記一個 `electionDeadline = now + timeout`；每次 `OnTick(now)` 檢查 `now >= electionDeadline` 就開選舉。leader 則在 `OnTick` 裡按 `heartbeatEvery` 週期廣播心跳。**別想在 `OnTick` 裡 sleep 或等**——它是被動觸發的 callback，你只能比對 `now` 與你自己記下的 deadline。

## 實作步驟建議

分六步，每步都能獨立驗證再往下：

### Step 1：狀態與型別

定義三種角色（follower / candidate / leader）、`LogEntry{Term, Cmd}`、四種訊息（RequestVote / RequestVoteReply / AppendEntries / AppendEntriesReply）。`Raft` 結構照 Figure 2 的「State」欄：persistent（`currentTerm`、`votedFor`、`log`）＋ volatile（`commitIndex`、`lastApplied`）＋ leader-only（`nextIndex`、`matchIndex`）。

**關鍵決策**：`log` 放一個 index 0 的**哨兵 entry**（term 0），真實 entry 從 index 1 起。這樣 `PrevLogIndex=0` 的一致性檢查天然成立（哨兵的 term 永遠對得上），省掉一堆邊界 if。

### Step 2：leader election

`OnTick` 裡逾時就 `startElection`：term++、投自己、廣播 RequestVote。收到 RequestVote 就決定投不投（同 term、還沒投過、對方 log 夠新）。收到夠多 grant（達多數）就 `becomeLeader`。先不做 log，log 全空也要能選出唯一 leader。**先跑 TestElection 的前半（選出 leader）再往下。**

### Step 3：心跳與 leader 維持

`becomeLeader` 後立刻廣播一輪空 AppendEntries 宣示主權，之後 `OnTick` 裡週期性發心跳。follower 收到合法心跳（term 夠大）就重置選舉計時器——這是「leader 還活著、別造反」的信號。驗證：選出 leader 後叢集**穩定**，term 不會亂飆。

### Step 4：log append ＋ AppendEntries 一致性檢查

`Propose` 把命令 append 進 leader 的 log。leader 的 AppendEntries 帶上 `PrevLogIndex/PrevLogTerm` 和實際 entries。follower 做**一致性檢查**：`PrevLog` 對不上就拒絕並回 `ConflictIndex`；對得上就覆蓋衝突、附加新 entry。leader 依回覆推進或回退 `nextIndex`。這是最容易寫錯的一步，慢慢來。

### Step 5：commit ＋ apply

leader 用 `matchIndex` 算「多數派已持有到哪一格」，據此推進 `commitIndex`——**但只 commit 當前 term 的 entry**（見卡關提示 3）。follower 從 AppendEntries 的 `LeaderCommit` 學到 commitIndex。兩邊都把 `commitIndex` 之前、`lastApplied` 之後的 entry `apply` 進狀態機（這裡就是塞進 `applied` 切片）。**跑 TestReplication：五個節點 apply 序列要一模一樣。**

### Step 6：加 crash / partition 測試

寫 TestElection（殺 leader 重選）、TestReplication、TestPartition（少數派卡住、多數派前進、heal 收斂）。用 `net.Crash` / `net.Partition` / `net.Heal` 注入失敗。**這一步才是真正檢驗你 Raft 對不對的地方**——前五步在無失敗下跑對很容易，扛得住 crash 和 partition 才算數。

## 完整參考解答

**自己先撞到 Step 6 綠燈再打開。** 偷看你會學不到「為什麼要有這一行」——而 Raft 的每一行幾乎都在防一個具體災難，那個「為什麼」才是這練習的全部價值。

<details>
<summary>點開參考實作（raft.go，已在 WSL Go 1.18.1 真跑通過三個測試）</summary>

把 `dsim/dsim.go` 的 `package dsim` 改成 `package main` 複製到同目錄，跟下面的 `raft.go`、`main.go` 放一起 `go run .` 即可（做法見 Ch 0）。

```go
// raft.go
package main

// ---- 訊息型別 ----

type LogEntry struct {
	Term int
	Cmd  interface{}
}

type RequestVote struct {
	Term         int
	CandidateID  NodeID
	LastLogIndex int
	LastLogTerm  int
}

type RequestVoteReply struct {
	Term        int
	VoteGranted bool
}

type AppendEntries struct {
	Term         int
	LeaderID     NodeID
	PrevLogIndex int
	PrevLogTerm  int
	Entries      []LogEntry
	LeaderCommit int
}

type AppendEntriesReply struct {
	Term          int
	Success       bool
	MatchIndex    int // 成功時：對方 log 最後 index，推進 matchIndex 用
	ConflictIndex int // 失敗時：建議 nextIndex 回溯到哪
}

type role int

const (
	follower role = iota
	candidate
	leader
)

type Raft struct {
	id    NodeID
	peers []NodeID
	role  role

	// persistent（Figure 2 左上）
	currentTerm int
	votedFor    NodeID     // -1 = 尚未投票
	log         []LogEntry // log[0] 是哨兵（term 0），真實 entry 從 index 1

	// volatile
	commitIndex int
	lastApplied int

	// leader-only（每次當選重設）
	nextIndex  map[NodeID]int
	matchIndex map[NodeID]int

	votesGot map[NodeID]bool

	// 選舉/心跳計時（邏輯時間）
	electionDeadline int
	electionTimeout  int
	heartbeatEvery   int
	lastHeartbeat    int

	applied []interface{} // 觀測：已 apply 的命令序列

	rng *randSource
}

// 每個節點自己的確定性 PRNG（以 id 錯開），不碰 net 的私有 rng。
type randSource struct{ state uint64 }

func newRand(seed uint64) *randSource {
	return &randSource{state: seed*2862933555777941757 + 3037000493}
}
func (r *randSource) next() uint64 {
	r.state = r.state*6364136223846793005 + 1442695040888963407
	return r.state
}
func (r *randSource) intn(n int) int { return int(r.next()>>33) % n }

const noVote NodeID = -1

func NewRaft(id NodeID, peers []NodeID) *Raft {
	return &Raft{
		id:             id,
		peers:          peers,
		role:           follower,
		currentTerm:    0,
		votedFor:       noVote,
		log:            []LogEntry{{Term: 0}}, // 哨兵
		nextIndex:      map[NodeID]int{},
		matchIndex:     map[NodeID]int{},
		votesGot:       map[NodeID]bool{},
		heartbeatEvery: 3,
		rng:            newRand(uint64(id) + 1),
	}
}

func (r *Raft) lastIndex() int { return len(r.log) - 1 }
func (r *Raft) lastTerm() int  { return r.log[len(r.log)-1].Term }

// peers 包含自己，所以多數 = ⌊N/2⌋ + 1。
func (r *Raft) majority() int { return len(r.peers)/2 + 1 }

func (r *Raft) resetElectionTimer(now int) {
	// timeout 要遠大於一次廣播往返（心跳每 3 tick、延遲 1-2）。
	// 用 node id 錯開 base（id*4）＋一點隨機打散，兩者都確定：
	// 保證任一分區裡 id 最小的節點通常最先逾時，破 split vote 的僵局，
	// 又不完全同步（隨機讓退讓的節點不會週期性同時醒來）。
	r.electionTimeout = 15 + int(r.id)*4 + r.rng.intn(6)
	r.electionDeadline = now + r.electionTimeout
}

// ---- OnTick：timeout 與心跳都在這裡（沒有真時鐘，只比對 now）----

func (r *Raft) OnTick(now int, net *Net) {
	if r.electionDeadline == 0 {
		r.resetElectionTimer(now)
	}
	switch r.role {
	case leader:
		if now-r.lastHeartbeat >= r.heartbeatEvery {
			r.lastHeartbeat = now
			r.broadcastAppend(net)
		}
	default: // follower / candidate
		if now >= r.electionDeadline {
			r.startElection(now, net)
		}
	}
	r.apply()
}

func (r *Raft) startElection(now int, net *Net) {
	r.role = candidate
	r.currentTerm++
	r.votedFor = r.id
	r.votesGot = map[NodeID]bool{r.id: true}
	r.resetElectionTimer(now)
	for _, p := range r.peers {
		if p == r.id {
			continue
		}
		net.Send(Message{From: r.id, To: p, Payload: RequestVote{
			Term:         r.currentTerm,
			CandidateID:  r.id,
			LastLogIndex: r.lastIndex(),
			LastLogTerm:  r.lastTerm(),
		}})
	}
}

func (r *Raft) becomeLeader(now int, net *Net) {
	r.role = leader
	r.nextIndex = map[NodeID]int{}
	r.matchIndex = map[NodeID]int{}
	for _, p := range r.peers {
		r.nextIndex[p] = r.lastIndex() + 1
		r.matchIndex[p] = 0
	}
	r.lastHeartbeat = now
	r.broadcastAppend(net) // 立刻宣示主權
}

// stepDown：看到更高 term 一律退位成 follower，採用新 term。
func (r *Raft) stepDown(term int, now int) {
	r.currentTerm = term
	r.role = follower
	r.votedFor = noVote
	r.resetElectionTimer(now)
}

// ---- OnMessage ----

func (r *Raft) OnMessage(m Message, net *Net) {
	now := net.Now()
	switch msg := m.Payload.(type) {
	case RequestVote:
		r.onRequestVote(m.From, msg, now, net)
	case RequestVoteReply:
		r.onRequestVoteReply(m.From, msg, now, net)
	case AppendEntries:
		r.onAppendEntries(m.From, msg, now, net)
	case AppendEntriesReply:
		r.onAppendEntriesReply(m.From, msg, now, net)
	}
	r.apply()
}

func (r *Raft) onRequestVote(from NodeID, msg RequestVote, now int, net *Net) {
	if msg.Term > r.currentTerm {
		r.stepDown(msg.Term, now)
	}
	grant := false
	if msg.Term == r.currentTerm &&
		(r.votedFor == noVote || r.votedFor == msg.CandidateID) &&
		r.candidateUpToDate(msg.LastLogTerm, msg.LastLogIndex) {
		grant = true
		r.votedFor = msg.CandidateID
		r.resetElectionTimer(now) // 投了票就重置，別搶著自己選
	}
	net.Send(Message{From: r.id, To: from, Payload: RequestVoteReply{
		Term: r.currentTerm, VoteGranted: grant,
	}})
}

// 選舉限制（Figure 2）：candidate 的 log 至少要跟我一樣新才給票。
// 「新」的定義：先比最後一格的 term，term 相同才比 index。
func (r *Raft) candidateUpToDate(lastTerm, lastIndex int) bool {
	if lastTerm != r.lastTerm() {
		return lastTerm > r.lastTerm()
	}
	return lastIndex >= r.lastIndex()
}

func (r *Raft) onRequestVoteReply(from NodeID, msg RequestVoteReply, now int, net *Net) {
	if msg.Term > r.currentTerm {
		r.stepDown(msg.Term, now)
		return
	}
	if r.role != candidate || msg.Term != r.currentTerm {
		return // 過期回覆，丟掉
	}
	if msg.VoteGranted {
		r.votesGot[from] = true
		if len(r.votesGot) >= r.majority() {
			r.becomeLeader(now, net)
		}
	}
}

func (r *Raft) onAppendEntries(from NodeID, msg AppendEntries, now int, net *Net) {
	reply := AppendEntriesReply{Term: r.currentTerm, Success: false}
	if msg.Term < r.currentTerm {
		net.Send(Message{From: r.id, To: from, Payload: reply}) // 拒絕過期 leader
		return
	}
	if msg.Term > r.currentTerm {
		r.stepDown(msg.Term, now)
	}
	r.role = follower // 承認這個 leader
	r.resetElectionTimer(now)
	reply.Term = r.currentTerm

	// 一致性檢查：PrevLogIndex 那格的 term 要對上
	if msg.PrevLogIndex > r.lastIndex() {
		reply.ConflictIndex = r.lastIndex() + 1 // log 太短，告訴 leader 從哪補
		net.Send(Message{From: r.id, To: from, Payload: reply})
		return
	}
	if r.log[msg.PrevLogIndex].Term != msg.PrevLogTerm {
		// term 對不上：回退到該衝突 term 的第一格，加速 nextIndex 回退
		bad := r.log[msg.PrevLogIndex].Term
		i := msg.PrevLogIndex
		for i > 1 && r.log[i-1].Term == bad {
			i--
		}
		reply.ConflictIndex = i
		net.Send(Message{From: r.id, To: from, Payload: reply})
		return
	}

	// 逐格比對：衝突就截斷覆蓋，缺的就附加
	for j, e := range msg.Entries {
		idx := msg.PrevLogIndex + 1 + j
		if idx <= r.lastIndex() {
			if r.log[idx].Term != e.Term {
				r.log = r.log[:idx] // 截掉衝突點之後
				r.log = append(r.log, e)
			}
			// term 相同 = 已有相同 entry，跳過（別無腦覆蓋，會截掉後面已附加的）
		} else {
			r.log = append(r.log, e)
		}
	}

	if msg.LeaderCommit > r.commitIndex {
		r.commitIndex = min(msg.LeaderCommit, r.lastIndex())
	}
	reply.Success = true
	reply.MatchIndex = msg.PrevLogIndex + len(msg.Entries)
	net.Send(Message{From: r.id, To: from, Payload: reply})
}

func (r *Raft) onAppendEntriesReply(from NodeID, msg AppendEntriesReply, now int, net *Net) {
	if msg.Term > r.currentTerm {
		r.stepDown(msg.Term, now)
		return
	}
	if r.role != leader || msg.Term != r.currentTerm {
		return
	}
	if msg.Success {
		if msg.MatchIndex+1 > r.nextIndex[from] {
			r.nextIndex[from] = msg.MatchIndex + 1
		}
		if msg.MatchIndex > r.matchIndex[from] {
			r.matchIndex[from] = msg.MatchIndex
		}
		r.advanceCommit()
	} else {
		// 一致性檢查失敗：回退 nextIndex 再試
		if msg.ConflictIndex > 0 {
			r.nextIndex[from] = msg.ConflictIndex
		} else if r.nextIndex[from] > 1 {
			r.nextIndex[from]--
		}
		r.sendAppendTo(from, net)
	}
}

// leader 推進 commitIndex：找最大的 n，使多數派 matchIndex >= n，
// 且 log[n].Term == currentTerm（Figure 8 安全限制，見說明）。
func (r *Raft) advanceCommit() {
	for n := r.lastIndex(); n > r.commitIndex; n-- {
		if r.log[n].Term != r.currentTerm {
			continue // 只 commit 當前 term 的 entry
		}
		count := 1 // 算自己
		for _, p := range r.peers {
			if p != r.id && r.matchIndex[p] >= n {
				count++
			}
		}
		if count >= r.majority() {
			r.commitIndex = n
			break
		}
	}
}

func (r *Raft) broadcastAppend(net *Net) {
	for _, p := range r.peers {
		if p == r.id {
			continue
		}
		r.sendAppendTo(p, net)
	}
}

func (r *Raft) sendAppendTo(p NodeID, net *Net) {
	ni := r.nextIndex[p]
	if ni < 1 {
		ni = 1
	}
	prevIndex := ni - 1
	prevTerm := r.log[prevIndex].Term
	entries := append([]LogEntry(nil), r.log[ni:]...) // 複製，別共享底層陣列
	net.Send(Message{From: r.id, To: p, Payload: AppendEntries{
		Term:         r.currentTerm,
		LeaderID:     r.id,
		PrevLogIndex: prevIndex,
		PrevLogTerm:  prevTerm,
		Entries:      entries,
		LeaderCommit: r.commitIndex,
	}})
}

// 客戶端在 leader 上提交命令。非 leader 回 false。
func (r *Raft) Propose(cmd interface{}, net *Net) bool {
	if r.role != leader {
		return false
	}
	r.log = append(r.log, LogEntry{Term: r.currentTerm, Cmd: cmd})
	r.matchIndex[r.id] = r.lastIndex()
	r.broadcastAppend(net)
	return true
}

// 把 commitIndex 之前、lastApplied 之後的 entry 送進狀態機。
func (r *Raft) apply() {
	for r.lastApplied < r.commitIndex {
		r.lastApplied++
		r.applied = append(r.applied, r.log[r.lastApplied].Cmd)
	}
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
```

測試檔：

```go
// main.go
package main

import (
	"fmt"
	"os"
)

func buildCluster(net *Net, n int) ([]*Raft, []NodeID) {
	ids := make([]NodeID, n)
	for i := 0; i < n; i++ {
		ids[i] = NodeID(i)
	}
	rafts := make([]*Raft, n)
	for i := 0; i < n; i++ {
		r := NewRaft(ids[i], ids)
		rafts[i] = r
		net.Add(ids[i], r)
	}
	return rafts, ids
}

// 找 term 最大、活著的 leader；-1 表示沒有。
func findLeader(rafts []*Raft, crashed map[NodeID]bool) NodeID {
	best, bestTerm := -1, -1
	for _, r := range rafts {
		if crashed[r.id] {
			continue
		}
		if r.role == leader && r.currentTerm > bestTerm {
			bestTerm, best = r.currentTerm, int(r.id)
		}
	}
	return NodeID(best)
}

func countLeadersInTerm(rafts []*Raft, term int) int {
	c := 0
	for _, r := range rafts {
		if r.role == leader && r.currentTerm == term {
			c++
		}
	}
	return c
}

func fail(msg string) { fmt.Println("FAIL:", msg); os.Exit(1) }

// ---- Test 1：選舉（唯一 leader、殺 leader 重選）----
func TestElection() {
	net := NewNet(3)
	net.SetLatency(1, 2)
	rafts, _ := buildCluster(net, 5)
	net.Run(60)

	crashed := map[NodeID]bool{}
	ld := findLeader(rafts, crashed)
	if ld < 0 {
		fail("TestElection: no leader elected")
	}
	term := rafts[ld].currentTerm
	if countLeadersInTerm(rafts, term) != 1 {
		fail("TestElection: more than one leader in same term")
	}
	fmt.Printf("[TestElection] leader=%d term=%d\n", ld, term)

	net.Crash(ld) // 殺掉 leader
	crashed[ld] = true
	net.Run(net.Now() + 80)
	ld2 := findLeader(rafts, crashed)
	if ld2 < 0 {
		fail("TestElection: no re-election after leader crash")
	}
	if ld2 == ld {
		fail("TestElection: crashed node still reported leader")
	}
	term2 := rafts[ld2].currentTerm
	if countLeadersInTerm(rafts, term2) != 1 {
		fail("TestElection: split-brain after re-election")
	}
	if term2 <= term {
		fail("TestElection: new term not greater")
	}
	fmt.Printf("[TestElection] after crash of %d: new leader=%d term=%d  (delivered=%d dropped=%d)\n",
		ld, ld2, term2, net.Delivered, net.Dropped)
	fmt.Println("[TestElection] PASS")
}

// ---- Test 2：複製（命令複製到多數、所有節點 apply 相同序列）----
func TestReplication() {
	net := NewNet(3)
	net.SetLatency(1, 2)
	rafts, _ := buildCluster(net, 5)
	net.Run(40)
	ld := findLeader(rafts, map[NodeID]bool{})
	if ld < 0 {
		fail("TestReplication: no leader")
	}

	cmds := []string{"set x=1", "set y=2", "set z=3", "del x", "set y=9"}
	for _, c := range cmds {
		if !rafts[ld].Propose(c, net) {
			fail("TestReplication: propose rejected by leader")
		}
		net.Run(net.Now() + 20)
	}
	net.Run(net.Now() + 40) // 讓 commit/apply 傳播

	for _, r := range rafts {
		if len(r.applied) != len(cmds) {
			fail(fmt.Sprintf("TestReplication: node %d applied %d, want %d", r.id, len(r.applied), len(cmds)))
		}
		for i := range cmds {
			if r.applied[i] != cmds[i] {
				fail(fmt.Sprintf("TestReplication: node %d applied[%d]=%v want %v", r.id, i, r.applied[i], cmds[i]))
			}
		}
	}
	fmt.Printf("[TestReplication] leader=%d, all 5 nodes applied identical sequence: %v\n", ld, rafts[0].applied)
	fmt.Println("[TestReplication] PASS")
}

// ---- Test 3：分區（少數側停滯、多數側前進、heal 後收斂、無 committed 遺失）----
func TestPartition() {
	net := NewNet(3)
	net.SetLatency(1, 2)
	rafts, ids := buildCluster(net, 5)
	net.Run(40)
	ld := findLeader(rafts, map[NodeID]bool{})
	if ld < 0 {
		fail("TestPartition: no leader")
	}

	for _, c := range []string{"a", "b"} { // 分區前先 commit 兩筆
		rafts[ld].Propose(c, net)
		net.Run(net.Now() + 20)
	}
	net.Run(net.Now() + 30)
	committedBefore := rafts[ld].commitIndex

	// leader 關進少數派（它＋一個 follower），多數派 3 個
	minoritySet := []NodeID{ld}
	var majoritySet []NodeID
	for _, id := range ids {
		if id == ld {
			continue
		}
		if len(minoritySet) < 2 {
			minoritySet = append(minoritySet, id)
		} else {
			majoritySet = append(majoritySet, id)
		}
	}
	net.Partition(minoritySet, majoritySet)

	// 少數派 leader 試著 propose：不該 commit
	rafts[ld].Propose("stale-in-minority", net)
	net.Run(net.Now() + 80)
	if rafts[ld].commitIndex > committedBefore {
		fail("TestPartition: minority side committed new entry (split-brain write)")
	}

	// 多數派應選出自己的 leader（term 最大者）並能 commit
	majLeader, majTerm := NodeID(-1), -1
	for _, id := range majoritySet {
		if rafts[id].role == leader && rafts[id].currentTerm > majTerm {
			majTerm, majLeader = rafts[id].currentTerm, id
		}
	}
	if majLeader < 0 {
		fail("TestPartition: majority side elected no leader")
	}
	rafts[majLeader].Propose("c-in-majority", net)
	net.Run(net.Now() + 60)
	majCommit := rafts[majLeader].commitIndex
	if majCommit <= committedBefore {
		fail("TestPartition: majority side made no progress")
	}
	fmt.Printf("[TestPartition] partitioned: minority=%v (leader %d stuck at commit=%d), majority=%v (leader %d advanced to commit=%d)\n",
		minoritySet, ld, rafts[ld].commitIndex, majoritySet, majLeader, majCommit)

	// heal：少數派追上、全體收斂
	net.Heal()
	net.Run(net.Now() + 120)

	final := findLeader(rafts, map[NodeID]bool{})
	if final < 0 {
		fail("TestPartition: no leader after heal")
	}
	ci := rafts[final].commitIndex
	for i := 1; i <= ci; i++ { // 所有節點 log 前 ci 格一致
		want := rafts[final].log[i]
		for _, r := range rafts {
			if r.lastIndex() < i || r.log[i].Term != want.Term || r.log[i].Cmd != want.Cmd {
				fail(fmt.Sprintf("TestPartition: node %d diverges at index %d after heal", r.id, i))
			}
		}
	}
	if rafts[final].log[1].Cmd != "a" || rafts[final].log[2].Cmd != "b" {
		fail("TestPartition: committed entries a/b lost after heal") // 不能遺失已 commit 的
	}
	fmt.Printf("[TestPartition] after heal: leader=%d commitIndex=%d, converged log[1..%d]=", final, ci, ci)
	for i := 1; i <= ci; i++ {
		fmt.Printf("%v ", rafts[final].log[i].Cmd)
	}
	fmt.Println()
	fmt.Printf("[TestPartition] applied on node0=%v\n", rafts[0].applied)
	fmt.Println("[TestPartition] PASS")
}

func main() {
	TestElection()
	fmt.Println("---")
	TestReplication()
	fmt.Println("---")
	TestPartition()
	fmt.Println("===")
	fmt.Println("all tests passed")
}
```

真跑（WSL, Go 1.18.1，`go run .`）：

```
[TestElection] leader=0 term=1
[TestElection] after crash of 0: new leader=1 term=2  (delivered=236 dropped=25)
[TestElection] PASS
---
[TestReplication] leader=0, all 5 nodes applied identical sequence: [set x=1 set y=2 set z=3 del x set y=9]
[TestReplication] PASS
---
[TestPartition] partitioned: minority=[0 1] (leader 0 stuck at commit=2), majority=[2 3 4] (leader 2 advanced to commit=3)
[TestPartition] after heal: leader=2 commitIndex=3, converged log[1..3]=a b c-in-majority
[TestPartition] applied on node0=[a b c-in-majority]
[TestPartition] PASS
===
all tests passed
```

三個全綠。因為是確定性模擬，你在你機器上用同樣的 seed（TestElection/Replication/Partition 都用 seed=3、5 節點、latency 1-2）跑，會拿到**一模一樣**的 leader、term、delivered/dropped 數字。

### 解答說明（每個關鍵決策為何這樣）

- **哨兵 entry（log[0]）**：讓 `PrevLogIndex=0` 的一致性檢查天然成立，第一筆 entry 也不用特判。少掉一堆 `if len(log)==0` 的邊界。

- **majority = ⌊N/2⌋+1，peers 含自己**：5 節點多數是 3。這行我一開始寫成 `(len(peers)+1)/2+1` 算成 4——結果多數派三個節點互投、每個都拿到 3 票卻永遠達不到「4」，term 無限飆高卻選不出 leader。**這是我實際踩到、靠 seed 重現 print 出來抓到的 bug**，也是確定性模擬的價值鐵證：非確定環境下這種偶發 split 幾乎不可能穩定重現。

- **選舉逾時 = 15 + id*4 + rng(6)**：三個常數都有來源。`15` 是 base，要遠大於一次心跳往返（心跳週期 3 + 延遲 up to 2 的往返約 3-7 tick），否則 leader 心跳還沒到 follower 就造反。`id*4` 用 node id 結構性錯開，保證任一分區裡 id 最小者通常最先逾時、破 split vote 僵局——這在真 Raft 是純隨機，但在確定性模擬裡用 id 錯開更穩且仍確定。`rng(6)` 加一點抖動，避免退讓的節點週期性同時醒來。全部走各節點自己 seed 的 PRNG，不碰 wall-clock，維持確定性。

- **投票就重置選舉計時器**：`onRequestVote` 給票後 `resetElectionTimer`。若不重置，follower 給了票卻馬上自己也逾時競選，會製造無意義的 term 競爭。

- **收到合法 AppendEntries 一律 `role = follower` + 重置計時器**：這是「leader 還活著」的信號。candidate 收到當前 term 的 leader 心跳要立刻退讓，不然分區 heal 後兩個 leader 會打架。

- **只 commit 當前 term（`advanceCommit` 裡的 `continue`）**：Figure 8 的命。leader 不能因為某個舊 term 的 entry 湊到多數就 commit 它——那個 entry 可能還沒真正安全，之後被覆蓋就會「已 commit 卻消失」。只有當前 term 的 entry 湊到多數，才連帶把它前面的一起 commit。這行是 Raft safety 的靈魂。

- **`ConflictIndex` 快速回退**：follower 一致性檢查失敗時，不是讓 leader 一格一格退，而是回報「衝突 term 的第一格」，leader 一次跳過整個 term。log 差很多時省掉大量往返。

- **AppendEntries 逐格比對而非無腦覆蓋**：`if r.log[idx].Term != e.Term` 才截斷。若無條件 `log = log[:idx]` 再 append，會把 follower 已經正確附加、但這次 RPC 沒帶到的後續 entry 給截掉——一則延遲/重排的舊 AppendEntries 就能把 log 弄短。這是很隱蔽的 bug。

- **`entries := append([]LogEntry(nil), r.log[ni:]...)` 複製一份**：不共享 leader log 的底層陣列。雖然 `dsim` 單執行緒不會有 data race，但 follower 若之後 append 到自己 log、又剛好和 leader 共享底層陣列，會互相污染。複製最安全。

</details>

## 測試用例表

| 測試 | seed / 設定 | 注入的失敗 | 驗收條件 | 對應要件 |
|---|---|---|---|---|
| TestElection | seed=3, 5 節點, lat 1-2 | `Crash(leader)` | 選出唯一 leader；殺掉後重選、term 嚴格遞增、無 split-brain | (a) (c) |
| TestReplication | seed=3, 5 節點, lat 1-2 | 無 | 5 筆命令 commit；**五個節點 apply 序列完全相同** | (b) |
| TestPartition | seed=3, 5 節點, lat 1-2 | `Partition(少2, 多3)` → `Heal` | 少數派 commit 不動；多數派前進；heal 後收斂、a/b 不遺失 | (b) (d) |

延伸你可以自己加的：`SetDropRate(0.1)` 看丟包下還能不能收斂（Raft 靠重送應該可以，只是慢）；跑一圈 seed 0..99 全部要綠（swarm testing 雛形，Ch 43 深講）；把叢集改成 3 節點重跑三個測試（多數變 2）。

## 延伸挑戰

刻完基本盤後，往下再挖三層（難度遞增）：

1. **Snapshot / log compaction**：log 無限長會爆記憶體。實作 `InstallSnapshot`：狀態機到某個 index 打快照、截斷之前的 log；leader 對落後太多（`nextIndex` 已被截掉）的 follower 直接送 snapshot 而非逐筆補。對應 [Ch 23](./23-raft-membership-snapshot.md)。

2. **成員變更（membership change）**：叢集要能安全地加/減節點。直接換配置會有「新舊多數派不重疊」的瞬間造成雙 leader。實作 Raft 的 joint consensus（`C-old,new` 過渡配置）或 single-server change。這是 Raft 最難寫對的部分之一。

3. **線性一致讀（ReadIndex）**：naive 的「leader 直接回讀」不安全——它可能已被分區、自己還不知道。實作 ReadIndex：leader 記下當前 commitIndex，先發一輪心跳確認自己仍是多數派的 leader，收到多數回覆後才用那個 commitIndex 回讀。避免讀到過期資料，又不必每次讀都寫一筆 log。對應 [Ch 26](./26-raft-kv-linearizable-reads.md)。

## 自我檢核

不看程式碼，主動回想以下問題——答不出來的就是你還沒真懂的地方：

- [ ] 為什麼選舉逾時**必須**隨機化？如果全部固定成同一個值，會發生什麼具體災難？
- [ ] leader 為什麼**不能**直接 commit 一個「已被多數派複製的舊 term entry」？舉一個它之後被覆蓋、造成「已 commit 卻消失」的時序。（Figure 8）
- [ ] 「選舉限制」（candidate 的 log 要夠新才給票）在防什麼？拿掉它，被 commit 的 entry 會怎麼丟？
- [ ] 分區時，少數派那側的 leader 明明還自認 leader、還在接受 `Propose`，為什麼那些命令**永遠 commit 不了**？heal 之後它們去哪了？
- [ ] `nextIndex` 和 `matchIndex` 差在哪？為什麼 leader 推進 commit 要看 `matchIndex` 而不是 `nextIndex`？
- [ ] 舊 leader 帶著過期 term 復活，第一件被強制做的事是什麼？哪一行程式碼保證它不會繼續當 leader？
- [ ] 我能解釋為什麼跑在 `dsim` 上的 Raft，任何一個「選出兩個 leader」的 bug 都能用 seed 精準重現——而真網路上不能。

刻完並跑綠這三個測試，你不再是「讀過 Raft」，而是「寫過 Raft」。下一章我們把這個 Raft 抽象成更通用的框架：**複製狀態機（RSM）**——把任意確定性狀態機掛上共識層，就得到一個容錯的服務。你手上這份 log replication 正是它的引擎。

→ [Ch 25 複製狀態機（RSM）](./25-replicated-state-machine.md)
