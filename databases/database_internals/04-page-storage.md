# Ch 4 — 頁面式儲存與 Slotted Page

> **目標**：理解資料庫為什麼以固定大小的 page 為 I/O 單位、slotted page 如何在一個 4 KB 的記憶體塊裡存放變長 record、以及如何用 Rust 實作 insert/get。這章是後面 buffer pool 與 B+tree 的地基——沒搞懂 page 內部結構，就沒辦法理解 node split 在操作什麼。

---

## 為什麼以 page 為單位？

磁碟不是位元組定址的記憶體。HDD 最小讀寫單位是磁區（sector，通常 512 B 或 4 KB）；SSD 的 flash page 通常是 4 KB，抹除單位（block）是 256 KB 到 1 MB。核心的 page cache 也以 4 KB 為單位管理記憶體。

如果資料庫直接以位元組讀寫，會有兩個問題：
1. **讀放大（read amplification）**：想讀 1 個 uint32，磁碟至少給你一個 sector；你沒辦法比 sector 更小。
2. **管理複雜度爆炸**：每個 record 大小不一，怎麼追蹤哪些位元組是空的？

解法：宣告一個固定大小的「頁面（page）」，讓 page 成為資料庫與磁碟之間的原子交換單位。所有讀寫都以整頁為單位，上層邏輯只需要管理 page id——不再需要知道它在磁碟的哪個位元組偏移。

常見 page size：
- SQLite：1 KB–64 KB，預設 4 KB
- Postgres：8 KB（固定）
- InnoDB：16 KB（固定）

page size 越大，fanout（B+tree 每個節點能放幾個 key）越大，樹越矮，但每次 I/O 搬的資料也越多，冷資料被迫一起讀進來的機率越高。這是一個設計取捨，不存在完美答案。

---

## 先建立直覺：一個 page 長什麼樣子

```
┌──────────────────────────────────────────────────────┐  offset 0
│  Header（固定大小，12 B）                            │
│  page_id(4) | num_slots(2) | free_start(2)           │
│  free_end(2) | flags(2)                              │
├──────────────────────────────────────────────────────┤  offset 12
│  Slot 0: (offset=4068, len=12)   ← 4 bytes           │
│  Slot 1: (offset=4055, len=13)   ← 4 bytes           │
│  Slot 2: (offset=4043, len=12)   ← 4 bytes           │
│  ...                                                 │
├──────────────────────────────────────────────────────┤  free_start ↓
│                                                      │
│              F  R  E  E   S  P  A  C  E              │
│                                                      │
├──────────────────────────────────────────────────────┤  free_end ↑
│  Cell 2: "slotted page" (12 bytes)    ← grow ↑       │
│  Cell 1: "rust database" (13 bytes)                  │
│  Cell 0: "hello, world" (12 bytes)                   │
└──────────────────────────────────────────────────────┘  offset 4096
```

**Slot array 從前往後長，Cell 從後往前長，兩者在中間相遇就是 page full。**

這個設計叫做 **slotted page**，由 Stonebraker 等人在 1970s 的 Ingres/System R 奠定，到今天 Postgres heap tuple 還在用同一個概念。

---

## 底層機制：各欄位意義

### Header（12 bytes）

| 欄位 | 型別 | 說明 |
|------|------|------|
| `page_id` | u32 | 這個 page 在檔案中的邏輯編號。`file_offset = page_id * PAGE_SIZE` |
| `num_slots` | u16 | 目前 slot array 的項目數 |
| `free_start` | u16 | slot array 結尾（下一個可寫 slot 的位置） |
| `free_end` | u16 | cell 區開頭（下一個 cell 從這裡往前寫） |
| `flags` | u16 | 保留位，B+tree 會用 bit0 區分 leaf/internal |

可用空間 = `free_end - free_start`。插入一個長度 `n` 的 record，需要消耗 `4 + n` bytes（4 bytes 的 slot entry + n bytes 的 cell data）。

### Slot entry（4 bytes）

