# Ch 27 — 大 codebase 工作流：session 與佈局

> **目標**：讀一個大 repo 常是好幾天的事。你今天打開了七個 buffer、排好了「主檔 + 側欄 outline + 底部 quickfix」的佈局、追到 `luaV_execute` 第 1512 行——然後你關掉 nvim 去睡覺。明天重開，一切歸零：buffer 沒了、佈局散了、游標回到某個檔頭。本章給你「凍結攻堅現場」的三件事：**session**（把 buffer/window/游標整包存下、明天一鍵還原）、**window 佈局**（讀碼常用的三區佈局怎麼排、怎麼管）、**大檔效能**（超大檔卡頓時關掉哪些功能）。這是把「攻堅一個大 codebase 好幾天」組織起來的工作流層。

> **環境**：Neovim v0.12.4，WSL2 / Ubuntu。session 用內建 `:mksession` 與 **persistence.nvim**，兩者都在隔離 XDG 環境 headless 驗證過。

## 為什麼需要這個？

攻堅小專案是一次性的：開檔、讀完、關掉。攻堅大 codebase 不是——它是**跨越好幾個工作階段的持續戰役**。你第一天建架構地圖、第二天追一條 call chain、第三天驗證假設。每天結束時，你腦中和螢幕上都有一個「當前戰場狀態」：哪些檔開著、怎麼排的、追到哪。

問題是：**這個戰場狀態預設是易失的**。關掉 nvim，它蒸發。明天你花二十分鐘重新開檔、重排佈局、找回昨天追到的行——才回到昨天離開的地方，還沒開始今天的進度。這二十分鐘 × 好幾天，是純粹的重建成本。

更糟的是**認知重建成本**：不只螢幕要重排，你腦中的「我昨天在追什麼」也要重建。Ch 26 的 harpoon 和 notes.md 幫你外化了熱點和思緒，但**佈局本身**——那個精心排好的三區視窗——也是戰場狀態的一部分，該一起凍結。session 就是幹這個的。

## 先建立直覺：session = 螢幕狀態的快照

```
   你的攻堅現場 = 三層狀態
 ──────────────────────────────────────
  思緒（假設/發現）    →  notes.md        （Ch 26）
  熱點（那四五個函式）  →  harpoon 清單     （Ch 26）
  螢幕（buffer/佈局/游標）→  session         （本章）
 ──────────────────────────────────────
  三層都持久 = 明天重開，一鍵回到今天離開的地方
```

session 存的是**螢幕狀態**：開了哪些 buffer、視窗怎麼分割、每個視窗開哪個檔、游標在第幾行、fold 開合狀態、當前目錄。它是「你螢幕長什麼樣」的完整快照。載入 session = 螢幕瞬間變回存的時候。

## 內建 session：`:mksession`

Neovim 內建就能存 session，不用外掛：

```vim
:mksession ~/lua-attack.vim      " 把當前螢幕狀態存成一個 vim 腳本
```

它產出的是一個 `.vim` 檔——一份**會執行的 Vimscript**，裡面是「重建這個螢幕」的指令。真跑看它長什麼樣（我在 lua 目錄開了 `lvm.c` 和 `lparser.c` 的分割後 `:mksession`）：

```vim
$ head lua-attack.vim
let SessionLoad = 1
...
cd ~/reading_code_lab/lua           " 存了工作目錄
badd +0 lvm.c                       " 把這些檔加回 buffer 列表
badd +0 lparser.c
argglobal
...
```

它記了 `cd`（工作目錄）、`badd`（每個 buffer）、還有後面的分割與游標位置。載入就是執行這份腳本：

```vim
:source ~/lua-attack.vim          " 螢幕變回存的時候
```

或從命令列直接開進 session：

```bash
nvim -S ~/lua-attack.vim          " 開 nvim 並載入 session
```

**存什麼由 `sessionoptions` 控制**。預設它存 buffer、視窗、fold、當前目錄。讀碼你通常想加 `localoptions`（保留每個 buffer 的局部設定）：

```lua
vim.opt.sessionoptions:append("localoptions")
```

`:mksession` 的問題跟大寫 mark 一樣：**你得記得存、記得存去哪、記得手動載入**。攻堅到累了直接關 nvim，忘了 `:mksession`——現場就沒了。我們要的是「自動存、自動找回」，這就是 persistence.nvim。

