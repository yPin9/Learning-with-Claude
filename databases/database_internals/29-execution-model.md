# Ch 29 — 執行模型：Volcano vs Vectorized

> **目標**：實作 Volcano iterator model（open/next/close 介面、pull-based、一次一 tuple），理解它的優缺點，再看 vectorized execution 如何攤提虛擬呼叫開銷與利用 SIMD/cache，並用 Rust 跑一個完整的 scan→filter 管線。

## 為什麼執行模型很重要？

physical plan 是一棵樹，但樹不會自己動。你需要一套**執行模型（execution model）**——一組定義「誰拉誰、什麼時候把資料交給誰」的規則。

選錯執行模型，效能差十倍不是誇張：
- 純 row-at-a-time 在百萬列上，一個 `SELECT COUNT(*) FROM t` 可能呼叫 `next()` 一百萬次，每次都是虛擬呼叫 dispatch。
- vectorized 同樣的查詢，呼叫次數降到幾千次，剩下的時間 CPU 在跑 tight SIMD loop。

這章是查詢層的核心，前面所有東西都為了讓 executor 有東西可以跑。

## 先建立直覺

### Volcano / Iterator Model（拉模型）

```
Consumer（上層）
    │
    │ next()     ← "給我下一筆"
    ▼
 Filter
    │
    │ next()
    ▼
 SeqScan
    │
    └── 讀 heap page，傳 Row 回去

控制流：上往下傳「要資料」，資料從下往上流
```

這是一個純 **pull-based** 架構：root operator 呼叫 `next()`，這個呼叫沿著樹往下傳播，每個 operator 被拉一次就吐一個 tuple 出來。

### Vectorized Execution（向量化）

```
Consumer
    │
    │ next_batch()     ← "給我下一批（例如 1024 筆）"
    ▼
 Filter
    │  (批次篩選，SIMD 比較)
    │ next_batch()
    ▼
 SeqScan
    └── 一次讀多個 page，批量傳 Column 回去
```

關鍵差異：一次傳 **一批 tuple（vector of columns）**，攤提虛擬呼叫開銷，讓 CPU 能用 SIMD 做批次比較。

### Push-Based（推模型）

```
SeqScan ──push──▶ Filter ──push──▶ Aggregate ──push──▶ Output
```

資料從 source 主動推給下游，不等上層呼叫。實作上通常是 producer 呼叫 consumer 的 callback 函式。優點是方便 pipeline fusion（編譯成一個大迴圈）；缺點是 backpressure 難做。

本章實作 Volcano，把 vectorized 和 push-based 作為概念對比。

## Volcano Iterator Model 的 open/next/close 介面

Volcano 論文（Graefe, 1994）定義三個操作：

| 方法 | 做什麼 |
|---|---|
| `open()` | 初始化 operator：分配記憶體、開檔、向子節點呼叫 `open()` |
| `next()` | 回傳下一個 tuple，若無更多資料回傳 `None` |
| `close()` | 釋放資源，向子節點呼叫 `close()` |

每個 operator 只關心自己的邏輯，不管上下游實作。這是純粹的介面分離。

```
open()  → close()  之間可以任意次呼叫 next()
多次 open() 不一定合法（operator 可能不支援 rewind）
```

## 用 Rust Trait 實作 Executor

這是本章的核心程式碼——能在 WSL 上 `cargo run` 跑通。

先建專案結構，以下是一個完整的獨立範例：

