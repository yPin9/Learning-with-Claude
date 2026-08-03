# Ch 21 — Raft ②：Log Replication

> **目標**：leader 選出來了，現在要幹正事——把 client 的命令可靠地複製到所有節點。搞懂 log entry 的結構、**Log Matching Property**、一致性檢查怎麼靠 `prevLogIndex/prevLogTerm` 回退對齊分歧尾巴、commitIndex 怎麼在多數複製後推進、apply 到狀態機。在 Ch 20 的同一份 `raft.go` 上加 replication，真跑：正常複製、落後 follower 被回補、分歧尾巴被覆蓋對齊。

> **環境**：Go 1.18.1, WSL2/Linux x86-64。程式碼跑在 Ch 0 的 `dsim` 上，同 seed 可重現。

## 為什麼需要這個？

Raft 存在的意義不是「選出一個 leader」——選 leader 只是手段。真正的目的是：**讓一群節點對「發生了哪些操作、以什麼順序發生」達成一致**。這串操作就是 **replicated log（複製日誌）**。

回想 [Ch 25](./25-replicated-state-machine.md) 會講的複製狀態機（RSM）核心洞見：**如果每個節點從同一個初始狀態出發、按同樣的順序套用同一串命令，它們的最終狀態必然相同**。這是決定性（determinism）的力量。所以「讓多台機器保持一致」這個模糊的目標，被 Raft 化約成一個具體得多的問題——**讓所有節點的 log 內容完全相同**。

log 一旦相同，狀態機一定相同。於是共識問題就變成「複製一個 log」。這就是為什麼 Raft 的心臟是 log replication，而不是選舉。

> 若你對「為什麼複製一個 log 就等於複製整個系統」還沒感覺，可以先跳看 [Ch 25](./25-replicated-state-machine.md) 開頭，再回來——不過本章會自足地把 log 講清楚。

## 先建立直覺

把每個節點的 log 想成一疊有編號的紙條，每張紙條記一個命令：

```
        index:   1      2      3      4      5
              ┌──────┬──────┬──────┬──────┬──────┐
  leader  n2  │ x=1  │ y=2  │ z=3  │ a=4  │ b=5  │  ← leader 手上最全
              │ t=1  │ t=1  │ t=1  │ t=2  │ t=2  │  （每格記 term）
              └──────┴──────┴──────┴──────┴──────┘
                         ▲ commitIndex=3（前 3 格已被多數複製，安全了）
              ┌──────┬──────┬──────┬──────┐
  follower n0 │ x=1  │ y=2  │ z=3  │ a=4  │         ← 落後一格，還在追
              │ t=1  │ t=1  │ t=1  │ t=2  │
              └──────┴──────┴──────┴──────┘
              ┌──────┬──────┐
  follower n1 │ x=1  │ y=2  │                        ← 落後更多
              │ t=1  │ t=1  │
              └──────┴──────┘
```

每個 log entry 有三個東西：

- **index**：它在 log 裡的位置（1-based；我們實作在 index 0 放一個 dummy 哨兵，省掉一堆邊界判斷）。
- **term**：這個 entry 是在哪個 term 被 leader 建立的。**這是 Raft 的秘密武器**——term 讓你能一眼判斷兩個節點在某個位置的 entry 是不是同一個。
- **command**：實際要套用到狀態機的操作（`set x=1` 之類）。

leader 收到 client 命令，做四步：(1) append 到自己 log 尾巴，(2) 透過 `AppendEntries` 送給所有 follower，(3) 等到**多數**節點也寫進去了，才把這個 entry 標記為 **committed**，(4) 把 committed 的 entry 依序 apply 到狀態機、回覆 client。

關鍵字是**多數（majority）**。一個 entry 只要被過半節點複製，就算其中一些節點之後掛掉，剩下的多數裡一定還有人留著它——這就是它「安全、不會丟」的定義。

