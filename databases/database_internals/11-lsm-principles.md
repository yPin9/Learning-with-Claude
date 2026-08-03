> **目標**：理解 LSM-Tree 為何存在、它的三層結構如何把隨機寫轉成循序寫，以及寫放大／讀放大／空間放大這三個互相牽制的代價從哪裡來。這是後面所有 MemTable、SSTable、Compaction 章節的理論地基。

# Ch 11 — LSM 原理與三放大

---

## 為什麼需要 LSM？

先把問題說清楚。

傳統資料庫用 B-tree 做儲存引擎。B-tree 的每次寫入是 in-place update：找到那個 leaf page，直接改掉裡面的值，然後把 dirty page 寫回磁碟。這在讀取上很有效率——查一個 key 走 O(log N) 層就找到了。

但寫入有個根本問題：**隨機寫**。

每個 key 對應的 leaf page 在磁碟上的位置是散的。插入 1000 筆不同 key，等於在磁碟的 1000 個不同位置各寫一次。對 HDD 來說，每次寫都要先 seek（磁頭移動），seek 一次 5-10ms，1000 次就是 5-10 秒。即使是 SSD，隨機寫也會加速 write amplification 並磨損 NAND cell。

**LSM-Tree（Log-Structured Merge-Tree）就是為了消滅隨機寫而生的。**

核心思路只有一句話：**永遠只做循序寫，永遠不改已寫的東西，只在背景整理（compaction）。**

---

## 建立直覺：循序寫 vs 隨機寫

先看物理層面的差距：

```
HDD 隨機寫（4KB block）：
  [seek ~7ms] → [rotational latency ~4ms] → [transfer ~0.1ms]
  總共 ~11ms per 4KB → 約 0.36 MB/s

HDD 循序寫（連續 4KB stream）：
  [transfer only ~0.1ms per block]
  總共 ~0.1ms per 4KB → 約 40 MB/s（快 100 倍）

SSD 隨機寫（4KB）：約 100-200 MB/s
SSD 循序寫（大塊）：約 500-3000 MB/s（快 5-30 倍）
```

差距是真實的。寫入密集的工作負載（time-series、log ingestion、事件流）每秒要寫幾萬筆，用 B-tree 的隨機寫根本跟不上。

LSM 的解法是把所有寫入先緩衝在記憶體，累積到一定大小後，**一次性循序寫到磁碟**。這樣 I/O 永遠是大塊循序操作，吞吐量可以逼近磁碟的循序頻寬上限。

---

## LSM 整體結構

```
Write Path:
  client write
       |
       v
  [ MemTable ]  <- in RAM, sorted, mutable
       |  (full -> freeze)
       v
  [ Immutable MemTable ] --flush--> [ L0 SSTable files ]
                                          |  (compaction)
                                          v
                                    [ L1 SSTables ]
                                          |  (compaction)
                                          v
                                    [ L2 SSTables ]
                                          |
                                         ...

Read Path (point query):
  check MemTable
       |  (not found)
       v
  check Immutable MemTable(s)
       |  (not found)
       v
  check L0 files (ALL files, may overlap)
       |  (not found)
       v
  check L1 (binary search, non-overlapping)
       |  (not found)
       v
  check L2, L3 ... Ln
```

三個層次：

1. **MemTable**：在記憶體裡，有序（skip list 或紅黑樹），可讀可寫。
2. **Immutable MemTable**：MemTable 滿了就 freeze，變成唯讀，背景 thread 把它 flush 成 SSTable 寫到磁碟。
3. **SSTable（Sorted String Table）**：不可變的磁碟檔，key 有序，各 level 有自己的 compaction 規則。

寫入只碰 MemTable（記憶體操作），所以寫入路徑沒有磁碟 I/O（除了 WAL，後面章節處理）。

---

## 三個放大問題

LSM 不是免費的。它把寫入的代價轉移到背景的 compaction，但這帶來三種放大（amplification）。這三個放大構成一個三角，動一邊必然影響另外兩邊。

### 1. 寫放大（Write Amplification, WA）

**定義**：用戶寫入 1 byte，實際上磁碟總共被寫入多少 bytes？

每次 compaction 都要把 SSTable 讀出來、合併、再寫回去。一筆資料從 L0 被 compact 到 L1，再從 L1 到 L2，每次都是一次完整的寫入。

