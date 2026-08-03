# Ch 13 — Quorum 複製

> **目標**：走到複製光譜的另一端——**無主（leaderless）複製**，也就是 Dynamo 風格的 quorum 複製。核心是一條看似簡單卻極深的不等式 **R + W > N**：只要「讀取聯絡的節點數」加「寫入聯絡的節點數」大於總副本數，讀寫集合必然相交，讀取就一定碰得到最新的寫入。我們要把這條不等式的直覺證明清楚，講 W 與 R 的取捨、sloppy quorum + hinted handoff 在分區時怎麼放寬、read repair 與 anti-entropy 怎麼收斂落後的副本。並在 `dsim` 上跑一個 N=3 的 quorum KV，親眼看 R+W>N 讀到最新、R+W≤N 讀到舊值。

> **環境**：Go 1.18.1, WSL2 / Linux x86-64。模擬跑在 Ch 0 的 `dsim` 上。

上一章主從複製把所有寫入收斂到單一 primary，簡單好推理，但 primary 是單點、故障切換會腦裂。這一章我們問一個叛逆的問題：**能不能乾脆不要 primary？** 讓每個副本都平等、都能收讀寫，用一套投票規則保證一致。這就是 Amazon Dynamo（2007）帶紅的無主複製，Cassandra、Riak、Voldemort 都是它的後裔。代價是你得親手處理「多個副本各說各話」的衝突，但換來的是**沒有單點、分區時仍能讀寫**的高可用。

## 為什麼需要這個？

主從複製的兩個痛，在無主複製裡直接消失：

1. **沒有單點故障**：沒有 primary，就沒有「primary 死了全系統不能寫」這回事。任何節點都能收寫入。
2. **沒有故障切換 / 腦裂**：不需要選 primary，就沒有「誤判死亡 → 扶正 → 兩個 primary」的腦裂災難。節點來來去去，系統照跑。

但天下沒有白吃的午餐。既然每個副本都能獨立收寫入，那**兩個客戶端同時往不同副本寫同一個 key 不同值**，怎麼辦？副本之間會分歧。無主複製不逃避這個分歧，它用兩招應對：

- **寫的時候多寫幾份**（寫給 W 個副本），**讀的時候多讀幾份**（讀 R 個副本），用 **R + W > N** 保證讀寫必相交，讀得到最新。
- **讀到分歧的值時，當場修復**（read repair）+ 背景持續對帳（anti-entropy）。

Dynamo 的設計哲學是「**可用性壓倒一切**」——購物車服務寧可讓兩次「加入購物車」暫時看到不一致（事後合併），也不要因為某個副本不可達就拒絕使用者操作。這是 Ch 10 CAP 裡選 AP 的典型，而 quorum 是它在複製層的具體機制。理解 quorum，你就理解了半個現代 NoSQL 的骨架。

## 先建立直覺：R+W>N 為什麼保證讀到最新

先把最核心的那條不等式用抽屜原理（鴿籠原理）講透。這是整章的靈魂。

設定：N 個副本。一次**寫入**成功，代表它已經寫進了 **W** 個副本（拿到 W 個 ack 才算成功）。一次**讀取**會去問 **R** 個副本，收集它們的值，挑版本最新的回傳。

問題：讀取這 R 個副本，保證至少碰到一個「有最新寫入」的副本嗎？

```
   N = 5 個副本：  [0] [1] [2] [3] [4]

   一次寫入寫了 W=3 個（打 W 的）：
                   [W] [W] [W] [ ] [ ]
                    0   1   2   3   4

   一次讀取讀了 R=3 個（打 R 的）：
                   [ ] [ ] [R] [R] [R]
                    0   1   2   3   4
                            ↑
                        副本 2 同時被寫過、也被讀到！
                        R 讀到它 → 看到最新版本 → 挑出來回傳 ✓
```

