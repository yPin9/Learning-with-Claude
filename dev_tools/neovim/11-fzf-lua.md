# Ch 11 — fzf-lua 路線與取捨

> **目標**：知道 telescope 不是唯一的搜尋前端。fzf-lua 是另一條路——底層用 fzf 這個 C 程式，更快更輕，在超大結果集（kernel 那種規模）比 telescope 順，代價是 UI 客製化不如 telescope 深。學完你能講清 telescope vs fzf-lua vs fzf.vim 的取捨、知道有人為什麼在超大 repo 選 fzf-lua、有一份等價操作對照與可選的 config 片段，並清楚**何時該切換**。

> **環境**：Neovim v0.12.4，WSL2 / Ubuntu，系統已裝 `fzf`（fzf-lua 的底層依賴）。本章不強制你裝 fzf-lua——telescope 已夠用，這章是讓你知道另一條路存在、遇到 telescope 頓的時候有得換。給的 config 片段可選裝。

## 為什麼需要這個？

你可能想：telescope 都學會了（Ch 9–10），為什麼還要知道別的？

因為 telescope 有個真實的痛點：**在超大結果集會頓**。它的 UI 全用 Lua 畫，previewer 也是 Lua 驅動。小專案感覺不出來，但當你 `find_files` 在有幾萬個檔的 kernel 上、或 `live_grep` 一個泛 pattern 炸出上萬命中時，telescope 的每次重繪、每次預覽都要 Lua 處理一大批資料，會有肉眼可見的延遲。裝了 fzf-native（Ch 9）能救排序那段，但 UI 繪製與 previewer 那層的成本還在。

這不是 telescope 寫得爛——是它「功能豐富、深度可客製」與「純 Lua 實作」這組取捨的必然結果。而 **fzf-lua 選了另一組取捨**：把重活外包給 fzf（一個成熟、極快的 C 命令列模糊搜尋器），Lua 只當薄薄一層膠水。結果是超大結果集明顯更順，代價是客製化空間小一點。

讀碼者要知道這條路，理由很實際：**當你的目標 codebase 大到 telescope 開始頓，你需要一個立即可換的順手替代**，而不是卡在那邊忍。這章讓你有這個選項，並知道怎麼判斷該不該切。

## 先建立直覺：重活外包給 C

```
   telescope                      fzf-lua
   ─────────────                 ─────────────
   finder（Lua）                  finder（Lua，跑 rg/fd）
   sorter（Lua，或 fzf-native）    │
   UI 繪製（Lua）                  └─► 把候選丟給 fzf 這個 C 程式
   previewer（Lua）                     ├ fzf 負責：模糊過濾 + UI + 選取
                                        └ previewer（可搭 bat/rg 外部程式）
   全在 nvim 進程內用 Lua 跑        重活在 fzf 這個外部 C 進程跑
```

關鍵差別：**telescope 什麼都在 nvim 的 Lua 裡做，fzf-lua 把「過濾 + 顯示 + 選取」整包外包給 fzf 這個 C 程式**。fzf 是一個獨立、極度優化過的模糊搜尋器（很多人在 shell 用 `Ctrl-R` 搜歷史指令就是它），處理幾十萬行候選面不改色。fzf-lua 就是把 fzf 接進 nvim、加上讀碼常用的 picker（find_files、live_grep、buffers…介面跟 telescope 幾乎一樣）。

代價在客製化。telescope 的每個零件（finder/sorter/previewer/mappings）都是 Lua、你能任意改、生態有海量 extension（各種第三方 picker）。fzf-lua 因為重活在 fzf 那個外部程式裡，客製化受限於 fzf 能吃什麼旗標，extension 生態也小。**telescope = 深度可客製 + 生態大 + 大集合會頓；fzf-lua = 快 + 輕 + 客製化淺。**

## fzf.vim：更老的那條路（先知道有它）

在 fzf-lua 之前，接 fzf 進 (Neo)vim 的主流是 **fzf.vim**（junegunn 作者，就是 fzf 本人寫的）。它用 Vimscript，靠 fzf 官方外掛（`fzf` 的 vim 綁定）跑。特點：極穩、極輕、命令是 `:Files` `:Rg` `:Buffers` 這種。缺點：Vimscript 寫的、previewer 與 nvim 整合不如 fzf-lua 深（fzf.vim 的預覽跳出來是 fzf 自己的視窗，不是 nvim buffer），跟 Lua config 生態格格不入。

