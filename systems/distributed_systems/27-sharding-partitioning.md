# Ch 27 — 分片（Sharding/Partitioning）

> **目標**：搞懂為什麼「加副本」救不了容量與吞吐、只有「切資料」能救，也就是分片（sharding，又稱 partitioning）。看清楚分片與複製是**正交**的兩件事、常被混為一談卻各解不同問題。掌握兩種分片策略（range vs hash）的取捨、multi-raft（每個 shard 一個共識 group）的架構、跨分片查詢與交易為什麼變難、以及熱點（hotspot）與再平衡（rebalancing）這兩個實務上真正會咬人的問題。最後對照 Spanner/CockroachDB/TiKV 是怎麼落地的。

> **環境**：本章是架構觀念章，重 ASCII 圖與真實系統對照，程式碼在下一章（一致性雜湊）與練習 D（分片 KV）動手。

## 為什麼需要這個？

你手上有一個線性一致的複製 KV（Ch 25–26）。它容錯、讀寫都對。現在來個現實問題：**資料有 10 TB，每秒 50 萬次寫入。**

先試試你已經會的招——加副本。把 3 個副本加到 7 個、11 個。有用嗎？

**完全沒用，甚至更糟。** 因為：

1. **容量沒變**。RSM 的每個副本都存**全量**資料（Ch 25：每台各自 apply 全部命令、各自持有完整狀態）。3 台每台 10 TB，加到 11 台還是每台 10 TB。副本是「同一份資料的多個拷貝」，不是「資料的不同部分」。你買了 11 台機器，存的還是那 10 TB。

2. **寫吞吐沒變，反而更慢**。所有寫都得過**同一個 leader**、複製到多數派才 commit。加副本讓「多數派」的門檻更高（11 台要 6 台確認 vs 3 台要 2 台），寫反而更慢。單一 Raft group 的寫吞吐有天花板，那天花板由 leader 的處理能力與複製延遲決定，加副本只會壓低它。

這就是複製的根本侷限：**它解決「可用性」與「讀吞吐」，但對「容量」和「寫吞吐」無能為力。** 一台機器裝不下的資料、一個 leader 扛不住的寫入，複製多少份都一樣裝不下、扛不住。

歷史上人們很早就撞到這面牆。早期的解法很土——**手動分庫分表**：DBA 用「user_id 尾號 0–4 進 DB1、5–9 進 DB2」這種規則，人肉把資料切到不同機器。應用層自己記得「這筆該去哪台」。這能用，但每次加機器都要人肉重新切、重寫路由規則、搬資料，痛苦不堪（下一章會看到「土法 hash mod N」加機器時的災難）。

分片就是把這件事系統化：**把整個資料集切成很多不重疊的片（shard / partition），每片放不同機器，讓容量和吞吐隨機器數量線性擴展。**

> 若對「複製解決什麼」不熟，回看 [Ch 8](./08-why-replicate.md)。一句話對照：複製是「同一份資料的多個拷貝」，分片是「資料切成不同部分放不同機器」。兩者解的問題不同。

## 先建立直覺：分片與複製正交

這是本章最重要的一張圖，也是最多人搞混的地方。**分片和複製是兩個獨立的維度**，真實系統兩個一起用。

```
                     複製（縱向：同一片的多個拷貝，為了容錯）
                     ─────────────────────────────────────►
   分片              副本0        副本1        副本2
  （橫向：       ┌──────────┐ ┌──────────┐ ┌──────────┐
   切成不同片    │ shard A  │ │ shard A  │ │ shard A  │   ← shard A 的 raft group
   為了擴容）    │ (leader) │ │(follower)│ │(follower)│
    │           └──────────┘ └──────────┘ └──────────┘
    │           ┌──────────┐ ┌──────────┐ ┌──────────┐
    │           │ shard B  │ │ shard B  │ │ shard B  │   ← shard B 的 raft group
    ▼           │(follower)│ │ (leader) │ │(follower)│
                └──────────┘ └──────────┘ └──────────┘
                ┌──────────┐ ┌──────────┐ ┌──────────┐
                │ shard C  │ │ shard C  │ │ shard C  │   ← shard C 的 raft group
                │(follower)│ │(follower)│ │ (leader) │
                └──────────┘ └──────────┘ └──────────┘
```

把這張表讀懂，你就懂了分片系統的骨架：

