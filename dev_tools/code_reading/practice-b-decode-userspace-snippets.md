# 練習 B — 解碼 user-space 天書片段（Lua、Redis）

> **目標**：把 Ch 8–14 的 C 語言層技巧（控制流、位元運算、指標、struct/union、所有權、前置處理器、巨集元程式）用在 Lua 5.4.9 與 Redis 7.2.17 裡最難讀的九段真實程式碼上。完成後，你能對任何一段「每個字都認得、整行看不懂」的 C 程式碼，系統化地把它拆成看得懂的步驟，並設計一個小實驗驗證你的解讀。
> **鴻溝**：本練習主要練**慣用法鴻溝**，第 1、7 題附帶**間接鴻溝**（巨集重新定義、指標裡藏 tag），第 8 題附帶**領域鴻溝**（漸進式 rehash 為什麼存在）。

> **環境**：WSL Ubuntu 22.04、gcc 11.4。Lua 5.4.9 以 `make MYCFLAGS="-std=c99 -DLUA_USE_LINUX -g" MYLIBS="-ldl" LOCAL=` build 在 `~/code_reading/scratch/pb/lua`；Redis 7.2.17 以 `make OPTIMIZATION=-O0` build 在 `~/code_reading/scratch/pb/redis`（jemalloc 5.3.0）。實驗程式放 `~/code_reading/scratch/pb/exp`。所有輸出都是實際執行結果。

## 背景與動機

Part 2 的每一章都在教一種「認字」的能力。真實世界的程式碼不會一次只用一種：Lua 的一行 `vmcase(OP_ADD) { op_arith(L, l_addi, luai_numadd); vmbreak; }` 同時用了巨集重新定義（Ch 13）、運算子當參數（Ch 14）、tagged union（Ch 11）、位元欄位解碼（Ch 9）、以及一個不看 compiler 就猜不到的 `pc++`。這個練習模擬你接手一個陌生專案、被指派去修一個 bug 時的真實處境：bug 就在這種程式碼裡，你得先讀懂它。

九題的選擇標準是「第一次看到時多數人會卡住」。每一題都是原封不動從原始碼複製的，標了檔案與行號，你可以在自己的 tree 上打開對照。

## 任務規格

對每一題，產出一份解碼筆記，包含：

| 項目 | 要求 |
|---|---|
| **逐層展開** | 把每個巨集展開到你能用普通 C 理解的程度。可以手寫，但至少一個巨集要用 `gcc -E` 驗證 |
| **一句話摘要** | 用一句不含任何巨集名的白話文說明這段程式碼做什麼 |
| **技巧對照** | 標出用到了 Ch 8–14 的哪些技巧（例如「Ch 11 tagged union」「Ch 14 `##`」） |
| **回答題目的問題** | 每題有 2–3 個具體問題 |
| **驗證方式** | 說明你會怎麼用實驗證明你的解讀是對的；第 1、2、6、7、8、9 題要實際跑 |

禁止事項：不要先打開 `<details>`。不要把整段貼給 LLM 要答案（Ch 53 會專門討論 LLM 讀碼的陷阱；在這裡用它等於跳過練習）。可以查原始碼的任何其他地方、可以 `gcc -E`、可以寫程式。

## 如果你卡住了

1. **先找定義，再猜意義**。看到不認得的名字，`rg -n "define NAME\b" .` 或 `rg -n "\bNAME\b" *.h`。在 heredoc 或 script 裡用 `rg` 記得給路徑參數 `.`，否則它會去讀 stdin（Ch 14 的提醒）。
2. **巨集展開不出來時，問自己它有沒有被 `#undef` 再重新定義過**。同一個名字在同一個檔案裡可以有兩種意思（第 1 題）。
3. **看到 `& 0x..`、`>> n`、`| 128` 就拿紙筆畫位元**。把常數寫成二進位，標出每個 bit 的意義（Ch 9 的方法）。
4. **看到奇怪的 `pc++` 或 `p--`，問「它下一步要讀的是什麼」**。指標的移動方向常常就是資料格式的答案。
5. **讀不懂就寫一個最小程式把它跑起來**。把巨集複製出來、或直接連結 build 好的 `.a`/`.o`，印出中間值。

## 實作步驟建議

### Step 1：建環境
照「環境」一節 build Lua 和 Redis。確認 `./lua -v` 與 `./redis-server --version` 能跑。

### Step 2：先做 Lua 三題（第 1–3 題）
它們共用同一個主迴圈，第 2 題的解碼器可以拿來驗證第 3 題。

### Step 3：做資料表示兩題（第 4–5 題）
需要 Ch 11 的 `TValue` 知識。

### Step 4：做 Redis 四題（第 6–9 題）
第 8、9 題需要啟動一個 `redis-server`，記得加 `--port` 避免和系統上其他 Redis 衝突，用完 `shutdown nosave`。

### Step 5：對答案並寫反思
對照每題的解答，記下「我卡在哪一道鴻溝」。九題做完，你應該看得出自己最弱的是哪一類。

---

## 第 1 題：Lua 主迴圈的分派巨集

```c
/* lua-5.4.9/lvm.c:1145 */
/* fetch an instruction and prepare its execution */
#define vmfetch()	{ \
  if (l_unlikely(trap)) {  /* stack reallocation or hooks? */ \
    trap = luaG_traceexec(L, pc);  /* handle hooks */ \
    updatebase(ci);  /* correct stack */ \
  } \
  i = *(pc++); \
}

#define vmdispatch(o)	switch(o)
#define vmcase(l)	case l:
#define vmbreak		break


void luaV_execute (lua_State *L, CallInfo *ci) {
  ...
#if LUA_USE_JUMPTABLE
#include "ljumptab.h"
#endif
  ...
  for (;;) {
    Instruction i;  /* instruction being executed */
    vmfetch();
    ...
    vmdispatch (GET_OPCODE(i)) {
      vmcase(OP_MOVE) {
        StkId ra = RA(i);
        setobjs2s(L, ra, RB(i));
        vmbreak;
      }
```

```c
/* lua-5.4.9/ljumptab.h:9 */
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
```

**問題**：

1. 在預設的 gcc build 下，`vmcase(OP_MOVE) { ... vmbreak; }` 最後變成什麼 C 程式碼？`vmbreak` 之後控制流去了哪裡？
2. 為什麼 `#include "ljumptab.h"` 寫在**函式本體裡面**，而不是檔案開頭？
3. `&&L_OP_MOVE` 是什麼語法？兩種模式（switch / jump table）的差異，對 CPU 有什麼影響？

**提示**：`lvm.c:38` 決定 `LUA_USE_JUMPTABLE` 的預設值。用 `gcc -E -P` 加與不加 `-DLUA_USE_JUMPTABLE=0` 比較。

<details>
<summary>解答</summary>

**逐層展開**

`lvm.c:38–44`：

```c
#if !defined(LUA_USE_JUMPTABLE)
#if defined(__GNUC__)
#define LUA_USE_JUMPTABLE	1
#else
#define LUA_USE_JUMPTABLE	0
#endif
#endif
```

gcc 定義了 `__GNUC__`，所以預設是 1，`ljumptab.h` 被 include 進 `luaV_execute` 裡。它先 `#undef` 掉 `lvm.c` 剛定義的三個巨集，再換成 jump table 版本。**同一個名字 `vmcase`，在 `lvm.c:1155` 和 `ljumptab.h:15` 有兩個定義，生效的是後者**——如果你只用編輯器「跳到定義」，很可能停在前者，以為它是 `case l:`。

實際展開（`scratch/pb/lua` 下執行，輸出用 `tr -s` 壓縮空白）：

