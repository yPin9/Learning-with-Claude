# Ch 5 — 第一個 vulnerable kernel module：file_operations、ioctl、建自己的靶

> **目標**：寫出全書的「靶原型」——一個完整、可編譯、可 `insmod`、故意帶一個 stack buffer overflow 的 misc character device。學完這章，你要能從零手刻一個 kernel module、建出對應的 `/dev` 節點、用 user-space 程式觸發漏洞、在 `dmesg` 裡讀懂崩潰訊息。後面每一章的 vulnerable module 都是這個骨架的變體——理解這一個，後面省掉大量重複解釋。

> **環境**：Linux 6.6，x86-64，沿用 Ch 0 建好的 QEMU + initramfs 環境。本章所有程式碼片段**未在真實環境編譯執行**——這是理論預期行為,寫法依據 6.6 原始碼樹的 `include/linux/miscdevice.h`、`include/linux/fs.h` 公開 API 簽名與長期穩定的 driver 撰寫慣例推斷。請在你自己的 Ch 0 環境裡實際 `make` 這個 module、`insmod` 它、跑 user-space 觸發程式，核對本章描述的每一個 `dmesg` 輸出是否與實際一致。

## 為什麼需要這一章？

前四章建立了環境、方法論、kernel 執行環境規則、user/kernel boundary 的概念。這一章把它們全部兜起來，產出第一個**真正能跑**的東西——一個你自己寫、自己編、自己打的 kernel module。

這個 module 的設計目標不是「示範一個真實世界常見的 bug 類型」（那是 Ch 11 開始的事），是**建立一個全書通用的骨架**：怎麼註冊一個裝置、怎麼接 `file_operations`、怎麼處理 ioctl、怎麼用 `copy_from_user` 接 user-space 資料——把這些「管線」寫對、寫熟，之後每一章要示範不同的漏洞類型（UAF、race condition、heap overflow……）時，只需要改動「漏洞本身那幾行」，不需要每次重新解釋整套管線怎麼接。

## 先建立直覺：一個 kernel module 和 user-space 程式的差異

```
User-space 程式：
┌──────────────┐
│  main() ───────▶ 從這裡開始執行
│  ...          │
│  exit()       │  跑完就結束,OS 收回所有資源
└──────────────┘

Kernel module：
┌──────────────────────────────────┐
│  module_init(my_init)  ──▶ insmod 時執行一次,
│                             「登記」自己(註冊裝置、
│                             配置資源),然後立刻返回
│                                    │
│                                    │  module 載入後,
│                                    │  「活」在 kernel 裡,
│                                    │  等待外部觸發
│                                    ▼
│                           file_operations 裡的
│                           open/read/write/ioctl
│                           ← 每次 user-space 呼叫對應
│                             的 syscall,就執行一次
│                                    │
│  module_exit(my_exit)  ──▶ rmmod 時執行一次,
│                             「註銷」自己(釋放資源)
└──────────────────────────────────┘
```

Kernel module 沒有一個會持續跑的 `main()`——`module_init` 標記的函式只在載入那一刻跑一次，跑完立刻返回（它的工作是**登記**，不是**執行主迴圈**）。之後所有的「活動」，都是外部（某個 process 呼叫 `open`/`ioctl`/`read`/`write`）觸發進來的,module 本身安靜地等著被呼叫。這個「被動、等待觸發」的模型，正是為什麼本書反覆用「vulnerable module + user-space 觸發程式」這個配對結構。

## `file_operations`：裝置和 syscall 的接口

User-space 程式用標準的 `open()`/`read()`/`write()`/`ioctl()`/`close()` 操作一個裝置節點（像 `/dev/vulndevice`）時，kernel 要知道「這些呼叫實際上該執行哪段程式碼」——這個對應關係就是 `struct file_operations`，一個函式指標表：

```c
struct file_operations {
        ...
        ssize_t (*read) (struct file *, char __user *, size_t, loff_t *);
        ssize_t (*write) (struct file *, const char __user *, size_t, loff_t *);
        long (*unlocked_ioctl) (struct file *, unsigned int, unsigned long);
        int (*open) (struct inode *, struct file *);
        int (*release) (struct inode *, struct file *);
        ...
};
```

