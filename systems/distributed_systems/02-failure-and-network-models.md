# Ch 2 — 失敗與網路模型

> **目標**：把上一章的「失敗」從直覺升級成**嚴謹的分類**。搞清楚三組正交的模型軸：（1）節點**失敗模型**——crash-stop、crash-recovery、omission、Byzantine；（2）**網路時序模型**——synchronous、asynchronous、partial synchrony；（3）**連結抽象**——fair-loss link 如何一層層蓋成 reliable link。最後把每一個抽象精準對應到 Ch 0 模擬器的旋鈕，並預告失敗偵測器（failure detector）。這是全書所有協定的「假設清單」——一個協定能不能容錯，取決於它假設對手有多壞、網路有多爛。

> **環境**：Go 1.18.1, WSL2/Linux x86-64。本章的 dsim 示範在 WSL 真跑。

## 為什麼需要這個？

論文裡每個容錯協定的開頭都有一段「系統模型（system model）」，長這樣：「我們假設 crash-stop 失敗、部分同步網路、至多 f 個節點失效、reliable point-to-point links……」新手總會跳過這段直接看演算法。**這是最大的錯誤。**

因為容錯不是絕對的。「這個協定容錯」這句話沒有意義——正確的問法是：**它容忍哪一種失敗、容忍幾個、在什麼網路假設下？** Raft 容忍 crash，但一個會說謊的 Byzantine 節點能輕易讓它出錯；PBFT 能扛 Byzantine，但代價是需要更多節點、更多輪訊息。同一個「失敗」，模型不同，需要的協定就是天差地別。

所以在碰任何協定之前，我們得先建立一套精確的詞彙，來描述「對手（adversary）有多壞」和「環境有多惡劣」。這套詞彙就是失敗模型 + 網路模型。缺了它，你讀論文只能背結論，無法判斷「這個協定能不能用在我的場景」。這一章就是把 Ch 0 那五個模擬器旋鈕背後的理論，補成一張完整的地圖。

## 先建立直覺

一個分散式系統的「難度」由兩個獨立的維度決定，把它們畫成一張座標：

```
         失敗模型（節點會壞到什麼程度）
              惡意 ▲
         Byzantine │  · 最難：對手任意作惡（Part 5 PBFT）
                   │
        crash-recovery │  · 節點會死、會復活、可能丟狀態
                   │
          crash-stop │  · 節點死了就永遠不回來（最單純）
                   └──────────────────────────────▶
                    synchronous  partial-sync  asynchronous
                    （時序可預測）           （延遲無上界，最難）
                         網路模型（時序有多不可預測）

     越往右上角，越難。真實系統落在「crash-recovery × partial-sync」，
     這正是 Raft/Paxos 選擇的戰場。左下角（crash-stop × sync）簡單但不真實。
```

這兩個軸**正交**：你可以有「crash-stop 節點 + 非同步網路」，也可以有「Byzantine 節點 + 同步網路」。每個協定都在這張圖上占一個位置，宣告「我在這個格子裡保證正確」。位置越靠右上，協定越貴（更多節點、更多訊息輪、更慢），但能扛的壞事越多。

第三個東西——連結抽象（link abstraction）——不是座標軸，而是**工具**：底層網路只給你一個很爛的連結（會丟包），你透過重送、去重，一層層把它「升級」成你要的可靠連結，再拿去建協定。理解這個「層層堆疊」是後面所有可靠廣播、複製的地基。

## 失敗模型：節點會壞成什麼樣

我們從最溫和的失敗排到最惡毒的。一個協定「假設的失敗模型越強（越惡毒），它能容忍的現實就越多」，但也越難設計、越貴。

### crash-stop（fail-stop）：死了就不回來

最單純的模型。節點在某個時刻**停止**——不再收訊息、不再發訊息、不再運算——而且**永遠不會再回來**。它不會做錯事，只是安靜地消失。

```
Node C:  ──執行──執行── ✗（crash）
                        └─────永遠沉默，不再參與─────
```

