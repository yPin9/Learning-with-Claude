# Ch 25 — 完成路徑：io_req_complete 與 CQE 填寫

> **目標**：把一個請求從「operation 回傳結果」到「user 在 CQ 環看到 CQE」這條路徑拆到每一行。你會看懂 io_uring 為什麼有**三條**不同的完成路徑（inline deferred、io-wq post、task_work）、`completion_lock` 到底保護什麼、CQ tail 是怎麼推進的、以及 overflow 與喚醒（含 eventfd）各在哪一步發生。

> **環境**：kernel 6.18 原始碼（`git describe` = `v6.18`，commit `7d0a66e`）。本章對照 `io_uring/io_uring.c`、`io_uring/io_uring.h`。

## 為什麼完成路徑比你想的複雜

Ch 24 看完 `io_issue_sqe`：operation handler（`io_read`、`io_send`……）跑完，回一個 `IOU_COMPLETE`。你可能以為接下來就是「把 `res` 寫進 CQ、推 tail、完」。

不是。這裡藏著 io_uring 整個設計裡最反直覺的一段：**填 CQE 的那個 CPU、跟當初提交請求的那個 task，常常不是同一個**。一個 `IORING_OP_READ` 打到慢速磁碟，實際完成是在 block layer 的 IRQ context；一個 `accept` 卡住，會被丟到 io-wq worker thread 去跑。這些 context **不能**直接去碰 CQ 環——有的拿不到該拿的鎖，有的會破壞 DEFER_TASKRUN「只有提交者能碰 CQ」的前提。

所以 io_uring 把「我算完了，`res` 是這個」跟「把這個 `res` 真正寫進 CQ 環」拆成兩件事，中間用一層排程把工作**送回正確的 context** 再填。本章就是講這層。先把三條路徑的骨架記住，後面每一條都對到真實原始碼。

## 先建立直覺：一個 res，三條路回到 CQ

```
          operation handler 算出 req->cqe.res
                        │
          ┌─────────────┼───────────────────────────┐
          ▼             ▼                             ▼
   (A) inline 完成   (B) io-wq 完成            (C) 慢速/跨 context 完成
   同一個 enter 內    worker thread 上          IRQ / 別的 task / poll 就緒
          │             │                             │
   F_COMPLETE_DEFER   io_req_complete_post        req->io_task_work.func
   加進 compl_reqs     (WARN 若非 io-wq)          = io_req_task_complete
          │             │                        丟進 task_work 佇列
          │        直接鎖 CQ 填一筆                     │
          │        或退回 task_work               回到提交者 task 再執行
          ▼             ▼                             ▼
          └──────────► 最終都走 io_fill_cqe_req 把 req->cqe 複製進環 ◄──┘
                        │
              io_commit_cqring: smp_store_release CQ tail
                        │
              io_cqring_wake / eventfd 喚醒等待者
```

三條路（A/B/C）匯流到**同一個**動作：`io_fill_cqe_req()` 把 `req->cqe` 這 16 bytes 複製進 CQ 環的下一個空槽。差別只在「誰、在什麼鎖底下、什麼時候」呼叫它。理解這張圖，本章剩下的就是把每個方框換成函式名。

## 分叉點：`io_issue_sqe` 回來之後

`io_issue_sqe()` 尾端就是第一個分叉（`io_uring/io_uring.c`）：

```c
	ret = __io_issue_sqe(req, issue_flags, def);

	if (ret == IOU_COMPLETE) {
		if (issue_flags & IO_URING_F_COMPLETE_DEFER)
			io_req_complete_defer(req);
		else
			io_req_complete_post(req, issue_flags);

		return 0;
	}
```

一個 flag 決定走哪條：

- **`IO_URING_F_COMPLETE_DEFER`**：這次 issue 是在提交路徑的「主線」上跑的（`io_submit_sqes` → Ch 23），持有 `uring_lock`，可以安全地把完成**攢起來批次處理**。走 `io_req_complete_defer()`（路徑 A）。
- **沒有這個 flag**：這次 issue 是 io-wq worker 在跑（`io_wq_submit_work` → Ch 26），沒有 `uring_lock`。走 `io_req_complete_post()`（路徑 B）。

絕大多數請求走路徑 A——這是 io_uring 快的原因之一：完成不是一筆一筆鎖 CQ，而是**攢一批、一次填、一次推 tail、一次喚醒**。

