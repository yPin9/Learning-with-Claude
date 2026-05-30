# Ch 35 — BPF-LSM：強制存取控制

> **目標**：理解 BPF-LSM 的架構——Linux Security Module hook 的位置、`BPF_PROG_TYPE_LSM` 的使用方式、和 SELinux/AppArmor 的關係，以及如何用 BPF-LSM 實作細粒度的 MAC policy。

## Linux Security Module（LSM）的架構

LSM 是 Linux kernel 的 security hook framework：在關鍵操作（file open、network connect、exec、IPC）的路徑上有數百個 hook，所有 LSM 模組（SELinux、AppArmor、Smack 等）都在這些 hook 上執行自己的策略。

```
Application
  │
  ▼ syscall
Kernel VFS / networking / IPC
  │
  ▼ LSM hook（例如 security_file_open）
  ├── SELinux（如果啟用）
  ├── AppArmor（如果啟用）
  └── BPF-LSM（kernel 5.7+，可以載入 BPF program）
  │
  ▼ 繼續執行（如果所有 module 都 allow）
```

BPF-LSM 讓你用 BPF 程式做「第三個 LSM 模組」，和 SELinux 並行執行（不需要替換現有的 LSM）。

## BPF-LSM 的 Hook 類型

BPF-LSM 支援大部分的 LSM hook，用 `SEC("lsm/<hook_name>")` 格式：

```c
/* 常用的 LSM hooks */
SEC("lsm/file_open")           /* 檔案被打開時 */
SEC("lsm/inode_mkdir")         /* mkdir 時 */
SEC("lsm/socket_connect")      /* TCP/UDP connect 時 */
SEC("lsm/socket_bind")         /* bind 時 */
SEC("lsm/bprm_check_security") /* exec 時（最關鍵的 hook）*/
SEC("lsm/task_kill")           /* 送 signal 時 */
SEC("lsm/cred_prepare")        /* 建立 credential 時 */
SEC("lsm/sb_mount")            /* mount 時 */
```

查看所有可用的 LSM hooks：

```bash
sudo bpftrace -l 'lsm:*' 2>/dev/null | head -30
# lsm:binder_set_context_mgr
# lsm:binder_transaction
# lsm:vm_enough_memory
# lsm:bprm_check_security
# lsm:bprm_committed_creds
# ...
```

## 第一個 BPF-LSM 程式

```c
/* lsm_demo.bpf.c */
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>
#include <bpf/bpf_core_read.h>

/* 需要 CAP_MAC_ADMIN 才能載入 LSM program */

SEC("lsm/bprm_check_security")
int BPF_PROG(prevent_exec, struct linux_binprm *bprm)
{
    /* bprm->filename = 要執行的程式路徑 */
    char filename[128];
    bpf_probe_read_kernel_str(filename, sizeof(filename),
                              BPF_CORE_READ(bprm, filename));

    /* 禁止執行 /usr/bin/curl */
    if (__builtin_memcmp(filename, "/usr/bin/curl", 13) == 0) {
        bpf_printk("LSM: blocked exec of %s\n", filename);
        return -EPERM;  /* 回傳負的 errno = 拒絕 */
    }

    return 0;  /* 0 = 允許 */
}

SEC("lsm/socket_connect")
int BPF_PROG(prevent_connect, struct socket *sock, struct sockaddr *address, int addrlen)
{
    /* 取得目標 IP */
    if (address->sa_family != AF_INET) return 0;

    struct sockaddr_in *addr4 = (struct sockaddr_in *)address;
    __be32 dst_ip = BPF_CORE_READ(addr4, sin_addr.s_addr);

    /* 禁止連接到 1.2.3.4 */
    if (dst_ip == bpf_htonl(0x01020304))
        return -EACCES;

    return 0;
}

char LICENSE[] SEC("license") = "GPL";
```

**回傳值**：
- `0`：允許
- 負數（例如 `-EPERM`，`-EACCES`）：拒絕，syscall 回傳對應的 error

## 存取 Cred 和 UID

```c
SEC("lsm/file_open")
int BPF_PROG(audit_open, struct file *file, int mask)
{
    /* 取得目前的 credentials */
    const struct cred *cred = (const struct cred *)bpf_get_current_cred();
    uid_t uid = BPF_CORE_READ(cred, uid.val);

    if (uid == 0) return 0;  /* root 允許一切 */

    /* 取得 file 的 inode 路徑 */
    /* （從 file->f_path.dentry->d_name.name）*/
    const char *name = BPF_CORE_READ(file, f_path.dentry, d_name.name);
    char filename[64];
    bpf_probe_read_kernel_str(filename, sizeof(filename), name);

    bpf_printk("uid=%u open: %s\n", uid, filename);
    return 0;  /* 只 audit，不 block */
}
```

## Landlock：用戶空間定義的沙箱

