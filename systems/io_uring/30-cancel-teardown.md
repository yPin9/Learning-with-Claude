# Ch 30 — cancel 與 teardown：引用計數、ring 拆除

> **目標**：搞懂一個 io_uring 實例怎麼**乾淨地消失**。你會看懂請求取消（`io_try_cancel` 怎麼在各子系統間輪詢找到目標）、process exit / `close(ring_fd)` 時的清理（`io_uring_cancel_generic`、`io_ring_exit_work`、`io_ring_ctx_free`）、`percpu_ref` 怎麼從「kill」到「歸零」驅動整個拆除、以及**為什麼 teardown 的順序如此要命**——順序錯一步就是 Part 4 一整類 UAF 的溫床。這一章是 Part 3 的收束，也是攻擊面的直接前奏。

> **環境**：kernel 6.18 原始碼（`v6.18`，commit `7d0a66e`）。對照 `io_uring/cancel.c`、`io_uring/io_uring.c`。

## 為什麼 teardown 這麼難

前面 11 章鋪陳的所有東西——in-flight 請求、io-wq worker、掛在 waitqueue 上的 armed poll、task_work 佇列、registered 資源、CQ overflow 鏈——在 ring 要拆掉的那一刻，**全都還可能活著**。teardown 的任務是把這一切以**正確順序**拆乾淨，而難點在於：**這些東西彼此持有引用，而且散落在不同 context（io-wq thread、別的 CPU、RCU 讀區）裡還在跑**。

想像 `close(ring_fd)` 的那一刻：

- 有 3 個請求正在 io-wq worker 上跑磁碟 I/O（會睡著）。
- 有 5000 個 armed poll 掛在各 socket 的 waitqueue 上等。
- 有一批 task_work 排在 `work_llist` 上還沒執行。
- registered 的 100 個 file、20 塊 buffer 的 node 還有 in-flight 請求引用著。

你不能直接 `kfree(ctx)`——那 5000 個 poll 的 callback 還指著 `ctx`，worker 跑完要填 CQE 還要碰 `ctx->rings`。**必須先讓所有這些「還指著 ctx 的東西」停下來、放手，`ctx` 才能死**。teardown 就是這個「等所有人放手」的精密編排。順序錯了，就會有人在 `ctx` 已釋放後還去碰它——UAF。

## 先建立直覺：kill → 等歸零 → 拆

io_uring 用 `percpu_ref`（per-CPU 引用計數）當整個 ctx 生死的總開關。拆除分三階段：

```
   close(ring_fd) / process exit
            │
            ▼
   io_ring_ctx_wait_and_kill
            │ percpu_ref_kill(&ctx->refs)   ← 階段 1：封死，不准再拿新引用
            │ queue_work(iou_wq, exit_work)
            ▼
   io_ring_exit_work（在 kworker 上，可以慢慢來）
            │ 迴圈：io_uring_try_cancel_requests   ← 階段 2：取消所有 in-flight
            │       取消 poll、timeout、io-wq work、task_work……
            │       cond_resched，直到沒有 in-flight
            │ wait_for_completion(&ctx->ref_comp)  ← 等 percpu_ref 真正歸零
            │ synchronize_rcu()（若 DEFER_TASKRUN） ← 等 RCU 讀區結束
            ▼
   io_ring_ctx_free                           ← 階段 3：refs 歸零，真正拆
            │ unregister buffers/files（放 rsrc node）
            │ 釋放 rings、caches、hash、cancel_table……
            │ percpu_ref_exit
            │ kfree(ctx)
            ▼
          ctx 消失
```

三階段的分工很關鍵：

- **階段 1（kill）** 在 `close` 的 syscall context 同步做，但只做「封死 + 排 work」，很快返回。
- **階段 2（取消 + 等歸零）** 丟到 kworker（`iou_wq`）上做，因為它可能要等很久（等 io-wq worker 跑完磁碟 I/O、等 poll 都摘除）。用 `percpu_ref` 歸零當「所有人都放手了」的信號。
- **階段 3（free）** 只有在 refs 確定歸零後才做——此時保證沒有任何 in-flight 引用，拆才安全。

