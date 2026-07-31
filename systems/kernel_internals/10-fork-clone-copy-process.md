# Ch 10 — Process 建立：fork/clone/copy_process

> **目標**：搞懂一個新的 task 是怎麼從無到有被造出來的——從使用者空間的 `fork()`/`clone()`/`clone3()`，一路到 `kernel/fork.c` 的 `kernel_clone()` → `copy_process()`，看清楚哪些子系統被複製、哪些被共享（這正是 process 與 thread 的分野），以及新 task 為什麼一被喚醒就能從 `fork()` 回傳 `0` 開始跑。

## 為什麼需要這個？

Unix 有個看似奇怪的設計決定：建立新 process 的方式，是把「造出一個空白 process」和「載入要跑的程式」拆成兩個 syscall——`fork()` 複製自己，`execve()` 再把自己換成別的程式。別的系統（Windows 的 `CreateProcess`、後來的 `posix_spawn`）是一步到位「開一個 process 跑這支程式」。

拆成兩步看起來多餘，但它換來一個極大的彈性：**在 `fork()` 之後、`execve()` 之前那個空窗，你握著一個和父行程一模一樣的分身，可以動手改它的環境再讓它變身**。shell 的 I/O redirection（`ls > out.txt`）就是靠這個：fork 出子行程 → 在子行程裡把 fd 1 重導到檔案 → 才 exec `ls`。這件事在一步到位的 API 裡做起來很彆扭。

但這帶來一個效能難題：`fork()` 要複製父行程的**整個位址空間**。一個吃了 4 GB 記憶體的 process fork 一下就要複製 4 GB？而且多數情況下子行程馬上就 `execve()` 把這 4 GB 全丟掉。這太蠢了。kernel 的答案是 **copy-on-write（CoW，寫時複製）**：fork 時不真的複製記憶體，只複製 page table 並把兩邊都標成唯讀，等真的有人寫才複製那一頁。這是 `fork()` 能快的根本原因，這章會點出機制、細節留給 Ch 20。

還有一件事：Linux 根本沒有「process」和「thread」兩套建立機制。`fork()`、`pthread_create()`、`clone()` **走的是同一條路**，差別只在一組 CLONE_* flag——要不要共享位址空間、fd 表、signal handler。一切都是 `clone`。這章要把這條統一的路徑拆開來看。

## 先建立直覺

先把「使用者空間有一堆 API，kernel 裡其實只有一條路」這件事畫清楚：

```
  使用者空間                        kernel/fork.c
  ─────────                        ────────────────────────────────
  fork()      ─┐
  vfork()      │                  ┌──────────────────┐
  clone()      ├──► syscall ────► │  kernel_clone()  │  (舊名 _do_fork)
  clone3()     │                  │  接收一個         │
  pthread_    ─┘                  │  kernel_clone_args│
  create()                        └────────┬─────────┘
  (glibc 內部                              │
   也是 clone)                             ▼
                                  ┌──────────────────┐
                                  │  copy_process()  │  ← 這章的靈魂
                                  └────────┬─────────┘
                                           │
                                           ▼
                                  ┌──────────────────┐
                                  │ wake_up_new_task │ ──► 丟進 run queue (Ch 11)
                                  └──────────────────┘
```

五個使用者空間 API，匯流到**同一個** `kernel_clone()`。它們的差別，全部被編碼成一組 flag 塞進 `struct kernel_clone_args`。理解這章的關鍵心法：

> **「複製一個 process」的本質，是「複製一個 task_struct，然後對它的每個子系統，逐一決定要深拷貝一份新的、還是淺拷貝共享父行程的」。CLONE_* flag 就是這一連串「複製 or 共享」決策的開關。**

一個 task_struct（Ch 9）裡面掛著一堆指標：`mm`（位址空間）、`files`（fd 表）、`fs`（cwd/root）、`sighand`（signal handler 表）、`signal`（signal 狀態）、`cred`（權限）。`copy_process()` 做的事，就是對這每一個指標問一句：「這次 clone，你要不要獨立的一份？」

- `fork()`：全部都要獨立的 → 一個完整的新 process
- `pthread_create()`：`mm`/`files`/`sighand`/`signal` 全部共享 → 一個新 thread，和父行程同一個位址空間

process 和 thread 在 kernel 眼中都是 task_struct，差別只是這幾個指標指向自己的還是共用的。這就是 Ch 9 講的 thread vs process 在建立時如何被實現的。

## 從 syscall 到 kernel_clone

四個相關 syscall 都定義在 `kernel/fork.c`，用 `SYSCALL_DEFINE` 巨集展開：

