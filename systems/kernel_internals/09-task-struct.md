# Ch 9 — task_struct 解剖

> **目標**：搞懂 kernel 眼中「一個 process/thread」到底是什麼——`struct task_struct`。學完你能在 gdb 裡遍歷全系統的 task 串列、看懂每個 task 的識別/狀態/記憶體/檔案欄位怎麼組起來，並理解「Linux 根本沒有獨立的 thread」這句話的源碼依據。這是 Part 2（process 與排程）的地基。

## 為什麼需要這個？

你在 `linux_commands` 用過 `ps`：一行是一個 process，有 PID、有狀態（R/S/D/Z）、有 owner、有 command。你在 `kernel_pwn` 打過的很多漏洞，最後都要靠竄改某個 task 的 `cred` 提權。你在 `bpf` 掛過的 tracepoint，第一件事就是 `bpf_get_current_task()` 拿到當前 task。

這些使用者空間看到的「process」，在 kernel 裡全部收斂到**同一個結構**：`struct task_struct`。kernel 排程誰、記帳給誰、發信號給誰、檢查誰有沒有權限——全都是在對某個 `task_struct` 動作。它是整個 kernel 對「一個可排程實體（schedulable entity）」的完整描述，是後面幾乎每一章的共同貨幣：排程器排的是它、page fault 要查它的 `mm`、`open()` 要動它的 `files`、`setuid()` 要換它的 `cred`。

先把一個關鍵誤解破掉：**Linux kernel 內部沒有「thread」這個獨立概念**。使用者空間你分 process 和 thread，但 kernel 裡兩者都是 `task_struct`，都排進同一個 runqueue，都由排程器一視同仁地挑。所謂「同一個 process 的多個 thread」，在 kernel 眼中只是**一組共享了某些資源（mm、files、signal handler）的 task**。這個設計貫穿本章，也是理解 Ch 10（fork/clone）的前提。

## 先建立直覺

`task_struct` 定義在 `include/linux/sched.h`。它非常大——編譯後一個實例通常好幾 KB，欄位數以百計，隨 config 增減。逐欄位講不切實際也沒意義。正確的讀法是**分組**：把幾百個欄位歸進「識別、狀態、排程、記憶體、檔案、憑證、親屬、thread_info」這幾個功能群，先建立地圖，需要細節時再往對應章節鑽。

```
                      struct task_struct（一個 task，位在 slab，數 KB）
  ┌──────────────────────────────────────────────────────────────────────────┐
  │ ── 識別（identity）───────────────────────────────────────────────────    │
  │   pid_t pid;        ← kernel 內部的 thread ID（每個 task 唯一）             │
  │   pid_t tgid;       ← thread group ID = 使用者空間看到的「PID」            │
  │   char  comm[16];   ← 執行檔名（ps 那個 COMMAND，最長 15 字元+\0）         │
  │   struct pid *thread_pid; ...                                              │
  │                                                                            │
  │ ── 狀態（state）──────────────────────────── 接 Ch 11-14、linux_commands  │
  │   unsigned int __state;      ← RUNNING/INTERRUPTIBLE/UNINTERRUPTIBLE...    │
  │   unsigned int exit_state;   ← ZOMBIE / DEAD（退出中）                     │
  │                                                                            │
  │ ── 排程（scheduling）──────────────────────────────── 接 Ch 11-13         │
  │   int prio, static_prio, normal_prio;                                     │
  │   const struct sched_class *sched_class;   ← 這個 task 歸哪個排程類        │
  │   struct sched_entity  se;   ← CFS/EEVDF 用的排程實體（含 vruntime）       │
  │   struct sched_rt_entity rt; ...                                          │
  │                                                                            │
  │ ── 記憶體（memory）────────────────────────────────── 接 Ch 16、19        │
  │   struct mm_struct *mm;         ← 這個 task 的位址空間；kernel thread=NULL │
  │   struct mm_struct *active_mm;  ← kernel thread 借用的位址空間             │
  │                                                                            │
  │ ── 開啟的檔案 / 檔案系統 ──────────────────── 接 Ch 33、linux_commands fd  │
  │   struct files_struct *files;  ← fd 表（fd → struct file）                │
  │   struct fs_struct    *fs;     ← cwd、root、umask                         │
  │                                                                            │
  │ ── 憑證（credentials）──────────────────────────────── 接 Ch 47           │
  │   const struct cred __rcu *cred;      ← euid/egid/capabilities            │
  │   const struct cred __rcu *real_cred;                                     │
  │                                                                            │
  │ ── 親屬關係（process tree）──────────────── 用 list_head，接 Ch 5         │
  │   struct task_struct __rcu *real_parent;                                  │
  │   struct list_head children;   ← 我的子 task 串列頭                        │
  │   struct list_head sibling;    ← 掛在父 task children 上的節點             │
  │   struct list_head tasks;      ← 全系統 task 的大串列（穿過 init_task）    │
  │                                                                            │
  │ ── thread_info（架構相關，含排程旗標）─────────────── 接 Ch 14            │
  │   struct thread_info thread_info;  ← 內嵌；含 flags（TIF_NEED_RESCHED...）│
  │   void *stack;                     ← 指向 kernel stack                    │
  └──────────────────────────────────────────────────────────────────────────┘
```