為什麼一定相交？**寫集合有 W 個成員，讀集合有 R 個成員，兩者都是從同一個 N 元素池子裡選的。如果 W + R > N，這兩個集合不可能不相交**——因為若它們完全不重疊，那總共要 W + R 個「不同」的節點，但池子只有 N 個，W + R > N 就裝不下，鴿籠原理逼出至少一個節點同時屬於兩邊。

那個交集裡的節點，**既存過最新的寫、又被這次讀讀到**。它會把最新版本交給讀取者，讀取者挑版本號最大的回傳——於是**讀到最新**。這就是 R + W > N 的全部魔法：一個純組合數學的保證，不需要任何節點間的協調投票，只要湊夠數量。

反過來，**R + W ≤ N 時，讀寫集合可能完全不相交**，讀取就可能全部落在「沒被最新寫入碰過」的副本上，讀到舊值。後面的 demo 會把這個直接跑出來。

> 注意這裡的「最新」怎麼判定：讀取者收到 R 個值，得有辦法比大小挑出最新的。最簡單用**版本號 / 時間戳**（誰版本大誰新），更嚴謹用 **vector clock**（Ch 6）來偵測「真並行的衝突」而非誤判。用什麼決定「最新」是 Ch 14 的主題，本章先用單調遞增的版本號把 quorum 機制講清楚。

## W 與 R 的取捨

R + W > N 只是約束，具體 R、W 選多少是可調的旋鈕，直接決定讀寫的性格。常見設定（以 N=3 為例，quorum 要求 W + R ≥ 4）：

```
   N=3，幾種 (W,R) 選法：

   W=2, R=2  ← 最常見的「均衡 quorum」。讀寫都等 2 個，都能容忍 1 個副本掛
   W=3, R=1  ← 寫得慢（等全部）、讀得快（讀 1 個就好）。寫少讀多的場景
   W=1, R=3  ← 寫得快（1 個 ack 就回）、讀得慢（讀全部）。寫多讀少
   W=1, R=1  ← R+W=2 ≤ 3，"不是" quorum！不保證讀到最新（但最快、最可用）
```

取捨的軸線：

- **W 大 → 寫更耐久、寫延遲高、寫可用性低**。W 越大，寫入落地的副本越多，故障切換/讀取更容易碰到最新；但要等更多 ack，慢；而且要湊到 W 個存活副本才寫得成，分區時更容易寫不動。
- **R 大 → 讀更容易碰到最新、讀延遲高、讀可用性低**。同理。
- **W + R 剛好 > N**（如 W=R=2, N=3）通常是甜蜜點：讀寫都能容忍 `N - max(R,W)` 個副本故障，延遲也不至於太糟。
- **W + R ≤ N**（如 W=R=1）：放棄「讀到最新」的保證，換極致的低延遲與高可用。這在能容忍 stale read 的場景（社群計數、快取）划算。

還有一個常被忽略的耐久性考量：**W=1 意味著寫入只在一個副本上就回 ok 了**——這跟上一章非同步主從的資料遺失視窗是同一個病：那唯一存了的副本一崩潰，寫入就沒了。所以就算讀路徑不在乎最新，寫路徑通常也不會設 W=1，至少 W=2 求個耐久。

## 底層機制：dsim 上的 N=3 quorum KV

在 `dsim` 上把 quorum 跑起來。三個副本（node 0/1/2）+ 一個 coordinator（node 50）。coordinator 負責 fan-out 寫入、收集 ack、fan-out 讀取、挑最新版本。副本本身很笨：收到寫就存（版本大的覆蓋版本小的），收到讀就回自己那份。

訊息與副本：

```go
type versioned struct { ver int; val string }

type replica struct {
    id   NodeID
    data map[string]versioned
}

func (s *replica) OnMessage(m Message, net *Net) {
    km := m.Payload.(kvMsg)
    switch km.kind {
    case putReq:
        cur := s.data[km.key]
        if km.vv.ver > cur.ver { // 版本大的贏（本章用版本號決定新舊）
            s.data[km.key] = km.vv
        }
        net.Send(Message{From: s.id, To: m.From,
            Payload: kvMsg{kind: putAck, key: km.key, rid: km.rid}})
    case getReq:
        net.Send(Message{From: s.id, To: m.From,
            Payload: kvMsg{kind: getResp, key: km.key, vv: s.data[km.key], rid: km.rid}})
    }
}
```

