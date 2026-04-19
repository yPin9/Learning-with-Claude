# Ch 12 — 在動作中建構 AST

> 目標：把 yacc action 從「直接求值」改為「建 AST 節點」，為語義分析和 IR 產生打好基礎。

## 核心模式

每條規則的 action 做兩件事：
1. 呼叫 `mk_xxx` 建構節點
2. 把新節點賦給 `$$`

```bison
expr : NUMBER              { $$ = mk_int($1, @1.first_line); }
     | IDENT               { $$ = mk_var($1, @1.first_line); }
     | expr '+' expr       { $$ = mk_bin(OP_ADD, $1, $3, @2.first_line); }
     ;
```

位置資訊要開 `%locations` 才能用。

## %union 放 AST 指標

```bison
%union {
    int ival;
    double fval;
    char *sval;
    Ast *ast;
    AstList *list;
}

%token <ival> INT_LIT
%token <sval> IDENT
%token IF ELSE WHILE RETURN
%token <sval> STR_LIT

%type <ast>  expr stmt program
%type <list> stmt_list args
```

## 串接多個語句

yacc 文法裡最典型的模式是 `list : list item | item`，在 action 裡要把新 item 加入 list：

```bison
stmt_list : stmt                    { $$ = list_cons($1, NULL); }
          | stmt_list stmt          { $$ = list_append($1, $2); }
          ;
```

或者反過來用 cons + reverse 也行，但 append 可讀性好。

## 處理可選項

```bison
if_stmt : IF '(' expr ')' stmt                   { $$ = mk_if($3, $5, NULL, @1.first_line); }
        | IF '(' expr ')' stmt ELSE stmt         { $$ = mk_if($3, $5, $7, @1.first_line); }
        ;
```

分兩條規則，各自傳不同 ast 指標。

## 變數宣告的微妙之處

```bison
decl : type IDENT ';'                 { $$ = mk_decl($1, $2, NULL, @1.first_line); }
     | type IDENT '=' expr ';'        { $$ = mk_decl($1, $2, $4, @1.first_line); }
     ;

type : INT_TYPE      { $$ = TY_INT; }
     | FLOAT_TYPE    { $$ = TY_FLOAT; }
     ;
```

注意 `$$ = TY_INT` 這邊 `$$` 的欄位是 `int`（TY_INT 是 enum），所以 `%type` 要宣告成 `<ival>`：

```bison
%type <ival> type
```

## 函式定義

```bison
fun_def : type IDENT '(' param_list ')' block
              { $$ = mk_fun($1, $2, $4, $6, @1.first_line); }
        ;

param_list : /* empty */          { $$ = NULL; }
           | param                { $$ = list_cons($1, NULL); }
           | param_list ',' param { $$ = list_append($1, $3); }
           ;

param : type IDENT   { $$ = mk_decl($1, $2, NULL, @1.first_line); }
      ;

block : '{' stmt_list '}'    { $$ = mk_block($2, @1.first_line); }
      | '{' '}'              { $$ = mk_block(NULL, @1.first_line); }
      ;
```

## 函式呼叫

```bison
call : IDENT '(' arg_list ')'   { $$ = mk_call($1, $3, @1.first_line); }
     ;

arg_list : /* empty */            { $$ = NULL; }
         | expr                   { $$ = list_cons($1, NULL); }
         | arg_list ',' expr      { $$ = list_append($1, $3); }
         ;
```

## Mid-rule Actions

yacc 允許在規則中間插入 action，但要小心：

```bison
/* 不推薦，但你會看到 */
scope_block : '{' { push_scope(); } stmt_list '}' { pop_scope(); $$ = mk_block($3, ...); }
            ;
```

問題：mid-rule action 會被當成**匿名非終結符**，可能產生意料外的衝突。建議用 **顯式規則**：

```bison
scope_start : '{'           { push_scope(); } ;
scope_end   : '}'           { pop_scope(); } ;
scope_block : scope_start stmt_list scope_end  { $$ = mk_block($2, ...); }
            ;
```

