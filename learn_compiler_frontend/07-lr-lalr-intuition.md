# Ch 7 — LR/LALR 直覺

> 目標：看懂 yacc 的工作原理、看懂它的錯誤訊息（特別是 `shift/reduce conflict`），但不需要手算 LR 表。

## 兩大 parser 家族

parser 的實作策略大致分兩類：

### Top-down（LL）

從開始符號出發，一路往下展開，嘗試匹配輸入。

- **代表**：recursive descent、ANTLR、LL(1)
- **直覺**：「我猜這是個 if 語句」→ 往下檢查是不是 `IF '(' ...`
- **限制**：不能處理左遞迴文法

### Bottom-up（LR）

從輸入開始，一路往上歸約，直到整個被歸約成開始符號。

- **代表**：yacc/bison 用的 LALR(1)
- **直覺**：「我看到 `NUMBER + NUMBER`，這可以歸約成 `expr`」
- **強項**：可以處理左遞迴、支援更大的文法子集

yacc 是 LR 家族，所以我們只聊 LR。

## LR 的核心操作：shift 與 reduce

LR parser 維護一個**堆疊**和一個**當前輸入指標**。每一步做兩種動作之一：

### Shift

把當前輸入 token 推入堆疊，指標往右移一格。

```
堆疊: [ ]          輸入: NUMBER + NUMBER
---(shift)---
堆疊: [ NUMBER ]   輸入: + NUMBER
```

### Reduce

看堆疊頂端的幾個符號，把它們**歸約**成某個非終結符。

文法 `expr : NUMBER`，那：

```
堆疊: [ NUMBER ]   輸入: + NUMBER
---(reduce by: expr → NUMBER)---
堆疊: [ expr ]     輸入: + NUMBER
```

### 完整範例

文法：
```
expr : expr '+' expr
     | NUMBER
```

解析 `1 + 2`：

| 步驟 | 堆疊 | 輸入 | 動作 |
|---|---|---|---|
| 0 | | `1 + 2` | shift |
| 1 | `1` | `+ 2` | reduce expr → NUMBER |
| 2 | `expr` | `+ 2` | shift |
| 3 | `expr +` | `2` | shift |
| 4 | `expr + 2` | | reduce expr → NUMBER |
| 5 | `expr + expr` | | reduce expr → expr + expr |
| 6 | `expr` | | 接受 |

這就是一個 shift-reduce parser 的一生。

## 那 LR(1)、LALR(1) 的「1」是什麼？

「1」表示 **lookahead 1 個 token**。parser 在決定 shift 還是 reduce 時，可以**偷看**下一個 token 一眼來幫助決策。

常見等級（從弱到強）：

- **LR(0)**：不偷看，純靠堆疊。能力有限。
- **SLR(1)**：偷看一個，但決策邏輯簡單。
- **LALR(1)**：偷看一個，yacc 用這個。在 SLR(1) 和 LR(1) 之間。
- **LR(1)**：偷看一個，最強的實用級別。狀態機可能很大。
- **GLR**：多路徑並行，能處理任意上下文無關文法（bison 可選）。

**LALR(1) 是一個實用上的甜蜜點**：強到能表達大部分程式語言，但狀態表不會爆炸。C、Java、SQL 的 yacc 語法都在這個級別內。

## 衝突：當 parser 不知道怎麼做

### shift/reduce conflict

parser 看堆疊和 lookahead，發現**既能 shift 也能 reduce**。

經典案例 dangling else：

```
stmt : IF '(' expr ')' stmt
     | IF '(' expr ')' stmt ELSE stmt
     | ...
```

輸入：
```
IF ( x ) IF ( y ) S ELSE T
```

解析到某個點，堆疊是 `IF ( expr ) IF ( expr ) stmt`，下一個 token 是 `ELSE`。

- **shift**：把 `ELSE` 推上，繼續組內層 if。`ELSE` 綁內層 if。
- **reduce**：先把內層變成 `stmt`，`ELSE` 綁外層 if。

這兩個選擇語義不同。yacc **預設選 shift**，這剛好就是 C 的行為（else 綁最近的 if），所以多數情況下 yacc 的 shift/reduce 警告**可以接受**，但你要確認它選的方向符合你的語言定義。

### reduce/reduce conflict

parser 可以用兩條不同的規則做 reduce，不知道該用哪條。這**通常是真 bug**，要修文法。

範例：
```
x : A B ;
y : A B ;
z : x | y ;
```

看到 `A B` 後，parser 可以 reduce 成 `x` 也可以 reduce 成 `y`。yacc 會選**先寫的**那條，但這種衝突基本上意味著你的文法設計有問題。

## 看懂 bison 的輸出

跑 bison 時加 `-v` 會產出 `xxx.output` 檔：

```bash
bison -v grammar.y
```

裡面有幾樣寶物：

### 狀態機

```
State 7

    3 expr: expr . '+' expr
    3      | expr '+' expr .

    '+'  shift, and go to state 5
    '+'  [reduce using rule 3]
    $end reduce using rule 3
```

`.` 表示「目前位置」。這個狀態告訴你：
- 規則 3 已經完全匹配 → 可以 reduce
- 但下一個如果是 `+`，也可以 shift

括號裡的 `[reduce using rule 3]` 表示這是被忽略的選項，bison 選了 shift。

### 衝突摘要

```
grammar.y: warning: 1 shift/reduce conflict [-Wconflicts-sr]
```

遇到這個就開 `.output` 找 `conflict`。

### 不可達規則

```
grammar.y:10.5-12: warning: rule useless in grammar [-Wother]
```

文法寫錯（某條規則永遠用不到）。

## 心智模型：把 yacc 當成翻譯機

你寫的是「文法 + 每條規則要做什麼（action）」，yacc 幫你產生一個 bottom-up parser，每當它做 reduce 時就呼叫你寫的 action。

實務上你不需要會算 LR 表，但要知道：

1. shift 就是把 token 吞進堆疊
2. reduce 就是把堆疊裡的一段換成非終結符 + 觸發你的 action
3. 衝突出現時，開 `.output` 看看，多半能看懂

## 為什麼 yacc 偏好左遞迴

bottom-up parser 做 reduce 是從底部往上推。左遞迴文法：

```
list : list ',' item | item ;
```

對 `a, b, c, d` 的解析過程：

```
[a]                  reduce  → [list]
[list , b]           reduce  → [list]
[list , c]           reduce  → [list]
[list , d]           reduce  → [list]
```

堆疊始終很淺。

右遞迴：
```
list : item ',' list | item ;
```

解析同一輸入：
```
[a , b , c , d]      必須全部 shift，再從右邊開始 reduce
```

堆疊深度 O(n)，對大列表可能爆。

## 動手：觀察 bison 怎麼工作

寫個最小 `.y` 產生 `.output`：

```bison
%token NUMBER
%%
expr : expr '+' expr
     | NUMBER
     ;
%%
```

跑：
```bash
bison -v demo.y
cat demo.output
```

你會看到一份「parser 狀態機說明書」。試著跟著某個輸入走一遍，看懂每一步在哪個狀態、做了什麼。

## 自我檢核

- [ ] 我能解釋 shift 和 reduce 的差別
- [ ] 我知道 LALR(1) 的「1」指 lookahead
- [ ] 我能說出 shift/reduce conflict 和 reduce/reduce conflict 的差別與處理態度
- [ ] 我能打開 `.output` 看狀態機

下一章進入 bison 的實戰語法。

→ [Ch 8 bison 基本語法](./08-bison-basics.md)