記住這張圖的分組，比記住任何單一欄位重要。下面逐組拆。

## 識別：pid、tgid，與「getpid 為什麼回傳 tgid」

第一個反直覺點：`task_struct` 裡的 `pid` 欄位，**不是**你 `getpid()` 拿到的那個數字。

- `pid`：kernel 內部給**每一個 task** 的唯一 ID。同一個 process 裡三個 thread，有三個不同的 `pid`。
- `tgid`（thread group ID）：一組 thread 共享的 ID，等於這個 thread group 裡**主 thread（group leader）的 `pid`**。

使用者空間講的「PID」其實是 kernel 的 `tgid`。所以 `getpid()` 這個 syscall（`kernel/sys.c` 的 `__do_sys_getpid`）回傳的是 `current->tgid`，不是 `current->pid`；而 `gettid()`（`__do_sys_gettid`）回傳的才是 `current->pid`。

為什麼這樣設計？因為 POSIX 要求「同一個 process 裡所有 thread `getpid()` 拿到同一個值」。kernel 內部又需要能唯一定位每一個 task。兩個需求用兩個欄位滿足：`tgid` 對外呈現 process 身分、`pid` 對內唯一識別 task。

```
  一個 process = 一個 thread group
  ┌─────────────────────── thread group（tgid = 1000）──────────────────────┐
  │                                                                          │
  │   task A（group leader）      task B              task C                  │
  │   pid  = 1000                 pid  = 1001         pid  = 1002            │
  │   tgid = 1000  ◄── leader     tgid = 1000         tgid = 1000            │
  │   getpid() → 1000             getpid() → 1000     getpid() → 1000        │
  │   gettid() → 1000             gettid() → 1001     gettid() → 1002        │
  │                                                                          │
  │   三個 task 共享：mm（同一個位址空間）、files（同一張 fd 表）、          │
  │                   signal handler、cred ...（由 clone flags 決定，Ch 10） │
  └──────────────────────────────────────────────────────────────────────────┘
```

`comm[16]`：執行檔名，就是 `ps` 的 COMMAND、`/proc/PID/comm` 的內容。**只有 16 bytes（15 字元 + `\0`）**——這是為什麼 `ps` 有時把長程式名截斷。它由 `set_task_comm()` / `__set_task_comm()`（`fs/exec.c`、`include/linux/sched.h`）設定，`prctl(PR_SET_NAME)` 也走這條路。

## PID 管理：struct pid 與 pid_namespace

上面說 `pid`/`tgid` 是整數，這是簡化。實際上 kernel 用一個結構 `struct pid`（`include/linux/pid.h`）來管理 PID，原因是 **PID namespace**（容器化的基礎，Ch 49、也是 `docker` 課裡容器隔離的核心）。

同一個 task 在不同 namespace 裡可以有**不同的 PID 數字**：在容器內是 PID 1，在 host 上可能是 PID 4213。一個整數存不下這種「一對多」映射，所以 `struct pid` 內含一個 `struct upid numbers[]` 陣列，每一層 namespace 一個 (nr, ns) 對。

```
  struct pid（refcount 管理的物件，被多個 task 指向）
  ┌────────────────────────────────────────────┐
  │ level                                        │
  │ numbers[0] = { nr=4213, ns=host_pidns   }    │  ← 在 host 看到 4213
  │ numbers[1] = { nr=1,    ns=container_pidns } │  ← 在容器內看到 PID 1
  │ tasks[PIDTYPE_PID]  ─► 指回擁有這個 pid 的 task│
  └────────────────────────────────────────────┘
        ▲
        │ task_struct->thread_pid
    struct task_struct
```

task_struct 透過 `thread_pid` 指向自己的 `struct pid`，反向則透過 `struct pid` 的 `tasks[]` hlist 找回 task。`pid_nr()` / `task_tgid_nr()` 這些 helper（`kernel/pid.c`、`include/linux/sched.h`）負責「給定 task，回傳它在某個 namespace 下的整數 PID」。本章你先知道「PID 不只是整數，背後是 `struct pid` + namespace」就夠，實作細節留給 Ch 49。

## 狀態：__state 與 exit_state，對上你在 ps 看到的 R/S/D/Z

`task_struct` 用**兩個**欄位表達狀態，這也是常見混淆點：

- `__state`（v5.14 起從 `state` 改名為 `__state`，強制大家改用 helper 存取）：task 「活著」時的排程狀態。
- `exit_state`：task 正在**退出**時的狀態（ZOMBIE / DEAD）。

`__state` 的主要值（定義在 `include/linux/sched.h`）：

