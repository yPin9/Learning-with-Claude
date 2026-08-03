# Ch 25 — 複製狀態機（RSM）

> **目標**：把你在練習 C 手刻的 Raft，從「一堆 log replication 的機制」抽象成一個乾淨的通用引擎——**複製狀態機（Replicated State Machine, RSM）**。理解一句話：只要所有副本從相同初始狀態出發、以相同順序 apply 相同的確定性命令，它們就必然收斂到相同狀態。共識演算法（Raft/Paxos）在這張圖裡只做一件事：對命令 log 的順序達成一致。把這件事想清楚，你就能把任何服務（KV、鎖、佇列、資料庫）變成容錯的。

> **環境**：Go 1.18.1, WSL2 / Linux x86-64。純標準庫，跑在 Ch 0 的 `dsim` 上。

## 為什麼需要這個？

你剛刻完 Raft。它能選 leader、能複製 log、能扛 crash 和 partition。但你可能有個模糊的困惑：**這一大套機制，到底是為了達成什麼？**

答案不是「複製 log」。log 只是手段。真正的目標是：**做出一個對外看起來像單機、但底下有多個副本、任何一個掛掉服務都不中斷的服務**。

回想這件事在 RSM 出現前是怎麼做的。最原始的容錯做法叫**主從備份（primary-backup）**（Ch 12 已談過）：primary 處理請求，把「處理後的新狀態」整包送給 backup。這有兩個大問題：

1. **狀態可能很大**。你的服務有 100 GB 資料，客戶端改了一個 byte，你要送 100 GB 過去？實務上會做 diff，但 diff 本身又是一坨複雜度。
2. **primary 若在「算完新狀態、還沒送出」的瞬間當機**，backup 永遠不知道這次變更——狀態不一致。

Lamport 在 1978 年的 "Time, Clocks" 論文裡埋下了另一條路的種子，後來被歸納成 **state machine replication（狀態機複製）** 這個範式（Schneider 1990 的survey 是經典）：**不要複製狀態，複製「導致狀態改變的命令」。**

差別是什麼？primary-backup 複製的是「結果」；RSM 複製的是「輸入」。只要每個副本拿到相同的輸入序列、且狀態轉移是確定的，它們各自算出來的結果就必然一樣——你根本不用送結果過去，每台自己算。

這個轉換看似只是換個角度，威力卻巨大：命令通常遠小於狀態（「把 x 設成 5」比「整份資料的新快照」小太多），而且「命令的順序」這件事，剛好就是共識演算法能幫你搞定的。

> 若對 primary-backup 為什麼不夠不熟，回看 [Ch 12](./12-primary-backup-replication.md)。RSM 正是它的下一代。

## 先建立直覺

把 RSM 想成一排一模一樣的自動點唱機。每台點唱機初始都是靜音、曲目表相同。現在有一條**廣播線**，把「按鈕序列」同時餵給每一台：先按 3 號、再按 7 號、再按 3 號。只要每台點唱機是確定性的機器（按 3 號永遠放同一首歌），它們就會同步播出完全相同的音樂——你不用去同步「現在正在放哪首」，你只要同步「按鈕序列」。

```
        客戶端命令（輸入）
              │
              ▼
   ┌──────────────────────┐
   │   共識層（Raft/Paxos） │  ← 唯一的工作：替命令排出「全域一致的順序」
   │   對 log 的順序達成一致 │     = Ch 7 的 total order broadcast
   └──────────────────────┘
        │        │        │      同一份 log、同一個順序，送給每個副本
        ▼        ▼        ▼
   ┌────────┐ ┌────────┐ ┌────────┐
   │ SM 副本0│ │ SM 副本1│ │ SM 副本2│  ← 確定性狀態機：同輸入 → 同輸出
   │ apply   │ │ apply   │ │ apply   │
   │ 1,2,3.. │ │ 1,2,3.. │ │ 1,2,3.. │
   └────────┘ └────────┘ └────────┘
     state X    state X    state X       三個必然相等
```

這張圖把整個分散式容錯拆成兩個正交的層：

- **共識層**：只負責一件事——**把命令排成一個所有副本都同意的全域順序**。這就是 Ch 7 講的全序廣播（total order broadcast）。Raft 的 log、Paxos 的 instance 序列，本質都是在實作它。
- **狀態機層**：拿到有序命令，一條一條 apply。它**必須是確定性的**——同樣的輸入序列，每次、每台都得到同樣的輸出。

