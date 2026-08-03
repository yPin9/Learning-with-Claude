# 練習 D — 拼出能跑的 SQL 引擎

> **目標**：把 Ch 24–33 學到的每一層——Parser、Logical Plan、Predicate Pushdown、Physical Plan、Volcano Executor——拼成一個能真正接收 SQL 字串、執行並回傳結果的完整管線。做完你手上就有一個能跑的微型 SQL 引擎。

---

## 背景動機

讀完 Part 4 的每一章，你對每一層都有概念了，但「概念清楚」和「能把東西接起來」是兩回事。真實資料庫的複雜度有一大半來自層與層之間的介面：型別要對齊、欄位 ID 要一致、計劃樹的走訪順序要正確。這個練習要求你把所有層實際拼在一起，跑四條不同性質的 SQL——單表掃描、WHERE 過濾、INNER JOIN、GROUP BY 聚合——讓每條都回傳正確結果。

使用「純記憶體儲存」作為 Scan 的資料來源。記憶體表夠讓你專注在查詢管線本身，不被 B+tree 的 page 格式或 Buffer Pool 的 pin/unpin 分心。Part 1–3 實作了真正儲存引擎的讀者，可以在完成本練習後，把 SeqScan 的資料來源換成 B+tree leaf scan——介面是一樣的。

---

## 任務規格

### 支援的 SQL 子集

```sql
-- 單表掃描 + 投影
SELECT col1, col2 FROM table_name

-- 單表過濾
SELECT col1 FROM table_name WHERE col2 > value

-- 單一 INNER JOIN（等值連接）
SELECT t1.col, t2.col FROM t1 JOIN t2 ON t1.id = t2.id WHERE ...

-- 簡單聚合（COUNT, SUM + GROUP BY）
SELECT col, COUNT(*), SUM(col2) FROM table_name GROUP BY col
```

### 系統必須做到的事

1. 接收 SQL 字串，輸出格式化的結果行
2. Predicate pushdown 必須真的發生（列印優化前後的計劃樹）
3. 每個 Volcano 運算子都實作 `fn next(&mut self) -> Option<Tuple>`
4. 結果必須正確（用下面的測試資料驗證）

### 測試資料

```
employees: id INT, name TEXT, dept_id INT, salary INT
  (1, "Alice",   1, 80000)
  (2, "Bob",     2, 45000)
  (3, "Carol",   1, 92000)
  (4, "Dave",    3, 60000)
  (5, "Eve",     2, 55000)

departments: id INT, name TEXT
  (1, "Engineering")
  (2, "Marketing")
  (3, "HR")
```

---

## 期望輸出範例

```
=== Q1: SELECT id, name FROM employees WHERE salary > 50000 ===
[Before pushdown]
Project([id, name])
  Filter(salary > 50000)
    Scan(employees)

[After pushdown]
Project([id, name])
  Filter(salary > 50000)
    Scan(employees)

id  | name
----|-------
1   | Alice
3   | Carol
4   | Dave
5   | Eve

=== Q2: SELECT e.name, d.name FROM employees e JOIN departments d ON e.dept_id = d.id ===
name    | name
--------|------------
Alice   | Engineering
Bob     | Marketing
Carol   | Engineering
Dave    | HR
Eve     | Marketing

=== Q3: SELECT dept_id, COUNT(*), SUM(salary) FROM employees GROUP BY dept_id ===
dept_id | count | sum
--------|-------|------
1       | 2     | 172000
2       | 2     | 100000
3       | 1     | 60000
```

---

## 卡住提示

- **型別不對齊**：`Value` 要同時支援整數比較和字串比較，寫一個 `impl PartialOrd for Value` 是最省力的做法。
- **欄位解析**：JOIN 之後欄位名稱會重複（兩個表都叫 `name`）。最簡單的處理：用 `table.column` 當全限定名稱，或在 schema 裡加前綴。
- **HashJoin**：先讀完 inner 表、建 `HashMap<Key, Vec<Tuple>>`，再逐行遍歷 outer 表做 probe。inner 表放在右邊（JOIN 的右側）。
- **HashAggregate**：先用 `HashMap<GroupKey, AggState>` 把所有行掃完再輸出，這是 blocking operator，不能 pipeline。
- **Predicate pushdown 的邊界**：本練習只需要把 Filter 推到 Scan 上面（兩層之間沒有 JOIN 阻隔的情況）。跨越 JOIN 的 predicate 保持原位即可。

