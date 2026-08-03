# Ch 16 — treesitter 查詢：InspectTree 與結構搜尋

> **目標**：把 treesitter 這棵樹拿來做**結構化搜尋**——不用 grep 靠「這串字長怎樣」猜，直接對樹提問：「給我所有函式定義的名字」「所有 `static` 函式」「所有呼叫某函式的地方」。你會學 treesitter query 的 S-expression 語法（`(function_definition) @func`）、用 `:InspectTree` 看清節點型別再照著寫 query、用 Neovim **內建**的 `vim.treesitter.query` API 真跑查詢。這章是 `reading_code` Ch 15（用 tree-sitter CLI 做結構查詢）在 Neovim 裡的鏡像與延伸——同一套心法，這次在你的讀碼機器內。

> **環境**：Neovim v0.12.4，WSL2 / Ubuntu。三個 query（函式定義、指定函式的呼叫點、static 函式）在測試檔與真實的 Lua 原始碼（`lparser.c`）上 headless 真跑，輸出如實貼出。

## 為什麼需要這個？

Part 2 給了你 telescope 與 ripgrep（Ch 9–11）——強在通用與速度，但它們**只看得到文字**。你在讀 redis / Lua 這種大 C 專案，想找「所有真正呼叫 `zmalloc` 的地方」，`rg 'zmalloc'` 會給你一堆噪音：

- 註解裡的 `/* never call zmalloc without checking */`
- 字串常數 `"call zmalloc to allocate"`
- 名字含 `zmalloc` 的別的東西 `zmalloc_used_memory`
- 真正的呼叫 `p = zmalloc(16)`

grep 分不出這四種——因為它的世界觀是「一維字元流」，而「呼叫」是**結構**概念。你可以用更精巧的 regex 逼近（`zmalloc\s*\(`），但永遠有邊界：跨行、巨集、`zmalloc /* 註解 */ (` 這種奇葩寫法，regex 遲早破功。

treesitter query 換個問法：不問「哪裡出現 `zmalloc` 這串字」，問「哪個 `call_expression` 節點的 `function` 欄位是 `zmalloc`」。註解是 `comment` 節點、字串是 `string_literal` 節點、`zmalloc_used_memory` 是別的 `identifier`——它們在樹上是**不同種類的節點**，天生和真正的呼叫分得開。這就是結構化搜尋：**問結構，不問文字。**

讀大 C 專案時你什麼時候用它：安全審計「所有真正呼叫 `strcpy`/`system`/`memcpy` 的地方」（精準度直接決定你要不要人工複核 100 個假陽性）；盤點「這個檔有哪些 static 函式（內部實作）vs 對外函式」；找「所有 `== NULL` 的 null 檢查」或「所有 if 的條件」這種 regex 根本表達不了的結構問題。

## 先建立直覺：query = 帶捕獲的樹形狀 pattern

treesitter 的查詢語言就是「一段部分的樹形狀，加上 `@捕獲名`」。你寫出你想找的節點長什麼樣，treesitter 在整棵樹裡找所有 match，`@name` 標記你要抓出來的節點。

```
   你想找：所有函式定義的名字

   query（S-expression pattern）：
   (function_definition
     declarator: (function_declarator
       declarator: (identifier) @func.name))
                                 ↑
                          抓這個節點，叫它 func.name

   treesitter 在樹上找所有符合這個形狀的地方，
   把每個匹配的 identifier 節點吐給你（含位置與文字）
```

三個要素：

- **`(節點種類 ...)`**：一對括號是一個節點，第一個 token 是節點種類（`function_definition`、`call_expression`）。
- **`field:` 具名欄位**：`declarator:`、`function:`、`condition:` 是 grammar 給子節點的角色名——靠它精準定位「函式名藏在哪個部分」。
- **`@捕獲名`**：標記你要抓出來的節點；一個 query 可以有多個捕獲。

**寫 query 的鐵律**：先 `:InspectTree` 看真實的樹，看清楚你要的東西在樹上的**路徑與欄位名**，再照抄著寫。憑記憶猜欄位名（以為函式名的欄位叫 `name:`，其實 C grammar 裡是巢狀的 `declarator:`）是新手 query 靜默回空的頭號原因。