## 請求取消：`io_try_cancel` 的輪詢

先看單個請求的取消，再看整體 teardown。取消一個 in-flight 請求（可能來自 `IORING_OP_ASYNC_CANCEL`，也可能來自 teardown）走 `io_try_cancel`（`io_uring/cancel.c`）：

```c
int io_try_cancel(struct io_uring_task *tctx, struct io_cancel_data *cd,
		  unsigned issue_flags)
{
	struct io_ring_ctx *ctx = cd->ctx;
	int ret;

	WARN_ON_ONCE(!io_wq_current_is_worker() && tctx != current->io_uring);

	ret = io_async_cancel_one(tctx, cd);
	/*
	 * Fall-through even for -EALREADY, as we may have poll armed
	 * that need unarming.
	 */
	if (!ret)
		return 0;

	ret = io_poll_cancel(ctx, cd, issue_flags);
	if (ret != -ENOENT)
		return ret;

	ret = io_waitid_cancel(ctx, cd, issue_flags);
	if (ret != -ENOENT)
		return ret;

	ret = io_futex_cancel(ctx, cd, issue_flags);
	if (ret != -ENOENT)
		return ret;

	spin_lock(&ctx->completion_lock);
	if (!(cd->flags & IORING_ASYNC_CANCEL_FD))
		ret = io_timeout_cancel(ctx, cd);
	spin_unlock(&ctx->completion_lock);

	return ret;
}
```

這個「輪詢各子系統」的結構，正好把 Part 3 走過的機制全點名一遍：一個請求此刻**可能卡在哪裡**？

1. **io-wq**（`io_async_cancel_one`）：在 worker 上跑/排隊？
2. **poll**（`io_poll_cancel`）：掛在某 socket 的 waitqueue 上 armed（Ch 28）？
3. **waitid / futex**：掛在那些子系統的等待結構上？
4. **timeout**（`io_timeout_cancel`）：掛在 timeout 鏈上（需 `completion_lock`）？

每個子系統回 `-ENOENT`（「不在我這」）就試下一個，找到就取消。注意那個 fall-through 註解：即使 io-wq 回「已經在跑了（`-EALREADY`）」，也要繼續檢查 poll——因為一個請求可能同時 io-wq 有份、poll 也 armed（例如 multishot 的複雜狀態），都得清。

這個輪詢揭示 teardown 難的根源：**一個請求的「位置」不是單一的**，它可能同時掛在多個子系統的資料結構上，取消得把每一處都摘乾淨。

## teardown 入口：`io_ring_ctx_wait_and_kill`

`close(ring_fd)` 觸發 file operations 的 `->release`，也就是 `io_uring_release` → `io_ring_ctx_wait_and_kill`（`io_uring/io_uring.c`）：

```c
static __cold void io_ring_ctx_wait_and_kill(struct io_ring_ctx *ctx)
{
	unsigned long index;
	struct creds *creds;

	mutex_lock(&ctx->uring_lock);
	percpu_ref_kill(&ctx->refs);
	xa_for_each(&ctx->personalities, index, creds)
		io_unregister_personality(ctx, index);
	mutex_unlock(&ctx->uring_lock);

	flush_delayed_work(&ctx->fallback_work);

	INIT_WORK(&ctx->exit_work, io_ring_exit_work);
	queue_work(iou_wq, &ctx->exit_work);
}
```

`percpu_ref_kill(&ctx->refs)` 是階段 1 的核心。`percpu_ref` 平常以 per-CPU 計數器運作（拿/放引用極快、無跨 CPU 同步）。`kill` 把它切換到「原子模式」並標記死亡：**從此不准再拿新引用**（`percpu_ref_tryget` 會失敗），而且開始追蹤「現有引用何時全部歸零」。歸零時會呼叫當初註冊的 release callback（`io_ring_ctx_ref_free`，它 `complete(&ctx->ref_comp)`）。

然後排 `io_ring_exit_work` 到 `iou_wq`（一個專用的 workqueue，避免大量 ring 同時退出時 kworker 爆炸），`close` 就返回了。剩下的慢活在 kworker 上做。