## Log Matching Property：Raft 的核心不變量

Raft 靠一條不變量（invariant）撐起整個 log 的一致性，叫 **Log Matching Property**。它有兩半：

> **(1)** 若兩個節點的 log 在**某個 index** 有相同的 **term**，則該 index 上的 **command 相同**。
> **(2)** 若兩個節點的 log 在某個 index 有相同的 term，則**該 index 之前的所有 entry 都相同**。

第一半靠一個簡單事實成立：**一個 (term, index) 組合，全世界最多對應一個 command**。因為每個 term 最多一個 leader（Ch 20 的投票規則保證），而只有 leader 能建立 entry，且它不會在同一個 index 建兩個不同的 entry。所以 (term=3, index=5) 這個座標，全叢集只會有一個唯一的 command。

第二半是遞迴推出來的，而**維持它的機制就是下面要講的一致性檢查**。這條性質太重要了：它讓你只要比對**一個** (index, term) 相符，就能斷定**整段前綴**都相同——不用逐格比對整條 log。這是 Raft 效率和正確性的基石。

```
如果 n0 和 n2 在 index=3 都是 term=1
   ──Log Matching──> index 1,2,3 三格 command 全部相同
   （不用一個個比，一個相符就保證前綴全相符）
```

## 底層機制：一致性檢查與 nextIndex 回退

問題來了：follower 可能落後、可能有一段錯的尾巴（跟過一個後來被推翻的舊 leader）。leader 怎麼把它拉回一致？

leader 為每個 follower 維護一個 **nextIndex**——「我下次要從第幾格開始送給你」。每個 `AppendEntries` 都夾帶兩個檢查欄位：

- **prevLogIndex**：這批新 entry 之前那一格的 index（= nextIndex - 1）。
- **prevLogTerm**：那一格的 term。

follower 收到後做**一致性檢查（consistency check）**：「我的 log 在 prevLogIndex 這格，term 是 prevLogTerm 嗎？」

- **符合** → 太好了，Log Matching 保證我們前綴全同。把新 entry 接上去（若有分歧尾巴就砍掉重接），回 `Success`。
- **不符合**（我根本沒這格，或這格 term 對不上）→ 回 `Fail`。leader 收到 fail，把這個 follower 的 **nextIndex 減 1**，下次送多一格、往前探。一直退到雙方找到共同前綴為止。

畫成流程：

```
leader 對 follower 送 AppendEntries{prevLogIndex=4, prevLogTerm=2, entries=[...]}
                              │
        follower 檢查：我 log[4].term == 2 嗎？
              ┌───────────────┴────────────────┐
            符合                              不符合
              │                                 │
     接上 entries                       回 Fail
     （分歧尾巴先砍掉）                       │
     回 Success + matchIndex           leader: nextIndex[f]--
              │                          下個週期送 prevLogIndex=3 再試
     leader: matchIndex[f]=...                  │
              │                          還不符? 再退... 直到找到共同前綴
     嘗試推進 commitIndex                       │
                                        找到後：從共同前綴一路覆蓋到最新
```

**回退可能一格一格慢**（論文提過可以帶額外資訊一次退一個 term，加速；我們的教學版就一格一格退，夠清楚）。一旦找到共同前綴，leader 就把從那裡到最新的所有 entry 一次覆蓋過去，follower 的分歧尾巴被 leader 的正確版本取代。**leader 永遠是對的、永遠不改自己的 log，只叫 follower 對齊自己**——這是強 leader 的直接後果，也是為什麼 Raft 好推理。

一致性檢查的 follower 端程式碼（`raft.go`）：