## 用 `:InspectTree` 看清路徑再寫 query

Ch 13 介紹過 `:InspectTree`——它是寫 query 的前置步驟。開一個 C 檔，`:InspectTree`，游標移到一個函式名上，右邊樹視窗高亮對應節點，你就看到函式名在樹上的完整路徑：

```
(function_definition
  type: (primitive_type)                  ← int / void
  declarator: (function_declarator
    declarator: (identifier)              ← 函式名在這，路徑是 declarator > declarator
    parameters: (parameter_list ...)))
```

看懂這條路徑——函式名是 `function_definition > declarator: function_declarator > declarator: identifier`——你就能寫出精準抓函式名的 query。這是結構化查詢的核心心法：**parse 一個範例 → 看清目標在樹上的路徑 → 照路徑寫 query。**

Neovim 還有一個寫 query 的實驗場：`:EditQuery`（Neovim 0.10+ 內建的 live query editor，舊 nvim-treesitter 有 `:TSEditQuery`）。它開一個 query 編輯視窗，你打 query，符合的節點在原始碼**即時高亮**——邊寫邊看命中哪些，比盲寫再跑快得多。

## headless 真跑：三個實用 query

這門課驗證工具靠 headless 真跑。以下三個 query 用 Neovim **內建**的 `vim.treesitter.query`（不碰 nvim-treesitter 外掛，繞開 Ch 13/14 的 master 分支坑），跑在測試檔 `foo.c` 上。`foo.c` 有三個函式：`static int add`、`int mul`、`int main`，其中 `mul` 和 `main` 裡各呼叫一次 `add`。

### Query 1：所有函式定義的名字

```lua
local root = vim.treesitter.get_parser(0, "c"):parse()[1]:root()
local q = vim.treesitter.query.parse("c", [[
(function_definition
  declarator: (function_declarator
    declarator: (identifier) @func.name))
]])
for id, node in q:iter_captures(root, 0) do
  local sr = ({node:range()})[1]
  print(string.format("  @%s  line %d  %s",
    q.captures[id], sr+1, vim.treesitter.get_node_text(node, 0)))
end
```

真跑輸出：

```
== all function definitions ==
  @func.name  line 4  add
  @func.name  line 8  mul
  @func.name  line 16  main
```

一份**乾淨的函式清單**：沒有函式指標型別、沒有宣告、沒有註解裡提到的函式名混進來——因為 query 只 match `function_definition` 這種節點。這是「這個檔有哪些函式」的精準答案，比 `rg '^\w.*(' ` 這種 regex 近似準太多。

### Query 2：所有呼叫 `add()` 的地方（帶 predicate）

用 `#eq?` predicate 對捕獲做精確比對——這是 grep 做不乾淨的經典：

```lua
local q2 = vim.treesitter.query.parse("c", [[
(call_expression
  function: (identifier) @fn
  (#eq? @fn "add"))
]])
for id, node in q2:iter_captures(root, 0) do
  local sr, sc = node:range()
  print(string.format("  line %d col %d", sr+1, sc+1))
end
```

真跑輸出：

```
== calls to add() ==
  line 11 col 13
  line 17 col 13
```

兩個都是**真正的呼叫點**（`mul` 裡第 11 行、`main` 裡第 17 行）。`function: (identifier) @fn` 先限定「被呼叫的名字是個單純 identifier 的呼叫」，`#eq? @fn "add"` 再過濾名字剛好是 `add`。結構過濾（必須是 call）疊上文字過濾（名字符合）——這就是「結構 + 精確比對」的組合拳。把 `#eq?` 換成 `#match?` 可以用 regex 過濾一整族名字（如 `"^z(malloc|calloc|free)$"` 抓 zmalloc 家族）。

### Query 3：所有 `static` 函式

盤點「哪些是內部實作（static）、哪些是對外介面」，一個 query 搞定。`static` 在樹上是 `storage_class_specifier` 節點：

