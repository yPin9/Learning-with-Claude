# Ch 30 — 讀有 lock 的程式碼：臨界區、lock 規則與 lockdep 標註

> **目標**：把 lock 當成程式碼裡最密集的「後設資訊」來讀：看到 `spin_lock_irqsave` 就知道這份資料會被中斷碰、看到 `mutex_lock` 就知道這裡可以睡；能從 struct 註解、`lockdep_assert_held`、`__must_hold`、命名慣例推出「這把 lock 保護哪些欄位」；會找並使用檔案開頭的 lock ordering 註解；讀得懂 `guard(mutex)` 這類新寫法，並避開常見的誤讀。
> **鴻溝**：本章主要練**慣用法鴻溝**（各種 lock API 的讀碼意義）與**領域鴻溝**（並行與執行情境），順帶一點**脈絡鴻溝**（為什麼這裡要放掉 lock 再拿回來）。

> **環境**：Linux kernel v6.18 原始碼與 `O=~/code_reading/build` 的 x86_64 build（`CONFIG_PROVE_LOCKING` 未開）；QEMU TCG + ftrace；user-space 範例以 gcc 11.4 + pthread 在 WSL2 上執行。本章**不教怎麼設計 lock**，只教怎麼讀。

## 為什麼需要這個？

初學者讀 kernel 時，常把 `spin_lock(&x->lock)` / `spin_unlock(&x->lock)` 當成雜訊跳過，專心看中間的「邏輯」。這是在丟掉資訊。一個 lock 呼叫一次告訴你好幾件事：

- **有誰會同時碰這份資料**：別的 CPU？中斷 handler？softirq？
- **這段程式碼在什麼執行情境**：可以睡嗎？中斷是開著還是關著？
- **哪些欄位是一組的**：被同一把 lock 保護的欄位，必須一起看才有意義。
- **作者擔心什麼 race**：在某處放掉 lock 再拿回來，通常意味著「這中間世界可能變了，回來要重新檢查」。

kernel 的 v6.18 tree 裡，`mutex_lock(` 出現兩萬三千多次、`spin_lock_irqsave(` 一萬六千多次。它們不是雜訊，是**作者用程式碼寫下的並行設計文件**。這份文件的語法，就是本章的內容。

Spinellis 的原書在第 5 章談過並行與同步，但那時的例子是 user-space 的 thread 與 signal。kernel 多了兩件 user space 沒有的事：中斷可以在任何兩條指令之間插進來，以及「不能睡」的情境。這兩件事讓 kernel 的 lock 種類比 pthread 多得多，每一種都帶著不同的訊息。

## 先建立直覺

把一把 lock 想成會議室門口的牌子，而 lock 的**種類**就是牌子上的使用規則：

```
spin_lock         「使用中，請在門口站著等，馬上好」   → 不能在裡面睡；等的人在空轉
spin_lock_irqsave 「使用中，而且電話（中斷）也轉走了」 → 中斷 handler 也會用這間會議室
spin_lock_bh      「使用中，下半場（softirq）先別進來」 → softirq 也會用
mutex_lock        「使用中，請去旁邊坐著等，叫號」     → 裡面的人可能會開很久，可以睡
down_read         「閱覽室：大家可以一起看，要改得清場」 → 讀多寫少
read_seqcount_*   「不擋人，看完對一下版本號，變了就重看」→ 讀端完全不拿鎖
rcu_read_lock     「看舊版沒關係，新版另外印」          → 見 Ch 31
```

讀碼時你不需要知道 spinlock 內部怎麼實作，但要能從牌子推出**誰會來敲門**。這是本章最重要的一個能力。

> 如果你對 `READ_ONCE` 與 memory barrier 還不熟，先回看 [Ch 21](./21-read-once-and-barriers.md)。seqlock 與 RCU 的讀端都依賴它。

## 每種 lock 告訴你什麼

| 你看到 | 它告訴你 | 推論 |
|---|---|---|
| `spin_lock(&l)` | 臨界區很短；裡面**不能睡** | 中間不會有 `kmalloc(GFP_KERNEL)`、`copy_to_user`、`mutex_lock`；有的話就是 bug |
| `spin_lock_irqsave(&l, flags)` | 這份資料**也會被 hardirq handler 碰**，且呼叫者不確定中斷現在是開是關 | 去找誰在中斷情境下拿同一把 lock |
| `spin_lock_irq(&l)` | 同上，但呼叫者**確定**中斷目前是開的（process context） | 這個函式不會從中斷或已關中斷的地方被呼叫 |
| `spin_lock_bh(&l)` | 資料會被 softirq／tasklet 碰（網路堆疊最常見） | 去找 softirq 側的使用者 |
| `mutex_lock(&m)` | 臨界區可能很長、**可以睡**；呼叫者在 process context | 這個函式不會從中斷呼叫；裡面可能有 I/O 或記憶體配置 |
| `down_read` / `down_write` | 讀寫分離的 sleeping lock（rwsem） | 讀多寫少；`mm->mmap_lock` 是最有名的例子 |
| `read_seqcount_begin/retry` | 讀端不擋寫端，讀到一半被改就重讀 | 寫很少、讀很多、資料小；讀端程式碼必須能安全地重跑 |
| `rcu_read_lock()` | 讀端幾乎零成本，更新者另做一份 | [Ch 31](./31-reading-rcu-code.md) |
| `atomic_*` / `refcount_*` | 單一變數的無鎖更新 | 不保護「多個欄位之間」的一致性（[Ch 20](./20-refcount-and-lifetime.md)） |

`irqsave` 與 `irq` 的差別值得多說一句：`spin_lock_irq` 拿鎖時關中斷、放鎖時**無條件開中斷**；如果呼叫時中斷本來就是關的，放鎖時就會錯誤地把它打開。所以 `spin_lock_irq` 只能用在確定中斷開著的地方，而 `irqsave` 把原本的狀態存在 `flags` 裡，放鎖時恢復。讀碼時兩者的差異就是「作者對呼叫情境有多確定」。

