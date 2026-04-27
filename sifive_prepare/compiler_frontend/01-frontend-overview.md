# Ch 1 — 編譯器前端全景

> 目標：在動手寫 lex/yacc 之前，先看清楚整個編譯流程，知道我們要學的這兩個工具在哪一段、做什麼、不做什麼。

## 一張圖看懂整個編譯器

```
原始碼 (source)
   │
   ▼
┌──────────────┐
│ 詞法分析     │  Lexer / Scanner   ← lex/flex
│ Lexical      │  字串 → token 流
└──────────────┘
   │ tokens
   ▼
┌──────────────┐
│ 語法分析     │  Parser            ← yacc/bison
│ Syntactic    │  token → parse tree
└──────────────┘
   │ parse tree / AST
   ▼
┌──────────────┐
│ 語義分析     │  Semantic Analysis ← 你自己寫
│ Semantic     │  型別檢查、符號表
└──────────────┘
   │ 帶語義資訊的 AST
   ▼
┌──────────────┐
│ 中介碼產生   │  IR Generation
│ IR Gen       │  AST → 三位址碼/SSA
└──────────────┘
   │ IR
   ▼
   ... 後端（最佳化、暫存器配置、組語產生） ...
```

**前端 (frontend)** 指的是「源碼 → IR」這段，不依賴目標機器。
**後端 (backend)** 才是「IR → 機器碼」，跟 CPU 架構掛鉤。

本課程只做前端。

## 三個階段在做什麼？實例演示

假設原始碼是：

```c
x = a + 42;
```

### Stage 1：詞法分析（Lexer）

lexer 看到的是一串字元，它的工作是切出一個個有意義的「詞」(token)：

```
['x']  ['=']  ['a']  ['+']  ['4']['2']  [';']
```

切完之後變成：

```
IDENT("x")  ASSIGN  IDENT("a")  PLUS  NUMBER(42)  SEMICOLON
```

每個 token 有兩個資訊：
- **種類** (kind)：是識別字、運算子、還是字面量？
- **值** (value)：實際的字串或數值

lexer **不關心**這些 token 拼起來合不合文法，它只負責切。
`+ + + ; ; ;` 在 lexer 眼中是合法的，會乖乖切出 6 個 token。

### Stage 2：語法分析（Parser）

parser 拿著 token 流，根據文法規則檢查「這串 token 能不能組成一個合法句子」，並且建出一棵樹：

```
        Assign
       /      \
    IDENT(x)  Plus
              /  \
         IDENT(a) NUMBER(42)
```

這棵樹叫 **parse tree**（具體語法樹）或 **AST**（抽象語法樹，去掉了如分號之類的雜訊）。

parser **不關心** `x` 是不是宣告過、`a` 跟 `42` 型別合不合。它只看結構。

### Stage 3：語義分析

語義分析走訪 AST，做這些事：

- `x` 有宣告嗎？是什麼型別？→ 查**符號表**
- `a + 42` 兩邊型別相容嗎？→ **型別檢查**
- 如果 `x` 是 `const`，能不能被 assign？→ **規則檢查**

語義分析會給 AST 的節點加上型別等資訊，或者直接報錯。

## 為什麼要分這麼多層？

歷史上有人問過：「lexer 跟 parser 為什麼不合在一起？反正都是讀字元。」

分層的理由：

1. **正則文法 vs 上下文無關文法**：詞法層次的東西（識別字、數字）用正則就能描述；句法層次（巢狀括號、if/else）需要更強的文法。用對工具事半功倍。
2. **效率**：lexer 是 DFA，超快。讓 parser 只處理 token 而非字元，可以少做很多狀態轉移。
3. **可讀性**：parser 規則裡寫 `expr PLUS expr` 比 `expr [ \t]* '+' [ \t]* expr` 清爽太多。

## lex/yacc 的角色

| 工具 | 輸入 | 輸出 |
|---|---|---|
| **flex** | `.l` 檔（正則 + 動作） | `lex.yy.c`（一個 `yylex()` 函式） |
| **bison** | `.y` 檔（文法 + 動作） | `xxx.tab.c`（一個 `yyparse()` 函式） |

它們都是 **產生器** (generator)：你寫規格描述，工具幫你產生 C 程式碼。整個架構長這樣：

```
your_lang.l ──flex──▶ lex.yy.c ──┐
                                  ├──gcc──▶ your_compiler
your_lang.y ──bison─▶ y.tab.c  ──┘
```

`yyparse()` 在運行時會反覆呼叫 `yylex()` 拿下一個 token，這就是兩者的接口。

## AST vs Parse Tree

容易混淆，澄清一下：

- **Parse Tree**：忠實反映文法推導，連分號、括號都有節點。
- **AST**：抽象掉語法噪音，只留語義有意義的結構。

範例：`(1 + 2) * 3`

Parse Tree（簡化）：
```
expr → expr * expr
       │       │
       (expr)  3
        │
       expr + expr
        │       │
        1       2
```

AST：
```
    *
   / \
  +   3
 / \
1   2
```

實務上我們**不會生成 parse tree**，而是在 yacc 動作裡直接生成 AST。Part 4 會詳細談。

## 一個常見誤解

「flex/bison 已經是 1980 年代的東西，是不是過時了？」

不全然。現代大型編譯器（gcc、clang）確實**手寫** lexer 和 recursive descent parser，因為要產生更好的錯誤訊息、做增量解析。

但是：

- 學習編譯原理時，flex/bison 讓你**專注在文法本身**，不會被 parser 實作細節淹沒。
- 寫小型 DSL、設定檔語言、計算器、SQL 子集，flex/bison 仍是性價比極高的選擇。
- PostgreSQL、MySQL、Bash 至今都還在用 bison。

學會這套，再看 recursive descent 或 parser combinator 會更有感。

## 動手練習

不用寫 code，用紙筆完成：

把下面這段 C 拆成 token 流，並畫出對應的 AST：

```c
if (x > 0) y = x + 1;
```

提示：token 種類大概有 `IF`、`LPAREN`、`IDENT`、`GT`、`NUMBER`、`RPAREN`、`ASSIGN`、`PLUS`、`SEMI`。

## 自我檢核

- [ ] 我能說出前端三個階段的輸入輸出
- [ ] 我能解釋為什麼 lexer 和 parser 要分開
- [ ] 我能區分 parse tree 和 AST
- [ ] 我知道 flex 產出 `yylex()`、bison 產出 `yyparse()`，後者呼叫前者

下一章我們補一點正則表達式與 DFA 的直覺，這是看懂 flex 行為的基礎。

→ [Ch 2 正則表達式與 DFA 直覺](./02-regex-and-dfa.md)
