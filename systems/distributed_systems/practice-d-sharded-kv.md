# 練習 D — 分片 KV + Shard Controller

> **目標**：在 Ch 0 的確定性模擬器 `dsim` 上，建一個**分片 KV store + shard controller**（對齊 MIT 6.5840 Lab 4 的精神，但用簡化的複製模型）。多個 shard 分散在多個 group 上、一個 controller 負責配置與再平衡、client 依 key 路由到對應 group。核心挑戰：**config 變更時 shard 遷移不能丟資料、不能重複服務，遷移中的請求要正確處理**。這是把整個 Part 4（RSM、KV、分片 [Ch 27](./27-sharding-partitioning.md)、一致性雜湊 [Ch 28](./28-consistent-hashing.md)、成員 [Ch 29](./29-membership-failure-detection-swim.md)）縫成一個能跑的系統。

> **環境**：Go 1.18.1, WSL2 / Linux x86-64。純標準庫，所有輸出以 WSL 實測為準。

## 背景與動機

你在[練習 C](./practice-c-build-raft.md) 手刻了 Raft，得到一個**單一複製組**——一群節點對同一份 log 達成一致，容錯、線性一致。但它有個天花板：**所有資料都在同一組節點上，容量和吞吐都被單組上限卡死**。一個 Raft group 再強，也塞不下 PB 級資料、扛不住每秒百萬請求。

真實系統的解法是**分片（sharding）**（[Ch 27](./27-sharding-partitioning.md)）：把 key 空間切成 N 個 shard，每個 shard 交給一個獨立的複製組（replica group）服務。10 個 group 就有 10 倍容量、10 倍吞吐——而且能水平擴展，加 group 就加容量。這是 Spanner、CockroachDB、TiDB、Vitess 的共同骨架。

但分片打開了一個練習 C 沒碰過的難題：**誰負責哪個 shard，是會變的**。加一個 group 進來要分擔負載、一個 group 掛了它的 shard 要轉交、負載不均要再平衡——每一次「配置變更」都意味著 **shard 要在 group 之間搬家**。而搬家的瞬間，最容易出兩種災難：

- **丟資料**：舊 owner 已經不服務了、新 owner 還沒收到資料，這中間來的請求打到誰都是空的；或者舊 owner 太早把資料刪了。
- **重複服務（雙寫/腦裂）**：舊 owner 以為自己還負責、新 owner 也開始服務，同一個 key 被兩個 group 各寫各的，資料分岔。

這正是 MIT 6.5840 Lab 4 被公認為整個課程最難的原因——它把 Raft、配置管理、原子的 shard 交接全部疊在一起。這個練習做一個**聚焦核心、簡化複製**的版本：我們不重刻 Raft（練習 C 已經證明你會了），把每個 group 簡化成單節點，好讓你把全副注意力放在**分片配置與 shard 遷移的正確性**上——那才是這個練習真正要教的東西。

> 若對 `dsim` 的 API（`Node` 介面、`OnMessage`/`OnTick`、`Send`、`Crash`）不熟，回看 [Ch 0](./00-environment-setup.md)。

## 系統架構

四種角色，只靠 `net.Send` 溝通：

```
                    ┌─────────────────────┐
                    │   Shard Controller   │  管配置：哪個 group 管哪些 shard
                    │   config#N 單調遞增   │  加 group -> rebalance -> 推播新 config
                    └──────────┬──────────┘
                    推播 NewConfig │ (含當前 + 上一版)
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
        ┌──────────┐    ┌──────────┐    ┌──────────┐
        │ Group 1   │    │ Group 2   │    │ Group 3   │  每個 group 服務一部分 shard
        │ shard 0-3 │◄──►│ shard 5-7 │◄──►│ shard 4,8,9│  config 變更時彼此遷移 shard
        │ store{}   │ 遷移 │ store{}   │ 遷移 │ store{}   │  (MigrateShard/MigrateAck)
        └──────────┘    └──────────┘    └──────────┘
              ▲               ▲               ▲
              └───────────────┼───────────────┘
                     Get/Put  │ 依 key -> shard -> 查 config -> 路由到 owner group
                        ┌──────────┐
                        │  Client   │  key2shard(key) -> config.Shards[shard] -> 送到那個 group
                        └──────────┘
```

- **Shard Controller**：唯一的配置權威。維護一個單調遞增版本號的 `Config`（`Shards[i]` = 負責 shard i 的 group）。有 group 加入時，重新平衡分配、把新 config 推播給所有 group。
- **Group（複製組）**：服務被分派給它的 shard。config 變更時，該遷出的 shard 把資料送給新 owner、該遷入的等資料到齊才開始服務。
- **Client**：拿 `key` 算出 `shard = key2shard(key)`，查 config 得知 owner group，把請求送過去。若被回「wrong group」（config 過期或遷移未就緒），重試/重路由。

### 簡化處（誠實聲明）

這個參考解答相對真實系統做了三處簡化，都不影響核心（遷移正確性）：

