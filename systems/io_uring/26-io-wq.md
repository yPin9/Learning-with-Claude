# Ch 26 — async 的祕密：io-wq worker pool

> **目標**：搞懂當一個 io_uring 操作「真的會阻塞」時，kernel 怎麼把它丟給一群背景 worker thread 去跑。你會看懂 `io_wq` / `io_worker` / `io_wq_acct` 的結構、bound vs unbound worker 的分野、worker 怎麼被動態建立與回收、work 怎麼排進佇列（`io_wq_enqueue`）、以及 io-wq 和 task_work 的分工——為什麼有些事非得開一個獨立 thread 不可。

> **環境**：kernel 6.18 原始碼（`v6.18`，commit `7d0a66e`）。對照 `io_uring/io-wq.c`、`io_uring/io_uring.c`。

## 為什麼需要一群獨立的 worker

Ch 24 講過 io_uring 的提交路徑預設帶 `IO_URING_F_NONBLOCK`：operation handler 先用「非阻塞」的方式試跑，能立刻完成最好。但很多 I/O **沒辦法**非阻塞完成：

- 讀一個沒有 page cache 的檔案，要等磁碟。
- `IORING_OP_STATX`、`openat`、`fsync` 這類 VFS 操作，底層可能睡在 inode 鎖、journal、網路檔案系統上。
- 一個 regular file 的 buffered read 打到 cache miss。

這些操作在 kernel 裡**會呼叫 `schedule()` 睡著**。問題是：提交請求的那個 context 是誰？是 user 程式在 `io_uring_enter` syscall 裡、或是 task_work 在跑。**你不能讓 user 的 submission 卡在那裡睡**——那整個 io_uring「提交不阻塞」的承諾就破了，而且會把同一批其他請求一起拖住。

poll 機制（Ch 28）能處理「可以 poll 的 fd」（socket、pipe），讓請求掛在 waitqueue 上、就緒再跑,不佔 thread。但 regular file 不支援 poll（永遠 readable/writable），檔案系統的 metadata 操作也沒有 poll 介面。對這些「只能同步阻塞」的操作，io_uring 只剩一招：**開一個獨立的 kernel thread 去替你睡**。那個 thread 睡著不影響任何人,它醒來把結果填好。這群 thread 就是 io-wq（io work-queue）。

## 先建立直覺：per-task 的 worker pool

```
     user task (tctx)
         │ 提交一個會阻塞的 op（如 buffered read on regular file）
         ▼
   io_queue_iowq(req)  ──►  io_wq_enqueue(tctx->io_wq, &req->work)
         │
         ▼
   ┌────────────────── struct io_wq （屬於這個 task）──────────────────┐
   │                                                                   │
   │   acct[BOUND]                        acct[UNBOUND]                │
   │   ├ work_list ──[work][work]         ├ work_list ──[work]        │
   │   ├ nr_workers / max_workers         ├ nr_workers / max_workers  │
   │   ├ free_list（閒置 worker）          ├ free_list                 │
   │   └ nr_running                       └ nr_running                │
   │        │                                   │                     │
   │        ▼                                   ▼                     │
   │   [iou-wrk thread] 睡在磁碟 I/O       [iou-wrk thread] 睡在 recv  │
   └───────────────────────────────────────────────────────────────┘
         │ worker 跑完
         ▼
   io_wq_submit_work ──► io_issue_sqe（這次是阻塞模式）──► 完成路徑（Ch 25 路徑 B）
```

幾個直覺要先建立：

- **io-wq 是 per-task 的**，不是全系統一個。每個用 io_uring 的 task 的 `io_uring_task`（tctx）底下掛一個 `tctx->io_wq`。
- 每個 `io_wq` 內部分**兩組**帳本（`acct`）：**bound**（給 regular file I/O）和 **unbound**（給 socket/pipe 等）。兩組各有自己的 work 佇列、worker 計數、上限。
- worker thread 的名字叫 `iou-wrk-<pid>`——你 `ps` 看得到。它們**動態建立、閒置逾時自動回收**，不是固定一池。

## 結構一：`io_wq`、`io_worker`、`io_wq_acct`

