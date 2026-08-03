# 練習 C — WAL + MVCC 整合

> **目標**：把前面分別學過的 WAL（Ch 17–18）和 MVCC（Ch 22）整合進同一個儲存引擎：支援 begin/commit/abort 語義、crash 後能從 WAL redo 恢復、並發交易看到一致 snapshot 且不會有 dirty read。

## 背景動機

你現在有兩個零件：

1. **WAL**（Write-Ahead Log）：每次修改先寫日誌再改資料，crash 後能 redo 恢復。
2. **MVCC**：每次寫產生新版本，讀者走版本鏈找 snapshot 對應的版本，讀寫不互斥。

這兩件事需要**協同工作**才有意義：

- WAL 保證**持久性（Durability）**：commit 的資料 crash 後不丟。
- MVCC 保證**隔離性（Isolation）**：並發交易互不干擾。

把它們整合起來，你就得到了一個具備 ACID「D」和「I」的小型資料庫核心。這就是 Postgres / SQLite WAL mode 的骨架。

## 任務規格

### 資料模型

用一個 in-memory KV store 作為「heap」，key 是 `String`，value 是版本鏈 `Vec<TupleVersion>`。WAL 寫到磁碟檔案 `wal.log`。

### 必須實作的 API

```rust
let db = Database::open("wal.log")?;  // 開啟時 replay WAL

let mut txn = db.begin();             // 開始交易，取得 snapshot
txn.write("key", value);              // 本地暫存
let v = txn.read("key");              // 走版本鏈找可見版本
txn.commit()?;                        // flush WAL record → 更新版本鏈

let mut txn2 = db.begin();
txn2.abort();                         // 丟棄 write set，寫 ABORT WAL record
```

### WAL 記錄格式

每條 WAL 記錄是一行 JSON（或自定二進位格式），至少包含：

```json
{"lsn": 1, "type": "BEGIN",  "txn_id": 10}
{"lsn": 2, "type": "WRITE",  "txn_id": 10, "key": "a", "value": 42}
{"lsn": 3, "type": "WRITE",  "txn_id": 10, "key": "b", "value": 99}
{"lsn": 4, "type": "COMMIT", "txn_id": 10}
{"lsn": 5, "type": "BEGIN",  "txn_id": 11}
{"lsn": 6, "type": "WRITE",  "txn_id": 11, "key": "a", "value": 200}
{"lsn": 7, "type": "ABORT",  "txn_id": 11}
```

Crash 後重開：replay WAL，只 redo COMMIT 過的 txn 的 WRITE，ABORT 或沒看到 COMMIT 的 txn 忽略。

### 兩類必通測試

**測試 A：Crash Recovery**

```rust
// 先寫並 commit
{
    let db = Database::open("wal.log").unwrap();
    let mut t = db.begin();
    t.write("x", 42);
    t.commit().unwrap();
}
// 模擬 crash：丟掉 in-memory 狀態，重新 open（replay WAL）
{
    let db = Database::open("wal.log").unwrap();
    let t = db.begin();
    assert_eq!(t.read("x"), Some(42));  // crash 後資料仍在
}
```

**測試 B：並發無 Dirty Read**

```rust
let db = Arc::new(Database::open("wal.log").unwrap());

let db2 = Arc::clone(&db);
let h = std::thread::spawn(move || {
    let mut t2 = db2.begin();
    t2.write("y", 999);
    // 還沒 commit，另一個 txn 不該看到 999
    std::thread::sleep(std::time::Duration::from_millis(50));
    t2.abort();
});

let t1 = db.begin();
std::thread::sleep(std::time::Duration::from_millis(10));
assert_eq!(t1.read("y"), None);  // t2 未 commit，看不到
h.join().unwrap();
```

## 期望輸出範例

執行 `cargo test` 後：

```
running 5 tests
test tests::test_basic_write_read ... ok
test tests::test_crash_recovery ... ok
test tests::test_no_dirty_read ... ok
test tests::test_snapshot_isolation ... ok
test tests::test_abort_not_visible ... ok

test result: ok. 5 passed; 0 failed; 0 ignored
```