```go
func (r *Raft) handleAppendEntries(from NodeID, req AppendEntries, net *Net) {
    rep := AppendEntriesReply{Term: r.currentTerm, Success: false}
    if req.Term < r.currentTerm {           // 舊 leader 的幽靈訊息，拒絕
        net.Send(...rep...); return
    }
    r.role = Follower
    r.resetElectionTimeout()                 // 合法 leader → 壓住選舉(Ch20)

    // ★ 一致性檢查：prevLogIndex 這格 term 對不上 → 拒絕，逼 leader 回退
    if req.PrevLogIndex > r.lastLogIndex() ||
        r.log[req.PrevLogIndex].Term != req.PrevLogTerm {
        net.Send(...rep...); return
    }

    // 對齊：逐格比對，遇到分歧就砍掉尾巴、接上 leader 的版本
    i := req.PrevLogIndex + 1
    for j, e := range req.Entries {
        if i+j <= r.lastLogIndex() {
            if r.log[i+j].Term != e.Term {   // 分歧！
                r.log = r.log[:i+j]           // 砍掉分歧尾巴
                r.log = append(r.log, req.Entries[j:]...)
                break
            }
        } else {
            r.log = append(r.log, req.Entries[j:]...) // 純追加
            break
        }
    }
    rep.Success = true
    rep.MatchIndex = req.PrevLogIndex + len(req.Entries)
    if req.LeaderCommit > r.commitIndex {    // 跟上 leader 的 commit
        r.commitIndex = min(req.LeaderCommit, r.lastLogIndex())
        r.applyCommitted(net)
    }
    net.Send(...rep...)
}
```

> **注意那個「砍掉分歧尾巴」不能無腦砍**。只有當 follower 的 entry 和 leader 送來的 entry **term 真的不同**時才砍。如果一則遲到的、內容其實一樣的 `AppendEntries` 重複送達，你不該把已經對齊、甚至已經 commit 的尾巴砍掉——那會是災難性的資料回退。我們的迴圈用 `r.log[i+j].Term != e.Term` 逐格比對，相同就跳過，只砍真正分歧的部分。這是實作 Raft 最常見的 bug 之一。

leader 端收到回覆、更新 nextIndex/matchIndex：

```go
func (r *Raft) handleAppendEntriesReply(from NodeID, rep AppendEntriesReply, net *Net) {
    if rep.Term > r.currentTerm { r.becomeFollower(rep.Term, net); return }
    if r.role != Leader || rep.Term != r.currentTerm { return }
    if rep.Success {
        r.matchIndex[from] = rep.MatchIndex   // 這個 follower 對齊到哪了
        r.nextIndex[from]  = rep.MatchIndex + 1
        r.advanceCommit(net)                  // 也許可以推進 commit 了
    } else {
        if r.nextIndex[from] > 1 { r.nextIndex[from]-- } // ★ 回退，下次多送一格
    }
}
```

## commitIndex：多數複製才算數

leader 手上的 entry append 進去，**還沒 committed**。它只有在確認「**多數**節點的 log 都有這個 entry」之後，才敢把它標記為 committed、apply 到狀態機、回覆 client。

判斷方式：leader 掃自己的 log，對每個還沒 commit 的 index，數有多少 follower 的 `matchIndex >= idx`（加上 leader 自己就是複製份數）。過半就 commit：

```go
func (r *Raft) advanceCommit(net *Net) {
    for idx := r.lastLogIndex(); idx > r.commitIndex; idx-- {
        if r.log[idx].Term != r.currentTerm { continue } // ★ 只 commit 當前 term(Ch22)
        count := 1                                       // leader 自己算一份
        for _, p := range r.peers {
            if r.matchIndex[p] >= idx { count++ }
        }
        if count >= r.majority() {
            r.commitIndex = idx
            r.applyCommitted(net)  // 依序 apply 到狀態機
            break
        }
    }
}
```

那行 `if r.log[idx].Term != r.currentTerm { continue }` 現在看起來突兀——「為什麼只能 commit 當前 term 的 entry？舊 term 被多數複製了不也安全嗎？」**這正是 Ch 22 Figure 8 要處理的致命陷阱，本章先埋著、下章證明它為什麼是 safety 的命根子。** 你現在只要記住：Raft **不靠計數直接 commit 舊 term 的 entry**。

