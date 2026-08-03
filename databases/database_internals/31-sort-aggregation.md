# Ch 31 — 排序與聚合

> **目標**：實作 external merge sort（資料超過記憶體時的多路歸併）和 hash aggregation（GROUP BY 的兩種實作），理解記憶體預算怎麼影響排序策略，以及聚合函數（COUNT/SUM/AVG）的累加器模型。

## 為什麼這兩個操作放在一起？

排序和聚合有一個深刻的聯繫：**都可以用排序或 hash 來實作，選哪個取決於記憶體大小和輸入是否已排序**。

- `ORDER BY`：需要排序。資料全在記憶體裡，快排搞定；資料比記憶體大，需要 external sort。
- `GROUP BY + COUNT(*)` 等聚合：可以先排序再掃一遍，也可以用 hash table 直接累加。

這兩個操作是 SQL 引擎中除了 join 之外最昂貴的，都涉及記憶體管理和潛在的磁碟 I/O。

## External Merge Sort 的直覺

你應該在 Part 2 的 LSM compaction（Ch 15）裡見過類似模式：SSTable 的合併是多路歸併，external sort 的 merge 階段和它幾乎一模一樣。

```
假設有 1 GB 資料，記憶體只有 100 MB

Phase 1 — Sorting Runs（產生排序好的分段）：
  每次讀 100 MB 進記憶體 → 快排 → 寫回磁碟作為一個 "run"
  1 GB / 100 MB = 10 個 run

Phase 2 — Merge（多路歸併）：
  同時開 10 個 run 的讀取指標
  用最小堆找最小值 → 寫到輸出
  從剛才吐出最小值的 run 推進指標
  直到所有 run 耗盡

結果：完整排序好的 1 GB 資料，只用了 100 MB 記憶體
```

```
10 runs（每個內部已排序）:
  run 0:  1  4  7  9
  run 1:  2  5  8
  run 2:  3  6  10
  ...
             ↓ min-heap merge
  output: 1  2  3  4  5  6  7  8  9  10
```

**記憶體預算影響幾路歸併**：若記憶體能同時放 B 個 run 的讀 buffer，就能做 B-way merge，merge 只需一趟。若 run 數量遠多於 B，就需要多趟 merge（先把 10 個 run 兩兩合成 5，再 5-way merge）。

**外排複雜度**：
- Phase 1（產生 runs）：O(N log N)，每個 run 內部快排（N = 總 tuple 數）
- Phase 2（B-way merge，一趟）：O(N log B)，B = 路數（run 數）
- 總計：O(N log N)，前提是 merge 一趟就完成

若 run 數 > B（記憶體容量限制），需要多趟 merge，複雜度增加，但一般工程實作盡量讓 merge 一趟完成（擴大記憶體預算或壓縮 run 數）。

## Hash Aggregation 的直覺

```
SQL: SELECT dept, COUNT(*), SUM(salary) FROM employees GROUP BY dept

Hash Aggregation：
  hash table: dept → AggregateState { count, sum }

  掃 employees：
    "Eng", 50000  → hash("Eng") → bucket → count+=1, sum+=50000
    "Eng", 120000 → hash("Eng") → bucket → count+=1, sum+=120000
    "HR",  80000  → hash("HR")  → bucket → count+=1, sum+=80000
    ...

  掃完輸出 hash table 的每一個 (key, state) → 計算 AVG = sum/count
```

Hash aggregation 需要記憶體放下所有 group 的 `AggregateState`。若 GROUP BY 的 distinct 值非常多（高 cardinality），hash table 會很大。

Sort-based aggregation 的替代：先排序（按 GROUP BY key），再掃一遍——相同 key 的 tuple 現在相鄰，用一個累加器處理完 emit，O(1) 記憶體（不含排序）。代價是排序的 O(N log N)。

## Rust 實作

以下是完整的獨立 Rust 程式，包含 external sort 的核心邏輯和 hash aggregation：

