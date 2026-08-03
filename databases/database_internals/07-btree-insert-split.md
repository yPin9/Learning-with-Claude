# Ch 7 — B+tree 實作（一）：insert / split

> **目標**：從零手刻一個能跑的 B+tree：index-based（arena）風格的節點管理、正確的 insert 路徑、leaf split 與 internal split 的邊界情況、root split 讓樹長高。全程 WSL 編譯 + 50 key 測試驗證有序，range scan 驗證 leaf linked list 正確。

---

## 為什麼用 index-based（arena）風格？

在 Rust 裡實作樹結構有三條路：

| 風格 | 指標型態 | 優點 | 缺點 |
|------|---------|------|------|
| `Box<Node>` 遞迴 | owned pointer | 最直覺 | 無法輕易共享，split 後要大量 move |
| `Rc<RefCell<Node>>` | 參考計數 + 內部可變 | 可共享 | 執行時 borrow 檢查開銷、多執行緒不適用 |
| **index-based** | `Vec<Node>` + `usize` 索引 | 極接近真實 DB 的 page-id 定址；zero-cost；split 只是 Vec::push | borrow checker 限制（需要 copy 索引後再存取） |

資料庫用的正是 index-based：page 存在磁碟檔案的固定位置，page_id 就是偏移量除以 page_size 的整數。我們的 `Vec<Node>` 就是「記憶體版的磁碟」，`NodeId = usize` 就是 page_id。

這個設計讓 Rust 的 borrow checker 最好滿足，也讓你理解真實資料庫是怎麼思考的。

---

## 設計決策：`ORDER = t`（minimum degree）

- `ORDER = t = 3`（minimum degree）
- 每個節點持有 `[t-1, 2t-1] = [2, 5]` 個 key（根節點最少 1 個 key）
- 節點「滿」的定義：`keys.len() == 2t-1 == 5`（`MAX_KEYS = 5`）
- 「最少」：`keys.len() >= t-1 == 2`（`MIN_KEYS = 2`，root 例外）

我們選 t=3 是因為它夠小，split 會頻繁發生，邊界情況容易觀察。生產系統裡 t 通常幾百，但演算法完全相同。

---

## 完整實作

### 型別定義與常數

```rust
const ORDER: usize = 3;
const MAX_KEYS: usize = 2 * ORDER - 1; // 5
type NodeId = usize;
const NULL: NodeId = usize::MAX;

#[derive(Clone)]
struct Node {
    keys:     Vec<u64>,
    values:   Vec<u64>,    // leaf only: 與 keys 等長
    children: Vec<NodeId>, // internal only: 長度 = keys.len() + 1
    is_leaf:  bool,
    next:     NodeId,      // leaf linked list；internal node 設為 NULL
}

impl Node {
    fn new_leaf() -> Self {
        Node { keys: vec![], values: vec![], children: vec![], is_leaf: true, next: NULL }
    }
    fn new_internal() -> Self {
        Node { keys: vec![], values: vec![], children: vec![], is_leaf: false, next: NULL }
    }
    fn is_full(&self) -> bool { self.keys.len() == MAX_KEYS }
}
```

### Arena 與 BPlusTree

```rust
struct BPlusTree {
    nodes: Vec<Node>, // arena：所有節點存這裡，NodeId 是 Vec 索引
    root:  NodeId,
}

impl BPlusTree {
    fn new() -> Self {
        // 樹初始只有一個空 leaf（root = leaf）
        BPlusTree { nodes: vec![Node::new_leaf()], root: 0 }
    }

    /// 分配新節點，回傳它的 NodeId。
    fn alloc(&mut self, node: Node) -> NodeId {
        let id = self.nodes.len();
        self.nodes.push(node);
        id
    }
```

---

## Search

```rust
    /// Internal node 路由：separator 是右子樹的最小 key。
    /// 有多少個 separator <= key，就走第幾個 child（0-indexed）。
    /// 必須用 `sep <= key`，不是 `sep < key`——等號情況要往右走。
    fn child_pos(keys: &[u64], key: u64) -> usize {
        keys.partition_point(|&sep| sep <= key)
    }

    fn search(&self, key: u64) -> Option<u64> {
        let mut cur = self.root;
        loop {
            let node = &self.nodes[cur];
            if node.is_leaf {
                return match node.keys.binary_search(&key) {
                    Ok(i)  => Some(node.values[i]),
                    Err(_) => None,
                };
            } else {
                let pos = Self::child_pos(&node.keys, key);
                cur = node.children[pos];
            }
        }
    }
```