1. **每個 group 是「單節點複製組」**（replica group of size 1），**不跑完整 Raft**。真實系統每個 group 是一個 Raft 群，config 變更和資料都要過共識。這裡把「共識」簡化成「單節點就是權威」——因為你在練習 C 已經證明你能把單一節點換成 Raft group，這裡不重複那份工，專注在分片邏輯。
2. **shard controller 也是單節點**（真實系統它自己也是一個小 Raft group，Lab 4A 就是刻它）。
3. **config 靠 controller 主動推播**給各 group（真實系統是 group 週期性 poll controller 拿最新 config）。推播 vs 拉取不影響遷移正確性。

把這三處換成 Raft，就是完整的 Lab 4——那是「延伸挑戰」的第一項。

## 任務規格

實作四種 `dsim.Node`：`controller`、`group`、`client`，以及它們之間的訊息。

### 訊息型別

| 訊息 | 方向 | 用途 |
|---|---|---|
| `NewConfig{cfg, prev}` | controller → group | 推播新配置（**含上一版 prev**，見卡關提示 2） |
| `ConfigAck{num}` | group → controller | 確認已套用某版 config |
| `MigrateShard{cfgNum, shard, data}` | 舊 owner → 新 owner | 把一個 shard 的資料整包遷過去 |
| `MigrateAck{cfgNum, shard}` | 新 owner → 舊 owner | 確認收到，舊 owner 可安心丟棄 |
| `Get{reqID, key}` / `Put{reqID, key, value}` | client → group | KV 讀寫 |
| `KVReply{reqID, ok, value, errWrong}` | group → client | 回覆；`errWrong=true` = wrong group |

### 正確性要件（驗收條件）

1. **讀寫路由正確**：client 依 key 路由到正確的 owner group，讀得到自己寫的值。
2. **遷移不丟資料**：加入 group 觸發 rebalance 後，被搬走的 shard 上所有 key 的值必須完好——遷移前 commit 的資料，遷移後一筆不能少。
3. **無重複服務**：任一時刻，每個 key 只被它**當前** config 的 owner group 持有並服務。遷出方在確認遷移完成後必須丟棄該 shard 的資料，不能兩個 group 同時服務同一個 shard。
4. **遷移中請求正確處理**：shard 正在遷移、新 owner 尚未就緒時，打到它的請求要被拒絕（回 `errWrong`），由 client 重試——不能回一個空值或錯值。

### 你要提供的介面

- `newController(id)` / `controller.join(group, net)`：新 group 加入，觸發 rebalance + 推播。
- `newGroup(id, ctrl)`：一個複製組。
- `newClient(id, cfg)` / `client.put/get(key, ..., net)`：依 key 路由的讀寫。

## 期望輸出範例

跑通後大致長這樣（實際依 seed 而定）：

```
=== Step 3：加入 group3 觸發 rebalance + shard 遷移 ===
  controller: group3 JOIN -> config#3 shards=[1 1 1 1 3 2 2 2 3 3]
  group1: 遷出 shard 4 -> group3 (1 筆資料)
  group2: 遷出 shard 9 -> group3 (1 筆資料)
  group3: 遷入 shard 4 完成 (1 筆)，開始服務
  group3: 遷入 shard 9 完成 (1 筆)，開始服務

=== Step 4：遷移後重新讀取全部 key，資料必須完好 ===
  GET banana =2 (want 2) true [已從 group2 遷到 group3]
  GET date   =4 (want 4) true [已從 group1 遷到 group3]
  ...
  遷移後資料完好：true；本次 rebalance 有 2 個 key 換了 owner
```

一眼看穿：group3 加入後 controller 重新分配（shard 4、8、9 給 group3），舊 owner 把對應 shard 的資料遷過去，遷移後 `banana`/`date` 從舊 group 搬到 group3 但值完好無損。

## 如果你卡住了

四個最會咬人的地方（先自己撞牆再看）：

1. **遷移的時序競態（migration race）**：新 owner 必須在**收到 MigrateShard 之前**就知道自己在等這個 shard（設好 `waitingIn` 旗標），否則遷移資料到達時它還不知道自己該收，就丟了。而新舊 owner 是**同時**收到 NewConfig 的——舊 owner 收到後發 MigrateShard、新 owner 收到後設 waitingIn。因為 `dsim` 的訊息有延遲（≥1 tick），只要兩者都在收到 NewConfig 的當下立刻各自處理（舊的發、新的設旗標），MigrateShard 到達時 waitingIn 早已就位。關鍵：**套用 config 時同步決定「哪些遷出（立刻發）、哪些遷入（立刻設等待旗標）」**，別拖到之後。

2. **新加入的 group 不知道 shard 的前任 owner 是誰**——這是本練習最陰險的 bug，我實際踩到過。group3 剛加入，它從一個空 config 直接跳到 config#3。它看 shard 4「現在是我的、我自己的舊 config 裡是 -1（沒人）」，於是**誤以為這是系統初始、沒有資料可遷、直接就緒服務**——結果它服務了一個空的 shard 4，`date` 的值憑空消失。根源：group3 缺席了 config#1、#2，它自己的「舊 config」是過期的，算不出 shard 4 的真正前任是 group1。**解法：controller 推播時連「上一版 config（prev）」一起送**，group 用這個權威的 prev 判斷每個 shard 的前任 owner——前任是 -1 才直接就緒，前任是某個真 group 就得等它遷資料來。這個 bug 在下面「參考解答說明」有完整覆盤。

