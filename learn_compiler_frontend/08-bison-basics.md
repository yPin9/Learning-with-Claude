# Ch 8 — bison 基本語法

> 目標：寫出一個完整可跑的 `.y` 檔，從宣告到文法到 `main()`，知道每一行在做什麼。

## .y 檔結構

跟 `.l` 一樣是三段式：

```
宣告區 (declarations)
%%
文法規則區 (rules)
%%
使用者程式碼 (user code)
```

## 宣告區

### C 程式碼

```bison
%{
#include <stdio.h>
#include <stdlib.h>
extern int yylex(void);
extern int yylineno;
void yyerror(const char *s);
%}
```

`yylex()` 跟 `yylineno` 是 flex 提供的，要聲明一下。`yyerror()` 是當 parser 遇到語法錯誤時會呼叫的函式，**你必須自己實作**。

### Token 宣告

所有 lexer 會回傳的 token 都要在這裡宣告：

```bison
%token NUMBER
%token IF ELSE WHILE RETURN
%token IDENT
```

這些會變成 C 的 `#define`（或 enum），編號從 258 開始（避開 ASCII）。

### 類型宣告：%union

每個 token 或非終結符可能有不同型別的「值」。用 `%union` 定義所有可能：

```bison
%union {
    int ival;
    double fval;
    char *sval;
}
```

然後**指定**哪個 token/規則用哪個欄位：

```bison
%token <ival> NUMBER
%token <sval> IDENT
%type  <ival> expr
```

- `%token <欄位>` 用於終結符
- `%type <欄位>` 用於非終結符

`<ival>` 指的是 `union` 裡的 `ival` 欄位。bison 會生成對應的 `yylval.ival` 賦值邏輯。

### 優先級與結合性

```bison
%left  '+' '-'
%left  '*' '/'
%right '^'
%nonassoc UMINUS
```

- `%left`：左結合（`a + b + c` → `(a+b)+c`）
- `%right`：右結合（`a ^ b ^ c` → `a^(b^c)`）
- `%nonassoc`：不允許結合（`a < b < c` 要報錯）

**位置越下面，優先級越高**。上面的例子：`^` > `*/` > `+-`。

### 開始符號

```bison
%start program
```

可選，預設是第一條規則的左邊。

### 錯誤追蹤

```bison
%define parse.error verbose    /* 詳細錯誤訊息 */
%locations                      /* 啟用 @1 @2 等位置變數 */
```

## 規則區

### 基本格式

```bison
rule_name : alternative_1   { $$ = ...; }
          | alternative_2   { $$ = ...; }
          | /* empty */     { $$ = ...; }
          ;
```

每條替代後面可以跟一個 C 動作 block，用 `{}` 包起來。

### $$ 與 $n

在動作裡：

- `$$`：當前規則（左邊非終結符）的值
- `$1`、`$2`、`$3`...：規則右邊第 n 個符號的值

範例：
```bison
expr : expr '+' expr   { $$ = $1 + $3; }
     | NUMBER          { $$ = $1; }
     ;
```

`expr '+' expr` 裡：
- `$1` 是第一個 `expr` 的值
- `$2` 是 `'+'`（通常沒意義）
- `$3` 是第二個 `expr` 的值

`$$` 就是整個規則 reduce 完後要賦給父節點的值。

### %union 多欄位的使用

當你用了 `%union`，動作裡要自己指定欄位嗎？bison 幫你：

```bison
%union { int ival; char *sval; }
%token <ival> NUMBER
%token <sval> IDENT
%type <ival> expr

%%
expr : NUMBER             { $$ = $1; }        /* $1 是 ival，$$ 是 ival，沒問題 */
     | expr '+' expr      { $$ = $1 + $3; }
     ;
```

bison 會根據 `%type <ival> expr` 自動把 `$$` 當 `ival`。

如果你要跨欄位取值（例如在規則裡混用 IDENT 跟 NUMBER），需要手動：

```bison
$<sval>1    /* 明確取 sval 欄位 */
```

### 位置資訊

啟用 `%locations` 後，可以用 `@$`、`@n`：

```bison
expr : expr '+' expr {
    printf("add at line %d\n", @2.first_line);
}
```

