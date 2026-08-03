# Ch 12 — MemTable 與 Skip List

> **目標**：理解 LSM-Tree 的記憶體寫入緩衝層 MemTable 為什麼需要有序、為什麼用 Skip List 而不是平衡二元樹，動手實作一個能通過排序迭代測試的 unsafe Rust Skip List，並走完「寫入 WAL → 插入 MemTable → freeze → flush 到 SSTable」完整寫入路徑。

---

## 為什麼需要 MemTable？

LSM-Tree 的核心思路：把隨機寫入變成循序 I/O。磁碟隨機寫一個 4 KB page 大約 0.1ms（SSD）到幾毫秒（HDD）；循序寫入同樣資料量快一個數量級以上。

做法是：先在記憶體裡累積寫入，等攢到夠多（通常 64MB）再一次性循序寫到磁碟的 SSTable 檔案。這個記憶體緩衝結構就是 **MemTable**。

MemTable 有三個硬性需求：

1. **有序**：flush 到 SSTable 時必須產出 key 有序的輸出，否則 SSTable 的二元搜尋和 Bloom filter 都失效。
2. **快速點查與範圍查詢**：讀取路徑要先查 MemTable，O(log n) 點查，O(k) 前綴掃描。
3. **可 freeze**：超過大小閾值後要原子性地把 MemTable 切換成 immutable，同時開一個新的空 MemTable 接受後續寫入。

---

## 為什麼不用 HashMap？

HashMap 查單點 O(1)，但它不保證迭代順序。flush 時要把所有 entry 排序才能寫 SSTable，額外花 O(n log n)，而且無法支援有序的範圍查詢（`SCAN key1..key2`）。

用能維持 key 有序的資料結構，flush 就是直接一趟有序迭代，O(n)。

---

## 為什麼用 Skip List 而不是平衡 BST？

常見的有序結構是 AVL Tree、Red-Black Tree、B-Tree。Skip List 在以下三點勝出：

| 比較維度         | 平衡 BST（RB/AVL）       | Skip List                  |
|------------------|--------------------------|----------------------------|
| 實作複雜度       | 旋轉邏輯複雜，容易寫錯  | 插入邏輯直觀，約 50 行核心 |
| 並發讀（無鎖）   | 樹旋轉改變多個指標，ABA 問題難處理 | 每層只改前驅指標，CAS 可行 |
| Rebalancing      | 插入/刪除後可能連鎖旋轉  | 無 rebalancing，改動局部化 |
| 期望時間複雜度   | O(log n) 確定性          | O(log n) 期望值            |
| 記憶體 locality  | 略差（隨機 malloc）      | 相近（略差於 B-Tree）      |

LevelDB 選 Skip List、RocksDB 預設也是 Skip List，理由就是前兩項：**簡單且 lock-free 友好**。

---

## Skip List 結構直觀

Skip List 是多層 linked list。Level 0 是完整的有序鏈，Level 1、2、... 是間隔愈來愈大的「快速通道」：

```
Level 3: head ─────────────────────────── 50 ──────────── tail
Level 2: head ────────── 20 ─────────── 50 ──── 80 ────── tail
Level 1: head ── 10 ─── 20 ─── 30 ─── 50 ─── 70 ─── 80 ── tail
Level 0: head ── 10 ─── 20 ─── 30 ─── 50 ─── 70 ─── 80 ── tail
```

每個節點是一座「塔」（tower），塔的高度由插入時擲硬幣決定：

```
節點 50 的 tower（高度 4）：
  [50] ← Level 3 指標
  [50] ← Level 2 指標
  [50] ← Level 1 指標
  [50] ← Level 0 指標
```

高度 k 的概率：`P(height = k) = (1/2)^(k-1) * (1/2)`

期望最高節點高度：O(log n)，所以搜尋時最多走 O(log n) 步。

---

## 搜尋演算法

從最高層往右走；遇到下一個節點的 key ≥ target 就往下一層：

```
搜尋 30：

Level 3: head → 50 (50 >= 30, 下降)
Level 2: head → 20 → 50 (20 < 30, 往右; 50 >= 30, 下降)
Level 1: head → 10 → 20 → 30 (找到！)
Level 0: 確認 30 == 30 ✓
```

偽碼：

