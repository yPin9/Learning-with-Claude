# Ch 43 — 測試分散式系統

> **目標**：搞懂為什麼一般的單元測試/整合測試在分散式系統上幾乎沒用，然後學會三種真正管用的武器：**確定性模擬測試（deterministic simulation testing）**（FoundationDB/TigerBeetle 的做法，呼應 Ch 0）、**Jepsen**（用 nemesis 把真實資料庫打到違反宣稱、用 Knossos/Elle 檢查一致性）、以及**線性一致性檢查器（linearizability checker）**的原理。動手：在 `dsim` 上寫一個 nemesis 掃多個 seed 攻擊我們的 Raft，展示「正確的 Raft 全部撐住、拿掉一條 safety 規則就整片噴 FAIL」；再寫一個極簡線性一致性檢查器，親手驗一段合法歷史、否決一段違反歷史。

> **環境**：Go 1.18.1, WSL2 / Linux x86-64。純標準庫。所有輸出以 WSL 實測為準。

## 為什麼需要這個？

你手上這個 Raft（練習 C）跑綠了三個測試。你覺得它對了。

它不對。或者說：你**不知道**它對不對。你測的是三個你想得到的場景（選舉、複製、一個分區），但分散式系統的 bug 幾乎全部藏在**你想不到的時序交錯**裡——兩個 candidate 在延遲差 1 tick 時同時逾時、一則 AppendEntries 在分區發生的那一瞬間還在飛行、舊 leader 帶著過期 term 在 heal 的當下復活。這些交錯的組合數是天文數字，你手寫三個測試連冰山一角都摸不到。

傳統測試的三個致命問題，在分散式系統裡全部放大：

1. **時序相依（timing-dependent）**：bug 只在特定的訊息到達順序下出現。你的測試跑一次是某個順序，跑一萬次可能都是那幾個「好走」的順序，那個會出事的順序永遠沒被走到。
2. **罕見交錯（rare interleaving）**：真的觸發 split-brain 的時序可能一百萬次執行才出現一次。你 CI 跑一千次全綠，上線第一週就中獎。
3. **不可重現（irreproducible）**：就算你在生產環境「看到」了 bug，它依賴真實網路抖動、goroutine 排程、OS 時脈——你抓不回那個時序，就修不了那個 bug。這是 Ch 0 開篇講的同一件事。

> 這正是我們從 Ch 0 就在鋪的路。整門課的動手都跑在確定性模擬器上，不是為了方便，而是因為**分散式測試沒有確定性就沒有意義**。

單機程式你可以靠「輸入 → 輸出」黑箱測。分散式系統不行——**同一組輸入，不同的訊息交錯會給你不同的（甚至錯誤的）輸出**，而交錯不在你的控制裡。要測它，你得先能控制交錯。

## 先建立直覺

三種測試哲學，攻擊的是問題的不同面向：

```
                  你控制執行嗎？        測的是真實系統嗎？
                  ┌──────────────┐    ┌──────────────────┐
確定性模擬測試      │ 完全控制      │    │ 否（模擬器裡的模型）│  開發期：磨演算法邏輯
(DST, Ch 0)       │ 每個訊息順序   │    │ 但同 seed 可重現    │  掃幾百萬 seed 找 bug
                  └──────────────┘    └──────────────────┘

Jepsen            ┌──────────────┐    ┌──────────────────┐
                  │ 不控制執行     │    │ 是（真的資料庫）   │  驗收期：打真實系統
                  │ 只注入 nemesis │    │ 真的斷網/跳時鐘    │  用 checker 事後判違反
                  └──────────────┘    └──────────────────┘

線性一致性檢查器    ┌──────────────┐    ┌──────────────────┐
(checker)         │ 不跑系統      │    │ 只看歷史記錄       │  分析期：給一段 client
                  │ 只分析歷史     │    │ 找合法線性化順序    │  觀測，判它是否線性一致
                  └──────────────┘    └──────────────────┘
```

- **確定性模擬測試（DST）**：把整個系統跑在你完全控制的模擬器裡，用隨機 seed 生成海量不同的執行，任何一個違反不變式的 seed 都能被精準重現。這是**開發期**的工具——在你把演算法邏輯磨對的階段用。
- **Jepsen**：不控制執行，但對**真實**資料庫注入 nemesis（斷網、跳時鐘、殺 process），錄下 client 觀測到的歷史，事後用 checker 判斷「這段歷史有沒有違反它宣稱的一致性」。這是**驗收期**的工具——你以為對了，Jepsen 告訴你哪裡在說謊。
- **線性一致性檢查器**：不跑任何系統，純粹接受一段「誰在什麼時候發了什麼操作、拿到什麼回覆」的歷史，判斷它是否可以被解釋成某個合法的順序執行。它是前兩者背後的**判官**。

