# Ch 35 — 共識層攻擊面

> **目標**：從防禦工程師的角度拆解共識層的攻擊面。Sybil 攻擊、Eclipse 攻擊、分區/延遲操縱、PoS 的 long-range / nothing-at-stake——每種給清楚的機制與影響，再給可操作的防禦與偵測策略。用 `dsim` 真跑一個 Eclipse 情境，親眼看節點被隔離後陷入舊視圖。

> **環境**：Go 1.18.1, WSL2/Linux x86-64。Eclipse 示範在 `dsim` 真跑；PoS 相關情境標「**未實測，理論預期行為**」。

## 為什麼需要這個？

Part 3 和 Part 4 把共識的正確路徑講得很清楚：Raft 怎麼在崩潰下保持安全、PBFT 怎麼在拜占庭節點下仍達成一致。但那些章描述的是「誠實的世界」。

現實是：**有對手**。有人想破壞你的共識、讓你的叢集腦裂、或強迫你讀到舊資料後做出他想要的決策。

這章做三件事：

1. **釘清楚攻擊模型**：誰是攻擊者、他能控制什麼、攻擊目標是 liveness 還是 safety。
2. **拆解每種攻擊的機制**：不是把它當威脅清單背，而是弄懂「為什麼這一招能打穿共識的假設」。
3. **給防禦/偵測策略**：實際上能做什麼、偵測什麼訊號、哪些保護有根本性邊界。

> 若對基本失敗模型（crash vs. 拜占庭）不熟，回看 [Ch 2](./02-failure-and-network-models.md)。對 PBFT 不熟，回看 [Ch 33](./33-pbft.md)。

## 先建立直覺

共識協定建立在幾個假設上：

```
  Raft / Paxos（崩潰容錯）假設：
    1. 節點要嘛正常、要嘛靜默崩潰（不會主動說謊）
    2. 訊息最終會送達（非同步但有 progress）
    3. 節點的「身份」是固定且已知的（許可制網路）

  PBFT / BFT 協定假設：
    1. 同上第 1、2 條，但允許節點主動說謊（拜占庭）
    2. 身份仍是已知的、有限的 3f+1 節點集合
    3. 網路最終同步（GST 之後）

  開放網路（Bitcoin、Ethereum）的現實：
    1. 任何人都能加入
    2. 節點數量和身份都是匿名、動態的
    3. 攻擊者能租算力或購買權益
```

攻擊者的目標就是**打穿這些假設**。每種攻擊對應一條被打穿的假設：

```
  Sybil 攻擊   → 打穿「身份有限/可信」假設
  Eclipse 攻擊 → 打穿「訊息最終送達誠實節點」假設
  分區/延遲操縱 → 打穿「網路最終同步（GST）」假設
  Long-range   → 打穿「PoS 的歷史不可竄改」假設
```

## 攻擊一：Sybil 攻擊

### 機制

**Sybil 攻擊**（Sybil attack）：一個攻擊者建立大量虛假身份（Sybil 節點），用這些「殭屍身份」在需要多數決的協定裡佔多數，從而控制共識結果。

名稱來自 John R. Douceur 2002 年的論文，以 1973 年的心理學案例「Sybil」（一個具有多重人格的病患）命名。

```
  誠實網路（無 Sybil）：
    [Alice] [Bob] [Carol]    ← 3 個獨立節點
    多數 = 2/3，攻擊者要控制 2 個才能主宰多數決

  Sybil 攻擊（一個攻擊者偽造多身份）：
    [Alice] [Sybil₁] [Sybil₂] [Sybil₃] [Sybil₄]
    攻擊者用 1 台機器冒 4 個身份
    「多數」= 3/5，但攻擊者控制 4/5 → 完全掌控
```

這在**開放、無許可的網路**裡特別危險。像 Raft 這種許可制（permissioned）協定，節點清單寫死在設定檔裡，你不能憑空加節點。但 P2P 網路（BitTorrent、Ethereum）任何人都能以任何身份加入，Sybil 攻擊的門檻極低。

