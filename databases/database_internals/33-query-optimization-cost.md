# Ch 33 — 查詢優化（二）：Cost-Based Optimizer

> **目標**：理解基於成本的最佳化器（Cost-Based Optimizer，CBO）的完整管線——從統計資訊收集、選擇率估算、基數估算到成本模型，最後用 System R 動態規劃解 join 排序問題。讀完本章你不只能讀懂 PostgreSQL optimizer 的核心設計，也能在自己的引擎裡實作一個可用的 CBO 骨架。

---

## 為什麼 RBO 不夠

上一章的規則式最佳化器（Rule-Based Optimizer，RBO）靠著「謂詞下推」「投影下推」這類啟發式規則把邏輯計劃整理乾淨。但它有一個根本缺陷：**沒有資料，就沒辦法決定哪個計劃更快**。

考慮最簡單的兩表 join：

```sql
SELECT * FROM orders o JOIN customers c ON o.cust_id = c.id
WHERE c.country = 'TW';
```

假設：
- `orders`：1,000,000 筆，1000 個磁碟頁
- `customers`：10,000 筆，10 個磁碟頁，其中 `country = 'TW'` 約佔 2%（200 筆）

RBO 知道謂詞要下推，但不知道該先掃哪張表。有兩種主要選擇：

**計劃 A**：先掃 `orders`（100 萬筆），對每筆找 `customers`（nested loop）
- 估算成本：1000 頁（orders）× 每筆去找 customers → 約 1,000,000 次 lookup

**計劃 B**：先過濾 `customers` 只留 200 筆（country='TW'），再做 hash join
- build hash table on 200 筆，probe 100 萬筆 orders
- 估算成本：10 頁（customers）+ 1000 頁（orders）≈ 1010 次 I/O

計劃 B 在這個資料分布下快了幾百倍。RBO 無法做出這個決定——它不知道 `country='TW'` 只選出 2% 的資料。這就是 CBO 存在的原因。

---

## 建立直覺：CBO 的核心管線

CBO 的工作管線分五步，每步依賴前一步的輸出：

```
收集統計資訊 (Statistics)
    │
    ▼
估算各謂詞的選擇率 (Selectivity Estimation)
    │
    ▼
估算中間結果的基數 (Cardinality Estimation)
    │
    ▼
計算每個運算子的成本 (Cost Model)
    │
    ▼
選出估算總成本最低的計劃 (Plan Enumeration)
```

**關鍵認知**：每一步都會引入誤差，誤差是累積的。統計資訊過時、選擇率假設錯誤、基數估算偏差——三個誤差疊在一起，最終成本估算可能差上幾個數量級。這不是設計缺陷，是本質限制。現代 CBO 的演化史就是一部跟估算誤差搏鬥的歷史。

---

## 統計資訊（Statistics）

### 收集什麼

CBO 需要兩個層級的統計資訊：

**表層級**：
- `n_rows`：總列數
- `n_pages`：佔用的磁碟頁數（決定 seq scan 成本）

**欄位層級**：
- `n_distinct`（NDV，Number of Distinct Values）：不同值的個數，選擇率估算的核心
- `min`、`max`：值域，用於範圍謂詞估算
- `null_fraction`：NULL 佔比
- 直方圖（Histogram）：值分布的近似表示

```rust
#[derive(Debug, Clone)]
pub struct TableStats {
    pub n_rows: u64,
    pub n_pages: u64,
}

#[derive(Debug, Clone)]
pub struct ColumnStats {
    pub n_distinct: f64,    // 正數：實際 NDV；負數（如 -0.3）：比例，PostgreSQL 慣例
    pub null_fraction: f64, // 0.0 ~ 1.0
    pub min_val: Option<Value>,
    pub max_val: Option<Value>,
    pub histogram: Option<Histogram>,
}

#[derive(Debug, Clone)]
pub struct Histogram {
    pub kind: HistogramKind,
    pub bounds: Vec<Value>, // n+1 個邊界代表 n 個桶
}

#[derive(Debug, Clone)]
pub enum HistogramKind {
    EquiWidth,  // 每桶寬度相同，適合均勻分布
    EquiDepth,  // 每桶列數相同（等頻），適合非均勻分布
}
```

