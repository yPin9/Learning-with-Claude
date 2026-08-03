# Final Project — Mini Relational Database

> **目標**：把全課 40 章學過的每一層——B-tree 儲存引擎、LSM 引擎、WAL + crash recovery、MVCC 交易、SQL parser → planner → optimizer → executor——組裝成一個能真正跑 SQL 的單機關聯式資料庫。它有名字：**ferrisdb**。完成後你不只有一份作品，你對「資料庫從磁碟到 SQL 的每一層」有的是親手建造的直覺。

---

## 專案願景

ferrisdb 是一個支援 SQL 子集的單機關聯式資料庫，從命令列 REPL 接受 SQL，把資料持久化到磁碟，支援 crash recovery 和 MVCC 交易。

你可以選擇儲存引擎：

- **B-tree 引擎**（接 Part 1 + 練習 A）：適合讀多寫少、需要範圍查詢的場景。
- **LSM 引擎**（接 Part 2 + 練習 B）：適合寫密集場景，但讀有放大成本。

無論選哪個，上面的 WAL + MVCC + SQL 層介面完全一樣——這就是 StorageEngine trait 的意義。

一次完整的端到端演示應該是：

```
ferrisdb> CREATE TABLE users (id INTEGER, name TEXT, age INTEGER);
OK

ferrisdb> INSERT INTO users VALUES (1, 'Alice', 30);
OK

ferrisdb> INSERT INTO users VALUES (2, 'Bob', 25);
OK

ferrisdb> SELECT name, age FROM users WHERE age > 28;
+---------+-----+
| name    | age |
+---------+-----+
| Alice   |  30 |
+---------+-----+
1 row

ferrisdb> BEGIN;
OK

ferrisdb> INSERT INTO users VALUES (3, 'Carol', 35);
OK

ferrisdb> ROLLBACK;
OK

ferrisdb> SELECT COUNT(*) FROM users;
+----------+
| count(*) |
+----------+
|        2 |
+----------+
1 row

ferrisdb> .exit
Bye.

$ # 重啟資料庫

ferrisdb> SELECT * FROM users;
+----+-------+-----+
| id | name  | age |
+----+-------+-----+
|  1 | Alice |  30 |
|  2 | Bob   |  25 |
+----+-------+-----+
2 rows
```

Carol 在 ROLLBACK 後消失，重啟後只有 2 筆資料——WAL + crash recovery + MVCC 全部在作用。

---

## 任務規格

### 模組一：儲存層（接練習 A 或 B）

**目標**：一個實作 `StorageEngine` trait 的 KV store，資料持久化到磁碟。

交付物：
- `StorageEngine` trait 實作（見參考解答中的 trait 定義）
- 支援 `get(key) -> Option<Vec<u8>>`、`put(key, value)`、`delete(key)`、`scan(start..end) -> Iterator`
- 資料在程序重啟後還在

驗收：
- 寫入 10,000 筆 KV，重啟後能全部查到
- 範圍掃描傳回的 key 按升序排列
- 刪除的 key 重啟後也不復存在

選 B-tree 引擎的注意事項：頁面大小固定 4KB，葉節點滿了要 split，內部節點也要 split，不要忘記根節點的 split 是特例（高度增加）。

選 LSM 引擎的注意事項：MemTable 超過 64MB 時 flush 到磁碟變 SSTable，Level 0 超過 4 個 SSTable 時觸發 minor compaction。

---

### 模組二：WAL + Recovery + MVCC（接練習 C）

**目標**：在儲存層上加 WAL 保護和 MVCC 交易。

交付物：
- WAL writer：每次寫入先寫 WAL，再寫儲存層
- Recovery：啟動時掃描 WAL，重放未 checkpoint 的操作
- MVCC：每個版本帶時間戳（txn_id），讀者根據 snapshot_ts 決定可見性
- `begin_txn()`, `commit_txn(txn_id)`, `abort_txn(txn_id)` 介面

