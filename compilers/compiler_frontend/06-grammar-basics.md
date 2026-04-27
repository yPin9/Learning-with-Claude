# Ch 6 — 文法基礎與 BNF

> 目標：掌握描述程式語言語法所需的文法概念。BNF、推導、左右遞迴、二義性是理解 yacc 輸入的前提。

## 為什麼需要「文法」？

lexer 負責認出 token，但 token 組成「合法句子」有規矩。考慮：

```
IDENT PLUS IDENT         → 合法：a + b
IDENT PLUS PLUS IDENT    → 非法（在大部分語言）
```

我們需要一種形式化的方式來描述「什麼順序的 token 合法」。這就是 **文法** (grammar)。

## BNF (Backus-Naur Form)

BNF 是描述上下文無關文法的標準符號。一條規則長這樣：

```
<expr> ::= <expr> "+" <expr>
         | <expr> "*" <expr>
         | <number>
```

讀法：「一個 expr 可以是 expr + expr，或 expr * expr，或一個 number」。

幾個術語：

- **非終結符** (non-terminal)：`<expr>`、`<number>`，用 `<>` 包圍，代表「還能再展開的東西」。
- **終結符** (terminal)：`"+"`、`"*"`，就是 lexer 送來的 token。
- **產生式** (production)：一條規則，例如 `<expr> ::= <expr> "+" <expr>`。
- **開始符號** (start symbol)：整個程式的根，通常是 `<program>` 或 `<translation_unit>`。

### yacc 的寫法

yacc/bison 用的是 BNF 的變種，省掉尖括號和引號：

```
expr : expr '+' expr
     | expr '*' expr
     | NUMBER
     ;
```

大小寫慣例：
- **小寫** 是非終結符（`expr`）
- **大寫** 是終結符/token（`NUMBER`、`IF`）
- **字面字元** 用單引號（`'+'`）

## 推導 (Derivation)

文法描述了「哪些句子是合法的」，方式是**推導**：從開始符號出發，反覆套用產生式替換非終結符，直到整串只剩終結符。

以文法：
```
expr : expr '+' expr
     | NUMBER
```

推導 `1 + 2 + 3`：

```
expr
→ expr '+' expr
→ expr '+' expr '+' expr   （把第一個 expr 展開）
→ NUMBER '+' expr '+' expr
→ NUMBER '+' NUMBER '+' expr
→ NUMBER '+' NUMBER '+' NUMBER
→ 1 + 2 + 3
```

能推導出某個串 → 該串屬於這個文法能產生的語言。

## Parse Tree

把推導過程畫成樹，就是 parse tree：

```
        expr
       / | \
      expr + expr
     /|\       \
    expr + expr  NUMBER(3)
     |       |
   NUMBER   NUMBER
     |       |
     1       2
```

葉子是終結符，內部節點是非終結符。

## 二義性 (Ambiguity)

上面的推導有個問題：同樣的 `1 + 2 + 3`，我可以**另一種**推導：

```
expr
→ expr '+' expr
→ NUMBER '+' expr             （把第二個 expr 展開）
→ NUMBER '+' expr '+' expr
→ 1 + 2 + 3
```

對應的 parse tree：

```
        expr
       / | \
      expr + expr
     /     /|\
    1     expr + expr
           |       |
           2       3
```

**同一個輸入、同一個文法，可以推出兩棵不同的樹**。這叫**二義性**。

這很糟糕，因為語義可能不同：`(1+2)+3` vs `1+(2+3)` 在整數加法下一樣，但換成減法 `1-2-3`：
- `(1-2)-3 = -4`
- `1-(2-3) = 2`

完全不同答案。

## 解決二義性的三種方法

### 方法 1：改寫文法

把二義文法重寫為不二義的等價文法。對加法來說，把左結合編進文法：

```
expr : expr '+' term
     | term
     ;

term : NUMBER ;
```

現在 `1 + 2 + 3` 只能推成：
```
expr → expr '+' term → expr '+' term '+' term → term '+' term '+' term → 1 + 2 + 3
```

parse tree 是左傾的，強制左結合。

**左遞迴 vs 右遞迴**：

左遞迴（yacc 偏好）：
```
expr : expr '+' term | term ;   /* 左結合 */
```

右遞迴（yacc 可以但效率差一點）：
```
expr : term '+' expr | term ;   /* 右結合 */
```

記住：**yacc/LALR 偏好左遞迴**，recursive descent 手寫的那種偏好右遞迴。

### 方法 2：加入優先級

當有多種運算子時（`+` 跟 `*`），要加層級：

```
expr : expr '+' term | term ;
term : term '*' factor | factor ;
factor : NUMBER | '(' expr ')' ;
```

這樣 `1 + 2 * 3` 強制被推成 `1 + (2 * 3)`，因為 `*` 在更深一層。

### 方法 3：告訴 parser 優先級（yacc 特有）

yacc 提供 `%left` `%right` `%nonassoc` 宣告：

```
%left '+' '-'
%left '*' '/'

%%
expr : expr '+' expr
     | expr '-' expr
     | expr '*' expr
     | expr '/' expr
     | NUMBER
     ;
```

這其實是二義文法，但 yacc 用宣告的優先級去化解衝突。Ch 9 會細講。

## 常見的文法寫法模式

### 列表（一個或多個）

```
args : expr
     | args ',' expr
     ;
```

### 可選列表（零個或多個）

```
args : /* empty */
     | args ',' expr
     ;
```

注意：`/* empty */` 就是空字串，yacc 允許規則右邊是空的。

但這個寫法**必須有個 `expr` 打頭**，否則第一個 `expr` 沒放。通常寫成：

```
args : /* empty */
     | arg_list
     ;

arg_list : expr
         | arg_list ',' expr
         ;
```

### 可選元素

```
else_opt : /* empty */
         | ELSE stmt
         ;

if_stmt : IF '(' expr ')' stmt else_opt ;
```

## 一個完整範例：簡易表達式文法

```
program : stmt_list ;

stmt_list : stmt
          | stmt_list stmt
          ;

stmt : expr ';'
     | IF '(' expr ')' stmt
     | IF '(' expr ')' stmt ELSE stmt
     | '{' stmt_list '}'
     ;

expr : expr '+' term
     | expr '-' term
     | term
     ;

term : term '*' factor
     | term '/' factor
     | factor
     ;

factor : NUMBER
       | IDENT
       | '(' expr ')'
       ;
```

這個文法：
- 左遞迴（yacc 友好）
- 編碼了左結合
- 編碼了 `*/` 優先於 `+-`
- 仍然有一個著名的 **dangling else** 二義性（Ch 9 處理）

## 動手練習

用紙筆或任何工具：

1. 根據上面的文法，畫出 `1 + 2 * 3` 的 parse tree。
2. 畫出 `(1 + 2) * 3` 的 parse tree。
3. 思考：如果我把 `term : term '*' factor | factor` 改成 `term : factor '*' term | factor`（右遞迴），`1 / 2 / 4` 會被解釋成 `(1/2)/4` 還是 `1/(2/4)`？（答：後者，結果是 1/0.5=2，而正常的除法左結合應該是 0.125）
4. 設計一個文法，能識別「括號對稱的字串」：`()`、`(())`、`(()())` 合法，`(()` 不合法。

## 自我檢核

- [ ] 我能用 BNF 寫出簡單表達式的文法
- [ ] 我能解釋二義性並舉一個例子
- [ ] 我知道 yacc 偏好左遞迴
- [ ] 我知道三種消除二義性的方法

→ [Ch 7 LR/LALR 直覺](./07-lr-lalr-intuition.md)