---

## 分段實作建議

### Step 1：記憶體儲存與型別系統（先把資料放進去）

定義 `Value`、`Tuple`、`Schema`、`Table`、`Catalog`。填入兩張測試表。確認能用 Rust 迭代所有行。

```rust
#[derive(Debug, Clone, PartialEq)]
enum Value {
    Int(i64),
    Text(String),
    Null,
}

type Tuple = Vec<Value>;

struct Schema {
    columns: Vec<(String, DataType)>,
}
```

**驗收**：印出 employees 表的所有行。

### Step 2：SQL Parser（夠用就好）

寫一個能解析目標 SQL 子集的遞迴下降 Parser。不需要完整的 SQL 語法，只要能處理上面四種查詢即可。

輸出：`SelectStmt { projections, from, join, filter, group_by }`

**驗收**：把四條測試 SQL 都 parse 成功，列印 AST。

### Step 3：Logical Plan 建構 + Predicate Pushdown

把 `SelectStmt` 轉成 `LogicalPlan` 樹，接著套用 predicate pushdown 規則（參考 Ch 32 的實作）。列印優化前後的計劃樹。

```rust
enum LogicalPlan {
    Scan { table: String },
    Filter { predicate: Expr, child: Box<LogicalPlan> },
    Project { columns: Vec<String>, child: Box<LogicalPlan> },
    Join { left: Box<LogicalPlan>, right: Box<LogicalPlan>, condition: Expr },
    Aggregate { keys: Vec<String>, aggs: Vec<AggFunc>, child: Box<LogicalPlan> },
}
```

**驗收**：`SELECT name FROM employees WHERE salary > 50000` 的 Filter 節點在 pushdown 後緊貼 Scan。

### Step 4：Physical Plan + Volcano Executor

把 `LogicalPlan` 轉成 `PhysicalPlan`（可以直接在同一個 enum 加 physical variant，或拆成兩個 enum）。每個 operator 實作：

```rust
trait Operator {
    fn next(&mut self) -> Option<Tuple>;
}
```

實作清單：`SeqScan`、`FilterOp`、`ProjectOp`、`HashJoinOp`、`HashAggregateOp`。

**驗收**：四條測試 SQL 都輸出正確結果。

### Step 5：整合與格式化輸出

寫 `fn execute_sql(catalog: &Catalog, sql: &str) -> Vec<Tuple>`，把 Step 1–4 串起來。加上格式化輸出（欄寬對齊）。

---

## 完整參考解答

**寫完再看。**

<details>
<summary>點開參考實作</summary>