`@n.first_line`、`@n.last_line`、`@n.first_column`、`@n.last_column` 都有。

## 使用者程式碼區

通常放 `main()` 跟 `yyerror()`：

```bison
%%
int main(void) {
    return yyparse();
}

void yyerror(const char *s) {
    fprintf(stderr, "syntax error: %s at line %d\n", s, yylineno);
}
```

`yyparse()` 是 bison 產生的主函式，回 0 表示成功、1 表示失敗。

## 完整範例：一個只解析不計算的表達式 parser

檔名 `parser.y`：

```bison
%{
#include <stdio.h>
#include <stdlib.h>
extern int yylex(void);
extern int yylineno;
void yyerror(const char *s);
%}

%union {
    int ival;
}

%token <ival> NUMBER
%left '+' '-'
%left '*' '/'

%type <ival> expr

%%

input : /* empty */
      | input line
      ;

line  : expr '\n'    { printf("= %d\n", $1); }
      | '\n'
      ;

expr  : expr '+' expr   { $$ = $1 + $3; }
      | expr '-' expr   { $$ = $1 - $3; }
      | expr '*' expr   { $$ = $1 * $3; }
      | expr '/' expr   { $$ = $1 / $3; }
      | '(' expr ')'    { $$ = $2; }
      | NUMBER          { $$ = $1; }
      ;

%%

int main(void) { return yyparse(); }

void yyerror(const char *s) {
    fprintf(stderr, "error: %s (line %d)\n", s, yylineno);
}
```

搭配的 `lexer.l`：

```lex
%option noyywrap yylineno
%{
#include "parser.tab.h"
#include <stdlib.h>
%}

%%
[0-9]+       { yylval.ival = atoi(yytext); return NUMBER; }
[ \t]+       { /* skip */ }
\n           { return '\n'; }
[+\-*/()]    { return yytext[0]; }
.            { return yytext[0]; }
%%
```

注意 `parser.tab.h` 是 bison 產生的標頭檔，內含 token 編號與 `yylval` 定義。

編譯流程：

```bash
bison -d parser.y         # 產 parser.tab.c 與 parser.tab.h
flex lexer.l              # 產 lex.yy.c
gcc lex.yy.c parser.tab.c -o calc
./calc
> 1 + 2 * 3
= 7
```

`-d` 叫 bison 額外產生 `.tab.h` 給 flex 用。

## 關於單字元 token

你可能發現我直接在文法裡用 `'+'` 而不是 `PLUS`。bison 對 ASCII 字元直接支援，lexer 只要 `return '+'` 就好。方便，但只對單字元有效。多字元運算子仍要走 `%token`：

```bison
%token EQ NE LE GE
```

```lex
"=="  { return EQ; }
"<="  { return LE; }
```

## 常用的宣告速查

```bison
%{ ... %}              /* C 程式碼 */
%union { ... }         /* 值的型別集合 */
%token NAME            /* 宣告 token */
%token <field> NAME    /* 帶型別 */
%type  <field> NAME    /* 非終結符的型別 */
%left TOK1 TOK2        /* 左結合 */
%right TOK             /* 右結合 */
%nonassoc TOK          /* 不結合 */
%start rule_name       /* 開始符號 */
%locations             /* 啟用位置追蹤 */
%define parse.error verbose  /* 詳細錯誤 */
```

## 動手練習

1. 把上面的「只解析計算器」跑起來。
2. 加入 `^` 運算子做次方（右結合，最高優先）。記得 `%right '^'` 要寫最下面。
3. 加入負號：`-3 + 2` 應該等於 `-1`。（提示：需要 `UMINUS` 優先級，見 Ch 9）
4. 讓它支援多行：輸入 `1+2\n3+4\n` 時分別印出 `= 3` 和 `= 7`。

## 自我檢核

- [ ] 我能寫出完整的 `.y` 檔，含 `%union`、`%token`、`%left`、規則、`main`、`yyerror`
- [ ] 我知道 `$$` 和 `$n` 在動作裡的意義
- [ ] 我能用 `bison -d` + `flex` + `gcc` 三步編譯
- [ ] 我知道單字元 token 和多字元 token 的處理差別

→ [Ch 9 解決文法衝突](./09-resolving-conflicts.md)
