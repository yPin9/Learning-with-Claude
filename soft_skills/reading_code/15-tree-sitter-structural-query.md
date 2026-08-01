# Ch 15 — tree-sitter 與結構化查詢

> **目標**：跳脫「把 code 當文字」的層次，改成「把 code 當語法樹」。你會懂 AST（抽象語法樹）為什麼是讀碼查詢的正確抽象、tree-sitter 憑什麼能同時做到增量與容錯、S-expression query 語法怎麼寫，並在 redis 真檔上跑出「所有函式定義的名字」「所有呼叫 zmalloc 的地方」「所有 TODO 註解」這幾類 regex 做不乾淨的查詢。最後看清 tree-sitter 相對 grep 與 ast-grep 的定位。

> **環境**：WSL2 Ubuntu 22.04，tree-sitter CLI 0.22.6。沙包 `~/reading_code_lab/redis`（redis 7.4.0）。tree-sitter-c grammar 需要一次性設定，本章**如實記錄了實跑步驟與踩到的 ABI 版本坑**。

## 為什麼 regex 讀 code 會騙你

先講一個你早就隱約知道、但很少認真面對的事實：**原始碼是有結構的文字，而 grep 只看得到文字。**

grep / ripgrep（Ch 12）強在通用與速度，但它對「程式的結構」一無所知。你想找「所有呼叫 `zmalloc` 的地方」，寫 `rg 'zmalloc'`，得到的是**所有出現 `zmalloc` 這七個字元的行**——包括：

- 註解裡提到的 `/* never call zmalloc without checking */`
- 字串常數 `"call zmalloc to allocate"`
- 名字裡含 `zmalloc` 的別的東西：`zmalloc_used_memory`、`zmalloc_dummy`
- 真正的呼叫 `p = zmalloc(16)`

grep 分不出這四種。你可以用更精巧的 regex（`zmalloc\s*\(`）逼近，但永遠有邊界：換行、巨集、`zmalloc /* 註解 */ (` 這種奇葩寫法，regex 遲早破功。**根本問題是 regex 的世界觀是「一維字元流」，而程式是「二維以上的巢狀結構」。** 用一維工具查二維結構，註定漏或多。

正確的抽象是**語法樹（AST）**。回顧 Ch 1 的編譯器前端：parser 把 token 流變成一棵樹，`zmalloc(16)` 在樹上是一個 `call_expression` 節點，它的 `function` 欄位是 `identifier "zmalloc"`。註解是 `comment` 節點、字串是 `string_literal` 節點——它們在樹上是**完全不同種類的節點**。只要你能對著這棵樹提問「給我所有 function 欄位是 `zmalloc` 的 `call_expression`」，上面四種噪音自動消失。

```
   regex 的世界              tree-sitter 的世界
   ┌───────────────┐        (call_expression
   │ 一維字元流    │          function: (identifier)  ← 精準指到「被呼叫的名字」
   │ z m a l l o c │          arguments: (argument_list ...))
   │ ( 1 6 )       │        註解是 (comment)、字串是 (string_literal)
   └───────────────┘        ── 不同節點種類，天生分得開
```

這章的工具 **tree-sitter** 就是把「對 AST 提問」變成一條 CLI 指令。

## tree-sitter 是什麼、憑什麼

tree-sitter 是一個**parser 產生器 + runtime**，它為幾十種語言各自提供一個小巧、快速、可嵌入的 parser。它的三個特點決定了它為什麼成為現代編輯器（Neovim、Zed、GitHub 語法高亮）與讀碼工具的底層：

1. **增量 parse（incremental）**：你在編輯器裡改一行，tree-sitter 不重 parse 整個檔，只重算受影響的子樹。這是它能在你每次按鍵時即時更新語法高亮而不卡的原因。對讀碼工具的意義：對大檔重複查詢很便宜。

2. **容錯（error-tolerant）**：真實 code 常常是**語法不完整**的——你正打到一半、有巨集展開後才合法的片段、`#ifdef` 切掉半個函式。傳統編譯器 parser 遇到語法錯就整個罷工；tree-sitter 會盡量 parse，把錯的地方標成 `ERROR` 節點，其餘照常給你樹。這對讀「編不起來的 legacy code」是關鍵——你不需要它能編譯（對照 Ch 13 的 clangd，那個非編不可）。

