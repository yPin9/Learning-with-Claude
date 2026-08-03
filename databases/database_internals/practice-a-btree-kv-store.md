# 練習 A — 可持久化 B+tree KV Store

> **目標**：把 Ch 4–10 學到的所有東西組成一個真正能用的 KV 引擎：支援 `put / get / delete / range_scan`，資料存到檔案，buffer pool 管理頁面，重開程式之後資料還在。

## 背景動機

到目前為止，我們的 B+tree 都是純記憶體的——樹在 `Vec<Node>` 裡，程式一關資料就消失。一個真實的儲存引擎必須：

1. 把樹的節點以固定大小的**頁面（page）**存到磁碟
2. 用**buffer pool** 快取熱頁，避免每次讀寫都做 I/O
3. 樹的根頁面位置要持久化，下次開機知道從哪裡開始讀

這個練習把這些零件拼起來，做出一個可以真跑的 KV store。規模小，但每一行都和工業級 SQLite/InnoDB 的對應部分直接呼應。

## 任務規格

### 功能需求

| 操作 | 說明 |
|---|---|
| `put(key: i64, value: &str)` | 插入或覆蓋 |
| `get(key: i64) -> Option<String>` | 精確查找 |
| `delete(key: i64) -> bool` | 刪除，不存在回傳 false |
| `range_scan(lo: i64, hi: i64) -> Vec<(i64, String)>` | 閉區間有序回傳 |

### 持久化需求

- 資料存在一個二進位檔案（例如 `data.db`）
- **Page size = 4096 bytes**，固定大小
- **Page 0**：存 metadata（root page id、total page count）
- 重開程式後，用相同路徑建立 `KvStore` 仍能讀到之前寫入的資料

### Buffer Pool

- 容量：最多快取 **8 個頁面**（可用常數調整）
- 替換策略：簡單 FIFO 或 clock 都可（不要求 LRU，但鼓勵）
- Dirty page tracking：修改過的頁面在被驅逐前必須先寫回磁碟

### 限制

- Key：`i64`；Value：字串，最長 200 bytes（超長截斷或報錯都可）
- 不需要實作 WAL 或 crash recovery（那是 Part 3 的主題）
- 不需要並發安全（單執行緒即可）
- B+tree Order = 4（每個 leaf 最多 4 個 key-value pair）

### 驗收條件

1. `cargo test` 全部通過
2. 通過 **持久化測試**：第一個程序寫入，退出，第二個程序開啟同一個檔案，能正確 `get` 和 `range_scan`
3. 100 筆亂序插入後，`range_scan(i64::MIN, i64::MAX)` 的輸出完全有序

## 期望輸出範例

```
$ cargo run -- put 10 hello
$ cargo run -- put 30 world
$ cargo run -- put 20 rust
$ cargo run -- get 20
rust
$ cargo run -- range 10 30
(10, "hello")
(20, "rust")
(30, "world")
$ cargo run -- delete 20
deleted
$ cargo run -- get 20
not found
$ cargo run -- range 10 30
(10, "hello")
(30, "world")
```

重開之後：

```
$ cargo run -- range 10 30
(10, "hello")
(30, "world")
```

## 如果你卡住了

**Page 格式想不出來怎麼排？** 先從最簡單的開始：leaf page 就是 `[is_leaf:1][key_count:2][key_0:8][val_len_0:2][val_0:200][key_1:8]...`，計算好不超過 4096 bytes 即可。internal page 把 value 換成 child page id（`u32`，4 bytes）。

**Buffer pool 不知道怎麼架？** 先做一個 `HashMap<u32, [u8; 4096]>` 作為 page cache，加一個 `dirty: HashSet<u32>`，每次 `get_page` 先查 cache 沒有才讀磁碟，`flush` 時把 dirty 的頁面寫回去。

**Root page 怎麼追蹤？** Page 0 專門存 metadata，格式：`[magic:4]["KVDB"][root_page_id:4][total_pages:4]`。每次打開檔案先讀 page 0，拿到 root；關閉前更新 page 0。

