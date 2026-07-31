# Ch 4 — Syscall 機制與自訂 syscall

> **目標**：搞懂使用者空間一條 `syscall` 指令是怎麼跨進 kernel 的——從 CPU 換棧、換 GS、查表，一路到 `__x64_sys_xxx` wrapper 被呼叫。學完你能親手加一條自訂 syscall（改 `.tbl` + `SYSCALL_DEFINE` + 重編），並在 gdb 裡停在 `do_syscall_64` 看它真的走進來。

## 為什麼需要這個？

user 跟 kernel 跑在同一顆 CPU 上，但權限天差地遠：user 空間（x86_64 的 ring 3）不能碰 page table、不能關中斷、不能直接讀寫別的行程的記憶體。可是 user 又非得請 kernel 幫忙不可——開檔、送封包、配記憶體，這些都要特權指令。所以需要一道**受控的門**：user 只能從 kernel 指定的**單一入口**進來，而且進來瞬間 CPU 就切到 ring 0。

這道門不能是「呼叫一個函式位址」那麼隨便。如果 user 能任意跳進 kernel 的任何位址，整個特權隔離就破了。硬體層級的機制是：CPU 提供一條專門指令（x86_64 的 `syscall`、ARM64 的 `svc`），執行它會**同時**做三件事——提權到 ring 0、跳到一個**事先在暫存器裡登記好的固定入口**、保存回程位址。user 唯一能控制的是「帶什麼參數進去」，跳去哪裡由 kernel 說了算。

這章拆解這道門的兩側：user 側怎麼發起（放哪些暫存器、執行哪條指令），kernel 側怎麼接（換棧、查表、分派）。搞懂它你才會明白 `strace` 看到的每一行 `openat(...) = 3` 底下，CPU 到底做了什麼；也才知道為什麼 kernel 拿到 user 給的指標**不能直接解參考**——那是 Ch 6/kernel_pwn 一切記憶體攻防的起點。

## 先建立直覺

先把「一次 syscall」想成一趟出入境：

```
   user 空間 (ring 3)                        kernel 空間 (ring 0)
   ─────────────────                        ─────────────────────
   把「要辦什麼」寫在暫存器：
     rax = syscall number   ┐
     rdi/rsi/rdx/r10/r8/r9  │ 參數（最多 6 個）
                            │
     syscall  ───────────────┼──► CPU 硬體動作（一條指令內完成）：
                            │      1. 提權 ring3 → ring0
                            │      2. rip ← MSR_LSTAR（entry_SYSCALL_64）
                            │      3. rcx ← 舊 rip（回程位址）
                            │      4. r11 ← 舊 rflags
                            │      （注意：此刻還在 user stack、user GS！）
                            │
                            └──► entry_SYSCALL_64（組語入口）
                                   SWAPGS         切到 kernel per-CPU 資料
                                   換 kernel stack
                                   PUSH 一整組暫存器 → 組成 struct pt_regs
                                   call do_syscall_64
                                     └─ 查 sys_call_table[nr]
                                         └─ __x64_sys_openat(regs)  ← C 函式
                                              解包 pt_regs → 真正的 openat 邏輯
                                   ...回來後還原暫存器
                                   sysretq  ─────────────────────► 回到 user 的下一條指令
```

三個要點先記住：

1. **number 放 rax，不是靠位址跳**。kernel 有一張表（`sys_call_table`），rax 是索引。你要辦 5 號業務就填 5，kernel 查表找到 5 號窗口。這是「單一入口 + 查表分派」的設計，門只有一扇。
2. **`syscall` 指令本身不換棧、不換 GS**。CPU 只做提權 + 跳入口 + 存回程位址。換 kernel stack、`SWAPGS` 這些是**入口組語自己動手做的**——這正是入口為什麼必須用組語寫、而且順序錯不得的原因（Ch 2 講過 `current` 靠 GS 取，GS 沒切好 `current` 就是錯的）。
3. **參數靠暫存器傳，且和 C 的呼叫慣例不完全一樣**。第 4 個參數 user 側放 **r10** 不是 rcx——因為 rcx 被 `syscall` 硬體拿去存回程位址了。這個錯位是後面 wrapper 要處理的細節之一。

## user 側：一條 syscall 指令長什麼樣

x86_64 上，發一次 syscall 的慣例（syscall calling convention，和一般函式的 System V ABI **不同**）：