3. **多語言統一介面**：C、C++、Python、Go、Rust、JS… 每種語言一個 grammar，但查詢語法（S-expression query）與 CLI 是同一套。學一次，跨語言用。

一句話定位：**tree-sitter = 不用能編譯、容錯、跨語言的「結構化 grep」**。它比 grep 懂結構，比 clangd 輕、不挑能不能編譯；代價是它只懂語法、不懂語意（分不出兩個同名符號的作用域、不做型別）。

## 實跑設定：grammar 與那個 ABI 版本坑

這裡如實記錄我在本機把 tree-sitter-c 跑起來的完整過程，包含踩到的坑——因為這正是你自己會遇到的。

tree-sitter CLI 本身裝好了（`tree-sitter 0.22.6`），但它**不內建任何語言的 grammar**，要自己提供。第一步 clone C grammar：

```
$ cd /tmp && git clone --depth 1 https://github.com/tree-sitter/tree-sitter-c
$ cd tree-sitter-c && tree-sitter parse ~/reading_code_lab/redis/src/ae.c
Warning: You have not configured any parser directories!
Please run `tree-sitter init-config` ...
incompatible language

Caused by:
    Incompatible language version 15. Expected minimum 13, maximum 14
```

**兩個坑同時爆**：

- **坑一：沒設 config**。tree-sitter CLI 要一份 `~/.config/tree-sitter/config.json` 告訴它去哪找 grammar。用 `tree-sitter init-config` 產生：
  ```
  $ tree-sitter init-config
  Saved initial configuration to /home/ypp/.config/tree-sitter/config.json
  ```
  它預設會去 `~/github`、`~/src`、`~/projects` 等目錄找 grammar。

- **坑二：ABI 版本不合**。clone 下來的 tree-sitter-c 是最新的，它 checked-in 的 `parser.c` 是 **language ABI version 15**，但我的 CLI 0.22.6 只支援到 **14**。錯誤訊息 `Incompatible language version 15. Expected minimum 13, maximum 14` 就是這個。

**解法**：不要用 repo 裡預先產好的 parser，用**你自己的 CLI 重新 `tree-sitter generate`**，並指定 `--abi 14` 產出相容版本。把 grammar 放進 parser 目錄後重生成：

```
$ mkdir -p ~/github && cp -r /tmp/tree-sitter-c ~/github/ && cd ~/github/tree-sitter-c
$ tree-sitter generate --abi 14
Adding a prebuildify script to package.json
Adding a `tree-sitter` section to package.json
$ echo exit=$?
exit=0
```

`generate` 讀 `grammar.js`（grammar 的 DSL 定義），吐出符合當前 CLI ABI 的 `src/parser.c` 並 build。之後 parse 就成功了：

```
$ tree-sitter parse ~/reading_code_lab/redis/src/ae.c | head -8
(translation_unit [0, 0] - [493, 0]
  (comment [0, 0] - [9, 3])
  (preproc_include [11, 0] - [12, 0]
    path: (string_literal [11, 9] - [11, 15]
      (string_content [11, 10] - [11, 14])))
  (preproc_include [12, 0] - [13, 0]
    path: (string_literal [12, 9] - [12, 17]
      (string_content [12, 10] - [12, 16])))
  ...
```

這就是 `ae.c` 的語法樹（S-expression 格式）。每個節點標了種類（`translation_unit`、`comment`、`preproc_include`、`string_literal`）與位置 `[起始行, 起始欄] - [結束行, 結束欄]`。第一個節點是 `[0,0]-[9,3]` 的 `comment`——就是檔案開頭那段版權註解。

> **教訓**：遇到 `Incompatible language version N` 不是 grammar 壞了，是 grammar 的預生成 parser 比你的 CLI 新。用自己的 CLI `tree-sitter generate --abi <你的max>` 重生成即可。反過來（grammar 太舊）就升級 grammar 或降 CLI 期望。這是 tree-sitter 新手第一個必踩的坑，值得記牢。

## 讀懂 S-expression 語法樹

