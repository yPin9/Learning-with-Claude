# Ch 5 — Lamport 邏輯時鐘

> **目標**：戒掉實體時鐘後，手上什麼都沒有——那就自己造一個「時鐘」。搞懂 happens-before（→）這個因果偏序，實作 Lamport 邏輯時鐘（logical clock）的三條規則，親手在 dsim 上跑三節點互傳、看因果事件的時戳如何遞增。並想清楚 Lamport clock 的致命限制：它捕捉因果是**必要非充分**——`a→b ⟹ L(a)<L(b)`，但反過來不成立。

> **環境**：Go 1.18.1, WSL2/Linux x86-64。本章有一個在 dsim 上真跑的三節點 Lamport clock 範例。

> 若對「為什麼不能用實體時鐘排序」不熟，回看 [Ch 4](./04-physical-clocks.md)。

## 為什麼需要這個？

上一章的結論很殘酷：實體時鐘會漂移、會被 NTP 往回跳，用它的時間戳排序分散式事件會把因果排反、把新資料蓋成舊資料。那我們到底還能不能給分散式事件排一個可信的順序？

Lamport 在 1978 年那篇奠基論文（就是 Ch 4 延伸閱讀那篇）給的洞見是：**我們其實不需要知道「幾點」，我們需要的是「誰在誰之前」**。而「誰在誰之前」不必靠實體時間——它可以完全從「訊息傳遞」這件事推導出來。

想一件事：如果節點 A 送了一則訊息給 B，那「A 送出」這個事件**一定**發生在「B 收到」之前。這是物理決定的，不管兩台機器的時鐘各自說什麼謊。訊息的因果關係，是分散式系統裡唯一不會說謊的順序來源。

Lamport 的想法就是把這個「訊息隱含的順序」變成一個可以計算的數字。這個數字不是時間，是一個**邏輯計數器**——它不告訴你事件發生在幾點，只保證：**如果 a 在因果上先於 b，那 a 的計數一定小於 b 的計數**。有了這個保證，我們就能用計數排序，而且這個排序永遠不違反因果——這正是實體時鐘辦不到的。

## 先建立直覺：happens-before（→）

在寫任何 code 前，先把「因果」這件事定義死。Lamport 定義了一個關係叫 **happens-before**，寫成 `a → b`，讀作「a 發生在 b 之前」。它只有三條來源：

```
  1. 同一個節點內：a 在 b 之前執行  =>  a → b
     （單機內的程式順序是明確的）

  2. 跨節點的訊息：a 是「送出訊息 m」，b 是「收到 m」  =>  a → b
     （送出一定先於收到，物理決定）

  3. 傳遞性：a → b 且 b → c  =>  a → c
```

關鍵在於，**不是所有事件都有 happens-before 關係**。如果 a 和 b 之間，你**無法**透過上面三條規則從一個推到另一個，那它們就是**並發（concurrent）**，寫成 `a ∥ b`。並發不是「同時發生」，而是「因果上互不相干、誰先誰後沒有客觀答案」。

```
   節點 P:  p1 ──────> p2 ──────> p3
                        │(送m)
                        ▼
   節點 Q:       q1 ──> q2(收m) ──> q3

   因果關係：
     p1 → p2 → p3           （P 內部）
     q1 → q2 → q3           （Q 內部）
     p2 → q2                （送 m → 收 m）
     p2 → q3, p1 → q3 ...   （傳遞性）
   並發：
     p1 ∥ q1   p3 ∥ q2   p3 ∥ q3 ...
        （這些事件之間無法用三條規則連起來 => 並發）
```

happens-before 是一個**偏序（partial order）**：有些事件對能比大小，有些不能（並發的那些）。這跟實體時間是**全序（total order）**——任兩個時間點都能比大小——很不一樣。偏序這件事是本章和下一章的靈魂：**分散式系統裡的因果，本質上就是偏序，硬要塞成全序就是在說謊**。

## Lamport clock 的三條規則

Lamport clock 給每個節點一個整數計數器 `L`，初值 0。三條規則：

```
  規則 1（本地事件）：節點做任何一個本地事件前，先 L = L + 1
  規則 2（送訊息）：送訊息時，先 L = L + 1，然後把 L 的值附在訊息上
  規則 3（收訊息）：收到帶時戳 t 的訊息時，L = max(L, t) + 1
```

規則 3 的 `max` 是整個機制的核心。收到訊息時，我的計數要「至少跟寄件者送出時一樣大，再 +1」——這樣才能保證「收」的計數一定大於「送」的計數，也就是保住 `送 → 收 ⟹ L(送) < L(收)`。