這兩層的分工乾淨到可以用一句話總結：**共識負責「順序」，狀態機負責「語意」**。你換掉狀態機（KV 換成 SQL 換成訊息佇列），共識層完全不用動；你換掉共識層（Raft 換成 Paxos），狀態機完全不用動。etcd、TiKV、CockroachDB 全部是這個結構。

## 確定性：RSM 的生死線

RSM 能成立，**完全**押在狀態機的確定性上。這不是「最好做到」，是「做不到就整個系統發散、丟資料」。

確定性的定義很硬：**給定相同的起始狀態與相同的命令序列，狀態轉移函式必須每次、每台產生位元組級相同的新狀態**。任何會讓兩台副本對同一條命令算出不同結果的東西，都是毒藥：

```
命令：apply "把 timestamp 記成現在時間"

  副本 0（apply 於 t=1000）→ 記下 1000
  副本 1（apply 於 t=1003）→ 記下 1003
                                  ↑
              兩台狀態不一樣了 —— RSM 徹底崩壞
```

這正是 Ch 0 反覆強調的那條確定性紀律，現在升級成了正確性的核心要件。具體的毒藥清單：

- **讀 wall-clock**（`time.Now()`）：每台 apply 的真實時間不同 → 發散。要用時間，必須把時間當成**命令的一部分**由 leader 決定後寫進 log，讓每台 apply 出相同的值。
- **亂數**（`rand.Intn`）：每台的隨機序列不同 → 發散。要隨機，把種子或結果寫進 log。
- **map 迭代順序**：Go 的 `range map` 是隨機的。如果你的狀態轉移邏輯依賴「先處理哪個 key」，兩台順序不同就可能發散（尤其涉及有副作用的迭代）。這跟 Ch 0 模擬器裡「tick 階段的 map 迭代」是同一個陷阱的兩個化身。
- **非確定的浮點/哈希**：跨平台浮點捨入、`unsafe` 指標值、goroutine 排程順序決定的結果——全都不行。
- **外部 I/O**：apply 時去打一個外部 API、讀一個檔案，回應內容每台可能不同 → 發散。副作用要嘛做成命令、要嘛只在 leader 做一次再把結果寫回 log。

一句話記住：**狀態機的 `Apply(command)` 必須是純函式**——它的輸出只能取決於「當前狀態」與「這條命令」，不能偷看外面的世界。

## 底層機制：命令怎麼從客戶端走到每個副本的狀態機

把練習 C 的 Raft 當引擎，掛一個 KV 狀態機上去。整條路徑：

```
1. 客戶端 → leader.Propose(Command{put x=1})
2. leader 把命令 append 進自己的 log（還沒 commit）
3. leader 透過 AppendEntries 複製給 followers
4. 多數派持有 → leader 推進 commitIndex（Ch 21 的 commit 規則）
5. 每個副本各自：commitIndex 前進 → apply()
        ┌──────────────────────────────────────┐
        │ for lastApplied < commitIndex:        │
        │     lastApplied++                     │
        │     cmd := log[lastApplied].Cmd        │
        │     stateMachine.Apply(cmd)  ← 關鍵！  │
        └──────────────────────────────────────┘
6. leader 把 apply 結果回覆客戶端
```

第 5 步是共識層與狀態機層的**唯一接縫**。共識層保證「所有副本的 log 在 commitIndex 之前逐格相同、順序相同」（這是 Raft 的 Log Matching Property，Ch 22 證過），狀態機層只要老實地照 log 順序 apply，收斂就是數學上的必然。

注意 apply 這一步的兩個紀律：

- **只 apply 已 commit 的**：`lastApplied` 永遠 `<= commitIndex`。沒 commit 的 log 可能之後被覆蓋（Ch 22 的 Figure 8 場景），apply 了就收不回來。
- **嚴格按 index 遞增 apply**：不能跳號、不能亂序。`lastApplied` 是單調遞增的游標。

我們在練習 C 的 `apply()` 已經寫好這個游標，現在只是把「塞進 `applied` 切片」升級成「餵給真的狀態機」。

## 動手：KV 狀態機跑在 Raft log 上

拿練習 C 的 Raft，只改一處——`apply()` 裡多呼叫一個狀態機。先定義狀態機：