3. **遷出方什麼時候能丟資料**：舊 owner 把 shard 送出去後，**不能立刻刪**——萬一 MigrateShard 丟了呢（`dsim` 會丟包）？得等新 owner 回 `MigrateAck` 確認收到，才安心刪。刪太早會丟資料，不刪會殘留（違反「無重複服務」——雖然舊 owner 已不服務該 shard，但 store 裡殘留的資料會讓「每個 key 只在其 owner」的檢查失敗，也浪費空間）。這是一個小型的「確認後才釋放」協定。

4. **遷移中的請求怎麼回**：一個請求打到某 group，但那個 shard 正在遷入、還沒就緒。這時**不能回空值**（client 會誤以為 key 不存在），也**不能回舊值**（可能已過期）。正確做法：回 `errWrong=true`，client 收到就等一下、重新查 config 再路由重試。判斷條件是 `own[shard] && ready[shard]`——**兩個都要**：`own` 是「config 說這 shard 是我的」，`ready` 是「資料已到位」。只有兩者皆真才服務。

## 實作步驟建議

分六步，每步能獨立驗證：

### Step 1：config 與 key 路由

定義 `Config{Num, Shards[NShards]NodeID}`、`key2shard(key)` 雜湊函式（簡單累加取模即可，確定性就好）。先讓 controller 能 `join` 一個 group、把所有 shard 分給它、推播 config。group 收到就記下自己 own 哪些 shard。**驗證：一個 group 時，所有 shard 都指向它。**

### Step 2：client 路由讀寫（無遷移）

client `put/get` 時 `key2shard(key)` → 查 config → 送到 owner group。group 服務 `own && ready` 的 shard。單 group、無遷移下，讀寫要正確。**驗證：寫入的值讀得回來。**

### Step 3：rebalance 演算法

controller 的 `rebalance`：把 NShards 盡量平均分給 groups（每組 `⌊N/G⌋` 或 `⌈N/G⌉` 個），且**盡量少搬**（保留原本就分對的 shard，只重新指派需要移動的）。「盡量少搬」很重要——每次 rebalance 搬的 shard 越少，遷移成本越低。**驗證：加第二個 group，shard 大致對半分。**

### Step 4：shard 遷移（核心）

group 套用新 config 時，逐 shard 判斷：**遷出**（前任是我、現在不是我）→ 把該 shard 資料送給新 owner；**遷入**（前任不是我、現在是我）→ 設 `waitingIn` 等資料（除非前任是 -1，系統初始無資料，直接就緒）；**續留**→ 保持就緒。收到 MigrateShard 就併入 store、標 ready、回 MigrateAck。**這是最容易錯的一步，慢慢來——尤其卡關提示 1、2 的兩個時序/前任問題。**

### Step 5：遷出方釋放 + 遷移中拒絕

舊 owner 收到 MigrateAck 才刪該 shard 資料。group 服務前檢查 `own && ready`，不滿足回 `errWrong`。client 收 `errWrong` 就重試。**驗證：遷移進行中打到未就緒 shard 的請求會重試、最終成功。**

### Step 6：端到端測試

寫測試：兩 group 加入 → 寫入若干 key、讀回驗證（要件 1）→ 加第三個 group 觸發 rebalance → 遷移後重讀全部 key 值完好（要件 2）→ 檢查每個 key 只在其當前 owner 的 store（要件 3、無重複）。**這一步才真正檢驗遷移對不對。**

## 完整參考解答

**自己先撞到 Step 6 綠燈再打開。** 尤其卡關提示 2 那個「新 group 不知前任」的 bug，自己撞到再看覆盤，收穫最大。

<details>
<summary>點開參考實作（shardkv.go + main.go，已在 WSL Go 1.18.1 真跑通過）</summary>

把 `dsim/dsim.go` 的 `package dsim` 改成 `package main` 複製到同目錄，跟下面的 `shardkv.go`、`main.go` 放一起 `go run .`（做法見 [Ch 0](./00-environment-setup.md)）。

