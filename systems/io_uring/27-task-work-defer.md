# Ch 27 — task_work、DEFER_TASKRUN、SINGLE_ISSUER

> **目標**：搞懂 io_uring 怎麼把「完成收尾」送回原本那個提交 task 去做,為什麼非回去不可,以及這套 task_work 機制如何演進成 `COOP_TASKRUN`(省 IPI)、`DEFER_TASKRUN`(把完成延到下次 enter)和 `SINGLE_ISSUER`(只有一個提交者)這三個改變 io_uring 效能格局的設定。你會看懂兩條 task_work 路徑(normal vs local)的真實原始碼、`TWA_SIGNAL` 是什麼、以及這些 flag 彼此的約束關係。

> **環境**：kernel 6.18 原始碼(`v6.18`,commit `7d0a66e`)。對照 `io_uring/io_uring.c`、`io_uring/io_uring.h`。

## 為什麼完成要「回到原 task」

Ch 25 留了一個伏筆:很多完成(block IRQ、poll 就緒、io-wq worker)**不在提交者 task 的 context**,它們不能直接填 CQE,得把工作送回去。為什麼?三個理由,一個比一個硬:

1. **身份與資源**:填 CQE 之外的收尾動作(釋放 buffer、推進 link、unregister 資源)可能需要提交者的 `mm`、creds、file table。在 IRQ 或別的 thread 做,拿到的是錯的 context。
2. **DEFER_TASKRUN 的單一 producer 假設**:Ch 25 看到 `lockless_cq` ring 省掉 `completion_lock`,前提是「只有提交者 task 填 CQE」。要維持這個假設,所有完成都**必須**排回提交者 task 才填。
3. **可以睡的收尾不該在 atomic context 做**:IRQ context 不能睡、不能拿 mutex。把收尾延到 task context,就能用 `uring_lock`(mutex)、能做需要睡的清理。

Linux kernel 本來就有一個通用機制做這件事:**task_work**。它讓你把一個回呼掛到某個 task 上,那個 task 下次從 kernel 返回 user space(或被明確要求執行)時,就會跑這些回呼。io_uring 重度使用它——幾乎每個非 inline 的完成,最後都是一筆 task_work。

## 先建立直覺:兩條 task_work 路徑

io_uring 有**兩套**task_work 排程,選哪套看有沒有設 `DEFER_TASKRUN`:

```
   某個 context 要完成一個 req
   設好 req->io_task_work.func（如 io_req_task_complete）
   io_req_task_work_add(req)
            │
            ▼
   __io_req_task_work_add(req, 0)
            │
   DEFER_TASKRUN?  ──否──►  io_req_normal_work_add
            │否                    │ llist_add 到 tctx->task_list
            │                      │ task_work_add(task, cb, notify_method)
            是                     │ notify_method = TWA_SIGNAL / TWA_SIGNAL_NO_IPI
            ▼                      ▼
   io_req_local_work_add      核心 task_work 機制:task 返回 user 前
            │ llist_add 到      或被 signal 喚醒時,跑 tctx_task_work
            │ ctx->work_llist         │
            │ 條件式喚醒提交者          ▼
            ▼                    tctx_task_work_run → io_handle_tw_list
   不一定馬上喚醒;可以攢著         逐個 req->io_task_work.func(req, tw)
   等提交者下次 io_uring_enter          │
   主動 io_run_local_work 收割          ▼
            │                     回到 Ch 25:io_req_task_complete
            └──────────────────►  → io_req_complete_defer（匯回批次填 CQE）
```

兩條路最後都呼叫 `req->io_task_work.func`,差別在**什麼時候、怎麼觸發提交者去跑**:

- **normal**(無 DEFER_TASKRUN):用核心 kernel 的 `task_work_add`,靠 `TWA_SIGNAL` 這類通知**主動**戳提交者 task,讓它盡快跑。
- **local**(DEFER_TASKRUN):把 work 掛進 ctx 自己的 `work_llist`,**不一定馬上戳**提交者;等提交者**下次主動 `io_uring_enter`** 時自己收割。把「完成」批次延到下次 enter,省掉中間的喚醒/IPI。

## 入口:`io_req_task_work_add`

