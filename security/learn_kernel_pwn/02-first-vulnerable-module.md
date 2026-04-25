# Ch 2 — 第一個 vulnerable kernel module：file_operations、ioctl、copy_from_user

> 目標：自己寫出一個有洞的 char device driver — `misc_register` + `file_operations` + `ioctl` + `copy_from_user` 一次走完。這個模板接下來 15+ 章的每個練習題都會拿來改。

## 為什麼題目長成「一個 char device」

CTF kernel pwn 題 99% 都是一個 **可載入的 kernel module**，開機時 `insmod vuln.ko`，在 `/dev/` 下生成一個字元裝置，user-space 用 `open("/dev/vuln")` + `ioctl()` 去戳它。原因：

- **攻擊面小而精**：出題者只暴露自己寫的幾個 ioctl，不讓你打整個 kernel
- **洞位置明確**：lab 題的目的是練技巧，不是練找 bug
- **乾淨 scope**：`copy_from_user` 接進 kernel、`copy_to_user` 回 user，是最短的 round trip

真實 kernelCTF 的 bug 在更深的子系統（nf_tables、io_uring），但 trigger 路徑在 user-space 看**幾乎一模一樣** — syscall 丟進一個結構，kernel 解析，出事。所以這個模板一點都不 toy。

## `misc_device` vs `cdev`：選 `misc_device`

Linux 有兩套註冊 char device 的方式：

| 方式 | 優點 | 缺點 |
|---|---|---|
| `misc_register` | 一行搞定，自動分 minor、自動生 `/dev/<name>` | 一律 major 10 |
| `alloc_chrdev_region` + `cdev_init` | 自己控 major/minor、多個 device | 六七行 boilerplate |

CTF 題的所有名作（kpwn lab、pawnyable、LKGit）都用 `misc_register`。我們也用它。`cdev_init` 這條路知道存在就好。

## 最小 driver 骨架

建個乾淨目錄：

```bash
mkdir -p ~/kpwn/module/ch02-vuln
cd ~/kpwn/module/ch02-vuln
```

`vuln.c`：

```c
// SPDX-License-Identifier: GPL-2.0
#include <linux/module.h>
#include <linux/init.h>
#include <linux/fs.h>
#include <linux/miscdevice.h>
#include <linux/uaccess.h>
#include <linux/slab.h>

#define DEVICE_NAME "vuln"

/* ---- ioctl 指令編號 ---- */
#define VULN_ECHO  _IOW('v', 1, char[128])   /* user → kernel: 寫入 */
#define VULN_PEEK  _IOR('v', 2, char[128])   /* kernel → user: 讀回 */

/* kernel 裡保留最後一次 echo 的內容 */
static char kbuf[128];

/* ---- file_operations callback ---- */

static int vuln_open(struct inode *inode, struct file *filp) {
    pr_info("vuln: open by pid=%d\n", current->pid);
    return 0;
}

static int vuln_release(struct inode *inode, struct file *filp) {
    pr_info("vuln: close by pid=%d\n", current->pid);
    return 0;
}

static long vuln_ioctl(struct file *filp, unsigned int cmd, unsigned long arg) {
    char local[64];    /* 故意 64 字節，但底下拷 128 — 這就是洞 */
    void __user *uarg = (void __user *)arg;

    switch (cmd) {
    case VULN_ECHO:
        /* 洞點：沒檢查 size，固定拷 128 到 64 字節的 local */
        if (copy_from_user(local, uarg, 128))
            return -EFAULT;
        memcpy(kbuf, local, 128);
        pr_info("vuln: echo stored, first byte=0x%02x\n", (u8)local[0]);
        return 0;

    case VULN_PEEK:
        if (copy_to_user(uarg, kbuf, 128))
            return -EFAULT;
        return 0;

    default:
        return -ENOTTY;
    }
}

static const struct file_operations vuln_fops = {
    .owner          = THIS_MODULE,
    .open           = vuln_open,
    .release        = vuln_release,
    .unlocked_ioctl = vuln_ioctl,
    .compat_ioctl   = vuln_ioctl,   /* 32-bit compat 也指過來，省事 */
};

/* ---- 向 kernel 註冊 ---- */

static struct miscdevice vuln_misc = {
    .minor = MISC_DYNAMIC_MINOR,
    .name  = DEVICE_NAME,
    .fops  = &vuln_fops,
    .mode  = 0666,    /* 人人可讀寫，真實世界別這樣 */
};

static int __init vuln_init(void) {
    int rc = misc_register(&vuln_misc);
    if (rc)
        return rc;
    pr_info("vuln: /dev/%s ready (kbuf @ %px, vuln_ioctl @ %px)\n",
            DEVICE_NAME, kbuf, vuln_ioctl);
    return 0;
}

static void __exit vuln_exit(void) {
    misc_deregister(&vuln_misc);
    pr_info("vuln: unloaded\n");
}

module_init(vuln_init);
module_exit(vuln_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("kpwn ch02: first vulnerable module");
```

