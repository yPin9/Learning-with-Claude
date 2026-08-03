# Ch 6 — Vector Clock：因果偵測

> **目標**：補上 Lamport clock 的致命洞——判不出並發。搞懂 vector clock（向量時鐘）怎麼用「每節點一個計數」同時捕捉因果與並發：能明確判定 `a→b`、`b→a`、還是 `a∥b`。親手在 dsim 上實作、構造一組事件、讓程式自己判定哪些有因果序、哪些並發。看版本向量（version vector）怎麼在 Dynamo 偵測寫衝突。踩雷：VC 大小隨節點數線性成長。

> **環境**：Go 1.18.1, WSL2/Linux x86-64。本章有一個在 dsim 上真跑、程式自動判定因果/並發的範例。

> 若對 happens-before 與 Lamport clock 的「必要非充分」不熟，回看 [Ch 5](./05-lamport-clocks.md)。

## 為什麼需要這個？

上一章結尾我們卡在一個具體的洞：Lamport clock 跑出來，`ts=2` 有兩個事件、`ts=6` 有兩個事件，這些時戳相等的事件之間**可能是並發、也可能只是碰巧**，而 Lamport clock 給不出答案。更糟的是，就算 `L(a) < L(b)`，你也不知道那是「a 真的因果先於 b」還是「a、b 並發但時戳一大一小」。

這個洞在真實系統裡會直接變成資料損毀。想像一個多主複製（multi-leader replication）的 KV store：使用者的手機和筆電同時離線改了同一個購物車，重新連上線後，系統收到兩個版本。這兩個寫入是**並發**的（各自基於同一個舊版本、互不知道對方）——正確的處理是**偵測到衝突、把兩個版本都留下讓應用層合併**（Amazon 購物車的經典作法：兩邊的商品聯集）。但如果你用 Lamport clock 或實體時間戳，你只會看到「一個時戳大、一個時戳小」，於是天真地拿大的蓋掉小的——**默默丟掉一個版本的購物車內容**。

問題的根源：**要偵測並發，你需要知道每個節點各自的進度**，而 Lamport 把這 N 個維度的資訊壓成了一個整數，壓縮過程中「並發」這個資訊就丟了。Vector clock 的想法直白到近乎暴力：**別壓縮，每個節點的進度各留一格**。

## 先建立直覺

Lamport clock 是一個整數；vector clock 是一個**長度 N 的整數陣列**（N = 節點數），每個節點各佔一格。節點 i 的 vector clock `V` 裡：

```
   V[i] = 「我自己」已經歷的事件數（我的進度）
   V[k] = 「我所知道的」節點 k 已經歷的事件數（我看過的 k 的進度）
```

關鍵直覺：**一個 vector clock 就是「這個事件發生時，發生者對整個系統各節點進度的認知快照」**。當節點 i 收到節點 j 的訊息時，它逐格取 max——意思是「把 j 對各節點進度的認知，併進我自己的認知」。這就是因果傳播：j 知道的事，i 收到 j 的訊息後也知道了。

```
   節點 P (id=0):  V=[?,?,?] 裡第 0 格是 P 自己的進度
   節點 Q (id=1):  第 1 格是 Q 自己的進度
   節點 R (id=2):  第 2 格是 R 自己的進度

   P 做一件本地事件:  V[0]++        （只有自己那格動）
   P 送訊息給 Q:      V[0]++, 把整個 V 附在訊息上
   Q 收到:           逐格 V[k]=max(V[k], msg.V[k]), 然後 V[1]++
                     （吸收 P 的全部認知，再記自己這次「收」也是一個事件）
```

有了完整的 N 維進度，比較兩個 vector clock 就能問出 Lamport 問不出的問題。

## 三種關係：因果 vs 並發，怎麼判

給兩個 vector clock `a` 和 `b`，先定義逐格比較：

```
   a ≤ b   iff   對所有 k， a[k] ≤ b[k]      （每一格都不大於）
```

然後三種關係：

