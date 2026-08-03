# Ch 34 — 統計與 Cardinality Estimation 深挖

> **目標**：理解查詢優化器如何用統計資料估算 selectivity 與 cardinality、實作 equi-depth histogram 與 HyperLogLog，並看清楚估錯 cardinality 如何毀掉整個執行計畫。

## 為什麼這件事如此關鍵

Ch 33 的 cost-based optimizer 要比較不同計畫的代價，代價公式裡最重要的輸入是 cardinality——「這個算子輸出幾列？」。你寫得再精巧的 optimizer，底下的 cardinality estimate 爛了，選出來的計畫一樣是垃圾。

實務最大的痛點在 join：三張表做 multi-join 時，如果第一個 join 的輸出 cardinality 估錯，第二個 join 的輸入就錯，第三個更錯。估計誤差在 join chain 裡會成倍放大。PostgreSQL 的查詢計畫偶爾慢到令人費解，通常就是統計過舊或 correlation 沒捕到，導致 optimizer 選了錯的 join order 或 join algorithm。

## 先建立直覺

問題從最簡單的開始。假設有張表：

```
orders(order_id, customer_id, amount, status)
  10,000,000 rows
```

查詢：`WHERE status = 'shipped'`

optimizer 問：這個 predicate 過濾後剩幾列？  
如果沒有統計，只能猜：`1 / distinct_values(status)`，假設均勻分佈。  
如果有統計，知道 `status` 有 5 個值且分佈不均：

| status    | 比例  |
|-----------|-------|
| pending   | 35%   |
| shipped   | 40%   |
| delivered | 20%   |
| cancelled | 4%    |
| returned  | 1%    |

有統計：選擇率 (selectivity) = 40%，輸出 4,000,000 列。  
沒統計：選擇率 = 1/5 = 20%，輸出 2,000,000 列。

差兩倍，已足以讓 optimizer 選錯 join order。

## 統計的三個層次

```
┌─────────────────────────────────────────────────────┐
│ Level 1：基本統計                                     │
│   - table cardinality (n_rows)                       │
│   - n_distinct per column                            │
│   - null fraction                                    │
│   - min / max                                        │
├─────────────────────────────────────────────────────┤
│ Level 2：分佈統計                                     │
│   - histogram（equi-width / equi-depth）              │
│   - most common values (MCV) + 其頻率                │
├─────────────────────────────────────────────────────┤
│ Level 3：多欄統計（進階）                              │
│   - multi-column correlation statistics              │
│   - extended statistics（PostgreSQL 14+）            │
└─────────────────────────────────────────────────────┘
```

PostgreSQL 的 `pg_statistic` 與 `pg_stats` 視圖就對應這三層。

## Histogram：Equi-Width vs Equi-Depth

### Equi-Width（等寬直方圖）

把值域 [min, max] 切成 B 個等寬桶（bucket），計算每桶有幾個值。

```
amount 範圍: [0, 1000]，切成 5 桶，每桶寬 200

桶:  [0,200)  [200,400)  [400,600)  [600,800)  [800,1000]
計數:   500      3000       4000       2000       500
```

估算 `WHERE amount BETWEEN 350 AND 450`：
- 跨越桶 [200,400) 後半 + 桶 [400,600) 前半
- 簡化：假設桶內均勻，350 在第二桶 75% 處，450 在第三桶 25% 處
- 估計 = 3000 * 0.25 + 4000 * 0.25 = 750 + 1000 = 1750

**缺點**：對偏斜分佈效果差。如果 amount 90% 都在 [0, 200) 這桶，等寬就完全沒用。

### Equi-Depth（等深直方圖）

每桶裡的資料列數相同（等深），桶的邊界不等寬。這樣密集區有很多細桶（精確），稀疏區合併成寬桶。

```
10,000 rows，切成 5 桶，每桶 2,000 rows

邊界 quantile:
  桶 0: [0, 45)       → 前 20% 的 row 都擠在這
  桶 1: [45, 120)
  桶 2: [120, 280)
  桶 3: [280, 600)
  桶 4: [600, 1000]
```

估算 `WHERE amount < 100`：
- 100 落在桶 1 中，桶 1 邊界 [45, 120)
- 100 在桶 1 中的比例 = (100 - 45) / (120 - 45) ≈ 73%
- 桶 1 有 2,000 rows，前面桶 0 有 2,000 rows
- 估計 = 2,000 + 2,000 * 0.73 = 3,460 rows

等深對偏斜分佈顯著更準，PostgreSQL 用的是等深直方圖。

## Rust 實作：Equi-Depth Histogram

