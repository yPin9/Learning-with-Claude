# Ch 10 — lex + yacc 整合

> 目標：把 flex 跟 bison 接起來，建立一套可以重複使用的專案骨架。

## 整合的核心：四個接觸點

1. **token 編號**：bison 產生 `parser.tab.h`，裡面 `#define` 了所有 token 編號；lexer 要 include 它。
2. **值傳遞**：bison 用 `%union` 定義 `yylval`；lexer 在動作裡填 `yylval.xxx`。
3. **呼叫時機**：`yyparse()` 每次需要 token 就呼叫 `yylex()`。
4. **錯誤處理**：bison 遇到語法錯誤呼叫 `yyerror()`，你自己實作。

## 專案結構

```
project/
├── parser.y        # bison 輸入
├── lexer.l         # flex 輸入
├── Makefile
└── main.c          # (選配) 可以在這裡放 main
```

產生的中間檔：
```
parser.tab.c        # bison 產生的 parser
parser.tab.h        # token 編號、YYSTYPE 定義
lex.yy.c            # flex 產生的 lexer
```

## Makefile

```makefile
CC = gcc
CFLAGS = -Wall -g

all: calc

calc: lex.yy.c parser.tab.c
	$(CC) $(CFLAGS) lex.yy.c parser.tab.c -o calc

parser.tab.c parser.tab.h: parser.y
	bison -d parser.y

lex.yy.c: lexer.l parser.tab.h
	flex lexer.l

clean:
	rm -f calc lex.yy.c parser.tab.c parser.tab.h parser.output

.PHONY: all clean
```

注意 `lexer.l` 依賴 `parser.tab.h`，所以 flex 必須在 bison **之後**跑。

## 完整範例：整數計算器

### parser.y

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
%token NEWLINE

%left '+' '-'
%left '*' '/'
%right UMINUS

%type <ival> expr

%%

input : /* empty */
      | input line
      ;

line  : expr NEWLINE        { printf("= %d\n", $1); }
      | NEWLINE
      | error NEWLINE       { yyerrok; }    /* 錯誤恢復 */
      ;

expr  : expr '+' expr       { $$ = $1 + $3; }
      | expr '-' expr       { $$ = $1 - $3; }
      | expr '*' expr       { $$ = $1 * $3; }
      | expr '/' expr {
          if ($3 == 0) { yyerror("division by zero"); $$ = 0; }
          else $$ = $1 / $3;
      }
      | '-' expr %prec UMINUS  { $$ = -$2; }
      | '(' expr ')'        { $$ = $2; }
      | NUMBER              { $$ = $1; }
      ;

%%

int main(void) {
    printf("calc> ");
    return yyparse();
}

void yyerror(const char *s) {
    fprintf(stderr, "line %d: %s\n", yylineno, s);
}
```

### lexer.l

```lex
%option noyywrap yylineno

%{
#include "parser.tab.h"
#include <stdlib.h>
%}

%%

[0-9]+        { yylval.ival = atoi(yytext); return NUMBER; }
[ \t]+        { /* skip */ }
\n            { return NEWLINE; }
[+\-*/()]     { return yytext[0]; }
.             { fprintf(stderr, "unexpected char: %c\n", yytext[0]); }

%%
```

### 編譯與測試

```bash
make
./calc
calc> 1 + 2 * 3
= 7
calc> (1 + 2) * 3
= 9
calc> -5 + 3
= -2
calc> 10 / 0
line 1: division by zero
= 0
calc> 1 + + 2
line 1: syntax error
```

## 通訊細節

### token 編號

bison 產生的 `parser.tab.h` 長得像：

```c
enum yytokentype {
    NUMBER = 258,
    NEWLINE = 259
};

typedef union {
    int ival;
} YYSTYPE;

extern YYSTYPE yylval;
```

lexer include 這個就能使用 `NUMBER`、`yylval` 等符號。

### yylval 的賦值

在 lexer 的動作裡：

```c
yylval.ival = atoi(yytext);
return NUMBER;
```

bison 的 `NUMBER` 規則用 `$1` 取值時，拿到的就是這個 `ival`。

### 單字元 token

`'+' '-' '*' '/' '(' ')'` 這種直接 `return yytext[0]`，bison 端直接用字元字面量即可。這是為什麼單字元 token **不需要** `%token` 宣告。

## 帶型別的範例

假設要同時支援整數和浮點：

```bison
%union {
    int ival;
    double fval;
}

