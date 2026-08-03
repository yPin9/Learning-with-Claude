# Ch 16 — LSM vs B-tree 總對比（RUM Conjecture）

> **目標**：用 RUM conjecture 這個理論框架統一解釋 LSM 和 B-tree 的取捨邏輯、系統性比較兩者在讀/寫/空間各維度的實際行為、能根據工作負載特徵做出有根據的選型決定。

---

## 為什麼需要這章

Part 1 用十章把 B-tree 的每個零件從頭寫過，Part 2 用五章把 LSM 的每個零件從頭寫過。現在你兩個都懂了，問題來了：工程師實際在選型的時候是怎麼想的？為什麼 PostgreSQL 死守 B-tree、RocksDB 全力押注 LSM、TiKV 是 LSM、InnoDB 是 B-tree？

這不是宗教選擇。每個決定背後都有清晰的工程邏輯。這章的任務是把那個邏輯說清楚，讓你不用死記「LSM 適合寫多」這種結論，而是能從第一原理推導出來。

---

## RUM Conjecture：三角取捨的理論基礎

RUM Conjecture 由 Idreos、Athanassoulis 等人在 2016 年 CIDR 論文中提出。名字來自三個放大因子的縮寫：

- **R**（Read Amplification，讀放大）：一次邏輯讀操作實際讀取的資料量 / 邏輯上需要的資料量。
- **U**（Update Amplification，寫放大）：一次邏輯寫操作實際寫入的資料量 / 邏輯上需要的資料量。
- **M**（Memory Amplification，空間放大）：實際佔用的儲存空間 / 有效資料量。

**RUM Conjecture 的核心主張**：針對任何一個資料結構，你無法同時做到讀放大最小、寫放大最小、空間放大最小。降低其中一個，必然增大另外一個或兩個。

這是一個設計空間的觀察，不是嚴格數學定理，但在實務上它準確刻畫了幾乎所有儲存引擎設計的取捨空間。

```
           讀放大最小（R↓）
                 △
                / \
               /   \
              /     \
             /  無法  \
            /  三者全優 \
           /─────────────\
          △               △
    寫放大最小（U↓）    空間放大最小（M↓）

B-tree：R↓、M 中等、U 偏高
LSM：   U↓、R 中等（靠 bloom）、M 偏高（compaction 前）
Hash index：R↓↓、M 中等、U 低（點查），不支援 range
```

讀了 RUM 之後，「為什麼要有 bloom filter」「為什麼 LSM 要做 compaction」就有了統一的解釋：它們都是在這個三角形裡選一個點，然後用各種手段讓那個點更接近理想。

---

## 系統性對比

### 寫入路徑

**B-tree**：

1. 找到目標 leaf page（可能多次磁碟 I/O）。
2. 修改 page（in-place update）。
3. 如果 page 滿了，split 並寫多個 dirty page。
4. 每個 dirty page 都要 fsync 才算持久。

隨機寫入的代價是**隨機 I/O**。SSD 上尚可，HDD 上每次隨機寫比順序寫慢 100 倍以上。B-tree 的寫放大大約是 2–10×（WAL + page 本身）。

**LSM**：

1. 寫入 WAL（順序追加）。
2. 寫入 MemTable（記憶體）。
3. MemTable 滿了 flush 成 SSTable（順序寫）。

所有寫入都是**順序 I/O**。不管資料多隨機，LSM 都把它轉換成追加操作。寫放大靠 compaction 增大（leveled 約 10–30×），但磁碟 I/O 的**模式**更友善，尤其對 HDD。

### 點查（Point Read）

**B-tree**：

從 root 走到 leaf，深度通常是 3–4 層（百萬到億級資料量）。每層一次 I/O，但 root 跟上層節點幾乎一直在 buffer pool 裡。實際 I/O 通常是 1–2 次。讀放大穩定、可預期。

**LSM**：

從 L0 往下找，每層查一個 SSTable（leveled），每個 SSTable 先查 bloom filter（記憶體），bloom 說 maybe 才做 binary search（一次磁碟 I/O）。最壞情況：L0 有 4 個 SSTable + 4 層 = 8 次 I/O。

加了 bloom filter（FPR ≈ 1%）後，大多數層直接被 bloom 過濾掉，實際平均 I/O 接近 1–2 次。但 bloom filter 的效果依賴：
- key 不存在（bloom 100% 節省）
- key 存在於最新層（只查少數幾個 SSTable）

最壞情況（key 存在最老的層）還是要往下翻所有層。

### 範圍查詢（Range Scan）

**B-tree**：

找到下界 leaf，沿著 leaf page 的 linked list 向右掃，全程順序 I/O。範圍查詢是 B-tree 的強項，特別是 covering index 場景。

**LSM**：