```rust
// 未編譯驗證（邏輯正確，需在 Rust 專案中實際整合）
// src/statistics/histogram.rs

#[derive(Debug, Clone)]
pub struct EquiDepthHistogram {
    /// 桶的邊界，長度 = buckets+1
    /// boundaries[i] <= x < boundaries[i+1] 屬於桶 i
    boundaries: Vec<f64>,
    /// 每桶的 row 數（估計值）
    counts: Vec<u64>,
    /// 總 row 數
    total: u64,
}

impl EquiDepthHistogram {
    /// 從已排序的樣本建立等深直方圖
    /// samples 必須已排序，num_buckets 通常取 100-200
    pub fn build(mut samples: Vec<f64>, num_buckets: usize) -> Self {
        samples.sort_by(|a, b| a.partial_cmp(b).unwrap());
        let n = samples.len();
        if n == 0 || num_buckets == 0 {
            return Self {
                boundaries: vec![],
                counts: vec![],
                total: 0,
            };
        }

        let bucket_size = n / num_buckets;
        let mut boundaries = Vec::with_capacity(num_buckets + 1);
        let mut counts = Vec::with_capacity(num_buckets);

        boundaries.push(samples[0]);

        for i in 1..=num_buckets {
            let idx = (i * bucket_size).min(n) - 1;
            boundaries.push(samples[idx]);
            let prev_idx = ((i - 1) * bucket_size).max(0);
            counts.push((idx - prev_idx + 1) as u64);
        }

        // 確保最後邊界包含最大值
        if let Some(last) = boundaries.last_mut() {
            *last = samples[n - 1];
        }

        Self {
            boundaries,
            counts,
            total: n as u64,
        }
    }

    /// 估算 value <= threshold 的 row 數
    pub fn estimate_less_equal(&self, threshold: f64) -> u64 {
        if self.boundaries.is_empty() {
            return 0;
        }
        if threshold < self.boundaries[0] {
            return 0;
        }
        if threshold >= *self.boundaries.last().unwrap() {
            return self.total;
        }

        // 找到 threshold 落在哪個桶
        let bucket_idx = self
            .boundaries
            .partition_point(|&b| b <= threshold)
            .saturating_sub(1)
            .min(self.counts.len() - 1);

        let low = self.boundaries[bucket_idx];
        let high = self.boundaries[bucket_idx + 1];
        let bucket_rows = self.counts[bucket_idx];

        // 此桶之前的所有 row 數
        let before: u64 = self.counts[..bucket_idx].iter().sum();

        // 此桶內線性插值
        let fraction = if high > low {
            (threshold - low) / (high - low)
        } else {
            1.0
        };

        before + (bucket_rows as f64 * fraction) as u64
    }

    /// 估算 lo <= value <= hi 的 row 數（range selectivity）
    pub fn estimate_range(&self, lo: f64, hi: f64) -> u64 {
        if lo > hi {
            return 0;
        }
        let upper = self.estimate_less_equal(hi);
        let lower = self.estimate_less_equal(lo).saturating_sub(1);
        upper.saturating_sub(lower)
    }

    /// 回傳 selectivity（比例 0.0–1.0）
    pub fn selectivity_range(&self, lo: f64, hi: f64) -> f64 {
        if self.total == 0 {
            return 0.0;
        }
        self.estimate_range(lo, hi) as f64 / self.total as f64
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_uniform_distribution() {
        // 均勻分佈 0..100
        let samples: Vec<f64> = (0..1000).map(|x| x as f64 / 10.0).collect();
        let hist = EquiDepthHistogram::build(samples, 10);

        // 約 50% 的 row 在 [0, 50]
        let sel = hist.selectivity_range(0.0, 50.0);
        assert!((sel - 0.5).abs() < 0.05, "selectivity={}", sel);
    }

    #[test]
    fn test_skewed_distribution() {
        // 偏斜：90% 的值在 [0, 10)，10% 在 [10, 100]
        let mut samples: Vec<f64> = (0..900).map(|x| x as f64 / 100.0 * 10.0).collect();
        samples.extend((0..100).map(|x| 10.0 + x as f64 * 0.9));
        let hist = EquiDepthHistogram::build(samples, 10);

        // [0, 5) 應有約 45% 的 row
        let sel = hist.selectivity_range(0.0, 5.0);
        assert!((sel - 0.45).abs() < 0.08, "selectivity={}", sel);
    }
}
```

## Distinct 值估計：HyperLogLog

「這欄有多少個 distinct 值？」直接計算代價是 O(n)，而且需要額外記憶體。HyperLogLog (HLL) 是一個機率資料結構，用少量記憶體（幾 KB）估計 cardinality，誤差約 1–2%。

