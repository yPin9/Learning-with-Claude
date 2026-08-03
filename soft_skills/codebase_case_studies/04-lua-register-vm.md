# Ch 4 — Lua 的 register-based VM：讀懂 `luaV_execute`

> **目標**：進屋讀 `lvm.c` 的 `luaV_execute`——Lua bytecode 直譯器的心臟。搞懂 register-based VM 和 stack-based VM 的差別（Lua 是前者，這是它跑得快的關鍵之一），讀懂 dispatch loop 的兩種實作（`switch` vs computed goto），並把一個 opcode 從取指到執行完整走一遍。這是本課三個 VM 的第一個，之後 SQLite 的 VDBE（Ch 9）、CPython 的 eval loop（Ch 23）會回頭跟它對照。

> **目標codebase**：Lua `v5.4.7`（commit `1ab3208`）

## 為什麼需要這個？

上一章偵察時你在 `lvm.c:1151` 釘了一個圖釘：`luaV_execute` 是主迴圈。現在要讀懂它。

為什麼 VM 值得單獨一章？因為**「一個直譯器怎麼執行 bytecode」是全 repo 反覆出現的骨架**。SQLite 的 VDBE 是 bytecode VM、CPython 是 bytecode VM、EVM（以太坊）是 bytecode VM、正則引擎的某些實作也是。它們的骨架都一樣：一個大迴圈，取一條指令，`switch` 到對應處理，改狀態，取下一條。讀懂 Lua 這台最乾淨的，你就有了讀所有 bytecode VM 的模板——這正是這門課「萃取可遷移 pattern」的核心動作。

而 Lua 這台特別值得讀，因為它是**register-based**。大多數教科書 VM（JVM、CPython 5.4 之前的骨架、WASM）是 stack-based。Lua 5.0 起改成 register-based，指令變少、跑得快。這個設計差異你在別處很難讀到這麼乾淨的實作。

## 先建立直覺：register VM vs stack VM

先講清楚兩者差在哪，這是讀 `luaV_execute` 前必須有的心智模型。

考慮 `a + b`（把兩個區域變數相加）。

**stack-based VM**（如 JVM）用一個運算元堆疊，指令不帶運算元位置，靠 push/pop：

```
   LOAD  a      ; push a 到堆疊
   LOAD  b      ; push b
   ADD          ; pop 兩個、相加、push 結果
   STORE c
```

四條指令，每條都在動堆疊頂端。

**register-based VM**（Lua）把函式的區域變數當成一排「暫存器」，指令直接**帶運算元指哪個暫存器**：

```
   ADD  R[c], R[a], R[b]    ; R[c] := R[a] + R[b]，一條搞定
```

一條指令。運算元 `a`、`b`、`c` 是暫存器編號，直接編碼在指令裡。少了 LOAD/STORE 的搬運，指令數少、dispatch 次數少（dispatch 是 VM 的主要成本），所以快。

**關鍵洞察，也是最容易誤解的地方**：Lua 的「register」不是 CPU 暫存器。它就是**函式呼叫在 Lua data stack 上分到的那段 slot**。第 0 號 register 是那段的第一個 slot，第 1 號是第二個，以此類推。所以 Lua 同時是 stack-based（有一條 data stack）**和** register-based（指令用 slot 當暫存器定址）。一開始你會以為「register VM 就沒有 stack 了」——錯，stack 還在，只是指令不透過 push/pop 操作它，而是用索引直接定址。

```
   Lua data stack（一條連續的 TValue 陣列）
   ┌────┬────┬────┬────┬────┬────┬─── ...
   │func│ R0 │ R1 │ R2 │ R3 │ ...│
   └────┴────┴────┴────┴────┴────┴───
         ▲
       base  ← 當前函式的 register 0 從這裡算起
   指令 "ADD 2 0 1" 意思是 base[2] := base[0] + base[1]
```

`luaV_execute` 開頭就把這個 `base` 算出來，之後所有 `R[A]`、`R[B]`、`R[C]` 都是 `base` 加偏移。

## 指令的位元佈局

