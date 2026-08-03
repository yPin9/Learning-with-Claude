# 分散式系統學習筆記：從時鐘謊言到手刻 Raft，再打穿共識層

> 給已經把單機挖到底（kernel、networking）、想補「多機協調」水平地基的系統/資安工程師。

這系列把你的能力從「一台機器挖到底」擴到「一群會各自當機、各自說謊、彼此看不到對方的機器要達成一致」。原理推到證明層級（FLP、Paxos、Raft safety），關鍵系統用 **Go** 真的寫一個能跑、能過 partition/crash 測試的版本，最後接上你的資安視角：把共識層當攻擊面來看（BFT、Sybil、eclipse、Nakamoto consensus）。學完你能讀懂 etcd / Spanner / Kafka 的設計、手刻一個容錯分片 KV store、並看穿共識協定被攻破的方式。

## 為什麼學這個？

- **這是你版圖裡最刺眼的水平缺口**：你什麼都往下鑽（kernel、微架構、firmware），卻沒往「單機以上」鑽。真實系統幾乎都是分散式的，而分散式的難不是「更大的單機」，是**部分失敗（partial failure）**——一部分壞了、其他還活著、而且你不知道是壞了還是只是慢。
- **底層理解的價值**：Paxos/Raft 不是背 API，是理解「在一個會丟訊息、會延遲、節點會當的世界裡，怎麼還能對一件事達成一致」。這套思維會反過來讓你更懂 kernel 的 RCU、記憶體一致性模型、甚至硬體的 cache coherence。
- **資安職涯角度**：共識層攻擊、拜占庭容錯、區塊鏈安全、雲端多節點協調的攻擊面——這些都建立在懂分散式系統原理之上。你的攻擊天梯缺這一塊地基。

## 先修知識

- **並發程式設計**（程度：懂 mutex/channel/race condition；本課用 Go 的 goroutine/channel，會邊用邊補）
- **網路基礎**（程度：TCP/IP、RPC 概念即可；你的 `networking` 課已足夠）
- **一種系統語言**（程度：能讀寫 Go 或任一 C-like 語言；Go 語法會在 Ch 0 補齊到夠用）
- 沒有也沒關係的：分散式系統經驗、Go 實務經驗、任何共識演算法背景——本課從零推導

## 課程地圖

### Part 0 — 地基與失敗模型（Ch 0–3）
- [Ch 0 環境搭建與確定性模擬器](./00-environment-setup.md)
- [Ch 1 為什麼分散式這麼難](./01-why-distributed-is-hard.md)
- [Ch 2 失敗與網路模型](./02-failure-and-network-models.md)
- [Ch 3 RPC 與訊息語意](./03-rpc-and-message-semantics.md)

### Part 1 — 時間與順序（Ch 4–7）
- [Ch 4 實體時鐘的謊言](./04-physical-clocks.md)
- [Ch 5 Lamport 邏輯時鐘](./05-lamport-clocks.md)
- [Ch 6 Vector Clock：因果偵測](./06-vector-clocks.md)
- [Ch 7 全序廣播](./07-total-order-broadcast.md)
- [練習 A：Vector Clock + 因果一致訊息層](./practice-a-vector-clock-causal-messaging.md)

### Part 2 — 複製與一致性模型（Ch 8–14）
- [Ch 8 為什麼複製](./08-why-replicate.md)
- [Ch 9 一致性模型光譜](./09-consistency-models.md)
- [Ch 10 CAP 定理](./10-cap-theorem.md)
- [Ch 11 PACELC 與延遲取捨](./11-pacelc.md)
- [Ch 12 主從複製](./12-primary-backup-replication.md)
- [Ch 13 Quorum 複製](./13-quorum-replication.md)
- [Ch 14 衝突解決與 CRDT 入門](./14-conflict-resolution-crdt.md)
- [練習 B：Quorum-based KV Store](./practice-b-quorum-kv-store.md)

### Part 3 — 共識核心（Ch 15–24）
- [Ch 15 共識問題定義](./15-consensus-problem.md)
- [Ch 16 FLP 不可能定理](./16-flp-impossibility.md)
- [Ch 17 繞過 FLP](./17-circumventing-flp.md)
- [Ch 18 Paxos：single-decree](./18-paxos-single-decree.md)
- [Ch 19 Multi-Paxos](./19-multi-paxos.md)
- [Ch 20 Raft ①：Leader Election](./20-raft-leader-election.md)
- [Ch 21 Raft ②：Log Replication](./21-raft-log-replication.md)
- [Ch 22 Raft ③：Safety 與選舉限制](./22-raft-safety.md)
- [Ch 23 Raft ④：Membership 與 Snapshot](./23-raft-membership-snapshot.md)
- [Ch 24 Paxos vs Raft vs VR vs Zab](./24-consensus-comparison.md)
- [練習 C：手刻 Raft](./practice-c-build-raft.md)

### Part 4 — 建構真實系統（Ch 25–31）
- [Ch 25 複製狀態機（RSM）](./25-replicated-state-machine.md)
- [Ch 26 用 Raft 建 KV Store：線性一致讀](./26-raft-kv-linearizable-reads.md)
- [Ch 27 分片（Sharding/Partitioning）](./27-sharding-partitioning.md)
- [Ch 28 一致性雜湊](./28-consistent-hashing.md)
- [Ch 29 成員與失敗偵測：SWIM/Gossip](./29-membership-failure-detection-swim.md)
- [Ch 30 分散式交易：2PC/3PC](./30-distributed-transactions-2pc-3pc.md)
- [Ch 31 Saga 與 Percolator](./31-saga-percolator.md)
- [練習 D：分片 KV + Shard Controller](./practice-d-sharded-kv.md)

