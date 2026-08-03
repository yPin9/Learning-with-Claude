# Ch 4 — 實體時鐘的謊言

> **目標**：徹底戒掉「用時間戳排序分散式事件」這個直覺。搞懂實體時鐘（physical clock）為什麼不可信——石英漂移、NTP 校正、時鐘偏移（clock skew），以及掛鐘時鐘（wall clock）會倒退而單調時鐘（monotonic clock）不會。最後看 Google TrueTime 怎麼把「不確定」變成「有界的不確定」，反過來當工具用。

> **環境**：Go 1.18.1, WSL2/Linux x86-64。本章只有一段短 Go（monotonic vs wall clock），其餘是機制與情境。

## 為什麼需要這個？

你在單機上寫程式，取時間就是 `time.Now()`，兩個時間戳相減就是經過的時間，早的時間戳代表先發生的事。這套直覺在單機上幾乎不會出錯，因為只有一個時鐘。

搬到分散式，這套直覺會**害你丟資料**。

看一個真實會發生的災難。兩台機器 A、B，各自有自己的石英時鐘。使用者在 A 上寫入 `x=1`（A 的時鐘顯示 10:00:00.500），這個寫入的因果後繼——「回覆使用者成功後，使用者又在 B 上寫 `x=2`」——發生在 B（B 的時鐘顯示 10:00:00.300，因為 B 的時鐘比 A 慢了 200ms）。

現在你用「時間戳大的贏」（last-write-wins，LWW）來解衝突。誰的時間戳大？`x=1` 是 .500，`x=2` 是 .300。**`x=1` 贏了。** 但 `x=2` 明明是後發生的、是使用者真正想要的最終值。你用實體時間戳排序，排出了一個**違反因果**的順序，把新值蓋回舊值。

這不是假想。Cassandra 早年用 LWW + 客戶端/伺服器時鐘，在時鐘不同步時默默丟寫入，是 Jepsen 報告裡的經典案例。DynamoDB、Riak 都因此改用邏輯時鐘（下一章）或版本向量。

問題的根源：**分散式系統裡沒有「單一的、正確的現在」**。每台機器的時鐘都在說一個略微不同的謊，而你無法靠它們排出一個可信的全域順序。

## 先建立直覺

先把「時鐘」這個詞拆成兩種完全不同的東西，它們常被混為一談：

```
  掛鐘時鐘 (wall clock / real-time clock)
    「現在是 2026-08-02 10:00:00.500 UTC」
    - 目的：告訴你「絕對時間點」，能跨機器比較、能存進 log
    - 來源：CLOCK_REALTIME，開機時從 RTC/NTP 取得
    - 致命傷：會被 NTP 往前跳、往後倒；會被手動改；夏令時
    -> 絕不能拿來量「經過多久」，因為它中途可能被人改掉

  單調時鐘 (monotonic clock)
    「從某個不明起點到現在，過了 500123 奈秒」
    - 目的：量「兩點之間經過多久」
    - 來源：CLOCK_MONOTONIC，開機起算，只增不減
    - 性質：保證不倒退（NTP 只會微調它前進的速率，不會讓它跳回）
    -> 量 timeout、量延遲、量 elapsed 一律用它
```

一張圖看兩者的差異：

```
        真實流逝的時間 ───────────────────────────────>

wall:   10:00:00.5   10:00:00.8   [NTP 校正: 倒退!]  10:00:00.6   10:00:00.9
                                        │
                                        ▼
                              你以為過了 -0.2 秒？
                              兩個時間戳相減得到「負的經過時間」

mono:   1000.5       1000.8            1001.0        1001.2       1001.5
                              永遠遞增，NTP 只改「走多快」不改「走到哪」
```

記住這條鐵律：**跨機器比較時間點是 wall clock 的事（而且不可信）；量單機上經過多久是 monotonic 的事（可信）**。混用會出災難。

## 石英時鐘為什麼漂移

一般伺服器的時鐘由石英振盪器驅動。石英切一片、通電讓它以固定頻率振盪，數振盪次數就是計時。問題是那個「固定頻率」根本不固定：