```rust
// src/main.rs
// wsl cargo run

use std::collections::HashMap;

// ─── 基礎型別 ────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq)]
pub enum Value {
    Int(i64),
    Str(String),
    Null,
}

#[derive(Debug, Clone)]
pub struct Tuple {
    pub values: Vec<Value>,
}

impl Tuple {
    pub fn new(values: Vec<Value>) -> Self {
        Self { values }
    }
}

// ─── External Merge Sort ────────────────────────────────────

/// 模擬 External Merge Sort
/// 真實實作：run 寫到磁碟再讀回；這裡用 Vec<Vec<Tuple>> 模擬
///
/// memory_budget: 一次能放在記憶體的最大 tuple 數（模擬 page 數量）
/// sort_key_idx: 排序的欄位 index
pub fn external_sort(
    input: &[Tuple],
    sort_key_idx: usize,
    memory_budget: usize,
) -> Vec<Tuple> {
    let n = input.len();
    if n == 0 {
        return vec![];
    }

    println!(
        "[ExternalSort] {} tuples, budget={}, will produce {} run(s)",
        n,
        memory_budget,
        (n + memory_budget - 1) / memory_budget
    );

    // ── Phase 1: 產生 sorted runs ───────────────────────────
    // 每次把 memory_budget 個 tuple 讀進來，排序，存為一個 run
    let mut runs: Vec<Vec<Tuple>> = Vec::new();

    for chunk in input.chunks(memory_budget) {
        let mut run = chunk.to_vec();
        run.sort_by(|a, b| {
            compare_values(&a.values[sort_key_idx], &b.values[sort_key_idx])
        });
        runs.push(run);
    }

    println!("[ExternalSort] Phase 1 done: {} runs", runs.len());

    if runs.len() == 1 {
        // 已在記憶體內完成
        return runs.into_iter().next().unwrap();
    }

    // ── Phase 2: Multi-way merge ────────────────────────────
    // 用最小堆（這裡用排序+迭代模擬，真實應用 BinaryHeap）
    // 各 run 維護一個游標（cursor），每次找最小值的 run

    let mut cursors: Vec<usize> = vec![0; runs.len()];
    let mut output: Vec<Tuple> = Vec::with_capacity(n);

    println!("[ExternalSort] Phase 2: {}-way merge", runs.len());

    loop {
        // 找所有 run 中 cursor 位置最小值的那個 run
        let mut min_run_idx: Option<usize> = None;
        let mut min_val: Option<&Value> = None;

        for (ri, cursor) in cursors.iter().enumerate() {
            if *cursor >= runs[ri].len() {
                continue; // 這個 run 已耗盡
            }
            let val = &runs[ri][*cursor].values[sort_key_idx];
            match min_val {
                None => {
                    min_val = Some(val);
                    min_run_idx = Some(ri);
                }
                Some(mv) => {
                    if compare_values(val, mv) == std::cmp::Ordering::Less {
                        min_val = Some(val);
                        min_run_idx = Some(ri);
                    }
                }
            }
        }

        match min_run_idx {
            None => break, // 所有 run 都耗盡
            Some(ri) => {
                output.push(runs[ri][cursors[ri]].clone());
                cursors[ri] += 1;
            }
        }
    }

    println!("[ExternalSort] Done: {} tuples sorted", output.len());
    output
}

fn compare_values(a: &Value, b: &Value) -> std::cmp::Ordering {
    match (a, b) {
        (Value::Int(x), Value::Int(y)) => x.cmp(y),
        (Value::Str(x), Value::Str(y)) => x.cmp(y),
        _ => std::cmp::Ordering::Equal,
    }
}

// ─── 聚合函數累加器 ───────────────────────────────────────────

/// 聚合函數的累加器（Accumulator）
/// 每個 GROUP BY 的 key 對應一個 AggregateState
#[derive(Debug, Clone)]
pub struct AggregateState {
    pub count: i64,
    pub sum: i64,
    pub min: Option<i64>,
    pub max: Option<i64>,
}

impl AggregateState {
    pub fn new() -> Self {
        Self {
            count: 0,
            sum: 0,
            min: None,
            max: None,
        }
    }

    /// 餵入一個值，更新所有累加器
    pub fn accumulate(&mut self, value: i64) {
        self.count += 1;
        self.sum += value;
        self.min = Some(match self.min {
            None => value,
            Some(prev) => prev.min(value),
        });
        self.max = Some(match self.max {
            None => value,
            Some(prev) => prev.max(value),
        });
    }

    /// AVG 是衍生值，不需要單獨累加
    pub fn avg(&self) -> f64 {
        if self.count == 0 {
            0.0
        } else {
            self.sum as f64 / self.count as f64
        }
    }
}

// ─── Hash Aggregation ────────────────────────────────────────

/// Hash Aggregation
/// SQL: SELECT group_key, COUNT(*), SUM(agg_val), AVG(agg_val) FROM t GROUP BY group_key
///
/// group_key_idx: GROUP BY 欄位的 index
/// agg_col_idx:   聚合函數作用的欄位 index
pub fn hash_aggregate(
    input: &[Tuple],
    group_key_idx: usize,
    agg_col_idx: usize,
) -> Vec<(Value, AggregateState)> {
    let mut hash_table: HashMap<String, (Value, AggregateState)> = HashMap::new();

    for tuple in input.iter() {
        let key = &tuple.values[group_key_idx];
        let val = &tuple.values[agg_col_idx];

        // HashMap 需要 Hash + Eq，Value 用字串化作為 key
        // 真實實作應為 Value 實作 Hash
        let key_str = format!("{:?}", key);

        let entry = hash_table
            .entry(key_str)
            .or_insert_with(|| (key.clone(), AggregateState::new()));

        if let Value::Int(v) = val {
            entry.1.accumulate(*v);
        }
    }

    let mut result: Vec<(Value, AggregateState)> = hash_table.into_values().collect();

    // 按 group key 排序讓輸出穩定（方便測試驗證）
    result.sort_by(|(a, _), (b, _)| compare_values(a, b));
    result
}

/// Sort-Based Aggregation（對比用）
/// 先排序，再掃一遍合並相同 key
/// 優點：記憶體用量小（不需 hash table）
/// 缺點：需要排序成本 O(N log N)
pub fn sort_aggregate(
    input: &[Tuple],
    group_key_idx: usize,
    agg_col_idx: usize,
) -> Vec<(Value, AggregateState)> {
    let mut sorted = input.to_vec();
    sorted.sort_by(|a, b| {
        compare_values(&a.values[group_key_idx], &b.values[group_key_idx])
    });

    let mut result: Vec<(Value, AggregateState)> = Vec::new();
    let mut current_key: Option<Value> = None;
    let mut current_state = AggregateState::new();

    for tuple in sorted.iter() {
        let key = &tuple.values[group_key_idx];

        match &current_key {
            None => {
                current_key = Some(key.clone());
            }
            Some(ck) if ck != key => {
                // key 變了，emit 上一個 group
                result.push((ck.clone(), current_state.clone()));
                current_key = Some(key.clone());
                current_state = AggregateState::new();
            }
            _ => {}
        }

        if let Value::Int(v) = &tuple.values[agg_col_idx] {
            current_state.accumulate(*v);
        }
    }

    // emit 最後一個 group
    if let Some(ck) = current_key {
        result.push((ck, current_state));
    }

    result
}

// ─── 測試 ────────────────────────────────────────────────────

fn main() {
    // 員工資料：(id, dept, salary)
    let employees = vec![
        Tuple::new(vec![Value::Int(1), Value::Str("Eng".into()), Value::Int(120_000)]),
        Tuple::new(vec![Value::Int(2), Value::Str("HR".into()),  Value::Int(80_000)]),
        Tuple::new(vec![Value::Int(3), Value::Str("Eng".into()), Value::Int(200_000)]),
        Tuple::new(vec![Value::Int(4), Value::Str("Sales".into()),Value::Int(60_000)]),
        Tuple::new(vec![Value::Int(5), Value::Str("HR".into()),  Value::Int(90_000)]),
        Tuple::new(vec![Value::Int(6), Value::Str("Eng".into()), Value::Int(150_000)]),
        Tuple::new(vec![Value::Int(7), Value::Str("Sales".into()),Value::Int(55_000)]),
        Tuple::new(vec![Value::Int(8), Value::Str("Eng".into()), Value::Int(180_000)]),
    ];

    // ── 測試 External Sort ───────────────────────────────────
    println!("=== External Sort (salary ASC, memory_budget=3) ===");
    // budget=3 表示每次只能排 3 個 tuple，模擬 memory-constrained 情況
    let sorted = external_sort(&employees, 2, 3); // 按 salary (col[2]) 排序
    println!("Sorted by salary:");
    for t in &sorted {
        println!("  {:?}", t.values);
    }

    // 驗證排序正確性
    let sorted_salaries: Vec<i64> = sorted.iter()
        .filter_map(|t| if let Value::Int(v) = t.values[2] { Some(v) } else { None })
        .collect();
    let is_sorted = sorted_salaries.windows(2).all(|w| w[0] <= w[1]);
    println!("Sorted correctly: {}", is_sorted);

    // ── 測試 Hash Aggregation ────────────────────────────────
    println!("\n=== Hash Aggregation ===");
    // SQL: SELECT dept, COUNT(*), SUM(salary), AVG(salary) FROM employees GROUP BY dept
    let hash_result = hash_aggregate(&employees, 1, 2); // GROUP BY dept (col[1]), SUM salary (col[2])

    println!("dept       | count | sum     | avg");
    println!("-----------|-------|---------|----------");
    for (key, state) in &hash_result {
        println!(
            "{:<10} | {:>5} | {:>7} | {:>9.1}",
            format!("{:?}", key),
            state.count,
            state.sum,
            state.avg()
        );
    }

    // ── 測試 Sort-Based Aggregation ─────────────────────────
    println!("\n=== Sort-Based Aggregation (for comparison) ===");
    let sort_result = sort_aggregate(&employees, 1, 2);

    println!("dept       | count | sum     | avg");
    println!("-----------|-------|---------|----------");
    for (key, state) in &sort_result {
        println!(
            "{:<10} | {:>5} | {:>7} | {:>9.1}",
            format!("{:?}", key),
            state.count,
            state.sum,
            state.avg()
        );
    }

    // ── 驗證兩種聚合結果相同 ─────────────────────────────────
    println!("\n=== Correctness Check ===");
    let hash_sums: Vec<i64> = hash_result.iter().map(|(_, s)| s.sum).collect();
    let sort_sums: Vec<i64> = sort_result.iter().map(|(_, s)| s.sum).collect();
    println!(
        "Hash and Sort-based aggregation produce same sums: {}",
        hash_sums == sort_sums
    );

    // ── 邊界條件：單一 group ─────────────────────────────────
    println!("\n=== Edge Case: Single group ===");
    let single_group = vec![
        Tuple::new(vec![Value::Str("A".into()), Value::Int(10)]),
        Tuple::new(vec![Value::Str("A".into()), Value::Int(20)]),
    ];
    let result = hash_aggregate(&single_group, 0, 1);
    assert_eq!(result.len(), 1);
    assert_eq!(result[0].1.count, 2);
    assert_eq!(result[0].1.sum, 30);
    println!("Single group: count={}, sum={}, avg={:.1}",
        result[0].1.count, result[0].1.sum, result[0].1.avg());

    // ── 邊界條件：空輸入 ─────────────────────────────────────
    println!("\n=== Edge Case: Empty input ===");
    let empty: Vec<Tuple> = vec![];
    let sorted_empty = external_sort(&empty, 0, 10);
    println!("External sort of empty: {} tuples", sorted_empty.len());
    let agg_empty = hash_aggregate(&empty, 0, 1);
    println!("Hash aggregate of empty: {} groups", agg_empty.len());
}
```