（這是簡化版,真實的 `struct file_operations` 定義在 `include/linux/fs.h`，欄位遠多於這幾個——`llseek`、`mmap`、`poll`、`fsync` 等。本章 module 只填我們需要的五個。）

你的 module 要做的事，就是提供自己的版本的這幾個函式，塞進一個 `file_operations` 結構，然後**註冊**它——告訴 kernel「以後有人對這個特定裝置節點呼叫 `read`，就執行我這個函式」。

## 用 `misc_register` 建一個最小裝置

Kernel 裡有好幾種註冊裝置的方式（`register_chrdev`、`cdev_add` 等更底層的 API，Ch 2 查過的 `misc_devices.html` 文件有說明），對教學/CTF 用途最方便的是 **misc device** 框架——`misc_register` 自動幫你處理 major number 分配、`/dev` 節點建立（配合 `devtmpfs`）這些瑣碎細節,你只需要給一個名字和 `file_operations`：

```c
#include <linux/miscdevice.h>

static struct miscdevice vuln_miscdev = {
        .minor = MISC_DYNAMIC_MINOR,   /* 讓 kernel 自動分配一個 minor number */
        .name  = "vulndevice",         /* 對應 /dev/vulndevice */
        .fops  = &vuln_fops,           /* 我們自己填的 file_operations */
};
```

`misc_register(&vuln_miscdev)` 執行成功後，kernel 會（在 `devtmpfs` 掛載的前提下,我們 Ch 0 的 `init` 腳本已經 `mount -t devtmpfs`）自動建出 `/dev/vulndevice` 這個節點，user-space 的 `open("/dev/vulndevice", ...)` 就能找到它。

## 完整 module 原始碼

把前面幾章的規則（`copy_from_user`、GFP flag、goto 清理階梯、pr_info/pr_err 系列）全部套進來，寫出完整的 `vulndevice.c`：

```c
/* vulndevice.c
 *
 * Ch 5 的教學用 vulnerable misc device。
 * 故意在 ioctl 的 WRITE 路徑放一個 stack buffer overflow：
 * 固定大小的 stack buffer，但 copy_from_user 的長度直接信任
 * user-space 傳進來的 size，沒有做上限檢查。
 *
 * 僅供在你自己的 QEMU 受控環境中學習、實驗用。
 */
#define pr_fmt(fmt) "vulndevice: " fmt

#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/fs.h>
#include <linux/miscdevice.h>
#include <linux/uaccess.h>
#include <linux/slab.h>

#define VULN_MAGIC 'v'
#define VULN_IOCTL_WRITE _IOW(VULN_MAGIC, 1, struct vuln_req)
#define VULN_IOCTL_READ  _IOR(VULN_MAGIC, 2, struct vuln_req)

struct vuln_req {
        unsigned long size;
        char __user *buf;
};

/* 這個固定大小的 stack buffer,是這一章故意放的洞 */
#define STACK_BUF_SIZE 64

static long vuln_do_write(struct vuln_req *req)
{
        char stack_buf[STACK_BUF_SIZE];

        pr_info("write request: size=%lu\n", req->size);

        /* 故意的漏洞：
         * 這裡「應該」檢查 req->size <= sizeof(stack_buf)，
         * 但我們沒有檢查，直接把 user 指定的長度丟給 copy_from_user。
         * 如果 req->size > STACK_BUF_SIZE，就是一個經典的
         * stack buffer overflow —— Ch 11 會詳細打它。
         */
        if (copy_from_user(stack_buf, req->buf, req->size)) {
                pr_err("copy_from_user failed\n");
                return -EFAULT;
        }

        pr_info("write ok, first byte = 0x%02x\n", (unsigned char)stack_buf[0]);
        return 0;
}

static long vuln_do_read(struct vuln_req *req)
{
        char stack_buf[STACK_BUF_SIZE] = "hello from vulndevice kernel stack\n";
        size_t to_copy = min_t(unsigned long, req->size, sizeof(stack_buf));

        if (copy_to_user(req->buf, stack_buf, to_copy)) {
                pr_err("copy_to_user failed\n");
                return -EFAULT;
        }
        return 0;
}

static long vuln_ioctl(struct file *filp, unsigned int cmd, unsigned long arg)
{
        struct vuln_req req;
        long ret;

        /* 先把 user 傳進來的 struct vuln_req 本身複製進 kernel，
         * 這一步的大小是固定的 sizeof(req)，沒有問題。
         * 真正的洞在於「req.size」這個欄位之後怎麼被使用。
         */
        if (copy_from_user(&req, (void __user *)arg, sizeof(req)))
                return -EFAULT;

        switch (cmd) {
        case VULN_IOCTL_WRITE:
                ret = vuln_do_write(&req);
                break;
        case VULN_IOCTL_READ:
                ret = vuln_do_read(&req);
                break;
        default:
                pr_err("unknown ioctl cmd: 0x%x\n", cmd);
                return -ENOTTY;
        }

        return ret;
}

static int vuln_open(struct inode *inode, struct file *filp)
{
        pr_info("device opened\n");
        return 0;
}

static int vuln_release(struct inode *inode, struct file *filp)
{
        pr_info("device closed\n");
        return 0;
}

static const struct file_operations vuln_fops = {
        .owner          = THIS_MODULE,
        .open           = vuln_open,
        .release        = vuln_release,
        .unlocked_ioctl = vuln_ioctl,
};

static struct miscdevice vuln_miscdev = {
        .minor = MISC_DYNAMIC_MINOR,
        .name  = "vulndevice",
        .fops  = &vuln_fops,
        .mode  = 0666,          /* 任何本機 user 都能 open,對應 Ch 1 的 local LPE 威脅模型 */
};

static int __init vuln_init(void)
{
        int ret = misc_register(&vuln_miscdev);

        if (ret) {
                pr_err("misc_register failed: %d\n", ret);
                return ret;
        }

        pr_info("loaded, /dev/%s ready\n", vuln_miscdev.name);
        return 0;
}

static void __exit vuln_exit(void)
{
        misc_deregister(&vuln_miscdev);
        pr_info("unloaded\n");
}

module_init(vuln_init);
module_exit(vuln_exit);

MODULE_LICENSE("GPL");
MODULE_AUTHOR("kernel_pwn course");
MODULE_DESCRIPTION("Ch5 teaching vulnerable misc device (controlled lab use only)");
```

