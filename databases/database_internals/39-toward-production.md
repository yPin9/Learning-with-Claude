# Ch 39 — 離生產級還有多遠

> **目標**：誠實評估你手刻的 mini DB 和工業級資料庫（Postgres、SQLite）之間的距離，知道「還沒做的」是什麼、為什麼那些東西重要，並拿到一份「如何讀真實 DB 原始碼」的路徑地圖。

## 你已經造了什麼

先承認這件事：你從頁面管理寫到 B+tree、寫了 LSM 引擎、做了 WAL 和 crash recovery、實作了 MVCC 和隔離級別、蓋了一個能跑 SQL 的查詢引擎。這不是玩具——這是很多工程師職涯中從來沒做過的東西。

但「能跑」和「能上線」是兩個世界。這章就是把兩個世界之間的鴻溝看清楚。

## 正確性測試：你的 DB 真的沒問題嗎

### Property-Based Testing

你大概寫了一些單元測試：插入幾筆資料、查詢確認結果對。這種測試的問題是**覆蓋面太窄**——你只測了你想到的情況，而 bug 通常藏在你沒想到的地方。

Property-based testing 換個方向：你描述「資料庫應該滿足什麼性質」，測試框架自動生成幾千個隨機輸入來嘗試打破這個性質。

例如：對任何插入序列，插入後查詢必須能讀到所有插入的資料。這個性質在 B+tree 的 split 邊界情況可能失敗，但你的手動測試很難恰好打中那個邊界。

Rust 的 `proptest` 和 `quickcheck` 都能做這件事。TiKV 用 proptest 測試其儲存層，發現過幾個手動測試完全沒抓到的 B-tree 邊界 bug。

### Fuzzing DB

資料庫的另一個攻擊面是輸入的 SQL 本身——畸形的 SQL 不應該讓資料庫 panic 或傳回錯誤結果。Fuzzing 是用隨機或變異的輸入轟炸 SQL parser 和 executor，觀察有沒有崩潰或靜默錯誤（wrong answer without error）。

SQLite 是目前最好的目標學習案例：它用 AFL 和 libFuzzer 長期測試，有一套完整的 fuzzing harness。你可以拿你的 DB 對接 `cargo-fuzz`，先從 SQL parser 開始。

### Jepsen 對分散式的測試

如果你的 DB 有複製或分散式交易（Ch 38 的內容），Jepsen 是業界最嚴苛的正確性測試工具。它在真實叢集上製造網路分割、機器重啟、時鐘漂移，然後驗證資料庫的一致性宣稱是否成立。

你的單機 DB 不需要 Jepsen，但這個框架的思考方式值得學習：**從故障假設出發**，而不是從「一切正常」出發設計測試。

## 可觀測性：資料庫在做什麼？

生產環境裡資料庫跑慢了，你能診斷嗎？

### EXPLAIN / EXPLAIN ANALYZE

Postgres 的 `EXPLAIN` 輸出執行計畫；`EXPLAIN ANALYZE` 真的執行並輸出每個節點實際花了多久、掃了多少行：

```
EXPLAIN ANALYZE SELECT * FROM orders WHERE user_id = 42;

Seq Scan on orders  (cost=0.00..184.00 rows=1 width=36)
                    (actual time=0.015..1.234 rows=1 loops=1)
  Filter: (user_id = 42)
  Rows Removed by Filter: 9999
Planning Time: 0.087 ms
Execution Time: 1.251 ms
```

你一看就知道：沒有用索引，掃了 10000 行只留 1 行。這就是「在 user_id 上建索引」的直接證據。

你的 mini DB 如果有 `EXPLAIN`，除錯就容易十倍。實作思路：在 executor 的每個 operator 記錄「實際掃描行數」和「花費時間」，查詢結束後印出來。

### 慢查詢日誌（Slow Query Log）

記錄超過閾值的查詢（例如 100ms 以上），並把執行計畫和參數一起存起來。這是生產環境最常用的效能診斷工具，SQLite 和 Postgres 都有。

實作不難：在查詢入口計時，結束時比較閾值，超標就寫一行到日誌。

### Metrics

你至少要知道這幾個數字：
- 每秒查詢數（QPS）
- 查詢延遲的 p50 / p95 / p99（平均數會被少數慢查詢拉壞）
- Buffer pool hit rate（低於 90% 說明 working set 超過快取）
- 寫入放大（LSM 引擎的 compaction 放大比）

這些指標暴露出來，才能做有根據的優化，而不是「感覺慢就亂猜」。

## 你的 mini DB 還沒做的

這些功能讓你的 DB 從「練習專案」變成「真的能用的工具」。按優先序列：

### 型別系統與約束