---

## Insert 主流程

```rust
    fn insert(&mut self, key: u64, value: u64) {
        // 如果 root 滿了，必須先分裂 root（B+tree 增高的唯一時機）
        if self.nodes[self.root].is_full() {
            let old_root = self.root;
            // 建一個新的 internal node 作為新 root，把舊 root 當第一個 child
            let mut new_root = Node::new_internal();
            new_root.children.push(old_root);
            let new_root_id = self.alloc(new_root);
            self.root = new_root_id;
            // 分裂舊 root（現在是新 root 的 child[0]）
            self.split_child(new_root_id, 0);
        }
        self.insert_non_full(self.root, key, value);
    }
```

`insert()` 只做一件事：確保 root 不滿，然後呼叫 `insert_non_full`。

---

## Split：最複雜的部分

Split 是 B+tree 所有 complexity 的來源。把它想清楚，後面 delete/merge 也會容易很多。

### Leaf Split（圖解）

```
分裂前（ORDER=3，MAX_KEYS=5，leaf 滿了）：
  LEAF: [1, 2, 3, 4, 5]   next → L2
           ↑
         mid = ORDER = 3
         左半：[1, 2, 3]（保留在原節點）
         右半：[4, 5]（移到新 leaf）
         往上推的 separator = 4（右半第一個 key）

分裂後：
  LEAF_LEFT:  [1, 2, 3]  next → LEAF_RIGHT
  LEAF_RIGHT: [4, 5]     next → L2
  
  父節點新增 separator=4，指向 LEAF_RIGHT
```

**重點**：leaf split 是「複製」（separator 4 同時出現在父節點 AND 右 leaf），不是「移除」。這與 internal split 不同。

### Internal Split（圖解）

```
分裂前（internal node 滿了，keys=[3,7,10,14,18]，6 個 child）：
                [3, 7, 10, 14, 18]
               /  |  |   |   |   \
             C0  C1 C2  C3  C4   C5
           ↑
         mid = ORDER - 1 = 2（index，對應 key=10）
         push_up = 10（上移到父節點，本節點移除）
         左半 keys=[3,7]，children=[C0,C1,C2]
         右半 keys=[14,18]，children=[C3,C4,C5]

分裂後：
  LEFT_INTERNAL:  keys=[3,7],    children=[C0,C1,C2]
  RIGHT_INTERNAL: keys=[14,18],  children=[C3,C4,C5]
  父節點新增 separator=10，指向 RIGHT_INTERNAL
```

**重點**：internal split 是「移除」（key=10 只出現在父節點，不留在任何子節點）。Internal node 的 separator 不複製，直接上移。

### 為什麼 mid 不一樣？

- Leaf split：`mid = ORDER = 3`（左節點保留 keys[0..mid]，右節點得 keys[mid..]）
  - 左 3 個，右 2 個（5 個分成 3+2）
  - separator = 右節點第一個 key（複製自 keys[mid]）
  
- Internal split：`mid = ORDER - 1 = 2`（對應第 3 個 key，index=2）
  - 這個 key「上移」，左得 keys[0..mid]，右得 keys[mid+1..]
  - 左 2 個，右 2 個（5 個分成 2+上移1+2）

如果把 internal split 也用 `mid = ORDER`，那 push_up key 還留在一個子節點裡，導致 internal node 的 separator 語意（一定是右子樹最小 key 的「上界」）不正確，搜尋路由就會出錯。

### 程式碼

