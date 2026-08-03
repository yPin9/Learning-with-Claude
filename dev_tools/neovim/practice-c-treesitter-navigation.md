# 練習 C — treesitter 導航複雜檔

> **目標**：把 Ch 13–16 的 treesitter 操作在一個**真實的大 C 檔**上練成反射。你會 clone Lua 直譯器原始碼，在一個兩千行的複雜檔裡：用 textobject（`vaf`/`yif`）量與抄函式、用 `]f`/`[f` 在函式間掃、用 incremental selection（`<CR>`）逐層選中巢狀結構、用 treesitter-context 在深處定位、`zM` 折疊鳥瞰——最後用一個 treesitter query 對這個檔提結構問題（「有幾個函式」「哪些呼叫了錯誤處理函式」），得到 grep 給不了的乾淨答案。

> **環境**：Neovim v0.12.4，WSL2 / Ubuntu，用 Ch 0 + Part 3 的 config（treesitter 釘 master、含 textobjects/context/fold、incremental_selection）。靶子：Lua 5.4 原始碼。本練習的 query 輸出全部 headless 真跑，貼在參考解答。

## 為什麼練這個

前四章你在玩具檔 `foo.c`（20 行、3 個函式）上看操作。真實讀碼的痛只在**大檔**才出現：函式多到 `}` 跳不準、巢狀深到忘了在哪個 scope、想盤點結構 grep 給你一堆假陽性。這練習把你丟進 Lua 的 `lvm.c`（虛擬機執行迴圈，約兩千行、三十幾個函式、巨集密集）——一個貨真價實會讓你迷路的檔。treesitter 的四類操作正是為這種檔而生，你要在它上面把操作練到不用想。

## 準備：clone 靶子

```bash
cd /tmp
git clone --depth 1 https://github.com/lua/lua.git
cd lua
wc -l lvm.c lparser.c lapi.c
```

`lvm.c`（Lua 虛擬機）是主戰場——它有一個巨大的 `luaV_execute` 函式（整個位元組碼 dispatch 迴圈，幾百行、巨集地獄），是「大到會迷路」的完美樣本。用你的 Part 3 config 打開它：

```bash
nvim lvm.c
```

先確認 treesitter 活著：`:InspectTree` 看得到樹（看不到＝parser 沒裝，`:TSInstall c`）。

## 熱身：先在 `lvm.c` 上摸一遍樹

正式計時前，花兩分鐘讓手熟悉這個檔的形狀，別一上來就急。

1. `:InspectTree` 開樹視窗，游標在原始碼上下移，看右邊哪個節點被高亮。移到一個函式簽名上——你會看到 `function_definition`，展開它看到 `declarator: function_declarator`，裡面才是函式名的 `identifier`。**這條路徑就是任務 4 你要寫進 query 的東西**，先用眼睛看清楚。
2. 關掉樹視窗，`zM` 全折。整個 `lvm.c` 收成一疊「函式標題」——這是 treesitter fold 的鳥瞰。用 `zj`/`zk` 在 fold 間跳，看這個檔的骨架。`zR` 全開回來。
3. `:Inspect` 停在一個 `if`、一個 `for`、一個 `#define` 上，各看一次它是什麼節點——建立「原始碼的每個部分在樹上叫什麼」的直覺。

熱身的目的：任務 4 的 query 不是憑空寫，是照著你**現在看到的樹**寫。這步省下的是等一下對著空結果 debug 的時間。

## 任務

### 任務 1：函式間快速掃（textobject move）— 目標 90 秒

`gg` 到檔頭，用 `]f` 一路跳過每個函式開頭，數出這個檔**大概**有幾個函式（跳到底、或跳個十幾次感受節奏）。過程中留意：`]f` 落點永遠是函式簽名那行，不會像 `}` 被函式內的空行騙。跳過頭了用 `[f` 回上一個、或 `Ctrl-o`（因為 config 設了 `set_jumps=true`）。

> 若你的機器上 `]f` 報 `tsrange.lua` 錯（Ch 14 講的 master 在 0.12 的坑），這是 master 老舊、不是你的錯。改用 Ch 14 的三條繞法之一（同期 Neovim / main / mini.ai），或本任務改用 `}`+`[[`/`]]`（C 的內建函式間跳）暫代——但 query 任務（任務 4）不受影響，照做。

### 任務 2：量一個大函式並抄它的簽名（textobject select）