好處是推理簡單：一個節點要嘛正確運作、要嘛徹底消失，沒有中間狀態。壞處是不真實——現實中機器當機後會**重啟**回來。但 crash-stop 是所有失敗模型的起點，很多論文先在這個模型下證明正確性。

### crash-recovery：會死，也會復活

更貼近現實。節點會 crash，但之後可能**重啟回來**繼續參與。麻煩在於：重啟後它的**記憶體（volatile state）沒了**，只剩寫進硬碟（stable storage）的東西。

```
Node C:  ──執行── ✗crash ──沉默── ↻restart ──繼續執行──
                  ↑                ↑
            記憶體狀態全丟    只能從硬碟讀回之前持久化的東西
```

這逼出一個核心設計問題：**哪些狀態必須在回應客戶端之前先寫進硬碟（persist）？** Raft 要求投票給誰、currentTerm、log 這些關鍵狀態在回應前落盤，就是為了應付 crash-recovery——重啟後才不會「忘記自己投過票」而投第二次，破壞安全性（Ch 22）。crash-recovery 是真實共識協定實際面對的模型。

### omission：訊息漏了

失敗不一定是節點整個死掉，也可能是**訊息層級**的遺漏。節點活著、運算正常，但：

- **send omission**：它想發的訊息沒發出去（發送端漏）。
- **receive omission**：別人發給它的訊息它沒收到（接收端漏）。

```
Node A ──發送──▶ [X 訊息漏在發送端]        ← send omission
Node A ──發送──▶ ═══網路═══▶ [X B 沒收到]  ← receive omission
```

從協定的角度，omission 常常**無法和 crash 區分**——「B 沒回應」到底是 B 死了（crash）還是只是這幾則訊息漏了（omission）？這正是上一章「慢與死不可區分」的另一個面向。實務上很多協定把 omission 當成暫時性的 crash 來處理（重送、超時、換 leader）。

### Byzantine：任意作惡

最惡毒的模型。一個 Byzantine 節點可以做**任何事**——發互相矛盾的訊息給不同人、偽造資料、假裝別人、聯合其他壞節點串謀、選在最糟的時機作惡。它不只是「壞了」，是「主動與你為敵」。

```
Byzantine Node X:
     對 A 說「我投給提案 1」  ┐
     對 B 說「我投給提案 2」  ├─ 同時發矛盾訊息，製造分歧
     偽造一則來自 C 的訊息    ┘
     選在系統最脆弱時集中作惡
```

名字來自 Lamport 1982 年的「拜占庭將軍問題」（Ch 32）：一群將軍要協調進攻，但其中有叛徒會傳假命令。要在 Byzantine 模型下達成一致，需要的節點數更多（容忍 f 個惡意節點要 3f+1 個節點）、訊息輪更多、還要密碼學簽章防偽造。這是 Part 5 的主題，也是區塊鏈共識的核心——**在一個對手可能任意作惡的公開網路裡達成一致**。

失敗模型的嚴重度階梯：

```
crash-stop  ⊂  crash-recovery  ⊂  omission  ⊂  Byzantine
（能容忍 Byzantine 的協定，自動能容忍前面所有較弱的失敗）
   簡單、便宜  ────────────────────────────▶  複雜、昂貴、需密碼學
```

## 網路時序模型：時間有多不可預測

失敗模型講「節點」，網路模型講「訊息傳遞的時序保證」。這決定了你能不能靠「等一段時間沒回應就判定它死了」這種手段。

### synchronous：時序完全可預測

存在一個**已知的上界** Δ：任何訊息送出後，最多 Δ 時間內一定送達；每個節點的處理速度也有已知上界。

在同步模型裡，失敗偵測是**可靠的**：我送請求，等 `2Δ + 處理時間`，還沒回應——那它**一定**死了（因為活著的話一定在這時限內回）。這讓協定設計簡單很多。問題是：**真實網路不是同步的**。你設不出一個 Δ 讓「超過就一定是死了」永遠成立——總有一次 GC 暫停、一次網路抽風讓延遲爆表。同步模型是理論上的理想國，不是現實。

### asynchronous：延遲沒有任何上界

