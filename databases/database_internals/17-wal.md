# Ch 17 — WAL：Write-Ahead Logging

> **目標**：理解為什麼原地更新磁碟頁面在 crash 後會留下半改的廢墟，以及 WAL（Write-Ahead Log，預寫式日誌）如何用「先寫日誌再改資料」這個鐵律讓資料庫從任何 crash 中復原。動手用 Rust 實作一個能 fsync 的 WAL writer。

## 為什麼原地更新（in-place update）不夠安全

B+tree 在修改一個 key 的時候，可能牽動：

1. 一個 leaf page 的 slot 被更新
2. 如果發生 split，一個 internal page 的 key 陣列被改寫
3. 父節點的 page 也可能需要更新

這三個 page 需要三次寫入磁碟。問題：磁碟寫入不是原子的——「儲存媒體」的最小原子單位是 512-byte 或 4096-byte sector，而我們的 page 可能是 8KB 甚至 16KB，一次寫需要多個 sector。

如果在第一個 page 寫完、第二個還沒寫的時候系統 crash：

```
crash!
  │
  ▼
Page 1 (leaf) ── 已更新  ✓
Page 2 (internal) ── 沒有更新  ✗   ← 指標指向不存在的子節點
Page 3 (父節點) ── 沒有更新  ✗
```

重開後這棵 B+tree 是壞的。更糟糕的是，你不知道哪裡壞了，因為沒有任何紀錄。

這叫做**部分寫入（partial write）**問題。就算每次只改一個 page，同一個 page 的多個 sector 也可能只有前半段寫成功。

---

## WAL 的黃金規則

WAL 的核心想法：

> **在修改任何 data page 之前，必須先把描述該修改的 log record 寫入（並 fsync）磁碟。**

這就是 **WAL protocol**（也叫 write-ahead logging rule）：

```
強制順序：

  [ write log record ]
         │
         ▼
  [ fsync log file ]          ← 這步不能省略，光 write 不夠
         │
         ▼
  [ modify / flush data page ] ← 這步才能進行
```

為什麼光 `write()` 系統呼叫不夠？因為 `write()` 只是把資料放進 kernel page cache，OS 可能幾秒後才真正寫到磁碟。`fsync()` 才能強制讓寫入抵達非揮發性儲存體。

有了這個規則：

- **Crash 前 log 已 fsync，data page 還沒改** → Redo：重放 log，補完修改。
- **Crash 前 log 也沒 fsync** → 這個交易沒被認可，完全忽略。
- **Crash 時 data page 已寫完** → 最好情況，沒事。

---

## 直覺圖：一次交易的 WAL 流程

```
Time ──────────────────────────────────────────────────────────▶

WAL file:
  [BEGIN txn=1] [UPDATE page=3 old=A new=B] [COMMIT txn=1]
       │                  │                       │
       │                  │                       │
       ▼                  ▼                       ▼
   寫入記憶體            寫入記憶體            fsync (強制落盤)
   log buffer           log buffer            ← COMMIT 必須 fsync

Data page:
                                                       │
                         只有在 COMMIT log fsync 後   │
                         才允許 flush page 3 到磁碟   ▼
                                                   [page 3 updated]
```

關鍵：**COMMIT record 落盤 = 這個交易「永遠存在」**，不管之後 crash 幾次。

---

## Log Record 格式

一個典型的 WAL log record 包含：

| 欄位 | 大小 | 說明 |
|---|---|---|
| `lsn` | 8 bytes | Log Sequence Number，全域單調遞增 |
| `prev_lsn` | 8 bytes | 同一交易的上一筆 record 的 LSN |
| `txn_id` | 8 bytes | 交易 ID |
| `type` | 1 byte | BEGIN / UPDATE / COMMIT / ABORT / CLR |
| `page_id` | 8 bytes | 被修改的 page（若有） |
| `offset` | 2 bytes | page 內偏移 |
| `length` | 2 bytes | 修改資料長度 |
| `before_image` | variable | 修改前的資料（undo 用） |
| `after_image` | variable | 修改後的資料（redo 用） |

**LSN（Log Sequence Number）** 是整個 WAL 的靈魂：

