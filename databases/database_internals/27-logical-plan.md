# Ch 27 — Logical Plan（關聯代數）

> **目標**：理解關聯代數（relational algebra）的核心運算子、它們如何組成一棵樹來表達查詢語義，以及如何把 Ch 25–26 產出的 Bound AST 轉換成 Logical Plan Tree。這棵樹是查詢優化器（Ch 32–33）的操作對象。

## 為什麼要有 Logical Plan？

Ch 25 的 AST 忠實反映了 SQL 的**語法結構**——`SELECT ... FROM ... WHERE ...` 的巢狀組織。但 SQL 語法結構和「執行的順序」完全是兩回事。

考慮這個查詢：
```sql
SELECT e.name, d.dept_name
FROM employees e
JOIN departments d ON e.dept_id = d.id
WHERE e.salary > 100000;
```

合法的執行路徑至少有：
1. 先做 cross join（笛卡兒積），再 filter `e.dept_id = d.id`，再 filter `salary > 100000`
2. 先 filter `salary > 100000`（過濾 employees），再 join
3. 先 index scan employees（salary 有索引），再 nested loop join

這三條路徑**語義等效**，但效能差異可能是萬倍。優化器需要一個**語義層面的中間表示**，可以在不改變結果的前提下做等效變換——這就是 Logical Plan。

Logical Plan 描述「要做什麼（what）」，Physical Plan（Ch 28）才決定「怎麼做（how）」。同一個 Logical Plan 可以對應多個合法的 Physical Plan；優化器的工作就是選最便宜的那個。

## 關聯代數：六個核心運算子

關聯代數（relational algebra）是 Codd 在 1970 年提出的查詢語言理論基礎。SQL 的每個子句都有對應的代數運算子：

### σ — Selection（選擇）
保留滿足謂詞（predicate）的 rows，丟棄不滿足的。

```
σ(salary > 100000)(employees)
```

對應 SQL 的 `WHERE salary > 100000`。

```
輸入：R（一個 relation）
謂詞：P（布林表達式）
輸出：{ t ∈ R | P(t) }
```

### π — Projection（投影）
只保留指定的欄，丟棄其餘欄。（可能去除重複 rows，取決於是否 DISTINCT）

```
π(name, dept_name)(...)
```

對應 SQL 的 `SELECT name, dept_name`。

```
輸入：R
欄位集合：A₁, A₂, ...
輸出：{ (t.A₁, t.A₂, ...) | t ∈ R }
```

### ⋈ — Join（連接）

最常見的是 **等值連接（equi-join）**：

```
employees ⋈(e.dept_id = d.id) departments
```

等同於 cross join + selection：

```
employees × departments（笛卡兒積）
σ(e.dept_id = d.id)
```

實際不這樣做（笛卡兒積太貴），但語義等效。

### γ — Aggregation（聚合）

```
γ(dept_id; COUNT(*) → cnt, AVG(salary) → avg_sal)(employees)
```

對應 SQL 的 `GROUP BY dept_id` + 聚合函式。

```
分組欄：G₁, G₂, ...
聚合函式：f₁(A₁), f₂(A₂), ...
輸出：每個 group 一個 row
```

### τ — Sort（排序）

```
τ(salary DESC)(employees)
```

對應 SQL 的 `ORDER BY salary DESC`。

### Limit（限制行數）

沒有希臘字母符號（非標準代數），但每個 DB 都需要：

```
LIMIT(10)(...)
```

對應 SQL 的 `LIMIT 10`。

## Logical Plan Tree：一個完整範例

```sql
SELECT e.name, d.dept_name
FROM employees e
JOIN departments d ON e.dept_id = d.id
WHERE e.salary > 100000
ORDER BY e.name
LIMIT 10;
```

對應的 Logical Plan Tree（從底部往上讀，如同資料流動方向）：

```
                    Limit(10)
                       │
                    Sort(e.name ASC)
                       │
              Projection(e.name, d.dept_name)
                       │
                  Selection(e.salary > 100000)
                       │
                Join(e.dept_id = d.id)
               /                     \
        Scan(employees, as=e)    Scan(departments, as=d)
```

注意：這裡 `Selection` 還在 `Join` 之上——表示先 join 再 filter。優化器（Ch 32）的謂詞下推（predicate pushdown）會把 `Selection(salary > 100000)` 推到 `Scan(employees)` 的上面，變成：

