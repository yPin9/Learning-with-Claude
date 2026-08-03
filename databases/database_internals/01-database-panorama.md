# Ch 1 — 資料庫全景：一條 SELECT 的旅程

> **目標**：在動手寫任何程式碼之前，先把資料庫的分層架構看清楚。知道一條 SQL 從字串到磁碟結果的完整路徑，知道本課每個 Part 在哪一層插手，知道為什麼這些層不能合併。

---

## 我們要追蹤的那條 SQL

```sql
SELECT name FROM users WHERE age > 30 ORDER BY name LIMIT 10;
```

這條查詢毫不起眼，但它在資料庫引擎裡走的路絕對不短。我們用它貫穿整章，讓每一層都不是抽象概念。

---

## 分層全景圖

```
  使用者 / 應用程式
        │
        │  "SELECT name FROM users WHERE age > 30 ORDER BY name LIMIT 10;"
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Parser（語法解析器）                                                │
│  輸入：SQL 字串                                                      │
│  輸出：AST（抽象語法樹，Abstract Syntax Tree）                       │
│  任務：詞法切分 → 文法驗證 → 建立樹狀結構                           │
└──────────────────────────────────────────────────────────────────────┘
        │  AST
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Binder（名稱解析器 / 語義繫結器）                                   │
│  輸入：AST（含未解析的名稱）                                         │
│  輸出：Bound AST（每個識別字都知道自己指的是哪張表、哪個欄位）       │
│  任務：查系統目錄 (catalog)，把 "users"、"name"、"age" 繫結到        │
│        具體 schema 物件，型別推導，權限初步檢查                      │
└──────────────────────────────────────────────────────────────────────┘
        │  Bound AST
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Planner（邏輯計劃產生器）                                           │
│  輸入：Bound AST                                                     │
│  輸出：Logical Plan（關聯代數樹，relational algebra tree）           │
│  任務：把 SQL 語義翻譯成 Scan → Filter → Sort → Limit → Project     │
│        等邏輯運算子的樹，不考慮實作細節                              │
└──────────────────────────────────────────────────────────────────────┘
        │  Logical Plan
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Optimizer（查詢最佳化器）                                           │
│  輸入：Logical Plan                                                  │
│  輸出：Physical Plan（含演算法選擇的實體計劃）                       │
│  任務：決定用 index scan 還是 sequential scan、join 演算法           │
│        （hash join / merge join / nested loop）、謂詞下推、          │
│        基數估算（cardinality estimation），挑出預期成本最低的計劃    │
└──────────────────────────────────────────────────────────────────────┘
        │  Physical Plan
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Executor（執行引擎）                                                │
│  輸入：Physical Plan（一棵運算子樹）                                 │
│  輸出：結果列（rows / tuples）                                       │
│  任務：走訪計劃樹，拉動每個運算子產出資料。                          │
│        常見模型：Volcano/Iterator（每次呼叫 next() 拉一筆）、         │
│        Vectorized（一次拉一批 batch）、Compiled（JIT 產機器碼）      │
└──────────────────────────────────────────────────────────────────────┘
        │  Tuple 請求（next()）
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Access Method（存取方法層）                                         │
│  輸入：掃描請求（資料表 OID、索引 OID、謂詞）                        │
│  輸出：符合條件的 tuple                                              │
│  任務：負責「怎麼找資料」的演算法，包括 B+ 樹遍歷、heap file 掃描、  │
│        索引 lookup。這層知道資料的**邏輯組織**，但不直接碰磁碟      │
└──────────────────────────────────────────────────────────────────────┘
        │  Page 請求（page_id）
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Buffer Pool（緩衝池）                                               │
│  輸入：page_id                                                       │
│  輸出：記憶體中的 page 指標（固定住，pin）                           │
│  任務：管理記憶體中的 page 快取，決定哪些 page 留在記憶體、哪些驅逐  │
│        （LRU-K、CLOCK 等置換策略），dirty page 何時寫回，確保         │
│        WAL（Write-Ahead Log）先落盤再 evict                          │
└──────────────────────────────────────────────────────────────────────┘
        │  I/O 請求（邏輯 page → 檔案偏移）
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Storage Engine / Disk Manager（儲存引擎 / 磁碟管理器）             │
│  輸入：page_id                                                       │
│  輸出：raw bytes（讀），或把 bytes 寫到正確位置（寫）                │
│  任務：管理資料庫檔案的空間，page_id → (file, offset) 的映射，      │
│        空閒頁追蹤（free-list / bitmap），直接 I/O vs mmap 策略       │
└──────────────────────────────────────────────────────────────────────┘
        │  syscall（read/write/fsync）
        ▼
  作業系統 / 磁碟
```

