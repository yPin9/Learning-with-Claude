# Ch 3 — Kernel 啟動流程：start_kernel 到 init

> **目標**：搞清楚從 `bzImage` 解壓、跳進 long mode，到 C 語言的 `start_kernel()` 接手、最後 kernel 執行第一支使用者空間程式（PID 1 的 `/sbin/init`）之間，kernel 到底做了哪些事、順序為什麼是這樣、以及 initcall 這個貫穿全 kernel 的初始化機制怎麼運作。學完你能用 gdb 停在開機路徑上任一點，看清「誰在什麼 context 建立了 PID 0/1/2」。

> **環境**：延續 Ch 0 的 QEMU + gdb。本章大量用 `break start_kernel` / `break rest_init` / `break kernel_init` 加 `backtrace` 觀測。x86_64 主線，ARM64 的入口差異在文末點一句。

## 為什麼需要這個？

如果你上過本 repo 的 `linux_boot`，故事講到「bootloader（GRUB 或 QEMU 自己）把 `bzImage` 載進記憶體、跳進去執行」就收尾了。那門課關注「怎麼把 kernel 開起來」；到此為止，kernel 對它來說是個黑盒——交棒完成，任務結束。

這一章接手那個黑盒。`bzImage` 拿到控制權之後、你在 shell 打第一個指令之前，中間發生的事情比大多數人以為的多得多：記憶體管理還沒建好（`kmalloc` 不能用）、排程器還不存在（沒有「下一個 task」的概念）、中斷是關的、連 `printk` 都還沒真正接上 console。kernel 要**在幾乎什麼基礎設施都沒有的狀態下，把自己的基礎設施一件一件搭起來**——而且順序錯一步就 panic，因為後面的初始化依賴前面的。

理解這條路徑的實際價值：

- **開機卡住 / panic 的 debug**：`Kernel panic - not syncing: No working init found` 是最經典的一種，你要知道 kernel 在哪一步、用什麼順序去找 init，才能推理為什麼找不到。
- **理解「初始化順序」這個貫穿全 kernel 的問題**：你後面每寫一個模組、每註冊一個驅動，都在往這條路徑上的 initcall 機制掛東西。不懂 initcall 分級，你不會知道自己的 `module_init` 到底什麼時候、相對於誰被呼叫。
- **PID 0/1/2 的身世**：`ps` 看到的 PID 1（`systemd` 或 `init`）、PID 2（`kthreadd`）、還有那個 PID 0 的 idle task，全都在這一章誕生。這是 Part 2（process 與排程）的起點。

## 先建立直覺

先把整條路徑的骨架畫出來，後面每一節都是在填這張圖的細節：

```
  bootloader（GRUB / QEMU -kernel）
        │  載入 bzImage，跳進去
        ▼
  ┌─────────────────────────────────────────────────────────┐
  │ bzImage = [setup 實模式程式] + [壓縮的 vmlinux + 解壓 stub] │
  │   實模式 setup → 切 protected mode → 切 long mode（64-bit） │
  │   解壓 stub 把真正的 vmlinux 解壓到記憶體                    │
  └─────────────────────────────────────────────────────────┘
        │  跳進解壓後的 vmlinux 入口
        ▼
  arch/x86/kernel/head_64.S     ← 組語：設頁表、切到 kernel 的堆疊、清 BSS
        │  最後 call
        ▼
  start_kernel()   （init/main.c）  ← C 世界從這裡開始，還在 PID 0 的身分裡
        │  一連串子系統初始化（setup_arch / mm / sched / irq / time …）
        │  最後 call
        ▼
  rest_init()      （init/main.c）
        │
        ├── 建 PID 1：kernel_init  ──► do_initcalls() ──► 最後 execve /sbin/init（進使用者空間）
        │
        └── 建 PID 2：kthreadd    ──► 所有 kernel thread 的祖先
        │
        └── 當前這條路自己變成 PID 0 的 idle task（swapper），無事可做就 cpu_idle
```

三個關鍵的心智模型，記住它們這章就通了一半：

