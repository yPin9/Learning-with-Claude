# Ch 13 — treesitter 基礎與 master/main 分裂

> **目標**：把 Neovim 的語法高亮從「正規表示式猜色」升級成「語法感知上色」，並搞懂 treesitter 到底把你的原始碼變成了什麼——一棵可以查詢、可以導航的**具體語法樹**。你會裝 C parser、用 `:InspectTree` 直接看樹、真跑 `vim.treesitter.get_parser` 把一個 C 檔解析出 `translation_unit` 根節點。最重要的是：講清楚 nvim-treesitter 那個把無數人坑進去的 **master / main 分支分裂**——Ch 0 的 config 為什麼刻意寫 `branch = "master"`，這章給你完整交代。

> **環境**：Neovim v0.12.4，WSL2 / Ubuntu。本章的 parse 輸出、樹的走訪、節點型別分布，全部在此環境用隔離 XDG 目錄 headless 真跑出來。

## 為什麼需要這個？

打開一個大 C 檔，你的眼睛第一個依賴的是**顏色**。關鍵字一個色、字串一個色、註解一個色、函式名一個色——顏色是你在幾萬行裡定位的第一層雷達。問題是：Vim 傳統的語法高亮是用**正規表示式**硬湊出來的。

正規表示式高亮的世界觀是「一維字元流」（這點 `reading_code` Ch 15 講透了）。它靠一堆 `syntax match` / `syntax region` 規則去猜「這串字看起來像關鍵字」「這段看起來像字串」。猜，就會錯：

- 巢狀太深、跨行的結構它跟不上（`vim.syntax` 有 `syncing` 上限，捲太快高亮會崩）。
- 它分不清「`read` 是函式名還是變數名」——因為那是**結構**問題，不是「這串字長怎樣」問題。
- 新語言特性、raw string、複雜巨集，規則永遠追不完，高亮總有破綻。

treesitter 換了一套世界觀：**不猜，直接 parse**。它為 C（以及幾十種語言）內建一個真正的 parser，把你的原始碼解析成一棵**具體語法樹**（CST，concrete syntax tree）。高亮不再是「這串字像什麼」，而是「這個節點在樹上**是什麼**」——`identifier`、`string_literal`、`comment` 在樹上是天生不同種類的節點，上色自然分得開。這叫**語法感知高亮**（syntax-aware highlighting）。

讀碼的意義：更準的顏色，是你在陌生大檔裡少一分誤讀。而且這棵樹不只餵高亮——後面三章的依結構移動（Ch 14）、incremental selection 與 sticky context（Ch 15）、結構化查詢（Ch 16），全都是對這同一棵樹的操作。**這章是 Part 3 的地基：先把樹立起來，看得見。**

## 先建立直覺：CST 是什麼，跟 AST 差在哪

parser 把 token 流變成樹，這件事 `compiler_frontend` 已經教過。但 treesitter 給的是 **CST（具體語法樹）**，不是編譯器教科書那個精簡過的 **AST（抽象語法樹）**。差別值得先講清楚：

```
   原始碼                       treesitter 的 CST
   int add(int a) {            (function_definition
       return a;                 type: (primitive_type)         ← int
   }                            declarator: (function_declarator
                                  declarator: (identifier)       ← add
                                  parameters: (parameter_list
                                    (parameter_declaration ...)))
                                body: (compound_statement
                                  { ← 這個大括號在 CST 裡也是節點
                                  (return_statement
                                    (identifier))                ← a
                                  }))
```

- **AST**（抽象）會**丟掉**對語意沒用的東西：括號、分號、大括號常常不進樹，因為結構已經隱含了。
- **CST**（具體）**保留一切**：每個 `{`、`;`、`(` 在樹上都有對應節點，連空白的位置資訊都在。treesitter 給的就是 CST——因為它要拿來做高亮、縮排、選取，必須知道「這個大括號在原始碼第幾行第幾欄」。

對讀碼的你，CST 的好處是：**樹上的每個節點都能對回原始碼的精確範圍**（第幾行第幾欄到第幾行第幾欄）。這是「選中整個函式」「跳到下一個函式開頭」能做到的物理基礎——節點知道自己在哪。