## 整合：一個小型語言的完整 parser.y

```bison
%{
#include <stdio.h>
#include <stdlib.h>
#include "ast.h"
extern int yylex(void);
extern int yylineno;
void yyerror(const char *s);
Ast *root;
%}

%union {
    int ival;
    double fval;
    char *sval;
    Ast *ast;
    AstList *list;
}

%locations

%token <ival> INT_LIT
%token <fval> FLOAT_LIT
%token <sval> STR_LIT IDENT
%token INT_TYPE FLOAT_TYPE VOID_TYPE STR_TYPE
%token IF ELSE WHILE RETURN
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

%type <ival> type
%type <ast>  expr stmt block fun_def decl call
%type <list> stmt_list args params param_opt program

%%

program : /* empty */        { root = mk_program(NULL); }
        | program_items      { root = mk_program($1); }
        ;

program_items : top_item                 { $$ = list_cons($1, NULL); }
              | program_items top_item   { $$ = list_append($1, $2); }
              ;

top_item : fun_def       { $$ = $1; }
         | decl          { $$ = $1; }
         ;

fun_def : type IDENT '(' param_opt ')' block
              { $$ = mk_fun($1, $2, $4, $6, @1.first_line); }
        ;

param_opt : /* empty */    { $$ = NULL; }
          | params         { $$ = $1; }
          ;

params : type IDENT                    { $$ = list_cons(mk_decl($1, $2, NULL, @1.first_line), NULL); }
       | params ',' type IDENT         { $$ = list_append($1, mk_decl($3, $4, NULL, @3.first_line)); }
       ;

type : INT_TYPE     { $$ = TY_INT; }
     | FLOAT_TYPE   { $$ = TY_FLOAT; }
     | STR_TYPE     { $$ = TY_STR; }
     | VOID_TYPE    { $$ = TY_VOID; }
     ;

block : '{' stmt_list '}'   { $$ = mk_block($2, @1.first_line); }
      | '{' '}'             { $$ = mk_block(NULL, @1.first_line); }
      ;

stmt_list : stmt             { $$ = list_cons($1, NULL); }
          | stmt_list stmt   { $$ = list_append($1, $2); }
          ;

stmt : expr ';'                                     { $$ = mk_expr_stmt($1, @1.first_line); }
     | decl                                         { $$ = $1; }
     | IDENT '=' expr ';'                           { $$ = mk_assign($1, $3, @1.first_line); }
     | IF '(' expr ')' stmt      %prec IFX          { $$ = mk_if($3, $5, NULL, @1.first_line); }
     | IF '(' expr ')' stmt ELSE stmt               { $$ = mk_if($3, $5, $7, @1.first_line); }
     | WHILE '(' expr ')' stmt                      { $$ = mk_while($3, $5, @1.first_line); }
     | RETURN ';'                                   { $$ = mk_return(NULL, @1.first_line); }
     | RETURN expr ';'                              { $$ = mk_return($2, @1.first_line); }
     | block                                        { $$ = $1; }
     | error ';'                                    { $$ = mk_int(0, yylineno); yyerrok; }
     ;

decl : type IDENT ';'                  { $$ = mk_decl($1, $2, NULL, @1.first_line); }
     | type IDENT '=' expr ';'         { $$ = mk_decl($1, $2, $4, @1.first_line); }
     ;

expr : INT_LIT                { $$ = mk_int($1, @1.first_line); }
     | FLOAT_LIT              { $$ = mk_float($1, @1.first_line); }
     | STR_LIT                { $$ = mk_str($1, @1.first_line); }
     | IDENT                  { $$ = mk_var($1, @1.first_line); }
     | call                   { $$ = $1; }
     | expr '+' expr          { $$ = mk_bin(OP_ADD, $1, $3, @2.first_line); }
     | expr '-' expr          { $$ = mk_bin(OP_SUB, $1, $3, @2.first_line); }
     | expr '*' expr          { $$ = mk_bin(OP_MUL, $1, $3, @2.first_line); }
     | expr '/' expr          { $$ = mk_bin(OP_DIV, $1, $3, @2.first_line); }
     | expr '%' expr          { $$ = mk_bin(OP_MOD, $1, $3, @2.first_line); }
     | expr '<' expr          { $$ = mk_bin(OP_LT, $1, $3, @2.first_line); }
     | expr '>' expr          { $$ = mk_bin(OP_GT, $1, $3, @2.first_line); }
     | expr LE expr           { $$ = mk_bin(OP_LE, $1, $3, @2.first_line); }
     | expr GE expr           { $$ = mk_bin(OP_GE, $1, $3, @2.first_line); }
     | expr EQ expr           { $$ = mk_bin(OP_EQ, $1, $3, @2.first_line); }
     | expr NE expr           { $$ = mk_bin(OP_NE, $1, $3, @2.first_line); }
     | expr AND expr          { $$ = mk_bin(OP_AND, $1, $3, @2.first_line); }
     | expr OR expr           { $$ = mk_bin(OP_OR, $1, $3, @2.first_line); }
     | '-' expr %prec UMINUS  { $$ = mk_unary(OP_NEG, $2, @1.first_line); }
     | '!' expr               { $$ = mk_unary(OP_NOT, $2, @1.first_line); }
     | '(' expr ')'           { $$ = $2; }
     ;

call : IDENT '(' args ')'     { $$ = mk_call($1, $3, @1.first_line); }
     ;

args : /* empty */          { $$ = NULL; }
     | expr                 { $$ = list_cons($1, NULL); }
     | args ',' expr        { $$ = list_append($1, $3); }
     ;

%%

int main(int argc, char **argv) {
    if (argc > 1) {
        extern FILE *yyin;
        yyin = fopen(argv[1], "r");
        if (!yyin) { perror(argv[1]); return 1; }
    }
    if (yyparse() == 0 && root) {
        print_ast(root, 0);
    }
    return 0;
}

void yyerror(const char *s) {
    fprintf(stderr, "line %d: %s\n", yylineno, s);
}
```