## 自動 session：persistence.nvim

**persistence.nvim**（folke 寫的，跟 lazy.nvim 同作者）把 session 自動化：**你離開 nvim 時它自動存當前目錄的 session，你回來時一鍵載入。** 核心心智模型：**session 依「工作目錄」自動綁定**——你在 redis 目錄關 nvim，它存一份 redis 的 session；在 lua 目錄關，存一份 lua 的。回到哪個目錄，就載入哪個目錄的現場。

加進 config：

```lua
-- Part 6: persistence 自動 session
{ "folke/persistence.nvim", event = "BufReadPre",
  opts = {},
  config = function(_, opts)
    require("persistence").setup(opts)
    -- <leader>qs 載入「當前目錄」的 session
    vim.keymap.set("n", "<leader>qs", function() require("persistence").load() end)
    -- <leader>ql 載入「最後一次」的 session（不管在哪個目錄）
    vim.keymap.set("n", "<leader>ql", function() require("persistence").load({ last = true }) end)
  end },
```

用法：

```
（正常關 nvim）           persistence 自動存當前 cwd 的 session
cd 到專案目錄再開 nvim
<leader>qs               載入這個目錄的 session → 螢幕回到上次離開的樣子
<leader>ql               載入最後一次的 session（跨目錄）
```

### 底層機制：session 檔怎麼依目錄分開

persistence 把每個目錄的 session 存成獨立檔，檔名是**把工作目錄路徑編碼進去**。真跑看它給 lua 目錄算出的 session 路徑：

```
$ nvim --headless "+lua print(require('persistence').current())" +qa
/tmp/nvim_p6/state/nvim/sessions/%home%ypp%reading_code_lab%lua.vim
```

看那個檔名 `%home%ypp%reading_code_lab%lua.vim`——它把 `/home/ypp/reading_code_lab/lua` 這個路徑的 `/` 換成 `%` 當檔名。這就是「session 依目錄綁定」的實作：**目錄路徑 = session 的 key**。所以你回到 lua 目錄，persistence 算出同一個檔名，載入同一份現場。這跟 Ch 26 harpoon 依 project 分清單是同一個設計——**攻堅現場依 project 隔離**。

persistence 內部就是在你退出前呼叫 `:mksession!` 寫到那個算出來的路徑，載入時 `:source` 它。它不是魔法，是把「你該手動做的 mksession/source」綁到「依 cwd 自動命名 + 進退時自動觸發」。

## window 佈局：讀碼的三區戰場

session 存的是佈局，但佈局要先排得好。讀大型 C 專案，一個經典且高效的佈局是**三區**：

```
 ┌──────────────────────────┬─────────────┐
 │                          │  outline    │
 │   主檔（你在讀的 .c）      │  （document │
 │                          │   symbols） │
 │                          │             │
 ├──────────────────────────┴─────────────┤
 │  quickfix（gr 的結果 / grep 命中清單）    │
 └──────────────────────────────────────────┘
```

- **主區**：你正在精讀的檔，佔最大。
- **右側欄**：這個檔的 symbol outline（函式/struct 列表），用 `<leader>ds`（document symbol，Ch 20）或 outline 外掛填。它是「這個檔有什麼」的地圖，讀長檔時一眼跳到目標函式。
- **底部**：quickfix（Ch 12）。`gr`（找所有引用）或 `:grep` 的結果進這裡，變成一張「待看清單」，`:cnext`/`:cprev` 逐一走。

排這個佈局的指令：

```
:vsplit          垂直分割（左右），開右側欄
:split           水平分割（上下），開底部區
Ctrl-w h/j/k/l   在視窗間移動（跟 hjkl 方向一致）
Ctrl-w H/J/K/L   把當前視窗推到最左/下/上/右（重排佈局）
Ctrl-w =         所有視窗等寬高
Ctrl-w _         當前視窗最大化高度
Ctrl-w |         當前視窗最大化寬度
Ctrl-w o         關掉其他所有視窗（只留當前）——追岔了想回到單檔
:copen           開 quickfix 視窗（底部區）
:cclose          關 quickfix 視窗
```