你目前的 SQL 引擎可能只有 INTEGER 和 TEXT。真實資料庫需要：
- `NULL`（以及 NULL 傳播的三值邏輯）
- `NOT NULL`、`UNIQUE`、`CHECK` 約束
- `FOREIGN KEY`（以及 ON DELETE CASCADE / RESTRICT）
- 日期/時間型別（連帶時區的複雜性）

約束違反必須在 executor 層攔截，外鍵約束還得查詢另一張表——這讓事情複雜起來。

### 觸發器與預存程序

觸發器（trigger）讓資料庫在特定事件（INSERT/UPDATE/DELETE）後自動執行邏輯。這需要在 executor 裡嵌入一個小的程式執行環境（很多資料庫用 PL/pgSQL 或 Lua）。SQLite 的觸發器實作是個好的學習範本。

### 連線管理與並發

現在你的 DB 大概是單執行緒的 REPL。生產環境需要：
- 接受多個客戶端連線（TCP 或 Unix socket）
- 每個連線獨立的交易上下文
- 連線池（connection pool）——建立連線成本高，重用比每次重建便宜
- 身份驗證與權限（至少要能問「你是誰」、「你有沒有讀這張表的權限」）

### Vacuum 與空間回收

MVCC（Ch 22）保留舊版本資料讓讀者看到一致快照。但舊版本不能留著不管，否則磁碟會爆炸。Postgres 的 VACUUM 就是定期清理「所有活躍交易都不再需要的舊版本」。

時機的判斷（什麼時候清、清到哪個時間點之前）比聽起來難——你要追蹤所有活躍交易的最小可見版本（xmin horizon）。

### 備份與時間點復原（PITR）

WAL 不只是 crash recovery 的工具，也是備份的基礎。你可以持續把 WAL 送到 S3，需要時把基礎快照加上 WAL 重放到任意時間點——這就是 Point-In-Time Recovery（PITR），Postgres 的完整 PITR 文件是很好的學習材料。

## 如何讀真實 DB 原始碼

有了本課的底層知識，你現在有能力讀真實資料庫的 source code，而不是看天書。三個推薦入口：

### SQLite：從 `btree.c` 讀起

SQLite 的 codebase 是最適合個人學習的：單一 C 檔案（amalgamation 模式）、文件詳盡、邏輯清晰。

路徑建議：
1. `btree.c`：B+tree 的 search / insert / delete，對照你寫的實作看「工業級怎麼處理邊界情況」。特別注意它的 page pinning 和 cursor 管理。
2. `pager.c`：page cache 和 WAL 邏輯，對應你的 buffer pool + WAL。
3. `vdbe.c`：Virtual Database Engine，SQLite 的查詢執行器（register-based VM），對應你的 executor。
4. `select.c` / `insert.c`：查詢計畫的生成，相對難讀，最後看。

SQLite 有一份極好的架構文件（https://www.sqlite.org/arch.html），讀 source 前先讀這份。

### LevelDB：精簡好讀的 LSM

LevelDB 只有約 2 萬行 C++，是學習 LSM 原理和工業實作的最佳標本：
- `db/log_writer.cc`, `db/log_reader.cc`：WAL 格式，結構清晰。
- `table/block.cc`, `table/format.cc`：SSTable 格式，對照你的 Ch 13。
- `db/version_set.cc`：Compaction 策略的核心，對照 Ch 15。
- `db/memtable.cc`：基於 skip list 的 MemTable，對照 Ch 12。

LevelDB 幾乎沒有歷史包袱，每個模組職責清晰。讀完 LevelDB 再看 RocksDB（它的 fork），就是在看「同樣的設計被 Facebook 帶著工業需求擴充了十年後長什麼樣」。

### Postgres：從 `executor` 讀起

Postgres 是幾百萬行的龐然大物，不能從頭讀，要有目標地切入：
- `src/backend/executor/`：從 `execMain.c` 看查詢執行的入口，再看 `nodeSeqscan.c`、`nodeHashjoin.c`——每個 executor node 一個檔案，結構非常清晰。
- `src/backend/optimizer/`：查詢優化器，`planner.c` 是入口，`costsize.c` 是 cost-based 優化的核心估算邏輯。
- `src/backend/storage/buffer/`：buffer pool 管理，對照你的 Ch 5。
- `src/backend/access/heap/`：heap file 的 tuple 格式和 MVCC 實作——你會看到 `t_xmin`, `t_xmax` 這些你在 MVCC 章學過的欄位真實存在的樣子。

Postgres 有 wiki（https://wiki.postgresql.org/wiki/Backend_flowchart）提供後端架構圖，讀 source 前先看這張圖定位方向。

## 踩雷

**「能過測試」不等於「正確」**。資料庫的 bug 最可怕的一種是靜默的錯誤資料——它不崩潰、不報錯，只是默默傳回錯的答案。property-based testing 和 Jepsen 的存在就是因為這類 bug 不靠隨機壓測根本發現不了。

