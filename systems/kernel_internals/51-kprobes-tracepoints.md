# Ch 51 — kprobes/tracepoints/uprobes 底層

> **目標**：理解「在不改源碼、不重編 kernel、不停機的情況下，把觀測點動態插進一顆正在跑的 kernel 任意位址」這件事底層是怎麼做到的。學完你能說清楚 kprobe 怎麼用 int3 攔截一條指令、tracepoint 為什麼沒掛時幾乎零成本、uprobe 怎麼把同一招用到 user space——以及 ftrace/perf/eBPF/bpftrace 都是站在這三個機制之上。

## 為什麼需要這個？

Ch 0 教你在 QEMU 裡用 gdb 停 kernel。那套很強，但有個致命前提：**你得能停下這顆 kernel**。生產環境的 kernel 你停不得——那是幾千個連線、幾萬個 request 正在跑的機器，`gdb` 一個中斷點下去把 CPU 凍住，服務就掛了。而且生產機通常也沒接 gdb stub。

所以生產環境的觀測需求是這樣的：

- **不能停**：機器要繼續服務，觀測是「旁路」——CPU 執行到觀測點時順手記一筆，然後繼續跑
- **不能改源碼重編**：你懷疑 `tcp_sendmsg` 有問題，不可能改一行 `printk` 進去、重編 kernel、重開機——那要幾小時，而且問題可能重開就消失了
- **要能插在任意位置**：問題可能在任何函式的任何一條指令上，你事先不知道要看哪

這正是**動態插樁（dynamic instrumentation）**要解決的事：在**運行中的 kernel** 上，動態地把觀測點插進去，用完拔掉，全程不停機、不改二進位檔（表面上）。

這章講的三個機制——**kprobes**、**tracepoints**、**uprobes**——是 Linux 所有觀測工具的共同地基。你在 `observability_tools` 課用過的 ftrace、`perf probe`，在 `bpf` 課寫過的 `kprobe:tcp_sendmsg { ... }`，底層全都掛在它們上面。`bpftrace` 一行 `kprobe:vfs_read { printf("%d\n", pid); }`，做的事就是「動態註冊一個 kprobe + 掛一段 BPF 當 handler」。這章把那個「動態註冊 kprobe」拆開看。

## 先建立直覺

你在 `gdb` 課學過軟體中斷點（software breakpoint）的原理：gdb 要在某位址停下，做法是**把那位址的第一個 byte 換成 `0xcc`（x86 的 `int3` 指令）**，存下原本的 byte；CPU 執行到那裡觸發 `#BP` 例外，控制權回到 gdb；gdb 要繼續時，把原 byte 換回去、單步執行原指令、再把 `0xcc` 補回去。

kprobe 用的是**一模一樣的招數**，只是舞台從「gdb 對著被 debug 的程式」搬到「kernel 對著它自己」。差別只在：觸發後跑的不是 gdb 的中斷處理，而是**你註冊的 handler 函式**；handler 跑完 kernel 自己讓被覆蓋的指令執行，然後繼續原本的執行流。整件事發生在**同一顆 kernel 內部**、**幾微秒內**、**不需要另一個 process 介入**。

```
   靜態插樁（tracepoint）                動態插樁（kprobe）
   ─────────────────────                ──────────────────
   開發者在源碼裡預先埋好              運行時把目標指令換成 int3
   trace_sched_switch(...)              哪都能插，函式改名就失效
   穩定 ABI、沒掛時零開銷                不穩定、開銷略高
   只在有埋的地方                        任意 kernel 指令位址
```

把這兩條路線先分清楚，這章後面所有東西都掛在這張圖上：**tracepoint 是「事先埋好的門」，kprobe 是「臨時鑿的洞」**。uprobe 則是把 kprobe 這招搬到 user space 程式上。

## kprobes：在任意 kernel 指令上攔截

源碼主體在 `kernel/kprobes.c`，架構相關的指令替換/single-step 在 `arch/x86/kernel/kprobes/core.c`，官方文件是 `Documentation/trace/kprobes.rst`。核心資料結構是 `include/linux/kprobes.h` 的 `struct kprobe`：

```c
struct kprobe {
    struct hlist_node hlist;        // 掛進全域 hash table（用位址當 key）
    kprobe_opcode_t *addr;          // 要插樁的位址
    const char *symbol_name;        // 或用符號名（register 時解析成 addr）
    unsigned int offset;            // 符號名 + offset，可插在函式中間

    kprobe_pre_handler_t pre_handler;   // 觸發後、執行原指令「前」跑
    kprobe_post_handler_t post_handler; // 執行原指令「後」跑

    kprobe_opcode_t opcode;         // 被覆蓋掉的原始指令 byte（存起來以便還原）
    struct arch_specific_insn ainsn;// 原指令的副本（給 single-step 用）
    u32 flags;
};
```