| 用途 | 暫存器 |
|---|---|
| syscall number | `rax` |
| 參數 1 | `rdi` |
| 參數 2 | `rsi` |
| 參數 3 | `rdx` |
| 參數 4 | `r10`（**注意**：函式慣例是 rcx，這裡改用 r10） |
| 參數 5 | `r8` |
| 參數 6 | `r9` |
| 回傳值 | `rax` |

為什麼第 4 個參數用 r10 不用 rcx？因為 `syscall` 指令**硬體行為**是把回程 rip 塞進 rcx、把 rflags 塞進 r11。這兩個暫存器被硬體徵用了，libc 的 syscall wrapper 只好把原本該放 rcx 的第 4 參數挪到 r10。這不是誰的品味問題，是硬體逼出來的錯位。

手寫一個不經 libc 的 `write(1, "hi\n", 3)`（syscall number 1 = `write`）：

```asm
    mov  $1, %rax        # rax = 1 (sys_write)
    mov  $1, %rdi        # fd = 1 (stdout)
    lea  msg(%rip), %rsi # buf
    mov  $3, %rdx        # count = 3
    syscall              # 進 kernel，回來後 rax = 寫入的位元組數（或負 errno）
```

回傳值放 rax。**失敗時 kernel 回的是負的 errno**（例如 `-EBADF` = -9），libc 的 wrapper 負責把負值轉成 `errno` 全域變數 + 回傳 -1。kernel 內部從頭到尾用「負 errno」這套約定，沒有 `errno` 這個東西。

> ARM64 對照（Ch 14 會再碰硬體差異）：number 放 **x8**，參數 x0–x5，指令是 **`svc #0`**，回傳值 x0。沒有 x86 那種 rcx 被徵用的錯位問題，六個參數乾淨地放 x0–x5。設計更清爽，但「單一入口 + 查表分派」的骨架和 x86 一模一樣。

## kernel 側：入口怎麼登記、CPU 怎麼找到它

CPU 執行 `syscall` 時跳去哪？答案寫在一個 MSR（Model-Specific Register）裡：**`MSR_LSTAR`**。開機時 kernel 把入口函式的位址寫進這個 MSR，之後每條 `syscall` 指令 CPU 都自動跳到那個位址。

寫入的地方在 `arch/x86/kernel/cpu/common.c` 的 `syscall_init()`：

```c
// arch/x86/kernel/cpu/common.c，syscall_init()（節錄概念）
wrmsrl(MSR_LSTAR, (unsigned long)entry_SYSCALL_64);
```

`entry_SYSCALL_64` 就是那扇門的位址，它是一段組語，定義在 **`arch/x86/entry/entry_64.S`**。開機時（Ch 3 的 `start_kernel` → `cpu_init` 路徑）每顆 CPU 都跑一次 `syscall_init`，把自己的 LSTAR 指到同一個入口。

整條路徑：

```
   user: syscall
     │  CPU 讀 MSR_LSTAR，跳過去
     ▼
   entry_SYSCALL_64            arch/x86/entry/entry_64.S   ← 組語，不能用 C
     │  SWAPGS                 切到 kernel 的 per-CPU GS base
     │  切 kernel stack        （從 per-CPU 的 cpu_current_top_of_stack 拿）
     │  PUSH_REGS              把 user 暫存器全推上棧 → 排成 struct pt_regs
     │  mov %rsp, %rdi         把 pt_regs 的位址當第一個參數
     ▼
   do_syscall_64(regs, nr)    arch/x86/entry/common.c     ← 回到 C 世界
     │  nr &= __SYSCALL_MASK   邊界檢查
     │  if (nr < NR_syscalls)
     │      sys_call_table[nr](regs)
     ▼
   __x64_sys_openat(regs)     由 SYSCALL_DEFINE4(openat, ...) 展開而來
     │  從 regs 解包出 dfd, filename, flags, mode
     ▼
   do_sys_openat2(...)        真正的 openat 邏輯（fs/open.c）
```

`entry_SYSCALL_64` 為什麼一定要組語？三個硬理由：

- **`SWAPGS` 必須在任何會用到 `current`（靠 GS）的 C 程式碼之前執行**。進來瞬間 GS 還是 user 的，這時若跑 C 碰到 `current` 就讀到垃圾。順序錯不得，C 編譯器不保證這個順序，只能手寫。
- **此刻還站在 user stack 上**。C 函式一呼叫就會用棧，但 user stack 不可信（可能被惡意設成指向 kernel 記憶體）。必須先手動切到 kernel stack 才敢進 C。
- **要把 user 的暫存器完整快照成 `struct pt_regs`**。這個結構（`arch/x86/include/asm/ptrace.h` 的 `struct pt_regs`）是 user 進 kernel 那一刻所有暫存器的凍結影像。後面 `ptrace`、signal、`__x64_sys_xxx` 解參數，全靠它。

