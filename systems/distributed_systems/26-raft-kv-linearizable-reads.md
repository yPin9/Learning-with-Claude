# Ch 26 — 用 Raft 建 KV Store：線性一致讀

> **目標**：把 Ch 25 的 RSM 變成一個真正能對外服務的 KV store，並攻克它最反直覺的陷阱——**讀**。寫走 log 天生正確，但「直接讀 leader 本地狀態機」會讓你讀到過期資料，而且這不是 bug，是天真做法的必然結果。我們會在 `dsim` 上親手製造這個 stale read、看它讀出舊值，再用 ReadIndex 修好它。順帶講清楚三種讀法（讀走 log / ReadIndex / lease read）各自的正確性與代價。

> **環境**：Go 1.18.1, WSL2 / Linux x86-64。純標準庫，跑在 Ch 0 的 `dsim` 上，接練習 C 的 Raft。

## 為什麼需要這個？

你有了一個複製 KV（Ch 25）。寫的路徑很清楚：`put x=1` 走 Propose → append log → 多數派複製 → commit → apply。這條路徑的正確性由 Raft 的所有 safety 規則撐著，你不用擔心。

那**讀**呢？直覺上簡單到不像個問題：「leader 有最新狀態，客戶端讀，leader 回本地狀態機的值不就好了？」

這個直覺是錯的，而且錯得很危險。它會在一種特定但**極常見**的時序下，讓你回一個**已經過期、但你自己不知道過期**的值——違反線性一致性（linearizability）。

問題的根源是一句話：**一個 leader 可能已經被取代、但它自己還不知道。**

想像 leader `A` 被網路分區切進了少數派。多數派那邊在幾百毫秒內選出了新 leader `B`，`B` 接受並 commit 了新的寫入 `x=new`。但 `A` 呢？`A` 沒收到任何「你被取代了」的通知——分區把這些訊息全擋掉了。`A` 還老神在在地自認 leader，它本地狀態機裡 `x` 還是舊值 `old`。這時一個客戶端連到 `A` 讀 `x`，`A` 開開心心回 `old`。

客戶端得到了一個**在它讀的當下、系統裡早已不是最新值**的答案。而且沒有任何錯誤、沒有任何警告——這是最陰險的一種 bug：它安靜、它偶發、它只在分區時現形。

Raft 論文第 8 節專門處理這件事，把它列為「read-only operations 的正確性」問題。這不是可選的優化，是「你的 KV 到底線不線性一致」的分水嶺。

> 若對線性一致性的嚴格定義（每個操作彷彿在某一瞬間原子發生、且尊重實時順序）不熟，回看 [Ch 9](./09-consistency-models.md)。stale read 破壞的正是「尊重實時順序」這條。

## 先建立直覺

把 leader 的權威想成一張**有可能已經過期、但沒印到期日的通行證**。`A` 手上這張通行證上一秒還有效，但多數派剛剛「掛失重發」了一張給 `B`。`A` 不知道自己這張被作廢了——通訊被分區切斷，作廢通知送不到。

```
         時間 →
  A（舊 leader）: [leader........] ← 被分進少數派，訊息進不來
                  x=old            仍自認 leader，x 還是舊值
  --------------- 分區發生 -----------------
  B,C,D（多數派）:        [B 選為新 leader][commit x=new]
                                            系統的真相：x=new

  此刻客戶端讀 A：
     naive read  → A 回本地 x=old   ✗ 讀到過期值（A 不知道自己過期了）
```

修法的核心洞察：**讀之前，leader 必須先「證明自己此刻仍是合法 leader」**。怎麼證明？在 Raft 裡「合法 leader」的定義就是「握有多數派的支持」。所以 leader 發一輪心跳，如果**多數**回應「對，你還是我這個 term 的 leader」，那就證明了在心跳的這一刻，沒有更高 term 的 leader 存在（否則多數裡至少一個會拒絕它、或它會看到更高 term）。證明成功後，用當時的 commitIndex 服務讀就是安全的。

被分進少數派的 `A` 呢？它發心跳，但只能碰到少數派的節點，**永遠湊不到多數**。所以它的讀會被**卡住**（拿不到證明），而不是回一個過期值。**卡住是對的**——寧可讓客戶端逾時重試（連到真 leader），也不能給它一個錯的答案。這就是 ReadIndex。

## 三種讀法的全景

