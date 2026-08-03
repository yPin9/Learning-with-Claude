# Ch 28 — 把整套串起來：一次完整讀碼流程

> **目標**：這是本課的高光。前面每個 Part 教一個工具——motion、telescope、treesitter、clangd、gtags、harpoon、session——各自都會了，但**攻堅時它們怎麼協同成一台機器**還沒示範過。本章拿一個真專案（lua），走一次**完整攻堅**，把 `reading_code` SOP 的每一步對應到「這時候按哪個 nvim 鍵、用哪個工具」。你會看到偵察用 telescope、找入口用 rg、看結構用 treesitter、追語意用 clangd、clangd 跪了退 gtags、把 caller 組進 quickfix、用 harpoon 標熱點、用 notes.md 外化——**一台機器的完整協同演出**。能 headless 驗的（rg / gtags / treesitter / symbol 查詢）全部真跑貼輸出。

> **環境**：Neovim v0.12.4，WSL2 / Ubuntu。示範專案是真 clone、真 build 的 lua（約 2 萬行 C，master 開發版）。下面的 gtags/rg/treesitter 輸出都是在此環境真跑出來的。

## 為什麼需要這個？

你可能已經覺得「工具都會了，還要示範什麼」。但會用工具和會**協同**工具是兩回事。真實攻堅時，難的不是「怎麼按 `gd`」，是**「現在這個當口，我該用哪個工具」**：

- 剛打開一個沒看過的 repo，第一步該做什麼？telescope 亂翻？還是先 rg 找 main？
- 追 call chain 追到 clangd 沒反應——是還沒 attach、缺 compile db、還是該退 gtags 了？
- 找到五個關鍵函式，怎麼組織它們，讓自己不迷失？

SOP（`reading_code` Ch 38）給了流程骨架：**界定 → 偵察 → 建圖 → 定位 → 追蹤 → 驗證**。這章把那個抽象骨架，變成一連串具體的 nvim 操作。看完你有的不是「更多工具」，是**一條把工具串起來的肌肉記憶路徑**。

## 先建立直覺：SOP 階段 ↔ nvim 工具對照

```
 SOP 階段（reading_code Ch 38）      主力 nvim 工具            後備
 ──────────────────────────────────────────────────────────────
 0 界定任務        notes.md 三欄開頭                        —
 1 偵察 recon      telescope find_files（看結構）          rg（找 main）
 2 建圖 map        <leader>ds document symbol（單檔地圖）  telescope workspace symbol
 3 定位 locate     workspace symbol / rg（從字串反推）     —
 4 追蹤 trace      gd/gr + jumplist（Ctrl-o/i）+ 側欄對照  gtags（clangd 跪時）
 5 組織/外化       quickfix（caller 清單）+ harpoon（熱點）+ notes.md
```

這張表是本章的地圖。下面我們照這個順序，在 lua 上真的走一遍。**每一步都標「此刻按什麼、為什麼是這個工具」。**

---

## 任務界定（SOP 階段 0）

打開任何檔之前，`:e notes.md`，寫三欄的頭（`reading_code` Ch 35/38）：

```markdown
# 讀碼筆記：lua 算術運算式的執行路徑  2026-08-03

## 任務
- 本次任務：搞懂 `a+b*2` 這種算術式從原始碼到 VM 執行的路徑
- 成功標準：能畫出 詞法→parser→codegen→VM 主流程，並追出 OP_ADD 落地在哪
- 不需要懂：GC 三色標記、字串 interning、coroutine

## 假設
## 問題
## 發現
```

**為什麼先做這步**：目標感是整個攻堅的定盤星。沒有它，你會在 telescope 裡亂翻半小時還不知道在找什麼。「不需要懂」那欄尤其防你 rabbit-hole 進 GC 細節。

---

## 偵察（SOP 階段 1）：這專案長什麼樣

### 用 telescope 看整體結構

`<leader>ff`（`Telescope find_files`）。跳出模糊搜尋 picker，**先不打字**——直接看它列出的檔名，感受目錄結構。

> 互動 UI（telescope picker）headless 截不了圖，以下為鍵位操作描述；底層 `Telescope find_files` 命令已在 Ch 9 headless 驗證存在。