```rust
// mini_sql.rs — 能跑的微型 SQL 引擎
// 編譯：rustc mini_sql.rs -o mini_sql && ./mini_sql
// 或：把內容放進 src/main.rs，cargo run

use std::collections::HashMap;

// ─── 型別系統 ────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq)]
enum Value {
    Int(i64),
    Text(String),
    Null,
}

impl std::fmt::Display for Value {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Value::Int(n) => write!(f, "{}", n),
            Value::Text(s) => write!(f, "{}", s),
            Value::Null => write!(f, "NULL"),
        }
    }
}

impl PartialOrd for Value {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        match (self, other) {
            (Value::Int(a), Value::Int(b)) => a.partial_cmp(b),
            (Value::Text(a), Value::Text(b)) => a.partial_cmp(b),
            _ => None,
        }
    }
}

type Tuple = Vec<Value>;

#[derive(Debug, Clone)]
struct Schema {
    columns: Vec<String>, // column names in order
}

impl Schema {
    fn col_index(&self, name: &str) -> Option<usize> {
        // support both "col" and "table.col" forms
        self.columns.iter().position(|c| c == name || c.ends_with(&format!(".{}", name)))
    }
}

#[derive(Debug)]
struct Table {
    schema: Schema,
    rows: Vec<Tuple>,
}

struct Catalog {
    tables: HashMap<String, Table>,
}

impl Catalog {
    fn new() -> Self {
        Catalog { tables: HashMap::new() }
    }

    fn add_table(&mut self, name: &str, columns: Vec<&str>, rows: Vec<Tuple>) {
        self.tables.insert(name.to_string(), Table {
            schema: Schema { columns: columns.iter().map(|s| s.to_string()).collect() },
            rows,
        });
    }
}

// ─── AST ────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
enum Expr {
    Column(String),                              // "salary" or "e.salary"
    Literal(Value),
    BinOp { op: Op, left: Box<Expr>, right: Box<Expr> },
}

#[derive(Debug, Clone, PartialEq)]
enum Op { Eq, Gt, Lt, Ge, Le, And }

#[derive(Debug, Clone)]
enum AggFunc { Count, Sum(String) }

#[derive(Debug, Clone)]
struct SelectStmt {
    projections: Vec<String>,        // ["e.name", "d.name"] or ["*"]
    from: String,                    // primary table name
    from_alias: Option<String>,
    join: Option<JoinClause>,
    filter: Option<Expr>,
    group_by: Vec<String>,
    aggs: Vec<AggFunc>,
}

#[derive(Debug, Clone)]
struct JoinClause {
    table: String,
    alias: Option<String>,
    on: Expr,
}

// ─── 極簡 Parser ─────────────────────────────────────────────────────────────

struct Parser {
    tokens: Vec<String>,
    pos: usize,
}

impl Parser {
    fn new(sql: &str) -> Self {
        let tokens = tokenize(sql);
        Parser { tokens, pos: 0 }
    }

    fn peek(&self) -> Option<&str> {
        self.tokens.get(self.pos).map(|s| s.as_str())
    }

    fn eat(&mut self) -> Option<String> {
        let t = self.tokens.get(self.pos).cloned();
        self.pos += 1;
        t
    }

    fn expect(&mut self, expected: &str) {
        let got = self.eat().unwrap_or_default();
        assert_eq!(got.to_uppercase(), expected.to_uppercase(),
            "expected '{}', got '{}'", expected, got);
    }

    fn parse_select(&mut self) -> SelectStmt {
        self.expect("SELECT");
        let (projections, aggs) = self.parse_projections();
        self.expect("FROM");
        let from = self.eat().unwrap();
        let from_alias = if self.peek().map(|s| !["WHERE","JOIN","GROUP","ORDER","LIMIT",""].contains(&s.to_uppercase().as_str())).unwrap_or(false) {
            let a = self.peek().unwrap().to_string();
            if a != "JOIN" && a != "WHERE" && a != "GROUP" {
                self.eat();
                Some(a)
            } else { None }
        } else { None };

        let join = if self.peek().map(|s| s.to_uppercase() == "JOIN").unwrap_or(false) {
            self.eat(); // JOIN
            let table = self.eat().unwrap();
            let alias = {
                let next = self.peek().unwrap_or("").to_uppercase();
                if next != "ON" { let a = self.eat().unwrap(); Some(a) } else { None }
            };
            self.expect("ON");
            let on = self.parse_expr();
            Some(JoinClause { table, alias, on })
        } else { None };

        let filter = if self.peek().map(|s| s.to_uppercase() == "WHERE").unwrap_or(false) {
            self.eat();
            Some(self.parse_expr())
        } else { None };

        let group_by = if self.peek().map(|s| s.to_uppercase() == "GROUP").unwrap_or(false) {
            self.eat(); // GROUP
            self.expect("BY");
            let mut cols = vec![self.eat().unwrap()];
            while self.peek() == Some(",") { self.eat(); cols.push(self.eat().unwrap()); }
            cols
        } else { vec![] };

        SelectStmt { projections, from, from_alias, join, filter, group_by, aggs }
    }

    fn parse_projections(&mut self) -> (Vec<String>, Vec<AggFunc>) {
        let mut cols = vec![];
        let mut aggs = vec![];
        loop {
            let tok = self.eat().unwrap();
            let upper = tok.to_uppercase();
            if upper == "COUNT" {
                self.expect("(");
                self.eat(); // * or col
                self.expect(")");
                aggs.push(AggFunc::Count);
                cols.push("COUNT(*)".to_string());
            } else if upper == "SUM" {
                self.expect("(");
                let col = self.eat().unwrap();
                self.expect(")");
                aggs.push(AggFunc::Sum(col.clone()));
                cols.push(format!("SUM({})", col));
            } else {
                cols.push(tok);
            }
            if self.peek() != Some(",") { break; }
            self.eat(); // comma
        }
        (cols, aggs)
    }

    fn parse_expr(&mut self) -> Expr {
        let left = self.parse_atom();
        let op_str = match self.peek() {
            Some("=") => Op::Eq,
            Some(">") => Op::Gt,
            Some("<") => Op::Lt,
            Some(">=") => Op::Ge,
            Some("<=") => Op::Le,
            _ => return left,
        };
        self.eat();
        let right = self.parse_atom();
        let base = Expr::BinOp { op: op_str, left: Box::new(left), right: Box::new(right) };
        if self.peek().map(|s| s.to_uppercase() == "AND").unwrap_or(false) {
            self.eat();
            let rhs = self.parse_expr();
            Expr::BinOp { op: Op::And, left: Box::new(base), right: Box::new(rhs) }
        } else {
            base
        }
    }

    fn parse_atom(&mut self) -> Expr {
        let tok = self.eat().unwrap();
        if let Ok(n) = tok.parse::<i64>() {
            Expr::Literal(Value::Int(n))
        } else if tok.starts_with('\'') || tok.starts_with('"') {
            Expr::Literal(Value::Text(tok.trim_matches(|c| c == '\'' || c == '"').to_string()))
        } else {
            Expr::Column(tok)
        }
    }
}

fn tokenize(sql: &str) -> Vec<String> {
    let mut tokens = vec![];
    let mut chars = sql.chars().peekable();
    while let Some(&c) = chars.peek() {
        if c.is_whitespace() { chars.next(); continue; }
        if c == ',' || c == '(' || c == ')' || c == '*' {
            tokens.push(c.to_string()); chars.next(); continue;
        }
        if c == '>' || c == '<' || c == '=' {
            chars.next();
            let mut op = c.to_string();
            if chars.peek() == Some(&'=') { op.push('='); chars.next(); }
            tokens.push(op); continue;
        }
        if c == '\'' || c == '"' {
            let q = c; chars.next();
            let mut s = q.to_string();
            while let Some(&ch) = chars.peek() {
                chars.next(); s.push(ch);
                if ch == q { break; }
            }
            tokens.push(s); continue;
        }
        let mut word = String::new();
        while let Some(&ch) = chars.peek() {
            if ch.is_alphanumeric() || ch == '_' || ch == '.' { word.push(ch); chars.next(); }
            else { break; }
        }
        if !word.is_empty() { tokens.push(word); }
    }
    tokens
}

// ─── Logical Plan ────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
enum LogicalPlan {
    Scan { table: String, alias: Option<String> },
    Filter { predicate: Expr, child: Box<LogicalPlan> },
    Project { columns: Vec<String>, child: Box<LogicalPlan> },
    Join {
        left: Box<LogicalPlan>,
        right: Box<LogicalPlan>,
        condition: Expr,
    },
    Aggregate {
        keys: Vec<String>,
        aggs: Vec<AggFunc>,
        child: Box<LogicalPlan>,
    },
}

fn print_plan(plan: &LogicalPlan, indent: usize) {
    let pad = "  ".repeat(indent);
    match plan {
        LogicalPlan::Scan { table, alias } => {
            println!("{}Scan({}{})", pad, table,
                alias.as_deref().map(|a| format!(" as {}", a)).unwrap_or_default());
        }
        LogicalPlan::Filter { predicate, child } => {
            println!("{}Filter({})", pad, fmt_expr(predicate));
            print_plan(child, indent + 1);
        }
        LogicalPlan::Project { columns, child } => {
            println!("{}Project([{}])", pad, columns.join(", "));
            print_plan(child, indent + 1);
        }
        LogicalPlan::Join { left, right, condition } => {
            println!("{}HashJoin(on: {})", pad, fmt_expr(condition));
            print_plan(left, indent + 1);
            print_plan(right, indent + 1);
        }
        LogicalPlan::Aggregate { keys, aggs, child } => {
            println!("{}Aggregate(keys=[{}])", pad, keys.join(", "));
            print_plan(child, indent + 1);
        }
    }
}

fn fmt_expr(e: &Expr) -> String {
    match e {
        Expr::Column(c) => c.clone(),
        Expr::Literal(v) => v.to_string(),
        Expr::BinOp { op, left, right } => {
            let op_str = match op {
                Op::Eq => "=", Op::Gt => ">", Op::Lt => "<",
                Op::Ge => ">=", Op::Le => "<=", Op::And => "AND",
            };
            format!("{} {} {}", fmt_expr(left), op_str, fmt_expr(right))
        }
    }
}

fn build_logical_plan(stmt: &SelectStmt) -> LogicalPlan {
    let mut plan: LogicalPlan = LogicalPlan::Scan {
        table: stmt.from.clone(),
        alias: stmt.from_alias.clone(),
    };

    if let Some(j) = &stmt.join {
        let right = LogicalPlan::Scan { table: j.table.clone(), alias: j.alias.clone() };
        plan = LogicalPlan::Join {
            left: Box::new(plan),
            right: Box::new(right),
            condition: j.on.clone(),
        };
    }

    if let Some(pred) = &stmt.filter {
        plan = LogicalPlan::Filter { predicate: pred.clone(), child: Box::new(plan) };
    }

    if !stmt.group_by.is_empty() {
        plan = LogicalPlan::Aggregate {
            keys: stmt.group_by.clone(),
            aggs: stmt.aggs.clone(),
            child: Box::new(plan),
        };
    }

    // only project if not aggregation (agg handles output itself)
    let real_cols: Vec<String> = stmt.projections.iter()
        .filter(|c| !c.starts_with("COUNT") && !c.starts_with("SUM"))
        .cloned().collect();
    if !real_cols.is_empty() && stmt.aggs.is_empty() {
        plan = LogicalPlan::Project { columns: stmt.projections.clone(), child: Box::new(plan) };
    } else if !stmt.projections.is_empty() && !stmt.aggs.is_empty() {
        // include group-by keys + agg cols
        plan = LogicalPlan::Project { columns: stmt.projections.clone(), child: Box::new(plan) };
    }

    plan
}

// ─── Predicate Pushdown ──────────────────────────────────────────────────────

fn predicate_pushdown(plan: LogicalPlan) -> LogicalPlan {
    match plan {
        LogicalPlan::Filter { predicate, child } => {
            match *child {
                // Filter over Scan → keep it (already at bottom)
                scan @ LogicalPlan::Scan { .. } => {
                    LogicalPlan::Filter { predicate, child: Box::new(scan) }
                }
                // Filter over Filter → merge into AND, push both
                LogicalPlan::Filter { predicate: inner_pred, child: inner_child } => {
                    let merged = Expr::BinOp {
                        op: Op::And,
                        left: Box::new(predicate),
                        right: Box::new(inner_pred),
                    };
                    predicate_pushdown(LogicalPlan::Filter {
                        predicate: merged,
                        child: inner_child,
                    })
                }
                // Filter over Project → push below project (safe when predicate cols in project)
                LogicalPlan::Project { columns, child: project_child } => {
                    let pushed = predicate_pushdown(LogicalPlan::Filter {
                        predicate,
                        child: project_child,
                    });
                    LogicalPlan::Project { columns, child: Box::new(pushed) }
                }
                // Filter over Join → keep above join (safe, don't push cross-table predicates)
                join @ LogicalPlan::Join { .. } => {
                    let optimized_join = predicate_pushdown(join);
                    LogicalPlan::Filter { predicate, child: Box::new(optimized_join) }
                }
                other => LogicalPlan::Filter { predicate, child: Box::new(predicate_pushdown(other)) }
            }
        }
        LogicalPlan::Project { columns, child } => {
            LogicalPlan::Project { columns, child: Box::new(predicate_pushdown(*child)) }
        }
        LogicalPlan::Join { left, right, condition } => {
            LogicalPlan::Join {
                left: Box::new(predicate_pushdown(*left)),
                right: Box::new(predicate_pushdown(*right)),
                condition,
            }
        }
        LogicalPlan::Aggregate { keys, aggs, child } => {
            LogicalPlan::Aggregate { keys, aggs, child: Box::new(predicate_pushdown(*child)) }
        }
        other => other,
    }
}

// ─── Volcano Executor ────────────────────────────────────────────────────────

struct ExecContext<'a> {
    catalog: &'a Catalog,
}

fn eval_expr(expr: &Expr, tuple: &Tuple, schema: &Schema) -> Value {
    match expr {
        Expr::Literal(v) => v.clone(),
        Expr::Column(name) => {
            if let Some(idx) = schema.col_index(name) {
                tuple[idx].clone()
            } else {
                Value::Null
            }
        }
        Expr::BinOp { op, left, right } => {
            let l = eval_expr(left, tuple, schema);
            let r = eval_expr(right, tuple, schema);
            match op {
                Op::And => {
                    let lb = matches!(l, Value::Int(1));
                    let rb = matches!(r, Value::Int(1));
                    Value::Int(if lb && rb { 1 } else { 0 })
                }
                _ => {
                    let cmp = l.partial_cmp(&r);
                    let result = match op {
                        Op::Eq => cmp == Some(std::cmp::Ordering::Equal),
                        Op::Gt => cmp == Some(std::cmp::Ordering::Greater),
                        Op::Lt => cmp == Some(std::cmp::Ordering::Less),
                        Op::Ge => matches!(cmp, Some(std::cmp::Ordering::Greater) | Some(std::cmp::Ordering::Equal)),
                        Op::Le => matches!(cmp, Some(std::cmp::Ordering::Less) | Some(std::cmp::Ordering::Equal)),
                        Op::And => unreachable!(),
                    };
                    Value::Int(if result { 1 } else { 0 })
                }
            }
        }
    }
}

fn execute_plan<'a>(plan: &LogicalPlan, ctx: &'a ExecContext<'a>) -> (Vec<Tuple>, Schema) {
    match plan {
        LogicalPlan::Scan { table, alias } => {
            let t = ctx.catalog.tables.get(table).expect("table not found");
            let schema = if let Some(a) = alias {
                Schema { columns: t.schema.columns.iter()
                    .map(|c| format!("{}.{}", a, c)).collect() }
            } else {
                Schema { columns: t.schema.columns.iter()
                    .map(|c| format!("{}.{}", table, c)).collect() }
            };
            (t.rows.clone(), schema)
        }

        LogicalPlan::Filter { predicate, child } => {
            let (rows, schema) = execute_plan(child, ctx);
            let filtered = rows.into_iter()
                .filter(|row| matches!(eval_expr(predicate, row, &schema), Value::Int(1)))
                .collect();
            (filtered, schema)
        }

        LogicalPlan::Project { columns, child } => {
            let (rows, schema) = execute_plan(child, ctx);
            let result: Vec<Tuple> = rows.iter().map(|row| {
                columns.iter().map(|col| {
                    if col.starts_with("COUNT") || col.starts_with("SUM") {
                        // pass through agg values already computed
                        if let Some(idx) = schema.col_index(col) {
                            row[idx].clone()
                        } else { Value::Null }
                    } else if let Some(idx) = schema.col_index(col) {
                        row[idx].clone()
                    } else {
                        Value::Null
                    }
                }).collect()
            }).collect();
            let out_schema = Schema { columns: columns.clone() };
            (result, out_schema)
        }

        LogicalPlan::Join { left, right, condition } => {
            let (left_rows, left_schema) = execute_plan(left, ctx);
            let (right_rows, right_schema) = execute_plan(right, ctx);

            // merge schemas
            let mut merged_cols = left_schema.columns.clone();
            merged_cols.extend(right_schema.columns.clone());
            let merged_schema = Schema { columns: merged_cols };

            let mut result = vec![];
            for lrow in &left_rows {
                for rrow in &right_rows {
                    let mut combined = lrow.clone();
                    combined.extend(rrow.clone());
                    if matches!(eval_expr(condition, &combined, &merged_schema), Value::Int(1)) {
                        result.push(combined);
                    }
                }
            }
            (result, merged_schema)
        }

        LogicalPlan::Aggregate { keys, aggs, child } => {
            let (rows, schema) = execute_plan(child, ctx);
            let mut groups: HashMap<Vec<String>, (i64, i64)> = HashMap::new(); // (count, sum)

            for row in &rows {
                let key: Vec<String> = keys.iter().map(|k| {
                    if let Some(idx) = schema.col_index(k) {
                        row[idx].to_string()
                    } else { "".to_string() }
                }).collect();
                let entry = groups.entry(key).or_insert((0, 0));
                entry.0 += 1; // COUNT
                for agg in aggs {
                    if let AggFunc::Sum(col) = agg {
                        if let Some(idx) = schema.col_index(col) {
                            if let Value::Int(v) = row[idx] {
                                entry.1 += v;
                            }
                        }
                    }
                }
            }

            // sort by key for deterministic output
            let mut group_keys: Vec<Vec<String>> = groups.keys().cloned().collect();
            group_keys.sort();

            let mut result_rows = vec![];
            for key in group_keys {
                let (cnt, sum) = groups[&key];
                let mut row: Vec<Value> = key.iter().map(|s| {
                    if let Ok(n) = s.parse::<i64>() { Value::Int(n) }
                    else { Value::Text(s.clone()) }
                }).collect();
                for agg in aggs {
                    match agg {
                        AggFunc::Count => row.push(Value::Int(cnt)),
                        AggFunc::Sum(_) => row.push(Value::Int(sum)),
                    }
                }
                result_rows.push(row);
            }

            let mut out_cols = keys.clone();
            for agg in aggs {
                match agg {
                    AggFunc::Count => out_cols.push("COUNT(*)".to_string()),
                    AggFunc::Sum(c) => out_cols.push(format!("SUM({})", c)),
                }
            }

            (result_rows, Schema { columns: out_cols })
        }
    }
}

fn print_results(header: &[String], rows: &[Tuple]) {
    let widths: Vec<usize> = header.iter().enumerate().map(|(i, h)| {
        let max_val = rows.iter().map(|r| r.get(i).map(|v| v.to_string().len()).unwrap_or(0)).max().unwrap_or(0);
        h.len().max(max_val)
    }).collect();

    let header_line: Vec<String> = header.iter().zip(&widths)
        .map(|(h, &w)| format!("{:<width$}", h, width = w)).collect();
    println!("{}", header_line.join(" | "));
    println!("{}", widths.iter().map(|&w| "-".repeat(w)).collect::<Vec<_>>().join("-+-"));
    for row in rows {
        let row_line: Vec<String> = row.iter().zip(&widths)
            .map(|(v, &w)| format!("{:<width$}", v.to_string(), width = w)).collect();
        println!("{}", row_line.join(" | "));
    }
}

fn run_sql(catalog: &Catalog, sql: &str) {
    println!("\n=== {} ===", sql);
    let mut parser = Parser::new(sql);
    let stmt = parser.parse_select();
    let logical = build_logical_plan(&stmt);
    println!("[Before pushdown]");
    print_plan(&logical, 0);
    let optimized = predicate_pushdown(logical);
    println!("[After pushdown]");
    print_plan(&optimized, 0);
    println!();
    let ctx = ExecContext { catalog };
    let (rows, schema) = execute_plan(&optimized, &ctx);
    print_results(&schema.columns, &rows);
}

fn main() {
    let mut catalog = Catalog::new();

    catalog.add_table("employees",
        vec!["id", "name", "dept_id", "salary"],
        vec![
            vec![Value::Int(1), Value::Text("Alice".into()),  Value::Int(1), Value::Int(80000)],
            vec![Value::Int(2), Value::Text("Bob".into()),    Value::Int(2), Value::Int(45000)],
            vec![Value::Int(3), Value::Text("Carol".into()),  Value::Int(1), Value::Int(92000)],
            vec![Value::Int(4), Value::Text("Dave".into()),   Value::Int(3), Value::Int(60000)],
            vec![Value::Int(5), Value::Text("Eve".into()),    Value::Int(2), Value::Int(55000)],
        ],
    );

    catalog.add_table("departments",
        vec!["id", "name"],
        vec![
            vec![Value::Int(1), Value::Text("Engineering".into())],
            vec![Value::Int(2), Value::Text("Marketing".into())],
            vec![Value::Int(3), Value::Text("HR".into())],
        ],
    );

    run_sql(&catalog, "SELECT id, name FROM employees WHERE salary > 50000");
    run_sql(&catalog, "SELECT name, salary FROM employees WHERE dept_id = 1");
    run_sql(&catalog, "SELECT e.name, d.name FROM employees e JOIN departments d ON e.dept_id = d.id");
    run_sql(&catalog, "SELECT dept_id, COUNT(*), SUM(salary) FROM employees GROUP BY dept_id");
}
```