coordinator 是 quorum 邏輯所在：`putAck` 收滿 W 個算成功，`getResp` 收滿 R 個就在裡面挑版本最大的回傳：

```go
func (c *coordinator) OnMessage(m Message, net *Net) {
    km := m.Payload.(kvMsg)
    switch km.kind {
    case putAck:
        c.putAcks[km.rid]++
        if c.putAcks[km.rid] == c.W && !c.done[km.rid] { // 收滿 W 個 ack
            c.done[km.rid] = true
            fmt.Printf("PUT rid=%d reached W=%d acks -> success\n", km.rid, c.W)
        }
    case getResp:
        c.getResps[km.rid] = append(c.getResps[km.rid], km.vv)
        if len(c.getResps[km.rid]) == c.R && !c.done[km.rid] { // 收滿 R 個回應
            best := versioned{ver: -1}
            for _, v := range c.getResps[km.rid] { // 挑版本最大的
                if v.ver > best.ver { best = v }
            }
            fmt.Printf("GET rid=%d -> picked ver=%d val=%q\n", km.rid, best.ver, best.val)
        }
    }
}
```

兩個對照情境。情境 A：**W=2, R=2, N=3（R+W=4>3）**。先寫 ver1 到 quorum {0,1}，再寫 ver2 到 quorum {1,2}，然後從 quorum {0,2} 讀。讀集合 {0,2} 和最後一次寫集合 {1,2} 相交於副本 2——所以一定讀到 ver2：

```
=== A. R+W>N : W=2,R=2,N=3  (2+2>3) reads see latest ===
[t=2] PUT rid=1 reached W=2 acks -> success
[t=8] PUT rid=2 reached W=2 acks -> success
[t=14] GET rid=3 collected R=2 responses [(v1,"old") (v2,"new")] -> picked ver=2 val="new"
```

讀取收到兩個回應 `(v1,"old")` 和 `(v2,"new")`——副本 0 還停在舊的 ver1（它沒被第二次寫碰到），副本 2 有新的 ver2。coordinator 挑版本大的，回傳 `ver2 "new"`。**相交保證生效，讀到最新。** 注意這裡副本 0 落後了（它有舊值），這正是 read repair 該上場的地方（下一節）。

情境 B：**W=1, R=1, N=3（R+W=2≤3）**。寫入只寫到副本 {0}（W=1），讀取只讀副本 {2}（R=1）。讀寫集合 {0} 和 {2} **完全不相交**：

```
=== B. R+W<=N : W=1,R=1,N=3  (1+1<=3) read may be stale ===
[t=2] PUT rid=1 reached W=1 acks -> success
[t=8] PUT rid=2 reached W=1 acks -> success
[t=14] GET rid=3 collected R=1 responses [(v0,"")] -> picked ver=0 val=""
```

讀取只碰到副本 2，而副本 2 **從沒被任何寫入碰過**（寫都去了副本 0），所以回傳 `ver=0 ""`——**讀到了不存在的初始值，徹底 stale**。這就是 R + W ≤ N 的代價：沒有相交保證，讀取可能整個錯過最新寫入。同一份程式碼，只是 W、R 從 2 調成 1，一致性保證就沒了——這讓你親眼看到那條不等式不是裝飾。

## Read Repair 與 Anti-Entropy：把落後的副本追上

情境 A 暴露了一個問題：quorum 保證「讀到」最新，但**沒保證所有副本都有最新**——副本 0 還停在舊值。如果放著不管，副本會越漂越遠。無主複製靠兩個機制持續收斂：