打開 picker 你會看到 lua 的檔名一眼分層：`llex.c`（lexer）、`lparser.c`（parser）、`lcode.c`（codegen）、`lvm.c`（VM）、`lapi.c`（C API）、`lgc.c`（GC）。**檔名就是架構情報**——lua 是教科書級的 `l<模組>.c` 命名。這一眼就對應到你任務要的「詞法→parser→codegen→VM」四塊。

### 用 rg 找入口

偵察的關鍵一問：入口在哪？`<leader>fg`（live_grep）打 `int main`，或直接跑 rg。真跑：

```
$ rg -n "int main" *.c
lua.c:777:int main (int argc, char **argv) {
```

只有一個 `main`，在 `lua.c`（CLI 直譯器）。乾淨——不像 redis 有八個 main。這告訴你：`lua.c` 只是薄殼，核心 library 在那批 `l*.c`。

把偵察結果記進 notes.md 的發現欄：

```markdown
## 發現
- F1: 唯一 main 在 lua.c:777，是 CLI 薄殼；核心在 l*.c
- F2: 檔名分層清楚：llex/lparser/lcode/lvm = 詞法/parser/codegen/VM
```

**此刻用的工具**：telescope（結構印象）+ rg（找 main）。偵察階段要「廣而快」，這兩個正是廣而快的工具。

---

## 建圖（SOP 階段 2）：畫出主流程

### 用 document symbol 建單檔地圖

打開 `lvm.c`（VM 主檔），`<leader>ds`（document symbol，Ch 20）。clangd 列出這個檔所有函式/struct。lvm.c 是長檔，這張 symbol 清單就是它的地圖，讓你不用捲一萬行找函式。

用 treesitter 也能數這個檔的結構（headless 真跑驗證）：

```
$ nvim --headless -l ts_check.lua      # 用 treesitter query 數 function_definition
treesitter c parse lvm.c: true
root: translation_unit
function_definition count in lvm.c: 32
```

treesitter 確認 lvm.c 解析成功、有 32 個函式定義。這個「結構視圖」是 treesitter（Ch 13-16）給的——它不懂語意（不知道誰呼叫誰），但精準知道「這個檔的語法結構」。

### 找 VM 主迴圈

VM 的心臟是主解譯迴圈。用 `<leader>fg` grep `luaV_execute` 或 gtags 查定義：

```
$ global -x luaV_execute
luaV_execute     1204 lvm.c    void luaV_execute (lua_State *L, CallInfo *ci) {
```

主迴圈在 `lvm.c:1204`。往下看它的 dispatch：

```
$ rg -n "vmdispatch|vmcase\(OP_ADD\)" lvm.c
1199:#define vmdispatch(o)	switch(o)
1512:      vmcase(OP_ADD) {
```

`vmdispatch` 是個 `switch`，`OP_ADD` 這個 opcode 在 `lvm.c:1512`。**這就是 `a+b` 的加法最終落地的地方**——我們的目標路徑的終點找到了。

把主流程畫進 notes.md（mermaid 或 ASCII，Ch 35）：

```
 原始碼文字
   │  llex.c   (lexer)     → token 流
   ▼
 lparser.c (parser)        → 邊 parse 邊叫 lcode
   │  lcode.c (codegen)    → 產 bytecode 寫進 Proto
   ▼
 lvm.c luaV_execute():1204  → dispatch switch 逐條跑 bytecode
   │                          OP_ADD 在 :1512
   ▼  結果
```

**此刻用的工具**：document symbol（單檔地圖）+ treesitter（結構）+ gtags/rg（定位主迴圈）。建圖是把偵察印象外化成圖，靠的是「看結構」和「定位」的工具。

---

## 定位 + 追蹤（SOP 階段 3-4）：追一條路徑到底

現在追「加法怎麼從 bytecode 執行」，並反查「主迴圈誰呼叫」建 call chain。

### 追語意：clangd 的 gd / gr

在 `lvm.c` 的 `luaV_execute` 上，`gr`（找所有引用，Ch 19）看誰呼叫主迴圈。若 clangd 有 compile db（lua 的 `bear -- make` 生的），它精準回答。