```
$ gcc -std=c99 -DLUA_USE_LINUX -E -P lvm.c | grep -A6 "L_OP_MOVE: {"
 L_OP_MOVE: {
 StkId ra = (base+(((int)((((i)>>((0 + 7))) & ((~((~(Instruction)0)<<(8)))<<(0)))))));
 { TValue *io1=((&(ra)->val)); const TValue *io2=((&((base+((((int)((((i)>>((((0 + 7) + 8) + 1))) & ((~((~(Instruction)0)<<(8)))<<(0)))))))))->val)); io1->value_ = io2->value_; ((io1)->tt_=(io2->tt_)); ((void)L, ((void)0)); ((void)0); };
 { if ((__builtin_expect(((trap) != 0), 0))) { trap = luaG_traceexec(L, pc); (base = ci->func.p + 1); } i = *(pc++); }; goto *disptab[(((OpCode)(((i)>>0) & ((~((~(Instruction)0)<<(7)))<<(0)))))];;;
 }
 L_OP_LOADI: {
```

```
$ gcc -std=c99 -DLUA_USE_LINUX -DLUA_USE_JUMPTABLE=0 -E -P lvm.c | grep -A6 "case OP_MOVE: {"
 case OP_MOVE: {
 StkId ra = (base+(((int)((((i)>>((0 + 7))) & ((~((~(Instruction)0)<<(8)))<<(0)))))));
 { TValue *io1=((&(ra)->val)); ... };
 break;
 }
 case OP_LOADI: {
```

**一句話摘要**：每個指令的處理碼結尾，直接讀下一條指令、取出 opcode、透過一張「標籤位址表」跳到那個 opcode 的處理碼，不回到迴圈頂端。

**問題 1**：jump table 模式下，`vmbreak` 展開成「`vmfetch()` 讀下一條指令 + `goto *disptab[opcode]`」。控制流**不回到** `for (;;)` 的頂端、也不經過 `switch`，直接跳到下一個 handler。整個 `lvm.c` 展開後共有 78 處 `goto *disptab`（`grep -o 'goto \*disptab' | wc -l`），每個 handler 結尾各一個，外加迴圈開頭的第一次分派。switch 模式下 `vmbreak` 就是 `break`，跳出 switch 回到 `for` 頂端再 `vmfetch()`。

**問題 2**：`disptab` 是用 `&&L_OP_MOVE` 這類**標籤位址**初始化的，而 C（GNU C）的標籤作用域是**函式**。表必須宣告在 `luaV_execute` 內部才能引用那些標籤。這是「`#include` 出現在函式中間」的典型理由：它是程式碼生成，不是引入介面（Ch 14 進階一節）。

**問題 3**：`&&label` 是 GNU C 的 **labels as values** 擴充，取得標籤的位址；`goto *ptr` 是 **computed goto**。效能上的差異來自分支預測：switch 版本所有 opcode 共用一個間接跳躍點，CPU 的分支預測器只能根據「上一次從這裡跳去哪」猜；jump table 版本每個 handler 結尾各有一個間接跳躍，預測器能學到「`ADD` 後面常接 `MMBIN`」這類模式。實際快多少依 CPU 與程式而定，本練習沒有做 benchmark，不下數字。

**用到的技巧**：Ch 13（巨集重新定義、`#if` 選路徑、`gcc -E` 驗證）、Ch 14（`##` 拼出 `L_OP_MOVE`、函式內 `#include`）、Ch 8（goto 控制流）。

**驗證方式**：上面兩次 `gcc -E` 就是驗證。進階：`objdump -d --no-show-raw-insn lvm.o | grep -c "jmp  *\*"` 比較兩種 build 的間接跳躍數量。

</details>

---

## 第 2 題：Lua 指令的位元解碼

```c
/* lua-5.4.9/lopcodes.h:20 */
iABC          C(8)     |      B(8)     |k|     A(8)      |   Op(7)     |
iABx                Bx(17)               |     A(8)      |   Op(7)     |
iAsBx              sBx (signed)(17)      |     A(8)      |   Op(7)     |

/* lua-5.4.9/lopcodes.h:104 */
/* creates a mask with 'n' 1 bits at position 'p' */
#define MASK1(n,p)	((~((~(Instruction)0)<<(n)))<<(p))

/* lua-5.4.9/lopcodes.h:114 */
#define GET_OPCODE(i)	(cast(OpCode, ((i)>>POS_OP) & MASK1(SIZE_OP,0)))
...
#define getarg(i,pos,size)	(cast_int(((i)>>(pos)) & MASK1(size,0)))
...
#define GETARG_A(i)	getarg(i, POS_A, SIZE_A)
...
#define GETARG_sC(i)	sC2int(GETARG_C(i))
```

**問題**：

1. `MASK1(8, 0)` 的值是多少？一步一步算出 `~((~(Instruction)0)<<(8))`。
2. 指令 `0x01000122` 的 opcode、A、B、C、k 各是多少？
3. `GETARG_sC` 為什麼要 `sC2int`？Lua 用什麼方法在 8 個無號 bit 裡表示負數？

**提示**：`POS_A = 7`、`POS_k = 15`、`POS_B = 16`、`POS_C = 24`（`lopcodes.h:47–52` 從 `SIZE_*` 累加出來）。寫一個程式連結 `liblua.a`，把一段 Lua 編譯成 `Proto` 後用這些巨集解碼。

<details>
<summary>解答</summary>

**問題 1**：`~(Instruction)0` 是 32 個 1（`Instruction` 是 32 位元無號整數）。左移 8 位得到 `0xFFFFFF00`，再取反得到 `0x000000FF`——低 8 位全是 1。再左移 `p` 位就是「在第 p 位開始的 n 個 1」。之所以先轉成 `Instruction` 再取反，是為了避免對有號 `int` 做位元運算的實作定義行為（Ch 9）。

**問題 2**：`0x01000122`：
- opcode = `0x122 & 0x7F` = `0x22` = 34
- A = `(0x01000122 >> 7) & 0xFF` = `0x122 >> 7` = 2
- k = bit 15 = 0
- B = `(i >> 16) & 0xFF` = 0
- C = `i >> 24` = 1

opcode 34 是哪個？寫程式查：

```
$ ./n        # scratch/pb/exp/n.c：印出幾個列舉值
NUM_OPCODES=83 OP_ADD=34 OP_ADDI=21 OP_MMBIN=46 TM_ADD=6 OFFSET_sC=127
```

是 `OP_ADD`，意思是 `R[2] := R[0] + R[1]`。

**驗證：寫一個迷你反組譯器**（`scratch/pb/exp/minidis.c`）。它用 `luaL_loadstring` 編譯一段 Lua，從 stack 頂端取出 `LClosure`，對 `cl->p->code[]` 的每條指令用 `GET_OPCODE`、`getOpMode` 與 `GETARG_*` 解碼，名字查 `lopnames.h` 的 `opnames[]`：

```c
static void dis(const Proto *p) {
    for (int pc = 0; pc < p->sizecode; pc++) {
        Instruction i = p->code[pc];
        OpCode op = GET_OPCODE(i);
        printf("%3d  0x%08x  %-10s", pc, (unsigned)i, opnames[op]);
        switch (getOpMode(op)) {
        case iABC:  printf(" A=%d B=%d C=%d k=%d", GETARG_A(i), GETARG_B(i), GETARG_C(i), GETARG_k(i)); break;
        case iABx:  printf(" A=%d Bx=%d", GETARG_A(i), GETARG_Bx(i)); break;
        case iAsBx: printf(" A=%d sBx=%d", GETARG_A(i), GETARG_sBx(i)); break;
        case iAx:   printf(" Ax=%d", GETARG_Ax(i)); break;
        case isJ:   printf(" sJ=%d", GETARG_sJ(i)); break;
        }
        printf("\n");
    }
}
```