| 維度 | 沿哪個方向 | 解決什麼 | 一片壞了會怎樣 |
|---|---|---|---|
| **分片（sharding）** | 橫向：不同 shard | 容量、寫吞吐 | 那片的資料不可用（但其他片沒事） |
| **複製（replication）** | 縱向：同 shard 的副本 | 可用性、讀吞吐 | 那份拷貝沒了，其他拷貝頂上 |

關鍵洞察：

- **每個 shard 自己是一個完整的 RSM**（一個獨立的 Raft group，有自己的 leader、log、狀態機）。shard A 的 leader 可以在機器 0，shard B 的 leader 在機器 1——這樣寫入壓力被打散到不同機器的 leader 上，寫吞吐才真的擴展了。這叫 **multi-raft**。
- **不同 shard 的 leader 分散在不同機器**，這是讓寫吞吐擴展的關鍵。如果所有 shard 的 leader 都擠在同一台，那台又變瓶頸了——所以要**均衡 leader 分布**（leader balancing）。
- **一台實體機器同時是多個 shard 的成員**（可能是 A 的 leader、B 的 follower、C 的 follower）。機器數量、shard 數量、副本數是三個獨立的旋鈕。

搞混這兩者的典型症狀：「我加了副本怎麼還是慢/裝不下」（該分片卻在複製）、或「我分了片怎麼一台掛了那片就沒了」（分片了卻沒複製）。**兩個都要，且各解各的。**

## 分片策略：range vs hash

資料切成片，第一個問題是**按什麼切**。兩大流派。

### Range 分片（按範圍）

按 key 的順序切成連續區間：`[a, f)` 一片、`[f, m)` 一片、`[m, z]` 一片。

```
key 空間（有序）:  a ── c ── f ── j ── m ── r ── z
shard:            └── shard 0 ──┘└─ shard 1 ─┘└─ shard 2 ─┘
                     [a, f)         [f, m)       [m, z]
```

- **優點：範圍查詢（range scan）高效**。「給我所有 `user_1000` 到 `user_2000` 的資料」只會落在一兩個相鄰 shard，掃描連續。有序 key（時間戳、遞增 ID、字典序）的區間查詢是 range 分片的殺手級場景。
- **缺點：容易熱點**。如果 key 是遞增的（時間戳、auto-increment ID），**所有新寫入都落在最後一個 shard**——那個 shard 的 leader 被寫爆，其他 shard 閒著。這是 range 分片最經典的坑（下面「熱點」細講）。

### Hash 分片（按雜湊）

對 key 取雜湊，按雜湊值切：`hash(key) % N` 或（更好）一致性雜湊環（下一章）。

```
key ──hash──► 雜湊值空間（打散、無序）──► 均勻散到各 shard
"user_1"  ─► 0x8f3a... ─► shard 2
"user_2"  ─► 0x1c07... ─► shard 0
"user_3"  ─► 0xa9e1... ─► shard 1   ← 相鄰的 key 被打散到不同 shard
```

- **優點：天然均衡**。好的雜湊把 key 均勻打散，寫入分布均勻，不容易熱點（除非單一 key 本身超熱，見下）。
- **缺點：範圍查詢爆炸**。相鄰的 key 被雜湊打散到所有 shard，「`user_1000` 到 `user_2000`」變成要問**每一個** shard——range scan 從「掃一兩片」變成「散射到全部片再合併」，慢且貴。

### 怎麼選

| 面向 | Range 分片 | Hash 分片 |
|---|---|---|
| 範圍查詢 | 快（連續掃描） | 慢（散射到所有 shard） |
| 負載均衡 | 差（遞增 key 熱點） | 好（天然打散） |
| 動態再平衡 | 好（可切分/合併區間） | 較難（改雜湊要搬很多 key，除非一致性雜湊） |
| 典型系統 | Spanner, CockroachDB, HBase, TiKV | Cassandra, DynamoDB, Riak |

真實系統的選擇很能說明問題：**要 SQL、要 range scan、要交易**的系統（Spanner、CockroachDB、TiKV）幾乎都選 **range 分片**，因為 SQL 的 `WHERE id BETWEEN ...`、`ORDER BY`、索引掃描全靠有序性；它們用其他手段（自動切分、load-based rebalancing）對抗熱點。**純 KV、要極致均衡**的系統（Cassandra、DynamoDB）選 **hash 分片**，用一致性雜湊環讓加減機器時搬遷最小化——這正是下一章的主角。

## 底層機制：路由——請求怎麼找到對的 shard

分了片，客戶端怎麼知道 `user_42` 該去哪個 shard、那個 shard 的 leader 在哪台機器？這需要一層**路由（routing）**，也叫 shard/placement 元資料。三種做法：