你要做的事就三步：填好 `addr`（或 `symbol_name`）、`pre_handler`、`post_handler`，然後呼叫 `register_kprobe(&kp)`。之後每次 CPU 執行到 `addr`，你的 `pre_handler` 就會被叫到，參數是 `struct pt_regs *`——那是**觸發當下所有暫存器的完整快照**，你能從裡面讀出函式參數（x86_64 前六個整數參數在 `rdi/rsi/rdx/rcx/r8/r9`，這是 Ch 4 syscall/ABI 那套 calling convention）。

### 底層機制：int3 替換與觸發流程

`register_kprobe` 幹的事，本質就是 gdb 下軟體中斷點那一套。`arch_arm_kprobe()` 把 `addr` 那一 byte 用 `text_poke()` 換成 `int3`（`0xcc`），原 byte 存進 `kp.opcode`。`text_poke` 是 kernel 專門用來**安全地改自己的 `.text`**（Ch 8 提過 kernel 程式碼段平常是唯讀的，改它得走特殊路徑，還要處理其他 CPU 正在跑同一段程式碼的問題）。

之後的執行流程：

```
   平常沒插樁：          插了 kprobe 後（addr 那 byte 變成 0xcc）：

   ...                   ...
   addr: mov %rdi,%rax   addr: int3 (0xcc)  ←── 原本的 mov 被存到 kp.opcode / ainsn
   ...                   ...

   CPU 執行到 addr → 撞上 int3 → 觸發 #BP 例外（向量 3，接 Ch 29 IDT）
        │
        ▼
   do_int3() → kprobe_int3_handler()   （arch/x86/kernel/kprobes/core.c）
        │
        │  1. 用 addr 去全域 hash table 找到對應的 struct kprobe
        │  2. 呼叫 p->pre_handler(p, regs)   ←── 你的程式碼在這裡跑！
        │                                         regs 是完整暫存器快照
        │  3. 設 single-step：把 CPU 指到「原指令的副本 ainsn」，開 TF flag
        ▼
   CPU 單步執行原指令（mov %rdi,%rax）的那份乾淨副本
        │  ←── 為什麼不直接把 0xcc 換回原指令執行再換回？那有 race：
        │      多核心下換回去的瞬間別的 CPU 可能剛好跑過那位址，漏抓。
        │      所以執行「另一份副本」，原位址的 0xcc 一直在，不動。
        ▼
   single-step 完 → #DB 例外 → kprobe_debug_handler()
        │  4. 呼叫 p->post_handler(p, regs, ...)  ←── 原指令執行後的觀測點
        │  5. 把 CPU 的 rip 修回 addr 的下一條指令，繼續原本的執行流
        ▼
   ...程式繼續正常跑，彷彿什麼都沒發生（只是慢了幾百 ns）
```

這裡最關鍵、也最容易被跳過的一點：**single-step 執行的是「原指令的乾淨副本」（`ainsn`），不是把 `0xcc` 換回去執行再換回來**。原因是 SMP race——如果你「暫時把原指令貼回位址、single-step、再貼回 `0xcc`」，那在「貼回原指令」到「補回 `0xcc`」這個窗口內，別的 CPU 執行到同一位址就會漏抓。執行副本則讓原位址的 `0xcc` 從頭到尾都在，任何 CPU 跑到那裡都會觸發。這是「把中斷點原理搬到多核心 kernel 自己身上」比 gdb 單一 process 難的地方。

> 有些原指令是相對跳轉、call、`rip`-relative 定址——搬到別的位址執行，位移就錯了。kprobe 對這類指令會做**指令模擬/改寫**（boost / instruction emulation，`arch/x86/kernel/kprobes/core.c` 裡的 `resume_execution` 和一堆 opcode 分析），把相對位移修正到副本的新位置。有極少數指令 kprobe 無法安全處理，`register_kprobe` 會直接拒絕。

`kprobe_int3_handler` 怎麼從「觸發的位址」找到「哪個 struct kprobe」？靠一個全域 hash table（`kernel/kprobes.c` 的 `kprobe_table`，以位址 hash 當 key）。這也是為什麼一個位址可以掛多個 kprobe——它們串在同一個 hash bucket 上。查表、跑 handler、single-step 這整段路徑本身**絕對不能再被 kprobe 攔**，否則觸發時無限遞迴。kernel 因此維護一份 **kprobe blacklist**（`NOKPROBE_SYMBOL` 標記 + `kprobe_blacklist`），把「int3 處理鏈上的所有函式」「中斷最前段」「kprobe 自己的內部函式」全列進去，`register_kprobe` 對這些位址一律拒絕。你在 `/sys/kernel/debug/kprobes/blacklist` 能看到完整清單——踩雷集錦第 1 條講的就是它。

### kretprobe：看函式的回傳值

kprobe 攔的是「進入某位址」。但你常常想看的是**函式回傳了什麼**——例如 `kmalloc` 到底回傳哪個位址、`vfs_read` 讀了幾個 byte。函式的返回點可能有很多個（多個 `return`），一個個插很麻煩。

