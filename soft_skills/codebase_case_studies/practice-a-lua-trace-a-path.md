# 練習 A — 限時攻堅：追一條 Lua 函式呼叫的完整執行路徑

> **目標**：把 Part 1 五章的機制在**一條真實 call chain** 上串起來。任務是追一個 Lua 函式呼叫 `f(1, 2)` 從 VM 的 `OP_CALL` 指令，一路到 `luaD_precall` 建新 `CallInfo`、擺參數，再回到 `luaV_execute` 開始執行被呼叫函式的完整流程。這是限時攻堅——計時、自己撞牆、卡住才看提示。能 build 能 gdb 的話，親手下中斷點驗證每一步。

> **目標codebase**：Lua `v5.4.7`（commit `1ab3208`）

## 為什麼做這個練習？

讀懂五章的機制，和能**在真實 code 上追出一條路徑**，是兩回事。前者是認得零件，後者是看零件怎麼裝在一起動起來。這個練習逼你把 Ch 4（VM dispatch）、Ch 5（CallInfo/stack 佈局）、Ch 3 抽驗過的 `luaD_precall` 串成一條連續的因果鏈——這正是 `reading_code` Part 2「追一條路徑」SOP 的實戰，也是找漏洞、debug、理解陌生系統的核心動作。

而「函式呼叫」是最值得追的路徑，因為它同時碰到 VM、stack、CallInfo 鏈三個核心，而且揭示 Ch 4 埋的伏筆——Lua 呼叫 Lua 為什麼**不遞迴呼叫 `luaV_execute`**（`goto startfunc`），這條路徑追完你就親眼看到了。

## 任務規格

**你要追的具體路徑**：以下這段 Lua

```lua
local function f(a, b)
  return a + b
end
print(f(1, 2))
```

當執行到 `f(1, 2)` 這個呼叫時，追出從 VM 執行 `OP_CALL` 指令開始的完整鏈，回答：

1. `OP_CALL` 這個 opcode 的 handler 在哪個檔案哪個函式裡？它做的第一件事是什麼？
2. `OP_CALL` 怎麼判斷被呼叫的是 Lua 函式還是 C 函式？分別走哪條路？
3. 對 Lua 函式，`luaD_precall` 做了哪幾件事來準備新的一層呼叫？（至少講出：建 CallInfo、設 savedpc、擺參數三件）
4. 新的 `CallInfo` 從哪來？它的 `func` 欄位指向哪、`base`（register 0）怎麼算出來？
5. 準備好之後，控制流怎麼回到 `luaV_execute` 開始執行 `f` 的 body？為什麼這裡**沒有**遞迴呼叫 `luaV_execute`（大部分情況下）？

**時限**：45 分鐘。純讀 code 就好，不強制 build。能 build + gdb 的話，用最後 10 分鐘下中斷點驗證你追的鏈是不是真的。

**起點**：`lvm.c` 的 `luaV_execute`，搜 `OP_CALL`。

## 開始前：擺好工具

```bash
$ cd /tmp/rd_lua && git rev-parse --short HEAD   # 確認 1ab3208
$ rg -n "vmcase\(OP_CALL\)" lvm.c                # 找 OP_CALL handler
```

計時開始。先自己追，卡住再往下看提示。

---

## 如果你卡住了（五條方向提示，不直接給答案）

<details>
<summary>提示 1：找不到 OP_CALL 的 handler 在哪</summary>

`OP_CALL` 是個 opcode，它的處理在 `luaV_execute` 的大 dispatch loop 裡（Ch 4）。用 `rg -n "vmcase\(OP_CALL\)" lvm.c` 找到那個 `vmcase(OP_CALL) { ... }` 區塊。注意它是 `vmcase` 巨集包的，不是普通 `case`——Ch 4 講過這組巨集。
</details>

<details>
<summary>提示 2：OP_CALL 裡那個回傳值判斷是關鍵</summary>

看 `OP_CALL` handler 裡 `luaD_precall(...)` 的**回傳值怎麼被用**。它回傳 `NULL` 和回傳一個 `CallInfo*` 走的是完全不同的兩條路。這個分岔就是「C 函式 vs Lua 函式」的分界。跟著非 `NULL` 那條走（Lua 函式）。
</details>

<details>
<summary>提示 3：luaD_precall 在哪、它怎麼分型別</summary>