1. **執行 `start_kernel` 的那條執行流，本身就是 PID 0**。它不是被誰 fork 出來的，它是 kernel 開機時「憑空存在」的第一個 task——`init_task`，靜態編進 kernel 的。它做完所有初始化後不會消失，而是**降格成 idle task（swapper）**，在沒有別的 task 可跑時佔著 CPU。
2. **PID 1 和 PID 2 是 PID 0 在 `rest_init()` 裡親手 fork 出來的**。PID 1（`kernel_init`）注定要變成使用者空間的第一支程式；PID 2（`kthreadd`）注定當所有 kernel thread 的媽。
3. **initcall 是「延遲初始化」的登記簿**。`start_kernel` 不可能把幾千個子系統/驅動的初始化都手寫在函式裡，所以 kernel 用一套 linker 魔法，讓每個子系統把自己的 init 函式「登記」到不同等級的表裡，開機到某個時間點統一按等級順序呼叫。

## 交棒點：從 bzImage 到 head_64.S

`linux_boot` 課的終點是我們這章的起點，先把交界處講清楚。`bzImage` 不是「kernel 本體」，它是個複合體：

```
bzImage
 ├─ arch/x86/boot/     ← 實模式 setup 程式（16-bit），bootloader 直接跳這裡
 │    header.S / main.c：搜集記憶體佈局、切到 protected mode
 └─ arch/x86/boot/compressed/   ← 解壓 stub + 壓縮的 vmlinux
      head_64.S：切到 long mode（64-bit paging）
      misc.c 的 extract_kernel()：把壓縮的 vmlinux 解壓到記憶體，再跳進去
```

也就是說，**你在 gdb 用 `vmlinux` 除錯時，看到的是解壓「之後」的 kernel**。解壓 stub（`arch/x86/boot/compressed/`）跑在解壓前，那段用你的 `vmlinux` 符號是停不到的——它是另一份程式碼。這章的 gdb 觀測全部從解壓之後、也就是 `arch/x86/kernel/head_64.S` 之後才有意義。這也解釋了 Ch 0 為什麼強調「QEMU 吃 bzImage、gdb 吃 vmlinux」——它們是同一次 build 的兩個階段的產物。

解壓完成後，控制權跳進**未壓縮 vmlinux 的組語入口** `arch/x86/kernel/head_64.S`（符號 `startup_64` / 之後的 `secondary_startup_64`）。這段組語做的是 C 語言跑起來的最低前提：

- 建立**早期頁表**（identity mapping + kernel 高位映射），讓分頁機制能運作（詳細的 page table 結構是 Ch 16）
- 切到 kernel 自己的**堆疊**（`init_task` 的 stack，`current` 現在指向 `init_task`——`current` 怎麼運作是 Ch 2）
- 清空 **BSS**（未初始化全域變數區歸零）
- 設好最基本的 GDT/IDT 佔位

這些做完，`head_64.S` 最後一步是 `call x86_64_start_kernel`（`arch/x86/kernel/head64.c`），它再做幾件 x86 特定的早期設定（清早期頁表殘留、設 `cr4` 等），最後 `call start_kernel()`。C 的世界正式開始。

> **不要在組語這段糾結太久**。它的存在只是為了「讓 C 能跑」——頁表、堆疊、BSS 三件事。真正有教育價值的設計都在 C 這邊。`linux_boot` 課對這段組語有逐行版本，想深挖回那門課；這門課從 `start_kernel` 開始才是主場。

## start_kernel()：C 世界的第一個函式

`start_kernel()` 在 `init/main.c`（這是少數幾個「眾所周知」的位置，值得記住）。它是一條**又長又線性的函式**，一路呼叫幾十個 `*_init()`。新手容易把它讀成流水帳；正確的讀法是抓住「哪幾個是奠基性的、彼此有依賴順序」。

打開 `init/main.c` 的 `start_kernel()`，挑出這幾個里程碑（順序就是源碼裡的順序，順序本身是重點）：

```c
asmlinkage __visible void __init __no_stack_protector start_kernel(void)
{
    ...
    set_task_stack_end_magic(&init_task);   // 在 init_task 堆疊尾放金絲雀，抓爆棧
    ...
    setup_arch(&command_line);              // ★ 架構相關的重頭戲
    ...
    trap_init();                            // 設 CPU 例外處理（IDT）
    mm_core_init();                         // ★ mm 早期核心初始化（內部做 build_all_zonelists、buddy/slab）
    ...
    sched_init();                           // ★ 排程器骨架就位
    ...
    early_irq_init();  init_IRQ();          // 中斷子系統
    ...
    time_init();                            // 計時硬體、tick
    ...
    console_init();                         // printk 從這裡起真正輸出到 console
    ...
    mm_init_cpumask ... kmem_cache_init_late();  // slab 完全可用
    ...
    rest_init();                            // ★ 不返回：分岔出 PID 1 / PID 2
}
```