每個 slot = `(offset: u16, len: u16)`。
- `offset`：cell 資料在 page 內的起始位置
- `len`：cell 資料的長度（bytes）
- 刪除時可將 `len` 設為 0，製造 tombstone；`offset=0, len=0` 代表 slot 已刪除

Slot id 是穩定的——即使 page 內部做 compaction（把 cell 往後移填補空洞），slot id 不變，外部只需要保存 `(page_id, slot_id)` 就能找到 record。這對 B+tree 的 record id 設計很重要。

### Cell（可變長度）

Cell 從 page 末端往前生長，緊鄰存放，沒有任何填充。Cell 之間沒有分隔符——靠 slot entry 的 offset/len 來界定每個 cell 的邊界。

---

## 為什麼要 slotted page，不直接定長 record？

定長 record 的確更簡單：slot 0 在 `header_size + 0 * RECORD_SIZE`，直接算偏移。但一旦 record 有變長欄位（VARCHAR、TEXT、BLOB），就必須用 slotted page 或類似機制。

真實資料庫幾乎都有變長欄位，所以 slotted page 是標準做法。

---

## Rust 實作

### 核心結構

```rust
const PAGE_SIZE: usize = 4096;

/// 一個 Page 就是一塊固定大小的位元組陣列。
/// 所有欄位都以 little-endian 手動序列化，不依賴 repr(C) 對齊假設。
struct Page {
    data: [u8; PAGE_SIZE],
}
```

我們故意不用 `#[repr(C)]` struct + `transmute`，原因：
1. Rust 的 `repr(C)` 不保證跨平台字節序（雖然 x86 通常沒問題）
2. DB 的 page 最終要寫到磁碟，手動序列化讓格式完全由我們控制，不受編譯器填充影響
3. 這也是 SQLite、Postgres 的實際做法

```rust
impl Page {
    const HDR_SIZE: usize = 12;

    fn new(page_id: u32) -> Self {
        let mut page = Page { data: [0u8; PAGE_SIZE] };
        page.set_u32(0, page_id);
        page.set_u16(4, 0);                        // num_slots = 0
        page.set_u16(6, Self::HDR_SIZE as u16);    // free_start 緊接 header
        page.set_u16(8, PAGE_SIZE as u16);         // free_end 指向末端
        page.set_u16(10, 0);                       // flags
        page
    }

    // --- 基礎讀寫輔助 ---
    fn get_u16(&self, off: usize) -> u16 {
        u16::from_le_bytes(self.data[off..off+2].try_into().unwrap())
    }
    fn set_u16(&mut self, off: usize, v: u16) {
        self.data[off..off+2].copy_from_slice(&v.to_le_bytes());
    }
    fn get_u32(&self, off: usize) -> u32 {
        u32::from_le_bytes(self.data[off..off+4].try_into().unwrap())
    }
    fn set_u32(&mut self, off: usize, v: u32) {
        self.data[off..off+4].copy_from_slice(&v.to_le_bytes());
    }

    // --- Header 存取器 ---
    fn page_id(&self)    -> u32 { self.get_u32(0) }
    fn num_slots(&self)  -> u16 { self.get_u16(4) }
    fn free_start(&self) -> u16 { self.get_u16(6) }
    fn free_end(&self)   -> u16 { self.get_u16(8) }
    fn free_space(&self) -> u16 { self.free_end() - self.free_start() }
```

### Slot 讀寫

```rust
    fn slot_base(idx: usize) -> usize {
        Self::HDR_SIZE + idx * 4  // 每個 slot = 4 bytes
    }

    fn read_slot(&self, idx: usize) -> (u16, u16) {
        let b = Self::slot_base(idx);
        (self.get_u16(b), self.get_u16(b + 2))  // (offset, len)
    }

    fn write_slot(&mut self, idx: usize, off: u16, len: u16) {
        let b = Self::slot_base(idx);
        self.set_u16(b, off);
        self.set_u16(b + 2, len);
    }
```

### Insert record

