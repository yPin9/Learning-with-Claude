# Ch 3 — Lua 偵察：60 分鐘畫出一張架構地圖

> **目標**：拿 `reading_code` 的 60 分鐘偵察 SOP（Ch 5）對 Lua v5.4.7 做第一次接觸。不追任何一條路徑到底，只求在一小時內建出「這 2 萬行分幾層、entry 在哪、我之後三章要讀的檔案各在哪」的地圖。這是全課第一個真讀真 clone 的目標，也是後面五個目標偵察節奏的範本。

> **目標codebase**：Lua `v5.4.7`（commit `1ab3208`）

## 為什麼需要這個？

Lua 是全世界最乾淨的語言 runtime。整個核心加標準庫約 2 萬 6 千行 C（你等下會親手數），沒有一行組語、沒有平台特例的地雷、沒有為了效能犧牲可讀性的巨集地獄——它是為了「一個人讀得完」而寫的。所以我們拿它熱身：先在一個你**真的讀得完**的目標上，把 `reading_code` 教的偵察流程跑順，之後攻 SQLite、CPython 這種大目標時，同一套動作只是放大。

偵察的目的不是讀懂，是**畫地圖**。你要在 60 分鐘後能回答：

- 這專案分幾層？每層的職責邊界在哪？
- 程式從哪裡開始跑（entry point）？
- 我接下來三章要讀的 VM、值表示、GC，各住在哪個檔案？
- 哪些檔案是我這次**刻意不讀**的？

讀不懂任何一個 function 的內部沒關係。偵察階段你要的是骨架，不是血肉。

## 先建立直覺：Lua 長什麼樣

在打開任何檔案前，先在腦中放一張預期圖。一個語言 runtime 大致就是這幾塊：

```
   .lua 原始碼
      │
      ▼  ┌─────────────┐
         │ lexer/parser│  把文字變成 bytecode
         └─────────────┘
      │  （產出 Proto：一個函式的 bytecode + 常數表）
      ▼  ┌─────────────┐
         │  VM 直譯器  │  一條一條執行 bytecode
         └─────────────┘
      │  ↕ 期間不斷配置物件（table、字串、closure）
      ▼  ┌─────────────┐
         │     GC      │  自動回收沒人用的物件
         └─────────────┘
```

外加一圈 **C API**：宿主程式（`lua` 這支獨立直譯器，或任何嵌入 Lua 的 C 程式）透過它跟 runtime 溝通。這張圖現在是猜的，偵察就是去 code 裡確認它對不對、每塊叫什麼名字。

## 第一步：目錄結構與命名慣例（10 分鐘）

先 clone，對齊版本（這一步 Ch 0 講過為什麼不能跳）：

```bash
$ git clone --depth 1 --branch v5.4.7 https://github.com/lua/lua /tmp/rd_lua
$ cd /tmp/rd_lua && git rev-parse --short HEAD
1ab3208
```

> **第一個坑先講**：官方 Lua 發行版把原始碼放在 `src/` 底下（`src/lvm.c` 等），但 GitHub 上的 `lua/lua` 鏡像把它們攤在 repo 根目錄。本章引用檔名時用**裸檔名**（`lvm.c`），對得上你 clone 到的這份。你若改讀官網 tarball，同一個檔案在 `src/lvm.c`。**檔名一樣，路徑前綴不同**——這正是「對齊你 clone 的來源」的紀律。

`ls` 一下，你會立刻注意到一件事：幾乎每個檔案都是 `l` 開頭。

```bash
$ ls *.c *.h | head
lapi.c   lapi.h   lauxlib.c ...
lcode.c  lcode.h  lctype.c  ...
```

**這是 Lua 的命名慣例，也是你的第一條偵察線索**。`l` = Lua core。去掉 `l` 之後的字根就是這個檔案管什麼：

