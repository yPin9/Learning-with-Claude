# Ch 25 — GNU Global（gtags）與 cscope

> **目標**：用 GNU Global（gtags）和 cscope 對一棵**編不起來的樹**建**交叉引用**索引，然後在 Neovim 裡秒查「這個函式**誰呼叫**（caller）、它**呼叫誰**（callee）」——這是 ctags 給不了、clangd 沒 compile db 時也給不了的能力。過程中你會撞到一個關鍵事實：**Neovim 移除了 Vim 內建的 `:cscope`**，照抄舊教學會直接報錯；本章給你 Neovim 上真正能用的整合方式。

> **環境**：Neovim v0.12.4，WSL2 / Ubuntu。GNU Global/gtags 6.6.7、cscope 15.9。靶樹是 clone 的 Lua。本章 `gtags` 建索引時間、`global -x/-rx` 查詢、cscope `-2/-3` 查詢、Neovim 內 quickfix 灌入，全是隔離 XDG 環境 headless 真跑照抄。

## 為什麼需要這個？

Ch 24 的 ctags 讓你在編不起來的樹裡跳定義。但讀碼真正卡人的問題不是「定義在哪」，是**「誰呼叫這個函式？」**——你要往上追一條 call chain、看一個函式在什麼情境下被用、評估改它的衝擊面。ctags 對這個問題**束手無策**，因為它只索引定義，沒有「使用」的資料。

GNU Global 和 cscope 補上這塊。它們建的是**交叉引用資料庫**：不只記「`luaV_execute` 定義在哪」，還記下**每一處用到它的地方**，以及那個用法是「呼叫」還是「宣告」。這讓「誰呼叫 `luaV_execute`」變成一條秒回的指令——而且**同樣不用編譯**，指到目錄就索引。這是 clangd 的 `gr`（find references）在**沒有 compile db** 時的替身。

## 先建立直覺：從「定義索引」到「交叉引用」

```
   ctags（Ch 24）              gtags / cscope（本章）
 ┌──────────────┐           ┌──────────────────────────┐
 │ luaV_execute  │           │ luaV_execute              │
 │  定義在 lvm.c │           │  定義在 lvm.c:1204        │
 │               │           │  被呼叫 @ ldo.c:768,876,933│  ← 多了「誰用它」
 │  （就這樣）   │           │  它呼叫了 aeProcessEvents…│  ← 多了「它用誰」
 └──────────────┘           │  被宣告 @ lvm.h:128       │
                            └──────────────────────────┘
      只有定義                    定義 + 所有引用（雙向）
```

兩個工具做同一件事（建交叉引用），分工是：

- **GNU Global（gtags）**：輸出乾淨、格式固定、多語言、好整合進 Neovim quickfix。**本 Part config 的主力。**
- **cscope**：對 C 的呼叫關係分析最深（區分呼叫 / 定義 / 宣告），有九種查詢，caller 查詢（`-3`）最精準。但**Neovim 內建整合被移除了**，得繞路。

## GNU Global（gtags）：不 build 索引整棵樹

### 建索引：GTAGS / GRTAGS / GPATH

在樹的根目錄跑 `gtags`，它生三個檔：

```
$ cd /tmp/lua_lab
$ time gtags
real	0m0.046s
$ ls -la GTAGS GRTAGS GPATH
-rw-r--r-- 1 ypp ypp  253952 GTAGS    ← 定義索引
-rw-r--r-- 1 ypp ypp  598016 GRTAGS   ← 引用索引（reference，caller 就在這）
-rw-r--r-- 1 ypp ypp   16384 GPATH    ← 檔案路徑映射
```

**46 毫秒**建完整棵 Lua。三個檔的分工：`GTAGS` 記定義、`GRTAGS` 記**引用**（誰用了這符號——caller 的來源）、`GPATH` 記路徑。不用 `compile_commands.json`、不用編譯、內建 C parser。

### 查詢：定義、引用、其他符號、grep

`global` 是查詢端。四個你會天天用的查詢：

**`-x` 定義**（帶行號與原始行）：

```
$ global -x luaV_execute
luaV_execute     1204 lvm.c            void luaV_execute (lua_State *L, CallInfo *ci) {
```

