# Ch 20 — Raft ①：Leader Election

> **目標**：搞懂 Raft 為什麼要「強 leader」、把共識拆成 election / replication / safety 三塊來理解，然後親手在確定性模擬器上跑出 leader 選舉——3~5 節點冷啟動選出唯一 leader、殺掉 leader 看它重新選、並用 randomized timeout 展示怎麼躲開 split vote。這章打出 election 骨架，Ch 21 在同一份 `raft.go` 上加 log replication。

> **環境**：Go 1.18.1, WSL2/Linux x86-64。程式碼跑在 Ch 0 的 `dsim` 模擬器上，同 seed 逐位元組可重現。

## 為什麼需要這個？

你在 Ch 18–19 已經看過 Paxos。它是對的，經過機器證明，跑在 Chubby、Spanner 底下二十年沒出事。但如果你讀完 single-decree Paxos 還是覺得「我知道它每一步在幹嘛，但我講不出整體怎麼運作」——你不孤單。這正是 Raft 誕生的理由。

Ongaro 和 Ousterhout 在 2014 年那篇論文的標題就把話講死了：**In Search of an Understandable Consensus Algorithm**。他們做過一個實驗：找兩組學生，一組學 Paxos、一組學 Raft，考同樣的題目，Raft 組平均分數高出一截。他們的主張很直白——**Paxos 難懂不是使用者的問題，是 Paxos 的問題**。一個沒人能不看論文就正確實作的演算法，工程上是危險的。

Raft 沒有比 Paxos 更強（容錯能力一樣是 `2f+1` 節點容忍 `f` 個故障），它換的是**可理解性**。它靠三個設計決定買到這個：

1. **強 leader（strong leader）**：任何時刻最多一個 leader，所有 log 只從 leader 單向流向 follower。Paxos 允許任何節點對任何位置提案，Raft 直接禁止——log 的流向被鎖死成一條線，你腦子裡的模型瞬間簡單一半。
2. **問題分解（decomposition）**：把共識切成三個幾乎獨立的子問題——**leader 選舉**（誰當家）、**log 複製**（怎麼把命令散出去）、**safety**（怎麼保證不出錯）。你可以一次只想一塊。
3. **狀態空間收斂**：靠一堆「限制」把可能的狀態砍到最少（例如「log 不能有洞」「只有 log 夠新的能當選」），讓你需要推理的情況變少。

這章只碰第一塊：**選舉**。搞定「誰當 leader、怎麼選、怎麼避免選出兩個」，下一章才談當上 leader 後怎麼複製 log。

> 若你對「為什麼共識在會丟訊息的非同步網路裡這麼難、FLP 為什麼說它不可能」還沒感覺，回看 [Ch 16](./16-flp-impossibility.md)、[Ch 17](./17-circumventing-flp.md)。Raft 繞過 FLP 的手法就是 Ch 17 講的「用逾時當失敗偵測器」——這章的 election timeout 正是那個 `◇P` 的具體長相。

## 先建立直覺

把 Raft 叢集想成一間有嚴格規矩的辦公室。任何時刻，每個節點只會是三種身分之一：

```
        ┌──────────────┐  逾時沒收到心跳    ┌──────────────┐
        │              │ ───────────────>  │              │
        │   Follower   │                   │  Candidate   │
        │  (乖乖聽命)   │  <───────────────  │  (拉票中)     │
        │              │  收到更高 term      │              │
        └──────────────┘  或合法 leader 心跳 └──────────────┘
              ▲                                    │
              │ 收到更高 term                       │ 拿到多數票
              │ 的訊息就退位                        ▼
              │                             ┌──────────────┐
              └─────────────────────────────│    Leader    │
                                            │ (發號施令)    │
                                            └──────────────┘
```

