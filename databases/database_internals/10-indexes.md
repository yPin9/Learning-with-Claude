# Ch 10 — 索引：secondary / covering / range scan

> **目標**：弄清楚 clustered vs non-clustered 索引的本質差異、secondary index 為什麼要指向 primary key 而不是直接存 tuple 位置、covering index 如何做到 index-only scan、composite index 的欄位順序為什麼不能隨便放。最後用 Rust 實作 leaf linked-list 上的 range scan iterator。

## 為什麼需要多個索引？

B+tree 本身是一個有序結構——按照 key 排好序的。問題是一張表只有「一種順序」，但查詢的 WHERE 條件五花八門：

```sql
SELECT * FROM orders WHERE customer_id = 42;     -- 沒有按 customer_id 排序
SELECT * FROM orders WHERE amount > 1000;         -- 也沒有
SELECT * FROM orders WHERE created_at > '2024-01'; -- 也沒有
```

沒有索引就是全表掃描（full table scan，FTS）：讀所有頁面、比對每一行。資料量大時是災難。

索引的代價：每次 write 要多維護幾棵 B+tree，占更多磁碟。用空間換時間。

## Clustered vs Non-Clustered Index

### Clustered Index（聚簇索引）

**資料本身就按這個 key 排列存放**。在 B+tree 儲存引擎裡，leaf 節點存的就是完整的行資料（或者說，leaf IS the table）。

```
primary key = user_id
leaf[1]: (user_id=1, name="Alice", email="alice@example.com", age=28)
leaf[2]: (user_id=2, name="Bob",   email="bob@example.com",   age=35)
leaf[3]: (user_id=3, name="Carol", ...)
```

InnoDB 預設用 PRIMARY KEY 作為 clustered index。如果你沒指定 PRIMARY KEY，InnoDB 會用第一個 UNIQUE NOT NULL 欄位，再不然自己生一個隱藏 6 bytes row ID。

**每張表只能有一個 clustered index**——因為物理排列只有一種。

### Non-Clustered Index（Secondary Index，二級索引）

按另一個欄位建立的索引，leaf 不存整行資料，而是存：

```
secondary key → primary key（或 row locator）
```

```
email 的 secondary index：
leaf: ("alice@example.com" → user_id=1)
leaf: ("bob@example.com"   → user_id=2)
```

查 `WHERE email = 'alice@example.com'` 的流程：

```
1. 在 email secondary index 找到 user_id=1
2. 用 user_id=1 去 primary key clustered index 找完整行資料  ← 這叫「回表」
```

### 為什麼 Secondary Index 指向 Primary Key 而不是物理位置？

直覺上「指向物理位置（page id + slot id）」好像更快，少一次查找。但：

- 每次 update 讓行移動（例如 variable-length 欄位變長，要搬到另一個 page），所有指向它的 secondary index 都要更新——代價 O(N\_indexes)
- Clustered index 只存 primary key，primary key 不變，所有 secondary index 都不用改

PostgreSQL 的 heap table 沒有 clustered index 概念，secondary index 指向 heap tuple 的物理位置（`ctid`）。UPDATE 在某些情況下會讓 secondary index 失效，需要 VACUUM 清理。InnoDB 的設計在 update-heavy workload 下明顯更省力。

## Covering Index（覆蓋索引）

如果查詢用到的所有欄位都在索引裡，就不需要回表。這叫 **index-only scan**（PostgreSQL）或 **covering index scan**（MySQL）。

```sql
-- 表結構：orders(order_id PK, customer_id, amount, created_at, ...)
-- 索引：INDEX idx ON orders(customer_id, amount)

-- 這個查詢只需要 customer_id 和 amount，都在索引裡 → covering index
SELECT customer_id, SUM(amount)
FROM orders
WHERE customer_id = 42
GROUP BY customer_id;

-- 但這個需要 created_at，不在索引裡 → 需要回表
SELECT customer_id, amount, created_at
FROM orders
WHERE customer_id = 42;
```

