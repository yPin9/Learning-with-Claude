# Ch 17 — ptrace 系統呼叫

> 目標：徹底搞懂 `ptrace` 這個 Linux kernel 系統呼叫，看懂 GDB 底層如何靠它讀寫 inferior 的暫存器、memory、控制執行。我們會寫一個 50 行 C 的玩具 debugger。

## ptrace 的定位

Ch 1 講過 debugger 的三個魔法：ptrace、signal、debug info。這章是第一個魔法的深度版。

`ptrace` 是 Linux 提供給 userspace「讓一個 process 控制另一個 process」的唯一官方管道。它的 signature：

```c
#include <sys/ptrace.h>

long ptrace(enum __ptrace_request op, pid_t pid, void *addr, void *data);
```

四個參數：

| 參數 | 意義 |
|---|---|
| `op` | 要做什麼（`PTRACE_ATTACH` / `PTRACE_CONT` / `PTRACE_PEEKDATA` 等） |
| `pid` | 目標 process 的 pid |
| `addr` | 記憶體位址（根據 op 有時用、有時不用） |
| `data` | 資料 / 旗標（根據 op 有時用、有時不用） |

回傳值：大多是 0/error，`PEEK*` 類回傳讀到的 word。

`man 2 ptrace` 列了所有 op。我們看最重要的幾個。

## 關鍵的 ptrace op

### 建立 tracing 關係

**`PTRACE_TRACEME`**（子 process 呼叫）：「親愛的父 process，請開始 trace 我」。用在 `fork` 後、`execve` 前。

**`PTRACE_ATTACH`**（tracer 呼叫）：「我要 trace pid 這個 process」。會發 SIGSTOP 給目標。

**`PTRACE_SEIZE`**：類似 ATTACH 但不發 SIGSTOP（更優雅）。現代 gdb 偏好用這個。

**`PTRACE_DETACH`**：解除 tracing。

### 控制執行

**`PTRACE_CONT`**：讓 tracee 繼續跑（直到下次 signal / exit）。`data` 可以指定要傳給 tracee 的 signal number（或 0 代表不傳）。

**`PTRACE_SINGLESTEP`**：單步一條機器指令然後 SIGTRAP 停下。

**`PTRACE_SYSCALL`**：繼續跑，但在「進入 syscall」與「離開 syscall」時各停一次。strace 就用這個。

**`PTRACE_SYSEMU`**：「進入 syscall」時停，讓 tracer 模擬 syscall（kernel 不執行）。

### 讀寫 tracee

**`PTRACE_PEEKDATA` / `PTRACE_PEEKTEXT`**：從 tracee 讀一個 word（x86_64 上是 8 byte）。`addr` 是要讀的位址。回傳值是讀到的內容。

**`PTRACE_POKEDATA` / `PTRACE_POKETEXT`**：寫一個 word 到 tracee。`data` 是要寫的內容。

**`PTRACE_GETREGS` / `PTRACE_SETREGS`**：讀寫一般暫存器（`struct user_regs_struct`）。`addr` 未用、`data` 指向 struct。

**`PTRACE_GETFPREGS` / `PTRACE_SETFPREGS`**：浮點暫存器。

**`PTRACE_GETREGSET` / `PTRACE_SETREGSET`**：更通用，用 iovec 傳 register 集合（支援 SSE / AVX / 其他架構）。

### 雜項

**`PTRACE_KILL`**：殺 tracee（現代 kernel 建議直接 `kill(pid, SIGKILL)`）。

**`PTRACE_GETSIGINFO`**：獲取 signal 資訊。

**`PTRACE_SETOPTIONS`**：設定 tracing flags。例如 `PTRACE_O_TRACESYSGOOD`（讓 syscall trap 跟普通 trap 分得出來）、`PTRACE_O_TRACEFORK`（auto-trace fork 出來的 child）。

## Tracer 與 tracee 的完整生命週期