### 等寬 vs 等頻直方圖

**等寬直方圖（Equi-Width）**：把值域 [min, max] 切成 k 個等寬桶，記錄每桶的列數。優點是實作簡單；缺點是資料分布不均時，熱桶和冷桶差異很大，估算誤差大。

**等頻直方圖（Equi-Depth）**：每桶儲存相同數量的列（或近似相同）。桶邊界不等寬，但每桶代表相同比例的資料。PostgreSQL 預設用等頻，面對長尾分布時準確度更好。

### 如何收集

`ANALYZE` 指令（PostgreSQL）或 `ANALYZE TABLE`（MySQL）觸發統計收集：對表做隨機取樣（PostgreSQL 預設取樣 30000 筆），從樣本推算全表分布。這意謂著統計資訊天生帶有取樣誤差，且在大量寫入後可能過時。

我們的引擎暫時跳過自動化取樣，讓使用者手動呼叫 `ANALYZE`，把結果寫進系統目錄（catalog）。

---

## 選擇率估算（Selectivity Estimation）

**選擇率**（selectivity）定義為謂詞通過的列佔總列數的比例，值域 [0.0, 1.0]。輸出行數 = 輸入行數 × 選擇率。

### 點謂詞：`col = val`

假設值均勻分布：

```
selectivity = 1.0 / NDV(col)
```

如果有直方圖，找到 `val` 落在哪個桶，用桶內列數 / 總列數。

### 範圍謂詞：`col > val`

無直方圖時，線性插值：

```
selectivity = (max - val) / (max - min)
```

有等頻直方圖時，找到 `val` 所在桶，加上右側所有完整桶的比例。

### 合取（AND）：獨立性假設

```
selectivity(A AND B) = selectivity(A) × selectivity(B)
```

**這個假設幾乎永遠是錯的**。`WHERE city = 'Taipei' AND country = 'TW'` 兩個謂詞高度正相關，獨立性假設會嚴重低估選擇率（估算結果比實際少很多），導致優化器以為結果集很小，選錯計劃。解決方法包括多欄位統計（PostgreSQL 的 `CREATE STATISTICS`）或機器學習估算器，但基本 CBO 都先用獨立性假設。

### 析取（OR）：容斥原理

```
selectivity(A OR B) = sel(A) + sel(B) - sel(A) × sel(B)
```

### LIKE 謂詞

沒有好辦法估算，通常用固定常數。PostgreSQL 對 `LIKE '%pattern%'` 給 0.3。

### Rust 實作

```rust
pub struct SelectivityEstimator<'a> {
    stats: &'a CatalogStats,
}

impl<'a> SelectivityEstimator<'a> {
    pub fn estimate(&self, table_id: u32, pred: &Predicate) -> f64 {
        match pred {
            Predicate::Eq { col, val } => {
                let col_stats = self.stats.column(table_id, *col);
                if col_stats.n_distinct <= 0.0 {
                    return 0.005; // 未知，給保守估計
                }
                1.0 / col_stats.n_distinct
            }
            Predicate::Gt { col, val } => {
                let cs = self.stats.column(table_id, *col);
                match (&cs.min_val, &cs.max_val) {
                    (Some(min), Some(max)) => {
                        let range = max.as_f64() - min.as_f64();
                        if range <= 0.0 { return 0.5; }
                        ((max.as_f64() - val.as_f64()) / range).clamp(0.0, 1.0)
                    }
                    _ => 0.3, // 無統計，給預設值
                }
            }
            Predicate::And(left, right) => {
                // 獨立性假設，已知有誤差
                self.estimate(table_id, left) * self.estimate(table_id, right)
            }
            Predicate::Or(left, right) => {
                let sl = self.estimate(table_id, left);
                let sr = self.estimate(table_id, right);
                (sl + sr - sl * sr).clamp(0.0, 1.0)
            }
            Predicate::Like { .. } => 0.3,
            Predicate::IsNull { col } => {
                self.stats.column(table_id, *col).null_fraction
            }
        }
    }
}
```