```
                    Limit(10)
                       │
                    Sort(e.name ASC)
                       │
              Projection(e.name, d.dept_name)
                       │
                Join(e.dept_id = d.id)
               /                     \
  Selection(salary > 100000)     Scan(departments)
          │
   Scan(employees)
```

這個等效變換在 Logical Plan 層做，不需要觸碰 Physical Plan。

## Logical 與 Physical 的分離：為什麼重要

| | Logical Plan | Physical Plan |
|--|-------------|---------------|
| 問的問題 | 結果集是什麼？ | 怎麼計算出來？ |
| Join 表示 | `Join(condition)` | `HashJoin` / `NestedLoopJoin` / `MergeJoin` |
| Selection 表示 | `Selection(pred)` | `Filter（行式）` / `IndexScan` |
| 一個 logical 對應的 physical 數量 | 1 | 多個（優化器選最佳） |
| 可做等效變換？ | 是（謂詞下推、join reorder） | 否（等效性更難保證） |
| 需要知道資料統計？ | 否 | 是（cost-based 需要） |

## Rust 實作：LogicalPlan

```rust
use std::fmt;

/// 表達式（複用 Ch 26 的 TypedExpr，這裡用簡化版）
#[derive(Debug, Clone)]
pub enum ScalarExpr {
    /// 常數
    Literal(Value),
    /// 欄位引用（帶 table alias 和欄位名）
    ColumnRef { table: String, column: String, data_type: DataType },
    /// 二元運算
    BinOp { op: BinOp, left: Box<ScalarExpr>, right: Box<ScalarExpr> },
    /// IS NULL
    IsNull { expr: Box<ScalarExpr>, negated: bool },
}

#[derive(Debug, Clone)]
pub enum Value {
    Int(i64),
    Float(f64),
    Text(String),
    Bool(bool),
    Null,
}

#[derive(Debug, Clone, PartialEq)]
pub enum BinOp {
    Eq, NotEq, Lt, Gt, LtEq, GtEq,
    And, Or,
    Add, Sub, Mul, Div,
}

#[derive(Debug, Clone, PartialEq)]
pub enum DataType { Int, BigInt, Float, Text, Boolean }

/// 投影欄位（帶可選別名）
#[derive(Debug, Clone)]
pub struct ProjectItem {
    pub expr: ScalarExpr,
    pub alias: Option<String>,
}

/// 排序鍵
#[derive(Debug, Clone)]
pub struct SortKey {
    pub expr: ScalarExpr,
    pub ascending: bool,
}

/// 聚合函式
#[derive(Debug, Clone)]
pub enum AggFunc {
    Count,       // COUNT(*)
    CountExpr(ScalarExpr), // COUNT(col)
    Sum(ScalarExpr),
    Avg(ScalarExpr),
    Min(ScalarExpr),
    Max(ScalarExpr),
}

#[derive(Debug, Clone)]
pub struct AggItem {
    pub func: AggFunc,
    pub alias: String,
}

/// Logical Plan 的核心：一個遞迴 enum，每個 variant 是一個關聯代數運算子
#[derive(Debug, Clone)]
pub enum LogicalPlan {
    /// TableScan：從 Catalog 掃一個 table（葉節點）
    TableScan {
        table_name: String,
        alias: String,                         // 查詢裡的別名
    },

    /// σ：Selection，過濾 rows
    Selection {
        predicate: ScalarExpr,
        input: Box<LogicalPlan>,
    },

    /// π：Projection，選欄
    Projection {
        items: Vec<ProjectItem>,
        input: Box<LogicalPlan>,
    },

    /// ⋈：Join（inner equi-join）
    Join {
        condition: ScalarExpr,                 // ON 子句
        left: Box<LogicalPlan>,
        right: Box<LogicalPlan>,
    },

    /// γ：Aggregation
    Aggregate {
        group_by: Vec<ScalarExpr>,
        aggregates: Vec<AggItem>,
        input: Box<LogicalPlan>,
    },

    /// τ：Sort
    Sort {
        keys: Vec<SortKey>,
        input: Box<LogicalPlan>,
    },

    /// Limit
    Limit {
        count: u64,
        input: Box<LogicalPlan>,
    },
}

impl LogicalPlan {
    /// 印出可讀的計畫樹（縮排格式）
    pub fn explain(&self, depth: usize) {
        let indent = "  ".repeat(depth);
        match self {
            LogicalPlan::TableScan { table_name, alias } => {
                println!("{}TableScan: {} AS {}", indent, table_name, alias);
            }
            LogicalPlan::Selection { predicate, input } => {
                println!("{}Selection: {:?}", indent, predicate);
                input.explain(depth + 1);
            }
            LogicalPlan::Projection { items, input } => {
                let cols: Vec<_> = items.iter().map(|i| {
                    if let Some(a) = &i.alias { a.clone() }
                    else { format!("{:?}", i.expr) }
                }).collect();
                println!("{}Projection: [{}]", indent, cols.join(", "));
                input.explain(depth + 1);
            }
            LogicalPlan::Join { condition, left, right } => {
                println!("{}Join: {:?}", indent, condition);
                left.explain(depth + 1);
                right.explain(depth + 1);
            }
            LogicalPlan::Aggregate { group_by, aggregates, input } => {
                println!("{}Aggregate: group_by={} aggs={}", indent, group_by.len(), aggregates.len());
                input.explain(depth + 1);
            }
            LogicalPlan::Sort { keys, input } => {
                let key_strs: Vec<_> = keys.iter().map(|k| {
                    format!("{:?} {}", k.expr, if k.ascending { "ASC" } else { "DESC" })
                }).collect();
                println!("{}Sort: [{}]", indent, key_strs.join(", "));
                input.explain(depth + 1);
            }
            LogicalPlan::Limit { count, input } => {
                println!("{}Limit: {}", indent, count);
                input.explain(depth + 1);
            }
        }
    }
}
```