- `SYSCALL_DEFINE0(fork)`：等價於 `clone(SIGCHLD)`——什麼都不共享，子行程結束時送 `SIGCHLD` 給父行程。有些不支援 MMU 的架構甚至沒實作它。
- `SYSCALL_DEFINE0(vfork)`：`clone(CLONE_VFORK | CLONE_VM | SIGCHLD)`——共享位址空間，且父行程**阻塞等子行程 exec 或 exit**。
- `SYSCALL_DEFINE5(clone, ...)`：經典 clone，直接收一個 flags 參數（外加 stack、tls、兩個 tid 指標）。
- `SYSCALL_DEFINE2(clone3, ...)`：新版（5.3 起），收一個 `struct clone_args`（在 `include/uapi/linux/sched.h`），是可擴充的結構體，能表達舊 `clone` 表達不了的東西（例如指定 pidfd、set_tid、cgroup fd）。

它們各自把參數整理進 `struct kernel_clone_args`（定義在 `include/linux/sched/task.h`），然後全部呼叫 `kernel_clone()`：

```c
// kernel/fork.c，SYSCALL_DEFINE0(fork) 大致長這樣（節錄示意）
SYSCALL_DEFINE0(fork)
{
    struct kernel_clone_args args = {
        .exit_signal = SIGCHLD,
    };
    return kernel_clone(&args);
}
```

`kernel_clone()`（v5.10 前叫 `_do_fork`，更早叫 `do_fork`；讀舊資料會看到這些名字）做三件大事：

1. 呼叫 `copy_process()` 造出一個新的、還沒開始跑的 task_struct。
2. 呼叫 `wake_up_new_task()` 把它丟進 scheduler 的 run queue。
3. 如果是 `vfork`，在這裡阻塞父行程，等子行程用 completion 機制喚醒它。

> **glibc 的 `fork()` 呼叫的是 `clone` 還是 `fork` syscall？** 現代 glibc 的 `fork()` 底層走的是 `clone` syscall（帶 `CLONE_CHILD_SETTID | CLONE_CHILD_CLEARTID | SIGCHLD`），不是裸的 `fork` syscall——那個 `CLEARTID` 是為了讓 pthread 的 join 機制運作。你用 `strace -f` 跑一支 `fork()` 程式，看到的會是 `clone(...)` 而不是 `fork(...)`。這印證了「一切都是 clone」。（strace 觀測見本 repo `observability_tools` 課。）

## copy_process()：這章的靈魂

`copy_process()` 是整個 process 建立的心臟。它很長（幾百行），但骨架清楚：**先複製 task_struct 本體，再逐一複製或共享每個子系統，任何一步失敗就沿著 `bad_fork_*` label 反向清理**。我們把骨架拆開：

### 第一步：dup_task_struct — 複製本體與配新 stack

```c
// kernel/fork.c，copy_process() 開頭
p = dup_task_struct(current, node);
```

`dup_task_struct()`（同檔）做兩件事：

1. **配一個新的 task_struct**，把父行程的內容整個 memcpy 過來（所以新 task 一開始的欄位值和父行程一樣，接下來才逐項改）。
2. **配一個全新的 kernel stack**（`alloc_thread_stack_node()`），並讓新 task 的 `stack` 指過去。**每個 task 一定有自己獨立的 kernel stack**——連 thread 也是。thread 共享的是 user space 的位址空間，但每個 thread 在 kernel 裡跑時各有各的 kernel stack 和 `thread_info`（Ch 2 講過 kernel stack 與 `current` 的關係）。

這裡有個關鍵：memcpy 之後，新 task 的所有指標欄位（`mm`、`files`…）**暫時都還指著父行程的物件**。接下來每個 `copy_*` 步驟的工作，就是視 flag 決定「換成一份新的」還是「增加參考計數繼續共享」。

### 第二步：逐一 copy_* 各子系統

`copy_process()` 接著依序呼叫一串 `copy_*`，每一個對應 task_struct 的一個子系統。這是這章的核心表：

