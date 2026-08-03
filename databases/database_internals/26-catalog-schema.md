# Ch 26 — Catalog / Schema

> **目標**：理解系統目錄（catalog）的結構——資料庫如何用自己來描述自己、schema 怎麼定義表與欄位的型別、binder 如何把 AST 裡的名字字串解析成 catalog 裡的實體（name resolution）。用 Rust 實作 Catalog + Binder，連接 Ch 25 的 AST 到 Ch 27 的 logical plan。

## 為什麼需要 Catalog？

Parser（Ch 25）產出的 AST 只有字串名字——`Column { table: Some("e"), name: "salary" }` 裡的 `"salary"` 就是個字串，parser 不知道它是什麼型別、在哪個 table、甚至這個 table 是否存在。

**Catalog**（系統目錄）是資料庫的 metadata 儲存：它記錄了所有 table 的名字、每個 table 有哪些欄（column）、每個欄的型別、索引資訊等。資料庫用自己的儲存引擎來存 catalog，這是「用資料庫描述資料庫」的自舉（bootstrap）性質。

Binder（綁定器）的工作是查 catalog，把 AST 裡的名字字串解析成 catalog 裡有具體定義的實體，同時做型別推斷。這個過程叫 **name resolution**（名稱解析）。如果 table 不存在、欄名不存在、或型別不相容，binder 報錯——不是 parser 報。

```
                     查詢：catalog 裡有沒有這個 table？
                     ↓            ↑ 有：綁定；沒有：報錯
AST (含字串名) ──► Binder ──────────────────────────────►  Typed AST
                              │
                         Catalog
                    (table/column/index metadata)
```

## 真實資料庫的 Catalog 長什麼樣？

PostgreSQL 有 `pg_catalog` schema，裡面的 `pg_class`、`pg_attribute`、`pg_type` 就是系統目錄的核心表，存在 PostgreSQL 自己的 heap 裡。你可以查詢它們：

```sql
-- 列出所有用戶 table
SELECT relname FROM pg_class WHERE relkind = 'r';

-- 列出某個 table 的所有欄
SELECT attname, atttypid FROM pg_attribute
WHERE attrelid = 'employees'::regclass AND attnum > 0;
```

SQLite 用 `sqlite_schema` 表（舊版叫 `sqlite_master`）存所有物件的 CREATE 語句。設計不同，但核心概念一樣：用資料庫存資料庫的 schema。

我們的實作用記憶體 HashMap，不真正持久化到磁碟——持久化的部分在整合 final project 時再加。

## Catalog 的資料結構

```
Catalog
 └─ databases: HashMap<String, Database>
     └─ Database
         └─ schemas: HashMap<String, Schema>
             └─ Schema（通常叫 "public"）
                 └─ tables: HashMap<String, TableSchema>
                     └─ TableSchema
                         ├─ name: String
                         ├─ columns: Vec<ColumnDef>
                         └─ indexes: Vec<IndexDef>    ← Ch 10 會用到
```

大多數單機 DB 的 catalog 是扁平的——一個 DB 裡直接放 tables，中間沒有額外的 schema 層。PostgreSQL 有 schema（namespace）層。我們加上去讓架構正確，預設 schema 叫 `"public"`。

## 型別系統

SQL 的型別系統比程式語言型別系統簡單得多——沒有 generics、沒有函式型別。核心型別：

| SQL 型別 | Rust 表示 | 儲存大小 | 備註 |
|----------|-----------|----------|------|
| INT / INTEGER | i32 | 4 bytes | 常見整數 |
| BIGINT | i64 | 8 bytes | 大整數、自增主鍵 |
| FLOAT / REAL | f64 | 8 bytes | 浮點，注意比較精度 |
| TEXT / VARCHAR | String | 可變長 | 簡化：不限長度 |
| BOOLEAN | bool | 1 byte | TRUE/FALSE |
| NULL | — | — | 不是型別，是 absence of value |

NULL 在 SQL 裡是特殊的：任何型別的欄位都可以是 NULL（除非有 NOT NULL 約束）。`NULL = NULL` 結果是 NULL，不是 TRUE——這個三值邏輯（three-valued logic）是 SQL 初學者最大的陷阱。

## Rust 實作：Catalog