執行 WAL replay 示範：

```
$ cargo run --example replay_demo
[WAL] Replaying wal.log...
  LSN 1: BEGIN txn=10
  LSN 2: WRITE txn=10 key=a val=42
  LSN 3: COMMIT txn=10 → applied
  LSN 4: BEGIN txn=11
  LSN 5: WRITE txn=11 key=a val=200
  → txn=11 no COMMIT, skipped (crash recovery)
[DB] x = Some(42), y = None
```

## 卡住提示

1. **WAL 和版本鏈的 timestamp 同步**：commit 時要用**同一個 commit_ts** 同時寫進 WAL 的 COMMIT record 和 TupleVersion 的 begin_ts。Replay 時拿 WAL 裡的 commit_ts 重建版本。

2. **Replay 順序**：先掃整個 WAL 找哪些 txn_id 有 COMMIT record，再第二次掃 WRITE records，只 redo COMMITTED 的。不能邊掃邊 redo（不知道後面有沒有 COMMIT）。

3. **TupleVersion 的 end_ts 在 replay 時怎麼設**：假設 WAL 裡有兩次寫同一 key：txn=10 commit_ts=3，txn=20 commit_ts=7。Replay 後，txn=10 的版本 end_ts 應是 7（txn=20 把它覆蓋），txn=20 的版本 end_ts=∞。按 commit_ts 排序後更新 end_ts 即可。

4. **Thread safety**：Database 內部的 MvccEngine 要包在 `Arc<Mutex<...>>` 裡，txn 持有一個 Arc clone，操作時 lock。

5. **WAL flush 的時機**：COMMIT record 必須在 `commit()` 回傳前 `flush`（`file.flush()` + 視系統決定要不要 `sync_all()`）。WRITE records 可以 buffer（batch write 效能更好）。

## 分段實作建議

### Step 1：WAL 讀寫層（不含 MVCC）

```rust
// src/wal.rs
pub enum WalRecord {
    Begin  { lsn: u64, txn_id: u64 },
    Write  { lsn: u64, txn_id: u64, key: String, value: i64 },
    Commit { lsn: u64, txn_id: u64, commit_ts: u64 },
    Abort  { lsn: u64, txn_id: u64 },
}

pub struct WalWriter {
    file: BufWriter<File>,
    next_lsn: u64,
}

impl WalWriter {
    pub fn append(&mut self, record: &WalRecord) -> io::Result<()>;
    pub fn flush(&mut self) -> io::Result<()>;
}

pub fn replay_wal(path: &str) -> io::Result<Vec<WalRecord>>;
```

先把 WAL 的讀寫跑通，寫一個 unit test 確認 replay 能拿回原始 records。

### Step 2：MVCC 引擎（Ch 22 的程式碼）

把 Ch 22 的 `MvccEngine` 程式碼整合進來，確認 `begin_txn / read / write / commit / abort` 在記憶體中能正常運作。測試 dirty read、snapshot isolation。

### Step 3：commit() 整合 WAL

修改 `Transaction::commit()`：

```rust
pub fn commit(self) -> io::Result<()> {
    let commit_ts = {
        let mut engine = self.db.lock().unwrap();
        // 1. 寫 WAL COMMIT record（先 WAL，再改狀態）
        let ts = next_ts();
        engine.wal.append(&WalRecord::Commit {
            lsn: engine.next_lsn(),
            txn_id: self.snapshot.txn_id,
            commit_ts: ts,
        })?;
        engine.wal.flush()?;    // ← 必須先 flush 才能改記憶體狀態
        ts
    };
    // 2. 更新版本鏈（同 Ch 22 的 commit 邏輯，但用 commit_ts）
    // ...
    Ok(())
}
```

關鍵：**WAL flush 必須在更新版本鏈之前**。這就是 WAL 的「write-ahead」語義——日誌先落盤，才改資料。