所有完成收尾的起點是這個(`io_uring/io_uring.h`):

```c
static inline void io_req_task_work_add(struct io_kiocb *req)
{
	__io_req_task_work_add(req, 0);
}
```

`__io_req_task_work_add` 就是上圖那個分叉(`io_uring/io_uring.c`):

```c
void __io_req_task_work_add(struct io_kiocb *req, unsigned flags)
{
	if (req->ctx->flags & IORING_SETUP_DEFER_TASKRUN)
		io_req_local_work_add(req, flags);
	else
		io_req_normal_work_add(req);
}
```

一個 `if` 決定命運。先看傳統那條(normal)。

## normal path:`io_req_normal_work_add` 與 TWA_SIGNAL

```c
static void io_req_normal_work_add(struct io_kiocb *req)
{
	struct io_uring_task *tctx = req->tctx;
	struct io_ring_ctx *ctx = req->ctx;

	/* task_work already pending, we're done */
	if (!llist_add(&req->io_task_work.node, &tctx->task_list))
		return;

	if (ctx->flags & IORING_SETUP_TASKRUN_FLAG)
		atomic_or(IORING_SQ_TASKRUN, &ctx->rings->sq_flags);

	/* SQPOLL doesn't need the task_work added, it'll run it itself */
	if (ctx->flags & IORING_SETUP_SQPOLL) {
		__set_notify_signal(tctx->task);
		return;
	}

	if (likely(!task_work_add(tctx->task, &tctx->task_work, ctx->notify_method)))
		return;

	io_fallback_tw(tctx, false);
}
```

逐步:

1. **`llist_add` 到 `tctx->task_list`**:把這個 req 掛進該 task 的 task_work lock-free 單向鏈。`llist_add` 回傳「這是不是第一個加入的」——如果不是第一個(鏈上已有 pending work),代表 `task_work_add` 之前就呼叫過了,**直接 return**,不重複通知。這個優化很重要:一批完成只戳提交者一次就夠。
2. **`IORING_SQ_TASKRUN` flag**:如果設了 `TASKRUN_FLAG`,在 `sq_flags` 設一個位元,讓 user space **不用進 kernel 就知道「有 task_work 待跑」**——這樣 user 可以主動呼叫 `io_uring_enter` 去跑它們(配合 COOP_TASKRUN,下詳)。
3. **SQPOLL 特例**:SQPOLL thread(Ch 9)自己會跑 task_work,只需 `__set_notify_signal` 戳一下,不走 `task_work_add`。
4. **`task_work_add(task, cb, notify_method)`**:這是核心 kernel 的 API,把 `tctx->task_work` 這個 callback 掛到提交者 task 上,並按 `notify_method` 通知它。失敗(task 正在死)就走 `io_fallback_tw` 備援。

### notify_method:TWA_SIGNAL vs TWA_SIGNAL_NO_IPI

`ctx->notify_method` 在 setup 時決定(`io_uring/io_uring.c`,約 3846 行):

```c
	/*
	 * For SQPOLL, we just need a wakeup, always. For !SQPOLL, if
	 * COOP_TASKRUN is set, then IPIs are never needed by the app.
	 */
	if (ctx->flags & (IORING_SETUP_SQPOLL|IORING_SETUP_COOP_TASKRUN))
		ctx->notify_method = TWA_SIGNAL_NO_IPI;
	else
		ctx->notify_method = TWA_SIGNAL;
```

這是理解 `COOP_TASKRUN` 的關鍵:

- **`TWA_SIGNAL`**(預設):掛上 task_work 後,如果提交者 task 正在**別的 CPU 上跑**(例如在 user space 算東西),就發一個 **IPI**(inter-processor interrupt)把它戳進 kernel,讓它馬上跑 task_work。好處是完成延遲低;壞處是**IPI 很貴**——打斷另一顆 CPU。
- **`TWA_SIGNAL_NO_IPI`**:掛上 task_work,但**不發 IPI**。提交者 task 等到自己**下次自然進 kernel**(下個 syscall、下次 `io_uring_enter`、被 reschedule)時才跑 task_work。延遲高一點,但**完全省掉 IPI 開銷**。

