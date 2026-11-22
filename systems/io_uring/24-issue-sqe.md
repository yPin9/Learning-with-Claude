# Ch 24 — io_issue_sqe 與 IO_URING_F_NONBLOCK

> **目標**：走完請求「執行」的那一瞬間：`io_issue_sqe` 怎麼透過 `io_issue_defs` 表呼叫每個 op 的 `->issue()`、`issue_flags`（`IO_URING_F_NONBLOCK`/`F_COMPLETE_DEFER`/`F_MULTISHOT`）各控制什麼、非阻塞嘗試遇到會阻塞時怎麼回 `-EAGAIN`、以及這個 `-EAGAIN` 如何把請求導向 poll 或 io-wq。讀完你就懂了 io_uring 非同步的三條岔路在哪分。

> **環境**：kernel 6.18 原始碼，`io_uring/io_uring.c`（`__io_issue_sqe` 第 1808 行、`io_issue_sqe` 第 1841 行、`io_queue_async` 第 2044 行、`io_wq_submit_work` 第 1900 行）。

## 為什麼 issue 這一步是 io_uring 非同步的分水嶺？

到 Ch 23 結束，請求已經是個解析好的 `io_kiocb`，站在 `io_queue_sqe` 的門口。推開這扇門，就是**真正嘗試執行**的那一刻。而 io_uring 非同步的全部祕密，就濃縮在這一刻的一個決定上：

> **先試著不阻塞地把它做完；做不完（會睡）就別睡，想辦法稍後再試。**

這就是 `IO_URING_F_NONBLOCK`。io_uring 不是「每個請求都開一條 thread 去阻塞」，而是「先在提交 context 裡用非阻塞模式賭一把能立刻完成」。賭贏了（資料在 page cache、socket 有資料）——請求當場完成，零額外 context，最快路徑。賭輸了（要等磁碟、socket 沒資料）——op 回 `-EAGAIN`，io_uring 接手把它導去**能等的地方**：可以 poll 的 fd 走 poll（Ch 28），不能 poll 的走 io-wq worker thread 真的阻塞（Ch 26）。

這三條岔路（inline 完成 / poll / io-wq）在哪裡分、怎麼分，就是這一章。它也是理解 io_uring 效能與攻擊面的關鍵：請求跨 context 的複雜生命週期（Ch 21）正是從這個 `-EAGAIN` 分岔開始的。

## 先建立直覺：issue 的三條岔路

```
   io_queue_sqe(req)                         issue_flags = NONBLOCK | COMPLETE_DEFER
        │
        ▼
   io_issue_sqe(req, issue_flags)
        │  io_assign_file()   需要 file 就現在綁（fixed/normal）
        │  __io_issue_sqe() → def->issue(req, issue_flags)   ← 呼叫 op 本體
        │
        ├── ret == IOU_COMPLETE ────────────▶ (A) 當場完成，填 CQE（→ Ch 25）
        │                                         最快路徑：零額外 context
        │
        ├── ret == IOU_ISSUE_SKIP_COMPLETE ─▶ op 自己接管完成（如 poll armed）
        │
        └── ret == -EAGAIN (IOU_RETRY) ─────▶ io_queue_async(req, ret)
                                                  │
                      ┌───────────────────────────┤
                      ▼                           ▼
              (B) io_arm_poll_handler()    (C) io_queue_iowq()
                  可 poll 的 fd：掛 poll，      不可 poll：退給 io-wq
                  就緒時 task_work 重試        worker thread 真的阻塞執行
                  (Ch 28)                     (Ch 26)
```

(A) 是常態與最快；(B)/(C) 是 `-EAGAIN` 後的補救。記住這張圖，下面每段 code 都在填它的細節。

## issue_flags：一個整數裝的四個指令

在讀 `io_issue_sqe` 之前先認識 `issue_flags`。它是傳給每個 op 的 `->issue()` 的一個 `unsigned int`，用 bit 攜帶「這次執行該怎麼做」的指令（定義在 `io_uring_types.h`）：