- **Follower（追隨者）**：被動。只回應 leader 和 candidate 的訊息，自己不主動發起任何事。收到 leader 心跳就重置一個「選舉逾時（election timeout）」的計時器。
- **Candidate（候選人）**：follower 太久沒收到心跳，就懷疑 leader 掛了，於是升自己一個 term、變成 candidate、對所有人拉票。
- **Leader（領導者）**：拿到多數票的 candidate 當選。它負責處理所有 client 請求、週期性發心跳壓制其他人起來造反。

貫穿這一切的是 **term（任期）**——一個單調遞增的整數，Raft 的**邏輯時鐘**。

```
term 1        term 2                 term 3          term 4
├─選舉─┬─────┤├─選舉(流局)┤├─選舉─┬───┤├─────────────┤
      n2 當選              沒人當選    n0 當選         ...
      │                    重來        │
      └ 一個 term 最多一個 leader        └ 每個 term 重新選
```

term 有三個關鍵性質，記牢了這章就懂一半：

1. **每個 term 最多一個 leader**（可能零個——選舉流局）。
2. **每則 RPC 都帶著發送者的 term**。收到比自己大的 term，立刻更新自己的 term 並退回 follower;收到比自己小的 term，直接拒絕（對方是舊時代的幽靈）。
3. **term 是全域可比的邏輯時間**。「誰的資訊比較新」不看時鐘，看 term。

term 就是 Raft 版的 Lamport clock（[Ch 5](./05-lamport-clocks.md)），只是它專門用來排序「誰是合法的當家」。

## RequestVote：選舉怎麼跑

一輪選舉的完整流程：

```
follower n0 的 election timer 逾時（太久沒收到心跳）
   │
   ▼
n0 becomeCandidate:
   currentTerm++            ← 進入新 term
   votedFor = 自己           ← 先投自己一票
   role = Candidate
   reset election timer     ← 這輪也可能逾時，逾時就再開一輪
   │
   ├── RequestVote{term, candidateID, lastLogIndex, lastLogTerm} ──> n1
   ├── RequestVote{...} ──> n2
   ├── RequestVote{...} ──> n3
   └── RequestVote{...} ──> n4
                              │
        每個收到的節點問自己三件事：
        (1) 你的 term >= 我的 term 嗎？（否則你是舊幽靈，拒絕）
        (2) 我這個 term 還沒投過票 嗎？（votedFor == -1 或就是你）
        (3) 你的 log 至少跟我一樣新 嗎？（選舉限制，Ch 22 深講）
        三個都 yes → 投票，並重置自己的 timer
                              │
                              ▼
        n0 收集 RequestVoteReply，票數 >= 多數(majority) → becomeLeader
```

`RequestVote` RPC 帶四個欄位，前兩個是身分（term、誰在拉票），後兩個 `lastLogIndex/lastLogTerm` 是給「選舉限制」用的——保證只有 log 夠新的候選人能當選（[Ch 22](./22-raft-safety.md) 會證明這條為什麼是 safety 的命根子）。這章的選舉還沒有 log，所以這兩欄先掛著，等 Ch 21 加 log 後自然生效。

**投票規則的靈魂是「每個 term 一票、先到先得」**。這是防止選出兩個 leader 的第一道防線：一個節點在某個 term 只會投給第一個來拉票、且條件符合的候選人。多數決 + 每 term 一票 ⟹ 兩個候選人不可能同時拿到多數（多數集合一定相交，相交的那個節點只投了一次票）。這就是**過半數 quorum** 的威力，你在 [Ch 13](./13-quorum-replication.md) 已經見過同一招。

## 心跳：leader 怎麼壓制新選舉

leader 當選後不能閒著。只要它一段時間不發聲，follower 的 election timer 就會逾時、有人跳出來選舉、把它推翻。所以 leader 週期性（比 election timeout 短很多）對所有 follower 送**心跳**。

Raft 有個漂亮的偷懶設計：**心跳就是不帶任何 log entry 的 `AppendEntries` RPC**。它不需要另外設計一個心跳訊息——複製 log 用的那個 RPC，把 `entries` 設成空的，就是心跳。follower 收到它（term 合法）就重置 election timer，乖乖繼續當 follower。