```
假設每 level 大小比例 r = 10：
  L0 -> L1 compaction：寫 1 份
  L1 -> L2 compaction：寫 1 份
  L2 -> L3 compaction：寫 1 份
  ...
  總 WA ≈ r * (number_of_levels)
  5 層的系統 WA 可達 10-50x
```

這表示用戶每寫入 1GB，磁碟實際上要做 10-50GB 的寫入工作。對 SSD 的壽命和 I/O 頻寬都是負擔。

### 2. 讀放大（Read Amplification, RA）

**定義**：讀一個 key，最壞情況要觸碰多少個檔案/頁面？

如果 key 不存在，或在最老的 level，讀操作必須依序檢查每一層：
- MemTable：1 次查詢
- L0：可能有多個檔案（L0 的 SSTable 之間 key range 可以重疊），全部要查
- L1, L2...：每層各查 1 個檔案（非重疊）

```
最壞情況 RA：
  1 (MemTable) + k (L0 files) + num_levels (L1..Ln)
  如果 L0 有 4 個檔案、有 6 層：
  RA ≈ 1 + 4 + 5 = 10 次 SSTable 查詢
  每次查詢還要讀 index block + bloom filter + data block
```

B-tree 的 point read 只要 O(log N) 頁，LSM 在沒有命中 MemTable 時就慢很多。Bloom filter 是用來減少 RA 的主要工具（後面章節會深挖）。

### 3. 空間放大（Space Amplification, SA）

**定義**：儲存 X bytes 的有效資料，磁碟實際佔用多少空間？

LSM 是 append-only。刪除一個 key 不是真的刪，而是寫一個 **tombstone**（墓碑標記）。更新一個 key 也不是改舊值，而是寫一個新版本。舊版本和 tombstone 一直佔著空間，直到 compaction 把它們清掉。

```
時間軸：
  t=1: write("foo", "bar")   -> SSTable 有 ("foo","bar")
  t=2: write("foo", "baz")   -> MemTable 有 ("foo","baz")
  flush ->
  磁碟現在有 ("foo","bar") 和 ("foo","baz") 兩份
  space_amplification = 2x（直到 compaction 合併）

  t=3: delete("foo")         -> 寫 tombstone ("foo", DEL)
  flush ->
  磁碟現在有三份記錄，有效資料是 0
  space_amplification = 無限大（直到 full compaction）
```

SA 在 compaction 週期長、刪除/更新比例高時會很嚴重。

---

## RUM 猜想（RUM Conjecture）

2016 年 Idreos 等人提出 RUM Conjecture：

> 對任何資料結構，你**不可能同時最小化** Read overhead（R）、Update overhead（U）、Memory overhead（M）。最佳化其中兩個，必然讓第三個變差。

```
            R（讀放大）
           / \
          /   \
         /     \
        /  ???  \
       /  不可能  \
      /  三角都小  \
     /_____________\
   U（寫放大）   M（空間放大）
```

B-tree 是偏 R 的設計：讀很快（in-place，直接找），但 U 不佳（隨機寫，page split）。

LSM 是偏 U 的設計：寫很快（循序 append），但 R 和 M 要靠參數調整（compaction 策略、Bloom filter、level 大小比）。

沒有萬能的結構。選 LSM 就是選擇犧牲讀放大和空間放大，換取極致的寫入吞吐。

---

## B-tree vs LSM 對比

```
+------------------+------------------+------------------+
| 操作             | B-tree           | LSM              |
+------------------+------------------+------------------+
| 循序寫（bulk）   | 慢（page split） | 快（append）     |
| 隨機寫（點寫）   | 慢（隨機 I/O）   | 快（寫 MemTable）|
| 點讀（point get）| 快（O(log N)）   | 中（多層查詢）   |
| 範圍讀（scan）   | 快（leaf chain） | 中（需 merge）   |
| 空間使用         | 效率高（in-place）| 較高（有舊版本）|
| 寫放大           | 低（3-5x）       | 高（10-50x）     |
| 適合場景         | OLTP 混合讀寫    | 寫密集、時序、日誌|
+------------------+------------------+------------------+
```

