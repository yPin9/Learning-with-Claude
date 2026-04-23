# Ch 19 — Breakpoint 的實作

> 目標：從 source 層次理解 software breakpoint、hardware breakpoint、watchpoint 的實作機制；能自己用 ptrace 下管理一個「斷點 table」、處理 re-arm 與多執行緒情境。

## 兩種 breakpoint

### Software breakpoint

- **原理**：把目標位址第一個 byte 改成 `0xcc`（x86 的 `int3` 指令）
- **觸發**：CPU 執行 `int3` 時，硬體產生 debug exception，kernel 發 `SIGTRAP` 給 tracee，tracer 收到通知
- **優點**：數量**沒上限**（只受你要改幾個 byte 限制）、不挑位址類型
- **缺點**：只能下在**可寫的 text segment**；某些極端情境改 byte 會跟 JIT / W^X 相衝

### Hardware breakpoint

- **原理**：x86 CPU 有 4 個 debug register（DR0–DR3）可以放「位址」，一個 DR7（control）可以開關並指定 trigger 條件（執行 / 讀 / 寫 / 讀寫）
- **觸發**：CPU 在每條 memory access / instruction fetch 時檢查 DR，match 就觸發 debug exception
- **優點**：不改 target memory（read-only section、ROM、shared lib 都能下）；可以 watch 讀寫資料（→ watchpoint）
- **缺點**：**只有 4 個**；不一定所有架構都有；處理器性能有微小影響

GDB 的 `break` 預設下 software；`hbreak` 強制硬體：

```
(gdb) hbreak main
```

Watchpoint（`watch var`）底層就是 hardware breakpoint 的 "watch data" 模式。

## Software breakpoint 的完整 round-trip

Ch 1 提過，這裡展開到 byte 層：

### 下斷點

```
用 PTRACE_PEEKDATA 讀目標位址的一個 word (8 byte on x86_64)
  orig_word = ptrace(PEEKDATA, pid, addr, 0)   // e.g. 0x5548e58948..
patched_word = (orig_word & ~0xFF) | 0xCC     // 最低 byte 改 0xCC
  -> 0x5548e58948..cc
ptrace(POKEDATA, pid, addr, patched_word)      // 寫回去
```

**只改一個 byte**（`int3` 是 single-byte opcode）。`0xcc` 會被 CPU 當 `int3` 指令、觸發 breakpoint exception。

### 命中

```
CPU 執行到 addr，看到 0xcc，觸發 #BP (vector 3) exception
kernel 收到 → 給 tracee 發 SIGTRAP
tracee 被停住
tracer 的 waitpid() 返回
```

此時 tracee 的 RIP = `addr + 1`（`int3` 已經 retire 了，PC 前進了一格）。

### 處理並繼續

```
1. 還原原本的 byte
   ptrace(POKEDATA, pid, addr, orig_word)

2. 把 RIP 退回一格
   ptrace(GETREGS, pid, ...), regs.rip -= 1, ptrace(SETREGS, pid, ...)

3. 單步執行原本的指令
   ptrace(SINGLESTEP, pid, ...)
   waitpid(pid, ...)

4. 重新 armed（把 0xcc 再寫回去）
   ptrace(POKEDATA, pid, addr, patched_word)

5. 繼續
   ptrace(CONT, pid, ...)
```

每次斷點命中都要這整套。慢嗎？慢。但對 debug 來說這點成本可忽略。

## 幾個細節

### 為什麼要「把 RIP 退回」？

`int3` 是 single-byte 指令，CPU 執行完它後 RIP 指向下一個 byte。但原本的指令其實還沒執行（那個 byte 被我們改成 `int3` 了）。我們把 byte 還原後，要從「那個位址」重新執行，所以 RIP 要退 1。

### 為什麼要先 single-step 再 re-arm？

因為我們剛還原 byte — 要先把原本的指令執行掉再 re-arm，否則一 CONT 馬上又撞到 0xcc（如果還 armed）會無限循環。

### `int3` vs `int 0x03`

x86 同時有兩個 opcode 都叫 "interrupt 3"：
- `0xCC`：1 byte，專門給 debugger
- `0xCD 0x03`：2 byte，通用 `int N`

GDB 用 `0xCC`，因為 1 byte 好 patch。

## Hardware breakpoint 的實作

### x86 debug registers