`IORING_SETUP_COOP_TASKRUN`(cooperative task run)就是跟 kernel 說:「我的 app 會**主動且頻繁**進 kernel(反正我一直在 submit/wait),你不用 IPI 催我,等我自己來」。對一個高吞吐、一直在 `io_uring_enter` 的 app,這省下的 IPI 是實打實的效能。搭配 `TASKRUN_FLAG`,app 甚至能在 user space 檢查 `sq_flags` 的 `IORING_SQ_TASKRUN` 位元,決定何時進來跑。

## normal path 的執行:`tctx_task_work`

提交者 task 被通知後(或自然進 kernel 後),核心 kernel 會跑掛上去的 callback,也就是 `tctx_task_work`(`io_uring/io_uring.c`):

```c
void tctx_task_work(struct callback_head *cb)
{
	struct io_uring_task *tctx;
	struct llist_node *ret;
	unsigned int count = 0;

	tctx = container_of(cb, struct io_uring_task, task_work);
	ret = tctx_task_work_run(tctx, UINT_MAX, &count);
	/* can't happen */
	WARN_ON_ONCE(ret);
}

struct llist_node *tctx_task_work_run(struct io_uring_task *tctx,
				      unsigned int max_entries,
				      unsigned int *count)
{
	struct llist_node *node;

	if (unlikely(current->flags & PF_EXITING)) {
		io_fallback_tw(tctx, true);
		return NULL;
	}

	node = llist_del_all(&tctx->task_list);
	if (node) {
		node = llist_reverse_order(node);
		node = io_handle_tw_list(node, count, max_entries);
	}
	...
	return node;
}
```

`llist_del_all` 一次把整條 `task_list` 摘下來,`llist_reverse_order` 轉回正序(llist 是後進前出,要翻回提交順序),然後 `io_handle_tw_list` 逐個呼叫 `req->io_task_work.func(req, tw)`。對「最終完成」的 req,那個 func 就是 Ch 25 的 `io_req_task_complete` → `io_req_complete_defer` → 匯回批次填 CQE。

**關鍵**:`tctx_task_work` 是在提交者 task、持有 `uring_lock` 的情況下跑的。所以此刻做 `io_req_complete_defer`(攢進 `compl_reqs`)、最後 `io_submit_flush_completions` 批次填 CQE,全都在對的 context、對的鎖底下。這就是「把完成送回原 task」整個繞路的終點。

## local path:`io_req_local_work_add`(DEFER_TASKRUN)

現在看 DEFER_TASKRUN 那條。它不走核心 kernel 的 `task_work_add`,自己維護一條 `ctx->work_llist`(`io_uring/io_uring.c`):

```c
static void io_req_local_work_add(struct io_kiocb *req, unsigned flags)
{
	struct io_ring_ctx *ctx = req->ctx;
	unsigned nr_wait, nr_tw, nr_tw_prev;
	struct llist_node *head;
	...
	guard(rcu)();

	head = READ_ONCE(ctx->work_llist.first);
	do {
		nr_tw_prev = 0;
		if (head) {
			struct io_kiocb *first_req = container_of(head,
							struct io_kiocb,
							io_task_work.node);
			/*
			 * Might be executed at any moment, rely on
			 * SLAB_TYPESAFE_BY_RCU to keep it alive.
			 */
			nr_tw_prev = READ_ONCE(first_req->nr_tw);
		}

		nr_tw = nr_tw_prev + 1;
		if (!(flags & IOU_F_TWQ_LAZY_WAKE))
			nr_tw = IO_CQ_WAKE_FORCE;

		req->nr_tw = nr_tw;
		req->io_task_work.node.next = head;
	} while (!try_cmpxchg(&ctx->work_llist.first, &head,
			      &req->io_task_work.node));

	if (!head) {
		if (ctx->flags & IORING_SETUP_TASKRUN_FLAG)
			atomic_or(IORING_SQ_TASKRUN, &ctx->rings->sq_flags);
		if (ctx->has_evfd)
			io_eventfd_signal(ctx, false);
	}

	nr_wait = atomic_read(&ctx->cq_wait_nr);
	/* not enough or no one is waiting */
	if (nr_tw < nr_wait)
		return;
	/* the previous add has already woken it up */
	if (nr_tw_prev >= nr_wait)
		return;
	wake_up_state(ctx->submitter_task, TASK_INTERRUPTIBLE);
}
```

