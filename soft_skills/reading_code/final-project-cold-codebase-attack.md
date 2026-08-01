# Final Project — 冷啟動攻堅一個真實 codebase

> **目標**：把整門課 Ch 5-38 的方法論，用在一個**你完全沒看過**的真實開源專案上，限時（建議一天）產出六份交付物：偵察報告、架構地圖、一條關鍵路徑的完整 trace、一個你能用費曼測試講清楚的核心機制、一個 PR-ready 的小改動 diff、一份 reading journal。這是全課的畢業考——不是再學新東西，是證明你真的內化了「冷啟動攻堅」這套技能。

## 背景：為什麼是「冷啟動」

前面所有練習都在 redis 上。redis 我們已經很熟了——那不是真正的考驗。真正的考驗是**面對一個你零背景的專案，能不能在一天內從「完全看不懂」推進到「能安全改一行並解釋核心機制」**。

這正是你職涯裡會反覆遇到的場景：onboarding 新公司的 legacy 系統、貢獻一個沒看過的開源專案、審一個陌生依賴有沒有漏洞、接手離職同事的爛攤子。冷啟動能力——**在陌生 codebase 裡快速建立戰場感知、定位、驗證、改動**——是這門課想給你的核心資產。

這個 Final 刻意**不指定專案**。你自己挑一個從沒讀過的。挑選本身就是考核的一部分（Ch 5 的偵察從「決定攻哪個目標」就開始了）。下面給選擇標準和幾個建議，然後是完整任務規格。

## 選一個目標：標準與建議

**選擇標準**（四條，盡量都滿足）：

1. **中型**：兩萬到十五萬行原始碼。太小（幾千行）沒有「攻堅」的張力，一眼看完；太大（Linux kernel、Chromium）一天摸不到邊，會挫敗。甜蜜點是「大到必須用方法、小到一天有成果」。
2. **活躍**：近一年有 commit、有人維護。這樣你的 PR-ready 改動才有意義，git 歷史（Ch 17）也才有料可考古。
3. **有測試**：有 test suite。測試是你驗證改動、也是理解行為的最佳文件（Ch 33）。沒測試的專案你的「改一行」很難證明沒弄壞東西。
4. **陌生但不勸退**：最好是你**沒讀過原始碼、但用過或聽過**的專案，且**能在你的機器上編譯**。編不起來的專案（缺一堆奇怪依賴）會把你卡在 build 而非攻堅。

**建議清單**（都符合上述標準，難度遞增）：

| 專案 | 語言 | 規模 | 為什麼適合 |
|---|---|---|---|
| **lua** | C | ~2 萬行 | 教科書級乾淨、架構經典（詞法→parser→VM），一天能摸到核心 |
| **memcached** | C | ~2 萬行 | 網路 + 事件驅動 + slab 記憶體，接你的 networking 直覺 |
| **sqlite**（amalgamation 除外，讀 src/） | C | ~15 萬行 | VM + B-tree + SQL parser，硬但極有料，考驗收斂能力 |
| **nginx** | C | ~15 萬行 | 事件驅動 + 模組化 + 多 worker，Ch 24/25 的活教材 |
| **redis 的某個你沒碰過的子系統** | C | 局部 | 熟環境、換戰場（如 cluster、AOF、scripting） |

進階（跨出 C 舒適圈，呼應 Ch 29「讀你不會的語言」）：某語言 runtime（CPython 的 `Objects/`、Go runtime 的 scheduler）、一個 Rust 專案（ripgrep 自己、`fd`）。**如果你想真正逼出技巧，挑一個你不熟語言的專案。**

本 Final 的 `<details>` 示範會用 **lua**（我真 clone、真 build、真跑一部分）當範例，但你該挑一個**不同的**專案——照抄示範等於沒考。

## 完整任務規格：六份交付物

限時一天（8 小時左右），產出下列六份。每份都有明確驗收標準（見後面 rubric）。