驗收：
- 正常關機重啟：資料全在
- 模擬 crash（`kill -9` 或程式中途 `process::exit`）後重啟：已 commit 的交易全在，未 commit 的全消失
- 並發兩個交易寫同一 key：後者看不到前者未 commit 的修改（Read Committed 語意）

---

### 模組三：SQL 引擎（接練習 D）

**目標**：SQL 字串 → AST → logical plan → physical plan → 執行結果。

交付物：
- **Parser**：支援 CREATE TABLE, INSERT, SELECT（含 WHERE、ORDER BY、LIMIT）、BEGIN/COMMIT/ROLLBACK、簡單 JOIN（選做）
- **Binder/Analyzer**：把 table name、column name 綁定到 catalog，型別推斷
- **Logical Planner**：SQL AST → 關聯代數（Scan, Filter, Project, Sort, Limit, Join）
- **Physical Planner**：logical plan → physical plan（選執行算法）
- **Executor**：Volcano 模型 iterator，每個 operator 實作 `next() -> Option<Row>`
- **Catalog**：in-memory + 持久化（儲存 schema 到磁碟）

最低可行的 SQL 子集：
```sql
CREATE TABLE t (col1 TYPE, col2 TYPE);
INSERT INTO t VALUES (v1, v2);
SELECT [*|col,...] FROM t [WHERE expr] [ORDER BY col] [LIMIT n];
BEGIN; COMMIT; ROLLBACK;
```

驗收：
- 上面 demo 的所有 SQL 都能正確執行
- WHERE 過濾結果正確（含 AND/OR/NOT）
- ORDER BY 結果按正確方向排序
- 交易語意：ROLLBACK 後資料消失，COMMIT 後重啟還在

---

### 模組四：REPL / CLI

**目標**：一個能用的命令列介面。

交付物：
- 接受 stdin 的 SQL 輸入，多行 SQL 以 `;` 結束
- 輸出格式化的表格（對齊列寬）
- 特殊指令：`.exit`（退出）、`.tables`（列出所有表）、`.schema t`（顯示表結構）
- 錯誤訊息要說清楚問題在哪，不要只說「syntax error」

---

### 模組五：整合測試

**目標**：端到端的自動化測試，不依賴 REPL 手動操作。

交付物：
- 測試：建表 → 插入 → 查詢 → 確認結果
- 測試：交易 COMMIT 後重啟確認持久
- 測試：交易 ROLLBACK 後資料不見
- 測試：crash 模擬（用 `process::abort`）後重啟確認 recover 正確
- 測試：並發兩個執行緒同時寫入，不 data race（Rust 的 borrow checker 通常會幫你）

---

## 如果你卡住了

**模組一 B-tree split 搞不定**：先把樹高固定為 1（只有根節點是葉節點），確認插入和查詢都正確；再加 split，一次只加一層。把「插入後 invariant（所有 key 有序、每個節點有正確的 parent 指標）」寫成驗證函式，每次操作後呼叫它。

**WAL recovery 邊界情況**：用最簡單的策略——記錄只有兩種：`BeginTxn(txn_id)` 和 `CommitTxn(txn_id)` 和 `Write(txn_id, key, value)`。Recovery 時只重放 `CommitTxn` 有出現的 txn_id 的寫入。

**SQL Parser 從哪裡開始**：先把 Token 的 `enum` 定義好，再寫一個 `struct Lexer` 用 `chars().peekable()` 掃字元流。Parser 用 recursive descent，每個 SQL 語句是一個函式（`parse_select`、`parse_insert`）。SELECT 的 WHERE expression 用 Pratt parsing 或直接 recursive descent 都行。

**Executor 不知道從哪串**：先讓 `SeqScan` 能跑（從 StorageEngine 讀所有 row），再加 `Filter`（在 `SeqScan` 外面包一層），再加 `Project`（只留指定欄位）。這三個加起來就能跑 `SELECT col FROM t WHERE cond`。

