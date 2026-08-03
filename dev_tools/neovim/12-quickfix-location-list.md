# Ch 12 — quickfix / location list：把搜尋變工作清單

> **目標**：把 Part 2 的搜尋能力收斂成「組織一次攻堅」。學完你懂 quickfix 是一個「位置清單」（檔:行:內容），能用 `:cnext`/`:cprev`/`:copen` 逐項導航、知道結果來源（`:grep`/`:vimgrep`、telescope `<C-q>`、LSP references）、分得清 quickfix 與 location list、會用 `:cdo`/`:cfdo` 對每項批次執行命令，並把「一個函式的所有 caller」變成一份逐項看過去、一個都不漏的清單。

> **環境**：Neovim v0.12.4，WSL2 / Ubuntu。本章 quickfix 導航是互動操作（截不了圖，以逐鍵描述），但 quickfix 的來源——rg 命中清單——是在真專案 `/tmp/lua` 真跑照抄的，你能看到「送進 quickfix 的到底是什麼」。

## 為什麼需要這個？

Ch 9–11 教你找得又快又準。但找到之後呢？

假設你 `live_grep` 一個函式名，得到 11 個命中。你 `<CR>` 開第一個看，看完想看第二個——重開 picker？`resume`（Ch 9）能回去，但你得記得看到第幾個、哪些看過了。開到第七個時你早忘了前面幾個講什麼。**這不叫組織攻堅，這叫瞎忙。**

讀碼有大量「我要把這一批位置一個一個看過、一個都不漏」的場景：一個函式的所有 caller、一個 struct 欄位的所有讀寫點、一個 TODO 的所有出現、一個 bug 假設涉及的所有可疑行。這些本質上是一份**工作清單**——你要能標記進度、逐項跳、跳完知道自己看完了。

Vim 內建的 **quickfix list** 就是為此而生，而且它被嚴重低估。它是一個結構化的「位置清單」，每項是 `檔案:行:欄:內容`。你用 `:cnext` 往下一項、`:cprev` 回上一項，游標**直接跳到那個檔那一行**——不用開 picker、不用記路徑。它是所有搜尋工具（rg、telescope、LSP、compiler 錯誤）的共同下游收集點。**Part 2 的搜尋（找得到）到這章才收斂成「找到之後系統化看完」。**

## 先建立直覺：quickfix 是一份可導航的位置清單

```
   來源（誰產生位置清單）              quickfix list              導航
   ────────────────────              ─────────────             ────────
   :grep / :vimgrep         ─┐      ┌──────────────────┐      :copen  開清單視窗
   telescope <C-q>          ─┼────► │ lua.c:779   ...   │ ◄─┐  :cnext  跳下一項
   LSP references (gr)      ─┤      │ ldo.c:137  ...   │   │  :cprev  跳上一項
   compiler / :make 錯誤    ─┘      │ llex.c:120 ...   │   │  :cc N   跳第 N 項
                                    │ ...              │   └─ :cdo    對每項跑命令
                                    └──────────────────┘
                                    每項：檔:行:欄:內容
```

三個要點：

1. **quickfix 是一個「格」（slot）**：nvim 只有一份 current quickfix list（其實有歷史堆疊，`:colder`/`:cnewer` 翻）。任何來源都往這個格填。填進去後，導航命令一律 `:c*` 開頭。
2. **它是「位置」不是「內容」**：每項記的是「哪個檔哪一行」。`:cnext` 是**帶你去那裡**，不是給你看一段文字。所以它天然適合「逐項檢視 code」。
3. **它跟你怎麼填無關**：無論位置是 rg 搜的、telescope 送的、還是 clangd 的 references——填進 quickfix 後導航方式完全一樣。**學一次 `:c*` 導航，套用到所有來源。** 這是 quickfix 被低估的原因也是它的威力：它是搜尋世界的共同終點。

## 來源一：telescope `<C-q>`（Part 2 的樞紐）

Ch 9 講過 picker 內按 `<C-q>` 把**整份結果**送進 quickfix。這是 Part 2 搜尋接到本章的主橋樑。

逐鍵（**互動 UI 無法貼截圖，以下為鍵位操作；送進去的清單內容見下方 rg 真跑輸出**）：