另一個極端。訊息**終究會送達**（不會永遠丟），但**延遲沒有任何上界**——可以是 1 毫秒，也可以是一年，你事先不知道。節點處理速度也沒有上界。

在非同步模型裡，**失敗偵測不可能可靠**：對任何 timeout `T`，都存在「訊息延遲剛好超過 T 但對方其實活著」的執行，你必然誤判。這就是上一章「慢與死不可區分」的嚴格版。非同步是最保守、最貼近「最壞情況」的模型——**FLP 不可能定理（Ch 16）就是在這個模型下證明「連 crash 都容忍不了的共識是不可能的」**。它太悲觀，以至於在它之下幾乎什麼強保證都做不到。

### partial synchrony：現實的甜蜜點

真實網路既不是同步（你設不出可靠的 Δ）也真的能運作（不像非同步那麼絕望）。Dwork、Lynch、Stockmeyer 在 1988 年的 **DLS 論文**給了介於中間的模型——**部分同步（partial synchrony）**：

> 系統**大部分時候**是同步的（延遲有界），但存在**未知的、有限長度的**非同步期（延遲爆表）。存在一個上界 Δ，但它要嘛數值未知、要嘛只在某個未知的時刻（GST, Global Stabilization Time）之後才開始成立。

```
時間軸：
   ├──同步（延遲<Δ）──┤├─非同步期（抽風）─┤├──同步──┤├─非同步─┤
                                        ↑ GST 之後保證恢復同步

   協定策略：非同步期只保證「不做錯事」（safety），
             同步期才保證「做出進展」（liveness）。
```

這是天才的一步。它讓協定可以承諾：「我在非同步期絕不違反正確性（不會選出兩個 leader、不會丟已提交的資料），只是可能暫時卡住不出進展；一旦網路恢復同步，我保證繼續進展。」**安全性（safety）永遠成立，活性（liveness）只在網路夠好時成立。** Raft、Paxos、PBFT 全都活在這個模型裡。Ch 17（繞過 FLP）會講這個「拆分 safety 與 liveness」的手法為什麼能繞過 FLP 的不可能性。

## 連結抽象：從 fair-loss 一層層蓋出 reliable

前面兩軸是「假設」，這一節是「工具」——底層網路給的連結很爛，我們自己動手把它變好。這是分散式演算法教科書（如 Cachin/Guerraoui/Rodrigues 的《Reliable and Secure Distributed Programming》）的經典堆疊。

### 第一層：fair-loss link（公平丟失連結）

最底層的假設，弱到不能再弱，但**可實現**（大致對應 UDP）：

- **fair-loss（公平丟失）**：如果你**無限次**重送同一則訊息，它**至少會送達一次**。換句話說，它不會把某則訊息「永遠、每次都」丟掉——丟失是隨機而非針對性的。
- **finite duplication（有限重複）**：訊息不會被複製無限次。
- **no creation（不憑空創造）**：收到的訊息一定是有人真的送過的（網路不會無中生有）。

fair-loss 的白話：**單發一則可能丟；但你一直重送，總有一則會漏過去。**

### 第二層：stubborn link（固執連結）

在 fair-loss 之上加一個動作：**永不放棄地重送**。發送端把「還沒被確認的訊息」在每個 tick 都重送一次，直到天荒地老。

```
stubbornSend(m):
    while forever:
        fairLossSend(m)      ← 底層可能丟，沒關係，下輪再送
        wait one tick
```

由 fair-loss 的定義（無限重送必至少送達一次），stubborn link 保證：**只要接收端不死，訊息終將送達（可能重複很多次）**。它解決了「丟失」，但製造了「大量重複」——這是下一層要處理的。

### 第三層：reliable link（可靠連結）

在 stubborn link 之上加**去重（deduplication）**：接收端記住已經收過的訊息 ID，重複的直接丟棄。發送端收到 ack 後就停止重送。

```
reliableSend(m):  給 m 一個唯一 id；stubbornSend((id, m))；收到 ack 就停
reliableRecv:     收到 (id, m)：
                     若 id 沒見過 → 交付上層 + 記下 id + 回 ack
                     若 id 見過   → 只回 ack，不重複交付
```

