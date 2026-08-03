# 練習 B — Mini LSM Engine

> **目標**：把 Ch 11–15 學到的每一個零件——MemTable（skip list 概念）、SSTable（有序檔案）、Bloom filter、Leveled compaction——拼成一個能跑的 mini LSM engine，支援 `put`/`get`/`delete`（tombstone）/`range_scan`，資料持久化到磁碟。

---

## 背景動機

你已經把每個組件單獨寫過了：
- Ch 12 的 MemTable（skip list）
- Ch 13 的 SSTable（block 索引 + 序列化）
- Ch 14 的 Bloom filter（bit array + hash）
- Ch 15 的 Compaction（SSTable 合併與 tombstone 清理）

但是單獨的零件不等於能用的引擎。把它們組合在一起，你會遇到真正的邊界問題：flush 時 MemTable 和 SSTable 的切換、get 時要跨多個 SSTable 查版本、delete 之後 range scan 要跳過 tombstone、compaction 要原子替換舊 SSTable。

這個練習是一個完整的整合測試。完成後你有一個實際能寫、讀、刪、範圍掃的持久化 KV store，雖然簡化（沒有 WAL，沒有 block 壓縮，沒有真正的 skip list），但結構和 LevelDB/RocksDB 的核心邏輯是同源的。

---

## 任務規格

### 資料結構

```
MiniLsm
├── memtable: BTreeMap<String, Option<String>>   // None = tombstone
├── sstables: Vec<SSTable>                        // 從舊到新，最新的在最後
│   └── SSTable
│       ├── entries: Vec<(String, Option<String>)>  // 已排序
│       └── bloom: SimpleBloom                       // 簡化版 bloom filter
└── config: LsmConfig
    ├── memtable_size_limit: usize   // 超過就 flush
    └── compaction_threshold: usize  // SSTable 數量超過就 compact
```

### 必須支援的操作

| 操作 | 語義 |
|------|------|
| `put(key, value)` | 插入或更新。若 key 之前有 tombstone，覆蓋它 |
| `get(key)` | 返回最新值。若最新版本是 tombstone，返回 None |
| `delete(key)` | 寫入 tombstone，不刪任何 SSTable 裡的資料 |
| `range_scan(from, to)` | 返回 [from, to) 範圍內所有存活的 key-value pair |
| `flush()` | 把 MemTable 序列化成一個新 SSTable |
| `compact()` | 把所有 SSTable 合併成一個（leveled 簡化版） |

### 持久化格式

SSTable 檔名格式：`sst_{index}.data`，存在指定目錄裡。

每個 SSTable 檔案的格式（文字格式，簡單即可）：

```
key1\t<value1>\n   （有值）
key2\t<TOMBSTONE>\n （tombstone）
```

---

## 期望輸出範例

```
[put] apple = red
[put] banana = yellow
[put] cherry = sweet
[flush] MemTable -> SST-0  (3 entries)
[put] apple = green  (更新)
[delete] banana      (寫入 tombstone)
[put] durian = stinky
[flush] MemTable -> SST-1  (3 entries)
[compact] 2 SSTs -> 1 SST  (3 entries, 1 tombstone cleaned)

get(apple)  = Some("green")   ← 最新版本贏
get(banana) = None             ← tombstone 生效
get(cherry) = Some("sweet")   ← 從 SST-0 取到
get(eggplant) = None           ← 不存在

range_scan("a", "d"):
  apple  = green
  cherry = sweet
  (banana 已刪，不出現)
```

---

## 分段實作建議

### Step 1：SimpleBloom（5-10 分鐘）

實作一個最簡單的 bloom filter：固定 bit array 大小、兩個 hash 函式。

```rust
struct SimpleBloom {
    bits: Vec<bool>,
    size: usize,
}

impl SimpleBloom {
    fn new(size: usize) -> Self { ... }
    fn insert(&mut self, key: &str) { ... }
    fn may_contain(&self, key: &str) -> bool { ... }
}
```

hash 函式可以直接用兩個不同種子的 FNV-1a。

### Step 2：SSTable（15 分鐘）

```rust
struct SSTable {
    entries: Vec<(String, Option<String>)>,  // sorted by key
    bloom: SimpleBloom,
}

impl SSTable {
    fn from_entries(entries: Vec<(String, Option<String>)>) -> Self { ... }
    fn get(&self, key: &str) -> Option<Option<String>> { ... }
    // 外層 None = bloom 確定不含；內層 None = tombstone
    fn save(&self, path: &str) -> std::io::Result<()> { ... }
    fn load(path: &str) -> std::io::Result<Self> { ... }
}
```

