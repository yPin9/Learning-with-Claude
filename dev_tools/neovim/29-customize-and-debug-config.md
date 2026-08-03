# Ch 29 — 客製與除錯 config

> **目標**：精通的收尾。到這一章，你那份 `init.lua` 的每一塊都是前面某一章加的——options、lazy bootstrap、treesitter、telescope、clangd、harpoon、persistence。本章做三件事：**回頭通讀一遍整份 config**（每一行你現在都懂了）、**學會按需改 keymap**（怎麼加/改一個 mapping）、以及最重要的——**config 出問題怎麼 debug**（`:checkhealth` 體檢、`:LspInfo` 看 LSP、`:Lazy` 看外掛、`:messages` 看錯誤、minimal repro 二分找兇手）。讀完你不只有一台讀碼機器，你**能自己維護它**——它壞了你修得好，你要改你改得動。這是「精通操作」和「抄一個黑箱發行版」的分水嶺。

> **環境**：Neovim v0.12.4，WSL2 / Ubuntu。本章的 `:checkhealth`、`lazy-lock.json` 輸出都是在隔離 XDG 環境 headless 真跑出來的。

## 為什麼需要這個？

Ch 0 講過這門課選「自建骨架」而非 LazyVim 的理由：**工具卡住時你 debug 不了工具，就無法專心 debug code**。這一章就是兌現那句話——把「怎麼 debug 你的工具」講清楚。

真實場景：你讀 kernel 讀得正順，突然 `gd` 不動了。可能是 clangd crash、可能是你昨天改 config 打錯字、可能是某個外掛更新後 API 變了。如果你用的是黑箱發行版，你只能 google「LazyVim gd not working」然後在別人的一萬行配置裡瞎找。但你這份 config 是自己搭的、每塊都懂——你 `:LspInfo` 看 clangd 狀態、`:messages` 看錯誤、`:Lazy` 看外掛，五分鐘定位兇手。**這就是自建的回報：你的機器你維護得了。**

## 先建立直覺：三個 debug 入口對應三類問題

```
   config 出問題，症狀 → 該跑哪個
 ──────────────────────────────────────────────
  「整體怪怪的、不確定哪壞」   →  :checkhealth  （全身體檢）
  「gd/gr 沒反應、LSP 相關」    →  :LspInfo      （LSP 專科）
  「某外掛沒載入/報錯」         →  :Lazy         （外掛管理器）
  「剛剛閃過一個錯誤訊息」      →  :messages     （看歷史訊息）
  「不知道哪個 config 造成的」  →  minimal repro （二分法找兇手）
```

這五個是你的診斷工具箱。下面先通讀 config，再逐一講這五個。

## 第一部分：通讀你的 config

打開 `~/.config/nvim/init.lua`。到這裡它每一塊你都認得——我們快速走一遍，確認你能說出每塊在幹嘛：

```lua
-- ① options 與 leader（Ch 0）
vim.g.mapleader = " "              -- leader = 空白，讀碼快捷都掛它下面
vim.opt.number = true
vim.opt.relativenumber = true      -- 相對行號：5j/12k 好算
vim.opt.scrolloff = 8              -- 游標上下留 8 行看上下文

-- ② bootstrap lazy.nvim（Ch 0）—— 第一次啟動自己 clone
local lazypath = vim.fn.stdpath("data") .. "/lazy/lazy.nvim"
if not vim.uv.fs_stat(lazypath) then
  vim.fn.system({ "git", "clone", "--filter=blob:none",
    "https://github.com/folke/lazy.nvim.git", "--branch=stable", lazypath })
end
vim.opt.rtp:prepend(lazypath)

-- ③ 外掛宣告（各 Part 累加）
require("lazy").setup({
  { "nvim-treesitter/nvim-treesitter", branch = "master", ... },  -- Part 3：看結構
  { "nvim-telescope/telescope.nvim", ... },                       -- Part 2：找東西
  { "neovim/nvim-lspconfig" },                                    -- Part 4：懂語意
  { "ThePrimeagen/harpoon", branch = "harpoon2", ... },           -- Ch 26：釘熱點
  { "folke/persistence.nvim", ... },                              -- Ch 27：自動 session
}, { rocks = { enabled = false } })

-- ④ clangd + 讀碼鍵位（Ch 0/17-19）
vim.lsp.enable("clangd")
vim.api.nvim_create_autocmd("LspAttach", {
  callback = function(ev)
    local o = { buffer = ev.buf }
    vim.keymap.set("n", "gd", vim.lsp.buf.definition, o)   -- 跳定義
    vim.keymap.set("n", "gr", vim.lsp.buf.references, o)   -- 找引用
    vim.keymap.set("n", "K",  vim.lsp.buf.hover, o)        -- 看型別/文件
    vim.keymap.set("n", "<leader>ds", vim.lsp.buf.document_symbol, o)
  end,
})
```