| 步驟 | 檔案 | 受哪個 CLONE flag 控制 | flag 設了會怎樣 | flag 沒設會怎樣 |
|---|---|---|---|---|
| `copy_creds` | `kernel/cred.c` | （見 Ch 47） | — | 複製一份 cred（權限） |
| `copy_semundo` | `ipc/sem.c` | `CLONE_SYSVSEM` | 共享 SysV semaphore undo | 各自一份 |
| `copy_files` | `kernel/fork.c` | `CLONE_FILES` | 共享 fd 表（`files_struct`） | 複製一份 fd 表（dup 每個 fd） |
| `copy_fs` | `kernel/fork.c` | `CLONE_FS` | 共享 cwd/root（`fs_struct`） | 複製一份 |
| `copy_sighand` | `kernel/fork.c` | `CLONE_SIGHAND` | 共享 signal handler 表 | 複製一份 handler 表 |
| `copy_signal` | `kernel/fork.c` | `CLONE_THREAD` | 加入同一個 thread group、共享 signal 狀態 | 開一個新的 signal_struct |
| `copy_mm` | `kernel/fork.c` | `CLONE_VM` | 共享位址空間（`mm_struct`）→ 這就是 thread | 複製位址空間（CoW，見下節） |
| `copy_namespaces` | `kernel/nsproxy.c` | `CLONE_NEW*`（見 Ch 49） | 進新的 namespace | 沿用父的 namespace |
| `copy_io` | `block/blk-ioc.c` | `CLONE_IO` | 共享 I/O context | 各自一份 |
| `copy_thread` | `arch/x86/kernel/process.c` | — | 設定架構相關的 register/return（見下節） | — |

每個 `copy_*` 的內部邏輯都是同一個模式，以 `copy_files` 為例：

```c
// kernel/fork.c，copy_files() 骨架
static int copy_files(unsigned long clone_flags, struct task_struct *tsk, ...)
{
    struct files_struct *oldf = current->files;

    if (clone_flags & CLONE_FILES) {
        atomic_inc(&oldf->count);   // 共享：只加參考計數
        goto out;
    }
    // 不共享：複製一份新的 files_struct，逐一 dup 每個 open 的 fd
    newf = dup_fd(oldf, ...);
    tsk->files = newf;
out:
    return 0;
}
```

**「flag 設了 → 加參考計數共享；flag 沒設 → 深拷貝一份」**——這個二選一模式在每個 `copy_*` 裡重複出現。看懂一個就看懂全部。這也是為什麼 process 建立比想像中「便宜」：多數東西能共享就共享，只有 `copy_mm` 在不共享時要動 page table（下節）。

### 第三步：分配 PID 與掛進各種關係

子系統複製完，`copy_process()` 呼叫 `alloc_pid()`（`kernel/pid.c`）分配 PID。PID 不只是一個整數——它是一個 `struct pid`，同時在**多個 pid namespace** 裡各有一個 number。所以一個 container 裡的 process 在 container 內看到 PID 是 42，在 host 上看是 31337，同一個 `struct pid` 掛兩個 namespace 的 number（pid namespace 細節見 Ch 49）。

接著把新 task 掛進各種資料結構：加入父行程的 children list、若 `CLONE_THREAD` 則加入 thread group、加進全域 task list、掛進 PID 的 hash。這些完成後，這個 task 才「存在」於系統中，但**還沒開始跑**。

### 失敗清理：bad_fork_* 的階梯

`copy_process()` 中間任何一步失敗（記憶體不夠、超過 rlimit 的 process 數上限…），都會 `goto` 到對應的 `bad_fork_cleanup_*` label，**反向**把已經做的步驟一一還原（已 copy 的 mm 要 free、已加的參考計數要減）。這是 kernel 錯誤處理的經典 goto-ladder 寫法——每個資源的清理都在它自己的 label，失敗點越晚，要清的東西越多。讀 `copy_process()` 尾巴那一串 label 就能看出資源獲取的順序。

## 底層機制：copy_mm 與 CoW 讓 fork 變快

`copy_mm`（`kernel/fork.c`）是最有戲的一步，因為位址空間最大。分兩種情況：

**情況一：`CLONE_VM` 有設（thread）**——直接讓新 task 的 `mm` 指向父行程的 `mm_struct`，`mmget()` 加參考計數。兩個 task 從此共用同一張 page table、同一片記憶體。這就是 thread。

**情況二：`CLONE_VM` 沒設（fork 出新 process）**——呼叫 `dup_mm()` → `dup_mmap()`，這裡是 CoW 的舞台：

```
   fork 前：父行程一頁可寫記憶體

       父 PTE ──► [ physical page ]   (RW)

   fork 後（dup_mmap 複製 page table，兩邊都標唯讀）：

       父 PTE ──┐
                ├──► [ physical page ]   (RO, 只有一份實體頁！)
       子 PTE ──┘

   任一方寫入 → CPU 觸發 page fault → do_wups_fault → 這時才複製：

       父 PTE ──► [ physical page A ]  (RW)
       子 PTE ──► [ physical page B ]  (RW，複製出來的新頁)
```

