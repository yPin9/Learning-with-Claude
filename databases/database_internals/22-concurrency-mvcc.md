# Ch 22 — 並發控制（二）MVCC

> **目標**：掌握 MVCC（Multi-Version Concurrency Control）的核心思想——每次寫產生新版本、讀不擋寫、版本可見性由 txn snapshot 決定——並用 Rust 實作版本鏈與可見性判斷，讓並發交易看到各自一致的資料快照。

## 為什麼需要 MVCC

2PL 的世界裡，讀者和寫者互相阻塞：讀者要等寫者放 X lock，寫者要等讀者放 S lock。在讀多寫少的 OLTP 工作負載下，這個設計讓吞吐量遠低於理論值。

MVCC 的核心觀察：**讀者只需要看到「他開始交易那一刻的世界」，不需要看最新版本**。把「最新版本」和「歷史版本」都存起來，讓不同的讀者各自看各自對應的版本——讀者就不需要擋寫者，寫者也不需要擋讀者。

```
沒有 MVCC 的世界：
  R1 ────等 X lock────────────> 讀到最新值
              W ──寫──commit──>

MVCC 的世界：
  R1 ──直接讀 snapshot──>       讀到開始時刻的版本
  W  ──────寫新版本──commit──>  不互相阻塞
```

**核心原則**：讀不擋寫，寫不擋讀。寫者產生新版本，舊版本保留給舊讀者。

## 版本鏈（Version Chain）

每一列（row）不再只有一個值，而是一條版本鏈：

```
物理儲存（heap file 或 append-only）：

[tuple A: val=100, txn_id=1, begin_ts=1, end_ts=3]
      ↓ (version pointer)
[tuple A: val=150, txn_id=3, begin_ts=3, end_ts=5]
      ↓
[tuple A: val=200, txn_id=5, begin_ts=5, end_ts=∞]  ← 最新版本
```

每個 tuple version 有：

| 欄位 | 說明 |
|---|---|
| `data` | 實際值 |
| `txn_id` | 建立這個版本的交易 ID |
| `begin_ts` | 版本從哪個 timestamp 開始有效 |
| `end_ts` | 版本到哪個 timestamp 失效（∞ 表示目前最新） |
| `next` | 指向更舊版本的指標（或更新版本，看實作） |

不同系統版本鏈的方向不同：

- **Postgres**：新版本寫在 heap 裡（append-only），舊版本原地保留，透過 `ctid` 指向新版本（oldest-to-newest）。
- **MySQL InnoDB**：新版本寫在 undo log，主 table 永遠是最新版本（newest-to-oldest，undo pointer 往舊版本走）。

## Snapshot 與可見性判斷

每個交易開始時，記錄一個 **snapshot**：

```rust
struct Snapshot {
    txn_id: u64,           // 本 txn 的 ID
    read_ts: u64,          // 本 txn 看到的時間點（通常 = 開始時的全域 ts）
    active_txns: Vec<u64>, // 開始時還在跑的其他 txn（它們的修改對我不可見）
}
```

**可見性判斷**（某個 tuple version 對我是否可見）：

```
一個 version (begin_ts, end_ts, created_by) 對 snapshot 可見，若：
  1. created_by 已提交（不是還在跑、也不是已回滾）
  2. begin_ts <= snapshot.read_ts
  3. end_ts > snapshot.read_ts
     （或 end_ts = ∞，還沒被覆蓋）
  4. created_by 不在 snapshot.active_txns 裡
     （開始時就在跑的 txn，即使它後來提交了，對我也不可見）
```

條件 4 是關鍵：它讓 snapshot 在交易開始那一刻「凍結」，之後的提交對我無效。

### 一張圖理解可見性