---

## 每一層在做什麼：走過那條 SQL

### 1. Parser — 「這句話合不合法？」

SQL 字串進來是一段 `&str`，Parser 先做詞法分析（Lexical Analysis）把它切成 token 序列：

```
SELECT  name  FROM  users  WHERE  age  >  30  ORDER  BY  name  LIMIT  10  ;
```

接著語法分析（Syntax Analysis）驗證 token 序列符合 SQL 文法，建出 AST。本課在 **Part 1** 手刻 SQL parser，從正則到遞迴下降（recursive descent）。

以下是一個示意性的 AST 結構（Rust enum）：

```rust
// 示意，非最終實作
pub enum Expr {
    ColumnRef { table: Option<String>, column: String },
    Literal(Value),
    BinaryOp { op: BinOp, left: Box<Expr>, right: Box<Expr> },
}

pub struct SelectStmt {
    pub projections: Vec<Expr>,         // name
    pub from:        TableRef,          // users
    pub filter:      Option<Expr>,      // age > 30
    pub order_by:    Vec<OrderByExpr>,  // name ASC
    pub limit:       Option<u64>,       // 10
}
```

AST 不知道 `users` 是不是真的存在、`age` 是什麼型別，那是下一層的事。

---

### 2. Binder — 「這些名字指的是誰？」

Binder 查系統目錄（Catalog），把 AST 裡的裸字串繫結成具體物件：

- `users` → table_id = 42, 欄位清單 `[(0, "id", INT), (1, "name", TEXT), (2, "age", INT)]`
- `name` → column_id = 1，型別 TEXT
- `age` → column_id = 2，型別 INT
- `30` → literal，型別 INT，與 `age` 型別相容，謂詞合法

Binder 輸出的 Bound AST 裡每個識別字都帶著 table_id + column_id，後面的層不再需要字串比對。Catalog 本身也是持久化資料，本課在 **Part 2** 處理。

---

### 3. Planner — 「用哪些邏輯運算子？」

Planner 把 SQL 語義機械地翻成關聯代數（Relational Algebra）樹：

```
Limit(10)
  └─ Sort(name ASC)
       └─ Project(name)
            └─ Filter(age > 30)
                 └─ Scan(users)
```

這棵樹是**邏輯計劃（Logical Plan）**，只描述「要做什麼」，不說明「怎麼做」。Scan 不指定是 index scan 還是 sequential scan；Sort 不說是外部排序還是記憶體排序。本課在 **Part 3** 實作 Planner。

---

### 4. Optimizer — 「哪條路最快？」

Optimizer 是整個資料庫裡最複雜的子系統之一，它拿 Logical Plan 做等價變換：

- **謂詞下推（Predicate Pushdown）**：`Filter` 盡量往 `Scan` 靠，減少往上傳的資料量。
- **索引選擇**：`users.age` 有索引嗎？B+ 樹 range scan 比 sequential scan + filter 快多少？估算要掃多少筆。
- **基數估算（Cardinality Estimation）**：`age > 30` 大約會過濾掉幾成資料？這影響後續算法選擇。

輸出是 Physical Plan，每個邏輯運算子換成具體算法：

```
Limit(10)
  └─ ExternalMergeSort(name ASC)      ← 若資料量大
       └─ IndexScan(users.age_idx, age > 30, project: name)
```

本課 **Part 3** 實作 rule-based optimizer，不做 cost-based（那需要統計資訊基礎設施，屬於進階）。

---

### 5. Executor — 「拉資料！」

