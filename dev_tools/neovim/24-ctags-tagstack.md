# Ch 24 — ctags 與 tagstack

> **目標**：用 universal-ctags 對一棵**編不起來的樹**建定義索引，然後用 Neovim **原生**（零外掛）的 `Ctrl-]` / `Ctrl-t` / `g Ctrl-]` 在符號定義間跳來跳去，並搞懂 tagstack 跟 jumplist 是兩個不同的東西。最後裝上 gutentags，讓 tags 存檔後自動跟著 code 更新。這一章給你 clangd 跪掉時**最無痛**的後備：跳定義。

> **環境**：Neovim v0.12.4，WSL2 / Ubuntu，universal-ctags 5.9.0。靶樹是 clone 下來的 Lua（`git clone --depth 1 https://github.com/lua/lua.git`）。本章 tags 檔內容、`:tag` 跳轉、tagstack 深度、gutentags 產出的 tags 都是隔離 XDG 環境下 headless 真跑照抄。

## 為什麼需要這個？

Ch 23 講清楚了：clangd 編不起來的樹就半殘。這種時候你最想要的、最基本的能力就是**跳定義**——游標停在 `luaV_execute` 上，按一個鍵跳到它定義的地方，看完按一個鍵跳回來。

好消息是：這個能力 Neovim **內建原生**，不用任何外掛、不用 LSP、不用 compile db。它靠的是 Vim 從 1991 年就有的 `tags` 檔機制。你只要用 ctags 生一張 `tags` 表，Neovim 的 `Ctrl-]` 就能用。這是所有後備手段裡最省事的一個——**一個 CLI 指令 + 兩個內建按鍵**。

壞消息（也是你必須知道的界線）：ctags 只有「定義」。它不知道「誰呼叫」、分不清同名符號。跳定義它很強，反查 caller 是下一章 gtags/cscope 的事。

## 先建立直覺：tags 檔就是一張排序過的「符號 → 位置」表

```
   ctags -R .                     Neovim
 ┌──────────────┐              ┌─────────────────────┐
 │ 掃過每個 .c/.h│              │ 游標在 luaV_execute  │
 │ 認出「定義」  │   tags 檔    │ 上，按 Ctrl-]        │
 │ 的形狀        │─────────────▶│ → 查 tags 表         │
 │ int foo(...) {│  (排序純文字) │ → 二分搜到那筆       │
 │ struct Bar {  │              │ → 開檔、跳到定義行   │
 └──────────────┘              │ → 把來源位置推進     │
                               │   tagstack（好跳回）  │
                               └─────────────────────┘
```

心智模型三句話：

1. **ctags 建表**：掃文字，把「符號名 → 檔案 → 怎麼找到那一行」記進一個叫 `tags` 的排序純文字檔。
2. **Neovim 讀表**：`Ctrl-]` 拿游標下的字去 `tags` 裡二分搜，開檔跳過去。
3. **tagstack 記路**：每跳一次，來源位置被推進一個叫 tagstack 的堆疊，`Ctrl-t` 就是把它彈出來、跳回去。

## 第一步：universal-ctags 建索引

先講清楚**是哪個 ctags**。歷史上有 exuberant-ctags（已死）和現在該用的 **universal-ctags**（活躍維護、認得現代 C）。確認你裝的是後者：

```
$ ctags --version | head -1
Universal Ctags 5.9.0, Copyright (C) 2015 Universal Ctags Team
```

對 Lua 樹遞迴建索引，`-R` 是遞迴、`.` 是當前目錄：

```
$ cd /tmp/lua_lab      # clone 下來的 lua
$ ctags -R .
```

有多快？對整棵 Lua（幾萬行 C）計時：

```
$ time ctags -R .
real	0m0.031s
```

**31 毫秒**。這個數字是重點——建索引成本可忽略，所以你可以每次 `git pull` 後無腦重建（或交給等下的 gutentags）。

### 看 tags 檔長什麼樣