## AST → Logical Plan：Planner 實作（完整可編譯）

```rust
/// 把 Bound AST（Ch 26 的輸出）轉換成 Logical Plan Tree
pub struct LogicalPlanner;

impl LogicalPlanner {
    pub fn plan_select(&self, stmt: &BoundSelect) -> Result<LogicalPlan, String> {
        // 1. 所有 table scan（葉節點）
        // 先建 FROM 的 scan
        let mut plan = if stmt.from_tables.is_empty() {
            return Err("SELECT without FROM not supported".into());
        } else {
            // 取第一個 table（支援多個 FROM 用 cross join 展開，簡化版）
            let alias = &stmt.from_tables[0];
            LogicalPlan::TableScan {
                table_name: alias.clone(), // 實際應查 catalog 取原名，此處簡化
                alias: alias.clone(),
            }
        };

        // 如果 FROM 有多個 table（implicit cross join），用 join 串起來
        // 注意：真實實作應查 catalog 取 table 原名 vs alias
        for alias in stmt.from_tables.iter().skip(1) {
            plan = LogicalPlan::Join {
                condition: ScalarExpr::Literal(Value::Bool(true)), // cross join
                left: Box::new(plan),
                right: Box::new(LogicalPlan::TableScan {
                    table_name: alias.clone(),
                    alias: alias.clone(),
                }),
            };
        }

        // 2. JOIN（顯式 JOIN ... ON）
        for join in &stmt.joins {
            let join_scan = LogicalPlan::TableScan {
                table_name: join.table_alias.clone(),
                alias: join.table_alias.clone(),
            };
            let condition = typed_expr_to_scalar(&join.on);
            plan = LogicalPlan::Join {
                condition,
                left: Box::new(plan),
                right: Box::new(join_scan),
            };
        }

        // 3. WHERE → Selection
        if let Some(pred) = &stmt.where_clause {
            plan = LogicalPlan::Selection {
                predicate: typed_expr_to_scalar(pred),
                input: Box::new(plan),
            };
        }

        // 4. Projection（SELECT 欄位）
        let items: Vec<ProjectItem> = stmt.projections.iter().map(|p| ProjectItem {
            expr: typed_expr_to_scalar(&p.expr),
            alias: p.alias.clone(),
        }).collect();
        plan = LogicalPlan::Projection { items, input: Box::new(plan) };

        // 5. LIMIT
        if let Some(count) = stmt.limit {
            plan = LogicalPlan::Limit { count, input: Box::new(plan) };
        }

        Ok(plan)
    }
}

/// TypedExpr（Ch 26）→ ScalarExpr（本章）的轉換
/// 兩者結構相似，這裡做一個直接對應
fn typed_expr_to_scalar(expr: &TypedExpr) -> ScalarExpr {
    match expr {
        TypedExpr::Int(n)   => ScalarExpr::Literal(Value::Int(*n)),
        TypedExpr::Float(f) => ScalarExpr::Literal(Value::Float(*f)),
        TypedExpr::Str(s)   => ScalarExpr::Literal(Value::Text(s.clone())),
        TypedExpr::Bool(b)  => ScalarExpr::Literal(Value::Bool(*b)),
        TypedExpr::Null     => ScalarExpr::Literal(Value::Null),
        TypedExpr::Column(c) => ScalarExpr::ColumnRef {
            table: c.table_alias.clone(),
            column: c.column_name.clone(),
            data_type: c.data_type.clone(),
        },
        TypedExpr::IsNull { expr, negated } => ScalarExpr::IsNull {
            expr: Box::new(typed_expr_to_scalar(expr)),
            negated: *negated,
        },
        TypedExpr::BinOp { op, left, right, .. } => ScalarExpr::BinOp {
            op: op.clone(),
            left: Box::new(typed_expr_to_scalar(left)),
            right: Box::new(typed_expr_to_scalar(right)),
        },
    }
}
```