| `__state` 值 | 意義 | `ps` 對應 | 誰能喚醒它 |
|---|---|---|---|
| `TASK_RUNNING` (0) | 正在跑，或在 runqueue 上等 CPU | `R` | —（已可跑） |
| `TASK_INTERRUPTIBLE` | 睡眠，等事件，**可被信號打斷** | `S` | 事件到 **或** 收到信號 |
| `TASK_UNINTERRUPTIBLE` | 睡眠，等事件，**不理信號** | `D` | 只有等的事件到 |
| `__TASK_STOPPED` | 被 SIGSTOP 停住 | `T` | SIGCONT |
| `TASK_TRACED`（現以 `JOBCTL_TRACED` 表達） | 被 debugger（ptrace）停住 | `t` | debugger |

`exit_state` 的值：

| `exit_state` 值 | 意義 | `ps` 對應 |
|---|---|---|
| `EXIT_ZOMBIE` | 已死，但父行程還沒 `wait()` 收屍 | `Z` |
| `EXIT_DEAD` | 收屍中，即將從系統徹底消失 | — |

回接 `linux_commands`：你在那門課看 `ps` 的 STAT 欄看到 `D`，會很挫折——`D`（`TASK_UNINTERRUPTIBLE`）連 `kill -9` 都殺不掉。現在你知道源碼理由了：`TASK_UNINTERRUPTIBLE` 的睡眠**設計上就不檢查 pending signal**，所以信號送達也不會喚醒它，只能等它在等的那個東西（通常是慢速 I/O，如卡住的 NFS、壞掉的硬碟）回來。這不是 bug，是為了避免在關鍵 I/O 中途被信號拉走、造成資料結構半途而廢。

`R` vs runqueue 的細節：`TASK_RUNNING` **不代表**「此刻正佔著 CPU」，它代表「可執行」——可能正在跑，也可能只是排在 runqueue 上等被挑中。真正「這顆 CPU 現在跑的是誰」由 per-CPU 的 `rq->curr` 記錄（Ch 11）。

存取狀態一律用 helper，不要直接讀 `p->__state`：

```c
/* 讀：READ_ONCE 保證看到完整寫入，不被編譯器拆開 */
unsigned int state = READ_ONCE(p->__state);

/* 判斷 */
if (task_is_running(p)) { ... }          /* include/linux/sched.h */

/* 設定當前 task 狀態（睡前用），有記憶體屏障語意 */
set_current_state(TASK_INTERRUPTIBLE);   /* 之後 schedule() 讓出 CPU */
```

`__state` 前綴的底線和 helper 的存在，本身就是設計訊息：狀態的讀寫牽涉排程器並行，直接讀寫容易 race，kernel 用命名逼你走安全路徑（memory barrier 語意見 Ch 24）。

## 排程：sched_class、sched_entity、prio

排程相關欄位是 Ch 11–13 的主角，這裡先建立連結。

- `sched_class`（指向 `struct sched_class`）：這個 task 歸**哪個排程類**管。Linux 的排程器是分層的：`stop` > `dl`（deadline）> `rt`（即時）> `fair`（CFS/EEVDF，一般 task）> `idle`。排程時從最高優先的類往下問「你有沒有 task 要跑」。你的一般 process 用的是 `fair_sched_class`。
- `se`（`struct sched_entity`）：**fair 類**用的排程實體，裡面有 `vruntime`（虛擬執行時間，CFS 的核心）、EEVDF 的 `deadline`/`vlag` 等。注意欄位名是 `se` 但它是「scheduling entity」，把「一個可被 CFS 排的東西」抽象出來（group scheduling 時一個 cgroup 也是一個 se，Ch 50）。
- `prio` / `static_prio` / `normal_prio`：優先權。`static_prio` 由 nice 值決定；`prio` 是「動態有效優先權」，priority inheritance（優先權繼承，避免優先權反轉）會臨時抬高它。

為什麼要有這麼多 sched 欄位塞在 task_struct 裡、而不是排程器自己另開結構？因為排程是 kernel **最熱**的路徑之一，`__schedule()`（`kernel/sched/core.c`）每次都要摸這些欄位。把它們直接內嵌在 task_struct、和 task 一起躺在快取裡，比每次跳一次指標去別的結構省。這是「熱資料放一起」的典型取捨。

## 記憶體：mm 與 active_mm，以及 kernel thread 為何 mm==NULL

兩個欄位，`mm` 和 `active_mm`，都指向 `struct mm_struct`（一個 task 的完整位址空間描述，Ch 19 主角：VMA、page table root 都在裡面）。為什麼要兩個？

- **一般 user process**：`mm == active_mm`，都指向自己的位址空間。
- **kernel thread**（如 `kswapd`、`kthreadd`、workqueue worker）：`mm == NULL`。kernel thread **沒有自己的使用者空間位址空間**——它只跑 kernel 程式碼，只用 kernel 那半邊（所有位址空間共享的高位）。

問題來了：CPU 的 page table base 暫存器（x86 的 CR3、ARM64 的 TTBR）**必須指向某個有效的 page table**，不能是 NULL。kernel thread 沒有自己的 mm，那 context switch 到它時 CR3 要填什麼？