> `gr` 的結果是互動的 references picker / quickfix，headless 截不了；以下用等價的 gtags 反查真跑，兩者答案應一致（clangd 精準、gtags 靠 tag 匹配）。

反查 `luaV_execute` 的呼叫者（gtags 真跑，過濾掉標頭宣告）：

```
$ global -rx luaV_execute
luaV_execute      768 ldo.c    luaV_execute(L, ci);  /* call it */
luaV_execute      876 ldo.c    luaV_execute(L, ci);  /* execute down to higher C 'boundary' */
luaV_execute      933 ldo.c    luaV_execute(L, ci);  /* just continue running Lua code */
```

主迴圈被 `ldo.c` 呼叫三處。再往上一層，`ldo.c` 的 `luaD_call` 是「呼叫一個 Lua 函式」的核心：

```
$ global -x luaD_call
luaD_call         777 ldo.c    void luaD_call (lua_State *L, StkId func, int nResults) {
```

一條 call chain 浮現：`main`（lua.c）→ `lua_pcall`（保護呼叫）→ `luaD_call`（ldo.c:777）→ `luaV_execute`（lvm.c:1204，主迴圈）→ dispatch 到 `OP_ADD`（lvm.c:1512）。

### 追蹤時的導航命脈：jumplist 來回

追這條鏈時你不斷 `gd` 跳進定義、看完 `Ctrl-o` 倒退（Ch 5）。這是追蹤階段按最多的兩個鍵：

```
gd        跳進游標下符號的定義
Ctrl-o    倒退（回到跳之前的位置）
Ctrl-i    前進（重做倒退）
```

配合 Ch 27 的佈局：左側主檔（lvm.c）、右側開 `ldo.c` 對照 caller，`Ctrl-w h/l` 在兩者間切。追 `luaD_call` → `luaV_execute` 這一跳，caller 和 callee 並排，data flow 一目了然。

### clangd 跪了：退 gtags

真實攻堅常撞到：**clangd 沒反應**。`gd` 按下去游標不動、或跳到錯的同名符號。三種可能（Ch 17/18/29）：

1. clangd 還沒 attach（剛開檔，`:LspInfo` 看狀態）。
2. 缺 `compile_commands.json`（clangd 不知道怎麼編，`gd` 半殘）。
3. clangd 還在背景索引大專案（等它）。

lua 這種小專案通常沒事，但讀 kernel（編不起來、沒 compile db）時 clangd 幾乎必跪。**這時候退 gtags**——它不需要編譯，純靠 tag 匹配，上面那些 `global -x`/`global -rx` 就是它。gtags 不如 clangd 精準（同名符號它全給你，不做語意辨析），但**它永遠有答案**。這是本課 Part 4→Part 5 的核心取捨：clangd 精準但脆弱、gtags 粗糙但不死。

追蹤階段的工具動用順序（`reading_code` Ch 38 的精華）：

```
rg          → 快速定位符號出現在哪（廣、快、不精準）
  ↓
gd/gtags    → 跳定義（clangd 精準版 / gtags 不死版）
gr/global -rx → 反查 caller（建 call chain 骨架）
  ↓
（Ch 29 的 gdb） → 靜態讀不確定時，動態驗證實際走哪條
```

---

## 組織與外化（SOP 貫穿全程）

### 把 caller 組進 quickfix

反查出 `luaV_execute` 的三個 caller（在 ldo.c 的 768/876/933），這是一張「待看清單」。`gr` 的結果會進 quickfix（Ch 12），或手動 `:grep luaV_execute`。然後：

```
:copen     開 quickfix 視窗（底部區，Ch 27 佈局）
:cnext     跳下一個命中
:cprev     跳上一個命中
```

三個 caller 逐一 `:cnext` 看過去，**不會漏、不會迷失**。quickfix 把「一堆散落的位置」變成「一張有序清單」，這是追蹤階段對抗迷失的關鍵。

### 用 harpoon 標熱點

這條路徑的核心是四個位置。逐一 `<leader>a` 釘進 harpoon（Ch 26）：

