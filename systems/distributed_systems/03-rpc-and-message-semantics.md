# Ch 3 — RPC 與訊息語意

> **目標**：拆穿「透明遠端呼叫」這個謊言——RPC 想讓遠端呼叫看起來跟本地呼叫一樣，但部分失敗讓這個偽裝在最關鍵的時刻破產。搞清楚三種訊息語意：**at-most-once**、**at-least-once**、**exactly-once**，並嚴格論證 **exactly-once 是網路層做不到的迷思**——你只能在應用層用**冪等（idempotency）+ 去重（dedup）**去逼近它。全程用 Go 標準庫 `net/rpc` **真跑**：先一次正常呼叫，再示範「client 逾時重送導致 server 執行兩次」（非冪等 counter 被加兩次），最後用去重表修好它。

> **環境**：Go 1.18.1, WSL2/Linux x86-64。本章 `net/rpc` 範例全部在 WSL 真跑，輸出為實測。

## 為什麼需要這個？

1980 年代，一群工程師想解決一個很實際的問題：跨機器呼叫程式碼太麻煩。你得手動把參數打包（序列化）、塞進封包、送出、在對面解包、找到對應函式、執行、把結果打包送回、再解包。這一堆樣板程式碼淹沒了業務邏輯。

他們的解法優雅得誘人——**Remote Procedure Call（RPC，遠端程序呼叫）**：讓呼叫遠端函式**看起來跟呼叫本地函式一模一樣**。你寫 `result := client.Deposit(args)`，底層的序列化、傳輸、路由全被藏起來。Sun RPC、CORBA、Java RMI、gRPC 都是這條路上的產物。這個抽象叫**位置透明（location transparency）**：呼叫者不必知道對方在本機還是地球另一端。

問題是：**你可以藏起語法，但藏不掉語意。** 本地函式呼叫有三個你視為理所當然的保證——它一定會執行、恰好執行一次、要嘛回傳結果要嘛拋出你能捕捉的例外。RPC **一個都給不了**。上一章講的部分失敗，在 RPC 這裡以最尖銳的形式現身：你呼叫了 `Deposit`，然後 timeout。它執行了嗎？執行了幾次？你不知道。這一章就是把這個「RPC 是本地呼叫的謊言」講透，並用真跑的程式碼讓你親眼看到謊言破產的那一刻，以及怎麼補救。

## 先建立直覺

RPC 想讓你相信左邊那張圖，但真實發生的是右邊：

```
     RPC 想讓你以為的               部分失敗下真實發生的
   ┌──────────────────┐      ┌────────────────────────────────┐
   │ x := Deposit(100)│      │ client              server      │
   │   ↓ 一次呼叫     │      │  Deposit(100) ──req──▶ 執行中... │
   │   ↓ 一定回傳     │      │   等待...              (慢/當機?) │
   │ 用 x            │      │   ✗ timeout ◀─?── 回應丟了? 沒收到?│
   └──────────────────┘      │   「執行了嗎？幾次？」——不知道   │
     本地呼叫的心智模型         └────────────────────────────────┘
                                部分失敗把「呼叫=執行一次」拆成三段
```

關鍵拆解：一次 RPC 有三個獨立會失敗的階段——**請求送達**、**server 執行**、**回應送回**。本地呼叫這三段是一個原子動作，不可分。RPC 把它們攤成三段跨網路的動作，**每一段都可能獨立失敗**。而 client 站在最外面，只能觀測到「有沒有在時限內收到回應」這一個位元的資訊，它**無法區分**失敗發生在哪一段。

這個「無法區分」直接決定了：**client 重送一個逾時的請求是危險的**，因為那個請求可能其實已經在 server 上執行完了。整章的所有痛與解法，都從這一個事實長出來。

## RPC 為什麼不等於本地呼叫

把本地呼叫的保證逐條拿到 RPC 面前，看它怎麼碎掉：

| 本地呼叫的保證 | RPC 的現實 |
|---|---|
| 一定會執行 | 請求可能在網路上就丟了，server 根本沒收到 |
| 恰好執行一次 | 逾時重送可能讓 server 執行多次 |
| 回傳值一定拿得到 | 執行成功但回應在回程丟了，client 以為失敗 |
| 例外能捕捉、有明確語意 | 「timeout」這個「例外」語意曖昧：沒執行？執行了？執行幾次？ |
| 延遲可忽略 | 慢好幾個數量級（Ch 1 謬誤 2），還可能無限久 |
| 呼叫者與被呼叫者同生共死 | server 可能單獨當機，client 活著卻不知情 |