| 檔名 | 字根 | 管什麼 |
|---|---|---|
| `lstate` | state | `lua_State`、`global_State`、`CallInfo`——runtime 的中樞資料結構 |
| `lobject` | object | `TValue`、`GCObject`、`Table`、`Proto`——所有值與物件的定義 |
| `lvm` | vm | virtual machine，bytecode 直譯器主迴圈 |
| `lgc` | gc | garbage collector，增量三色標記清除 |
| `ldo` | do | 「執行」相關：函式呼叫、stack 管理、錯誤處理（longjmp） |
| `ltable` | table | Lua 唯一的複合資料結構，array + hash 混合 |
| `lapi` | api | C API 邊界，`lua_*` 那些對外函式 |
| `lcode` / `lparser` / `llex` | code/parser/lex | 前端：詞法、語法、bytecode 產生 |
| `lstring` / `lfunc` / `ltm` | string/func/tm | 字串駐留、closure/upvalue、tag method（metamethod） |

還有一組 `l*lib.c`（`lbaselib`、`lstrlib`、`ltablib`、`liolib`……）是**標準庫**，跟核心 runtime 分開。`lauxlib.c` 是給 C 擴充作者用的輔助 API（`luaL_` 前綴，注意是 `luaL` 不是 `lua`）。

光是這張命名表，你就已經把 2 萬行分成三堆了：**core（`l*` 核心）／ lib（`l*lib`）／ auxlib（`lauxlib`）**。這就是 `reading_code` Ch 7「建立架構地圖」的第一刀。

## 第二步：用 wc 量體積（5 分鐘）

地圖要有比例尺。哪個檔案大，通常代表那裡邏輯最重、最值得先看：

```bash
$ wc -l *.c | sort -n | tail -12
   962 ldebug.c
   995 ltable.c
  1028 ldo.c
  1126 lauxlib.c
  1463 lapi.c
  1743 lgc.c
  1874 lcode.c
  1874 lstrlib.c
  1899 lvm.c
  1967 lparser.c
  1983 ltests.c
 26044 total
```

（這是在 WSL2 上對 `1ab3208` 真跑出來的。`ltests.c` 是測試專用檔，正常 build 不編進去，先劃掉。）

讀這張表的方法：**最大的幾個檔案就是你這門課要攻的山頭**。`lvm.c`（1899 行）是 VM，`lgc.c`（1743 行）是 GC，`lparser.c`/`lcode.c` 是前端。我們 Part 1 刻意**不碰前端**（`lparser`/`lcode`/`llex`）——那是編譯器的活，你的 compiler 課群已經深挖過；我們專攻 runtime：VM（Ch 4）、值與 table（Ch 5）、GC（Ch 6）。

這就是收斂：2 萬 6 千行裡，我們這個 Part 真正要精讀的其實是 `lvm.c` + `lobject.h`/`lstate.h` + `ltable.c` + `lgc.c` 這幾個檔的關鍵路徑，加起來不到 3000 行。`reading_code` Ch 11「從 50 萬行收斂到你要改的 200 行」的技巧，在這裡就是「從 2 萬 6 千行收斂到 3000 行」。

## 第三步：找 entry point（15 分鐘）

語言 runtime 有兩種 entry，別搞混：

1. **程序的 entry**：獨立直譯器 `lua` 這支執行檔的 `main()`。
2. **runtime 的 entry**：一個 `lua_State`（一份 Lua 世界）怎麼被建立、bytecode 怎麼開始被執行。

先找程序 entry。`main` 只可能在獨立直譯器 `lua.c` 裡（不在核心，核心是給人嵌入的庫，沒有 `main`）：

```bash
$ rg -n "int main" lua.c
671:int main (int argc, char **argv) {
```

打開 `lua.c:671` 讀，它短得可以整段貼（`lua.c:671–686`，v5.4.7）：