## 等待與取消：`io_ring_exit_work`

階段 2 的主體（`io_uring/io_uring.c`，擷取重點）：

```c
static __cold void io_ring_exit_work(struct work_struct *work)
{
	struct io_ring_ctx *ctx = container_of(work, struct io_ring_ctx, exit_work);
	unsigned long timeout = jiffies + HZ * 60 * 5;
	unsigned long interval = HZ / 20;
	...
	do {
		if (test_bit(IO_CHECK_CQ_OVERFLOW_BIT, &ctx->check_cq)) {
			mutex_lock(&ctx->uring_lock);
			io_cqring_overflow_kill(ctx);
			mutex_unlock(&ctx->uring_lock);
		}
		...
		if (ctx->flags & IORING_SETUP_DEFER_TASKRUN)
			io_move_task_work_from_local(ctx);

		/* The SQPOLL thread never reaches this path */
		while (io_uring_try_cancel_requests(ctx, NULL, true, false))
			cond_resched();

		if (ctx->sq_data) {
			... io_wq_cancel_cb(... io_cancel_ctx_cb, ctx, true);
		}

		io_req_caches_free(ctx);
		...
	} while (!wait_for_completion_interruptible_timeout(&ctx->ref_comp, interval));

	...
	mutex_lock(&ctx->uring_lock);
	while (!list_empty(&ctx->tctx_list)) {
		... task_work_add(node->task, &exit.task_work, TWA_SIGNAL);
		... wait_for_completion_interruptible(&exit.completion);
	}
	mutex_unlock(&ctx->uring_lock);
	spin_lock(&ctx->completion_lock);
	spin_unlock(&ctx->completion_lock);

	/* pairs with RCU read section in io_req_local_work_add() */
	if (ctx->flags & IORING_SETUP_DEFER_TASKRUN)
		synchronize_rcu();

	io_ring_ctx_free(ctx);
}
```

把它拆開看，每一步都在清一類東西：

1. **`io_cqring_overflow_kill`**：把 CQ overflow 鏈（Ch 25）的 `io_overflow_cqe` 全部清掉（`kfree`），不然它們是漏的記憶體。
2. **`io_move_task_work_from_local`**（DEFER_TASKRUN）：把 `work_llist` 上還沒跑的 local task_work 搬走、fallback 處理——不能留著沒人跑。
3. **`io_uring_try_cancel_requests` 迴圈**：反覆取消所有 in-flight 請求（內部對每個請求走類似 `io_try_cancel` 的輪詢），`cond_resched` 讓出 CPU，直到一輪下來沒有任何請求可取消。
4. **io-wq 取消**：若有 SQPOLL，取消掛在 sqpoll worker 上的 work。
5. **`wait_for_completion_interruptible_timeout(&ctx->ref_comp, interval)`**：這是迴圈條件——等 `percpu_ref` 歸零（透過 `ref_comp` 這個 completion）。還沒歸零（timeout 到了還有引用）就再跑一輪取消。有個 5 分鐘的 `timeout` 保護，超過會 WARN（代表有請求卡死，是 bug）。

迴圈出來，代表 **in-flight 請求全部完成/取消、`percpu_ref` 歸零**。接著：

6. **tctx_list 迴圈**：對每個曾用過這個 ring 的 task，用 `task_work_add(TWA_SIGNAL)` 送一個 exit work 過去，讓它清掉自己 tctx 裡對這個 ctx 的記錄（`io_tctx_exit_cb`）。等每個都做完。
7. **`spin_lock(&completion_lock); spin_unlock(...)`** 這對空的鎖/解鎖不是廢碼——它是一個**記憶體屏障式的同步點**，確保「任何此刻正持 `completion_lock` 在收尾的 context 都跑完了」才往下走。
8. **`synchronize_rcu()`**（DEFER_TASKRUN）：這就是 Ch 27、Ch 29 提過的那個配對。`io_req_local_work_add` 在 `guard(rcu)()` 讀區裡會碰 `ctx`；teardown 必須 `synchronize_rcu()` **等所有這種 RCU 讀區結束**，才能保證沒有人還在 RCU 保護下碰 `ctx`，然後才 free。少了它就是經典 RCU UAF。

