# Ch 37 — 記憶體與效能：mmap 的爭議

> **目標**：理解 arena/bump allocator 在資料庫中的用途、掌握 page 對齊與 cache line 對效能的影響、深入 mmap 作為 buffer pool 的誘惑與四個根本問題（Andy Pavlo 論文），以及為什麼多數嚴肅 DB 選擇自管 buffer pool。

## 為什麼記憶體管理在資料庫中不是小事

資料庫是記憶體的重度使用者。一個正在跑的查詢可能同時持有：

- Buffer pool 裡的數千個 8KB page（每個有 pin count、dirty flag、latch）
- 查詢執行器的 tuple buffer（每批 1024-65536 個 tuple）
- Join 的 hash table（可能幾十 GB）
- Sort 的 run buffer
- Plan 樹的 node 結構（查詢生命週期內全活著）

把這些全丟給系統 allocator（glibc malloc/jemalloc），每次 alloc/free 都有鎖爭用、fragmentation、和不可預測的延遲。高效能 DB 在記憶體管理上下的功夫，不亞於 B-tree 本身。

## Arena / Bump Allocator

### 概念

Arena（也叫 Bump Allocator 或 Linear Allocator）：從一大塊預分配的記憶體開頭往後線性分配，free 的時候什麼都不做，等到整個 arena 不需要了再一次全部釋放。

```
Arena 記憶體佈局：

┌──────────────────────────────────────────────────────────┐
│ allocated │ allocated │ allocated │ free space...         │
└──────────────────────────────────────────────────────────┘
 ^─────────────────────^                ^
 已分配的物件              bump pointer（下次從這裡分配）

alloc(size)：
  ptr = bump_ptr
  bump_ptr += size（對齊到 alignment）
  return ptr
  → O(1)，無鎖，無 fragmentation

free：
  什麼都不做（free 整個 arena 只需重設 bump_ptr）
```

### 資料庫中的用法

**查詢執行期的臨時分配**：一條查詢的生命週期內，所有臨時 tuple、中間結果、operator state 都從同一個 arena 分配。查詢結束，整個 arena reset，O(1) 釋放，不管裡面有多少個物件。PostgreSQL 的 Memory Context 就是這個概念的進階版本（樹狀 arena，父 context 清除時子 context 一起清）。

**Page Buffer 的 Frame 管理**：buffer pool 的 frame 也是從一個大 arena 一次分配完，之後只是把 frame 從 free list 取出/放回，不真正 malloc/free。

### Rust 實作：Bump Allocator

