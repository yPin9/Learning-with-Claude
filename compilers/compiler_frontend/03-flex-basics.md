# Ch 3 — flex 基本語法

> 目標：完整掌握 `.l` 檔的結構，寫出一個會打招呼、會數東西、會忽略垃圾的 lexer。

## 三段結構複習

```
宣告區 (declarations)
%%
規則區 (rules)
%%
使用者程式碼 (user code)
```

三段都是選配的（規則區可以空但 `%%` 要留），但實務上三段都會用到。

## 宣告區

### C 程式碼 block

`%{ ... %}` 之間的內容原封不動複製到產生的 `lex.yy.c` 開頭：

```lex
%{
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int line_no = 1;

void error(const char *msg) {
    fprintf(stderr, "line %d: %s\n", line_no, msg);
    exit(1);
}
%}
```

這裡通常放：

- `#include`
- 全域變數（行號、計數器）
- 輔助函式宣告
- 要共用的 `enum` / `typedef`

### flex 選項

用 `%option` 開啟功能，不需要分號：

```lex
%option noyywrap      /* 不需要 yywrap() */
%option yylineno      /* 自動維護 yylineno 變數 */
%option case-insensitive  /* 規則不分大小寫 */
%option nodefault     /* 沒匹配到就報錯，而不是印出來 */
```

我個人幾乎每支 flex 檔都會寫：

```lex
%option noyywrap nodefault yylineno
```

- `noyywrap` 省掉寫 `yywrap()` 的麻煩
- `nodefault` 讓你的規則漏掉什麼字元時 flex 會抱怨，而不是默默把它印到 stdout（超容易讓你以為程式壞了）
- `yylineno` 自動數行，語法錯誤定位靠它

### 命名模式（定義）

可以把常用正則取名字，之後引用：

```lex
DIGIT       [0-9]
LETTER      [a-zA-Z_]
ID          {LETTER}({LETTER}|{DIGIT})*
INT         {DIGIT}+
FLOAT       {DIGIT}+"."{DIGIT}+
```

注意：

- 定義名不能用引號包裹
- 引用時要用 `{NAME}`，花括號不可少
- 定義是純文字展開，如果怕優先權出錯，建議整個用括號包起來

## 規則區

每條規則的格式：

```
pattern    { action code }
```

pattern 和 action 之間必須是**空白或 tab**，不能有其他東西。action 是 C 程式碼，可以多行但要用 `{}` 包起來。

### 範例：一個可用的最小 lexer

```lex
%option noyywrap nodefault yylineno

DIGIT   [0-9]
ID      [a-zA-Z_][a-zA-Z0-9_]*

%%
{DIGIT}+            { printf("NUM(%s)\n", yytext); }
"if"                { printf("IF\n"); }
"else"              { printf("ELSE\n"); }
"return"            { printf("RETURN\n"); }
{ID}                { printf("IDENT(%s)\n", yytext); }
"+"                 { printf("PLUS\n"); }
"-"                 { printf("MINUS\n"); }
"*"                 { printf("STAR\n"); }
"/"                 { printf("SLASH\n"); }
"="                 { printf("ASSIGN\n"); }
";"                 { printf("SEMI\n"); }
[ \t\n]+            { /* 忽略空白 */ }
.                   { printf("UNKNOWN(%s)\n", yytext); }
%%

int main(void) {
    yylex();
    return 0;
}
```

編譯與執行：

```bash
flex mini.l
gcc lex.yy.c -o mini
echo "if (x == 10) return x + 1;" | ./mini
```

## 動作裡能寫什麼？

幾乎任何 C 程式碼，但有幾個特別的：

### return

在 yacc 整合時，action 通常會 `return` 一個 token 編號：

```lex
"if"   { return IF; }
```

`return` 讓 `yylex()` 暫停並把 token 送給 parser。下次 parser 再呼叫 `yylex()` 時會從斷點繼續。

### BEGIN (起始狀態切換)

```lex
"/*"   { BEGIN(COMMENT); }
```

### ECHO

把 `yytext` 印到 stdout，等同 `fwrite(yytext, yyleng, 1, yyout)`。

### REJECT

放棄當前匹配，改用**次優**匹配。很少用，除非你在做重疊規則的處理。

### yyless(n) / yymore()

- `yyless(n)`：把已吃掉的字元往回吐，只保留前 n 個。
- `yymore()`：下次匹配時接在目前 `yytext` 後面，不重置。

這些是進階工具，Ch 5 會再提。

## 使用者程式碼區

第二個 `%%` 之後的東西會複製到 `lex.yy.c` 尾端。通常放 `main()` 和輔助函式：

```lex
%%
int main(int argc, char **argv) {
    if (argc > 1) {
        FILE *f = fopen(argv[1], "r");
        if (!f) { perror(argv[1]); return 1; }
        yyin = f;
    }
    yylex();
    return 0;
}
```

### yyin 是什麼？

`yyin` 是 flex 從哪裡讀取輸入的 `FILE*`，預設是 `stdin`。你可以改它：

```c
yyin = fopen("input.txt", "r");
yylex();
```

這是 lexer「讀檔 vs 讀管線」的切換點。

## 規則的順序策略

整理一下先前講過的原則，加上新規則：

1. **關鍵字在識別字之前**（平手時前面贏）
2. **長的運算子在短的之前**：`==` 寫在 `=` 之前（雖然最長匹配會照顧到，但明確更好）
3. **最後放 `.`** 當 catch-all：匹配任何看不懂的單一字元
4. **空白通常忽略**：`[ \t\n]+` 放在規則群組末尾、catch-all 之前

這個順序已經能解決 90% 的問題。

## 常用的 `%option` 整理

| 選項 | 作用 |
|---|---|
| `noyywrap` | 不需要 `yywrap()` |
| `yylineno` | 自動維護 `yylineno` |
| `nodefault` | 沒匹配到就當錯誤（推薦） |
| `case-insensitive` | 規則忽略大小寫 |
| `outfile="xxx.c"` | 指定輸出檔名 |
| `prefix="foo"` | 把 `yy...` 前綴換成 `foo...`，用於同一個程式有多個 lexer |
| `reentrant` | 產生執行緒安全的版本 |
| `bison-bridge` | 配合 bison 的 `%locations` / `%pure-parser` |

前三個幾乎必備，後面的看需求。

## 動手練習

1. 照上面範例把 `mini.l` 跑起來，確認輸出符合預期。
2. 加入 C 的比較運算子：`==`, `!=`, `<`, `>`, `<=`, `>=`，每個印出對應 token。
3. 故意犯個錯：把 `==` 規則拿掉，看看輸入 `x == 10` 會怎麼被切。理解為什麼。
4. 加入 `%option nodefault` 看看漏掉某些字元（例如 `@`）時會發生什麼。

## 自我檢核

- [ ] 我能寫一個有 `%{}` 宣告 + 命名模式 + 規則 + `main` 的完整 flex 檔
- [ ] 我知道 `%option noyywrap nodefault yylineno` 各做什麼
- [ ] 我知道動作裡的 `return` 在 yacc 整合時的意義
- [ ] 我能解釋為什麼規則順序重要

→ [Ch 4 常見詞法模式](./04-flex-common-patterns.md)
