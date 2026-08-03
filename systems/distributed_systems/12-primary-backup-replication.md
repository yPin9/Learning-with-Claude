# Ch 12 — 主從複製

> **目標**：把複製最古老、最直覺、也最多真實系統在用的一種架構——**主從複製（primary-backup replication）**——從頭講清楚。你要理解同步複製與非同步複製的差別、以及非同步那個「已回覆客戶端成功、但資料還沒複製出去」的**資料遺失視窗（data-loss window）**是怎麼咬人的。接著看故障切換（failover）怎麼把 backup 扶正、以及它最惡名昭彰的失敗模式：**腦裂（split-brain）**——兩個節點同時以為自己是 primary。最後說清楚為什麼要用 fencing / lease / 多數決來堵這個洞，這也是我們往 Part 3 共識前進的動機。

> **環境**：Go 1.18.1, WSL2 / Linux x86-64。本章的模擬跑在 Ch 0 的 `dsim` 上。

上一章 PACELC 告訴我們：就算沒有分區，一致性和延遲之間也永遠在角力。這一章我們挑一個具體的複製架構，把那個角力落到程式碼層次看。主從複製是最簡單的答案——選一個節點當老大，其他人抄它。簡單，但魔鬼全在故障切換的細節裡。

## 為什麼需要這個？

Ch 8 我們論證了「為什麼要複製」：容錯、讀吞吐、地理就近。但複製一旦有多份，馬上冒出一個新問題——**寫入該往哪份寫？** 如果允許任何副本都能接受寫入，那兩個客戶端同時往兩份副本寫不同值，你就得處理衝突（那是 Ch 13、14 的世界）。

主從複製給了一個極簡的答案：**指定一個副本當 primary，所有寫入只能經過它。** 其他副本是 backup（也叫 follower / secondary / replica），只被動接收 primary 傳來的更新。這樣一來，寫入有了單一的序列化點——primary 決定了所有寫入的順序，backup 只要照抄，副本之間就不會對「誰先誰後」有分歧。

這個模型古老到不行。MySQL 的 binlog 主從複製、PostgreSQL 的 streaming replication、Redis 的 replica、MongoDB 的 replica set（primary + secondaries）、甚至檔案系統層的 DRBD——骨架全是主從。它之所以歷久不衰，是因為它把「多副本一致」這個難題化簡成「一個人說了算，其他人照抄」，而「照抄一份有序的更新流」是相對好做對的事。

代價在哪？在 primary 死掉的那一刻。單一寫入點意味著**單點故障（single point of failure）**——primary 一死，整個系統就不能寫了，直到有人把某個 backup 扶正。而「扶正」這個動作，就是本章後半所有痛苦的來源。

## 先建立直覺

先把主從的資料流畫出來。一次寫入的生命週期：

```
   client                primary (node 0)            backup (node 1)
     │                        │                            │
     │──── write(x=1) ───────>│                            │
     │                        │  append to log             │
     │                        │──── replicate(x=1) ───────>│
     │                        │                            │ apply x=1
     │                        │<──────── ack ──────────────│
     │<─────── ok ────────────│                            │
     │                        │                            │
   讀取可以走 primary（一定最新），也可以走 backup（可能落後）
```

關鍵問題只有一個：**primary 在「哪個時間點」回覆客戶端 ok？**

- **等 backup 也存好了才回**（上圖）→ **同步複製（synchronous）**。客戶端拿到 ok 時，資料已經在至少兩個地方，primary 死了也不丟。代價：每次寫入多付一趟 primary↔backup 的往返，延遲上升，而且 backup 慢或死了，primary 就卡住不能回覆。

- **primary 自己存好就先回，複製在背景做** → **非同步複製（asynchronous）**。客戶端幾乎零等待。代價：ok 回出去的那一刻，資料**只在 primary 一個地方**。如果 primary 在複製追上之前就死了，這筆「已經跟客戶端說成功」的寫入，隨 primary 一起消失。這就是資料遺失視窗。