把三條規則作用在剛才那張圖上，你會看到計數沿著每條因果鏈嚴格遞增。這就是 Lamport clock 要達成的**時鐘條件（clock condition）**：

```
   對所有事件： a → b  ⟹  L(a) < L(b)
```

注意這是**單向蘊含**。反過來——`L(a) < L(b)` 是否代表 `a → b`？**不一定。** 兩個並發事件也可能一個計數大、一個計數小（純粹因為它們各自的本地計數碰巧到那），但它們之間根本沒有因果關係。這個「必要非充分」是 Lamport clock 最需要記住的限制，等等程式跑完會看得很清楚。

## 底層機制：在 dsim 上實作三節點 Lamport clock

我們用 Ch 0 的確定性模擬器 `dsim` 來實作。每個節點維護自己的 `clock`，按三條規則更新，把每個事件連同它的 Lamport 時戳記到一個全域 log（只是為了事後印出觀察，節點之間**不共享狀態**，只靠 `net.Send` 溝通）。

> 若對 `dsim` 的 `OnMessage`/`OnTick`/`Send` API 不熟，回看 [Ch 0](./00-environment-setup.md)。

```go
package main

import (
	"fmt"
	"sort"
)

// msg 是節點間傳的東西：帶上寄件者送出時的 Lamport 時戳。
type msg struct {
	ts   int    // 寄件者送出時的 Lamport clock
	kind string
}

// logEntry 記一次「事件發生」，最後統一排序印出（純觀察用，非節點狀態）。
type logEntry struct {
	node NodeID
	lt   int // 該事件的 Lamport 時戳
	desc string
}

type lamportNode struct {
	id     NodeID
	clock  int
	log    *[]logEntry
	script map[int]NodeID // now -> 這個 tick 要送訊息給哪個 peer
}

func (n *lamportNode) OnTick(now int, net *Net) {
	to, ok := n.script[now]
	if !ok {
		return
	}
	// 規則 2：送訊息前 clock++，把 clock 附在訊息上
	n.clock++
	*n.log = append(*n.log, logEntry{n.id, n.clock, fmt.Sprintf("send to N%d", to)})
	net.Send(Message{From: n.id, To: to, Payload: msg{ts: n.clock, kind: "data"}})
}

func (n *lamportNode) OnMessage(m Message, net *Net) {
	in := m.Payload.(msg)
	// 規則 3：clock = max(local, msg.ts) + 1
	if in.ts > n.clock {
		n.clock = in.ts
	}
	n.clock++
	*n.log = append(*n.log, logEntry{n.id, n.clock,
		fmt.Sprintf("recv from N%d (msg ts=%d)", m.From, in.ts)})
}

func main() {
	net := NewNet(42)
	net.SetLatency(2, 4) // 訊息飛 2~4 個邏輯時刻，故意讓收發不同步

	var log []logEntry
	mk := func(id NodeID, script map[int]NodeID) *lamportNode {
		return &lamportNode{id: id, clock: 0, log: &log, script: script}
	}

	// 三節點互傳，腳本刻意造出因果鏈與並發：
	n0 := mk(0, map[int]NodeID{1: 1, 8: 2}) // N0 在 t=1 送 N1、t=8 送 N2
	n1 := mk(1, map[int]NodeID{6: 2})        // N1 在 t=6 送 N2
	n2 := mk(2, map[int]NodeID{10: 0})       // N2 在 t=10 送 N0
	net.Add(0, n0); net.Add(1, n1); net.Add(2, n2)

	net.Run(20)

	// 全域旁觀者視角：把所有事件按 (Lamport ts, node) 排序 -> 得一個全序。
	sort.Slice(log, func(i, j int) bool {
		if log[i].lt != log[j].lt {
			return log[i].lt < log[j].lt
		}
		return log[i].node < log[j].node // 用 node id 做 tiebreak
	})

	fmt.Println("Lamport ts | node | event")
	fmt.Println("-----------+------+-------------------------")
	for _, e := range log {
		fmt.Printf("   %2d      |  N%d  | %s\n", e.lt, e.node, e.desc)
	}
	fmt.Printf("\nfinal clocks: N0=%d N1=%d N2=%d\n", n0.clock, n1.clock, n2.clock)
}
```

真跑（WSL, Go 1.18.1，seed=42）：

