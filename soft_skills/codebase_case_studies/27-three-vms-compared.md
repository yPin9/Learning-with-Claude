# Ch 27 — 三個 VM 橫向對照：pattern 遷移的高光

> **目標**：把 Lua、SQLite VDBE、CPython 三個 bytecode VM 的 dispatch 並排，逐項對照（register vs stack、指令編碼、dispatch 機制、值表示），親眼看到「這三個東西是同一個 pattern 的三種變體」。讀懂第一個花你三天，讀懂第三個花你三十分鐘——這章證明給你看為什麼。

> **目標codebase**：Lua `v5.4.7`（`1ab3208`）、SQLite `version-3.47.2`（`262de1b`）、CPython `v3.13.1`（`0671451`）。三份都在 Ch 0 clone 過。

## 為什麼需要這個？

你在 Part 1 讀了 Lua 的 `luaV_execute`，Part 2 讀了 SQLite 的 `sqlite3VdbeExec`，Part 5 讀了 CPython 的 `_PyEval_EvalFrameDefault`。當時每一個都是硬仗：第一次看到 `luaV_execute` 那個上千行的 switch，你得花時間搞懂 opcode 怎麼取、operand 藏在指令的哪幾個 bit、值到底存哪。

但如果你按順序讀完三個，會浮現一個令人不安的既視感：**這三個東西幾乎一模一樣。** 都是「取指令 → 解碼 → 分派到對應處理 → 執行 → 回圈」。差別只在細節：register 還是 stack、switch 還是 computed goto、值用 tagged union 還是 boxed pointer。

這不是巧合。**bytecode VM 是一個成熟到有標準結構的 pattern。** 一旦你把這個結構 chunk 成一個心智單位（`reading_code` Ch 1 講的 chunking），第三個 VM 你不再從零讀——你帶著「VM 的骨架長這樣」的模板去，剩下的是填三個空：register/stack？怎麼 dispatch？值怎麼表示？三十分鐘定位完畢。

這一章就是把三個 VM 拆開並排，讓那個 pattern 顯形。這是全課「pattern 遷移」主張的證明題。

## 先建立直覺：所有 bytecode VM 的共同骨架

不管哪個 VM，主迴圈都長這樣：

```
   ┌─────────────────────────────────────────────┐
   │  loop:                                        │
   │    opcode = decode(*pc)      ← 取指令 + 解碼   │
   │    pc++                       ← 前進           │
   │    switch/goto (opcode) {     ← 分派           │
   │      case OP_A: ...; next     │               │
   │      case OP_B: ...; next     │  一個 case     │
   │      ...                      │  = 一條指令     │
   │    }                          │               │
   │    goto loop                  ← 回圈           │
   └─────────────────────────────────────────────┘
```

三個 VM 全部符合這個骨架。它們的不同，全是這個骨架上的**參數選擇**。把這張圖記牢，下面的對照全是在填空。

三個維度決定一個 VM 的「個性」：

```
   維度 1：值放哪？         維度 2：怎麼分派？        維度 3：值怎麼表示？
   ┌──────────────┐        ┌──────────────┐        ┌──────────────┐
   │ register     │        │ switch        │        │ tagged union │
   │  （陣列索引）│        │  （可攜）     │        │  （值內嵌）  │
   │      vs      │        │      vs      │        │      vs      │
   │ stack        │        │ computed goto │        │ boxed object │
   │  （push/pop）│        │  （快）      │        │  （堆上物件）│
   └──────────────┘        └──────────────┘        └──────────────┘
   Lua/SQLite=reg          Lua/CPython 可 goto      Lua/SQLite=tagged
   CPython=stack           SQLite=switch            CPython=boxed
```

## 對照一：register vs stack

這是三個 VM 最根本的分歧。**指令的 operand 指向哪裡？**

