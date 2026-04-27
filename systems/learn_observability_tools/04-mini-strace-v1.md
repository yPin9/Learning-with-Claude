# Ch 4 — 動手：mini-strace v1

> 目標：用 ptrace 寫一支真正能用的 mini-strace。會 print syscall name、參數（含字串）、回傳值。500 行內，500 行外搞定 strace 90% 的場景。

## 我們在哪裡

Part 1 的整合練習。Ch 3 學了 ptrace 機制，這章把它變成「人類看得懂的 syscall 紀錄器」。

## 規格

```bash
./mystrace /bin/ls /tmp
```

期望輸出：

```
execve("/bin/ls", ["ls", "/tmp"], ...) = 0
brk(NULL)                               = 0x55b...
arch_prctl(0x3001, 0x7ff...)            = -1 EINVAL (Invalid argument)
access("/etc/ld.so.preload", R_OK)      = -1 ENOENT (No such file or directory)
openat(AT_FDCWD, "/etc/ld.so.cache", O_RDONLY|O_CLOEXEC) = 3
fstat(3, {st_mode=...,st_size=...})     = 0
mmap(NULL, ..., PROT_READ, MAP_PRIVATE, 3, 0) = 0x7ff...
close(3)                                = 0
...
write(1, "file1\nfile2\n", 12)          = 12
exit_group(0)                           = ?
+++ exited with 0 +++
```

關鍵特徵：syscall name、參數（字串內容）、回傳值、errno 名字。

## 實作策略

5 件事：

1. **fork + PTRACE_TRACEME + execvp**：把 child 啟動成 tracee
2. **loop**：PTRACE_SYSCALL → wait → 印 enter → PTRACE_SYSCALL → wait → 印 exit
3. **syscall number → name**：簡單表
4. **register → 參數**：按 syscall 的 signature decode，字串用 PEEKDATA / process_vm_readv 讀
5. **errno → name**：失敗時把負的 rax 翻名字

要短就只支援幾個 syscall（read/write/open/close/...），要完整就大量 case。我們做出 ~30 個常用 syscall 支援。

## 骨架

