# Ch 19 — ptrace 進階：注入與 register 操作

> 目標：把 Ch 3-4 學的 ptrace 推到「能改變 tracee 行為」的程度 — 寫 register、改記憶體、注入 syscall、寫一個 mini debugger 的 breakpoint。

## 從觀察到操控

Ch 3-4 我們用 ptrace **觀察**：read register、read memory、印 syscall。這章用 ptrace **改寫**：

- 改 register（`PTRACE_SETREGS`）
- 改 memory（`PTRACE_POKEDATA` / `PTRACE_POKETEXT`）
- 注入 syscall（讓 tracee 幫忙做事）
- 設 breakpoint（INT 3 instruction）

這些是 gdb / debugger / 動態 patch / 安全工具的基本盤。

## PTRACE_SETREGS — 改 register

```c
struct user_regs_struct regs;
ptrace(PTRACE_GETREGS, pid, 0, &regs);
regs.rax = 42;                    // 把 rax 改成 42
ptrace(PTRACE_SETREGS, pid, 0, &regs);
```

例：把 syscall 結果改了

```c
// tracee 跑 getpid()，被攔在 syscall exit
ptrace(PTRACE_GETREGS, pid, 0, &regs);
regs.rax = 99999;     // 假裝 getpid 回 99999
ptrace(PTRACE_SETREGS, pid, 0, &regs);
ptrace(PTRACE_CONT, pid, 0, 0);
```

tracee 看到的「自己的 PID」變 99999。`man ptrace` 看完整 register list。

## PTRACE_POKEDATA — 寫 memory

```c
ptrace(PTRACE_POKEDATA, pid, addr, value);
```

一次寫一 word（8 byte on x86_64）。寫多 byte 要 loop。

例：把 tracee 字串 "hello" 改成 "world"

```c
const char *new = "world\0";
long word;
memcpy(&word, new, 8);    // 含 null
ptrace(PTRACE_POKEDATA, pid, str_addr, word);
```

## 注入 syscall：讓 tracee 幫你跑

進階技巧：**借 tracee 的身體跑 syscall**。例如 attach 後讓對方 open 一個檔。

步驟：

1. 找 tracee 程式碼裡有 `syscall` 指令的地方（或自己寫一個進去）
2. 設 register 成「想要的 syscall」(rax = SYS_open, rdi = 字串位址, ...)
3. 把字串先用 POKEDATA 寫進 tracee 某塊 writable memory
4. 把 IP 設到那條 syscall 指令
5. PTRACE_SINGLESTEP 跑一條 → syscall 完成、rax 是回傳值
6. 把 register / memory 復原

實作有點長，但這是 `gdb call function()`、`live patching`、許多 EDR 工具的核心機制。

## 簡化版：注入 syscall demo

```c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/ptrace.h>
#include <sys/wait.h>
#include <sys/user.h>
#include <sys/syscall.h>

int main(int argc, char *argv[]) {
    pid_t pid = atoi(argv[1]);

    // 1. attach
    if (ptrace(PTRACE_ATTACH, pid, 0, 0) < 0) {
        perror("attach"); return 1;
    }
    int status;
    waitpid(pid, &status, 0);

    // 2. 拿目前 register（之後要恢復）
    struct user_regs_struct saved, regs;
    ptrace(PTRACE_GETREGS, pid, 0, &saved);
    regs = saved;

    // 3. 找一個 RX 區域（用現成的 vDSO 或 libc 的 syscall 指令）
    // 簡化：假設我們知道某個 vDSO 位置有 syscall 指令
    // 實務要 parse /proc/PID/maps 找 syscall + ret 序列

    // 4. 設 rax = SYS_getpid（最簡單的 syscall）
    regs.rax = SYS_getpid;
    regs.rip = saved.rip;    // 假設 saved.rip 就在 syscall 指令上
    ptrace(PTRACE_SETREGS, pid, 0, &regs);

    // 5. 跑一條
    ptrace(PTRACE_SINGLESTEP, pid, 0, 0);
    waitpid(pid, &status, 0);

    // 6. 看 rax = tracee 的 getpid 回傳
    ptrace(PTRACE_GETREGS, pid, 0, &regs);
    printf("tracee getpid() returned %lld\n", regs.rax);

    // 7. 復原 register
    ptrace(PTRACE_SETREGS, pid, 0, &saved);
    ptrace(PTRACE_DETACH, pid, 0, 0);
    return 0;
}
```

實際 robust 版本要：

- parse `/proc/PID/maps` 找有 `syscall; ret` 序列的 page
- 暫時把 instruction 改成 `syscall; int 3`，用 POKETEXT
- single step 後恢復 instruction
- 處理 signal、其他 thread

這就是 `criu`（Checkpoint/Restore in Userspace）、各種 process injection tool 的基本機制。

## 設 breakpoint：INT 3 trick

x86 上 `INT 3` 是 1 byte 指令 (`0xCC`)，用來觸發 SIGTRAP。debugger 設 breakpoint 的方法：

