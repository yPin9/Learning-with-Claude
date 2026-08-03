# Ch 15 — incremental selection 與 sticky context

> **目標**：把 treesitter 這棵樹再榨出三個讀碼利器。**incremental selection**——按 `<CR>` 讓選取從游標下一個 token 沿著樹「往上長」，一路擴大到整個表達式、整個 if、整個函式，`<BS>` 縮回去；讀複雜巢狀 code 時「精確選中越來越大的結構」。**treesitter-context（sticky scroll）**——捲到函式深處時，視窗頂端**固定顯示**你正身處的函式/if/for 簽名，讀長函式不再問「這行到底在哪個 scope」。**treesitter 折疊**——`foldmethod=expr` 配 treesitter foldexpr，折的是真正的語法區塊，比手動 fold 準。

> **環境**：Neovim v0.12.4，WSL2 / Ubuntu。incremental selection 的擴大、foldexpr 的巢狀 fold level、treesitter-context 命令，全部 headless 真跑驗證，輸出如實貼出。

## 為什麼需要這個？

讀大 C 函式有兩個反覆出現的痛：

**痛一：選取的邊界難抓**。你看到一段 `if (eventLoop->events == NULL || eventLoop->fired == NULL)`，想把整個條件抄出來。手動選：`v` 進 visual，然後一個個字元或 `f)` 慢慢框，遇到巢狀括號、`||`、換行就算歪。你要的其實是「選中這個**條件表達式節點**」——一個語法概念，不是一堆字元。

**痛二：捲到函式深處，忘了自己在哪**。讀一個三百行的函式，游標在第 250 行的某個 `if` 裡面，你看到 `r = add(r, x)` 這行——但這是哪個函式的、包在哪個 `for` 裡、哪個 `if` 的哪個分支？函式簽名早捲出畫面了。你得 `[f` 跳回函式開頭確認，再跳回來——思路斷一次。

這兩個痛，treesitter 都能解，而且用的是 Ch 13 那棵樹：**選取沿樹往上長**解痛一，**頂端固定顯示祖先節點**解痛二。加上 treesitter 折疊，你對大函式的掌控整個上一個台階。

## incremental selection：讓選取沿樹往上長

### 先建立直覺

游標停在某個 token 上，這個 token 在樹上是一個葉節點。它的父節點是更大的結構，父節點的父節點又更大——一路往上到整個函式、整個檔。incremental selection 就是**把游標映到樹上的節點，然後每按一次 `<CR>` 就選中「當前節點的父節點」**，選取範圍隨之擴大：

```
   r = add(r, x);   ← 游標在 add

   按 <CR>：選中 add            (identifier)
   按 <CR>：選中 add(r, x)      (call_expression)
   按 <CR>：選中 r = add(r, x)  (assignment_expression)
   按 <CR>：選中 r = add(r, x); (expression_statement)
   按 <CR>：選中整個 for { }    (for_statement)
   按 <CR>：選中整個函式主體    (compound_statement)
   ...
   按 <BS>：往回縮一層
```

每一步選中的都是**一個完整的語法節點**——不會選到「半個表達式」這種語法上不成立的範圍。這是它比手動 `v` + motion 強的地方：手動選容易選出語法上破碎的片段，incremental selection 永遠停在合法的節點邊界上。

### config

Part 3 的 config 在 treesitter 的 opts 裡加 `incremental_selection` 模組（本體釘 master，這模組是內建的）：

```lua
opts = {
  ensure_installed = { "c", "lua" },
  highlight = { enable = true },
  incremental_selection = {
    enable = true,
    keymaps = {
      init_selection = "<CR>",     -- normal 模式按 <CR> 開始選（選中游標下最小節點）
      node_incremental = "<CR>",   -- visual 中再按 <CR> 往上擴一層
      node_decremental = "<BS>",   -- 往回縮一層
      scope_incremental = "<Tab>", -- 直接跳到下一個 scope（函式/區塊）層級
    },
  },
  -- （textobjects 模組見 Ch 14）
}
```

四個鍵：`<CR>` 開始並逐層擴大、`<BS>` 逐層縮小、`<Tab>` 直接跳到 scope 層（跳過中間小節點，一步到函式/區塊）。

### headless 驗證：選取真的會長

游標放在 `foo.c` 第 11 行 `r = add(r, x);` 的 `add` 上（第 13 欄），連按兩次 `<CR>`，看 visual 選取範圍：

```lua
vim.fn.cursor(11, 13)   -- add 這個 identifier 上
vim.api.nvim_feedkeys(
  vim.api.nvim_replace_termcodes("<CR><CR>", true, false, true), "x", false)
local a = vim.fn.line("v"); local b = vim.fn.line(".")
print(string.format("after 2x<CR> visual spans lines %d..%d", math.min(a,b), math.max(a,b)))
```