### 拆解幾個關鍵決策

- **`.mode = 0666`**：故意讓任何本機 user（不只是 root）都能 `open` 這個裝置——這對應 Ch 1 講的 local LPE 威脅模型：一個無特權 user 透過這個 driver 的漏洞,目標是提權到 root。真實世界的 driver 權限設計錯誤（本該限制、卻開成任何人可存取）本身就是一類漏洞（Ch 10 的邊界不清類別）。
- **`unlocked_ioctl` 而不是舊版 `ioctl`**：6.6（以及長期以來的現代 kernel）已經移除了舊式帶 big kernel lock 的 `ioctl` 欄位，一律用 `unlocked_ioctl`（不自動持有任何鎖，driver 自己決定要不要加鎖——這個決定本身也是 Ch 39/40 race condition 漏洞的常見來源：忘記自己該加的鎖）。
- **`struct vuln_req` 先整個 `copy_from_user` 一次**：這是常見、正確的模式——先把一個固定大小的「描述用」結構搬進 kernel，再根據裡面的欄位（這裡是 `size`）決定後續怎麼處理更大、長度可變的資料。漏洞**不在**這一步（這裡大小固定、正確），而在 `vuln_do_write` 拿到 `req.size` 之後,**沒有驗證它不超過 `STACK_BUF_SIZE`** 就直接傳給第二個 `copy_from_user`。

### 這個漏洞的性質：預告 Ch 11

這是一個經典的 **stack buffer overflow**——固定大小的 stack 區域（`stack_buf[64]`），被一個攻擊者完全控制、沒有上限檢查的長度參數（`req.size`）寫入。如果 `req.size` 遠大於 64，`copy_from_user` 會乖乖把超出 `stack_buf` 邊界的資料繼續往更高位址寫——覆寫 `vuln_do_write` 這個函式 stack frame 裡 `stack_buf`之後的記憶體（可能是其他局部變數、saved 暫存器、最終是 return address）。這正是 Ch 11 要完整打穿的目標。本章的任務只是把這個洞**建出來**、**確認它存在且能被觸發**,不要求你在這章就打穿它。

## Makefile（Kbuild）