`dup_mmap()` 走過父行程每一個 VMA（virtual memory area），複製 page table 項，並把可寫的頁在**父子兩邊都標成唯讀**。實體記憶體一頁都沒複製——兩邊的 PTE 指向同一批實體頁。等到任何一方試圖寫，CPU 產生 write protection fault，page fault handler 發現「這頁是 CoW 頁」，才配一張新頁、複製內容、把寫入方的 PTE 改回可寫。

這是 `fork()` 快的根本原因：一個 4 GB 的 process fork，只需要複製它的 page table（幾 MB），不是 4 GB 資料。而且如果子行程馬上 `execve()`，整張 page table 直接丟棄，一頁都沒白複製。

> 這裡只點到 CoW 的骨架。「page fault handler 怎麼判斷這是 CoW 頁」「`vm_flags` 的 `VM_WRITE` 與 PTE 的 write bit 如何配合」「rmap 怎麼追蹤誰在共享這頁」是 **Ch 19（page fault）、Ch 20（demand paging / CoW / rmap）** 的主場。這章你只要記住：**fork 複製 page table 並標唯讀，寫時才真的複製記憶體**。

## 底層機制：新 task 怎麼開始跑

到這裡新 task 造好了、掛進系統了，但它從沒執行過一條指令。它怎麼「開始」？關鍵在兩個地方：`copy_thread()` 事先佈好局，`wake_up_new_task()` 扣下扳機。

### copy_thread：偽造一個「剛從中斷返回」的現場

`copy_thread()`（x86_64 在 `arch/x86/kernel/process.c`）是架構相關的一步。它在新 task 的 kernel stack 上**偽造一組暫存器狀態**，讓這個 task 第一次被排程執行時，看起來像是「剛從一次 syscall / 中斷返回使用者空間」。它做的關鍵事情：

1. 把新 task 的 `thread.sp`（saved stack pointer）設成指向 stack 上一個叫 `struct fork_frame` 的結構。
2. 把新 task 的 return address 設成 **`ret_from_fork`**（`arch/x86/entry/entry_64.S` 的一段組語）。
3. 把 stack 上那份 `pt_regs`（將被 pop 回使用者空間的暫存器）裡的 **RAX 設成 0**。

第 3 點是關鍵魔法：**RAX 是 x86_64 的 syscall 回傳值暫存器**。父行程從 `fork()` syscall 返回時，RAX 是子行程的 PID；而子行程的這份 pt_regs 被 `copy_thread` 手動填了 RAX = 0，所以子行程從 `fork()` 「返回」時拿到的是 0。這就是「fork 回傳兩次、父拿 PID、子拿 0」在最底層是怎麼實現的——不是什麼魔法，就是 kernel 在子行程的 stack 上先把回傳暫存器填好。

```
  第一次被排程執行時，新 task 的路徑：

   context switch 切到新 task
        │
        ▼
   ret_from_fork  (entry_64.S)
        │  呼叫 schedule_tail() 收尾上一個 task
        ▼
   如果是 kernel thread → 跳去執行 kthread 的函式
   如果是使用者 task    → 走到 swapgs + iretq，pop 那份 pt_regs
        │
        ▼
   回到使用者空間，RAX = 0
        │
        ▼
   使用者程式在 fork() 呼叫的下一行醒來，if(pid==0) 這一支成立
```

> ARM64 的機制概念相同，只是暫存器名不同：回傳值在 `x0`，`copy_thread` 把子行程的 `pt_regs->regs[0]` 設 0，返回路徑是 `ret_from_fork`（`arch/arm64/kernel/entry.S`）。「哪個暫存器裝 syscall 回傳值」是 ABI 差異，機制一致。

### wake_up_new_task：扣扳機

回到 `kernel_clone()`，`copy_process()` 成功回傳後，呼叫 `wake_up_new_task()`（`kernel/sched/core.c`）：

1. 把新 task 狀態設為 `TASK_RUNNING`。
2. 呼叫 scheduler class 的 `enqueue_task`，把它放進某個 CPU 的 run queue。
3. 呼叫 `check_preempt_curr` 看新 task 該不該搶佔當前正在跑的 task。

在這之前，新 task 只是一個「存在但沒排程資格」的殼。`wake_up_new_task` 之後，它成為 scheduler 的候選人，下次排程就可能輪到它，然後從 `ret_from_fork` 開始跑。**「怎麼放進哪個 run queue、怎麼決定要不要搶佔」是 Ch 11（排程器框架）的主場**，這章只要知道 fork 的最後一步是把新 task 交給 scheduler。

