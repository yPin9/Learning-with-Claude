# Ch 38 — char/misc device 深入

> **目標**：從 `open("/dev/foo")` 那一刻回推整條路徑——VFS 怎麼靠 major/minor number 找到你的 `cdev`、怎麼把你的 `file_operations` 掛上去；能親手寫出一個帶 `open`/`read`/`write`/`ioctl`/`poll` 的 char device 或 misc device 模組，從使用者空間讀寫它、發 ioctl、用 `poll` 阻塞等資料；並看懂為什麼 `ioctl` 既是驅動的萬用控制介面、也是 kernel LPE 最愛的入口。

Ch 37 我們看了 device model 那套 `kobject`/`sysfs`/`bus`/`driver` 骨架——那是「裝置在 kernel 裡怎麼被組織、怎麼在 `/sys` 現形」的**抽象層**。這一章下降到最具體的一種裝置：**char device（字元裝置）**。它是自訂驅動最常見的形態，也是「使用者空間拿一個 `/dev/xxx` 檔案 `read`/`write`/`ioctl`」這件事在 kernel 端的真正落點。Ch 33 我們建立了 VFS 的 `file_operations` 概念，Ch 34 走過一次 `read()` 的完整路徑；這一章把那條路徑接到**你自己寫的驅動**上。

## 為什麼需要這個？

Linux 有一句口號：**everything is a file**（你在 `linux_commands` 課的第一章就見過）。`cat /dev/urandom`、`echo x > /dev/ttyS0`、`dd if=/dev/sda`——這些「檔案」背後根本不是磁碟上的位元組，而是**驅動程式**。使用者空間用它熟悉的 `open`/`read`/`write`/`close` 介面去操作硬體或核心功能，不必知道底層是串口、亂數產生器還是 KVM。這個「把裝置包裝成檔案」的統一介面，是 Unix 最成功的設計之一。

問題來了：VFS 那套 `file_operations`（Ch 33）是給**檔案系統**用的——`ext4` 的 `read` 去讀 block、`tmpfs` 的 `read` 去讀 page cache。但 `/dev/foo` 不屬於任何檔案系統的資料，它的 `read` 應該跑**你的驅動程式碼**。VFS 怎麼知道「這個 inode 不是普通檔案，要把 `read` 轉給某個驅動」？答案就是 **char device 機制**：一個 inode 被標記成 character special file（`ls -l` 開頭那個 `c`），帶著一組 **major/minor number**，VFS 在 `open` 時用這組號碼去一張全域表裡查出對應的 `cdev`，把 `cdev` 的 `file_operations` 換上 `struct file`。之後所有 `read`/`write`/`ioctl` 就都打到你的驅動。

三大裝置類型先擺清楚：

| 類型 | `ls -l` 首字 | 存取模型 | 典型例子 | 本課章節 |
|---|---|---|---|---|
| **character（字元）** | `c` | 串流，一個 byte 接一個 byte，通常不可 seek | tty、串口、`/dev/null`、`/dev/urandom`、`/dev/kvm` | 本章 |
| **block（區塊）** | `b` | 固定大小 block、可隨機存取、走 page cache + block layer | `/dev/sda`、`/dev/nvme0n1` | Ch 36 |
| **network** | 無 `/dev` 節點 | 不走檔案介面，走 socket API | `eth0`、`lo` | Ch 43 |

network device 根本不在 `/dev` 底下（它走 socket，Ch 43 講），所以真正「長得像檔案」的自訂驅動絕大多數是 char device。block device 要接 block layer（Ch 36）那套 `bio`/`blk-mq`，複雜得多；char device 是**最簡單、最常見**的自訂驅動形態——你在 kernel_pwn 課裡打的那些 CTF 漏洞模組，幾乎清一色是 char device（開一個 `/dev/vuln`，`ioctl` 進去觸發 bug）。這就是為什麼這章要講透。

## 先建立直覺

先看清楚「`open("/dev/foo")` 到底發生什麼」。這是整章的心智模型：

```
使用者空間                     VFS 層                          你的驅動
─────────                    ────────                        ─────────
open("/dev/foo")
   │  syscall
   ▼
 do_sys_open ──► path_openat ──► 走到 /dev/foo 這個 inode
                                     │
                     這個 inode 是 character special file
                     （S_ISCHR），帶 i_rdev = MKDEV(major, minor)
                                     │
                     VFS 看到 S_ISCHR，呼叫 chrdev_open()
                                     │
                     用 major 去一張全域表 (cdev_map) 查
                     ──► 找到你 cdev_add() 註冊的那個 struct cdev
                                     │
                     把 file->f_op 換成 cdev->ops（＝你的 file_operations）
                                     │
                     呼叫 file->f_op->open()  ──────────────►  your_open()
                                                                   │
之後每個 read/write/ioctl                                          你在這裡
   file->f_op->read()  ──────────────────────────────────────►  your_read()
   file->f_op->unlocked_ioctl() ───────────────────────────────►  your_ioctl()
```

關鍵洞見：**`/dev/foo` 這個檔案節點裡幾乎沒有東西，只有一組 (major, minor)**。真正的驅動邏輯掛在 kernel 裡一個叫 `struct cdev` 的物件上，這個 cdev 透過 major number 被全域表索引。inode 只是一個「指路牌」，上面寫著 major/minor；VFS 照著指路牌去 kernel 內部找到真正幹活的 cdev。

再看 **major vs minor** 的分工：

```
dev_t（32-bit）
┌────────────────────────┬───────────────────────────────┐
│      major (12-bit)    │        minor (20-bit)          │
│  「哪個驅動」            │   「這個驅動管的第幾個裝置」      │
└────────────────────────┴───────────────────────────────┘

例：串口驅動 major=4
   /dev/ttyS0  →  (4, 64)   ─┐
   /dev/ttyS1  →  (4, 65)    ├─ 同一個驅動 (major=4)，
   /dev/ttyS2  →  (4, 66)   ─┘   minor 區分是哪一個實體串口
```

major 回答「這是哪一類裝置、由哪個驅動負責」，minor 回答「同一個驅動下的第幾個實體」。一個串口驅動註冊 major=4，然後用 minor 0/1/2… 區分主機板上第幾個串口。你在 `linux_commands` 課用 `ls -l /dev/` 看到的那些 `c 4, 64` 就是這組號碼。

> 用 `ls -l /dev/` 現在就能驗證：`crw-rw---- 1 root dialout 4, 64 ... /dev/ttyS0`——首字 `c`（char device）、`4, 64` 就是 (major, minor)。對照 block device：`brw-rw---- 1 root disk 8, 0 ... /dev/sda`，首字是 `b`、major=8（SCSI disk）。這是使用者空間唯一能直接看到的 char device 內部狀態。

## char device 機制：從配號到掛上 file_operations

寫一個 char driver，kernel 端有四步，缺一不可。我們一步步拆。

### 第一步：拿到裝置號（dev_t）

`dev_t`（定義在 `include/linux/types.h`）是一個 32-bit 整數，把 major 和 minor 打包在一起。操作它的巨集在 `include/linux/kdev_t.h`：