```rust
use std::collections::HashMap;

/// 欄位的型別定義
#[derive(Debug, Clone, PartialEq)]
pub enum DataType {
    Int,
    BigInt,
    Float,
    Text,
    Boolean,
}

impl DataType {
    /// 兩個型別在二元運算中能否相容（簡化規則）
    pub fn is_compatible_with(&self, other: &DataType) -> bool {
        match (self, other) {
            (DataType::Int, DataType::Int) => true,
            (DataType::Int, DataType::BigInt) | (DataType::BigInt, DataType::Int) => true,
            (DataType::BigInt, DataType::BigInt) => true,
            (DataType::Float, DataType::Float) => true,
            (DataType::Float, DataType::Int) | (DataType::Int, DataType::Float) => true,
            (DataType::Text, DataType::Text) => true,
            (DataType::Boolean, DataType::Boolean) => true,
            _ => false,
        }
    }

    pub fn is_numeric(&self) -> bool {
        matches!(self, DataType::Int | DataType::BigInt | DataType::Float)
    }
}

/// 一個欄位的定義（schema level）
#[derive(Debug, Clone)]
pub struct ColumnDef {
    pub name: String,
    pub data_type: DataType,
    pub nullable: bool,
    pub ordinal: usize,   // 在 table 中的順序（第幾欄），0-indexed
}

/// 一個 table 的 schema
#[derive(Debug, Clone)]
pub struct TableSchema {
    pub name: String,
    pub columns: Vec<ColumnDef>,
    // 之後 Ch 10 索引章節會在這裡加 indexes: Vec<IndexDef>
}

impl TableSchema {
    pub fn new(name: &str, columns: Vec<ColumnDef>) -> Self {
        TableSchema { name: name.to_string(), columns }
    }

    /// 按名字找欄位，回傳 (ordinal, &ColumnDef)
    pub fn find_column(&self, name: &str) -> Option<(usize, &ColumnDef)> {
        self.columns.iter().enumerate().find(|(_, c)| c.name == name)
    }
}

/// SQL namespace（預設叫 "public"）
#[derive(Debug, Default)]
pub struct Schema {
    pub tables: HashMap<String, TableSchema>,
}

impl Schema {
    pub fn add_table(&mut self, table: TableSchema) {
        self.tables.insert(table.name.clone(), table);
    }

    pub fn get_table(&self, name: &str) -> Option<&TableSchema> {
        self.tables.get(name)
    }
}

/// 最頂層：整個資料庫的 Catalog
#[derive(Debug, Default)]
pub struct Catalog {
    /// key = schema name（我們預設只用 "public"）
    schemas: HashMap<String, Schema>,
}

impl Catalog {
    pub fn new() -> Self {
        let mut cat = Catalog::default();
        cat.schemas.insert("public".to_string(), Schema::default());
        cat
    }

    pub fn public_schema(&self) -> &Schema {
        self.schemas.get("public").unwrap()
    }

    pub fn public_schema_mut(&mut self) -> &mut Schema {
        self.schemas.get_mut("public").unwrap()
    }

    /// 建立 table（等同執行 CREATE TABLE）
    pub fn create_table(&mut self, name: &str, cols: Vec<(&str, DataType, bool)>) {
        let columns = cols.into_iter().enumerate().map(|(i, (col_name, dt, nullable))| {
            ColumnDef {
                name: col_name.to_string(),
                data_type: dt,
                nullable,
                ordinal: i,
            }
        }).collect();
        let table = TableSchema::new(name, columns);
        self.public_schema_mut().add_table(table);
    }

    /// 查一個 table
    pub fn get_table(&self, name: &str) -> Option<&TableSchema> {
        self.public_schema().get_table(name)
    }
}
```

## Name Resolution：把名字綁到 Catalog

Binder 的核心邏輯：

1. 從 `FROM` / `JOIN` 子句收集查詢裡涉及的所有 table，建立「本次查詢的作用域（scope）」
2. 對每個欄位引用（`Column { table, name }`）在 scope 裡查找：
   - 如果有 table qualifier（`e.salary`）：在別名為 `e` 的 table 裡找 `salary`
   - 如果沒有（`salary`）：在所有 scope 裡的 table 找，找到多個則報 ambiguous error
3. 推斷表達式的型別