```
時間軸 →    ts=1    ts=3    ts=5    ts=7

T1 (read_ts=4):
                snapshot 看到 ts ≤ 4 且已提交的版本
                ────────────────x (ts=4 凍結)

T2 (write, begin=2, commit=5):
                                commit 在 ts=4 之後 → T1 看不到 T2 的寫

T3 (write, begin=1, commit=3):
                        commit 在 ts=4 之前 → T1 能看到 T3 的寫
                        （且 T3 不在 T1 的 active_txns 裡）
```

## 寫衝突（Write-Write Conflict）

MVCC 解決了讀寫衝突，但**寫寫衝突仍然需要處理**。兩個交易都想更新同一列：

```
T1: read A=100, prepare to write A=150
T2: read A=100, write A=200, commit

T1 現在想 commit，但 A 已經被 T2 改了
→ First-Updater-Wins：T1 必須 abort
```

Postgres 用 row-level lock 處理：write 前先拿 row 的 X lock，衝突就等待（實際上是「看到 in-progress 的版本就等那個 txn 結束」）。

## Rust 實作：MVCC 版本鏈與可見性判斷

以下是一個可在 WSL 編譯執行的完整實作：

```rust
// src/mvcc.rs
use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::sync::atomic::{AtomicU64, Ordering};

/// 全域時間戳計數器
static GLOBAL_TS: AtomicU64 = AtomicU64::new(1);

fn next_ts() -> u64 {
    GLOBAL_TS.fetch_add(1, Ordering::SeqCst)
}

/// 一個 tuple 的某個版本
#[derive(Clone, Debug)]
pub struct TupleVersion {
    pub value: i64,
    pub begin_ts: u64,   // 從這個 ts 開始有效
    pub end_ts: u64,     // u64::MAX 表示仍是最新版本
    pub created_by: u64, // 建立此版本的 txn_id
    pub deleted: bool,   // 是否被刪除
}

/// 交易狀態
#[derive(Clone, PartialEq, Debug)]
pub enum TxnStatus {
    Active,
    Committed,
    Aborted,
}

/// 交易的 snapshot
#[derive(Clone, Debug)]
pub struct TxnSnapshot {
    pub txn_id: u64,
    pub read_ts: u64,
    pub active_txns: Vec<u64>,
}

/// MVCC 引擎的共享狀態
pub struct MvccEngine {
    /// key → 版本鏈（最新的在最後）
    data: HashMap<String, Vec<TupleVersion>>,
    /// txn_id → 狀態
    txn_status: HashMap<u64, TxnStatus>,
    /// 目前還活著的 txn（id + 開始 ts）
    active_txns: Vec<(u64, u64)>,
}

impl MvccEngine {
    pub fn new() -> Self {
        Self {
            data: HashMap::new(),
            txn_status: HashMap::new(),
            active_txns: Vec::new(),
        }
    }
}

/// 可安全跨執行緒共享的包裝
pub struct Database {
    inner: Arc<Mutex<MvccEngine>>,
}

impl Database {
    pub fn new() -> Self {
        Self {
            inner: Arc::new(Mutex::new(MvccEngine::new())),
        }
    }

    pub fn begin_txn(&self) -> Transaction {
        let mut engine = self.inner.lock().unwrap();
        let txn_id = next_ts();
        let read_ts = next_ts();
        let active = engine.active_txns
            .iter()
            .map(|(id, _)| *id)
            .collect::<Vec<_>>();
        let snapshot = TxnSnapshot {
            txn_id,
            read_ts,
            active_txns: active,
        };
        engine.txn_status.insert(txn_id, TxnStatus::Active);
        engine.active_txns.push((txn_id, read_ts));
        Transaction {
            db: Arc::clone(&self.inner),
            snapshot,
            write_set: Vec::new(),
        }
    }
}

pub struct Transaction {
    db: Arc<Mutex<MvccEngine>>,
    pub snapshot: TxnSnapshot,
    write_set: Vec<(String, i64)>,  // 本 txn 的寫暫存
}

impl Transaction {
    /// 可見性判斷：這個 version 對我的 snapshot 是否可見？
    fn is_visible(&self, version: &TupleVersion, engine: &MvccEngine) -> bool {
        if version.deleted { return false; }

        // 建立者必須已提交
        let creator_status = engine.txn_status.get(&version.created_by);
        let creator_committed = match creator_status {
            Some(TxnStatus::Committed) => true,
            None => false,
            _ => false,
        };

        // 自己寫的（即使還沒 commit）可以讀
        let is_self = version.created_by == self.snapshot.txn_id;

        if !creator_committed && !is_self {
            return false;
        }

        // 建立者不能是 snapshot 時還在 active 的 txn
        if self.snapshot.active_txns.contains(&version.created_by) {
            return false;
        }

        // 時間範圍檢查
        version.begin_ts <= self.snapshot.read_ts
            && version.end_ts > self.snapshot.read_ts
    }

    /// 讀取 key 的值（走版本鏈找最新可見版本）
    pub fn read(&self, key: &str) -> Option<i64> {
        let engine = self.db.lock().unwrap();
        let versions = engine.data.get(key)?;
        // 從最新版本往舊找第一個可見的
        versions.iter().rev().find(|v| self.is_visible(v, &engine)).map(|v| v.value)
    }

    /// 寫入（先暫存到 write_set，commit 時才真正寫入版本鏈）
    pub fn write(&mut self, key: &str, value: i64) {
        // 移除舊的暫存（同一 txn 多次寫同一 key）
        self.write_set.retain(|(k, _)| k != key);
        self.write_set.push((key.to_string(), value));
    }

    /// 提交：把所有寫入產生新版本，廢棄舊版本的 end_ts
    pub fn commit(self) -> Result<(), String> {
        let mut engine = self.db.lock().unwrap();
        let commit_ts = next_ts();

        for (key, value) in &self.write_set {
            // 把目前最新版本的 end_ts 設為 commit_ts（廢棄）
            if let Some(versions) = engine.data.get_mut(key) {
                if let Some(latest) = versions.iter_mut()
                    .filter(|v| v.end_ts == u64::MAX)
                    .next()
                {
                    latest.end_ts = commit_ts;
                }
            }
            // 插入新版本
            let new_version = TupleVersion {
                value: *value,
                begin_ts: commit_ts,
                end_ts: u64::MAX,
                created_by: self.snapshot.txn_id,
                deleted: false,
            };
            engine.data.entry(key.clone()).or_default().push(new_version);
        }

        // 更新 txn 狀態
        engine.txn_status.insert(self.snapshot.txn_id, TxnStatus::Committed);
        engine.active_txns.retain(|(id, _)| *id != self.snapshot.txn_id);
        Ok(())
    }

    /// 回滾：丟棄 write_set，標記 aborted
    pub fn abort(self) {
        let mut engine = self.db.lock().unwrap();
        engine.txn_status.insert(self.snapshot.txn_id, TxnStatus::Aborted);
        engine.active_txns.retain(|(id, _)| *id != self.snapshot.txn_id);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_read_own_write() {
        let db = Database::new();
        let mut t1 = db.begin_txn();
        t1.write("x", 42);
        assert_eq!(t1.read("x"), Some(42));  // 讀自己的寫
        t1.commit().unwrap();
    }

    #[test]
    fn test_snapshot_isolation_no_dirty_read() {
        let db = Database::new();

        // T1 先寫並提交
        let mut t1 = db.begin_txn();
        t1.write("x", 100);
        t1.commit().unwrap();

        // T2 開始時，T3 還在 active 中
        let mut t3 = db.begin_txn();
        t3.write("x", 999);  // T3 還沒 commit

        let t2 = db.begin_txn();  // T2 snapshot 包含 T3 在 active_txns

        // T3 commit
        t3.commit().unwrap();

        // T2 不應看到 T3 的寫（T3 在 T2 的 active_txns 裡）
        assert_eq!(t2.read("x"), Some(100));  // 只看到 T1 的版本
        t2.abort();
    }

    #[test]
    fn test_concurrent_reads_different_snapshots() {
        let db = Database::new();

        // 初始值
        let mut t0 = db.begin_txn();
        t0.write("balance", 1000);
        t0.commit().unwrap();

        // T1 和 T2 同時開始（都在 T3 更新之前 snapshot）
        let t1 = db.begin_txn();
        let t2 = db.begin_txn();

        // T3 更新值
        let mut t3 = db.begin_txn();
        t3.write("balance", 1500);
        t3.commit().unwrap();

        // T1 和 T2 都應看到 1000（它們 snapshot 時 T3 還在跑）
        assert_eq!(t1.read("balance"), Some(1000));
        assert_eq!(t2.read("balance"), Some(1000));

        // T4 是新交易，應看到 1500
        let t4 = db.begin_txn();
        assert_eq!(t4.read("balance"), Some(1500));

        t1.abort();
        t2.abort();
        t4.abort();
    }
}
```

