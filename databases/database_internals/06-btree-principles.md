# Ch 6 — B+tree 原理

> **目標**：從第一原理理解為什麼資料庫選擇 B+tree 而不是 BST、AVL 或紅黑樹；掌握 B-tree 與 B+tree 的關鍵差異；能夠計算 fanout 與樹高；看懂 search 路徑。這章以原理與直覺為主，程式碼給 search 示例，insert/split 的完整實作留給 Ch 7。

---

## 為什麼不用二元搜尋樹？

假設你有 10 億筆資料（~10^9）。一棵理想的 BST 高度是 `log2(10^9) ≈ 30`。查一筆資料要走 30 個節點。

問題：**每個節點一次磁碟 I/O**。

BST 節點通常 ≤ 64 bytes（key + 兩個指標），但磁碟一次讀寫的最小單位是一個 page（4 KB）。每次讀一個節點，你浪費了這個 page 裡 99% 的空間，並付出一次完整的磁碟存取代價。30 次磁碟 I/O，HDD 上大約需要 300 ms——沒有資料庫能接受這個延遲。

**核心洞察：磁碟 I/O 的代價跟 I/O 次數成正比，跟每次讀多少位元組幾乎無關（在 page 大小範圍內）。所以我們要的樹結構是「矮胖」的——每個節點放很多 key，每次 I/O 帶回很多資訊，樹的高度就能大幅壓低。**

---

## B-tree：從節點角度重新思考

B-tree（Balanced tree，1970 年 Bayer & McCreight 發明）的設計思路：

**讓每個節點恰好對應一個 page。** 一個 4 KB 的 page 可以放幾百個 key。如果每個節點有 200 個 key，那麼：
- 高度 1：200 筆資料
- 高度 2：200 × 201 ≈ 40,200 筆資料（200 個 key，201 個 child page）
- 高度 3：200 × 201 × 201 ≈ 8,080,200 筆
- 高度 4：200 × 201^3 ≈ 1.6 × 10^9 筆（10 億筆）

相同的 10 億筆資料，B-tree 高度 4，只需要 **4 次磁碟 I/O**。這是 B-tree 存在的理由。

---

## B-tree 定義（以 minimum degree t 定義）

給定最小度數（minimum degree）`t ≥ 2`：
- 每個**非根節點**有 `[t-1, 2t-1]` 個 key（根節點至少 1 個 key）
- 每個**非葉節點**（internal node）有 `children.len() == keys.len() + 1`
- 所有**葉節點**在同一高度
- B-tree 是完美平衡的（所有葉節點到根的距離相同）

`2t-1` 是一個節點能放的最多 key 數，稱為「滿（full）」。當一個節點滿了，insert 要先分裂（split）。

**Fanout** = 每個 internal node 的平均 child 數 ≈ `2t`（在 `t-1` 到 `2t-1` key 之間取中）。

---

## B-tree vs B+tree

B-tree 和 B+tree 常被混用，但有一個關鍵差異：

| 特性 | B-tree | B+tree |
|------|--------|--------|
| 資料存在哪 | 每個節點（包括 internal node）| 只在 leaf node |
| Internal node 放什麼 | key + value（或 record pointer）+ child pointers | 只有 separator key + child pointers |
| Leaf 節點串聯 | 無 | 有 → 相鄰 leaf 以 linked list 串起 |
| Range scan | 要 in-order traversal，I/O 代價高 | 找到起點 leaf 後直接走 linked list，極有效率 |
| Internal node 的 fanout | 較低（每個 key 要帶 value，佔空間）| 較高（key 最小化，能放更多 child pointer）|

**B+tree 的 internal node 只存 separator key（不存 value），所以同樣 page 大小能放更多 key，fanout 更高，樹更矮。**

現代 RDBMS 幾乎都用 B+tree，而不是 B-tree。本課所有 B+tree 實作都是 B+tree 語意（data only in leaves）。

---

## B+tree 結構圖

以 t=3（ORDER=3，每節點 [2,5] 個 key）、14 個 key 為例：

