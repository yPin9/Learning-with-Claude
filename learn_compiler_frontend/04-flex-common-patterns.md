# Ch 4 — 常見詞法模式

> 目標：收集真實語言常見的詞法模式，遇到時有參考答案可抄。

## 識別字 (Identifier)

C 風格：字母或底線開頭，之後可接字母、數字、底線。

```lex
[a-zA-Z_][a-zA-Z_0-9]*    { return IDENT; }
```

支援 Unicode 識別字（例如 Python 3）要複雜一點，這邊先不展開。

## 整數

### 十進位

```lex
[0-9]+     { yylval.ival = atoi(yytext); return INT; }
```

### 十六進位

```lex
0[xX][0-9a-fA-F]+   { yylval.ival = strtol(yytext, NULL, 16); return INT; }
```

### 八進位

```lex
0[0-7]+             { yylval.ival = strtol(yytext, NULL, 8); return INT; }
```

### 整合起來

注意順序：十六進位要在十進位之前（因為 `0x10` 會被「最長匹配」吃光，但萬一你寫反了會出問題）。

```lex
0[xX][0-9a-fA-F]+   { yylval.ival = strtol(yytext, NULL, 16); return INT; }
0[0-7]*             { yylval.ival = strtol(yytext, NULL, 8);  return INT; }
[1-9][0-9]*         { yylval.ival = atoi(yytext);             return INT; }
```

## 浮點數

簡化版（C 規格完整的太囉嗦）：

```lex
[0-9]+"."[0-9]+              { yylval.fval = atof(yytext); return FLOAT; }
[0-9]+"."[0-9]+[eE][+-]?[0-9]+  { yylval.fval = atof(yytext); return FLOAT; }
[0-9]+[eE][+-]?[0-9]+        { yylval.fval = atof(yytext); return FLOAT; }
```

實務上會再合併成一條：

```lex
([0-9]+"."[0-9]+|[0-9]+"."|"."[0-9]+)([eE][+-]?[0-9]+)?  { ... }
```

但拆開可讀性比較好，也利於錯誤訊息。

## 關鍵字

方法一：**一條規則一個關鍵字**（推薦給小語言）

```lex
"if"       { return IF; }
"else"     { return ELSE; }
"while"    { return WHILE; }
"return"   { return RETURN; }
"int"      { return TYPE_INT; }
"float"    { return TYPE_FLOAT; }
```

方法二：**查表法**（關鍵字很多時）

```lex
%{
struct keyword { const char *name; int token; };
static struct keyword kw[] = {
    {"if", IF}, {"else", ELSE}, {"while", WHILE},
    {"return", RETURN}, {NULL, 0}
};

static int lookup_keyword(const char *s) {
    for (struct keyword *p = kw; p->name; p++)
        if (strcmp(s, p->name) == 0) return p->token;
    return IDENT;
}
%}

%%
[a-zA-Z_][a-zA-Z_0-9]*    { return lookup_keyword(yytext); }
```

方法二的好處是規則更少，新增關鍵字只改表。

## 字串字面量

最常見的坑。先看錯誤寫法：

```lex
\"[^\"]*\"    { /* 不夠嚴謹！ */ }
```

這寫法有三個問題：
1. 不支援跳脫 `\"`
2. 允許字串內含換行
3. `yytext` 兩端有引號，要自己剝掉

### 正確做法：用起始狀態

```lex
%x STRING

%%
\"                  { BEGIN(STRING); string_buf_init(); }
<STRING>\"          { BEGIN(INITIAL); yylval.sval = string_buf_get(); return STR; }
<STRING>\\n         { string_buf_append('\n'); }
<STRING>\\t         { string_buf_append('\t'); }
<STRING>\\\"        { string_buf_append('"'); }
<STRING>\\\\        { string_buf_append('\\'); }
<STRING>\n          { error("unterminated string"); }
<STRING>.           { string_buf_append(yytext[0]); }
```

`%x STRING` 宣告一個**排他** (exclusive) 起始狀態，在這個狀態只有 `<STRING>` 前綴的規則生效。`%s` 是**包含** (inclusive) 起始狀態，原本的規則也會生效。99% 情況你要的是 `%x`。

## 註解

### 單行註解（`//`）

```lex
"//".*    { /* 忽略整行註解 */ }
```

`.` 不含換行，所以這條會一路吃到行尾。

### 多行註解（`/* */`）

跟字串一樣，用起始狀態最乾淨：

```lex
%x COMMENT

%%
"/*"             { BEGIN(COMMENT); }
<COMMENT>"*/"    { BEGIN(INITIAL); }
<COMMENT>\n      { /* 讓 yylineno 自動更新 */ }
<COMMENT>.       { /* 忽略 */ }
<COMMENT><<EOF>> { error("unterminated comment"); yyterminate(); }
```

`<<EOF>>` 是特殊 pattern，代表「在這個狀態遇到檔案結束」。

### 錯誤做法警告

很多人第一次會寫：

```lex
"/*".*"*/"   { /* 忽略 */ }
```