```c
/* include/linux/io_uring_types.h, enum io_uring_cmd_flags（節錄） */
	IO_URING_F_COMPLETE_DEFER	= 1,   /* 完成可延後批次處理 */
	IO_URING_F_UNLOCKED		= 2,   /* 不持 uring_lock（io-wq context） */
	IO_URING_F_MULTISHOT		= 4,   /* 這是 multishot 執行 */
	IO_URING_F_IOWQ			= 8,   /* 由 io-wq 執行 */
	IO_URING_F_INLINE		= 16,  /* 從 syscall inline 執行 */
	/* int 的最高位，sign 檢查通常比 bit test 快 */
	IO_URING_F_NONBLOCK		= INT_MIN,
```

最關鍵是 **`IO_URING_F_NONBLOCK = INT_MIN`**——它故意放在最高位（sign bit）。為什麼？因為 op handler 檢查「要不要非阻塞」極其頻繁，而檢查一個 int 的正負號（`if (issue_flags < 0)` 或 sign test）比測試任意 bit 快一點。這種為了熱路徑的微觀設計在 io_uring 裡很典型。

不同來源的 issue 帶不同 flags：

- **提交 context inline 執行**：`NONBLOCK | COMPLETE_DEFER | INLINE`（見 `io_queue_sqe`）。非阻塞 + 完成延後批次。
- **io-wq worker 執行**：`UNLOCKED | IOWQ`（見 `io_wq_submit_work`），**沒有** NONBLOCK——worker 就是來阻塞的，當然可以睡。
- **poll 就緒後重試**：`NONBLOCK | MULTISHOT | COMPLETE_DEFER`（見 `io_poll_issue`）。

所以同一個 `def->issue()` 會在不同 context 被呼叫，靠 `issue_flags` 知道「這次能不能睡、要不要延後完成」。

## io_queue_sqe → io_issue_sqe：常態路徑

Ch 23 末尾普通請求走的是這裡：

```c
/* io_uring/io_uring.c */
static inline void io_queue_sqe(struct io_kiocb *req, unsigned int extra_flags)
	__must_hold(&req->ctx->uring_lock)
{
	unsigned int issue_flags = IO_URING_F_NONBLOCK |
				   IO_URING_F_COMPLETE_DEFER | extra_flags;
	int ret;

	ret = io_issue_sqe(req, issue_flags);

	/*
	 * We async punt it if the file wasn't marked NOWAIT, or if the file
	 * doesn't support non-blocking read/write attempts
	 */
	if (unlikely(ret))
		io_queue_async(req, issue_flags, ret);
}
```

邏輯極簡：帶著 `NONBLOCK | COMPLETE_DEFER` 去 issue。**回傳 0 代表搞定了**（當場完成或 op 自己接管），非 0（實際上是 `-EAGAIN`）就交給 `io_queue_async` 走補救岔路。

`io_issue_sqe` 本身：

```c
/* io_uring/io_uring.c */
static int io_issue_sqe(struct io_kiocb *req, unsigned int issue_flags)
{
	const struct io_issue_def *def = &io_issue_defs[req->opcode];
	int ret;

	if (unlikely(!io_assign_file(req, def, issue_flags)))
		return -EBADF;

	ret = __io_issue_sqe(req, issue_flags, def);

	if (ret == IOU_COMPLETE) {
		if (issue_flags & IO_URING_F_COMPLETE_DEFER)
			io_req_complete_defer(req);
		else
			io_req_complete_post(req, issue_flags);

		return 0;
	}

	if (ret == IOU_ISSUE_SKIP_COMPLETE) {
		ret = 0;

		/* If the op doesn't have a file, we're not polling for it */
		if ((req->ctx->flags & IORING_SETUP_IOPOLL) && def->iopoll_queue)
			io_iopoll_req_issued(req, issue_flags);
	}
	return ret;
}
```

三件事：

1. **`io_assign_file`**：需要 file 的 op（`def->needs_file`），現在把 fd 解析成 `struct file *`。fixed file 走 registered 表、normal 走 `fget`：

```c
/* io_uring/io_uring.c */
static bool io_assign_file(struct io_kiocb *req, const struct io_issue_def *def,
			   unsigned int issue_flags)
{
	if (req->file || !def->needs_file)
		return true;

	if (req->flags & REQ_F_FIXED_FILE)
		req->file = io_file_get_fixed(req, req->cqe.fd, issue_flags);
	else
		req->file = io_file_get_normal(req, req->cqe.fd);

	return !!req->file;
}
```