`tree-sitter parse` 吐的 S-expression（`(節點種類 子節點...)`）就是 AST 的文字表示。讀它的訣竅：

- **括號 = 一個節點**，第一個 token 是**節點種類**（node type，如 `call_expression`）。
- **`field:` 標的是具名欄位**（named field）。`function:`、`declarator:`、`path:` 這些是 grammar 給子節點的角色名——查詢時靠它精準定位「呼叫的是哪個部分」。
- **`[行,欄]-[行,欄]`** 是節點在原始碼的範圍。

先看一個函式定義在樹上長什麼樣（`aeMain`）。概念結構是：

```
(function_definition
  type: (primitive_type)                    ← void
  declarator: (function_declarator
    declarator: (identifier)                ← aeMain  ← 我們要抓的名字
    parameters: (parameter_list ...)))
```

看懂這個「路徑」——函式名藏在 `function_definition > declarator: function_declarator > declarator: identifier`——你就能寫出精準抓函式名的 query。這是結構化查詢的核心心法：**先 parse 一個範例，看清楚你要的東西在樹上的路徑，再照著路徑寫 query。**

再看一個完整的小範例，把「控制流結構」在樹上長怎樣看透。把這段丟給 `tree-sitter parse`：

```c
int f(int x) {
    if (x > 0) {
        return x + 1;
    } else {
        return 0;
    }
}
```

得到（真實輸出，節略位置）：

```
(function_definition
  type: (primitive_type)                       ← int
  declarator: (function_declarator
    declarator: (identifier)                   ← f
    parameters: (parameter_list
      (parameter_declaration
        type: (primitive_type)
        declarator: (identifier))))            ← x
  body: (compound_statement
    (if_statement
      condition: (parenthesized_expression
        (binary_expression                     ← x > 0
          left: (identifier)
          right: (number_literal)))
      consequence: (compound_statement         ← then 分支
        (return_statement
          (binary_expression ...)))            ← x + 1
      alternative: (else_clause                ← else 分支
        (compound_statement
          (return_statement
            (number_literal)))))))             ← 0
```

一個 `if_statement` 節點掛著三個具名欄位：`condition:`（條件）、`consequence:`（then 分支）、`alternative:`（else，包在 `else_clause` 裡）。這棵樹把「巢狀」「分支」全部顯性化——`if` 裡的 `return` 是 `if_statement > consequence: compound_statement > return_statement`，路徑清清楚楚。regex 要表達「if 的 else 分支裡的 return」幾乎不可能；在樹上它只是一條欄位路徑。

想知道一個檔的樹有多「厚」，數一數節點種類的分布（真實輸出，`ae.c`）：

```
$ tree-sitter parse src/ae.c | grep -oP '\(\K[a-z_]+' | sort | uniq -c | sort -rn | head
    533 identifier
    159 field_identifier
    159 field_expression      ← eventLoop->events 這種
     97 binary_expression
     87 assignment_expression
     84 parenthesized_expression
     56 if_statement          ← 56 個 if
     49 compound_statement
     46 primitive_type
     42 pointer_declarator
```

這份直方圖本身就是讀碼線索：159 個 `field_expression`（`a->b` 存取）說明 `ae.c` 大量在操作 struct 欄位、56 個 `if_statement` 說明控制流分支密集。每一種節點都是你能寫 query 鎖定的目標。

## S-expression Query：對樹提問

tree-sitter 的查詢語言就是「帶捕獲的 S-expression pattern」。你寫一個部分的樹形狀，tree-sitter 幫你在整棵樹裡找所有 match，`@name` 標記你想抓出來的節點。

### Query 1：所有函式定義的名字

把上面看到的路徑寫成 query，存成 `.scm` 檔：

```scheme
(function_definition
  declarator: (function_declarator
    declarator: (identifier) @func.name))
```

`@func.name` 捕獲那個 `identifier` 節點。跑：