在細看 ReadIndex 前，先把三種讀法擺在一起，你才知道 ReadIndex 卡在光譜的哪裡。

```
正確性 ▲
       │  讀走 log ────── ReadIndex ────── lease read
       │  最安全          一樣安全          安全但賭時鐘
       │  最慢            中等              最快
       │  一筆 log 寫入    一輪心跳 RTT       零額外通訊（lease 內）
       └──────────────────────────────────────────────►  效能
```

**(a) 讀也走 log**：把讀當成一條命令 append 進 log、commit、apply 時回值。最直觀地正確——讀在 log 裡有明確位置，線性化點就是它 commit 那格。代價是**每次讀都寫一筆 log**：多一輪複製、log 白白變長、寫入吞吐被讀吃掉。對讀多寫少的負載（絕大多數真實系統）這代價高到不能接受。

**(b) ReadIndex**：不寫 log。leader 記下當前 commitIndex，發一輪心跳確認自己仍是多數派 leader，收到多數回應後，等本地狀態機 apply 到那個 commitIndex，就用它服務讀。省掉了 log 寫入，只付一輪心跳 RTT。**正確性跟讀走 log 一樣**，但快得多。這是實務主力。

**(c) lease read**：更進一步——leader 賭「我上次確認自己是 leader 之後的一小段時間（lease）內，不可能有新 leader 誕生」，於是在 lease 內的讀**完全免確認**，直接回本地。快到極致（零額外通訊），但它**依賴一個時鐘假設**：所有節點的時鐘漂移有上界。這把 Ch 4「實體時鐘會說謊」的風險引了進來——時鐘假設一破，lease read 就可能 stale。

> 若對「為什麼不能信任跨節點的實體時鐘、什麼是 clock skew 上界假設」不熟，回看 [Ch 4](./04-physical-clocks.md)。lease read 的安全性完全押在那個假設上。

我們接下來用 `dsim` 把 (a) 的天真變體（直接讀本地）的災難演出來，再用 (b) ReadIndex 修好。(c) lease read 依賴真時鐘，在邏輯時間的 `dsim` 裡無法忠實模擬其時鐘風險，我們只在概念上講清楚。

## 底層機制：ReadIndex 怎麼運作

ReadIndex 的完整協定（Raft 論文 §8）：

```
leader 收到讀請求 read(key)：
  1. readIndex := commitIndex          ← 記下當下的 commit 點
  2. （論文要求）確保當前 term 至少已 commit 過一筆 entry
     —— 保證 leader 的 commitIndex 反映了它 term 內的最新狀態
  3. 發一輪 heartbeat 給所有 follower
  4. 等到「多數」（含自己）回應這輪心跳
        → 這證明了：在心跳的這一刻，沒有更高 term 的 leader 存在
        → 所以 readIndex 是「至少和任何已完成的寫一樣新」的安全讀點
  5. 等本地狀態機 apply 到 readIndex（lastApplied >= readIndex）
  6. 回本地狀態機在 key 上的值
```

第 4 步是靈魂。為什麼「多數回應心跳」就能證明沒有更新的 leader？因為 Raft 的選舉需要多數票。如果存在一個 term 更高的新 leader，它必然已經拿到多數票——那多數裡至少有一個節點的 term 已經升高，這個節點收到舊 leader 的心跳時會**拒絕**（或它的拒絕回應會帶回更高 term，讓舊 leader stepDown）。所以「舊 leader 能湊到多數的正面心跳回應」與「存在更高 term 的 leader」兩者不可能同時成立。這是一個乾淨的反證。

第 5 步容易被忽略但必要：確認自己是 leader 是一回事，本地狀態機**真的 apply 到那個點**是另一回事。follower 剛當選 leader 時 commitIndex 可能已推進、但 apply 游標還沒追上，這時直接讀會讀到 apply 之前的舊狀態。所以要等 `lastApplied >= readIndex`。

畫成 `dsim` 的事件流：

```
StartReadIndex(key):
  readIndex = commitIndex
  acked = 1  (自己)
  廣播 ReadHeartbeat 給所有 peer
        │
        ▼  每個 peer OnMessage:
  onReadHeartbeat: 若 term 夠大 → 承認你是 leader，回 OK=true
        │
        ▼  leader OnMessage:
  onReadHeartbeatReply: acked++
        若 acked >= majority → 這個讀標記 ready
        │
        ▼  每次 tick/message 後:
  serveReads: 若 ready 且 lastApplied >= readIndex → 讀本地、回值、完成
```