注意「點讀」欄位：LSM 不是一定慢。如果 key 在 MemTable 或 L0 就命中，速度可以很快。只有 key 不存在或在最深層才會慢。Bloom filter 可以把「key 不存在」這個 case 的 I/O 降到幾乎零。

---

## Rust 程式碼範例

### 範例 1：Append-Only Log Writer（循序寫的核心概念）

> 未編譯驗證，僅供概念示意。

```rust
use std::fs::{File, OpenOptions};
use std::io::{BufWriter, Write};

/// 最簡單的 append-only log：永遠只往尾端寫，絕不修改已寫的內容。
/// 這就是 LSM MemTable flush 到磁碟的基本姿態。
struct AppendLog {
    writer: BufWriter<File>,
    bytes_written: u64,
}

impl AppendLog {
    fn open(path: &str) -> std::io::Result<Self> {
        let file = OpenOptions::new()
            .create(true)
            .append(true)  // 關鍵：只能 append，不能 seek 回去改
            .open(path)?;
        Ok(AppendLog {
            writer: BufWriter::new(file),
            bytes_written: 0,
        })
    }

    /// 寫入一筆 key-value。格式：[key_len: u32][key][val_len: u32][val]
    fn append(&mut self, key: &[u8], value: &[u8]) -> std::io::Result<()> {
        let key_len = key.len() as u32;
        let val_len = value.len() as u32;

        self.writer.write_all(&key_len.to_le_bytes())?;
        self.writer.write_all(key)?;
        self.writer.write_all(&val_len.to_le_bytes())?;
        self.writer.write_all(value)?;

        self.bytes_written += (8 + key.len() + value.len()) as u64;
        Ok(())
    }

    /// 刷到磁碟（OS page cache -> disk）
    fn sync(&mut self) -> std::io::Result<()> {
        self.writer.flush()?;
        self.writer.get_ref().sync_all()  // fsync
    }
}

// 使用示範
fn write_demo() -> std::io::Result<()> {
    let mut log = AppendLog::open("/tmp/demo.log")?;

    // 這些寫入都是循序的：每次都往檔案尾端追加
    log.append(b"user:1001", b"alice")?;
    log.append(b"user:1002", b"bob")?;
    // 更新 user:1001 不是修改舊的，而是再追加一筆新的
    log.append(b"user:1001", b"alice_updated")?;
    // 刪除用 tombstone：value 用特殊標記
    log.append(b"user:1002", b"\x00TOMBSTONE")?;

    log.sync()?;

    // 注意：磁碟上現在有 4 筆 record，"有效"資料只有 1 筆。
    // 這就是空間放大的起點。讀取時要從頭掃或靠索引跳到最新版本。
    println!("written {} bytes", log.bytes_written);
    Ok(())
}
```

**邊界情況**：如果 `sync()` 前程式 crash，BufWriter 裡未 flush 的資料會遺失。真正的 LSM 靠 WAL（Write-Ahead Log）來確保持久性，MemTable 的內容是可以從 WAL 重建的。

---

### 範例 2：寫放大計算器

> 未編譯驗證，僅供概念示意。