Executor 走訪 Physical Plan 樹，本課採用 Volcano（迭代器）模型：每個運算子都實作一個 `next() -> Option<Tuple>` 介面，上層運算子呼叫下層的 `next()` 拉一筆，層層往下傳遞請求：

```
Limit.next()
  → ExternalMergeSort.next()
      → IndexScan.next()   ← 這裡才真正去存取資料
```

Volcano 模型容易理解和實作，但有 function call overhead。Vectorized（批次）模型和 Compiled（JIT 產機器碼）是現代 OLAP 引擎的做法，本課不做，但在 **Part 4** 說明差異。

---

### 6. Access Method — 「用什麼資料結構找？」

本課的核心結構是 **B+ 樹（B+ Tree）**。對這條 SQL：

- 若 `age` 有索引：走 B+ 樹 range scan，找 `age > 30` 的所有 leaf 節點，回傳對應 page_id + slot。
- 若無索引：走 heap file sequential scan，一頁一頁讀、逐筆比較。

Access Method 以 page 為單位和 Buffer Pool 溝通，它說「給我 page 42」，Buffer Pool 負責找。**Part 2** 建 heap file 和 slot 管理，**Part 5** 建 B+ 樹。

---

### 7. Buffer Pool — 「記憶體裡有嗎？」

Buffer Pool 是資料庫的記憶體管理核心，一般直接呼叫 OS 的 `mmap` 是不夠的，原因在後面「踩雷」區說明。Buffer Pool 做的事：

- 維護一個 frame 陣列（`Vec<Page>`），每個 frame 可放一個 page。
- `fetch_page(page_id)` → 若 page 在 frame 裡，直接回傳；否則從磁碟讀進來，必要時先驅逐一個 frame（LRU-K / CLOCK 策略）。
- 追蹤每個 frame 的 **pin count**（有幾個執行緒正在用）、**dirty bit**（有沒有被修改）。
- Dirty page 在驅逐前必須先確認對應的 WAL 記錄已落盤（WAL 在 **Part 6** 處理）。

**Part 2** 實作 Buffer Pool 和置換策略。

---

### 8. Disk Manager — 「page 在哪個 byte 偏移？」

最底層。維護 `page_id → (file_path, offset)` 的映射，管理空閒 page 的 free-list，實際呼叫 `pread` / `pwrite`。

```
page_id 42 → database.db, offset = 42 * 4096
```

本課固定 page 大小為 4096 bytes（一個 OS page），**Part 2** 處理這層。

---

## 每層對應本課哪個 Part

| 層次              | 本課 Part       | 關鍵主題                                      |
|-------------------|-----------------|-----------------------------------------------|
| Disk Manager      | Part 2          | 檔案格式、page layout、空閒頁管理             |
| Buffer Pool       | Part 2          | frame 管理、LRU-K、pin/unpin                  |
| Access Method     | Part 2, Part 5  | Heap file, Slot page, B+ 樹 CRUD              |
| Executor          | Part 3, Part 4  | Volcano model, 運算子實作                     |
| Planner           | Part 3          | SQL → 關聯代數樹                              |
| Optimizer         | Part 3          | Rule-based 最佳化、謂詞下推                   |
| Binder            | Part 1, Part 2  | Catalog schema, 名稱解析                      |
| Parser            | Part 1          | Tokenizer, recursive descent parser           |
| 交易 / WAL        | Part 6          | ACID, MVCC, crash recovery                    |
| 並行控制          | Part 6          | 2PL, deadlock detection                       |

---

## 為什麼要分這麼多層？

這不是過度設計。每一層的分離都有具體理由：

**關注點分離（Separation of Concerns）**：Parser 不需要知道磁碟格式；Buffer Pool 不需要懂 SQL。各層的工程師可以獨立作業、獨立測試。

**可替換性**：SQLite 有三種 journal mode（DELETE / WAL / MEMORY），切換的是 Storage 層；PostgreSQL 允許插拔不同的 access method（heap, BRIN index, hash index）。這些替換之所以可行，是因為層與層之間有明確的介面。