三者不是競爭關係，是一條流水線：DST 幫你在開發期用可重現的方式找 bug，Jepsen 幫你在真實系統上驗收，而 checker 是它們共用的「這樣算不算違反」的裁決引擎。

## 確定性模擬測試：把整個世界關進籠子

我們從 Ch 0 就在做這件事，這裡把它的方法論講透。

DST 的核心主張很極端：**放棄真實測試，把整個系統跑在一個確定性模擬器上**。所有的時間、所有的訊息順序、所有的失敗注入，全部由一個吃固定 seed 的偽隨機源決定。同一個 seed → 逐位元組相同的世界線。

FoundationDB 團隊把這條路走到了極致。他們花了兩年**只寫模擬器**（`flow`），然後才寫資料庫。模擬器裡：

- 網路的每一則訊息延遲、丟包、重排，由 seed 決定。
- 磁碟 IO 的完成時機、甚至「磁碟寫一半突然斷電」，由 seed 決定。
- 一個「搗亂者」（他們叫 `buggify`）在隨機時刻注入各種故障：殺 process、切網路、塞爆記憶體、把時鐘往前跳。

然後他們跑**幾百萬個 seed**，每個 seed 是一個平行宇宙。任何一個 seed 讓系統違反了不變式（丟了一筆已 commit 的資料、選出兩個 leader、交易 ACID 破了），這個 seed 就是一張可以無限重播的錄影帶——工程師拿著它，一路 print 到看見 bug 為什麼發生。結果：FoundationDB 上線後幾乎沒有一致性 bug。這在分散式資料庫史上是異數。

TigerBeetle（金融級資料庫）把這個方法論寫進了他們的工程文化，他們的模擬器叫 **VOPR**（"the VOPR" — 對應電影 WarGames 裡那台不斷自我對弈的電腦），一樣是「隨機 seed 掃出 bug、失敗 seed 精準重現」。

DST 為什麼這麼強？因為它把「罕見交錯」這個問題正面解決了：

```
傳統測試：真實世界的交錯空間巨大，你只走到其中極小、且偏「好走」的一撮
   ┌────────────────────────────────────┐
   │  全部可能的執行交錯（天文數字）        │
   │   ┌──┐  ← 你的測試實際走到的（一小撮） │
   │   └──┘    多半是不會出事的「好」交錯    │
   │            ██ ← bug 藏在這些罕見交錯   │
   └────────────────────────────────────┘

DST：每個 seed 是一個交錯樣本，掃幾百萬 seed = 用亂數大面積撒網
   ┌────────────────────────────────────┐
   │  . . . . . . . . . . . . . . . . .  │  每個點是一個 seed 走的交錯
   │  . . . . ██ . . . . . . . . . . . . │  撒夠密就會命中 bug 交錯
   │  . . . . . . . . . . . . . . . . .  │  命中後：那個 seed 100% 重現
   └────────────────────────────────────┘
```

關鍵不只是「撒網找 bug」——**找到之後能重現**才是致勝點。傳統 fuzzing 也能撞到 bug，但撞到之後常常重現不了（因為依賴真實時序）。DST 的每個 bug 都附一張錄影帶。

DST 的代價：你得把系統寫成「可被模擬器控制」的形狀——所有 IO、時間、隨機都要走模擬器提供的介面，不能偷用 `time.Now()`、不能開真的 goroutine 亂跑。這是一種侵入式的架構約束（我們的 `dsim.Node` 介面就是這個約束的最小版）。但一旦付了這個代價，你得到的是分散式測試的聖杯：**可重現的隨機故障測試**。

## Jepsen：打真實系統的黑帽

DST 測的是「模擬器裡的模型」。但你的模型對，不代表你真實部署的那套 etcd/MongoDB/Cassandra 對——真實系統有你模型裡沒有的東西：作業系統的 TCP 行為、GC 停頓、時鐘漂移、磁碟謊報 fsync。要驗真實系統，你需要 Jepsen。

Jepsen 是 Kyle Kingsbury（網名 Aphyr）做的一套框架，它的方法論簡單而致命：

1. **架一個真的叢集**（真的 5 台機器跑真的資料庫）。
2. **一群 client 並發地對它讀寫**，每個操作都記下 `(invoke 時刻, response 時刻, 操作內容, 回傳值)`——這叫**歷史（history）**。
3. **一個 nemesis 在旁邊搗亂**：隨機切網路分區、把某台機的時鐘往前/後跳、暫停 process（模擬 GC）、殺掉再重啟。
4. **事後把歷史丟給 checker**（Knossos 或 Elle）判斷：這段 client 觀測到的歷史，**有沒有可能是一個合法的線性一致執行**？如果找不到任何合法解釋，這個系統就違反了它宣稱的線性一致性。

