# Ch 0 — 環境搭建

> 目標：在你的機器上裝好 `flex`、`bison`、`gcc`，並且能編譯執行第一個 hello-world 範例。

## 工具關係釐清

先弄清楚名詞，避免之後混亂：

| 名稱 | 角色 | 備註 |
|---|---|---|
| **lex** | 早期 AT&T 詞法產生器 | 老古董，現在多半指其後繼者 |
| **yacc** | 早期 AT&T 語法產生器 | 同上 |
| **flex** | lex 的開源實作 | 現代主流，向後相容 lex |
| **bison** | yacc 的 GNU 實作 | 現代主流，向後相容 yacc |

**本課程一律用 flex + bison。** 寫法上，95% 跟古典 lex/yacc 相同，差別在於少數擴充功能和命令列選項。

## 安裝

### Windows（推薦 MSYS2）

1. 從 <https://www.msys2.org/> 下載安裝 MSYS2。
2. 開 MSYS2 終端，跑：

```bash
pacman -Syu               # 第一次先全更新
pacman -S flex bison gcc make
```

3. 把 `C:\msys64\usr\bin` 加到 Windows PATH（這樣 cmd/PowerShell 也能用）。

### Linux / WSL

```bash
sudo apt update
sudo apt install flex bison build-essential
```

### macOS

```bash
# Homebrew 自帶的 bison 比較新，建議用它而不是系統內建
brew install flex bison
```

注意 macOS 系統內建的 bison 是 2.3 老版本，要在 PATH 裡優先指向 Homebrew 的版本。

## 驗證安裝

```bash
flex --version
bison --version
gcc --version
```

三個都跑得出來，就 OK。建議版本：flex ≥ 2.6、bison ≥ 3.0。

## 第一個範例：字數統計器

我們不馬上碰 yacc，先用 flex 寫一個經典：**讀標準輸入，輸出字元數、單字數、行數**（就是 `wc` 指令的迷你版）。

建立檔案 `wc.l`：

```lex
%{
#include <stdio.h>
int chars = 0;
int words = 0;
int lines = 0;
%}

%%
[a-zA-Z]+    { words++; chars += yyleng; }
\n           { chars++; lines++; }
.            { chars++; }
%%

int main(void) {
    yylex();
    printf("lines=%d words=%d chars=%d\n", lines, words, chars);
    return 0;
}

int yywrap(void) { return 1; }
```

編譯與執行：

```bash
flex wc.l            # 產生 lex.yy.c
gcc lex.yy.c -o wc   # 編譯
echo "hello world from flex" | ./wc
# 輸出： lines=1 words=4 chars=23
```

## 拆解這支程式

flex 檔有三個段，用 `%%` 分隔：

```
宣告區
%%
規則區
%%
使用者程式碼
```

- **宣告區**：`%{ ... %}` 之間的 C 程式碼會原樣抄到輸出檔開頭，用來 include 標頭、宣告變數。
- **規則區**：每行是 `pattern { action }`。flex 會把這些 pattern 編譯成一台 DFA，遇到輸入時找最長匹配，跑對應 action。
- **使用者程式碼**：原樣抄到輸出檔結尾，通常放 `main()`。

## yywrap 是什麼？

`yywrap()` 是當輸入結束時 flex 會呼叫的回呼。回 `1` 表示「沒有更多輸入了」。如果你不寫，連結階段會缺符號。

懶得寫的話，可以在宣告區加：

```lex
%option noyywrap
```

之後就不用提供這個函式了。

## yyleng 與 yytext

flex 在每次匹配成功後會自動更新兩個變數：

- `yytext`：指向匹配到的字串（不是你管理的記憶體，下一次匹配會被覆蓋）
- `yyleng`：字串長度

用 `yytext` 但別保存它的指標，要存就 `strdup()` 一份。

## 常見坑

1. **沒裝 flex 卻以為裝了**：MSYS2 安裝完要重開終端，PATH 才生效。
2. **編譯時 `undefined reference to yywrap`**：加 `%option noyywrap` 或自己寫一個。
3. **正則沒匹配時整段被吃掉**：flex 預設規則是「印到 stdout」，不是「忽略」。如果你想丟掉看不懂的字元，要明確寫 `. { /* ignore */ }`。

## 動手練習

1. 把 `wc.l` 跑起來。
2. 餵它一個你寫的 `.c` 檔，看看單字數對不對（應該會偏多，因為它把 `int` `printf` 都算成單字）。
3. 加一條規則：把連續的空白（含 tab）視為單一分隔符，不計入 chars。

## 自我檢核

- [ ] 我能解釋 flex 三個段的作用
- [ ] 我知道 `yytext` 為什麼不能長期保存
- [ ] 我能編譯並跑通 `wc.l`

下一章我們來看編譯器前端的全景圖，理解 lex/yacc 在整條編譯流程裡是哪一段。

→ [Ch 1 編譯器前端全景](./01-frontend-overview.md)
