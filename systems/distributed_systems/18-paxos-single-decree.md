# Ch 18 — Paxos：single-decree

> **目標**：從零推導 single-decree Paxos（單值 Paxos）——它是所有實用共識協定的祖師爺。我們不背協定，而是**逐步推導每一條規則為什麼非存在不可**：為什麼 acceptor 要拒絕更小的編號、為什麼 proposer 要採用「已被 accept 的最高編號的值」、為什麼多數決保證不會決定兩個值。最後在 Ch 0 的模擬器上實作一個 3-acceptor 的 Paxos，讓兩個 proposer 競爭，**親眼看它憑什麼只決定一個值**——貼真跑輸出。

> **環境**：Go 1.18.1, WSL2/Linux x86-64。程式碼跑在 [Ch 0 的 dsim 模擬器](./00-environment-setup.md)上。

## 為什麼需要這個？

Ch 17 我們知道了：靠部分同步（timeout + leader）可以繞過 FLP，讓共識在網路夠好時終止。但「靠 leader」有個雞生蛋問題——**選 leader 本身就是共識**（Ch 15 說過 leader election 歸約到共識）。所以我們需要一個**不預設 leader 也能保 safety** 的共識協定：就算同時有好幾個節點都自以為是 leader、都在提議、彼此打架，它也**絕不決定出兩個不同的值**。

這就是 Paxos 的定位。Lamport 1998 年的《The Part-Time Parliament》提出它（用一個虛構的希臘議會當比喻，結果沒人看懂，八年後他又寫了《Paxos Made Simple》重講一遍）。Paxos 的核心承諾是：

> **無論有多少 proposer 同時競爭、無論訊息怎麼延遲亂序、無論 acceptor 怎麼當機重啟，Paxos 永遠只會決定一個值（safety 絕不違反）。終止（liveness）則靠實務上收斂到單一 leader 來保證。**

它把 safety 和 liveness **乾淨地切開**：safety 由協定的數學結構鐵板釘釘地保證，liveness 交給「大多數時候只有一個 leader」這個工程假設。這個切法是 Paxos 最深刻的貢獻，也是它為什麼難懂——因為它為了 safety 的極致，把協定壓縮到每一個位元都在防某個競爭情境，沒有一句廢話，也就沒有直覺的餘地。這一章的任務就是**把每一句話背後防的那個情境挖出來**。

## 先建立直覺：角色與兩階段

Paxos 有三種角色（一個實體可以同時扮演多個）：

```
   Proposer（提議者）      Acceptor（接受者）       Learner（學習者）
   ┌──────────────┐       ┌──────────────┐        ┌──────────────┐
   │ 想讓某個值被   │       │ 投票的「議員」 │        │ 只想知道最後   │
   │ 決定。發起提案  │──────►│ 記住自己承諾/   │───────►│ 決定了什麼值   │
   │ 兩階段推動      │◄──────│ 接受了什麼      │        │（不參與決策）   │
   └──────────────┘       └──────────────┘        └──────────────┘
        （可以有多個，會競爭）    （通常奇數個，多數決）      （純觀察）
```

- **Proposer**：想推動某個值被決定。它不能自己拍板，得說服**多數 acceptor** 接受它的提案。
- **Acceptor**：分散式的「記憶體」+「投票者」。它們是唯一持有狀態的角色。**決定 = 一個提案被多數 acceptor 接受**。
- **Learner**：只想知道結果。當它發現某個值被多數 acceptor 接受了，它就學到了「決定值」。

協定分**兩個階段**，每階段一來一回：

```
   Proposer                          Acceptor（多數）
      │  ── Phase 1a: Prepare(n) ──►    │   "我想用編號 n 提案，先問問路"
      │  ◄─ Phase 1b: Promise(n, ...) ─ │   "好，我承諾不理更小的編號；
      │                                 │    順便告訴你我之前接受過什麼"
      │  ── Phase 2a: Accept(n, v) ──►  │   "那我正式提議值 v（編號 n）"
      │  ◄─ Phase 2b: Accepted(n, v) ── │   "接受！"
      │
   收到多數 Accepted → 值 v 被決定（chosen）
```

