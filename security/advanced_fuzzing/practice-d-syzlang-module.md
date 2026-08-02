# 練習 D：為有 Bug 的 Kernel Module 寫 Syzlang Description

**Part 4 — Syzkaller 深度實戰**

---

## 1. 目標

給定一個帶有已知 OOB write bug 的 char device kernel module，你要：

1. 讀懂 module 的介面（ioctl 定義、結構、device 路徑）
2. 用 syzlang 精確描述這個介面，讓 syzkaller 能自動生成合法的呼叫序列
3. 確保 description 能覆蓋到 `idx >= 64` 的越界輸入，使 fuzzer 能觸發 KASAN 報警
4. 理解 syzkaller 找到 bug 後的輸出物（crash entry、C reproducer）長什麼樣

完成後你對 syzlang 的 resource 繼承、ioctl number 編碼、struct 描述會有清楚的實作感。

---

## 2. 背景

Syzkaller 需要「知道介面長什麼樣」才能有效生成測試案例。這份知識來自 syzlang description（`.txt` 檔）。如果 description 寫得太保守（例如 idx 只在 0–63 之間），fuzzer 永遠看不到越界案例；如果 description 寫錯型別，生成的 syscall 會在 userspace 就 EFAULT 掉，根本進不了有 bug 的路徑。

這個練習的 module 刻意保持極簡：一個 char device，一個 ioctl command，一個沒做 bounds check 的陣列寫入。重點不在 module 有多複雜，在於你能不能把這個「介面語義」翻譯成 syzkaller 讀得懂的語言。

**核心漏洞機制：**

```
// kernel buffer 大小固定 64 bytes
char buf[64];

// ioctl handler 沒有做任何 bounds check
buf[req.idx] = (char)req.val;
//  ^^^^^^^^^^^
//  idx 若 >= 64，直接 OOB write 到 kernel heap
```

KASAN 的 shadow memory 會在越界的那一刻記錄寫入位址、呼叫堆疊，並 panic kernel（或繼續跑依 kasan_fault 設定）。

---

## 3. 任務規格

### 3.1 有 bug 的 Kernel Module

以下是完整的 module 源碼，直接使用，不要修改 bug。

**`mydev.c`**

```c
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/fs.h>
#include <linux/miscdevice.h>
#include <linux/uaccess.h>
#include <linux/slab.h>
#include <linux/ioctl.h>

#define MYDEV_MAGIC  'M'
#define MYDEV_CMD_WRITE  _IOW(MYDEV_MAGIC, 1, struct mydev_req)

struct mydev_req {
    __u32 idx;
    __u64 val;
};

struct mydev_state {
    char buf[64];   /* fixed-size buffer; no dynamic allocation */
    int  ref;
};

static struct mydev_state *global_state;

static int mydev_open(struct inode *inode, struct file *filp)
{
    filp->private_data = global_state;
    global_state->ref++;
    return 0;
}

static int mydev_release(struct inode *inode, struct file *filp)
{
    struct mydev_state *st = filp->private_data;
    st->ref--;
    /* BUG2 (optional): filp->private_data 沒清掉，
       若呼叫者在 close 後仍持有 filp 並再次 ioctl，
       st 指向的記憶體若已被釋放即成 UAF。
       本練習核心只要求觸發 BUG1（OOB write）。 */
    return 0;
}

static long mydev_ioctl(struct file *filp, unsigned int cmd,
                        unsigned long arg)
{
    struct mydev_state *st = filp->private_data;
    struct mydev_req req;

    if (cmd != MYDEV_CMD_WRITE)
        return -EINVAL;

    if (copy_from_user(&req, (void __user *)arg, sizeof(req)))
        return -EFAULT;

    /* BUG1: OOB write — idx 沒有做 bounds check */
    st->buf[req.idx] = (char)req.val;   /* crash 在這裡 */

    return 0;
}

static const struct file_operations mydev_fops = {
    .owner          = THIS_MODULE,
    .open           = mydev_open,
    .release        = mydev_release,
    .unlocked_ioctl = mydev_ioctl,
};

static struct miscdevice mydev_misc = {
    .minor = MISC_DYNAMIC_MINOR,
    .name  = "mydev",
    .fops  = &mydev_fops,
    .mode  = 0666,
};

static int __init mydev_init(void)
{
    int ret;

    global_state = kzalloc(sizeof(*global_state), GFP_KERNEL);
    if (!global_state)
        return -ENOMEM;

    ret = misc_register(&mydev_misc);
    if (ret) {
        kfree(global_state);
        return ret;
    }

    pr_info("mydev: loaded, /dev/mydev ready\n");
    return 0;
}

static void __exit mydev_exit(void)
{
    misc_deregister(&mydev_misc);
    kfree(global_state);
    pr_info("mydev: unloaded\n");
}

module_init(mydev_init);
module_exit(mydev_exit);
MODULE_LICENSE("GPL");
MODULE_AUTHOR("fuzzing-course");
MODULE_DESCRIPTION("Intentionally buggy char device for syzlang practice");
```