```rust
/// 計算 Leveled Compaction 的理論寫放大。
///
/// 假設：
/// - L0 有 k 個 SSTable 就觸發 compaction
/// - L1..Ln 的大小比例為 r（每層比上層大 r 倍）
/// - num_levels 是 L1..Ln 的層數（不含 L0）
fn write_amplification(
    l0_file_count: u32,  // L0 的 SSTable 數量上限
    level_ratio: u32,    // 相鄰 level 的大小倍率
    num_levels: u32,     // L1 到 Ln 的層數
) -> f64 {
    // L0 -> L1：把 L0 的所有 SSTable 與 L1 重疊的 SSTable 合併
    // 最壞情況：L0 的所有 key 都和 L1 重疊
    // WA for this step ≈ l0_file_count + level_ratio（L1 那些被選中的）
    let l0_to_l1_wa = (l0_file_count + level_ratio) as f64;

    // L1 -> L2, L2 -> L3, ... 每層的 WA 都是 level_ratio
    // 因為每次只 compact L(i) 的一個 SSTable 和 L(i+1) 的 r 個 SSTable
    let per_level_wa = level_ratio as f64;
    let lower_levels_wa = per_level_wa * (num_levels - 1) as f64;

    l0_to_l1_wa + lower_levels_wa
}

fn amplification_demo() {
    // LevelDB 預設：L0=4 files, ratio=10, 7 levels (L1-L7)
    let wa = write_amplification(4, 10, 6);
    println!("LevelDB 理論最壞 WA: {:.1}x", wa);
    // 輸出：LevelDB 理論最壞 WA: 64.0x

    // RocksDB 常見配置：L0=4, ratio=10, 4 levels
    let wa_rocks = write_amplification(4, 10, 3);
    println!("RocksDB 4-level WA: {:.1}x", wa_rocks);
    // 輸出：RocksDB 4-level WA: 34.0x

    // 注意：這是理論上界。平均 WA 通常低很多，
    // 因為 compaction 是漸進的，不是每層都完整觸碰。
    println!("實際 WA 通常是理論值的 30-60%");
}

/// 空間放大的粗估
fn space_amplification(
    valid_data_bytes: u64,
    tombstone_ratio: f64,   // 0.0 ~ 1.0，有多少是 tombstone
    version_staleness: f64, // 平均每個 key 有幾個舊版本在磁碟上
) -> f64 {
    // 有效資料以外的空間
    let tombstone_overhead = valid_data_bytes as f64 * tombstone_ratio;
    let version_overhead = valid_data_bytes as f64 * version_staleness;

    let total_disk = valid_data_bytes as f64 + tombstone_overhead + version_overhead;
    total_disk / valid_data_bytes as f64
}

fn space_demo() {
    let sa = space_amplification(1_000_000_000, 0.3, 1.5);
    println!("1GB 有效資料，30% tombstone，平均 1.5 個舊版本 -> SA: {:.2}x", sa);
    // 輸出：1GB 有效資料，30% tombstone，平均 1.5 個舊版本 -> SA: 2.80x
}
```

---

### 範例 3：LSM 分層結構的記憶體表示

> 未編譯驗證，僅供概念示意。

```rust
use std::collections::BTreeMap;

/// 最簡化的 LSM 層次結構示意。
/// 真正的實作 SSTable 是磁碟上的檔案；這裡用 Vec<BTreeMap> 模擬概念。
struct ToyLsm {
    /// MemTable：可寫，在記憶體
    memtable: BTreeMap<Vec<u8>, Option<Vec<u8>>>,  // None = tombstone
    /// 每個 level 的 SSTable 集合（簡化：用 BTreeMap 表示一個 SSTable）
    levels: Vec<Vec<BTreeMap<Vec<u8>, Option<Vec<u8>>>>>,
    memtable_size_limit: usize,
    current_size: usize,
}

impl ToyLsm {
    fn new(num_levels: usize, memtable_size_limit: usize) -> Self {
        ToyLsm {
            memtable: BTreeMap::new(),
            levels: vec![Vec::new(); num_levels],
            memtable_size_limit,
            current_size: 0,
        }
    }

    fn put(&mut self, key: Vec<u8>, value: Vec<u8>) {
        self.current_size += key.len() + value.len();
        self.memtable.insert(key, Some(value));

        if self.current_size >= self.memtable_size_limit {
            self.flush_memtable();
        }
    }

    fn delete(&mut self, key: Vec<u8>) {
        // 刪除不是真的刪：插入 tombstone（None）
        self.memtable.insert(key, None);
    }

    fn get(&self, key: &[u8]) -> Option<&[u8]> {
        // 1. 先查 MemTable（最新）
        if let Some(val) = self.memtable.get(key) {
            return val.as_deref();
        }
        // 2. 從 L0 開始往下查（讀放大的來源）
        for level in &self.levels {
            for sstable in level.iter().rev() {  // L0 新的在後面
                if let Some(val) = sstable.get(key) {
                    return val.as_deref();
                }
            }
        }
        None
    }

    fn flush_memtable(&mut self) {
        if self.memtable.is_empty() {
            return;
        }
        // 把 MemTable 的內容凍結成一個新的 L0 SSTable
        let frozen: BTreeMap<_, _> = self.memtable.drain().collect();
        self.levels[0].push(frozen);
        self.current_size = 0;

        // 觸發 L0 compaction（簡化：超過 4 個就合併到 L1）
        if self.levels[0].len() >= 4 {
            self.compact_l0_to_l1();
        }
    }

    fn compact_l0_to_l1(&mut self) {
        // 把所有 L0 SSTable 合併：後寫的版本覆蓋先寫的
        // 這一步就是寫放大的源頭之一
        let mut merged: BTreeMap<Vec<u8>, Option<Vec<u8>>> = BTreeMap::new();
        for sstable in self.levels[0].drain(..) {
            for (k, v) in sstable {
                merged.insert(k, v);  // 新版本覆蓋舊版本
            }
        }
        // 清掉 tombstone（只有當 L1 以下沒有更舊版本時才能清）
        // 真實實作要檢查更深的 level，這裡簡化直接清
        merged.retain(|_, v| v.is_some());

        if !merged.is_empty() {
            self.levels[1].push(merged);
        }
    }
}

fn lsm_demo() {
    let mut lsm = ToyLsm::new(3, 1024);

    lsm.put(b"alice".to_vec(), b"25".to_vec());
    lsm.put(b"bob".to_vec(), b"30".to_vec());
    lsm.put(b"carol".to_vec(), b"28".to_vec());

    // 更新：不改舊值，寫新版本
    lsm.put(b"alice".to_vec(), b"26".to_vec());

    // 刪除：寫 tombstone
    lsm.delete(b"bob".to_vec());

    assert_eq!(lsm.get(b"alice"), Some("26".as_bytes()));
    assert_eq!(lsm.get(b"bob"), None);      // tombstone -> None
    assert_eq!(lsm.get(b"carol"), Some("28".as_bytes()));
    assert_eq!(lsm.get(b"dave"), None);     // 不存在 -> None（這會掃所有層）

    println!("LSM 基本操作正確");
}
```

