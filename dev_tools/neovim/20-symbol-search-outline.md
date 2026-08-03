# Ch 20 — symbol 搜尋與 outline

> **目標**：Ch 19 的探針解決「追一個已知符號」，這章解決「鳥瞰一個陌生檔/專案的符號地圖」。三個能力：**document symbols**（當前檔的函式/型別大綱，進陌生大檔先看它建地圖）、**workspace symbols**（全專案搜符號名——知道函式名直接跳過去，讀大 repo 神器）、以及 **outline 側欄**（aerial.nvim/symbols-outline，把大綱釘在側邊常駐）。我們把這些接到 telescope 的模糊選單上，讓「符號名記不全也能跳」。學完你進任何陌生檔/repo 都有「先看骨架再讀肉」的操作。

> **環境**：Neovim v0.12.4，WSL2 / Ubuntu，clangd 14，telescope。本章 document/workspace symbol 的輸出是 headless 對真 C 專案跑 clangd-14 照抄的；telescope 互動選單無法貼截圖，標「互動 UI」處用逐鍵描述，底層 LSP 查詢已驗證。

## 為什麼需要這個？進陌生大檔的第一件事不是從第一行讀

`reading_code` 的偵察信條：進一個陌生檔，別急著從第一行往下讀。先鳥瞰——這檔有哪些函式、哪些型別、大概怎麼組織的。一個 3000 行的 C 檔，你從頭讀到尾會迷路；先看它的**符號大綱**（有 40 個函式、5 個 struct），你立刻有張地圖，知道核心函式在哪、輔助函式在哪，再針對性地讀。

文字工具做不好這件事。`rg "^[a-z].*\(" foo.c` 想撈函式定義，會漏掉多行簽名、撈到一堆呼叫點和註解。語意工具知道「哪些是真的函式/型別定義」，因為它有 AST。這章就是用 clangd 的 symbol 能力建這張地圖。

兩個層次：

- **document symbols**：當前**這一個檔**的符號大綱。進陌生大檔第一個動作。
- **workspace symbols**：**整個專案**的符號搜尋。你知道函式叫 `luaD_call`，不管它在哪個檔，打名字直接跳過去。

## document symbols：進陌生大檔先建地圖

`<leader>ds`（document symbol）問 clangd「這檔裡有哪些符號」，列出所有函式、型別、全域變數。

實測，對一個小專案的 `geometry.c`（headless `textDocument/documentSymbol`，照抄）：

```
=== documentSymbol (geometry.c) ===
  [Function] distance (line 4)
  [Function] area_triangle (line 10)
```

兩個函式、各在哪行，一目了然。真專案（如 Lua 的 `lvm.c`）這清單會有幾十個 entry，含函式、struct、typedef、macro——**這就是那張地圖**。

**讀碼情境：陌生大檔的偵察**。你打開 Lua 的 `lapi.c`（對外 API 的實作，上千行），第一個動作按 `<leader>ds`，telescope 跳出所有 `lua_*` API 的清單。你打字模糊過濾 `pcall`，秒跳到 `lua_pcallk` 的定義，不用捲一千行。**先看骨架、再讀肉**——這是進任何陌生大檔的固定開場。

document symbols 在 telescope 裡是**可模糊過濾**的清單，比原生的 `vim.lsp.buf.document_symbol`（丟 quickfix）好用得多——因為你常常記得「函式名有 pcall 這幾個字」但記不全，模糊過濾正是為此。

## workspace symbols：全專案搜符號名直接跳

`<leader>ws`（workspace symbol）是讀大 repo 的神器：問 clangd「整個專案裡，名字含這串的符號在哪」，你打字它即時過濾，選中直接跳過去——**不管那符號在哪個檔**。

實測，對一個專案搜 `area`（headless `workspace/symbol`，query=`area`，照抄）：

```
=== workspace/symbol query=area ===
  [Function] area_triangle @ geometry.c:10
```

真專案威力更大：在 Lua repo 搜 `luaD`，clangd 列出所有 `luaD_*` 核心函式（`luaD_call`/`luaD_pcall`/`luaD_poscall`...）跨所有檔，你選一個直接跳。

**讀碼情境：知道名字直接跳**。讀 kernel/Lua 這種大 repo，你常從文件或別處知道「核心是 `luaD_call` 這個函式」，但不知道它在哪個檔。以前你得 `rg luaD_call` 撈一堆呼叫點再自己找定義；現在 `<leader>ws` 打 `luaD_call`，clangd 直接列出**它的定義**（不是呼叫點），跳過去。這是讀大 repo 從「聽說有個函式」到「站在它定義前」最快的路徑。

**document vs workspace 的分工**：