## 底層機制：增量解析為什麼快、憑什麼能嵌進編輯器

treesitter 有兩個特性讓它能塞進編輯器、每次按鍵都即時更新而不卡：

**1. 增量解析（incremental parse）**。你改一行，treesitter **不重 parse 整個檔**。它拿舊的樹，只重算被你的編輯影響到的子樹，其餘子樹原封不動接回去。複雜度接近 `O(改動大小)` 而非 `O(檔案大小)`。這是它能在你每次敲鍵盤時即時更新高亮而不掉幀的技術核心——對一個五千行的檔改一個字元，它只動那一小塊。

```
   你改了第 42 行的一個字
        │
        ▼
   treesitter 標記「第 42 行的子樹髒了」
        │
        ▼
   只重 parse 那棵子樹（可能就一個 statement）
        │
        ▼
   接回舊樹 → 新樹好了，高亮更新
   （其餘 4999 行的樹完全沒動）
```

**2. 容錯解析（error-tolerant）**。真實編輯中的 code 常常語法不完整——你正打到一半、`#ifdef` 切掉半個函式、巨集展開前不合法。傳統編譯器 parser 遇到語法錯就整個罷工；treesitter 會盡量 parse，把修不好的地方標成 `ERROR` 節點，其餘照常給你樹。所以你打字打到一半，高亮不會整片崩掉。

這兩點合起來，就是為什麼 Neovim、Zed、GitHub 的語法高亮底層全是 treesitter。

## 裝 C parser 並看它動起來

Ch 0 的 config 骨架已經宣告了 treesitter，`ensure_installed = { "c", "lua" }` 會在第一次 `:Lazy sync` 時自動編譯 C parser。手動裝別的語言用 `:TSInstall`：

```vim
:TSInstall c
:TSInstall lua python cpp
```

`:TSInstall c` 做的事：抓 C grammar、用你機器的 C compiler **編譯出一個 parser（`.so`）**、放進 nvim 的 parser 目錄。注意「要編譯」這件事——所以第一次裝 parser 需要機器上有 C compiler（`gcc`/`cc`），沒有的話 `:TSInstall` 會失敗。查裝了哪些 parser：

```vim
:TSInstall info      " 列出所有可裝的語言與已裝狀態
```

裝好之後，開一個 `.c` 檔，高亮立刻變成語法感知的。真正想「看見那棵樹」，用內建命令 `:InspectTree`（Neovim 0.9+ 內建，舊名 `:TSPlaygroundToggle`）：

```vim
:InspectTree
```

它會開一個分割視窗，顯示當前 buffer 的完整 S-expression 語法樹，游標在原始碼移動時，樹視窗會**高亮對應的節點**。這是你這章最該養成的習慣：**看不懂某段 code 的結構、或想寫 query（Ch 16）之前，先 `:InspectTree` 把樹叫出來看清楚。**

另一個實用命令，游標停在某個 token 上，問「我現在踩在哪種節點上」：

```vim
:Inspect
```

它會告訴你游標下的字元屬於哪個 treesitter 節點、套了哪個高亮 group。「這個字為什麼是這個顏色」的除錯就靠它。

## headless 真跑：把樹解析出來走一遍

這門課驗證工具的方式是 headless 真跑。下面這段（存成 `check.lua`，用隔離 XDG 目錄跑）證明 treesitter 真的把一個 C 檔解析成樹，並走訪根節點的每個 top-level 子節點。測試檔 `foo.c` 開頭是這樣：

```c
#include <stdio.h>

/* a demo file for treesitter */
static int add(int a, int b) {
    return a + b;
}

int mul(int x, int y) { ... }
int main(void) { ... }
```

驗證腳本：

```lua
vim.cmd("edit /tmp/nvim_p3/foo.c")
local ok, parser = pcall(vim.treesitter.get_parser, 0, "c")
print("parser ok: " .. tostring(ok and parser ~= nil))
local root = parser:parse()[1]:root()
print("root type: " .. root:type())
print("child count: " .. root:child_count())
for i = 0, root:child_count()-1 do
  local c = root:child(i)
  local sr = ({c:range()})[1]
  print(string.format("  [%d] %-22s line %d", i, c:type(), sr+1))
end
```