## 完整可編譯的 main：端到端展示

```rust
fn main() {
    // 模擬 Ch 26 binder 產出的 BoundSelect
    // （實際使用應接 Lexer → Parser → Binder 的完整 pipeline）
    let bound = BoundSelect {
        projections: vec![
            BoundSelectItem {
                expr: TypedExpr::Column(ResolvedColumn {
                    table_alias: "e".into(),
                    column_name: "name".into(),
                    data_type: DataType::Text,
                    ordinal: 1,
                }),
                alias: None,
            },
            BoundSelectItem {
                expr: TypedExpr::Column(ResolvedColumn {
                    table_alias: "d".into(),
                    column_name: "dept_name".into(),
                    data_type: DataType::Text,
                    ordinal: 1,
                }),
                alias: None,
            },
        ],
        from_tables: vec!["e".to_string()],
        joins: vec![BoundJoin {
            table_alias: "d".to_string(),
            on: TypedExpr::BinOp {
                op: BinOp::Eq,
                left: Box::new(TypedExpr::Column(ResolvedColumn {
                    table_alias: "e".into(), column_name: "dept_id".into(),
                    data_type: DataType::Int, ordinal: 3,
                })),
                right: Box::new(TypedExpr::Column(ResolvedColumn {
                    table_alias: "d".into(), column_name: "id".into(),
                    data_type: DataType::Int, ordinal: 0,
                })),
                result_type: DataType::Boolean,
            },
        }],
        where_clause: Some(TypedExpr::BinOp {
            op: BinOp::Gt,
            left: Box::new(TypedExpr::Column(ResolvedColumn {
                table_alias: "e".into(), column_name: "salary".into(),
                data_type: DataType::Float, ordinal: 2,
            })),
            right: Box::new(TypedExpr::Float(100000.0)),
            result_type: DataType::Boolean,
        }),
        limit: Some(10),
    };

    let planner = LogicalPlanner;
    match planner.plan_select(&bound) {
        Ok(plan) => {
            println!("=== Logical Plan ===");
            plan.explain(0);
        }
        Err(e) => eprintln!("Planning error: {}", e),
    }
}
```

執行 `cargo run` 的預期輸出：

```
=== Logical Plan ===
Limit: 10
  Projection: [ColumnRef { table: "e", column: "name", ... }, ColumnRef { table: "d", ... }]
    Selection: BinOp { op: Gt, left: ColumnRef { table: "e", column: "salary" }, right: Literal(Float(100000.0)) }
      Join: BinOp { op: Eq, left: ColumnRef { table: "e", column: "dept_id" }, right: ColumnRef { table: "d", column: "id" } }
        TableScan: e AS e
        TableScan: d AS d
```

## 謂詞下推預覽：等效變換在 Logical 層做