```
$ tree-sitter query /tmp/q_funcdef.scm ~/reading_code_lab/redis/src/ae.c
/home/ypp/reading_code_lab/redis/src/ae.c
  pattern: 0
    capture: 0 - func.name, start: (80, 4), end: (80, 16), text: `aeGetSetSize`
  pattern: 0
    capture: 0 - func.name, start: (89, 5), end: (89, 18), text: `aeSetDontWait`
  pattern: 0
    capture: 0 - func.name, start: (103, 4), end: (103, 19), text: `aeResizeSetSize`
  pattern: 0
    capture: 0 - func.name, start: (121, 5), end: (121, 22), text: `aeDeleteEventLoop`
  pattern: 0
    capture: 0 - func.name, start: (138, 5), end: (138, 11), text: `aeStop`
  pattern: 0
    capture: 0 - func.name, start: (142, 4), end: (142, 21), text: `aeCreateFileEvent`
  ...
```

每個 match 給你捕獲名、精確位置、與 `text`（原始碼片段）。這是一份**乾淨的函式清單**——沒有宣告、沒有函式指標型別、沒有註解裡提到的函式名混進來，因為 query 只 match `function_definition` 這種節點。對照 ctags（Ch 14）也能列函式，但 tree-sitter 的優勢是**你能任意組合條件**（下面就看到）。

### Query 2：所有呼叫 zmalloc 家族的地方（帶 predicate）

這是 regex 做不乾淨的經典。用 `#match?` predicate 對捕獲做 regex 過濾：

```scheme
(call_expression
  function: (identifier) @fn
  (#match? @fn "^z(malloc|calloc|realloc|free)$"))
```

`function: (identifier) @fn` 只 match「被呼叫的名字是個單純 identifier」的呼叫，`#match?` 再過濾名字符合 `zmalloc/zcalloc/zrealloc/zfree`。跑在 `dict.c`：

```
$ tree-sitter query /tmp/q_alloc.scm ~/reading_code_lab/redis/src/dict.c | head -12
/home/ypp/reading_code_lab/redis/src/dict.c
  pattern: 0
    capture: 0 - fn, start: (145, 30), end: (145, 37), text: `zmalloc`
  pattern: 0
    capture: 0 - fn, start: (186, 14), end: (186, 21), text: `zmalloc`
  pattern: 0
    capture: 0 - fn, start: (203, 9), end: (203, 17), text: `zrealloc`
  pattern: 0
    capture: 0 - fn, start: (250, 23), end: (250, 30), text: `zcalloc`
  pattern: 0
    capture: 0 - fn, start: (268, 28), end: (268, 33), text: `zfree`
  ...
```

每一筆都是**真正的呼叫點**。`#match?` 讓你在結構過濾（必須是 call）之上再加文字過濾（名字符合 pattern），兩者疊加。這就是「結構 + 正則」的組合拳。

### Query 3：所有 TODO / FIXME 註解

註解在樹上是 `comment` 節點，一查一個準：

```scheme
((comment) @c
 (#match? @c "TODO|FIXME|XXX|HACK"))
```

`server.c` 沒有這類註解（查詢回空），換到 `t_stream.c` 就命中：

```
$ tree-sitter query /tmp/q_todo.scm ~/reading_code_lab/redis/src/t_stream.c
/home/ypp/reading_code_lab/redis/src/t_stream.c
  pattern: 0
    capture: 0 - c, start: (838, 12), end: (838, 53), text: `/* TODO: perform a garbage collection. */`
  pattern: 0
    capture: c, start: (1306, 4), end: (1307, 54)
```

第二筆跨兩行（`[1306,4]-[1307,54]`）——一個多行 `/* ... */` 註解，tree-sitter 當成**單一 comment 節點**正確處理。regex 要跨行匹配註解得動用 `-U` multiline 加小心翼翼的 pattern；tree-sitter 天生知道「這是一個註解節點」，跨不跨行無所謂。

### Query 4：所有 if 的條件（結構定位，regex 全無能為力）

用上面看懂的 `if_statement` 路徑，抓每個 if 的條件表達式：

```scheme
(if_statement
  condition: (parenthesized_expression) @cond)
```

跑在 `ae.c`：