解不到 file 就 `-EBADF`——這正是 Ch 0「故意弄壞它」拿到 `-EBADF` 的 kernel 來源（但那是從 CQE 回來的，不是 syscall 當場）。

2. **`__io_issue_sqe` 呼叫 op 本體**（下一節細看），拿到回傳碼。
3. **依回傳碼決定後續**：
   - **`IOU_COMPLETE`（= 0）**：op 完成了。`COMPLETE_DEFER` 就 `io_req_complete_defer`（排進完成批次，稍後 flush）、否則 `io_req_complete_post`（立刻貼 CQE）。兩者都是 Ch 25 的主題。回 0。
   - **`IOU_ISSUE_SKIP_COMPLETE`（= `-EIOCBQUEUED`）**：op 說「我接管了，別幫我完成」——典型是 armed poll：op 掛好 poll 就返回，完成等 poll 就緒。回 0（對提交者而言沒錯誤）。
   - **其他（`-EAGAIN` 等）**：原樣回傳，讓 `io_queue_sqe` 送去 `io_queue_async`。

這三個回傳碼的定義值得記（`io_uring.h`）：

```c
/* io_uring/io_uring.h */
	IOU_COMPLETE		= 0,
	IOU_ISSUE_SKIP_COMPLETE	= -EIOCBQUEUED,
	IOU_RETRY		= -EAGAIN,
```

**op handler 和核心引擎之間的全部協定，就是這三個值。** op 做完回 `IOU_COMPLETE`；op 自己接管後續回 `IOU_ISSUE_SKIP_COMPLETE`；op 會阻塞、想重試回 `IOU_RETRY`（即 `-EAGAIN`）。讀任何一個 op 的 `issue` 實作，看它回哪個值，就知道它把請求交還給引擎的哪條路。

## __io_issue_sqe：真正呼叫 ->issue() 的地方

```c
/* io_uring/io_uring.c */
static inline int __io_issue_sqe(struct io_kiocb *req,
				 unsigned int issue_flags,
				 const struct io_issue_def *def)
{
	const struct cred *creds = NULL;
	struct io_kiocb *link = NULL;
	int ret;

	if (unlikely(req->flags & REQ_ISSUE_SLOW_FLAGS)) {
		if ((req->flags & REQ_F_CREDS) && req->creds != current_cred())
			creds = override_creds(req->creds);
		if (req->flags & REQ_F_ARM_LTIMEOUT)
			link = __io_prep_linked_timeout(req);
	}

	if (!def->audit_skip)
		audit_uring_entry(req->opcode);

	ret = def->issue(req, issue_flags);

	if (!def->audit_skip)
		audit_uring_exit(!ret, ret);

	if (unlikely(creds || link)) {
		if (creds)
			revert_creds(creds);
		if (link)
			io_queue_linked_timeout(link);
	}

	return ret;
}
```

中間那行 `ret = def->issue(req, issue_flags)` 就是 Ch 19 說的函式指標表 dispatch 的最終落點——`io_read`、`io_accept`、`io_timeout`…任何一個 op 的本體在這裡被呼叫。前後包了兩層「慢路徑」處理，用 `REQ_ISSUE_SLOW_FLAGS` 先過濾，常態請求完全不碰：

```c
/* io_uring/io_uring.c */
#define REQ_ISSUE_SLOW_FLAGS	(REQ_F_CREDS | REQ_F_ARM_LTIMEOUT)
```

- **`REQ_F_CREDS`**：請求指定了 personality（Ch 23 的 `sqe->personality`），執行前 `override_creds` 換身分、執行後 `revert_creds` 換回。
- **`REQ_F_ARM_LTIMEOUT`**：請求掛了 linked timeout，執行前 arm（`__io_prep_linked_timeout`）、執行後把 timeout 真正排上（`io_queue_linked_timeout`）。這是 Ch 17/30 linked timeout 的 issue 端。

`audit_uring_entry/exit` 是 Linux audit 子系統的掛點（`def->audit_skip` 為真的 op 跳過，像 NOP/READ 這種高頻 op 都 skip 以省成本）。