Kernel module 不是用一般的 `gcc` 單檔編譯——它需要走 kernel 自己的 build 系統（Kbuild），因為編譯 module 需要用**和目標 kernel 完全一致**的編譯選項、header、符號版本資訊：

```makefile
obj-m += vulndevice.o

KDIR ?= /path/to/linux-6.6

all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules

clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean
```

把 `KDIR` 改成你 Ch 0 建好的 kernel 原始碼樹路徑（已經 `make -j$(nproc)` 編過一次,這個步驟會留下 `Module.symvers`、`.config` 等 Kbuild 需要的中介檔）。`obj-m += vulndevice.o` 告訴 Kbuild「把 `vulndevice.c` 編成一個 module（`.ko` 檔）」而不是編進 kernel 本體（那是 `obj-y`，給真正要內建的子系統用）。

編譯：

```bash
make
# 產出 vulndevice.ko
```

> **未實測，理論預期行為**：這份 Makefile 遵循 kernel module out-of-tree 編譯的長期標準慣例（`Documentation/kbuild/modules.rst` 描述的模式），但實際編譯結果依你的 `KDIR` 路徑、kernel 版本、`.config` 是否與編譯 module 時的環境一致而定——如果 `insmod` 時出現 "module compiled for different kernel" 一類的錯誤，通常代表 `KDIR` 指向的樹和實際跑在 QEMU 裡的那顆 kernel 不是同一次編譯產物，兩者必須完全對應。

## 把 module 放進 initramfs，載入、觸發、看 log

### 放進 rootfs

回到 Ch 0 打包 initramfs 的流程，把編好的 `vulndevice.ko` 放進去：

```bash
cp vulndevice.ko rootfs/root/
```

（假設 `rootfs/root/` 目錄已存在；需要時先 `mkdir -p rootfs/root`。）

### 在 `init` 腳本加載入指令

Ch 0 的 `init` 範本留了一行占位的 `# insmod /root/vuln.ko`，現在填上：

```sh
insmod /root/vulndevice.ko
```

重新打包 initramfs（`build_rootfs.sh`），重開 QEMU。

### insmod / rmmod / dmesg 流程

如果你的 `init` 腳本沒有自動載入（或者你想手動練習完整流程），在 QEMU 開機後的 shell 裡：

```sh
insmod /root/vulndevice.ko
dmesg | tail -5
# 預期看到:
# vulndevice: loaded, /dev/vulndevice ready

ls -l /dev/vulndevice
# 預期看到一個字元裝置節點,crw-rw-rw- 權限對應 mode=0666

rmmod vulndevice
dmesg | tail -3
# 預期看到:
# vulndevice: unloaded
```

> **未實測，理論預期行為**：以上 `dmesg` 輸出格式依照 `pr_fmt` 定義的前綴（`vulndevice: `）與 `pr_info` 呼叫內容推斷,實際輸出可能多帶時間戳記（例如 `[   12.345678] vulndevice: loaded, ...`，取決於你的 `console_loglevel` 與 `-append` 裡是否有 `quiet` 之類的參數）。請在你自己的環境跑一次,核對完整輸出格式。

## user 端觸發程式：open + ioctl

寫一支完整、靜態連結的 C 程式，從 user-space 觸發這個 module 的 `read`/`write` 路徑：

```c
/* trigger.c
 *
 * 觸發 vulndevice 的 ioctl 介面。
 * 用 -static 編譯，避免 initramfs 裡缺動態連結的 libc。
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/ioctl.h>

struct vuln_req {
        unsigned long size;
        char *buf;
};

#define VULN_MAGIC 'v'
#define VULN_IOCTL_WRITE _IOW(VULN_MAGIC, 1, struct vuln_req)
#define VULN_IOCTL_READ  _IOR(VULN_MAGIC, 2, struct vuln_req)

int main(int argc, char **argv)
{
        int fd;
        struct vuln_req req;
        char payload[64] = "hello from userspace\n";
        char readback[128] = {0};

        fd = open("/dev/vulndevice", O_RDWR);
        if (fd < 0) {
                perror("open");
                return 1;
        }

        /* 正常路徑:size 不超過 64,不觸發漏洞 */
        req.size = strlen(payload);
        req.buf = payload;
        if (ioctl(fd, VULN_IOCTL_WRITE, &req) < 0) {
                perror("ioctl WRITE (normal)");
        } else {
                printf("normal write ok\n");
        }

        req.size = sizeof(readback);
        req.buf = readback;
        if (ioctl(fd, VULN_IOCTL_READ, &req) < 0) {
                perror("ioctl READ");
        } else {
                printf("read back: %s", readback);
        }

        close(fd);
        return 0;
}
```

