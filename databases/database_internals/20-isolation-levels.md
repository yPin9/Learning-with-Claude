# Ch 20 — 隔離級別與異常現象

> **目標**：理解並發交易在弱隔離下會出現哪些具體的資料異常，SQL 標準四個隔離級別各自防哪些、放行哪些，以及 Snapshot Isolation（快照隔離）的陷阱。核心是那張「級別 × 異常」對應表——要能從記憶中默寫出來並解釋原因。

## 為什麼需要弱隔離？

完全的隔離（Serializable）在語意上等同「所有交易一個接一個串行執行」。這沒有並發，吞吐量極低。

現實資料庫的選擇：**用正確性換效能**。放行某些異常，換取更高的並發度。這就是隔離級別光譜存在的原因。

搞懂隔離級別，就是搞懂「放行哪些異常的代價是什麼」。

---

## 並發交易的七種異常現象

先把所有可能的異常搞清楚，再討論哪個級別防哪個。

### 1. Dirty Write（髒寫）

T1 和 T2 都修改同一個 object，T2 覆蓋了 T1 還沒提交的修改，然後 T1 abort。

```
T1: BEGIN
T2: BEGIN
T1: Write(x=10)         ← x 原本是 0
T2: Write(x=20)         ← 覆蓋了 T1 未提交的值
T1: ABORT               ← T1 abort，undo x to 0
result: x = 0           ← T2 的 Write(x=20) 消失了
```

T2 的修改在 T1 abort 後被連帶撤銷，這不是任何一個交易語意下合理的行為。**幾乎所有資料庫都防止 Dirty Write**，因為它會破壞 Atomicity 的保障。

---

### 2. Dirty Read（髒讀）

T2 讀到了 T1 **還沒提交**的修改。T1 之後 abort，T2 讀到的是一個從未「真正存在」的值。

```
T1: BEGIN
T1: Write(x=10)         ← x 原本是 0
T2: Read(x) → 10        ← 讀到 T1 未提交的值
T1: ABORT               ← T1 abort，x 回到 0
T2: 拿著 x=10 繼續運作  ← 資料根本不存在
```

---

### 3. Non-Repeatable Read（不可重複讀）

同一個交易內，兩次讀同一個 row，得到不同的值（因為兩次讀之間有其他交易提交了修改）。

```
T1: Read(x) → 10
T2: Write(x=20); COMMIT
T1: Read(x) → 20        ← 同一交易讀到不同值
```

---

### 4. Phantom Read（幻讀）

同一個交易內，兩次執行同樣的範圍查詢（如 `WHERE salary > 1000`），得到不同數量的 row（因為兩次查詢之間有其他交易 INSERT 或 DELETE 了符合條件的 row）。

```
T1: SELECT * FROM emp WHERE salary > 1000 → [Alice]
T2: INSERT INTO emp VALUES ('Bob', 1500); COMMIT
T1: SELECT * FROM emp WHERE salary > 1000 → [Alice, Bob]  ← 多出一列
```

Non-Repeatable Read 是已存在 row 的值改變；Phantom Read 是結果集的行數改變。

---

### 5. Lost Update（更新遺失）

兩個交易都先讀一個值、各自計算、再寫回，後寫的覆蓋了先寫的，第一個寫入「消失」。

```
T1: Read(counter=0)
T2: Read(counter=0)
T1: Write(counter=1)    ← counter + 1
T2: Write(counter=1)    ← counter + 1（但 T2 用了舊值 0，不是 T1 的 1）
result: counter = 1     ← 應該是 2
```

常見場景：網站計數器、庫存扣減、帳戶轉帳。

---

### 6. Write Skew（寫偏）

兩個交易各自讀一些資料，根據讀到的值做決定，各自寫入**不同的 object**，但合起來的結果違反了業務約束。

```
約束：醫院至少一名醫生值班

T1: Read(doctors_on_call) → 2   ← 還有 2 人，可以請假
T2: Read(doctors_on_call) → 2   ← 還有 2 人，可以請假
T1: Update(doctor_A.on_call = false)  ← doctor A 請假
T2: Update(doctor_B.on_call = false)  ← doctor B 請假
result: doctors_on_call = 0     ← 違反約束，但每個交易單獨看都是合法的
```