```
$ gcc -std=c99 -I../lua -o minidis minidis.c ../lua/liblua.a -lm -ldl && ./minidis
source: local a, b = ... ; return a + b
  0  0x00000051  VARARGPREP A=0 B=0 C=0 k=0
  1  0x03000050  VARARG     A=0 B=0 C=3 k=0
  2  0x01000122  ADD        A=2 B=0 C=1 k=0
  3  0x0601002e  MMBIN      A=0 B=1 C=6 k=0
  4  0x01020146  RETURN     A=2 B=2 C=1 k=0
  5  0x01010146  RETURN     A=2 B=1 C=1 k=0
```

第 2 條就是我們手算的那條，結果吻合。（第 5 條是編譯器在每個函式結尾固定加的 `RETURN`，第 4 條才是 `return a + b`。）

**問題 3**：B、C 欄位是無號的 8 bit。Lua 用 **excess-K**（偏移 K）表示有號數：存的是 `值 + K`，讀的時候減回來。對 8-bit 的 C，`OFFSET_sC = MAXARG_C >> 1 = 127`（`lopcodes.h:98`）。驗證：

```
$ ./minidis "local a = ... ; return a + 1"
  2  0x80000095  ADDI       A=1 B=0 C=128 k=0
  3  0x0680002f  MMBINI     A=0 B=128 C=6 k=0
```

`ADDI` 的 C 欄位存 128，`sC2int(128) = 128 - 127 = 1`，就是 `+ 1` 那個立即數。只看 `GETARG_C` 會以為是加 128。

**用到的技巧**：Ch 9（位元遮罩、無號運算避免 UB、偏移表示法）、Ch 11（`Proto` 結構）、Ch 13（巢狀巨集展開）。

</details>

---

## 第 3 題：算術指令後面那個神祕的 `pc++`

```c
/* lua-5.4.9/lvm.c:962 */
#define op_arith_aux(L,v1,v2,iop,fop) {  \
  StkId ra = RA(i); \
  if (ttisinteger(v1) && ttisinteger(v2)) {  \
    lua_Integer i1 = ivalue(v1); lua_Integer i2 = ivalue(v2);  \
    pc++; setivalue(s2v(ra), iop(L, i1, i2));  \
  }  \
  else op_arithf_aux(L, v1, v2, fop); }

/* lua-5.4.9/lvm.c:974 */
#define op_arith(L,iop,fop) {  \
  TValue *v1 = vRB(i);  \
  TValue *v2 = vRC(i);  \
  op_arith_aux(L, v1, v2, iop, fop); }

/* lua-5.4.9/lvm.c:1468 */
      vmcase(OP_ADD) {
        op_arith(L, l_addi, luai_numadd);
        vmbreak;
      }
```

**問題**：

1. `l_addi` 和 `luai_numadd` 是什麼？為什麼要當參數傳進去？
2. 加法成功時為什麼要 `pc++`？`pc` 原本指向哪裡？
3. 如果 `a` 是字串 `"10"`、`b` 是 table，`OP_ADD` 會做什麼？

**提示**：看第 2 題反組譯的輸出，`ADD` 的下一條是什麼。再讀 `lvm.c:1518` 的 `vmcase(OP_MMBIN)`。

<details>
<summary>解答</summary>

**問題 1**：`l_addi(L,a,b)` 是 `intop(+, a, b)`（`lvm.c:896`），也就是「轉無號、相加、轉回有號」的環繞加法（Ch 14「運算子也能當參數」）；`luai_numadd(L,a,b)` 是 `((a)+(b))`（`llimits.h:346`），浮點加法。`op_arith` 是一個**算術指令的模板**：整數路徑和浮點路徑的結構一樣，只有運算本身不同，所以把運算當參數傳入。`OP_SUB` 傳 `l_subi, luai_numsub`，`OP_MUL` 傳 `l_muli, luai_nummul`，以此類推。

**問題 2**：`vmfetch()` 在處理目前指令之前已經做了 `i = *(pc++)`，所以進入 handler 時 `pc` 指向**下一條指令**——根據第 2 題的反組譯，那就是 `MMBIN`。

`lcode.c:1411–1416`（`finishbinexpval`）在產生算術指令之後，**固定緊接著產生一條 `MMBIN`**：

```c
  int pc = luaK_codeABCk(fs, op, 0, v1, v2, 0);
  ...
  luaK_codeABCk(fs, mmop, v1, v2, event, flip);  /* to call metamethod */
```

所以：
- **快速路徑**（兩邊都是數字）：算完結果，`pc++` 跳過 `MMBIN`，下一個 `vmbreak` 執行的是 `MMBIN` 之後的指令。
- **慢速路徑**（有一邊不是數字）：`op_arith_aux` 和 `op_arithf_aux` 都沒有命中，**不做** `pc++`，`vmbreak` 讀到的下一條就是 `MMBIN`，由它去找 metamethod。

```c
/* lua-5.4.9/lvm.c:1518 */
      vmcase(OP_MMBIN) {
        StkId ra = RA(i);
        Instruction pi = *(pc - 2);  /* original arith. expression */
        TValue *rb = vRB(i);
        TMS tm = (TMS)GETARG_C(i);
        StkId result = RA(pi);
        ...
        Protect(luaT_trybinTM(L, s2v(ra), rb, result, tm));
```

`*(pc - 2)` 回頭找「前一條指令」（`pc` 此時指向 `MMBIN` 的下一條，減 2 就是那條 `ADD`），從它取出結果要寫到哪個暫存器。反組譯裡 `MMBIN A=0 B=1 C=6` 的 C=6 就是 `TM_ADD`（第 2 題 `./n` 輸出 `TM_ADD=6`）。

**一句話摘要**：算術指令把「型別都是數字」的常見情況內嵌處理並跳過下一條指令；不是數字時讓下一條指令去呼叫 metamethod。

**問題 3**：`"10"` 是字串，`ttisinteger` 失敗；`op_arithf_aux` 用 `tonumberns`（`lvm.h:56`）嘗試轉成浮點，它只認 float 和 integer 兩種 tag，**不做字串轉換**（`ns` = no string），失敗。沒有 `pc++`，執行 `MMBIN`，`luaT_trybinTM` 先找第一個運算元 `"10"` 的 `__add`。字串的 metatable 來自 `lstrlib.c`，Lua 5.4 在那裡定義了一組算術 metamethod（`lstrlib.c:330` 的 `stringmetamethods`），它們會嘗試把字串轉成數字；另一邊是 table、轉不了，於是走 `trymt`（`lstrlib.c:277`）報錯。實測：

```
$ ./lua -e 'print("10" + {})'
./lua: (command line):1: attempt to add a 'string' with a 'table'
stack traceback:
	[C]: in metamethod 'add'
	(command line):1: in main chunk
	[C]: in ?
$ ./lua -e 'print("10" + 1, math.type("10"+1))'
11	integer
```

traceback 裡的 `[C]: in metamethod 'add'` 證實錯誤是從字串的 `__add` metamethod 裡丟出來的，不是 VM 本身。而 `"10" + 1` 能得到整數 11，也是同一條 metamethod 路徑的功勞——VM 的快速路徑完全不碰字串。

**這題的心法**：一個看起來無意義的 `pc++`，答案在**另一個檔案**（`lcode.c`，編譯器）。VM 與編譯器之間有一個隱含契約：「算術指令後面一定是 MMBIN」。這種跨檔案的不變量是**脈絡鴻溝**的一種，讀碼時只能靠搜尋「誰產生這個資料」來補。

</details>

---

## 第 4 題：一行判斷，三層巨集