1. PEEKTEXT 拿 target address 的原指令
2. POKETEXT 把第一 byte 改成 `0xCC`
3. tracee 跑到那 → SIGTRAP → tracer 接到
4. tracer 把原指令 PEEK 寫回、把 IP 退一格、SINGLESTEP 跑那條原指令、再 POKE 回 `0xCC`

```c
// 設 breakpoint
long orig = ptrace(PTRACE_PEEKTEXT, pid, addr, 0);
long bp = (orig & ~0xFFL) | 0xCCL;
ptrace(PTRACE_POKETEXT, pid, addr, bp);

// 等 tracee hit
ptrace(PTRACE_CONT, pid, 0, 0);
waitpid(pid, &status, 0);    // SIGTRAP

// 復原指令、退 IP、step、改回
ptrace(PTRACE_POKETEXT, pid, addr, orig);
struct user_regs_struct r;
ptrace(PTRACE_GETREGS, pid, 0, &r);
r.rip -= 1;     // INT 3 是 1 byte
ptrace(PTRACE_SETREGS, pid, 0, &r);
ptrace(PTRACE_SINGLESTEP, pid, 0, 0);
waitpid(pid, &status, 0);
ptrace(PTRACE_POKETEXT, pid, addr, bp);    // 重設 breakpoint
ptrace(PTRACE_CONT, pid, 0, 0);
```

這就是 gdb `b function` 的核心機制。

## 實用：mini debugger

把上面組合起來能寫一個 debugger，支援：

- attach to PID
- 設 breakpoint at address
- continue / step
- print register
- print memory

幾百行 C code。完整實作見 [Eli Bendersky 的 ptrace 系列](https://eli.thegreenplace.net/2011/01/23/how-debuggers-work-part-1) 或 [Sy Brand 的 dbg blog](https://blog.tartanllama.xyz/writing-a-linux-debugger-setup/)。

## 安全考量

ptrace 能改 register / memory / 注入 syscall —— 等於 **完全控制 tracee**。如果攻擊者能 ptrace 你的 process：

- 偷 secret（讀 memory）
- 改行為（patch code）
- 假冒你發 syscall（exec 別的東西）

這就是 `yama/ptrace_scope` 的意義。預設限制 attach 範圍。

container 預設禁 ptrace。Kubernetes pod 沒 `SYS_PTRACE` 不能 attach。

## 一個常見踩雷：POKETEXT 寫 read-only page

```c
ptrace(PTRACE_POKETEXT, pid, code_addr, 0xCC);
// 失敗（如果 code 是 r-x，沒有 w）
```

正常程式 `.text` 是 r-x。但 ptrace 有特權，**可以**寫 r-x page（kernel 特殊處理）。如果失敗多半是 process 對 mprotect 後改成更嚴格。

## 一個常見踩雷：multi-thread tracee

ptrace 一個 thread 不會自動 stop 其他 thread。要 trace 全部 thread 用 `PTRACE_O_TRACECLONE` + 對每個 thread 各 ptrace。

gdb 內部用 `PTRACE_SEIZE` + 整個 thread group。實作複雜。

## 一個常見踩雷：注入後 tracee 看到「不可能的 register」

如果 tracee 用 signal handler 檢查狀態（如 `sigaltstack`），注入後 register 跟 signal context 不一致，可能 crash。

實務工具（criu、frida）要花大量精力處理 corner case。

## 動手練習

**1. attach + 改 getpid**

寫個簡單 tracee：

```c
#include <stdio.h>
#include <unistd.h>
int main() {
    while (1) {
        printf("my pid is %d\n", getpid());
        sleep(1);
    }
}
```

寫一個 ptrace 工具，attach 上去、在 getpid 的 syscall exit 改 rax = 99999。tracee 印的 PID 變 99999。

**2. 設 breakpoint**

寫 tracee：

```c
void target(void) { puts("hit!"); }
int main() {
    while (1) { target(); sleep(1); }
}
```

用 nm / readelf 找 `target` 位址。寫 ptrace 工具設 breakpoint，每次 hit 印「caught」，然後 continue。

**3. 改 tracee 字串**

tracee 印一個全域字串。attach + POKEDATA 把字串改掉。看下次 print 變了。

**4. 寫 mini-strace 加 inject**

延伸 Ch 4 mini-strace，加：「在 openat 時把 path 改成別的」。能用 ptrace 改檔案路徑做 redirection。

## 自我檢核

- [ ] 用 SETREGS 改過 register
- [ ] 用 POKEDATA / POKETEXT 寫過 tracee memory
- [ ] 知道 INT 3 (0xCC) 怎麼當 breakpoint
- [ ] 知道注入 syscall 大致流程
- [ ] 知道 ptrace 安全意義（攻擊面）

下一章看 LD_PRELOAD —— 不用 ptrace 也能改行為的方法。

→ [Ch 20 動手:LD_PRELOAD interceptor](./20-ld-preload-interceptor.md)
