# Ch 6 — 搜尋：/ ? * # 與 regex

> **目標**：掌握 Neovim **單檔內**的搜尋——`/` `?` 搜、`n` `N` 巡、`*` `#` 搜游標下的字、very magic mode（`\v`）讓 regex 正常一點、以及讀碼最有用的 `:g/pattern/`（一次列出檔內所有配對行，等於「本檔的迷你目錄」）。強調界線：**搜尋是「單檔內」定位；跨檔全文搜尋是 Part 2 的 telescope/ripgrep。** 這章的每個輸出都在真的 `lvm.c` 上 headless 跑過。

> **環境**：Neovim v0.12.4，WSL2。範例用 Lua 的 `lvm.c`（1978 行）。

## 為什麼需要這個？

打開一個陌生大檔，你腦中的問題往往是「**這個檔裡哪裡碰到 X**」：哪裡呼叫 `luaG_runerror`（拋錯）、哪裡 dispatch `OP_ADD`、`vmcase` 出現幾次（有幾個 opcode）。這些問題的答案不在螢幕上，你得**搜**。

Ch 5 的 jumplist 讓你在**已知的內容間**追路徑；搜尋讓你**定位還沒看到的東西**。兩者是讀碼導航的一體兩面：先搜到目標（`/`），跳過去，再用 jumplist 追下去（`Ctrl-O` 隨時退回）。搜尋本身也進 jumplist——所以你搜一個字跳過去，`Ctrl-O` 能回到搜之前的位置。

Vim 的搜尋比一般編輯器的 Ctrl-F 強的地方：它是 **motion**（能配 operator，`d/foo` 刪到下一個 foo）、能用 **regex**、`*` 能**零打字**搜游標下的字、`:g` 能把「所有配對」變成一份**清單**。這章教你把搜尋當導航工具用，不只是「找字」。

## 先建立直覺：搜尋是 motion，配對是資料

```
    /pattern<CR>   →  游標跳到「下一個」配對，開始高亮所有配對
         n         →  下一個配對（同方向）
         N         →  上一個配對（反方向）
         *         →  「搜游標下的字」的下一個（零打字）

    ┌──────────────────────────────────────────────┐
    │ 單一定位：/ ? * #  →  跳到某個配對，一次看一個 │
    │ 全部列清單：:g/pat/  →  把所有配對行印成清單    │
    └──────────────────────────────────────────────┘
        前者是「找到就跳過去」，後者是「給我全貌」
```

心智模型：`/` 是「帶我去下一個」，`:g` 是「把全部攤給我看」。讀碼時你兩種都要——追一個具體出現用 `/`+`n`；想掌握「這個 pattern 在檔內的分布」用 `:g`。

## 核心一：/ 與 ? 前後搜、n 與 N 巡

- `/pattern<CR>`：往**下**搜，跳到下一個配對
- `?pattern<CR>`：往**上**搜
- `n`：跳到**下一個**配對（沿原搜尋方向）
- `N`：跳到**上一個**配對（反方向）
- `<CR>`（在搜尋列直接按）：重複上次搜尋

實戰：在 `lvm.c` 想找主迴圈，`/luaV_execute<CR>`——跳到第一個出現，`n` 跳下一個，`N` 回上一個。`incsearch`（Ch 0 沒設，這章加）會在你**還在打字時**就即時高亮、預覽跳到哪，打錯立刻看得出來。

搜尋是 motion，可配 operator：`y/return<CR>` 複製「從游標到下一個 return」之間的內容、`d/;<CR>` 刪到下一個分號。讀碼少用 `d`，但 `y/…` 抄一段去筆記偶爾好用。

**搜尋歷史**：`/` 後按 `↑`（或 `Ctrl-p`）叫出之前搜過的 pattern，不用重打。`q/` 開啟搜尋歷史的可編輯視窗（進階，能改舊 pattern 再送出）。

## 核心二：* 與 #（搜游標下的字，零打字）

Ch 5 提過，這裡放進搜尋脈絡完整講：

- `*`：把游標下的 word 當 pattern，搜**下一個**出現（自動加 `\<...\>` 整字邊界）
- `#`：搜**上一個**
- `g*` / `g#`：同上，但**不要求整字**（`foo` 也配到 `foobar`）

`*` 是讀碼「追同名符號」最快的動作——不用打字。游標放在 `luaV_flttointeger` 上按 `*`，跳到它下一個出現，`n` 繼續巡。搭配 `hlsearch`（下面），檔內所有 `luaV_flttointeger` 同時亮起來，你一眼看到它「散佈在哪」。這是「這個函式在本檔哪些地方被用到」的最快答案（單檔內；跨檔要 Part 2 的 ripgrep）。