四塊：**options → bootstrap → 外掛宣告 → LSP 與鍵位**。這是本課從 Ch 0 長到 Ch 27 的完整成果，沒有一行是你不懂的黑箱。**這就是自建骨架的終點**——回頭看，整份 config 透明可讀。

> 順帶一個維護心法：config 到一定大小就該**拆檔**。`init.lua` 塞五百行會難維護。慣例是 `lua/plugins/` 一個外掛一個檔、`lua/config/keymaps.lua` 放鍵位。本課刻意保持單檔（教學透明），但你自己長大後拆檔是對的。拆法見 kickstart 進階版與 lazy 的 `import` 機制。

## 第二部分：按需改 keymap

「精通」的一個具體標誌：你能隨手加/改一個 mapping。核心 API 是 `vim.keymap.set`：

```lua
vim.keymap.set(模式, 按鍵, 動作, 選項)
```

- **模式**：`"n"`（normal）、`"i"`（insert）、`"v"`（visual）、`"x"`（visual block）、`"t"`（terminal）；多個模式用 table `{"n", "v"}`。
- **按鍵**：`"<leader>x"`、`"gd"`、`"<C-e>"`（Ctrl-e）、`"<A-j>"`（Alt-j）。
- **動作**：一個 Lua 函式（`function() ... end`）或一個字串命令（`"<cmd>Telescope find_files<cr>"`）。
- **選項**：`{ desc = "說明", buffer = 0, silent = true }`。`desc` 讓 which-key 之類的能顯示說明；`buffer` 限定只在某 buffer。

實例——加幾個讀碼常用的：

```lua
-- 一鍵開 notes.md（Ch 26 外化）
vim.keymap.set("n", "<leader>n", "<cmd>edit notes.md<cr>", { desc = "開讀碼筆記" })

-- 讓 Ctrl-o/Ctrl-i（jumplist）配一個「置中」——跳完自動 zz 讓目標在螢幕中央
vim.keymap.set("n", "<C-o>", "<C-o>zz", { desc = "倒退並置中" })

-- visual 模式選中一段，用 J/K 上下移動整段（讀碼重排對照時偶用）
vim.keymap.set("v", "J", ":m '>+1<cr>gv=gv")
vim.keymap.set("v", "K", ":m '<-2<cr>gv=gv")
```

**改一個現有 mapping**：直接 `vim.keymap.set` 同一個鍵覆蓋。例如你覺得 `<leader>ff` 該找當前目錄而非 project root：

```lua
-- 覆蓋 Ch 0 骨架的 <leader>ff
vim.keymap.set("n", "<leader>ff", "<cmd>Telescope find_files cwd=%:p:h<cr>")
```

**查一個鍵綁到哪**（debug mapping 衝突）：

```
:verbose nmap <leader>ff     顯示 <leader>ff 綁到什麼、在哪個檔設的
:map <leader>                列出所有 leader 開頭的 mapping
```

`:verbose nmap` 的 `verbose` 會告訴你「這個 mapping 在哪個檔的第幾行設的」——**兩個 config 綁同一個鍵、後設的贏**，這個命令幫你抓「為什麼我的鍵被別的東西搶了」。