```rust
/// Binder 的 scope：本次查詢可見的 table（含別名）
#[derive(Debug, Default)]
pub struct QueryScope {
    /// key = 別名（或 table 原名）, value = 實際 TableSchema
    tables: HashMap<String, TableSchema>,
}

impl QueryScope {
    pub fn add_table(&mut self, alias: String, schema: TableSchema) {
        self.tables.insert(alias, schema);
    }

    /// 解析無 table qualifier 的欄位引用
    /// 回傳 (resolved_table_alias, &ColumnDef)
    pub fn resolve_column_unqualified(
        &self,
        col_name: &str,
    ) -> Result<(String, &ColumnDef), String> {
        let mut found: Vec<(String, &ColumnDef)> = Vec::new();
        for (alias, schema) in &self.tables {
            if let Some((_, col)) = schema.find_column(col_name) {
                found.push((alias.clone(), col));
            }
        }
        match found.len() {
            0 => Err(format!("column '{}' does not exist", col_name)),
            1 => Ok(found.remove(0)),
            _ => {
                let tables: Vec<_> = found.iter().map(|(a, _)| a.as_str()).collect();
                Err(format!(
                    "column '{}' is ambiguous: appears in tables {:?}",
                    col_name, tables
                ))
            }
        }
    }

    /// 解析有 table qualifier 的欄位引用
    pub fn resolve_column_qualified(
        &self,
        table_alias: &str,
        col_name: &str,
    ) -> Result<&ColumnDef, String> {
        let schema = self.tables.get(table_alias).ok_or_else(|| {
            format!("table alias '{}' not found in query scope", table_alias)
        })?;
        let (_, col) = schema.find_column(col_name).ok_or_else(|| {
            format!("column '{}' does not exist in table '{}'", col_name, table_alias)
        })?;
        Ok(col)
    }
}
```

## Binder 主體（完整可編譯）

把 Ch 25 的 AST 型別引入，加上 Binder：

