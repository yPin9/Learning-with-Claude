# Ch 28 — poll 機制內部：armed poll、double poll

> **目標**：搞懂 io_uring 處理「可 poll 的 fd 但此刻沒就緒」時最精妙的機制。你會看懂 armed poll（把操作掛上 file 的 waitqueue、就緒才真正執行，完全不佔 io-wq worker）、double poll（一個請求同時等多個 waitqueue）、`io_poll_wake` 的喚醒路徑、poll 的引用計數（`poll_refs`）如何在「喚醒」和「執行」之間防 race，以及 multishot poll 怎麼重掛。這是 io_uring 高效的關鍵機制之一。

> **環境**：kernel 6.18 原始碼（`v6.18`，commit `7d0a66e`）。對照 `io_uring/poll.c`、`io_uring/poll.h`。

## 為什麼需要 armed poll

Ch 26 說 io-wq 是處理「會阻塞」操作的後備：開一個獨立 thread 去替你睡。但對 socket/pipe 這類 fd，用 io-wq 其實很浪費。想像一個伺服器掛一萬個 `recv`，每個都在等對端資料——如果每個都佔一個 io-wq worker 去空等，你就要一萬個 kernel thread 睡在那裡。這正是 io_uring 想取代的 thread-per-connection 模型。

socket/pipe 有個 io-wq 沒利用的性質：它們**支援 poll**。你可以把一個操作「掛」到 file 的 poll waitqueue 上，然後**什麼都不做**——不佔 thread、不佔 worker。等 file 就緒（有資料可讀/可寫），kernel 的網路堆疊會喚醒 waitqueue，io_uring 這時才真正去跑那個 `recv`。一萬個等待中的 `recv` 只是一萬個掛在 waitqueue 上的小結構，零 thread。

這就是 **armed poll**（武裝 poll）：操作先「上膛」掛好，就緒才「擊發」執行。它是 io_uring 能用極少資源撐起海量並發連線的核心。Ch 24 提過 issue 時先試 `NONBLOCK`、拿到 `-EAGAIN`；對可 poll 的 fd，拿到 `-EAGAIN` 的下一步不是 io-wq，而是 `io_arm_poll_handler`——掛 poll。

## 先建立直覺：上膛、等待、擊發

```
   io_read/io_recv 以 NONBLOCK 試跑 ──► -EAGAIN（沒資料）
                        │
              可以 poll 嗎？（socket/pipe 可，regular file 不可）
                        │ 可
                        ▼
   io_arm_poll_handler ──► io_arm_apoll ──► __io_arm_poll_handler
                        │
            配一個 async_poll，init io_poll（內嵌 wait_queue_entry）
                        │
            vfs_poll(file, &pt)  ──►  file 的 ->poll 把我們的 wait entry
                        │              加進它的 waitqueue（io_poll_queue_proc）
                        ▼
   ┌──────── 掛好了，請求「休眠」在 waitqueue 上，零 thread ────────┐
   │                                                              │
   │   ... 時間流逝，網路堆疊收到資料 ...                            │
   │                                                              │
   └──► file 喚醒 waitqueue ──► io_poll_wake（我們的 wait callback）──┘
                        │
            搶 ownership（poll_refs），設好 mask
                        │
            __io_poll_execute ──► 排一個 task_work（io_poll_task_func）
                        │
            回到提交者 task：io_poll_check_events
                        │ 重新 vfs_poll 確認就緒
                        ▼
            真正執行操作（io_poll_issue → 重跑 io_read/io_recv）
                        │
            oneshot：完成填 CQE；multishot：填一筆 CQE_F_MORE，重掛繼續等
```

幾個名詞先認識：

