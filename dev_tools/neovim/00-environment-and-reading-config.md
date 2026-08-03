# Ch 0 — 環境與讀碼 config 骨架

> **目標**：裝好一個現代 Neovim，並從零建一個**讀碼專用的 config 骨架**——不是抄一個黑箱發行版，是自己搭、每一塊都理解。這份 config 是全課的 contract：後面每一章都往它上面加東西（telescope、treesitter textobjects、clangd 進階、gtags…），你最後會有一台自己懂、能改的讀碼機器。

> **環境**：Neovim v0.12.4（官方 stable tarball），WSL2 / Ubuntu，clangd 14、universal-ctags 5.9、GNU Global、rg、fzf、bear。本章所有輸出都是在此環境真跑出來的。

## 為什麼需要這個？

你可以裝 LazyVim / NvChad 這種現成發行版，五分鐘就有一台漂亮的編輯器。那對**寫** code 很好。但這門課的目標是**精通讀碼操作**——而精通的前提是**理解每一塊在做什麼**。

黑箱發行版的問題：clangd 跳不到定義時，你不知道是 LSP 沒 attach、還是缺 `compile_commands.json`、還是發行版把 keymap 綁到別的鍵了。你會卡在「它為什麼不動」而不是「我要怎麼讀這段 code」。**工具卡住時你 debug 不了工具，就無法專心 debug code。**

所以我們從一個**最小、可理解的 config 骨架**開始，一章加一塊。到 Part 6 你回頭看，整份 config 沒有一行是你不懂的。這也對應 `reading_code` 的信條：不信黑箱，看清楚裡面。

> 這份 config 走 **kickstart.nvim** 的精神（單檔、註解、自己懂），不走 LazyVim（開箱即用但你不知道它幫你設了什麼）。想要「開箱即用」而非「精通」，LazyVim 是合理選擇——但那是另一條路。

## 先建立直覺：config = 一份會執行的 Lua

老 Vim 的設定是 Vimscript（`.vimrc`）。**Neovim 的設定是 Lua**（`init.lua`），一份開機時被執行的程式。這改變一切：你的設定不是一堆神秘指令，是可讀、可 debug、可組合的程式碼。

```
    Neovim 啟動
        │
        ▼
   讀 ~/.config/nvim/init.lua   ← 一份 Lua 程式，從上往下跑
        │
        ├─ 設 options（行號、大小寫…）
        ├─ bootstrap 外掛管理器（lazy.nvim）
        ├─ 宣告要哪些外掛 + 它們的設定
        └─ 設 keymap（gd 跳定義、<leader>ff 找檔…）
        │
        ▼
   一台讀碼機器
```

關鍵心智模型：**外掛（plugin）也只是別人寫的 Lua**，`lazy.nvim` 負責幫你下載、載入、更新它們。你的 `init.lua` 是「宣告我要什麼」，lazy 去把它們湊齊。

## 第一步：裝現代 Neovim

**版本很重要**。這門課用的 LSP、treesitter、`vim.lsp.enable` 這些 API 需要新版 Neovim。Ubuntu apt 的版本通常太舊（0.6/0.7），會讓一半的 config 跑不起來。用官方 tarball：

```bash
# 抓官方 stable（注意新版檔名是 nvim-linux-x86_64.tar.gz，舊的 nvim-linux64 已 404）
curl -fsSL -o nvim.tar.gz \
  https://github.com/neovim/neovim/releases/download/stable/nvim-linux-x86_64.tar.gz
sudo tar -C /opt -xzf nvim.tar.gz
/opt/nvim-linux-x86_64/bin/nvim --version | head -1
```

真跑輸出：

```
NVIM v0.12.4
```

把它放進 PATH（加到 `~/.bashrc`）：

```bash
export PATH="/opt/nvim-linux-x86_64/bin:$PATH"
```

> 踩雷預告：官方 release 的 tarball 檔名在 2024 年底改過，`nvim-linux64.tar.gz` 會回 404，要用 `nvim-linux-x86_64.tar.gz`。這種「檔名悄悄改掉」的事在工具世界很常見——遇到 404 先確認你抄的 URL 是不是舊的。

