# Ch 15 — Compaction 策略

> **目標**：搞清楚為什麼 LSM 必須做 compaction、size-tiered 與 leveled 兩種策略在寫/讀/空間放大上的實質差異、tombstone 怎麼被清乾淨、用 Rust 實作簡化版 leveled compaction 並在 WSL 跑通。

---

## 為什麼需要 Compaction

MemTable 滿了就 flush 成 SSTable。沒有 compaction 的 LSM，SSTable 只會越積越多，造成三個死亡螺旋：

1. **讀放大（read amplification）爆炸**：每次 get 要從最新的 L0 SSTable 一路往下翻，每個 SSTable 都要查 bloom filter，bloom 說 maybe 就得做 binary search。十幾個 SSTable 疊在一起，一次點讀會變成十幾次磁碟 I/O。

2. **空間放大（space amplification）惡化**：同一個 key 可能同時存在五個 SSTable 裡，最新版本有效、舊版本佔著空間不還。刪除只寫 tombstone，真正的舊值要靠 compaction 才能物理消除。

3. **tombstone 永不消失**：你執行 `delete("user:42")`，LSM 只寫一個帶刪除標記的 tombstone entry。這個 tombstone 要被合併到包含原始值的 SSTable 才能把舊值清掉。沒有 compaction，tombstone 跟原始值各活各的，白白佔空間，讀的時候還需要兩個都遍歷。

Compaction 的本質是：**定期把多個 SSTable 合併成更少、更大、去重、去 tombstone 的 SSTable**。這個動作本身會產生 I/O，這叫寫放大（write amplification）。所有的 compaction 策略都在做這個三角取捨。

---

## 先建立直覺

想像你是圖書館員，每次有新書就先堆在入口的暫存桌（L0）。暫存桌滿了，你得去整理書架：

- **Size-Tiered（分層堆疊）**：等暫存桌上有四本差不多厚的書，把它們合成一本厚書推到「厚書區」；厚書區滿了四本，再合成超厚書——層層堆疊。優點：合併次數少，整理工作量小。缺點：找一本書要掃很多層，而且同一本書可能在三個不同的厚書裡各有一份（舊版本）。

- **Leveled（分層有序）**：每一層保證 key 範圍不重疊，每一層比上一層大 10 倍。新書合入某一層時必須找到同 key 範圍的舊書合併，確保每一層裡每個 key 最多一份。找書只需掃每層一個位置。代價：合併更頻繁，寫放大高。

這個直覺對應到了真實資料庫：Cassandra 早期用 STCS（size-tiered），RocksDB、LevelDB、TiKV 用 LCS（leveled）。

---

## 兩種策略的結構

### Size-Tiered Compaction（STCS）

```
L0:  [SST-1]  [SST-2]  [SST-3]  [SST-4]   ← 每個 ~64MB，key 範圍可能重疊
           │
           │  4 個同量級的 SSTable 合併
           ▼
L1:  [SST-A]  [SST-B]                       ← 每個 ~256MB，依然可能 key 重疊
           │
           │  再累積、再合併
           ▼
L2:  [SST-X]                                ← ~1GB
```

- 觸發條件：同一層的 SSTable 數量超過閾值（通常 4）。
- 合併對象：同一層大小相近的 SSTable。
- key 重疊：**允許**，同層 SSTable 的 key range 可以交叉。
- 寫放大：低（每個資料大約被合併 log 次，以 10 倍成長為例約 log₁₀(資料量)）。
- 讀放大：高（同層 SSTable 有 key 重疊，一次點查可能要掃整層）。
- 空間放大：高（舊版本可能分散在多個 SSTable，tombstone 清得慢）。

### Leveled Compaction（LCS）

```
L0:  [SST-new]                              ← 可以有少量（通常 ≤ 4）
         │
         │  flush 觸發，和 L1 中有 key 重疊的 SSTable 合併
         ▼
L1:  [a–f]  [g–m]  [n–z]                   ← key range 不重疊，總大小上限 ~256MB
         │
         │  L1 超出限制，選一個 SSTable 往 L2 推
         ▼
L2:  [a–c][d–f][g–i]...[x–z]               ← key range 不重疊，總大小上限 ~2.5GB（L1 * 10）
```

