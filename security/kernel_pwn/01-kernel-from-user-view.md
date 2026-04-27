# Ch 1 — Linux kernel 從 user 視角：syscall、user/kernel 切換、address space

> 目標：把「user 程式呼叫一個 syscall → kernel 端怎麼接 → 怎麼回去」整條路畫成一張你能在腦袋裡重播的地圖。後面講 SMAP、KPTI、ret2usr、`swapgs_restore_regs_and_return_to_usermode` 這些名字時，你知道它們各自卡在哪個節點上。

## 為什麼不能跳過

user-space pwn 做久了，對 kernel 的心智模型常常是「某個黑盒，syscall 是 RPC」。這模型在寫 CTF 題時夠用，但打 kernel pwn 不行：

- 你要知道 `copy_from_user` 為什麼會觸發 SMAP 檢查
- 你要知道 ROP 最後為什麼**不能直接** `ret` 回 user-space 某個地址（沒 `swapgs` / 沒 cr3 切換 / `cs` 錯）
- 你要知道為什麼 leak 一個 `ffffffff81xxxxxx` 的地址就等於 leak kernel text base

這些都站在這章要建的地圖上。不建，後面 exploit 你能抄作業但改不動。

## 全貌：一次 syscall 的生命

user 程式呼叫 `getuid()` 到回來，x86-64 上大致是這樣：

```
user-space (ring 3)
    mov rax, 102         ; __NR_getuid
    syscall              ; ← 這條指令的一瞬間發生很多事
    ─────────────────────────────────────────
    │ CPU 硬體動作（atomic）：
    │   rcx ← rip
    │   r11 ← rflags
    │   rip ← MSR_LSTAR   ; 跳到 entry_SYSCALL_64
    │   cs  ← MSR_STAR.syscall_cs   ; ring 3 → ring 0
    │   ss  ← ...
    │   （rsp 還是 user 的！）
    ↓
kernel-space (ring 0)
    entry_SYSCALL_64:      ; asm
        swapgs             ; GS 指向 per-CPU 區
        mov rsp, PER_CPU(cpu_current_top_of_stack)  ; 切 kernel stack
        push ...           ; 組 pt_regs
        call do_syscall_64 ; C 世界
            sys_getuid()
            return ret;
        ; 回到 asm
        pop ...            ; 還 pt_regs
        swapgs
        sysretq            ; ring 0 → ring 3，rip ← rcx, rflags ← r11
    ─────────────────────────────────────────
user-space 繼續跑
```

三個重點要記：

1. **`syscall` 指令切的是 `cs`/`rip`，不切 `rsp`** — entry code 第一件事就是手動把 rsp 切到 kernel stack，所以前幾行 kernel asm 還是在 user rsp 上跑的，**踩這幾行是 race condition 的高發地**。
2. **`swapgs` 是 gs 段基址的交換**，不是什麼魔法。用途是讓 kernel 在「不能信任任何 user 暫存器」的前提下，快速拿到 per-CPU 變數位置。
3. **回去時 `sysretq` 把 rcx 放回 rip** — 這也是為什麼 kernel ROP 結束要能自己控 rcx + r11 才回得來（Ch 7 KPTI 會深入）。

## `syscall` 指令到底做什麼

Intel SDM 文件講得像天書，實際上就六件事：

```
SYSCALL:
    RCX    ← RIP           ; 保存 user return address
    R11    ← RFLAGS        ; 保存 user flags
    RIP    ← MSR_LSTAR     ; 跳到 kernel entry
    RFLAGS &= ~MSR_FMASK   ; 清掉某些 flag（例如 IF 關中斷）
    CS     ← MSR_STAR[47:32]  ; 新 cs（ring 0）
    SS     ← MSR_STAR[47:32] + 8
```

注意：

- **MSR_LSTAR** 在 boot 時被設成 `entry_SYSCALL_64` 的地址。每個 CPU 都是這個值。leak 這個 MSR → 知道 kernel text base。
- **cr3 沒換**。開 KPTI 的話 cr3 要在 entry code 裡手動切（Ch 7）。
- **rsp 沒換**。entry code 第一件事才換。

你看 Linux source `arch/x86/entry/entry_64.S` 的 `entry_SYSCALL_64`：

```asm
SYM_CODE_START(entry_SYSCALL_64)
    swapgs
    /* tss.sp2 is scratch space. */
    movq    %rsp, PER_CPU_VAR(cpu_tss_rw + TSS_sp2)
    SWITCH_TO_KERNEL_CR3 scratch_reg=%rsp       ; ← KPTI 切 cr3
    movq    PER_CPU_VAR(pcpu_hot + X86_top_of_stack), %rsp
    /* 現在在 kernel stack 了，開始組 pt_regs */
    pushq   $__USER_DS              /* pt_regs->ss */
    pushq   PER_CPU_VAR(cpu_tss_rw + TSS_sp2)   /* pt_regs->sp */
    pushq   %r11                    /* pt_regs->flags */
    pushq   $__USER_CS              /* pt_regs->cs */
    pushq   %rcx                    /* pt_regs->ip */
    ...
```