### Step 4：open() 實作 WAL Replay

```rust
pub fn open(wal_path: &str) -> io::Result<Self> {
    let records = replay_wal(wal_path)?;

    // 找哪些 txn 有 COMMIT
    let committed: HashMap<u64, u64> = records.iter()
        .filter_map(|r| if let WalRecord::Commit { txn_id, commit_ts, .. } = r {
            Some((*txn_id, *commit_ts))
        } else { None })
        .collect();

    // Redo committed txn 的 WRITE
    let mut engine = MvccEngine::new();
    for record in &records {
        if let WalRecord::Write { txn_id, key, value, .. } = record {
            if let Some(&commit_ts) = committed.get(txn_id) {
                // 插入版本（end_ts 暫設 ∞，後面整理）
                engine.data.entry(key.clone()).or_default().push(TupleVersion {
                    value: *value,
                    begin_ts: commit_ts,
                    end_ts: u64::MAX,
                    created_by: *txn_id,
                    deleted: false,
                });
                engine.txn_status.insert(*txn_id, TxnStatus::Committed);
            }
        }
    }

    // 整理 end_ts：同一 key 按 begin_ts 排序，前一個 end_ts = 後一個 begin_ts
    for versions in engine.data.values_mut() {
        versions.sort_by_key(|v| v.begin_ts);
        for i in 0..versions.len().saturating_sub(1) {
            versions[i].end_ts = versions[i + 1].begin_ts;
        }
    }

    // 重建 WAL writer（append mode）
    let wal = WalWriter::open_append(wal_path)?;
    Ok(Database { inner: Arc::new(Mutex::new(engine)), wal })
}
```

### Step 5：多執行緒測試 + Crash 模擬

用 `std::thread::spawn` 跑並發測試（測試 B）。Crash 模擬不需要真的 kill 進程——直接丟掉 `Database` 物件（`drop`），然後重新 `Database::open()` 即可。

## 完整參考解答

<details>
<summary>展開完整參考解答（可在 WSL 編譯執行）</summary>

```toml
# Cargo.toml
[package]
name = "wal-mvcc"
version = "0.1.0"
edition = "2021"

[dependencies]
serde = { version = "1", features = ["derive"] }
serde_json = "1"
```