```
   你要找的符號在哪？
        │
        ├─ 就在當前這個檔裡    → <leader>ds （document symbols，建當前檔地圖）
        └─ 在專案某個檔（不知哪個）→ <leader>ws （workspace symbols，全專案搜名字）
```

## telescope 的三個 LSP symbol picker

Ch 0 骨架已把 telescope 裝好、`<leader>fs` 綁了 `lsp_workspace_symbols`。這章把三個相關 picker 說清楚，它們都比原生 LSP 命令（丟 quickfix）好用，因為 telescope 給模糊過濾 + 預覽：

| telescope picker | 作用 | 對應 LSP |
|---|---|---|
| `lsp_document_symbols` | 當前檔符號，模糊過濾 | `textDocument/documentSymbol` |
| `lsp_workspace_symbols` | 全專案符號，**固定 query** | `workspace/symbol` |
| `lsp_dynamic_workspace_symbols` | 全專案符號，**你打字它即時重查** | `workspace/symbol`（每次打字重送） |

`lsp_workspace_symbols` vs `lsp_dynamic_workspace_symbols` 的差別要懂：

- **`lsp_workspace_symbols`**：送一次 query 給 clangd，把結果load進 telescope，之後在 telescope 內做**本地**模糊過濾。適合「query 相對固定、想在結果裡篩」。
- **`lsp_dynamic_workspace_symbols`**：你**每打一個字**，telescope 就重送一次 `workspace/symbol` 給 clangd。適合「大專案符號太多，一次全撈太重，靠 clangd 逐字縮小範圍」。讀大 repo（符號幾萬個）用 dynamic 這個，讓 clangd 幫你即時過濾，別一次撈爆。

**互動 UI 無法貼截圖，以下為鍵位操作；底層 LSP 查詢已 headless 驗證存在/可執行**：按 `<leader>ws`，telescope 跳出符號 picker，打字模糊過濾（dynamic 版每個字重查 clangd），`<C-n>`/`<C-p>` 上下選，`<CR>` 跳到那符號定義。這套「打字過濾 + 上下選 + Enter 跳」跟 Part 2 的 telescope 找檔完全同構，肌肉共用。

## config：接上三個 symbol picker + document symbol 鍵位

往 `init.lua` 的 telescope `keys` 加 document/dynamic workspace symbol，並把 `LspAttach` 裡的 `<leader>ds` 改指向 telescope 版（有模糊過濾）。telescope spec 補：

```lua
{ "nvim-telescope/telescope.nvim", branch = "0.1.x",
  dependencies = { "nvim-lua/plenary.nvim" },
  keys = {
    { "<leader>ff", "<cmd>Telescope find_files<cr>" },
    { "<leader>fg", "<cmd>Telescope live_grep<cr>" },
    -- symbol：當前檔地圖 / 全專案動態搜尋
    { "<leader>ds", "<cmd>Telescope lsp_document_symbols<cr>",           desc = "當前檔符號大綱" },
    { "<leader>ws", "<cmd>Telescope lsp_dynamic_workspace_symbols<cr>",  desc = "全專案符號搜尋" },
  } },
```

因為 `<leader>ds`/`<leader>ws` 現在由 telescope 提供，`LspAttach` 裡就不用再重複綁 `vim.lsp.buf.document_symbol`（那個丟 quickfix、無模糊過濾）。這是 config 逐 Part 累加的一個小重構：Ch 19 先用原生版，這章有了 telescope 就升級成模糊過濾版。

> **注意**：`lsp_document_symbols`/`lsp_dynamic_workspace_symbols` 這些 picker 需要 buffer 已 attach clangd 才有結果。沒 attach（非 C 檔、或 clangd 沒起來）按了會空。這跟 Ch 17 的 attach 前提一致。

## outline 側欄：把大綱釘在側邊常駐（選配）

telescope 的 document symbols 是「叫出來、用完就關」的**臨時**清單。有時你想要大綱**常駐在側欄**，一邊讀 code 一邊看著整檔結構、點哪個符號跳哪——這是 outline 外掛做的事。

兩個主流選配：

- **aerial.nvim**：功能較全，可用 treesitter **或** LSP 當 symbol 來源（沒 clangd 時退 treesitter 也有大綱），側欄可摺疊、有 breadcrumb（麵包屑，顯示游標在哪個函式的哪個 scope）。我推這個，因為它「clangd 沒起來也有大綱」的降級對讀碼很實用。
- **symbols-outline.nvim**：較輕，純 LSP 來源，介面簡潔。

aerial 的最小 config（選配，加進 lazy spec）：