| Register | 作用 |
|---|---|
| DR0–DR3 | 放要監視的 linear address（4 個 slot） |
| DR4–DR5 | 保留 |
| DR6 | Status — 哪個 DR 觸發了 |
| DR7 | Control — 啟用 / 條件 |

DR7 每個 slot 控制：

- **L**（local）/ **G**（global）：僅這個 task 有效 / 全域
- **R/W bits**（2 bits）：`00` = execute、`01` = write、`10` = I/O、`11` = read/write
- **LEN bits**（2 bits）：1 / 2 / 4 / 8 byte（執行 breakpoint 必須是 1 byte）

你能 watch `int` 變數（4 byte）、`char` 變數（1 byte）、或更大的資料（但要在 alignment 正確的位置）。

### 設定 hardware breakpoint（user space）

直接用 `ptrace(PTRACE_POKEUSER, ...)` 寫 DR：

```c
#include <sys/user.h>
#include <sys/ptrace.h>

// 設 DR0 = addr（execute breakpoint）
ptrace(PTRACE_POKEUSER, pid,
       offsetof(struct user, u_debugreg[0]), addr);

// DR7：啟用 DR0 的 L bit（local）
unsigned long dr7 = 0x1;    // L0 = 1
ptrace(PTRACE_POKEUSER, pid,
       offsetof(struct user, u_debugreg[7]), dr7);
```

- L0 = 啟用 slot 0
- R/W 與 LEN 都是 0 → execute breakpoint、1 byte

觸發後，讀 DR6 看哪個 slot 命中：

```c
unsigned long dr6 = ptrace(PTRACE_PEEKUSER, pid,
                           offsetof(struct user, u_debugreg[6]), 0);
if (dr6 & 0x1) printf("DR0 hit\n");
if (dr6 & 0x2) printf("DR1 hit\n");
// ...
```

### Watchpoint 就是 data breakpoint

```c
// Watchpoint: DR0 = addr, R/W = 01 (write), LEN = 11 (4 bytes)
dr7 = 0x00000001 | (0x05 << 16);    // L0=1, RW0=01, LEN0=11 → 0x50001
```

複雜的是 LEN 與 alignment：要監視 4 byte 的 `int`，addr 必須 4-byte aligned。

## 多執行緒中的 breakpoint

**Software breakpoint**：因為改的是 text memory，**所有 thread 共享**。任一 thread 到那個位址都會停。這是預期行為。

**Hardware breakpoint**：**每個 thread 獨立 DR**！kernel 在 context switch 時會 save/restore DR0–DR7，所以 thread A 設的 DR 不影響 thread B。如果你要對所有 thread watch 同一個變數，需要逐一 set。

GDB 抽象掉這層（`watch` 命令自動對當前所有 thread set）。

## 多處理器（SMP）

x86 的 `int3` 是 CPU-local 的中斷。不論哪個 CPU 上的 thread 執行到 0xcc，都會觸發。跨 CPU 沒問題。

DR 也是 per-CPU 的 register，但 kernel 在 switch in thread 時 restore 它們，所以邏輯上是 "per-thread"。

## Breakpoint manager：自己實作一個

```c
// bp.h
typedef struct Breakpoint {
    pid_t pid;
    unsigned long addr;
    unsigned char orig_byte;
    int enabled;
} Breakpoint;

void bp_set(Breakpoint *bp);
void bp_unset(Breakpoint *bp);
void bp_after_hit(Breakpoint *bp);      // 命中後要做的：還原 + RIP-1 + singlestep + re-arm
```

### 實作

```c
#include <sys/ptrace.h>
#include <sys/user.h>
#include <sys/wait.h>
#include <errno.h>
#include "bp.h"

void bp_set(Breakpoint *bp) {
    errno = 0;
    long word = ptrace(PTRACE_PEEKDATA, bp->pid, (void *)bp->addr, NULL);
    if (word == -1 && errno != 0) {
        perror("peek");
        return;
    }
    bp->orig_byte = word & 0xFF;
    long patched = (word & ~0xFFL) | 0xCC;
    if (ptrace(PTRACE_POKEDATA, bp->pid, (void *)bp->addr, (void *)patched) < 0) {
        perror("poke");
        return;
    }
    bp->enabled = 1;
}

void bp_unset(Breakpoint *bp) {
    if (!bp->enabled) return;
    errno = 0;
    long word = ptrace(PTRACE_PEEKDATA, bp->pid, (void *)bp->addr, NULL);
    long restored = (word & ~0xFFL) | bp->orig_byte;
    ptrace(PTRACE_POKEDATA, bp->pid, (void *)bp->addr, (void *)restored);
    bp->enabled = 0;
}

void bp_after_hit(Breakpoint *bp) {
    // 1. 還原
    bp_unset(bp);

    // 2. RIP -= 1
    struct user_regs_struct regs;
    ptrace(PTRACE_GETREGS, bp->pid, NULL, &regs);
    regs.rip -= 1;
    ptrace(PTRACE_SETREGS, bp->pid, NULL, &regs);

    // 3. single-step
    ptrace(PTRACE_SINGLESTEP, bp->pid, NULL, NULL);
    int status;
    waitpid(bp->pid, &status, 0);

    // 4. re-arm
    bp_set(bp);
}
```