```c
int main (int argc, char **argv) {
  int status, result;
  lua_State *L = luaL_newstate();  /* create state */
  if (L == NULL) {
    l_message(argv[0], "cannot create state: not enough memory");
    return EXIT_FAILURE;
  }
  lua_gc(L, LUA_GCSTOP);  /* stop GC while building state */
  lua_pushcfunction(L, &pmain);  /* to call 'pmain' in protected mode */
  lua_pushinteger(L, argc);  /* 1st argument */
  lua_pushlightuserdata(L, argv); /* 2nd argument */
  status = lua_pcall(L, 2, 1, 0);  /* do the call */
  result = lua_toboolean(L, -1);  /* get result */
  report(L, status);
  lua_close(L);
  return (result && status == LUA_OK) ? EXIT_SUCCESS : EXIT_FAILURE;
}
```

這 16 行是整個 C API 慣用法的縮影，值得逐行看：

- `luaL_newstate()`：建立一份 Lua 世界（`lua_State`）。這就是 **runtime 的 entry**——你剛找到了。
- `lua_pushcfunction(L, &pmain)` + 三個 `lua_push*`：把「要呼叫的函式」和「參數」推上 Lua 的 stack。**注意這裡沒有直接呼叫 `pmain`**，而是把它和參數堆到 stack 上，再用 `lua_pcall` 觸發。這是 Lua C API 的核心心智模型：**C 和 Lua 之間所有東西都經過一個 stack**（Ch 5 會細講，Ch 7 會列成 pattern）。
- `lua_pcall(L, 2, 1, 0)`：在保護模式（protected，能捕捉 Lua 錯誤而不是讓整個程序 crash）下呼叫，2 個參數、1 個回傳值。真正的 REPL/跑檔邏輯在 `pmain`（`lua.c:626`）裡。
- `lua_close(L)`：拆掉這份世界，回收所有東西。

你不用讀懂 `pmain` 內部——偵察階段知道「entry 是 `luaL_newstate` 建 state、`lua_pcall` 驅動執行」就夠了。這正是偵察的紀律：**找到門，不進屋**。

runtime 執行 bytecode 的真正引擎在哪？搜 VM 主迴圈：

```bash
$ rg -n "luaV_execute" lvm.c
1055:** Function 'luaV_execute': main interpreter loop
1151:void luaV_execute (lua_State *L, CallInfo *ci) {
```

`lvm.c:1151` 的 `luaV_execute` 就是「一條一條跑 bytecode」的心臟，Ch 4 整章讀它。現在只要在地圖上釘一個圖釘：**entry 是 `main → luaL_newstate → lua_pcall`，執行引擎是 `luaV_execute`**。

## 第四步：抓分層（15 分鐘）

偵察最後一步是確認你猜的分層對不對。方法：挑幾個關鍵資料結構，看它們住在哪、被誰引用。

runtime 的中樞是 `lua_State`。它在哪定義？

```bash
$ rg -n "struct lua_State \{" lstate.h
```

（在 `lstate.h`，Ch 5 會讀它的欄位。）搭配它的還有 `global_State`（所有 thread 共享的全域狀態，GC 的欄位都掛在這）和 `CallInfo`（一層函式呼叫的資訊）。三個都在 `lstate.h`——這確認了 `lstate` = runtime 中樞的猜測。

再驗 VM 跟 GC 的關係。VM 執行時會配物件，配物件就可能觸發 GC。搜 `lvm.c` 裡有沒有 GC 的呼叫：

```bash
$ rg -n "luaC_" lvm.c | head
344:        luaC_barrierback(L, obj2gco(h), val);
806:        luaC_objbarrier(L, ncl, ncl->upvals[i]);
1132:      { luaC_condGC(L, (savepc(L), L->top.p = (c)), ...
1246:        luaC_barrier(L, uv, s2v(ra));
1865:        luaC_barrierback(L, obj2gco(h), val);
```