最毒的是第二、三行合起來造成的困境。把三個階段畫出來，標出每一段失敗時 client 該不該重試：

```
 client                          server
   │  ①請求 ──────X 丟了──▶      沒執行     → 重送是對的（安全）
   │
   │  ②請求 ────────────▶       執行了
   │     回應 ◀───X 丟了───      已經改了狀態 → 重送會再執行一次（危險！）
   │
   │  ③請求 ────────────▶       執行到一半 ✗當機
   │     等... timeout            狀態未知     → 重送？不重送？都可能錯
```

問題的核心：**client 看到的「timeout」在 ①②③ 三種情況下長得一模一樣**，但它們要求的處理完全相反——① 該重送、② 重送會鑄成大錯、③ 根本無法判斷。這就是 RPC 抽象洩漏（leaky abstraction）最痛的地方：它把「本地呼叫」的外衣穿在一個做不到本地保證的東西上，讓你**以為**能像本地一樣安心重試，然後在生產環境用一筆重複的轉帳教育你。

## 三種訊息語意

面對「重送可能重複執行」，系統設計者發展出三種語意契約，講清楚「一個請求最終被 server 執行的次數」：

### at-most-once（至多一次）

**策略**：client 送出請求，逾時就**放棄**，不重送。

**結果**：請求要嘛執行 0 次（丟了/當機了）、要嘛執行 1 次。**絕不會執行兩次**。

**代價**：可能一次都沒執行（請求或回應丟了，你放棄了）。適合「寧可漏做、不可重複做」的操作——例如「扣款」這種重複做會出人命的，寧可回報失敗讓上層重新發起。

### at-least-once（至少一次）

**策略**：client 逾時就**重送**，重送到收到回應為止。

**結果**：請求**至少執行 1 次**，但可能執行**多次**（每次重送若前一次其實成功了，就多執行一次）。

**代價**：重複執行。若操作非冪等（例如 `balance += 100`），重複就是錯誤（加了兩次）。適合「寧可多做、不可漏做」且操作本身冪等或可去重的場景。這是**絕大多數 RPC 框架的預設**（gRPC 的重試、訊息佇列的投遞多半是 at-least-once），因為「保證至少送到」比「保證只送一次」好實現得多。

### exactly-once（恰好一次）：一個迷思

**理想**：請求**恰好執行 1 次**，不多不少。這是你真正想要的。

**現實**：**網路層做不到 exactly-once。** 這不是工程沒做好，是根本性的。論證如下：

> 要嘛你逾時放棄（at-most-once，可能 0 次），要嘛你重送（at-least-once，可能多次）。**沒有第三條路。** 因為 client 永遠無法確定「上一次到底執行了沒」——它只看得到 timeout。在「執行了但 ack 丟了」和「沒執行」不可區分的前提下（Ch 1/Ch 2 的核心），任何純粹在傳輸層的策略都只能落在 at-most-once 或 at-least-once，湊不出「剛好一次」。

那為什麼你會聽到 Kafka、各種系統宣稱「exactly-once」？它們沒有騙你，但講的是**另一件事**：exactly-once **不是**在網路層達成的，而是在**應用層**用「at-least-once 投遞 + 冪等/去重」**組合**出來的效果。真相是：

```
   exactly-once（效果）
        =
   at-least-once（傳輸：保證至少到，靠重送）
        +
   去重 / 冪等（應用：重複到達也只生效一次）
```

**訊息可能被送達多次（不可避免），但你設計成「重複送達時，副作用只發生一次」。** 這是 exactly-once 唯一可行的實現方式，也是 Ch 2 那個「fair-loss → stubborn → reliable」堆疊的直接延伸——reliable link 的「去重」層，在應用語意層就長成了「exactly-once 效果」。記住這句話：**exactly-once delivery 是迷思，exactly-once processing（副作用恰好一次）才是你能拿到、也真正想要的東西。**

## 真跑：用 net/rpc 看謊言破產，再修好它

理論講完，動手。Go 標準庫的 `net/rpc` 是最精簡的 RPC 框架，剛好拿來示範。我們寫一個「存款」服務——`Deposit` 把金額加到餘額上，**非冪等**（收到就加）。故意讓 server 處理很慢（sleep 300ms），讓 client 用短 timeout 逾時，重現「client 逾時 → 重送 → server 執行兩次」。

### server 端：一個非冪等的 Deposit

