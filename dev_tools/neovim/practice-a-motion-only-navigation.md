# 練習 A — 純 motion 限時導航

> **目標**：把 Part 1（Ch 3–8）的 motion 練成反射。規則殘酷但有效——**禁用滑鼠、禁用方向鍵**，只准用 motion 在一個真實的大型 C 檔裡完成一串定位任務並計時。讀碼是速度技能，逼自己在時限內用最短的 motion 到達，你才會把「用意圖移動」內化成肌肉，而不是「一格一格爬」。

> **環境**：Neovim v0.12.4，WSL2。靶檔：Lua 直譯器的虛擬機主檔 **`lvm.c`**（1978 行）——一份你八成沒讀過、夠大、結構真實的 C 檔。本練習所有「答案位置」都在真檔上 headless 跑過驗證（`%` 找配對括號、`:g` 列函式、行號都是真的）。

## 準備靶檔

```bash
cd /tmp
git clone --depth 1 https://github.com/lua/lua
cd lua
wc -l lvm.c          # 應為 1978
nvim lvm.c
```

用你 Ch 0 起就在養的讀碼 config 開它（`nvim lvm.c`）。這份檔的地形（真跑 `:g/\v^\w.*\(.*\{$/#` 驗過，共 29 個函式定義），你會反覆用到的幾個地標：

| 函式 | 起始行 | 備註 |
|---|---|---|
| `luaV_tonumber_` | 108 | 有 if/else if/else 巢狀 |
| `luaV_flttointeger` | 126 | tointeger 鏈的底層 |
| `luaV_tointegerns` | 142 | 呼叫 flttointeger |
| `luaV_tointeger` | 157 | 呼叫 tointegerns |
| `forprep` | 214 | 深巢狀 if，close brace 在 265 |
| `luaV_lessthan` | 555 | 短、乾淨 |
| `luaV_execute` | 1204 | **主迴圈**，close brace 在 1976，內含 86 個 `vmcase` |

## 鐵律

1. **禁滑鼠**。真的別碰。（可以 `:set mouse=` 關掉逼自己。）
2. **禁方向鍵 ↑↓←→**。用 `h j k l` 微調、用 motion 大跳。
3. **禁 `10j` 連按湊數**——每個任務想「最短 motion」，不是「按很多次小 motion」。
4. **計時**。每個任務段給自己一個目標秒數，計時器放旁邊。
5. 全程待在 **normal mode**。這是導航練習，不打字（除了搜尋 `/` 和 ex 指令 `:`）。

---

## 任務清單

### 段 1：檔案地形（目標 60 秒）

從打開檔案的初始位置（第 1 行）開始：

1. 跳到**檔案最後一行**，看檔尾是什麼。
2. 跳回**檔案第一行**，看 copyright 與 `#include`。
3. 游標放到某個 `#include "..."` 的檔名上，**跳進那個 header**，看一眼，再**退回 `lvm.c`**。
4. **一行指令**列出這個檔所有函式定義 + 行號（生一份目錄）。

### 段 2：精準跳到一個函式（目標 45 秒）