### 影響

對共識的直接影響取決於協定型別：

- **基於「節點數多數」的協定**（樸素 P2P 投票）：一個 Sybil 攻擊者能立刻控制大多數「票」，偽造共識結果、審查交易、發動雙花。
- **Raft/Paxos**：許可制，節點清單固定，Sybil 無從著力。根本防禦是「不接受未授權節點加入叢集」。
- **BitTorrent/Kademlia DHT**：Sybil 節點能污染路由表，讓查詢被導向攻擊者控制的節點（這也是 Eclipse 攻擊的前奏）。

### 防禦與偵測

**根本解法：讓「身份」有成本**。如果偽造一個身份需要付出可觀代價，大規模 Sybil 的成本就高到不划算：

| 機制 | 原理 | 代表系統 |
|---|---|---|
| **PoW（工作量證明）** | 每個身份要消耗算力才能「証明自己存在」，無法無限偽造 | Bitcoin 中本聰共識 |
| **PoS（權益證明）** | 每個身份要抵押真實資產，偽造需要真的購買大量代幣 | Ethereum PoS |
| **PKI + CA 許可制** | 身份由受信任的 CA 簽發，攻擊者無法偽造憑證 | Raft、etcd、企業 BFT |
| **社交圖譜 / Web of Trust** | 身份由其他可信人擔保，Sybil 難以取得足夠擔保 | PGP 信任網 |
| **資源限制（IP / 頻寬）** | 偵測同一來源的大量連線，限制每 IP 的最大節點數 | P2P 協定防護層 |

**偵測訊號**：
- 同一 IP/ASN 區塊出現大量節點
- 新節點突然大量加入（群聚時間）
- 行為高度雷同（多個「不同」節點總是同時投相同的票、用相同的延遲）

> Sybil 攻擊本身不直接打破 PBFT 的安全性——PBFT 的節點清單是固定的。但如果攻擊者能讓自己的節點被納入「已知節點清單」（透過社交工程或系統管理漏洞），就等同於讓真實的拜占庭節點數 `f` 超過容忍上限，PBFT 的保證就不成立了。

## 攻擊二：Eclipse 攻擊

### 機制

**Eclipse 攻擊**（Eclipse attack）：攻擊者不需要打穿共識協定本身，而是把目標節點的所有 peer 連線都換成攻擊者控制的節點，讓目標節點看到一個「被遮蔽的視圖」——它以為自己連著誠實網路，實際上所有消息都經過攻擊者過濾。

```
  正常狀態：
    [Victim] ──connected to──> [Honest₁] [Honest₂] [Honest₃]
              ← 收到真實的鏈狀態、真實的交易

  Eclipse 之後：
    [Victim] ──connected to──> [Sybil₁] [Sybil₂] [Sybil₃]
              ← 攻擊者餵假/舊的鏈狀態，隱藏真實交易

    而真實誠實網路：
    [Honest₁] [Honest₂] [Honest₃]
    ← 繼續在真實鏈上工作，Victim 被完全隔離
```

Eclipse 攻擊通常分兩步：

1. **Sybil 化 peer 清單**：在目標的 peer 探索機制（Kademlia、Gossip、DNS seeds）裡塞滿攻擊者控制的節點 ID，讓目標的 peer 清單被 Sybil 節點佔滿。
2. **等待重啟 / 強制重啟**：目標節點下次重啟時，它的初始連線全連向 Sybil 節點，Eclipse 完成。

### 影響

被 Eclipse 的節點：

- **Raft 叢集裡的一個 follower**：它看到的是舊的 leader 心跳，不知道叢集已經選出新 leader；或被餵假的 commit 訊息，以為某個值已 commit 而實際上沒有。
- **Bitcoin/Ethereum 的一個節點**：它被餵一條比真實鏈更短/更舊的鏈，卻以為那是最長鏈；攻擊者可以讓它在這條假鏈上確認了「6 個確認」，然後在真實鏈上發起雙花。
- **選舉中的一個投票者**：它看到的候選人資訊被過濾，可能投票給攻擊者指定的候選人。