```
$ wc -l tags
4462 tags

$ head -8 tags
!_TAG_FILE_FORMAT	2	/extended format; --format=1 will not append ;" to lines/
!_TAG_FILE_SORTED	1	/0=unsorted, 1=sorted, 2=foldcase/
!_TAG_OUTPUT_EXCMD	mixed	/number, pattern, mixed, or combineV2/
!_TAG_OUTPUT_FILESEP	slash	/slash or backslash/
!_TAG_OUTPUT_MODE	u-ctags	/u-ctags or e-ctags/
!_TAG_PATTERN_LENGTH_LIMIT	96	/0 for no limit/
!_TAG_PROC_CWD	/tmp/lua_lab/	//
!_TAG_PROGRAM_AUTHOR	Universal Ctags Team	//
```

`!_TAG_FILE_SORTED	1` 是關鍵：tags 檔**已排序**，所以查詢能做二分搜尋，這是 `Ctrl-]` 秒回的原因。4462 個符號，一個純文字檔。

### tags 檔的格式：symbol / 檔 / 位址

每一筆非註解行是三個 Tab 分隔的欄位（加上可選的擴充欄位）。看 `luaV_execute` 這筆：

```
$ grep -P '^luaV_execute\t' tags
luaV_execute	lvm.c	/^void luaV_execute (lua_State *L, CallInfo *ci) {$/;"	f	typeref:typename:void
```

拆開來：

| 欄位 | 內容 | 意義 |
|---|---|---|
| **symbol** | `luaV_execute` | 符號名 |
| **檔** | `lvm.c` | 定義在哪個檔 |
| **位址（跳轉 pattern）** | `/^void luaV_execute (lua_State *L, CallInfo *ci) {$/` | 一段 regex，不是行號 |
| 擴充（`;"` 之後） | `f`、`typeref:typename:void` | kind=function、回傳 void |

**為什麼位址是 regex 而不是行號**：如果檔案上面被改動、行號位移，純行號會跳錯；但只要「那一行本身」沒變，regex 照樣找得到。這讓索引就算稍微過期也還能跳對。這是 ctags 三十年的一個聰明設計。

## 第二步：Neovim 原生 tag 導航

tags 檔就位後，**不用任何外掛**，Neovim 立刻能用。先讓 Neovim 知道 tags 檔在哪——這是本 Part 往 config 加的第一塊：

```lua
-- Part 5 config 增量：讓 Neovim 找得到 tags 檔
vim.opt.tags = "./tags;,tags"
```

`./tags;` 的分號是「向上找」：從當前檔所在目錄往上一路找 `tags`，找到 project root 的那個為止。這樣你在子目錄開檔也能用到根目錄的 tags。

真跑驗證：在隔離環境下 headless 開 Lua 的 `lua.c`，執行 `:tag luaV_execute`，看它跳去哪：

```
$ nvim --headless -l check_tag.lua   # 腳本裡: edit lua.c → tag luaV_execute
tag jump ok: true
landed file: lvm.c
landed line 1204: void luaV_execute (lua_State *L, CallInfo *ci) {
tagstack depth: 1  curidx: 2
```

`:tag luaV_execute` 直接跳到 `lvm.c` 第 1204 行的定義，tagstack 深度變成 1——**跳轉與堆疊都真的動了**。

### 四個核心按鍵

`:tag <name>` 是打字版，日常你用的是**游標下**的版本：

| 模式 | 按鍵 | 作用 |
|---|---|---|
| n | `Ctrl-]` | 跳到**游標下符號**的定義（最常按） |
| n | `Ctrl-t` | 從 tagstack **跳回**上一個位置（`Ctrl-]` 的反向） |
| n | `g Ctrl-]` | 游標下符號**有多個定義**時，跳出選單讓你選（`:tselect`） |
| n | `Ctrl-w ]` | 在**分割視窗**開定義（原視窗留著對照） |

搭配的 ex 命令：

| 命令 | 作用 |
|---|---|
| `:tag <name>` | 跳到 `<name>` 的定義（可 Tab 補全符號名） |
| `:tags` | 印出 **tagstack**：你一路 `Ctrl-]` 跳過哪些符號 |
| `:tselect <name>` | 列出 `<name>` 的**所有**定義，選一個跳 |
| `:tjump <name>` | 只有一個就直接跳，多個才列選單（`g Ctrl-]` 用的就是它） |
| `:tnext` / `:tprev` | 在多個同名定義間往下 / 往上切 |
| `:ltag` | 把 tag 結果丟進 location list |

