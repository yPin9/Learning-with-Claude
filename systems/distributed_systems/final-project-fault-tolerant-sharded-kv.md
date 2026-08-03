# Final Project — 容錯分片 KV Store

> **目標**：把整門課縫成一個系統。整合 **Raft**（練習 C）＋ **分片 + shard controller**（練習 D）＋ **線性一致讀 ReadIndex**（[Ch 26](./26-raft-kv-linearizable-reads.md)）＋ **動態 membership**，並用 **Jepsen 風格 fault injection**（[Ch 43](./43-testing-distributed-systems.md) 的 nemesis：partition / crash / clock skew）驗證線性一致性。做完，你手上是一個能水平擴展、能容錯、經得起對抗性測試的分片 KV store——Spanner / TiDB / CockroachDB 的教學等級骨架。

> **環境**：Go 1.18.1, WSL2 / Linux x86-64。純標準庫。所有輸出以 WSL 實測為準。

## 這個專案在做什麼

一句話：**多個 Raft group，每個管一部分 shard，一個 controller 協調配置，client 線性一致地讀寫，注入故障後仍不丟已 commit 資料、不違反線性一致。**

```
                    ┌─────────────────────────┐
                    │   Shard Controller        │  配置權威（自己也是一個小 Raft group）
                    │   config#N 單調遞增        │  join/leave -> rebalance -> 推播新 config
                    └────────────┬─────────────┘
              推播 NewConfig（含 prev）│
        ┌──────────────────┬────────┴────────┬──────────────────┐
        ▼                  ▼                  ▼
  ┌───────────┐      ┌───────────┐      ┌───────────┐
  │ Raft Group1│      │ Raft Group2│      │ Raft Group3│  每個 group = 3-5 節點 Raft 群
  │ n0,n1,n2   │◄────►│ n3,n4,n5   │◄────►│ n6,n7,n8   │  config 變更觸發 shard 遷移
  │ shard 0-3  │ 遷移  │ shard 4-6  │ 遷移  │ shard 7-9  │  遷移本身也過 Raft log
  │ Raft log   │      │ Raft log   │      │ Raft log   │  ReadIndex 保線性一致讀
  └───────────┘      └───────────┘      └───────────┘
        ▲                  ▲                  ▲
        └──────────────────┼──────────────────┘
              Get/Put + 重試 │ key -> shard -> 查 config -> 路由到 owner group 的 leader
                       ┌───────────┐
                       │  Client    │  依 config 路由；wrong group / not leader 就重試
                       └───────────┘
```

比較它和兩個前置練習的關係：

| 元件 | 練習 C | 練習 D | 這個 final |
|---|---|---|---|
| 複製 | 真 Raft（單 group）| 單節點（簡化）| **真 Raft（多 group）** |
| 分片 | 無 | 有 | 有 |
| controller | 無 | 單節點 | **Raft group（或單節點起步）** |
| 線性一致讀 | 無（naive）| 無 | **ReadIndex** |
| 故障驗證 | 三個手寫測試 | 三個要件 | **Jepsen nemesis 掃 seed + 線性一致檢查** |

**這個 final 的獨特難點**（前兩個練習都沒有）：把「真 Raft 複製」和「分片遷移」疊在一起。練習 D 為了聚焦分片，把 group 簡化成單節點；練習 C 有真 Raft 但只有一個 group。真正難的地方在它們的**交界**：shard 遷移本身也要過共識（不能 leader 私自遷移然後掛掉），config 變更要在 Raft log 裡定序（避免同 group 不同 replica 對「現在是 config 幾」看法不一）。這正是 MIT 6.5840 Lab 4 被公認全課最難的原因。

## 分階段里程碑

別想一次做完。分三個里程碑，每個都能獨立跑綠再往下——這本身就是[反模式 6](./45-design-pitfalls.md)（別過度分散式）的實踐：先讓最小的東西對，再加複雜度。