```
tracer (e.g. gdb)                    tracee (target program)
-----------------                    -----------------------
fork()
                                     <子 process 誕生>
                                     ptrace(TRACEME, 0, 0, 0)
                                     execve("./target", ...)
                                     <執行到第一條指令，kernel 發 SIGTRAP>

waitpid(pid, ...)  ◄─────── SIGTRAP (tracee 已停)

ptrace(GETREGS, pid, 0, &regs)
ptrace(PEEKDATA, pid, addr, 0)
ptrace(POKEDATA, pid, addr, 0xcc...) ← 下斷點（改 memory）
ptrace(CONT, pid, 0, 0) ─────────►   tracee 繼續跑
                                     <跑到 0xcc，觸發 SIGTRAP>
waitpid(...)       ◄─────── SIGTRAP

... 一直來回 ...

ptrace(DETACH, pid, 0, 0) ─────────► tracee 脫離 tracing，自由了
```

這個「`ptrace(CONT)` → `waitpid()`」的迴圈就是整個 debugger 的核心迴圈。

## 寫一個 minimal tracer

```c
// tracer.c — 一個會 fork、exec 目標、trace 它、印暫存器的最小 tracer
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/ptrace.h>
#include <sys/user.h>
#include <sys/wait.h>

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s <target>\n", argv[0]);
        return 1;
    }

    pid_t pid = fork();
    if (pid == 0) {
        // child
        ptrace(PTRACE_TRACEME, 0, NULL, NULL);
        execv(argv[1], &argv[1]);
        perror("execv");
        return 1;
    }

    // parent (tracer)
    int status;
    waitpid(pid, &status, 0);          // 等 child 停在 execve 後的第一個 SIGTRAP

    printf("child stopped. initial SIGTRAP received.\n");

    struct user_regs_struct regs;
    ptrace(PTRACE_GETREGS, pid, NULL, &regs);
    printf("RIP = 0x%llx\n", regs.rip);

    // single-step 5 次
    for (int i = 0; i < 5; i++) {
        ptrace(PTRACE_SINGLESTEP, pid, NULL, NULL);
        waitpid(pid, &status, 0);
        ptrace(PTRACE_GETREGS, pid, NULL, &regs);
        printf("step %d: RIP = 0x%llx\n", i, regs.rip);
    }

    // detach 讓它跑完
    ptrace(PTRACE_DETACH, pid, NULL, NULL);
    waitpid(pid, &status, 0);
    printf("child exited with status %d\n", WEXITSTATUS(status));
    return 0;
}
```

編譯與測試：

```bash
gcc tracer.c -o tracer

# 目標就用 Ch 0 的 hello
./tracer ./hello
```

輸出類似：

```
child stopped. initial SIGTRAP received.
RIP = 0x7ffff7fcb290
step 0: RIP = 0x7ffff7fcb293
step 1: RIP = 0x7ffff7fcb295
step 2: RIP = 0x7ffff7fcb298
...
child exited with status 0
```

**你剛剛寫了一個 minimal debugger frontend**。能：attach、單步、讀暫存器、detach。

## 讀寫 tracee memory：PEEKDATA / POKEDATA

```c
long word;
word = ptrace(PTRACE_PEEKDATA, pid, (void *)addr, NULL);
if (word == -1 && errno != 0) {
    perror("peek");
}
printf("at 0x%lx: 0x%lx\n", addr, word);

// 寫
ptrace(PTRACE_POKEDATA, pid, (void *)addr, (void *)new_value);
```

一次讀寫一個 word（8 byte on x86_64）。要讀一整塊 memory 就 loop：

```c
void read_mem(pid_t pid, unsigned long addr, void *buf, size_t n) {
    unsigned long *dst = (unsigned long *)buf;
    for (size_t i = 0; i < n / sizeof(long); i++) {
        dst[i] = ptrace(PTRACE_PEEKDATA, pid, (void *)(addr + i * sizeof(long)), NULL);
    }
}
```

更新的 kernel 有 `PTRACE_PEEKDATA_EXT` 或 `process_vm_readv` 可以一次讀大塊（效能好很多）。

## 下一個斷點：POKEDATA 寫 `int3`

這是 GDB 最底層的 breakpoint 實作：

