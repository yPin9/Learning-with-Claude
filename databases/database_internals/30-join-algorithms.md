# Ch 30 — Join 演算法

> **目標**：掌握 nested loop join（含 block 版本）、hash join（build/probe 兩階段）、sort-merge join 的完整演算法與複雜度；理解 join 是查詢最貴的操作的原因；用 Rust 實作 hash join 和 nested loop join 並驗證結果正確。

## 為什麼 Join 是最貴的操作？

一個查詢的其他算子——scan、filter、sort——成本通常是輸入大小的線性或 O(N log N)。Join 不同：它在最壞情況下要把兩張表的每一對 tuple 都考慮過，複雜度是 O(N × M)。

更要命的是，Join 是關聯式資料庫最常見的操作。幾乎每個有意義的 SQL 查詢都會 join 至少兩張表。優化 join 演算法是資料庫效能工程最核心的課題之一。

三種基本 join 演算法各有適用場景：

| 演算法 | 前提 | 時間複雜度 | 記憶體 | 適用 |
|---|---|---|---|---|
| Nested Loop Join | 無 | O(N × M) | O(1) | 小表、非等值謂詞 |
| Block Nested Loop | 無 | O(N × M/B) pages | O(B) | 記憶體有限時優化 NLJ |
| Hash Join | 等值謂詞 | O(N + M) 平均 | O(min(N,M)) | 等值 join 主力 |
| Sort-Merge Join | 等值謂詞 | O(N log N + M log M) | O(sort buffer) | 輸入已排序、或需要排序結果 |

**複雜度前提說明**：
- N = outer（左）表的 tuple 數；M = inner（右）表的 tuple 數。
- Hash join 的 O(N + M) 前提是 build 表能放進記憶體、hash 函式無碰撞退化。
- Sort-merge 的 O(N log N + M log M) 若輸入已排序則降為 O(N + M)。

## 先建立直覺

### Nested Loop Join

```
for each row r in outer (left) table:
    for each row s in inner (right) table:
        if r.key == s.key:
            emit (r, s)

最壞：N × M 次比較
```

### Block Nested Loop Join

```
把 outer 切成 block（一次裝 B 筆進 buffer）：
for each block of B rows from outer:
    for each row s in inner:
        for each row r in this block:
            if r.key == s.key:
                emit (r, s)

掃 inner 的次數從 N 次降到 ceil(N/B) 次
```

### Hash Join（Build/Probe）

```
Phase 1 — Build:
    for each row s in build side (small table):
        insert s into hash table keyed on join key

Phase 2 — Probe:
    for each row r in probe side (large table):
        lookup hash table with r.key
        for each matching s:
            emit (r, s)

Build: O(M)；Probe: O(N)；總計 O(N + M)
```

```
Build Phase:
  customers table           Hash Table
  ┌────┬────────┐           key → [rows]
  │ id │ name   │     →     ┌─────────────────┐
  │  1 │ Alice  │           │ 1 → [Alice, TW]  │
  │  2 │ Bob    │           │ 2 → [Bob, US]    │
  │  3 │ Carol  │           │ 3 → [Carol, JP]  │
  └────┴────────┘           └─────────────────┘

Probe Phase:
  orders table          match against hash table
  ┌────┬─────────┐
  │ id │ cust_id │  →  cust_id=1: emit (order, Alice)
  │ 10 │    1    │  →  cust_id=2: emit (order, Bob)
  │ 11 │    2    │  →  cust_id=1: emit (order, Alice)
  │ 12 │    1    │
  └────┴─────────┘
```

### Sort-Merge Join

```
Phase 1 — Sort both inputs on join key
Phase 2 — Merge (like merge sort's merge step):
    i = 0, j = 0
    while i < N and j < M:
        if outer[i].key == inner[j].key:
            emit all matching pairs (handle duplicates)
        elif outer[i].key < inner[j].key:
            i++
        else:
            j++
```

## Rust 實作：Nested Loop Join 與 Hash Join

下面是能在 WSL 上 `cargo run` 的完整實作。延續 Ch 29 的 `Tuple` 定義。