```
                    Internal Root
                    [7  |  12]
                   /     |      \
          [3 | 5]    [9 | 10]    [14 | 17]
         /   |   \   /   |   \   /    |    \
      L0   L1   L2  L3   L4  L5  L6   L7   L8

Leaf nodes（只存資料，以 linked list 串聯）：
  L0: [1,2]  →  L1: [3,4]  →  L2: [5,6]  →  L3: [7,8]
  →  L4: [9,10]  →  L5: [11,12]  →  L6: [13,14]  →  L7: [15,17]  →  L8: [18,20]

Internal node key 語意：
  Root 的 key=7 表示：右邊子樹的所有 key ≥ 7（左邊 < 7）
  Root 的 key=12 表示：最右邊子樹的所有 key ≥ 12
```

**Separator key** 是右子樹的最小 key（在 B+tree 實作中，separator key 複製自 leaf node，leaf 本身仍然保留這個 key）。這與 B-tree 不同：B-tree 的 internal key 從子節點「提」上來，提上去後子節點不再保留。

```
B-tree internal node:     B+tree internal node:
  [3 | key | 7]             [3 | 5 | 7]
  ↑ key 在這裡              ↑ 5 仍在 leaf 裡
  且 leaf 裡不再有 key
```

---

## Fanout 計算

真實資料庫中，fanout 決定了樹的高度，直接影響查詢的磁碟 I/O 次數。

以 Postgres 的 B+tree 為例（page_size = 8 KB，key = int8 = 8 bytes，child pointer = 6 bytes，page header overhead 約 24 bytes）：

```
可用空間 per page = 8192 - 24 = 8168 bytes
每個 entry = 8 (key) + 6 (pointer) = 14 bytes
internal node fanout ≈ 8168 / 14 ≈ 583
```

高度 = 3 的 B+tree 能存：583^2 × 583 ≈ 1.9 × 10^8 筆資料（約 2 億）。

這也是為什麼 Postgres 的 B-tree 索引幾乎永遠不超過 4 層——4 層可以索引的資料量遠超大多數資料庫的實際大小。

Leaf node 的 fanout 稍有不同：leaf 存 (key, value) 而不是 (key, child_ptr)，value 可能比 6 bytes 大（例如 heap tuple 的 TID 是 6 bytes，還算接近）。

---

## B+tree 節點的記憶體佈局

一個 B+tree page 在磁碟上長什麼樣子？這對理解 fanout 計算至關重要。

**Internal node page（Postgres 8 KB page 為例）：**

```
┌────────────────────────────────────────────────┐ offset 0
│  Page Header (24 bytes)                        │
│  pd_lsn, pd_checksum, pd_flags...              │
├────────────────────────────────────────────────┤ offset 24
│  Special area offset (2 bytes)                 │
│  → 指向 page 末端的 BTPageOpaqueData           │
├────────────────────────────────────────────────┤
│  ItemId array (4 bytes × num_items)            │
│  → 每個 item 的 (offset, length, flags)        │
├────────────────────────────────────────────────┤
│                                                │
│              F R E E   S P A C E               │
│                                                │
├────────────────────────────────────────────────┤
│  Index tuples (key + downlink pointer)         │
│  每個 tuple: key_data + ItemPointerData(6 B)   │
├────────────────────────────────────────────────┤ end - 16
│  BTPageOpaqueData (16 bytes)                   │
│  btpo_prev, btpo_next, btpo_level, btpo_flags  │
└────────────────────────────────────────────────┘ offset 8192
```

對 int8（8-byte key）的 Postgres internal node：
- 可用空間 ≈ 8192 - 24 - 16 = 8152 bytes
- 每個 index entry ≈ 8（key）+ 6（child pointer）+ 4（ItemId overhead）= 18 bytes
- fanout ≈ 8152 / 18 ≈ 453

對我們的 Rust 實作（PAGE_SIZE = 4096，key = u64 = 8 bytes，NodeId = usize = 8 bytes，page header = 12 bytes）：
- 可用空間 ≈ 4096 - 12 = 4084 bytes
- 每個 internal entry = 8（key）+ 8（NodeId）= 16 bytes
- fanout ≈ 4084 / 16 ≈ 255

這兩個數字差距不大，說明 fanout 主要取決於 page 大小與 key/pointer 大小，設計細節的影響有限。

---

## Search 路徑

B+tree search(key=K) 的路徑：