**1. Read repair（讀修復）**：讀取時順手修。coordinator 收集 R 個回應時，若發現它們版本不一致（像情境 A 的 `v1` vs `v2`），它知道哪個最新，就**把最新值寫回那些落後的副本**。修復搭在讀取路徑上，不額外花一趟主動掃描——讀得越頻繁的資料，修得越勤。缺點：沒人讀的冷資料永遠不會被 read repair 碰到。

```
   讀取發現分歧：
   副本0: v1 "old"  ┐
   副本1: (沒讀)     ├─ coordinator 看到 v1 vs v2，知道 v2 最新
   副本2: v2 "new"  ┘
        │
        └─> 順手把 v2 "new" 寫回副本 0  ← read repair
            下次任何人讀副本 0 都對了
```

**2. Anti-entropy（反熵）**：背景主動對帳，補 read repair 的缺（冷資料）。副本之間週期性地互相比對「我有哪些 key 的哪些版本」，找出差異、補齊。難點是**怎麼高效比對兩個可能有幾百萬 key 的副本，而不用把所有 key 傳一遍**。答案是 **Merkle tree（默克爾樹）**：

```
   把 key 空間切段，每段算 hash，再把 hash 兩兩往上合併成一棵樹：

              root hash
             /         \
         h(左半)      h(右半)
         /    \        /    \
       h(A)  h(B)   h(C)   h(D)     ← 葉節點 = 一段 key 範圍的 hash

   兩副本比對：先比 root hash。
     一樣 → 整棵樹一致，收工，零傳輸。
     不一樣 → 往下比子節點，只有 hash 不同的分支才繼續下探。
   最後只需傳輸「真正有差異」的那幾段 key，其餘全靠 hash 比對跳過。
```

Merkle tree 讓「找出兩個大副本的差異」從 O(key 數) 降到 O(差異數 × log)。Dynamo、Cassandra、Riak 的 anti-entropy 全靠它。這也是為什麼 Git、比特幣、IPFS 這些要「高效比對大量資料是否一致」的系統都用 Merkle tree——同一個工具的不同應用。

read repair（讀路徑、被動、修熱資料）+ anti-entropy（背景、主動、修冷資料）兩者互補，共同保證「停止寫入後最終所有副本收斂」——這正是 Ch 9 的 eventual consistency 的實現手段。

## Sloppy Quorum 與 Hinted Handoff：分區時放寬

到目前為止的 quorum 叫 **strict quorum（嚴格 quorum）**：W、R 必須是「N 個指定副本裡」的成員。但分區來了：如果一次寫入的 N 個「正牌」副本裡，有超過 `N - W` 個不可達，這次寫入就湊不到 W 個 ack，**寫不成**。對「可用性壓倒一切」的 Dynamo 來說，這不能接受。

**Sloppy quorum（鬆散 quorum）** 的放寬：湊不到 W 個正牌副本時，**允許把寫入暫存到「非正牌」的其他節點上**，只要湊夠 W 個節點（不管是不是正牌）就算寫成功。這保住了寫入可用性——只要系統裡有 W 個活著的節點，就寫得進去。

那個代收的節點怎麼知道這份資料其實不該歸它？靠 **hinted handoff（暗示移交）**：代收節點在資料上附一個 hint（「這其實是要給副本 3 的，我先幫忙存著」）。等分區 heal、正牌副本 3 回來了，代收節點就把暫存的資料**移交**回副本 3，然後刪掉自己的暫存。

```
   正常：key 該存在副本 {3,4,5}
   分區：副本 3 不可達，湊不到 W=2 個正牌
   sloppy：借用副本 6 暫存（附 hint "屬於 3"）→ 寫入 {4,5,6} 湊到 W=2，成功
   heal：副本 6 把暫存移交回副本 3 → 副本 6 刪暫存，資料歸位
```