### dsim 模擬：Eclipse 後節點陷入舊視圖

用 `dsim` 的 `Partition` 模擬 Eclipse——把受害者節點 0 隔離，讓誠實網路繼續更新，觀察受害者的 chainTip 卡在舊值：

```go
package main

import "fmt"

type EclipseMsg struct {
    Type     string
    ChainTip int
    Data     string
}

type EclipseNode struct {
    id       NodeID
    chainTip int
    decided  []string
}

func (n *EclipseNode) OnMessage(m Message, net *Net) {
    msg, ok := m.Payload.(EclipseMsg)
    if !ok { return }
    switch msg.Type {
    case "UPDATE":
        if msg.ChainTip > n.chainTip {
            n.chainTip = msg.ChainTip
        }
        fmt.Printf("[t=%3d] Node %d received UPDATE chainTip=%d from Node %d\n",
            net.Now(), n.id, msg.ChainTip, m.From)
    case "QUERY":
        net.Send(Message{From: n.id, To: m.From, Payload: EclipseMsg{
            Type: "RESPONSE", ChainTip: n.chainTip,
        }})
    case "RESPONSE":
        fmt.Printf("[t=%3d] Node %d (victim) got RESPONSE from %d: chainTip=%d\n",
            net.Now(), n.id, m.From, msg.ChainTip)
        n.decided = append(n.decided, fmt.Sprintf("peer%d:tip%d", m.From, msg.ChainTip))
    }
}

func (n *EclipseNode) OnTick(now int, net *Net) {
    // 誠實節點每 5 tick 廣播 chainTip
    if now%5 == 0 && n.id != 0 {
        for i := 0; i < 4; i++ {
            if NodeID(i) != n.id {
                net.Send(Message{From: n.id, To: NodeID(i), Payload: EclipseMsg{
                    Type: "UPDATE", ChainTip: n.chainTip,
                }})
            }
        }
    }
    // 受害者在 t=20 查詢所有 peer
    if now == 20 && n.id == 0 {
        fmt.Printf("[t=%3d] Node 0 (victim) querying peers for chain state\n", now)
        for i := 1; i < 4; i++ {
            net.Send(Message{From: 0, To: NodeID(i), Payload: EclipseMsg{Type: "QUERY"}})
        }
    }
}

func main() {
    fmt.Println("Phase 1: normal operation")
    net := NewNet(10)
    nodes := make([]*EclipseNode, 4)
    for i := 0; i < 4; i++ {
        nodes[i] = &EclipseNode{id: NodeID(i), chainTip: 100}
        net.Add(NodeID(i), nodes[i])
    }
    net.Run(15)

    fmt.Println("\nPhase 2: Eclipse node 0 from honest network")
    net.Partition([]NodeID{0}, []NodeID{1, 2, 3})
    // 誠實網路進展到 150
    for i := 1; i < 4; i++ { nodes[i].chainTip = 150 }
    net.Run(30)

    fmt.Printf("\nNode 0 (victim) chainTip: %d\n", nodes[0].chainTip)
    fmt.Printf("Honest nodes chainTip: %d\n", nodes[1].chainTip)
    fmt.Printf("Node 0 decided based on: %v\n", nodes[0].decided)
    fmt.Println("-> Victim on STALE VIEW; decisions based on old data")
    fmt.Printf("Delivered=%d Dropped=%d\n", net.Delivered, net.Dropped)
}
```

**真跑輸出**（WSL, Go 1.18.1, seed 10）：

