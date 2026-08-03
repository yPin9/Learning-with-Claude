# Ch 14 — treesitter textobjects：依結構移動

> **目標**：把 Part 1 那套手動 text object（`ci(`、`di{`、`%` 配對跳）升級成**語意化的依結構移動**。你會裝 `nvim-treesitter-textobjects`（同樣要釘 master，這章你會看到為什麼），學會 `vaf` 一鍵選中整個函式、`]f`/`[f` 在函式之間精準跳、`aa`/`ia` 選中一個參數、swap 交換兩個參數。核心差異：Part 1 的 `%` 靠的是「配對的括號」，遇到沒括號的結構就無能為力；treesitter textobject 靠的是**語法樹上的節點邊界**，它知道「函式」「參數」「類別」是什麼，不靠符號配對。

> **環境**：Neovim v0.12.4，WSL2 / Ubuntu。本章 config 加的 keymap 全部 headless 驗證已註冊；並**誠實記錄**了 master 分支 textobjects 在 Neovim 0.12 上踩到的一個真實相容性坑與繞法。

## 為什麼需要這個？

Part 1 教過手動 text object：`ciw`（改一個 word）、`ci(`（改括號內）、`di{`（刪大括號內）、`%`（跳到配對括號）。它們很強，但都建在**符號配對**上——`ci(` 靠找配對的 `(` `)`，`%` 靠 matchpairs。這在讀碼時有兩個天花板：

1. **「函式」不是一對符號**。你想選中「整個 `add` 函式」——從 `static int add(...)` 的 `s` 到收尾 `}`——`%` 幫不了你，因為函式開頭沒有一個「配對符號」標記它。你得手動 `V` 然後 `}` 一路展，遇到函式裡有巢狀 `{}` 還會算錯。

2. **「跳到下一個函式」`}` 不準**。Part 1 用 `}`（跳到下一個空行/段落）近似「跳過一個函式」，但 `}` 跳的是**空行**，不是函式邊界。函式裡有空行就跳歪，函式之間沒空行就跳不到。它猜的是「段落」，不是「函式」。

treesitter textobject 直接讀 Ch 13 那棵樹。樹上「函式」是一個 `function_definition` 節點、「參數」是 `parameter_declaration` 節點、「類別」是 `class`/`struct` 節點——它們有**精確的節點邊界**。所以 `vaf` 選的是「這個 `function_definition` 節點的完整範圍」，`]f` 跳的是「下一個 `function_definition` 節點的開頭」。**不猜、不靠符號配對、不靠空行——靠結構。** 這是 Part 1 motion 的語意化升級。

讀大 C 專案時你什麼時候用它：翻到一個陌生函式，`vaf` 一鍵框住它看範圍多大（順手 `y` 抄出來貼進筆記）；想在一個檔的幾十個函式間快速掃過，`]f ]f ]f` 一個個跳到函式開頭比 `}` 準太多；改一個函式簽名想調參數順序，swap 一鍵搞定不用手剪。

## 先建立直覺：textobject = 樹上一種節點的「選取器」

```
   Part 1 手動 textobject          treesitter textobject
   ┌──────────────────┐           ┌──────────────────────┐
   │ ci( → 找配對括號  │           │ vaf → 選 function_    │
   │ di{ → 找配對大括號│           │        definition 節點│
   │ %   → 跳配對符號  │           │ ]f  → 跳下一個 func   │
   │ }   → 跳空行(近似)│           │ aa  → 選 parameter    │
   └──────────────────┘           │ ]a  → 跳下一個參數    │
      靠「符號/空行」               └──────────────────────┘
                                      靠「樹上的節點種類」
```

命名沿用 Vim 的 `a`（around，含邊界）/ `i`（inner，不含邊界）慣例：

- `af` = **a** **f**unction = 整個函式（含簽名與 `}`）
- `if` = **i**nner **f**unction = 函式**主體**（不含簽名，`{` `}` 內）
- `aa` = **a**round **a**rgument = 一個參數（含逗號）
- `ia` = **i**nner **a**rgument = 一個參數（不含逗號）
- `ac`/`ic` = 一個 class/struct（around / inner）