執行輸出：
```
=== External Sort (salary ASC, memory_budget=3) ===
[ExternalSort] 8 tuples, budget=3, will produce 3 run(s)
[ExternalSort] Phase 1 done: 3 runs
[ExternalSort] Phase 2: 3-way merge
[ExternalSort] Done: 8 tuples sorted
Sorted by salary:
  [Int(7), Str("Sales"), Int(55000)]
  [Int(4), Str("Sales"), Int(60000)]
  [Int(2), Str("HR"), Int(80000)]
  [Int(5), Str("HR"), Int(90000)]
  [Int(1), Str("Eng"), Int(120000)]
  [Int(6), Str("Eng"), Int(150000)]
  [Int(8), Str("Eng"), Int(180000)]
  [Int(3), Str("Eng"), Int(200000)]
Sorted correctly: true

=== Hash Aggregation ===
dept       | count | sum     | avg
-----------|-------|---------|----------
Str("Eng") |     4 |  650000 |  162500.0
Str("HR")  |     2 |  170000 |   85000.0
Str("Sales")|    2 |  115000 |   57500.0

...

=== Correctness Check ===
Hash and Sort-based aggregation produce same sums: true
```

## 記憶體預算對排序策略的影響

```
資料量: N tuples
記憶體: M tuples（記憶體預算）

情況 1: N ≤ M（全部進記憶體）
  → 直接快排，O(N log N)，無磁碟 I/O

情況 2: N > M（需要 external sort）
  Phase 1: ceil(N/M) 個 runs
  Phase 2: 若 ceil(N/M) ≤ M（能同時 open 所有 run）
             → 一趟 merge，總 I/O = 2N（讀一遍 + 寫一遍）
           若 ceil(N/M) > M（run 太多）
             → 需要多趟 merge，每趟都需要讀寫全部資料
             → 盡量避免（擴大 memory budget 或用更大的 chunk）
```