```
$ go run .
Lamport ts | node | event
-----------+------+-------------------------
    1      |  N0  | send to N1
    2      |  N0  | send to N2
    2      |  N1  | recv from N0 (msg ts=1)
    3      |  N1  | send to N2
    4      |  N2  | recv from N1 (msg ts=3)
    5      |  N2  | send to N0
    6      |  N0  | recv from N2 (msg ts=5)
    6      |  N2  | recv from N0 (msg ts=2)

final clocks: N0=6 N1=3 N2=6
```

逐行讀出因果，這是全章的重點：

- `send to N1 (ts=1)` → `recv from N0 (ts=2)`：送出 ts=1，收到 ts=2。**送 < 收，因果保住**。收方 max(0, 1)+1 = 2。
- `send to N2 (ts=3)` → `recv from N1 (ts=4)`：N1 收到 N0 的訊息後 clock 變 2，t=6 送 N2 時 clock 變 3；N2 收到後 max(0,3)+1 = 4。整條因果鏈 `N0 送(1) → N1 收(2) → N1 送(3) → N2 收(4)` 時戳嚴格遞增。
- `send to N2 (ts=5)` → `recv from N2 (ts=6)`：N2 送給 N0，N0 收到後 max(2,5)+1 = 6。

再看那個「必要非充分」的活證據——**時戳相同的兩個事件（ts=2）**：`N0 send to N2` 和 `N1 recv from N0`。這兩個事件之間**沒有**因果關係（N0 在 t=2 送給 N2，跟 N1 收到 N0 早先那則訊息，是兩件並發的事），但它們的 Lamport 時戳都是 2。同理 ts=6 有兩個事件。**Lamport clock 給不出「這兩個是並發」這個資訊**——你只知道它們時戳相等，但時戳相等既可能是並發、也可能只是碰巧。這正是下一章 vector clock 要補的洞。

## 故意弄壞：拿掉收訊息的 +1，看因果怎麼崩

規則 3 那個 `+1` 常被當成可有可無的細節。我們把它拿掉——收訊息改成只 `clock = max(clock, ts)`，不再 +1——重跑同一個腳本，親眼看因果條件破在哪。

```go
func (n *lamportNode) OnMessage(m Message, net *Net) {
	in := m.Payload.(msg)
	// BROKEN: 故意拿掉 +1，只 max
	if in.ts > n.clock {
		n.clock = in.ts
	}
	// n.clock++ 被移除了
	...
}
```

真跑（WSL, Go 1.18.1，同 seed=42）：

```
$ go run .
Lamport ts | node | event
-----------+------+-------------------------
    1      |  N0  | send to N1
    1      |  N1  | recv from N0 (msg ts=1)     ← 送(1) 和 收(1) 時戳相等!
    2      |  N0  | send to N2
    2      |  N1  | send to N2
    ...
```

問題一眼就看到：`N0 send to N1` 時戳 1、`N1 recv from N0` 時戳也是 1。這則訊息的「送出」和「收到」是明確的 happens-before（`send → recv`），時鐘條件要求 `L(send) < L(recv)`，但拿掉 +1 後變成 `L(send) = L(recv)`——**嚴格不等式破了**。後果：任何靠「時戳更小 = 更早」來排序的邏輯，都會把這對因果事件判成「同時」，於是可能把「收」排在「送」前面，因果順序被違反。

這就是那個 +1 在守的東西：它保證「收」的計數**嚴格大於**「送」，讓因果鏈上每一步都嚴格遞增，而不只是不減。一個看似多餘的 +1，是整個時鐘條件成立的關鍵。這也是本課「故意把它弄壞才看得懂它在防什麼」的精神——回看 [Ch 0](./00-environment-setup.md) 那個把 `reachable` 檢查移掉看分區洩漏的實驗，同一個學法。

## 用 node id 做 tiebreak 得到全序

Lamport clock 給的是偏序，但很多場景（例如「所有節點要以相同順序處理請求」）需要一個**全序**——任兩個事件都要能明確比大小。

作法很簡單：當兩個事件的 Lamport 時戳相等時，用一個固定的 tiebreaker 打破平手，最自然的就是 **node id**。定義：

```
   (L(a), id(a)) < (L(b), id(b))
   iff  L(a) < L(b)  或  (L(a) == L(b) 且 id(a) < id(b))
```

這就是上面程式碼 `sort.Slice` 裡那行 `return log[i].node < log[j].node` 在做的事——時戳相等時比 node id。這樣任兩個事件都能比，得到一個**全序**。