```rust
// src/main.rs
// cargo run  (WSL: wsl cargo run)

use std::collections::HashMap;

// ─── 資料模型 ───────────────────────────────────────────────

/// 欄位值（簡化，只支援整數與字串）
#[derive(Debug, Clone, PartialEq)]
pub enum Value {
    Int(i64),
    Str(String),
    Null,
}

/// 一個 tuple（row）= 有序的欄位值
#[derive(Debug, Clone)]
pub struct Tuple {
    pub values: Vec<Value>,
}

impl Tuple {
    pub fn new(values: Vec<Value>) -> Self {
        Self { values }
    }
}

// ─── Executor Trait ─────────────────────────────────────────

/// Volcano iterator 介面
/// open()/close() 在真實實作中可用 Drop trait 處理，
/// 這裡明確列出方便理解
pub trait Executor {
    fn open(&mut self);
    fn next(&mut self) -> Option<Tuple>;
    fn close(&mut self);
}

// ─── SeqScan：模擬從 heap 讀資料 ─────────────────────────────

pub struct SeqScan {
    /// 內存中的假資料，模擬 heap file
    data: Vec<Tuple>,
    cursor: usize,
    opened: bool,
}

impl SeqScan {
    pub fn new(data: Vec<Tuple>) -> Self {
        Self { data, cursor: 0, opened: false }
    }
}

impl Executor for SeqScan {
    fn open(&mut self) {
        self.cursor = 0;
        self.opened = true;
        println!("[SeqScan] open: {} rows available", self.data.len());
    }

    fn next(&mut self) -> Option<Tuple> {
        assert!(self.opened, "must call open() before next()");
        if self.cursor < self.data.len() {
            let t = self.data[self.cursor].clone();
            self.cursor += 1;
            Some(t)
        } else {
            None
        }
    }

    fn close(&mut self) {
        self.opened = false;
        println!("[SeqScan] close: read {} rows", self.cursor);
    }
}

// ─── Filter：謂詞評估 ────────────────────────────────────────

/// 簡化謂詞：比較某欄位（by index）與常數
pub enum Predicate {
    /// values[col_idx] > threshold
    GreaterThan { col_idx: usize, threshold: i64 },
    /// values[col_idx] == value
    Equal { col_idx: usize, value: Value },
}

impl Predicate {
    fn evaluate(&self, tuple: &Tuple) -> bool {
        match self {
            Predicate::GreaterThan { col_idx, threshold } => {
                match &tuple.values[*col_idx] {
                    Value::Int(v) => v > threshold,
                    _ => false,
                }
            }
            Predicate::Equal { col_idx, value } => {
                &tuple.values[*col_idx] == value
            }
        }
    }
}

pub struct Filter {
    input: Box<dyn Executor>,
    predicate: Predicate,
    passed: usize,
    total: usize,
}

impl Filter {
    pub fn new(input: Box<dyn Executor>, predicate: Predicate) -> Self {
        Self { input, predicate, passed: 0, total: 0 }
    }
}

impl Executor for Filter {
    fn open(&mut self) {
        self.input.open();
        self.passed = 0;
        self.total = 0;
        println!("[Filter] open");
    }

    fn next(&mut self) -> Option<Tuple> {
        // 不斷向下游拉 tuple，直到找到符合謂詞的
        loop {
            match self.input.next() {
                None => return None,
                Some(t) => {
                    self.total += 1;
                    if self.predicate.evaluate(&t) {
                        self.passed += 1;
                        return Some(t);
                    }
                    // 不符合就繼續拉下一筆（丟棄）
                }
            }
        }
    }

    fn close(&mut self) {
        self.input.close();
        println!("[Filter] close: {}/{} rows passed", self.passed, self.total);
    }
}

// ─── Projection：選取欄位 ────────────────────────────────────

pub struct Projection {
    input: Box<dyn Executor>,
    /// 要輸出的欄位 indices
    col_indices: Vec<usize>,
}

impl Projection {
    pub fn new(input: Box<dyn Executor>, col_indices: Vec<usize>) -> Self {
        Self { input, col_indices }
    }
}

impl Executor for Projection {
    fn open(&mut self) {
        self.input.open();
        println!("[Projection] open: projecting cols {:?}", self.col_indices);
    }

    fn next(&mut self) -> Option<Tuple> {
        self.input.next().map(|t| {
            let values = self.col_indices
                .iter()
                .map(|&i| t.values[i].clone())
                .collect();
            Tuple::new(values)
        })
    }

    fn close(&mut self) {
        self.input.close();
    }
}

// ─── Limit ──────────────────────────────────────────────────

pub struct Limit {
    input: Box<dyn Executor>,
    max: usize,
    count: usize,
}

impl Limit {
    pub fn new(input: Box<dyn Executor>, max: usize) -> Self {
        Self { input, max, count: 0 }
    }
}

impl Executor for Limit {
    fn open(&mut self) {
        self.input.open();
        self.count = 0;
    }

    fn next(&mut self) -> Option<Tuple> {
        if self.count >= self.max {
            return None;
        }
        let t = self.input.next()?;
        self.count += 1;
        Some(t)
    }

    fn close(&mut self) {
        self.input.close();
    }
}

// ─── 組裝並執行 pipeline ─────────────────────────────────────

fn main() {
    // 模擬一張 employees 表，欄位：[id: Int, salary: Int, dept: Str]
    let data = vec![
        Tuple::new(vec![Value::Int(1), Value::Int(50_000), Value::Str("Eng".into())]),
        Tuple::new(vec![Value::Int(2), Value::Int(120_000), Value::Str("Eng".into())]),
        Tuple::new(vec![Value::Int(3), Value::Int(80_000), Value::Str("HR".into())]),
        Tuple::new(vec![Value::Int(4), Value::Int(200_000), Value::Str("Eng".into())]),
        Tuple::new(vec![Value::Int(5), Value::Int(30_000), Value::Str("Sales".into())]),
        Tuple::new(vec![Value::Int(6), Value::Int(150_000), Value::Str("Eng".into())]),
    ];

    // SQL: SELECT id, salary FROM employees WHERE salary > 100000 LIMIT 3
    //
    // Physical plan:
    //   Limit(3)
    //     Projection([0, 1])         -- id, salary
    //       Filter(salary > 100000)  -- col[1] > 100000
    //         SeqScan(employees)

    let scan = Box::new(SeqScan::new(data));
    let filter = Box::new(Filter::new(
        scan,
        Predicate::GreaterThan { col_idx: 1, threshold: 100_000 },
    ));
    let project = Box::new(Projection::new(filter, vec![0, 1])); // id, salary
    let mut limit = Limit::new(project, 3);

    println!("=== Execute: SELECT id, salary FROM employees WHERE salary > 100000 LIMIT 3 ===\n");

    limit.open();

    println!("\n--- Results ---");
    while let Some(tuple) = limit.next() {
        println!("  {:?}", tuple.values);
    }

    println!();
    limit.close();
}
```