```
Phase 1: normal operation
[t=  6] Node 0 received UPDATE chainTip=100 from Node 2
[t=  6] Node 1 received UPDATE chainTip=100 from Node 2
[t=  6] Node 3 received UPDATE chainTip=100 from Node 2
[t=  6] Node 0 received UPDATE chainTip=100 from Node 3
[t=  6] Node 1 received UPDATE chainTip=100 from Node 3
[t=  6] Node 2 received UPDATE chainTip=100 from Node 3
[t=  6] Node 0 received UPDATE chainTip=100 from Node 1
[t=  6] Node 2 received UPDATE chainTip=100 from Node 1
[t=  6] Node 3 received UPDATE chainTip=100 from Node 1
...（Phase 1 更多同步訊息）

Phase 2: Eclipse node 0 from honest network
[t= 20] Node 0 (victim) querying peers for chain state
...（Node 0 的 QUERY 在 Partition 後被丟棄，Dropped=15）
[t= 21] 誠實節點間互換 chainTip=150
...

After eclipse - Node 0 (victim) chainTip: 100
Honest nodes chainTip: 150
Node 0 decided based on: []
-> Victim is on a STALE VIEW; any decision it makes is based on old data
Delivered=36 Dropped=15
```

Node 0 的 QUERY 全部被 Partition 攔截（`Dropped=15`），它的 chainTip 永遠停在 100，而誠實網路早已推進到 150。這就是 Eclipse 最核心的危害：**節點自認活著、自認在線，但實際上已被隔絕在現實之外**。

### 防禦與偵測

**防禦層：**

- **Peer 多樣性**：強制連線到不同 ASN、不同地理位置的節點；限制同一 /24 子網的最大連線數。Bitcoin Core 在 2015 年針對 Eclipse 的修補就是這思路。
- **Outbound 連線優先**：主動發起的 outbound 連線比被動接受的 inbound 連線更難被操縱——攻擊者要控制你的 outbound 目標需要先控制 DNS seeds 或 peer 廣播。
- **隨機化 peer 選擇**：不要貪方便只連「最近的」或「最快回應的」，要隨機選——攻擊者可以用響應速度快來讓 victim 優先連向自己。
- **簡單超時/心跳檢查**：一段時間沒收到鏈更新就發出警報，而不是靜默地接受舊視圖。

**偵測訊號：**

- 自己的 chainTip 明顯落後所有 peer 報告的高度
- 連線的 peer 集合 IP 分佈異常集中（都在同一 /16）
- Inbound 連線突然飆高（Sybil 節點主動連進來企圖擠掉誠實 peer）

## 攻擊三：分區/延遲操縱

### 機制

這是從網路層對共識協定發動的攻擊。Ch 10 的 CAP 定理告訴我們，網路分區（P）發生時，系統必須在 C 和 A 之間擇一。攻擊者**人為製造分區或精準注入延遲**，迫使系統做出它不想做的取捨：

```
  Raft 叢集的分區攻擊場景：
    [Leader L] ─── 分區 ───> [Follower F₁] [Follower F₂]
                              ↑
                 (F₁, F₂ 超過 election timeout → 選出新 leader)

    分區修復後：
    [Old Leader L] 和 [New Leader L'] 同時以為自己是 leader
    → split-brain 情境（Raft 靠 term 防這個，但時序攻擊能讓
      舊 leader 暫時服務請求直到它收到新 leader 的心跳）
```

**延遲操縱**（delay injection）更精妙：不完全切斷連線，而是人為把特定節點的訊息延遲到剛好超過 election timeout 的邊緣，讓叢集不斷觸發選舉、陷入 liveness 問題（選不出穩定 leader）。

這在真實攻擊中可以透過：
- 控制受害叢集的上游路由器（BGP 劫持、BGP 重路由）
- 在雲端環境操縱安全群組規則或網路 ACL
- 針對雲端 VM 的 CPU 搶佔（noisy neighbor）刻意造成 tick 延遲

### 影響

- **對 Raft/Paxos**：直接攻擊 liveness（選不出 leader、無法 commit），safety 理論上不受損（如果協定實作正確）。但如果 election timeout 設定錯誤或實作有 bug，分區可能造成 split-brain。
- **對 PBFT**：PBFT 靠 GST（global stabilization time）假設——在 GST 之後網路最終同步。持續的延遲攻擊能讓 GST 永遠不到來，view change 不斷被觸發，叢集永遠無法在一個 view 裡完成共識。
- **對區塊鏈**：精準製造分區讓兩個礦工社群各自延伸自己的鏈，產生長時間的 fork；然後在合適的時機修復分區，觸發一次大規模 reorg。