```rust
// src/memory/arena.rs

use std::alloc::Layout;

pub struct Arena {
    storage: Vec<u8>,
    bump: usize, // 下次分配的起始 offset
}

impl Arena {
    /// 預分配 capacity bytes
    pub fn new(capacity: usize) -> Self {
        Self {
            storage: vec![0u8; capacity],
            bump: 0,
        }
    }

    /// 分配 size bytes，對齊到 align（必須是 2 的冪）
    /// 回傳指向 Arena 內部的原始指標
    /// Safety：回傳的指標只在 Arena 存活期間有效
    pub fn alloc_raw(&mut self, size: usize, align: usize) -> Option<*mut u8> {
        // 對齊
        let aligned_bump = (self.bump + align - 1) & !(align - 1);
        let new_bump = aligned_bump.checked_add(size)?;

        if new_bump > self.storage.len() {
            return None; // OOM
        }

        self.bump = new_bump;
        Some(unsafe { self.storage.as_mut_ptr().add(aligned_bump) })
    }

    /// 分配 T 的空間並初始化
    pub fn alloc<T>(&mut self, val: T) -> Option<&mut T> {
        let layout = Layout::new::<T>();
        let ptr = self.alloc_raw(layout.size(), layout.align())? as *mut T;
        unsafe {
            ptr.write(val);
            Some(&mut *ptr)
        }
    }

    /// 分配 n 個 T 的連續空間（slice）
    pub fn alloc_slice<T: Copy>(&mut self, val: T, n: usize) -> Option<&mut [T]> {
        let layout = Layout::array::<T>(n).ok()?;
        let ptr = self.alloc_raw(layout.size(), layout.align())? as *mut T;
        unsafe {
            for i in 0..n {
                ptr.add(i).write(val);
            }
            Some(std::slice::from_raw_parts_mut(ptr, n))
        }
    }

    /// 重設 arena（O(1) 釋放所有分配）
    pub fn reset(&mut self) {
        self.bump = 0;
        // 記憶體還在，只是重設 bump pointer
        // 不清零內容（效能考量）；呼叫者負責初始化
    }

    pub fn used_bytes(&self) -> usize {
        self.bump
    }

    pub fn capacity(&self) -> usize {
        self.storage.len()
    }

    pub fn remaining(&self) -> usize {
        self.storage.len() - self.bump
    }
}

/// 針對查詢執行的 Arena wrapper
/// 一條查詢用一個 QueryArena，結束後 reset
pub struct QueryArena {
    arena: Arena,
    query_id: u64,
}

impl QueryArena {
    pub fn new(capacity: usize, query_id: u64) -> Self {
        Self {
            arena: Arena::new(capacity),
            query_id,
        }
    }

    pub fn alloc<T>(&mut self, val: T) -> Option<&mut T> {
        self.arena.alloc(val)
    }

    pub fn reset_for_next_query(&mut self, new_query_id: u64) {
        self.arena.reset();
        self.query_id = new_query_id;
    }

    pub fn stats(&self) -> (usize, usize) {
        (self.arena.used_bytes(), self.arena.capacity())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_basic_alloc() {
        let mut arena = Arena::new(1024);

        let x = arena.alloc(42u32).unwrap();
        let y = arena.alloc(100u64).unwrap();

        assert_eq!(*x, 42u32);
        assert_eq!(*y, 100u64);
        assert!(arena.used_bytes() >= 12); // 4 + 對齊 + 8
    }

    #[test]
    fn test_slice_alloc() {
        let mut arena = Arena::new(4096);
        let slice = arena.alloc_slice(0u32, 100).unwrap();
        assert_eq!(slice.len(), 100);
        slice[0] = 1;
        slice[99] = 42;
        assert_eq!(slice[99], 42);
    }

    #[test]
    fn test_reset() {
        let mut arena = Arena::new(256);
        arena.alloc(1u64).unwrap();
        arena.alloc(2u64).unwrap();
        let used_before = arena.used_bytes();
        assert!(used_before > 0);

        arena.reset();
        assert_eq!(arena.used_bytes(), 0);

        // 重設後可以重新分配
        let ptr = arena.alloc(99u64).unwrap();
        assert_eq!(*ptr, 99u64);
    }

    #[test]
    fn test_oom() {
        let mut arena = Arena::new(16);
        // 16 bytes 的 arena 放不下 24 bytes 的資料
        let _ = arena.alloc(1u64); // 8 bytes
        let _ = arena.alloc(2u64); // 8 bytes
        let result = arena.alloc(3u64); // OOM
        assert!(result.is_none());
    }

    #[test]
    fn test_alignment() {
        let mut arena = Arena::new(4096);
        // 分配一個 1-byte 物件
        let _ = arena.alloc(1u8);
        // 接下來分配 8-byte 物件，bump 要對齊到 8
        let ptr = arena.alloc(42u64).unwrap();
        // 指標必須是 8 的倍數
        assert_eq!(ptr as usize % 8, 0);
    }
}
```

## Page 對齊與 Cache Line

### Page 對齊的必要性

資料庫的 page（通常 4KB 或 8KB）要從磁碟 I/O 讀進來，OS 的 O_DIRECT 要求 buffer 必須對齊到磁碟 sector size（通常 512 bytes 或 4096 bytes）。

```rust
// 對齊分配（用於 O_DIRECT buffer）
fn alloc_aligned(size: usize, align: usize) -> Vec<u8> {
    // 未編譯驗證（需要 POSIX posix_memalign 或 aligned_alloc）
    // Rust 標準做法：
    use std::alloc::{alloc, Layout};
    let layout = Layout::from_size_align(size, align).unwrap();
    let ptr = unsafe { alloc(layout) };
    unsafe { Vec::from_raw_parts(ptr, size, size) }
}

// 更簡單的方法：分配 size + align - 1 個 bytes，手動找對齊邊界
// （代價是浪費最多 align-1 個 bytes）
```

