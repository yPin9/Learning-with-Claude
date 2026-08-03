# Ch 13 — SSTable（Sorted String Table）

> **目標**：理解 SSTable 的不可變檔案格式設計——data block / index block / footer 三層結構——動手實作一個能實際讀寫的 Rust SSTableWriter 與 SSTableReader，走完「MemTable flush → SSTable 寫入 → binary search 讀取」的完整路徑，並預覽 block 壓縮與 Bloom filter 的整合接口。

---

## 為什麼需要 SSTable？

Ch 12 的 MemTable 解決了記憶體寫入問題：Skip List 保持有序，flush 時只要線性迭代就能輸出排序好的 key-value 串流。這個排序好的串流寫到磁碟就是 **SSTable（Sorted String Table）**。

SSTable 的設計目標：
1. **寫入一次，永不修改**。把 MemTable flush 到磁碟是循序寫入，沒有隨機 I/O。
2. **讀取時能二元搜尋**。不是從頭掃到尾；用 index block 定位到正確的 4KB data block，再在 block 內線性掃描。
3. **可壓縮**。每個 block 獨立壓縮（LZ4/Snappy），不影響其他 block 的讀取。

SSTable 是 LevelDB、RocksDB、Cassandra、HBase 等系統的共同基石。名字來自 BigTable 論文（Chang et al., 2006, Section 5.3）。

---

## 直觀：SSTable 是個分層的唯讀字典

想像一本厚厚的紙本字典：

```
字典前面的索引頁（index block）：
  A  ...  第 1 頁
  G  ...  第 50 頁
  M  ...  第 100 頁
  S  ...  第 150 頁

正文（data blocks）：
  第 1-49 頁：A 開頭的詞條，按字母排列
  第 50-99 頁：G 開頭的詞條
  ...
```

查 "mango"：先翻索引找到 "M 從第 100 頁開始"，再翻到第 100 頁往後掃描。不需要從第 1 頁掃到底。

SSTable 的 index block 就是這個索引頁；data block 就是正文頁；footer 是封底印的「索引頁在哪頁」那行小字。

---

## SSTable 檔案格式

```
位元組 offset 0
┌───────────────────────────────────────────────────────┐
│  Data Block 0                                         │
│  ┌─────────────────────────────────────────────────┐  │
│  │ block_len: u32 LE  (4 bytes)                    │  │
│  │ [key_len: u32][key bytes][val_len: u32][val bytes] │  │
│  │ [key_len: u32][key bytes][val_len: u32][val bytes] │  │
│  │  ...  (填滿到 ~4KB 為止)                        │  │
│  └─────────────────────────────────────────────────┘  │
├───────────────────────────────────────────────────────┤
│  Data Block 1  (block_len + 內容)                    │
├───────────────────────────────────────────────────────┤
│  Data Block 2  ...                                    │
├───────────────────────────────────────────────────────┤
│  Index Block                                          │
│  ┌─────────────────────────────────────────────────┐  │
│  │ index_block_len: u32 LE  (length prefix)        │  │
│  │ num_entries: u32 LE                             │  │
│  │ [key_len: u32][first_key][block_offset: u64 LE] │  │  ← entry 0
│  │ [key_len: u32][first_key][block_offset: u64 LE] │  │  ← entry 1
│  │  ...                                            │  │
│  └─────────────────────────────────────────────────┘  │
├───────────────────────────────────────────────────────┤
│  Footer  (固定 16 bytes)                              │
│  ┌──────────────────────────────┐                     │
│  │ index_block_offset: u64 LE   │                     │
│  │ magic_number: u64 LE         │  0x00_DB_DB_DB...   │
│  └──────────────────────────────┘                     │
└───────────────────────────────────────────────────────┘
                              ↑ 檔案尾端（EOF）
```

幾個設計決定值得說明：

- **Footer 固定 16 bytes，置於檔案最尾**。Reader 只需要 `seek(End, -16)` 就能拿到 index 的位置，不需要從頭讀。
- **Index block 用 length prefix 保護**。若 index 也固定大小，則 data block 數量就受限；用 length prefix 可以任意多個 data block。
- **每個 data block 前 4 bytes 是 block_len**。Reader 只 seek 到 block offset，讀 4 bytes 知道要讀多少，再讀 block 內容。不需要從 index 再記錄 block size（雖然有些實作選擇記）。