```
M1: 單 group 線性一致 KV        M2: 加分片與 controller       M3: 加 nemesis 驗證
┌──────────────────────┐      ┌──────────────────────┐    ┌──────────────────────┐
│ 一個 Raft group        │      │ 多 group、每個管數 shard│    │ Jepsen nemesis:        │
│ KV 狀態機掛在 log 上    │  ──► │ controller 管 config    │──► │ partition/crash/skew   │
│ ReadIndex 線性一致讀    │      │ config 變更觸發遷移      │    │ 掃 seed 驗線性一致      │
│ 過期 leader 讀被拒絕    │      │ 遷移過 Raft、不丟資料    │    │ heal 後收斂、無資料遺失 │
└──────────────────────┘      └──────────────────────┘    └──────────────────────┘
  [跑通] 本文真跑（PASS）        架構 + 骨架（理論預期）      [跑通] 本文真跑（與 M1 合併驗）
```

- **M1（本文真跑）**：把練習 C 的 Raft 疊上一個 KV 狀態機，加 ReadIndex 線性一致讀。單 group、無分片。這是整個系統的正確性核心。
- **M2（架構 + 骨架，標理論預期）**：加多 group 與 shard controller，config 變更觸發遷移，遷移過 Raft。這是把練習 D 的分片邏輯升級成「每個 group 是真 Raft」。本文給架構與關鍵整合處的骨架，完整多 group 整合跑動標「理論預期」並給驗證步驟——因為它是練習 C + 練習 D 兩份已跑通程式碼的機械化組裝，篇幅所限不全貼。
- **M3（本文真跑，與 M1 合併）**：用 [Ch 43](./43-testing-distributed-systems.md) 的 nemesis 對系統注入 partition/crash，掃多個 seed 驗線性一致性。本文在 M1 上真跑這一關。

## M1：單 group 線性一致 KV（真跑）

### KV 狀態機疊在 Raft 之上

Raft（練習 C）給我們一個容錯的 log。**複製狀態機（RSM，[Ch 25](./25-replicated-state-machine.md)）**的核心洞見：把任意確定性狀態機掛在這個 log 上，apply 每一筆 committed 命令，就得到一個容錯的服務。KV store 就是最簡單的狀態機——`put(k,v)` 進 log，apply 時寫進 map。

```go
// KV 命令進 Raft log；apply 時套用到 store。
type KVCmd struct { Op, Key string; Val int }

type KVNode struct {
	raft    *Raft          // 練習 C 的 Raft，原封不動
	store   map[string]int // 狀態機
	applied int            // 已套用到 store 的 log index
}

func (kv *KVNode) OnMessage(m Message, net *Net) { kv.raft.OnMessage(m, net); kv.applyToStore() }
func (kv *KVNode) OnTick(now int, net *Net)      { kv.raft.OnTick(now, net); kv.applyToStore() }

// 把 Raft 已 commit 的命令套用到 store（RSM 的核心動作）。
func (kv *KVNode) applyToStore() {
	for kv.applied < len(kv.raft.applied) {
		cmd := kv.raft.applied[kv.applied]; kv.applied++
		if c, ok := cmd.(KVCmd); ok && c.Op == "put" { kv.store[c.Key] = c.Val }
	}
}

func (kv *KVNode) Put(key string, val int, net *Net) bool {
	return kv.raft.Propose(KVCmd{Op: "put", Key: key, Val: val}, net)
}
```

寫入很直接：`Propose` 進 log、走 Raft 複製、commit 後 apply。**讀才是陷阱。**

### ReadIndex：為什麼 naive 的 leader 讀不安全

最誘人的錯誤：「讀就直接回 leader 的 store 嘛，leader 有最新資料。」

**這會違反線性一致。** 一個被分進少數派的舊 leader，**不知道自己已被取代**——它看到的只是「其他節點都沒回應」，這和「其他節點都掛了」長得一模一樣（[反模式 2](./45-design-pitfalls.md) 部分失敗）。它會拿著過期的 store 回你一個舊值（stale read），而此刻多數派那邊早已 commit 了新值。

這不是假想。我們先寫一個 naive 版（`role==leader` 就回讀），跑 M3 的 nemesis 攻擊，**它真的違反了**：

```
=== Final M1+M3：Raft-backed 線性一致 KV + ReadIndex + Jepsen nemesis (seed=3) ===
  初始 leader = node0 (term=1)
  !! 線性一致違反 @now=150 讀到 12 但讀前已 commit >= 13
  !! 線性一致違反 @now=385 讀到 45 但讀前已 commit >= 46
  !! 線性一致違反 @now=390 讀到 45 但讀前已 commit >= 46
```