```c
// mystrace.c
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <sys/ptrace.h>
#include <sys/wait.h>
#include <sys/user.h>
#include <sys/uio.h>
#include <sys/syscall.h>

// 從 register 拿 syscall arg
static long get_arg(struct user_regs_struct *r, int n) {
    switch (n) {
        case 0: return r->rdi;
        case 1: return r->rsi;
        case 2: return r->rdx;
        case 3: return r->r10;
        case 4: return r->r8;
        case 5: return r->r9;
    }
    return 0;
}

// 從 tracee 讀字串，回傳到 buf
static void read_string(pid_t pid, long addr, char *buf, size_t buflen) {
    struct iovec local = { .iov_base = buf, .iov_len = buflen - 1 };
    struct iovec remote = { .iov_base = (void*)addr, .iov_len = buflen - 1 };
    ssize_t n = process_vm_readv(pid, &local, 1, &remote, 1, 0);
    if (n < 0) { strcpy(buf, "<read err>"); return; }
    buf[n] = '\0';
    // 找 null terminator
    size_t i;
    for (i = 0; i < (size_t)n; i++) if (buf[i] == '\0') break;
    if (i == (size_t)n) buf[i-1] = '\0';   // 截斷
}

// errno 名字表（簡化）
static const char *errno_name(int err) {
    switch (err) {
        case EPERM: return "EPERM";
        case ENOENT: return "ENOENT";
        case ESRCH: return "ESRCH";
        case EINTR: return "EINTR";
        case EBADF: return "EBADF";
        case EAGAIN: return "EAGAIN";
        case ENOMEM: return "ENOMEM";
        case EACCES: return "EACCES";
        case EFAULT: return "EFAULT";
        case EEXIST: return "EEXIST";
        case ENOTDIR: return "ENOTDIR";
        case EISDIR: return "EISDIR";
        case EINVAL: return "EINVAL";
        case EMFILE: return "EMFILE";
        case ENOSPC: return "ENOSPC";
        case EPIPE: return "EPIPE";
        case ECONNREFUSED: return "ECONNREFUSED";
        default: return "?";
    }
}

// syscall name 表（簡化）
static const char *syscall_name(long n) {
    switch (n) {
        case SYS_read:        return "read";
        case SYS_write:       return "write";
        case SYS_open:        return "open";
        case SYS_close:       return "close";
        case SYS_stat:        return "stat";
        case SYS_fstat:       return "fstat";
        case SYS_lseek:       return "lseek";
        case SYS_mmap:        return "mmap";
        case SYS_mprotect:    return "mprotect";
        case SYS_munmap:      return "munmap";
        case SYS_brk:         return "brk";
        case SYS_rt_sigaction:return "rt_sigaction";
        case SYS_rt_sigprocmask:return "rt_sigprocmask";
        case SYS_ioctl:       return "ioctl";
        case SYS_pread64:     return "pread64";
        case SYS_pwrite64:    return "pwrite64";
        case SYS_access:      return "access";
        case SYS_pipe:        return "pipe";
        case SYS_dup:         return "dup";
        case SYS_dup2:        return "dup2";
        case SYS_nanosleep:   return "nanosleep";
        case SYS_getpid:      return "getpid";
        case SYS_socket:      return "socket";
        case SYS_connect:     return "connect";
        case SYS_accept:      return "accept";
        case SYS_sendto:      return "sendto";
        case SYS_recvfrom:    return "recvfrom";
        case SYS_clone:       return "clone";
        case SYS_fork:        return "fork";
        case SYS_execve:      return "execve";
        case SYS_exit:        return "exit";
        case SYS_exit_group:  return "exit_group";
        case SYS_wait4:       return "wait4";
        case SYS_kill:        return "kill";
        case SYS_uname:       return "uname";
        case SYS_fcntl:       return "fcntl";
        case SYS_getdents64:  return "getdents64";
        case SYS_openat:      return "openat";
        case SYS_newfstatat:  return "newfstatat";
        case SYS_arch_prctl:  return "arch_prctl";
        case SYS_set_tid_address: return "set_tid_address";
    }
    return NULL;
}

// 印 syscall enter（只印名字 + 主要參數）
static void print_enter(pid_t pid, struct user_regs_struct *r) {
    long n = r->orig_rax;
    const char *name = syscall_name(n);
    char strbuf[256];

    if (!name) {
        printf("syscall_%ld(", n);
    } else {
        printf("%s(", name);
    }

    // 大部分 syscall 印前 3 個參數，特殊 syscall 特殊處理
    switch (n) {
        case SYS_openat: {
            long dfd = get_arg(r, 0);
            long pathaddr = get_arg(r, 1);
            long flags = get_arg(r, 2);
            read_string(pid, pathaddr, strbuf, sizeof(strbuf));
            if (dfd == AT_FDCWD)
                printf("AT_FDCWD, \"%s\", %#lx", strbuf, flags);
            else
                printf("%ld, \"%s\", %#lx", dfd, strbuf, flags);
            break;
        }
        case SYS_open:
        case SYS_access:
        case SYS_stat: {
            long pathaddr = get_arg(r, 0);
            read_string(pid, pathaddr, strbuf, sizeof(strbuf));
            printf("\"%s\", %#lx", strbuf, get_arg(r, 1));
            break;
        }
        case SYS_write: {
            long fd = get_arg(r, 0);
            long bufaddr = get_arg(r, 1);
            long len = get_arg(r, 2);
            size_t n = len > 32 ? 32 : len;
            read_string(pid, bufaddr, strbuf, n + 1);
            // 替換不可印字元
            for (size_t i = 0; i < n; i++)
                if (strbuf[i] < 32 || strbuf[i] > 126) strbuf[i] = '.';
            strbuf[n] = '\0';
            printf("%ld, \"%s\"%s, %ld", fd, strbuf, len > 32 ? "..." : "", len);
            break;
        }
        case SYS_read:
        case SYS_close: {
            printf("%ld", get_arg(r, 0));
            if (n != SYS_close) printf(", ?, %ld", get_arg(r, 2));
            break;
        }
        case SYS_execve: {
            long pathaddr = get_arg(r, 0);
            read_string(pid, pathaddr, strbuf, sizeof(strbuf));
            printf("\"%s\", ..., ...", strbuf);
            break;
        }
        case SYS_brk:
        case SYS_mmap:
        case SYS_munmap:
            printf("%#lx, ...", get_arg(r, 0));
            break;
        default:
            printf("%#lx, %#lx, %#lx", get_arg(r, 0), get_arg(r, 1), get_arg(r, 2));
    }

    printf(")");
    fflush(stdout);
}

// 印 syscall exit（回傳值）
static void print_exit(struct user_regs_struct *r) {
    long ret = r->rax;
    if (ret < 0 && ret > -4096) {
        int err = -ret;
        printf(" = -1 %s (%s)\n", errno_name(err), strerror(err));
    } else {
        printf(" = %ld\n", ret);
    }
}

int main(int argc, char *argv[]) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s <command> [args...]\n", argv[0]);
        return 1;
    }

    pid_t pid = fork();
    if (pid == 0) {
        ptrace(PTRACE_TRACEME, 0, 0, 0);
        execvp(argv[1], &argv[1]);
        perror("execvp");
        return 1;
    }

    int status;
    waitpid(pid, &status, 0);
    ptrace(PTRACE_SETOPTIONS, pid, 0, PTRACE_O_TRACESYSGOOD);

    int in_syscall = 0;
    struct user_regs_struct regs;

    while (1) {
        if (ptrace(PTRACE_SYSCALL, pid, 0, 0) < 0) break;
        if (waitpid(pid, &status, 0) < 0) break;

        if (WIFEXITED(status)) {
            printf("+++ exited with %d +++\n", WEXITSTATUS(status));
            break;
        }
        if (WIFSIGNALED(status)) {
            printf("+++ killed by %d +++\n", WTERMSIG(status));
            break;
        }
        if (!WIFSTOPPED(status)) continue;
        if (WSTOPSIG(status) != (SIGTRAP | 0x80)) {
            // 不是 syscall stop，可能是其他 signal，pass through
            ptrace(PTRACE_SYSCALL, pid, 0, WSTOPSIG(status));
            continue;
        }

        ptrace(PTRACE_GETREGS, pid, 0, &regs);

        if (!in_syscall) {
            print_enter(pid, &regs);
            in_syscall = 1;
        } else {
            print_exit(&regs);
            in_syscall = 0;
        }
    }

    return 0;
}
```