```
#1  lua.c:777       main（入口）
#2  ldo.c:777       luaD_call（呼叫核心）
#3  lvm.c:1204      luaV_execute（主迴圈）
#4  lvm.c:1512      OP_ADD（加法落地）
```

接下來讀 #4 的 OP_ADD 實作，想確認 #3 主迴圈怎麼 dispatch 過來——`<leader>3` 瞬移回主迴圈看，看完 `<leader>4` 回來。四個攻堅點一鍵之間來回，不用記行號。

### 用 notes.md 外化理解

每追通一段，記進 notes.md 的發現欄（帶證據）：

```markdown
## 假設
- [x] H1: OP_ADD 是加法的落地點 —— 已驗證，見 F4

## 發現
- F3: call chain: main → lua_pcall → luaD_call(ldo.c:777) → luaV_execute(lvm.c:1204)
- F4: luaV_execute 是巨大 dispatch switch，OP_ADD 在 lvm.c:1512，做整數/浮點加法
- F5: 主迴圈被 ldo.c 三處呼叫（768/876/933），對應不同的呼叫情境
```

**位置交給 harpoon，理解交給 notes.md**——Ch 26 的分工在這裡活體示範。

### 動態驗證（收尾，接 Ch 29 精神）

靜態追完，跑一次確認。lua 能自我觀察：

```
$ ./lua -e 'local f=function(a,b) return a+b*2 end; print(f(3,4))'
11
```

`3 + 4*2 = 11`，行為與「先乘後加、OP_ADD 最後執行」的 bytecode 推論一致。完整攻堅這裡會再上 gdb 在 `luaV_execute` 的 `OP_ADD` 下斷點看真實 dispatch（Ch 29 展開），但這一跑已初步驗證假設 H1。

---

## 一整套協同：從打開 repo 到懂一條路徑

把上面串成一條連續的操作流，這就是「一台機器」的樣子：

```
 cd lua && nvim          進場
 :e notes.md             界定任務（三欄）
 <leader>ff              telescope 看結構 → 檔名分層一目了然
 rg "int main"           找入口 → lua.c:777 唯一 main
 開 lvm.c, <leader>ds    document symbol 建單檔地圖
 global -x luaV_execute  定位主迴圈 → lvm.c:1204
 gr / global -rx         反查 caller → ldo.c 三處
 gd + Ctrl-o             追 call chain，左右分割對照
 （clangd 跪就退 gtags）  global -x/-rx 不死版
 :copen + :cnext         caller 組進 quickfix 逐一看
 <leader>a ×4            harpoon 釘四個熱點
 <leader>1-4             在熱點間瞬移
 記 notes.md             外化假設/發現（帶證據）
 ./lua -e '...'          動態驗證
 （關 nvim）             persistence 自動存現場（Ch 27）
```

**十幾個工具，一條流水線。** 這就是本課的全部——把六個 Part 的工具，串成攻堅一條路徑的連續動作。你不用每次都跑完全部，但這個順序（廣而快 → 窄而精、靜態 → 動態、位置與理解都外化）是攻堅的預設路徑。

## 鍵位表（本次流程用到的）

| 階段 | 模式 | 按鍵 / 命令 | 作用 |
|---|---|---|---|
| 界定 | c | `:e notes.md` | 開三欄筆記 |
| 偵察 | n | `<leader>ff` | telescope 看結構 |
| 偵察 | n | `<leader>fg` | live_grep 找 main |
| 建圖 | n | `<leader>ds` | document symbol 單檔地圖 |
| 定位 | n | `<leader>fs` | workspace symbol 跨檔找符號 |
| 追蹤 | n | `gd` | 跳定義（clangd） |
| 追蹤 | n | `gr` | 找所有引用 |
| 追蹤 | n | `Ctrl-o` / `Ctrl-i` | jumplist 倒退 / 前進 |
| 追蹤 | n | `Ctrl-w h/l` | 左右分割間切 |
| 後備 | — | `global -x foo` | gtags 查定義（clangd 跪時） |
| 後備 | — | `global -rx foo` | gtags 反查 caller |
| 組織 | c | `:copen` / `:cnext` | quickfix 逐一看 caller |
| 外化 | n | `<leader>a` | harpoon 釘熱點 |
| 外化 | n | `<leader>1-4` | 熱點間瞬移 |