1. `<leader>fg` 開 live_grep，打 `luaD_throw`（Lua 的錯誤拋出函式），結果列出所有出現。
2. （可選）`<Tab>` 多選你要的幾項，或不選就送全部。
3. 按 `<C-q>`——telescope 關閉，所有結果進 quickfix 並自動 `:copen` 開清單視窗。

這時 quickfix 裡的內容，就是這條 rg 在 `/tmp/lua` 真跑的結果（telescope live_grep 底層就是 rg）：

```
$ rg -w "luaD_throw" -n *.c | head -11
lundump.c:47:  luaD_throw(S->L, LUA_ERRSYNTAX);
lobject.c:517:      luaD_throw(L, LUA_ERRMEM);
lobject.c:669:    luaD_throw(L, LUA_ERRMEM);
llex.c:120:  luaD_throw(ls->L, LUA_ERRSYNTAX);
ldo.c:137:      luaD_throw(mainth, errcode);  /* re-throw in main thread */
ldo.c:156:  luaD_throw(L, errcode);
ldo.c:219:  luaD_throw(L, LUA_ERRERR);
ldo.c:1031:    luaD_throw(L, LUA_YIELD);
ldo.c:1120:    luaD_throw(L, LUA_ERRSYNTAX);
ldebug.c:853:  luaD_throw(L, LUA_ERRRUN);
ldebug.c:975:    luaD_throw(L, LUA_YIELD);
```

**這 11 行就是你的工作清單。** 每一行是一個「錯誤拋出點」，散在 6 個檔（lundump/lobject/llex/ldo/ldebug）。讀碼情境：你想搞懂 Lua 怎麼處理錯誤，這 11 個點就是你要一個個看的地方。quickfix 讓你 `:cnext` 一個個跳過去，看完 `ldo.c:156` 直接 `:cnext` 到 `ldo.c:219`——**不用手動開檔、找行、記進度**。

## 來源二：`:grep` 與 `:vimgrep`（不開 telescope 直接填）

有時你不想開 picker，直接命令列填 quickfix。兩個內建：

- **`:grep`**：跑外部 grep 程式，結果進 quickfix。把它的 `grepprg` 設成 rg（讀碼必做），一條命令搜完直接得到清單：

```lua
-- 往 config 加：讓 :grep 用 ripgrep，結果進 quickfix
vim.opt.grepprg = "rg --vimgrep --smart-case"
vim.opt.grepformat = "%f:%l:%c:%m"   -- 告訴 quickfix 怎麼解析 rg 的輸出
```

設好後 `:grep -w luaD_throw` 就跑 rg、把 11 個命中填進 quickfix、`:copen` 看。`--vimgrep` 讓 rg 輸出 `檔:行:欄:內容` 這種 quickfix 吃得懂的格式。**這是「搜尋 → quickfix」最直接的一步**，適合你已經知道 pattern、不需要 previewer 探索的時候。

- **`:vimgrep`**：nvim **內建**的搜尋（不靠外部程式），`:vimgrep /pattern/ **/*.c` 搜所有 .c 填 quickfix。好處是不依賴外部工具、吃 vim regex；缺點是比 rg 慢（單執行緒、不並行）。**有 rg 就優先 `:grep`（設成 rg），`:vimgrep` 當沒 rg 時的後備。**

## 來源三：LSP references（Part 4 深用，先知道）

clangd 的 `vim.lsp.buf.references`（Ch 0 綁在 `gr`）也能把結果送 quickfix——而且這是**語意**級的（懂型別、懂作用域，不像 rg 會被同名字串騙）。讀碼追「一個函式的真正 caller」，語意版比純文字準得多。Ch 19「語意導航」會把 `gr` → quickfix → `:cnext` 這條鏈講透，這章先知道 quickfix 也吃 LSP 的料。

## quickfix 導航：`:c*` 全家

清單填好後，這組命令是操作核心：

- **`:copen`** / **`:cclose`**：開 / 關 quickfix 視窗（清單視窗裡 `<CR>` 也能跳到該項）。
- **`:cnext`**（`:cn`）/ **`:cprev`**（`:cp`）：下一項 / 上一項，游標跳到該檔該行。
- **`:cfirst`** / **`:clast`**：跳第一 / 最後一項。
- **`:cc N`**：跳第 N 項。
- **`:cdo {cmd}`**：對**每一項**執行 `{cmd}`（下一節詳談）。
- **`:cfdo {cmd}`**：對每個**涉及的檔**執行一次（不是每項，是每檔）。
- **`:colder`** / **`:cnewer`**：quickfix 有歷史堆疊，翻回上一份 / 下一份清單。你送了新的 rg 結果覆蓋舊的？`:colder` 翻回去。