真跑輸出：

```
incsel: after 2x<CR> visual spans lines 11..11  ok=true
```

兩次 `<CR>` 後選取還在第 11 行內（從 `add` 擴到 `add(r, x)` 再擴到 `r = add(r, x)`，都在同一行）——繼續按會擴到跨行的 `for` 區塊、整個函式。**這條路徑用的是 Neovim 內建 treesitter 節點 API，不碰 Ch 14 那個 master `tsrange` 坑，所以在 0.12 上穩跑。** 這也印證 Ch 13/14 的話：incremental selection 屬於「不受 master 老舊影響」的那一批。

### 讀碼情境

想抄出一段複雜的巢狀條件或整個 case 分支，不用手數括號：游標點進去，`<CR>` 連按到選取剛好框住你要的那層結構（狀態列看範圍），`y` 抄走。想選「這個 `if` 的整個 then 分支」——點進分支內，`<CR>` 擴到 `compound_statement` 那層停手。**「選中越來越大的結構」這個動作本身就是在讀碼**——你在確認每一層的邊界，順便理解了巢狀關係。

## treesitter-context：捲到深處也知道自己在哪

### 先建立直覺

treesitter-context（俗稱 sticky scroll，VS Code 也有）做的事：**當函式簽名、`for`、`if` 的開頭捲出畫面時，把它們釘在視窗頂端**。你在第 250 行，頂端就固定顯示這幾行的「祖先」：

```
┌────────────────────────────────────┐
│ int mul(int x, int y) {            │ ← 釘住：你在 mul 函式裡
│   for (int i = 0; i < y; i++) {    │ ← 釘住：在這個 for 裡
├────────────────────────────────────┤ ← 這條線以下是真實 buffer
│         r = add(r, x);      ← 游標 │
│     }                              │
│     return r;                      │
```

頂端那幾行不是真的 buffer 內容，是 treesitter-context 從樹上抓出「游標所在節點的祖先鏈」（函式 → for → …）貼上去的浮動視窗。捲動時它即時更新——你永遠看得到「我在哪個函式、哪個迴圈、哪個分支」。讀長函式時這消除了「這行在哪個 scope」的認知負擔。

### config

treesitter-context 是**獨立外掛**，加進 Part 3 config：

```lua
{ "nvim-treesitter/nvim-treesitter-context",
  opts = { max_lines = 3, mode = "cursor" } },
```

- `max_lines = 3`：頂端最多釘 3 行（祖先太深時只顯示最近的幾層，不吃掉半個畫面）。
- `mode = "cursor"`：以**游標**所在的節點算祖先鏈（另一個選項 `"topline"` 以畫面頂行算）。讀碼用 `cursor` 較直覺——顯示的是你正在看的那行的 scope。

### headless 驗證：外掛與命令就位

```lua
local ok, tc = pcall(require, "treesitter-context")
print("treesitter-context loaded: " .. tostring(ok))
print("TSContext command exists: " .. tostring(vim.fn.exists(":TSContext") == 2))
```

真跑輸出：

```
treesitter-context loaded: true
TSContext command exists: true
```

外掛載入、`:TSContext` 命令註冊成功。

> **互動 UI 無法貼截圖**：sticky context 那條「釘在頂端的浮動視窗」是視覺效果，headless 截不了圖。以上 headless 驗證的是外掛載入成功、命令存在；「頂端固定顯示祖先」的效果請在真 nvim 裡捲一個長函式看。

### 鍵位 / 命令

| 模式 | 命令 / 按鍵 | 作用 |
|---|---|---|
| `:` | `:TSContext toggle` | 開/關 sticky context |
| `:` | `:TSContext enable` / `disable` | 明確開 / 關 |
| Lua | `require("treesitter-context").go_to_context()` | 跳到頂端顯示的那個 context（祖先節點）開頭——常綁 `[c` |

實用綁定：把 `go_to_context()` 綁到 `[c`，讀到函式深處想回到函式簽名那行，`[c` 一鍵跳上去（比 `[f` 更貼「回到我所在的 context 頭」）。

## treesitter 折疊：折的是真正的語法區塊

### 先建立直覺

Vim 的 fold（折疊）能把一段 code 收成一行，讀大檔時把不看的函式折起來、只留骨架。傳統 fold 方法有兩種痛：`foldmethod=indent`（靠縮排，遇到縮排不規律就折歪）、`foldmethod=manual`（手動 `zf`，累）。treesitter 提供 `foldexpr`——**折疊邊界直接來自語法樹**：一個函式是一個 fold、一個 `for` 區塊是一個 fold，巢狀 fold level 對應樹的深度。準。