答案是 `active_mm`：kernel thread **借用**前一個 user task 的 mm 當 `active_mm`，context switch 時不切 CR3（因為 kernel 半邊在所有位址空間都一樣，借誰的都行），省下一次昂貴的 TLB flush。這叫 **lazy TLB**。

```
   切換順序：user task P  →  kernel thread K  →  user task Q

   P 跑時：     P->mm = P->active_mm = mm_P        CR3 → mm_P 的 page table
                │
                ▼ 切到 K（kernel thread，K->mm == NULL）
   K 跑時：     K->mm = NULL
                K->active_mm = mm_P   ← 借用 P 的！不切 CR3，不 flush TLB
                │
                ▼ 切到 Q（一般 user task）
   Q 跑時：     Q->mm = Q->active_mm = mm_Q        CR3 → mm_Q（這時才真的切）
```

`context_switch()`（`kernel/sched/core.c`）裡有一段專門判斷 `next->mm` 是不是 NULL 來決定要不要真的切位址空間、要不要 `mmgrab`/`mmdrop` `active_mm` 的 refcount。這是「kernel thread 為什麼 `ps` 看不到記憶體用量、`/proc/PID/maps` 空空」的源碼根因。細節在 Ch 14（context switch）和 Ch 19（mm_struct）。

## 檔案：files 與 fs

- `files`（`struct files_struct *`）：**開啟檔案表**。裡面是 `fd → struct file *` 的陣列（`fdtable`）。你在 `linux_commands` 學的 fd 0/1/2、`dup2`、`/proc/PID/fd/`，底層就是這張表。同一個 thread group 的 thread 共享同一個 `files`（clone `CLONE_FILES`），所以一個 thread `open()` 得到的 fd，別的 thread 也能用。
- `fs`（`struct fs_struct *`）：檔案系統上下文——current working directory（`cwd`）、root（`chroot` 改的就是這個）、umask。

這兩個都是**指標**，因為它們天生要被多個 task 共享（thread group、或 `CLONE_FS`）。共享靠 refcount（`files->count`、`fs->users`）管理生命週期：最後一個放手的 task 負責釋放。這也是為什麼 `close()` 一個被多 thread 共享的 fd，只是遞減引用，不一定真的關檔。

## 憑證：cred

`cred`（`const struct cred __rcu *`，`include/linux/cred.h`）裝的是**安全身分**：`uid`/`gid`、`euid`/`egid`（有效身分，權限檢查看這個）、`fsuid`、以及 capabilities 集合（`cap_effective` 等）。Ch 47 專講，這裡點三個關鍵：

1. `__rcu` 標記：cred 用 RCU 保護（Ch 27）。讀取者不必上鎖，用 `current_cred()` / `__task_cred()` 在 RCU read-side 拿；修改時用 `prepare_creds()` 複製一份、改完 `commit_creds()` 整個換掉指標。**copy-on-write + RCU**，讓「檢查權限」這個超高頻操作免鎖。
2. 有 `cred`（有效身分）和 `real_cred`（真實身分）兩份，對應 setuid 程式「我是誰執行的」vs「我現在以誰的身分跑」。
3. `kernel_pwn` 的經典提權：想辦法把某個 task 的 `cred` 換成 root 的 cred（或直接改 `cap_effective`）。你現在知道它攻擊的正是 task_struct 這個欄位——`commit_creds(prepare_kernel_cred(NULL))` 這句提權 payload，就是走上面第 1 點的合法換 cred 路徑，只是被拿來作惡。

## 親屬關係：用 list_head 串起 process tree

process 有樹狀父子關係（`pstree` 看到的），kernel 用 Ch 5 教的 `struct list_head` 串接：

- `real_parent`：真正 fork 出我的那個 task。
- `parent`：回報 SIGCHLD 的對象（通常等於 `real_parent`，被 ptrace 時會不同）。
- `children`：**串列頭**，我所有子 task 掛在這上面。
- `sibling`：**節點**，我自己掛在父 task 的 `children` 串列上。

```
        parent task
        ┌───────────────┐
        │ children ●────┼──────────────┐  （children 是串列頭）
        └───────────────┘              │
                                       ▼
   child A            child B            child C
   ┌──────────┐  next ┌──────────┐ next ┌──────────┐
   │ sibling ●┼─────► │ sibling ●┼────► │ sibling ●┼──► 回到 parent->children
   │ real_    │       │ real_    │      │ real_    │
   │ parent ──┼──┐    │ parent   │      │ parent   │
   └──────────┘  │    └──────────┘      └──────────┘
                 └──► 都指回 parent task
```

`children` + `sibling` 的組合是 list_head 的標準用法：一個當頭、一個當節點，就能 O(1) 增刪、O(n) 走訪一個 task 的所有小孩。`for_each_child` 之類的走訪就靠這對欄位。