- 每筆 record 有唯一的、單調遞增的 LSN
- Buffer pool 的每個 page 維護一個 `page_lsn`，記錄「最後一筆修改這個 page 的 log record 的 LSN」
- Flush data page 前，必須確認 `page_lsn` 對應的 log record 已 fsync

```
page_lsn 語意：

  page_lsn = 100  →  LSN ≤ 100 的所有修改都已反映在此 page
```

---

## Redo Log vs Undo Log

WAL 通常同時包含兩種資訊：

| | Redo Log | Undo Log |
|---|---|---|
| 目的 | 重放已提交交易的修改 | 回滾未提交交易的修改 |
| 記錄什麼 | after image（修改後） | before image（修改前） |
| 何時使用 | crash recovery 的 Redo 階段 | crash recovery 的 Undo 階段、正常 ROLLBACK |
| 範例 | `UPDATE page=3, new=B` | `UPDATE page=3, old=A` |

ARIES 使用的是 **physiological logging**：每條記錄包含 page_id（physical）與 page 內的邏輯操作（logical），這樣可以在 Redo 時跳過已寫入的 page（用 `page_lsn` 判斷），效率最佳。

---

## Group Commit：攤提 fsync 成本

`fsync` 很貴，一次通常耗費 1–10 ms（HDD 更長）。如果每筆交易 COMMIT 都各做一次 fsync，吞吐量上限就是 100–1000 TPS，遠低於實際需求。

**Group commit** 的想法：把多個交易的 COMMIT log 積在一起，只做一次 fsync：

```
時間線：

  txn A COMMIT ──┐
  txn B COMMIT ──┼──→ 累積在 log buffer ──→ 一次 fsync ──→ 全部確認
  txn C COMMIT ──┘

  節省：3 次 fsync → 1 次 fsync
```

實作方式：
1. 交易完成 COMMIT record 後，**不立刻** fsync，而是把 COMMIT record 推進 log buffer，然後等待。
2. 一個背景 log writer thread 定期（例如每 1 ms）或當 buffer 滿時批次 fsync。
3. fsync 完成後通知所有等待的交易。

Postgres 的 `wal_writer_delay` 就是控制這個間隔的參數（預設 200 ms）。MySQL InnoDB 的 `innodb_flush_log_at_trx_commit` 是控制 group commit 積極性的開關（0/1/2）。

---

## Buffer Pool 與 WAL 的互動

還記得 Ch 5 的 buffer pool？它決定 page 什麼時候能被 evict 到磁碟。現在加入 WAL：

**No-Force 政策**：COMMIT 時不強制把 dirty page flush 到磁碟（page 可以留在 buffer pool 中）。
- 優點：不用每次都做多個隨機 I/O，批次寫更有效率。
- 但要求：WAL 必須先記錄所有修改，讓 crash 後可以 redo。

**Steal 政策**：允許把未提交交易的 dirty page evict 到磁碟（因為 buffer pool 空間可能不夠）。
- 優點：buffer pool 不會因為長交易把空間霸死。
- 但要求：WAL 必須記錄 before image，讓 crash 後可以 undo 未提交的修改。

ARIES 採用 **No-Force + Steal**，這是現代資料庫的標準設定，靈活性最高，但需要完整的 redo + undo log。

```
Buffer Pool page eviction 規則（Steal + WAL）：

  要 evict page P（page_lsn = 100）？
  │
  ├─ WAL 中 LSN ≤ 100 的 log 已 fsync？
  │    YES → 可以 evict（redo 資訊已在 log）
  │    NO  → 必須先 fsync WAL 到 LSN 100
  │
  └─ page 是 dirty（有未提交的修改）？
       YES → before image 已在 WAL → 可以 evict（undo 資訊已在 log）
       NO  → 直接 evict
```

---

## Rust 實作：WAL Writer

下面用 Rust 實作一個簡化的 WAL writer，支援 append log record 與 fsync。

```toml
# Cargo.toml
[package]
name = "wal-demo"
version = "0.1.0"
edition = "2021"

[dependencies]
byteorder = "1.5"
```

