# Ch 28 — Physical Plan

> **目標**：理解 logical plan 到 physical plan 的轉換，知道 physical operator 帶了什麼額外資訊、access path 選擇怎麼運作，並用 Rust 定義 `PhysicalPlan` 讓它能從 logical plan 產生。

## 為什麼需要 Physical Plan？

上一章我們把 SQL 翻成關聯代數（relational algebra）樹——那是 logical plan：描述「要做什麼（what）」，不說「怎麼做（how）」。

logical plan 的 `Join(R, S)` 沒告訴你要用 hash join 還是 nested loop join；`Filter(x > 5)` 沒告訴你要全表掃還是走索引。這些決策留到 **physical plan（實體計畫）** 階段才做。

physical plan 做的事：
1. 為每個 logical operator 選一個具體的實作（implementation）
2. 決定 access path：全表掃（SeqScan）或走索引（IndexScan）
3. 帶上執行所需的所有元資訊——用哪個表、哪個索引、join buffer 大小

沒有 physical plan，executor 不知道從哪裡讀資料、用什麼演算法合并。

## 先建立直覺

```
SQL: SELECT * FROM orders o JOIN customers c ON o.cust_id = c.id WHERE c.region = 'TW'

Logical Plan:
    Project(*)
        └── Join(o.cust_id = c.id)
               ├── Scan(orders)
               └── Filter(c.region = 'TW')
                      └── Scan(customers)

Physical Plan（一種選擇）:
    PhysicalProject(*)
        └── HashJoin(build=customers, probe=orders, key=cust_id=id)
               ├── SeqScan(table=orders)
               └── IndexScan(table=customers, index=idx_region, pred=region='TW')

Physical Plan（另一種選擇）:
    PhysicalProject(*)
        └── NestedLoopJoin(outer=orders, inner=customers, pred=cust_id=id)
               ├── SeqScan(table=orders)
               └── SeqScan(customers, filter=region='TW')
```

兩個 physical plan 對應同一個 logical plan，但成本天差地別。選哪個是 optimizer 的工作（Ch 32-33），本章聚焦「怎麼定義和建出 physical plan」。

## Logical Operator vs Physical Operator

一個 logical operator 可能對應多個 physical operator，這是 1-to-N 的關係：

| Logical Operator | Physical Operators |
|---|---|
| `Scan(table)` | `SeqScan`、`IndexScan`、`IndexOnlyScan` |
| `Join(pred)` | `NestedLoopJoin`、`BlockNestedLoopJoin`、`HashJoin`、`SortMergeJoin` |
| `Filter(pred)` | `Filter`（通常直接推入 scan，做 predicate pushdown） |
| `Aggregate(GROUP BY)` | `HashAggregate`、`SortAggregate` |
| `Sort(keys)` | `InMemorySort`、`ExternalSort` |
| `Project(cols)` | `Projection`（幾乎 1-to-1，但可能有 expression rewrite） |

physical operator 多出來的資訊：
- **access path**：哪個索引、哪個表 heap file
- **build/probe 角色**（hash join）
- **sort key 與排序方向**
- **buffer 大小**（external sort 的 run size）

## Access Path 選擇

「access path（存取路徑）」決定如何讀資料：

```
SeqScan     ── 從頭到尾掃整個 heap file
              成本 ∝ 表的 page 數
              永遠可用

IndexScan   ── 走 B+tree 索引找符合謂詞的 rowid，再回 heap 拿完整列
              成本 = 索引走訪 + 回表讀
              條件：謂詞在索引鍵上、selectivity 夠高（選出的比例小）

IndexOnlyScan ── 索引葉節點已有所有需要的欄位，不回表
              成本 = 索引走訪
              條件：covering index
```

什麼時候用 IndexScan 比 SeqScan 好？

```
直覺規則：selectivity < 10–20% 時，IndexScan 通常贏。
若 selectivity = 80%（選出 80% 列），回表隨機讀的代價 > SeqScan 順序讀。
```

真正的決策要靠統計資訊（histogram、row count），那是 cost-based optimizer 的活，Ch 33 再細談。本章的 physical planner 用一個簡化規則：**有等值謂詞且欄位有索引就走 IndexScan，否則 SeqScan**。

## Physical Plan 的 Rust 資料結構

先定義 physical operator 的 enum，再定義樹狀結構：

