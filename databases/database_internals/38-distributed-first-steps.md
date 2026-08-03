# Ch 38 — 分散式的第一步

> **目標**：看清單機資料庫的天花板在哪裡、分散式系統怎麼打破這道牆，掌握 replication、sharding、2PC 這三個基礎概念的直覺與代價，並理解 CAP 定理說的究竟是什麼事。本章是「銜接章」，不深入共識演算法——Raft/Paxos 留給 distributed systems 課，這裡只建地基。

## 單機跑到天花板了

你已經用 Rust 親手刻了一個完整的單機資料庫：B-tree 儲存、LSM 壓縮、WAL 保護、MVCC 交易、SQL 查詢引擎。這套東西放一台機器上，能撐多大？

現實的上限大約是：

- **儲存容量**：最快的 NVMe 每台幾十 TB，但熱資料放 RAM 更快，而 RAM 通常幾百 GB 頂天。
- **寫入吞吐**：即使用 group commit，單機 WAL 的 fsync 約束讓寫入 IOPS 難超 50–100K 次/秒。
- **可用性**：那台機器壞了，整個服務就掛了（single point of failure）。

這三個天花板在不同場景的優先序不一樣：

| 場景 | 瓶頸 |
|---|---|
| 用戶讀放大（內容平台） | 讀吞吐不夠 |
| 寫入密集（IoT、日誌） | 寫吞吐不夠 |
| 金融、醫療服務 | 可用性不夠（任何停機都不可接受） |
| 資料超過單機容量 | 儲存容量不夠 |

分散式資料庫就是用多台機器來打破其中一個或多個天花板，代價是系統複雜度爆炸。

## Replication：讓多台機器都有同一份資料

複製（replication）是指把同一份資料的完整副本存在多台機器上。最常見的模式是 **primary-replica**（也叫 leader-follower）：

```
         ┌──────────┐
寫入 ──▶ │  Primary │ ──── WAL ──▶ replica 1
         │ (Leader) │ ──── WAL ──▶ replica 2
         └──────────┘
              │
讀取 ──▶ replica 1 / replica 2 / primary（都可以）
```

Primary 負責接受所有寫入；replica 把 primary 的 WAL（redo log）搬過來重放，讓自己的資料追上。讀可以分散到 replica，減輕 primary 的讀壓力。

### 同步複製 vs 非同步複製

寫入何時算「成功」？這是最核心的取捨：

**同步複製（synchronous）**：primary 等至少一個 replica 確認收到 WAL 後才回應客戶端「寫入成功」。

```
客戶端 ──write──▶ primary ──WAL──▶ replica
                    ▲                 │
                    └──── ACK ────────┘
                  (等到這裡才回應)
```

- 優點：primary 掛掉時，replica 保證有最新資料，不會遺失已確認的寫入。
- 缺點：每次寫入都多了一個網路來回，延遲顯著上升（通常增加數毫秒到幾十毫秒）。

**非同步複製（asynchronous）**：primary 把 WAL 寫進自己的磁碟就回應客戶端，背景慢慢推給 replica。

- 優點：寫入延遲與單機無異。
- 缺點：primary 突然掛掉時，尚未複製的 WAL 就消失了——**複製延遲（replication lag）** 期間的寫入全部丟失。

PostgreSQL 的 `synchronous_standby_names` 就是在做這件事：設定哪些 replica 需要同步確認。MySQL Group Replication、TiDB 則走半同步（semi-sync）：至少一個節點確認才提交。

### 複製延遲（Replication Lag）帶來的陷阱

非同步複製下，replica 永遠比 primary 稍舊。這帶來幾個違反直覺的異常：

**讀你自己的寫入（read-your-own-writes）**：你剛更新了自己的頭像，馬上刷新頁面，結果從 replica 讀到舊頭像——replica 還沒跟上。

**單調讀（monotonic reads）**：你先從 lag 小的 replica 1 讀到新資料，再從 lag 大的 replica 2 讀到更舊的資料——時間倒轉。

**一致前綴讀（consistent prefix reads）**：資料有因果關係（問題 A 的答案依賴問題 A 的提問），但因為兩個更新複製速度不同，讀到的順序顛倒。

這些問題的解法通常是：讀寫都去 primary（犧牲讀分散效果）、或者用 session token 把同一個用戶的讀強制黏在特定 replica。

## Partitioning / Sharding：資料切片分散到多台

複製解決了讀放大和可用性問題，但每台機器還是要存「全量資料」——容量天花板沒打破。分割（partitioning，俗稱 sharding）才是把資料真的拆開。

每個分割（shard/partition）只存總資料的一個子集，不同 shard 放在不同機器上。

### Range Partitioning（範圍分割）

把 key 按範圍切塊：

```
Shard 1: user_id 0 – 999,999
Shard 2: user_id 1,000,000 – 1,999,999
Shard 3: user_id 2,000,000 – 2,999,999
```