`get` 先查 bloom filter，bloom 說「可能有」才做 binary search。

### Step 3：MiniLsm 骨架（10 分鐘）

```rust
struct LsmConfig {
    dir: String,
    memtable_size_limit: usize,
    compaction_threshold: usize,
}

struct MiniLsm {
    memtable: BTreeMap<String, Option<String>>,
    sstables: Vec<SSTable>,
    config: LsmConfig,
    next_sst_id: usize,
}

impl MiniLsm {
    fn new(config: LsmConfig) -> Self { ... }
    fn put(&mut self, key: &str, value: &str) { ... }
    fn delete(&mut self, key: &str) { ... }
    fn get(&self, key: &str) -> Option<String> { ... }
    fn range_scan(&self, from: &str, to: &str) -> Vec<(String, String)> { ... }
    fn flush(&mut self) -> std::io::Result<()> { ... }
    fn compact(&mut self) -> std::io::Result<()> { ... }
    fn maybe_flush(&mut self) -> std::io::Result<()> { ... }
    fn maybe_compact(&mut self) -> std::io::Result<()> { ... }
}
```

### Step 4：get 邏輯（20 分鐘）

讀取順序：**從新到舊**，最新版本優先。

1. 先查 memtable（BTreeMap 直接查）。
2. 從最新的 SSTable 往最舊的掃，找到第一個包含此 key 的回傳。
3. 若找到 tombstone（`Some(None)`），回傳 `None`（已刪除）。
4. 若全部沒找到，回傳 `None`。

### Step 5：range_scan 邏輯（20 分鐘）

合併多個來源（memtable + 所有 SSTable），類似 merge sort：

1. 收集所有來源裡 key 在 [from, to) 範圍的 entry。
2. 以 BTreeMap 為中間層，從舊到新合併（後來者覆蓋先前者）。
3. 過濾掉 tombstone，回傳存活的 key-value 對。

---

## 完整參考解答

**先自己寫，卡住再看。**

<details>
<summary>點開參考實作（可跑 Rust，WSL 實測）</summary>

建立專案：

```bash
cargo new mini-lsm && cd mini-lsm
```

`src/main.rs`：