幾個要記的點：

- **`file_operations` 是 vtable**：kernel 的 VFS 看到 user 呼叫 `ioctl(fd, ...)`，就呼叫你註冊的 `.unlocked_ioctl`。`open`、`read`、`write`、`mmap`、`release` 同理。Ch 12 後半會看我們打 `->ops` hijack 就是打這張表。
- **`copy_from_user(dst, src, n)`** 傳 kernel 地址當 dst，user 地址當 src。**搞反**是 kernel CVE 的常見來源。
- **`__user` 註解**是 sparse 靜態檢查用的，沒它也能編。但寫上讓人一眼看出哪個指標是 untrusted。
- **`pr_info("...%px...", ptr)`**：`%px` 印真實地址，**不是** `%p`（`%p` 會 hash 起來保護 KASLR）。我們自己的 lab 用 `%px` 方便 debug；真實 kernel code 幾乎不這樣寫。

## `_IOW` / `_IOR` / `_IOWR` 宏

`_IOW('v', 1, char[128])` 展開成一個 32-bit int，編碼：

- 方向（R / W / RW / NONE）
- type（'v' 是我們自取的，避免跟別人撞）
- 編號（1, 2, ...）
- size（sizeof(type)）

kernel 不強制檢查方向與 size（`vuln_ioctl` 裡用 switch 拿到就用），但 `strace -e ioctl` 會根據編碼印得比較好看。

**建議：每個 module 固定一個 type byte**（像我們用 `'v'`），指令從 1 開始數。這樣 ioctl 編號撞車機率接近零，dmesg / strace 都可讀。

## Makefile — 後面每章都是這個模板

```bash
cat > Makefile <<'EOF'
obj-m += vuln.o

KDIR := $(HOME)/kpwn/kernel/linux-6.6.60
PWD  := $(shell pwd)

all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules

clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean
EOF

make
ls vuln.ko
```

之後每章只需要改 `obj-m` 的名字與 `.c` 檔。`KDIR` 永遠指 Ch 0 build 好的 source 樹。

## User-space client：觸發那個洞

`exploit/ch02/trigger.c`：

```c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/ioctl.h>

#define VULN_ECHO  _IOW('v', 1, char[128])
#define VULN_PEEK  _IOR('v', 2, char[128])

int main(void) {
    int fd = open("/dev/vuln", O_RDWR);
    if (fd < 0) { perror("open"); return 1; }

    /* 先送一段正常長度的內容進去，讓 kbuf 有東西 */
    char payload[128];
    memset(payload, 'A', sizeof(payload));
    payload[127] = 0;

    if (ioctl(fd, VULN_ECHO, payload) < 0) {
        perror("ioctl ECHO");
        return 1;
    }

    /* 讀回來確認 round trip 通了 */
    char back[128] = {0};
    if (ioctl(fd, VULN_PEEK, back) < 0) {
        perror("ioctl PEEK");
        return 1;
    }
    printf("peek first byte: 0x%02x len=%zu\n", (unsigned char)back[0], strlen(back));

    close(fd);
    return 0;
}
```

Static 編譯、丟進 initramfs：

```bash
mkdir -p ~/kpwn/exploit/ch02
# 把上面的 trigger.c 存到 ~/kpwn/exploit/ch02/trigger.c

gcc -static -O2 -o ~/kpwn/exploit/ch02/trigger ~/kpwn/exploit/ch02/trigger.c
cp ~/kpwn/module/ch02-vuln/vuln.ko ~/kpwn/initramfs/
cp ~/kpwn/exploit/ch02/trigger ~/kpwn/initramfs/home/user/
~/kpwn/scripts/make-initramfs.sh
~/kpwn/scripts/run.sh
```

## 在 guest 跑

```
/ # insmod /vuln.ko
[    3.123] vuln: /dev/vuln ready (kbuf @ ffffffffc0XXxxxx, vuln_ioctl @ ffffffffc0XXxxxx)

/ # /home/user/trigger
vuln: open by pid=XX
vuln: echo stored, first byte=0x41
peek first byte: 0x41 len=127
vuln: close by pid=XX
```

OK — round trip 通了。

## 現在故意觸發溢位

修改 `trigger.c`：`memset(payload, 0x42, 128)` 其實就是合法輸入（size 剛好 128）。真正的洞在**沒檢查 size**，但 kernel side 固定拷 128 不管 user buffer 多大。這在目前這版 module 裡拷的是固定 128，size 和 stack buffer (`local[64]`) 不匹配才是洞。

把 `VULN_ECHO` 的 payload 全填 `0x41`，實際上 `copy_from_user(local, uarg, 128)` 會把 64 字節的 `local` 往下覆蓋 64 字節 — 包括 canary、保存的 rbp、return address。

guest 裡再跑一次應該看到：

```
[   xx.xxx] Kernel panic - not syncing: stack-protector: Kernel stack is corrupted in: vuln_ioctl+0x??
[   xx.xxx] CPU: 0 PID: xx Comm: trigger Tainted: ...
[   xx.xxx] Call Trace:
...
```