## 範例一：pipe 的 mutex——為什麼不是 spinlock

上一章結束在 `anon_pipe_read` 的第一行：`mutex_lock(&pipe->mutex)`。先看這把 lock 宣告的地方：

```c
/**
 *	struct pipe_inode_info - a linux kernel pipe
 *	@mutex: mutex protecting the whole thing
 *	@rd_wait: reader wait point in case of empty pipe
 *	@wr_wait: writer wait point in case of full pipe
 *	@head: The point of buffer production
 *	@tail: The point of buffer consumption
 ...
 *	@files: number of struct file referring this pipe (protected by ->i_lock)
 ...
 **/
struct pipe_inode_info {
	struct mutex mutex;
	wait_queue_head_t rd_wait, wr_wait;
```

（`include/linux/pipe_fs_i.h:62-88（v6.18）`，節錄）

kernel-doc 註解直接寫出了保護範圍：`@mutex: mutex protecting the whole thing`，並且特別標出例外：`@files` 由 inode 的 `i_lock` 保護。這是最理想的情況——作者把答案寫在 struct 旁邊。**讀一個子系統之前，先讀它核心 struct 的註解，找「protected by」「protects」「under」這幾個字。**

為什麼是 mutex？看 `anon_pipe_read` 在 pipe 空的時候怎麼辦：

```c
		mutex_unlock(&pipe->mutex);
		/*
		 * We only get here if we didn't actually read anything.
		 *
		 * But because we didn't read anything, at this point we can
		 * just return directly with -ERESTARTSYS if we're interrupted,
		 * since we've done any required wakeups and there's no need
		 * to mark anything accessed. And we've dropped the lock.
		 */
		if (wait_event_interruptible_exclusive(pipe->rd_wait, pipe_readable(pipe)) < 0)
			return -ERESTARTSYS;

		wake_next_reader = true;
		mutex_lock(&pipe->mutex);
	}
```

（`fs/pipe.c:379-393（v6.18）`）

`wait_event_interruptible_exclusive` 會讓這個行程**睡著**直到有資料。睡之前必須放掉 mutex（否則寫端永遠拿不到 lock，死鎖），醒來再拿回來，然後迴圈回到頂端**重新檢查狀態**。這個「放鎖 → 睡 → 拿鎖 → 重新檢查」的形狀是 kernel 裡最常見的 lock 圖樣之一，[Ch 33](./33-wait-queues-completion.md) 會專門講。

但睡眠不是選 mutex 的唯一理由（放掉 spinlock 也能睡）。更關鍵的是臨界區**裡面**：pipe 讀取要把資料 `copy_to_user`，而 user 的緩衝區可能還沒對應到實體記憶體，會觸發 page fault——[Ch 28](./28-ops-tables-indirect-calls.md) 的 ftrace 輸出裡 `anon_pipe_read` 底下就真的出現了 `handle_mm_fault`。page fault 處理可能要讀磁碟、可能睡。**持有 spinlock 時不能睡**，所以這裡只能用 mutex。從 lock 的種類反推：「臨界區裡會做可能睡的事」。

## 範例二：timerfd——三把鎖、一個中斷 handler

`fs/timerfd.c` 只有 613 行，卻把 kernel 最常見的幾種 lock 用法放在一起，很適合練習。先看 struct：

```c
struct timerfd_ctx {
	union {
		struct hrtimer tmr;
		struct alarm alarm;
	} t;
	ktime_t tintv;
	ktime_t moffs;
	wait_queue_head_t wqh;
	u64 ticks;
	int clockid;
	short unsigned expired;
	short unsigned settime_flags;	/* to show in fdinfo */
	struct rcu_head rcu;
	struct list_head clist;
	spinlock_t cancel_lock;
	bool might_cancel;
};

static LIST_HEAD(cancel_list);
static DEFINE_SPINLOCK(cancel_lock);
```

（`fs/timerfd.c:30-50（v6.18）`）

這次沒有「protected by」註解。我們要自己推。看得到的 lock 有三把：`ctx->wqh.lock`（藏在 `wait_queue_head_t` 裡的 spinlock）、每個 ctx 自己的 `ctx->cancel_lock`、全域的 `cancel_lock`。

### 第一把：`ctx->wqh.lock` 為什麼要 irqsave

```c
static void timerfd_triggered(struct timerfd_ctx *ctx)
{
	unsigned long flags;

	spin_lock_irqsave(&ctx->wqh.lock, flags);
	ctx->expired = 1;
	ctx->ticks++;
	wake_up_locked_poll(&ctx->wqh, EPOLLIN);
	spin_unlock_irqrestore(&ctx->wqh.lock, flags);
}

static enum hrtimer_restart timerfd_tmrproc(struct hrtimer *htmr)
{
	struct timerfd_ctx *ctx = container_of(htmr, struct timerfd_ctx,
					       t.tmr);
	timerfd_triggered(ctx);
	return HRTIMER_NORESTART;
}
```

（`fs/timerfd.c:63-80（v6.18）`）

`timerfd_tmrproc` 是 hrtimer 的 callback。hrtimer 用 `hrtimer_setup(&ctx->t.tmr, timerfd_tmrproc, clockid, htmode)` 設定（`fs/timerfd.c:208`），mode 裡沒有 `HRTIMER_MODE_SOFT`，依 `include/linux/hrtimer.h:30-33` 的說明，callback 會在 **hard irq context** 執行（PREEMPT_RT kernel 例外，預設改到 softirq）。這是推論；我們用 ftrace 驗證。寫一個用 timerfd 的小程式：

```c
#include <sys/timerfd.h>
#include <stdio.h>
#include <stdint.h>
#include <unistd.h>
int main(void)
{
	int fd = timerfd_create(CLOCK_MONOTONIC, 0);
	struct itimerspec its = { .it_value = { 0, 10 * 1000 * 1000 } }; /* 10 ms 後到期 */
	uint64_t ticks;
	timerfd_settime(fd, 0, &its, NULL);
	read(fd, &ticks, sizeof(ticks));          /* 睡到 timer 到期 */
	printf("ticks=%llu\n", (unsigned long long)ticks);
	return 0;
}
```

