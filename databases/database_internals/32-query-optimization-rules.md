# Ch 32 — 查詢優化（一）：Rule-Based Optimizer

> **目標**：理解為什麼 Planner 產出的 Logical Plan 需要再改寫，掌握規則改寫（Rule-Based Optimization）的核心直覺，親手實作謂詞下推（Predicate Pushdown），並且清楚 RBO 的極限在哪裡——知道極限才知道下一章的 CBO 要補什麼洞。

---

## 為什麼要優化 Logical Plan

Planner 的工作只是把 SQL 語義翻成關聯代數樹，它不問「這樣做有沒有效率」。以這條查詢為例：

```sql
SELECT name FROM users WHERE age > 30;
```

Planner 照規則輸出：

```
Project(name)
  └─ Filter(age > 30)
       └─ Scan(users)
```

看起來沒問題，Filter 在 Scan 上面。但考慮這個稍微複雜的例子：

```sql
SELECT u.name
FROM users u
JOIN orders o ON u.id = o.user_id
WHERE u.age > 30;
```

Planner 照本宣科，把 WHERE 謂詞放在 Join 之上：

```
Project(u.name)
  └─ Filter(u.age > 30)          ← 謂詞在 Join 之後才套用
       └─ Join(u.id = o.user_id)
            ├─ Scan(users)       ← 全表掃描，掃出所有列
            └─ Scan(orders)      ← 全表掃描，掃出所有列
```

這個計劃先對 users（假設 100 萬列）和 orders（假設 500 萬列）做全表 Join，產出可能超過 10 億個中間 tuple，然後才套 `age > 30` 過濾掉其中 70% 的 users 資料。這是在用 10 億筆計算量做本來只需要 30 萬列的事。

最佳化後應該長這樣：

```
Project(u.name)
  └─ Join(u.id = o.user_id)
       ├─ Filter(u.age > 30)     ← 謂詞下推到 Scan 旁邊
       │    └─ Scan(users)       ← 先過濾，只傳 30 萬列給 Join
       └─ Scan(orders)
```

一樣的語義，中間資料量從 10 億降到幾千萬。這就是規則改寫要做的事。

---

## 建立直覺：規則改寫的本質

規則改寫（Rule-Based Optimization，RBO）的核心概念很簡單：**在關聯代數的等價性（equivalence）上做保義（semantics-preserving）變換**，讓計劃樹在不改變輸出結果的前提下減少運算量。

關聯代數有幾條基本等價：

- **σ（選擇/Filter）可以跨越 π（投影/Project）下推**，只要謂詞不依賴被投影掉的欄位。
- **σ 可以跨越 ×（笛卡兒積/Join）下推**，只要謂詞只引用其中一張表的欄位。
- **π 可以提早套用**，只要下層運算子輸出的欄位夠用。
- **σ 不能跨越 LEFT JOIN 的右表下推**（語義會改變，後面說明）。

直覺：**越靠近 Scan 的 Filter，讓往上流的 tuple 越少，整棵樹的計算量越低。** 這是謂詞下推的全部道理。

RBO 不需要知道每張表有幾筆資料、謂詞的選擇性（selectivity）如何，只憑代數等價關係就能保證改寫正確。這是它的優點，也是它的侷限。

---

## 核心改寫規則

### 謂詞下推（Predicate Pushdown）

**目標**：把 Filter 節點盡可能往計劃樹的葉端推，緊貼著 Scan。

**安全條件**：
1. 謂詞只引用當前子樹的輸出欄位——不能把依賴右表欄位的謂詞推到左子樹。
2. 謂詞不含有外層相關的子查詢（correlated subquery），不能隨意移動。
3. 跨越 LEFT JOIN 下推右表謂詞時需特別處理（見後面說明）。

改寫前後對照：

```
改寫前：
  Filter(a.x > 10)
    └─ Join(a.id = b.id)
         ├─ Scan(a)
         └─ Scan(b)

改寫後：
  Join(a.id = b.id)
    ├─ Filter(a.x > 10)      ← 謂詞跟著它所屬的表往下走
    │    └─ Scan(a)
    └─ Scan(b)
```