```rust
// 假設 Ch 25 的 AST 型別已可用（SelectStmt, Expr, BinOp 等）
// 我們定義一個「帶型別的欄位引用」作為 binding 結果

#[derive(Debug, Clone)]
pub struct ResolvedColumn {
    pub table_alias: String,
    pub column_name: String,
    pub data_type: DataType,
    pub ordinal: usize,
}

#[derive(Debug, Clone)]
pub enum TypedExpr {
    Int(i64),
    Float(f64),
    Str(String),
    Bool(bool),
    Null,
    Column(ResolvedColumn),
    BinOp {
        op: BinOp,
        left: Box<TypedExpr>,
        right: Box<TypedExpr>,
        result_type: DataType,
    },
    IsNull {
        expr: Box<TypedExpr>,
        negated: bool,
    },
}

impl TypedExpr {
    pub fn data_type(&self) -> Option<DataType> {
        match self {
            TypedExpr::Int(_)    => Some(DataType::Int),
            TypedExpr::Float(_)  => Some(DataType::Float),
            TypedExpr::Str(_)    => Some(DataType::Text),
            TypedExpr::Bool(_)   => Some(DataType::Boolean),
            TypedExpr::Null      => None,
            TypedExpr::Column(c) => Some(c.data_type.clone()),
            TypedExpr::BinOp { result_type, .. } => Some(result_type.clone()),
            TypedExpr::IsNull { .. } => Some(DataType::Boolean),
        }
    }
}

pub struct Binder<'a> {
    catalog: &'a Catalog,
}

impl<'a> Binder<'a> {
    pub fn new(catalog: &'a Catalog) -> Self {
        Binder { catalog }
    }

    /// 綁定 SelectStmt：建立 scope，解析所有欄位引用
    pub fn bind_select(
        &self,
        stmt: &SelectStmt,
    ) -> Result<BoundSelect, String> {
        // 1. 建立查詢 scope
        let mut scope = QueryScope::default();
        for table_ref in &stmt.from {
            let schema = self.catalog.get_table(&table_ref.name).ok_or_else(|| {
                format!("table '{}' does not exist", table_ref.name)
            })?;
            let alias = table_ref.alias.clone().unwrap_or_else(|| table_ref.name.clone());
            scope.add_table(alias, schema.clone());
        }
        for join in &stmt.joins {
            let schema = self.catalog.get_table(&join.table.name).ok_or_else(|| {
                format!("table '{}' does not exist", join.table.name)
            })?;
            let alias = join.table.alias.clone().unwrap_or_else(|| join.table.name.clone());
            scope.add_table(alias, schema.clone());
        }

        // 2. 綁定 WHERE
        let where_expr = if let Some(expr) = &stmt.where_clause {
            Some(self.bind_expr(expr, &scope)?)
        } else {
            None
        };

        // 3. 綁定 projections
        let projections = stmt.projections.iter().map(|item| {
            let expr = self.bind_expr(&item.expr, &scope)?;
            Ok(BoundSelectItem { expr, alias: item.alias.clone() })
        }).collect::<Result<Vec<_>, String>>()?;

        // 4. 綁定 JOIN ON 條件
        let joins = stmt.joins.iter().map(|j| {
            let on = self.bind_expr(&j.on, &scope)?;
            let alias = j.table.alias.clone().unwrap_or_else(|| j.table.name.clone());
            Ok(BoundJoin { table_alias: alias, on })
        }).collect::<Result<Vec<_>, String>>()?;

        Ok(BoundSelect {
            projections,
            from_tables: stmt.from.iter().map(|t| {
                t.alias.clone().unwrap_or_else(|| t.name.clone())
            }).collect(),
            joins,
            where_clause: where_expr,
            limit: stmt.limit,
        })
    }

    fn bind_expr(&self, expr: &Expr, scope: &QueryScope) -> Result<TypedExpr, String> {
        match expr {
            Expr::Int(n)   => Ok(TypedExpr::Int(*n)),
            Expr::Float(f) => Ok(TypedExpr::Float(*f)),
            Expr::Str(s)   => Ok(TypedExpr::Str(s.clone())),
            Expr::Bool(b)  => Ok(TypedExpr::Bool(*b)),
            Expr::Null     => Ok(TypedExpr::Null),
            Expr::Wildcard => Ok(TypedExpr::Null), // SELECT * 由上層展開

            Expr::Column { table, name } => {
                let resolved = if let Some(t) = table {
                    let col = scope.resolve_column_qualified(t, name)?;
                    ResolvedColumn {
                        table_alias: t.clone(),
                        column_name: name.clone(),
                        data_type: col.data_type.clone(),
                        ordinal: col.ordinal,
                    }
                } else {
                    let (alias, col) = scope.resolve_column_unqualified(name)?;
                    ResolvedColumn {
                        table_alias: alias,
                        column_name: name.clone(),
                        data_type: col.data_type.clone(),
                        ordinal: col.ordinal,
                    }
                };
                Ok(TypedExpr::Column(resolved))
            }

            Expr::IsNull { expr, negated } => {
                let inner = self.bind_expr(expr, scope)?;
                Ok(TypedExpr::IsNull { expr: Box::new(inner), negated: *negated })
            }

            Expr::BinOp { op, left, right } => {
                let l = self.bind_expr(left, scope)?;
                let r = self.bind_expr(right, scope)?;
                let result_type = self.infer_binop_type(op, &l, &r)?;
                Ok(TypedExpr::BinOp {
                    op: op.clone(),
                    left: Box::new(l),
                    right: Box::new(r),
                    result_type,
                })
            }

            Expr::UnaryOp { op: UnaryOp::Neg, operand } => {
                let inner = self.bind_expr(operand, scope)?;
                match inner.data_type() {
                    Some(dt) if dt.is_numeric() => {
                        Ok(TypedExpr::BinOp {
                            op: BinOp::Mul,
                            left: Box::new(TypedExpr::Int(-1)),
                            right: Box::new(inner),
                            result_type: dt,
                        })
                    }
                    _ => Err("unary minus requires numeric operand".into()),
                }
            }

            Expr::UnaryOp { op: UnaryOp::Not, operand } => {
                let inner = self.bind_expr(operand, scope)?;
                match inner.data_type() {
                    Some(DataType::Boolean) | None => {
                        Ok(TypedExpr::BinOp {
                            op: BinOp::Eq,
                            left: Box::new(inner),
                            right: Box::new(TypedExpr::Bool(false)),
                            result_type: DataType::Boolean,
                        })
                    }
                    Some(dt) => Err(format!("NOT requires boolean, got {:?}", dt)),
                }
            }
        }
    }

    fn infer_binop_type(
        &self,
        op: &BinOp,
        left: &TypedExpr,
        right: &TypedExpr,
    ) -> Result<DataType, String> {
        match op {
            BinOp::And | BinOp::Or => Ok(DataType::Boolean),
            BinOp::Eq | BinOp::NotEq | BinOp::Lt | BinOp::Gt
            | BinOp::LtEq | BinOp::GtEq => {
                // 比較運算結果永遠是 Boolean
                let lt = left.data_type();
                let rt = right.data_type();
                match (lt, rt) {
                    (None, _) | (_, None) => Ok(DataType::Boolean), // NULL 參與比較，結果 NULL
                    (Some(l), Some(r)) if l.is_compatible_with(&r) => Ok(DataType::Boolean),
                    (Some(l), Some(r)) => Err(format!(
                        "type mismatch in comparison: {:?} vs {:?}", l, r
                    )),
                }
            }
            BinOp::Add | BinOp::Sub | BinOp::Mul | BinOp::Div => {
                let lt = left.data_type().ok_or("NULL in arithmetic")?;
                let rt = right.data_type().ok_or("NULL in arithmetic")?;
                if !lt.is_numeric() || !rt.is_numeric() {
                    return Err(format!("arithmetic requires numeric types, got {:?} {:?}", lt, rt));
                }
                // 如果任一是 Float 就提升到 Float
                if matches!(lt, DataType::Float) || matches!(rt, DataType::Float) {
                    Ok(DataType::Float)
                } else if matches!(lt, DataType::BigInt) || matches!(rt, DataType::BigInt) {
                    Ok(DataType::BigInt)
                } else {
                    Ok(DataType::Int)
                }
            }
        }
    }
}

/// Binder 的輸出：完成 name resolution 後的 SELECT 語句
#[derive(Debug)]
pub struct BoundSelect {
    pub projections: Vec<BoundSelectItem>,
    pub from_tables: Vec<String>,  // 表的別名列表
    pub joins: Vec<BoundJoin>,
    pub where_clause: Option<TypedExpr>,
    pub limit: Option<u64>,
}

#[derive(Debug)]
pub struct BoundSelectItem {
    pub expr: TypedExpr,
    pub alias: Option<String>,
}

#[derive(Debug)]
pub struct BoundJoin {
    pub table_alias: String,
    pub on: TypedExpr,
}
```