```
$ tree-sitter query /tmp/q_if.scm ~/reading_code_lab/redis/src/ae.c | head -9
/home/ypp/reading_code_lab/redis/src/ae.c
  pattern: 0
    capture: 0 - cond, start: (51, 7), end: (51, 58),
      text: `((eventLoop = zmalloc(sizeof(*eventLoop))) == NULL)`
  pattern: 0
    capture: 0 - cond, start: (54, 7), end: (54, 62),
      text: `(eventLoop->events == NULL || eventLoop->fired == NULL)`
  pattern: 0
    capture: 0 - cond, start: (63, 7), end: (63, 37),
      text: `(aeApiCreate(eventLoop) == -1)`
```

每一筆都是一個 if 的**完整條件**，連跨行、含函式呼叫、含 `||` 的複雜條件都完整框出（`text` 是精確的原始碼片段）。這種查詢 regex 做不到——「找 if 的條件」需要知道「什麼是 if、它的 condition 欄位在哪」，那是結構層的知識。這類 query 在審計時很有用：例如抓「所有 `== NULL` 的 null 檢查」或「所有含賦值的條件（`if (x = f())` 這種可疑寫法）」，一 query 掃全庫。

## 決定性對照：regex vs tree-sitter 的假陽性

把「grep 會騙你」講清楚，我造一個小檔，讓 `zmalloc` 出現在四種語境，只有一個是真呼叫：

```c
/* We must never call zmalloc here without checking. */   // ← 註解
#include <stdlib.h>
const char *msg = "call zmalloc to allocate";             // ← 字串
void f(void) {
    // zmalloc below is the real one:                     // ← 註解
    char *p = zmalloc(16);                                // ← 真呼叫
    free(p);
}
void
zmalloc_dummy(void) {}   /* NAME 含 zmalloc */            // ← 別的符號
```

naive grep 五個命中，四個是噪音：

```
$ grep -n "zmalloc" /tmp/tsdemo/demo.c
1:/* We must never call zmalloc here without checking. */
4:const char *msg = "call zmalloc to allocate";
7:    // zmalloc below is the real one:
8:    char *p = zmalloc(16);
13:zmalloc_dummy(void) {}   /* a function whose NAME contains zmalloc */
```

tree-sitter query（`#eq?` 精確比對）只給那**唯一一個真呼叫**：

```
$ tree-sitter query /tmp/q_call_only.scm /tmp/tsdemo/demo.c
/tmp/tsdemo/demo.c
  pattern: 0
    capture: 0 - fn, start: (7, 14), end: (7, 21), text: `zmalloc`
```

5 → 1。這就是結構化查詢的價值：**它問的是「哪裡真的呼叫了 zmalloc」，而不是「哪裡出現了 zmalloc 這串字」。** 在讀大 codebase 做「找出所有真正用到某危險 API（`strcpy`、`system`、`memcpy`）的地方」這種安全審計任務時（呼應 Ch 32），這個精準度直接決定你要不要人工複核 100 個假陽性。

## ast-grep：更好上手的結構搜尋

tree-sitter query 的 S-expression 語法有學習曲線——你得先 parse 看樹、知道節點種類與欄位名。**ast-grep**（`sg`）是建立在 tree-sitter 之上的工具，把查詢語法換成「**寫一段看起來像 code 的 pattern，用 `$VAR` 當萬用洞**」，直覺得多。

> **實跑狀態**：本機當下**未安裝 ast-grep**（`ast-grep NOT INSTALLED`），以下語法為理論說明，未實測。安裝方式 `cargo install ast-grep` 或 `npm i -g @ast-grep/cli`。

同樣「找所有 `zmalloc(...)` 呼叫」，ast-grep 寫成：

```
$ sg -p 'zmalloc($$$)' -l c src/          # $$$ 代表任意多個引數
```

你不用知道節點叫 `call_expression`、欄位叫 `function`——直接寫你想找的 code 形狀，`$A`（單一節點）、`$$$`（多節點/引數列）當佔位。要重構還能 `-r` 給 rewrite pattern（例如把 `malloc($N)` 改成 `zmalloc($N)`）。

**取捨**：ast-grep 上手快、適合日常「找形狀 / 批次改寫」；tree-sitter 原生 query 更底層、能表達更精細的結構條件（巢狀欄位、多 pattern、`#match?`/`#eq?`/`#not-eq?` predicate 組合），且是各家編輯器語法功能的共同底座。學讀碼**兩個都值得會**：ast-grep 當手邊快刀，tree-sitter query 當精密工具。