`luaD_precall` 在 `ldo.c`（Ch 3 偵察時定位過、抽驗過它的 backtrace 是真的）。`rg -n "luaD_precall" ldo.c`。它開頭是一個 `switch (ttypetag(s2v(func)))`——按被呼叫物件的型別 tag（Ch 5 的 `ttypetag`）分流。找 `case LUA_VLCL`（Lua closure）那一支。
</details>

<details>
<summary>提示 4：CallInfo 從哪來、base 怎麼算</summary>

`LUA_VLCL` 那一支裡有 `prepCallInfo(...)`。跟進去看它怎麼取一個新 CallInfo（`next_ci`）、設哪些欄位。`base`（register 0）不是在 `luaD_precall` 算的，是回到 `luaV_execute` 後用 `ci->func.p + 1` 算的——回頭看 Ch 4/Ch 5 的 `base = ci->func.p + 1`。`func` 欄位指向 stack 上「被呼叫函式那個 slot」。
</details>

<details>
<summary>提示 5：控制流怎麼回到 luaV_execute 執行 f 的 body</summary>

回到 `OP_CALL` handler 的非 `NULL` 分支，看它做了什麼。關鍵是兩行：`ci = newci;` 和一個 `goto`。那個 `goto` 跳到哪個 label？（Ch 4 講過 `startfunc`/`returning` 兩個 label 的用途。）想清楚「為什麼是 goto 而不是 `luaV_execute(L, newci)` 遞迴呼叫」——這是 Lua 呼叫不吃 C stack 的核心。
</details>

---

## 參考解答（追完再看）

### 第 0 步：這段 Lua 編成什麼 bytecode

`f(1, 2)` 在 bytecode 層是「把 `f`、`1`、`2` 放到連續的 register，然後一條 `OP_CALL`」。`print(f(1,2))` 大致長這樣（概念，實際可用 `./luac -l` 反組譯確認）：

```
   ...
   GETTABUP  print          ; 取 print 到某 register
   ...  把 f、1、2 載入連續 register  ...
   CALL      f 3 2          ; 呼叫 f，2 個參數，1 個回傳（A=f的slot, B=3, C=2）
   CALL      print ...      ; 再呼叫 print
```

我們追的是那條 `CALL f`。

### 第 1 步：OP_CALL handler（`lvm.c`，v5.4.7）

```c
      vmcase(OP_CALL) {
        StkId ra = RA(i);
        CallInfo *newci;
        int b = GETARG_B(i);
        int nresults = GETARG_C(i) - 1;
        if (b != 0)  /* fixed number of arguments? */
          L->top.p = ra + b;  /* top signals number of arguments */
        /* else previous instruction set top */
        savepc(L);  /* in case of errors */
        if ((newci = luaD_precall(L, ra, nresults)) == NULL)
          updatetrap(ci);  /* C call; nothing else to be done */
        else {  /* Lua call: run function in this same C frame */
          ci = newci;
          goto startfunc;
        }
        vmbreak;
      }
```

逐步（回答問題 1、2）：

- `ra = RA(i)`：`RA(i)` = `base + GETARG_A(i)`（Ch 4），指向 stack 上放著被呼叫函式 `f` 的那個 slot。**第一件事就是解出「要呼叫的函式在 stack 哪」**。
- `b = GETARG_B(i)`：參數個數 +1 的編碼；`L->top.p = ra + b` 把 stack top 設到「函式 + 參數」的末端，這樣被呼叫方知道有幾個參數。
- `savepc(L)`：把當前 pc 存回 `ci->u.l.savedpc`，萬一 precall 出錯能報對行號（Ch 5 的 `savedpc`）。
- **分岔**：`luaD_precall(L, ra, nresults)` 回傳 `NULL` → 是 C 函式，precall 已經跑完它，`OP_CALL` 這條結束；回傳非 `NULL` → 是 Lua 函式，`newci` 是它的新 CallInfo，走 `else` 分支。

### 第 2 步：luaD_precall 對 Lua 函式做什麼（`ldo.c`，v5.4.7）