Write Skew 的特徵：每個交易寫的是不同 object，所以不構成 Dirty Write；也沒有哪個交易讀到未提交的值。但組合起來違反了跨 object 的不變量（invariant）。

---

### 7. Read Skew（讀偏，通常不單獨列出）

同一交易讀多個 object，讀到的是不同時間點的快照，違反了 object 間的不變量。

```
x + y = 100 的不變量

T1: Read(x=50)
T2: Write(x=0, y=150); COMMIT  ← 保持了 x+y=150（違反不變量的寫法其實是 bug，這裡是合法改動）

（實際 read skew 範例：）
x=50, y=50，某操作 T2 把 x 加 10，y 減 10

T1: Read(x=50)
T2: Write(x=60, y=40); COMMIT
T1: Read(y=40)                  ← T1 看到 x=50 + y=40 = 90，不等於 100
```

這在 Snapshot Isolation 下不會發生（整個交易看同一個快照），但在 Read Committed 下可能發生。

---

## SQL 標準四個隔離級別

SQL-92 標準定義了四個隔離級別，透過**允許哪些異常**來區分：

| 隔離級別 | Dirty Read | Non-Repeatable Read | Phantom Read |
|---|---|---|---|
| **Read Uncommitted** | 可能發生 | 可能發生 | 可能發生 |
| **Read Committed** | 不可能 | 可能發生 | 可能發生 |
| **Repeatable Read** | 不可能 | 不可能 | 可能發生 |
| **Serializable** | 不可能 | 不可能 | 不可能 |

這張表是 SQL 標準 Table 1，原文如此定義。注意：

- SQL 標準只列了三種異常（Dirty/Non-repeatable/Phantom），**沒有**提到 Write Skew 和 Lost Update。
- Dirty Write 被所有級別禁止（甚至比 Read Uncommitted 更基本的要求）。
- Snapshot Isolation 不在 SQL 標準內，但現代 DB 廣泛使用。

---

## 完整異常 × 隔離級別對應表

結合學術研究（Berenson et al. 1995 對 SQL-92 標準的批評）的更完整版本：

| 異常 | Read Uncommitted | Read Committed | Repeatable Read | Snapshot Isolation | Serializable |
|---|---|---|---|---|---|
| Dirty Write | 不可能 | 不可能 | 不可能 | 不可能 | 不可能 |
| Dirty Read | **可能** | 不可能 | 不可能 | 不可能 | 不可能 |
| Non-Repeatable Read | **可能** | **可能** | 不可能 | 不可能 | 不可能 |
| Phantom Read | **可能** | **可能** | **可能** | 不可能* | 不可能 |
| Read Skew | **可能** | **可能** | 不可能 | 不可能 | 不可能 |
| Lost Update | **可能** | **可能** | 不可能† | 不可能† | 不可能 |
| Write Skew | **可能** | **可能** | **可能** | **可能** | 不可能 |

*Postgres 的 Repeatable Read 實際上是 Snapshot Isolation，比 SQL 標準要求的強（防 Phantom）。

†Repeatable Read（用鎖實作）和 Snapshot Isolation 通常防 Lost Update（透過寫寫衝突偵測），但細節依實作而定。

**Write Skew 是 Snapshot Isolation 的已知缺陷**，只有 Serializable 能完全防止。

---

## 各級別的直覺理解

### Read Uncommitted
放行所有異常，只防 Dirty Write。幾乎沒有實用場景，因為讀到未提交的資料在任何業務邏輯下都是危險的。MySQL 支援但極少使用。

### Read Committed
最常用的預設級別（Oracle、PostgreSQL 預設）。每次讀操作都看到「最新已提交的資料」。

實作：
- 2PL 版本：讀 lock 在讀完立刻釋放（不持有到交易結束）
- MVCC 版本：每次 `SELECT` 取一個新的 snapshot（statement-level snapshot）

無法防 Non-Repeatable Read，因為兩次 `SELECT` 之間可能有其他交易提交。

### Repeatable Read
同一交易內，多次讀同一 row 結果相同。

實作：
- 2PL 版本：讀 lock 持有到交易結束（不提前釋放）
- MVCC 版本：整個交易看同一個 snapshot（transaction-level snapshot）

SQL 標準允許 Phantom Read，但 PostgreSQL 的 Repeatable Read 實際上是 Snapshot Isolation，連 Phantom Read 也防了。MySQL InnoDB 的 Repeatable Read 則用 Next-Key Lock 防 Phantom。