被分進少數派的 leader 走同一套流程，但卡在「acked 永遠達不到 majority」——它只能收到少數派的回應。讀就一直是 `ready=false`，不會回過期值。

## 動手：製造 stale read，再用 ReadIndex 修好

我們在練習 C 的 Raft 上加兩條讀路徑。第一條是天真的直接讀：

```go
// read.go — 天真讀：直接讀 leader 本地狀態機，不做任何確認
func (r *Raft) NaiveRead(key string) (string, bool, bool) {
	if r.role != leader {
		return "", false, false
	}
	v, ok := r.sm.Get(key)
	return v, ok, true // 直接回本地值 —— 若自己是 stale leader 就回過期值
}
```

第二條是 ReadIndex。它需要一種新的心跳訊息與回應，和一個等待中讀的佇列：

```go
// read.go — ReadIndex 的訊息與狀態
type ReadHeartbeat struct{ Term, Epoch int }
type ReadHeartbeatReply struct {
	Term, Epoch int
	OK          bool
}

type pendingRead struct {
	key   string
	rIdx  int  // 發起讀時的 commitIndex
	acked int  // 收到多少心跳確認（含自己）
	ready bool // 是否已達多數確認
	done  bool
	value string
	found bool
}

func (r *Raft) StartReadIndex(key string, net *Net) *pendingRead {
	if r.role != leader {
		return &pendingRead{done: true} // 非 leader，拒絕
	}
	pr := &pendingRead{key: key, rIdx: r.commitIndex, acked: 1} // 自己算一票
	r.pendingReads = append(r.pendingReads, pr)
	r.readEpoch++
	for _, p := range r.peers {
		if p == r.id {
			continue
		}
		net.Send(Message{From: r.id, To: p, Payload: ReadHeartbeat{
			Term: r.currentTerm, Epoch: r.readEpoch,
		}})
	}
	return pr
}
```

follower 收到 ReadHeartbeat 時，若 leader 的 term 夠大就承認它（並重置自己的選舉計時器），回 `OK=true`：

```go
func (r *Raft) onReadHeartbeat(from NodeID, msg ReadHeartbeat, net *Net) {
	now := net.Now()
	ok := false
	if msg.Term >= r.currentTerm {
		if msg.Term > r.currentTerm {
			r.stepDown(msg.Term, now)
		}
		r.role = follower
		r.resetElectionTimer(now)
		ok = true // 「我承認你是我這個 term 的 leader」
	}
	net.Send(Message{From: r.id, To: from, Payload: ReadHeartbeatReply{
		Term: r.currentTerm, Epoch: msg.Epoch, OK: ok,
	}})
}
```

leader 累計回應，滿多數就把等待中的讀標記 ready；`serveReads` 在 apply 追上後完成讀：

```go
func (r *Raft) onReadHeartbeatReply(from NodeID, msg ReadHeartbeatReply, net *Net) {
	if msg.Term > r.currentTerm {
		r.stepDown(msg.Term, net.Now())
		return
	}
	if r.role != leader || !msg.OK {
		return
	}
	for _, pr := range r.pendingReads {
		if pr.ready || pr.done {
			continue
		}
		pr.acked++
		if pr.acked >= r.majority() { // 湊到多數 = 證明自己仍是合法 leader
			pr.ready = true
		}
	}
}

func (r *Raft) serveReads() {
	kept := r.pendingReads[:0]
	for _, pr := range r.pendingReads {
		if pr.done {
			continue
		}
		if pr.ready && r.lastApplied >= pr.rIdx { // 確認 + apply 到位，才回值
			pr.value, pr.found = r.sm.Get(pr.key)
			pr.done = true
			continue
		}
		kept = append(kept, pr)
	}
	r.pendingReads = kept
}
```

`OnMessage` 和 `OnTick` 尾端各加一行 `r.serveReads()`，並在 `OnMessage` 的 switch 補上兩個新訊息的 dispatch。主程式製造這個場景：選 leader → 寫 `x=old` 並 commit → 把舊 leader 分進少數派 → 多數派選新 leader、寫 `x=new` 並 commit → 分別對兩個 leader 試 naive read 和 ReadIndex：