裸命令太長，讀碼時 `:cnext`/`:cprev` 每項都按會累。設好用的 keymap：

```lua
-- 往 config 加：quickfix 導航 keymap（讀碼逐項看的核心）
vim.keymap.set("n", "]q", "<cmd>cnext<cr>zz", { desc = "quickfix 下一項" })
vim.keymap.set("n", "[q", "<cmd>cprev<cr>zz", { desc = "quickfix 上一項" })
vim.keymap.set("n", "<leader>qo", "<cmd>copen<cr>", { desc = "開 quickfix" })
vim.keymap.set("n", "<leader>qc", "<cmd>cclose<cr>", { desc = "關 quickfix" })
```

`]q` / `[q` 這對是 vim 社群慣例（`unimpaired` 外掛帶起來的），下一項/上一項。後面接 `zz` 是「跳過去後把該行置中」——讀碼時你想看命中行的上下文，置中比貼在螢幕頂好。**這對鍵你會按到反射**：`live_grep` → `<C-q>` 送 quickfix → `]q ]q ]q` 一路看過去。

## `:cdo` / `:cfdo`：對每項批次執行

quickfix 不只是導航清單，還能當「批次操作的目標集」。`:cdo` 對每一項跑一個 ex 命令。讀碼中最實用的是**批次改名 / 標記**：

- **全專案安全改名**：`gr` 找到某函式所有 reference 送 quickfix，`:cdo s/old_name/new_name/g | update`——對每個命中做替換並存檔。這是純文字工具做不到的「只改真正 reference」（因為來源是 LSP 語意清單）。
- **批次加標記讀**：讀懂一批命中後，`:cdo` 在每處插一行註解標「已讀」。

`:cfdo` 是「對每個涉及的**檔**跑一次」——例如 `:cfdo %s/foo/bar/g | update` 在每個有命中的檔內全域替換（不只命中那幾行）。差別：`:cdo` = 逐**項**，`:cfdo` = 逐**檔**。

> **讀碼者的 `:cdo` 心法**：把 quickfix 當成「我圈出來的一批要一起處理的位置」。找到 → 送 quickfix → 逐項看（`]q`）或批次處理（`:cdo`）。這把「散在 6 個檔的 11 個點」變成一個可一次操作的集合。

## location list：window-local 的 quickfix

location list 是 quickfix 的孿生兄弟，命令把 `c` 換成 `l`：`:lopen`、`:lnext`、`:lprev`、`:lgrep`、`:ldo`。功能一模一樣，**唯一差別：quickfix 是全域一份，location list 是綁在某個 window 的**。

為什麼要有兩個？場景：你已經有一份主攻堅清單在 quickfix（某函式的所有 caller），現在想在**某個檔裡**另外搜一批東西但不想蓋掉主清單。用 location list 開一份 window-local 的次要清單——主清單（quickfix）留著，次清單（loclist）在旁邊。

讀碼實務：**quickfix 放你的「主線工作清單」（這次攻堅要看完的一批），location list 放「臨時的、局部的次要搜尋」**。多數時候你只用 quickfix；location list 是「我想再開一份不蓋掉主清單」時才用。有些外掛（如 LSP document diagnostics）預設輸出到 location list 就是這個道理——它是那個 window 的局部資訊。

導航 keymap 也給一對：

```lua
vim.keymap.set("n", "]l", "<cmd>lnext<cr>zz", { desc = "loclist 下一項" })
vim.keymap.set("n", "[l", "<cmd>lprev<cr>zz", { desc = "loclist 上一項" })
```

## 完整讀碼流：Part 2 收斂到一次攻堅

把 Ch 9–12 串起來，一個真實動作序列（追「Lua 怎麼拋錯誤」）：