裝配套工具（後面各 Part 會用）：

```bash
sudo apt-get install -y clangd fzf bear universal-ctags global cscope ripgrep
```

## 第二步：讀碼 config 骨架

建 `~/.config/nvim/init.lua`。這是本課的 contract 起點，**完整檔在** `dev_tools/neovim/config/init.lua`，這裡逐塊解釋。

先是 options 與 leader key：

```lua
-- 讀碼機器 config（課程 contract 骨架）
vim.g.mapleader = " "          -- leader = 空白鍵，讀碼快捷都掛它下面
vim.opt.number = true
vim.opt.relativenumber = true  -- 相對行號：讓 5j / 12k 這種跳躍好算
vim.opt.mouse = "a"
vim.opt.ignorecase = true
vim.opt.smartcase = true       -- 搜尋全小寫時忽略大小寫，有大寫才精確配對
vim.opt.scrolloff = 8          -- 游標上下永遠留 8 行，讀碼時看得到上下文
```

接著 bootstrap 外掛管理器 `lazy.nvim`——第一次啟動時自己 `git clone` 下來：

```lua
local lazypath = vim.fn.stdpath("data") .. "/lazy/lazy.nvim"
if not vim.uv.fs_stat(lazypath) then
  vim.fn.system({ "git", "clone", "--filter=blob:none",
    "https://github.com/folke/lazy.nvim.git", "--branch=stable", lazypath })
end
vim.opt.rtp:prepend(lazypath)   -- 把 lazy 加進 runtimepath
```

然後宣告讀碼三大外掛——**treesitter（看結構）、telescope（找東西）、lspconfig（懂語意）**，這三塊是後面 Part 3/2/4 的主角：

```lua
require("lazy").setup({
  -- treesitter：語法感知的高亮與導航（Part 3）
  { "nvim-treesitter/nvim-treesitter", branch = "master", build = ":TSUpdate",
    opts = { ensure_installed = { "c", "lua" }, highlight = { enable = true } },
    config = function(_, opts) require("nvim-treesitter.configs").setup(opts) end },

  -- telescope：模糊找檔 / 全文搜尋 / 符號（Part 2）
  { "nvim-telescope/telescope.nvim", branch = "0.1.x",
    dependencies = { "nvim-lua/plenary.nvim" },
    keys = {
      { "<leader>ff", "<cmd>Telescope find_files<cr>" },  -- 找檔
      { "<leader>fg", "<cmd>Telescope live_grep<cr>" },   -- 全文搜尋
      { "<leader>fs", "<cmd>Telescope lsp_workspace_symbols<cr>" },
    } },

  -- LSP client 設定（Part 4）
  { "neovim/nvim-lspconfig" },
}, { rocks = { enabled = false } })
```

最後開 clangd 並掛上讀碼鍵位——**這是全課最常按的四個鍵**：

```lua
vim.lsp.enable("clangd")   -- Neovim 0.11+ 內建的 LSP 啟用 API
vim.api.nvim_create_autocmd("LspAttach", {
  callback = function(ev)
    local o = { buffer = ev.buf }
    vim.keymap.set("n", "gd", vim.lsp.buf.definition, o)   -- 跳定義
    vim.keymap.set("n", "gr", vim.lsp.buf.references, o)   -- 找所有引用
    vim.keymap.set("n", "K",  vim.lsp.buf.hover, o)        -- 看型別/文件
    vim.keymap.set("n", "<leader>ds", vim.lsp.buf.document_symbol, o)
  end,
})
```

> `branch = "master"` 這行是刻意的，**不是筆誤**。nvim-treesitter 在 2024 年把開發搬到重寫的 `main` 分支、封存了 `master`，兩個分支的設定 API 完全不同（`main` 拿掉了 `require("nvim-treesitter.configs").setup()`）。多數現存教材與 kickstart 還在用 `master` 的經典 API，所以我們釘 `master`。Ch 13 會詳談這個分裂。

## 底層機制：第一次啟動時發生什麼

`init.lua` 寫好後第一次開 `nvim`，這條鏈會跑：