```
   a → b（a 因果先於 b）  iff  a ≤ b 且 a ≠ b
        （a 每格都不大於 b，且至少一格嚴格小：b 看過 a 的一切、還多知道一些）

   b → a                 iff  b ≤ a 且 b ≠ a

   a ∥ b（並發）          iff  a ≤ b 不成立 且 b ≤ a 也不成立
        （互相都有對方不知道的進度 => 因果上互不相干）
```

用直覺讀第三條：如果 a 有某格比 b 大（a 知道某節點的進度比 b 多），同時 b 也有某格比 a 大（b 也知道某節點的進度比 a 多），那**兩者互相都有對方不知道的資訊**——它們不可能有因果先後，只能是並發。這正是 Lamport 的單一整數壓不出來的判斷。

```
   例：三節點 (N=3)
     a = [2, 0, 1]
     b = [1, 2, 0]

   a[0]=2 > b[0]=1  => a 有 b 不知道的（N0 的進度）
   b[1]=2 > a[1]=0  => b 有 a 不知道的（N1 的進度）
   => 互有對方不知道的 => a ∥ b（並發!）

   Lamport clock 看這兩個：L(a)=?, L(b)=? 只是兩個整數，判不出並發
```

**這是 vector clock 相對 Lamport clock 唯一但決定性的升級：能判並發**。代價後面談。

## 底層機制：在 dsim 上實作 vector clock

我們在 `dsim` 上實作。用固定 `N=3`（三節點）把 vector clock 寫成 `[3]int`，規則就三條：本地事件自己那格 +1、送訊息附上整個向量、收訊息逐格 max 再自己那格 +1。

> 若對 `dsim` API 不熟，回看 [Ch 0](./00-environment-setup.md)。

```go
package main

import "fmt"

const N = 3 // 三個節點

type VC [N]int

func (a VC) String() string { return fmt.Sprintf("[%d %d %d]", a[0], a[1], a[2]) }

// leq: a <= b（每一分量都成立）
func leq(a, b VC) bool {
	for i := 0; i < N; i++ {
		if a[i] > b[i] {
			return false
		}
	}
	return true
}

// happensBefore: a -> b  iff a<=b 且 a!=b
func happensBefore(a, b VC) bool { return leq(a, b) && a != b }

// concurrent: 互不 <=
func concurrent(a, b VC) bool { return !leq(a, b) && !leq(b, a) }

type vcMsg struct{ vc VC }

type vcEvent struct {
	node NodeID
	vc   VC
	desc string
}

type vcNode struct {
	id     NodeID
	vc     VC
	log    *[]vcEvent
	script map[int]NodeID // now -> 送給誰
}

func (n *vcNode) OnTick(now int, net *Net) {
	to, ok := n.script[now]
	if !ok {
		return
	}
	// 送訊息是本地事件：自己那格 +1，附上整個向量
	n.vc[n.id]++
	*n.log = append(*n.log, vcEvent{n.id, n.vc, fmt.Sprintf("send to N%d", to)})
	net.Send(Message{From: n.id, To: to, Payload: vcMsg{vc: n.vc}})
}

func (n *vcNode) OnMessage(m Message, net *Net) {
	in := m.Payload.(vcMsg)
	// 收訊息：逐格取 max，再自己那格 +1
	for i := 0; i < N; i++ {
		if in.vc[i] > n.vc[i] {
			n.vc[i] = in.vc[i]
		}
	}
	n.vc[n.id]++
	*n.log = append(*n.log, vcEvent{n.id, n.vc, fmt.Sprintf("recv from N%d", m.From)})
}

func main() {
	net := NewNet(7)
	net.SetLatency(2, 5)

	var log []vcEvent
	mk := func(id NodeID, s map[int]NodeID) *vcNode {
		return &vcNode{id: id, log: &log, script: s}
	}

	// 造一組事件：
	//  - N0 送 N1（因果鏈 a），N1 收到後送 N2（延續 a）
	//  - N2 獨立地在早期送 N0（與 a 早段並發）
	n0 := mk(0, map[int]NodeID{1: 1})
	n1 := mk(1, map[int]NodeID{7: 2})
	n2 := mk(2, map[int]NodeID{2: 0})
	net.Add(0, n0); net.Add(1, n1); net.Add(2, n2)
	net.Run(20)

	fmt.Println("== 事件序（依發生的邏輯順序記錄）==")
	for i, e := range log {
		fmt.Printf("e%d: N%d %-14s %s\n", i, e.node, e.desc, e.vc)
	}

	fmt.Println("\n== 兩兩關係判定 ==")
	rel := func(a, b vcEvent, ai, bi int) {
		var r string
		switch {
		case happensBefore(a.vc, b.vc):
			r = fmt.Sprintf("e%d -> e%d (因果先行)", ai, bi)
		case happensBefore(b.vc, a.vc):
			r = fmt.Sprintf("e%d -> e%d (因果先行)", bi, ai)
		case concurrent(a.vc, b.vc):
			r = fmt.Sprintf("e%d || e%d (並發 concurrent)", ai, bi)
		default:
			r = "相等"
		}
		fmt.Printf("e%d %s  vs  e%d %s  =>  %s\n", ai, a.vc, bi, b.vc, r)
	}
	for i := 0; i < len(log); i++ {
		for j := i + 1; j < len(log); j++ {
			rel(log[i], log[j], i, j)
		}
	}
}
```

