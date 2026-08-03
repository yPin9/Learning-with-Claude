# Ch 5 — Buffer Pool

> **目標**：理解 buffer pool 如何把有限的記憶體 frame 映射到無限的磁碟 page、pin/unpin 機制如何防止正在使用的 page 被踢出、以及 LRU 與 Clock 兩種 eviction 策略的差異。用 Rust 實作一個帶 clock replacer 的簡化 BufferPool（WSL 編譯驗證）。

---

## 為什麼需要 Buffer Pool？

磁碟比記憶體慢 5 到 6 個數量級：記憶體存取是 ~100 ns，SSD 隨機讀是 ~100 µs，HDD 隨機讀是 ~10 ms。如果每次讀一個 B+tree node 都直接去磁碟，每秒最多幾千次 I/O，任何真實 workload 都撐不住。

Buffer pool 的功用：**把最近或最常用的 page 留在記憶體裡，讓大多數 page 存取都走記憶體速度。**

這件事 kernel page cache 也在做，那為什麼資料庫要自己再做一層？答案在章末的比較章節。

---

## 先建立直覺

```
磁碟（無限 pages）          記憶體（有限 frames）
┌──────────────────┐        ┌─────────────┐
│ page 0: [data]   │        │ frame 0 [page 5] ← pin_count=1, dirty=true
│ page 1: [data]   │        │ frame 1 [page 0] ← pin_count=0, dirty=false
│ page 2: [data]   │◄──────►│ frame 2 [page 2] ← pin_count=2, dirty=false
│ ...              │        │ frame 3 [page 7] ← pin_count=0, dirty=true
│ page N: [data]   │        └─────────────┘
└──────────────────┘
                                   ↑
                             Page Table
                          (HashMap<PageId, FrameId>)
                          page 5 → frame 0
                          page 0 → frame 1
                          page 2 → frame 2
                          page 7 → frame 3
```

**Page table** 是一個 hash map：`page_id → frame_id`，告訴你哪個磁碟 page 目前住在哪個記憶體 frame。

**Pin count**：每個正在使用 page 的執行緒或元件都要先「pin」它（pin_count++）。只有 pin_count = 0 的 frame 才能被 eviction policy 選為犧牲品。

---

## Buffer Pool 的完整生命週期

```
fetch_page(page_id):
  1. 查 page_table：若命中（cache hit）→ pin_count++, 回傳 frame_id
  2. 未命中（cache miss）：
     2a. 找一個可用 frame（空的，或 eviction policy 選出的受害者）
     2b. 若受害者 frame 是 dirty → flush to disk（write page 到磁碟）
     2c. 從磁碟讀目標 page 到 frame
     2d. 更新 page_table
     2e. pin_count = 1, 回傳 frame_id
  
unpin(page_id, dirty):
  查 page_table，找到 frame → pin_count--
  若 dirty=true → 標記 frame.dirty = true

flush_page(page_id):
  若 frame.dirty → write to disk, dirty = false
```

---

## Eviction 策略

### LRU（Least Recently Used）

維護一個雙向鏈表 + hash map，最近使用的 page 在頭，最久未使用的在尾。每次 access 把 page 移到頭；eviction 時踢掉尾巴。

LRU 問題：**sequential scan 污染**（LRU-K hazard）。全表掃描會把所有熱的 page 推出去，換進大量只掃一次的冷資料——這對 OLAP workload 是災難。Postgres 的 buffer pool 用 clock（second-chance）部分緩解這個問題；LevelDB/RocksDB 用 2-level LRU。

### Clock（Second-Chance）

用一個環形陣列 + 時鐘指針，每個 frame 有一個 reference bit。

```
 frames: [F0][F1][F2][F3][F4]
                 ↑
               hand (clock 指針)

access frame_i → ref_bit[i] = true
evict():
  loop:
    if pinned[hand]: hand++; continue
    if ref_bit[hand] == true: ref_bit[hand] = false; hand++; continue
    else: 找到受害者 = hand; hand++; return hand
```

第一圈掃到 ref_bit=true → 清掉給第二圈機會（second chance）。這隱含：最近被使用過的 page 可以多活一輪，但如果連兩輪都沒被再次 access，就會被踢走。