**Lua 是 register machine。** operand 是「暫存器編號」，直接當索引去查一個平坦的陣列（call frame 的 stack window）。`OP_ADD` 這條指令自己帶著三個編號：兩個來源、一個目的。看 `luaV_execute` 裡 `OP_ADD` 前面那組取值 macro（`lvm.c:906` 附近的 `op_arith` 展開）——它用 `RA(i)`、`GETARG_B`、`GETARG_C` 從指令直接算出暫存器位置。加法的結果直接寫回目的暫存器，不動 stack top。

```c
/* lvm.c:1460 (v5.4.7) */
      vmcase(OP_ADD) {
        op_arith(L, l_addi, luai_numadd);
        vmbreak;
      }
```

`op_arith` 展開後會用 `StkId ra = RA(i);`（`lvm.c:906`）取出目的暫存器 `ra`，operand B/C 取兩個加數。**一條指令直接說清楚「把 R[B] + R[C] 放進 R[A]」**，不需要 push/pop。

**SQLite VDBE 也是 register machine。** operand `p1`/`p2`/`p3` 是 `aMem[]` 這個 Mem cell 陣列的索引。看 `OP_Add` 的 case，它從 `&aMem[pOp->p1]` 和 `&aMem[pOp->p2]` 取兩個運算元，結果寫到 `&aMem[pOp->p3]`。跟 Lua 同一個路數：**指令自己攜帶「來源、來源、目的」的索引**。

```
   SQLite VDBE 指令格式（每條固定 5 個欄位）：
   ┌────────┬────┬────┬────┬─────┬─────┐
   │ opcode │ p1 │ p2 │ p3 │ p4  │ p5  │
   └────────┴────┴────┴────┴─────┴─────┘
              └─ p1/p2/p3 多半是 aMem[] 的索引（= register 編號）
```

**CPython 是 stack machine。** operand 大多是「常數表索引」或「local 變數槽索引」，運算元本身在一個值 stack 上。`BINARY_OP` 不帶運算元位置——它預設兩個運算元就在 stack 頂端，pop 兩個、算、push 一個。看 `bytecodes.c` 的 `_BINARY_OP_ADD_INT`：

```c
/* Python/bytecodes.c:450 (v3.13.1) */
        pure op(_BINARY_OP_ADD_INT, (left, right -- res)) {
```

那個 `(left, right -- res)` 是 CPython 3.13 的 DSL 語法：**箭頭左邊 `left, right` 是從 stack pop 的輸入，右邊 `res` 是 push 回去的輸出**。整條指令沒有任何暫存器編號——運算元的位置是隱含的（stack top）。這就是 stack machine 的特徵：指令更短（不用編碼 operand 位置），但同一件事要更多條指令（先 LOAD 把值搬上 stack，才能 BINARY_OP）。

**取捨**（這是 register vs stack 的經典辯論，你現在有三份真 source 佐證）：

| | register（Lua、SQLite） | stack（CPython） |
|---|---|---|
| 指令數 | 少（`a=b+c` 一條 ADD 搞定） | 多（LOAD b、LOAD c、ADD、STORE a） |
| 指令寬度 | 寬（要編碼 A/B/C 三個位置） | 窄（operand 少或無） |
| 解碼複雜度 | 高（要拆 bit field） | 低 |
| dispatch 次數 | 少（每條做更多事） | 多（每條做更少事） |

Lua 選 register 正是為了少 dispatch（dispatch 是 VM 的主要開銷）；CPython 選 stack 是歷史包袱 + 編譯器簡單。你讀 code 時的判斷法：**看 `OP_ADD`/`BINARY_OP` 這條指令帶不帶「來源位置」的 operand。帶 → register；不帶、預設在 stack 上 → stack。** 一眼分辨。

## 對照二：指令編碼

**Lua：32-bit 定長指令，bit field 塞進 opcode + A/B/C。** `Instruction` 是 `unsigned int`（`llimits.h`）。取值靠 `GET_OPCODE`、`GETARG_A`、`GETARG_B`、`GETARG_C` 這組 macro（`lopcodes.h`）在 32 bit 裡切位置。`vmfetch()` 一次抓一個 `Instruction`：