**Covering index 的威力**：只讀 index 頁，完全不碰 data 頁。對讀多寫少的 reporting 查詢效果驚人。代價是 index 變胖（多存幾個欄位），write 也慢一點。

## Composite Index（複合索引）

```sql
INDEX idx ON orders(customer_id, created_at)
```

Key 是 `(customer_id, created_at)` 的 tuple，B+tree 先按 customer\_id 排序，同 customer\_id 的再按 created\_at 排序。

### 欄位順序的規則：最左前綴原則

```sql
-- ✓ 能用索引（customer_id 是最左欄位）
WHERE customer_id = 42
WHERE customer_id = 42 AND created_at > '2024-01'
WHERE customer_id BETWEEN 10 AND 20

-- ✗ 不能用索引（跳過了 customer_id）
WHERE created_at > '2024-01'
```

這不是任意規定——B+tree 的 key 是 `(cid, date)` 的 tuple，只按 `date` 過濾等於在一個按 `(cid, date)` 排列的索引裡找所有 `date > 某值`，這些資料分散在整棵樹各處，完全沒有 locality。

### 何時分拆成多個 index？

如果你同時需要：

- `WHERE customer_id = ?`（高頻）
- `WHERE created_at > ?`（偶爾）

可以考慮 `INDEX ON (customer_id)` 和 `INDEX ON (created_at)` 分開，讓 planner 選。composite index 對「兩個條件都有的查詢」最有利，但要為每個查詢模式分析。

## Range Scan Iterator（利用 Leaf Linked List）

B+tree 的 leaf 節點用 next\_leaf 指標串成連結串列，這讓 range scan 極為高效：

```
找到 lower_bound 的 leaf → 沿著 next_leaf 往右走 → 遇到 upper_bound 就停
```

不需要再走 internal 節點，純粹的 sequential read。