### Part 5 — 拜占庭容錯與分散式安全（Ch 32–38）
- [Ch 32 拜占庭將軍問題](./32-byzantine-generals.md)
- [Ch 33 PBFT](./33-pbft.md)
- [Ch 34 現代 BFT：Tendermint/HotStuff](./34-modern-bft-hotstuff.md)
- [Ch 35 共識層攻擊面](./35-consensus-attack-surface.md)
- [Ch 36 Nakamoto Consensus](./36-nakamoto-consensus.md)
- [Ch 37 分散式系統安全](./37-distributed-systems-security.md)
- [Ch 38 攻擊複製與交易系統](./38-attacking-replication-transactions.md)
- [練習 E：PBFT 簡化版 + 拜占庭攻擊模擬](./practice-e-pbft-and-byzantine-attack.md)

### Part 6 — 現代系統與工程實務（Ch 39–45）
- [Ch 39 Google Spanner](./39-google-spanner.md)
- [Ch 40 Kafka：日誌即系統核心](./40-kafka-log.md)
- [Ch 41 etcd / ZooKeeper](./41-etcd-zookeeper.md)
- [Ch 42 事件溯源 / CQRS](./42-event-sourcing-cqrs.md)
- [Ch 43 測試分散式系統](./43-testing-distributed-systems.md)
- [Ch 44 可觀測性與除錯](./44-observability-tracing.md)
- [Ch 45 設計陷阱與反模式](./45-design-pitfalls.md)
- [Final Project：容錯分片 KV Store](./final-project-fault-tolerant-sharded-kv.md)

## 學習方式建議

1. **讀完一章就動手**：本課的靈魂是 Ch 0 的確定性模擬器——它能重現 partition、delay、crash、時鐘偏移。每個練習都跑在它上面，bug 可重現。
2. **故意把它弄壞**：拔掉 Raft 的一條 safety 規則，看它怎麼在 partition 後選出兩個 leader、丟失已提交的 log。分散式的 bug 只有在故意注入失敗時才會現形。
3. **先推導再看 code**：Paxos/Raft/PBFT 都先從「為什麼需要這一步」推到協定，再看實作。背協定沒用，理解每一步在防哪個失敗才有用。
4. **讀原始論文**：這領域的頂會論文（Lamport、Ongaro、Castro-Liskov）出奇地好讀，每章延伸閱讀都指向具體該讀哪一節。

## 精選資料庫

這裡列整門課最值得反覆參照的資源，每章的「延伸閱讀」會指向更具體的小節。

### 必讀基礎

- **《Designing Data-Intensive Applications》** — Martin Kleppmann（O'Reilly, 2017）
  - 整門課的主參考書；第 5–9 章（複製、分片、交易、一致性與共識）涵蓋本課 Part 1–4 的 70%，而且是工程視角寫得最清楚的一本
- **[MIT 6.5840 (原 6.824) Distributed Systems](https://pdos.csail.mit.edu/6.824/)**
  - 課程 lab 用 Go 手刻 Raft + 分片 KV，本課 Part 3–4 的練習就是對齊它；lecture notes 與 paper 清單是權威來源

### 推薦論文

- **[In Search of an Understandable Consensus Algorithm (Raft)](https://raft.github.io/raft.pdf)** — Ongaro & Ousterhout, USENIX ATC（2014）
  - Raft 原始論文，可讀性極高；本課 Ch 20–23 逐節對照它
- **[Impossibility of Distributed Consensus with One Faulty Process (FLP)](https://groups.csail.mit.edu/tds/papers/Lynch/jacm85.pdf)** — Fischer, Lynch, Paterson, JACM（1985）
  - 分散式系統最重要的不可能結果；Ch 16 帶你讀懂證明骨架
- **[Practical Byzantine Fault Tolerance](https://pmg.csail.mit.edu/papers/osdi99.pdf)** — Castro & Liskov, OSDI（1999）
  - BFT 的奠基作，Part 5 的核心；Ch 33 逐階段拆解

### 推薦部落格 / 文章

- **[Notes on Distributed Systems for Young Bloods](https://www.somethingsimilar.com/2013/01/14/notes-on-distributed-systems-for-young-bloods/)** — Jeff Hodges
  - 業界老手把「分散式系統實務上真正會咬你的東西」濃縮成一篇，讀完再回頭看理論會更有感
- **[Jepsen Analyses](https://jepsen.io/analyses)** — Kyle Kingsbury (Aphyr)
  - 真實資料庫在 partition/時鐘異常下違反一致性的實測報告；Ch 43 的靈魂，看真實系統怎麼壞

### 讀完本課之後

- **《Database Internals》** — Alex Petrov（O'Reilly, 2019）（把儲存引擎 + 分散式的下半場推更深，接你未來若開「資料庫內核」課）
- **[Raft 視覺化 thesecretlivesofdata.com/raft](https://thesecretlivesofdata.com/raft/)**（動畫看 Raft 選舉與複製，卡住時的直覺補帖）