真跑（WSL, Go 1.18.1，`go run .`，seed=3、5 節點、latency 1-2）：

```
初始 leader=0 term=1
已 commit x=old（leader 0 commit=1）

分區：minority=[0 1]（含舊 leader 0）majority=[2 3 4]
多數派新 leader=2 term=2，已 commit x=new（commit=2）
舊 leader 0 此刻 role=leader term=1（它還不知道自己被取代）

[naive read on 舊 leader 0] x="old" exists=true isLeader=true  ← 讀到過期值！
[ReadIndex on 舊 leader 0] ready=false done=false acked=2/需要3  ← 拿不到多數確認，讀被卡住（拒絕回過期值）
[ReadIndex on 新 leader 2] ready=true done=true value="new"  ← 確認自己仍是 leader，讀到正確值
```

三行輸出把整件事講完了：

1. **naive read 讀到過期值**：舊 leader `0` 還自認 leader（term 還是 1，它沒收到 term 2 的任何消息），直接回本地的 `x="old"`。系統真相是 `x=new`，客戶端拿到舊值——線性一致性破了。
2. **ReadIndex 在舊 leader 上被卡住**：它發心跳只碰得到少數派（節點 1），`acked=2`（自己 + 節點 1），永遠達不到 `majority=3`。讀 `ready=false`、`done=false`——它**拒絕回過期值**，寧可卡住讓客戶端逾時去連真 leader。
3. **ReadIndex 在新 leader 上成功**：新 leader `2` 在多數派裡，心跳拿得到多數確認，`ready=true`、`done=true`，讀到正確的 `x="new"`。

這就是 ReadIndex 的價值：它不會讓你讀到過期值，代價只是「stale leader 上的讀會卡住」——而那正是我們要的行為。

## 不只是分區：stale leader 的其他來源

上面的 demo 用網路分區製造 stale leader，因為它最直觀。但你要小心一個更廣的教訓：**任何讓 leader「與外界失聯一段時間、卻沒當機」的事件，都能製造 stale leader**——而且不需要真的斷網。

- **GC 長停頓（stop-the-world GC）**：leader 是 JVM/Go 程式，跑了一次長達幾秒的 full GC。這幾秒內它**完全凍結**——不發心跳、不處理訊息。其他節點等不到心跳，逾時、選出新 leader、commit 新資料。GC 結束、舊 leader「醒來」，它感覺只過了一瞬間，仍自認 leader——**stale**。這正是 Kleppmann 在 DDIA 裡那個著名的 "process pause" 例子。
- **VM 被暫停 / live migration**：雲環境裡 VM 可能被 hypervisor 暫停幾秒（遷移、超賣資源）。對 VM 內的程式，時間「跳」了——它以為的「上一次心跳」其實是很久以前。
- **NTP 大幅跳變**：時鐘被 NTP 往前/往後拉幾秒，任何依賴 wall-clock 判斷「lease 還沒過期」的邏輯瞬間錯亂。

這些的共同點：**leader 沒有 crash（crash 反而好，它就不發心跳、乾脆地被取代），而是「假死後復活」，帶著過時的自我認知回來。** 這就是為什麼「我是 leader」的自我認知本質上不可靠——它是一個**過去某一刻**為真的判斷，而分散式系統裡「過去為真」不蘊含「現在為真」。ReadIndex 的心跳確認之所以正確，正是因為它把判斷從「我記得我是 leader」升級成「我此刻剛剛向多數派確認過我是 leader」——把一個關於過去的陳述，換成一個關於當下的證明。

lease read 的危險也在這裡看得最清楚：它恰恰是**信任了「我記得我的 lease 到 T 為止」這個關於過去的判斷**。GC pause 或 VM 暫停讓時間跳過 T，程式卻不知道——lease read 就回了 stale。所以 lease read 的時鐘假設不只是「時鐘走得準」，更是「程式不會被凍結超過某個上界」，後者在有 GC、有虛擬化的環境裡是很強的假設。

## 對比與取捨

| 讀法 | 正確性 | 每次讀成本 | 依賴時鐘假設？ | 何時用 |
|---|---|---|---|---|
| 讀走 log | 線性一致 | 一輪 log 複製（寫一筆） | 否 | 幾乎不用（太貴），除非要簡單 |
| **ReadIndex** | **線性一致** | **一輪心跳 RTT** | **否** | **實務主力**，讀多寫少 |
| lease read | 線性一致（時鐘假設成立時） | 零（lease 內） | **是**（clock skew 上界） | 極致低延遲、能接受時鐘賭注 |
| 直接讀本地（naive） | **可能 stale，非線性一致** | 零 | — | **永遠不要**（除非明確接受 stale read） |