```c
dev_t dev = MKDEV(major, minor);   // 把 major/minor 組成 dev_t
unsigned int maj = MAJOR(dev);     // 取出 major
unsigned int min = MINOR(dev);     // 取出 minor
```

拿號有兩種方式，選錯是新手常見錯誤：

```c
// 方式 A：靜態指定 major（你自己挑一個號，要求 kernel 給你）
int register_chrdev_region(dev_t from, unsigned count, const char *name);

// 方式 B：讓 kernel 動態分配一個沒被用的 major（推薦）
int alloc_chrdev_region(dev_t *dev, unsigned baseminor,
                        unsigned count, const char *name);
```

兩者都在 `fs/char_dev.c`，底層都走 `__register_chrdev_region()`。差別：

- **方式 A（靜態）** 你硬挑一個 major（例如 240，屬於 `LOCAL/EXPERIMENTAL` 範圍）。風險是那個號可能已被別的驅動占用，`register_chrdev_region` 就回 `-EBUSY`。傳統驅動這樣做是因為要有固定號讓 `mknod` 腳本寫死。
- **方式 B（動態，推薦）** kernel 從高位往下找一個沒人用的 major 給你，透過 `dev` 參數回傳。**永遠優先用這個**——不會撞號。代價是 major 每次可能不同，所以你得從 `/proc/devices` 或 `/sys/class` 查出實際號碼才能建節點（後面會示範）。

`count` 是你要連續配幾個 minor。一個驅動管 4 個實體裝置就 `count=4`，minor 從 `baseminor` 起算。

`fs/char_dev.c` 裡維護的核心資料結構是 `struct char_device_struct`（每個註冊的 major 一個），它們掛在一個 hash 表 `chrdevs[]` 上，用 `major % 255` 當 hash key。`alloc_chrdev_region` 做的事就是：找一個空的 major，配一個 `char_device_struct` 掛進 `chrdevs[]`，記下 name 和 minor 範圍。

> 注意：`register_chrdev_region` **只是登記了「這組號碼歸我」**，還沒把任何 `file_operations` 綁上去。你現在有號了，但 `open("/dev/foo")` 還不會跑你的程式碼。綁定是下一步 `cdev_add` 幹的事。這個「配號」與「綁 fops」分兩步的設計，正是舊 API `register_chrdev()` 一步搞定、新 API 拆開的原因——拆開後你能精確控制 minor 範圍、也能對每個 minor 掛不同 fops。

### 第二步：準備 file_operations

這一步就是 Ch 33 的 `struct file_operations`（定義在 `include/linux/fs.h`），你填上你的驅動要支援哪些操作：

```c
static const struct file_operations foo_fops = {
    .owner          = THIS_MODULE,      // 防止模組被卸載時還有人開著檔案
    .open           = foo_open,
    .release        = foo_release,      // 對應使用者的 close()（最後一個 fd 關閉時才呼叫）
    .read           = foo_read,
    .write          = foo_write,
    .unlocked_ioctl = foo_ioctl,        // 現代 ioctl 入口（不是舊的 .ioctl）
    .poll           = foo_poll,         // 支援 select/poll/epoll
    .llseek         = noop_llseek,        // 串流裝置通常不支援 seek
};
```

`.owner = THIS_MODULE` 這行不能漏：它讓 kernel 在有人開著你的裝置時，把模組的 refcount 加一，`rmmod` 就會擋下來（回 `-EBUSY`）。漏了它，使用者開著 `/dev/foo` 你卻 `rmmod` 成功，之後那個 fd 的 `read` 會打到已被釋放的程式碼——use-after-free，kernel panic。

### 第三步：cdev_init + cdev_add，把 fops 綁到裝置號

`struct cdev`（定義在 `include/linux/cdev.h`）是「一個 char device 實例」的 kernel 內部代表。它把 `file_operations` 和一段 dev_t 範圍綁在一起：

```c
struct cdev {
    struct kobject kobj;              // 接 device model（Ch 37 的 kobject）
    struct module *owner;
    const struct file_operations *ops;  // 你的 fops
    struct list_head list;
    dev_t dev;                        // 起始裝置號
    unsigned int count;               // 管幾個 minor
};
```

綁定兩步：

```c
static struct cdev foo_cdev;

cdev_init(&foo_cdev, &foo_fops);    // 把 fops 塞進 cdev，初始化 kobject
foo_cdev.owner = THIS_MODULE;
int err = cdev_add(&foo_cdev, dev, count);   // 正式把 cdev 掛進全域表
```

`cdev_add`（`fs/char_dev.c`）是關鍵：它把你的 cdev 加進一個叫 `cdev_map` 的全域結構（型別 `struct kobj_map`，在 `drivers/base/map.c`）。這張表就是前面 ASCII 圖裡「用 major 去查」的那張表。從 `cdev_add` 回傳成功那一刻起，**任何人 `open` 一個 major/minor 落在你範圍內的 char special file，VFS 都會找到你的 cdev**。

### 第四步：/dev 節點怎麼來

現在 kernel 端全部就緒，但使用者空間還沒有 `/dev/foo` 這個檔案可以 open。節點的來源有三種歷史演進：

```
手動 (古老)          udev (傳統桌面)          devtmpfs (現代預設)
──────────           ──────────────           ──────────────────
你自己跑：            驅動送 uevent            kernel 自己在 devtmpfs
mknod /dev/foo       ──► udevd 收到           掛載時，只要驅動呼叫
   c <major> <minor> ──► 照規則跑             device_create()，節點
                         mknod                就自動出現在 /dev
需 root、易與         需 udevd daemon          不需 daemon，kernel
kernel 配號不同步      規則可自訂 名字/權限      內建，開機早期就有
```

現代做法是在驅動裡呼叫 `class_create()` + `device_create()`（`drivers/base/core.c`，這是 Ch 37 device model 的一部分）：

```c
static struct class *foo_class;

foo_class = class_create("foo");                    // 在 /sys/class/foo 建一個 class
device_create(foo_class, NULL, dev, NULL, "foo");   // 觸發 devtmpfs 自動建 /dev/foo
```

`device_create` 會產生一個 uevent，devtmpfs（kernel 內建的一個小 tmpfs，掛在 `/dev`）收到後**自動建立節點**，major/minor 直接取自你傳的 `dev`——不會像手動 `mknod` 那樣號碼寫錯。這也解決了動態 major 的問題：你不知道 kernel 給你哪個 major 沒關係，`device_create` 幫你把正確號碼寫進節點。

> 三種方式現在都還能運作，但**新驅動一律用 class_create + device_create**。手動 mknod 只在最小系統（像我們 Ch 0 的 busybox initramfs，沒跑 udev 也沒掛 devtmpfs）才需要——那時你得先從 `/proc/devices` 查出 kernel 配給你的 major，再 `mknod`。這個手動流程等下動手環節會用到，因為它逼你看清「配號」和「建節點」是兩件獨立的事。

## file_operations 逐個看：一個 char driver 要實作什麼

`open`/`release` 通常最簡單（配置/釋放 per-open 狀態）。真正有內容的是 `read`/`write`/`ioctl`/`poll`/`mmap`。逐個拆。