---

## 寫入路徑（flush MemTable → SSTable）

```
MemTable 有序迭代（Level 0 walk）
          │
          ▼
  呼叫 writer.add(key, value)
          │
          ├─ current_block 未滿 4KB
          │        └→ encode_bytes(key) + encode_bytes(value) 進 current_block
          │
          └─ current_block 加入此 entry 會超過 4KB
                   └→ flush_block()
                        ├─ 把 current_block 長度前綴 + 內容寫進檔案
                        ├─ 記錄 (first_key, block_start_offset) 進 index[]
                        └─ 清空 current_block，繼續 add()
          │
          ▼ (所有 entry 加完)
  writer.finish()
    ├─ flush_block()（flush 最後一個未滿的 block）
    ├─ 寫 Index Block：length + num_entries + entries
    └─ 寫 Footer：index_block_offset + magic
```

關鍵規則：**key-value pair 不能跨 block 分割**。若加入一筆 entry 會讓 current_block 超過 BLOCK_SIZE，就先 flush 再開新 block 放這筆 entry。

---

## 讀取路徑（get by key）

```
get("date")
      │
      ▼
  open SSTable 檔案
      │
      ▼
  seek(End, -16) → 讀 Footer 16 bytes
      │   取出 index_block_offset
      ▼
  seek(index_block_offset) → 讀 index_len (u32)
  → 讀 index_buf[index_len bytes]
  → parse: num_entries, [(first_key, block_offset), ...]
      │
      ▼
  binary search：找最後一個 first_key <= "date" 的 entry
      │   假設找到 entry[1]: first_key="cherry", offset=X
      ▼
  seek(X) → 讀 block_len (u32)
  → 讀 block_data[block_len bytes]
      │
      ▼
  線性掃描 block_data：
    entry: key="cherry", value="red small"  → 不符
    entry: key="date",   value="sweet"      → 命中！
      │
      ▼
  return Some("sweet")
```

Binary search 用的是 `partition_point`：找第一個 `first_key > target` 的位置，往左退一格就是「可能包含 target 的那個 block」。

---

## Rust 實作：SSTableWriter（實測通過 WSL）