```
nvim 啟動
  │
  ├─ init.lua 執行到 bootstrap 那段
  │     └─ lazypath 不存在 → git clone lazy.nvim
  │
  ├─ require("lazy").setup({...})
  │     └─ lazy 發現 treesitter/telescope/lspconfig 還沒裝
  │        → 背景 git clone 每個外掛到 ~/.local/share/nvim/lazy/
  │        → treesitter 的 build=":TSUpdate" 觸發編譯 C parser
  │
  └─ 開好，clangd 在你打開 .c 檔時 attach
```

可以用 **headless 模式**（不開 UI，純腳本）驗證這一切真的動了——這也是這門課驗證 config 的方式：

```bash
# 裝所有外掛（headless，不進 UI）
nvim --headless "+Lazy! sync" +qa
```

真跑後，外掛都到位：

```
$ ls ~/.local/share/nvim/lazy/
lazy.nvim  nvim-lspconfig  nvim-treesitter  plenary.nvim  telescope.nvim
```

再驗證 **treesitter 真的能解析 C、clangd 真的會 attach**（把下面存成 check.lua，headless 跑）：

```lua
vim.cmd("edit /tmp/foo.c")
local ok, parser = pcall(vim.treesitter.get_parser, 0, "c")
print("treesitter c parser: " .. tostring(ok and parser ~= nil))
if ok then print("root node: " .. parser:parse()[1]:root():type()) end
vim.wait(8000, function() return #vim.lsp.get_clients({bufnr=0}) > 0 end, 200)
print("LSP clients attached: " .. #vim.lsp.get_clients({bufnr=0}))
vim.cmd("qa!")
```

真跑輸出（`foo.c` 旁有 `compile_commands.json`，用 `bear -- gcc -c foo.c` 生的）：

```
treesitter c parser: true
root node: translation_unit
LSP clients attached: 1
```

三件事確認了：treesitter 把 C 解析成語法樹（root 是 `translation_unit`）、clangd 這個 LSP client 成功 attach 到 buffer。**你的讀碼機器的地基會動了。**

## 對比與取捨

| 起手方式 | 你懂多少 | 上手速度 | 適合 |
|---|---|---|---|
| **本課：自建骨架（kickstart 精神）** | 每一行都懂 | 慢（要一章章加） | 想**精通**、能 debug 能改 |
| **kickstart.nvim**（單檔起手模板） | 大致懂 | 中 | 想學會自己改的折衷 |
| **LazyVim / NvChad**（完整發行版） | 黑箱 | 最快 | 想開箱即用、不深究 |
| 裸 Vim（無外掛） | 全懂但沒功能 | — | 讀碼不夠力，別用 |

## 踩雷集錦

1. **用 Ubuntu apt 的 Neovim**：`apt install neovim` 常給你 0.6/0.7，太舊——`vim.lsp.enable`、新版 treesitter、一堆外掛都要 0.9+/0.10+。用官方 tarball 或 appimage 拿新版。
2. **抄到舊的 tarball URL**：`nvim-linux64.tar.gz` 已改名，會 404。用 `nvim-linux-x86_64.tar.gz`。
3. **treesitter 用錯分支**：不寫 `branch = "master"` 會抓到重寫的 `main` 分支，經典的 `require("nvim-treesitter.configs").setup()` 就報錯（我第一版就踩到，line 21 直接 crash）。釘 `master`（Ch 13 詳談）。
4. **以為 config 存了就會動**：外掛要 `git clone` 下來、treesitter parser 要**編譯**。第一次啟動要等它裝完，或先 `nvim --headless "+Lazy! sync" +qa` 裝好。網路不通就全裝不了。
5. **clangd attach 了卻跳不到定義**：多半是缺 `compile_commands.json`（clangd 不知道怎麼編你的檔）。Ch 18 專講怎麼生它。attach ≠ 能精準跳轉，是兩回事。

## 進階：再往深一層

