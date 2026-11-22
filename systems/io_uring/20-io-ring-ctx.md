# Ch 20 — io_ring_ctx 全解剖

> **目標**：把 `struct io_ring_ctx`——「一個 io_uring 實例的全部 kernel 狀態」——逐欄位分組拆開。讀完這一章，你看到任何 io_uring 函式裡的 `ctx->xxx`，都知道它屬於哪一組、被哪把鎖保護、在熱路徑還是冷路徑。這是讀懂 Ch 22–30 每一條路徑的前提。

> **環境**：kernel 6.18 原始碼，`include/linux/io_uring_types.h`（`struct io_ring_ctx` 定義在第 258–469 行）。

## 為什麼要先征服這一個 struct？

Ch 2 你在 user 端建立了一個 ring：一個 fd、三塊 mmap。在 kernel 這一側，那個 fd 的 `file->private_data` 指向的，就是**一個 `struct io_ring_ctx`**。它是整個實例的中樞：SQ/CQ 環的指標、所有 in-flight 請求的歸屬、每一把鎖、registered 資源表、io-wq、task_work 佇列、cancel/timeout/overflow 的各種 list——全部掛在這一個結構上。

你在 Part 3 讀的每一個函式，第一個參數幾乎都是 `struct io_ring_ctx *ctx`。`io_submit_sqes(ctx, nr)`、`io_cqring_wait(ctx, …)`、`io_ring_ctx_wait_and_kill(ctx)`。不先把 `ctx` 的欄位分組記熟，讀那些函式就像讀一本每個代名詞都不知道指誰的小說。

更重要的是：**這個 struct 的「物理排版」本身就是一堂效能課**。它不是隨便宣告的，欄位被刻意按「存取頻率 + 誰會碰 + 哪把鎖」分成好幾個 `____cacheline_aligned_in_smp` 的 block，讓不同 CPU 碰不同 cacheline，避免 false sharing。讀懂這個排版，你就懂了 io_uring 效能設計的一半。

## 先建立直覺：ctx 是一棟分區的大樓

```
   struct io_ring_ctx  （一個 io_uring 實例）
   ┌──────────────────────────────────────────────────────┐
   │ [cacheline A] const / read-mostly 熱資料                │
   │   flags, 一堆 1-bit 狀態, submitter_task, *rings, refs  │
   ├──────────────────────────────────────────────────────┤
   │ [cacheline B] 提交側（submission）                       │
   │   uring_lock(mutex), sq_array, sq_sqes, cached_sq_head  │
   │   file_table, buf_table, 各種 alloc_cache, submit_state │
   ├──────────────────────────────────────────────────────┤
   │ [cacheline C] 完成側（completion）                       │
   │   cqe_cached/sentinel, cached_cq_tail, cq_entries,io_ev_fd│
   ├──────────────────────────────────────────────────────┤
   │ [cacheline D] task_work / 非同步喚醒                     │
   │   work_llist, retry_llist, check_cq, cq_wait            │
   ├──────────────────────────────────────────────────────┤
   │ [cacheline E] timeouts                                  │
   │   timeout_lock, timeout_list, ltimeout_list             │
   ├──────────────────────────────────────────────────────┤
   │ 其餘：completion_lock, cq_overflow_list, futex_list,     │
   │   sq_data(SQPOLL), personalities, mm_account,           │
   │   teardown(exit_work, ref_comp, tctx_list), mmap 區      │
   └──────────────────────────────────────────────────────┘
```

每條橫線大致是一個 cacheline 邊界。核心設計原則：**提交側和完成側分開**，因為在 DEFER_TASKRUN 這類模式下它們可能在不同 CPU 跑；**會跨 CPU bounce 的 task_work 欄位獨立一行**，免得拖累提交側的 cacheline。下面逐組看。

## 第一組：const / read-mostly 熱資料

