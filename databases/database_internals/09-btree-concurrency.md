# Ch 9 — B+tree 並發：latch crabbing

> **目標**：搞懂多執行緒同時讀寫 B+tree 時會出什麼事、latch 和 lock 的差別、latch crabbing（也叫 latch coupling）怎麼用最少的鎖持有時間保住正確性、以及 B-link tree 如何進一步壓低 latch 開銷。用 Rust `RwLock` 示意核心概念。

## 為什麼 B+tree 並發不是「加一把大鎖」就好？

最簡單的做法：整棵樹一把 `RwLock`，讀者共用、寫者獨佔。

這在低並發下完全沒問題。問題出在高並發 OLTP：

- 一棵索引樹可能被 100 個執行緒同時存取
- 每次 insert/delete 從 root 走到 leaf，鎖住整棵樹等於序列化所有寫入
- 現代 NVMe 的 I/O 延遲已經壓到幾十微秒，但 root latch 爭奪就能讓吞吐量崩潰

所以實作需要**細粒度的節點級鎖定**。

## Latch vs Lock — 兩個不同層次

這兩個詞在資料庫文獻裡有明確分工，但外面的世界常常混用：

| 概念 | Latch | Lock |
|---|---|---|
| 保護對象 | **記憶體中的資料結構**（節點、頁面） | **資料庫邏輯物件**（行、表） |
| 持有期間 | 極短：操作（讀/改）期間 | 可能跨越整個交易 |
| 死鎖處理 | 不做死鎖偵測，靠協議避免 | 有死鎖偵測/超時機制 |
| 實作 | OS mutex / spinlock / `RwLock` | 交易管理器維護的鎖表 |
| 交易回滾時 | 不釋放（因為已經釋放了） | 要釋放 |

本章討論的全部是 **latch**——保護 B+tree 節點在記憶體裡的並發存取。

## Latch Crabbing（Latch Coupling）

### 核心思路

從 root 往 leaf 走時，**抓住子節點的 latch，才放父節點的 latch**——像螃蟹橫著走，永遠有一隻腳踩著地。

```
Search（read）：
  1. 鎖 root（讀鎖）
  2. 找到下一個 child，鎖 child（讀鎖）
  3. 放 root 的讀鎖
  4. 重複直到 leaf

Insert/Delete（write）：
  1. 鎖 root（寫鎖）
  2. 找到下一個 child，鎖 child（寫鎖）
  3. 如果 child 是「安全的」（insert: keys < max；delete: keys > min）
     → 放掉所有祖先的鎖
  4. 否則繼續持有，往下走
  5. 在 leaf 完成操作，釋放全部鎖
```

### 「安全節點」的定義

```
安全（對 insert）：節點目前 keys 數 < ORDER - 1，插入後不會 split
安全（對 delete）：節點目前 keys 數 > MIN_KEYS，刪除後不會 underflow
```

如果一個節點是安全的，它的操作不會往上傳播（不會 split 或 merge 到父節點），所以祖先的鎖可以提前釋放。

### 為什麼正確？

不變量：持有父節點 latch 期間，子節點的結構不會被其他執行緒改變。所以往下走的路徑不會因為另一個 split 或 merge 讓我走到錯的節點。

## 圖解：兩個寫者同時 insert

```
執行緒 A：insert(25)        執行緒 B：insert(75)

root[50] ← A 鎖住（寫鎖）

A 往左走 child[10,30]      B 往右走 child[60,80]
A 判斷 child[10,30] 安全   B 嘗試鎖 root → 等待...
A 放 root 的寫鎖
                            B 拿到 root 寫鎖
                            B 往右走，鎖 child[60,80]
                            B 判斷安全，放 root 寫鎖
A 鎖 child[10,30]（寫鎖）  B 鎖 child[60,80]（寫鎖）
→ A 和 B 現在在不同 child，平行執行 ✓
```

Root 是最大的競爭點，但只要往下走的節點是安全的，root 的鎖很快就被放掉。

## 樂觀 Latch（Optimistic Locking）

Write 版本的 crabbing 預設取寫鎖，但實際上大多數 insert 不會觸發 split（假設樹不滿）。

**樂觀策略**：