Phase 1 是**探路 + 佔坑**（用編號 n 宣示主權，並打聽有沒有人已經決定過東西）。Phase 2 是**正式提議**。看起來多此一舉——為什麼要兩階段？為什麼不直接提議？答案就是接下來要逐步推導的東西。**每一條看似囉嗦的規則，都在防一個具體的腦裂情境。**

## 逐步推導：從「最笨的協定」開始加規則

理解 Paxos 的最好方式，是從一個顯然錯的協定出發，找出它的漏洞，然後加一條規則堵住，重複這個過程直到協定正確。我們的目標約束是這個核心不變式：

> **P2：一旦一個值 v 被決定（被多數 acceptor 接受），那麼之後任何被決定的值也必須是 v。**（只決定一個值）

### 嘗試 0：直接多數決

最笨的協定：proposer 直接發 `Accept(v)`，acceptor 誰來就接受誰，收到多數 Accepted 就算決定。

**漏洞**：兩個 proposer P0 提 "apple"、P1 提 "banana" 同時發。acceptor A0、A1 接受了 apple，A2、A0 接受了 banana……亂成一團，可能 apple 拿到 {A0,A1} 兩票、banana 拿到 {A2,A0} 兩票（A0 接受了兩次）。在 5 個 acceptor 下甚至可能 apple 和 banana 各湊出一個多數 → **決定兩個值，腦裂**。

**病根**：acceptor 來者不拒，沒有「先來後到」的秩序。

### 加規則 1：acceptor 只能接受一個值？——不行，會卡死

直覺修法：acceptor **只接受它看到的第一個值**，之後拒絕。

**新漏洞**：假設 5 個 acceptor，apple 被 A0、A1 接受，banana 被 A2、A3 接受，A4 還沒收到任何訊息。現在 apple 和 banana **都只有兩票，都湊不到多數（3 票）**，而所有 acceptor 都已經「用掉」了自己唯一的接受權（除了 A4）。A4 接受誰，誰也才 3 票——但另一個永遠停在 2 票。這次沒腦裂，但**可能永遠卡在誰都不夠多數**，而且 acceptor 一旦接受就不能改，死局。

**病根**：acceptor 只能接受一次太僵硬。我們需要讓 acceptor **可以改變主意接受新提案**，但改的時候不能破壞已經被決定的值。這就逼出了「提案編號」和「兩階段」。

### 加規則 2：提案編號 + 「拒絕更小的編號」

給每個提案一個**全域唯一、可比大小的編號 n**（實務上用 `(輪次, proposerID)` 這種 lexicographic pair 保證唯一且可排序）。

規則：**acceptor 一旦承諾了編號 n，就拒絕所有編號 < n 的 Prepare 和 Accept。**

這就是 Phase 1 的 Prepare/Promise 存在的理由：proposer 先用編號 n 發 `Prepare(n)`，acceptor 若沒承諾過更大的編號，就回 `Promise(n)`——**承諾今後不再理會 < n 的任何東西**。這建立了「先來後到」的秩序：編號大的提案能「蓋過」編號小的，acceptor 可以改接受新（更大編號的）提案，但舊的編號自動失效。

在程式碼裡，acceptor 的 `promisedN` 就記這個承諾：

```go
case Prepare:
    if p.N > a.promisedN {
        a.promisedN = p.N            // 承諾：今後拒絕 < p.N 的一切
        // 回 Promise，OK: true
    } else {
        // 回 Promise，OK: false（拒絕，你的編號太小了）
    }
```

**但這還不夠**。編號秩序解決了「誰能蓋過誰」，卻沒解決最致命的問題：如果一個值**已經被決定**了（被多數接受），一個帶著更大編號、想提**不同**值的 proposer 闖進來，它會用大編號蓋過舊的，然後提議它自己的值——**把已決定的值改掉，腦裂！** 這就需要最後、也是最精妙的一條規則。

### 加規則 3（P2c，核心）：proposer 必須「繼承」已被接受的值

這是 Paxos 的靈魂，Lamport 稱為 **P2c 不變式**。規則是：