Landlock（kernel 5.13+）是 BPF-LSM 的上層：讓非特權 userspace 程式自我限制自己的存取能力（類似 OpenBSD 的 pledge / unveil）。

```c
/* landlock_demo.c */
#include <linux/landlock.h>
#include <sys/prctl.h>
#include <sys/syscall.h>

int main(void)
{
    /* 建立 Landlock ruleset */
    struct landlock_ruleset_attr rs_attr = {
        .handled_access_fs =
            LANDLOCK_ACCESS_FS_EXECUTE |
            LANDLOCK_ACCESS_FS_WRITE_FILE |
            LANDLOCK_ACCESS_FS_READ_FILE |
            LANDLOCK_ACCESS_FS_READ_DIR,
    };

    int ruleset_fd = syscall(SYS_landlock_create_ruleset,
                             &rs_attr, sizeof(rs_attr), 0);

    /* 允許 /tmp 的所有 access */
    int dir_fd = open("/tmp", O_PATH | O_DIRECTORY);
    struct landlock_path_beneath_attr path_attr = {
        .allowed_access = LANDLOCK_ACCESS_FS_READ_FILE |
                          LANDLOCK_ACCESS_FS_WRITE_FILE |
                          LANDLOCK_ACCESS_FS_READ_DIR,
        .parent_fd = dir_fd,
    };
    syscall(SYS_landlock_add_rule, ruleset_fd,
            LANDLOCK_RULE_PATH_BENEATH, &path_attr, 0);
    close(dir_fd);

    /* 啟用：之後只能存取 /tmp */
    prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0);
    syscall(SYS_landlock_restrict_self, ruleset_fd, 0);
    close(ruleset_fd);

    /* 現在嘗試存取 /etc/passwd 會被拒絕 */
    FILE *f = fopen("/etc/passwd", "r");
    if (!f) printf("/etc/passwd access denied (expected)\n");

    /* /tmp 可以存取 */
    f = fopen("/tmp/test", "w");
    if (f) { printf("/tmp/test accessible\n"); fclose(f); }

    return 0;
}
```

## BPF-LSM vs SELinux vs AppArmor

| 面向 | BPF-LSM | SELinux | AppArmor |
|---|---|---|---|
| **Policy 語言** | BPF C code | Type Enforcement | Profile files |
| **動態更新** | 是（reload BPF program）| 否（需要 policy compile）| 是（reload profile）|
| **粒度** | 任意（BPF map）| Label-based | Path-based |
| **與現有 LSM 的關係** | 並行運行 | 替代 | 並行（有限）|
| **適合場景** | 自訂 audit、細粒度 deny | 系統級 MAC | Application sandbox |

## 踩雷集錦

1. **BPF-LSM 需要 `CAP_MAC_ADMIN`**：載入 LSM program 需要這個 capability；普通 root 用戶（uid=0）不夠；需要 `sudo -g cap_mac_admin`

2. **BPF-LSM 的 return value 是 security decision**：`0` = 允許；負數 = 拒絕；`return 1` 可能 crash（verifier 通常會 reject，但要小心）

3. **LSM hook 只能 append（不能取代既有的 MAC）**：BPF-LSM 程式和 SELinux 並行；如果 SELinux 已經拒絕了，BPF-LSM 的 allow 沒有用

4. **`bpf_get_current_cred()` 的型別**：回傳 `const struct cred *`，但 verifier 要求用 `bpf_core_read` 而不是直接 dereference

5. **Kernel 5.7+ 需要 `CONFIG_BPF_LSM=y` 且 `lsm=bpf` 在 kernel 命令列**：確認 `cat /sys/kernel/security/lsm` 包含 `bpf`

## 動手練習

1. 寫一個 BPF-LSM `bprm_check_security` program，audit（log）所有 uid > 1000 的 process 執行的程式，但不 block

2. 加入 block 功能：禁止 uid > 1000 的 user 執行 `/usr/bin/nc`（netcat）

3. 用 Landlock 沙箱化一個你寫的程式，讓它只能存取 `/tmp`，確認存取其他路徑時回傳 EACCES

## 本章重點整理

- BPF-LSM 讓你用 BPF 程式實作 LSM 策略，和 SELinux 並行執行
- Hook 在 kernel 的安全關鍵操作（file open、exec、connect）上；返回負 errno = 拒絕
- Landlock 是面向 userspace 的沙箱機制，讓程式自我限制 filesystem 存取
- BPF-LSM 的優勢：動態更新、BPF map 存儲細粒度策略

## 自我檢核

- [ ] 能說出 BPF-LSM program 的 return value 語意（0 = allow，負數 = deny）
- [ ] 知道 BPF-LSM 需要哪個 capability，以及和 SELinux 的並行關係
- [ ] 能解釋 Landlock 和 seccomp-bpf 的應用場景差異

→ [Ch 36 Falco & Tetragon](./36-falco-tetragon.md)