進到 `do_syscall_64`（**`arch/x86/entry/common.c`**）就回到 C 了。它做的事情本質很單純：**拿 nr 當索引查 `sys_call_table`，呼叫對應的 handler，把 `pt_regs` 指標傳進去**。中間夾了 entry 的一堆 bookkeeping（`enter_from_user_mode`、seccomp/ptrace 攔截點、處理待決 signal），但核心就是查表分派。

## 這張表哪來的：`.tbl` → 自動生成

`sys_call_table` 不是手寫的巨大陣列，是**從一張文字表自動產生的**。源頭是：

```
arch/x86/entry/syscalls/syscall_64.tbl
```

長這樣（節錄）：

```
# number   abi   name      entry point
0          common  read    sys_read
1          common  write   sys_write
2          common  open    sys_open
...
257        common  openat  sys_openat
```

build 時，`scripts/syscalltbl.sh` 讀這張 `.tbl`，生成 `arch/x86/include/generated/asm/syscalls_64.h` 之類的標頭；`arch/x86/entry/syscall_64.c` 再 include 它，湊出真正的 `sys_call_table[]` 陣列。所以你**改 syscall 表不是改一個 C 陣列，是改那張 `.tbl` 然後讓 build 系統重生**。這是等下動手加自訂 syscall 的關鍵。

`.tbl` 每一行四欄：number、abi（`common`/`64`/`x32`）、name、entry point。entry point 那欄寫 `sys_openat`，但真正被 `sys_call_table` 裝進去的是 `__x64_sys_openat`——中間的 `__x64_` 前綴是 `SYSCALL_DEFINEn` 巨集加的。這帶出下一個問題：那個 wrapper 到底是什麼、為什麼要它。

## `SYSCALL_DEFINEn`：為什麼需要一層 wrapper

你在 `fs/open.c` 看到的 openat 定義長這樣：

```c
// fs/open.c
SYSCALL_DEFINE4(openat, int, dfd, const char __user *, filename,
                int, flags, umode_t, mode)
{
    ...
    return do_sys_openat2(dfd, filename, flags, &how);
}
```

`SYSCALL_DEFINE4` 是巨集（定義在 **`include/linux/syscalls.h`**），`4` 是參數個數。它展開後**不是**只生一個函式，而是生**兩層**：

```
SYSCALL_DEFINE4(openat, ...)  展開成大致：

   ┌─ __x64_sys_openat(const struct pt_regs *regs)   ← 進 sys_call_table 的是「這個」
   │     從 regs 解包：
   │       dfd      = regs->di
   │       filename = regs->si
   │       flags    = regs->dx
   │       mode     = regs->r10        ← 第 4 參數，取 r10 不是 rcx
   │     return __se_sys_openat(dfd, filename, flags, mode);
   │
   └─ __do_sys_openat(int dfd, const char __user *filename, int flags, umode_t mode)
         ← 你寫的函式本體真正住在這裡
```

為什麼要這層「pt_regs 解包 wrapper」？兩個原因，兩個都是被安全與 ABI 逼出來的：

1. **統一介面**。`sys_call_table` 裡每個 handler 型別都必須一致——收一個 `const struct pt_regs *`，回一個 `long`。這樣 `do_syscall_64` 才能用單一個函式指標型別呼叫任何 syscall。真正的參數（dfd、flags…）藏在 pt_regs 裡，由 `__x64_` wrapper 拆出來。若沒這層，每個 syscall 參數個數、型別都不同，沒法統一查表呼叫。

2. **防止 register 污染 / 隱蔽的資訊洩漏（這層是 2018 年 Spectre/Meltdown 之後強化的）**。早期 kernel 直接讓 C 函式從暫存器收參數，但這樣「上層沒用到的暫存器高位」可能夾帶 user 塞進來的值。改成從 pt_regs 逐一解包後，每個參數都經過明確的型別轉換（例如 `int dfd = (int)regs->di`，高 32 位被截掉），**user 沒法靠在暫存器高位藏東西來影響 kernel**。`__se_sys_` 那層（sign-extend wrapper）還負責把窄型別參數正確地符號延伸，避免 user 用一個看似小的值繞過邊界檢查。