```c
CallInfo *luaD_precall (lua_State *L, StkId func, int nresults) {
 retry:
  switch (ttypetag(s2v(func))) {
    ...
    case LUA_VLCL: {  /* Lua function */
      CallInfo *ci;
      Proto *p = clLvalue(s2v(func))->p;
      int narg = cast_int(L->top.p - func) - 1;  /* number of real arguments */
      int nfixparams = p->numparams;
      int fsize = p->maxstacksize;  /* frame size */
      checkstackGCp(L, fsize, func);
      L->ci = ci = prepCallInfo(L, func, nresults, 0, func + 1 + fsize);
      ci->u.l.savedpc = p->code;  /* starting point */
      for (; narg < nfixparams; narg++)
        setnilvalue(s2v(L->top.p++));  /* complete missing arguments */
      lua_assert(ci->top.p <= L->stack_last.p);
      return ci;
    }
    ...
  }
}
```

回答問題 3、4，這一支做了四件事：

1. **取出被呼叫函式的 Proto**：`p = clLvalue(s2v(func))->p`。`Proto` 是 `f` 編譯後的產物（bytecode `p->code`、參數數 `p->numparams`、frame 大小 `p->maxstacksize`，Ch 4/Ch 5 見過）。
2. **算參數與 frame**：`narg` = 實際傳進來幾個參數；`nfixparams` = `f` 宣告了幾個固定參數（`a`, `b` → 2）；`fsize` = `f` 需要多少 register。
3. **建新 CallInfo**：`prepCallInfo(L, func, nresults, 0, func + 1 + fsize)`。跟進 `prepCallInfo`（`ldo.c`，v5.4.7）：

   ```c
   l_sinline CallInfo *prepCallInfo (lua_State *L, StkId func, int nret,
                                                   int mask, StkId top) {
     CallInfo *ci = L->ci = next_ci(L);  /* new frame */
     ci->func.p = func;
     ci->nresults = nret;
     ci->callstatus = mask;
     ci->top.p = top;
     return ci;
   }
   ```

   `next_ci(L)` 從 CallInfo 鏈取下一個（Ch 5：CallInfo 是 `previous`/`next` 雙向鏈，這就是 Lua 的呼叫堆疊，通常直接用鏈上預配好的下一個，不必 malloc）。`ci->func.p = func` —— **新 CallInfo 的 `func` 指向 stack 上 `f` 那個 slot**（回答問題 4 前半）。
4. **設起始 pc + 補參數**：`ci->u.l.savedpc = p->code` —— 新這層的 pc 指向 `f` 的 bytecode 開頭（`return a+b` 的第一條指令）。`for` 迴圈把宣告了但沒傳的參數補 nil（這裡 2 個參數都傳了，不補）。

回答問題 4 後半：`base`（register 0）**不在這裡算**。`prepCallInfo` 設好 `ci->func.p`，回到 `luaV_execute` 後才 `base = ci->func.p + 1`（func 自己佔一格，register 0 從下一格起）。

### 第 3 步：回到 luaV_execute 執行 f（回答問題 5）

回到 `OP_CALL` 的 `else` 分支：

```c
        else {  /* Lua call: run function in this same C frame */
          ci = newci;
          goto startfunc;
        }
```

`ci = newci`（切換到 `f` 的 CallInfo），`goto startfunc`（跳回 `luaV_execute` 開頭的 label）。回看 Ch 4 的 `luaV_execute` 開頭：

```c
 startfunc:
  trap = L->hookmask;
 returning:
  cl = ci_func(ci);          /* 現在 ci 是 f 的，取出 f 的 closure */
  k = cl->p->k;              /* f 的常數表 */
  pc = ci->u.l.savedpc;      /* 剛設的 p->code，f 的第一條指令 */
  ...
  base = ci->func.p + 1;     /* f 的 register 0 */
  for (;;) { ... }           /* 開始跑 f 的 body */
```

**關鍵洞察（問題 5 的答案）**：這裡是 `goto startfunc`，**不是** `luaV_execute(L, newci)`。同一個 C 函式的 stack frame 被複用，只是把局部變數 `ci`/`base`/`pc`/`cl` 換成 `f` 的，重新進入迴圈。所以：

- Lua 呼叫 Lua **不增加 C 呼叫堆疊深度**。深度記在 `CallInfo` 鏈上（`ci->previous`），不是 C stack。
- Lua 深遞迴不會 C 段錯誤，而是 Lua 自己檢查 CallInfo 數量超限時報 `stack overflow`。
- `f` 執行完 `return a+b` 遇到 `OP_RETURN` 時，會 `ci = ci->previous`、`goto returning`，回到呼叫者那層繼續——同樣不 pop C stack。