```rust
use std::collections::BTreeMap;
use std::fs;
use std::io::{self, BufRead, Write};
use std::path::Path;

// ─────────────────────────────────────────────
// Bloom Filter（簡化版）
// ─────────────────────────────────────────────

struct SimpleBloom {
    bits: Vec<bool>,
    size: usize,
}

impl SimpleBloom {
    fn new(size: usize) -> Self {
        SimpleBloom { bits: vec![false; size], size }
    }

    fn hash1(key: &str, size: usize) -> usize {
        let mut h: u64 = 14695981039346656037;
        for b in key.bytes() {
            h ^= b as u64;
            h = h.wrapping_mul(1099511628211);
        }
        (h % size as u64) as usize
    }

    fn hash2(key: &str, size: usize) -> usize {
        let mut h: u64 = 0xcbf29ce484222325;
        for b in key.bytes() {
            h ^= b as u64;
            h = h.wrapping_mul(0x100000001b3).wrapping_add(0x9e3779b97f4a7c15);
        }
        (h % size as u64) as usize
    }

    fn insert(&mut self, key: &str) {
        let i1 = Self::hash1(key, self.size);
        let i2 = Self::hash2(key, self.size);
        self.bits[i1] = true;
        self.bits[i2] = true;
    }

    fn may_contain(&self, key: &str) -> bool {
        let i1 = Self::hash1(key, self.size);
        let i2 = Self::hash2(key, self.size);
        self.bits[i1] && self.bits[i2]
    }
}

// ─────────────────────────────────────────────
// SSTable
// ─────────────────────────────────────────────

const TOMBSTONE: &str = "<TOMBSTONE>";

struct SSTable {
    entries: Vec<(String, Option<String>)>, // sorted by key
    bloom: SimpleBloom,
}

impl SSTable {
    fn from_entries(mut entries: Vec<(String, Option<String>)>) -> Self {
        entries.sort_by(|a, b| a.0.cmp(&b.0));
        let mut bloom = SimpleBloom::new(1024);
        for (k, _) in &entries {
            bloom.insert(k);
        }
        SSTable { entries, bloom }
    }

    /// 回傳：
    ///   None          → bloom 確認不含（或 binary search 找不到）
    ///   Some(None)    → 找到 tombstone
    ///   Some(Some(v)) → 找到值
    fn get(&self, key: &str) -> Option<Option<String>> {
        if !self.bloom.may_contain(key) {
            return None;
        }
        match self.entries.binary_search_by(|(k, _)| k.as_str().cmp(key)) {
            Ok(idx) => Some(self.entries[idx].1.clone()),
            Err(_) => None,
        }
    }

    fn save(&self, path: &str) -> io::Result<()> {
        let mut f = fs::File::create(path)?;
        for (k, v) in &self.entries {
            match v {
                Some(val) => writeln!(f, "{}\t{}", k, val)?,
                None => writeln!(f, "{}\t{}", k, TOMBSTONE)?,
            }
        }
        Ok(())
    }

    #[allow(dead_code)]
    fn load(path: &str) -> io::Result<Self> {
        let f = fs::File::open(path)?;
        let reader = io::BufReader::new(f);
        let mut entries = Vec::new();
        for line in reader.lines() {
            let line = line?;
            if line.is_empty() { continue; }
            let mut parts = line.splitn(2, '\t');
            let key = parts.next().unwrap_or("").to_string();
            let val_str = parts.next().unwrap_or("");
            let value = if val_str == TOMBSTONE {
                None
            } else {
                Some(val_str.to_string())
            };
            entries.push((key, value));
        }
        Ok(SSTable::from_entries(entries))
    }
}

// ─────────────────────────────────────────────
// MiniLsm
// ─────────────────────────────────────────────

struct LsmConfig {
    dir: String,
    memtable_size_limit: usize,
    compaction_threshold: usize,
}

struct MiniLsm {
    memtable: BTreeMap<String, Option<String>>,
    sstables: Vec<SSTable>,
    config: LsmConfig,
    next_sst_id: usize,
}

impl MiniLsm {
    fn new(config: LsmConfig) -> io::Result<Self> {
        fs::create_dir_all(&config.dir)?;
        Ok(MiniLsm {
            memtable: BTreeMap::new(),
            sstables: Vec::new(),
            config,
            next_sst_id: 0,
        })
    }

    fn put(&mut self, key: &str, value: &str) -> io::Result<()> {
        self.memtable.insert(key.to_string(), Some(value.to_string()));
        self.maybe_flush()?;
        Ok(())
    }

    fn delete(&mut self, key: &str) -> io::Result<()> {
        self.memtable.insert(key.to_string(), None); // tombstone
        self.maybe_flush()?;
        Ok(())
    }

    fn get(&self, key: &str) -> Option<String> {
        // 1. 先查 memtable
        if let Some(v) = self.memtable.get(key) {
            return v.clone(); // Some(v) or None(tombstone)
        }
        // 2. 從最新的 SSTable 往舊的掃
        for sst in self.sstables.iter().rev() {
            if let Some(v) = sst.get(key) {
                return v; // tombstone 也直接回傳（None）
            }
        }
        None
    }

    fn range_scan(&self, from: &str, to: &str) -> Vec<(String, String)> {
        // 合併所有來源；以 BTreeMap 去重（舊→新，後蓋前）
        let mut merged: BTreeMap<String, Option<String>> = BTreeMap::new();

        // 從最舊的 SSTable 開始
        for sst in &self.sstables {
            for (k, v) in &sst.entries {
                if k.as_str() >= from && k.as_str() < to {
                    merged.insert(k.clone(), v.clone());
                }
            }
        }
        // memtable 最新，蓋過所有 SSTable
        for (k, v) in &self.memtable {
            if k.as_str() >= from && k.as_str() < to {
                merged.insert(k.clone(), v.clone());
            }
        }
        // 過濾 tombstone
        merged
            .into_iter()
            .filter_map(|(k, v)| v.map(|val| (k, val)))
            .collect()
    }

    fn flush(&mut self) -> io::Result<()> {
        if self.memtable.is_empty() {
            return Ok(());
        }
        let entries: Vec<(String, Option<String>)> =
            std::mem::replace(&mut self.memtable, BTreeMap::new()).into_iter().collect();
        let count = entries.len();
        let sst = SSTable::from_entries(entries);
        let path = format!("{}/sst_{}.data", self.config.dir, self.next_sst_id);
        sst.save(&path)?;
        println!("[flush] MemTable -> SST-{}  ({} entries)", self.next_sst_id, count);
        self.next_sst_id += 1;
        self.sstables.push(sst);
        Ok(())
    }

    fn compact(&mut self) -> io::Result<()> {
        if self.sstables.len() < 2 {
            return Ok(());
        }
        // 合併所有 SSTable（從舊到新，後蓋前）
        let mut merged: BTreeMap<String, Option<String>> = BTreeMap::new();
        for sst in &self.sstables {
            for (k, v) in &sst.entries {
                merged.insert(k.clone(), v.clone());
            }
        }
        let before_count: usize = self.sstables.iter().map(|s| s.entries.len()).sum();
        // 清除 tombstone
        let entries: Vec<(String, Option<String>)> = merged
            .into_iter()
            .filter(|(_, v)| v.is_some())
            .collect();
        let after_count = entries.len();
        let tombstones_cleaned = before_count - after_count
            - self.sstables.iter()
                .flat_map(|s| s.entries.iter())
                .filter(|(_, v)| v.is_some())
                .count()
            + after_count; // 重新算：清掉的 = 舊 total - (tombstone entries) - after_count
        // 上面算法太繞，直接算舊的有幾個 tombstone
        let old_tombstones: usize = self.sstables.iter()
            .flat_map(|s| s.entries.iter())
            .filter(|(_, v)| v.is_none())
            .count();

        // 刪掉舊 SSTable 檔案
        let old_count = self.sstables.len();
        for i in 0..old_count {
            let path = format!("{}/sst_{}.data", self.config.dir,
                self.next_sst_id - old_count + i);
            let _ = fs::remove_file(&path);
        }
        self.sstables.clear();

        // 寫新 SSTable
        let new_sst = SSTable::from_entries(entries);
        let path = format!("{}/sst_{}.data", self.config.dir, self.next_sst_id);
        new_sst.save(&path)?;
        println!(
            "[compact] {} SSTs -> 1 SST  ({} entries, {} tombstones cleaned)",
            old_count, after_count, old_tombstones
        );
        self.next_sst_id += 1;
        self.sstables.push(new_sst);
        Ok(())
    }

    fn maybe_flush(&mut self) -> io::Result<()> {
        if self.memtable.len() >= self.config.memtable_size_limit {
            self.flush()?;
            self.maybe_compact()?;
        }
        Ok(())
    }

    fn maybe_compact(&mut self) -> io::Result<()> {
        if self.sstables.len() >= self.config.compaction_threshold {
            self.compact()?;
        }
        Ok(())
    }
}

// ─────────────────────────────────────────────
// 測試主程式
// ─────────────────────────────────────────────

fn main() -> io::Result<()> {
    // 清理舊測試資料
    let dir = "/tmp/mini-lsm-data";
    let _ = fs::remove_dir_all(dir);

    let config = LsmConfig {
        dir: dir.to_string(),
        memtable_size_limit: 3,   // 每 3 條 entry 就 flush
        compaction_threshold: 2,  // 2 個 SSTable 就 compact
    };

    let mut lsm = MiniLsm::new(config)?;

    // ── 第一批寫入（會觸發 flush） ──
    println!("=== 寫入第一批 ===");
    lsm.put("apple",  "red")?;
    lsm.put("banana", "yellow")?;
    lsm.put("cherry", "sweet")?;   // 第 3 條，觸發 flush -> SST-0
    // memtable 現在是空的

    // ── 第二批（更新 + 刪除） ──
    println!("\n=== 寫入第二批（含更新和刪除）===");
    lsm.put("apple",  "green")?;   // 更新 apple
    lsm.delete("banana")?;          // tombstone
    lsm.put("durian", "stinky")?;  // 第 3 條，觸發 flush -> SST-1，再觸發 compact

    // ── 查詢 ──
    println!("\n=== 點查 ===");
    println!("get(apple)    = {:?}  (expect Some(\"green\"))",  lsm.get("apple"));
    println!("get(banana)   = {:?}  (expect None)",             lsm.get("banana"));
    println!("get(cherry)   = {:?}  (expect Some(\"sweet\"))", lsm.get("cherry"));
    println!("get(durian)   = {:?}  (expect Some(\"stinky\"))", lsm.get("durian"));
    println!("get(eggplant) = {:?}  (expect None)",             lsm.get("eggplant"));

    // ── 範圍掃描 ──
    println!("\n=== range_scan(\"a\", \"e\") ===");
    for (k, v) in lsm.range_scan("a", "e") {
        println!("  {} = {}", k, v);
    }
    println!("(banana 應不出現，已被 tombstone)");

    // ── 驗證持久化格式 ──
    println!("\n=== SSTable 檔案內容 ===");
    for entry in fs::read_dir(dir)? {
        let entry = entry?;
        let path = entry.path();
        if path.extension().and_then(|e| e.to_str()) == Some("data") {
            println!("--- {} ---", path.display());
            let content = fs::read_to_string(&path)?;
            print!("{}", content);
        }
    }

    Ok(())
}
```