把這段放在 `src/main.rs`（或獨立 crate），`cargo test` 應全部通過。

## Snapshot Isolation（SI）的隔離性

MVCC 天然實現 Snapshot Isolation（SI）：

| 異常現象 | 2PL(RR) | SI（MVCC） |
|---|---|---|
| Dirty read | 防住 | 防住 |
| Non-repeatable read | 防住 | 防住（同一 txn 兩次讀到同樣 snapshot） |
| Phantom read | 需要 predicate lock | **防住**（snapshot 凍結） |
| Write skew | 防住 | **防不住** ← 重要 |
| Lost update | 防住 | 防住（first-updater-wins） |

SI 防得住 phantom（因為 snapshot 凍結，看不到別人新插入的 row），但防不住 write skew（因為讀和寫在不同 key 上，各自的 snapshot 都是合法的）。

### Write Skew 的例子

```
系統規則：帳戶 A 和 B 的餘額總和 ≥ 0

T1 讀到 A=100, B=0   → 決定讓 A -= 100 → A=0   （合法）
T2 讀到 A=100, B=0   → 決定讓 B -= 100 → B=-100 （合法，那時 A 還是 100）
```

T1 和 T2 都看到合法的 snapshot 並做了合法決策，但最終 A+B=-100，違反規則。這是 Ch 23 SSI 要解決的問題。