```rust
// 實測通過 (WSL) — rustc 1.97.1
use std::convert::TryInto;
use std::fs::{File, OpenOptions};
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::Path;

const MAGIC: u64    = 0x00_DB_DB_DB_DB_DB_DB_DB;
const BLOCK_SIZE: usize = 4096; // 4 KB per data block
const FOOTER_SIZE: i64  = 16;   // index_offset(8) + magic(8)

/// 長度前綴編碼：[u32 LE 長度][bytes]
fn encode_bytes(buf: &mut Vec<u8>, bytes: &[u8]) {
    let len = bytes.len() as u32;
    buf.extend_from_slice(&len.to_le_bytes());
    buf.extend_from_slice(bytes);
}

/// 從 data 的 offset 處解碼一個長度前綴 bytes slice
fn decode_bytes<'a>(data: &'a [u8], offset: &mut usize) -> Option<&'a [u8]> {
    if *offset + 4 > data.len() { return None; }
    let len = u32::from_le_bytes(data[*offset..*offset+4].try_into().unwrap()) as usize;
    *offset += 4;
    if *offset + len > data.len() { return None; }
    let s = &data[*offset..*offset + len];
    *offset += len;
    Some(s)
}

pub struct SSTableWriter {
    file: File,
    current_block: Vec<u8>,           // 目前 data block 累積的 bytes
    current_block_first_key: Vec<u8>, // 此 block 第一個 key（存進 index 用）
    current_offset: u64,              // 已寫到檔案的 bytes 數
    index: Vec<(Vec<u8>, u64)>,       // (first_key, block_start_offset)
}

impl SSTableWriter {
    pub fn create(path: &Path) -> io::Result<Self> {
        let file = OpenOptions::new()
            .write(true).create(true).truncate(true)
            .open(path)?;
        Ok(SSTableWriter {
            file,
            current_block: Vec::new(),
            current_block_first_key: Vec::new(),
            current_offset: 0,
            index: Vec::new(),
        })
    }

    /// 加入一筆 entry（caller 保證 key 遞增有序）
    pub fn add(&mut self, key: &[u8], value: &[u8]) -> io::Result<()> {
        let entry_size = 4 + key.len() + 4 + value.len();
        // block 非空且加入此 entry 會超過 BLOCK_SIZE → 先 flush
        if !self.current_block.is_empty()
            && self.current_block.len() + entry_size > BLOCK_SIZE
        {
            self.flush_block()?;
        }
        // block 為空 → 記下此 block 的 first_key
        if self.current_block.is_empty() {
            self.current_block_first_key = key.to_vec();
        }
        encode_bytes(&mut self.current_block, key);
        encode_bytes(&mut self.current_block, value);
        Ok(())
    }

    /// 把 current_block 寫進檔案，記入 index
    fn flush_block(&mut self) -> io::Result<()> {
        if self.current_block.is_empty() { return Ok(()); }
        let block_offset = self.current_offset;
        let first_key = std::mem::take(&mut self.current_block_first_key);
        let block_data = std::mem::take(&mut self.current_block);
        let block_len = block_data.len() as u32;
        // 寫 length prefix + block 內容
        self.file.write_all(&block_len.to_le_bytes())?;
        self.file.write_all(&block_data)?;
        self.current_offset += 4 + block_len as u64;
        // 記入 index
        self.index.push((first_key, block_offset));
        Ok(())
    }

    /// 寫完所有 entry 後呼叫：flush 剩餘 block → 寫 index → 寫 footer
    pub fn finish(mut self) -> io::Result<()> {
        // 1. flush 最後一個（可能未滿的）block
        self.flush_block()?;

        // 2. 序列化 index block
        let index_offset = self.current_offset;
        let mut index_buf: Vec<u8> = Vec::new();
        index_buf.extend_from_slice(&(self.index.len() as u32).to_le_bytes());
        for (first_key, block_offset) in &self.index {
            encode_bytes(&mut index_buf, first_key);
            index_buf.extend_from_slice(&block_offset.to_le_bytes());
        }

        // 3. 寫 index block（length prefix + 內容）
        self.file.write_all(&(index_buf.len() as u32).to_le_bytes())?;
        self.file.write_all(&index_buf)?;

        // 4. 寫 footer（16 bytes，固定）
        self.file.write_all(&index_offset.to_le_bytes())?;
        self.file.write_all(&MAGIC.to_le_bytes())?;

        self.file.flush()?;
        Ok(())
    }
}
```

---

## Rust 實作：SSTableReader（實測通過 WSL）