### Cache Line 對效能的影響

一個 cache line 是 64 bytes（x86_64）。跨 cache line 邊界的資料存取需要兩次 cache miss。

```
錯誤設計（false sharing）：

struct BufPoolFrame {
    pin_count: AtomicU32,   // 4 bytes
    dirty: bool,            // 1 byte
    // padding...
    data: [u8; 8192],
}

多個 thread 同時存取不同 frame 的 pin_count：
  Thread A 改 frame[0].pin_count
  Thread B 改 frame[1].pin_count
  如果兩個 pin_count 在同一個 cache line → false sharing
  → 一方修改導致另一方的 cache line 失效 → 效能暴跌

正確設計（hot field 對齊到 cache line）：

#[repr(align(64))]
struct FrameHeader {
    pin_count: AtomicU32,
    dirty: AtomicBool,
    page_id: u32,
    // padding to fill 64 bytes
    _pad: [u8; 55],
}

frame header 各佔一個 cache line，彼此不干擾
```

實際上 PostgreSQL 的 buffer descriptor（對應我們的 FrameHeader）就有類似的 cache line padding，見 `src/backend/storage/buffer/bufmgr.c`。

## mmap 的誘惑

mmap（memory-mapped file）讓你把檔案映射進虛擬位址空間，直接用指標存取，OS 負責 page fault 和 write-back。對資料庫開發者來說，這看起來非常誘人：

```
不用 mmap：
  fd = open(db_file)
  buf = malloc(PAGE_SIZE)
  pread(fd, buf, PAGE_SIZE, page_id * PAGE_SIZE)  ← 系統呼叫
  // 用完後：
  if dirty: pwrite(fd, buf, PAGE_SIZE, ...)       ← 系統呼叫
  free(buf)

用 mmap：
  data = mmap(NULL, file_size, PROT_READ|PROT_WRITE, MAP_SHARED, fd, 0)
  page = &data[page_id * PAGE_SIZE]               ← 直接指標！
  // OS 自動 page fault 載入，自動 write-back
  // 不需要手動管 buffer pool
```

表面上，mmap 讓 buffer pool 變成「OS 幫你管」，省掉大量 userspace 程式碼。SQLite（WAL mode 前）、LMDB、早期的 MongoDB（WiredTiger 之前）都走這條路。

**這條路是錯的。**

## Andy Pavlo 的四大罪：「Are You Sure You Want to Use MMAP in Your DBMS?」

Andy Pavlo（CMU）在 2022 年 CIDR 發表了這篇論文，系統性地整理了 mmap 作為 DBMS buffer pool 的四個根本問題。這裡逐一講清楚。

### 問題一：Transactional Safety（交易安全性）

mmap 的 write-back 由 OS 決定時機，DBMS 無法控制「哪個 page 何時落磁碟」。

```
MVCC/WAL 要求的順序：WAL record 必須比 dirty page 先落磁碟
（Write-Ahead Logging invariant：log before data）

用 mmap 的困境：
  txn 修改了 page P（變成 dirty，mmap 會最終 write-back）
  txn 的 WAL record 還在 log buffer，尚未 fsync
  OS 在你不知道的時刻把 dirty page P flush 到磁碟
  → Crash → page P 已落磁碟，但 WAL 沒有 → 不知道這個修改是否已提交
  → 違反 ACID

自管 buffer pool 的解法：
  DBMS 在 write dirty page 之前，先確認對應的 WAL 已 fsync
  這個控制點在 mmap 模式下消失了
```

**唯一的 workaround**：`msync(MS_SYNC)` 前先 fsync WAL，但這讓 mmap 退化成「每次 sync 都要呼叫昂貴的系統呼叫」，比自管 buffer pool 更慢且更複雜。

### 問題二：I/O Stall（I/O 停滯）

mmap 的 page fault 在任意時刻阻塞任意 thread，DBMS 無法非同步 I/O。