## 完整可編譯的測試 main

```rust
fn main() {
    // 1. 建立 Catalog，加入兩個 table
    let mut catalog = Catalog::new();
    catalog.create_table("employees", vec![
        ("id",       DataType::BigInt,   false),
        ("name",     DataType::Text,     false),
        ("salary",   DataType::Float,    false),
        ("dept_id",  DataType::Int,      true),
    ]);
    catalog.create_table("departments", vec![
        ("id",        DataType::Int,  false),
        ("dept_name", DataType::Text, false),
    ]);

    println!("=== Catalog ===");
    for (tname, tschema) in &catalog.public_schema().tables {
        println!("  TABLE {} ({} columns)", tname, tschema.columns.len());
        for col in &tschema.columns {
            println!("    {:?} {:?} nullable={}", col.name, col.data_type, col.nullable);
        }
    }

    // 2. 模擬 parse 得到的 AST（直接構造，不跑 parser）
    let ast = SelectStmt {
        projections: vec![
            SelectItem {
                expr: Expr::Column { table: Some("e".into()), name: "name".into() },
                alias: None,
            },
            SelectItem {
                expr: Expr::Column { table: Some("d".into()), name: "dept_name".into() },
                alias: None,
            },
        ],
        from: vec![TableRef { name: "employees".into(), alias: Some("e".into()) }],
        joins: vec![JoinClause {
            table: TableRef { name: "departments".into(), alias: Some("d".into()) },
            on: Expr::BinOp {
                op: BinOp::Eq,
                left: Box::new(Expr::Column { table: Some("e".into()), name: "dept_id".into() }),
                right: Box::new(Expr::Column { table: Some("d".into()), name: "id".into() }),
            },
        }],
        where_clause: Some(Expr::BinOp {
            op: BinOp::Gt,
            left: Box::new(Expr::Column { table: Some("e".into()), name: "salary".into() }),
            right: Box::new(Expr::Float(100000.0)),
        }),
        order_by: vec![],
        limit: Some(10),
    };

    // 3. 綁定
    let binder = Binder::new(&catalog);
    match binder.bind_select(&ast) {
        Ok(bound) => {
            println!("\n=== Bound SELECT ===");
            println!("FROM tables: {:?}", bound.from_tables);
            println!("Joins: {} join(s)", bound.joins.len());
            if let Some(w) = &bound.where_clause {
                println!("WHERE: {:?}", w);
            }
            println!("LIMIT: {:?}", bound.limit);
        }
        Err(e) => eprintln!("Bind error: {}", e),
    }

    // 4. 錯誤示範：引用不存在的欄位
    let bad_ast = SelectStmt {
        projections: vec![SelectItem {
            expr: Expr::Column { table: None, name: "nonexistent".into() },
            alias: None,
        }],
        from: vec![TableRef { name: "employees".into(), alias: None }],
        joins: vec![],
        where_clause: None,
        order_by: vec![],
        limit: None,
    };
    println!("\n=== Error case: nonexistent column ===");
    match binder.bind_select(&bad_ast) {
        Ok(_) => println!("should not reach here"),
        Err(e) => println!("Expected error: {}", e),
    }
}
```