```go
// shardkv.go
package main

// 簡化版分片 KV + shard controller（MIT 6.5840 Lab 4 精神）。
//
// 簡化處（相對真實系統）：
//  1. 每個 shard group 是「單節點複製組」（replica group of size 1），
//     不跑完整 Raft。真實系統每個 group 是一個 Raft 群，config/資料都過共識。
//     這裡把「共識」簡化成「單節點就是權威」，聚焦分片與遷移邏輯。
//  2. shard controller 也是單節點（真實系統它自己也是一個 Raft group）。
//  3. config 靠 controller 主動推播給各 group（真實系統 group 主動 poll controller）。
// 這些簡化不影響本練習的核心：config 變更下 shard 遷移不丟資料、不重複服務。

const NShards = 10

func key2shard(key string) int {
	h := 0
	for i := 0; i < len(key); i++ {
		h = h*31 + int(key[i])
	}
	if h < 0 {
		h = -h
	}
	return h % NShards
}

// ---- 配置（config）----

type Config struct {
	Num    int             // 配置版本號，單調遞增
	Shards [NShards]NodeID // Shards[i] = 負責 shard i 的 group（-1=無人負責）
}

// ---- client 與 group 之間的訊息 ----

type Get struct {
	reqID int
	key   string
}
type Put struct {
	reqID int
	key   string
	value string
}
type KVReply struct {
	reqID    int
	ok       bool   // false = 我不負責這個 shard，client 要重試/重路由
	value    string // Get 用
	errWrong bool   // true = wrong group（config 過期或遷移中尚未就緒）
}

// ---- controller 與 group 之間的訊息 ----

type NewConfig struct {
	cfg  Config
	prev Config // 上一版 config，讓新加入的 group 也能正確算出每個 shard 的前任 owner
}
type ConfigAck struct{ num int }

// shard 遷移：舊 owner 把該 shard 的資料整包送給新 owner。
type MigrateShard struct {
	cfgNum int
	shard  int
	data   map[string]string
}
type MigrateAck struct {
	cfgNum int
	shard  int
}

// ---- shard controller ----

type controller struct {
	id      NodeID
	groups  []NodeID
	cfg     Config
	prevCfg Config // 上一版，push 時一起送出
	log     func(format string, args ...interface{})
}

func newController(id NodeID) *controller {
	c := &controller{id: id}
	for i := range c.cfg.Shards {
		c.cfg.Shards[i] = -1
	}
	c.prevCfg = c.cfg
	return c
}

// 重新平衡：把 NShards 盡量平均分給 groups，並「盡量少搬」（保留原本就對的）。
func (c *controller) rebalance() {
	g := c.groups
	if len(g) == 0 {
		for i := range c.cfg.Shards {
			c.cfg.Shards[i] = -1
		}
		return
	}
	base := NShards / len(g)
	extra := NShards % len(g)
	target := map[NodeID]int{}
	for i, id := range g {
		target[id] = base
		if i < extra {
			target[id]++ // 前 extra 個 group 多分一個
		}
	}
	// 保留現有分配裡仍合法、且未超過目標數的；其餘標為 orphan 待重指派。
	count := map[NodeID]int{}
	var orphans []int
	for s := 0; s < NShards; s++ {
		owner := c.cfg.Shards[s]
		valid := false
		for _, id := range g {
			if id == owner {
				valid = true
				break
			}
		}
		if valid && count[owner] < target[owner] {
			count[owner]++
		} else {
			orphans = append(orphans, s)
		}
	}
	// orphan 依 group 順序塞給還沒滿的（確定性）。
	for _, s := range orphans {
		for _, id := range g {
			if count[id] < target[id] {
				c.cfg.Shards[s] = id
				count[id]++
				break
			}
		}
	}
}

func (c *controller) join(g NodeID, net *Net) {
	c.prevCfg = c.cfg // 記住舊版，推播時一起送
	c.groups = append(c.groups, g)
	c.cfg.Num++
	c.rebalance()
	if c.log != nil {
		c.log("controller: group%d JOIN -> config#%d shards=%v", g, c.cfg.Num, c.cfg.Shards)
	}
	c.pushConfig(net)
}

func (c *controller) pushConfig(net *Net) {
	for _, g := range c.groups {
		net.Send(Message{From: c.id, To: g, Payload: NewConfig{cfg: c.cfg, prev: c.prevCfg}})
	}
}

func (c *controller) OnMessage(m Message, net *Net) {}
func (c *controller) OnTick(now int, net *Net)      {}

// ---- shard group（單節點複製組）----

type group struct {
	id        NodeID
	ctrl      NodeID
	cfg       Config
	own       [NShards]bool // 當前 config 我該負責的 shard
	ready     [NShards]bool // 已就緒（資料到位）可服務的 shard
	store     map[string]string
	waitingIn map[int]bool // shard -> 等待遷入
	log       func(format string, args ...interface{})
}

func newGroup(id, ctrl NodeID) *group {
	g := &group{id: id, ctrl: ctrl, store: map[string]string{}, waitingIn: map[int]bool{}}
	for i := range g.cfg.Shards {
		g.cfg.Shards[i] = -1
	}
	return g
}

func (g *group) applyConfig(cfg, prev Config, net *Net) {
	if cfg.Num <= g.cfg.Num {
		return // 舊 config，忽略
	}
	g.cfg = cfg

	var newOwn [NShards]bool
	for s := 0; s < NShards; s++ {
		newOwn[s] = cfg.Shards[s] == g.id
	}

	// 用「controller 給的權威上一版 prev」判斷每個 shard 的前任 owner，
	// 而不是本 group 自己可能過期/缺席的舊 config——這是新加入 group 也能
	// 正確等待遷入資料的關鍵（見「參考解答說明」的 bug 覆盤）。
	for s := 0; s < NShards; s++ {
		wasMine := prev.Shards[s] == g.id
		nowMine := newOwn[s]
		switch {
		case !wasMine && nowMine:
			// 遷入：前任是 -1（系統初始，無資料）直接就緒；否則等前任 owner 送資料。
			if prev.Shards[s] == -1 {
				g.ready[s] = true
			} else {
				g.ready[s] = false
				g.waitingIn[s] = true // 先設等待旗標，遷移資料到達才收得到
			}
		case wasMine && !nowMine:
			// 遷出：把該 shard 資料送給新 owner，並停止服務。
			g.ready[s] = false
			if newOwner := cfg.Shards[s]; newOwner >= 0 {
				g.sendShard(s, newOwner, cfg.Num, net)
			}
		case wasMine && nowMine:
			g.ready[s] = true // 續留
		}
	}
	g.own = newOwn

	if g.log != nil {
		g.log("group%d: apply config#%d, own=%s ready=%s", g.id, cfg.Num, shardsStr(g.own), shardsStr(g.ready))
	}
	net.Send(Message{From: g.id, To: g.ctrl, Payload: ConfigAck{num: cfg.Num}})
}

func (g *group) sendShard(s int, to NodeID, cfgNum int, net *Net) {
	data := map[string]string{}
	for k, v := range g.store {
		if key2shard(k) == s {
			data[k] = v
		}
	}
	if g.log != nil {
		g.log("group%d: 遷出 shard %d -> group%d (%d 筆資料)", g.id, s, to, len(data))
	}
	net.Send(Message{From: g.id, To: to, Payload: MigrateShard{cfgNum: cfgNum, shard: s, data: data}})
}

func (g *group) OnMessage(m Message, net *Net) {
	switch msg := m.Payload.(type) {
	case NewConfig:
		g.applyConfig(msg.cfg, msg.prev, net)

	case MigrateShard:
		// 收到前任 owner 送來的 shard 資料。
		if g.own[msg.shard] && g.waitingIn[msg.shard] {
			for k, v := range msg.data {
				g.store[k] = v
			}
			g.ready[msg.shard] = true
			delete(g.waitingIn, msg.shard)
			if g.log != nil {
				g.log("group%d: 遷入 shard %d 完成 (%d 筆)，開始服務", g.id, msg.shard, len(msg.data))
			}
		}
		// 一律回 ack（即使重複收到），讓舊 owner 能釋放。
		net.Send(Message{From: g.id, To: m.From, Payload: MigrateAck{cfgNum: msg.cfgNum, shard: msg.shard}})

	case MigrateAck:
		// 舊 owner 收到確認，安心丟掉該 shard 資料（避免重複服務同一 key）。
		for k := range g.store {
			if key2shard(k) == msg.shard {
				delete(g.store, k)
			}
		}

	case Get:
		s := key2shard(msg.key)
		if !g.own[s] || !g.ready[s] { // own && ready 兩者皆真才服務
			net.Send(Message{From: g.id, To: m.From, Payload: KVReply{reqID: msg.reqID, ok: false, errWrong: true}})
			return
		}
		net.Send(Message{From: g.id, To: m.From, Payload: KVReply{reqID: msg.reqID, ok: true, value: g.store[msg.key]}})

	case Put:
		s := key2shard(msg.key)
		if !g.own[s] || !g.ready[s] {
			net.Send(Message{From: g.id, To: m.From, Payload: KVReply{reqID: msg.reqID, ok: false, errWrong: true}})
			return
		}
		g.store[msg.key] = msg.value
		net.Send(Message{From: g.id, To: m.From, Payload: KVReply{reqID: msg.reqID, ok: true}})
	}
}

func (g *group) OnTick(now int, net *Net) {}

// ---- client：依 key 路由到對應 group，wrong group 就重路由/重試 ----

type client struct {
	id      NodeID
	cfg     *Config // 簡化：client 直接看 controller 的最新 cfg（真實系統 client 也要 poll config）
	nextReq int
	results map[int]KVReply
	pending map[int]bool
}

func newClient(id NodeID, cfg *Config) *client {
	return &client{id: id, cfg: cfg, results: map[int]KVReply{}, pending: map[int]bool{}}
}

func (c *client) OnMessage(m Message, net *Net) {
	if r, ok := m.Payload.(KVReply); ok {
		c.results[r.reqID] = r
		delete(c.pending, r.reqID)
	}
}
func (c *client) OnTick(now int, net *Net) {}

func (c *client) put(key, value string, net *Net) int {
	c.nextReq++
	rid := c.nextReq
	g := c.cfg.Shards[key2shard(key)] // 依 key -> shard -> 當前 owner group
	c.pending[rid] = true
	net.Send(Message{From: c.id, To: g, Payload: Put{reqID: rid, key: key, value: value}})
	return rid
}
func (c *client) get(key string, net *Net) int {
	c.nextReq++
	rid := c.nextReq
	g := c.cfg.Shards[key2shard(key)]
	c.pending[rid] = true
	net.Send(Message{From: c.id, To: g, Payload: Get{reqID: rid, key: key}})
	return rid
}

// ---- 小工具 ----

func shardsStr(b [NShards]bool) string {
	s := "["
	for i := 0; i < NShards; i++ {
		if b[i] {
			s += "1"
		} else {
			s += "."
		}
	}
	return s + "]"
}
```