**`-rx` 所有引用 / caller**（`-r` = reference，這是 clangd `gr` 的無 build 版）：

```
$ global -rx luaV_execute
luaV_execute      768 ldo.c                luaV_execute(L, ci);  /* call it */
luaV_execute      876 ldo.c                  luaV_execute(L, ci);  /* execute down to higher C 'boundary' */
luaV_execute      933 ldo.c                  luaV_execute(L, ci);  /* just continue running Lua code */
luaV_execute      128 lvm.h            LUAI_FUNC void luaV_execute (lua_State *L, CallInfo *ci);
```

一條指令從整棵樹撈出 `luaV_execute` 的三個真呼叫點（都在 `ldo.c`，Lua 的執行迴圈核心）加一個 header 宣告。**注意最後那筆 `lvm.h:128` 是宣告不是呼叫**——這是 `-r` 的特性：它給的是「引用」，包含 header 宣告。要純呼叫點得用 cscope `-3`（下面）。

**`-s` 其他符號**（沒有定義的符號，如巨集、外部符號的使用點）：

```
$ global -sx NULL | head -3
NULL              421 lapi.c                 if (len != NULL) *len = 0;
NULL              423 lapi.c                 return NULL;
NULL              430 lapi.c             if (len != NULL)
```

**`-g` grep pattern**（在 source 裡搜任意 pattern，含註解字串）：

```
$ global -gx "Ready to" | head -1     # 找 log 訊息、字串常數
```

還有兩個好用的：`global -f lvm.c`（列某檔所有定義，迷你 outline）、`global -c luaV`（前綴補全，做 shell 補全用）。

### 腳本友善格式：`--result=grep`

這是 gtags 相對 cscope 最重要的甜點——輸出 `file:line:text`，任何吃 grep 輸出的工具（Neovim quickfix、fzf、CI）都能直接消化：

```
$ global --result=grep -r luaV_execute
ldo.c:768:    luaV_execute(L, ci);  /* call it */
ldo.c:876:      luaV_execute(L, ci);  /* execute down to higher C 'boundary' */
ldo.c:933:      luaV_execute(L, ci);  /* just continue running Lua code */
lvm.h:128:LUAI_FUNC void luaV_execute (lua_State *L, CallInfo *ci);
```

**這個格式是本 Part config 整合 gtags 的關鍵**——下面我們就用它灌 quickfix。

## 整合進 Neovim：先講清楚 cscope 為什麼不能照抄 Vim

你會在網路上看到大量 Vim 的 cscope 教學：`set cscopeprg=gtags-cscope`、`cscope add GTAGS`、`Ctrl-\ c` 查 caller。**這些在 Neovim 全部報錯**——Neovim 移除了 Vim 的內建 cscope 支援。headless 驗證給你看：

```
$ nvim --headless -u NONE \
    "+lua print('has cscope: '..vim.fn.has('cscope'))" \
    "+set cscopeprg=gtags-cscope" +qa
has cscope: 0
E518: Unknown option: cscopeprg=gtags-cscope
```

`has('cscope')` 回 **0**（沒有），`set cscopeprg` 報 **`E518: Unknown option`**，`:cscope` 命令也不存在（`E492: Not an editor command`）。這不是你設錯——是 Neovim 早年就把 `:cscope`/`Ctrl-\` 那套 legacy 命令整組拔掉了。**照抄 Vim 教學是這一章最大的坑。**

那 Neovim 上怎麼用 gtags/cscope？兩條路：

1. **`global --result=grep` + quickfix**（本課採用）：不靠任何 cscope 相容層，直接把 `global` CLI 的輸出灌進 quickfix list。乾淨、可控、無外掛依賴。
2. **外掛**（如 `gtags.vim`、`cscope_maps.nvim`）：包裝上面這件事。想要現成 UI 可裝，但原理一樣是背後跑 CLI。

### 本 Part config：把 global 查詢灌進 quickfix

這是 Part 5 config 加的第三塊。核心是一個小函式：拿游標下的字、跑 `global --result=grep`、把結果塞進 quickfix、開 quickfix 視窗。

```lua
-- Part 5 config：gtags 當後備索引（Neovim 無內建 cscope，改走 global CLI + quickfix）
local function global_query(flag, sym)
  sym = sym or vim.fn.expand("<cword>")                       -- 游標下的符號
  local out = vim.fn.systemlist({ "global", "--result=grep", flag, sym })
  if vim.v.shell_error ~= 0 or #out == 0 then
    vim.notify("gtags: no result for " .. sym, vim.log.levels.WARN)
    return
  end
  vim.fn.setqflist({}, " ", { title = "global " .. flag .. " " .. sym, lines = out })
  vim.cmd("copen")                                            -- 開 quickfix