### tagstack：怎麼跳回來

讀碼是「跳進去看、跳回來、再跳別的」。tagstack 就是記錄這條「跳進去」路徑的堆疊。真跑一個完整的來回：

```
start: lua.c line 1
after Ctrl-]: lauxlib.h line 104 : LUALIB_API lua_State *(luaL_newstate) (void);   ← 跳進定義
tagstack depth=1 curidx=2
after Ctrl-t: lua.c line 1                                                            ← Ctrl-t 跳回原點
```

`Ctrl-]` 把「`lua.c` 第 1 行」推進 tagstack、跳到 `luaL_newstate`；`Ctrl-t` 把它彈出來、精準跳回起點。你可以連按好幾次 `Ctrl-]` 一路深入（tagstack 越堆越高），再連按 `Ctrl-t` 一路退回來——這就是讀 call chain 的基本手感。

> 注意上面 `Ctrl-]` 落在 `lauxlib.h` 的**巨集宣告** `LUALIB_API lua_State *(luaL_newstate)`，而不是 `lauxlib.c` 的真正函式定義。這是 ctags「同名不分辨」的現場：`luaL_newstate` 這個名字在 header 的宣告與 `.c` 的定義都被 ctags 當成「定義」收了，`Ctrl-]` 挑了排序上的第一筆。想看全部、自己選，用 `g Ctrl-]`。

## g Ctrl-] 與同名符號：ctags 的軟肋現場

ctags 沒有語意，一個名字有好幾筆時它**全給你、不排序哪個對**。這在大 C 樹很常見。Lua 裡 `next` 就有 4 筆：

```
$ readtags -t tags next
next	llex.c	/^#define next(/                                              ← 巨集
next	lobject.h	/^      struct UpVal *next;  \/* linked list *\/$/         ← struct 成員
next	lobject.h	/^    int next;  \/* for chaining *\/$/                     ← 另一個 struct 成員
next	lstate.h	/^  struct CallInfo *previous, *next;  \/* dynamic call link *\/$/
```

游標停在 `next` 上按 `Ctrl-]`，Neovim 只會跳第一筆（可能不是你要的）。按 **`g Ctrl-]`**（或 `:tselect next`）會列出全部四筆讓你挑：

```
  # pri kind tag               file
  1 F   d    next              llex.c
               #define next(...
  2 F   m    next              lobject.h
               struct UpVal *next; ...
  3 F   m    next              lobject.h
               int next; ...
  4 F   m    next              lstate.h
               struct CallInfo *previous, *next; ...
> 打數字 + Enter 選一個
```

`kind` 欄（`d`=define、`m`=member、`f`=function）幫你一眼判斷哪筆是你要的。**這就是 ctags 的天花板**：它把判斷丟回給你。clangd 能靠作用域自動選對的那筆，ctags 不能——這是你退回 tags 時要吞下的代價。實務上：先 `Ctrl-]` 試，跳錯了改 `g Ctrl-]` 自己選。

## tagstack vs jumplist：兩個不同的堆疊

新手最容易混的一點。Neovim 有**兩個**記錄「你去過哪」的堆疊，作用不同：

| | **tagstack** | **jumplist** |
|---|---|---|
| 記什麼 | 只記**tag 跳轉**（`Ctrl-]`） | 記**所有大跳轉**（`Ctrl-]`、`/` 搜尋、`G`、`gg`、`{`、標記跳…） |
| 跳回鍵 | `Ctrl-t` | `Ctrl-o`（回）/ `Ctrl-i`（前） |
| 看清單 | `:tags` | `:jumps` |
| 範圍 | 全域 | 每個 window 各一份 |
| 何時用 | 追符號定義鏈、想「退回上一個定義」 | 一般導航、想「退回剛才那個搜尋位置」 |