在讀 dispatch loop 前，先看一條 Lua 指令長什麼樣。每條指令是一個 32-bit 整數（`Instruction`）。格式定義在 `lopcodes.h`（v5.4.7）的註解裡，直接節錄：

```
        3 3 2 2 2 2 2 2 2 2 2 2 1 1 1 1 1 1 1 1 1 1 0 0 0 0 0 0 0 0 0 0
        1 0 9 8 7 6 5 4 3 2 1 0 9 8 7 6 5 4 3 2 1 0 9 8 7 6 5 4 3 2 1 0
iABC          C(8)     |      B(8)     |k|     A(8)      |   Op(7)     |
iABx                Bx(17)               |     A(8)      |   Op(7)     |
iAsBx              sBx (signed)(17)      |     A(8)      |   Op(7)     |
iAx                           Ax(25)                     |   Op(7)     |
isJ                           sJ (signed)(25)            |   Op(7)     |
```

最常見的是 `iABC`：低 7 bit 是 opcode，接著 8 bit 的 A、1 bit 的 k、8 bit 的 B、8 bit 的 C。A/B/C 通常就是**暫存器編號**（0–255）。這也解釋了 Lua 函式的一個硬限制：`maxstacksize` 最多 255 個 register，因為 A 只有 8 bit。

取欄位的巨集在 `lopcodes.h`（v5.4.7）：

```c
#define GET_OPCODE(i)	(cast(OpCode, ((i)>>POS_OP) & MASK1(SIZE_OP,0)))
#define GETARG_A(i)	getarg(i, POS_A, SIZE_A)
#define GETARG_B(i)	check_exp(checkopm(i, iABC), getarg(i, POS_B, SIZE_B))
#define GETARG_C(i)	check_exp(checkopm(i, iABC), getarg(i, POS_C, SIZE_C))
```

`GET_OPCODE(i)` 把最低 7 bit 挖出來當 opcode；`GETARG_A/B/C` 挖出三個運算元。讀 dispatch loop 時你會看到 `RA(i)`、`RB(i)`——它們就是「`base` 加上 `GETARG_A(i)`」，把運算元編號翻成真正的 stack slot 指標。看 `lvm.c`（v5.4.7）開頭的定義：

```c
#define RA(i)	(base+GETARG_A(i))
#define RB(i)	(base+GETARG_B(i))
#define vRB(i)	s2v(RB(i))
#define RC(i)	(base+GETARG_C(i))
```

`RA(i)` = `base + GETARG_A(i)`，一個指向 stack slot 的指標。`vRB(i)` 的 `s2v` 是 stack-slot-to-value（stack 上存的是帶額外資訊的 `StackValue`，`s2v` 取出裡面那個 `TValue`——這種 indirection 是 `reading_code` Ch 23 的典型例子，第一次讀會被騙一下）。

## dispatch loop 的骨架

現在讀主迴圈本體。`luaV_execute` 的開頭（`lvm.c:1151`，v5.4.7）：

```c
void luaV_execute (lua_State *L, CallInfo *ci) {
  LClosure *cl;
  TValue *k;
  StkId base;
  const Instruction *pc;
  int trap;
#if LUA_USE_JUMPTABLE
#include "ljumptab.h"
#endif
 startfunc:
  trap = L->hookmask;
 returning:  /* trap already set */
  cl = ci_func(ci);
  k = cl->p->k;
  pc = ci->u.l.savedpc;
  if (l_unlikely(trap))
    trap = luaG_tracecall(L);
  base = ci->func.p + 1;
  /* main loop of interpreter */
  for (;;) {
    Instruction i;  /* instruction being executed */
    vmfetch();
    ...
```

逐行拆這幾個關鍵局部變數，它們是整個直譯器的暫存狀態：

- `cl`：當前正在執行的 Lua closure（`ci_func(ci)` 從 CallInfo 取出）。
- `k`：常數表（`cl->p->k`）。指令裡編號指常數時（如 `LOADK`）查這張表。`p` 是 `Proto`，一個 Lua 函式編譯後的產物（bytecode + 常數 + 除錯資訊），Ch 5 會細看。
- `pc`：program counter，指向下一條要取的指令。就是「現在跑到哪」。
- `base`：前面講的，當前函式 register 0 的 stack 位置（`ci->func.p + 1`——func 自己佔一個 slot，參數/register 從下一個開始）。
- `trap`：是否要在每條指令觸發 debug hook/line trace。正常執行是 0，這是為了 debugger 支援埋的鉤子，第一次讀可以先無視。