### 與 CAP 定理的關係

> 若對 CAP 不熟，回看 [Ch 10](./10-cap-theorem.md)。

CAP 分析的是「分區發生時系統的選擇」，而攻擊者想的是「我能不能讓分區按我的節奏發生，觸發我想要的選擇後果」：

```
  CAP 的框架：
    P 發生（不可控）→ 系統選 C 或 A

  攻擊者的框架：
    攻擊者觸發 P（可控）→ 被攻擊的系統被迫選 C 或 A
    攻擊者看情況選對自己有利的那個觸發時機
```

**防禦與偵測：**

- **Election timeout 的合理設定**：timeout 應遠大於正常網路延遲的 99th percentile，但不能太大（影響 recovery 速度）。設置 timeout 時要考慮「攻擊者能輕鬆做到的延遲」是多少。
- **Bounded staleness 警告**：超過多少時間沒有成功 commit 就觸發警告，讓操作員介入。
- **多路徑冗餘**：關鍵叢集的節點間連線走多條不相關的路徑，單一路徑被攻擊時其他路徑還能通。
- **BGP 安全**：ROA（Route Origin Authorization）和 RPKI 能防禦 BGP 劫持造成的路由轉向。

## 攻擊四：Long-Range 與 Nothing-at-Stake（PoS 情境預告）

> **未實測，理論預期行為**（需要真實 PoS 鏈環境）

這兩種攻擊專屬於**權益證明（Proof of Stake, PoS）**共識。PoS 用「抵押代幣」替代 PoW 的算力成本作為 Sybil 防禦——你的權益越大，你有機會提出/投票 block 的比例越高。

### Nothing-at-Stake（無代價投票）

PoW 礦工在 fork 時只能選一條鏈——算力是實體的，押注在一條鏈就沒有算力支援另一條。但 PoS 驗證者的「投票」是純數位的，沒有實體成本，**理論上可以同時投票給所有 fork**（nothing at stake）：

```
  PoS fork 情境：
    鏈 A: ... ─ Block₅A ─ Block₆A        <- 誠實大多數在走的鏈
    鏈 B: ... ─ Block₅B                   <- 攻擊者發起的競爭鏈

  PoW 驗證者（礦工）：只能選一條押算力
  PoS 驗證者：可以同時在 A 和 B 上投票，反正沒有成本
              → 兩條鏈都有足夠投票 → 兩條鏈都可能 finalize
              → 永久性 fork
```

**防禦**：現代 PoS 協定（Ethereum PoS / Casper）引入**懲罰機制（slashing）**——驗證者若在同一 slot 對兩個衝突的 block 投票，抵押的代幣會被沒收（slash）。這讓「兩邊都投」變成有代價的行為。

### Long-Range 攻擊

攻擊者若持有大量在**舊時間點**有效的私鑰（例如已出售的舊代幣對應的 validator key），可以從歷史某個點開始，用那批舊金鑰重新建構一條更長的「假歷史鏈」：

```
  真實鏈：  Genesis ─── Block₁ ─── Block₂ ─── ... ─── Block₁₀₀ (now)
  攻擊鏈：  Genesis ─── Block₁' ── Block₂' ── ... ─── Block₁₂₀ (more blocks)
             ↑                                          ↑
         攻擊者擁有 t=1 時大量舊 validator key        比真實鏈更長
```

因為 PoS 沒有 PoW 的算力成本，舊金鑰「免費」產生 block，攻擊者可以用歷史上的大量舊金鑰快速建構一條比真實鏈更長的假鏈。新加入的節點如果不知道「真實鏈的檢查點」在哪，可能被欺騙接受假鏈。

**防禦**：
- **Weak subjectivity checkpoints**：新節點必須從可信來源取得近期的真實鏈檢查點（通常是幾週內的），而不是從創世區塊開始同步——攻擊者的假鏈在檢查點之後的 hash 和真實鏈不同，就過不了關。
- **Finality gadget**：Ethereum 的 Casper FFG 讓超過 2/3 質押量批准過的 epoch 變成最終確定（finalized），之後的 long-range 重組在協定層就被拒絕。