靜態連結後放進 initramfs，在 QEMU 裡用 function tracer 追 `timerfd_tmrproc` 與 `timerfd_read_iter`，打開 `latency-format` 選項顯示情境旗標：

```
# T=/sys/kernel/tracing
# echo timerfd_tmrproc > $T/set_ftrace_filter; echo timerfd_read_iter >> $T/set_ftrace_filter
# echo function > $T/current_tracer; echo 1 > $T/options/latency-format
# echo 1 > $T/tracing_on; /bin/tfd; echo 0 > $T/tracing_on; cat $T/trace
ticks=1
...
#                    _------=> CPU#
#                   / _-----=> irqs-off/BH-disabled
#                  | / _----=> need-resched
#                  || / _---=> hardirq/softirq
#                  ||| / _--=> preempt-depth
#                  |||| / _-=> migrate-disable
#                  ||||| /     delay
#  cmd     pid     |||||| time  |   caller
#     \   /        ||||||  \    |    /
     tfd-59        0..... 33058us#: timerfd_read_iter <-vfs_read
  <idle>-0         0d.h1. 42895us : timerfd_tmrproc <-__hrtimer_run_queues
```

兩行的旗標欄位對照表頭讀：`timerfd_read_iter` 是 `0.....`，在 `tfd` 行程的 process context、中斷開著；`timerfd_tmrproc` 是 `0d.h1.`——`d` 表示中斷關閉、`h` 表示**在 hardirq 裡**、`1` 是 preempt depth。推論被證實了。

現在 lock 種類的選擇就說得通了：`ctx->ticks` 會在 hardirq 裡被改。如果 process context 的 `timerfd_read_iter` 用普通 `spin_lock` 拿著這把鎖時，同一顆 CPU 上 timer 中斷進來、handler 也要拿同一把鎖——handler 會永遠等下去，因為它打斷的正是持有鎖的那段程式碼。所以 process context 這一側必須在持有鎖期間**關掉本地中斷**。看讀端：

```c
	spin_lock_irq(&ctx->wqh.lock);
	if (file->f_flags & O_NONBLOCK || iocb->ki_flags & IOCB_NOWAIT)
		res = -EAGAIN;
	else
		res = wait_event_interruptible_locked_irq(ctx->wqh, ctx->ticks);
```

（`fs/timerfd.c:272-276（v6.18）`）

讀端用 `spin_lock_irq` 而不是 `irqsave`：作者確定 `read` 一定從 process context、中斷開著的狀態進來。中斷側的 `timerfd_triggered` 則用 `irqsave`，因為它可能從 hrtimer 中斷（中斷已關）或 alarm timer 路徑進來，不想假設狀態。**同一把鎖，兩側用不同的 API，正好洩漏了兩側的執行情境。**

### 第二、三把：從巢狀推出 lock 順序

```c
static void __timerfd_remove_cancel(struct timerfd_ctx *ctx)
{
	if (ctx->might_cancel) {
		ctx->might_cancel = false;
		spin_lock(&cancel_lock);
		list_del_rcu(&ctx->clist);
		spin_unlock(&cancel_lock);
	}
}

static void timerfd_remove_cancel(struct timerfd_ctx *ctx)
{
	spin_lock(&ctx->cancel_lock);
	__timerfd_remove_cancel(ctx);
	spin_unlock(&ctx->cancel_lock);
}
...
static void timerfd_setup_cancel(struct timerfd_ctx *ctx, int flags)
{
	spin_lock(&ctx->cancel_lock);
	if ((ctx->clockid == CLOCK_REALTIME ||
	     ctx->clockid == CLOCK_REALTIME_ALARM) &&
	    (flags & TFD_TIMER_ABSTIME) && (flags & TFD_TIMER_CANCEL_ON_SET)) {
		if (!ctx->might_cancel) {
			ctx->might_cancel = true;
			spin_lock(&cancel_lock);
			list_add_rcu(&ctx->clist, &cancel_list);
			spin_unlock(&cancel_lock);
		}
	} else {
		__timerfd_remove_cancel(ctx);
	}
	spin_unlock(&ctx->cancel_lock);
}
```

（`fs/timerfd.c:132-173（v6.18）`）

沒有任何註解，但結構把規則寫出來了：

1. **誰保護 `might_cancel`？** 所有**寫** `might_cancel` 的地方（`= false`、`= true`）都在 `ctx->cancel_lock` 底下（`__timerfd_remove_cancel` 的雙底線前綴暗示「呼叫者已持有鎖」，而它唯二的呼叫者都先拿了 `ctx->cancel_lock`）。所以 `ctx->cancel_lock` 保護的是「這個 ctx 是否在 cancel list 上」這個狀態。
2. **誰保護 `cancel_list`？** 所有 `list_add_rcu`／`list_del_rcu` 都在全域 `cancel_lock` 底下。讀端（`timerfd_clock_was_set`）則用 `rcu_read_lock()` + `list_for_each_entry_rcu`，**不拿** `cancel_lock`——這是 [Ch 31](./31-reading-rcu-code.md) 的標準模式：寫者之間用鎖互斥，讀者用 RCU。
3. **lock 順序**：`ctx->cancel_lock` 總是在外層、`cancel_lock` 總是在內層。只要整個 kernel 沒有任何地方反過來拿，就不會死鎖。這條規則沒寫在註解裡，是你從巢狀結構**推出來**的。

這就是「從程式碼推出 lock 保護哪些欄位」的實際做法：**列出一個欄位所有的寫入點，看它們共同持有哪把鎖**。用 `rg -n 'might_cancel' fs/timerfd.c` 一次列出 7 處，逐一檢查即可。讀取點有時可以不拿鎖（例如 `timerfd_clock_was_set` 在 RCU 底下讀 `might_cancel`，`timerfd_canceled` 在 `wqh.lock` 底下讀），這時要問「讀到舊值會怎樣」——通常作者已經容忍了它，或有其他機制兜底。