**這就是 stack canary（`CONFIG_STACKPROTECTOR=y` 預設開著）在救命**。kernel 發現函式結尾 canary 值跟入口時不一樣，直接 `panic`，避免 return address 控制權落到你手上。

panic 完 QEMU 會因為 `panic=1` 重啟。

## 為什麼還沒拿到 shell

你剛才的溢位在 user-space 眼裡看等於 `return -EFAULT` 都沒有 — kernel 直接倒在地上。Ch 4 會講怎麼：

1. leak 出當前 task 的 canary（通常靠另一個 info leak primitive）
2. 把 canary 寫回 overflow payload 正確的 offset
3. 精確控 return address 跳去我們要的地方
4. 然後遇到 SMEP、再來 Ch 5

這章只是**把整條訊息管道打通**。從「C 檔案 → .ko → insmod → /dev/vuln → user-space ioctl → copy_from_user → 觸發崩潰」這十步路，後面每一題都重複。打錯任一步都卡。

## 常見踩雷

**`insmod: ERROR: could not insert module: Invalid module format`** — 用 host kernel header 編的，不是 `~/kpwn/kernel/linux-6.6.60`。檢查 `KDIR`。

**`make` 報 `implicit declaration of function 'copy_from_user'`** — 缺 `#include <linux/uaccess.h>`。

**ioctl 回 `-ENOTTY`** — 你的 `_IOW` / `_IOR` 編碼和 user-space 對不上，或 switch 裡漏 case。用 `strace ./trigger` 確認 ioctl cmd 數值兩邊一致。

**`copy_from_user` 回非 0** — user 傳的指標是壞的（NULL、未 map、user 看不見）。gdb 進去看 `arg` 是什麼值。

**panic log 沒看到就重啟** — `-append` 裡 `quiet` 把 log 壓了。debug 時改成 `loglevel=7`，panic log 會狂噴。

**`/dev/vuln` 權限不對** — 沒加 `.mode = 0666`。真實世界別設 `0666`，lab 方便。

**module unload 後 `insmod` 再次報 "File exists"** — 你用 `rmmod vuln` 而不是 `rmmod /vuln.ko`（busybox 接 module 名而非路徑）。

## 模板收納

現在你手上有兩個將會永遠用下去的模板：

```
~/kpwn/module/chXX-<name>/
    ├── <name>.c      ← 有漏洞的 kernel module
    └── Makefile      ← obj-m += <name>.o

~/kpwn/exploit/chXX/
    └── <exp>.c       ← user-space exploit，gcc -static 編
```

後續每章都遵循這個檔名與路徑慣例。

## 動手練習

1. **加一個 `VULN_WIPE` ioctl**，把 `kbuf` 清零。驗證 `VULN_PEEK` 讀回全 0。熟悉新增 ioctl 的流程。
2. **加 `.read` callback**，讓 `cat /dev/vuln` 能讀出 `kbuf`。（hint：`simple_read_from_buffer`。）你會看到一個檔案原語能接進來的另一條路徑。
3. **故意傳一個壞指標** — `ioctl(fd, VULN_PEEK, (void*)0x1)`，看 `copy_to_user` 回什麼、dmesg 噴什麼。這是未來 SMAP 章（Ch 5）會用的直覺。
4. **把 canary 關掉再試**：build kernel 時 `./scripts/config --disable STACKPROTECTOR_STRONG` 並 `--disable STACKPROTECTOR`，重 build、重開。這次跑 trigger 看 dmesg — 應該是 `general protection fault` 或 `unable to handle kernel paging request`，而不是 canary panic。記住這個差異，Ch 4 要用。

## 自我檢核

- [ ] 能從空目錄寫出一個帶 ioctl 的 misc_device，`insmod` 後 `/dev/xxx` 出現
- [ ] 知道 `_IOW` / `_IOR` / `_IOWR` 各編碼什麼、怎麼挑 type byte
- [ ] 理解 `copy_from_user(dst, src, n)` 參數順序，知道搞反會怎樣
- [ ] 能解釋為什麼這個 overflow 觸發了 `stack-protector` panic 而不是乾脆跳到我們的 payload
- [ ] module 目錄與 exploit 目錄的檔名慣例已經記起來
- [ ] 知道 `%px` 和 `%p` 的差別（我們 lab 用 `%px`，真實 code 用 `%p`）

通了這章你就有「自己出題」的能力 — 要練什麼漏洞型態就自己寫 module。下一章我們轉方向，不寫 code，把 SLUB allocator 拆透：`kmalloc(32)` 究竟從哪個 slab 拿 object、freelist 怎麼串、為什麼 free 過的 object 地址可以被別的 allocation 立刻拿回去。這是 Part 3 heap 戰場的地基。

→ [Ch 3 — SLUB Allocator：kmalloc-N cache、freelist、object 生命週期](./03-slub-allocator.md)