1. `<leader>fg` live_grep 打 `luaD_throw`（Ch 9–10）。
2. 掃 previewer 確認是這個（不是同名字串）。
3. `<C-q>` 把 11 個命中送 quickfix（Ch 9 樞紐 + 本章）。
4. `<leader>qo`（或自動）開 quickfix 視窗，看清單全貌——**6 個檔、11 個點，這是攻堅地圖**。
5. `]q ]q ]q` 逐項跳，每項讀那行前後的錯誤處理邏輯，看完一個都不漏。
6. 想改名 / 標記就 `:cdo`。
7. 中途想在某檔另搜一批，用 location list（`:lgrep`）不蓋掉主清單。

**這才是「組織一次攻堅」**——搜尋找到入口，quickfix 把散落的點收集成一份有進度、有全貌、逐項可跳的工作清單。Part 2 到此完整。

## 鍵位 / 命令表

| 模式 | 按鍵 / 命令 | 作用 |
|---|---|---|
| picker | `<C-q>` | 把搜尋結果送 quickfix 並開啟 |
| Cmd | `:grep -w foo src` | 跑 rg（設好 grepprg），結果進 quickfix |
| Cmd | `:vimgrep /foo/ **/*.c` | 內建搜尋（無 rg 後備），進 quickfix |
| Cmd | `:copen` / `:cclose` | 開 / 關 quickfix 視窗 |
| Normal | `]q` / `[q` | quickfix 下一項 / 上一項（+ 置中） |
| Cmd | `:cnext` / `:cprev` / `:cc N` | 下一 / 上一 / 第 N 項 |
| Cmd | `:cfirst` / `:clast` | 第一 / 最後一項 |
| Cmd | `:cdo {cmd}` | 對每一項執行命令（批次改名/標記） |
| Cmd | `:cfdo {cmd}` | 對每個涉及的檔執行一次 |
| Cmd | `:colder` / `:cnewer` | 翻 quickfix 歷史（回上一份清單） |
| Normal | `]l` / `[l` | location list 下 / 上一項 |
| Cmd | `:lopen` / `:lgrep` / `:ldo` | location list 版（window-local） |

## 對比與取捨

| 想做的事 | 用 quickfix / loclist | 別用 |
|---|---|---|
| 逐項看完一批命中 | quickfix + `]q` | 一直重開 picker 記進度 |
| 主線攻堅清單 | quickfix（全域一份） | loclist（會被別的 window 覆蓋概念混淆） |
| 局部次要搜尋、不蓋主清單 | location list | quickfix（會蓋掉主線） |
| 批次改名（語意精準） | `gr` → quickfix → `:cdo` | 手動逐檔改（漏 + 誤改同名） |

| 填 quickfix 的來源 | 精準度 | 何時用 |
|---|---|---|
| `:grep`（rg） / telescope `<C-q>` | 純文字，會中同名 | 快、通用、不需能編譯 |
| LSP references（`gr`，Part 4） | 語意，只中真 reference | 有 clangd + compile_commands 時首選 |

## 踩雷集錦

1. **`:grep` 直接跳到第一個命中、蓋掉當前檔**：`:grep` 預設跑完就跳第一項。想只填清單不亂跳，用 `:grep!`（加 `!`）或先習慣它會跳、`<C-o>`（jumplist，Ch 5）跳回來。
2. **`grepprg` 沒設，`:grep` 還在用系統 grep**：nvim 預設 `grepprg` 是 `grep -n`，慢又不 gitignore 感知。一定要設成 `rg --vimgrep`（本章 config）。設完 `:grep` 才是 rg 的威力。
3. **送了新搜尋，舊清單不見了以為丟了**：quickfix 是有歷史堆疊的，新結果不是覆蓋是壓一層。`:colder` 翻回上一份。別以為舊清單消失。
4. **搞混 quickfix 與 location list 的命令**：`:cnext` 是 quickfix，`:lnext` 是 loclist。在 loclist 按 `:cnext` 會操作到（可能空的）quickfix。記住 `c` = quickfix（全域）、`l` = location（window-local）。
5. **`:cdo` 改完沒存檔**：`:cdo s/a/b/g` 只改 buffer 不存。要 `:cdo s/a/b/g | update`（`update` 存有改動的檔）。忘了 `| update` 你的批次替換全在記憶體、關掉就沒了。

## 進階：再往深一層