執行輸出：
```
=== Execute: SELECT id, salary FROM employees WHERE salary > 100000 LIMIT 3 ===

[SeqScan] open: 6 rows available
[Filter] open
[Projection] open: projecting cols [0, 1]

--- Results ---
  [Int(2), Int(120000)]
  [Int(4), Int(200000)]
  [Int(6), Int(150000)]

[SeqScan] close: read 6 rows
[Filter] close: 3/6 rows passed
```

注意：Limit 滿足後停止拉，但底層的 close 仍然被呼叫（Rust 的 Drop 語義在真實實作中能做到這點）。

## Volcano Model 的優缺點分析

| 面向 | 優點 | 缺點 |
|---|---|---|
| 實作複雜度 | 每個 operator 獨立，容易組合 | —— |
| 記憶體 | 每次一 tuple，記憶體佔用低 | —— |
| I/O | 天然 lazy（Limit 不必讀完整表） | —— |
| 函式呼叫 | —— | 每 tuple 一次虛擬呼叫（vtable dispatch） |
| CPU 利用率 | —— | 難以 SIMD，branch prediction 差 |
| 快取效率 | —— | 欄位分散在不同 tuple，欄位計算 cache miss 多 |
| 適用場景 | OLTP（tuple 少、邏輯複雜） | OLAP（億行以上的掃描聚合） |

一個 `next()` 呼叫鏈在深度 5 的樹上，每個 tuple 需要 5 次虛擬 dispatch。一億列 × 5 = 五億次 vtable 查詢，這在 OLAP 上是真實的瓶頸。

## Vectorized Execution（概念）

MonetDB/X100（Boncz et al., 2005）提出的解法：把 `next()` 改成 `next_batch()`，一次處理一個 vector（例如 1024 個 tuple）。