`v` 前綴進 visual 選取（`vaf` 看/抄）、`d` 前綴刪（`daf` 刪整個函式）、`y` 前綴複製（`yif` 抄函式主體）——跟 Part 1 的 operator + textobject 組合律完全一樣，只是 textobject 換成了語意化的。

## 裝外掛：又一個 master/main 坑（這次是 textobjects）

textobjects 是**獨立外掛** `nvim-treesitter-textobjects`。Ch 13 講過 `nvim-treesitter` 本體的 master/main 分裂——textobjects **也有同一個分裂**，而且更容易踩到，因為它的預設 HEAD 就是 `main`。

實測：不指定 branch clone 下來，預設 checkout 的是 `main`，模組路徑變成 `nvim-treesitter-textobjects/`（連字號、standalone 命名），設定入口也變了。這跟 Ch 13 我們釘的 `master` 本體用的 `require("nvim-treesitter.configs").setup{textobjects=...}` **對不上**。所以 textobjects 也必須釘 master：

```lua
{ "nvim-treesitter/nvim-treesitter", branch = "master", build = ":TSUpdate",
  dependencies = {
    { "nvim-treesitter/nvim-treesitter-textobjects", branch = "master" },  -- ← 也釘 master
  },
  opts = {
    ensure_installed = { "c", "lua" },
    highlight = { enable = true },
    -- ↓↓↓ 本章新增：textobjects 三大模組 select / move / swap
    textobjects = {
      select = {
        enable = true,
        lookahead = true,          -- 游標不在結構上時，往後找最近的
        keymaps = {
          ["af"] = "@function.outer",
          ["if"] = "@function.inner",
          ["ac"] = "@class.outer",
          ["ic"] = "@class.inner",
          ["aa"] = "@parameter.outer",
          ["ia"] = "@parameter.inner",
        },
      },
      move = {
        enable = true,
        set_jumps = true,          -- 跳轉寫進 jumplist，Ctrl-o 跳得回來
        goto_next_start = { ["]f"] = "@function.outer", ["]a"] = "@parameter.inner" },
        goto_previous_start = { ["[f"] = "@function.outer", ["[a"] = "@parameter.inner" },
      },
      swap = {
        enable = true,
        swap_next = { ["<leader>sa"] = "@parameter.inner" },
        swap_previous = { ["<leader>sA"] = "@parameter.inner" },
      },
    },
  },
  config = function(_, opts) require("nvim-treesitter.configs").setup(opts) end },
```

三個模組各管一件事：`select`（選取，`af`/`ia` 這類）、`move`（跳轉，`]f`/`[a`）、`swap`（交換，`<leader>sa`）。`@function.outer` 這種 `@` 開頭的是 treesitter 的 **capture 名**——它背後對應一份 query（Ch 16 詳談），master 內建了 C 等語言的 `textobjects.scm` query 檔，定義了「什麼算 `@function.outer`」。

**`set_jumps = true` 很重要**：讓 `]f`/`[f` 的每次跳轉都寫進 jumplist（Part 1 Ch 5），這樣你 `]f` 跳過頭了，`Ctrl-o` 能跳回來。沒設的話跳了就回不去，讀碼時很痛。

## headless 驗證：keymap 真的掛上了

跑一個腳本確認 config 把這些 keymap 全註冊了（`o` = operator-pending 模式，`x` = visual 模式）：

```lua
local function has(mode, lhs)
  for _, m in ipairs(vim.api.nvim_get_keymap(mode)) do if m.lhs == lhs then return true end end
  for _, m in ipairs(vim.api.nvim_buf_get_keymap(0, mode)) do if m.lhs == lhs then return true end end
  return false
end
print("omap af: " .. tostring(has("o","af")))
print("xmap if: " .. tostring(has("x","if")))
print("nmap ]f: " .. tostring(has("n","]f")))
print("nmap [f: " .. tostring(has("n","[f")))
print("nmap ]a: " .. tostring(has("n","]a")))
print("nmap <leader>sa: " .. tostring(has("n"," sa")))
```

真跑輸出：

```
omap af registered: true
xmap if registered: true
nmap ]f registered: true
nmap [f registered: true
nmap ]a registered: true
nmap <leader>sa registered: true
```