有一個常被忽略的細節：**follower 讀**。你可以讓 follower 也服務讀來分攤 leader 壓力，但 follower 的 ReadIndex 要先跟 leader 要一個 readIndex（follower 自己不知道 commit 到哪），再等自己 apply 到那個點。這叫 follower read，Ch 39 的 Spanner、TiKV 都有做，代價是多一次 leader 往返或依賴 leader lease。

「直接讀本地」不是全錯——如果你的應用**明確聲明可以接受 stale read**（例如「讀一個統計儀表板，晚幾百毫秒無所謂」），那直接讀本地換取零延遲是合理取捨。錯的是**以為它線性一致卻直接讀本地**。取捨要是清醒的。

## 踩雷集錦

1. **「leader 一定有最新資料，讀 leader 本地就對」→ 這是全章要打破的錯覺。** leader 身分可能已經過期而它自己不知道（分區、GC pause、時鐘卡頓都能造成）。「我是 leader」這個自我認知，在分散式裡**不是**「我此刻仍是 leader」的證明。要證明，得去問多數派。

2. **「ReadIndex 確認完自己是 leader 就能直接讀了」→ 漏了 apply 那步。** 確認 leader 身分只保證「readIndex 這個 commit 點是安全的」，但你的本地狀態機可能還沒 apply 到 readIndex。必須等 `lastApplied >= readIndex` 才讀，否則讀到的是 apply 游標之前的舊狀態。上面 `serveReads` 的兩個條件缺一不可。

3. **「新當選的 leader 馬上就能服務 ReadIndex」→ 有個空窗。** Raft 論文 §8 特別要求：leader 剛上任時，要**先 commit 一筆自己 term 的 entry**（實務上是一個 no-op entry），才能確定自己的 commitIndex 反映了最新狀態。否則新 leader 可能還沒把前任的 committed entry 認全，readIndex 取得太低。我們的 demo 為求聚焦沒放 no-op，真實系統務必要有。

4. **「lease read 沒有網路開銷，一定最好」→ 你把正確性押在時鐘上了。** lease read 假設「所有節點時鐘漂移不超過某上界」，這樣舊 leader 才能安全地認定「我的 lease 還沒過期 = 還沒有新 leader」。但 VM 被暫停、NTP 跳變、GC 長停頓都能讓一個節點的「時間感」跳掉幾秒——lease 假設一破，你就回 stale。這是 Ch 4「時鐘會說謊」的直接後果。用 lease read 前先問：我的部署環境時鐘假設站得住嗎？

5. **「讀不用去重」→ 對，但寫一定要（承 Ch 25）。** ReadIndex 讀是冪等的，重送無害。但別忘了寫路徑的 `(clientID, seq)` 去重（Ch 25），否則客戶端逾時重送 `x+=1` 會多加一次。讀寫的一致性要一起看，別只顧一邊。

## 進階：再往深一層

- **批次 ReadIndex**：一輪心跳可以同時確認**一批**等待中的讀（它們的 readIndex 都 <= 當下 commitIndex）。上面 `onReadHeartbeatReply` 就是把票加到所有 pending read 上——這正是批次的雛形。真實系統把讀請求攢一小批、共用一輪心跳，大幅攤薄 RTT 成本。

- **wait-free lease read 的實作細節**：etcd 的 lease read 是「leader 每次心跳成功就把 lease 往後推 `election_timeout` 的一個保守比例」，讀落在 lease 內就免確認。它靠的是「新 leader 選出前，舊 leader 的 lease 一定先過期」這個時間不等式——前提是各節點時鐘漂移有界。CockroachDB 更進一步用 HLC（hybrid logical clock）與明確的 lease 機制，Ch 39 會碰到。

- **linearizable read 與 stale read 的 API 分離**：成熟系統會把兩者做成不同 API（如 etcd 的 `Serializable` vs 預設的 linearizable），讓應用**自己選**要正確性還是要低延遲。這比「全部線性一致」更務實——不是所有讀都需要最新。