實務差異：`Ctrl-]` 這一個動作**同時**推進 tagstack 和 jumplist。所以你按 `Ctrl-]` 跳進定義後，`Ctrl-t` 和 `Ctrl-o` **都**能跳回。差別在後續：如果你跳進定義、又用 `/` 搜了幾次、又 `G` 到檔尾——這時 `Ctrl-t` 會無視那些搜尋，直接把你彈回「跳定義之前」的位置（因為它只認 tag）；而 `Ctrl-o` 會一步步倒退經過每個搜尋位置。

一句話記法：**`Ctrl-t` 是「退出這層定義」，`Ctrl-o` 是「退回上一個落腳點」**。追 call chain 用 `Ctrl-t`（乾淨地退出符號層層深入），一般亂逛用 `Ctrl-o`。Ch 5 講的 jumplist 在這裡跟 tagstack 分工。

## 第三步：gutentags 自動更新 tags

手動 `ctags -R .` 的問題是**會忘**：你 `git pull`、切 branch、改了檔，tags 還是舊的，`Ctrl-]` 跳到錯行。解法是 **vim-gutentags** 外掛——它偵測 project root、背景重建 tags、存檔後增量更新，你完全不用手動跑 ctags。

這是本 Part config 的第二塊。加進 Ch 0 的 `require("lazy").setup({...})`：

```lua
-- Part 5 config 增量：gutentags 自動管理 tags
{ "ludovicchabant/vim-gutentags",
  init = function()
    vim.g.gutentags_modules = { "ctags" }                 -- 用 ctags 後端（Ch 25 會加 gtags）
    vim.g.gutentags_project_root = { ".git", "Makefile", "configure.ac" }  -- 這些檔標記 project root
    vim.g.gutentags_cache_dir = vim.fn.stdpath("cache") .. "/gutentags"    -- tags 存到 cache，不弄髒 repo
    vim.g.gutentags_ctags_extra_args = { "--fields=+niazS" }               -- 多帶 kind/line/typeref/signature
  end },
```

`gutentags_cache_dir` 這行很重要：預設 gutentags 會在 project root 生 `tags` 檔，弄髒你的 repo（還得加進 `.gitignore`）。指到一個 cache 目錄後，所有專案的 tags 集中放、不污染 source。

### 真跑：gutentags 真的自動生了 tags 嗎

在隔離環境開一個小 C 專案（有 `.git` 當 root marker），headless 開 `a.c`，等 gutentags 背景跑完，看 cache：

```
$ nvim --headless -u init.lua -l check_gutentags.lua
loaded_gutentags: 1
cache contents:
_wildignore.options
tmp-gtlab-tags
grep helper in tmp-gtlab-tags:
helper	/tmp/gtlab/a.c	/^int helper(void){return 1;}$/;"	kind:f	line:1	typeref:typename:int	signature:(void)
main	/tmp/gtlab/a.c	/^int main(void){return helper();}$/;"	kind:f	line:2	typeref:typename:int	signature:(void)
```

三件事確認了：gutentags **載入**（`loaded_gutentags: 1`）、**自動在 cache 生了 tags 檔**（`tmp-gtlab-tags`）、而且**帶了我們設定的擴充欄位**（`kind:f`、`line:1`、`typeref:typename:int`、`signature:(void)`——這正是 `--fields=+niazS` 的效果）。你打開檔案它就默默把索引建好了，之後 `Ctrl-]` 直接能用。

### gutentags 的底層：它其實就是幫你跑 ctags

別把 gutentags 想成魔法。它做的事：偵測到你進了一個有 `.git` 的專案 → 在背景 `ctags -R` → 存檔（`BufWritePost`）時再跑一次增量更新 → 把 `&tags` 指到它生的檔。**本質就是自動化 `ctags -R .`**，把「記得重建索引」這件事從你腦袋卸掉。理解這點，gutentags 出問題時（tags 沒更新、跳到舊行）你知道去看它有沒有偵測到 root、ctags 是不是 universal 版。

| 模式 | 按鍵 / 命令 | 作用 |
|---|---|---|
| — | `:GutentagsUpdate` | 手動觸發一次重建（懷疑 tags 舊了時） |
| — | `:GutentagsToggleTrace` | 開 trace，看 gutentags 到底在背景跑什麼（debug 用） |
| n | `Ctrl-]` | （gutentags 生完 tags 後）跳定義，跟原生一樣 |

