# Ch 35 — 進階索引

> **目標**：掌握 B+tree 以外的主要索引結構——hash、bitmap、inverted index——理解各自適用的查詢模式、實作 hash index 與 bitmap index，並能在設計階段正確選擇索引種類。

## 為什麼 B+tree 不夠

B+tree 是個全能選手，但全能意味著每件事都不是最快的。三個典型的 B+tree 痛點：

1. **等值查詢**：`WHERE id = 42`，B+tree 要走 O(log n) 層，hash index 是 O(1)。資料量大時差距明顯。
2. **低基數過濾 + 多條件組合**：`WHERE gender = 'M' AND region = 'Asia' AND status = 'active'`，每個欄位單獨的 B+tree index 只能用一個，bitmap index 可以把三個結果做 AND 再回表。
3. **全文搜尋**：`WHERE content LIKE '%database%'`，B+tree 對非前綴 LIKE 完全沒用，需要 inverted index。

理解「查詢模式 → 索引結構」的對應是這章的核心。

## Hash Index

### 原理

把 key 雜湊到一個 slot，直接跳到那個 bucket，O(1) 平均查詢。

```
INSERT INTO idx(k=42, row_id=1000)

hash(42) % N_BUCKETS = 7

Bucket 7: [(42, 1000)]

查詢 WHERE k = 42：
  hash(42) % N_BUCKETS = 7
  掃 Bucket 7，找到 (42, 1000)
  → 回到 heap 讀第 1000 列
```

**只支援等值查詢**：hash 後的順序和原始值順序無關，範圍查詢 `k > 42` 需要掃全部 bucket，毫無優勢。

### 碰撞處理

兩種主流策略：

```
Chaining（鏈結法）：
  每個 bucket 是一個 linked list
  碰撞就把新元素掛在鏈尾
  優點：實作簡單，load factor 容忍度高
  缺點：cache 不友善（鏈表跳指標）

Open Addressing（開放定址）：
  碰撞就往下一個 slot 探查（linear/quadratic probing）
  優點：cache 友善（連續記憶體）
  缺點：load factor 過高（>0.7）效能急劇下降
```

資料庫的 hash index 通常用分頁鏈結（每個 bucket 對應一個 page，page 滿了加 overflow page），原因是要把 index 持久化到磁碟。

### Rust 實作：In-Memory Hash Index

```rust
// src/index/hash_index.rs
use std::collections::HashMap;

/// 簡單的 in-memory hash index，支援等值查詢
/// RID = row identifier（page_id, slot_id）
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Rid {
    pub page_id: u32,
    pub slot_id: u16,
}

pub struct HashIndex {
    /// 每個 key 可能對應多個 RID（重複值）
    buckets: HashMap<Vec<u8>, Vec<Rid>>,
}

impl HashIndex {
    pub fn new() -> Self {
        Self {
            buckets: HashMap::new(),
        }
    }

    /// 插入一筆索引記錄
    pub fn insert(&mut self, key: &[u8], rid: Rid) {
        self.buckets
            .entry(key.to_vec())
            .or_default()
            .push(rid);
    }

    /// 等值查詢，回傳所有匹配的 RID
    pub fn lookup(&self, key: &[u8]) -> &[Rid] {
        self.buckets
            .get(key)
            .map(|v| v.as_slice())
            .unwrap_or(&[])
    }

    /// 刪除指定 key + rid 的索引記錄
    pub fn delete(&mut self, key: &[u8], rid: Rid) {
        if let Some(rids) = self.buckets.get_mut(key) {
            rids.retain(|&r| r != rid);
            if rids.is_empty() {
                self.buckets.remove(key);
            }
        }
    }

    pub fn len(&self) -> usize {
        self.buckets.values().map(|v| v.len()).sum()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_basic_operations() {
        let mut idx = HashIndex::new();
        let rid1 = Rid { page_id: 1, slot_id: 0 };
        let rid2 = Rid { page_id: 2, slot_id: 5 };

        idx.insert(b"alice", rid1);
        idx.insert(b"bob", rid2);
        idx.insert(b"alice", Rid { page_id: 3, slot_id: 1 }); // 重複 key

        assert_eq!(idx.lookup(b"alice").len(), 2);
        assert_eq!(idx.lookup(b"bob").len(), 1);
        assert_eq!(idx.lookup(b"charlie").len(), 0);

        idx.delete(b"alice", rid1);
        assert_eq!(idx.lookup(b"alice").len(), 1);
    }

    #[test]
    fn test_duplicate_keys() {
        let mut idx = HashIndex::new();
        for i in 0u32..100 {
            idx.insert(b"same_key", Rid { page_id: i, slot_id: 0 });
        }
        assert_eq!(idx.lookup(b"same_key").len(), 100);
    }
}
```