> **proposer 在 Phase 2 要提議的值，不能是它自己想提的值——如果它在 Phase 1 的 Promise 回應裡發現，有 acceptor 已經接受過某個值，它必須改提「已被接受的、編號最高的那個值」。**

換句話說，Promise 回應裡 acceptor 要**帶上「我之前接受過的最高編號提案 (acceptedN, acceptedV)」**。proposer 收齊多數 Promise 後，看這些回應：

- 如果**沒有任何** acceptor 接受過值 → proposer 自由，提它自己想提的值。
- 如果**有** acceptor 接受過值 → proposer **必須放棄自己的值**，改提「所有 Promise 回應裡 acceptedN 最高的那個 acceptedV」。

程式碼裡，proposer 收齊多數 Promise 後這樣選值：

```go
if pr.promises == pr.quorum {
    v := pr.myV                 // 預設：提我自己想提的值
    if pr.bestAccN >= 0 {       // 但若有人已接受過值……
        v = pr.bestAccV         // P2c：必須沿用已被接受的最高編號的值
    }
    // 發 Accept(pr.myN, v)
}
```

**為什麼這條就夠了？** 直覺論證：假設值 v 已被決定（被多數 Q 接受，編號 m）。現在一個編號 n > m 的新 proposer 進來，它的 Phase 1 要收集多數 Promise，這個多數叫 Q'。**關鍵：任意兩個多數必相交**（下一節詳談），所以 `Q ∩ Q'` 至少有一個 acceptor a。a 屬於 Q，所以 a 接受過 v（編號 m）；a 也屬於 Q'，所以 a 的 Promise 回應會**告訴新 proposer「我接受過 (m, v)」**。於是新 proposer 在 Phase 1 就會看到 v，被 P2c 逼著也提 v。**它想改都改不了。** 已決定的值像病毒一樣，透過「多數必相交」感染每一個後續的 proposer，永遠傳下去。

## 底層機制：兩個多數必相交——safety 的數學核心

整個 Paxos 的 safety 壓在一個小學數學事實上：

> **在 N 個 acceptor 中，任意兩個「多數（超過一半）」的集合，必定至少有一個共同成員。**

```
   5 個 acceptor: {A0, A1, A2, A3, A4}
   多數 = 至少 3 個

   多數 Q  = {A0, A1, A2}
   多數 Q' = {A2, A3, A4}
                └─ A2 在兩邊都有！

   證明：|Q| + |Q'| ≥ 3 + 3 = 6 > 5 = |全體|
        兩個集合大小加起來超過全體 → 鴿籠原理 → 必有重疊
```

這個「重疊的 acceptor」是資訊傳遞的橋樑：

- v 被決定 = v 被某個多數 Q 接受。
- 任何後來想提案的 proposer，Phase 1 要問到一個多數 Q'。
- `Q ∩ Q' ≠ ∅`，那個交集裡的 acceptor **記得** v，會在 Promise 裡把 v 報告出去。
- P2c 逼新 proposer 沿用 v。

**這就是為什麼 acceptor 數量通常取奇數**：5 個 acceptor，多數是 3，能容忍 2 個當機（還剩 3 個湊得出多數）。偶數（如 6 個）多數是 4，也只能容忍 2 個當機，卻多花一台機器——奇數性價比更高。容錯公式：**N 個 acceptor 容忍 `f = ⌊(N-1)/2⌋` 個 crash**，即 `N = 2f+1`（呼應 Ch 15 的下界）。

把 safety 論證串成一條完整的 ASCII 推理：

```
  v 被決定（多數 Q 在編號 m 接受 v）
        │
        │ 任何編號 n > m 的新提案，Phase 1 問到多數 Q'
        ▼
  Q ∩ Q' ≠ ∅（兩多數必相交）  ── 交集裡的 acceptor a 記得 (m, v)
        │
        ▼
  a 在 Promise 回應裡報告 (m, v) 給新 proposer
        │
        ▼
  P2c 逼新 proposer 也提 v（而非它自己的值）
        │
        ▼
  於是編號 n 決定的也是 v ── 遞迴地，之後所有決定都是 v
        │
        ▼
  ∴ 只會決定一個值。safety 成立。□
```