## 範例三：seqcount——讀端不拿鎖

```c
u64 get_jiffies_64(void)
{
	unsigned int seq;
	u64 ret;

	do {
		seq = read_seqcount_begin(&jiffies_seq);
		ret = jiffies_64;
	} while (read_seqcount_retry(&jiffies_seq, seq));
	return ret;
}
```

（`kernel/time/jiffies.c:48-58（v6.18）`）

在 32-bit 機器上，讀 64-bit 的 `jiffies_64` 要兩條指令，可能讀到「低 32 bit 是新的、高 32 bit 是舊的」的撕裂值。seqcount 的讀法：先記下序號、讀資料、再看序號有沒有變（或是奇數，表示寫者正在寫）；變了就重讀。寫端在 `kernel/time/tick-common.c:89-96`：

```c
		raw_spin_lock(&jiffies_lock);
		write_seqcount_begin(&jiffies_seq);
		...
		do_timer(1);
		write_seqcount_end(&jiffies_seq);
```

讀 seqcount 程式碼的要點：**`do { } while (read_seqcount_retry(...))` 迴圈裡的程式碼可能跑很多次，而且可能讀到不一致的中間值**。所以迴圈內只能做「讀、存到區域變數」，不能根據讀到的值去解參考指標或做有副作用的事。看到迴圈裡有 `p->next->field` 這種東西，就要提高警覺。寫者之間仍需要互斥（這裡是 `jiffies_lock`），`seqcount_raw_spinlock_t` 這個型別名稱本身就在告訴你「寫者用哪把鎖」（`kernel/time/jiffies.c:43-45` 的宣告把兩者綁在一起）。

## 推出「這把 lock 保護什麼」的五個線索

| 線索 | 範例 | 可靠度 |
|---|---|---|
| struct 旁的註解 | `@mutex: mutex protecting the whole thing`；`fs/inode.c` 開頭的 "inode->i_lock protects: ..." | 高，但可能過時 |
| `lockdep_assert_held(&l)` | `file_close_fd_locked()` 開頭 | 很高：開 lockdep 時執行期驗證 |
| kernel-doc 的 `Context:` 與 `_locked` 命名 | `Context: files_lock must be held.` | 中高 |
| 靜態標註 `__must_hold`／`__acquires`／`__releases` | `expand_fdtable` | 中：sparse 檢查，但只檢查平衡 |
| 統計所有寫入點 | timerfd 的 `might_cancel` | 視你的耐心而定，最根本 |

### 線索：struct 註解與檔案開頭的總表

有些檔案把規則集中寫在開頭。`fs/inode.c`：

```c
/*
 * Inode locking rules:
 *
 * inode->i_lock protects:
 *   inode->i_state, inode->i_hash, __iget(), inode->i_io_list
 * Inode LRU list locks protect:
 *   inode->i_sb->s_inode_lru, inode->i_lru
 * inode->i_sb->s_inode_list_lock protects:
 *   inode->i_sb->s_inodes, inode->i_sb_list
 * bdi->wb.list_lock protects:
 *   bdi->wb.b_{dirty,io,more_io,dirty_time}, inode->i_io_list
 * inode_hash_lock protects:
 *   inode_hashtable, inode->i_hash
 *
 * Lock ordering:
 *
 * inode->i_sb->s_inode_list_lock
 *   inode->i_lock
 *     Inode LRU list locks
 *
 * bdi->wb.list_lock
 *   inode->i_lock
 *
 * inode_hash_lock
 *   inode->i_sb->s_inode_list_lock
 *   inode->i_lock
 *
 * iunique_lock
 *   inode_hash_lock
 */
```

（`fs/inode.c:31-60（v6.18）`）

前半是「誰保護誰」，後半是順序（縮排越深越內層）。注意 `inode->i_io_list` 同時出現在 `i_lock` 與 `bdi->wb.list_lock` 底下：改它要**兩把都拿**，讀它拿其中一把就夠。這種「一個欄位被兩把鎖共同保護」的設計在 kernel 很常見，註解是唯一能讓你一眼看出來的地方。

### 線索：`lockdep_assert_held`——會執行的註解

```c
 * Context: files_lock must be held.
 *
 * Returns: The file associated with @fd (NULL if @fd is not open)
 */
struct file *file_close_fd_locked(struct files_struct *files, unsigned fd)
{
	struct fdtable *fdt = files_fdtable(files);
	struct file *file;

	lockdep_assert_held(&files->file_lock);
```

（`fs/file.c:688-697（v6.18）`）

同一件事寫了三次：kernel-doc 的 `Context:`、函式名的 `_locked` 後綴、以及 `lockdep_assert_held`。前兩者是給人看的，第三個是給機器檢查的：開了 `CONFIG_LOCKDEP` 的 kernel 在執行到這行時，如果目前的 task 沒持有 `file_lock` 就會印出警告。沒開時它編譯成空的。v6.18 tree 裡有 3070 處 `lockdep_assert_held(`，是最可靠的「這個函式要求呼叫者持有哪把鎖」資料來源。`_locked(` 後綴的函式呼叫則有近六千處。

讀碼技巧：想知道一把鎖保護什麼，`rg 'lockdep_assert_held\(&\w+->file_lock\)'` 列出所有宣告「我需要這把鎖」的函式，再看這些函式碰了哪些欄位。

### 線索：sparse 的 context 標註

```c
static int expand_fdtable(struct files_struct *files, unsigned int nr)
	__releases(files->file_lock)
	__acquires(files->file_lock)
{
	struct fdtable *new_fdt, *cur_fdt;

	spin_unlock(&files->file_lock);
	new_fdt = alloc_fdtable(nr + 1);
	...
	spin_lock(&files->file_lock);
```

（`fs/file.c:252-267（v6.18）`）