### config

Part 3 config 尾端加：

```lua
vim.opt.foldmethod = "expr"
vim.opt.foldexpr = "v:lua.vim.treesitter.foldexpr()"  -- Neovim 內建的 treesitter foldexpr
vim.opt.foldenable = false   -- 開檔預設不折（不然一開就全折起來很煩），要折才 zc/zM
```

`vim.treesitter.foldexpr()` 是 **Neovim 核心內建**（不是 nvim-treesitter 外掛），所以不吃 master/main 分支問題。`foldenable = false` 讓檔案打開時是展開的，你想折再 `zM`（全折）/ `zc`（折游標處）。

### headless 驗證：fold level 對應樹深度

```lua
vim.o.foldenable = true
vim.cmd("normal! zx")   -- 重算 fold
print("foldlevel line 4 (add body): " .. vim.fn.foldlevel(4))
print("foldlevel line 10 (for in mul): " .. vim.fn.foldlevel(10))
print("foldlevel line 11 (inside for): " .. vim.fn.foldlevel(11))
```

真跑輸出：

```
foldmethod=expr foldexpr=v:lua.vim.treesitter.foldexpr()
foldlevel line 4 (add body): 1
foldlevel line 10 (for in mul): 2
foldlevel line 11 (inside for): 2
```

fold level 精確對應巢狀深度：函式主體（第 4 行 `add` 內）是 level 1，`for` 迴圈本身（第 10 行）與迴圈內（第 11 行）是 level 2——因為 `for` 巢狀在函式裡，深一層。這是**語法感知的折疊**：level 直接反映樹的深度，不靠縮排猜。用 `zM` 全折後，一個大檔立刻收成「一堆函式標題」的骨架，`zR` 全開，`za` 切換游標處——大檔鳥瞰的利器。

### fold 常用鍵位

| 模式 | 按鍵 | 作用 |
|---|---|---|
| normal | `za` | 切換游標處的 fold（開/折） |
| normal | `zc` / `zo` | 折 / 開游標處 |
| normal | `zM` | 全部折起（大檔鳥瞰：只剩函式標題） |
| normal | `zR` | 全部展開 |
| normal | `zj` / `zk` | 跳到下一個 / 上一個 fold |
| normal | `zx` | 重算 fold 並恢復（fold 亂了按它） |

## 三者合體的讀碼流

這三個工具在讀長函式時是連動的：

1. `zM` 把整個檔折成骨架，`zj`/`zk` 或 `]f`（Ch 14）掃過函式標題，找到目標函式 `za` 展開。
2. 進到函式深處讀，**treesitter-context** 在頂端一直告訴你「你在哪個函式的哪個迴圈」——不迷路。
3. 讀到一段複雜巢狀邏輯，`<CR>` **incremental selection** 逐層選中確認邊界、順便理解巢狀，要抄就 `y`。

折疊管**鳥瞰**、context 管**定位**、incremental selection 管**細讀與選取**——一套下來，三百行的函式不再是一堵牆。

## 對比與取捨

| 工具 | 傳統做法的痛 | treesitter 版的好 | 底層 |
|---|---|---|---|
| 選取 | 手動 `v`+motion，易選出破碎片段 | incremental selection 永遠停在合法節點邊界 | 內建 treesitter node，不吃 master 坑 |
| 定位 | 捲深了忘了在哪個 scope，要跳回確認 | sticky context 頂端固定顯示祖先鏈 | 獨立外掛 nvim-treesitter-context |
| 折疊 | indent 折歪、manual 折累 | foldexpr 折真正的語法區塊，level=樹深 | 核心內建 `vim.treesitter.foldexpr()` |

值得注意：incremental selection 與 foldexpr 都用**核心內建** treesitter，只有 treesitter-context 是外掛——所以前兩者在最新 Neovim 上最穩。

## 踩雷集錦

1. **`foldenable = false` 沒設，開檔就全折起來**。foldmethod=expr 一算完，若 `foldenable` 是開的，大檔一打開就收成一坨，很嚇人。設 `foldenable = false`，要折再 `zM`。

2. **fold 沒生效或折歪**。多半是 parser 沒裝（`:TSInstall c`）或 fold 沒重算——按 `zx` 重算。確認 `foldexpr` 真的是 `v:lua.vim.treesitter.foldexpr()`（`:set foldexpr?` 看）。

3. **incremental selection 的 `<CR>` 和其他插件搶鍵**。`<CR>` 在 normal 是「下一行」、在 quickfix 是「開該項」。這個 keymap 只在你手動觸發時擴大選取，但若你另有插件也綁 normal `<CR>`（如某些 which-key/autopair），會衝突。衝突就換鍵（如 `<C-space>`）。