---

## 基數估算（Cardinality Estimation）

**基數**（cardinality）指中間結果的列數。

### 單表過濾後基數

```
output_rows = n_rows × selectivity
```

### Join 基數：等值 Join

對 `A.x = B.y` 的等值 join（equi-join），標準公式：

```
output_rows = |A| × |B| / max(NDV(A.x), NDV(B.y))
```

**為什麼是這個公式？** 直覺：如果 A.x 有 100 個不同值，B.y 也有 100 個不同值，且均勻分布，那麼 A 中每個 x 值平均與 B 中 `|B|/100` 筆匹配。`max` 取較大的 NDV 是保守估計——較大的 NDV 代表更少的匹配。

這個公式同樣假設值均勻分布且兩欄位值域重疊，實際上常有大誤差。

### 誤差累積

三表 join 的基數估算是三次獨立估算的乘積。假設每次估算誤差 3 倍，三次累積就是 27 倍。Leis 等人（VLDB 2015）的基準測試顯示，在真實資料集（JOB Benchmark，IMDb 資料）上，現有 CBO 的基數估算誤差中位數超過 1000 倍。

**現代改進**：學習型基數估算器（Learned Cardinality Estimators）如 MSCN（Multi-Set Convolutional Network）用神經網路直接從查詢樣本學習，在實驗設定下誤差比傳統估算低一到兩個數量級。但訓練成本高、泛化性是問題，生產系統尚未廣泛採用。

---

## 成本模型（Cost Model）

成本模型把基數和統計資訊換算成可比較的純量成本值。PostgreSQL 用頁面 I/O 次數作為基本單位，另外乘以調校常數。

| 參數 | PostgreSQL 預設值 | 意義 |
|------|------------------|------|
| `seq_page_cost` | 1.0 | 循序讀一頁的基準成本 |
| `random_page_cost` | 4.0 | 隨機讀一頁（HDD 假設，SSD 應設 1.1～1.5） |
| `cpu_tuple_cost` | 0.01 | 處理一筆 tuple 的 CPU 成本 |
| `cpu_operator_cost` | 0.0025 | 執行一次比較運算的 CPU 成本 |

**注意**：這些常數是 HDD 時代的預設值。現代 SSD 的循序/隨機 I/O 差距遠小於 4 倍，應該根據實際硬體重新校準，否則優化器會過度偏好 seq scan。

### 運算子成本公式

**Seq Scan**：
```
cost = n_pages × seq_page_cost + n_rows × cpu_tuple_cost
```

**Index Scan**（B-tree，簡化版）：
```
cost = log2(n_pages) × random_page_cost   -- 找到葉節點
     + result_rows × random_page_cost      -- 讀取各筆 tuple（假設隨機分散）
     + result_rows × cpu_tuple_cost
```

**Hash Join**：
- Build 階段：掃描 inner 表，建 hash table
- Probe 階段：掃描 outer 表，對每筆 probe

```
cost_build  = n_pages(inner) × seq_page_cost + n_rows(inner) × cpu_tuple_cost
cost_probe  = n_pages(outer) × seq_page_cost + n_rows(outer) × cpu_operator_cost
cost_output = result_rows × cpu_tuple_cost
cost_total  = cost_build + cost_probe + cost_output
```

### Rust 成本計算