所以一次 openat 進來，實際穿過三層：`__x64_sys_openat`（解 pt_regs）→ `__se_sys_openat`（型別/符號處理）→ `__do_sys_openat`（你寫的邏輯）。你只寫最裡層，巨集把外兩層生給你。

## copy_from_user / copy_to_user：為什麼不能直接解參考 user 指標

syscall 參數裡常有指標，例如 openat 的 `filename` 是 `const char __user *`。那個 `__user` 標註（`include/linux/compiler_types.h` 定義，靠 sparse 靜態檢查工具驗證）在喊：**這是 user 空間的位址，kernel 絕對不能直接 `*filename` 解參考**。理由：

- **它可能是惡意的 kernel 位址**。user 可以傳任何數值當「指標」。若 kernel 直接解參考，user 就能誘導 kernel 讀寫任意 kernel 記憶體——這正是 kernel_pwn 課裡一整類漏洞的原型。
- **它可能沒被映射（尚未 page in、或根本非法）**。直接解參考會觸發 page fault，在錯的 context 裡（例如持鎖時）fault 會炸。
- **在 SMAP（Supervisor Mode Access Prevention）開啟的 CPU 上，kernel 直接碰 user 位址會硬體例外**。硬體幫你把「隨手解參考 user 指標」變成當場報錯。

正確做法是走專用的搬運函式：

```c
// 從 user 搬進 kernel（例如把 user 給的字串拷進 kernel buffer）
if (copy_from_user(kbuf, ubuf, len))
    return -EFAULT;          // 位址非法就回 -EFAULT，不是 crash

// 從 kernel 搬回 user（例如把結果寫回 user 給的 buffer）
if (copy_to_user(ubuf, kbuf, len))
    return -EFAULT;
```

`copy_from_user` / `copy_to_user`（`include/linux/uaccess.h` 宣告，arch 實作在 `arch/x86/lib/`）內部做兩件事：

1. **`access_ok(ptr, size)`**：檢查這段位址範圍是否**完全落在 user 空間**（不會跨進 kernel 位址區）。落在 kernel 區直接拒。
2. **加上 exception fixup 的安全存取**：真正搬資料時，若 fault 了，kernel 有一張「例外表」（`__ex_table`）把這個 fault 導向一段 fixup 程式碼，讓函式**回傳沒搬完的位元組數**而不是 panic。所以 `copy_from_user` 回傳非 0 = 沒搬完 = user 指標有問題 → 你回 `-EFAULT`。

一句話記牢：**syscall 收到的任何 `__user` 指標，只能透過 `copy_*_user` / `get_user` / `put_user` 這套來碰，永遠不直接解參考。** 這是使用者/核心邊界最硬的一條紀律，違反它就是漏洞。（延伸到 Ch 6 記憶體 API 和 kernel_pwn 的 heap 攻防。）

## 動手：加一條你自己的 syscall

我們加一條 `sys_hello`，行為是把一個 user 給的字串拷進 kernel、印到 dmesg、回傳字串長度。走「正規」做法——改 `.tbl` + `SYSCALL_DEFINE` + 重編。

**Step 1：在 `.tbl` 登記一個號碼。** 打開 `arch/x86/entry/syscalls/syscall_64.tbl`，找到目前最大的 common 號碼後面接一個沒被用的（6.12 的 x86_64 表最大號已排到 462（`mseal`），這裡示範挑一個明顯還沒被用的號 **548**，實際請確認你那份 `.tbl` 該號沒被佔）：

```
548     common  hello       sys_hello
```

> 別亂挑號碼：號碼是 ABI 的一部分，一旦某號被正式指派給某 syscall 就永遠不能改用途（會破壞既有 binary）。你自己玩用一個明顯超出目前範圍的號碼，跟未來上游衝突機率低。真的要上游得走 kernel mailing list 流程。

**Step 2：寫實作。** 找個地方放，例如新開 `kernel/hello.c`：

```c
// kernel/hello.c
#include <linux/syscalls.h>
#include <linux/kernel.h>
#include <linux/uaccess.h>

#define HELLO_MAX 128

SYSCALL_DEFINE2(hello, const char __user *, ubuf, size_t, len)
{
    char kbuf[HELLO_MAX];

    if (len == 0 || len >= HELLO_MAX)
        return -EINVAL;                 // 邊界：太長或空一律拒

    if (copy_from_user(kbuf, ubuf, len))
        return -EFAULT;                 // user 指標非法 → 回 -EFAULT，不 crash

    kbuf[len] = '\0';                   // 手動收尾，別信 user 有給 '\0'
    pr_info("sys_hello: got \"%s\" (len=%zu)\n", kbuf, len);

    return len;                         // 回傳值放進 user 的 rax
}
```

