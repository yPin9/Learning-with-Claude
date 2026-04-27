# Ch 2 — process / syscall / signal / fd 模型

> 目標：把 Linux 上「process 是什麼、syscall 怎麼進出 kernel、signal 怎麼遞送、fd 怎麼運作」打底完。後面所有工具都建立在這四件事上。

## 為什麼這章重要

strace 看 syscall、lsof 看 fd、gdb 透過 ptrace + signal 控制 process —— 不懂這四個基本模型，看到工具輸出會像看天書。本章不深入到 kernel implementation，但要把「使用者程式怎麼跟 kernel 互動」的整個故事講清楚。

## Process：一個跑著的程式

Linux 對 process 的核心抽象很簡單：

- 一個 PID（整數）
- 一個 address space（virtual memory map）
- 一組 file descriptor table
- 一個 credential（uid / gid）
- 一個 signal handler table
- 一組 register（每 thread 一份）

每個 process 在 kernel 裡是一個 `task_struct`。你能透過 `/proc/PID/` 讀到大部分欄位。

```bash
ls /proc/$$/   # $$ 是 shell 自己的 PID
```

幾個重點檔案後面 Ch 7 詳細看：

| 檔案 | 內容 |
|---|---|
| `cmdline` | argv |
| `status` | 各種統計（state, uid, mem, ...） |
| `fd/` | 開的 file descriptor |
| `maps` | virtual memory layout |
| `stack` | kernel stack（process 在 kernel 哪段 code） |
| `syscall` | 目前停在哪個 syscall |

## fork / exec：process 怎麼生出來

**`fork()`**：現有 process 複製一份，新的叫 child，PID 不一樣，其他幾乎都一樣（記憶體 COW、fd table 拷貝）。

**`exec*()`**：把當前 process 的 address space 換成另一個 binary，**PID 不變**。

組合：`fork()` + `exec()` = 「啟動新程式」的 idiom。shell 跑 `ls`：

```c
pid_t pid = fork();
if (pid == 0) {
    // child
    execvp("ls", (char *[]){"ls", NULL});
} else {
    // parent
    waitpid(pid, NULL, 0);
}
```

為什麼分兩步？分開讓 fork 後 exec 前可以做事 — 比如 redirect stdout、setuid、close fd。Ch 8 / 19 / 20 都會用到這個 pattern。

## Syscall：跨過 user/kernel 邊界

C 程式跑在 user mode，沒辦法直接做 IO、不能直接讀別 process 的記憶體、不能直接 access 硬體。要 kernel 幫忙就用 **syscall**。

x86_64 上 syscall 的 ABI（簡化版）：

```
register   傳什麼
────────   ───────
rax        syscall number
rdi        arg1
rsi        arg2
rdx        arg3
r10        arg4
r8         arg5
r9         arg6
```

實際指令：

```asm
mov rax, 1        ; SYS_write = 1
mov rdi, 1        ; fd = stdout
mov rsi, msg      ; buffer
mov rdx, 13       ; length
syscall           ; 進 kernel
; 回來時 rax = 回傳值（負數表示 errno）
```

`syscall` 指令做的事：

1. 切到 kernel mode（CPU privilege ring 3 → 0）
2. 跳到 kernel 設好的 entry point (`entry_SYSCALL_64`)
3. 從 `rax` 查 syscall table、call 對應 handler
4. handler 跑完，`rax` 設回傳值
5. 切回 user mode、回到 caller 下一條指令

**strace 攔的就是這條線**。它在 syscall 進去前後各停一次、印參數跟回傳值。Ch 3 講 ptrace 機制。

## syscall vs libc function

新手常混。**libc function 不等於 syscall**。例：

| C code | libc 做了什麼 | 真的 syscall |
|---|---|---|
| `printf("hi\n")` | format、寫到 stdio buffer，buffer 滿才 flush | `write` (有時) |
| `fopen("f", "r")` | malloc FILE struct、call open | `openat` |
| `malloc(8)` | 自己管 heap，不夠才向 kernel 要 | `brk` 或 `mmap`（少） |
| `time(NULL)` | 多數 case 走 vDSO | 0 個 syscall（!） |
| `getpid()` | glibc cache 過 | 0 個 syscall |

**vDSO**（virtual dynamic shared object）很重要：kernel 把幾個常用 function（gettimeofday、clock_gettime、getcpu、time）直接 map 到 user space，呼叫不用切 ring，超快。

```bash
ldd /bin/ls | grep vdso
# linux-vdso.so.1 (0x00007ffd...)
```

**這就是為什麼有些 function 用 strace 看不到** — 它根本沒進 kernel。要看用 ltrace（看 lib call）或 gdb step。

## errno 怎麼運作