```rust
impl LogicalPlan {
    /// 簡易謂詞下推：把 Selection 推到 join 下面（如果謂詞只依賴一側的 table）
    pub fn push_down_selection(self) -> LogicalPlan {
        match self {
            LogicalPlan::Selection { predicate, input } => {
                match *input {
                    LogicalPlan::Join { condition, left, right } => {
                        // 分析謂詞依賴哪些 table
                        let left_tables = collect_tables(&left);
                        let pred_tables = collect_pred_tables(&predicate);

                        if pred_tables.iter().all(|t| left_tables.contains(t)) {
                            // 謂詞只依賴 left 側，推下去
                            LogicalPlan::Join {
                                condition,
                                left: Box::new(LogicalPlan::Selection {
                                    predicate,
                                    input: left,
                                }),
                                right,
                            }
                        } else {
                            // 謂詞跨兩側或只依賴右側，暫不移動
                            LogicalPlan::Selection {
                                predicate,
                                input: Box::new(LogicalPlan::Join { condition, left, right }),
                            }
                        }
                    }
                    other => LogicalPlan::Selection {
                        predicate,
                        input: Box::new(other),
                    },
                }
            }
            // 對其他節點的子樹遞迴處理
            LogicalPlan::Join { condition, left, right } => LogicalPlan::Join {
                condition,
                left: Box::new(left.push_down_selection()),
                right: Box::new(right.push_down_selection()),
            },
            other => other, // 其他節點直接回傳
        }
    }
}

fn collect_tables(plan: &LogicalPlan) -> Vec<String> {
    match plan {
        LogicalPlan::TableScan { alias, .. } => vec![alias.clone()],
        LogicalPlan::Join { left, right, .. } => {
            let mut t = collect_tables(left);
            t.extend(collect_tables(right));
            t
        }
        LogicalPlan::Selection { input, .. } | LogicalPlan::Projection { input, .. } => {
            collect_tables(input)
        }
        _ => vec![],
    }
}

fn collect_pred_tables(expr: &ScalarExpr) -> Vec<String> {
    match expr {
        ScalarExpr::ColumnRef { table, .. } => vec![table.clone()],
        ScalarExpr::BinOp { left, right, .. } => {
            let mut t = collect_pred_tables(left);
            t.extend(collect_pred_tables(right));
            t
        }
        ScalarExpr::IsNull { expr, .. } => collect_pred_tables(expr),
        _ => vec![],
    }
}
```

## 對比表格：六個 Logical Operator

| 運算子 | 符號 | SQL 對應 | 輸入數 | 是否 blocking？ |
|--------|------|----------|--------|----------------|
| Selection | σ | WHERE | 1 | 否（pipeline） |
| Projection | π | SELECT cols | 1 | 否（pipeline） |
| Join | ⋈ | JOIN ... ON | 2 | 依實作（hash join 是） |
| Aggregation | γ | GROUP BY | 1 | 是（需要全部資料） |
| Sort | τ | ORDER BY | 1 | 是（需要全部資料） |
| Limit | — | LIMIT | 1 | 否（早停） |

**Blocking**（阻塞）：必須讀完所有輸入才能產出第一個輸出 row 的算子。Sort 和 Aggregation 都是 blocking 的——這是它們代價高的原因。

## 踩雷

1. **WHERE 子句並非只有 Selection**。`WHERE e.dept_id = d.id` 這樣的條件在 SQL 裡寫在 WHERE，但在關聯代數裡是 Join 的 condition，不是 Selection。正確做法：把 WHERE 子句拆分——純屬一個 table 的條件是 Selection（謂詞下推），跨兩個 table 的 equi-join 條件是 Join condition。很多人第一次建 logical plan 時把所有 WHERE 都做成 Selection，然後 join 結果無法正確過濾。

2. **Projection 應該在 Join 之後還是之前**？Projection 只保留需要的欄。直覺上越早做越好（少搬資料），但如果 join 的 ON 條件需要某個欄，而 projection 把那欄去掉了，就壞了。正確做法：Logical Plan 裡先把 Projection 放在 root 附近，讓 optimizer 的 projection pushdown 規則安全地把它下推——只下推「下方算子不需要的欄」才安全。

3. **LogicalPlan 必須是樹，不能是 DAG（有向無環圖）**。如果同一個 table 在查詢裡出現兩次（self-join），要建兩個獨立的 `TableScan` 節點，不能讓兩個父節點指向同一個子節點。Rust 的 `Box<LogicalPlan>` 天然是樹形（所有權唯一），不會有這個問題；但如果你改用 `Rc<LogicalPlan>` 或 `Arc`，就要小心了。

4. **Sort + Limit 的組合（Top-K）**。`ORDER BY ... LIMIT N` 理論上是先 Sort 再 Limit，但最優的物理實作是 Top-K heap，不需要完整排序。這個優化在 Logical Plan 層無法表示（因為 Logical Plan 沒有演算法細節），要在 Physical Planner 層識別 `Sort → Limit` 模式並替換成 `TopK`。