```c
/* lvm.c:1138 (v5.4.7) */
#define vmfetch()	{ \
  if (l_unlikely(trap)) {  /* stack reallocation or hooks? */ \
    trap = luaG_traceexec(L, pc);  /* handle hooks */ \
    updatebase(ci);  /* correct stack */ \
  } \
  i = *(pc++); \
}
```

`i = *(pc++)` 一句話：取一個 32-bit 指令、pc 前進一個 word。定長 = 解碼快、pc 前進固定。

**SQLite：結構化指令，不是 packed bits。** 每條指令是一個 `VdbeOp` struct（有 `opcode`、`p1`、`p2`、`p3`、`p4`、`p5` 欄位），迴圈用 `pOp++` 走一個 struct。**它不省空間，換來的是可讀性和除錯性**——你可以 `EXPLAIN` 一個 query 直接印出人類可讀的 opcode 表。這是 SQLite「防禦式 C」哲學的延伸（Part 2 Ch 11）：清晰優先於緊湊。

```c
/* vdbe.c:898 (v3.47.2) —— 迴圈本體：pOp 是指向 VdbeOp struct 的指標 */
  for(pOp=&aOp[p->pc]; 1; pOp++){
```

**CPython：變長 code unit + inline cache。** 指令是 `_Py_CODEUNIT`（opcode + oparg 各一 byte 的基本單位），但 3.11 之後導入 inline caching——某些指令後面跟著若干 cache 用的 code unit。`next_instr` 前進的步數因指令而異。這是三個裡最複雜的編碼，因為它服務於 CPython 的 adaptive specialization（同一個 `BINARY_OP` 執行時會就地特化成 `BINARY_OP_ADD_INT`）。

| | Lua | SQLite VDBE | CPython |
|---|---|---|---|
| 指令單位 | 32-bit packed word | `VdbeOp` struct | `_Py_CODEUNIT`（+ inline cache）|
| 定長？ | 定長 | 定長（struct 大小固定）| 變長（cache 尾隨）|
| operand 取法 | bit field macro | struct 欄位 `pOp->p1` | oparg byte + `NEXTOPARG` |
| 為什麼這樣選 | 極致速度/密度 | 可讀、可 EXPLAIN、可除錯 | 支援 runtime 特化 |

## 對照三：dispatch 機制（switch vs computed goto）

這是效能最敏感的部分。三個 VM 各有立場。

**Lua：預設 computed goto，可退回 switch。** 關鍵在 `lvm.c:38` 附近：如果編譯器支援（GCC/Clang 的 label-as-value 擴充），`LUA_USE_JUMPTABLE` 為 1。它用巧妙的 macro trick：**同一份 case body，靠重新定義 `vmdispatch`/`vmcase`/`vmbreak` 三個 macro，在 switch 和 computed goto 之間切換。**

switch 版本（`lvm.c:1146`）：
```c
#define vmdispatch(o)	switch(o)
#define vmcase(l)	case l:
#define vmbreak		break
```

computed goto 版本（`ljumptab.h`，被 `lvm.c:1158` 條件 include）：
```c
/* ljumptab.h (v5.4.7) */
#define vmdispatch(x)     goto *disptab[x];
#define vmcase(l)     L_##l:
#define vmbreak		vmfetch(); vmdispatch(GET_OPCODE(i));
```

看懂這個 trick 是這章的高光：**`vmcase(OP_ADD)` 在 switch 模式展開成 `case OP_ADD:`，在 goto 模式展開成 label `L_OP_ADD:`。** 一份原始碼、兩種 dispatch。`disptab[]` 是一張 `&&L_OP_MOVE, &&L_OP_LOADI, ...` 的 label 位址表（`ljumptab.h`），`goto *disptab[opcode]` 直接跳到對應 label，省掉 switch 的邊界檢查與間接跳表。這是「一開始你以為是 switch，讀下去發現是 computed goto」的經典 indirection——被 macro 騙到很正常。