1. 先用讀鎖走到 leaf（很快）
2. 鎖 leaf 的寫鎖
3. 如果 leaf 安全 → 直接修改，done
4. 如果 leaf 不安全（需要 split）→ 放棄，重新用完整寫鎖的 crabbing 走一遍

這讓「不需要 split 的 insert」只在 leaf 取一次寫鎖，而不是從 root 開始持寫鎖。實測在 90% 不 split 的負載下吞吐量有 2-3x 提升。

## Crabbing 寫路徑的完整走法

讓我用一個具體例子說明悲觀寫路徑：

```
樹結構（order=4，每個節點最多 4 個 key）：

              root[50]                     ← level 0
             /        \
        [20,30]      [60,70,80]            ← level 1
        /  |   \     /  |   |  \
[10,20][20,25][30,40][55,60][65,70][75,80][85,90]  ← level 2（leaf）

執行緒 A：insert(22)
```

**悲觀 crabbing 步驟**：

```
Step 1：write-lock root[50]
  檢查：root[50] 安全？keys.len()=1 < ORDER-1=3 → 安全
  可以提前釋放（但還沒有子節點）

Step 2：找下一個節點 = left child [20,30]
  write-lock [20,30]
  釋放 root[50] 的寫鎖（root 是安全的）

Step 3：[20,30] 安全？keys.len()=2 < 3 → 安全
  可以提前釋放

Step 4：找下一個節點 = leaf [20,25]（22 在 20-25 之間）
  write-lock [20,25]
  釋放 [20,30] 的寫鎖（它是安全的）

Step 5：[20,25] 安全？keys.len()=2 < 3 → 安全（插入後變 3，不超過）
  插入 22：[20,22,25]
  釋放 [20,25] 的寫鎖
```

在這個例子中，root 和 level 1 都是安全的，所以鎖被很快釋放。整條路徑在任何時刻最多持有 2 個 latch（parent + child），並發度很高。

**不安全的情況**（假設 insert(82) 讓 [75,80] overflow）：

```
[75,80] 有 2 個 key，ORDER-1=3，插入後變 3 ← 還安全

但如果 [75,80] 已經有 3 個 key（[75,78,80]）：
  不安全！需要 split！
  → 不能釋放 level 1 的 [60,70,80]（split 需要改 parent）
  → 如果 [60,70,80] 也不安全，繼續持有 root 的鎖
  → 最壞情況：從 root 到 leaf 全部持有寫鎖
```

這就是悲觀策略在觸發 split 時退化的原因——但 B+tree 只有在幾乎滿的情況下才會一路 split，正常工作負載下罕見。

## 死鎖分析：Crabbing 為什麼不會死鎖？

**死鎖的四個必要條件**（Coffman, 1971）：
1. 互斥：資源只能一個持有
2. 保留等待：持有資源的同時等待其他資源
3. 不可搶占：資源只能被持有者主動釋放
4. 循環等待：A 等 B 的資源、B 等 A 的資源

Crabbing 打破的是**循環等待**：所有執行緒永遠都從 root 往 leaf 走，latch 的獲取順序是固定的（level 0 → level 1 → level 2 → ...）。

如果 A 持有 level 2 的節點等 level 3，B 只能是等 level 2 或更低的節點——不可能有「B 持有 level 3 等 level 2」的情況，因為那個方向不存在。

**破壞的反例**：如果你加了一個「leaf 向左 merge 時需要鎖住左兄弟」的邏輯，而且左兄弟和當前節點在不同的 sub-tree 下，就可能出現：

```
A：持有 leaf[30]，嘗試鎖 leaf[20]（左兄弟）
B：持有 leaf[20]，嘗試鎖 leaf[30]（右兄弟，因為它要 merge）
→ 死鎖！
```

解決方案：merge 只往一個方向做（只合併進左兄弟，或只合併進右兄弟），確保鎖的獲取方向全局一致。

## B-link Tree（Lehman-Yao，1981）

### 問題：crabbing 仍然慢

即使樂觀策略，搜尋路徑上的每個節點都要拿讀鎖再放。高度 4 的樹 = 4 次 latch acquire/release。

Lehman 和 Yao 1981 年提出 B-link tree，用**每個節點加一個 right-link（兄弟指標）**，讓搜尋可以在**完全不拿任何 latch** 的情況下完成。