## 路徑 A：deferred completion —— 攢起來批次填

`io_req_complete_defer()` 本體小到你會懷疑它有沒有做事（`io_uring/io_uring.h`）：

```c
static inline void io_req_complete_defer(struct io_kiocb *req)
	__must_hold(&req->ctx->uring_lock)
{
	struct io_submit_state *state = &req->ctx->submit_state;

	lockdep_assert_held(&req->ctx->uring_lock);

	wq_list_add_tail(&req->comp_list, &state->compl_reqs);
}
```

它只是把 `req` 掛進 `ctx->submit_state.compl_reqs` 這條鏈。真正填 CQE 的動作延到**這一輪提交結束**才一次做完。`__must_hold(&uring_lock)` 不是裝飾——因為沒鎖 CQ，靠的是「`uring_lock` 保證此刻只有我一個 task 在動 `submit_state`」。

### 批次 flush：`__io_submit_flush_completions`

這一輪 `io_submit_sqes` 跑完，呼叫 `io_submit_flush_completions()`，它判斷鏈非空就進 `__io_submit_flush_completions()`（`io_uring/io_uring.c`）：

```c
void __io_submit_flush_completions(struct io_ring_ctx *ctx)
	__must_hold(&ctx->uring_lock)
{
	struct io_submit_state *state = &ctx->submit_state;
	struct io_wq_work_node *node;

	__io_cq_lock(ctx);
	__wq_list_for_each(node, &state->compl_reqs) {
		struct io_kiocb *req = container_of(node, struct io_kiocb,
					    comp_list);

		/*
		 * Requests marked with REQUEUE should not post a CQE, they
		 * will go through the io-wq retry machinery and post one
		 * later.
		 */
		if (!(req->flags & (REQ_F_CQE_SKIP | REQ_F_REISSUE)) &&
		    unlikely(!io_fill_cqe_req(ctx, req))) {
			if (ctx->lockless_cq)
				io_cqe_overflow(ctx, &req->cqe, &req->big_cqe);
			else
				io_cqe_overflow_locked(ctx, &req->cqe, &req->big_cqe);
		}
	}
	__io_cq_unlock_post(ctx);

	if (!wq_list_empty(&state->compl_reqs)) {
		io_free_batch_list(ctx, state->compl_reqs.first);
		INIT_WQ_LIST(&state->compl_reqs);
	}

	if (unlikely(ctx->drain_active))
		io_queue_deferred(ctx);

	ctx->submit_state.cq_flush = false;
}
```

這一段把整章的動詞都串起來了：

1. `__io_cq_lock(ctx)` — 鎖 CQ（底下會看到它可能根本不鎖）。
2. 走訪整條 `compl_reqs`，每個請求 `io_fill_cqe_req()` 把 `req->cqe` 寫進環。`REQ_F_CQE_SKIP`（不需 CQE 的請求，例如 link 中間被跳過的）和 `REQ_F_REISSUE`（要退回 io-wq 重跑的）跳過不填。
3. 填不進去（環滿了）就走 overflow，掛進備援鏈，之後再補（下節）。
4. `__io_cq_unlock_post(ctx)` — **推 tail + 解鎖 + 喚醒**，一次做完（重點，下面拆）。
5. `io_free_batch_list()` 把這批請求物件批次回收進 slab cache（這就是為什麼要攢著：連釋放都批次化）。

## 匯流點：`io_fill_cqe_req` 真正寫 CQE

三條路最後都走到這個 inline（`io_uring/io_uring.h`）：

```c
static __always_inline bool io_fill_cqe_req(struct io_ring_ctx *ctx,
					    struct io_kiocb *req)
{
	bool is_cqe32 = req->cqe.flags & IORING_CQE_F_32;
	struct io_uring_cqe *cqe;

	/*
	 * If we can't get a cq entry, userspace overflowed the submission
	 * (by quite a lot).
	 */
	if (unlikely(!io_get_cqe(ctx, &cqe, is_cqe32)))
		return false;

	memcpy(cqe, &req->cqe, sizeof(*cqe));
	if (ctx->flags & IORING_SETUP_CQE32 || is_cqe32) {
		memcpy(cqe->big_cqe, &req->big_cqe, sizeof(*cqe));
		memset(&req->big_cqe, 0, sizeof(req->big_cqe));
	}

	if (trace_io_uring_complete_enabled())
		trace_io_uring_complete(req->ctx, req, cqe);
	return true;
}
```