一個實用技巧：quickfix 用 `:botright copen` 讓它**橫跨整個底部**（而不是只在當前視窗下方），配合右側 outline 就是上面那個三區佈局。

## 多 nvim vs 一個 nvim 多分割/多 tab

一個常見的組織問題：讀大 repo，你該開**多個 nvim 實例**（多個終端各開一個）、還是**一個 nvim 多 tab/多分割**？

- **一個 nvim 多分割**：適合「同時對照的檔」——追 call chain 時 caller 和 callee 並排看。這是主力模式。
- **一個 nvim 多 tab**：Vim 的 tab 不是「一個檔一個 tab」（那是 VSCode 的概念），Vim 的 tab 是**一整組視窗佈局**。所以 tab 適合「不同的攻堅主題」：tab 1 是「命令分派」那條線的三區佈局、tab 2 是「網路 IO」那條線的佈局。`gt`/`gT` 切 tab，等於切換攻堅主題。
- **多個 nvim 實例**：適合「完全獨立、互不干擾的兩件事」——一個 nvim 讀 code，另一個終端跑 build/git/gtags。但**不建議用多 nvim 讀同一專案的不同部分**：它們的 jumplist、harpoon、mark 不共享，你在實例 A 標的熱點實例 B 看不到，外化就割裂了。

原則：**一個 nvim 一個專案**（讓 harpoon/session/mark 統一），內部用分割對照、用 tab 分攻堅主題。要跑命令另開終端，別另開 nvim 讀同專案。

## 大檔效能：超大檔卡頓怎麼辦

讀大 codebase 偶爾會撞到**單一超大檔**——一個一萬行的 `.c`、一個機器產生的檔、一個塞滿資料的標頭。開下去 nvim 明顯卡：捲動延遲、打字掉幀。原因通常是**treesitter 高亮**和 **LSP 分析**在對整個大檔做重活。

排查與應對：

```vim
:syntax off              " 極端情況：關掉語法高亮（含 treesitter 的傳統 syntax）
:TSBufDisable highlight   " 只關這個 buffer 的 treesitter 高亮（保留其他 buffer）
:set foldmethod=manual    " 若 fold 用 treesitter/expr 算，改手動可省算力
```

Neovim 其實內建一個**大檔自動保護**：檔案超過 `g:bigfile` 相關的門檻（或你可以自己設 autocmd），自動關掉高亮。手動版：

```lua
-- 檔案大於 2MB 就關 treesitter 高亮和部分功能
vim.api.nvim_create_autocmd("BufReadPre", {
  callback = function(ev)
    local ok, stats = pcall(vim.uv.fs_stat, vim.api.nvim_buf_get_name(ev.buf))
    if ok and stats and stats.size > 2 * 1024 * 1024 then
      vim.b[ev.buf].bigfile = true
      vim.cmd("syntax clear")
    end
  end,
})
```

還有一招針對「clangd 對整個大專案卡」：clangd 是**背景索引**，第一次開大 repo 它會花幾分鐘掃全專案建索引（`.cache/clangd/`）。這期間 `gd`/`gr` 會慢或沒反應——不是壞了，是還沒索引完。`:LspInfo`（Ch 29）能看到它的進度。索引完就快了，且索引會 cache，下次秒開。

## 鍵位表

| 模式 | 按鍵 / 命令 | 作用 | 讀碼時機 |
|---|---|---|---|
| c | `:mksession <file>` | 手動存 session | 存一個具名的攻堅現場 |
| c | `:source <file>` / `nvim -S <file>` | 載入 session | 還原現場 |
| n | `<leader>qs` | persistence 載入當前目錄 session | 回到這專案的現場 |
| n | `<leader>ql` | persistence 載入最後一次 session | 續上最後在做的 |
| c | `:vsplit` / `:split` | 垂直 / 水平分割 | 排三區佈局 |
| n | `Ctrl-w h/j/k/l` | 視窗間移動 | 在三區間切 |
| n | `Ctrl-w H/J/K/L` | 把視窗推到某邊 | 重排佈局 |
| n | `Ctrl-w o` | 只留當前視窗 | 追岔了回到單檔 |
| n | `Ctrl-w =` | 視窗等分 | 佈局亂了拉平 |
| c | `:botright copen` | quickfix 橫跨底部 | 開底部待看清單 |
| n | `gt` / `gT` | 下一個 / 上一個 tab | 切換攻堅主題 |
| c | `:TSBufDisable highlight` | 關這 buffer 的 ts 高亮 | 大檔卡頓 |
| c | `:syntax off` | 全域關高亮 | 極端大檔救急 |