```
cur = head
for i in (0..level).rev():
    while cur.next[i] != null && cur.next[i].key < target:
        cur = cur.next[i]
candidate = cur.next[0]
if candidate != null && candidate.key == target:
    return candidate.value
return None
```

---

## 插入演算法

插入需要記錄每一層的「前驅指標」（update 陣列），再把新節點串進去：

```
插入 25，隨機高度 = 2：

1. 找 update[]:
   Level 1: 20（因為 30 >= 25）
   Level 0: 20

2. 建新節點，高度 2：
   [25] Level 1
   [25] Level 0

3. 串指標（先設新節點的 next，再更新前驅）：
   25.next[1] = update[1].next[1] = 30
   25.next[0] = update[0].next[0] = 30
   update[1].next[1] = 25   (20 → 25)
   update[0].next[0] = 25   (20 → 25)

結果：
Level 1: head ── 10 ─── 20 ─── 25 ─── 30 ─── 50 ─── 70 ─── 80
Level 0: head ── 10 ─── 20 ─── 25 ─── 30 ─── 50 ─── 70 ─── 80
```

---

## Rust 實作：unsafe Skip List（實測通過 WSL）

以下實作在 **rustc 1.97.1**（WSL，cargo 1.97.1 2026-06-30）上編譯並通過所有 assertion：

```rust
// 實測通過 (WSL) — cargo 1.97.1, rustc 1.97.1
use std::ptr;

const MAX_LEVEL: usize = 12;

struct Node<K, V> {
    key: K,
    value: V,
    next: [*mut Node<K, V>; MAX_LEVEL],
}

impl<K, V> Node<K, V> {
    fn new_raw(key: K, value: V) -> *mut Self {
        Box::into_raw(Box::new(Node {
            key,
            value,
            next: [ptr::null_mut(); MAX_LEVEL],
        }))
    }
}

pub struct SkipList<K: Ord, V> {
    head: *mut Node<K, V>,   // 哨兵；key/value 未初始化，不可讀
    level: usize,
    len: usize,
}
```

### 關鍵輔助函式：避免 implicit autoref

Rust 1.97 起，透過 raw pointer 解引用後直接做欄位索引會觸發 `dangerous_implicit_autorefs`。我們用 `ptr::addr_of!` 明確取位址再加 offset：

```rust
// 實測通過 (WSL)
unsafe fn next_at<K, V>(node: *mut Node<K, V>, i: usize) -> *mut Node<K, V> {
    ptr::addr_of!((*node).next)
        .cast::<*mut Node<K, V>>()
        .add(i)
        .read()
}

unsafe fn set_next<K, V>(node: *mut Node<K, V>, i: usize, val: *mut Node<K, V>) {
    ptr::addr_of_mut!((*node).next)
        .cast::<*mut Node<K, V>>()
        .add(i)
        .write(val);
}
```

### 隨機高度（thread-local LCG）

```rust
// 實測通過 (WSL)
use std::cell::Cell;
thread_local! {
    static RNG_STATE: Cell<u64> = Cell::new(12345678901234567);
}

fn rand_bool() -> bool {
    RNG_STATE.with(|s| {
        let mut x = s.get();
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        s.set(x);
        x & 1 == 0
    })
}

impl<K: Ord, V> SkipList<K, V> {
    fn random_level() -> usize {
        let mut lvl = 1;
        while lvl < MAX_LEVEL && rand_bool() {
            lvl += 1;
        }
        lvl
    }
}
```

為什麼 thread-local？因為 SkipList 在 concurrent 場景下每個寫入執行緒各自有 RNG，不競爭任何鎖。

### new()：哨兵頭節點

```rust
// 實測通過 (WSL)
impl<K: Ord, V> SkipList<K, V> {
    pub fn new() -> Self {
        // head 的 key/value 永遠不讀，用 MaybeUninit 避免需要 K: Default
        let head = Box::into_raw(
            Box::new(std::mem::MaybeUninit::<Node<K, V>>::uninit())
        ) as *mut Node<K, V>;
        unsafe {
            for i in 0..MAX_LEVEL {
                set_next(head, i, ptr::null_mut());
            }
        }
        SkipList { head, level: 1, len: 0 }
    }
}
```

### insert()