兩個關鍵：

- **`io_get_cqe()`** 回傳 CQ 環裡下一個可寫槽的指標（`&rings->cqes[tail & mask]` 的本質），同時把 `ctx->cached_cq_tail` 往前算。它失敗（回 `false`）唯一的原因就是 **CQ 滿了**——user 收 CQE 太慢，head 沒跟上。
- 填寫就是一個 `memcpy`：把 `req->cqe`（operation handler 早就用 `io_req_set_res()` 填好的 `user_data`/`res`/`flags`）整包複製進環。CQE32（`IORING_SETUP_CQE32`，附帶 `extra1/extra2`）再多複製一截。

注意：`req->cqe` 在 operation handler 裡就填好了。`io_req_set_res(req, res, cflags)`（`io_uring.h`）只是 `req->cqe.res = res; req->cqe.flags = cflags;`。所以到了這裡，「結果」早已在 `req` 身上，`io_fill_cqe_req` 只負責**搬進共享環**。

## CQ tail 推進：唯一一次 store-release

填完 CQE，要讓 user 看得到，就得推進 CQ tail。這件事只在一個地方做——`io_commit_cqring()`（`io_uring/io_uring.h`）：

```c
static inline void io_commit_cqring(struct io_ring_ctx *ctx)
{
	/* order cqe stores with ring update */
	smp_store_release(&ctx->rings->cq.tail, ctx->cached_cq_tail);
}
```

這一行就是 Ch 2 你手刻 raw ring 時那個「kernel 推 CQ tail」的真身。kernel 填 CQE 時先只動 `ctx->cached_cq_tail`（kernel 私有的影子計數器），等整批填完，才用 **`smp_store_release`** 把它一次發佈到共享的 `rings->cq.tail`。

`smp_store_release` 不是可有可無：它保證「user 一旦讀到新的 tail，就一定也看到前面那些 `memcpy` 進環的 CQE 內容」。對應 user 端收 CQE 前的 acquire load（Ch 2、Ch 5）。**這是 CQ 側 producer/consumer 正確性的核心屏障。**

## `completion_lock` 到底保護什麼——以及為什麼常常不鎖

你會看到兩組鎖函式，差別很關鍵（`io_uring/io_uring.c`）：

```c
static inline void __io_cq_lock(struct io_ring_ctx *ctx)
{
	if (!ctx->lockless_cq)
		spin_lock(&ctx->completion_lock);
}

static inline void __io_cq_unlock_post(struct io_ring_ctx *ctx)
{
	io_commit_cqring(ctx);
	if (!ctx->task_complete) {
		if (!ctx->lockless_cq)
			spin_unlock(&ctx->completion_lock);
		/* IOPOLL rings only need to wake up if it's also SQPOLL */
		if (!ctx->syscall_iopoll)
			io_cqring_wake(ctx);
	}
	io_commit_cqring_flush(ctx);
}
```

重點是 `ctx->lockless_cq`。它在 setup 時這樣決定（`io_uring/io_uring.c`，約 3813 行）：

```c
	if ((ctx->flags & IORING_SETUP_DEFER_TASKRUN) &&
	    !(ctx->flags & IORING_SETUP_IOPOLL) &&
	    !(ctx->flags & IORING_SETUP_SQPOLL))
		ctx->task_complete = true;

	if (ctx->task_complete || (ctx->flags & IORING_SETUP_IOPOLL))
		ctx->lockless_cq = true;
```

`completion_lock` 保護的是「同時有多個 context 想往 CQ 環填 CQE / 動 overflow list」這件事。但 io_uring 發現：如果設定了 **`DEFER_TASKRUN`**（Ch 27），**只有提交者那一個 task 會填 CQE**——所有慢速完成都被 task_work 送回這個 task 才填。既然 CQ 的 producer 只有一個，**就不需要鎖**。`lockless_cq` 為真時 `__io_cq_lock` 直接空轉。

這是 DEFER_TASKRUN 快的另一個理由：不只省 IPI，連 `completion_lock` 這把在高併發下會成為熱點的 spinlock 都省掉了。沒設 DEFER_TASKRUN 的普通 ring，`completion_lock` 照拿——因為 io-wq worker、IRQ 完成、提交主線可能同時想碰 CQ。

## 喚醒等待者：`io_cqring_wake` 與 eventfd