`*` 加 `\<\>` 整字邊界的細節很重要：搜 `n` 這個變數，`*` 只配獨立的 `n`，不會配到 `init`、`nvalue` 裡的 n——這正是你要的。想放寬用 `g*`。

## 核心三：very magic mode（\v）——讓 regex 正常一點

Vim 的預設 regex（"magic" 模式）很煩：`(` `)` `+` `{` `|` 這些在正常 regex 裡有特殊意義的字元，在 Vim 裡**要跳脫**才有意義（`\(` `\+` `\|`）。這跟你在別處學的 PCRE/grep -E 相反，很容易寫錯。

`\v`（**v**ery magic）開頭，讓 regex **接近正常**：`(` `)` `+` `?` `{` `|` 直接就是特殊字元，不用跳脫。

```
    找函式定義開頭（int/void/static 開頭 + 有左括號）：

    magic（預設）：  /^\(int\|void\|static\).*(
    very magic：     /\v^(int|void|static).*\(
                      ↑ 跟你熟的 regex 一樣，只有 ( 當「字面括號」時要跳脫
```

**建議：讀碼寫稍複雜的 pattern，一律 `\v` 開頭**，省得數哪個要跳脫。單純找一個字串（`/luaV_execute`）不用 `\v`，沒有特殊字元。

## 核心四：:g/pattern/ ——本檔的迷你目錄（讀碼最有用）

`:global`（簡寫 `:g`）對**所有配對的行**做一件事。最有用的兩個讀碼用法：

- `:g/pattern/p`：**印出**所有配對行（p = print）
- `:g/pattern/#`：印出配對行**加行號**（# = 帶行號）

這等於「**這個檔裡所有碰到 X 的地方，一次列給我看**」。以下是真在 `lvm.c` 上 headless 跑出來的：

`:g/luaG_runerror/p`（列出所有拋 runtime error 的地方——這檔在哪些情況會報錯）：

```
      luaG_runerror(L, "'for' step is zero");
  luaG_runerror(L, "'__index' chain too long; possible loop");
  luaG_runerror(L, "'__newindex' chain too long; possible loop");
          luaG_runerror(L, "string length overflow");
      luaG_runerror(L, "attempt to divide by zero");
      luaG_runerror(L, "attempt to perform 'n%%0'");
```

一眼看完 VM 會拋哪些錯——這是理解一個模組「異常邊界」的捷徑。

用 very magic 列出**所有函式定義**，等於生一份本檔目錄，`:g/\v^(int|void|static|lu_byte|l_sinline).*\(/#`（真跑輸出，含行號）：

```
  91 static int l_strton (const TValue *obj, TValue *result) {
 108 int luaV_tonumber_ (const TValue *obj, lua_Number *n) {
 126 int luaV_flttointeger (lua_Number n, lua_Integer *p, F2Imod mode) {
 142 int luaV_tointegerns (const TValue *obj, lua_Integer *p, F2Imod mode) {
 157 int luaV_tointeger (const TValue *obj, lua_Integer *p, F2Imod mode) {
 214 static int forprep (lua_State *L, StkId ra) {
 273 static int floatforloop (lua_State *L, StkId ra) {
 291 lu_byte luaV_finishget (lua_State *L, const TValue *t, TValue *key,
 334 void luaV_finishset (lua_State *L, const TValue *t, TValue *key,
```

**這一行指令，把一個 1978 行的陌生檔壓成一張函式地圖。** 你拿到行號，`157G` 直接飛過去。這是 `reading_code` 偵察階段「先建地形圖」在單檔層級的具體手法。（互動時 `:g` 的輸出會進一個訊息視窗，按行號跳；也能 `:g/pat/t$` 把配對行抄到檔尾當清單，或配 quickfix，Part 2 Ch 12 詳談。）

計配對數量：`:%s/vmcase//gn`（`n` = 只算不改）。真跑：

```
86 matches on 86 lines
```

`vmcase` 出現 86 次——這 VM 有 86 個 opcode 分支。**數量本身就是資訊**：一行指令就知道這個 dispatch 表多大。

## 核心五：設定——incsearch / hlsearch / noh / offset

往 Ch 0 的 config 加這幾行（搜尋體驗的關鍵）：

```lua
vim.opt.incsearch = true    -- 邊打邊即時高亮 + 預覽跳到哪
vim.opt.hlsearch = true     -- 高亮「所有」配對，看清分布
-- ignorecase + smartcase 在 Ch 0 已設：全小寫搜忽略大小寫，有大寫才精確
```