```
leader n2                follower n0/n1/n3/n4
  │  AppendEntries{term, entries:[]}  │
  │ ─────────────────────────────────> │  收到 → reset election timer
  │        每 heartbeatInterval 一次    │  （timer 被壓住，永遠逾時不了）
  │ ─────────────────────────────────> │
  │                                    │
  X 若 n2 當機不再發心跳                  │  timer 終於逾時 → 有人起來選舉
```

這就是 Raft 的失敗偵測：**沒有專門的 heartbeat 監控，「一段時間沒收到 leader 的 AppendEntries」本身就是「leader 可能掛了」的訊號**。簡單、夠用。

## 底層機制：randomized election timeout 與 split vote

現在講這章最關鍵、最容易被輕視的一個設計：**為什麼 election timeout 要隨機化**。

想像所有 follower 用同一個固定的 election timeout（比如都是 8 個 tick）。leader 掛掉那一刻，大家的 timer 幾乎同時開始倒數。8 tick 後——**所有人同時逾時，同時變 candidate，同時把票投給自己，同時拉票**。結果：每個候選人都拿到自己那一票，沒人拿到多數。這叫 **split vote（分票）**，這一輪選舉**流局**。

更糟的是，流局後大家又同時重試，又撞在一起，又流局……理論上這可以無限重演，叢集永遠選不出 leader。這正是 [Ch 16](./16-flp-impossibility.md) FLP 說的「非同步共識可能不終止」的一個具體長相。

Raft 的解法簡單到有點狡猾：**每個節點的 election timeout 是一個區間內的隨機值**（論文用 150~300ms，我們模擬器用 `baseTimeout + rand()%(jitter+1)`）。這樣大家的逾時時刻被打散，某個倒霉鬼會先逾時、先發起選舉、先拿到多數票，其他人還沒醒就已經收到新 leader 的心跳被壓下去了。**用隨機打破對稱性（symmetry breaking）**——這是分散式系統裡反覆出現的母題（乙太網路的 exponential backoff 同一招）。

magic number 說明：我們的 `baseTimeout=8`、`timeoutJitter=8`（逾時落在 8~16 tick），`heartbeatInterval=3`。真實 Raft 的鐵律是 `heartbeatInterval << electionTimeout`——心跳週期必須遠小於選舉逾時，否則心跳還沒到、timer 就逾時了，叢集會一直誤判 leader 死亡而重選。我們設 3 << 8 就是遵守這條。這幾個數字不是玄學，是這個不等式逼出來的。

## 上程式碼：election 骨架

以下是 `raft.go` 的選舉部分（Ch 21 會在同一個 struct 上加 log replication，所以有些欄位這章先放著）。完整檔案跑在 Ch 0 的 `dsim` 上，把 `dsim.go` 的 `package dsim` 改成 `package main` 併入同目錄即可。

```go
type role int
const ( Follower role = iota; Candidate; Leader )

type Raft struct {
    id    NodeID
    peers []NodeID

    currentTerm int      // 持久：我看過的最大 term
    votedFor    NodeID   // 持久：這個 term 投給誰（-1 = 還沒投）
    log         []LogEntry // Ch 21 才真正用；index 0 是 dummy

    role        role
    commitIndex int      // Ch 21
    // ... leader 專用的 nextIndex/matchIndex 也是 Ch 21

    electionElapsed int  // 距上次重置過了幾 tick
    electionTimeout int  // 這輪的逾時門檻（隨機）
    heartbeatElapsed int
    votesGot map[NodeID]bool

    seed int64  // per-node 確定性偽隨機源，用來生 randomized timeout
}

const (
    heartbeatInterval = 3  // leader 每 3 tick 送一次心跳
    baseTimeout       = 8  // election timeout 下界
    timeoutJitter     = 8  // 上加 0..jitter，打破對稱躲 split vote
)
```

