# 資料庫內部學習筆記：用 Rust 從零手刻一個關聯式資料庫

> 給懂系統底層（kernel / 檔案系統 / 記憶體）、想搞懂「資料到底怎麼存、怎麼查、怎麼保證不壞」的工程師。

這系列用 Rust 從零打造一個單機關聯式資料庫：兩種儲存引擎（B-tree 與 LSM-tree）都親手寫、加上 WAL 與 crash recovery、MVCC 交易、再蓋一層能跑 SQL 的查詢引擎（parser → planner → optimizer → executor）。學完你不再是「會用資料庫」，而是「知道 SQLite/Postgres/RocksDB 內部每一層在做什麼、為什麼那樣設計」。

## 為什麼學這個？

- **補 CS 系統地基最後一塊**：你懂 CPU、kernel、compiler、network、檔案系統，但「資料怎麼持久化與查詢」是唯一還沒動手的系統領域。它把 page cache、fsync、B-tree、mmap 這些你見過的零件組成一個真的東西。
- **底層理解的價值**：資料庫是「用對的資料結構對抗磁碟物理限制」的極致展示。B-tree vs LSM 的取捨、MVCC 怎麼讓讀寫不互斥、query optimizer 怎麼把宣告式 SQL 變成執行計畫——這些思維遠超資料庫本身。
- **職涯**：資料庫內部是系統工程師的高含金量技能，面試常考（隔離級別、索引、join 演算法），也是進 infra/DB 團隊的門票。

## 先修知識

- 系統程式概念：process/memory、檔案 I/O、page cache（程度：讀過 kernel_internals 那種最好，不強求）
- Rust 基礎：ownership/borrow/trait/泛型（程度：能讀寫一般 Rust；tree 結構會用到 `Rc<RefCell>`/`unsafe`，課程會解釋）
- 資料結構：BST、hash table、堆積排序（程度：知道 Big-O 與基本操作）
- 沒有也沒關係的：資料庫理論、SQL 標準細節——課程會補

## 課程地圖

### Part 0 — 地基與心法（Ch 0–3）
- [Ch 0 環境搭建](./00-environment-setup.md)
- [Ch 1 資料庫全景：一條 SELECT 的旅程](./01-database-panorama.md)
- [Ch 2 儲存的根本問題：記憶體 vs 磁碟](./02-storage-fundamentals.md)
- [Ch 3 檔案 I/O 與持久性](./03-file-io-durability.md)

### Part 1 — B-tree 儲存引擎（Ch 4–10）
- [Ch 4 頁面式儲存與 slotted page](./04-page-storage.md)
- [Ch 5 Buffer Pool](./05-buffer-pool.md)
- [Ch 6 B+tree 原理](./06-btree-principles.md)
- [Ch 7 B+tree 實作（一）search/insert/split](./07-btree-insert-split.md)
- [Ch 8 B+tree 實作（二）delete/merge/rebalance](./08-btree-delete-merge.md)
- [Ch 9 B+tree 並發：latch crabbing](./09-btree-concurrency.md)
- [Ch 10 索引：secondary/covering/range scan](./10-indexes.md)
- [練習 A：可持久化 B+tree KV store](./practice-a-btree-kv-store.md)

### Part 2 — LSM 儲存引擎（Ch 11–16）
- [Ch 11 LSM 原理與三放大](./11-lsm-principles.md)
- [Ch 12 MemTable（skip list）](./12-memtable.md)
- [Ch 13 SSTable](./13-sstable.md)
- [Ch 14 Bloom filter](./14-bloom-filter.md)
- [Ch 15 Compaction 策略](./15-compaction.md)
- [Ch 16 LSM vs B-tree 總對比（RUM）](./16-lsm-vs-btree.md)
- [練習 B：mini LSM engine](./practice-b-lsm-engine.md)