這樣就得到 reliable link 的保證：**送出的訊息恰好被交付一次（只要收發雙方最終都不死）**——不丟、不重複。TCP 本質上就在做這件事（序號 + 重傳 + 累積確認）。

**這個「fair-loss → stubborn → reliable」的三層堆疊是整門課的縮影**：你永遠是在一個較弱的保證上，用「重送 + 去重 + 超時」湊出一個較強的保證。Ch 3 的 RPC 語意、Part 2 的可靠廣播、複製，全是這個模式的放大版。而 Ch 3 會親手指出：**這個「恰好一次」是在應用層用去重湊出來的近似，網路層本身給不了 exactly-once**。

### 用 dsim 真跑：在 50% 丟包上用 stubborn 蓋出可靠交付

我們拿 Ch 0 的模擬器，把丟包率開到 50%（模擬一個很爛的 fair-loss 網路），然後讓發送端固執地重送，看是否真能把全部訊息送達。

```go
// 在 dsim 上示範 stubborn link（丟包率 50% 的 fair-loss 網路）
type receiver struct {
    id   NodeID
    seen map[int]bool
}
func (r *receiver) OnMessage(m Message, net *Net) {
    seq := m.Payload.(int)
    r.seen[seq] = true
    net.Send(Message{From: r.id, To: m.From, Payload: seq}) // 回 ack（帶回 seq）
}
func (r *receiver) OnTick(now int, net *Net) {}

type stubbornSender struct {
    id, target NodeID
    acked      map[int]bool
    total      int
    sends      int
}
func (s *stubbornSender) OnMessage(m Message, net *Net) {
    s.acked[m.Payload.(int)] = true // 收到 ack
}
func (s *stubbornSender) OnTick(now int, net *Net) {
    for seq := 0; seq < s.total; seq++ {
        if !s.acked[seq] {                 // 每個 tick，把還沒 ack 的全部重送
            net.Send(Message{From: s.id, To: s.target, Payload: seq})
            s.sends++
        }
    }
}

func main() {
    net := NewNet(3)
    net.SetDropRate(0.5) // fair-loss：一半機率丟
    s := &stubbornSender{id: 0, target: 1, acked: map[int]bool{}, total: 5}
    r := &receiver{id: 1, seen: map[int]bool{}}
    net.Add(0, s); net.Add(1, r)
    net.Run(60)
    // ... 統計輸出 ...
}
```

真跑（WSL, Go 1.18.1）：

```
fair-loss 丟包率 50% 下，用 stubborn 重送：
  想送 5 則；receiver 收到 5 種；sender 確認 ack 5/5；實際送出 22 次；Dropped=14
  結論：即使一半訊息被丟，靠不斷重送，全部 5 則最終都送達
        （reliable link 就是這樣在 fair-loss 上蓋出來的）
```

看那個數字：想送 5 則，實際動用了 **22 次** send、被網路丟掉 **14 次**，才把 5 則全部確認送達。這就是可靠交付的真實代價——**可靠性是用重複的頻寬換來的**。而 receiver 的 `seen` 是個 set，收到重複的 seq 不會重複交付上層，這就是去重（reliable link 那一層）在做的事。

## 對照 dsim 旋鈕：理論與模擬器的一一對應

Ch 0 那五個失敗注入 API，現在可以用本章的詞彙精確定位它們模擬的是哪種失敗：

| dsim 旋鈕 | 對應的模型概念 | 語意 |
|---|---|---|
| `SetDropRate(p)` | **omission**（訊息遺漏）、fair-loss link | 每則訊息獨立地以機率 p 遺失；重送終會過去 → fair-loss |
| `Crash(id)` | **crash-stop** | 該節點停止收發，且（不 Restart 的話）永不回來 |
| `Crash` + `Restart` | **crash-recovery** | 死後可復活；但 dsim 的節點狀態在記憶體，模擬「丟失 volatile state」要自己在 Restart 時清 |
| `Partition(g1, g2)` | **非同步 / 不可達**（asynchrony 的極端：延遲=∞） | 跨群組訊息全丟，等同「這對節點間延遲無上界」；`Heal` 恢復 |
| `SetLatency(min,max)` | 網路時序模型的旋鈕 | `(0,0)` 近似 synchronous；範圍拉大 + partition 近似 partial synchrony |