## kernel thread：沒有使用者空間的 task

不是所有 task 都有使用者空間。`kswapd`、`ksoftirqd`、workqueue 的 worker 都是 **kernel thread**——只在 kernel 空間跑、`mm` 為 `NULL`（它們借用被切換出去的 task 的位址空間，Ch 14 講）。它們也是 task_struct，也是走 clone 造出來的，但入口不同：

```c
// 建立並啟動一個 kernel thread
struct task_struct *t = kthread_run(threadfn, arg, "my_kthread");
// 等價於 kthread_create() + wake_up_process()
```

`kthread_run`/`kthread_create`（`kernel/kthread.c`）不直接呼叫 `copy_process`。它們把「請幫我建一個 kernel thread」的請求丟給一個特殊的 task：**`kthreadd`，PID 2**。`kthreadd` 是所有 kernel thread 的父行程（Ch 3 講開機時它被建立），它在一個迴圈裡等請求，收到就用 `kernel_thread()`（內部走 `kernel_clone`，帶 `CLONE_VM` 但無使用者 mm）真正把 thread 造出來。

為什麼要繞道 `kthreadd`？因為 kernel thread 必須有個乾淨、可預測的建立環境（不繼承呼叫者的 signal mask、cgroup、namespace 等亂七八糟的狀態）。統一由 PID 2 來生，保證每個 kernel thread 的出身一致。你在 `ps` 裡看到方括號的 process（`[kworker/0:1]`、`[ksoftirqd/0]`）全都是 `kthreadd` 的後代——`ps --ppid 2` 列得出來。

## 動手：gdb 追 fork，寫模組起一個 kernel thread

### 實驗一：gdb 停在 copy_process 看新 task 誕生

按 Ch 0 把 QEMU + gdb 接好（`-S -s`，關 KASLR）。在 gdb：

```gdb
(gdb) target remote :1234
(gdb) break copy_process
(gdb) break wake_up_new_task
(gdb) continue
```

回到 QEMU 的 shell，跑任何會 fork 的指令（busybox 的每個外部指令都 fork+exec）：

```
/ # ls
```

gdb 會停在 `copy_process`。看幾件事：

```gdb
(gdb) print current->comm          # 誰在 fork？應該是 sh
(gdb) print clone_flags            # 這次 clone 的 flag（fork 的話含 SIGCHLD）
(gdb) finish                       # 讓 copy_process 跑完
(gdb) print $rax                   # copy_process 回傳的新 task_struct 指標
(gdb) continue                     # 走到 wake_up_new_task
(gdb) print p->pid                 # 新 task 的 PID
(gdb) print p->comm                # 此刻還是 sh（execve 還沒把它換成 ls）
```

重點觀察：在 `wake_up_new_task` 這一刻，新 task 的 `comm` 還是 `sh`——因為 `ls` 是後面 `execve()` 才載入的。fork 造出的是父行程的**分身**，變身是下一個 syscall 的事（`execve` 是 Ch 之後的主題）。

想看 CoW 的痕跡，可以進一步 `break copy_mm` 或 `break dup_mmap`，確認 fork（`ls`）會進，而純 thread 建立（`CLONE_VM`）不會進 `dup_mmap`。

### 實驗二：寫模組用 kthread_run 起一個 kernel thread

這個模組起一個 kernel thread，每兩秒印一次訊息，直到模組卸載。

```c
// kthread_demo.c
#include <linux/init.h>
#include <linux/module.h>
#include <linux/kthread.h>
#include <linux/delay.h>
#include <linux/sched.h>

static struct task_struct *demo_task;

static int demo_threadfn(void *arg)
{
    int count = 0;
    // kthread 的標準迴圈：直到有人叫我停
    while (!kthread_should_stop()) {
        pr_info("kthread_demo: tick %d, my pid=%d, comm=%s, parent pid=%d\n",
                count++, current->pid, current->comm,
                current->real_parent->pid);   // 應該是 2 (kthreadd)
        ssleep(2);                            // 睡 2 秒（會讓出 CPU）
    }
    pr_info("kthread_demo: asked to stop, exiting\n");
    return 0;
}

static int __init kthread_demo_init(void)
{
    // kthread_run = kthread_create + wake_up_process
    demo_task = kthread_run(demo_threadfn, NULL, "demo_kthread");
    if (IS_ERR(demo_task)) {
        pr_err("kthread_demo: failed to create kthread\n");
        return PTR_ERR(demo_task);
    }
    pr_info("kthread_demo: created kthread, pid=%d\n", demo_task->pid);
    return 0;
}

static void __exit kthread_demo_exit(void)
{
    kthread_stop(demo_task);   // 設 should_stop + 等它真的結束
    pr_info("kthread_demo: module unloaded\n");
}

module_init(kthread_demo_init);
module_exit(kthread_demo_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Ch10: create a kernel thread with kthread_run");
```