注意這裡把前面每一條紀律都用上了：`__user` 標註、`copy_from_user` 而非直接解參考、檢查回傳值回 `-EFAULT`、邊界檢查（`len >= HELLO_MAX` 防溢位）、手動 null 收尾（不信任 user 的字串有結尾）。

**Step 3：讓 build 系統看到 `hello.c`。** 在 `kernel/Makefile` 加一行把它編進去：

```
obj-y += hello.o
```

**Step 4：重編。** `make -j"$(nproc)"`。build 系統會重新從 `.tbl` 生成 `sys_call_table`，把 548 號指到 `__x64_sys_hello`。重編後用新的 `bzImage`/`vmlinux` 開 QEMU（Ch 0 的流程）。

**Step 5：在 QEMU 裡呼叫它。** libc 沒有 `hello` 這個 wrapper，用通用的 `syscall(2)`：

```c
// caller.c —— 在 QEMU 的環境裡編（或塞進 initramfs）
#include <unistd.h>
#include <sys/syscall.h>
#include <stdio.h>

#define __NR_hello 548

int main(void)
{
    const char *msg = "hi from userspace";
    long ret = syscall(__NR_hello, msg, 17);
    printf("sys_hello returned %ld\n", ret);   // 應印 17
    return 0;
}
```

跑起來後，`dmesg | tail` 應看到 `sys_hello: got "hi from userspace" (len=17)`，且 caller 印 `returned 17`。試試邊界：傳 `len=200` 應回 -1（`-EINVAL`）；傳一個亂數當指標應回 -1（`-EFAULT`）——證明你的檢查有效。

### 那些「不用重編 kernel」的 hacky 替代法，為什麼不推薦

網路上很多教學教你用 LKM（可載入模組）「劫持」既有 syscall，而不是改 `.tbl` 重編。常見兩招，都各有致命問題：

| 手法 | 怎麼做 | 為什麼不推薦 |
|---|---|---|
| **改寫 `sys_call_table`** | 找到 `sys_call_table` 位址，把某個 entry 換成你的函式指標 | `sys_call_table` 在現代 kernel 是**唯讀**（`.rodata`，靠頁表權限保護）。你得先關 CR0 的 WP 位元才能寫——這本身就是 rootkit 技法，會被 CFI/lockdown 擋，且和其他 CPU race。純屬 hack，正式場合零可行性 |
| **kprobe 攔截** | 在 `__x64_sys_xxx` 入口下 kprobe，在 handler 裡改行為 | 適合**觀測**（Ch 51、bpf 課），不適合**新增**功能。你沒法用它憑空多一個 syscall number；改既有 syscall 行為也脆弱（函式改名/inline 就失效），且有效能開銷 |

正規做法（改 `.tbl` 重編）唯一的「缺點」是要重編 kernel——但你這門課本來就在重編 kernel。它的好處是**乾淨、可被 gdb 正常追、和 upstream 機制完全一致**：你加的 syscall 和 `read`/`write` 走的是同一條 entry path、同一張表、同一套 `SYSCALL_DEFINE` 巨集。你學到的是 kernel 真正怎麼運作，不是繞過它的偏方。劫持 `sys_call_table` 那類技法留給 Ch 51 和 kernel_pwn 當「攻擊/rootkit 視角」讀，別當成加 syscall 的正途。

## 動手：gdb 停在 `do_syscall_64` 看它走進來

用 Ch 0 的 QEMU + gdb 環境。開機（記得 `nokaslr`），gdb 連上後：

```gdb
(gdb) break do_syscall_64
(gdb) continue
```

回到 QEMU 的 shell 隨便下個指令（`ls`、`echo hi`），gdb 立刻停在 `do_syscall_64`。這時：

```gdb
(gdb) print regs->orig_ax        # 這次 syscall 的 number（rax 原值）
(gdb) print regs->di             # 第 1 參數
(gdb) print regs->si             # 第 2 參數
(gdb) backtrace                  # 看它從 entry_SYSCALL_64 一路過來
```

