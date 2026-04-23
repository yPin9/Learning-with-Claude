# Ch 5 — flex 與 C 互動

> 目標：搞懂 lexer 和 C 程式之間的介面，特別是在還沒接 yacc 之前，自己掌控 token 的傳遞。

## flex 公開的全域變數/函式

以下都在 `lex.yy.c` 裡產生，你的 C code 可以用：

| 名稱 | 型別 | 用途 |
|---|---|---|
| `yytext` | `char *` | 當前匹配的字串 |
| `yyleng` | `int` | 當前匹配的長度 |
| `yylineno` | `int` | 當前行號（需 `%option yylineno`） |
| `yyin` | `FILE *` | 輸入來源，預設 stdin |
| `yyout` | `FILE *` | 輸出目的，預設 stdout |
| `yylex()` | `int (*)()` | 主函式，呼叫一次處理一段輸入 |
| `yyrestart(FILE*)` | `void` | 重設輸入來源並清狀態 |
| `yyterminate()` | macro | 立刻結束 `yylex()` 回傳 0 |

搭配 yacc 時還會多一個 `yylval`，下面會講。

## yytext 的壽命

**這是新手最常爆的地雷。**

`yytext` 指向 flex 內部的緩衝區，**下一次** `yylex()` 匹配時會被覆寫。如果你想把某個識別字的名字存到符號表，不能直接：

```c
symbol->name = yytext;   // 錯！下次匹配就爛掉
```

必須複製：

```c
symbol->name = strdup(yytext);   // 對，但記得 free
```

或自己 `malloc(yyleng + 1)` 再 `memcpy`。

## yylval：lexer 傳值給 parser

當 lexer 和 parser 整合時，光回傳 token 種類不夠，還要把「值」傳過去。例如遇到整數 `42`，parser 需要拿到 42 這個數字。

這靠一個全域變數 `yylval` 實現：

```c
/* 在 yacc 裡宣告（Ch 10 細講） */
%union {
    int ival;
    char *sval;
}
%token <ival> INT_LIT
%token <sval> IDENT
```

bison 會產生對應的 `yylval` 變數。lexer 寫：

```lex
[0-9]+     { yylval.ival = atoi(yytext); return INT_LIT; }
{ID}       { yylval.sval = strdup(yytext); return IDENT; }
```

在**還沒接 yacc** 的階段，你可以自己宣告：

```c
typedef union { int ival; char *sval; double fval; } YYVAL;
extern YYVAL yylval;
```

但實務上很少這樣，通常就直接進 yacc 整合。

## 起始狀態（Start Conditions）

前一章用過了，這裡系統整理。

### 宣告

```lex
%s INCLUSIVE_STATE    /* 包含：原規則仍生效 */
%x EXCLUSIVE_STATE    /* 排他：只有 <STATE> 規則生效 */
```

大部分情況用 `%x`，更安全。

### 切換

```c
BEGIN(STATE_NAME);     /* 切到指定狀態 */
BEGIN(0);              /* 或 INITIAL，回到預設狀態 */
```

### 使用

```lex
<STATE1>pattern    { action }
<STATE1,STATE2>p   { action }    /* 多個狀態 */
<*>p               { action }    /* 所有狀態 */
```

### 典型應用

1. **字串字面量**：遇到 `"` 進入 STRING，遇到第二個 `"` 出來
2. **多行註解**：遇到 `/*` 進入 COMMENT，遇到 `*/` 出來
3. **預處理指令**：遇到 `#` 進入 PREPROCESSOR，遇到 `\n` 出來
4. **heredoc**（如 bash）：遇到 `<<EOF` 進入 HEREDOC 模式

### 小陷阱：狀態是全域的

flex 的狀態是共享的單一變數，所以**不要**用遞迴的方式進出狀態（例如巢狀註解）。要支援巢狀註解得自己維護 depth counter：

```lex
%{
int comment_depth = 0;
%}

%x COMMENT

%%
"/*"             { comment_depth = 1; BEGIN(COMMENT); }
<COMMENT>"/*"    { comment_depth++; }
<COMMENT>"*/"    { if (--comment_depth == 0) BEGIN(INITIAL); }
<COMMENT>\n      { }
<COMMENT>.       { }
```