- **`io_poll`**：一個內嵌 `wait_queue_entry` 的小結構，是「掛在 file waitqueue 上的那個鉤子」。
- **`async_poll`**：包著 `io_poll` 的外層，給「本來是 read/recv 等操作、臨時需要 poll」用（相對於使用者直接下的 `IORING_OP_POLL_ADD`）。
- **armed poll**：泛指這整套「掛上 waitqueue 等就緒」機制。
- **擊發**不是在喚醒當下直接跑操作，而是排一個 **task_work**（Ch 27）送回提交者 task 再跑——因為喚醒可能發生在網路軟中斷 context，不能直接做複雜 I/O。

## 結構：io_poll 與 async_poll

```c
// io_uring/poll.h
struct io_poll {
	struct file			*file;
	struct wait_queue_head		*head;
	__poll_t			events;
	int				retries;
	struct wait_queue_entry		wait;
};

struct async_poll {
	struct io_poll		poll;
	struct io_poll		*double_poll;
};
```

`io_poll.wait` 就是掛進 file waitqueue 的那個 entry，它的 callback 是 `io_poll_wake`。`head` 記著掛在哪個 waitqueue 上（為了之後摘除）。`async_poll` 多一個 `double_poll` 指標——為什麼需要「第二個」poll，下面 double poll 一節講。

poll table（驅動 `vfs_poll` 的上下文）：

```c
// io_uring/poll.c
struct io_poll_table {
	struct poll_table_struct pt;
	struct io_kiocb *req;
	int nr_entries;
	int error;
	bool owning;
	__poll_t result_mask;
};
```

`nr_entries` 數這個請求掛上了幾個 waitqueue——正常 1 個，碰到 double poll 變 2 個。

## 上膛：`__io_arm_poll_handler`

不管是 user 直接下的 `IORING_OP_POLL_ADD`（走 `io_poll_add`）還是操作臨時轉 poll（走 `io_arm_apoll`），核心都是 `__io_arm_poll_handler`（`io_uring/poll.c`）：

```c
static int __io_arm_poll_handler(struct io_kiocb *req,
				 struct io_poll *poll,
				 struct io_poll_table *ipt, __poll_t mask,
				 unsigned issue_flags)
{
	INIT_HLIST_NODE(&req->hash_node);
	io_init_poll_iocb(poll, mask);
	poll->file = req->file;
	req->apoll_events = poll->events;

	ipt->pt._key = mask;
	ipt->req = req;
	ipt->error = 0;
	ipt->nr_entries = 0;
	...
	ipt->owning = issue_flags & IO_URING_F_UNLOCKED;
	atomic_set(&req->poll_refs, (int)ipt->owning);
	...
	mask = vfs_poll(req->file, &ipt->pt) & poll->events;

	if (unlikely(ipt->error || !ipt->nr_entries)) {
		io_poll_remove_entries(req);
		...
	}

	if (mask &&
	   ((poll->events & (EPOLLET|EPOLLONESHOT)) == (EPOLLET|EPOLLONESHOT))) {
		...
	}

	io_poll_add_hash(req, issue_flags);

	if (mask && (poll->events & EPOLLET) &&
	    io_poll_can_finish_inline(req, ipt)) {
		__io_poll_execute(req, mask);
		return 0;
	}
	io_napi_add(req);

	if (ipt->owning) {
		if (atomic_cmpxchg(&req->poll_refs, 1, 0) != 1)
			__io_poll_execute(req, 0);
	}
	return 0;
}
```

核心是 `mask = vfs_poll(req->file, &ipt->pt)`。`vfs_poll` 呼叫 file 的 `->poll` 方法，它做兩件事：

1. 回傳 file **現在**的就緒遮罩（有沒有資料可讀等）。
2. 透過 poll table 的 callback（`io_poll_queue_proc`），把我們的 `io_poll.wait` entry **加進 file 的 waitqueue**。

這第二件事就是「上膛」——掛好之後，file 一就緒就會回呼我們。如果 `vfs_poll` 回來發現**當下就已就緒**（`mask` 非零）且是 edge-triggered oneshot，可以 `io_poll_can_finish_inline` 直接就地執行，連 task_work 都省。否則掛著等。