```rust
    /// 分裂 `parent_id` 的第 `child_idx` 個 child。
    /// 前置條件：parent 不滿，child 已滿。
    fn split_child(&mut self, parent_id: NodeId, child_idx: usize) {
        let child_id = self.nodes[parent_id].children[child_idx];

        if self.nodes[child_id].is_leaf {
            // === Leaf split ===
            let mid = ORDER; // 右節點從 mid 開始

            // 複製右半到新 leaf
            let right_keys   = self.nodes[child_id].keys[mid..].to_vec();
            let right_values = self.nodes[child_id].values[mid..].to_vec();
            let next_leaf    = self.nodes[child_id].next; // 保留原本的 next 指針

            let mut right = Node::new_leaf();
            right.keys   = right_keys;
            right.values = right_values;
            let right_id = self.alloc(right);

            // 原 leaf 截斷為左半，更新 next 指針
            self.nodes[child_id].keys.truncate(mid);
            self.nodes[child_id].values.truncate(mid);
            self.nodes[child_id].next = right_id;
            self.nodes[right_id].next = next_leaf;

            // 往上複製 separator（右節點第一個 key）
            let push_up = self.nodes[right_id].keys[0];
            self.nodes[parent_id].keys.insert(child_idx, push_up);
            self.nodes[parent_id].children.insert(child_idx + 1, right_id);

        } else {
            // === Internal split ===
            let mid = ORDER - 1; // 要上移的 key 的 index

            // key[mid] 上移
            let push_up = self.nodes[child_id].keys[mid];

            // 右半：keys[mid+1..]，children[mid+1..]
            let right_keys     = self.nodes[child_id].keys[mid+1..].to_vec();
            let right_children = self.nodes[child_id].children[mid+1..].to_vec();

            let mut right = Node::new_internal();
            right.keys     = right_keys;
            right.children = right_children;
            let right_id = self.alloc(right);

            // 原節點截斷為左半（key[mid] 已移走）
            self.nodes[child_id].keys.truncate(mid);
            self.nodes[child_id].children.truncate(mid + 1);

            // 往上插入 push_up（移除，不複製）
            self.nodes[parent_id].keys.insert(child_idx, push_up);
            self.nodes[parent_id].children.insert(child_idx + 1, right_id);
        }
    }
```

---

## Insert Non-Full

```rust
    /// 在 node_id 為根的子樹中插入 key-value。
    /// 前置條件：node_id 對應的節點不滿（由呼叫方保證）。
    fn insert_non_full(&mut self, node_id: NodeId, key: u64, value: u64) {
        if self.nodes[node_id].is_leaf {
            // 葉節點：直接找有序插入位置
            let pos = self.nodes[node_id].keys.partition_point(|&k| k < key);
            self.nodes[node_id].keys.insert(pos, key);
            self.nodes[node_id].values.insert(pos, value);
        } else {
            // Internal node：找應該往哪個 child 走
            let pos = Self::child_pos(&self.nodes[node_id].keys, key);
            let child_id = self.nodes[node_id].children[pos];

            if self.nodes[child_id].is_full() {
                // Child 滿了：先分裂，再重新決定走哪邊
                self.split_child(node_id, pos);
                // Split 後，parent 多了一個 key，重新計算 pos
                let pos2 = Self::child_pos(&self.nodes[node_id].keys, key);
                let child2 = self.nodes[node_id].children[pos2];
                self.insert_non_full(child2, key, value);
            } else {
                self.insert_non_full(child_id, key, value);
            }
        }
    }
```

注意 split 後必須重新計算 `pos2` 而不是直接用 `pos + (if key >= sep { 1 } else { 0 })`——後者看起來能省一次計算，但容易出 off-by-one。重新呼叫 `child_pos` 最安全，性能影響可忽略（這只是幾個整數比較）。

---

## Range Scan

```rust
    fn range_scan(&self, lo: u64, hi: u64) -> Vec<(u64, u64)> {
        // 找到 lo 所在（或應在）的 leaf
        let mut cur = self.root;
        loop {
            let node = &self.nodes[cur];
            if node.is_leaf { break; }
            let pos = Self::child_pos(&node.keys, lo);
            cur = node.children[pos];
        }
        // 順序掃描 leaf linked list
        let mut result = vec![];
        loop {
            let node = &self.nodes[cur];
            for (i, &k) in node.keys.iter().enumerate() {
                if k > hi { return result; } // 超出上界，提前結束
                if k >= lo { result.push((k, node.values[i])); }
            }
            if node.next == NULL { break; }
            cur = node.next;
        }
        result
    }
```

---

## Leaf 有序遍歷（驗證用）

```rust
    fn collect_all(&self) -> Vec<u64> {
        // 找最左 leaf
        let mut cur = self.root;
        while !self.nodes[cur].is_leaf {
            cur = self.nodes[cur].children[0];
        }
        // 走 linked list 收集所有 key
        let mut keys = vec![];
        loop {
            let node = &self.nodes[cur];
            keys.extend_from_slice(&node.keys);
            if node.next == NULL { break; }
            cur = node.next;
        }
        keys
    }
} // end impl BPlusTree
```