**優化介入點**：Optimizer 能在 Physical Plan 層面選演算法，正是因為 Logical Plan 只描述意圖。如果 SQL 直接翻成執行碼，就沒有空間做等價變換。

**可觀測性**：`EXPLAIN` / `EXPLAIN ANALYZE` 輸出的就是 Physical Plan，你能看到每個運算子的預估成本和實際耗時。分層讓這種剖析成為可能。

---

## 和 SQLite 架構的對比

SQLite 是學習資料庫內部最好的參照之一，因為它的實作極為精簡（約 15 萬行 C）。

```
SQLite 架構

  SQL 介面
      │
  Tokenizer + Parser   ← 和我們的 Part 1 對應
      │
  Code Generator       ← 把 AST 直接編譯成 bytecode（沒有顯式 Binder/Planner/Optimizer）
      │
  Virtual Machine      ← 執行 bytecode（對應 Executor）
      │
  B-Tree               ← 同時是 access method 和 storage format（和我們分開的設計不同）
      │
  Pager                ← 對應我們的 Buffer Pool + Disk Manager
      │
  OS Interface (VFS)   ← 抽象層，讓 SQLite 跨平台（Windows / Linux / WASM）
      │
  檔案系統
```

SQLite 最大的設計選擇是把 **整個資料庫塞進一個檔案**，並且 B-Tree 層同時管邏輯結構和實體儲存，沒有獨立的 heap file。這讓它極易嵌入，但限制了它做複雜查詢優化的空間（SQLite 的 optimizer 相對簡單）。

我們的設計更接近 PostgreSQL 的分層哲學：heap file 和 index 是分開的結構，Buffer Pool 是獨立模組，Optimizer 有自己的 plan tree。這樣寫起來複雜度更高，但每一層都是可以獨立研究的教材。

---

## 踩雷：常見錯誤直覺

**錯誤直覺 1：「直接用 `mmap` 就好，OS 會幫我管 page cache」**

正確認識：`mmap` 讓 OS 的 page replacement 策略決定哪些 page 留記憶體，但 OS 不知道哪些 page 正在被 transaction 用、dirty page 的 WAL 保證、eviction 的順序。Buffer Pool 存在的意義就是把這個控制權拿回來。PostgreSQL 的開發者多次討論並拒絕全面 mmap 的方案，原因正在此。

**錯誤直覺 2：「Parser 驗完語法就知道查詢能不能執行」**

正確認識：Parser 只看結構合不合文法。`SELECT xyz FROM nonexistent_table WHERE 1 = 1` 對 Parser 完全合法，要等 Binder 查 Catalog 才知道 `nonexistent_table` 不存在。型別錯誤（把 TEXT 欄位和整數比較）也是 Binder 層才報。

**錯誤直覺 3：「Optimizer 一定會找到最好的計劃」**

正確認識：Optimizer 是在有限的搜尋空間和統計資訊精度下做決策，基數估算本身就是估計，多表 join 的計劃空間是 join 數量的指數。Cost-based optimizer 可以選出比直覺差的計劃，所以 PostgreSQL 提供 `SET enable_seqscan = off` 這類旋鈕讓使用者強制干預。

**錯誤直覺 4：「B+ 樹只是教科書結構，實務上都用其他東西」**

正確認識：B+ 樹是硬碟友好的資料結構，節點大小對齊 page size，range scan 走 leaf linked list 不需要回到根，是關聯式資料庫 index 最主流的選擇。MySQL InnoDB、PostgreSQL、SQLite 的核心 index 結構全是 B+ 樹（或 B-Tree 的變體）。LSM Tree 在寫入密集場景（LevelDB、RocksDB）更好，但 range scan 成本較高。

**錯誤直覺 5：「Transaction 是在 SQL 層處理的，和存取層無關」**

正確認識：Transaction 的影響滲透到每一層。Buffer Pool 要處理 dirty page 的 WAL 順序；Access Method 的 B+ 樹修改要做 latch；Executor 拿到的 tuple 可能因 MVCC（多版本並行控制，Multi-Version Concurrency Control）而需要做可見性判斷。分層不代表層與層之間沒有橫切（cross-cutting）的語義，Transaction 就是最明顯的橫切關切點（cross-cutting concern）。