```rust
// 實測通過 (WSL) — rustc 1.97.1
pub struct SSTableReader {
    file: File,
    index: Vec<(Vec<u8>, u64)>, // (first_key, data_block_offset)，已排序
}

impl SSTableReader {
    pub fn open(path: &Path) -> io::Result<Self> {
        let mut file = File::open(path)?;

        // ── 步驟 1：讀 footer ────────────────────────────────────────────
        let file_size = file.seek(SeekFrom::End(0))?;
        if file_size < FOOTER_SIZE as u64 {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "file too small"));
        }
        file.seek(SeekFrom::End(-FOOTER_SIZE))?;
        let mut footer = [0u8; 16];
        file.read_exact(&mut footer)?;
        let index_offset = u64::from_le_bytes(footer[0..8].try_into().unwrap());
        let magic        = u64::from_le_bytes(footer[8..16].try_into().unwrap());
        if magic != MAGIC {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "bad magic"));
        }

        // ── 步驟 2：讀 index block ──────────────────────────────────────
        file.seek(SeekFrom::Start(index_offset))?;
        let mut len_buf = [0u8; 4];
        file.read_exact(&mut len_buf)?;
        let index_len = u32::from_le_bytes(len_buf) as usize;
        let mut index_buf = vec![0u8; index_len];
        file.read_exact(&mut index_buf)?;

        // ── 步驟 3：parse index entries ─────────────────────────────────
        let mut index: Vec<(Vec<u8>, u64)> = Vec::new();
        let mut off = 0usize;
        let num_entries =
            u32::from_le_bytes(index_buf[off..off+4].try_into().unwrap()) as usize;
        off += 4;
        for _ in 0..num_entries {
            let key = decode_bytes(&index_buf, &mut off)
                .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "bad index key"))?
                .to_vec();
            if off + 8 > index_buf.len() {
                return Err(io::Error::new(io::ErrorKind::InvalidData, "bad index offset"));
            }
            let block_off = u64::from_le_bytes(index_buf[off..off+8].try_into().unwrap());
            off += 8;
            index.push((key, block_off));
        }

        Ok(SSTableReader { file, index })
    }

    pub fn get(&mut self, target: &[u8]) -> io::Result<Option<Vec<u8>>> {
        if self.index.is_empty() { return Ok(None); }

        // Binary search：找最後一個 first_key <= target 的 index entry
        // partition_point 回傳第一個「first_key > target」的位置
        let pos = self.index.partition_point(|(fk, _)| fk.as_slice() <= target);
        if pos == 0 {
            // 所有 first_key 都 > target，key 不可能在此 SSTable
            return Ok(None);
        }
        let block_offset = self.index[pos - 1].1;

        // ── 讀 data block ───────────────────────────────────────────────
        self.file.seek(SeekFrom::Start(block_offset))?;
        let mut len_buf = [0u8; 4];
        self.file.read_exact(&mut len_buf)?;
        let block_len = u32::from_le_bytes(len_buf) as usize;
        let mut block_data = vec![0u8; block_len];
        self.file.read_exact(&mut block_data)?;

        // ── 線性掃描 block ──────────────────────────────────────────────
        let mut scan_off = 0;
        while scan_off < block_data.len() {
            let key = match decode_bytes(&block_data, &mut scan_off) {
                Some(k) => k,
                None => break,
            };
            let value = match decode_bytes(&block_data, &mut scan_off) {
                Some(v) => v,
                None => break,
            };
            if key == target {
                return Ok(Some(value.to_vec()));
            }
        }
        Ok(None)
    }
}
```

---

## 完整測試（實測通過 WSL）

```rust
// 實測通過 (WSL) — rustc 1.97.1
// 執行輸出：
//   Write done.
//   banana = "yellow fruit"
//   date = "sweet"
//   apple = "red fruit"
//   fig = None (correct, not in SSTable)
//   elderberry = "purple"
//   All assertions passed!
fn main() {
    let path = Path::new("/tmp/test_sstable.sst");

    // ── 寫入（keys 必須有序，caller 負責排序）──────────────────────────
    let mut w = SSTableWriter::create(path).unwrap();
    let entries: &[(&[u8], &[u8])] = &[
        (b"apple",      b"red fruit"),
        (b"banana",     b"yellow fruit"),
        (b"cherry",     b"red small"),
        (b"date",       b"sweet"),
        (b"elderberry", b"purple"),
    ];
    for (k, v) in entries {
        w.add(k, v).unwrap();
    }
    w.finish().unwrap();
    println!("Write done.");

    // ── 讀取 ────────────────────────────────────────────────────────────
    let mut r = SSTableReader::open(path).unwrap();

    let v = r.get(b"banana").unwrap();
    assert_eq!(v.as_deref(), Some(b"yellow fruit" as &[u8]));

    let v = r.get(b"date").unwrap();
    assert_eq!(v.as_deref(), Some(b"sweet" as &[u8]));

    let v = r.get(b"apple").unwrap();
    assert_eq!(v.as_deref(), Some(b"red fruit" as &[u8]));

    // 不存在的 key 應回傳 None
    let v = r.get(b"fig").unwrap();
    assert_eq!(v, None);

    let v = r.get(b"elderberry").unwrap();
    assert_eq!(v.as_deref(), Some(b"purple" as &[u8]));

    println!("All assertions passed!");
}
```

---

## SSTable vs B-Tree Page 比較

| 比較維度         | SSTable                              | B-Tree Page                            |
|------------------|--------------------------------------|----------------------------------------|
| **可變性**       | 完全不可變，寫完即封存               | 可原地修改，支援 in-place update       |
| **寫入模式**     | 循序批次寫入（flush 時一次到底）     | 隨機寫入，每次 update 改一個 page      |
| **讀取模式**     | index binary search + block 線性掃  | 樹狀路徑 B(log N) 層 random I/O       |
| **空間效率**     | 高（沒有碎片、page 填充率接近 100%）| 中（page 平均填充率 ~50-70%）          |
| **是否需要 compaction** | 需要（舊版本/tombstone 累積）  | 不需要（in-place 更新直接覆蓋）        |
| **循序掃描**     | 極快（檔案循序讀）                   | 中（需要遍歷 B-Tree leaf 層）          |