- **用獨立的 config 環境測試**：`NVIM_APPNAME=nvim-reading nvim` 或設 `XDG_CONFIG_HOME` 指到別的目錄，可以在不動你主 config 的情況下實驗新設定。這門課的驗證就是用隔離的 XDG 目錄跑的。
- **鎖版本（lazy-lock.json）**：lazy.nvim 會生 `lazy-lock.json` 記錄每個外掛的 commit，把它一起版本控制，換機器 `:Lazy restore` 就是一模一樣的環境。讀碼環境的可重現性和 `reading_code` 釘 commit 是同一個道理。
- **`:checkhealth`**：Neovim 內建的體檢，告訴你哪個外掛缺依賴（缺 node、缺 compiler、LSP 沒裝）。config 出問題第一個跑它（Ch 29 詳談）。

## 動手練習

1. 裝好 nvim 0.10+、把上面的 `init.lua` 放進 `~/.config/nvim/`，第一次啟動讓它自己裝外掛。
2. `nvim --headless "+Lazy! sync" +qa` 跑一次，然後 `ls ~/.local/share/nvim/lazy/` 確認五個外掛都在。
3. 故意把 `branch = "master"` 刪掉，重開 nvim，看它怎麼在 line 21 報 `.configs` 的錯——這就是踩雷 3 的現場。看完把它加回去。

## 本章重點整理

- 精通讀碼操作的前提是**理解你的 config**——所以自建骨架而非用黑箱發行版。
- Neovim 的 config 是一份 **Lua 程式**（`init.lua`），外掛也是 Lua，`lazy.nvim` 幫你湊齊。
- 三大讀碼外掛：**treesitter（結構）+ telescope（搜尋）+ clangd/LSP（語意）**，後面各 Part 深挖。
- 用 **headless 模式**驗證 config 真的動了（外掛裝好、treesitter 解析、LSP attach）——這是本課驗證工具的方式。

## 自我檢核

- [ ] 我知道為什麼這門課選「自建骨架」而不是 LazyVim，能說出黑箱的代價
- [ ] 我能解釋 `init.lua` 開機時從上到下做了哪幾件事
- [ ] 我知道「clangd attach 成功」和「clangd 能精準跳定義」是兩件事，後者還缺什麼
- [ ] 我的 nvim 是 0.10+，不是 apt 的舊版，且五個外掛都裝好了
- [ ] 我知道 treesitter 為什麼要釘 `master` 分支

## 延伸閱讀

### 官方文件

- **[Neovim `:help lua-guide`](https://neovim.io/doc/user/lua-guide.html)**
  - **讀哪裡**：整篇；這是「用 Lua 設定 Neovim」的官方入門，本章 config 的每個 `vim.opt` / `vim.keymap` / `vim.api` 都在這裡有定義
  - **前提**：會一點程式即可，不需先懂 Lua
- **[Neovim `:help lsp`](https://neovim.io/doc/user/lsp.html)**
  - **讀哪裡**：開頭的 overview 與 `vim.lsp.enable`、`LspAttach`；Part 4 會反覆回來，本章先看 attach 的概念
  - **注意**：LSP API 在 0.10→0.11→0.12 有演進，以你裝的版本的 `:help` 為準

### 起手模板 / 工具

- **[kickstart.nvim](https://github.com/nvim-lua/kickstart.nvim)** — TJ DeVries（Neovim 核心開發者）
  - **這是什麼**：單檔、大量註解的讀碼/寫碼起手 config，本課骨架的精神來源；卡住時對照它怎麼設
  - **讀哪裡**：`init.lua` 從上讀到下，每段註解都值得看；特別是 LSP 與 telescope 那兩塊
- **[lazy.nvim 官方文件](https://lazy.folke.dev/)** — folke
  - **讀哪裡**：Plugin Spec 那頁（`keys` / `opts` / `config` / `dependencies` 的意思），本章的外掛宣告全用到
  - **前提**：看得懂 Lua table

環境與 config 骨架就位。下一章我們先講清楚一件事：為什麼是 Neovim？它憑什麼是讀大型程式碼的好機器，而不只是「潮」？

→ [Ch 1 為什麼 Neovim 是讀碼機器](./01-why-neovim-for-reading.md)