```go
// kv.go — 一個確定性 KV 狀態機
package main

// Command 是狀態機的一筆確定性轉移。Op 只有 put/del。
type Command struct {
	Op    string // "put" | "del"
	Key   string
	Value string
}

// KVStore 是 key-value 狀態機。它是「確定性」的：
// 給相同初始狀態、apply 相同順序的 Command，就得到相同狀態。
type KVStore struct {
	data map[string]string
}

func NewKVStore() *KVStore { return &KVStore{data: map[string]string{}} }

func (kv *KVStore) Apply(c Command) {
	switch c.Op {
	case "put":
		kv.data[c.Key] = c.Value
	case "del":
		delete(kv.data, c.Key)
	}
}

func (kv *KVStore) Get(key string) (string, bool) {
	v, ok := kv.data[key]
	return v, ok
}
```

`Apply` 是純函式：輸出只取決於 `data` 與 `c`。沒有 wall-clock、沒有亂數、沒有 I/O。這是刻意的。

比對狀態時有個坑：如果你直接 `range kv.data` 拼字串，Go map 的迭代順序是隨機的，兩台**內容相同**的副本會印出**不同字串**——那不是狀態發散，是你的比對函式自己引入了不確定。所以 fingerprint 一定要先排序：

```go
// Fingerprint 把整個狀態壓成一個確定的字串：key 先排序再串接。
func (kv *KVStore) Fingerprint() string {
	keys := make([]string, 0, len(kv.data))
	for k := range kv.data {
		keys = append(keys, k)
	}
	sort.Strings(keys) // 沒有這行，兩個相同狀態會印出不同結果
	var b strings.Builder
	for _, k := range keys {
		b.WriteString(k); b.WriteString("="); b.WriteString(kv.data[k]); b.WriteString(";")
	}
	return b.String()
}
```

Raft 那邊只改 `apply()` 一處，把命令餵進狀態機：

```go
// raft.go 的 apply()（練習 C 版本 + 一行狀態機呼叫）
func (r *Raft) apply() {
	for r.lastApplied < r.commitIndex {
		r.lastApplied++
		cmd := r.log[r.lastApplied].Cmd
		r.applied = append(r.applied, cmd)
		if c, ok := cmd.(Command); ok {
			r.sm.Apply(c) // ← 確定性狀態機轉移，這是 RSM 的接縫
		}
	}
}
```

`r.sm` 是每個 Raft 節點自己的 `*KVStore`，在 `NewRaft` 裡 `NewKVStore()` 建好。主程式：選出 leader，丟一串 KV 命令，讓它們走 log commit，最後印每個副本的 fingerprint：

```go
// main.go（節錄）
cmds := []Command{
	{Op: "put", Key: "x", Value: "1"},
	{Op: "put", Key: "y", Value: "2"},
	{Op: "put", Key: "z", Value: "3"},
	{Op: "del", Key: "x"},       // x 被刪
	{Op: "put", Key: "y", Value: "9"}, // y 被覆蓋
}
for _, c := range cmds {
	ld.Propose(c, net)
	net.Run(net.Now() + 15)
}
net.Run(net.Now() + 40) // 讓 commit/apply 傳播到每個副本

first := rafts[0].sm.Fingerprint()
for _, r := range rafts {
	fp := r.sm.Fingerprint()
	fmt.Printf("  node %d  applied=%d  state=%q\n", r.id, len(r.applied), fp)
	// 比對 fp == first
}
```

真跑（WSL, Go 1.18.1，`go run .`，seed=3、5 節點、latency 1-2）：

```
leader=0 term=1

各副本狀態機 fingerprint（key 排序後串接）：
  node 0  applied=5  state="y=9;z=3;"
  node 1  applied=5  state="y=9;z=3;"
  node 2  applied=5  state="y=9;z=3;"
  node 3  applied=5  state="y=9;z=3;"
  node 4  applied=5  state="y=9;z=3;"

所有副本狀態一致 — RSM 收斂成功

從 node 2 讀 y = "9" (exists=true)
從 node 4 讀 x 是否存在 = false (已被 del)
```

五個副本 apply 了同樣的 5 條命令，最終狀態全部是 `y=9;z=3;`——`x` 被 del 掉、`y` 被 `set y=9` 覆蓋。**沒有任何一台去複製「狀態」，每台各自從相同 log 算出相同狀態**。這就是 RSM：共識層排好順序，狀態機層各自 apply，收斂是必然。

