# Ch 23 — 提交路徑：io_submit_sqes 與 opcode dispatch

> **目標**：跟著一次 `io_uring_enter`，走完 kernel 把 SQ 裡的 SQE 變成待執行 `io_kiocb` 的完整路徑：讀 SQ tail、`io_get_sqe` 取 SQE、`io_init_req` 解析、經 `io_issue_defs` 表 dispatch、link chain 怎麼組裝。讀完你能把 Ch 2 user 端「推 tail + enter」之後發生的一切，在 kernel code 裡逐行指認。

> **環境**：kernel 6.18 原始碼，`io_uring/io_uring.c`（`io_submit_sqes` 第 2420 行、`io_get_sqe` 第 2388 行、`io_init_req` 第 2155 行、`io_submit_sqe` 第 2290 行）。

## 為什麼提交路徑是 Part 3 的樞紐？

Ch 2 你在 user 端做的最後兩步是「store-release 推 SQ tail」和「`io_uring_enter`」。推 tail 那一刻，請求對 kernel 可見了；enter 那一刻，kernel 開始處理。這一章就是 enter 之後、請求被真正執行（Ch 24）之前的一切：**從一塊共享記憶體裡的 64 bytes，變成一個有型別、有狀態、掛好 link、準備派發的 `io_kiocb`**。

這條路徑是三個子系統的交會點：Ch 20 的 ctx（提交狀態都在 `ctx->submit_state`）、Ch 21 的 io_kiocb（這裡誕生）、Ch 22 的共享記憶體（SQE 從這裡讀）。它也是 Part 4 最密集的攻擊面——因為這是 user 可控資料（SQE）進入 kernel 的第一道關卡，每個 `READ_ONCE`、每個驗證、每個 `array_index_nospec` 都在防某種濫用。

## 先建立直覺：一次提交的資料流

```
   io_uring_enter(fd, to_submit=N, ...)        [SYSCALL_DEFINE6]
        │  file = fd → ctx = file->private_data
        │  mutex_lock(&ctx->uring_lock)
        ▼
   io_submit_sqes(ctx, N)                       ── 批次迴圈 ──
        │  io_submit_state_start()  起 batch 狀態（free list、link、plug）
        │
        │  for (N 次):
        │    ┌──────────────────────────────────────────────┐
        │    │ io_alloc_req()   從 ctx free_list 拿空 req     │  (Ch 21)
        │    │ io_get_sqe()     讀 sq_array[head] → 拿 SQE 指標│
        │    │ io_submit_sqe():                               │
        │    │   io_init_req()  解析 SQE 欄位 + def->prep()    │
        │    │   link 處理 / 直接 io_queue_sqe() 送去 issue    │  (→ Ch 24)
        │    └──────────────────────────────────────────────┘
        │
        │  io_submit_state_end()   flush link、completion batch、plug
        │  io_commit_sqring()      smp_store_release 寫回 sq.head
        ▼
   回傳實際提交數
```

核心觀察：**提交是批次的**。一次 enter 說「我推了 N 個」，kernel 就在持有 `uring_lock` 的情況下一口氣處理 N 個，中間共用一份 `submit_state`（free request、completion batch、block plug），最後一次性 commit SQ head。這個批次化是 io_uring 攤平 syscall 成本的關鍵——N 個請求一次 enter，不是 N 次 syscall。

## 入口：enter 如何找到 ctx 並呼叫提交

Ch 22 建好的 ring fd，在 enter 這裡被解回 ctx：

```c
/* io_uring/io_uring.c, SYSCALL_DEFINE6(io_uring_enter, ...)（節錄） */
	if (flags & IORING_ENTER_REGISTERED_RING) {
		struct io_uring_task *tctx = current->io_uring;

		if (unlikely(!tctx || fd >= IO_RINGFD_REG_MAX))
			return -EINVAL;
		fd = array_index_nospec(fd, IO_RINGFD_REG_MAX);
		file = tctx->registered_rings[fd];
		if (unlikely(!file))
			return -EBADF;
	} else {
		file = fget(fd);
		if (unlikely(!file))
			return -EBADF;
		ret = -EOPNOTSUPP;
		if (unlikely(!io_is_uring_fops(file)))
			goto out;
	}

	ctx = file->private_data;
	/* ... */

	} else if (to_submit) {
		ret = io_uring_add_tctx_node(ctx);
		if (unlikely(ret))
			goto out;

		mutex_lock(&ctx->uring_lock);
		ret = io_submit_sqes(ctx, to_submit);
		if (ret != to_submit) {
			mutex_unlock(&ctx->uring_lock);
			goto out;
		}
		/* ... GETEVENTS 處理 ... */
		mutex_unlock(&ctx->uring_lock);
	}
```