## 真跑：3-acceptor Paxos，兩個 proposer 競爭

理論夠了，上模擬器。我們建 3 個 acceptor（多數 = 2）、1 個 learner、2 個競爭的 proposer：

- **Proposer 0**：想提 `"apple"`，用編號 n=1，在 now=1 起手。
- **Proposer 1**：想提 `"banana"`，用**更大**的編號 n=2，稍晚在 now=8 起手（此時 apple 可能已被部分接受）。

我們要看的是：P1 帶著更大的編號和**不同的值**闖進來，它到底能不能把已被接受的 `apple` 改成 `banana`？

完整程式碼（`main.go`，與 `dsim.go` 同放一個 `package main` 目錄；`dsim.go` 就是 Ch 0 的模擬器，把 `package dsim` 改成 `package main` 複製進來）：

```go
package main

import (
    "fmt"
    "sort"
)

// ============ 訊息型別 ============
type Prepare struct{ N int }
type Promise struct {
    N           int
    AcceptedN   int    // acceptor 曾接受的最高編號（無則 -1）
    AcceptedV   string
    HasAccepted bool
    OK          bool   // 是否承諾（編號夠大）
}
type Accept struct {
    N int
    V string
}
type Accepted struct {
    N  int
    V  string
    OK bool
}

// ============ Acceptor ============
type Acceptor struct {
    id        NodeID
    learner   NodeID
    promisedN int // 已承諾的最高編號（初始 -1）
    acceptedN int // 已接受的最高編號（初始 -1）
    acceptedV string
}

func newAcceptor(id, learner NodeID) *Acceptor {
    return &Acceptor{id: id, learner: learner, promisedN: -1, acceptedN: -1}
}

func (a *Acceptor) OnMessage(m Message, net *Net) {
    switch p := m.Payload.(type) {
    case Prepare:
        if p.N > a.promisedN {
            a.promisedN = p.N // 承諾：今後拒絕 < p.N 的一切
            net.Send(Message{From: a.id, To: m.From, Payload: Promise{
                N: p.N, AcceptedN: a.acceptedN, AcceptedV: a.acceptedV,
                HasAccepted: a.acceptedN >= 0, OK: true,
            }})
        } else {
            net.Send(Message{From: a.id, To: m.From, Payload: Promise{N: p.N, OK: false}})
        }
    case Accept:
        if p.N >= a.promisedN { // 沒承諾過更大的編號才接受
            a.promisedN = p.N
            a.acceptedN = p.N
            a.acceptedV = p.V
            net.Send(Message{From: a.id, To: m.From, Payload: Accepted{N: p.N, V: p.V, OK: true}})
            net.Send(Message{From: a.id, To: a.learner, Payload: Accepted{N: p.N, V: p.V, OK: true}})
        } else {
            net.Send(Message{From: a.id, To: m.From, Payload: Accepted{N: p.N, OK: false}})
        }
    }
}
func (a *Acceptor) OnTick(now int, net *Net) {}

// ============ Learner：收集 Accepted，某編號被多數接受即宣告決定 ============
type Learner struct {
    id       NodeID
    quorum   int
    votes    map[int]map[NodeID]string // n -> acceptor -> v
    decided  bool
    decidedV string
    decidedN int
}

func newLearner(id NodeID, quorum int) *Learner {
    return &Learner{id: id, quorum: quorum, votes: map[int]map[NodeID]string{}}
}
func (l *Learner) OnMessage(m Message, net *Net) {
    p, ok := m.Payload.(Accepted)
    if !ok || !p.OK {
        return
    }
    if l.votes[p.N] == nil {
        l.votes[p.N] = map[NodeID]string{}
    }
    l.votes[p.N][m.From] = p.V
    if !l.decided && len(l.votes[p.N]) >= l.quorum {
        l.decided, l.decidedN, l.decidedV = true, p.N, p.V
    }
}
func (l *Learner) OnTick(now int, net *Net) {}

// ============ Proposer ============
type Proposer struct {
    id        NodeID
    acceptors []NodeID
    myN       int
    myV       string
    quorum    int
    started   int
    phase     int // 0 idle, 1 prepare, 2 accept, 3 done
    promises  int
    bestAccN  int
    bestAccV  string
    chosenV   string
}

func (pr *Proposer) OnTick(now int, net *Net) {
    if pr.phase == 0 && now == pr.started {
        pr.phase, pr.promises, pr.bestAccN = 1, 0, -1
        for _, a := range pr.acceptors {
            net.Send(Message{From: pr.id, To: a, Payload: Prepare{N: pr.myN}})
        }
    }
}

func (pr *Proposer) OnMessage(m Message, net *Net) {
    switch p := m.Payload.(type) {
    case Promise:
        if pr.phase != 1 || p.N != pr.myN || !p.OK {
            return
        }
        pr.promises++
        if p.HasAccepted && p.AcceptedN > pr.bestAccN {
            pr.bestAccN, pr.bestAccV = p.AcceptedN, p.AcceptedV
        }
        if pr.promises == pr.quorum {
            v := pr.myV
            if pr.bestAccN >= 0 {
                v = pr.bestAccV // P2c：沿用已被接受的最高編號的值
            }
            pr.chosenV, pr.phase = v, 2
            for _, a := range pr.acceptors {
                net.Send(Message{From: pr.id, To: a, Payload: Accept{N: pr.myN, V: v}})
            }
        }
    case Accepted:
        if pr.phase == 2 && p.N == pr.myN && p.OK {
            pr.phase = 3
        }
    }
}

func main() {
    const A0, A1, A2 NodeID = 0, 1, 2
    const L NodeID = 3
    const P0, P1 NodeID = 4, 5
    quorum := 2 // 3 acceptor 的多數

    net := NewNet(42)
    net.SetLatency(1, 2)

    acc := []NodeID{A0, A1, A2}
    learner := newLearner(L, quorum)
    net.Add(A0, newAcceptor(A0, L))
    net.Add(A1, newAcceptor(A1, L))
    net.Add(A2, newAcceptor(A2, L))
    net.Add(L, learner)

    p0 := &Proposer{id: P0, acceptors: acc, myN: 1, myV: "apple", quorum: quorum, started: 1}
    p1 := &Proposer{id: P1, acceptors: acc, myN: 2, myV: "banana", quorum: quorum, started: 8}
    net.Add(P0, p0)
    net.Add(P1, p1)

    net.Run(60)

    fmt.Printf("proposer0 (n=%d, want=%q) phase=%d chose=%q\n", p0.myN, "apple", p0.phase, p0.chosenV)
    fmt.Printf("proposer1 (n=%d, want=%q) phase=%d chose=%q\n", p1.myN, "banana", p1.phase, p1.chosenV)
    fmt.Println("---- acceptor 最終狀態 ----")
    for _, id := range acc {
        a := net.nodes[id].(*Acceptor)
        fmt.Printf("acceptor%d: promisedN=%d acceptedN=%d acceptedV=%q\n", id, a.promisedN, a.acceptedN, a.acceptedV)
    }
    fmt.Println("---- learner ----")
    if learner.decided {
        fmt.Printf("DECIDED value=%q at n=%d\n", learner.decidedV, learner.decidedN)
    }
    fmt.Println("---- 各提案編號拿到的 accepted 票數 ----")
    ns := []int{}
    for n := range learner.votes {
        ns = append(ns, n)
    }
    sort.Ints(ns)
    for _, n := range ns {
        vs := map[string]int{}
        for _, v := range learner.votes[n] {
            vs[v]++
        }
        fmt.Printf("n=%d votes=%d detail=%v\n", n, len(learner.votes[n]), vs)
    }
}
```