`req->poll_refs` 這個原子引用計數是整個機制防 race 的關鍵，稍後專門講。

## 掛上 waitqueue：`io_poll_queue_proc` 與 `__io_queue_proc`

`vfs_poll` 內部會回呼 poll table 的 `_qproc`，對 io_uring 就是 `io_poll_queue_proc` → `__io_queue_proc`（`io_uring/poll.c`）。這個函式藏著 double poll 的全部邏輯：

```c
static void __io_queue_proc(struct io_poll *poll, struct io_poll_table *pt,
			    struct wait_queue_head *head,
			    struct io_poll **poll_ptr)
{
	struct io_kiocb *req = pt->req;
	unsigned long wqe_private = (unsigned long) req;

	/*
	 * The file being polled uses multiple waitqueues for poll handling
	 * (e.g. one for read, one for write). Setup a separate io_poll
	 * if this happens.
	 */
	if (unlikely(pt->nr_entries)) {
		struct io_poll *first = poll;

		/* double add on the same waitqueue head, ignore */
		if (first->head == head)
			return;
		/* already have a 2nd entry, fail a third attempt */
		if (*poll_ptr) {
			if ((*poll_ptr)->head == head)
				return;
			pt->error = -EINVAL;
			return;
		}

		poll = kmalloc(sizeof(*poll), GFP_ATOMIC);
		if (!poll) {
			pt->error = -ENOMEM;
			return;
		}

		wqe_private |= IO_WQE_F_DOUBLE;
		io_init_poll_iocb(poll, first->events);
		if (!io_poll_double_prepare(req)) {
			kfree(poll);
			return;
		}
		*poll_ptr = poll;
	} else {
		req->flags |= REQ_F_SINGLE_POLL;
	}

	pt->nr_entries++;
	poll->head = head;
	poll->wait.private = (void *) wqe_private;

	if (poll->events & EPOLLEXCLUSIVE)
		add_wait_queue_exclusive(head, &poll->wait);
	else
		add_wait_queue(head, &poll->wait);
}
```

## double poll：一個請求等多個 waitqueue

為什麼 `__io_queue_proc` 要處理「第二個 entry」？因為**有些 file 用多個 waitqueue 做 poll**。最典型的是「同時 poll 讀和寫」的場景，或某些 file（如特定 driver、tty）內部對不同事件維護不同 waitqueue。當 `vfs_poll` 要把你掛上去時，file 的 `->poll` 可能呼叫 `poll_wait()` **不只一次**，每次指向不同的 waitqueue head。

一個 `io_poll` 只內嵌**一個** `wait_queue_entry`，只能掛一個 waitqueue。碰到 file 要掛第二個，`__io_queue_proc` 就 `kmalloc` 出**第二個** `io_poll`（存在 `async_poll->double_poll`），掛上第二個 waitqueue，並在它的 `wait.private` 標記 `IO_WQE_F_DOUBLE`。這就是 **double poll**：一個請求透過兩個 `io_poll` entry，同時掛在兩個 waitqueue 上，任一就緒都能喚醒它。

第三個 waitqueue 怎麼辦？`pt->error = -EINVAL`——io_uring 只支援到 double，超過就拒絕。這是刻意的複雜度上限。

double poll 也是歷史上 bug 的高發區：那個額外 `kmalloc` 的 `io_poll` 生命週期要和請求、和兩個 waitqueue 的喚醒/摘除精確同步，稍有差池就 UAF。`io_poll_double_prepare` 和 `poll_refs` 就是在守這個。Part 4 會回來看具體 CVE。

## 擊發：`io_poll_wake` 的喚醒路徑

file 就緒時，網路堆疊（或任何 waitqueue 持有者）呼叫 `wake_up`，觸發我們掛上去的 wait callback `io_poll_wake`（`io_uring/poll.c`）：