代價很現實：sloppy quorum **犧牲了 R+W>N 的相交保證**。因為寫入的 W 個節點裡有「借用的」，讀取的 R 個正牌節點可能完全碰不到它們——相交數學失效，讀到 stale 的機率上升。所以 sloppy quorum 是「拿一致性換寫入可用性」的權衡，Dynamo 預設開，但需要嚴格一致的場景要關掉。這也再次印證 CAP：分區時，強一致（strict quorum，可能拒寫）和高可用（sloppy quorum，可能讀舊）只能選一個。

## 對比與取捨

| 設定（N=3） | R+W>N? | 保證讀最新? | 寫延遲 | 讀延遲 | 容忍故障數 | 適用 |
|---|---|---|---|---|---|---|
| W=2, R=2 | 是（4>3） | 是 | 中 | 中 | 讀寫各容 1 掛 | 均衡預設 |
| W=3, R=1 | 是（4>3） | 是 | 高 | 低 | 寫容 0、讀容 2 | 寫少讀多 |
| W=1, R=3 | 是（4>3） | 是 | 低 | 高 | 寫容 2、讀容 0 | 寫多讀少 |
| W=1, R=1 | 否（2≤3） | **否** | 低 | 低 | 高可用 | 容忍 stale |

| 收斂機制 | 觸發 | 修哪種資料 | 靠什麼高效 |
|---|---|---|---|
| Read repair | 讀取時發現分歧 | 熱資料（有人讀的） | 搭在讀路徑，零額外掃描 |
| Anti-entropy | 背景週期性 | 冷資料（沒人讀的） | Merkle tree 只傳差異 |

| Quorum 種類 | 分區時寫入 | 相交保證 | 代價 |
|---|---|---|---|
| Strict quorum | 湊不到 W 就拒寫（保一致，犧牲可用） | 有（R+W>N） | 分區時可能寫不動 |
| Sloppy quorum + hinted handoff | 借節點暫存，湊 W 就成（保可用） | **無**（借的節點讀不到） | stale 機率上升 |

## 踩雷集錦

1. **「R+W>N 就是 linearizable（強一致）了」——不是。** R+W>N 只保證「一次讀取碰得到最新已完成的寫入」，但它**不保證 linearizability**：並行的讀寫、寫入中途讀取、缺乏原子性的更新，都能製造出違反線性一致的執行（例如兩個並行讀，一個讀到新、一個讀到舊，因為寫入還沒傳到所有 quorum 成員）。Dynamo 風格 quorum 提供的是「**sloppy 的 read-your-writes 傾向 + 最終一致**」，不是真的線性一致。要真線性一致得配版本協調或共識（Ch 26）。這是最常見的誤解，Kleppmann DDIA 專門有一段拆穿它。

2. **「用時間戳決定最新版本就好」——時間戳會咬你。** 用 wall-clock 時間戳當版本號（Cassandra 早期的 LWW），會踩到 Ch 4 的時鐘謊言：兩個節點時鐘不同步，「較晚發生」的寫入可能帶著較小的時間戳，被「較早但時鐘快」的寫入覆蓋掉——**寫入靜默丟失**。這是 LWW 的致命傷（Ch 14 詳談）。本章 demo 用單調遞增的邏輯版本號迴避了它，但真實系統若用 wall-clock，就繼承了時鐘的所有問題。

3. **「W=1 反正 read repair 會補上，沒差」——耐久性沒了。** W=1 代表寫入只落地一個副本就回 ok。那個副本在 read repair / anti-entropy 把它傳出去之前崩潰，這筆寫入就**永久消失**——跟非同步主從的資料遺失視窗一模一樣。read repair 只能修「還活著的副本之間的分歧」，救不了「唯一有這份資料的副本掛了」。寫路徑至少 W=2 求耐久，別為了寫延遲把 W 壓到 1。

4. **「sloppy quorum 也是 quorum，一樣保證讀到最新」——sloppy 恰恰放棄了那個保證。** sloppy quorum 為了寫入可用性，允許寫到「非正牌」節點，這**破壞了 R+W>N 的相交前提**——讀取的正牌節點可能完全碰不到那些借用節點上的寫入。sloppy 換來的是「幾乎不會拒寫」，代價是「更容易讀到舊值」。把 sloppy quorum 當 strict quorum 用，會在分區期間得到你以為不會有的 stale read。