dsim **沒有內建 Byzantine**——因為 Byzantine 不是「網路對訊息做壞事」，而是「節點本身作惡」。要模擬它，你得寫一個**故意發矛盾訊息、偽造來源**的節點型別（那是它自己的 `OnMessage`/`OnTick` 邏輯），而不是調網路旋鈕。Part 5 的 PBFT 練習就會這樣寫一個 Byzantine 節點來攻擊協定。

一個實測示範，把幾種失敗並排跑，看送達統計怎麼變（seed 固定，可重現）：

真跑（WSL, Go 1.18.1，送 10 則訊息、每則期待一個 ack）：

```
送 10 則訊息，觀察不同失敗模型下的送達情況：
(a) 無失敗 baseline             echoer 收到=10  sender 收到 ack=10  Delivered=20 Dropped= 0
(b) omission 丟包 30%          echoer 收到= 7  sender 收到 ack= 5  Delivered=12 Dropped= 5
(c) crash-stop 收方當機          echoer 收到= 0  sender 收到 ack= 0  Delivered= 0 Dropped=10
(d) partition 兩邊隔離           echoer 收到= 0  sender 收到 ack= 0  Delivered= 0 Dropped=10
```

注意 (c) crash-stop 和 (d) partition 的送達統計**看起來一模一樣**（都是 0 送達、10 丟棄）——這正是本章最重要的一課的模擬版：**從發送端的視角，crash 和 partition 觀測上不可區分**。sender 只知道「沒人回我 ack」，它無從得知對方是死了（c）還是只是被隔離、其實活得好好的（d）。這個「不可區分」到了共識層會變成致命的——如果 sender 在 partition 時誤判對方死了而自己上位當 leader，另一邊那個活著的節點也在做同樣的事，腦裂就發生了（Ch 10 CAP、Ch 20 Raft 選舉都要處理這個）。

## 失敗偵測器：預告（接 Ch 17）

既然非同步網路裡「慢與死不可區分」是鐵律，那共識協定怎麼還能運作？答案是把「判斷誰死了」這件髒活抽象成一個獨立元件——**失敗偵測器（failure detector）**。

它是一個「會犯錯的預言機」：每個節點問它「你覺得誰死了？」它給一份**懷疑名單（suspected list）**。因為在非同步網路它不可能永遠正確，Chandra 與 Toueg（1996）用兩個性質來刻畫它有多好：

- **完整性（completeness）**：真的死掉的節點，最終會被懷疑。
- **準確性（accuracy）**：活著的節點，不要（老是）被誤懷疑。

實務上失敗偵測器就是「超時 + 心跳（heartbeat）」：節點定期發心跳，一段時間沒收到就把對方加進懷疑名單。它會誤判（把慢節點當死的），但協定被設計成**能容忍誤判**——誤判只傷活性（多換一次 leader、多花點時間），不傷安全性（絕不會因為誤判而做出矛盾決定）。這個「把不可靠的失敗偵測隔離起來、讓協定容忍它的錯誤」是繞過 FLP 的關鍵一招，Ch 17 會完整展開。現在你只要記住：**沒有完美的失敗偵測器，只有能容忍它犯錯的協定。**

## 對比與取捨

| 模型組合 | 失敗偵測 | 難度 | 代表協定 | 真實嗎 |
|---|---|---|---|---|
| crash-stop × synchronous | 可靠 | 最低 | 教科書起點 | 否（Δ 設不出來） |
| crash-recovery × partial-sync | 不可靠但可用 | 中 | **Raft / Paxos / VR** | **是（主流）** |
| omission × asynchronous | 不可能可靠 | 高 | 理論分析（FLP 的地盤） | 悲觀上界 |
| Byzantine × partial-sync | 不可靠 + 需防偽造 | 最高 | PBFT / HotStuff | 是（區塊鏈/高安全場景） |