**SQLite：純 switch。** `vdbe.c:981` 就是 `switch( pOp->opcode ){`，之後幾千行 `case OP_xxx:`。SQLite 刻意不用 computed goto——它要極致可攜（跑在你想得到的所有平台/編譯器上），不賭 GCC 擴充。這又是防禦式 C 的取捨：**放棄一點速度，換所有平台都能編。**

**CPython：computed goto（`USE_COMPUTED_GOTOS`），可退回 switch。** 跟 Lua 同思路但實作不同。`ceval_macros.h:73`：

```c
/* Python/ceval_macros.h:73 (v3.13.1) */
#if USE_COMPUTED_GOTOS
#  define TARGET(op) TARGET_##op:
#  define DISPATCH_GOTO() goto *opcode_targets[opcode]
#else
#  define TARGET(op) case op: TARGET_##op:
#  define DISPATCH_GOTO() goto dispatch_opcode
#endif
```

跟 Lua 一模一樣的 pattern：`TARGET(op)` 在 goto 模式是 label、switch 模式是 `case`；`DISPATCH_GOTO()` 是 `goto *表[opcode]` 或落回 switch。連「巨大 switch 塞在一個 `#if !USE_COMPUTED_GOTOS` 的 `switch(opcode)` 裡」（`ceval.c:781`）都跟 Lua 同構。**你在 Lua 認得的 dispatch trick，直接搬過來讀 CPython——這就是 pattern 遷移的複利。** CPython 的 `DISPATCH()` 還多包一層 `NEXTOPARG()`（取下一條 oparg）：

```c
/* Python/ceval_macros.h:109 (v3.13.1) */
#define DISPATCH() \
    { \
        NEXTOPARG(); \
        PRE_DISPATCH_GOTO(); \
        DISPATCH_GOTO(); \
    }
```

| | Lua | SQLite VDBE | CPython |
|---|---|---|---|
| 預設 dispatch | computed goto（可退 switch）| switch（永遠）| computed goto（可退 switch）|
| macro trick | `vmdispatch/vmcase/vmbreak` 重定義 | 無，直白 switch | `TARGET/DISPATCH_GOTO` 重定義 |
| 為什麼 | 速度優先，賭有 GCC 擴充 | 可攜優先，不賭擴充 | 速度優先，賭有 GCC 擴充 |

## 對照四：值表示（tagged union vs boxed object vs Mem cell）

「一個值在記憶體裡長什麼樣」是三個 VM 的世界觀差異。

**Lua：tagged union（值內嵌）。** `TValue` = 一個 union `Value` + 一個 tag byte `tt_`：

```c
/* lobject.h:49 (v5.4.7) */
typedef union Value {
  struct GCObject *gc;    /* collectable objects */
  void *p;         /* light userdata */
  lua_CFunction f; /* light C functions */
  lua_Integer i;   /* integer numbers */
  lua_Number n;    /* float numbers */
  lu_byte ub;
} Value;
/* lobject.h:65 */
#define TValuefields	Value value_; lu_byte tt_
```

小值（integer、float、bool、nil）**直接內嵌在 `TValue` 裡**，不上堆。只有需要 GC 的大東西（string、table、function）才用 `gc` 指標指向堆上的 `GCObject`。**tag 決定 union 現在是哪一種。** 一個 `TValue` 在 64-bit 上大約 16 byte，複製一個值就是複製這 16 byte，快。

**SQLite：Mem cell（比 tagged union 更肥的變體）。** `sqlite3_value`（也就是 `Mem`）也是 union + flags，但欄位多得多：