每個 SSTable 都要做 merge（用 priority queue 合併多個 SSTable 的遊標）。如果有 key 重疊（L0），還要處理版本選擇。I/O 模式依然偏向順序讀，但 merge 本身有 CPU 開銷。

實務上 LSM 的範圍查詢比 B-tree 貴，差距在 2–5×。但是有個補救手段：block index + block cache 讓 SSTable 的順序讀可以批次進行，HDD 上差距縮小。

### 空間使用

**B-tree**：

- Page 內部碎片（通常 60–70% 填充率，leaf page 可能有 30% 空洞）。
- 不需要 compaction 相關的臨時空間。
- 更新是 in-place，不會有多份舊版本。

**LSM**：

- 同一個 key 在 compaction 完成前可能存在多個 SSTable。
- Compaction 過程中臨時需要 1.5× 磁碟空間。
- Compaction 完成後空間放大低（每 key 幾乎一份）。
- Tombstone 清除不即時，刪大量資料後磁碟佔用不立刻下降。

---

## 總比較表

| 維度 | B-tree | LSM |
|------|--------|-----|
| 寫入模式 | 隨機 in-place I/O | 全部順序追加 I/O |
| 點讀穩定性 | 穩定（3–4 次 I/O） | 依賴 bloom，最壞更差 |
| 範圍查詢 | 優秀（leaf 鏈） | 良好但有 merge 開銷 |
| 空間放大 | 中等（page 碎片） | 高（compaction 前）→ 低（後） |
| 寫放大 | 中等（2–10×） | 高（10–30×，leveled） |
| 讀放大 | 低（穩定） | 中（bloom 後接近低） |
| 刪除 | 立即（in-place） | 延遲（tombstone + compaction） |
| 更新 | 立即（in-place） | 多版本（compaction 清） |
| 並發控制難度 | 複雜（latch crabbing） | 較簡單（immutable SSTable） |
| 崩潰恢復 | WAL redo dirty pages | WAL + MemTable 重放 |
| 主要使用者 | PostgreSQL/MySQL（InnoDB）/SQLite | RocksDB/Cassandra/TiKV/LevelDB/Pebble |

---

## 實務選型指南

### 選 B-tree（或 B-tree 為底的引擎）的場景

1. **OLTP 點查密集**：電商訂單查詢、用戶 profile 讀取——每個請求都是 primary key 查詢，需要穩定的低延遲讀。B-tree 的讀放大穩定可預期。

2. **複雜混合查詢**：範圍查詢、多欄位篩選、join——SQL 資料庫的 secondary index 場景，B-tree 的範圍掃描比 LSM 乾淨。

3. **資料有明顯刪除模式**：電商系統常常刪訂單、軟刪帳號，B-tree 的 in-place 更新讓空間立刻回收，不需要等 compaction。

4. **read-heavy、write 不密集**：讀寫比 10:1 以上，B-tree 沒有 compaction 負擔，更穩定。

### 選 LSM 的場景

1. **寫入密集**：時序資料（metrics、logs、traces）、IoT 資料流、事件溯源（event sourcing）——每秒幾十萬筆寫入，B-tree 的隨機 I/O 是瓶頸，LSM 的順序追加吃滿磁碟頻寬。

2. **HDD 環境**：隨機寫的代價在 HDD 上是 SSD 的 100 倍，LSM 把所有寫入變成順序寫，是 HDD 上最重要的優化之一。

3. **寫多讀少，且讀可以容忍略高延遲**：Cassandra 的典型使用場景，寫入近乎線性擴展，讀取靠 bloom filter 和記憶體快取撐。

4. **資料主要是 append + 偶爾 tombstone**：日誌類、稽核記錄，幾乎沒有更新，compaction 的開銷幾乎全是合併，tombstone 少。

### 邊界模糊的情況

- **SSD + 寫讀混合**：SSD 的隨機寫速度比 HDD 好得多，B-tree 的缺點縮小，兩者差距不大。這時工程品質（buffer pool 大小、WAL 設計）比引擎選型更重要。
- **時序資料庫**：TimescaleDB 用 PostgreSQL（B-tree 底），Prometheus 用自家的 TSDB（類 LSM）。前者優先查詢彈性，後者優先攝取速度。
- **嵌入式場景**：SQLite（B-tree）是最小化依賴的嵌入式選擇；RocksDB 作為 embedded engine 也很常見（TiKV、CockroachDB 都把它當 storage layer）。

---

## 工業界案例