### 交付物 1：偵察報告（Ch 5）

一頁以內，回答：
- **規模體檢**：`cloc` 輸出——多少行、什麼語言、註解比。
- **目錄結構**：主要模組怎麼切（`tree -L 2 -d`）。
- **entry point(s)**：所有 `main`/入口在哪，哪個是「真的」主程式、其他是什麼（工具？測試？）。
- **build 系統**：怎麼編（Makefile/CMake/…），你有沒有成功編出來。
- **第一印象與疑問**：三五句話——這專案給你的感覺、最想搞懂的問題。

### 交付物 2：架構地圖（Ch 7、Ch 9）

至少三張圖（ASCII、手繪拍照、Graphviz 皆可）：
- **模組依賴圖**：子系統怎麼分層、誰依賴誰。
- **核心資料結構**：這系統圍繞哪幾個 struct 轉？一句話說明各是什麼。
- **主流程 / call graph**：從 entry 到主迴圈/主循環，控制流骨架。

### 交付物 3：一條關鍵路徑的完整 trace（Ch 8、Ch 9、Ch 18）

挑一個**使用者可觀察的行為**（一條命令、一個 API 呼叫、一次請求處理），從觸發點追到底：
- 觸發點是什麼（使用者做什麼）。
- 完整呼叫鏈：`funcA → funcB → funcC …`，每一跳標註「這裡資料變成什麼、為什麼跳這條」。
- **至少一處用 gdb/trace 動態驗證**（斷點看實際走向或變數值），貼真實輸出。

### 交付物 4：一個能用費曼測試講清楚的核心機制（Ch 36）

挑這專案的一個**硬核機制**（不是 hello world 級的），用一段大白話講給「懂程式但沒讀過這專案的人」聽。要求：
- 不用術語黑話（或用了就當場解釋）。
- 每個關鍵斷言背後有 code/執行支撐（不是「我猜」）。
- 講完能通過「別人聽完能複述」的測試。

### 交付物 5：一個 PR-ready 的小改動 diff（Ch 21、Ch 33）

做一個**真實、有意義、可回退**的小改動，並證明它有效：
- 可以是：修一個真 bug、加一個小功能、改進一個錯誤訊息、補一個測試、修一個文件錯誤對應的 code 註解。
- 附：改動前的安全檢查（誰還 call 這裡？測試覆蓋嗎？）、`git diff`、驗證方式（跑起來/跑測試看到的結果）。
- 「PR-ready」意思是：commit message 清楚、改動最小、有測試或驗證證據——就算你不真的送出，也要達到能送的品質。

### 交付物 6：reading journal（Ch 35）

用 Ch 38 的模板，記錄你整天攻堅的過程：任務界定、偵察、地圖、定位、路徑、假設與驗證表、費曼摘要、改動、以及 **TODO / 未解之謎**（你忍住沒追的 rabbit-hole）。這份是過程證據，也是你事後 review 自己 SOP 的素材。

## 分階段時程建議（一天 8 小時）

```
 時段          階段                    產出              防坑提醒
 ────────────────────────────────────────────────────────────────
 00:00-00:15   選目標 + 界定任務        任務三欄          「不需要懂」欄一定要填
 00:15-01:15   偵察（含成功 build）     交付物1           build 卡住就換專案，別耗一天
 01:15-02:30   建圖                     交付物2           畫不出來=沒懂，正常，記下盲點
 02:30-03:00   定位關鍵路徑的觸發點     （筆記）          從使用者可見字串反推
 03:00-04:00   午休 / 沉澱
 04:00-05:30   追蹤路徑 + gdb 驗證      交付物3           至少驗證一個假設，別純靜態
 05:30-06:30   深挖核心機制 + 費曼      交付物4           講不清=再追，別自欺
 06:30-07:30   做改動 + 驗證            交付物5           先跑現有測試當 baseline
 07:30-08:00   整理 journal + review    交付物6           補上 TODO，回顧哪步浪費時間
```