**kretprobe**（`struct kretprobe`，`kernel/kprobes.c` 的 `pre_handler_kretprobe`）用了個聰明招：它在函式**入口**放一個 kprobe，觸發時把**原本的返回位址存起來**，然後把 stack 上的返回位址**改成一個 trampoline**（`kretprobe_trampoline`）。函式正常執行、正常 `ret`，但 `ret` 跳到的是 trampoline 而不是真正的呼叫者。trampoline 裡呼叫你的 `ret_handler`（此時 `regs->ax` 就是回傳值），跑完再跳回**真正存起來的返回位址**。

```
   呼叫者 → func()            正常：func ret 回呼叫者
            ↓ kretprobe 入口攔截
            存下真正返回位址 R，把 stack 上返回位址改成 trampoline T
            ↓
        func 正常執行 ... ret → 跳到 T（不是 R）
            ↓
        trampoline：叫 ret_handler(regs)  ←── regs->ax = 回傳值
            ↓
        跳回真正的 R，呼叫者無感繼續
```

每個「進入中的 func 實例」需要一份空間存返回位址（因為可能遞迴、可能多核心同時進），kretprobe 用 `maxactive` 預先配一池 `kretprobe_instance`。`maxactive` 設太小、同時進行的實例超過池子，就會 miss（`nmissed` 計數會漲）——這是 kretprobe 特有的踩雷點。

### optimized kprobe：用 jmp 取代 int3

`int3` 走的是**例外處理**路徑——觸發 `#BP`、進 IDT、存暫存器、查 handler、single-step、`#DB`……這一趟開銷不小（相對於原本一條指令）。對高頻函式（一秒觸發幾百萬次），這開銷會很有感。

於是有了 **optimized kprobe**（`CONFIG_OPTPROBES`，`arch/x86/kernel/kprobes/opt.c`）。它把 `int3` 那一 byte 的攔截，升級成**一條 5-byte 的相對 `jmp`**，直接跳到一段動態生成的 trampoline，trampoline 裡準備好 `pt_regs`、呼叫 handler、執行被取代的指令、再跳回來。省掉了整個例外進出的開銷，快很多。

代價是條件更苛刻：`jmp` 是 5 byte，會蓋掉目標位址後面好幾條指令，所以 kprobe 得確認「這 5 byte 範圍內沒有別的地方會被 jump 進來」（否則別人跳進 jmp 中間就爆了），還要處理其他 CPU 可能正在執行這段。滿足不了條件就**退回普通 int3 kprobe**。這是典型的 kernel 設計：能快就快，不能快就安全降級，對使用者透明。

> 還有一條路是 **KPROBE_ON_FTRACE**：如果插樁點剛好在函式入口、而該函式已經被 ftrace 埋了 `fentry` 的 nop 樁（見 Ch 53），kprobe 可以直接**復用 ftrace 那個樁**，連 int3 都不用。所以「插在函式入口」的 kprobe 通常最便宜。這也解釋了為什麼 `bpftrace kprobe:func`（插入口）比 `kprobe:func+0x40`（插函式中間）在很多機器上更快、更穩。

## tracepoints：源碼裡預先埋好的靜態樁

kprobe 哪都能插，但它有兩個根本問題：**不穩定**（你插 `tcp_sendmsg+0x40`，下個版本這函式一改，offset 就指到別的指令，甚至函式沒了）、**開銷不是零**（就算沒人用，指令還在那；要用時得動態改 `.text`）。

**tracepoint** 走另一條路：**讓開發者在源碼裡、關鍵事件處，預先埋好一個具名的觀測點**。定義在 `include/trace/events/` 下（例如排程事件在 `include/trace/events/sched.h`），核心巨集在 `include/linux/tracepoint.h`。你在 `bpf` 課和本課練習 B 用過的 `trace_sched_switch`、`trace_kmalloc`、`trace_sys_enter` 都是 tracepoint。

源碼裡長這樣（排程器每次切換 task 都會經過，Ch 14）：

```c
// kernel/sched/core.c 的 __schedule() 裡
trace_sched_switch(preempt, prev, next, prev_state);
```

`trace_sched_switch` 不是普通函式呼叫——它是 `DEFINE_EVENT`/`TRACE_EVENT` 巨集展開出來的東西。沒有人掛上去時，它**幾乎不做事**；一旦有工具（ftrace/perf/eBPF）掛了 callback 上去，每次執行到這行就會把 `prev`、`next` 等參數餵給所有掛著的 callback。

### 底層機制：static key / jump label 讓沒掛時零開銷

tracepoint 最漂亮的地方是「沒掛時幾乎零成本」。怎麼做到的？答案是 **static key / jump label**（`kernel/jump_label.c`，`include/linux/jump_label.h`，接 Ch 24 講過的 static branch）。