- **製造誤差**：每片石英切出來頻率略有差異。
- **溫度**：石英頻率隨溫度變化。機房冷氣、CPU 發熱都會讓它偏。
- **老化**：晶體會隨時間慢慢改變特性。

典型伺服器石英的漂移率是 **10⁻⁶ 到 10⁻⁵**，也就是每天累積偏移約 **幾十毫秒到一秒**。聽起來很小？在分散式系統裡，事件間隔常常只有幾毫秒，而兩台機器可能各自往相反方向漂——A 快 30ms/天、B 慢 30ms/天，一天後兩者相差 60ms。這足以讓上一節的 LWW 災難天天上演。

> **這個數字哪來的**：10⁻⁶ 意思是每秒偏 1 微秒，一天 86400 秒 × 1μs ≈ 86ms。這是「未校正的普通伺服器石英」的量級。裝了 GPS 紀律的原子鐘可以壓到 10⁻⁹ 以下，但那是資料中心等級的投資（見 TrueTime）。

**時鐘偏移（clock skew）** 指的就是「兩個時鐘在同一瞬間讀數的差」。**時鐘漂移（clock drift）** 指的是「偏移隨時間累積的速率」。漂移造成偏移，偏移讓你排錯順序。

## NTP：怎麼把時鐘拉回來（以及為何拉不準）

網路時間協定（NTP, Network Time Protocol）就是用來對抗漂移的：定期問一台更準的時鐘伺服器「現在幾點」，然後校正自己。

NTP 是**分層（stratum）** 架構：

```
Stratum 0:  原子鐘 / GPS 接收器（不上網，直接接到 stratum 1 機器）
                │
Stratum 1:  直接接 stratum 0 的時間伺服器（一級參考）
                │
Stratum 2:  向 stratum 1 同步的伺服器
                │
Stratum 3:  向 stratum 2 同步 ... 你的機器通常在這一層或更下面
```

層數越高離參考源越遠、誤差越大。你的伺服器 `ntpd` 通常同步到幾台 stratum 2/3 的公共伺服器。

**NTP 怎麼算校正量**？核心是量往返延遲，假設去程回程對稱：

```
   client                          server
     │  t1 (送出請求)                  │
     │────────────────────────────────>│ t2 (伺服器收到)
     │                                  │ t3 (伺服器回覆)
     │<────────────────────────────────│
     │  t4 (收到回覆)                   │

   往返延遲 RTT = (t4 - t1) - (t3 - t2)
   時鐘偏移 offset ≈ ((t2 - t1) + (t3 - t4)) / 2
```

那個「/2」藏著 NTP 準不了的根本原因：它**假設去程和回程延遲相等**。真實網路裡去回程常常不對稱（不同路由、佇列排隊、上下行頻寬不同），這個假設一破，估出的 offset 就有誤差。一般 NTP 在公網上能到 **幾毫秒到幾十毫秒** 的精度；同機房內用 PTP（Precision Time Protocol，走硬體時間戳）能到微秒級，但那要交換器支援。

**校正的兩種模式，各有一個坑**：

- **slew（緩調）**：偏移小的時候，NTP 不直接改時間，而是**微調時鐘走的速率**——讓它走快一點或慢一點，慢慢把偏移磨掉。這樣時鐘不會跳，monotonic 性質保住。
- **step（跳調）**：偏移大到超過門檻（`ntpd` 預設 128ms），slew 磨太久，NTP 會直接**把時鐘一步設到正確值**。這一步**可能往回跳**——你的 wall clock 會倒退。

那個倒退就是殺手。如果你的程式碼寫成 `if time.Now() > deadline`，而中間 NTP 把時鐘往回跳，你的 deadline 邏輯就會錯亂；如果你用兩個 wall clock 時間戳相減量 elapsed，會算出負數。

## 底層機制：為什麼不能用實體時間戳排序事件

把前面的東西串成一張因果違反圖，這是全章最該記住的一張：