### Serializable
完全的隔離，等同串行執行。

實作：
- 2PL 版本：用 predicate lock（謂詞鎖）防 Phantom，所有 lock 持有到交易結束
- SSI（Serializable Snapshot Isolation）版本：Snapshot Isolation 加上衝突偵測，不用鎖（Ch 23 會討論）

---

## Snapshot Isolation（快照隔離）

Snapshot Isolation（SI）是 Repeatable Read 的一種常見實作，也是 PostgreSQL RR 和 Serializable 的底層基礎。

**核心思想**：交易開始時取一個「快照」（snapshot）—— 記錄哪些交易在此時已提交。整個交易的所有讀操作都看這個快照，不管其他交易在這之後提交了什麼。

```
Snapshot 的語意：

  T1 的 snapshot = { 所有在 T1 BEGIN 時已 COMMIT 的交易的版本 }

  之後提交的交易：T1 看不到
  T1 BEGIN 前提交的交易：T1 看得到（即使 T1 開始時那個交易已提交了很久）
```

SI 的寫入衝突處理（First Committer Wins）：

```
T1 和 T2 的 snapshot 相同（同時 BEGIN）。
T1 先 COMMIT，T2 試圖 COMMIT 時，
若 T2 的 write_set 與 T1 的 write_set 有重疊 → T2 abort。
```

這防止了 Lost Update：如果兩個交易都想寫同一個 row，後者會 abort。

### SI 防不了什麼：Write Skew

回到醫院值班的例子：

```
T1 和 T2 的 snapshot 都顯示：doctor_A.on_call=true, doctor_B.on_call=true（兩人都在班）

T1: Write(doctor_A.on_call = false)   ← 寫 A
T2: Write(doctor_B.on_call = false)   ← 寫 B

T1 先 COMMIT，T2 的 write_set = {doctor_B}，
T1 的 write_set = {doctor_A}，沒有重疊 → T2 也可以 COMMIT

結果：兩人都不在班，違反約束。
```

Write Skew 的根本原因：**讀的和寫的不是同一個 object**。SI 的衝突偵測只看寫寫重疊，看不到「你的讀依賴於我的寫」這種跨 object 的依賴。

要防 Write Skew，需要 Serializable（SSI 追蹤讀寫依賴）或應用層用 `SELECT FOR UPDATE`（讀時加寫 lock，強制序列化這條讀寫路徑）。

---

## 各資料庫的預設隔離級別

| 資料庫 | 預設級別 | 備注 |
|---|---|---|
| PostgreSQL | Read Committed | 可設定到 Serializable（SSI 實作） |
| MySQL InnoDB | Repeatable Read | 用 Next-Key Lock 防 Phantom |
| Oracle | Read Committed | Serializable 是 SI（非 true Serializable） |
| SQL Server | Read Committed | 支援 Read Committed Snapshot Isolation（RCSI） |
| SQLite | Serializable | WAL mode 下是 SI |
| CockroachDB | Serializable | 分散式 SI + SSI |

一個著名的陷阱：**Oracle 的 Serializable 其實是 Snapshot Isolation**，不是 SQL 標準意義的 Serializable，Write Skew 仍可能發生。

---

## 實際範例：隔離級別的影響

### Read Committed 的 Non-Repeatable Read

```sql
-- Session 1 (Read Committed)
BEGIN;
SELECT salary FROM emp WHERE id = 1;  -- → 10000

-- Session 2
BEGIN;
UPDATE emp SET salary = 20000 WHERE id = 1;
COMMIT;

-- Session 1 繼續
SELECT salary FROM emp WHERE id = 1;  -- → 20000 （值變了）
COMMIT;
```

如果 Session 1 在第一次讀後做了決策，第二次讀卻得到不同值，業務邏輯可能出錯。

### Repeatable Read 防 Non-Repeatable Read

```sql
-- Session 1 (Repeatable Read)
BEGIN;
SELECT salary FROM emp WHERE id = 1;  -- → 10000

-- Session 2 提交修改

-- Session 1
SELECT salary FROM emp WHERE id = 1;  -- → 10000 （還是舊值，snapshot 不更新）
COMMIT;
```