```c
/* include/linux/io_uring_types.h, struct io_ring_ctx 開頭 */
struct io_ring_ctx {
	/* const or read-mostly hot data */
	struct {
		unsigned int		flags;
		unsigned int		drain_next: 1;
		unsigned int		restricted: 1;
		unsigned int		off_timeout_used: 1;
		unsigned int		drain_active: 1;
		unsigned int		has_evfd: 1;
		/* all CQEs should be posted only by the submitter task */
		unsigned int		task_complete: 1;
		unsigned int		lockless_cq: 1;
		unsigned int		syscall_iopoll: 1;
		unsigned int		poll_activated: 1;
		unsigned int		drain_disabled: 1;
		unsigned int		compat: 1;
		unsigned int		iowq_limits_set : 1;

		struct task_struct	*submitter_task;
		struct io_rings		*rings;
		struct percpu_ref	refs;

		clockid_t		clockid;
		enum tk_offsets		clock_offset;

		enum task_work_notify_mode	notify_method;
		unsigned			sq_thread_idle;
	} ____cacheline_aligned_in_smp;
```

這一組幾乎只讀不寫，所以放最前面、獨佔 cacheline，讓它能常駐每個 CPU 的 cache：

- **`flags`**：setup 時傳進來的 `IORING_SETUP_*`（SQPOLL、IOPOLL、DEFER_TASKRUN、SINGLE_ISSUER、CQE32、NO_SQARRAY…），整個生命週期不變。幾乎每個路徑都會 `if (ctx->flags & IORING_SETUP_XXX)`。
- **那一排 1-bit 旗標**：都是從 `flags` 推導出來、快取好的 fast-path 判斷。特別記三個：
  - **`task_complete`**：所有 CQE 只能由提交者 task 貼出。DEFER_TASKRUN + 非 IOPOLL + 非 SQPOLL 時為真（Ch 22 的 `io_uring_create()` 設定它）。
  - **`lockless_cq`**：完成端不需要 `completion_lock`。`task_complete` 或 IOPOLL 時為真——因為此時只有一個 context 會碰 CQ。這是 DEFER_TASKRUN 效能的根源，Ch 25/27 會展開。
  - **`drain_active` / `drain_next`**：`IOSQE_IO_DRAIN` 的狀態機，Ch 23 的 `io_init_drain()` 會翻它們。
- **`submitter_task`**：SINGLE_ISSUER 時綁定的唯一提交者 task。很多 lockless 的正確性靠「只有這個 task 會動某些欄位」。
- **`rings`**：指向那塊和 user 共享的 `struct io_rings`（SQ/CQ 的 head/tail/mask 都在裡面）。Ch 2 user 端 mmap 到的就是它。
- **`refs`**：**percpu_ref**，整個 ctx 的主引用計數。只要還有 in-flight 請求或 open 的 fd，它就不為零；歸零才觸發拆除（Ch 30）。用 percpu_ref 是因為提交熱路徑要頻繁 get/put，per-CPU 計數避免 cacheline 爭用。
- **`notify_method`**：task_work 通知方式（`TWA_SIGNAL` / `TWA_SIGNAL_NO_IPI`），COOP_TASKRUN 會選後者省 IPI（Ch 27）。

### ctx->rings：和 user 共享的那一半

第一組裡的 `struct io_rings *rings` 特別值得單獨看——它是 ctx 裡**唯一和 user 共享物理頁**的部分（Ch 22 的 `io_allocate_scq_urings` 分配它，Ch 2 user 端三次 mmap 映射它）。其餘 ctx 欄位都是純 kernel 私有，user 碰不到；只有 `rings` 這塊是 ABI 邊界：

```c
/* include/linux/io_uring_types.h（節錄） */
struct io_rings {
	struct io_uring		sq, cq;        /* 各含 u32 head, tail */
	u32			sq_ring_mask, cq_ring_mask;
	u32			sq_ring_entries, cq_ring_entries;
	u32			sq_dropped;
	atomic_t		sq_flags;
	u32			cq_flags;
	u32			cq_overflow;
	struct io_uring_cqe	cqes[] ____cacheline_aligned_in_smp;
};
```

對照 Ch 2 user 端用 `sq_off`/`cq_off` 定位的欄位，這裡就是它們的 kernel 定義：`sq.head`/`sq.tail`（Ch 23 的 `io_commit_sqring` 寫 `sq.head`、user 寫 `sq.tail`）、`cq.head`/`cq.tail`（完成端寫 `cq.tail`、user 寫 `cq.head`）、`sq_dropped`（Ch 23 `io_get_sqe` 遇到越界 index 時 ++）、`cq_overflow`（CQ 滿時 ++）、尾端的 `cqes[]` 彈性陣列（CQE 本體）。