### 核心思想

允許「在我讀某個節點的時候，另一個執行緒正在 split 它」。這時讀者看到的可能是舊版本（split 後的 left half），但沒關係——如果我的 key 應該在 right half，right-link 讓我找得到：

```
split 前：  node[10,20,30,40]  → ...

split 中（另一個執行緒正在做）：
            node[10,20]  →(right-link)→  new_node[30,40]
            parent 還沒更新

讀者走到 node，看到 keys 只到 20，且我的 key=35 > 20
→ 跟著 right-link 到 new_node，找到 35 ✓
```

### 代價

- 每個節點多一個指標（16 bytes，可接受）
- 讀者可能多走一個節點（順著 right-link 走）
- 實作複雜度提升，特別是 delete/merge 的時候 right-link 要正確維護

InnoDB 的 B+tree 就是 B-link tree 的變體。

## 真實系統的 Latch 設計

### InnoDB（MySQL）

InnoDB 的 B-link tree 使用自製的 `rw_lock_t`（`storage/innobase/include/sync0rw.h`），底層是 spinlock + 等待機制的混合體：

- 持有時間短時：CAS spinlock
- 等待時間長時：yield 讓出 CPU

關鍵函式 `btr_cur_search_to_nth_level()` 實作了 crabbing，並根據游標模式（`BTR_SEARCH_LEAF` vs `BTR_MODIFY_TREE`）選擇讀鎖還是寫鎖。

### PostgreSQL

PostgreSQL 的 heap 不是 B-link tree（是 heap + nbtree），但 `nbtree` 的 page latch 用的是 lightweight lock（`LWLock`），有讀寫之分，並且有 dedicated waiters list（避免 pthread_rwlock 的 starvation 問題）。

PostgreSQL 處理 concurrent split 的方式不是 B-link right-link，而是 **right-link + high key**：每個 leaf 有一個 high key（這個 leaf 能存的最大 key），讀者用它判斷要不要跟著 right-link 走。

### SQLite

SQLite 是單程序資料庫，多執行緒共用同一個連線靠 global mutex（`sqlite3_mutex`）保護，不做細粒度 latch。並發的實現靠「多個連線」而不是「一個連線多執行緒」——每個連線有自己的 b-tree cursor，用**檔案級 locking**（shared/exclusive）協調。

這是設計哲學的差異：SQLite 的目標是嵌入式使用（單一 writer），InnoDB 的目標是高並發 server。

## Rust 示意實作

生產級會用 spinlock 或自製 latch（避免 OS 的 mutex 開銷），這裡用 `RwLock` 示意 crabbing 的結構：