- **為什麼寫不需要 ReadIndex**：因為寫本來就走 log、本來就要多數派複製才 commit——commit 這步已經內建了「多數派確認」，stale leader 的寫根本 commit 不了（湊不到多數，見 Ch 25/練習 C 的分區測試）。是「讀不走 log」才漏掉了這層確認，所以要 ReadIndex 補回來。這個對稱性值得玩味：寫的安全來自 commit，讀的安全來自 ReadIndex，本質是同一個「多數派確認」。

## 本章重點整理

- 寫走 log 天生線性一致（commit 內建多數派確認）；**讀直接讀 leader 本地會 stale**——leader 身分可能已過期而它不自知。
- stale read 的根源：「我是 leader」的自我認知 ≠ 「我此刻仍是合法 leader」。後者只能靠問多數派來證明。
- 三種正確讀法：**讀走 log**（貴）、**ReadIndex**（一輪心跳確認，實務主力）、**lease read**（免通訊但賭時鐘）。
- ReadIndex 兩個必要條件：多數派心跳確認 leader 身分 + 本地 apply 追上 readIndex。缺一都可能讀到舊/未 apply 的狀態。
- stale leader 上的 ReadIndex 會**卡住**（湊不到多數），這是正確行為——寧可卡住讓客戶端重試，不給錯答案。
- lease read 把正確性押在時鐘漂移上界假設上，是 Ch 4「時鐘會說謊」風險的直接引入。

## 自我檢核

- [ ] 不看筆記，我能講出「直接讀 leader 本地」為什麼會 stale，並畫出觸發它的分區時序
- [ ] 我能解釋 ReadIndex 的「一輪心跳拿到多數回應」為什麼能證明「不存在更新的 leader」
- [ ] 我知道 ReadIndex 除了確認 leader 身分，為什麼還要等 `lastApplied >= readIndex`
- [ ] 我能說出被分進少數派的舊 leader，它的 ReadIndex 會發生什麼、為什麼那是對的
- [ ] 我能講清楚 lease read 快在哪、賭什麼、時鐘假設破掉會怎樣
- [ ] 我能解釋為什麼寫不需要 ReadIndex 而讀需要（提示：commit 這步做了什麼）

## 延伸閱讀

- **[Raft 論文](https://raft.github.io/raft.pdf) 第 8 節「Client interaction」** — Ongaro & Ousterhout（2014）
  - **這篇說什麼**：ReadIndex 與 lease read 的原始規格就在這節，含「新 leader 要先 commit no-op」「read-only 的 ReadIndex 協定」的精確描述
  - **讀哪裡**：整個第 8 節不長，"Processing read-only queries more efficiently" 那段是本章的原始出處
  - **前提**：你已刻過練習 C 的 Raft，讀這節會逐句對得上

- **[etcd 的線性一致讀實作](https://etcd.io/docs/latest/learning/api_guarantees/)** — etcd 官方文件
  - **這是什麼**：真實系統怎麼提供 linearizable 與 serializable（可 stale）兩種讀，以及各自的保證
  - **讀哪裡**："Consistency" 與 "Isolation level and consistency of replicas" 兩節，看它怎麼把本章的理論做成 API 選項

- **[TiKV 的 ReadIndex 與 lease read](https://tikv.org/deep-dive/consensus-algorithm/lease-read/)** — TiKV 官方 deep dive
  - **這篇說什麼**：把 ReadIndex 與 lease read 的取捨、follower read 講得很工程化，含真實效能數字
  - **讀哪裡**：整頁；lease read 那段補足了本章在 `dsim` 裡無法演示的時鐘假設細節
  - **為什麼值得讀**：它明確說明 lease read 需要的「clock drift bound」在生產環境怎麼設、怎麼被打破

- **《Designing Data-Intensive Applications》第 9 章** — Martin Kleppmann（O'Reilly, 2017）
  - **讀哪裡**："Linearizability and quorums" 那小節，用 quorum 讀的角度解釋 stale read，與本章 leader 讀的角度互補——同一個問題的兩個切面

我們有了線性一致的讀寫，但整個服務還是**一個 Raft group、一個 leader 扛所有流量**。當資料量和吞吐超過一台的上限，怎麼辦？答案是把資料切開——下一章談分片。

→ [Ch 27 分片（Sharding/Partitioning）](./27-sharding-partitioning.md)