```lua
local q3 = vim.treesitter.query.parse("c", [[
(function_definition
  (storage_class_specifier) @sc
  declarator: (function_declarator
    declarator: (identifier) @name)
  (#eq? @sc "static"))
]])
for id, node in q3:iter_captures(root, 0) do
  if q3.captures[id] == "name" then
    print("  " .. vim.treesitter.get_node_text(node, 0))
  end
end
```

真跑輸出：

```
== static functions ==
  add
```

`foo.c` 三個函式只有 `add` 是 static，query 精準抓到。這個 query 有**兩個捕獲**（`@sc` 給 predicate 過濾用、`@name` 是我們要的名字），示範了「多捕獲 + predicate」的組合。

## 在真實大檔上跑：Lua 的 lparser.c

玩具檔證明語法對，真價值在大檔。把 Query 3（static 函式）跑在 Lua 直譯器的 `lparser.c`（2193 行的真實 C 檔）：

```lua
vim.cmd("edit lua/lparser.c")
-- 同 Query 3 的 static 函式 query
local count = 0
for id, node in q:iter_captures(root, 0) do
  if q.captures[id] == "name" then count = count + 1 ... end
end
print("lparser.c static functions: " .. count)
```

真跑輸出：

```
lparser.c static functions: 98
  line 68  error_expected
  line 74  errorlimit
  line 95  testnext
  line 107  check
  line 116  checknext
  line 130  check_match
```

`lparser.c` 有 **98 個 static 函式**——這一個 query 直接告訴你「這個檔有大量內部 helper」，是讀懂它的重要線索（它是個自包含的模組，大部分是內部函式）。這種盤點用 grep 做不乾淨（`static` 也出現在變數宣告、註解裡），treesitter query 精準到只給你 static **函式**。

## query 的 predicate 家族

query 裡 `#` 開頭的是 predicate，對捕獲做額外過濾：

| predicate | 作用 | 例 |
|---|---|---|
| `#eq? @x "str"` | 捕獲文字**等於** str | `(#eq? @fn "add")` |
| `#match? @x "regex"` | 捕獲文字**符合** regex | `(#match? @fn "^z(malloc|free)$")` |
| `#not-eq? @x "str"` | 捕獲文字**不等於** str | 排除某名字 |
| `#any-of? @x "a" "b"` | 捕獲文字是列舉之一 | `(#any-of? @fn "strcpy" "strcat" "sprintf")` |

`#any-of?` 是安全審計的利器：一個 query 抓「所有呼叫危險 API（`strcpy`/`strcat`/`sprintf`/`gets`）之一」的地方。結構過濾（必須是 call）＋列舉過濾，一次掃全檔。

## 對比：query vs telescope/grep

| 面向 | telescope / ripgrep（Part 2） | treesitter query（本章） | clangd（Part 4，對照） |
|---|---|---|---|
| 看得懂結構 | 否（純文字） | **是**（語法樹） | 是（+ 語意/型別） |
| 被註解/字串/子字串騙 | 會 | **不會** | 不會 |
| 要能編譯 | 否 | 否 | **是** |
| 「所有真正呼叫 X」 | 有假陽性 | 精準 | 精準（且跨檔） |
| 「這個 read 是哪個 read」 | 做不到 | **做不到**（只懂語法） | 做得到（懂語意） |
| 學習曲線 | 低 | 中（S-expr + 看樹） | 低（點就跳） |
| 範圍 | 跨整個專案（rg 掃檔案系統） | **當前 buffer 的樹**（要跨檔得自己迭代檔案） | 跨整個 workspace |

**心法**（延續 `reading_code` Ch 15）：文字層問題（找 log 訊息、跨語言掃關鍵字）→ ripgrep。結構層問題（「所有真正的 X 呼叫」「所有 static 函式」「所有某形狀的 code」）→ treesitter query。語意層問題（「這個符號跨檔的所有引用」「這個 `read` 指哪個」）→ clangd（Part 4）。三層各有其位。

一個重要限制：Neovim 內的 `vim.treesitter.query` 預設對**當前 buffer 的樹**跑。想「對整個專案跑一個 query」，你得自己迭代檔案（`vim.fs` 走目錄 + 逐檔 parse），或直接用 `reading_code` Ch 15 的 **tree-sitter CLI** / `ast-grep` 在 shell 裡對整個 `src/` 掃——CLI 天生是「對一堆檔跑 query」的工具。Neovim 內的 query 強在「對我正在讀的這個檔即時提結構問題」，跨檔大掃用 CLI 更順。