（欄位順序以 v6.12 的 `init/main.c` 為準，函式名可能隨版本微調；上面標 ★ 的是奠基里程碑。）

逐個里程碑講「它做什麼、為什麼在這個位置」：

**`setup_arch(&command_line)`**（`arch/x86/kernel/setup.c`）——這是 `start_kernel` 裡最重的一步。它解析 bootloader 傳來的參數、探測記憶體佈局（e820 map）、建立 early memory allocator（`memblock`，在 buddy allocator 起來前的臨時記憶體分配器）、初始化 early page table、抓 CPU 特性。**mm 的一切都建立在 `setup_arch` 探測出的記憶體佈局上**，所以它必須早。這一步同時也是 x86 vs ARM64 差最多的地方（各有各的 `setup_arch`）。

**mm 早期初始化**（`build_all_zonelists`、`mm_core_init`）——把物理記憶體切成 zone、建立 buddy allocator 的 free lists、初始化 slab。**在這之前 `kmalloc` 不能用**，只能用 `memblock`。這是為什麼很多超早期的初始化只能用靜態陣列或 `memblock_alloc`。buddy allocator 是 Ch 17、slab/slub 是 Ch 18 的主題，這裡你只需要知道「mm 在 `start_kernel` 中段才完整可用」。

**`sched_init()`**（`kernel/sched/core.c`）——建立每個 CPU 的 run queue（`struct rq`）、初始化各 scheduler class、把當前這條執行流（`init_task`）設成 CPU 0 的 idle task 的雛形。做完這步，「排程」這個概念才存在。但注意——**此刻還不能真的排程**，因為 preemption 還關著、也還沒有別的 task 可切。排程器框架是 Ch 11、CFS/EEVDF 是 Ch 12–13。

**`trap_init()` / `early_irq_init()` / `init_IRQ()`**——設定 CPU 例外向量（除以零、page fault 等）與外部中斷控制器（x86 的 APIC、ARM64 的 GIC，見 Ch 29）。在這之前中斷是**全域關閉**的（`local_irq_disable` 的狀態）；`start_kernel` 接近尾聲才 `local_irq_enable()`。

**`time_init()`**——初始化計時硬體與 tick，`jiffies` 開始跳動（時間子系統是 Ch 32）。

**`console_init()`**——注意它相對晚。這解釋了一個常見困惑：**`start_kernel` 早期的 `printk` 不是即時印出來的**，而是先塞進 kernel 的 log ring buffer（`printk` 用的環形緩衝），等 `console_init()` 把 console driver 接上後才一次刷出。所以你在 QEMU 看到的開機訊息，最前面那批其實是「補印」的。

**為什麼順序不能亂**：這條線性函式的順序不是隨意的，是**依賴鏈**。`sched_init` 要用到 mm（run queue 要配記憶體），所以 mm 早期初始化在它前面；中斷處理要能配記憶體存 handler 資料，所以也在 mm 之後；`console_init` 要 mm 起來才好配 buffer。你可以把 `start_kernel` 讀成一張「拓撲排序過的依賴圖被攤平成一條線」。

## rest_init()：PID 1 與 PID 2 誕生

`start_kernel` 的最後一行是 `rest_init()`（也在 `init/main.c`），而且它**永遠不返回**。這是整個開機路徑最精巧的一步，值得逐行讀。簡化後的骨架：

```c
noinline void __ref __noreturn rest_init(void)
{
    struct task_struct *tsk;
    int pid;

    rcu_scheduler_starting();          // RCU 開始正常運作（Ch 27）

    // ① fork 出 PID 1，進入點是 kernel_init
    pid = user_mode_thread(kernel_init, NULL, CLONE_FS);
    ...
    // ② fork 出 PID 2，進入點是 kthreadd
    pid = kernel_thread(kthreadd, NULL, NULL, CLONE_FS | CLONE_FILES);
    rcu_read_lock();
    kthreadd_task = find_task_by_pid_ns(pid, &init_pid_ns);
    rcu_read_unlock();
    ...
    complete(&kthreadd_done);          // 通知：kthreadd 建好了

    // ③ 打開 preemption，當前這條流降格成 idle
    schedule_preempt_disabled();
    cpu_startup_entry(CPUHP_ONLINE);   // ← 進 idle loop，不返回
}
```