```rust
// src/main.rs
mod wal;
mod mvcc;
mod database;

fn main() {
    // 清理舊 WAL
    let _ = std::fs::remove_file("wal.log");

    // 寫入並提交
    {
        let db = database::Database::open("wal.log").unwrap();
        let mut t = db.begin();
        t.write("x", 42);
        t.write("y", 100);
        t.commit().unwrap();
    }

    // 模擬 crash：drop 掉 db，重新 open（replay WAL）
    {
        let db = database::Database::open("wal.log").unwrap();
        let t = db.begin();
        println!("After crash recovery:");
        println!("  x = {:?}", t.read("x"));  // Some(42)
        println!("  y = {:?}", t.read("y"));  // Some(100)
        t.abort();
    }
}

#[cfg(test)]
mod tests {
    use super::database::Database;
    use std::sync::Arc;

    #[test]
    fn test_basic_write_read() {
        let _ = std::fs::remove_file("test_basic.log");
        let db = Database::open("test_basic.log").unwrap();
        let mut t = db.begin();
        t.write("hello", 123);
        assert_eq!(t.read("hello"), Some(123));
        t.commit().unwrap();

        let t2 = db.begin();
        assert_eq!(t2.read("hello"), Some(123));
        t2.abort();
        let _ = std::fs::remove_file("test_basic.log");
    }

    #[test]
    fn test_crash_recovery() {
        let _ = std::fs::remove_file("test_crash.log");
        {
            let db = Database::open("test_crash.log").unwrap();
            let mut t = db.begin();
            t.write("survive", 999);
            t.commit().unwrap();

            let mut t2 = db.begin();
            t2.write("lost", 777);
            // t2 沒有 commit → crash
        }
        // Reopen: replay WAL
        {
            let db = Database::open("test_crash.log").unwrap();
            let t = db.begin();
            assert_eq!(t.read("survive"), Some(999));
            assert_eq!(t.read("lost"), None);  // 未 commit 的不見
            t.abort();
        }
        let _ = std::fs::remove_file("test_crash.log");
    }

    #[test]
    fn test_no_dirty_read() {
        let _ = std::fs::remove_file("test_dirty.log");
        let db = Arc::new(Database::open("test_dirty.log").unwrap());

        // T1 開始（拿 snapshot）
        let t1 = db.begin();

        // T2 寫了但還沒 commit（在另一個 thread）
        let db2 = Arc::clone(&db);
        let h = std::thread::spawn(move || {
            let mut t2 = db2.begin();
            t2.write("dirty", 888);
            // 不 commit，直接 abort
            t2.abort();
        });
        h.join().unwrap();

        // T1 看不到 T2 的 dirty write
        assert_eq!(t1.read("dirty"), None);
        t1.abort();
        let _ = std::fs::remove_file("test_dirty.log");
    }

    #[test]
    fn test_snapshot_isolation() {
        let _ = std::fs::remove_file("test_snapshot.log");
        let db = Database::open("test_snapshot.log").unwrap();

        // 初始值
        let mut t0 = db.begin();
        t0.write("balance", 1000);
        t0.commit().unwrap();

        // T1 和 T2 同時開始 snapshot
        let t1 = db.begin();
        let t2 = db.begin();

        // T3 更新
        let mut t3 = db.begin();
        t3.write("balance", 2000);
        t3.commit().unwrap();

        // T1 和 T2 都應看到 1000（snapshot 時 T3 還沒 commit 或在 active 中）
        assert_eq!(t1.read("balance"), Some(1000));
        assert_eq!(t2.read("balance"), Some(1000));

        // 新 txn 應看到 2000
        let t4 = db.begin();
        assert_eq!(t4.read("balance"), Some(2000));

        t1.abort(); t2.abort(); t4.abort();
        let _ = std::fs::remove_file("test_snapshot.log");
    }

    #[test]
    fn test_abort_not_visible() {
        let _ = std::fs::remove_file("test_abort.log");
        let db = Database::open("test_abort.log").unwrap();

        let mut t = db.begin();
        t.write("ghost", 42);
        t.abort();

        let t2 = db.begin();
        assert_eq!(t2.read("ghost"), None);
        t2.abort();
        let _ = std::fs::remove_file("test_abort.log");
    }
}
```

```rust
// src/wal.rs
use serde::{Deserialize, Serialize};
use std::fs::{File, OpenOptions};
use std::io::{self, BufRead, BufReader, BufWriter, Write};

#[derive(Serialize, Deserialize, Debug, Clone)]
#[serde(tag = "type")]
pub enum WalRecord {
    Begin  { lsn: u64, txn_id: u64 },
    Write  { lsn: u64, txn_id: u64, key: String, value: i64 },
    Commit { lsn: u64, txn_id: u64, commit_ts: u64 },
    Abort  { lsn: u64, txn_id: u64 },
}

pub struct WalWriter {
    writer: BufWriter<File>,
    pub next_lsn: u64,
}

impl WalWriter {
    pub fn open_create(path: &str) -> io::Result<Self> {
        let f = OpenOptions::new().create(true).append(true).open(path)?;
        Ok(Self { writer: BufWriter::new(f), next_lsn: 1 })
    }

    pub fn open_append(path: &str, next_lsn: u64) -> io::Result<Self> {
        let f = OpenOptions::new().create(true).append(true).open(path)?;
        Ok(Self { writer: BufWriter::new(f), next_lsn })
    }

    pub fn append(&mut self, record: WalRecord) -> io::Result<u64> {
        let lsn = self.next_lsn;
        self.next_lsn += 1;
        let json = serde_json::to_string(&record).unwrap();
        writeln!(self.writer, "{}", json)?;
        Ok(lsn)
    }

    pub fn flush(&mut self) -> io::Result<()> {
        self.writer.flush()
    }
}

pub fn replay_wal(path: &str) -> io::Result<Vec<WalRecord>> {
    let f = match File::open(path) {
        Ok(f) => f,
        Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(vec![]),
        Err(e) => return Err(e),
    };
    let reader = BufReader::new(f);
    let mut records = Vec::new();
    for line in reader.lines() {
        let line = line?;
        if line.trim().is_empty() { continue; }
        if let Ok(rec) = serde_json::from_str::<WalRecord>(&line) {
            records.push(rec);
        }
    }
    Ok(records)
}
```