### SI 下的 Write Skew（在 Postgres RR 或 SI 下可重現）

```sql
-- 初始：doctor A 和 B 都 on_call

-- Session 1 (Repeatable Read)
BEGIN;
SELECT count(*) FROM doctors WHERE on_call = true;  -- → 2
UPDATE doctors SET on_call = false WHERE name = 'A';
COMMIT;

-- Session 2 (Repeatable Read, 與 Session 1 同時 BEGIN)
BEGIN;
SELECT count(*) FROM doctors WHERE on_call = true;  -- → 2 (snapshot)
UPDATE doctors SET on_call = false WHERE name = 'B';
COMMIT;  -- ← Postgres RR 允許此 COMMIT（寫的是不同 row）

-- 結果：兩人都不在班
```

---

## 用 Rust 描述隔離級別的語意（示意）

這段程式碼不是真實實作（MVCC 在 Ch 22），而是用型別系統表達隔離級別的意圖：

```rust
// 未編譯驗證——概念示意

/// 隔離級別設定
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum IsolationLevel {
    ReadUncommitted,
    ReadCommitted,
    RepeatableRead,
    Serializable,
}

/// 某個隔離級別下的讀行為
///
/// Read Committed：每次 SELECT 取最新的已提交版本
/// Repeatable Read：整個交易用同一個 snapshot
#[derive(Debug, Clone, Copy)]
pub enum ReadPolicy {
    /// 取「此刻」最新已提交版本（statement-level snapshot）
    LatestCommitted,
    /// 取「交易開始時」的快照（transaction-level snapshot）
    Snapshot { snapshot_ts: u64 },
    /// 連未提交的也讀（實務中不用）
    ReadUncommitted,
}

/// 某個隔離級別下的寫衝突處理
#[derive(Debug, Clone, Copy)]
pub enum WriteConflictPolicy {
    /// 後寫者 abort（First Committer Wins，SI 的做法）
    AbortLater,
    /// 用鎖序列化（2PL 的做法）
    LockBased,
}

impl IsolationLevel {
    pub fn read_policy(self, txn_snapshot_ts: u64) -> ReadPolicy {
        match self {
            IsolationLevel::ReadUncommitted => ReadPolicy::ReadUncommitted,
            IsolationLevel::ReadCommitted => ReadPolicy::LatestCommitted,
            IsolationLevel::RepeatableRead | IsolationLevel::Serializable => {
                ReadPolicy::Snapshot { snapshot_ts: txn_snapshot_ts }
            }
        }
    }

    /// 此級別是否需要偵測 write skew？
    pub fn needs_write_skew_detection(self) -> bool {
        self == IsolationLevel::Serializable
    }
}
```

---

## 如何選擇隔離級別

| 場景 | 建議級別 | 理由 |
|---|---|---|
| 簡單讀多寫少（報表、分析） | Read Committed | 夠用，效能好 |
| 批次更新、需要一致性快照（備份、ETL） | Repeatable Read | 防 Non-Repeatable Read |
| 涉及多 row 的業務不變量（庫存、轉帳） | Serializable 或手動 FOR UPDATE | 防 Write Skew |
| 高吞吐 OLTP，可接受偶爾 retry | Repeatable Read + 應用層衝突重試 | SI 效能好，衝突 abort 就 retry |

**應用層 workaround（當不用 Serializable 時）**：

```sql
-- 防 Lost Update：用 FOR UPDATE 鎖定讀取的 row
SELECT balance FROM accounts WHERE id = 1 FOR UPDATE;
-- 現在其他交易無法修改 id=1，直到我們 COMMIT

-- 防 Write Skew：對讀取的「guard row」加 lock
SELECT * FROM doctors WHERE on_call = true FOR UPDATE;
-- 現在其他交易修改 on_call 前要等我們 COMMIT
```

---

## 踩雷

### 1. 以為 Repeatable Read 防 Write Skew

這是最常見的誤解。Repeatable Read 只防「同一 row 被讀兩次值不同」，無法防「讀不同 row、寫不同 row 但組合起來違反約束」。

### 2. 以為 Postgres 的 RR 與 MySQL 的 RR 相同

Postgres RR 是 Snapshot Isolation（防 Phantom Read），MySQL InnoDB 的 RR 用 Next-Key Lock 也防 Phantom Read，但兩者的實作與行為邊界不同（如 Lost Update 的處理）。不要跨 DB 假設相同語意。