這兩個是同一根光譜的兩端，中間還有「半同步（semi-sync）」：等至少一個 backup ack 就回（MySQL 的 `rpl_semi_sync`）。取捨的本質永遠是：**你願意為「不丟資料」付多少延遲？**

## 同步 vs 非同步：資料遺失視窗

把非同步的資料遺失視窗畫成時間軸，這是本章最需要盯著看的一張圖：

```
非同步複製 + primary 在視窗內崩潰：

時間 ───────────────────────────────────────────────────►
primary: [收到 write(x=1)]──[本地存好]──[回 client "ok!"]──[開始複製]──X 崩潰
                                              │                    │
                                              │            複製訊息還在飛
client:  ──────────────────────────[拿到 ok，以為成功]──────────
                                              ▲
                              ┌───────────────┴────────────────┐
                              │  資料遺失視窗：ok 已回，但 x=1  │
                              │  只在(即將崩潰的)primary 上      │
                              └─────────────────────────────────┘
backup:  [x=0]────────────────────────────────────────────[x=0] 仍是舊值
                                                              │
                                          扶正 backup 當 primary → x=1 永遠消失
```

**視窗的長度 = 複製延遲。** 跨資料中心的非同步複製，這個視窗可能是幾十上百毫秒；主從之間網路一抖，視窗可能拉到幾秒。任何在這個視窗內崩潰的 primary，都會帶走視窗內所有「已回 ok 但沒複製出去」的寫入。

這不是理論。GitHub 2018 年那場著名的 24 小時故障，根因之一就是跨區 MySQL 的複製拓撲在網路分區後做了故障切換，非同步複製的落後導致資料需要人工調和。「已經告訴使用者成功、事後卻發現丟了」是這類架構最傷信任的失敗。

同步複製堵住了這個視窗——ok 回出去時資料保證在兩個地方。但它把問題換成了**可用性**：backup 死了或慢了，primary 要不要繼續等？等，就跟著卡死（一個 backup 的故障拖垮整個寫入路徑）；不等（退化成非同步），視窗又回來了。**你沒辦法同時要到「零遺失」和「backup 故障不影響 primary」——這是 Ch 10 CAP 在複製層的直接投影。**

## 底層機制：dsim 上跑一個主從，primary 崩潰後 backup 接手

光講不夠，我們在 `dsim` 上把它跑起來。一個 primary（node 0）+ 一個 backup（node 1），支援同步/非同步兩種模式。primary 崩潰後，一個外部 controller 送 `promote` 把 backup 扶正。

節點只透過 `net.Send` 溝通（Ch 0 的鐵律），訊息型別如下：

```go
type msgKind int
const (
    clientWrite msgKind = iota // client -> primary : 一次寫入
    replicate                  // primary -> backup : 複製這筆
    replicaAck                 // backup -> primary : 複製完成
    promote                    // controller -> backup : 你現在是 primary
)

type entry struct { ver int; val string }
type repMsg struct { kind msgKind; e entry }

type replica struct {
    id, peer NodeID
    role     string // "primary" | "backup"
    sync     bool   // 同步複製：等 backup ack 才算對客戶端 durable
    log      []entry
    applied  string // 已套用的值（"committed" 狀態）
    appVer   int
    pendingAcked map[int]bool // primary-only：sync 模式下等 backup ack 的寫入
}
```

核心邏輯在 `OnMessage`。同步模式下，primary 收到 `clientWrite` **不立刻**當作 durable，而是先 `replicate` 給 backup，等 `replicaAck` 回來才 apply（代表「現在才能安心回客戶端 ok」）。非同步模式下，primary 收到就 apply、就當作可以回 ok，複製在背景走：