Makefile 照 Ch 0。編好放進 initramfs，在 QEMU 裡：

```
/ # insmod /kthread_demo.ko
kthread_demo: created kthread, pid=42
kthread_demo: tick 0, my pid=42, comm=demo_kthread, parent pid=2
kthread_demo: tick 1, my pid=42, comm=demo_kthread, parent pid=2
/ # ps | grep demo         # 看得到 [demo_kthread]，方括號代表 kernel thread
/ # rmmod kthread_demo
kthread_demo: asked to stop, exiting
kthread_demo: module unloaded
```

三個觀察點：（1）`parent pid=2` 證明它的父是 `kthreadd`，不是 `insmod` 你的那個 shell；（2）`ps` 裡它是 `[demo_kthread]`（方括號 = kernel thread，`mm` 為 NULL）；（3）`kthread_stop` + `kthread_should_stop` 是 kernel thread 收尾的標準協議——直接讓 threadfn `return` 而不用這對機制，卸載模組會出事（見踩雷）。

## 對比與取捨

| 建立方式 | 底層 flag（關鍵者） | 位址空間 | fd 表 | 典型用途 |
|---|---|---|---|---|
| `fork()` | `SIGCHLD` | 複製（CoW） | 複製 | 傳統開子行程，接著 exec |
| `vfork()` | `CLONE_VM \| CLONE_VFORK` | 共享，父阻塞 | 複製 | 馬上要 exec、想省掉 CoW 開銷的極端場景 |
| `pthread_create()` | `CLONE_VM \| CLONE_FILES \| CLONE_SIGHAND \| CLONE_THREAD` | 共享 | 共享 | 同一 process 內的並行 |
| `clone3()` | 任意組合（結構化） | 依 flag | 依 flag | 容器 runtime、需要 pidfd/set_tid 的場合 |
| `kthread_run()` | 內部 `kernel_thread`，無 user mm | 無（`mm=NULL`） | — | kernel 內部背景工作 |

`vfork` 值得多說一句：它比 fork 更省（連 page table 都不複製，直接共享），代價是**父行程被凍住、子行程借用父的位址空間**，子行程在 exec 或 exit 前不能亂改記憶體、不能從呼叫 vfork 的函式 return（會踩爛父行程的 stack）。它是為「fork 完立刻 exec」這個窄場景做的優化，CoW 出現後大部分優勢消失，現在很少直接用，但理解它有助於看懂 `posix_spawn` 的底層。

## 踩雷集錦

1. **以為 fork 真的複製了整個記憶體**。錯。fork 複製的是 page table 並標唯讀，實體記憶體靠 CoW 拖到寫入才複製。一個吃 4 GB 的 process fork 不會瞬間多用 4 GB。正確認識：**fork 便宜是因為它把「複製」延後到「真的要寫」的那一刻**。

2. **以為 thread 和 process 是兩種東西、走兩套 kernel 路徑**。錯。兩者都是 task_struct，都走 `kernel_clone → copy_process`，差別只在 CLONE_* flag（thread 多設了 `CLONE_VM | CLONE_FILES | CLONE_SIGHAND | CLONE_THREAD`）。正確認識：**Linux 只有 task，thread 是「共享得比較多的 task」**。

3. **以為「fork 回傳兩次」是什麼特殊控制流魔法**。其實只是 `copy_thread` 在子行程的 stack 上把回傳暫存器（x86_64 的 RAX / ARM64 的 x0）填成 0。父子從 kernel 返回時各自讀自己 stack 上那份 pt_regs，一個是 PID 一個是 0。沒有魔法，只有兩份預先填好的暫存器狀態。

4. **kernel thread 的 threadfn 直接 return 卻不配合 `kthread_should_stop`**。如果 threadfn 自己 return 了，但你在 exit 時又呼叫 `kthread_stop` 去停一個已經死掉的 task，會 use-after-free / oops。標準協議是：threadfn 用 `while (!kthread_should_stop())` 迴圈，模組 exit 用 `kthread_stop()`——它負責設旗標並等 thread 真的結束。除非你的 thread 註定自己跑完就消失（那就別呼叫 `kthread_stop`）。