**REPL 輸出表格麻煩**：先確定每一列的字串長度，取最大值作為欄寬，用 `format!("{:<width$}", val, width=col_width)` 對齊。

---

## 分段實作建議

這個專案不要從頭線性寫，用以下順序：

1. **建 crate 結構**（1天）：`cargo new ferrisdb`，把模組骨架建好，確保 `cargo check` 通過。
2. **StorageEngine trait + B-tree 引擎**（3–5天）：先讓資料能存取。
3. **WAL + recovery**（2–3天）：加到儲存引擎上，跑 crash 測試。
4. **MVCC**（2–3天）：在 WAL 層上加版本。
5. **SQL Parser + Catalog**（3–4天）：能把 SQL 字串變成 AST。
6. **Logical + Physical Planner + Executor**（4–6天）：能跑最基本的 SELECT。
7. **REPL**（1–2天）：把以上接起來，能在命令列互動。
8. **整合測試**（2天）：把所有正確性用測試寫死。

總計 18–26天，全職每天 3–5小時。如果你跳過不感興趣的部分，最小可行路徑是：SQLite 的 `rusqlite` crate 做儲存層 + 自己寫 parser + 自己寫 executor，約 10天能跑起來基本功能。

---

## 完整參考解答

**寫完再看。** 如果你還沒試過模組一的儲存層，現在就去寫；如果你已經卡了超過一天，才值得展開下面的提示。

<details>
<summary>點開參考架構與關鍵介面</summary>

### Crate / Module 組織

```
ferrisdb/
├── Cargo.toml
└── src/
    ├── main.rs              # REPL 入口
    ├── storage/
    │   ├── mod.rs           # StorageEngine trait
    │   ├── btree/
    │   │   ├── mod.rs
    │   │   ├── page.rs      # 頁面結構
    │   │   ├── node.rs      # 葉節點 / 內部節點
    │   │   └── tree.rs      # B+tree 操作
    │   └── lsm/
    │       ├── mod.rs
    │       ├── memtable.rs
    │       ├── sstable.rs
    │       └── compaction.rs
    ├── wal/
    │   ├── mod.rs           # WAL writer / reader
    │   └── recovery.rs
    ├── mvcc/
    │   └── mod.rs           # MVCC 版本管理
    ├── sql/
    │   ├── mod.rs
    │   ├── lexer.rs         # Tokenizer
    │   ├── parser.rs        # Recursive descent parser
    │   ├── ast.rs           # AST 節點定義
    │   ├── binder.rs        # Name resolution
    │   ├── planner.rs       # Logical + physical plan
    │   └── executor/
    │       ├── mod.rs       # Executor trait
    │       ├── seq_scan.rs
    │       ├── filter.rs
    │       ├── project.rs
    │       ├── sort.rs
    │       └── insert.rs
    ├── catalog/
    │   └── mod.rs           # Schema 管理
    └── error.rs             # 統一錯誤型別
```

### 關鍵 Trait 定義

以下骨架已通過 `cargo check`（未編譯驗證：Rust 型別正確，執行時行為需實作後驗證）。

```rust
// src/error.rs
use std::fmt;

#[derive(Debug)]
pub enum DbError {
    Io(std::io::Error),
    Parse(String),
    Execution(String),
    TransactionConflict,
    NotFound(String),
}

impl fmt::Display for DbError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            DbError::Io(e) => write!(f, "IO error: {}", e),
            DbError::Parse(s) => write!(f, "Parse error: {}", s),
            DbError::Execution(s) => write!(f, "Execution error: {}", s),
            DbError::TransactionConflict => write!(f, "Transaction conflict"),
            DbError::NotFound(s) => write!(f, "Not found: {}", s),
        }
    }
}

impl From<std::io::Error> for DbError {
    fn from(e: std::io::Error) -> Self {
        DbError::Io(e)
    }
}

pub type Result<T> = std::result::Result<T, DbError>;
```