三個階段：

**① 建 PID 1（`kernel_init`）**。`user_mode_thread` 是 `kernel_thread` 的變體，fork 出的 task 之後會轉去執行使用者空間程式。此刻 `kernel_init` 還是個 kernel thread，但它的宿命是變成 `/sbin/init`。fork 的底層（`copy_process`）是 Ch 10 的主題。

**② 建 PID 2（`kthreadd`）**。`kthreadd`（`kernel/kthread.c`）是**所有 kernel thread 的祖先**——之後任何 `kthread_create` 建立的 kernel thread（`kworker`、`ksoftirqd`、`kswapd`……）都是它的子孫。為什麼要有一個統一的祖先？因為 kernel thread 不能隨便從任意 context fork（會繼承錯誤的資源），統一由 `kthreadd` 在乾淨的 kernel context 裡代工，資源乾淨可控。`complete(&kthreadd_done)` 是同步點：`kernel_init` 後面會 `wait_for_completion(&kthreadd_done)`，確保 `kthreadd` 就緒後才繼續。

**③ 當前這條流變成 idle**。`cpu_startup_entry(CPUHP_ONLINE)` 進入 CPU idle loop，**永不返回**。這正是 PID 0 的最終歸宿——它把自己降格成 swapper/idle task，之後只在「沒有任何別的 task 可跑」時被排程器選中，通常就是執行 `hlt` 讓 CPU 省電。idle task 的細節是 Ch 9。

畫成圖，這一步是整條路徑唯一的「分岔」：

```
     rest_init()（身分：PID 0 / init_task）
          │
          ├──① fork──►  PID 1  kernel_init ──（見下一節）──► execve → 使用者空間
          │
          ├──② fork──►  PID 2  kthreadd ──► 之後所有 kernel thread 的爸媽
          │
          └──③ 自己 ──►  PID 0  idle / swapper（cpu idle loop，不返回）
```

## initcall 機制：延遲初始化的登記簿

`start_kernel` 手寫呼叫的只是最核心的那批子系統。剩下**幾千個**驅動、檔案系統、網路協定的初始化怎麼辦？不可能全塞進 `start_kernel`。kernel 的解法是 **initcall**：一套用 linker section 實作的「登記 + 分級呼叫」機制。

先看你天天用的那個宏。你在 Ch 0 寫的模組用 `module_init(hello_init)`——當這段程式**編進 kernel（built-in）而非當模組載入**時，`module_init` 其實展開成 `__initcall`，把 `hello_init` 的位址放進一個特殊的 linker section。built-in 的驅動不會有人手動呼叫它的 init，它靠 initcall 機制在開機時被統一叫到。

initcall 分成幾個**等級（level）**，定義在 `include/linux/init.h`，實際的呼叫等級與宏對應大致是：

| 等級（宏） | section | 典型用途 |
|---|---|---|
| `early_initcall` | `.initcallearly` | 最早，setup 剛完就要跑的 |
| `core_initcall` | `.initcall1` | 核心子系統（如 sysfs、部分 mm） |
| `postcore_initcall` | `.initcall2` | 依賴 core 的基礎設施 |
| `arch_initcall` | `.initcall3` | 架構相關的裝置初始化 |
| `subsys_initcall` | `.initcall4` | 子系統（如各 bus、網路子系統） |
| `fs_initcall` | `.initcall5` | 檔案系統註冊 |
| `device_initcall` | `.initcall6` | 大多數裝置驅動（`module_init` built-in 時等於這級） |
| `late_initcall` | `.initcall7` | 最後，依賴前面全部就緒的 |

linker script（`include/asm-generic/vmlinux.lds.h` 的 `INIT_CALLS`）把這些 section **按等級順序**排在一起，形成一個大陣列 `[__initcall_start, __initcall_end)`。開機時，`kernel_init`（PID 1）呼叫的 `kernel_init_freeable()` → `do_initcalls()`（`init/main.c`）就是**從頭到尾走這個陣列、依序呼叫每個登記進來的函式**：

```c
// init/main.c，簡化
static void __init do_initcalls(void)
{
    int level;
    for (level = 0; level < ARRAY_SIZE(initcall_levels) - 1; level++)
        do_initcall_level(level, ...);   // 一級一級呼叫
}
```