`trace_sched_switch` 展開後，核心是一個 `static_branch_unlikely(&__tracepoint_sched_switch.key)` 判斷：沒人掛時走「什麼都不做」的路，有人掛時走「呼叫所有 callback」的路。天真的做法是用一個全域 flag + `if`——但那每次都要 load flag、比較、branch，還會污染 branch predictor。static key 更狠：**它把這個判斷編譯成程式碼裡一個可以被運行時改寫的位置**。

```
   tracepoint 沒掛（預設）：            有工具掛上去後（runtime patch）：

   __schedule():                        __schedule():
     ...                                  ...
     nop           ←── 5-byte nop         jmp  trace_path   ←── nop 被改成 jmp
     （繼續正常流程）                      （繼續正常流程）
     ...                                  ...
                                        trace_path:
   沒掛時：那行就是個 nop，              呼叫所有掛著的 callback(prev, next, ...)
   CPU 幾乎零成本滑過去                   jmp 回原流程
```

沒掛時，判斷點是一條 **`nop`**——CPU 滑過去，沒有 load、沒有比較、沒有 branch mispredict，成本趨近於零。有人 `register` 上去時，`jump_label` 用 `text_poke` 把那條 `nop` **原地改寫成 `jmp`**，跳去執行 callback。拔掉時再改回 `nop`。

這就是為什麼 kernel 可以在**所有** tracepoint 都埋好的情況下出貨——反正沒掛就是一堆 nop，不影響效能；要用哪個才把哪個的 nop 翻成 jmp。這技術本身很通用，kernel 裡大量「預設關、極少開的功能開關」都用它（例如某些 debug feature、SELinux enforcing 判斷）。

> 這裡跟 kprobe 的 `text_poke` 是同一個底層能力（運行時改自己的 `.text`），但用途相反：kprobe 是**插入**攔截（nop/指令 → int3/jmp），jump label 是**啟用預埋的分支**（nop ↔ jmp）。理解「kernel 能安全地改寫自己正在執行的程式碼」是這整章的底層共識。

### tracepoint 的穩定性：為什麼它是「半個 ABI」

tracepoint 是開發者**刻意**放的、帶名字和明確參數（透過 `TRACE_EVENT` 定義欄位格式，導出到 `/sys/kernel/tracing/events/*/format`）。社群對它有**穩定性承諾**（不像內部函式說改就改，雖然不是鐵板一塊，偶爾也會變），所以你用 `tracepoint:sched:sched_switch` 寫的觀測工具，跨 kernel 版本大機率不會壞。這是它相對 kprobe 最大的價值：**穩定**。

代價是**只在開發者埋了的地方有**。想觀測一個沒埋 tracepoint 的函式？那就得回去用 kprobe。所以實務上的選擇是：**有 tracepoint 就優先用 tracepoint（穩定、便宜），沒有才用 kprobe（萬能、不穩）**。

## uprobes：把這招用到 user space

kprobe 攔 kernel 指令，那 user space 程式呢？你想觀測一個跑在生產機上的 `nginx`、`mysql`、或某個 Go 二進位檔的某個函式進出，不改它、不重啟它——這是 **uprobe**（`kernel/events/uprobes.c`）。

原理和 kprobe 一脈相承，但多了 user space 特有的複雜度：

- 你指定的是**檔案（inode）+ 檔案內 offset**，不是記憶體位址——因為同一個執行檔可能被很多 process map、位址各不同（ASLR），但檔案 offset 是共通的
- kernel 在那個 inode 對應的**可執行頁**上，把目標 offset 的 byte 換成 `int3`（一樣是 0xcc）。因為那頁是多個 process 共享的唯讀 code page，kernel 用 CoW（Ch 20）的機制處理：改的時候複製一份給插樁用
- 任何 process 執行到那條 user 指令 → `int3` → 陷入 kernel（`#BP`）→ `uprobe_pre_sstep_notifier` → 跑 handler（在 kernel 態）→ single-step 那條 user 指令（同樣用副本，放在一個特殊的 user 頁）→ 回到 user 態繼續

```
   user 程式的 .text（某函式）          uprobe 掛上後
   ────────────────────────            ─────────────
   0x1234: push %rbp                    0x1234: int3   ←── inode+offset 定位，改可執行頁
   0x1235: mov  %rsp,%rbp               （原指令存副本，single-step 用）

   process 跑到 0x1234 → int3 → 陷入 kernel → handler → single-step → 回 user
```

register 的路徑也和 kprobe 不同：`uprobe_register`（`kernel/events/uprobes.c`）把探針掛在 **inode** 上（用 rb-tree 依 offset 索引），而不是某個 process 的位址空間。之後靠 mmap hook——**任何 process map 這個檔案的可執行段時**，kernel 就在它的頁裡種 int3。這是為什麼你對一個 `libc.so` 掛 uprobe，系統上所有現在跑的、以及之後才啟動的、用到那個 libc 的 process 都會被觀測到：探針綁的是檔案，不是行程。