## 對比與取捨

| 做法 | 存什麼 | 自動 | 依目錄綁定 | 適合 |
|---|---|---|---|---|
| **`:mksession`（內建）** | buffer/佈局/游標 | 否（手動存/載） | 否（自己命名） | 少數幾個要具名保存的現場 |
| **persistence.nvim** | 同上 | 是（進退自動） | 是（cwd 當 key） | 日常攻堅，回目錄就續上 |
| **shada（`:h shada`）** | mark/暫存器/命令歷史 | 是 | 否（全域） | 跨 session 的 mark/歷史，非佈局 |
| **一個 nvim 多分割** | — | — | — | 對照檔、追 call chain |
| **一個 nvim 多 tab** | — | — | — | 分不同攻堅主題 |
| **多 nvim 實例** | — | — | — | code 一個、build/git 一個 |

取捨一句話：**日常用 persistence 自動綁目錄**，某個現場想長期保留（如「這是我攻 GC 的佈局」）用 `:mksession` 具名存一份。**別用多 nvim 讀同專案不同部分**——會割裂 harpoon/mark。

## 踩雷集錦

1. **session 存了但載入後 LSP/外掛狀態怪怪的。** session 存的是「buffer 和佈局」，不是「外掛的內部狀態」。載入 session 後 clangd 要重新 attach、treesitter 要重新解析（因為 session 只記了開哪些檔，不記 LSP 的分析結果）。這是正常的——載入後給它一兩秒重新 attach。若 `sessionoptions` 存了 `options`（全域選項），載入可能覆蓋你當前設定，通常移掉 `options` 只留 `localoptions` 較安全。

2. **persistence 沒自動存/載，因為 cwd 不對。** persistence 依**工作目錄**綁 session。你如果從 home 目錄開 nvim 再 `:cd` 進專案，或用 `nvim src/foo.c` 開檔（cwd 還是你打指令的地方），persistence 算出的 key 可能不是專案根。習慣**先 `cd` 進專案根再開 `nvim`**，session 才綁對目錄。

3. **開一堆 tab 當「檔案分頁」用。** 從 VSCode 來的人會把每個檔開成一個 tab，結果十幾個 tab、`gt` 切到瘋。Vim 的 tab 是**佈局容器**不是檔案分頁——一個 tab 裝一整組視窗。檔案切換用 buffer（`:b`、Ch 7）或 harpoon（Ch 26），tab 留給「不同攻堅主題」。搞混會讓你的 tab 列爆掉且難導航。

4. **超大檔卡死了才想到關高亮。** 一萬行的機器產生檔，你直接 `nvim` 開下去，treesitter 當場對整檔解析、卡幾秒甚至更久。與其開了才救，不如設好上面那個 `BufReadPre` 的大檔 autocmd，超過門檻**自動**關高亮。預防比急救好。

5. **以為 `Ctrl-w o` 會關 buffer。** `Ctrl-w o`（only）只關**其他視窗**，buffer 還在（只是沒顯示）。你追岔了想「清乾淨」按了它，佈局是乾淨了，但那些 buffer 還在列表裡。要真的清 buffer 是 `:bd`（Ch 7）。視窗（window）和 buffer 是兩層概念，關視窗 ≠ 關 buffer。

## 進階：再往深一層

- **session 進 git（團隊共享攻堅佈局）**：`:mksession` 產的是純文字 `.vim` 檔，可以 commit。團隊 onboarding 時，資深的人可以存一份「讀這個模組的推薦佈局」session 進 repo，新人 `nvim -S onboarding/read-scheduler.vim` 一鍵進入排好的三區戰場。這是把個人工作流升級成團隊資產。

- **`sessionoptions` 精修**：`:help sessionoptions` 列出所有可存項。讀碼建議 `blank,buffers,curdir,folds,help,tabpages,winsize,localoptions`——存佈局和 fold（Ch 8 你標的 fold 也一起回來），但**不存 `options`**（避免載入時覆蓋全域設定）、看情況存不存 `terminal`。調對 sessionoptions，你的現場還原得更完整。