填好、推完 tail，最後要叫醒在 `io_uring_enter(..., GETEVENTS)` 裡睡著等 CQE 的人。`io_cqring_wake()`（`io_uring/io_uring.h`）：

```c
static inline void io_cqring_wake(struct io_ring_ctx *ctx)
{
	/*
	 * Trigger waitqueue handler on all waiters on our waitqueue. ...
	 */
	__io_wq_wake(&ctx->cq_wait);
}
```

它喚醒掛在 `ctx->cq_wait` 上的 waiter。`__io_wq_wake` 用 `EPOLL_URING_WAKE | EPOLLIN` 當 wakeup key——這個 key 不只喚醒自己的 waiter，還讓「如果有 epoll/eventfd 遞迴依賴到這個 ring」的 multishot poll 知道該在此處斷鏈（避免無窮喚醒迴圈，細節 Ch 28）。

eventfd 走另一條岔路。`__io_cq_unlock_post` 尾端呼叫 `io_commit_cqring_flush()`（`io_uring/io_uring.h`）：

```c
static inline void io_commit_cqring_flush(struct io_ring_ctx *ctx)
{
	if (unlikely(ctx->off_timeout_used ||
		     ctx->has_evfd || ctx->poll_activated))
		__io_commit_cqring_flush(ctx);
}
```

只有在真的註冊了 eventfd（`has_evfd`）、用了 offset timeout、或啟用了 poll ring 時才進慢路。`__io_commit_cqring_flush()`（`io_uring/io_uring.c`）：

```c
void __io_commit_cqring_flush(struct io_ring_ctx *ctx)
{
	if (ctx->poll_activated)
		io_poll_wq_wake(ctx);
	if (ctx->off_timeout_used)
		io_flush_timeouts(ctx);
	if (ctx->has_evfd)
		io_eventfd_signal(ctx, true);
}
```

`io_eventfd_signal()` 往註冊的 eventfd 寫一下，讓「用 epoll 盯著那個 eventfd」的程式被喚醒——這就是把 io_uring 完成事件接進傳統 epoll loop 的機制（應用面見 Ch 18）。這條喚醒路徑**和填 CQE 是分開的兩件事**：CQE 永遠先進環，eventfd 只是額外敲一下門。

## 路徑 B：`io_req_complete_post` —— io-wq 的完成

回到分叉點。io-wq worker 跑完的請求沒有 `uring_lock`，不能走 defer，改走這條（`io_uring/io_uring.c`）：

```c
static void io_req_complete_post(struct io_kiocb *req, unsigned issue_flags)
{
	struct io_ring_ctx *ctx = req->ctx;
	bool completed = true;

	/*
	 * All execution paths but io-wq use the deferred completions by
	 * passing IO_URING_F_COMPLETE_DEFER and thus should not end up here.
	 */
	if (WARN_ON_ONCE(!(issue_flags & IO_URING_F_IOWQ)))
		return;

	/*
	 * Handle special CQ sync cases via task_work. DEFER_TASKRUN requires
	 * the submitter task context, IOPOLL protects with uring_lock.
	 */
	if (ctx->lockless_cq || (req->flags & REQ_F_REISSUE)) {
defer_complete:
		req->io_task_work.func = io_req_task_complete;
		io_req_task_work_add(req);
		return;
	}

	io_cq_lock(ctx);
	if (!(req->flags & REQ_F_CQE_SKIP))
		completed = io_fill_cqe_req(ctx, req);
	io_cq_unlock_post(ctx);

	if (!completed)
		goto defer_complete;

	/*
	 * We don't free the request here because we know it's called from
	 * io-wq only, which holds a reference, so it cannot be the last put.
	 */
	req_ref_put(req);
}
```

兩種情況：

- **普通 ring**（非 lockless_cq）：io-wq worker 直接 `io_cq_lock()`（這裡是無條件的 `spin_lock(&completion_lock)`，因為 worker 不是提交者 task，必須靠鎖排他）→ `io_fill_cqe_req` → `io_cq_unlock_post`（推 tail + 解鎖 + 喚醒）。填好直接在 worker context 完成。
- **`lockless_cq`（DEFER_TASKRUN / IOPOLL）或要 reissue**：worker **不准**自己碰 CQ——會違反「只有提交者能填」。於是把 `req->io_task_work.func` 設成 `io_req_task_complete`，丟進 task_work（路徑 C），送回提交者 task 再填。