```c
/* vdbeInt.h:225 (v3.47.2) */
struct sqlite3_value {
  union MemValue {
    double r;           /* Real value */
    i64 i;              /* Integer value */
    int nZero;
    const char *zPType;
    FuncDef *pDef;
  } u;
  char *z;            /* String or BLOB value */
  int n;              /* length */
  u16 flags;          /* MEM_Null, MEM_Str, MEM_Int, ... */
  u8  enc;            /* text encoding */
  ...
  sqlite3 *db;
  ...
};
```

同樣是 tagged union 的世界觀（`flags` 就是 tag），但 SQLite 的值可能是任意長 blob/string，所以 cell 帶了 `z`（資料指標）、`n`（長度）、`zMalloc`（自管的緩衝）。**這是「tagged union」pattern 為了資料庫值型別（含變長 blob、多種文字編碼）撐大的版本。** 你認得 Lua 的 `TValue` 之後看這個，一眼就懂 `flags` 是 tag、`u` 是 union——只是欄位變多。

**CPython：everything is a boxed object。** 沒有 tagged union。**每一個值都是堆上的 `PyObject`，連小整數也是。** stack 上放的是 `PyObject*` 指標：

```c
/* Include/object.h:163 (v3.13.1) —— 概念版 layout */
struct _object {
    ...
    Py_ssize_t ob_refcnt;   /* 引用計數 */
    PyTypeObject *ob_type;  /* 型別指標 */
};
```

每個物件開頭都有 `ob_refcnt`（refcount）+ `ob_type`（指向型別物件，型別決定它能做什麼）。**值不內嵌、全在堆上、靠指標傳遞、靠 refcount 管生死。** 這就是為什麼 CPython 的 `LOAD_FAST` 之後總要 `Py_INCREF(value)`（`bytecodes.c:234`）：

```c
/* Python/bytecodes.c:232 (v3.13.1) */
        replicate(8) pure inst(LOAD_FAST, (-- value)) {
            value = GETLOCAL(oparg);
            assert(value != NULL);
            Py_INCREF(value);
        }
```

把一個 local「載入」到 stack，本質是複製一個指標 + refcount 加一。這是 Lua/SQLite 完全沒有的負擔（它們小值內嵌，複製不涉及 refcount），也是 CPython 慢的一大原因，但換來統一的物件模型（任何值都有 type、都能被 introspect）。

| | Lua `TValue` | SQLite `Mem` | CPython `PyObject*` |
|---|---|---|---|
| 世界觀 | tagged union，小值內嵌 | tagged union（肥版），變長值帶指標 | 全 boxed，值在堆上 |
| tag 在哪 | `tt_` byte | `flags` (u16) | `ob_type` 指標 |
| 小整數 | 內嵌 `i` | 內嵌 `u.i` | 堆上 `PyLongObject` |
| 複製一個值 | copy 16 byte | copy cell（可能 shallow）| copy 指標 + `Py_INCREF` |
| 記憶體管理 | GC 掃 `gc` 指標的物件 | 自管 `zMalloc` + db | refcount + 循環 GC |

## 底層機制：一張大對照表

把四個維度收成一張表——這是這章要你帶走的東西。以後遇到第四個 VM，你就照這張表的欄位一格一格填：

| 維度 | **Lua** `luaV_execute` | **SQLite** `sqlite3VdbeExec` | **CPython** `_PyEval_EvalFrameDefault` |
|---|---|---|---|
| 檔案 | `lvm.c:1151` | `vdbe.c:813` | `Python/ceval.c:682` |
| 機器類型 | register | register | stack |
| operand 指向 | 暫存器編號（stack window 索引）| `aMem[]` 索引（p1/p2/p3）| 常數/local 槽索引；運算元在值 stack |
| 指令編碼 | 32-bit packed（bit field）| `VdbeOp` struct | `_Py_CODEUNIT` + inline cache |
| 取指令 | `i = *(pc++)`（`vmfetch`）| `pOp++`（走 struct）| `NEXTOPARG()` + `next_instr` |
| dispatch | computed goto / switch（macro 切）| 純 switch | computed goto / switch（macro 切）|
| dispatch macro | `vmdispatch/vmcase/vmbreak` | 直白 `switch(pOp->opcode)` | `TARGET/DISPATCH_GOTO/DISPATCH` |
| 值表示 | `TValue`（tagged union）| `Mem`（肥 tagged union）| `PyObject*`（boxed）|
| tag 位置 | `tt_` byte | `flags` u16 | `ob_type` 指標 |
| 記憶體管理 | 增量 GC（Part 1 Ch 6）| 自管 + connection | refcount + 循環 GC（Ch 24）|
| 設計優先 | 速度/密度 | 可攜/可除錯 | 統一物件模型/introspection |
| 一句話 | 最省的 register VM | 最好除錯的 register VM | 最統一的 stack VM |