## 鍵位 / 命令

| 模式 | 命令 / 按鍵 | 作用 |
|---|---|---|
| `:` | `:InspectTree` | 看當前 buffer 的樹，寫 query 前先看清路徑 |
| `:` | `:Inspect` | 查游標下節點種類與高亮 group |
| `:` | `:EditQuery`（0.10+ 內建） | 開 live query 編輯器，邊寫 query 邊即時高亮命中 |
| Lua | `vim.treesitter.query.parse("c", [[...]])` | 解析一段 query 字串 |
| Lua | `q:iter_captures(root, bufnr)` | 迭代所有捕獲（回 capture id + node） |
| Lua | `vim.treesitter.get_node_text(node, bufnr)` | 取節點對應的原始碼文字 |

## 踩雷集錦

1. **query 寫錯欄位名，靜默回空**。以為函式名欄位叫 `name:`，其實 C grammar 是 `declarator:` 巢狀 `function_declarator > declarator: identifier`。**回空優先懷疑 query 寫錯，不是「沒東西」**。除錯：先 `:InspectTree` 看真實樹，照抄節點種類與欄位名，或用 `:EditQuery` 邊寫邊看。

2. **用 nvim-treesitter 外掛的 query API 撞 master 坑**。這章的 query 全用 Neovim **核心內建** `vim.treesitter.query`，不碰外掛——所以不吃 Ch 13/14 的 master 分支問題。若你去用外掛的 query 執行路徑，可能撞到 master 老舊。認明 `vim.treesitter.*` 是核心。

3. **以為 query 會跨整個專案**。`iter_captures` 只對你給的那個 tree（通常是當前 buffer）跑。要跨檔得自己迭代檔案，或用 CLI/ast-grep。別以為打一個 query 就掃了整個 repo。

4. **把 query 當語意工具**。它只懂**語法**：兩個同名 local 它分不出作用域、`typedef` 後的型別它不追。「這個 `x` 是哪個 `x`」「這個函式的所有 caller（跨檔）」是 clangd 的活。query 回答「結構上長這樣的節點」，不是「語意上是同一個東西」。

5. **大量 `ERROR` 節點導致 query 漏 match**。treesitter 容錯但不保證正確——巨集地獄、非標準語法會 parse 出 `ERROR` 節點，你的 query 在那些區域可能漏。查重要結果（安全審計）時抽樣人工核對；也可主動 query `(ERROR)` 找出 parser 卡住的區域。

## 進階：再往深一層

- **多 pattern 同一個 query**：一個 query 字串裡可放多個 `(...)` pattern，treesitter 一次全跑。你可以建一份「這個 codebase 的所有危險 pattern」query（危險 API 呼叫 + 可疑的 `if (x = f())` 賦值條件 + `strcpy` 家族…），一鍵掃全檔。

- **`(ERROR)` 當導航線索**：主動 query `(ERROR) @e` 找 parser 修不好的地方——那往往是巨集地獄或非標準語法，讀碼時本來就值得留意的複雜點。

- **query 就是編輯器功能的設定語言**：Ch 13 的高亮、Ch 14 的 textobject、Ch 15 的 fold，底層全是 query 檔（`highlights.scm`、`textobjects.scm`、`folds.scm`）。會寫 query，你就能**自訂**這些——例如加一條 highlight query 讓某類節點特別上色。你在本章學的 S-expression，是這整套的通用語言。

- **接回 `reading_code` Ch 15 的 CLI**：Neovim 內 query 適合「對正在讀的檔即時提問」；要對整個 `src/` 大掃、或放進 CI 當 lint，用 tree-sitter CLI（`tree-sitter query`）或 `ast-grep`（`sg -p 'zmalloc($$$)'`，pattern 像 code 更好上手）。同一套結構化思維，兩個場景兩個工具。