```
mmap 觸發 page fault 的時機：
  thread 第一次存取虛擬位址 → page fault → OS 從磁碟載入
  但：OS 的 page fault handler 是同步的，整個 thread 卡住直到 I/O 完成

影響：
  假設 200 個 worker thread，任何一個 thread 存取未載入的 page
  → 該 thread 完全阻塞（不是 sleep，是 kernel trap）
  → 其他等待這個 thread 持有的 latch 的 thread 也全部阻塞
  → 整個 query 的延遲不可預測

自管 buffer pool 的解法：
  prefetch：在需要 page 之前就非同步發出 pread
  io_uring（Linux 5.1+）：非同步 I/O，不阻塞 thread
  mmap 完全無法做非同步 prefetch（page fault 是同步的）
```

論文中的實測：在 NVMe SSD 上，mmap 的 tail latency（P99）比自管 buffer pool + io_uring 高 10x 以上，原因就是隨機 page fault 的阻塞行為。

### 問題三：Error Handling（錯誤處理）

mmap 存取出錯時傳遞的是 SIGBUS 信號，不是 return value 可以處理的錯誤碼。

```
自管 buffer pool：
  ret = pread(fd, buf, PAGE_SIZE, offset)
  if ret < 0: handle_error(errno)  ← 正常的 error propagation

mmap：
  char *data = mmap(...)
  // 某個時刻，disk 壞了，或 NFS timeout：
  char c = data[0x1000]  ← SIGBUS!
  // 沒有辦法 catch return value，只能 signal handler
  // signal handler 裡能做的事非常有限
```

**checksum 失敗**也是同樣問題：讀取 mmap 的 page 後發現 checksum 不對，DBMS 知道資料壞了，但「拒絕這個 page」的動作沒有辦法傳回給應用層，只能 abort 整個 process。自管 buffer pool 下，checksum 失敗可以回傳錯誤、重試、或觸發 replenish from replica。

### 問題四：Performance（效能問題）

mmap 在高並發、大於 RAM 的資料集下有三個效能問題：

**4a. TLB Shootdown**：

```
多核心環境，一個 core 把某個 mmap page evict，
OS 必須通知所有其他 core 的 TLB 失效這個位址映射。
這需要 IPI（Inter-Processor Interrupt），對所有 core 造成 stall。

自管 buffer pool：
  buffer pool 的 frame 位址是固定的，TLB 不需要 shootdown
  page eviction 只改 page table 裡的 page_id，不改虛擬位址映射
```

**4b. Page Table Walk Overhead**：

```
mmap 一個大 DB 檔案（幾十 GB）：
  每個 mmap page = 一個 PTE（Page Table Entry）
  幾十 GB / 4KB = 幾千萬個 PTE
  page table 本身消耗幾十 MB 記憶體
  page walk 慢（4-5 層 page table）

使用 Huge Pages（2MB）可以緩解，但需要特殊配置：
  MAP_HUGETLB or /sys/kernel/mm/hugepages/
  且 DBMS 需要確保 mmap 對齊到 2MB 邊界
```

**4c. OS 的 Page Replacement Policy 不理解 DB 的存取模式**：

```
OS LRU 近似（clock algorithm）vs DB 的知識：
  OS 不知道這個 page 是 hot B+tree root（應該 pin）
  OS 不知道這個 page 是 sequential scan 的中間 page（用完就丟）
  OS 不知道這條 query 即將存取的下一個 page（無法 prefetch）

DB 的 buffer pool 有這些知識：
  buffer pool manager 知道 reference count、access pattern
  可以實作 LRU-K、CLOCK-Pro、2Q 等更適合 DB 的替換策略
  可以根據 query plan 決定 prefetch 策略
```

## 那誰在用 mmap？結果怎樣？

| 系統          | mmap 狀態                        | 備注                                    |
|--------------|----------------------------------|-----------------------------------------|
| LMDB         | 完全依賴 mmap                    | 因 MDB_WRITEMAP 的交易安全問題被廣泛批評 |
| SQLite        | WAL mode 前用 mmap，WAL mode 後改 | WAL mode 解決了 transactional safety    |
| MongoDB       | WiredTiger 之前用 mmap            | 換引擎就是為了解決 mmap 的問題          |
| MySQL InnoDB  | 自管 buffer pool                  | 不用 mmap 處理 data pages               |
| PostgreSQL    | 自管 shared_buffers               | mmap 只用於小型輔助結構，不用於 data pages|
| CockroachDB   | Pebble（自管 buffer pool）        | 明確拒絕 mmap for data                  |