## 對比與取捨

| 面向 | grep / ripgrep | tree-sitter query | ast-grep | clangd (LSP，對照) |
|---|---|---|---|---|
| 看得懂結構 | 否（純文字） | **是**（AST） | 是（tree-sitter 底層） | 是（+ 語意/型別） |
| 被空白/換行/格式騙 | 會 | **不會** | 不會 | 不會 |
| 要能編譯 | 否 | 否 | 否 | **是** |
| 容錯（parse 壞 code） | N/A | **是** | 是 | 差 |
| 學習曲線 | 低 | 中高（S-expr） | 低（像 code） | 低（點就跳） |
| 跨語言統一 | 是 | 是（grammar） | 是 | 否（換 server） |
| 懂語意/作用域 | 否 | **否** | 否 | 是 |
| 適合 | 找任意文字、log | 精準結構查詢、審計 | 日常找形狀、批改 | 精準跳轉、重構 |

**心法**：文字層問題（找 log 訊息、跨語言掃關鍵字）→ ripgrep。結構層問題（「所有真正的 X 呼叫」「所有符合某形狀的函式」）→ tree-sitter / ast-grep。語意層問題（「這個 `read` 是哪個 `read`」）→ clangd。三層各有其位，tree-sitter 補的正是 ripgrep 與 clangd 之間那塊「懂結構但不用編譯」的空缺。

## 踩雷集錦

1. **`Incompatible language version N` 以為 grammar 壞了**。錯誤直覺：「clone 錯版本 / 工具有 bug」。正確認識：grammar 預生成的 parser ABI 比你的 CLI 新（或舊）。用自己的 CLI `tree-sitter generate --abi <你支援的max>` 重生成。這是第一個必踩坑，本章實跑就中了。

2. **忘了設 config，parse 直接報「no parser directories」**。tree-sitter CLI 要 `~/.config/tree-sitter/config.json` 指出 grammar 在哪。`tree-sitter init-config` 產生後，把 grammar 放進它列的目錄（預設含 `~/github`）。

3. **query 寫錯欄位名，靜默回空**。你以為函式名的欄位是 `name:`，其實 C grammar 裡是 `declarator:` 巢狀 `function_declarator > declarator: identifier`。**查詢回空優先懷疑 query 寫錯，不是「沒東西」**。除錯法：先 `tree-sitter parse` 看真實樹，照抄節點種類與欄位名。

4. **把 tree-sitter 當語意工具用**。它**只懂語法不懂語意**：兩個同名 local 變數它分不出作用域、`typedef` 後的型別它不追。要「這個 `x` 是哪個 `x`」是 clangd 的活。tree-sitter 回答的是「結構上長這樣的節點」，不是「語意上是同一個東西」。

5. **grammar 版本與語言方言不合**。C grammar 對某些 GNU 擴充、奇怪巨集展開前的片段會 parse 出 `ERROR` 節點。容錯是它的特性不是保證正確——遇到大量 `ERROR` 節點時，你的 query 可能漏 match。查詢重要結果時，抽樣人工核對。

## 進階：再往深一層

- **`ERROR` 與 `MISSING` 節點**：容錯 parse 時，tree-sitter 用這兩種節點標記它修不好的地方。寫審計 query 時可以**主動查 `(ERROR)`** 找出 parser 卡住的區域——那些往往是巨集地獄或非標準語法，讀碼時本來就值得留意。

- **query 的 predicate 家族**：除了 `#match?`（regex）、`#eq?`/`#not-eq?`（字串相等），還有 `#any-of?`、以及各語言可自訂的 predicate。多個 pattern 可放同一個 `.scm` 檔，tree-sitter 一次跑完全部。這讓你能建一份「這個 codebase 的所有危險 pattern」query 檔，一鍵掃全庫。

- **編輯器裡的 tree-sitter**：Neovim 的 `nvim-treesitter` 用同一套 query 做語法高亮（`highlights.scm`）、縮排、textobject（「選中整個函式」`af`）、structural navigation。你在本章學的 S-expression query 語法，就是這些功能的設定語言——會寫 query 就能自訂高亮與導航。