end

vim.keymap.set("n", "<leader>gd", function() global_query("-d") end, { desc = "gtags: 定義" })
vim.keymap.set("n", "<leader>gr", function() global_query("-r") end, { desc = "gtags: 所有引用/caller" })
vim.keymap.set("n", "<leader>gs", function() global_query("-s") end, { desc = "gtags: 其他符號" })
```

`setqflist` 的 `lines = out` 直接吃 `file:line:text` 格式（Neovim 用內建 `&errorformat` 解析），所以 `--result=grep` 才這麼好接。quickfix 開起來後，`Ctrl-]` 那套不管用，改用 quickfix 導航：`:cnext`/`:cprev` 或在 quickfix 視窗 `<CR>` 開檔（Ch 12 的東西）。

### 真跑：`<leader>gr` 查 caller 灌 quickfix

headless 模擬「游標在 `luaV_execute` 上按 `<leader>gr`」——跑 `global -r` 並灌 quickfix：

```
$ nvim --headless -u init.lua -l check_gr.lua   # edit lvm.c → global -r luaV_execute → setqflist
global -r luaV_execute entries: 4
quickfix loaded: 4
  ldo.c:768:     luaV_execute(L, ci);  /* call it */
  ldo.c:876:       luaV_execute(L, ci);  /* execute down to higher C 'boundary' */
  ldo.c:933:       luaV_execute(L, ci);  /* just continue running Lua code */
  lvm.h:128: LUAI_FUNC void luaV_execute (lua_State *L, CallInfo *ci);