這段比 normal 複雜,因為它做了一件 normal 不做的事:**條件式喚醒**。重點:

- work 掛進 `ctx->work_llist`(ctx 層級,不是 tctx 層級——因為只有一個提交者 task,work 屬於 ctx)。
- `nr_tw` 記錄「目前累積了幾個 pending work」。
- 結尾那段:只有當累積的 work 數 `nr_tw` **達到提交者正在等的數量 `cq_wait_nr`** 時,才 `wake_up_state` 叫醒提交者。如果提交者在等 4 個完成,現在才累積 2 個,**不喚醒**——讓它繼續睡,攢夠了一次叫醒。

這就是 DEFER_TASKRUN 省喚醒的精髓:**完成不主動催提交者,而是攢著,等它要的數量到了、或它自己進來收,才動作**。`IOU_F_TWQ_LAZY_WAKE` 控制「lazy 喚醒」——不急著在第一個 work 就叫醒。

那 work 什麼時候真正被跑?提交者下次 `io_uring_enter(GETEVENTS)` 時,會主動呼叫 `io_run_local_work`(底層 `__io_run_local_work`):

```c
static int __io_run_local_work(struct io_ring_ctx *ctx, io_tw_token_t tw,
			       int min_events, int max_events)
{
	struct llist_node *node;
	...
	if (WARN_ON_ONCE(ctx->submitter_task != current))
		return -EEXIST;
	...
	node = llist_reverse_order(llist_del_all(&ctx->work_llist));
	ret += __io_run_local_work_loop(&node, tw, max_events - ret);
	...
	io_submit_flush_completions(ctx);
	...
}
```

注意開頭那個 `WARN_ON_ONCE(ctx->submitter_task != current)`:**local work 只能由註冊的那個提交者 task 跑**。這直接連到 SINGLE_ISSUER——下節。

## SINGLE_ISSUER:只有一個提交者

`IORING_SETUP_SINGLE_ISSUER` 是你向 kernel 承諾:**這個 ring 只會有一個 task 提交請求**。setup 時(`io_uring/io_uring.c`,約 3878 行):

```c
	if (ctx->flags & IORING_SETUP_SINGLE_ISSUER
	    && !(ctx->flags & IORING_SETUP_R_DISABLED)) {
		ctx->submitter_task = get_task_struct(current);
	}
```

記下 `submitter_task`。之後很多路徑會 `WARN_ON_ONCE(ctx->submitter_task != current)` 來強制這個承諾。

SINGLE_ISSUER 和 DEFER_TASKRUN 的關係是**強制綁定**的(`io_uring/io_uring.c`,約 3706 行):

```c
	/*
	 * For DEFER_TASKRUN we require the completion task to be the same as
	 * the submission task. This implies that there is only one submitter.
	 */
	if ((flags & IORING_SETUP_DEFER_TASKRUN) &&
	    !(flags & IORING_SETUP_SINGLE_ISSUER))
		return -EINVAL;
```

**DEFER_TASKRUN 必須搭配 SINGLE_ISSUER**,否則 setup 直接 `-EINVAL`。原因是一條因果鏈:

- DEFER_TASKRUN 要「把完成攢在 `work_llist`,等提交者自己來收」——這只有在**提交者唯一且確定**時才成立(否則該叫醒誰?誰來收?)。
- 提交者唯一 → CQ 的 producer 唯一 → 可以 `lockless_cq`(Ch 25 省掉 `completion_lock`)。
- 提交者唯一 → 完成都排回同一個 task → 批次填 CQE 的 `uring_lock` 排他自然成立。

所以這三個設定不是獨立的開關,而是一個**協同設計**:`SINGLE_ISSUER` 給出「唯一提交者」的保證,`DEFER_TASKRUN` 利用這保證把完成延後批次化,`lockless_cq` 再利用它省鎖。這就是為什麼 io_uring 社群建議「追求極致效能就開 `DEFER_TASKRUN | SINGLE_ISSUER`」——它們一起把提交/完成路徑上的 IPI、喚醒、鎖全部削掉。