---

## 完整測試（WSL 編譯 + 執行）

```rust
fn main() {
    // === 測試 1：亂序插入 20 個 key ===
    let mut tree = BPlusTree::new();
    let inserts = [10u64, 5, 15, 3, 7, 12, 18, 1, 4, 6, 8, 11, 13, 16, 20, 2, 9, 14, 17, 19];
    for &k in &inserts {
        tree.insert(k, k * 10);
    }

    // 驗證 leaf 有序
    let all = tree.collect_all();
    let expected: Vec<u64> = (1..=20).collect();
    assert_eq!(all, expected, "leaf 有序驗證失敗: {:?}", all);
    println!("20-key leaf 有序: {:?}", all);

    // 驗證 search
    for k in 1u64..=20 {
        let got = tree.search(k);
        assert_eq!(got, Some(k * 10), "search({}) 失敗, got {:?}", k, got);
    }
    assert_eq!(tree.search(0), None);   // 不存在的 key
    assert_eq!(tree.search(21), None);
    println!("所有 20 個 point lookup 正確。");

    // 驗證 range scan
    let range = tree.range_scan(5, 12);
    let rk: Vec<u64> = range.iter().map(|(k, _)| *k).collect();
    assert_eq!(rk, vec![5, 6, 7, 8, 9, 10, 11, 12]);
    println!("Range scan [5,12]: {:?}", rk);

    // === 測試 2：50 個 key 壓力測試（會觸發多層 split）===
    let mut t2 = BPlusTree::new();
    for k in 0u64..50 {
        t2.insert(k, k);
    }
    let a2 = t2.collect_all();
    let e2: Vec<u64> = (0..50).collect();
    assert_eq!(a2, e2, "50-key tree 排序失敗");
    for k in 0u64..50 {
        assert_eq!(t2.search(k), Some(k), "50-key search({}) 失敗", k);
    }
    println!("50-key tree: 所有 key 有序且可搜尋。");

    // === 測試 3：邊界——奇數個 key（7 個）split 後各邊幾個？===
    // ORDER=3：leaf split 後左 3 個右 2 個（5 個分）
    // 第一次 leaf split 從 5 個 key 開始
    let mut t3 = BPlusTree::new();
    for k in [1u64, 2, 3, 4, 5, 6, 7] {
        t3.insert(k, k);
    }
    let a3 = t3.collect_all();
    assert_eq!(a3, vec![1, 2, 3, 4, 5, 6, 7]);
    for k in 1u64..=7 {
        assert_eq!(t3.search(k), Some(k), "7-key search({}) 失敗", k);
    }
    println!("7-key tree leaves: {:?}", a3);

    println!("\nB+tree insert/split: 所有測試通過。");
}
```

實測輸出（WSL 編譯通過，無錯誤）：
```
20-key leaf 有序: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]
所有 20 個 point lookup 正確。
Range scan [5,12]: [5, 6, 7, 8, 9, 10, 11, 12]
50-key tree: 所有 key 有序且可搜尋。
7-key tree leaves: [1, 2, 3, 4, 5, 6, 7]

B+tree insert/split: 所有測試通過。
```

---

## 逐步追蹤：前 6 個 key 的 insert

用 ORDER=3（MAX_KEYS=5）插入 [1, 2, 3, 4, 5, 6]，觀察第一次 split 怎麼發生：

```
插入 1, 2, 3, 4, 5：
  root (leaf): [1, 2, 3, 4, 5]  ← 已滿（MAX_KEYS = 5）

插入 6，先觸發 root split：
  建新 internal root，舊 root 為 child[0]
  分裂 child[0]（leaf，mid=3）：
    left:  [1, 2, 3]
    right: [4, 5]
    separator = 4
  
  新 root（internal）：[4]
    children[0] = left leaf  [1,2,3]
    children[1] = right leaf [4,5]
  
  現在 insert 6 進 insert_non_full(root):
    child_pos([4], 6) = 1  （6 >= 4，走右邊）
    children[1] = [4,5]，未滿，insert 6
    right leaf: [4,5,6]

最終結構：
  root: [4]
  left: [1,2,3] → right: [4,5,6]
```