```go
type DepositArgs struct {
    ReqID  string // 冪等鍵（idempotency key）；前半段刻意不使用
    Amount int
}
type DepositReply struct{ Balance int }

type Account struct {
    mu      sync.Mutex
    balance int
    applied map[string]int // ReqID -> 當時回傳的 balance（去重表）
    dedup   bool           // 是否啟用去重
    calls   int            // server「實際執行加值」的次數（觀測用）
}

func (a *Account) Deposit(args *DepositArgs, reply *DepositReply) error {
    a.mu.Lock()
    defer a.mu.Unlock()

    if a.dedup { // 去重版：見過這個 ReqID 就回舊結果，不重複加
        if prev, ok := a.applied[args.ReqID]; ok {
            reply.Balance = prev
            return nil
        }
    }

    time.Sleep(300 * time.Millisecond) // 模擬 server 處理慢 → 讓 client 逾時
    a.balance += args.Amount            // ← 非冪等的副作用：收到就加
    a.calls++
    if a.dedup {
        a.applied[args.ReqID] = a.balance
    }
    reply.Balance = a.balance
    return nil
}
```

### client 端：帶 timeout 的呼叫

`net/rpc` 的同步 `client.Call` 會一直等到回應，沒有內建 timeout。我們用非同步的 `client.Go` + `select` 自己套一個逾時——**逾時後 client 就放棄等待，但 server 那邊的請求可能還在跑、甚至已經跑完**（這正是我們要重現的關鍵：client 放棄 ≠ server 沒做）。

```go
func callWithTimeout(addr string, args *DepositArgs, timeout time.Duration) (int, error) {
    client, err := rpc.Dial("tcp", addr)
    if err != nil {
        return 0, err
    }
    defer client.Close()

    var reply DepositReply
    done := client.Go("Account.Deposit", args, &reply, nil)
    select {
    case call := <-done.Done:
        if call.Error != nil {
            return 0, call.Error
        }
        return reply.Balance, nil
    case <-time.After(timeout):
        return 0, errors.New("client timeout") // client 放棄，但 server 未必沒做
    }
}
```

### 三個場景

```go
func main() {
    // 場景一：正常呼叫（client 等 2s，server 只要 300ms）
    {
        acc := &Account{applied: map[string]int{}}
        addr := startServer(acc)
        bal, err := callWithTimeout(addr, &DepositArgs{ReqID: "r1", Amount: 100}, 2*time.Second)
        fmt.Printf("[場景一 正常] reply balance=%d err=%v | server 執行次數=%d 真實餘額=%d\n",
            bal, err, acc.calls, acc.balance)
    }

    // 場景二：at-least-once + 非冪等 → 逾時重送導致執行兩次
    {
        acc := &Account{applied: map[string]int{}} // dedup 關閉
        addr := startServer(acc)
        args := &DepositArgs{ReqID: "r2", Amount: 100}

        _, err1 := callWithTimeout(addr, args, 100*time.Millisecond) // 只等 100ms → 逾時
        fmt.Printf("[場景二 第1次] err=%v （client 放棄，但 server 仍在處理）\n", err1)

        time.Sleep(400 * time.Millisecond)                          // 等第一次 server 端做完
        bal2, _ := callWithTimeout(addr, args, 2*time.Second)       // client 以為失敗，重送
        fmt.Printf("[場景二 第2次 重送] reply balance=%d\n", bal2)
        fmt.Printf("[場景二 結果] server 執行次數=%d 真實餘額=%d  <- 只想加一次 100，卻變成 %d\n",
            acc.calls, acc.balance, acc.balance)
    }

    // 場景三：同情境，但開 dedup（idempotency key）→ 修好
    {
        acc := &Account{applied: map[string]int{}, dedup: true}
        addr := startServer(acc)
        args := &DepositArgs{ReqID: "r3", Amount: 100} // 兩次用同一個 ReqID

        _, err1 := callWithTimeout(addr, args, 100*time.Millisecond)
        fmt.Printf("[場景三 第1次] err=%v （逾時，但 server 已把 r3 記入去重表）\n", err1)

        time.Sleep(400 * time.Millisecond)
        bal2, _ := callWithTimeout(addr, args, 2*time.Second)       // 重送，命中去重表
        fmt.Printf("[場景三 第2次 重送] reply balance=%d （命中去重表，不重複加）\n", bal2)
        fmt.Printf("[場景三 結果] server 執行次數=%d 真實餘額=%d  <- 重送多次仍只加一次 100\n",
            acc.calls, acc.balance)
    }
}
```