## 第三部分：debug config 的五個工具

### `:checkhealth` — 全身體檢

最該先跑的。它對每個子系統做檢查，報告缺什麼、哪裡不對。headless 真跑一次看它檢查哪些項：

```
$ nvim --headless "+checkhealth" +qa
checking lazy ... lspconfig ... nvim-treesitter ... vim.lsp ... vim.provider ... vim.treesitter
```

各節的體檢結果（真跑輸出，`✅` 是過、`⚠️` 是警告）：

```
lazy:            ✅
lspconfig:       ✅
nvim-treesitter: ✅
vim.lsp:         ✅
vim.treesitter:  ✅
```

treesitter 那節細節（真跑，確認 C parser 的工具鏈齊全）：

```
- ✅ OK `cc` executable found ... Version: cc (Ubuntu 11.4.0) 11.4.0
- ✅ OK Neovim compiled with tree-sitter runtime ABI version 15 (required >=13)
```

**重點是學會讀 `⚠️` 警告——分辨哪些要修、哪些能忽略**。真跑常見的無害警告：

```
- ⚠️ WARNING No clipboard tool found（沒裝剪貼簿工具，WSL 常見，不影響讀碼）
- ⚠️ WARNING Missing "neovim" npm package（Node provider，只有寫 JS 外掛才需要）
- ⚠️ WARNING "Neovim::Ext" cpan module is not installed（Perl provider，幾乎沒人用）
```

這三個都可以忽略——它們是「可選 provider」沒裝。**要修的警告**長不一樣：`clangd not found`、`tree-sitter CLI missing`、`parser for C not installed`——這種才影響讀碼。checkhealth 的技能不是「跑它」，是**分辨哪些 `⚠️` 該理**。無害的可以 `let g:loaded_node_provider = 0` 之類關掉少看幾行。

只體檢某個子系統：`:checkhealth vim.lsp`、`:checkhealth nvim-treesitter`——縮小範圍。

### `:LspInfo` — LSP 專科

`gd`/`gr` 沒反應時第一個跑。它顯示**當前 buffer 的 LSP 狀態**：哪些 client attach 了、root 目錄在哪、有沒有 compile db。真跑 `:checkhealth vim.lsp` 看到 clangd 的配置（`:LspInfo` 是它的互動版）：

```
- clangd:
  - cmd: { "clangd" }
  - root_markers: { ".clangd", "compile_commands.json", "compile_flags.txt", ".git" }
```

`:LspInfo` 幫你判斷 Ch 28 那個經典問題——**clangd 為什麼半殘**：

- **沒 client attach**：`:LspInfo` 顯示「0 clients」→ clangd 沒啟動。查 clangd 有沒有裝（`:checkhealth vim.lsp` 會報 `clangd not found`）、檔案 filetype 對不對。
- **attach 了但 root 不對**：clangd 找不到 `compile_commands.json`（那些 `root_markers` 一個都沒命中）→ 它退化成「只看單檔」模式，`gd` 半殘。解法是 Ch 18 生 compile db。
- **attach 了、root 對，但還是慢**：clangd 在背景索引（`:LspInfo` 或 `:LspLog` 能看進度）→ 等它。大 repo 首次索引要幾分鐘。

`:LspLog` 看 clangd 的原始日誌——crash 或報錯的細節在裡面。

### `:Lazy` — 外掛管理器

某個外掛沒作用時跑它。`:Lazy` 開一個面板，列出每個外掛的狀態：裝好沒（`●` vs `○`）、載入沒、有沒有更新、有沒有錯誤。面板裡的命令：

```
:Lazy         開面板
:Lazy sync    裝新宣告的 + 更新 + 清掉移除的
:Lazy update  只更新
:Lazy restore 還原到 lazy-lock.json 記錄的版本（版本回退！）
:Lazy log     看最近的更新日誌
:Lazy health  外掛層的體檢
```

常見用途：你加了 harpoon 的宣告但 `<leader>a` 沒反應——`:Lazy` 看 harpoon 是不是「not installed」（沒 `:Lazy sync`）或「loaded: false」（lazy-load 條件沒觸發）。