```rust
// WSL 驗證通過：wsl cargo test — 6 tests ok（包含亂序插入後 range scan 有序）

const ORDER: usize = 4;
const MIN_KEYS: usize = ORDER / 2;

#[derive(Debug, Clone)]
struct Node {
    is_leaf: bool,
    keys: Vec<i64>,
    children: Vec<usize>,   // internal: child ids
    values: Vec<String>,    // leaf: payload
    next_leaf: Option<usize>,
}

struct BPlusTree {
    nodes: Vec<Node>,
    root: usize,
}

impl BPlusTree {
    fn new() -> Self {
        let root = Node {
            is_leaf: true, keys: vec![], children: vec![],
            values: vec![], next_leaf: None,
        };
        BPlusTree { nodes: vec![root], root: 0 }
    }

    /// 找到 key 應該在的 leaf（不管 key 存不存在）
    fn find_leaf_for(&self, mut id: usize, key: i64) -> usize {
        loop {
            let node = &self.nodes[id];
            if node.is_leaf { return id; }
            let pos = node.keys.partition_point(|&k| k <= key);
            // partition_point 回傳第一個 > key 的位置，即要走的 child
            let pos = if pos < node.children.len() { pos } else { node.children.len() - 1 };
            id = node.children[pos];
        }
    }

    /// 插入（用於建樹，含 split，完整版見 Ch7）
    fn insert(&mut self, key: i64, value: String) {
        // 找到 leaf
        let mut path: Vec<usize> = vec![];
        let mut id = self.root;
        loop {
            path.push(id);
            let node = &self.nodes[id];
            if node.is_leaf { break; }
            let pos = node.keys.partition_point(|&k| k <= key);
            let pos = pos.min(node.children.len() - 1);
            id = node.children[pos];
        }
        let leaf_id = *path.last().unwrap();
        // 插入 key
        {
            let leaf = &mut self.nodes[leaf_id];
            let pos = leaf.keys.partition_point(|&k| k < key);
            if pos < leaf.keys.len() && leaf.keys[pos] == key {
                leaf.values[pos] = value; // update
                return;
            }
            leaf.keys.insert(pos, key);
            leaf.values.insert(pos, value);
        }
        // Split if overflow
        if self.nodes[leaf_id].keys.len() > ORDER {
            self.split_and_fix(path);
        }
    }

    fn split_and_fix(&mut self, path: Vec<usize>) {
        let mut child_id = *path.last().unwrap();
        let mut depth = path.len() - 1;

        loop {
            if self.nodes[child_id].keys.len() <= ORDER { break; }

            let mid = (self.nodes[child_id].keys.len()) / 2;
            let is_leaf = self.nodes[child_id].is_leaf;

            // 右半
            let right_keys: Vec<i64> = self.nodes[child_id].keys[mid..].to_vec();
            let right_values: Vec<String> = if is_leaf {
                self.nodes[child_id].values[mid..].to_vec()
            } else { vec![] };
            let right_children: Vec<usize> = if !is_leaf {
                self.nodes[child_id].children[mid+1..].to_vec()
            } else { vec![] };
            let right_next = self.nodes[child_id].next_leaf;

            let promote_key = if is_leaf { right_keys[0] } else { right_keys[0] };

            // 截斷左半
            if is_leaf {
                self.nodes[child_id].keys.truncate(mid);
                self.nodes[child_id].values.truncate(mid);
            } else {
                self.nodes[child_id].keys.truncate(mid);
                self.nodes[child_id].children.truncate(mid + 1);
            }

            // 建右節點
            let right_id = self.nodes.len();
            self.nodes.push(Node {
                is_leaf,
                keys: if is_leaf { right_keys } else { right_keys[1..].to_vec() },
                values: right_values,
                children: right_children,
                next_leaf: right_next,
            });

            // 維護 leaf linked list
            if is_leaf {
                self.nodes[child_id].next_leaf = Some(right_id);
            }

            // 把 promote_key 推到 parent
            if depth == 0 {
                // 建新 root
                let new_root_id = self.nodes.len();
                self.nodes.push(Node {
                    is_leaf: false,
                    keys: vec![promote_key],
                    children: vec![child_id, right_id],
                    values: vec![],
                    next_leaf: None,
                });
                self.root = new_root_id;
                break;
            }

            depth -= 1;
            let parent_id = path[depth];
            let pos = self.nodes[parent_id].keys.partition_point(|&k| k < promote_key);
            self.nodes[parent_id].keys.insert(pos, promote_key);
            self.nodes[parent_id].children.insert(pos + 1, right_id);

            child_id = parent_id;
        }
    }

    /// Range scan：回傳 [lo, hi] 之間所有 (key, value)
    pub fn range_scan(&self, lo: i64, hi: i64) -> RangeScanIter<'_> {
        // 找到 lo 所在的 leaf，以及在 leaf 裡的起始位置
        let leaf_id = self.find_leaf_for(self.root, lo);
        let start_pos = self.nodes[leaf_id].keys.partition_point(|&k| k < lo);

        RangeScanIter {
            tree: self,
            current_leaf: leaf_id,
            pos: start_pos,
            hi,
        }
    }
}

/// Range scan iterator：利用 leaf linked list 往右掃
struct RangeScanIter<'a> {
    tree: &'a BPlusTree,
    current_leaf: usize,
    pos: usize,
    hi: i64,
}

impl<'a> Iterator for RangeScanIter<'a> {
    type Item = (i64, &'a str);

    fn next(&mut self) -> Option<Self::Item> {
        loop {
            let node = &self.tree.nodes[self.current_leaf];

            // 還有剩餘的 key 在這個 leaf？
            if self.pos < node.keys.len() {
                let k = node.keys[self.pos];
                if k > self.hi {
                    return None; // 超出範圍
                }
                let v = node.values[self.pos].as_str();
                self.pos += 1;
                return Some((k, v));
            }

            // 這個 leaf 走完了，跳到下一個
            match node.next_leaf {
                Some(next_id) => {
                    self.current_leaf = next_id;
                    self.pos = 0;
                }
                None => return None, // 最後一個 leaf
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn build_tree(n: i64) -> BPlusTree {
        let mut t = BPlusTree::new();
        for i in 1..=n {
            t.insert(i * 10, format!("val_{}", i * 10));
        }
        t
    }

    #[test]
    fn test_range_scan_basic() {
        let t = build_tree(10); // keys: 10,20,...,100
        let result: Vec<(i64, &str)> = t.range_scan(20, 50).collect();
        let keys: Vec<i64> = result.iter().map(|(k, _)| *k).collect();
        assert_eq!(keys, vec![20, 30, 40, 50]);
    }

    #[test]
    fn test_range_scan_full() {
        let t = build_tree(10);
        let result: Vec<(i64, &str)> = t.range_scan(1, 200).collect();
        assert_eq!(result.len(), 10);
        assert_eq!(result[0].0, 10);
        assert_eq!(result[9].0, 100);
    }

    #[test]
    fn test_range_scan_empty() {
        let t = build_tree(5); // keys: 10..50
        let result: Vec<(i64, &str)> = t.range_scan(60, 100).collect();
        assert!(result.is_empty());
    }

    #[test]
    fn test_range_scan_single() {
        let t = build_tree(5);
        let result: Vec<(i64, &str)> = t.range_scan(30, 30).collect();
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].0, 30);
    }

    #[test]
    fn test_range_scan_values_correct() {
        let t = build_tree(5);
        let result: Vec<(i64, &str)> = t.range_scan(10, 30).collect();
        assert_eq!(result[0], (10, "val_10"));
        assert_eq!(result[1], (20, "val_20"));
        assert_eq!(result[2], (30, "val_30"));
    }

    #[test]
    fn test_range_scan_ordered_after_random_insert() {
        // 亂序插入，range scan 出來仍然有序
        let mut t = BPlusTree::new();
        for &k in &[50i64, 10, 90, 30, 70, 20, 80, 40, 60] {
            t.insert(k, k.to_string());
        }
        let result: Vec<i64> = t.range_scan(20, 70).map(|(k, _)| k).collect();
        assert_eq!(result, vec![20, 30, 40, 50, 60, 70]);
    }
}
```