```lua
{ "stevearc/aerial.nvim",
  opts = {
    backends = { "lsp", "treesitter" },   -- LSP 優先，退 treesitter
    layout = { default_direction = "right" },
  },
  keys = {
    { "<leader>o", "<cmd>AerialToggle<cr>", desc = "切換 outline 側欄" },
  } },
```

**互動 UI 無法貼截圖，以下為操作**：按 `<leader>o`，右側開一個大綱側欄，列出當前檔所有函式/型別的樹狀結構，游標在 code 裡移動時側欄會高亮「你在哪個符號」，在側欄按 `<CR>` 跳到對應定義。

**讀碼情境**：讀一個上千行、函式互相呼叫很複雜的檔（如 Lua 的 `lvm.c` 字節碼直譯器），aerial 側欄常駐，你隨時看得到「整檔的骨架 + 我現在在哪」，不會在捲動中迷失。這是 telescope 臨時清單補不了的「常駐地圖」。

**選配的取捨**：aerial 是額外外掛、額外一塊側欄佔螢幕。如果你螢幕小、或習慣用 `<leader>ds` 臨時叫大綱，不裝也完全沒問題——document symbols 的模糊跳轉已覆蓋八成需求。outline 側欄是「大檔精讀」的加分項，不是必需。

## 鍵位表

| 模式 | 按鍵 | 作用 | 讀碼情境 |
|---|---|---|---|
| n | `<leader>ds` | 當前檔符號大綱（telescope 模糊） | 進陌生大檔先建地圖 |
| n | `<leader>ws` | 全專案符號搜尋（telescope 動態） | 知道名字直接跳到定義 |
| n | `<leader>o` | 切換 outline 側欄（aerial，選配） | 大檔精讀常駐地圖 |
| picker | 打字 | 模糊過濾符號 | 記不全名字也能找 |
| picker | `<C-n>`/`<C-p>` | 上下選 | — |
| picker | `<CR>` | 跳到選中符號定義 | — |

## 對比與取捨

| | document symbols | workspace symbols | outline 側欄 |
|---|---|---|---|
| 範圍 | 當前檔 | 整個專案 | 當前檔 |
| 形態 | 臨時 picker | 臨時 picker | 常駐側欄 |
| 用途 | 建當前檔地圖 | 全域找符號跳 | 大檔精讀常駐 |
| 需要 clangd | 是（aerial 可退 treesitter） | 是 | LSP 優先，可退 treesitter |
| 何時用 | 進陌生大檔第一動作 | 知道名字不知檔 | 上千行複雜檔 |

| symbol 搜尋 vs Part 2 工具 | 何時用 |
|---|---|
| `<leader>ff`（find_files） | 知道**檔名** |
| `<leader>fg`（live_grep） | 找**任意文字**（含字串、註解） |
| `<leader>ws`（workspace symbol） | 知道**符號名**，要跳**定義**（不是呼叫點） |

三者互補：找檔用 ff、找字串用 fg、找符號定義用 ws。`<leader>ws` 相對 `<leader>fg` 的優勢是它給**定義**（語意），grep 給所有出現（含呼叫、註解、字串）。

## 踩雷集錦

1. **workspace symbol 空 / 不全**：背景 index 沒建完，clangd 的全專案符號表還沒 ready。大專案剛開檔的前幾十秒 `<leader>ws` 可能撈不到東西，等 index 建好。這跟 Ch 19 的 `gr` 漏引用同源。

2. **`lsp_workspace_symbols` 打字沒反應**：這個版本是**固定 query** 的——它在 telescope 內做本地過濾，不會每字重查 clangd。要「打字即時重查」用 `lsp_dynamic_workspace_symbols`。搞混這兩個會以為壞了。

3. **document symbols 在非 C 檔空的**：symbol picker 靠 attach 的 LSP。打開一個 `.txt`、`.md`、或 clangd 沒 attach 的檔按 `<leader>ds` 當然空。aerial 配了 treesitter backend 的話還能退 treesitter 給大綱，純 LSP 的 picker 就空。

4. **大 repo `<leader>ws` 撈爆/卡頓**：符號幾萬個時，非 dynamic 版一次全撈會卡。用 dynamic 版讓 clangd 逐字縮範圍。這也是為什麼本課 config 綁 dynamic 那個。

5. **outline 側欄跟不上**：aerial/symbols-outline 更新有節流，你快速編輯時側欄可能慢半拍。這是效能取捨不是壞，讀碼（不編輯）時不受影響。

## 進階：再往深一層

- **symbol kind 過濾**：LSP 的 symbol 有 kind（Function/Struct/Variable/Macro...）。telescope 和 aerial 都可以只顯示某幾種 kind——例如只看 Function 建「函式地圖」，不被一堆變數干擾。aerial 的 `filter_kind` 設這個。