- 每層的 **key 完全不重疊**（L0 是例外，剛 flush 的還沒合併）。
- 合併觸發：某層超出大小上限，選一個 SSTable 和下一層有 key 重疊的 SSTable 合併，結果寫回下一層。
- 寫放大：高（一份資料在每層都要被重寫一次，LCS 的寫放大約 10× 每層）。
- 讀放大：低（每層最多查一個 SSTable）。
- 空間放大：低（每個 key 在每層最多一份，舊版本很快被合併覆蓋）。

### 對比一覽

| 維度 | STCS（size-tiered） | LCS（leveled） |
|------|---------------------|----------------|
| 寫放大 | 低（~3–5×） | 高（~10–30×） |
| 讀放大 | 高（掃多個 SSTable） | 低（每層一個） |
| 空間放大 | 高（多份舊版本） | 低（每 key 幾乎一份） |
| tombstone 清理速度 | 慢 | 快 |
| 適合場景 | 大量寫入、讀少或靠 bloom filter 擋 | 讀寫混合、空間敏感 |
| 工業案例 | Cassandra 早期 STCS、ScyllaDB | RocksDB、LevelDB、TiKV、Pebble |

---

## Tombstone 與過期資料清理

Tombstone 是 LSM 的「軟刪除」機制：刪除一個 key 時，寫入一個帶有刪除標記的 entry，不管舊值在哪個 SSTable 裡。

tombstone 要被物理清除，有一個嚴格的前提：**合併時必須已經包含了所有可能含有該 key 舊版本的 SSTable**。

```
SST-A（舊）：  key="user:42" value="alice"
SST-B（新）：  key="user:42" tombstone

合併 A+B → 兩條都刪掉（tombstone 消滅了舊值）
如果只合併 B：tombstone 留著（後面還要和 A 相遇）
如果只合併 A：舊值被保留（看起來像復活了）
```

在 leveled compaction 裡，因為每層 key 不重疊，tombstone 只要推到最底層就能安全刪除（最底層已經是最舊的資料）。在 STCS 裡，tombstone 要橫跨多個同層 SSTable 才能清乾淨，清理比較慢。

TTL（time-to-live）資料的清理也在 compaction 時進行：每條 entry 帶過期時間戳，合併時檢查，過期就丟棄。

---

## Compaction 觸發與 I/O 放大

觸發 compaction 的時機：

1. **L0 SSTable 數量超過閾值**：最高優先，L0 有 key 重疊，數量多讀放大最嚴重。
2. **某層總大小超出上限**：leveled compaction 的主要觸發路徑。
3. **後台定時觸發**：確保 tombstone 不會太久沒清。

Compaction 本身是 I/O 密集型操作：讀入多個 SSTable、歸併排序、寫出新 SSTable。這個 I/O 和正常的讀寫請求競爭磁碟頻寬。

工業級 LSM 引擎（RocksDB）的處理方式：
- **rate limiter**：限制 compaction 使用的 I/O 速率（bytes/sec）。
- **優先佇列**：L0 → L1 的 compaction 優先級高於 L2 → L3。
- **自適應觸發**：根據當前寫入速率動態調整 compaction 觸發閾值。

---

## Rust 實作：簡化版 Leveled Compaction

我們實作一個概念層面的 leveled compaction：把多個有序 SSTable（用 `Vec<(String, Option<String>)>` 表示，`None` 是 tombstone）合併成一個去重、去 tombstone 的有序結果。