5. **在 kernel thread 裡碰 `current->mm`**。kernel thread 的 `mm` 是 NULL，直接解參考會 NULL pointer oops。kernel thread 不該存取使用者空間記憶體——它根本沒有自己的使用者位址空間。要存取特定 process 的 mm 得用 `use_mm()`/`kthread_use_mm()` 借用。

## 進階：再往深一層

- **`CLONE_PIDFD` 與 pidfd**：`clone3` 可以回傳一個 file descriptor（pidfd）代表新子行程，解決了「用 PID 追蹤子行程有 PID 重用 race」的老問題。容器 runtime（runc）和 systemd 都在用。面試被問「怎麼安全地等一個特定子行程」，pidfd 是現代答案。

- **`copy_process` 裡的 rlimit 與 `nr_threads` 檢查**：process 數不是無限的。`copy_process` 開頭會檢查 `RLIMIT_NPROC` 和全域 `max_threads`。fork bomb 之所以能被 `ulimit -u` 或 cgroup 的 `pids.max` 擋住，就是在這裡（cgroup pids controller 見 Ch 50）。

- **`ret_from_fork` 為什麼要先呼叫 `schedule_tail`**：context switch 把 CPU 交給新 task 時，上一個 task 的收尾（release run queue lock 等）還沒做完，得由被切進來的 task 代為完成——這是 scheduler 的「finish_task_switch」約定（Ch 14）。新 task 第一次跑一定先過 `schedule_tail` 補這一刀。

- **為什麼 `dup_task_struct` 要獨立 kernel stack 但可共享 user stack**：user stack 是位址空間的一部分，thread 共享 `mm` 就共享 user stack 的位址範圍（實務上 pthread 給每個 thread 在同一位址空間裡切一塊獨立 user stack）。但 kernel stack 是每個 task 執行 kernel code 時的私有工作區，絕不能共享——否則兩個 task 同時進 kernel 就互踩。

- **`fork()` 之後、`exec()` 之前只能呼叫 async-signal-safe 函式（多執行緒程式）**：一個多 thread 的 process fork 後，子行程只有呼叫 fork 的那個 thread 存活，其他 thread 憑空消失，它們持有的 lock 永遠不會釋放。所以 POSIX 規定 fork 和 exec 之間只能用 async-signal-safe 函式。這是「一切都是 clone」在使用者空間留下的坑。

## 動手練習

1. **證明 fork 走的是 clone syscall**：在 QEMU 裡（或任何 Linux）寫一支呼叫 `fork()` 的 C 程式，`strace -f ./a.out`，確認你看到的是 `clone(...)` 而不是 `fork(...)`，並記下它帶了哪些 flag。對照本章 glibc 那段。

2. **gdb 抓兩次 clone 的 flag 差異**：`break copy_process`，在 shell 裡分別跑一個外部指令（fork+exec）和……找一個會建 thread 的程式（例如編一支 `pthread_create` 的程式塞進 initramfs）。比較兩次 `copy_process` 收到的 `clone_flags`——確認 pthread 那次有 `CLONE_VM | CLONE_THREAD`，fork 那次沒有。

3. **手動填 RAX 的證據**：`break copy_thread`（或在 `ret_from_fork` 附近），想辦法在 gdb 裡找到子行程 stack 上那份 `pt_regs`，確認它的 `ax`/`orig_ax` 欄位在返回前被設成 0。這是「fork 回傳 0 給子行程」的物理證據。（提示：`copy_thread` 的參數裡有 `struct kernel_clone_args`，childregs 從 `task_pt_regs(p)` 拿。）

4. **弄壞 kernel thread**：把實驗二的 threadfn 改成不理會 `kthread_should_stop`、直接 `while(1) { ...; ssleep(2); }`，然後 `rmmod`。觀察會發生什麼（`kthread_stop` 會一直等一個不肯停的 thread）。修好它，體會 `kthread_should_stop` 這個協議為什麼存在。

5. **數 kthreadd 的孩子**：在 QEMU 裡 `ps --ppid 2`（或 `ps -o pid,ppid,comm | grep '^\s*[0-9]* *2 '`），列出所有 PID 2 的直接子行程。這些全是 kernel thread。對照 `dmesg` 裡開機時出現的那些 `[kworker]`、`[ksoftirqd]`。

## 本章重點整理

