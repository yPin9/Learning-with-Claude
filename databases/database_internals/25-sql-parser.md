# Ch 25 — SQL Parser

> **目標**：手寫一個能解析 SQL 子集的詞法分析器（lexer）和遞迴下降語法分析器（recursive descent parser），產出 AST。理解 SQL 文法的結構，以及 token 設計怎麼影響 parser 的複雜度。

> 如果你對 lexer/parser 的概念還不熟，先回看 [compiler_frontend Ch 1–3](../../compilers/compiler_frontend/01-frontend-overview.md)——那邊解釋了詞法 vs 語法、parse tree vs AST、DFA vs 遞迴下降，這裡不重複那些基礎。

## 為什麼要手寫 Parser？

資料庫有現成的 parser generator（sqlparser-rs crate），但從零手寫有兩個無法替代的價值：

**第一，你得真正讀懂 SQL 文法**。SQL 的 `WHERE` 子句表達式文法是有結合性（associativity）和優先序（precedence）的——`a + b * c AND d > 0 OR e IS NULL` 的解析順序由優先序決定。現成工具幫你隱藏了這個複雜度；手寫讓你直面它。

**第二，錯誤訊息的品質**。Parser generator 的錯誤訊息往往是「unexpected token」，手寫 parser 可以產出「expected column name after SELECT, found ','」這種有用的錯誤。PostgreSQL 從 yacc 切到手寫 parser 的理由之一就是錯誤品質。

## SQL 詞法：Token 的種類

SQL 的詞法（lexical structure）比大多數程式語言簡單——沒有縮排規則、沒有複雜的 string escape（至少子集裡沒有）。Token 種類：

```
關鍵字  (Keyword)     : SELECT, FROM, WHERE, AND, OR, NOT,
                        JOIN, ON, ORDER, BY, GROUP, HAVING,
                        INSERT, UPDATE, DELETE, CREATE, TABLE,
                        AS, IS, NULL, LIKE, IN, BETWEEN, ...

識別字  (Identifier)  : 表名、欄名、別名（e, dept_name, ...）

字面量  (Literal)     : 整數 42, 浮點 3.14, 字串 'hello', 布林 TRUE/FALSE

運算子  (Operator)    : =, !=, <>, <, >, <=, >=, +, -, *, /

標點    (Punctuation) : (, ), ,, ;, .（用於 table.column）

EOF                   : 輸入結束標記
```

關鍵字和識別字的差別在於：關鍵字是 SQL 保留的，識別字是使用者命名的。Lexer 的做法是先把所有字母字串讀出來，再查關鍵字表（hashmap 或 match 分支）——在表裡的是關鍵字，否則是識別字。

## SQL 文法：SELECT 的結構

我們支援的 SQL 子集的文法（BNF 表示）：

```
query       := select_stmt ';'?

select_stmt := 'SELECT' projection
               'FROM' table_ref (',' table_ref)*
               ('JOIN' table_ref 'ON' expr)*
               ('WHERE' expr)?
               ('ORDER' 'BY' order_item (',' order_item)*)?
               ('LIMIT' INTEGER)?

projection  := '*'
             | select_item (',' select_item)*
select_item := expr ('AS' IDENT)?

table_ref   := IDENT ('AS' IDENT)?

order_item  := expr ('ASC' | 'DESC')?

expr        := or_expr
or_expr     := and_expr ('OR' and_expr)*
and_expr    := not_expr ('AND' not_expr)*
not_expr    := 'NOT'? cmp_expr
cmp_expr    := add_expr (('='|'!='|'<'|'>'|'<='|'>='|'<>') add_expr)*
add_expr    := mul_expr (('+'|'-') mul_expr)*
mul_expr    := unary_expr (('*'|'/') unary_expr)*
unary_expr  := '-'? primary_expr
primary_expr:= INTEGER | FLOAT | STRING | 'TRUE' | 'FALSE' | 'NULL'
             | IDENT ('.' IDENT)?     -- column ref (可能帶 table qualifier)
             | '(' expr ')'
             | 'IS' 'NULL'            -- 作為後綴（在 cmp_expr 處理）
```

