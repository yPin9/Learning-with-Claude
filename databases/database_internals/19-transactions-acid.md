# Ch 19 — 交易與 ACID

> **目標**：理解「交易」不只是一個語法糖，而是四個獨立的工程保證各自需要不同機制來實現。搞清楚 ACID 的每一個字母對應資料庫哪個組件，以及為什麼現實中每一個保證都可能被妥協。用 Rust 實作交易的狀態機。

## 為什麼需要交易？

設想一個銀行轉帳：

```sql
UPDATE accounts SET balance = balance - 100 WHERE id = 1;
UPDATE accounts SET balance = balance + 100 WHERE id = 2;
```

這兩個操作必須全部成功或全部失敗。如果第一個執行完、第二個之前 crash，100 元就消失了。

再設想兩個並發操作：A 讀帳戶餘額，B 同時扣款。A 讀到的餘額可能是扣款前也可能是扣款後，取決於 CPU 排程——這種不確定性在單線程程式裡不存在，但在資料庫中是常態。

交易（Transaction）給了這個混亂的世界一個契約：**交易內的操作，對外看起來是一個不可分割的整體**。

---

## ACID：四個字母，四個獨立保證

### A — Atomicity（原子性）

**定義**：交易的所有操作，要麼全部完成，要麼全部不做。沒有中間狀態。

**對應實作**：**Undo log**。

如果交易中途失敗（Abort 或 crash），資料庫用 undo log 把已執行的修改回滾。Ch 17 的 WAL 記錄了 before image，就是為了這個目的。

```
原子性保證的場景：

正常 Commit：
  BEGIN → op1 → op2 → op3 → COMMIT
  結果：op1 + op2 + op3 全部生效

Abort（應用層呼叫 ROLLBACK）：
  BEGIN → op1 → op2 → ROLLBACK
  結果：op1 op2 全部撤銷，像從來沒發生

Crash（op2 執行完、op3 還沒）：
  BEGIN → op1 → op2 → [crash]
  Recovery：Undo op2 → Undo op1 → 像從來沒發生
```

注意：Atomicity 只保證「全有或全無」，不保證 Isolation（中間步驟是否對其他人可見）。

---

### C — Consistency（一致性）

**定義**：交易把資料庫從一個合法狀態帶到另一個合法狀態。合法的定義由使用者定義的約束（constraint）決定。

**對應實作**：**約束檢查（constraint checking）**，主要是應用層的邏輯。

這是 ACID 四個字母中最「軟」的一個：

- NOT NULL、UNIQUE、FOREIGN KEY 是資料庫層面的約束，違反時交易會自動 abort
- 業務邏輯的一致性（帳戶餘額不能為負）要應用層自己寫 `CHECK` 約束或程式碼來保證
- 資料庫本身無法知道「什麼是業務上合法的狀態」

一個重要觀點：Consistency 是由 AID 三個性質推導出來的——只要 Atomicity、Isolation、Durability 都做到了，且應用層邏輯正確，Consistency 自然成立。所以 C 是目標，AID 是手段。

---

### I — Isolation（隔離性）

**定義**：並發執行的多個交易，對彼此的影響是不可見的——就像它們是序列執行的一樣。

**對應實作**：**並發控制（concurrency control）**，包括 2PL（Ch 21）和 MVCC（Ch 22）。

Isolation 是 ACID 中實作最複雜、妥協最多的一個。完全的隔離性（Serializable）代價太高，大多數資料庫預設比 Serializable 弱的隔離級別。Ch 20 會詳細討論隔離級別的光譜。

---

### D — Durability（持久性）

**定義**：已提交的交易，其修改必須永久保存，即使之後系統 crash。

**對應實作**：**WAL + fsync**。

Ch 17 說清楚了：COMMIT record 落盤（fsync）= 這個交易的修改永遠存在。任何後續的 crash 都能透過 ARIES Recovery 把它重播出來。

---

## 直覺圖：ACID 與資料庫組件的對應

```
 ACID 性質          對應組件                  本課章節

 Atomicity    ←──  Undo Log / WAL           Ch 17-18
 Consistency  ←──  約束檢查 + 應用層邏輯     （SQL DDL）
 Isolation    ←──  2PL / MVCC               Ch 21-22
 Durability   ←──  WAL + fsync              Ch 17-18
```

這個圖說明了一件事：ACID 不是一個系統，是四個系統的組合。其中 Atomicity 和 Durability 共享 WAL 機制，Isolation 是獨立的並發控制子系統。