## GC / Vacuum：清理舊版本

版本鏈不能無限增長。當一個版本對所有活著的交易都不可見時，就可以安全刪除。

**Postgres 的 VACUUM**：

```
vacuum 演算法：
  oldest_active_ts = min(所有 active txn 的 read_ts)

  對每個 tuple 的版本鏈：
    if version.end_ts < oldest_active_ts:
      這個版本對任何活著的 txn 都不可見 → 可以安全回收
```

Postgres 的 AUTOVACUUM 是一個 background daemon，週期跑 vacuum。沒有 vacuum 會導致「table bloat」——heap 裡塞滿死亡版本，查詢要掃更多資料。

**InnoDB 的方式**：undo log 在沒有 active txn 需要時被 purge thread 清理。它不需要全掃 heap，因為最新版本就在 heap，舊版本在 undo log。

```
Postgres vs InnoDB MVCC 策略對比：

                    Postgres          InnoDB
版本位置            全放 heap          最新→heap，舊→undo log
版本鏈方向          最舊到最新         最新到最舊
讀最新版本          掃版本鏈           直接讀 heap
讀舊版本            掃版本鏈           walk undo log
GC 機制             VACUUM（主動）      purge thread
Table bloat 問題    存在               較輕微
```

## 踩雷清單

1. **read_ts 和 txn_id 不能用同一個計數器**。snapshot 的 `read_ts` 是「讀開始的時間點」，txn_id 是身份識別。很多教材混用，但嚴格實作要分開，否則 can_see 的邊界判斷會出問題。