## -EAGAIN 的補救：io_queue_async 的分岔

op 回了 `-EAGAIN`（會阻塞），`io_queue_sqe` 把它送到這裡做分岔決定：

```c
/* io_uring/io_uring.c */
static void io_queue_async(struct io_kiocb *req, unsigned int issue_flags, int ret)
	__must_hold(&req->ctx->uring_lock)
{
	if (ret != -EAGAIN || (req->flags & REQ_F_NOWAIT)) {
fail:
		io_req_defer_failed(req, ret);
		return;
	}

	ret = io_req_sqe_copy(req, issue_flags);
	if (unlikely(ret))
		goto fail;

	switch (io_arm_poll_handler(req, 0)) {
	case IO_APOLL_READY:
		io_req_task_queue(req);
		break;
	case IO_APOLL_ABORTED:
		io_queue_iowq(req);
		break;
	case IO_APOLL_OK:
		break;
	}
}
```

這就是三條岔路的分叉點：

- **不是 `-EAGAIN`，或請求標了 `REQ_F_NOWAIT`**（user 要求「不准等」）：直接失敗（`io_req_defer_failed`，最終以負 errno 貼 CQE）。`NOWAIT` 的請求寧可回 `-EAGAIN` 給 user，也不退 io-wq——這是給「我自己管 polling，別幫我阻塞」的應用用的。
- **否則先 `io_req_sqe_copy`**：把 SQE 內容拷貝進 req（Ch 23 說過，延後執行不能依賴共享記憶體的 SQE）。
- **然後 `io_arm_poll_handler` 嘗試掛 poll**，三種結果：
  - **`IO_APOLL_OK`**：poll 掛成功。請求就此掛起，等 fd 就緒時 poll 的 task_work 會重新 issue（走 `io_poll_issue`，Ch 28）。這是**岔路 (B)**，也是網路 I/O 的主路徑——比 io-wq 便宜得多，不占 worker thread。
  - **`IO_APOLL_READY`**：掛的瞬間就發現已就緒，直接排 task_work 立刻重試（`io_req_task_queue`）。
  - **`IO_APOLL_ABORTED`**：這個 fd 不能 poll（例如一般檔案的 buffered read），只好退給 **io-wq**（`io_queue_iowq`，**岔路 (C)**），由 worker thread 真的阻塞執行。

**為什麼 poll 優先於 io-wq？** poll 不需要一條 worker thread 整個阻塞在那——它只是在 fd 的 wait queue 掛個 callback，就緒時喚醒。一萬個等資料的 socket，poll 模式幾乎零 thread 成本；io-wq 模式要一萬個 worker 阻塞著，不可行。所以**可 poll 的 fd 一律走 poll，io-wq 是給不可 poll 的（主要是 regular file I/O）保底的**。這是 io_uring 網路效能的根基（Ch 28），也糾正一個常見誤解：io-wq 不是 io_uring 非同步的主力，是最後手段。

## io-wq 端的 issue：沒有 NONBLOCK 的世界

請求退到 io-wq 後，worker thread 從 `io_wq_submit_work` 重新執行它。這裡是「可以阻塞」的世界，看它和 inline 路徑的差異：

```c
/* io_uring/io_uring.c, io_wq_submit_work()（節錄） */
void io_wq_submit_work(struct io_wq_work *work)
{
	struct io_kiocb *req = container_of(work, struct io_kiocb, work);
	const struct io_issue_def *def = &io_issue_defs[req->opcode];
	unsigned int issue_flags = IO_URING_F_UNLOCKED | IO_URING_F_IOWQ;
	bool needs_poll = false;
	int ret = 0, err = -ECANCELED;

	/* one will be dropped by io_wq_free_work() after returning to io-wq */
	if (!(req->flags & REQ_F_REFCOUNT))
		__io_req_set_refcount(req, 2);
	else
		req_ref_get(req);
	/* ... cancel / multishot / force_async 處理 ... */

	do {
		ret = io_issue_sqe(req, issue_flags);
		if (ret != -EAGAIN)
			break;

		if (req->flags & REQ_F_NOWAIT)
			break;

		if (!needs_poll) {
			if (!(req->ctx->flags & IORING_SETUP_IOPOLL))
				break;
			if (io_wq_worker_stopped())
				break;
			cond_resched();
			continue;
		}

		if (io_arm_poll_handler(req, issue_flags) == IO_APOLL_OK)
			return;
		/* aborted or ready, in either case retry blocking */
		needs_poll = false;
		issue_flags &= ~IO_URING_F_NONBLOCK;
	} while (1);

	/* avoid locking problems by failing it from a clean context */
	if (ret)
		io_req_task_queue_fail(req, ret);
}
```