這個文法已經處理了優先序：OR < AND < NOT < 比較 < 加減 < 乘除 < 一元 < 基本量。遞迴下降的每個函式對應一個優先序層次，自然得到正確的結合性。

## Rust 實作：Token

```rust
// 未編譯驗證（邏輯已在後續完整範例中驗證）
#[derive(Debug, Clone, PartialEq)]
pub enum Token {
    // 關鍵字
    Select, From, Where, And, Or, Not,
    Join, On, Order, By, Asc, Desc,
    Limit, As, Is, Null, True, False,
    Create, Table, Insert, Into, Values,

    // 字面量
    IntLiteral(i64),
    FloatLiteral(f64),
    StringLiteral(String),

    // 識別字
    Ident(String),

    // 運算子
    Eq, NotEq, Lt, Gt, LtEq, GtEq,
    Plus, Minus, Star, Slash,

    // 標點
    LParen, RParen, Comma, Semicolon, Dot,

    // 結束
    Eof,
}
```

## Rust 實作：Lexer

```rust
pub struct Lexer {
    input: Vec<char>,
    pos: usize,
}

impl Lexer {
    pub fn new(input: &str) -> Self {
        Lexer { input: input.chars().collect(), pos: 0 }
    }

    fn peek(&self) -> Option<char> {
        self.input.get(self.pos).copied()
    }

    fn advance(&mut self) -> Option<char> {
        let ch = self.input.get(self.pos).copied();
        self.pos += 1;
        ch
    }

    fn skip_whitespace(&mut self) {
        while matches!(self.peek(), Some(' ' | '\t' | '\n' | '\r')) {
            self.advance();
        }
    }

    pub fn next_token(&mut self) -> Token {
        self.skip_whitespace();
        match self.peek() {
            None => Token::Eof,
            Some(ch) => match ch {
                '(' => { self.advance(); Token::LParen }
                ')' => { self.advance(); Token::RParen }
                ',' => { self.advance(); Token::Comma }
                ';' => { self.advance(); Token::Semicolon }
                '.' => { self.advance(); Token::Dot }
                '+' => { self.advance(); Token::Plus }
                '-' => { self.advance(); Token::Minus }
                '*' => { self.advance(); Token::Star }
                '/' => { self.advance(); Token::Slash }
                '=' => { self.advance(); Token::Eq }
                '<' => {
                    self.advance();
                    if self.peek() == Some('=') { self.advance(); Token::LtEq }
                    else if self.peek() == Some('>') { self.advance(); Token::NotEq }
                    else { Token::Lt }
                }
                '>' => {
                    self.advance();
                    if self.peek() == Some('=') { self.advance(); Token::GtEq }
                    else { Token::Gt }
                }
                '!' => {
                    self.advance();
                    if self.peek() == Some('=') { self.advance(); Token::NotEq }
                    else { panic!("unexpected char after '!': {:?}", self.peek()) }
                }
                '\'' => self.lex_string(),
                '0'..='9' => self.lex_number(),
                'a'..='z' | 'A'..='Z' | '_' => self.lex_ident_or_keyword(),
                other => panic!("unexpected character: {:?}", other),
            }
        }
    }

    fn lex_string(&mut self) -> Token {
        self.advance(); // consume '
        let mut s = String::new();
        loop {
            match self.advance() {
                None => panic!("unterminated string literal"),
                Some('\'') => break,
                Some(ch) => s.push(ch),
            }
        }
        Token::StringLiteral(s)
    }

    fn lex_number(&mut self) -> Token {
        let mut s = String::new();
        while matches!(self.peek(), Some('0'..='9')) {
            s.push(self.advance().unwrap());
        }
        if self.peek() == Some('.') {
            s.push(self.advance().unwrap());
            while matches!(self.peek(), Some('0'..='9')) {
                s.push(self.advance().unwrap());
            }
            Token::FloatLiteral(s.parse().unwrap())
        } else {
            Token::IntLiteral(s.parse().unwrap())
        }
    }

    fn lex_ident_or_keyword(&mut self) -> Token {
        let mut s = String::new();
        while matches!(self.peek(), Some('a'..='z' | 'A'..='Z' | '0'..='9' | '_')) {
            s.push(self.advance().unwrap());
        }
        // 關鍵字不區分大小寫
        match s.to_uppercase().as_str() {
            "SELECT" => Token::Select,
            "FROM"   => Token::From,
            "WHERE"  => Token::Where,
            "AND"    => Token::And,
            "OR"     => Token::Or,
            "NOT"    => Token::Not,
            "JOIN"   => Token::Join,
            "ON"     => Token::On,
            "ORDER"  => Token::Order,
            "BY"     => Token::By,
            "ASC"    => Token::Asc,
            "DESC"   => Token::Desc,
            "LIMIT"  => Token::Limit,
            "AS"     => Token::As,
            "IS"     => Token::Is,
            "NULL"   => Token::Null,
            "TRUE"   => Token::True,
            "FALSE"  => Token::False,
            "CREATE" => Token::Create,
            "TABLE"  => Token::Table,
            "INSERT" => Token::Insert,
            "INTO"   => Token::Into,
            "VALUES" => Token::Values,
            _        => Token::Ident(s),
        }
    }

    /// 把整個輸入詞法化成 token 陣列（方便 parser 使用）
    pub fn tokenize(&mut self) -> Vec<Token> {
        let mut tokens = Vec::new();
        loop {
            let tok = self.next_token();
            let done = tok == Token::Eof;
            tokens.push(tok);
            if done { break; }
        }
        tokens
    }
}
```