真跑（WSL, Go 1.18.1）：

```
$ go run .
proposer0 (n=1, want="apple") phase=3 chose="apple"
proposer1 (n=2, want="banana") phase=3 chose="apple"
---- acceptor 最終狀態 ----
acceptor0: promisedN=2 acceptedN=2 acceptedV="apple"
acceptor1: promisedN=2 acceptedN=2 acceptedV="apple"
acceptor2: promisedN=2 acceptedN=2 acceptedV="apple"
---- learner ----
DECIDED value="apple" at n=1
---- 各提案編號拿到的 accepted 票數 ----
n=1 votes=3 detail=map[apple:3]
n=2 votes=3 detail=map[apple:3]
```

**這個輸出就是 P2c 的活體證據，逐行讀**：

1. `proposer0 ... chose="apple"`：P0 用編號 1 提了 apple，成功（phase=3）。
2. `proposer1 (want="banana") ... chose="apple"`：**P1 明明想提 banana，最後卻提了 apple！** 因為它 Phase 1 的 Prepare(n=2) 打聽到「已經有 acceptor 接受過 apple」，P2c 逼它放棄 banana、改提 apple。它想改都改不成。
3. `DECIDED value="apple"`：learner 學到的決定值是 apple。
4. 最後兩行是鐵證：**編號 n=1 和 n=2 都拿到 3 票，而且都是 apple**（`detail=map[apple:3]`）。banana 一票都沒有。**兩個 proposer 競爭，值從頭到尾沒變過。** 這就是「只決定一個值」在真實執行裡的樣子。