**邊界情況**：`get(b"dave")` 這個 case 最昂貴——key 不存在，但程式碼必須把每一層都查完才能確認。Bloom filter 就是為了這個 case：用很小的記憶體（每個 key 約 10 bits），讓「key 不存在」的判斷在 O(1) 完成，避免磁碟 I/O。

---

## 常見陷阱

**1. 誤以為 LSM 的寫入完全不碰磁碟**

寫入到 MemTable 確實不碰磁碟，但 WAL（Write-Ahead Log）是例外。每次寫入同時要 append 到 WAL 確保 crash recovery。所以一次寫入 = 1 次循序磁碟 I/O（WAL）+ 1 次記憶體操作（MemTable）。這比 B-tree 的隨機 I/O 好很多，但不是零 I/O。

**2. 誤以為 tombstone 立刻釋放空間**

Tombstone 只是一個標記。空間要等到 **full compaction**（tombstone 被 compact 到最底層並清除）才真正釋放。如果資料庫有大量刪除操作，磁碟使用量可能長期虛高，直到 compaction 趕上。

**3. L0 的特殊性**

L0 的 SSTable 之間 key range 可以重疊（因為每個 flush 都是獨立的，不和其他 L0 file 協調）。這讓 L0 的讀操作必須查所有 L0 file，讀放大比 L1+ 嚴重。這就是為什麼要設 L0 file 數量上限，超過就強制觸發 L0->L1 compaction。

**4. 空間放大在 compaction 期間會暫時翻倍**

Compaction 進行時，舊的 SSTable 和新合併的 SSTable 同時存在，直到合併完成才刪掉舊的。如果本來就已 SA=2x，compaction 期間可能瞬間到 3-4x。規劃磁碟容量要留這個 headroom。

**5. RUM 不是說 LSM 就是壞的讀**

RUM 說的是三個維度不能同時最小化。在 workload 以寫為主（time-series、event store、cache 底層）的場景，LSM 的讀放大是可以接受的代價，而且可以靠 Bloom filter + block cache 大幅降低。評估 LSM 適不適合，要先量 read/write ratio，不是直接說「讀慢就不用」。

---

## 進階延伸

**Tiered vs Leveled Compaction**

這裡介紹的是 Leveled Compaction（LevelDB/RocksDB 預設），每層 SSTable 的 key range 不重疊。另一種是 Tiered Compaction（Cassandra/ScyllaDB），同層可以有重疊，compaction 只在同層做 merge，WA 比 Leveled 低，但 RA 和 SA 更高。FIFO Compaction 是第三種，專給時序資料（直接按時間 expire 最老的檔案）。