```rust
// src/main.rs
// wsl cargo run

use std::collections::HashMap;

// ─── 基礎資料型別（同 Ch 29）────────────────────────────────

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
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

    /// 合併兩個 tuple（join 結果）
    pub fn concat(left: &Tuple, right: &Tuple) -> Tuple {
        let mut values = left.values.clone();
        values.extend(right.values.clone());
        Tuple { values }
    }
}

// ─── Nested Loop Join ────────────────────────────────────────

/// Nested Loop Join
/// 前提：無（適用任意謂詞）
/// 時間：O(N × M)，N = outer rows，M = inner rows
/// 記憶體：O(1)（不含輸入資料）
pub fn nested_loop_join(
    outer: &[Tuple],
    inner: &[Tuple],
    outer_key_idx: usize,
    inner_key_idx: usize,
) -> Vec<Tuple> {
    let mut result = Vec::new();

    for r in outer.iter() {
        for s in inner.iter() {
            // 等值 join 條件：outer.key == inner.key
            if r.values[outer_key_idx] == s.values[inner_key_idx] {
                result.push(Tuple::concat(r, s));
            }
        }
    }

    result
}

/// Block Nested Loop Join（改良版 NLJ）
/// 把 outer 切成 block，減少 inner 被掃描的次數
/// block_size: 一個 block 持有多少 outer tuple
pub fn block_nested_loop_join(
    outer: &[Tuple],
    inner: &[Tuple],
    outer_key_idx: usize,
    inner_key_idx: usize,
    block_size: usize,
) -> Vec<Tuple> {
    let mut result = Vec::new();

    // 每次取一個 block 的 outer rows
    for outer_chunk in outer.chunks(block_size) {
        // 對 inner 只掃一次，比對 chunk 內每一個 outer row
        for s in inner.iter() {
            for r in outer_chunk.iter() {
                if r.values[outer_key_idx] == s.values[inner_key_idx] {
                    result.push(Tuple::concat(r, s));
                }
            }
        }
    }

    result
}

// ─── Hash Join ───────────────────────────────────────────────

/// Hash Join（in-memory）
/// 前提：等值謂詞、build side 能放進記憶體
/// 時間：O(N + M) 平均（N = probe，M = build）
/// 記憶體：O(M)（build 側的 hash table）
pub fn hash_join(
    probe_side: &[Tuple],     // 通常是大表
    build_side: &[Tuple],     // 通常是小表
    probe_key_idx: usize,
    build_key_idx: usize,
) -> Vec<Tuple> {
    // ── Phase 1: Build ──────────────────────────────────────
    // 把 build side 的所有 tuple 按 key 存入 hash table
    // 注意：同一個 key 可能對應多個 tuple（1:N 關係），用 Vec 儲存
    let mut hash_table: HashMap<Value, Vec<Tuple>> = HashMap::new();

    for s in build_side.iter() {
        let key = s.values[build_key_idx].clone();
        hash_table
            .entry(key)
            .or_insert_with(Vec::new)
            .push(s.clone());
    }

    println!(
        "[HashJoin] Build phase: {} tuples → {} distinct keys",
        build_side.len(),
        hash_table.len()
    );

    // ── Phase 2: Probe ──────────────────────────────────────
    // 對 probe side 每個 tuple，查 hash table
    let mut result = Vec::new();
    let mut probe_hits = 0usize;

    for r in probe_side.iter() {
        let key = &r.values[probe_key_idx];
        if let Some(matches) = hash_table.get(key) {
            for s in matches.iter() {
                // probe 在左（outer），build 在右（inner）
                result.push(Tuple::concat(r, s));
                probe_hits += 1;
            }
        }
    }

    println!(
        "[HashJoin] Probe phase: {} tuples probed, {} matches",
        probe_side.len(),
        probe_hits
    );

    result
}

// ─── Sort-Merge Join（簡化等值版）────────────────────────────

/// Sort-Merge Join
/// 前提：等值謂詞
/// 時間：O(N log N + M log M) 排序 + O(N + M) 合併
/// 若輸入已排序：O(N + M)
pub fn sort_merge_join(
    left: &[Tuple],
    right: &[Tuple],
    left_key_idx: usize,
    right_key_idx: usize,
) -> Vec<Tuple> {
    // 先對兩邊排序（若已排序可省略）
    let mut sorted_left = left.to_vec();
    let mut sorted_right = right.to_vec();

    sorted_left.sort_by(|a, b| {
        compare_values(&a.values[left_key_idx], &b.values[left_key_idx])
    });
    sorted_right.sort_by(|a, b| {
        compare_values(&a.values[right_key_idx], &b.values[right_key_idx])
    });

    // Merge
    let mut result = Vec::new();
    let mut i = 0;
    let mut j = 0;

    while i < sorted_left.len() && j < sorted_right.len() {
        let lk = &sorted_left[i].values[left_key_idx];
        let rk = &sorted_right[j].values[right_key_idx];

        match compare_values(lk, rk) {
            std::cmp::Ordering::Equal => {
                // 找出 left 和 right 中 key 相同的所有 tuple（處理重複）
                let key = lk.clone();

                // 找 left 中連續相同 key 的範圍 [i, left_end)
                let left_start = i;
                while i < sorted_left.len()
                    && sorted_left[i].values[left_key_idx] == key
                {
                    i += 1;
                }
                let left_end = i;

                // 找 right 中連續相同 key 的範圍 [j, right_end)
                let right_start = j;
                while j < sorted_right.len()
                    && sorted_right[j].values[right_key_idx] == key
                {
                    j += 1;
                }
                let right_end = j;

                // 笛卡爾積（cross product of matching groups）
                for li in left_start..left_end {
                    for rj in right_start..right_end {
                        result.push(Tuple::concat(
                            &sorted_left[li],
                            &sorted_right[rj],
                        ));
                    }
                }
            }
            std::cmp::Ordering::Less => {
                i += 1;
            }
            std::cmp::Ordering::Greater => {
                j += 1;
            }
        }
    }

    result
}

fn compare_values(a: &Value, b: &Value) -> std::cmp::Ordering {
    match (a, b) {
        (Value::Int(x), Value::Int(y)) => x.cmp(y),
        (Value::Str(x), Value::Str(y)) => x.cmp(y),
        _ => std::cmp::Ordering::Equal, // 簡化處理
    }
}

// ─── 測試與驗證 ──────────────────────────────────────────────

fn print_results(label: &str, results: &[Tuple]) {
    println!("\n[{}] {} rows:", label, results.len());
    for t in results.iter() {
        println!("  {:?}", t.values);
    }
}

fn main() {
    // 模擬 customers 表：(id, name)
    let customers = vec![
        Tuple::new(vec![Value::Int(1), Value::Str("Alice".into())]),
        Tuple::new(vec![Value::Int(2), Value::Str("Bob".into())]),
        Tuple::new(vec![Value::Int(3), Value::Str("Carol".into())]),
        Tuple::new(vec![Value::Int(4), Value::Str("Dave".into())]),
    ];

    // 模擬 orders 表：(order_id, cust_id, amount)
    // 注意：cust_id=5 在 customers 中不存在（測試 join 不展開無匹配的列）
    let orders = vec![
        Tuple::new(vec![Value::Int(100), Value::Int(1), Value::Int(500)]),
        Tuple::new(vec![Value::Int(101), Value::Int(2), Value::Int(300)]),
        Tuple::new(vec![Value::Int(102), Value::Int(1), Value::Int(200)]),
        Tuple::new(vec![Value::Int(103), Value::Int(3), Value::Int(800)]),
        Tuple::new(vec![Value::Int(104), Value::Int(5), Value::Int(100)]),  // no match
    ];

    // SQL: SELECT * FROM orders o JOIN customers c ON o.cust_id = c.id
    // orders.cust_id (col[1]) = customers.id (col[0])

    println!("=== Nested Loop Join ===");
    let nlj_result = nested_loop_join(&orders, &customers, 1, 0);
    print_results("NLJ", &nlj_result);

    println!("\n=== Block Nested Loop Join (block_size=2) ===");
    let bnlj_result = block_nested_loop_join(&orders, &customers, 1, 0, 2);
    print_results("BNLJ", &bnlj_result);

    println!("\n=== Hash Join (build=customers, probe=orders) ===");
    let hj_result = hash_join(&orders, &customers, 1, 0);
    print_results("HashJoin", &hj_result);

    println!("\n=== Sort-Merge Join ===");
    let smj_result = sort_merge_join(&orders, &customers, 1, 0);
    print_results("SMJ", &smj_result);

    // 驗證三種演算法結果相同
    // 先把 result 依 order_id 排序後比較
    fn sort_result(mut v: Vec<Tuple>) -> Vec<Tuple> {
        v.sort_by(|a, b| compare_values(&a.values[0], &b.values[0]));
        v
    }

    let nlj_sorted = sort_result(nlj_result);
    let hj_sorted = sort_result(hj_result);
    let smj_sorted = sort_result(smj_result);
    let bnlj_sorted = sort_result(bnlj_result);

    let all_match = nlj_sorted.iter().zip(hj_sorted.iter()).zip(smj_sorted.iter())
        .zip(bnlj_sorted.iter())
        .all(|(((a, b), c), d)| {
            a.values == b.values && b.values == c.values && c.values == d.values
        });

    println!("\n=== Correctness Check ===");
    println!("All algorithms produce same result: {}", all_match);
    println!("Expected 4 result rows (cust_id=5 has no match): {}",
        nlj_sorted.len() == 4);
}
```