## 對比與取捨：這一步該用哪個工具

| 你想做的事 | 主力工具 | 為什麼 | 退而求其次 |
|---|---|---|---|
| 看專案整體結構 | telescope find_files | 廣、快、模糊 | `tree -L 2` |
| 找入口/某字串 | rg / live_grep | 全文、極快、不需索引 | telescope grep |
| 看單檔有什麼函式 | document symbol（clangd） | 語意精準的清單 | treesitter / ctags |
| 跨檔找某符號 | workspace symbol | 語意 | gtags `global` |
| 跳定義 | `gd`（clangd） | 語意精準、辨析同名 | gtags `global -x` |
| 反查 caller | `gr`（clangd） | 精準 | gtags `global -rx` |
| clangd 跪了（沒 compile db） | **gtags** | 不需編譯、永遠有答案 | ctags（更粗） |
| 組織一堆待看位置 | quickfix | 有序清單、逐一走 | — |
| 記熱點位置 | harpoon | 一鍵瞬移 | 大寫 mark |
| 記假設/發現 | notes.md 三欄 | 理解外化 | scratch buffer |

## 踩雷集錦

1. **偵察還沒做完就一頭栽進讀函式體。** 最常見的失誤：打開 repo，`<leader>ff` 隨便開一個檔就開始逐行讀。你連 main 在哪、架構怎麼分層都不知道，讀了半小時還在迷宮裡。**偵察（telescope 看結構 + rg 找 main）是廣而快的，先花十分鐘建全局印象再深入。** SOP 階段有順序不是形式主義。

2. **clangd 沒反應就硬等/硬幹，不退 gtags。** `gd` 按了游標不動，你以為 nvim 壞了、反覆按。其實是 clangd 沒 attach 或缺 compile db。**先 `:LspInfo` 判斷（Ch 29），確認 clangd 半殘就立刻退 gtags**——`global -x`/`global -rx` 三秒給你答案。在 clangd 上耗時間是新手最大的時間漏洞。

3. **追 call chain 不用 jumplist，用搜尋跳回去。** 追進 `luaV_execute` 看完，想回 `luaD_call`——你 `<leader>fg` 打字搜 `luaD_call` 跳回去。慢死，且丟了位置。**`Ctrl-o` 一鍵倒退**就回原地。追蹤階段 `gd`/`Ctrl-o` 是一對，進去出來成反射，別用搜尋當倒退。

4. **找到一堆 caller 不組進 quickfix，靠腦記。** `gr` 出來八個 caller，你一個個手動開、記著看了哪幾個——看到第五個忘了前面看過沒。**進 quickfix、`:cnext` 逐一走**，機器替你記進度。quickfix 就是為「一張待看清單」設計的（Ch 12）。

5. **只追不外化，追完就忘。** 你花一小時追通了 call chain，關 nvim 去吃飯——回來忘了一半。**追的過程就該記 notes.md（帶證據）、釘 harpoon（記位置）。** 這章示範的協同裡，外化不是最後補的，是**貫穿全程**的。位置和理解一邊追一邊倒出腦袋，才不會白追。

## 進階：再往深一層

- **這條流水線是可壓縮的**：熟練後很多步合併。看結構直接 `<leader>ff` 掃一眼、找主迴圈直接 gtags 一查、追鏈 `gd`/`Ctrl-o` 連發。生手照 SOP 一步步走，熟手把它壓成幾秒的連續動作——但**壓縮的是速度，不是省略階段**。卡住時退回完整流程，就像 SOP 是「卡住時的預設路徑」（Ch 38）。

- **不同專案調整工具順序**：lua 乾淨、clangd 好用，所以主力是 clangd、gtags 當後備。讀**編不起來的 kernel** 時反過來——clangd 幾乎沒用（缺全域 compile db），gtags 變主力（練習 E 就是練這個）。讀**動態語言**（Python）靜態索引弱，重心移到「跑起來 + 讀測試」。工具順序隨生態變（`reading_code` Ch 38「客製 SOP」）。