- 優點：範圍查詢效率高（user_id BETWEEN 500000 AND 600000 只查一個 shard）。
- 缺點：容易產生**熱點（hotspot）**——如果大量新用戶連續寫入，全中 shard 3；如果 user_id 只有少數幾個在用，某些 shard 閒置。

### Hash Partitioning（雜湊分割）

```
shard = hash(key) % num_shards
```

- 優點：負載分散均勻，無熱點問題。
- 缺點：範圍查詢必須打所有 shard，再做 scatter-gather merge；也讓 resharding（改 shard 數）變成大工程（consistent hashing 是常見解法）。

### 分割的代價

分割讓「跨分割的操作」變複雜。一個簡單的 JOIN 如果兩張表在不同 shard，就得先分別取資料再在應用層合併——或者用分散式 join，複雜度陡升。Facebook 曾為此引入 co-location（把相關資料盡量放同一 shard），TiDB 則是把分散式 join 藏進查詢引擎裡。

## 分散式交易與 Two-Phase Commit

複製和分割都還好，麻煩在「我要跨多個 shard 做一筆原子性的操作」。例如：從 shard A 的帳戶轉錢到 shard B 的帳戶，兩邊必須同時成功或同時失敗。

這就是分散式交易（distributed transaction）的問題，最經典的協議是 **Two-Phase Commit（2PC，兩階段提交）**：

```
協調者（Coordinator）
      │
      │── Phase 1 Prepare ──▶ 參與者 A：「你能提交嗎？」
      │                            A：「能」(寫 prepare log)
      │── Phase 1 Prepare ──▶ 參與者 B：「你能提交嗎？」
      │                            B：「能」(寫 prepare log)
      │
      │（若有任一方說「不能」，直接 abort 告知所有人）
      │
      │── Phase 2 Commit ───▶ 參與者 A：「提交」
      │── Phase 2 Commit ───▶ 參與者 B：「提交」
      │
      ◀── ACK ──── A, B 確認提交完成
```

2PC 保證：只要所有參與者都 vote「能」，且協調者決定 commit，就一定所有人都 commit（原子性）。

### 2PC 的阻塞問題（Blocking Problem）

2PC 有一個根本缺陷：**如果協調者在 Phase 2 送出 commit 之前就掛了，所有參與者都卡在「prepared」狀態，無法自行決定要 commit 還是 abort**——它們只能等協調者復活。

```
協調者 ──prepare──▶ A, B（都回應「能」）
協調者 ──掛掉──
A, B：鎖住了，我們 prepared 但不知道要 commit 還是 abort
（其他交易也被這些鎖卡住，整個系統部分凍結）
```

這段時間裡，持有鎖的資源被凍結，其他交易進不來。這就是 2PC 的阻塞問題，也是它被稱為 **blocking commit protocol** 的原因。

更強的共識算法（Raft、Paxos）能解決這個問題，代價是更複雜的協議。這是 distributed systems 課的主題，本章點到為止。

實務上，Google Spanner 用 Paxos group + 原子鐘（TrueTime）做分散式交易；TiDB 用 Percolator 協議（基於 Bigtable 的 2PC 變體）；CockroachDB 用 Raft。每種方案都是在 2PC 的阻塞問題上貼不同的膠帶。

## CAP 定理速覽

CAP 是 2000 年 Eric Brewer 提出的猜想，2002 年 Gilbert & Lynch 正式證明：**在一個分散式系統裡，以下三個性質不可能同時全滿足**：

- **C（Consistency，一致性）**：每次讀取都能讀到最新的寫入，或者報錯。（注意：這裡的 C 是 linearizability，不是 ACID 的 C。）
- **A（Availability，可用性）**：每個請求都能收到非錯誤的回應（不保證是最新資料）。
- **P（Partition Tolerance，分割容忍）**：當網路分割（nodes 彼此無法通訊）時，系統仍能繼續運作。

對於真實的分散式系統，P 不是可選項——網路分割是現實（光纖被挖斷、交換器失效），你必須容忍它。所以實際上的選擇是：**分割發生時，你犧牲 C 還是 A**？

| 選擇 | 代表系統 | 表現 |
|---|---|---|
| CP（犧牲 A） | ZooKeeper, HBase, etcd | 分割時拒絕服務（讓客戶端看到錯誤），但不傳回過舊資料 |
| AP（犧牲 C） | Cassandra, CouchDB, Riak | 分割時繼續服務，但可能傳回過舊資料 |

CAP 是一個重要的框架，但它被過度簡化和誤用。現實中的取捨遠比「三選二」細緻：網路分割是偶發事件，多數時候系統可以同時給你 C 和 A。更精確的框架是 PACELC（分割時 P→AC 取捨，正常時 L→latency-C 取捨）。

Jepsen 計畫（由 Kyle Kingsbury 主持）專門用真實的網路故障注入去測試資料庫的 CAP 宣稱是否誠實。結果不少「標榜 CP」的資料庫在真實分割下其實傳回了過舊資料。Ch 39 會提到 Jepsen。