```go
func (r *replica) OnMessage(m Message, net *Net) {
    rm := m.Payload.(repMsg)
    switch rm.kind {
    case clientWrite:
        if r.role != "primary" { return } // backup 不收客戶端寫入
        r.log = append(r.log, rm.e)
        if r.sync {
            net.Send(Message{From: r.id, To: r.peer, Payload: repMsg{replicate, rm.e}})
            // 注意：這裡「還沒」對客戶端 ok，等 replicaAck
        } else {
            r.applied, r.appVer = rm.e.val, rm.e.ver // 非同步：立刻 apply + 回 ok
            net.Send(Message{From: r.id, To: r.peer, Payload: repMsg{replicate, rm.e}})
        }
    case replicate: // backup 端
        r.log = append(r.log, rm.e)
        r.applied, r.appVer = rm.e.val, rm.e.ver
        net.Send(Message{From: r.id, To: r.peer, Payload: repMsg{replicaAck, rm.e}})
    case replicaAck: // primary 端（sync）
        if r.sync && !r.pendingAcked[rm.e.ver] {
            r.pendingAcked[rm.e.ver] = true
            r.applied, r.appVer = rm.e.val, rm.e.ver // 現在才 durable，才回 ok
        }
    case promote:
        r.role = "primary" // 扶正：backup 用它「已複製到」的狀態接手
    }
}
```

`promote` 是故障切換的核心：backup 收到後把自己 role 改成 primary，然後**用它手上已經複製到的狀態**開始服務。它有多新，完全取決於崩潰前複製追到哪。

跑兩個情境。情境一：同步複製，寫兩筆（A、B）後 primary 崩潰、扶正 backup：

```
=== 1. SYNC replication, then primary crash + failover ===
[t=6] backup  1 replicated ver=1 val="A"
[t=9] primary 0 got backup ack ver=1 -> NOW ack client (SYNC durable)
[t=13] backup  1 replicated ver=2 val="B"
[t=15] primary 0 got backup ack ver=2 -> NOW ack client (SYNC durable)
-- before crash: primary applied="B"(ver2), backup applied="B"(ver2)
-- primary 0 CRASHED --
[t=21] backup  1 PROMOTED to primary, serving applied="B"(ver2) [no data lost, sync]
```

看清楚時序：ver=1 的 ok 直到 `t=9`（backup ack 回來）才對客戶端發出，不是 primary 本地存好的 `t=6`。這一趟延遲就是同步複製的價錢。回報：primary 崩潰時 backup 已經有 ver2，扶正後無縫服務 B，**零遺失**。

情境二：非同步複製，primary 剛回 ok 就在複製飛行途中崩潰：

```
=== 2. ASYNC replication data-loss window ===
[t=1] primary 0 applied ver=1 val="X" (ASYNC ack client now)
-- primary applied="X"(ver1), backup applied=""(ver0) <- backup behind
-- primary 0 CRASHED mid-replication (async) --
[t=2] backup  1 PROMOTED to primary, serving applied ""(ver0)  ** ver1 X was ACKed to client but LOST **
```

這就是資料遺失視窗的血淋淋現場：`t=1` primary 已經對客戶端回了 ok（X 寫成功了），但複製訊息還在飛，backup 仍是 ver0。`t=1` 之後 primary 崩潰，扶正的 backup 只有 ver0——**那筆已經跟客戶端說成功的 X，永遠消失了**。程式沒有 bug，這是非同步複製的固有語意。

## Chain Replication：兼顧吞吐與強一致

主從的一個變體值得專門提：**chain replication（鏈式複製）**（van Renesse & Schneider, OSDI 2004）。它把副本排成一條鏈，而不是「一個 primary 對多個 backup」的星狀。

```
   寫入 ─────────────────────────────────────────► 讀取
        ┌──────┐    ┌──────┐    ┌──────┐
   write│ HEAD │───>│ MID  │───>│ TAIL │read
        └──────┘    └──────┘    └──────┘
        寫只進頭       中間        讀只出尾
        沿鏈往下傳播，到 TAIL 才算 committed
```

規則：**寫入只從 HEAD 進**，沿鏈一路傳到 **TAIL**；**讀取只從 TAIL 出**。一筆寫入抵達 TAIL 才算 committed，TAIL 回 ok。

為什麼這樣安排能兼顧吞吐與強一致？