先看三個核心結構（`io_uring/io-wq.c`）。注意這裡要誠實標一個版本差異：**6.18 沒有 `io_wqe` 這個結構**。早期（5.x）io-wq 是 per-NUMA-node 設計，有 `io_wqe`（wq entry per node）；後來被簡化掉了,現在 `io_wq` 直接內嵌一個 `acct[2]` 陣列，per-node 的概念不復存在。所以如果你看舊 writeup 提到 `io_wqe`,對照 6.18 要知道它已經合併進 `io_wq` 了。

`io_worker`——一個 worker thread：

```c
struct io_worker {
	refcount_t ref;
	unsigned long flags;
	struct hlist_nulls_node nulls_node;
	struct list_head all_list;
	struct task_struct *task;
	struct io_wq *wq;
	struct io_wq_acct *acct;

	struct io_wq_work *cur_work;
	raw_spinlock_t lock;

	struct completion ref_done;

	unsigned long create_state;
	struct callback_head create_work;
	int init_retries;

	union {
		struct rcu_head rcu;
		struct delayed_work work;
	};
};
```

`task` 是這個 worker 背後真正的 kernel thread；`cur_work` 是它正在跑的 work；`acct` 指回它屬於哪一組帳本（bound 還是 unbound）。

`io_wq_acct`——一組帳本（一個 `io_wq` 有兩個）：

```c
struct io_wq_acct {
	raw_spinlock_t workers_lock;

	unsigned nr_workers;
	unsigned max_workers;
	atomic_t nr_running;

	struct hlist_nulls_head free_list;
	struct list_head all_list;

	raw_spinlock_t lock;
	struct io_wq_work_list work_list;
	unsigned long flags;
};

enum {
	IO_WQ_ACCT_BOUND,
	IO_WQ_ACCT_UNBOUND,
	IO_WQ_ACCT_NR,
};
```

欄位對應上面那張圖：`work_list` 是待處理 work 的佇列、`nr_workers`/`max_workers` 是這組有幾個/最多幾個 worker、`nr_running` 是此刻有幾個在實際跑（不是睡著等）的、`free_list` 是閒置可喚醒的 worker、`all_list` 是全部 worker。

`io_wq`——整個 pool：

```c
struct io_wq {
	unsigned long state;
	struct io_wq_hash *hash;
	atomic_t worker_refs;
	struct completion worker_done;
	struct hlist_node cpuhp_node;
	struct task_struct *task;
	struct io_wq_acct acct[IO_WQ_ACCT_NR];
	struct wait_queue_entry wait;
	struct io_wq_work *hash_tail[IO_WQ_NR_HASH_BUCKETS];
	cpumask_var_t cpu_mask;
};
```

`acct[IO_WQ_ACCT_NR]` 就是那兩組帳本。`hash` / `hash_tail` 是給「同一個 inode 的寫入不能並行」用的——下面會提。

## bound vs unbound：為什麼要分兩組

分組的函式一看就懂（`io_uring/io-wq.c`）：

```c
static inline struct io_wq_acct *io_get_acct(struct io_wq *wq, bool bound)
{
	return &wq->acct[bound ? IO_WQ_ACCT_BOUND : IO_WQ_ACCT_UNBOUND];
}

static inline struct io_wq_acct *io_work_get_acct(struct io_wq *wq,
						  unsigned int work_flags)
{
	return io_get_acct(wq, !(work_flags & IO_WQ_WORK_UNBOUND));
}
```

`IO_WQ_WORK_UNBOUND` 這個 flag 在請求準備 async 時設上（`io_uring/io_uring.c` 的 `io_prep_async_work`）。分野是：

- **bound work**：regular file 的 I/O。它的阻塞時間有「界」（bounded）——磁碟再慢也會回來。這組 worker 數量上限跟 CPU 數掛鉤（setup 時 `bounded` 參數,預設 `min(ctx->sq_entries, 4 * num_online_cpus)` 之類）。
- **unbound work**：socket、pipe 這種的操作。阻塞時間**無界**（unbounded）——一個 `recv` 可能等對端一整天。這組上限大得多（預設跟 `RLIMIT_NPROC` 掛鉤）。

為什麼要分開上限？因為如果不分,一堆「等對端、無界阻塞」的 socket work 會把 worker 名額佔光,regular file I/O 就排不進去了。分兩組帳本,各自獨立限額,互不餓死。

## 排入佇列：`io_wq_enqueue`