```
做法 1：客戶端直連（client-side routing）
  客戶端持有分片表 → 自己算出 shard → 直接連對應 leader
  快，但客戶端要跟得上分片表變化（rebalance 後表會變）

做法 2：中間層代理（routing tier / proxy）
  客戶端 → proxy（持有分片表）→ 轉給對的 shard leader
  客戶端無腦，但多一跳；proxy 可能成瓶頸

做法 3：任意節點轉發（request routing at any node）
  客戶端連任一節點 → 若不是負責這片的，回「你該找 X」或幫轉
  節點要互相知道分片表（靠 gossip 或中央元資料服務）
```

不管哪種，都需要一個**權威的分片元資料**：哪個 key range/hash 範圍屬於哪個 shard、每個 shard 的成員與當前 leader 是誰。這份元資料本身是關鍵狀態，**它自己也得容錯**——所以通常**它自己也是一個 RSM**！

- Spanner 用 **placement driver** 管 tablet 的分布。
- TiKV 用 **PD（Placement Driver）**，PD 自己是一個小 Raft group。
- CockroachDB 把 range 元資料（meta ranges）存成系統自己的 range，一樣用 Raft 複製。

這是個漂亮的遞迴：**用共識管理「誰負責哪片」，每片又各自用共識複製。** 元資料層（少量、關鍵）與資料層（大量、分片）分開，各自是 RSM。

## 跨分片查詢與交易：分片的代價

分片不是免費的。它把一個原本在單機（單一 RSM）內原子完成的事，變成跨越多個獨立 RSM 的協調問題。

**跨分片查詢**：`SELECT * WHERE age > 30`，資料散在所有 shard → 要**散射-聚合（scatter-gather）**：問每個 shard、各自回部分結果、再合併。慢、且任一 shard 慢就拖累整體（尾延遲放大）。這是 hash 分片下 range 查詢的常態，也是為什麼要 range 分片的動機之一。

**跨分片交易**才是真正的痛。單一 shard 內的寫，靠那個 shard 的 Raft 就能原子 commit（Ch 25）。但「從 shard A 的帳戶轉錢到 shard B 的帳戶」——這兩個寫在**兩個獨立的 RSM**裡，各自的 Raft 管不到對方。要讓「A 扣款」和「B 加款」**同時成功或同時失敗**（原子性），你需要一個跨 RSM 的原子提交協定：

```
跨分片交易「A 轉 100 給 B」：
  shard A（一個 raft group）：扣款 -100
  shard B（另一個 raft group）：加款 +100
        ↑ 兩個獨立的共識 group，各自 commit 自己那半
        問題：怎麼讓兩半「要嘛都成、要嘛都敗」？

  → 需要 two-phase commit (2PC) 之類的原子提交協定，
    協調者先問兩邊「準備好了嗎」（prepare），
    都說好才叫兩邊「提交」（commit）。
```

這就是 Ch 30（2PC/3PC）與 Ch 31（Saga/Percolator）要處理的——**分片把「跨片原子性」這個大問題逼了出來**。Spanner 的做法是「每個 shard 內用 Paxos，跨 shard 用 2PC，再加 TrueTime 給全域一致的時間戳」（Ch 39）。這裡先埋下：跨分片交易 = 分片系統最貴的操作，能避則避（好的分片設計會讓相關資料落在同一片，減少跨片交易）。

> 跨分片交易的完整協定在 [Ch 30](./30-distributed-transactions-2pc-3pc.md)。本章只要記住：分片讓單片交易變便宜、跨片交易變昂貴且需要額外協定。

## 二級索引：分片系統最容易被忽略的坑

上面談的都是「按主鍵（primary key）分片」。但真實查詢常常不是按主鍵找，而是按別的欄位——「找所有 `color=red` 的商品」。這需要**二級索引（secondary index）**，而二級索引在分片系統裡有兩種做法，各有致命取捨。

**做法一：本地索引（local / document-partitioned index）**。每個 shard 只為「自己這片的資料」建索引。寫入很爽——資料寫哪片，索引就更新哪片，一次寫只碰一個 shard。但**讀爆炸**：`color=red` 的商品可能散在所有 shard，查詢得問**每一個** shard「你這片有沒有 red」再合併——又是散射-聚合。這叫 scatter-gather read，尾延遲被最慢的 shard 拖累。