```
   實際發生順序（因果）：  write1  ──happens-before──>  write2
                          (在 A)                        (在 B)

   時鐘偏移：A 快 200ms，B 正常
   A 的鐘:  10:00:00.500 標記 write1
   B 的鐘:  10:00:00.300 標記 write2   ← B 真的比 write1 晚發生，但鐘顯示更早!

   ┌──────────────────────────────────────────────────┐
   │ 用實體時間戳排序 => write2(.300) 排在 write1(.500) 前 │
   │ LWW 判定 => write1 (.500) 較新 => 保留 write1        │
   │ 結果：把因果上更新的 write2 蓋掉 => 資料遺失          │
   └──────────────────────────────────────────────────┘
```

核心矛盾一句話：**因果順序（happens-before）是我們真正在乎的，但實體時間戳無法可靠反映它**——因為時鐘偏移可以讓「後發生」的事件帶上「更小」的時間戳。時鐘偏移多大，你的排序就可能錯多少。

**把「多大偏移會出錯」量化一下**，你會發現這個雷比想像中好踩。假設你的機群 NTP 同步得不錯，殘餘偏移 ε = 5ms（樂觀值）。那麼：

```
   兩個事件的實體時間戳，只有在「真實時間差 > 2ε」時才能可靠排序。
   （最壞情況：A 快 +ε、B 慢 -ε，或反過來，偏移窗口寬 2ε = 10ms）

   事件真實間隔 > 10ms  =>  時間戳排序「大致」可信
   事件真實間隔 < 10ms  =>  時間戳可能把先後排反 => 不可信
```

問題是微服務之間、同一請求鏈裡的事件，間隔常常就是**幾毫秒**——遠小於 10ms 的偏移窗口。也就是說，恰恰在你最需要排序的高頻場景（同一秒內大量相關寫入），實體時間戳最不可信。這不是「偶爾出錯」，是「在你最在乎的地方系統性出錯」。這就是為什麼下一章的邏輯時鐘不是理論潔癖，是工程剛需。

這推出下一章的整個動機：既然實體時鐘辦不到，我們需要一種**不依賴實體時間、只捕捉因果**的時鐘——**邏輯時鐘（logical clock）**。它不告訴你「幾點」，只告訴你「誰在誰之前」，而且保證不被偏移騙。

在那之前，先看實體時鐘唯一該用的地方（量 elapsed）怎麼正確用。

## 唯一該信實體時鐘的地方：量 elapsed（用 monotonic）

實體時鐘不是一無是處。量「單機上一段程式跑了多久」「距離上次心跳過了多久」——這些**只在同一台機器內、量相對時長**的場景，用 monotonic clock 是可靠的。關鍵是別用 wall clock 幹這件事。

Go 的 `time.Now()` 很貼心：它回傳的 `time.Time` **同時**帶 wall clock 和 monotonic reading。只要你用 `time.Since(t)` 或 `t2.Sub(t1)`（兩者都含 monotonic），Go 自動用 monotonic 相減，不受 NTP 影響。

```go
package main

import (
	"fmt"
	"time"
)

func main() {
	// time.Now() 同時帶 wall clock 與 monotonic reading。
	// 量「經過多久」一律該用 monotonic：不受 NTP 校正/夏令時/手動改鐘影響。
	start := time.Now()

	time.Sleep(50 * time.Millisecond)

	elapsed := time.Since(start) // 內部用 monotonic clock 相減
	fmt.Printf("time.Since (monotonic): %v\n", elapsed)

	// 對照：如果只有 wall clock（把 monotonic reading 剝掉）。
	// Round(0) 會去掉 monotonic reading，模擬「只有掛鐘時間」。
	wallStart := start.Round(0)
	wallEnd := time.Now().Round(0)
	fmt.Printf("wall end - wall start:   %v\n", wallEnd.Sub(wallStart))

	// monotonic 的關鍵性質：只會前進，永不倒退。
	a := time.Now()
	b := time.Now()
	fmt.Printf("monotonic 單調遞增: b>=a ? %v (b-a=%v)\n", !b.Before(a), b.Sub(a))
}
```

真跑（WSL, Go 1.18.1）：