## 對比與取捨

| 面向 | 手動 `ctags -R .` | gutentags | clangd（對照） |
|---|---|---|---|
| 要不要編譯 | 否 | 否 | **要** |
| 索引誰維護 | 你（會忘） | 外掛自動 | LSP 背景 |
| 建索引速度 | 0.03s | 背景無感 | 分鐘級 |
| 跳定義 | ✓（原生 `Ctrl-]`） | ✓ | ✓ 更準 |
| 反查 caller | **不能** | **不能** | ✓ |
| 同名分辨 | 否（`g Ctrl-]` 自己選） | 否 | ✓（作用域） |
| 弄髒 repo | 會（tags 檔） | 否（cache_dir） | 否 |

**選型**：只要「跳定義」、樹編不起來、要最無痛 → gutentags + `Ctrl-]`，這是後備手段的第一層。要「反查誰呼叫」→ ctags 給不了，往下一章的 gtags/cscope。要「同名分辨 / 巨集真相」→ 這是 tags 的天花板，能編就回 clangd。

## 踩雷集錦

1. **裝到 exuberant-ctags（老死版）或 BSD ctags**。老版本認不得現代 C（`typeref`、部分 C11），生出來的 tags 殘缺。一律確認 `ctags --version` 開頭是 `Universal Ctags`。macOS 內建的是 BSD ctags，要 `brew install universal-ctags`。

2. **以為 `Ctrl-]` 一定跳對**。同名符號它只跳排序第一筆，常常是 header 宣告而非 `.c` 定義（本章 `luaL_newstate` 就是現場）。跳錯別罵工具，改用 `g Ctrl-]` 自己從清單選——ctags 沒語意，判斷是你的事。

3. **把 tagstack 跟 jumplist 搞混**。`Ctrl-t` 只退 tag 跳轉，`Ctrl-o` 退所有跳轉。你追 call chain 深入好幾層後想「乾淨退出」，該按 `Ctrl-t`；按 `Ctrl-o` 會被中間的搜尋/`G` 卡住一步步退。搞混會覺得「怎麼退不回去」。

4. **索引過期跳到錯行**。切 branch / pull 後沒重建，tags 指著舊位置。裝了 gutentags 大多自動處理，但大改動（rebase、merge）後若跳詭異，先 `:GutentagsUpdate` 或手動 `ctags -R .`。

5. **gutentags 把 tags 生在 repo 裡弄髒 git**。不設 `gutentags_cache_dir` 的預設行為。一定設 cache_dir，否則每個專案 root 冒出一個 `tags` 檔，還要記得加 `.gitignore`。

6. **`&tags` 沒設，`Ctrl-]` 報 `E433: No tags file`**。Neovim 預設 `&tags` 是 `./tags,./TAGS,tags,TAGS`——多數情況夠用，但深層子目錄可能找不到。用本章的 `./tags;,tags`（分號向上找）最保險。gutentags 會自己接管 `&tags`，但手動建 tags 時要確認這個。

## 進階：再往深一層

- **`tagfunc`：自訂 tag 怎麼查**。`:help tag-function` 允許你用 Lua 函式接管 `Ctrl-]` 的解析——例如讓它同時查 tags 檔和 LSP，或做更聰明的同名過濾。進階但少用；知道有這個鉤子，哪天 `Ctrl-]` 行為不合意時能改。

- **`--fields` 擠更多欄位**。本章 config 用 `--fields=+niazS`（name/line/kind/access/signature）。想只列 struct 成員或只列回傳某型別的函式時，這些欄位讓 `readtags` / 外掛能做精準過濾。`ctags --list-fields` 看全部可用欄位。

- **`.ctags` / `.ctags.d/` 專案設定檔**。在 repo 放 `.ctags.d/foo.ctags` 可以客製化：排除 `test/`、加自訂語言的 kind、認得專案自己的巨集風格（如 kernel 的 `SYSCALL_DEFINE`）。大專案讀碼前值得配一份。