注意兩個 label：`startfunc` 和 `returning`。**這是 Lua 5.4 的一個關鍵設計**：當一個 Lua 函式呼叫另一個 Lua 函式時，**不遞迴呼叫 `luaV_execute`**，而是 `goto startfunc` 在同一個 C stack frame 裡重新初始化跑新函式（等下 OP_CALL 會看到）。這避免了 C stack 隨 Lua 呼叫深度膨脹。一開始你可能以為 Lua 呼叫就是 C 遞迴——讀到這兩個 label 才發現不是，Lua 自己管一條 CallInfo 鏈當呼叫堆疊。

## 底層機制：`vmfetch` 與兩種 dispatch

迴圈每一圈做兩件事：**取指**（fetch）和**分派**（dispatch）。取指是 `vmfetch()` 巨集（`lvm.c`，v5.4.7）：

```c
#define vmfetch()	{ \
  if (l_unlikely(trap)) {  /* stack reallocation or hooks? */ \
    trap = luaG_traceexec(L, pc);  /* handle hooks */ \
    updatebase(ci);  /* correct stack */ \
  } \
  i = *(pc++); \
}
```

核心就一行：`i = *(pc++)`——讀出 `pc` 指的指令、`pc` 前進。前面那坨 `trap` 是 debug hook 的慢路徑，正常情況 `l_unlikely(trap)` 為假，直接跳過。

取到指令 `i` 後要**分派**到對應的處理程式碼。Lua 巧妙地用同一份 source 支援兩種分派方式，靠三個巨集抽象（`lvm.c`，v5.4.7）：

```c
#define vmdispatch(o)	switch(o)
#define vmcase(l)	case l:
#define vmbreak		break
```

這是預設的 **`switch` 分派**：`vmdispatch(GET_OPCODE(i))` 展開成 `switch(GET_OPCODE(i))`，每個 opcode 是一個 `case`。乾淨、可攜、任何 C 編譯器都能編。

但當 `LUA_USE_JUMPTABLE` 開啟時（gcc/clang 上預設開），`luaV_execute` 開頭那行 `#include "ljumptab.h"` 會把這三個巨集**重新定義**成 computed goto 版（`ljumptab.h`，v5.4.7）：

```c
#undef vmdispatch
#undef vmcase
#undef vmbreak

#define vmdispatch(x)     goto *disptab[x];
#define vmcase(l)     L_##l:
#define vmbreak		vmfetch(); vmdispatch(GET_OPCODE(i));

static const void *const disptab[NUM_OPCODES] = {
  ...
  &&L_OP_MOVE,
  &&L_OP_LOADI,
  ...
```

`&&L_OP_MOVE` 是 GNU C 的擴充語法「取 label 的位址」。`disptab` 是一張「opcode 編號 → 對應 code 位址」的表。`goto *disptab[x]` 直接跳到那個位址。

**為什麼 computed goto 更快？** `switch` 版每跑完一條 opcode 要 `break` 回迴圈頂端、再判斷一次 switch，CPU 分支預測器看到的是「同一個間接跳轉」，難預測。computed goto 版把「取下一條 + 跳到它的 handler」直接接在每個 opcode 尾巴（`vmbreak` 展開成 `vmfetch(); goto *disptab[...]`），每個 opcode 有自己的分派點，分支預測命中率高很多。實測能快 10–20%。這是直譯器優化的經典技巧，CPython 3.11 之後也用同樣手法。

**讀碼教訓**：同一份 `luaV_execute` 的 source，因為這三個巨集的重定義，**編出來的機器碼結構完全不同**。如果你只看 `switch(o)` 就以為它一定是 switch，會錯過真相。看到巨集包裝的控制流，一定要去找巨集定義（`reading_code` Ch 22「讀懂巨集與 metaprogramming」）——`vmdispatch`/`vmcase`/`vmbreak` 這組就是活教材。