5. **「quorum 讀寫節點越多越安全，全設 R=W=N 最保險」——你把可用性歸零了。** R=N 意味著讀取要**所有**副本都回應才成功，只要一個副本慢或掛，讀取就卡死/失敗——你親手把「容忍節點故障」這個複製的核心價值丟掉了。quorum 的精髓是「**多數就夠**」（W=R=⌈(N+1)/2⌉ 之類），既保相交又留容錯餘裕。設 R=W=N 等於退化成「必須全體同意」，比主從還脆。

## 進階：再往深一層

- **Dynamo 用 vector clock 處理並行寫入，而非時間戳**：本章 demo 用版本號簡化，但真實 Dynamo 面對「兩個並行寫入同一 key」時，用 **vector clock（Ch 6）** 來判斷是「一個因果地覆蓋另一個」（那就取代）還是「真並行的衝突」（那就**兩個都保留成 siblings**，交給應用層或下次寫入來合併）。這是 Ch 14 衝突解決的核心，quorum 只負責「湊夠數量讀到候選值」，「哪個值對」是另一層的事。

- **「wide-column 的 quorum 是逐操作可調的」**：Cassandra 讓你**每次讀寫**單獨指定一致性等級（`ONE`/`QUORUM`/`ALL`/`LOCAL_QUORUM`…），而非全表固定。你可以對關鍵寫入用 `QUORUM`、對計數器讀取用 `ONE`。這呼應 Ch 9 踩雷 #4：一致性往往是「每操作的選擇」不是「全系統屬性」。`LOCAL_QUORUM`（只在本資料中心湊 quorum）更是跨區部署的常用取捨——本地強一致、跨區非同步。

- **Merkle tree 的實務調校**：anti-entropy 的 Merkle tree 不是免費的——維護樹、算 hash 有 CPU 成本，樹太細（葉節點覆蓋 key 範圍太小）則樹太大、比對慢；太粗則一點差異就要傳一大段 key。Cassandra 的 repair 就是出了名地吃資源、要小心排程。這是「用 hash 樹換傳輸量」的典型工程權衡，值得看 Cassandra 的 `nodetool repair` 文件理解真實系統怎麼調。

- **quorum 的「多數」為什麼是容錯的黃金分割**：設 W = R = ⌈(N+1)/2⌉（多數），則任兩個 quorum 必相交（多數的數學，跟上一章堵腦裂的多數決同源），且能容忍 ⌊(N-1)/2⌋ 個節點故障。這個「多數 quorum」是連接本章與 Part 3 共識的橋——Paxos/Raft 的每一步 commit 也都要求多數，本質上就是 quorum 相交保證「新舊 leader / 新舊決議必有一個共同見證者」。**quorum 相交，是整個共識理論的地基。** 帶著這個直覺進 Part 3，Paxos 會好懂很多。

## 本章重點整理

- **無主（leaderless）複製**：沒有 primary，每個副本平等、都能收讀寫。消除了主從的單點故障與腦裂，代價是要自己處理副本分歧。Dynamo/Cassandra/Riak 是代表。
- **R + W > N**：讀集合與寫集合必相交（鴿籠原理），交集節點同時有最新寫入又被讀到，於是**讀得到最新**。純組合數學，不需節點間協調投票。
- **W、R 是可調旋鈕**：W 大→寫更耐久但更慢更難湊；R 大→讀更容易碰到最新但更慢。W=R=⌈(N+1)/2⌉（多數）是甜蜜點。R+W≤N（如 1,1）放棄「讀最新」保證換極致低延遲。
- **收斂機制**：read repair（讀時發現分歧就把最新值寫回落後副本，修熱資料）+ anti-entropy（背景用 **Merkle tree** 高效比對、只傳差異，修冷資料）。兩者共同實現 eventual consistency。
- **sloppy quorum + hinted handoff**：分區時湊不到正牌 quorum，就借非正牌節點暫存（附 hint），heal 後移交回正牌副本。保住寫入可用性，但**犧牲 R+W>N 的相交保證**（stale 機率上升）——又一個 CAP 權衡。
- **R+W>N ≠ linearizable**：它保「碰得到最新」，但並行讀寫仍能違反線性一致。要真線性一致得配版本協調或共識。
- **quorum 相交是共識的地基**：多數 quorum 保證任兩個 quorum 有共同見證者，這正是 Part 3 Paxos/Raft 每步 commit 要多數的原因。