現在的建議：**新專案別選 fzf.vim**。要 fzf 的速度就用 fzf-lua（Lua、整合更好、維護更活）；要功能與生態就用 telescope。fzf.vim 你會在很多老 dotfiles、老教材裡看到，知道它是「fzf 進 vim 的第一代、Vimscript 版」即可，不用學。

## telescope vs fzf-lua 取捨表

| 面向 | telescope | fzf-lua |
|---|---|---|
| **底層過濾** | Lua sorter（可加 fzf-native 的 C sorter） | fzf（C 程式）全包 |
| **超大結果集（幾萬檔/上萬命中）** | 會頓，即使有 fzf-native UI 層仍慢 | 明顯更順，fzf 天生為此設計 |
| **客製化深度** | 深——每個零件都是 Lua 可改 | 淺——受限於 fzf 能吃的旗標 |
| **extension 生態** | 大——海量第三方 picker | 小——內建夠用但第三方少 |
| **previewer** | Lua 驅動，功能多但大檔慢 | 可搭 bat/rg，輕快 |
| **相依** | plenary（+ 建議 fzf-native 編譯） | 系統要有 `fzf` 命令 + rg/fd |
| **UI 一致性** | 全 nvim 內、風格統一 | 大致統一，細節有 fzf 味 |
| **上手** | 生態文件多、教材多 | 介面像 telescope，好上手 |

一句話決策：**預設用 telescope（生態、客製、教材都贏）；當你的目標 codebase 大到 telescope 明顯頓、或你要極簡快速的環境，切 fzf-lua。**

## 為什麼有人在大 repo 選 fzf-lua

具體場景，讀碼者最有感的：

- **`find_files` 在 kernel/chromium 這種幾萬檔的 repo**：telescope 列全部候選、Lua 逐項算分、UI 重繪，打字會黏。fzf 是為「百萬行 candidate 即時過濾」而生的，同樣操作無感延遲。
- **`live_grep` 泛 pattern 炸出上萬命中**：telescope 把上萬行灌進 Lua 排序 + 繪製 + previewer，卡。fzf-lua 讓 fzf 處理那批，順很多。
- **遠端 / 資源受限機器**：SSH 進一台弱機讀碼，telescope 的 Lua 開銷更明顯。fzf-lua 把重活丟給 C 進程，輕。
- **偏好極簡**：有人就是不想要 telescope 那麼多零件與設定，fzf-lua + fzf 這組更「Unix 味」——一個做一件事的 C 工具加一層膠水。

反過來，**不該為了「聽說更快」就無腦切**。多數讀碼的專案沒大到讓 telescope 頓，而 telescope 的生態、客製、教材優勢是實打實的。切換的觸發點是「telescope 在我這個 repo 真的頓了」，不是信仰。

## 等價操作對照

fzf-lua 的介面刻意做得像 telescope，你 Ch 9 學的操作幾乎無痛遷移：

| 動作 | telescope | fzf-lua |
|---|---|---|
| 找檔名 | `:Telescope find_files` | `:FzfLua files` |
| 全文即時搜 | `:Telescope live_grep` | `:FzfLua live_grep` |
| 切 buffer | `:Telescope buffers` | `:FzfLua buffers` |
| 搜游標下的字 | `:Telescope grep_string` | `:FzfLua grep_cword` |
| 最近檔 | `:Telescope oldfiles` | `:FzfLua oldfiles` |
| 回上次搜尋 | `:Telescope resume` | `:FzfLua resume` |
| LSP references | `:Telescope lsp_references` | `:FzfLua lsp_references` |

picker 內鍵位也幾乎一樣（`<C-n>/<C-p>` 選、`<CR>` 開、`<C-v>`/`<C-x>` 分割、`<C-q>` 送 quickfix、`<Tab>` 多選）——**因為這些是 fzf 本來就有的鍵位慣例，telescope 當初也是抄 fzf 的**。所以你切過去手不用重新訓練。差別在客製化那些鍵時的寫法（fzf-lua 用 fzf 的 `--bind` 語法，telescope 用 Lua actions）。

## 可選：把 fzf-lua 加進 config（不覆蓋 telescope）

你可以兩個都裝，用不同 leader 前綴分開，需要時切。**這章不強制**——telescope 已夠用，這片段給想試 fzf-lua 的人。把它加進 Ch 0 的 `require("lazy").setup({...})` 裡（telescope 那塊保留）：