syscall 失敗時 kernel 把錯誤放在 register（負的 error code）。glibc wrapper 把它取負號塞進 `errno`，wrapper 回傳 -1。

```c
int fd = open("/nope", O_RDONLY);
if (fd < 0) {
    perror("open");        // 印 "open: No such file or directory"
    fprintf(stderr, "errno = %d\n", errno);
}
```

strace 直接顯示 errno 名稱：

```
openat(AT_FDCWD, "/nope", O_RDONLY) = -1 ENOENT (No such file or directory)
```

**`-1 ENOENT` 是線索**。看 strace 找錯誤就是找這種行 —— 程式會 retry 或 fallback 的時候你只看 source 看不出來，strace 看一目了然。

## File Descriptor：Linux 的 IO 抽象

fd 是個整數，process 用它指 kernel 內部的「open file」對象。kernel 維護一張表：

```
process X 的 fd table:

  0 → stdin   (terminal / pipe)
  1 → stdout  (terminal / pipe)
  2 → stderr  (terminal / pipe)
  3 → /etc/passwd        (regular file)
  4 → socket:[12345]     (TCP socket)
  5 → /dev/random        (char device)
  6 → pipe:[67890]       (pipe)
  ...
```

**任何「open」的東西都是 fd**：file、socket、pipe、event fd、timer fd、signal fd、inotify、epoll handle、memfd、even /proc/PID/mem。

這個統一 abstraction 是 Unix 哲學的核心。`read(fd, ...)` 不管你那個 fd 是檔案還是 socket，**都用同一個 syscall**。

```bash
ls -l /proc/$$/fd/    # 看你 shell 開了什麼
```

每個 process 預設 fd table 大小（`ulimit -n`）通常 1024 或更高。

## fd 的繼承：fork 跟 exec 的差別

- **fork()**：child 拷貝整張 fd table，**parent 跟 child 看同一個 open file**（同 offset、同 flag）
- **exec()**：fd 預設**保留**（除非 fd 設了 `O_CLOEXEC`）

`O_CLOEXEC` 是個重要 flag：「我不想讓我 fork 出去 exec 的 child 看到這個 fd」。沒設可能 leak，新 code 應該預設加上。

```c
int fd = open("secret", O_RDONLY | O_CLOEXEC);  // ✅ exec 後消失
```

Ch 8 / 練習 B 會反覆碰這個。

## Signal：非同步打斷

signal 是 kernel 給 user process 發的「事件通知」。30+ 種，常見：

| Signal | 預設行為 | 何時發 |
|---|---|---|
| `SIGINT` (2) | 終止 | Ctrl-C |
| `SIGQUIT` (3) | 終止 + core | Ctrl-\ |
| `SIGKILL` (9) | 終止（**不可 catch**） | `kill -9` |
| `SIGSEGV` (11) | 終止 + core | invalid memory access |
| `SIGPIPE` (13) | 終止 | write 到沒人讀的 pipe |
| `SIGTERM` (15) | 終止（可 catch） | `kill` 預設 |
| `SIGCHLD` | 忽略 | child 死了 |
| `SIGSTOP` | stop（**不可 catch**） | `kill -STOP` |
| `SIGCONT` | resume | `kill -CONT` |
| `SIGTRAP` (5) | 終止 + core | breakpoint / debugger |
| `SIGUSR1` / `SIGUSR2` | 終止 | 給 app 自定義 |

process 可以註冊 handler：

```c
#include <signal.h>

void handler(int sig) {
    printf("caught %d\n", sig);
}

int main(void) {
    signal(SIGINT, handler);
    while (1) pause();
}
```

`SIGKILL` 跟 `SIGSTOP` **不能 catch、不能 ignore、不能 block**。kernel 強制執行。

## ptrace 跟 signal 的關係

這是後面 Ch 3 的核心鋪墊：**ptrace 用 SIGSTOP 跟 SIGTRAP 控制 tracee**。

- tracer call `PTRACE_ATTACH` → kernel 對 tracee 發 SIGSTOP，tracee 凍住
- tracer call `PTRACE_SYSCALL` → tracee 跑到下一個 syscall 進 / 出，kernel 發 SIGTRAP 通知 tracer
- tracer call `PTRACE_CONT` → tracee 繼續跑

整個 strace、gdb 都建在這個 signal-based 控制機制上。**signal 不只是「中斷程式」，更是 tracer 跟 tracee 之間的通訊管道**。

## 一個常見誤解：「fork 跟 thread 一樣」

不一樣。fork 整個拷貝記憶體（COW 但邏輯獨立），thread 共享記憶體。Linux 上兩者底層都是 `clone()` syscall，差別在傳的 flag：