```c
static int io_poll_wake(struct wait_queue_entry *wait, unsigned mode, int sync,
			void *key)
{
	struct io_kiocb *req = wqe_to_req(wait);
	struct io_poll *poll = container_of(wait, struct io_poll, wait);
	__poll_t mask = key_to_poll(key);

	if (unlikely(mask & POLLFREE))
		return io_pollfree_wake(req, poll);

	/* for instances that support it check for an event match first */
	if (mask && !(mask & (poll->events & ~IO_ASYNC_POLL_COMMON)))
		return 0;

	if (io_poll_get_ownership(req)) {
		if (mask & EPOLL_URING_WAKE)
			poll->events |= EPOLLONESHOT;

		if (mask && poll->events & EPOLLONESHOT) {
			list_del_init(&poll->wait.entry);
			poll->head = NULL;
			if (wqe_is_double(wait))
				req->flags &= ~REQ_F_DOUBLE_POLL;
			else
				req->flags &= ~REQ_F_SINGLE_POLL;
		}
		__io_poll_execute(req, mask);
	}
	return 1;
}
```

幾個關鍵：

- **事件過濾**：`mask && !(mask & poll->events)`——如果喚醒帶的事件不是我們要的（例如我們等 `EPOLLIN`，但喚醒是因 `EPOLLOUT`），直接 `return 0` 不理。
- **`EPOLL_URING_WAKE` 自我偵測**：如果喚醒的 key 帶這個 flag，代表這是一個「io_uring 喚醒鏈遞迴回自己」的情況（例如 poll 一個 eventfd，而那 eventfd 又被 io_uring 完成觸發）。此時強制設 `EPOLLONESHOT`，**打斷 multishot 的循環依賴**，避免無窮喚醒。這呼應 Ch 25 `io_cqring_wake` 用的那個 key。
- **`io_poll_get_ownership`**：搶 ownership（增 `poll_refs`）。搶到才 `__io_poll_execute` 去排 task_work；搶不到代表已有別人在處理，這次喚醒作罷。

重點：`io_poll_wake` **不直接跑操作**。它只是搶到 ownership、記下 mask、排一個 task_work。真正執行延到 task context——因為喚醒常發生在 softirq（網路軟中斷），不能在那裡做複雜 I/O 或拿 mutex。

## poll_refs：喚醒與執行之間的 race 防護

這是 poll 內部最難但最核心的一環。一個 armed poll 面臨多方並發：

- file 的 waitqueue 可能**同時**從多個 CPU 喚醒它（double poll 的兩個 entry 一起就緒）。
- cancel（Ch 30）可能同時想取消它。
- task_work 可能正在執行它。

io_uring 用 `req->poll_refs`（一個原子計數 + 幾個 flag 位元）當**狀態機**來協調。定義（`io_uring/poll.c`）：

```c
#define IO_POLL_CANCEL_FLAG	BIT(31)
#define IO_POLL_RETRY_FLAG	BIT(30)
#define IO_POLL_REF_MASK	GENMASK(29, 0)
```

「搶 ownership」就是對 `poll_refs` 做原子加。`io_poll_check_events`（task_work 執行時跑的）靠它判斷「我處理期間有沒有新的喚醒進來」（`io_uring/poll.c`）：