commit 之後 apply——把 committed 但還沒套用的 entry 依序丟進狀態機：

```go
func (r *Raft) applyCommitted(net *Net) {
    for r.lastApplied < r.commitIndex {
        r.lastApplied++
        r.applied = append(r.applied, r.log[r.lastApplied].Command) // 套用到狀態機
    }
}
```

`commitIndex` 和 `lastApplied` 是兩個不同的指標：前者是「哪些已經安全」，後者是「哪些已經真的套用到狀態機」。分開的原因是 apply 可能是昂貴操作（寫 KV、跑交易），要能非同步慢慢追。

leader 的 commitIndex 怎麼傳給 follower？夾在 `AppendEntries` 的 `LeaderCommit` 欄位裡。follower 看到 `LeaderCommit` 比自己高，就把自己的 commitIndex 跟上（但不超過自己 log 的長度），然後 apply。**follower 從不自己決定 commit，一律聽 leader 的。** 又是強 leader。

## 真跑：複製三連

`dsim` 上跑三個場景（WSL, Go 1.18.1，seed 固定可重現）。

**場景 1——正常複製，多數複製才 commit：**

```
=== 場景 1：leader 複製 log 到 followers，多數複製才 commit ===
選出 leader = n2
[t=30 n2 leader term=1] append 命令 "set x=1" 到 index 1
[t=32 n2 leader term=1] commitIndex 推進到 1 (多數 2 已複製)
[t=42 n2 leader term=1] append 命令 "set y=2" 到 index 2
[t=45 n2 leader term=1] commitIndex 推進到 2 (多數 2 已複製)
[t=54 n2 leader term=1] append 命令 "set z=3" 到 index 3
[t=56 n2 leader term=1] commitIndex 推進到 3 (多數 2 已複製)
複製完成後各節點狀態：
  n0 [follower] log(term)=[1 1 1] commit=3 applied=[set x=1 set y=2 set z=3]
  n1 [follower] log(term)=[1 1 1] commit=3 applied=[set x=1 set y=2 set z=3]
  n2 [leader  ] log(term)=[1 1 1] commit=3 applied=[set x=1 set y=2 set z=3]
```

三筆命令 append → 各晾兩三個 tick 等 `AppendEntries` 往返 → **多數（3 節點裡的 2）複製後 commitIndex 推進 → apply**。最終三個節點的 log 和 applied 完全相同。注意 `commit` 總是在 `append` 之後幾個 tick 才發生——這個時間差就是「等多數確認」的網路往返。**在確認之前，client 的寫入還不能算成功**，這是 Raft 給你的線性一致寫入語意的代價。

**場景 2——落後的 follower 被回補：**

```
=== 場景 2：落後的 follower 被回補 ===
leader = n2，先讓三節點都同步兩筆
>>> 隔離 follower n0，leader 這段時間繼續收 c/d/e
隔離期間（n0 停在 2 筆，多數側已到 5 筆）：
  n0 [candidate] log(term)=[1 1]         commit=2 applied=[a b]
  n1 [follower]  log(term)=[1 1 1 1 1]   commit=5 applied=[a b c d e]
  n2 [leader  ]  log(term)=[1 1 1 1 1]   commit=5 applied=[a b c d e]
>>> heal，n0 開始被回補
回補後：
  n0 [follower] log(term)=[1 1 1 1 1] commit=5 applied=[a b c d e]
  n1 [follower] log(term)=[1 1 1 1 1] commit=5 applied=[a b c d e]
  n2 [leader  ] log(term)=[1 1 1 1 1] commit=5 applied=[a b c d e]
```