```rust
// 未編譯驗證（完整專案依賴前幾章的 Catalog/LogicalPlan 結構）

use std::sync::Arc;

/// 謂詞：簡化版，只有等值比較
#[derive(Debug, Clone)]
pub enum Predicate {
    Eq { column: String, value: ScalarValue },
    And(Box<Predicate>, Box<Predicate>),
}

#[derive(Debug, Clone)]
pub enum ScalarValue {
    Int64(i64),
    Str(String),
    Bool(bool),
}

/// Join 條件：只支援等值
#[derive(Debug, Clone)]
pub struct JoinCondition {
    pub left_col: String,
    pub right_col: String,
}

/// Physical Operator 枚舉
/// 每個 variant 帶執行所需的完整資訊
#[derive(Debug, Clone)]
pub enum PhysicalOperator {
    /// 全表掃描
    SeqScan {
        table: String,
        alias: Option<String>,
        filter: Option<Predicate>,
    },

    /// 索引掃描（等值謂詞）
    IndexScan {
        table: String,
        index: String,
        predicate: Predicate,
        alias: Option<String>,
    },

    /// Hash Join：先 build 小表的 hash table，再 probe 大表
    HashJoin {
        condition: JoinCondition,
        build_side: Box<PhysicalPlan>,
        probe_side: Box<PhysicalPlan>,
    },

    /// Nested Loop Join：外層每行對內層全掃
    NestedLoopJoin {
        condition: JoinCondition,
        outer: Box<PhysicalPlan>,
        inner: Box<PhysicalPlan>,
    },

    /// Sort
    Sort {
        keys: Vec<SortKey>,
        input: Box<PhysicalPlan>,
    },

    /// Hash Aggregation
    HashAggregate {
        group_by: Vec<String>,
        aggregates: Vec<AggregateExpr>,
        input: Box<PhysicalPlan>,
    },

    /// Projection
    Projection {
        columns: Vec<String>,
        input: Box<PhysicalPlan>,
    },

    /// Filter（未被 pushdown 的殘餘謂詞）
    Filter {
        predicate: Predicate,
        input: Box<PhysicalPlan>,
    },
}

#[derive(Debug, Clone)]
pub struct SortKey {
    pub column: String,
    pub descending: bool,
}

#[derive(Debug, Clone)]
pub struct AggregateExpr {
    pub func: AggFunc,
    pub column: String,
    pub alias: String,
}

#[derive(Debug, Clone)]
pub enum AggFunc {
    Count,
    Sum,
    Avg,
    Min,
    Max,
}

/// Physical Plan 節點：operator + schema 資訊
#[derive(Debug, Clone)]
pub struct PhysicalPlan {
    pub operator: PhysicalOperator,
    /// 這個節點輸出的欄位列表
    pub output_schema: Vec<String>,
}

impl PhysicalPlan {
    pub fn new(operator: PhysicalOperator, output_schema: Vec<String>) -> Self {
        Self { operator, output_schema }
    }
}
```

## 從 Logical Plan 產生 Physical Plan

這個轉換函式是本章的核心：它遞迴走訪 logical plan 樹，為每個節點選一個 physical operator：