```c
static int io_poll_check_events(struct io_kiocb *req, io_tw_token_t tw)
{
	int v;
	...
	do {
		v = atomic_read(&req->poll_refs);

		if (unlikely(v != 1)) {
			if (WARN_ON_ONCE(!(v & IO_POLL_REF_MASK)))
				return IOU_POLL_NO_ACTION;
			if (v & IO_POLL_CANCEL_FLAG)
				return -ECANCELED;
			if ((v & IO_POLL_REF_MASK) != 1)
				req->cqe.res = 0;
			...
		}

		/* the mask was stashed in __io_poll_execute */
		if (!req->cqe.res) {
			struct poll_table_struct pt = { ._key = req->apoll_events };
			req->cqe.res = vfs_poll(req->file, &pt) & req->apoll_events;
			...
		}
		if (req->apoll_events & EPOLLONESHOT)
			return IOU_POLL_DONE;

		/* multishot, just fill a CQE and proceed */
		if (!(req->flags & REQ_F_APOLL_MULTISHOT)) {
			__poll_t mask = mangle_poll(req->cqe.res &
						    req->apoll_events);

			if (!io_req_post_cqe(req, mask, IORING_CQE_F_MORE)) {
				io_req_set_res(req, mask, 0);
				return IOU_POLL_REMOVE_POLL_USE_RES;
			}
		} else {
			int ret = io_poll_issue(req, tw);
			...
		}
		req->cqe.res = 0;

		/*
		 * Release all references, retry if someone tried to restart
		 * task_work while we were executing it.
		 */
		v &= IO_POLL_REF_MASK;
	} while (atomic_sub_return(v, &req->poll_refs) & IO_POLL_REF_MASK);

	io_napi_add(req);
	return IOU_POLL_NO_ACTION;
}
```

這個 `do { ... } while` 迴圈是重點：處理完一輪後 `atomic_sub_return(v, &poll_refs)`，如果結果**還有 ref**（代表處理期間又有新喚醒把計數加上去了），就**再跑一輪**。這保證「處理期間進來的喚醒不會漏」——不會發生「我正在跑 vfs_poll，此時資料又到了、喚醒它、但我已經決定這輪結果了」的漏事件。`poll_refs` 把「有幾個喚醒待處理」編碼成計數，迴圈直到清零。

另外 `vfs_poll` 要**重做一次**：`io_poll_wake` 記的 mask 只是「第一次喚醒看到的」，可能過時。task_work 執行時重新 `vfs_poll` 拿最新狀態，確認真的就緒才跑操作——否則（spurious wakeup）就重掛繼續等。

## multishot poll 的重掛

oneshot poll（`EPOLLONESHOT`）就緒一次、執行一次、填一筆 CQE、結束。**multishot** poll（如 multishot `recv`、multishot `accept`，Ch 13）要「每次就緒都吐一筆 CQE，然後繼續等下一次」。重掛就發生在 `io_poll_check_events` 裡：

- 非 oneshot：`io_req_post_cqe(req, mask, IORING_CQE_F_MORE)` 填一筆帶 **`IORING_CQE_F_MORE`** flag 的 CQE（告訴 user「還會有更多」），然後**不**結束——`req->cqe.res = 0` 重置，迴圈繼續，poll entry 留在 waitqueue 上等下次。
- oneshot：`return IOU_POLL_DONE`，走最終完成。

`IORING_CQE_F_MORE` 是 user 判斷 multishot 是否還活著的信號：帶著 `F_MORE` 表示這個 multishot 請求還在，之後還有 CQE；某次 CQE 不帶 `F_MORE` 就代表它結束了（要重新提交）。

這裡有個 Ch 25 埋的伏筆兌現：`io_req_post_cqe` 開頭會先 `__io_submit_flush_completions` 把攢著的 deferred 完成 flush 掉，**才** post 這筆 aux CQE——否則同一個 multishot 請求的多筆 CQE 可能和 deferred 批次亂序。multishot 的「每次就緒一筆 CQE」順序正確性，就靠這個先 flush 再 post。

另外 `io_poll_wake` 裡那個 `EPOLL_URING_WAKE → 強制 EPOLLONESHOT` 的邏輯，就是 multishot 的安全閥：如果偵測到 multishot 的喚醒形成了對自己的循環依賴，就把它降級成 oneshot，斷開循環，避免 CPU 空轉。

## apoll：操作臨時轉 poll

前面講的 `io_poll` 可以是 user 直接下的 `IORING_OP_POLL_ADD`，但更常見的是**一個 read/recv 操作臨時需要 poll**。`io_arm_poll_handler`（`io_uring/poll.c`）：