同樣有 **uretprobe** 看 user 函式回傳值，做法和 kretprobe 對應（改 user stack 上的返回位址到 trampoline）。uprobe 的開銷比 kprobe 更大，因為每次都要 user↔kernel 態切換兩趟，高頻 user 函式上掛 uprobe 要非常小心（`bpf` 課裡 USDT / uprobe 那節的效能警告就是這來的）。

> **USDT**（Userland Statically Defined Tracing）是 user space 版的 tracepoint——開發者在程式裡用 `DTRACE_PROBE` 埋好靜態樁，工具用 uprobe 掛上去。你在 `observability_tools`/`bpf` 課看過 `bpftrace -l 'usdt:*'`，那底層就是 uprobe 掛到 USDT 標記的位址。

## 它們怎麼被上層用

這章講的三個機制是**地基**，你平常直接碰的是蓋在上面的工具：

```
   bpftrace  perf  BCC  自寫工具
        │      │     │      │
        ▼      ▼     ▼      ▼
   ┌──────── ftrace / perf_events / eBPF（Ch 52）────────┐
   │  提供 kprobe_events、tracepoint 掛載、BPF program    │
   │  attach 的統一介面                                    │
   └──────────────────────────────────────────────────────┘
        │                    │                   │
        ▼                    ▼                   ▼
     kprobes            tracepoints           uprobes
   （本章）             （本章）             （本章）
```

- **ftrace**（Ch 53）：透過 `/sys/kernel/tracing/kprobe_events` 讓你不寫模組就能建 kprobe，觸發時把資訊寫進 ring buffer
- **perf**：`perf probe` 動態建 kprobe/uprobe，`perf record -e` 訂閱 tracepoint
- **eBPF**（Ch 52）：把一段驗證過的 BPF 程式**當作 kprobe/tracepoint/uprobe 的 handler** 掛上去。`bpftrace` 的 `kprobe:vfs_read { @[comm] = count(); }` 一行，做的就是「動態 register 一個 kprobe，pre_handler 是這段 BPF」

所以你在 `bpf` 課寫的每個 `kprobe:` / `tracepoint:` / `uprobe:` probe，回到本課看，都是這章三個底層機制的一次具現。反過來，理解了本章，你就懂為什麼 `bpftrace` 掛 tracepoint 幾乎不影響效能（static key）、掛高頻 kprobe 會有可測開銷（int3 例外或 jmp trampoline）、掛 uprobe 開銷最大（雙向態切換）。

## 動手：四種掛 kprobe 的方式

假設你 build 了 Ch 0 那顆 6.12（tracing 相關 config：`CONFIG_KPROBES`、`CONFIG_KPROBE_EVENTS`、`CONFIG_UPROBE_EVENTS`、`CONFIG_DYNAMIC_FTRACE` 通常 defconfig 就有；eBPF 那條要 `CONFIG_BPF_SYSCALL`）。以下在 QEMU 的 shell 裡跑。

**(1) ftrace kprobe_events——不寫模組，echo 到 sysfs**

```bash
cd /sys/kernel/tracing        # 舊路徑是 /sys/kernel/debug/tracing

# 建一個 kprobe：在 do_sys_openat2 進入時，抓第二個參數（檔名指標）當字串
echo 'p:myopen do_sys_openat2 filename=+0(%si):string' > kprobe_events
echo 1 > events/kprobes/myopen/enable
echo 1 > tracing_on
cat trace_pipe                # 現在系統上任何 open 都會印出檔名
```

`p:` = probe（進入），`+0(%si):string` = 從 `%rsi`（第二參數）指到的位址讀一個字串。這串語法就是 kprobe event 的參數擷取 DSL，`perf probe`/`bpftrace` 底層生成的也是這種。

**(2) kretprobe——看回傳值**

```bash
echo 'r:myret do_sys_openat2 ret=$retval' > kprobe_events
echo 1 > events/kprobes/myret/enable
cat trace_pipe                # ret= 就是 openat 回傳的 fd（或負的 errno）
```

`r:` = return probe，`$retval` 就是回傳值。

**(3) bpftrace——一行動態掛 kprobe + BPF handler（接 bpf 課）**

```bash
# 統計哪個 process 呼叫最多次 vfs_read
bpftrace -e 'kprobe:vfs_read { @[comm] = count(); }'

# kretprobe 看 vfs_read 讀了幾 byte 的分佈
bpftrace -e 'kretprobe:vfs_read { @bytes = hist(retval); }'
```

這兩行做的，就是本章講的「register_kprobe + pre_handler / kretprobe + ret_handler」，只是 handler 是 BPF、掛載走 eBPF 而非寫模組。

**(4) perf probe**

```bash
perf probe --add 'vfs_read count'     # 建一個 kprobe，擷取 count 參數
perf record -e probe:vfs_read -a sleep 5
perf script                            # 看每次觸發的紀錄
perf probe --del vfs_read
```

## 動手：寫一個 kprobe 模組