**`Makefile`**（WSL2 Ubuntu，需先安裝 `linux-headers-$(uname -r)`）

```makefile
obj-m := mydev.o

KDIR  ?= /lib/modules/$(shell uname -r)/build
PWD   := $(shell pwd)

all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules

clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean
```

### 3.2 計算 ioctl number

在寫 syzlang 之前，你需要知道 `MYDEV_CMD_WRITE` 展開後的數值。

`_IOW(type, nr, size)` 的編碼規則（x86-64）：

```
bits[31:30] = 方向 (01 = write from user)
bits[29:16] = sizeof(struct)
bits[15: 8] = type (magic byte)
bits[ 7: 0] = nr
```

`struct mydev_req` 的大小（含對齊）：

```c
struct mydev_req {
    __u32 idx;   // offset 0, size 4
                 // 4 bytes padding（讓 val 對齊 8-byte boundary）
    __u64 val;   // offset 8, size 8
};               // total = 16 bytes
```

計算（用 Python 驗算）：

```python
import ctypes

class mydev_req(ctypes.Structure):
    _fields_ = [("idx", ctypes.c_uint32),
                ("val", ctypes.c_uint64)]

print(ctypes.sizeof(mydev_req))   # 應該是 16

IOC_WRITE     = 1
IOC_NRBITS    = 8
IOC_TYPEBITS  = 8
IOC_SIZEBITS  = 14
IOC_NRSHIFT   = 0
IOC_TYPESHIFT = IOC_NRSHIFT   + IOC_NRBITS
IOC_SIZESHIFT = IOC_TYPESHIFT + IOC_TYPEBITS
IOC_DIRSHIFT  = IOC_SIZESHIFT + IOC_SIZEBITS

size      = ctypes.sizeof(mydev_req)
magic     = ord('M')   # 0x4d
nr        = 1
direction = IOC_WRITE

result = ((direction << IOC_DIRSHIFT)  |
          (size      << IOC_SIZESHIFT) |
          (magic     << IOC_TYPESHIFT) |
          (nr        << IOC_NRSHIFT))

print(hex(result))   # 預期：0x40104d01
```

### 3.3 你要完成的 syzlang description

在 syzkaller 的 `sys/linux/` 目錄下建立 `mydev.txt`。

**你需要寫出：**

1. `resource fd_mydev[fd]`：繼承 fd 型別的 resource，代表 `/dev/mydev` 的 fd
2. `openat$mydev` syscall 的描述，返回 `fd_mydev`
3. `ioctl$MYDEV_CMD_WRITE`：呼叫 `MYDEV_CMD_WRITE`，帶入 `mydev_req` struct
4. `mydev_req` 的 struct 定義，確保 `idx` 的型別能覆蓋到 `>= 64` 的值
5. 確保 fuzzer 知道呼叫順序：先 open，再 ioctl

---

## 4. 期望輸出

### 4.1 正確的 syzlang description

`mydev.txt` 應讓 syzkaller 能自動生成如下的程式片段：

```
r0 = openat$mydev(0xffffffffffffff9c, &AUTO='/dev/mydev\x00', 0x0, 0x0)
ioctl$MYDEV_CMD_WRITE(r0, 0x40104d01, &AUTO={0x80, 0x41})
```