```c
int io_arm_poll_handler(struct io_kiocb *req, unsigned issue_flags)
{
	const struct io_issue_def *def = &io_issue_defs[req->opcode];
	__poll_t mask = POLLPRI | POLLERR;

	if (!def->pollin && !def->pollout)
		return IO_APOLL_ABORTED;
	if (!io_file_can_poll(req))
		return IO_APOLL_ABORTED;

	if (def->pollin) {
		mask |= EPOLLIN | EPOLLRDNORM;
		if (req->flags & REQ_F_CLEAR_POLLIN)
			mask &= ~EPOLLIN;
	} else {
		mask |= EPOLLOUT | EPOLLWRNORM;
	}
	if (def->poll_exclusive)
		mask |= EPOLLEXCLUSIVE;

	return io_arm_apoll(req, issue_flags, mask);
}
```

兩個前提：操作本身支援 poll（`def->pollin || def->pollout`，opdef 標的）、且 file 可 poll（`io_file_can_poll`，socket/pipe 可、regular file 不可）。都滿足才 `io_arm_apoll` 配一個 `async_poll` 掛上去；否則 `IO_APOLL_ABORTED`，退回 io-wq（Ch 26）。

這就是 Ch 26 說的「能 poll 就 poll、io-wq 是最後手段」的分流點：`io_read` 拿到 `-EAGAIN` → `io_arm_poll_handler` → 成功就掛 poll（零 thread），失敗才 punt io-wq。對 socket 伺服器，幾乎所有 `-EAGAIN` 都走 poll，io-wq 很少被用到。

`io_arm_apoll` 會設 `EPOLLET`（edge-triggered）：

```c
int io_arm_apoll(struct io_kiocb *req, unsigned issue_flags, __poll_t mask)
{
	struct async_poll *apoll;
	struct io_poll_table ipt;
	int ret;

	mask |= EPOLLET;
	...
	apoll = io_req_alloc_apoll(req, issue_flags);
	...
	ipt.pt._qproc = io_async_queue_proc;
	ret = __io_arm_poll_handler(req, &apoll->poll, &ipt, mask, issue_flags);
	...
}
```

edge-triggered 是刻意的：io_uring 只想在「狀態轉變為就緒」時被喚醒一次，然後自己重做 `vfs_poll` 確認，而不是 level-triggered 那樣只要就緒就反覆喚醒。這和 epoll 的 ET 模式同理，減少無效喚醒。

## 底層機制：為什麼 armed poll 這麼省

把資源帳算一次。傳統 thread-per-connection：一萬個連線 = 一萬個 thread，每個 thread 一個 kernel stack（8KB）+ task_struct，光記憶體就幾十上百 MB，還有排程開銷。

io_uring armed poll：一萬個等待中的 `recv` = 一萬個掛在各自 socket waitqueue 上的 `io_poll` entry（每個幾十 bytes）+ 對應的 `io_kiocb`。**零額外 thread**。喚醒靠網路堆疊本來就有的 waitqueue 機制（收到封包本來就要喚醒等待者），io_uring 只是把自己的 callback 掛進去。執行時用 task_work 回到**唯一一個**提交者 task 批次處理。

這就是 io_uring 在高並發網路服務上輾壓 thread 模型的根本原因：它把「等待」從「佔一個執行實體空轉」變成「掛一個資料結構在既有的喚醒機制上」。armed poll 是這個轉變的實作核心。

## 對比與取捨

| 面向 | armed poll | io-wq（Ch 26） |
|---|---|---|
| 適用 | 可 poll 的 fd（socket/pipe） | 不可 poll（regular file、VFS metadata） |
| 等待成本 | 一個 waitqueue entry，零 thread | 一個 kernel thread 睡著 |
| 就緒通知 | file waitqueue 喚醒 → callback | thread 從阻塞 syscall 返回 |
| 執行 context | task_work 回提交者 task | io-wq worker thread |
| 海量並發 | 極省（io_uring 的招牌） | 吃 thread，不適合海量 |
| 複雜度 | 高（poll_refs 狀態機、double poll） | 中（thread pool） |