| 系統 | 引擎 | 為什麼選它 |
|------|------|-----------|
| PostgreSQL | B-tree（堆表 + 索引） | OLTP、複雜 SQL、讀寫均衡 |
| MySQL InnoDB | B-tree（clustered index） | OLTP、點查 + 範圍查詢 |
| SQLite | B-tree | 嵌入式、單文件、讀多 |
| RocksDB | LSM（leveled） | Meta 的 UDB，寫入密集、SSD 優化 |
| Cassandra | LSM（STCS/LCS 可選） | NoSQL、寫入優先、分散式 |
| TiKV | LSM（RocksDB） | 分散式 KV，Raft 複製，寫密集 |
| LevelDB | LSM（leveled） | Chrome IndexedDB，嵌入式寫密集 |
| Pebble | LSM（leveled） | CockroachDB 引擎，RocksDB 的 Go 重寫 |
| WiredTiger | B-tree + LSM 可選 | MongoDB 引擎，預設 B-tree |
| InfluxDB IOx | Parquet + LSM 概念 | 時序 OLAP，列式儲存優先 |

---

## 從 RUM 看兩者的演化方向

B-tree 的工程師一直在填它的寫入短板：
- WAL（redo log）把隨機 page write 合併成順序追加。
- Double-write buffer（InnoDB）防止 torn page。
- Undo log（InnoDB）讓 MVCC 不用 in-place 複製整個 page。

LSM 的工程師一直在填它的讀取短板：
- Bloom filter 把大多數讀 miss 從磁碟 I/O 變成記憶體操作。
- Block cache 讓熱資料留在記憶體。
- Leveled compaction 確保每層 key 不重疊，控制讀放大上限。
- WiscKey/TitanDB 把 value 從 LSM 樹分離，大幅降低 compaction 的寫放大。

兩條路線都在向三角形的中心靠攏，但出發點不同，各自的代價結構永遠不會完全消失。

---

## 本章重點整理

- RUM Conjecture：讀放大、寫放大、空間放大三者無法同時最小化，所有儲存引擎設計都在這個空間裡選點。
- B-tree：讀放大穩定低（3–4 I/O）、寫放大中等（隨機 I/O 代價）、空間放大中等（page 碎片）；適合 OLTP 讀多、複雜查詢。
- LSM：寫放大低（順序 I/O）、讀放大靠 bloom filter 壓低（有上限）、空間放大 compaction 前偏高；適合寫密集、HDD、時序資料。
- 選型不是宗教，是根據讀寫比、I/O 模式、硬體環境做的工程決定。
- 工業界案例：PostgreSQL/MySQL = B-tree；RocksDB/Cassandra/TiKV = LSM。

## 自我檢核

- [ ] 我能用 RUM Conjecture 解釋為什麼 bloom filter 存在（它在三角形哪個方向拉）
- [ ] 我能說出 B-tree 點讀穩定而 LSM 點讀最壞情況更差的具體原因
- [ ] 我能解釋為什麼 LSM 在 HDD 環境比 B-tree 有更大的優勢
- [ ] 我能說出 Cassandra 用 LSM 而 PostgreSQL 用 B-tree 的根本原因
- [ ] 我能描述 B-tree 和 LSM 各自在補自己的哪個短板

## 延伸閱讀

1. **「Designing Access Methods: The RUM Conjecture」**（Idreos et al., CIDR 2016）：RUM 的原始論文，不長，值得親讀一遍。它給出的不只是三角取捨，還有每個資料結構在 RUM 空間裡的精確定位——讀完你對 skip list、hash index、B-tree 的位置都會有新的理解。

2. **《Designing Data-Intensive Applications》Ch 3**（Martin Kleppmann）：LSM vs B-tree 最好的白話對比，沒有數學推導，全是直覺和實務觀察；讀這個章節大約要一個小時，是整本書裡最值得單獨摘出來讀的一段。

3. **「WiscKey: Separating Keys from Values in SSD-Conscious Storage」**（Lu et al., FAST 2016）：對 LSM 寫放大的根本性攻擊。把 value 從 LSM 樹分離出去存 vLog，compaction 只排序 key，寫放大下降 90%。TitanDB（TiKV 的子引擎）直接從這篇論文實作。

4. **RocksDB Performance Benchmarks**（`github.com/facebook/rocksdb/wiki/Benchmarking-tools`）：真實的 RocksDB vs 其他引擎 benchmark 數字，包含 random write、sequential write、range scan 各場景。比理論分析更有說服力，也讓你知道「寫放大高」在實際數字上是多少。

5. **「The Design and Implementation of Modern Column-Oriented Database Systems」**（Abadi et al.）：當你把 LSM 的設計思維帶到列式儲存（columnar storage），就到了 OLAP 引擎的世界。這篇 survey 是從 row-store 到 column-store 的橋樑，也是 Ch 36 的預習。

---

Part 2 到這裡結束。我們完整走過了 LSM 儲存引擎的每一層：MemTable（skip list）→ SSTable（block/index/bloom）→ Bloom Filter → Compaction → 和 B-tree 的系統性對比。是時候把這些零件組成一個能跑的 mini LSM engine。

→ [練習 B：mini LSM engine](./practice-b-lsm-engine.md)