還有一個更大的串列：`tasks`（`struct list_head`）。**全系統每一個 task** 都掛在同一條環狀雙向串列上，串列的「錨點」是 `init_task`（PID 0，`swapper`，靜態定義在 `init/init_task.c`）。`for_each_process()`（`include/linux/sched/signal.h`）這個巨集就是從 `init_task` 出發沿 `tasks` 走一圈——待會動手環節會用到。

> 注意 `for_each_process()` 只走 **thread group leader**（每個 process 一個），不走非 leader 的 thread。要連 thread 一起走要用 `for_each_process_thread()`。這對應「process 串列」vs「所有 task」的差別。

## thread_info 與 current：怎麼從 stack 找到 task

`task_struct` 裡內嵌一個 `struct thread_info`（架構相關，x86_64 在 `arch/x86/include/asm/thread_info.h`）。它裝的是**最貼近硬體、最常被組語碰到**的東西，最關鍵的是 `flags`——一組 per-task 的位元旗標：

- `TIF_NEED_RESCHED`：「這個 task 該被搶佔了」。排程器決定要換人時，設這個 flag；回到使用者空間或允許搶佔的點檢查它，設了就呼叫 `schedule()`。Ch 14（preemption）的主角。
- `TIF_SIGPENDING`：有 pending signal 要處理。
- `TIF_SIGPENDING`、`TIF_NOTIFY_RESUME` 等其他旗標。（`TIF_NEED_RESCHED_LAZY` 是 6.13 才加的 lazy preemption 旗標，v6.12 尚無。）

為什麼這些旗標放在 `thread_info` 而不是 task_struct 別處？因為它們要在**中斷返回、syscall 返回**這種 assembly 熱路徑上被檢查，放在架構已知偏移的 `thread_info` 裡，組語能用固定 offset 快速摸到，不必解一堆 C 結構。

這連到第二個關鍵問題：**kernel 執行時怎麼知道「我現在服務的是哪個 task」？** 這個「當前 task」就是 `current` 巨集（你在 Ch 2 見過）。它的實作是架構相關的：

- **x86_64**：`current` 讀一個 **per-CPU 變數** `current_task`（`arch/x86/include/asm/current.h` 的 `get_current()`，透過 `%gs` 段基底存取）。每次 context switch，`__switch_to()` 會更新這顆 CPU 的 `current_task` 指向新 task。所以 `current` 就是「這顆 CPU 的 per-CPU current_task 指標」。
- **ARM64**：`current` 從專用系統暫存器 `SP_EL0` 取得（`arch/arm64/include/asm/current.h`），context switch 時更新它。

```
   x86_64：per-CPU 找 current
   ┌── CPU 0 的 per-CPU 區 ──┐         ┌── CPU 1 的 per-CPU 區 ──┐
   │ current_task ●─────────┼──►taskA │ current_task ●─────────┼──►taskB
   └────────────────────────┘         └────────────────────────┘
       current（在 CPU0 跑時）= taskA      current（在 CPU1 跑時）= taskB

   歷史對照（舊 kernel / 部分架構）：
   kernel stack 底部放 thread_info，把 sp 對齊遮罩就得到 thread_info，
   再 ->task 找到 task_struct。x86_64 現在改用 per-CPU，較快也較安全
   （stack overflow 不會踩爛 thread_info）。
```

回接 Ch 2：那章講「kernel 的執行 context 與 stack」，這裡補上「每個 task 有自己的 kernel stack（`task_struct->stack`，x86_64 預設 16 KB），而 `current` 靠 per-CPU 指標定位」。stack 和 task 的關係、`THREAD_SIZE`、stack overflow 保護（`VMAP_STACK`）等細節在 Ch 2 與 Ch 14 展開。

## 動手：在 gdb 裡遍歷全系統的 task

把 Ch 0 的環境開起來（QEMU `-S -s`，gdb `target remote :1234`、`source vmlinux-gdb.py`），開機到 shell 後從 gdb `Ctrl-C` 中斷。

### 1. 列出所有 task

```gdb
(gdb) lx-ps
      TASK          PID    COMM
0xffffffff...  0        swapper/0      ← init_task
0xffff8880...  1        init
0xffff8880...  2        kthreadd
...
```

`lx-ps` 來自 `vmlinux-gdb.py`，它做的事就是「從 `init_task` 沿 `tasks` 串列走一圈」——跟 kernel 的 `for_each_process()` 同一條路。

### 2. 看 init_task 本尊

```gdb
(gdb) p init_task
$1 = { thread_info = {...}, __state = 0, ... pid = 0, tgid = 0,
       comm = "swapper/0\000...", ... }

(gdb) p init_task.comm
$2 = "swapper/0\000\000\000\000\000\000"
(gdb) p init_task.pid
$3 = 0                       ← PID 0，系統的第 0 號 task
```

`init_task` 是靜態編進 kernel 的第一個 task（`init/init_task.c` 的 `struct task_struct init_task`），也是全系統 task 串列的錨。它就是 `swapper`/idle task。

### 3. 手動走 tasks 串列（不靠 lx-ps）

`tasks` 是嵌在 task_struct 裡的 `list_head`，用 Ch 5 的 `container_of` 手法從節點還原回 task。gdb 裡可以直接算偏移：