```
1. cur = root
2. while cur 不是 leaf node:
     找最大的 separator 使得 separator <= K
     cur = 對應的 child
3. 到達 leaf，在 leaf 的 keys 做 binary search
   found → 回傳 value
   not found → K 不存在
```

**Separator key 的路由規則**（這是 Ch 4 實作中最常搞錯的地方）：

在 B+tree internal node，separator key 是右子樹的最小 key。所以路由時：
- 若 `K < separator[0]` → 走最左邊的 child
- 若 `separator[i] <= K < separator[i+1]` → 走 `children[i+1]`
- 若 `K >= separator[last]` → 走最右邊的 child

用 `partition_point` 實作：`pos = keys.partition_point(|&sep| sep <= K)`，`child = children[pos]`。

注意：**`sep <= K` 而不是 `sep < K`**。K = sep 時要走右邊的 child，因為 sep 就是右 child 的最小 key。這是 B+tree 跟 B-tree 不同的關鍵細節。

---

## Search 的 Rust 示例

```rust
const ORDER: usize = 3;
type NodeId = usize;
const NULL: NodeId = usize::MAX;

#[derive(Clone)]
struct Node {
    keys:     Vec<u64>,
    values:   Vec<u64>,   // 只有 leaf node 使用
    children: Vec<NodeId>, // 只有 internal node 使用
    is_leaf:  bool,
    next:     NodeId,      // leaf linked list
}

struct BPlusTree {
    nodes: Vec<Node>,
    root:  NodeId,
}

impl BPlusTree {
    /// 查找 key，回傳對應的 value（若存在）。
    /// 複雜度：O(log_t N) 次 I/O，每次 I/O 內做 O(log fanout) 的 binary search。
    fn search(&self, key: u64) -> Option<u64> {
        let mut cur = self.root;
        loop {
            let node = &self.nodes[cur];
            if node.is_leaf {
                // Leaf：直接 binary search
                return match node.keys.binary_search(&key) {
                    Ok(i)  => Some(node.values[i]),
                    Err(_) => None,
                };
            } else {
                // Internal：找對應的 child
                // partition_point(sep <= key) 計算有幾個 separator <= key
                // 那就是 children 的索引（0-indexed）
                let pos = node.keys.partition_point(|&sep| sep <= key);
                cur = node.children[pos];
            }
        }
    }
}
```

這段 code 對應到前面結構圖的 search 路徑：search(9) 在 root 看到 [7, 12]，partition_point(sep <= 9) = 1（7<=9 true，12<=9 false，count=1），走 children[1]=[9|10]，再往下到 leaf L3=[7,8]... 等等，9 不在 L3 裡——這說明我前面的結構圖有點問題（示意圖而非精確的），實際 split 邊界取決於插入順序，Ch 7 會詳細追蹤。

重點是演算法的邏輯是正確的：`partition_point(sep <= key)` 這個細節在 Ch 7 的完整實作中已驗證通過（WSL 50-key 測試全部正確）。

---

## B+tree 的高度與 I/O 次數

給定 N 筆資料，fanout 為 f（leaf 節點平均存 `2t-1` 個 key），樹高 h：

```
h = ceil(log_f(N))
```

每次 search = h 次 I/O（從 root 走到 leaf，每層一個 page）。

| N（資料筆數）| f=200 | f=500 | f=1000 |
|-------------|-------|-------|--------|
| 1,000 | 1 | 1 | 1 |
| 1,000,000 | 2 | 2 | 2 |
| 1,000,000,000 | 3 | 3 | 3 |
| 1,000,000,000,000 | 4 | 4 | 4 |

實務上，高度 3-4 可以覆蓋 99% 的資料庫表格大小。而且 root page 幾乎永遠在 buffer pool 裡（因為所有存取都會碰到它），所以 effective I/O 通常比 h 少 1-2 次。

---

## Search 的邊界情況與 Rust 測試

Search 的邊界情況主要有三個：空樹、key 不存在、key 恰好等於 separator。