```
本地索引：索引跟著資料分片
  shard 0: 資料 {A:red, B:blue}   索引 {red:[A], blue:[B]}
  shard 1: 資料 {C:red, D:green}  索引 {red:[C], green:[D]}
  查 color=red → 問 shard 0（得 A）+ 問 shard 1（得 C）→ 合併 [A,C]
                  ↑ 每次二級查詢都要問所有 shard
```

**做法二：全域索引（global / term-partitioned index）**。索引本身**獨立分片**，按索引鍵（term）分片而非按資料主鍵。`color=red` 這個 term 只落在一個 shard 上，查詢只問那一片——**讀很快**。代價是**寫爆炸**：寫一筆「商品 X，color=red，size=L」要更新「red 那片的索引」和「L 那片的索引」，可能碰多個 shard，且這個更新**跨 shard**——要嘛非同步（索引短暫落後、最終一致），要嘛用跨片交易（貴）。

```
全域索引：索引獨立按 term 分片
  index shard 0（存 a-m 的 term）: {blue:[B], green:[D]}
  index shard 1（存 n-z 的 term）: {red:[A,C]}
  查 color=red → 只問 index shard 1 → 直接得 [A,C]  ← 讀快
  但寫入 X:red 要更新 index shard 1，跨 shard 寫  ← 寫慢/非同步
```

| 面向 | 本地索引（document-partitioned） | 全域索引（term-partitioned） |
|---|---|---|
| 寫 | 快（只碰資料所在 shard） | 慢（跨 shard 更新索引） |
| 二級查詢讀 | 慢（散射到所有 shard） | 快（只問索引所在 shard） |
| 索引一致性 | 天然一致（同 shard 內原子） | 常做成非同步、最終一致 |
| 典型 | Elasticsearch, MongoDB 預設, Cassandra 本地索引 | DynamoDB GSI, 部分搜尋系統 |

這個取捨沒有標準答案，取決於你是讀多還是寫多。重點是：**分片一旦引入，二級索引就不再是「加個索引」那麼簡單，而是一個「讀寫在哪爆炸」的架構決策**。很多人分片時只想主鍵，上線後才發現二級查詢慢到不能用——因為預設的本地索引把讀變成了散射-聚合。

## 熱點與再平衡：實務的兩座大山

分片理論很乾淨，實務上兩個問題會反覆咬你。

### 熱點（hotspot / hot shard）

理想上負載均勻散到所有 shard，實際上常常不是。熱點的來源：

1. **遞增 key（range 分片）**：時間戳、auto-increment ID 當 key，所有新寫落在最後一片。解法：key 前綴加雜湊/反轉（把 `1000, 1001, 1002` 變成雜湊前綴打散），或直接用 hash 分片——但這犧牲了 range scan。
2. **單一超熱 key**：某個名人的帳號、某個爆紅商品的庫存，這個 key 本身流量巨大，而它只能待在一個 shard 上——**再怎麼分片都救不了單一 key 的熱**。解法要往應用層走：對這個 key 做快取、讀寫分離、或把它拆成多個子 key（如庫存分桶）。
3. **傾斜的 key 分布**：某個範圍的 key 特別多/特別熱。range 分片可以把那個熱區間**再切細**（一片變兩片），hash 分片較難針對性處理。

熱點的殘酷之處：**它讓「分片擴展」的承諾打折**。你有 100 台機器，但 90% 流量打在 3 個 hot shard 上，那 3 台被打爆、其他 97 台閒著——擴展性有名無實。真實系統花大量工程在偵測熱點、動態切分/搬遷 hot shard。

### 再平衡（rebalancing）

叢集不是靜態的：加機器（擴容）、減機器（縮容/故障）、shard 變大要切分、負載不均要搬遷。**再平衡就是把 shard 在機器間重新分配**，而且要在**服務不中斷、搬遷量最小**的前提下做。

再平衡的鐵律：

- **搬遷量要最小**。加一台機器，理想上只需把「總資料的 1/N」搬到新機器，**不該動到其他 shard**。土法 hash mod N 在這裡會災難性地失敗（幾乎全部重映射）——這正是下一章一致性雜湊要解決的核心問題。
- **搬遷中服務不能停**。搬一個 shard 時，讀寫要能繼續（或短暫轉到舊位置），搬完再原子切換路由。
- **別搬過頭**。自動再平衡若太激進，會在負載波動時反覆搬遷（thrashing），本身變成負載。真實系統的 rebalancer（TiKV 的 PD、CockroachDB 的 allocator）都有節流與冷卻機制。