真跑（WSL, Go 1.18.1，seed=7）：

```
$ go run .
== 事件序（依發生的邏輯順序記錄）==
e0: N0 send to N1     [1 0 0]
e1: N2 send to N0     [0 0 1]
e2: N1 recv from N0   [1 1 0]
e3: N0 recv from N2   [2 0 1]
e4: N1 send to N2     [1 2 0]
e5: N2 recv from N1   [1 2 2]

== 兩兩關係判定 ==
e0 [1 0 0]  vs  e1 [0 0 1]  =>  e0 || e1 (並發 concurrent)
e0 [1 0 0]  vs  e2 [1 1 0]  =>  e0 -> e2 (因果先行)
e0 [1 0 0]  vs  e3 [2 0 1]  =>  e0 -> e3 (因果先行)
e0 [1 0 0]  vs  e4 [1 2 0]  =>  e0 -> e4 (因果先行)
e0 [1 0 0]  vs  e5 [1 2 2]  =>  e0 -> e5 (因果先行)
e1 [0 0 1]  vs  e2 [1 1 0]  =>  e1 || e2 (並發 concurrent)
e1 [0 0 1]  vs  e3 [2 0 1]  =>  e1 -> e3 (因果先行)
e1 [0 0 1]  vs  e4 [1 2 0]  =>  e1 || e4 (並發 concurrent)
e1 [0 0 1]  vs  e5 [1 2 2]  =>  e1 -> e5 (因果先行)
e2 [1 1 0]  vs  e3 [2 0 1]  =>  e2 || e3 (並發 concurrent)
e2 [1 1 0]  vs  e4 [1 2 0]  =>  e2 -> e4 (因果先行)
e2 [1 1 0]  vs  e5 [1 2 2]  =>  e2 -> e5 (因果先行)
e3 [2 0 1]  vs  e4 [1 2 0]  =>  e3 || e4 (並發 concurrent)
e3 [2 0 1]  vs  e5 [1 2 2]  =>  e3 || e5 (並發 concurrent)
e4 [1 2 0]  vs  e5 [1 2 2]  =>  e4 -> e5 (因果先行)
```

這張表就是 vector clock 的全部價值。逐塊讀：

- **因果鏈被完整抓出**：`e0 → e2 → e4 → e5`。N0 送 `[1 0 0]` → N1 收 `[1 1 0]` → N1 送 `[1 2 0]` → N2 收 `[1 2 2]`。每一步 vector 逐格不減、至少一格增，判定 `→`。
- **並發被明確識別**：`e0 [1 0 0] ∥ e1 [0 0 1]`。N0 的第一個事件和 N2 的第一個事件，各自只動了自己那格，互有對方不知道的進度，判定並發。`e2 ∥ e3`、`e3 ∥ e4`、`e3 ∥ e5` 同理——`e3 [2 0 1]` 是 N0 收到 N2 訊息後的狀態（知道 N0=2, N2=1），跟 N1 那條線 `[1 2 0]`/`[1 2 2]` 互不包含，全是並發。