`orig_ax` 是 `pt_regs` 裡保存的**原始 rax**（syscall number）——因為 rax 之後會被拿去放回傳值，number 另存在 `orig_ax`。你會看到一連串不同 number 刷過（shell 一個指令背後幾十次 syscall）。

想只停在你自訂的那條：

```gdb
(gdb) break __x64_sys_hello       # 停在你加的 syscall
(gdb) continue
```

然後在 QEMU 裡跑你的 `caller`，gdb 會停進 `__x64_sys_hello`，你能 `step` 進去看 `copy_from_user` 怎麼把字串搬進來、`kbuf` 內容長怎樣。這就把這一章從「讀懂」變成「親眼看它跑」。

## 對比與取捨

| 面向 | x86_64 | ARM64 |
|---|---|---|
| 發起指令 | `syscall` | `svc #0` |
| number 放哪 | `rax` | `x8` |
| 參數暫存器 | rdi, rsi, rdx, **r10**, r8, r9 | x0–x5 |
| 第 4 參數為何錯位 | rcx 被硬體徵用存回程 rip，改用 r10 | 無錯位，x0–x5 乾淨 |
| 回程資訊 | rcx=舊 rip, r11=舊 rflags（硬體塞） | ELR_EL1=回程, SPSR_EL1=狀態 |
| 入口登記在哪 | `MSR_LSTAR`（`syscall_init`） | `VBAR_EL1` 指向的 exception vector table |
| 組語入口 | `entry_SYSCALL_64`（`entry_64.S`） | `el0_svc`（`arch/arm64/kernel/entry.S`） |
| C 分派函式 | `do_syscall_64` | `el0_svc_common` / `invoke_syscall` |
| syscall table | `sys_call_table`（`.tbl` 生成） | `sys_call_table`（`arch/arm64/.../syscall.tbl` 生成） |

骨架完全同構：**單一入口 → 換 GS/棧 → 存成 pt_regs → 查表分派 → wrapper 解包 → 本體**。差別只在硬體怎麼提供入口、參數放哪。ARM64 用 exception（`svc` 是同步例外，走 exception vector）而非 x86 那種專用 `syscall` 指令 + MSR，但對 kernel C 層來說幾乎一樣。

舊 x86 還有 `int 0x80` 這條路（透過 IDT 的 128 號中斷進 kernel）。它慢（走完整中斷處理流程）、且只傳 32 位參數，早被 `syscall` 指令取代。64 位程式一律走 `syscall`；`int 0x80` 只在跑 32 位相容 binary 時還會遇到。看到教學還在講 `int 0x80` 當主線，就知道它過時了。

## 踩雷集錦

1. **「syscall number 就是函式位址」——錯**。number 是 `sys_call_table` 的**索引**，不是位址。user 給 5，kernel 查表第 5 格找到 handler。單一入口 + 查表，才守得住特權邊界。若是跳位址，隔離就沒了。

2. **「第 4 個參數放 rcx」——錯**。函式呼叫慣例（System V ABI）第 4 參數是 rcx，但 **syscall 慣例改用 r10**，因為 rcx 被 `syscall` 指令硬體拿去存回程 rip。手寫組語 syscall 最常見的 bug 就是照抄函式慣例把第 4 參數放 rcx。

3. **「kernel 可以直接解參考 user 指標」——大錯，且是漏洞根源**。必須走 `copy_from_user`/`copy_to_user`/`get_user`/`put_user`，它們做 `access_ok` 邊界檢查 + fault 安全處理。直接 `*ptr` 會被 SMAP 擋、或讓 user 讀寫任意 kernel 記憶體。

4. **「`SYSCALL_DEFINE4` 只生一個函式」——不是**。它生 `__x64_sys_xxx`（解 pt_regs、進 table 的）、`__se_sys_xxx`（型別/符號延伸）、`__do_sys_xxx`（你的本體）三層。進 `sys_call_table` 的是 `__x64_` 那層，不是你寫的本體。gdb 要停整個 syscall 入口下 `__x64_sys_xxx`，停本體下 `__do_sys_xxx`（有時被 inline 掉停不到）。

5. **「改 syscall 只要改 C 陣列」——不是**。`sys_call_table` 是 build 時從 `arch/x86/entry/syscalls/syscall_64.tbl` **自動生成**的。加 syscall 要改那張 `.tbl` 並重編，不是去找一個 C 陣列改。找不到那個陣列是正常的，它是 generated。