```c
/* lua-5.4.9/lobject.h:364 */
#define ttisshrstring(o)	checktag((o), ctb(LUA_VSHRSTR))

/* lua-5.4.9/lobject.h:247 */
#define l_isfalse(o)	(ttisfalse(o) || ttisnil(o))

/* lua-5.4.9/lobject.h:118 */
#define setobj(L,obj1,obj2) \
	{ TValue *io1=(obj1); const TValue *io2=(obj2); \
          io1->value_ = io2->value_; settt_(io1, io2->tt_); \
	  checkliveness(L,io1); lua_assert(!isnonstrictnil(io1)); }
```

**問題**：

1. `ttisshrstring(o)` 最後比較的常數是多少（十六進位）？
2. `l_isfalse` 裡 `ttisfalse` 用 `checktag`、`ttisnil` 用 `checktype`。為什麼不都用同一個？如果 `ttisnil` 改用 `checktag(o, LUA_VNIL)` 會壞掉什麼？
3. `setobj` 為什麼先把參數存進 `io1`、`io2` 兩個區域變數，而不是直接寫 `(obj1)->value_ = (obj2)->value_`？

**提示**：Ch 11 的 tag 位元圖與 `realtt` 實驗輸出。`lobject.h:183–189` 定義了三種 nil。

<details>
<summary>解答</summary>

**問題 1**：`LUA_VSHRSTR = makevariant(LUA_TSTRING, 0) = 4 | (0 << 4) = 0x04`，`ctb(0x04) = 0x04 | (1 << 6) = 0x44`。Ch 11 的 `realtt` 實驗對 `'hi'` 印出 `tt_=0x44`，吻合。

**問題 2**：`checktag` 比較完整的 tag（含 variant 與 collectable bit），`checktype` 只比較低 4 bit 的基本型別。Lua 5.4 有三種 nil variant（`lobject.h:183–189`）：`LUA_VNIL`（一般 nil，0x00）、`LUA_VEMPTY`（table 裡的空槽，0x10）、`LUA_VABSTKEY`（查不到的 key，0x20）。對 Lua 程式而言它們都是 nil、都是假值，所以 `l_isfalse` 必須用 `checktype` 一網打盡。如果改成 `checktag(o, LUA_VNIL)`，從 table 讀出一個不存在的 key（tag 是 `LUA_VABSTKEY`）時會被判斷成真值，`if t.missing then` 就會錯誤地執行。布林則只有 `false` 一種假值，用 `checktag` 精確比對 `LUA_VFALSE`。

**問題 3**：避免多次求值（Ch 13）。`obj1` 在 `setobj` 本體裡用了三次（寫值、寫 tag、`checkliveness`），如果呼叫端傳的是 `s2v(ra++)` 之類有副作用的運算式，展開後會被執行三次。存進區域變數保證只求值一次。另一個好處是型別檢查：`TValue *io1=(obj1)` 會讓傳錯型別的呼叫在編譯期報警告。這個巨集用 `{ }` 而不是 `do { } while (0)` 包起來——Lua 原始碼的呼叫端總是寫成 `setobj(...);` 而且不會放在無大括號的 `if/else` 中間，作者接受了這個風險。讀碼時看到 `{ }` 包的多敘述巨集，要記得它在 `if (x) setobj(...); else ...` 裡會壞掉（Ch 13 的 `SWAP_BRACE` 實驗）。

**用到的技巧**：Ch 11（tagged union、variant bit）、Ch 13（多次求值、巨集包裝方式）。

</details>

---

## 第 5 題：GC 物件的轉型巨集

```c
/* lua-5.4.9/lstate.h:370 */
#define cast_u(o)	cast(union GCUnion *, (o))

/* macros to convert a GCObject into a specific value */
#define gco2ts(o)  \
	check_exp(novariant((o)->tt) == LUA_TSTRING, &((cast_u(o))->ts))
...
#define obj2gco(v)	check_exp((v)->tt >= LUA_TSTRING, &(cast_u(v)->gc))

/* lua-5.4.9/llimits.h:106 */
#define lua_assert(c)           assert(c)
...
#define check_exp(c,e)		(lua_assert(c), (e))
/* llimits.h:114 */
#define lua_assert(c)		((void)0)
#define check_exp(c,e)		(e)
```

**問題**：

1. 在正式 build 下 `gco2ts(o)` 展開成什麼？它在執行期花了幾條指令？
2. `obj2gco` 的註解（`lstate.h:387–389`）說「The access to 'tt' tries to ensure that 'v' is actually a Lua object」。正式 build 下 `check_exp` 會丟掉條件，那這句話還成立嗎？
3. `check_exp(c, e)` 為什麼用逗號運算子，而不是寫成兩個敘述？

**提示**：`llimits.h:104–116` 的 `#if defined(LUAI_ASSERT)` 決定用哪組定義。Ch 11「用途三：layout 重疊」。

<details>
<summary>解答</summary>

**問題 1**：`LUAI_ASSERT` 沒定義時，`check_exp(c, e)` 就是 `(e)`，`gco2ts(o)` 變成 `&(((union GCUnion *)(o))->ts)`。union 所有成員的 offset 都是 0，所以這只是一次**指標型別轉換**，執行期 0 條指令。

**問題 2**：正式 build 下不成立。正式 build 的 `check_exp(c,e)` 是 `(e)`，條件 `c`（也就是 `(v)->tt >= LUA_TSTRING`）完全不出現在展開結果裡，編譯器連看都看不到它——就算 `v` 的型別根本沒有 `tt` 欄位，也不會有編譯錯誤，因為 `cast_u` 是強制轉型。所以那句註解描述的保護**只在 debug build（定義了 `LUAI_ASSERT`）有效**。這是一個值得記住的讀碼細節：註解描述的是作者的意圖，實際效果要看巨集在你的設定下展開成什麼。用 `gcc -E -P` 加與不加 `-DLUAI_ASSERT` 各展開一次 `lstring.c` 就能看到差異。

**問題 3**：`check_exp` 要能用在**運算式**裡（例如 `TString *ts = gco2ts(o);`）。兩個敘述不能出現在運算式位置；逗號運算子 `(a, b)` 先求值 `a`、丟掉結果、再求值 `b` 並以 `b` 為整個運算式的值。這是「讓巨集在運算式位置也能做檢查」的標準手法，kernel 的 `BUILD_BUG_ON_ZERO` 則用另一種方式達成同樣目的（Ch 14 errname 一節）。

**用到的技巧**：Ch 11（union 指標轉換）、Ch 13（條件編譯決定巨集定義）、Ch 9（逗號運算子）。

</details>

---

## 第 6 題：`sdslen` 怎麼知道字串多長

```c
/* redis-7.2.17/src/sds.h:83 */
#define SDS_HDR_VAR(T,s) struct sdshdr##T *sh = (void*)((s)-(sizeof(struct sdshdr##T)));
#define SDS_HDR(T,s) ((struct sdshdr##T *)((s)-(sizeof(struct sdshdr##T))))
#define SDS_TYPE_5_LEN(f) ((f)>>SDS_TYPE_BITS)

static inline size_t sdslen(const sds s) {
    unsigned char flags = s[-1];
    switch(flags&SDS_TYPE_MASK) {
        case SDS_TYPE_5:
            return SDS_TYPE_5_LEN(flags);
        case SDS_TYPE_8:
            return SDS_HDR(8,s)->len;
        ...
```

```c
/* redis-7.2.17/src/sds.c:61 */
static inline char sdsReqType(size_t string_size) {
    if (string_size < 1<<5)
        return SDS_TYPE_5;
    if (string_size < 1<<8)
        return SDS_TYPE_8;
    if (string_size < 1<<16)
        return SDS_TYPE_16;
#if (LONG_MAX == LLONG_MAX)
    if (string_size < 1ll<<32)
        return SDS_TYPE_32;
    return SDS_TYPE_64;
#else
    return SDS_TYPE_32;
#endif
}
```