- **把整套現場凍結接續**（接 Ch 27）：今天追到 harpoon #4、notes.md 記了 F5，關 nvim persistence 自動存。明天 `cd lua && nvim`、`<leader>qs` 載入 session（螢幕回來）、`<C-e>` 看 harpoon（四個熱點回來）、`:e notes.md`（思緒回來）——五秒重建整個攻堅現場，接著追 F5 留下的問題。這才是「讀大 codebase 好幾天」的完整循環。

- **同一路徑第二輪換視角**（接 `reading_code` Ch 32）：這輪追的是「正常執行路徑」。第二輪用**找漏洞視角**重走——OP_ADD 的整數溢位？luaD_call 的 stack 邊界檢查在哪？同一台機器、同一套導航，換一組問題，就從「讀懂」升級成「審安全」。

## 本章重點整理

- 會用工具 ≠ 會協同工具。攻堅的難處是「此刻該用哪個」，本章把 SOP 骨架對應到具體 nvim 操作。
- 完整流程：界定（notes.md）→ 偵察（telescope + rg）→ 建圖（document symbol + treesitter）→ 定位/追蹤（gd/gr + jumplist，clangd 跪退 gtags）→ 組織外化（quickfix + harpoon + notes.md）→ 驗證（跑起來）。
- 工具動用順序是效率核心：廣而快（telescope/rg）→ 窄而精（clangd/gtags）、靜態 → 動態。
- clangd 精準但脆弱、gtags 粗糙但不死，判斷 clangd 跪了就立刻退 gtags，別硬等。
- 外化貫穿全程：位置一邊追一邊釘 harpoon、理解一邊追一邊記 notes.md，不是最後補。

## 自我檢核

- [ ] 打開一個沒看過的 C repo，我第一步做什麼？用哪個工具、為什麼是廣而快的？
- [ ] `gd` 按了沒反應，我怎麼判斷是 clangd 沒 attach、缺 compile db、還是該退 gtags？
- [ ] 追 call chain 時我靠什麼倒退回上一個位置？為什麼不該用搜尋跳回去？
- [ ] 反查出八個 caller，我怎麼組織它們不漏不迷失？
- [ ] 這條流水線裡，外化（harpoon/notes.md）該在什麼時候做——最後補還是全程？

## 延伸閱讀

- **`reading_code` Ch 38「打造你自己的讀碼 SOP」**（`soft_skills/reading_code/38-your-reading-sop.md`）
  - **讀哪裡**：整章，特別是「工具動用順序」那張圖（rg→ctags/cscope→clangd→gdb）和六階段時間盒。
  - **學到什麼**：本章是那份 SOP 的「nvim 落地版」。那章講抽象流程和工具順序的邏輯，本章把它變成具體按鍵——兩章對照讀，你會看到方法論怎麼變成肌肉。

- **`reading_code` Ch 39「案例研究：完整攻堅實況」**（`soft_skills/reading_code/39-case-study-full-attack.md`）
  - **讀哪裡**：整章的 redis 完整攻堅示範。
  - **學到什麼**：另一個完整攻堅案例（redis），跟本章的 lua 案例互補。看同一套 SOP 在不同專案上怎麼跑，能幫你抽象出「不變的流程」和「隨專案變的工具順序」。

- **Neovim `:help jumplist` 與 `:help quickfix`**
  - **讀哪裡**：`:help jump-motions`（`Ctrl-o`/`Ctrl-i` 機制）、`:help quickfix`（`:copen`/`:cnext` 與 quickfix 怎麼被 `gr` 填充）。
  - **學到什麼**：追蹤階段最核心的兩個導航機制。jumplist 是「追進去→倒退」的命脈，quickfix 是「組織一堆待看位置」的容器，本章反覆用到，讀懂底層你用得更狠。

一台機器完整演出過了。但這台機器是你搭的——config 每一塊都是前面某一章加的。下一章我們回頭**通讀整份 config**、學會按需改 keymap、以及最重要的：**config 出問題怎麼 debug**（`:checkhealth`/`:LspInfo`/`:Lazy`）。讀者到這裡，該能自己維護這台機器了。

→ [Ch 29 客製與除錯 config](./29-customize-and-debug-config.md)