編譯（在 host 上用 kernel pwn 常用的靜態連結方式,因為 initramfs 裡沒有動態連結的 libc,見 Ch 0 踩雷集錦第 1 條）：

```bash
gcc -static -o trigger trigger.c
cp trigger rootfs/root/
```

重新打包 initramfs、重開 QEMU，在跑起來的 shell 裡：

```sh
/root/trigger
# 預期輸出：
# normal write ok
# read back: hello from vulndevice kernel stack
```

`dmesg` 應該同時看到對應的 `pr_info` 輸出（`write request: size=21`、`write ok, first byte = ...`）。

## 看 crash：故意觸發 overflow

把 `trigger.c` 複製一份改成故意觸發漏洞的版本（或者加一個命令列參數切換），把 `req.size` 設成遠大於 64：

```c
char big_payload[512];
memset(big_payload, 'A', sizeof(big_payload));

req.size = sizeof(big_payload);     /* 512 > STACK_BUF_SIZE(64),觸發 overflow */
req.buf = big_payload;
ioctl(fd, VULN_IOCTL_WRITE, &req);
```

**未實測，理論預期行為**——這一步的確切結果高度依賴你的 kernel 編譯設定：

- 如果開著 `CONFIG_STACKPROTECTOR`（stack canary，Ch 0 的 config 建議先關掉,但後面 Ch 11 會故意開回來對比），overflow 到一定程度覆寫了 canary，函式返回時 `__stack_chk_fail` 會被觸發，kernel 會印出類似 `Kernel panic - not syncing: stack-protector: kernel stack is corrupted` 的訊息並 panic（配合 Ch 0 的 `oops=panic` 設定）。
- 如果關著 stack protector，overflow 直接覆寫 saved return address，函式返回時 CPU 會嘗試跳到被覆寫成垃圾值（這裡是重複的 `0x41414141...`，也就是 `'A'` 的 ASCII 值）的位址，觸發 **page fault**（因為 `0x4141414141414141` 幾乎不可能是一個合法映射的 kernel 位址），印出類似 `BUG: unable to handle page fault for address: 0x4141414141414141` 加一段 **oops**（kernel 對這類錯誤的標準錯誤報告格式，包含暫存器狀態、呼叫堆疊）。

這個「512 bytes 的 `'A'` 導致 crash 在一個看起來像 `0x41414141...` 的位址」的現象，是 kernel pwn（和 user-space pwn）共通的除錯直覺起點——crash 位址裡認得出 payload 的 pattern，就知道「問題確實出在我控制的那段資料上」。Ch 11 會把這個現在只是「讓它崩潰」的 overflow，升級成精確控制 return address 的完整利用。

## 對比與取捨

| 設計選擇 | 這裡的做法 | 替代方案 | 為什麼選這個 |
|---|---|---|---|
| 裝置註冊方式 | `misc_register`（misc device 框架） | `register_chrdev` + 手動管理 major/minor、`cdev_add` | misc 框架省掉大量瑣碎的裝置號碼管理，適合教學/CTF；真實世界大型 driver（網卡、儲存裝置）常用更底層的 API 是因為需要更細的控制 |
| 觸發介面 | `ioctl` | `read`/`write` 搭配自訂協定、`/proc`/`sysfs` 介面、netlink | `ioctl` 能傳結構化資料、容易擴充多個命令,也貼近真實世界大量 LPE CVE 的觸發路徑 |
| 漏洞位置 | stack buffer，固定大小 | heap buffer（之後 Ch 17 開始的主題） | Part 0 先用最簡單、最直覺的 stack overflow 建立骨架；heap 版本的複雜度（SLUB 行為）留給 Part 1/3 專門處理 |

## 踩雷集錦