1. 用**一個** motion 跳到第 **1204** 行（`luaV_execute` 主迴圈）。
2. 把這行**捲到螢幕中央**，讓你看得到函式簽名上面的註解。
3. 找到函式開頭的 `{`，用**一個鍵**跳到它的**配對 `}`**——記下 `}` 在第幾行（這告訴你主迴圈多長）。
4. 用 `` ` `` 系 motion **跳回**你剛才跳走前的位置。

### 段 3：追一條 tointeger 鏈（目標 60 秒）

在 `lvm.c` 裡有一條呼叫鏈：`luaV_tointeger`（157）→ 呼叫 `luaV_tointegerns`（142）→ 呼叫 `luaV_flttointeger`（126）。

1. 跳到第 157 行的 `luaV_tointeger`。
2. 游標移到它函式體裡呼叫的 `luaV_tointegerns` 上，**用內建 `gd` 跳到定義**（142 行）。
3. 再從那裡，游標移到它呼叫的 `luaV_flttointeger` 上，`gd` 跳到定義（126 行）。
4. 現在**原路退回**：一路退回 142，再退回 157——**不准打行號、不准搜尋、不准捲動**。

### 段 4：在一個函式內選中結構（目標 45 秒）

到 `forprep`（214 行）：

1. 跳到第 214 行。
2. 這函式有個深巢狀的 `if (ttisinteger(pinit) && ttisinteger(pstep)) { ... }`。游標放進**最外層**函式體，用**一個** text object 指令**選中整個函式體 block**（看它涵蓋幾行）。
3. 找到函式簽名那行的**第三個逗號**，用行內 motion 一步步到達（不用 `l`）。
4. 用 `%` 從 `forprep` 開頭的 `{` 跳到它的配對 `}`（真檔在 265 行）。

### 段 5：搜尋 + 標記 + 來回（目標 60 秒）

1. `OP_ADD` 這個 opcode 的 handler 在哪？用**搜尋**跳過去（真檔在 1512 行）。
2. 在那裡**設一個 mark**（如 `ma`）。
3. 跳到 `luaV_execute` 開頭（1204），**再設一個 mark**（如 `mb`）。
4. 現在用 mark 在「迴圈入口（`b`）」和「OP_ADD handler（`a`）」之間**來回跳三次**。
5. **數出** `vmcase` 在這檔出現幾次（一行 ex 指令，答案是 opcode 總數）。

---

## 如果你卡住了

只給方向，不給答案——先自己試。

1. **段 1 跳檔頭尾**：想「檔案第一行」「檔案最後一行」的 motion 是哪兩個大寫/小寫組合（Ch 3）。「跳進游標下的檔名」是一個兩字母 `g?`（Ch 5，讀 `#include` 那個）。「退回上一個檔」——它進了 jumplist，用哪個 `Ctrl-?`（Ch 5）。
2. **段 1 列函式目錄**：`:g/pattern/#` 印配對行 + 行號（Ch 6）。pattern 要配「行首是型別字、行尾是 `{`」的函式定義行——用 `\v` 讓 regex 正常（Ch 6）。
3. **段 2 捲到中央 + 找配對**：跳完接哪個兩字母 `z?`（Ch 8）把行置中。配對括號是**一個鍵**（Ch 4/5），游標得先在括號上（先 `f{` 落上去）。「跳回跳走前」是兩個反引號（Ch 5/8）。
4. **段 3 追鏈退回**：進去用 `gd`（Ch 5 內建版）。退回**不准**用行號/搜尋——那就只剩 jumplist 的 `Ctrl-?`（Ch 5）。這題就是逼你用 jumplist 而非硬記行號。
5. **段 4 選 block + 第三個逗號**：選整個 block 是 `v` + inner + 括號（Ch 4）。第三個逗號：`f,` 到第一個，`;` 重複往同方向（Ch 3），按幾次？

---

## 參考解答（逐鍵示範）

**先自己做完再看。** 偷看你就練不到肌肉。

<details>
<summary>點開逐鍵解答</summary>

### 段 1

```
G           跳到檔案最後一行
gg          跳回第一行
```
跳進 header（游標在某個 `#include "lstate.h"` 的 `lstate.h` 上）：
```
/lstate.h<CR>   先搜到含 include 的行（或直接把游標移到檔名上）
gf              跳進 lstate.h（go to file）
Ctrl-O          退回 lvm.c（jumplist 往回）
```
> `gf` 需要 `path` 找得到 header。同目錄的 `.h`（Lua 的 header 都在 `lvm.c` 旁）預設就找得到；找不到先 `:set path+=.`。

列函式目錄（**真檔 headless 跑過**）：
```
:g/\v^\w.*\(.*\{$/#<CR>
```
真跑輸出（節錄，含行號）：
```
 108 int luaV_tonumber_ (const TValue *obj, lua_Number *n) {
 126 int luaV_flttointeger (lua_Number n, lua_Integer *p, F2Imod mode) {
 142 int luaV_tointegerns (const TValue *obj, lua_Integer *p, F2Imod mode) {
 157 int luaV_tointeger (const TValue *obj, lua_Integer *p, F2Imod mode) {
 214 static int forprep (lua_State *L, StkId ra) {
 555 int luaV_lessthan (lua_State *L, const TValue *l, const TValue *r) {
1204 void luaV_execute (lua_State *L, CallInfo *ci) {
```
一行把 1978 行的檔壓成函式地圖。

### 段 2

```
1204G       一個 motion 跳到第 1204 行（luaV_execute）
zz          當前行捲到螢幕中央
f{          行內跳到函式開頭那個 {（先落到括號上）
%           跳到配對的 }
```
`%` 落點——**真檔 headless 驗證**（`+1204 +normal f{% +lua print(line("."))`）：第 **1976** 行。所以主迴圈跨 1204→1976，七百多行。
```
``          兩個反引號，跳回跳走前的位置
```

### 段 3

```
157G                        跳到 luaV_tointeger
```
游標移到函式體裡 `luaV_tointegerns` 上（可 `/luaV_tointegerns<CR>` 或 `f` + `w` 挪過去），然後：
```
gd                          內建跳定義 → 142（luaV_tointegerns）
```
游標移到 `luaV_flttointeger` 上：
```
gd                          → 126（luaV_flttointeger）
```
原路退回（**只准 jumplist**）：
```
Ctrl-O                      退回 142
Ctrl-O                      退回 157（起點）
```
> 這題的靈魂：退回**不靠記憶行號**。`gd` 每次都把跳前位置推進 jumplist，`Ctrl-O` 沿麵包屑回家。真檔的鏈：157 的 `luaV_tointeger` `return luaV_tointegerns(...)`；142 的 `luaV_tointegerns` `return luaV_flttointeger(...)`——這條鏈是真的（`grep -n luaV_flttointeger lvm.c` 可驗）。