```rust
// src/wal.rs
use std::fs::{File, OpenOptions};
use std::io::{self, BufWriter, Write};
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use byteorder::{LittleEndian, WriteBytesExt};

/// Log record 的種類
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum RecordType {
    Begin = 1,
    Update = 2,
    Commit = 3,
    Abort = 4,
    /// Compensation Log Record：Undo 動作的紀錄，避免 Undo 再被 Undo
    Clr = 5,
}

/// 一筆 WAL record 的記憶體表示
#[derive(Debug, Clone)]
pub struct LogRecord {
    pub lsn: u64,
    pub prev_lsn: u64,       // 同一 txn 的前一筆；BEGIN 時為 0
    pub txn_id: u64,
    pub record_type: RecordType,
    pub page_id: Option<u64>,
    pub before_image: Vec<u8>,
    pub after_image: Vec<u8>,
}

impl LogRecord {
    /// 序列化成二進位，寫入檔案
    ///
    /// 格式（總長度 = header 41 bytes + before_len + after_len）：
    ///   [total_len: u32][lsn: u64][prev_lsn: u64][txn_id: u64]
    ///   [type: u8][page_id: u64][before_len: u16][after_len: u16]
    ///   [before_image...][after_image...]
    fn serialize(&self) -> Vec<u8> {
        let before_len = self.before_image.len() as u16;
        let after_len = self.after_image.len() as u16;
        let total_body = 8 + 8 + 8 + 1 + 8 + 2 + 2
            + self.before_image.len()
            + self.after_image.len();
        let total_len = (4 + total_body) as u32; // 含 total_len 自身

        let mut buf = Vec::with_capacity(total_len as usize);
        buf.write_u32::<LittleEndian>(total_len).unwrap();
        buf.write_u64::<LittleEndian>(self.lsn).unwrap();
        buf.write_u64::<LittleEndian>(self.prev_lsn).unwrap();
        buf.write_u64::<LittleEndian>(self.txn_id).unwrap();
        buf.write_u8(self.record_type as u8).unwrap();
        buf.write_u64::<LittleEndian>(self.page_id.unwrap_or(0)).unwrap();
        buf.write_u16::<LittleEndian>(before_len).unwrap();
        buf.write_u16::<LittleEndian>(after_len).unwrap();
        buf.extend_from_slice(&self.before_image);
        buf.extend_from_slice(&self.after_image);
        buf
    }
}

/// WAL Writer：負責 append log record 到磁碟，並在需要時 fsync
pub struct WalWriter {
    file: Mutex<File>,
    next_lsn: AtomicU64,
    flushed_lsn: AtomicU64, // 最後一次 fsync 後的最大 LSN
}

impl WalWriter {
    /// 開啟（或建立）WAL 檔案
    pub fn open(path: impl AsRef<Path>) -> io::Result<Arc<Self>> {
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)?;
        Ok(Arc::new(WalWriter {
            file: Mutex::new(file),
            next_lsn: AtomicU64::new(1),
            flushed_lsn: AtomicU64::new(0),
        }))
    }

    /// 分配一個新的 LSN
    fn alloc_lsn(&self) -> u64 {
        self.next_lsn.fetch_add(1, Ordering::SeqCst)
    }

    /// 寫入一筆 log record，回傳該 record 的 LSN
    ///
    /// 注意：這個呼叫不做 fsync——資料在 kernel page cache 中。
    /// 需要持久性時，呼叫 `flush(target_lsn)`。
    pub fn append(&self, mut record: LogRecord) -> io::Result<u64> {
        let lsn = self.alloc_lsn();
        record.lsn = lsn;
        let bytes = record.serialize();

        let mut file = self.file.lock().unwrap();
        file.write_all(&bytes)?;
        Ok(lsn)
    }

    /// fsync WAL 到 `target_lsn`：確保 LSN ≤ target_lsn 的所有 record 已持久化
    ///
    /// WAL protocol 的強制點：COMMIT record 必須呼叫此函式且成功後才算提交。
    pub fn flush(&self, target_lsn: u64) -> io::Result<()> {
        if self.flushed_lsn.load(Ordering::Acquire) >= target_lsn {
            return Ok(()); // 已 fsync，不需重做
        }
        let file = self.file.lock().unwrap();
        file.sync_all()?; // fsync
        // 更新 flushed_lsn（實際應記錄真正 fsync 到的 LSN，這裡簡化）
        self.flushed_lsn.fetch_max(target_lsn, Ordering::Release);
        Ok(())
    }

    pub fn flushed_lsn(&self) -> u64 {
        self.flushed_lsn.load(Ordering::Acquire)
    }
}

// src/main.rs
use wal::{LogRecord, RecordType, WalWriter};

mod wal;

fn main() -> std::io::Result<()> {
    let writer = WalWriter::open("/tmp/mydb.wal")?;

    // 交易 1：BEGIN → UPDATE → COMMIT
    let begin_lsn = writer.append(LogRecord {
        lsn: 0, // 由 append 填入
        prev_lsn: 0,
        txn_id: 1,
        record_type: RecordType::Begin,
        page_id: None,
        before_image: vec![],
        after_image: vec![],
    })?;
    println!("BEGIN  lsn={begin_lsn}");

    let update_lsn = writer.append(LogRecord {
        lsn: 0,
        prev_lsn: begin_lsn,
        txn_id: 1,
        record_type: RecordType::Update,
        page_id: Some(42),
        before_image: b"old_value".to_vec(),
        after_image: b"new_value".to_vec(),
    })?;
    println!("UPDATE lsn={update_lsn} page=42");

    let commit_lsn = writer.append(LogRecord {
        lsn: 0,
        prev_lsn: update_lsn,
        txn_id: 1,
        record_type: RecordType::Commit,
        page_id: None,
        before_image: vec![],
        after_image: vec![],
    })?;
    println!("COMMIT lsn={commit_lsn}");

    // WAL protocol：COMMIT 之後必須 fsync 才算真正提交
    writer.flush(commit_lsn)?;
    println!("fsync done, flushed_lsn={}", writer.flushed_lsn());

    // 只有在 flush 成功之後，才允許把 page 42 寫回磁碟
    println!("now safe to flush data page 42");

    Ok(())
}
```