全部清完，才進階段 3。

## 真正拆除：`io_ring_ctx_free`

階段 3（`io_uring/io_uring.c`）：

```c
static __cold void io_ring_ctx_free(struct io_ring_ctx *ctx)
{
	io_sq_thread_finish(ctx);

	mutex_lock(&ctx->uring_lock);
	io_sqe_buffers_unregister(ctx);
	io_sqe_files_unregister(ctx);
	io_unregister_zcrx_ifqs(ctx);
	io_cqring_overflow_kill(ctx);
	io_eventfd_unregister(ctx);
	io_free_alloc_caches(ctx);
	io_destroy_buffers(ctx);
	io_free_region(ctx, &ctx->param_region);
	mutex_unlock(&ctx->uring_lock);
	if (ctx->sq_creds)
		put_cred(ctx->sq_creds);
	if (ctx->submitter_task)
		put_task_struct(ctx->submitter_task);
	...
	io_rings_free(ctx);
	...
	percpu_ref_exit(&ctx->refs);
	free_uid(ctx->user);
	io_req_caches_free(ctx);

	WARN_ON_ONCE(ctx->nr_req_allocated);
	...
	kfree(ctx);
}
```

這裡才放 registered 資源（`io_sqe_buffers_unregister` / `io_sqe_files_unregister`，Ch 29）——到這一步才安全，因為所有 in-flight 請求（會引用 rsrc node 的）都已在階段 2 清完，node 的 refs 都歸零了。順序：先放所有子系統持有的東西，再 `io_rings_free`（釋放 SQ/CQ 共享記憶體），最後 `percpu_ref_exit`、`kfree(ctx)`。

`WARN_ON_ONCE(ctx->nr_req_allocated)` 是最後的護欄：如果還有 req 沒回收，代表前面漏了什麼，是 bug。

## 底層機制：為什麼 percpu_ref 當總開關

為什麼用 `percpu_ref` 而不是普通 `refcount_t` 管 ctx 生死？因為 **ctx 的引用在熱路徑上被頻繁拿放**——每個提交、每個完成、每個 task_work 都可能 `percpu_ref_get(&ctx->refs)` / `put`。用普通原子計數，每次都要跨 CPU 同步（cache line 彈來彈去），在高並發下是嚴重瓶頸。

`percpu_ref` 的巧思：**存活期間**用 per-CPU 計數器（各 CPU 加減自己的，零跨 CPU 同步，極快），**代價**是沒辦法隨時知道「總和是不是 0」（要加總所有 CPU）。但這沒關係——存活期間本來就不該歸零。只有在 `percpu_ref_kill` 之後，它切成原子模式、開始精確追蹤歸零。這完美契合 ctx 的生命週期：**活著時要快（per-CPU），要死時才需要精確知道歸零（原子）**。

這就是階段 1 的 `kill` 和階段 2 的 `wait_for_completion(&ref_comp)` 的配合：kill 切換模式並封鎖新引用，然後所有現有引用陸續 put，歸零時 callback 觸發 `ref_comp`，kworker 醒來進階段 3。`percpu_ref` 把「成千上萬個散落各處的引用何時全部放手」這個難題，變成一個等一個 completion 的簡單操作。

## process exit 的路徑：`io_uring_cancel_generic`

`close(ring_fd)` 是一條路；process **exit**（沒 close 就死了，或 `exec`）是另一條。task 退出時 kernel 呼叫 `__io_uring_cancel` → `io_uring_cancel_generic`（`io_uring/io_uring.c`，擷取）：