這段是 kernel exploit 作者每個人都看過的。幾個後面會用到的事實：

- **`pt_regs` 的結構固定** — Ch 12 會用 `pt_regs 技巧`，就是你在 syscall 剛進 kernel 時，把要控的 RIP 藏在暫存器裡，等後面某個路徑跳回 user 時它會被還原。
- **`swapgs` 跟 cr3 切換是分開的兩件事**，Ch 7 KPTI 才會把這兩件事講全。

## per-CPU 與 `swapgs`：為什麼 gs 那麼特別

x86-64 幾乎不用段暫存器，除了 fs / gs 用來做 thread-local 與 per-CPU。

- **user-space**：glibc 把 fs 當 TLS 用（你 gdb 看 `mov rax, %fs:0x28` 就是在讀 stack canary）。
- **kernel-space**：把 gs 當 per-CPU 變數的 base。`%gs:0x...` 讀的就是當前 CPU 專屬的資料。

問題：CPU 剛進 ring 0 時，gs 還是 user 設的（user 沒設 gs 的話是 0）。如果不切，kernel 第一個指令 `mov ..., %gs:0x...` 就炸。

解法：CPU 提供 **`swapgs`** — 交換 `MSR_GS_BASE` 與 `MSR_KERNEL_GS_BASE` 兩個 MSR 的內容。kernel entry 一進來 `swapgs`，出去前再 `swapgs` 一次。

看 `/proc/cpuinfo` 只看得到 flags，看不到 MSR。要讀 MSR：

```bash
# guest VM 裡
modprobe msr 2>/dev/null || true
rdmsr 0xc0000100   # IA32_FS_BASE
rdmsr 0xc0000101   # IA32_GS_BASE (kernel 用的)
rdmsr 0xc0000102   # IA32_KERNEL_GS_BASE (user 用的，被 swap 藏起來的)
```

我們 busybox initramfs 沒有 `rdmsr`。記結論就好：**swapgs 不是切地址空間，是切一個暫存器的意義**。

## x86-64 virtual address space

這是 kernel pwn 最該內化的一張圖：

```
0xffffffffffffffff ┌──────────────────────────────┐  ← 63-bit 頂
                   │  kernel fixmap / vsyscall    │
0xffffffff80000000 │  kernel text & modules       │  ← kernel base 在這一帶
0xffffffff00000000 │                              │
0xffffe90000000000 │  vmalloc / ioremap           │
0xffff888000000000 │  direct map (physmap)        │  ← 所有實體記憶體在這
0xffff800000000000 ├──────────────────────────────┤  ← kernel 起點
                   │                              │
                   │   non-canonical hole         │  ← 訪問就 #GP
                   │                              │
0x00007fffffffffff ├──────────────────────────────┤  ← user 頂
                   │  user stack / heap / mmap    │
0x0000000000400000 │  user text (mostly)          │
0x0000000000000000 └──────────────────────────────┘
```

幾個後面反覆用的事實：

- **48-bit canonical address**：bit 47 是 sign-extend 到 bit 63。所以 user 地址 bit 47 一定是 0，kernel 地址 bit 47 一定是 1。任何 `0x0000...` 開頭是 user、`0xffff...` 開頭是 kernel。
- **Kernel text 在 `0xffffffff81000000` 附近**（KASLR 關掉時）— 這個地址在後面每章都會出現。
- **Direct map (`0xffff888000000000` 起)**：kernel 把**所有**實體記憶體 1:1 映射到這個區域。所以 kernel 隨時可以透過這個 range 讀寫任意實體頁。這也是後面 Dirty Pagetable / USMA 的根基。
- **Modules 區**在 kernel text 附近，`insmod` 載入的 module 地址也是 `0xffffffffc0...` 這種。

確認這張圖：guest 裡關掉 `kptr_restrict` 後看 kallsyms：

```
/ # echo 0 > /proc/sys/kernel/kptr_restrict
/ # cat /proc/kallsyms | grep -E " (commit_creds|_text|__start_rodata)$"
ffffffff810cxxxx T commit_creds
ffffffff81000000 T _text
ffffffff81e00000 R __start_rodata
```

`_text` 就是 kernel 的起點。這次我們關了 KASLR 所以每次重啟都是 `ffffffff81000000`。Ch 6 才開啟 KASLR。

## 四種 user → kernel transition

不是只有 `syscall` 會進 kernel，打 kernel pwn 要知道全部四種：

| 進入路徑 | 觸發方式 | entry code |
|---|---|---|
| **syscall** | `syscall` 指令 | `entry_SYSCALL_64` |
| **interrupt** | 硬體中斷 | `asm_common_interrupt` |
| **exception** | page fault、#GP、divide error... | `asm_exc_*`（每種一個） |
| **IPI** | 跨 CPU 中斷 | 類似 interrupt |

對 exploit 作者，前兩個最重要：