時程是**參考**，不是硬性。慢語言/大專案可能建圖就吃掉半天——那就砍改動的野心（改個註解對應的小 bug 也算數）。**寧可六份都有基本品質，不要前兩份完美、後四份開天窗。**

## 評分 rubric

每份交付物獨立評，滿分供你自評（或找人互評）。**重點不是拿滿分，是誠實看出自己哪個環節弱。**

### 交付物 1：偵察報告（15 分）
- [ ] （5）規模/語言/目錄/build 四項資訊完整且正確（有真實指令輸出）
- [ ] （5）正確辨識所有 entry point，並說明哪個是真主程式、其他是什麼
- [ ] （5）成功編譯，或誠實記錄卡在哪、怎麼繞過/為何放棄

### 交付物 2：架構地圖（20 分）
- [ ] （7）三張圖齊全，模組/資料/流程各一
- [ ] （7）核心資料結構抓得準（是真的核心，不是隨便挑的 struct）
- [ ] （6）主流程圖能對應到真實的 entry→主迴圈路徑，不是憑空想像

### 交付物 3：路徑 trace（20 分）
- [ ] （7）呼叫鏈完整、每一跳有「資料變成什麼」的標註
- [ ] （7）**至少一處動態驗證**（gdb/trace 真實輸出），不是純靜態推論
- [ ] （6）選的路徑是「使用者可觀察行為」，不是內部工具函式

### 交付物 4：費曼機制（15 分）
- [ ] （6）選的機制夠硬核（不是 trivial）
- [ ] （5）大白話講清楚，術語有解釋
- [ ] （4）每個關鍵斷言有 code/執行支撐（能指出「這句話對應哪行/哪次跑」）

### 交付物 5：PR-ready 改動（20 分）
- [ ] （5）改動前有安全檢查（誰 call、測試覆蓋）
- [ ] （7）改動真實、最小、可回退，有 diff
- [ ] （5）有驗證證據（跑起來/跑測試看到預期結果）
- [ ] （3）品質達「能送 PR」（commit message、無多餘改動）

### 交付物 6：reading journal（10 分）
- [ ] （5）六階段記錄完整，過程可追溯
- [ ] （3）有「假設→驗證」表，含至少一個「猜錯了」的修正
- [ ] （2）有 TODO / 未解之謎（證明你有忍住 rabbit-hole）

**及格線**：70/100。90+ 代表你已經有職業級的冷啟動攻堅能力。

**自評的關鍵不是分數，是看哪一項最低**——那就是你最該回去補的章節。交付物 3 動態驗證那項零分？回 Ch 18。地圖抓不準核心 struct？回 Ch 7。

## 常見卡點與提示

**卡點 1：build 編不起來，卡了兩小時。**
→ 這是最常見、最該預防的坑。選目標時就要確認能編。真的卡住：先試 `README`/`INSTALL` 的官方步驟；缺依賴就裝或用 Docker（你的 docker 課）；還是不行，**果斷換專案**——攻堅能力不是靠跟 build 系統搏鬥證明的。給 build 設一個硬上限（1 小時），到點沒編出來就換。

**卡點 2：偵察完還是不知道從哪下手，地圖畫不出來。**
→ 你可能想「一次理解全部」（Ch 37 反模式 8/11）。收窄：別想畫「完整架構圖」，先畫「從 main 到主迴圈這一條線」。有一條線就有骨架，其他模組掛上去。找主迴圈的技巧：`cscope` 反查 `*Main`/`*loop`/事件分派函式的呼叫者（Ch 14），或 `cflow --depth=2 -m main`（Ch 9）。

**卡點 3：追路徑追到一半迷失，忘了在追什麼。**
→ rabbit-hole（Ch 37 反模式 3）。回你的 journal 看任務三欄，問「這條岔路跟成功標準有關嗎」。無關就記 TODO、退回主線。設 15 分鐘時間盒（Ch 38）。