## 走一遍：OP_ADD 從取指到執行

挑 `OP_ADD` 走完整流程。它在 `lvm.c`（v5.4.7）：

```c
      vmcase(OP_ADD) {
        op_arith(L, l_addi, luai_numadd);
        vmbreak;
      }
```

處理本體是一個巨集 `op_arith`。跟過去（`lvm.c`，v5.4.7）：

```c
#define op_arith(L,iop,fop) {  \
  TValue *v1 = vRB(i);  \
  TValue *v2 = vRC(i);  \
  op_arith_aux(L, v1, v2, iop, fop); }
```

`v1 = vRB(i)`、`v2 = vRC(i)`：把運算元 B、C 對應的兩個 register 值取出來（`vRB` = `s2v(base + GETARG_B(i))`）。然後 `op_arith_aux` 分整數/浮點兩條路：兩邊都整數就走 `iop`（這裡是 `l_addi`，整數加）並把結果寫回 `RA(i)`（register A）；否則試浮點 `fop`（`luai_numadd`）；再否則落到 metamethod（`__add`）的慢路徑。

把 `ADD 2 0 1` 這條指令的完整生命週期串起來：

```
1. vmfetch():   i = *pc++            ← 取出這條 32-bit 指令，pc 前進
2. dispatch:    GET_OPCODE(i)==OP_ADD → switch/goto 到 OP_ADD 的 handler
3. 解碼運算元:  vRB(i) = s2v(base + 0)  ← register 0 的值
                vRC(i) = s2v(base + 1)  ← register 1 的值
4. 執行:        兩者都是整數 → l_addi 相加
5. 寫回:        結果存進 s2v(base + 2)  ← register 2（GETARG_A）
6. vmbreak:     switch 版→break 回迴圈頂; goto 版→直接 fetch 下一條並跳
```

六步，一圈迴圈。VM 就是把這六步重複幾百萬次。你讀懂這一個 opcode，其餘六十幾個 opcode 全是同一個骨架：取指 → 解碼 → 做事 → 寫回 → 下一條。差別只在「做事」那步——`OP_GETTABLE` 去查 table、`OP_CALL` 去呼叫函式、`OP_JMP` 去改 `pc`。

## 再走一個：OP_CALL 與「不遞迴」的呼叫

`OP_CALL` 特別值得看，因為它揭示了前面 `startfunc` label 的用途。看 `lvm.c`（v5.4.7）：

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

關鍵在 `luaD_precall` 的回傳值判斷：

- 若被呼叫的是 **C 函式**，`luaD_precall` 直接把它跑完、回傳 `NULL`，`OP_CALL` 這條就結束，`vmbreak` 跑下一條。
- 若是 **Lua 函式**，`luaD_precall` 建好新的 `CallInfo`（新函式的 stack frame 資訊）並回傳它。此時 `ci = newci; goto startfunc;`——**在同一個 C stack frame 裡，跳回迴圈開頭，用新的 `ci` 重新初始化 `base`/`pc`/`cl`，開始跑被呼叫的 Lua 函式**。

這就是 Lua「Lua 呼叫 Lua 不吃 C stack」的機制：呼叫深度記在 `CallInfo` 鏈（`ci->previous`/`ci->next`）上，不是記在 C 的呼叫堆疊上。`luaD_precall` 的內部（建 CallInfo、擺參數）是 Ch 5 和練習 A 的主角，這裡先知道 OP_CALL 怎麼分岔就好。

## 對比與取捨

| 面向 | stack-based VM | register-based VM（Lua） |
|---|---|---|
| 指令數（`a+b`） | 多（LOAD/LOAD/ADD/STORE） | 少（一條 ADD 帶三個運算元） |
| 每條指令大小 | 小（多半只有 opcode） | 大（要編碼運算元位置） |
| dispatch 次數 | 多 → 慢 | 少 → 快 |
| 編譯器（產 bytecode）複雜度 | 低（push/pop 好生成） | 高（要做 register 配置） |
| 讀 bytecode 的直覺 | 像逆波蘭式，看堆疊 | 像三位址碼，看暫存器 |
| 代表 | JVM、WASM、早期 CPython 骨架 | Lua 5.x、Android Dalvik |

