# Ch 3 — ptrace(2) 完整剖析

> 目標：搞懂 `ptrace` 這個 syscall 的所有 operation、tracer/tracee 關係、wait/signal 流程。下一章我們就用這些寫 mini-strace。

## 我們在哪裡

到目前為止：知道 syscall 怎麼進出 kernel（Ch 2）、知道 signal 是什麼、知道 SIGSTOP 凍 process。**ptrace 把這些拼起來變成「程式控制另一個程式」的機制**。

## ptrace 是什麼

一個 syscall：

```c
long ptrace(enum __ptrace_request op, pid_t pid, void *addr, void *data);
```

一個 syscall 做幾十種事 — `op` 決定。`addr` 跟 `data` 隨 `op` 解讀不同。

整個 strace、gdb、ltrace、各種 sandbox（含 chrome / firejail），底層都靠這支 syscall。

## tracer 跟 tracee 關係

```
   ┌──────────┐                ┌──────────┐
   │  tracer  │ ────ptrace───► │  tracee  │
   │ (gdb等)  │ ◄───signal──── │  (目標)  │
   └──────────┘                └──────────┘
```

- **tracer**：呼叫 ptrace 的 process（gdb、strace、自己寫的工具）
- **tracee**：被觀察的 process

關係建立有兩種方式：

1. **`PTRACE_TRACEME`**：tracee 自己宣告「我要被 tracer」，然後通常立刻 exec —— `strace cmd` 就是這個
2. **`PTRACE_ATTACH` / `PTRACE_SEIZE`**：tracer 主動 attach 已存在的 process —— `strace -p PID` 是這個

關係建立後，**tracer 就是 tracee 的 parent**（從 wait() 角度），原本的 parent 拿不到 SIGCHLD。

## ptrace 的 6 個常用 op

實際上 ptrace 有 30+ op，但常用的就這幾個：

| op | 作用 |
|---|---|
| `PTRACE_TRACEME` | tracee 說「我要被 trace」 |
| `PTRACE_ATTACH` | tracer 從外部 attach（送 SIGSTOP） |
| `PTRACE_DETACH` | 離開 |
| `PTRACE_CONT` | 讓 tracee 繼續跑 |
| `PTRACE_SYSCALL` | 跑到下一個 syscall enter / exit |
| `PTRACE_SINGLESTEP` | 跑一條指令 |
| `PTRACE_GETREGS` / `PTRACE_SETREGS` | 讀寫所有 register |
| `PTRACE_PEEKDATA` / `PTRACE_POKEDATA` | 讀寫 tracee 記憶體 |
| `PTRACE_PEEKTEXT` / `PTRACE_POKETEXT` | 讀寫 code 區（同 PEEK/POKEDATA） |
| `PTRACE_GETSIGINFO` | 拿目前的 signal info |

新版 Linux 推 `PTRACE_GETREGSET` / `PTRACE_PEEKSIGINFO` 等更通用的，但學習從上面這些開始。

## 完整工作流：trace 一個 child

```c
#include <sys/ptrace.h>
#include <sys/wait.h>
#include <unistd.h>
#include <stdio.h>
#include <sys/user.h>

int main(int argc, char *argv[]) {
    pid_t pid = fork();

    if (pid == 0) {
        // ─── child / tracee ───
        ptrace(PTRACE_TRACEME, 0, NULL, NULL);
        execvp(argv[1], &argv[1]);
        perror("execvp");
        return 1;
    }

    // ─── parent / tracer ───
    int status;
    waitpid(pid, &status, 0);    // 等 child 在 execve 進入 SIGTRAP

    while (1) {
        // 跑到下一個 syscall enter
        ptrace(PTRACE_SYSCALL, pid, 0, 0);
        waitpid(pid, &status, 0);
        if (WIFEXITED(status)) break;

        // 在 syscall enter 取 register
        struct user_regs_struct regs;
        ptrace(PTRACE_GETREGS, pid, 0, &regs);
        printf("syscall #%lld\n", regs.orig_rax);

        // 跑到 syscall exit
        ptrace(PTRACE_SYSCALL, pid, 0, 0);
        waitpid(pid, &status, 0);
        if (WIFEXITED(status)) break;

        // 在 syscall exit 取回傳值
        ptrace(PTRACE_GETREGS, pid, 0, &regs);
        printf("  → %lld\n", regs.rax);
    }

    return 0;
}
```