6. **回傳值忘了用「負 errno」約定**。syscall 本體失敗要 `return -EINVAL` / `-EFAULT` 這種**負值**，不是回 -1 再設 errno（kernel 內沒有 errno）。回正的 errno 或 0 表示成功，user 側 libc 才能正確翻譯。

## 進階：再往深一層

- **`entry_SYSCALL_64` 的 KPTI 分頁切換**。Meltdown 之後，kernel 開了 KPTI（Kernel Page Table Isolation），user 態跑的頁表**看不到大部分 kernel 記憶體**。所以入口除了 `SWAPGS`，還要切 CR3（換到含完整 kernel 映射的頁表）。這是 entry 組語又臭又長、且效能敏感的原因之一——每次 syscall 多一次 CR3 切換。這條線接 Ch 16（頁表）和 kernel_pwn（KPTI 繞過）。

- **`orig_ax` vs `ax` 的區別**。`pt_regs` 同時有 `orig_ax`（進來時的 syscall number）和 `ax`（回傳值會覆蓋它）。signal 處理若要「重啟」被打斷的 syscall（`ERESTARTSYS`），靠的就是 `orig_ax` 還留著 number，能重放這次呼叫。面試會問「syscall 被 signal 打斷怎麼辦」，答案的機制根就在這。

- **seccomp 攔在哪**。`do_syscall_64` 進去後、真正查表前有 seccomp 的攔截點（`syscall_trace_enter`）。seccomp-BPF（Ch 49、docker 沙箱）就在這裡對 number + 參數做過濾，決定放行/擋掉/殺行程。你加的自訂 syscall 若在有 seccomp filter 的環境裡（如容器）可能被擋——因為 filter 白名單沒有你的 number。

- **vDSO：不進 kernel 的「假 syscall」**。有些高頻 syscall（`gettimeofday`、`clock_gettime`）其實**不真的進 kernel**——kernel 把一小段程式碼 + 資料（時間）映射進每個行程（vDSO），user 直接在 user 態算出答案，省掉整趟 entry 開銷。所以 `strace` 有時看不到 `gettimeofday`。這是「最快的 syscall 是不做 syscall」的實例。

## 動手練習

1. **gdb 數 syscall**：`break do_syscall_64`，在 QEMU 裡跑 `ls`，用 `print regs->orig_ax` 記下每次的 number，數一個 `ls` 背後發了多少次、都是哪些 syscall。對照 `strace ls` 的輸出（若 QEMU 環境有 strace）驗證你數的對不對。

2. **把自訂 syscall 加完並跑通**：照「動手」那節加 `sys_hello`，重編、在 QEMU 用 `syscall(__NR_hello, ...)` 呼叫，`dmesg` 看到輸出。**再故意弄壞**：把 `copy_from_user` 改成 `strcpy(kbuf, ubuf)`（直接解參考 user 指標），重編，傳一個亂指標——觀察 kernel 怎麼炸（Oops），對照為什麼要 `copy_from_user`。

3. **手寫組語 syscall**：不經 libc，用內嵌組語發一次 `write`（number 1）把 "hi\n" 印到 stdout。故意把第 4 參數的暫存器寫成 rcx（模擬踩雷 #2），看它為什麼行為錯亂。

4. **追 wrapper 三層**：對某個既有 syscall（如 `openat`），在 Elixir 上找 `SYSCALL_DEFINE4(openat, ...)`，畫出 `__x64_sys_openat` → `__se_sys_openat` → `__do_sys_openat` 的展開，標出每個參數從哪個 `regs->` 欄位解出來。

## 本章重點整理

- 一條 `syscall` 指令的硬體動作只有三件：提權、跳 `MSR_LSTAR` 登記的入口、把回程存進 rcx/r11。換 GS、換 kernel stack、存 pt_regs 都是入口組語 `entry_SYSCALL_64` 手動做的。
- number 放 rax 當 `sys_call_table` 的**索引**（不是位址），第 4 參數放 r10（rcx 被硬體徵用）。單一入口 + 查表分派是特權隔離的骨架。
- `sys_call_table` 從 `arch/x86/entry/syscalls/syscall_64.tbl` **自動生成**；`SYSCALL_DEFINEn` 生 `__x64_`（解 pt_regs、進表）/`__se_`（型別）/`__do_`（你的本體）三層 wrapper，防 register 污染、統一介面。
- user 指標一律走 `copy_from_user`/`copy_to_user`（含 `access_ok` + fault 安全），**永不直接解參考**——這是使用者/核心邊界最硬的紀律，也是 kernel_pwn 一切攻防的起點。
- 加自訂 syscall 的正途是改 `.tbl` + `SYSCALL_DEFINE` + 重編；劫持 `sys_call_table` / kprobe 是 hack，留給觀測與 rootkit 視角。