前面都是「用工具掛」，現在自己 `register_kprobe`，看清楚 handler 拿到的 `pt_regs`。這模組在 `do_sys_openat2` 入口攔截，印出被開的檔名。

```c
// kp_open.c
#include <linux/module.h>
#include <linux/kprobes.h>
#include <linux/kernel.h>
#include <linux/ptrace.h>
#include <linux/uaccess.h>

static struct kprobe kp = {
    .symbol_name = "do_sys_openat2",   // register 時解析成位址
};

// 進入 do_sys_openat2 時被叫到；regs 是完整暫存器快照
static int pre_handler(struct kprobe *p, struct pt_regs *regs)
{
    // x86_64 calling convention（Ch 4）：第 2 個參數在 rsi
    // do_sys_openat2(int dfd, struct open_how *how, ...) 的 filename
    // 實際簽名依版本而異，這裡示範怎麼從 regs 讀參數
    struct filename *fn = (struct filename *)regs->si;

    // 注意：這裡直接解參數是示範用；真實情境參數位置要對照該版本源碼
    pr_info("kp_open: pid=%d comm=%s openat entered (rsi=0x%lx)\n",
            current->pid, current->comm, regs->si);
    (void)fn;
    return 0;    // 回傳非 0 有特殊語義（表示你自己改了 rip），一般回 0
}

// 執行完被覆蓋的原指令後被叫到
static void post_handler(struct kprobe *p, struct pt_regs *regs,
                         unsigned long flags)
{
    // 這裡可以看原指令執行後的狀態；很多 kprobe 用不到 post_handler
}

static int __init kp_init(void)
{
    int ret;
    kp.pre_handler  = pre_handler;
    kp.post_handler = post_handler;

    ret = register_kprobe(&kp);       // 這裡就會 text_poke 把 int3 種下去
    if (ret < 0) {
        pr_err("kp_open: register_kprobe failed: %d\n", ret);
        return ret;
    }
    pr_info("kp_open: planted kprobe at %p\n", kp.addr);  // 印出實際位址
    return 0;
}

static void __exit kp_exit(void)
{
    unregister_kprobe(&kp);           // 把原指令還原（int3 換回原 byte）
    pr_info("kp_open: kprobe removed\n");
}

module_init(kp_init);
module_exit(kp_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("kprobe on do_sys_openat2");
```

用 Ch 0 那份 Makefile 編出 `kp_open.ko`，塞進 initramfs，在 QEMU 裡：

```bash
/ # insmod /kp_open.ko
kp_open: planted kprobe at 000000000xxxxxxx     # 這就是被種 int3 的位址
/ # cat /etc/hostname                            # 觸發一次 open
kp_open: pid=72 comm=cat openat entered (rsi=0x...)
/ # rmmod kp_open
kp_open: kprobe removed
```

**用 gdb 看 int3 真的被種進去了**（回到 Ch 0 的 gdb + QEMU 組合）：模組載入後，在 gdb 裡 `x/i <kp.addr>`，你會看到那位址第一個 byte 是 `int3`（`cc`），而不是原本的指令——這就是本章開頭那張圖在真實 kernel 上發生。`rmmod` 後再看，原指令回來了。這一步把「kprobe = kernel 對自己下軟體中斷點」從文字變成你親眼看到的東西，跟 `gdb` 課的軟體中斷點實驗完全同構。

## 對比與取捨

| 面向 | kprobe | tracepoint | uprobe |
|---|---|---|---|
| 插在哪 | 幾乎任意 kernel 指令位址 | 開發者在源碼預埋的點 | user 程式指令（inode+offset） |
| 底層機制 | int3 替換 / optimized jmp | static key（nop↔jmp） | int3 替換（user code page，CoW） |
| 沒掛時開銷 | 不適用（要用才動態插） | 趨近零（就是個 nop） | 不適用 |
| 觸發開銷 | 中（例外）或低（optprobe/on-ftrace） | 低（jmp+callback） | 高（雙向 user↔kernel 態切換） |
| 穩定性 | 低（函式改名/inline 就失效） | 高（半個 ABI，跨版本大多穩） | 依目標程式的符號穩定性 |
| 能看什麼 | 進入點參數；kretprobe 看回傳值 | 開發者定義的欄位（format 檔） | user 函式參數/回傳值 |
| 何時選它 | 沒 tracepoint 覆蓋、要看內部函式 | **有就優先**：穩定又便宜 | 觀測 user 程式不改不重啟 |

一句話決策：**能用 tracepoint 就別用 kprobe；kprobe 是 tracepoint 沒覆蓋到時的萬能備胎；uprobe 是把戰場延伸到 user space。**

## 踩雷集錦