```
繼續插入 7, 8, 9, 10, 11（right leaf 再次滿了時）：
插入 9 後 right leaf = [4,5,6,7,8,9]... 不對——right leaf max 是 5，插入 8 前就該 split

插入 7 後 right leaf: [4,5,6,7]
插入 8 後 right leaf: [4,5,6,7,8]  ← 滿
插入 9：
  insert_non_full(root)：
    child_pos([4], 9) = 1，children[1]=[4,5,6,7,8] 滿
    → split_child(root, 1)
      leaf mid=3：left=[4,5,6]  right=[7,8]  separator=7
    root keys=[4,7]，children=[left1, left2, left3]=[lf(1,2,3), lf(4,5,6), lf(7,8)]
    重新計算：child_pos([4,7], 9) = 2，children[2]=[7,8]，插入 9
    lf(7,8,9)

結構：
  root: [4, 7]
  lf0: [1,2,3] → lf1: [4,5,6] → lf2: [7,8,9]
```

這種逐步追蹤是最好的 split 直覺建立方式。建議自己插入 1–15，在紙上畫每一步的樹形狀。

---

## Split 邊界：奇數 vs 偶數 key 數

我們的 `mid = ORDER = 3`（leaf）意味著：
- 滿的 leaf = 5 個 key
- 分裂後：左 3 個，右 2 個（不對稱）

為什麼不分成 2+3 或 2.5+2.5？

- 分成左 3 右 2：對**升序插入**更友好，因為新 key 通常插在右邊，右邊少一點有更多空間。
- 分成左 2 右 3：對**降序插入**更友好。
- SQLite 用**left-biased**（左多）；B+tree 標準定義說至少 ⌈(2t-1)/2⌉ 個，兩種分法都合法。

只要每個子節點 key 數在 `[t-1, 2t-1]` 之間，分法就合法。我們的 left=3（>= t-1=2）、right=2（>= t-1=2）完全正確。

---

## 記憶體佈局：為什麼 index-based 更貼近真實

真實 B+tree 是持久化到磁碟的：

```
我們的 index-based             真實 DB
────────────────────          ────────────────────
Vec<Node>[0]           ↔      page 0（in buffer pool / on disk）
Vec<Node>[1]           ↔      page 1
NodeId = usize(2)      ↔      PageId = u32(2)
alloc(node)            ↔      new_page()（在 buffer pool 分配新 frame）
nodes[id]              ↔      buffer_pool.fetch_page(id) → frame
```

這個對應關係很直接。Ch 8 的 delete/merge 實作，以及後來要做持久化時，這個心智模型會讓工作輕鬆很多。

`Box<Node>` 版本在記憶體上是散亂的 heap allocation，完全無法對應到磁碟的連續 page 佈局。

---

## 踩雷

1. **Leaf split 用了 `mid = ORDER - 1`（internal 的 mid）**：Leaf split 應該用 `mid = ORDER`，讓右節點是 `keys[ORDER..]`。如果用 `mid = ORDER - 1`，separator（右節點第一個 key）選錯，搜尋路由會在 separator key 附近出錯。

2. **split_child 後用舊的 `pos` 而不是重新計算**：Split 在 parent 插入新 separator 後，child 陣列的索引全部往後移了一位（插入點之後的都加 1）。繼續用舊 `pos` 可能去到分裂前的左節點而不是分裂後的正確節點。最安全的做法：split 後重新呼叫 `child_pos`。

3. **Leaf next 指針沒有正確維護**：Split leaf 後，新的右 leaf 的 `next` 要設成舊 leaf 原本的 `next`（而不是 NULL）。否則 leaf linked list 斷掉，range scan 看不到後面的 leaf，`collect_all` 只返回部分結果。

4. **Root split 忘記更新 `self.root`**：建了新的 internal root 後要更新 `self.root = new_root_id`，否則整棵樹都走舊的 root，等於白 split。這個 bug 不會 panic，只是讓樹高度永遠不增加，insert 超過一定量後就找不到資料。

5. **Internal split 的 children 分錯**：Internal node 有 `keys.len() + 1` 個 child。分裂時：
   - 左節點：`children[0..mid+1]`（mid+1 個 child）
   - 右節點：`children[mid+1..]`（`children.len() - mid - 1` 個 child）
   
   如果 truncate 的邊界錯了一個，右節點少一個或多一個 child，整棵樹就亂掉（search 可能返回錯值甚至 panic）。建議用測試驗證：每次 split 後，internal node 的 `children.len() == keys.len() + 1`。