真跑輸出：

```
parser ok: true
root type: translation_unit
child count: 5
-- top-level children --
  [0] preproc_include        line 1
  [1] comment                line 3
  [2] function_definition    line 4
  [3] function_definition    line 8
  [4] function_definition    line 16
```

這證明了幾件事：treesitter 把 C 解析成樹，根節點是 `translation_unit`（整個 C 檔的頂層，對應 `reading_code` Ch 15 看到的一樣）；它的五個 top-level 子節點清清楚楚——一個 `preproc_include`（`#include`）、一個 `comment`（那段版權註解）、三個 `function_definition`（`add`/`mul`/`main`），而且每個都標了在原始碼的**行號**。這棵樹就是後面三章所有操作的對象。

## master vs main：這門課最重要的一個坑

現在講 Ch 0 埋的伏筆。你在 config 骨架裡看到這行、還被特別註明「不是筆誤」：

```lua
{ "nvim-treesitter/nvim-treesitter", branch = "master", ... }
```

**背景**：`nvim-treesitter` 這個外掛（幫你裝 parser、配高亮/縮排/textobject 的那個）在 2024 年做了一次大重寫。他們把**經典的 `master` 分支封存（frozen）**，開發搬到**完全重寫的 `main` 分支**。兩個分支的設定 API **互不相容**：

| | `master`（經典、封存） | `main`（重寫、當前開發） |
|---|---|---|
| 設定入口 | `require("nvim-treesitter.configs").setup{...}` | `require("nvim-treesitter").setup{...}`（不同簽名） |
| `ensure_installed` | 在 `.configs` 的 opts 裡 | 改用 `:TSInstall` / 不同機制 |
| highlight/incremental_selection/textobjects 等模組 | **內建在 configs 裡**，一份 opts 全配 | **拿掉了**，改成你自己接 Neovim 內建 API 或裝獨立外掛 |
| 相容的 Neovim | 0.9+ | 需要較新的 Neovim |

**災難點**：`main` 分支**拿掉了** `require("nvim-treesitter.configs").setup()` 這個所有現存教材、kickstart、你 google 到的九成 config 都在用的入口。所以如果你不寫 `branch = "master"`，lazy.nvim 預設會抓到 `main`（因為 remote 的 `HEAD -> main`），你的 config 在 `require("nvim-treesitter.configs").setup(opts)` 那行直接 crash——`.configs` 這個 module 在 main 根本不存在。這正是 Ch 0 踩雷 3 的現場。

驗證這不是嚇你：直接看兩個分支的 remote HEAD 指向。clone 下來預設 checkout 的就是 `main`：

```
$ git -C nvim-treesitter-textobjects branch -a
* (HEAD detached at 898ee30)
  ...
  remotes/origin/HEAD -> origin/main      ← 預設就是 main
  remotes/origin/master
```

**這門課的選擇**：釘 `master`。理由：（1）`master` 的一份 `configs.setup{}` 就把 highlight、incremental_selection、textobjects 全配好，最省事、最符合這門課「一份 config 逐 Part 累加」的節奏；（2）你 google 到的絕大多數範例都是 master API，釘 master 你抄得動；（3）Ch 14 要用的 textobjects 模組在 master 裡是內建的，main 得另外接。**代價**：master 已封存，不再跟進 Neovim 最新的 API 變動——Ch 14 你會親眼看到這代價（master 的某些 textobjects 操作在 Neovim 0.12 上會踩到已移除的節點方法）。這是一個**知情的取捨**，不是無腦選預設。

> 什麼時候該用 main？如果你願意自己把每個功能接到 Neovim 內建 treesitter API（`vim.treesitter.*`）、想要長期維護、且不依賴 master 那套內建模組——main 是未來。但那是另一條路，且教材稀少。這門課的目標是「精通讀碼操作」而非「追外掛前沿」，所以走 master 這條踩得穩、教材多的路，並在踩到 master 老舊之處時**誠實標出、給你繞法**。

## 鍵位表 / 命令

treesitter 基礎這章大多是命令（`:` 開頭），還不是靠鍵位操作（那從 Ch 14 開始）。

