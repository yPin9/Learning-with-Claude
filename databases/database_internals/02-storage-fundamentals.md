# Ch 2 — 儲存的根本問題：記憶體 vs 磁碟

> **目標**：理解「記憶體快但易失、磁碟慢但持久」這個物理現實如何驅動所有資料庫設計決策，以及為什麼不能把記憶體資料結構（BST、hash table）直接搬到磁碟上。這個矛盾是 B-tree、LSM-tree、buffer pool、WAL 存在的共同根源。

---

## 這個矛盾為什麼是所有設計的根

資料庫要解決一個看起來很基本的問題：**讓資料在程式重啟後還在，且能被快速找到**。

RAM 滿足「快」，不滿足「還在」。失電、程式崩潰、OS 重啟——記憶體內容全消。磁碟（HDD 或 SSD）滿足「還在」，不滿足「快」：比記憶體慢好幾個數量級。

這不是工程失誤，是物理限制。DRAM 靠電容儲存電荷，電容放電或斷電就消；旋轉磁碟靠磁場，HDD 要移動機械臂、等磁盤轉到對應磁道，SSD 要做 FTL（Flash Translation Layer）映射。這些物理機制決定了各自的速度上限。

資料庫引擎的每一個設計選擇——B-tree 的 node 大小選 4KB、LSM 的寫路徑走記憶體→磁碟、buffer pool 的存在——都可以追溯到「如何在這個速度鴻溝上優雅地取捨」這個根問題。先把數字搞清楚。

---

## 物理特性對比

### HDD（硬碟）

HDD 把資料寫在旋轉磁盤上，用磁頭讀寫。存取一個位置要花兩個時間：

1. **尋軌時間（seek time）**：磁臂移動到正確磁道，典型 5–10 ms
2. **旋轉延遲（rotational latency）**：等磁盤轉到正確磁區，7200 RPM 磁盤的半周期約 4.2 ms

隨機讀一個 4KB block：平均要等 8–10 ms（機械動作）。

**順序讀則完全不同**。磁頭停在同一軌，資料連續流過，7200 RPM HDD 的順序讀寫可達 **100–200 MB/s**。隨機 vs 順序的差距在 HDD 上是兩個數量級——這個事實比任何別的事實都更深刻地影響了資料庫的設計。

### SSD（NAND Flash）

SSD 沒有移動部件，但它的 flash 物理特性帶來另一種複雜度：

- **讀**：4KB 隨機讀 ~100 µs（比 HDD 快 100×）；順序讀 500 MB/s–7 GB/s（NVMe Gen4）
- **寫**：flash cell 無法直接覆寫，必須先**擦除**再寫入，且擦除的單位（erase block）遠大於讀寫的單位（page）。典型 SSD page 大小 4–16 KB，erase block 大小 256 KB–4 MB
- **寫入放大（Write Amplification）**：更新一個 page 時，SSD 必須讀出整個 erase block、擦除、再把新內容加上舊資料一起寫回。寫入放大因子（WAF）一般 1.5×–10×
- **磨損**：flash cell 有寫入壽命上限（MLC ~3000–5000 次 P/E cycle），FTL 做 wear leveling 延長壽命

對資料庫的意義：SSD 的隨機寫比隨機讀貴（因為寫入放大），所以「寫路徑應該盡量順序寫」的原則在 SSD 時代仍然成立，只是數字縮小了。

### DRAM

DRAM 存取沒有機械動作：CPU 送地址，記憶體控制器取資料，整個過程 ~50–100 ns（main memory），加上 cache miss 的 LLC（L3 cache）miss penalty 約 40–60 ns（依 CPU 架構）。

### 對比表

| 儲存層 | 隨機讀延遲 | 順序讀頻寬 | 備注 |
|--------|-----------|-----------|------|
| DRAM | ~100 ns | ~50 GB/s | 估計值，依 DDR 世代 |
| NVMe SSD | ~100 µs | 3–7 GB/s | 估計值，Gen4/Gen5 差距大 |
| SATA SSD | ~100 µs | ~500 MB/s | 估計值 |
| HDD 7200 RPM | ~8–10 ms | 100–200 MB/s | 估計值，依硬體 |

*以上全為估計值，實際依硬體規格、系統負載、存取模式而異。數字量級比絕對值更重要。*