執行輸出：
```
=== Nested Loop Join ===

[NLJ] 4 rows:
  [Int(100), Int(1), Int(500), Int(1), Str("Alice")]
  [Int(101), Int(2), Int(300), Int(2), Str("Bob")]
  [Int(102), Int(1), Int(200), Int(1), Str("Alice")]
  [Int(103), Int(3), Int(800), Int(3), Str("Carol")]

=== Block Nested Loop Join (block_size=2) ===

[BNLJ] 4 rows:
  ...（同上，順序可能不同）

=== Hash Join (build=customers, probe=orders) ===
[HashJoin] Build phase: 4 tuples → 4 distinct keys
[HashJoin] Probe phase: 5 tuples probed, 4 matches

[HashJoin] 4 rows:
  ...

=== Sort-Merge Join ===

[SMJ] 4 rows:
  ...

=== Correctness Check ===
All algorithms produce same result: true
Expected 4 result rows (cust_id=5 has no match): true
```

## 各演算法深度對比

### 什麼時候用 Nested Loop Join？

- 連接條件是**非等值**謂詞（`r.val > s.val`、`r.start < s.end`）——hash join 和 sort-merge join 只支援等值。
- inner table 很小（幾十 ~ 幾百筆），迴圈代價可忽略，實作最簡單。
- inner table 有索引：每個 outer tuple 用索引查 inner，成本降為 O(N × log M)，稱為 **Index Nested Loop Join**。