### `:messages` — 看閃過的錯誤

啟動時閃過一個紅色錯誤、來不及看清？`:messages` 顯示所有歷史訊息，包括啟動時的錯誤。config 打錯字（Lua 語法錯、外掛名拼錯）通常在這裡留下 traceback。搭配：

```
:messages          看歷史訊息
:mes clear         清空
```

啟動就報錯還可以命令列看：`nvim --headless "+messages" +qa` 或載入你的 init 看它 print 什麼。

### minimal repro — 二分法找兇手

上面四個都沒定位到，用終極手段：**最小重現**。你的 config 兩百行，某個地方讓 `gd` 壞了但不知道哪。做法是**二分**：

1. 建一個 `min.lua`，只放「重現問題所需的最少 config」（bootstrap + 出問題的那個外掛 + 相關鍵位）。
2. 用它啟動：`nvim --clean -u min.lua`（`--clean` 忽略你的正常 config）。
3. 問題**還在** → 兇手在這最小集合裡，繼續砍。問題**消失** → 兇手在你砍掉的部分，把它加回來一半再測。

這是 debug 的通用二分法：每次砍一半，log₂ 次就定位。回報外掛 bug 時，維護者也會要你給 minimal repro——因為你的兩百行 config 他沒法重現。**「能生一份 minimal repro」是 debug 能力的分水嶺。**

隔離測試不動主 config 的技巧（Ch 0 進階提過）：

```bash
# 用獨立的 config 名，完全不碰你的正常 config
NVIM_APPNAME=nvim-test nvim
# 或指定隔離的 XDG（本課驗證就是這樣跑的）
XDG_CONFIG_HOME=/tmp/test/config nvim
```

## 第四部分：lazy-lock.json — 可重現的環境

Ch 0 提過但值得展開。`:Lazy sync` 會生一個 `lazy-lock.json`，記錄**每個外掛當前的確切 commit**。真跑看它（本課驗證環境生的）：

```json
{
  "harpoon":         { "branch": "harpoon2", "commit": "87b1a350..." },
  "nvim-treesitter": { "branch": "master",   "commit": "cf12346a..." },
  "persistence.nvim":{ "branch": "main",     "commit": "b20b2a78..." },
  "telescope.nvim":  { "branch": "0.1.x",    "commit": "a0bbec21..." }
}
```

看那個 `harpoon` 記了 `branch: harpoon2`——你 config 釘的分支忠實反映在 lock 裡。**把 lazy-lock.json 一起 commit 進 git**，換機器 `:Lazy restore` 就裝回一模一樣的 commit。這跟 `reading_code` 攻堅時「釘專案的 commit」是同一個道理——**環境的可重現性**。

它還救你一種災難：某天 `:Lazy update` 後某外掛新版壞了你的流程——`:Lazy restore` 一鍵回到 lock 記錄的舊版，先恢復生產力，再慢慢查新版怎麼壞的。**lock 檔是你的還原點。**

## 鍵位表 / 命令表

| 模式 | 命令 | 作用 | 什麼時候用 |
|---|---|---|---|
| c | `:checkhealth` | 全身體檢 | 整體怪、不知哪壞 |
| c | `:checkhealth vim.lsp` | 只體檢 LSP | 縮小到 LSP |
| c | `:LspInfo` | 當前 buffer 的 LSP 狀態 | gd/gr 沒反應 |
| c | `:LspLog` | clangd 原始日誌 | LSP crash/報錯細節 |
| c | `:Lazy` | 外掛管理面板 | 外掛沒作用 |
| c | `:Lazy sync` | 裝/更新/清外掛 | 加了新外掛沒生效 |
| c | `:Lazy restore` | 還原到 lock 版本 | 更新後壞了，回退 |
| c | `:messages` | 歷史訊息/錯誤 | 閃過的錯誤沒看清 |
| c | `:verbose nmap <鍵>` | 查某鍵綁到哪、在哪設的 | mapping 衝突 |
| c | `:map <leader>` | 列所有 leader mapping | 查我綁了什麼 |
| bash | `nvim --clean -u min.lua` | 最小重現 | 二分找兇手 |
| bash | `NVIM_APPNAME=nvim-test nvim` | 隔離的 config 環境 | 不動主 config 測試 |