`@now=150 讀到 12 但讀前已 commit >= 13`——一個舊 leader 回了 12，但系統早已 commit 了 13。這就是 [Ch 26](./26-raft-kv-linearizable-reads.md) 存在的全部理由。挖進去看那個時刻的真實狀態（用 seed=3 精準重現、print 出來，這正是 [Ch 43](./43-testing-distributed-systems.md) DST 的價值）：

```
now=150：value 13 已 commit 在多數派 {0,1,3,4}（term 1）
         node2 是 term 2 的 leader，但它的 log 只到 value 12——
         它在分區裡贏了 term-2 選舉，卻缺了前任 term-1 已 commit 的 13。
         naive 讀從 node2 回 12 = stale read = 線性一致違反。
```

**ReadIndex 用兩道關卡擋掉這個**：

```go
func (kv *KVNode) LinearizableRead(key string, net *Net) (int, bool) {
	r := kv.raft
	if r.role != leader {
		return 0, false // 關卡 0：不是 leader，client 重試別的節點
	}
	// 關卡 1：這一刻我能觸及多數派嗎？（真實系統靠一輪心跳 quorum 確認；
	//         確定性模擬裡「能觸及多數」等價於「心跳能到多數」，直接用可達性當 oracle）
	reachable := 1
	for _, p := range r.peers {
		if p != r.id && net.reachable(r.id, p) && net.reachable(p, r.id) { reachable++ }
	}
	if reachable < r.majority() {
		return 0, false // 觸不到多數 = 我可能是過期 leader，拒絕讀
	}
	// 關卡 2（Raft 論文 §8）：新當選 leader 在「本任期尚未 commit 任何 entry」前，
	//   不知道自己真正的 commitIndex（可能缺前任已 commit 的 entry）。
	//   必須等本任期第一筆 entry（實務上一筆 no-op）commit 後才能安全讀。
	if r.log[r.commitIndex].Term != r.currentTerm {
		return 0, false // 本任期還沒 commit 過，commitIndex 不可信，拒絕讀
	}
	kv.applyToStore()
	v, ok := kv.store[key]
	if !ok { return 0, true }
	return v, true
}
```

- **關卡 1（心跳 quorum 確認）**：擋掉「被分進少數派的舊 leader」——它觸不到多數，拒絕服務讀。
- **關卡 2（本任期 no-op 規則）**：擋掉「剛當選、log 較短的新 leader」——上面 node2 那個情況。它本任期還沒 commit 過東西，`commitIndex` 不可信，拒絕讀。

這兩關是 [Ch 26](./26-raft-kv-linearizable-reads.md) ReadIndex 的完整版。**少任何一關都會 stale read**——上面三個違反裡，`@now=150` 是關卡 2 沒擋（新 leader log 短），另外兩個是關卡 1 沒擋（舊 leader 在少數派）。這是「把 safety 規則拿掉就出事」的又一鐵證。

## M3：Jepsen nemesis 驗證（與 M1 合併真跑）

現在把 ReadIndex 版接上 [Ch 43](./43-testing-distributed-systems.md) 的 nemesis，讓它全程攪局，同時驗三個不變式：

1. **無 committed 資料遺失**：任一 index 一旦被觀測到 commit，內容永遠不變。
2. **無線性一致違反**：任何成功的線性一致讀，回傳值不得比「讀開始前已被觀測 commit 的值」更舊（值不能倒退）。
3. **過期讀被正確拒絕**：非 leader、或觸不到多數、或本任期未 commit 的節點，讀請求回 `false` 讓 client 重試，**不回過期值**。

nemesis 隨機注入 partition / crash / heal / restart（全走 `net.rng` 保確定），client 不斷寫、不斷做線性一致讀。真跑（WSL, Go 1.18.1，`go run .`，seed=3，5 節點）：