### 直覺原理

隨機雜湊一個值，觀察雜湊結果的**前導零位數**：
- 前導零越多，代表遇到這個值的機率越低
- 如果最多看到 k 個前導零，大約見過 2^k 個不同值

HLL 把這個想法擴展：
1. 用雜湊的前 p 位選一個「暫存器」（共 2^p 個）
2. 用剩餘位的最長前導零更新該暫存器
3. 最終用所有暫存器的調和平均數估計 cardinality

```
HLL sketch（p=4，即 16 個暫存器）：

輸入 hash(x) = 0b0001_0111_1010_...
前 4 位 = 0001 → 暫存器 1
剩餘前導零數 = 3 → 暫存器[1] = max(暫存器[1], 3)

最終估計：α * m^2 * harmonic_mean(2^-M[j])
  α：修正常數（由 m 決定）
  m：暫存器數量 = 2^p
  M[j]：暫存器 j 的值
```

### Rust HLL 概念實作

```rust
// 未編譯驗證（需加入 siphasher 依賴）
// src/statistics/hyperloglog.rs

use std::hash::{Hash, Hasher};
use std::collections::hash_map::DefaultHasher;

pub struct HyperLogLog {
    /// precision bits，m = 2^p 個暫存器
    p: u32,
    /// 暫存器陣列，每個儲存最大前導零 + 1
    registers: Vec<u8>,
}

impl HyperLogLog {
    /// p 建議 10–16（p=14 → 16384 暫存器，約 16KB，誤差 ~0.8%）
    pub fn new(p: u32) -> Self {
        assert!((4..=16).contains(&p));
        let m = 1usize << p;
        Self {
            p,
            registers: vec![0u8; m],
        }
    }

    fn hash_value<T: Hash>(&self, value: &T) -> u64 {
        let mut hasher = DefaultHasher::new();
        value.hash(&mut hasher);
        hasher.finish()
    }

    pub fn add<T: Hash>(&mut self, value: &T) {
        let h = self.hash_value(value);
        let m = 1u64 << self.p;
        // 前 p 位選暫存器
        let idx = (h >> (64 - self.p)) as usize;
        // 剩餘位計算前導零 + 1（至少為 1）
        let remaining = h << self.p;
        let leading_zeros = remaining.leading_zeros() + 1;
        self.registers[idx] = self.registers[idx].max(leading_zeros as u8);
    }

    pub fn estimate(&self) -> f64 {
        let m = self.registers.len() as f64;

        // 調和平均數
        let sum: f64 = self
            .registers
            .iter()
            .map(|&r| 2f64.powi(-(r as i32)))
            .sum();

        // 修正常數 alpha
        let alpha = match self.registers.len() {
            16 => 0.673,
            32 => 0.697,
            64 => 0.709,
            _ => 0.7213 / (1.0 + 1.079 / m),
        };

        let raw = alpha * m * m / sum;

        // 小基數修正（Linear Counting）
        if raw <= 2.5 * m {
            let zeros = self.registers.iter().filter(|&&r| r == 0).count() as f64;
            if zeros > 0.0 {
                return m * (m / zeros).ln();
            }
        }

        raw
    }

    /// 合併兩個 HLL（同 p）
    pub fn merge(&mut self, other: &HyperLogLog) {
        assert_eq!(self.p, other.p);
        for (a, &b) in self.registers.iter_mut().zip(other.registers.iter()) {
            *a = (*a).max(b);
        }
    }
}
```

PostgreSQL 的 `pg_stats.n_distinct` 負值表示「估計 distinct 比例」，大值表示「估計 distinct 絕對數」，底下就有類似 HLL 的近似統計在驅動。

## 獨立性假設的致命誤差

optimizer 估算多個 predicate 聯合的 selectivity 時，預設使用**獨立性假設**：

```
selectivity(A AND B) = selectivity(A) * selectivity(B)
```

如果 A 和 B 相關，這個公式就錯得離譜。

**典型案例**：電商訂單表

```sql
-- city = 'Taipei' AND postal_code = '100'
-- city 和 postal_code 高度相關！

selectivity(city='Taipei') = 0.05   -- 5%
selectivity(postal_code='100') = 0.01  -- 1%

獨立性假設: 0.05 * 0.01 = 0.05%
實際值: ~0.01%（因為 100 是台北的郵遞區號）
```

乘起來比實際值高 5 倍，optimizer 以為輸出很多列，可能選了錯的 join order。

**PostgreSQL 的解法**：`CREATE STATISTICS` 建立 multi-column statistics：

```sql
CREATE STATISTICS stat_city_postal ON city, postal_code FROM orders;
ANALYZE orders;
```