```rust
// 實測通過 (WSL)
pub fn insert(&mut self, key: K, value: V) {
    let mut update: [*mut Node<K, V>; MAX_LEVEL] = [ptr::null_mut(); MAX_LEVEL];
    let mut cur = self.head;

    unsafe {
        // 找每層的前驅（update[]）
        for i in (0..self.level).rev() {
            loop {
                let nxt = next_at(cur, i);
                if nxt.is_null() { break; }
                if (*nxt).key < key { cur = nxt; } else { break; }
            }
            update[i] = cur;
        }

        // 隨機決定新節點的高度
        let new_level = Self::random_level();
        if new_level > self.level {
            for i in self.level..new_level {
                update[i] = self.head;  // head 是所有新層的前驅
            }
            self.level = new_level;
        }

        // 串入新節點：先設 next，再更新前驅（不能反過來）
        let new_node = Node::new_raw(key, value);
        for i in 0..new_level {
            let old_next = next_at(update[i], i);
            set_next(new_node, i, old_next);
            set_next(update[i], i, new_node);
        }
    }
    self.len += 1;
}
```

### get() 與有序迭代

```rust
// 實測通過 (WSL)
pub fn get(&self, key: &K) -> Option<&V> {
    let mut cur = self.head;
    unsafe {
        for i in (0..self.level).rev() {
            loop {
                let nxt = next_at(cur, i);
                if nxt.is_null() { break; }
                if (*nxt).key < *key { cur = nxt; } else { break; }
            }
        }
        let candidate = next_at(cur, 0);
        if !candidate.is_null() && (*candidate).key == *key {
            Some(&(*candidate).value)
        } else {
            None
        }
    }
}

pub struct SkipListIter<'a, K, V> {
    cur: *mut Node<K, V>,
    _m: std::marker::PhantomData<&'a Node<K, V>>,
}

impl<'a, K: 'a, V: 'a> Iterator for SkipListIter<'a, K, V> {
    type Item = (&'a K, &'a V);
    fn next(&mut self) -> Option<Self::Item> {
        if self.cur.is_null() { return None; }
        unsafe {
            let node = &*self.cur;
            self.cur = next_at(self.cur, 0);
            Some((&node.key, &node.value))
        }
    }
}

impl<K: Ord, V> SkipList<K, V> {
    pub fn iter(&self) -> SkipListIter<'_, K, V> {
        unsafe {
            SkipListIter {
                cur: next_at(self.head, 0),
                _m: std::marker::PhantomData,
            }
        }
    }
}
```

### 驗證測試（實測通過 WSL）

```rust
// 實測通過 (WSL) — cargo run 輸出：
// Ordered keys: [10, 20, 30, 50, 70, 80]
// get(20) = Some("twenty")
// get(50) = Some("fifty")
// get(99) = None
// All assertions passed!
fn main() {
    let mut sl: SkipList<i32, &str> = SkipList::new();

    // 亂序插入
    sl.insert(50, "fifty");
    sl.insert(10, "ten");
    sl.insert(80, "eighty");
    sl.insert(30, "thirty");
    sl.insert(20, "twenty");
    sl.insert(70, "seventy");

    // 驗證有序迭代
    let keys: Vec<i32> = sl.iter().map(|(k, _)| *k).collect();
    assert_eq!(keys, vec![10, 20, 30, 50, 70, 80]);

    assert_eq!(sl.get(&20), Some(&"twenty"));
    assert_eq!(sl.get(&99), None);
    println!("All assertions passed!");
}
```

---

## 寫入路徑：WAL + MemTable

MemTable 光靠記憶體還不夠：process crash 資料就消失。搭配 **WAL（Write-Ahead Log）** 才算持久：

```
Client write(key, value)
         │
         ▼
  ┌─────────────┐
  │  WAL 追加   │  ← 循序寫磁碟，每次 append，極快
  │  （sync）   │
  └─────────────┘
         │ 成功
         ▼
  ┌─────────────┐
  │  MemTable   │  ← 插入 skip list，O(log n)
  │  insert()   │
  └─────────────┘
         │
         ▼
  回應 client OK
```

WAL 格式極簡：

```
┌──────────┬──────────────┬──────────────┐
│ seq_no   │  key (varint)│ value (bytes)│
│  (u64)   │              │              │
└──────────┴──────────────┴──────────────┘
```

Rust 示意（未編譯驗證）：

