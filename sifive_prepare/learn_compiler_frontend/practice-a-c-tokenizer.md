# 練習 A — C 子集 tokenizer

> 目標：把 Ch 2–5 學到的東西拼起來，寫一個能處理 C 子集的 tokenizer，輸出格式化的 token 流。

## 任務規格

寫一個 flex 程式 `ctok.l`，讀取 C 風格的原始碼，輸出每一個 token：

```
<行號>:<欄位>  <TOKEN_KIND>  <lexeme>
```

### 要支援的 token

| 類別 | 內容 |
|---|---|
| 關鍵字 | `if`, `else`, `while`, `for`, `return`, `int`, `float`, `char`, `void`, `break`, `continue` |
| 識別字 | C 規則（字母/底線開頭） |
| 整數 | 十進位、十六進位（`0x`）、八進位（`0` 開頭） |
| 浮點數 | `3.14`、`.5`、`2.`、`1e10`、`3.14e-5` |
| 字串 | 支援 `\n` `\t` `\\` `\"` 跳脫，跨行報錯 |
| 字元 | `'a'`、`'\n'`（可選） |
| 運算子 | `+ - * / % = == != < > <= >= && \|\| ! & \| ^ ~ << >>` |
| 複合指派 | `+= -= *= /= %=` |
| 標點 | `( ) { } [ ] , ; :` |
| 註解 | `//` 與 `/* */`（支援多行，但不需支援巢狀） |

### 要處理的錯誤

- 未結束的字串（`"abc\n`）
- 未結束的註解（檔案結束前沒看到 `*/`）
- 未知字元（如 `@`）

錯誤要印到 stderr 並繼續解析（錯誤恢復）。

## 期望輸出範例

輸入：
```c
int main() {
    int x = 0x1F;
    // comment
    if (x >= 10) return x;
}
```

輸出（大致）：
```
1:1   INT_TYPE   int
1:5   IDENT      main
1:9   LPAREN     (
1:10  RPAREN     )
1:12  LBRACE     {
2:5   INT_TYPE   int
2:9   IDENT      x
2:11  ASSIGN     =
2:13  INT        0x1F
2:17  SEMI       ;
4:5   IF         if
4:8   LPAREN     (
4:9   IDENT      x
4:11  GE         >=
4:14  INT        10
4:16  RPAREN     )
4:18  RETURN     return
4:25  IDENT      x
4:26  SEMI       ;
5:1   RBRACE     }
```

## 實作步驟建議

### Step 1：骨架

```lex
%option noyywrap nodefault yylineno

%{
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int yycolumn = 1;

#define LOG(kind) \
    printf("%d:%d\t%s\t%s\n", yylineno, yycolumn, kind, yytext); \
    yycolumn += yyleng

/* 這個宏在每次匹配前被呼叫（不是後），用來先記當前位置 */
#define YY_USER_ACTION                        \
    do {                                       \
        /* 欄位號追蹤放這裡 */                 \
    } while (0);
%}

%%

%%

int main(int argc, char **argv) {
    if (argc > 1) yyin = fopen(argv[1], "r");
    yylex();
    return 0;
}
```

### Step 2：加關鍵字與識別字

注意順序：關鍵字在 ident 之前。

### Step 3：數字

十六進位、八進位、十進位、浮點，留意最長匹配。

### Step 4：運算子與標點

一條一條列出。複合指派 `+=` 記得在 `+` 之前。

### Step 5：註解

單行用 `"//".*`。多行用起始狀態 `COMMENT`，記得處理 `<<EOF>>`。

### Step 6：字串

起始狀態 `STRING`，用 `strbuf` 累積字元。

### Step 7：錯誤處理

catch-all 規則印錯誤訊息。

## 完整參考解答

**寫完再看！不要偷看**，否則學不到東西。

<details>
<summary>點開參考實作</summary>