1. **錯誤直覺：「kernel module 寫完直接 `gcc vulndevice.c -o vulndevice.ko` 就能編。」** 正確認識：kernel module 必須透過 Kbuild（走 `make -C <kernel源碼樹> M=<module目錄> modules`），因為它需要用和目標 kernel 完全匹配的編譯旗標、header path、以及符號版本資訊（`Module.symvers`）——直接用一般 `gcc` 編譯出的東西不是合法的 `.ko`，`insmod` 會直接拒絕載入。

2. **錯誤直覺：「module 裡的 `pr_info`/`printk` 會立刻顯示在我的終端上。」** 正確認識：`printk` 系列函式寫進 kernel 內部的 ring buffer，要不要同時顯示在 console 上取決於目前的 console log level 設定與你的 `-append` cmdline（例如 `loglevel=` 參數、是否有 `quiet`）。看不到預期的輸出時，先用 `dmesg` 主動讀 ring buffer，不要假設它會自動印出來。

3. **錯誤直覺：「user-space 觸發程式隨便 `gcc trigger.c -o trigger` 編一編塞進 initramfs 就能跑。」** 正確認識：initramfs 裡的最小 rootfs 沒有完整的動態連結 libc（Ch 0 已經點過這個雷），一般動態連結編譯出的執行檔會在執行時找不到 `.so` 直接失敗。本章和之後所有章節的 user-space 觸發程式一律用 `gcc -static` 編譯。

4. **錯誤直覺：「`unlocked_ioctl` 這個名字代表它『不需要鎖』，所以不用擔心並發問題。」** 正確認識：`unlocked_ioctl` 的「unlocked」指的是 kernel **不會自動替你持有**舊版 `ioctl` 曾經隨附的 big kernel lock（一個已經被移除的全域鎖機制）——它完全不代表「這個函式不需要任何鎖保護」。如果你的 ioctl handler 操作了可能被多個 thread 同時存取的共享狀態,你自己要負責加鎖，忘記加正是 Ch 40 race condition 漏洞的常見來源。

5. **錯誤直覺：「觸發 overflow 之後 kernel 一定會立刻 panic，看到 panic 訊息就代表漏洞被正確觸發了。」** 正確認識：overflow 覆寫的內容如果**沒有**精確命中會被用到的敏感資料（例如只覆寫了一個之後不會被讀取的局部變數），kernel 可能完全沒有任何異常反應，函式正常返回,你會誤以為「這個 overflow 沒有效果」。能不能立刻看到 crash,取決於 overflow 的長度有沒有長到命中 saved return address 或其他關鍵資料——這也是為什麼本章範例故意用 512 bytes（遠超過 64）確保一定命中,之後 Ch 11 會教你怎麼精確計算需要覆寫多少 bytes 才會命中特定目標。

## 進階：再往深一層

- **把這個骨架擴充成支援多個「漏洞模式」的 module**，用一個額外的 ioctl 命令切換（例如正常模式 / overflow 模式 / UAF 模式），這樣後面幾章可以沿用同一支 module 檔案,只是切換行為,減少每章重新編譯/打包的負擔——這是本書建議但不強制的工程化做法，Ch 17 開始會示範一個更完整的「多模式教學靶」版本。
- **研究 `file_operations` 裡本章沒用到的其他欄位**（`mmap`、`poll`、`llseek`），這些欄位本身是真實世界 driver 漏洞的另一個常見來源（尤其 `mmap` 處理錯誤曾是多個真實 CVE 的根源）——本書 Part 5 談具體子系統時會回頭談這些介面。
- **比較 `misc_register` 和更底層的 `cdev_add`**：讀一次 `drivers/char/misc.c` 裡 `misc_register` 的實作（Ch 2 的方法論練習），看它內部怎麼呼叫更底層的 `cdev_add`、怎麼處理 minor number 分配,理解「方便的框架」底下實際發生了什麼。

## 動手練習