分清楚兩件事：**`rings->sq.head`（共享，給 user 看）和 `ctx->cached_sq_head`（第二組，kernel 私有快取）是兩個東西**。kernel 平時只動 `cached_sq_head`，批次結束才 `smp_store_release` 同步回 `rings->sq.head`。完成側的 `cached_cq_tail` 與 `rings->cq.tail` 同理。這個「kernel 私有快取 + 批次同步回共享」的雙層設計，是 io_uring 減少對共享 cacheline 寫入的關鍵——共享記憶體的寫入會造成 user/kernel 的 cacheline bounce，能少則少。

## 第二組：提交側（submission）

```c
	/* submission data */
	struct {
		struct mutex		uring_lock;

		u32			*sq_array;
		struct io_uring_sqe	*sq_sqes;
		unsigned		cached_sq_head;
		unsigned		sq_entries;

		atomic_t		cancel_seq;

		bool			poll_multi_queue;
		struct io_wq_work_list	iopoll_list;

		struct io_file_table	file_table;
		struct io_rsrc_data	buf_table;
		struct io_alloc_cache	node_cache;
		struct io_alloc_cache	imu_cache;

		struct io_submit_state	submit_state;

		struct xarray		io_bl_xa;

		struct io_hash_table	cancel_table;
		struct io_alloc_cache	apoll_cache;
		struct io_alloc_cache	netmsg_cache;
		struct io_alloc_cache	rw_cache;
		struct io_alloc_cache	cmd_cache;

		struct hlist_head	cancelable_uring_cmd;
		u64			hybrid_poll_time;
	} ____cacheline_aligned_in_smp;
```

這整組都受 **`uring_lock`** 這把 mutex 保護（除了底層自帶鎖的 xarray）。`uring_lock` 是 io_uring 的**主鎖**：提交路徑全程持有它，大部分 register 操作也要它。它是 mutex（可睡眠）而非 spinlock，因為提交過程可能睡（配置記憶體、等 block layer）。

- **`sq_array` / `sq_sqes`**：對應 Ch 2 的兩層間接。`sq_array` 是 indices 陣列（SQ 環裡存的），`sq_sqes` 是 SQE 本體陣列（`IORING_OFF_SQES` 那塊）。`io_get_sqe()` 先讀 `sq_array[head & mask]` 拿到真正的 SQE index，再去 `sq_sqes` 取（Ch 23）。NO_SQARRAY 模式下沒有 `sq_array`，直接 1:1。
- **`cached_sq_head`**：kernel 自己快取的 SQ head，批次處理後才一次 `smp_store_release` 寫回共享的 `rings->sq.head`（`io_commit_sqring()`）。Ch 2 說的「kernel 移 head」就是動它。
- **`file_table`**：registered（fixed）files 表。`buf_table`：registered buffers 表。這兩個是 Ch 15 fixed file/buffer 的 kernel 端，詳解在 Ch 29。
- **`node_cache` / `imu_cache` / `apoll_cache` / `netmsg_cache` / `rw_cache` / `cmd_cache`**：一堆 `io_alloc_cache`。這是 io_uring 的**物件回收池**——async poll 結構、網路 msghdr、read/write 的 async 狀態、uring_cmd 的 async 資料，這些會重複配置釋放的結構，用 alloc_cache 存著重用，省 slab 往返。Ch 21 會講 request 本身的快取，這裡是「request 私有資料」的快取。
- **`submit_state`**：`struct io_submit_state`，一次 `io_submit_sqes()` 批次提交的暫存狀態——free request list、completion batch list、當前 link chain、block plug。Ch 23 的主角之一。
- **`cancel_table`**：`io_hash_table`，把可被 cancel 的請求（poll、timeout 等）用 `user_data` 雜湊進 bucket，讓 `IORING_OP_ASYNC_CANCEL` 能 O(1) 找到目標（Ch 30）。
- **`iopoll_list`**：IOPOLL 模式下，已提交、等著被輪詢完成的請求 list。

## 第三組：完成側（completion）

```c
	struct {
		struct io_uring_cqe	*cqe_cached;
		struct io_uring_cqe	*cqe_sentinel;

		unsigned		cached_cq_tail;
		unsigned		cq_entries;
		struct io_ev_fd	__rcu	*io_ev_fd;

		void			*cq_wait_arg;
		size_t			cq_wait_size;
	} ____cacheline_aligned_in_smp;
```

和提交側對稱，獨立一個 cacheline：