---

## 交易的生命週期

一個交易有幾個狀態：

```
                  操作執行中
     BEGIN ──────────────────→ ACTIVE
       │                          │
       │                          │ op 失敗 / 應用層 ROLLBACK
       │                          ▼
       │                      ABORTING ──→ undo log 回滾 ──→ ABORTED
       │                                                         │
       │                                                         ▼
       │                          │                          （結束）
       │                          │ 所有 op 成功，應用層 COMMIT
       │                          ▼
       │                      COMMITTING ──→ WAL fsync ──→ COMMITTED
       │                                                         │
       │                                                         ▼
       └──────────────────────────────────────────────────（結束）
```

關鍵轉換點：

- `ACTIVE → ABORTING`：任何一個操作失敗，或應用層呼叫 `ROLLBACK`
- `ABORTING → ABORTED`：undo log 全部回滾完成
- `ACTIVE → COMMITTING`：應用層呼叫 `COMMIT`，所有操作都成功
- `COMMITTING → COMMITTED`：COMMIT record 的 fsync 成功

`COMMITTING → COMMITTED` 這一步才是真正的「提交」。在 fsync 完成之前，交易雖然邏輯上已完成，但不具備持久性保障。

---

## Rust 實作：交易狀態機

```rust
// src/transaction.rs
use std::collections::HashMap;

/// 交易 ID，全域唯一遞增
pub type TxnId = u64;

/// LSN（從 WAL 取得）
pub type Lsn = u64;

/// 交易狀態
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TxnStatus {
    Active,
    Committing,
    Committed,
    Aborting,
    Aborted,
}

/// 單一交易的快照：記錄該交易所做的修改，方便 Rollback
#[derive(Debug)]
pub struct WriteSetEntry {
    pub page_id: u64,
    pub offset: usize,
    pub before_image: Vec<u8>,
    pub lsn: Lsn, // 對應 WAL 中的 LSN，Rollback 時用
}

/// 一個交易的執行上下文
#[derive(Debug)]
pub struct Transaction {
    pub id: TxnId,
    pub status: TxnStatus,
    /// 這個交易修改了哪些 page（write set），用於 Rollback 與 Isolation 衝突偵測
    pub write_set: Vec<WriteSetEntry>,
    /// 同一交易最後一筆 WAL record 的 LSN（用於 ARIES Undo 鏈結）
    pub last_lsn: Lsn,
}

impl Transaction {
    pub fn new(id: TxnId) -> Self {
        Transaction {
            id,
            status: TxnStatus::Active,
            write_set: Vec::new(),
            last_lsn: 0,
        }
    }

    /// 記錄一個寫入操作（由資料庫在 apply 之前呼叫）
    pub fn record_write(
        &mut self,
        page_id: u64,
        offset: usize,
        before_image: Vec<u8>,
        lsn: Lsn,
    ) -> Result<(), TxnError> {
        if self.status != TxnStatus::Active {
            return Err(TxnError::NotActive(self.id, self.status.clone()));
        }
        self.write_set.push(WriteSetEntry { page_id, offset, before_image, lsn });
        self.last_lsn = lsn;
        Ok(())
    }

    /// 進入 COMMITTING 狀態（WAL fsync 之前）
    pub fn begin_commit(&mut self) -> Result<(), TxnError> {
        if self.status != TxnStatus::Active {
            return Err(TxnError::NotActive(self.id, self.status.clone()));
        }
        self.status = TxnStatus::Committing;
        Ok(())
    }

    /// WAL fsync 成功後，交易正式完成
    pub fn finish_commit(&mut self) -> Result<(), TxnError> {
        if self.status != TxnStatus::Committing {
            return Err(TxnError::InvalidTransition(
                self.id,
                self.status.clone(),
                TxnStatus::Committed,
            ));
        }
        self.status = TxnStatus::Committed;
        self.write_set.clear(); // 已提交，before_image 不再需要
        Ok(())
    }

    /// 開始 Abort（回滾 write_set，由 WalWriter 負責寫 undo log）
    pub fn begin_abort(&mut self) -> Result<Vec<WriteSetEntry>, TxnError> {
        if self.status != TxnStatus::Active && self.status != TxnStatus::Aborting {
            return Err(TxnError::InvalidTransition(
                self.id,
                self.status.clone(),
                TxnStatus::Aborting,
            ));
        }
        self.status = TxnStatus::Aborting;
        // 回傳 write_set，呼叫端負責 apply before_image 並寫 CLR
        let ws = std::mem::take(&mut self.write_set);
        Ok(ws)
    }

    /// Undo 全部完成，交易正式 Abort
    pub fn finish_abort(&mut self) {
        self.status = TxnStatus::Aborted;
    }
}

/// 交易錯誤
#[derive(Debug)]
pub enum TxnError {
    NotActive(TxnId, TxnStatus),
    InvalidTransition(TxnId, TxnStatus, TxnStatus),
    TxnNotFound(TxnId),
}

impl std::fmt::Display for TxnError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            TxnError::NotActive(id, status) => {
                write!(f, "txn {id} is not active (current: {status:?})")
            }
            TxnError::InvalidTransition(id, from, to) => {
                write!(f, "txn {id}: invalid transition {from:?} → {to:?}")
            }
            TxnError::TxnNotFound(id) => write!(f, "txn {id} not found"),
        }
    }
}

/// 交易管理器：管理所有活躍交易
pub struct TransactionManager {
    next_id: TxnId,
    active: HashMap<TxnId, Transaction>,
}

impl TransactionManager {
    pub fn new() -> Self {
        TransactionManager {
            next_id: 1,
            active: HashMap::new(),
        }
    }

    /// 開始一個新交易，回傳 TxnId
    pub fn begin(&mut self) -> TxnId {
        let id = self.next_id;
        self.next_id += 1;
        self.active.insert(id, Transaction::new(id));
        id
    }

    pub fn get_mut(&mut self, id: TxnId) -> Result<&mut Transaction, TxnError> {
        self.active.get_mut(&id).ok_or(TxnError::TxnNotFound(id))
    }

    /// Commit：進入 Committing，呼叫端負責 WAL fsync，成功後呼叫 confirm_commit
    pub fn commit(&mut self, id: TxnId) -> Result<(), TxnError> {
        self.get_mut(id)?.begin_commit()
    }

    /// WAL fsync 成功後確認 commit
    pub fn confirm_commit(&mut self, id: TxnId) -> Result<(), TxnError> {
        let txn = self.get_mut(id)?;
        txn.finish_commit()?;
        self.active.remove(&id);
        Ok(())
    }

    /// Rollback：取出 write_set，呼叫端負責 undo
    pub fn begin_rollback(
        &mut self,
        id: TxnId,
    ) -> Result<Vec<WriteSetEntry>, TxnError> {
        self.get_mut(id)?.begin_abort()
    }

    /// Undo 完成後確認 abort
    pub fn confirm_rollback(&mut self, id: TxnId) -> Result<(), TxnError> {
        let txn = self.get_mut(id).ok_or(TxnError::TxnNotFound(id))?;
        txn.finish_abort();
        self.active.remove(&id);
        Ok(())
    }

    pub fn active_txn_count(&self) -> usize {
        self.active.len()
    }
}
```