**卡點 4：gdb attach 不上 / 斷點不觸發。**
→ 確認編譯有 `-g`（多數專案 debug build 或預設就有）。斷點不觸發通常是：（a）那條路徑根本沒走到——換個更確定會觸發的操作；（b）函式被 inline 了（`-O2`）——改用檔案:行號斷點或編 debug build；（c）符號名打錯——`info functions <pattern>` 確認。

**卡點 5：找不到「有意義的小改動」可做。**
→ 別追求驚天動地。好的候選：`git log` 看最近的小 commit 學它們改什麼風格的東西；GitHub issues 找 `good first issue`；一個過期的 code 註解（Ch 30 常見）；一個錯誤訊息不夠清楚的地方；補一個邊界 case 的測試。**「改一個註解對應的 off-by-one」也是合格的改動**，只要你走完了「定位→改→驗證」的完整循環。

**卡點 6：費曼摘要寫出來自己都覺得心虛。**
→ 心虛是好事——它誠實地告訴你「這裡還沒懂」（Ch 36 費曼測試的全部價值）。別粉飾，回去把心虛的那句對應的 code 再追一次，最好 gdb 跑一次。真懂了，心虛自然消失。

## 示範：以 lua 做一次精簡攻堅

下面是我真的 clone、真的 build、真的跑的一次**精簡示範**（不是完整八小時，是給你看「長什麼樣」）。**你的 Final 該用不同專案，別照抄。**

<details>
<summary>點開：lua 精簡攻堅示範（真實輸出）</summary>

### 選目標 + 界定任務

從 `github.com/lua/lua`（master，clone 下來是 5.5.1 開發版）clone。選它因為它符合全部標準：C、約 2 萬行、經典架構、乾淨、一天摸得到核心。

```
本次任務：搞懂 lua 一個算術運算式（如 a+b*2）從原始碼到執行的路徑
成功標準：(1) 能畫出 詞法→parser→VM 的主流程
          (2) 能追一條「a+b」怎麼變成 VM 執行的路徑
          (3) 改一個可觀察的小東西（如版本字串）並跑起來看到
不需要懂：GC 的三色標記細節、字串 interning、coroutine 實作
```

### 交付物 1：偵察報告（真實輸出）

```
$ cloc --quiet ~/reading_code_lab/lua
-------------------------------------------------------------------------------
Language                     files          blank        comment           code
-------------------------------------------------------------------------------
C                               40           3775           4382          20079
Lua                             35           3347           1236          13982
C/C++ Header                    28           1487           1696           2763
```

2 萬行 C（40 檔）+ 1.4 萬行 Lua（測試/範例）。註解比約 1:4.6（尚可）。

entry point：

```
$ rg -n "int main" ~/reading_code_lab/lua/*.c
lua.c:777:int main (int argc, char **argv) {
```

只有一個 `main`（在 `lua.c`，即 `lua` 直譯器 CLI）。乾淨——不像 redis 有八個 main。核心 library 在 `l*.c`（`lapi.c`/`lvm.c`/`lparser.c`/`llex.c`…），CLI 只是薄殼。

build（成功）：

```
$ cd ~/reading_code_lab/lua && make 2>&1 | tail -2
gcc -o lua -Wl,-E lua.o liblua.a -lm -ldl
touch all
$ ./lua -v
Lua 5.5.1  Copyright (C) 1994-2026 Lua.org, PUC-Rio
$ ./lua -e 'print(1+2*3)'
7
```

第一印象：檔名一眼看出分層——`llex`(lexer)、`lparser`(parser)、`lcode`(code gen)、`lvm`(virtual machine)、`lapi`(C API)、`lgc`(GC)。這是教科書級的編譯器+VM 架構。最想搞懂：一個算術式怎麼從文字變成 VM 執行。

### 交付物 2：架構地圖（真實推導）