隨機逾時——注意我們**不用全域 `rand`**，每個節點自己一個 LCG（線性同餘），seed 從節點 id 種入，保證整個模擬確定可重現（Ch 0 的第一條踩雷）：

```go
func (r *Raft) rand() int {
    r.seed = r.seed*6364136223846793005 + 1442695040888963407 // 標準 LCG 常數
    x := int((r.seed >> 33) & 0x7fffffff)
    if x < 0 { x = -x }
    return x
}
func (r *Raft) resetElectionTimeout() {
    r.electionElapsed = 0
    r.electionTimeout = baseTimeout + r.rand()%(timeoutJitter+1) // 8..16
}
```

`OnTick`——時間流逝的入口。follower/candidate 檢查選舉逾時，leader 檢查心跳週期：

```go
func (r *Raft) OnTick(now int, net *Net) {
    if r.role == Leader {
        r.heartbeatElapsed++
        if r.heartbeatElapsed >= heartbeatInterval {
            r.heartbeatElapsed = 0
            r.broadcastAppend(net) // 空 entries = 心跳
        }
        return
    }
    r.electionElapsed++
    if r.electionElapsed >= r.electionTimeout {
        r.becomeCandidate(net) // 逾時 → 發起選舉
    }
}
```

發起選舉：

```go
func (r *Raft) becomeCandidate(net *Net) {
    r.currentTerm++              // 進新 term
    r.role = Candidate
    r.votedFor = r.id            // 投自己
    r.votesGot = map[NodeID]bool{r.id: true}
    r.resetElectionTimeout()     // 這輪也可能逾時，重開下一輪
    for _, p := range r.peers {
        net.Send(Message{From: r.id, To: p, Payload: RequestVote{
            Term: r.currentTerm, CandidateID: r.id,
            LastLogIndex: r.lastLogIndex(), LastLogTerm: r.lastLogTerm(),
        }})
    }
}
```

收到 `RequestVote`——三道關卡缺一不投：

```go
func (r *Raft) handleRequestVote(from NodeID, req RequestVote, net *Net) {
    if req.Term > r.currentTerm {
        r.becomeFollower(req.Term, net) // 對方 term 更高，先退位更新
    }
    grant := false
    if req.Term >= r.currentTerm &&                         // (1) 不是舊幽靈
        (r.votedFor == -1 || r.votedFor == req.CandidateID) && // (2) 這 term 沒投過
        r.candidateLogOK(req.LastLogTerm, req.LastLogIndex) {   // (3) log 夠新(Ch22)
        grant = true
        r.votedFor = req.CandidateID
        r.resetElectionTimeout() // 投票也算「聽到有人在領導」，重置 timer
    }
    net.Send(Message{From: r.id, To: from,
        Payload: RequestVoteReply{Term: r.currentTerm, VoteGranted: grant}})
}
```

收到投票回覆，湊到多數就當選：

```go
func (r *Raft) handleRequestVoteReply(from NodeID, rep RequestVoteReply, net *Net) {
    if rep.Term > r.currentTerm { r.becomeFollower(rep.Term, net); return }
    if r.role != Candidate || rep.Term != r.currentTerm { return } // 過期回覆丟掉
    if rep.VoteGranted {
        r.votesGot[from] = true
        if len(r.votesGot) >= r.majority() { r.becomeLeader(net) }
    }
}
func (r *Raft) majority() int { return (len(r.peers)+1)/2 + 1 }
```

處理 `AppendEntries`（這章先看它壓制選舉的部分——term 合法就退回 follower、重置 timer）：

```go
func (r *Raft) handleAppendEntries(from NodeID, req AppendEntries, net *Net) {
    // ... term 檢查 ...
    r.role = Follower
    r.resetElectionTimeout() // 收到合法 leader 心跳 → 壓住選舉
    // ... log 一致性檢查是 Ch 21 的事 ...
}
```

## 真跑：選舉三連