**測試持久化怎麼測？** 用 `std::process::Command` 跑子程序，或者在測試裡建兩個 `KvStore` 實例（第一個 `drop` 會 flush），直接在同一個測試函式內驗證。

**B+tree 的 split/merge 邏輯不確定？** 先用 Ch 7 的 insert+split 和 Ch 8 的 delete+merge 的邏輯，把 `usize` node id 換成 `u32` page id 就差不多了。arena 變成「buffer pool + 磁碟」。

## 分段實作建議

### Step 1：Page 格式與磁碟 I/O（~60 行）

```rust
const PAGE_SIZE: usize = 4096;
const ORDER: usize = 4;

struct DiskManager {
    file: std::fs::File,
    total_pages: u32,
}

impl DiskManager {
    fn open(path: &str) -> Self { ... }
    fn read_page(&mut self, page_id: u32) -> [u8; PAGE_SIZE] { ... }
    fn write_page(&mut self, page_id: u32, data: &[u8; PAGE_SIZE]) { ... }
    fn alloc_page(&mut self) -> u32 { ... }  // 在檔案尾端分配新頁
}
```

驗證：寫一個頁面再讀回來，bytes 完全相同。

### Step 2：Buffer Pool（~80 行）

```rust
struct BufferPool {
    disk: DiskManager,
    cache: HashMap<u32, [u8; PAGE_SIZE]>,
    dirty: HashSet<u32>,
    order: Vec<u32>,  // FIFO 用
}

impl BufferPool {
    fn get_page(&mut self, id: u32) -> &[u8; PAGE_SIZE] { ... }
    fn get_page_mut(&mut self, id: u32) -> &mut [u8; PAGE_SIZE] { ... }
    fn flush_all(&mut self) { ... }  // 把所有 dirty page 寫回磁碟
}
```

驗證：寫入 page 3，讓 buffer pool 驅逐它，再讀回，內容一致。

### Step 3：Node 序列化 / 反序列化（~100 行）

定義頁面 layout 並實作 encode/decode：

```rust
struct LeafPage {
    keys: Vec<i64>,
    values: Vec<String>,
    next_leaf: Option<u32>,  // u32::MAX 代表 None
}

struct InternalPage {
    keys: Vec<i64>,
    children: Vec<u32>,  // page ids
}

fn encode_leaf(page: &LeafPage) -> [u8; PAGE_SIZE] { ... }
fn decode_leaf(data: &[u8; PAGE_SIZE]) -> LeafPage { ... }
fn encode_internal(page: &InternalPage) -> [u8; PAGE_SIZE] { ... }
fn decode_internal(data: &[u8; PAGE_SIZE]) -> InternalPage { ... }
```

驗證：encode 一個 leaf，decode 回來，資料相同。

### Step 4：B+tree 操作（~200 行）

把 Ch 7–8 的邏輯套進來，node id 換成 page id，每次讀節點從 buffer pool 拿，每次修改後標記 dirty：

```rust
struct KvStore {
    bp: BufferPool,
    root: u32,  // root page id
}

impl KvStore {
    pub fn open(path: &str) -> Self { ... }
    pub fn put(&mut self, key: i64, value: &str) { ... }
    pub fn get(&mut self, key: i64) -> Option<String> { ... }
    pub fn delete(&mut self, key: i64) -> bool { ... }
    pub fn range_scan(&mut self, lo: i64, hi: i64) -> Vec<(i64, String)> { ... }
}

impl Drop for KvStore {
    fn drop(&mut self) {
        self.bp.flush_all();
        // 更新 page 0 的 metadata
    }
}
```

驗證：put 50 個 key，range\_scan 全部，結果有序。

### Step 5：持久化測試與 CLI（~40 行）

加一個簡單的 CLI（讀 `argv`），讓你能在終端機驗證跨程序的持久化。再加一個 `#[test]` 自動測試：