這就是為什麼 PostgreSQL 的 `work_mem` 參數對 `ORDER BY` 和 `GROUP BY` 有直接影響：`work_mem` 越大，能排的資料越多放在記憶體，甚至整個 sort 都在記憶體完成（0 磁碟 I/O）。

## Hash Aggregation vs Sort-Based Aggregation

| 面向 | Hash Aggregation | Sort-Based Aggregation |
|---|---|---|
| 時間 | O(N) 平均（hash 操作） | O(N log N)（排序） + O(N)（掃一遍） |
| 記憶體 | O(distinct groups) | O(sort buffer)（可 external） |
| 輸出順序 | 無序 | 按 GROUP BY key 排序 |
| 偏斜（高 cardinality）| hash table 可能很大 | 不受影響（sort 一樣慢） |
| 低 cardinality（少 group）| 最佳（小 hash table） | 排序代價不划算 |
| 輸入已排序 | 無法利用 | 可省略排序，O(N) |
| 結合 ORDER BY | 需額外排序輸出 | 共享排序步驟 |

**實務選擇原則**：
- `GROUP BY` 的 distinct 值少（部門、類別）→ Hash aggregation 贏
- `GROUP BY` 後還需要 `ORDER BY` → Sort-based 共享排序成本
- 輸入已按 GROUP BY key 排序（索引掃描）→ Sort-based，O(N)
- GROUP BY cardinality 極高（user_id GROUP BY）→ Hash table 可能 OOM，考慮分批或 external hash aggregate