- `fork()` = `clone(SIGCHLD, ...)` 不共享
- `pthread_create` = `clone(CLONE_VM | CLONE_FS | CLONE_FILES | CLONE_SIGHAND | ...)` 共享一堆

strace 兩者都看得到（用 `-f`），但 thread 的 PID 在 ps 裡看叫 LWP（Light Weight Process）或 TID。Ch 5 詳細展開。

## 一個常見誤解：「syscall 阻塞 = process hang」

阻塞中的 process 不吃 CPU，這跟 hang 不一樣。`top` 看：

- `R` (running) — 真的在 CPU 跑
- `S` (sleeping) — 被 kernel 阻塞中（等 IO、等 lock、等 signal）
- `D` (uninterruptible) — 阻塞中且**signal 也叫不醒**（通常是 disk IO）
- `T` (stopped) — 被 SIGSTOP 凍住
- `Z` (zombie) — 死了但 parent 還沒 wait

`S` 的 process 不是壞，正在等東西。`D` 太久才是問題（多半 disk 卡）。Ch 7 看 `/proc/PID/status` 的 State 欄位。

## 動手練習

**1. 看一個 process 的 fd**

```bash
sleep 100 &
PID=$!
ls -l /proc/$PID/fd/
# 0, 1, 2 都指向你的 terminal (/dev/pts/N)
kill $PID
```

**2. 看 syscall 跟 lib call 的差別**

```c
// hello2.c
#include <stdio.h>
int main(void) {
    printf("hello %d\n", 42);
    fflush(stdout);
    return 0;
}
```

```bash
gcc hello2.c -o hello2

# 看 lib call
ltrace ./hello2
# printf("hello %d\n", 42)        = 9
# fflush(0x...)                    = 0
# +++ exited (status 0) +++

# 看 syscall
strace ./hello2 2>&1 | grep -v "^(execve\|brk\|mmap\|mprotect\|munmap\|access\|openat\|read\|fstat\|close\|arch_prctl\|set_tid_address\|set_robust_list\|rseq\|prlimit64\|getrandom\|statx)"
# write(1, "hello 42\n", 9)        = 9
# exit_group(0)                    = ?
```

**這就是 syscall 跟 lib call 的差別**：printf 是 lib call，最終變成 1 個 write syscall。

**3. 拿掉 fflush**

```c
printf("hello %d\n", 42);
// 沒 fflush
return 0;
```

跑 strace 還是看到 write — 因為 exit 時會 flush。但如果 printf 沒換行、又沒 fflush、又沒 exit（例如 loop 中印），buffer 不滿就不會 write。

這就是「printf debug 但什麼都看不到」的常見原因。

**4. 觀察 fd 繼承**

```c
// fd_inherit.c
#include <stdio.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/wait.h>

int main(void) {
    int fd = open("/tmp/inherit.log", O_CREAT | O_WRONLY | O_TRUNC, 0644);
    dprintf(fd, "from parent\n");

    pid_t pid = fork();
    if (pid == 0) {
        // child 不顯式提到 fd，但它還在
        dprintf(fd, "from child\n");
        return 0;
    }
    waitpid(pid, NULL, 0);
    dprintf(fd, "parent again\n");
    close(fd);
    return 0;
}
```

```bash
gcc fd_inherit.c -o fd_inherit
./fd_inherit
cat /tmp/inherit.log
# from parent
# from child
# parent again
```

child 沒重新 open，靠繼承。

**5. 觀察 zombie**

```c
// zombie.c
#include <stdio.h>
#include <unistd.h>

int main(void) {
    if (fork() == 0) {
        return 0;   // child 立刻死
    }
    sleep(60);      // parent 不 wait
    return 0;
}
```

```bash
gcc zombie.c -o zombie
./zombie &
sleep 1
ps -ef | grep -E "zombie|defunct"
# 你會看到一個 <defunct> 的 entry
```

zombie 的 entry 還佔 PID slot 但不吃資源。kill parent 後 init 收養並 reap 掉。

## 自我檢核

- [ ] 講得出 process 五個核心抽象（PID / addr space / fd table / credential / signal）
- [ ] 知道 fork + exec 為什麼分兩步
- [ ] 懂 syscall 怎麼從 user mode 進 kernel mode（rax + syscall instruction）
- [ ] 知道 vDSO 是什麼、為什麼有些 function strace 看不到
- [ ] 知道 fd 是統一 abstraction（file / socket / pipe / ... 通用）
- [ ] 知道 SIGKILL / SIGSTOP 不能 catch
- [ ] 講得出 fork 跟 thread 在 clone() flag 上的差別

下一章正式進 ptrace。所有 tracer 工具的底層機制。

→ [Ch 3 ptrace(2) 完整剖析](./03-ptrace-syscall-deep-dive.md)