**這張表就是 pattern 的具體化。** 「bytecode VM」這個 chunk，在你腦裡不再是一團模糊——它是一張有十二個欄位的模板。讀新 VM = 填這十二格。

## 證明題：用 30 分鐘上手第四個 VM（jq）

這章的主張是「讀懂三個，第四個 30 分鐘上手」。不空口說——現場做給你看。目標 jq（`jqlang/jq`，一個 JSON 處理器，自帶 bytecode VM）。我不預先讀 jq，就照上面那張十二欄模板，一格一格填。

**動作 1：找主迴圈（beacon：`while`/`for(;;)` + `switch(opcode)`）。** `rg` 一下：

```bash
$ rg -n "while \(1\)|switch \(opcode\)|uint16_t opcode" src/execute.c
351:  while (1) {
357:    uint16_t opcode = *pc;
400:    switch (opcode) {
```

命中。`jq_next`（`src/execute.c:340`）就是它的 dispatch loop：

```c
/* src/execute.c:340,351,357,400 (jqlang/jq) 節選 */
jv jq_next(jq_state *jq) {
  ...
  while (1) {
    uint16_t opcode = *pc;
    ...
    pc++;
    switch (opcode) {
```

**填欄位（照模板三問）：**

- **register 還是 stack？** 看一條真指令。`DUP` 這個 case（`execute.c:424`）：
  ```c
  case DUP: {
    jv v = stack_pop(jq);
    stack_push(jq, jv_copy(v));
    stack_push(jq, v);
    break;
  }
  ```
  `stack_pop`/`stack_push`——**stack machine**。指令不帶運算元位置，預設在 stack 上。（跟 CPython 同派。）
- **怎麼 dispatch？** 上面是 `pc++; switch(opcode)`——**純 switch**，沒有 computed goto。（跟 SQLite 同派：可攜優先，不賭 GCC 擴充。）
- **值怎麼表示？** `rg "typedef struct" src/jv.h` 找到 `jv`：
  ```c
  /* src/jv.h:34 節選 */
  typedef struct {
    unsigned char kind_flags;
    ...
    union { struct jv_refcnt* ptr; double number; } u;
  } jv;
  ```
  **tagged union**（`kind_flags` 是 tag、`u` 是 union）——小值（`double number`）內嵌、大值（string/array）用 `jv_refcnt* ptr` 指向堆上帶 refcount 的物件。**這是 Lua 的 tagged union（小值內嵌）+ CPython 的 refcount（堆上大值）的混血。**

**30 分鐘不到，jq 的個性填完了**：stack machine（同 CPython）、純 switch（同 SQLite）、tagged-union-混-refcount 的值（Lua + CPython 各學一半）。

**這就是複利。** 我沒有從第一行讀 jq——我帶著三個 VM 練出的模板去，jq 只是在填空。每一格我都能立刻歸類「這格跟誰同派」。第四個 VM 對我不是閱讀理解題，是選擇題。這正是這門課要給你的能力：不是讀懂特定 VM，是把「VM」變成一個你能三十分鐘拆解的 chunk。

## 對比與取捨