這解答了一個你遲早會撞到的問題：**「為什麼我的驅動 A 在驅動 B 之前跑？」**——因為 A 用了較早等級的 initcall 宏（如 `subsys_initcall`），或者兩者同級時，取決於它們在 linker section 裡的順序（通常是**編譯連結順序**，也就是 Makefile 裡 object 的排列）。想控制順序，選對等級的宏，而不是祈禱連結順序。

> **built-in vs module 的雙面性**：同一個 `module_init(foo_init)`，編進 kernel 時走 initcall（開機時 `do_initcalls` 叫它），編成 `.ko` 動態載入時走的是模組載入路徑（`insmod` 觸發，Ch 8）。同一個宏，兩條命運。這是 kernel「一份源碼、兩種存在形式」設計的縮影。驅動註冊怎麼掛進 device model 是 Ch 37。

## kernel_init：PID 1 轉生成使用者空間

回到 PID 1。`kernel_init`（`init/main.c`）是 `rest_init` fork 出來的那條流，它做兩大件事：

**第一：跑完剩下的初始化**。`kernel_init` 先呼叫 `kernel_init_freeable()`，裡面包含 `do_initcalls()`（上一節那個），把所有 built-in 驅動/子系統初始化完。做完後，kernel 會**釋放 `__init` 標記的記憶體**——所有標了 `__init`（如 `start_kernel`、各 `*_init` 函式）的程式碼與 `__initdata` 資料，開機後就沒用了，kernel 把這幾 MB 收回。這是為什麼你在 gdb 開機後想 `break start_kernel` 再觸發會失敗——那段碼已經被釋放了。

**第二：找到並執行使用者空間的 init**。這是整條開機路徑的終點。`kernel_init` 依序嘗試：

```c
// init/main.c 的 kernel_init()，簡化
if (ramdisk_execute_command) {          // 通常是 "/init"（initramfs 裡的）
    ret = run_init_process(ramdisk_execute_command);
    if (!ret) return 0;                  // 成功就不返回了（已變使用者空間）
}
if (execute_command) {                   // 命令列 init= 指定的
    ret = run_init_process(execute_command);
    ...
}
// 都沒有，退回一組候選路徑
if (!run_init_process("/sbin/init") ||
    !run_init_process("/etc/init")   ||
    !run_init_process("/bin/init")   ||
    !run_init_process("/bin/sh"))
    return 0;

panic("No working init found.  Try passing init= option ...");
```

順序與來源：

1. **initramfs 的 `/init`**（`ramdisk_execute_command`）。kernel 在 `kernel_init` 前已經把 initramfs 解開到 rootfs（`init/initramfs.c` 的 `populate_rootfs`，透過 initcall 觸發）。你在 Ch 0 寫的那個 `initramfs/init` 就是被這一步執行的——這解釋了為什麼 Ch 0 的 initramfs 一定要有一個 `/init`。
2. 命令列 `init=` 指定的路徑（如 `init=/bin/sh`，救援常用）。
3. rootfs 上的一組候選：`/sbin/init`、`/etc/init`、`/bin/init`、`/bin/sh`。真實系統這裡命中的通常是 `/sbin/init`，多半是 `systemd` 的 symlink。
4. **全都失敗 → `panic("No working init found")`**。這就是文章開頭那個經典 panic。你現在能推理它：initramfs 沒有可執行的 `/init`、或 rootfs 沒 mount 上、或 `init=` 指錯路徑。

關鍵一步是 `run_init_process` → 底層的 `kernel_execve`（`fs/exec.c` 的 `do_execve` 家族，syscall 版是 Ch 4）。**`execve` 成功後不返回**——它把當前這個 PID 1 的整個位址空間換成 `/sbin/init` 的程式碼。從這一刻起，PID 1 不再是 kernel thread，它是一支**使用者空間程式**，是系統上所有其他 process 的祖先（`linux_commands` 課裡你 `pstree` 看到根部的那個 `systemd`）。

一句話收束整條路徑：**PID 0 開機、建了 PID 1 和 PID 2、自己去當 idle；PID 1 跑完 initcall 然後 execve 成使用者空間的 init；kernel 的開機到此結束，接下來的世界由使用者空間接管。**

## 動手：用 gdb 走一遍開機路徑

延續 Ch 0 的環境（QEMU `-S -s` 凍住、gdb 連上）。這一節把三個關鍵停點串起來看。