### Part 3 — WAL、交易與復原（Ch 17–23）
- [Ch 17 WAL：redo log / LSN / group commit](./17-wal.md)
- [Ch 18 Crash Recovery：ARIES](./18-crash-recovery-aries.md)
- [Ch 19 交易與 ACID](./19-transactions-acid.md)
- [Ch 20 隔離級別與異常現象](./20-isolation-levels.md)
- [Ch 21 並發控制（一）2PL](./21-concurrency-2pl.md)
- [Ch 22 並發控制（二）MVCC](./22-concurrency-mvcc.md)
- [Ch 23 進階並發：SSI / OCC](./23-advanced-concurrency.md)
- [練習 C：WAL + MVCC 整合](./practice-c-wal-mvcc.md)

### Part 4 — 查詢層（Ch 24–33）
- [Ch 24 查詢處理全景](./24-query-processing-overview.md)
- [Ch 25 SQL Parser](./25-sql-parser.md)
- [Ch 26 Catalog / Schema](./26-catalog-schema.md)
- [Ch 27 Logical Plan（關聯代數）](./27-logical-plan.md)
- [Ch 28 Physical Plan](./28-physical-plan.md)
- [Ch 29 執行模型：Volcano vs vectorized](./29-execution-model.md)
- [Ch 30 Join 演算法](./30-join-algorithms.md)
- [Ch 31 排序與聚合](./31-sort-aggregation.md)
- [Ch 32 查詢優化（一）rule-based](./32-query-optimization-rules.md)
- [Ch 33 查詢優化（二）cost-based](./33-query-optimization-cost.md)
- [練習 D：SQL parser + planner + executor](./practice-d-sql-engine.md)

### Part 5 — 進階與整合（Ch 34–39）
- [Ch 34 統計與 cardinality estimation](./34-statistics-cardinality.md)
- [Ch 35 進階索引](./35-advanced-indexes.md)
- [Ch 36 欄式儲存與向量化](./36-columnar-storage.md)
- [Ch 37 記憶體與效能：mmap 的爭議](./37-memory-performance.md)
- [Ch 38 分散式的第一步](./38-distributed-first-steps.md)
- [Ch 39 離生產級還有多遠](./39-toward-production.md)
- [Final Project：mini relational database](./final-project-mini-database.md)

## 學習方式建議

1. **讀完一章就寫 code**：這門課的每個概念都對應一段可跑的 Rust。看懂不等於會寫，動手才知道 B+tree 的 split 有多少邊界情況。
2. **故意把它弄壞**：拔掉 fsync 看 crash 後資料怎麼掉、關掉 latch 看並發怎麼壞——失敗最能建立直覺。
3. **對照真實 DB 原始碼**：每章延伸閱讀指向 SQLite/LevelDB/Postgres 的對應實作，看工業級怎麼處理你剛寫的東西。

## 精選資料庫

### 必讀基礎

- **《Database Internals》** — Alex Petrov（O'Reilly, 2019）
  - 這門課的主要參考書；Part I 講儲存引擎（B-tree/LSM）、Part II 講分散式，前半與本課高度重疊
- **[CMU 15-445/645 Database Systems](https://15445.courses.cs.cmu.edu/)** — Andy Pavlo
  - 全世界最好的資料庫系統公開課，有影片+作業（BusTub，C++）；本課很多主題對應它的 lecture
- **《Designing Data-Intensive Applications》** — Martin Kleppmann（O'Reilly, 2017）
  - Ch 3（儲存）與 Ch 7（交易）是本課 Part 1-3 的最佳白話補充

### 推薦論文

- **[The Log-Structured Merge-Tree](https://www.cs.umb.edu/~poneil/lsmtree.pdf)** — O'Neil et al.（1996）
  - LSM 的原始論文，Part 2 的源頭
- **[ARIES](https://cs.stanford.edu/people/chrismre/cs345/rl/aries.pdf)** — Mohan et al.（1992）
  - crash recovery 的經典，Ch 18 的基礎
- **[Access Path Selection in a Relational DBMS](https://people.eecs.berkeley.edu/~brewer/cs262/3-selinger79.pdf)** — Selinger et al.（System R, 1979）
  - cost-based 查詢優化的開山之作，Ch 33 的源頭

### 讀完本課之後

- **《Designing Data-Intensive Applications》後半**（分散式資料，接未來的 distributed systems 課）
- **[RocksDB Wiki](https://github.com/facebook/rocksdb/wiki)**（把你寫的 mini LSM 拿去對照工業級 LSM 的每個細節）