### 段 4

```
214G        跳到 forprep
```
選整個函式體 block（游標在函式體內任一行）：
```
vi{         visual 選中最外層 { } 內容
```
> 若游標在內層 if 裡，`vi{` 會選內層；`Esc` 後把游標移到最外層（或 `vi{` 後在 visual 裡再按 `i{` 往外擴一層，Ch 4）。

第三個逗號（在簽名行 `static int forprep (lua_State *L, StkId ra) {`——這行只有一個逗號；到有三個逗號的簽名如 `forlimit`（181 行）練更明顯）。以行內 motion：
```
f,          到第一個逗號
;           下一個（第二個）
;           下一個（第三個）
```
配對括號：
```
214G f{ %   從 forprep 的 { 跳到 }
```
`%` 落點——**真檔 headless 驗證**：第 **265** 行。

### 段 5

```
/OP_ADD<CR>     搜到 OP_ADD handler
```
真檔位置（`grep -n "vmcase(OP_ADD)" lvm.c`）：第 **1512** 行 `vmcase(OP_ADD) {`。
```
ma              在 OP_ADD 設 mark a
1204G           跳到 luaV_execute 入口
mb              設 mark b
`a              跳到 OP_ADD
`b              跳回入口
`a `b `a        來回跳
```
數 `vmcase` 次數（**真檔 headless 跑過**）：
```
:%s/vmcase//gn<CR>
```
真跑輸出：
```
86 matches on 86 lines
```
86 個 `vmcase` = 這個 VM 有 86 個 opcode 分支。（`grep -c vmcase lvm.c` 也是 86，交叉驗證。）

</details>

## 驗證方式

你可以用 headless 驗證你算出的「答案位置」對不對（這也示範這門課怎麼驗證位置類問題）：

```bash
NV=/opt/nvim-linux-x86_64/bin/nvim

# 驗證 luaV_execute 的配對括號落在 1976
$NV --headless "+1204" "+normal f{%" "+lua print('close brace at ' .. vim.fn.line('.'))" +qa lvm.c
# → close brace at 1976

# 驗證 forprep 的配對括號落在 265
$NV --headless "+214" "+normal f{%" "+lua print('close brace at ' .. vim.fn.line('.'))" +qa lvm.c
# → close brace at 265

# 驗證 vmcase 數量
$NV --headless "+%s/vmcase//gn" +qa lvm.c   # → 86 matches on 86 lines

# 驗證函式目錄
$NV --headless "+g/\v^\w.*\(.*\{$/#" +qa lvm.c   # → 印出 29 個函式 + 行號
```

以上全在真 `lvm.c` 上跑過，輸出如參考解答所示。你的 nvim 應得到一樣的數字——不一樣就是你抓錯 Lua 版本，重新 `git clone` 官方 repo。

## 延伸挑戰

1. **全程計時三輪**，記錄總秒數。第三輪應該比第一輪快一倍以上——motion 正在變成反射的證據。
2. **關掉 relativenumber 再做一次**（`:set norelativenumber`）。你會發現算「往下幾行」變難——這證明 Ch 0 開 relativenumber 是有理由的，然後把它開回來。
3. **用 folds 看骨架**：`:set foldmethod=indent` 後 `zM` 全折，看 `luaV_execute` 的 86 個 case 折成一列列，鎖定 `OP_FORLOOP` 那行 `za` 展開。體會「先看大結構再展開」（Ch 8）。
4. **挑一個更難的靶**：clone nginx（`git clone https://github.com/nginx/nginx`），開 `src/core/ngx_string.c`（更長、更多巨集），重做段 1–2。不同專案的風格會考驗你的 motion 通用性。
5. **只用 `%` 和 `[{`/`]}` 在 `luaV_execute` 裡定位**：進到某個 `vmcase` 深處，用 `[{` 一層層往外跳，數你在第幾層 block。體會結構跳（Ch 5）在深巢狀裡「我現在在哪」的用途。

## 自我檢核

- [ ] 我全程沒碰滑鼠、沒按方向鍵完成所有段落
- [ ] 我能不假思索地：跳到第 N 行、跳配對括號、選中一個 block、追一條鏈再用 `Ctrl-O` 退回
- [ ] 段 3 我是靠 jumplist（`Ctrl-O`）退回的，不是靠記行號——我理解為什麼這才是對的做法
- [ ] 我能一行 ex 指令列出檔案的函式目錄、數出某 pattern 的出現次數
- [ ] 我的三輪計時有明顯進步，motion 開始變成反射而非思考

做完這個練習，你在大 C 檔裡的移動已經是反射級——這是後面每個 Part 的地基。接下來（Part 2）我們裝上 telescope + ripgrep，把「定位」從**單檔內**擴展到**整個 repo**：不再是「這個檔的 OP_ADD 在哪」，而是「這整個專案裡所有實作 opcode 的地方在哪」。

→ [Ch 9 telescope 核心](./09-telescope-core.md)