```rust
// src/storage/mod.rs
use crate::error::Result;
use std::ops::RangeBounds;

/// 所有儲存引擎實作這個 trait。
/// Key 和 Value 都是 bytes，型別語意由上層（MVCC/Catalog）負責。
pub trait StorageEngine: Send + Sync {
    fn get(&self, key: &[u8]) -> Result<Option<Vec<u8>>>;
    fn put(&mut self, key: &[u8], value: &[u8]) -> Result<()>;
    fn delete(&mut self, key: &[u8]) -> Result<()>;

    /// 按 key 升序掃描一個範圍，傳回 (key, value) 迭代器。
    fn scan<R: RangeBounds<Vec<u8>>>(
        &self,
        range: R,
    ) -> Result<Box<dyn Iterator<Item = Result<(Vec<u8>, Vec<u8>)>> + '_>>;

    /// 把所有未寫入磁碟的資料刷到磁碟。
    fn flush(&mut self) -> Result<()>;
}
```

```rust
// src/mvcc/mod.rs
use crate::error::Result;
use crate::storage::StorageEngine;

pub type TxnId = u64;

/// MVCC 層，包裝一個 StorageEngine，提供帶版本的讀寫。
pub struct MvccEngine<S: StorageEngine> {
    inner: S,
    next_txn_id: TxnId,
    /// 目前還沒 commit 的 txn_id 集合，用於可見性判斷。
    active_txns: std::collections::BTreeSet<TxnId>,
}

impl<S: StorageEngine> MvccEngine<S> {
    pub fn new(inner: S) -> Self {
        Self {
            inner,
            next_txn_id: 1,
            active_txns: Default::default(),
        }
    }

    pub fn begin_txn(&mut self) -> TxnId {
        let txn_id = self.next_txn_id;
        self.next_txn_id += 1;
        self.active_txns.insert(txn_id);
        txn_id
    }

    pub fn commit_txn(&mut self, txn_id: TxnId) -> Result<()> {
        self.active_txns.remove(&txn_id);
        self.inner.flush()
    }

    pub fn abort_txn(&mut self, txn_id: TxnId) -> Result<()> {
        self.active_txns.remove(&txn_id);
        // 實際實作需要把這個 txn 的所有 write 從儲存層移除（或標記刪除）
        Ok(())
    }

    /// 讀取 key 在 snapshot_txn_id 時間點的可見版本。
    /// 可見性規則：version.txn_id <= snapshot_txn_id，且 txn_id 已 commit。
    pub fn get(&self, key: &[u8], snapshot_txn_id: TxnId) -> Result<Option<Vec<u8>>> {
        // MVCC key 格式：[user_key][txn_id (8 bytes big-endian)]
        // 掃描所有版本，找最新的可見版本
        let _ = (key, snapshot_txn_id); // 佔位，實作時移除
        Ok(None)
    }

    pub fn put(&mut self, key: &[u8], value: &[u8], txn_id: TxnId) -> Result<()> {
        let mut versioned_key = key.to_vec();
        versioned_key.extend_from_slice(&txn_id.to_be_bytes());
        self.inner.put(&versioned_key, value)
    }
}
```