### Linear Hashing（可擴展 Hash）

靜態 hash index 的桶數固定，資料量增長時整個 index 要 rehash，代價高。Linear Hashing 是一個動態方案：只分裂一個桶而不是全部 rehash，讓擴展平滑。這是 PostgreSQL hash index 的底層策略之一。

```
初始 4 個桶（split pointer = 0）

查詢 hash(k) = h：
  level = floor(log2(n_buckets_initial + splits))
  b = h mod 2^level
  if b < split_pointer:
    b = h mod 2^(level+1)   ← 用下一層 hash
  → 定址到桶 b
```

## Bitmap Index

### 原理

對欄位的每個 distinct 值，建立一個 **bit vector**（bitmap），每一個 bit 對應一列，bit=1 表示該列此欄位等於這個值。

```
orders 表（10 rows）：
row: 0 1 2 3 4 5 6 7 8 9

status 欄 bitmap index：
  'pending':   1 0 0 1 0 1 0 0 1 0
  'shipped':   0 1 0 0 1 0 0 1 0 1
  'delivered': 0 0 1 0 0 0 1 0 0 0
  'cancelled': 0 0 0 0 0 0 0 0 0 0
```

多條件查詢 = bitmap 做位元運算：

```
WHERE status = 'shipped' AND region = 'Asia'

status='shipped'  bitmap: 0 1 0 0 1 0 0 1 0 1
region='Asia'     bitmap: 1 1 0 0 1 0 1 1 0 0
                   AND:   0 1 0 0 1 0 0 1 0 0
                   → rows 1, 4, 7 匹配
```

位元 AND/OR 操作可以用 SIMD 加速，一次處理 64 或 256 個 row 的判斷，效率極高。

### 適用場景

| 欄位特性             | 是否適合 bitmap | 原因                                |
|----------------------|-----------------|-------------------------------------|
| 低基數（< 1000 distinct）| 適合        | bitmap 數量少，每個 bitmap 夠稠密   |
| 高基數（百萬 distinct） | 不適合       | bitmap 數量多，每個極稀疏，浪費空間 |
| 唯讀或讀多寫少       | 適合            | 維護 bitmap 代價高（update 要改位元）|
| OLAP 分析查詢        | 適合            | 多欄位組合過濾正是 bitmap 強項      |
| OLTP 高頻更新        | 不適合          | 每次 UPDATE 要更新多個 bitmap       |

Oracle 的 bitmap index 廣泛用於資料倉庫，PostgreSQL 沒有原生 bitmap index 但查詢執行層有 BitmapScan（動態建 bitmap，不持久化），ClickHouse 的向量化引擎也重度依賴 bitmap 過濾。

### Rust 實作：Bitmap Index

