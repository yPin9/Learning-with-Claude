# Final Project — 迷你 stack VM 直譯器

> **目標**：從零寫一個可用的 stack 虛擬機器 `svm`：文字組譯器把組合語言變成 32 位元指令字，直譯器以 `switch` 與 computed goto 兩種方式分派，值是 tagged union，記憶體由 arena 管理，原生函式從 `dlopen` 載入的共享程式庫註冊進來，bytecode 可以存成與主機無關的檔案再載回。全程開著 `-fsanitize=address,undefined` 跑單元測試。做完這個專案，等於把整門課 70% 以上的核心概念在一個真實系統裡各用過一次。

> **對應原文**：本專案為本課自行設計的整合題，技術來源分散於 [前置處理器應用篇](https://hackmd.io/@sysprog/c-preprocessor) 的「X macro」、[goto 和流程控制篇](https://hackmd.io/@sysprog/c-control-flow) 的「computed goto」、[動態連結器篇](https://hackmd.io/@sysprog/c-dynamic-linkage) 的「dlopen」等節

> **環境**：WSL Ubuntu 22.04 x86_64，gcc 11.4、clang 14、glibc 2.35、GNU make。computed goto（labels as values）是 GNU C 擴充，以 `-std=gnu11` 編譯；gcc 與 clang 都支援。

## 背景與動機

你每天都在用直譯器：CPython、Lua、Ruby 的 YARV、Erlang 的 BEAM、eBPF 的直譯模式、甚至 SQLite 的查詢引擎（VDBE）都是 bytecode 虛擬機器。它們的骨架驚人地一致：

```
   原始程式 (文字)                                          ┌─────────── libsvm.so ───────────┐
  ┌──────────────┐   svm_assemble()   ┌──────────────┐      │                                  │
  │ PUSH 30      │ ─────────────────► │ 0x01_00001e  │      │  ┌──────────┐    ┌────────────┐ │
  │ CALL fib     │   兩趟：收集標籤   │ 0x15_000004  │ ───► │  │ verify() │───►│ exec_goto/ │ │
  │ PRINT        │   → 編碼指令       │ 0x18_000000  │      │  │ 一次檢查 │    │ exec_switch│ │
  │ ...          │                    │ ...          │      │  └──────────┘    └─────┬──────┘ │
  └──────────────┘                    └──────┬───────┘      │                        │ NCALL  │
                                     svm_save│▲svm_load     │   natives (intrusive   ▼        │
                                             ▼│             │   list + 索引表) ◄── isqrt, gcd │
                                      ┌──────────────┐      │                        ▲        │
                                      │ prog.svmb    │      └────────────────────────┼────────┘
                                      │ 小端序 + 原生 │                   dlopen()   │
                                      │ 函式名稱表    │              ┌───────────────┴──┐
                                      └──────────────┘              │ libsvm_math.so    │
                                                                    │ svm_plugin_init() │
                                                                    └───────────────────┘
```

SQLite 的 VDBE、Lua 5.x 的 `lvm.c` 都用類似的「opcode 表 + 分派迴圈」；CPython 3.x 的 `ceval.c` 用 `TARGET()` 巨集讓同一份指令本體同時支援 `switch` 與 computed goto——我們的 `vm_exec.inc` 用的是同一招。這不是玩具題：做完之後再去讀 `ceval.c` 或 `lvm.c`，你會認得每一個結構。

> Brainfuck 直譯器與 JIT 在 [Ch 38](./38-compiler-construction.md) 與 [練習 E](./practice-e-malloc-tracer-and-bf-jit.md) 做過；本專案的差別是有型別的值、函式呼叫、外掛與檔案格式，規模更接近真實的 VM。

### 這個專案整合了哪些章節

| 元件 | 用到的概念 | 回看 |
|---|---|---|
| `opcodes.def` 產生 enum、名稱表、運算元種類表、computed goto 標籤表 | X macro | [Ch 28](./28-preprocessor-advanced.md) |
| `vm_exec.inc` 被 `#include` 兩次，產生兩個分派器 | 前置處理器元程式設計、`#undef` 重定義 | [Ch 27](./27-preprocessor-basics.md)、[Ch 28](./28-preprocessor-advanced.md) |
| `exec_switch` vs `exec_goto` | switch 跳躍表、computed goto、分支預測 | [Ch 31](./31-goto-and-control-flow.md)、[Ch 37](./37-compiler-optimization.md) |
| `svm_value` | tagged union、`_Bool`、複合字面值 | [Ch 32](./32-tricks-syntax-and-resources.md)、[Ch 2](./02-types-and-objects.md) |
| `arena.c` | bump allocator、對齊、flexible array member、`max_align_t` | [Ch 16](./16-alignment-and-padding.md)、[Ch 17](./17-malloc-internals.md)、[練習 B](./practice-b-my-malloc.md) |
| `list.h` | intrusive list、`container_of`、`offsetof` | [Ch 19](./19-linux-list-head.md) |
| `svm_load()`、`svm_load_plugin()` | goto 清理鏈 | [Ch 31](./31-goto-and-control-flow.md) |
| `ADD/SUB/MUL/DIV/MOD/NEG` | `__builtin_*_overflow`、`INT64_MIN / -1` | [Ch 22](./22-integer-overflow.md) |
| 32 位元指令字、24 位元有號運算元、小端序檔案 | 位元欄位編碼、符號擴充、位元組序 | [Ch 21](./21-number-systems.md)、[Ch 23](./23-bitwise-basics.md) |
| `libsvm.so`、`libsvm_math.so`、`dlopen`/`dlsym`、`$ORIGIN` | 共享程式庫、符號解析、rpath | [Ch 39](./39-static-linking.md)、[Ch 40](./40-dynamic-linking.md) |
| 原生函式表、`svm_native_fn` | 函式指標、`void *` ↔ 函式指標 | [Ch 9](./09-void-pointer-and-callbacks.md) |
| 不透明的 `struct svm` | 封裝、不完整型別 | [Ch 29](./29-oop-in-c.md) |
| `CALL`/`RET` 回返堆疊、`STACK_MAX` | stack frame、遞迴深度 | [Ch 11](./11-process-layout-and-abi.md)、[Ch 13](./13-recursion.md) |
| `test_vm.c`、`make san` | unit test、ASan/UBSan | [練習 D](./practice-d-unit-test-framework.md)、[Ch 0](./00-environment-setup.md)、[Ch 34](./34-undefined-behavior.md) |
| 組譯器的 `strtoll` + `errno` + 結尾檢查、`strtok_r` | 字串處理與錯誤回報 | [Ch 10](./10-strings-and-null.md) |
| 嵌入式考題的回聲：`volatile` 不需要、`static` 隔離內部符號、`(getc)` 式的單次求值 | — | [Ch 42](./42-embedded-quiz-part1.md)、[Ch 43](./43-embedded-quiz-part2.md) |

## 任務規格

### 1. 值（tagged union）

```c
typedef enum { SVM_NIL, SVM_INT, SVM_BOOL } svm_type;
typedef struct {
    svm_type type;
    union { int64_t i; _Bool b; } as;
} svm_value;
```

- 整數是 64 位元有號，**所有算術都做溢位檢查**，溢位是執行時期錯誤而不是 UB。
- `EQ`、`LT`、`NOT` 產生 `BOOL`。真假值（`JZ`/`JNZ`/`NOT` 使用）：`BOOL` 看本身、`INT` 非零為真、`NIL` 為假。
- 變數槽（slot）初始為 `NIL`。

### 2. 指令格式（32 位元字）

```
  31        24 23                                    0
 ┌────────────┬──────────────────────────────────────┐
 │  opcode 8  │       operand 24（有號，二補數）       │
 └────────────┴──────────────────────────────────────┘
   0..24        範圍 [-8388608, 8388607]
```

- 編碼：`(uint32_t)op << 24 | ((uint32_t)arg & 0xFFFFFF)`。
- 解碼運算元時**不得**對負數右移（實作定義，C11 6.5.7 §5），改用 `(int32_t)((w & 0xFFFFFF) ^ 0x800000) - 0x800000` 做符號擴充。
- 立即數超出 24 位元時，組譯器自動改用 `PUSHK k`，把值放進 64 位元常數池。

### 3. 指令集

| opcode | 助憶符 | 運算元 | 堆疊效果 | 錯誤條件 |
|---|---|---|---|---|
| 0 | `HALT` | — | `--` | — |
| 1 | `PUSH` | imm24 | `-- imm` | 堆疊滿 |
| 2 | `PUSHK` | 常數索引 | `-- consts[k]` | 堆疊滿 |
| 3 | `POP` | — | `a --` | 堆疊空 |
| 4 | `DUP` | — | `a -- a a` | |
| 5 | `SWAP` | — | `a b -- b a` | |
| 6 | `OVER` | — | `a b -- a b a` | |
| 7–11 | `ADD SUB MUL DIV MOD` | — | `a b -- a op b` | 非整數、溢位、除以 0 |
| 12 | `NEG` | — | `a -- -a` | `INT64_MIN` |
| 13 | `EQ` | — | `a b -- bool` | （型別不同視為不等） |
| 14 | `LT` | — | `a b -- bool` | 非整數 |
| 15 | `NOT` | — | `a -- !a` | |
| 16 | `JMP` | 位址 | `--` | |
| 17 | `JZ` | 位址 | `c --` | 為假則跳 |
| 18 | `JNZ` | 位址 | `c --` | 為真則跳 |
| 19 | `LOAD` | slot 0..255 | `-- slot[n]` | |
| 20 | `STORE` | slot 0..255 | `a --` | |
| 21 | `CALL` | 位址 | 推入回返位址 | 呼叫深度 > 256 |
| 22 | `RET` | — | 彈出回返位址 | 回返堆疊空 |
| 23 | `NCALL` | 原生函式名稱 | `args -- ret` | 原生函式傳回非 0 |
| 24 | `PRINT` | — | `a --` | |

資料堆疊 1024 格、回返堆疊 256 層、變數槽 256 個。`DIV`/`MOD` 採 C 的截斷除法（`-7 / 2 == -3`）。

### 4. 組合語言語法

```
label:  MNEMONIC  operand   ; comment
```

- 一行最多一個標籤、一個指令；助憶符不分大小寫；`;` 之後是註解。
- 整數接受 `strtoll(s, &end, 0)` 的格式（十進位、`0x`、`0` 開頭八進位），整個 token 都要被吃掉才算合法。
- `JMP/JZ/JNZ/CALL` 的運算元是標籤；`NCALL` 的運算元是**已註冊**的原生函式名稱（所以外掛要在組譯前載入）。
- 錯誤訊息格式：`line N: ...`。

### 5. 驗證器（verifier）

組譯完、或從檔案載入後，跑一次 `verify()`：opcode < `OP_COUNT`；跳躍目標在 `[0, ncode)`；slot、常數索引、原生函式索引都在範圍內；**最後一道指令必須是 `HALT`、`JMP` 或 `RET`**（控制流不能掉出程式尾端）。通過驗證後，直譯迴圈不必每圈檢查 `pc` 越界——這是 JVM 驗證器的思路縮小版。

### 6. Bytecode 檔案格式（`.svmb`，全部小端序）

```
 offset  大小        內容
 0       4           magic "SVM1"
 4       4           ncode
 8       4           nconsts
 12      4           nnatives
 16      4*ncode     指令字
 ...     8*nconsts   常數（低 32 位元在前）
 ...     變長        原生函式名稱表：每項 1 位元組長度 + 名稱，依索引順序
```

載入時依**名稱**把每個 `NCALL` 的索引重新對應到目前 VM 的原生函式表——這正是動態連結器做的事（Ch 40 的重定位與符號解析），找不到就回報 `unresolved native`。

### 7. 外掛介面

外掛是一個共享程式庫，匯出：

```c
int svm_plugin_init(svm *vm);   /* 呼叫 svm_register_native() 註冊函式；成功傳回 0 */
```

原生函式型別：`int (*)(svm *vm, const svm_value *args, svm_value *ret)`，傳回非 0 表示失敗。

### 8. 命令列

```
svm [-d switch|goto] [-p plugin.so]... [-o out.svmb] [-t] prog.{asm,svmb}
```

`-d` 選分派器（預設 goto），`-p` 可重複，`-o` 只組譯並存檔，`-t` 在 stderr 印執行時間。錯誤一律印 `svm: <訊息>` 並以結束碼 1 離開。

### 驗收標準

- `make` 在 gcc 與 clang 下 `-Wall -Wextra` 零警告。
- `make test` 全部通過；`make san`（ASan + UBSan）全部通過且無任何 sanitizer 報告。
- 下面的範例程式輸出完全一致；兩種分派器對所有測試輸出、錯誤訊息、最終堆疊頂完全一致。

## 期望輸出範例

**費氏數列（遞迴，只用資料堆疊與回返堆疊）**：

```
; fib.asm -- recursive Fibonacci: prints fib(30)
        PUSH 30
        CALL fib
        PRINT
        HALT

; fib: ( n -- fib(n) )
fib:    DUP
        PUSH 2
        LT
        JNZ base        ; n < 2: fib(n) = n, already on the stack
        DUP
        PUSH 1
        SUB
        CALL fib        ; ( n fib(n-1) )
        SWAP
        PUSH 2
        SUB
        CALL fib        ; ( fib(n-1) fib(n-2) )
        ADD
base:   RET
```

```
$ ./svm fib.asm
832040
```

**迴圈加總（變數槽 + 自動常數池）**：

```
; sum.asm -- 1 + 2 + ... + N with two variables in slots 0 and 1
        PUSH 0
        STORE 0         ; sum = 0
        PUSH 100000000  ; wider than 24 bits: assembled as PUSHK
        STORE 1         ; i = N
loop:   LOAD 1
        JZ done         ; while (i != 0)
        LOAD 0
        LOAD 1
        ADD
        STORE 0         ;   sum += i
        LOAD 1
        PUSH 1
        SUB
        STORE 1         ;   i -= 1
        JMP loop
done:   LOAD 0
        PRINT
        HALT
```

```
$ ./svm sum.asm
5000000050000000
```

（N(N+1)/2 = 10^8 × (10^8 + 1) / 2 = 5000000050000000。）

**溢位是錯誤，不是 UB**：

```
$ ./svm fact.asm          # 每圈印 n!，直到 int64 溢位
1
2
6
...
121645100408832000
2432902008176640000
svm: pc 6: integer overflow in MUL
```

20! = 2432902008176640000 是 `int64_t` 放得下的最大階乘，21! 被 `__builtin_mul_overflow` 攔下。

**外掛與 bytecode 檔**：

```
$ ./svm native.asm
svm: line 5: unknown native 'isqrt'
$ ./svm -p ./libsvm_math.so native.asm
1000000007
21
$ ./svm -p ./libsvm_math.so -o native.svmb native.asm
$ xxd native.svmb | head -2
00000000: 5356 4d31 0a00 0000 0200 0000 0200 0000  SVM1............
00000010: 0000 0002 0100 0002 0000 0009 0000 0017  ................
$ ./svm native.svmb
svm: native.svmb: unresolved native 'isqrt' (load its plugin first)
$ ./svm -p ./libsvm_math.so native.svmb
1000000007
21
```

`native.asm` 計算 isqrt(1000000007²) 與 gcd(1071, 462)。看 hexdump：header 之後第一個字是 `00 00 00 02` ⇒ 小端序的 `0x02000000`，opcode 2（`PUSHK`）、運算元 0——1000000007 超過 24 位元，進了常數池。

**錯誤輸入**：

```
$ ./svm e1.asm      → svm: line 2: undefined label 'nowhere'
$ ./svm e2.asm      → svm: pc 2: division by zero
$ ./svm e3.asm      → svm: program must end with HALT, JMP or RET
$ ./svm -d switch e4.asm → svm: pc 4: ADD needs two ints        (BOOL + INT)
$ ./svm -p ./libsvm_math.so trunc.svmb → svm: trunc.svmb: truncated file
$ ./svm -p ./nope.so native.asm → svm: dlopen: ./nope.so: cannot open shared object file: No such file or directory
```

## 如果你卡住了

1. **X macro 不知道怎麼開始？** 先只寫 `enum`：`#define OP(name, kind, effect) OP_##name,` 然後 `#include "opcodes.def"`，最後加一個 `OP_COUNT`。名稱表用同一個檔案、換一個 `OP` 定義（`[OP_##name] = #name,`）。三張表永遠不會不同步，這就是 X macro 的價值。
2. **switch 和 computed goto 要寫兩份指令本體？** 不用。把本體寫成 `CASE(ADD) ... NEXT;`，在 switch 版裡 `CASE` 是 `case OP_ADD:`、`NEXT` 是 `break`；在 goto 版裡 `CASE` 是標籤 `L_ADD:`、`NEXT` 是 `goto *labels[op]`。把本體放進一個 `.inc` 檔，`#include` 兩次。注意 `#undef` 乾淨，否則第二次 include 會撞名。
3. **24 位元運算元的負數怎麼還原？** 先想 `(int32_t)(w << 8) >> 8` 為什麼不行（有號右移是實作定義），再想「把符號位元翻轉、當無號數、再減掉偏移」：`(x ^ 0x800000) - 0x800000`。用 −1、`SVM_ARG_MIN`、`SVM_ARG_MAX` 驗證。
4. **組譯器的前向參照（`JMP` 到後面的標籤）？** 兩趟：第一趟只數指令並記錄標籤位址，第二趟才編碼。第一趟結束就知道要配多少空間。
5. **`dlsym` 傳回 `void *`，怎麼變成函式指標？** ISO C 不保證 `void *` 與函式指標可互轉，POSIX 保證；`dlsym` 手冊推薦的寫法是 `*(void **)(&fn) = dlsym(h, "name");`。
6. **外掛註冊的名稱字串放哪？** 不能存外掛傳進來的指標——那是外掛 `.rodata` 裡的字串，`dlclose` 之後就懸空。複製一份到 arena。

## 實作步驟建議

### Step 1：值、指令編碼、opcode 表

寫 `vm.h`（`svm_value`、`svm_encode/svm_op/svm_arg`）與 `opcodes.def`。產出：一個測試程式對 `{0, 1, -1, SVM_ARG_MAX, SVM_ARG_MIN}` 做編碼來回，全部相等。

### Step 2：arena 與 intrusive list

寫 `arena_alloc(a, size, align)`：當前 chunk 放得下就把 `used` 對齊後往前推，否則 `malloc` 一個新 chunk（容量取 `max(4096, size + align)`，加法要做溢位檢查）。chunk 的 `data[]` 用 `max_align_t` 型別的 flexible array member，保證 offset 0 滿足任何對齊。`list.h` 照 Linux 的 `list_head` 寫 `list_init`、`list_add_tail`、`container_of`、`list_for_each_entry`。產出：配置 1000 個不同對齊的物件，檢查每個位址都對齊。

### Step 3：直譯器（先只做 switch）

用寫死的指令陣列（還沒有組譯器）跑 `PUSH 2; PUSH 3; ADD; HALT`。加上堆疊上下溢檢查與 `__builtin_*_overflow`。產出：堆疊頂是 5；`INT64_MAX + 1` 回報溢位。

### Step 4：組譯器 + 驗證器

兩趟組譯、標籤表（放在暫存 arena，組譯結束整個釋放）、運算元依種類解析、立即數過寬時改用 `PUSHK`。寫 `verify()`。產出：`fib.asm` 印 832040；各種錯誤輸入得到帶行號的訊息。

### Step 5：computed goto 分派器

把指令本體抽到 `vm_exec.inc`，include 兩次。產出：所有程式在兩種分派器下輸出一致；量兩者的速度。

### Step 6：外掛與共享程式庫

把 VM 編成 `libsvm.so`，主程式以 `-Wl,-rpath,'$ORIGIN'` 連結；寫 `svm_register_native`、`svm_load_plugin`（`dlopen` 失敗、找不到 `svm_plugin_init`、init 失敗三條錯誤路徑都要 `dlclose`）。產出：`native.asm` 正確。

### Step 7：bytecode 存取 + 測試 + sanitizer

小端序的 `put32/get32`、存檔、載入（goto 清理鏈：`out_free` → `out_close`）、名稱重定位、載入後再跑 `verify()`。測試框架照 [練習 D](./practice-d-unit-test-framework.md) 的精神寫一個 `CHECK()` 巨集。產出：`make test` 與 `make san` 全過。

## 測試用例

| 輸入 | 預期結果 | 說明 |
|---|---|---|
| `PUSH 2 / PUSH 3 / MUL / PUSH 4 / SUB / HALT` | 堆疊頂 2 | 基本算術 |
| `fib(20)` | 堆疊頂 6765 | CALL/RET 遞迴 |
| `7 / -2`、`7 % -2` | `-3`、`1` | C 的截斷除法 |
| `PUSH 9223372036854775807 / PRINT` | 原值 | 常數池、`strtoll` 上限 |
| `PUSH -8388609 / PRINT` | 原值 | 比 `SVM_ARG_MIN` 小 1，必須走常數池 |
| `LOAD 5 / PRINT` | `nil` | slot 初值 |
| `INT64_MAX + 1` | `overflow in ADD` | 溢位檢查 |
| `(INT64_MIN) / -1` | `overflow in DIV` | 唯一會溢位的除法 |
| `1 % 0` | `division by zero` | |
| `ADD` 於空堆疊 | `stack underflow` | |
| `BOOL + INT` | `needs two ints` | 型別檢查 |
| `loop: PUSH 1 / JMP loop` | `stack overflow` | 資料堆疊上限 |
| `f: CALL f / RET` | `call stack overflow` | 回返堆疊上限 |
| `PUSH -1 / NCALL isqrt` | `native 'isqrt' failed` | 原生函式回報錯誤 |
| `FOO` | `line 2: unknown mnemonic` | |
| `JMP nowhere` | `undefined label` | |
| 重複標籤 | `duplicate label` | |
| `PUSH`（無運算元） | `needs an operand` | |
| `PUSH 12abc` | `bad integer` | `strtoll` 結尾檢查 |
| `STORE 256` | `slot must be 0..255` | |
| `PUSH 1 / PRINT`（無 HALT） | `must end with` | 驗證器 |
| 只有註解 | `empty program` | |
| 存檔後在沒有外掛的 VM 載入 | `unresolved native 'isqrt'` | 名稱重定位 |
| 同一外掛載入兩次 | `already registered` | init 失敗時要 `dlclose` |
| header 說 1 個字、檔案只有 header | `truncated` | 載入的錯誤路徑 |

每個執行類測試都在兩種分派器下各跑一次，比對輸出、錯誤訊息與堆疊頂。

## 完整參考解答

**寫完再看！不要偷看**，否則學不到東西。

<details>
<summary>點開參考實作（13 個檔案，約 1150 行）</summary>

**`Makefile`**

```makefile
CC      = gcc
CFLAGS ?= -std=gnu11 -O2 -g -Wall -Wextra -fPIC
SAN    := -fsanitize=address,undefined -fno-omit-frame-pointer
RPATH  := -Wl,-rpath,'$$ORIGIN'

all: libsvm.so svm libsvm_math.so test_vm

libsvm.so: vm.c arena.c vm.h arena.h list.h opcodes.def vm_exec.inc
	$(CC) $(CFLAGS) -shared -o $@ vm.c arena.c -ldl

svm: main.c vm.h libsvm.so
	$(CC) $(CFLAGS) -o $@ main.c -L. -lsvm $(RPATH)

libsvm_math.so: plugin_math.c vm.h
	$(CC) $(CFLAGS) -shared -o $@ plugin_math.c

test_vm: test_vm.c vm.h libsvm.so libsvm_math.so
	$(CC) $(CFLAGS) -o $@ test_vm.c -L. -lsvm $(RPATH)

test: all
	./test_vm

san:
	$(MAKE) clean
	$(MAKE) CFLAGS="-std=gnu11 -O1 -g -Wall -Wextra -fPIC $(SAN)" test

clean:
	rm -f libsvm.so libsvm_math.so svm test_vm *.svmb

.PHONY: all test san clean
```

**`opcodes.def`**

```c
/* X macro table: OP(name, operand kind, stack effect)
 * Include this file after defining OP(); every table in vm.c is generated from it. */
OP(HALT,  OPND_NONE,   "--")
OP(PUSH,  OPND_IMM,    "-- imm24")
OP(PUSHK, OPND_KONST,  "-- consts[k]")
OP(POP,   OPND_NONE,   "a --")
OP(DUP,   OPND_NONE,   "a -- a a")
OP(SWAP,  OPND_NONE,   "a b -- b a")
OP(OVER,  OPND_NONE,   "a b -- a b a")
OP(ADD,   OPND_NONE,   "a b -- a+b")
OP(SUB,   OPND_NONE,   "a b -- a-b")
OP(MUL,   OPND_NONE,   "a b -- a*b")
OP(DIV,   OPND_NONE,   "a b -- a/b")
OP(MOD,   OPND_NONE,   "a b -- a%b")
OP(NEG,   OPND_NONE,   "a -- -a")
OP(EQ,    OPND_NONE,   "a b -- bool")
OP(LT,    OPND_NONE,   "a b -- bool")
OP(NOT,   OPND_NONE,   "a -- !a")
OP(JMP,   OPND_ADDR,   "--")
OP(JZ,    OPND_ADDR,   "c --")
OP(JNZ,   OPND_ADDR,   "c --")
OP(LOAD,  OPND_SLOT,   "-- slot[n]")
OP(STORE, OPND_SLOT,   "a --")
OP(CALL,  OPND_ADDR,   "-- (push return address)")
OP(RET,   OPND_NONE,   "-- (pop return address)")
OP(NCALL, OPND_NATIVE, "args -- ret")
OP(PRINT, OPND_NONE,   "a --")
```

**`vm.h`**

```c
#ifndef SVM_H
#define SVM_H
#include <stdint.h>
#include <stdio.h>

/* ---- instruction word: [31..24] opcode | [23..0] operand (24-bit) ---- */
#define SVM_ARG_BITS 24
#define SVM_ARG_MASK ((UINT32_C(1) << SVM_ARG_BITS) - 1)
#define SVM_ARG_MIN  (-(INT32_C(1) << (SVM_ARG_BITS - 1)))
#define SVM_ARG_MAX  ((INT32_C(1) << (SVM_ARG_BITS - 1)) - 1)

static inline uint32_t svm_encode(unsigned op, int32_t arg)
{
    return (uint32_t) op << SVM_ARG_BITS | ((uint32_t) arg & SVM_ARG_MASK);
}
static inline unsigned svm_op(uint32_t w) { return w >> SVM_ARG_BITS; }
static inline int32_t svm_arg(uint32_t w)  /* sign-extend 24 -> 32 without >> on negatives */
{
    return (int32_t) ((w & SVM_ARG_MASK) ^ 0x800000u) - 0x800000;
}

enum svm_opcode {
#define OP(name, kind, effect) OP_##name,
#include "opcodes.def"
#undef OP
    OP_COUNT
};

/* ---- tagged union value ---- */
typedef enum { SVM_NIL, SVM_INT, SVM_BOOL } svm_type;
typedef struct {
    svm_type type;
    union {
        int64_t i;
        _Bool b;
    } as;
} svm_value;

typedef struct svm svm; /* opaque: callers only hold pointers */
typedef int (*svm_native_fn)(svm *vm, const svm_value *args, svm_value *ret);
typedef enum { SVM_DISPATCH_SWITCH, SVM_DISPATCH_GOTO } svm_dispatch;

svm *svm_new(void);
void svm_free(svm *vm);
int svm_register_native(svm *vm, const char *name, int arity, svm_native_fn fn);
int svm_load_plugin(svm *vm, const char *path);
int svm_assemble(svm *vm, const char *src);
int svm_save(const svm *vm, const char *path);
int svm_load(svm *vm, const char *path);
int svm_run(svm *vm, svm_dispatch d);
void svm_set_output(svm *vm, FILE *out);
const char *svm_error(const svm *vm);
size_t svm_depth(const svm *vm);
svm_value svm_peek(const svm *vm); /* top of stack after HALT (NIL if empty) */

#endif
```

**`arena.h`**

```c
#ifndef ARENA_H
#define ARENA_H
#include <stddef.h>

/* bump allocator: many allocations, one free */
struct arena_chunk {
    struct arena_chunk *next;
    size_t used, cap;
    max_align_t data[]; /* flexible array member, maximally aligned */
};
typedef struct {
    struct arena_chunk *head;
} arena;

void *arena_alloc(arena *a, size_t size, size_t align); /* align: power of 2 */
char *arena_strdup(arena *a, const char *s);
void arena_free(arena *a);

#endif
```

**`arena.c`**

```c
#include "arena.h"
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define CHUNK_MIN 4096

void *arena_alloc(arena *a, size_t size, size_t align)
{
    struct arena_chunk *c = a->head;
    if (c) {
        size_t off = (c->used + align - 1) & ~(align - 1);
        if (off >= c->used && off <= c->cap && size <= c->cap - off) {
            c->used = off + size;
            return (char *) c->data + off;
        }
    }
    size_t cap; /* new chunk: cap = max(CHUNK_MIN, size + align), overflow-checked */
    if (__builtin_add_overflow(size, align, &cap))
        return NULL;
    if (cap < CHUNK_MIN)
        cap = CHUNK_MIN;
    if (cap > SIZE_MAX - sizeof *c || !(c = malloc(sizeof *c + cap)))
        return NULL;
    c->next = a->head;
    c->cap = cap;
    c->used = size; /* data[] is max_align_t-aligned, so offset 0 fits any align */
    a->head = c;
    return c->data;
}

char *arena_strdup(arena *a, const char *s)
{
    size_t n = strlen(s) + 1;
    char *p = arena_alloc(a, n, 1);
    return p ? memcpy(p, s, n) : NULL;
}

void arena_free(arena *a)
{
    for (struct arena_chunk *c = a->head, *next; c; c = next) {
        next = c->next;
        free(c);
    }
    a->head = NULL;
}
```

**`list.h`**

```c
#ifndef LIST_H
#define LIST_H
#include <stddef.h>

/* intrusive doubly linked list in the style of Linux <linux/list.h> */
struct list_head {
    struct list_head *next, *prev;
};

static inline void list_init(struct list_head *h) { h->next = h->prev = h; }

static inline void list_add_tail(struct list_head *n, struct list_head *h)
{
    n->prev = h->prev;
    n->next = h;
    h->prev->next = n;
    h->prev = n;
}

#define container_of(ptr, type, member) \
    ((type *) ((char *) (ptr) - offsetof(type, member)))

#define list_for_each_entry(pos, head, member)                          \
    for (pos = container_of((head)->next, __typeof__(*pos), member);    \
         &pos->member != (head);                                        \
         pos = container_of(pos->member.next, __typeof__(*pos), member))

#endif
```

**`vm.c`**

```c
#define _POSIX_C_SOURCE 200809L
#include "vm.h"
#include "arena.h"
#include "list.h"
#include <dlfcn.h>
#include <errno.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#define STACK_MAX   1024
#define FRAMES_MAX  256
#define NSLOTS      256
#define NATIVES_MAX 256
#define LINE_MAX_   256
#define NAME_MAX_   32

enum opnd { OPND_NONE, OPND_IMM, OPND_KONST, OPND_ADDR, OPND_SLOT, OPND_NATIVE };

/* tables generated from the same X macro as enum svm_opcode */
static const char *const op_name[OP_COUNT] = {
#define OP(name, kind, effect) [OP_##name] = #name,
#include "opcodes.def"
#undef OP
};
static const enum opnd op_kind[OP_COUNT] = {
#define OP(name, kind, effect) [OP_##name] = kind,
#include "opcodes.def"
#undef OP
};

struct native {
    struct list_head node; /* intrusive: the list lives inside the object */
    const char *name;      /* arena copy: plugin's string dies at dlclose */
    int arity, index;
    svm_native_fn fn;
};
struct plugin {
    struct list_head node;
    void *handle;
};

struct svm {
    arena mem; /* natives, plugins, code, consts: freed in one shot */
    uint32_t *code;
    int64_t *consts;
    uint32_t ncode, nconsts;
    struct list_head natives, plugins;
    struct native *ntab[NATIVES_MAX];
    int nnatives;
    svm_value stack[STACK_MAX];
    size_t sp, fp;
    uint32_t frames[FRAMES_MAX];
    svm_value slots[NSLOTS];
    FILE *out;
    char err[160];
};

static int fail(const svm *cvm, const char *fmt, ...)
{
    svm *vm = (svm *) cvm; /* err[] is diagnostic state, writable even via const API */
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(vm->err, sizeof vm->err, fmt, ap);
    va_end(ap);
    return -1;
}

svm *svm_new(void)
{
    svm *vm = calloc(1, sizeof *vm);
    if (!vm)
        return NULL;
    list_init(&vm->natives);
    list_init(&vm->plugins);
    vm->out = stdout;
    return vm;
}

void svm_free(svm *vm)
{
    if (!vm)
        return;
    struct plugin *p;
    list_for_each_entry(p, &vm->plugins, node)
        dlclose(p->handle); /* safe: nodes live in the arena, freed below */
    arena_free(&vm->mem);
    free(vm);
}

void svm_set_output(svm *vm, FILE *out) { vm->out = out; }
const char *svm_error(const svm *vm) { return vm->err; }
size_t svm_depth(const svm *vm) { return vm->sp; }
svm_value svm_peek(const svm *vm)
{
    return vm->sp ? vm->stack[vm->sp - 1] : (svm_value) {.type = SVM_NIL};
}

static struct native *find_native(svm *vm, const char *name)
{
    struct native *n;
    list_for_each_entry(n, &vm->natives, node)
        if (strcmp(n->name, name) == 0)
            return n;
    return NULL;
}

int svm_register_native(svm *vm, const char *name, int arity, svm_native_fn fn)
{
    if (arity < 0 || arity > 8 || !fn || strlen(name) >= NAME_MAX_)
        return fail(vm, "bad native '%s'", name);
    if (find_native(vm, name))
        return fail(vm, "native '%s' already registered", name);
    if (vm->nnatives == NATIVES_MAX)
        return fail(vm, "too many natives");
    struct native *n = arena_alloc(&vm->mem, sizeof *n, _Alignof(struct native));
    if (!n || !(n->name = arena_strdup(&vm->mem, name)))
        return fail(vm, "out of memory");
    n->arity = arity;
    n->fn = fn;
    n->index = vm->nnatives;
    vm->ntab[vm->nnatives++] = n;
    list_add_tail(&n->node, &vm->natives);
    return 0;
}

int svm_load_plugin(svm *vm, const char *path)
{
    void *h = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (!h)
        return fail(vm, "dlopen: %s", dlerror());
    int (*init)(svm *);
    *(void **) &init = dlsym(h, "svm_plugin_init"); /* POSIX idiom: void* -> fn ptr */
    if (!init) {
        fail(vm, "%s: no svm_plugin_init", path);
        goto err_close;
    }
    struct plugin *p = arena_alloc(&vm->mem, sizeof *p, _Alignof(struct plugin));
    if (!p) {
        fail(vm, "out of memory");
        goto err_close;
    }
    if (init(vm) != 0) /* init calls svm_register_native(), which sets err */
        goto err_close;
    p->handle = h;
    list_add_tail(&p->node, &vm->plugins);
    return 0;
err_close:
    dlclose(h);
    return -1;
}

/* ---------------- verifier: run once, so the hot loop can skip checks ---------------- */
static int verify(svm *vm)
{
    if (vm->ncode == 0)
        return fail(vm, "empty program");
    for (uint32_t pc = 0; pc < vm->ncode; pc++) {
        unsigned op = svm_op(vm->code[pc]);
        int32_t arg = svm_arg(vm->code[pc]);
        if (op >= OP_COUNT)
            return fail(vm, "pc %u: bad opcode %u", pc, op);
        int ok = 1;
        switch (op_kind[op]) {
        case OPND_ADDR:   ok = arg >= 0 && (uint32_t) arg < vm->ncode; break;
        case OPND_SLOT:   ok = arg >= 0 && arg < NSLOTS; break;
        case OPND_KONST:  ok = arg >= 0 && (uint32_t) arg < vm->nconsts; break;
        case OPND_NATIVE: ok = arg >= 0 && arg < vm->nnatives; break;
        default: break;
        }
        if (!ok)
            return fail(vm, "pc %u: %s operand %d out of range", pc, op_name[op], (int) arg);
    }
    unsigned last = svm_op(vm->code[vm->ncode - 1]); /* control must not fall off the end */
    if (last != OP_HALT && last != OP_JMP && last != OP_RET)
        return fail(vm, "program must end with HALT, JMP or RET");
    return 0;
}

/* ---------------- assembler: two passes over the text ---------------- */
struct label {
    char name[NAME_MAX_];
    uint32_t addr;
};
struct asm_line {
    char *label, *mnem, *arg;
};

static void split_line(char *s, struct asm_line *l) /* modifies s in place */
{
    char *semi = strchr(s, ';');
    if (semi)
        *semi = '\0';
    l->label = l->mnem = l->arg = NULL;
    char *tok[3];
    int n = 0;
    for (char *save, *t = strtok_r(s, " \t\r\n", &save); t && n < 3;
         t = strtok_r(NULL, " \t\r\n", &save))
        tok[n++] = t;
    int i = 0;
    if (n > 0 && tok[0][strlen(tok[0]) - 1] == ':') {
        tok[0][strlen(tok[0]) - 1] = '\0';
        l->label = tok[i++];
    }
    if (i < n)
        l->mnem = tok[i++];
    if (i < n)
        l->arg = tok[i++];
}

static int lookup_op(const char *m)
{
    for (int op = 0; op < OP_COUNT; op++)
        if (strcasecmp(m, op_name[op]) == 0)
            return op;
    return -1;
}

static int parse_int(const char *s, int64_t *out)
{
    char *end;
    errno = 0;
    long long v = strtoll(s, &end, 0);
    if (errno == ERANGE || end == s || *end != '\0')
        return -1;
    *out = v;
    return 0;
}

int svm_assemble(svm *vm, const char *src)
{
    arena tmp = {0}; /* scratch memory for this call only */
    struct label *labels = NULL;
    uint32_t nlabels = 0, cap = 0, ninstr = 0, k = 0;
    int rc = -1;

    for (int pass = 1; pass <= 2; pass++) {
        const char *p = src;
        uint32_t pc = 0;
        for (int lineno = 1; *p; lineno++) {
            char buf[LINE_MAX_];
            size_t len = strcspn(p, "\n");
            if (len >= sizeof buf) {
                fail(vm, "line %d: line too long", lineno);
                goto out;
            }
            memcpy(buf, p, len);
            buf[len] = '\0';
            p += len + (p[len] == '\n');
            struct asm_line l;
            split_line(buf, &l);
            if (pass == 1 && l.label) {
                if (strlen(l.label) >= NAME_MAX_) {
                    fail(vm, "line %d: label too long", lineno);
                    goto out;
                }
                for (uint32_t i = 0; i < nlabels; i++)
                    if (strcmp(labels[i].name, l.label) == 0) {
                        fail(vm, "line %d: duplicate label '%s'", lineno, l.label);
                        goto out;
                    }
                if (nlabels == cap) { /* grow by copying inside the scratch arena */
                    cap = cap ? cap * 2 : 16;
                    struct label *nl = arena_alloc(&tmp, cap * sizeof *nl, _Alignof(struct label));
                    if (!nl) {
                        fail(vm, "out of memory");
                        goto out;
                    }
                    if (nlabels)
                        memcpy(nl, labels, nlabels * sizeof *nl);
                    labels = nl;
                }
                strcpy(labels[nlabels].name, l.label);
                labels[nlabels++].addr = pc;
            }
            if (!l.mnem)
                continue;
            int op = lookup_op(l.mnem);
            if (op < 0) {
                fail(vm, "line %d: unknown mnemonic '%s'", lineno, l.mnem);
                goto out;
            }
            if ((op_kind[op] == OPND_NONE) != (l.arg == NULL)) {
                fail(vm, "line %d: %s %s an operand", lineno, op_name[op],
                     op_kind[op] == OPND_NONE ? "takes no" : "needs");
                goto out;
            }
            if (pass == 2) {
                int64_t v = 0;
                switch (op_kind[op]) {
                case OPND_IMM:
                case OPND_KONST:
                    if (parse_int(l.arg, &v) != 0) {
                        fail(vm, "line %d: bad integer '%s'", lineno, l.arg);
                        goto out;
                    }
                    if (op == OP_PUSHK || v < SVM_ARG_MIN || v > SVM_ARG_MAX) {
                        vm->consts[k] = v; /* too wide for 24 bits: constant pool */
                        op = OP_PUSHK;
                        v = k++;
                    }
                    break;
                case OPND_ADDR: {
                    uint32_t i = 0;
                    while (i < nlabels && strcmp(labels[i].name, l.arg) != 0)
                        i++;
                    if (i == nlabels) {
                        fail(vm, "line %d: undefined label '%s'", lineno, l.arg);
                        goto out;
                    }
                    v = labels[i].addr;
                    break;
                }
                case OPND_SLOT:
                    if (parse_int(l.arg, &v) != 0 || v < 0 || v >= NSLOTS) {
                        fail(vm, "line %d: slot must be 0..%d", lineno, NSLOTS - 1);
                        goto out;
                    }
                    break;
                case OPND_NATIVE: {
                    struct native *n = find_native(vm, l.arg);
                    if (!n) {
                        fail(vm, "line %d: unknown native '%s'", lineno, l.arg);
                        goto out;
                    }
                    v = n->index;
                    break;
                }
                default:
                    break;
                }
                vm->code[pc] = svm_encode((unsigned) op, (int32_t) v);
            }
            if (++pc > SVM_ARG_MAX) {
                fail(vm, "program too long");
                goto out;
            }
        }
        if (pass == 1) {
            ninstr = pc;
            vm->code = arena_alloc(&vm->mem, (ninstr ? ninstr : 1) * sizeof *vm->code, 4);
            vm->consts = arena_alloc(&vm->mem, (ninstr ? ninstr : 1) * sizeof *vm->consts, 8);
            if (!vm->code || !vm->consts) {
                fail(vm, "out of memory");
                goto out;
            }
        }
    }
    vm->ncode = ninstr;
    vm->nconsts = k;
    rc = verify(vm);
out:
    if (rc != 0)
        vm->ncode = 0; /* never leave a half-assembled program runnable */
    arena_free(&tmp);
    return rc;
}

/* ---------------- bytecode file: little-endian, independent of host ---------------- */
static void put32(unsigned char *b, uint32_t v)
{
    for (int i = 0; i < 4; i++)
        b[i] = (unsigned char) (v >> (8 * i));
}
static uint32_t get32(const unsigned char *b)
{
    return (uint32_t) b[0] | (uint32_t) b[1] << 8 | (uint32_t) b[2] << 16 | (uint32_t) b[3] << 24;
}

/* layout: "SVM1" | u32 ncode | u32 nconsts | u32 nnatives | code[] | consts[] (u64 as 2 x u32)
 *         | natives: u8 len + name bytes, in index order (the "symbol table") */
int svm_save(const svm *vm, const char *path)
{
    if (vm->ncode == 0)
        return fail(vm, "nothing to save");
    FILE *f = fopen(path, "wb");
    if (!f)
        return fail(vm, "%s: %s", path, strerror(errno));
    unsigned char b[16];
    memcpy(b, "SVM1", 4);
    put32(b + 4, vm->ncode);
    put32(b + 8, vm->nconsts);
    put32(b + 12, (uint32_t) vm->nnatives);
    int ok = fwrite(b, 1, 16, f) == 16;
    for (uint32_t i = 0; ok && i < vm->ncode; i++) {
        put32(b, vm->code[i]);
        ok = fwrite(b, 1, 4, f) == 4;
    }
    for (uint32_t i = 0; ok && i < vm->nconsts; i++) {
        uint64_t u = (uint64_t) vm->consts[i];
        put32(b, (uint32_t) u);
        put32(b + 4, (uint32_t) (u >> 32));
        ok = fwrite(b, 1, 8, f) == 8;
    }
    for (int i = 0; ok && i < vm->nnatives; i++) {
        size_t len = strlen(vm->ntab[i]->name);
        ok = fputc((int) len, f) != EOF && fwrite(vm->ntab[i]->name, 1, len, f) == len;
    }
    if (fclose(f) != 0 || !ok)
        return fail(vm, "%s: write error", path);
    return 0;
}

int svm_load(svm *vm, const char *path)
{
    int rc = -1;
    uint32_t *code = NULL;
    int64_t *consts = NULL;
    int remap[NATIVES_MAX];
    unsigned char b[16];
    FILE *f = fopen(path, "rb");
    if (!f)
        return fail(vm, "%s: %s", path, strerror(errno));
    if (fread(b, 1, 16, f) != 16 || memcmp(b, "SVM1", 4) != 0) {
        fail(vm, "%s: not an SVM1 file", path);
        goto out_close;
    }
    uint32_t ncode = get32(b + 4), nconsts = get32(b + 8), nnat = get32(b + 12);
    if (ncode == 0 || ncode > SVM_ARG_MAX || nconsts > ncode || nnat > NATIVES_MAX) {
        fail(vm, "%s: corrupt header", path); /* also bounds the multiplications below */
        goto out_close;
    }
    if (!(code = malloc(ncode * sizeof *code)) ||
        !(consts = malloc((nconsts ? nconsts : 1) * sizeof *consts))) {
        fail(vm, "out of memory");
        goto out_free;
    }
    for (uint32_t i = 0; i < ncode; i++) {
        if (fread(b, 1, 4, f) != 4)
            goto truncated;
        code[i] = get32(b);
    }
    for (uint32_t i = 0; i < nconsts; i++) {
        if (fread(b, 1, 8, f) != 8)
            goto truncated;
        consts[i] = (int64_t) ((uint64_t) get32(b + 4) << 32 | get32(b));
    }
    for (uint32_t i = 0; i < nnat; i++) { /* resolve native names, like a dynamic linker */
        char name[256];
        int len = fgetc(f);
        if (len == EOF || fread(name, 1, (size_t) len, f) != (size_t) len)
            goto truncated;
        name[len] = '\0';
        struct native *n = find_native(vm, name);
        if (!n) {
            fail(vm, "%s: unresolved native '%s' (load its plugin first)", path, name);
            goto out_free;
        }
        remap[i] = n->index;
    }
    for (uint32_t i = 0; i < ncode; i++)
        if (svm_op(code[i]) == OP_NCALL) {
            int32_t a = svm_arg(code[i]);
            if (a < 0 || (uint32_t) a >= nnat) {
                fail(vm, "%s: pc %u: bad native index", path, i);
                goto out_free;
            }
            code[i] = svm_encode(OP_NCALL, remap[a]);
        }
    vm->code = arena_alloc(&vm->mem, ncode * sizeof *code, 4);
    vm->consts = arena_alloc(&vm->mem, (nconsts ? nconsts : 1) * sizeof *consts, 8);
    if (!vm->code || !vm->consts) {
        fail(vm, "out of memory");
        goto out_free;
    }
    memcpy(vm->code, code, ncode * sizeof *code);
    memcpy(vm->consts, consts, nconsts * sizeof *consts);
    vm->ncode = ncode;
    vm->nconsts = nconsts;
    rc = verify(vm); /* never trust a file: same checks as freshly assembled code */
    if (rc != 0)
        vm->ncode = 0;
    goto out_free;
truncated:
    fail(vm, "%s: truncated file", path);
out_free:
    free(consts);
    free(code);
out_close:
    fclose(f);
    return rc;
}

/* ---------------- interpreter ---------------- */
#define INTV(v)  ((svm_value) {.type = SVM_INT, .as.i = (v)})
#define BOOLV(v) ((svm_value) {.type = SVM_BOOL, .as.b = (v)})
#define ERR(...) do { fail(vm, __VA_ARGS__); goto fail; } while (0)
#define NEED(n)  do { if (sp < (n)) ERR("pc %u: stack underflow", pc - 1); } while (0)
#define ROOM(n)  do { if (sp + (n) > STACK_MAX) ERR("pc %u: stack overflow", pc - 1); } while (0)
#define INTS2(name)                                                        \
    do {                                                                   \
        NEED(2);                                                           \
        if (st[sp - 2].type != SVM_INT || st[sp - 1].type != SVM_INT)      \
            ERR("pc %u: %s needs two ints", pc - 1, name);                 \
    } while (0)
#define ARITH(builtin, name)                                               \
    do {                                                                   \
        INTS2(name);                                                       \
        if (builtin(st[sp - 2].as.i, st[sp - 1].as.i, &st[sp - 2].as.i))   \
            ERR("pc %u: integer overflow in %s", pc - 1, name);            \
        sp--;                                                              \
    } while (0)

static _Bool truthy(svm_value v)
{
    return v.type == SVM_BOOL ? v.as.b : v.type == SVM_INT ? v.as.i != 0 : 0;
}
static _Bool equal(svm_value a, svm_value b)
{
    if (a.type != b.type)
        return 0;
    return a.type == SVM_INT ? a.as.i == b.as.i : a.type == SVM_BOOL ? a.as.b == b.as.b : 1;
}
static void print_value(FILE *out, svm_value v)
{
    if (v.type == SVM_INT)
        fprintf(out, "%lld\n", (long long) v.as.i);
    else if (v.type == SVM_BOOL)
        fputs(v.as.b ? "true\n" : "false\n", out);
    else
        fputs("nil\n", out);
}

#define USE_GOTO 0
#include "vm_exec.inc"
#undef USE_GOTO
#define USE_GOTO 1
#include "vm_exec.inc"
#undef USE_GOTO

int svm_run(svm *vm, svm_dispatch d)
{
    if (vm->ncode == 0)
        return fail(vm, "no program loaded");
    vm->sp = vm->fp = 0;
    memset(vm->slots, 0, sizeof vm->slots); /* all slots start as nil */
    vm->err[0] = '\0';
    return d == SVM_DISPATCH_GOTO ? exec_goto(vm) : exec_switch(vm);
}
```

**`vm_exec.inc`**

```c
/* Interpreter body, included twice by vm.c:
 *   USE_GOTO 0 -> exec_switch()   (one indirect branch shared by all opcodes)
 *   USE_GOTO 1 -> exec_goto()     (computed goto: one indirect branch per opcode) */
#if USE_GOTO
#define CASE(name) L_##name:
#define NEXT       do { w = code[pc++]; goto *labels[svm_op(w)]; } while (0)
#define BEGIN      NEXT;
#define END
static int exec_goto(svm *vm)
#else
#define CASE(name) case OP_##name:
#define NEXT       break
#define BEGIN      for (;;) { w = code[pc++]; switch (svm_op(w)) {
#define END        default: ERR("pc %u: bad opcode %u", pc - 1, svm_op(w)); } }
static int exec_switch(svm *vm)
#endif
{
#if USE_GOTO
    static void *const labels[OP_COUNT] = {
#define OP(name, kind, effect) [OP_##name] = &&L_##name,
#include "opcodes.def"
#undef OP
    };
#endif
    const uint32_t *code = vm->code;
    svm_value *st = vm->stack;
    uint32_t pc = 0, w;
    size_t sp = 0;

    BEGIN
    CASE(HALT)  goto done;
    CASE(PUSH)  ROOM(1); st[sp++] = INTV(svm_arg(w)); NEXT;
    CASE(PUSHK) ROOM(1); st[sp++] = INTV(vm->consts[svm_arg(w)]); NEXT;
    CASE(POP)   NEED(1); sp--; NEXT;
    CASE(DUP)   NEED(1); ROOM(1); st[sp] = st[sp - 1]; sp++; NEXT;
    CASE(SWAP)  { NEED(2); svm_value t = st[sp - 1]; st[sp - 1] = st[sp - 2]; st[sp - 2] = t; } NEXT;
    CASE(OVER)  NEED(2); ROOM(1); st[sp] = st[sp - 2]; sp++; NEXT;
    CASE(ADD)   ARITH(__builtin_add_overflow, "ADD"); NEXT;
    CASE(SUB)   ARITH(__builtin_sub_overflow, "SUB"); NEXT;
    CASE(MUL)   ARITH(__builtin_mul_overflow, "MUL"); NEXT;
    CASE(DIV)
    CASE(MOD) {
        INTS2(svm_op(w) == OP_DIV ? "DIV" : "MOD");
        int64_t a = st[sp - 2].as.i, b = st[sp - 1].as.i;
        if (b == 0)
            ERR("pc %u: division by zero", pc - 1);
        if (a == INT64_MIN && b == -1) /* the only overflowing case; % is UB too */
            ERR("pc %u: integer overflow in %s", pc - 1, svm_op(w) == OP_DIV ? "DIV" : "MOD");
        st[sp - 2].as.i = svm_op(w) == OP_DIV ? a / b : a % b;
        sp--;
    } NEXT;
    CASE(NEG)
        NEED(1);
        if (st[sp - 1].type != SVM_INT)
            ERR("pc %u: NEG needs an int", pc - 1);
        if (__builtin_sub_overflow((int64_t) 0, st[sp - 1].as.i, &st[sp - 1].as.i))
            ERR("pc %u: integer overflow in NEG", pc - 1);
        NEXT;
    CASE(EQ)    NEED(2); st[sp - 2] = BOOLV(equal(st[sp - 2], st[sp - 1])); sp--; NEXT;
    CASE(LT)    INTS2("LT"); st[sp - 2] = BOOLV(st[sp - 2].as.i < st[sp - 1].as.i); sp--; NEXT;
    CASE(NOT)   NEED(1); st[sp - 1] = BOOLV(!truthy(st[sp - 1])); NEXT;
    CASE(JMP)   pc = (uint32_t) svm_arg(w); NEXT;
    CASE(JZ)    NEED(1); if (!truthy(st[--sp])) pc = (uint32_t) svm_arg(w); NEXT;
    CASE(JNZ)   NEED(1); if (truthy(st[--sp])) pc = (uint32_t) svm_arg(w); NEXT;
    CASE(LOAD)  ROOM(1); st[sp++] = vm->slots[svm_arg(w)]; NEXT;
    CASE(STORE) NEED(1); vm->slots[svm_arg(w)] = st[--sp]; NEXT;
    CASE(CALL)
        if (vm->fp == FRAMES_MAX)
            ERR("pc %u: call stack overflow", pc - 1);
        vm->frames[vm->fp++] = pc;
        pc = (uint32_t) svm_arg(w);
        NEXT;
    CASE(RET)
        if (vm->fp == 0)
            ERR("pc %u: RET with empty call stack", pc - 1);
        pc = vm->frames[--vm->fp];
        NEXT;
    CASE(NCALL) {
        struct native *n = vm->ntab[svm_arg(w)];
        NEED((size_t) n->arity);
        if (n->arity == 0)
            ROOM(1);
        svm_value ret = {.type = SVM_NIL};
        if (n->fn(vm, &st[sp - n->arity], &ret) != 0)
            ERR("pc %u: native '%s' failed", pc - 1, n->name);
        sp -= n->arity;
        st[sp++] = ret;
    } NEXT;
    CASE(PRINT) NEED(1); print_value(vm->out, st[--sp]); NEXT;
    END

done:
    vm->sp = sp;
    return 0;
fail:
    vm->sp = sp;
    return -1;
}
#undef CASE
#undef NEXT
#undef BEGIN
#undef END
```

**`main.c`**

```c
#define _POSIX_C_SOURCE 200809L
#include "vm.h"
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static char *read_file(const char *path)
{
    FILE *f = fopen(path, "rb");
    if (!f)
        return NULL;
    char *buf = NULL;
    size_t len = 0;
    FILE *mem = open_memstream(&buf, &len); /* grows as needed */
    int c;
    while (mem && (c = fgetc(f)) != EOF)
        fputc(c, mem);
    fclose(f);
    if (mem)
        fclose(mem);
    return buf;
}

static int ends_with(const char *s, const char *suffix)
{
    size_t n = strlen(s), m = strlen(suffix);
    return n >= m && strcmp(s + n - m, suffix) == 0;
}

int main(int argc, char *argv[])
{
    svm_dispatch d = SVM_DISPATCH_GOTO;
    const char *out = NULL;
    int timing = 0, opt, status = 1;
    svm *vm = svm_new();
    if (!vm)
        return 1;
    while ((opt = getopt(argc, argv, "d:p:o:t")) != -1) {
        switch (opt) {
        case 'd': d = strcmp(optarg, "switch") == 0 ? SVM_DISPATCH_SWITCH : SVM_DISPATCH_GOTO; break;
        case 'p':
            if (svm_load_plugin(vm, optarg) != 0)
                goto fail;
            break;
        case 'o': out = optarg; break;
        case 't': timing = 1; break;
        default: goto usage;
        }
    }
    if (optind != argc - 1)
        goto usage;
    const char *path = argv[optind];
    if (ends_with(path, ".svmb")) {
        if (svm_load(vm, path) != 0)
            goto fail;
    } else {
        char *src = read_file(path);
        if (!src) {
            perror(path);
            goto out;
        }
        int rc = svm_assemble(vm, src);
        free(src);
        if (rc != 0)
            goto fail;
    }
    if (out) {
        status = svm_save(vm, out) != 0;
        if (status)
            goto fail;
        goto out;
    }
    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    if (svm_run(vm, d) != 0)
        goto fail;
    clock_gettime(CLOCK_MONOTONIC, &t1);
    if (timing)
        fprintf(stderr, "[%s] %.3f s\n", d == SVM_DISPATCH_GOTO ? "goto" : "switch",
                (t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) / 1e9);
    status = 0;
    goto out;
usage:
    fprintf(stderr, "usage: %s [-d switch|goto] [-p plugin.so]... [-o out.svmb] [-t] prog.{asm,svmb}\n",
            argv[0]);
    goto out;
fail:
    fprintf(stderr, "svm: %s\n", svm_error(vm));
out:
    svm_free(vm);
    return status;
}
```

**`plugin_math.c`**

```c
/* Native functions shipped as a shared object; loaded with dlopen() */
#include "vm.h"

static int n_isqrt(svm *vm, const svm_value *a, svm_value *ret)
{
    (void) vm;
    if (a[0].type != SVM_INT || a[0].as.i < 0)
        return -1;
    uint64_t x = (uint64_t) a[0].as.i, r = 0;
    for (uint64_t bit = UINT64_C(1) << 62; bit; bit >>= 2) { /* digit-by-digit, base 4 */
        if (x >= r + bit) {
            x -= r + bit;
            r = (r >> 1) + bit;
        } else {
            r >>= 1;
        }
    }
    *ret = (svm_value) {.type = SVM_INT, .as.i = (int64_t) r};
    return 0;
}

static int n_gcd(svm *vm, const svm_value *a, svm_value *ret)
{
    (void) vm;
    if (a[0].type != SVM_INT || a[1].type != SVM_INT)
        return -1;
    uint64_t x = a[0].as.i < 0 ? -(uint64_t) a[0].as.i : (uint64_t) a[0].as.i;
    uint64_t y = a[1].as.i < 0 ? -(uint64_t) a[1].as.i : (uint64_t) a[1].as.i;
    while (y) {
        uint64_t t = x % y;
        x = y;
        y = t;
    }
    if (x > INT64_MAX) /* gcd(INT64_MIN, 0) = 2^63 does not fit */
        return -1;
    *ret = (svm_value) {.type = SVM_INT, .as.i = (int64_t) x};
    return 0;
}

int svm_plugin_init(svm *vm)
{
    return svm_register_native(vm, "isqrt", 1, n_isqrt) ||
           svm_register_native(vm, "gcd", 2, n_gcd);
}
```

**`test_vm.c`**

```c
#define _POSIX_C_SOURCE 200809L
#include "vm.h"
#include <stdlib.h>
#include <string.h>

static int runs, fails;
#define CHECK(cond)                                                        \
    do {                                                                   \
        runs++;                                                            \
        if (!(cond)) {                                                     \
            fails++;                                                       \
            fprintf(stderr, "%s:%d: CHECK(%s) failed\n", __FILE__, __LINE__, #cond); \
        }                                                                  \
    } while (0)

/* assemble + run under one dispatch mode; captures PRINT output and the error text */
static int run_src(const char *src, svm_dispatch d, char *out, size_t outsz, char *err,
                   int64_t *top)
{
    svm *vm = svm_new();
    char *buf = NULL;
    size_t len = 0;
    FILE *mem = open_memstream(&buf, &len);
    svm_set_output(vm, mem);
    int rc = svm_load_plugin(vm, "./libsvm_math.so");
    if (rc == 0)
        rc = svm_assemble(vm, src);
    if (rc == 0)
        rc = svm_run(vm, d);
    fclose(mem);
    snprintf(out, outsz, "%s", buf ? buf : "");
    snprintf(err, 160, "%s", svm_error(vm));
    if (top)
        *top = svm_peek(vm).as.i;
    free(buf);
    svm_free(vm);
    return rc;
}

/* every program must behave identically under both dispatchers */
static int run_both(const char *src, char *out, char *err, int64_t *top)
{
    char out2[256], err2[160];
    int64_t top2 = 0;
    int rc1 = run_src(src, SVM_DISPATCH_SWITCH, out, 256, err, top);
    int rc2 = run_src(src, SVM_DISPATCH_GOTO, out2, sizeof out2, err2, &top2);
    CHECK(rc1 == rc2 && strcmp(out, out2) == 0 && strcmp(err, err2) == 0);
    if (top)
        CHECK(*top == top2);
    return rc1;
}

static const char FIB[] = "    PUSH 20\n    CALL fib\n    HALT\n"
                          "fib: DUP\n    PUSH 2\n    LT\n    JNZ base\n"
                          "    DUP\n    PUSH 1\n    SUB\n    CALL fib\n"
                          "    SWAP\n    PUSH 2\n    SUB\n    CALL fib\n    ADD\n"
                          "base: RET\n";

int main(void)
{
    char out[256], err[160];
    int64_t top;

    /* instruction encoding round trip, including negative operands */
    int32_t samples[] = {0, 1, -1, SVM_ARG_MAX, SVM_ARG_MIN, 12345, -54321};
    for (size_t i = 0; i < sizeof samples / sizeof samples[0]; i++) {
        uint32_t w = svm_encode(OP_JMP, samples[i]);
        CHECK(svm_op(w) == OP_JMP && svm_arg(w) == samples[i]);
    }

    CHECK(run_both("PUSH 2\nPUSH 3\nMUL\nPUSH 4\nSUB\nHALT\n", out, err, &top) == 0 && top == 2);
    CHECK(run_both(FIB, out, err, &top) == 0 && top == 6765);
    CHECK(run_both("PUSH 7\nPUSH -2\nDIV\nPRINT\nPUSH 7\nPUSH -2\nMOD\nPRINT\nHALT\n",
                   out, err, NULL) == 0 && strcmp(out, "-3\n1\n") == 0);
    /* wide constants go through the constant pool */
    CHECK(run_both("PUSH 9223372036854775807\nPRINT\nPUSH -8388609\nPRINT\nHALT\n",
                   out, err, NULL) == 0 &&
          strcmp(out, "9223372036854775807\n-8388609\n") == 0);
    CHECK(run_both("PUSH 1\nPUSH 1\nEQ\nPRINT\nPUSH 1\nNOT\nPRINT\nHALT\n", out, err, NULL) == 0 &&
          strcmp(out, "true\nfalse\n") == 0);
    CHECK(run_both("LOAD 5\nPRINT\nPUSH 99\nNCALL isqrt\nPUSH 84\nPUSH 36\nNCALL gcd\nHALT\n",
                   out, err, &top) == 0 && strcmp(out, "nil\n") == 0 && top == 12);

    /* runtime errors: caught, reported, never UB inside the VM */
    CHECK(run_both("PUSH 9223372036854775807\nPUSH 1\nADD\nHALT\n", out, err, NULL) != 0 &&
          strstr(err, "overflow in ADD"));
    CHECK(run_both("PUSH -9223372036854775807\nPUSH 1\nSUB\nPUSH -1\nDIV\nHALT\n", out, err,
                   NULL) != 0 && strstr(err, "overflow in DIV"));
    CHECK(run_both("PUSH 1\nPUSH 0\nMOD\nHALT\n", out, err, NULL) != 0 &&
          strstr(err, "division by zero"));
    CHECK(run_both("ADD\nHALT\n", out, err, NULL) != 0 && strstr(err, "underflow"));
    CHECK(run_both("PUSH 1\nPUSH 1\nEQ\nPUSH 1\nADD\nHALT\n", out, err, NULL) != 0 &&
          strstr(err, "needs two ints"));
    CHECK(run_both("loop: PUSH 1\nJMP loop\n", out, err, NULL) != 0 && strstr(err, "stack overflow"));
    CHECK(run_both("f: CALL f\nRET\n", out, err, NULL) != 0 && strstr(err, "call stack overflow"));
    CHECK(run_both("PUSH -1\nNCALL isqrt\nHALT\n", out, err, NULL) != 0 && strstr(err, "isqrt"));

    /* assembler and verifier errors */
    CHECK(run_both("PUSH 1\nFOO\nHALT\n", out, err, NULL) != 0 && strstr(err, "line 2: unknown mnemonic"));
    CHECK(run_both("JMP nowhere\nHALT\n", out, err, NULL) != 0 && strstr(err, "undefined label"));
    CHECK(run_both("a: HALT\na: HALT\n", out, err, NULL) != 0 && strstr(err, "duplicate label"));
    CHECK(run_both("PUSH\nHALT\n", out, err, NULL) != 0 && strstr(err, "needs an operand"));
    CHECK(run_both("PUSH 12abc\nHALT\n", out, err, NULL) != 0 && strstr(err, "bad integer"));
    CHECK(run_both("STORE 256\nHALT\n", out, err, NULL) != 0 && strstr(err, "slot"));
    CHECK(run_both("NCALL nosuch\nHALT\n", out, err, NULL) != 0 && strstr(err, "unknown native"));
    CHECK(run_both("PUSH 1\nPRINT\n", out, err, NULL) != 0 && strstr(err, "must end with"));
    CHECK(run_both("; only a comment\n\n", out, err, NULL) != 0 && strstr(err, "empty program"));

    /* save -> load round trip, with the native renumbered by name on load */
    svm *a = svm_new(), *b = svm_new();
    CHECK(svm_load_plugin(a, "./libsvm_math.so") == 0);
    CHECK(svm_assemble(a, "PUSH 1000000000000\nNCALL isqrt\nHALT\n") == 0);
    CHECK(svm_save(a, "t.svmb") == 0);
    CHECK(svm_load(b, "t.svmb") != 0 && strstr(svm_error(b), "unresolved native 'isqrt'"));
    CHECK(svm_register_native(b, "dummy", 0, (svm_native_fn) 0) != 0); /* fn must be non-NULL */
    CHECK(svm_load_plugin(b, "./libsvm_math.so") == 0);
    CHECK(svm_load(b, "t.svmb") == 0 && svm_run(b, SVM_DISPATCH_GOTO) == 0 &&
          svm_peek(b).as.i == 1000000);
    CHECK(svm_load_plugin(b, "./libsvm_math.so") != 0 && strstr(svm_error(b), "already registered"));
    CHECK(svm_load_plugin(b, "./nonexistent.so") != 0 && strstr(svm_error(b), "dlopen"));
    svm_free(a);
    svm_free(b);
    FILE *f = fopen("bad.svmb", "wb");
    fwrite("SVM1\x01\0\0\0\0\0\0\0\0\0\0\0", 1, 16, f); /* header says 1 word, file has 0 */
    fclose(f);
    b = svm_new();
    CHECK(svm_load(b, "bad.svmb") != 0 && strstr(svm_error(b), "truncated"));
    svm_free(b);

    printf("%d checks, %d failed\n", runs, fails);
    return fails != 0;
}
```

**`fact.asm`**

```asm
; fact.asm -- print n! until int64 overflows
        PUSH 1
        STORE 0         ; acc = 1
        PUSH 1
        STORE 1         ; n = 1
loop:   LOAD 0
        LOAD 1
        MUL
        DUP
        STORE 0         ; acc *= n
        PRINT
        LOAD 1
        PUSH 1
        ADD
        STORE 1         ; n += 1
        JMP loop
```

**`native.asm`**

```asm
; native.asm -- natives come from libsvm_math.so
        PUSH 1000000007
        PUSH 1000000007
        MUL             ; 1000000014000000049
        NCALL isqrt
        PRINT
        PUSH 1071
        PUSH 462
        NCALL gcd
        PRINT
        HALT
```

`fib.asm` 與 `sum.asm` 見上方「期望輸出範例」。建置與測試：

```
$ make && ./test_vm
66 checks, 0 failed
$ make san          # ASan + UBSan，重新建置後跑同一組測試
66 checks, 0 failed
$ make clean && make CC=clang && ./test_vm
66 checks, 0 failed
```


</details>

**解答說明**：

- **一份表格，四個產物**。`opcodes.def` 被 include 了四次：`vm.h` 的 `enum`、`vm.c` 的 `op_name[]` 與 `op_kind[]`、`vm_exec.inc` 的 `labels[]`。新增一道指令只改 `opcodes.def` 加一行、`vm_exec.inc` 加一個 `CASE`；漏寫 `CASE` 時 switch 版會掉進 `default`、goto 版的 `labels[]` 會因為 `&&L_FOO` 未定義而編譯失敗——錯誤在編譯時期就出現。
- **驗證一次，熱迴圈少檢查**。`JMP` 的目標、`LOAD` 的 slot、`NCALL` 的索引都在 `verify()` 檢查過，直譯迴圈不再檢查；堆疊深度取決於執行路徑，靜態檢查做不到（需要資料流分析，JVM 的 verifier 就是在做這件事），所以 `NEED`/`ROOM` 留在執行時期。
- **溢位用 `__builtin_*_overflow`**。它們在 GCC 5+ 與 clang 都有，x86-64 上編成 `add` + `jo`，比手寫 `a > INT64_MAX - b` 的前置檢查快也不容易寫錯。`DIV`/`MOD` 另外處理 `INT64_MIN / -1`：數學結果 2^63 放不進 `int64_t`，而 C 的 `INT64_MIN % -1` 同樣是 UB（C11 6.5.5 §6：`a/b` 無法表示時 `a%b` 也是未定義）。
- **兩個 arena**。VM 的 arena 存活到 `svm_free`；組譯器的標籤表放在函式內的暫存 arena，`out:` 時整個丟掉。標籤表擴容時舊陣列留在暫存 arena 裡不釋放——arena 的哲學就是「用空間換掉所有個別 `free`」。
- **intrusive list 的節點在 arena 裡**。`svm_free` 先走訪 `plugins` 串列 `dlclose`，再 `arena_free`；順序反過來就是 use-after-free（ASan 會抓到，你可以故意調換試試）。
- **goto 清理鏈**。`svm_load` 有 `truncated` → `out_free` → `out_close` 三個標籤，每個錯誤點跳到「釋放到目前為止取得的資源」的位置。`free(NULL)` 是合法的（C11 7.22.3.3 §2），所以 `code`/`consts` 初始化為 `NULL` 後可以共用 `out_free`。
- **不透明 handle**。`vm.h` 只有 `typedef struct svm svm;`，外掛與主程式都碰不到內部欄位；`struct svm` 的配置可以任意改而不必重編外掛——共享程式庫穩定 ABI 的基本手法（Ch 43 題目 0x0F 的 SQLite 式 handle）。
- **內部函式全是 `static`**。`nm -D libsvm.so` 只會看到 `svm_*` 與 `arena_*`。`arena_*` 其實也不該匯出，這留給延伸挑戰（`-fvisibility=hidden`）。

## 實測：兩種分派器的速度

```
$ make && for d in switch goto; do taskset -c 2 ./svm -t -d $d sum.asm; done
```

AMD Ryzen 7 5700G（Zen 3），各三次：

| 編譯器 | 程式 | switch | computed goto |
|---|---|---|---|
| gcc 11 -O2 | sum.asm（約 11 億道指令） | 1.765 / 1.775 / 1.812 s | 1.702 / 1.662 / 1.696 s |
| gcc 11 -O2 | fib(32)（約 1 億道指令） | 0.110 / 0.110 / 0.122 s | 0.118 / 0.130 / 0.120 s |
| clang 14 -O2 | sum.asm | 1.734 / 1.749 / 1.779 s | 1.691 / 1.732 / 1.709 s |
| clang 14 -O2 | fib(32) | 0.128 / 0.132 / 0.127 s | 0.125 / 0.124 / 0.125 s |

computed goto 在 sum.asm 上快約 3–4%，在 fib 上幾乎沒差（gcc 甚至略慢）。這和「computed goto 能快 15–25%」的老說法差很多，原因有二：

1. **分派點數量不如預期**。數一下 `jmp *` 間接跳躍：gcc 11 的 switch 版（被 inline 進 `svm_run`）有 1 個，goto 版只有 **6 個**，不是每個 opcode 一個——gcc 的 cross-jumping 把相同的 `goto *labels[...]` 尾巴合併了（加 `-fno-gcse` 變成 5 個，速度 switch 1.95 s / goto 1.77 s，差距反而拉大）。clang 14 的 goto 版有 27 個，比較接近教科書的樣子。**想要 computed goto 的效果，先看組合語言確認編譯器沒有把它合併回去。**
2. **現代 CPU 的間接分支預測器會利用歷史**。Rohou、Swamy、Seznec 在 CGO 2015 的〈Branch Prediction and the Performance of Interpreters — Don't Trust Folklore〉量測 Python、Javascript 等直譯器，發現從 Nehalem 到 Haswell，switch 分派的單一間接跳躍預測失準率大幅下降，computed goto 的優勢跟著縮小；他們的結論是「別再相信民間傳說」。Zen 3 的預測器同樣是這一代的設計。

我們這個 VM 每道指令還做了堆疊檢查、型別檢查、16 位元組的值複製，分派之外的工作佔比高，也稀釋了分派方式的差異。CPython 在 gcc/clang 上預設開 computed goto，是因為在**它的**負載與目標平台上量得到好處——結論永遠是「在你的負載上量」。

## 延伸挑戰（加分）

- **反組譯器與單步除錯器**：用 `op_name[]` 與 `op_kind[]` 寫 `svm_disasm()`；再加一個 `-g` 模式，每執行一道指令印出 `pc`、指令、堆疊內容，支援 `break <label>`、`step`、`stack`。這是 gdb 的縮小版（[Ch 5](./05-gdb-in-practice.md)）。
- **超級指令（superinstruction）**：統計 `sum.asm` 中最常連續出現的指令對（`LOAD 1; PUSH 1`、`SUB; STORE 1`），合併成一道新 opcode，量測加速。這是 Ertl 與 Gregg 研究過的經典最佳化。
- **字串與垃圾回收**：加 `SVM_STR` 型別，字串物件放在一個自己管理的 heap；實作 mark-and-sweep：根是資料堆疊與 slot，mark 位元放在物件標頭。思考：arena 為什麼不適合存放會死掉的物件？
- **模板 JIT**：仿照 [練習 E](./practice-e-malloc-tracer-and-bf-jit.md) 的 Brainfuck JIT，把每道指令翻成一段固定的 x86-64 機械碼貼在 `mmap(PROT_READ|PROT_WRITE)` 的頁面，完成後 `mprotect` 改成 `PROT_READ|PROT_EXEC`（W^X）。先只支援整數與 `JMP/JZ`，型別檢查失敗就退回直譯器（deoptimization 的雛形）。
- **符號可見性**：用 `-fvisibility=hidden` 編 `libsvm.so`，只對公開 API 加 `__attribute__((visibility("default")))`，用 `nm -D` 比較前後的動態符號表，並量測 PLT 呼叫是否減少（[Ch 40](./40-dynamic-linking.md)）。
- **嵌入式移植**：拿掉 `dlopen` 與 `stdio`，把原生函式表改成編譯時期的 `const` 陣列（放 flash），堆疊大小用 `-fstack-usage` 檢查，在 Cortex-M 上執行。這時 Ch 42–43 的每一題都會回來找你。

## 自我檢核

- [ ] 能不看程式碼說出 X macro 讓「新增一道指令」需要改哪幾處，以及漏改時會在哪個階段失敗
- [ ] 能解釋為什麼 24 位元運算元的符號擴充不寫成 `(int32_t)(w << 8) >> 8`
- [ ] 能說明驗證器檢查了什麼、沒檢查什麼，以及為什麼堆疊深度留給執行時期
- [ ] 能說出 `svm_free` 裡 `dlclose` 與 `arena_free` 的順序為什麼不能顛倒，以及原生函式名稱為什麼要複製
- [ ] 能解釋 bytecode 載入時的「名稱重定位」和 ELF 動態連結的對應關係
- [ ] 如果有人說「computed goto 一定比 switch 快」，你能拿出本專案的量測與組合語言反駁或附條件同意
- [ ] 能說出自己的實作和參考解答的差異，並解釋各自的取捨

## 延伸閱讀

### 論文

- **[The Structure and Performance of Efficient Interpreters](https://jilp.org/vol5/v5paper12.pdf)** — M. Anton Ertl, David Gregg，*Journal of Instruction-Level Parallelism* 5（2003）
  - **核心貢獻**：系統性量測 switch、direct threading 等分派方式的分支預測行為，提出超級指令與指令複製等改善方法。
  - **讀哪裡**：介紹分派技術（switch、threaded code）與量測分支預測失準的章節；模擬器設定細節可略過。
  - **和本章的關聯**：本專案的兩種分派器就是這篇的 switch dispatch 與 threaded code；讀完再看我們的量測，會知道 2003 年的硬體結論為什麼到 2015 年被修正。
- **Branch Prediction and the Performance of Interpreters — Don't Trust Folklore** — Erven Rohou, Bharath Narasimha Swamy, André Seznec，CGO（2015），[DOI 10.1109/CGO.2015.7054191](https://doi.org/10.1109/CGO.2015.7054191)（HAL 預印本 hal-01100647）
  - **核心貢獻**：在 Intel Nehalem 到 Haswell 數代處理器上量測 Python 等多個直譯器，發現現代間接分支預測器（以及 ITTAGE 模擬）讓 switch 分派的失準率大幅降低。
  - **讀哪裡**：實驗結果與結論兩節。
  - **和本章的關聯**：解釋了「實測：兩種分派器的速度」一節為什麼差距只有幾個百分點。

### 部落格 / 技術文章

- **[Computed goto for efficient dispatch tables](https://eli.thegreenplace.net/2012/07/12/computed-goto-for-efficient-dispatch-tables)** — Eli Bendersky（2012）
  - **這篇說什麼**：用一個小型 VM 比較 switch 與 computed goto，並分析 CPython 為何採用後者。
  - **讀哪裡**：整篇，尤其「Why it's faster」一節談的「每個 opcode 一個分派點」，正是我們在 gcc 11 上看到被 cross-jumping 合併掉的東西。
  - **為什麼值得讀**：Bendersky 的解說是這個主題最常被引用的入門文。
- **[Crafting Interpreters](https://craftinginterpreters.com/)** — Robert Nystrom
  - **這篇說什麼**：第二部分 clox 用 C 實作一個完整的 bytecode VM：tagged union 值、常數池、字串駐留、mark-sweep GC、閉包。
  - **讀哪裡**：Chapter 14（Chunks of Bytecode）到 Chapter 18（Types of Values）對應本專案；Chapter 26（Garbage Collection）對應延伸挑戰。
  - **為什麼值得讀**：線上免費、程式碼完整，是本專案最自然的下一步。

### 官方文件 / Spec

- **[GCC Manual: Labels as Values](https://gcc.gnu.org/onlinedocs/gcc/Labels-as-Values.html)**
  - **讀哪裡**：整節，特別是「`&&label` 只能在同一函式內使用」與「不要讓計算出的位址跨越函式」的限制。
- **[dlsym(3) — POSIX.1-2017](https://pubs.opengroup.org/onlinepubs/9699919799/functions/dlsym.html)**
  - **讀哪裡**：APPLICATION USAGE 與 RATIONALE，說明 `void *` 轉函式指標的問題與 `*(void **)(&fn)` 寫法的由來。

### 書籍

- **《Virtual Machines: Versatile Platforms for Systems and Processes》** — James E. Smith, Ravi Nair（Morgan Kaufmann，2005）
  - **這本書的定位**：VM 的教科書，從直譯、二進位翻譯到系統虛擬化；年代較早，但直譯與動態翻譯的章節原理至今適用。
  - **讀哪幾章**：第 2 章（Emulation: Interpretation and Binary Translation）與本專案直接相關。

恭喜完成整門課。從 `a[i] = i++` 為什麼是 UB 開始，到能寫出一個有驗證器、有外掛、有檔案格式、在 sanitizer 下零報告的虛擬機器——下一站可以是 Linux 核心原始碼、CPython 的 `ceval.c`，或是你自己的編譯器。