1. 完整跑一次本章流程：編譯 module → 打包進 initramfs → QEMU 開機 → `insmod` → `/root/trigger`（正常路徑）→ 確認 `dmesg` 輸出符合預期。
2. 跑故意觸發 overflow 的版本，記錄 `dmesg` 裡完整的 crash/oops 輸出,嘗試從輸出中找出：crash 發生在哪個函式附近、崩潰位址是不是看起來像你的 payload pattern。
3. 把 `STACK_BUF_SIZE` 改成 128，重新編譯、重新測試正常路徑與 overflow 路徑，觀察需要多大的 `req.size` 才會重新觸發 crash——驗證你對「overflow 長度要蓋到關鍵資料才會有效果」這個踩雷點的理解。
4. 在開著 `CONFIG_STACKPROTECTOR_STRONG` 的 kernel 上重跑一次 overflow 測試（回頭調整 Ch 0 的 config，重編 kernel），對比 `dmesg` 輸出和關掉 stack protector 時的差異。

## 本章重點整理

- Kernel module 沒有持續執行的 `main()`——`module_init`/`module_exit` 只在載入/卸載時各跑一次，真正的行為透過 `file_operations` 的函式指標表,被外部 `open`/`read`/`write`/`ioctl` 呼叫觸發。
- `misc_register` 是教學/CTF 場景最方便的裝置註冊方式,自動處理 minor number 與 `/dev` 節點建立。
- ioctl handler 裡,第一層 `copy_from_user`（把固定大小的描述結構搬進 kernel）正確,不代表第二層根據結構裡欄位值做的操作安全——本章的漏洞正是「描述裡的 `size` 欄位沒有上限檢查」就直接餵給第二個 `copy_from_user`。
- user-space 觸發程式一律 `gcc -static` 編譯；kernel module 一律走 Kbuild（`make -C <kernel source> M=<module dir> modules`）編譯,不能用一般 `gcc` 單檔編譯。

## 自我檢核

- [ ] 不看筆記，能說出一個 kernel module 從 `insmod` 到準備好接收 ioctl 要經過哪些步驟
- [ ] 能解釋為什麼本章範例的漏洞不在第一個 `copy_from_user`（複製 `struct vuln_req`），而在第二個（複製 payload）
- [ ] 能說出 `misc_register` 幫你處理了哪些瑣碎細節,換成 `register_chrdev` 你需要自己多做什麼
- [ ] 能解釋為什麼 user-space 觸發程式要用 `-static` 編譯,而不是一般動態連結編譯

## 延伸閱讀

### 官方文件 / Spec

- **[Miscellaneous Devices (driver-api, v6.6)](https://kernel.org/doc/html/v6.6/driver-api/misc_devices.html)**
  - **讀哪裡**：整頁都短，直接通讀
  - **和本章的關聯**：`misc_register`/`misc_deregister` 的正式 API 文件,本章範例直接基於它

- **[Documentation/kbuild/modules.rst](https://www.kernel.org/doc/html/latest/kbuild/modules.rst.html)**
  - **讀哪裡**：「Building External Modules」一節
  - **和本章的關聯**：本章的 Makefile 直接對應這份文件描述的 out-of-tree module 編譯標準流程,遇到編譯問題回來查

### 部落格 / 技術文章

- **[The Linux Kernel Module Programming Guide — Character Device Files](https://sysprog21.github.io/lkmpg/#character-device-drivers)**
  - **這篇說什麼**：從最基本的字元裝置註冊，到 `file_operations` 各欄位逐一說明
  - **讀哪裡**：Character Device Drivers 整章
  - **為什麼值得讀**：持續維護、範例完整可編譯,是本章內容的直接延伸讀物，涵蓋了本章刻意省略的其他 `file_operations` 欄位

- **[pawnyable — Linux Kernel 系列，環境與第一個 module](https://pawnyable.cafe/linux-kernel/)**
  - **這篇說什麼**：用近似的教學靶設計思路（vulnerable misc device + user-space 觸發程式），走過環境搭建到第一個漏洞
  - **讀哪裡**：系列開頭的環境與基礎章節
  - **前提知識**：日文/部分有英文翻譯，搭配機器翻譯閱讀；本書後續多個 Part 的靶題設計理念參考自這個系列,提前熟悉它的風格對讀後面章節有幫助

這個 vulnerable module 骨架是全書的起點靶。Part 1 接下來要往下挖一層——理解 kernel 的記憶體管理全貌，尤其是 SLUB allocator 的內部運作,這是看懂後面所有 heap 利用技術（Part 3 的核心戰場）的地基。

→ [Ch 6 kernel 記憶體全景](./06-kernel-memory-layout.md)