值得注意：`applied=5` 對所有節點都成立，代表連 follower 都 apply 了全部命令——不是只有 leader 有完整狀態。這正是容錯的來源：leader 掛了，任何一個 follower 都握有完整狀態能接手。

## 動手：把確定性弄壞，親眼看副本發散

上面的收斂是「狀態機確定性」撐出來的。抽掉這個前提會怎樣？我們寫一個最小的對照實驗——**兩個副本 apply 完全相同的命令序列**，一個狀態機確定、一個偷讀了「每個副本各自不同」的本地計數器（模擬 wall-clock 這類非確定來源）：

```go
type badSM struct {
	data      map[string]int
	localTick int // 每副本各自遞增，模擬讀 wall-clock 這種非確定來源
}

func (s *badSM) apply(c cmd) {
	s.localTick++                       // 兩副本 apply 同一條命令時，localTick 可能不同步
	s.data[c.key] = c.val + s.localTick // 把非確定值摻進狀態 —— 毒藥
}
```

兩個副本從相同初始資料出發、apply 同樣的 `{x:10, y:20, z:30}`，唯一差別是它們的 `localTick` 起點不同步（真實系統裡「兩台的本地時鐘/計數器完全同步」是不可能保證的）。真跑（WSL, Go 1.18.1）：

```
[確定性狀態機]
  副本 A state = "x=10;y=20;z=30;"
  副本 B state = "x=10;y=20;z=30;"
  一致？ true

[非確定狀態機：apply 時偷讀本地計數器]
  副本 A state = "x=11;y=22;z=33;"
  副本 B state = "x=13;y=24;z=35;"
  一致？ false  ← 相同命令序列，狀態卻發散
```

同樣的命令、同樣的順序，確定性狀態機收斂，非確定的當場發散。這是 RSM 最重要的一課的實證：**共識層把 log 排得再完美，只要狀態機不確定，副本照樣分家。** 而且這種 bug 極陰險——它不會立刻爆炸，而是讓副本悄悄長出不同的狀態，直到某次 leader 切換或讀取才暴露「兩台答案不一樣」。這也是為什麼真實系統（如 CockroachDB）在測試時會定期對所有副本的狀態做 checksum 比對，一旦不一致就當致命錯誤崩潰——寧可停機也不能帶著發散的狀態繼續服務。

## 線性化點與命令去重

RSM 給我們的不只是收斂，還給了一致性模型裡最強的那個：**線性一致性（linearizability）**（Ch 9 已定義）。

**線性化點（linearization point）** 在哪？就是命令**被 commit（多數派持有）並被 leader apply 的那一刻**。一旦跨過這個點，這條命令的效果對後續所有讀都可見、且不可逆。從客戶端視角，整個操作彷彿在「送出到收到回覆之間的某一個瞬間」原子地發生——那個瞬間就是 commit-apply 點。這讓一個分散式的 KV 對外看起來就像一個單執行緒的 `map`。

但這裡藏著一個練習 C 沒處理、真實系統必踩的問題：**命令重複**。

考慮這個時序：客戶端送 `put x=1` → leader commit 了 → 回覆還在路上時網路抖了一下，客戶端**逾時重送** → 這條命令**又被 append 進 log 一次**。對 `put x=1` 這種冪等操作沒差，但如果命令是 `x += 1`（把 x 加一），重複 apply 就多加了一次——**線性一致性被破壞**。

> 若對「重送為什麼無法避免、exactly-once 是迷思」不熟，回看 [Ch 3](./03-rpc-and-message-semantics.md)。網路層做不到只送一次，去重必須在應用層做。

解法是**命令去重**：每個客戶端帶一個 `(clientID, seq)`，`seq` 對每個客戶端單調遞增。狀態機維護一張表記錄「每個 clientID 已經 apply 到哪個 seq、結果是什麼」：

```
狀態機收到命令 (clientID=7, seq=42, op=...)：
  if lastSeq[7] >= 42:        // 這條或更新的已經 apply 過
      回傳 cachedResult[7]     // 直接回快取結果，不再 apply
  else:
      apply(op)
      lastSeq[7] = 42
      cachedResult[7] = result
```