執行 `cargo run` 應看到：

```
=== Catalog ===
  TABLE employees (4 columns)
    "id" BigInt nullable=false
    ...
=== Bound SELECT ===
FROM tables: ["e"]
Joins: 1 join(s)
WHERE: BinOp { op: Gt, left: Column(ResolvedColumn { table_alias: "e", column_name: "salary", ... }), ... }
LIMIT: Some(10)
=== Error case: nonexistent column ===
Expected error: column 'nonexistent' does not exist
```

## CREATE TABLE 流程

`CREATE TABLE` 是 catalog 的寫操作。流程：

```
SQL: "CREATE TABLE employees (id BIGINT NOT NULL, name TEXT, salary FLOAT);"
           │
           ▼ Parser (Ch 25)
      CreateTableStmt { name: "employees", columns: [...] }
           │
           ▼ Binder (本章)
      validate：table 是否已存在？型別是否合法？
           │ 沒問題
           ▼ Catalog::create_table()
      TableSchema 寫入 Catalog（記憶體）
      未來：序列化到磁碟（WAL → 頁面）
```

實際上寫入磁碟涉及 WAL（Ch 17）和 B-tree（Ch 4–8）——`CREATE TABLE` 本身就是一個需要持久化的交易。我們的記憶體實作跳過了這一步。

## 對比表格：各資料庫的 Catalog 實作

| 資料庫 | Catalog 儲存方式 | Schema 查詢 API |
|--------|----------------|----------------|
| PostgreSQL | 自己的 heap（`pg_catalog`） | `pg_class`, `pg_attribute` 等系統表 |
| SQLite | `sqlite_schema` 表（每 db 一個） | `SELECT * FROM sqlite_schema` |
| MySQL | `information_schema`（虛擬表） | `SHOW TABLES`, `DESCRIBE` |
| 我們的實作 | 記憶體 HashMap | `Catalog::get_table()` |

## 踩雷

1. **別名（alias）vs 原名的優先序**。當查詢裡有 `FROM employees e`，scope 裡的 key 應該是 `"e"` 而不是 `"employees"`。如果同時把兩個都放進去，`employees.salary` 和 `e.salary` 都能解析，但實作者常常只放別名而忘了「沒有別名時要用原名」。我們的實作用 `alias.unwrap_or(name)` 統一處理。

2. **SELECT * 的展開時機**。`SELECT *` 的 `Wildcard` 在 binder 層才展開成具體的欄位列表——因為只有 binder 知道 FROM 子句涉及哪些 table、各有哪些欄。Parser 看到 `*` 只需要產 `Expr::Wildcard`，不需要展開。展開後的欄位順序要按 table 加入 scope 的順序，再按各 table 的 `ordinal` 排。

3. **NULL 的型別推斷**。`NULL` 沒有具體型別，它是 absence of value。在 binder 的 `data_type()` 實作裡，`Expr::Null` 要回傳 `None` 而非某個具體型別。比較運算（`= NULL`）的結果型別是 Boolean，但值永遠是 NULL。很多初學者把 NULL 的型別設成 `DataType::Null` 然後到處加 special case——正確做法是用 `Option<DataType>`。