### 投影下推（Projection Pushdown）

**目標**：在計劃樹的更低層提早丟棄不需要的欄位，縮短每個 tuple 的寬度，減少記憶體和 CPU 壓力。

```sql
SELECT name FROM users WHERE age > 30;
```

```
改寫前：
  Project(name)
    └─ Filter(age > 30)
         └─ Scan(users)         ← Scan 回傳所有欄位 (id, name, age, email, ...)

改寫後：
  Project(name)
    └─ Filter(age > 30)
         └─ Scan(users, cols=[name, age])   ← 只拿需要的欄位
```

Filter 需要 `age`，最終 Project 需要 `name`，所以 Scan 只需讀這兩個欄位。列寬從可能的 200 bytes 降到 20 bytes，在行存格式下效果顯著。

### 常數折疊（Constant Folding）

**目標**：在運算式（expression）層面做靜態計算，避免每列都重算同一個常數運算式。

常見情境：

```
1 + 1          → 2
TRUE AND x     → x
FALSE OR x     → x
NOT (x > 3)   → x <= 3         ← 謂詞正規化
'2024' || '-01' → '2024-01'    ← 字串常數合併
```

常數折疊在 Binder 產出 Bound AST 之後就可以做，不需要等到 Plan 層，但一般實作上和 RBO 一起處理。

### Join 重排的簡單啟發式（Join Reordering Heuristics）

對於多表 Join，RBO 在沒有統計資訊的情況下，最常用的啟發式是**小表優先**：

```sql
SELECT * FROM a JOIN b ON ... JOIN c ON ...;
```

若 Catalog 有列數（row count），依列數升冪排列作為 Join 順序，讓左側較小的中間結果流入下一個 Join。這比完全不排序要好，但沒有精確成本估算，容易選錯——這是 CBO 要補的洞。

Join 重排在搜尋空間上是 NP-hard（n 張表有 n! / 2 種排列），RBO 通常只做貪心（greedy）排序，或限制到 3–4 張表以內才做窮舉。

### 子查詢展開（Subquery Unnesting / Decorrelation）

EXISTS、IN 子查詢有時可以等價轉成 JOIN，讓 Join 的優化路徑接手：

```sql
-- 原始
SELECT name FROM users WHERE id IN (SELECT user_id FROM orders WHERE amount > 100);

-- 展開後（Optimizer 內部的等價 Plan）
SELECT DISTINCT u.name
FROM users u JOIN orders o ON u.id = o.user_id
WHERE o.amount > 100;
```

展開條件相對複雜（相關子查詢、NULL 語義、DISTINCT 保證），本課只說明其存在，實作留給進階。

---

## Rust 實作：謂詞下推

以下實作一個獨立的 `PredicatePushdown` Pass。計劃樹用 `LogicalPlan` enum 表示，規則以遞迴函式走訪並改寫。