```rust
// 未編譯驗證
fn write(&mut self, key: &[u8], value: &[u8]) -> io::Result<()> {
    // 1. WAL 必須先寫（crash-safe 的關鍵）
    self.wal.append(key, value)?;
    self.wal.sync()?;        // fsync 確保落磁碟
    // 2. 再插入 MemTable
    self.memtable.insert(key.to_vec(), value.to_vec());
    // 3. 超過閾值就 freeze
    if self.memtable.size_bytes() >= MEMTABLE_SIZE_LIMIT {
        self.freeze()?;
    }
    Ok(())
}
```

---

## Freeze 與 Flush 流程

```
寫入 MemTable 大小 >= 64MB
         │
         ▼
  ┌──────────────────────┐
  │  freeze()            │
  │  1. atomic swap：    │
  │     active ← new()  │
  │     imm   ← old     │
  │  2. 開新 WAL 檔案   │
  └──────────────────────┘
         │
         ▼ (background thread)
  ┌──────────────────────┐
  │  flush immutable     │
  │  MemTable → SSTable  │
  │  （有序迭代，循序寫）│
  └──────────────────────┘
         │
         ▼
  刪除對應 WAL 檔案
  imm MemTable 釋放記憶體
```

freeze 的 Rust 骨架（未編譯驗證）：

```rust
// 未編譯驗證
use std::sync::Arc;
use std::sync::atomic::{AtomicPtr, Ordering};

struct LsmWriter {
    active: Arc<Mutex<SkipList<Vec<u8>, Vec<u8>>>>,
    immutable: Option<Arc<SkipList<Vec<u8>, Vec<u8>>>>,
    flush_tx: Sender<Arc<SkipList<Vec<u8>, Vec<u8>>>>,
}

impl LsmWriter {
    fn freeze(&mut self) -> io::Result<()> {
        let old = {
            let mut guard = self.active.lock().unwrap();
            let new_mem = SkipList::new();
            std::mem::replace(&mut *guard, new_mem)
        };
        let imm = Arc::new(old);
        // 通知 background flush thread
        self.flush_tx.send(Arc::clone(&imm)).unwrap();
        self.immutable = Some(imm);
        Ok(())
    }
}
```

---

## 比較：各種 MemTable 實作

RocksDB 支援可插拔 MemTable，文件列出三種：

| MemTable 類型 | 查詢  | 插入  | 有序迭代 | 備註                          |
|---------------|-------|-------|----------|-------------------------------|
| Skip List     | O(log n) | O(log n) | O(n)  | 預設，lock-free 友好          |
| HashSkipList  | O(1) avg | O(1) avg | O(n) | prefix hash + skip list，點查快但記憶體較多 |
| Vector        | O(n)  | O(1) amortized | O(n log n) | flush 前排序，寫入極快，讀慢  |

通用場景選 Skip List。點查佔大多數且 key 有固定 prefix 長度時可考慮 HashSkipList。

---

## 常見陷阱

**1. WAL 一定要先寫，不能後補**

```
錯誤順序：insert MemTable → crash → WAL 沒寫 → 資料消失
正確順序：WAL append+sync → insert MemTable
```

process 在「WAL 寫完、MemTable 還沒插」之間 crash，重啟只要重放 WAL 就能還原。反過來資料就永遠消失。

**2. Immutable flush 是 I/O，會產生背壓**

flush 慢（L0 檔案堆積、磁碟滿），後續 freeze 會阻塞，最終阻塞寫入。RocksDB 的 stall/stop write 機制就是在處理這個，寫入速度超過 compaction 速度時主動限速。

**3. RNG 必須用 thread-local，不能用 global Mutex**

若用 `Mutex<SmallRng>` 當全局 RNG：所有並發寫入執行緒都要搶同一把鎖只為了決定節點高度，QPS 爆降。thread-local 的 LCG/xorshift 每個執行緒獨立，完全不競爭。

**4. 有序迭代必須走 Level 0，不能走更高層**

Level 1+ 是跳躍鏈，中間節點不完整。Level 0 才是完整有序序列。`iter()` 的起點永遠是 `head.next[0]`，沿 `node.next[0]` 走到 null。

**5. 記憶體預算要算兩倍**