**Pavlo 的結論**：「如果你在做一個嚴肅的（production-grade）DBMS，不要用 mmap 管 data pages。自管 buffer pool 雖然麻煩，但每一個麻煩點都是在解決 mmap 躲避的真實問題。」

我們的課程 Ch 5 從一開始就選擇自管 buffer pool，現在你知道這個決定背後的理由了。

## Prefetch

無論自管還是 mmap，sequential scan 都可以利用 prefetch 隱藏 I/O latency：

```rust
// 概念示意：在 buffer pool 中 prefetch 下一批 page
// 未編譯驗證

fn sequential_scan_with_prefetch(
    buf_pool: &mut BufferPool,
    table_pages: &[PageId],
) {
    const PREFETCH_WINDOW: usize = 4;

    for (i, &page_id) in table_pages.iter().enumerate() {
        // 提前發出 prefetch
        if i + PREFETCH_WINDOW < table_pages.len() {
            let prefetch_page = table_pages[i + PREFETCH_WINDOW];
            buf_pool.prefetch_async(prefetch_page); // 非同步 I/O
        }

        // 現在處理當前 page（I/O 可能已在 prefetch 中完成）
        let frame = buf_pool.fetch_page(page_id);
        process_page(frame);
        buf_pool.unpin_page(page_id, false);
    }
}
```

Linux 的 `posix_fadvise(POSIX_FADV_SEQUENTIAL)` 是告訴 OS 的 hint，io_uring 的 `IORING_OP_READ` 系列可以真正非同步發出多個 read 請求。

## 對比：自管 Buffer Pool vs mmap

| 維度                | 自管 Buffer Pool          | mmap                        |
|--------------------|--------------------------|------------------------------|
| 實作複雜度           | 高（需要自己寫 eviction、pin）| 低（OS 全包）                |
| Transactional Safety| 完全控制 flush 順序       | 無法控制，需要 msync workaround|
| I/O Stall           | 可非同步（io_uring）      | Page fault 同步阻塞 thread   |
| Error Handling      | return value，可 propagate| SIGBUS，signal handler 受限  |
| Page Replacement    | 可實作 DB 專用策略        | OS LRU，不懂 DB 存取模式     |
| Prefetch            | 完全自主控制              | 只能 madvise hint            |
| TLB Shootdown       | 無（frame 位址固定）      | 有（page evict 需 shootdown）|
| 適合場景            | 所有嚴肅的 DB             | 簡單嵌入式/讀多寫少的 DB     |

## 踩雷

1. **Arena 的生命週期陷阱**：Arena 內的物件指標只在 arena reset 之前有效。把 arena 分配的指標存進長生命週期的 struct，然後 reset arena，就是 use-after-free。Rust 的 lifetime 系統在 unsafe 指標下無法保護你——只能靠設計紀律。

2. **mmap + fsync 不等於安全**：`msync(MS_ASYNC)` 只是告訴 OS 可以開始 write-back，不保證已落磁碟。`msync(MS_SYNC)` 才是同步等待，但代價極高。很多初學者誤解 mmap 配合 msync 就有交易安全，不是的。

3. **Cache line false sharing 是隱形殺手**：多核心下，兩個 thread 修改同一個 cache line 的不同欄位，效能可以比單核心還差（因為 cache coherence 協議）。用 perf stat 的 cache-misses 計數器或 `perf c2c` 找 false sharing。

4. **Huge pages 的配置不是免費的**：mmap + MAP_HUGETLB 需要 OS 預先保留 2MB 的連續實體記憶體，在記憶體碎片嚴重時申請失敗，且無法動態申請。生產環境要在系統啟動時配置。

5. **Arena 不適合需要 individual free 的場景**：如果你需要在生命週期中間釋放某個物件並回收空間，bump allocator 做不到。這時需要 slab allocator（固定大小的 object pool）或回退到 jemalloc。