## AST 節點設計

AST 需要精確反映 SQL 的語義結構，同時不要照抄 SQL 語法（parse tree 的錯誤）。

```rust
/// 頂層：一條 SQL 語句
#[derive(Debug, Clone)]
pub enum Stmt {
    Select(SelectStmt),
    CreateTable(CreateTableStmt),
    Insert(InsertStmt),
}

#[derive(Debug, Clone)]
pub struct SelectStmt {
    pub projections: Vec<SelectItem>,  // SELECT 後面的欄位/表達式
    pub from: Vec<TableRef>,           // FROM 後面的表
    pub joins: Vec<JoinClause>,        // JOIN ... ON ...
    pub where_clause: Option<Expr>,    // WHERE
    pub order_by: Vec<OrderItem>,      // ORDER BY
    pub limit: Option<u64>,            // LIMIT
}

#[derive(Debug, Clone)]
pub struct SelectItem {
    pub expr: Expr,
    pub alias: Option<String>,         // AS alias
}

#[derive(Debug, Clone)]
pub struct TableRef {
    pub name: String,
    pub alias: Option<String>,
}

#[derive(Debug, Clone)]
pub struct JoinClause {
    pub table: TableRef,
    pub on: Expr,
}

#[derive(Debug, Clone)]
pub struct OrderItem {
    pub expr: Expr,
    pub asc: bool,                     // true = ASC, false = DESC
}

/// 表達式：SQL 中最複雜的部分
#[derive(Debug, Clone)]
pub enum Expr {
    // 字面量
    Int(i64),
    Float(f64),
    Str(String),
    Bool(bool),
    Null,

    // 欄位引用（可帶 table qualifier）
    Column { table: Option<String>, name: String },

    // 二元運算
    BinOp { op: BinOp, left: Box<Expr>, right: Box<Expr> },

    // 一元運算
    UnaryOp { op: UnaryOp, operand: Box<Expr> },

    // IS NULL / IS NOT NULL
    IsNull { expr: Box<Expr>, negated: bool },

    // 萬用字元（SELECT * 的情況）
    Wildcard,
}

#[derive(Debug, Clone, PartialEq)]
pub enum BinOp {
    Eq, NotEq, Lt, Gt, LtEq, GtEq,
    And, Or,
    Add, Sub, Mul, Div,
}

#[derive(Debug, Clone)]
pub enum UnaryOp {
    Neg,   // -
    Not,   // NOT
}

#[derive(Debug, Clone)]
pub struct CreateTableStmt {
    pub name: String,
    pub columns: Vec<ColumnDef>,
}

#[derive(Debug, Clone)]
pub struct ColumnDef {
    pub name: String,
    pub data_type: DataType,
    pub nullable: bool,
}

#[derive(Debug, Clone)]
pub enum DataType {
    Int,
    BigInt,
    Float,
    Text,
    Boolean,
}

#[derive(Debug, Clone)]
pub struct InsertStmt {
    pub table: String,
    pub columns: Vec<String>,
    pub values: Vec<Vec<Expr>>,
}
```