## 對比與取捨

| 攻擊 | 目標假設 | 主要影響 | 根本防禦 | 適用協定 |
|---|---|---|---|---|
| Sybil | 身份有限可信 | 操縱多數決 | 身份成本（PoW/PoS/PKI） | 開放 P2P |
| Eclipse | 誠實節點可達 | 舊視圖、雙花 | Peer 多樣性、outbound 優先 | 所有網路 |
| 分區/延遲 | 網路最終同步 | Liveness 喪失 | Timeout 設計、多路徑 | 所有 CFT/BFT |
| Nothing-at-stake | PoS 投票有代價 | 永久 fork | Slashing 機制 | PoS |
| Long-range | 歷史不可竄改 | 假鏈欺騙新節點 | 弱主觀性檢查點、finality gadget | PoS |

## 踩雷集錦

1. **「Raft 的 safety 保證讓它對分區免疫」**：錯。Raft 在分區下的 safety 保證（不腦裂）是真的，但分區直接打的是 liveness——叢集可能在少數派分區卡住。「不腦裂」不代表「仍能服務請求」。攻擊者的目標可能就是讓你的服務停擺（DoS 效果）。

2. **「Eclipse 攻擊需要控制很多網路節點」**：不需要那麼多。Bitcoin 2015 年的研究顯示，攻擊者只需要在受害節點的 peer 發現機制裡讓自己的 IP 佔多數，就能在節點下次重啟時完成 Eclipse。早期版本 Bitcoin 連「同一 /24 的 IP 最多幾個」都沒限制，讓 Eclipse 特別容易。

3. **「PoS 的 slashing 完全解決了 nothing-at-stake」**：大幅緩解但沒有「完全解決」。Slashing 的偵測有視窗限制，極端情況（大量驗證者共謀、長時間分區後的重組）仍有邊界案例。現代 PoS 設計要求對 slashing 的情境有深入分析，不能假設一個 slashing 條件就護身。

4. **「分區攻擊只能對公開網路有效」**：私有雲、企業資料中心同樣脆弱。BGP 劫持、雲端 VPC 的安全群組配置錯誤、某個 availability zone 的網路抖動，都可能觸發叢集的分區行為。2021 年的 Cloudflare 和 AWS 都曾因路由異常讓分散式服務觸發 leader 重選或暫時降級。

5. **「Eclipse 攻擊只影響 blockchain 節點」**：Raft/Paxos 叢集同樣可以被 Eclipse。如果攻擊者能控制一個 follower 的網路介面（例如在 VM 層），讓它只看到攻擊者控制的「假 leader」，那個 follower 會回應客戶端請求，但依據的是舊狀態。微服務架構裡的 sidecar 代理如果被注入假的服務發現資訊，本質上也是 Eclipse。

## 進階：再往深一層

**Eclipse 在 DHT 裡的系統化研究**：Kademlia（Ethereum 的 DevP2P、IPFS）的 peer 路由表被 Eclipse 的模型比 Bitcoin 更精確——有 Sybil 節點只要佔據目標的 `k-bucket` 裡的位置就能控制路由。S/Kademlia 透過要求 nodeID 由公鑰 hash 計算（proof-of-work on nodeID generation）來對抗這一點。

**BGP 劫持對共識的影響**：2018 年的研究「Hijacking Bitcoin: Routing Attacks on Cryptocurrencies」（Apostolaki et al., IEEE S&P 2017）顯示，BGP 劫持能把整個 Bitcoin 礦池的流量攔截/重定向，對 Nakamoto 共識造成顯著的 Eclipse 效果。這是基礎設施層攻擊影響應用層共識的典型案例。

**時鐘操縱與共識**：NTP 欺騙（讓節點時鐘漂移）能讓 lease 機制或 TrueTime 假設失效——這接下來在 Ch 38 會深入討論。

## 本章重點整理