```rust
#[test]
fn test_persistence() {
    let path = "/tmp/test_kv.db";
    let _ = std::fs::remove_file(path);

    {
        let mut kv = KvStore::open(path);
        kv.put(10, "hello");
        kv.put(30, "world");
        kv.put(20, "rust");
        // Drop → flush
    }

    {
        let mut kv = KvStore::open(path);
        assert_eq!(kv.get(20), Some("rust".to_string()));
        let r = kv.range_scan(10, 30);
        assert_eq!(r[0].0, 10);
        assert_eq!(r[1].0, 20);
        assert_eq!(r[2].0, 30);
    }
    let _ = std::fs::remove_file(path);
}
```

## 完整參考解答

**寫完再看！** 對照自己的實作，看你在哪裡做了不同的設計選擇，以及為什麼。

<details>
<summary>點開參考實作（可跑 Rust crate）</summary>

```rust
// src/lib.rs
// WSL 驗證：wsl cargo test -- --nocapture  全部通過

use std::collections::{HashMap, HashSet, VecDeque};
use std::fs::{File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};

// ── 常數 ─────────────────────────────────────────────────────
pub const PAGE_SIZE: usize = 4096;
pub const ORDER: usize = 4;           // leaf 最多 4 個 key-value
pub const BP_CAPACITY: usize = 8;     // buffer pool 最多 8 頁
pub const MAGIC: &[u8; 4] = b"KVDB";

// ── DiskManager ──────────────────────────────────────────────
struct DiskManager {
    file: File,
    total_pages: u32,
}

impl DiskManager {
    fn open(path: &str) -> Self {
        let file = OpenOptions::new()
            .read(true).write(true).create(true)
            .open(path)
            .expect("open db file");
        let size = file.metadata().unwrap().len();
        let total_pages = (size / PAGE_SIZE as u64) as u32;
        DiskManager { file, total_pages }
    }

    fn read_page(&mut self, id: u32) -> [u8; PAGE_SIZE] {
        let mut buf = [0u8; PAGE_SIZE];
        if id < self.total_pages {
            self.file.seek(SeekFrom::Start(id as u64 * PAGE_SIZE as u64)).unwrap();
            self.file.read_exact(&mut buf).unwrap();
        }
        buf
    }

    fn write_page(&mut self, id: u32, data: &[u8; PAGE_SIZE]) {
        self.file.seek(SeekFrom::Start(id as u64 * PAGE_SIZE as u64)).unwrap();
        self.file.write_all(data).unwrap();
        if id >= self.total_pages { self.total_pages = id + 1; }
    }

    fn alloc_page(&mut self) -> u32 {
        let id = self.total_pages;
        self.total_pages += 1;
        // 寫一個空頁確保檔案延伸
        let empty = [0u8; PAGE_SIZE];
        self.write_page(id, &empty);
        id
    }

    fn flush(&mut self) {
        self.file.flush().unwrap();
    }
}

// ── Buffer Pool ───────────────────────────────────────────────
struct BufferPool {
    disk: DiskManager,
    cache: HashMap<u32, [u8; PAGE_SIZE]>,
    dirty: HashSet<u32>,
    fifo: VecDeque<u32>,
}

impl BufferPool {
    fn new(disk: DiskManager) -> Self {
        BufferPool {
            disk,
            cache: HashMap::new(),
            dirty: HashSet::new(),
            fifo: VecDeque::new(),
        }
    }

    fn ensure_loaded(&mut self, id: u32) {
        if !self.cache.contains_key(&id) {
            // 若滿了，驅逐最舊的乾淨頁（或髒頁先 flush）
            if self.cache.len() >= BP_CAPACITY {
                let victim = self.fifo.pop_front().unwrap();
                if self.dirty.contains(&victim) {
                    let data = self.cache[&victim];
                    self.disk.write_page(victim, &data);
                    self.dirty.remove(&victim);
                }
                self.cache.remove(&victim);
            }
            let data = self.disk.read_page(id);
            self.cache.insert(id, data);
            self.fifo.push_back(id);
        }
    }

    fn get_page(&mut self, id: u32) -> &[u8; PAGE_SIZE] {
        self.ensure_loaded(id);
        self.cache.get(&id).unwrap()
    }

    fn get_page_mut(&mut self, id: u32) -> &mut [u8; PAGE_SIZE] {
        self.ensure_loaded(id);
        self.dirty.insert(id);
        self.cache.get_mut(&id).unwrap()
    }

    fn alloc_page(&mut self) -> u32 {
        let id = self.disk.alloc_page();
        let empty = [0u8; PAGE_SIZE];
        self.cache.insert(id, empty);
        self.fifo.push_back(id);
        self.dirty.insert(id);
        id
    }

    fn flush_all(&mut self) {
        let dirty_ids: Vec<u32> = self.dirty.iter().copied().collect();
        for id in dirty_ids {
            let data = self.cache[&id];
            self.disk.write_page(id, &data);
        }
        self.dirty.clear();
        self.disk.flush();
    }
}

// ── Page 格式 ─────────────────────────────────────────────────
// Byte layout:
//   [0]      is_leaf: u8  (1 = leaf, 0 = internal)
//   [1..3]   key_count: u16
//   Leaf:
//     per key: [key:i64(8)] [val_len:u16(2)] [val:200 bytes]
//     at end:  [next_leaf:u32(4)]  (0xFFFFFFFF = None)
//   Internal:
//     [child_0:u32(4)] [key_0:i64(8)] [child_1:u32(4)] [key_1:i64(8)] ...

const VAL_SLOT: usize = 210; // 8(key) + 2(len) + 200(val)
const LEAF_HEADER: usize = 3;
const LEAF_NEXT_OFFSET: usize = PAGE_SIZE - 4;

fn encode_leaf(keys: &[i64], values: &[String], next: Option<u32>) -> [u8; PAGE_SIZE] {
    let mut buf = [0u8; PAGE_SIZE];
    buf[0] = 1;
    let n = keys.len() as u16;
    buf[1..3].copy_from_slice(&n.to_le_bytes());
    let mut off = LEAF_HEADER;
    for i in 0..keys.len() {
        buf[off..off+8].copy_from_slice(&keys[i].to_le_bytes());
        off += 8;
        let v = values[i].as_bytes();
        let vlen = v.len().min(200) as u16;
        buf[off..off+2].copy_from_slice(&vlen.to_le_bytes());
        off += 2;
        buf[off..off + vlen as usize].copy_from_slice(&v[..vlen as usize]);
        off += 200;
    }
    let next_val = next.unwrap_or(u32::MAX);
    buf[LEAF_NEXT_OFFSET..LEAF_NEXT_OFFSET+4].copy_from_slice(&next_val.to_le_bytes());
    buf
}

fn decode_leaf(buf: &[u8; PAGE_SIZE]) -> (Vec<i64>, Vec<String>, Option<u32>) {
    assert_eq!(buf[0], 1);
    let n = u16::from_le_bytes([buf[1], buf[2]]) as usize;
    let mut keys = Vec::with_capacity(n);
    let mut values = Vec::with_capacity(n);
    let mut off = LEAF_HEADER;
    for _ in 0..n {
        let k = i64::from_le_bytes(buf[off..off+8].try_into().unwrap());
        off += 8;
        let vlen = u16::from_le_bytes([buf[off], buf[off+1]]) as usize;
        off += 2;
        let v = std::str::from_utf8(&buf[off..off+vlen]).unwrap_or("").to_string();
        off += 200;
        keys.push(k);
        values.push(v);
    }
    let next_raw = u32::from_le_bytes(buf[LEAF_NEXT_OFFSET..LEAF_NEXT_OFFSET+4].try_into().unwrap());
    let next = if next_raw == u32::MAX { None } else { Some(next_raw) };
    (keys, values, next)
}

fn encode_internal(keys: &[i64], children: &[u32]) -> [u8; PAGE_SIZE] {
    let mut buf = [0u8; PAGE_SIZE];
    buf[0] = 0;
    let n = keys.len() as u16;
    buf[1..3].copy_from_slice(&n.to_le_bytes());
    let mut off = 3;
    // layout: child_0, key_0, child_1, key_1, ..., child_n
    for i in 0..children.len() {
        buf[off..off+4].copy_from_slice(&children[i].to_le_bytes());
        off += 4;
        if i < keys.len() {
            buf[off..off+8].copy_from_slice(&keys[i].to_le_bytes());
            off += 8;
        }
    }
    buf
}

fn decode_internal(buf: &[u8; PAGE_SIZE]) -> (Vec<i64>, Vec<u32>) {
    assert_eq!(buf[0], 0);
    let n = u16::from_le_bytes([buf[1], buf[2]]) as usize;
    let mut keys = Vec::with_capacity(n);
    let mut children = Vec::with_capacity(n + 1);
    let mut off = 3;
    for i in 0..=n {
        let c = u32::from_le_bytes(buf[off..off+4].try_into().unwrap());
        off += 4;
        children.push(c);
        if i < n {
            let k = i64::from_le_bytes(buf[off..off+8].try_into().unwrap());
            off += 8;
            keys.push(k);
        }
    }
    (keys, children)
}

// ── Metadata（Page 0）────────────────────────────────────────
fn read_meta(bp: &mut BufferPool) -> (u32, bool) {
    // 回傳 (root_page_id, is_new_db)
    let buf = *bp.get_page(0);
    if &buf[0..4] != MAGIC {
        // 新資料庫
        (u32::MAX, true)
    } else {
        let root = u32::from_le_bytes(buf[4..8].try_into().unwrap());
        (root, false)
    }
}

fn write_meta(bp: &mut BufferPool, root: u32) {
    let buf = bp.get_page_mut(0);
    buf[0..4].copy_from_slice(MAGIC);
    buf[4..8].copy_from_slice(&root.to_le_bytes());
}

// ── KvStore ───────────────────────────────────────────────────
pub struct KvStore {
    bp: BufferPool,
    root: u32,
}

impl KvStore {
    pub fn open(path: &str) -> Self {
        let disk = DiskManager::open(path);
        let mut bp = BufferPool::new(disk);

        // 確保 page 0 存在
        if bp.disk.total_pages == 0 {
            bp.disk.alloc_page(); // page 0 = metadata
        }

        let (root, is_new) = read_meta(&mut bp);
        let root = if is_new {
            // 建立第一個 leaf page 作為 root
            let leaf_id = bp.alloc_page();
            let data = encode_leaf(&[], &[], None);
            bp.get_page_mut(leaf_id).copy_from_slice(&data);
            write_meta(&mut bp, leaf_id);
            leaf_id
        } else {
            root
        };

        KvStore { bp, root }
    }

    // ── search ────────────────────────────────────────────────
    pub fn get(&mut self, key: i64) -> Option<String> {
        let leaf_id = self.find_leaf(self.root, key);
        let buf = *self.bp.get_page(leaf_id);
        let (keys, values, _) = decode_leaf(&buf);
        keys.iter().position(|&k| k == key).map(|i| values[i].clone())
    }

    fn find_leaf(&mut self, mut id: u32, key: i64) -> u32 {
        loop {
            let buf = *self.bp.get_page(id);
            if buf[0] == 1 { return id; } // leaf
            let (keys, children) = decode_internal(&buf);
            let pos = keys.partition_point(|&k| k <= key);
            id = children[pos.min(children.len()-1)];
        }
    }

    fn find_path(&mut self, key: i64) -> Vec<u32> {
        let mut path = vec![self.root];
        let mut id = self.root;
        loop {
            let buf = *self.bp.get_page(id);
            if buf[0] == 1 { break; }
            let (keys, children) = decode_internal(&buf);
            let pos = keys.partition_point(|&k| k <= key);
            id = children[pos.min(children.len()-1)];
            path.push(id);
        }
        path
    }

    // ── insert / put ──────────────────────────────────────────
    pub fn put(&mut self, key: i64, value: &str) {
        let path = self.find_path(key);
        let leaf_id = *path.last().unwrap();

        // 讀 leaf
        let buf = *self.bp.get_page(leaf_id);
        let (mut keys, mut values, next) = decode_leaf(&buf);

        // 插入或覆蓋
        let pos = keys.partition_point(|&k| k < key);
        if pos < keys.len() && keys[pos] == key {
            values[pos] = value.to_string();
        } else {
            keys.insert(pos, key);
            values.insert(pos, value.to_string());
        }

        // 寫回
        let data = encode_leaf(&keys, &values, next);
        self.bp.get_page_mut(leaf_id).copy_from_slice(&data);

        // 需要 split？
        if keys.len() > ORDER {
            self.split_leaf(path, leaf_id, keys, values, next);
        }
    }

    fn split_leaf(
        &mut self,
        path: Vec<u32>, leaf_id: u32,
        keys: Vec<i64>, values: Vec<String>, old_next: Option<u32>,
    ) {
        let mid = keys.len() / 2;
        let right_keys = keys[mid..].to_vec();
        let right_values = values[mid..].to_vec();
        let left_keys = keys[..mid].to_vec();
        let left_values = values[..mid].to_vec();

        // 建右 leaf
        let right_id = self.bp.alloc_page();
        let right_data = encode_leaf(&right_keys, &right_values, old_next);
        self.bp.get_page_mut(right_id).copy_from_slice(&right_data);

        // 更新左 leaf
        let left_data = encode_leaf(&left_keys, &left_values, Some(right_id));
        self.bp.get_page_mut(leaf_id).copy_from_slice(&left_data);

        let promote_key = right_keys[0];
        let path_len = path.len();
        self.insert_into_parent(path, path_len-1, promote_key, leaf_id, right_id);
    }

    fn insert_into_parent(
        &mut self,
        path: Vec<u32>, child_depth: usize,
        key: i64, left: u32, right: u32,
    ) {
        if child_depth == 0 {
            // child 是 root → 建新 root
            let new_root = self.bp.alloc_page();
            let data = encode_internal(&[key], &[left, right]);
            self.bp.get_page_mut(new_root).copy_from_slice(&data);
            self.root = new_root;
            write_meta(&mut self.bp, new_root);
            return;
        }

        let parent_id = path[child_depth - 1];
        let buf = *self.bp.get_page(parent_id);
        let (mut pkeys, mut pchildren) = decode_internal(&buf);

        let pos = pkeys.partition_point(|&k| k < key);
        pkeys.insert(pos, key);
        pchildren.insert(pos + 1, right);

        let data = encode_internal(&pkeys, &pchildren);
        self.bp.get_page_mut(parent_id).copy_from_slice(&data);

        // parent overflow？
        if pkeys.len() > ORDER {
            let mid = pkeys.len() / 2;
            let promote = pkeys[mid];
            let right_keys = pkeys[mid+1..].to_vec();
            let right_children = pchildren[mid+1..].to_vec();
            let left_keys = pkeys[..mid].to_vec();
            let left_children = pchildren[..=mid].to_vec();

            let new_right = self.bp.alloc_page();
            let rd = encode_internal(&right_keys, &right_children);
            self.bp.get_page_mut(new_right).copy_from_slice(&rd);

            let ld = encode_internal(&left_keys, &left_children);
            self.bp.get_page_mut(parent_id).copy_from_slice(&ld);

            self.insert_into_parent(path, child_depth-1, promote, parent_id, new_right);
        }
    }

    // ── delete ────────────────────────────────────────────────
    pub fn delete(&mut self, key: i64) -> bool {
        let leaf_id = self.find_leaf(self.root, key);
        let buf = *self.bp.get_page(leaf_id);
        let (mut keys, mut values, next) = decode_leaf(&buf);

        let Some(pos) = keys.iter().position(|&k| k == key) else {
            return false;
        };
        keys.remove(pos);
        values.remove(pos);

        let data = encode_leaf(&keys, &values, next);
        self.bp.get_page_mut(leaf_id).copy_from_slice(&data);
        true
        // 注意：這裡省略了 underflow 處理（merge/rebalance）
        // 完整實作參考 Ch 8；練習的驗收不要求 merge
    }

    // ── range scan ────────────────────────────────────────────
    pub fn range_scan(&mut self, lo: i64, hi: i64) -> Vec<(i64, String)> {
        let mut result = Vec::new();
        let mut leaf_id = self.find_leaf(self.root, lo);

        loop {
            let buf = *self.bp.get_page(leaf_id);
            let (keys, values, next) = decode_leaf(&buf);

            for (i, &k) in keys.iter().enumerate() {
                if k > hi { return result; }
                if k >= lo { result.push((k, values[i].clone())); }
            }

            match next {
                Some(n) => leaf_id = n,
                None => break,
            }
        }
        result
    }
}

impl Drop for KvStore {
    fn drop(&mut self) {
        write_meta(&mut self.bp, self.root);
        self.bp.flush_all();
    }
}

// ── 測試 ─────────────────────────────────────────────────────
#[cfg(test)]
mod tests {
    use super::*;

    fn tmp_path(name: &str) -> String {
        format!("/tmp/kv_test_{}.db", name)
    }

    #[test]
    fn test_basic_put_get() {
        let path = tmp_path("basic");
        let _ = std::fs::remove_file(&path);
        let mut kv = KvStore::open(&path);
        kv.put(10, "hello");
        kv.put(20, "world");
        assert_eq!(kv.get(10), Some("hello".to_string()));
        assert_eq!(kv.get(20), Some("world".to_string()));
        assert_eq!(kv.get(99), None);
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn test_put_overwrite() {
        let path = tmp_path("overwrite");
        let _ = std::fs::remove_file(&path);
        let mut kv = KvStore::open(&path);
        kv.put(10, "first");
        kv.put(10, "second");
        assert_eq!(kv.get(10), Some("second".to_string()));
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn test_delete() {
        let path = tmp_path("delete");
        let _ = std::fs::remove_file(&path);
        let mut kv = KvStore::open(&path);
        kv.put(10, "hello");
        assert!(kv.delete(10));
        assert_eq!(kv.get(10), None);
        assert!(!kv.delete(99));
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn test_range_scan_ordered() {
        let path = tmp_path("range");
        let _ = std::fs::remove_file(&path);
        let mut kv = KvStore::open(&path);
        // 亂序插入
        for &k in &[50i64, 10, 90, 30, 70, 20, 80, 40, 60] {
            kv.put(k, &k.to_string());
        }
        let r = kv.range_scan(20, 70);
        let keys: Vec<i64> = r.iter().map(|(k, _)| *k).collect();
        assert_eq!(keys, vec![20, 30, 40, 50, 60, 70]);
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn test_persistence() {
        let path = tmp_path("persist");
        let _ = std::fs::remove_file(&path);

        // 第一個 KvStore：寫入後 drop（觸發 flush）
        {
            let mut kv = KvStore::open(&path);
            kv.put(10, "hello");
            kv.put(30, "world");
            kv.put(20, "rust");
        }

        // 第二個 KvStore：重新開啟，驗證資料還在
        {
            let mut kv = KvStore::open(&path);
            assert_eq!(kv.get(10), Some("hello".to_string()));
            assert_eq!(kv.get(20), Some("rust".to_string()));
            assert_eq!(kv.get(30), Some("world".to_string()));
            let r = kv.range_scan(10, 30);
            assert_eq!(r[0].0, 10);
            assert_eq!(r[1].0, 20);
            assert_eq!(r[2].0, 30);
        }

        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn test_100_inserts_sorted_scan() {
        let path = tmp_path("large");
        let _ = std::fs::remove_file(&path);
        let mut kv = KvStore::open(&path);

        // 亂序插入 100 筆
        let mut keys: Vec<i64> = (1..=100).map(|i| i * 7 % 101).collect();
        // 去重後排列（7 與 101 互質，0..100 * 7 % 101 不重複）
        keys.sort_unstable();
        keys.dedup();
        let orig_len = keys.len();

        // 先打亂再插入
        use std::collections::hash_map::DefaultHasher;
        use std::hash::{Hash, Hasher};
        let mut shuffled = keys.clone();
        shuffled.sort_by_key(|&k| {
            let mut h = DefaultHasher::new();
            k.hash(&mut h);
            h.finish()
        });
        for k in &shuffled { kv.put(*k, &format!("v{}", k)); }

        let r = kv.range_scan(i64::MIN, i64::MAX);
        assert_eq!(r.len(), orig_len);
        // 驗證有序
        for w in r.windows(2) {
            assert!(w[0].0 < w[1].0, "not sorted: {} >= {}", w[0].0, w[1].0);
        }
        let _ = std::fs::remove_file(&path);
    }
}
```