實務系統幾乎都選 **crash-recovery × partial synchrony**——它在「夠真實」和「還做得出來」之間取得平衡。往上（Byzantine）只有在對手可能作惡（公開網路、跨組織）時才付那個代價。

## 踩雷集錦

1. **以為「容錯」是個絕對的形容詞**：錯誤直覺——這協定容錯，所以什麼壞事都扛得住。正確認識——容錯永遠是相對於一個明確的失敗模型與數量上界的。Raft 容忍 crash，但一個 Byzantine 節點就能讓它出錯；「容忍 f 個」超過 f 個一樣崩。讀協定第一件事是找出它的模型假設。

2. **把 omission 當成 crash 的特例而忽略它**：錯誤直覺——訊息漏了跟節點死了差不多，都當死的處理就好。正確認識——omission 是暫時的（重送會過去），crash-recovery 是「死了但會帶著殘缺狀態回來」。把 omission 當永久 crash 會讓你過早放棄一個其實還活著的節點；反過來把 crash 當暫時 omission 會讓你無限等一個死人。

3. **相信自己能在真實系統設出一個「可靠的」timeout**：錯誤直覺——我把 timeout 設夠大（比如 30 秒），超過就一定是死了。正確認識——真實網路是 partial synchrony，非同步期沒有上界，任何固定 timeout 都會在某次抽風時誤判。正確做法不是追求「不誤判」，而是設計成「誤判只傷活性、不傷安全性」。

4. **以為 partition 兩邊都會停下來**：錯誤直覺——網路斷了，大家都連不上，那大家都會卡住等。正確認識——partition 的每一邊都覺得「是對方掛了」，於是可能各自繼續做決定。少數派若不主動停下（放棄提供服務），就會和多數派產生矛盾的結果——這是 CAP 裡選 C 就得放棄 A 的根本原因（Ch 10）。

5. **在模擬器裡調旋鈕來製造 Byzantine**：錯誤直覺——把丟包、延遲開到最大就能模擬惡意節點。正確認識——Byzantine 是**節點主動作惡**（發矛盾訊息、偽造來源），不是網路對訊息做壞事。dsim 的旋鈕只能造 omission/crash/partition；Byzantine 得寫一個惡意的 Node 型別。搞混這兩者，你的「Byzantine 測試」其實只測到了 crash。

## 進階：再往深一層

**為什麼 Byzantine 容錯要 3f+1 個節點？** 直覺推導：假設只有 2f+1（一般 crash 容錯的數量）。壞節點有 f 個，網路可能讓 f 個好節點的訊息暫時到不了（不可區分於 crash）。那麼你「等到的多數」裡，可能有 f 個是壞節點的謊言 + 剩下的好節點——你無法確定多數就是誠實的。要在「f 個作惡 + f 個因網路暫時缺席」的情況下，仍保證你收到的回應裡誠實的占真正多數，就需要 3f+1 個節點、等 2f+1 個回應。這個計數論證是 PBFT（Ch 33）的骨架，也是為什麼 BFT 協定天生比 crash 容錯貴。

**部分同步不只一種。** DLS 論文其實給了兩個變體：(1) Δ 存在但數值未知；(2) Δ 已知，但只在某個未知的 GST（Global Stabilization Time）之後才開始成立。兩者都能繞過 FLP，但對協定的要求略有不同。現代共識論文（尤其 BFT 的 HotStuff、Tendermint）幾乎都採「GST 之後同步」這個表述——協定承諾「GST 之後一定達成進展」，GST 之前只保安全。理解這個表述，你才讀得懂 Part 5 那些協定的活性證明在講什麼。

## 本章重點整理