```rust
    /// 插入 record，回傳 slot_id。None 表示 page 已滿。
    fn insert_record(&mut self, record: &[u8]) -> Option<u16> {
        let rec_len = record.len() as u16;
        // 需要 4 bytes (新 slot entry) + rec_len bytes (cell data)
        let needed = 4 + rec_len;
        if self.free_space() < needed {
            return None; // page full
        }

        // cell 從末端往前生長
        let new_end = self.free_end() - rec_len;
        self.data[new_end as usize..new_end as usize + rec_len as usize]
            .copy_from_slice(record);

        // 寫 slot entry
        let slot_idx = self.num_slots();
        self.write_slot(slot_idx as usize, new_end, rec_len);

        // 更新 header
        self.set_u16(4, slot_idx + 1);             // num_slots++
        let new_fs = self.free_start() + 4;
        self.set_u16(6, new_fs);                   // free_start += 4
        self.set_u16(8, new_end);                  // free_end = new cell start

        Some(slot_idx)
    }
```

### Get record

```rust
    /// 根據 slot_id 讀取 record bytes。
    fn get_record(&self, slot_idx: u16) -> Option<&[u8]> {
        if slot_idx >= self.num_slots() {
            return None;
        }
        let (off, len) = self.read_slot(slot_idx as usize);
        if off == 0 && len == 0 {
            return None; // tombstone（已刪除）
        }
        Some(&self.data[off as usize..off as usize + len as usize])
    }
} // end impl Page
```

### 執行結果（WSL 實測）

```rust
fn main() {
    let mut page = Page::new(1);
    println!("Fresh page: free_space={}", page.free_space());  // 4084

    let s0 = page.insert_record(b"hello").unwrap();
    let s1 = page.insert_record(b"world").unwrap();
    assert_eq!(page.get_record(s0).unwrap(), b"hello");
    assert_eq!(page.get_record(s1).unwrap(), b"world");

    // 填滿 page：100-byte record 重複插入直到失敗
    let mut count = 2;
    loop {
        let rec = vec![b'A'; 100];
        match page.insert_record(&rec) {
            Some(_) => count += 1,
            None    => break,
        }
    }
    println!("Inserted {} records before full", count);
    // 空 page 可用 4084 bytes；每個 100-byte record 消耗 104 bytes
    // (4 slot + 100 cell)，所以大約能放 39 筆（前兩筆 5+5=10 byte data + 8 byte slot）
}
```

實測輸出：
```
Fresh page: free_space=4084
Inserted 41 records before full
```

這代表一個 4 KB page 能放 41 筆 100-byte record，page overhead 約 1%，很有效率。

---

## record 序列化格式

上面的例子把 raw bytes 直接塞進 cell。真實資料庫需要一個序列化格式，讓 cell 裡的 bytes 可以被還原成結構化 record。

最常見的做法是**固定長度欄位 + 變長尾**：

```
┌─────────────────────────────────────────┐
│ null_bitmap (ceil(N/8) bytes)            │  每個 bit 代表一個欄位是否為 NULL
├─────────────────────────────────────────┤
│ fixed_col_0 (4 bytes, i32)              │
│ fixed_col_1 (8 bytes, i64)              │
│ fixed_col_2 (4 bytes, f32)              │
├─────────────────────────────────────────┤
│ var_offsets[0] (2 bytes)                │  第 0 個變長欄位在 cell 內的起始位移
│ var_offsets[1] (2 bytes)                │
├─────────────────────────────────────────┤
│ "hello"    ← 變長欄位 0 的資料          │
│ "world!"   ← 變長欄位 1 的資料          │
└─────────────────────────────────────────┘
```

Postgres 用的是類似格式（`HeapTupleHeaderData`），SQLite 則有自己的 record format（type code 陣列 + data section）。本課後續的 B+tree 實作為了簡化，直接用 `u64` key，不再包一層序列化格式；完整序列化會在 Part 4 查詢層時碰到。

---

## page id 定址

page id 到磁碟位置的映射很直接：

```
file_offset = page_id as u64 * PAGE_SIZE as u64
```