**WSL 執行**：

```bash
wsl cargo test -- --nocapture
```

</details>

## 測試用例表

| 測試名 | 輸入 | 期望結果 |
|---|---|---|
| `test_basic_put_get` | put(10,"hello"), put(20,"world") | get(10)="hello", get(99)=None |
| `test_put_overwrite` | put(10,"first"), put(10,"second") | get(10)="second" |
| `test_delete` | put(10,"hello"), delete(10) | get(10)=None, delete(99)=false |
| `test_range_scan_ordered` | 亂序插入 9 個 key | range\_scan(20,70) 回傳有序 6 個 |
| `test_persistence` | 第一個 KvStore 寫入，drop，第二個開啟 | 所有 key 仍然可讀 |
| `test_100_inserts_sorted_scan` | 100 筆亂序插入 | range\_scan 全部有序 |

## 延伸挑戰

1. **加上 merge/rebalance**：把 Ch 8 的 delete 邏輯完整搬進來。刪除大量 key 之後，確認樹不會退化成只有很淺的節點。

2. **LRU Buffer Pool**：把 FIFO 替換成 LRU（用 `LinkedHashMap` 或自製的 LRU cache），比較在循環讀取工作負載下的 page miss 率。

3. **加 Crash Safety 預告（WAL 日誌）**：在每個 `put/delete` 之前，先把操作記錄到一個 append-only 的 log 檔；重開時如果發現 log 有未完成的操作，先 replay 再服務請求。這就是 Ch 17 要做的事情——先試試看，感受一下為什麼這比你想的難。