```rust
#[derive(Debug, Clone, Copy)]
pub struct CostParams {
    pub seq_page_cost:    f64, // 1.0
    pub random_page_cost: f64, // 4.0（HDD），SSD 建議 1.2
    pub cpu_tuple_cost:   f64, // 0.01
    pub cpu_op_cost:      f64, // 0.0025
}

impl Default for CostParams {
    fn default() -> Self {
        Self {
            seq_page_cost:    1.0,
            random_page_cost: 4.0,
            cpu_tuple_cost:   0.01,
            cpu_op_cost:      0.0025,
        }
    }
}

pub fn cost_seq_scan(n_pages: u64, n_rows: u64, params: &CostParams) -> f64 {
    n_pages as f64 * params.seq_page_cost
        + n_rows as f64 * params.cpu_tuple_cost
}

pub fn cost_hash_join(
    outer_pages: u64, outer_rows: u64,
    inner_pages: u64, inner_rows: u64,
    result_rows: u64,
    params: &CostParams,
) -> f64 {
    let build = inner_pages as f64 * params.seq_page_cost
        + inner_rows as f64 * params.cpu_tuple_cost;
    let probe = outer_pages as f64 * params.seq_page_cost
        + outer_rows as f64 * params.cpu_op_cost;
    let output = result_rows as f64 * params.cpu_tuple_cost;
    build + probe + output
}
```

---

## Join 排序：System R 動態規劃

這是 CBO 最核心的演算法。

### 問題規模

n 張表的 join 有 n! 種排列順序，加上不同的 join 演算法選擇，搜尋空間是指數級的：

| n | n! |
|---|-----|
| 4 | 24 |
| 7 | 5,040 |
| 10 | 3,628,800 |
| 15 | 1.3 × 10¹² |

暴力搜尋在 10 張表以上就不現實了。

### System R 的洞見（Selinger 1979）

Selinger 等人在 IBM System R 的論文中提出兩個關鍵限制，把搜尋空間壓到可接受範圍：

1. **只考慮 Left-Deep 計劃**：join 樹的右子樹永遠是單張基本表（base table），永遠不是中間結果。這讓 inner relation 可以完整載入 hash table 或排序，支援管線化（pipelining）。
2. **動態規劃**：用 `best_plan[S]`（S 是表的子集）記錄「join 集合 S 中所有表的最低成本計劃」，子問題結果重複利用。

Left-deep 限制把搜尋空間從 n! 壓到 2ⁿ 個子集。n=15 時，2¹⁵ = 32768，完全可以接受。

### DP 公式

**基本情況**：
```
best_plan[{T}] = 最便宜的單表存取方法（seq scan 或 index scan）
```

**狀態轉移**：
```
best_plan[S] = argmin over T ∈ S of:
    cost( join( best_plan[S ∖ {T}],  T ) )
```

也就是說：要 join 集合 S 中的所有表，枚舉「最後加入哪張表 T」，取最小成本。

### Rust 實作