### read / write：copy_to_user / copy_from_user 的邊界

driver 的 `read` 要把資料從 kernel 空間搬到使用者傳進來的 buffer。**絕對不能直接 `memcpy`**——使用者傳進來的指標是使用者空間位址，可能是惡意的、可能指向沒映射的頁、可能指向 kernel 空間想騙你寫。必須用 Ch 4 講過的 `copy_to_user`/`copy_from_user`：

```c
static ssize_t foo_read(struct file *filp, char __user *buf,
                        size_t count, loff_t *ppos)
{
    struct foo_dev *dev = filp->private_data;
    size_t avail = dev->data_len - *ppos;
    size_t n = min(count, avail);

    if (n == 0)
        return 0;                         // 回 0 = EOF，read() 端看到讀完

    if (copy_to_user(buf, dev->buffer + *ppos, n))
        return -EFAULT;                   // 使用者指標壞掉，回 EFAULT

    *ppos += n;                           // 更新 offset
    return n;                             // 回實際搬了幾 byte（可能 < count）
}
```

幾個容易錯的點：

- **`char __user *buf`** 那個 `__user` 是給 sparse 靜態檢查器看的標記，提醒「這是使用者空間指標，碰它一定要走 copy_*」。你不能 deref 它。
- **`copy_to_user` 回傳「還沒搬完的 byte 數」**，成功時回 0。回非 0 代表使用者 buffer 有一段不可寫，慣例回 `-EFAULT`。
- **回傳值語意**：回 0 代表 EOF，回正數代表搬了幾 byte（可以少於 `count`，使用者的 `read` 會拿到部分資料再自己重試），回負數是 `-errno`。
- **`*ppos`** 是這個 open 的檔案 offset。純串流裝置（像串口）通常忽略它；有內部 buffer 的（像我們的範例）要維護它，不然重複讀會一直讀同一段。

`write` 對稱：用 `copy_from_user(dev->buffer + *ppos, buf, n)` 把資料從使用者搬進來。

### ioctl：char device 的萬用控制介面，也是最大攻擊面

`read`/`write` 只能搬「資料流」。但驅動常需要「控制指令」——設定串口鮑率、查詢裝置狀態、重置硬體。這些不適合塞進資料流，於是有了 **`ioctl`（I/O control）**：一個萬用的「對這個 fd 下命令」介面。

現代入口是 `.unlocked_ioctl`（舊的 `.ioctl` 早在 2.6.36 移除了，因為它持有 BKL）：

```c
static long foo_ioctl(struct file *filp, unsigned int cmd, unsigned long arg)
```

`cmd` 是命令號，`arg` 是一個泛用參數（通常是使用者空間某個 struct 的指標，強制轉成 `unsigned long`）。問題：`cmd` 這個數字誰定？如果隨便用 `0`、`1`、`2`，不同驅動會撞號，而且無法從 `cmd` 判斷 `arg` 該怎麼解讀。kernel 定了一套 **cmd 編碼規則**（`include/uapi/asm-generic/ioctl.h`），把「方向、type、序號、arg 大小」全編進那個 32-bit `cmd`：

```
ioctl cmd number（32-bit）
┌──────┬──────────────┬────────────┬──────────────┐
│ dir  │     size     │    type    │      nr      │
│ 2bit │    14bit     │    8bit    │     8bit     │
│讀/寫  │ arg 結構大小  │ 幻數(magic)│  這驅動第幾個 │
└──────┴──────────────┴────────────┴──────────────┘
```

你用四個巨集來產生 cmd（不要手寫數字）：

```c
#define FOO_MAGIC  'F'                        // 挑一個幻數當這驅動的 namespace

#define FOO_RESET     _IO(FOO_MAGIC, 0)               // 無 arg
#define FOO_GET_LEN   _IOR(FOO_MAGIC, 1, int)         // kernel → user 讀出一個 int
#define FOO_SET_LEN   _IOW(FOO_MAGIC, 2, int)         // user → kernel 寫入一個 int
#define FOO_XCHG      _IOWR(FOO_MAGIC, 3, struct foo_arg)  // 雙向
```

- `_IO`：沒有資料
- `_IOR`：使用者要**讀**（kernel 寫回 arg），第三參數是 arg 型別，巨集自動算 size 塞進 cmd
- `_IOW`：使用者要**寫**（kernel 讀 arg）
- `_IOWR`：雙向

`dir` 和 `size` 編進 cmd 的用意：驅動可以用 `_IOC_DIR(cmd)`、`_IOC_SIZE(cmd)` 反解出方向和大小，先驗證再處理，是一層防呆。實作：

```c
static long foo_ioctl(struct file *filp, unsigned int cmd, unsigned long arg)
{
    struct foo_dev *dev = filp->private_data;
    int val;

    if (_IOC_TYPE(cmd) != FOO_MAGIC)    // 不是給我的命令，拒絕
        return -ENOTTY;                 // ENOTTY 是 ioctl 的「不支援此命令」慣例

    switch (cmd) {
    case FOO_RESET:
        dev->data_len = 0;
        return 0;
    case FOO_GET_LEN:
        val = dev->data_len;
        if (copy_to_user((int __user *)arg, &val, sizeof(val)))
            return -EFAULT;
        return 0;
    case FOO_SET_LEN:
        if (copy_from_user(&val, (int __user *)arg, sizeof(val)))
            return -EFAULT;
        if (val < 0 || val > FOO_BUF_SIZE)   // ★ 邊界檢查，缺這行就是漏洞
            return -EINVAL;
        dev->data_len = val;
        return 0;
    default:
        return -ENOTTY;
    }
}
```

**為什麼 ioctl 是 kernel LPE（本地提權）最愛的入口**（接 kernel_pwn 課）：

1. **它是巨大的、非結構化的攻擊面**。一個驅動可能有幾十個 ioctl cmd，每個都收使用者傳進來的 `arg`。只要有一個 cmd 忘了驗證 `arg`（像上面那個 `FOO_SET_LEN` 若少了邊界檢查），使用者就能塞非法值——越界寫、整數溢位、type confusion。
2. **`arg` 常是使用者控制的指標**。驅動若把 `arg` 當成 kernel 指標用、或 `copy_from_user` 進來的長度沒驗證，就是經典的 heap overflow / OOB write。你在 kernel_pwn 打的 CTF 模組，`ioctl` 幾乎都是漏洞觸發點：`open("/dev/vuln")` → `ioctl(fd, EVIL_CMD, crafted_arg)` → 觸發 slub overflow → 打 KASLR/提權。
3. **稽核困難**。ioctl 的介面不像 syscall 有嚴格審查，各家驅動品質參差，是 CVE 大戶（GPU 驅動、Android vendor 驅動的 ioctl 提權 CVE 一大票）。

寫 ioctl 的鐵律：**每個 cmd 的 `arg` 都當成敵意輸入**，長度、範圍、指標全部驗證後才用。這正是你在 kernel_pwn 課從攻擊方看到的破綻，現在從防守方補起來。