## yymore() 與 yyless()

### yymore()

「下一次匹配接在當前 `yytext` 後面，不清空」。常用於：

```lex
\"[^\"\n]*\"    { ... }
\"[^\"\n]*\\\n  { yymore(); }   /* 字串跨行，繼續吃 */
```

### yyless(n)

「吐回 n 個之後的字元」。常用於需要 lookahead 但不想吞掉的情況：

```lex
"return"/[ \t\n]    { /* 確認後面是空白才算 return 關鍵字 */ }
```

或用更明確的 trailing context：`"return"/[ \t\n]`（斜線後是 lookahead，不計入匹配）。

## 多次呼叫 yylex 與切換輸入

### 讀多個檔案

```c
FILE *files[] = {fopen("a.c", "r"), fopen("b.c", "r")};
for (int i = 0; i < 2; i++) {
    yyrestart(files[i]);
    yylex();
    fclose(files[i]);
}
```

### 從字串讀

flex 提供 `yy_scan_string`：

```c
const char *src = "int x = 10;";
YY_BUFFER_STATE buf = yy_scan_string(src);
yylex();
yy_delete_buffer(buf);
```

做 REPL 或從記憶體解析時很好用。

## 回傳 token：一個完整可跑的例子

在沒接 yacc 的情況下，模擬 parser 的呼叫方式：

```lex
%option noyywrap yylineno

%{
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum { T_EOF = 0, T_INT = 256, T_IDENT, T_PLUS, T_MINUS };

typedef union { int ival; char *sval; } Value;
Value current_val;
%}

%%
[0-9]+      { current_val.ival = atoi(yytext); return T_INT; }
[a-zA-Z_]+  { current_val.sval = strdup(yytext); return T_IDENT; }
"+"         { return T_PLUS; }
"-"         { return T_MINUS; }
[ \t\n]+    { /* skip */ }
.           { fprintf(stderr, "bad char %c\n", yytext[0]); }
%%

int main(void) {
    int tok;
    while ((tok = yylex()) != T_EOF) {
        switch (tok) {
            case T_INT:   printf("INT(%d)\n", current_val.ival); break;
            case T_IDENT: printf("IDENT(%s)\n", current_val.sval);
                          free(current_val.sval); break;
            case T_PLUS:  printf("PLUS\n"); break;
            case T_MINUS: printf("MINUS\n"); break;
        }
    }
    return 0;
}
```

把 token 的回傳值從 256 開始，避開 ASCII 字元範圍，這是 yacc 慣例。

執行：
```bash
flex tok.l && gcc lex.yy.c -o tok
echo "abc + 123 - xyz" | ./tok
```

## 錯誤處理與定位

有 `yylineno` 是好的，但通常你還想要「欄位」（column）資訊：

```lex
%{
int yycolumn = 1;
#define YY_USER_ACTION                    \
    yycolumn += yyleng;                   \
    if (strchr(yytext, '\n')) yycolumn = 1;
%}
```

`YY_USER_ACTION` 是每次匹配後 flex 都會呼叫的 hook，用它維護欄位號很方便。

## 動手練習

1. 把上面那支「沒接 yacc 的 token 迴圈」跑起來。
2. 加入浮點數，讓 `main` 能印出 `FLOAT(3.14)`。
3. 加入行列號追蹤：輸入 `abc\n  + 123`，在遇到 `+` 時輸出 `line 2, col 3, PLUS`。
4. 用 `yy_scan_string` 寫一個小 REPL，能把使用者輸入的每一行 tokenize 後印出。

## 自我檢核

- [ ] 我知道 `yytext` 的壽命限制，複製時用 `strdup`
- [ ] 我能用 `%x` 寫排他起始狀態
- [ ] 我知道 `yylval` 是 lexer 給 parser 傳值的管道
- [ ] 我能用 `YY_USER_ACTION` 自己加欄位號追蹤

下一章是實戰練習：寫一個 C 子集的完整 tokenizer。

→ [練習 A：C 子集 tokenizer](./practice-a-c-tokenizer.md)