這會計算相關係數與 MCV（Most Common Values）組合，修正獨立性假設的誤差。

## 統計何時更新

| 系統       | 觸發時機                          | 指令                    |
|------------|-----------------------------------|-------------------------|
| PostgreSQL | autovacuum 達到 threshold        | `ANALYZE` / `VACUUM ANALYZE` |
| MySQL      | 達到 10% 更新量或手動觸發        | `ANALYZE TABLE`         |
| SQLite     | 手動                              | `ANALYZE`               |
| 我們的 DB  | 手動 or 定期背景任務              | `ANALYZE` command       |

**統計過舊的症狀**：
- 查詢計畫突然變慢（EXPLAIN 看 rows 估計離實際值差很大）
- 大量資料一次插入後（ETL、資料匯入）計畫仍用舊的統計
- PostgreSQL: `pg_stat_user_tables.n_live_tup` vs `reltuples` 差距大

## Cardinality 估錯如何毀計畫：完整案例

```
查詢：
  SELECT * FROM orders o
  JOIN customers c ON o.customer_id = c.id
  JOIN regions r ON c.region_id = r.id
  WHERE r.name = 'Asia' AND o.status = 'shipped'

實際 cardinality 流：
  regions WHERE name='Asia'  → 2 rows
  customers JOIN regions     → 50,000 rows
  orders JOIN customers      → 500,000 rows  ← 最終

統計過舊，估算錯：
  regions WHERE name='Asia'  → 100 rows（誤差 50x）
  customers JOIN regions     → 2,000,000 rows（誤差 40x）
  orders JOIN customers      → optimizer 以為輸出很多

後果：
  - optimizer 選 Hash Join（兩大表），應該選 Nested Loop（小驅動表）
  - 執行時間從 0.1s 變成 30s
```

```
正確計畫（統計準確）：
  regions (2 rows)
     ↓ NL-Join
  customers (50K rows，以 region_id 索引掃)
     ↓ Hash Join（build side: customers 50K）
  orders (lookup by customer_id)

錯誤計畫（統計過舊）：
  orders (估計 10M→ 選為 outer）
     ↓ Hash Join（build: regions 100 rows）
  結果超大中間集 join customers
```

## 我們的 DB 統計模組設計

```rust
// src/statistics/catalog_stats.rs（結構示意）
use crate::statistics::histogram::EquiDepthHistogram;
use crate::statistics::hyperloglog::HyperLogLog;

#[derive(Debug, Clone)]
pub struct ColumnStats {
    pub n_distinct: i64,      // 正值=絕對數，-1=全不同，< -1=-比例
    pub null_fraction: f64,
    pub avg_width: usize,     // 平均欄寬（bytes）
    pub most_common_vals: Vec<(String, f64)>,  // (值, 頻率)
    pub histogram: Option<EquiDepthHistogram>,
}

#[derive(Debug, Clone)]
pub struct TableStats {
    pub n_rows: u64,
    pub columns: std::collections::HashMap<String, ColumnStats>,
}

impl TableStats {
    /// 估算單一等值 predicate 的 selectivity
    pub fn selectivity_eq(&self, col: &str, val: &str) -> f64 {
        let Some(cs) = self.columns.get(col) else {
            return 1.0 / 100.0; // default guess
        };

        // 先查 MCV
        for (v, freq) in &cs.most_common_vals {
            if v == val {
                return *freq;
            }
        }

        // 不在 MCV 裡，用 n_distinct 估
        if cs.n_distinct > 0 {
            1.0 / cs.n_distinct as f64
        } else if cs.n_distinct < 0 {
            1.0 / (self.n_rows as f64 * cs.n_distinct.unsigned_abs() as f64)
        } else {
            0.01
        }
    }

    /// 估算 range predicate 的 selectivity（用 histogram）
    pub fn selectivity_range(&self, col: &str, lo: f64, hi: f64) -> f64 {
        let Some(cs) = self.columns.get(col) else {
            return 0.3333; // 1/3 是常見 default
        };
        cs.histogram
            .as_ref()
            .map(|h| h.selectivity_range(lo, hi))
            .unwrap_or(0.3333)
    }
}
```

## 對比表格

| 統計類型         | 空間     | 精確度     | 適用場景                         |
|------------------|----------|------------|----------------------------------|
| n_distinct（精確）| O(n)    | 100%       | 小表、離線分析                   |
| HyperLogLog      | 幾 KB   | ~98-99%    | 線上串流、大表                   |
| Equi-Width 直方圖| O(B)    | 偏斜時差   | 均勻分佈的數值欄                 |
| Equi-Depth 直方圖| O(B)    | 對偏斜好   | 大多數情境（PostgreSQL 預設）    |
| MCV list         | O(k)    | 高頻值精確 | 低基數欄位（status, type）       |
| Multi-col stats  | O(k^2)  | 捕捉相關性 | 有 correlation 的欄位組合        |