找到 `luaV_execute`（`/luaV_execute` 搜尋，或 `]f` 跳到它）。游標停在它任一行，`vaf`——整個函式被 visual 選中，狀態列告訴你選了幾百行。這就是傳說中的巨型 dispatch 函式。按 `<Esc>` 取消選取。然後 `yif` 抄函式主體、或只想要簽名就在簽名行 `y$`。感受 `vaf` 對「上帝函式」的一鍵框選——這是讀碼第一步「量它多大」。

### 任務 3：巢狀深處的定位與逐層選取（context + incremental selection）

跳進 `luaV_execute` 內部某個 `case`（位元組碼處理，如搜尋 `case OP_ADD`）。此時：

- **看頂端**：treesitter-context 應該在視窗頂端釘著 `luaV_execute(...)` 的簽名（和可能的 `for`/`switch`），告訴你「你在這個函式的這個 switch 裡」。捲上捲下，頂端跟著更新。
- **逐層選取**：游標停在某個運算式的一個 identifier 上，連按 `<CR>`——選取從那個 token 一路擴大（identifier → 運算式 → statement → case 區塊 → switch → 函式）。每按一次確認選中的是一個完整結構。`<BS>` 縮回去。

### 任務 4：對這個檔提結構問題（treesitter query）— 核心任務

這是練習的重點。用 Neovim 內建 `vim.treesitter.query` 對 `lvm.c` 跑兩個 query，得到 grep 給不了的乾淨答案：

1. **這個檔有幾個函式定義**（不含註解/字串/函式指標型別的噪音）。
2. **哪些地方呼叫了 `luaG_runerror`**（Lua 的執行期錯誤拋出函式）——也就是「這個 VM 在哪些情況會拋錯」，讀懂錯誤路徑的入口。

你可以在 nvim 內用 `:lua` 一行行跑，或寫成一個小 `.lua` 檔 headless 跑（參考解答示範 headless）。query 的形狀在 Ch 16 教過，寫不出來看「如果你卡住了」。

### 任務 5：query 打敗 grep（決定性對照）

找「`luaV_execute` 這個函式定義在哪」——一個 grep 會被註解騙、query 不會的經典。

1. 在 shell（或 nvim `:!`）跑 `rg -n 'luaV_execute' lvm.c`，數它幾個命中、看每一個是什麼。
2. 寫一個 query 只抓「名字是 `luaV_execute` 的 `function_definition`」（在任務 4 的函式定義 query 上加 `(#eq? @name "luaV_execute")`），跑，看它幾個。

grep 會命中好幾個，但其中**只有一個是真正的函式定義**，其餘全是**註解裡提到函式名**（Lua 在 `luaV_execute` 附近寫了好幾行 `/* ... 'luaV_execute' ... */` 的說明）。query 精準只給那一個定義。這就是 Ch 16 的核心論點在真檔上的現形：問結構（「哪個 `function_definition` 節點叫這名字」）vs 問文字（「哪裡出現這串字」），差在假陽性。把「grep N 個、query 1 個、多出來的 N−1 個是註解」寫進你的筆記——這個對照做過一次，你就永遠記得結構化搜尋的價值。

## 如果你卡住了

1. **`]f` 不動或報錯**：先 `:InspectTree` 確認 treesitter 有解析（沒有＝`:TSInstall c`）。報 `tsrange` 錯是 master/0.12 的坑（任務 1 的提示框），用繞法或改內建 `]]`/`[[`。
2. **`vaf` 選不到函式**：游標要在函式**內**（含簽名行）。`lookahead=true` 會往後找最近的，但若游標在函式之間的空白可能抓歪。先跳進函式再 `vaf`。
3. **看不到頂端的 sticky context**：`:TSContext enable` 手動開；確認 config 裝了 `nvim-treesitter-context`。它只在函式簽名**捲出畫面**時才顯示——在短函式或畫面容得下整個函式時看不到是正常的。
4. **query 回空**：你多半欄位名寫錯了。C 的函式名是巢狀的 `declarator: (function_declarator (declarator: (identifier)))`，不是 `name:`。**先 `:InspectTree` 看真實路徑**，或用 `:EditQuery` 邊寫邊看命中。
5. **query 只抓到當前 buffer**：`iter_captures` 對當前 buffer 的樹跑，這正是本任務要的（單檔）。想跨整個 Lua repo 掃是另一回事（用 tree-sitter CLI / ast-grep，見 `reading_code` Ch 15）。

## 分段步驟