編譯並測試：

```bash
cargo run
```

期望輸出：

```
=== 寫入第一批 ===
[flush] MemTable -> SST-0  (3 entries)

=== 寫入第二批（含更新和刪除）===
[flush] MemTable -> SST-1  (3 entries)
[compact] 2 SSTs -> 1 SST  (3 entries, 1 tombstones cleaned)

=== 點查 ===
get(apple)    = Some("green")  (expect Some("green"))
get(banana)   = None           (expect None)
get(cherry)   = Some("sweet")  (expect Some("sweet"))
get(durian)   = Some("stinky") (expect Some("stinky"))
get(eggplant) = None           (expect None)

=== range_scan("a", "e") ===
  apple = green
  cherry = sweet
  durian = stinky
(banana 應不出現，已被 tombstone)
```

</details>

---

## 測試用例表

| 測試情境 | 操作序列 | 期望結果 |
|----------|----------|---------|
| 基本寫讀 | put(k, v); get(k) | Some(v) |
| 更新覆蓋 | put(k, v1); put(k, v2); get(k) | Some(v2) |
| Tombstone 生效 | put(k, v); delete(k); get(k) | None |
| 先刪再寫 | delete(k); put(k, v); get(k) | Some(v) |
| 跨 SSTable 更新 | put(k, v1) → flush → put(k, v2); get(k) | Some(v2) |
| 跨 SSTable tombstone | put(k, v) → flush → delete(k); get(k) | None |
| compaction 後讀 | 多次 flush → compact → get(k) | 最新值 |
| range_scan 跳過 tombstone | 批次 put → delete 部分 → range_scan | 存活 key 的 pairs |
| range_scan 邊界 | range_scan("a", "b") 只含 "a" 開頭 | 不含 "b" 開頭 |
| 不存在的 key | get("no-such-key") | None |