**SILK（SIngle-Level Key-value）**

2019 年 USENIX ATC 的工作。傳統 LSM 的 compaction 會搶走 I/O 頻寬，影響前景讀寫。SILK 提出分離 compaction I/O 和用戶 I/O，讓前景操作優先。

**Monkey / Dostoevsky**

針對不同 workload（存在查詢 vs 不存在查詢）的 Bloom filter 預算分配最佳化。Monkey（2017）和 Dostoevsky（2018）都是 Idreos 組的工作，在 RUM 框架下做最佳點分析。

**PebblesDB / SplinterDB**

用 Guard（類似 skip list level）把大的 compaction 拆成小塊，降低 WA 同時維持合理的 RA。

---

## 本章重點整理

- LSM 存在的原因：把隨機寫轉成循序寫，繞過磁碟 seek 的物理瓶頸。
- 三層結構：MemTable（記憶體可寫）→ Immutable MemTable（凍結待 flush）→ L0/L1/L2... SSTable（磁碟不可變）。
- 寫放大（WA）：每個 byte 被 compaction 重複寫多次，Leveled Compaction 典型 10-50x。
- 讀放大（RA）：point query 最壞要查 MemTable + 每層一個 SSTable；Bloom filter 是主要緩解工具。
- 空間放大（SA）：tombstone 和舊版本在 compaction 前一直佔空間。
- RUM 猜想：R（讀開銷）、U（更新開銷）、M（記憶體開銷）三者不可同時最小化。
- B-tree vs LSM：B-tree 偏讀（in-place，點查快），LSM 偏寫（append-only，吞吐高）；兩者都是工具，看 workload 選擇。

---

## 自我檢核

1. 為什麼 HDD 的隨機寫比循序寫慢 100 倍？seek latency 從哪裡來？
2. MemTable 滿了之後的流程是什麼？「freeze」是什麼意思？
3. 寫放大 30x 是什麼意思？用戶寫 1GB，磁碟總共要寫多少？
4. 為什麼 L0 的讀比 L1 的讀慢？L0 和 L1 的結構差異在哪？
5. Tombstone 為什麼不能立刻釋放空間？什麼時候才真的釋放？
6. RUM 猜想告訴我們什麼？Leveled Compaction 在 RUM 三角的哪個角落？
7. 如果你有一個每秒寫入 10 萬筆、讀取 100 筆的 workload，你會選 B-tree 還是 LSM？為什麼？

---

## 延伸閱讀

1. **O'Neil et al. 1996 "The Log-Structured Merge-Tree"**（原始論文）
   讀 Section 1-2（動機與基本結構）。這是整個 LSM 家族的起點，作者解釋為什麼 B-tree 的 I/O cost 在大量小型更新時無法接受，以及「rolling merge」的原始設計。理解動機比理解實作更重要。

2. **RocksDB Wiki — "RocksDB Overview"**（`github.com/facebook/rocksdb/wiki`）
   看 Column Families、Compaction、Write Buffer 幾節。這是本章所有概念在生產系統的對應點：MemTable=WriteBuffer、Leveled/Tiered/FIFO Compaction 各自的 tradeoff 直接有文字說明，還附 RocksDB 作者的 benchmark 數據。

3. **《Database Internals》Part I Chapter 7（LSM Trees）**（Alex Petrov 著）
   和本章的結構對照閱讀。Petrov 的處理方式偏重 SSTable 格式和 merge iterator 的實作細節，是本章「原理」層次之後的下一步。特別注意他對 Tiered vs Leveled 的對比段落。

4. **LevelDB 原始碼 `db/version_set.cc`**（`github.com/google/leveldb`）
   從這個檔案進去看 `VersionSet::LogAndApply` 和 `Compaction` 類別。`version_set.cc` 是 LevelDB 的「大腦」，管理每個 level 有哪些 SSTable、compaction 選哪些檔案、manifest 怎麼更新。把本章的結構圖對著原始碼看，每個概念都有對應的資料結構。

---

→ [Ch 12 MemTable（skip list）](./12-memtable.md)
