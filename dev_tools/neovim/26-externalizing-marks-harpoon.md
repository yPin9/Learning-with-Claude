# Ch 26 — 外化：marks / harpoon 標攻堅點

> **目標**：把讀碼從「全靠腦記位置」變成「機器替你記」。你攻堅一個陌生大 codebase 時，一條路徑常在四五個關鍵函式間來回跳——`processCommand` → `lookupCommand` → 那張命令表 → 具體 handler → 寫回的地方。這些位置沒有名字，全靠你記「剛剛那個在哪個檔第幾行」。本章給你三層外化工具：內建 **marks**（單檔/跨檔的書籤）、升級版 **harpoon**（把四五個攻堅熱點釘成一鍵直達清單）、以及 **scratch buffer / markdown 筆記**（把假設與 call chain 倒出腦袋）。這是 `reading_code` Ch 35「外化理解」在 Neovim 裡的落地——那章講「為什麼要外化」，這章講「用什麼鍵外化」。

> **環境**：Neovim v0.12.4，WSL2 / Ubuntu。harpoon 用 `harpoon2` 分支，已在隔離 XDG 環境 headless 驗證裝好、可 `require`。

## 為什麼需要這個？

回想你上次追一條 call chain 的樣子：讀 `A` 讀到一半，`gd` 跳進它呼叫的 `B`，`B` 又叫 `C`，你追到 `C` 底部，回頭想確認 `A` 剛剛那個 flag——結果忘了 `A` 在哪個檔第幾行，只好重新 `Telescope find_files` 打字找回去。這一天你把同一個 `A` 找回來了八次。

這不是你笨，是**工作記憶溢位**（`reading_code` Ch 35 講的 4±1 chunk 上限）。每一個「還沒追完、等下要回去」的位置都佔一個腦內 slot，陌生 code 瞬間把你的 slot 塞爆。`Ctrl-o`／jumplist（Ch 5）能倒退，但 jumplist 是**線性歷史**——它只知道「上一個跳點」，不知道「我這次攻堅的五個核心函式」。你需要的是一組**具名、隨機存取、跨檔**的定位點：「我要看的就這五個，一鍵之間來回」。

marks 是內建的第一層答案，harpoon 是把它做成「攻堅熱點快取」的第二層。

## 先建立直覺：三層外化，對應三種東西要記

```
   你腦中正在耗工作記憶維持的東西        外化到哪
 ─────────────────────────────────────────────────────
  「我要在這五個函式間來回」      →   harpoon 清單（<leader>1-4 直跳）
  「這一行等下要回來看」          →   mark（ma 標、'a 回）
  「我猜 X」「還沒懂 Y」「確認 Z」 →   scratch buffer / notes.md（三欄筆記）
```

三層各管一種東西，別混用。位置的隨機存取交給 harpoon/mark，離散的假設/發現交給文字筆記。這正是 `reading_code` Ch 35 說的「只外化腦中正在耗工作記憶的東西」——位置和思緒都是。

## 第一層：內建 marks

Vim 內建 mark 是最原始的外化。按 `m` + 一個字母，把**當前游標位置**存進那個字母；按 `` ` `` + 同字母跳回去。

```
ma        把當前位置存進 mark a
`a        跳回 mark a（精確到欄）
'a        跳回 mark a 那行的行首
```

關鍵區別在**大小寫**：