## 自我檢核

- [ ] 不看筆記，能畫出 user `syscall` → `entry_SYSCALL_64` → `do_syscall_64` → `sys_call_table[nr]` → `__x64_sys_xxx` 的完整路徑
- [ ] 能解釋為什麼 syscall 的第 4 參數用 r10 不用 rcx
- [ ] 能解釋為什麼 `entry_SYSCALL_64` 必須用組語寫（SWAPGS/換棧/pt_regs 順序）
- [ ] 面試被問「kernel 為什麼不能直接解參考 user 指標」，你能講出 access_ok、SMAP、任意讀寫漏洞三個角度
- [ ] 能獨立加一條自訂 syscall（改 `.tbl` + `SYSCALL_DEFINE` + 重編）並在 QEMU 跑通
- [ ] 能說出 `SYSCALL_DEFINE4` 展開成哪三層、各層負責什麼、哪一層進 `sys_call_table`
- [ ] 能講清「改 `sys_call_table` / kprobe」為什麼不是加 syscall 的正途

## 延伸閱讀

### 官方文件與源碼

- **[Documentation/process/adding-syscalls.rst](https://www.kernel.org/doc/html/latest/process/adding-syscalls.html)**
  - **讀哪裡**：整篇。這是 kernel 官方教你怎麼正式新增一條 syscall 的權威文件，涵蓋 `.tbl`、`SYSCALL_DEFINE`、多架構、相容性、ABI 穩定性考量
  - **和本章的關聯**：本章「動手加 syscall」是它的最小可跑版；要把 syscall 弄到能上游、或加到 ARM64，照這篇補齊

- **`arch/x86/entry/entry_64.S` 的 `entry_SYSCALL_64` 與 `arch/x86/entry/common.c` 的 `do_syscall_64`**（v6.12）
  - **怎麼讀**：在 [Elixir v6.12](https://elixir.bootlin.com/linux/v6.12/source) 上搜這兩個符號。`entry_64.S` 讀組語入口那段（SWAPGS、切棧、PUSH_REGS），`common.c` 讀查表分派那段
  - **為什麼值得讀**：這是本章描述的機制的真身。組語看不懂沒關係，抓 SWAPGS→切棧→呼叫 do_syscall_64 的順序就夠

### 深入文章

- **[LWN: "System calls" 系列與 syscall wrapper 相關文章](https://lwn.net/)**（在 LWN 搜 "syscall wrappers"、"SYSCALL_DEFINE"）
  - **能學到什麼**：`SYSCALL_DEFINE` wrapper 為何演化成現在這樣、Spectre/Meltdown 後 entry 路徑的強化（pt_regs-based、KPTI CR3 切換）的第一手記述
  - **前提**：讀過本章、對 Spectre 類側信道有基本概念

- **[Linux Inside — System calls 章節](https://0xax.gitbooks.io/linux-insides/content/SysCall/)**（0xAX）
  - **讀哪裡**：System Call 那個 Part，尤其 "How the Linux kernel handles a system call"
  - **為什麼值得讀**：逐行拆 `entry_SYSCALL_64` 組語的最詳細中英文資源之一，補足本章沒展開的每條組語指令
  - **注意**：對應的 kernel 版本較舊，`entry_64.S` 細節（尤其 KPTI 相關）以 v6.12 為準

### 書籍

- **《Linux Kernel Development, 3rd Ed.》** — Robert Love，第 5 章 "System Calls"
  - **這章談什麼**：syscall 設計哲學、number 為何不能變、參數傳遞、`copy_*_user` 的必要性，用最好讀的方式講清「為什麼這樣設計」
  - **注意**：講的是舊 kernel（沒有 pt_regs-based wrapper、沒有 KPTI），機制細節以本章 6.12 為準，但設計思想歷久彌新

搞懂了 user/kernel 邊界怎麼跨，接下來要進 kernel 內部的「工具箱」——kernel 裡到處都在用的那幾個核心資料結構（鏈結串列、紅黑樹、xarray），它們是後面排程器、記憶體、VFS 一切子系統的地基。

→ [Ch 5 核心資料結構：list / rbtree / xarray](./05-core-data-structures.md)