- **`cqe_cached` / `cqe_sentinel`**：一段「現成可寫的 CQE」範圍。填 CQE 時先在 `[cqe_cached, cqe_sentinel)` 裡拿，用完才走慢路徑 `__io_get_cqe()` 重設範圍。減少每次填 CQE 都重算位址的成本。
- **`cached_cq_tail`**：kernel 快取的 CQ tail。每貼一個 CQE +1，commit 時 `smp_store_release` 寫回 `rings->cq.tail`。Ch 2 說的「kernel 推 CQ tail」動的就是它。
- **`io_ev_fd`**：`__rcu` 保護的 eventfd 橋接物件（`has_evfd` 為真時有效）。完成時若設了 eventfd，透過它通知。用 RCU 是因為 eventfd 可以在請求 in-flight 時被動態註冊/移除（Ch 18/`eventfd.c`）。

## 第四組：task_work 與非同步喚醒

```c
	/*
	 * task_work and async notification delivery cacheline. Expected to
	 * regularly bounce b/w CPUs.
	 */
	struct {
		struct llist_head	work_llist;
		struct llist_head	retry_llist;
		unsigned long		check_cq;
		atomic_t		cq_wait_nr;
		atomic_t		cq_timeouts;
		struct wait_queue_head	cq_wait;
	} ____cacheline_aligned_in_smp;
```

註解自己說了：**這條 cacheline 預期會頻繁在 CPU 間 bounce**，所以刻意獨立，免得污染提交/完成側的 cache。

- **`work_llist`**：DEFER_TASKRUN 模式下，待執行的 local task_work 的 lock-free list（`llist`）。其他 CPU 完成 I/O 後把 task_work 原子地推進來，提交者 task 回到 `io_uring_enter` 時一次跑完。這是 Ch 27 的核心資料結構。
- **`check_cq`**：bitmap 旗標，標記「CQ 有特殊狀況要處理」——overflow、dropped CQE 等。`io_uring_enter` 回來會檢查它（Ch 25 的 `-EBADR` 就是這裡來的）。
- **`cq_wait`**：等 CQE 的 wait queue。`io_cqring_wait()` 睡在這；完成端貼了 CQE 就 `wake_up` 它。
- **`cq_wait_nr`**：目前等待者想要的最少 CQE 數，供 lazy wake 判斷「夠了沒、要不要現在叫醒」。

## 第五組：timeouts

```c
	/* timeouts */
	struct {
		raw_spinlock_t		timeout_lock;
		struct list_head	timeout_list;
		struct list_head	ltimeout_list;
		unsigned		cq_last_tm_flush;
	} ____cacheline_aligned_in_smp;
```

- **`timeout_lock`**：**raw_spinlock**（不可被 PREEMPT_RT 搶佔的 spinlock），因為 timeout 要在中斷/timer context 操作。
- **`timeout_list`**：`IORING_OP_TIMEOUT` 的 list；**`ltimeout_list`**：linked timeout（`IORING_OP_LINK_TIMEOUT`，幫前一個請求設逾時）的 list。Ch 17 的 kernel 端，詳解 Ch 30。

## 其餘：鎖、list、SQPOLL、teardown

剩下的欄位不在對齊 block 裡，依功能散落，挑最關鍵的：

```c
	spinlock_t		completion_lock;
	struct list_head	cq_overflow_list;
	struct hlist_head	waitid_list;
#ifdef CONFIG_FUTEX
	struct hlist_head	futex_list;
	struct io_alloc_cache	futex_cache;
#endif
	const struct cred	*sq_creds;
	struct io_sq_data	*sq_data;	/* if using sq thread polling */
	struct wait_queue_head	sqo_sq_wait;
	struct list_head	sqd_list;
	/* ... */
	struct xarray		personalities;
	struct user_struct	*user;
	struct mm_struct	*mm_account;
	/* ctx exit and cancelation */
	struct llist_head	fallback_llist;
	struct delayed_work	fallback_work;
	struct work_struct	exit_work;
	struct list_head	tctx_list;
	struct completion	ref_comp;
	/* ... */
	struct mutex		mmap_lock;
	struct io_mapped_region	sq_region;
	struct io_mapped_region	ring_region;
	struct io_mapped_region	param_region;
```