兩條取 fd 的路：一般 fd 走 `fget` + `io_is_uring_fops` 驗證（確認這真的是個 io_uring fd，不是隨便一個 fd）；registered ring fd（Ch 3）走 tctx 的陣列，連 `fget` 都省。拿到 `ctx = file->private_data`（Ch 22 綁的）後，**持 `uring_lock`** 呼叫 `io_submit_sqes()`。注意 SQPOLL 模式下根本不走這條——那時 kernel thread 自己提交，enter 只負責叫醒它（Ch 27）。`array_index_nospec` 是 Spectre 緩解，防止用越界 fd index 做推測執行側信道——這種防護在提交路徑裡到處都是。

## io_submit_sqes：批次提交的主迴圈

```c
/* io_uring/io_uring.c */
int io_submit_sqes(struct io_ring_ctx *ctx, unsigned int nr)
	__must_hold(&ctx->uring_lock)
{
	unsigned int entries = io_sqring_entries(ctx);
	unsigned int left;
	int ret;

	if (unlikely(!entries))
		return 0;
	/* make sure SQ entry isn't read before tail */
	ret = left = min(nr, entries);
	io_get_task_refs(left);
	io_submit_state_start(&ctx->submit_state, left);

	do {
		const struct io_uring_sqe *sqe;
		struct io_kiocb *req;

		if (unlikely(!io_alloc_req(ctx, &req)))
			break;
		if (unlikely(!io_get_sqe(ctx, &sqe))) {
			io_req_add_to_cache(req, ctx);
			break;
		}

		/*
		 * Continue submitting even for sqe failure if the
		 * ring was setup with IORING_SETUP_SUBMIT_ALL
		 */
		if (unlikely(io_submit_sqe(ctx, req, sqe)) &&
		    !(ctx->flags & IORING_SETUP_SUBMIT_ALL)) {
			left--;
			break;
		}
	} while (--left);

	if (unlikely(left)) {
		ret -= left;
		if (!ret && io_req_cache_empty(ctx))
			ret = -EAGAIN;
		current->io_uring->cached_refs += left;
	}

	io_submit_state_end(ctx);
	 /* Commit SQ ring head once we've consumed and submitted all SQEs */
	io_commit_sqring(ctx);
	return ret;
}
```

逐段拆：

- **`entries = io_sqring_entries(ctx)`**：算出 SQ 裡實際有幾個待提交（`tail - head`）。`left = min(nr, entries)`——user 說推了 `nr` 個，但以 kernel 看到的 SQ tail 為準，不信 user 的數字。這對應 Ch 2 「kernel acquire 讀 tail」。
- **`io_get_task_refs(left)`**：一次預取 `left` 個 task 引用（per-task 的 `io_uring_task` 計數），攤平每個請求各自 get 的成本。沒用完的在結尾 `cached_refs += left` 退回。
- **`io_submit_state_start()`**：初始化這批的 `submit_state`——清空 link head、設定 block plug 門檻（`need_plug = max_ios > 2`，超過 2 個 I/O 才值得開 plug）。
- **迴圈三步**：`io_alloc_req`（拿空 req，Ch 21）、`io_get_sqe`（拿 SQE 指標）、`io_submit_sqe`（解析 + 送出）。任一步失敗就 break；SQE 解析失敗時，除非設了 `SUBMIT_ALL`（要求「即使有錯也繼續提交剩下的」），否則停。
- **結尾**：`io_submit_state_end()` flush 掉未完成的 link、completion batch、block plug；`io_commit_sqring()` 把 kernel 快取的 `cached_sq_head` 寫回共享的 `rings->sq.head`。

## io_get_sqe：穿過兩層間接拿到 SQE

