# Ch 34 — seccomp-bpf：syscall 過濾

> **目標**：理解 seccomp-bpf 的設計——BPF filter 的 seccomp context（`struct seccomp_data`）、所有 `SECCOMP_RET_*` action 的語意、如何用 seccomp 限制 container 的 syscall 集合，以及 libseccomp 的 API。

## Seccomp 的演化

```
seccomp 的歷史：

kernel 2.6.12（2005）：strict mode
  → 一旦啟用，process 只能 read/write/exit/_exit/sigreturn
  → 用於計算沙箱（折舊，實際用途有限）

kernel 3.5（2012）：seccomp-bpf
  → 用 Classic BPF filter 決定每個 syscall 的命運
  → Chrome browser 率先大規模使用
  → Docker/containerd 用它實作 seccomp profiles
  → 現在是容器安全的標準組件
```

## `struct seccomp_data`：BPF filter 看到的 syscall 資訊

```c
/* <linux/seccomp.h> */
struct seccomp_data {
    int   nr;                    /* syscall number（__NR_*）*/
    __u32 arch;                  /* AUDIT_ARCH_X86_64 等 */
    __u64 instruction_pointer;   /* syscall 指令的 RIP */
    __u64 args[6];               /* syscall 的 6 個參數 */
};
```

BPF filter 可以讀取這個 struct 的任何欄位，決定 syscall 的命運。

## `SECCOMP_RET_*` Actions

| Action | 值 | 語意 |
|---|---|---|
| `SECCOMP_RET_KILL_PROCESS` | `0x80000000` | 立即 kill 整個 process group（SIGSYS）|
| `SECCOMP_RET_KILL_THREAD` | `0x00000000` | Kill 當前 thread |
| `SECCOMP_RET_TRAP` | `0x00030000` | 送 SIGSYS（可以用 signal handler 捕獲）|
| `SECCOMP_RET_ERRNO` | `0x00050000 \| errno` | 讓 syscall 回傳指定的 error（不實際執行）|
| `SECCOMP_RET_TRACE` | `0x7ff00000` | 通知 ptrace tracer |
| `SECCOMP_RET_LOG` | `0x7ffc0000` | 記錄 log，然後 ALLOW |
| `SECCOMP_RET_ALLOW` | `0x7fff0000` | 允許 syscall |
| `SECCOMP_RET_USER_NOTIF` | `0x7fc00000` | 通知 userspace（kernel 5.0+，用於 rootless container）|

## 直接用 BPF 指令寫 Seccomp Filter

```c
/* seccomp_demo.c */
#include <linux/seccomp.h>
#include <linux/filter.h>
#include <linux/audit.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <stdio.h>
#include <errno.h>

/* 定義 architecture 檢查（防止 syscall table confusion attacks）*/
#define VALIDATE_ARCHITECTURE \
    BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, arch)), \
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, AUDIT_ARCH_X86_64, 1, 0), \
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS)

/* 載入 syscall 號碼到 accumulator */
#define EXAMINE_SYSCALL \
    BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr))

/* 允許 syscall nr */
#define ALLOW_SYSCALL(nr) \
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, (nr), 0, 1), \
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW)

/* 讓 syscall 回傳 -EPERM（不 kill）*/
#define DENY_SYSCALL(nr) \
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, (nr), 0, 1), \
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | EPERM)

/* 範例 filter：允許基本操作，拒絕 ptrace */
static struct sock_filter filter[] = {
    VALIDATE_ARCHITECTURE,
    EXAMINE_SYSCALL,
    /* 允許的 syscall */
    ALLOW_SYSCALL(__NR_read),
    ALLOW_SYSCALL(__NR_write),
    ALLOW_SYSCALL(__NR_fstat),
    ALLOW_SYSCALL(__NR_mmap),
    ALLOW_SYSCALL(__NR_mprotect),
    ALLOW_SYSCALL(__NR_munmap),
    ALLOW_SYSCALL(__NR_brk),
    ALLOW_SYSCALL(__NR_exit),
    ALLOW_SYSCALL(__NR_exit_group),
    /* 拒絕危險 syscall */
    DENY_SYSCALL(__NR_ptrace),
    DENY_SYSCALL(__NR_process_vm_readv),
    DENY_SYSCALL(__NR_process_vm_writev),
    /* 預設：KILL */
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS),
};

int main(void)
{
    /* 必須先設定 NO_NEW_PRIVS */
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) < 0) {
        perror("prctl"); return 1;
    }

    /* 安裝 filter */
    struct sock_fprog prog = {
        .len    = sizeof(filter) / sizeof(filter[0]),
        .filter = filter,
    };
    if (prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &prog) < 0) {
        perror("seccomp"); return 1;
    }

    /* 現在這個 process 被限制了 */
    write(STDOUT_FILENO, "Hello, seccomp!\n", 16);

    /* 這行會被 EPERM（因為 __NR_ptrace 被 deny）*/
    /* long r = ptrace(PTRACE_TRACEME, 0, NULL, NULL); */
    /* printf("ptrace: %ld (errno=%d)\n", r, errno); */

    return 0;
}
```

## libseccomp：更高階的 API