測試 driver：

```go
// main.go
package main

import "fmt"

// 帶重試的同步 client helper：發請求，跑模擬直到有回覆；
// 若回 wrong group（config 過期/遷移中），等一下再依最新 cfg 路由重試。
func doPut(cl *client, key, val string, net *Net) {
	for attempt := 0; attempt < 50; attempt++ {
		rid := cl.put(key, val, net)
		net.Run(net.Now() + 15)
		if r, ok := cl.results[rid]; ok && r.ok {
			return
		}
		net.Run(net.Now() + 10) // wrong group：等遷移完成再重試
	}
	fmt.Printf("  !! doPut(%s) 重試耗盡\n", key)
}

func doGet(cl *client, key string, net *Net) string {
	for attempt := 0; attempt < 50; attempt++ {
		rid := cl.get(key, net)
		net.Run(net.Now() + 15)
		if r, ok := cl.results[rid]; ok && r.ok {
			return r.value
		}
		net.Run(net.Now() + 10)
	}
	return "<TIMEOUT>"
}

func main() {
	net := NewNet(4)
	net.SetLatency(1, 2)

	ctrlID := NodeID(0)
	ctrl := newController(ctrlID)
	ctrl.log = func(f string, a ...interface{}) { fmt.Printf("  %s\n", fmt.Sprintf(f, a...)) }
	net.Add(ctrlID, ctrl)

	g1 := newGroup(NodeID(1), ctrlID)
	g2 := newGroup(NodeID(2), ctrlID)
	for _, g := range []*group{g1, g2} {
		g.log = func(f string, a ...interface{}) { fmt.Printf("  %s\n", fmt.Sprintf(f, a...)) }
		net.Add(g.id, g)
	}

	cl := newClient(NodeID(9), &ctrl.cfg)
	net.Add(cl.id, cl)

	fmt.Println("=== Step 1：兩個 group 加入，config#1/#2 分配 shard ===")
	ctrl.join(NodeID(1), net)
	net.Run(net.Now() + 20)
	ctrl.join(NodeID(2), net)
	net.Run(net.Now() + 20)

	fmt.Println("\n=== Step 2：client 依 key 路由寫入 6 筆，讀回驗證 ===")
	kvs := map[string]string{
		"apple": "1", "banana": "2", "cherry": "3",
		"date": "4", "egg": "5", "fig": "6",
	}
	order := []string{"apple", "banana", "cherry", "date", "egg", "fig"} // 固定順序，確定性
	for _, k := range order {
		doPut(cl, k, kvs[k], net)
		fmt.Printf("  PUT %-7s=%s -> shard %d -> group%d\n", k, kvs[k], key2shard(k), ctrl.cfg.Shards[key2shard(k)])
	}
	fmt.Println("  -- 讀回 --")
	allok := true
	for _, k := range order {
		v := doGet(cl, k, net)
		ok := v == kvs[k]
		if !ok {
			allok = false
		}
		fmt.Printf("  GET %-7s=%s (want %s) %v\n", k, v, kvs[k], ok)
	}
	fmt.Printf("  Step 2 讀寫路由正確：%v\n", allok)

	fmt.Println("\n=== Step 3：加入 group3 觸發 rebalance + shard 遷移 ===")
	g3 := newGroup(NodeID(3), ctrlID)
	g3.log = func(f string, a ...interface{}) { fmt.Printf("  %s\n", fmt.Sprintf(f, a...)) }
	net.Add(g3.id, g3)
	beforeOwner := map[string]NodeID{}
	for _, k := range order {
		beforeOwner[k] = ctrl.cfg.Shards[key2shard(k)]
	}
	ctrl.join(NodeID(3), net)
	net.Run(net.Now() + 60) // 讓遷移完成

	fmt.Println("\n=== Step 4：遷移後重新讀取全部 key，資料必須完好 ===")
	migrated := 0
	allok2 := true
	for _, k := range order {
		afterOwner := ctrl.cfg.Shards[key2shard(k)]
		if afterOwner != beforeOwner[k] {
			migrated++
		}
		v := doGet(cl, k, net)
		ok := v == kvs[k]
		if !ok {
			allok2 = false
		}
		mark := ""
		if afterOwner != beforeOwner[k] {
			mark = fmt.Sprintf(" [已從 group%d 遷到 group%d]", beforeOwner[k], afterOwner)
		}
		fmt.Printf("  GET %-7s=%s (want %s) %v%s\n", k, v, kvs[k], ok, mark)
	}
	fmt.Printf("\n  遷移後資料完好：%v；本次 rebalance 有 %d 個 key 換了 owner\n", allok2, migrated)

	fmt.Println("\n=== Step 5：驗證「無重複服務」——遷出方已丟棄該 shard 資料 ===")
	dup := false
	groups := map[NodeID]*group{1: g1, 2: g2, 3: g3}
	for _, k := range order {
		owner := ctrl.cfg.Shards[key2shard(k)]
		holders := []NodeID{}
		for gid, g := range groups {
			if _, has := g.store[k]; has {
				holders = append(holders, gid)
			}
		}
		if len(holders) != 1 || holders[0] != owner {
			dup = true
			fmt.Printf("  !! key %s owner=group%d 但存在於 %v\n", k, owner, holders)
		}
	}
	if !dup {
		fmt.Println("  每個 key 只存在於其當前 owner group —— 無重複服務、無殘留。")
	}

	fmt.Println("\n=== 總結 ===")
	fmt.Printf("  config 最終版本 #%d，shard 分配 = %v\n", ctrl.cfg.Num, ctrl.cfg.Shards)
	if allok && allok2 && !dup {
		fmt.Println("  PASS：讀寫路由正確、遷移不丟資料、無重複服務。")
	} else {
		fmt.Println("  FAIL")
	}
}
```