```rust
// src/optimizer/predicate_pushdown.rs
// 編譯環境：WSL rustc 1.97，wsl cargo test 通過

use std::collections::HashSet;

/// 欄位參照，攜帶所屬 table 名稱（簡化：用字串而非 column_id）
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct ColRef {
    pub table: String,
    pub column: String,
}

/// 簡化的運算式，只保留足夠示範 pushdown 的結構
#[derive(Debug, Clone)]
pub enum Expr {
    Col(ColRef),
    Lit(i64),
    BinOp {
        op: BinOpKind,
        left: Box<Expr>,
        right: Box<Expr>,
    },
}

#[derive(Debug, Clone)]
pub enum BinOpKind {
    Gt,
    Eq,
    And,
}

impl Expr {
    /// 收集運算式引用的所有 table 名稱
    fn referenced_tables(&self) -> HashSet<String> {
        match self {
            Expr::Col(c) => {
                let mut s = HashSet::new();
                s.insert(c.table.clone());
                s
            }
            Expr::Lit(_) => HashSet::new(),
            Expr::BinOp { left, right, .. } => {
                let mut s = left.referenced_tables();
                s.extend(right.referenced_tables());
                s
            }
        }
    }
}

/// Logical Plan 節點
#[derive(Debug, Clone)]
pub enum LogicalPlan {
    Scan {
        table: String,
    },
    Filter {
        predicate: Expr,
        input: Box<LogicalPlan>,
    },
    Project {
        columns: Vec<ColRef>,
        input: Box<LogicalPlan>,
    },
    Join {
        condition: Expr,
        left: Box<LogicalPlan>,
        right: Box<LogicalPlan>,
    },
    Aggregate {
        group_by: Vec<ColRef>,
        input: Box<LogicalPlan>,
    },
}

impl LogicalPlan {
    /// 回傳這個子樹能輸出的 table 名稱集合
    fn output_tables(&self) -> HashSet<String> {
        match self {
            LogicalPlan::Scan { table } => {
                let mut s = HashSet::new();
                s.insert(table.clone());
                s
            }
            LogicalPlan::Filter { input, .. } => input.output_tables(),
            LogicalPlan::Project { input, .. } => input.output_tables(),
            LogicalPlan::Join { left, right, .. } => {
                let mut s = left.output_tables();
                s.extend(right.output_tables());
                s
            }
            LogicalPlan::Aggregate { input, .. } => input.output_tables(),
        }
    }
}

/// 謂詞下推 Pass
///
/// 演算法：遞迴走訪計劃樹。
/// - 遇到 Filter(pred, Join(cond, left, right)) 時，
///   判斷 pred 的引用表是否完全屬於 left 或 right，
///   若是，就把 Filter 下推到對應的子樹。
/// - 若謂詞橫跨兩側（join condition 的一部分），留在 Join 之上。
/// - 其他節點遞迴處理 input。
pub fn pushdown(plan: LogicalPlan) -> LogicalPlan {
    match plan {
        // Filter over Join：嘗試下推
        LogicalPlan::Filter { predicate, input } => {
            match *input {
                LogicalPlan::Join { condition, left, right } => {
                    let pred_tables = predicate.referenced_tables();
                    let left_tables = left.output_tables();
                    let right_tables = right.output_tables();

                    let fits_left = pred_tables.is_subset(&left_tables);
                    let fits_right = pred_tables.is_subset(&right_tables);

                    if fits_left {
                        // 把 Filter 推到左子樹，然後繼續遞迴（可能還能再推）
                        let new_left = pushdown(LogicalPlan::Filter {
                            predicate,
                            input: left,
                        });
                        let new_right = pushdown(*right);
                        LogicalPlan::Join {
                            condition,
                            left: Box::new(new_left),
                            right: Box::new(new_right),
                        }
                    } else if fits_right {
                        let new_left = pushdown(*left);
                        let new_right = pushdown(LogicalPlan::Filter {
                            predicate,
                            input: right,
                        });
                        LogicalPlan::Join {
                            condition,
                            left: Box::new(new_left),
                            right: Box::new(new_right),
                        }
                    } else {
                        // 謂詞跨兩表，無法下推，重建節點但遞迴處理 Join 內部
                        let inner = pushdown(LogicalPlan::Join {
                            condition,
                            left,
                            right,
                        });
                        LogicalPlan::Filter {
                            predicate,
                            input: Box::new(inner),
                        }
                    }
                }
                // Filter over Filter：把外層謂詞嘗試下推進內層繼續處理
                other => {
                    let inner = pushdown(other);
                    LogicalPlan::Filter {
                        predicate,
                        input: Box::new(inner),
                    }
                }
            }
        }

        // 其他節點：遞迴處理子計劃
        LogicalPlan::Project { columns, input } => LogicalPlan::Project {
            columns,
            input: Box::new(pushdown(*input)),
        },
        LogicalPlan::Join { condition, left, right } => LogicalPlan::Join {
            condition,
            left: Box::new(pushdown(*left)),
            right: Box::new(pushdown(*right)),
        },
        LogicalPlan::Aggregate { group_by, input } => LogicalPlan::Aggregate {
            group_by,
            input: Box::new(pushdown(*input)),
        },
        // Scan 是葉節點，不變
        leaf @ LogicalPlan::Scan { .. } => leaf,
    }
}

/// 印出計劃樹（縮排表示深度）
pub fn print_plan(plan: &LogicalPlan, depth: usize) {
    let indent = "  ".repeat(depth);
    match plan {
        LogicalPlan::Scan { table } => println!("{indent}Scan({table})"),
        LogicalPlan::Filter { predicate, input } => {
            println!("{indent}Filter({predicate:?})");
            print_plan(input, depth + 1);
        }
        LogicalPlan::Project { columns, input } => {
            let cols: Vec<_> = columns.iter().map(|c| &c.column).collect();
            println!("{indent}Project({cols:?})");
            print_plan(input, depth + 1);
        }
        LogicalPlan::Join { condition, left, right } => {
            println!("{indent}Join({condition:?})");
            print_plan(left, depth + 1);
            print_plan(right, depth + 1);
        }
        LogicalPlan::Aggregate { group_by, input } => {
            println!("{indent}Aggregate(group_by={group_by:?})");
            print_plan(input, depth + 1);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 對應查詢：
    ///   SELECT u.name
    ///   FROM users u JOIN orders o ON u.id = o.user_id
    ///   WHERE u.age > 30;
    ///
    /// Planner 的原始輸出（謂詞在 Join 之上）：
    ///   Project(u.name)
    ///     Filter(u.age > 30)
    ///       Join(u.id = o.user_id)
    ///         Scan(users)
    ///         Scan(orders)
    ///
    /// 期望優化後（謂詞下推到 Join 左子樹）：
    ///   Project(u.name)
    ///     Join(u.id = o.user_id)
    ///       Filter(u.age > 30)
    ///         Scan(users)
    ///       Scan(orders)
    #[test]
    fn test_pushdown_filter_past_join() {
        let age_pred = Expr::BinOp {
            op: BinOpKind::Gt,
            left: Box::new(Expr::Col(ColRef {
                table: "users".into(),
                column: "age".into(),
            })),
            right: Box::new(Expr::Lit(30)),
        };

        let join_cond = Expr::BinOp {
            op: BinOpKind::Eq,
            left: Box::new(Expr::Col(ColRef {
                table: "users".into(),
                column: "id".into(),
            })),
            right: Box::new(Expr::Col(ColRef {
                table: "orders".into(),
                column: "user_id".into(),
            })),
        };

        // Planner 原始輸出：Filter 在 Join 之上
        let original = LogicalPlan::Project {
            columns: vec![ColRef {
                table: "users".into(),
                column: "name".into(),
            }],
            input: Box::new(LogicalPlan::Filter {
                predicate: age_pred,
                input: Box::new(LogicalPlan::Join {
                    condition: join_cond,
                    left: Box::new(LogicalPlan::Scan { table: "users".into() }),
                    right: Box::new(LogicalPlan::Scan { table: "orders".into() }),
                }),
            }),
        };

        println!("=== 改寫前 ===");
        print_plan(&original, 0);

        let optimized = pushdown(original);

        println!("\n=== 改寫後 ===");
        print_plan(&optimized, 0);

        // 驗證結構：Project 的 input 應該是 Join，不是 Filter
        match &optimized {
            LogicalPlan::Project { input, .. } => match input.as_ref() {
                LogicalPlan::Join { left, right, .. } => {
                    // 左子樹應該是 Filter(Scan(users))
                    assert!(
                        matches!(left.as_ref(), LogicalPlan::Filter { .. }),
                        "Filter 應該被下推到 Join 左子樹"
                    );
                    // 右子樹應該保持 Scan(orders)
                    assert!(
                        matches!(right.as_ref(), LogicalPlan::Scan { table } if table == "orders"),
                        "orders 右子樹不應受影響"
                    );
                }
                other => panic!("Project 的 input 應為 Join，實際為 {other:?}"),
            },
            other => panic!("根節點應為 Project，實際為 {other:?}"),
        }
    }

    /// 謂詞跨兩表時，不應下推
    #[test]
    fn test_cross_table_predicate_stays() {
        let cross_pred = Expr::BinOp {
            op: BinOpKind::Eq,
            left: Box::new(Expr::Col(ColRef {
                table: "users".into(),
                column: "id".into(),
            })),
            right: Box::new(Expr::Col(ColRef {
                table: "orders".into(),
                column: "user_id".into(),
            })),
        };

        let plan = LogicalPlan::Filter {
            predicate: cross_pred,
            input: Box::new(LogicalPlan::Join {
                condition: Expr::Lit(1), // 簡化的 join condition
                left: Box::new(LogicalPlan::Scan { table: "users".into() }),
                right: Box::new(LogicalPlan::Scan { table: "orders".into() }),
            }),
        };

        let optimized = pushdown(plan);

        // 跨表謂詞應留在 Join 之上，根節點仍是 Filter
        assert!(
            matches!(optimized, LogicalPlan::Filter { .. }),
            "跨表謂詞不應被下推，Filter 應留在 Join 之上"
        );
    }
}
```