把這張表跟上一章 Lamport clock 的輸出對照：Lamport 只能告訴你「ts=2 有兩個事件」，vector clock 直接告訴你「e0 和 e1 是並發、e0 和 e2 是因果」。**這就是升級的全部意義**——同樣構造一組事件，vector clock 能回答「這兩個到底並不並發」，Lamport 不能。

## 版本向量：Dynamo 怎麼偵測寫衝突

vector clock 最著名的工業應用是 Amazon Dynamo（以及後來的 Riak）用的**版本向量（version vector）**。名字不同、精神一樣：給資料的每個版本掛一個向量，用來偵測「這兩個版本是因果覆蓋、還是並發衝突」。

Dynamo 是一個高可用的 KV store，允許在網路分區時多個副本各自接受寫入（AP，Ch 10 會細講）。當分區癒合、副本互相同步時，同一個 key 可能有多個版本。關鍵問題：**這些版本是同一條因果鏈（新的該覆蓋舊的），還是並發衝突（都得留下）？**

```
   購物車 key = "cart:user42"

   初始:      v0 = {milk}          版本向量 [0 0]  (兩個副本 A, B)
   使用者在 A 加 eggs:  v1 = {milk, eggs}   [1 0]   （基於 v0，A 那格 +1）
   分區! A、B 斷聯
   使用者（另一裝置）在 B 加 flour: v2 = {milk, flour}  [0 1]   （也基於 v0，B 那格 +1）

   分區癒合，A、B 同步，比較 v1 [1 0] 和 v2 [0 1]:
     v1[0]=1 > v2[0]=0  且  v2[1]=1 > v1[1]=0
     => v1 || v2  並發衝突!
     => 不能拿一個蓋另一個，兩個版本都保留（sibling）
     => 交給應用層合併：{milk, eggs, flour}（購物車取聯集）
```

如果沒有版本向量、只用時間戳 LWW，系統會拿時戳大的蓋掉小的，使用者會發現「我加的 flour 不見了」——這就是 Ch 4 講的 LWW 丟資料，vector clock 正是為了避免它。Dynamo 論文明講：**購物車寧可復活已刪除的商品（合併時的副作用），也不要丟掉使用者加的東西**——這是刻意的取捨，偵測到並發後的合併策略由應用層決定，系統只負責「誠實地告訴你這是衝突」。

> Riak 早期直接暴露 vector clock 給客戶端（讀時拿到、寫時帶回），讓客戶端負責因果。後來因為 vector clock 會無限增長（見踩雷），改用 **dotted version vector**，這是版本向量的一個更省空間的變體。

## 對比與取捨

| | Lamport clock | Vector clock |
|---|---|---|
| 狀態大小 | O(1) 一個整數 | **O(N) 一個向量** |
| 訊息附帶開銷 | O(1) | O(N) |
| `a→b ⟹ 時戳關係` | `L(a)<L(b)` | `V(a)<V(b)`（逐格） |
| 反推因果 | **不能** | 能（逐格比較） |
| 偵測並發 `a∥b` | **不能** | **能** |
| 適用 | 只需全序、不需判並發（如 Ch 7 用它排序） | 需偵測衝突/因果（多主複製、Dynamo、因果一致） |

一句話取捨：**vector clock 用 O(N) 的空間，買到「能判並發」這唯一但關鍵的能力**。如果你的系統根本不需要偵測並發衝突（例如單主複製、或只需要一個全序），別用 vector clock，Lamport clock 的 O(1) 更划算。只有在「必須知道兩個事件是不是並發衝突」時，才值得付這個 O(N) 的代價。