執行（WSL）：

```bash
cd /tmp/wal-demo
cargo run
# BEGIN  lsn=1
# UPDATE lsn=2 page=42
# COMMIT lsn=3
# fsync done, flushed_lsn=3
# now safe to flush data page 42
```

---

## 範例二：Group Commit 模擬

這個範例用多個 thread 模擬多個交易並發提交，WAL writer 批次 fsync：

```rust
// 在 main.rs 加入（或另開 examples/group_commit.rs）

use std::sync::Arc;
use std::thread;

mod wal;
use wal::{LogRecord, RecordType, WalWriter};

fn commit_txn(writer: Arc<WalWriter>, txn_id: u64) -> std::io::Result<u64> {
    let begin = writer.append(LogRecord {
        lsn: 0,
        prev_lsn: 0,
        txn_id,
        record_type: RecordType::Begin,
        page_id: None,
        before_image: vec![],
        after_image: vec![],
    })?;

    let update = writer.append(LogRecord {
        lsn: 0,
        prev_lsn: begin,
        txn_id,
        record_type: RecordType::Update,
        page_id: Some(txn_id * 10),
        before_image: format!("old_{txn_id}").into_bytes(),
        after_image: format!("new_{txn_id}").into_bytes(),
    })?;

    let commit = writer.append(LogRecord {
        lsn: 0,
        prev_lsn: update,
        txn_id,
        record_type: RecordType::Commit,
        page_id: None,
        before_image: vec![],
        after_image: vec![],
    })?;

    Ok(commit)
}

fn main() -> std::io::Result<()> {
    let writer = WalWriter::open("/tmp/mydb_gc.wal")?;

    let mut handles = vec![];
    let mut commit_lsns = vec![];

    // 10 個交易並發產生 log records（不立刻 fsync）
    for txn_id in 1u64..=10 {
        let w = Arc::clone(&writer);
        let commit_lsn = commit_txn(w, txn_id)?;
        commit_lsns.push(commit_lsn);
    }

    // Group commit：找到最大 commit LSN，一次 fsync 搞定所有交易
    let max_commit_lsn = *commit_lsns.iter().max().unwrap();
    writer.flush(max_commit_lsn)?;
    println!(
        "Group commit: {} txns, one fsync, flushed_lsn={}",
        commit_lsns.len(),
        writer.flushed_lsn()
    );

    Ok(())
}
```