`__releases` + `__acquires` 一起出現，翻成人話是「呼叫時持有鎖、返回時也持有，但**中間會放掉**」。這是很重要的警告：呼叫 `expand_fdtable` 之後，呼叫者在之前讀到的任何受 `file_lock` 保護的值都可能過期了，要重讀。為什麼要放掉？因為 `alloc_fdtable` 可能睡（配置大塊記憶體），持有 spinlock 不能睡。`__must_hold(x)` 則表示「進出都持有、中間不放」，例如 `fs/jbd2/transaction.c:210` 的 `add_transaction_credits`（它的註解還坦白說：因為有時會放鎖，所以用 `__must_hold` 來「騙過 sparse」，見 `fs/jbd2/transaction.c:203-207`）。

這些標註只在 sparse（`make C=1`）下有作用，一般編譯時展開成空的（`include/linux/compiler_types.h:38-41` 與 `66-69`）。sparse 只檢查進出時的鎖數量是否平衡，不檢查「存取某欄位時有沒有持有鎖」。詳見 [Ch 18](./18-annotations-and-sparse.md)。

### 線索的未來：`__guarded_by`（v6.18 還沒有）

最直接的線索是把「這個欄位由哪把鎖保護」寫進型別系統。v6.18 tree 裡搜 `__guarded_by` 是 0 筆。它在 v7.0 隨 Marco Elver 的 Clang「Context Analysis」基礎建設加入（commit `3269701cb256`，2025-12）：

```
$ git tag --contains 3269701cb256 | sort -V | head -1
v7.0-rc1
$ git show v7.0:include/linux/compiler-context-analysis.h | sed -n 42,56p
 * __guarded_by - struct member and globals attribute, declares variable
 *                only accessible within active context
 *
 * Declares that the struct member or global variable is only accessible within
 * the context entered by the given context lock. Read operations on the data
 * require shared access, while write operations require exclusive access.
 *
 * .. code-block:: c
 *
 *	struct some_state {
 *		spinlock_t lock;
 *		long counter __guarded_by(&lock);
 *	};
 */
# define __guarded_by(...)		__attribute__((guarded_by(__VA_ARGS__)))
```

開了 `CONFIG_WARN_CONTEXT_ANALYSIS` 並用夠新的 Clang 編譯時，存取 `counter` 卻沒持有 `lock` 會在**編譯期**被警告。依 v7.0 的 `Documentation/dev-tools/context-analysis.rst`，它是逐個子系統 opt-in 的。如果你讀的是 v7.0 以後的 tree，看到 `__guarded_by` 就等於拿到了作者親筆的答案；讀 v6.18 時，只能用前面四種線索拼湊。

## lock 順序：去檔案開頭找那張圖

死鎖最常見的成因是 ABBA：一條路徑先拿 A 再拿 B，另一條先拿 B 再拿 A。kernel 的對策是**規定全域順序**，並把它寫在最相關的檔案開頭。`mm/rmap.c` 那張大概是全 kernel 最有名的：

```c
/*
 * Lock ordering in mm:
 *
 * inode->i_rwsem	(while writing or truncating, not reading or faulting)
 *   mm->mmap_lock
 *     mapping->invalidate_lock (in filemap_fault)
 *       folio_lock
 *         hugetlbfs_i_mmap_rwsem_key (in huge_pmd_share, see hugetlbfs below)
 *           vma_start_write
 *             mapping->i_mmap_rwsem
 *               anon_vma->rwsem
 *                 mm->page_table_lock or pte_lock
 *                   swap_lock (in swap_duplicate, swap_info_get)
 *                     mmlist_lock (in mmput, drain_mmlist and others)
 *                     mapping->private_lock (in block_dirty_folio)
 *                         i_pages lock (widely used)
 *                           lruvec->lru_lock (in folio_lruvec_lock_irq)
 *                     inode->i_lock (in set_page_dirty's __mark_inode_dirty)
 *                     bdi.wb->list_lock (in set_page_dirty's __mark_inode_dirty)
 *                       sb_lock (within inode_lock in fs/fs-writeback.c)
 *                       i_pages lock (widely used, in set_page_dirty,
 *                                 in arch-dependent flush_dcache_mmap_lock,
 *                                 within bdi.wb->list_lock in __sync_single_inode)
 ...
 */
```

（`mm/rmap.c:20-41（v6.18）`）

怎麼用這張圖讀碼：

1. **定位**：你讀的函式持有哪幾把鎖？在圖上找到它們的位置。
2. **預期**：這個函式之後只能再拿**更深層**的鎖。看到它去拿上層的鎖，要嘛有特殊的 trylock 處理，要嘛它先放掉了內層鎖。
3. **解讀怪碼**：看到 `folio_trylock()` 失敗就整個退出重來，或是「先放掉 pte lock、拿 folio lock、再重拿 pte lock、重新檢查」，通常就是在**繞過**這張圖的順序。沒有這張圖，這種程式碼看起來毫無道理。

括號裡的註記（"in filemap_fault"、"widely used"）告訴你這條巢狀關係出現在哪裡，可以順著去讀。這張圖本身也有歷史：`git log -L 20,52:mm/rmap.c` 可以看到每一次調整（[Ch 37](./37-git-archaeology.md)）。

### 失敗示範：ABBA 在 user space 長什麼樣