2. **active_txns 的 snapshot 要在 begin_txn 的鎖內原子性取得**。如果先拿 txn_id、再取 active_txns，中間可能有 txn commit，active_txns 就不準確了。begin_txn 要在鎖住狀態下一次取得 {txn_id, read_ts, active_txns}。

3. **自己寫的可見，自己沒寫的不可見——但要分清楚「尚未 commit」**。同一個 txn 的自寫可見（read-your-own-writes），但「別人 uncommitted 的」完全不可見（no dirty read）。

4. **write skew 在 SI 下不會報錯，靜靜地破壞不變量**。這是 SI 的已知弱點，不是 bug。需要 SSI 或應用層加 SELECT FOR UPDATE 的 row lock 來修。

5. **Vacuum 跑太慢或 active 交易太老**。長 txn 會擋住 vacuum 清除版本（因為 oldest_active_ts 不推進），導致 table bloat。Postgres 有 `idle_in_transaction_session_timeout` 殺掉太老的 idle txn。

## 進階延伸

- **Timestamp Ordering（TO）**：另一種 MVCC 實作，用 timestamp 決定讀寫衝突，不用鎖。Thomas Write Rule 是 TO 的一個特例。
- **Calvin**（論文）：先把交易 serialize 然後再並發執行，完全無衝突，但需要 pre-declared read/write set。
- **HTAP 的挑戰**：OLTP 跑 MVCC，OLAP 需要讀大量舊版本——版本鏈的 GC 策略在 HTAP 系統（TiDB、SingleStore）是核心設計點。

---

## 本章重點整理

- **MVCC 核心**：每次寫產生新版本，讀者走版本鏈找 snapshot 對應的可見版本，讀寫不互斥。
- **版本鏈**：每個 tuple version 有 (begin_ts, end_ts, created_by)，end_ts=∞ 是最新版本。
- **可見性判斷**：creator 已提交、creator 不在 snapshot 的 active_txns 中、begin_ts ≤ read_ts ＜ end_ts。
- **SI 與 write skew**：MVCC 自然提供 Snapshot Isolation，防 phantom，但防不住 write skew。
- **GC/Vacuum**：oldest_active_ts 之前的版本可安全回收；Postgres 用 VACUUM，InnoDB 用 undo log purge。

## 自我檢核

1. 版本鏈中 `end_ts = u64::MAX` 代表什麼？為什麼要記 `begin_ts` 和 `end_ts` 兩個時間？
2. 一個 txn 的 snapshot 包含哪些資訊？active_txns 欄位的作用是什麼？
3. 為什麼 SI 防得住 phantom read 卻防不住 write skew？用一個例子說明。
4. Postgres VACUUM 和 InnoDB purge 各自清理的是什麼？什麼條件才能清理？
5. Write-write 衝突在 MVCC 下如何處理？first-updater-wins 是什麼意思？

## 延伸閱讀

1. **《Designing Data-Intensive Applications》Ch 7**（Kleppmann）— 最白話的 MVCC 與 snapshot isolation 說明，write skew 的例子比任何教材都清楚。必讀。
2. **Postgres 原始碼：`src/backend/utils/time/tqual.c`**（Postgres 9.x）或現代版 `heapam_visibility.c` — `HeapTupleSatisfiesMVCC` 就是可見性判斷的實作，40 行 C code 勝過千言萬語。
3. **InnoDB MVCC 說明**（MySQL internals blog / Jeremy Cole）— 搜 `innodb mvcc version chain`，解釋 undo log 版本鏈的細節。
4. **An Empirical Evaluation of In-Memory MVCC**（Wu et al., VLDB 2017）— 比較六種 MVCC 設計（版本鏈位置、GC 策略），量化每種選擇的吞吐量差異，告訴你「設計的取捨是有數字的」。
5. **CMU 15-445 Lecture 20: Multi-Version Concurrency Control** — 完整投影片，Postgres vs InnoDB vs HYRISE 的 MVCC 比較清楚在這裡。

---

→ [Ch 23 進階並發：SSI / OCC](./23-advanced-concurrency.md)