```rust
// 概念示意：Vectorized Executor 介面
// 未編譯驗證（完整實作需要 columnar layout）

/// 一個 column batch：同一欄的 N 個值連續存放
pub struct ColumnBatch {
    pub col_id: usize,
    pub data: Vec<i64>,     // 簡化：都當 i64
    pub len: usize,
}

pub trait VectorizedExecutor {
    /// 回傳一批（最多 batch_size 個）tuple，以 column 為單位
    fn next_batch(&mut self, batch_size: usize) -> Option<Vec<ColumnBatch>>;
}

// Filter 的向量化版本偽碼：
// fn next_batch_filter(input: &mut dyn VectorizedExecutor, col: usize, threshold: i64)
//     -> Option<Vec<ColumnBatch>>
// {
//     let batch = input.next_batch(1024)?;
//     // 計算選擇向量（selection vector）
//     let mut sel: Vec<bool> = batch[col].data.iter().map(|v| v > &threshold).collect();
//     // 套用選擇向量到所有欄位
//     // 這一步可以用 SIMD 做：_mm256_cmpgt_epi64
//     // ...
// }
```

vectorized 的好處：
- 一萬筆資料的 `salary > 100000`，CPU 用 AVX2 一次比較 4 個 i64，等效迴圈次數從一萬降到 2500。
- 一個函式呼叫處理 1024 個 tuple，vtable dispatch 開銷攤提到 1/1024。
- 同一欄的資料連續存放，L1 cache 命中率大幅提升。

代價：
- 實作複雜度高（需要 columnar layout + selection vector + NULL bitmap）。
- 部分 operator（如 hash join 的 build 階段）向量化較難。
- 記憶體峰值略高（一次持有一批資料）。

## Push-Based 與 Query Compilation 概觀

除了 Volcano 和 vectorized，還有兩種方向值得知道：

**Push-Based**（推模型）：
```
// 資料從 source 主動推給 consumer
// 每個 operator 的邏輯是一個 produce() 函式

scan.produce() {
    for row in heap {
        filter.consume(row)   // 主動推給下游
    }
}

filter.consume(row) {
    if predicate(row) {
        aggregate.consume(row)
    }
}
```

優點：自然 pipeline，適合整合成一個大迴圈（loop fusion）。  
缺點：backpressure 不自然（Limit 要讓 scan 停下來需要額外機制）。

**Query Compilation**（查詢編譯）：
把 physical plan 直接編譯成機器碼（或 LLVM IR），完全消除虛擬呼叫。  
代表作：HyPer 資料庫、Peloton（CMU）。  
代價：compilation latency（查詢首次執行需編譯，小查詢反而慢）、實作複雜度極高。

```
Volcano model  → 通用、低延遲啟動、OLTP 友好
Vectorized     → OLAP 吞吐量最佳、現代 OLAP DB（DuckDB、ClickHouse）採用
Query Compile  → 極致效能、複雜、適合查詢固定的場景
```

## 把 Executor 改成 Iterator 風格

Rust 標準庫的 `Iterator` trait 和 Volcano 幾乎同構。把 `Executor` 改成實作 `Iterator` 可以直接用 Rust 的迭代器組合子：

```rust
// 未編譯驗證（概念示意）

// 讓 Executor 也實作 Iterator
impl Iterator for SeqScan {
    type Item = Tuple;

    fn next(&mut self) -> Option<Tuple> {
        if self.cursor < self.data.len() {
            let t = self.data[self.cursor].clone();
            self.cursor += 1;
            Some(t)
        } else {
            None
        }
    }
}

// 這樣就能用 Rust iterator chain：
// scan.filter(|t| ...).map(|t| ...).take(10).collect::<Vec<_>>()
//
// 這等同於 Volcano model，Rust 的 lazy iterator 天然是 pull-based。
// 問題：open()/close() 語義消失了，需要另外管理初始化/釋放資源。
```

真實資料庫實作中，常見的做法是讓 `next()` 回傳 `Option<Result<Tuple, Error>>`，同時把 open/close 整合進 `new()` 和 `Drop`。

## 踩雷

1. **open() 忘了呼叫**。Volcano 的每個 operator 都要先 `open()` 才能呼叫 `next()`。如果你的 `Executor` 鏈忘了呼叫最頂層的 `open()`，子節點沒初始化，行為未定義。Rust 裡可以用 builder 模式強制在 `execute()` 時自動呼叫 `open()`，避免忘記。

2. **close() 在 panic 時不被呼叫**。`close()` 是手動的，如果 `next()` 途中 panic，close 不會執行，造成資源洩漏（開著的檔案、持有的 latch）。正確做法：把 close 語義放進 `Drop`，或用 `scopeguard` crate。