---

## 常見陷阱

**1. Footer seek 算錯 offset**

最常見的 off-by-one：`seek(End, -8)` 而不是 `-16`。Footer 有 16 bytes（u64 + u64），要 seek 到 `file_size - 16`。若 Footer 格式改動（例如加一個 CRC32 變成 20 bytes），全部 reader 都要改。建議把 FOOTER_SIZE 定成常數，在 reader 和 writer 兩邊引用同一個常數。

```rust
// 錯誤：只跳 8 bytes，讀到的是 magic，不是 index_offset
file.seek(SeekFrom::End(-8))?;

// 正確
file.seek(SeekFrom::End(-(FOOTER_SIZE)))?;  // FOOTER_SIZE = 16
```

**2. key-value pair 不能跨 block 分割**

block 是隨機存取的單位：reader seek 到 block_offset，讀 block_len bytes，然後線性掃描。若一個 entry 橫跨兩個 block，reader 讀完第一個 block 只會看到半個 entry，parse 失敗。

規則：**判斷是否 overflow 要用「加入前」的 current_block.len() + entry_size 比較，不是「加入後」**。若 current_block 已空（block 剛剛 flush），不管 entry 多大都要放進去——否則會無限迴圈。

```rust
// 正確：block 非空 AND 加入後超過限制 → 先 flush
if !self.current_block.is_empty()
    && self.current_block.len() + entry_size > BLOCK_SIZE
{
    self.flush_block()?;
}
// flush 之後 current_block 是空的，無條件加入 entry
```

**3. Endianness 要全程一致（用 LE）**

Rust 的整數預設沒有固定 byte order。`u64::to_le_bytes()` 和 `u64::from_le_bytes()` 是明確 LE；直接用 `to_ne_bytes()`（native endian）在 x86 開發機是 LE，但部署到 big-endian 平台讀就爆。

資料庫實作慣例選 **little-endian**（LevelDB/RocksDB 都選 LE），並且 magic number 裡通常會埋一個非對稱的 byte 序列讓 endianness 錯誤時能明顯察覺。

**4. Tombstone 不等於 key 不存在**

SSTable 是不可變的；刪除一個 key 的方式是寫入一個特殊的「tombstone」entry。tombstone 通常是 value 為空 bytes，或者用一個特殊的 value tag（例如第一個 byte = 0x01 = TYPE_DELETION）。

Reader 的 `get()` 看到 tombstone 要回傳 `Deleted`，而不是 `None`：

```rust
// 未編譯驗証
pub enum GetResult {
    Found(Vec<u8>),
    Deleted,      // tombstone：key 曾存在但已刪除
    NotFound,     // key 根本不在此 SSTable
}
```

compaction 才負責把 tombstone 清掉（掃描所有 SSTable，如果 tombstone 比所有包含同一 key 的舊版本都新，就可以丟棄）。

**5. SSTable 命名要用單調遞增序號**

LSM-Tree 同時有多個 SSTable（L0 層可能有 8 個，L1 有更多）。讀取路徑要按「新→舊」順序查 SSTable，因為新的版本有更高優先權。

命名慣例：`{level}-{sequence_number}.sst`，例如 `0-00042.sst`。sequence number 單調遞增，保證命名排序 = 時間順序。LevelDB 用一個全局的 `next_file_number` 原子計數器。

---

## 進階：Block 壓縮預覽

每個 data block 在寫入磁碟前可以單獨壓縮。SSTable 格式的優勢在於 block 邊界明確（有 block_len），壓縮解壓縮不影響其他 block：

```
Data Block（壓縮前）：原始 key/value bytes
    │
    ▼
LZ4_compress() 或 snappy::compress()
    │
    ▼
寫入檔案：[compressed_len: u32][compression_type: u8][compressed_bytes]
```

reader 讀到 block 後：
1. 看 `compression_type`：0x00 = 無壓縮，0x01 = Snappy，0x02 = LZ4。
2. 依類型解壓縮到 decompressed buffer。
3. 照常線性掃描。