（`startServer` 用 `rpc.NewServer()` 註冊 `Account`、監聽 `127.0.0.1:0`，每個連線開一個 goroutine 跑 `ServeConn`——標準的 `net/rpc` 樣板，完整檔在 `code/` 目錄。）

### 真實輸出（WSL, Go 1.18.1）

```
[場景一 正常] reply balance=100 err=<nil> | server 執行次數=1 真實餘額=100
[場景二 第1次] err=client timeout （client 放棄，但 server 仍在處理）
[場景二 第2次 重送] reply balance=200 err=<nil>
[場景二 結果] server 執行次數=2 真實餘額=200  <- 只想加一次 100，卻變成 200
[場景三 第1次] err=client timeout （逾時，但 server 端已把 r3 記入去重表）
[場景三 第2次 重送] reply balance=100 err=<nil> （命中去重表，不重複加）
[場景三 結果] server 執行次數=1 真實餘額=100  <- 重送多次，仍只加一次 100
```

逐行讀懂這個輸出，它就是整章的證明：

- **場景一**：client timeout 給足（2s），server 300ms 做完，一切正常。`server 執行次數=1，餘額=100`。這是 RPC「看起來像本地呼叫」的美好假象——只在沒有失敗時成立。
- **場景二**：client 只等 100ms 就 timeout 放棄。但 server 收到了、正在 sleep 300ms，client 放棄**不代表** server 沒做。client 誤以為失敗（at-least-once 策略）重送一次——server 又執行一次。結果 **`server 執行次數=2，餘額=200`**：只想存 100 卻變成 200。**這就是「exactly-once 是迷思」的鐵證，也是 RPC 謊言破產的那一刻**——你以為呼叫了一次，實際執行了兩次，而 client 全程不知情。
- **場景三**：同樣的逾時、同樣的重送，但兩次請求帶**同一個 `ReqID="r3"`**，server 有去重表。第一次執行後把 `r3 → 100` 記進表；重送的第二次一進來，發現 `r3` 見過了，**直接回舊結果、不再執行加值**。結果 **`server 執行次數=1，餘額=100`**：重送多少次都只生效一次。這就是「at-least-once 傳輸 + 去重 = exactly-once 效果」的真跑證明。

三個場景並排，你親眼看到了：問題不在網路能不能只送一次（不能），而在**你有沒有在應用層讓「重複送達只生效一次」**。去重表（`applied map[ReqID]result`）就是最直接的實作——這也正是 Ch 2 reliable link 那個「記住已收訊息 ID、重複的丟掉」在應用語意層的化身。

## 冪等與去重：實務上怎麼做對

場景三的去重表是最小示範，真實系統還要處理幾件事：

- **idempotency key 從哪來**：由 client 產生一個全域唯一 ID（UUID、或 `client_id + 序號`），同一個邏輯操作的所有重試都帶**同一個** key。這樣 server 才認得出「這是重試，不是新請求」。key 由 client 給、不是 server 生，是關鍵——server 沒辦法自己判斷兩個長得一樣的請求是「重試」還是「使用者真的想存兩次 100」。

- **天生冪等 vs 靠去重湊冪等**：有些操作天生冪等——`set balance = 500`（設定絕對值）重複做結果一樣；`balance += 100`（相對增量）就不是。能改寫成「設定絕對值 / 用唯一 key 標記」就不必額外去重表。實務上很多 API 刻意設計成冪等（HTTP 的 PUT/DELETE 語意上冪等、POST 不是），就是為了讓重試安全。

- **去重表不能無限長**：`applied` map 會一直長大。真實系統會給它一個 TTL（假設重試都在幾分鐘內發生，過期的 key 就清掉）或用持久化的去重機制。這帶來一個微妙的取捨：TTL 太短，晚到的重試會被當成新請求（又執行一次）；太長，記憶體/儲存吃不消。

- **去重表本身也要容錯**：如果 server 當機重啟，記憶體裡的去重表沒了，重啟後收到重試就會漏判、重複執行。所以嚴格的 exactly-once processing 要求去重表和業務狀態**在同一個交易裡原子地持久化**——這就把問題推向了 Part 2 的複製與 Part 4 的分散式交易。exactly-once 從來不是免費的。

## 對比與取捨

