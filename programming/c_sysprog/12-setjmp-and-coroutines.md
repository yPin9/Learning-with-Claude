# Ch 12 — setjmp/longjmp 與協同式多工

> **目標**：搞懂 `setjmp`/`longjmp` 在暫存器與 stack 層級到底存了什麼、為什麼有 `volatile` 的限制，並用它做出例外處理與協同式排程器，最後對照有獨立 stack 的 `ucontext`，知道「共用 stack」為何是這類實作的死穴。

> **對應原文**：[你所不知道的 C 語言：函式呼叫篇](https://hackmd.io/@sysprog/c-function) 的「從函式呼叫到協同式多工」「setjmp 與 longjmp」「將 setjmp/longjmp 改造為協同式排程器」等節

> **環境**：gcc 11.4、clang 14、gdb 12.1，glibc 2.35，WSL2 Ubuntu 22.04 x86-64。Ubuntu 的 gcc 預設啟用 `-fstack-protector-strong`、`-D_FORTIFY_SOURCE=2`（`-O1` 以上）與 `-fcf-protection`，會影響下面的組語輸出，遇到時會註明。

## 為什麼需要這個？

`goto` 只能在同一個函式裡跳。想像一個五層深的呼叫鏈：解析器呼叫詞法分析器，後者呼叫讀檔函式，讀檔函式在最底層發現檔案壞了。怎麼讓「最底層」的錯誤直接回報給「最上層」？

- **一層一層回傳錯誤碼**：每一層都要檢查 `if (ret < 0) return ret;`。程式碼被錯誤處理淹沒，漏掉一層檢查就是 bug。
- **用全域旗標加提早 return**：和上面同樣的問題。
- **非區域跳躍**（non-local jump）：在最上層標記一個「記錄點」，最底層直接跳回去，中間的層全部略過。這就是 `setjmp`/`longjmp`，標準 C 唯一的跨函式跳躍機制。

C 沒有 `try/catch`，所以 `setjmp`/`longjmp` 長期是 C 的例外處理手段。就我所知，libpng 預設以它回報解碼錯誤，Lua 在以 C 編譯時也用它實作錯誤的拋出（C++ 編譯時才改用真正的 exception），不過具體實作細節請以各專案原始碼為準。

它還有第二個用途：既然能存下「當下的執行環境」並在別處恢復，就能在多個「執行流」之間切換，也就是**協同式多工**（cooperative multitasking）。本章兩個用途都做一遍。

## 先建立直覺

把 `setjmp` 想成遊戲的**存檔點**，`longjmp` 是**讀檔**：

```
  main                                   level1       level2       level3
   │ setjmp(env)  ← 存檔（回傳 0）
   │───────────────▶ level1() ──────────▶ level2() ──────▶ level3()
   │                                                          │ longjmp(env, 12) ← 讀檔
   │◀═════════════════════════════════════════════════════════╯
   │ setjmp 「又」回傳了一次，這次回傳 12
```

這個比喻在一個關鍵處不成立：遊戲存檔會把**整個世界**存下來，`jmp_buf` 只存**暫存器**（以及 `rsp`、返回位址），**不存記憶體內容**。讀檔之後，堆上的資料、全域變數維持「讀檔當下」的狀態，而不是存檔時的狀態。這是理解 `volatile` 限制的鑰匙。

> 如果你對 stack frame、`rsp`、`rbp` 還不熟，先回看 [Ch 11](./11-process-layout-and-abi.md) 的「用 gdb 逐步看一次呼叫」與 callee-saved 暫存器一節。

## 基本用法

`sj_basic.c`：

```c
#include <setjmp.h>
#include <stdio.h>

static jmp_buf env;

static void level3(int n)
{
    printf("  level3: n=%d, 準備 longjmp\n", n);
    longjmp(env, n);                 /* 不會回來 */
    printf("  level3: 這行永遠不會印\n");
}
static void level2(int n) { printf(" level2\n"); level3(n + 1); printf(" level2: 不會印\n"); }
static void level1(int n) { printf("level1\n");  level2(n + 1); printf("level1: 不會印\n"); }

int main(void)
{
    int v = setjmp(env);
    if (v == 0) {
        printf("setjmp 第一次回傳 0\n");
        level1(10);
    } else {
        printf("setjmp 又回傳了：%d（由 longjmp 帶回）\n", v);
    }
    return 0;
}
```

```
$ gcc -Wall -Wextra -g sj_basic.c -o sj_basic && ./sj_basic
setjmp 第一次回傳 0
level1
 level2
  level3: n=12, 準備 longjmp
setjmp 又回傳了：12（由 longjmp 帶回）
```

三件事要注意：

1. **`setjmp` 回傳兩次**：第一次是直接呼叫，回傳 `0`；之後每次 `longjmp(env, v)`，都讓同一個 `setjmp` 呼叫「再回傳一次」，值為 `v`。
2. **`longjmp(env, 0)` 會被改成回傳 `1`**（規格書 7.13.2.1：`longjmp` 不能讓 `setjmp` 回傳 0，否則無法區分「直接呼叫」與「跳回來」）。實測（`sj_zero.c`）：

```
第一次：setjmp 回傳 0
setjmp 回傳了 1（雖然 longjmp 傳的是 0）
```

3. **`level1`、`level2`、`level3` 中 `longjmp` 之後的 `printf` 都不會執行**，它們的 frame 被一口氣丟棄，沒有 epilogue 也沒有 `ret`。

> 我在這段程式用了 `int v = setjmp(env);`，為了貼近原文的寫法。但嚴格說，這個寫法**不在規格書允許的範圍內**，見下一節。

## 規格書的限制：setjmp 只能出現在特定語境

N1570 7.13.1.1p4 規定，`setjmp` 的呼叫只能出現在下列語境之一：

- 作為 `if`、`switch`、`while`、`do`、`for` 的完整控制運算式
- 作為關係或相等運算（`<`、`==` 等）的一個運算元，另一個運算元是整數常數運算式，且整個比較是控制運算式
- 作為 `!` 運算的運算元，且整個 `!` 運算是控制運算式
- 作為獨立的運算式述句（可以被轉型為 `void`）

所以 `if (setjmp(env) == 0)`、`switch (setjmp(env))`、`setjmp(env);` 合法；`int v = setjmp(env);` 與 `f(setjmp(env))` 在規格書上是未定義行為。原因是 `setjmp` 回傳兩次，如果它是一個大運算式的一部分，第二次回傳時「這個運算式算到一半的暫存狀態」無法保證。實務上 gcc/clang 在簡單的 `int v = setjmp(env);` 都能運作，但正式程式碼應遵守規格書的寫法；要拿到 `setjmp` 的回傳值，用 `switch` 或 `if/else if` 分流。

## 底層機制：jmp_buf 裡到底存了什麼？

規格書只說 `jmp_buf` 是「能存足以恢復呼叫環境資訊的陣列型別」。glibc 的 x86-64 實作可以直接用 gdb 看，我們在 `sj_basic` 上反組譯 `__sigsetjmp` 與 `__longjmp`（`setjmp` 巨集在 glibc 展開成 `_setjmp(env)`，它再跳到 `__sigsetjmp(env, 0)`）：

```
(gdb) disas __sigsetjmp            # 節錄，Intel 語法；rdi = jmp_buf 的位址
   <+4>:   mov    QWORD PTR [rdi],rbx           # [0x00] rbx
   <+7>:   mov    rax,rbp
   <+10>:  xor    rax,QWORD PTR fs:0x30         #        rbp 先與 pointer guard 做 XOR
   <+19>:  rol    rax,0x11                      #        再循環左移 17 bit
   <+23>:  mov    QWORD PTR [rdi+0x8],rax       # [0x08] 混淆後的 rbp
   <+27>:  mov    QWORD PTR [rdi+0x10],r12      # [0x10] r12
   <+31>:  mov    QWORD PTR [rdi+0x18],r13      # [0x18] r13
   <+35>:  mov    QWORD PTR [rdi+0x20],r14      # [0x20] r14
   <+39>:  mov    QWORD PTR [rdi+0x28],r15      # [0x28] r15
   <+43>:  lea    rdx,[rsp+0x8]                 #        rsp+8 = setjmp 返回後呼叫者的 rsp
   <+48>:  xor    rdx,QWORD PTR fs:0x30
   <+57>:  rol    rdx,0x11
   <+61>:  mov    QWORD PTR [rdi+0x30],rdx      # [0x30] 混淆後的 rsp
   <+65>:  mov    rax,QWORD PTR [rsp]           #        [rsp] = setjmp 的返回位址
   <+70>:  nop
   <+70>:  xor    rax,QWORD PTR fs:0x30
   <+79>:  rol    rax,0x11
   <+83>:  mov    QWORD PTR [rdi+0x38],rax      # [0x38] 混淆後的返回位址（= 回來後要執行的 rip）
   <+87>:  test   DWORD PTR fs:0x48,0x2
   <+99>:  je     <+110>
   <+101>: rdsspq rax                           # CET 啟用時：存 shadow stack pointer
   <+106>: mov    QWORD PTR [rdi+0x58],rax
   <+110>: jmp    __sigjmp_save                 # 處理 signal mask 的部分
```

對應的 `__longjmp`（節錄）：

```
   <+4>:   mov    r8,QWORD PTR [rdi+0x30]       # 取混淆的 rsp
   <+8>:   mov    r9,QWORD PTR [rdi+0x8]        # 取混淆的 rbp
   <+12>:  mov    rdx,QWORD PTR [rdi+0x38]      # 取混淆的返回位址
   <+16>:  ror    r8,0x11 ; xor r8,fs:0x30      # 還原：先右移再 XOR
   ...                                          # （中間處理 CET shadow stack）
   <+114>: mov    rbx,QWORD PTR [rdi]           # 還原 callee-saved
   <+117>: mov    r12,QWORD PTR [rdi+0x10]
   ...
   <+133>: mov    eax,esi                       # longjmp 的第二參數 val -> eax：這就是 setjmp 的「第二次回傳值」
   <+135>: mov    rsp,r8                        # 換回舊的 stack pointer
   <+138>: mov    rbp,r9
   <+142>: jmp    rdx                           # 跳到存好的返回位址
```

整理成表：

| 偏移 | 內容 | 為什麼存它 |
|---|---|---|
| `0x00` | `rbx` | callee-saved（Ch 11），`longjmp` 後呼叫者期待它沒被改 |
| `0x08` | `rbp`（混淆） | 同上 |
| `0x10`–`0x28` | `r12`–`r15` | 同上 |
| `0x30` | `rsp`（混淆） | 要恢復的 stack pointer：其實存的是「`setjmp` 返回後」的 `rsp`，即 `rsp + 8` |
| `0x38` | 返回位址（混淆） | 恢復後要跳去的 `rip` |
| `0x58` | shadow stack pointer | 啟用 CET 時才存（就我所知這是 glibc 把它放在 `jmp_buf` 尾端保留區的做法） |

幾個重點：

- **`rax`、`rcx`、`rdx` 等 caller-saved 暫存器沒存**：`setjmp` 是一個函式呼叫，呼叫者本來就不能假設這些暫存器在呼叫之後還保持原樣，這是 ABI 給的便利。
- **`longjmp` 的第二個引數直接放進 `eax`**，因為 `setjmp` 的返回值本來就在 `eax`，所以「再回傳一次」不需要任何特殊機制：`longjmp` 把 `rsp`/`rbp` 換回去、`eax` 填好值、`jmp` 到 `setjmp` 的返回位址，就像 `setjmp` 剛返回一樣。
- **指標混淆（pointer mangling）**：`rbp`、`rsp`、返回位址不是原樣存放，而是先與 `fs:0x30`（每個行程啟動時隨機產生的 *pointer guard*）做 XOR 再循環左移 17 bit。目的是防禦：如果攻擊者能覆寫整個 `jmp_buf`，也無法直接寫入一個真實位址讓 `longjmp` 跳過去，因為不知道 pointer guard。這是 Ch 14 談的縱深防禦的一個實例。
- `sizeof(jmp_buf)` 在這個環境是 `200`：8 個 `long`（64 bytes）存暫存器、一個 `int` 旗標（加 padding 共 8 bytes）、128 bytes 的 `sigset_t`（存 signal mask，可選）。

換句話說，**`setjmp` 存下來的是「如何回到這個 frame」，不是「這個 frame 的內容」**。frame 的內容（區域變數）還留在 stack 上，`longjmp` 不備份也不還原它。

## 限制一：volatile 與「存檔之後才改」的區域變數

規格書 7.13.2.1 規定：包含 `setjmp` 呼叫的那個函式裡，**不是 `volatile` 且在 `setjmp` 呼叫之後、`longjmp` 之前被修改過**的自動變數，在 `longjmp` 返回後值是不確定的（indeterminate）。

`sj_volatile.c`：

```c
int main(void)
{
    int plain = 0;             /* 沒有 volatile */
    volatile int safe = 0;     /* 有 volatile   */

    if (setjmp(env) == 0) {
        plain = 42;            /* setjmp 之後才修改 */
        safe  = 42;
        thrower();             /* 裡面 longjmp(env, 1) */
    }
    printf("plain = %d, safe = %d\n", plain, safe);
    return 0;
}
```

```
gcc -O0: plain = 42, safe = 42
gcc -O1: plain = 0, safe = 42
gcc -O2: plain = 0, safe = 42
clang -O2: plain = 0, safe = 42
```

`-O0` 時所有變數都放記憶體，所以「碰巧」正確；`-O1` 起 `plain` 變成 `0`。看 `-O1` 的組語：

```asm
main:
    subq    $24, %rsp
    movl    $0, 12(%rsp)            # safe = 0（volatile，放在記憶體）
    leaq    env(%rip), %rdi
    call    _setjmp@PLT
    testl   %eax, %eax
    je      .L5                     # 回傳 0：走第一次路徑（.L5 沒貼出，內含 plain/safe = 42 與 thrower()）
    movl    12(%rsp), %ecx          # 讀 safe：從記憶體讀 -> 42
    movl    $0, %edx                # plain：編譯器直接放常數 0！
    ...
```

編譯器的推理很合理：在「`setjmp` 回傳非 0」這條路徑上，沒有任何程式碼寫過 `plain`（寫入發生在 `je .L5` 之後的另一條路徑），所以 `plain` 一定還是初始值 `0`。它沒有義務假設「有人從另一條路徑跳回來」。`volatile` 就是告訴編譯器「這個變數隨時可能被你看不見的路徑改變，每次都要從記憶體讀」。

這也意味著：**只有「`setjmp` 所在函式的自動變數」受影響**，全域變數、堆上的物件、其他函式的區域變數都不受此條款約束，因為它們在 `longjmp` 之前就寫進了記憶體。`gcc -Wextra` 內含 `-Wclobbered`，但它是啟發式警告：上面這個例子在 gcc 11.4 `-O2 -Wall -Wextra` 沒有警告，**不能指望它抓到所有情況**。

## 限制二：目標函式必須還活著

`longjmp` 只能跳回一個「**還在 stack 上**」的 `setjmp` 呼叫。如果呼叫 `setjmp` 的函式已經返回，那個 frame 不存在了，行為未定義。`sj_dead.c`：

```c
__attribute__((noinline)) static void arm(void)
{
    if (setjmp(env) == 0)
        return;                  /* arm() 結束了，它的 frame 已不存在 */
    puts("從已經 return 的 arm() 裡回來了？");
}
int main(void)
{
    arm();
    puts("arm() 已返回，現在 longjmp 回去");
    longjmp(env, 1);             /* 未定義行為：目標函式已終止 */
}
```

```
$ gcc -O0 -U_FORTIFY_SOURCE -fno-stack-protector sj_dead.c -o dead0 && ./dead0
Segmentation fault (core dumped)
$ gcc -O2 sj_dead.c -o dead2 && timeout 5 ./dead2
arm() 已返回，現在 longjmp 回去
從已經 return 的 arm() 裡回來了？
arm() 已返回，現在 longjmp 回去
從已經 return 的 arm() 裡回來了？
...（無限重複，被 timeout 結束）
```

兩種結果都是「運氣」：`-O0` 崩潰，`-O2` 因為 `arm` 的返回位址還躺在 stack 上沒被蓋掉，反而一路返回到 `main` 再來一次，無限迴圈。`-O2` 這個版本的二進位檔裡 `longjmp` 已被換成 `__longjmp_chk`（`_FORTIFY_SOURCE=2` 的效果，它只檢查「是否往更深的 frame 跳」，這個案例沒被攔下）。**不要依賴任何「看起來有運作」的未定義行為。**

另一個限制是 signal mask。`setjmp`（glibc 實作為 `_setjmp`）預設**不**存 signal mask；要存就用 `sigsetjmp(env, 1)` / `siglongjmp`（POSIX）。實驗（`sigsj.c`）：SIGALRM 的 handler 裡呼叫 `siglongjmp`，而進入 handler 時核心會自動把 SIGALRM 加進 blocked mask：

```
savemask=1：setjmp 前 SIGALRM blocked = 0
  handler 內：SIGALRM blocked = 1
savemask=1：longjmp 後 SIGALRM blocked = 0       ← 有存 mask：跳回後還原為 0
savemask=0：setjmp 前 SIGALRM blocked = 0
  handler 內：SIGALRM blocked = 1
savemask=0：longjmp 後 SIGALRM blocked = 1       ← 沒存 mask：SIGALRM 一直被擋住，後續不會再收到
```

`savemask=0` 的後果是：離開 handler 的方式從「正常返回」（核心 `sigreturn` 會還原 mask）變成「`longjmp`」（沒人還原），SIGALRM 永遠被擋。從 signal handler 跳出，一律用 `sigsetjmp(env, 1)`。

## 實作：用 setjmp/longjmp 做例外處理

把「記錄點」做成一個**鏈結串列的 stack**，每個 `TRY` 區塊推入一個 `jmp_buf`，離開時彈出，`THROW` 就跳到最內層：

```c
struct exc_frame {
    jmp_buf env;
    struct exc_frame *prev;       /* 外層 TRY 的 frame */
};
static struct exc_frame *exc_top; /* 目前最內層的 TRY（單執行緒） */
static const char *exc_msg;
enum { EXC_NONE = 0, EXC_PARSE = 1, EXC_RANGE = 2 };

#define TRY                                              \
    do {                                                 \
        struct exc_frame exc_f;                          \
        int exc_code;                                    \
        exc_f.prev = exc_top;                            \
        exc_top = &exc_f;                                \
        exc_code = setjmp(exc_f.env);                    \
        if (exc_code == 0) {
#define CATCH(c)                                         \
            exc_top = exc_f.prev;      /* 正常離開 TRY */ \
        } else if (exc_code == (c)) {                    \
            exc_top = exc_f.prev;      /* 進入 handler */ \
#define END_TRY                                          \
        } else {                                         \
            exc_top = exc_f.prev;                        \
            THROW(exc_code, exc_msg);  /* 沒人接，往外丟 */ \
        }                                                \
    } while (0)

static void THROW(int code, const char *msg)
{
    if (!exc_top) { fprintf(stderr, "uncaught exception %d: %s\n", code, msg); exit(2); }
    exc_msg = msg;
    longjmp(exc_top->env, code);
}
```

（完整可編譯版 `exc.c` 另有 `parse_digit`、`checked_div` 與 `run`/`main`，這裡省略。注意 `exc_code = setjmp(...)` 是賦值述句，嚴格說不在 7.13.1.1p4 的允許語境，是為了簡潔的取捨；gcc/clang 都能正確處理，但要以標準寫法重寫，可改成 `switch (setjmp(exc_f.env)) { case 0: ... }`。）

`main` 的測試：

```c
run("8", "2");          /* 正常 */
run("x", "2");          /* EXC_PARSE：被 run() 內的 CATCH 接住 */
TRY { run("8", "0"); } CATCH(EXC_RANGE) { printf("[range] %s (在 main 接住)\n", exc_msg); } END_TRY;
run("9", "0");          /* EXC_RANGE 沒人接 -> uncaught */
```

```
8 / 2 = 4
[parse] not a digit: "x" "2"
[range] divide by zero (在 main 接住)
uncaught exception 2: divide by zero
```

第三個案例展示了**往外傳遞**：`run()` 的 `TRY` 只有 `CATCH(EXC_PARSE)`，`EXC_RANGE` 進到 `END_TRY` 的 `else` 分支被重新 `THROW`，此時 `exc_top` 已指向外層（`main` 的 `TRY`），所以由 `main` 的 `CATCH(EXC_RANGE)` 接住。`gcc -O0`、`-O2`、`-fsanitize=address,undefined` 輸出一致。

這個機制的先天缺陷，也是 `setjmp` 例外處理在現實中麻煩的地方：

- **沒有 stack unwinding**：中間被跳過的函式裡的 `malloc` 不會被 `free`、檔案不會被 `fclose`。C++ 的 exception 會呼叫解構函式，這裡什麼都不做。要嘛在 `TRY` 區塊內不配置資源，要嘛自己維護清理鏈（對照 [Ch 31 goto 與流程控制](./31-goto-and-control-flow.md) 的 cleanup chain）。
- **區域變數的 volatile 規則**：在 `TRY` 內修改、要在 `CATCH` 裡讀的變數必須是 `volatile`。上例的 `x`、`y` 沒被 handler 用到，所以安全。
- **非 reentrant、非 thread-safe**：`exc_top` 是全域變數，多執行緒要改成 `_Thread_local`。

## 實作：協同式排程器（共用 stack 版）

**協同程式（coroutine）** 是可以暫停並稍後從中斷處繼續的執行流，概念由 Melvin Conway 在 1958 年提出。和執行緒不同，coroutine 之間沒有搶占：一個 coroutine 主動 `yield` 才會換下一個。

我們用 `setjmp`/`longjmp` 實作：每個 task 把自己的環境存進 `jmp_buf`，`yield` 時放回佇列尾端，`longjmp` 到佇列頭端 task 的環境。這是原文引用的 `coro.c` 的精神，我重寫成不依賴 `list_head` 的版本 `coro_sj.c`：

```c
struct task { jmp_buf env; int id; int step; int nsteps; };   /* 狀態全放 heap */
static jmp_buf sched_env;
static struct task *runq[16];  static int q_head, q_tail;
static struct task *cur;

static void yield(void)
{
    struct task *self = cur;
    if (setjmp(self->env) == 0) {     /* 存檔 */
        q_push(self);
        cur = q_pop();
        longjmp(cur->env, 1);          /* 讀別人的檔 */
    }
    /* 被 longjmp 喚醒後從這裡繼續；cur 已被排程方設成 self */
}

static void task_body(int id, int nsteps)
{
    struct task *t = malloc(sizeof *t);
    t->id = id; t->step = 0; t->nsteps = nsteps;
    if (setjmp(t->env) == 0) {         /* 第一次：登記自己，回排程器啟動下一個 */
        q_push(t);
        longjmp(sched_env, 1);
    }
    t = cur;                           /* 喚醒後用全域 cur 重新取得（stack 上的 t 不可信） */
    while (t->step < t->nsteps) {
        printf("task %d: step %d\n", t->id, t->step);
        t->step++;
        yield();
        t = cur;
    }
    printf("task %d: done\n", t->id);
    free(t);
    cur = q_pop();
    if (cur) longjmp(cur->env, 1);
    longjmp(sched_env, 2);
}
```

`main` 依序啟動 3 個 task（`task_body(0, 2)`、`(1, 3)`、`(2, 4)`），登記完再開跑第一個。編譯執行：

```
$ gcc -Wall -Wextra -O0 coro_sj.c -o coro_sj && ./coro_sj
task 0: step 0
task 1: step 0
task 2: step 0
task 0: step 1
task 1: step 1
task 2: step 1
task 0: done
所有 task 完成
```

**預期有 task 1 與 task 2 各自的 `step 2`、`done`，結果不見了。** `-O0` 與 `-O2` 輸出一樣（也不是編譯器的最佳化造成的）。這是我故意保留的壞版本，值得花時間拆解。

### 為什麼壞掉：所有 task 共用同一條 stack

```
   時刻 A：task 1 在 yield() 裡存檔            時刻 B：task 0 結束時
   (task 1 的 frame 在 stack 這個區段)          (task 0 在同一個區段做別的事)

   高 ┌──────────────┐                        ┌──────────────┐
      │ main         │                        │ main         │
      ├──────────────┤                        ├──────────────┤
      │ task_body    │ ← 所有 task 的           │ task_body    │ ← task 0 的
      ├──────────────┤   task_body 在同一個位置   ├──────────────┤
      │ yield 的 frame│ ← task 1 的 jmp_buf      │ printf/free/ │ ← 蓋掉了 task 1
      │ (返回位址…)   │   rsp 指在這             │ q_pop 的 frame│   存檔點的 frame
   低 └──────────────┘                        └──────────────┘
```

`jmp_buf` 只存 `rsp`（指向 stack 的某處），不存那塊 stack 的內容。三個 task 的 `task_body` 都是被 `main` 同一個地方呼叫的，所以它們的 frame 落在**同一塊記憶體**。task 0 繼續跑的時候，它做的一切都在寫這塊記憶體，task 1 在 `yield` 裡的存檔點（返回位址、saved rbp）就被覆蓋了。

為什麼前面的切換都沒事？因為在 task 0 結束之前，每個 task 在切走之前最後一件事都是呼叫 `yield()`，而且是從**同一個 call site** 呼叫，`yield` 的 frame 內容（返回位址、saved rbp）每次寫得完全一樣，所以每個 task 切走時，等於都**重新寫了一遍所有人共用的存檔點**。task 0 結束時走的是另一條路徑：`printf("done")` → `free` → `q_pop` → `longjmp`，這些呼叫的 frame 蓋掉了 `yield` 留下的內容，之後 `longjmp` 回 task 1 的存檔點時，stack 上的返回位址已是垃圾（這個環境下恰好走到 `main` 的 `longjmp(sched_env, 2)` 收尾路徑，所以印出「所有 task 完成」）。

加上 `-fstack-protector-all` 時，這個壞版本更會被 canary 抓到：

```
*** stack smashing detected ***: terminated
```

這正是規格書把「`longjmp` 到已經不存在的 frame」列為未定義行為的原因：stack 內容不再是 `setjmp` 當時的樣子。

### 讓它在這個特例下運作：單一 call site

既然 `jmp_buf` 假設的是「stack 內容不變」，就讓所有 task 的 stack 內容**一定相同**：所有切換都從同一個 call site 進入 `yield`。`coro_sj_ok.c` 的 `task_body` 主迴圈改成：

```c
    for (;;) {
        int finished = 0;
        if (t->step < t->nsteps) {
            printf("task %d: step %d\n", t->id, t->step);
            t->step++;
        } else {
            printf("task %d: done\n", t->id);
            finished = 1;
        }
        yield(finished);                /* 全程式唯一的 yield call site */
        t = cur;
    }
```

`yield(int finished)` 內部：`finished` 時 `free(self)` 而不 `q_push`，佇列空了就 `longjmp(sched_env, 2)`。執行結果（`-O0`、`-O2`、`-fstack-protector-all`、`-fsanitize=address` 皆通過）：

```
task 0: step 0
task 1: step 0
task 2: step 0
task 0: step 1
task 1: step 1
task 2: step 1
task 0: done
task 1: step 2
task 2: step 2
task 1: done
task 2: step 3
task 2: done
所有 task 完成
```

但要認清楚：**這依然是未定義行為，只是在「所有 task 同形、單一 call site、狀態全在 heap」這個極窄的條件下碰巧能動。** 任何一個 task 在 `yield` 之前呼叫了另一個函式留下不同的 frame、或把區域變數的位址交給別人，這個設計就崩潰。教學價值在於讓你看到 `jmp_buf` 的真正語意，而不是鼓勵你這樣寫。

## 實作：有獨立 stack 的 coroutine（ucontext）

根本的解法是**每個 coroutine 有自己的 stack**。POSIX 的 `<ucontext.h>` 提供：

| 函式 | 作用 |
|---|---|
| `getcontext(&c)` | 把目前的 CPU 狀態存進 `c`（初始化用） |
| `makecontext(&c, fn, argc, ...)` | 修改 `c`，使得切換過去時從 `fn` 開始執行，用 `c.uc_stack` 指定的 stack |
| `swapcontext(&from, &to)` | 存目前狀態到 `from`、載入 `to`（一次完成 `setjmp` + `longjmp`） |
| `uc_link` | `fn` 返回時要接續的 context |

`coro_uc.c`（節錄）：

```c
#define STACK_SIZE (64 * 1024)
struct task { ucontext_t ctx; char *stack; int id, step, nsteps, done; };

static void task_entry(void)
{
    struct task *t = cur;
    int local_counter = 100 * t->id;              /* 放在「自己的」stack，跨 yield 仍有效 */
    while (t->step < t->nsteps) {
        printf("task %d: step %d (local_counter=%d)\n", t->id, t->step, local_counter);
        t->step++;
        local_counter++;
        swapcontext(&t->ctx, &sched_ctx);         /* yield */
    }
    printf("task %d: done\n", t->id);
    t->done = 1;                                  /* 返回時依 uc_link 回排程器 */
}
/* main：對每個 task：t->stack = malloc(STACK_SIZE); getcontext; 設 uc_stack、uc_link = &sched_ctx;
   makecontext(&t->ctx, task_entry, 0)。排程器 round-robin 地 swapcontext(&sched_ctx, &task->ctx)。 */
```

```
$ gcc -Wall -Wextra -g coro_uc.c -o coro_uc && ./coro_uc
task 0: step 0 (local_counter=0)
task 1: step 0 (local_counter=100)
task 2: step 0 (local_counter=200)
task 0: step 1 (local_counter=1)
task 1: step 1 (local_counter=101)
task 2: step 1 (local_counter=201)
task 0: done
task 1: step 2 (local_counter=102)
task 2: step 2 (local_counter=202)
task 1: done
task 2: step 3 (local_counter=203)
task 2: done
所有 task 完成
```

關鍵差異：`local_counter` 是普通的區域變數，跨 `yield` 完全正常，因為每個 task 的 stack 是獨立的 64 KiB 區塊，沒有任何人會覆蓋它。`-fsanitize=address` 同樣通過。

用 `ucontext` 還能做 generator（`gen_uc.c`：`fib_gen` 在無窮迴圈裡 `yield` 出下一個 Fibonacci 數）：

```
$ ./gen_uc
0 1 1 2 3 5 8 13 21 34 55 89
```

### 成本：swapcontext 比 longjmp 慢兩個數量級

`bench_sw.c` 量測（`-O2`，執行兩次的值相近）：

```
swapcontext  : 804 ns / 來回一趟 (2 次切換)
setjmp/longjmp: 7 ns / 一次 longjmp
```

（同一台機器的粗略比較：`setjmp`/`longjmp` 的測試只量跳躍本身，沒有切換 stack，並不等價於 swapcontext 的功能；請把它們當成「暫存器保存」與「加上 signal mask 系統呼叫」的成本差距。）

差距來自 `swapcontext` 會呼叫 `rt_sigprocmask` 系統呼叫來保存與還原 signal mask。用 `strace -c -f ./coro_uc`，這個只有 12 次 resume、9 次 yield、3 次結束返回的小程式就呼叫了 **27** 次 `rt_sigprocmask`（12 + 9 + 3，再加 3 次 `getcontext` 初始化）。每次系統呼叫要進出核心，這就是 800 ns 的主因，也是 minicoro、libco 這類函式庫改用手寫組語切換 stack、完全不碰 signal mask 的理由。

POSIX.1-2008 已經把 `getcontext`/`makecontext`/`swapcontext` 從規範移除（標為過時並建議改用 POSIX threads），但 glibc 仍提供，新程式碼要考慮可攜性。

## 對比與取捨

| 方案 | stack | 切換成本 | 可攜性 | 適用情境 |
|---|---|---|---|---|
| `goto` | 同一個 frame | 0 | 標準 C | 函式內的錯誤清理 |
| `setjmp`/`longjmp` 做例外 | 往上跳回舊 frame | 約 10 ns | 標準 C | 錯誤回報、從 signal handler 跳出 |
| `setjmp`/`longjmp` 做 coroutine | 共用（UB，只在特例運作） | 約 10 ns | 標準 C，但不正確 | 教學示範 |
| `ucontext` | 每個 coroutine 一條 | 約 400 ns／次切換 | POSIX（已移除規範）、glibc 有 | 需要獨立 stack 的原型 |
| 手寫組語切換（minicoro、libco） | 每個一條 | 數十 ns | 每個架構要寫一份 | 高效能的使用者層排程 |
| POSIX threads | 每個一條，核心排程 | 數 μs，且可平行 | POSIX | 真正的平行 |
| C++ exception | 往上 unwinding，呼叫解構函式 | throw 時很貴，沒拋出時接近 0 | C++ | 需要自動清理資源 |

**為什麼 C 沒有內建 coroutine？** C 標準委員會的原則是不引入需要 runtime 配置與管理額外 stack 的機制：C 的函式呼叫模型就是「一條 stack」，其他需求交給 library（如 POSIX threads、第三方 coroutine 庫）。

## 踩雷集錦

1. **以為 `longjmp` 之後所有變數回到 `setjmp` 當時的值**：它只還原暫存器，記憶體維持現狀；`setjmp` 之後修改過、非 `volatile` 的區域變數是 indeterminate（`sj_volatile.c`，`-O1` 起 `plain = 0`）。
2. **在 `setjmp` 所在函式返回之後 `longjmp`**：未定義行為，`-O0` 崩潰、`-O2` 無限迴圈（`sj_dead.c`）。把 `jmp_buf` 放全域不會讓這件事合法。
3. **用 `longjmp` 跳過 `free`／`fclose`**：沒有 unwinding，資源洩漏。更危險的是跳過會 `unlock` 的程式碼，造成死鎖。
4. **在 signal handler 裡用 `longjmp` 卻沒用 `sigsetjmp(env, 1)`**：signal mask 沒還原，該訊號之後被永遠擋住（`sigsj.c`）。而且 POSIX 只保證少數 async-signal-safe 函式能在 handler 裡呼叫，跳出去之後也不能假設被中斷的函式（如 `malloc`）的內部狀態是一致的。
5. **多執行緒共用 `jmp_buf`**：`longjmp` 只能跳回同一個執行緒的 frame；跨執行緒是未定義行為。例外 frame 鏈 `exc_top` 要改成 `_Thread_local`。
6. **把 `int v = setjmp(env);` 當成合法寫法**：它在 7.13.1.1p4 之外，要用 `if` 或 `switch`。

## 進階：再往深一層

**`__builtin_setjmp` / `__builtin_longjmp`**：gcc 內建的輕量版本，只存 `rbp`、`rsp`、跳轉位址三個值（其他暫存器靠編譯器在呼叫點前主動 spill），比 libc 的快，gcc 的 SJLJ exception 機制與一些 JIT 使用它。限制更多（`longjmp` 只能傳 1，且必須在同一個 `-O` 下配對使用）。

**C++ 的 exception 為何不用 setjmp**：現代 ABI（Itanium C++ ABI）採「table-driven unwinding」：函式在 `.eh_frame` 裡留下描述「哪個位址範圍有哪些解構函式要呼叫」的表格，不拋出時**零成本**，拋出時由 `libgcc` 的 unwinder 查表逐 frame 回溯。早期與某些嵌入式平台使用 SJLJ（setjmp/longjmp-based）exception，每次進入 `try` 都要付出 `setjmp` 的代價。

**ASan 與 `longjmp`**：AddressSanitizer 的 shadow memory 會記錄 stack 上哪些位置是「毒化」的（例如 redzone）。`longjmp` 跳過的 frame 不會走正常的 epilogue 還原毒化，因此 ASan 對 `longjmp`、`swapcontext` 有特殊處理（攔截後清除被跳過範圍的 shadow）。就我所知，自己手寫組語切換 stack 的 coroutine 與 ASan 搭配時，需要用 `__sanitizer_start_switch_fiber` 等 API 通知它，否則可能出現誤報；細節請查 sanitizer 文件。

**`setjmp` 與資安**：`jmp_buf` 裡存著 `rsp` 與跳轉位址，如果它存在可被覆寫的位置，就是一個控制流劫持的目標，上面看到的 pointer mangling 就是為此而設。這個攻擊面屬於 [Ch 14](./14-function-call-security.md) 的主題。

## 動手練習

1. 把 `exc.c` 的 `TRY` 改成 `switch (setjmp(exc_f.env))` 的合法寫法，並加入 `FINALLY` 區塊（無論有無例外都執行一次，然後把例外往外傳）。
2. 在 `sj_volatile.c` 加上 `-Wclobbered`，找出一個會觸發警告的最小修改（提示：讓編譯器必須把變數放在暫存器，例如在迴圈裡改變數）。
3. 把 `coro_sj_ok.c` 的 `task_body` 改成在 `yield` 之前先呼叫一個會用到較大區域陣列的函式，觀察排程器是否仍然正確；再解釋為什麼。
4. 用 `ucontext` 實作 producer/consumer：producer 每次 `yield` 一個整數，consumer 在主流程中印出。
5. 查閱 `man 3 makecontext`，說明為什麼它的 `argc` 之後只能傳 `int` 引數。

## 本章重點整理

- `setjmp` 存的是暫存器（callee-saved、`rsp`、返回位址），不是記憶體；`longjmp` 把 `rsp` 換回、`eax` 設成 `val`、`jmp` 回返回位址。
- 不是 `volatile` 且 `setjmp` 之後被修改的區域變數，`longjmp` 後是不確定值；`setjmp` 只能出現在規格書允許的語境。
- 共用 stack 的 `setjmp` coroutine 是未定義行為，只在極窄條件下碰巧運作；`ucontext`／手寫組語給每個 coroutine 獨立 stack，是正確解。
- `swapcontext` 因為 `rt_sigprocmask` 系統呼叫，比 `longjmp` 慢兩個數量級。

## 自我檢核

- [ ] 不看筆記，能說出 `jmp_buf` 裡存了哪些暫存器，以及為什麼不必存 `rax`、`rcx`、`rdx`？
- [ ] 能解釋「為什麼 `-O2` 下 `plain` 變成 0」，而不是只說「要加 `volatile`」？
- [ ] 如果面試官問「用 `setjmp`/`longjmp` 實作 coroutine 會出什麼問題」，你能說出共用 stack 的具體失效機制嗎？
- [ ] 想一個你寫過的、錯誤要穿過 3 層以上函式才處理的程式，用 `setjmp` 重寫會遇到哪些資源清理問題？
- [ ] 知道什麼情況下**不**該用 `longjmp`（提示：持有鎖、多執行緒、需要自動清理）。

## 延伸閱讀

### 官方文件 / Spec

- **[N1570 7.13「Nonlocal jumps」](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf)**
  - **讀哪裡**：7.13.1.1（`setjmp` 的語境限制）與 7.13.2.1（`longjmp` 的語意與 indeterminate 規則）。
  - **和本章的關聯**：`volatile` 限制與「目標函式必須還活著」兩條規則的原始條文。
- **`man 3 setjmp`、`man 3 sigsetjmp`、`man 3 makecontext`**
  - **讀哪裡**：NOTES 與 ATTRIBUTES 段；`sigsetjmp` 的 `savemask` 行為說明、`makecontext` 的限制。
  - **前提**：讀過本章即可。

### 部落格 / 技術文章

- **[sysprog21/concurrent-programs 的 coro](https://github.com/sysprog21/concurrent-programs/blob/master/coro/coro.c)** — jserv 團隊
  - **這篇說什麼**：原文引用的 `setjmp`/`longjmp` 排程器實作；讀完本章後去看，應能自己指出它在哪裡倚賴「碰巧」。
  - **前提**：Linux 核心風格的 `list_head`（[Ch 19](./19-linux-list-head.md)）。
- **[minicoro](https://github.com/edubart/minicoro)** — Eduardo Bart
  - **這篇說什麼**：單檔、用手寫組語切換 stack 的 C coroutine 函式庫；可以讀它的 x86-64 `switch` 實作，對照本章的 `__longjmp` 反組譯。
  - **前提**：本章與 [Ch 11](./11-process-layout-and-abi.md) 的暫存器保存規則。

### 書籍

- **《The Linux Programming Interface》** — Michael Kerrisk（No Starch Press，2010）
  - **讀哪幾章**：第 6.8 節「Nonlocal Gotos」講 `setjmp`/`longjmp` 的規則與陷阱；第 21 章講 signal handler 與 `sigsetjmp`。書出版較早，但這些章節的 POSIX 語意至今適用。

`setjmp` 展示了「stack 不一定要對稱彈出」；下一章我們看另一種和 stack 深度直接相關的技巧：遞迴，以及編譯器如何在 `-O2` 把它變成迴圈。

→ [Ch 13 遞迴與尾端遞迴最佳化](./13-recursion.md)