```c
/* abba.c — 兩個 thread 用相反順序拿兩把鎖；用 timedlock 偵測卡死 */
#include <errno.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <sched.h>

static pthread_mutex_t A = PTHREAD_MUTEX_INITIALIZER, B = PTHREAD_MUTEX_INITIALIZER;
static int reverse;                        /* 1 = thread 2 用 B→A 的順序 */

static int lock_timeout(pthread_mutex_t *m)
{
	struct timespec ts;
	clock_gettime(CLOCK_REALTIME, &ts);
	ts.tv_sec += 1;
	return pthread_mutex_timedlock(m, &ts);
}

static void *worker(void *arg)
{
	int id = (int)(long)arg;
	pthread_mutex_t *first = &A, *second = &B;
	if (id == 2 && reverse) { first = &B; second = &A; }
	for (int i = 0; i < 20000; i++) {
		pthread_mutex_lock(first);
		sched_yield();
		if (lock_timeout(second) == ETIMEDOUT) {
			printf("thread %d: stuck at iter %d (holding %s, waiting %s)\n",
			       id, i, first == &A ? "A" : "B", second == &A ? "A" : "B");
			pthread_mutex_unlock(first);
			return NULL;
		}
		pthread_mutex_unlock(second);
		pthread_mutex_unlock(first);
	}
	printf("thread %d: done\n", id);
	return NULL;
}

int main(int argc, char **argv)
{
	reverse = argc > 1 && strcmp(argv[1], "abba") == 0;
	pthread_t t1, t2;
	pthread_create(&t1, NULL, worker, (void *)1L);
	pthread_create(&t2, NULL, worker, (void *)2L);
	pthread_join(t1, NULL);
	pthread_join(t2, NULL);
	return 0;
}
```

```
$ gcc -O2 -Wall -pthread -o abba abba.c
$ ./abba
thread 2: done
thread 1: done
$ ./abba abba
thread 1: stuck at iter 0 (holding A, waiting B)
thread 2: done
$ ./abba abba
thread 2: stuck at iter 0 (holding B, waiting A)
thread 1: done
$ ./abba abba
thread 1: stuck at iter 0 (holding A, waiting B)
thread 2: stuck at iter 215 (holding B, waiting A)
```

同順序跑兩萬次都沒事；反順序幾乎立刻卡住，而且每次卡在哪一個、第幾圈都不同（第三次兩個都逾時，是因為 thread 1 放棄並放鎖之後，兩者又在後面某一圈撞上）。這說明了兩件事：死鎖是**時序相依**的，測試跑過不代表沒有；以及為什麼 kernel 需要 lockdep——它不等死鎖真的發生，而是記錄每次「持有 X 時拿 Y」的順序，一旦看到某處「持有 Y 時拿 X」就報警，即使這兩條路徑從來沒有真的同時跑過。

> lockdep 的報告（splat）長什麼樣子：本課的 build 沒開 `CONFIG_PROVE_LOCKING`，所以**本段未實測**。在開了它的 kernel 上，ABBA 會產生一段以 "WARNING: possible circular locking dependency detected" 開頭的訊息，列出兩條鎖鏈與各自的 stack。怎麼讀這份報告，`Documentation/locking/lockdep-design.rst` 有完整說明。

## 新寫法：`guard(mutex)` 與 `scoped_guard`

v6.5 起（commit `54da6a092431`，Peter Zijlstra，"locking: Introduce __cleanup() based infrastructure"），kernel 有了作用域自動解鎖的寫法：

```c
static int kthreads_online_cpu(unsigned int cpu)
{
	cpumask_var_t affinity;
	struct kthread *k;
	int ret;

	guard(mutex)(&kthreads_hotplug_lock);

	if (list_empty(&kthreads_hotplug))
		return 0;

	if (!zalloc_cpumask_var(&affinity, GFP_KERNEL))
		return -ENOMEM;
	...
	free_cpumask_var(affinity);

	return ret;
}
```

（`kernel/kthread.c:906-933（v6.18）`，節錄）

函式裡沒有任何 `mutex_unlock`，卻有三個 `return`。`guard(mutex)(&lock)` 拿鎖之後，在**目前這個大括號作用域結束時**自動放鎖，不管是從哪個 `return` 離開。它的定義只有一行：

```c
DEFINE_GUARD(mutex, struct mutex *, mutex_lock(_T), mutex_unlock(_T))
```

（`include/linux/mutex.h:228（v6.18）`）

底下是 GCC/Clang 的 `__attribute__((cleanup(fn)))`：變數離開作用域時自動呼叫 `fn`。[Ch 17](./17-kernel-error-conventions.md) 介紹過它在錯誤處理上的用法。我們在 user space 重現一個最小版本：

```c
/* guard.c — 用 __attribute__((cleanup)) 在 user space 重現 kernel 的 guard(mutex) */
#include <pthread.h>
#include <stdio.h>

static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static int balance = 100;                  /* 由 lock 保護 */

static void unlock_cleanup(pthread_mutex_t **m)
{
	printf("  [cleanup] unlock\n");
	pthread_mutex_unlock(*m);
}
#define guard_mutex(m) \
	pthread_mutex_t *__guard __attribute__((cleanup(unlock_cleanup))) = \
		(pthread_mutex_lock(m), (m))

static int withdraw(int amount)
{
	guard_mutex(&lock);                    /* 從這行到 } 都持有 lock */
	if (amount > balance) {
		printf("  insufficient\n");
		return -1;                         /* 提早 return：仍會 unlock */
	}
	balance -= amount;
	printf("  ok, balance=%d\n", balance);
	return 0;
}

int main(void)
{
	printf("withdraw(30):\n");  withdraw(30);
	printf("withdraw(500):\n"); withdraw(500);
	/* 若上面忘了 unlock，這裡的 trylock 會失敗 */
	printf("trylock after: %s\n", pthread_mutex_trylock(&lock) == 0 ? "free" : "STILL HELD");
	return 0;
}
```

```
$ gcc -O2 -Wall -pthread -o guard guard.c && ./guard
withdraw(30):
  ok, balance=70
  [cleanup] unlock
withdraw(500):
  insufficient
  [cleanup] unlock
trylock after: free
```

注意 `[cleanup] unlock` 印在 `ok, balance=70` **之後**：unlock 發生在函式真正離開時，也就是 `return` 的值已經算好之後。

### 讀 guard 程式碼的三個重點