---

## 範例三：WAL Reader（Recovery 的前置）

Recovery 需要能讀回 WAL 檔案。這個簡化的 reader 示範格式驗證：

```rust
// src/wal_reader.rs
use std::fs::File;
use std::io::{self, BufReader, Read};
use byteorder::{LittleEndian, ReadBytesExt};

pub struct WalReader {
    reader: BufReader<File>,
}

impl WalReader {
    pub fn open(path: &str) -> io::Result<Self> {
        let file = File::open(path)?;
        Ok(WalReader { reader: BufReader::new(file) })
    }

    /// 讀取下一筆 record；檔案結尾回傳 None
    pub fn next_record(&mut self) -> io::Result<Option<WalEntry>> {
        let total_len = match self.reader.read_u32::<LittleEndian>() {
            Ok(v) => v,
            Err(e) if e.kind() == io::ErrorKind::UnexpectedEof => return Ok(None),
            Err(e) => return Err(e),
        };

        let lsn = self.reader.read_u64::<LittleEndian>()?;
        let prev_lsn = self.reader.read_u64::<LittleEndian>()?;
        let txn_id = self.reader.read_u64::<LittleEndian>()?;
        let record_type = self.reader.read_u8()?;
        let page_id = self.reader.read_u64::<LittleEndian>()?;
        let before_len = self.reader.read_u16::<LittleEndian>()? as usize;
        let after_len = self.reader.read_u16::<LittleEndian>()? as usize;

        let mut before_image = vec![0u8; before_len];
        self.reader.read_exact(&mut before_image)?;
        let mut after_image = vec![0u8; after_len];
        self.reader.read_exact(&mut after_image)?;

        Ok(Some(WalEntry {
            total_len,
            lsn,
            prev_lsn,
            txn_id,
            record_type,
            page_id: if page_id == 0 { None } else { Some(page_id) },
            before_image,
            after_image,
        }))
    }
}

#[derive(Debug)]
pub struct WalEntry {
    pub total_len: u32,
    pub lsn: u64,
    pub prev_lsn: u64,
    pub txn_id: u64,
    pub record_type: u8,
    pub page_id: Option<u64>,
    pub before_image: Vec<u8>,
    pub after_image: Vec<u8>,
}
```

---

## 邊界情況與常見踩雷

### 1. `write()` 不等於 `fsync()`

這是新手最常犯的錯：

```rust
// 錯誤：這樣 COMMIT 不安全
file.write_all(&bytes)?; // 資料在 kernel buffer，不在磁碟

// 正確
file.write_all(&bytes)?;
file.sync_all()?; // 強制落盤
```

`sync_data()` 只同步資料，不同步 metadata（如 file size）；`sync_all()` 兩者都同步。WAL 建議用 `sync_all()`。

### 2. Log Buffer 沒 flush 就 crash

如果在 `append()` 後還沒呼叫 `flush()` 前 crash，這筆交易就像從來沒發生。這是預期行為——但要確保呼叫端邏輯正確：**flush 失敗 = commit 失敗，不能告訴客戶端 OK**。

### 3. LSN 回繞（Wraparound）

`u64` 的 LSN 幾乎不可能在實務中回繞（18 quintillion 筆記錄），但不要用 `u32`。PostgreSQL 曾經有 32-bit transaction ID wraparound 的著名問題，需要 VACUUM 防止。

### 4. WAL 檔案無限成長

WAL 不可能永遠保留所有歷史記錄——磁碟會滿。真實系統用**checkpoint**來截斷 WAL：checkpoint 點之前的 log 已確保所有 dirty page 都已寫回磁碟，所以可以安全刪除。Ch 18 會詳細討論。

### 5. O_DIRECT 與 fsync 的微妙關係