```rust
// 未編譯驗證（需要前幾章的 LogicalPlan 定義）

use std::collections::HashMap;

/// 模擬 Catalog：記錄哪些欄位有索引
pub struct Catalog {
    /// table_name -> 有索引的欄位集合
    pub indexes: HashMap<String, Vec<String>>,
}

impl Catalog {
    pub fn new() -> Self {
        let mut indexes = HashMap::new();
        // 假設 customers 表有 region 欄位的索引
        indexes.insert(
            "customers".to_string(),
            vec!["id".to_string(), "region".to_string()],
        );
        Self { indexes }
    }

    /// 檢查 table.column 是否有索引
    pub fn has_index(&self, table: &str, column: &str) -> bool {
        self.indexes
            .get(table)
            .map(|cols| cols.iter().any(|c| c == column))
            .unwrap_or(false)
    }

    /// 取得對應的索引名稱（簡化：直接回傳 idx_{column}）
    pub fn index_name(&self, table: &str, column: &str) -> String {
        format!("idx_{}_{}", table, column)
    }
}

/// Logical Plan（簡化版，對應 Ch 27）
#[derive(Debug, Clone)]
pub enum LogicalPlan {
    Scan {
        table: String,
        alias: Option<String>,
        filter: Option<Predicate>,
    },
    Join {
        condition: JoinCondition,
        left: Box<LogicalPlan>,
        right: Box<LogicalPlan>,
    },
    Project {
        columns: Vec<String>,
        input: Box<LogicalPlan>,
    },
    Filter {
        predicate: Predicate,
        input: Box<LogicalPlan>,
    },
    Sort {
        keys: Vec<SortKey>,
        input: Box<LogicalPlan>,
    },
    Aggregate {
        group_by: Vec<String>,
        aggregates: Vec<AggregateExpr>,
        input: Box<LogicalPlan>,
    },
}

/// Physical Planner：把 logical plan 轉換為 physical plan
pub struct PhysicalPlanner<'a> {
    catalog: &'a Catalog,
}

impl<'a> PhysicalPlanner<'a> {
    pub fn new(catalog: &'a Catalog) -> Self {
        Self { catalog }
    }

    pub fn plan(&self, logical: &LogicalPlan) -> PhysicalPlan {
        match logical {
            LogicalPlan::Scan { table, alias, filter } => {
                self.plan_scan(table, alias.as_deref(), filter.as_ref())
            }
            LogicalPlan::Join { condition, left, right } => {
                self.plan_join(condition, left, right)
            }
            LogicalPlan::Project { columns, input } => {
                let child = self.plan(input);
                PhysicalPlan::new(
                    PhysicalOperator::Projection {
                        columns: columns.clone(),
                        input: Box::new(child),
                    },
                    columns.clone(),
                )
            }
            LogicalPlan::Filter { predicate, input } => {
                let child = self.plan(input);
                let schema = child.output_schema.clone();
                PhysicalPlan::new(
                    PhysicalOperator::Filter {
                        predicate: predicate.clone(),
                        input: Box::new(child),
                    },
                    schema,
                )
            }
            LogicalPlan::Sort { keys, input } => {
                let child = self.plan(input);
                let schema = child.output_schema.clone();
                PhysicalPlan::new(
                    PhysicalOperator::Sort {
                        keys: keys.clone(),
                        input: Box::new(child),
                    },
                    schema,
                )
            }
            LogicalPlan::Aggregate { group_by, aggregates, input } => {
                let child = self.plan(input);
                let mut schema = group_by.clone();
                for agg in aggregates {
                    schema.push(agg.alias.clone());
                }
                PhysicalPlan::new(
                    PhysicalOperator::HashAggregate {
                        group_by: group_by.clone(),
                        aggregates: aggregates.clone(),
                        input: Box::new(child),
                    },
                    schema,
                )
            }
        }
    }

    /// 決定 scan 的 access path
    fn plan_scan(
        &self,
        table: &str,
        alias: Option<&str>,
        filter: Option<&Predicate>,
    ) -> PhysicalPlan {
        // 嘗試用等值謂詞走索引
        if let Some(pred) = filter {
            if let Predicate::Eq { column, value } = pred {
                if self.catalog.has_index(table, column) {
                    let index = self.catalog.index_name(table, column);
                    return PhysicalPlan::new(
                        PhysicalOperator::IndexScan {
                            table: table.to_string(),
                            index,
                            predicate: pred.clone(),
                            alias: alias.map(|s| s.to_string()),
                        },
                        // 簡化：schema 在真實實作中從 Catalog 取得
                        vec!["*".to_string()],
                    );
                }
            }
        }

        // 回退到 SeqScan
        PhysicalPlan::new(
            PhysicalOperator::SeqScan {
                table: table.to_string(),
                alias: alias.map(|s| s.to_string()),
                filter: filter.cloned(),
            },
            vec!["*".to_string()],
        )
    }

    /// 決定 join 演算法
    /// 簡化規則：等值 join 一律用 HashJoin
    fn plan_join(
        &self,
        condition: &JoinCondition,
        left: &LogicalPlan,
        right: &LogicalPlan,
    ) -> PhysicalPlan {
        let left_plan = self.plan(left);
        let right_plan = self.plan(right);

        // 真實實作會根據統計估算大小，小表放 build side
        // 這裡簡化：right 當 build（假設是維度表）
        let mut schema = left_plan.output_schema.clone();
        schema.extend(right_plan.output_schema.clone());

        PhysicalPlan::new(
            PhysicalOperator::HashJoin {
                condition: condition.clone(),
                build_side: Box::new(right_plan),
                probe_side: Box::new(left_plan),
            },
            schema,
        )
    }
}

fn main() {
    let catalog = Catalog::new();
    let planner = PhysicalPlanner::new(&catalog);

    // 建一個 logical plan：customers 表用 region='TW' 過濾
    let logical = LogicalPlan::Scan {
        table: "customers".to_string(),
        alias: Some("c".to_string()),
        filter: Some(Predicate::Eq {
            column: "region".to_string(),
            value: ScalarValue::Str("TW".to_string()),
        }),
    };

    let physical = planner.plan(&logical);

    // 應該產生 IndexScan（customers 的 region 欄有索引）
    match &physical.operator {
        PhysicalOperator::IndexScan { table, index, .. } => {
            println!("Access path: IndexScan on {} via {}", table, index);
        }
        PhysicalOperator::SeqScan { table, .. } => {
            println!("Access path: SeqScan on {}", table);
        }
        _ => println!("other operator"),
    }

    // 沒有索引的表應該回退 SeqScan
    let logical2 = LogicalPlan::Scan {
        table: "orders".to_string(),
        alias: None,
        filter: Some(Predicate::Eq {
            column: "status".to_string(),
            value: ScalarValue::Str("shipped".to_string()),
        }),
    };
    let physical2 = planner.plan(&logical2);
    match &physical2.operator {
        PhysicalOperator::SeqScan { table, .. } => {
            println!("Access path: SeqScan on {} (no index, as expected)", table);
        }
        PhysicalOperator::IndexScan { table, index, .. } => {
            println!("Unexpected IndexScan on {} via {}", table, index);
        }
        _ => {}
    }
}
```