5. **子查詢（subquery）的 decorrelation**。`WHERE dept_id IN (SELECT id FROM departments WHERE ...)` 裡的子查詢在 AST 裡是嵌套結構，直接翻譯到 Logical Plan 會得到一個嵌套的 Exists/In 節點，這很難高效執行。工業級資料庫會做 subquery decorrelation（去關聯化），把它轉換成 semi-join。這個轉換在 Logical Plan 層做，但需要相當複雜的規則。我們的實作不處理子查詢。

## 進階延伸

**等效代數規則的完整集合**：Ramakrishnan & Gehrke 的《Database Management Systems》第 15 章列出了所有關聯代數的等效規則（selection cascade、join commutativity/associativity 等）。理解這些規則就理解了 optimizer 能做哪些變換，以及為什麼某些 SQL 寫法效能不同。

**Memo / ONNX 結構**：Cascades 框架把每個 Logical Plan 節點的所有等效表示存在 memo table 裡。`plan_a ≡ plan_b` 表示它們語義等效，優化器在 memo 裡探索所有等效形態，選 cost 最低的。DuckDB 的 optimizer 用的就是 Cascades 架構。

**Lateral Join / Subquery**：PostgreSQL 的 LATERAL 關鍵字讓子查詢可以引用外部 FROM 子句的 table，這在 Logical Plan 裡需要特殊處理（dependent join），因為右側子樹的結果依賴左側產出的每一 row。

## 本章重點整理

- 關聯代數六個核心運算子：σ（Selection）、π（Projection）、⋈（Join）、γ（Aggregation）、τ（Sort）、Limit
- Logical Plan Tree 是這些運算子的組合，描述「要做什麼」，不涉及「怎麼做」
- AST → Logical Plan 的轉換（Planner）：FROM→TableScan, JOIN→Join, WHERE→Selection, SELECT→Projection, LIMIT→Limit
- 謂詞下推是 Logical Plan 層的等效變換，把 Selection 推近 TableScan，減少 Join 的輸入量
- Logical 與 Physical 分離的核心價值：同一份 Logical Plan 可以對應多個合法的 Physical Plan，優化器只需要在 Logical 層探索等效變換

## 自我檢核

- [ ] 我能用 σ、π、⋈ 符號表示一個 SELECT...FROM...WHERE...JOIN 查詢
- [ ] 我能畫出 `SELECT a FROM t1 JOIN t2 ON t1.id=t2.fk WHERE t1.x > 5` 的 Logical Plan Tree
- [ ] 我能解釋謂詞下推（predicate pushdown）的等效性——為什麼推下去結果不變？
- [ ] 我能說出 Sort 和 Aggregation 為什麼是 blocking 算子，以及對執行的影響

## 延伸閱讀

1. **CMU 15-445 Lecture 14（Query Planning II）**
   讀什麼：Logical → Physical plan 的轉換流程、join ordering 問題的規模（n 個 table 有 n! 種 join 順序）、為什麼 cost-based 優化器必要
   關聯：本章建好 Logical Plan 之後，Ch 32–33 要做的事的全景；看 Andy Pavlo 解釋為什麼 join reordering 是 NP-hard

2. **《Database Internals》Part II Overview（分散式查詢）**
   讀什麼：Logical Plan 在分散式環境下的延伸——新增 Exchange 算子表示資料在節點間的移動
   關聯：了解 Logical Plan 的抽象如何能延伸到分散式場景，只需加新的 operator 而不改動整個框架

3. **《Readings in Database Systems》（Red Book）— Volcano / Cascades paper**（Goetz Graefe, 1993/1995）
   讀什麼：Volcano 模型和 Cascades 框架的原始論文，這是現代優化器架構的起點
   關聯：Ch 32–33 的優化器架構直接來自這些論文；先看本章的 Logical Plan 概念再讀論文會好理解很多

4. **DuckDB blog：[Unnesting Subqueries](https://duckdb.org/2023/05/26/optimizing-join-order.html)**
   讀什麼：DuckDB 怎麼把 correlated subquery 轉成 join（decorrelation）——這是 Logical Plan 層最複雜的等效變換之一
   關聯：踩雷第 5 條（子查詢 decorrelation）的工業級實作，讀完對「Logical Plan 能做什麼變換」的邊界有清晰認識

→ [Ch 28 Physical Plan](./28-physical-plan.md)