- **`setqflist()` 自組清單**：Lua 能用 `vim.fn.setqflist()` 把任意來源（自己解析的 log、外部工具輸出）塞進 quickfix。讀碼時把「一份手抄的可疑位置」變成可導航清單。
- **quickfix 視窗裡編輯**：裝 `nvim-bqf` 之類的外掛能讓 quickfix 視窗更好用（模糊過濾清單、預覽）。原生的也堪用，但知道有增強選項。
- **`:make` 與 errorformat**：quickfix 本來是給編譯錯誤用的——`:make` 跑 build、把 compiler 錯誤依 `errorformat` 解析進 quickfix，`]q` 逐個錯誤跳去修。讀碼時 build 一個沒讀過的專案、用 quickfix 逐個 warning 看，也是理解 code 的路。
- **quickfix 當「外化」的載體**：reading_code 講「外化理解」——quickfix 就是一種外化。把「我這次要搞懂的一批位置」外化成清單，腦子不用記進度，專心讀 code。跟 Ch 26 的 marks/harpoon 是互補的外化工具。

## 本章重點整理

- quickfix 是一份**可導航的位置清單**（檔:行:欄:內容），是所有搜尋工具（rg / telescope / LSP / compiler）的共同下游收集點；學一次 `:c*` 導航套用到所有來源。
- 三大來源：telescope **`<C-q>`**（Part 2 樞紐）、**`:grep`**（設成 rg）、**LSP references**（`gr`，語意版，Part 4）。
- 導航核心：`]q`/`[q`（下/上一項 + 置中）、`:copen`、`:cc N`；**`:cdo`/`:cfdo`** 對每項/每檔批次執行（語意精準改名的利器）。
- **location list** 是 window-local 版（命令 `c`→`l`）：quickfix 放主線攻堅清單，loclist 放局部次要搜尋不蓋主清單。
- Part 2 到此收斂：搜尋（找得到）→ `<C-q>`（收集）→ quickfix（全貌 + 進度）→ `]q` 逐項（一個不漏）= **組織一次攻堅**。

## 自我檢核

- [ ] 我能解釋為什麼 quickfix 是「所有搜尋來源的共同終點」，以及這為什麼讓它值得學
- [ ] 我不用查就能：把 telescope 結果送 quickfix、逐項跳、跳回全貌、翻回上一份清單
- [ ] 我知道 quickfix 和 location list 的唯一差別，能講出各自該放什麼
- [ ] 我會用 `:cdo ... | update` 做語意精準的全專案改名，並知道漏掉 `| update` 的後果
- [ ] 我能把 Ch 9–12 串成一個完整動作序列：live_grep → `<C-q>` → `]q` 逐項看完一個函式的所有 caller

## 延伸閱讀

- **Neovim `:help quickfix`**
  - **讀哪裡**：開頭 overview + `:help quickfix-window`（`:copen` 那些）+ `:help :cdo`；這是 quickfix 的權威定義，本章每個 `:c*` 命令都在這
  - **注意**：同一頁也涵蓋 location list，搜 `location-list` 標籤看差異
- **Neovim `:help grepprg` 與 `:help :grep`**
  - **讀哪裡**：`grepprg`/`grepformat` 怎麼設（本章設成 rg 那段的官方說明）
- **[vim-unimpaired](https://github.com/tpope/vim-unimpaired)** — tpope
  - **讀哪裡**：`]q`/`[q`、`]l`/`[l` 這組導航慣例的來源；本章的 keymap 是手抄它常用的那幾個，想要全套（`]b` buffer、`]a` arglist…）可直接裝它
- **對照本課**：Ch 9（`<C-q>` 送 quickfix 的樞紐）；Ch 19（`gr` → quickfix 的語意版完整流程）；Ch 26（marks/harpoon，與 quickfix 互補的外化工具）；`reading_code` Ch 35「外化理解」（quickfix 是外化的載體之一）

Part 2「找得到」到此完整：telescope 找、rg 搜、fzf-lua 是大 repo 的替代前端、quickfix 把結果組織成攻堅清單。接下來的練習 B 讓你在一個真專案上把這整套跑一遍——限時定位一個功能的入口與所有相關函式，組進 quickfix 逐一檢視。之後 Part 3 進入 treesitter，讓你「看得懂結構」。

→ [練習 B：大 repo 快速定位一個功能](./practice-b-locate-a-feature.md)