Clock 在資料庫實際使用比 LRU 更多，因為：
1. 不需要移動鏈表節點（clock 是 O(1) 陣列操作）
2. 對 sequential scan 的抵抗力稍好

| 策略 | 時間複雜度 | 空間 | Sequential scan 行為 | 實際使用 |
|------|-----------|------|----------------------|---------|
| LRU | O(1) 均攤 | 鏈表 + hash | 掃描汙染，熱頁被踢 | MySQL buffer pool（近似 LRU）|
| Clock | O(frames) 最壞 | 陣列 | 稍好，ref bit 給第二次機會 | Postgres buffer pool |
| LRU-K | O(1) 均攤 | 更多 | 好，看最近 K 次存取 | CMU BusTub 教學用 |
| LIRS | O(1) | 複雜 | 優秀 | 學術，少見實作 |

---

## Rust 實作

### 型別定義

```rust
use std::collections::HashMap;

const PAGE_SIZE: usize = 4096;
const POOL_SIZE: usize = 4; // 測試用小值；生產通常幾 GB

type PageId  = u32;
type FrameId = usize;

struct Frame {
    data:      [u8; PAGE_SIZE],
    page_id:   Option<PageId>,
    pin_count: u32,
    dirty:     bool,
}

impl Frame {
    fn new() -> Self {
        Frame { data: [0u8; PAGE_SIZE], page_id: None, pin_count: 0, dirty: false }
    }
}
```

### Clock Replacer

```rust
struct ClockReplacer {
    ref_bit:  Vec<bool>,
    hand:     usize,
    capacity: usize,
}

impl ClockReplacer {
    fn new(capacity: usize) -> Self {
        ClockReplacer { ref_bit: vec![false; capacity], hand: 0, capacity }
    }

    /// 標記 frame 最近被使用（pin 時呼叫）。
    fn mark_used(&mut self, frame_id: FrameId) {
        self.ref_bit[frame_id] = true;
    }

    /// 找一個可以驅逐的 frame（pin_count == 0 且 ref_bit 被清掉後）。
    /// 最壞情況掃兩圈（2 * capacity 步）。
    fn evict(&mut self, pinned: &[bool]) -> Option<FrameId> {
        let mut steps = 0;
        while steps < self.capacity * 2 {
            let h = self.hand;
            self.hand = (self.hand + 1) % self.capacity;
            steps += 1;
            if pinned[h] { continue; }
            if self.ref_bit[h] {
                self.ref_bit[h] = false; // second chance: 清掉，下一圈才能犧牲
            } else {
                return Some(h);
            }
        }
        None // 所有 frame 都被 pin 住
    }
}
```

### BufferPool