請求被判定「只能阻塞」時，`io_uring.c` 走 `io_queue_iowq()`：

```c
static void io_queue_iowq(struct io_kiocb *req)
{
	struct io_uring_task *tctx = req->tctx;

	BUG_ON(!tctx);

	if ((current->flags & PF_KTHREAD) || !tctx->io_wq) {
		io_req_task_queue_fail(req, -ECANCELED);
		return;
	}

	/* init ->work of the whole link before punting */
	io_prep_async_link(req);
	...
	trace_io_uring_queue_async_work(req, io_wq_is_hashed(&req->work));
	io_wq_enqueue(tctx->io_wq, &req->work);
}
```

注意 `io_prep_async_link(req)`：如果這個請求是一條 link（Ch 16）的頭，整條 link 都要先準備好 async context 再 punt。然後進 `io_wq_enqueue()`（`io_uring/io-wq.c`）：

```c
void io_wq_enqueue(struct io_wq *wq, struct io_wq_work *work)
{
	unsigned int work_flags = atomic_read(&work->flags);
	struct io_wq_acct *acct = io_work_get_acct(wq, work_flags);
	struct io_cb_cancel_data match = {
		.fn		= io_wq_work_match_item,
		.data		= work,
		.cancel_all	= false,
	};
	bool do_create;

	if (test_bit(IO_WQ_BIT_EXIT, &wq->state) ||
	    (work_flags & IO_WQ_WORK_CANCEL)) {
		io_run_cancel(work, wq);
		return;
	}

	raw_spin_lock(&acct->lock);
	io_wq_insert_work(wq, acct, work, work_flags);
	clear_bit(IO_ACCT_STALLED_BIT, &acct->flags);
	raw_spin_unlock(&acct->lock);

	rcu_read_lock();
	do_create = !io_acct_activate_free_worker(acct);
	rcu_read_unlock();

	if (do_create && ((work_flags & IO_WQ_WORK_CONCURRENT) ||
	    !atomic_read(&acct->nr_running))) {
		bool did_create;

		did_create = io_wq_create_worker(wq, acct);
		if (likely(did_create))
			return;
		...
	}
}
```

邏輯三步：

1. **選帳本**：`io_work_get_acct` 按 bound/unbound 選一組 `acct`。
2. **插入佇列**：鎖 `acct->lock`，`io_wq_insert_work` 把 work 掛進 `acct->work_list`，清掉 stalled 標記（有新 work 進來，之前「卡在 hashed work」的狀態解除）。
3. **確保有 worker 來跑**：先 `io_acct_activate_free_worker` 試著喚醒一個 `free_list` 上閒置的 worker；喚不到（`do_create` 為真）而且目前沒有 running worker,就 `io_wq_create_worker` **新建一個**。

這個「先喚醒閒置的、沒有才新建」的策略，就是 io-wq 動態伸縮的核心：負載來了才長 worker,閒了就讓它們逾時自己死。

## worker 的一生：建立、幹活、回收

### 建立

`create_io_worker` → 底層用 `create_io_thread()` 開一個真正的 kernel thread，entry point 是 `io_wq_worker()`。worker 一誕生就跑這個主迴圈（`io_uring/io-wq.c`）：

```c
static int io_wq_worker(void *data)
{
	struct io_worker *worker = data;
	struct io_wq_acct *acct = io_wq_get_acct(worker);
	struct io_wq *wq = worker->wq;
	bool exit_mask = false, last_timeout = false;
	char buf[TASK_COMM_LEN] = {};

	set_mask_bits(&worker->flags, 0,
		      BIT(IO_WORKER_F_UP) | BIT(IO_WORKER_F_RUNNING));

	snprintf(buf, sizeof(buf), "iou-wrk-%d", wq->task->pid);
	set_task_comm(current, buf);

	while (!test_bit(IO_WQ_BIT_EXIT, &wq->state)) {
		long ret;

		set_current_state(TASK_INTERRUPTIBLE);

		while (io_acct_run_queue(acct))
			io_worker_handle_work(acct, worker);

		raw_spin_lock(&acct->workers_lock);
		if (last_timeout && (exit_mask || acct->nr_workers > 1)) {
			acct->nr_workers--;
			raw_spin_unlock(&acct->workers_lock);
			__set_current_state(TASK_RUNNING);
			break;
		}
		last_timeout = false;
		__io_worker_idle(acct, worker);
		raw_spin_unlock(&acct->workers_lock);
		if (io_run_task_work())
			continue;
		ret = schedule_timeout(WORKER_IDLE_TIMEOUT);
		if (signal_pending(current)) {
			struct ksignal ksig;
			if (!get_signal(&ksig))
				continue;
			break;
		}
		if (!ret) {
			last_timeout = true;
			exit_mask = !cpumask_test_cpu(raw_smp_processor_id(),
							wq->cpu_mask);
		}
	}
	...
	io_worker_exit(worker);
	return 0;
}
```