---

## 範例一：正常 Commit 流程

```rust
// src/main.rs
mod transaction;

use transaction::{TransactionManager, TxnStatus};

fn main() {
    let mut mgr = TransactionManager::new();

    // T1：正常 Commit
    let t1 = mgr.begin();
    println!("T1 started (id={t1})");

    {
        let txn = mgr.get_mut(t1).unwrap();
        txn.record_write(
            42,          // page_id
            0,           // offset
            b"old_data".to_vec(),
            100,         // lsn（實際由 WAL writer 提供）
        ).unwrap();
        txn.record_write(43, 16, b"other_old".to_vec(), 101).unwrap();
        println!("T1 write set size: {}", txn.write_set.len());
    }

    // 模擬 WAL 寫入 COMMIT record 並 fsync
    mgr.commit(t1).unwrap();
    // ... 此處 WAL writer.flush(commit_lsn) ...
    mgr.confirm_commit(t1).unwrap();

    println!("T1 committed, active txns: {}", mgr.active_txn_count());
}
```

輸出（WSL `cargo run`）：
```
T1 started (id=1)
T1 write set size: 2
T1 committed, active txns: 0
```

---

## 範例二：Rollback 流程

```rust
fn rollback_example() {
    let mut mgr = TransactionManager::new();
    let t2 = mgr.begin();

    {
        let txn = mgr.get_mut(t2).unwrap();
        txn.record_write(50, 0, b"before_rollback".to_vec(), 200).unwrap();
    }

    // 模擬某個操作失敗，觸發 Rollback
    let write_set = mgr.begin_rollback(t2).unwrap();

    println!("Rolling back T2, {} ops to undo:", write_set.len());
    // 從最新往最舊 undo（write_set 用 Vec，最後 push 的最新）
    for entry in write_set.iter().rev() {
        println!(
            "  Undo: page={}, offset={}, restore to {:?}",
            entry.page_id, entry.offset,
            std::str::from_utf8(&entry.before_image).unwrap_or("?")
        );
        // 實際會：apply before_image to page，寫 CLR 到 WAL
    }

    mgr.confirm_rollback(t2).unwrap();
    println!("T2 aborted, active txns: {}", mgr.active_txn_count());
}
```