```rust
// src/mvcc.rs
use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};

pub static GLOBAL_TS: AtomicU64 = AtomicU64::new(1);

pub fn next_ts() -> u64 {
    GLOBAL_TS.fetch_add(1, Ordering::SeqCst)
}

#[derive(Clone, Debug)]
pub struct TupleVersion {
    pub value: i64,
    pub begin_ts: u64,
    pub end_ts: u64,
    pub created_by: u64,
}

#[derive(Clone, PartialEq, Debug)]
pub enum TxnStatus {
    Active,
    Committed,
    Aborted,
}

#[derive(Clone, Debug)]
pub struct TxnSnapshot {
    pub txn_id: u64,
    pub read_ts: u64,
    pub active_txns: Vec<u64>,
}

pub struct MvccEngine {
    pub data: HashMap<String, Vec<TupleVersion>>,
    pub txn_status: HashMap<u64, TxnStatus>,
    pub active_txns: Vec<(u64, u64)>,  // (txn_id, start_ts)
}

impl MvccEngine {
    pub fn new() -> Self {
        Self {
            data: HashMap::new(),
            txn_status: HashMap::new(),
            active_txns: Vec::new(),
        }
    }

    pub fn is_visible(&self, version: &TupleVersion, snapshot: &TxnSnapshot) -> bool {
        let is_self = version.created_by == snapshot.txn_id;
        if is_self {
            return version.begin_ts <= snapshot.read_ts
                && version.end_ts > snapshot.read_ts;
        }

        let committed = matches!(
            self.txn_status.get(&version.created_by),
            Some(TxnStatus::Committed)
        );
        if !committed { return false; }

        if snapshot.active_txns.contains(&version.created_by) {
            return false;
        }

        version.begin_ts <= snapshot.read_ts && version.end_ts > snapshot.read_ts
    }
}
```