DRAM 與 HDD 的隨機讀延遲差了 **5 個數量級（~100,000×）**。這不是「快一點」的問題，是截然不同的物理世界。

---

## 為什麼記憶體資料結構搬到磁碟會慘

把 in-memory 資料結構直接持久化到磁碟是最直覺的想法，也是最錯的想法。問題不在實作，在物理定律。

### BST（AVL / Red-Black Tree）

BST 是教科書資料結構的首選：插入、查找、刪除都是 O(log N)，有序遍歷 O(N)，邏輯優雅。

但 BST 的每個操作走的是 **pointer chain**——每個 node 的 `left` 和 `right` 指向任意記憶體位置。在記憶體裡，pointer dereference 的代價是一次 cache miss，最壞 ~100 ns，可以接受。

搬到磁碟上之後，每個 pointer dereference 變成一次**隨機磁碟 I/O**。

**計算示範：100 萬筆資料的 BST 查找**

```
N = 1,000,000
BST 高度 ≈ log₂(1,000,000) ≈ 20 層
每層 = 一次 pointer dereference = 一次隨機 I/O
```

| 儲存媒介 | 每次隨機 I/O | 20 次隨機 I/O | 備注 |
|---------|------------|-------------|------|
| HDD | ~10 ms | ~200 ms | 估計值 |
| SATA SSD | ~100 µs | ~2 ms | 估計值 |
| NVMe SSD | ~50 µs | ~1 ms | 估計值 |

HDD 上查一筆資料要 200 ms。每秒頂多 5 次查詢。對任何正式用途這都是災難性的效能。

更重要的是：每個 BST node 通常只存一個 key + 兩個指標，大小可能只有幾十 bytes。但磁碟 I/O 的最小單位是一個 **block/page**（通常 512B 或 4KB）。你花一次 I/O 讀 4KB，只用到其中幾十 bytes——I/O 預算的 99% 被浪費了。

### Hash Table

Hash table 的隨機查找是 O(1)，看起來更好。對點查詢（point lookup）確實可以。但問題是：

- **無法有序遍歷**：`SELECT * WHERE age BETWEEN 20 AND 30` 這種範圍查詢對 hash table 是 O(N) full scan
- **沒有空間局部性**：hash function 刻意把 key 分散，相鄰 key 的 bucket 離得很遠，順序 I/O 優勢完全用不上
- **resize 代價高**：hash table 滿了要 rehash，期間要搬移大量資料

資料庫 workload 幾乎都有範圍查詢（`WHERE date > X`、`ORDER BY id LIMIT 10`）。純 hash 結構在關聯式資料庫裡只能做特殊場景（hash join 的 join probe 端）。

---

## B-tree 存在的理由

B-tree 的核心思路：**讓「一次 I/O」做更多有用的工作**。

BST 每個 node 一個 key、兩個 pointer。B-tree 每個 node 放 **幾百個 key**（high fanout，高扇出）：

```
BST：高度 ~20，每節點 1 key
B-tree（fanout=200）：高度 ~3，每節點 200 keys

100 萬筆資料：
  BST：   log₂(1,000,000)  ≈ 20 層
  B-tree：log₂₀₀(1,000,000) ≈  3 層   （200^3 = 8,000,000 > 1M）
```

高度從 20 層降到 3–4 層，磁碟 I/O 從 20 次降到 3–4 次。

**ASCII 圖：BST vs B-tree 高度對比**

```
BST（N=1M，高度約 20）：

            root
           /    \
          o      o          ← 每層只有少數 key
         / \    / \
        o   o  o   o
       /\  /\ /\  /\
      ... ... ... ...       ← 還有 16 層...

B-tree（fanout=200，高度約 3）：

    ┌─────────────────────────────────────────────┐
    │  root (200 keys 分隔 201 個子樹)             │
    └──────┬────────────────────┬─────────────────┘
           │                    │
    ┌──────▼──────┐    ┌────────▼────────┐
    │ internal    │    │ internal        │    ← level 2
    │ (200 keys)  │    │ (200 keys)      │
    └──┬────┬─────┘    └──┬────────┬────┘
       │    │             │        │
    ┌──▼─┐ ┌▼──┐       ┌──▼─┐  ┌──▼─┐
    │leaf│ │leaf│       │leaf│  │leaf│        ← level 3（葉節點）
    └────┘ └────┘       └────┘  └────┘
```