### poll：怎麼支援 select / poll / epoll

使用者想「等這個裝置有資料再讀，沒資料時別忙等」——這就是 `select`/`poll`/`epoll`（你在 linux_commands / networking 課用過的多路複用）。驅動端要實作 `.poll`：

```c
static __poll_t foo_poll(struct file *filp, struct poll_table_struct *wait)
{
    struct foo_dev *dev = filp->private_data;
    __poll_t mask = 0;

    poll_wait(filp, &dev->read_wq, wait);    // 把自己掛上等待佇列（不會睡）

    if (dev->data_len > 0)
        mask |= EPOLLIN | EPOLLRDNORM;       // 有資料可讀
    if (dev->space_available)
        mask |= EPOLLOUT | EPOLLWRNORM;      // 有空間可寫

    return mask;                             // 回報現在的可讀/可寫狀態
}
```

`poll_wait` 的機制值得看清楚：它**不會阻塞**，只是把「這個 file 對應的 wait queue」登記給 poll 子系統。當 `poll()` 系統呼叫發現所有 fd 都還沒 ready，它會讓 process 睡在這些 wait queue 上；之後驅動裡一旦有資料，呼叫 `wake_up(&dev->read_wq)` 就會叫醒睡在上面的 process，`poll` 重新掃一遍 mask。這是 `poll` 高效的關鍵：process 不是輪詢，是**睡著等被叫醒**。`fs/select.c` 的 `do_poll()` 是這整套的驅動端。

### mmap：把裝置記憶體直接映射給使用者

有些裝置有大塊記憶體（framebuffer、DMA buffer），使用者若透過 `read`/`write` 一個 byte 一個 byte 搬會很慢。`.mmap` 讓使用者把裝置記憶體**直接映射進自己的位址空間**，之後讀寫就是普通 memory access，零 copy：

```c
static int foo_mmap(struct file *filp, struct vm_area_struct *vma)
{
    struct foo_dev *dev = filp->private_data;
    unsigned long pfn = virt_to_phys(dev->buffer) >> PAGE_SHIFT;

    return remap_pfn_range(vma, vma->vm_start, pfn,
                           vma->vm_end - vma->vm_start,
                           vma->vm_page_prot);
}
```

`remap_pfn_range`（`mm/memory.c`）把一段物理頁框（pfn）建立進使用者的 VMA（Ch 19 的 `vm_area_struct`）。之後使用者對那段映射的讀寫，MMU 直接落到裝置記憶體，不經過 syscall。mmap 的完整機制（page fault、`vm_operations_struct`、DMA 一致性）是 Ch 41 的主題，這裡先知道 char driver 可以提供它、以及它為什麼快（省掉 copy_to_user 的搬運）。

## misc device：char device 的一行封裝

寫上面那套 char device，你得：`alloc_chrdev_region` 配號、`cdev_init`/`cdev_add` 綁定、`class_create`/`device_create` 建節點、卸載時反著全部拆掉——樣板程式碼一大堆。如果你只是要**一個單一裝置**（不需要一堆 minor），這套顯得笨重。

**misc device**（`drivers/char/misc.c`）就是為此而生的簡化封裝。它的核心觀察：所有「單一、簡單」的 char device 其實可以**共用同一個 major——10**（`MISC_MAJOR`），彼此用 minor 區分。misc 子系統本身在初始化時 `register_chrdev(MISC_MAJOR, ...)` 一次性註冊了 major 10，之後每個 misc driver 只要**認領一個 minor + 給一組 fops**，不必自己碰 cdev、不必自己管 major。

註冊一行搞定：

```c
#include <linux/miscdevice.h>

static struct miscdevice foo_misc = {
    .minor = MISC_DYNAMIC_MINOR,     // 讓 misc 子系統自動配一個 minor
    .name  = "foo",                  // 節點名，會出現在 /dev/foo
    .fops  = &foo_fops,              // 你的 file_operations（跟 char device 一樣）
    .mode  = 0666,                   // /dev 節點權限
};

// 模組載入
misc_register(&foo_misc);            // 一行！配 minor + 建 /dev/foo 全包
// 模組卸載
misc_deregister(&foo_misc);
```

`misc_register`（`drivers/char/misc.c`）做了什麼：從一個 bitmap 配一個沒用的 minor（`MISC_DYNAMIC_MINOR` 時）、把你的 `miscdevice` 掛進 `misc_list`、呼叫 `device_create` 建 `/dev/foo` 節點。它內部**共用 misc 子系統早已註冊好的那個 cdev（major 10）**，misc 的 `misc_open()`（fops 掛在 major 10 的 cdev 上）在 open 時用 minor 去 `misc_list` 找到你的 `miscdevice`，再把 `file->f_op` 換成你的 fops——等於幫你做完 char device 那整套，你只出 minor 和 fops。

```
   char device（自己來）              misc device（misc 幫你）
   ──────────────────                ──────────────────────
   自己 alloc_chrdev_region          共用 major 10（misc 已註冊）
   自己 cdev_init + cdev_add         misc 內部共用一個 cdev
   自己 class_create + device_create misc_register 內部代勞
   自己配 minor 範圍                  MISC_DYNAMIC_MINOR 自動配
   ~30 行樣板                         ~1 行 misc_register
   → 適合：一個驅動管多個實體、        → 適合：單一、簡單裝置，
     要自訂 major、大量 minor           不想管 major
```

**何時用 misc**：你的驅動只有一個裝置節點、不需要自己控制 major、不需要幾百個 minor——這涵蓋了絕大多數自訂驅動。真實世界一大票東西是 misc device：`/dev/fuse`（FUSE 使用者空間檔案系統）、`/dev/kvm`（KVM 虛擬化）、`/dev/net/tun`（TUN/TAP，你在 networking 課建 VPN 用過）、`/dev/hwrng`、`/dev/vhost-net`……全都是 misc device。它們共用 major 10、各占一個 minor。用 `ls -l /dev/kvm` 會看到 `crw-rw-rw- 1 root root 10, 232`——major 10 就是 misc。

**何時不能用 misc**：你要管一堆實體裝置、要連續一大段 minor（如 tty 要幾百個）、要自訂 major（有 udev 規則寫死了號）——這些回去用完整的 cdev。

> 我的建議：**新驅動先用 misc device**。除非你明確需要多 minor 或自訂 major，否則 misc 省掉的樣板讓你專注在真正的驅動邏輯上，出錯面也小。等你真的需要 cdev 的彈性時，換過去不難——fops 完全一樣，差別只在註冊那幾行。

## 底層機制：wait queue 與阻塞 I/O

前面 `read` 那版有個問題：資料還沒來時就回 0（EOF）。但真實裝置（串口收字元、感測器等資料）常常是「**沒資料時該讓 read 睡著，等資料到了再醒來讀**」——這叫 blocking I/O，是 char device 的核心行為。實現它靠 **wait queue（等待佇列）**。

wait queue 的心智模型：