- **`readtags` 命令列查詢**（回扣 `reading_code` Ch 14）：不開編輯器也能查 tags——`readtags -t tags luaV_execute`。批次腳本化（查一堆符號定義在哪、輸出 CSV）時用它，Neovim 內互動時用 `Ctrl-]`。同一張表兩種吃法。

## 本章重點整理

- universal-ctags `ctags -R .` **不編譯**、**毫秒級**建一張排序的 `tags` 定義索引；位址是 regex 不是行號（稍過期也跳得對）。
- Neovim **原生**支援 tag：`Ctrl-]` 跳定義、`Ctrl-t` 跳回、`g Ctrl-]` 多定義選單、`:tag`/`:tselect`/`:tags`。零外掛。
- **tagstack ≠ jumplist**：`Ctrl-t` 只退 tag 跳轉、`Ctrl-o` 退所有跳轉。追 call chain 用 `Ctrl-t`。
- ctags 的天花板：**只有定義、不反查 caller、同名不分辨**（`g Ctrl-]` 把判斷丟回給你）。
- **gutentags** 自動化 `ctags -R`：偵測 root、背景重建、存檔增量更新、tags 進 cache 不弄髒 repo——本 Part config 加的第二塊。

## 自我檢核

- [ ] 我知道 tags 檔三個核心欄位是什麼，以及位址為什麼用 regex 不用行號
- [ ] 不看筆記，我能說出 `Ctrl-]` / `Ctrl-t` / `g Ctrl-]` 各做什麼
- [ ] 我能講清 tagstack 和 jumplist 的差別，以及 `Ctrl-t` vs `Ctrl-o` 何時用哪個
- [ ] 我知道為什麼 `Ctrl-]` 有時跳到 header 宣告而非 `.c` 定義，以及怎麼救
- [ ] 我知道 gutentags 本質就是自動跑 ctags，以及為什麼要設 `cache_dir`
- [ ] 我能說出 ctags 相對 clangd 的優勢與那個「不能反查 caller」的界線

## 延伸閱讀

### Neovim 內建 `:help`（優先）

- **`:help tagsrch.txt`（`:help tag-commands`）**
  - **讀哪裡**：`Ctrl-]`、`Ctrl-t`、`:tag`、`:tselect`、`:tags` 那幾節——這是本章所有按鍵的權威定義
  - **學什麼**：`g Ctrl-]` 與 `:tjump` 的差別、priority 排序規則（為什麼跳第一筆）
- **`:help tagstack`（`:help gettagstack()`）**
  - **讀哪裡**：tagstack 結構那段，對照本章「tagstack vs jumplist」
  - **學什麼**：`curidx` / `items` 的意義，理解 `Ctrl-t` 在堆疊上怎麼移動
- **`:help 'tags'`**
  - **讀哪裡**：`tags` 選項的路徑語法，特別是 `;`（向上找）與 `./`（相對開啟檔）
  - **學什麼**：本章 `./tags;,tags` 為什麼這樣寫

### 工具

- **[universal-ctags 官方文件](https://docs.ctags.io/)**
  - **讀哪裡**：`ctags(1)` man page 的 `--fields`、`--kinds-<LANG>`、`--languages`
  - **學什麼**：怎麼擠更多欄位、怎麼只索引 C、怎麼排除 test 目錄
- **[vim-gutentags](https://github.com/ludovicchabant/vim-gutentags)** — Ludovic Chabant
  - **讀哪裡**：README 的 options 那節，特別是 `gutentags_cache_dir`、`gutentags_modules`（下一章會加 gtags module）
  - **這是什麼**：本章自動更新 tags 的外掛，Ch 25 會讓它同時管 ctags 和 gtags

跳定義搞定了，但你一定會撞到 ctags 的牆：**「這個函式到底誰呼叫？」** ctags 答不出來。下一章換 GNU Global（gtags）和 cscope——同樣不用 build，但能反查 caller/callee，把整棵編不起來的樹的呼叫關係攤開給你看。

→ [Ch 25 GNU Global（gtags）與 cscope](./25-gtags-cscope.md)