```gdb
(gdb) target remote :1234
(gdb) source vmlinux-gdb.py

(gdb) break start_kernel
(gdb) break rest_init
(gdb) break kernel_init
(gdb) continue
```

停在 `start_kernel`：

```gdb
Thread 1 hit Breakpoint 1, start_kernel () at init/main.c:...
(gdb) print init_task.pid            # 應該是 0 —— 當前身分就是 PID 0
$1 = 0
(gdb) print init_task.comm           # "swapper/0"
(gdb) backtrace                      # 上面是 x86_64_start_kernel → head_64.S 的殘影
```

`print init_task.pid` 拿到 0，親眼確認「執行 `start_kernel` 的這條流就是 PID 0」。`continue` 到 `rest_init`：

```gdb
(gdb) continue
Thread 1 hit Breakpoint 2, rest_init () at init/main.c:...
(gdb) lx-ps                          # 此刻應只有 PID 0（PID 1/2 還沒 fork）
```

在 `rest_init` 裡對 `user_mode_thread` 那行下手，單步過 fork，再 `lx-ps` 就能看到 PID 1、PID 2 陸續出現。`continue` 到 `kernel_init`：

```gdb
(gdb) continue
Thread 1 hit Breakpoint 3, kernel_init (...) at init/main.c:...
(gdb) print (int)current->pid        # 這裡是 1 —— 我們已經在 PID 1 的 context
```

注意 `start_kernel`/`rest_init` 時 `current` 是 PID 0，到 `kernel_init` 時 `current` 變成 PID 1——**因為 `kernel_init` 是新 fork 出來的那條流在跑**，不是同一條。這是理解「fork 出一條新執行流」最直接的證據（`current` 的機制見 Ch 2）。

想看 `execve` 那一刻，對 `run_init_process` 下中斷點，`backtrace` 能看到 `kernel_init` → `kernel_init_freeable`（已跑完）→ 準備 `execve`。過了 `execve`，PID 1 就變成使用者空間程式，你的 QEMU console 會出現 Ch 0 那個 `>>> Hello from ...` ——那正是 `/init` 開始跑的證據。

## 對比與取捨：initramfs 直接當 rootfs vs 真實開機

| 面向 | Ch 0 的 initramfs 當根 | 真實發行版開機 |
|---|---|---|
| PID 1 執行誰 | initramfs 裡的 `/init`（busybox sh） | initramfs `/init` 先跑，再 `switch_root` 到磁碟上的 `/sbin/init`（systemd） |
| 為什麼要 initramfs | 學習用，直接給 shell | 磁碟 rootfs 可能在需要驅動/解密/組 RAID 的裝置上，initramfs 先載入這些驅動、mount 真正的 root，再交棒 |
| root 怎麼來 | 全在記憶體，不 mount 磁碟 | initramfs 的 `/init` 負責 mount 真 root，然後 `exec switch_root /sbin/init` |
| 適合 | 教學、救援、embedded | 通用桌面/伺服器 |

重點：**kernel 這一側的路徑完全一樣**——`kernel_init` 一律先找 initramfs 的 `/init`。差別在使用者空間的 `/init` 自己選擇「就當 PID 1 跑下去」（我們的教學版）還是「mount 真 root 再 `switch_root` 交棒」（發行版）。kernel 不管這個決定，它交完棒就去 idle 了。

## 踩雷集錦

1. **「`start_kernel` 是 kernel 的 `main()`」——半對半錯**。它確實是 C 世界的第一個函式，但它前面有 `head_64.S` 的組語、更前面有解壓 stub。而且它不返回（尾巴進 `rest_init` 再進 idle loop），跟 `main()` 返回就結束的語意相反。

2. **以為 `printk` 開機一開始就即時輸出**。`console_init` 相對晚，之前的 `printk` 全塞在 ring buffer 補印。你若在超早期插一個 `printk` 想 debug 開機，看到的時間點會「錯位」——它不是沒執行，是還沒有 console 可印。

3. **把 PID 1 和 PID 0 搞混**。PID 0（`init_task`/swapper）是跑 `start_kernel` 的那條、最後變 idle；PID 1（`kernel_init`→`/sbin/init`）是被 fork 出來、注定去使用者空間的。兩者身分、命運、context 都不同。`ps` 看不到 PID 0（它是 idle，不在一般 task list 顯示），但 `lx-ps` 在 gdb 裡看得到。