1. **臨界區的範圍是「到這個大括號結束」，不是「到下一個 unlock」**。讀舊式程式碼時，你找 `unlock` 來確定臨界區的終點；讀 guard 程式碼，你要找**包住 guard 的那對大括號**。在 `kthreads_online_cpu` 裡，整個函式後半（包括 `zalloc_cpumask_var(..., GFP_KERNEL)` 這個可能睡的配置）都在鎖裡——對 mutex 這沒問題，但如果是 `guard(spinlock)`，臨界區裡出現可能睡的呼叫就是 bug。
2. **`scoped_guard(mutex, &lock) { ... }`** 把範圍明確限制在後面的區塊。看到它，臨界區就是那個區塊。
3. **混用時要更小心**。同一個函式裡有 guard 也有手動 `mutex_unlock`，或在迴圈裡用 guard（每一圈結束都會放鎖），都是讀碼時容易看錯的地方。v6.18 tree 裡 `guard(mutex)` 出現 2377 次、`scoped_guard(` 1449 次，新程式碼越來越多用這種寫法，兩種風格你都要會讀。

## 對比與取捨

| lock | 可以睡嗎 | 誰能用 | 讀端成本 | 讀碼時的推論 |
|---|---|---|---|---|
| spinlock | 持有時不行 | 任何情境 | 一次原子操作 | 臨界區短；看有沒有 irq/bh 變體判斷誰會搶 |
| mutex | 可以 | 只有 process context | 一次原子操作（無競爭時） | 臨界區裡可能有 I/O、配置、copy_*_user |
| rwsem | 可以 | process context | 讀者之間不互斥 | 讀多寫少，臨界區可能很長 |
| seqcount/seqlock | 讀端不拿鎖 | 讀端任何情境 | 兩次讀序號，可能重試 | 資料小、寫很少、讀端可重跑 |
| RCU | 讀端不行（一般 RCU） | 任何情境 | 接近零 | 讀多寫極少，舊資料可以多活一陣子（Ch 31） |
| atomic/refcount | — | 任何情境 | 一次原子操作 | 只保護單一變數，不保護欄位之間的關係 |

## 踩雷集錦

1. **「lock 保護的是程式碼」**：lock 保護的是**資料**。同一個欄位在三個函式裡被改，三個地方都要拿同一把鎖；只在其中一個函式加鎖沒有意義。讀碼時的問題永遠是「這把鎖保護哪些欄位」，而不是「這把鎖保護哪幾行」。
2. **「`spin_lock_irqsave` 是比較安全的 `spin_lock`，多用無害」**：它的訊息是「這份資料會被 hardirq 碰」。讀到它卻找不到任何中斷側的使用者，要嘛你漏找了（timer callback、IRQ handler、`wake_up` 從中斷呼叫），要嘛作者過度防禦。不要反過來從它推論出不存在的中斷路徑，要去找證據——本章用 ftrace 的 `h` 旗標證實了 timerfd 的中斷路徑。
3. **「放掉 lock 再拿回來，前面讀到的值仍然有效」**：中間世界可能全變了。`anon_pipe_read` 睡醒後回到迴圈頂端重讀 `head`/`tail`，`expand_files` 在 `wait_event` 之後 `goto repeat` 重新檢查，都是這個原因。看到 `__releases` + `__acquires` 的函式，呼叫之後的程式碼必須重新驗證狀態。
4. **「沒拿鎖就讀，一定是 bug」**：kernel 裡有大量刻意的 lockless 讀取：`pipe_readable(pipe)` 在 `wait_event` 條件裡不拿 mutex、timerfd 在 RCU 底下讀 `might_cancel`。判斷方法是問「讀到過期的值會怎樣」——如果後面拿鎖後會重新檢查，或過期值只會造成一次多餘的喚醒，作者通常是刻意的。這類讀取應該配 `READ_ONCE`（[Ch 21](./21-read-once-and-barriers.md)），沒配的話才值得懷疑。
5. **「guard 的臨界區到下一行空行就結束了」**：到包住它的大括號結束。函式開頭的 `guard(mutex)` 代表整個函式都在鎖裡。
6. **「lockdep 沒報錯就代表沒有死鎖風險」**：lockdep 只能檢查**實際執行過**的鎖順序。沒跑到的路徑、或用 `lockdep_off()`／不同 lock class 繞過的地方，它看不見。

## 進階：再往深一層

- **PREEMPT_RT 改變了 lock 的語意**：在 RT kernel 上，`spinlock_t` 變成會睡的 rtmutex，只有 `raw_spinlock_t` 還是真正的自旋鎖；hrtimer callback 預設也改在 softirq 執行。所以 `jiffies_lock` 用 `DEFINE_RAW_SPINLOCK` 是有意的：它必須在 RT 下仍然不能睡。`Documentation/locking/locktypes.rst` 有完整對照。
- **lock class 與 `_nested` 變體**：同一種 struct 的兩個實例的鎖（例如兩個 inode 的 `i_rwsem`）在 lockdep 眼中是同一個 class，同時拿兩個會被誤判為遞迴。kernel 用 `mutex_lock_nested(&lock, SINGLE_DEPTH_NESTING)` 或 `lock_two_nondirectories()` 這類 helper 告訴 lockdep「這是有順序的兩個實例」。讀到 `_nested` 時，旁邊通常有「依位址或 inode 號碼排序」的規則。
- **`might_sleep()`**：函式開頭的 `might_sleep()` 是另一種會執行的註解：開 `CONFIG_DEBUG_ATOMIC_SLEEP` 時，如果在持有 spinlock 或關中斷時呼叫，就印警告。它告訴你「這個函式不能從 atomic context 呼叫」。
- **per-CPU 資料與 `local_lock`**：只在本 CPU 存取的資料，用關搶占或 `local_lock_t` 代替真正的鎖（[Ch 27](./27-bitmap-percpu-kfifo.md)）。
- **面試題**：「spinlock 和 mutex 怎麼選？」初階答案是「短用 spin、長用 mutex」；有深度的答案要講到中斷情境、能不能睡、`irqsave` 的意義、PREEMPT_RT 的差異——本章都有了。

## 動手練習