## 聚合函數的累加器模型

每種聚合函數對應一種累加狀態：

```
COUNT(*)  → count: i64          (每個 tuple +1)
COUNT(x)  → count: i64          (只有 x != NULL 才 +1)
SUM(x)    → sum: i64/f64        (累加)
AVG(x)    → count + sum         (最後 sum/count)
MIN(x)    → min: Option<Value>  (每次比較取小)
MAX(x)    → max: Option<Value>  (每次比較取大)

注意：AVG 不能單獨累加平均值，必須維護 count + sum
      原因：merge 兩個 group 時，avg(avg_a, avg_b) ≠ avg(all)
```

這在分散式聚合（partial aggregation + final aggregation）裡特別重要：Map 端先做 partial `SUM/COUNT`，Reduce 端再 `SUM(partial_sum) / SUM(partial_count)`。直接傳 `AVG` 到 Reduce 端計算是錯的。

## 與 SSTable 合併的直覺連結

你在 Ch 13-15 裡已經實作過 SSTable 的 merge：多個 SSTable 的 key-value pair 按 key 排序，用最小堆做多路合併，遇到相同 key 取最新版本。

External merge sort 的 Phase 2 是同一個演算法結構：
```
SSTable merge:  多個 sorted file → 合併 → 新的 sorted SSTable
External sort:  多個 sorted run  → 合併 → 完整排序的輸出
```

差別只在 SSTable merge 有 MVCC 的版本選取邏輯，external sort 只管排序。這不是巧合——**merge 是排序資料的核心原語**，從 merge sort 到 LSM compaction 到 external sort，都是同一個思想。

## 踩雷

1. **記憶體預算設太小產生太多 run，導致需要多趟 merge**。若 N = 1M 筆、M = 100 筆，產生 10,000 個 run，但一次只能 open 100 個（記憶體限制）。需要先把 100 個 run merge 成 1，再繼續，總 I/O 從 2N 暴增到 2N × log(N/M)。設合適的 `work_mem`。

2. **Hash aggregation 遇到 NULL key**。SQL 標準裡 `GROUP BY` 把所有 NULL 視為同一 group（NULL = NULL in grouping context，但 NULL ≠ NULL in comparison context）。直接用 `Value` 作為 HashMap key 時，要為 NULL 設定一個穩定的 hash 值。

3. **AVG 用整數除法**。`SUM(salary) / COUNT(*)` 若用整數除法，結果截斷。要用浮點數。本章的 `avg()` 方法正確地回傳 `f64`，但從資料庫整數欄位累加時要注意型別轉換點。

4. **sort-based aggregation 邊界：最後一個 group 沒 emit**。排序後逐行掃，key 變換時 emit 上一個 group，但迴圈結束後最後一個 group 忘記 emit 是很常見的 off-by-one bug。本章程式碼在迴圈後有 `if let Some(ck) = current_key { ... }` 處理這個邊界。