```c
// 下斷點
long original_byte = ptrace(PTRACE_PEEKDATA, pid, (void *)addr, NULL);
long patched = (original_byte & ~0xff) | 0xcc;    // 把最低 byte 改成 0xcc
ptrace(PTRACE_POKEDATA, pid, (void *)addr, (void *)patched);

// continue 讓它撞斷點
ptrace(PTRACE_CONT, pid, NULL, NULL);
waitpid(pid, &status, 0);                          // 等 SIGTRAP

// 收到斷點了！要繼續的話要：
// 1. 還原 byte
ptrace(PTRACE_POKEDATA, pid, (void *)addr, (void *)original_byte);
// 2. 把 RIP 倒退一格
struct user_regs_struct regs;
ptrace(PTRACE_GETREGS, pid, NULL, &regs);
regs.rip -= 1;
ptrace(PTRACE_SETREGS, pid, NULL, &regs);
// 3. single-step 執行原本的指令
ptrace(PTRACE_SINGLESTEP, pid, NULL, NULL);
waitpid(pid, &status, 0);
// 4. 再寫回 0xcc 重新 armed
ptrace(PTRACE_POKEDATA, pid, (void *)addr, (void *)patched);
// 5. continue
ptrace(PTRACE_CONT, pid, NULL, NULL);
```

這套流程 Ch 19 會完整包成 class。

## ptrace 與 signal 的交織

Tracee 收到任何 signal 時，kernel 都會先讓 tracer 看一眼。流程：

1. signal 到達 tracee
2. kernel 把 tracee 標為 stopped，發 `SIGCHLD` 給 tracer
3. tracer 呼叫 `waitpid` 看到 tracee 停了，狀態是「stopped with signal N」
4. tracer 決定怎麼辦：
   - **吃掉 signal**：`ptrace(CONT, pid, 0, 0)` — data 為 0，signal 不傳給 tracee
   - **傳給 tracee**：`ptrace(CONT, pid, 0, signo)` — tracee 醒來後會收到 signo
   - **換成別的 signal**：`ptrace(CONT, pid, 0, new_signo)`

這是 Ch 9 講過的 `handle` 指令背後的機制。

## waitpid 的狀態解讀

```c
int status;
waitpid(pid, &status, 0);

if (WIFSTOPPED(status)) {
    int sig = WSTOPSIG(status);
    printf("stopped by signal %d (%s)\n", sig, strsignal(sig));
}
if (WIFEXITED(status)) {
    printf("exited with code %d\n", WEXITSTATUS(status));
}
if (WIFSIGNALED(status)) {
    printf("killed by signal %d\n", WTERMSIG(status));
}
```

`WIFSTOPPED` + `WSTOPSIG == SIGTRAP` 是「撞到斷點 / 單步完成」的常規路徑。

有些情境要分辨「一般 SIGTRAP」跟「syscall-stop」、「ptrace event（例如 fork 事件）」，這就需要 `PTRACE_O_TRACESYSGOOD` 之類的 option 讓 SIGTRAP 的高位元帶 extra info。

## 權限與 Yama

近代 Linux 有 **Yama LSM**（Linux Security Module）限制「哪些 process 能 trace 哪些 process」。預設（`/proc/sys/kernel/yama/ptrace_scope = 1`）規則：

- 只能 trace 自己的 descendant（fork 出來的）
- 或者需要 CAP_SYS_PTRACE

所以 `gdb -p <ANY_PID>` 不一定能直接 attach。解法：

- 改設定：`sudo sysctl -w kernel.yama.ptrace_scope=0`（放寬到「同 user 就行」）
- 或 container 內給 `CAP_SYS_PTRACE`
- 或設 `/etc/security/limits.conf`

## ptrace 的限制

1. **只能 attach 一個 tracer**：一個 process 同時只能被一個 tracer trace。所以你不能同時用 gdb + strace。
2. **SIGSTOP 不能被 trace**：tracee 收到 SIGSTOP，tracer 看到也沒辦法 suppress（kernel 特殊處理）。
3. **ptrace 跟 fork 的互動複雜**：沒設 `PTRACE_O_TRACEFORK` 的話，tracee fork 出來的 child 不會被 trace。
4. **signal 在 delivery 與 stop 之間 race**：罕見但存在。現代 `PTRACE_SEIZE` + `PTRACE_INTERRUPT` 組合更穩。
5. **多 thread 複雜得多**：Linux 下每個 thread 是獨立 LWP，要各自 `ptrace(ATTACH, tid)`。gdb 幫你包起來。