## 踩雷

**非同步複製 + failover = 資料遺失的標準配方**。primary 掛掉時自動把 lag 最小的 replica 升為 primary，但「lag 最小」不代表「沒有 lag」。生產環境記錄好每次 failover 丟了幾筆。

**Range partitioning 容量熱點**。按時間範圍分割的表（如 event log），最新的 shard 永遠是寫入熱點，舊 shard 閒置。這是「最自然的分割方式」卻也是「最容易燒壞的分割方式」。

**把 2PC 協調者也做成高可用是必要的**。協調者是單點，它自己掛掉就造成全局 block。生產上協調者的狀態要寫入多副本存儲（例如本身跑 Raft）。

**CAP 中的 C 和 ACID 的 C 不是同一件事**。CAP 的 C 是 linearizability（全局順序一致），ACID 的 C 是「交易保持資料庫的完整性約束」。搞混了在面試和設計會議上會露餡。

**Resharding 是一場手術**。Hash partitioning 改 shard 數時，大量資料要搬移。沒有做 zero-downtime reshard 計畫就改 shard 數，資料庫會有幾小時不可用。Consistent hashing 減輕但無法完全消除這個問題。

## 進階延伸

你會想繼續深挖的方向：

**Raft 共識算法**：解決 2PC blocking problem 的現代答案。Diego Ongaro 的論文〈In Search of an Understandable Consensus Algorithm〉是最好的入門；TiKV 和 CockroachDB 的 Raft 實作都有詳細文件。

**Percolator 與 Google Spanner**：Percolator 論文（2010）展示了如何用一個 timestamp oracle + 跨行事務做大規模分散式交易。Spanner 論文（2012）加上 TrueTime 讓外部一致性（external consistency）在地球規模成為可能。

**CRDT（Conflict-free Replicated Data Types）**：在 AP 系統裡，衝突是必然的。CRDT 讓某些資料結構（計數器、集合、文字）可以無衝突地合併，是 eventual consistency 的一種實現策略。

**《Designing Data-Intensive Applications》第二部分**：Kleppmann 的 Ch 5–9 是分散式資料庫概念最好的白話書，把 replication/partitioning/transactions/consensus 完整串起來。

## 本章重點整理

- 單機資料庫的瓶頸：儲存容量、寫入吞吐、可用性（single point of failure）。
- Replication 解讀放大與可用性，代價是複製延遲和相關讀取異常。
- 同步複製保資料不丟，非同步複製保低延遲，semi-sync 是折衷。
- Partitioning 打破容量天花板；range 分割有熱點風險，hash 分割損失範圍查詢效率。
- 2PC 是分散式原子性的基礎協議，核心缺陷是 blocking：協調者掛掉時所有參與者凍結。
- CAP 定理：分割發生時只能保 C 或保 A，兩者不可兼得；現實比「三選二」複雜，PACELC 是更精確的框架。

## 自我檢核

- [ ] 我能解釋同步複製和非同步複製的延遲/可靠性取捨。
- [ ] 我能說出 read-your-own-writes、monotonic reads 各自是什麼異常、在什麼條件下發生。
- [ ] 我能說出 range partitioning 和 hash partitioning 各自適合什麼查詢模式。
- [ ] 我能走一遍 2PC 的兩個 phase，並指出協調者在哪個時間點掛掉會造成 blocking。
- [ ] 我能解釋 CAP 裡的 C 和 ACID 的 C 有何不同。

## 延伸閱讀

1. **《Designing Data-Intensive Applications》Ch 5, Ch 8, Ch 9** — Kleppmann。複製、分割、分散式交易的最佳入門讀物，概念清晰、圖示豐富。讀完後你對本章每一個概念都會有更深的直覺。
2. **Raft 論文：〈In Search of an Understandable Consensus Algorithm〉**（Ongaro & Ousterhout, 2014）。2PC blocking 的解方，共識算法裡最容易讀懂的一篇。分散式課的必讀起點。
3. **Spanner 論文：〈Spanner: Google's Globally-Distributed Database〉**（Corbett et al., OSDI 2012）。看 Google 如何用 TrueTime 把分散式交易做到地球尺度。2PC + Paxos + 原子鐘的工業級整合。
4. **Jepsen 測試報告**（https://jepsen.io/analyses）。Kyle Kingsbury 對幾十個主流資料庫做真實故障注入的結果——哪些資料庫說謊了？如何用 Elle/Knossos 框架驗證 consistency。
5. **〈A Critique of the CAP Theorem〉**（Martin Kleppmann, 2015）。CAP 的精確陳述和常見誤用，以及為什麼 PACELC 是更實用的思考框架。

---

本章是這門課最後一個純概念章，把單機的邊界說清楚之後，你就準備好去讀分散式系統課了。Ch 39 從另一個方向收尾：你手刻的這個 DB 離 production-ready 還差什麼，以及讀真實 DB 原始碼的路徑。

→ [Ch 39 離生產級還有多遠](./39-toward-production.md)