## 對比與取捨

| 症狀 | 先跑 | 再跑 | 最後手段 |
|---|---|---|---|
| 整體行為怪 | `:checkhealth` | `:messages` | minimal repro |
| `gd`/`gr` 沒反應 | `:LspInfo` | `:LspLog` / `:checkhealth vim.lsp` | 查 compile db（Ch 18） |
| 某外掛沒作用 | `:Lazy`（狀態） | `:Lazy log` | minimal repro |
| 某鍵沒反應/按錯事 | `:verbose nmap <鍵>` | 查是否被覆蓋 | — |
| 改 config 後啟動報錯 | `:messages` | 註解掉剛改的 | `nvim --clean -u min.lua` |
| 更新後壞了 | `:Lazy restore` | `:Lazy log` 看改了什麼 | 釘舊 commit |

## 踩雷集錦

1. **一看到 `⚠️` 就慌著修。** checkhealth 的多數警告是「可選 provider 沒裝」（Node/Perl/Ruby provider、剪貼簿工具）——讀 C 專案根本用不到。新手看到滿螢幕黃色警告以為機器壞了，花一小時裝一堆用不到的東西。**先分辨這個警告影響不影響讀碼**：`clangd not found` 要修，`Missing neovim npm package` 忽略。checkhealth 的技能是「讀懂並篩選警告」，不是「消滅所有黃色」。

2. **`gd` 不動就重裝 clangd/砍 config。** 症狀是 LSP，該先 `:LspInfo` 精準診斷——十有八九是「attach 了但缺 compile db」（clangd 半殘、不是壞），生個 `compile_commands.json` 就好（Ch 18）。跳過診斷直接重裝/砍 config，可能把好的東西也弄壞，且沒學到「怎麼判斷」。

3. **改了 config 沒重載就以為沒生效。** 改完 `init.lua` 存檔，nvim **不會自動重載**——要 `:source $MYVIMRC` 或重開 nvim。有些東西（外掛 setup、autocmd）即使 `:source` 也可能殘留舊狀態，最保險是重開。改了鍵位沒反應，先確認你重載了。

4. **回報 bug 時甩兩百行 config。** 你的完整 config 維護者沒法重現、也沒義務讀。正確做法是**生 minimal repro**（能重現問題的最少 config）。生 repro 的過程本身常常就讓你自己找到兇手了——砍到某一塊問題消失，兇手現形。不會生 repro，你的 issue 會被關「cannot reproduce」。

5. **不 commit lazy-lock.json，環境不可重現。** 你換了台機器 `:Lazy sync`，抓到的是外掛的**最新** commit——可能跟你原本的不一樣、行為變了、甚至壞了。lock 檔記的是「你驗證過能跑的確切版本」，commit 它、`:Lazy restore` 才能複製出一樣的環境。這跟你讀碼釘專案 commit 是同一個紀律。

## 進階：再往深一層

- **`:Lazy profile` 看啟動耗時**：外掛裝多了 nvim 啟動變慢，`:Lazy profile` 列出每個外掛的載入時間，抓出拖慢的兇手。讀碼機器該追求快啟動——反模式之一就是「裝過多外掛拖慢啟動」（Ch 30）。搭配 `nvim --startuptime /tmp/st.log` 看更底層的啟動時間分解。

- **lazy-load 是雙面刃**：lazy.nvim 預設「用到才載」（`event`/`keys`/`cmd` 觸發），啟動快。但這也是「外掛沒作用」的常見原因——lazy-load 條件沒觸發，外掛根本沒載。`:Lazy` 面板看到某外掛 `loaded: false` 可能是正常（還沒觸發）也可能是條件寫錯。懂 lazy-load 機制才能分辨。