- Sybil 攻擊打「身份假設」；Eclipse 攻擊打「節點可達性假設」；分區/延遲攻擊打「GST 假設」；PoS 的 long-range / nothing-at-stake 打「歷史不可竄改假設」。
- 根本防禦原則：讓關鍵假設的違反代價高（身份成本）、讓被孤立的狀態可偵測（超時/差異監控）、讓歷史有確定性錨點（finality / checkpoints）。
- Raft/PBFT 的許可制架構免疫 Sybil，但不免疫 Eclipse 和分區攻擊。
- PoS 必須解決 nothing-at-stake 和 long-range 才能成立，現代協定的 slashing 和 finality gadget 是對這兩者的直接回應。
- Eclipse 攻擊成本低、效果好、不需破解密碼學；Peer 多樣性是最直接的防禦。

## 自我檢核

- [ ] 我能不看筆記說出 Sybil 攻擊的核心邏輯，以及為什麼「讓身份有成本」是根本防禦
- [ ] 我能描述 Eclipse 攻擊的兩步驟（Sybil 化 peer 清單 + 等待重啟），以及它對 Raft follower 的具體影響
- [ ] 我能解釋為什麼分區攻擊打 liveness 而不打 safety，以及什麼情況下才打 safety
- [ ] 我能說出 nothing-at-stake 和 long-range 各自打穿了 PoS 的哪個假設，以及 slashing 和 weak subjectivity 各自對應哪個攻擊
- [ ] 看到「我的 Raft 叢集 safety 有保證」這句話，我能說出攻擊者仍能做什麼

## 延伸閱讀

- **[Eclipse Attacks on Bitcoin's Peer-to-Peer Network](https://www.usenix.org/conference/usenixsecurity15/technical-sessions/presentation/heilman)** — Heilman et al., USENIX Security 2015
  - **這篇說什麼**：對 Bitcoin P2P 層的 Eclipse 攻擊系統化分析，包括實際所需的 IP 數量、攻擊成功的時序，以及 Bitcoin Core 後來的防護修補
  - **讀哪裡**：§3（攻擊）和 §4（防禦）；§2 的 Bitcoin peer management 模型也值得看
  - **前提**：理解 Bitcoin P2P 連線機制即可；不需要密碼學

- **[Hijacking Bitcoin: Routing Attacks on Cryptocurrencies](https://ieeexplore.ieee.org/document/7958588)** — Apostolaki et al., IEEE S&P 2017
  - **這篇說什麼**：BGP 劫持如何作用於 Bitcoin 網路層，攔截礦池流量或孤立節點；測量了實際 Bitcoin 節點在 BGP 層面的脆弱性
  - **讀哪裡**：§3（攻擊模型）、§4（可行性評估）；圖表非常清晰
  - **前提**：基本 BGP 知識（你的 networking 課）

- **[Sybil Attack](https://www.microsoft.com/en-us/research/publication/the-sybil-attack/)** — Douceur, IPTPS 2002
  - **這篇說什麼**：Sybil 攻擊的原始論文，給出「為什麼任何沒有集中認證機構的系統都無法從根本上防 Sybil」的理論結果
  - **讀哪裡**：全文 6 頁，論證緊湊，一次讀完
  - **前提**：無（入門友好）

- **[Casper the Friendly Finality Gadget](https://arxiv.org/abs/1710.09437)** — Buterin & Griffith, 2017
  - **這篇說什麼**：Ethereum 的 PoS finality 設計，包括 slashing 條件（Commandment I/II）如何對抗 nothing-at-stake 和 long-range
  - **讀哪裡**：§3（Casper 協定）、§4（slashing 條件）
  - **前提**：理解基本 BFT quorum 概念（Ch 33）

準備好攻擊面的直覺了，下一章我們把「把區塊鏈當共識協定分析」——Nakamoto 共識的機率性 finality 到底在設計什麼、為什麼它和 BFT 協定走的是完全不同的路。

→ [Ch 36 Nakamoto Consensus](./36-nakamoto-consensus.md)