```
                    wait_queue_head_t read_wq
                    （一個「睡覺區」的入口）
                              │
  process A: read() 沒資料 ──►│ 把自己加進佇列，狀態設 INTERRUPTIBLE，
                              │ 呼叫 schedule() 讓出 CPU（睡著，不占 CPU）
  process B: read() 沒資料 ──►│ 同上，也睡在這
                              │
      ┌───────────────────────┘
      │
   某個 IRQ handler 或 write() 塞進資料後：
      wake_up_interruptible(&read_wq)
      ──► 把佇列上所有 process 標記 RUNNABLE，
          排程器擇機讓它們重跑 read()，這次有資料了
```

實作用 `wait_event_interruptible`（`include/linux/wait.h`），它把「檢查條件→不成立就睡→被叫醒後重新檢查」包成一個巨集：

```c
static ssize_t foo_read(struct file *filp, char __user *buf,
                        size_t count, loff_t *ppos)
{
    struct foo_dev *dev = filp->private_data;

    // 沒資料時睡，直到 data_len > 0（或被 signal 打斷）
    if (wait_event_interruptible(dev->read_wq, dev->data_len > 0))
        return -ERESTARTSYS;    // 被 signal 中斷，回這個讓 syscall 層決定重啟或回 EINTR

    // 醒來後這裡保證 data_len > 0，正常搬資料
    mutex_lock(&dev->lock);
    /* ... copy_to_user ... */
    mutex_unlock(&dev->lock);
    ...
}
```

對應的喚醒端（例如 `write` 塞入資料後）：

```c
static ssize_t foo_write(...)
{
    /* ... copy_from_user 進 dev->buffer，更新 dev->data_len ... */
    wake_up_interruptible(&dev->read_wq);   // 叫醒睡在 read 的人
    return n;
}
```

幾個關鍵，都接前面的章節：

- **`_interruptible` 版本**（接 Ch 9 的 task 狀態、Ch 26 的 sleep）讓 process 睡成 `TASK_INTERRUPTIBLE`——signal 可以打斷它。這很重要：使用者按 Ctrl-C 時，卡在 `read` 的 process 要能被 signal 叫醒退出，不然就是你在 `ps` 看到的 `D` 狀態（不可中斷睡眠，連 kill -9 都殺不掉）。**驅動裡的等待幾乎都要用 interruptible 版**。
- **`wait_event_interruptible` 回非 0 = 被 signal 打斷**，你要回 `-ERESTARTSYS`。這個特殊回傳值告訴 syscall 層「這個 syscall 被 signal 中斷了」，層會依 signal 設定決定自動重啟 syscall 或回使用者 `-EINTR`。直接回 `-EINTR` 也行但少了自動重啟的好處。
- **`wait_event_*` 的條件是重新檢查的**：巨集是個迴圈，被叫醒後會**再測一次條件**，不成立就繼續睡。所以「假喚醒」（spurious wakeup）或「多個 reader 搶一份資料」不會出錯——醒來發現條件不成立的那個會自己睡回去。這是它比手寫 `add_wait_queue`/`schedule` 安全的地方。
- **`wake_up` 和條件更新的順序、加鎖**：更新 `data_len` 和 `wake_up` 之間有 race 空間（Ch 24/28 的記憶體序）。實務上用一把 `mutex`（Ch 26）保護共享狀態，`wait_event` 的條件檢查放在鎖外（巨集自己處理），搬資料放鎖內。

> 非阻塞模式：如果使用者 `open` 時帶了 `O_NONBLOCK`，`read` 沒資料就該**立刻回 `-EAGAIN`** 而不是睡。標準寫法是先檢查 `filp->f_flags & O_NONBLOCK`，是的話沒資料就回 `-EAGAIN`，否則才 `wait_event_interruptible`。`poll`/`epoll` 的多路複用就是建立在非阻塞 + wait queue 上——`poll` 用 `poll_wait` 掛上同一個 `read_wq`，資料到了 `wake_up` 同時叫醒睡在 `read` 和睡在 `poll` 的人。

## 動手：寫一個完整的 misc device 模組

把上面全部串起來。這是一個「核心記事本」——內部一塊 buffer，使用者 `write` 存進去、`read` 取出、`ioctl` 查長度/清空、`poll` 等資料、`read` 在沒資料時阻塞。用 **misc device**（省樣板），fops 一應俱全。

```c
// notebook.c —— kernel_internals Ch 38：完整 misc char device
#include <linux/module.h>
#include <linux/miscdevice.h>
#include <linux/fs.h>
#include <linux/uaccess.h>      // copy_to_user / copy_from_user
#include <linux/mutex.h>
#include <linux/wait.h>
#include <linux/poll.h>
#include <linux/slab.h>

#define NB_BUF_SIZE 4096
#define NB_MAGIC 'N'
#define NB_RESET   _IO(NB_MAGIC, 0)             // 清空
#define NB_GET_LEN _IOR(NB_MAGIC, 1, int)       // 讀出目前長度

struct nb_dev {
    char *buffer;
    size_t data_len;                // 目前存了幾 byte
    struct mutex lock;              // 保護 buffer / data_len
    wait_queue_head_t read_wq;     // read 阻塞用
};

static struct nb_dev nb;            // 單一裝置，一個全域實例

static int nb_open(struct inode *inode, struct file *filp)
{
    filp->private_data = &nb;       // 把裝置狀態掛到這個 open 上（多裝置時用它區分）
    return 0;
}

static int nb_release(struct inode *inode, struct file *filp)
{
    return 0;                       // 這裡沒有 per-open 資源要釋放
}

static ssize_t nb_read(struct file *filp, char __user *buf,
                       size_t count, loff_t *ppos)
{
    struct nb_dev *d = filp->private_data;
    ssize_t ret;

    // 非阻塞模式：沒資料立刻回 EAGAIN
    if (d->data_len == 0 && (filp->f_flags & O_NONBLOCK))
        return -EAGAIN;

    // 阻塞模式：睡到有資料（signal 可打斷）
    if (wait_event_interruptible(d->read_wq, d->data_len > 0))
        return -ERESTARTSYS;

    mutex_lock(&d->lock);
    {
        size_t avail = d->data_len;
        size_t n = min(count, avail);
        if (copy_to_user(buf, d->buffer, n)) {
            ret = -EFAULT;
            goto out;
        }
        // 這個簡化模型：讀完就清掉（一次性取出），真實 FIFO 會搬剩下的
        d->data_len = 0;
        ret = n;
    }
out:
    mutex_unlock(&d->lock);
    return ret;
}

static ssize_t nb_write(struct file *filp, const char __user *buf,
                        size_t count, loff_t *ppos)
{
    struct nb_dev *d = filp->private_data;
    size_t n = min(count, (size_t)NB_BUF_SIZE);
    ssize_t ret;

    mutex_lock(&d->lock);
    if (copy_from_user(d->buffer, buf, n)) {
        ret = -EFAULT;
        goto out;
    }
    d->data_len = n;
    ret = n;
out:
    mutex_unlock(&d->lock);
    if (ret > 0)
        wake_up_interruptible(&d->read_wq);   // 叫醒睡在 read/poll 的人
    return ret;
}

static long nb_ioctl(struct file *filp, unsigned int cmd, unsigned long arg)
{
    struct nb_dev *d = filp->private_data;
    int val;

    if (_IOC_TYPE(cmd) != NB_MAGIC)
        return -ENOTTY;

    switch (cmd) {
    case NB_RESET:
        mutex_lock(&d->lock);
        d->data_len = 0;
        mutex_unlock(&d->lock);
        return 0;
    case NB_GET_LEN:
        val = (int)d->data_len;
        if (copy_to_user((int __user *)arg, &val, sizeof(val)))
            return -EFAULT;
        return 0;
    default:
        return -ENOTTY;             // ioctl 慣例：不認識的 cmd 回 ENOTTY
    }
}

static __poll_t nb_poll(struct file *filp, struct poll_table_struct *wait)
{
    struct nb_dev *d = filp->private_data;
    __poll_t mask = 0;

    poll_wait(filp, &d->read_wq, wait);        // 掛上等待佇列（不阻塞）
    if (d->data_len > 0)
        mask |= EPOLLIN | EPOLLRDNORM;         // 有資料可讀
    mask |= EPOLLOUT | EPOLLWRNORM;            // 永遠可寫（覆蓋式 buffer）
    return mask;
}

static const struct file_operations nb_fops = {
    .owner          = THIS_MODULE,
    .open           = nb_open,
    .release        = nb_release,
    .read           = nb_read,
    .write          = nb_write,
    .unlocked_ioctl = nb_ioctl,
    .poll           = nb_poll,
    .llseek         = noop_llseek,
};

static struct miscdevice nb_misc = {
    .minor = MISC_DYNAMIC_MINOR,
    .name  = "notebook",           // → /dev/notebook
    .fops  = &nb_fops,
    .mode  = 0666,
};

static int __init nb_init(void)
{
    int err;
    nb.buffer = kzalloc(NB_BUF_SIZE, GFP_KERNEL);   // Ch 6 的 kmalloc 家族
    if (!nb.buffer)
        return -ENOMEM;
    mutex_init(&nb.lock);
    init_waitqueue_head(&nb.read_wq);

    err = misc_register(&nb_misc);      // 一行：配 minor + 建 /dev/notebook
    if (err) {
        kfree(nb.buffer);
        return err;
    }
    pr_info("notebook: registered, minor=%d\n", nb_misc.minor);
    return 0;
}

static void __exit nb_exit(void)
{
    misc_deregister(&nb_misc);
    kfree(nb.buffer);
    pr_info("notebook: unregistered\n");
}

module_init(nb_init);
module_exit(nb_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("kernel_internals Ch38: a full misc char device");
```