Ch 2/Ch 20 說過 SQ 的兩層間接（sq_array → sq_sqes）。這裡是它的 kernel 實作：

```c
/* io_uring/io_uring.c */
static bool io_get_sqe(struct io_ring_ctx *ctx, const struct io_uring_sqe **sqe)
{
	unsigned mask = ctx->sq_entries - 1;
	unsigned head = ctx->cached_sq_head++ & mask;

	if (static_branch_unlikely(&io_key_has_sqarray) &&
	    (!(ctx->flags & IORING_SETUP_NO_SQARRAY))) {
		head = READ_ONCE(ctx->sq_array[head]);
		if (unlikely(head >= ctx->sq_entries)) {
			WRITE_ONCE(ctx->rings->sq_dropped,
				   READ_ONCE(ctx->rings->sq_dropped) + 1);
			return false;
		}
		head = array_index_nospec(head, ctx->sq_entries);
	}

	/* double index for 128-byte SQEs, twice as long */
	if (ctx->flags & IORING_SETUP_SQE128)
		head <<= 1;
	*sqe = &ctx->sq_sqes[head];
	return true;
}
```

- **`head = ctx->cached_sq_head++ & mask`**：kernel 自己的 head 計數器（Ch 20 的 `cached_sq_head`），`& mask` 折成槽位——正是 Ch 2 user 端 `tail & ring_mask` 的對稱。這裡只動 kernel 快取，批次結束才 commit。
- **兩層間接**：`sq_array[head]` 拿到真正的 SQE index。這解釋了 Ch 2「提交順序可和填寫順序不同」——user 可以填好一堆 SQE，再用 sq_array 決定這次提交哪幾個、什麼順序。`static_branch_unlikely(&io_key_has_sqarray)` 是 Ch 22 setup 時 `static_branch_inc` 開的 static key：若全系統沒有任何 ring 用 sq_array，這個分支被 code patching 掉，零成本。
- **防越界**：`sq_array[head]` 是 user 可寫的，所以 `if (head >= ctx->sq_entries)` 必須檢查——user 塞一個越界 index 進 sq_array，kernel 不能拿去索引 `sq_sqes`。超界就 `sq_dropped++`（Ch 2 user 端讀得到這個計數）並回 false。`array_index_nospec` 再防推測執行。
- **`READ_ONCE`**：SQE index 在共享記憶體，必須 `READ_ONCE` 一次讀定，不能讓編譯器重載（user 可能同時在改）。

這一小段把「共享記憶體 + user 不可信」的紀律展現得淋漓盡致：每個從 user 記憶體讀的值都 `READ_ONCE`、每個拿去當 index 的值都邊界檢查 + `array_index_nospec`。

## io_init_req：SQE 的 64 bytes 變成 io_kiocb

拿到 SQE 指標後，`io_submit_sqe()` 先呼叫 `io_init_req()` 做解析。這是請求「誕生」的核心（Ch 21 階段一）：

```c
/* io_uring/io_uring.c, io_init_req()（節錄） */
static int io_init_req(struct io_ring_ctx *ctx, struct io_kiocb *req,
		       const struct io_uring_sqe *sqe)
	__must_hold(&ctx->uring_lock)
{
	const struct io_issue_def *def;
	unsigned int sqe_flags;
	int personality;
	u8 opcode;

	req->ctx = ctx;
	req->opcode = opcode = READ_ONCE(sqe->opcode);
	/* same numerical values with corresponding REQ_F_*, safe to copy */
	sqe_flags = READ_ONCE(sqe->flags);
	req->flags = (__force io_req_flags_t) sqe_flags;
	req->cqe.user_data = READ_ONCE(sqe->user_data);
	req->file = NULL;
	req->tctx = current->io_uring;
	req->cancel_seq_set = false;
	req->async_data = NULL;

	if (unlikely(opcode >= IORING_OP_LAST)) {
		req->opcode = 0;
		return io_init_fail_req(req, -EINVAL);
	}
	opcode = array_index_nospec(opcode, IORING_OP_LAST);

	def = &io_issue_defs[opcode];
	if (unlikely(sqe_flags & ~SQE_COMMON_FLAGS)) {
		/* enforce forwards compatibility on users */
		if (sqe_flags & ~SQE_VALID_FLAGS)
			return io_init_fail_req(req, -EINVAL);
		if (sqe_flags & IOSQE_BUFFER_SELECT) {
			if (!def->buffer_select)
				return io_init_fail_req(req, -EOPNOTSUPP);
			req->buf_index = READ_ONCE(sqe->buf_group);
		}
		if (sqe_flags & IOSQE_CQE_SKIP_SUCCESS)
			ctx->drain_disabled = true;
		if (sqe_flags & IOSQE_IO_DRAIN) {
			if (ctx->drain_disabled)
				return io_init_fail_req(req, -EOPNOTSUPP);
			io_init_drain(ctx);
		}
	}
	/* ... restriction / drain / ioprio / iopoll 檢查 ... */

	if (def->needs_file) {
		struct io_submit_state *state = &ctx->submit_state;

		req->cqe.fd = READ_ONCE(sqe->fd);

		if (state->need_plug && def->plug) {
			state->plug_started = true;
			state->need_plug = false;
			blk_start_plug_nr_ios(&state->plug, state->submit_nr);
		}
	}

	personality = READ_ONCE(sqe->personality);
	if (personality) {
		/* ... xa_load creds, override, 設 REQ_F_CREDS ... */
	}

	return def->prep(req, sqe);
}
```