1. **「kprobe 哪都能插」不是真的哪都能插**：函式被 `inline` 掉就沒有獨立入口可插；標了 `NOKPROBE_SYMBOL`(`kernel/kprobes.c` 用它保護「kprobe 自己的處理路徑」，例如中斷處理最前段）的函式不能插——否則 kprobe 觸發時又觸發 kprobe，無限遞迴把 kernel 弄死。這是為什麼 int3/#BP 處理鏈上的函式全被標成 NOKPROBE。

2. **插函式中間 offset 會隨版本漂移**：`kprobe:tcp_sendmsg`（入口）跨版本相對穩；`kprobe:tcp_sendmsg+0x40`（中間某指令）換個 kernel、甚至換個編譯器/優化等級，那 offset 就指到別的指令了。offset 探針是「快照當下這顆二進位」的，別當穩定介面用。

3. **kretprobe 的 maxactive 太小會漏抓**：同時「進行中」的函式實例超過 `maxactive`（遞迴、高並發、多核心同時進）就 miss，`nmissed` 會漲但你不會收到那幾次的回傳。高頻函式上用 kretprobe 記得調大 maxactive。

4. **把 tracepoint 開銷想成零是近似**：沒掛時是 nop（趨近零沒錯），但**掛上之後**每次觸發要跑所有 callback、可能寫 ring buffer，那是有成本的。「零開銷」只在**沒掛**時成立。

5. **高頻 uprobe 會拖垮目標程式**：uprobe 每次觸發兩趟態切換，插在一秒幾百萬次的 user 熱函式上，目標程式會明顯變慢。生產機上掛 uprobe 前先估觸發頻率，或用取樣/USDT 替代。

## 進階：再往深一層

- **kprobe 與 ftrace 的融合（fprobe / KPROBE_ON_FTRACE）**：現代 kernel 傾向讓「插在函式入口」的探針走 ftrace 的 `fentry` 樁而非 int3，更快更穩。`fprobe`（較新的介面）就是為「一次高效掛很多函式入口」設計，eBPF 的 `fentry`/`fexit` program 也是這條路——比傳統 kprobe 快，是 `bpf` 課裡 `fentry:` 比 `kprobe:` 更受推崇的原因。

- **為什麼 `int3` 而不是別的**：`int3` 是**單 byte**（`0xcc`）指令。單 byte 才能原子地覆蓋任意指令的第一個 byte 而不破壞相鄰指令的解碼——換成多 byte 的攔截指令，在改寫的瞬間別的 CPU 可能解碼到「半條舊半條新」的垃圾。這跟 gdb 用 int3 下軟體中斷點是同一個理由（Ch 29 中斷向量 3 就是保留給它的）。

- **面試常問**：「ftrace / perf / eBPF 三者關係？」——它們不是競爭關係，是**上層工具共用同一批底層 hook（kprobe/tracepoint/uprobe）**，各自提供不同的資料收集與程式化能力。「tracepoint 為什麼沒掛時零開銷？」——static key，nop 運行時 patch 成 jmp。「kprobe 怎麼在不停機下插入？」——text_poke 種 int3 + single-step 副本，答得出「為什麼執行副本而非原位址」加分。

- **CONFIG 與可觀測性的取捨**：發行版 kernel 通常開好 `KPROBES`/`UPROBES`/`FTRACE`/`BPF`，讓生產機能事後觀測；但這也是攻擊面（`kernel_pwn` 視角：能任意插樁的能力若落到攻擊者手上很危險，所以要 CAP_SYS_ADMIN/CAP_BPF、`kernel.perf_event_paranoid`、lockdown 管制）。觀測能力與安全永遠在拉扯。

## 動手練習

1. **看見 int3**：跑上面的 kprobe 模組，用 Ch 0 的 gdb + QEMU，`insmod` 前後各 `x/4i kp.addr` 一次，親眼確認第一個 byte 從原指令變成 `int3`（`cc`）、`rmmod` 後變回來。寫下你看到的兩組反組譯。

2. **kretprobe 抓回傳值**：把模組改成 kretprobe（`struct kretprobe` + `.handler`），掛 `kmalloc` 或 `do_sys_openat2`，印出回傳值。再用 `bpftrace -e 'kretprobe:do_sys_openat2 { @ = hist(retval); }'` 對照，確認兩種做法看到同一件事。

3. **tracepoint vs kprobe 的穩定性實驗**：用 `bpftrace -l 'tracepoint:sched:*'` 列出排程 tracepoint，掛 `tracepoint:sched:sched_switch` 印出 `args`。再用 `kprobe:__schedule` 掛同一個位置。想想：如果 kernel 把 `__schedule` 改名或 inline，哪個會壞、哪個不會？

4. **量開銷**：用 `bpftrace` 分別掛一個 tracepoint（如 `tracepoint:syscalls:sys_enter_read`）和一個高頻 kprobe（`kprobe:vfs_read`），跑一個 `dd if=/dev/zero of=/dev/null bs=1 count=1000000`，比較兩者對耗時的影響。感受 static key 便宜、int3 有成本。

## 本章重點整理

- **kprobe = kernel 對自己下軟體中斷點**：text_poke 把目標指令換 int3，觸發後跑你的 pre_handler、single-step 原指令的**副本**（避 SMP race）、跑 post_handler、繼續。kretprobe 改返回位址到 trampoline 看回傳值；optprobe 用 jmp 取代 int3 減開銷。
- **tracepoint = 源碼預埋的靜態樁**，靠 static key（nop↔jmp 運行時 patch）做到**沒掛時零開銷**，是半個穩定 ABI；kprobe 萬能但不穩，有 tracepoint 就優先用 tracepoint。
- **uprobe 把 int3 那招搬到 user space**（inode+offset 定位、CoW 改 code page），開銷最大因為每次雙向態切換；USDT 是 user 版 tracepoint。
- **ftrace/perf/eBPF/bpftrace 全都掛在這三個機制上**：`bpftrace kprobe:foo { ... }` = 動態 register kprobe + BPF handler。理解本章就懂各工具的效能特性從何而來。

## 自我檢核

- [ ] 不看筆記，能畫出 kprobe 從 int3 觸發到繼續執行的完整流程，並說明**為什麼 single-step 執行的是副本而非原位址**
- [ ] 能解釋 tracepoint 沒掛時為什麼幾乎零開銷（static key / nop↔jmp），以及它跟 kprobe 在穩定性上的根本差別
- [ ] 面試被問「kprobe 怎麼在不停機、不改源碼下插入觀測點」，能從 text_poke + int3 + single-step 講清楚，並連到 gdb 軟體中斷點原理
- [ ] 能說出 kretprobe / optprobe / KPROBE_ON_FTRACE 各自解決 kprobe 的什麼不足
- [ ] 能說清楚 `bpftrace kprobe:vfs_read { ... }` 一行背後，底層動了哪些本章講過的東西

## 延伸閱讀

### 官方文件

- **[Documentation/trace/kprobes.rst](https://www.kernel.org/doc/html/v6.12/trace/kprobes.html)**
  - **讀哪裡**：整篇。kprobe/kretprobe/optprobe 的設計、限制（哪些指令不能插）、API（`register_kprobe`）都在這，是本章 kprobe 段落的一手依據
  - **和本章關聯**：本章「int3 替換 + single-step 副本」的流程就是這篇的白話版；寫 kprobe 模組遇到 register 失敗回來查限制清單

- **[Documentation/trace/tracepoints.rst](https://www.kernel.org/doc/html/v6.12/trace/tracepoints.html)** 與 **[Documentation/trace/uprobetracer.rst](https://www.kernel.org/doc/html/v6.12/trace/uprobetracer.html)**
  - **讀哪裡**：tracepoint 的定義方式（`TRACE_EVENT`）與 uprobe event 的建立語法
  - **能學到什麼**：怎麼自己在源碼裡加一個 tracepoint、以及 `/sys/kernel/tracing` 的 uprobe DSL 全貌

### LWN 文章

- **[Kernel probes (kprobes)](https://lwn.net/Articles/132196/)** 與 **[An introduction to KProbes](https://lwn.net/Articles/132196/)** 一脈的 LWN kprobe 系列
  - **為什麼讀**：LWN 對「為什麼這樣設計」的解說比 Documentation 更有脈絡，尤其 optprobe、KPROBE_ON_FTRACE 的演進動機
  - **前提**：讀完本章對機制有整體印象後再讀，會更有共鳴

- **[The BPF/tracing relationship](https://lwn.net/Kernel/Index/#BPF)** — LWN 的 BPF/tracing 索引
  - **這是什麼**：eBPF 如何掛到 kprobe/tracepoint/uprobe、fentry/fprobe 演進的一手記錄，直接銜接 Ch 52 與 `bpf` 課

### 源碼與工具

- **[Bootlin: kernel/kprobes.c、kernel/jump_label.c、kernel/events/uprobes.c（v6.12）](https://elixir.bootlin.com/linux/v6.12/source/kernel/kprobes.c)**
  - **讀哪裡**：`register_kprobe`、`arch_arm_kprobe`（x86 在 `arch/x86/kernel/kprobes/`）、`static_branch_*`、`uprobe_register`
  - **怎麼配本章**：對照本章的資料結構與流程圖逐段讀，把「文字描述」對回真實源碼

- **《BPF Performance Tools》** — Brendan Gregg（Addison-Wesley, 2019）第 2、4 章
  - **定位**：從**使用者**角度講 kprobe/uprobe/tracepoint 怎麼用（bpftrace/BCC），和本章的**底層**視角互補；你在 `bpf`/`observability_tools` 課用的工具，這本是權威手冊

這章把「動態插樁」的地基打好了——kprobe/tracepoint/uprobe 是所有觀測工具的共同底層。下一章我們往上爬一層，看 kernel 怎麼把一段不受信任的 BPF 程式安全地當作這些 hook 的 handler：verifier 怎麼證明它不會弄壞 kernel、JIT 怎麼把它編成原生碼。

→ [Ch 52 Kernel 如何 host eBPF：verifier、JIT、hooks](./52-ebpf-host.md)