### 什麼時候用 Hash Join？

- 等值謂詞的大表 join——最常見的 OLAP join。
- 記憶體能放下 build side（若放不下需要 **grace hash join**：先按 key hash 分桶到磁碟，再逐桶做記憶體內 hash join）。
- 不需要排序順序的輸出。

Grace Hash Join（處理 build side 不能放進記憶體的情況）：
```
Phase 1 — Partition:
    把 build side 和 probe side 都按相同 hash 函式分到 k 個分區
    （相同 key 一定落在相同分區號）

Phase 2 — Per-partition Join:
    for each partition i in [0, k):
        load partition_build[i] into memory
        hash join with partition_probe[i]
        emit results
```

### 什麼時候用 Sort-Merge Join？

- 輸入**已經排序**（比如前面有 ORDER BY，或索引掃描帶排序序）——排序成本為零，只剩 O(N + M) 的 merge。
- 需要排序的輸出（ORDER BY 和 JOIN 共享排序成本）。
- 記憶體比 build side 小（sort-merge 的記憶體峰值是 sort buffer，通常比 hash table 小）。

### 三種演算法複雜度比較

```
設 N = probe 筆數（大表），M = build 筆數（小表），N ≥ M

Nested Loop Join:   O(N × M)     -- 等值 join 時 N=10K, M=100 → 100萬次比較
Block NLJ:          O((N/B) × M) -- B=1000 則比較次數降 1000 倍
Hash Join:          O(N + M)     -- build 4ms + probe 8ms ≈ 12ms
Sort-Merge Join:    O(N logN + M logM) -- 若已排序則 O(N + M)
```

實際上對於 N = 1M 筆、M = 100K 筆的 join：
- NLJ: 10^11 次操作（不可接受）
- Hash Join: ~1.1M 次操作（在 build 能進記憶體的前提下）
- SMJ（需先排序）: ~1M × 20 + 100K × 17 ≈ 22M 次操作，加上排序 I/O

## Join 重新排序（Join Reordering）

兩個以上的表 join 時，順序很重要。三張表 R, S, T 的 join 有 (3-1)! = 2 種線性順序（left-deep tree），加上 bushy tree 更多。

```
Left-deep tree:         Bushy tree:
   ⊳⊲                     ⊳⊲
  /  \                    /  \
 ⊳⊲   T                 ⊳⊲   ⊳⊲
 / \                   / \   / \
R   S                 R   S T   U
```

cost-based optimizer 用動態規劃枚舉所有 join 順序，選成本最低的。這是 Ch 33 的主題。

## 踩雷

1. **Hash join 的 build side 選錯造成 OOM**。永遠把估計較小的表放 build side。如果 optimizer 統計錯誤，把一張 1 億筆的表當 build，hash table 會吃掉數十 GB 記憶體。PostgreSQL 有 hash batch 機制（類似 grace hash join）來應對，但代價是磁碟 I/O。