- **強一致（linearizable）**：讀只走 TAIL，而 TAIL 是鏈上「最落後」的節點——但它上面的每個值都已經流經整條鏈、被所有副本存過了。所以從 TAIL 讀到的任何值，一定已經在全部副本上 durable。讀寫都收斂到 TAIL 這一個序列化點，天然 linearizable，不需要星狀主從那種「讀 primary 才保證最新、讀 backup 可能舊」的糾結。

- **吞吐**：星狀主從裡，primary 要同時扛「接客戶端寫 + 複製給所有 backup + 服務讀」，是瓶頸。chain 把工作拆開了——HEAD 只管收寫、TAIL 只管服務讀、中間只管轉發，負載分散到不同節點。讀完全不碰 HEAD。

**CRAQ**（Chain Replication with Apportioned Queries, USENIX ATC 2009）再進一步：讓**鏈上任何節點都能服務讀**，而不只是 TAIL。做法是每個節點為每個 key 標記「clean（乾淨）/ dirty（有未 commit 的新版本）」——讀到 clean 的直接回，讀到 dirty 的就去問 TAIL「現在 committed 到哪版」。這樣在讀多寫少的負載下，讀吞吐幾乎隨副本數線性擴展，同時仍維持強一致。這是「強一致不等於讀吞吐一定爛」的漂亮反例。

代價：鏈越長，寫入延遲越高（要流過每一節點）；而且任何一個中間節點故障，鏈就斷了，要重新配置（誰接誰）——這個重配置本身又是一個需要協調的動作。真實系統（如 Object storage、部分 FoundationDB 的儲存層）用它，但都配一個獨立的協調服務（通常是共識，Ch 15+）來管鏈的成員。

## 故障切換與腦裂：兩個 primary 的災難

現在來到本章真正凶險的部分。primary 死了，要扶正一個 backup。問題是——**你怎麼知道 primary 真的死了？**

這是分散式系統最深的坑之一（Ch 1、Ch 2 已埋伏筆）：**你無法區分「一個節點死了」和「一個節點只是很慢、或跟你之間的網路斷了」。** 從 backup 或監控的視角，「primary 沒回應」可能是：primary 崩潰了、primary 在 GC 停頓、primary 跟你之間網路分區了但它跟客戶端還通著。這三種情況**在網路上長得一模一樣**。

腦裂就從這裡誕生：

```
   原本：           網路分區發生：
                    ┌─── 客戶端群 A ───┐   ┌─── 客戶端群 B ───┐
   client           │                  │   │                  │
     │              │   舊 primary     │ X │   monitor 看不到  │
     ▼              │   (其實還活著!)  │ 分 │   舊 primary      │
  primary(0)        │   繼續收 A 的寫  │ 區 │   -> 扶正 backup  │
     │              │                  │   │   新 primary 收 B │
  backup(1)         └──────────────────┘   └──────────────────┘

   結果：兩個 primary 同時存在，各自接受寫入 = SPLIT-BRAIN
         群 A 寫進舊 primary，群 B 寫進新 primary，兩份資料分岔
         heal 之後：x 到底是多少？無解，資料已經衝突
```

腦裂的定義：**因為誤判 primary 死亡而扶正了新 primary，導致舊 primary（沒死透）與新 primary 同時存在、同時接受寫入。** 兩個「老大」各自序列化各自那群客戶端的寫入，資料徹底分岔。等網路 heal，你手上有兩份無法合併的歷史——這是複製系統最嚴重的故障，比單純丟資料還糟，因為它默默地讓兩邊都「成功」，事後才發現不一致。

Redis 的 Sentinel、早期各種土法煉鋼的 MySQL VIP 切換，都出過腦裂事故。

## 怎麼堵腦裂：fencing、lease、多數決

腦裂的根源是「兩個 primary 同時活著並被接受」。堵它的所有手段，本質都是**確保任一時刻至多一個 primary 能真正生效**。三個層次：