```
=== Final M1+M3：Raft-backed 線性一致 KV + ReadIndex + Jepsen nemesis (seed=3) ===
  初始 leader = node0 (term=1)

  跑完 900 步：寫嘗試=79 成功讀=76 過期讀被正確拒絕=18
  已觀測 commit 的最新值 x=75
  node0 store[x]=79 commit=74
  node1 store[x]=79 commit=74
  node2 store[x]=79 commit=74
  node3 store[x]=79 commit=74
  node4 store[x]=79 commit=74

  PASS：nemesis 全程攪局，無 committed 資料遺失、無線性一致違反、過期讀被正確拒絕。
        heal 後所有節點 store 收斂一致。
```

對照上面 naive 版噴出三個違反——加了 ReadIndex 的兩道關卡後，**全部消失**：76 次成功讀無一違反，18 次讀在故障期間被正確拒絕（client 會重試），五個節點最終 store 全部收斂到 79。掃多個 seed 也全綠：

```
seed=3  ... PASS（過期讀被正確拒絕=18）
seed=5  ... PASS（過期讀被正確拒絕=17）
seed=7  ... PASS（過期讀被正確拒絕=14）
seed=11 ... PASS（過期讀被正確拒絕=5）
seed=42 ... PASS（過期讀被正確拒絕=29）
```

> **一個誠實的踩雷覆盤**：我第一版的驗證 harness 用了一個 package 全域的 `lastCommitted` map 記錄已 commit 內容，掃多 seed 時**不同 seed 共用同一個 map**——seed A 在 index 40 commit `w39`、seed B 在 index 40 commit 別的，就誤報「已 commit 資料變更」。這不是系統的 bug，是**測試自己的 bug**——正是 [Ch 43](./43-testing-distributed-systems.md) 踩雷第 2 條「測試自己有 bug、誤報」的活教材。修法：把 `lastCommitted` 改成每個 run 專屬。這也再次證明：**先確認你的檢查器抓得到已知 bug、且不會誤報，綠燈才可信。**

> **另一個誠實聲明**：M1+M3 用的線性一致性檢查是「單 key 值單調遞增 + 讀不倒退」的簡化模型，不是 [Ch 43](./43-testing-distributed-systems.md) 那種對任意歷史找合法線性化順序的通用檢查器。它足以抓出 stale read（實測抓到了 naive 版的三個違反），但沒有覆蓋所有可能的線性一致異常。要完整驗證，該把 client 的每個操作記成 `(invoke, response, op, ret)` 歷史，跑通用線性一致性檢查器——這是延伸挑戰。

## M2：分片與 shard controller（架構 + 骨架，理論預期）

M1 給了「單 group 線性一致 KV」。M2 把它水平擴展：多個 group、每個管一部分 shard、一個 controller 協調。這是把[練習 D](./practice-d-sharded-kv.md) 的分片邏輯升級成「每個 group 是真 Raft group」。

### 關鍵整合處：三個「疊起來才出現」的難點

練習 C（真 Raft）和練習 D（分片）分開都跑通了。組裝時，難的是它們的交界：

**難點 1：config 變更要進 Raft log 定序。** 練習 D 的單節點 group 收到 `NewConfig` 直接套用。但真 Raft group 有多個 replica——如果 leader 私自套用 config 然後掛了，新 leader 不知道套過沒，不同 replica 對「現在是 config 幾」看法不一。**解法**：把 config 變更當成一筆命令 `ConfigCmd{cfg, prev}` 提進 Raft log，走共識定序。每個 replica 在 **apply** 這筆命令時才套用 config——這樣所有 replica 依 log 順序看到完全相同的 config 序列。

```go
// group 的狀態機命令：KV 寫入 + config 變更 + shard 遷移，全部進同一條 Raft log 定序
type GroupCmd struct {
	Kind      string // "put" | "config" | "installShard"
	Put       KVCmd
	Config    Config            // Kind=="config"
	PrevCfg   Config
	Shard     int               // Kind=="installShard"
	ShardData map[string]int
}
```

**難點 2：shard 遷移本身要過共識。** 練習 D 的遷入方收到 `MigrateShard` 直接併入 store。但真 Raft group 不能讓 leader 私自併入——它得把「安裝這個 shard 的資料」也當成一筆 `installShard` 命令提進 log，等 commit 後所有 replica 一起安裝。**解法**：遷入方 leader 收到 `MigrateShard`，`Propose(GroupCmd{Kind:"installShard", Shard, ShardData})`；apply 時每個 replica 把資料併入自己的 store 並標 ready。這樣遷入的資料在 group 內也是複製的、容錯的——遷入方 leader 中途掛了，新 leader 從 log 恢復出「這個 shard 已安裝」。