```gdb
# init_task 的下一個 task（第一個真正的 process，通常是 PID 1 init）
(gdb) set $off = (char*)&init_task.tasks - (char*)&init_task
(gdb) p ((struct task_struct *)((char*)init_task.tasks.next - $off))->comm
$4 = "init\000..."
(gdb) p ((struct task_struct *)((char*)init_task.tasks.next - $off))->pid
$5 = 1
```

`$off` 就是 `offsetof(struct task_struct, tasks)`；`tasks.next` 是串列上下一個節點的位址，減掉偏移還原成 task_struct 指標——這正是 `container_of` / `list_entry` 的手算版。走通這一步，你就真的懂了 kernel 怎麼用 list_head 把幾百個 task 串起來。

### 4. 看當前 task

```gdb
(gdb) p $lx_current().comm       # vmlinux-gdb.py 提供的 convenience function
(gdb) p $lx_current().pid
(gdb) p $lx_current().__state
```

`$lx_current()` 幫你解 per-CPU 的 `current_task`，省掉手算 `%gs`。

## 動手：寫模組用 for_each_process 印出所有 task

`lx-ps` 是 gdb 視角；換成 kernel 內部視角，我們寫個模組，用 `for_each_process()` 走同一條串列，印出每個 process 的 pid / tgid / comm / 狀態。

```c
// taskdump.c
#include <linux/init.h>
#include <linux/module.h>
#include <linux/sched.h>
#include <linux/sched/signal.h>   // for_each_process
#include <linux/pid.h>

static const char *state_str(unsigned int s)
{
    if (s == TASK_RUNNING)              return "R";
    if (s & TASK_INTERRUPTIBLE)         return "S";
    if (s & TASK_UNINTERRUPTIBLE)       return "D";
    if (s & __TASK_STOPPED)             return "T";
    return "?";
}

static int __init taskdump_init(void)
{
    struct task_struct *p;

    pr_info("taskdump: PID  TGID  STATE  COMM  mm?\n");

    rcu_read_lock();                    // 保護 task 串列走訪（見下方說明）
    for_each_process(p) {
        pr_info("taskdump: %5d %5d  %-5s  %-16s  %s\n",
                p->pid, p->tgid,
                state_str(READ_ONCE(p->__state)),   // helper 語意：別直接讀
                p->comm,
                p->mm ? "user" : "kernel-thread");  // mm==NULL ⇒ kernel thread
    }
    rcu_read_unlock();

    return 0;
}

static void __exit taskdump_exit(void)
{
    pr_info("taskdump: bye\n");
}

module_init(taskdump_init);
module_exit(taskdump_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Walk the task list with for_each_process");
```

配 Ch 0 的 Makefile 編出 `taskdump.ko`，放進 initramfs，`insmod` 後 `dmesg` 看輸出。你會看到：

- PID 1（init/systemd）之後一堆 `pid == tgid` 的一般 process（`mm` = user）。
- 一堆 `comm` 像 `kworker/*`、`ksoftirqd/*`、`kswapd0` 的，`mm` 都是 `kernel-thread`——印證「kernel thread 沒有 user 位址空間」。
- 狀態多半是 `S`（睡著等事件），很少 `R`——因為大多數時間系統是閒的。

三個要點：

1. **為什麼要 `rcu_read_lock()`**：走 task 串列時，別的 CPU 可能正在 fork/exit 增刪節點。task 串列用 RCU 保護（Ch 27），讀端進 RCU critical section 才安全——這是 kernel 到處可見的模式，`for_each_process` 幾乎都配 RCU 或 `tasklist_lock`。
2. **為什麼 `READ_ONCE(p->__state)`**：`__state` 可能被別的 CPU 同時改，`READ_ONCE` 防編譯器把讀取拆開或優化掉，拿到一個完整值。
3. **`p->mm ? user : kernel-thread`**：一行判斷 kernel thread，直接對應本章「mm==NULL ⇒ kernel thread」。

## 對比與取捨

| 主題 | 選項 A | 選項 B | Linux 選了什麼、為什麼 |
|---|---|---|---|
| process vs thread | 各自獨立的 kernel 結構 | **統一成 task，靠共享資源區分** | Linux 選 B：一種 `task_struct`，thread group 靠共享 mm/files。排程器/生命週期程式碼只需一套，clone flags 決定共享哪些（Ch 10） |
| current 定位 | 從 kernel stack 底的 thread_info 推 | **per-CPU `current_task` 指標**（x86_64） | x86_64 選 per-CPU：一次記憶體讀就到，且 stack overflow 不會踩爛「我是誰」的資訊 |
| PID 表示 | 純整數 | **`struct pid` + namespace 陣列** | 選結構：容器要求同一 task 在不同 namespace 有不同 PID，整數存不下（Ch 49） |
| cred 存取同步 | 每次讀權限上鎖 | **RCU + copy-on-write** | 選 RCU：權限檢查頻率極高，讀端免鎖才不拖垮效能（Ch 27、47） |
| 排程欄位位置 | 排程器另開結構 | **內嵌 se/prio 在 task_struct** | 內嵌：排程是最熱路徑，熱資料放一起省快取 miss |