```lua
-- 可選：fzf-lua，另一條搜尋路線（掛在 <leader>z 前綴，不搶 telescope 的 <leader>f）
{ "ibhagwan/fzf-lua",
  dependencies = { "nvim-tree/nvim-web-devicons" },  -- 圖示可省
  config = function()
    require("fzf-lua").setup({ "default" })  -- 用內建的 default profile
  end,
  keys = {
    { "<leader>zf", "<cmd>FzfLua files<cr>" },       -- 找檔（大 repo 更順）
    { "<leader>zg", "<cmd>FzfLua live_grep<cr>" },   -- 全文搜
    { "<leader>zb", "<cmd>FzfLua buffers<cr>" },     -- 切 buffer
    { "<leader>zw", "<cmd>FzfLua grep_cword<cr>" },  -- 搜游標下的字
    { "<leader>zr", "<cmd>FzfLua resume<cr>" },      -- 回上次
  } },
```

> **前提**：系統要有 `fzf` 命令（`which fzf`；Ch 0 的裝機清單已裝）。fzf-lua 沒有 `fzf` 就跑不起來，跟 telescope 沒有 rg 一樣。裝好後 `:FzfLua files` 能開就代表通了；`:checkhealth fzf-lua` 會列缺什麼。

兩套並存的用法：日常小專案用 telescope（`<leader>f*`，功能多），開到一個大到 telescope 頓的 repo 就改按 `<leader>z*` 走 fzf-lua。**同一個 nvim，兩把搜尋前端，看 repo 大小切。**

## 鍵位表（可選 fzf-lua 那套）

| 模式 | 按鍵 | 作用 |
|---|---|---|
| Normal | `<leader>zf` | fzf-lua 找檔（大 repo 更順） |
| Normal | `<leader>zg` | fzf-lua 全文即時搜 |
| Normal | `<leader>zb` | fzf-lua 切 buffer |
| Normal | `<leader>zw` | fzf-lua 搜游標下的字 |
| Normal | `<leader>zr` | fzf-lua 回上次搜尋 |
| picker | `<C-n>/<C-p>` | 選（同 telescope，因為都學 fzf） |
| picker | `<CR>` / `<C-v>` / `<C-x>` | 開 / 垂直分割 / 水平分割 |
| picker | `<Tab>` / `<C-q>` | 多選 / 送 quickfix |

## 對比與取捨

（核心取捨表見上方「telescope vs fzf-lua 取捨表」。這裡補**該選哪個**的情境判斷）

| 你的情況 | 建議 |
|---|---|
| 一般專案（幾百到幾千檔）、想要生態與客製 | telescope（本課主線） |
| 目標是 kernel/chromium 這種幾萬檔巨物、telescope 明顯頓 | fzf-lua |
| SSH 進弱機 / 極簡主義 | fzf-lua |
| 想兩個都留、看 repo 切 | 兩個都裝，不同 leader 前綴 |
| 維護老 dotfiles 看到 fzf.vim | 知道是 fzf 進 vim 第一代即可，別新學 |

## 踩雷集錦

1. **裝了 fzf-lua 卻報「fzf 找不到」**：fzf-lua 硬依賴系統的 `fzf` 命令（不是那個 C 排序器 fzf-native，是完整的 fzf 程式）。`which fzf` 確認，沒有就 `apt install fzf`。這跟 telescope-fzf-native（只是排序器 `.so`）是**兩個不同東西**，別搞混。
2. **以為 fzf-lua 一定比 telescope 快就無腦切**：小專案兩者都無感，你卻失去 telescope 的生態與客製。切換要有理由（telescope 在你這 repo 真頓），不是信仰。
3. **fzf-native 與 fzf-lua 搞混**：`telescope-fzf-native` 是給 **telescope 用的 C 排序器**（Ch 9 裝的 `.so`）；`fzf-lua` 是**獨立的搜尋前端**（用完整 fzf 程式）。名字都有 fzf，角色完全不同。
4. **兩套 keymap 打架**：如果 fzf-lua 也綁 `<leader>f*`，會跟 telescope 撞。用不同前綴（本章用 `<leader>z*`）分開，或乾脆只留一套。
5. **fzf-lua 的 previewer 沒圖示 / 沒語法高亮**：previewer 靠外部 `bat`（帶語法高亮的 cat）之類。沒裝 bat 預覽是純文字。要漂亮預覽補裝 `bat`，但讀碼夠用不必強求。