## 踩雷

1. **統計 lag 最難察覺**：生產環境 ETL 跑完大量資料進來，沒跑 ANALYZE，查詢慢到不行，排查半天才發現是統計問題。規則：大量資料異動後一定跑 ANALYZE。

2. **ANALYZE 抽樣比例影響精度**：PostgreSQL 的 `default_statistics_target`（預設 100）決定每欄的直方圖桶數與 MCV 個數。對高基數且頻繁過濾的欄位，把它調高：
   ```sql
   ALTER TABLE orders ALTER COLUMN amount SET STATISTICS 500;
   ```

3. **HLL 合併要求相同 precision**：兩個 p 不同的 HLL 無法合併。分散式系統要在初始化就統一 p 值。

4. **獨立性假設是預設，不是特例**：大多數 DB 在沒有 multi-column statistics 時都假設獨立性。熟悉你的 DB 何時打破這個假設（PostgreSQL 14+ 的 extended statistics、MySQL 的 statistics histogram）。

5. **HLL 估計值在小基數時不準**：少於 m（暫存器數）個 distinct 值時要切換到 Linear Counting。上面的實作有做修正，別忘了這個邊界條件。

## 進階延伸

**Count-Min Sketch**：HLL 估 distinct count，Count-Min Sketch 估每個值的頻率（比 MCV 更節省記憶體，適合高基數欄位的頻率估計）。

**Machine Learning Cardinality Estimation**：近年研究方向，用 learned model 取代傳統統計（論文：Learned Cardinalities by Kipf et al., 2018）。PostgreSQL 有研究分支 pg_plan_stats。

**Selectivity Graphs**：對 join cardinality 估算，有研究用圖結構建模 predicate 間的相關性，效果優於獨立性假設。

## 本章重點整理

- Equi-depth 直方圖對偏斜分佈比 equi-width 顯著更準，PostgreSQL 預設使用等深。
- HyperLogLog 以幾 KB 空間估計 cardinality，誤差 ~1-2%，可以合併，適合串流與分散式場景。
- 獨立性假設在有 correlation 的欄位組合下會嚴重低估 selectivity，需要 multi-column statistics 修正。
- Cardinality 估算誤差在 multi-join 中成倍放大，是 optimizer 選錯計畫的頭號原因。
- 統計更新時機比統計演算法本身更重要，大量資料變動後必須 ANALYZE。

## 自我檢核

- [ ] 說得出 equi-width 和 equi-depth 的差異，以及各自適合什麼分佈？
- [ ] 能解釋 HyperLogLog 為什麼能用幾個 bytes 估計上億個 distinct 值的 cardinality？
- [ ] 知道獨立性假設在什麼情況下失效，以及 PostgreSQL 如何修正？
- [ ] 能用 EXPLAIN ANALYZE 找出 cardinality 估算嚴重偏差的節點嗎？
- [ ] 我們的 `TableStats::selectivity_eq` 在 MCV miss 時退回哪個估算公式？

## 延伸閱讀

1. **CMU 15-445 Lecture "Query Optimization II" (2023)**  
   重點看 histogram 建構與 selectivity estimation 的數學推導，以及 multi-join cardinality 放大問題的案例分析。

2. **《Database Internals》第 5 章（Query Engine）**  
   Alex Petrov 對 statistics catalog 與 selectivity 的實作細節說明，是本章的直接延伸。

3. **HyperLogLog: the analysis of a near-optimal cardinality estimation algorithm** — Flajolet et al. (2007)  
   HLL 原始論文，Alpha 修正常數的推導來源，讀懂調和平均數那段就夠了。

4. **Estimating the Number of Distinct Values in a Database Column** — Haas & Stokes (1998)  
   傳統統計估算 distinct count 的方法，理解 PostgreSQL n_distinct 負值計算方式的背景。

5. **How Good Are Query Optimizers, Really?** — Leis et al. (VLDB 2015)  
   實測 PostgreSQL optimizer 在 JOB benchmark 上的 cardinality 估算誤差，直接看圖表就能感受 multi-join 誤差放大有多嚴重。

---

銜接：統計是 optimizer 的眼睛，但 optimizer 的手是索引——光估準還不夠，得選對索引結構。B+tree 之外還有哪些索引適合不同場景？

→ [下一章：Ch 35 進階索引](./35-advanced-indexes.md)