- **conditionally 啟用 config**：進階 config 常按環境切——`if vim.fn.executable("clangd") == 1 then ...`（沒 clangd 就不啟用）、依專案根載不同設定。這讓一份 config 在不同機器/專案都能跑。是 config 從「能跑」到「健壯」的一步。

- **把 debug 能力接回讀碼**：debug config 和 debug code 是**同一種技能**——縮小範圍、二分、看日誌、最小重現。你在這章學的 minimal repro，跟 `reading_code` 找 bug 時「寫最小 harness 觸發它」是同構的。工具卡住時你 debug 工具的能力，回頭讓你 debug code 也更強。

## 本章重點整理

- 到這章你的 config 每塊都懂了：options → bootstrap → 外掛宣告 → LSP 與鍵位，四塊透明可讀。這是自建骨架的回報。
- 改 keymap 靠 `vim.keymap.set(模式, 鍵, 動作, 選項)`；`:verbose nmap <鍵>` 查衝突。
- debug 五工具：`:checkhealth`（全身體檢）、`:LspInfo`（LSP 專科）、`:Lazy`（外掛）、`:messages`（錯誤）、minimal repro（二分找兇手）。
- checkhealth 的技能是**篩選警告**，多數 `⚠️` 是用不到的可選 provider，別全消滅。
- `gd` 不動先 `:LspInfo`，八成是「缺 compile db、clangd 半殘」，不是壞。
- lazy-lock.json 記確切 commit，commit 它 + `:Lazy restore` = 可重現環境 + 更新壞掉的還原點。

## 自我檢核

- [ ] 我能不看檔說出 init.lua 的四大塊各是什麼、哪一章加的嗎？
- [ ] `gd` 沒反應，我的診斷順序是什麼？怎麼分辨 clangd「沒 attach」「半殘」「還在索引」？
- [ ] checkhealth 報一堆 `⚠️`，我怎麼分辨哪些要理、哪些忽略？
- [ ] minimal repro 是什麼、怎麼用二分法定位兇手？為什麼回報 bug 要給它？
- [ ] lazy-lock.json 存在的意義是什麼？它怎麼當「更新壞掉的還原點」？

## 延伸閱讀

- **Neovim `:help health` 與 `:help vim.health`**
  - **讀哪裡**：`:help health`（checkhealth 機制）、`:help lsp-faq`（LSP 常見問題與 `:LspInfo`/`:LspLog`）。
  - **學到什麼**：checkhealth 怎麼運作、各節在檢查什麼、警告的權威解釋。本章教你篩選警告，這裡是每個警告的字典。

- **[lazy.nvim 官方文件 — Lockfile 與 Profiling](https://lazy.folke.dev/)** — folke
  - **讀哪裡**：Lockfile（`lazy-lock.json` 的機制與 `restore`）、Profiling（`:Lazy profile`）、以及 lazy-load 的 `event`/`keys`/`cmd` spec。
  - **學到什麼**：本章 `:Lazy`/`lazy-lock.json`/lazy-load 那幾節的權威來源。特別是 lazy-load 機制——「外掛沒作用」的頭號成因，懂它才 debug 得動。

- **[kickstart.nvim 的 `init.lua`](https://github.com/nvim-lua/kickstart.nvim)** — TJ DeVries
  - **讀哪裡**：從上讀到下對照你自己的 config，特別看它的 keymap 段和 LSP 段怎麼組織、註解怎麼寫。
  - **學到什麼**：一份「每行都懂」的參考 config 該長什麼樣。你的 config 出問題時，對照它是「標準寫法」的參照物——這也是 Ch 0 就推薦的卡住時對照對象。

你現在能維護這台機器了——它壞了你修得好、你要改你改得動。最後一章收尾：把全課的**常見誤區**盤點一遍（每條反模式→為什麼→正確做法），並給你一張帶走的**完整讀碼鍵位手冊**——全課所有 keymap 按情境分組的一張大表。

→ [Ch 30 常見誤區與你的讀碼鍵位手冊](./30-anti-patterns-and-keymap-manual.md)