```c
__cold void io_uring_cancel_generic(bool cancel_all, struct io_sq_data *sqd)
{
	struct io_uring_task *tctx = current->io_uring;
	...
	atomic_inc(&tctx->in_cancel);
	do {
		bool loop = false;

		io_uring_drop_tctx_refs(current);
		if (!tctx_inflight(tctx, !cancel_all))
			break;

		inflight = tctx_inflight(tctx, false);
		if (!inflight)
			break;

		if (!sqd) {
			xa_for_each(&tctx->xa, index, node) {
				if (node->ctx->sq_data)
					continue;
				loop |= io_uring_try_cancel_requests(node->ctx,
							current->io_uring,
							cancel_all, false);
			}
		} else {
			... io_uring_try_cancel_requests(ctx, ..., true);
		}
		...
		prepare_to_wait(&tctx->wait, &wait, TASK_INTERRUPTIBLE);
		io_run_task_work();
		...
		if (inflight == tctx_inflight(tctx, !cancel_all))
			schedule();
		finish_wait(&tctx->wait, &wait);
	} while (1);

	io_uring_clean_tctx(tctx);
	if (cancel_all) {
		atomic_dec(&tctx->in_cancel);
		__io_uring_free(current);
	}
}
```

這條路是 **per-task** 的：它取消「這個 task 在所有它用過的 ring 上的 in-flight 請求」（`xa_for_each` 走訪 `tctx->xa` 裡所有 ctx）。設 `tctx->in_cancel` 旗標（讓完成路徑知道「正在取消，別再排新 task_work」），迴圈取消 + 等 inflight 歸零，最後 `io_uring_clean_tctx` 清掉 tctx 對各 ctx 的記錄。

`close` 路徑和 exit 路徑的分工：`close` 拆的是 **ctx**（一個 ring 實例），exit 拆的是 **tctx**（一個 task 對所有 ring 的使用）。一個 task exit 時，它用過的每個 ring 的 ctx 可能還有別的 task 在用（除非 SINGLE_ISSUER），所以 exit 只清自己這份；等最後一個引用（可能是別的 task 的 close）才觸發 ctx 的 `wait_and_kill`。兩條路最終都靠 `percpu_ref` 歸零來協調。

## 為什麼順序要命：UAF 的根源

把 teardown 的順序約束講白，就直接看到 Part 4 的攻擊面：

1. **先 kill 再取消**：必須先 `percpu_ref_kill` 封死新引用，才取消舊的。如果反過來——邊取消邊還能拿新引用——會永遠取消不完。
2. **先取消完才 free**：`io_ring_ctx_free` 放 rsrc、釋放 rings，**前提是沒有 in-flight 請求還會碰它們**。所以一定要等 `percpu_ref` 歸零（所有請求完成/取消）。若有一個請求「以為自己被取消了、其實還在某 worker 上跑」，在 free 之後去碰 `ctx->rings` 填 CQE——UAF。
3. **synchronize_rcu 在 free 之前**：DEFER_TASKRUN 的 `io_req_local_work_add` 在 RCU 讀區碰 ctx/req。free 前不 `synchronize_rcu` 等讀區結束，就是 RCU UAF。
4. **rsrc 放在請求清完之後**：先 `io_sqe_buffers_unregister`、再有請求用 buffer node，就 UAF（Ch 29 的 node 生命週期）。順序必須是「請求全清 → 才放 rsrc」。

這些順序約束裡**每一條**都對應過真實 CVE。io_uring 的 teardown 之所以是 UAF 重災區，正因為它要協調「散落在 io-wq thread、別的 CPU、RCU 讀區、各子系統等待結構裡」的大量 in-flight 狀態，任何一個被遺漏的「還活著的引用」，在 free 之後都是一個 UAF 原語。Part 4（尤其 Ch 32 UAF 模式、Ch 35 timeout/poll race）會拿具體 CVE 回來印證這些順序。

## 對比與取捨

| 路徑 | 觸發 | 拆什麼 | 核心機制 |
|---|---|---|---|
| `io_ring_ctx_wait_and_kill` | `close(ring_fd)` / 最後引用放手 | 一個 ctx（ring 實例） | `percpu_ref_kill` → exit_work → free |
| `io_uring_cancel_generic` | process exit / exec | 一個 tctx（task 的所有 ring 使用） | per-task 取消迴圈 + `in_cancel` 旗標 |
| `io_try_cancel` | `IORING_OP_ASYNC_CANCEL` / 上述兩者內部 | 單個請求 | 各子系統輪詢（io-wq/poll/timeout/...） |