range 分片的再平衡靠**切分（split）與合併（merge）區間**加**搬遷（move）**：一片太大/太熱就 split 成兩片、把其中一片搬到別台；相鄰的小片可以 merge。hash 分片的再平衡則是下一章一致性雜湊的主場——用環的結構讓加減節點只影響環上相鄰的 key。

## 對比與取捨

| 系統 | 分片策略 | 分片單位名稱 | 共識/複製 | 元資料/路由 |
|---|---|---|---|---|
| **Google Spanner** | range | tablet / split | 每 split 一個 Paxos group | placement driver + 目錄 |
| **CockroachDB** | range | range（預設 512 MB 自動 split） | 每 range 一個 Raft group | meta ranges（自身也是 range） |
| **TiKV** | range | region（預設 ~96 MB） | 每 region 一個 Raft group | PD（Placement Driver，自身 Raft） |
| **Cassandra** | hash（一致性雜湊 + vnode） | token range | 無 leader，quorum 複製 | gossip 傳播 token ring |
| **DynamoDB** | hash（一致性雜湊） | partition | quorum 複製 | 內部路由層 |

三個 range 系統（Spanner/CockroachDB/TiKV）的共同模式很清楚：**range 分片 + 每片一個共識 group（multi-raft/multi-paxos）+ 自動 split/merge + 一個管元資料的控制平面**。這是 2020 年代 NewSQL 的標準架構，你在練習 D 會親手搭一個簡化版。Cassandra/DynamoDB 則走 hash + 一致性雜湊 + 無 leader quorum 這條，換取更簡單的擴展但較弱的一致性與交易能力。

## 踩雷集錦

1. **「分片和複製是同一件事」→ 兩個正交維度。** 複製是同一份資料的多個拷貝（縱向，為容錯）；分片是資料切成不同部分放不同機器（橫向，為擴容）。加副本救不了容量與寫吞吐，分片救不了單片的可用性——**兩個都要**。搞混會導致「加副本還是慢」或「分了片一台掛就丟資料」。

2. **「加副本能提升寫吞吐」→ 恰恰相反。** 所有寫過同一個 leader、要多數派 commit。加副本抬高多數派門檻，寫**更慢**。要提升寫吞吐只能分片（把寫打散到不同 shard 的不同 leader）。

3. **「hash 分片一定比 range 好，因為均衡」→ 看你要不要 range scan。** hash 打散讓 range 查詢變成散射到所有 shard、慢且貴。要 SQL/範圍查詢/交易的系統幾乎都選 range 分片，用自動 split 對抗熱點。策略選擇是取捨，不是誰絕對好。

4. **「分片後單一超熱 key 也能靠加機器解決」→ 救不了。** 單一 key 只能待在一個 shard 上，再怎麼分片、加機器，那個 key 的熱都壓在一台上。這是分片的硬邊界，只能往應用層走（快取、拆 key、讀寫分離）。以為「加機器就能擴展任何負載」是危險的錯覺。

5. **「加機器再平衡很簡單，重算一下 key 屬於誰就好」→ 土法 hash mod N 會搬走幾乎全部資料。** 加一台機器讓 `% N` 變 `% (N+1)`，幾乎每個 key 的落點都變，等於整個叢集重搬——服務癱瘓。這正是下一章一致性雜湊存在的理由：把搬遷量從「幾乎全部」降到「約 1/N」。

## 進階：再往深一層

- **shard 的粒度怎麼定**：太大（一片 100 GB）→ 搬遷慢、熱點難拆、split 代價高；太小（一片 1 MB）→ 元資料爆炸（幾百萬個 shard，路由表巨大、每個 Raft group 的心跳開銷加總可觀）。真實系統的甜蜜點：TiKV region ~96 MB、CockroachDB range 預設 512 MB。這是「搬遷靈活性」與「元資料/心跳開銷」的權衡。

- **自動 split 的觸發**：range 系統監控每個 range 的大小與負載，超過閾值就自動一分為二。split 本身是個需要共識的操作（要原子地把一個 Raft group 變兩個、更新元資料），CockroachDB/TiKV 對此有專門的 split 協定。load-based split 更進階——不只看大小，看流量，把熱區間切細。

- **shard 內再加一層**：真實系統的複製常不只是「一個 shard 一個 Raft group」。可能有 learner（只複製不投票，用於加副本時的預熱或跨區唯讀副本）、witness（只投票不存全量資料，省儲存）。這些變體讓副本配置更靈活。