`WARN_ON_ONCE(!(issue_flags & IO_URING_F_IOWQ))` 這行是護欄：這函式**只該**從 io-wq 進來。任何別的 context 跑到這裡就是 bug。

## 路徑 C：task_work completion —— 送回正確的 task

慢速完成（block IRQ、poll 就緒、io-wq 退回）共用一個手法：不在當下 context 填 CQE，而是設好 `req->io_task_work.func` 再排進 task_work，等回到提交者 task 才執行。對「最終完成」而言，那個 func 就是 `io_req_task_complete()`（`io_uring/io_uring.c`）：

```c
void io_req_task_complete(struct io_kiocb *req, io_tw_token_t tw)
{
	io_req_complete_defer(req);
}
```

繞了一圈，它**又回到路徑 A**的 `io_req_complete_defer`——因為 task_work 是在提交者 task、持有 `uring_lock` 的情況下批次執行的（Ch 27 詳述），此時掛進 `compl_reqs`、等 task_work 這一輪結束再 `__io_submit_flush_completions` 一次填，和提交主線的 deferred completion 完全同構。

這就是為什麼說「三條路匯流成一條」：B 在特殊情況退化成 C，C 又匯回 A。真正把 CQE 搬進環的永遠是 `io_fill_cqe_req` + `io_commit_cqring`，差別只是**誰在什麼鎖底下呼叫它**。task_work 的機制本身（怎麼排、`TWA_SIGNAL`、怎麼回到 task、DEFER_TASKRUN 怎麼把它延到下次 enter）是 Ch 27 的主題。

## CQ overflow：環滿了怎麼辦

`io_fill_cqe_req` 回 `false`（`io_get_cqe` 拿不到槽）代表 CQ 環滿了。io_uring 不丟結果，改掛進一條 kernel 側的備援鏈（`io_uring/io_uring.c`）：

```c
static __cold void io_cqe_overflow(struct io_ring_ctx *ctx, struct io_cqe *cqe,
				   struct io_big_cqe *big_cqe)
{
	struct io_overflow_cqe *ocqe;

	ocqe = io_alloc_ocqe(ctx, cqe, big_cqe, GFP_KERNEL);
	spin_lock(&ctx->completion_lock);
	io_cqring_add_overflow(ctx, ocqe);
	spin_unlock(&ctx->completion_lock);
}
```

每個溢出的 CQE 另外 `kmalloc` 一個 `io_overflow_cqe`，掛進 `ctx->cq_overflow_list`，並在 `rings->sq_flags` 設 `IORING_SQ_CQ_OVERFLOW` 讓 user 察覺（應用面 Ch 8）。等 user 收掉一些 CQE、環騰出空間，下次進 kernel 時 `io_cqring_do_overflow_flush()` 把備援鏈倒回環裡（`io_uring/io_uring.c`）：

```c
static void io_cqring_do_overflow_flush(struct io_ring_ctx *ctx)
{
	mutex_lock(&ctx->uring_lock);
	__io_cqring_overflow_flush(ctx, false);
	mutex_unlock(&ctx->uring_lock);
}
```

`__io_cqring_overflow_flush` 走訪 `cq_overflow_list`，`io_get_cqe_overflow` 把每筆倒回環、`kfree(ocqe)`，倒完清掉 `IORING_SQ_CQ_OVERFLOW`。它還有一段 `need_resched()` 的保護，是給 syzbot 那種「故意溢出幾百萬筆」的病態輸入用的，正常程式碰不到。

overflow 的重點對後面 Part 4 很關鍵：**溢出路徑要 `kmalloc`、要動 list、要在 `completion_lock` 下操作**——比正常填 CQE 複雜得多，歷史上出過不少 bug。先記住它的存在。

## 底層機制：為什麼完成要「攢批」

把前面串起來看一次 fast path（最常見的 DEFER_TASKRUN ring，一次提交 N 個很快完成的請求）：

1. `io_submit_sqes` 持 `uring_lock`，逐個 `io_issue_sqe`，每個帶 `F_COMPLETE_DEFER`。
2. 每個完成只做 `wq_list_add_tail(&req->comp_list, &compl_reqs)`——**零鎖、零屏障、零喚醒**。
3. 一輪結束 `__io_submit_flush_completions`：因為 `lockless_cq`，`__io_cq_lock` 空轉；一個迴圈 `memcpy` N 筆進環；**一次** `smp_store_release` 推 tail；**一次**喚醒；`io_free_batch_list` 批次回收 N 個 req。

