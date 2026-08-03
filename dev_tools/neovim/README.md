# Neovim 讀碼機器：把編輯器打造成讀大型 codebase 的利器

> 給 `reading_code` / `codebase_case_studies` 走過、想要一台真正順手的讀碼機器的工程師——WSL、終端、系統/資安/RE 取向。

`reading_code` 給你攻堅 source 的**方法**，`codebase_case_studies` 給你**目標 codebase**。這門課給你那雙**手**：把 Neovim 配成一台讀 kernel / Lua / nginx / CPython 這種大型 C 專案的機器，並**精通它的操作**。不是抄一個黑箱發行版按爽——是從骨架自己搭起、每一塊都懂、能 debug 能改，讓「跳來跳去讀陌生 code」的摩擦降到最低。

核心是六個工具串成一條流水線：**motion（移動）→ telescope/fzf（找）→ treesitter（看結構）→ clangd/LSP（懂語意）→ gtags/cscope（沒 compile db 時的後備）→ marks/quickfix（外化與組織）**。全程在 WSL 真跑、headless 驗證。

## 為什麼學這個？

- **讀碼是導航密集的活，導航摩擦決定你的速度**：跳定義、跳回、找 reference、開檔對照、全文搜尋——這些操作每天做幾百次。vim 全鍵盤、無滑鼠，把摩擦壓到反射級。省下的不是打字時間，是「維持思路」的認知資源。
- **精通的前提是理解工具**：clangd 跳不到定義時，你要能分辨是 LSP 沒 attach、缺 `compile_commands.json`、還是該退回 gtags。工具卡住時你 debug 不了工具，就無法專心 debug code。這門課讓你懂到能自己修。
- **這是你 `reading_code` SOP 的落地**：偵察、假設驅動、追 data flow、外化——那套方法需要一台趁手的機器才跑得快。這門課就是把方法變成肌肉的載體。

## 先修知識

- **會讀 source + 讀碼 SOP**（`reading_code` 的偵察/追蹤/外化；這門是它的「手」）
- **命令列 + git**（能裝東西、clone、跑 build）
- **C 基礎**（本課主戰場是大型 C 專案；範例以 C 為主）
- 沒有也沒關係的：Vim 經驗（Part 1 從 motion 地基教起）、Lua（config 邊看邊懂）、任何 Neovim 背景

## 課程地圖

### Part 0 — 起步與心智模型（Ch 0–2）
- [Ch 0 環境與讀碼 config 骨架](./00-environment-and-reading-config.md)
- [Ch 1 為什麼 Neovim 是讀碼機器](./01-why-neovim-for-reading.md)
- [Ch 2 Neovim 架構：Lua / LSP client / treesitter](./02-neovim-architecture.md)