```rust
// 未完整編譯驗證，用於說明演算法結構
use std::collections::{BTreeMap, BTreeSet};

#[derive(Debug, Clone)]
pub struct PlanWithCost {
    pub cost: f64,
    pub rows: f64,       // 估算輸出行數
    pub pages: f64,      // 估算輸出頁數（給上層 join 用）
    pub plan: PhysicalPlan,
}

pub fn dp_join_order(
    tables: &[TableRef],          // 所有參與 join 的表，以 index 識別
    join_preds: &[JoinPredicate], // 所有 join 條件
    stats: &CatalogStats,
    params: &CostParams,
) -> Option<PlanWithCost> {
    let n = tables.len();
    // best[S] = 集合 S 的最佳計劃
    let mut best: BTreeMap<BTreeSet<usize>, PlanWithCost> = BTreeMap::new();

    // 基本情況：單表
    for i in 0..n {
        let t = &tables[i];
        let ts = stats.table(t.id);
        let cost = cost_seq_scan(ts.n_pages, ts.n_rows, params);
        let mut s = BTreeSet::new();
        s.insert(i);
        best.insert(s, PlanWithCost {
            cost,
            rows: ts.n_rows as f64,
            pages: ts.n_pages as f64,
            plan: PhysicalPlan::SeqScan { table_id: t.id },
        });
    }

    // 依集合大小由小到大遞推
    for size in 2..=n {
        for s in subsets_of_size(n, size) {
            let mut best_for_s: Option<PlanWithCost> = None;

            for &last in &s {
                // 去掉 last，得到左側集合
                let mut left_set = s.clone();
                left_set.remove(&last);

                let left = match best.get(&left_set) {
                    Some(p) => p,
                    None => continue, // 左側還沒有合法計劃（可能是 cross join，先跳過）
                };

                let right_table = &tables[last];
                let right_stats = stats.table(right_table.id);

                // 找到連接 left_set 與 {last} 的 join 條件
                let pred = find_join_pred(&left_set, last, join_preds);

                // 估算 join 輸出基數
                let (join_rows, join_sel) = estimate_join_cardinality(
                    left.rows, right_stats.n_rows as f64,
                    &pred, stats, params,
                );

                // 這裡只考慮 Hash Join；完整實作也應考慮 Merge Join / Nested Loop
                let j_cost = left.cost + cost_hash_join(
                    left.pages as u64, left.rows as u64,
                    right_stats.n_pages, right_stats.n_rows,
                    join_rows as u64,
                    params,
                );

                let candidate = PlanWithCost {
                    cost: j_cost,
                    rows: join_rows,
                    pages: (join_rows * 8.0 / 4096.0).max(1.0), // 粗估輸出頁數
                    plan: PhysicalPlan::HashJoin {
                        left: Box::new(left.plan.clone()),
                        right: Box::new(PhysicalPlan::SeqScan { table_id: right_table.id }),
                        pred,
                    },
                };

                if best_for_s.as_ref().map_or(true, |b| candidate.cost < b.cost) {
                    best_for_s = Some(candidate);
                }
            }

            if let Some(p) = best_for_s {
                best.insert(s, p);
            }
        }
    }

    // 結果：所有表的完整集合
    let full_set: BTreeSet<usize> = (0..n).collect();
    best.remove(&full_set)
}

// 生成 {0..n-1} 的所有大小為 size 的子集
fn subsets_of_size(n: usize, size: usize) -> Vec<BTreeSet<usize>> {
    let mut result = Vec::new();
    let mut current = BTreeSet::new();
    subsets_helper(0, n, size, &mut current, &mut result);
    result
}

fn subsets_helper(
    start: usize, n: usize, remaining: usize,
    current: &mut BTreeSet<usize>,
    result: &mut Vec<BTreeSet<usize>>,
) {
    if remaining == 0 {
        result.push(current.clone());
        return;
    }
    for i in start..n {
        current.insert(i);
        subsets_helper(i + 1, n, remaining - 1, current, result);
        current.remove(&i);
    }
}
```

### 為什麼只考慮 Left-Deep

Left-deep 計劃的右子樹永遠是基本表（base table），因此 inner relation 的大小是已知且固定的，可以：
- 對 hash join：一次性 build hash table，大小可預測
- 對 merge join：inner 可以提前排序並重複使用
- 整棵計劃樹可以管線化：outer 邊 scan 邊 probe，不需要把整個左側結果具體化（materialize）

**Bushy 計劃**（Bushy Plans）允許兩側都是中間結果。在某些情況下（特別是平行執行環境）bushy 計劃可以更快，但搜尋空間從 2ⁿ 暴增到接近卡特蘭數，通常只有在 n 很小（≤ 5）時才值得枚舉。

---

## Cascades / Volcano 框架概觀

System R DP 在固定的計劃空間（left-deep）上搜尋，規則是靜態的。更現代的框架採用不同思路。