**難點 3：ReadIndex 要按 shard 檢查 ownership。** M1 的 ReadIndex 只檢查「我是不是有效 leader」。M2 還要檢查「這個 key 的 shard 現在是不是我的、且 ready」——這是練習 D 的 `own[shard] && ready[shard]`，疊上 M1 的 ReadIndex 三關卡。少了 shard 檢查，一個剛把 shard 遷出去的 group 會服務一個它已經不該管的 key。

```go
// M2 的線性一致讀：ReadIndex 三關卡（M1）+ shard ownership 檢查（練習 D）
func (g *RaftGroup) LinearizableRead(key string, net *Net) (int, bool) {
	s := key2shard(key)
	if !g.own[s] || !g.ready[s] { return 0, false }      // 練習 D：shard 不是我的/未就緒 -> wrong group
	if !g.readIndexOK(net) { return 0, false }           // M1：ReadIndex 三關卡（leader+quorum+本任期）
	return g.store[key], true
}
```

### 為什麼 M2 標「理論預期」

M2 的完整多 group 整合，是練習 C（約 400 行 Raft）× 3 個 group + 練習 D（約 350 行分片）+ 上面三個整合處的機械化組裝，接近 1500 行。它沒有新的演算法難點——**難點全在上面那三處交界，而每一處的正確做法都已經給了骨架**。完整跑動的整合 demo 篇幅所限不在本文全貼，標「理論預期行為」。

**理論預期行為**：config 變更（join group3）進每個 group 的 Raft log 定序 → apply 時觸發 shard 遷移 → 遷入資料經 `installShard` 命令在遷入 group 內複製 → client 依 config 路由、遇 wrong group/not leader 重試 → 全程注入 nemesis 仍不丟已 commit 資料、不違反線性一致、無重複服務。

**驗證步驟**（你要跑通 M2 該這樣做）：

1. 從 M1 的 `KVNode` 出發，把 `store` 改成「只服務 `own && ready` 的 shard」（抄練習 D 的 `own/ready` 旗標）。
2. 把 config 變更、shard 安裝都改成走 `Propose` 進 Raft log，在 apply 時套用（難點 1、2）。
3. controller 先用單節點起步（練習 D 那樣），跑通 M2 的分片路由 + 遷移，驗練習 D 的三個要件（路由正確、遷移不丟、無重複）**在每個 group 是真 Raft 的情況下**仍成立。
4. 接上 M3 的 nemesis：對某個 group 注入 partition，驗「該 group 少數派側寫不進、多數派側繼續、heal 後收斂」，且遷移在故障下仍不丟資料（遷出方等 `MigrateAck`（其實是遷入方 `installShard` commit 的確認）才刪，抄練習 D 的「確認後才釋放」）。
5. 最後把 controller 也換成 Raft group（練習 D 延伸挑戰 3），得到完整的 Lab 4。

## 驗收標準（明列）

一條一條打勾。前四條 M1+M3 本文已真跑驗證，後三條是 M2 的目標（理論預期 + 驗證步驟已給）。

**M1 + M3（本文已驗）**
- [x] **線性一致寫**：`put` 走 Raft log、commit 後 apply，多數派持有才算成功。
- [x] **線性一致讀（ReadIndex）**：讀經過「是 leader + 能觸及多數 + 本任期已 commit」三關卡；不滿足就拒絕，不回過期值。
- [x] **注入故障不丟已 commit 資料**：nemesis 全程 partition/crash，任一 index 一旦觀測到 commit，內容永不變。（seed 3/5/7/11/42 全綠）
- [x] **不違反線性一致**：成功讀的值不倒退；naive 版會噴違反，ReadIndex 版全部消失。
- [x] **heal 後收斂**：分區/當機修復後，所有節點 store 收斂到一致。

**M2（目標，理論預期 + 驗證步驟已給）**
- [ ] **多 raft group 各管數個 shard**：key → shard → config → owner group 的 leader，路由正確。
- [ ] **config 變更觸發安全的 shard 遷移**：config 與遷移都過 Raft log 定序；遷移不丟資料、無重複服務（每個 key 只在當前 owner）。
- [ ] **動態 membership**：join/leave group 觸發 rebalance（盡量少搬）；遷移中請求回 wrong group、client 重試。