對照 inline 路徑的三個關鍵差異：

- **`issue_flags = IO_URING_F_UNLOCKED | IO_URING_F_IOWQ`**，**沒有 `NONBLOCK`**。worker 就是來阻塞的，所以 op 這次會真的睡著等 I/O，做完回 `IOU_COMPLETE`。`UNLOCKED` 告訴 op「你不在 `uring_lock` 下，碰共享狀態要自己上鎖」。
- **引用計數一定開著**：Ch 21 看過的 `__io_req_set_refcount(req, 2)`——因為 worker 和 `io_wq_free_work` 兩方持有請求。這是「多方持有才啟用 refcount」原則的具體落點。
- **`do/while` 重試迴圈**：為了 IOPOLL 和 poll 的邊角情況保留的重試。一般情況 op 一次就 `IOU_COMPLETE` 跳出。

worker 完成後，完成處理不直接在 worker context 做，而是 `io_req_task_queue_fail`/正常完成導回 task_work（避免在 worker 的「髒」context 碰需要特定 context 的完成邏輯）——這銜接 Ch 25/27。

## 底層機制：為什麼「先賭非阻塞」是對的

這套「NONBLOCK 先試、EAGAIN 再補救」的設計，賭的是一個經驗事實：**大多數 I/O 其實不會阻塞**。read 的資料常在 page cache、socket 常已有資料、accept 的連線常已在佇列。對這些,inline 非阻塞嘗試一次命中，請求在提交 context 當場完成，連 task_work 都不排——這是 io_uring 比 thread-pool 式非同步快的根本原因：**快路徑沒有任何 context switch**。

只有真的會睡的少數情況才付出代價（arm poll 或退 io-wq）。而即使付代價，poll 路徑也只是掛個 callback，成本遠低於「每個請求一條 thread」。這就是為什麼 io_uring 能用少量 thread 扛十萬級並行連線。

代價是**複雜度與攻擊面**：一個請求可能 inline 試一次、arm poll、就緒後 task_work 重試、再退 io-wq……跨好幾個 context 和時間點（Ch 21 的生命週期圖）。每次跨越都是一次「誰持有這個請求、它現在什麼狀態」的同步問題，也是 Part 4 race/UAF 的溫床。效能和攻擊面在這裡是同一枚硬幣的兩面。

## 對比與取捨：三條岔路的成本

| 岔路 | 觸發 | 成本 | 典型場景 |
|---|---|---|---|
| (A) inline 完成 | 非阻塞一次命中 | 最低，無額外 context | 資料在 cache、socket 有資料 |
| (B) armed poll | `-EAGAIN` 且 fd 可 poll | 低，掛 callback + 一次 task_work 重試 | 網路 I/O 等待（socket 沒資料） |
| (C) io-wq | `-EAGAIN` 且 fd 不可 poll | 高，占一條 worker thread 阻塞 | buffered regular file I/O |

設計目標是讓絕大多數請求落在 (A)/(B)，(C) 盡量少。所以用 O_DIRECT + 可 poll 的設計、或用 registered buffer 走更快的路徑，都是在避免落入 (C)。`IOSQE_ASYNC`（`REQ_F_FORCE_ASYNC`）則是反向：強制直接走 (C)，用在「我不想讓這個工作在提交 context 跑」的少數場景。

## 踩雷集錦

