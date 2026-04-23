# Ch 23 — BPF LSM：kernel 級安全鉤子

> 目標：認識 LSM 框架、BPF LSM 在 kernel 5.7 加入的意義、寫一個 file_open hook 阻擋特定路徑、與 SELinux / AppArmor / seccomp 的對位。

## LSM 是什麼

Linux Security Module — 2002 加入的 kernel 框架。在 kernel **每個安全敏感操作**前面插一個 hook，讓 security module（例如 SELinux）有機會說「這個操作准不准」。

例子：

```c
// kernel 內部簡化版
int do_open(struct file *f, ...) {
    /* ... 一堆檢查 ... */
    if (security_file_open(f) != 0)   // ← LSM hook
        return -EPERM;
    /* ... 真的開檔 ... */
}
```

`security_file_open` 是 LSM hook。每個 LSM module 可以註冊自己的 callback，回傳 0（允許）或 `-EPERM`（拒絕）。

歷史上的 LSM module：
- SELinux（Red Hat 主導）
- AppArmor（Ubuntu 主導）
- Smack
- TOMOYO
- Yama

問題是這些都用**自己的政策語言**，學習曲線陡、開發痛。

## BPF LSM：用 eBPF 寫 LSM hook

5.7 加入。讓你寫 BPF 程式 attach 到任意 LSM hook：

```c
SEC("lsm/file_open")
int BPF_PROG(check_open, struct file *file) {
    /* 你的邏輯 */
    return 0;        // 允許
    // return -EPERM;   // 拒絕
}
```

**革命性**：不用學 SELinux policy、不用寫 AppArmor profile、用熟悉的 C + BPF 寫 — 而且能用 BPF 的所有東西（map、helper、CO-RE）。

## 列出可掛的 LSM hook

```bash
sudo bpftrace -l 'lsm:*' | head -20
# lsm:bpf
# lsm:bprm_check_security
# lsm:bprm_committed_creds
# lsm:capable
# lsm:file_alloc_security
# lsm:file_open
# lsm:file_permission
# lsm:inode_create
# lsm:inode_link
# lsm:inode_unlink
# lsm:socket_bind
# lsm:socket_connect
# lsm:task_alloc
# lsm:task_kill
# ...
```

幾百個 hook，覆蓋 file、inode、socket、task、capability、bpf、key、tun 等子系統。

完整 list 在 kernel `include/linux/lsm_hook_defs.h`。

## 啟用 BPF LSM

不是預設啟用：

```bash
# 檢查
sudo cat /sys/kernel/security/lsm
# 應該包含 "bpf"，例如 "lockdown,capability,landlock,yama,apparmor,bpf"
```

沒有 bpf 的話 boot 時加 kernel param：

```
lsm=lockdown,capability,landlock,yama,apparmor,bpf
```

或在 `/etc/default/grub` 裡 `GRUB_CMDLINE_LINUX_DEFAULT` 加 `lsm=...,bpf`，`update-grub` 後重開。

## 第一個 BPF LSM — 阻擋讀 /etc/shadow

```c
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>
#include <bpf/bpf_core_read.h>

char LICENSE[] SEC("license") = "GPL";

const char target[] = "/etc/shadow";

SEC("lsm/file_open")
int BPF_PROG(deny_shadow, struct file *file) {
    char path[64];
    struct dentry *dentry = BPF_CORE_READ(file, f_path.dentry);
    struct qstr d_name = BPF_CORE_READ(dentry, d_name);

    bpf_probe_read_kernel_str(path, sizeof(path), d_name.name);

    // 簡化版：只比對 basename
    for (int i = 0; i < 7 && i < sizeof(path) - 1; i++) {
        if (path[i] != "shadow"[i]) return 0;
    }
    return -1;   // -EPERM
}
```

build & attach：

```bash
clang -O2 -g -target bpf -c lsm.bpf.c -o lsm.bpf.o
sudo bpftool prog load lsm.bpf.o /sys/fs/bpf/lsm autoattach

# 試
cat /etc/shadow
# cat: /etc/shadow: Operation not permitted

# detach
sudo rm /sys/fs/bpf/lsm
```

**整個 system 上所有 process 都被擋**（除了你 detach）。

## BPF LSM 的能力比 seccomp 強多少

| 能力 | seccomp | BPF LSM |
|---|---|---|
| 看 syscall + arg | ✅ | ✅（透過 task_struct） |
| 看 user pointer 內容 | ❌ | ✅（用 `bpf_probe_read_user`） |
| 看 task / process 屬性 | 有限 | ✅（透過 BTF） |
| 看 file path、inode、mount | ❌ | ✅ |
| 看 socket 對端、namespace | ❌ | ✅ |
| 阻擋特定 syscall | ✅ | ✅ |
| 阻擋特定 file 操作 | ❌ | ✅ |
| 阻擋特定 network 連線 | ❌ | ✅ |
| 動態載入 / 修改規則 | ❌（filter immutable） | ✅（map update） |

簡單說：**seccomp 是粗篩，BPF LSM 是精篩**。Tetragon 等現代安全工具的 enforcement 部分都用 BPF LSM。

## 跟 SELinux / AppArmor 怎麼共存

LSM 設計是「stackable」 — 多個 module 同時跑，**任何一個說 NO 就 NO**。所以 BPF LSM 跟 SELinux 共存沒問題：

```
syscall → SELinux check → AppArmor check → BPF LSM check → kernel 真正執行
                                ↓ 任何一個 deny
                              EPERM
```

不會「裝 BPF LSM 就要關 SELinux」。

## 一個常見誤解

「BPF LSM 取代 SELinux」 — **不全然**。

SELinux 的 strength 是「成熟的 policy 語言、形式驗證、官方 distro 維護」。BPF LSM 強在「動態、客製化、用 C 寫」。

實務上：
- 政府 / 嚴格合規環境：SELinux 仍主流
- Cloud-native runtime security：BPF LSM 主導（Tetragon）
- Container default：seccomp + LSM（用哪個 LSM 看 distro）

兩者並非 zero-sum。

## 動手練習

1. **enable BPF LSM**：照上面 boot param 啟用。
2. **跑 deny_shadow**：上面範例完整跑通。
3. **改成 deny `/etc/passwd`**：改 target、重新 load。
4. **加 process whitelist**：用 map 維護「誰可以讀 shadow」（例如 systemd-shadow-daemon），其他全擋。
5. **試 socket_connect hook**：寫一個 LSM 阻擋 connect 到特定 IP。

## 自我檢核

- [ ] 我能解釋 LSM 框架的角色與 stackable 性質
- [ ] 我能列出至少 5 個 LSM hook 並說出能擋什麼
- [ ] 我能寫 BPF LSM 程式 + attach
- [ ] 我能對比 BPF LSM 與 seccomp 的能力差異
- [ ] 我能解釋為什麼 BPF LSM 不取代 SELinux

下一章我們看兩個明星專案 Falco / Tetragon — 一個觀測派、一個阻擋派，看大型 BPF 安全產品實際怎麼設計。

→ [Ch 24 觀測派 vs 阻擋派：Falco / Tetragon 架構](./24-falco-tetragon.md)