要小心的是：這個全序**尊重因果但不等於因果**。它保證「若 `a → b` 則 a 在全序裡排 b 前面」（因為 `a→b ⟹ L(a)<L(b)`，時戳就分出勝負了，輪不到 tiebreak）。但它會**強行給並發事件也排個順序**——ts=2 的兩個並發事件，全序硬說 N0 那個在前（因為 node id 0 < 1）。這個「硬排」的順序是任意的，但**所有節點都用同一套規則，所以會排出一致的結果**。這個「一致但任意」的全序，正是 Ch 7 全序廣播和複製狀態機的基礎——只要每台機器對同一批事件排出相同的順序，它們就能保持一致，不管那個順序在因果上是不是唯一正解。

## 對比與取捨

| | 實體時鐘（Ch 4） | Lamport clock | Vector clock（Ch 6） |
|---|---|---|---|
| 捕捉因果 `a→b ⟹ L(a)<L(b)` | 不保證（skew） | **保證** | 保證 |
| 反推 `L(a)<L(b) ⟹ a→b` | 不成立 | **不成立**（必要非充分） | 部分可（能判並發） |
| 能偵測並發 `a∥b` | 不能 | **不能** | **能** |
| 每節點狀態大小 | O(1) | **O(1)**（一個整數） | O(N)（一個向量） |
| 能給全序 | 能（但違反因果） | 能（+tiebreak，尊重因果） | 能 |

Lamport clock 的甜蜜點：**用一個整數的成本，換到「排序永遠不違反因果」**。它的天花板：**判不出並發**。如果你的系統需要偵測「這兩個寫入是不是衝突的並發寫入」（例如多主複製的衝突偵測），Lamport clock 不夠，得升級到 vector clock——代價是狀態從 O(1) 變 O(N)。這個取捨貫穿下一章。

## 踩雷集錦

1. **「`L(a) < L(b)` 所以 a 因果上先於 b」→ 大錯**。這是把必要條件當充分條件。Lamport clock 只保證 `a→b ⟹ L(a)<L(b)`，反推不成立。ts=2 的兩個並發事件就是反例。要判因果或並發，你需要 vector clock。**任何「時戳小的先發生」的推論都是 bug 溫床**。

2. **「收訊息時忘了 +1，直接 `L = max(L, t)`」→ 因果會壞在邊界**。少了那個 +1，「收」事件的計數可能等於「送」事件的計數，於是 `L(送) < L(收)` 變成 `L(送) ≤ L(收)`，時鐘條件的嚴格不等式破了。規則 3 的 +1 不是可省的，它保證收嚴格大於送。

3. **「本地事件不用動 clock，只有收發訊息才動」→ 漏掉本地因果**。規則 1 說**任何**本地事件前都要 +1。如果你只在收發訊息時動 clock，同一節點內兩個沒有訊息參與的事件會拿到相同時戳，它們之間的本地順序（明明是 happens-before）就丟了。實務上常見的偷懶是只給「有意義的」事件編號，這會讓因果鏈斷裂。

4. **「Lamport clock 能拿來當實體時間用」→ 它不是時間**。時戳的數值大小跟真實經過的時間毫無關係——一個節點很忙、事件多，時戳衝很快；另一個節點閒著，時戳很小。你不能從「時戳差 100」推出「過了 100 秒」或任何時長。它只在「比大小」這件事上有意義。

5. **「tiebreak 用什麼都行」→ 必須是所有節點一致且確定的**。如果 A 用「時戳相等時比 node id」、B 用「比事件內容的雜湊」，兩者對同一批並發事件會排出不同的全序，一致性就毀了。tiebreak 必須是全域約定、確定的規則（node id 是最常見選擇，因為它天然唯一）。

## 進階：再往深一層

- **Lamport clock 給不出因果的「反向」資訊**：我們證明了 `a→b ⟹ L(a)<L(b)`，但拿到兩個時戳，你分不清「a 真的因果先於 b」還是「a、b 並發只是時戳碰巧一大一小」。這個資訊缺口不是實作問題，是 Lamport clock 的**本質極限**——單一整數壓不下 N 個節點各自的進度。要補這個洞，唯一的辦法是為每個節點各留一個計數，那就是 vector clock。這也解釋了為什麼 vector clock 的狀態非得是 O(N)：因果的完整資訊本來就需要 N 個維度。