## Rust 實作：Parser（完整可編譯）

```rust
pub struct Parser {
    tokens: Vec<Token>,
    pos: usize,
}

impl Parser {
    pub fn new(tokens: Vec<Token>) -> Self {
        Parser { tokens, pos: 0 }
    }

    fn peek(&self) -> &Token {
        &self.tokens[self.pos]
    }

    fn advance(&mut self) -> &Token {
        let tok = &self.tokens[self.pos];
        if self.pos + 1 < self.tokens.len() {
            self.pos += 1;
        }
        tok
    }

    fn expect(&mut self, expected: &Token) -> Result<(), String> {
        if self.peek() == expected {
            self.advance();
            Ok(())
        } else {
            Err(format!("expected {:?}, found {:?}", expected, self.peek()))
        }
    }

    fn eat(&mut self, tok: &Token) -> bool {
        if self.peek() == tok {
            self.advance();
            true
        } else {
            false
        }
    }

    pub fn parse_stmt(&mut self) -> Result<Stmt, String> {
        match self.peek().clone() {
            Token::Select => Ok(Stmt::Select(self.parse_select()?)),
            Token::Create => Ok(Stmt::CreateTable(self.parse_create_table()?)),
            Token::Insert => Ok(Stmt::Insert(self.parse_insert()?)),
            tok => Err(format!("unexpected token at statement start: {:?}", tok)),
        }
    }

    fn parse_select(&mut self) -> Result<SelectStmt, String> {
        self.expect(&Token::Select)?;

        // SELECT projections
        let projections = self.parse_select_list()?;

        // FROM table_refs
        self.expect(&Token::From)?;
        let mut from = vec![self.parse_table_ref()?];
        while self.eat(&Token::Comma) {
            from.push(self.parse_table_ref()?);
        }

        // JOIN clauses
        let mut joins = Vec::new();
        while self.eat(&Token::Join) {
            let table = self.parse_table_ref()?;
            self.expect(&Token::On)?;
            let on = self.parse_expr()?;
            joins.push(JoinClause { table, on });
        }

        // WHERE
        let where_clause = if self.eat(&Token::Where) {
            Some(self.parse_expr()?)
        } else {
            None
        };

        // ORDER BY
        let mut order_by = Vec::new();
        if self.eat(&Token::Order) {
            self.expect(&Token::By)?;
            order_by.push(self.parse_order_item()?);
            while self.eat(&Token::Comma) {
                order_by.push(self.parse_order_item()?);
            }
        }

        // LIMIT
        let limit = if self.eat(&Token::Limit) {
            if let Token::IntLiteral(n) = self.advance().clone() {
                Some(n as u64)
            } else {
                return Err("expected integer after LIMIT".into());
            }
        } else {
            None
        };

        self.eat(&Token::Semicolon);

        Ok(SelectStmt { projections, from, joins, where_clause, order_by, limit })
    }

    fn parse_select_list(&mut self) -> Result<Vec<SelectItem>, String> {
        if self.eat(&Token::Star) {
            return Ok(vec![SelectItem { expr: Expr::Wildcard, alias: None }]);
        }
        let mut items = vec![self.parse_select_item()?];
        while self.eat(&Token::Comma) {
            items.push(self.parse_select_item()?);
        }
        Ok(items)
    }

    fn parse_select_item(&mut self) -> Result<SelectItem, String> {
        let expr = self.parse_expr()?;
        let alias = if self.eat(&Token::As) {
            if let Token::Ident(name) = self.advance().clone() {
                Some(name)
            } else {
                return Err("expected alias name after AS".into());
            }
        } else {
            None
        };
        Ok(SelectItem { expr, alias })
    }

    fn parse_table_ref(&mut self) -> Result<TableRef, String> {
        let name = match self.advance().clone() {
            Token::Ident(s) => s,
            tok => return Err(format!("expected table name, found {:?}", tok)),
        };
        let alias = if self.eat(&Token::As) {
            if let Token::Ident(s) = self.advance().clone() {
                Some(s)
            } else {
                return Err("expected alias after AS".into());
            }
        } else if let Token::Ident(s) = self.peek().clone() {
            // 支援 "employees e" 這種沒有 AS 的別名
            self.advance();
            Some(s)
        } else {
            None
        };
        Ok(TableRef { name, alias })
    }

    fn parse_order_item(&mut self) -> Result<OrderItem, String> {
        let expr = self.parse_expr()?;
        let asc = if self.eat(&Token::Desc) { false } else { self.eat(&Token::Asc); true };
        Ok(OrderItem { expr, asc })
    }

    // ──────────────────────────────────────
    // 表達式解析（遞迴下降，處理優先序）
    // ──────────────────────────────────────

    fn parse_expr(&mut self) -> Result<Expr, String> {
        self.parse_or()
    }

    fn parse_or(&mut self) -> Result<Expr, String> {
        let mut left = self.parse_and()?;
        while self.eat(&Token::Or) {
            let right = self.parse_and()?;
            left = Expr::BinOp {
                op: BinOp::Or,
                left: Box::new(left),
                right: Box::new(right),
            };
        }
        Ok(left)
    }

    fn parse_and(&mut self) -> Result<Expr, String> {
        let mut left = self.parse_not()?;
        while self.eat(&Token::And) {
            let right = self.parse_not()?;
            left = Expr::BinOp {
                op: BinOp::And,
                left: Box::new(left),
                right: Box::new(right),
            };
        }
        Ok(left)
    }

    fn parse_not(&mut self) -> Result<Expr, String> {
        if self.eat(&Token::Not) {
            let operand = self.parse_not()?;
            Ok(Expr::UnaryOp { op: UnaryOp::Not, operand: Box::new(operand) })
        } else {
            self.parse_cmp()
        }
    }

    fn parse_cmp(&mut self) -> Result<Expr, String> {
        let mut left = self.parse_add()?;
        loop {
            let op = match self.peek() {
                Token::Eq    => BinOp::Eq,
                Token::NotEq => BinOp::NotEq,
                Token::Lt    => BinOp::Lt,
                Token::Gt    => BinOp::Gt,
                Token::LtEq  => BinOp::LtEq,
                Token::GtEq  => BinOp::GtEq,
                Token::Is    => {
                    self.advance(); // consume IS
                    let negated = self.eat(&Token::Not);
                    self.expect(&Token::Null)?;
                    left = Expr::IsNull { expr: Box::new(left), negated };
                    continue;
                }
                _ => break,
            };
            self.advance();
            let right = self.parse_add()?;
            left = Expr::BinOp { op, left: Box::new(left), right: Box::new(right) };
        }
        Ok(left)
    }

    fn parse_add(&mut self) -> Result<Expr, String> {
        let mut left = self.parse_mul()?;
        loop {
            let op = match self.peek() {
                Token::Plus  => BinOp::Add,
                Token::Minus => BinOp::Sub,
                _ => break,
            };
            self.advance();
            let right = self.parse_mul()?;
            left = Expr::BinOp { op, left: Box::new(left), right: Box::new(right) };
        }
        Ok(left)
    }

    fn parse_mul(&mut self) -> Result<Expr, String> {
        let mut left = self.parse_unary()?;
        loop {
            let op = match self.peek() {
                Token::Star  => BinOp::Mul,
                Token::Slash => BinOp::Div,
                _ => break,
            };
            self.advance();
            let right = self.parse_unary()?;
            left = Expr::BinOp { op, left: Box::new(left), right: Box::new(right) };
        }
        Ok(left)
    }

    fn parse_unary(&mut self) -> Result<Expr, String> {
        if self.eat(&Token::Minus) {
            let operand = self.parse_primary()?;
            Ok(Expr::UnaryOp { op: UnaryOp::Neg, operand: Box::new(operand) })
        } else {
            self.parse_primary()
        }
    }

    fn parse_primary(&mut self) -> Result<Expr, String> {
        match self.peek().clone() {
            Token::IntLiteral(n) => { self.advance(); Ok(Expr::Int(n)) }
            Token::FloatLiteral(f) => { self.advance(); Ok(Expr::Float(f)) }
            Token::StringLiteral(s) => { self.advance(); Ok(Expr::Str(s)) }
            Token::True  => { self.advance(); Ok(Expr::Bool(true)) }
            Token::False => { self.advance(); Ok(Expr::Bool(false)) }
            Token::Null  => { self.advance(); Ok(Expr::Null) }
            Token::LParen => {
                self.advance();
                let expr = self.parse_expr()?;
                self.expect(&Token::RParen)?;
                Ok(expr)
            }
            Token::Ident(name) => {
                self.advance();
                // table.column 的情況
                if self.eat(&Token::Dot) {
                    if let Token::Ident(col) = self.advance().clone() {
                        Ok(Expr::Column { table: Some(name), name: col })
                    } else {
                        Err("expected column name after '.'".into())
                    }
                } else {
                    Ok(Expr::Column { table: None, name })
                }
            }
            tok => Err(format!("unexpected token in expression: {:?}", tok)),
        }
    }

    fn parse_create_table(&mut self) -> Result<CreateTableStmt, String> {
        self.expect(&Token::Create)?;
        self.expect(&Token::Table)?;
        let name = match self.advance().clone() {
            Token::Ident(s) => s,
            tok => return Err(format!("expected table name, found {:?}", tok)),
        };
        self.expect(&Token::LParen)?;
        let mut columns = Vec::new();
        loop {
            let col_name = match self.advance().clone() {
                Token::Ident(s) => s,
                tok => return Err(format!("expected column name, found {:?}", tok)),
            };
            let data_type = self.parse_data_type()?;
            let nullable = !self.try_consume_not_null();
            columns.push(ColumnDef { name: col_name, data_type, nullable });
            if !self.eat(&Token::Comma) { break; }
        }
        self.expect(&Token::RParen)?;
        self.eat(&Token::Semicolon);
        Ok(CreateTableStmt { name, columns })
    }

    fn parse_data_type(&mut self) -> Result<DataType, String> {
        match self.advance().clone() {
            Token::Ident(s) => match s.to_uppercase().as_str() {
                "INT" | "INTEGER" => Ok(DataType::Int),
                "BIGINT"          => Ok(DataType::BigInt),
                "FLOAT" | "REAL"  => Ok(DataType::Float),
                "TEXT" | "VARCHAR"=> Ok(DataType::Text),
                "BOOLEAN" | "BOOL"=> Ok(DataType::Boolean),
                other => Err(format!("unknown data type: {}", other)),
            },
            tok => Err(format!("expected data type, found {:?}", tok)),
        }
    }

    fn try_consume_not_null(&mut self) -> bool {
        // 非常簡化：如果看到 NOT NULL 就吃掉
        if self.peek() == &Token::Not {
            self.advance();
            self.eat(&Token::Null);
            true
        } else {
            false
        }
    }

    fn parse_insert(&mut self) -> Result<InsertStmt, String> {
        self.expect(&Token::Insert)?;
        self.expect(&Token::Into)?;
        let table = match self.advance().clone() {
            Token::Ident(s) => s,
            tok => return Err(format!("expected table name, found {:?}", tok)),
        };
        self.expect(&Token::LParen)?;
        let mut columns = Vec::new();
        loop {
            match self.advance().clone() {
                Token::Ident(s) => columns.push(s),
                tok => return Err(format!("expected column name, found {:?}", tok)),
            }
            if !self.eat(&Token::Comma) { break; }
        }
        self.expect(&Token::RParen)?;
        self.expect(&Token::Values)?;
        let mut rows = Vec::new();
        loop {
            self.expect(&Token::LParen)?;
            let mut row = Vec::new();
            loop {
                row.push(self.parse_primary()?);
                if !self.eat(&Token::Comma) { break; }
            }
            self.expect(&Token::RParen)?;
            rows.push(row);
            if !self.eat(&Token::Comma) { break; }
        }
        self.eat(&Token::Semicolon);
        Ok(InsertStmt { table, columns, values: rows })
    }
}
```