**問題**：

1. 長度 0、1、31、32、255、256、65535、65536 的字串，各用哪種 header？header 幾個 byte？`s[-1]` 是多少？先預測再驗證。
2. `s[-1]` 是合法的 C 嗎？在什麼前提下合法？
3. `#if (LONG_MAX == LLONG_MAX)` 在處理什麼平台差異？

**提示**：Ch 12 的 sds 一節。`sds.c:110` 對空字串有特殊處理。

<details>
<summary>解答</summary>

**問題 1**：寫一支連結 Redis `sds.c`/`zmalloc.c`（libc 配置器）的程式（`scratch/pb/exp/sdstype.c`），用 `sdsnewlen(SDS_NOINIT, len)` 建立各種長度：

```c
sds s = sdsnewlen(SDS_NOINIT, lens[k]);
unsigned char flags = s[-1];
printf("len=%-6zu s[-1]=0x%02x  type=%u  header=%zu bytes  sdslen=%zu\n",
       lens[k], flags, flags & SDS_TYPE_MASK, (size_t)(s - (char *)sdsAllocPtr(s)), sdslen(s));
```

```
$ ./sdstype
len=0      s[-1]=0x01  type=1  header=3 bytes  sdslen=0
len=1      s[-1]=0x08  type=0  header=1 bytes  sdslen=1
len=31     s[-1]=0xf8  type=0  header=1 bytes  sdslen=31
len=32     s[-1]=0x01  type=1  header=3 bytes  sdslen=32
len=255    s[-1]=0x01  type=1  header=3 bytes  sdslen=255
len=256    s[-1]=0x02  type=2  header=5 bytes  sdslen=256
len=65535  s[-1]=0x02  type=2  header=5 bytes  sdslen=65535
len=65536  s[-1]=0x03  type=3  header=9 bytes  sdslen=65536
```

- 長度 0 用 type 8，不是 type 5：`_sdsnewlen` 的特例（`sds.c:110`，「Empty strings are usually created in order to append」）。
- 長度 1：`0x08 = 0b00001_000`，type 5、長度 1 存在高 5 bit。長度 31：`0xf8 = 0b11111_000`，高 5 bit 是 31，這是 type 5 能存的最大長度。
- 長度 32 跨到 type 8；256 跨到 type 16；65536 跨到 type 32。邊界恰好是 `1<<5`、`1<<8`、`1<<16`。
- header 大小：type 5 是 1 byte（只有 flags），type 8 是 3（len、alloc、flags 各 1），type 16 是 5（2+2+1），type 32 是 9（4+4+1）。全部 packed，沒有 padding。

**問題 2**：`s[-1]` 等價於 `*(s - 1)`。指標算術只要結果落在**同一個陣列物件內（或尾端後一位）**就合法（C11 6.5.6p8）。`s` 是從 `zmalloc` 回傳的區塊中間算出來的（`s = (char*)sh + hdrlen`），`s - 1` 仍在同一個配置區塊內，所以合法。如果有人把一個普通 `char *`（例如字串常數）當 `sds` 傳進來，`s[-1]` 就讀到區塊外——這就是 Ch 12 `badfree` 實驗的同類錯誤。

**問題 3**：`LONG_MAX == LLONG_MAX` 表示 `long` 是 64 位元，即 LP64（Linux/macOS 64 位元）。在 32 位元平台或 LLP64（64 位元 Windows），`size_t` 可能是 32 位元，長度不可能超過 `1ll<<32`，type 64 沒有意義，於是直接回傳 type 32。這是用前置處理器在編譯期判斷資料模型的寫法（Ch 13）。

**用到的技巧**：Ch 12（header-before-pointer）、Ch 14（`##` 拼型別名）、Ch 9（位元欄位打包在一個 byte）、Ch 11（packed struct、flexible array）。

</details>

---

## 第 7 題：藏在指標低位元裡的型別

```c
/* redis-7.2.17/src/dict.c:118 */
/* The 3 least significant bits in a pointer to a dictEntry determines what the
 * pointer actually points to. If the least bit is set, it's a key. Otherwise,
 * the bit pattern of the least 3 significant bits mark the kind of entry. */

#define ENTRY_PTR_MASK     7 /* 111 */
#define ENTRY_PTR_NORMAL   0 /* 000 */
#define ENTRY_PTR_NO_VALUE 2 /* 010 */

/* Returns 1 if the entry pointer is a pointer to a key, rather than to an
 * allocated entry. Returns 0 otherwise. */
static inline int entryIsKey(const dictEntry *de) {
    return (uintptr_t)(void *)de & 1;
}
...
static inline dictEntry *encodeMaskedPtr(const void *ptr, unsigned int bits) {
    assert(((uintptr_t)ptr & ENTRY_PTR_MASK) == 0);
    return (dictEntry *)(void *)((uintptr_t)ptr | bits);
}

/* redis-7.2.17/src/dict.c:831 */
static dictEntry *dictGetNext(const dictEntry *de) {
    if (entryIsKey(de)) return NULL; /* there's no next */
    if (entryIsNoValue(de)) return decodeEntryNoValue(de)->next;
    return de->next;
}
```

**問題**：

1. 一個 `dictEntry *` 可能指向哪三種東西？各自的低 3 bit 是什麼？
2. 這個技巧依賴什麼前提？`encodeMaskedPtr` 裡的 `assert` 在保護什麼？
3. 「key 直接存在 bucket 裡」的情況，key 本身的指標最低位為什麼會是 1？誰保證的？
4. 如果你在 gdb 裡看到 `d->ht_table[0][5] = 0x55d0...b2`，直接 `p *d->ht_table[0][5]` 會發生什麼？

**提示**：`dict.h:64–68` 的 `no_value`、`keys_are_odd` 旗標。Ch 11 的「對比與取捨」表提到了 pointer tagging。

<details>
<summary>解答</summary>

**問題 1**：

| 低 3 bit | 意義 | 實際指向 |
|---|---|---|
| `xx1` | key 本身 | 呼叫者的 key（例如一個 sds），沒有 entry 結構、沒有 `next` |
| `000` | 一般 entry | `struct dictEntry`（`dict.c:63`：key、value union、next、metadata） |
| `010` | 無 value 的精簡 entry | `dictEntryNoValue`（`dict.c:77`：只有 key 和 next） |

**問題 2**：前提是**所有被標記的指標都至少 8-byte 對齊**，低 3 bit 原本一定是 0，才能借來當 tag。glibc 與 jemalloc 在 x86-64 上回傳的位址都是 16-byte 對齊。`assert` 確保被標記的指標真的是乾淨的，否則 tag 會和位址本身混在一起，decode 回來是錯的位址。

**驗證**（`scratch/pb/exp/tagptr.c`，把 `dict.c` 的幾個 inline 函式複製出來）：

```
$ ./tagptr
malloc(1..64) 回傳位址的低 4 bit OR 起來 = 0
normal entry           0x5d0489aa42a0  low3=0  isKey=0 isNormal=1 isNoValue=0
key stored directly    0x5d0489aa5391  low3=1  isKey=1 isNormal=0 isNoValue=0
no-value entry (tagged) 0x5d0489aa53b2  low3=2  isKey=0 isNormal=0 isNoValue=1
decodeMaskedPtr(nv) == novalue ? 1
```

第一行：64 次不同大小的 `malloc`，回傳位址的低 4 bit 全部是 0，16-byte 對齊成立。`0x...b2` 就是 `0x...b0 | 2`，decode 之後拿回原本的位址。