- **`completion_lock`**：保護 CQ 貼寫與 `cq_overflow_list` 的 spinlock。注意：當 `lockless_cq` 為真（DEFER_TASKRUN/IOPOLL），完成端**不走這把鎖**，靠「單一 context」保證正確。這把鎖存在，是給「可能多 context 貼 CQE」的傳統模式用的。io_uring 有兩把主鎖要分清：`uring_lock`（提交側 mutex）、`completion_lock`（完成側 spinlock）。
- **`cq_overflow_list`**：CQ 滿了而放不下的 CQE 暫存在這（`struct io_overflow_cqe`），等 CQ 有空再 flush 回去。Ch 8 的 CQ overflow 的 kernel 端。
- **`sq_data` / `sqo_sq_wait` / `sqd_list`**：SQPOLL 模式的 kernel 提交執行緒狀態。多個 ring 可以用 `IORING_SETUP_ATTACH_WQ` 共用一個 `io_sq_data`（一條 SQPOLL thread 服務多個 ring），`sqd_list` 串起它們（Ch 27/`sqpoll.c`）。
- **`personalities`**：xarray，存註冊的 cred（`io_uring_register` 的 personality），讓一個請求能以不同身分執行（`IOSQE_` personality 欄位）。
- **`mm_account`**：抓著的 `mm_struct`，純粹為了帳務——process 退出時 mm 先走，但還要能 unaccount 鎖定的記憶體（Ch 22 的 `io_uring_create()` 有 `mmgrab`）。
- **teardown 一組**：`exit_work`（拆除的 work）、`ref_comp`（等 `refs` 歸零的 completion）、`tctx_list`（用過這個 ctx 的所有 task 的 `io_uring_task` list）、`fallback_work`（task 死掉時的後備執行路徑）。Ch 30 整章在講它們怎麼協作把 ring 乾淨拆掉。
- **`mmap_lock` + 三個 `io_mapped_region`**：ring 記憶體的 mmap 管理。`ring_region`（SQ/CQ 環）、`sq_region`（SQE 陣列）、`param_region`（最佳化參數傳遞）。6.18 把 ring 記憶體統一成 `io_mapped_region` 抽象，Ch 22 會看到 `io_create_region()`。

### 兩個容易被跳過、但很重要的欄位

- **`hash_map`（`struct io_wq_hash *`）**：buffered write 的序列化。同一個 inode 的多個 buffered write 若同時丟給不同 io-wq worker，會互相打架（page cache 的寫入要序列化）。`hash_map` 讓「同一個檔的 buffered write」雜湊到 io-wq 的**同一個 hash bucket**，強制它們在同一條 worker 上依序執行。這是 Ch 26 io-wq 的 hashed work 概念的 ctx 端錨點——為什麼有些 work「不能並行」，根源在這。
- **teardown 三件組的協作**（`exit_work` / `ref_comp` / `tctx_list` / `fallback_work`）：ring 拆除不是一個函式呼叫，是一支小小的狀態機。`ref_comp` 是一個 `completion`，拆除方睡在它上面等 `refs`（percpu_ref）歸零；`exit_work` 是排進系統 workqueue 的拆除工作；`tctx_list` 讓拆除能找到所有用過這個 ctx 的 task 去取消它們的 in-flight 請求；`fallback_work`/`fallback_llist` 是「提交者 task 已經死了、task_work 無處可送」時的保命路徑——把來不及處理的請求導到一個系統 workqueue 收尾，確保沒有請求永遠懸著。這四者怎麼編舞，是 Ch 30 整章的內容；在這裡先知道「拆除為什麼需要這麼多欄位」——因為 in-flight 請求可能散在 io-wq、poll wait queue、task_work 各處，要全部可靠地收回來。
- **`evfd_last_cq_tail` / `nr_req_allocated`**：都標了「protected by `->completion_lock`」。前者記上次通知 eventfd 時的 CQ tail（避免重複通知）；後者是「總共配置過幾個 io_kiocb」（Ch 21 的 `__io_alloc_req_refill` 會 `nr_req_allocated += ret`），拆除時用來確認所有請求都回收了。這兩個小計數器是「ctx 欄位各由不同鎖保護」的又一例證——它們不歸 `uring_lock`，歸 `completion_lock`。

## 底層機制：為什麼要這樣排版

`____cacheline_aligned_in_smp` 這個巨集把欄位對齊到一條 cacheline（通常 64 bytes）的起點，確保**不同 block 落在不同 cacheline**。目的是避免 **false sharing**：CPU0 在提交側寫 `cached_sq_head`，CPU1 在完成側寫 `cached_cq_tail`，如果這兩個欄位同一條 cacheline，兩個 CPU 就會互相讓對方的 cache line 失效，即使它們根本沒碰同一個變數。