1. clone Lua，`nvim lvm.c`，`:InspectTree` 確認 treesitter 活著。
2. `gg` → `]f` 連跳，數函式、感受落點準度（任務 1）。
3. `/luaV_execute` → `vaf` 量大小 → `<Esc>` → `yif` 抄主體（任務 2）。
4. 跳進 `luaV_execute` 內某 `case`，看頂端 context、`<CR>` 逐層選取（任務 3）。
5. 寫兩個 query（函式數、`luaG_runerror` 呼叫點），跑，看乾淨結果（任務 4）。
6. 對 `luaV_execute` 做 grep vs query 對照，數 grep 多出的假陽性（任務 5）。
7. 對照參考解答的 headless 真跑輸出，數字對得上嗎？

## 參考解答

**做完再看**。query 尤其要自己先寫過再對答案。

<details>
<summary>點開參考解答（逐鍵 + query headless 真跑輸出）</summary>

### 任務 1–3：逐鍵

```
# 任務 1：函式間掃
gg              " 到檔頭
]f ]f ]f ...    " 一路跳函式，每次落在簽名行
[f              " 回上一個
Ctrl-o          " 跳回 jumplist 前一位置（set_jumps=true 之效）

# 任務 2：量與抄
/luaV_execute<CR>   " 搜到大函式
vaf                 " 選中整個函式（狀態列顯示幾百行）
<Esc>               " 取消
yif                 " 抄函式主體

# 任務 3：定位與選取
/case OP_ADD<CR>    " 跳進 dispatch 迴圈某分支
                    " → 看視窗頂端 treesitter-context 釘著 luaV_execute 簽名
<CR><CR><CR>        " 從游標下 token 逐層擴大選取
<BS>                " 縮回一層
```

### 任務 4：query（headless 真跑）

存成 `q.lua`，用隔離 XDG 目錄跑 `nvim --headless -u <init.lua> -l q.lua`：

```lua
vim.cmd("edit /tmp/lua/lvm.c")
local root = vim.treesitter.get_parser(0, "c"):parse()[1]:root()

-- Query 1: 所有函式定義
local qf = vim.treesitter.query.parse("c", [[
(function_definition
  declarator: (function_declarator
    declarator: (identifier) @name))
]])
local n = 0
for id, node in qf:iter_captures(root, 0) do n = n + 1 end
print("lvm.c function definitions: " .. n)

-- Query 2: 呼叫 luaG_runerror 的地方
local qc = vim.treesitter.query.parse("c", [[
(call_expression
  function: (identifier) @fn
  (#eq? @fn "luaG_runerror"))
]])
for id, node in qc:iter_captures(root, 0) do
  local sr = ({node:range()})[1]
  print("  luaG_runerror call at line " .. (sr + 1))
end
vim.cmd("qa!")
```

真跑輸出（Lua 5.4，本機實測）：

```
lvm.c function definitions: 32
  luaG_runerror call at line 223
  luaG_runerror call at line 253
  luaG_runerror call at line 321
  luaG_runerror call at line 377
  luaG_runerror call at line 713
  luaG_runerror call at line 775
  luaG_runerror call at line 795
```

（第一個 query 也印了前幾個函式名確認乾淨：`l_strton`, `luaV_tonumber_`, `luaV_flttointeger`, `luaV_tointegerns`, `luaV_tointeger`——全是真函式，沒有註解/字串/型別混入。`luaG_runerror` 在 `lvm.c` 共 7 處呼叫，每一處都是一個「VM 會拋執行期錯誤」的點——`'for' step is zero`、`divide by zero`、`string length overflow` 這些。）

**讀碼意義**：Query 1 的 32 告訴你這檔的規模與「內部 helper 多」；Query 2 的 7 個呼叫點是你讀「Lua VM 在哪些情況拋錯」的直接入口——跳到那幾行，就看到觸發錯誤的具體條件（除以零、for step 為零、字串長度溢位…）。這裡剛好 grep 與 query 都是 7（`luaG_runerror` 的宣告在別的檔、`lvm.c` 內沒有註解提到它），所以看不出差距——差距在任務 5 用 `luaV_execute` 才現形。

### 任務 5：grep vs query（headless 真跑）

grep 對 `luaV_execute`：

```
$ rg -n 'luaV_execute' lvm.c
925:** Macros for arithmetic/bitwise/comparison opcodes in 'luaV_execute'
928:** interpreter loop (function luaV_execute) and may access directly
1099:** Function 'luaV_execute': main interpreter loop
1104:** some macros for common tasks in 'luaV_execute'
1204:void luaV_execute (lua_State *L, CallInfo *ci) {
```