```rust
// src/sql/ast.rs
/// SQL AST 節點定義（子集）

#[derive(Debug, Clone)]
pub enum Statement {
    CreateTable(CreateTableStmt),
    Insert(InsertStmt),
    Select(SelectStmt),
    BeginTxn,
    CommitTxn,
    RollbackTxn,
}

#[derive(Debug, Clone)]
pub struct CreateTableStmt {
    pub table_name: String,
    pub columns: Vec<ColumnDef>,
}

#[derive(Debug, Clone)]
pub struct ColumnDef {
    pub name: String,
    pub data_type: DataType,
}

#[derive(Debug, Clone)]
pub enum DataType {
    Integer,
    Text,
    Float,
    Boolean,
}

#[derive(Debug, Clone)]
pub struct InsertStmt {
    pub table_name: String,
    pub values: Vec<Value>,
}

#[derive(Debug, Clone)]
pub struct SelectStmt {
    pub columns: SelectColumns,
    pub from: String,
    pub where_clause: Option<Expr>,
    pub order_by: Option<OrderBy>,
    pub limit: Option<u64>,
}

#[derive(Debug, Clone)]
pub enum SelectColumns {
    All,
    Named(Vec<String>),
}

#[derive(Debug, Clone)]
pub struct OrderBy {
    pub column: String,
    pub ascending: bool,
}

#[derive(Debug, Clone)]
pub enum Expr {
    Column(String),
    Literal(Value),
    BinOp { op: BinOp, left: Box<Expr>, right: Box<Expr> },
    UnaryOp { op: UnaryOp, operand: Box<Expr> },
}

#[derive(Debug, Clone)]
pub enum BinOp { Eq, Ne, Lt, Le, Gt, Ge, And, Or }

#[derive(Debug, Clone)]
pub enum UnaryOp { Not }

#[derive(Debug, Clone, PartialEq)]
pub enum Value {
    Integer(i64),
    Float(f64),
    Text(String),
    Boolean(bool),
    Null,
}
```

```rust
// src/sql/executor/mod.rs
use crate::error::Result;
use crate::sql::ast::Value;

/// 一行資料，欄位順序對應 schema。
pub type Row = Vec<Value>;

/// Volcano 模型：每個 operator 實作 next()，
/// 上層 operator 反覆呼叫下層的 next() 拉資料。
pub trait Executor {
    /// 傳回下一行，None 代表結束。
    fn next(&mut self) -> Result<Option<Row>>;

    /// 這個 executor 輸出的欄位名稱（用於結果表頭）。
    fn column_names(&self) -> Vec<String>;
}

/// 簡單的結果收集器，把 executor 跑完並收集成 Vec<Row>。
pub fn collect(exec: &mut dyn Executor) -> Result<Vec<Row>> {
    let mut rows = Vec::new();
    while let Some(row) = exec.next()? {
        rows.push(row);
    }
    Ok(rows)
}
```

```rust
// src/catalog/mod.rs
use crate::sql::ast::{ColumnDef, DataType};
use crate::error::Result;
use std::collections::HashMap;

#[derive(Debug, Clone)]
pub struct TableSchema {
    pub name: String,
    pub columns: Vec<ColumnDef>,
}

impl TableSchema {
    pub fn column_index(&self, name: &str) -> Option<usize> {
        self.columns.iter().position(|c| c.name == name)
    }

    pub fn column_type(&self, name: &str) -> Option<&DataType> {
        self.columns.iter().find(|c| c.name == name).map(|c| &c.data_type)
    }
}

pub struct Catalog {
    tables: HashMap<String, TableSchema>,
}

impl Catalog {
    pub fn new() -> Self {
        Self { tables: HashMap::new() }
    }

    pub fn create_table(&mut self, schema: TableSchema) -> Result<()> {
        self.tables.insert(schema.name.clone(), schema);
        Ok(())
    }

    pub fn get_table(&self, name: &str) -> Option<&TableSchema> {
        self.tables.get(name)
    }

    pub fn all_tables(&self) -> impl Iterator<Item = &TableSchema> {
        self.tables.values()
    }
}
```

### 端到端 Demo 預期輸出

執行以下整合測試，應該得到對應輸出（未編譯驗證：需實作完整後跑 `cargo test`）：