**完整鏈收束**：

```
   luaV_execute（呼叫者那層）
     └─ OP_CALL handler
          ├─ ra = RA(i)              找到 f 在 stack 的位置
          ├─ L->top = ra + b         標記參數個數
          └─ luaD_precall(L, ra, nresults)   ── ldo.c
               └─ case LUA_VLCL:
                    ├─ p = f 的 Proto
                    ├─ prepCallInfo → next_ci 取新 CallInfo
                    │    ci->func = f 的 slot
                    ├─ ci->u.l.savedpc = p->code   f 的第一條指令
                    └─ 補缺參數為 nil；return ci
          ← 回到 OP_CALL：ci = newci; goto startfunc
     └─ startfunc:（同一 C frame，換成 f 的 ci/base/pc）
          for(;;) { ... 執行 f 的 body ... }
```

### 用 gdb 驗證（能 build 的話）

Ch 0 build 好 `lua`（記得 `-g -O0` 才好追）。下中斷點在 `luaD_precall`，跑我們的腳本：

```bash
$ cd /tmp/rd_lua
$ cat > /tmp/call.lua <<'EOF'
local function f(a, b)
  return a + b
end
print(f(1, 2))
EOF
$ gdb -q ./lua
(gdb) break ldo.c:604      # LUA_VLCL 支裡 prepCallInfo 那行
(gdb) run /tmp/call.lua
```

第一次命中是 openlibs 期間別的呼叫；`continue` 到我們的 `f`。命中後 `bt` 得到的**真實 backtrace**（在 WSL2、gcc、`1ab3208` 跑出來的）：

```
#0  luaD_precall (L=0x5555555b02a8, func=0x5555555b0990, nresults=-1) at ldo.c:604
#1  0x00005555555820d1 in luaV_execute (L=0x5555555b02a8, ci=0x5555555b1af0) at lvm.c:1682
#2  0x0000555555565167 in ccall (L=..., func=0x5555555b0950, nResults=-1, inc=65537) at ldo.c:637
#3  0x00005555555651e4 in luaD_callnoyield (L=..., func=0x5555555b0950, nResults=-1) at ldo.c:655
#4  0x000055555556001e in f_call (L=..., ud=0x7fffffffe010) at lapi.c:1038
#5  0x0000555555563aff in luaD_rawrunprotected (L=..., f=<f_call>, ud=...) at ldo.c:144
#6  0x0000555555565af9 in luaD_pcall (L=..., func=<f_call>, ...) at ldo.c:957
#7  0x00005555555600fd in lua_pcallk (L=..., nargs=0, nresults=-1, ...) at lapi.c:1064
#8  0x000055555555c0b6 in docall (L=..., narg=0, nres=-1) at lua.c:161
#9  0x000055555555c53e in handle_script (L=..., argv=...) at lua.c:265
#10 0x000055555555d257 in pmain (L=...) at lua.c:654
...
```

**看第 #1 框**：`luaV_execute (...) at lvm.c:1682`——這正是 `OP_CALL` handler 裡呼叫 `luaD_precall` 那一行（去 `sed -n '1678,1685p' lvm.c` 對照，就是 `if ((newci = luaD_precall(L, ra, nresults)) == NULL)`）。**你追的「`luaV_execute` 的 OP_CALL → `luaD_precall`」這條鏈，backtrace 親自確認了。**

再驗參數，在中斷點處 `print`：

```
(gdb) p p->numparams
$1 = 2 '\002'
(gdb) p fsize
$2 = 3
(gdb) p narg
$3 = 2
```

`p->numparams=2`（`f` 宣告了 `a`,`b` 兩個參數）、`narg=2`（真的傳了 `1`,`2` 兩個）、`fsize=3`（`f` 的 frame 需要 3 個 register）。數字全對得上你追的路徑——這就是 debugger-driven reading（`reading_code` Ch 18）：**不猜，讓 gdb 告訴你真值**。

## 測試/驗證方式

- **不 build 的驗證**：把你追的 call chain 寫下來（像上面那張圖），每一步標出「哪個檔案:哪個函式」，然後回去 `rg`/`sed -n` 逐一核對函式名和它做的事對不對。追錯的地方，通常是你在某個回傳值/分岔上猜錯了走向。
- **build + gdb 的驗證**：如上，`break luaD_precall`、`bt` 看 backtrace 第 #1 框是不是 `luaV_execute`，`print p->numparams` 看參數數對不對。backtrace 對上、參數對上，你追的鏈就是真的。
- **反組譯驗證**：`echo 'local function f(a,b) return a+b end print(f(1,2))' | ./luac -l -` 看真 bytecode 裡的 `CALL` 指令，A/B/C 是哪幾個 register，和你第 0 步的猜測對照。