- 一個協定的容錯能力由三組正交的模型決定：**失敗模型**（節點壞的程度）、**網路時序模型**（時間的可預測性）、**連結抽象**（你手上有多可靠的連結）。
- 失敗模型嚴重度階梯：**crash-stop ⊂ crash-recovery ⊂ omission ⊂ Byzantine**。越往右越惡毒、越貴，能扛 Byzantine 的自動能扛前面全部。
- 網路模型：**synchronous**（有可靠 Δ，理想不真實）、**asynchronous**（延遲無上界，最悲觀，FLP 的地盤）、**partial synchrony**（DLS，大部分時候同步偶爾抽風，真實系統的甜蜜點）。
- 連結抽象三層堆疊：**fair-loss → stubborn（無限重送）→ reliable（去重 + ack）**。這個「用重送+去重把弱保證升級成強保證」是全課的縮影，也是「exactly-once 是應用層湊出來的近似」的伏筆（Ch 3）。
- dsim 旋鈕對應：`SetDropRate`=omission/fair-loss、`Crash`=crash-stop、`Crash+Restart`=crash-recovery、`Partition`=非同步/不可達。**Byzantine 得自己寫惡意節點，不是調旋鈕**。
- crash 與 partition 從發送端視角**觀測上不可區分**——這是腦裂與 CAP 取捨的根源。
- 失敗偵測器是「會犯錯的預言機」（心跳+超時），協定被設計成容忍它的誤判——誤判只傷活性、不傷安全性（接 Ch 17）。

## 自我檢核

- [ ] 不看筆記，我能把四種失敗模型（crash-stop / crash-recovery / omission / Byzantine）按嚴重度排序，並各說一個特徵
- [ ] 我能解釋 synchronous、asynchronous、partial synchrony 的差別，以及為什麼真實系統選 partial synchrony
- [ ] 我能說出 fair-loss → stubborn → reliable 每一層「加了什麼、解決了什麼、又製造了什麼新問題」
- [ ] 我能把 dsim 的五個旋鈕各對應到本章的哪個模型概念，並指出哪種失敗 dsim 的旋鈕做不到（Byzantine）
- [ ] 我能解釋為什麼 crash 和 partition 從發送端看不可區分，以及這件事怎麼導致腦裂
- [ ] 我能說出失敗偵測器的兩個性質（完整性、準確性），以及為什麼它注定會犯錯

## 延伸閱讀

- **[Consensus in the Presence of Partial Synchrony (DLS)](https://groups.csail.mit.edu/tds/papers/Lynch/jacm88.pdf)** — Dwork, Lynch, Stockmeyer, JACM（1988）
  - **這篇說什麼**：部分同步模型的原始論文，本章「partial synchrony」一節就是它的濃縮
  - **讀哪裡**：第 2 節（模型定義）與它區分的兩種部分同步變體；證明部分可略讀
  - **前提**：讀懂本章的三種網路模型即可；這篇是繞過 FLP 的理論基礎，Ch 16-17 會回來

- **《Reliable and Secure Distributed Programming》第 2 章** — Cachin, Guerraoui, Rodrigues（Springer, 2011）
  - **這章說什麼**：把失敗模型、網路模型、連結抽象（fair-loss/stubborn/reliable link）用最嚴謹的偽碼一層層建構，本章的連結堆疊就出自這套框架
  - **讀哪裡**：2.2（失敗模型）與 2.4.3–2.4.4（link abstractions）
  - **為什麼值得看**：這是把「抽象一層層堆疊」講得最清楚的教科書，Part 2 的可靠廣播也是它的體系

- **[Unreliable Failure Detectors for Reliable Distributed Systems](https://www.cs.utexas.edu/~lorenzo/corsi/cs380d/papers/p225-chandra.pdf)** — Chandra & Toueg, JACM（1996）
  - **這篇說什麼**：失敗偵測器的奠基論文，用完整性/準確性刻畫它，並找出「解共識所需的最弱失敗偵測器」
  - **讀哪裡**：第 1–2 節（動機與失敗偵測器分類）；本章的失敗偵測器預告出自這裡
  - **前提**：理解「非同步網路無法可靠偵測失敗」；Ch 17 會用到這篇的結果

有了失敗模型與網路模型的詞彙，下一章我們把焦點縮到最常見的通訊抽象——**RPC**，看它如何試圖把「遠端呼叫」偽裝成「本地呼叫」，以及這個謊言在部分失敗面前如何破產；並親手用 Go 真跑一個「逾時重送導致執行兩次」的 bug，再用去重修好它。

→ [Ch 3 RPC 與訊息語意](./03-rpc-and-message-semantics.md)