這段把 worker 的一生講完了：

- 設好名字 `iou-wrk-<pid>`，標上 `IO_WORKER_F_UP | IO_WORKER_F_RUNNING`。
- 主迴圈：有 work 就 `io_worker_handle_work` 一直做；沒 work 就 `__io_worker_idle`（掛回 `free_list`）然後 `schedule_timeout(WORKER_IDLE_TIMEOUT)` 睡著等。
- **回收條件**：如果上一次睡覺是逾時醒來（`last_timeout`），而且「這組不只我一個 worker」或「我的 CPU affinity 變了」，就 `nr_workers--` 然後 `break` 出去 `io_worker_exit`——自己退場。這就是「閒置自動回收」。`WORKER_IDLE_TIMEOUT` 是幾秒等級;最後一個 worker 不會因逾時退場（留著待命）。

### 幹活

`io_worker_handle_work()` 是 worker 從佇列取 work、跑 work 的核心（`io_uring/io-wq.c`）：

```c
static void io_worker_handle_work(struct io_wq_acct *acct,
				  struct io_worker *worker)
	__releases(&acct->lock)
{
	struct io_wq *wq = worker->wq;
	bool do_kill = test_bit(IO_WQ_BIT_EXIT, &wq->state);

	do {
		struct io_wq_work *work;

		work = io_get_next_work(acct, wq);
		if (work) {
			raw_spin_lock(&worker->lock);
			worker->cur_work = work;
			raw_spin_unlock(&worker->lock);
		}
		raw_spin_unlock(&acct->lock);
		if (!work)
			break;
		__io_worker_busy(acct, worker);
		io_assign_current_work(worker, work);
		__set_current_state(TASK_RUNNING);

		/* handle a whole dependent link */
		do {
			...
			io_wq_submit_work(work);
			io_assign_current_work(worker, NULL);
			linked = io_wq_free_work(work);
			work = next_hashed;
			...
		} while (work);
	} while (1);
}
```

重點：

- `io_get_next_work` 從 `acct->work_list` 取下一個。這裡有個 **hashed work** 機制：`io_wq_hash_work` 按 inode 把寫入 hash 到同一個 bucket，同一 bucket 的 work **不並行**（避免兩個 worker 同時寫同一個檔案造成亂序）。`hash_tail[]` 和 `IO_ACCT_STALLED_BIT` 就是為這個服務的。
- `worker->cur_work = work` 要在真正執行**之前**設好,這樣 cancel（Ch 30）才找得到「這個 worker 正在跑哪個請求」。
- 真正執行 work 的是 `io_wq_submit_work`——它在 `io_uring.c` 那邊。

### work 怎麼被執行：`io_wq_submit_work`

這是 io-wq 和 io_uring 主體的接點（`io_uring/io_uring.c`）：

```c
void io_wq_submit_work(struct io_wq_work *work)
{
	struct io_kiocb *req = container_of(work, struct io_kiocb, work);
	const struct io_issue_def *def = &io_issue_defs[req->opcode];
	unsigned int issue_flags = IO_URING_F_UNLOCKED | IO_URING_F_IOWQ;
	bool needs_poll = false;
	int ret = 0, err = -ECANCELED;

	if (!(req->flags & REQ_F_REFCOUNT))
		__io_req_set_refcount(req, 2);
	else
		req_ref_get(req);
	...
	do {
		ret = io_issue_sqe(req, issue_flags);
		if (ret != -EAGAIN)
			break;
		if (req->flags & REQ_F_NOWAIT)
			break;
		...
	} while (1);
}
```

兩個關鍵 flag：