---

## 進階延伸

- **持久化 B+tree**：把 `Vec<Node>` 替換成「讀/寫 page 到檔案」的函式，加上 buffer pool（Ch 5），就能做出持久化的 B+tree。Node 需要序列化成 binary format（Ch 4 的 slotted page 或更簡單的定長格式）。
- **Bulk loading**：如果你有 N 個已排好序的 key-value，可以直接從左到右填 leaf，再從下往上建 internal node，比逐一 insert 快很多（O(N) vs O(N log N)）。SQLite 的 `CREATE INDEX` 用這個技術。
- **插入時 preemptive split vs split on the way back**：我們的實作是 top-down preemptive split（往下走之前先把 child 可能的 full 解決掉）；另一種是 bottom-up（找到 leaf 再往上傳播分裂，需要 parent stack）。Top-down 的好處是只需要一次 path traversal；bottom-up 在某些並發方案下更容易實作細粒度 latch（latch crabbing，見 Ch 9）。

---

## 本章重點整理

- index-based arena 風格（`Vec<Node>` + `usize` 作 NodeId）是 Rust 中做 B+tree 最務實的選擇，直接對應真實 DB 的 page_id 定址。
- Leaf split：`mid = ORDER`；separator 複製到父節點（key 仍留在右 leaf）；更新 next 指針串聯 linked list。
- Internal split：`mid = ORDER - 1`；中間 key 上移到父節點（從子節點移除）；左右各取剩下的 key 和 child。
- Root split：建新 internal root，把舊 root 當第一個 child，再分裂之——這是樹唯一長高的時機。
- `partition_point(sep <= key)` 是 B+tree internal node 路由的關鍵，separator == key 時走右邊。

## 自我檢核

1. ORDER=3，leaf split 後左右節點各幾個 key？(3 和 2)
2. Internal split 的 `mid = ORDER - 1`，這個 key 去哪了？（上移到父節點，從子節點移除）
3. 插入已存在的 key 會發生什麼？（目前的實作：leaf 有序插入會在 binary search 找到同一個 key，但還是 `Err(pos)` 走 `partition_point(k < key)` 的位置插入，產生重複 key）正確做法是先 search 確認不存在或做 upsert。
4. 為什麼 split 後要重新計算 child 位置，而不是直接用 `pos` 或 `pos + 1`？
5. 試著手動插入 [5,3,7,1,4,6,8,2]（8 個 key，ORDER=3）並追蹤第一次 root split 後的樹形狀。

## 延伸閱讀

1. **《Database Internals》Ch 4 — Implementing B-Trees**（Petrov）：「Splits and Merges」一節有比本章更嚴謹的數學定義，並討論了 bulk loading 與 overflow pages。搭配本章實作讀效果最好。
2. **CMU 15-445 Lecture 8 — Tree Indexes（二）**（Pavlo, 15445.courses.cs.cmu.edu）：有 split 的動畫，以及 C++ BusTub 的作業要求。比對你的 Rust 實作和他的 C++ skeleton，會發現設計決策驚人地相似。
3. **SQLite btree.c — `balance()` 函式**（github.com/sqlite/sqlite）：SQLite 的 split/merge 是 `balance()` 函式，大概在第 7000–9000 行。這個函式很複雜（處理 variable-length cells、free space、overflow），但核心邏輯跟我們完全一樣。從 `balance_nonroot()` 開始看。
4. **CLRS《Introduction to Algorithms》Chapter 18 — B-Trees**（Cormen 等）：最嚴謹的 B-tree 演算法書面定義，用偽代碼描述 insert/delete。我們的 Rust 版本直接對應這裡的演算法。
5. **Bayer & McCreight（1972）原始論文**（acm.org，搜尋「Organization and Maintenance of Large Ordered Indices」）：16 頁，看完對 B-tree 發明背景（磁碟磁頭移動代價、block transfer 最小化）有第一手感受，讓你理解為什麼這個資料結構的設計選擇是這樣而不是另一種方式。

---

→ [Ch 8 B+tree 實作（二）](./08-btree-delete-merge.md)：insert 做完了，現在面對真正的挑戰——delete。刪除 key 後節點可能 underflow（key 太少），需要向兄弟借一個 key（rebalance）或與兄弟合併（merge）。Merge 觸發父節點 key 減少，可能往上傳播，最終讓 root 為空、樹高度降低。