這個函式是「user 可控資料的消毒站」，看它的紀律：

- **每個 SQE 欄位都 `READ_ONCE`**：`opcode`、`flags`、`user_data`、`fd`、`personality`。共享記憶體，一次讀定。
- **opcode 邊界 + nospec**：`opcode >= IORING_OP_LAST` 就 `-EINVAL`，然後 `array_index_nospec` 才拿去索引 `io_issue_defs[]`。這是 dispatch 表安全的前提——Ch 19 說的函式指標表，索引必須先消毒。
- **flags 直接複製的玄機**：`req->flags = (__force io_req_flags_t) sqe_flags`。Ch 21 說過前 7 個 `REQ_F_*` bit 和 `IOSQE_*` 數值對齊，所以 user 的 flags 能原樣複製成 req flags，省一堆轉換。
- **向前相容的執法**：`sqe_flags & ~SQE_VALID_FLAGS`（有未知 flag bit）就 `-EINVAL`——拒絕 user 設 kernel 不認識的 flag，保證舊 kernel 不會默默忽略新語意。
- **能力門檻**：`IOSQE_BUFFER_SELECT` 但這個 op 不支援 buffer selection（`!def->buffer_select`）→ `-EOPNOTSUPP`。opcode 的性質（Ch 19 的 bitfield）在這裡被用來驗證 user 的請求是否合理。
- **block plug**：讀寫類 op（`def->plug`）且這批超過 2 個 I/O，開一個 block plug 把多個 bio 批次送進 block layer（效能，Ch 10 相關）。
- **最後 `def->prep(req, sqe)`**：把控制權交給 opcode 自己的 prep handler，讓它解析該 op 特有的欄位（read 的 buf/len/off、accept 的 addr…）進 `req->cmd` 或 `async_data`。prep 之後，請求「誕生」完成。

## link chain 的組裝：io_submit_sqe

`io_init_req` 成功後，`io_submit_sqe()` 決定這個請求是**單獨送出**還是**接進一條 link 鏈**：

```c
/* io_uring/io_uring.c, io_submit_sqe()（節錄） */
	ret = io_init_req(ctx, req, sqe);
	if (unlikely(ret))
		return io_submit_fail_init(sqe, req, ret);

	trace_io_uring_submit_req(req);

	if (unlikely(link->head)) {
		trace_io_uring_link(req, link->last);
		io_req_sqe_copy(req, IO_URING_F_INLINE);
		link->last->link = req;
		link->last = req;

		if (req->flags & IO_REQ_LINK_FLAGS)
			return 0;
		/* last request of the link, flush it */
		req = link->head;
		link->head = NULL;
		if (req->flags & (REQ_F_FORCE_ASYNC | REQ_F_FAIL))
			goto fallback;

	} else if (unlikely(req->flags & (IO_REQ_LINK_FLAGS |
					  REQ_F_FORCE_ASYNC | REQ_F_FAIL))) {
		if (req->flags & IO_REQ_LINK_FLAGS) {
			link->head = req;
			link->last = req;
		} else {
fallback:
			io_queue_sqe_fallback(req);
		}
		return 0;
	}

	io_queue_sqe(req, IO_URING_F_INLINE);
	return 0;
```