- **`IO_URING_F_UNLOCKED`**：worker **沒有** `uring_lock`——它是獨立 thread，不在提交主線上。
- **`IO_URING_F_IOWQ`**：告訴底下「這是 io-wq context」。還記得 Ch 25 的 `io_req_complete_post` 開頭那個 `WARN_ON_ONCE(!(issue_flags & IO_URING_F_IOWQ))` 嗎?就是在這裡滿足的——只有 io-wq 完成才走那條 post 路徑。

注意這次 `io_issue_sqe` **沒有**帶 `IO_URING_F_NONBLOCK`。所以 operation handler 這次是**用阻塞模式**跑的——它可以安心 `schedule()` 睡著，因為睡的是 worker thread,不是任何人的提交路徑。這就是整個 io-wq 存在的理由:**在一個可以隨便睡的 context 裡,做那件會睡的事**。

跑完之後,完成走 Ch 25 的路徑 B（`io_req_complete_post`）。

## io-wq 和 task_work 的分工

初學最容易混的兩個機制,放在一起分清楚:

| | io-wq | task_work（Ch 27） |
|---|---|---|
| 本質 | 獨立 kernel thread | 掛在**原 task** 上的回呼 |
| 解決的問題 | 操作**會阻塞**,需要一個能睡的 context | 操作**完成**了,需要回到原 task context 填 CQE / 收尾 |
| 在誰的 context 跑 | `iou-wrk-*` worker thread | 提交請求的那個 user task |
| 典型場景 | regular file buffered I/O、fsync、openat | poll 就緒後執行、完成回呼、link 推進 |
| 可以睡嗎 | 可以（這是重點） | 一般不睡,是輕量回呼 |

一句話:**io-wq 是「替你去睡」,task_work 是「回到你這裡收尾」**。一個阻塞的 read 可能兩個都用到:先 punt 到 io-wq 讓 worker 睡著等磁碟(io-wq),worker 把資料讀回來、算好 `res`,如果是 DEFER_TASKRUN ring 還要把「填 CQE」這動作用 task_work 送回提交者 task(task_work,Ch 25 路徑 B 退回 C)。兩者是接力,不是二選一。

還有一個優化要知道:很多「看起來會阻塞」的 op 其實能先用 poll 擋下來,不用真的開 worker。`io_wq_submit_work` 裡那段 `REQ_F_MULTISHOT`、`io_arm_poll_handler` 的處理就是:如果這個 file 可以 poll,寧可掛 poll(Ch 28)也不要佔一個 io-wq worker。io-wq 是**最後手段**,能 poll 就 poll,能 nonblock 就 nonblock。

## 底層機制:running 計數與「該不該再開一個」

`nr_running` 是 io-wq 動態伸縮的樞紐。worker 被排進來跑時 `io_wq_inc_running`,睡著(去等 I/O)時 `io_wq_dec_running`(`io_uring/io-wq.c`):

```c
void io_wq_worker_running(struct task_struct *tsk)
{
	struct io_worker *worker = tsk->worker_private;
	...
	if (test_bit(IO_WORKER_F_RUNNING, &worker->flags))
		return;
	set_bit(IO_WORKER_F_RUNNING, &worker->flags);
	io_wq_inc_running(worker);
}
```

這兩個函式掛在排程器的 sched_in / sched_out 鉤子上。意義是:當一個 worker **因為等 I/O 而睡著**(sched_out),`nr_running` 減一——代表「現在實際在 CPU 上推進 work 的 worker 少了一個」。`io_wq_enqueue` 看到 `!atomic_read(&acct->nr_running)` 就知道「所有 worker 都睡死在 I/O 上了,來的新 work 沒人跑」,於是**再開一個**。

這設計很巧:worker 數量不是看「佇列多長」,而是看「有多少在睡」。只要還有一個 worker 在實際跑,就不急著開新的;一旦全部睡在 I/O,馬上補一個上來。這讓 io-wq 在「大量並發阻塞 I/O」時能自動長出夠多的 worker,又不會在輕載時浪費 thread。

## 對比與取捨

**為什麼不直接用 kernel 的 workqueue(`kworker`)?** io_uring 早期考慮過,但自己的 io-wq 有幾個非它不可的理由:

- **身份繼承**:io-wq worker 要以**提交者的身份**(creds、cgroup、namespace、mm)去跑 I/O,否則權限檢查會錯。`iou-wrk` thread 是用 `create_io_thread` 從提交 task clone 出來的,繼承這些;通用 kworker 做不到。
- **per-task 隔離與計費**:io-wq 是 per-tctx 的,一個 task 的阻塞 I/O 不會吃掉別 task 的 worker 名額,資源也算在對的 task 頭上。
- **精細的建立/回收控制**:按 `nr_running` 動態伸縮、bound/unbound 分組限額,這些是 io_uring I/O 模式特化的策略,通用 workqueue 給不了。

取捨是:io_uring 得自己維護一套相當複雜的 thread pool(io-wq.c 一千多行),也因此成了 Part 4 一整類 race 的來源——worker thread 和提交 task、cancel、teardown 之間的同步極其微妙。

## 踩雷集錦

1. **以為每個 io_uring 操作都走 io-wq**:大錯。絕大多數操作在提交路徑用 `NONBLOCK` 就地完成,或掛 poll 就緒再跑。**只有「不能 nonblock、又不能 poll」的操作**(主要是 regular file I/O 和部分 VFS metadata op)才 punt 到 io-wq。io-wq 是慢路、是後備。

2. **把 6.18 的 io-wq 想成有 `io_wqe`**:per-NUMA-node 的 `io_wqe` 結構在 6.18 **已經不存在**,`io_wq` 直接內嵌 `acct[2]`。看舊資料(5.x、早期 writeup)談 `io_wqe` 時,心裡要換算成現在的 `io_wq` + `io_wq_acct`。

3. **混淆 bound 和 unbound 的限額**:bound(regular file)worker 上限跟 CPU 數掛鉤、很小;unbound(socket)上限大得多。以為「socket recv 開不出足夠 worker」去調錯參數,是因為沒分清兩組帳本各自獨立限額。

4. **以為 io-wq worker 持有 `uring_lock`**:它帶的是 `IO_URING_F_UNLOCKED`——**沒有** uring_lock。這正是為什麼 io-wq 完成不能走 deferred 批次(那需要 uring_lock),只能走 `io_req_complete_post`(Ch 25 路徑 B)。

5. **把 io-wq 和 task_work 當同一回事**:io-wq 是「替你去睡的獨立 thread」,task_work 是「回到你 task 的輕量回呼」。一個阻塞 read 可能先 io-wq 後 task_work 接力。搞混會讓你讀 Ch 27、Ch 30 時整個對不上。

## 進階:hashed work 與同檔案寫入順序

`io_wq` 裡那個 `hash_tail[IO_WQ_NR_HASH_BUCKETS]` 和 `io_wq_hash_work` 解決一個真實問題:如果對同一個 regular file 併發發好幾個 buffered write,分給不同 worker 並行跑,寫入順序會亂,甚至破壞檔案一致性。io-wq 的解法是按 inode 把這些 write **hash 到同一個 bucket**,同 bucket 的 work 序列化執行(一個做完才做下一個),跨 bucket 才並行。

```c
void io_wq_hash_work(struct io_wq_work *work, void *val)
{
	unsigned int bit;

	bit = hash_ptr(val, IO_WQ_HASH_ORDER);
	atomic_or(IO_WQ_WORK_HASHED | (bit << IO_WQ_HASH_SHIFT), &work->flags);
}
```

worker 在 `io_worker_handle_work` 裡取到一個 hashed work,會順著 `hash_tail` 把同 bucket 的後續 work 接著做完(那段 `do { ... } while` 的 `next_hashed`),不讓別的 worker 插隊。`IO_ACCT_STALLED_BIT` 則標記「這組所有 pending work 都卡在同一個正在跑的 hash bucket 上,暫時別再找工作」。這是 io-wq 一個容易忽略但對檔案 I/O 正確性很關鍵的細節。

## 本章重點整理