4. **treesitter-context 吃掉太多畫面**。`max_lines` 沒限制、又遇到很深的巢狀，頂端釘一大片。設 `max_lines = 3` 左右，只留最近幾層祖先。

5. **把 incremental selection 當語意選取**。它沿的是**語法樹**——選中的是「語法上的節點」，不懂「這個變數的作用域到哪」。要語意層的選取（如「這個符號的所有出現」）是 LSP 的活。

## 進階：再往深一層

- **`scope_incremental`（`<Tab>`）跳過中間層**：連按 `<CR>` 要好幾下才擴到函式層，`<Tab>` 直接從當前跳到下一個 **scope**（函式/區塊）層級，省按鍵。讀碼想「一步選到整個函式主體」用它。

- **`go_to_context()` 綁 `[c`**：treesitter-context 的 `go_to_context()` 跳到頂端顯示的祖先開頭。綁 `[c` 後，讀到函式深處一鍵回到「我所在的 context 頭」——比 `[f`（跳函式開頭）更貼「回到最近的祖先簽名」。

- **fold 的 `foldtext`**：預設 fold 起來那行顯示的文字可自訂（`foldtext`），能讓折起的函式顯示簽名而非 `+-- 42 lines`。想要 `zM` 後的骨架直接可讀，客製 `foldtext` 顯示每個 fold 的第一行（函式簽名）。

- **fold 與 session**：fold 狀態預設不跨 session 保存。想關掉 nvim 再開還記得你折了哪些，設 `foldmethod` 相關的 view 保存（`:mkview`/`viewoptions`，Part 6 Ch 27 的 session 會談）。

## 本章重點整理

- **incremental selection**（`<CR>` 擴 / `<BS>` 縮 / `<Tab>` 跳 scope）：選取沿語法樹往上長，永遠停在合法節點邊界；讀複雜巢狀時逐層確認與選取。用核心 treesitter node API，不吃 master 坑，0.12 上穩。
- **treesitter-context（sticky scroll）**：捲到函式深處時頂端固定顯示祖先鏈（函式/for/if），不迷路；`max_lines`/`mode="cursor"` 設定，`:TSContext toggle`、`go_to_context()` 可綁 `[c`。獨立外掛。
- **treesitter 折疊**：`foldmethod=expr` + `vim.treesitter.foldexpr()`（核心內建），fold level 對應樹深度（實測函式 body=1、內層 for=2）；`foldenable=false` 避免開檔全折；`zM`/`zR`/`za` 鳥瞰大檔。
- 三者合體：折疊鳥瞰、context 定位、incremental selection 細讀選取——把三百行函式拆成可掌控的結構。

## 自我檢核

- [ ] 我能解釋 incremental selection 為什麼「永遠停在合法節點邊界」，比手動選強在哪
- [ ] 我知道 `<CR>`/`<BS>`/`<Tab>` 各做什麼
- [ ] 我能說出 treesitter-context 頂端那幾行是哪來的（樹上的祖先鏈）、解決什麼痛
- [ ] 我知道 foldexpr 的 fold level 對應樹深度，且它是核心內建不吃分支坑
- [ ] 我記得 `foldenable = false` 是為了避免開檔全折
- [ ] 我能描述折疊/context/incremental selection 三者在讀長函式時怎麼連動

## 延伸閱讀

### 官方文件（優先）

- **Neovim `:help vim.treesitter.foldexpr()`**
  - **讀哪裡**：這個函式的用法與 `foldmethod`/`foldexpr` 的搭配；本章 fold config 的核心，核心內建不挑分支
- **Neovim `:help fold`**
  - **讀哪裡**：`za`/`zM`/`zR`/`zj` 等 fold 操作與 `foldlevel`/`foldenable` 選項；fold 的完整操作手冊

### 外掛

- **[nvim-treesitter-context README](https://github.com/nvim-treesitter/nvim-treesitter-context)** — 官方倉庫
  - **讀哪裡**：`max_lines`/`mode`/`separator` 等設定與 `go_to_context()`；把 sticky context 調到你順手
- **[nvim-treesitter 的 incremental_selection 說明](https://github.com/nvim-treesitter/nvim-treesitter)**（master 分支 README）
  - **讀哪裡**：`incremental_selection` 模組的 keymap 設定；對照本章 config

鳥瞰、定位、細讀都有了。最後一章我們把這棵樹拿來做**結構化搜尋**——不用 grep 靠猜，直接對樹提問「給我所有函式定義」「所有 static 函式」「所有呼叫某函式的地方」，接上 `reading_code` Ch 15 的結構查詢，只是這次在 Neovim 裡。

→ [Ch 16 treesitter 查詢：InspectTree 與結構搜尋](./16-treesitter-queries.md)