其中 `idx=0x80`（128，超過 64）會觸發 OOB write。

### 4.2 KASAN 報告的樣貌

觸發 BUG1 後，帶 KASAN 的 kernel 應輸出類似：

```
==================================================================
BUG: KASAN: slab-out-of-bounds in mydev_ioctl+0x8c/0xb0 [mydev]
Write of size 1 at addr ffff888103a4c040 by task syz-executor/1234

CPU: 0 PID: 1234 Comm: syz-executor Tainted: G           OE     6.1.0 #1
Hardware name: QEMU Standard PC (i440FX + PIIX, 1996)
Call Trace:
 <TASK>
 dump_stack_lvl+0x44/0x5c
 print_report+0x17c/0x4a8
 kasan_report+0xb3/0x130
 mydev_ioctl+0x8c/0xb0 [mydev]
 __x64_sys_ioctl+0x127/0x190
 do_syscall_64+0x3d/0x90
 entry_SYSCALL_64_after_hwframe+0x46/0xb0

Allocated by task 1234:
 kasan_save_stack+0x26/0x50
 __kasan_kmalloc+0x84/0xa0
 mydev_init+0x3c/0x80 [mydev]
 do_one_initcall+0x84/0x2a0

The buggy address belongs to the object at ffff888103a4c000
 which belongs to the cache kmalloc-128 of size 128
The buggy address is located 64 bytes inside of
 128-byte region [ffff888103a4c000, ffff888103a4c080)

Memory state around the buggy address:
 ffff888103a4c000: 00 00 00 00 00 00 00 00
 ffff888103a4c040: 00 00 00 00 00 00 00 00
>ffff888103a4c040: 00[03]00 00 00 00 00 00
                      ^
 ffff888103a4c060: fc fc fc fc fc fc fc fc
==================================================================
```

注意報告說 `kmalloc-128` 而不是 `kmalloc-64`：`struct mydev_state` 大小是 `64 + 4 (int ref) + padding = ~72 bytes`，kmalloc 會向上取到 128-byte slab。這決定了實際的 OOB 寫入範圍（只有 128-64=64 bytes 的後半段是 OOB）。

### 4.3 Syzkaller crash entry

Syzkaller 在 `workdir/crashes/` 下會建立：

```
crashes/
  0000000000000001/           # crash signature 的 hash
    description               # "KASAN: slab-out-of-bounds in mydev_ioctl"
    log0                      # 完整 dmesg
    report0                   # 精簡報告
    repro.prog                # syzlang reproducer
    repro.cprog               # C reproducer（若 syz-repro 成功）
```

`repro.prog` 長相：

```
openat(0xffffffffffffff9c, &AUTO='/dev/mydev\x00', 0x0, 0x0)
ioctl$MYDEV_CMD_WRITE(r0, 0x40104d01, &AUTO={0x80, 0x0})
```

---

## 5. 卡住提示

**提示 1：ioctl number 的正確寫法**

syzlang 不認識 `_IOW` 宏，你必須用展開後的數值。如果你照上面 Python 算出 `0x40104d01`，description 裡寫：

```
ioctl$MYDEV_CMD_WRITE(fd fd_mydev, cmd const[0x40104d01], arg ptr[in, mydev_req])
```

如果你直接寫 `_IOW('M', 1, mydev_req)`，syzkaller 會報 parse error。先用 Python 或小 C 程式算清楚，再填進去。

**提示 2：`ptr[in]` vs `ptr[inout]` 的差異**

- `ptr[in, T]`：syscall 從 userspace 讀 T（kernel 是 consumer）
- `ptr[out, T]`：kernel 寫回 T 到 userspace（kernel 是 producer）
- `ptr[inout, T]`：雙向

`MYDEV_CMD_WRITE` 的 ioctl 只做 `copy_from_user`，kernel 不會寫回任何東西。用 `ptr[in, mydev_req]`。如果你寫 `inout`，syzkaller 不會報錯，但會多做沒必要的 mutation，浪費 coverage。

**提示 3：resource 繼承的寫法**

```
resource fd_mydev[fd]
```