1. 在 `fs/eventfd.c` 找出 `struct eventfd_ctx` 的鎖，判斷它是哪種鎖、為什麼。（提示：看 `eventfd_signal_mask` 可能從什麼情境被呼叫，它的註解有說明。）這也是 final project 的一部分。
2. 對 `struct files_struct`（`include/linux/fdtable.h:38-57`）做「統計寫入點」：`rg -n 'next_fd' fs/file.c`，確認所有寫入 `next_fd` 的地方都持有 `file_lock`。有沒有例外？例外的理由是什麼？
3. 打開 `fs/dcache.c` 開頭，找出它的 lock 規則註解，畫成一張樹狀圖，和 `fs/inode.c` 的圖拼在一起，看 `inode->i_lock` 在兩張圖裡的位置是否一致。
4. 把 `guard.c` 改成在 `withdraw` 裡用兩層大括號，內層用 guard、外層做 `printf`，觀察 unlock 的時機，驗證「範圍到大括號結束」。

## 本章重點整理

- lock 的**種類**是執行情境的宣告：`irqsave`/`irq` 表示 hardirq 會碰這份資料，`_bh` 表示 softirq，mutex 表示可以睡、只在 process context。
- 推出「保護哪些欄位」的線索：struct 與檔案開頭的註解、`lockdep_assert_held`、`Context:` 與 `_locked` 命名、`__must_hold`/`__acquires`/`__releases`、以及最根本的「列出所有寫入點看共同持有哪把鎖」。`__guarded_by` 要到 v7.0 才有。
- lock 順序寫在檔案開頭（`mm/rmap.c`、`fs/inode.c`），看到 trylock、放鎖重拿、`goto retry`，通常是在繞過順序規則。
- `guard(mutex)` 的臨界區到大括號結束；放鎖再拿回來之後，之前讀到的值都要重新驗證。

## 自我檢核

- [ ] 看到一個函式用 `spin_lock_irq`、另一個用 `spin_lock_irqsave` 拿同一把鎖，你能推論出兩個函式各自的執行情境嗎？
- [ ] 不看筆記，說出三種在 kernel 程式碼裡找出「這把鎖保護哪些欄位」的方法，以及各自的可靠度。
- [ ] 有人在 code review 說「這裡讀 `x->flag` 沒拿鎖，是 bug」，你會先問哪兩個問題再下結論？
- [ ] 能解釋為什麼 `anon_pipe_read` 用 mutex 而 timerfd 的 `ticks` 用 spinlock，並各給一個程式碼裡的證據嗎？
- [ ] 給你一個用 `guard(mutex)` 的函式，你能準確指出臨界區的起點與終點，並判斷裡面有沒有不該出現的呼叫嗎？

## 延伸閱讀

### 官方文件

- **Documentation/kernel-hacking/locking.rst — "Unreliable Guide To Locking"**（[docs.kernel.org/kernel-hacking/locking.html](https://docs.kernel.org/kernel-hacking/locking.html)）
  - **讀哪裡**：「Locking in the Linux Kernel」的情境對照表（user context、softirq、hardirq 之間該用哪種 lock），以及「Common Examples」。
  - **能學到什麼**：本章「每種 lock 告訴你什麼」那張表的權威版本，從「設計者該怎麼選」的角度講，反過來就是讀碼者的推論規則。作者 Rusty Russell 的文風很直接，值得整篇讀。
  - **前提**：知道 hardirq、softirq、process context 的差別（[Ch 32](./32-async-irq-softirq-workqueue.md)）。
- **Documentation/locking/locktypes.rst**（[docs.kernel.org/locking/locktypes.html](https://docs.kernel.org/locking/locktypes.html)）
  - **讀哪裡**：「Lock categories」與「PREEMPT_RT caveats」。
  - **能學到什麼**：sleeping lock、CPU local lock、spinning lock 三大類的正式分類，以及 RT kernel 下 `spinlock_t` 與 `raw_spinlock_t` 的差異。
- **Documentation/locking/lockdep-design.rst**（[docs.kernel.org/locking/lockdep-design.html](https://docs.kernel.org/locking/lockdep-design.html)）
  - **讀哪裡**：「Lock-class」、「State」與「Rules」三節，以及報告格式的範例。
  - **能學到什麼**：lockdep 如何從「實際觀察到的順序」推論出潛在死鎖；讀完能看懂 lockdep splat 的每一行。
- **Documentation/core-api/cleanup.rst**（[docs.kernel.org/core-api/cleanup.html](https://docs.kernel.org/core-api/cleanup.html)）
  - **讀哪裡**：整篇，特別是關於 `guard()` 與 `scoped_guard()` 的使用規範與「不要混用」的建議。
  - **能學到什麼**：作用域資源管理的官方風格指引，讀 v6.5 之後的新程式碼必備。

### 技術文章

- **Scope-based resource management for the kernel** — Jonathan Corbet（LWN，2023）
  - **這篇說什麼**：Peter Zijlstra 的 cleanup 基礎建設 patch 引起的討論、設計取捨，以及 Linus 對這種寫法的看法。
  - **為什麼值得讀**：補上「為什麼 kernel 到 2023 年才接受這種寫法」的脈絡鴻溝。（URL 未逐一核對，請在 lwn.net 以標題搜尋。）

### 書籍

- **《Is Parallel Programming Hard, And, If So, What Can You Do About It?》** — Paul E. McKenney（免費線上書，[kernel.org/pub/linux/kernel/people/paulmck/perfbook/perfbook.html](https://kernel.org/pub/linux/kernel/people/paulmck/perfbook/perfbook.html)）
  - **讀哪幾章**：「Locking」一章（死鎖、lock 階層、lock-based 的效能陷阱），以及「Deferred Processing」一章的 sequence lock 小節。
  - **定位**：作者是 RCU 的主要維護者；這本書把 kernel 的並行原語放回一般並行程式設計的脈絡裡，讀完 Ch 30–31 後讀它，會知道每種原語為什麼存在。持續更新中，以最新版為準。

lock 讓寫者與讀者互相等待；但 timerfd 的 cancel list、fd table、`inet_protos[]` 的讀者都沒有拿鎖，它們靠的是另一種機制。

→ [Ch 31 讀 RCU 程式碼：reader、updater 與 grace period](./31-reading-rcu-code.md)