**問題 3**：「key 直接存」只在 `dictType` 設了 `no_value = 1` 且 `keys_are_odd = 1` 時才啟用（`dict.h:64–68` 的註解，以及第 8 題 `dictRehash` 裡的 `if (d->type->keys_are_odd && ...)`）。誰保證 key 是奇數位址？使用這種 dict 的呼叫者。`rg -n "keys_are_odd" .` 在 Redis 7.2.17 只找到一個使用者：

```c
/* redis-7.2.17/src/server.c:441 */
/* Set dictionary type. Keys are SDS strings, values are not used. */
dictType setDictType = {
    ...
    .no_value = 1,             /* no values in this dict */
    .keys_are_odd = 1          /* an SDS string is always an odd pointer */
};
```

「SDS 永遠是奇數指標」的理由要拼兩章的知識：五種 sds header 的大小是 1、3、5、9、17 byte，**全部是奇數**（第 6 題實測了前四種）；配置器回傳的位址是 16 的倍數（下面的 `tagptr` 實驗）；偶數加奇數是奇數。這是一個由資料格式「碰巧」提供、然後被另一個模組刻意利用的不變量——如果哪天有人新增一種 header 大小為偶數的 sds 型別，`setDictType` 會靜悄悄地壞掉。

**問題 4**：`0x...b2` 的低 3 bit 是 `010`，它是一個被標記的 `dictEntryNoValue *`，真正的位址是 `0x...b0`，而且結構是 `dictEntryNoValue` 不是 `dictEntry`。直接解參考會把位址偏 2 byte、用錯誤的 struct 解讀，印出垃圾。正確做法：`p *(dictEntryNoValue *)((uintptr_t)d->ht_table[0][5] & ~7)`。**指標裡藏 tag 的程式碼，debugger 和 IDE 都不會幫你 decode**——這是間接鴻溝的一種極端形式：連「這個指標指向什麼型別」都要靠讀程式碼才知道。

**用到的技巧**：Ch 9（位元遮罩）、Ch 10（指標與整數轉換 `uintptr_t`）、Ch 11（tagged representation 的另一種實作）。

</details>

---

## 第 8 題：漸進式 rehash

```c
/* redis-7.2.17/src/dict.c:295（節錄） */
int dictRehash(dict *d, int n) {
    int empty_visits = n*10; /* Max number of empty buckets to visit. */
    ...
    while(n-- && d->ht_used[0] != 0) {
        dictEntry *de, *nextde;
        ...
        while(d->ht_table[0][d->rehashidx] == NULL) {
            d->rehashidx++;
            if (--empty_visits == 0) return 1;
        }
        de = d->ht_table[0][d->rehashidx];
        /* Move all the keys in this bucket from the old to the new hash HT */
        while(de) {
            uint64_t h;

            nextde = dictGetNext(de);
            void *key = dictGetKey(de);
            /* Get the index in the new hash table */
            if (d->ht_size_exp[1] > d->ht_size_exp[0]) {
                h = dictHashKey(d, key) & DICTHT_SIZE_MASK(d->ht_size_exp[1]);
            } else {
                /* We're shrinking the table. The tables sizes are powers of
                 * two, so we simply mask the bucket index in the larger table
                 * to get the bucket index in the smaller table. */
                h = d->rehashidx & DICTHT_SIZE_MASK(d->ht_size_exp[1]);
            }
            ...
            d->ht_table[1][h] = de;
            d->ht_used[0]--;
            d->ht_used[1]++;
            de = nextde;
        }
        d->ht_table[0][d->rehashidx] = NULL;
        d->rehashidx++;
    }

    /* Check if we already rehashed the whole table... */
    if (d->ht_used[0] == 0) {
        zfree(d->ht_table[0]);
        /* Copy the new ht onto the old one */
        d->ht_table[0] = d->ht_table[1];
        ...
        d->rehashidx = -1;
        return 0;
    }

    /* More to rehash... */
    return 1;
}
```

```c
/* redis-7.2.17/src/dict.h:81 */
#define DICTHT_SIZE(exp) ((exp) == -1 ? 0 : (unsigned long)1<<(exp))
#define DICTHT_SIZE_MASK(exp) ((exp) == -1 ? 0 : (DICTHT_SIZE(exp))-1)
```

**問題**：

1. 參數 `n` 的單位是什麼？為什麼需要 `empty_visits`？
2. 縮小時為什麼可以不重算 hash，直接用 `rehashidx & mask`？
3. rehash 進行中，一個**新** key 會插到哪個表？一個查詢要查幾個表？
4. 設計一個實驗，在真的 Redis 裡看到「rehash 做到一半」的狀態。

**提示**：`dict.c:414` 的 `_dictRehashStep` 呼叫 `dictRehash(d, 1)`。Redis 的 `DEBUG HTSTATS <dbid>` 會印出兩個表的大小與元素數；設定 `activerehashing no` 可以關掉背景的 rehash。

<details>
<summary>解答</summary>

**問題 1**：`n` 是要搬移的**非空 bucket 數**，不是 key 數。每搬一個 bucket（及它整條鏈上的所有 entry）`n` 減 1。`empty_visits = n*10` 限制「為了找下一個非空 bucket 最多掃幾個空 bucket」——一個很稀疏的大表可能有一長串空 bucket，沒有這個上限，單次 rehash 步驟的耗時就沒有上界。這正是漸進式 rehash 的目的：**把一次 O(N) 的搬家拆成很多次 O(1) 的小步驟**，Redis 是單執行緒的事件迴圈，任何一個長時間操作都會讓所有客戶端等待。這是領域知識：不懂「單執行緒伺服器的延遲預算」，就看不懂為什麼要把一個簡單的迴圈拆得這麼碎。

**問題 2**：表的大小都是 2 的冪，bucket 索引是 `hash & (size - 1)`，也就是 hash 的低 `exp` 位。從大表（低 `exp0` 位）縮到小表（低 `exp1` 位，`exp1 < exp0`），新索引就是舊索引的低 `exp1` 位：`(hash & mask0) & mask1 = hash & mask1`。同一個舊 bucket 裡的所有 key 低 `exp0` 位都相同，所以它們在新表也落在同一個 bucket，不必重算 hash。擴大時則不行：需要更多位元，舊索引裡沒有那些資訊。

**問題 3**：rehash 進行中新 key 一律插到 `ht_table[1]`（否則剛插進 `[0]` 的 key 可能落在已經搬過的 bucket，永遠搬不到），查詢則兩個表都要查。這兩點在 `dict.c` 的 `dictFindPositionForInsert` 與 `dictFind` 裡，可以 `rg -n "dictIsRehashing" dict.c` 找到每一處分支。

**問題 4：實驗**。關掉背景 rehash，塞 4096 個 key 填滿一個 4096 bucket 的表，再插一個觸發擴張：

```
$ ./redis-server --port 6399 --save "" --appendonly no --enable-debug-command yes \
      --activerehashing no --pidfile /tmp/r6399.pid > /tmp/r6399.out 2>&1 &
$ C="./redis-cli -p 6399"
$ $C debug populate 4096 > /dev/null
$ $C debug htstats 0
[Dictionary HT]
Hash table 0 stats (main hash table):
 table size: 4096
 number of elements: 4096
...
$ $C set extra1 v > /dev/null
$ $C debug htstats 0
[Dictionary HT]
Hash table 0 stats (main hash table):
 table size: 4096
 number of elements: 4096
Hash table 1 stats (rehashing target):
 table size: 8192
 number of elements: 1
```

`SET extra1` 觸發擴張到 8192，新 key 直接進了表 1；表 0 還是 4096 個，一個都還沒搬。接著做 5 次查詢，每次查詢都會順便呼叫 `_dictRehashStep`（重開一個乾淨的 server 重做上述步驟後）：