## 踩雷集錦

1. **「`task_struct->pid` 就是使用者的 PID」**——錯。使用者的 PID 是 `tgid`；`pid` 是 kernel 內部的 thread ID（= `gettid()`）。`getpid()` 回傳 `tgid`。單 thread process 兩者相等，容易讓人以為它們永遠一樣。

2. **「Linux 有 thread 這種東西」**——kernel 裡沒有。thread 就是和別的 task 共享 mm/files/signal 的 task。「process 有幾個 thread」= 「這個 tgid 底下掛了幾個 task」。理解這點，Ch 10 的 clone flags 才會通。

3. **「`D` 狀態的 process `kill -9` 殺得掉」**——殺不掉。`TASK_UNINTERRUPTIBLE` 設計上不檢查信號，只能等它等的 I/O 回來。看到一堆 `D` 通常是底層儲存/網路卡住，不是 process 本身有問題。

4. **「kernel thread 也有自己的位址空間」**——沒有，`mm == NULL`。它借前一個 user task 的 `active_mm`（lazy TLB）。所以 `/proc/2/maps`（kthreadd）是空的，`ps` 看它記憶體是 `-` 或 0。

5. **「直接 `p->__state = TASK_INTERRUPTIBLE` 讓它睡」**——會 race。要用 `set_current_state()`（帶記憶體屏障，確保「設狀態」和「檢查條件」不被重排），且睡眠要走標準的 wait 迴圈（Ch 26）。直接寫欄位在 SMP 上會漏喚醒（lost wakeup）。

6. **`comm` 當成完整命令列**——`comm` 只有 15 字元，且只是**執行檔名**，不含參數。完整命令列在 `mm->arg_start/arg_end` 指的使用者空間記憶體（`/proc/PID/cmdline`），不在 task_struct 裡。

## 進階：再往深一層

- **task_struct 有多大、為什麼在意**：一個實例好幾 KB，用專屬的 slab cache `task_struct_cachep`（`kernel/fork.c` 的 `fork_init()` 建立）配置。fork 一個 process 就要配一個——這是 fork 成本的一部分（Ch 10）。欄位順序也經過 cacheline 調校，熱欄位（排程相關）盡量集中，減少 context switch 時的 cache miss。用 `pahole task_struct`（`dwarves` 套件，Ch 0 裝過）可以看它的實際佈局與 padding。

- **`__state` 為什麼加底線**：v5.14 的 commit 把 `state` 改名 `__state`，就是要**破壞所有直接存取的舊 code**，逼大家改用 `READ_ONCE`/`task_is_running()` 等 helper。這是 kernel 常用的手段：改名一個欄位來強制全樹改用安全存取。面試被問「為什麼不直接讀 `p->state`」，答得出 memory ordering 與這段歷史，就是有讀源碼。

- **thread group leader 的特殊性**：`group_leader` 欄位指向 thread group 的頭（`pid == tgid` 那個）。leader 退出時如果還有別的 thread 活著，它會變成一種特殊 zombie（保留 tgid 直到整組退出）。`ps` 之所以以 process 為單位顯示，就是只列 leader（`for_each_process` 的語意）。

- **面試常問**：「一個 process 在 kernel 裡由什麼表示？」→ `task_struct`。「thread 呢？」→ 也是 `task_struct`，靠 clone 共享資源。「`current` 怎麼實作？」→ x86_64 per-CPU `current_task`（`%gs`），ARM64 `SP_EL0`。「D 狀態殺不掉為什麼？」→ `TASK_UNINTERRUPTIBLE` 不檢查信號。這四題答得乾淨，process 這塊基本過關。

## 動手練習

1. **gdb 手走串列**：不靠 `lx-ps`，用本章「手動走 tasks 串列」的偏移技巧，從 `init_task` 開始連續走三個 task，印出它們的 `comm` 和 `pid`。確認第一個是 `swapper/0`(0)、第二個是 `init`(1)。目的是把 list_head + container_of 走一遍。

2. **抓一個 kernel thread**：在 `taskdump.ko` 的輸出裡找出所有 `mm == NULL` 的 task，數一數你的系統有幾個 kernel thread。再挑一個（如 `kthreadd`，PID 2），在 gdb 裡 `p` 它的 `mm` 和 `active_mm`，確認 `mm` 是 0、`active_mm` 不是。

3. **製造 D 狀態（觀察，可選）**：這在 QEMU 最小環境不好複現（需要卡住的 I/O）。替代做法：讀懂 `TASK_UNINTERRUPTIBLE` 在 `include/linux/sched.h` 的定義，並在 `kernel/sched/core.c` 的 `try_to_wake_up()` 裡找到「為什麼信號喚不醒 D 狀態」的源碼依據（提示：喚醒是靠改狀態成 RUNNING，而送信號對 UNINTERRUPTIBLE 不觸發喚醒路徑）。