**1. Lease（租約）**：primary 的「老大」身分不是永久的，是一份**有時限的租約**。primary 必須週期性地續租（跟一個協調者續、或跟多數 backup 續），續不到（因為網路斷了）租約就過期，它**必須自動退位、停止接受寫入**。新 primary 只有在確定舊租約過期後才上任。這樣即使舊 primary 沒崩潰、只是被分區了，它也會因為續不到租而自我了斷——不會出現兩個同時有效的租約。關鍵：這依賴時鐘，租約時長要留足夠 margin 應付時鐘漂移（Ch 4 的謊言在這咬人）。

**2. Fencing（隔離）**：就算舊 primary 沒及時退位，也要在**它下游的地方**把它擋掉。做法是給每次 leadership 換代一個單調遞增的 **epoch / fencing token**（1, 2, 3…）。新 primary 上任拿到更大的 token。所有下游（儲存、backup、客戶端）**只接受 token ≥ 已見過的最大值**的請求。舊 primary 帶著過期的小 token 來寫，直接被拒。這是「即使你以為你是 primary，別人也不認你」的最後一道牆。ZooKeeper 的 `zxid`、各種系統的 epoch number 都是這個。

```
   舊 primary (token=5)  ──write(token=5)──> 儲存 [已見過 token=6] ──拒絕! stale token
   新 primary (token=6)  ──write(token=6)──> 儲存 [接受，更新到 6]
```

**3. 多數決（quorum / majority）**：這是最根本的解，也是 Part 3 的核心。規則：**一個節點只有在拿到「多數節點（>N/2）承認它是 primary」時，才能當 primary。** 為什麼這能堵腦裂？因為**兩個不相交的多數不可能同時存在**——N 個節點裡，任兩個「超過一半」的集合必然有交集，交集裡的節點不會同時承認兩個 primary。分區時，至多一邊能湊到多數，另一邊（少數側）湊不到、當不成 primary、只能停止服務。**用「多數」把「至多一個 primary」變成一個數學保證，而不是靠時鐘或運氣。**

這三者不是互斥的，成熟系統疊著用：多數決選出唯一 primary（epoch 遞增），primary 持有 lease 避免頻繁重選，下游用 fencing token 擋掉漏網的舊 primary。而「怎麼讓一群會失敗的節點對『誰是 primary』達成多數共識」——正是**共識演算法（consensus）**要解的問題。主從複製要做對故障切換，最終繞不開共識。這就是為什麼 Ch 15 開始我們要花整個 Part 3 打磨 Paxos 和 Raft：**它們是把「選出唯一 primary、且分區時安全」這件事做對的唯一嚴謹辦法。**

## 對比與取捨

| 面向 | 同步複製 | 非同步複製 | 半同步 |
|---|---|---|---|
| 寫入延遲 | 高（等 backup ack） | 低（primary 本地就回） | 中（等 1 個 backup） |
| 資料遺失視窗 | 無（ok 時已在 ≥2 處） | 有（= 複製延遲） | 小（至少 1 副本有） |
| backup 故障影響 | primary 卡住/退化 | primary 不受影響 | 退化成非同步 |
| 典型系統 | 金融、強一致要求 | MySQL 預設、跨區複製 | MySQL semi-sync |

| 故障切換防護 | 擋什麼 | 依賴 | 弱點 |
|---|---|---|---|
| Lease | 舊 primary 分區後自己退位 | 時鐘（要防漂移） | 時鐘偏差 → margin 難抓 |
| Fencing token | 漏網舊 primary 的寫 | 下游檢查 token | 下游得全都認 token |
| 多數決 | 從根本上禁止兩個 primary | 共識演算法 | 需要 >N/2 存活、較複雜 |

## 踩雷集錦

1. **「非同步複製 = 最終會複製過去，所以沒差」——錯得離譜。** 差別在「primary 崩潰的時機」。若 primary 在複製追上前崩潰，那些「已回客戶端 ok」的寫入**永久消失**，不是「晚點會到」。非同步複製的正確心智是：**你回給客戶端的 ok，在同步複製之前，是一張可能跳票的支票。** 對「絕不能丟」的資料（付款、訂單），非同步複製配自動故障切換是危險組合。