**一次 I/O 讀一整個 page（4KB 或 16KB）**，攤銷了隨機 I/O 成本。B-tree node 被設計成恰好填滿一個磁碟 page，每次讀取都充分利用頻寬。

更重要的是：B-tree 維持有序性。葉節點從左到右是排好序的，範圍查詢從起始 key 掃描到結束 key，磁碟存取是 **順序的**。這把隨機 I/O 轉換成快 100× 的順序 I/O。

---

## LSM-tree 存在的理由

B-tree 優化了讀路徑和範圍查詢，但它的寫路徑有個問題：更新一個 node 後要把那個 page 寫回磁碟。如果每筆寫入都觸發隨機 I/O，寫入頻率高的 workload 還是慢。

LSM-tree（Log-Structured Merge-tree）的思路是把「**隨機寫**」變成「**順序寫**」：

1. 寫入先進記憶體（MemTable）
2. MemTable 滿了，整批順序 flush 到磁碟形成一個 SSTable（Sorted String Table）
3. 後台把多個 SSTable 合併（compaction），維持有序性

磁碟上的寫入**永遠是順序的**（append-only），從不做隨機寫。順序寫的速度在 HDD 上比隨機寫快 100×，在 SSD 上也減少了寫入放大。

但代價是讀路徑變貴：找一個 key 可能要查 MemTable + 多層 SSTable，讀放大（read amplification）是 B-tree 的數倍。這就是 **RUM conjecture** 的預告——Read、Update（write）、Memory 三者無法同時最優，取其二必捨其一。Ch 16 會詳細推導。

---

## Rust 範例

### 範例一：記憶體 BST 結構 + 磁碟 I/O 成本計算

這個範例展示 BST 的結構，並用計算說明為什麼不能直接放磁碟。

```rust
// 未在真實磁碟環境驗證；邏輯計算部分正確，I/O 模擬為數字示意

use std::time::Duration;

struct BstNode {
    key: i64,
    left: Option<Box<BstNode>>,
    right: Option<Box<BstNode>>,
}

impl BstNode {
    fn new(key: i64) -> Self {
        BstNode { key, left: None, right: None }
    }

    fn insert(&mut self, key: i64) {
        if key < self.key {
            match self.left {
                Some(ref mut l) => l.insert(key),
                None => self.left = Some(Box::new(BstNode::new(key))),
            }
        } else if key > self.key {
            match self.right {
                Some(ref mut r) => r.insert(key),
                None => self.right = Some(Box::new(BstNode::new(key))),
            }
        }
    }

    fn height(&self) -> usize {
        let lh = self.left.as_ref().map_or(0, |n| n.height());
        let rh = self.right.as_ref().map_or(0, |n| n.height());
        1 + lh.max(rh)
    }
}

/// 模擬磁碟查找代價（不實際讀磁碟，純計算）
fn estimate_bst_disk_lookup(n: u64) -> (Duration, Duration) {
    let height = (n as f64).log2().ceil() as u64;

    // HDD 隨機讀估計 10ms，SSD 估計 100µs（均為估計值）
    let hdd_per_io = Duration::from_millis(10);
    let ssd_per_io = Duration::from_micros(100);

    let hdd_total = hdd_per_io * height as u32;
    let ssd_total = ssd_per_io * height as u32;

    (hdd_total, ssd_total)
}

fn main() {
    // 用排序插入建出最差情況 BST（退化成鏈結串列）
    // 實際資料庫用平衡 BST，但高度仍是 O(log N)
    let n_values: &[u64] = &[1_000, 100_000, 1_000_000, 10_000_000];

    println!("{:<12} {:>8} {:>16} {:>16}", "N", "高度≈", "HDD 延遲（估）", "SSD 延遲（估）");
    println!("{}", "-".repeat(56));

    for &n in n_values {
        let (hdd, ssd) = estimate_bst_disk_lookup(n);
        let height = (n as f64).log2().ceil() as u64;
        println!(
            "{:<12} {:>8} {:>14.1}ms {:>14}µs",
            n,
            height,
            hdd.as_millis(),
            ssd.as_micros(),
        );
    }

    println!();
    println!("B-tree（fanout=200）高度對比：");
    for &n in n_values {
        let height = (n as f64).log(200.0_f64).ceil() as u64;
        let hdd_total = Duration::from_millis(10) * height as u32;
        println!(
            "N={:<12} B-tree 高度≈{:>2}，HDD 估計延遲 {:>4}ms",
            n, height, hdd_total.as_millis()
        );
    }
}
```