```
$ go run .
time.Since (monotonic): 50.547797ms
wall end - wall start:   50.627497ms
monotonic 單調遞增: b>=a ? true (b-a=0s)
```

平時兩者看起來差不多（都約 50ms），因為沒人在這 50ms 內改時鐘。差異只在**當 NTP step 或有人 `date -s` 改鐘的那一刻**才爆發：monotonic 那行永遠對，wall clock 那行可能算出負數或暴增。這就是為什麼所有 timeout、rate limiter、超時重試的邏輯都該建在 monotonic 上。

> Go 官方在 `time` 套件文件裡明講：`t.Sub(u)`、`time.Since`、`time.Until` 都會在兩個 time 都有 monotonic reading 時用 monotonic。序列化（存 log、傳網路）會剝掉 monotonic reading——這合理，因為 monotonic 的起點只在該行程內有意義，跨行程/跨機器無意義。

## TrueTime：把「不確定」變成「有界的不確定」

Google Spanner（Ch 39 會細講）面對的問題比我們更狠：它要做**跨資料中心、全球一致**的交易，需要一個能排序的時間戳。前面我們論證了「實體時間戳不可信」，Google 的答案不是放棄實體時鐘，而是**誠實面對它的不確定性**。

一般時鐘的 API 是 `now() -> 一個時間點`，它假裝自己精確，其實在說謊。TrueTime 的 API 是：

```
TT.now() -> 一個區間 [earliest, latest]
         「真正的絕對時間，一定落在這個區間內」
```

TrueTime 靠每個資料中心部署 **GPS 接收器 + 原子鐘**（互為備援，GPS 抓到天線問題時原子鐘頂著），把時鐘的不確定性壓到一個**有界**的區間 ε（epsilon）。Google 論文報的 ε 通常在 **1~7 毫秒**，多數時候平均約 4ms 左右。關鍵不是它多小，而是它**有上界且被明確回報**——你拿到的不是「現在是 10:00:00.500」，而是「現在在 10:00:00.496 到 10:00:00.504 之間」。

有了有界區間，Spanner 玩了一個漂亮的把戲叫 **commit-wait**：

```
   交易要提交、拿到 commit 時間戳 s = TT.now().latest
   然後 Spanner 故意「等」，直到 TT.now().earliest > s
   ────────────────────────────────────────────────────
   等的這段時間 ≈ 2ε（一兩個 ms 到十幾 ms）
   意義：等過整個不確定區間後，才敢說「這個交易的時間戳已成過去」

   保證：如果交易 T1 在 T2 開始前就 commit 完（真實因果），
        那 T1 的時間戳一定 < T2 的時間戳。
        => 時間戳的順序不會違反真實的因果順序（外部一致性 / linearizability）
```

換句話說，Spanner **用「等過不確定區間」換來「時間戳可以拿來排序」**。這是把實體時鐘的謊言關進一個有界的籠子——你不是消滅不確定，而是量化它、等過它。代價是每次提交多等 2ε 的延遲，這也是為什麼 Spanner 拼命把 ε 壓小（ε 越小、commit-wait 越短、吞吐越高）。細節留到 Ch 39。

這裡的教訓對本 Part 很關鍵：**當你有辦法把時鐘不確定性變成「有界」，實體時鐘就能重新變成有用的排序工具**。沒有這個界，就只能退回下一章的邏輯時鐘。

## 對比與取捨

| 時鐘 | 保證 | 能跨機器比較? | 能量 elapsed? | 能排序分散式事件? |
|---|---|---|---|---|
| Wall clock（未同步） | 幾乎沒有 | 可比但不可信（skew） | 不能（會倒退） | **不能** |
| Wall clock + NTP | 誤差幾 ms~幾十 ms | 有界但界不小 | 不能（step 會跳） | 不能（skew > 事件間隔就錯） |
| Monotonic clock | 單調不倒退 | 不能（起點無跨機意義） | **能，可靠** | 不能（只在單機有意義） |
| Logical clock（Ch 5） | 捕捉因果的必要條件 | 能（比大小） | 不能（不是實體時間） | **能（因果偏序）** |
| TrueTime（有界區間） | 真值落在 [e, l] 內 | 能，且有界誤差 | 能 | **能（付 commit-wait 代價）** |