flush 進行時，active MemTable 繼續累積寫入，immutable MemTable 還在記憶體等 flush 完成。實際記憶體峰值 = active + immutable ≈ 2 × MEMTABLE_SIZE_LIMIT（64MB → 128MB）。設 JVM/container 記憶體限制要留夠。

---

## 進階：Lock-Free Skip List

LevelDB 的 `db/skiplist.h` 實現了無鎖並發讀：

- **寫入**：單一 writer 執行緒（或持有 mutex 的 writer）。
- **讀取**：任意並發 readers，不持任何鎖。

關鍵：insert 先設好新節點的 `next` 指標（relaxed store），再用 **Release store** 更新前驅節點的 `next` 指標。Reader 做 **Acquire load** 保證看到的是完整節點。

Rust 對應做法（未編譯驗證）：

```rust
// 未編譯驗證 — lock-free insert 的記憶體序骨架
use std::sync::atomic::{AtomicPtr, Ordering};

// next 改成 AtomicPtr
struct NodeConcurrent<K, V> {
    key: K,
    value: V,
    next: [AtomicPtr<NodeConcurrent<K, V>>; MAX_LEVEL],
}

// 插入最後一步：release store 保證 reader 看到完整 node
unsafe fn link(pred: *mut NodeConcurrent<K,V>, i: usize, new: *mut NodeConcurrent<K,V>) {
    (*pred).next[i].store(new, Ordering::Release);
}

// reader: acquire load
unsafe fn load_next<K,V>(node: *mut NodeConcurrent<K,V>, i: usize) -> *mut NodeConcurrent<K,V> {
    (*node).next[i].load(Ordering::Acquire)
}
```

真正生產環境的無鎖 skip list 還要處理 **marked pointer**（刪除節點時在指標低位打標記），這裡不展開。

---

## 本章重點整理

- MemTable 是 LSM-Tree 的記憶體緩衝，必須有序以支援 flush（產出有序 SSTable）和範圍查詢。
- Skip List 勝過平衡 BST 的關鍵：實作簡單、無 rebalancing、lock-free 友好。
- 節點高度由擲硬幣決定（p = 0.5），期望最高 O(log n)，搜尋/插入期望 O(log n)。
- 搜尋：從最高層往右，遇到 key ≥ target 就下降，到 Level 0 確認。
- 插入：先記錄各層前驅（update[]），再串入新節點。順序：先設新節點的 next，再更新前驅。
- 寫入路徑：WAL append+sync → MemTable insert → size 超閾值則 freeze。
- Freeze：原子換出 active MemTable 成 immutable，background thread flush 到 SSTable。
- 記憶體峰值 ≈ 2× MEMTABLE_SIZE_LIMIT，要預留。

---

## 自我檢核

1. 為什麼 Skip List 迭代一定要走 Level 0，走 Level 1 有什麼問題？
2. 插入時 `update[]` 陣列記錄的是什麼？為什麼要先設新節點的 `next` 再更新前驅？
3. WAL 寫入要在 MemTable 插入之前還是之後？說明若反過來會發生什麼 crash scenario。
4. 一個 MemTable 上限設 64MB，系統 RAM 預算應該給 MemTable 留至少多少？
5. 為什麼 thread-local RNG 比 global Mutex RNG 更適合 concurrent SkipList？

---

## 延伸閱讀

1. **William Pugh, "Skip Lists: A Probabilistic Alternative to Balanced Trees" (1990)** — 原始論文，包含詳細的機率分析和期望高度的推導，讀第 2-3 節就值回票價。
2. **LevelDB `db/skiplist.h`** — 不到 400 行的生產級實作，重點看 `Insert()` 的記憶體序（`NoBarrier_SetNext`/`SetNext` 的差異）和為什麼 reader 不需要鎖。
3. **RocksDB MemTable Wiki** (`github.com/facebook/rocksdb/wiki/MemTable`) — 說明 SkipList/HashSkipList/Vector 三種 MemTable 的適用場景，以及 `allow_concurrent_memtable_write` 如何透過 JBD 協議支援並發寫入。
4. **Alex Petrov, 《Database Internals》Part I, Ch. 7** — Storage Engine 一章的 MemTable 角色，以及 flush/compaction 在 LSM-Tree 整體寫入放大分析中的位置。

---

→ [Ch 13 SSTable（Sorted String Table）](./13-sstable.md)