## 編譯與跑

```bash
gcc -O2 mystrace.c -o mystrace
./mystrace /bin/echo hello
```

期望看到：

```
execve("/bin/echo", ..., ...) = 0
brk(0x0, ...) = 0x55...
arch_prctl(0x3001, 0x7ff..., 0x7ff...) = -1 EINVAL (Invalid argument)
access("/etc/ld.so.preload", 0x4) = -1 ENOENT (No such file or directory)
openat(AT_FDCWD, "/etc/ld.so.cache", 0x80000) = 3
...
write(1, "hello", 6) = 6
exit_group(0x0, 0x0, 0x0) = ?
+++ exited with 0 +++
```

對照真的 strace：

```bash
strace /bin/echo hello 2>&1 | head -10
```

看你的版本少印什麼（多半是參數細節 — 例如 `O_RDONLY|O_CLOEXEC` 沒翻成 flag 名字）。

## 一個常見踩雷：execve 的「兩次 enter」

execve 有點怪 —— 進去 enter stop、kernel 把整個 address space 換掉、出來時 register 在新的 binary 裡。所以你會看到 execve 的 enter 跟 exit 「register 不一致」。實務上很多 strace 實作把 execve 的 exit 特殊處理。

我們的版本沒處理，會看到 execve 的 = 0 之前可能有奇怪 syscall。要 robust 要加：