取捨：teardown 放在 kworker（`iou_wq`）非同步做，是為了不讓 `close` syscall 卡住等磁碟 I/O 跑完（可能很久）。代價是 ctx 的釋放是**延遲**的——`close` 返回後 ctx 可能還活著幾毫秒到幾秒。這個「close 已返回但 ctx 未死」的視窗，本身也是某些 race 的舞台。

## 踩雷集錦

1. **以為 `close(ring_fd)` 返回就等於 ring 已銷毀**：不是。`close` 只做階段 1（kill + 排 work）就返回，真正的取消和 free 在 kworker 上非同步進行。ring 的記憶體在 `close` 返回後仍可能存活一段時間。

2. **混淆 ctx teardown 和 tctx cancel**：`close` 走 `wait_and_kill` 拆**一個 ctx**；process exit 走 `cancel_generic` 清**一個 task 對所有 ring 的使用**。兩者是不同粒度、不同觸發、不同函式，但都靠 `percpu_ref` 協調。

3. **忽略 `synchronize_rcu()` 的必要性**：DEFER_TASKRUN ring 的 teardown 尾端那個 `synchronize_rcu()` 不是可省的——它等 `io_req_local_work_add` 的 RCU 讀區結束。拿掉它（或在非 DEFER_TASKRUN 誤加）都會出事。這是 Ch 27/29 RCU 配對的下半場。

4. **以為請求只掛在一個子系統**：`io_try_cancel` 輪詢 io-wq/poll/waitid/futex/timeout，因為一個請求可能**同時**掛在多處（例如 io-wq 有份、poll 也 armed）。取消要每處都摘。只清一處會留下懸空引用。

5. **把那對空的 `spin_lock/spin_unlock(&completion_lock)` 當廢碼刪掉**：它是同步屏障，確保任何持 `completion_lock` 收尾的 context 都跑完。刪了就可能在別人還在收尾時 free。

6. **假設 teardown 一定很快**：`io_ring_exit_work` 有 5 分鐘 timeout 和 WARN——因為如果有請求卡死（例如一個 io-wq worker 永遠醒不來），teardown 會等很久。正常情況快，但它被設計成「慢也要等到乾淨」。

## 進階：為什麼 teardown 用獨立 workqueue `iou_wq`

`io_ring_ctx_wait_and_kill` 把 exit_work 排到 `iou_wq` 而非系統預設 workqueue。原因在 `io_uring_init`（`io_uring/io_uring.c`）建立它時的註解精神：如果大量 ring **同時**退出（例如一個 process 開了幾百個 ring 然後 exit），每個 exit_work 都進系統 workqueue 會瞬間 spawn 一堆 event kworker，純粹是噪音和開銷。用一個專用的 `WQ_UNBOUND` workqueue（`alloc_workqueue("iou_exit", WQ_UNBOUND, 64)`）把它們收攏，限制並發、減少 kworker 爆炸。這是個小但實際的工程決定,反映 io_uring 要處理「一個 process 大量 ring」的真實場景。

## 本章重點整理

- teardown 難在：ring 要拆時，in-flight 請求、armed poll、io-wq work、task_work、rsrc node 都還可能活在不同 context 裡，彼此持有對 ctx 的引用。必須以正確順序讓所有人放手，ctx 才能死。
- 三階段：**(1) kill**（`percpu_ref_kill`，封死新引用，排 exit_work）→ **(2) 取消 + 等歸零**（`io_ring_exit_work` 在 kworker 上反覆 `io_uring_try_cancel_requests`，等 `percpu_ref` 歸零，`synchronize_rcu`）→ **(3) free**（`io_ring_ctx_free` 放 rsrc、釋放 rings、`kfree`）。
- 單請求取消 `io_try_cancel` **輪詢各子系統**（io-wq → poll → waitid → futex → timeout），因為一個請求可能同時掛在多處。
- `percpu_ref` 當 ctx 生死總開關：存活時 per-CPU（快、無跨 CPU 同步），`kill` 後切原子模式精確追蹤歸零——契合「活著要快、要死才需知道歸零」。
- 兩條拆除路徑：`close` → `wait_and_kill` 拆**一個 ctx**；process exit → `cancel_generic` 清**一個 tctx 對所有 ring 的使用**。
- **順序約束每一條都對應真實 UAF**：先 kill 再取消、先取消完才 free、`synchronize_rcu` 在 free 前、rsrc 放在請求清完之後。這是 Part 4 攻擊面的直接前奏。
- `close` 返回 ≠ ring 已銷毀；真正的 free 在 kworker 上非同步延遲發生。