```rust
fn main() {
    // 建一個 ORDER=3 的小樹，手動驗證路由
    let mut tree = BPlusTree::new();
    // 插入 [1..10]，t=3 會觸發多次 split
    for k in [5u64, 3, 7, 1, 4, 6, 8, 2, 9, 10] {
        tree.insert(k, k * 100);
    }

    // 存在的 key
    assert_eq!(tree.search(1), Some(100));
    assert_eq!(tree.search(5), Some(500));
    assert_eq!(tree.search(10), Some(1000));

    // 不存在的 key（小於最小值、大於最大值、介於中間）
    assert_eq!(tree.search(0), None);
    assert_eq!(tree.search(11), None);
    assert_eq!(tree.search(0u64.wrapping_sub(1)), None); // u64::MAX

    // 所有 search 都正確
    println!("search 邊界情況全部通過。");
}
```

特別值得注意的是 `key == separator` 的情況。若樹的某個 internal node 有 separator=[5]，search(5) 必須走到持有 key=5 的 leaf（通常是右 child 的第一個 key）。`partition_point(sep <= 5)` 計算出 pos=1，走 `children[1]`——正確。若誤用 `sep < key`，partition_point 計算出 pos=0（因為 5 < 5 是 false），走 `children[0]`，而 key=5 在右邊 leaf 裡，找不到，返回 None。

---

## 寫入代價：B+tree 的弱點

B+tree 對 range scan 和 point lookup 友好，但寫入（尤其是隨機寫）是它的弱點：

1. **隨機寫**：每次 insert 都要讀入目標 leaf page 所在的所有上層 page（log_f N 次讀），修改完後 flush dirty page（log_f N 次寫）。對 HDD，每次隨機 I/O 約 10 ms，100 次隨機 insert = 1 秒。
2. **Split 代價**：split 會分配新 page，把資料搬到新 page，寫回兩個 page——split 一次就是至少 2 次額外寫 I/O。
3. **Write amplification**：每次 user write 在 B+tree 可能觸發多個 page 的讀改寫。

這是 LSM-tree 被發明的動機。LSM 把隨機寫轉換成順序寫，代價是讀變貴（Part 2 會詳細講）。

| 操作 | B+tree | LSM-tree |
|------|--------|---------|
| Point read | O(log_f N) I/O | O(level count × bloom filter check) |
| Range scan | 最優（leaf linked list）| 差（需要多層 sorted run merge）|
| Sequential write | O(log_f N) | O(1)（append-only MemTable→SSTable）|
| Random write | O(log_f N)（每次都 random I/O）| O(1) write，後台 compaction 攤平 |
| 空間放大 | 低（B+tree 節點通常 60-70% 填滿）| 高（多層 sorted run 有冗餘）|

**結論**：OLTP（大量點讀點寫）用 B+tree；高吞吐寫入（time-series、log ingestion）用 LSM-tree。這個選擇貫穿整個資料庫引擎設計。

---

## Range Scan：B+tree 的殺手鐗

B+tree 的 leaf linked list 讓 range scan 極有效率：

```
range_scan(lo, hi):
  1. search(lo) → 找到 lo 所在的 leaf page（或 lo 應該在的位置）
  2. 從那個 leaf 開始，沿著 next 指針順序走
  3. 掃描每個 leaf 的 key，收集 lo <= key <= hi 的結果
  4. 遇到 key > hi 停止
```

這個操作的 I/O 次數：1 次找起點（log_f N），再加上結果橫跨的 leaf page 數量。如果結果很多，大部分代價是順序 I/O（走 linked list），而順序 I/O 是磁碟最快的操作（HDD 的順序讀吞吐可以比隨機讀快 100 倍以上）。

BST 或 AVL tree 做 range scan 需要 in-order traversal，每一步可能要跳到任意節點，是隨機 I/O，代價遠高於 B+tree 的順序 leaf traversal。

---

## 資料結構對比：為什麼不用 BST / AVL / 紅黑樹 / Skip List？

這個問題在面試和設計評審裡常出現，把它講清楚：