- **小寫 mark（a-z）是 buffer-local**：`ma` 只在當前檔有效，換個檔它的 `a` 是另一個 mark。適合「這個檔裡我要記三個點」。
- **大寫 mark（A-Z）是 global**：`mA` 記住「哪個檔的哪個位置」，你在任何檔按 `` `A `` 都會跳到那個檔那個位置。**這是跨檔攻堅的關鍵**——`mM` 標主迴圈、`mD` 標 dispatch、`mH` 標你正在追的 handler，之後在任何檔一鍵回到它們。

還有一組**自動 mark**，Neovim 幫你設好、不用手動標：

```
``        跳回「上次跳轉前」的位置（跟自己 toggle，兩下回原地）
`.        跳到最後一次修改的位置
`^        跳到最後一次插入的位置
`"        跳到上次離開這個 buffer 時的位置
`[  `]    上次 yank / paste 的起訖
```

`` `` `` 特別好用：追進一個定義、看完、`` `` `` 一下就回原位——比記 jumplist 深度直覺。

看目前有哪些 mark：`:marks`。它列出所有 mark 和對應的檔/行/內容，是你外化位置的儀表板。

### marks 的問題：字母要自己記、看不到清單

大寫 mark 能跨檔，但它有個現實的痛：**你得記住「M 是主迴圈、D 是 dispatch」這個對照**，記錯就跳錯。攻堅到第三小時，你已經標了 `A B D H M P` 六個，哪個是哪個早忘了。`:marks` 能查，但查表本身就是摩擦。

marks 適合「臨時記一兩個點」。要管「這次攻堅的五個熱點、還要看得到清單、還要一鍵切」，我們升級到 harpoon。

## 第二層：harpoon——攻堅熱點的快取

**harpoon** 是 ThePrimeagen（前 Netflix 工程師）寫的外掛。它的心智模型一句話：**你手動維護一份「就這幾個檔/位置」的短清單，用固定的鍵一鍵直達，不用記字母、不用打字找、不用在一堆 buffer 裡翻。**

它解決的正是讀碼的核心場景：一條攻堅路徑通常就纏在**四五個**關鍵位置。harpoon 讓你把這四五個釘住，然後 `<leader>1` `<leader>2` `<leader>3` `<leader>4` 在它們之間瞬移——像遊戲的快速存檔點。

加進 config（往 Ch 0 骨架的 `require("lazy").setup({...})` 裡加一塊）：

```lua
-- Part 6: harpoon 釘攻堅熱點
{ "ThePrimeagen/harpoon", branch = "harpoon2",
  dependencies = { "nvim-lua/plenary.nvim" },
  config = function()
    local harpoon = require("harpoon")
    harpoon:setup()
    -- <leader>a 把當前檔加進清單
    vim.keymap.set("n", "<leader>a", function() harpoon:list():add() end)
    -- <C-e> 開快速選單（看清單 / 刪 / 重排）
    vim.keymap.set("n", "<C-e>", function() harpoon.ui:toggle_quick_menu(harpoon:list()) end)
    -- <leader>1-4 直跳清單第 1-4 個
    vim.keymap.set("n", "<leader>1", function() harpoon:list():select(1) end)
    vim.keymap.set("n", "<leader>2", function() harpoon:list():select(2) end)
    vim.keymap.set("n", "<leader>3", function() harpoon:list():select(3) end)
    vim.keymap.set("n", "<leader>4", function() harpoon:list():select(4) end)
  end },
```

> 注意 `branch = "harpoon2"`。harpoon 有兩代，harpoon2 是重寫版、API 完全不同（用 `harpoon:list()` 物件，不是舊版的 `require("harpoon.mark")`）。多數新教材是 harpoon2，釘這個分支才對得上上面的 API——這跟 Ch 13 treesitter 釘 `master` 是同一類「分支分裂」的坑。

用法流程：

```
<leader>a    把當前檔釘進 harpoon 清單（釘的是檔 + 當時游標位置）
<C-e>        開快速選單：一個小浮窗列出你釘的檔，可上下移動重排、dd 刪、<CR> 開
<leader>1    直接跳到清單第 1 個
<leader>2    直接跳到清單第 2 個
<leader>3    直接跳到清單第 3 個
<leader>4    直接跳到清單第 4 個
```

### 底層機制：harpoon 到底存了什麼

harpoon 的清單不是「buffer 列表」，是一份**你手動策展的、有序的、可持久化的位置清單**。它跟其他導航工具的本質差異：

- **buffer list（`:ls`）** 是 Neovim 自動累積的——你開過的每個檔都進去，包括你只是瞥一眼的。攻堅一小時後你有 40 個 buffer，找你要的那個還是要翻。它是「開過什麼」的歷史，不是「我在乎什麼」的清單。
- **jumplist（`Ctrl-o`）** 是線性的跳轉歷史，只能前後倒退，不能「直接跳第三個」。
- **harpoon** 是**你親手放進去的**——只有你按 `<leader>a` 的才進清單。所以它永遠只有那四五個你真正在乎的，且順序是你排的、`<leader>3` 永遠是同一個檔。

它把清單存成一個 JSON 檔（放在 `stdpath("data")` 下），**依 project 分開**（用當前工作目錄當 key）。所以你在 redis 目錄釘的四個檔，跟你在 lua 目錄釘的四個檔互不干擾——切回 redis 目錄，你的四個攻堅點還在。這跟 Ch 27 的 session 一樣，是「攻堅現場的持久化」。

`<leader>a` 釘的是「檔 + 當時的游標行」，所以 `<leader>2` 跳過去會落在你當初釘的那一行附近，不是檔頭——它記的是**位置**不只是**檔**。

## 讀碼情境：一次真實的四點攻堅

假設你在讀 redis，任務是搞懂「一個命令從進來到執行」。你已經用 gtags/clangd 定位出四個核心函式（下面的行號是真跑 `global -x` / `global -rx` 查到的）：

```
$ global -x processCommand          # 命令分派中心
processCommand   3884 server.c    int processCommand(client *c) {
$ global -rx aeMain                 # 主迴圈的呼叫點 = 心臟
aeMain           7251 server.c        aeMain(server.el);
```

攻堅動作：

1. 開 `server.c` 跳到 `aeMain` 那行（主迴圈），`<leader>a` 釘成 harpoon #1。
2. `gd`/gtags 跳到 `readQueryFromClient`（read handler），`<leader>a` 釘成 #2。
3. 跳到 `processCommand`（分派中心），`<leader>a` 釘成 #3。
4. 跳到具體命令 handler（如 `getCommand`），`<leader>a` 釘成 #4。

現在這四個是你的攻堅快取。接下來一小時你都在這四點間來回：讀 #3 看到它呼叫 handler，想確認 #1 主迴圈怎麼把事件分派過來——`<leader>1` 瞬移過去看，看完 `<leader>3` 回來。**不用記行號、不用打字找、不用在 40 個 buffer 裡翻。** 這就是 harpoon 對讀碼的價值：把「一條路徑上的幾個熱點」變成肌肉記憶的四個鍵。

`<C-e>` 隨時開選單檢視/重排——追到後來發現 handler 才是重點，把它拖到 #1，攻堅重心就換了。

## 第三層：scratch buffer / notes.md——外化思緒不只位置

marks 和 harpoon 外化的是**位置**。但 `reading_code` Ch 35 講的三類外化裡，還有兩類是**文字**：待驗證的假設、還沒懂的問題、確認的發現。這些不該塞進腦袋，也不該塞進位置清單——它們該進一個文字檔。

兩個做法：

**做法 A：scratch buffer（不落地的暫存）。** 開一個沒有檔名、不寫盤的 buffer 當草稿紙：

```vim
:enew                          " 開一個新的空 buffer
:setlocal buftype=nofile bufhidden=hide noswapfile
```

在裡面隨手記「H1: aeMain 是唯一主迴圈？待驗證」。它不佔硬碟、關 nvim 就沒了——適合「這次 session 的臨時思緒」。可以做成一個 keymap 一鍵開：

```lua
vim.keymap.set("n", "<leader>sc", function()
  vim.cmd("botright vnew")            -- 右側開一個直向分割
  vim.bo.buftype = "nofile"
  vim.bo.bufhidden = "hide"
  vim.bo.swapfile = false
end)
```

**做法 B：一個真的 `notes.md`（要留下來的）。** 攻堅一個專案好幾天，思緒該進一個真檔，進 git、下次回來還在。直接 `:e notes.md`，用 `reading_code` Ch 35 的三欄模板：

```markdown
# 讀碼筆記：redis 命令分派  2026-08-03

## 假設（待驗證）
- [ ] H1: aeMain 是唯一主迴圈，所有 client 事件從這裡分派
- [x] H2: processCommand 是分派中心 —— 已驗證，見 F2

## 問題（待查）
- [ ] Q1: lookupCommand 怎麼從 argv[0] 查到 redisCommand？

## 發現（帶證據）
- F1: 主迴圈 aeMain(server.el) 在 server.c:7251（global -rx 確認）
- F2: processCommand 在 server.c:3884，dispatch 靠 lookupCommand 查表
```

**位置 + 思緒的分工**：harpoon #1-4 釘住那四個函式的位置，`notes.md` 記你對那四個函式的假設與發現。讀到 `processCommand`（harpoon #3），在 notes.md 記下「F2: 它是守門員，一串 gate 檢查後才 call proc」。位置讓你「跳得回去」，筆記讓你「想得起來剛剛想什麼」。兩者缺一不可——這正是 Ch 35 的核心。

在 nvim 裡邊讀邊記的好處：筆記檔就是另一個 buffer，`<C-e>` 也能把它釘進 harpoon，`<leader>fg` 也能搜它。你的外化和你的 code 在同一台機器上，零切換摩擦。

## 鍵位表

| 模式 | 按鍵 | 作用 | 讀碼時機 |
|---|---|---|---|
| n | `ma` | 設 buffer-local mark a | 這個檔裡臨時記一個點 |
| n | `` `a `` | 跳回 mark a（精確到欄） | 回到剛才記的點 |
| n | `mA` | 設 global mark A（跨檔） | 標一個跨檔要回來的關鍵位置 |
| n | `` `A `` | 從任何檔跳到 global mark A | 跨檔一鍵回到熱點 |
| n | `` `` `` | 跳回上次跳轉前的位置 | 追進定義後回原地 |
| n | `` `. `` | 跳到最後一次修改處 | 回到剛改的地方 |
| n | `` `" `` | 跳到上次離開這 buffer 的位置 | 重開檔續讀 |
| c | `:marks` | 列出所有 mark | 查我標了哪些 |
| n | `<leader>a` | harpoon 加當前檔進清單 | 釘一個攻堅熱點 |
| n | `<C-e>` | harpoon 開快速選單 | 看/刪/重排清單 |
| n | `<leader>1` | harpoon 跳清單第 1 個 | 瞬移到熱點 1 |
| n | `<leader>2` | harpoon 跳清單第 2 個 | 瞬移到熱點 2 |
| n | `<leader>3` | harpoon 跳清單第 3 個 | 瞬移到熱點 3 |
| n | `<leader>4` | harpoon 跳清單第 4 個 | 瞬移到熱點 4 |
| n | `<leader>sc`（自訂） | 開 scratch buffer | 隨手記臨時思緒 |
| c | `:e notes.md` | 開/建讀碼筆記 | 三欄外化假設/問題/發現 |

## 對比與取捨

| 工具 | 記的是 | 隨機存取 | 跨檔 | 持久 | 最適合 |
|---|---|---|---|---|---|
| **jumplist（`Ctrl-o`）** | 跳轉歷史 | 否（只能前後） | 是 | session 內 | 追進去→倒退回來 |
| **小寫 mark** | 單檔位置 | 是（記字母） | 否 | 否 | 一個檔內幾個點 |
| **大寫 mark** | 跨檔位置 | 是（記字母） | 是 | 是（進 shada） | 少數幾個固定錨點 |
| **harpoon** | 策展的熱點清單 | 是（`<leader>1-4`） | 是 | 是（依 project） | 一條路徑的 4-5 個熱點 |
| **buffer list（`:ls`）** | 開過的所有檔 | 半（要看編號） | 是 | session 內 | 檢視所有開過的檔 |

一句話取捨：**臨時一兩個點用 mark，這次攻堅的核心四五點用 harpoon，思緒用 notes.md。** harpoon 不是取代 mark，是補上 mark 缺的「看得到清單 + 不用記字母 + 依 project 持久」。

## 踩雷集錦

1. **釘太多 harpoon，退化成 buffer list。** harpoon 的價值在「就那四五個」。你釘了十二個，`<leader>1-4` 只能到前四個，剩下的又要開選單翻——你把它用成了另一個 buffer list，失去意義。攻堅重心變了就用 `<C-e>` 刪舊的、加新的，**保持清單精簡**。四個熱點是甜蜜點，超過八個代表你該問「我這次到底在追哪條路徑」。

2. **用小寫 mark 想跨檔。** `ma` 在 `server.c` 標的 `a`，跳到 `networking.c` 按 `` `a `` 不會回 server.c——小寫 mark 是 buffer-local，每個檔一套。要跨檔錨點必須用**大寫** `mA`。這是最常見的 mark 誤用，症狀是「我明明標了怎麼跳不回去」。

3. **harpoon 釘錯分支（用到 harpoon1 的 API）。** 不寫 `branch = "harpoon2"` 會抓到舊版預設分支，上面 `harpoon:list()` 的 API 全報錯（舊版是 `require("harpoon.mark").add_file()`）。網路上兩代教材混雜，抄到 harpoon1 的 config 配 harpoon2 的外掛（或反之）就 crash。釘 `harpoon2`、用 `harpoon:list()` 系列 API。

4. **全靠 harpoon/mark 記位置，卻不記思緒。** 你跳得回那四個函式，但回去了還是不記得「我上次在這裡想通了什麼」。位置外化 ≠ 理解外化。位置交給 harpoon，**假設/發現一定要進 notes.md**——這是 Ch 35 反覆強調的：位置和思緒是兩類東西，都要外化。

5. **scratch buffer 記了重要東西又忘了它不落地。** `buftype=nofile` 的 buffer 關掉就沒了。你在裡面記了三個關鍵發現，關 nvim——蒸發。scratch 只放「這次 session 的臨時草稿」，值得留的（假設/發現/架構圖）搬進真的 `notes.md`。搞混兩者會丟資料。

## 進階：再往深一層

- **harpoon 的多清單（lists）**：harpoon2 支援具名清單，除了預設的檔案清單，你可以開一個專門存「terminal 命令」的清單、一個存「要跑的測試」的清單。攻堅時用預設清單釘檔，用另一個清單釘常用的 `global -rx foo` 查詢命令，`harpoon:list("cmd")` 取用。多數讀碼用預設清單就夠，但知道它能擴展。

- **global mark 存進 shada**：大寫 mark 會寫進 Neovim 的 shada（shared data）檔，**跨 nvim session 存活**——今天標的 `mM`，明天重開 nvim 按 `` `M `` 還在。這是 mark 勝過 harpoon 的一點（harpoon 依 project 目錄、mark 依 shada 全域）。攻堅一個超長期專案，可以用大寫 mark 記幾個「永遠的錨點」（如 main、主迴圈），harpoon 記「這幾天在追的路徑」。

- **用 `:mksession` 連 harpoon 一起存**（接 Ch 27）：harpoon 清單已經依 project 持久了，配合 Ch 27 的 session，你關掉 nvim 明天重開，buffer 佈局 + harpoon 熱點 + 游標位置全部回到攻堅現場。這是「大 codebase 讀好幾天」工作流的地基，下一章展開。

- **把外化釘回 code**（接 `reading_code` Ch 35 第三層）：clone 一份專案，在你搞懂的函式上補一句「我讀懂後補的」註解、把 `tmp` 重命名成 `lastUnexpiredNode`（LSP rename，Ch 19）。這是最持久的外化——理解長在 code 裡，下次讀到直接送到眼前，連筆記都不用翻。

## 本章重點整理

- 讀碼是導航密集的活，一條路徑纏在四五個熱點間來回。全靠腦記位置會工作記憶溢位，要用機器外化。
- 三層外化：**harpoon 記熱點清單**（一鍵直達）、**mark 記臨時位置**（小寫 buffer-local、大寫跨檔）、**notes.md 記思緒**（假設/問題/發現三欄）。
- harpoon 是「你親手策展的短清單」，勝過 buffer list（自動累積、太雜）和 jumplist（線性、不能隨機存取）；依 project 持久、`<leader>1-4` 瞬移。
- 位置外化 ≠ 理解外化。harpoon 讓你跳得回去，notes.md 讓你想得起來剛剛想什麼，兩者缺一不可。
- 保持 harpoon 清單精簡（四五個），釘太多就退化成 buffer list。

## 自我檢核

- [ ] 小寫 mark 和大寫 mark 差在哪？我要跨檔記錨點該用哪個？
- [ ] harpoon 跟 buffer list 的本質差異是什麼？為什麼 harpoon 更適合攻堅一條路徑？
- [ ] `<leader>a` / `<C-e>` / `<leader>1-4` 各做什麼？我能不看筆記說出來嗎？
- [ ] 為什麼「位置外化」不等於「理解外化」？思緒該外化到哪、用什麼格式？
- [ ] harpoon 為什麼要釘 `harpoon2` 分支？不釘會怎樣？

## 延伸閱讀

- **Neovim `:help mark-motions`**
  - **讀哪裡**：`:help marks`、`:help mark-motions`、`:help 'shada'`。看小寫/大寫 mark 的區別、自動 mark（`` `` `` / `` `. `` / `` `" ``）清單、以及大寫 mark 怎麼透過 shada 跨 session 存活。
  - **學到什麼**：內建 mark 的完整能力，很多人只會 `ma`/`` `a ``，錯過了自動 mark 這批不用手動標就能用的定位點。

- **[harpoon（harpoon2 分支）GitHub README](https://github.com/ThePrimeagen/harpoon/tree/harpoon2)** — ThePrimeagen
  - **讀哪裡**：README 的 quick start 與 `harpoon:list()` API 段；特別注意它強調的「這是給你手動策展的少數幾個檔，不是 buffer manager」的設計哲學。
  - **學到什麼**：harpoon2 的正確 API（別抄到 harpoon1）、多清單機制、以及作者對「為什麼不用 buffer list」的論述——正好呼應本章的取捨表。

- **`reading_code` Ch 35「外化理解」**（`soft_skills/reading_code/35-externalizing-understanding.md`）
  - **讀哪裡**：整章，特別是三欄筆記模板（假設/問題/發現）和「只外化腦中正在耗工作記憶的東西」那節。
  - **學到什麼**：本章的「為什麼」。這章教你按哪個鍵外化，那章從認知科學講清楚為什麼腦中讀是輸家策略——兩章合起來才完整。

位置和思緒都外化了，但攻堅一個大專案往往是好幾天的事。你關掉 nvim 去睡覺，明天重開——buffer 沒了、佈局散了、游標回到檔頭。下一章講怎麼把整個「攻堅現場」凍結起來：session 與佈局，讓你明天一鍵回到今天離開的地方。

→ [Ch 27 大 codebase 工作流：session 與佈局](./27-large-codebase-workflow.md)