## 完整可編譯的 main 測試

把以上所有程式碼（Token、Lexer、AST nodes、Parser）放進 `src/main.rs`，加上這個 main：

```rust
fn main() {
    let sql = "SELECT e.name, d.dept_name \
               FROM employees e \
               JOIN departments d ON e.dept_id = d.id \
               WHERE e.salary > 100000 \
               ORDER BY e.name ASC \
               LIMIT 10;";

    println!("=== Input SQL ===");
    println!("{}", sql);

    let mut lexer = Lexer::new(sql);
    let tokens = lexer.tokenize();

    println!("\n=== Tokens ===");
    for tok in &tokens {
        println!("  {:?}", tok);
    }

    let mut parser = Parser::new(tokens);
    match parser.parse_stmt() {
        Ok(stmt) => {
            println!("\n=== AST ===");
            println!("{:#?}", stmt);
        }
        Err(e) => eprintln!("Parse error: {}", e),
    }
}
```

執行（WSL）：
```
cargo run
```

預期輸出片段：
```
=== AST ===
Select(
    SelectStmt {
        projections: [
            SelectItem { expr: Column { table: Some("e"), name: "name" }, alias: None },
            SelectItem { expr: Column { table: Some("d"), name: "dept_name" }, alias: None },
        ],
        from: [ TableRef { name: "employees", alias: Some("e") } ],
        joins: [ JoinClause {
            table: TableRef { name: "departments", alias: Some("d") },
            on: BinOp { op: Eq,
                left: Column { table: Some("e"), name: "dept_id" },
                right: Column { table: Some("d"), name: "id" }
            }
        }],
        where_clause: Some(BinOp { op: Gt,
            left: Column { table: Some("e"), name: "salary" },
            right: Int(100000)
        }),
        order_by: [ OrderItem { expr: Column { table: Some("e"), name: "name" }, asc: true } ],
        limit: Some(10),
    }
)
```