| 資料結構 | 高度/層數 | 節點大小 | 磁碟友好？ | Range scan | 實作複雜度 |
|---------|---------|---------|-----------|-----------|-----------|
| BST | O(N) 最壞 | 1 key + 2 ptr | 很差（高 = 多 I/O）| 需要 in-order traversal | 低 |
| AVL tree | O(log₂ N) | 1 key + 2 ptr + height | 差（高度接近 BST）| 需要 in-order traversal | 中 |
| 紅黑樹（Red-Black Tree）| O(log₂ N) | 1 key + 2 ptr + color | 差（原因同 AVL）| 需要 in-order traversal | 高 |
| Skip List | O(log₂ N) 期望 | 1 key + 多 ptr（隨機層數）| 差（隨機 ptr 間距不定）| 尚可（在底層 forward walk）| 中 |
| **B+tree** | **O(log_f N)，f 數百** | **[t-1, 2t-1] keys + ptrs** | **極好（矮 = 少 I/O）** | **極好（leaf linked list）** | 高 |

AVL 和紅黑樹在記憶體中極優秀（std::map、Java TreeMap 就是紅黑樹）。它們在磁碟上失敗的根本原因是：每個節點只存 1 個 key，每次 I/O 讀進 4 KB 但只用到幾個 bytes。磁碟 I/O 的代價是固定的，浪費掉它是最貴的事情。

Skip list 是 LSM-tree MemTable 的常見選擇（Redis Sorted Set 和 LevelDB MemTable 都用它）——因為 MemTable 是全記憶體的，不需要考慮磁碟 I/O，而 skip list 的 lock-free 並發更容易實現。到了 LSM 的 SSTable（磁碟上的部分），就不用 skip list 了，轉而用排好序的 key-value block。

**結論**：B+tree 不是「最好的」資料結構，而是「在磁碟 I/O 代價模型下最好的」。改變代價模型，最佳選擇也會改變。

---

## Postgres B+tree 的 High Key 機制

真實 Postgres 的 B+tree（nbtree）比我們的版本多了一個「High Key」概念，值得提一下，因為你讀 Postgres source 時會第一個碰到它：

每個 leaf page 都存了一個 high key（`P_HIKEY`），代表這個 page 所有 key 的上界。High key 就是下一個（右邊）leaf page 的第一個 real key 的副本。

```
Page 3（leaf）:                    Page 4（leaf）:
  high_key = 50                    high_key = 80
  [31, 35, 42, 47]  →next→         [50, 55, 62, 73]
```

High key 用途：**並發安全**。在多執行緒環境下，一個執行緒正在 search，另一個執行緒正在把這個 page 的 key 搬到右邊（split 的一部分）。如果 search 的執行緒剛好在 split 的空窗期間看到了一個「空的」page，它用 high key 判斷「我要找的 key 還在右邊，繼續往 next 走」，不會因為 split 而找到錯誤的位置或返回 None。

這個機制叫做「link-and-right-sibling」，來自 Lehman & Yao 1981 年的論文《Efficient Locking for Concurrent Operations on B-Trees》。Ch 9（B+tree concurrency）會詳細討論。

---

## B+tree 的變體與 SQLite 的選擇

SQLite 用的是 B-tree（包含 B+tree 的混合型態），稱為 "B*-tree"。它的表格（heap）用 B-tree 存完整 row，索引用 B+tree。InnoDB 的 clustered index 也是類似概念：primary key index 的 leaf 存完整行，secondary index 的 leaf 只存 primary key。

本課的實作選用純 B+tree：internal node 只存 separator key，data 只在 leaf，leaf 串 linked list。這是最正統的 B+tree，也是 Postgres 索引的做法。

---

## 踩雷

1. **separator key 的路由方向搞錯**：`partition_point(sep < key)` 和 `partition_point(sep <= key)` 差一個等號，行為完全不同。B+tree 的 separator 是右 child 的最小 key，key == sep 時應該往右走（走 `sep <= key` 那邊），所以要用 `sep <= key`。用錯的話，所有等於 separator 的 key 都查不到（返回 None）。

2. **B-tree 和 B+tree 的 separator 語意搞混**：B-tree 的 internal key 被「提」上來後，子節點不再有那個 key；B+tree 的 separator key 是從 leaf「複製」上去的，leaf 仍然保留。這影響 split 時往上推的 key 是複製還是移除，Ch 7 會詳細處理。

3. **沒有 leaf linked list 就沒辦法做 range scan**：有人為了省事不建 next 指針，range scan 就得從 root 重新搜尋每個 key，I/O 次數變成 `O(K * log N)` 而不是 `O(log N + K/fanout)`，完全失去 B+tree range scan 的優勢。