4. **改 taskdump 印 tgid 差異**：擴充模組，只印出 `pid != tgid` 的 task（即非 leader 的 thread）。在最小 initramfs 裡可能一個都沒有——那就 `insmod` 後在 shell 開一個多 thread 程式（或用 busybox 起背景任務）再看。體會「thread 在 kernel 裡就是 pid≠tgid 的 task」。

## 本章重點整理

- 一個 process/thread 在 kernel 裡都是 `struct task_struct`（`include/linux/sched.h`）；Linux 沒有獨立 thread，thread = 和別人共享 mm/files/signal 的 task，同組共享 `tgid`。
- 識別看 `pid`（內部 thread ID = `gettid()`）與 `tgid`（對外 PID = `getpid()`）；狀態看 `__state`（R/S/D/T）與 `exit_state`（Z/DEAD），對得上 `ps` 的 STAT。
- 記憶體看 `mm`/`active_mm`：`mm==NULL` 就是 kernel thread，它借 `active_mm` 玩 lazy TLB；檔案看 `files`/`fs`、身分看 `cred`、親屬用 `children`/`sibling`/`tasks` 三條 list_head 串。
- `current` 在 x86_64 是 per-CPU `current_task` 指標、ARM64 是 `SP_EL0`；排程熱旗標 `TIF_NEED_RESCHED` 在內嵌的 `thread_info` 裡。

## 自我檢核

- [ ] 不看筆記，能解釋 `getpid()` 為什麼回傳 `tgid` 而不是 `task_struct->pid`
- [ ] 能說出 R/S/D/T/Z 各對應 `__state`/`exit_state` 的哪個值，以及 `D` 為什麼 `kill -9` 殺不掉
- [ ] 面試被問「thread 在 Linux kernel 裡是什麼」，能用 task_struct + 共享資源（clone flags）答清楚
- [ ] 能解釋 kernel thread 為什麼 `mm == NULL`、`active_mm` 拿來做什麼（lazy TLB）
- [ ] 能在 gdb 裡從 `init_task` 沿 `tasks` 串列走到下一個 task，並說明用到了 container_of
- [ ] 能寫出用 `for_each_process()` 走 task 串列的模組，並解釋為什麼要 `rcu_read_lock()` 和 `READ_ONCE(__state)`

## 延伸閱讀

### 官方文件 / 源碼

- **`include/linux/sched.h` 的 `struct task_struct`（v6.12）** — [Bootlin Elixir](https://elixir.bootlin.com/linux/v6.12/source/include/linux/sched.h)
  - **讀哪裡**：`struct task_struct` 的定義從頭掃一遍，配本章的分組圖對照。重點看 `__state`、`pid`/`tgid`、`mm`/`active_mm`、`sched_class`/`se`、`cred`、`children`/`sibling`/`tasks` 這幾組
  - **和本章的關聯**：這就是本章解剖的對象。第一次讀會被欄位數量嚇到，用分組地圖當索引，別想一次記完

- **`init/init_task.c` 的 `struct task_struct init_task`** — [Bootlin Elixir](https://elixir.bootlin.com/linux/v6.12/source/init/init_task.c)
  - **讀哪裡**：`init_task` 的靜態初始化。這是全系統第一個 task（PID 0）、task 串列的錨點，也是你 gdb `p init_task` 看到的那個
  - **能學到什麼**：一個 task_struct 的每個欄位初始值長什麼樣，比讀定義更具體

### 書籍

- **《Linux Kernel Development, 3rd Ed.》第 3 章 "Process Management"** — Robert Love
  - **讀哪裡**：整章，特別是 task_struct、process descriptor、`current` 的定位、process state 幾節
  - **注意**：書用的是舊 kernel，`state`（現 `__state`）、`thread_info` 找 current 的方式（x86_64 現改 per-CPU）等細節以 6.12 源碼為準；但「process 在 kernel 怎麼被表示」的骨架講得最清楚白話

### 文章

- **LWN: "The rapidly-changing kernel process" 系列與 pid namespace 相關文章** — [LWN.net Kernel index](https://lwn.net/Kernel/Index/)（搜 "pid namespace"、"task_struct"）
  - **讀哪裡**：pid namespace 的設計動機與 `struct pid` 的演進
  - **為什麼值得讀**：本章 PID 管理只點到為止，LWN 補齊「為什麼 PID 不能只是整數」的來龍去脈，接 Ch 49 的 namespace

有了 task_struct 這張地圖，下一個問題自然是：這樣一個好幾 KB、欄位盤根錯節的結構，**是怎麼被生出來的**？`fork()` 一次要複製哪些欄位、共享哪些、清空哪些？thread 和 process 的差別在 clone 那一刻怎麼決定？下一章我們跟著 `fork`/`clone` 進到 `copy_process()`，看一個新 task 如何從無到有。

→ [Ch 10 Process 建立：fork/clone/copy_process](./10-fork-clone-copy-process.md)