編譯與執行：
```bash
# 方法一：直接編譯
rustc mini_sql.rs -o mini_sql && ./mini_sql

# 方法二：cargo 專案
cargo new mini_sql_engine
# 把上方程式碼貼進 src/main.rs
cargo run
```

</details>

---

## 測試用例

| SQL | 期望行為 |
|-----|---------|
| `SELECT id, name FROM employees WHERE salary > 50000` | 回傳 Alice(80000)、Carol(92000)、Dave(60000)、Eve(55000)，共 4 筆 |
| `SELECT name, salary FROM employees WHERE dept_id = 1` | 回傳 Alice 和 Carol，兩筆工程部員工 |
| `SELECT e.name, d.name FROM employees e JOIN departments d ON e.dept_id = d.id` | 5 筆交叉對應，每位員工都有部門名稱 |
| `SELECT dept_id, COUNT(*), SUM(salary) FROM employees GROUP BY dept_id` | dept 1: 2人 172000；dept 2: 2人 100000；dept 3: 1人 60000 |

---

## 延伸挑戰

**挑戰 1：加 ORDER BY + LIMIT**

在 `LogicalPlan` 加 `Sort { keys, child }` 和 `Limit { n, child }`，executor 實作 `Sort`（把所有行拉完、排序、再 yield）和 `Limit`（計數器）。測試：