```rust
// src/main.rs
use std::collections::BTreeMap;

/// 一條 SSTable entry。
/// key: 資料庫 key
/// value: Some(v) 表示正常值，None 表示 tombstone（刪除標記）
#[derive(Debug, Clone)]
struct Entry {
    key: String,
    value: Option<String>,
}

/// 模擬一個 SSTable（已排序）。
type SSTable = Vec<Entry>;

/// Leveled compaction 的核心操作：
/// 把一組 SSTable 合併成一個新的 SSTable。
/// - sst_list[0] 是最舊的，sst_list[last] 是最新的。
/// - 同一個 key 以最新版本為準。
/// - tombstone（value = None）的 key 在最終結果裡完全移除
///   （假設這次 compaction 已經包含了所有可能的舊版本）。
fn compact(sst_list: &[SSTable]) -> SSTable {
    // BTreeMap 保證輸出有序；後寫入的覆蓋先寫入的（從舊到新遍歷）
    let mut merged: BTreeMap<String, Option<String>> = BTreeMap::new();

    for sst in sst_list {
        for entry in sst {
            // 後來的永遠覆蓋先前的
            merged.insert(entry.key.clone(), entry.value.clone());
        }
    }

    // 過濾 tombstone，輸出最終存活的 entry
    merged
        .into_iter()
        .filter_map(|(k, v)| {
            v.map(|val| Entry { key: k, value: Some(val) })
        })
        .collect()
}

/// 在合併結果裡做點查。
fn get(compacted: &SSTable, target_key: &str) -> Option<String> {
    // SSTable 已排序，可以二分搜；這裡為示範用線性搜
    compacted
        .iter()
        .find(|e| e.key == target_key)
        .and_then(|e| e.value.clone())
}

fn main() {
    // 模擬三個 SSTable，從舊到新
    let sst0: SSTable = vec![
        Entry { key: "apple".into(),  value: Some("fruit".into()) },
        Entry { key: "banana".into(), value: Some("yellow".into()) },
        Entry { key: "cherry".into(), value: Some("red".into()) },
    ];

    let sst1: SSTable = vec![
        Entry { key: "banana".into(), value: Some("ripe".into()) },  // 更新
        Entry { key: "date".into(),   value: Some("sweet".into()) },
    ];

    let sst2: SSTable = vec![
        Entry { key: "apple".into(),  value: None },                  // tombstone：刪掉 apple
        Entry { key: "cherry".into(), value: Some("dark red".into()) }, // 更新
    ];

    println!("=== Before Compaction ===");
    println!("SST-0 (oldest): {:?}", sst0);
    println!("SST-1:          {:?}", sst1);
    println!("SST-2 (newest): {:?}", sst2);

    let result = compact(&[sst0, sst1, sst2]);

    println!("\n=== After Compaction ===");
    for entry in &result {
        println!("  {:?} => {:?}", entry.key, entry.value);
    }

    println!("\n=== Point Queries ===");
    println!("get(apple)  = {:?}  (should be None, tombstone cleaned)", get(&result, "apple"));
    println!("get(banana) = {:?}  (should be 'ripe')", get(&result, "banana"));
    println!("get(cherry) = {:?}  (should be 'dark red')", get(&result, "cherry"));
    println!("get(date)   = {:?}  (should be 'sweet')", get(&result, "date"));
}
```

執行：

```bash
cargo new lsm-compact && cd lsm-compact
# 把上面的程式碼貼入 src/main.rs
cargo run
```

期望輸出：

```
=== Before Compaction ===
SST-0 (oldest): [Entry { key: "apple", value: Some("fruit") }, ...]
SST-1:          [Entry { key: "banana", value: Some("ripe") }, ...]
SST-2 (newest): [Entry { key: "apple", value: None }, ...]

=== After Compaction ===
  "banana" => Some("ripe")
  "cherry" => Some("dark red")
  "date"   => Some("sweet")

=== Point Queries ===
get(apple)  = None  (should be None, tombstone cleaned)
get(banana) = Some("ripe")  (should be 'ripe')
get(cherry) = Some("dark red")  (should be 'dark red')
get(date)   = Some("sweet")  (should be 'sweet')
```

`apple` 的 tombstone 在合併時把原始值一起消滅，最終結果裡完全消失。這就是 compaction 物理清除資料的機制。

**邊界情況**：tombstone 出現在最舊的 SSTable、值出現在最新的 SSTable（代表先刪再寫）時，最新版本（Some 值）應該贏，不應該被舊的 tombstone 覆蓋。上面的實作用從舊到新的遍歷順序讓後來者覆蓋先前者，正確處理了這個情況。

---

## 進階：TIERED+LEVELED 混合策略

RocksDB 的實際 compaction 策略比上面複雜得多：

- **Universal Compaction**：STCS 的變體，適合寫入密集、SSD 場景，動態計算 size ratio 決定合併對象。
- **FIFO Compaction**：時序資料特化，只保留最近 N 秒的資料，超出直接刪最舊的 SSTable，不做合併。
- **Leveled + Tiered 混合（RocksDB L0 特殊處理）**：L0 允許 key 重疊（tiered 行為），L1 以下強制 key 不重疊（leveled 行為）。這是一個工程上的妥協：L0 的 SSTable 直接從 MemTable flush 過來，如果也要維持 key 不重疊會大幅增加 flush 延遲。