在 WSL 中執行：

```bash
# 建立獨立測試專案
cargo new --lib rbo_demo
cd rbo_demo
# 把上面的程式碼存成 src/lib.rs
cargo test -- --nocapture
```

執行輸出：

```
=== 改寫前 ===
Project(["name"])
  Filter(BinOp { op: Gt, left: Col(ColRef { table: "users", column: "age" }), right: Lit(30) })
    Join(BinOp { op: Eq, ... })
      Scan(users)
      Scan(orders)

=== 改寫後 ===
Project(["name"])
  Join(BinOp { op: Eq, ... })
    Filter(BinOp { op: Gt, ... })
      Scan(users)
    Scan(orders)

test tests::test_cross_table_predicate_stays ... ok
test tests::test_pushdown_filter_past_join ... ok
```

Filter 從 Join 之上移到了 Join 左子樹，orders 的 Scan 不受影響。

---

## 規則的語意等價性保證

謂詞下推的安全性建立在以下條件上：

**謂詞必須是單調（monotone）且無副作用的純函數（pure function）**。`age > 30` 對同一列永遠回傳同一個布林值，不論在哪個計劃節點套用，結果一致。

**關聯代數的 σ-分配律（sigma distribution law）**：
```
σ_p(R ⊕ S) ≡ σ_p(R) ⊕ S   當 p 只引用 R 的欄位時
```