執行後預期輸出（數字為估計值）：

```
N            高度≈   HDD 延遲（估）   SSD 延遲（估）
--------------------------------------------------------
1000             10          100ms           1000µs
100000           17          170ms           1700µs
1000000          20          200ms           2000µs
10000000         24          240ms           2400µs

B-tree（fanout=200）高度對比：
N=1000         B-tree 高度≈ 2，HDD 估計延遲   20ms
N=100000       B-tree 高度≈ 3，HDD 估計延遲   30ms
N=1000000      B-tree 高度≈ 3，HDD 估計延遲   30ms
N=10000000     B-tree 高度≈ 4，HDD 估計延遲   40ms
```

100 萬筆：BST 在 HDD 上 200ms，B-tree 30ms——差了近 7 倍。但更重要的是 B-tree 的高度幾乎不隨 N 增長（3→4），BST 是 20→24。N 再大 100 倍，B-tree 高度只加 1。

### 範例二：Vec 連續記憶體 vs 鏈結串列的 cache miss

這個範例在純記憶體中演示空間局部性（spatial locality）的影響，體感化「pointer 跳躍」的代價。

```rust
// 可在 WSL/Linux 實際編譯執行：rustc -O2 cache_miss.rs

use std::time::Instant;

/// 連續記憶體：Vec<i64>，sum 走訪 cache 友好
fn sum_vec(data: &[i64]) -> i64 {
    data.iter().sum()
}

/// 模擬 linked-list 跳躍：把索引打亂後按跳躍順序走訪
/// 不用真 linked-list（Rust 的 linked-list 在 safe code 裡寫起來麻煩）
/// 概念等價：每次存取都跳到 data 中一個「不可預測」的位置
fn sum_random_access(data: &[i64], indices: &[usize]) -> i64 {
    let mut s = 0i64;
    for &i in indices {
        s += data[i];
    }
    s
}

fn main() {
    const N: usize = 4_000_000;

    let data: Vec<i64> = (0..N as i64).collect();

    // 產生打亂的索引（模擬 pointer 隨機跳躍）
    let mut indices: Vec<usize> = (0..N).collect();
    // 用 xorshift 簡易 shuffle，避免引入外部 crate
    let mut rng = 12345u64;
    for i in (1..N).rev() {
        rng ^= rng << 13;
        rng ^= rng >> 7;
        rng ^= rng << 17;
        let j = (rng as usize) % (i + 1);
        indices.swap(i, j);
    }

    // 連續存取
    let t0 = Instant::now();
    let s1 = sum_vec(&data);
    let seq_time = t0.elapsed();

    // 隨機跳躍存取
    let t1 = Instant::now();
    let s2 = sum_random_access(&data, &indices);
    let rand_time = t1.elapsed();

    // 防止編譯器最佳化掉計算
    assert_eq!(s1, s2);

    println!("N = {}", N);
    println!("連續存取（Vec seq）：{:?}", seq_time);
    println!("隨機跳躍存取：        {:?}", rand_time);
    println!(
        "比值：{:.1}x（估計值，依 CPU cache 大小與頻率而異）",
        rand_time.as_nanos() as f64 / seq_time.as_nanos() as f64
    );
    println!("→ linked-list 式的 pointer 跳躍在大資料集上比 Vec 慢數倍至數十倍");
}
```

在典型 x86-64 機器上（L3 cache 8–32 MB），N=4M 個 i64 = 32 MB，超過 L3，隨機存取大量 LLC miss。預期比值 **5×–20×**（估計值，依 CPU 型號與記憶體頻寬而異）。

這個差距在記憶體內部已經如此明顯，搬到磁碟上隨機 I/O 的懲罰是記憶體 LLC miss 的 **1000–100,000 倍**。

---

## B-tree vs LSM-tree vs 純 Hash 取捨對比