LevelDB 和 RocksDB 都支援 per-block 壓縮。RocksDB 預設用 Snappy，可以改成 LZ4（速度快）或 Zstd（壓縮率高）。

---

## 進階：Bloom Filter 整合預覽

每個 SSTable 除了 index block 之外，還帶一個 **Bloom filter**（Ch 14 詳述）。Bloom filter 可以用 O(1) 時間回答「key 一定不在這個 SSTable 嗎？」

沒有 Bloom filter 的 LSM-Tree 讀一個不存在的 key，要從 L0 最新的 SSTable 一路查到最舊的。有 Bloom filter，通常只需要查幾個：

```
get("nonexistent_key")
    │
    ▼  對每個 SSTable（從新到舊）
    ├─ bloom_filter.may_contain("nonexistent_key") → false
    │      └→ 跳過此 SSTable（不做任何 I/O）
    ├─ bloom_filter.may_contain("nonexistent_key") → false
    │      └→ 跳過
    └─ ...
    ▼ 全部跳過 → return NotFound
```

Bloom filter 的 bytes 和 index block 一樣，存在 SSTable 的 meta block 區域，footer 裡記錄 meta block 的 offset。Ch 14 會實作。

---

## 本章重點整理

- SSTable 是 MemTable flush 到磁碟的產物：寫入一次，永不修改，key 有序。
- 檔案結構三層：data blocks（length-prefixed key/value entries）→ index block（first_key + block_offset per block）→ footer（16 bytes：index_offset + magic）。
- 寫入：累積 entry 到 current_block，超過 BLOCK_SIZE 就 flush。最後寫 index block 和 footer。
- 讀取：seek End-16 讀 footer → 拿 index_offset → 讀 index block → binary search → 讀 target data block → 線性掃描。
- key/value pair 不能跨 block 分割；endianness 要一致（LE）；tombstone 和 key 不存在是不同語義。
- SSTable 命名用 `{level}-{sequence_number}.sst`，序號單調遞增保證新舊順序。
- Block 壓縮（LZ4/Snappy）和 Bloom filter 是兩個重要的後續整合點。

---

## 自我檢核

1. Reader 如何只靠 `seek(End, -16)` 就找到 index block？如果 Footer 格式改成 24 bytes（多了一個 CRC32），要改哪裡？
2. `partition_point` 回傳的 `pos` 代表什麼？為什麼要用 `pos - 1`，而不是 `pos`，來選取 data block？
3. 一個 entry 大小是 200 bytes，BLOCK_SIZE 是 4096 bytes，但 current_block 已有 3980 bytes。下一步是什麼？flush 之後 current_block_first_key 如何設定？
4. 刪除 key "foo" 時，SSTable 裡實際存了什麼？compaction 什麼時候可以把它清掉？
5. 有兩個 SSTable：`0-00005.sst` 和 `0-00003.sst` 都包含 key "bar"。讀取時應該用哪個版本？

---

## 延伸閱讀

1. **LevelDB `table/table_builder.cc` 與 `table/table.cc`** — 正典 SSTable 實作，不到 600 行。重點看 block restart points（每隔 K 個 entry 存一個「重啟點」來減少 key prefix 壓縮的解碼成本）以及 `BlockBuilder::Add()` 的 key 前綴壓縮邏輯。
2. **RocksDB Wiki "SST File Format"** (`github.com/facebook/rocksdb/wiki/Rocksdb-BlockBasedTable-Format`) — 比 LevelDB 更細的格式說明，含 index 類型（binary search index vs hash index）、compression 欄位、checksum 選項（CRC32/xxHash）。
3. **《Database Internals》Part I, Ch. 7（Alex Petrov）** — log-structured storage 一章完整討論 SSTable 格式選擇的 trade-off，包括為什麼 index block 比整個 B-Tree index 更省空間、以及 merge 時的 sorted run 語義。
4. **BigTable paper（Chang et al., 2006）Section 5.3** — SSTable 這個名字的出處。原文只有 3 段，但解釋了 SSTable 作為 immutable sorted map 的核心語義，以及 Bloom filter 如何讓 non-existent key lookup 變成 O(1)。

---

→ [Ch 14 Bloom Filter](./14-bloom-filter.md)