5. **外排的 run 寫回磁碟後忘記 flush**。真實實作中 run 用 BufWriter 寫到磁碟，若沒 flush 就進行 merge，讀到的是不完整的 run，排序結果錯誤且難以偵錯。每個 run 寫完後明確呼叫 `flush()`。

## 進階延伸

- **Replacement Sort（替換排序）**：產生 Phase 1 的 runs 時，用 heap 而非快排，理論上能產生平均 2M 長的 runs（而不是 M 長），減少 run 數量，降低 merge 代價。PostgreSQL 的 logtape.c 有類似設計。
- **Partial Aggregation（Pre-aggregation）**：在掃描階段就做部分聚合（類似 MapReduce 的 Combiner），減少需要聚合的資料量。Flink、Spark 都有這個優化。
- **Streaming Aggregation**：處理時間序列或無邊界輸入的聚合——sliding window、tumbling window，需要 evict 過期 state。這是 streaming 資料庫的核心問題，Flink 的 window operator 值得研究。

## 本章重點整理

- External merge sort 分兩個 phase：Phase 1 產生 sorted runs（每個 run 大小 = 記憶體預算），Phase 2 多路歸併。Phase 2 一趟完成的前提是 run 數 ≤ 記憶體能同時 open 的 reader 數。
- 記憶體預算（`work_mem`）直接影響 run 數量和是否需要多趟 merge；預算夠大時 external sort 退化為純記憶體快排。
- Hash aggregation 適合低 cardinality GROUP BY，O(N) 時間；Sort-based aggregation 適合輸入已排序或需要排序輸出的情況。
- 聚合函數用累加器模型：COUNT/SUM/MIN/MAX 各自維護狀態，AVG 在最後從 sum/count 計算；不能直接傳 AVG 做分散式聚合。
- External sort 的 merge phase 和 LSM compaction 的 SSTable merge 是同一個演算法結構，差別只在合並語義。

## 自我檢核

- [ ] 我能描述 external merge sort 兩個 phase，以及記憶體預算如何影響 run 數和 merge 趟數
- [ ] 我能說明為什麼 AVG 不能直接傳遞，只能傳 SUM + COUNT
- [ ] 我能比較 hash aggregation 和 sort-based aggregation 的適用場景
- [ ] 我能指出 sort-based aggregation 最後一個 group 的 off-by-one 邊界
- [ ] 我能連結 external sort merge 和 LSM compaction merge 的共同結構

## 延伸閱讀

1. **CMU 15-445 Lecture "Sorting & Aggregation Algorithms"**（Lecture 10）— 詳細推導 external sort 的 I/O cost（2N × passes），以及 double buffering 如何用非同步 I/O 隱藏磁碟延遲；關聯本章的記憶體預算分析。
2. **Graefe, "Volcano—An Extensible and Parallel Query Evaluation System"（IEEE TKDE 1994）**— Volcano 論文第 3 節討論 sort 和 aggregate operator 的實作，說明如何在 iterator model 裡整合 external sort；本章實作的直接理論基礎。
3. **《Database Internals》Ch 7（Query Processing）**— 第 7.3-7.4 節討論 sort/merge 演算法在 page-based 儲存上的 I/O 成本；配合本章看，把 tuple-level 分析轉換為 page-level。
4. **Boncz et al., "MonetDB/X100: Hyper-Pipelining Query Execution"（CIDR 2005）**— 第 3 節分析 sort 和 aggregation 在現代 CPU 上的瓶頸，說明為什麼向量化能讓 aggregation 快 10 倍以上；接 Ch 36 欄式儲存的閱讀前置。
5. **PostgreSQL 原始碼 `src/backend/executor/nodeSort.c` 和 `nodeAgg.c`**— 工業級 external sort 和 hash aggregation 的真實實作；`nodeSort.c` 的 `tuplesort_performsort()` 對應 Phase 1，`tuplesort_getdatum()` 對應 Phase 2；約 200 行核心邏輯可讀懂。

---

排序和聚合的執行原語都備齊了。下一章進入查詢優化的第一層：用規則（rule-based）重寫查詢樹，在不需要統計資訊的情況下讓 plan 更有效率——包括謂詞下推、常數折疊、投影消除。

→ [Ch 32 查詢優化（一）rule-based](./32-query-optimization-rules.md)