| dispatch 實作 | 可攜性 | 速度 | 讀碼難度 |
|---|---|---|---|
| `switch(opcode)` | 任何 C 編譯器 | 基準 | 好讀，就是個 switch |
| computed goto（`goto *tab[op]`） | 只 GNU C 系（gcc/clang） | 快 10–20% | 巨集重定義騙人，要找 `ljumptab.h` |

## 踩雷集錦

1. **以為 Lua 的 register 是 CPU register**。不是。它是**函式在 data stack 上分到的那段 slot 的索引**。`RA(i)` 展開成 `base + GETARG_A(i)`，一個 stack 指標。誤把它當硬體暫存器，你會完全誤解 `base` 的意義。
2. **以為看到 `switch(o)` 就一定編成 switch**。`vmdispatch(o)` 預設是 `switch`，但 `ljumptab.h` 在 gcc/clang 上把它 `#undef` 重定義成 `goto *disptab[o]`。同一份 source 兩種機器碼。看到巨集包的控制流，先找巨集定義再下結論。
3. **以為 Lua 函式呼叫是 C 遞迴呼叫 `luaV_execute`**。錯。Lua 呼叫 Lua 時走 `goto startfunc`，在同一個 C frame 換 `ci` 重跑迴圈。呼叫堆疊是 `CallInfo` 鏈，不是 C stack。這是 Lua 5.4 的核心設計，也是它不會因 Lua 深遞迴爆掉 C stack 的原因（Lua 深遞迴會 `stack overflow` 是它自己檢查 CallInfo 數量，不是 C 段錯誤）。
4. **被 `s2v`、`vRB` 這層 indirection 卡住**。stack 上存的是 `StackValue`（帶額外欄位），`s2v` 取出裡面的 `TValue`。第一次讀會覺得「為什麼不直接用」——因為 Lua 的 to-be-closed 變數等機制需要在 stack slot 上掛額外資訊。先接受這層轉換，Ch 5 讀 `TValue` 時會更清楚。
5. **想一次讀懂全部六十幾個 opcode**。不需要。讀懂 `OP_ADD`（算術）、`OP_GETTABLE`（table 存取）、`OP_CALL`（呼叫）、`OP_JMP`（跳轉）四個代表，就掌握了所有 opcode 的骨架。其餘的用到再查 `lopcodes.h` 的註解（每個 opcode 旁邊都有 `R[A] := R[B] + R[C]` 這種語意說明）。

## 進階：再往深一層

- **用 `luac -l` 看真 bytecode**：`echo 'local a,b=1,2; return a+b' | ./luac -l -` 會反組譯出 bytecode，你能親眼看到 `ADD` 指令的 A/B/C 是哪幾個 register。把它跟 `luaV_execute` 的 handler 對照，「指令→執行」的迴路就完全打通了。（`luac` 是 Lua 的編譯器，跟 `lua` 一起 build 出來。）
- **gdb 下中斷點看真 dispatch**：`gdb ./lua` → `break luaV_execute` → `run script.lua`，然後在迴圈裡 `print GET_OPCODE(i)` 看每一圈跑哪個 opcode。這是 `reading_code` Ch 18 debugger-driven reading 的實戰，練習 A 會帶你做。
- **對照 CPython 的 eval loop**：CPython 的 `_PyEval_EvalFrameDefault`（`Python/ceval.c`）也是 computed goto 的巨大 switch，但它是 stack-based。讀完 Lua 這台再去看 CPython Ch 23，你會直接認出骨架、只需注意「它用運算元堆疊而非 register」的差異。這就是 pattern 遷移的價值。
- **register 配置在哪做的**：Lua 產 bytecode 時要決定每個變數用哪個 register，這在 `lcode.c`（我們刻意不精讀的前端）。若好奇「`ADD 2 0 1` 的 2/0/1 怎麼決定的」，`lcode.c` 的 `luaK_*` 系列是答案，但那是編譯器的活，跟本課主線（runtime）分開。