我們用 `net.Partition` 把 n0 隔離。隔離期間 n2 和 n1 這個**多數**繼續 commit（`c/d/e`），叢集照常運作——**這就是容錯**：5 節點少 1 個（其實這裡是 3 節點少 1，剩 2 仍是多數）還是能服務。n0 被關在外面收不到，停在 2 筆，甚至因為收不到心跳而變成 candidate 在空轉。`net.Heal()` 之後，leader 對 n0 的 `AppendEntries` 恢復送達，n0 一致性檢查通過、**一路被回補到 5 筆**，追平多數。整個過程 leader 沒做任何特殊處理——同一套 nextIndex 機制自動把落後者拉回來。

**場景 3——分歧尾巴被覆蓋對齊：**

```
=== 場景 3：有分歧尾巴的 follower 被覆蓋對齊 ===
人工在 n0 尾巴塞入分歧 entry（term 99 的 ghost1/ghost2）：
  n0 [follower] log(term)=[1 1 99 99] commit=2 applied=[p1 p2]
  n1 [follower] log(term)=[1 1]       commit=2 applied=[p1 p2]
  n2 [leader  ] log(term)=[1 1]       commit=2 applied=[p1 p2]
>>> leader n2 複製新命令 q1，觀察對 n0 的一致性回退
[t=50 n2 leader term=1] append 命令 "q1" 到 index 3
[t=52 n2 leader term=1] commitIndex 推進到 3 (多數 2 已複製)
對齊後（n0 的 ghost 尾巴被覆蓋成 leader 的 log）：
  n0 [follower] log(term)=[1 1 1] commit=3 applied=[p1 p2 q1]
  n1 [follower] log(term)=[1 1 1] commit=3 applied=[p1 p2 q1]
  n2 [leader  ] log(term)=[1 1 1] commit=3 applied=[p1 p2 q1]
```

我們手動在 n0 尾巴塞了兩筆 `term=99` 的 ghost entry（模擬它曾跟過一個後來被推翻的短命 leader，log 尾巴分歧了）。leader n2 複製新命令 `q1` 時，`AppendEntries` 的一致性檢查在 index 2 就發現 n0 的 term（99）對不上、拒絕，leader 回退 nextIndex，最終把 n0 的 `[1 1 99 99]` **覆蓋對齊成 `[1 1 1]`**——ghost 尾巴被無情抹掉。這就是「leader 永遠對、follower 對齊 leader」的實際運作：**分歧的、未被多數 commit 的尾巴一定會被覆蓋**（下一章會證明：已經被多數 commit 的 entry 絕不會遇到這種命運，那正是 safety 的核心）。

## 對比與取捨

| 面向 | 主從複製（Ch 12） | Quorum 複製（Ch 13） | **Raft log replication** |
|---|---|---|---|
| 寫入確認 | 主寫完即回（或等從） | 等 W 個節點 | 等**多數**節點 |
| 順序保證 | 靠單一主 | 無全域順序 | **全域一致的 log 順序** |
| 落後節點修復 | 各家自訂 | read-repair / anti-entropy | **nextIndex 回退自動對齊** |
| 分歧處理 | 通常無 | 靠版本/vector clock 合併 | **leader 直接覆蓋** |
| 一致性強度 | 取決於實作 | 可調（W+R>N 才強一致） | 線性一致 |

Raft 用「單一 leader 決定順序 + 多數確認 + 覆蓋式修復」買到**一個全域一致的操作序列**，這是 quorum 複製給不了的（它只保證每個 key 的新舊，不保證跨 key 的全域順序）。代價是寫入吞吐卡在單 leader。這個取捨在需要「線性一致 + 好推理」的元資料系統（etcd、ZooKeeper）裡是完勝的。

## 踩雷集錦

1. **錯誤直覺：「entry append 到 leader 的 log 就算成功」→ 正確：append ≠ commit**。append 只是寫進 leader 自己的 log，此時掛掉這筆會消失。只有**多數複製後 commit** 的 entry 才保證不丟。回覆 client「成功」必須等到 commit，不是 append。搞錯這個，你會在 leader 崩潰時丟掉「已回覆成功」的寫入——最嚴重的一致性 bug。