| 模式 | 命令 / 按鍵 | 作用 |
|---|---|---|
| `:` | `:TSInstall c` | 裝並編譯 C 的 parser |
| `:` | `:TSInstall info` | 列出所有語言與已裝狀態 |
| `:` | `:TSUpdate` | 更新已裝的 parser |
| `:` | `:InspectTree` | 開分割視窗看當前 buffer 的語法樹（游標同步高亮節點） |
| `:` | `:Inspect` | 查游標下字元屬於哪個節點、套哪個高亮 group |
| `:` | `:TSBufToggle highlight` | 開/關當前 buffer 的 treesitter 高亮（除錯用） |
| Lua | `vim.treesitter.get_parser(0, "c")` | 取得當前 buffer 的 C parser（寫 query/腳本用） |

## 對比與取捨

| 面向 | 正規表示式高亮（傳統 syntax） | treesitter 高亮 |
|---|---|---|
| 世界觀 | 一維字元流，靠規則猜 | 語法樹，直接 parse |
| 準度 | 巢狀/跨行/複雜巨集會破 | 語法感知，準得多 |
| 大檔捲動 | 有 sync 上限，捲快會崩 | 增量解析，不崩 |
| 分得清 ident vs keyword | 靠 pattern，有邊界 | 節點種類天生分開 |
| 額外能力 | 只有上色 | 同一棵樹餵導航/選取/query/fold |
| 代價 | 內建、零依賴 | parser 要編譯、要裝、master/main 有坑 |

treesitter 不是「潮」，是**把高亮建在正確的抽象上**。唯一的代價是 parser 要編譯、以及這章講的分支坑——都是一次性的。

## 踩雷集錦

1. **不寫 `branch = "master"`，config 在啟動時 crash**。抓到 `main` 分支，`require("nvim-treesitter.configs").setup()` 報 module 不存在。這是這門課最常見的坑，Ch 0 踩雷 3 的現場。釘 master，或全面改用 main 的新 API（教材稀少）。

2. **`:TSInstall c` 失敗，說找不到 compiler**。裝 parser 要**編譯 C**，機器上得有 `gcc`/`cc`。跑 `:checkhealth nvim-treesitter` 看它抱怨缺什麼。WSL/Ubuntu 上 `apt install build-essential` 補齊。

3. **以為高亮沒生效**。開了 `.c` 檔顏色卻沒變？先 `:InspectTree` 看樹有沒有出來（沒出來＝parser 沒裝好），再 `:Inspect` 看游標下有沒有套到高亮 group。也可能是你的 colorscheme 沒定義 treesitter 的高亮 group（`@function`、`@variable` 這些），換一個支援 treesitter 的 colorscheme。

4. **把 CST 當成語意分析**。treesitter 只懂**語法**不懂**語意**：兩個同名 local 變數它分不出作用域、`typedef` 後的型別它不追。「這個 `x` 是哪個 `x`」是 clangd（Part 4）的活，不是 treesitter 的。

5. **parser 太新／太舊的 ABI 坑（CLI 情境）**。如果你在 nvim **外面**用 tree-sitter CLI（像 `reading_code` Ch 15 那樣），會遇到 `Incompatible language version`——grammar 預生成的 parser ABI 和 CLI 對不上。nvim 內用 `:TSInstall` 自己編譯就避開這坑，但知道有這回事，跨到 CLI 時不慌。

## 進階：再往深一層

- **`:InspectTree` 的 `o` / `i` 切換**：在 InspectTree 視窗裡按 `o` 可切換顯示語言（多語言注入時有用，如 markdown 裡的 code block）、按 `i` 顯示匿名節點（`{`、`;` 這種平常隱藏的）。想看完整 CST（含標點節點）就按 `i`。

- **多語言注入（injection）**：treesitter 能在一種語言裡「注入」另一種——例如 C 字串裡的 SQL、Lua 裡 `vim.cmd([[...]])` 的 Vimscript。`:InspectTree` 看得到注入的子樹。讀混語言的 code 時這很有用。