跑三個場景（WSL, Go 1.18.1，`dsim` seed 固定，你在你機器上跑同 seed 拿到一樣的結果）。

**場景 1——5 節點冷啟動，選出唯一 leader：**

```
=== 場景 1：5 節點冷啟動，選出唯一 leader ===
[t= 8 n2 candidate term=1] election timeout -> 發起選舉
[t= 8 n3 candidate term=1] election timeout -> 發起選舉
[t= 9 n0 follower  term=1] 投票給 n2 (term 1)
[t= 9 n4 follower  term=1] 投票給 n2 (term 1)
[t=10 n1 follower  term=1] 投票給 n2 (term 1)
[t=10 n2 leader    term=1] *** 當選 leader (拿到 3/5 票) ***
結果：leaders=[2]，各節點 term=[1 1 1 1 1]
```

注意 t=8 時 **n2 和 n3 同時逾時**（隨機 timeout 只是降低機率，不保證不撞）——這是一次輕微的競爭。但 n2 的拉票訊息先送達，n0/n4/n1 都把「這個 term 唯一的一票」投給了 n2，n3 什麼都拿不到。**先到先得 + 每 term 一票**當場化解了衝突，最終只有一個 leader。這比任何文字說明都直觀：即使兩人同時起跑，quorum 也只會認一個。

**場景 2——殺掉現任 leader，重新選舉：**

```
=== 場景 2：殺掉現任 leader，觀察重新選舉 ===
>>> t=40 殺掉 leader n2
[t=46 n0 candidate term=2] election timeout -> 發起選舉
[t=47 n3 follower  term=2] 投票給 n0 (term 2)
[t=47 n4 follower  term=2] 投票給 n0 (term 2)
[t=48 n1 follower  term=2] 投票給 n0 (term 2)
[t=49 n0 leader    term=2] *** 當選 leader (拿到 3/5 票) ***
結果：新 leaders=[0]（原 n2 已死不列入），term=[2 2 1 2 2]
```

`net.Crash(n2)` 之後，n2 不再發心跳。約 6 個 tick（一個 election timeout 週期）後，n0 的 timer 逾時、升 term=2、發起選舉，順利當選新 leader。**term 從 1 漲到 2**——這就是 term 作為邏輯時鐘的用途：新 leader 屬於新 term，任何還帶著 term=1 的舊訊息都會被當幽靈拒絕。n2 停在 term=1 是因為它死了、狀態凍結，這反而真實。

**場景 3——關掉 randomized timeout，看 split vote：**

```
=== 場景 3：關掉 randomized timeout -> 容易 split vote ===
無 jitter 跑 60 tick：leaders=[]，最高 term=7
  -> term 一路狂飆卻選不出 leader：每輪大家同時逾時、各投自己、票數平分流局，重試又撞在一起。
--- 對照：同樣 4 節點但開回 randomized timeout ---
有 jitter：leaders=[1]，最高 term=1（一兩輪就收斂）
```

這是這章最有說服力的一組對照。**關掉 jitter**（所有節點固定 8 tick 逾時），4 個節點永遠同步逾時、票永遠 2-2 平分，term 一路飆到 7 都選不出 leader——這就是 split vote 的死循環，活生生跑給你看。**開回 jitter**，同樣 4 節點在 term=1 就選出了 leader。一個看似不起眼的「隨機化逾時」，是 Raft 能終止的關鍵。

## 對比與取捨

| 面向 | Paxos (Multi-Paxos) | Raft |
|---|---|---|
| leader 角色 | 選擇性（可有 distinguished proposer，但非強制） | **強制強 leader**，log 只從 leader 單向流 |
| 選舉機制 | 論文沒明說，實作各自發揮 | **明定** RequestVote + randomized timeout |
| log 允許有洞嗎 | 允許（各位置獨立決議） | **不允許**，log 連續無洞 |
| 可理解性 | 惡名昭彰地難 | 設計目標就是好懂 |
| 容錯能力 | `2f+1` 容 `f` | 一樣 `2f+1` 容 `f` |