## 各 flag 的約束一覽

setup 時的合法性檢查(`io_uring/io_uring.c` 的 `io_uring_sanitise_params` 附近)把這些約束寫死:

```c
	if (flags & IORING_SETUP_SQPOLL) {
		/* IPI related flags don't make sense with SQPOLL */
		if (flags & (IORING_SETUP_COOP_TASKRUN |
			     IORING_SETUP_TASKRUN_FLAG |
			     IORING_SETUP_DEFER_TASKRUN))
			return -EINVAL;
	}

	if (flags & IORING_SETUP_TASKRUN_FLAG) {
		if (!(flags & (IORING_SETUP_COOP_TASKRUN |
			       IORING_SETUP_DEFER_TASKRUN)))
			return -EINVAL;
	}
	...
	if ((flags & IORING_SETUP_DEFER_TASKRUN) &&
	    !(flags & IORING_SETUP_SINGLE_ISSUER))
		return -EINVAL;
```

整理成表:

| flag | 作用 | 約束 |
|---|---|---|
| `SQPOLL` | kernel thread 自己輪詢提交 | 和 COOP/TASKRUN_FLAG/DEFER_TASKRUN **互斥**(SQPOLL 自己跑 task_work,IPI 類 flag 無意義) |
| `COOP_TASKRUN` | notify 用 `TWA_SIGNAL_NO_IPI`,省 IPI | app 需頻繁進 kernel 才划算 |
| `TASKRUN_FLAG` | 在 `sq_flags` 曝露 `IORING_SQ_TASKRUN`,讓 user 不進 kernel 也知道有 task_work | 必須搭 COOP 或 DEFER |
| `DEFER_TASKRUN` | 完成延到下次 enter,ctx 層 `work_llist`,條件式喚醒 | **必須搭 SINGLE_ISSUER** |
| `SINGLE_ISSUER` | 承諾唯一提交者,記 `submitter_task` | DEFER_TASKRUN 的前提;也獨立開啟 `lockless_cq` 相關優化 |

## 底層機制:為什麼 llist(lock-free list)

兩條路都用 `llist`(lock-free single-linked list)掛 work,不是普通 list + spinlock。因為加 work 的 context 五花八門——IRQ、io-wq worker、別的 CPU——它們要能**無鎖、原子地**把一個 node 推進鏈頭。`llist_add` 底層是一個 `cmpxchg` 迴圈(你在 `io_req_local_work_add` 看到的 `try_cmpxchg`),無論多少 producer 同時推都安全,而且不會在 IRQ context 卡在 spinlock 上。消費端(提交者 task)則用 `llist_del_all` 一次把整條摘走再慢慢處理。這是「多 producer 無鎖推入、單 consumer 批次取出」的經典 lock-free 模式,和 Ch 25 的 `compl_reqs` 批次化精神一致。

`SLAB_TYPESAFE_BY_RCU`(Ch 25 提過 req cache 用它)在這裡也現身:`io_req_local_work_add` 讀 `head` 上那個 req 的 `nr_tw` 時,那個 req **可能正被別的 context 完成、釋放**。`SLAB_TYPESAFE_BY_RCU` 保證「即使被 free 回 slab,在 RCU grace period 內該記憶體仍是同型別的 req、讀它不會踩到別的東西」。所以那段 `guard(rcu)()` 不是裝飾——它和 Ch 30 teardown 尾端那個 `synchronize_rcu()` 配對,是這條 lock-free 路徑安全的基礎。橫向見 [../kernel_internals/27-rcu.md](../kernel_internals/27-rcu.md)。

## 對比與取捨

| 面向 | normal task_work | local work(DEFER_TASKRUN) |
|---|---|---|
| work 掛哪 | `tctx->task_list`(per-task) | `ctx->work_llist`(per-ctx) |
| 誰能提交 | 任意 task | 只有 `submitter_task` |
| 通知方式 | `task_work_add` + `TWA_SIGNAL`(可能 IPI) | 條件式 `wake_up_state`,常常不喚醒 |
| 何時跑 | task 返回 user / 被 signal 時 | 提交者下次 `io_uring_enter` 主動收 |
| 省什麼 | COOP 可省 IPI | 省 IPI + 省喚醒 + 配合 lockless_cq 省鎖 |
| 代價 | 通用,無額外約束 | 綁 SINGLE_ISSUER,只能單提交者 |