N 個請求，只有一次 release barrier、一次 wake、一次 slab 批次操作。對比「每個請求各自鎖 CQ、各自推 tail、各自喚醒」的 naive 做法,這是數量級的差距。io_uring 把完成路徑的所有「貴」動作（屏障、鎖、喚醒、記憶體回收）全部批次化,靠的就是 `compl_reqs` 這條鏈加 `uring_lock` 的排他性。

## 對比與取捨

| 面向 | deferred（路徑 A） | io-wq post（路徑 B） | task_work（路徑 C） |
|---|---|---|---|
| 觸發 | 提交主線 inline 完成 | io-wq worker 完成 | 慢速/跨 context 完成 |
| 關鍵 flag | `IO_URING_F_COMPLETE_DEFER` | `IO_URING_F_IOWQ` | `req->io_task_work.func` |
| 填 CQE 的 context | 提交者 task | io-wq worker（或退回 C） | 提交者 task |
| 鎖 CQ？ | `lockless_cq` 則否 | 普通 ring 要 `completion_lock` | 退化回 A，同 A |
| 批次？ | 是（整串一次填） | 否（一筆） | 是（匯回 A） |
| 誰會走 | 絕大多數快請求 | 阻塞類 op（Ch 26） | poll 就緒、IRQ、reissue |

取捨的核心：**正確性 vs 速度**。能攢批就攢批（最快），但只有「單一 producer + 持 uring_lock」時才安全；做不到就退回鎖 CQ（io-wq 普通 ring）或送回正確 task（DEFER_TASKRUN）。`lockless_cq` 這個 bool 就是這個取捨的開關。

## 踩雷集錦

1. **以為 `res` 是在完成路徑算出來的**：不是。`res` 在 operation handler 裡就用 `io_req_set_res()` 填進 `req->cqe` 了。完成路徑只負責把 `req->cqe` **搬進共享環**（`io_fill_cqe_req` 的 `memcpy`），不做任何計算。

2. **把 `completion_lock` 當成「填 CQE 必鎖」**：DEFER_TASKRUN 的 `lockless_cq` ring **完全不拿** `completion_lock` 來填 CQE（`__io_cq_lock` 空轉）。它靠「只有提交者 task 填」來保證排他。拿著舊 kernel 的印象讀 6.18 會對不上。

3. **以為 CQ tail 每填一筆就推一次**：kernel 填 CQE 只動私有的 `ctx->cached_cq_tail`，**整批填完才** `io_commit_cqring` 用一次 `smp_store_release` 發佈到 `rings->cq.tail`。中途 user 看不到半成品。

4. **把 eventfd signal 當成 CQE 投遞的一部分**：eventfd（`io_eventfd_signal`）走的是 `io_commit_cqring_flush` 這條**另外**的慢路，只有 `has_evfd` 才觸發。CQE 早就進環了，eventfd 只是額外敲門；兩者先後、失敗與否互不影響。

5. **在 io-wq worker 直接填 DEFER_TASKRUN ring 的 CQE**：`io_req_complete_post` 看到 `lockless_cq` 會**拒絕**自己填，改丟 task_work。如果你讀 code 時以為 worker 會當場填，就會對不上——違反「單一 producer」正是這類 ring 歷史 UAF 的來源之一（Part 4 會回來）。

## 進階：`io_req_post_cqe` 與 multishot 的額外 CQE

到目前講的都是「請求完成 → 一筆 CQE」。但 multishot（Ch 13）一個請求會吐**多筆** CQE。那條路走 `io_req_post_cqe()`（`io_uring/io_uring.c`），它跟 `io_fill_cqe_req` 的差別在：它填的是 `io_fill_cqe_aux`（直接寫 `user_data`/`res`/`flags`，不搬整個 `req->cqe`），而且會先確認：

```c
	/*
	 * If multishot has already posted deferred completions, ensure that
	 * those are flushed first before posting this one. If not, CQEs
	 * could get reordered.
	 */
	if (!wq_list_empty(&ctx->submit_state.compl_reqs))
		__io_submit_flush_completions(ctx);
```

先把 `compl_reqs` 裡攢著的 deferred 完成 flush 掉，才 post 這筆 aux CQE——否則同一個請求的多筆 CQE 可能**亂序**出現在環裡。這條 ordering 細節在 Ch 28（multishot poll）會再遇到，這裡先知道「multishot 的額外 CQE 走 aux 路、而且要小心和 deferred 批次的順序」。