- `incsearch`：打字時即時預覽，`hlsearch`：所有配對持續高亮（讀碼看「散佈在哪」很有用）。
- `:noh`（`:nohlsearch`）：**關掉高亮**（搜完一堆黃底很煩，這個清掉）。常綁一個快捷：`vim.keymap.set("n", "<Esc>", "<cmd>noh<cr>")`。
- **search offset**：`/pattern/e` 讓游標落在配對的**結尾**而非開頭；`/pattern/+2` 落在配對行**下方第 2 行**。讀碼場景：`/luaV_execute/+1` 跳到函式定義的下一行（直接進函式體）。

這幾行加進 config 後，headless 驗證它有生效（`incsearch`/`hlsearch` 是 boolean 選項，載入後可 print）：

```lua
-- check.lua 片段
print("incsearch=" .. tostring(vim.o.incsearch) .. " hlsearch=" .. tostring(vim.o.hlsearch))
```

> **互動高亮 headless 截不了圖**：`incsearch`/`hlsearch` 的黃底高亮是即時 UI，終端截不到。上面 print 驗證選項有開；高亮行為是 Vim 核心，`:help hlsearch` 有定義。而 `:g` 的**輸出**能 headless 貼（如上），所以那些是真跑結果。

## 鍵位表

| 模式 | 按鍵 | 作用 |
|---|---|---|
| n | `/pat<CR>` / `?pat<CR>` | 往下 / 往上搜 |
| n | `n` / `N` | 下一個 / 上一個配對 |
| n | `*` / `#` | 搜游標下的字（整字），下 / 上一個 |
| n | `g*` / `g#` | 同上但不要求整字 |
| c | `\v` | very magic：regex 特殊字元不用跳脫 |
| n | `/` 後按 `↑` / `Ctrl-p` | 叫出搜尋歷史 |
| c(ex) | `:g/pat/p` | 印出所有配對行 |
| c(ex) | `:g/pat/#` | 印出所有配對行 + 行號（本檔目錄） |
| c(ex) | `:g/pat/t$` | 把配對行抄到檔尾（做清單） |
| c(ex) | `:%s/pat//gn` | 計配對數量（不改動內容） |
| c(ex) | `:noh` | 清掉搜尋高亮 |
| n | `/pat/e` 、 `/pat/+N` | search offset：落在結尾 / 下方 N 行 |

## 對比與取捨

| 你想做的事 | 工具 | 為什麼 |
|---|---|---|
| 追游標下符號在**本檔**的出現 | `*` `n` | 零打字，整字匹配 |
| 找一個具體字串跳過去 | `/pat<CR>` | 最直接 |
| 「本檔所有碰到 X 的地方」清單 | `:g/pat/#` | 一次看全貌 + 行號 |
| 生一份本檔函式目錄 | `:g/\v^(int\|void\|static).*\(/#` | 壓成地圖 |
| 數某 pattern 出現幾次 | `:%s/pat//gn` | 數量即資訊 |
| **跨檔**全文搜 | **Part 2 ripgrep / telescope** | `/` 只管單檔 |
| 語意級「所有 reference」 | **Part 4 LSP `gr`** | 懂型別、跨檔、不誤配字串 |

**界線劃清楚**：`/` `*` `:g` 全是**單檔**工具。「這個函式在整個 repo 哪裡被呼叫」超出它們的範圍——那要 `:Telescope live_grep`（Part 2 Ch 9/10）或 LSP 的 `gr`（Part 4 Ch 19）。搞混會讓你在單檔裡 `*` 半天找不到，其實它定義在別的檔。

## 踩雷集錦

1. **在 Vim 寫 regex 忘了要跳脫 `(` `+` `|`**，配不到還以為 pattern 錯。預設 magic 模式下 `(` 是字面括號、`\(` 才是群組——跟 grep -E 相反。**複雜 pattern 一律 `\v` 開頭**，回歸正常 regex 直覺。
2. **搜完一堆黃底高亮清不掉**，很干擾。`:noh` 清掉。強烈建議綁 `<Esc>` → `:noh`，按 Esc 順手就清。
3. **`*` 搜不到你以為該有的出現**。`*` 是**整字**匹配（自動加 `\<\>`）。搜 `n` 不會配到 `init` 裡的 n——多數時候這是好事，但你若真要子字串匹配，用 `g*`。
4. **用 `/` 找跨檔的東西找不到**。`/` 只搜當前 buffer。「這函式定義在哪個檔」是跨檔問題，`/` 幫不了——換 telescope/ripgrep（Part 2）或 LSP `gd`（Part 4）。這是新手最常見的工具誤用。
5. **`:g` 的 pattern 沒跳脫特殊字元**，如 `:g/a.b/` 裡 `.` 配任意字元不只配 `.`。要配字面點用 `:g/a\.b/` 或 `\v` 下的 `a\.b`。`:g` 用的是跟 `/` 同一套 regex。
6. **`smartcase` 的意外**：搜 `Foo`（有大寫）時**強制大小寫敏感**，配不到 `foo`。這是 `smartcase` 設計（Ch 0 開的）——想強制忽略大小寫，pattern 裡加 `\c`（`/\cfoo`）。