取捨很清楚:**普適性 vs 極致效能**。normal path 不限提交者數量、不要求 app 配合,是安全預設。DEFER_TASKRUN 把所有能省的開銷都省掉,但要你保證單提交者、且 app 願意「主動進 kernel 收完成」的模式。寫高吞吐伺服器(每個 thread 一個 ring、自己 submit 自己 wait)的話,後者是現代 io_uring 的建議配置。

## 踩雷集錦

1. **以為 DEFER_TASKRUN 可以不搭 SINGLE_ISSUER**:setup 直接 `-EINVAL`。這兩個是綁定的——DEFER_TASKRUN 的整套「攢著等提交者來收」邏輯建立在「提交者唯一且確定」之上。

2. **把 COOP_TASKRUN 當成 DEFER_TASKRUN**:兩者不同層次。COOP_TASKRUN 只是把 normal path 的 notify 從 `TWA_SIGNAL` 換成 `TWA_SIGNAL_NO_IPI`(省 IPI),work 還是走 `tctx->task_list`、還是會在 task 返回 user 時跑。DEFER_TASKRUN 是**整條路換掉**(ctx 層 llist、條件喚醒、延到 enter 收)。可以只開 COOP 不開 DEFER。

3. **DEFER_TASKRUN 下不主動 reap 完成卻抱怨「CQE 沒出來」**:DEFER_TASKRUN 的完成**要提交者主動進 kernel 收**(`io_uring_enter` 的 `io_run_local_work`)。如果你的 loop 提交後長時間不 enter、又沒在等 CQE,work 就一直攢在 `work_llist`。這是設計,不是 bug——配合 `TASKRUN_FLAG` 檢查 `IORING_SQ_TASKRUN` 位元來決定何時進來。

4. **在非 submitter task 碰 DEFER_TASKRUN 的 ring**:`__io_run_local_work` 開頭 `WARN_ON_ONCE(ctx->submitter_task != current)`。從別的 thread 去 enter/reap 一個 SINGLE_ISSUER ring 會踩 WARN,行為未定義。ring 要綁死在建立它的 thread 上用。

5. **SQPOLL 還想開 COOP/DEFER**:互斥,`-EINVAL`。SQPOLL 的 kernel thread 自己跑 task_work,IPI/notify 類 flag 對它沒意義。

## 進階:TWA_SIGNAL 到底做了什麼

`TWA_SIGNAL` 是核心 kernel task_work 的通知模式(不是 io_uring 專屬)。`task_work_add(task, work, TWA_SIGNAL)` 做兩件事:把 `work` 掛到 `task->task_works`,然後 `set_notify_signal(task)`——這會設 task 的 `TIF_NOTIFY_SIGNAL` flag,**並且如果 task 正在別的 CPU 上跑,發 IPI 把它踢進 kernel**。task 進 kernel 後,在返回 user space 的路徑上(`exit_to_user_mode_loop`)檢查到 `TIF_NOTIFY_SIGNAL`,就跑 `task_work_run()`,於是 `tctx_task_work` 被呼叫。

`TWA_SIGNAL_NO_IPI` 幾乎一樣,只差**不發 IPI**:只設 flag,等 task 下次自然進 kernel 時才處理。對一個本來就一直在 `io_uring_enter` 的 app,下次進 kernel 很快就到,省下的 IPI 是純賺。這就是為什麼 `COOP_TASKRUN` 對高頻 app 有效、對「提交後長時間在 user space 算東西」的 app 反而會增加完成延遲——取決於你多久進一次 kernel。

## 本章重點整理