3. **Filter 吃掉 None 但沒有往上傳**。Filter 的 `next()` 收到子節點的 `None` 時，要立刻回傳 `None`，不能進入無限迴圈再拉。本章程式碼的 `match self.input.next() { None => return None, ... }` 是關鍵。

4. **Limit 不呼叫底層的 next() 後，底層沒 close()**。Volcano 依賴呼叫者最終呼叫 `close()`，而不是依賴 `next()` 回傳 `None` 來觸發清理。Limit 滿足後停止拉，但一定要讓呼叫者呼叫 `close()` 傳播下去。

5. **Box<dyn Executor> 的動態分派開銷**。在 OLTP 查詢（一次幾個 tuple）裡這無所謂；在 OLAP（億行）時，每個 `next()` 都是 vtable 查詢，加上 branch predictor 無法預測，這是真實的效能問題。解法是泛型靜態分派（`impl<T: Executor>`）或 vectorized。

## 進階延伸

- **Morsel-Driven Parallelism**（Leis et al., 2014）：HyPer 的做法——把 physical plan 切成小塊（morsel），由多個線程並行處理，並根據 NUMA 拓撲分配。這是現代 OLAP 平行化的主流方式。
- **DuckDB 的 vectorized push-based**：DuckDB 結合向量化和推模型，每次 push 一個 vector（1024 行），兼得兩者優點。它的源碼是學習 vectorized execution 最好的參考。
- **LLVM-based Query Compilation**：PostgreSQL 16 的 JIT（`pg_jit`）用 LLVM 把 expression evaluation 編譯成機器碼，效能提升 20-40% on OLAP workloads。

## 本章重點整理

- Volcano model：每個 operator 實作 `open/next/close`，上層 pull 下層，一次一 tuple，簡單易組合，OLTP 適用。
- Vectorized：一次 next_batch() 傳一批 tuple（欄式），攤提虛擬呼叫、利於 SIMD，OLAP 主流。
- push-based：資料從 source 主動推給 consumer，適合 pipeline fusion，backpressure 難做。
- query compilation：消除所有虛擬呼叫，極致效能但實作複雜、有 compilation latency。
- Rust 的 `Iterator` trait 天然對應 Volcano pull model；把 `Executor` 實作為 `Iterator` 可以享用標準庫的迭代器組合。

## 自我檢核

- [ ] 我能手寫 Volcano 的 open/next/close 介面並解釋資料流方向
- [ ] 我能說明為什麼 Volcano 在 OLAP 上慢（每 tuple 一次 vtable dispatch，SIMD 無法利用）
- [ ] 我能描述 vectorized execution 如何解決這個問題
- [ ] 我能指出 close() 不被呼叫的情境（panic / Limit 停止）並說明解法
- [ ] 我能解釋 push-based 和 pull-based 的差別，以及各自的適用場景

## 延伸閱讀

1. **Graefe, "Volcano—An Extensible and Parallel Query Evaluation System"（IEEE TKDE 1994）**— Volcano model 的原始論文；介面定義、pipeline parallelism、exchange operator 都在這篇，讀 Section 2（Iterator Model）即可建立完整理解。
2. **Boncz et al., "MonetDB/X100: Hyper-Pipelining Query Execution"（CIDR 2005）**— vectorized execution 的奠基論文；說明為什麼 Volcano 在 OLAP 上慢（Volcano 效率不到理論值的 10%）以及 X100 如何用向量化解決；關聯本章的 vectorized 概觀。
3. **CMU 15-445 Lecture "Query Execution I & II"**— 涵蓋 Volcano/vectorized/push-based 三種模型的比較，有 slides 動畫說明控制流；對照本章程式碼食用。
4. **《Database Internals》Ch 7（Query Processing）**— 白話版執行模型概述；配合本章看，補強 cost 直覺。
5. **DuckDB 原始碼 `src/execution/`**— 現代 vectorized push-based 的真實實作，`PhysicalOperator::GetData()` 對應本章的 `next_batch()`；讀 `physical_filter.cpp` 約 100 行即可看懂模式。

---

Executor 的骨架建好了。下一章進入最貴的操作：Join 演算法——nested loop、hash join、sort-merge，各自的複雜度和適用場景，以及 Rust 的完整實作。

→ [Ch 30 Join 演算法](./30-join-algorithms.md)