**可觀測性應該第一天就加**。「等功能做好再加 metrics」是陷阱——等你真的需要診斷問題時，重構加入 metrics 的成本已經很高。從設計開始就把計時和計數的 hook 留好。

**讀真實 DB 原始碼要有問題驅動**。「我要讀 Postgres source」會迷失；「我要看 Postgres 的 MVCC 怎麼清舊版本」就很具體，你知道搜什麼關鍵字、看哪個函式。用你已有的知識（本課學的概念）當搜尋的導航。

**NULL 比你想的複雜**。`NULL = NULL` 在 SQL 裡是 UNKNOWN，不是 TRUE。三值邏輯（TRUE/FALSE/UNKNOWN）滲透進 WHERE、JOIN、聚合的每個角落。很多「自己寫 SQL 引擎」的專案在這裡埋下了靜默 bug。

**Vacuum 太慢和 Vacuum 太快都是問題**。Vacuum 太少，磁碟被舊版本撐爆；Vacuum 太積極，佔用 I/O 造成查詢延遲。Postgres 的 autovacuum 有一套複雜的節流邏輯，就是在走這條鋼索。

## 進階延伸

**Column-store vs Row-store 的真實代價**：你學了欄式儲存（Ch 36），但真實的分析型資料庫（ClickHouse、DuckDB）和 OLTP 資料庫在工程上的取捨遠不止「欄存 vs 行存」。DuckDB 的 blog 有一系列深度文章講它每個設計決策的理由。

**Write-Optimized Data Structures**：除了 B-tree 和 LSM，還有 Bw-tree（微軟 Hekaton 用，無鎖 B-tree 變體）、WiredTiger（MongoDB 預設引擎，B-tree + MVCC 的特殊組合）。你對 B-tree 和 LSM 的底層理解讓你現在有能力讀懂這些論文。

**Database Testing 的最佳實踐**：SQLite 有一個叫「所有執行路徑都被覆蓋測試」的目標——它的測試套件有幾百萬行，比實作本身大幾十倍。〈How SQLite Is Tested〉這篇文章是測試嚴謹性的極佳範本。

## 本章重點整理

- Property-based testing 用性質描述替代手寫測試案例，能找到邊界情況 bug；fuzzing 對 parser/executor 也有效。
- Jepsen 是分散式資料庫正確性的壓力測試框架，思考方式（從故障假設出發）適用於任何系統。
- 可觀測性工具（EXPLAIN、慢查詢日誌、metrics）是診斷生產問題的必要條件，不是可選功能。
- 生產級資料庫還需要：完整型別系統、約束、觸發器、連線管理、Vacuum、PITR 備份——每一項都是獨立的工程問題。
- 讀真實 DB source 的入口：SQLite 從 `btree.c`、LevelDB 從 `db/log_writer.cc`、Postgres 從 `src/backend/executor/`。
- 用「我想理解這個具體機制」驅動閱讀，不要「我要通讀 Postgres」——那是一條沒有盡頭的路。

## 自我檢核

- [ ] 我能說出 property-based testing 和傳統單元測試在測試策略上的根本差異。
- [ ] 我能解釋為什麼慢查詢應該記錄 p99 延遲而不是平均值。
- [ ] 我知道 VACUUM 在 MVCC 資料庫裡的角色——它清的是什麼，為什麼必要。
- [ ] 我能指出 SQLite 的 B+tree 實作在哪個檔案，並說出從哪個函式進入 search 流程。
- [ ] 我能說出 NULL 在 SQL 三值邏輯裡的行為，以及 `NULL = NULL` 傳回什麼。

## 延伸閱讀

1. **〈How SQLite Is Tested〉**（https://www.sqlite.org/testing.html）。世界上測試覆蓋率最高的 C 程式之一的測試哲學，每個做資料庫或系統軟體的工程師都應該讀一遍。
2. **《Database Internals》第一部分後記**（Petrov）。作者對「從這裡去哪」的路線圖建議，與本章互補。
3. **Jepsen 部落格**（https://jepsen.io）。每份測試報告都是一個「真實分散式系統在壓力下的故障模式」的案例研究，讀法：先看結論，再回頭看測試方法。
4. **LevelDB source code**（https://github.com/google/leveldb）。讀完本課再看，你有能力在一週內讀懂它的 90%，這是建立自信「我真的理解 DB 內部」的最好驗收。
5. **DuckDB 技術部落格**（https://duckdb.org/news/）。DuckDB 是目前設計最乾淨的 embedded OLAP 資料庫，每篇文章都在解釋一個設計決策——vectorized execution、out-of-core join、parallel query——對照你學的 executor 知識很有感。

---

理論看完了，整合時間到了。Final Project 把全課的每一塊都組起來，目標是一個能真正跑 SQL 的單機資料庫。

→ [Final Project：mini relational database](./final-project-mini-database.md)