link 的狀態機（Ch 16 的 kernel 端）：

- **已有 link head**（`link->head` 非空）：把 req 接到鏈尾（`link->last->link = req`）。如果 req 自己也帶 `LINK`/`HARDLINK` flag（`IO_REQ_LINK_FLAGS`），鏈還沒結束，`return 0` 繼續等下一個。如果 req 不帶 link flag，它是**鏈的最後一個**——取出整條鏈的 head，準備一次送出。
- **沒有 link head，但 req 帶 link flag**：這是**新鏈的開頭**，設 `link->head = link->last = req`，`return 0` 等後續。
- **普通請求**（無 link）：直接 `io_queue_sqe(req, IO_URING_F_INLINE)` 送去執行（Ch 24）。

`io_queue_sqe()` 就是通往 Ch 24 `io_issue_sqe` 的門。帶 `REQ_F_FORCE_ASYNC`（`IOSQE_ASYNC`）或已失敗（`REQ_F_FAIL`）的走 `io_queue_sqe_fallback()`——不嘗試 inline，直接退到 io-wq 或失敗處理。

**為什麼 link 要在鏈完整後才送？** 因為 link 的語意是「依序執行」：前一個完成才跑下一個。鏈組好後，只有 head 先送去執行，其餘掛在 `head->link` 上，由完成路徑（Ch 25）接力喚醒。`io_req_sqe_copy()` 在這裡把 SQE 內容複製進 req——因為 link 的後續請求可能延後執行，而共享記憶體的 SQE 隨時會被 user 覆寫，必須先拷貝一份穩定的（這也是 Ch 22 說的「共享記憶體信任邊界」的後果之一）。

## 收尾：commit SQ head

批次跑完，`io_commit_sqring()` 把 kernel 快取的 head 發佈回共享記憶體：

```c
/* io_uring/io_uring.c */
static void io_commit_sqring(struct io_ring_ctx *ctx)
{
	struct io_rings *rings = ctx->rings;

	/*
	 * Ensure any loads from the SQEs are done at this point,
	 * since once we write the new head, the application could
	 * write new data to them.
	 */
	smp_store_release(&rings->sq.head, ctx->cached_sq_head);
}
```

**`smp_store_release`**——這是 Ch 2/Ch 5 memory ordering 的 kernel 另一面。user 端推 tail 用 store-release，kernel 端移 head 也用 store-release。註解講明原因：一旦寫了新 head，user 就認為那些 SQE 槽空了、可以覆寫；所以必須保證「kernel 對 SQE 的所有讀取」都在這個 store 之前完成。release 語意正是「我之前的所有存取，對看到這個 store 的人都可見」。少了它，user 可能在 kernel 還沒讀完 SQE 時就覆寫它——偶發的資料損壞。

## 底層機制：為什麼全程持 uring_lock

整個 `io_submit_sqes` 在 `__must_hold(&ctx->uring_lock)` 下跑。為什麼提交要獨佔這把 mutex？因為提交會碰的東西——`submit_state`（free list、link、plug）、`cached_sq_head`、request 快取、per-op 的 prep（可能動 registered files/buffers 表）——大多沒有自己的細粒度鎖，靠 `uring_lock` 這把大鎖序列化。io_uring 的設計哲學是「提交路徑單執行緒化」：要嘛一個 app thread 持鎖提交，要嘛 SQPOLL 一條 kernel thread 提交（它天生單執行緒，不需要鎖，見 Ch 20 的 `iopoll_list` 註解）。SINGLE_ISSUER/DEFER_TASKRUN 更進一步把這假設推到完成端，換來 Ch 20 的 `lockless_cq`。

這也解釋了為什麼 `uring_lock` 是 mutex 不是 spinlock：prep 可能睡（配置 `async_data`、等 block layer 資源），持 spinlock 不能睡。

## 對比與取捨：批次 vs 逐個