這**完全錯誤**，原因：
1. `.` 不含換行 → 跨行註解不行
2. 就算用 `[\s\S]` 也會貪婪匹配到「第一個 `/*`」和「最後一個 `*/`」之間，把中間所有東西吃光

一定用起始狀態做。

## 空白

```lex
[ \t]+      { /* 忽略 */ }
\n          { /* 忽略，yylineno 自動加 */ }
```

如果語言對換行敏感（例如 Python），就要把 `\n` 當成 token 回傳。

## 運算子

一個一個列，flex 的最長匹配會幫你搞定多字元運算子：

```lex
"=="     { return EQ; }
"!="     { return NE; }
"<="     { return LE; }
">="     { return GE; }
"<"      { return LT; }
">"      { return GT; }
"&&"     { return AND; }
"||"     { return OR; }
"="      { return ASSIGN; }
"+"      { return PLUS; }
"-"      { return MINUS; }
"*"      { return STAR; }
"/"      { return SLASH; }
```

長的在前比較保險，雖然最長匹配會處理，但明確一點。

## 標點

```lex
"("      { return LPAREN; }
")"      { return RPAREN; }
"{"      { return LBRACE; }
"}"      { return RBRACE; }
","      { return COMMA; }
";"      { return SEMI; }
```

## 錯誤恢復：catch-all

```lex
.        { fprintf(stderr, "line %d: unexpected char '%c'\n", yylineno, yytext[0]); }
```

有了 `%option nodefault`，漏掉任何字元 flex 會報警，但有這條 catch-all 可以讓 lexer 繼續跑，一次蒐集多個錯誤。

## 完整拼裝範例

把以上拼起來，一個能當「MiniC」lexer 的骨架：

```lex
%option noyywrap nodefault yylineno

DIGIT  [0-9]
ID     [a-zA-Z_][a-zA-Z_0-9]*

%x STRING COMMENT

%{
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static char strbuf[4096];
static int strbuf_len;

void strbuf_init(void) { strbuf_len = 0; }
void strbuf_append(char c) {
    if (strbuf_len < sizeof(strbuf) - 1) strbuf[strbuf_len++] = c;
}
char *strbuf_get(void) { strbuf[strbuf_len] = 0; return strdup(strbuf); }
%}

%%

"if"       { printf("IF\n"); }
"else"     { printf("ELSE\n"); }
"while"    { printf("WHILE\n"); }
"return"   { printf("RETURN\n"); }
"int"      { printf("INT_TYPE\n"); }

{DIGIT}+        { printf("NUM(%s)\n", yytext); }
{ID}            { printf("IDENT(%s)\n", yytext); }

"=="  { printf("EQ\n"); }
"!="  { printf("NE\n"); }
"<="  { printf("LE\n"); }
">="  { printf("GE\n"); }
"<"   { printf("LT\n"); }
">"   { printf("GT\n"); }
"="   { printf("ASSIGN\n"); }
"+"   { printf("PLUS\n"); }
"-"   { printf("MINUS\n"); }
"*"   { printf("STAR\n"); }
"/"   { printf("SLASH\n"); }
"("   { printf("LPAREN\n"); }
")"   { printf("RPAREN\n"); }
"{"   { printf("LBRACE\n"); }
"}"   { printf("RBRACE\n"); }
","   { printf("COMMA\n"); }
";"   { printf("SEMI\n"); }

"//".*       { /* 行註解 */ }
"/*"         { BEGIN(COMMENT); }
<COMMENT>"*/" { BEGIN(INITIAL); }
<COMMENT>\n  { }
<COMMENT>.   { }
<COMMENT><<EOF>> { fprintf(stderr, "unterminated comment\n"); yyterminate(); }

\"           { BEGIN(STRING); strbuf_init(); }
<STRING>\"   { BEGIN(INITIAL); printf("STR(%s)\n", strbuf_get()); }
<STRING>\\n  { strbuf_append('\n'); }
<STRING>\\t  { strbuf_append('\t'); }
<STRING>\\\" { strbuf_append('"'); }
<STRING>\\\\ { strbuf_append('\\'); }
<STRING>\n   { fprintf(stderr, "line %d: unterminated string\n", yylineno); }
<STRING>.    { strbuf_append(yytext[0]); }

[ \t\n]+    { }
.           { fprintf(stderr, "line %d: unexpected '%c'\n", yylineno, yytext[0]); }

%%

int main(void) { yylex(); return 0; }
```

編譯與測試：

```bash
flex minic.l
gcc lex.yy.c -o minic
echo 'int main() { return 42; }' | ./minic
```

## 動手練習

1. 把上面這支編譯並測試各種情況：關鍵字、識別字、數字、字串、註解。
2. 加入 hex、八進位整數。
3. 加入浮點數與科學記號。
4. 把字串的行為改成：若遇到換行，報錯但恢復到 `INITIAL` 狀態繼續解析（錯誤恢復）。

## 自我檢核

- [ ] 我能用起始狀態處理字串與多行註解
- [ ] 我能解釋為什麼 `"/*".*"*/"` 是錯的
- [ ] 我能寫一個有 20+ 條規則的 lexer 並知道順序怎麼排

→ [Ch 5 flex 與 C 互動](./05-flex-c-interaction.md)