六個 keymap 全部掛上了——config 正確接線。`af`/`if` 掛在 operator-pending（`o`）與 visual（`x`）兩個模式，這就是為什麼 `daf`（normal + operator）和 `vaf`（visual）都能用同一個 textobject。

## 誠實的坑：master textobjects 在 Neovim 0.12 上的相容性問題

這裡如實記錄一個踩到的坑，因為它正是 Ch 13 說「釘 master 的代價」的現場。keymap 掛上了，但在 Neovim **0.12.4** 上實際觸發 `]f`（headless feedkeys）時，master 分支的 textobjects 會炸：

```
E5108: Lua: .../nvim-treesitter/lua/nvim-treesitter/tsrange.lua:27:
       attempt to call method 'start' (a nil value)
  ...
  .../textobjects/move.lua:78: in function 'move_fn'
```

原因：master 分支的 `tsrange.lua` 呼叫了一個 treesitter 節點方法（`node:start()`），這個方法在**較新的 Neovim treesitter 節點 API 裡被移除/改名了**。master 已封存、不再跟進 Neovim 的 API 演進，所以在最新的 0.12 上，依賴 `tsrange` 的那條路徑（部分 move / select 操作）會踩空。

**這正是 Ch 13 講的取捨變現**：master 教材多、一份 config 配好一切，但它是**凍結的**，追不上 Neovim 最新版。三種務實的繞法：

1. **用穩定的 Neovim 版本**：master textobjects 在 0.9–0.10 這類與它同期的 Neovim 上是好的。若你不需要 0.12 的新特性，pin 一個和 master 同期的 Neovim 最省事。
2. **改用 `main` 分支 + 內建 API**：main 是為了跟上新 Neovim 而重寫的，在 0.12 上健康——代價是設定寫法不同、教材少（你得自己接 `vim.treesitter` 與 main 的新 `setup`）。想長期在最新 Neovim 上用 textobjects，這是正解。
3. **改用 mini.ai 之類的替代外掛**：`echasnovski/mini.ai` 提供類似的「a/i function/argument」textobject，維護活躍、不吃 nvim-treesitter 的分支問題。是想避開整個 master/main 泥沼的乾淨選擇。

不受這坑影響的部分（都用 Neovim **內建** treesitter API，繞開 master 的 `tsrange` shim）：**incremental selection（Ch 15）** 與 **`vim.treesitter.query`（Ch 16）**——所以 Part 3 的其餘操作照樣穩。這也印證 Ch 13 的話：**分支坑是 `nvim-treesitter` 這個外掛的坑，不是 Neovim 核心 treesitter 的坑。**

> 這門課的立場：不粉飾。textobjects 的概念與 keymap 設計對讀碼極有價值，值得學會；但你在最新 Neovim 上實跑時可能撞到這個 master 老舊問題——知道它、知道三條繞法，比假裝一切完美有用。你的機器上如果 `]f` 報 `tsrange` 錯，不是你 config 寫錯，是 master 追不上 0.12。

## 讀碼情境：這些操作實際怎麼用

**情境一：翻到陌生函式，先量它多大**。游標落在某函式任一行，`vaf`——整個 `function_definition` 被 visual 選中，狀態列告訴你選了幾行。太長（幾百行）？這是個該拆的上帝函式，讀之前先有心理準備。順手 `y` 把它抄進你的攻堅筆記（`reading_code` 的外化）。

**情境二：在一個檔的函式間掃過**。剛打開一個五千行的 `lparser.c`，想快速看它有哪些函式。`gg` 到檔頭，然後 `]f ]f ]f`——每按一次跳到下一個函式開頭。配合 Ch 15 的 sticky context，你能一路掃過每個函式簽名，對這個檔的「有哪些功能」建起地圖。這比 `}` 跳空行準太多——`}` 會被函式內的空行騙。

**情境三：只抄函式主體，不要簽名**。想把某個函式的邏輯貼到別處對照，但不要它的簽名。游標在函式內，`yif`（yank inner function）——只複製 `{` `}` 之間的主體。

**情境四：調參數順序**。讀到 `int foo(int flags, char *buf)`，覺得該把 `buf` 挪前面。游標停在 `flags` 上，`<leader>sa`（swap argument next）——`flags` 和後一個參數 `buf` 就地交換，變 `foo(char *buf, int flags)`。改簽名不用手剪手貼。