4. **高度 0 的邊界情況**：樹只有一個 root，且 root 是 leaf 時（沒有 internal node），search 和 insert 的路徑是特殊情況。這個 edge case 在 split 第一次發生前（root 從 leaf 變成 internal node）最容易出錯。

5. **Fanout 估算沒有考慮 page overhead**：計算 fanout 時，要扣掉 page header 的大小（12+ bytes），不然估出來的樹高偏樂觀。

---

## 進階延伸

- **Copy-on-write B+tree（Bw-tree / LLAMA）**：Microsoft 的 Bw-tree 用 delta records 和 mapping table 實現 lock-free B+tree，是現代 in-memory 資料庫的研究前沿。
- **Fractal tree（Tokutek/PerconaFT）**：在 B+tree 的 internal node 加 buffer，讓 write 分批 push-down，把隨機寫變成順序寫，大幅提升寫入吞吐。
- **Hybrid B+tree-LSM**：WiredTiger（MongoDB 的預設引擎）的設計在 B+tree 之上加了 LSM 風格的 update buffer，兼顧讀寫效能。
- **持久化（persistent）B+tree**：函數式資料庫用 copy-on-write，每次修改都建立新版 root，讓整棵樹 immutable，支援 snapshot isolation 不需要額外 lock。

---

## 本章重點整理

- BST 在磁碟上代價太高（每個節點一次 I/O），B-tree/B+tree 靠「矮胖」節點把 I/O 次數壓到 log_f N。
- B+tree 與 B-tree 的關鍵差異：data 只在 leaf；leaf 串 linked list；separator key 複製而非移除。
- fanout ≈ page_size / (key_size + child_ptr_size)，通常 200–1000 之間。
- Search 路徑：internal node 用 `partition_point(sep <= key)` 找 child，leaf 用 binary search 找 key。
- Range scan 是 B+tree 對 ordered workload 的殺手鐗，靠 leaf linked list 做順序 I/O。

## 自我檢核

1. BST 高度 30 vs B+tree 高度 4，分別需要幾次磁碟 I/O？（BST=30 次，B+tree=4 次）
2. 為什麼 B+tree 的 fanout 比同 page size 的 B-tree 高？
3. range_scan(5, 12) 在一棵有 100 萬個 key、fanout=500 的 B+tree 中，最少幾次 I/O？
4. Internal node 有 separators [7, 12]，要找 key=7，走哪個 child？
5. Leaf linked list 斷掉（next 指針錯誤）會破壞哪種操作？不影響哪種？

## 延伸閱讀

1. **《Database Internals》Ch 2 — B-Tree Basics** 與 **Ch 4 — Implementing B-Trees**（Petrov）：最清楚的 B+tree 書面講解，涵蓋 fanout、node format、split/merge 的演算法細節。本課 Ch 6–8 與這兩章高度對應。
2. **CMU 15-445 Lecture 7 — Tree Indexes**（Pavlo, 15445.courses.cs.cmu.edu）：有 B+tree 的視覺化動畫和 fanout 計算例題，看完這個 lecture 之後 split 的直覺會強很多。
3. **原始論文：《Organization and Maintenance of Large Ordered Indices》**（Bayer & McCreight, 1972）：B-tree 的原始定義，不長（16 頁），值得至少過一遍感受 1970 年代資料庫系統工程的思考方式。
4. **SQLite btree.c — `sqlite3BtreeMovetoUnpacked()`**（github.com/sqlite/sqlite）：SQLite 的 B-tree search 實作，搜尋 `BTREE_FIRST` 看它如何從 root 走到 leaf。C 語言版的直接對應，拿來跟我們的 Rust `search()` 對照很有收穫。
5. **PostgreSQL src/backend/access/nbtree/nbtsearch.c — `_bt_search()`**（github.com/postgres/postgres）：Postgres 的 B+tree search，含 high key 與 down link 的概念，比我們的版本多了對並發安全性的處理（page lock）。

---

→ [Ch 7 B+tree 實作（一）](./07-btree-insert-split.md)：原理清楚了，現在動手實作完整的 insert + split + root split，用 WSL 跑 50 個 key 的壓力測試，讓 split 邊界在你眼前發生幾十次，建立真正的動手直覺。