2. **Hash collision 退化**。當 join key 的分布非常偏斜（skew），同一個 hash bucket 裡有大量 tuple，probe 的內層迴圈從 O(1) 退化到 O(bucket_size)。解法：用高品質的 hash 函式（murmur3、xxhash）而不是取模。

3. **Sort-merge join 的重複 key 笛卡爾積爆炸**。若 join key 大量重複（例如 join 兩個都有 10 萬筆 region='TW' 的表），sort-merge 的 matching group 是 10^10 個 tuple——正確，但可能是業務邏輯的 bug。應在 optimizer 層警告。

4. **Nested loop join 的 inner table 沒有索引**。NLJ 最常見的錯誤是 inner table 沒建 index：每個 outer tuple 都做 full table scan，N×M 代價。SQL `EXPLAIN` 看到 NLJ 時，立刻檢查 inner 的 join key 有沒有索引。

5. **join 結果的 schema 欄位重疊**。`SELECT *` 在 join 後，若兩表都有 `id` 欄，結果有兩個 `id`，呼叫者用名稱取欄時歧義。真實 DB 用 `table.column` 限定名，或在 physical plan 的 `output_schema` 裡重新命名。

## 進階延伸

- **Grace Hash Join（Partitioned Hash Join）**：build side 放不進記憶體時的解法；PostgreSQL 8.x 實作了 batch hash join，DuckDB 也有類似實作。
- **Symmetric Hash Join**：適合流式資料——build side 和 probe side 都建 hash table，兩邊都可以作為 probe，處理無邊界的流式 join。
- **Hybrid Hash Join**：先把能放進記憶體的部分直接 join，放不進去的溢出到磁碟分桶；比純 grace hash 節省 I/O。

## 本章重點整理

- 三種基本 join 演算法：NLJ（O(N×M)，無前提）、Hash Join（O(N+M) 等值，build side 進記憶體）、Sort-Merge（O(N logN+M logM) 或 O(N+M) 若已排序，等值）。
- Build side 放小表是 hash join 的鐵律；build side 進不了記憶體需要 grace hash join（分桶到磁碟）。
- NLJ 適合非等值謂詞或小表；sort-merge 適合輸入已排序或需要排序輸出。
- Join 是查詢最貴的操作；join 順序和演算法選擇是 cost-based optimizer 的核心任務（Ch 33）。
- 重複 key 的 sort-merge join 結果是笛卡爾積，可能合法但也可能是業務邏輯問題。

## 自我檢核

- [ ] 我能說出三種 join 演算法各自的複雜度與前提
- [ ] 我能解釋 hash join 的 build/probe 兩個階段，以及為什麼 build side 要放小表
- [ ] 我能描述 sort-merge join 遇到重複 key 時怎麼處理
- [ ] 我能說明什麼情況下 NLJ 比 hash join 好
- [ ] 我能解釋 grace hash join 解決了什麼問題

## 延伸閱讀

1. **CMU 15-445 Lecture "Join Algorithms"**（Lecture 11）— 詳細講解 NLJ/BNL/hash join/sort-merge join 的 I/O cost 模型，有 page-level 的成本計算範例；對照本章的 O() 複雜度，補 I/O cost 直覺。
2. **Shapiro, "Join Processing in Database Systems with Large Main Memories"（ACM TODS 1986）**— hash join 系列演算法（包含 grace hash join、hybrid hash join）的完整分析；讀 Section 3（Simple Hash Join）和 Section 4（Grace Hash Join）即可建立 production-grade hash join 的完整圖像。
3. **《Database Internals》Ch 6（"B-Tree Variants"）讀完後看 Ch 7**— Ch 7 包含 join 演算法在儲存層的 cost model，說明 page-level access 如何影響 join 效能，與本章的 in-memory 分析形成對比。
4. **Neumann, "Efficiently Compiling Efficient Query Plans for Modern Hardware"（VLDB 2011）**— HyPer 的 query compilation 論文，說明如何把 join pipeline 編譯成 tight loop 消除 Volcano 的 per-tuple 開銷；讀 Section 2（Pipeline Concept）理解 pipeline breaker（hash join build 階段）的概念。

---

Join 演算法搞定了。下一章處理另外兩個查詢引擎的基礎操作：排序（尤其是資料超過記憶體的 external sort）和聚合（GROUP BY 的 hash 與 sort 兩種實作）。

→ [Ch 31 排序與聚合](./31-sort-aggregation.md)