io_uring 把「誰在熱路徑寫、頻率多高、哪個 context 碰」當成排版的第一準則：

- read-mostly 的放最前，常駐 cache。
- 提交側、完成側各自一條 line——因為 DEFER_TASKRUN 下它們是不同 CPU。
- task_work 那條標明「會 bounce」，隔離開來。

這就是為什麼這個 struct 不是按邏輯分類排，而是按 cacheline 行為排。讀它的排版，等於讀設計者對「哪些東西會在哪個 CPU 被碰」的判斷。（cacheline 與 false sharing 的基礎見 kernel_internals 的 [Ch 23 TLB 與記憶體屏障](../kernel_internals/23-tlb-memory-barriers.md) 和 [Ch 7 per-cpu 與並行](../kernel_internals/07-per-cpu-and-concurrency.md)。）

### 具體看一次 DEFER_TASKRUN 完成碰了哪幾條 cacheline

把排版和實際路徑對上，就懂了分組的必要。在 DEFER_TASKRUN 下，一個請求在 CPU1（io-wq worker 或中斷下半部）完成，而提交者 task 在 CPU0 睡在 `io_uring_enter`：

1. **CPU1**：請求做完，要通知提交者。它把 task_work 原子地推進 `work_llist`（第四組，task_work cacheline），再動 `cq_wait_nr`/`cq_wait` 判斷要不要叫醒 CPU0。**碰的是第四組那條「預期會 bounce」的 cacheline**——這條被刻意獨立，就是因為它天生要跨 CPU 寫。
2. **CPU0**：被喚醒，回到 `io_uring_enter`，跑 `io_run_local_work` 把 `work_llist` 的 task_work 一一執行。每個 task_work 填一個 CQE——動 `cqe_cached`/`cached_cq_tail`（第三組，完成側 cacheline）。因為 `lockless_cq` 為真，**全程不碰 `completion_lock`**。
3. CPU0 填完 CQE，`smp_store_release` 寫回 `rings->cq.tail`，user 收得到。

整個過程，**提交側第二組那條 cacheline（`uring_lock`、`sq_array`…）CPU1 從頭到尾沒碰**。如果完成側和提交側欄位擠在同一條 cacheline，CPU1 寫完成資料就會讓 CPU0 的提交側 cache 失效——即使兩者邏輯無關。分組把它們隔開，CPU1 的寫只污染「本來就要跨 CPU」的那條，提交側保持 local。這就是 `____cacheline_aligned_in_smp` 在這個 struct 上的實際回報，也是 DEFER_TASKRUN 能把高並行完成做到幾乎無爭用的硬體層原因。

## 對比與取捨：ctx 的鎖策略演進

io_uring 早期（5.x 初期）完成端一律走 `completion_lock` spinlock，提交端走 `uring_lock`。隨著 DEFER_TASKRUN（6.1）引入「所有完成都回到單一提交者 task 處理」的模型，`lockless_cq` 讓完成端可以**完全不上鎖**——因為此時 CQ 只有一個 writer。這是 io_uring 近年最大的效能躍進之一：高並行下 `completion_lock` 的爭用曾是瓶頸。

取捨是：DEFER_TASKRUN 要求 SINGLE_ISSUER（只有一個提交者），犧牲了「多執行緒共用一個 ring 同時提交」的彈性，換來無鎖完成。對大多數「一個 event loop 一個 ring」的架構（tokio-uring、多數 proxy），這是划算的。對「執行緒池共用 ring」的舊設計，就得留在有鎖模式。Ch 27 會把這條權衡講透。

## 進階：怎麼判斷一個 ctx 欄位被哪把鎖保護

讀 Part 3/4 時最常問的問題是「我碰的這個 `ctx->xxx`，現在安全嗎」。io_uring 沒有一張總表，但有幾個可靠的判斷法：