- **task_work** 是把「完成收尾」送回提交者 task context 的機制;非回去不可是因為身份/資源、DEFER_TASKRUN 單 producer 假設、以及 atomic context 不能睡。
- 兩條路徑由 `DEFER_TASKRUN` 決定:**normal**(`io_req_normal_work_add`,掛 `tctx->task_list`,`task_work_add` + `TWA_SIGNAL` 通知)vs **local**(`io_req_local_work_add`,掛 `ctx->work_llist`,條件式喚醒,延到提交者下次 enter 收)。
- **COOP_TASKRUN**:把 notify 從 `TWA_SIGNAL`(可能 IPI)換成 `TWA_SIGNAL_NO_IPI`(無 IPI),適合頻繁進 kernel 的 app。
- **TASKRUN_FLAG**:在 `sq_flags` 曝露 `IORING_SQ_TASKRUN`,讓 user 不進 kernel 也知道有 task_work 待跑。
- **DEFER_TASKRUN** 把完成攢在 ctx 層 llist、延到下次 enter 批次處理,省 IPI + 省喚醒;**必須搭 SINGLE_ISSUER**(否則 `-EINVAL`)。
- **SINGLE_ISSUER** 承諾唯一提交者(記 `submitter_task`),是 DEFER_TASKRUN 的前提,也讓 `lockless_cq`(省 completion_lock)成立——三者是協同設計。
- 兩條路都用 `llist`(lock-free)讓多 producer 無鎖推入、單 consumer 批次取出;`SLAB_TYPESAFE_BY_RCU` 保護 lock-free 讀取已可能被釋放的 req。

## 自我檢核

- [ ] 我能說出完成為什麼要送回原 task(至少兩個理由)
- [ ] 我能分辨 normal 和 local 兩條 task_work 路徑,各掛在哪條 list、怎麼通知、何時跑
- [ ] 我能解釋 COOP_TASKRUN 改了什麼(TWA_SIGNAL → NO_IPI)、對什麼 app 有效
- [ ] 我能講清楚為什麼 DEFER_TASKRUN 必須搭 SINGLE_ISSUER,以及這如何連到 Ch 25 的 lockless_cq
- [ ] 我知道 DEFER_TASKRUN 的完成要提交者主動 enter 才收,以及 TASKRUN_FLAG 的用途
- [ ] 我能說出為什麼這些路徑用 llist 而非 list+spinlock

## 延伸閱讀

### 必讀

- **Linux 6.18 原始碼 `io_uring/io_uring.c`** — <https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/io_uring/io_uring.c?h=v6.18>
  - **讀哪裡**:`io_req_normal_work_add`、`io_req_local_work_add`、`tctx_task_work_run`、`__io_run_local_work`、setup 的 flag 檢查
  - **和本章關聯**:本章每個函式名都可在此 grep 到

### 底層機制(repo 內)

- **[../kernel_internals/27-rcu.md](../kernel_internals/27-rcu.md)**
  - **讀哪裡**:RCU grace period 與 `SLAB_TYPESAFE_BY_RCU` 那節
  - **和本章關聯**:`io_req_local_work_add` 讀「可能已被釋放的 req」靠 RCU 型別安全保證,Ch 30 的 `synchronize_rcu()` 與它配對

### LWN

- **[Deferred io_uring task work](https://lwn.net/Articles/902466/)** 與相關 DEFER_TASKRUN / COOP_TASKRUN 報導 — LWN
  - **讀哪裡**:搜 LWN io_uring 系列談 `DEFER_TASKRUN`、`COOP_TASKRUN` 引入動機的那幾篇
  - **和本章關聯**:這些 flag「為什麼加、解決什麼效能問題」的第一手說明

### man page

- **[io_uring_setup(2)](https://man7.org/linux/man-pages/man2/io_uring_setup.2.html)**
  - **讀哪裡**:`IORING_SETUP_COOP_TASKRUN`、`IORING_SETUP_TASKRUN_FLAG`、`IORING_SETUP_DEFER_TASKRUN`、`IORING_SETUP_SINGLE_ISSUER` 的 flag 說明與互斥關係
  - **注意**:man page 把「哪些 flag 要搭哪些」寫得最清楚,查約束比翻 code 快

task_work 和 io-wq 是 io_uring 處理「非當下完成」的兩大後備機制。但還有一類情況我們一直沒碰:一個 socket 操作,此刻沒資料、但 fd **可以 poll**——既不該開 io-wq worker 去空等,也不是立刻能完成。io_uring 對這類有一套極高效的 armed poll 機制,下一章專講。

→ [Ch 28 poll 機制內部:armed poll、double poll](./28-poll-internals.md)