真跑（WSL, Go 1.18.1，`go run .`，seed=4）：

```
=== Step 1：兩個 group 加入，config#1/#2 分配 shard ===
  controller: group1 JOIN -> config#1 shards=[1 1 1 1 1 1 1 1 1 1]
  group1: apply config#1, own=[1111111111] ready=[1111111111]
  controller: group2 JOIN -> config#2 shards=[1 1 1 1 1 2 2 2 2 2]
  group1: 遷出 shard 5 -> group2 (0 筆資料)
  group1: 遷出 shard 6 -> group2 (0 筆資料)
  group1: 遷出 shard 7 -> group2 (0 筆資料)
  group1: 遷出 shard 8 -> group2 (0 筆資料)
  group1: 遷出 shard 9 -> group2 (0 筆資料)
  group1: apply config#2, own=[11111.....] ready=[11111.....]
  group2: apply config#2, own=[.....11111] ready=[..........]
  group2: 遷入 shard 7 完成 (0 筆)，開始服務
  group2: 遷入 shard 5 完成 (0 筆)，開始服務
  group2: 遷入 shard 6 完成 (0 筆)，開始服務
  group2: 遷入 shard 8 完成 (0 筆)，開始服務
  group2: 遷入 shard 9 完成 (0 筆)，開始服務

=== Step 2：client 依 key 路由寫入 6 筆，讀回驗證 ===
  PUT apple  =1 -> shard 0 -> group1
  PUT banana =2 -> shard 9 -> group2
  PUT cherry =3 -> shard 3 -> group1
  PUT date   =4 -> shard 4 -> group1
  PUT egg    =5 -> shard 7 -> group2
  PUT fig    =6 -> shard 0 -> group1
  -- 讀回 --
  GET apple  =1 (want 1) true
  GET banana =2 (want 2) true
  GET cherry =3 (want 3) true
  GET date   =4 (want 4) true
  GET egg    =5 (want 5) true
  GET fig    =6 (want 6) true
  Step 2 讀寫路由正確：true

=== Step 3：加入 group3 觸發 rebalance + shard 遷移 ===
  controller: group3 JOIN -> config#3 shards=[1 1 1 1 3 2 2 2 3 3]
  group1: 遷出 shard 4 -> group3 (1 筆資料)
  group1: apply config#3, own=[1111......] ready=[1111......]
  group2: 遷出 shard 8 -> group3 (0 筆資料)
  group2: 遷出 shard 9 -> group3 (1 筆資料)
  group2: apply config#3, own=[.....111..] ready=[.....111..]
  group3: apply config#3, own=[....1...11] ready=[..........]
  group3: 遷入 shard 4 完成 (1 筆)，開始服務
  group3: 遷入 shard 9 完成 (1 筆)，開始服務
  group3: 遷入 shard 8 完成 (0 筆)，開始服務

=== Step 4：遷移後重新讀取全部 key，資料必須完好 ===
  GET apple  =1 (want 1) true
  GET banana =2 (want 2) true [已從 group2 遷到 group3]
  GET cherry =3 (want 3) true
  GET date   =4 (want 4) true [已從 group1 遷到 group3]
  GET egg    =5 (want 5) true
  GET fig    =6 (want 6) true

  遷移後資料完好：true；本次 rebalance 有 2 個 key 換了 owner

=== Step 5：驗證「無重複服務」——遷出方已丟棄該 shard 資料 ===
  每個 key 只存在於其當前 owner group —— 無重複服務、無殘留。

=== 總結 ===
  config 最終版本 #3，shard 分配 = [1 1 1 1 3 2 2 2 3 3]
  PASS：讀寫路由正確、遷移不丟資料、無重複服務。
```