Raft 用「強 leader + 禁止 log 有洞」換來可理解性，代價是**寫入吞吐受限於單一 leader**（所有寫都過它）。這在多數場景是划算的——etcd、Consul、TiKV、CockroachDB 都選 Raft。需要 leaderless、更高寫入並行度的場景才會回頭看 EPaxos 那類（[Ch 24](./24-consensus-comparison.md) 會談）。

## 踩雷集錦

1. **錯誤直覺：「randomized timeout 保證不會 split vote」→ 正確：它只是大幅降低機率，不是消滅**。看場景 1，開了 jitter n2 和 n3 還是同時逾時了。隨機化打散的是「同時逾時」的機率，撞到了就靠「先到先得」化解，撞不到最好。Raft 的 liveness 是機率性的——這不是 bug，是繞過 FLP 的必然代價（FLP 說確定性演算法不可能同時保證 safety 和 liveness，Raft 選擇犧牲 liveness 的確定性）。

2. **錯誤直覺：「term 是計時器」→ 正確：term 是邏輯時鐘，跟真實時間無關**。term 只在「有人發起選舉」時 +1，一個 term 可能持續幾毫秒也可能幾小時。別把它想成秒數。它唯一的用途是**排序誰的資訊比較新**、拒絕過期訊息。

3. **錯誤直覺：「收到 RequestVote 就投」→ 正確：三道關卡缺一不投**。term 不夠大（舊幽靈）不投、這 term 投過票了不投、log 沒我新不投（Ch 22）。少任何一條，split-brain 或丟資料就會發生。尤其「每 term 一票」這條，是「不會選出兩個 leader」的數學基礎。

4. **錯誤直覺：「heartbeatInterval 設多少都行」→ 正確：必須 `heartbeat << electionTimeout`**。心跳週期若逼近或超過選舉逾時，心跳還在路上 timer 就逾時了，叢集會不斷誤判 leader 死亡、瘋狂重選、根本沒空幹活。我們設 3 << 8 是有意的。真實系統這條沒守好是「叢集莫名一直換 leader」的頭號原因。

5. **錯誤直覺：「candidate 拉票時要停下等結果」→ 正確：candidate 自己也在倒數，逾時就再開一輪**。`becomeCandidate` 裡也呼叫了 `resetElectionTimeout`。如果這一輪拉票沒湊到多數（比如票被瓜分、或回覆都丟了），candidate 自己的 timer 會再次逾時、升 term、重來。沒有這個「候選人也會重試」機制，一次流局就卡死了。

6. **錯誤直覺：「投票不用重置自己的 timer」→ 正確：投出票就等於承認「有人在試圖領導」，要重置 timer**。若投票不重置，你投完票馬上自己也逾時跳出來選，會加劇混亂。Raft 的規則：只要你「聽到了合法的領導活動」（收到心跳、投了票），就把自己的 election timer 壓回去。

## 進階：再往深一層

- **Pre-Vote 擴充**：基本 Raft 有個惱人問題——被網路分區隔離的節點會不斷逾時、不斷升 term。等它回到叢集時，它的 term 已經高得離譜，會逼現任 leader（term 較低）退位、觸發一次不必要的重選，白白中斷服務。**Pre-Vote** 讓 candidate 在真正 +1 term 之前先問一輪「假設我要選，你們會投我嗎」，沒把握就不升 term。etcd 預設開這個。Ongaro 的博士論文 §9.6 有完整描述。

- **Leader Lease / Leadership transfer**：leader 主動下台前可以把 leadership「交棒」給一個 log 最新的 follower（先追平它的 log，再叫它立刻發起選舉），避免一段無 leader 的空窗。用於滾動升級。