（`1ab3208` 真跑。）這幾行就是**分層之間的接縫**：VM（`lvm.c`）不自己管記憶體，它在配物件的地方呼叫 `luaC_condGC`（觸發 GC step）、在寫引用的地方呼叫 `luaC_barrier`/`luaC_barrierback`（維持 GC 正確性）——這些都是 `lgc.c` 的介面。你現在還不用懂 barrier 是什麼（Ch 6 的主題），但你已經在偵察階段**看到了 VM 和 GC 怎麼咬合**：VM 執行、GC 搭便車。找到接縫，兩層的關係就清楚了。同理 `rg -n "luaD_" lvm.c` 會露出 VM↔呼叫子系統的接縫（`OP_CALL` 呼叫 `luaD_precall`，Ch 4 的主線）。

把偵察結果收成一張地圖：

```
                        獨立直譯器 lua.c
                    (main → luaL_newstate → lua_pcall)
                                 │
   ┌─────────────────────────────┼─────────────────────────────┐
   │            C API 邊界  lapi.c (lua_* 函式)                  │
   │            輔助庫    lauxlib.c (luaL_* 函式)                 │
   └─────────────────────────────┬─────────────────────────────┘
                                 │  一切經過一個 stack
   ┌─────────────────────────────▼─────────────────────────────┐
   │                        CORE runtime                        │
   │                                                            │
   │   前端(本課不讀)          執行引擎             物件層        │
   │  ┌──────────────┐    ┌──────────────┐   ┌──────────────┐   │
   │  │ llex  詞法   │    │ lvm.c        │   │ lobject.h/.c │   │
   │  │ lparser 語法 │───▶│ luaV_execute │──▶│ TValue/Proto │   │
   │  │ lcode  產碼  │ Proto│ dispatch loop│   │ ltable.c     │   │
   │  └──────────────┘    └──────┬───────┘   │ lstring lfunc │   │
   │                             │            └──────────────┘   │
   │   狀態/呼叫             ┌────▼─────┐         記憶體/回收      │
   │  ┌──────────────┐      │checkGC/  │      ┌──────────────┐   │
   │  │ lstate.h     │◀────▶│barrier   │─────▶│ lgc.c        │   │
   │  │ lua_State    │      └──────────┘      │ 增量三色標記  │   │
   │  │ CallInfo     │                        │ lmem.c 配置   │   │
   │  │ ldo.c 呼叫/  │                        └──────────────┘   │
   │  │ longjmp錯誤  │                                            │
   │  └──────────────┘                                            │
   └────────────────────────────────────────────────────────────┘
              標準庫 l*lib.c (lbaselib/lstrlib/ltablib/liolib...)
```

這張圖是你這一小時的產出。它不完美（`ltm.c` 的 metamethod、`lstring.c` 的字串駐留還沒定位），但它**夠你決定接下來三章讀哪裡、以什麼順序讀**：Ch 4 攻 `lvm.c`、Ch 5 攻 `lobject.h`+`lstate.h`+`ltable.c`、Ch 6 攻 `lgc.c`。

## 底層機制：偵察為什麼要「先量再讀」

新手偵察最常見的錯誤是**打開第一個檔案就開始逐行讀**。這在 Lua（2 萬行）會浪費時間，在 CPython（近 50 萬行）會直接淹死你。正確的偵察是一層層縮小，每一步都廉價：

```
   ls (1秒)         →  看命名慣例，切出 core/lib/auxlib 三堆
      ▼
   wc -l | sort (2秒) →  按體積排出「山頭」，決定攻誰
      ▼
   rg entry (10秒)   →  找 main / 主迴圈，釘住起點
      ▼
   rg 關鍵struct(30秒) →  確認分層猜測、找層與層的接縫
      ▼
   讀 <60分鐘        →  只有到這一步才真正打開檔案讀
```

前面四步加起來不到一分鐘，卻幫你把「該讀哪 3000 行」從「該讀哪 2 萬 6 千行」裡框出來。**偵察的產出是一張地圖和一份「刻意不讀清單」，不是理解**。理解是後面三章的事。