## 自我檢核

- [ ] 不看圖，我能用鴿籠原理講清楚「為什麼 R+W>N 保證讀寫集合相交」
- [ ] 給我 N、W、R，我能判斷這組設定「保不保證讀到最新」，並說出它能容忍幾個副本故障
- [ ] 我能解釋為什麼 W=1 即使配 read repair 仍有耐久性風險
- [ ] 我能分別說出 read repair 和 anti-entropy 各修哪種資料、為什麼互補，以及 Merkle tree 在後者的作用
- [ ] 我能講清楚 sloppy quorum 換到了什麼（可用性）、犧牲了什麼（相交保證），並把它連回 CAP
- [ ] 我能說出「為什麼 R+W>N 不等於 linearizable」，舉一個違反線性一致的並行執行
- [ ] 我能把「quorum 相交」連到 Part 3 共識為什麼要多數

## 延伸閱讀

- **[Dynamo: Amazon's Highly Available Key-value Store](https://www.allthingsdistributed.com/files/amazon-dynamo-sosp2007.pdf)** — DeCandia et al., SOSP（2007）
  - **這篇說什麼**：無主複製的奠基作，quorum（含 sloppy quorum + hinted handoff）、vector clock 衝突偵測、Merkle tree anti-entropy、一致性雜湊全在裡面
  - **讀哪裡**：Section 4.5（quorum 與 sloppy quorum）、4.6（hinted handoff）、4.7（Merkle tree anti-entropy）；4.4 的 vector clock 為 Ch 14 鋪路
  - **前提**：讀懂本章 quorum 與 Ch 6 vector clock；這篇工程味濃、極好讀

- **《Designing Data-Intensive Applications》第 5 章 "Leaderless Replication"** — Martin Kleppmann（2017）
  - **這章說什麼**：把 quorum 的 R+W>N、W/R 取捨、sloppy quorum、read repair/anti-entropy 用工程語言講一遍，並專門拆穿「quorum ≠ linearizable」
  - **讀哪裡**："Quorums for reading and writing" 到 "Detecting Concurrent Writes" 幾節，直接對應本章
  - **為什麼值得讀**："Limitations of Quorum Consistency" 那一節是本章踩雷 #1 的完整論證，務必讀

- **[Cassandra: Read repair & Anti-entropy repair 官方文件](https://cassandra.apache.org/doc/latest/cassandra/managing/operating/repair.html)**
  - **這是什麼**：真實系統怎麼實作與調校 anti-entropy（`nodetool repair`）、Merkle tree 的實務代價
  - **讀哪裡**：repair 概念與 incremental repair 一節，理解本章「Merkle tree 不是免費的」的工程現實
  - **前提**：讀懂本章收斂機制；配 Dynamo 論文對照「論文理想 vs 生產調校」

quorum 讓我們在無主架構下讀到最新，但它刻意迴避了一個問題：當兩個副本真的收到**並行的、衝突的**寫入時，「最新」到底是誰？用版本號簡化掩蓋了這個坑。下一章我們正面處理衝突——LWW 為什麼危險、version vector 怎麼偵測並保留衝突、以及讓副本「無論合併順序都收斂到同值」的 CRDT。

→ [Ch 14 衝突解決與 CRDT 入門](./14-conflict-resolution-crdt.md)