某些配置用 `O_DIRECT` 繞過 kernel page cache，直接走 DMA。這樣 `write()` 就真的是直接寫硬體，但仍需 `fsync()` 確保持久（NVMe 內部也有快取）。不能因為用了 `O_DIRECT` 就省去 `fsync`。

---

## WAL 寫入吞吐量的實際數字

| 介質 | 一次 fsync 延遲 | WAL 上限 TPS（無 group commit） | WAL 上限 TPS（group commit 1ms batch） |
|---|---|---|---|
| 機械硬碟 HDD | 5–15 ms | 66–200 | ~1000 |
| SSD（SATA） | 0.1–0.5 ms | 2000–10000 | ~50000 |
| NVMe | 0.02–0.1 ms | 10000–50000 | ~200000 |

這解釋了為什麼 Postgres 建議把 WAL 放在獨立的 SSD 或 NVMe 上。

---

## 進階延伸

**WAL 壓縮**：記錄 after image 的 delta（差值）而非完整 page，大幅減少 WAL 大小。MySQL binlog 的 row-based format 就是這種策略。

**Logical WAL vs Physical WAL**：Physical WAL 記錄 byte-level 的 page 改動（恢復快但空間大）；Logical WAL 記錄 SQL 層面的操作（`INSERT INTO t VALUES ...`，空間小但 replay 慢，也是 Postgres logical replication 的基礎）。

**WAL 分段（segmented WAL）**：把 WAL 切成固定大小的 segment 檔案（Postgres 預設 16MB/segment），方便截斷與管理。Raft 的 log 也是同樣概念。

**Parallel WAL apply**：Recovery 時單線程 redo 可能很慢，可以按 page 分區並行 replay。MySQL 8.0 的 MTS（Multi-Threaded Slave）就是這樣。

---

## 本章重點整理

- 原地更新磁碟的部分寫入問題是 WAL 存在的根本原因
- WAL 黃金規則：**log record fsync 後，才能改 data page**
- LSN 是整個 WAL 系統的主軸：每個 page 有 `page_lsn`，flush page 前必須確認對應 log 已 fsync
- COMMIT record 的 fsync 決定了一筆交易是否真正持久
- Group commit 把多個 fsync 合併成一次，大幅提升吞吐量
- Buffer pool 採 No-Force + Steal 政策，依賴 WAL 提供 redo 與 undo 能力

## 自我檢核

- [ ] 我能解釋 partial write 為什麼破壞 B+tree 的一致性
- [ ] 我能描述 WAL protocol 的強制順序（先 log fsync，再改 page）
- [ ] 我知道 LSN 在 buffer pool flush 前扮演什麼角色
- [ ] 我能解釋 group commit 為什麼可以減少 fsync 次數
- [ ] 我能說出 No-Force 政策為什麼需要 redo log、Steal 政策為什麼需要 undo log

## 延伸閱讀

- **ARIES 原始論文**：Mohan et al.《ARIES: A Transaction Recovery Method Supporting Fine-Granularity Locking and Partial Rollbacks Using Write-Ahead Logging》（1992）——第 3 節（WAL protocol）與第 5 節（log record 格式）是本章的完整數學版本，建議配合讀。
- **《Database Internals》Part I Ch 7（Log-Structured Storage）**——Alex Petrov 對 WAL 與 LSN 的講解，有助於與 LSM 的 WAL 對比。
- **CMU 15-445 Lecture 19（Logging Schemes）**——Andy Pavlo 從 No-Undo/No-Redo 到 ARIES 的四格組合，建立系統性分類直覺。
- **《Designing Data-Intensive Applications》Ch 7（Transactions）**——DDIA 從應用程式角度解釋為什麼 durability 需要 WAL，是本章更友善的入門讀物。
- **PostgreSQL 文件 WAL 章節**（https://www.postgresql.org/docs/current/wal-intro.html）——真實系統如何用 `wal_level`、`fsync`、`synchronous_commit` 控制 WAL 行為，對照本章理論。

---

→ [Ch 18 Crash Recovery：ARIES](./18-crash-recovery-aries.md)