這一行讓 syzkaller 知道 `fd_mydev` 是 `fd` 的子型別：它是個合法的 fd，但只用在 mydev 相關的 syscall。繼承保證了兩件事：首先，如果某個 syscall 需要 `fd`（泛型），syzkaller 可以用 `fd_mydev` 滿足它；其次，fuzzer 會在 `openat$mydev` 回傳後自動把 r0 當 `fd_mydev` 使用，確保先 open 再 ioctl 的順序。

如果你漏了這行直接寫 `fd` 作為參數型別，syzkaller 會亂傳任何 fd，ioctl 呼叫大多會吃到 EINVAL，永遠進不了有 bug 的路徑。

**提示 4：讓 fuzzer 覆蓋到 `idx >= 64`**

如果你把 `idx` 定義為 `flags[mydev_idx_vals]` 並且只列 0–63，fuzzer 只會傳安全的值，永遠找不到 bug。

正確做法：把 `idx` 定義為 `int32`，讓 syzkaller 的 mutation 自由決定數值。Syzkaller 的整數 mutation 預設會嘗試邊界值（0、1、-1、INT_MAX、隨機大數），很快就會覆蓋到 `>= 64`。

或者你也可以定義一個 flags 型別，但要故意包含越界值：

```
mydev_idx_flags = 0, 1, 63, 64, 127, 255, 0xffffffff
```

**提示 5：struct 的 padding 問題**

`struct mydev_req` 在 C ABI 下有 4 bytes padding（`__u32` 後面補齊讓 `__u64` 對齊）。syzlang 的 struct 定義不需要顯式寫 padding；syzkaller 會自動按 C ABI 對齊。如果你擔心對不對，可以加明確的 padding 欄位：

```
mydev_req {
    idx    int32
    pad    array[const[0, int8], 4]
    val    int64
}
```

但通常不必要，`int32` 後接 `int64` syzkaller 自動處理對齊。

---

## 6. 實作步驟

**步驟 1：計算 ioctl number**

在 WSL2 Ubuntu 上確認 `struct mydev_req` 的大小：

```bash
cat > /tmp/check_size.c << 'EOF'
#include <stdio.h>
#include <stdint.h>
#include <sys/ioctl.h>

struct mydev_req {
    uint32_t idx;
    uint64_t val;
};

int main(void) {
    printf("sizeof(mydev_req) = %zu\n", sizeof(struct mydev_req));

    /* 手動計算 _IOW('M', 1, struct mydev_req) */
    unsigned long nr =
        ((unsigned long)1             << 30) |   /* direction: write */
        (sizeof(struct mydev_req)     << 16) |   /* size */
        ((unsigned long)'M'           <<  8) |   /* type */
        ((unsigned long)1             <<  0);    /* nr */

    printf("MYDEV_CMD_WRITE = 0x%08lx\n", nr);
    return 0;
}
EOF
gcc /tmp/check_size.c -o /tmp/check_size && /tmp/check_size
# 期望輸出：
# sizeof(mydev_req) = 16
# MYDEV_CMD_WRITE = 0x40104d01
```

記下輸出的數值，這是 syzlang 裡 `cmd` 的 `const` 值。

**步驟 2：建立 syzlang description 骨架**

進入 syzkaller source 目錄，建立 description 檔：

```bash
cd ~/syzkaller
touch sys/linux/mydev.txt
```

先寫最小骨架再慢慢填細節：

```
# sys/linux/mydev.txt

resource fd_mydev[fd]

openat$mydev(fd const[AT_FDCWD], file ptr[in, string["/dev/mydev"]], \
    flags const[0], mode const[0]) fd_mydev

ioctl$MYDEV_CMD_WRITE(fd fd_mydev, cmd const[0x40104d01], \
    arg ptr[in, mydev_req])

mydev_req {
    idx    int32
    val    int64
}
```

**步驟 3：加入 include 並解決 AT_FDCWD**

syzkaller 有內建 `AT_FDCWD` 的常數定義（值為 `-100`，即 `0xffffffffffffff9c`），通常不需要額外 include。但若要用 `AT_FDCWD` 符號名稱，加：

```
include <uapi/linux/fcntl.h>
```