| 面向 | io_uring 批次提交 | 傳統 syscall 逐個 |
|---|---|---|
| syscall 次數 | N 個請求 1 次 enter（SQPOLL 下可 0 次） | N 次 |
| 鎖 | 持 `uring_lock` 一次處理整批 | 每次各自進 kernel |
| block I/O | 一個 plug 批次送 bio | 各自送 |
| 錯誤處理 | 單個失敗預設停（除非 SUBMIT_ALL） | 各自回報 |
| 代價 | 提交路徑序列化（一次一個提交者） | 可多執行緒各自 syscall |

批次的代價是提交端的序列化——這就是為什麼高並行設計傾向「一個 ring 配一個 thread」而非「多 thread 搶一個 ring」。io_uring 用「多個 ring」而非「一個 ring 多 writer」來 scale。

## 踩雷集錦

1. **以為 kernel 信 `to_submit` 的數字**。不。`io_submit_sqes` 用 `min(nr, io_sqring_entries(ctx))`，以 kernel 讀到的 SQ tail 為準。user 謊報大數字不會讓 kernel 讀越界。
2. **忽略 `io_get_sqe` 的越界檢查**。`sq_array[head]` 是 user 可寫的，kernel 必須檢查它 `< sq_entries` 才能拿去索引 `sq_sqes`。少了這步就是 OOB——這類 index 驗證正是 Part 4 要盯的。
3. **以為 link 的每個請求都立刻執行**。只有 link head 先送，其餘掛在 `head->link` 鏈上，由完成路徑接力。鏈未完成（最後一個還沒到）之前，整條都不送。
4. **把 `IOSQE_ASYNC`（`REQ_F_FORCE_ASYNC`）當成「比較快」**。它跳過 inline 非阻塞嘗試直接退 io-wq，對本來能 inline 完成的 op 反而**更慢**。它的用途是強制某些不想在提交 context 跑的工作離開（Ch 24/26）。
5. **忘了 SQE 內容可能被 user 覆寫**。link 的後續請求用 `io_req_sqe_copy` 拷貝 SQE 正是因此。任何「延後執行」的路徑都不能假設共享記憶體的 SQE 還在。
6. **以為 `io_commit_sqring` 的 `smp_store_release` 可省**。不行。它保證 kernel 讀完 SQE 才釋放槽位給 user，少了它是偶發資料損壞，和 Ch 2 user 端漏 release 推 tail 對稱。

## 進階：drain 的狀態機與 SUBMIT_ALL

正文略過了 `io_init_req` 裡 drain 的細節，但它是 io_uring 一個精巧的小狀態機。`IOSQE_IO_DRAIN` 的語意是「這個請求要等前面所有已提交的請求都完成，才能開始」——它需要一個 barrier。提交時 `io_init_drain` 把 ctx 切進 drain 模式：

```c
/* io_uring/io_uring.c */
static void io_init_drain(struct io_ring_ctx *ctx)
{
	struct io_kiocb *head = ctx->submit_state.link.head;

	ctx->drain_active = true;
	if (head) {
		/*
		 * 若 drain 落在 link 中間，連 link head 一起 drain，
		 * 且本 link 之後的請求也要 drain
		 */
		head->flags |= REQ_F_IO_DRAIN | REQ_F_FORCE_ASYNC;
		ctx->drain_next = true;
	}
}
```

`drain_active` 一旦為真，後續請求在 `io_init_req` 裡都會被標 `REQ_F_FORCE_ASYNC` 踢到慢路徑（`io_queue_sqe_fallback` → `io_drain_req`），在那裡排進 `ctx->defer_list` 等前序完成。`drain_next` 處理「drain 落在 link 中間」的邊角：一條 link 是一個整體，drain 必須涵蓋整條，所以標記「下一個非 link 請求也要 drain」。這就是 Ch 20 那兩個 `drain_active`/`drain_next` 1-bit 旗標的用途。drain 是昂貴的全域 barrier（序列化整個 ring），所以 `IOSQE_CQE_SKIP_SUCCESS` 會設 `ctx->drain_disabled` 禁掉它——兩個功能語意衝突。