讀 page 42：
```rust
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};

fn read_page(file: &mut File, page_id: u32, buf: &mut [u8; PAGE_SIZE]) {
    let offset = page_id as u64 * PAGE_SIZE as u64;
    file.seek(SeekFrom::Start(offset)).unwrap();
    file.read_exact(buf).unwrap();
}
```

寫 page：
```rust
fn write_page(file: &mut File, page_id: u32, buf: &[u8; PAGE_SIZE]) {
    use std::io::Write;
    let offset = page_id as u64 * PAGE_SIZE as u64;
    file.seek(SeekFrom::Start(offset)).unwrap();
    file.write_all(buf).unwrap();
    // 注意：write_all 後不等於 fsync。ch3 講過為什麼這很重要。
}
```

這也是為什麼 page_id 選用 u32：最大 page_id = 2^32 - 1，對應最大資料庫檔案大小 = 4 GB × PAGE_SIZE/1KB = 16 TB（PAGE_SIZE=4096）。SQLite 的最大資料庫檔案就是 2^32 × 4096 = 16 TB。

---

## 與 kernel page cache 的關係

你呼叫 `pread/pwrite` 讀寫資料庫 page 時，資料不是直接去磁碟——它先過 kernel page cache（4 KB 對齊的記憶體緩衝）。資料庫的 buffer pool（下一章）坐在 kernel page cache 之上，是一層額外的應用層快取。

```
[資料庫 Buffer Pool] ← 資料庫自己管的記憶體
        ↕  pread / pwrite（每次都是整頁）
[Kernel Page Cache] ← kernel 管的記憶體
        ↕  只有 dirty page 才會被 writeback
[磁碟]
```

資料庫通常不用 `O_DIRECT`（繞過 kernel cache）是因為 kernel cache 可以免費幫你做 read-ahead 和 write-coalescing。InnoDB 在 Linux 上預設就不用 `O_DIRECT`。SQLite 也不用，除非你在很特殊的嵌入式場景。

---

## 對比：Slotted Page vs 定長 Record Page

| 特性 | Slotted Page | 定長 Record Page |
|------|-------------|-----------------|
| 適合欄位類型 | 變長（VARCHAR/TEXT/BLOB）| 定長（INT/FIXED CHAR）|
| Record 定位 | Header + slot array 查找 | 直接算偏移 `header + idx * size` |
| 刪除後空間回收 | 需要 compaction | 直接標記 free，下次直接覆寫 |
| Fragmentation | 可能產生碎片（cell 間的空洞）| 不產生碎片 |
| 實作複雜度 | 中等 | 低 |
| 使用場景 | Postgres heap, SQLite, InnoDB | 部分欄式儲存引擎的固定寬度列 |

---

## 踩雷

1. **free_space 計算沒算 slot entry**：只檢查 `free_end - free_start >= rec_len` 是錯的。插入一個 record 要同時消耗 `4 bytes（slot）+ rec_len bytes（cell）`，要檢查 `free_space >= 4 + rec_len`。算錯的話，slot array 會踩進 cell 區。

2. **slot id 混淆 record 刪除**：刪除 record 時只把 slot 的 len 設成 0，不要把 slot id 回收再用——外部可能還有指向這個 slot id 的引用（比如 B+tree 的 leaf node 存的 record id）。slot id 一旦分配就不應該改變，這是 slotted page 設計的核心保證。

3. **忘記 page 大小對齊**：如果你用 `mmap` 而且 page_id 計算偏移時溢位或沒對齊，會悄悄讀到錯的資料。`page_id as u64 * PAGE_SIZE as u64` 確保使用 64-bit 乘法，不要直接 `u32 * usize` 讓 Rust 決定型別。

4. **header 沒有 checksum**：生產用的 page header 一定要有 checksum（通常是 CRC32）。如果磁碟返回損壞的 page，沒有 checksum 你完全不知道資料已經爛了——資料庫默默寫入錯誤資料，直到查出來時已遠離根因。Postgres 的 `pg_checksum` 就是補這個洞的。