| 面向 | register（Lua/SQLite）勝 | stack（CPython）勝 |
|---|---|---|
| 執行速度 | ✓ dispatch 少 | |
| 編譯器簡單 | | ✓ codegen 直白 |
| 指令密度 | 看情況（指令少但寬）| 看情況（指令多但窄）|
| 可除錯/可讀 | SQLite 特別強（EXPLAIN）| CPython dis 模組也不錯 |

| 面向 | computed goto（Lua/CPython）| switch（SQLite）|
|---|---|---|
| 速度 | ✓ 省邊界檢查、幫分支預測 | |
| 可攜性 | 賭 GCC label-as-value 擴充 | ✓ 標準 C，處處能編 |

沒有絕對優劣。**每個選擇都是一組取捨，讀 code 時你要問的不是「哪個對」，而是「這個專案為什麼選這邊」**——答案往往寫在它的設計哲學裡（Lua 要小快、SQLite 要處處能跑且好除錯、CPython 要統一物件模型）。

## 踩雷集錦

1. **以為看到 `switch(opcode)` 就是 switch dispatch。** 錯。Lua 和 CPython 的 switch 只是 computed goto 不可用時的 fallback，真跑起來是 `goto *表[opcode]`。被 macro 騙到很常見——`vmcase`/`TARGET` 這種名字就是要你「看起來像 case、其實是 label」。讀到 dispatch macro 一定要展開兩種模式都看。

2. **以為 register machine 的 register 是 CPU register。** 不是。Lua/SQLite 的「register」是**虛擬**的——是一個記憶體陣列的索引（Lua 是 stack window、SQLite 是 `aMem[]`）。名字借用 CPU 術語，但住在 RAM 裡。

3. **以為 stack machine 一定比 register machine 慢。** 不必然。stack machine 指令多但每條窄、解碼快；register machine 指令少但每條寬、解碼慢。實測誰快取決於工作負載和實作。別憑「stack=慢」的刻板印象下結論——這也是為什麼三個頂尖 runtime 沒有統一選擇。

4. **以為 CPython 的值也內嵌。** 完全相反。CPython 沒有 tagged union，連 `1` 都是堆上的 `PyLongObject`。stack 上是 `PyObject*`。這決定了它每個 LOAD 都要 `Py_INCREF`——你如果帶著 Lua 的 tagged-union 直覺讀 ceval，會對滿地的 INCREF/DECREF 感到困惑。認清「CPython everything is boxed」才讀得順。

5. **以為 SQLite 的 `Mem` 跟 Lua 的 `TValue` 不是同一回事。** 它們是同一個 pattern（tagged union）的兩個尺寸。SQLite 的 cell 肥，是因為要裝變長 blob/多編碼字串/自管緩衝。剝掉那些欄位，`flags` 對應 `tt_`、`u` 對應 `value_`，骨架一致。

## 進階：再往深一層

- **inline caching / adaptive specialization**：CPython 3.11+ 的 `BINARY_OP` 執行時會就地改寫成 `BINARY_OP_ADD_INT` 這種特化版（`bytecodes.c` 裡 `macro(BINARY_OP_ADD_INT) = ...` 在 466 行附近）。這是三個 VM 裡 CPython 獨有的「自我修改 bytecode」機制，Lua/SQLite 沒有。讀懂它需要先讀 `_SPECIALIZE_BINARY_OP`（`bytecodes.c:4050`）。這是「同一個 pattern 上疊了一層優化」的好例子。
- **第四個 VM：jq。** 想驗證「三十分鐘上手第四個」？clone `jqlang/jq`，看 `src/execute.c` 的 `jq_next`（`while(1)` + `switch(opcode)`，stack machine + backtracking）。你會發現照這章的十二欄模板，二十分鐘就填完——這就是複利。
- **從 VM 回頭看編譯器**：三個 VM 都有配套的 bytecode 編譯器（Lua `lcode.c`、SQLite 的 codegen、CPython `compile.c` + `Python/bytecodes.c` 的 DSL）。讀懂 VM 執行什麼，回頭讀「誰產生了這些 bytecode」會快很多——因為你已經知道目標長什麼樣。