```rust
// src/database.rs
use std::collections::HashMap;
use std::io;
use std::sync::{Arc, Mutex};

use crate::mvcc::{next_ts, MvccEngine, TupleVersion, TxnSnapshot, TxnStatus};
use crate::wal::{replay_wal, WalRecord, WalWriter};

pub struct DatabaseInner {
    engine: MvccEngine,
    wal: WalWriter,
}

pub struct Database {
    inner: Arc<Mutex<DatabaseInner>>,
}

impl Database {
    pub fn open(wal_path: &str) -> io::Result<Self> {
        let records = replay_wal(wal_path)?;

        // 找 committed txn
        let committed: HashMap<u64, u64> = records.iter()
            .filter_map(|r| match r {
                WalRecord::Commit { txn_id, commit_ts, .. } => Some((*txn_id, *commit_ts)),
                _ => None,
            })
            .collect();

        let max_lsn = records.iter()
            .map(|r| match r {
                WalRecord::Begin  { lsn, .. } => *lsn,
                WalRecord::Write  { lsn, .. } => *lsn,
                WalRecord::Commit { lsn, .. } => *lsn,
                WalRecord::Abort  { lsn, .. } => *lsn,
            })
            .max()
            .unwrap_or(0);

        let mut engine = MvccEngine::new();

        // Redo committed writes
        for record in &records {
            if let WalRecord::Write { txn_id, key, value, .. } = record {
                if let Some(&commit_ts) = committed.get(txn_id) {
                    engine.data.entry(key.clone()).or_default().push(TupleVersion {
                        value: *value,
                        begin_ts: commit_ts,
                        end_ts: u64::MAX,
                        created_by: *txn_id,
                    });
                    engine.txn_status.insert(*txn_id, TxnStatus::Committed);
                }
            }
        }

        // 整理 end_ts（同 key 按 begin_ts 排序，前一個 end_ts = 後一個 begin_ts）
        for versions in engine.data.values_mut() {
            versions.sort_by_key(|v| v.begin_ts);
            let len = versions.len();
            for i in 0..len.saturating_sub(1) {
                versions[i].end_ts = versions[i + 1].begin_ts;
            }
        }

        // 把全域 ts 推到比 WAL 裡所有 ts 都大
        let max_ts = committed.values().max().copied().unwrap_or(0);
        let current = crate::mvcc::GLOBAL_TS.load(std::sync::atomic::Ordering::SeqCst);
        if max_ts >= current {
            crate::mvcc::GLOBAL_TS.store(max_ts + 1, std::sync::atomic::Ordering::SeqCst);
        }

        let wal = WalWriter::open_append(wal_path, max_lsn + 1)?;

        Ok(Self {
            inner: Arc::new(Mutex::new(DatabaseInner { engine, wal })),
        })
    }

    pub fn begin(&self) -> Transaction {
        let mut inner = self.inner.lock().unwrap();
        let txn_id = next_ts();
        let read_ts = next_ts();
        let active = inner.engine.active_txns.iter().map(|(id, _)| *id).collect::<Vec<_>>();
        let snapshot = TxnSnapshot { txn_id, read_ts, active_txns: active };
        inner.engine.txn_status.insert(txn_id, TxnStatus::Active);
        inner.engine.active_txns.push((txn_id, read_ts));
        // 寫 WAL BEGIN
        let _ = inner.wal.append(WalRecord::Begin { lsn: 0, txn_id });
        Transaction {
            db: Arc::clone(&self.inner),
            snapshot,
            write_buf: Vec::new(),
        }
    }
}

pub struct Transaction {
    db: Arc<Mutex<DatabaseInner>>,
    pub snapshot: TxnSnapshot,
    write_buf: Vec<(String, i64)>,
}

impl Transaction {
    pub fn read(&self, key: &str) -> Option<i64> {
        let inner = self.db.lock().unwrap();

        // 先看 write_buf（read-your-own-writes）
        if let Some((_, v)) = self.write_buf.iter().rev().find(|(k, _)| k == key) {
            return Some(*v);
        }

        let versions = inner.engine.data.get(key)?;
        versions.iter().rev()
            .find(|v| inner.engine.is_visible(v, &self.snapshot))
            .map(|v| v.value)
    }

    pub fn write(&mut self, key: &str, value: i64) {
        self.write_buf.retain(|(k, _)| k != key);
        self.write_buf.push((key.to_string(), value));
    }

    pub fn commit(self) -> io::Result<()> {
        let mut inner = self.db.lock().unwrap();
        let commit_ts = next_ts();

        // WAL: 先寫所有 WRITE records
        for (key, value) in &self.write_buf {
            inner.wal.append(WalRecord::Write {
                lsn: 0,
                txn_id: self.snapshot.txn_id,
                key: key.clone(),
                value: *value,
            })?;
        }
        // WAL: COMMIT record
        inner.wal.append(WalRecord::Commit {
            lsn: 0,
            txn_id: self.snapshot.txn_id,
            commit_ts,
        })?;
        inner.wal.flush()?;  // 必須在改記憶體狀態前 flush

        // 更新版本鏈
        for (key, value) in &self.write_buf {
            if let Some(versions) = inner.engine.data.get_mut(key) {
                for v in versions.iter_mut().filter(|v| v.end_ts == u64::MAX) {
                    v.end_ts = commit_ts;
                }
            }
            inner.engine.data.entry(key.clone()).or_default().push(TupleVersion {
                value: *value,
                begin_ts: commit_ts,
                end_ts: u64::MAX,
                created_by: self.snapshot.txn_id,
            });
        }

        inner.engine.txn_status.insert(self.snapshot.txn_id, TxnStatus::Committed);
        inner.engine.active_txns.retain(|(id, _)| *id != self.snapshot.txn_id);
        Ok(())
    }

    pub fn abort(self) {
        let mut inner = self.db.lock().unwrap();
        let _ = inner.wal.append(WalRecord::Abort { lsn: 0, txn_id: self.snapshot.txn_id });
        let _ = inner.wal.flush();
        inner.engine.txn_status.insert(self.snapshot.txn_id, TxnStatus::Aborted);
        inner.engine.active_txns.retain(|(id, _)| *id != self.snapshot.txn_id);
    }
}
```