---

## 範例三：非法狀態轉換的防護

```rust
fn invalid_transition_example() {
    let mut mgr = TransactionManager::new();
    let t3 = mgr.begin();

    // 先 commit
    mgr.commit(t3).unwrap();
    mgr.confirm_commit(t3).unwrap();

    // 嘗試對已 committed 的交易再 commit
    // 但 t3 已從 active 移除，應回傳 TxnNotFound
    match mgr.commit(t3) {
        Err(e) => println!("Expected error: {e}"),
        Ok(_) => panic!("should have failed"),
    }
}
```

---

## ACID 不是理所當然：每個保證的成本

| 性質 | 保證內容 | 成本 | 常見妥協 |
|---|---|---|---|
| Atomicity | 全有或全無 | Undo log 的寫入與空間 | 通常不妥協（否則資料直接壞） |
| Consistency | 約束始終滿足 | 約束檢查的 CPU + 可能的 abort | Deferred constraint（提交時才檢查） |
| Isolation | 並發交易不互見 | Lock 競爭或 MVCC 的版本儲存空間 | 弱化隔離級別（下章） |
| Durability | Commit 後永久存在 | fsync 延遲（1–10 ms/次） | `synchronous_commit=off` in Postgres |

**PostgreSQL 的 `synchronous_commit=off`**：把 COMMIT 的 `fsync` 延遲到背景 writer 定期做，COMMIT 對客戶端即刻返回。代價：crash 後最近幾百毫秒的已提交交易可能丟失。這是主動把 Durability 換效能的設計，不是 bug。

---

## BEGIN/COMMIT/ABORT 的 WAL 紀錄

回顧 Ch 17 的 WAL，現在我們知道這三筆 record 的語意：

```
[BEGIN   txn=T1 lsn=10]  ← 交易開始，分配 TxnId
[UPDATE  txn=T1 page=5 before="A" after="B" lsn=20]
[UPDATE  txn=T1 page=6 before="X" after="Y" lsn=30]
[COMMIT  txn=T1 lsn=40]  ← COMMIT record 落盤 = T1 永久存在
                            在這個 fsync 完成前，T1 不算提交
```

```
[BEGIN   txn=T2 lsn=50]
[UPDATE  txn=T2 page=7 before="M" after="N" lsn=60]
[ABORT   txn=T2 lsn=70]  ← T2 放棄，Recovery 時 Undo 到 before image
```

ABORT record 的寫入：正常 Rollback 時，Undo 完所有 write_set 後寫入 ABORT；Recovery 的 Undo 階段最後也會寫入 ABORT（告知下一次 Recovery 這個交易已 Undo 完）。

---

## Savepoint：交易內的部分回滾

SQL 標準支援 Savepoint：

```sql
BEGIN;
UPDATE accounts SET balance = balance - 100 WHERE id = 1;
SAVEPOINT my_save;
UPDATE accounts SET balance = balance + 100 WHERE id = 2;
-- 這裡發現 id=2 不存在
ROLLBACK TO SAVEPOINT my_save;
-- 只 undo 第二個 UPDATE，第一個保留
UPDATE accounts SET balance = balance + 100 WHERE id = 3;
COMMIT;
```

實作：Savepoint 記錄一個 LSN（savepoint_lsn），Rollback to Savepoint 只 undo savepoint_lsn 之後的操作。對應 WAL 中的 `SAVEPOINT` record 與 Undo 鏈結截斷。

---

## 交易 vs 批次操作

一個常見誤解：「把多個操作放進一個交易是為了效能（減少 round-trip）。」這是附帶效果，不是本質。

本質是：**交易是對失敗模式的聲明**。一個交易說的是：「這些操作，要麼全成功，要麼全不做。」即使只有一個操作，也可能因為需要 Rollback（如約束違反）而需要交易語意。

真正的效能來源是把多個交易合併成少幾次 fsync（group commit），而不是把多個操作塞進一個大交易。

---

## 踩雷

### 1. 在交易外做不可逆操作