```rust
use std::sync::{Arc, RwLock};

// 每個節點被 Arc<RwLock<Node>> 包住
// 注意：生產級不用 std::sync::RwLock，而是用 parking_lot 或自製 spinlock
#[derive(Debug)]
struct Node {
    is_leaf: bool,
    keys: Vec<i64>,
    // leaf: values；internal: child Arc pointers
    values: Vec<String>,
    children: Vec<Arc<RwLock<Node>>>,
    next_leaf: Option<Arc<RwLock<Node>>>,
}

struct BPlusTree {
    root: Arc<RwLock<Node>>,
}

impl BPlusTree {
    /// 用 latch crabbing 做搜尋（read path）
    pub fn search(&self, key: i64) -> Option<String> {
        // Step 1：鎖 root（讀鎖）
        let mut current_lock = self.root.read().unwrap();

        loop {
            if current_lock.is_leaf {
                // 找 key
                return current_lock.keys.iter()
                    .position(|&k| k == key)
                    .map(|i| current_lock.values[i].clone());
            }

            // 找下一個 child
            let pos = current_lock.keys.partition_point(|&k| k <= key);
            let next = Arc::clone(&current_lock.children[pos]);

            // Step 2：先鎖 child（讀鎖），才放 parent 的讀鎖
            // Rust borrow checker 要我們用 drop 顯式控制生命週期
            let next_lock = next.read().unwrap();
            drop(current_lock);          // 放父節點讀鎖
            current_lock = next_lock;   // 繼續持有子節點讀鎖
        }
    }

    /// 樂觀 insert：先樂觀走讀路徑，leaf 安全就直接改
    /// （為了展示概念，省略實際 split 觸發的悲觀回退路徑）
    pub fn optimistic_insert(&self, key: i64, value: String) -> Result<(), ()> {
        // Phase 1：用讀鎖走到 leaf
        let leaf_arc = self.find_leaf_optimistic(key);

        // Phase 2：拿 leaf 寫鎖
        let mut leaf = leaf_arc.write().unwrap();

        // Phase 3：檢查是否安全（不需要 split）
        let order = 4usize;
        if leaf.keys.len() >= order {
            // 不安全，需要 split → 應該走悲觀路徑（這裡簡化為 Err）
            return Err(());
        }

        // 安全：直接插入
        let pos = leaf.keys.partition_point(|&k| k < key);
        leaf.keys.insert(pos, key);
        leaf.values.insert(pos, value);
        Ok(())
    }

    fn find_leaf_optimistic(&self, key: i64) -> Arc<RwLock<Node>> {
        let mut current_arc = Arc::clone(&self.root);
        loop {
            let next_arc = {
                let guard = current_arc.read().unwrap();
                if guard.is_leaf {
                    return Arc::clone(&current_arc);
                }
                let pos = guard.keys.partition_point(|&k| k <= key);
                Arc::clone(&guard.children[pos])
                // guard（讀鎖）在這裡 drop ← 這就是樂觀策略的關鍵：
                // 到 leaf 之前，每個節點只是短暫拿讀鎖再馬上放
            };
            current_arc = next_arc;
        }
    }
}

// 注意：上面的 Arc<RwLock<Node>> 結構在 Rust 裡有一個問題：
// 若要實作 B-link tree，next_leaf 指標會造成循環引用。
// 生產做法是用 arena + index，避免 Arc 的引用計數開銷，
// 也避免循環引用問題。這裡純粹示意 latch 持有策略。

#[cfg(test)]
mod tests {
    use super::*;

    fn make_leaf(keys: Vec<i64>, values: Vec<String>) -> Arc<RwLock<Node>> {
        Arc::new(RwLock::new(Node {
            is_leaf: true, keys, values,
            children: vec![], next_leaf: None,
        }))
    }

    fn make_internal(keys: Vec<i64>, children: Vec<Arc<RwLock<Node>>>) -> Arc<RwLock<Node>> {
        Arc::new(RwLock::new(Node {
            is_leaf: false, keys, values: vec![],
            children, next_leaf: None,
        }))
    }

    #[test]
    fn test_search_in_two_level_tree() {
        // 建立一棵 2 層的樹：root[30] → [leaf[10,20], leaf[30,40]]
        let left  = make_leaf(vec![10, 20], vec!["ten".into(), "twenty".into()]);
        let right = make_leaf(vec![30, 40], vec!["thirty".into(), "forty".into()]);
        let root  = make_internal(vec![30], vec![left, right]);
        let tree  = BPlusTree { root };

        assert_eq!(tree.search(10), Some("ten".to_string()));
        assert_eq!(tree.search(40), Some("forty".to_string()));
        assert_eq!(tree.search(99), None);
    }

    #[test]
    fn test_optimistic_insert_safe() {
        let leaf = make_leaf(vec![10, 20], vec!["ten".into(), "twenty".into()]);
        let tree = BPlusTree { root: leaf };
        // 樹只有一個 leaf，且只有 2 個 key（< order=4），safe to insert
        assert!(tree.optimistic_insert(15, "fifteen".to_string()).is_ok());
        assert_eq!(tree.search(15), Some("fifteen".to_string()));
    }
}
```

**WSL 執行**：

```bash
# 在 Cargo 專案的 src/ 下建立此模組後：
wsl cargo test btree_concurrency -- --nocapture
```

## 多執行緒競爭的實際後果

沒有任何 latch 保護的話，兩個執行緒同時修改 B+tree 節點會出現什麼情況？

### 場景：Write-Write 競爭