## 踩雷集錦

1. **「vector clock 的大小是固定的」→ 它隨節點數線性成長，而且會腐爛**。VC 是 O(N) 的，N 是**曾經存在過**的節點數。在成員固定的系統還好；但在客戶端也各持一格（如早期 Riak）或節點頻繁加入/離開的系統，向量會**無限膨脹**——每個離開的節點都在向量裡留一格屍體。真實系統得靠版本向量修剪（pruning）、dotted version vector、或限制向量長度來對抗。這是 vector clock 最大的工程痛點。

2. **「收訊息時逐格 max 就好，不用自己那格 +1」→ 收事件本身丟了**。跟 Lamport 一樣，收訊息是一個**事件**，逐格 max 之後還要自己那格 +1，否則「收」這個事件沒有被記錄成一個新的因果點，後續判定會錯。程式裡那行 `n.vc[n.id]++` 在 max 迴圈之後，順序不能顛倒也不能省。

3. **「`V(a) < V(b)` 判 `a→b` 時用『某格小』就夠」→ 要『每格都不大於且至少一格嚴格小』**。因果判定是 `a ≤ b 且 a ≠ b`——**每一格**都要 `a[k] ≤ b[k]`。只要有一格 `a[k] > b[k]`，就不是 `a→b`（可能是並發或反向）。常見 bug 是只檢查「發起者那格」而漏掉其他格，會把並發誤判成因果。

4. **「vector clock 能給全序」→ 它給的是偏序，並發事件無法比大小**。這是特性不是 bug——vector clock 的價值就在於它**拒絕**給並發事件強排順序（那正是 Lamport clock 會做的謊）。如果你需要全序（例如 Ch 7），得在 vector clock 判出並發後，額外用 tiebreak（如 node id）強排，但那一步就丟掉了「這是並發」的資訊，要清楚自己在做什麼。

5. **「vector clock 告訴你怎麼合併衝突」→ 它只偵測衝突，不解決衝突**。VC 判出「這兩個版本並發」之後，怎麼合併（取聯集？讓使用者選？用 CRDT？）是**應用層**的事，vector clock 不管。Dynamo 把合併丟給應用層（購物車取聯集）；CRDT（Ch 14）則設計成合併總有確定結果。別指望 vector clock 幫你決定保留哪個版本。

## 進階：再往深一層

- **為什麼 O(N) 是理論下界**：能偵測任意 N 節點系統並發關係的機制，其狀態**至少**需要 O(N) 空間——這是可以證明的（Charron-Bost 1991）。所以 vector clock 不是「實作偷懶才 O(N)」，而是「捕捉完整因果資訊本來就需要 N 個維度」。任何宣稱能用更少空間完整偵測並發的方案，一定是在某處犧牲了精確度（例如 Lamport 就是犧牲了並發偵測換 O(1)）。

- **dotted version vector（DVV）**：Riak 用來取代裸 vector clock 的方案。裸 VC 在「同一個 key 被同一個節點連續寫多次、又有並發寫入」的場景下，會出現「假並發」或向量爆炸。DVV 在版本向量外加一個「dot」（記錄這個版本是哪個節點的第幾次寫），能更精確地表示「一個版本」而非「一個節點的整體進度」，避免不必要的 sibling 爆炸。想深入 Dynamo 系統的衝突處理，這是必讀。

- **因果一致性（causal consistency）的基石**：vector clock 不只用來偵測衝突，它是實作**因果一致性**的核心工具——保證「如果一個寫入因果依賴另一個，所有節點都以尊重這個依賴的順序看到它們」。這正是本 Part 練習 A 要做的**因果遞交（causal delivery）**：亂序到達的訊息，用 vector clock 判斷因果前置是否到齊，沒到齊就緩衝。COPS、Bayou 這些系統都建在這個機制上。

## 本章重點整理