4. **Ambiguous column reference 要早報錯**。`SELECT salary FROM employees e JOIN employees e2 ON e.id = e2.manager_id` 裡，`salary` 在兩個 alias 下都存在，binder 要報 ambiguous error，而不是隨機選一個。這個錯誤如果推遲到 executor 才發現，debug 成本高很多。

5. **Catalog 的可見性是交易作用域的問題**。如果一個交易正在執行 `CREATE TABLE`，另一個交易的查詢應不應該看到這個 table？這是 schema change 與 MVCC 的交叉問題。PostgreSQL 的解法是 DDL 語句持有 AccessExclusiveLock，我們的記憶體實作暫不處理這個。

## 進階延伸

**Information schema**：SQL 標準定義了 `INFORMATION_SCHEMA` 這組虛擬視圖（`INFORMATION_SCHEMA.TABLES`、`INFORMATION_SCHEMA.COLUMNS`等），讓應用程式以標準 SQL 查詢 catalog。PostgreSQL 在 `information_schema` schema 裡用視圖實作它，這些視圖查的就是底層的 `pg_catalog` 系統表。

**Schema versioning / DDL 與 DML 共存**：生產資料庫需要在不停服的情況下做 `ALTER TABLE ADD COLUMN`。Facebook 的 OnlineSchemaChange、Percona 的 pt-online-schema-change 都是繞過鎖定限制的解法——本質上是在舊 schema 和新 schema 之間做雙寫 + 同步。理解 catalog 的結構是理解這些工具的基礎。

## 本章重點整理

- Catalog 是資料庫的 metadata 儲存，記錄 table/column/index 的定義
- Binder 的核心工作是 name resolution：把 AST 的字串名解析成 Catalog 的具體定義
- QueryScope 管理一次查詢可見的 table（含別名），是 name resolution 的查找上下文
- NULL 沒有具體型別，用 `Option<DataType>` 表示，不要設成 `DataType::Null`
- `SELECT *` 的展開在 binder 層做，parser 只產 `Wildcard`

## 自我檢核

- [ ] 我能說出 Catalog、Schema、TableSchema、ColumnDef 四層的關係
- [ ] 我能解釋為什麼「table 不存在」的錯誤在 binder 報，而不是 parser
- [ ] 我能說出 `SELECT *` 的展開在哪一層做，以及為什麼不在 parser 層
- [ ] 我能解釋為什麼 NULL 的型別要用 `Option<DataType>` 而不是 `DataType::Null`

## 延伸閱讀

1. **CMU 15-445 Lecture 13（Query Planning）的 Catalog 部分**
   讀什麼：Andy Pavlo 對 system catalog 的定義、catalog 在 query pipeline 中的位置、BusTub 的 catalog 實作結構
   關聯：本章的學術版本，看 BusTub 的 C++ Catalog 可以對比我們的 Rust 實作

2. **PostgreSQL 文件：[System Catalogs](https://www.postgresql.org/docs/current/catalogs.html)**（特別是 `pg_class` 和 `pg_attribute`）
   讀什麼：`pg_class` 的每個欄位代表什麼、`pg_attribute` 的 `attnum`/`atttypid` 怎麼對應到欄位定義
   關聯：我們的 `TableSchema.columns` 和 `ColumnDef.ordinal` 直接對應 `pg_attribute.attnum`

3. **《Database Internals》Ch 2（B-Tree Basics）引言部分**
   讀什麼：Petrov 描述 metadata 和資料如何共用同一個 B-tree 儲存引擎——catalog 本身也是資料，不是特殊的存在
   關聯：理解「catalog 用自己存自己」這個 bootstrap 性質的底層儲存基礎

4. **SQLite 原始碼 `src/parse.y`（`CREATE TABLE` 的 action 部分）**
   讀什麼：SQLite 如何在 parser action 裡把 column 定義存進 `sqlite_schema`，以及 schema 的序列化格式
   關聯：工業級最簡單的 catalog 實作，和我們的記憶體 HashMap 設計對比

→ [Ch 27 Logical Plan（關聯代數）](./27-logical-plan.md)