## 這個專案用到哪些章（整合對照表）

這個 final 整合了全課 70%+ 的概念。逐項對照——每一格都是「這個專案哪裡用到那一章」：

| 章 | 主題 | 在這個專案的哪裡 |
|---|---|---|
| [Ch 0](./00-environment-setup.md) | 確定性模擬器 | 整個系統跑在 `dsim` 上；seed 重現 stale read |
| [Ch 1-2](./01-why-distributed-is-hard.md) | 部分失敗、失敗模型 | ReadIndex 關卡 1 擋的「觸不到多數」就是部分失敗 |
| [Ch 3](./03-rpc-and-message-semantics.md) | at-least-once、冪等 | client 重試 + 遷移的 MigrateAck 確認後才釋放 |
| [Ch 4-6](./04-physical-clocks.md) | 時鐘、邏輯時鐘 | Raft term 是邏輯時鐘；不靠實體時鐘排序（nemesis 的 clock skew 打不倒它）|
| [Ch 8-9](./08-why-replicate.md) | 複製、一致性模型 | KV 狀態機複製；目標是線性一致 |
| [Ch 10-11](./10-cap-theorem.md) | CAP / PACELC | 分區時少數派側犧牲可用性（拒絕寫/讀）保一致性 = CP |
| [Ch 13](./13-quorum-replication.md) | Quorum | 多數派 commit、ReadIndex 的 quorum 確認 |
| [Ch 15-17](./15-consensus-problem.md) | 共識、FLP、繞過 | Raft 用 randomized timeout 繞過 FLP |
| [Ch 20-23](./20-raft-leader-election.md) | Raft 全套 | 練習 C 的 Raft 是複製引擎；safety 規則保不 split-brain |
| [Ch 25](./25-replicated-state-machine.md) | RSM | KV 狀態機掛在 Raft log 上——這個專案的骨架 |
| [Ch 26](./26-raft-kv-linearizable-reads.md) | 線性一致讀 | ReadIndex 兩道關卡（M1 核心）|
| [Ch 27-28](./27-sharding-partitioning.md) | 分片、一致性雜湊 | 多 group 分 shard、rebalance 盡量少搬（M2）|
| [Ch 29](./29-membership-failure-detection-swim.md) | 成員/失敗偵測 | 動態 membership、controller 偵測 group 加入（M2）|
| [Ch 43](./43-testing-distributed-systems.md) | 測試 | Jepsen nemesis 掃 seed + 線性一致檢查（M3）|
| [Ch 44](./44-observability-tracing.md) | 除錯 | seed 重現 + print 追出 stale read 的根因 |
| [Ch 45](./45-design-pitfalls.md) | 反模式 | 全程避坑：quorum 防腦裂、冪等重試、按資料選一致性 |

沒直接用到的主要是 Part 5（拜占庭）——這個系統假設 crash 故障模型，不防說謊節點。要防拜占庭得換成 PBFT（練習 E），那是另一條路。

## 參考架構與整合骨架

完整程式碼的組裝方式（你照這個拼，配上練習 C/D 的實作）：

**檔案佈局**（`go run .` 全放一個 package main）：

```
dsim.go        練習 C/D 用的模擬器（package 改 main）
raft.go        練習 C 的 Raft，原封不動
kv.go          M1：KVNode 疊在 Raft 上 + ReadIndex（本文已給）
shard.go       M2：Config/key2shard/controller/RaftGroup（升級練習 D，group 換真 Raft）
nemesis.go     M3：Ch 43 的 nemesis（本文已給）
main.go        driver：建叢集、跑 milestone、驗不變式
```

**M1 已完整可跑**（本文 `kv.go` + `nemesis.go` + `main.go` 的驗證迴圈，真跑 PASS）。**M2 的 `shard.go`** 是練習 D 的 `shardkv.go` 做三處改造（config/遷移走 Raft log、group 內用練習 C 的 Raft、ReadIndex 加 shard 檢查），骨架見上面「難點 1-3」。