- **`vim.treesitter.query.get()` 用內建 query**：不想自己寫 query，可以直接取語言內建的 query（如 `vim.treesitter.query.get("c", "highlights")`）來看 nvim-treesitter 怎麼定義高亮——學別人的 query 是進步最快的路。

## 本章重點整理

- treesitter query = **帶 `@捕獲` 的樹形狀 pattern**：問「哪個節點長這樣」而非「哪裡出現這串字」，天生和註解/字串/子字串分得開。
- 寫 query 鐵律：**先 `:InspectTree`/`:EditQuery` 看真實樹的路徑與欄位名，再照抄**——憑記憶猜欄位是回空的頭號原因。
- 用 Neovim **核心內建** `vim.treesitter.query`（`parse` + `iter_captures` + `get_node_text`），**繞開 master 分支坑**。三個實跑 query：所有函式定義（`add`/`mul`/`main`）、指定函式的呼叫點（`add` 兩處）、static 函式（`foo.c` 一個、真實 `lparser.c` 98 個）。
- predicate 家族：`#eq?`/`#match?`/`#not-eq?`/`#any-of?`——結構過濾疊文字過濾；`#any-of?` 是危險 API 審計利器。
- 三層分工：文字→ripgrep，結構→treesitter query，語意→clangd。Neovim 內 query 對當前 buffer；跨檔大掃用 CLI/ast-grep。
- query 是高亮/textobject/fold 的共同設定語言——會寫 query 就能自訂這些。

## 自我檢核

- [ ] 我能說出 treesitter query 為什麼沒有 grep 的假陽性（問結構不問文字）
- [ ] 我知道寫 query 前要先 `:InspectTree` 看路徑，為什麼
- [ ] 我能寫出「抓所有函式定義名字」的 query 的大致形狀
- [ ] 我知道 `#eq?`/`#match?`/`#any-of?` 各做什麼、`#any-of?` 何時特別有用
- [ ] 我記得 Neovim 內 query 只對當前 buffer，跨檔大掃該用什麼
- [ ] 我能講清楚 treesitter query（語法）和 clangd（語意跨檔）的分工

## 延伸閱讀

### 官方文件（優先）

- **Neovim `:help treesitter-query`**
  - **讀哪裡**：整篇；S-expression 語法、predicate（`#eq?`/`#match?`/`#any-of?`）、`vim.treesitter.query.parse`/`iter_captures` 的官方定義——本章 query 的每一塊都在這裡
- **Neovim `:help :EditQuery` 與 `:help :InspectTree`**
  - **讀哪裡**：live query 編輯器與樹檢視器的用法；寫 query 的兩大實驗場

### 資源

- **[tree-sitter 官方 query 語法文件](https://tree-sitter.github.io/tree-sitter/using-parsers/queries)** — tree-sitter 專案
  - **讀哪裡**：Query Syntax 那頁；capture、field、predicate、量詞（`*`/`+`/`?`）的完整語法，比 Neovim `:help` 更全
- **`reading_code` Ch 15：tree-sitter 與結構化查詢**（本 repo）
  - **讀哪裡**：整章；同一套心法的 **CLI 版**（在 redis 上跑），含 `#match?` 抓 zmalloc 家族、多行註解、regex vs treesitter 的假陽性對照、以及 ast-grep。本章是它在 Neovim 內的鏡像，兩章互補
- **[nvim-treesitter 的 queries 目錄](https://github.com/nvim-treesitter/nvim-treesitter/tree/master/queries)** — 各語言的 `.scm` query 檔
  - **讀哪裡**：`queries/c/highlights.scm`、`textobjects.scm`；讀別人寫好的 query 是學 query 最快的路，也讓你懂 Ch 13–15 的功能底層長怎樣

Part 3 到此完整：你能看懂結構（高亮/樹）、依結構移動（textobject）、沿樹選取與定位（incremental selection / context / fold）、對樹提結構問題（query）。動手把這四章的操作在一個真專案上練成反射，就是接下來的練習 C。之後 Part 4 我們換一個維度——從「懂語法結構」升級到「懂語意」：clangd 能回答 treesitter 答不出的「這個符號跨檔的定義與所有引用在哪」。

→ [練習 C：treesitter 導航複雜檔](./practice-c-treesitter-navigation.md)