```rust
// src/index/bitmap_index.rs
use std::collections::HashMap;

/// Bitmap：用 Vec<u64> 儲存 bit vector，支援 64 個 row 一批操作
#[derive(Debug, Clone)]
pub struct Bitmap {
    bits: Vec<u64>,
    n_rows: usize,
}

impl Bitmap {
    pub fn new(n_rows: usize) -> Self {
        let words = (n_rows + 63) / 64;
        Self {
            bits: vec![0u64; words],
            n_rows,
        }
    }

    pub fn set(&mut self, row: usize) {
        assert!(row < self.n_rows);
        self.bits[row / 64] |= 1u64 << (row % 64);
    }

    pub fn clear(&mut self, row: usize) {
        assert!(row < self.n_rows);
        self.bits[row / 64] &= !(1u64 << (row % 64));
    }

    pub fn get(&self, row: usize) -> bool {
        row < self.n_rows && (self.bits[row / 64] >> (row % 64)) & 1 == 1
    }

    /// bitwise AND（多條件 AND 過濾）
    pub fn and(&self, other: &Bitmap) -> Bitmap {
        assert_eq!(self.bits.len(), other.bits.len());
        Bitmap {
            bits: self.bits.iter().zip(other.bits.iter()).map(|(a, b)| a & b).collect(),
            n_rows: self.n_rows,
        }
    }

    /// bitwise OR（多條件 OR 過濾）
    pub fn or(&self, other: &Bitmap) -> Bitmap {
        assert_eq!(self.bits.len(), other.bits.len());
        Bitmap {
            bits: self.bits.iter().zip(other.bits.iter()).map(|(a, b)| a | b).collect(),
            n_rows: self.n_rows,
        }
    }

    /// 迭代所有 bit=1 的 row index
    pub fn iter_set(&self) -> impl Iterator<Item = usize> + '_ {
        self.bits.iter().enumerate().flat_map(|(word_idx, &word)| {
            (0..64u32)
                .filter(move |&bit| (word >> bit) & 1 == 1)
                .map(move |bit| word_idx * 64 + bit as usize)
        }).filter(|&row| row < self.n_rows)
    }

    pub fn popcount(&self) -> usize {
        self.bits.iter().map(|w| w.count_ones() as usize).sum()
    }
}

/// Bitmap Index：為每個 distinct value 維護一個 Bitmap
pub struct BitmapIndex {
    n_rows: usize,
    bitmaps: HashMap<Vec<u8>, Bitmap>,
}

impl BitmapIndex {
    pub fn new(n_rows: usize) -> Self {
        Self {
            n_rows,
            bitmaps: HashMap::new(),
        }
    }

    pub fn set(&mut self, value: &[u8], row: usize) {
        self.bitmaps
            .entry(value.to_vec())
            .or_insert_with(|| Bitmap::new(self.n_rows))
            .set(row);
    }

    /// 等值查詢：回傳 bitmap（表示哪些 row 匹配）
    pub fn lookup_eq(&self, value: &[u8]) -> Option<&Bitmap> {
        self.bitmaps.get(value)
    }

    /// IN 查詢：OR 多個 bitmap
    pub fn lookup_in(&self, values: &[&[u8]]) -> Bitmap {
        let mut result = Bitmap::new(self.n_rows);
        for &val in values {
            if let Some(bm) = self.bitmaps.get(val) {
                result = result.or(bm);
            }
        }
        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_bitmap_and_filter() {
        let n = 10;
        let mut status_idx = BitmapIndex::new(n);
        let mut region_idx = BitmapIndex::new(n);

        // rows 1, 4, 7, 9 are 'shipped'
        for &r in &[1usize, 4, 7, 9] {
            status_idx.set(b"shipped", r);
        }
        // rows 1, 4, 7 are 'Asia'
        for &r in &[1usize, 4, 7] {
            region_idx.set(b"Asia", r);
        }

        let shipped = status_idx.lookup_eq(b"shipped").unwrap();
        let asia = region_idx.lookup_eq(b"Asia").unwrap();
        let result = shipped.and(asia);

        let matched: Vec<usize> = result.iter_set().collect();
        assert_eq!(matched, vec![1, 4, 7]);
        assert_eq!(result.popcount(), 3);
    }

    #[test]
    fn test_bitmap_or_filter() {
        let n = 10;
        let mut status_idx = BitmapIndex::new(n);
        status_idx.set(b"shipped", 1);
        status_idx.set(b"shipped", 4);
        status_idx.set(b"pending", 0);
        status_idx.set(b"pending", 3);

        let result = status_idx.lookup_in(&[b"shipped".as_ref(), b"pending".as_ref()]);
        let matched: Vec<usize> = result.iter_set().collect();
        assert_eq!(matched, vec![0, 1, 3, 4]);
    }
}
```

## Inverted Index（全文搜尋概觀）

Inverted index 是全文搜尋的核心資料結構。和一般索引「key → row」相反，inverted index 是「term（詞）→ posting list（哪些 document 含有這個詞，以及位置）」。

```
文件集：
  doc1: "database index performance"
  doc2: "index structure design"
  doc3: "database performance tuning"

Inverted index：
  "database"   → [(doc1, pos:0), (doc3, pos:0)]
  "index"      → [(doc1, pos:1), (doc2, pos:0)]
  "performance"→ [(doc1, pos:2), (doc3, pos:1)]
  "structure"  → [(doc2, pos:1)]
  "design"     → [(doc2, pos:2)]
  "tuning"     → [(doc3, pos:2)]

查詢 "database AND performance"：
  "database"    → {doc1, doc3}
  "performance" → {doc1, doc3}
  intersection  → {doc1, doc3}
```

**PostgreSQL 的 GIN（Generalized Inverted Index）** 就是 inverted index，用於 `tsvector` 全文搜尋、`jsonb @>` 包含查詢、`array @>` 陣列包含查詢。Elasticsearch 底層也是 Lucene 的 inverted index。