- Lamport clock 判不出並發，因為它把 N 維進度壓成一個整數。**vector clock 不壓縮：每節點各留一格**（O(N)）。
- 規則三條：本地事件自己那格 +1、送訊息附整個向量、收訊息逐格 max 再自己那格 +1。
- 三種關係靠逐格比較判定：`a→b`（`a≤b 且 a≠b`）、`b→a`、`a∥b`（互不 `≤`）。**能明確判並發**是相對 Lamport 唯一但決定性的升級。
- 真跑輸出證明了這點：同一組事件，vector clock 明確分出 `e0→e2→e4→e5` 的因果鏈和 `e0∥e1`、`e2∥e3` 的並發，Lamport clock 做不到。
- **版本向量**是 Dynamo/Riak 偵測寫衝突的工具：並發寫入判為 sibling、兩個版本都留、交應用層合併（購物車取聯集），避免 LWW 丟資料。
- 代價：**O(N) 且會腐爛**——節點/客戶端各佔一格，向量隨成員增長甚至無限膨脹，真實系統要靠 DVV/pruning 對抗。

## 自我檢核

- [ ] 不看筆記，我能寫出 vector clock 的三條更新規則，特別是收訊息那條的「逐格 max 再自己 +1」
- [ ] 我能寫出 `a→b`、`a∥b` 的逐格判定條件，並手算 `[2 0 1]` 和 `[1 2 0]` 是什麼關係
- [ ] 我能解釋為什麼 vector clock 能判並發而 Lamport clock 不能，關鍵差在哪
- [ ] 我能講出 Dynamo 用版本向量偵測購物車衝突的完整流程，以及它跟 LWW 的差別
- [ ] 我能說出 vector clock 的兩個主要代價（O(N) 空間、會腐爛），以及真實系統怎麼緩解

## 延伸閱讀

### 原始論文

- **[Dynamo: Amazon's Highly Available Key-value Store](https://www.allthingsdistributed.com/files/amazon-dynamo-sosp2007.pdf)** — DeCandia et al., SOSP（2007）
  - **讀哪裡**：Section 4.4「Data Versioning」——版本向量偵測衝突、sibling、購物車合併的原始出處
  - **學什麼**：vector clock 在真實高可用系統裡怎麼用來「偵測而非解決」衝突，以及那個「寧可復活已刪商品也不丟寫入」的取捨
  - **前提**：讀懂本章的並發判定即可；一致性哈希、gossip 等其他部分留到 Ch 28/29

- **[Detection of Mutual Inconsistency in Distributed Systems](https://ieeexplore.ieee.org/document/1702394)** — Parker et al.（1983）
  - **這是什麼**：version vector 的原始出處（比 Dynamo 早 24 年），提出用向量偵測副本間的不一致
  - **讀哪裡**：向量比較與衝突偵測的定義部分
  - **前提**：本章程度即可，是理解 version vector「為什麼這樣設計」的源頭

### 文章 / 講義

- **[Why Vector Clocks Are Easy / Why Vector Clocks Are Hard](https://riak.com/posts/technical/why-vector-clocks-are-hard/)** — Basho（Riak 團隊）
  - **讀哪裡**：兩篇對照著讀，"Hard" 那篇講裸 vector clock 的膨脹問題與為什麼要 dotted version vector
  - **學什麼**：直接對應本章踩雷 1（VC 腐爛）與進階段的 DVV，是「工業界踩過的坑」第一手記錄

- **《Designing Data-Intensive Applications》第 5 章 "Detecting Concurrent Writes" 一節** — Martin Kleppmann
  - **讀哪裡**：version vectors 與 merging concurrently written values 兩小節
  - **學什麼**：用購物車例子把「偵測並發 → 合併 sibling」講得極清楚，跟本章 Dynamo 段落互補
  - **前提**：本章程度即可

vector clock 讓我們能判因果與並發。但「判斷順序」還不等於「讓所有節點以相同順序遞交訊息」——後者是複製狀態機的核心原語，也是共識的門檻。下一章我們談全序廣播，並揭露它跟共識等價這個關鍵事實。

→ [Ch 7 全序廣播](./07-total-order-broadcast.md)