4. **`No working init found` panic 歸咎於 kernel bug**。這幾乎永遠是使用者空間/開機參數問題：initramfs 沒 `/init` 或它不可執行、rootfs 沒 mount、`init=` 指錯、或 `/sbin/init` 依賴的 libc 不在 initramfs 裡。從 `kernel_init` 找 init 的那組候選路徑往回推。

5. **以為 `module_init` 一定「載入時」被呼叫**。built-in 時它是開機 `do_initcalls` 叫的、時間點由 initcall 等級決定，跟 `insmod` 無關。只有編成 `.ko` 動態載入時才是「載入時」。搞混會讓你誤判自己 init 函式的執行時機。

## 進階：再往深一層

- **`initcall_debug` 開機參數**：`-append "... initcall_debug"` 會讓 kernel 印出**每一個 initcall 的名字與耗時**。這是 debug「開機為什麼慢」「哪個驅動 init 卡住/失敗」的第一手工具，配 `systemd-analyze` 從使用者空間互補。想親眼看 `do_initcalls` 的順序，開這個。
- **SMP：其他 CPU 什麼時候上線**。`start_kernel` 跑在 CPU 0（boot CPU）。其他 CPU（AP，application processor）是後來由 `smp_init()` 透過 `secondary_startup_64`（`head_64.S` 的另一入口）一顆一顆喚醒的，每顆也各有自己的 idle task。SMP 起來與 CPU hotplug 是 Ch 15。
- **`__init` / `__initdata` 的省記憶體設計**：這些標記讓開機用完即棄的程式碼/資料被放進特殊 section，開機後 `free_initmem()` 整段釋放。這是 kernel「連幾 MB 都要摳」的典型。面試會問「為什麼開機後 `start_kernel` 的符號還在 vmlinux 卻停不到中斷點」——答案就是這段記憶體被回收了。
- **x86 vs ARM64 的入口差異（一句）**：x86_64 走 `arch/x86/kernel/head_64.S` 的 `startup_64`；ARM64 走 `arch/arm64/kernel/head.S` 的 `primary_entry`，各自建早期頁表、切到對應的 exception level（ARM64 是 EL1），但**兩者最後都匯流到同一個 `start_kernel()`**——`init/main.c` 是架構無關的。架構差異被 `setup_arch()` 這層吸收掉了，這是 Linux「架構無關核心 + 架構相關薄層」設計的縮影。
- **面試常問**：「PID 0/1/2 分別是什麼、誰建了誰？」「`start_kernel` 到 `/sbin/init` 之間的關鍵步驟？」「built-in 驅動的 init 什麼時候被呼叫、順序怎麼決定？」——這章全部回答得了。

## 動手練習

1. **三停點走一遍**：`break start_kernel` / `rest_init` / `kernel_init`，各自 `print current->pid`（或 `init_task.pid`），親手確認 PID 從 0 變到 1 的那一刻。用 `lx-ps` 在三個停點各看一次 task list，觀察 PID 1/2 何時出現。

2. **看 initcall 順序**：開機參數加 `initcall_debug`，開機後 `dmesg | grep initcall`（或直接看 QEMU console），找出幾個你認得的子系統，確認它們的先後符合 initcall 等級。挑一個問自己「它為什麼在這個位置」。

3. **弄壞 init，複現經典 panic**：把 Ch 0 的 `initramfs/init` 改成不可執行（`chmod -x`）或直接刪掉，重打包 initramfs，開機——你應該會看到 `No working init found` panic。然後用 `-append "... init=/bin/busybox"` 之類的方式救回來，體會 `kernel_init` 找 init 的那組 fallback。

4. **停在 execve 那一刻**：`break run_init_process`，`backtrace` 看它從 `kernel_init` 一路下來的呼叫鏈；`continue` 過去後 QEMU 出現你的 initramfs 訊息，理解「這一步之後 PID 1 已經是使用者空間程式」。

## 本章重點整理