### Posting List 壓縮

實際的 posting list 可能有數百萬筆，壓縮是關鍵：

- **Delta encoding**：存 doc_id 的差值而非絕對值（差值更小，VarInt 編碼省空間）
- **FOR（Frame Of Reference）**：一個 block 內的最大最小值，用相對值 bit-packing
- **PFOR（Patched Frame of Reference）**：大多數值用較少 bit，少數 outlier 用 patch 補全

這些正是 Ch 36 欄式儲存 encoding 的親戚，技術同源。

## LSM 上的 Secondary Index 挑戰

LSM-tree（Ch 11–16）對 secondary index 有額外的複雜性，值得單獨討論。

### 主要挑戰

B+tree 環境的 secondary index 更新是「找到葉節點，改一個位置」。LSM 環境則不同：

```
LSM secondary index 的寫路徑：

INSERT INTO orders(id=100, customer_id=42, amount=999)

1. 主 LSM（按 id 排序）：寫 (id=100) 到 memtable
2. Secondary index（按 customer_id 排序）：
   寫 (customer_id=42, id=100) 到另一個 LSM memtable

UPDATE orders SET customer_id=55 WHERE id=100

3. 主 LSM：寫 (id=100, customer_id=55) tombstone + 新 kv
4. Secondary index：
   - 刪舊: (customer_id=42, id=100) tombstone
   - 加新: (customer_id=55, id=100)
   問題：這兩步不是原子的！Crash 中間會有一段 inconsistency。
```

### 解法

**全域一致性**：RocksDB 用 WriteBatch，把主 LSM 和所有 secondary index 的更新打包成一個原子 batch。Crash 後要麼全部有、要麼全部沒有。

**MyRocks（MySQL 的 RocksDB 後端）** 對 secondary index 的做法：
- 所有 secondary index 都是 LSM tree
- Index entry 用 `(index_key, pk)` 作為 LSM key
- 查詢時先查 secondary index 得到 pk，再查主 LSM 取完整 row（類似 B+tree 的 secondary index）

**TiKV（TiDB 的儲存層）** 走得更遠：用 MVCC 版本號統一主 key 和 secondary index 的一致性視圖。

## 部分索引與函數索引

### 部分索引（Partial Index）

只對滿足特定條件的 row 建索引，大幅縮小 index 大小：

```sql
-- 只對未完成訂單建索引（完成的訂單不需要快速查詢）
CREATE INDEX idx_orders_pending
  ON orders(created_at)
  WHERE status = 'pending';

-- 只有查詢含 WHERE status = 'pending' 時才會用到這個 index
SELECT * FROM orders WHERE status = 'pending' AND created_at > '2024-01-01';
```

設計上：index 和 predicate 一起儲存在 catalog，optimizer plan 時只在查詢條件 implies index predicate 時選用。

### 函數索引（Expression Index）

對表達式的結果建索引，而非原始欄位值：

```sql
-- 對 lower(email) 建索引，支援大小寫不敏感的 email 查詢
CREATE INDEX idx_users_email_lower ON users(lower(email));

-- 下面這個查詢就能用 index
SELECT * FROM users WHERE lower(email) = 'alice@example.com';
```

實作上：INSERT/UPDATE 時評估表達式並把結果當作 index key 儲存。查詢時，optimizer 看到 predicate 的 expression 和 index expression 完全匹配，就選用這個 index。

## 各索引結構對比

| 索引類型     | 等值查詢   | 範圍查詢   | 排序      | 適用基數  | 更新代價  | 典型場景                    |
|-------------|-----------|-----------|----------|---------|---------|---------------------------|
| B+tree      | O(log n)  | O(log n)  | 天然有序  | 全基數   | 中       | 通用，OLTP 主力             |
| Hash Index  | O(1)      | 不支援    | 無序      | 高基數   | 低       | 等值查詢密集，不需範圍       |
| Bitmap      | O(1)位元  | 不直接支援| 無序      | 低基數   | 高       | OLAP 多欄位 AND/OR 過濾    |
| Inverted    | O(log n)  | 不適合    | 相關度    | 詞典     | 高       | 全文搜尋、JSON 包含查詢     |
| 部分 Index  | 同基底    | 同基底    | 同基底    | 子集     | 低於全量 | 過濾後資料量小的欄位        |
| 函數 Index  | O(log n)  | O(log n)  | 有序      | 視表達式 | 中       | 轉換後查詢（lower/date 等） |