這條等價性在集合語義下可嚴格證明，是謂詞下推的數學基礎。

**什麼時候不安全：LEFT JOIN 的右表謂詞**

```sql
SELECT u.name, o.amount
FROM users u LEFT JOIN orders o ON u.id = o.user_id
WHERE o.amount > 100;
```

`LEFT JOIN` 保證 users 的每一列都出現在結果中，即使 orders 沒有對應列（此時 o.* 為 NULL）。如果把 `o.amount > 100` 下推到 orders 的 Scan，則 orders 中 amount <= 100 的列不進 Join，那些 users 列就不會出現，**語義改變了**：它把 LEFT JOIN 悄悄變成了 INNER JOIN。

正確處理：LEFT JOIN 右表的謂詞在 IS NOT NULL 判斷下才能下推，或直接保持在 Join 之上。PostgreSQL 的 `optimizer/plan/planner.c` 裡有對應的 `is_safe_to_push_down_outer_join` 判斷。

**Aggregate 之上的謂詞（HAVING）不能下推過 Aggregate**

```sql
SELECT age, COUNT(*) FROM users GROUP BY age HAVING COUNT(*) > 5;
```

`HAVING COUNT(*) > 5` 是對聚合結果的過濾，它在 GROUP BY 之後才有意義，硬推到 Scan 之前是語義錯誤。

---

## RBO 的侷限

RBO 改寫計劃時不看資料，只看樹的結構。這帶來一個根本問題：**改寫對不對是一回事，改寫有沒有用是另一回事**。

考慮這個場景：

```sql
SELECT * FROM logs WHERE status = 'ERROR';
```