- 開機路徑：解壓 stub → `arch/x86/kernel/head_64.S`（頁表/堆疊/BSS）→ `start_kernel()`（`init/main.c`，C 世界起點）→ `rest_init()` → 分岔出 PID 1/2 並自降為 PID 0 idle。
- `start_kernel` 是一條被依賴關係攤平的線性初始化：`setup_arch` → mm → `sched_init` → 中斷 → 時間 → console，順序不能亂，因為後者依賴前者。
- `rest_init` 建 PID 1（`kernel_init`，注定 execve 成使用者空間 init）與 PID 2（`kthreadd`，所有 kernel thread 的祖先）；跑 `start_kernel` 的那條流本身是 PID 0（`init_task`/swapper），最後進 idle loop 不返回。
- initcall 是用 linker section 實作的分級延遲初始化登記簿；`do_initcalls()` 按 early→core→…→late 順序呼叫所有 built-in 的 init 函式。`module_init` 在 built-in 時就是登記一筆 initcall。

## 自我檢核

- [ ] 不看筆記，能畫出 `head_64.S → start_kernel → rest_init → {PID1 kernel_init, PID2 kthreadd, PID0 idle}` 這張圖並說明每一步
- [ ] 能解釋 PID 0/1/2 分別是什麼、誰建立誰、各自的最終命運
- [ ] 面試被問「built-in 驅動的 init 何時、以什麼順序被呼叫」，你能講出 initcall 分級 + `do_initcalls`
- [ ] 能推理 `Kernel panic: No working init found` 的至少三種成因，並知道從 `kernel_init` 的哪段程式碼往回查
- [ ] 能解釋為什麼開機後在 gdb `break start_kernel` 停不到（`__init` 記憶體被 `free_initmem` 回收）
- [ ] 知道 x86_64 與 ARM64 入口不同、但都匯流到同一個 `start_kernel`，差異被 `setup_arch` 吸收

## 延伸閱讀

### 官方文件與源碼

- **`init/main.c` 的 `start_kernel()` 與 `rest_init()`（[Bootlin v6.12](https://elixir.bootlin.com/linux/v6.12/source/init/main.c)）**
  - **讀哪裡**：整個 `start_kernel` 從頭讀到 `rest_init`，對照本章的里程碑清單。這是本章的第一手材料，讀源碼比讀任何二手說明都準
  - **和本章的關聯**：本章挑出的 ★ 里程碑都在這裡；建議邊讀邊在 gdb 停在每一個看它跑

- **`init/initramfs.c` 的 `populate_rootfs()`（[Bootlin v6.12](https://elixir.bootlin.com/linux/v6.12/source/init/initramfs.c)）**
  - **讀哪裡**：`populate_rootfs` 怎麼把 cpio 格式的 initramfs 解到 rootfs。這解釋了 Ch 0 那個 `-H newc` cpio 為什麼是那個格式、`/init` 怎麼被放進根目錄
  - **前提**：Ch 0 做過 initramfs

- **[Documentation/core-api/kernel-api.rst](https://www.kernel.org/doc/html/latest/core-api/kernel-api.html) 與 `include/linux/init.h`**
  - **讀哪裡**：`include/linux/init.h` 裡各 `*_initcall` 宏的定義與註解，是 initcall 分級的權威來源
  - **能學到什麼**：每個等級的確切用途與呼叫時機，比本章的表格更完整

### 文章與書籍

- **[Bootlin Kernel bootup 相關訓練材料](https://bootlin.com/docs/) 與 kernelnewbies 的 boot 流程整理**
  - **為什麼值得讀**：有配圖的開機流程總覽，適合把本章的文字路徑視覺化；注意版本，細節以 v6.12 源碼為準

- **《Linux Kernel Development, 3rd Ed.》** — Robert Love（Addison-Wesley, 2010）
  - **讀哪裡**："Process Management" 與開頭 kernel 啟動的段落
  - **注意**：版本較舊（`start_kernel`/`rest_init` 的骨架與 PID 0/1/2 的概念至今適用，但函式細節以 6.12 為準）

- **[LWN: Booting Linux 系列 / initcall 相關文章](https://lwn.net/Kernel/Index/)** — 在 LWN Kernel index 搜 "initcall" 與 "boot"
  - **為什麼值得讀**：想深入 initcall 的設計動機、`initcall_debug`、開機時間優化，LWN 有一手討論

開機路徑走完，PID 1 已經在使用者空間跑，kernel 交出了控制權。下一章我們回到 kernel 與使用者空間的**邊界**本身：使用者程式怎麼透過 syscall 請 kernel 幫忙、`syscall` 指令發生時 CPU 與 kernel 各做了什麼，並動手加一個自訂 syscall。

→ [Ch 4 Syscall 機制與自訂 syscall](./04-syscall-mechanism.md)