- **與一致性雜湊的關係**：range 分片用「切分區間 + 搬遷」再平衡，hash 分片用「一致性雜湊環」再平衡。下一章的一致性雜湊是 hash 分片下讓再平衡搬遷量最小化的核心資料結構——它不是分片策略的替代，是 hash 分片的實作利器。

## 本章重點整理

- 複製救不了容量與寫吞吐（每副本存全量、所有寫過一個 leader）；**只有分片能讓兩者隨機器數擴展**。
- 分片與複製**正交**：分片橫向切資料（擴容）、複製縱向拷貝（容錯），真實系統兩個一起用。搞混是常見大錯。
- 每個 shard 自己是一個完整 RSM（一個 Raft group），不同 shard 的 leader 散在不同機器 → 寫吞吐才擴展。這叫 **multi-raft**。
- 兩種策略：**range**（範圍查詢快、易熱點）vs **hash**（天然均衡、範圍查詢爆炸）。要 SQL/交易選 range，要極致均衡選 hash。
- 分片逼出兩個難題：**跨分片查詢**（散射-聚合）與**跨分片交易**（需要 2PC，Ch 30）——單片便宜、跨片昂貴。
- 實務兩座大山：**熱點**（遞增 key、超熱單 key）與**再平衡**（加減機器要搬遷量最小、服務不中斷）。後者是下一章一致性雜湊的動機。

## 自我檢核

- [ ] 不看筆記，我能解釋為什麼「加副本」救不了容量和寫吞吐，只有分片能救
- [ ] 我能畫出分片 × 複製的正交關係圖，並說出一台機器可以同時是哪些 shard 的什麼角色
- [ ] 我能講清楚 range 與 hash 分片各自的優缺點，以及「要 SQL 就選 range」的理由
- [ ] 我能說出跨分片交易為什麼比單片交易難，需要什麼額外機制
- [ ] 我能舉出至少兩種熱點來源，並指出「單一超熱 key 再分片也救不了」為什麼成立
- [ ] 我知道再平衡的三條鐵律，以及為什麼土法 hash mod N 在再平衡時會災難性失敗

## 延伸閱讀

- **《Designing Data-Intensive Applications》第 6 章「Partitioning」** — Martin Kleppmann（O'Reilly, 2017）
  - **這章說什麼**：分片的權威工程視角，range vs hash、熱點、再平衡、二級索引分片、路由，本章的每個主題它都講得更深
  - **讀哪裡**：整章都值得，"Partitioning and Replication"（正交關係）、"Rebalancing Partitions"（再平衡策略）、"Request Routing"（路由三做法）與本章最貼合
  - **前提**：讀得懂本章即可，這是本課 Part 4 的主參考

- **[Spanner: Google's Globally-Distributed Database](https://research.google/pubs/pub39966/)** — Corbett et al., OSDI（2012）
  - **這篇說什麼**：range 分片（tablet/split）+ 每片 Paxos + 跨片 2PC + TrueTime 的完整落地，本章「range 系統標準架構」的鼻祖
  - **讀哪裡**：第 2 節（架構、tablet、Paxos group）看分片與複製怎麼結合；第 4 節是交易與 TrueTime（Ch 39 會細讀）

- **[TiKV: The Placement Driver](https://tikv.org/deep-dive/scalability/introduction/)** — TiKV 官方 deep dive
  - **這是什麼**：真實系統怎麼做 region（range 分片單位）、自動 split/merge、PD 如何調度與再平衡，本章 multi-raft + 控制平面的工業級細節
  - **讀哪裡**：Scalability 章節，看 region 的生命週期與 PD 的調度邏輯

- **[Dynamo: Amazon's Highly Available Key-value Store](https://www.allthingsdistributed.com/files/amazon-dynamo-sosp2007.pdf)** — DeCandia et al., SOSP（2007）
  - **這篇說什麼**：hash 分片 + 一致性雜湊 + 無 leader quorum 這條路線的奠基作，與 range 系統形成鮮明對照
  - **讀哪裡**：第 4.2 節「Partitioning」講一致性雜湊與 virtual node，正好是下一章的引子；讀完再進 Ch 28 會很順

分片把資料切開，但「加減機器時怎麼把搬遷量降到最小」還沒解決——土法 hash mod N 會搬走幾乎全部資料。下一章我們用一致性雜湊把這個搬遷量從「幾乎全部」壓到「約 1/N」，並親手跑實驗驗證這個比例。

→ [Ch 28 一致性雜湊](./28-consistent-hashing.md)