4. **Bloom Filter 加速 get**：在 KvStore 裡加一個記憶體 Bloom Filter（Ch 14 的主題）。`get` 先問 Bloom Filter，若回傳「肯定不存在」就省掉磁碟 I/O。測量加了之後對不存在 key 的查找速度的影響。

5. **Benchmark**：用 `criterion` crate 或手動計時，比較 buffer pool 容量 4 vs 8 vs 32 的吞吐量差異（1000 次隨機 get）。畫出 hit rate 和 IOP 的關係圖。

## 自我檢核

- [ ] 我實作了 page 格式的 encode/decode，而且 round-trip 不損失資料
- [ ] 我的 buffer pool 在 dirty page 被驅逐前會先 flush 到磁碟
- [ ] `Drop` 時確實呼叫了 `flush_all` 和更新 metadata，否則持久化測試一定不過
- [ ] 100 筆亂序插入後，`range_scan` 的結果完全有序（若有序才代表 B+tree 結構正確）
- [ ] 我能說出「next\_leaf 指標壞掉」會讓哪個操作出問題（range\_scan 跳過或死迴圈）
- [ ] 我知道這個實作少了什麼（WAL、crash recovery、merge），以及 Part 3 會補上哪些

---

→ [Ch 11 LSM 原理與三放大](./11-lsm-principles.md)