## 進階：再往深一層

- **fzf 的過濾語法**：fzf-lua 繼承 fzf 的精確語法——`'foo`（精確含 foo）、`^foo`（開頭）、`foo$`（結尾）、`!foo`（排除）、`foo | bar`（或）。telescope 裝 fzf-native 後也吃這套（因為就是同個演算法）。這套過濾語法讀碼收窄結果極有用。
- **fzf-lua 的 profiles**：`setup({ "fzf-native" })`、`{ "max-perf" }` 等預設 profile 調不同的速度/外觀取捨。大 repo 用 `max-perf` 關掉重的 previewer。
- **skim（sk）**：另一個 Rust 寫的 fzf 相容替代，某些場景更快，fzf-lua 也能接。知道有這選項即可。
- **telescope 也能調快**：切 fzf-lua 前，先試 telescope 的 `find_files` 加 `find_command` 用更快的 fd、或關掉 previewer（`previewer = false`）、或設 `file_ignore_patterns` 砍候選。有時調一調 telescope 就夠，不必換前端。

## 本章重點整理

- fzf-lua 是 telescope 的替代前端：**把過濾+顯示+選取外包給 fzf 這個 C 程式**，超大結果集更順，代價是客製化淺、生態小。
- 取捨一句話：**telescope = 深客製 + 大生態 + 大集合會頓；fzf-lua = 快 + 輕 + 客製淺**。預設 telescope，telescope 在你的 repo 真頓了才切 fzf-lua。
- **fzf.vim** 是 fzf 進 vim 的第一代（Vimscript），新專案別選；知道老 dotfiles 裡的它是什麼即可。
- 操作幾乎無痛遷移——fzf-lua 的 picker 介面與鍵位刻意像 telescope（因為兩者都學 fzf 的鍵位慣例）。
- 別把三個 fzf 搞混：**fzf**（C 命令列程式）、**telescope-fzf-native**（給 telescope 的 C 排序器 `.so`）、**fzf-lua**（獨立搜尋前端，用完整 fzf）。

## 自我檢核

- [ ] 我能講清 telescope 和 fzf-lua 的核心取捨（Lua 全包 vs 外包給 C），以及各自的代價
- [ ] 我知道什麼情況該從 telescope 切到 fzf-lua（不是「聽說更快」）
- [ ] 我能分辨 fzf、telescope-fzf-native、fzf-lua 三個「fzf」各是什麼、角色差在哪
- [ ] 我知道 fzf.vim 是什麼、為什麼新專案不選它
- [ ] 我知道切前端前可以先試著調快 telescope（換 fd、關 previewer、砍候選）

## 延伸閱讀

- **[fzf-lua README](https://github.com/ibhagwan/fzf-lua)** — ibhagwan
  - **讀哪裡**：`Commands` 那節（對照 telescope 的等價 picker）、`Profiles`（不同速度/外觀取捨）、`Dependencies`（要哪些外部程式）
- **[fzf README + man fzf](https://github.com/junegunn/fzf)** — junegunn（fzf 作者）
  - **讀哪裡**：`Search syntax` 那段（`'foo` / `^foo` / `!foo` / `|` 的過濾語法）——這套 telescope 裝 fzf-native 後也通用，讀碼收窄結果的利器
- **Neovim `:help fzf`（若裝了 fzf.vim）/ fzf-lua 的 `:checkhealth fzf-lua`**
  - **讀哪裡**：checkhealth 列出缺的外部依賴（fzf/rg/fd/bat），裝不起來時第一個跑它
- **對照本課**：Ch 9（telescope 四件套，fzf-lua 是它的替代前端）；Ch 10（rg 是兩個前端共同的全文搜引擎，換前端 rg 依然是底層）

telescope 或 fzf-lua 讓你「找得到」，但找到一堆命中之後呢？逐個 `<CR>` 開、開了忘記還有哪些沒看——這不叫組織攻堅。下一章把 Part 2 的搜尋能力收斂：quickfix / location list，把一次搜尋的結果變成一份可導航的工作清單，`:cnext` 一項項看過去，一個都不漏。

→ [Ch 12 quickfix / location list：把搜尋變工作清單](./12-quickfix-location-list.md)