編譯（Makefile 同 Ch 0），把 `notebook.ko` 放進 initramfs，在 QEMU 裡：

```sh
/ # insmod /notebook.ko
notebook: registered, minor=123

# 因為我們的 initramfs 有掛 devtmpfs（Ch 0 的 /init 做了 mount -t devtmpfs），
# misc_register 的 device_create 已自動建好 /dev/notebook。確認一下：
/ # ls -l /dev/notebook
crw-rw-rw-  1 0 0  10, 123  /dev/notebook       # major 10 = misc，minor 123

# 從使用者空間讀寫
/ # echo "hello kernel" > /dev/notebook
/ # cat /dev/notebook
hello kernel

# 看它在 kernel 的登記
/ # cat /proc/devices | grep -E "misc|10 "
 10 misc
/ # ls /sys/class/misc/notebook       # device model（Ch 37）幫我們建的 sysfs 節點
```

> 如果你的環境沒掛 devtmpfs（純手動 char device、非 misc），你得手動建節點：先 `cat /proc/devices` 找出 kernel 配給你的 major，再 `mknod /dev/foo c <major> 0`。這就是前面說的「配號與建節點是兩件事」的實際體現。misc device 因為走 `device_create` + devtmpfs，這步自動了。

發 ioctl / 用 poll 需要一支小的使用者空間程式（shell 發不了 ioctl）：

```c
// nb_ctl.c —— 使用者空間：發 ioctl、poll 等資料
#include <stdio.h>
#include <fcntl.h>
#include <sys/ioctl.h>
#include <poll.h>
#include <unistd.h>

#define NB_MAGIC 'N'
#define NB_RESET   _IO(NB_MAGIC, 0)
#define NB_GET_LEN _IOR(NB_MAGIC, 1, int)

int main(void)
{
    int fd = open("/dev/notebook", O_RDWR);
    int len;

    ioctl(fd, NB_GET_LEN, &len);        // 查目前長度
    printf("current len = %d\n", len);

    ioctl(fd, NB_RESET);                // 清空
    ioctl(fd, NB_GET_LEN, &len);
    printf("after reset len = %d\n", len);

    // poll：阻塞等資料可讀（另開一個 shell 對 /dev/notebook 寫東西就會醒）
    struct pollfd pfd = { .fd = fd, .events = POLLIN };
    printf("polling for data...\n");
    poll(&pfd, 1, -1);                  // -1 = 無限等
    if (pfd.revents & POLLIN)
        printf("data ready!\n");

    close(fd);
    return 0;
}
```

用 gdb 觀測（接 Ch 0 的手法）：QEMU 開 `-s`，host gdb `insmod` 後 `lx-symbols` 載入模組符號，`b nb_ioctl`，然後在 QEMU 跑 `nb_ctl`——gdb 會停在 `nb_ioctl`，`print cmd`、`print/x cmd` 你能看到 `_IOR('N',1,int)` 展開後的那個 32-bit 數字，用 `_IOC_TYPE`/`_IOC_NR` 反解驗證編碼。這把「ioctl cmd number 怎麼編碼」從紙上談兵變成你眼睛看到的數字。

## 對比與取捨

| 面向 | 完整 char device（cdev） | misc device |
|---|---|---|
| major | 自己配（`alloc_chrdev_region`） | 共用 10（`MISC_MAJOR`） |
| minor | 自己管一整段 | 認領一個（`MISC_DYNAMIC_MINOR` 自動配） |
| 註冊程式碼 | ~30 行（cdev + class + device） | ~1 行（`misc_register`） |
| /dev 節點 | 自己 `device_create` 或手動 mknod | `misc_register` 內部代勞 |
| 適合 | 多實體裝置、要連續 minor、自訂 major | 單一簡單裝置（絕大多數自訂驅動） |
| 真實例子 | tty（major 4，幾百 minor）、`/dev/mem` | `/dev/fuse`、`/dev/kvm`、`/dev/net/tun` |

| ioctl vs sysfs vs 新 syscall（驅動要暴露一個控制介面時選哪個） | |
|---|---|
| **ioctl** | 萬用、彈性高、能傳任意 struct；但非結構化、難稽核、是攻擊面大戶。適合複雜/二進位控制 |
| **sysfs 屬性**（Ch 37） | 一個屬性一個檔案，`echo`/`cat` 就能操作、易稽核、有型別；但只適合簡單純量、每次一個值 |
| **新 syscall**（Ch 4） | 全域介面、審查最嚴；但要動 syscall table、跨架構、進主線門檻極高。除非是通用機制不會為單一驅動加 |