這把「網路層做不到的 exactly-once」在**狀態機層**用 at-most-once 的去重補回來——這正是 Ch 3 講的「冪等性（idempotency）是分散式容錯的地基」在 RSM 裡的落地。去重表本身也是狀態機狀態的一部分，跟著 log 複製、跟著 snapshot 一起存，所以每個副本的去重判斷也是確定的、一致的。

## 對比與取捨

| 面向 | Primary-Backup（Ch 12） | 複製狀態機（RSM） |
|---|---|---|
| 複製什麼 | 狀態（結果） | 命令（輸入） |
| 傳輸量 | 大（整份狀態或 diff） | 小（命令通常很小） |
| 對狀態機的要求 | 無（可非確定） | **必須確定性** |
| 一致性強度 | 依實作，常較弱 | 可到線性一致 |
| 容錯上限 | 通常 1 個 backup | 2f+1 台容忍 f 台當機 |
| 誰在用 | 早期資料庫、簡單 HA | etcd, TiKV, CockroachDB, Spanner |

RSM 的代價很明確：**你的狀態機必須是確定性的**，這對某些工作負載是硬約束（例如要用當前時間、要呼叫外部服務）。但只要你能把非確定性擠進命令裡（讓 leader 決定好再寫進 log），RSM 給你的容錯與一致性強度是 primary-backup 給不了的。

## 踩雷集錦

1. **「共識演算法本身就保證狀態一致」→ 錯。** 共識只保證 **log 順序一致**。狀態一致是「log 一致」加上「狀態機確定性」兩者的**乘積**。你的 Raft 完美無瑕，但狀態機裡偷讀了一次 `time.Now()`，副本照樣發散。共識層管不到你狀態機裡幹了什麼——那是你的責任。

2. **「apply 沒 commit 的 log 應該沒關係，反正之後會 commit」→ 大錯。** 沒 commit 的 log 可能之後被新 leader 覆蓋（Ch 22 Figure 8）。你一旦 apply 了，狀態機的副作用（改了值、回了客戶端）就收不回來——這是「已回報卻消失」類 bug 的溫床。`lastApplied` 必須嚴格 `<= commitIndex`，一格都不能超前。

3. **「把 `time.Now()` 寫進命令值不就好了」——方向對，但要 leader 寫、不是每台 apply 時寫。** 正確做法：leader 在 `Propose` 時就把時間戳固定進命令內容，讓這個值成為 log 的一部分，每台 apply 時讀的是同一個寫死的值。錯誤做法：命令裡寫 `op=record_current_time`，每台 apply 時各自呼叫 `time.Now()`——那還是發散。**非確定的東西要在進 log 之前就被釘死成確定的值。**

4. **「去重表不用複製吧，反正只是防重送」→ 錯。** 去重表是狀態機狀態。如果只有 leader 有、follower 沒有，leader 掛了換新 leader，新 leader 不知道「client 7 的 seq 42 已經處理過」，客戶端重送時它會再 apply 一次——去重失效。去重表必須跟資料一起走 log、一起 snapshot。

5. **「fingerprint 直接 range map 拼起來比對就好」→ 你會誤判發散。** Go map 迭代隨機。兩台狀態完全相同、迭代順序不同，拼出的字串就不同，你會以為系統壞了。比對狀態務必先排序 key（或用其他順序無關的方式，如對排序後內容取 hash）。這個坑跟狀態機本身該不該用 map 迭代是兩回事，但都源自「map 順序不確定」。

## 進階：再往深一層

- **snapshot 與 log compaction**：log 無限長會爆記憶體，也讓落後太多的 follower 要補很久。解法是狀態機到某個 index 打一份快照、截斷之前的 log（Ch 23）。快照必須包含**全部**狀態機狀態——包括去重表。leader 對落後到 log 已被截斷的 follower，直接送 snapshot 而非逐筆 log。

- **狀態機不一定是 KV**。同一個 RSM 引擎，換個狀態機就是不同的服務：狀態機是「一把鎖」→ 你得到分散式鎖（ZooKeeper 的核心）；是「一個佇列」→ 分散式訊息佇列；是「一棵樹」→ ZooKeeper 的 znode 樹；是「一個 SQL 執行引擎」→ 複製資料庫（CockroachDB 每個 range 是一個 RSM）。**共識層完全不用改**，這就是分層的威力。

- **多個 RSM（multi-raft）**：單一 Raft group 的吞吐有上限（所有寫都過一個 leader）。真實系統把資料切成很多片（shard），每片一個獨立的 Raft group、各自是一個 RSM。這就是 Ch 27 的分片。