## 除錯流程

1. **先驗證 parse 成功**：讓 `main` 只印 "parse ok"
2. **再驗證 AST 結構**：呼叫 `print_ast(root)`，比對輸入
3. **用小範例**：從 `int x;` 開始，逐步加功能
4. **出錯就看 `parser.output`**：bison 的狀態機是你的朋友

## 典型錯誤

### 錯誤 1：`$1` 型別不對

```
warning: incompatible pointer types assigning to 'int'
```

通常是 `%type <欄位>` 宣告錯了，或 `$1` 指向不同 union 欄位。

### 錯誤 2：忘記 `mk_xxx` 回傳指標

```c
Ast *mk_int(int v, int line);   /* 對 */
int  mk_int(int v, int line);   /* 錯，$$ = $1 無效 */
```

### 錯誤 3：記憶體洩漏

每個 `strdup` 的 string 最後要追蹤誰 free。如果 action 裡不用 `strdup` 直接用 lexer 送來的，下個 token 一到就會被覆寫。

這也是為什麼我們在 lexer 裡 `strdup(yytext)`，在 AST free 時再 free。

### 錯誤 4：forgot %locations

如果沒開 `%locations`，用 `@1.first_line` 會 compile error。

## 動手練習

1. 把上面的 parser.y 實作完整，配合 Ch 11 的 `ast.h`。
2. 寫 `print_ast` 函式，能印出整棵樹。
3. 輸入一個範例程式：
   ```c
   int fib(int n) {
       if (n < 2) return n;
       return fib(n-1) + fib(n-2);
   }
   ```
   驗證 AST 印出符合預期。
4. 加入 `for` 迴圈支援：`for (init; cond; update) body`

## 自我檢核

- [ ] 我能在 yacc action 裡建構 AST 節點
- [ ] 我能用 `AstList` 表示可變長度的子節點
- [ ] 我能用 `@1.first_line` 取位置資訊
- [ ] 我能避免 mid-rule action 造成的衝突

→ [Ch 13 符號表與作用域](./13-symbol-table.md)