執行方式：

```bash
cargo test          # 跑所有 5 個測試
cargo run           # 跑 main，看 crash recovery 輸出
```

</details>

## 測試用例表

| # | 測試名 | 場景 | 期望結果 |
|---|---|---|---|
| 1 | `test_basic_write_read` | 同一 txn write 後 read | 讀到自己寫的值（read-your-own-writes） |
| 2 | `test_crash_recovery` | commit 後 drop db，reopen replay WAL | crash 前 committed 的資料仍在；未 commit 的不見 |
| 3 | `test_no_dirty_read` | T1 寫但 abort；T2 讀 | T2 看不到 T1 的寫 |
| 4 | `test_snapshot_isolation` | T1/T2 snapshot 後 T3 commit | T1/T2 看到 snapshot 時的值；新 txn 看到 T3 的值 |
| 5 | `test_abort_not_visible` | abort 的 txn 寫了 key | 後續 txn 看不到該 key |

進階測試（手動寫）：

| # | 場景 | 驗證點 |
|---|---|---|
| 6 | WAL 截斷（只寫 BEGIN+WRITE，沒有 COMMIT）→ replay | 截斷的 txn 不被 redo |
| 7 | 同一 key 被三個 txn 依序更新 → crash 後 replay | 版本鏈 end_ts 正確，最新值是第三個 txn |
| 8 | 10 個 thread 並發 begin/write/commit | 沒有 panic，所有 commit 的值都在 |

## 延伸挑戰

1. **加死鎖偵測**：加入 `LockManager`（Ch 21 的版本），write 前先拿 row X lock。用 wait-for graph 偵測死鎖，選 victim abort。

2. **加 SSI**：實作簡化版 SIREAD lock 追蹤，偵測 rw anti-dependency。可以先只偵測直接的兩個 txn write skew（T1 →rw T2 且 T2 →rw T1）。

3. **WAL 壓縮（Checkpoint）**：當 WAL 超過某大小，把目前 in-memory 狀態 dump 到 snapshot 檔案，截斷 WAL。Replay 時從 snapshot 開始。

4. **支援 String value**：目前 value 是 `i64`，改成 `serde_json::Value` 支援任意 JSON 值。

5. **測量吞吐量**：用 `std::time::Instant` 測 10 個 thread 並發 1000 個短 txn 的吞吐量（ops/sec），調整 `Mutex` 粒度或改用 `parking_lot::Mutex` 看差多少。

## 自我檢核

1. 為什麼 WAL 的 COMMIT flush 必須在更新記憶體版本鏈**之前**？如果順序顛倒，crash 後 replay 會發生什麼？
2. Replay 時為什麼要兩次掃 WAL（先找 committed txn、再 redo write）？能不能一次掃完？
3. `end_ts` 的整理（版本鏈排序後設前一個的 end_ts）是正確的嗎？如果同一 key 被同一 txn 寫了兩次會有什麼問題？
4. `is_visible` 中為什麼有「creator 在 snapshot.active_txns 中就不可見」這條規則？去掉會有什麼後果？
5. 目前的實作在高並發下的瓶頸是什麼？（提示：`Mutex<DatabaseInner>` 的粒度）

---

→ [Ch 24 查詢處理全景](./24-query-processing-overview.md)