## 進階：再往深一層

- **`:g` 的完整威力是 `:g/pat/cmd`**：對每個配對行執行任意 ex 命令。`:g/luaG_runerror/t$` 把所有拋錯行抄到檔尾做筆記；`:g/^$/d` 刪所有空行；`:v/pat/d`（`:v` = `:g!`）刪**不**配對的行（只留你要的）。讀碼時 `:g/pat/t$` 收集「所有相關行」到一處是很強的外化手法。
- **`gn` text object**：`gn` 是「下一個搜尋配對」當 text object。`/foo<CR>` 後 `cgn` 改下一個 foo、`.` 重複——批次改同名的招式。讀碼少用（不改東西），但知道搜尋能變 text object 有助理解 Vim 的正交性。
- **搜尋 × quickfix**：`:g` 的清單能導進 quickfix list（Part 2 Ch 12），變成可 `:cnext`/`:cprev` 逐項跳的**工作清單**。「本檔所有 TODO」「所有 error 拋出點」變成一個能逐項巡的待辦——這是 `:g` 從「印清單」升級到「可導航清單」的關鍵，Ch 12 詳談。
- **`:help pattern.txt`** 是 Vim regex 的完整規格，含 magic 等級（`\v \m \M \V`）、`\<\>` 邊界、`\zs \ze`（設定配對的實際起訖，精準抽取用）。讀碼寫進階 pattern 時查它。

## 本章重點整理

- 搜尋是**單檔內**的導航：`/` `?` 搜、`n` `N` 巡、`*` `#` 零打字搜游標下的字（整字，`g*` 放寬）。
- **very magic（`\v`）讓 Vim regex 回歸正常直覺**——複雜 pattern 一律 `\v` 開頭，省得數跳脫。
- **`:g/pat/#` 是讀碼神器**：一行把陌生大檔壓成「所有配對行 + 行號」的目錄；`:%s/pat//gn` 數出現次數。兩者本章都在真 `lvm.c` 上 headless 跑過。
- 設定 `incsearch`（即時預覽）+ `hlsearch`（全高亮）+ `:noh`（清高亮，綁 Esc）。
- **界線**：`/`/`*`/`:g` 都只管單檔；跨檔全文搜是 Part 2（ripgrep/telescope），語意級 reference 是 Part 4（LSP `gr`）。

## 自我檢核

- [ ] 我知道 `*` 為什麼是整字匹配，什麼時候該改用 `g*`
- [ ] 我能寫一個 `\v` 開頭的 pattern 找「int 或 void 開頭的函式定義行」，並用 `:g/…/#` 列出來
- [ ] 我能一行指令數出 `vmcase` 在檔裡出現幾次
- [ ] 我知道 `/` 只搜單檔，跨檔要換什麼工具（ripgrep/telescope 或 LSP gr）
- [ ] 我知道搜完高亮怎麼清（`:noh`），以及為什麼建議綁到 Esc

## 延伸閱讀

- **Neovim `:help pattern.txt`**
  - **讀哪裡**：`/magic`（magic 等級 `\v \m \M \V`）、`/\<` `/\>`（整字邊界）、`search-offset`；這是本章 regex 與 offset 的權威來源
  - **重點**：先搞懂 `\v` 讓哪些字元不用跳脫，讀碼寫 pattern 最實用
- **Neovim `:help :global` / `:help :g`**
  - **讀哪裡**：`:g/pat/cmd` 的語意、`:v`（反向）、常見 `cmd`（`p` `#` `d` `t` `normal`）；本章的 `:g` 神技全出於此
- **`soft_skills/reading_code` Ch 12「grep/ripgrep 的藝術」**
  - **讀哪裡**：整章；本章是 grep 藝術在「單檔 + Vim 內」的版本，Ch 12 講的是跨檔 + CLI 版，兩者互補——本課 Part 2 會把 ripgrep 整合進 nvim

搜尋讓你在單一檔案裡定位。但讀大專案時你常要**同時開好幾個檔對照**——左邊看 caller、右邊看 callee，或一個檔看 `.c`、旁邊看 `.h`。這需要搞懂 buffer / window / tab 三者的區別，是下一章。

→ [Ch 7 buffer / window / tab：多檔對照讀](./07-buffers-windows-tabs.md)