2. **「backup 落後一點沒關係，反正故障切換時它會追上」——不會。** 故障切換發生在 primary **已經死了**的時候，死掉的 primary 沒辦法再把落後的部分傳給 backup。扶正的瞬間，backup 有多新就是多新，缺的就是缺的。落後量（replication lag）直接等於故障切換時的潛在遺失量。所以生產系統要**持續監控 replication lag**，lag 大到超過你能容忍的遺失視窗就要告警。

3. **「primary 沒回應就是死了，趕快扶正 backup」——這正是腦裂的配方。** 沒回應 ≠ 死亡，可能只是慢或網路分區。無腦地「偵測到沒回應就自動扶正」，會在分區時造出兩個 primary。正確做法必須有「確認舊 primary 真的退場」的機制（lease 過期 + fencing + 多數決），寧可多等一個 lease 週期，也不能貿然扶正。**故障切換的難點從來不是『怎麼扶正 backup』，而是『怎麼確定舊 primary 不會再搗亂』。**

4. **「讀 backup 分攤 primary 壓力，反正資料一樣」——資料不一樣。** backup 是**非同步**追上的（大多數主從的預設），讀 backup 可能讀到舊值（stale read）。這在「讀自己剛寫的」場景會炸：使用者改了資料、頁面重整走到 backup、看到舊值以為沒存成功。要嘛強制關鍵讀走 primary，要嘛提供 read-your-writes 保證（Ch 9 的 session 一致性）。**「有多副本可以讀」和「讀到的是最新的」是兩回事。**

5. **「有 fencing token 就不用 lease / 多數決了吧」——三者管的層次不同，別互相替代。** fencing 是「下游擋 stale 請求」的最後一道牆，但它不決定「誰該當 primary」；lease 讓舊 primary 主動退位，但依賴時鐘不夠硬；只有多數決能從根本保證「至多一個 primary 被選出」。缺了多數決，你可能**同時選出兩個都持有『看似有效』token 的 primary**（因為沒有一個權威的、防分區的選舉）。這也是為什麼真正嚴謹的系統底層都跑共識。

## 進階：再往深一層

- **複製拓撲不只主從單鏈**：single-leader（主從）之外還有 **multi-leader**（多個 primary，各自接受寫入再互相複製，跨資料中心寫入友善但要處理寫寫衝突→Ch 14）和 **leaderless**（Dynamo 風格，沒有 primary，靠 quorum→Ch 13）。主從是三者中最好推理但可用性最受單點限制的。Kleppmann DDIA 第 5 章把這三種拓撲的取捨講得最透。

- **同步複製的「等幾個」是可調的**：不必「等全部 backup」（太脆弱，一個慢節點拖垮全部）也不必「一個都不等」（非同步）。真實系統設一個 `min_sync_replicas`：等 k 個 backup ack 就回。這其實已經滑向 quorum 的思路了（Ch 13）——「等多數」是這個旋鈕最有理論支撐的一個設定，因為它同時給你「不丟資料」和「容忍少數故障」。

- **故障切換的「新 primary 選誰」有講究**：多個 backup 落後程度不同，該扶正**最新的那個**（複製追最遠、遺失最少）。但「誰最新」本身要協調確認（不能問死掉的舊 primary），這又回到共識。Raft 的「選舉限制」（Ch 22）——只有 log 夠新的節點才能當選 leader——正是把「扶正最新的 backup」這件事做成 safety 保證的精髓。

- **腦裂在真實系統的花式變種**：除了網路分區，長 GC 停頓（primary 停頓超過 lease，被判死扶正 backup，然後 primary 醒來還以為自己是老大）、VM 被 hypervisor 暫停後恢復、時鐘跳變導致 lease 誤判，都能觸發腦裂。Kleppmann 那篇《How to do distributed locking》（延伸閱讀）用一個 GC 停頓造成 fencing 失效的例子，把「為什麼 lease 一定要配 fencing token」講到骨子裡，強烈建議讀。

## 本章重點整理