- io-wq 存在的唯一理由:**對「不能 nonblock、又不能 poll」的操作(主要 regular file I/O),開一個獨立 kernel thread 去替你阻塞睡眠**,不拖累任何人的提交路徑。
- io-wq 是 **per-task** 的(`tctx->io_wq`),內部分 **bound**(regular file,阻塞有界,worker 少)和 **unbound**(socket,阻塞無界,worker 多)兩組 `io_wq_acct`,各自獨立佇列與限額。
- **6.18 沒有 `io_wqe`**:per-node 結構已合併,`io_wq` 直接內嵌 `acct[2]`。
- 排入:`io_queue_iowq` → `io_wq_enqueue`,先喚醒 `free_list` 的閒置 worker,沒有才 `io_wq_create_worker` 新建。
- 執行:worker 主迴圈 `io_wq_worker` → `io_worker_handle_work` → `io_wq_submit_work`,帶 `IO_URING_F_UNLOCKED | IO_URING_F_IOWQ`,這次 `io_issue_sqe` **不帶 NONBLOCK**,handler 可安心睡。
- 回收:worker 閒置 `schedule_timeout(WORKER_IDLE_TIMEOUT)`,逾時且非最後一個就自己退場。伸縮看 `nr_running`(睡著的 worker 讓它變 0,觸發補新 worker)。
- io-wq(替你去睡)和 task_work(回到你 task 收尾)是**接力**不是二選一;能 poll 的優先掛 poll,io-wq 是最後手段。
- hashed work 按 inode 序列化同檔案寫入,避免併發 buffered write 亂序。

## 自我檢核

- [ ] 我能說出哪些操作會 punt 到 io-wq、哪些不會,以及為什麼 regular file I/O 非它不可
- [ ] 我能解釋 bound 和 unbound 兩組帳本各服務什麼、為什麼要分開限額
- [ ] 我能描述一個 worker 從建立、取 work、跑 work、到閒置回收的完整生命週期
- [ ] 我知道 `io_wq_submit_work` 帶的兩個 issue flag,以及它們怎麼接回 Ch 25 的完成路徑 B
- [ ] 我能講清楚 io-wq 和 task_work 的分工,並舉一個「兩者接力」的例子
- [ ] 我能解釋 `nr_running` 為什麼是伸縮的樞紐,以及 hashed work 解決什麼問題

## 延伸閱讀

### 必讀

- **Linux 6.18 原始碼 `io_uring/io-wq.c`** — <https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/io_uring/io-wq.c?h=v6.18>
  - **讀哪裡**:`io_wq_enqueue`、`io_wq_worker`、`io_worker_handle_work` 三個函式;對照本章把 work 從入列到執行走一遍
  - **和本章關聯**:本章所有結構與函式名都可在此 grep 到

### 底層機制(repo 內)

- **[../kernel_internals/30-softirq-workqueue.md](../kernel_internals/30-softirq-workqueue.md)**
  - **讀哪裡**:workqueue 與 kworker 的通用機制那節
  - **和本章關聯**:理解 io-wq **為什麼不直接用** kernel workqueue(身份繼承、per-task 隔離),先懂通用版本的限制

- **[../kernel_internals/18-slub-allocator.md](../kernel_internals/18-slub-allocator.md)**
  - **讀哪裡**:物件生命週期與 cache 那節
  - **和本章關聯**:worker、work 物件的配置與回收是 Part 4 race 的舞台,先建立 slab 直覺

### LWN / 設計

- **[The io_uring worker pool](https://lwn.net/Articles/838331/)** 與 LWN io_uring 系列
  - **讀哪裡**:搜 LWN 談 io-wq / async worker 的報導
  - **注意**:早期文章仍以 `io_wqe` / per-node 描述,對照 6.18 要知道已簡化

### 工具

- **`ps -eLf | grep iou-wrk`**
  - **用途**:跑一個會觸發 io-wq 的 io_uring 程式(例如對沒 cache 的大檔做大量 buffered read),即時看 `iou-wrk-<pid>` worker thread 長出來又消失
  - **和本章關聯**:把「動態建立/回收」從 code 變成你螢幕上真的看得到的東西

io-wq 替我們把「會睡的事」搬到獨立 thread 跑完了。但這些 worker 把 `res` 算好之後,常常**不能**自己填 CQE(DEFER_TASKRUN ring),得把「收尾」送回原本那個提交 task。這個「送回原 task」的機制就是 task_work——下一章的主角,也是 io_uring 近年效能演進(COOP_TASKRUN、DEFER_TASKRUN)的核心。

→ [Ch 27 task_work、DEFER_TASKRUN、SINGLE_ISSUER](./27-task-work-defer.md)