2. **錯誤直覺：「follower 收到 AppendEntries 就無腦砍掉尾巴接上新的」→ 正確：只砍 term 真正分歧的部分**。遲到、重複的 `AppendEntries` 很常見。若你無腦 truncate，一則過期的、內容較短的 `AppendEntries` 會把已經對齊甚至 commit 的尾巴砍掉，造成資料回退。必須逐格比對 term，相同就保留、只砍真正分歧處。

3. **錯誤直覺：「commit 是靠數 matchIndex 過半就好」→ 正確：還要 entry 的 term == 當前 term**。這是 Ch 22 Figure 8 的坑。leader 不能因為「某個舊 term 的 entry 被多數複製了」就 commit 它——那可能被後來的 leader 覆蓋。只能透過 commit「當前 term」的 entry，順帶把它前面的舊 entry 一起帶 commit。下章詳證。

4. **錯誤直覺：「follower 可以自己決定 commit」→ 正確：follower 的 commitIndex 一律跟 leader 走**。follower 從 `AppendEntries` 的 `LeaderCommit` 學到「leader 已經 commit 到哪」，才跟著 commit。它自己看不到全叢集的複製狀況，沒資格自己判斷。強 leader 的一致體現。

5. **錯誤直覺：「nextIndex 和 matchIndex 是同一個東西」→ 正確：nextIndex 是樂觀猜測、matchIndex 是已證實的事實**。nextIndex 是「我下次**試著**從這送」，可能猜太前面被打回、回退。matchIndex 是「這 follower **確定**已對齊到這」，只在收到 Success 時更新，是 commit 計數的唯一依據。混用兩者是常見 bug——尤其別拿 nextIndex 去算 commit。

6. **錯誤直覺：「commitIndex 和 lastApplied 可以合成一個」→ 正確：分開，因為 apply 可能慢**。commit 是「安全了」，apply 是「真的執行到狀態機」。狀態機操作可能昂貴（寫盤、跑交易），要能非同步落後追趕。合成一個會逼 commit 等 apply，拖垮吞吐。

## 進階：再往深一層

- **加速回退（fast backup）**：教學版一格一格退 nextIndex，一個差很多的 follower 要退幾百次。論文 §5.3 尾註提了個優化：follower 拒絕時回報「我在那個 term 的第一個 index」或「我 log 多長」，leader 一次跳過一整個 term。etcd/TiKV 都實作了這個。

- **batching 與 pipelining**：真實系統不會一筆一筆送。leader 把多筆 client 命令攢成一批一次 `AppendEntries`（batching），且不等前一批回覆就送下一批（pipelining），大幅提升吞吐。我們的教學版是同步一問一答，好懂但慢。

- **持久化與 fsync**：`currentTerm`、`votedFor`、`log` 是**持久狀態**——必須在回覆 RPC **之前** fsync 到磁碟，否則崩潰重啟後「忘記自己投過票」或「忘記某個 committed entry」，safety 就破了。這是實務上 Raft 效能的最大瓶頸（每筆寫入至少一次 fsync），也是為什麼有 group commit、raft log 專用 SSD 這些優化。我們模擬器不落盤，這塊是「未實測，理論上必要」。

- **read 怎麼辦**：這章只講寫入複製。線性一致的**讀**其實很微妙——leader 不能直接讀自己的狀態就回（它可能已被分區、是個過氣 leader）。要靠 ReadIndex / lease read 確認自己仍是多數認可的 leader。這是 [Ch 26](./26-raft-kv-linearizable-reads.md) 的主題。

## 本章重點整理