## 延伸挑戰

1. **追 return 路徑**：`f` 執行完 `return a+b` 的 `OP_RETURN`（或 `OP_RETURN1`）怎麼把結果放回呼叫者、`ci = ci->previous` 怎麼退一層、`goto returning` 怎麼回到呼叫者的迴圈。這是本練習的鏡像，追完你就懂了 Lua 呼叫的完整往返。
2. **追 C 函式那條路**：`print(f(1,2))` 裡 `print` 是 C 函式。追 `OP_CALL` → `luaD_precall` 的 `LUA_VCCL`/`LUA_VLCF` 支 → `precallC` → 直接跑完回傳 `NULL`。對照「Lua 函式建 CallInfo 後 goto，C 函式當場跑完」的差異。
3. **追 `t[k]=v` 而非函式呼叫**：改追 `OP_SETTABLE`（Ch 5 引過）→ `luaV_finishset` → `luaH_finishset`/`luaH_newkey`，看寫入 table 怎麼觸發 `luaC_barrierback`（Ch 6 的 write barrier），把 Ch 5 的 table 和 Ch 6 的 GC 在一條路徑上串起來。
4. **gdb 追 dispatch loop**：`break luaV_execute`，在 `for(;;)` 裡設 watch 或反覆 `next`，`print GET_OPCODE(i)` 看每一圈跑哪個 opcode，親眼看 `f(1,2)` 從 `LOADK`/`CALL` 一路跑到 `RETURN`。

## 自我檢核

- [ ] 我能不看解答，說出 `OP_CALL` 在 `luaV_execute` 裡、handler 第一件事是 `RA(i)` 找函式位置
- [ ] 我知道 `luaD_precall` 的回傳值（`NULL` vs `CallInfo*`）就是 C 函式 vs Lua 函式的分岔
- [ ] 我能講出 `luaD_precall` 對 Lua 函式做的三件事：建 CallInfo、設 `savedpc=p->code`、補缺參數
- [ ] 我理解 `base = ci->func.p + 1` 是回到 `luaV_execute` 後才算、`func` 指向 stack 上函式那格
- [ ] 我能解釋 `goto startfunc` 為什麼不是遞迴呼叫 `luaV_execute`，以及這對 C stack 深度的意義
- [ ] （能 build 的話）我 gdb `break luaD_precall`、`bt` 看到第 #1 框是 `luaV_execute`、`print` 的參數數對得上

## 延伸閱讀

- **`reading_code` Ch 9「控制流與 call graph」與 Ch 18「debugger-driven reading」**（本 repo）
  - **讀哪裡**：Ch 9 教怎麼系統化追一條 call chain（本練習的方法論），Ch 18 教 debugger-driven reading（本練習 gdb 驗證那段的母課）。回頭對照，把追路徑的通法內化。
  - **前提**：做過本練習。
- **Ch 4「register-based VM」與 Ch 5「值表示與 table」**（本課）
  - **讀哪裡**：追不順時回去看 Ch 4 的 `OP_CALL`/`startfunc` 段、Ch 5 的 `CallInfo`/`base` 段。本練習就是這兩章在一條路徑上的合流。
  - **前提**：無。
- **《The Implementation of Lua 5.0》— §6 (Closures) 與 §7 (VM)**（[lua.org/doc/jucs05.pdf](https://www.lua.org/doc/jucs05.pdf)）
  - **讀哪裡**：§7 講 call 的實作與 stack 佈局。作者一手解釋，補全你追出的路徑背後的設計意圖。
  - **前提**：做過本練習。

Part 1 完成。你在最乾淨的語言 runtime 上跑完了偵察→機制深挖→萃取 pattern→限時攻堅的完整循環。下一個目標把難度加一級：SQLite——同樣有一台 bytecode VM（VDBE，你剛存的 dispatch loop 卡片第一次被觸發），但多了 B-tree 儲存引擎和 pager 分層。

→ [Ch 8 SQLite 偵察：分層架構](./08-sqlite-recon.md)