我把兩個 proposer 的起手時間、延遲都調過，就是為了製造「P0 先讓 apple 被部分接受，P1 才帶著更大編號和不同值闖進來」這個最有教學價值的競爭時序。這個結果同 seed 每次跑都一樣（模擬器確定性），你在你機器上跑應該拿到逐字相同的輸出。

## 對比與取捨

| 面向 | Paxos 的選擇 | 代價 |
|---|---|---|
| safety | 由「兩多數必相交 + P2c」數學保證，無論多少 proposer 競爭 | 協定極度反直覺，每一步都在防競爭 |
| liveness | **不保證**（呼應 FLP），靠實務收斂到單一 leader | 純 Paxos 可能活鎖（下面踩雷 4） |
| 訊息輪數 | 每個值要 2 個 RTT（Phase 1 + Phase 2） | Multi-Paxos（Ch 19）省掉 Phase 1 優化 |
| 角色 | proposer / acceptor / learner 分離 | 概念清楚，但實作常合併 |

## 踩雷集錦

1. **「Phase 1 是多餘的，直接 Phase 2 提議不就好了」**：不行。Phase 1 是**打聽「有沒有值已被接受」**的唯一機會——沒有它，新 proposer 就不知道該不該繼承舊值，P2c 無從執行，已決定的值會被後來者覆蓋 → 腦裂。Phase 1 的 Prepare/Promise 是 safety 的探照燈，關掉它 Paxos 就瞎了。

2. **「proposer 提的一定是自己想提的值」**：這是最常見的誤解，也是 Paxos 最反直覺的地方。上面的真跑輸出打臉了它：P1 想提 banana，結果被 P2c 逼著提了 apple。**proposer 常常提出一個不是自己想要的值**——它是「已決定值的搬運工」，不是「自己意志的執行者」。內化這一點，你才算真懂 Paxos。

3. **「編號 n 是時間戳 / 遞增計數器就好」**：n 必須**全域唯一且可比大小**。如果兩個 proposer 用同一個 n，「拒絕更小的編號」的秩序就崩了。實務上用 `(round, proposerID)` 的字典序 pair：round 相同時用 proposerID 破平手，保證任兩個提案編號絕不相等。

4. **「Paxos 保證終止」**：不保證（FLP 的直接後果）。純 Paxos 有**活鎖（livelock）**風險：P0 用 n=1 Prepare 成功，正要 Accept 時，P1 用 n=2 Prepare 把 A 的承諾蓋掉，P0 的 Accept 被拒；P0 改用 n=3 重來，又被 P1 的 n=4 蓋掉……兩個 proposer 無限互相搶佔，**永遠沒人能走完兩階段**。解法就是 Ch 17 的部分同步——選一個 distinguished proposer（leader），只讓它提案，別人不搶。這正是 Multi-Paxos（Ch 19）要做的。

5. **「acceptor 接受 = 值被決定」**：不對。一個 acceptor 接受，只是「一票」。**值被決定 = 被多數 acceptor 接受**。單個 acceptor 的 acceptedV 隨時可能被更大編號的提案改掉；只有「多數同時接受同一個 (n, v)」的那一刻，值才真正 chosen，且從此不可逆。上面輸出裡 learner 就是靠「數到 2 票」才宣告 DECIDED。