- Raft 的目的是**複製一個 log**——log 相同 ⟹ 狀態機相同（RSM 原理）。選 leader 只是手段。
- **log entry = (index, term, command)**。term 讓你能一眼判斷兩節點在某位置是不是同一個 entry。
- **Log Matching Property**：某 (index, term) 相符 ⟹ 該 command 相同、且前綴全同。這是效率與正確性的基石。
- **一致性檢查**靠 `prevLogIndex/prevLogTerm`：不符則 leader 回退 nextIndex，逐步找到共同前綴、覆蓋分歧尾巴對齊。**leader 永遠對，follower 對齊 leader**。
- **commit = 多數複製**。append ≠ commit；只有 commit 的 entry 才安全、才 apply、才回覆 client。
- **commitIndex 與 lastApplied 分開**；follower 的 commit 一律跟 leader 的 `LeaderCommit` 走。
- 真跑驗證了：正常複製、落後 follower 用 nextIndex 自動回補、分歧尾巴（ghost entry）被覆蓋對齊。

## 自我檢核

- [ ] 我能說出 leader 收到 client 命令後的四個步驟，並解釋「append ≠ commit」的分界在哪
- [ ] 我能陳述 Log Matching Property 的兩半，並解釋第一半為什麼靠「每 term 一 leader」成立
- [ ] 不看程式碼，我能描述一致性檢查失敗後 nextIndex 怎麼回退、怎麼找到共同前綴
- [ ] 我能解釋為什麼「follower 砍尾巴」必須逐格比 term、不能無腦 truncate
- [ ] 我能說出 commitIndex 和 lastApplied 為什麼要分開
- [ ] 我能指出 `advanceCommit` 裡「只 commit 當前 term」那行在防什麼（即使我還沒完全懂為什麼——那是 Ch 22）

## 延伸閱讀

- **[In Search of an Understandable Consensus Algorithm (Raft)](https://raft.github.io/raft.pdf)** — Ongaro & Ousterhout, USENIX ATC 2014
  - **讀哪裡**：§5.3（Log replication）、Figure 2 的 AppendEntries RPC 定義、Figure 3 的五條性質。本章逐句對照 §5.3
  - **為什麼值得讀**：一致性檢查、nextIndex 回退的原始描述，比任何二手解說都清楚

- **[Designing Data-Intensive Applications](https://dataintensive.net/)** — Martin Kleppmann, O'Reilly 2017
  - **讀哪裡**：Ch 9「Consistency and Consensus」的 "Total Order Broadcast" 與 "Distributed Transactions and Consensus" 兩節
  - **學什麼**：把 Raft 的 log 複製放進「全序廣播（total order broadcast）」這個更大的框架看——複製 log 本質上就是全序廣播（[Ch 7](./07-total-order-broadcast.md)）

- **[Students' Guide to Raft](https://thesquareplanet.com/blog/students-guide-to-raft/)** — Jon Gjengset（MIT 6.824 助教）
  - **讀哪裡**：整篇，尤其 "The importance of details" 和 nextIndex/matchIndex 那段
  - **學什麼**：實作 Raft 時最常踩的坑（包括本章踩雷 2、5 那兩個），寫給正在做 lab 的人看，血淚經驗

- **[etcd raft `log.go` / `MsgApp` 處理](https://github.com/etcd-io/raft)** — 生產級實作
  - **讀哪裡**：`raftLog.maybeAppend`、`raft.handleAppendEntries`、`raft.maybeCommit`
  - **前提**：讀完論文 §5.3 再來，對照本章的教學版看真實系統怎麼做 fast backup、batching

log 能複製、能對齊了，但我們還沒證明一件最要命的事：**已經回覆給 client「成功」的 entry，永遠不會丟**。這在 leader 頻繁更迭、網路分區交錯的極端時序下並不顯然——下一章逐步走一遍 Figure 8，看 Raft 用哪兩條 safety 規則堵死這個洞。

→ [Ch 22 Raft ③：Safety 與選舉限制](./22-raft-safety.md)