跑：

```bash
gcc tracer.c -o tracer
./tracer /bin/ls
# syscall #59
#   → 0
# syscall #12
#   → 22159360
# syscall #158
#   → 0
# ...
```

每個 `syscall #N` 是 syscall 進 kernel 前；`→ M` 是回來。number 對照 `/usr/include/asm/unistd_64.h` 或 `ausyscall N`。

## 關鍵概念：syscall enter / exit 兩次停

每個 syscall 對 ptrace 來說是**兩個 stop**：

```
   user code
      │
      │  syscall instruction
      ▼
   ─── stop 1 (syscall enter) ───  tracer 看 register、看參數
      │
      │  kernel handler
      ▼
   ─── stop 2 (syscall exit) ───   tracer 看回傳值
      │
      ▼
   user code 繼續
```

這就是為什麼上面 code 連續 call 兩次 `PTRACE_SYSCALL`。第一次到 enter、第二次到 exit。

`orig_rax` 是 syscall number（rax 在 exit 時被回傳值覆蓋了，原本的 number 在 `orig_rax`）。

## wait/signal 互動

ptrace 跟 wait 緊密綁。每次 tracee 停下，tracer 必須 `waitpid` 才知道。

`waitpid` 回傳的 status 用 macro 解：

```c
WIFEXITED(status)      // tracee 正常 exit
WEXITSTATUS(status)    // exit code
WIFSIGNALED(status)    // 被 signal 殺
WTERMSIG(status)       // 哪個 signal
WIFSTOPPED(status)     // 被 stop（被 SIGTRAP 或其他 signal）
WSTOPSIG(status)       // 哪個 signal 造成 stop
```

ptrace 常見的 stop 都用 SIGTRAP，但 syscall stop 是 `SIGTRAP | 0x80`（如果設了 `PTRACE_O_TRACESYSGOOD`），跟「真的 SIGTRAP」（debugger 斷點）區分。

## 讀 tracee 的記憶體

`PTRACE_PEEKDATA` 一次讀 word（8 byte on x86_64）：

```c
long word = ptrace(PTRACE_PEEKDATA, pid, addr, NULL);
```

`addr` 是 tracee 的 virtual address。要讀字串就 loop 一個 word 一個 word 讀，找到 `\0` 為止。

實務上用 `process_vm_readv`（Linux 3.2+）一次讀整段，比 PEEKDATA 快很多：

```c
struct iovec local = { .iov_base = buf, .iov_len = sizeof(buf) };
struct iovec remote = { .iov_base = (void*)addr, .iov_len = sizeof(buf) };
process_vm_readv(pid, &local, 1, &remote, 1, 0);
```

不需要 ptrace stop 也能讀（但要 `CAP_SYS_PTRACE` 或同 user）。strace 內部用這個。

## ptrace 的限制

- **同時只能一個 tracer** trace 一個 tracee。所以 `gdb -p PID` 會跟其他正在 trace 的工具搶
- **tracer 死了，tracee 也跟著死**（PTRACE_DETACH 沒 call）
- **不能 trace 自己的 ancestor**：A → B → C，C 不能 trace A
- **`yama/ptrace_scope`** 限制 attach（Ch 0 設過）
- **某些 syscall 不會被 ptrace 捕捉**：vDSO 的 `gettimeofday` 等，**因為它們根本沒進 kernel**

## ptrace overhead 為什麼這麼大