1. **以為 io-wq 是 io_uring 非同步的主力**。不是。可 poll 的 fd 一律優先走 poll（岔路 B），io-wq（C）是給不可 poll 的 regular file I/O 保底。網路 I/O 幾乎不碰 io-wq。
2. **把 `-EAGAIN` 當成錯誤回給 user**。在 io_uring 內部，op 回 `-EAGAIN`（`IOU_RETRY`）是「我會阻塞，請重試」的信號，觸發 poll/io-wq 補救，**不是** user 看到的錯誤。只有 `REQ_F_NOWAIT` 的請求才會把 `-EAGAIN` 真的傳回 user。
3. **以為 `def->issue()` 只被呼叫一次**。同一個 op 的 issue 可能被呼叫多次：inline 非阻塞試一次、退 io-wq 後阻塞試一次。它靠 `issue_flags` 有沒有 `NONBLOCK` 知道這次能不能睡。
4. **搞混三個回傳碼**。`IOU_COMPLETE`(0) = 做完了、`IOU_ISSUE_SKIP_COMPLETE`(-EIOCBQUEUED) = 我接管了別管我、`IOU_RETRY`(-EAGAIN) = 會阻塞請重試。讀 op 實作要盯它回哪個。
5. **忽略 io-wq 路徑沒有 NONBLOCK**。inline 路徑帶 `NONBLOCK`、io-wq 路徑帶 `UNLOCKED|IOWQ` 不帶 NONBLOCK。同一行 `def->issue(req, issue_flags)` 在兩個 context 行為天差地別，別假設 issue 永遠非阻塞。
6. **忘了 linked timeout 在 `__io_issue_sqe` 前後 arm/queue**。`REQ_F_ARM_LTIMEOUT` 的請求在 issue 前後有額外動作，這是 Ch 17 linked timeout 的 issue 端落點，推理 timeout race 時要記得。

## 進階：multishot 的 issue 與 io_poll_issue

Ch 13 的 multishot（一個請求產生多個 CQE，像 multishot accept/recv）在 issue 端有獨立的進入點。當 poll 就緒要重新執行一個 poll-armed 請求時，走的不是 `io_issue_sqe` 而是 `io_poll_issue`：

```c
/* io_uring/io_uring.c */
int io_poll_issue(struct io_kiocb *req, io_tw_token_t tw)
{
	const unsigned int issue_flags = IO_URING_F_NONBLOCK |
					 IO_URING_F_MULTISHOT |
					 IO_URING_F_COMPLETE_DEFER;
	int ret;

	io_tw_lock(req->ctx, tw);

	WARN_ON_ONCE(!req->file);
	if (WARN_ON_ONCE(req->ctx->flags & IORING_SETUP_IOPOLL))
		return -EFAULT;

	ret = __io_issue_sqe(req, issue_flags, &io_issue_defs[req->opcode]);

	WARN_ON_ONCE(ret == IOU_ISSUE_SKIP_COMPLETE);
	return ret;
}
```

注意 `issue_flags` 帶了 `IO_URING_F_MULTISHOT`——這告訴 op：「這次是從 poll 回來的 multishot 執行，你可以貼一個 CQE（帶 `IORING_CQE_F_MORE` 表示還有更多）然後**保持 armed**，而不是完成並釋放。」`io_tw_token_t tw` 代表它跑在 task_work context（Ch 27）：此時已持 `uring_lock`、完成會被 flush，所以能安全 `COMPLETE_DEFER`。這條路徑是 Ch 28 poll 就緒後把請求「重新 issue」的落點，和 Ch 24 正文的「首次 issue」是同一個 `__io_issue_sqe`，但 context 與 flags 不同。

這解釋了 `io_wq_submit_work` 為什麼刻意**禁止 multishot 在 io-wq 跑**（正文那段 `if (req->flags & (REQ_F_MULTISHOT|REQ_F_APOLL_MULTISHOT))`）：multishot 要持續貼 auxiliary CQE，而在 DEFER_TASKRUN 下 auxiliary CQE 只能由提交者 task 貼（Ch 20 的 `task_complete`）。io-wq worker 不是提交者 task，所以 io_uring 乾脆讓 multishot 一律走 poll（arm poll handler），不下放 io-wq——「比必要更嚴格，但更乾淨」（原註解語）。這是 Ch 24 三條岔路規則的一個重要例外：multishot 永遠不落 (C)。