取捨：armed poll 省到極致，但複雜度和 race 風險也最高——`poll_refs` 狀態機、double poll 的額外配置、multishot 重掛、循環依賴偵測，每一處都是精密同步。這複雜度直接反映在 Part 4：poll 相關的 UAF/race 是 io_uring CVE 的一大類（Ch 35）。

## 踩雷集錦

1. **以為 `io_poll_wake` 會直接跑操作**：不會。它只搶 ownership、記 mask、排 task_work。真正執行在 task context（`io_poll_check_events` → `io_poll_issue`），因為喚醒常在 softirq，不能在那做 I/O。

2. **忽略 task_work 執行時會重做 `vfs_poll`**：`io_poll_wake` 記的 mask 可能過時/spurious。`io_poll_check_events` 一定重新 `vfs_poll` 確認真就緒，沒就緒就重掛。以為「喚醒了就一定有資料」會對不上。

3. **把 double poll 當常態**：只有「file 用多個 waitqueue poll」才觸發第二個 `io_poll`（額外 kmalloc）。大多數 socket 是單 waitqueue，single poll。但 double poll 的額外配置正是歷史 UAF 高發區，讀 code 要特別留意它的生命週期。

4. **不理解 `poll_refs` 的迴圈**：`io_poll_check_events` 的 `do...while(atomic_sub_return...)` 是為了「處理期間進來的新喚醒不漏」。以為處理一次就結束，會看不懂為什麼要迴圈、為什麼 `poll_refs` 要編碼計數。

5. **搞混 multishot 的 `IORING_CQE_F_MORE`**：multishot 每次就緒吐一筆帶 `F_MORE` 的 CQE 且繼續等；某筆不帶 `F_MORE` 才代表結束。user 端要靠這個 flag 判斷 multishot 死活，漏判會以為請求還在、結果它早結束了（Ch 13 應用面）。

6. **regular file 也想 armed poll**：`io_file_can_poll` 擋掉——regular file 永遠「就緒」，poll 沒意義，只能走 io-wq。這是 armed poll 和 io-wq 分流的硬界線。

## 進階：POLLFREE 與 file 在你等待時被關掉

`io_poll_wake` 開頭那個 `if (unlikely(mask & POLLFREE))` 處理一個險惡場景：你掛在某 file 的 waitqueue 上等著，結果那個 file **被關閉、waitqueue 要被銷毀**了。kernel 用 `POLLFREE` 這個特殊 mask 喚醒所有 waiter，意思是「我要死了，你們趕快從我的 waitqueue 上滾下來」。`io_pollfree_wake`（`io_uring/poll.c`）必須在那一刻**同步**把自己的 entry 從即將消失的 waitqueue 摘除，否則就是 UAF——waitqueue 記憶體被釋放後，你的 entry 還指著它。這是 io_uring poll 最棘手的 corner case 之一，`io_poll_double_prepare`、`poll_refs` 的許多複雜度都是為了讓這一刻安全。看 Part 4 的 poll race CVE 時，POLLFREE 路徑會反覆出現。

## 本章重點整理