## 對比表格：手寫 Parser vs Parser Generator

| 維度 | 手寫遞迴下降 | sqlparser-rs / nom / pest |
|------|-------------|--------------------------|
| 實作複雜度 | 中（但完全掌控） | 低（寫文法規則） |
| 錯誤訊息品質 | 可精確控制 | 通常差 |
| 效能 | 快（無額外間接層） | 通常夠快 |
| 調試難度 | 低（就是普通函式） | 高（宏/trait 黑魔法） |
| 完整 SQL 標準 | 工作量巨大 | crate 幫你處理 |
| 學習價值 | 高 | 低 |

教學用途：手寫。Production：視規模，小型 DB 手寫，大型考慮 sqlparser-rs。

## 踩雷

1. **關鍵字和識別字的衝突**。`ORDER`、`BY`、`AS` 在某些 SQL 方言裡可以當識別字用（`SELECT as FROM t`）。處理方式有兩種：禁用（最簡單）、或在 parser 遇到識別字預期位置時同時接受關鍵字 token。我們的實作選前者。

2. **`IS NULL` 的位置**。`IS NULL` / `IS NOT NULL` 是後綴運算子，必須在 `cmp_expr` 層處理，不是前綴也不是 `primary`。很多人第一次寫時把它放錯層，導致 `a = 1 IS NULL` 無法正確解析。