- **主從複製**：指定一個 primary 接受所有寫入、序列化順序，backup 被動照抄。把「多副本一致」化簡成「一個人說了算」，代價是 primary 的單點故障。
- **同步 vs 非同步**：差別在 primary「何時回客戶端 ok」。同步等 backup 存好才回（無遺失視窗，但 backup 故障拖累可用性）；非同步本地存好就回（低延遲，但有等於複製延遲的**資料遺失視窗**）。
- **資料遺失視窗**：非同步複製下，「已回 ok 但還沒複製出去」的寫入，會隨崩潰的 primary 永久消失。視窗長度 = replication lag，必須監控。
- **chain replication / CRAQ**：把副本排成鏈，寫進 HEAD、讀出 TAIL（CRAQ 讓任何節點可讀），在 TAIL 這個序列化點天然 linearizable，同時分散負載提升吞吐——強一致不等於讀吞吐爛。
- **腦裂（split-brain）**：因誤判 primary 死亡而扶正新 primary，導致兩個 primary 同時接受寫入、資料分岔。根源是「死了」和「慢/分區」在網路上無法區分。
- **堵腦裂三層**：lease（舊 primary 分區後自己退位）、fencing token（下游擋 stale 請求）、**多數決**（從數學上保證至多一個 primary，因為兩個不相交的多數不存在）。做對故障切換最終繞不開共識——這是 Part 3 的動機。

## 自我檢核

- [ ] 不看圖，我能畫出非同步複製的「資料遺失視窗」，並說出視窗長度等於什麼
- [ ] 我能解釋為什麼同步複製「不丟資料」的代價是「backup 故障會拖累 primary」，並把它連回 CAP
- [ ] 我能說清楚 chain replication 為什麼「讀走 TAIL」就能保證讀到的值已在所有副本 durable
- [ ] 我能用「死了 vs 慢/分區無法區分」講清楚腦裂是怎麼發生的，而不只是背「兩個 primary」
- [ ] 我能分別說出 lease、fencing token、多數決各堵腦裂的哪一環，以及為什麼三者不能互相替代
- [ ] 我能解釋「為什麼多數決能保證至多一個 primary」（提示：兩個超過一半的集合必相交）

## 延伸閱讀

- **[Chain Replication for Supporting High Throughput and Availability](https://www.cs.cornell.edu/home/rvr/papers/OSDI04.pdf)** — van Renesse & Schneider, OSDI（2004）
  - **這篇說什麼**：chain replication 的原始論文，證明它同時給出強一致與高吞吐，並分析各種節點故障下的鏈重配置
  - **讀哪裡**：Section 2（協定）與 Section 3（故障處理）；重點看它怎麼論證「讀 TAIL 即 linearizable」
  - **前提**：讀懂本章主從與強一致定義即可；配 CRAQ 論文一起看更完整

- **[How to do distributed locking](https://martin.kleppmann.com/2016/02/08/how-to-do-distributed-locking.html)** — Martin Kleppmann（2016）
  - **這篇說什麼**：用一個 GC 停頓的例子，示範沒有 fencing token 的分散式鎖/lease 怎麼失效、造成兩個持有者，把「lease 一定要配 fencing」講到見骨
  - **讀哪裡**：整篇，尤其「Making the lock safe with fencing」那一節的那張時序圖
  - **為什麼值得讀**：本章「堵腦裂三層」的 fencing 部分就是濃縮自這篇的論證

- **《Designing Data-Intensive Applications》第 5 章 "Replication"** — Martin Kleppmann（2017）
  - **這章說什麼**：single-leader / multi-leader / leaderless 三種複製拓撲的完整取捨，同步/非同步、複製延遲問題、故障切換的陷阱
  - **讀哪裡**："Leaders and Followers" 到 "Problems with Replication Lag" 幾節，直接對應本章
  - **前提**：無；這是本課 Part 2 的主參考，讀完本章正好接它把拓撲補齊

主從複製把寫入收斂到單一 primary，簡單好推理，但單點故障和腦裂的陰影始終在。下一章我們走向光譜另一端——**乾脆不要 primary**，讓每個副本都能讀寫，用 quorum 的數學（R+W>N）來保證讀到最新。

→ [Ch 13 Quorum 複製](./13-quorum-replication.md)