- **CheckQuorum**：leader 若連續幾個心跳週期都收不到多數 follower 的回應，應該主動退位（它可能自己被分到少數側了）。沒有這個，被隔離在少數側的舊 leader 會傻傻以為自己還在當家——雖然它的寫入永遠 commit 不了（湊不到多數），但它可能回覆過期的讀。這是 Ch 26 線性一致讀要處理的坑。

## 本章重點整理

- Raft 的賣點是**可理解性**，不是更強容錯。它靠**強 leader、問題分解、狀態限制**三招換來好懂。
- 三態機：**Follower / Candidate / Leader**。逾時沒收到心跳 → 變 candidate 選舉；拿多數票 → 變 leader；收到更高 term 或合法 leader → 退回 follower。
- **term 是邏輯時鐘**：單調遞增、每則 RPC 帶著、每 term 最多一個 leader、收到更高 term 就退位。
- 選舉靠 **RequestVote** + 投票規則（**term 夠大、每 term 一票、log 夠新**）。多數決 + 每 term 一票 ⟹ 不可能兩個 leader。
- 心跳 = **空 entries 的 AppendEntries**，壓制 follower 的選舉逾時。
- **randomized election timeout** 打破對稱性、躲開 split vote——是 Raft 能終止的關鍵，我們用固定 timeout 跑出了 term 飆到 7 都選不出 leader 的死循環來證明它的必要。

## 自我檢核

- [ ] 不看圖，我能畫出 Follower/Candidate/Leader 三態之間的所有轉換，並說出每條邊的觸發條件
- [ ] 我能解釋「為什麼多數決 + 每 term 一票」能保證一個 term 不會有兩個 leader（用 quorum 相交論證）
- [ ] 我能說出投票的三道關卡分別在防什麼失敗，拿掉任一條會出什麼事
- [ ] 我能解釋 split vote 怎麼發生、randomized timeout 怎麼化解，以及為什麼它只是機率性保證
- [ ] 我知道為什麼 `heartbeatInterval` 必須遠小於 `electionTimeout`，違反了會怎樣
- [ ] 我能說出 term 為什麼是「邏輯時鐘」而不是「計時器」

## 延伸閱讀

- **[In Search of an Understandable Consensus Algorithm (Raft)](https://raft.github.io/raft.pdf)** — Ongaro & Ousterhout, USENIX ATC 2014
  - **讀哪裡**：§5.1（Raft basics / term）、§5.2（Leader election）。這章逐節對照的就是這兩節，Figure 4（狀態轉換）和 Figure 2（RPC 定義）務必看
  - **為什麼值得讀**：分散式系統論文裡少見的好讀，作者刻意寫給人看的。全課 Ch 20–23 的骨架都出自它

- **[Raft 視覺化 · thesecretlivesofdata.com/raft](https://thesecretlivesofdata.com/raft/)**
  - **讀哪裡**：Leader Election 那一段動畫。卡在「split vote 到底長怎樣」時，這個動畫十秒讓你看懂
  - **前提**：無，純動畫

- **[Consensus: Bridging Theory and Practice](https://github.com/ongardie/dissertation)** — Diego Ongaro 博士論文（2014）
  - **讀哪裡**：§3（Raft basics）、§9.6（Pre-Vote）。論文的加長完整版，Pre-Vote / membership change 等論文塞不下的細節都在這
  - **前提**：先讀完上面那篇論文

- **[etcd raft 原始碼](https://github.com/etcd-io/raft)** — 生產級 Raft 實作
  - **讀哪裡**：`raft.go` 的 `Step` 函式（狀態機分派）、`campaign`（發起選舉）、`tickElection`。看真實系統怎麼把這章的骨架做成能扛生產流量的東西
  - **前提**：讀完論文再來，否則會被工程細節淹沒

選舉解決了「誰當家」，但當家要幹的正事是**把 client 的命令可靠地複製到所有節點**。下一章我們在同一份 `raft.go` 上加 log replication——這才是 Raft 真正在做的事。

→ [Ch 21 Raft ②：Log Replication](./21-raft-log-replication.md)