Jepsen 的威力在於它是**經驗性的、黑帽的**——它不管你的設計文件怎麼寫、你的證明多漂亮，它直接對真實系統施暴，然後看它流不流血。Aphyr 用它逐一「處刑」了業界一票宣稱有某某一致性保證的資料庫：早期的 MongoDB 在分區下丟已確認的寫入、早期的 etcd/Consul 的 stale read、Redis 的 sentinel 在分區下 split-brain、Cassandra 的 lightweight transaction 不是真的線性一致……這些都是廠商文件裡宣稱「安全」、被 Jepsen 打出血的真實案例。

> 去讀 [jepsen.io/analyses](https://jepsen.io/analyses)。每一篇都是一個真實資料庫在 partition/時鐘異常下違反宣稱的驗屍報告，附完整的重現步驟與歷史記錄。這是「真實系統怎麼壞」最好的教材，沒有之一。

Jepsen 教我們一件事：**宣稱（claim）和保證（guarantee）是兩回事**。一個系統宣稱線性一致，只在它被 Jepsen 這種對抗性測試打過、且 checker 沒找到違反時，你才有理由相信它。

### nemesis 是什麼

nemesis（源自希臘神話的復仇女神）就是「搗亂者」——一個和 client 平行運作、專門注入故障的角色。它的動作庫大致是：

| nemesis 動作 | 模擬的真實故障 | 對應 `dsim` API |
|---|---|---|
| partition | 網路分區、交換機故障 | `Partition` / `Heal` |
| kill/pause process | 節點當機、GC 長停頓 | `Crash` / `Restart` |
| clock skew | NTP 失效、VM 時鐘漂移 | （我們的 Raft 不用實體時鐘，故不受影響——這本身是個好性質，見 Ch 4）|
| slow network | 跨區延遲、擁塞 | `SetLatency` |
| packet loss | 網路品質差 | `SetDropRate` |

nemesis 的精髓：它是**隨機且對抗性**的。它不會挑「系統剛好穩定」的時候搗亂，它專挑最尷尬的時刻——正在選舉時斷網、剛 commit 一半時殺 leader。我們接下來就在 `dsim` 上寫一個這樣的 nemesis。

## 線性一致性檢查器：判官的原理

DST 和 Jepsen 都需要一個共同的裁決引擎：**給我一段歷史，告訴我它合不合法**。這個引擎就是線性一致性檢查器。

先回憶線性一致性（linearizability，Ch 9）的定義：一段並發歷史是線性一致的，若**存在**一個把所有操作排成一條線的順序，使得：

1. 這個順序是所有操作的一個排列。
2. 它**尊重實時序（real-time order）**：如果操作 A 的 response 早於操作 B 的 invoke（A 完全發生在 B 之前），那 A 在這條線上必須排在 B 前面。並發的操作（時間區間重疊）則可以任意排。
3. 依這條線**順序執行**，每個讀操作都讀到「它前面最近一次寫」的值。

檢查器要做的就是：**搜尋這樣一條線存不存在**。存在 → 線性一致；窮舉完所有可能的線都找不到 → 違反。

```
歷史（client 觀測到的實時區間）：
  put(1)  ├────────────┤              invoke=0  response=10
  get()->0    ├──┤                    invoke=1  response=3    讀到舊值 0
  get()->1              ├────┤        invoke=11 response=14   讀到新值 1

問：能排出一條尊重實時序、且每個 get 讀到最近一次 put 的線嗎？
答：能。 get()->0  →  put(1)  →  get()->1
       （get()->0 和 put(1) 時間重疊，可以排 get 在前；讀到 put 前的初始值 0）
    => 線性一致
```

而下面這段就排不出來：

```
  put(1)  ├──────┤                    invoke=0  response=5
  get()->1     ├──┤                   invoke=6  response=8    讀到 1
  get()->0          ├──┤              invoke=9  response=11   又讀到 0 ??

  get()->1 完全在 get()->0 之前（8 < 9），實時序強制 get()->1 排前面。
  但值一旦被讀成 1，暫存器就是 1，後面的 get 不可能倒退讀回 0。
  => 找不到合法的線 => 非線性一致（值倒退了）
```

**這個搜尋問題是 NP-hard 的**（Gibbons & Korach 1997 證明了一般情況下判定線性一致性 NP-complete）。直覺原因：並發操作的排列組合是階乘級的，你可能要試 O(n!) 種順序。真實的 checker（Knossos 用的 **WGL 演算法**、Elle 用的**依賴圖循環偵測**）用了大量剪枝——一旦某個前綴已經不合法就整棵子樹剪掉、用偏序而非全序來壓縮搜尋空間——才讓它在實務上的歷史長度（幾百到幾千個操作）跑得動。

Elle（Jepsen 的新一代 checker）更聰明：它不暴力搜順序，而是從歷史裡**推導出操作之間的依賴關係**（誰讀到了誰寫的值 → 誰必須在誰之後），建成一張圖，然後**找環**——圖裡有環就代表存在循環依賴，也就代表不可能線性化。這把 NP-hard 的搜尋轉成了近乎多項式的找環，是 Jepsen checker 的一大躍進。

我們接下來寫的檢查器是**暴力版**（O(n!)），只能吃很短的歷史，但它把「線性一致性 = 存在一條合法的線」這個本質展示得最清楚。

## 底層機制：在 dsim 上寫 nemesis 掃 seed

現在動手。我們要在 `dsim` 上實作一個 nemesis，讓它隨機注入 partition / crash / heal / restart 攻擊練習 C 的 Raft，同時一個 client 不斷 propose。每一步檢查兩個不變式：

- **選舉安全**：同一個 term 至多一個 leader。
- **已 commit 不丟**：任何一格 log 一旦被觀測到 commit，它的內容永遠不能變（不能「已 commit 卻被覆蓋」）。

然後我們掃 seed 0..49，看正確的 Raft 能不能全部撐住。

nemesis 的關鍵：**所有隨機都必須走 `net.rng`**，否則就失去確定性——這是 Ch 0 反覆強調的鐵律。因為我們把 nemesis 和 `dsim` 放在同一個 package，可以直接存取未匯出的 `net.rng`。

```go
// nemesis 在隨機時刻注入 partition / crash / heal / restart。
// 所有隨機都走 net.rng（同 package 可存取未匯出欄位），保證同 seed 可重現。
type nemesis struct {
	net       *Net
	nodes     []NodeID
	nextFlip  int
	crashedID NodeID
	state     int // 0=正常 1=分區中 2=有節點當機
}

func (nm *nemesis) tick(now int) {
	if now < nm.nextFlip {
		return
	}
	switch nm.state {
	case 0: // 正常 -> 製造故障（擲骰決定切分區還是殺節點）
		if nm.net.rng.Intn(2) == 0 {
			half := len(nm.nodes)/2 + nm.net.rng.Intn(2)
			if half < 1 { half = 1 }
			if half >= len(nm.nodes) { half = len(nm.nodes) - 1 }
			nm.net.Partition(nm.nodes[:half], nm.nodes[half:])
			nm.state = 1
		} else {
			nm.crashedID = nm.nodes[nm.net.rng.Intn(len(nm.nodes))]
			nm.net.Crash(nm.crashedID)
			nm.state = 2
		}
	case 1: // 分區中 -> 修復
		nm.net.Heal()
		nm.state = 0
	case 2: // 當機中 -> 復活
		nm.net.Restart(nm.crashedID)
		nm.crashedID = -1
		nm.state = 0
	}
	nm.nextFlip = now + 10 + nm.net.rng.Intn(15) // 下次搗亂時刻，仍走 net.rng
}
```

主迴圈：每步 nemesis 攪局、偶爾 propose、推進模擬、檢查不變式。

```go
func runOneSeed(seed int64, steps int) (bool, string) {
	net := NewNet(seed)
	net.SetLatency(1, 3)
	net.SetDropRate(0.02)
	// ... 建 5 節點 Raft、掛上 nemesis ...

	committed := map[int]interface{}{} // index -> 曾被觀測到 commit 的內容
	for now := 1; now <= steps; now++ {
		nm.tick(now)
		if now%4 == 0 { /* 找一個活著的 leader propose 一筆 */ }
		net.Run(now)

		// 不變式 1：選舉安全（同 term 至多一 leader）
		termLeaders := map[int][]NodeID{}
		for _, r := range rafts {
			if r.role == leader {
				termLeaders[r.currentTerm] = append(termLeaders[r.currentTerm], r.id)
			}
		}
		for term, ls := range termLeaders {
			if len(ls) > 1 {
				return false, fmt.Sprintf("選舉安全違反 @now=%d term=%d leaders=%v", now, term, ls)
			}
		}

		// 不變式 2：已 commit 不丟（同一 index 的已 commit 內容不能變）
		for _, r := range rafts {
			for i := 1; i <= r.commitIndex && i < len(r.log); i++ {
				got := r.log[i].Cmd
				if prev, seen := committed[i]; seen && prev != got {
					return false, fmt.Sprintf("已 commit 資料變更 @now=%d index=%d 舊=%v 新=%v", now, i, prev, got)
				}
				committed[i] = got
			}
		}
	}
	return true, "撐過 " + /*...*/ " 步"
}
```

掃 50 個 seed，真跑（WSL, Go 1.18.1，`go run .`）：

```
=== nemesis 掃 seed 0..49（5 節點 Raft，隨機注入 partition/crash/heal）===
  範例（seed=7 通過）：撐過 400 步，delivered=670 dropped=118 committed=38 格
  50 個 seed 全部撐住：正確的 Raft 在隨機故障下不違反選舉安全、不丟 committed 資料。
  （若把 raft.go 的某條 safety 規則拿掉，這裡就會噴出 FAIL seed，且該 seed 可無限重現）
```

50 個 seed，每個都是一段不同的隨機故障排程，正確的 Raft 全部撐住——不違反選舉安全、不丟 committed 資料。這比練習 C 的三個手寫測試強太多：那三個測特定場景，這裡是**用亂數大面積撒網、每個 seed 一個平行宇宙**。

> **一個誠實且重要的細節**：上面 `delivered=670 dropped=118 committed=38` 這幾個數字，**你在你機器上跑會不一樣**，同一台機器跑兩次也可能不一樣。為什麼？因為 `dsim` 的 `Run` 在 tick 階段用 `for id, node := range n.nodes` 迭代——Go 的 map 迭代順序是隨機的（Ch 0 就標注過這個「故意留下的細節」）。它影響「哪個節點先 tick」，進而影響 leader 是誰、訊息計數。**但注意：PASS/FAIL 的判決是穩定的**——不管 map 怎麼迭代，正確的 Raft 永遠 0 個 FAIL。這正是分散式測試該有的性質：**你測的是不變式（invariant），不是某個具體的執行痕跡**。訊息數變來變去無所謂，「不丟資料、不 split-brain」這條線不能破。若要連訊息數都逐位元組重現，得把 tick 迴圈改成按 NodeID 排序迭代——這是 Ch 0「進階」提到的強化方向。

### 把 safety 規則拿掉，看 nemesis 抓到它

「50 個 seed 全綠」這句話有沒有意義，取決於**這個 nemesis 抓不抓得到真的 bug**。一個什麼都抓不到的測試，全綠是假象。我們來破壞 Raft，證明 nemesis 有牙齒。

Raft 的 `majority()` 是 `⌊N/2⌋ + 1`（5 節點要 3 票）。我們把它改成 `⌊N/2⌋`（5 節點只要 2 票）——這一改，**兩個不重疊的少數派可以各自湊到 2 票、各自選出 leader**，split-brain 的門就開了；而兩個 leader 各寫各的，已 commit 的資料就會被覆蓋。

```go
// 破壞：多數從 /2+1 改成 /2（少數派也能選出 leader）
func (r *Raft) majority() int { return len(r.peers) / 2 }
```

只改這一行，其餘完全不動，重跑同一個 nemesis sweep：

```
=== nemesis 掃 seed 0..49（5 節點 Raft，隨機注入 partition/crash/heal）===
  seed= 0  FAIL  選舉安全違反 @now=28 term=1 有 leaders=[0 2]
  seed= 2  FAIL  選舉安全違反 @now=28 term=1 有 leaders=[0 2]
  seed= 3  FAIL  已 commit 資料變更 @now=217 index=40 舊=w39 新=w41 (node=3)
  seed= 8  FAIL  選舉安全違反 @now=29 term=1 有 leaders=[0 2]
  seed=10  FAIL  已 commit 資料變更 @now=396 index=61 舊=w61 新=w66 (node=2)
  seed=13  FAIL  已 commit 資料變更 @now=370 index=70 舊=w69 新=w75 (node=0)
  seed=15  FAIL  選舉安全違反 @now=26 term=1 有 leaders=[0 1]
  seed=18  FAIL  已 commit 資料變更 @now=64 index=3 舊=w2 新=w9 (node=1)
  ...
  觸發問題的 seed：[0 2 3 8 10 13 15 18 20 30 31 35 36 39 41 43 46] ...
```

一改壞就有 17 個 seed 噴出違反——一半是 split-brain（同 term 兩個 leader），一半是丟資料（已 commit 的 `w39` 被 `w41` 覆蓋）。這證明兩件事：

1. **nemesis 有牙齒**：它抓得到真實的 safety 破壞。前面「50 個全綠」因此是有意義的綠。
2. **每個 FAIL 都可精準重現**：`seed=3 @now=217 index=40` 不是一句「偶爾會出事」，是一個座標。你拿 seed=3 重跑，一路 print 到 now=217，就能看見 index=40 那格是怎麼被覆蓋的。**這就是 DST 的整個價值**——非確定環境下，這種偶發的 split 幾乎不可能穩定重現，你只能看著生產環境的 log 乾瞪眼。

> 這也是練習 C 解答說明裡「majority 寫錯成 4，靠 seed 重現 print 抓到」那個 bug 的放大版。分散式的 safety 規則每一條都在防一個具體災難，拿掉任何一條，nemesis + 確定性重現就會把災難擺到你面前。

## 底層機制：暴力線性一致性檢查器

現在寫那個「判官」。給一段對單一暫存器（register）的操作歷史，暴力搜尋合法的線性化順序。

```go
type op struct {
	kind     opKind // opPut / opGet
	val      int    // put 寫入值 / get 回傳值
	invoke   int    // client 發出操作的時刻
	response int    // client 收到回覆的時刻
}

// 暴力搜尋合法線性化順序：DFS 排列所有 op，每步只允許排入
// 「沒有任何未排入的 op 在實時上一定早於它」的 op，最後驗證每個 get 讀到最近一次 put。
func checkLinearizable(ops []op) (bool, []int) {
	n := len(ops)
	perm := make([]int, n); used := make([]bool, n)
	var order []int
	var dfs func(depth int) bool
	dfs = func(depth int) bool {
		if depth == n { // 排完一條完整的線，模擬執行驗證每個 get
			reg, has := 0, false
			for _, idx := range perm[:depth] {
				o := ops[idx]
				if o.kind == opPut { reg, has = o.val, true } else {
					if !has { if o.val != 0 { return false } } else if o.val != reg { return false }
				}
			}
			order = append([]int(nil), perm[:depth]...); return true
		}
		for i := 0; i < n; i++ {
			if used[i] { continue }
			// 實時序約束：若有未排入的 j 其 response < ops[i].invoke，j 必須先排，i 不能現在排
			ok := true
			for j := 0; j < n; j++ {
				if !used[j] && j != i && ops[j].response < ops[i].invoke { ok = false; break }
			}
			if !ok { continue }
			used[i] = true; perm[depth] = i
			if dfs(depth + 1) { return true }
			used[i] = false
		}
		return false
	}
	if dfs(0) { return true, order }
	return false, nil
}
```

餵它兩段歷史：A 線性一致（put 和一個 get 時間重疊，存在合法排法），B 違反（值讀到 1 後又倒退回 0）。真跑輸出：

```
=== 線性一致性檢查器：給一段歷史，找合法的線性化順序 ===
  歷史 A：
    put(1)@[0,10]
    get()->0@[1,3]
    get()->1@[11,14]
    -> 線性一致。一個合法順序：get()->0@[1,3] put(1)@[0,10] get()->1@[11,14]
  歷史 B：
    put(1)@[0,5]
    get()->1@[6,8]
    get()->0@[9,11]
    -> 非線性一致：找不到任何合法順序（值一旦讀到 1 就不能倒退成 0）。
       這正是 Jepsen 用 Knossos/Elle 在真實資料庫裡抓到的那種違反。
```

檢查器正確地接受 A、否決 B。歷史 A 裡 `get()->0` 和 `put(1)` 時間重疊，所以可以把 get 排在 put 之前（讀到初始值 0）——合法。歷史 B 裡 `get()->1`（response=8）完全早於 `get()->0`（invoke=9），實時序強制它排前面，於是暫存器已經是 1，後面的 `get()->0` 無論如何排不出來——**這就是一個線性一致性違反**，跟 Jepsen 在真實資料庫裡抓到的「stale read / 值倒退」是同一種東西。

這個暴力版是 O(n!)，餵它十幾個操作就會爆。但它把本質講清楚了：**線性一致性 = 存在一條尊重實時序、且讀寫自洽的全序**。Knossos/Elle 做的是同一件事，只是用 WGL 剪枝和依賴圖找環把它加速到能吃真實長度的歷史。

## 對比與取捨

| 方法 | 何時用 | 抓得到什麼 | 抓不到什麼 | 成本 |
|---|---|---|---|---|
| 手寫場景測試（練習 C 那三個） | 開發初期冒煙 | 你想得到的場景 | 你想不到的時序交錯 | 低 |
| **確定性模擬測試（DST）** | 開發期磨演算法 | 罕見交錯 bug，且可重現 | 模型與真實部署的落差 | 中（架構要可模擬）|
| **Jepsen** | 真實系統驗收 | 真實部署下的一致性違反 | 沒被 nemesis 觸發的 bug | 高（要架真叢集）|
| 形式化驗證（TLA+） | 設計期 | 設計層的邏輯漏洞 | 實作與設計的落差 | 很高（要寫規格+證明）|

沒有一個方法夠。實務上的組合拳：**TLA+ 驗設計 → DST 驗實作邏輯（開發期主力）→ Jepsen 驗真實部署（上線前驗收）**。FoundationDB 用 DST 為主，MongoDB/etcd/TiDB 這些都被 Jepsen 打過並公開修復記錄。你的 Raft 練習 C 停在「手寫場景測試」，這章把它升級到了 DST。

## 踩雷集錦

1. **「跑一萬次都綠 = 沒 bug」**：錯得離譜。跑一萬次若都是同幾種「好走」的交錯，那個會出事的罕見交錯一次都沒被走到。**次數不等於覆蓋**——你要的是交錯空間的覆蓋，不是重複跑同一撮交錯。DST 用不同 seed 撒網才有覆蓋，光加迴圈次數沒用。

2. **測試自己有 bug，誤報或漏報**：Ch 0 就踩過——「我以為在測 heal，其實測的是一條已經死掉的訊息鏈」。分散式測試裡「我以為在測 A、其實測到 B」極常見。務必**先驗證你的測試抓得到已知的 bug**（像我們故意改壞 `majority()` 那樣），再相信它的綠燈。抓不到已知 bug 的測試，綠燈是假的。

3. **nemesis 用了 `net.rng` 以外的隨機源**：一旦 nemesis 偷用全域 `rand` 或 `time.Now()`，整個確定性就沒了——你找到的 bug seed 重跑不出來，DST 最值錢的「可重現」直接歸零。**所有隨機、所有時間都必須走模擬器**，這是不可退讓的（Ch 0 踩雷第 1 條的延伸）。

4. **把 delivered/committed 這種執行痕跡當成不變式**：我們的 sweep 裡訊息數會因 map 迭代而變，但那**不是** bug。把「訊息數必須等於某個定值」寫進 assertion，你會得到一堆假 FAIL。**只 assert 真正的安全性質**（不 split-brain、不丟 committed），別 assert 執行的偶然細節。

5. **以為線性一致性檢查器很快**：它是 NP-hard。歷史一長（幾十個並發操作）暴力版就爆，就算用 Knossos 也可能跑到天荒地老。實務上要**限制歷史長度、限制並發度**，或用 Elle 的依賴圖找環（近多項式）。別天真地把幾千個操作丟給暴力 checker。

6. **只測 happy path 的失敗注入**：nemesis 若只在「系統剛好穩定」時搗亂，等於沒搗亂。真正的 nemesis 要專挑最尷尬的時刻——選舉進行到一半、commit 剛過半數。對抗性（adversarial）是 nemesis 的靈魂，隨機但要夠密、夠壞。

## 進階：再往深一層

- **shrinking（縮小反例）**：DST 找到一個失敗 seed 後，那個執行可能有幾千步，人看不動。進階框架會**自動縮小**：把故障注入的次數、client 操作數一步步砍掉，只要還能重現失敗就繼續砍，最後給你一個「最小可重現案例」（可能只剩 3 個操作 + 1 次分區）。這是 property-based testing（QuickCheck 血脈）的核心技巧，madsim / TigerBeetle VOPR 都有。

- **swarm testing**：不要固定一組 nemesis 參數掃 seed，而是**連 nemesis 的配置也隨機化**——這次多丟包少分區、下次反過來。不同的「故障配方」暴露不同的 bug。Regehr 的 swarm testing 論文顯示這比固定配置找到的 bug 多得多。

- **Elle 的黑魔法**：去讀 Elle 論文（Kingsbury & Alvaro, VLDB 2020）。它不需要 client 記錄「我讀到誰寫的值」這種強資訊，光從「讀到的值」就能反推出依賴圖、找環判違反，而且能區分是哪一種一致性異常（G0/G1a/G1b/G2...，對應 Adya 的隔離級別形式化）。這是把「檢查一致性」從暴力搜尋提升到圖論的一次躍進。

- **把 checker 接進 dsim**：我們的 sweep 只檢查了「不 split-brain、不丟 log」這種結構不變式。更強的做法是讓 client 記錄完整的 `(invoke, response, op, ret)` 歷史，跑完把歷史丟給線性一致性檢查器——這樣連「讀到過期值」這種細微違反都抓得到。這正是把本章兩個 demo 縫起來的方向，final project 會做。

## 本章重點整理

- 一般測試在分散式系統上幾乎無效：bug 藏在**時序相依、罕見、不可重現**的交錯裡，手寫場景摸不到。
- **確定性模擬測試（DST）**：把整個系統跑在確定性模擬器上，隨機 seed 掃出 bug，失敗 seed 精準重現。FoundationDB/TigerBeetle 的做法，也是我們從 Ch 0 走的路。關鍵不只找 bug，是**找到後能重播**。
- **Jepsen**：對真實資料庫注入 nemesis（分區/時鐘/殺 process），錄歷史、用 Knossos/Elle 事後判違反。它是經驗性、對抗性的——打真實系統看它流不流血，逼出「宣稱 vs 真實保證」的落差。
- **線性一致性檢查器**：判斷一段歷史是否存在合法的線性化順序（尊重實時序 + 讀寫自洽）。一般情況 **NP-hard**；Knossos 用 WGL 剪枝、Elle 用依賴圖找環加速。
- 動手證明了：正確的 Raft 撐過 50 個隨機故障 seed；把 `majority()` 改壞一行，17 個 seed 立刻噴 split-brain / 丟資料，且每個都可用 seed 精準重現。
- **測試要 assert 不變式，不 assert 執行痕跡**；且要先驗證測試抓得到已知 bug，綠燈才可信。

## 自我檢核

- [ ] 我能說出「跑一萬次都綠」為什麼**不等於**沒 bug，而 DST 的「掃一萬個 seed」為什麼不同。
- [ ] 我能解釋 DST 相對傳統 fuzzing 多出來、也是它最值錢的那個性質是什麼（提示：不是「找到 bug」）。
- [ ] 不看內文，我能說出 Jepsen 的四個步驟（架叢集、client 記歷史、nemesis 搗亂、checker 判違反）各在做什麼。
- [ ] 我能手動判斷一段三操作的歷史是否線性一致，並說出「實時序約束」在其中扮演什麼角色。
- [ ] 我能解釋為什麼判定線性一致性是 NP-hard，以及 Elle 用什麼技巧繞過暴力搜尋。
- [ ] 我能說出為什麼我們的 nemesis sweep 裡 `delivered` 數字會變、但 PASS/FAIL 判決是穩定的——這反映了「測不變式而非執行痕跡」的原則。

## 延伸閱讀

### 影片 / 演講

- **[Testing Distributed Systems w/ Deterministic Simulation](https://www.youtube.com/watch?v=4fFDFbi3toc)** — Will Wilson（FoundationDB, Strange Loop 2014）
  - **說什麼**：DST 的奠基演講，FoundationDB 怎麼用它做到上線零一致性 bug。Ch 0 也引了這場。
  - **讀哪裡**：前 20 分鐘講「為什麼真實測試抓不到 bug」，後半講 `buggify` 與 seed 重現。
  - **前提**：讀得懂本章的 nemesis sweep 即可。

### 部落格 / 分析報告

- **[Jepsen Analyses](https://jepsen.io/analyses)** — Kyle Kingsbury
  - **說什麼**：一整排真實資料庫在 partition/時鐘異常下違反宣稱的驗屍報告。本章的靈魂。
  - **讀哪裡**：挑你用過的資料庫（etcd、MongoDB、Cassandra）那篇，看它宣稱什麼、被打出什麼血、怎麼修。
  - **為什麼值得**：這是「真實系統怎麼壞」最權威、最完整的公開教材。

- **[Jepsen: How to check if your database is linearizable](https://aphyr.com/posts/313-strong-consistency-models)** — Aphyr
  - **說什麼**：一致性模型光譜 + checker 怎麼判線性一致，配大量插圖。
  - **讀哪裡**：「Linearizability」與後面 checker 那幾節，補本章暴力 checker 的理論背景。

### 論文

- **[Elle: Inferring Isolation Anomalies from Experimental Observations](https://www.vldb.org/pvldb/vol14/p268-alvaro.pdf)** — Kingsbury & Alvaro, VLDB（2020）
  - **說什麼**：Jepsen 新一代 checker，從歷史反推依賴圖、找環判違反，還能分類異常。
  - **讀哪裡**：Section 3（依賴圖建構）與 Section 4（異常分類）。
  - **前提**：懂本章的線性一致性定義 + 一點圖論（有向圖找環）。

- **[Linearizability: A Correctness Condition for Concurrent Objects](https://cs.brown.edu/~mph/HerlihyW90/p463-herlihy.pdf)** — Herlihy & Wing, TOPLAS（1990）
  - **說什麼**：線性一致性的原始定義論文，本章 checker 判準的源頭。
  - **讀哪裡**：Section 2 的定義與範例，其餘偏形式化可略。

我們現在能測分散式系統了——能大面積掃 bug、能判一致性違反。但**測試告訴你「有問題」，除錯告訴你「問題在哪」**。分散式系統的除錯又是另一頭猛獸：沒有全域快照、因果散在多節點、log 各說各話。下一章我們補上這塊——分散式追蹤、結構化日誌、以及怎麼在一群節點上照出一張「一致的全域快照」。

→ [Ch 44 可觀測性與除錯](./44-observability-tracing.md)