## 本章重點整理

- 一個請求的 `res` 在 operation handler 就填進 `req->cqe` 了；**完成路徑只負責把它搬進共享 CQ 環**，核心動作是 `io_fill_cqe_req()` 的 `memcpy` + `io_commit_cqring()` 的 `smp_store_release` 推 tail。
- 三條完成路徑：**(A) deferred**（`io_req_complete_defer`，攢進 `compl_reqs` 批次填，最快，走提交主線）、**(B) io-wq post**（`io_req_complete_post`，worker 直接鎖 CQ 填，或退回 C）、**(C) task_work**（`io_req_task_complete`，送回提交者 task，又匯回 A）。
- `completion_lock` 保護多 producer 併發填 CQE；但 **DEFER_TASKRUN 的 `lockless_cq` ring 根本不拿它**，靠「單一提交者 task 填」排他。這是省鎖的關鍵。
- CQ tail 只在 `io_commit_cqring` 用一次 `smp_store_release` 推進，對應 user 端的 acquire load。
- overflow（環滿）走 `io_cqe_overflow` 掛備援鏈 + 設 `IORING_SQ_CQ_OVERFLOW`，之後 `io_cqring_do_overflow_flush` 倒回環。
- 喚醒分兩路：`io_cqring_wake`（喚醒 enter 等待者）和 `io_commit_cqring_flush → io_eventfd_signal`（額外敲 eventfd），後者只在 `has_evfd` 時走。
- 完成路徑快的本質是**批次化**：N 個請求只付一次屏障、一次喚醒、一次 slab 回收。

## 自我檢核

- [ ] 我能說出一個請求完成後，`res` 是從哪裡來、`io_fill_cqe_req` 做了什麼、CQ tail 在哪一行被推進
- [ ] 我能解釋 `IO_URING_F_COMPLETE_DEFER` 有無的兩條分叉，以及各走哪個函式
- [ ] 我能說出 `lockless_cq` 為真時 `completion_lock` 發生什麼事，以及為什麼這樣是安全的
- [ ] 我能解釋「三條完成路徑最後匯流成一條」是什麼意思
- [ ] 我知道 eventfd 的喚醒和填 CQE 是分開的兩件事，各在哪個函式
- [ ] 我能說出 CQ overflow 發生在 `io_fill_cqe_req` 的哪個回傳值、結果被掛去哪

## 延伸閱讀

### 必讀

- **Linux 6.18 原始碼 `io_uring/io_uring.c`** — <https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/io_uring/io_uring.c?h=v6.18>
  - **讀哪裡**：`__io_submit_flush_completions`、`io_req_complete_post`、`__io_cq_unlock_post` 三個函式；對照本章把三條路徑走一遍
  - **和本章關聯**：本章每個函式名都可在此 grep 到

### 設計文件

- **[Efficient IO with io_uring](https://kernel.dk/io_uring.pdf)** — Jens Axboe
  - **讀哪裡**：completion queue 與 memory ordering 兩節
  - **和本章關聯**：`io_commit_cqring` 的 `smp_store_release` 正是這份文件描述的 CQ producer 屏障

### LWN

- **[The rapid growth of io_uring](https://lwn.net/Articles/810414/)** 與其後續 DEFER_TASKRUN 相關報導 — Jonathan Corbet
  - **讀哪裡**：搜 LWN io_uring 系列中談 `IORING_SETUP_DEFER_TASKRUN` 的那幾篇
  - **和本章關聯**：解釋 `lockless_cq` / `task_complete` 為何能成立（單一提交者假設），Ch 27 會深入

### commit / 歷史

- **io_uring 的 `lockless_cq` 與 deferred completion 演進** — 在 kernel git log 搜 `io_req_complete_defer`、`lockless_cq`、`task_complete` 的引入 commit
  - **讀哪裡**：commit message 解釋了「為什麼能省 completion_lock」的 reasoning
  - **注意**：6.18 的完成路徑和 5.x 差異很大，看舊 writeup 要對版本

把「完成」拆完了，但我們一直把一個名詞當黑盒帶過：「阻塞類 op 會被丟到 io-wq」。下一章就打開這個 worker pool——它是 io_uring 處理「會卡住的 I/O」的後備機制，也是 Part 4 一整類 race 的舞台。

→ [Ch 26 async 的祕密：io-wq worker pool](./26-io-wq.md)
