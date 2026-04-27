# 練習 B — 表達式計算器

> 目標：把 Ch 6–10 的 yacc/bison 知識整合，寫一個支援變數、運算子、簡單控制流的直譯器。**這個版本直接在 yacc 動作裡求值，不建 AST**（那是 Part 4 的事）。

## 任務規格

做一個直譯式計算器 `calc`，支援：

### 資料型別

- 整數（預設型別）

### 語句

```
x = 10;                         // 變數指派
y = x + 5;
print y;                        // 印出表達式的值

if (x > 0) print x;             // 條件
if (x > 0) print x; else print 0;

while (x > 0) { print x; x = x - 1; }  // 迴圈
```

### 運算子（從高到低優先）

- 括號 `()`
- 一元 `-` `!`
- `*` `/` `%`
- `+` `-`
- `<` `>` `<=` `>=`
- `==` `!=`
- `&&`
- `||`

### 註解

- 行註解 `//`
- 區塊註解 `/* */`

## 預期互動

```
$ ./calc
> x = 1;
> y = 10;
> print x + y;
11
> while (y > 0) { print y; y = y - 1; }
10
9
8
...
1
> if (x == 1) print 42;
42
```

也應該能讀檔：

```
$ ./calc program.txt
```

## 實作結構

```
calc/
├── parser.y
├── lexer.l
├── symtab.c         # 簡單符號表
├── symtab.h
└── Makefile
```

## 設計決策

### 符號表

最簡單的做法是一個全域線性陣列：

```c
// symtab.h
void set_var(const char *name, int value);
int  get_var(const char *name);   // 未定義時回 0 並印警告

// symtab.c
#define MAX_VARS 256
struct { char *name; int value; } vars[MAX_VARS];
int nvars = 0;

void set_var(const char *name, int value) {
    for (int i = 0; i < nvars; i++)
        if (strcmp(vars[i].name, name) == 0) { vars[i].value = value; return; }
    if (nvars >= MAX_VARS) { fprintf(stderr, "too many vars\n"); return; }
    vars[nvars].name = strdup(name);
    vars[nvars].value = value;
    nvars++;
}

int get_var(const char *name) {
    for (int i = 0; i < nvars; i++)
        if (strcmp(vars[i].name, name) == 0) return vars[i].value;
    fprintf(stderr, "undefined variable: %s\n", name);
    return 0;
}
```

### while 的挑戰

在 yacc 動作裡直接求值時，**while 很棘手**，因為 yacc 在看到迴圈體 token 時就已經歸約了，沒辦法重新執行。

有兩種處理方式：

**方法 A（簡單但假）**：只執行一次迴圈體，用 goto 跳回。醜，不推薦。

**方法 B（正確）**：遇到 `while`、`if` 時先**蒐集**AST，之後走訪執行。

為了讓這個練習能完成，我們採**混合方案**：
- 表達式、指派、print：直接在 action 求值
- `if` / `while`：建立小型 AST 並呼叫 evaluator

這其實已經跨到 Part 4 的 AST 範疇。如果你覺得太跳，可以**先省略 while**，只做 if，等做完 Part 4 再回來補 while。

### 先省略 while 的版本

如果只支援 if，可以這樣作弊：把條件表達式的值存起來，後面的 stmt 決定是否執行。

但 yacc 的 action 是**永遠執行**的，這做不到。所以純 yacc action 版本的 if 其實也要 AST。

**結論**：這個練習需要**小型 AST**。先讀下面的 AST 快速版，或先做完 Ch 11–12 再回來。

## 快速 AST 快速版

```c
typedef enum {
    AST_NUM, AST_VAR,
    AST_ADD, AST_SUB, AST_MUL, AST_DIV, AST_MOD,
    AST_NEG, AST_NOT,
    AST_LT, AST_GT, AST_LE, AST_GE, AST_EQ, AST_NE,
    AST_AND, AST_OR,
    AST_ASSIGN,
    AST_IF, AST_WHILE, AST_PRINT,
    AST_BLOCK, AST_SEQ
} AstKind;

typedef struct Ast {
    AstKind kind;
    union {
        int ival;                               // AST_NUM
        char *sval;                             // AST_VAR
        struct { struct Ast *l, *r; } bin;      // 二元運算、AST_ASSIGN
        struct Ast *unary;                      // AST_NEG, NOT, PRINT
        struct { struct Ast *cond, *then, *els; } ifs;
        struct { struct Ast *cond, *body; } wh;
        struct { struct Ast *first, *second; } seq;
    };
} Ast;

Ast *mk_num(int v);
Ast *mk_var(char *name);
Ast *mk_bin(AstKind k, Ast *l, Ast *r);
Ast *mk_unary(AstKind k, Ast *x);
Ast *mk_if(Ast *c, Ast *t, Ast *e);
Ast *mk_while(Ast *c, Ast *b);
Ast *mk_seq(Ast *a, Ast *b);

int eval(Ast *node);       // 遞迴求值
void free_ast(Ast *node);
```

## 參考實作骨架

### parser.y（節選）