執行輸出應為：
```
Access path: IndexScan on customers via idx_customers_region
Access path: SeqScan on orders (no index, as expected)
```

## 同一 Logical Plan 的多個 Physical 候選

optimizer 實際上不只產生一個 physical plan，而是枚舉多個候選，算成本，取最低。下面是同一 logical join 的兩個候選：

```rust
// 概念示意，未編譯驗證

fn enumerate_join_candidates(
    condition: &JoinCondition,
    left: PhysicalPlan,
    right: PhysicalPlan,
) -> Vec<PhysicalPlan> {
    let mut candidates = Vec::new();

    // 候選 1：HashJoin，right 當 build
    {
        let mut schema = left.output_schema.clone();
        schema.extend(right.output_schema.clone());
        candidates.push(PhysicalPlan::new(
            PhysicalOperator::HashJoin {
                condition: condition.clone(),
                build_side: Box::new(right.clone()),
                probe_side: Box::new(left.clone()),
            },
            schema,
        ));
    }

    // 候選 2：NestedLoopJoin，適合小表或非等值 join
    {
        let mut schema = left.output_schema.clone();
        schema.extend(right.output_schema.clone());
        candidates.push(PhysicalPlan::new(
            PhysicalOperator::NestedLoopJoin {
                condition: condition.clone(),
                outer: Box::new(left.clone()),
                inner: Box::new(right.clone()),
            },
            schema,
        ));
    }

    // 候選 3：HashJoin，left 當 build（若 left 更小）
    {
        let mut schema = left.output_schema.clone();
        schema.extend(right.output_schema.clone());
        candidates.push(PhysicalPlan::new(
            PhysicalOperator::HashJoin {
                condition: condition.clone(),
                build_side: Box::new(left.clone()),
                probe_side: Box::new(right.clone()),
            },
            schema,
        ));
    }

    candidates
}
```

cost model 拿這些候選，套用統計資訊估算成本，選最低的。本課 Ch 33 再深入。

## Physical Plan 的 pretty-print

能印出 physical plan 是偵錯的基礎（類似 PostgreSQL 的 `EXPLAIN`）：

```rust
// 未編譯驗證

impl PhysicalPlan {
    pub fn explain(&self, depth: usize) {
        let indent = "  ".repeat(depth);
        match &self.operator {
            PhysicalOperator::SeqScan { table, filter, .. } => {
                println!("{}SeqScan({}){}", indent, table,
                    filter.as_ref().map(|_| " [filter]").unwrap_or(""));
            }
            PhysicalOperator::IndexScan { table, index, .. } => {
                println!("{}IndexScan({} via {})", indent, table, index);
            }
            PhysicalOperator::HashJoin { condition, build_side, probe_side } => {
                println!("{}HashJoin({} = {})", indent,
                    condition.left_col, condition.right_col);
                println!("{}  [build]", indent);
                build_side.explain(depth + 2);
                println!("{}  [probe]", indent);
                probe_side.explain(depth + 2);
            }
            PhysicalOperator::Projection { columns, input } => {
                println!("{}Project({})", indent, columns.join(", "));
                input.explain(depth + 1);
            }
            PhysicalOperator::Filter { predicate: _, input } => {
                println!("{}Filter", indent);
                input.explain(depth + 1);
            }
            PhysicalOperator::Sort { keys, input } => {
                let key_str: Vec<_> = keys.iter()
                    .map(|k| format!("{}{}", k.column,
                        if k.descending { " DESC" } else { "" }))
                    .collect();
                println!("{}Sort({})", indent, key_str.join(", "));
                input.explain(depth + 1);
            }
            PhysicalOperator::HashAggregate { group_by, aggregates, input } => {
                println!("{}HashAggregate(group_by=[{}])", indent, group_by.join(", "));
                input.explain(depth + 1);
            }
            _ => println!("{}(other)", indent),
        }
    }
}
```