---

## 踩雷記錄

1. **Tombstone 提前被清除**：compaction 沒有包含某層更舊的 SSTable，就把 tombstone 丟掉，結果舊值「復活」。觸發場景：只做 L0→L1 的部分合併，L2 裡還有老資料。規則：tombstone 只能在確定包含了所有更舊版本後才能安全清除。

2. **L0 fan-out 未限制**：寫入速度遠超 compaction 速度，L0 SSTable 累積到幾十個，每次點讀都要掃所有 L0。解法：在 L0 SSTable 數量超過警戒值（如 20）時，降低寫入速率甚至暫停寫入（write stall）。

3. **Compaction I/O 和前景讀寫搶磁碟**：沒有 rate limiting 的 compaction 可以讓 p99 讀延遲飆到秒級。線上環境必須給 compaction 設 I/O 限速。

4. **Key 刪除後的 key space 碎片**：大量刪除後 SSTable 裡存的都是 tombstone，空間沒有立刻回收。要確保 compaction 有被觸發，不然磁碟佔用率持續攀升。

5. **合併輸出的臨時空間**：leveled compaction 合併 L1→L2 時，要先把新 SSTable 寫完才能刪舊的（原子替換）。這段時間舊 SSTable 和新 SSTable 同時存在，磁碟需要額外 ~1.5× 的暫存空間。

---

## 本章重點整理

- LSM 需要 compaction 才能控制讀放大、清理 tombstone、回收空間。
- STCS：寫放大低、讀放大高、空間放大高，適合寫多讀少。
- LCS：讀放大低、空間放大低、寫放大高，每層 key 不重疊，適合讀寫混合。
- Tombstone 只在 compaction 合併了所有更舊版本的 SSTable 後才能安全清除。
- Compaction 本身是 I/O 密集型後台任務，生產環境必須做 rate limiting 和 write stall 保護。

## 自我檢核

- [ ] 我能解釋為什麼沒有 compaction 的 LSM 讀取會越來越慢
- [ ] 我能說出 STCS 和 LCS 在寫放大、讀放大、空間放大三個維度上的差異
- [ ] 我能解釋為什麼 tombstone 不能在 compaction 合併不完整的情況下提前清除
- [ ] 我能說出 L0 的 key 重疊與 L1+ 的 key 不重疊分別帶來什麼代價與好處
- [ ] 我了解 compaction rate limiting 為什麼是生產環境的必需品

## 延伸閱讀

1. **RocksDB Compaction — 官方 Wiki**（`github.com/facebook/rocksdb/wiki/Compaction`）：RocksDB 六種 compaction 策略的完整說明，含 Universal/FIFO/Leveled 的觸發邏輯與參數調整——你自己寫完 compaction 再來對照這個，直接看到工業級實作的每個決策點。

2. **《Database Internals》Ch 7「Log-Structured Storage」**（Alex Petrov）：STCS 與 LCS 的理論推導，包含 size ratio 選擇對寫放大的影響數學式，比 RocksDB wiki 更系統化。

3. **「Monkey: Optimal Navigable Key-Value Store」**（Dayan et al., SIGMOD 2017）：從理論上推導 bloom filter 的記憶體分配方式，讓 leveled LSM 的讀寫放大達到最優——這篇論文直接影響了 RocksDB 的 bloom filter budget 設計。

4. **RocksDB 原始碼 `db/compaction/compaction_picker.cc`**：真實的 compaction 選擇邏輯，把「選哪個 SSTable 合併」的演算法完整實作出來，是上面簡化版的工業級版本。

5. **「WiscKey: Separating Keys from Values in SSD-Conscious Storage」**（Lu et al., FAST 2016）：把 value 從 LSM 樹分離出去存到單獨的 vLog，大幅降低 compaction 的寫放大——這是對 LSM 寫放大問題的一個根本性解法，TitanDB（TiKV 的 value-separation 引擎）源自這篇。

---

下一章我們把整個儲存引擎的討論收束：LSM 和 B-tree 的系統性對比，以及工業界怎麼在兩者之間做選型決策。

→ [Ch 16 LSM vs B-tree 總對比（RUM）](./16-lsm-vs-btree.md)