**Volcano Optimizer（Graefe 1993）** 和 **Cascades（Graefe 1995）** 把最佳化問題拆成：
- **邏輯轉換規則（Logical rules）**：等價的關聯代數變換，如 join 交換律、結合律、子查詢展開
- **實體實作規則（Physical rules）**：把邏輯運算子映射到具體演算法，如 `Join` → `HashJoin` / `MergeJoin` / `NestedLoop`

**Memo 結構**：Memo 儲存所有已探索的等價類（Equivalence Group）。每個等價類包含多個等價的邏輯表達式，以及各自的物理實作選項。搜尋是在 Memo 上用 branch-and-bound 剪枝，避免重複計算等價類的最佳解。

這個框架的優勢：
- 規則可擴充，不需要修改核心搜尋邏輯
- 等價類自動處理等價子計劃的去重
- 天然支援啟發式剪枝和成本限制

**現代採用者**：Apache Calcite（Flink、Hive 背後的優化框架）、CockroachDB 的 opt 套件、DuckDB 都採用 Cascades 或類似框架。讀 DuckDB 的 `optimizer/` 目錄可以看到清晰的 Cascades 實作。

我們的課程引擎不會實作完整的 Cascades——它的工程複雜度遠超出課程範疇。但理解 Memo 和規則驅動搜尋的概念，是讀現代優化器論文的前提。

---

## 踩雷

**1. 獨立性假設的陷阱比你想像的嚴重**

`WHERE region = 'North' AND city = 'Taipei'` 的實際選擇率可能是 0.02，但獨立性假設算出 `0.1 × 0.5 = 0.05`——差了兩倍。如果這個謂詞在三層 join 中都出現，最終基數估算可能差 8 倍以上。發現 EXPLAIN ANALYZE 的實際行數和估算行數差距超過 10 倍，先懷疑欄位相關性。

**2. 成本常數沒有針對 SSD 校準**

PostgreSQL 的 `random_page_cost = 4.0` 是 HDD 時代的預設值。在 NVMe SSD 上，循序和隨機 I/O 的差距只有 1.2～2 倍。如果保留預設值，優化器會過度偏好 seq scan，在結果集小的查詢上放棄有效的 index scan。在 SSD 環境把 `random_page_cost` 設成 1.1～1.5。

**3. 統計資訊過時**

批次匯入 100 萬筆資料後，如果沒有執行 `ANALYZE`，優化器還在用舊的 n_rows 和直方圖做決策。PostgreSQL 有 autovacuum 自動觸發 ANALYZE，但在大量寫入後要確認統計資訊已更新。可以查 `pg_stat_user_tables.last_analyze`。

**4. Join DP 忘記處理 Cross Join**

當兩個子集之間沒有任何 join 條件時，就是 Cartesian product（笛卡爾積）。DP 實作中如果跳過沒有 join 條件的組合，最後可能找不到完整集合的計劃。正確做法：允許 cross join，但給它很高的成本，讓優化器盡量避免。

**5. 盲目信任 EXPLAIN，不跑 EXPLAIN ANALYZE**

`EXPLAIN` 只顯示估算成本，不執行查詢。只有 `EXPLAIN ANALYZE` 才能看到實際行數、實際執行時間。估算和實際差距很大時，優化器選的計劃可能完全是錯的。調優工作流應該是：EXPLAIN ANALYZE → 找估算誤差最大的節點 → 收集或更新統計資訊 → 重新評估。

---

## 進階延伸

**Adaptive Query Processing（自適應查詢處理）**：Eddies（Avnur & Hellerstein 2000）的想法是不在查詢開始前固定計劃，而是在執行過程中根據實際觀察到的 tuple 流量動態調整運算子順序。現代系統（SQL Server Adaptive Join、Flink 的 Adaptive Batch Scheduler）已有生產實作。