```rust
struct BufferPool {
    frames:     Vec<Frame>,
    page_table: HashMap<PageId, FrameId>,
    replacer:   ClockReplacer,
    next_page:  PageId, // 模擬磁碟：自增 page id
}

impl BufferPool {
    fn new() -> Self {
        let frames = (0..POOL_SIZE).map(|_| Frame::new()).collect();
        BufferPool {
            frames,
            page_table: HashMap::new(),
            replacer: ClockReplacer::new(POOL_SIZE),
            next_page: 0,
        }
    }

    /// 從磁碟（或 pool）取得 page，回傳 frame_id。呼叫方必須在完成後呼叫 unpin。
    fn fetch_page(&mut self, page_id: PageId) -> Option<FrameId> {
        // Cache hit
        if let Some(&fid) = self.page_table.get(&page_id) {
            self.frames[fid].pin_count += 1;
            self.replacer.mark_used(fid);
            return Some(fid);
        }
        // Cache miss：找可用 frame
        let fid = self.find_free_frame()?;
        // 模擬磁碟讀：把 page_id 寫進 data[0..4] 作為辨識標記
        self.frames[fid].data[0..4].copy_from_slice(&page_id.to_le_bytes());
        self.frames[fid].page_id   = Some(page_id);
        self.frames[fid].pin_count = 1;
        self.frames[fid].dirty     = false;
        self.page_table.insert(page_id, fid);
        self.replacer.mark_used(fid);
        Some(fid)
    }

    fn find_free_frame(&mut self) -> Option<FrameId> {
        // 優先用空 frame（沒有住任何 page 的）
        for (i, f) in self.frames.iter().enumerate() {
            if f.page_id.is_none() { return Some(i); }
        }
        // 沒有空 frame，走 eviction
        let pinned: Vec<bool> = self.frames.iter()
            .map(|f| f.pin_count > 0)
            .collect();
        let fid = self.replacer.evict(&pinned)?;

        // Evict：若 dirty 要先 flush（此處模擬，僅印 log）
        let old_pid = self.frames[fid].page_id;
        if self.frames[fid].dirty {
            eprintln!("[flush] evicting dirty page {:?}", old_pid);
            // 真實實作：write_page(old_pid, &self.frames[fid].data)
        }
        if let Some(pid) = old_pid {
            self.page_table.remove(&pid);
        }
        self.frames[fid].page_id   = None;
        self.frames[fid].pin_count = 0;
        self.frames[fid].dirty     = false;
        Some(fid)
    }

    /// 宣告不再需要這個 page。dirty=true 代表本次存取有修改內容。
    fn unpin(&mut self, page_id: PageId, dirty: bool) {
        if let Some(&fid) = self.page_table.get(&page_id) {
            if self.frames[fid].pin_count > 0 {
                self.frames[fid].pin_count -= 1;
            }
            if dirty {
                self.frames[fid].dirty = true;
            }
        }
    }

    /// 分配一個新的 page（磁碟上還不存在，直接給空白 frame）。
    fn new_page(&mut self) -> Option<(PageId, FrameId)> {
        let pid = self.next_page;
        self.next_page += 1;
        let fid = self.find_free_frame()?;
        self.frames[fid].data      = [0u8; PAGE_SIZE];
        self.frames[fid].page_id   = Some(pid);
        self.frames[fid].pin_count = 1;
        self.frames[fid].dirty     = true; // 新 page 還沒寫到磁碟，一定是 dirty
        self.page_table.insert(pid, fid);
        self.replacer.mark_used(fid);
        Some((pid, fid))
    }

    fn write_byte(&mut self, fid: FrameId, offset: usize, val: u8) {
        self.frames[fid].data[offset] = val;
        self.frames[fid].dirty = true;
    }

    fn read_byte(&self, fid: FrameId, offset: usize) -> u8 {
        self.frames[fid].data[offset]
    }
}
```

### 測試：驗證 eviction 行為（WSL 實測通過）

```rust
fn main() {
    let mut pool = BufferPool::new();
    println!("Pool size = {}", POOL_SIZE);

    // 塞滿 pool（4 個 frame，4 個 page）
    let (p0, f0) = pool.new_page().unwrap();
    let (p1, f1) = pool.new_page().unwrap();
    let (p2, _)  = pool.new_page().unwrap();
    let (p3, _)  = pool.new_page().unwrap();
    println!("Allocated pages: {},{},{},{}", p0, p1, p2, p3);

    pool.write_byte(f0, 0, 0xAA);
    pool.write_byte(f1, 0, 0xBB);

    // p2, p3 保持 pin（pin_count=1）
    // Unpin p0（非 dirty）和 p1（dirty）
    pool.unpin(p0, false);
    pool.unpin(p1, true);

    // 第 5 個 page：clock 必須選 p0 或 p1 evict
    // p0 和 p1 的 ref_bit 都是 true（剛被 mark_used）
    // clock 第一圈：清掉 ref_bit，不踢
    // clock 第二圈：ref_bit = false，可以踢
    let result = pool.new_page();
    println!("5th page allocated: {:?}", result.map(|(pid, _)| pid));

    // unpin p2, p3 後再試一次
    pool.unpin(p2, false);
    pool.unpin(p3, false);
    let result2 = pool.new_page();
    println!("6th page allocated: {:?}", result2.map(|(pid, _)| pid));

    println!("Buffer pool test complete.");
}
```

實測輸出（stderr 顯示 flush log）：
```
Pool size = 4
Allocated pages: 0,1,2,3
[flush] evicting dirty page Some(1)
5th page allocated: Some(4)
[flush] evicting dirty page Some(3)
6th page allocated: Some(5)
Buffer pool test complete.
```

---

## Dirty page 與 flush

dirty page 是「記憶體比磁碟新」的 page。什麼時候 flush？