如果 module 有自己的 uapi header，也要在這裡引入。

**步驟 4：重新編譯 syzkaller 讓新 description 生效**

```bash
cd ~/syzkaller
make generate   # 解析 .txt，生成 Go binding
make            # 重新編譯所有工具
```

如果有 parse error，`make generate` 會直接報出哪一行有問題。常見錯誤：

- struct field 末尾多了逗號（syzlang 不用逗號分隔 field）
- const 數值前面忘記 `0x` 前綴
- `ptr[in mydev_req]` 漏了逗號（應該是 `ptr[in, mydev_req]`）

**步驟 5：用 `syz-prog2c` 驗證 description 能生成合法 C 程式**

```bash
# 手寫一個最簡單的 syz prog
cat > /tmp/test.prog << 'EOF'
r0 = openat$mydev(0xffffffffffffff9c, &AUTO='/dev/mydev\x00', 0x0, 0x0)
ioctl$MYDEV_CMD_WRITE(r0, 0x40104d01, &AUTO={0x80, 0x0})
EOF

./bin/syz-prog2c -prog /tmp/test.prog -enable=none
```

應輸出可編譯的 C 程式，包含正確的 `ioctl(fd, 0x40104d01, &req)` 呼叫。如果 prog2c 報錯「unknown syscall」，表示 `make generate` 沒成功或你的 description 有 parse error。

**步驟 6：在 KASAN kernel 上載入 module 並手動驗證**

本段未實測，為理論預期行為。

```bash
# 在 QEMU VM 內（已帶 CONFIG_KASAN=y 的 kernel）
insmod mydev.ko
ls -la /dev/mydev    # 確認 device 建立成功

# 手動觸發 OOB：idx=100 > 64
cat > /tmp/trigger.c << 'EOF'
#include <fcntl.h>
#include <sys/ioctl.h>
#include <stdint.h>
#include <stdio.h>

#define MYDEV_CMD_WRITE 0x40104d01UL

struct mydev_req {
    uint32_t idx;
    uint64_t val;
};

int main(void) {
    int fd = open("/dev/mydev", O_RDWR);
    if (fd < 0) { perror("open"); return 1; }

    struct mydev_req req = { .idx = 100, .val = 0x41 };
    int ret = ioctl(fd, MYDEV_CMD_WRITE, &req);
    printf("ioctl ret = %d\n", ret);
    return 0;
}
EOF
gcc /tmp/trigger.c -o /tmp/trigger && /tmp/trigger
# dmesg 應出現 KASAN: slab-out-of-bounds
```

**步驟 7：設定 syz-manager 並等待 crash**

本段未實測，為理論預期行為。

建立 config 檔 `mydev.cfg`：

```json
{
    "target": "linux/amd64",
    "http": "0.0.0.0:56741",
    "workdir": "/tmp/syzkaller-work",
    "kernel_obj": "/path/to/kernel/build",
    "kernel_src": "/path/to/kernel/src",
    "image": "/path/to/rootfs.img",
    "sshkey": "/path/to/id_rsa",
    "syzkaller": "/home/user/syzkaller",
    "procs": 8,
    "type": "qemu",
    "vm": {
        "count": 4,
        "kernel": "/path/to/bzImage",
        "cpu": 2,
        "mem": 2048
    }
}
```

啟動：

```bash
./bin/syz-manager -config mydev.cfg
# 瀏覽 http://localhost:56741 觀察 coverage 和 crash 數量
```

因為介面極簡（只有一個 ioctl），syzkaller 應在數分鐘內找到 crash。

---

## 7. 完整參考解答

<details>
<summary>展開查看完整 mydev.txt 與逐行解說</summary>