## 本章重點整理

- **所有 bytecode VM 共用一個骨架**：取指令 → 解碼 → 分派 → 執行 → 回圈。差異全是這骨架上的參數選擇。
- **四個決定性維度**：register vs stack、指令編碼、dispatch 機制（switch vs computed goto）、值表示（tagged union vs boxed）。
- **Lua = 最省的 register VM**（tagged union、computed goto），**SQLite = 最好除錯的 register VM**（struct 指令、純 switch、防禦式），**CPython = 最統一的 stack VM**（全 boxed object、refcount）。
- **Lua 和 CPython 用同一個 macro trick** 在 switch/computed goto 間切換——你在一個認得的東西，直接搬去讀另一個。
- **pattern 遷移的複利**：把 VM 骨架 chunk 成十二欄模板後，第四個 VM 是填空題，不是閱讀理解題。

## 自我檢核

- [ ] 我能不看教材，說出三個 VM 各是 register 還是 stack，並指出判斷依據（看 `OP_ADD`/`BINARY_OP` 帶不帶 operand 位置）
- [ ] 我能解釋 Lua/CPython 的 `vmcase`/`TARGET` macro 為什麼「看起來像 case 其實可能是 label」
- [ ] 我能說出為什麼 SQLite 選純 switch 而 Lua/CPython 賭 computed goto（可攜 vs 速度）
- [ ] 我能對照 `TValue`、`Mem`、`PyObject*` 三種值表示的世界觀差異，並解釋為什麼 CPython 每個 LOAD 要 INCREF
- [ ] 我能拿一個沒讀過的 VM（如 jq），照十二欄模板在半小時內填出它的個性

## 延伸閱讀

- **Lua 官方論文《The Implementation of Lua 5.0》**（Ierusalimschy 等，2005）
  - **讀哪裡**：第 7 節「The Virtual Machine」與 register-based 設計的動機；本課讀 `luaV_execute` 前後看，理解「為什麼 Lua 5.0 從 stack 改成 register VM」
  - **前提**：讀過 Part 1 的 Lua VM 章
- **CPython Developer's Guide — Changing CPython's Bytecode / Interpreter**（[devguide.python.org/internals](https://devguide.python.org/internals/interpreter/)）
  - **讀哪裡**：解釋 `bytecodes.c` 的 DSL 與 `generated_cases.c.h` 怎麼生成；本課 Ch 23 的官方補充，讀懂 `(left, right -- res)` 語法從哪來
  - **前提**：讀過 Part 5 的 ceval 章
- **《Crafting Interpreters》** — Robert Nystrom（第 III 部 clox）
  - **讀哪裡**：第 14–17、23 章，親手寫一個 stack-based bytecode VM + computed goto dispatch；把這章「讀」三個 VM 的知識，用「寫」一個 VM 來固化
  - **前提**：C 基礎；讀完能回頭把三個真 VM 看得更透
- **SQLite 官方《The Virtual Database Engine of SQLite》**（[sqlite.org/opcode.html](https://www.sqlite.org/opcode.html)）
  - **讀哪裡**：opcode 參考 + `EXPLAIN` 輸出解讀；本課 Part 2 Ch 9 的官方對照，理解為什麼 VDBE 選 struct 指令（可 EXPLAIN 出人類可讀表）
  - **前提**：讀過 Part 2 的 VDBE 章

三個 VM 對照完，你手上有了「填空模板」式的讀碼能力。下一章我們把這種能力用在一個你**完全沒背景**的大 C 專案——PostgreSQL 的 executor。這次不再是 bytecode VM，是另一種 query 執行模型：火山模型的節點樹。

→ [Ch 28 Capstone：冷讀 PostgreSQL executor](./28-capstone-postgres-executor.md)