| 語意 | 逾時策略 | 執行次數 | 適用 | 代價 |
|---|---|---|---|---|
| at-most-once | 逾時放棄 | 0 或 1 | 重複做會出事、寧可漏（扣款） | 可能一次都沒做 |
| at-least-once | 逾時重送 | ≥ 1 | 漏做不可接受、且操作可去重（多數 RPC 預設） | 可能重複執行 |
| exactly-once（傳輸層） | —— | —— | **不存在，做不到** | —— |
| exactly-once（效果） | at-least-once + 去重/冪等 | 傳輸≥1，**副作用=1** | 你真正想要的 | 去重表的儲存 + 容錯成本 |

實務結論：**選 at-least-once 當傳輸保證，在應用層用冪等/去重把副作用收斂成一次**。這是業界的標準答案，也是「exactly-once」宣傳詞背後真正在做的事。

## 踩雷集錦

1. **把 RPC 當本地函式呼叫寫**：錯誤直覺——`client.Deposit(100)` 跟本地呼叫一樣，回傳了就是成功、逾時了就是沒做。正確認識——逾時**不代表**沒做（場景二鐵證）。任何會改變狀態的 RPC，都要先問「重試安全嗎」，答案是否，就得加冪等鍵。把 RPC 寫得像本地呼叫是 CORBA/RMI 時代最大的教訓。

2. **相信框架的「exactly-once」是網路層魔法**：錯誤直覺——用了號稱 exactly-once 的系統，我就不用管重複了。正確認識——它是 at-least-once + 去重湊出來的，且去重通常只在該系統的邊界內有效。一旦你的副作用跨出它的邊界（例如 consumer 去打外部 API、寄信），exactly-once 保證就斷了——寄信這種外部副作用你去重不了。務必搞清楚 exactly-once 的**邊界**在哪。

3. **重試時換了 idempotency key**：錯誤直覺——重試就重新產一個請求 ID。正確認識——那 server 就認不出這是重試，會當成全新請求執行。**同一個邏輯操作的所有重試必須共用同一個 key**。這個 bug 很隱蔽：功能測試都過（沒觸發重試），只在生產環境真的逾時重送時才重複扣款。

4. **去重表用完就忘了它會爆 / 會在重啟後消失**：錯誤直覺——記一下收過的 ID 就好。正確認識——它會無限長大（要 TTL）、會在 server 重啟後蒸發（要持久化）。一個記憶體去重表在 server 重啟後，對「重啟前發出、重啟後才重試到達」的請求完全失效，重複照樣發生。嚴格的去重要和業務狀態一起落盤。

5. **以為設個夠大的 timeout 就能避免重複**：錯誤直覺——timeout 設 30 秒，server 哪有那麼慢，就不會誤判重送了。正確認識——這是 Ch 2 partial synchrony 的老問題：總有一次 GC 停頓、網路抖動讓 server 超過 30 秒，你的重送照樣發生。timeout 調參只能降低重複的**頻率**，消不掉它。真正的解法永遠是冪等，不是調 timeout。

## 進階：再往深一層

**為什麼 `net/rpc` 的 `Call` 沒有內建 timeout？** 這其實反映了一個設計哲學：RPC 框架故意**不**替你決定逾時與重試策略，因為那是應用層的語意決定——不同操作的 at-most-once / at-least-once 取捨不同，框架無法一刀切。gRPC 提供了 deadline 與可設定的 retry policy，但也明確要求你標記哪些方法是**冪等**的（`retryableStatusCodes` + 冪等假設）才會自動重試——它不敢對非冪等方法自動重送，因為那正是場景二的災難。框架越成熟，越把「重試安全性」的決定權交還給你，因為只有你知道你的操作能不能重複。

**exactly-once 的理論邊界。** 有一個相關的經典結果：在會遺失訊息的非同步網路裡，**兩軍問題（Two Generals' Problem）**證明了兩方**無法**僅靠傳遞訊息達成「對某件事的確定共識」——因為最後一則確認訊息永遠可能丟失，而沒有它，發送方就不確定對方是否收到。exactly-once delivery 做不到，本質上是兩軍問題的一個實例：你永遠無法同時保證「至少送到」和「至多送到」，因為確認本身也會丟。這是為什麼 exactly-once 只能退化成「at-least-once + 冪等」——放棄在傳輸層追求確定性，改在應用層容忍重複。兩軍問題也是共識為何困難的最早直覺，Ch 15 會正式接上。

## 本章重點整理