```lex
%option noyywrap nodefault yylineno

DIGIT   [0-9]
HEX     [0-9a-fA-F]
OCT     [0-7]
ID      [a-zA-Z_][a-zA-Z_0-9]*

%x STRING COMMENT

%{
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int yycolumn = 1;

#define YY_USER_ACTION  \
    start_col = yycolumn; \
    yycolumn += yyleng;

int start_col = 1;
static char strbuf[4096];
static int strbuf_len;
static int string_start_line, string_start_col;

static void sb_init(void) { strbuf_len = 0; }
static void sb_add(char c) {
    if (strbuf_len < (int)sizeof(strbuf) - 1) strbuf[strbuf_len++] = c;
}
static void sb_end(void) { strbuf[strbuf_len] = 0; }

#define TOK(k) printf("%d:%d\t%s\t%s\n", yylineno, start_col, k, yytext)
%}

%%

"if"         { TOK("IF"); }
"else"       { TOK("ELSE"); }
"while"      { TOK("WHILE"); }
"for"        { TOK("FOR"); }
"return"     { TOK("RETURN"); }
"break"      { TOK("BREAK"); }
"continue"   { TOK("CONTINUE"); }
"int"        { TOK("INT_TYPE"); }
"float"      { TOK("FLOAT_TYPE"); }
"char"       { TOK("CHAR_TYPE"); }
"void"       { TOK("VOID_TYPE"); }

0[xX]{HEX}+                             { TOK("INT"); }
0{OCT}+                                  { TOK("INT"); }
{DIGIT}+                                 { TOK("INT"); }

{DIGIT}+"."{DIGIT}+([eE][+-]?{DIGIT}+)?  { TOK("FLOAT"); }
"."{DIGIT}+([eE][+-]?{DIGIT}+)?          { TOK("FLOAT"); }
{DIGIT}+"."([eE][+-]?{DIGIT}+)?          { TOK("FLOAT"); }
{DIGIT}+[eE][+-]?{DIGIT}+                { TOK("FLOAT"); }

{ID}         { TOK("IDENT"); }

"=="  { TOK("EQ"); }
"!="  { TOK("NE"); }
"<="  { TOK("LE"); }
">="  { TOK("GE"); }
"&&"  { TOK("AND"); }
"||"  { TOK("OR"); }
"<<"  { TOK("LSHIFT"); }
">>"  { TOK("RSHIFT"); }
"+="  { TOK("PLUSEQ"); }
"-="  { TOK("MINUSEQ"); }
"*="  { TOK("STAREQ"); }
"/="  { TOK("SLASHEQ"); }
"%="  { TOK("MODEQ"); }
"<"   { TOK("LT"); }
">"   { TOK("GT"); }
"="   { TOK("ASSIGN"); }
"+"   { TOK("PLUS"); }
"-"   { TOK("MINUS"); }
"*"   { TOK("STAR"); }
"/"   { TOK("SLASH"); }
"%"   { TOK("MOD"); }
"!"   { TOK("NOT"); }
"&"   { TOK("AMP"); }
"|"   { TOK("BAR"); }
"^"   { TOK("XOR"); }
"~"   { TOK("TILDE"); }
"("   { TOK("LPAREN"); }
")"   { TOK("RPAREN"); }
"{"   { TOK("LBRACE"); }
"}"   { TOK("RBRACE"); }
"["   { TOK("LBRACK"); }
"]"   { TOK("RBRACK"); }
","   { TOK("COMMA"); }
";"   { TOK("SEMI"); }
":"   { TOK("COLON"); }

"//".*       { }
"/*"         { BEGIN(COMMENT); }
<COMMENT>"*/" { BEGIN(INITIAL); }
<COMMENT>\n  { yycolumn = 1; }
<COMMENT>.   { }
<COMMENT><<EOF>> { fprintf(stderr, "error: unterminated comment\n"); yyterminate(); }

\"           { string_start_line = yylineno; string_start_col = start_col;
                sb_init(); BEGIN(STRING); }
<STRING>\"   { sb_end();
                BEGIN(INITIAL);
                printf("%d:%d\tSTR\t\"%s\"\n", string_start_line, string_start_col, strbuf); }
<STRING>\\n  { sb_add('\n'); }
<STRING>\\t  { sb_add('\t'); }
<STRING>\\\\ { sb_add('\\'); }
<STRING>\\\" { sb_add('"'); }
<STRING>\n   { fprintf(stderr, "line %d: unterminated string\n", yylineno);
                yycolumn = 1; BEGIN(INITIAL); }
<STRING>.    { sb_add(yytext[0]); }

\n           { yycolumn = 1; }
[ \t]+       { }
.            { fprintf(stderr, "%d:%d: unexpected char '%c'\n",
                        yylineno, start_col, yytext[0]); }

%%

int main(int argc, char **argv) {
    if (argc > 1) {
        yyin = fopen(argv[1], "r");
        if (!yyin) { perror(argv[1]); return 1; }
    }
    yylex();
    return 0;
}
```

編譯與測試：
```bash
flex ctok.l
gcc lex.yy.c -o ctok
./ctok sample.c
```

</details>

## 測試用例

建議你用這幾個檔案測：

**1. 正常程式**
```c
int fib(int n) {
    if (n < 2) return n;
    return fib(n - 1) + fib(n - 2);
}
```

**2. 各種數字**
```c
int a = 0;
int b = 42;
int c = 0xFF;
int d = 0755;
float e = 3.14;
float f = .5;
float g = 2e10;
float h = 1.5e-3;
```

**3. 字串與註解**
```c
// single line
/* multi
   line */
char *s = "hello\n\tworld";
```

**4. 故意出錯**
```c
int x = @;       // 未知字元
char *s = "abc   // 未結束字串
/* no end
```

## 自我檢核

完成後你應該能回答：

- [ ] 為什麼 `0x1F` 被切成一個 token 而不是 `0` + `x1F`？
- [ ] `0.5e-3` 裡的 `-` 為什麼沒被切成 `MINUS`？
- [ ] 如果我把浮點規則放在整數規則之前，會有什麼影響？
- [ ] 字串裡出現 `\\n` 應該存成「反斜線+n」還是「換行字元」？（答：如果要支援跳脫，是換行字元）

做完這個練習，你已經會寫 lexer 了。下一 Part 進入 yacc 的世界，學習如何把這些 token 組成有結構的語法樹。

→ [Ch 6 文法基礎與 BNF](./06-grammar-basics.md)