直接寫 BPF filter 很容易出錯（尤其是 jt/jf offset）。`libseccomp` 提供了高階 API：

```c
/* 用 libseccomp 建立 whitelist policy */
#include <seccomp.h>

int setup_seccomp(void)
{
    /* 預設 action：KILL_PROCESS */
    scmp_filter_ctx ctx = seccomp_init(SCMP_ACT_KILL_PROCESS);
    if (!ctx) return -1;

    /* 允許的 syscall */
    int allowed[] = {
        SCMP_SYS(read), SCMP_SYS(write), SCMP_SYS(open),
        SCMP_SYS(close), SCMP_SYS(fstat), SCMP_SYS(mmap),
        SCMP_SYS(mprotect), SCMP_SYS(munmap), SCMP_SYS(brk),
        SCMP_SYS(exit), SCMP_SYS(exit_group),
    };

    for (int i = 0; i < sizeof(allowed)/sizeof(allowed[0]); i++)
        seccomp_rule_add(ctx, SCMP_ACT_ALLOW, allowed[i], 0);

    /* 特殊規則：允許 open，但拒絕 O_WRONLY | O_RDWR */
    seccomp_rule_add(ctx, SCMP_ACT_ERRNO(EACCES), SCMP_SYS(open), 1,
                     SCMP_A1(SCMP_CMP_MASKED_EQ, O_WRONLY | O_RDWR, O_WRONLY));

    int ret = seccomp_load(ctx);
    seccomp_release(ctx);
    return ret;
}
```

```bash
# 安裝 libseccomp
sudo apt install libseccomp-dev

# 編譯
gcc -o demo demo.c -lseccomp
```

## Docker 的 Seccomp Profile

Docker 使用 JSON 格式的 seccomp profile：

```json
{
    "defaultAction": "SCMP_ACT_ERRNO",
    "syscalls": [
        {
            "names": ["read", "write", "open", "close", "stat", "fstat"],
            "action": "SCMP_ACT_ALLOW"
        },
        {
            "names": ["ptrace"],
            "action": "SCMP_ACT_ERRNO",
            "errnoRet": 1
        }
    ]
}
```

```bash
# 使用自訂 seccomp profile 啟動 container
docker run --security-opt seccomp=/path/to/profile.json alpine sh
```

## SECCOMP_USER_NOTIF（kernel 5.0+）

允許 userspace 程式接管被攔截的 syscall 的決策（不需要 root）：

```c
/* 設定 user notification filter */
/* seccomp(..., SECCOMP_RET_USER_NOTIF, ...) */
/* 用 seccomp_notif 機制讓 container manager 代替 container 做 syscall */
```

這是 rootless container（不需要 root 的 container）的關鍵機制，讓 container runtime 可以代理某些需要 root 的 syscall。

## 踩雷集錦

1. **`PR_SET_NO_NEW_PRIVS` 必須在 `PR_SET_SECCOMP` 之前設定**：否則得到 EPERM（除非有 `CAP_SYS_ADMIN`）

2. **arch 檢查很重要**：在 x86-64 上可以執行 32-bit 的 syscall（`int 0x80`）；如果不檢查 arch，攻擊者可以用 32-bit syscall 繞過 64-bit 的 filter

3. **Filter 是 AND 疊加的**：可以多次呼叫 `prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, ...)`，每個 filter 都會執行；最嚴格的（最早 return 的）獲勝

4. **`SECCOMP_RET_ERRNO` 的值**：`SECCOMP_RET_ERRNO | 1` 讓 syscall 回傳 errno = 1（EPERM）；注意 errno 值要放在低 16 bits

5. **Filter 繼承**：子 process 繼承父 process 的 seccomp filter；`PR_SET_SECCOMP` 是不可 undo 的（只能加嚴格，不能放寬）

## 動手練習

1. 寫一個 seccomp filter，只允許 write + exit，然後在 apply filter 後嘗試 `read()`，確認得到 SIGSYS（或 EPERM）

2. 用 `seccomp-tools` 工具（`gem install seccomp-tools`）分析 Docker 容器的 seccomp profile：`docker run alpine sh -c 'cat /proc/1/status'` 然後 `sudo seccomp-tools dump -p <pid>`

3. 用 libseccomp 寫一個「paranoid」filter，允許 web server 需要的最小 syscall 集合，測試能否正常提供 HTTP 服務

## 本章重點整理

- seccomp-bpf 用 Classic BPF filter 對每個 syscall 做決策，是容器安全的標準組件
- `SECCOMP_RET_ERRNO` 讓 syscall 「失敗」而不殺掉 process（對 probe 更友好）
- Arch 檢查是必要的安全措施（防止 32-bit syscall confusion）
- libseccomp 提供比裸 BPF 指令更友好的 API

## 自我檢核

- [ ] 能說出 `SECCOMP_RET_KILL_PROCESS`、`SECCOMP_RET_ERRNO`、`SECCOMP_RET_ALLOW` 的語意差異
- [ ] 知道為什麼 seccomp filter 裡必須做 arch 檢查
- [ ] 能解釋為什麼 `PR_SET_NO_NEW_PRIVS` 必須先設

→ [Ch 35 BPF-LSM：強制存取控制](./35-bpf-lsm.md)