`IORING_SETUP_HYBRID_IOPOLL` 是另一個進階變體：IOPOLL 模式下，對會短暫阻塞的請求，先睡一小段再開始 busy poll，平衡「純 busy poll 燒 CPU」和「純中斷有延遲」。它的狀態藏在 `io_kiocb` 的 `iopoll_start`（Ch 21 那個 union 欄位）與 `REQ_F_IOPOLL_STATE` 旗標，Ch 41 效能章會展開。

## 本章重點整理

- `io_queue_sqe` 帶 `NONBLOCK | COMPLETE_DEFER` 呼叫 `io_issue_sqe`；回 0 代表搞定，非 0（`-EAGAIN`）交 `io_queue_async` 補救。
- `io_issue_sqe`：`io_assign_file` 綁 file（解不到 `-EBADF`）→ `__io_issue_sqe` 呼叫 `def->issue()` → 依回傳碼分流。
- op 與引擎的協定只有三個回傳碼：`IOU_COMPLETE`(0,做完)、`IOU_ISSUE_SKIP_COMPLETE`(-EIOCBQUEUED,op 接管)、`IOU_RETRY`(-EAGAIN,會阻塞請重試)。
- `issue_flags` 攜帶執行指令，`IO_URING_F_NONBLOCK=INT_MIN`（最高位，快判斷）；inline 帶 NONBLOCK、io-wq 不帶（worker 可睡）。
- `-EAGAIN` 的三岔：inline 命中(A)最快、可 poll 的 fd arm poll(B)、不可 poll 的退 io-wq(C)。poll 優先於 io-wq，因為 poll 幾乎零 thread 成本。
- 「先賭非阻塞」讓絕大多數請求走無 context switch 的快路徑；代價是跨 context 的複雜生命週期，即 Part 4 race/UAF 的根源。

## 自我檢核

- [ ] 我能畫出 `-EAGAIN` 後 inline / poll / io-wq 三條岔路，以及各自的觸發條件與成本。
- [ ] 我能解釋 `IOU_COMPLETE` / `IOU_ISSUE_SKIP_COMPLETE` / `IOU_RETRY` 三個回傳碼各代表什麼。
- [ ] 我知道 inline 路徑和 io-wq 路徑的 `issue_flags` 差在哪、為什麼 io-wq 不帶 NONBLOCK。
- [ ] 我能說出為什麼 poll 優先於 io-wq，以及哪種 fd 會落到 io-wq。
- [ ] 我能解釋 `-EAGAIN` 在 io_uring 內部和回給 user 的 `-EAGAIN` 有什麼不同。

## 延伸閱讀

- **`io_uring/io_uring.c`（`io_issue_sqe`、`__io_issue_sqe`、`io_queue_async`、`io_wq_submit_work`）與 `io_uring/io_uring.h`（`IOU_*` 列舉）** — 6.18
  - **讀哪裡**：本章四個函式；對照 `IOU_*` 的值理解回傳碼協定
- **本課 [Ch 28 poll 機制內部](./28-poll-internals.md)、[Ch 26 io-wq worker pool](./26-io-wq.md)**
  - **讀哪裡**：岔路 (B)/(C) 的下游實作；本章只到分岔點，它們接續講 poll arm 與 worker 執行
- **本課 [Ch 9 提交模型](./09-submission-models.md)、[Ch 12 網路 I/O](./12-network-io.md)**
  - **讀哪裡**：user 視角為什麼網路 I/O 走 poll、regular file 走 io-wq，和本章岔路對照
- **[Efficient IO with io_uring](https://kernel.dk/io_uring.pdf)** — Jens Axboe
  - **讀哪裡**：非阻塞嘗試與 async punt 的設計說明，第一手動機
- **kernel_internals [Ch 30 softirq 與 workqueue](../kernel_internals/30-softirq-workqueue.md)**
  - **讀哪裡**：worker thread/workqueue 的底層，理解 io-wq 退回路徑的機制基礎

請求的執行我們看到了當場完成那一刻。下一章接著 `io_req_complete_defer` / `io_req_complete_post`，看結果怎麼變成 CQE 填進 CQ 環、完成批次怎麼 flush、CQ overflow 怎麼處理——把 Ch 2 user 端「收 CQE」的 kernel 另一面補齊。

→ [Ch 25 完成路徑：io_req_complete 與 CQE 填寫](./25-completion-path.md)