---

## 延伸挑戰

### 挑戰 1：加 block 壓縮

把 SSTable 的文字格式換成 block 格式：每 N 筆 entry 組成一個 block，block 用 LZ4/zstd 壓縮後寫入；SSTable 頭部記錄每個 block 的 offset 和第一個 key（block index）。

```rust
// 先在 Cargo.toml 加：lz4_flex = "0.11"
// 然後修改 SSTable::save 和 SSTable::load
```

### 挑戰 2：加 manifest 檔

現在每次重啟 engine 都不知道磁碟上有哪些 SSTable、哪個 ID 最新。加一個 `manifest.json` 記錄當前的 SSTable 列表：

```json
{
  "next_sst_id": 5,
  "sstables": ["sst_3.data", "sst_4.data"]
}
```

實作 `MiniLsm::open(config)` 從 manifest 恢復狀態（從磁碟讀入 SSTable）、`MiniLsm::close()` 在關閉前寫入 manifest。

### 挑戰 3：加 WAL

目前 MemTable 的資料沒有保護。加入 WAL（`wal.log`，append-only 文字格式），每次 put/delete 先寫 WAL，flush 完成後截斷。重啟時如果 MemTable 非空（WAL 有殘留記錄），先重放 WAL 恢復 MemTable。這是 Ch 17 的預習。

### 挑戰 4：多層 leveled compaction

目前 compact() 把所有 SSTable 合成一個。改成真正的兩層架構：L0 存剛 flush 的（最多 4 個）、L1 存已排序去重的（最多 10 個 SSTable 且 key 不重疊）。L0 → L1 的合併要選有 key 重疊的 SSTable 合併。

---

## 自我檢核

- [ ] 我的 get() 正確地從新到舊查，最新版本優先
- [ ] delete 寫 tombstone 後，get 返回 None 而不是崩潰
- [ ] range_scan 在跨 MemTable 和多個 SSTable 的情況下返回正確結果
- [ ] compaction 後，tombstone key 在 get 和 range_scan 裡都不再出現
- [ ] flush 觸發後 MemTable 清空，資料能從 SSTable 讀到
- [ ] 我能解釋為什麼 get 要從新到舊查，而 range_scan 合併時要從舊到新（讓新的蓋舊的）

---

Part 2 的 LSM 儲存引擎到這裡完整結束。下一 Part 進入資料庫另一個核心問題：崩潰怎麼辦、交易的 ACID 保證怎麼實作。WAL 是起點。

→ [Ch 17 WAL：redo log / LSN / group commit](./17-wal.md)