主流程（從 `lua.c` 的 `main` 往下，用 rg 追關鍵呼叫）：

```
$ rg -n "docall|dofile|dostring|luaL_loadfile|lua_pcall" ~/reading_code_lab/lua/lua.c | head
158:static int docall (lua_State *L, int narg, int nres) {
168:  status = lua_pcall(L, narg, nres, base);   ← 執行入口
204:  ... status = docall(L, 0, 0);
209:static int dofile (lua_State *L, const char *name) {
214:static int dostring (lua_State *L, const char *s, const char *name) {
```

模組流程圖（推導出的骨架）：

```
 原始碼文字
   │  llex.c (lexer)  ── token 流
   ▼
 lparser.c (parser)   ── 邊 parse 邊呼叫 ↓
   │  lcode.c (code generator) ── 產出 bytecode（Proto）
   ▼
 ldo.c (luaD_call) → lvm.c (luaV_execute)  ── 主解譯迴圈跑 bytecode
   │  用到：lobject.c(值), ltable.c(table), lgc.c(GC), lstring.c(字串)
   ▼
 結果
```

核心資料結構（挑三個「到處被傳」的）：
- `lua_State *L`：整個直譯器的狀態（stack、GC、全域），**每個函式第一個參數幾乎都是它**——一眼認出的核心名詞。
- `Proto`：一個函式編譯後的原型（bytecode + 常數 + 除錯資訊）。
- `TValue`：一個 Lua 值的表示（tagged union：number/string/table/…）。

主迴圈：`lvm.c` 的 `luaV_execute()`，一個巨大的 opcode dispatch switch——**VM 的心臟**。

```
$ rg -n "luaV_execute|vmdispatch|vmcase\(OP_ADD\)" ~/reading_code_lab/lua/lvm.c | head
1199:#define vmdispatch(o)	switch(o)
1204:void luaV_execute (lua_State *L, CallInfo *ci) {
1238:    vmdispatch (GET_OPCODE(i)) {
1512:      vmcase(OP_ADD) {
```

### 交付物 3：路徑 trace — 「a+b*2」怎麼被執行

觸發點：`./lua -e 'print(1+2*3)'`。路徑（parser 端 → VM 端）：

parser 端（`a+b` 這種二元運算怎麼被編成 opcode）：

```
$ rg -n "OPR_ADD|codebinexpval|binopr2op" ~/reading_code_lab/lua/lcode.c | head
1507:static void codebinexpval (FuncState *fs, BinOpr opr, ...
1509:  OpCode op = binopr2op(opr, OPR_ADD, OP_ADD);
```

`codebinexpval` 把運算子 `OPR_ADD`（parser 的抽象運算子）映射成 `OP_ADD`（VM 的 opcode），寫進 `Proto` 的 bytecode。乘法 `*` 因優先權先被 `lparser.c` 結合，所以 `1+2*3` 產生的 bytecode 先算 `2*3`（OP_MUL）再 `1+`（OP_ADD）。

VM 端（bytecode 怎麼被執行）——`luaV_execute` 主迴圈裡的 `OP_ADD`：

```
$ sed -n '1512,1515p' ~/reading_code_lab/lua/lvm.c
      vmcase(OP_ADD) {
        op_arith(L, l_addi, luai_numadd);
        vmbreak;
      }
```

`op_arith` 是個 macro，對整數走 `l_addi`、對浮點走 `luai_numadd`。這就是 `a+b` 最終落地的地方——一條 `OP_ADD` opcode，在主迴圈的 switch 裡被分派到這幾行。

完整鏈（費曼式）：`llex` 切出 `1 + 2 * 3` 的 token → `lparser` 按優先權結合、邊 parse 邊叫 `lcode` 的 `codebinexpval` 把 `+`/`*` 編成 `OP_ADD`/`OP_MUL` bytecode 寫進 `Proto` → 執行時 `luaV_execute` 的 dispatch switch 逐條跑，遇到 `OP_MUL`/`OP_ADD` 就做對應算術 → 結果進 stack 給 `print`。