這就是 minimal breakpoint manager。Final Project 會把它包得更完整。

## 特殊情境

### 同一位址下兩次斷點

GDB 讓 user 下兩個 breakpoint 在同一位址（例如一個 conditional、一個非 conditional），但底層只改一次 byte — 用內部的 "breakpoint list" 記「這個位址有哪幾個 user-level bp」，一個 byte 命中後檢查所有 user bp 看要不要停。

### 斷點被 dlopen 後的 shared lib

Ch 20 會談。簡言之：GDB 會監視 dynamic linker 的事件、lib 載入後重新 resolve pending breakpoint。

### 斷點下在 text 被 strip 的地方

依然可以下，因為只要改 byte 就行。但 `bt`、source mapping 都失效。

## `catch` 的底層

Ch 6 的 `catch throw`（C++ exception）實作：GDB 在 `__cxa_throw` 函式下斷點。`catch syscall` 靠 `PTRACE_SYSCALL` 而非斷點。`catch signal` 靠 signal handling。

## 常見坑

1. **POKEDATA 報 EIO**：位址不可寫。發生在 `.rodata` 或 unmapped 區域。
2. **下在 `main` 進入點前**：binary 還沒 relocate / loader 還沒跑到 user code。GDB 處理這種情況用 "shared library hook"（Ch 20）。
3. **RIP 沒減一導致循環**：漏了上面的 step 2。
4. **Re-arm 沒做導致只命中一次**：漏了 step 4。
5. **`singlestep` 又停在另一個斷點**：罕見但可能（一條指令 step 就執行下一個 breakpoint 的 `int3`），要遞迴處理。
6. **hardware breakpoint 超過 4 個**：GDB 會 fallback 到 software。Watchpoint 變成 software watchpoint — 極慢。

## 動手練習

### 練習一：擴充 Ch 17 的 tracer

在 Ch 17 的 `tracer.c` 上加入上面的 `Breakpoint` struct 與函式，實作：

```
./tracer2 ./hello 0x11b8
```

在 `0x11b8`（main 入口）下斷點，程式會停在那，印當下 register，然後自動處理 + continue。

### 練習二：hardware breakpoint 版

改寫 bp_set / bp_unset 用 `PTRACE_POKEUSER` 操作 DR0 / DR7。試：

1. 正常下 bp 在某函式入口
2. 轉成 watchpoint（寫某個變數時觸發）

### 練習三：drift 測試

下個 bp 在一個被呼叫 1000 次的函式。用你的 manager 跑完整個程式。驗證：

- 每次命中都正確還原、RIP 退 1、re-arm
- 不會有 RIP 漂移（最後程式結果還是對的）

## 自我檢核

- [ ] 我能說出 software breakpoint 的 5 步驟 round-trip
- [ ] 我知道為什麼命中後要 RIP -= 1
- [ ] 我知道 software bp 跟 hardware bp 的優缺
- [ ] 我能用 `PTRACE_POKEUSER` 設 DR0 / DR7
- [ ] 我知道 watchpoint 是 hardware bp 的 data breakpoint 模式
- [ ] 我能自己實作 breakpoint manager
- [ ] 我知道 multi-thread 下 software bp vs hardware bp 的差異

下一章處理 debug 的另一個痛點：現代 binary 的位址隨機化（ASLR）、位置無關可執行（PIE），以及 shared library 載入後 symbol 怎麼 resolve。

→ [Ch 20 ASLR / PIE / 符號重定位](./20-aslr-pie-symbol-resolution.md)