```
$ for k in 1 2 3 4 5; do $C get nosuchkey > /dev/null; done
$ $C debug htstats 0 | sed -n '1,7p'
[Dictionary HT]
Hash table 0 stats (main hash table):
 table size: 4096
 number of elements: 4090
Hash table 1 stats (rehashing target):
 table size: 8192
 number of elements: 7
```

每次 `_dictRehashStep` 呼叫 `dictRehash(d, 1)`，最多搬一個非空 bucket（連續掃到 10 個空 bucket 就放棄這一步）。5 次查詢搬走了 6 個 key（有的 bucket 鏈上不只一個 key）。搬了幾個 key 每次執行會不同，因為 Redis 每次啟動用隨機的 hash seed（`dictSetHashFunctionSeed`）；我們另一次執行得到的是表 0 剩 4087 個。對照組：不加 `--activerehashing no`，`SET` 之後等 0.5 秒：

```
--- activerehashing yes, after 0.5s:
[Dictionary HT]
Hash table 0 stats (main hash table):
 table size: 8192
 number of elements: 4097
```

背景的 `serverCron` 已經把 rehash 做完，表 1 併入表 0。實驗結束記得 `$C shutdown nosave`。

**用到的技巧**：Ch 9（2 的冪與遮罩）、Ch 8（巢狀迴圈與提早 return）、Ch 12（`zfree` 舊表、指標交接所有權）。

</details>

---

## 第 9 題：listpack 的編碼與反向長度

```c
/* redis-7.2.17/src/listpack.c:55（節錄） */
#define LP_ENCODING_7BIT_UINT 0
#define LP_ENCODING_7BIT_UINT_MASK 0x80
#define LP_ENCODING_IS_7BIT_UINT(byte) (((byte)&LP_ENCODING_7BIT_UINT_MASK)==LP_ENCODING_7BIT_UINT)

#define LP_ENCODING_6BIT_STR 0x80
#define LP_ENCODING_6BIT_STR_MASK 0xC0
#define LP_ENCODING_IS_6BIT_STR(byte) (((byte)&LP_ENCODING_6BIT_STR_MASK)==LP_ENCODING_6BIT_STR)

#define LP_ENCODING_13BIT_INT 0xC0
#define LP_ENCODING_13BIT_INT_MASK 0xE0
...
#define LP_ENCODING_16BIT_INT 0xF1
...
#define LP_ENCODING_12BIT_STR_LEN(p) ((((p)[0] & 0xF) << 8) | (p)[1])

/* redis-7.2.17/src/listpack.c:386 */
static inline uint64_t lpDecodeBacklen(unsigned char *p) {
    uint64_t val = 0;
    uint64_t shift = 0;
    do {
        val |= (uint64_t)(p[0] & 127) << shift;
        if (!(p[0] & 128)) break;
        shift += 7;
        p--;
        if (shift > 28) return UINT64_MAX;
    } while(1);
    return val;
}
```

**問題**：

1. 每個 `IS_*` 巨集的 MASK 長度都不同（`0x80`、`0xC0`、`0xE0`、`0xF0`、`0xFF`）。為什麼？如果 `LP_ENCODING_IS_6BIT_STR` 偷懶只用 `0x80` 當 MASK，`lpCurrentEncodedSizeUnsafe`（`listpack.c:428`）會怎麼誤判？
2. 解碼這些元素的 encoding byte：`05`、`85`、`f1 20 4e`、`df fd`、`e0 46`。
3. `lpDecodeBacklen` 的 `p--` 說明 backlen 是怎麼擺的？為什麼 listpack 需要一個「從後往前讀」的長度？
4. 用真的 Redis 驗證你的答案。

**提示**：把每個 `_MASK` 寫成二進位，它告訴你前綴有幾個 bit。`DEBUG LISTPACK <key>` 會把一個 listpack 的逐元素結構印到 server 的 **stdout**。

<details>
<summary>解答</summary>

**問題 1**：這是一個**前綴碼**（prefix code）：

| 前綴（二進位） | MASK | 編碼 |
|---|---|---|
| `0xxxxxxx` | `0x80` | 7-bit 無號整數，值就在這個 byte 裡 |
| `10xxxxxx` | `0xC0` | 6-bit 長度的字串，後面接字串內容 |
| `110xxxxx` | `0xE0` | 13-bit 有號整數，再讀 1 byte |
| `1110xxxx` | `0xF0` | 12-bit 長度的字串 |
| `11110001` 等 | `0xFF` | 16/24/32/64-bit 整數、32-bit 長度字串，用整個 byte 區分 |

每個 MASK 只保留「前綴那幾個 bit」，比較值就是前綴本身。前綴互不重疊（沒有一個前綴是另一個的開頭），所以每個 byte 只會符合一種編碼。如果 6BIT_STR 偷懶用 `0x80` 當 MASK，`(byte & 0x80) == 0x80` 對所有 `1xxxxxxx` 都成立——13-bit 整數、12-bit 字串、各種多 byte 整數全部會被當成 6-bit 字串。`lpCurrentEncodedSizeUnsafe` 的判斷順序是 7BIT_UINT、6BIT_STR、13BIT_INT……，第二個判斷就會吃掉後面所有情況，而且它算出的長度（`1 + (byte & 0x3F)`）是錯的，解析器會從錯的位置繼續讀，整個 listpack 解析錯亂。

容易看錯的是 `0xF0`：它既是 12BIT_STR 的 **MASK**，也是 32BIT_STR 的**編碼值**。`LP_ENCODING_IS_12BIT_STR(0xF0)` 是 `(0xF0 & 0xF0) == 0xE0`，不成立，所以不會誤判。讀這類程式碼時，**把每個 MASK 和比較值寫成二進位排在一起**，是唯一可靠的方法。

**問題 2**：

- `05`：`0xxxxxxx`，7-bit 整數 5。
- `85`：`10000101`，前綴 `10`，6-bit 字串長度 `000101` = 5，後面 5 個 byte 是字串。
- `f1 20 4e`：`0xF1` 是 16-bit 整數，後兩個 byte 是 little-endian 的 `0x4e20` = 20000。
- `df fd`：`11011111`，前綴 `110`，13-bit 有號整數：`((0xdf & 0x1f) << 8) | 0xfd = 0x1ffd = 8189`；13-bit 二補數的範圍是 -4096..4095，8189 ≥ 4096，所以值是 `8189 - 8192 = -3`。
- `e0 46`：`11100000`，前綴 `1110`，12-bit 字串長度 `LP_ENCODING_12BIT_STR_LEN` = `((0xe0 & 0xF) << 8) | 0x46` = 70。

**問題 4：用 Redis 驗證**。建一個 list，五個元素正好對應上面五種編碼：

```
$ ./redis-server --port 6399 --save "" --appendonly no --enable-debug-command yes \
      --pidfile /tmp/r6399.pid > /tmp/r6399.out 2>&1 &
$ C="./redis-cli -p 6399"
$ $C rpush mylist 5 hello 20000 -3 $(printf 'x%.0s' $(seq 1 70))
$ $C object encoding mylist
listpack
$ $C debug listpack mylist
Listpack structure printed on stdout
```

server 的 stdout 被導向檔案時是全緩衝的，`lpRepr` 印出的內容要等 server 結束（`shutdown nosave`，正常 exit 會 flush stdio）才會出現在 `/tmp/r6399.out`。我們第一次做這個實驗時用了 `--daemonize yes`，stdout 被接到 `/dev/null`，什麼都看不到；第二次沒 shutdown 就去讀檔案，也是空的。整理後的輸出：