沒有一種時鐘全能。工程上的選擇是：量時長用 monotonic，排因果用邏輯時鐘，要全球外部一致又付得起硬體錢就上 TrueTime。**用 wall clock 排序分散式事件是唯一絕不該做的選項**。

## 踩雷集錦

1. **「NTP 同步過了，時間戳就能拿來排序了吧」→ 錯**。NTP 只把偏移壓到幾毫秒~幾十毫秒，不是零。只要你的事件間隔比殘餘偏移小（微服務間常常就是幾毫秒），你照樣會排錯。NTP 讓你「不那麼錯」，不讓你「不錯」。要真的能排序，得上邏輯時鐘或 TrueTime。

2. **「用 `time.Now()` 相減量 timeout」→ 隱藏的定時炸彈**。在 Go 裡若你保留 monotonic reading（別 `Round(0)`、別序列化再讀回）其實沒事，Go 幫你用 monotonic。但在 C/Python/Java 裡若你用的是 `CLOCK_REALTIME`/`System.currentTimeMillis()`，NTP step 或有人改鐘就會讓你的 timeout 算出負值或無限久。量時長一律指名 monotonic（`CLOCK_MONOTONIC`、`System.nanoTime()`、`time.monotonic()`）。

3. **「monotonic clock 的絕對值有意義」→ 錯**。monotonic 的起點是「某個未指定的過去時刻」（通常是開機），它的絕對值跨行程、跨機器、甚至跨重開機都無意義。它**只**能用來算同一行程內兩點的差。把 monotonic 讀數存進資料庫當時間戳是無意義的。

4. **「LWW（last-write-wins）用時間戳解衝突很安全」→ 資料會默默消失**。這是本章開頭的災難。時鐘偏移讓「較新的寫入」帶上「較小的時間戳」，LWW 就把新值蓋成舊值，而且**不報錯**。要嘛用邏輯時鐘/版本向量偵測真正的因果（Ch 6），要嘛接受 LWW 在時鐘不同步下必然丟資料這個事實。

5. **「wall clock 只會往前走」→ 它會倒退**。NTP step、夏令時、閏秒、VM 從 snapshot 還原、有人手動改鐘——都能讓 wall clock 往回跳。任何假設「時間單調遞增」卻建在 wall clock 上的邏輯（例如用時間戳當單調遞增的 ID）都是錯的。要單調遞增的 ID，用邏輯時鐘或專門的 ID 生成器（如 Snowflake，但它也得處理時鐘倒退）。

## 進階：再往深一層

- **閏秒（leap second）與 smearing**：UTC 偶爾插入一閏秒，讓某分鐘有 61 秒。天真的實作會讓時鐘出現 `23:59:60` 這種值或往回跳一秒，曾搞垮過不少系統（2012 的閏秒讓一堆 Linux 伺服器 CPU 飆滿）。Google 的解法是 **leap smear**：把那一秒「抹」在前後 24 小時裡慢慢加，時鐘永遠不跳。這是「寧可時間走得稍不準，也不要時間跳」的哲學。

- **HLC（Hybrid Logical Clock，混合邏輯時鐘）**：CockroachDB 用的東西，把 wall clock 的「大致對得上真實時間」和 Lamport clock 的「保證捕捉因果」縫在一起——時間戳的高位是實體時間、低位是邏輯計數器，既能大致對照真實時間、又保證因果單調。它是「沒有 TrueTime 硬體、又想要接近 Spanner 效果」的折衷。讀完 Ch 5、Ch 6 再回來看 HLC 論文會很有感。

- **PTP（Precision Time Protocol, IEEE 1588）**：比 NTP 準幾個數量級的同步協定，靠交換器/網卡的**硬體時間戳**避開作業系統排程抖動，同機房內能到亞微秒。金融交易、5G 基站這種對時間敏感的場景在用。它不改變「時鐘仍有偏移」這個事實，只是把偏移壓得更小。