```
# sys/linux/mydev.txt
# Description for intentionally-buggy mydev char device
# Covers: open, ioctl MYDEV_CMD_WRITE (OOB write via unvalidated idx)

include <uapi/linux/fcntl.h>

# resource 定義：fd_mydev 繼承自 fd
# 這讓 syzkaller 知道這個 fd 是透過 openat$mydev 取得的，
# 並且之後的 ioctl$MYDEV_CMD_WRITE 要消耗同一個 fd。
# 若沒有這行，fuzzer 不知道 ioctl 的第一個參數從哪裡來。
resource fd_mydev[fd]

# openat$mydev：建立 fd_mydev resource 的唯一來源
# fd = AT_FDCWD (0xffffffffffffff9c) 是慣例寫法，表示相對 cwd
# file：固定字串 "/dev/mydev"，不要讓 fuzzer 亂改 path
# flags、mode 都 const[0]，因為這個 device 沒有 O_NONBLOCK 等特殊語意
# 回傳型別 fd_mydev 告訴 syzkaller 這個 syscall「產生」一個 fd_mydev
openat$mydev(fd const[AT_FDCWD], file ptr[in, string["/dev/mydev"]], flags const[0], mode const[0]) fd_mydev

# ioctl$MYDEV_CMD_WRITE：核心 syscall
# fd：消耗一個 fd_mydev（確保先 open 再 ioctl 的順序）
# cmd：固定為展開後的數值 0x40104d01
#       計算方式：_IOW('M'=0x4d, 1, sizeof(mydev_req)=16)
#       = (1<<30) | (16<<16) | (0x4d<<8) | 1
#       = 0x40000000 | 0x00100000 | 0x00004d00 | 0x00000001
#       = 0x40104d01
# arg：指向 mydev_req 結構的 in pointer（kernel 只讀不寫）
ioctl$MYDEV_CMD_WRITE(fd fd_mydev, cmd const[0x40104d01], arg ptr[in, mydev_req])

# mydev_req struct 定義
# idx：int32，讓 syzkaller 自由 mutation
#       syzkaller 預設的整數 mutation 會嘗試：
#       0, 1, -1, INT_MAX, INT_MIN, 隨機值
#       這樣 idx >= 64 的情況很快就會被覆蓋到
#
#       若寫成 flags[safe_vals] 並只列 0–63，
#       fuzzer 永遠看不到越界，bug 永遠找不到。
#
# val：int64，寫入 buf[idx] 時會截斷成 char，
#       數值本身不影響 crash 是否發生，
#       但不同值可能影響後續 heap corruption 的程度。
#
# 注意：struct 有 4 bytes 隱性 padding（idx 後、val 前），
# syzkaller 自動按 C ABI 對齊，不需要顯式寫 padding。
mydev_req {
    idx    int32
    val    int64
}
```

**關鍵設計決策整理：**

| 決策 | 選擇 | 原因 |
|------|------|------|
| `idx` 型別 | `int32`（自由 mutation） | 需覆蓋 `>= 64` 的越界值 |
| `val` 型別 | `int64` | 和 kernel struct 對齊，避免 EFAULT |
| `arg` 方向 | `ptr[in, ...]` | kernel 只 `copy_from_user`，不回寫 |
| `cmd` 值 | `const[0x40104d01]` | ioctl number 是固定的，不需 mutation |
| `openat` path | `string["/dev/mydev"]` | 固定 path，避免 fuzzer 亂試其他 device |

**為什麼 KASAN 報告說 kmalloc-128？**

`struct mydev_state` 的實際大小：`char buf[64]` + `int ref`（4 bytes）+ padding（4 bytes）= 72 bytes。kmalloc 向上取到最近的 power-of-two slab，就是 128 bytes。

所以雖然 buf 是 64 bytes，OOB 寫入進入的是 128-byte slab 的後半段（byte 64–127），KASAN 的 `fc` shadow byte 標記這段為「已分配但在 object 邊界之外」，一旦寫入就會觸發報警。

</details>

---

## 8. 測試用例表

| 測試案例 | idx 值 | 期望行為 | KASAN 報警 |
|----------|--------|----------|-----------|
| 正常邊界內 | 0 | 寫入 `buf[0]`，成功回傳 0 | 無 |
| 正常邊界內 | 63 | 寫入 `buf[63]`，成功回傳 0 | 無 |
| 恰好越界 | 64 | OOB write 1 byte，進入 ref 欄位 | 是：slab-out-of-bounds |
| 明顯越界 | 100 | OOB write，覆蓋 slab 後半段 | 是 |
| 接近 slab 邊界 | 127 | OOB write，接近 128-byte slab 末端 | 是 |
| 跨越 slab 邊界 | 128 | 可能進下一個 slab 或 GPF | 是（或 GPF） |
| 負值 (無符號解釋) | -1 (0xffffffff) | idx 被 cast 成超大正整數，距離 buf 極遠 | 是（可能 GPF） |
| val 為 0 | 64, val=0 | 寫入 null byte，仍是越界 | 是 |
| 大 val | 64, val=0xdeadbeef | 截斷後只有最低 byte 0xef 寫入 | 是 |