| | B-tree | LSM-tree | 純 Hash |
|-|--------|----------|---------|
| **點查詢（讀）** | O(log N)，3–4 次 I/O | O(log N)–O(N)，需查多層 | O(1) 均攤 |
| **範圍查詢** | 優秀，葉節點有序 | 尚可，需合併多個 SSTable | 退化 O(N) |
| **寫入** | 隨機寫，原地更新 | 順序寫，append-only | 均攤 O(1)，可能 rehash |
| **讀放大** | 低（~3–4） | 高（~10–100×，依層數） | 極低（~1） |
| **寫放大** | 中（CoW 或 WAL） | 高（compaction） | 低–中 |
| **空間放大** | 低（約 33%） | 中–高（多個 SSTable 副本） | 低–中（load factor） |
| **有序遍歷** | 支援 | 支援 | 不支援 |
| **代表系統** | PostgreSQL、SQLite、MySQL InnoDB | RocksDB、LevelDB、Cassandra | Redis hash、MemSQL |
| **最適 workload** | 讀多寫少、範圍查詢多 | 寫多、append-heavy | 點查多、無範圍查詢 |

空間放大（space amplification）：指儲存 1 byte 資料實際佔用的磁碟空間。B-tree 節點填充率通常 60–70%，所以放大約 1.4–1.7×；LSM 在 compaction 途中同時存在新舊版本，放大更高。

---

## 踩雷：錯誤直覺 vs 正確認識

**1. 「SSD 很快，隨機 vs 順序的差距不重要了」**

錯。SSD 的 4K 隨機讀 ~100 µs，順序讀可達 GB/s 級。隨機讀的 IOPS 上限（~100K–1M IOPS）遠不如順序讀的頻寬利用率。寫入放大讓 SSD 隨機寫更貴。順序存取優先的設計原則在 SSD 時代依然有效，只是絕對數字縮小了。

**2. 「B-tree 既然這麼好，為什麼還需要 LSM-tree？」**

B-tree 原地更新（in-place update）的寫路徑：找到 node → 修改 page → 把 dirty page 寫回磁碟。高頻寫入時這是大量隨機寫。LSM 的順序 flush 在 write-heavy workload（log 系統、時序資料庫、KV 儲存）下吞吐量高出數倍。沒有萬能結構，只有取捨。

**3. 「記憶體裡跑得快，磁碟上應該也差不多」**

快 1000 倍和快 100,000 倍在程式行為上沒有感知差異，但搬到磁碟後，in-memory 設計的每個 pointer dereference 都變成 10 ms 的隨機 I/O，整個演算法的實際速度可能差 5 個數量級。這不是「優化」能補回來的，是架構選擇的根本性錯誤。

**4. 「BST 平衡了，高度只有 log N，應該夠好吧？」**

log₂(1,000,000) ≈ 20。20 次隨機 I/O on HDD = 200 ms per query，每秒只能跑 5 次查詢。即使在 NVMe SSD 上也要 ~1 ms，每秒 1000 queries。現代資料庫要每秒處理 10 萬次以上查詢，BST 的常數因子完全不夠用。

**5. 「Hash index 查找 O(1)，比 B-tree 的 O(log N) 快，應該用 hash」**

O(1) 的前提是你知道要查的確切 key。一旦有範圍查詢、排序、前綴搜尋，hash index 無能為力。關聯式資料庫的核心 workload 幾乎都帶範圍查詢，hash index 只能作為補充索引（PostgreSQL 支援 hash index，但默認建 B-tree，原因正在這）。

---

## 本章重點整理

- DRAM 與 HDD 的隨機讀延遲差 5 個數量級（~100 ns vs ~10 ms），這個物理鴻溝是資料庫所有核心設計的根本驅動力。
- HDD 的順序讀比隨機讀快 100×；SSD 縮小了這個差距，但順序存取仍有顯著優勢，且 SSD 的寫入放大讓隨機寫更貴。
- BST 搬到磁碟：100 萬筆資料查找 = 20 次隨機 I/O ≈ 200 ms on HDD，每秒只能跑 5 次查詢。
- B-tree 的核心技巧：高扇出（fanout ~200）把樹高壓到 3–4 層，node 大小對齊磁碟 page，每次 I/O 讀一整個 page 攤銷成本，葉節點有序支援範圍查詢。
- LSM-tree 的核心技巧：寫入先進 MemTable，批次順序 flush 到磁碟，把隨機寫變成順序寫；代價是讀放大升高。
- 讀放大、寫放大、空間放大是三方取捨（RUM conjecture），沒有全贏的結構。