## 對比與取捨

| 偵察策略 | 適用 | 風險 |
|---|---|---|
| **先量再讀**（本章） | 任何陌生專案 | 幾乎無；最多多花一分鐘量 |
| 從 `main` 一路 step 進去 | 你只想追一條路徑（練習 A 會這樣） | 偵察階段用會迷失在細節，看不到全局 |
| 打開最大的檔案硬讀 | 你已經知道要改那個檔 | 沒地圖，讀到一半不知道自己在哪層 |
| 讀官方文件/README 代替讀 code | 建初步心智模型 | 文件會過時、會騙人；Lua 的 `manual/` 是規格不是實作導覽 |

Lua 有個奢侈品：它真的小到你可以在偵察後直接精讀完核心。SQLite（Part 2）、CPython（Part 5）不行，那時「刻意不讀清單」會比「要讀清單」長十倍，偵察的價值更大。

## 踩雷集錦

1. **以為 `lua` 的 `main` 在核心裡**。核心（`liblua.a` 那堆 `l*.c`）是**庫**，給人嵌入用，沒有 `main`。`main` 只在獨立直譯器 `lua.c`。錯誤直覺「runtime 一定有個 main 迴圈啟動它」→ 正確：runtime 的啟動是 `luaL_newstate` 建 state，執行是宿主呼叫 `lua_pcall` 觸發，`luaV_execute` 才是 bytecode 迴圈。
2. **把 `lauxlib` 當核心讀**。`lua*` 前綴是核心 C API（`lapi.c`），`luaL_*`（大寫 L）是**輔助庫**（`lauxlib.c`），建在核心 API 之上、給 C 擴充作者省事用。偵察時把它劃到「邊界層」而非「core」，別花時間精讀。
3. **被 `l` 前綴騙到以為檔名沒規律**。恰恰相反，`l` 之後的字根**就是**檔案職責，是 Lua 最好用的一條偵察線索。`rg -l "luaH_"` 找 table 相關、`luaV_` 找 VM 相關——前綴系統一致到可以拿來當索引。
4. **偵察就想讀懂 `luaV_execute`**。它 700 多行、巨集密度極高，偵察階段打開它只會挫敗。偵察只需確認「它在 `lvm.c:1151`、它是主迴圈」，讀懂是 Ch 4 的事。**找到門，不進屋**。
5. **不 clone 光看本章**。你少了親手 `wc`、親手 `rg` 的肌肉記憶，下個目標（SQLite）沒人幫你列命名表時就抓瞎。這門課的技巧是「做」，不是「看別人做」。

## 進階：再往深一層

- **用 `rg` 統計前綴分布**：把命名慣例當量化偵察工具，數各子系統前綴各被呼叫幾次，反推哪裡邏輯最重。這是接 `reading_code` Ch 12「grep/ripgrep 的藝術」。在 `1ab3208` 上真跑：

  ```bash
  $ for p in luaV luaH luaC luaD; do
      printf "%-6s %d\n" "$p" "$(rg -o "\b${p}_\w+" -N *.c | wc -l)"
    done
  luaV   122     # VM（lvm.c）
  luaC   94      # GC（lgc.c）
  luaD   108     # do/call（ldo.c）
  luaH   88      # table（ltable.c）
  ```

  四個核心子系統的呼叫點數量都在 88–122 之間，彼此相當——這印證了偵察的判斷：VM、GC、呼叫、table 是四根同等重要的柱子，沒有哪根可以偷懶不讀。前綴系統一致到可以當量化索引，是 Lua 適合當首個目標的又一個理由。