每個 syscall 兩次 stop + tracer 要 waitpid + tracer 要讀 register + tracer 要讀記憶體（為了印參數）。每個 syscall 從原本 ~100ns 變幾十 us。**100x slowdown 不誇張**。

```bash
strace -c /bin/true        # 看實際 syscall 數量跟時間
```

這是為什麼 production 不能 long-running strace —— 即使你只想看「程式有沒有開錯檔」，所有 syscall 都被攔，整個程式變慢。

## 一個常見誤解：「strace 用 LD_PRELOAD 攔 libc」

**錯**。LD_PRELOAD 只攔 lib call（ltrace 用的方法）。strace 攔 syscall，唯一辦法是 ptrace（或現代的 seccomp-bpf user notifications）。攔到 LD_PRELOAD 的話，靜態 link 的 binary 就攔不到了 — 但 strace 對 static binary 一樣有效。

## 一個常見誤解：「ptrace 看到的 syscall == strace 印的」

對，但 strace 多做了好多事：

- 把 syscall number 翻成名字
- 把參數從 register 拉出來、按 syscall signature 解析（`int` / `char*` / `struct stat*`...）
- pointer 參數還要 PEEK 進 tracee 記憶體把字串 / struct 拿出來印
- 把 errno 翻成名字（ENOENT 等）

ptrace 是 raw mechanism，strace 是 raw 之上的「人類可讀」layer。Ch 4 你會發現自己寫 mini-strace 90% 的 code 是在做這層轉換。

## 一個常見踩雷：忘了 `PTRACE_O_TRACESYSGOOD`

```c
ptrace(PTRACE_SETOPTIONS, pid, 0, PTRACE_O_TRACESYSGOOD);
```

設這個之後，syscall stop 用 `SIGTRAP | 0x80` 通知。沒設就用普通 SIGTRAP，跟「程式自己 raise(SIGTRAP)」分不開。實務上 strace 一定會設。

## 動手練習

**1. 跑上面那支 tracer**

把 code 存 `tracer.c`、`gcc tracer.c -o tracer`、跑 `./tracer /bin/echo hi`。

對照 `strace /bin/echo hi` 看少了什麼（你的版本只印 number 不印名字、不印參數）。

**2. 把 syscall number 翻成名字**

加一個 table 或 case，把 `0` 翻 `read`、`1` 翻 `write`、`2` 翻 `open` 等。看 `/usr/include/asm/unistd_64.h`。

**3. 試讀字串**

挑 `openat` (#257)：第二個參數（`rsi`）是 path 字串。寫 code 在 syscall enter 時用 `ptrace(PTRACE_PEEKDATA, ...)` 把字串拿出來印。

**4. attach 模式**

把 `PTRACE_TRACEME` + execvp 改成 `PTRACE_ATTACH` + 從 argv 拿 PID。學會 attach 已存在的 process。

```c
pid_t target = atoi(argv[1]);
ptrace(PTRACE_ATTACH, target, 0, 0);
waitpid(target, &status, 0);
// ...
ptrace(PTRACE_DETACH, target, 0, 0);
```

**5. 觀察 ptrace overhead**

```bash
time /bin/ls / > /dev/null
time strace /bin/ls / > /dev/null 2>/dev/null
```

慢 5-50 倍是正常的。

## 自我檢核

- [ ] 知道 `PTRACE_TRACEME` 跟 `PTRACE_ATTACH` 適用情境
- [ ] 知道每個 syscall 對 ptrace 來說是**兩次** stop
- [ ] 知道 `orig_rax` 跟 `rax` 在 syscall enter / exit 時的差別
- [ ] 看 wait status 的 macro 用法
- [ ] 知道 `process_vm_readv` 比 `PEEKDATA` 快
- [ ] 跑過自己的 tracer，至少印 syscall number

下一章把以上湊起來變一支真的能用的 mini-strace。

→ [Ch 4 動手：mini-strace v1](./04-mini-strace-v1.md)