現代的取捨：**簡單的設定/狀態用 sysfs（好稽核），複雜的二進位控制才用 ioctl**。歷史上很多該用 sysfs 的東西被塞進 ioctl，留下一堆攻擊面——這是 kernel 社群現在會 review 擋下的反模式。

## 踩雷集錦

1. **錯誤直覺：`register_chrdev_region` 之後 `open` 就會跑我的 read**。→ 正確：配號和綁 fops 是**兩件事**。只 `alloc_chrdev_region` 而沒 `cdev_add`，`open("/dev/foo")` 會回 `-ENODEV`（找得到 major 但沒 cdev）。必須 `cdev_add` 把 cdev 掛進 `cdev_map`，fops 才真的被掛上。misc device 之所以「一行搞定」是因為 `misc_register` 內部把這兩步都做了。

2. **錯誤直覺：驅動的 `read` 可以直接 deref 使用者傳進來的 `buf`**。→ 正確：`buf` 是使用者空間位址，直接 `memcpy`/deref 在開了 SMAP（Supervisor Mode Access Prevention）的機器上會立刻 fault、在沒開的機器上是安全漏洞（使用者能騙你讀寫任意位址）。**一律走 `copy_to_user`/`copy_from_user`**，回傳非 0 時回 `-EFAULT`。這是 Ch 4 講過的 user/kernel 邊界，在驅動裡最常被違反。

3. **錯誤直覺：ioctl 的 `arg` 是我自己編碼過的，可以信任**。→ 正確：`arg` 完全由使用者控制，`cmd` 也是。**每個 cmd 分支都要驗證 `arg` 的範圍/長度/指標**，例如 `FOO_SET_LEN` 少了 `val > BUF_SIZE` 的檢查就是越界寫漏洞。這是 kernel_pwn 課裡你打的那類 CVE 的源頭。連 `cmd` 本身都要先用 `_IOC_TYPE(cmd) != MAGIC` 過濾掉不屬於你的命令（回 `-ENOTTY`）。

4. **錯誤直覺：`read` 沒資料就回 0（EOF）**。→ 正確：回 0 是 EOF，會讓 `cat` 之類的程式**認為裝置讀完了直接結束**，而不是等更多資料。串流裝置沒資料時該用 `wait_event_interruptible` **阻塞等待**（或 `O_NONBLOCK` 時回 `-EAGAIN`），不是回 0。回 0 只在「這個裝置的資料真的到頭了」時用。

5. **錯誤直覺：wait 用 `wait_event`（不可中斷版）比較穩**。→ 正確：驅動裡的等待幾乎都要用 **`wait_event_interruptible`**。不可中斷版讓 process 睡成 `TASK_UNINTERRUPTIBLE`（`ps` 的 `D` 狀態），signal 打不斷、Ctrl-C 無效、連 `kill -9` 都殺不掉——使用者會以為卡死。你在 linux_commands 課看到的「D 狀態進程」很多就是驅動用錯了等待版本。只有「這個等待極短且絕不能被打斷」才用不可中斷版。

6. **錯誤直覺：模組卸載時只要 `cdev_del` 就乾淨了**。→ 正確：卸載要**反著把註冊時做的全拆掉**——`device_destroy`、`class_destroy`、`cdev_del`、`unregister_chrdev_region`，順序相反。漏一個會留下殭屍節點或 major 洩漏。而且 `.owner = THIS_MODULE` 沒設的話，使用者開著檔案時你還能 `rmmod`，之後那個 fd 的操作 use-after-free。misc device 這方面也簡單：`misc_deregister` 一行拆完。

## 進階：再往深一層

- **`file->private_data` 的正確用法**：多裝置（多 minor）時，`open` 的 `inode` 帶著 minor（`iminor(inode)`），你用它找到對應的裝置結構，存進 `filp->private_data`，之後 `read`/`write`/`ioctl` 從 `filp->private_data` 拿。這是 char driver 支援多實體的標準手法——一組 fops、靠 private_data 區分是哪個裝置。

- **`compat_ioctl`：32-bit 使用者跑在 64-bit kernel**。如果你的 ioctl `arg` struct 裡有指標或 `long`，32-bit 和 64-bit 的 struct 佈局不同（指標大小、對齊）。你得實作 `.compat_ioctl` 處理 32-bit 呼叫者，否則 `arg` 解讀錯位——這也是一類安全漏洞（compat 層的 CVE）。純量 arg（只有 `int`）通常 `.compat_ioctl = compat_ptr_ioctl` 就夠。

- **seqfile 與 `/proc` 介面**：除了 `/dev` 節點，驅動常在 `/proc` 或 `debugfs` 暴露狀態。`seq_file`（`fs/seq_file.c`）幫你處理「一次 read 讀不完、要分批」的分頁問題，比自己在 `read` 裡算 offset 安全得多。這是 bpf/observability 課裡你 `cat /proc/xxx` 看到的那些東西的產生端。

- **面試常問**：「char device 和 block device 差在哪」（存取模型串流 vs 隨機 block、走不走 page cache/block layer）、「ioctl 為什麼是安全問題」（非結構化攻擊面 + arg 驗證缺失）、「misc device 相對 char device 省了什麼」（共用 major 10、省 cdev 樣板）、「`read` 沒資料該回什麼」（阻塞或 `-EAGAIN`，不是 0）、「為什麼 `copy_to_user` 不能用 `memcpy` 取代」（user 指標 + SMAP + 安全）。這些是韌體/驅動職缺的高頻題。

- **接練習 A**：練習 A 你寫了第一個模組。那個模組如果當時做的是「核心記事本」，可以用本章這套 misc device 重寫——加上真正的 `read`/`write`/`ioctl`/`poll`，從「模組會 `pr_info`」升級到「使用者空間能 open/讀寫/控制的真裝置」。這是 char driver 從玩具到可用的分水嶺。

## 動手練習

1. **跑通完整流程**：編上面的 `notebook.ko` 和 `nb_ctl`，`insmod`，用 `echo`/`cat` 讀寫 `/dev/notebook`，用 `nb_ctl` 發 `NB_GET_LEN`/`NB_RESET`，確認每步都對。看 `ls -l /dev/notebook` 的 `10, <minor>`、`cat /proc/devices | grep misc`、`ls /sys/class/misc/`。

2. **驗證 ioctl 編碼**：在 `nb_ctl` 裡 `printf("%#x\n", NB_GET_LEN)` 印出展開的 cmd number，然後手算 `_IOR('N',1,sizeof(int))` 應該是多少（dir=讀=2、type='N'=0x4E、nr=1、size=4），對照。再用 gdb `b nb_ioctl`、`print/x cmd` 從 kernel 端看到同一個數字。

3. **弄壞它看阻塞**：把 `nb_read` 的 `wait_event_interruptible` 換成 `wait_event`（不可中斷版），`insmod`，`cat /dev/notebook`（此時 buffer 空、會阻塞）。另開 shell 試 `Ctrl-C` 和 `kill -9` 那個 `cat`——你會發現殺不掉（D 狀態）。這親手複現了踩雷 5。改回 interruptible，Ctrl-C 立刻生效。