```c
// 在 execve 完成後重設 in_syscall
if (regs.orig_rax == SYS_execve && in_syscall) {
    in_syscall = 0;
    print_exit(&regs);
    continue;
}
```

## 一個常見踩雷：fork / clone 的 child 沒 trace

我們的版本只 trace child，child 自己 fork 出的孫子不會被 trace。要 trace 全家：

```c
ptrace(PTRACE_SETOPTIONS, pid,
       PTRACE_O_TRACESYSGOOD |
       PTRACE_O_TRACEFORK |
       PTRACE_O_TRACEVFORK |
       PTRACE_O_TRACECLONE,
       0);
```

設了之後 fork/vfork/clone 的 child 自動加進來 trace。但邏輯變複雜（要管多個 PID），留給 v2。

## 一個常見踩雷：wait status 不是 syscall stop

收到 SIGSEGV、SIGTERM 等其他 signal 也會 stop tracee。**必須 pass through** 給 tracee（用 `PTRACE_SYSCALL` 第 4 參數），不然 tracee 永遠收不到。

我們的版本有處理：

```c
if (WSTOPSIG(status) != (SIGTRAP | 0x80)) {
    ptrace(PTRACE_SYSCALL, pid, 0, WSTOPSIG(status));
    continue;
}
```

## 跟 strace 對照

我們的 mystrace ~250 行，strace 是 30K+ 行 C code。差別在：

| 功能 | mystrace | strace |
|---|---|---|
| syscall name 表 | ~30 個 | 全部（按 arch） |
| 參數 decode | 簡略 | 按 signature 完整解析 |
| flag bitmask 翻成名字 | ❌ | ✅（`O_RDONLY|O_CLOEXEC`） |
| follow fork | ❌ | `-f` |
| filter | ❌ | `-e trace=` |
| stack trace | ❌ | `-k` |
| timing | ❌ | `-c` / `-T` / `-tt` |
| attach 到 PID | ❌ | `-p` |
| seccomp 加速 | ❌ | 新版有 |
| 多 arch (32/64/ARM/...) | ❌ | ✅ |

但**核心機制完全一樣**。你寫完這個就懂 strace 在做什麼了。

## 動手練習

**1. 跑起來**

照上面 code 跑、對照 strace 看差異。

**2. 加 -e filter**

```bash
./mystrace -e openat,write /bin/ls
```

加參數 parse + 一個 filter set，只印指定 syscall。

**3. 加 attach 模式**

```bash
./mystrace -p PID
```

```c
// pseudocode
if (-p flag) {
    pid = atoi(arg);
    ptrace(PTRACE_ATTACH, pid, 0, 0);
    waitpid(pid, &status, 0);
} else {
    // fork + exec
}
```

**4. 加 follow fork**

設 `PTRACE_O_TRACEFORK` 等，維護一個 PID set，每次 wait 都用 `waitpid(-1, ..., __WALL)`。

**5. 加 timing**

每個 syscall enter / exit 之間 `clock_gettime`，印 elapsed。

**6. 加 -c summary**

每個 syscall 累計次數、累計時間，最後印 summary table。

## 自我檢核

- [ ] 自己 mini-strace 跑得出來
- [ ] 看得懂上面 code 每一行（特別是 `in_syscall` 切換邏輯）
- [ ] 知道為什麼要 pass-through 非 syscall 的 signal
- [ ] 知道 strace 比我們的版本多做了哪些事
- [ ] 知道 follow fork / filter / -c 該怎麼加（concept 而非實作）

Part 1 結束。下一章正式進 Part 2：用真的 strace 看真的 bug。

→ [Ch 5 strace 完整指南](./05-strace-complete-guide.md)