## 進階延伸

**io_uring**：Linux 5.1 引入的非同步 I/O 介面，比 aio 更高效。RocksDB 6.x、PostgreSQL 16+ 都在研究/實作 io_uring 後端。和自管 buffer pool 配合，可以批量發出 read request，在等待 I/O 期間不阻塞任何 thread。

**Slab Allocator**：固定大小的 object pool，每個 slab 只分配同一種大小的物件，避免 fragmentation 且 O(1) 分配和釋放。Linux kernel 的 kmem_cache 就是 slab allocator，資料庫用於管理固定大小的 tuple buffer 或 node 結構。

**jemalloc**：Facebook 開發的高效能 allocator，Firefox 和 Redis 的預設。相比 glibc malloc 在多核心下 fragmentation 更低、lock contention 更少。RocksDB 建議在生產環境用 jemalloc。

## 本章重點整理

- Arena/bump allocator 在資料庫查詢執行中廣泛使用：O(1) 分配、O(1) 批量釋放，無鎖無 fragmentation，適合有明確生命週期邊界的場景。
- Cache line 對齊是高並發資料結構設計的基礎——hot 的 header 欄位要對齊到 cache line 邊界，避免 false sharing。
- mmap 作為 buffer pool 有四個根本問題：交易安全性（OS 控制 flush 順序）、I/O stall（page fault 同步阻塞）、錯誤處理（SIGBUS 難以傳遞）、效能（TLB shootdown、OS 不理解 DB 存取模式）。
- 嚴肅的資料庫選擇自管 buffer pool，代價是複雜度，換來的是對 I/O 的完整控制。
- Prefetch 是隱藏 I/O latency 的有效手段，必須配合自管 buffer pool 或 io_uring 才能非同步執行。

## 自我檢核

- [ ] 能解釋 bump allocator 的 alloc 和 reset 各自 O(1) 的原因？
- [ ] 為什麼 cache line 對齊影響多核心下的效能？能舉一個 false sharing 的例子？
- [ ] mmap 的四個問題各自是什麼，自管 buffer pool 各如何解決？
- [ ] `msync(MS_ASYNC)` 能保證交易安全嗎？為什麼？
- [ ] 我們的 `Arena::alloc` 為什麼需要做對齊（align）計算，不對齊會有什麼問題？

## 延伸閱讀

1. **Are You Sure You Want to Use MMAP in Your DBMS?** — Crotty, Leis, Pavlo (CIDR 2022)  
   本章核心論文，直接讀原文。四個問題的實測數據在這裡，Figure 2–5 是最有說服力的部分。  
   <https://db.cs.cmu.edu/papers/2022/cidr2022-p13-crotty.pdf>

2. **CMU 15-445 Lecture "Buffer Pools" (2023)**  
   Andy Pavlo 在課堂上把 mmap 問題講得更口語，配合 buffer pool replacement policy（LRU-K、CLOCK）的完整推導，是 Ch 5 的深度延伸，也呼應本章。

3. **《Database Internals》第 4 章（B-Tree Implementation Details）及附錄 A**  
   Alex Petrov 對 buffer pool 實作細節、page 對齊、以及 OS page cache 和 DB buffer pool 互動有深入討論。

4. **jemalloc: A Scalable Concurrent malloc Implementation for FreeBSD** — Evans (2006)  
   理解 DB 為什麼偏好 jemalloc 而非 glibc malloc 的背景，slab/arena 設計思路的延伸。

5. **io_uring 官方文件與 Lord of the io_uring**  
   <https://unixism.net/lbyl/>  
   io_uring 是未來非同步 I/O 的方向，RocksDB 已有 io_uring 後端，PostgreSQL 16 也在 patch。理解它的提交/完成環模型，和傳統 aio/epoll 的差異。

---

銜接：我們的單機資料庫已經有了儲存引擎、查詢層、和效能基礎。下一步是把這些知識移到分散式環境——replication、partitioning、分散式事務的第一步。

→ [下一章：Ch 38 分散式的第一步](./38-distributed-first-steps.md)