**學習型基數估算器（Learned Cardinality Estimators）**：MSCN（Multi-Set Convolutional Network，SIGMOD 2019）用卷積神經網路學習查詢模板到基數的映射；NeuroCard（NeurIPS 2020）用 deep autoregressive model 做無 join 的聯合分布估算。這是目前最活躍的學術研究方向，但工程落地仍有挑戰（訓練資料要求、模型更新頻率、推論延遲）。

---

## 本章重點整理

- RBO 沒有資料就無法選擇 join 順序；CBO 的核心是把統計資訊轉換為可比較的成本。
- 統計資訊：表層的 n_rows / n_pages，欄位層的 NDV / min / max / histogram（等頻優於等寬）。
- 選擇率估算：點謂詞 1/NDV，範圍謂詞線性插值，AND 獨立相乘（有誤差），OR 容斥原理。
- Join 基數公式：`|A| × |B| / max(NDV(A.x), NDV(B.y))`；誤差隨 join 數累積，可達數百倍。
- 成本模型：seq scan = n_pages × seq_page_cost；hash join = build + probe + output。SSD 環境必須重新校準 random_page_cost。
- System R DP：只枚舉 left-deep 計劃，狀態 `best[S]` = join 集合 S 的最低成本計劃；狀態轉移枚舉最後加入的那張表，複雜度從 n! 降到 2ⁿ。
- Cascades 框架：規則驅動 + Memo 等價類，是現代生產優化器（Calcite、CockroachDB、DuckDB）的主流選擇。
- 常見誤差來源：欄位相關性、成本常數未校準、統計資訊過時。

---

## 自我檢核

1. 為什麼 `WHERE a = 1 AND b = 2` 的選擇率用獨立相乘會被低估？給一個具體的反例。
2. 等頻直方圖相較等寬直方圖在什麼情況下明顯更準？
3. Join 基數公式 `|A| × |B| / max(NDV(A.x), NDV(B.y))` 的直覺是什麼？如果 NDV(A.x) > |B|，公式會給出什麼結果，合理嗎？
4. System R DP 為什麼只考慮 left-deep 計劃？bushy 計劃在什麼條件下值得搜尋？
5. `random_page_cost = 4.0` 在 NVMe SSD 上會讓優化器偏向哪個方向？應該怎麼修正？
6. Cascades 的 Memo 等價類解決了什麼問題，是 System R DP 沒有解決的？

---

## 延伸閱讀

1. **Selinger et al., "Access Path Selection in a Relational Database Management System" (SIGMOD 1979)** — System R 的原始論文。Section 3（Path Selection for Joins）和 Section 4（Algorithm for Ordering Joins）直接對應本章的 DP join ordering 演算法，必讀。

2. **Graefe, "The Cascades Framework for Query Optimization" (IEEE Data Engineering Bulletin, 1995)** — 重點看 Section 2（Memo Structure）和 Section 3（Rules and Tasks）。理解 Memo 的等價類結構和 rule application 的 top-down 搜尋策略。

3. **CMU 15-445 Lecture 15：Query Planning & Optimization II** — 這堂課專門講 cost model 和 join ordering DP，配合 Andy Pavlo 的投影片看。特別注意他對 left-deep vs bushy 搜尋空間的對比分析。

4. **PostgreSQL source: `src/backend/optimizer/path/joinpath.c`** — 找 `add_paths_to_joinrel()` 函數：這是 PostgreSQL 為每個 join relation 枚舉 hash join / merge join / nested loop 路徑的入口。再看 `src/backend/optimizer/plan/planner.c` 的 `make_one_rel()` 理解整體控制流。

5. **Leis et al., "How Good Are Query Optimizers, Really?" (VLDB 2015)** — 用 JOB Benchmark（IMDb 資料，114 個 SQL）量化現有 CBO 的基數估算誤差，結論是誤差中位數超過 1000 倍。是理解「為什麼學習型估算器被研究」的必讀背景。

---

→ [練習 D：拼出能跑的 SQL 引擎](./practice-d-sql-engine.md)