3. **別名沒有 AS 關鍵字的情況**。`FROM employees e` 中 `e` 是別名，沒有 `AS`。Table alias 可以省 AS，但 column alias 通常不能。parser 在 parse_table_ref 裡要特別處理：看到 FROM 後的 IDENT 之後，如果下一個 token 還是 IDENT（且不是關鍵字），就是別名。

4. **`SELECT *` 的特殊處理**。`*` 在 SQL 裡同時是乘號和萬用字元。Lexer 只產一種 `Token::Star`，parser 在 `parse_select_list` 裡遇到 Star 直接返回 Wildcard，在 expression context 裡遇到 Star 就是乘號。靠上下文（parse 函式的呼叫位置）區分，不需要兩種 token。

5. **優先序層次不能跳**。遞迴下降的每個函式必須對應一個優先序層次，你不能為了省程式碼把兩個層次合併——那樣會破壞結合性。`a - b - c` 必須解析成 `(a - b) - c`（左結合），如果你把加減和乘除放同一個函式就會弄錯。

## 進階延伸

**Pratt Parser**：比遞迴下降更適合表達式解析的技術，用 binding power（綁定力）數值代替多層函式。對 SQL 的 infix 操作符密集部分特別有用。可讀 Matklad 的 [Simple but Powerful Pratt Parsing](https://matklad.github.io/2020/04/13/simple-but-powerful-pratt-parsing.html)。

**Error recovery**：工業級 parser 遇到語法錯誤不會直接 panic，而是跳到下一個同步點（如 `;`）繼續嘗試 parse，收集所有錯誤後一次報告。這需要 parser 的錯誤處理從 `panic!` 改成 `Result` + 同步機制。

**位置資訊（span）**：AST 節點應該帶上它在原始 SQL 字串的位置（byte offset），這樣錯誤訊息才能精確指到出問題的那個 token。每個 token 加 `span: (usize, usize)` 欄位是最直接的方式。

## 本章重點整理

- SQL 詞法：Keyword / Identifier / Literal / Operator / Punctuation 五種 token 類型
- 關鍵字大小寫不敏感，lexer 在 `lex_ident_or_keyword` 裡用 `to_uppercase` 統一
- 遞迴下降：每個優先序層次一個函式，函式間的呼叫關係建立優先序
- AST 設計：`Stmt → SelectStmt/CreateTableStmt/InsertStmt`，`Expr` 是遞迴 enum
- `SELECT *` 的 `*`、乘法的 `*` 靠 parse 上下文區分，不靠兩種 token

## 自我檢核

- [ ] 我能說出 SQL 詞法的五種 token 類型，並各舉一例
- [ ] 我能解釋為什麼遞迴下降能正確處理優先序（每層函式對應一個優先序）
- [ ] 我能說出 `IS NULL` 為什麼要在 `cmp_expr` 層處理，而不是 `primary`
- [ ] 我能手動追蹤 `"salary > 100000"` 的 parse 過程，畫出它的 AST

## 延伸閱讀

1. **CMU 15-445 Lecture 13（Query Processing）前半段**
   讀什麼：query processing 的整體介紹，parser 在其中的位置，以及 Andy Pavlo 對 SQL 語法解析的簡短說明
   關聯：本章實作的學術背景，幫你把手寫 code 連結到資料庫系統課程框架

2. **compiler_frontend Ch 2–4（本 repo）**
   讀什麼：DFA/NFA 與 lexer 的關係、LL(1) 文法與遞迴下降的對應、first/follow set
   關聯：本章 lexer/parser 技術的理論基礎，SQL 子集的文法接近 LL(1)，所以遞迴下降直接適用

3. **[sqlparser-rs 原始碼](https://github.com/sqlparser-rs/sqlparser-rs)**（特別是 `src/parser/mod.rs`）
   讀什麼：工業級手寫 SQL parser 的結構——token 設計、優先序處理、錯誤恢復
   關聯：我們的實作是精簡版，看這份 code 了解「完整 SQL 標準需要多少額外複雜度」

4. **Matklad：[Simple but Powerful Pratt Parsing](https://matklad.github.io/2020/04/13/simple-but-powerful-pratt-parsing.html)**
   讀什麼：Pratt parser 技術，用 binding power 取代多層遞迴函式
   關聯：本章 `parse_or/and/not/cmp/add/mul` 的替代設計，SQL expression 層特別值得改用 Pratt

→ [Ch 26 Catalog / Schema](./26-catalog-schema.md)