- **armed poll** 是 io_uring 處理「可 poll 但未就緒」的機制：把操作掛上 file 的 waitqueue，**零 thread** 等待，就緒才執行。這是 io_uring 撐海量並發連線的核心，取代 thread-per-connection。
- 上膛：`__io_arm_poll_handler` → `vfs_poll` 把 `io_poll.wait`（callback = `io_poll_wake`）加進 file waitqueue。擊發：file 就緒 → `io_poll_wake` 搶 ownership、記 mask、排 task_work → 回提交者 task 的 `io_poll_check_events` 重做 `vfs_poll` 確認、執行操作。
- **double poll**：file 用多個 waitqueue 時，`__io_queue_proc` 額外 `kmalloc` 第二個 `io_poll`（存 `async_poll->double_poll`），掛第二個 waitqueue；最多到 double，第三個 `-EINVAL`。是歷史 UAF 高發區。
- **`poll_refs`** 是原子狀態機（含 `CANCEL`/`RETRY` flag + 計數），協調「多方喚醒 / cancel / 執行」的 race；`io_poll_check_events` 的迴圈靠它保證「處理期間進來的喚醒不漏」。
- **multishot** 每次就緒 `io_req_post_cqe(..., IORING_CQE_F_MORE)` 吐一筆 CQE 並重掛繼續等；`F_MORE` 是 user 判斷死活的信號。post 前先 flush deferred 完成以保順序。
- apoll（操作臨時轉 poll）走 `io_arm_poll_handler`，用 `EPOLLET`；前提是操作支援 poll 且 file 可 poll，否則退 io-wq。這是「能 poll 就 poll、io-wq 最後手段」的分流點。
- `EPOLL_URING_WAKE` 自我偵測打斷 multishot 循環依賴；`POLLFREE` 處理「等待中 file 被關」的 UAF 風險。

## 自我檢核

- [ ] 我能講清楚 armed poll 從上膛（掛 waitqueue）到擊發（就緒執行）的完整流程，以及為什麼擊發要經 task_work
- [ ] 我能解釋為什麼 armed poll 比 io-wq 省，並算出海量連線下的資源差距
- [ ] 我知道 double poll 什麼時候產生、存在哪、上限是多少、為什麼危險
- [ ] 我能說出 `poll_refs` 解決什麼 race，以及 `io_poll_check_events` 為什麼要迴圈
- [ ] 我能解釋 multishot 怎麼重掛、`IORING_CQE_F_MORE` 的意義
- [ ] 我知道 apoll 的分流條件，以及 regular file 為什麼不能 armed poll

## 延伸閱讀

### 必讀

- **Linux 6.18 原始碼 `io_uring/poll.c`** — <https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/io_uring/poll.c?h=v6.18>
  - **讀哪裡**：`__io_arm_poll_handler`、`__io_queue_proc`、`io_poll_wake`、`io_poll_check_events` 四個函式，對照本章走一遍上膛→擊發
  - **和本章關聯**：本章所有函式/結構名都可在此 grep 到

### VFS poll 基礎

- **Linux 原始碼 `fs/select.c` 的 `poll_wait` / `vfs_poll`** 與 `include/linux/poll.h`
  - **讀哪裡**：`poll_wait` 怎麼把 wait entry 加進 waitqueue、`poll_table` 的 `_qproc` 機制
  - **和本章關聯**：io_uring 的 armed poll 是建在這套通用 VFS poll 之上，先懂 `vfs_poll` 的 callback 機制，`__io_queue_proc` 才看得懂

### LWN

- **[Operations on poll](https://lwn.net/Articles/)（搜 io_uring poll / multishot）** — LWN io_uring 系列
  - **讀哪裡**：multishot poll、multishot recv/accept 的引入報導
  - **和本章關聯**：multishot 重掛機制「為什麼要」的設計背景

### 安全（前瞻 Part 4）

- **[chompie1337 — Put an io_uring on it](https://chompie.rip/Blog+Posts/Put+an+io_uring+on+it+-+Exploiting+the+Linux+Kernel)**
  - **讀哪裡**：涉及 poll / double poll 生命週期的那段
  - **和本章關聯**：本章 `poll_refs`、double poll、POLLFREE 的複雜度，正是 Ch 35 poll race UAF 的根源，先建立機制印象

poll 讓請求能「零 thread 等就緒」。但無論 io-wq、task_work 還是 poll，它們操作的資源（檔案、buffer）常常是 user 預先 register 進 kernel 的。這些 registered 資源的內部表示、引用計數、回收時機，是下一章的主題——也和 Part 4 多個 CVE 直接相關。

→ [Ch 29 資源管理內部：io_rsrc_node、RCU 回收](./29-rsrc-internals.md)