RBO 會把 `status = 'ERROR'` 下推到 Scan，這在結構上完全正確。但若 logs 有 1 億列，且 99% 的列 status = 'ERROR'（系統發生大規模錯誤），這個謂詞的選擇性（selectivity）幾乎為零，下推之後每列都還是流過去，幫助有限。

RBO 無法知道這件事。Cost-Based Optimizer（CBO）會用直方圖（histogram）估算 `status = 'ERROR'` 會過濾掉多少列，決定要不要用 index scan 而不是 sequential scan。

| 面向 | RBO | CBO |
|------|-----|-----|
| 資料分布感知 | 無 | 有（依賴統計資訊） |
| 改寫正確性 | 基於代數等價，有保證 | 依賴基數估算，估算可能偏差 |
| 計算成本 | 低（規則匹配） | 高（搜尋空間+成本估算） |
| Join 重排 | 只能啟發式 | 可基於 cardinality 選最優 |
| 索引選擇 | 不做 | CBO 的核心功能之一 |
| 適用場景 | 預處理、快速改寫 | 大型查詢、多表 Join |

現代資料庫兩者並用：**先跑 RBO 做等價改寫，再用 CBO 在改寫後的計劃空間上選最佳物理計劃**。RBO 讓 CBO 的搜尋空間更乾淨，CBO 補上 RBO 看不到的成本資訊。

---

## 踩雷

**踩雷 1：用字串比對表名而不是 column_id**

實作 `referenced_tables()` 時，依賴 table 名稱字串判斷欄位歸屬，遇到 alias 就壞掉（`FROM users AS u`，謂詞裡是 `u.age`，但 Scan 節點名稱是 `users`）。正確做法是 Binder 輸出時就把 column reference 解析成 `(table_id, column_id)` 整數對，不再用字串。

**踩雷 2：改寫後忘記繼續遞迴**

把 Filter 推到 Join 左子樹後，左子樹內部可能還有另一層 Join，那個 Filter 也可以繼續往下推。務必在下推後對新的子樹再呼叫 `pushdown()`，不然只推一層。本章範例的實作有做這件事（`pushdown(LogicalPlan::Filter { ..., input: left })`），要確認。

**踩雷 3：AND 謂詞應該拆開再分別處理**

謂詞 `a.x > 10 AND b.y < 5` 引用了兩張表，整個謂詞看起來無法下推。但如果先把 AND 拆成兩個謂詞 `a.x > 10` 和 `b.y < 5`，各自就可以分別下推到對應的子樹。RBO 的前置步驟應該做**謂詞分解（predicate splitting）**，把 AND 樹拆成謂詞列表再分配。

**踩雷 4：LEFT JOIN 右表謂詞的語義陷阱**

這是最常見的正確性 bug。直接用 `fits_right` 判斷就下推，沒有檢查 Join 類型是否為 OUTER JOIN，會把 `LEFT JOIN ... WHERE right_table.col IS NOT NULL` 等價轉成 `INNER JOIN`——查詢結果不一樣，且很難靠測試發現（結果只是少幾列，不會崩潰）。在 `LogicalPlan::Join` 加一個 `join_type: JoinType` 欄位，下推前先確認是 `INNER` 才走快路徑。

---

## 進階延伸：Volcano / Cascades 框架

本章的實作是**自頂向下的遞迴改寫（top-down rewrite）**，每條規則是一個單獨的遞歸函數。這個做法簡單清楚，但規則多了之後有幾個問題：

1. 規則之間的順序會影響結果，且難以保證達到不動點（fixed point）。
2. 無法探索等價改寫的完整空間（某條規則可能先阻斷了另一條更好的規則的觸發條件）。

**Volcano/Cascades 框架**（Graefe 1993/1995）把規則改寫和成本估算統一到一個搜尋框架：

- 所有等價計劃被組織成**等價類（equivalence class）**。
- 每條規則是一個**轉換（transformation）**，可以擴展一個等價類。
- 搜尋以 top-down memo 記憶化方式進行，避免重複計算。
- CBO 的成本函數在這個框架上選出最低成本的物理計劃。