keymap <leader>gd set: true
keymap <leader>gr set: true
keymap <leader>gs set: true
```

三件事確認了：`global -r` 撈到 4 筆、**全部進了 quickfix**（Neovim 正確解析了 `file:line:text`）、三個 `<leader>g*` keymap 都註冊成功。你現在在編不起來的樹裡，一鍵反查 caller 並得到一個可導航的工作清單——這正是 clangd 缺席時最想要的。

> 互動 UI 無法貼截圖：實際使用是游標停在符號上按 `<leader>gr`，quickfix 視窗跳出來列所有 caller，`j/k` 選、`<CR>` 跳過去看。上面 headless 驗證的是底層（查詢 + quickfix 灌入 + keymap 註冊）確實可執行。

## cscope：caller/callee 最精準，但要繞過 Neovim

cscope 對 C 呼叫關係的分析比 gtags 更細——它區分「呼叫 / 定義 / 宣告 / 賦值」，九種查詢。因為 Neovim 沒內建整合，我們**在 CLI 用它**（`-L -N` line mode），需要精準 caller 時把它當外部工具跑。

### 建 cscope.out 並查 caller / callee

```
$ ls *.c *.h > cscope.files      # 明列要索引的檔（比 -R 可控）
$ time cscope -b -q -k           # -b 只建庫 -q 反向索引 -k 不搜 /usr/include
real	0m0.040s
```

**`-3` 誰呼叫它（caller，讀碼最需要的反查）**：

```
$ cscope -d -L -3 luaV_execute
ldo.c ccall 768 luaV_execute(L, ci);
ldo.c unroll 876 luaV_execute(L, ci);
ldo.c resume 933 luaV_execute(L, ci);
```

看差別：cscope `-3` 給的**只有真正的呼叫點**（過濾掉了 `lvm.h:128` 那個宣告，gtags `-rx` 會混進來），而且**多一欄「所在函式」**——`luaV_execute` 分別在 `ccall`、`unroll`、`resume` 三個函式裡被呼叫。這欄是追 call chain 的金礦：你一眼知道往上一層是誰。這是 cscope 相對 gtags 的殺手級精度。

**`-2` 它呼叫了誰（callee，往下追）**：

```
$ cscope -d -L -2 luaV_execute | head -4
lvm.c ci_func 1216 cl = ci_func(ci);
lvm.c luaG_tracecall 1220 trap = luaG_tracecall(L);
lvm.c vmfetch 1225 vmfetch();
lvm.c luaG_getfuncline 1230 ... luaG_getfuncline(cl->p, pcrel), ...
```

`luaV_execute` 函式體裡呼叫了 `ci_func`、`luaG_tracecall`、`vmfetch`…——往下一層的 callee。`-2` callee、`-3` caller，別搞反（記法：`-3` 找「上游三代祖宗」）。

### 在 Neovim 裡用 cscope：兩個務實選項

因為沒有內建 `:cscope`：

1. **`:!cscope -d -L -3 <cword>`**：直接 shell out，快而髒。要進 quickfix 就套跟 gtags 一樣的 `setqflist` 手法（cscope 的 `-L` 輸出不是 `file:line:text`，得自己轉格式，比 gtags 麻煩——這就是為什麼 config 主力用 gtags）。
2. **裝 `cscope_maps.nvim` 之類的外掛**：它重新實作了 `:Cscope find c foo` 命令並灌 quickfix，把 caller 查詢包好。想要 cscope 的精度又要 Neovim 整合，這是最省事的一條。

實務建議：**日常 caller 查詢用 config 裡的 `<leader>gr`（gtags，夠好又乾淨），需要「純呼叫點、帶所在函式」的精準度時，跳 terminal 跑 `cscope -L -3`**。兩個工具都建、各取所長。

## gtags 的自動更新：gutentags 的 gtags 模組在 Neovim 不可用

Ch 24 用 gutentags 自動更新 ctags。gutentags 也有 `gtags_cscope` 模組——但它在 Neovim **會直接報錯**，因為該模組依賴 `has('cscope')`：

```
Can't enable the gtags-cscope module for Gutentags
```

（這是我真的把 `gutentags_modules = { 'ctags', 'gtags_cscope' }` 打開後 headless 撞到的錯——又一次證實 Neovim 拔掉 cscope 的連鎖效應。）

所以 GTAGS 的更新走另兩條路，加進 config：

```lua
-- 手動 / 半自動重建 GTAGS（gutentags 的 gtags 模組在 Neovim 不可用）
vim.api.nvim_create_user_command("Gtags", function()
  local root = vim.fs.root(0, { ".git", "Makefile", "GTAGS" }) or vim.fn.getcwd()
  vim.fn.system({ "sh", "-c", "cd " .. vim.fn.shellescape(root)
    .. " && (test -f GTAGS && global -u || gtags)" })   -- 有 GTAGS 就增量更新，否則全建
  vim.notify("gtags updated at " .. root, vim.log.levels.INFO)
end, {})
```

`global -u`（update）只重建變動檔的索引，比全建省——大樹（kernel）值得用。另一條是 **git hook**：在 `.git/hooks/post-checkout` 與 `post-merge` 放 `gtags` 或 `global -u`，切 branch / pull 後自動更新（`reading_code` Ch 14 有完整腳本）。

## 鍵位表

| 模式 | 按鍵 / 命令 | 作用 | 底層 |
|---|---|---|---|
| n | `<leader>gd` | gtags 查游標下符號**定義**，灌 quickfix | `global -d` |
| n | `<leader>gr` | gtags 查游標下符號**所有引用 / caller**，灌 quickfix | `global -r` |
| n | `<leader>gs` | gtags 查**其他符號**（無定義的使用），灌 quickfix | `global -s` |
| c | `:Gtags` | 重建 / 增量更新 GTAGS | `global -u` \|\| `gtags` |
| c | `:cnext` / `:cprev` | 在 quickfix 結果間跳（Ch 12） | 內建 |
| c | `:copen` / `:cclose` | 開 / 關 quickfix 視窗 | 內建 |

（cscope 精準查詢在 terminal：`cscope -d -L -3 <sym>` caller、`-L -2` callee。）

## 對比與取捨

| 面向 | ctags（Ch 24） | GNU Global (gtags) | cscope |
|---|---|---|---|
| 反查 caller | **不能** | ✓（`-rx`，混宣告） | ✓ **最精準**（`-3`，帶所在函式） |
| 反查 callee | 不能 | 間接 | ✓（`-2`） |
| 建索引速度（Lua） | 0.03s | 0.05s | 0.04s |
| 輸出腳本友善 | 中 | **高**（`--result=grep`） | 中（`-L`，非 grep 格式） |
| 多語言 | 廣 | 廣（plugin） | C/C++ 為主 |
| Neovim 整合 | **內建原生** | grepprg / 函式 + quickfix | **內建已移除**，要外掛 / CLI |
| 自動更新 | gutentags ✓ | `:Gtags` / git hook（gutentags gtags 模組不可用） | 手動 / git hook |

**選型**：Neovim 裡日常反查 caller → **gtags（`<leader>gr`）**，格式乾淨、整合無痛、多語言。要「純呼叫點 + 所在函式」的最高精度、且是純 C → **cscope `-3`**（在 terminal 或外掛）。只要跳定義 → ctags（Ch 24）最輕。三個都不編譯，都是 clangd 跪掉時的後備。

## 踩雷集錦

1. **照抄 Vim 的 `set cscopeprg` / `:cscope` / `Ctrl-\ c`**。Neovim 移除了內建 cscope，`has('cscope')` 是 0，設 `cscopeprg` 報 `E518`。這是本章頭號坑。改用 `global --result=grep` + quickfix，或 cscope 外掛。

2. **gutentags 開 `gtags_cscope` 模組**。它依賴 `has('cscope')`，在 Neovim 直接報「Can't enable the gtags-cscope module」。gutentags 在 Neovim 只用 `ctags` 模組；GTAGS 用 `:Gtags` 或 git hook 更新。

3. **`-2` 和 `-3` 方向搞反**。`-2` 是 callee（它呼叫誰、往下），`-3` 是 caller（誰呼叫它、往上）。搞反了追 data flow 整個朝錯方向。gtags 這邊：`-d` 定義、`-r` 引用（含 caller）、`-s` 其他符號。

4. **把 `global -rx` 當成純 caller**。`-r` 給的是**引用**，包含 header 宣告（本章 `lvm.h:128`）。要只有呼叫點，cscope `-3` 更準。gtags 勝在格式與整合，不勝在精度。

5. **cscope 吞進 `/usr/include`**。不加 `-k`（kernel mode）時它會去搜系統標頭，索引膨脹、混入 libc 符號。純專案查詢一律 `-k`，並用 `cscope.files` 明列範圍。

6. **索引過期**。GTAGS/cscope.out 是快照，`git pull` / 切 branch 後不更新就查到舊行號。`:Gtags`（`global -u` 增量）或 git hook 自動化，別手動記。

## 進階：再往深一層

- **`GTAGSLABEL` 換 parser 認更多語言**：`GTAGSLABEL=pygments gtags` 或 `new-ctags` 讓 gtags 借 ctags/pygments 認幾十種語言。這是它在混語言樹（C + Python + Go）勝過 cscope 的地方——一個 GTAGS 跨語言查。

- **`htags` 產 HTML 導覽**：`htags -h -F` 把整棵樹變成可點擊的靜態網站，每個符號是超連結。遠端伺服器沒 IDE、或要給團隊一份可導覽 snapshot 時很實用（`reading_code` Ch 14 有示範）。

- **cscope 的 TUI**：`cscope -d` 進九個輸入框的互動介面，探索期（還不知道要查什麼）比背 `-N` 順手。Neovim 裡叫不出來，但 terminal 分割一個視窗跑它，跟 nvim 並用。

- **gtags + fzf**：把 `global -c`（補全）或 `global --result=grep -r` 的輸出餵進 fzf（Ch 11），做「模糊搜 caller」的互動 UI。config 的 quickfix 版適合「全看一遍」，fzf 版適合「知道大概、模糊縮」。

- **兩層工作流**（回扣 Ch 23、接 Ch 28）：clangd 掛 `gd`/`gr`（能編時精準），gtags 掛 `<leader>g*`（編不起來時的後備）。同一 buffer，clangd 跳空了就手一滑按 gtags 那組。這套「主武器 + 後備」是讀陌生大樹的核心節奏。

## 本章重點整理

- gtags / cscope 建**交叉引用**索引，不編譯、秒級，能反查「誰呼叫」——ctags 和沒 compile db 的 clangd 都給不了。
- **GNU Global（gtags）**：`gtags` 建 GTAGS/GRTAGS/GPATH；`global -x` 定義、`-rx` 引用/caller、`-s` 其他符號、`-g` grep；**`--result=grep` 是整合的關鍵格式**。
- **Neovim 移除了內建 `:cscope`**（`has('cscope')`=0，`cscopeprg` 報 `E518`）——照抄 Vim 教學必踩。改用 `global --result=grep` + quickfix（本課 config）或外掛。
- **cscope `-3` caller 最精準**（純呼叫點 + 所在函式），`-2` callee；但 Neovim 整合要繞路（CLI 或外掛），日常反查用 gtags `<leader>gr` 更無痛。
- gutentags 的 gtags 模組在 Neovim **不可用**（依賴 cscope）；GTAGS 用 `:Gtags`（`global -u` 增量）或 git hook 更新。

## 自我檢核

- [ ] 我知道 gtags 建的三個檔各存什麼，以及 `-rx` 為什麼會混進 header 宣告
- [ ] 我能說出 Neovim 上為什麼不能設 `cscopeprg`，以及本課改用什麼整合 gtags
- [ ] 不看筆記，我能說出 cscope `-2` 和 `-3` 哪個是 caller、哪個是 callee
- [ ] 我知道 `global -rx` 和 cscope `-3` 給的東西差在哪，各自何時用
- [ ] 我能解釋 `<leader>gr` 背後那個函式做了哪三件事（查、灌 quickfix、開視窗）
- [ ] 我知道為什麼 gutentags 不能在 Neovim 管 GTAGS，以及 GTAGS 該怎麼更新

## 延伸閱讀

### Neovim 內建 `:help`（優先）

- **`:help quickfix`（`:help setqflist()`）**
  - **讀哪裡**：`setqflist()` 的 `lines` 與 `{what}` 參數——本章 config 把 global 輸出灌 quickfix 的核心 API
  - **學什麼**：為什麼 `--result=grep` 的 `file:line:text` 能被 `&errorformat` 直接解析
- **`:help 'grepprg'`（`:help :grep`）**
  - **讀哪裡**：grepprg / grepformat；另一種整合 gtags 的路（`grepprg=global\ --result=grep`）
  - **學什麼**：quickfix 的通用「外部工具 → 工作清單」模式，gtags 只是其中一種來源

### 工具

- **[GNU Global 官方手冊](https://www.gnu.org/software/global/manual/global.html)**
  - **讀哪裡**：`global` 的 `-r` / `-s` / `-x` / `--result` 選項；`gtags` 的 `-u` 增量、`GTAGSLABEL`
  - **學什麼**：本章每個查詢的完整語意、多語言 parser 怎麼切
- **[cscope man page](https://cscope.sourceforge.net/cscope_man_page.html)**
  - **讀哪裡**：`-L -N` line mode 的九個 `-N` 數字，特別是 `-2`/`-3`
  - **學什麼**：九種查詢的完整清單，terminal 裡直接用 cscope 時的參考

### 本課與姊妹課

- **`soft_skills/reading_code` Ch 14「離線索引三巨頭」**
  - **這是什麼**：本章的 CLI 原理母章，redis 上真跑 `cscope -L -3`、`global -rx`、htags、git hook 自動化
  - **讀哪裡**：cscope 九種查詢那張表、git hook 腳本、「誰呼叫 aeMain」三工具對照
- **本課 Ch 12「quickfix / location list」**
  - **這是什麼**：本章 gtags 結果落腳的地方；`<leader>gr` 灌完 quickfix 後怎麼導航
  - **讀哪裡**：`:cnext`/`:cprev`、quickfix 視窗操作

三個後備索引（ctags 定義、gtags/cscope 交叉引用）都會用了。下一個練習把它們用在最硬的靶上：一棵**編不起來的 Linux kernel 子系統**——不 build、用 gtags 找一個 syscall handler 的定義與所有 caller，追一條真實的 call chain。

→ [練習 E：對編不起來的樹用 gtags 導航](./practice-e-navigate-unbuildable-tree.md)