另一個進階旗標 `IORING_SETUP_SUBMIT_ALL`：預設下，一個 SQE 解析失敗（`io_submit_sqe` 回非 0）就停止處理整批剩下的（正文 `io_submit_sqes` 迴圈的 `break`）。`SUBMIT_ALL` 改成「即使某個失敗也繼續提交後面的」——適合「這批請求彼此獨立、一個壞的不該拖垮其他」的場景。失敗的那個仍會以錯誤 CQE 回報（`io_submit_fail_init` → `io_queue_sqe_fallback`），只是不中斷批次。

## 本章重點整理

- enter 用 `fd → file->private_data` 拿回 ctx，持 `uring_lock` 呼叫 `io_submit_sqes` 批次提交（SQPOLL 模式則只叫醒 kernel thread）。
- `io_submit_sqes` 迴圈：`io_alloc_req`（拿空 req）→ `io_get_sqe`（穿過 sq_array 兩層間接拿 SQE）→ `io_submit_sqe`（`io_init_req` 解析 + link 處理 + 送出）。
- `io_get_sqe`/`io_init_req` 是 user 資料消毒站：每個欄位 `READ_ONCE`、opcode/index 邊界檢查 + `array_index_nospec`、未知 flag 一律 `-EINVAL`。
- opcode dispatch 靠 `io_issue_defs[opcode]`（函式指標表，Ch 19），`io_init_req` 最後呼叫 `def->prep` 讓 opcode 解析自己的欄位。
- link chain 在 `io_submit_sqe` 組裝：帶 link flag 的接進鏈、等鏈完整才送 head，後續用 `io_req_sqe_copy` 拷貝 SQE 以防共享記憶體被覆寫。
- 結尾 `io_commit_sqring` 用 `smp_store_release` 寫回 `sq.head`，與 Ch 2 user 端 memory ordering 對稱；全程持 `uring_lock` 序列化提交。

## 自我檢核

- [ ] 我能畫出從 `io_uring_enter` 到請求被送去 issue 的完整呼叫鏈。
- [ ] 我能解釋 `io_get_sqe` 為什麼要檢查 `head >= sq_entries`、為什麼用 `READ_ONCE` 和 `array_index_nospec`。
- [ ] 我知道 `io_init_req` 怎麼把 SQE flags 直接複製成 `req->flags`，以及為什麼可以。
- [ ] 我能描述 link chain 的組裝狀態機，以及為什麼只有 head 先送。
- [ ] 我能說出 `io_commit_sqring` 的 `smp_store_release` 保證什麼、少了會怎樣。

## 延伸閱讀

- **`io_uring/io_uring.c`（`io_submit_sqes`、`io_get_sqe`、`io_init_req`、`io_submit_sqe`、`io_commit_sqring`）** — 6.18
  - **讀哪裡**：本章五個函式全文；特別數一數 `READ_ONCE` 和邊界檢查出現幾次
- **本課 [Ch 2 三十分鐘全貌](./02-thirty-minute-tour.md)、[Ch 5 提交與完成的 memory ordering](./05-submit-complete-ordering.md)**
  - **讀哪裡**：user 端推 tail/enter 與 barrier；和本章 `io_commit_sqring` 一體兩面
- **本課 [Ch 16 Linked operations 與 drain](./16-linked-operations.md)**
  - **讀哪裡**：link chain 的 user 語意，對照本章 `io_submit_sqe` 的組裝邏輯
- **kernel_internals [Ch 24 atomics 與 memory ordering](../kernel_internals/24-atomics-memory-ordering.md)、[Ch 26 mutex](../kernel_internals/26-mutex-semaphore.md)**
  - **讀哪裡**：`smp_store_release`/acquire 語意、mutex 為何能睡——本章 barrier 與 `uring_lock` 的底層
- **[Lord of the io_uring — Submission](https://unixism.net/loti/)** — Shuveb Hussain
  - **讀哪裡**：提交流程的 user 視角，和本章 kernel 視角互補

請求已經解析好、送到 `io_queue_sqe` 的門口了。下一章推開那扇門：`io_issue_sqe` 怎麼呼叫每個 op 的 `->issue()`，`IO_URING_F_NONBLOCK` 的非阻塞嘗試怎麼運作，`-EAGAIN` 時怎麼退回 poll 或 io-wq。

→ [Ch 24 io_issue_sqe 與 IO_URING_F_NONBLOCK](./24-issue-sqe.md)