%token <ival> INT_LIT
%token <fval> FLOAT_LIT

%type <fval> expr

%%

expr : INT_LIT         { $$ = (double)$1; }
     | FLOAT_LIT       { $$ = $1; }
     | expr '+' expr   { $$ = $1 + $3; }
     ;
```

lexer：

```lex
[0-9]+           { yylval.ival = atoi(yytext); return INT_LIT; }
[0-9]+\.[0-9]+   { yylval.fval = atof(yytext); return FLOAT_LIT; }
```

## 加入識別字與簡單變數

```bison
%union {
    int ival;
    char *sval;
}

%token <ival> NUMBER
%token <sval> IDENT
%token ASSIGN
%token NEWLINE

%type <ival> expr

%%

line : IDENT ASSIGN expr NEWLINE  {
         set_var($1, $3);
         free($1);
     }
     | expr NEWLINE               { printf("= %d\n", $1); }
     ;

expr : IDENT     { $$ = get_var($1); free($1); }
     | NUMBER    { $$ = $1; }
     | expr '+' expr { $$ = $1 + $3; }
     ;
```

lexer：

```lex
[a-zA-Z_][a-zA-Z_0-9]*  { yylval.sval = strdup(yytext); return IDENT; }
"="                      { return ASSIGN; }
[0-9]+                   { yylval.ival = atoi(yytext); return NUMBER; }
```

`set_var` / `get_var` 你自己實作，簡單 hash table 或線性陣列都可。Ch 13 會詳細講符號表。

## 記憶體管理：誰負責 free？

這是整合時的常見痛點。慣例：

- lexer `strdup` 字串，bison action 用完後 `free`
- AST 節點 malloc，交給上層，最後統一釋放
- 遇到錯誤時，bison 可能會「丟棄」某些值（mid-rule abort），這會洩漏記憶體

bison 提供 `%destructor` 宣告，告訴它某型別在丟棄時該怎麼釋放：

```bison
%destructor { free($$); } <sval>
```

在錯誤恢復時這能救你一命。

## 除錯技巧

### 1. 開啟 trace

```bison
%{
int yydebug = 1;     /* 全域開關 */
%}
```

或編譯時 `-DYYDEBUG=1`。bison 會印出每一步 shift/reduce。

### 2. 看 .output

`bison -v` 產生 `parser.output`，逐一狀態與衝突都在裡面。

### 3. verbose 錯誤

```bison
%define parse.error verbose
```

原本只會說 `syntax error`，開了之後會說「expected 'IDENT' before ';'」這類具體訊息。

### 4. 位置追蹤

```bison
%locations
```

之後可以用 `@1.first_line`、`@$.last_column` 等變數，對錯誤訊息很有用。

## 常見整合錯誤

1. **lexer 沒 include `parser.tab.h`**：lexer 回傳 token 的數字不對，parser 完全崩。
2. **忘記跑 `bison -d`**：沒有 `.tab.h`，flex 編不過。
3. **`%union` 有修改但沒重跑 bison**：lex 的 `yylval` 欄位對不上，編譯出錯。
4. **token 在 `%token` 漏宣告**：bison 報 `unrecognized token`。
5. **`yyerror` 沒實作**：連結錯誤。

## 動手練習

1. 把上面的計算器跑起來，測試正常輸入與語法錯誤恢復。
2. 加入 `^` 次方、比較運算子 `<` `>` `==`（回傳 0/1）。
3. 支援變數指派：`x = 10; y = x + 5; y` 能正確印出 15。
4. 加入註解：`// ...` 行尾註解、`/* */` 區塊註解。
5. 開啟 `yydebug = 1`，觀察輸入 `1 + 2 * 3` 時 bison 的每一步 shift/reduce。

## 自我檢核

- [ ] 我能寫出完整可跑的 flex + bison 專案（含 Makefile）
- [ ] 我知道 `bison -d` 為何必要
- [ ] 我能在 lexer 用 `yylval.ival = ...` 傳整數給 parser
- [ ] 我知道 `%destructor` 的作用
- [ ] 我能用 `%locations` + `parse.error verbose` 改善錯誤訊息

下一章是 Part 3 的收尾練習：把計算器擴成支援變數和控制流的語言。

→ [練習 B：表達式計算器](./practice-b-calculator.md)