## 踩雷

1. **Hash index 不支援 `ORDER BY`**：拿 hash index 去回答 `ORDER BY customer_id` 要全掃，不如 seqscan。PostgreSQL 的 hash index 不能用於排序，optimizer 知道這點，不會誤用，但手動 force index hint 可能踩到。

2. **Bitmap index 在高更新頻率下變殭屍**：每次 UPDATE 一列，可能要修改多個 bitmap 的對應位元，write amplification 比 B+tree 嚴重。OLTP 場景不要用原生 bitmap index。

3. **LSM secondary index 的一致性窗口**：crash 在 secondary index tombstone 和新 entry 之間，重啟後可能查到舊 index 指向不存在或已更新的 row。需要用 WriteBatch + WAL 保護。

4. **部分索引的 predicate 必須精確匹配**：optimizer 只在查詢的 WHERE clause implies index predicate 時才選用。`WHERE status IN ('pending', 'processing')` 的查詢，不會用 `WHERE status = 'pending'` 的部分索引。

5. **函數索引的表達式必須是 deterministic**：`NOW()`、`random()` 這類 non-deterministic 函數不能建函數索引，因為每次呼叫結果不同，index 存的值和查詢計算的值不一致。

## 進階延伸

**GiST（Generalized Search Tree）**：PostgreSQL 的通用索引框架，可以插入自訂的資料類型和比較函數。R-tree（地理空間索引）是 GiST 的一個實例，`geometry &&` 的範圍查詢靠它。

**Covering Index**：B+tree 的特殊用法，把查詢所需的所有欄位都放進 index，避免回表（Index-Only Scan）。`CREATE INDEX ON orders(customer_id) INCLUDE (amount, status)` 讓 `SELECT amount, status WHERE customer_id = 42` 完全不碰 heap。

## 本章重點整理

- Hash index 等值查詢 O(1) 但不支援範圍和排序，適合高基數欄位的等值密集查詢。
- Bitmap index 對低基數欄位的多條件 AND/OR 組合查詢極快，本質是位元運算，但維護代價高，只適合讀多寫少的 OLAP 場景。
- Inverted index 是全文搜尋的核心，PostgreSQL 的 GIN 就是 inverted index 的泛化。
- LSM 上的 secondary index 有原子性挑戰，必須用 WriteBatch 保護。
- 部分索引和函數索引是 B+tree 的空間/彈性優化，不是新結構而是 B+tree 的加料用法。

## 自我檢核

- [ ] 能說出 hash index 為什麼不支援範圍查詢？
- [ ] 如果有個欄位只有 5 個 distinct 值，且查詢都是多欄位組合過濾，該選哪種索引，為什麼？
- [ ] LSM secondary index 在 crash 中間會出什麼問題？RocksDB 如何解決？
- [ ] 部分索引和函數索引各在什麼情境下最值得建？
- [ ] 我們的 `BitmapIndex::lookup_in` 回傳的是哪些 row 的 bitmap？

## 延伸閱讀

1. **CMU 15-445 Lecture "Indexes" (2023)**  
   涵蓋 hash table 設計（linear hashing / extendible hashing）與 B+tree 以外索引的完整課堂版本，Andy Pavlo 對 hash index 的分析特別清楚。

2. **《Database Internals》第 3 章（File Formats）及第 6–7 章**  
   Alex Petrov 對 LSM secondary index 的一致性挑戰有深入討論，是本章 LSM 段落的理論基礎。

3. **Generalized Search Trees for Database Systems** — Hellerstein et al. (VLDB 1995)  
   GiST 的原始論文，理解 PostgreSQL 空間索引和通用索引框架的設計思路。

4. **An Introduction to Information Retrieval** — Manning, Raghavan, Schütze (Cambridge, 2008)  
   Ch 1 講 inverted index 結構，Ch 5 講 posting list 壓縮（FOR/PFOR）；免費線上全文。

5. **MyRocks: LSM-Tree Database Storage Engine Serving Facebook's Social Graph** — Matsunobu et al. (VLDB 2020)  
   Facebook 把 RocksDB 作為 MySQL 後端的工程報告，secondary index 在 LSM 上的工業級解法在這裡。

---

銜接：索引解決的是「找到哪些 row」，但當查詢只需要幾個欄位的聚合，逐列掃描仍然太慢——這是欄式儲存登場的時候。

→ [下一章：Ch 36 欄式儲存與向量化](./36-columnar-storage.md)