---

## 自我檢核

主動回憶，不要看上面的內容：

- [ ] 說出 DRAM、NVMe SSD、HDD 的隨機讀延遲數量級（估計值即可）
- [ ] 解釋 HDD 的「尋軌時間」和「旋轉延遲」各是什麼，合計約多久
- [ ] 說出 SSD 的「寫入放大」為什麼存在（擦除單位 vs 讀寫單位的大小差）
- [ ] 計算：100 萬筆資料的 BST 高度是多少？用 HDD 查找一筆要多久？
- [ ] 解釋 B-tree 的「高扇出」如何把 I/O 次數從 20 次壓到 3–4 次
- [ ] 用一句話說明 LSM-tree 如何把「隨機寫」變成「順序寫」
- [ ] Hash index 的致命弱點是什麼？在什麼場景適合用？

---

## 延伸閱讀

1. **《Database Internals》Ch 1–2（Alex Petrov，2019）**
   讀哪：Ch 1「Introduction and Overview」、Ch 2「B-Tree Basics」前半。
   學什麼：和本章同主題，但給了更多 SSD 物理細節（cell 類型 SLC/MLC/TLC、擦除機制）和 B-tree 變體（B+ tree、B\* tree）的歷史演進。
   關聯：本章給你直覺，Petrov 給你嚴謹的物理模型，放在一起讀效果最好。

2. **《Designing Data-Intensive Applications》Ch 3「Storage and Retrieval」（Martin Kleppmann，2017）**
   讀哪：SSTable/LSM-tree 那一節和 B-tree 那一節，以及「Comparing B-Trees and LSM-Trees」小節。
   學什麼：Kleppmann 用「log 結構儲存」的視角重新解釋 LSM，讀完對 write-ahead log 和 append-only 的哲學更有感。B-tree vs LSM 的取捨在他筆下非常清晰。
   關聯：Ch 17 WAL 章之前可以回頭重讀這節。

3. **Latency Numbers Every Programmer Should Know（Jeff Dean，廣傳版本；多人維護更新版）**
   讀哪：原始投影片 + Colin Scott 的互動版（https://colin-scott.github.io/personal_website/research/interactive_latency.html）
   學什麼：從 L1 cache 到磁碟、網路的延遲全景，以及這些數字在 2005–2020 年間的演變（SSD 如何改變了格局）。
   關聯：本章所有「估計值」的出處都可在這裡找到更有脈絡的版本，且互動版能讓你選年份看數字如何變化。

4. **「The Log-Structured Merge-Tree」（O'Neil et al., 1996）**
   讀哪：Section 1–3（Introduction、Cost Model、The LSM-tree Structure），共約 15 頁。
   學什麼：LSM-tree 的原始論文，最重要的貢獻是「cost model」：把隨機 I/O 換算成順序 I/O 的成本比，給出 LSM 適合哪種讀寫比的量化判斷。
   關聯：Ch 11「LSM 原理」章的理論基礎。

5. **「Organization and Maintenance of Large Ordered Indices」（Bayer & McCreight，1972）**
   讀哪：摘要 + Section 1–2，共 3–4 頁即可。
   學什麼：B-tree 的原始論文（注意：這篇講的是原始 B-tree，不是 B+ tree；葉節點設計不同）。最值得讀的是動機部分：作者直接說了「磁碟存取是瓶頸，我們需要讓每次 I/O 讀更多 key」——和本章的推導一模一樣，只是寫於 50 年前。
   關聯：驗證「B-tree 是對磁碟物理特性的直接回應」這個本章核心論點。

---

儲存的物理現實決定了資料結構的形狀。下一章我們把視角下移到 OS 層：`fsync`、page cache、`O_DIRECT`——這些是資料庫和檔案系統之間的真實接口，也是「寫入成功」到底意味著什麼的答案。

→ [下一章：Ch 3 檔案 I/O 與持久性](./03-file-io-durability.md)