## 自我檢核

- [ ] 我能說出 teardown 的三階段、各在什麼 context 做、為什麼要分階段
- [ ] 我能解釋 `percpu_ref` 為什麼適合當 ctx 生死開關，kill 前後行為差在哪
- [ ] 我能講清楚 `io_try_cancel` 為什麼要輪詢多個子系統
- [ ] 我能分辨 `close` 路徑（拆 ctx）和 exit 路徑（清 tctx）的差別
- [ ] 我能說出至少三條 teardown 的順序約束，以及違反各自會造成什麼 UAF
- [ ] 我知道 `close` 返回不代表 ring 已死，以及 `synchronize_rcu` 在這裡的作用

## 延伸閱讀

### 必讀

- **Linux 6.18 原始碼 `io_uring/io_uring.c`** — <https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/io_uring/io_uring.c?h=v6.18>
  - **讀哪裡**：`io_ring_ctx_wait_and_kill`、`io_ring_exit_work`、`io_ring_ctx_free`、`io_uring_cancel_generic`
  - **和本章關聯**：本章所有函式名都可在此 grep 到；對照三階段走一遍

- **Linux 6.18 原始碼 `io_uring/cancel.c`**
  - **讀哪裡**：`io_try_cancel`、`io_async_cancel_one`
  - **和本章關聯**：單請求取消的子系統輪詢

### percpu_ref 機制

- **Linux 原始碼 `lib/percpu-refcount.c` 與 `include/linux/percpu-refcount.h`**
  - **讀哪裡**：`percpu_ref_kill`、`percpu_ref_switch_to_atomic` 的註解
  - **和本章關聯**：理解「活著 per-CPU、kill 後原子」的切換機制，本章 ctx 生死全靠它

### 底層機制（repo 內）

- **[../kernel_internals/27-rcu.md](../kernel_internals/27-rcu.md)**
  - **讀哪裡**：`synchronize_rcu` 與 grace period
  - **和本章關聯**：teardown 尾端 `synchronize_rcu()` 和 Ch 27 `io_req_local_work_add` 的 RCU 讀區配對，是防 UAF 的關鍵一環

### 安全（前瞻 Part 4）

- **[chompie1337 — Put an io_uring on it](https://chompie.rip/Blog+Posts/Put+an+io_uring+on+it+-+Exploiting+the+Linux+Kernel)**
  - **讀哪裡**：涉及請求生命週期 / teardown race 的段落
  - **和本章關聯**：本章的順序約束正是這類 exploit 的攻擊點，先建立 teardown 機制印象再看 Ch 32、Ch 35

Part 3 到此走完：我們把一個請求從提交（Ch 23）、派發（Ch 24）、完成（Ch 25）、到各種後備機制（io-wq、task_work、poll、rsrc）、最後到整個 ring 的拆除（本章），逐行對照 6.18 原始碼看了一遍。你現在手上有一張完整的「io_uring kernel 內部地圖」。接下來的練習 D 會讓你**動手改 kernel**——加一個自訂 opcode，把這張地圖從「讀得懂」變成「改得動」。之後 Part 4 會拿這張地圖去拆真實 CVE：你會發現每個漏洞都落在我們走過的某條路徑、某個生命週期的縫隙裡。

→ [練習 D 給 kernel 加一個自訂 io_uring opcode](./practice-d-custom-opcode.md)