- 使用者空間的 `fork`/`vfork`/`clone`/`clone3`/`pthread_create` 全部匯流到 `kernel/fork.c` 的 `kernel_clone()` → `copy_process()`；差別只是一組 CLONE_* flag。**一切都是 clone**。
- `copy_process()` 的骨架是：`dup_task_struct`（複製本體 + 配獨立 kernel stack）→ 一連串 `copy_*`（每個依 flag 決定深拷貝 or 加參考計數共享）→ 分配 PID、掛進各關係 → 失敗走 `bad_fork_*` 反向清理。
- `copy_mm` 在不共享時走 CoW：複製 page table、兩邊標唯讀，寫入時才真的複製那頁——這是 fork 快的根本原因（機制詳見 Ch 20）。
- 新 task 靠 `copy_thread` 事先在 stack 佈好「假的中斷返回現場」（含把子行程回傳暫存器設 0），再由 `wake_up_new_task` 丟進 run queue，第一次被排程時從 `ret_from_fork` 開始跑（排程細節 Ch 11、Ch 14）。
- kernel thread（`mm=NULL`）由 `kthread_run` 委託 PID 2 `kthreadd` 建立，收尾用 `kthread_should_stop` + `kthread_stop` 的協議。

## 自我檢核

- [ ] 不看筆記，能畫出 `fork() → kernel_clone → copy_process（各 copy_* 步驟）→ wake_up_new_task` 這條路徑
- [ ] 能解釋 process 和 thread 在 kernel 建立時的唯一差別（哪幾個 CLONE flag），以及它們共享/獨立哪些子系統
- [ ] 面試被問「fork 為什麼快、複製了什麼」，能講出 CoW：複製 page table 標唯讀、寫時才複製實體頁
- [ ] 能解釋「fork 回傳兩次、子行程拿 0」在最底層（`copy_thread` 填回傳暫存器）是怎麼實現的
- [ ] 能說出為什麼每個 task（含 thread）都要有自己的 kernel stack，但可以共享 user 位址空間
- [ ] 能寫一個用 `kthread_run` 起 kernel thread 的模組，並正確用 `kthread_should_stop`/`kthread_stop` 收尾
- [ ] 能解釋為什麼 kernel thread 統一由 PID 2 `kthreadd` 生，以及它的 `mm` 為什麼是 NULL

## 延伸閱讀

### 官方文件 / 源碼

- **`kernel/fork.c` 的 `copy_process()`（[elixir v6.12](https://elixir.bootlin.com/linux/v6.12/source/kernel/fork.c)）**
  - **讀哪裡**：從 `copy_process` 開頭讀到結尾那一串 `bad_fork_*` label。先看它呼叫了哪些 `copy_*`（對照本章的表），再看失敗清理的 goto 階梯
  - **和本章的關聯**：這章講的全部在這個函式裡；讀一遍源碼，本章的表就活起來了

- **`man 2 clone` / `man 2 clone3`**
  - **讀哪裡**：CLONE_* flag 那一大段列表，逐個對照它們控制哪個子系統的共享
  - **能學到什麼**：使用者空間看到的完整 flag 語意，補足本章表格沒展開的每個 flag 細節

### 部落格 / 課程

- **[LWN: "A pair of pidfd system calls"](https://lwn.net/Articles/794707/) 與 pidfd 系列**
  - **讀哪裡**：pidfd 的動機與 `CLONE_PIDFD` 的用法
  - **為什麼值得讀**：本章進階提到的 pidfd，這是最權威的來龍去脈；理解容器 runtime 為什麼需要它

- **[Bootlin: Understanding the Linux kernel（process management 章節）](https://bootlin.com/docs/)**
  - **讀哪裡**：process creation / kernel thread 相關投影片
  - **前提**：跟完本章、Ch 9；配 elixir 邊讀邊跳源碼

### 書籍

- **《Understanding the Linux Kernel, 3rd Ed.》** — Bovet & Cesati（O'Reilly）
  - **這本書的定位**：第 3 章「Processes」把 `do_fork`（今 `kernel_clone`）→ `copy_process` 拆得極細，含 CoW 與 thread group 的來龍去脈
  - **注意**：講的是 2.6 kernel，函式已改名（`do_fork`→`kernel_clone`），但骨架與設計理念沒變，是理解「為什麼這樣設計」的最佳讀物

- **《Linux Kernel Development, 3rd Ed.》** — Robert Love
  - **這本書的定位**：第 3 章「Process Management」用更白話講 fork/CoW/kernel thread，比 Bovet 好入口，適合先讀它建立直覺再啃源碼

新 task 造好、丟進了 run queue——但「run queue 到底是什麼、scheduler 怎麼從一堆 ready 的 task 裡挑下一個來跑」還沒交代。下一章我們拆開排程器的框架：scheduler class 的分層設計與 run queue 的結構。

→ [Ch 11 排程器框架：scheduler class 與 run queue](./11-scheduler-framework.md)