三個要件全綠。因為是確定性模擬，你用同樣的 seed=4 跑，會拿到**一模一樣**的 shard 分配、遷移順序、config 版本。

### 解答說明（每個關鍵決策為何這樣）

- **`own && ready` 兩個旗標分開**：`own` 是「config 說這 shard 屬於我」，`ready` 是「資料已經到位」。遷入中的 shard `own=true` 但 `ready=false`——**必須兩者皆真才服務**。只看 `own` 會在資料還沒到時就服務空 shard（丟資料誤判為 key 不存在）；只看 `ready` 邏輯上不完整。這對旗標把「config 意圖」和「資料實況」解耦，是遷移正確性的核心。

- **推播 config 帶上 `prev`（本練習最重要的一行）**：這是我實際踩到的 bug 的解法。**第一版沒帶 prev**，group 用自己的舊 config 算前任 owner。結果新加入的 group3 缺席了 config#1、#2，它自己的舊 config 是空的（全 -1），於是看 shard 4「現在是我的、舊 config 裡是 -1」→ 誤判「系統初始無資料，直接就緒」→ 服務了一個空的 shard 4，`date` 的值消失。真跑印出來是 `GET date= (want 4) false`。根源：**group3 沒有全域配置歷史，算不出 shard 4 的真正前任是 group1**。修法是讓 controller（它有全域視角）在推播時把權威的上一版 config 一起送，group 用它判斷前任。這個 bug 完美示範了分散式系統的一個通則：**節點的局部視角常常不足以做正確決定，需要有全域視角的角色（這裡是 controller）補上缺失的資訊**。

- **確認後才釋放（MigrateAck 才刪）**：遷出方送出 MigrateShard 後不立刻刪，等新 owner 回 MigrateAck 才刪。因為 `dsim` 會丟包，MigrateShard 可能沒到——太早刪就兩邊都沒資料。這是最小的「至少一次交付 + 冪等接收」協定：新 owner 一律回 ack（即使重複收到也回），舊 owner 收到才釋放。