- **auto-session 類外掛的更強自動化**：persistence 是輕量的，還有 `auto-session` 等外掛能做「per-branch session」（切 git 分支自動切 session）、session lens（用 telescope 挑要載入哪個 session）。攻堅多個專案/多個分支時，用 telescope 列出所有 session 一鍵切換很順手。但先把 persistence 用熟，需要再升級。

- **和 harpoon/notes 三位一體**：把本章的 session 和 Ch 26 的 harpoon、notes.md 想成一組。理想工作流：`cd` 進專案 → 開 nvim → `<leader>qs` 載入 session（螢幕回來）→ `<C-e>` 看 harpoon（熱點回來）→ `:e notes.md`（思緒回來）。三個動作，五秒鐘，昨天的攻堅現場完整重建——螢幕、熱點、思緒全回來了。這是「讀大 codebase 好幾天」的正確開場。

## 本章重點整理

- 攻堅大 codebase 是跨越好幾天的持續戰役，「攻堅現場」預設易失，關 nvim 就蒸發，重建有時間和認知雙重成本。
- 現場有三層：思緒（notes.md）、熱點（harpoon）、螢幕（session）。本章補上第三層。
- session 存螢幕狀態（buffer/佈局/游標/cwd）。內建 `:mksession` 要手動；**persistence.nvim 自動化**，依 cwd 綁定、進退自動存載。
- 讀碼三區佈局：主檔 + 右側 outline + 底部 quickfix，用 `:vsplit`/`:split`/`Ctrl-w` 系列排。
- 一個 nvim 一個專案（統一 harpoon/mark），內部用分割對照、tab 分攻堅主題；別用多 nvim 讀同專案。
- 超大檔卡頓：`:TSBufDisable highlight` 或設大檔 autocmd 自動關高亮。

## 自我檢核

- [ ] session 存的是什麼、不存什麼？載入後為什麼 LSP 要重新 attach？
- [ ] persistence 怎麼決定載入哪份 session？「依 cwd 綁定」實作上是怎麼做的？
- [ ] 讀碼三區佈局是哪三區？各放什麼、怎麼排出來？
- [ ] Vim 的 tab 跟 VSCode 的分頁差在哪？tab 該用來裝什麼？
- [ ] 為什麼不建議用多個 nvim 實例讀同一專案的不同部分？

## 延伸閱讀

- **Neovim `:help session-file` 與 `:help 'sessionoptions'`**
  - **讀哪裡**：`:help mksession`、`:help session-file`、`:help 'sessionoptions'` 三段。看 `:mksession` 產出什麼、`sessionoptions` 每個值存什麼、以及 `:source` / `nvim -S` 怎麼還原。
  - **學到什麼**：session 的內建機制。persistence 等外掛都是包在這之上，懂了底層你才知道外掛在幫你自動化什麼、出問題往哪查。

- **[persistence.nvim GitHub README](https://github.com/folke/persistence.nvim)** — folke
  - **讀哪裡**：README 的 setup 與 API 段（`load` / `load({last=true})` / `current()`），以及它「依 cwd 自動命名 session」的說明。
  - **學到什麼**：本章 config 那塊的來源，以及 persistence 相對於 `:mksession` 的自動化到底做了什麼——正好對照本章的底層機制節。

- **Neovim `:help window` 與 `:help tabpage`**
  - **讀哪裡**：`:help windows-intro`、`:help CTRL-W`（所有 `Ctrl-w` 開頭的視窗命令）、`:help tab-page-intro`。
  - **學到什麼**：window 和 tab 的完整命令與正確心智模型（tab 是佈局容器不是檔案分頁）。本章三區佈局的每個 `Ctrl-w` 動作都在這裡，讀完你能隨手排任何佈局。

現場能一鍵還原了，佈局也排得順了。到這裡，你手上的每個工具——motion、telescope、treesitter、clangd、gtags、harpoon、session——都各自學過了。下一章是本課的高光：拿一個真專案，走一次**完整的攻堅流程**，看這些工具怎麼協同成一台機器。

→ [Ch 28 把整套串起來：一次完整讀碼流程](./28-full-reading-workflow.md)