## strace 是什麼？一個 ptrace 的範例

`strace` 就是一個 ptrace 應用：

1. `fork` + `PTRACE_TRACEME` + `execve` 目標
2. loop：`ptrace(SYSCALL, pid)` → `waitpid` → `ptrace(GETREGS)` 拿 syscall number 跟參數 → 印出來 → 再 `ptrace(SYSCALL, pid)` 繼續到 syscall return → 印 return value → loop

Source code 一兩百行（核心邏輯，不含格式化）。讀一下 strace source 是理解 ptrace 的好方法。

## 其他類似工具的原理

- **ltrace**：類似 strace 但 trace library call，靠讀 PLT 下 breakpoint
- **perf**：用 `perf_event_open` 而非 ptrace，因為只讀不改
- **bpftrace / eBPF**：更新潮的方法，kernel 直接提供觀察點

gdb 用 ptrace 因為它需要**控制**，不只觀察。

## 動手練習

### 練習一：完成 tracer.c

在上面的骨架上加：

1. 用 `PTRACE_PEEKDATA` 印出 tracee 當前 PC 附近的 memory。
2. 用 `PTRACE_POKEDATA` 在 main 入口前（你要自己算 addr）下斷點。
3. continue 後收到 SIGTRAP，還原 byte，單步一次，重 armed，繼續。

### 練習二：簡易 strace

寫 `mystrace.c`：

1. `fork` + TRACEME + execve。
2. loop `PTRACE_SYSCALL` 等 syscall。
3. 用 `GETREGS` 拿 `rax`（syscall number）、`rdi/rsi/rdx/...`（參數）。
4. 印出類似 `strace` 的格式。

### 練習三：POKEDATA patch 掉一個函式

給個 hello.c：

```c
int secret(void) { return 42; }
int main(void) { printf("%d\n", secret()); return 0; }
```

你的 tracer 在啟動後，找到 `secret` 的位址（假設你知道），用 POKEDATA 把它的機器碼改成 `mov $100, %eax; ret`（`b8 64 00 00 00 c3`）。然後 CONT，看輸出變 `100`。

這是**binary patching** — reverse engineering 常用的技巧。

## 常見坑

1. **`ptrace: Operation not permitted`**：Yama 或權限問題。
2. **`ptrace(TRACEME)` 後 exec 沒停**：忘了先 `ptrace(TRACEME, 0, 0, 0)` 再 `execve`。
3. **PEEKDATA 讀到 `-1`**：可能是真的讀到 0xFFFF...，也可能是錯誤。要檢查 `errno`：先 `errno = 0`，讀完看 `errno != 0`。
4. **多 thread 時 attach 了 PID 但其他 thread 沒停**：要個別 attach 每個 thread。
5. **斷點 POKEDATA 後沒停**：你算的位址不對、或剛好在 compiler optimize 掉的 dead code 裡。`objdump -d` 確認位址。
6. **fork 後 child 的 tracer 是誰**：要 `PTRACE_O_TRACEFORK` + `PTRACE_O_TRACEVFORK` 才會自動 trace child。

## 自我檢核

- [ ] 我能說出 ptrace 的 5–8 個常用 op
- [ ] 我能寫一個 minimal tracer（fork + TRACEME + 單步）
- [ ] 我知道 breakpoint 的完整 round-trip：peek → poke 0xcc → cont → wait → restore → RIP-1 → single-step → re-arm → cont
- [ ] 我能用 ptrace 讀寫 tracee memory 與 register
- [ ] 我知道 waitpid 狀態 `WIFSTOPPED` / `WSTOPSIG` 怎麼解讀
- [ ] 我知道 Yama 可能擋住 attach，怎麼放寬

下一章講「地圖」— DWARF debug info 的格式與解析。你會理解為什麼 `print x` 的背後是一個小型編譯程式。

→ [Ch 18 DWARF debug info](./18-dwarf-debug-info.md)