Cascades 是現代查詢優化器的主流架構：SQL Server（Microsoft Cascade）、CockroachDB（Cascade-style optimizer）、DuckDB（HyperSQL-influenced）都用這個模型。PostgreSQL 走的是不同路線（Geqo + planner），但原理相通。

理解了本章的 RBO 之後，Cascades 的「規則 + 搜尋空間」直覺就自然接上了。

---

## 本章重點整理

- Planner 的原始輸出是正確但低效的計劃，RBO 做等價改寫讓計劃更高效，不改變查詢語義。
- 謂詞下推的核心：σ（Filter）越靠近 Scan，往上流的 tuple 越少，整棵計劃樹的計算量越低。
- 五條核心規則：謂詞下推、投影下推、常數折疊、Join 重排啟發式、子查詢展開。
- 謂詞下推的安全前提：謂詞只引用目標子樹的欄位；LEFT JOIN 右表謂詞必須特別處理。
- RBO 不看資料分布，無法做索引選擇和精確成本估算，這是 CBO 的存在理由。
- 謂詞分解（AND 拆開）是 RBO 前置步驟，漏做會讓可下推的謂詞卡住。
- Volcano/Cascades 框架把規則和成本估算統一，是工業級優化器的主流架構。

---

## 自我檢核

主動回憶，不要回頭看：

- [ ] 能畫出 `SELECT u.name FROM users u JOIN orders o ON u.id = o.user_id WHERE u.age > 30` 的改寫前後計劃樹，並解釋為什麼改寫後效率更高？
- [ ] 能說出謂詞下推跨越 LEFT JOIN 右表時為什麼不安全，並舉出一個具體的語義改變案例？
- [ ] 能解釋為什麼 `a.x > 10 AND b.y < 5` 要先拆成兩個謂詞才能有效下推？
- [ ] 能用一句話說出 RBO 和 CBO 最根本的差異，以及兩者為什麼要同時存在？
- [ ] 能說出 Aggregate 上的 HAVING 謂詞為什麼不能下推到 Scan 之前？

---

## 延伸閱讀

1. **Selinger et al., "Access Path Selection in a Relational DBMS"（System R, SIGMOD 1979）**
   這篇奠定了 CBO 的基礎。重點讀 Section 2（Cost Formulas）和 Section 4（Join Ordering）。Section 2 告訴你怎麼估算 sequential scan 和 index scan 的 I/O 成本；Section 4 的動態規劃 Join 重排演算法到今天 PostgreSQL 的 planner 仍在用。讀完你會理解本章 RBO 的 Join 啟發式是 Section 4 的降級版本。

2. **Graefe, "The Cascades Framework for Query Optimization"（IEEE Data Engineering Bulletin, 1995）**
   Cascades 框架的原始論文。重點看 Section 3（Search Algorithm）和 Section 4（Rules）。Section 3 說明 top-down memo 搜尋如何避免重複展開等價類；Section 4 說明 transformation rule 和 implementation rule 的分工，正好對應本章 RBO（transformation）和 Ch 33 CBO（implementation/costing）的關係。

3. **CMU 15-445 Database Systems，Lecture 14：Query Planning & Optimization I**
   Andy Pavlo 的 lecture 14 和 15 直接對應本課 Ch 32 和 Ch 33。Lecture 14 側重 relational algebra equivalences 和 cost model 前置概念；看投影片的「Heuristic-Based Optimization」小節，可以看到和本章一樣的謂詞下推圖示，但用 C++ 框架描述，對照閱讀有助於確認理解無誤。

4. **PostgreSQL 原始碼 `src/backend/optimizer/plan/`**
   看兩個檔案：`planner.c`（入口，負責呼叫各個 Pass，可以看到 RBO 和 CBO 的串接順序）和 `prepqual.c`（謂詞正規化，包含 AND 拆分和常數折疊的實際實作）。PostgreSQL 的謂詞下推實作在 `src/backend/optimizer/path/allpaths.c` 的 `push_down_restrict_and_join` 函數，是工業級謂詞下推的最佳參照實作。

---

→ [Ch 33 查詢優化（二）：Cost-Based Optimizer](./33-query-optimization-cost.md)