5. **compaction 時機不對**：刪掉多個 record 後，page 內可能有很多碎片（slot 指向已刪除的 cell）。Compaction 會把所有活著的 cell 往 page 末端擠，釋放中間空洞。但 compaction 前要確保持有這個 page 的所有 latch，不能在有人讀著的同時移動 cell。

---

## 進階延伸

- **Page compaction**：實作一個 `compact()` 函式，把所有活著的 cell 往 page 末端重排，消除碎片，更新所有 slot entry 的 offset。
- **Large object (TOAST)**：Postgres 的 TOAST 機制把超過 8 KB 的欄位分切成多個 page 存放，在 heap tuple 留一個指向 TOAST table 的指標。
- **Page type 分化**：B+tree 的 leaf/internal node 可以用同一個 Page 結構，但 flags 欄位標記類型，内部 node 的 cell 格式不同（存 key + child_page_id 而不是 key + value）。
- **Overflow pages**：如果 record 比 page 還大（雖然少見），需要把 cell 溢出到另一個 page，在原 slot 留一個指向 overflow page 的「指標 cell」。

---

## 本章重點整理

- 資料庫以固定大小的 page（4 KB / 8 KB / 16 KB）為磁碟 I/O 單位，讓管理複雜度可控。
- Slotted page = header + 向前長的 slot array + 向後長的 cell 區，兩者在中間相遇代表 page full。
- Slot id 是穩定的 record 定址機制：`(page_id, slot_id)` 能唯一定位一筆 record。
- `file_offset = page_id * PAGE_SIZE` 是 page_id 到磁碟的映射。
- Header 的 `free_start` 和 `free_end` 是維持 page 內空間管理的兩個游標。

## 自我檢核

1. 一個 4 KB page，header 12 bytes，每個 slot entry 4 bytes，最多能放幾個 1-byte record？答：`(4096 - 12) / (4 + 1) = 816`。
2. 插入 record 後，`free_start` 往哪個方向移動？`free_end` 呢？
3. 為什麼 slot id 不能在 record 刪除後被回收再利用？
4. 如果 `free_space() == 3`，能插入 0-byte record 嗎？（需要 4 bytes slot entry，所以不行。）
5. page_id 用 u32 的最大資料庫檔案大小（假設 page_size=4096）？

## 延伸閱讀

1. **《Database Internals》Ch 2 — B-Tree Basics**（Petrov）：前幾節討論 page layout 與 B-tree page 的關係，是本章的直接延伸。重點看 cell 格式與 separator key 如何存在 internal page。
2. **CMU 15-445 Lecture 3 — Database Storage**（Pavlo, 15445.courses.cs.cmu.edu）：有完整投影片討論 tuple layout、slotted pages、log-structured storage；是最清楚的視覺化教材之一。
3. **SQLite btree.c — `getAndInitPage()` 與 `allocateBtreePage()`**（github.com/sqlite/sqlite）：SQLite 的 page 管理是 C 語言手工位移的經典展示。搜尋 `SQLITE_PAGE_SIZE`，看它如何用 `nCell`、`cellOffset`、`aData` 做跟我們類似的事情。
4. **Postgres heaptuple.c — `heap_form_tuple()`**（github.com/postgres/postgres）：Postgres 的 heap tuple 格式比我們複雜（有 visibility info、OID 等），但結構概念相同。看 `t_infomask`、`t_hoff`、`t_data` 三個欄位是怎麼類比我們的 flags/header_size/record。
5. **Linux `struct page` vs 資料庫 page**：kernel 的 page 指的是 4 KB 的 physical memory frame；資料庫的 page 是應用層的邏輯概念。兩者大小常相同（都是 4 KB）但完全不是同一件事。

---

→ [Ch 5 Buffer Pool](./05-buffer-pool.md)：page 現在能在磁碟上表示了；下一章解決「怎麼把磁碟上的 page 帶進記憶體、管理記憶體裡的有限 frame、以及決定哪個 page 要被踢出」。