**WSL 驗證**：

```bash
# 建立 Cargo 專案並把上面程式碼放進 src/lib.rs 後：
wsl cargo test -- --nocapture
```

測試通過後的輸出：

```
running 6 tests
test tests::test_range_scan_basic ... ok
test tests::test_range_scan_empty ... ok
test tests::test_range_scan_full ... ok
test tests::test_range_scan_ordered_after_random_insert ... ok
test tests::test_range_scan_single ... ok
test tests::test_range_scan_values_correct ... ok

test result: ok. 6 passed; 0 failed
```

## 索引設計原則整理

| 場景 | 建議 |
|---|---|
| 高選擇性欄位（email、uuid） | 單欄 secondary index |
| 多欄等值過濾 | Composite index，高選擇性欄放前面 |
| Range 查詢 | 等值欄在前，range 欄在後 |
| 查詢只要幾個欄位 | Covering index（把 SELECT 欄位加進索引） |
| 更新極頻繁的欄位 | 謹慎加索引，write 代價大 |
| 低基數欄位（is\_deleted, status） | 通常不值得單獨建 B+tree index（bitmap index 更適合） |

## 踩雷

1. **`partition_point` 的語義**：Rust 的 `partition_point(|k| k <= key)` 回傳「第一個 `> key` 的位置」，在 internal 節點導航時這正好是「要走的 child index」。但如果你用 `k < key`，結果差一個，會走到錯誤的 child。這個 off-by-one 錯誤在 B+tree 裡非常難 debug。

2. **Leaf linked list 在 split 時必須更新**：split 出新的 right leaf 時，必須把舊 leaf 的 `next_leaf` 指向 right，right 的 `next_leaf` 指向原來的 next。忘記這步，range scan 會跳過一整個 leaf。