- **看它在哪個對齊 block**。提交側 block 的欄位（`sq_array`、`submit_state`、`file_table`、各 alloc_cache）預設受 `uring_lock`；timeout block 受 `timeout_lock`；mmap 區受 `mmap_lock`。block 的位置本身就是鎖的線索。
- **grep 函式註解的 `__must_hold`**。io_uring 大量用 `__must_hold(&ctx->uring_lock)` / `__must_hold(&ctx->completion_lock)` 標註函式的鎖前提（你在 Ch 23 的 `io_submit_sqes`、Ch 24 的 `io_queue_sqe` 都看到）。`grep -n "__must_hold" io_uring/*.c` 幾乎是一張免費的鎖文件。
- **自帶鎖的欄位不靠 ctx 的鎖**：xarray（`personalities`、`io_bl_xa`、`zcrx_ctxs`）、percpu_ref（`refs`）、llist（`work_llist`、`fallback_llist`）、`atomic_t`（`cancel_seq`、`cq_wait_nr`）各自有內建同步，不需要再上 ctx 的鎖。看到型別是這些，就別去找「哪把 spinlock 保護它」。
- **`lockless_cq` 的例外**：完成側欄位（`cqe_cached`、`cached_cq_tail`、`cq_overflow_list`）在傳統模式受 `completion_lock`，但 `lockless_cq` 為真時改由「單一 context」保證——此時找不到鎖不是漏看。

另外值得單獨記 **`check_cq`** 這個 `unsigned long`：它是一組 bit 旗標（`IO_CHECK_CQ_OVERFLOW_BIT`、`IO_CHECK_CQ_DROPPED_BIT`），標記「CQ 有特殊狀況」。它用 atomic 的 `set_bit`/`test_bit` 操作，不靠 ctx 的鎖。`io_uring_enter` 回來前會檢查它，Ch 23 看到的 `-EBADR`（CQE 被 drop）就是讀 `IO_CHECK_CQ_DROPPED_BIT` 得知、回報給 user 後再 `clear_bit`。把「CQ 異常」做成一個 lockless 的 bitmap，而非每次都掃 overflow list，是完成熱路徑的另一個省鎖設計。

## 踩雷集錦

1. **以為 `completion_lock` 保護「所有」CQE 操作**。在 `lockless_cq`（DEFER_TASKRUN/IOPOLL）下，完成端根本不碰這把鎖，靠單一 context 保證安全。你在那些路徑找不到 `spin_lock(&ctx->completion_lock)` 不是 bug。
2. **把 `uring_lock` 和 `completion_lock` 當同一把**。前者是提交側 mutex（可睡），後者是完成側 spinlock（不可睡）。持有順序、能否睡眠都不同，搞混會在推理 race 時全盤皆錯。
3. **以為 `ctx->refs` 歸零 = 沒有 in-flight 請求**。`refs` 是 percpu_ref，in-flight 請求、open 的 ring fd、SQPOLL thread 都持有 ref。fd 一關不代表請求做完；請求做完不代表 fd 關了。Ch 30 會看它怎麼協調。
4. **在 `sq_array` 和 `sq_sqes` 之間迷路**。`sq_array[i]` 存的是 index（指向 `sq_sqes`），不是 SQE 本身。這層間接是 Ch 2 提過的「提交順序可和填寫順序不同」。NO_SQARRAY 模式沒有 `sq_array`，別假設它一定存在。
5. **忽略 1-bit 旗標是「推導快取」而非「原始設定」**。`task_complete`、`lockless_cq`、`syscall_iopoll` 都是 setup 時從 `flags` 算出來存著的，不是 user 直接設的。要知道它們怎麼來，看 Ch 22 的 `io_uring_create()`，不是去 uapi header 找。

## 進階：ctx 的動態 resize 與 mmap_lock

Ch 20 的 ctx 大多是 setup 時定型的，但 6.x 加了一個反直覺的能力：**ring 可以在不重建的情況下改大小**（`IORING_REGISTER_RESIZE_RINGS`，`register.c`）。這用在「一開始不知道要多深、跑起來才發現 CQ 太小」的場景——重新分配一塊更大的 `io_rings`，把舊的 CQE 搬過去，再切換 `ctx->rings` 指標。

這就是 Ch 20 結尾那三個欄位存在的原因：

```c
	struct mutex			mmap_lock;
	struct io_mapped_region		sq_region;
	struct io_mapped_region		ring_region;
	struct io_mapped_region		param_region;
```