**5 個命中，前 4 個全是註解**（Lua 在這函式附近寫了大段說明註解，反覆提到函式名），只有第 5 個（line 1204）是真正的函式定義。

query 只要函式定義：

```lua
local q = vim.treesitter.query.parse("c", [[
(function_definition
  declarator: (function_declarator
    declarator: (identifier) @name)
  (#eq? @name "luaV_execute"))
]])
```

真跑輸出：

```
function_definition luaV_execute at line 1204
```

**5 → 1**。query 精準給那唯一的定義，4 個註解假陽性自動消失。這就是 Ch 16 「問結構 vs 問文字」的決定性對照，在真檔上現形。

### 驗證你的結果對不對

你的函式數字不一定剛好 32（Lua 版本不同、`static inline` 巨集展開差異都會影響），但應該在**三十幾**這個量級，且函式名清單裡不該出現註解片段或型別名——出現了就是你 query 的節點種類寫太寬。`luaG_runerror` 呼叫點應是 7 個且行號指向真實的 `luaG_runerror(...)` 呼叫。任務 5 的 `luaV_execute`：grep 5、query 1，多出的 4 是註解。

</details>

## 驗證方式

- **任務 1**：`]f` 落點都在函式簽名行（不是空行），`Ctrl-o` 跳得回來。
- **任務 2**：`vaf` 選中的行數 = `luaV_execute` 的完整行數（狀態列數字對得上你 `:InspectTree` 看到的節點範圍）。
- **任務 3**：頂端 context 顯示 `luaV_execute`；`<CR>` 每按一次選取範圍嚴格變大且停在完整節點邊界。
- **任務 4**：query 回傳的函式名清單**乾淨**（無註解/字串/型別）；`luaG_runerror` 行號跳過去確實是呼叫。
- **任務 5**：`rg 'luaV_execute' lvm.c` 給 5（4 註解 + 1 定義），query 給 1（只定義）。5→1 的差就是結構化搜尋消掉的假陽性。

## 延伸挑戰

1. **危險/錯誤 API 審計**：寫一個帶 `#any-of?` 的 query，一次抓 `lvm.c` 裡所有呼叫 `luaG_runerror` / `luaG_typeerror` / `luaG_ordererror` 之一的地方——「這個 VM 所有拋錯路徑」一 query 盤點。
2. **static vs 對外**：用 Ch 16 的 static 函式 query 分出 `lvm.c` 哪些是 `static`（內部）、哪些對外（`LUAI_FUNC`/無 static）。內部函式佔比說明這個模組多自包含。
3. **跨檔**：Neovim 內 query 只掃當前 buffer。用 `reading_code` Ch 15 的 tree-sitter **CLI**（`tree-sitter query`）或 `ast-grep`（`sg -p 'luaG_runerror($$$)' -l c .`）對整個 Lua `src/` 掃同一個 query，比較「單檔（nvim）」與「全庫（CLI）」兩種場景。
4. **自訂 fold 骨架**：`zM` 折起 `lvm.c`，看它收成的「函式標題骨架」。試著在 config 加 `foldtext` 讓折起的每個 fold 顯示函式簽名而非 `+-- N lines`。

## 自我檢核

- [ ] 我能在一個兩千行的檔用 `]f`/`[f` 快速在函式間掃，且知道為什麼比 `}` 準
- [ ] 我能用 `vaf` 一鍵量一個大函式的大小、`yif` 抄主體
- [ ] 我讀函式深處時會看頂端的 treesitter-context 定位，不再迷路
- [ ] 我能用 `<CR>` 逐層選中巢狀結構，理解每一層是什麼節點
- [ ] 我能寫出「抓所有函式定義」「抓所有呼叫某函式」的 query，得到乾淨結果
- [ ] 我親手做過 grep vs query 的對照，看到 grep 的假陽性被 query 消掉
- [ ] 我知道 Neovim 內 query 是單檔、跨檔大掃該用 CLI/ast-grep
- [ ] 若 `]f` 撞 master/0.12 的 `tsrange` 坑，我知道那不是我的錯、有繞法

Part 3 的四章操作都在真檔上練過了——看得懂結構、依結構移動、沿樹選取定位、對樹提問。這台機器現在「懂語法」。但它還不「懂語意」：它答不出「這個 `luaG_runerror` 的定義在哪個檔」「這個符號在整個 Lua repo 有哪些 caller」。那是下一 Part 的事——clangd 這個 LSP 會補上跨檔的語意導航。

→ [Ch 17 LSP 與 clangd](./17-lsp-and-clangd.md)