- **讀的線性一致沒那麼簡單**：你可能以為「讀 leader 本地狀態機就好」，畢竟 leader 有最新狀態。但 stale leader 問題會讓你讀到舊值——這是 Ch 26 整章要處理的坑，也是 RSM 從「能收斂」到「讀寫都線性一致」的最後一哩路。

## 本章重點整理

- RSM 的核心公式：**相同初始狀態 + 相同順序的確定性命令 = 相同最終狀態**。
- 分工正交：**共識層排順序（= 全序廣播）**，**狀態機層做語意（必須確定性）**。換掉任一層另一層不用動。
- 複製「命令（輸入）」而非「狀態（結果）」——命令小、且順序剛好是共識能解的問題。
- **確定性是生死線**：wall-clock、亂數、map 迭代、外部 I/O 全是毒藥。非確定的值要在進 log 前由 leader 釘死。
- 線性化點 = 命令 commit-apply 的那一刻。命令去重（clientID + seq）把 Ch 3 的 exactly-once 迷思在狀態機層補成 at-most-once。
- apply 紀律：只 apply 已 commit 的、嚴格按 index 遞增、`lastApplied <= commitIndex`。

## 自我檢核

- [ ] 不看筆記，我能解釋為什麼「複製命令」比「複製狀態」更好，各自的代價是什麼
- [ ] 我能說出共識層與狀態機層各自負責什麼，以及為什麼這兩層是正交的
- [ ] 我能舉出至少三種會讓狀態機發散的非確定性來源，並說出各自的修法
- [ ] 我知道線性化點在 RSM 裡具體是哪一刻，以及為什麼在那一刻之後效果不可逆
- [ ] 我能解釋命令去重為什麼需要 `(clientID, seq)`，以及為什麼去重表必須跟著 log 複製
- [ ] 我能說出為什麼 `lastApplied` 不能超過 `commitIndex`，超過會出什麼災難

## 延伸閱讀

- **[Implementing Fault-Tolerant Services Using the State Machine Approach: A Tutorial](https://www.cs.cornell.edu/fbs/publications/smsurvey.pdf)** — Fred Schneider, ACM Computing Surveys（1990）
  - **這篇說什麼**：state machine replication 的奠基 survey，把本章的直覺講成嚴謹的框架。RSM 這個名詞與範式的權威來源
  - **讀哪裡**：第 2–3 節（狀態機的定義、確定性要求）與第 4 節（容錯與共識的關係）最核心
  - **前提**：讀得懂本章即可，數學不重

- **《Designing Data-Intensive Applications》第 9 章「Consistency and Consensus」** — Martin Kleppmann（O'Reilly, 2017）
  - **讀哪裡**："Total Order Broadcast" 與 "State Machine Replication" 兩小節，把 Ch 7 的全序廣播與本章 RSM 的等價關係講得最清楚
  - **為什麼值得讀**：它明確點出「全序廣播 ⟺ 共識」的等價性，這是理解「為什麼共識能拿來做 RSM」的關鍵

- **[Raft 論文](https://raft.github.io/raft.pdf) 第 6–7 節（Client interaction / Log compaction）** — Ongaro & Ousterhout（2014）
  - **讀哪裡**：第 8 節「Client interaction」講命令去重（linearizable semantics via unique serial numbers），第 7 節講 snapshot——本章進階提到的兩件事論文都有正式規格
  - **前提**：你已經刻過練習 C 的 Raft，讀這兩節會非常有感

- **[TiKV 的 Raft 與狀態機分層](https://tikv.org/deep-dive/consensus-algorithm/raft/)** — TiKV 官方 deep dive
  - **這是什麼**：真實生產系統怎麼把 raft-rs（共識層）與狀態機（RocksDB 之上的 KV）分層，本章分層設計的工業版
  - **讀哪裡**：Raft 章節看它怎麼把「apply」這一步接到底層儲存

把狀態機掛上共識層，我們就有了一個會收斂的複製 KV。但別急著慶祝——「讀」這件事還藏著一個能讓你讀到過期資料的陷阱，而且它不是 bug，是你天真地「讀 leader 本地狀態」必然會踩的坑。

→ [Ch 26 用 Raft 建 KV Store：線性一致讀](./26-raft-kv-linearizable-reads.md)