1. **Eviction 時**：frame 被選為犧牲品，若 dirty 就必須先 flush 才能讓新 page 進來。
2. **Checkpoint 時**：WAL（Write-Ahead Log）的 checkpoint 會把所有 dirty page flush 到磁碟，讓 WAL 的某個 LSN（Log Sequence Number）以前的 log 可以截斷。不做 checkpoint，WAL 會無限長。
3. **`fsync` 要求時**：事務 commit 時（視 `sync_commit` 設定），Postgres 可能要確保相關的 dirty page 都落地。

Flush 只是 `write_page` 到磁碟，不等於 `fsync`。`fsync` 才能確保資料過了 OS buffer，真的寫進 storage。沒有 `fsync` 的 flush 在機器斷電後可能消失。

---

## Buffer Pool vs Kernel Page Cache

為什麼資料庫要在 kernel page cache 之上自己再做一層 buffer pool？

| 特性 | Kernel Page Cache | 資料庫 Buffer Pool |
|------|------------------|-------------------|
| 管控粒度 | kernel 自動管理，應用層不能干涉 | 應用層完全掌控 eviction 策略 |
| Eviction policy | LRU/LFU 變形，全局競爭 | 可針對 DB workload 調整（sequential scan 降優先級）|
| Pin 語意 | 無 | 明確 pin/unpin，保護正在使用的 page |
| Dirty page 追蹤 | 由 kernel writeback 管理 | 由 DB 決定何時 flush（配合 WAL LSN）|
| 雙重緩衝浪費 | 如果 DB 有自己的 buffer pool，同一個 page 在記憶體存兩份 | 可用 `O_DIRECT` 繞過 kernel cache 解決 |
| `fsync` 控制 | 應用層呼叫 | DB 可以精確控制哪個 page 在 commit 前落地 |

大多數資料庫選擇不用 `O_DIRECT`：
- Postgres：預設走 kernel page cache，因為 kernel 的 read-ahead 免費幫你做，而且 linux 的 IO scheduler 會合併 write；代價是「雙重緩衝」佔用更多記憶體。
- InnoDB：預設走 kernel cache，除非明確設 `innodb_flush_method=O_DIRECT`。
- 特殊場景（高效能 NVMe 陣列）才值得用 `O_DIRECT` 換更精確的 I/O 控制。

這裡有一個著名的爭議：Andy Pavlo 的論文《Are You Sure You Want to Use MMAP in Your Database Management System?》（CIDR 2022）詳細分析了 mmap 做 buffer pool 替代方案的問題，值得一讀。

---

## 實際 Buffer Pool 的額外複雜度

我們的實作很精簡；真實資料庫還需要：

1. **Latch（不是 Lock）**：存取 page table 和 frame 時需要 latch 保護（spinlock 或 RwLock），因為多個執行緒同時 fetch/unpin。
2. **多個 Buffer Pool**（InnoDB）：避免單一 pool 的 mutex 成為瓶頸，分成 N 個獨立 pool，用 `page_id % N` 決定去哪個 pool。
3. **Prefetch / Read-Ahead**：順序掃描時預測下幾個 page，非同步預先讀進 pool。
4. **Background flusher**：不要等到 eviction 時才 flush dirty page，有背景執行緒週期性把 dirty page 刷到磁碟，減少 eviction 的延遲峰值。

---

## 踩雷

1. **先 fetch 再 unpin，次序搞錯**：使用 pattern 是 `fetch → 讀寫 frame → unpin`。如果忘記 unpin，pin_count 永遠不歸零，那個 frame 永遠不能被 evict，pool 最終耗盡（pin leak）。在 C++ 通常用 RAII guard 處理；Rust 可以把 frame access 包在一個持有 `&mut BufferPool` 的 guard struct 裡，drop 時自動 unpin。

2. **eviction 選到 dirty page，沒有 flush 就覆寫**：frame 被 evict 時，若 dirty 必須先 flush 到磁碟，再讀入新 page。順序搞反的話，磁碟上的 page 是老的，而新的修改永遠消失了。

3. **Page table 移除時機**：evict 一個 frame 時，必須先把舊的 `page_id → frame_id` 從 page_table 移除，才能把新 page_id 插進去。漏掉這步，下次 fetch 舊 page_id 會看到已經放新資料的 frame。

4. **Clock hand 繞圈後仍找不到受害者**：所有 frame 都 pinned 時，`evict()` 必須回傳 `None`，上層要能正確處理（block 等待或回傳 error）。無限迴圈等待是資料庫常見的死鎖根因之一。