**driver 的驗證迴圈**（M3，本文已跑的那個）：

```go
for now := 41; now <= 600; now++ {
	nm.tick(now)                          // Ch 43 nemesis 攪局
	if now%6 == 0 { leader().Put(key, nextVal, net); nextVal++ }  // client 寫
	net.Run(now)
	updateObservedCommitted(kvs, key)     // 更新 ground truth 下界
	if now%5 == 0 {                        // client 線性一致讀 + 檢查不倒退
		if v, ok := leader().LinearizableRead(key, net); ok {
			if v < snapshotBefore { flagViolation() }
		} else { staleRejected++ }
	}
	checkCommittedConsistency(kvs, lastCommitted, &lostData)  // 已 commit 不變
}
net.Heal(); runMore(); assertConverged(kvs)  // heal 後收斂
```

## 自我檢核

不看程式碼，主動回想——答不出來的就是還沒真懂：

- [ ] 為什麼 naive 的「role==leader 就回讀」會違反線性一致？舉出兩種會 stale read 的具體情境（提示：一種是舊 leader、一種是新 leader）。
- [ ] ReadIndex 的兩道關卡各擋掉哪一種 stale read？少了關卡 2（本任期 no-op 規則）會漏掉什麼？
- [ ] 把 config 變更「進 Raft log 定序」而非「leader 私自套用」——這解決了什麼具體災難？
- [ ] shard 遷移的資料為什麼也要過 Raft（`installShard` 命令），不能遷入方 leader 直接併入 store？
- [ ] M2 的線性一致讀要多檢查什麼（相對 M1）？少了它會怎樣？
- [ ] 我踩到的「全域 `lastCommitted` map 誤報」是哪一類 bug？對應 Ch 43 的哪條踩雷？這告訴我測試該先做什麼？
- [ ] 這個系統在 CAP 光譜上是 CP 還是 AP？分區時少數派側發生什麼？為什麼這是**正確**的行為而非缺陷？
- [ ] 為什麼 nemesis 的 clock skew 打不倒這個系統？（提示：它用什麼排序，不用什麼）

## 做完你站在哪

跑通 M1+M3、把 M2 照驗證步驟拼出來，你不再是「讀過分散式系統」，而是**造過一個容錯分片 KV store**——而且是經得起 Jepsen 風格對抗性測試的那種。

具體地說，你現在能：

- **讀懂 etcd / TiKV / CockroachDB 的核心設計**：它們就是「多 Raft group + 分片 + 線性一致讀」，你剛親手縫過這個骨架。
- **看穿一個分散式系統宣稱的一致性是真是假**：你知道 stale read 怎麼發生、ReadIndex 怎麼擋、怎麼用 nemesis 掃出違反。下次看到某資料庫宣稱「線性一致」，你會問「它的讀走 ReadIndex 還是 lease？分區時少數派 leader 怎麼處理？」
- **用確定性模擬 + nemesis 測任何分散式邏輯**：這套方法論（DST + Jepsen 風格故障注入 + 不變式檢查）是 FoundationDB/TigerBeetle 的工程核心，你已經在小規模上完整實踐過。
- **設計時避開所有已知的坑**：[Ch 45](./45-design-pitfalls.md) 那份 checklist 你不只讀過，還在這個專案裡逐條實踐過——quorum 防腦裂、ReadIndex 防 stale read、config 過共識防分岔、冪等重試、按資料選一致性。

這門課從 [Ch 0](./00-environment-setup.md) 的一個確定性模擬器出發，走過時鐘的謊言、CAP 的取捨、FLP 的不可能、Paxos/Raft 的共識、分片與交易、拜占庭容錯、真實系統、測試除錯與反模式——到這個把它們全部縫起來的 final。你手上有一個模擬器、一個 Raft、一個分片 KV，和一套能驗證它們對不對的方法論。

分散式系統的難，不在任何單一演算法，而在「一群會各自失敗、彼此看不到對方的機器要對一件事達成一致」這個根本困境。你現在有了直面它的工具和直覺。接下來，去讀真實系統的原始碼（etcd 的 raft 套件是最好的起點）、去跑真正的 Jepsen、去造你自己的分散式系統——並且，記得永遠先問那句話：**這真的需要分散式嗎？**