## 本章重點整理

- Lua 是 **register-based VM**：指令直接帶運算元指 register 編號，比 stack-based 少很多 LOAD/STORE 和 dispatch，跑得快。
- Lua 的「register」不是 CPU 暫存器，是**函式在 data stack 上分到的 slot**；`base` 是 register 0 的位置，`RA(i)` = `base + GETARG_A(i)`。
- 指令是 32-bit 整數，最常見的 `iABC` 格式：7-bit opcode + A/B/C 三個 8-bit 運算元（所以每函式最多 255 register）。
- dispatch loop 用 `vmdispatch`/`vmcase`/`vmbreak` 三巨集抽象；預設 `switch`，gcc/clang 上 `ljumptab.h` 重定義成 computed goto（快 10–20%）。**同一份 source，兩種機器碼**。
- 每個 opcode 都是同一骨架：**vmfetch → dispatch → 解碼運算元 → 做事 → 寫回 RA → 下一條**。讀懂 `OP_ADD` 就掌握全部。
- Lua 呼叫 Lua **不遞迴 C**：`OP_CALL` 建新 `CallInfo` 後 `goto startfunc` 在同一 C frame 重跑迴圈；呼叫堆疊是 `CallInfo` 鏈。

## 自我檢核

- [ ] 我能用 `a+b` 為例，講清楚 register VM 和 stack VM 的指令差在哪、為什麼前者 dispatch 較少
- [ ] 我知道 Lua 的 register 其實是 data stack 上的 slot，能解釋 `RA(i)` = `base + GETARG_A(i)`
- [ ] 我能說出 `vmdispatch`/`vmcase`/`vmbreak` 三巨集在 `switch` 模式和 computed goto 模式各展開成什麼
- [ ] 我能把 `OP_ADD` 從 vmfetch 到寫回 RA 的六步複述一遍
- [ ] 我懂 `OP_CALL` 遇到 Lua 函式時 `goto startfunc` 的意義，以及為什麼 Lua 呼叫不吃 C stack
- [ ] 我試過用 `luac -l` 反組譯一小段 Lua，對照 opcode

## 延伸閱讀

- **《The Implementation of Lua 5.0》— §7 The Virtual Machine**（[lua.org/doc/jucs05.pdf](https://www.lua.org/doc/jucs05.pdf)）
  - **讀哪裡**：第 7 節，作者親自解釋為什麼從 stack-based（Lua 4）改成 register-based（Lua 5），並比較指令數。雖是 5.0，register VM 的核心思想 5.4 完全沿用。
  - **前提**：讀過本章，知道 register 是什麼。
- **[A Look at the Lua 5.4 Bytecode](https://the-ravi-programming-language.readthedocs.io/en/latest/lua_bytecode_reference.html)**（Ravi/社群整理）
  - **讀哪裡**：opcode 逐條表，對照 `lopcodes.h` 的註解讀。查某個 opcode 語意時很好用。
  - **前提**：知道 iABC 格式。
- **[Computed goto for efficient dispatch tables — Eli Bendersky](https://eli.thegreenplace.net/2012/07/12/computed-goto-for-efficient-dispatch-tables)**（部落格）
  - **讀哪裡**：整篇。用最小例子講清楚 computed goto 為什麼比 switch 快、分支預測的角色。讀完再回看 `ljumptab.h` 就懂了。
  - **前提**：會 C，知道 GNU C 的 `&&label` 擴充。
- **`reading_code` Ch 22「讀懂巨集與 metaprogramming」**（本 repo）
  - **讀哪裡**：本章 `vmdispatch`/`vmcase`/`vmbreak` 的重定義就是這章講的「巨集改變控制流、騙過第一眼」的活例子。回頭對照。
  - **前提**：無。

VM 這台引擎讀懂了，但它操作的「值」到底長什麼樣？`R[A]`、`R[B]` 裡裝的 `TValue` 怎麼同時能是整數、字串、table？下一章拆值的表示，並讀 Lua 唯一的複合結構——array + hash 混合的 table。

→ [Ch 5 Lua 的值表示與 table](./05-lua-values-and-tables.md)