輸出範例：
```
Project(*)
  HashJoin(cust_id = id)
    [build]
    IndexScan(customers via idx_customers_region)
    [probe]
    SeqScan(orders)
```

## 踩雷

1. **physical planner 和 optimizer 的邊界模糊**。有人把 access path 選擇放進 optimizer，有人放進 physical planner。本課把「枚舉 physical 候選」放 physical planner，「選最低成本」放 optimizer。你的架構決策前先想清楚分層。

2. **predicate pushdown 要在 logical plan 就做**。如果 logical → physical 時才做 predicate pushdown，你的 PhysicalOperator::Filter 可能包著一個本該消失的節點。最好在 Ch 32 的 rule-based 優化裡先完成 pushdown，physical planner 再看推過來的 logical plan。

3. **join 的 build side 選錯代價很高**。HashJoin build 小表、probe 大表是基本原則。physical planner 若不查統計就隨便選 build side，遇到大表 build 時記憶體會爆。

4. **IndexScan 不一定比 SeqScan 快**。高 selectivity（選出比例大）時，回表（index scan + heap fetch）的隨機 I/O 比順序 SeqScan 更慢。沒有統計資訊的規則式 access path 選擇，要誠實標「簡化，非最優」。

5. **output_schema 追蹤容易出 bug**。Projection 後 schema 縮減；Join 後 schema 是兩邊合併；Filter 不改 schema。如果 schema 傳錯，executor 取欄位時會拿到錯的 offset，靜默產生錯誤結果而不是 panic。

## 進階延伸

- **Interesting Orders**：sort-merge join 要求輸入已排序；如果 logical plan 已有 sort，physical planner 可以把這個「interesting order」傳遞下去，避免重複排序。System R 的 Selinger 論文原創這個概念。
- **物化（Materialization）vs Pipeline**：某些 physical operator（如 hash join 的 build 階段）需要物化整個輸入；pipeline-able operator（如 filter）不需要。physical plan 標記哪些節點需要物化，executor 才能正確調度。
- **Adaptive Query Execution（AQE）**：Spark 的做法——execution 到一半才根據真實統計重新選 physical plan。這讓 planner 不必在 compile time 猜對所有統計。

## 本章重點整理

- logical plan 描述「做什麼」；physical plan 描述「怎麼做」——帶 access path、演算法選擇、執行所需元資訊。
- 一個 logical operator 對應多個 physical operator 候選，optimizer 選成本最低的。
- access path 選擇：等值謂詞 + 有索引 → IndexScan；否則 SeqScan；selectivity 高時 SeqScan 可能反而更快。
- physical planner 是遞迴轉換：為每個 logical 節點選一個 physical 節點，子樹先遞迴。
- output_schema 要在每個節點正確維護，executor 才能按欄位名取值。

## 自我檢核

- [ ] 我能說出 logical plan 和 physical plan 的差別（帶什麼額外資訊）
- [ ] 我能列舉 SeqScan 和 IndexScan 各自適用的條件
- [ ] 我能說明為什麼同一 logical plan 會有多個 physical 候選
- [ ] 我能解釋 HashJoin 為什麼要分 build side 和 probe side，哪邊放小表
- [ ] 我能指出 predicate pushdown 應該在哪個階段做

## 延伸閱讀

1. **CMU 15-445 Lecture "Query Execution I"**（slides + video）— 涵蓋 physical operator 的種類與 access path 選擇決策；對照本章的 `PhysicalOperator` enum 看，理解每個 variant 的動機。
2. **Selinger et al., "Access Path Selection in a Relational DBMS"（System R, 1979）**— 第一個系統性討論 access path 選擇與 interesting order 的論文；Ch 3-4 直接對應本章。
3. **《Database Internals》Ch 4（B-tree）+ Ch 7（query processing overview）**— 說明儲存層如何支撐 physical plan 的 scan/index 操作；配合本課 Part 1 一起讀。
4. **PostgreSQL `EXPLAIN` 文件**（官方 docs, "Using EXPLAIN"）— 看真實資料庫怎麼呈現 physical plan；每個節點類型都對應本章的某個 physical operator。

---

本章定義了 physical plan 的資料結構和轉換邏輯。下一章進入**執行模型**：physical plan 樹建好後，executor 怎麼把它跑起來、一個個把 tuple 吐出來。

→ [Ch 29 執行模型：Volcano vs vectorized](./29-execution-model.md)