### Part 1 — 移動即一切：motion 給讀者（Ch 3–8）
- [Ch 3 模式與基本移動](./03-modes-and-basic-motion.md)
- [Ch 4 text objects：選中一個結構](./04-text-objects.md)
- [Ch 5 跳轉與 jumplist：導航命脈](./05-jumps-and-navigation.md)
- [Ch 6 搜尋：/ ? * # 與 regex](./06-search.md)
- [Ch 7 buffer / window / tab：多檔對照讀](./07-buffers-windows-tabs.md)
- [Ch 8 marks / folds / 捲動：大檔定位](./08-marks-folds-scrolling.md)
- [練習 A：純 motion 限時導航](./practice-a-motion-only-navigation.md)

### Part 2 — 找得到：模糊搜尋與全文（Ch 9–12）
- [Ch 9 telescope 核心](./09-telescope-core.md)
- [Ch 10 ripgrep 深度整合](./10-ripgrep-integration.md)
- [Ch 11 fzf-lua 路線與取捨](./11-fzf-lua.md)
- [Ch 12 quickfix / location list：把搜尋變工作清單](./12-quickfix-location-list.md)
- [練習 B：大 repo 快速定位一個功能](./practice-b-locate-a-feature.md)

### Part 3 — 看得懂結構：treesitter（Ch 13–16）
- [Ch 13 treesitter 基礎與 master/main 分裂](./13-treesitter-basics.md)
- [Ch 14 treesitter textobjects：依結構移動](./14-treesitter-textobjects.md)
- [Ch 15 incremental selection 與 sticky context](./15-treesitter-selection-context.md)
- [Ch 16 treesitter 查詢：InspectTree 與結構搜尋](./16-treesitter-queries.md)
- [練習 C：treesitter 導航複雜檔](./practice-c-treesitter-navigation.md)

### Part 4 — 懂語意：LSP + clangd（Ch 17–22）
- [Ch 17 LSP 與 clangd](./17-lsp-and-clangd.md)
- [Ch 18 compile_commands.json：clangd 精準的前提](./18-compile-commands.md)
- [Ch 19 語意導航：gd / gr / call hierarchy](./19-semantic-navigation.md)
- [Ch 20 symbol 搜尋與 outline](./20-symbol-search-outline.md)
- [Ch 21 clangd 進階與 macro/ifdef 的坑](./21-clangd-advanced.md)
- [Ch 22 診斷與 inlay hints](./22-diagnostics-inlay-hints.md)
- [練習 D：clangd 追一條 call chain](./practice-d-trace-a-call-chain.md)

### Part 5 — 沒有 compile_commands 時：tags 後備（Ch 23–25）
- [Ch 23 為什麼需要 tags 後備](./23-why-tags-fallback.md)
- [Ch 24 ctags 與 tagstack](./24-ctags-tagstack.md)
- [Ch 25 GNU Global（gtags）與 cscope](./25-gtags-cscope.md)
- [練習 E：對編不起來的 kernel 子系統導航](./practice-e-navigate-unbuildable-tree.md)

### Part 6 — 整合成讀碼機器（Ch 26–30）
- [Ch 26 外化：marks / harpoon 標攻堅點](./26-externalizing-marks-harpoon.md)
- [Ch 27 大 codebase 工作流：session 與佈局](./27-large-codebase-workflow.md)
- [Ch 28 把整套串起來：一次完整讀碼流程](./28-full-reading-workflow.md)
- [Ch 29 客製與除錯 config](./29-customize-and-debug-config.md)
- [Ch 30 常見誤區與你的讀碼鍵位手冊](./30-anti-patterns-and-keymap-manual.md)
- [Final Project：用 Neovim 冷啟動攻堅一個大 C 專案](./final-project-cold-read-with-neovim.md)

## 學習方式建議

1. **每章都真的在 nvim 裡按一遍**：讀碼是操作技能，看不會，手會。每個 keymap 找一個真檔按到變反射。
2. **一章加一塊 config**：本課的 config 從 Ch 0 骨架長起來，每個 Part 加它的外掛。到 Part 6 你有一份自己懂的完整 config。
3. **拿真目標練**：別用玩具檔。clone Lua/nginx（`codebase_case_studies` 那批）在上面練，操作才有讀大碼的手感。
4. **卡住先 `:checkhealth` / `:LspInfo`**：工具不動時，先 debug 工具再 debug code——這門課教你怎麼分辨。

## 精選資料庫

每章「延伸閱讀」會指向更具體的 `:help` 小節與資源。

### 必讀基礎

- **Neovim 內建 `:help`**（`:help lua-guide`、`:help lsp`、`:help motion`、`:help treesitter`）
  - 最權威、最新、且離線就有。本課每章延伸閱讀都指向具體的 `:help` 標籤。行為和你這版對不上時，`:help` 是最終仲裁。
- **[kickstart.nvim](https://github.com/nvim-lua/kickstart.nvim)** — TJ DeVries（Neovim 核心）
  - 單檔註解版讀碼 config，本課骨架的精神來源；卡住時對照它。

### 推薦資源

- **[Vim 內建 `vimtutor`](https://neovim.io/doc/user/)**（終端跑 `nvim +Tutor`）
  - Part 1 motion 地基的最佳動手教材，30 分鐘打底。
- **[ThePrimeagen 的 Vim/Neovim 影片](https://www.youtube.com/c/ThePrimeagen)** — 前 Netflix 工程師
  - 大量「怎麼快速在 code 裡移動」的實戰示範，看高手怎麼用 motion 讀碼。

### 讀完本課之後

- 把這台機器接回 `codebase_case_studies` 的六個 codebase 與 `reverse_engineering`——讀 source 用 nvim + clangd/gtags，讀 binary 用 nvim + 反組譯輸出，同一套導航肌肉兩邊通用。