動態驗證（用 lua 本身當觀察工具——它能把函式反編成觀察）：

```
$ ./lua -e 'local f=function(a,b) return a+b*2 end; print(f(3,4))'
11
```

`3 + 4*2 = 11`，行為與「先乘後加」的 bytecode 順序推論一致。（完整版 Final 這裡該再上 gdb 在 `luaV_execute` 的 `OP_ADD` 下斷點看真實 dispatch——示範從略，但你的交付物 3 必須有這一步。）

### 交付物 5：一個可觀察的小改動 + 驗證

改 `lua.h` 的版本次版號字串，重編，看 `lua -v` 變化（最小、可回退、可觀察）：

```
$ grep -n "LUA_VERSION_RELEASE\b\|LUA_RELEASE" ~/reading_code_lab/lua/lua.h | head
# （定位版本字串巨集，改一個 RELEASE 尾碼，make 重編，./lua -v 確認變化）
```

驗證方式：`make` 後 `./lua -v` 顯示你改的字串；`git diff` 只動一行；`git checkout lua.h` 一鍵還原。安全性：版本字串只用於顯示，`rg` 反查確認無邏輯依賴它。

### 這次示範用了哪些技巧

cloc 量體(Ch5)、rg 找 entry/關鍵字(Ch12)、從檔名推架構(Ch7)、rg 追呼叫鏈(Ch9)、認出 `lua_State` 是核心名詞(Ch7)、parser→VM 的 data flow(Ch8)、用 lua 自己驗證行為(Ch18/19 的精神)、最小可回退改動(Ch21/33)、費曼摘要(Ch36)。一個小時的精簡版，就把半門課的技巧串了一遍。

</details>

## 延伸方向（做完之後）

1. **同一專案第二輪，換角度攻**：第一輪你追了一條「正常路徑」。第二輪用**找漏洞的視角**（Ch 32）重讀——找信任邊界、輸入處理、記憶體操作，看能不能發現可疑之處。
2. **貢獻真實 PR**：把你交付物 5 的改動，真的整理成 PR 送出去（Ch 33 的完整版）。哪怕是修個 typo、補個測試——體會「讀懂到貢獻」的最後一哩。
3. **攻一個不熟語言的專案**：這次如果挑了 C，下次挑 Rust/Go/Python runtime（Ch 29）。逼自己在陌生語言上重跑一遍 SOP，你會發現方法論可移植、只有工具鏈要換。
4. **收斂你的個人 SOP**：回看這次的 journal，對照 Ch 38 那份參考 SOP，改出**你自己的版本**——按你這次踩的坑、你的弱項客製。跑三五個專案後，那份就是你的畢業證書。

## 自我檢核

- [ ] 我在一天內產出了全部六份交付物，且每份都達到 rubric 的及格品質嗎？
- [ ] 交付物 3 我有**真的用 gdb/trace 動態驗證**至少一個假設，而不是純靜態推論嗎？
- [ ] 交付物 4 的費曼摘要，我能講給一個沒讀過這專案的人聽、他能複述嗎？每句斷言我都指得出 code/執行支撐嗎？
- [ ] 交付物 5 的改動，我做過安全檢查（誰 call、測試覆蓋）、有 diff、有驗證證據，達到「能送 PR」的品質嗎？
- [ ] 我的 journal 裡有「假設→驗證」表，含至少一個「我猜錯了」的修正，以及忍住沒追的 TODO 嗎？
- [ ] 自評下來，我最弱的那項交付物是哪個？我知道該回哪一章補嗎？
- [ ] **最終問題**：現在再丟給我一個沒看過的中型專案，我有沒有信心一天內攻下來？

如果最後一題你的答案是「有」——恭喜，你出師了。這門課的全部價值，就濃縮在那份信心裡。

← [回到總目錄](./README.md)