4. **改成 char device（不用 misc）**：把 `misc_register` 那套換成 `alloc_chrdev_region` + `cdev_init`/`cdev_add` + `class_create`/`device_create`，卸載端對稱拆掉。對比行數，體會 misc 幫你省了什麼。故意漏掉 `cdev_add`，看 `open` 回 `-ENODEV`（驗證踩雷 1）。

5. **加一個危險 ioctl 再修好**：加一個 `NB_SET_LEN`（`_IOW`）讓使用者設 `data_len`，**故意不做邊界檢查**，然後 `ioctl(fd, NB_SET_LEN, &huge)` 設一個 > `NB_BUF_SIZE` 的值，再 `read`——觀察越界讀（配 KASAN，Ch 53，會直接抓到 out-of-bounds）。補上 `if (val > NB_BUF_SIZE) return -EINVAL;` 修好。這把 kernel_pwn 的攻擊視角和本章的防守視角合起來。

## 本章重點整理

- `open("/dev/foo")` 的路徑：inode 是 char special file（帶 major/minor）→ VFS 的 `chrdev_open` 用 major 去 `cdev_map` 查出你 `cdev_add` 的 cdev → 把 `file->f_op` 換成你的 `file_operations`。**inode 只是指路牌，驅動邏輯掛在 cdev 上**。
- char device 四步：`alloc_chrdev_region` 配號 → 填 `file_operations` → `cdev_init`+`cdev_add` 綁定 → `class_create`+`device_create` 建節點（走 devtmpfs 自動化）。**配號與綁 fops 是兩件獨立的事**。
- misc device 是 char device 的一行封裝：共用 major 10、`misc_register` 一次搞定配 minor + 建節點。單一簡單裝置（`/dev/fuse`、`/dev/kvm`、`/dev/net/tun`）都是它——新驅動優先用 misc。
- `read`/`write` 用 `copy_to_user`/`copy_from_user` 過 user/kernel 邊界；沒資料用 `wait_event_interruptible` 阻塞（不是回 0、不是用不可中斷版）；`poll` 靠 `poll_wait` 掛 wait queue 支援 epoll。
- **ioctl 是驅動的萬用控制介面，也是 kernel LPE 最大入口**——cmd 用 `_IOR`/`_IOW` 巨集編碼，每個 cmd 的 `arg` 都當敵意輸入驗證範圍/長度/指標，這是 kernel_pwn 攻擊面的防守側。

## 自我檢核

- [ ] 不看筆記，能畫出 `open("/dev/foo")` 從 syscall 到你的 `foo_open` 的完整路徑，並說出 major number 在哪一步被用來查表
- [ ] 能解釋 major 和 minor 各回答什麼問題，並從 `ls -l /dev/ttyS0` 的輸出讀出這兩個號
- [ ] 能說出 char device 完整四步、以及 misc device 幫你省掉哪幾步、為什麼能省（共用 major 10）
- [ ] 面試被問「ioctl 為什麼是安全問題」，你能講出「非結構化的大攻擊面 + arg 驗證缺失 + 使用者控制的指標/長度」，並舉一個具體的漏洞形態
- [ ] 能解釋 `read` 沒資料時該做什麼（阻塞 vs `-EAGAIN` vs 回 0 的差別），以及為什麼幾乎都用 `wait_event_interruptible` 而非不可中斷版
- [ ] 能獨立寫出一個帶 `open`/`read`/`write`/`ioctl`/`poll` 的 misc device 模組，並從使用者空間 open/讀寫/發 ioctl 驗證

## 延伸閱讀

### 官方文件

- **[Documentation/driver-api/basics.rst](https://www.kernel.org/doc/html/latest/driver-api/basics.html)** 及 driver-api 目錄
  - **讀哪裡**：char device、misc device 相關的 API 章節
  - **和本章關聯**：`cdev`/`register_chrdev_region`/`miscdevice` 的官方 API 說明，本章用到的函式簽名以這裡為準

- **[include/uapi/asm-generic/ioctl.h](https://elixir.bootlin.com/linux/v6.12/source/include/uapi/asm-generic/ioctl.h)** 與 `Documentation/userspace-api/ioctl/ioctl-number.rst`
  - **讀哪裡**：`_IO`/`_IOR`/`_IOW`/`_IOWR` 巨集定義，以及 ioctl-number.rst 那張「哪個 magic 幻數已被誰占用」的表
  - **為什麼讀**：寫 ioctl 前查這張表挑一個沒被占的 magic，避免撞號；看巨集定義才真懂 cmd number 的 32-bit 佈局

### 書籍

- **《Linux Device Drivers, 3rd Ed.》(LDD3)** — Corbet, Rubini, Kroah-Hartman（O'Reilly, 2005，全書免費線上）
  - **讀哪裡**：Ch 3「Char Drivers」整章、Ch 6「Advanced Char Driver Operations」（ioctl、blocking I/O、poll）
  - **定位**：char driver 的經典教科書，本章的骨架就是它的濃縮。**注意**：講的是 2.6，`.ioctl` 早改成 `.unlocked_ioctl`、`class_create` 簽名也變了，API 細節以本章的 6.12 為準，但設計思想歷久彌新
  - **前提**：會 C、跟過本課 Ch 33（VFS）與 Ch 0（模組工具鏈）

- **《Linux Kernel Development, 3rd Ed.》** — Robert Love
  - **讀哪裡**：VFS 與裝置模型相關章節，補本章沒展開的 kobject/sysfs 背景（Ch 37 已鋪）

### 文章 / 指南

- **[The Linux Kernel Module Programming Guide (LKMPG)](https://sysprog21.github.io/lkmpg/)** — sysprog21 維護
  - **讀哪裡**：「Character Device drivers」與「The `/proc` File System」章節
  - **為什麼讀**：目前維護最勤、對應新 kernel 的模組實作指南；本章 `notebook.ko` 的完整版可以在這裡找到更多 char device 範例與變體
  - **前提**：跟完本課 Ch 0 的工具鏈

- **[LWN.net 的 ioctl 安全相關文章](https://lwn.net/Kernel/Index/)**（在 index 搜 "ioctl"）
  - **讀哪裡**：關於 ioctl 攻擊面、compat_ioctl 漏洞、以及「該用 sysfs 還是 ioctl」的討論
  - **和本章關聯**：把本章「ioctl 是 LPE 入口」的論點放進真實 CVE 與 kernel 社群 review 的脈絡，是攻防雙視角的第一手材料

char device 是「把 kernel 功能包裝成 `/dev` 檔案」的最通用手法。但真實硬體驅動還要回答一個問題：**這個裝置在哪、kernel 怎麼知道它存在、怎麼把驅動和裝置配對**——尤其在沒有 PCI 列舉的嵌入式 SoC 上。下一章我們進 platform driver 與 device tree，看 kernel 怎麼從一棵描述硬體的樹裡認出裝置、觸發你的 `probe`，這正是 MTK 韌體與 ARM SoC 每天在打交道的機制。

→ [Ch 39 platform driver 與 device tree](./39-platform-driver-device-tree.md)