- **breadcrumb（麵包屑）**：顯示「游標現在在 `foo() > if 區塊 > for 迴圈`」的路徑條，通常放狀態列或視窗頂。aerial 內建、或用 `nvim-navic`（吃 LSP 的 documentSymbol）。讀深層巢狀的大函式時，隨時知道「我在哪個 scope」很有用。這跟 Part 3 treesitter 的 sticky context（Ch 15）是不同來源（LSP vs treesitter）但目的類似的兩個工具。

- **workspace symbol 的模糊匹配是 clangd 做的**：dynamic 版每次打字送 `workspace/symbol` query，**模糊匹配在 clangd 端**（它的 fuzzy matcher），不是 telescope 端。所以匹配品質看 clangd 版本。這解釋了為什麼有時打了字結果排序跟你預期不同——那是 clangd 的排序邏輯。

- **treesitter 也能做 document symbol**：沒有 clangd（樹編不起來、非 C）時，treesitter 也能解析出函式/型別大綱（Part 3）。aerial 的 `backends = {"lsp","treesitter"}` 就是「有 LSP 用 LSP，沒有退 treesitter」。這是「語意不可用時退語法」的又一個例子，跟 Part 5 gtags 後備同理。

## 本章重點整理

- 進陌生檔/repo 別從第一行讀——**先看 symbol 大綱建地圖**（reading_code 偵察信條的落地）。
- **document symbols**（`<leader>ds`）：當前檔的函式/型別大綱，進陌生大檔第一動作。
- **workspace symbols**（`<leader>ws`）：全專案搜符號名直接跳**定義**，讀大 repo 神器。
- telescope 三 picker：`lsp_document_symbols`、`lsp_workspace_symbols`（固定 query）、`lsp_dynamic_workspace_symbols`（逐字重查，大 repo 用這個）。
- **outline 側欄**（aerial/symbols-outline，選配）：把大綱釘側邊常駐，大檔精讀不迷路；aerial 可退 treesitter。
- 分工：找檔 `ff`、找字串 `fg`、找符號定義 `ws`；三者互補。

## 自我檢核

- [ ] 我知道進陌生大檔的第一動作是 `<leader>ds` 建地圖，而非從第一行讀
- [ ] 我能分清 document symbols（當前檔）與 workspace symbols（全專案）的用途
- [ ] 我知道 `lsp_workspace_symbols`（固定 query）與 `lsp_dynamic_workspace_symbols`（逐字重查）差在哪，以及大 repo 該用哪個
- [ ] 我能說出 `<leader>ws` 相對 `<leader>fg` 的優勢（給定義 vs 給所有出現）
- [ ] 我知道 outline 側欄（aerial）是選配、何時值得裝、以及它能退 treesitter
- [ ] 我知道 symbol picker 靠 attach 的 LSP，非 C 檔/沒 attach 會空

## 延伸閱讀

### 官方文件（優先）

- **Neovim `:help vim.lsp.buf.document_symbol`** 與 **`workspace_symbol`**
  - **讀哪裡**：兩個函式的行為。telescope picker 底層就是呼叫它們。
- **`:help lsp-symbol`**（symbol kind 相關）
  - **讀哪裡**：LSP 的 SymbolKind 列表，理解 Function/Struct/Macro 等 kind 用於過濾。

### 外掛文件

- **[telescope.nvim — LSP pickers](https://github.com/nvim-telescope/telescope.nvim)**（README 的 Pickers 段）
  - **讀哪裡**：`lsp_document_symbols` / `lsp_workspace_symbols` / `lsp_dynamic_workspace_symbols` 的差別與參數。本章 config 的依據。
- **[aerial.nvim](https://github.com/stevearc/aerial.nvim)**
  - **讀哪裡**：`backends`（lsp/treesitter/markdown）、`filter_kind`、breadcrumb 設定。決定你要不要裝側欄大綱。

### 橫向連結

- **本課 Part 3 Ch 15**「incremental selection 與 sticky context」
  - treesitter 的 sticky context 與本章的 breadcrumb 都在解「巢狀深層時我在哪個 scope」，一個來自語法一個來自語意，對照理解。

有了符號地圖，你能鳥瞰也能精跳了。但 clangd 在大專案、重度 macro/ifdef 的樹（如 kernel）會有它的**局限與坑**——它只看到「一種編譯組態」的世界。下一章誠實講 clangd 何時不可靠，以及 `.clangd` 設定檔與背景索引的調教。

→ [Ch 21 clangd 進階與 macro/ifdef 的坑](./21-clangd-advanced.md)