把「發送 Email」或「呼叫第三方 API」放在交易內是個陷阱：交易 Abort 時，Email 已經送出無法撤銷。這是兩階段提交（2PC）出現的原因，但大多數系統用「outbox pattern」解決：把「要發的 Email」寫進 DB 內一張表（在同一交易內），由獨立的 worker 在 commit 後發送。

### 2. 長交易拖死系統

一個交易持有 lock（2PL）或保留舊版本（MVCC）的時間越長，對其他交易的影響越大。應用層應盡量縮短交易長度，把大量計算移到交易外。

### 3. Autocommit 的陷阱

大多數資料庫 client 預設開啟 autocommit——每個 SQL statement 自動是一個交易。如果要多個 statement 在同一交易，必須明確 `BEGIN`。忘記這點導致的 Bug：中間出錯，之前的 statement 已提交無法回滾。

### 4. Rollback 不是免費的

Rollback 需要執行 undo log，代價與交易修改量成正比。一個修改了 100 萬行的交易 Rollback 會跑幾秒到幾十秒，期間這些 page 仍被 lock 住（2PL 情況下）。

### 5. Consistency 的責任邊界

`CHECK (balance >= 0)` 確實可以防止帳戶餘額為負，但如果應用程式在 `SELECT` 和 `UPDATE` 之間有競爭（兩個交易同時讀到餘額 10，各自扣 10），帳戶仍可能被扣成 -10。這是 Isolation 的問題，不是 Consistency 的問題——要用 `SELECT FOR UPDATE` 或更高隔離級別解決。

---

## 進階延伸

**Nested Transaction（巢狀交易）**：子交易可以獨立 commit 或 abort，但 abort 的子交易不會影響父交易（只 undo 子交易自己的修改）。SQL Server 的 nested transaction 語意與這不同（只有最外層 COMMIT 才真正提交），容易混淆。

**Autonomous Transaction**：在一個交易內開啟另一個完全獨立的交易，即使外部交易 Abort 也不受影響。常用於記錄審計日誌（audit log）。Oracle 支援；Postgres 用 `dblink` 模擬。

**Distributed Transaction**：跨多台機器的交易，需要 Two-Phase Commit（2PC）或 Saga 模式。兩者各有取捨，是分散式系統的大主題，留到 Ch 38。

---

## 本章重點整理

- 交易是對失敗模式的聲明，不只是語法糖
- ACID 是四個獨立的工程保證：A（undo log）、C（約束檢查，主要是應用層）、I（並發控制）、D（WAL fsync）
- 交易狀態機：Active → Committing（WAL fsync 中）→ Committed（真正提交）；Active → Aborting → Aborted
- COMMIT record 落盤（fsync 成功）才是交易真正持久的瞬間
- 每個 ACID 保證都有成本，現實中都可以被妥協，代價是不同程度的安全降級

## 自我檢核

- [ ] 我能解釋 Atomicity 由什麼機制保證，以及 Rollback 的代價
- [ ] 我能說出為什麼 Consistency 是由 AID 推導出來的，不是獨立機制
- [ ] 我能描述 COMMITTING 與 COMMITTED 之間的差別（WAL fsync 的角色）
- [ ] 我能用 Rust 的 Transaction 狀態機描述正常 Commit 與 Rollback 的流程
- [ ] 我能說出至少兩個 ACID 被現實妥協的例子

## 延伸閱讀

- **ARIES 原始論文**：Mohan et al.（1992）Section 2.2（Transaction processing assumptions）——對 ACID 的形式定義，理解本章的數學基礎。
- **《Designing Data-Intensive Applications》Ch 7（Transactions）**——DDIA 用「交易保護你免於哪種失敗」的角度講 ACID，是本章最佳的白話補充，特別是它如何解構「C」的責任邊界。
- **CMU 15-445 Lecture 18（Concurrency Control Theory）**——Andy Pavlo 從 schedule 理論出發定義 Isolation，把 ACID 與並發排程理論接起來，是 Ch 20-21 的預熱。
- **《Database Internals》Part I Ch 5（Transaction Processing and Recovery）**——Alex Petrov 對 ACID 實作的精要描述，特別是 Undo log 的格式。
- **PostgreSQL 文件：Transaction Isolation**（https://www.postgresql.org/docs/current/transaction-iso.html）——真實系統如何實作四個隔離級別，對照 Ch 19-20 的理論。

---

→ [Ch 20 隔離級別與異常現象](./20-isolation-levels.md)