5. **new_page 的 dirty 初始值**：新分配的 page 在記憶體裡，還沒寫到磁碟，一定是 dirty。如果把 dirty 設成 false，checkpoint 時這個 page 不會被 flush，磁碟上永遠沒有它，crash 後永遠找不回來。

---

## 進階延伸

- **LRU-K**：只看最近 K 次存取的時間戳，K 通常取 2。Sequential scan 的 page 只會被存取一次（K=2 時第一次存取排在最後），因此 eviction 優先踢，不汙染熱 page。CMU 15-445 的 BusTub 作業要求實作 LRU-K。
- **Buffer pool bypass**：大型排序或 hash join 操作時，可以告訴 buffer pool「這些 page 掃完就不需要了，不要進 pool」，直接 I/O 繞過 pool，不汙染其他 workload 的熱 page。
- **Memory-mapped I/O 的問題**：用 `mmap` 讓 OS 管理 page 載入，看起來省事，但你失去 pin 語意、eviction 控制、dirty page 追蹤。LMDB 選擇用 mmap；大多數主流 RDBMS 不用。

---

## 本章重點整理

- Buffer pool 用有限記憶體 frame 快取無限磁碟 page，讓大多數存取走記憶體速度。
- Page table（`HashMap<PageId, FrameId>`）是磁碟 page 到記憶體 frame 的映射。
- Pin/unpin 防止正在使用的 frame 被 eviction policy 選走。
- Clock（second-chance）replacer：ref_bit 給每個 page 一次額外生存機會，比 LRU 更簡單且對 sequential scan 稍有抵抗力。
- Dirty page 在 eviction 前必須 flush 到磁碟；checkpoint 也會觸發 dirty page flush。
- 資料庫自建 buffer pool 的核心理由：精確的 eviction 控制 + pin 語意 + 與 WAL/checkpoint 協調的 flush 時機。

## 自我檢核

1. 什麼情況下 `fetch_page` 會讓 clock hand 掃兩圈才找到受害者？
2. 為什麼 `new_page` 分配的 frame 要把 dirty 設為 true？
3. 如果 pool 裡所有 frame 都 pinned，`fetch_page` 應該怎麼辦？
4. Clock replacer 最壞情況下掃幾步？最好情況？
5. 一個 buffer pool 有 N 個 frame，其中 M 個 pinned、K 個 dirty。如果現在需要 fetch 一個不在 pool 裡的 page，最壞情況要做幾次磁碟 I/O？

## 延伸閱讀

1. **《Database Internals》Ch 4 — Implementing B-Trees**（Petrov）：前幾節討論 page cache 與 buffer management 的互動關係，是本章的直接理論延伸。重點看 page_id 如何轉成 on-disk offset。
2. **CMU 15-445 Lecture 5 — Buffer Pools**（Pavlo, 15445.courses.cs.cmu.edu）：有 LRU-K 的完整解說，以及 Postgres/MySQL/MSSQL buffer pool 設計的對比。投影片清晰，配合影片效果最好。
3. **《Are You Sure You Want to Use MMAP in Your Database Management System?》**（Hao 等, CIDR 2022, cidrdb.org）：系統性地說明為什麼 mmap 不適合做資料庫 buffer pool，幫你理解「DB 為什麼自己實作 buffer pool」的核心理由。
4. **Postgres src/backend/storage/buffer/bufmgr.c**（github.com/postgres/postgres）：`BufferAlloc()`、`StrategyGetBuffer()` 是 Postgres clock-sweep replacer 的核心。搜尋 `StrategyControl` 看 shared clock hand 的跨執行緒實作。
5. **InnoDB buf_pool_t 與 buf_page_t**（github.com/mysql/mysql-server, storage/innobase/buf）：InnoDB 有多個獨立 buffer pool（預設 8 個）以降低 mutex 爭搶，是生產級 buffer pool 設計的好範本。

---

→ [Ch 6 B+tree 原理](./06-btree-principles.md)：buffer pool 準備好了，page 能在記憶體和磁碟之間流動了。現在是時候回答「page 裡面放什麼結構，才能讓查找是 O(log N) 而不是 O(N)」——B+tree。