3. **Secondary index 的 range scan 必然要回表**：如果 covering index 沒有包含所有需要的欄位，range scan 在 secondary index leaf 拿到每個 primary key 之後，都要去 primary index 查一次。I/O 模式從 sequential 變成 random，效能可能差 100x。這叫 **index dive**，查詢優化器要估計代價決定是否值得用 index。

4. **Composite index 的欄位順序改了就是不同索引**：`INDEX(a, b)` 和 `INDEX(b, a)` 在物理上是兩棵完全不同的 B+tree，不能互相替代。`WHERE a = ? AND b = ?` 兩者都能用；`WHERE a = ?` 只有前者能用；`WHERE b = ?` 只有後者能用。

5. **Range scan 的 `hi` 邊界比較要注意是否包含端點**：這個實作用 `k > hi` 停止，所以是閉區間 `[lo, hi]`。改成 `k >= hi` 就是半開區間 `[lo, hi)`。SQL 的 `BETWEEN` 是閉區間，`<` 是開區間，要根據語義選對。

## 進階延伸

- **Index Skip Scan**：MySQL 8.0 和 PostgreSQL 15+ 支援，對 `(a, b)` composite index，就算查詢只指定 `b`，優化器可以枚舉所有 `a` 的值再做 skip scan——免去建 `INDEX(b)` 的需求。
- **Partial Index**：只對滿足條件的行建索引，例如 `INDEX ON orders(customer_id) WHERE status = 'active'`。適合高偏斜資料（90% 是 inactive，只查 active）。
- **Function-based Index**：對 `lower(email)` 建索引，讓 `WHERE lower(email) = ?` 能用索引。需要讓 B+tree key 是函式輸出而非原始欄位值。

## 本章重點整理

- Clustered index：資料本身按 key 排列，每張表只能有一個（InnoDB = PRIMARY KEY）。
- Secondary index：leaf 存 `secondary_key → primary_key`，查詢需要回表（index dive）。
- Secondary index 指向 primary key（不是物理位置）是為了讓 update/move 不用更新所有 secondary index。
- Covering index：查詢所有欄位都在索引裡，省去回表，變成 index-only scan。
- Composite index 遵守最左前綴，欄位順序影響哪些查詢能用這個索引。
- Range scan 利用 leaf linked list 做 sequential read，比隨機 I/O 快得多。

## 自我檢核

- [ ] 我能解釋為什麼每張表只能有一個 clustered index
- [ ] 我能說出 secondary index 指向 primary key 而不是物理地址的理由
- [ ] 我知道 covering index 如何省去回表，以及它的代價
- [ ] 我能判斷 `INDEX(a, b)` 對哪些 WHERE 條件有效、哪些沒有
- [ ] 我能說出 range scan 為什麼需要 leaf linked list，以及迭代器如何跨越 leaf 邊界

## 延伸閱讀

1. **《Use The Index, Luke》**（use-the-index-luke.com）— 免費網站，從執行計畫的視角解釋 B-tree index 行為，有 MySQL/PostgreSQL/Oracle 的對照，是索引設計最好的實用指南。
2. **《Database Internals》Ch 6（Exploring Storage Internals）** — 涵蓋 secondary index 設計與 covering scan 的細節。
3. **CMU 15-445 Lecture 8（Trees Indexes II）** — 討論 composite index、index scan、covering index 的 optimizer 視角。
4. **InnoDB Technical Reference：Clustered and Secondary Indexes** — MySQL 官方文件，解釋 InnoDB 的 clustered B+tree 和 secondary index 的實際儲存格式。
5. **PostgreSQL 文件：Index-Only Scans and Covering Indexes** — 解釋 PostgreSQL 如何用 visibility map 讓 index-only scan 安全（不需要回表做 MVCC 可見性檢查）。

---

→ [練習 A：可持久化 B+tree KV store](./practice-a-btree-kv-store.md)