- **增量的實際意義**：tree-sitter runtime 的 `ts_tree_edit` + reparse 只重算改動子樹，複雜度接近 O(改動大小) 而非 O(檔案大小)。這是它能嵌進編輯器逐鍵更新的技術核心；對批次讀碼工具，意味著對同一大檔跑很多 query 很便宜。

- **跨語言審計**：因為 query 語法統一，你可以對一個 polyglot 專案（C 後端 + Python 腳本 + JS 前端）用**同一套心法**分別寫 query 找各語言的危險 pattern。ast-grep 的 `sg scan` 更把這包成規則檔（YAML），適合放進 CI 當 lint。

## 動手練習

1. **把 grammar 跑起來**：照本章步驟 clone tree-sitter-c、`init-config`、`generate --abi 14`，對 `src/ae.c` 跑 `tree-sitter parse`。如果你的 CLI 版本不同，記下你遇到的 ABI 數字並用對應 `--abi` 值。

2. **看樹寫 query**：`tree-sitter parse src/dict.c | less`，找出一個 `if` 敘述在樹上的節點種類（提示：`if_statement`）與它的 `condition:` / `consequence:` 欄位，然後寫 query 抓「所有 if 的條件表達式」。

3. **危險 API 審計**：寫一個 query 抓 redis 裡所有對 `strcpy` / `strcat` / `sprintf` 的**真實呼叫**（用 `#match?` 或多 pattern），跑在 `src/` 幾個檔上。跟 `rg 'strcpy'` 的結果比對，數數 grep 多出幾個假陽性（註解/字串/名字含子字串）。

4. **多行註解**：找一個含跨行 `/* ... */` 註解的檔，用 `(comment) @c` query 確認 tree-sitter 把它當單一節點，對照 `rg 'TODO'` 對多行的無力。

5. **（選）裝 ast-grep**：`cargo install ast-grep`，用 `sg -p 'zmalloc($$$)' -l c src/` 重做練習 3，體會「寫 pattern 像寫 code」比 S-expression 直覺多少。

## 本章重點整理

- regex 把 code 當一維字元流，會被註解/字串/子字串騙；正確抽象是 **AST**——註解、字串、呼叫在樹上是**不同種類的節點**，天生分得開。
- **tree-sitter** = 增量 + 容錯 + 跨語言的 parser：不用能編譯、能 parse 壞 code、學一套語法跨語言用。它懂**語法**不懂**語意**。
- 實跑設定兩個坑：**要 `init-config`**、**ABI 版本不合就 `tree-sitter generate --abi 14` 重生成**（本章實測踩中 version 15 vs max 14）。
- **S-expression query** 心法：先 `parse` 看樹 → 找出目標在樹上的路徑與欄位名 → 照著寫 pattern + `@capture`，可加 `#match?`/`#eq?` predicate 疊文字過濾。
- 決定性對照：同一個 `zmalloc`，grep 5 命中（4 噪音）、tree-sitter 精準 1。安全審計時這個精準度直接省掉大量人工複核。
- **ast-grep** 是更好上手的上層（pattern 像 code、`$$$` 當洞），適合日常找形狀/批改；tree-sitter query 更底層更精細。兩者都值得會。

## 自我檢核

- [ ] 能說出「regex 讀 code 為什麼會有假陽性」的根本原因（維度不對）嗎？
- [ ] tree-sitter 的三大特點（增量、容錯、多語言）各自對讀碼有什麼實際意義？
- [ ] 遇到 `Incompatible language version` 你知道是什麼、怎麼修嗎？
- [ ] 給你一段 C，你能描述「先 parse 看樹、再照路徑寫 query」的流程嗎？
- [ ] tree-sitter 懂語法但不懂語意——舉一個它答不出、非得 clangd 的問題。
- [ ] ast-grep 相對 tree-sitter query 的取捨是什麼？各自何時用？

結構化查詢讓你能精準問「哪裡符合某形狀」。但讀碼還有一種更省力的線索來源：**別人的工具已經幫你標出可疑處了**。下一章我們把靜態分析器的警告當成讀碼導航——編譯器與 linter 標紅的地方，往往正是你該優先看的地方。

→ [Ch 16 靜態分析輔助讀碼](./16-static-analysis-reading.md)