---

## 9. 延伸挑戰

**挑戰 1：為 BUG2（UAF）補 syzlang description**

BUG2 需要的攻擊序列：open → ioctl → close → 觸發 UAF。

設計 description 讓 fuzzer 能生成 close 後再呼叫 ioctl 的序列。提示：syzkaller 對 resource 的生命週期有特殊支援。研究 `close$mydev` syscall 應該怎麼描述，以及 fuzzer 如何在 close 後重用已失效的 fd（故意製造 UAF）。

**挑戰 2：動態大小的 buf**

把 module 改成 buf 大小由另一個 ioctl 設定（`MYDEV_CMD_SETSIZE`），OOB 條件從 `idx >= 64` 變成 `idx >= dynamic_size`。

設計 description 處理這個有狀態的介面：fuzzer 需要先呼叫 SETSIZE，然後用超過 size 的 idx 呼叫 WRITE。提示：研究 syzlang 的 `len` 型別和如何讓兩個 syscall 之間的參數有依賴關係。

**挑戰 3：用 `syz-prog2c` 生成 C reproducer**

把下面的 syz prog 存成檔案，用 `syz-prog2c` 轉成 C：

```
r0 = openat$mydev(0xffffffffffffff9c, &AUTO='/dev/mydev\x00', 0x0, 0x0)
ioctl$MYDEV_CMD_WRITE(r0, 0x40104d01, &AUTO={0x80, 0x41})
```

觀察生成的 C 程式如何設定 struct 的記憶體佈局，確認 padding 位置對不對。然後手動編譯、在 KASAN kernel 上執行，對照 dmesg 確認 crash 地址與報告一致。

**挑戰 4：coverage-guided 觀察**

在 syzkaller config 裡打開 coverage，觀察 fuzzer 找到 OOB 需要多少次 syscall 執行。接著把 `idx` 型別從 `int32` 改成只列安全值的 `flags[mydev_safe_idx]`（0–63），比較兩種情況下 fuzzer 多久能找到 crash（或永遠找不到）。這個對比直接展示了 description 品質對 fuzzer 效率的影響。

---

## 10. 自我檢核

完成本練習後，確認你能回答以下問題：

- [ ] `_IOW('M', 1, struct mydev_req)` 展開後的數值是多少？你用什麼方法算出來的？
- [ ] syzlang 的 `resource fd_mydev[fd]` 這行不寫會發生什麼事？fuzzer 的行為如何改變？
- [ ] `ptr[in, mydev_req]` 和 `ptr[inout, mydev_req]` 的差別是什麼？在這個 ioctl 裡哪個正確？
- [ ] 如果你把 `idx` 定義為 `const[0]`，fuzzer 還能找到 OOB bug 嗎？為什麼？
- [ ] KASAN 報告裡的「buggy address belongs to cache kmalloc-128」這句話的意思是什麼？為什麼是 128 而不是 64？
- [ ] `repro.prog` 和 `repro.cprog` 分別是什麼格式？哪個更適合手動驗證？
- [ ] 如果 `make generate` 報 parse error，你會從哪裡開始 debug？
- [ ] 本練習的 module 為什麼用 `miscdevice` 而不是完整的 `cdev` + `class_create`？兩者對 syzlang description 的寫法有什麼影響？

---

*本練習屬於「Part 4：Syzkaller 深度實戰」，建議在讀完第 24–26 章（syzkaller 架構、syzlang、執行 syzkaller）後動手。syzlang 語法的完整參考在 syzkaller repo 的 `docs/syscall_descriptions.md` 和 `docs/syscall_descriptions_syntax.md`，遇到不確定的型別優先查這兩份文件。*