- **rebalance「盡量少搬」**：`rebalance` 保留現有分配裡仍合法的 shard，只重指派 orphan。加第三個 group 時，config#2 的 shard 0-4（group1）大多保留、只把超出目標數的挪給 group3。若每次 rebalance 都全部重洗，遷移成本會爆炸。這對應真實系統「一致性雜湊」[Ch 28](./28-consistent-hashing.md) 想達到的「加節點只搬 1/N 資料」目標。

- **client 直接看 `&ctrl.cfg`**：這是簡化——真實系統 client 也要向 controller poll config、拿到才路由，路由錯（config 過期）會被 group 回 `errWrong` 而重試。這裡讓 client 直接持有 controller 的 config 指標省掉那層 poll，但**保留了 `errWrong` 重試路徑**（遷移中未就緒時仍會觸發），所以遷移正確性的驗證不打折。

</details>

## 測試用例表

| 測試 | seed / 設定 | 觸發的變更 | 驗收條件 | 對應要件 |
|---|---|---|---|---|
| Step 2 路由讀寫 | seed=4, 2 group, lat 1-2 | 無 | 6 筆 key 依 shard 路由、讀回值正確 | (1) |
| Step 3-4 遷移 | seed=4 | `join(group3)` → rebalance | 遷移後全部 key 值完好、有 key 換 owner | (2) |
| Step 5 無重複 | seed=4 | 同上 | 每個 key 只在其當前 owner 的 store | (3) |
| （隱含）遷移中請求 | seed=4 | 遷移進行中 | 打到未就緒 shard 的請求回 errWrong → 重試成功 | (4) |

延伸你可以自己加的：`SetDropRate(0.1)` 看丟包下遷移還能不能完成（要件是舊 owner 收不到 MigrateAck 就得重送 MigrateShard——參考解答目前**沒做重送**，丟包下會卡住，這正是「延伸挑戰」第 2 項）；連續加 group 4、5 看多次 rebalance 累積是否仍正確；把某 group `Crash` 後看它的 shard 如何轉交（需要 controller 偵測失敗，接 [Ch 29](./29-membership-failure-detection-swim.md) 的 SWIM）。

## 延伸挑戰

刻完基本盤後，往下再挖三層（難度遞增）：

1. **把每個 group 換成真 Raft group**：這是把本練習升級成完整 MIT 6.5840 Lab 4 的關鍵一步。用[練習 C](./practice-c-build-raft.md) 的 Raft 取代單節點：每個 group 是 3 或 5 節點的 Raft 群，config 變更和 KV 寫入都當成命令過 Raft log。難點：shard 遷移本身也要過共識（不能 leader 私自遷移然後掛掉），且 config 變更要在 Raft log 裡定序，避免不同 replica 對「現在是 config 幾」看法不一。

2. **遷移的丟包重送**：目前參考解答假設 MigrateShard 不丟。加上：舊 owner 送出後若一段時間沒收到 MigrateAck 就重送（在 `OnTick` 裡計時）；新 owner 對重複的 MigrateShard 冪等處理（已收過就只回 ack、不重複併入）。這樣才能在 `SetDropRate(0.1)` 下仍完成遷移。

3. **shard controller 自己做成 Raft group**（Lab 4A）：controller 是本練習唯一的單點。把它換成一個小 Raft 群，`join`/`leave`/`move` 這些配置操作都過共識，config 歷史複製到多數。這樣 controller 掛一台也不影響配置服務。做完這個 + 挑戰 1，你就有一個真正容錯的分片 KV——Spanner/TiDB 的教學等級骨架。

## 自我檢核

不看程式碼，主動回想以下問題——答不出來就是還沒真懂：

- [ ] client 拿到一個 key，要經過哪幾步才知道該送到哪個 group？config 過期時會發生什麼、怎麼修正？
- [ ] 一個 shard 從 group A 遷到 group B，A 什麼時候能停止服務、什麼時候能刪資料、B 什麼時候能開始服務？三個時間點的順序為什麼不能顛倒？
- [ ] 為什麼新 owner 要在**收到遷移資料之前**就設好 `waitingIn` 旗標？如果它是收到資料才臨時決定要不要收，會出什麼問題？
- [ ] 為什麼「新加入的 group」算不出某 shard 的前任 owner？controller 帶上 `prev` config 怎麼解掉這個問題？這反映了分散式系統的什麼通則？
- [ ] `own` 和 `ready` 兩個旗標各代表什麼？為什麼服務請求要**兩者皆真**、少檢查一個各會出什麼災難？
- [ ] 遷移中打到「正在遷入、尚未就緒」shard 的請求，為什麼不能回空值、也不能回舊值？正確做法是什麼？
- [ ] 「無重複服務」這個要件，是靠哪個機制保證的（哪個訊息、哪個刪除時機）？拿掉它會怎樣？

刻完並跑綠這三個要件，你就把整個 Part 4 縫成了一個能水平擴展、能安全遷移的分片儲存——這正是 final project「容錯分片 KV Store」的核心骨架。下一 Part 我們換一個視角：如果節點不只會當機，還會**說謊**（拜占庭故障）呢？

→ [Ch 32 拜占庭將軍問題](./32-byzantine-generals.md)