- **架 clangd 再偵察**：Ch 0 教過 `bear -- make` 產 `compile_commands.json`。有 LSP 後，在 `main` 裡對 `luaL_newstate` 按 go-to-definition，一路跳到 `lstate.c` 的建 state 流程，比 `rg` 追還快。Lua 夠小，純 `rg` 也活得下去，但練 LSP 是為了下個大目標。
- **對照官方架構自述**：Lua 沒有像 SQLite 那樣的架構頁，但 [Lua 5.4 Reference Manual](https://www.lua.org/manual/5.4/) 的「The Application Program Interface」章講清楚了 stack-based C API 的設計意圖。偵察完 code 再回頭讀規格，兩邊對照會發現「原來這個 stack 慣例是刻意的設計而非偶然」。

## 本章重點整理

- 偵察的產出是**地圖 + 刻意不讀清單**，不是理解。理解留給後面的機制章。
- Lua 的 `l` 前綴命名慣例是最強的偵察線索：`l` 之後的字根即檔案職責。據此把 2 萬 6 千行切成 **core / lib / auxlib** 三堆。
- 標準流程：`ls`（看命名）→ `wc -l | sort`（排體積找山頭）→ `rg entry`（釘起點）→ `rg 關鍵struct`（驗分層、找接縫），前四步不到一分鐘。
- Lua 兩個 entry 別混：程序 entry 是 `lua.c` 的 `main`；runtime entry 是 `luaL_newstate` 建 state、`lua_pcall` 驅動、`luaV_execute` 跑 bytecode。
- 本 Part 收斂到的精讀範圍：`lvm.c`（VM）+ `lobject.h`/`lstate.h`/`ltable.c`（值與 table）+ `lgc.c`（GC），刻意跳過前端 `llex`/`lparser`/`lcode`。

## 自我檢核

- [ ] 我 clone 了 v5.4.7 並確認 `git rev-parse --short HEAD` 是 `1ab3208`
- [ ] 我親手跑了 `wc -l *.c | sort -n`，能說出最大的三個 `.c` 檔是哪些、各管什麼
- [ ] 我能解釋 `l` 前綴命名慣例怎麼幫我把檔案分成 core/lib/auxlib
- [ ] 我知道 Lua 的兩個 entry（`main` vs `luaL_newstate`/`luaV_execute`）差在哪
- [ ] 我畫得出（或能複述）那張架構地圖，並說出接下來三章各攻哪個檔案
- [ ] 我能說出至少三個「本 Part 刻意不讀」的檔案，以及為什麼

## 延伸閱讀

- **[Lua 5.4 Reference Manual — §4 The Application Program Interface](https://www.lua.org/manual/5.4/manual.html#4)**（官方）
  - **讀哪裡**：4.1「The Stack」與 4.2「Stack Size」。讀完你會懂本章 `main` 裡那串 `lua_push*` + `lua_pcall` 為什麼要經過 stack。
  - **前提**：會讀 C，看過本章的 `main` 節選。
- **《The Implementation of Lua 5.0》— Ierusalimschy, de Figueiredo, Celes**（[lua.org/doc](https://www.lua.org/doc/jucs05.pdf)）
  - **讀哪裡**：整篇但先看第 2 節（values & types）與第 7 節（register-based VM）。這是 Lua 作者親自寫的實作導覽，雖是 5.0 但核心設計沿用到 5.4，是你偵察後補全局觀的最佳一手資料。
  - **前提**：無，可當本 Part 的伴讀。
- **`reading_code` Ch 5「第一次接觸：60 分鐘偵察」與 Ch 7「建立架構地圖」**（本 repo）
  - **讀哪裡**：本章就是這兩章 SOP 的實戰。回頭對照，把 SOP 的抽象步驟和你剛在 Lua 上做的動作一一對上。
  - **前提**：無，這是本課的方法論母課。

偵察完成，地圖在手。下一章我們進屋——打開 `lvm.c`，讀懂 `luaV_execute` 這台 register-based 虛擬機怎麼一條一條把 bytecode 跑起來。

→ [Ch 4 Lua 的 register-based VM](./04-lua-register-vm.md)