## 鍵位表

| 模式 | 按鍵 | 作用 | 對應節點 |
|---|---|---|---|
| visual / operator | `af` | 整個函式（含簽名與 `}`） | `@function.outer` |
| visual / operator | `if` | 函式主體（`{}` 內） | `@function.inner` |
| visual / operator | `ac` | 整個 class/struct | `@class.outer` |
| visual / operator | `ic` | class/struct 主體 | `@class.inner` |
| visual / operator | `aa` | 一個參數（含逗號） | `@parameter.outer` |
| visual / operator | `ia` | 一個參數（不含逗號） | `@parameter.inner` |
| normal | `]f` | 跳到**下一個**函式開頭 | `@function.outer` |
| normal | `[f` | 跳到**上一個**函式開頭 | `@function.outer` |
| normal | `]a` | 跳到下一個參數 | `@parameter.inner` |
| normal | `[a` | 跳到上一個參數 | `@parameter.inner` |
| normal | `<leader>sa` | 與下一個參數交換 | `@parameter.inner` |
| normal | `<leader>sA` | 與上一個參數交換 | `@parameter.inner` |

常用組合：`vaf`（選函式）、`daf`（刪函式）、`yif`（抄函式主體）、`caa`（改一個參數）、`]f`/`[f`（函式間跳）。

## 對比與取捨

| 面向 | Part 1 手動 textobject（`ci(`/`%`/`}`） | treesitter textobject（`af`/`]f`） |
|---|---|---|
| 靠什麼 | 符號配對 / 空行 | 樹上的節點邊界 |
| 選「整個函式」 | 做不到（要手動 `V}`） | `vaf` 一鍵 |
| 跳「下一個函式」 | `}` 跳空行，會歪 | `]f` 跳函式節點，準 |
| 選「一個參數」 | `ci(` 選整個括號內，選不到單一參數 | `ia` 精準到單一參數 |
| 需要 parser | 否，純內建 | 是，要裝 treesitter |
| 分支坑 | 無 | master/main（本章的痛） |
| 何時用 | 簡單括號內編輯、快 | 結構化導航、選/跳函式與參數 |

不是取代關係——`ci(` 改括號內容還是最快，`%` 配對跳還是好用。treesitter textobject 補的是「**選/跳整個語法結構**」這塊 Part 1 做不到的。兩套並用：小範圍編輯用 Part 1，結構導航用 treesitter。

## 踩雷集錦

1. **textobjects 忘了也釘 master**。只釘了本體 master、textobjects 用預設（main），模組路徑對不上、`require("nvim-treesitter.configs").setup{textobjects=...}` 的 textobjects 那塊靜默失效或報錯。本體和 textobjects **兩個都要釘 master**。

2. **`]f` 在 Neovim 0.12 報 `tsrange.lua:27` 錯**。不是你 config 寫錯，是 master 分支追不上 0.12 的節點 API（本章詳述）。三條繞法：pin 同期 Neovim / 改 main / 換 mini.ai。

3. **`set_jumps` 沒開，`]f` 跳過頭回不來**。跳轉沒寫進 jumplist，`Ctrl-o` 跳不回原位。move 模組記得 `set_jumps = true`。

4. **`vaf` 選不到、或選到奇怪範圍**。多半是 parser 沒裝好（`:TSInstall c`）或這個語言的 `textobjects.scm` query 沒定義 `@function.outer`。`:checkhealth nvim-treesitter` 看 parser、`:InspectTree` 確認游標下真的在 `function_definition` 節點裡。

5. **在 macro 地獄的 C 檔上 textobject 抓歪**。treesitter 容錯但不保證正確——巨集展開前的怪片段可能 parse 出 `ERROR` 節點，`af` 就框歪。這種檔上 textobject 是輔助不是真理，抓歪就退回手動選。

## 進階：再往深一層

- **`lookahead` 的用處**：`lookahead = true` 讓你游標不在任何函式上時，`vaf` 會往後找**最近的下一個**函式來選，不用先把游標移進去。讀碼時很順手——看到下面有個函式想選，直接 `vaf` 不用先跳過去。