---

## 本章重點整理

- 一條 SQL 從字串到結果，必須經過 Parser → Binder → Planner → Optimizer → Executor → Access Method → Buffer Pool → Disk Manager 八層。
- 每層有明確的輸入型別和輸出型別：字串 → AST → Bound AST → Logical Plan → Physical Plan → Tuples → Pages → Bytes。
- Binder 才是語義層，Parser 只管語法；這兩件事的分工不能搞混。
- Optimizer 做等價變換但不保證最優，基數估算是它最大的不確定性來源。
- Buffer Pool 存在是為了把 page replacement 控制權從 OS 手中拿回來，並配合 WAL 保證 crash safety。
- SQLite 把 B-Tree 和 Pager 緊耦合，我們的設計把它們分開，更接近 PostgreSQL 的風格。
- Transaction 是橫切關切點，它的語義在每一層都有影響。

---

## 自我檢核

主動回憶，不要回頭看：

- [ ] 不看圖，能默背 Parser / Binder / Planner / Optimizer / Executor / Access Method / Buffer Pool / Disk Manager 的輸入和輸出各是什麼？
- [ ] 能解釋 Logical Plan 和 Physical Plan 的差異，並舉一個具體例子說明 Optimizer 做了什麼決定？
- [ ] 能說出 Buffer Pool 為什麼不能直接用 `mmap` 替代？
- [ ] 能指出 `SELECT name FROM nonexistent` 在哪一層報錯，以及為什麼不是 Parser 報？
- [ ] 能畫出 SQLite 的層次，並說明它和本課設計的最大結構差異？

---

## 延伸閱讀

1. **《Architecture of a Database System》— Hellerstein, Stonebraker, Hamilton（2007）**
   這是理解現代 RDBMS 架構最有密度的論文，免費 PDF 可取得。直接看 Section 2（SQL Parser / Rewriter / Optimizer）和 Section 4（Buffer Management），對應本章的每一層都有深度展開。閱讀目標：理解 PostgreSQL 架構決策背後的歷史。

2. **《CMU 15-445 Database Systems》課程講義（Andy Pavlo）**
   每個 lecture 對應一層，Lecture 3–6 處理 Storage + Buffer Pool，Lecture 11–12 處理 Query Optimization。配合課程的 BusTub 作業，是學本課架構最直接的補充材料。閱讀目標：用 C++ 版的類似架構驗證本課的 Rust 設計。

3. **SQLite 原始碼中的 `btree.c` 和 `pager.c`**
   `pager.c` 大約 7000 行，實作 Page Cache + WAL；`btree.c` 大約 10000 行，實作 B-Tree。兩個檔案在本章說到的 Buffer Pool 和 Access Method 兩層各有一個完整的、可直接讀懂的 C 實作。閱讀目標：在動手寫 Rust 版之前，先從這兩個檔案感受各層的 API 邊界。

4. **《Designing Data-Intensive Applications》Chapter 3 — Martin Kleppmann**
   用工程師能讀懂的語言比較 B-Tree 和 LSM Tree 的取捨：寫放大（write amplification）、讀放大（read amplification）、空間放大，以及各自適合的 workload。閱讀目標：理解我們為什麼選 B+ 樹，以及什麼情境下 LSM 會贏。

5. **PostgreSQL 原始碼 `src/backend/storage/buffer/bufmgr.c`**
   PostgreSQL 的 Buffer Pool 實作，約 4000 行 C，可以看到 LRU-Clock（「clock sweep」）替換策略、pin/unpin 計數、shared buffer 的 latch 設計。閱讀目標：本課的 Buffer Pool 實作完成後，對照這份代碼看工業級版本多了哪些考量。

---

這章是地圖，後面每個 Part 都是在地圖上的某個區域深挖。帶著這張全景圖繼續，你在寫 Buffer Pool 的 `fetch_page` 時就不會忘記它在整個系統裡的位置。

→ [下一章](./02-storage-fundamentals.md)