```
節點 N 有 keys = [10, 20, 30]
執行緒 A：insert(25)  執行緒 B：insert(35)（同時進行，都沒拿鎖）

A 讀 keys = [10, 20, 30]，找到位置 2，準備插入
B 讀 keys = [10, 20, 30]，找到位置 3，準備插入

A 寫：keys = [10, 20, 25, 30]
B 寫：keys = [10, 20, 30, 35]（覆蓋了 A 的修改！）

結果：25 消失，資料遺失
```

Rust 的 `Vec` 操作不是 atomic，`insert()` 底層要移動記憶體，期間另一個執行緒讀到中間狀態是 UB。

### 場景：Split 期間的 Read

```
節點 N 正在被 split：A 執行緒把 [10,20,30,40,50] 分成 [10,20] 和 [30,40,50]
parent 的 separator 還沒更新

B 執行緒找 key=35：
  走到 N（old pointer）→ N 只剩 [10,20] → 找不到 35
  parent 沒有 right-link → B 認為 35 不存在
  
結果：Ghost Read（key 明明存在卻找不到）
```

這是 crabbing 要解決的問題：B 在 A 更新 parent 之前就放棄了正確的搜尋路徑。

## Latch 的實作選型

不同場景用不同的 latch 實作：

| 實作 | 適用場景 | 特性 |
|---|---|---|
| `std::sync::Mutex` | 低並發、簡單正確性 | Poisoning；Linux 上 futex，比 spinlock 省 CPU |
| `std::sync::RwLock` | 讀多寫少 | Linux 有 writer starvation 風險 |
| `parking_lot::RwLock` | 生產 B+tree | Starvation-free；lock/unlock 比 std 快 ~2x |
| 自製 spinlock（`AtomicBool` + CAS） | 持有時間極短（<1 µs）的 latch | 在高核心數機器上可能比 mutex 快；但空轉浪費 CPU |
| 版本號（`AtomicU64` + seqlock） | OLFIT/OLC 讀路徑 | 讀者零 latch，寫者增版本號，讀者最後驗證 |

**實務建議**：B+tree latch 的持有時間通常在幾百 ns 以內（修改節點 keys 的時間），spinlock 或 `parking_lot` 都合理。如果持有時間超過 1 µs（因為需要 I/O），一定用 mutex/RwLock，不然空轉浪費核心。

## OLC（Optimistic Lock Coupling）版本號示意

版本號方案不用傳統 latch，而是在每個節點加一個 `version: AtomicU64`：

```rust
use std::sync::atomic::{AtomicU64, Ordering};

struct NodeHeader {
    version: AtomicU64,
    // ... 其他 metadata
}

// 寫者協議：
// 1. version 奇數 = 有寫者持有（lock bit）
// 2. 寫前：version += 1（變奇數）
// 3. 寫後：version += 1（變偶數）

// 讀者協議：
// 1. 讀前記錄 v1 = version（確認是偶數）
// 2. 讀資料
// 3. 讀後比對 v2 = version，若 v1 == v2 → 讀有效

fn read_with_validation(header: &NodeHeader) -> bool {
    let v1 = header.version.load(Ordering::Acquire);
    if v1 & 1 == 1 { return false; } // 有寫者，重試
    
    // ... 讀節點資料 ...
    
    let v2 = header.version.load(Ordering::Acquire);
    v1 == v2 // 讀期間沒有寫者介入
}
```

這是 OLFIT 的核心思想：讀者完全不拿 latch，讀完才用版本號驗證有沒有被打擾。如果被打擾，重新讀一次。

好處：讀路徑的 critical section 消失，多核心 scalability 接近線性。代價：程式設計複雜，且讀者必須容忍「讀到中間狀態再重試」。

## 對比表格：三種並發策略

| 策略 | Latch 範圍 | 讀吞吐 | 寫吞吐 | 實作難度 |
|---|---|---|---|---|
| 大樹鎖 | 整棵樹 | 低（讀者也序列） | 低 | 低 |
| Crabbing（悲觀） | 路徑上每個節點 | 中 | 中 | 中 |
| Crabbing（樂觀） | 只有 leaf 寫鎖 | 高 | 高（大多數情況） | 中高 |
| B-link tree | 搜尋零 latch | 最高 | 高 | 高 |

## 踩雷