## 進階：再往深一層

- **Lamport 的 P1/P2/P2a/P2b/P2c 推導鏈**：《Paxos Made Simple》用一條漂亮的不變式加強鏈導出協定——從 P2（只決定一個值）逐步加強到 P2a、P2b，最後到可實現的 P2c。本章的「從笨協定加規則」就是這條鏈的白話版。去讀原文的 §2.1–2.3，你會看到每一步加強都是「為了讓上一步可實現」而被逼出來的——沒有一個決定是任意的。

- **learner 怎麼可靠學到決定值**：本章的 learner 靠 acceptor 主動通報。實務中有多種方案：所有 acceptor 廣播給所有 learner（訊息量大）、或選一個 distinguished learner 再轉發（單點但省流量）、或 learner 主動輪詢。這是工程權衡，不影響 safety。

- **Cheap Paxos / Fast Paxos**：Fast Paxos 讓 proposer 在無競爭時省掉一個 RTT（客戶端直接發給 acceptor），代價是衝突時要更大的 quorum。Cheap Paxos 用少數「輔助」acceptor 降成本。這些變體都在動 liveness / 效能，**從不動 safety 的核心**（兩多數相交 + P2c）——這再次說明 Paxos 的 safety 骨架有多穩固。

- **Paxos 與 Raft 的 safety 是同一件事**：Raft 的「Log Matching」和「Leader Completeness」（Ch 22）本質上就是 P2c 的另一種包裝——都在保證「已提交的東西不會被後來的 leader 改掉」。學透 Paxos 的 P2c，Raft 的 safety 你會覺得似曾相識。

## 本章重點整理

- Single-decree Paxos = 對**單一個值**達成共識，safety 由數學鐵板釘釘，liveness 交給工程（收斂到單 leader）。
- 三角色：**proposer**（推動）、**acceptor**（投票 + 記憶，唯一有狀態）、**learner**（觀察結果）。決定 = 值被**多數 acceptor 接受**。
- 兩階段：**Prepare/Promise**（用編號 n 佔坑 + 打聽已接受的值）、**Accept/Accepted**（正式提議）。
- 三條核心規則逐步被逼出來：**唯一遞增編號**（建立先來後到）、**拒絕更小的編號**（承諾秩序）、**P2c**（proposer 必須繼承已被接受的最高編號的值）。
- safety 壓在一個小學事實上：**任意兩個多數必相交**。交集的 acceptor 把已決定的值傳給每個後來者，P2c 逼他們繼承 → 永遠只決定一個值。
- 真跑證實：兩個 proposer 競爭，想提 banana 的那個被 P2c 逼著提了 apple，n=1 和 n=2 都決定 apple——**值從頭到尾沒變**。
- 純 Paxos 有活鎖風險（proposer 互搶），這是 Multi-Paxos 引入單一 leader 的動機。

## 自我檢核

- [ ] 不看講義，我能畫出 Paxos 兩階段的四個訊息（Prepare/Promise/Accept/Accepted）並說出每個在幹嘛
- [ ] 我能解釋「acceptor 為何要拒絕更小的編號」防的是什麼腦裂情境
- [ ] 我能解釋 P2c（proposer 繼承已接受的最高編號的值）為什麼是 safety 的核心，並說出它靠「兩多數相交」怎麼運作
- [ ] 我能用真跑輸出說明「為什麼 P1 想提 banana 卻提了 apple」
- [ ] 我能說出 Paxos 為什麼**不保證**終止，以及活鎖是怎麼發生的、怎麼解
- [ ] 我能區分「acceptor 接受一個值」和「值被決定」的差別

Paxos 的 safety 骨架搞定了，但它只決定「一個值」。真實系統要決定的是**一長串值**（一本 log、一連串指令）。下一章我們把 single-decree 擴成 Multi-Paxos，並直面工程界那句名言——「Paxos 難懂、難落地」——到底難在哪。

→ [Ch 19 Multi-Paxos](./19-multi-paxos.md)