```
{total bytes 96} {num entries 5}
{
	index:  0,
	offset: 6,
	hdr+entrylen+backlen:  2,
	bytes: 05|01|
	[str]5
}
{
	index:  1,
	offset: 8,
	hdr+entrylen+backlen:  7,
	bytes: 85|68|65|6c|6c|6f|06|
	[str]hello
}
{
	index:  2,
	offset: 15,
	hdr+entrylen+backlen:  4,
	bytes: f1|20|4e|03|
	[str]20000
}
{
	index:  3,
	offset: 19,
	hdr+entrylen+backlen:  3,
	bytes: df|fd|02|
	[str]-3
}
{
	index:  4,
	offset: 22,
	hdr+entrylen+backlen: 73,
	bytes: e0|46|78|78|...|78|48|
	[str]xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx...
}
{end}
```

（省略了 `addr`、`hdrlen`、`backlen`、`payload` 幾行。）五個元素的第一個 byte 和我們的解碼完全一致。第一個元素的 offset 是 6：listpack 開頭有 4 byte 的總長度與 2 byte 的元素個數。

**問題 3**：每個元素的**最後**一個（或幾個）byte 是 backlen：這個元素「encoding + 內容」的長度。`hello` 的 backlen 是 `06`（1 byte encoding + 5 byte 內容），70 個 x 的 backlen 是 `0x48` = 72（2 + 70）。

為什麼要放在尾端、從後往前讀？為了讓 listpack 能**反向走訪**（`LRANGE` 負索引、`RPOP`）：站在某個元素的開頭，往前退一個 byte 就是前一個元素的 backlen 的最後一個 byte，讀出長度就能跳到前一個元素的開頭。backlen 本身是變長編碼：每個 byte 用低 7 bit 存資料、最高 bit 表示「前面還有」，所以 `lpDecodeBacklen` 從最後一個 byte 開始、`p--` 往前讀，直到遇到最高 bit 為 0 的 byte。

把 `listpack.c:347` 的 `lpEncodeBacklen` 與 `:386` 的 `lpDecodeBacklen` 原樣複製出來（`scratch/pb/exp/backlen.c`，用 `diff` 確認 encode 部分與原始碼一字不差）：

```
$ ./backlen
len=5      encoded(1 B): 05   decode from last byte -> 5
len=127    encoded(1 B): 7f   decode from last byte -> 127
len=128    encoded(2 B): 01 80   decode from last byte -> 128
len=500    encoded(2 B): 03 f4   decode from last byte -> 500
len=16382  encoded(2 B): 7f fe   decode from last byte -> 16382
len=20000  encoded(3 B): 01 9c a0   decode from last byte -> 20000
```

`500` 編成 `03 f4`：最後一個 byte `0xf4 = 1_1110100`，最高 bit 是 1（前面還有），低 7 bit 是 116；往前一個 byte `0x03`，最高 bit 0（結束），值 3。`3 << 7 | 116 = 500`。注意 encode 時高位在前、低位在後，而且「還有更多」的旗標設在**後面**的 byte 上——這和一般的 varint（LEB128）方向相反，正是為了從尾端讀。

**這題的心法**：資料格式的程式碼，**拿一份真實資料對著讀**比看任何註解都快。`DEBUG LISTPACK`、`DEBUG ZIPLIST`、`OBJECT ENCODING` 這類除錯指令，是 Redis 給讀碼者的禮物。

**用到的技巧**：Ch 9（前綴碼、二補數、變長整數）、Ch 10（指標往回走 `p--`）、Ch 13（一組 `IS_*` 巨集的命名規律）。

</details>

---

## 測試用例（自我評分表）

把你的解碼筆記和下表對照。每題的「必須答出」是及格線。

| 題 | 必須答出 | 常見錯誤答案 | 你答錯代表卡在 |
|---|---|---|---|
| 1 | gcc build 用 computed goto；`vmcase` 被 `ljumptab.h` 重新定義 | 「`vmcase` 就是 `case`」 | 慣用法（沒注意 `#undef`）/ 間接（IDE 跳錯定義） |
| 2 | `0x01000122` = ADD A=2 B=0 C=1；sC 是 excess-127 | 把 C=128 讀成 +128 | 慣用法（位元格式） |
| 3 | `pc++` 跳過緊接的 MMBIN；契約來自 `lcode.c` | 「`pc++` 是多餘的」 | 脈絡（跨檔案不變量） |
| 4 | `0x44`；nil 有三種 variant，`l_isfalse` 要全接 | 「`checktag`/`checktype` 一樣」 | 慣用法（tag 結構） |
| 5 | 正式 build 下是純指標轉型、0 條指令；條件被丟掉 | 「有執行期型別檢查」 | 慣用法（條件編譯改變巨集意義） |
| 6 | 邊界 32/256/65536；空字串用 type 8 | 「空字串用 type 5」 | 慣用法 + 沒有實際跑 |
| 7 | 低 3 bit 三種意義；依賴 8-byte 對齊 | 「`& 1` 是在檢查奇偶數 key 值」 | 間接（指標的真實型別藏在位元裡） |
| 8 | `n` 是 bucket 數；新 key 進表 1；縮小時 mask 即可 | 「`n` 是 key 數」 | 領域（單執行緒延遲預算） |
| 9 | 前綴碼解碼正確；backlen 在尾端、反向變長 | `df fd` 解成 8189 | 慣用法（二補數）/ 領域（為什麼要反向走訪） |

## 延伸挑戰（加分）

- **挑戰一：完整的 listpack 反組譯器**。寫一支程式讀 `DEBUG LISTPACK` 之外的來源——例如用 `DUMP mylist` 取得序列化後的 bytes，自己解析 RDB 格式找到 listpack 本體，再逐元素解碼。對照 `listpack.c` 的 `lpGet`。
- **挑戰二：量 computed goto 的效果**。分別用預設與 `-DLUA_USE_JUMPTABLE=0` build 兩份 Lua，跑同一個 CPU 密集的 Lua 腳本（例如遞迴 fib(32)）各十次取中位數。用 `perf stat -e branch-misses`（若環境允許）比較分支預測失誤數。寫下你的結論與實驗條件（CPU 型號、gcc 版本、優化等級）。
- **挑戰三：驗證「SDS 永遠是奇數指標」**。修改第 6 題的 `sdstype.c`，對長度 0 到 70000 的每一種長度建立 sds，檢查 `(uintptr_t)s & 1` 是否全部為 1。再改用 jemalloc 版的 `zmalloc` 重做一次。有沒有任何例外？
- **挑戰四：同樣的方法讀一段 SQLite**。SQLite 的 `vdbe.c` 也是一個大型 switch 直譯器，`sqlite3VdbeExec` 裡的 `case OP_Add:` 等。比較它和 Lua 在分派、型別表示（SQLite 的 `Mem` 結構）上的異同。

## 自我檢核

- [ ] 拿到一段充滿巨集的程式碼，我的第一個動作是什麼？第二個？（如果答案不是「找定義」和「`gcc -E` 驗證」，回去重讀 Ch 13。）
- [ ] 我能不看解答，解釋 Lua 為什麼在 `ADD` 之後總是有一條 `MMBIN`，以及 VM 怎麼利用它。
- [ ] 我能說出至少兩種「型別資訊不在型別系統裡」的手法（tag 欄位、指標低位元、前綴碼），並說出讀它們時的共同方法。
- [ ] 九題中，我答錯最多的屬於哪一道鴻溝？針對那一道，我下一步要補什麼？
- [ ] 我能說出自己的實驗和參考解答的實驗有什麼不同，各自驗證了什麼、沒驗證什麼。

Part 2 到此結束。你已經能讀懂標準 C 加上常見慣用法寫成的 user-space 程式。Kernel 用的不是標準 C：它大量依賴 GNU 擴充，有自己的錯誤慣例、記憶體配置器、物件生命週期規則。下一個 Part 從 kernel C 方言的第一課開始。

→ [Ch 15 GNU C 擴充：statement expression、typeof、attribute、flexible array](./15-kernel-c-gnu-extensions.md)