```rust
// tests/integration_test.rs

// 測試流程（虛擬碼，實際測試用你的 API）：
//
// 1. CREATE TABLE users (id INTEGER, name TEXT, age INTEGER)
//    → 傳回 Ok(())
//
// 2. INSERT INTO users VALUES (1, 'Alice', 30)
//    INSERT INTO users VALUES (2, 'Bob', 25)
//    → 各傳回 Ok(())
//
// 3. SELECT name, age FROM users WHERE age > 28
//    → [["Alice", "30"]]
//
// 4. BEGIN; INSERT INTO users VALUES (3, 'Carol', 35); ROLLBACK;
//    → SELECT COUNT(*) FROM users → [[2]]
//
// 5. 程序重啟（重建 MvccEngine + Catalog，執行 recovery）
//    SELECT * FROM users → [["1", "Alice", "30"], ["2", "Bob", "25"]]
//    Carol 不在
```

### WAL Record 格式建議

```rust
// src/wal/mod.rs

/// WAL 的每一條記錄。
/// 格式：[record_type: u8][txn_id: u64][key_len: u32][key: bytes][val_len: u32][val: bytes]
/// val_len = 0 代表 delete。
#[derive(Debug)]
pub enum WalRecord {
    Begin { txn_id: u64 },
    Write { txn_id: u64, key: Vec<u8>, value: Option<Vec<u8>> },
    Commit { txn_id: u64 },
    Abort { txn_id: u64 },
}
```

Recovery 邏輯：讀完所有 record，找出哪些 txn_id 有 Commit record，只重放這些 txn 的 Write record。

</details>

---

## 評分 Rubric

| 面向 | 滿分 | 評分標準 |
|---|---|---|
| 儲存正確性 | 25 | 資料重啟後還在；刪除的 key 不復存在；scan 順序正確 |
| 交易正確性 | 25 | ROLLBACK 後資料消失；crash 後 committed 資料還在；並發讀不見未 commit 的寫 |
| SQL 功能完整度 | 25 | 支援所有指定 SQL 語法；WHERE/ORDER BY/LIMIT 結果正確 |
| 程式碼品質 | 15 | trait 邊界清晰、模組分離、無 `.unwrap()` 在非測試路徑 |
| 測試涵蓋 | 10 | 整合測試涵蓋上述五個場景，每個測試有清楚的 assert |

---

## 延伸挑戰

**換儲存引擎重跑**：如果你用 B-tree 完成了，現在實作 LSM 引擎並讓它實作同一個 `StorageEngine` trait，SQL 層完全不用改。你會深刻感受到 trait 抽象的價值。

**Cost-Based Optimizer**：在 `Filter` 前加一個統計收集器，記錄每個欄位的最小值、最大值、distinct count。讓 planner 根據這些統計估算 filter 的選擇率（selectivity），當 selectivity < 10% 且有索引時選擇 index scan 而不是 seq scan。

**簡單 Replication**：讓 ferrisdb 能把 WAL 用 TCP socket 推給另一個 ferrisdb 實例，讓後者重放，達到 primary-replica 的非同步複製。這是 Ch 38 的具體化。

**並行查詢**：讓 SeqScan 能分段，多個執行緒分別掃一段，最後 merge——這是向量化執行的第一步，也是 DuckDB 的核心設計。

---

## 自我檢核

- [ ] `cargo test` 全綠，整合測試五個場景都通過。
- [ ] 我能在不看程式碼的情況下，口頭說明一條 INSERT SQL 從輸入到磁碟的完整路徑（parser → AST → executor → MVCC → WAL → StorageEngine → 磁碟）。
- [ ] 我能解釋 `StorageEngine` trait 讓我能在不改 SQL 層的情況下換儲存引擎。
- [ ] crash recovery 的整合測試確實跑了：程序中途終止後，已 commit 的資料在重啟後存在，未 commit 的不存在。
- [ ] 我知道 ferrisdb 和 SQLite 之間最大的差距是什麼（選一個你沒做到的面向說清楚）。

---

這門課從磁碟存取原理走到 SQL 查詢引擎，從一個 `put(key, value)` 走到一個能 ROLLBACK 的 MVCC 交易系統。你沒有在概念層面「了解資料庫」，你是逐行寫出來的。這就是理解和讀懂的差距。恭喜你完成這門課。