1. **讀鎖在切換到子節點前沒有先拿好子節點鎖**：crabbing 的核心是「先鎖子，再放父」，順序顛倒就等於短暫沒有任何鎖，另一個 split 剛好在這個 window 發生，讀者就走到一個已被 split 分裂的節點的錯誤位置。

2. **Arc\<RwLock\<Node\>\> 在 Rust 裡的生命週期**：`read()` 回傳的 `RwLockReadGuard` 持有 borrow，必須在拿下一個節點的鎖之後才 `drop`，否則 Rust borrow checker 會拒絕（或者更危險：手動 unsafe 繞過之後出現 deadlock）。

3. **Deadlock：兩個寫者從不同方向走**：crabbing 協議規定永遠從 root 往 leaf 走，所以不會有「A 持有 node1 等 node2、B 持有 node2 等 node1」的循環等待。破壞這個前提（例如允許 leaf scan 反向走），就可能 deadlock。

4. **B-link tree 的 split 必須先寫好 right-link 才能斷開 parent pointer**：如果順序反過來，一個讀者在 parent 更新和 right-link 建好之間的 window 進入，就找不到被 split 出去的右半。

5. **`parking_lot::RwLock` vs `std::sync::RwLock`**：std 版本在 Linux 上是 pthread_rwlock，有 writer starvation 問題（無限多讀者可以讓寫者餓死）。生產環境用 `parking_lot` 的 fair RwLock 或自製 latch。

## 進階延伸

- **OLFIT（Optimistic Latch-Free Index Traversal）**：OLC（Optimistic Lock Coupling）的進一步演化，讀者完全不拿 latch，只在最後驗證版本號（optimistic validation）。被 HyPer、Umbra 等 HTAP 系統使用。
- **Bw-Tree**（Microsoft，2013）：delta record + CAS，實現無鎖 B-tree，是 Azure Cosmos DB 的儲存索引。完全不用 latch，靠記憶體級 CAS 操作。
- **CMU 15-445 Lec 9（Index Concurrency Control）**：Andy Pavlo 的完整講解，含 OLFIT 與 Bw-tree 的圖解。

## 本章重點整理

- Latch 保護記憶體資料結構（節點），Lock 保護邏輯資料物件（行/表），持有期間完全不同。
- Latch crabbing：從 root 往 leaf 走，「先鎖子節點才放父節點」，安全節點提前釋放祖先鎖。
- 樂觀策略：先讀鎖走到 leaf，leaf 安全就只拿一次 leaf 寫鎖；不安全時回退到悲觀路徑。
- B-link tree 讓搜尋不需任何 latch，靠 right-link 修復 split 期間的「看到舊 left half」問題。
- `Arc<RwLock<Node>>` 在 Rust 裡示意並發，生產用 arena + spinlock 或 parking\_lot。

## 自我檢核

- [ ] 我能解釋 latch 和 lock 的三個主要差異（保護對象、持有時間、死鎖處理）
- [ ] 我能畫出 crabbing 搜尋的鎖定/釋放順序
- [ ] 我能說出「安全節點」的定義，以及為什麼安全節點允許提前釋放祖先鎖
- [ ] 我能解釋 B-link tree 如何讓讀者在沒有 latch 的情況下正確處理 split
- [ ] 我知道 Rust `Arc<RwLock<Node>>` 的 `drop` 順序為什麼影響 crabbing 的正確性

## 延伸閱讀

1. **Lehman, P. L., & Yao, S. B. (1981). "Efficient Locking for Concurrent Operations on B-Trees."** ACM TODS — B-link tree 的原始論文，3 頁，非常易讀，是本章的理論基礎。
2. **CMU 15-445 Lecture 9 "Index Concurrency Control"** — 完整涵蓋 crabbing、OLFIT、Bw-tree，有投影片和影片。
3. **InnoDB 源碼 `btr0btr.cc`**（MySQL）— 看真實的 B-link tree 加 latch crabbing 實作，函式 `btr_cur_search_to_nth_level()` 是起點。
4. **Wang, Z. et al. (2018). "Building A Bw-Tree Takes More Than Just Implementing The Paper."** SIGMOD — 誠實紀錄把 Bw-tree 論文做成工業級系統時踩的所有坑。

---

→ [Ch 10 索引：secondary/covering/range scan](./10-indexes.md)