### 3. 忽略 Oracle 的 Serializable 陷阱

Oracle 文件說「Serializable isolation」，但實際上是 SI，Write Skew 可能發生。在需要真 Serializable 的系統上依賴 Oracle 的這個設定是個坑。

### 4. 隔離級別是 session/transaction 級別的，不是全域的

可以對同一個資料庫，不同 session 設定不同隔離級別。不要以為設了 RR 就整個 DB 都是 RR。

### 5. 弱隔離 + 長交易 = 看到很老的資料

Read Committed 每次 SELECT 取最新版本；但 Repeatable Read 的 snapshot 是交易開始時的。一個長達 1 小時的 RR 交易，讀到的是 1 小時前的資料狀態，可能讓業務決策錯誤。

---

## 進階延伸

**Serializable Snapshot Isolation（SSI）**：Cahill et al. 2008 提出，PostgreSQL 9.1 實作。在 SI 基礎上追蹤「反向依賴」（anti-dependency），偵測 Write Skew 並 abort 其中一個交易。效能接近 SI，但提供 Serializable 保證。Ch 23 詳述。

**MVCC 實作的細節**：每個 row 有多個版本（version），每個版本帶有創建它的交易 ID（`xmin`）和刪除它的交易 ID（`xmax`）。讀操作根據 snapshot 的 transaction ID 判斷哪個版本可見。Ch 22 會完整討論。

**Gray & Lamport 的正式定義**：Jim Gray 的原始論文（1976）把隔離級別定義為 lock 的持有粒度；Berenson et al. 1995 論文《A Critique of ANSI SQL Isolation Levels》則把 SI、Write Skew 納入討論，是本章的學術基礎。

---

## 本章重點整理

- 並發交易的七種異常：Dirty Write、Dirty Read、Non-Repeatable Read、Phantom Read、Lost Update、Write Skew（Read Skew）
- SQL 標準四級別與異常的對應：Read Uncommitted 只防 Dirty Write；Read Committed 加防 Dirty Read；Repeatable Read 加防 Non-Repeatable Read；Serializable 全防
- Snapshot Isolation 比 Repeatable Read 更強（防 Phantom 和 Lost Update），但**無法防 Write Skew**
- Postgres 預設 Read Committed；MySQL InnoDB 預設 Repeatable Read；Oracle 的 Serializable 實為 SI
- Write Skew 的應用層 workaround：`SELECT FOR UPDATE` 把讀的 row 也加進 lock 範圍

## 自我檢核

- [ ] 我能舉出 Write Skew 的具體例子，並解釋為什麼 SI 防不了
- [ ] 我能默寫「級別 × 異常」對應表（至少 SQL 標準的四級別 × 三異常）
- [ ] 我知道 Lost Update 的標準 workaround（FOR UPDATE）
- [ ] 我能解釋為什麼 Phantom Read 與 Non-Repeatable Read 是不同的問題
- [ ] 我能說出 Postgres RR 與 SQL 標準 RR 的差異

## 延伸閱讀

- **Berenson et al.《A Critique of ANSI SQL Isolation Levels》（1995）**——把 SQL-92 的三種異常擴展到 SI 和 Write Skew，是本章「完整異常表」的來源。必讀，很短（20 頁），圖例清晰。
- **《Designing Data-Intensive Applications》Ch 7（Transactions）**——DDIA 用故事講 Write Skew（醫院值班、訂票系統），是本章最好的直覺補充，建議讀 p.246–269。
- **CMU 15-445 Lecture 19（Concurrency Control Theory）**——從 conflict serializable 和 view serializable 的形式定義出發，是本章「為什麼 Write Skew 不好」的嚴格版本。
- **Cahill et al.《Serializable Isolation for Snapshot Databases》（2008）**——SSI 的原始論文，是 Ch 23 的預讀；理解 SI 的反向依賴如何被偵測。
- **PostgreSQL 文件：Transaction Isolation**（https://www.postgresql.org/docs/current/transaction-iso.html）——Postgres 如何實作 SSI、RR 和 RC 的說明，包括 Phantom Read 如何被防止，建議對照本章表格逐條驗證。

---

→ [Ch 21 並發控制（一）2PL](./21-concurrency-2pl.md)