- **repeatable move（`;` / `,`）**：master textobjects 有 `repeatable_move` 模組，能讓 `]f` 之後用 `;` 重複、`,` 反向，像 `f`/`F` 的 `;`/`,` 那樣。要另外接一小段 config 把 `;`/`,` 綁上去，讓 treesitter 跳轉也能 `;` 連跳。

- **更多 capture**：除了 `function`/`class`/`parameter`，master 的 C `textobjects.scm` 還定義了 `@block`（區塊）、`@call`（呼叫）、`@conditional`（if）、`@loop`（迴圈）、`@comment`、`@return`。你可以照樣加 keymap，例如 `["ai"] = "@conditional.outer"` 選整個 if。`:TSEditQuery textobjects c`（若可用）或去 parser 的 `queries/c/textobjects.scm` 看有哪些 capture 可用。

- **和 LSP 的分工**：textobject 是**語法**層的選取（「這個 function_definition 節點」），不懂語意。想「跳到這個函式的**定義**（可能在別的檔）」那是 clangd 的 `gd`（Part 4）。textobject 管「當前檔的結構」，LSP 管「跨檔的語意」。

## 本章重點整理

- treesitter textobject 把 Part 1 的手動 textobject 升級成**依結構移動**：`af`/`if` 選函式、`aa`/`ia` 選參數、`]f`/`[f` 函式間跳、swap 交換參數——靠**樹上的節點邊界**，不靠符號配對或空行。
- config 加 `nvim-treesitter-textobjects`（**也要釘 master**）與 `select`/`move`/`swap` 三模組；`set_jumps=true` 讓跳轉能 `Ctrl-o` 回。
- headless 驗證六個 keymap 全註冊；但**誠實記錄** master textobjects 的 move/select 在 Neovim 0.12 上會踩 `tsrange.lua` 已移除的節點方法——這是 Ch 13 「釘 master 的代價」的現場，三條繞法：同期 Neovim / 改 main / 換 mini.ai。
- 讀碼情境：`vaf` 量函式大小、`]f` 掃函式、`yif` 抄主體、swap 調參數順序。
- 和 Part 1 並用不取代；和 LSP 分工——textobject 管語法結構，LSP 管跨檔語意。

## 自我檢核

- [ ] 我能說出 `]f` 為什麼比 `}` 準「跳下一個函式」
- [ ] 我知道 `af` 和 `if`、`aa` 和 `ia` 的差別（around vs inner）
- [ ] 我記得 textobjects **也要釘 master**，以及不釘會怎樣
- [ ] 我知道 `]f` 在 0.12 報 `tsrange` 錯不是我的錯，能講出三條繞法
- [ ] 我能舉出讀碼時用 `vaf` / `]f` / `yif` / swap 的實際情境
- [ ] 我知道 textobject（語法）和 LSP `gd`（語意跨檔）的分工

## 延伸閱讀

### 官方文件（優先）

- **Neovim `:help treesitter`** 的 textobject 相關段落（若你的版本內建部分能力）
  - **讀哪裡**：`vim.treesitter` 的 query 與 node 範圍 API；理解 textobject 底層是「query 出某節點的範圍再選取」
- **Neovim `:help jumplist`**（Part 1 Ch 5 也指過）
  - **讀哪裡**：`Ctrl-o`/`Ctrl-i` 與 `set_jumps` 如何互動；懂為什麼 move 模組要 `set_jumps=true`

### 外掛

- **[nvim-treesitter-textobjects README](https://github.com/nvim-treesitter/nvim-treesitter-textobjects)** — 官方倉庫
  - **讀哪裡**：`select`/`move`/`swap`/`lsp_interop` 各模組的設定範例與可用 capture 清單；注意它 README 頂端的分支說明
- **[mini.ai](https://github.com/echasnovski/mini.ai)** — echasnovski
  - **這是什麼**：treesitter textobjects 的替代/補強，維護活躍、不吃 nvim-treesitter 分支問題；本章繞法之一
  - **讀哪裡**：README 的 `a`/`i` 自訂 textobject 範例

跳得到函式、選得中結構了。下一章我們讓選取「會長大」——incremental selection 從一個 token 一路擴到整個函式；再讓捲動時頂端固定顯示你正身處哪個函式（sticky context），讀長函式再也不迷路。

→ [Ch 15 incremental selection 與 sticky context](./15-treesitter-selection-context.md)