- **全序廣播的伏筆**：本章的「Lamport clock + node id tiebreak = 全序」看起來很美，但它有個致命前提——要決定某個事件的全序位置，你得**確定不會再有時戳更小的事件冒出來**。在真實系統裡這需要「收齊所有節點的訊息」或「等到某個時戳以下都封閉」，這正是 Ch 7 全序廣播為什麼難、為什麼跟共識等價的核心。Lamport 原論文用這套做了一個互斥鎖演算法，但它要求「收到所有節點的 ack」才能推進——不容錯（一個節點掛了就卡住）。

- **HLC（Hybrid Logical Clock）再訪**：Ch 4 提過的 HLC，本質是「Lamport clock 但把 max 的對象換成 `max(本地 wall clock, 收到的 HLC, 本地 HLC)`」。它保住 Lamport 的因果性質（`a→b ⟹ HLC(a)<HLC(b)`），又讓時戳的高位大致對得上真實時間，方便人類 debug 和跟外部系統對照。讀完下一章再看 HLC 論文，會發現它就是 Lamport 規則的一個工程加強版。

## 本章重點整理

- 實體時鐘不可信，那就自己造：**邏輯時鐘不問「幾點」，只問「誰在誰之前」**，靠訊息傳遞這個唯一不說謊的順序來源。
- **happens-before（→）** 由三條規則定義（本地順序、送→收、傳遞性），是一個**偏序**——並發事件之間沒有先後。
- Lamport clock 三規則：本地事件 +1、送訊息 +1 並附時戳、收訊息 `max(本地, 時戳)+1`。達成**時鐘條件** `a→b ⟹ L(a)<L(b)`。
- 這是**必要非充分**：`L(a)<L(b)` 推不出 `a→b`。**Lamport clock 判不出並發**——程式裡 ts 相等的兩個事件既可能並發也可能碰巧，你分不出來。
- 加 **node id tiebreak** 可從偏序得到**全序**：尊重因果、對並發事件強行排序但所有節點一致。這是 Ch 7 全序廣播和複製狀態機的基礎。

## 自我檢核

- [ ] 不看筆記，我能寫出 happens-before 的三條規則，並說出「並發」的精確定義
- [ ] 我能寫出 Lamport clock 的三條更新規則，特別是收訊息那條為什麼要 `max` 再 `+1`
- [ ] 我能指出程式輸出裡「時戳相等的並發事件」，並解釋為什麼 Lamport clock 判不出它們並發
- [ ] 我能解釋「Lamport clock + tiebreak 得到的全序」為什麼尊重因果、又為什麼對並發事件的排序是任意的
- [ ] 我能反駁「時戳小的事件一定先發生」這個常見錯誤

## 延伸閱讀

### 原始論文

- **[Time, Clocks, and the Ordering of Events in a Distributed System](https://lamport.azurewebsites.net/pubs/time-clocks.pdf)** — Leslie Lamport, CACM（1978）
  - **讀哪裡**：全篇不長，Section 2「The Partial Ordering」定義 happens-before，Section 3「Logical Clocks」給三規則與時鐘條件，Section 4 用它做互斥鎖
  - **學什麼**：本章所有東西的原始出處；Lamport 的論證極其乾淨，值得逐行讀
  - **前提**：讀懂本章的偏序直覺即可；互斥鎖那節可略讀，重點在前三節

### 書 / 課程

- **《Designing Data-Intensive Applications》第 9 章「Consistency and Consensus」的 "Ordering and Causality" 一節** — Martin Kleppmann
  - **讀哪裡**：Lamport timestamps 與 "Timestamp ordering is not sufficient" 兩小節
  - **學什麼**：從工程角度講「為什麼 Lamport clock 判不出並發、什麼場景會咬你」，跟本章踩雷 1 直接呼應

- **[Martin Kleppmann 的 Distributed Systems 課程 Lecture 3（Logical time）](https://www.cl.cam.ac.uk/teaching/2021/ConcDisSys/dist-sys-notes.pdf)**（劍橋講義）
  - **讀哪裡**：Logical clocks 那一講的 Lamport clocks 與 causality 小節
  - **學什麼**：講義用清楚的圖解釋 happens-before 與 Lamport clock，補足本章 ASCII 圖；同一份講義下一講就是 vector clock，可直接接 Ch 6
  - **前提**：本章程度即可

Lamport clock 的天花板是「判不出並發」。下一章我們給每個節點一個向量，換到能同時判定因果與並發——代價是狀態從一個整數變成一個 O(N) 的向量。

→ [Ch 6 Vector Clock：因果偵測](./06-vector-clocks.md)