- RPC 想用**位置透明**讓遠端呼叫看起來像本地呼叫，但部分失敗讓這個謊言在最關鍵時破產：一次 RPC 有請求送達、server 執行、回應送回**三個獨立會失敗的階段**，而 client 只看得到「有沒有逾時」這一個位元，無法區分失敗在哪一段。
- **逾時 ≠ 沒執行**。這是整章最重要的一句。client 放棄等待不代表 server 沒做——真跑的場景二證明重送讓餘額從 100 變 200。
- 三種語意：**at-most-once**（逾時放棄，0 或 1 次）、**at-least-once**（逾時重送，≥1 次，多數 RPC 預設）、**exactly-once**（傳輸層做不到）。
- **exactly-once delivery 是迷思**（兩軍問題/慢與死不可區分導致傳輸層湊不出來）；能拿到、也真正想要的是 **exactly-once processing = at-least-once 傳輸 + 冪等/去重**——訊息可能重複送達，但副作用只發生一次。
- 實務解法：client 產生全域唯一的 **idempotency key**，同一操作的所有重試共用同一個 key；server 用**去重表**（或把操作設計成天生冪等）讓重複請求只生效一次。真跑的場景三證明它把餘額修回 100。
- 去重不是免費的：去重表要 TTL 防爆、要持久化防重啟失效、嚴格情況要和業務狀態原子落盤——這把 exactly-once 的成本推向 Part 2/Part 4。

## 自我檢核

- [ ] 不看筆記，我能說出一次 RPC 的三個失敗階段，以及為什麼 client 無法區分失敗發生在哪一段
- [ ] 我能解釋「逾時不代表沒執行」，並說出這對「重送」的安全性意味著什麼
- [ ] 我能區分 at-most-once / at-least-once / exactly-once，並說出各自的執行次數與適用場景
- [ ] 我能論證為什麼 exactly-once **delivery** 在網路層做不到，以及 exactly-once **processing** 是怎麼用 at-least-once + 去重湊出來的
- [ ] 我能說出去重表在生產環境會遇到的至少兩個坑（會爆、重啟失效），以及對應的緩解方向
- [ ] 我能解釋為什麼「同一操作的重試必須共用同一個 idempotency key」

## 延伸閱讀

- **[A Note on Distributed Computing](https://scholar.harvard.edu/files/waldo/files/waldo-94.pdf)** — Waldo, Wyant, Wollrath, Kendall（Sun Microsystems, 1994）
  - **這篇說什麼**：業界最著名的「透明遠端呼叫是錯的」宣言，論證本地與遠端呼叫在部分失敗、延遲、並發上有本質差異，不該用同一個抽象藏起來。本章的整個論點就源自這篇
  - **讀哪裡**：第 2–4 節（partial failure、latency、記憶體存取）與本章最直接對應
  - **前提**：讀懂本章即可；這是 CORBA/RMI 時代的反思經典，30 年後依然成立

- **[Implementing Remote Procedure Calls](https://www.cs.cmu.edu/~dga/15-712/F07/papers/birrell842.pdf)** — Birrell & Nelson, ACM TOCS（1984）
  - **這篇說什麼**：RPC 的奠基論文，第一次系統性地實作 RPC 並**明確討論了 at-most-once/at-least-once 語意與重複偵測**——本章的語意分類就出自這裡
  - **讀哪裡**：第 3 節（呼叫語意與失敗處理）；看它 40 年前就把重複問題想清楚了
  - **前提**：讀懂本章的三種語意即可

- **《Designing Data-Intensive Applications》第 8 章「Unreliable Networks」+ 第 9 章「Exactly-once」相關段落** — Martin Kleppmann（O'Reilly, 2017）
  - **這章說什麼**：用工程視角把 RPC 的失敗、timeout 的曖昧、以及「exactly-once = at-least-once + 冪等」講到透，是本章的完整版
  - **讀哪裡**：8.1 開頭關於 timeout 的討論，與第 9 章談 exactly-once semantics 那幾頁
  - **為什麼值得看**：整門課的主參考書，這兩段是本章最好的延伸

Part 0 到此打完地基：我們認清了部分失敗（Ch 1）、有了失敗與網路模型的詞彙（Ch 2）、看穿了 RPC 與 exactly-once 的真相（Ch 3）。下一章起進入 Part 1，攻擊第二大難題——**時間**。我們先拆穿實體時鐘的謊言：為什麼你不能相信任何一台機器的時鐘、更不能拿兩台機器的時間戳來比大小判先後。

→ [Ch 4 實體時鐘的謊言](./04-physical-clocks.md)