- **syscall**：正常攻擊面，99% 的漏洞從 syscall 進
- **exception**：ret2usr 失敗、無效指令、SMEP/SMAP 阻擋都會觸發 exception，kernel oops log 就是 exception handler 印的。Debug 時你幾乎在讀 exception 的 log。

## user ↔ kernel 的「切換點」清單

下面是 kernel exploit 每一次 crash、每一次回退都會撞到的地方，先混個臉熟：

| 動作 | 在哪發生 | 相關 MSR / 硬體 |
|---|---|---|
| CPL 切換 | `syscall` / `sysret` / `int` / `iretq` | cs 暫存器低 2 bit |
| stack 切換 | entry asm 手動 | `cpu_tss_rw.sp0`、`pcpu_hot.top_of_stack` |
| gs 切換 | `swapgs` | `MSR_GS_BASE` / `MSR_KERNEL_GS_BASE` |
| cr3 切換（KPTI） | entry asm 手動 | `cr3` 暫存器 |
| rip 切換 | `syscall` / `sysretq` 直接、或 push/iret | — |

**每一欄你都至少要能說出「為什麼要切、不切會怎樣」** — Ch 7 KPTI 會用這張表。

## SMEP / SMAP：這章先知道它們在哪

詳細 Ch 5 才打。這章只要知道位置：

- **SMEP（Supervisor Mode Execution Prevention）**：在 CR4 裡一個 bit。打開後，CPU 在 ring 0 執行 user-pages（U=1 的 page）指令時觸發 #PF。→ 用來擋 ret2usr。
- **SMAP（Supervisor Mode Access Prevention）**：同樣 CR4 裡一個 bit。打開後，ring 0 讀寫 user pages 觸發 #PF。→ 用來擋 kernel `mov` user 指標去讀。kernel 要讀 user memory 必須走 `copy_from_user` / `copy_to_user`，這些函式會先 `stac`（set AC flag）臨時關 SMAP。

check guest 有沒有開：

```
/ # cat /proc/cpuinfo | grep -E "smep|smap"
... smep ... smap ...
```

這兩個 flag 出現表示 CPU 支援而且 kernel 開了。如果沒出現，檢查 QEMU 的 `-cpu` 參數是不是漏了 `+smep,+smap`（Ch 0 的 `run.sh` 有加）。

## 常見誤解

**「syscall 切 ring 時會自動切 stack」** — 不會。CPU 只切 cs / ss 暫存器的 selector，**rsp 沿用**。kernel entry code 自己換 rsp。這是為什麼 `entry_SYSCALL_64` 第一段那麼小心翼翼。

**「kernel 跟 user 的 virtual address 是不同 page table」** — 半對半錯。**同一張** page table 同時有 user 和 kernel 的 mapping，差別只在 U/S bit。KPTI 打開後才變成兩張（Ch 7 細講）。

**「kernel 在 `0xffffffff...`，user 在 `0x7fff...`，兩邊地址不可能衝突」** — 對。這是 x86-64 48-bit canonical 地址的硬性規定。leak 到 `0xffff...` 就是 kernel。

**「kernel stack 也在 direct map 裡」** — 嚴格說不一定。預設 `CONFIG_VMAP_STACK=y`，kernel stack 在 vmalloc 區，跟 direct map 不同。這影響 Ch 12 `pt_regs` 技巧的地址算法。

## 動手練習

1. **算 kernel text base**：guest 裡 `grep " _text$" /proc/kallsyms`，記下地址。這是 `nokaslr` 下的 baseline。
2. **找 syscall entry**：`grep " entry_SYSCALL_64$" /proc/kallsyms`，看它和 `_text` 差多少（幾十 KB 以內）。
3. **看自己被 map 在哪**：host 跑個 C `while(1) sleep(1);` 然後 `cat /proc/<pid>/maps`。確認你從沒看到 `ffff...` 的地址 — user-space 看不到 kernel。
4. **Host gdb 追一次 syscall**（進階）：在 guest gdb 連線後 `b entry_SYSCALL_64`，繼續 guest 執行、在 guest 裡跑 `ls`，gdb 會停在 entry，這時你腦袋裡的流程應該和斷點位置完全對得上。

## 自我檢核

- [ ] 能默寫出「`syscall` 指令硬體做哪六件事」
- [ ] 能解釋 `swapgs` 存在的理由（per-CPU、user 暫存器不可信）
- [ ] 能說出 user / kernel / direct map / modules 各自的地址範圍
- [ ] 看到 `0xffff888...` 開頭的地址能馬上說「direct map」
- [ ] 看到 `0xffffffff81...` 開頭的地址能馬上說「kernel text」
- [ ] 知道 SMEP / SMAP 各擋什麼、在 CR4 裡

下一章我們從這張地圖上最具體的一點切入 — 寫一個 char device driver，自己做個 `file_operations`，讓 user-space 可以透過 `open` / `ioctl` 呼叫到我們藏的漏洞函式。後面 Part 2、3 的每個練習題都是這個模板。

→ [Ch 2 — 第一個 vulnerable kernel module：file_operations、ioctl、copy_from_user](./02-first-vulnerable-module.md)