```bison
%{
#include <stdio.h>
#include <stdlib.h>
#include "ast.h"
extern int yylex(void);
extern int yylineno;
void yyerror(const char *s);
%}

%union {
    int ival;
    char *sval;
    Ast *ast;
}

%token <ival> NUMBER
%token <sval> IDENT
%token IF ELSE WHILE PRINT
%token EQ NE LE GE AND OR

%left OR
%left AND
%left EQ NE
%left '<' '>' LE GE
%left '+' '-'
%left '*' '/' '%'
%right UMINUS '!'
%nonassoc IFX
%nonassoc ELSE

%type <ast> expr stmt stmt_list

%%

program : stmt_list        { eval($1); free_ast($1); }
        ;

stmt_list : stmt                    { $$ = $1; }
          | stmt_list stmt          { $$ = mk_seq($1, $2); }
          ;

stmt : expr ';'                         { $$ = $1; }
     | PRINT expr ';'                   { $$ = mk_unary(AST_PRINT, $2); }
     | IDENT '=' expr ';'               { $$ = mk_bin(AST_ASSIGN, mk_var($1), $3); }
     | IF '(' expr ')' stmt      %prec IFX { $$ = mk_if($3, $5, NULL); }
     | IF '(' expr ')' stmt ELSE stmt   { $$ = mk_if($3, $5, $7); }
     | WHILE '(' expr ')' stmt          { $$ = mk_while($3, $5); }
     | '{' stmt_list '}'                { $$ = $2; }
     | error ';'                        { $$ = mk_num(0); yyerrok; }
     ;

expr : NUMBER                  { $$ = mk_num($1); }
     | IDENT                   { $$ = mk_var($1); }
     | expr '+' expr           { $$ = mk_bin(AST_ADD, $1, $3); }
     | expr '-' expr           { $$ = mk_bin(AST_SUB, $1, $3); }
     | expr '*' expr           { $$ = mk_bin(AST_MUL, $1, $3); }
     | expr '/' expr           { $$ = mk_bin(AST_DIV, $1, $3); }
     | expr '%' expr           { $$ = mk_bin(AST_MOD, $1, $3); }
     | expr '<' expr           { $$ = mk_bin(AST_LT, $1, $3); }
     | expr '>' expr           { $$ = mk_bin(AST_GT, $1, $3); }
     | expr LE expr            { $$ = mk_bin(AST_LE, $1, $3); }
     | expr GE expr            { $$ = mk_bin(AST_GE, $1, $3); }
     | expr EQ expr            { $$ = mk_bin(AST_EQ, $1, $3); }
     | expr NE expr            { $$ = mk_bin(AST_NE, $1, $3); }
     | expr AND expr           { $$ = mk_bin(AST_AND, $1, $3); }
     | expr OR  expr           { $$ = mk_bin(AST_OR, $1, $3); }
     | '-' expr %prec UMINUS   { $$ = mk_unary(AST_NEG, $2); }
     | '!' expr                { $$ = mk_unary(AST_NOT, $2); }
     | '(' expr ')'            { $$ = $2; }
     ;

%%
```

### eval 實作

```c
int eval(Ast *n) {
    if (!n) return 0;
    switch (n->kind) {
    case AST_NUM: return n->ival;
    case AST_VAR: return get_var(n->sval);
    case AST_ADD: return eval(n->bin.l) + eval(n->bin.r);
    case AST_SUB: return eval(n->bin.l) - eval(n->bin.r);
    case AST_MUL: return eval(n->bin.l) * eval(n->bin.r);
    case AST_DIV: {
        int r = eval(n->bin.r);
        if (r == 0) { fprintf(stderr, "div by 0\n"); return 0; }
        return eval(n->bin.l) / r;
    }
    case AST_MOD: return eval(n->bin.l) % eval(n->bin.r);
    case AST_NEG: return -eval(n->unary);
    case AST_NOT: return !eval(n->unary);
    case AST_LT:  return eval(n->bin.l) <  eval(n->bin.r);
    case AST_GT:  return eval(n->bin.l) >  eval(n->bin.r);
    case AST_LE:  return eval(n->bin.l) <= eval(n->bin.r);
    case AST_GE:  return eval(n->bin.l) >= eval(n->bin.r);
    case AST_EQ:  return eval(n->bin.l) == eval(n->bin.r);
    case AST_NE:  return eval(n->bin.l) != eval(n->bin.r);
    case AST_AND: return eval(n->bin.l) && eval(n->bin.r);
    case AST_OR:  return eval(n->bin.l) || eval(n->bin.r);
    case AST_ASSIGN: {
        int v = eval(n->bin.r);
        set_var(n->bin.l->sval, v);
        return v;
    }
    case AST_IF:
        if (eval(n->ifs.cond)) eval(n->ifs.then);
        else if (n->ifs.els) eval(n->ifs.els);
        return 0;
    case AST_WHILE:
        while (eval(n->wh.cond)) eval(n->wh.body);
        return 0;
    case AST_PRINT:
        printf("%d\n", eval(n->unary));
        return 0;
    case AST_SEQ:
        eval(n->seq.first);
        eval(n->seq.second);
        return 0;
    }
    return 0;
}
```

## 測試程式

```
// fib.calc
a = 0;
b = 1;
n = 10;
while (n > 0) {
    print a;
    tmp = a + b;
    a = b;
    b = tmp;
    n = n - 1;
}
```

執行：
```bash
./calc fib.calc
# 0 1 1 2 3 5 8 13 21 34
```

## 進階挑戰

做完基本版後，可以挑戰：

1. **for 迴圈**：`for (i = 0; i < 10; i = i + 1) { ... }`
2. **浮點與整數共存**：需要型別標籤
3. **字串型別**：加入 `print "hello";`
4. **函式定義與呼叫**：`fn add(a, b) { return a + b; }`
5. **短路求值**：`&&` / `||` 遇到結果已定時不求值右側（目前的 eval 實作已經做到了，因為 C 自己短路）

## 自我檢核

- [ ] 我能編譯執行，跑通 fib 範例
- [ ] 我能解釋為什麼 `while` 不能純靠 yacc action 求值
- [ ] 我能處理 dangling else
- [ ] 我能在語法錯誤時恢復並繼續解析

做完這個練習，Part 3 結束。恭喜你，你已經能寫一個完整的小型直譯器了。下一 Part 專注於如何**正式地**建 AST、管理符號表、做語義檢查。

→ [Ch 11 設計 AST](./11-ast-design.md)