## 本章重點整理

- 分散式系統沒有「單一正確的現在」；每台機器的實體時鐘都在說一個略微不同的謊。
- 石英漂移量級是 10⁻⁶~10⁻⁵（每天幾十 ms~1 秒），兩台機器往相反方向漂會累積出可觀的**時鐘偏移**。
- NTP 靠 stratum 分層 + 往返延遲估算校正時鐘，但「去回程對稱」的假設讓它只能到幾 ms~幾十 ms 精度；step 校正還會讓 wall clock **倒退**。
- **wall clock 用來報絕對時間（不可信於跨機排序）；monotonic clock 用來量 elapsed（可信，不倒退）**。混用是災難來源。
- **絕不能用實體時間戳排序分散式事件**——偏移能讓後發生的事件帶更小的時間戳，LWW 會因此默默丟資料。
- TrueTime 用 GPS+原子鐘把不確定性壓成**有界區間** [earliest, latest]，Spanner 靠 commit-wait 等過區間換來可排序的時間戳。

## 自我檢核

- [ ] 不看筆記，我能講出一個「用實體時間戳排序導致資料遺失」的具體因果違反情境
- [ ] 我能說清楚 wall clock 和 monotonic clock 各自的用途，以及為什麼量 timeout 不能用 wall clock
- [ ] 我能解釋 NTP 那個「/2」的假設是什麼、為什麼它讓 NTP 準不了
- [ ] 我能說出 TrueTime 跟一般時鐘 API 的根本差異，以及 commit-wait 在換什麼
- [ ] 我能回答「NTP 同步後就能安全用時間戳排序了嗎」並說明為什麼不能

## 延伸閱讀

### 原始論文

- **[Spanner: Google's Globally-Distributed Database](https://research.google/pubs/pub39966/)** — Corbett et al., OSDI（2012）
  - **讀哪裡**：Section 3「TrueTime」+ Section 4.1.2「commit-wait」兩節即可，是本章 TrueTime 段落的原始出處
  - **學什麼**：TT.now() 回傳區間的 API 設計，以及 commit-wait 怎麼用有界不確定換外部一致性
  - **前提**：讀懂本章「有界不確定」的直覺即可，交易細節可留到 Ch 39

- **[Time, Clocks, and the Ordering of Events in a Distributed System](https://lamport.azurewebsites.net/pubs/time-clocks.pdf)** — Leslie Lamport, CACM（1978）
  - **讀哪裡**：前兩節（happens-before 的定義動機）為下一章鋪路；本章讀「為什麼實體時鐘不夠」的部分
  - **學什麼**：這篇同時是「實體時鐘為何不足」和「邏輯時鐘為何是答案」的奠基作，橫跨 Ch 4-5

### 技術文章 / 官方文件

- **[Kernel of the problem: NTP, PTP, and clock discipline](https://www.usenix.org/publications/loginonline)**（或 Julia Evans 的 "How does NTP work" 系列筆記）
  - **讀哪裡**：NTP 的 offset/delay 計算與 slew vs step 的部分
  - **學什麼**：補足本章 NTP 機制的實作細節；前提是懂本章的往返延遲圖

- **[Go `time` 套件文件的 Monotonic Clocks 一節](https://pkg.go.dev/time#hdr-Monotonic_Clocks)**
  - **讀哪裡**：整段 "Monotonic Clocks"，很短
  - **學什麼**：`time.Time` 怎麼同時帶 wall+monotonic、哪些操作會剝掉 monotonic reading，直接對應本章的 Go 範例
  - **注意**：`Round(0)`、序列化都會去掉 monotonic reading，這是本章 demo 刻意用 `Round(0)` 模擬純 wall clock 的原因

實體時鐘的謊言講完了。既然不能靠「幾點」排序，下一章我們造一種只問「誰在誰之前」的時鐘——不依賴任何實體時間，卻能可靠捕捉因果。

→ [Ch 5 Lamport 邏輯時鐘](./05-lamport-clocks.md)