```sql
SELECT name, salary FROM employees ORDER BY salary DESC LIMIT 3
```

**挑戰 2：加簡單 CBO 選 JOIN 順序**

對兩表 JOIN，用 catalog 的 `rows.len()` 當 cardinality，把較小的表放 inner（build side）。在 `HashJoinOp` 裡加判斷：若 right table 行數 > left table，交換 left/right。這是 Ch 33 System R DP 的簡化版（2 表情況）。

**挑戰 3：加 DISTINCT**

在 `Project` 後面加 `Distinct` operator：用 `HashSet<Vec<String>>` 追蹤已見過的行。

**挑戰 4：接 B+tree 儲存**

把 `LogicalPlan::Scan` 的執行從記憶體陣列換成呼叫 Part 1 B+tree 的 `range_scan()`。介面對齊只需要讓 B+tree 的 scan 回傳 `Vec<Tuple>` 即可。

---

## 自我檢核

完成後，你應該能回答：

- [ ] Volcano 模型裡，`execute_plan` 是深度優先還是廣度優先地消費資料？為什麼 HashAggregate 是 blocking operator？
- [ ] Predicate pushdown 把 Filter 推到 Scan 下面，在這份實作裡為什麼推不進 JOIN 的子樹？什麼情況可以安全推過 JOIN？
- [ ] HashJoin 為什麼把「小表」放 build side（inner）？如果 inner 表大到記憶體放不下，該怎麼辦？
- [ ] 這份 Parser 是 recursive descent。`parse_expr` 遞迴呼叫自己處理 AND 連結，但對 OR 或更複雜的優先序有什麼問題？
- [ ] GROUP BY 的 `HashAggregate` 在這份實作裡先把所有行掃完才輸出，為什麼不能像 Filter 那樣做 streaming pipeline？

---

→ [Ch 34 統計與 cardinality estimation](./34-statistics-cardinality.md)