註解寫得很白：`mmap_lock` 是「保護 resize vs mmap 的競態」。想像一條 thread 正在 `io_uring_mmap`（user 第一次映射環），另一條在 resize（換掉 `ctx->rings` 底下的 region）——兩者同時跑，mmap 可能映射到正在被釋放的舊 region。`mmap_lock` 把這兩側序列化。這是一個很好的例子說明「為什麼 ctx 需要這麼多把看似重複的鎖」:`uring_lock` 管提交、`completion_lock` 管完成、`mmap_lock` 管記憶體映射/resize、`timeout_lock` 管 timer——每把鎖對應一個獨立的、可能並行的子系統，粒度拆細才能減少爭用。

想在實機看一個 ctx 的即時狀態，不用寫 code，`cat` 它的 fdinfo（Ch 19 的 `fdinfo.c`）：

```bash
cat /proc/<pid>/fdinfo/<ring_fd>
```

輸出裡的 `SqHead`/`SqTail`/`CqHead`/`CqTail`、registered files/buffers 數量、SQPOLL 狀態，全部直接讀自這個 `io_ring_ctx` 的欄位。debug io_uring 程式「請求卡住了嗎、CQ 滿了嗎」時，這是第一個該看的地方。

## 本章重點整理

- `io_ring_ctx` 是一個 io_uring 實例的全部 kernel 狀態，掛在 ring fd 的 `file->private_data`。
- 欄位按 cacheline 行為分組：read-mostly 熱資料、提交側（`uring_lock` 保護）、完成側、task_work（會 bounce）、timeouts、以及鎖/list/SQPOLL/teardown。
- 兩把主鎖：`uring_lock`（提交 mutex）、`completion_lock`（完成 spinlock）；`lockless_cq` 下完成端無鎖。
- `refs`（percpu_ref）是主引用計數，歸零才拆除；`rings` 指向與 user 共享的 SQ/CQ metadata。
- 1-bit 旗標（`task_complete`、`lockless_cq`…）是 setup 時從 `flags` 推導快取的 fast-path 判斷。
- 排版即設計：`____cacheline_aligned_in_smp` 隔離 cacheline，避免 false sharing，反映「哪些欄位在哪個 CPU 被碰」。

## 自我檢核

- [ ] 我能說出 `uring_lock` 與 `completion_lock` 各保護什麼、各是哪種鎖、能不能睡。
- [ ] 我能解釋為什麼提交側和完成側欄位被放在不同 cacheline。
- [ ] 我知道 `lockless_cq` 何時為真、為什麼它下面完成端能不上鎖。
- [ ] 我能說出 `sq_array` 和 `sq_sqes` 的差別與那層間接的用途。
- [ ] 給我一個 `ctx->xxx`，我能講出它屬於哪一組、大概被哪把鎖/哪個 context 保護。

## 延伸閱讀

- **`include/linux/io_uring_types.h`（6.18，`struct io_ring_ctx`）** — `~/io_uring_course/ref/linux/`
  - **讀哪裡**：整個 struct 定義，對照本章的分組，自己把每個欄位標上「哪一組」
  - **和本章關聯**：本章就是這段原始碼的導讀
- **[Efficient IO with io_uring](https://kernel.dk/io_uring.pdf)** — Jens Axboe
  - **讀哪裡**：ring 與 ctx 的高層結構一節；理解 `rings` 欄位與 user 端 mmap 的對應
- **[An introduction to the io_uring asynchronous I/O framework](https://blogs.oracle.com/linux/post/an-introduction-to-the-io-uring-asynchronous-io-framework)** — Bijan Mottahedeh (Oracle)
  - **讀哪裡**：ctx 與 request 的關係概述，補一個高層視角
- **kernel_internals [Ch 7 per-cpu 與並行](../kernel_internals/07-per-cpu-and-concurrency.md)、[Ch 25 spinlocks](../kernel_internals/25-spinlocks.md)**
  - **讀哪裡**：percpu_ref、spinlock vs mutex、cacheline false sharing——本章鎖與排版的底層
- **[DEFER_TASKRUN 引入的 LWN 報導](https://lwn.net/Articles/897327/)**
  - **讀哪裡**：為什麼需要單一提交者與無鎖完成，`task_complete`/`lockless_cq` 的設計動機

ctx 是「實例」的中樞；下一章換個視角，看「一個請求」的中樞——`struct io_kiocb` 從 SQE 誕生到回收的完整生命週期，它正是 Part 4 所有 UAF 的案發現場。

→ [Ch 21 io_kiocb：request 物件的生命週期](./21-io-kiocb-lifecycle.md)