- **`vim.treesitter` 是 Neovim 內建，`nvim-treesitter` 是外掛**：別搞混。`vim.treesitter.*`（`get_parser`、`query.parse`、`foldexpr`）是 Neovim **核心內建**的 treesitter API，永遠在、不挑分支。`nvim-treesitter` 那個外掛只是幫你**裝 parser + 配模組**的方便層——master/main 的坑是外掛的坑，不是核心的坑。Ch 16 的 query 我們就直接用核心 `vim.treesitter.query`，繞開外掛分支問題。

- **parser 放哪**：`:TSInstall` 編出的 `.so` 放在 nvim 的 `parser/` runtime 目錄下。`:checkhealth nvim-treesitter` 會列出每個語言 parser 的路徑與 ABI 版本，config 出問題第一個看它。

## 本章重點整理

- treesitter 把原始碼 parse 成**具體語法樹（CST）**，用來做**語法感知高亮**——比正規表示式猜色準，且同一棵樹還餵導航/選取/query/fold。
- CST 保留一切（含標點、位置），每個節點都能對回原始碼精確範圍——這是後面「選中整個函式」「跳下一個函式」的物理基礎。
- treesitter 快在**增量解析**（改一行只重算子樹）、穩在**容錯**（壞 code 也 parse，錯處標 `ERROR`）。
- `:TSInstall` 裝並**編譯** parser、`:InspectTree` 看樹、`:Inspect` 查游標下節點——這三個是你的基本工具。
- **master/main 分裂**是這門課最重要的坑：`nvim-treesitter` 把 `master` 封存、開發搬到重寫的 `main`，兩者 API 不相容，`main` 拿掉了 `require("nvim-treesitter.configs").setup()`。這門課釘 `master`（教材多、一份 config 配好一切），代價是老舊、Ch 14 會踩到。

## 自我檢核

- [ ] 我能說出「語法感知高亮」比「正規表示式高亮」準在哪、為什麼
- [ ] 我知道 CST 和 AST 差在哪，以及為什麼 treesitter 給的是 CST
- [ ] 我能解釋增量解析為什麼讓 treesitter 能嵌進編輯器逐鍵更新
- [ ] 我能講清楚 master vs main 分裂：差在哪、這門課為什麼釘 master、代價是什麼
- [ ] 我知道 `vim.treesitter`（內建）和 `nvim-treesitter`（外掛）是兩回事，分支坑是外掛的坑
- [ ] 我能用 `:InspectTree` 看一個 C 檔的樹、用 `:Inspect` 查游標下節點

## 延伸閱讀

### 官方文件（優先）

- **Neovim `:help treesitter`**
  - **讀哪裡**：開頭的 overview 與 `vim.treesitter.get_parser`、`vim.treesitter.query`；這是核心內建 API，不挑外掛分支，Ch 16 會反覆回來
  - **注意**：treesitter API 在 0.9→0.12 有演進，以你這版的 `:help` 為準
- **Neovim `:help :InspectTree` 與 `:help :Inspect`**
  - **讀哪裡**：兩個命令的用法與視窗內按鍵（`o` 切語言、`i` 顯示匿名節點）；養成「看不懂結構先 InspectTree」的習慣

### 外掛 / 背景

- **[nvim-treesitter README](https://github.com/nvim-treesitter/nvim-treesitter)** — 官方倉庫
  - **讀哪裡**：README 頂端關於 `main` vs `master` 的說明（他們有明確講分支狀態）；對照本章講的分裂
  - **前提**：看得懂 lazy.nvim 的 plugin spec（Ch 0 講過）
- **[tree-sitter 官方文件](https://tree-sitter.github.io/tree-sitter/)** — treesitter 專案本身
  - **讀哪裡**：Introduction 講增量/容錯的設計目標；想懂「憑什麼快」看這裡
- **`reading_code` Ch 15：tree-sitter 與結構化查詢**（本 repo）
  - **讀哪裡**：整章；那章用 tree-sitter **CLI** 在 redis 上做結構查詢，是本章的 CLI 版鏡像。本課 Ch 16 是它在 Neovim 內的延伸

樹立起來、看得見了。下一章我們開始**用**這棵樹——把 Part 1 手動的 text object（`%`、`}`、`ci(`）升級成依結構移動：一鍵選中整個函式、在函式之間精準跳。

→ [Ch 14 treesitter textobjects：依結構移動](./14-treesitter-textobjects.md)
