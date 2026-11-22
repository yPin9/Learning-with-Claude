# Ch 21 — io_kiocb：request 物件的生命週期

> **目標**：追蹤 `struct io_kiocb`——io_uring 裡「一個請求」的物件——從 SQE 被解析成它、執行、完成、到釋放回收的完整一生。把它的引用計數、`REQ_F_*` 旗標狀態機、以及它怎麼從 slab/alloc_cache 進出講清楚。這是 Part 4 安全章的地基：io_uring 的 UAF，案發現場幾乎都在這個物件上。

> **環境**：kernel 6.18 原始碼，`include/linux/io_uring_types.h`（`struct io_kiocb` 第 658 行）、`io_uring/io_uring.c`、`io_uring/refs.h`。

## 為什麼 io_kiocb 值得整整一章？

一個 io_uring 請求的一生，比一個同步 syscall 複雜一個數量級。`read(2)` 進 kernel、做事、回來，生命週期就是那個 stack frame。io_uring 的一個 read 請求可能：被解析成 `io_kiocb` → 嘗試非阻塞執行失敗 → arm 一個 poll → fd 可讀時被 task_work 喚醒 → 重新執行 → 退回 io-wq worker → 在 worker thread 完成 → 貼 CQE → 回收。它跨越**多個執行 context、多個時間點**，中間還可能被 cancel、被 link 到別的請求、被 timeout 砍掉。

這一切都掛在一個 `io_kiocb` 上。這個物件什麼時候「還活著」、誰持有它的引用、哪個旗標代表它處在哪個狀態——這些不是學究細節，是**正確性的全部**。io_uring 近年最嚴重的 CVE，幾乎都是「某條路徑以為請求還在、另一條已經把它釋放了」的 UAF。要讀懂那些 CVE（Part 4），得先把這個物件的生死脈絡刻進腦子。

## 先建立直覺：一個請求的四個階段

```
   ┌─────────┐  io_get_sqe + io_init_req    ┌──────────┐
   │   SQE   │ ───────────────────────────▶ │ io_kiocb │  (1) 誕生
   │ (user)  │  def->prep() 解析進私有結構     │  prepped │
   └─────────┘                              └────┬─────┘
                                                 │ io_issue_sqe → def->issue()
                                                 ▼
                                          ┌──────────────┐
                                          │  執行中       │  (2) 可能跨 context：
                                          │  inline / poll│      inline 嘗試、arm poll、
                                          │  / io-wq      │      退回 io-wq worker
                                          └──────┬───────┘
                                                 │ 結果就緒
                                                 ▼
                                          ┌──────────────┐
                                          │ 填 CQE        │  (3) 完成
                                          │ complete_defer│      贴 res/user_data 到 CQ
                                          │ /complete_post│
                                          └──────┬───────┘
                                                 │ 引用歸零
                                                 ▼
                                          ┌──────────────┐
                                          │ 回收          │  (4) 回 alloc cache 或 slab
                                          │ add_to_cache  │      （SLAB_TYPESAFE_BY_RCU）
                                          └──────────────┘
```

四個階段：**誕生**（SQE→req）、**執行**（可能跨 context）、**完成**（填 CQE）、**回收**。本章順著這條線走，每一步看 kernel code 到底動了這個物件的哪些欄位。

## io_kiocb 的結構：一個精心壓縮的物件

```c
/* include/linux/io_uring_types.h */
struct io_kiocb {
	union {
		/* 每個 io_kiocb 子型別的第一個欄位都是 file 指標，
		 * 所以可以透過任何子結構或直接用 'file' 存取 */
		struct file		*file;
		struct io_cmd_data	cmd;
	};

	u8				opcode;
	u8				iopoll_completed;
	u16				buf_index;
	unsigned			nr_tw;
	io_req_flags_t			flags;        /* REQ_F_* 旗標 */
	struct io_cqe			cqe;          /* user_data + res + flags/fd */
	struct io_ring_ctx		*ctx;
	struct io_uring_task		*tctx;
	union {
		struct io_buffer	*kbuf;        /* REQ_F_BUFFER_SELECTED */
		struct io_rsrc_node	*buf_node;
	};
	union {
		struct io_wq_work_node	comp_list;    /* cache / completion batch / iopoll */
		__poll_t		apoll_events;
	};
	struct io_rsrc_node		*file_node;
	atomic_t			refs;         /* 引用計數 */
	bool				cancel_seq_set;
	struct io_task_work		io_task_work;
	union {
		struct hlist_node	hash_node;    /* poll：掛進 cancel_table */
		u64			iopoll_start;
		struct rcu_head		rcu_head;     /* 私有釋放用 */
	};
	struct async_poll		*apoll;
	void				*async_data;  /* opcode 的 async 私有資料 */
	atomic_t			poll_refs;
	struct io_kiocb			*link;        /* link chain 下一個 */
	const struct cred		*creds;
	struct io_wq_work		work;         /* io-wq 用 */

	struct io_big_cqe {
		u64			extra1;
		u64			extra2;
	} big_cqe;                                    /* CQE32 的額外 16 bytes */
};
```

幾個設計重點：

- **開頭的 union 讓 `cmd` 疊在 `file` 上**。每個 opcode 把自己的私有結構（`struct io_rw`、`struct io_accept`…）疊在 `io_cmd_data` 上，但它們的第一個欄位都是 `struct file *`，所以無論哪個子型別，`req->file` 都有效。`io_cmd_data` 給每個 command 固定 56 bytes 私有空間：

```c
struct io_cmd_data {
	struct file		*file;
	/* each command gets 56 bytes of data */
	__u8			data[56];
};
```

這 56 bytes 不夠放的（例如要存 iovec、msghdr），就另外配 `async_data`，從 Ch 20 說的 `rw_cache`/`netmsg_cache` 那些 alloc_cache 拿。

- **大量 union**：`comp_list`/`apoll_events` 共用、`hash_node`/`iopoll_start`/`rcu_head` 共用。因為這些欄位在不同生命週期階段用，不會同時有效，疊起來省空間。物件越小，一條 cacheline 裝越多，slab 密度越高。
- **`cqe`** 就是將來要貼進 CQ 的那筆（`user_data`、`res`、`flags`），請求執行時就地填好，完成時搬進 CQ 環。
- **兩個引用計數**：`refs`（請求整體生命週期）和 `poll_refs`（poll 子系統專用，Ch 28）。別混。

## 階段一：誕生——SQE 變成 io_kiocb

請求物件不是臨時 malloc 的，是從**每個 ctx 的 request 快取**拿。`io_submit_sqes()` 的迴圈裡第一件事就是 `io_alloc_req()`：

```c
/* io_uring/io_uring.h */
static inline bool io_alloc_req(struct io_ring_ctx *ctx, struct io_kiocb **req)
{
	if (unlikely(io_req_cache_empty(ctx))) {
		if (!__io_alloc_req_refill(ctx))
			return false;
	}
	*req = io_extract_req(ctx);
	return true;
}
```

快取（`ctx->submit_state.free_list`）空了才去 slab 批次補貨。補貨走 `__io_alloc_req_refill()`，一次抓 8 個（`IO_REQ_ALLOC_BATCH`）：

```c
/* io_uring/io_uring.c */
__cold bool __io_alloc_req_refill(struct io_ring_ctx *ctx)
	__must_hold(&ctx->uring_lock)
{
	gfp_t gfp = GFP_KERNEL | __GFP_NOWARN | __GFP_ZERO;
	void *reqs[IO_REQ_ALLOC_BATCH];
	int ret;

	ret = kmem_cache_alloc_bulk(req_cachep, gfp, ARRAY_SIZE(reqs), reqs);
	/* Bulk alloc is all-or-nothing. 失敗就退回單顆配置保底 */
	if (unlikely(ret <= 0)) {
		reqs[0] = kmem_cache_alloc(req_cachep, gfp);
		if (!reqs[0])
			return false;
		ret = 1;
	}

	percpu_ref_get_many(&ctx->refs, ret);
	ctx->nr_req_allocated += ret;

	while (ret--) {
		struct io_kiocb *req = reqs[ret];
		io_req_add_to_cache(req, ctx);
	}
	return true;
}
```

兩個關鍵：**批次配置**（`kmem_cache_alloc_bulk` 一次 8 個，攤平 slab 往返成本）、**每配置一個就對 `ctx->refs` +1**（`percpu_ref_get_many`）——所以只要有 req 存在，ctx 就不會被拆掉。這是 Ch 20 說的 ctx 引用計數與請求生命週期掛鉤的地方。

`req_cachep` 這個 slab 的建立方式，是整個 Part 4 最重要的一行：

```c
/* io_uring/io_uring.c, io_uring_init() */
req_cachep = kmem_cache_create("io_kiocb", sizeof(struct io_kiocb), &kmem_args,
			SLAB_HWCACHE_ALIGN | SLAB_PANIC | SLAB_ACCOUNT |
			SLAB_TYPESAFE_BY_RCU);
```

**`SLAB_TYPESAFE_BY_RCU`**——記住這個旗標。它的意思是：一個 `io_kiocb` 被釋放後，那塊記憶體**不會立刻拿去當別的型別用**，而是在 RCU grace period 內保證「還是一個 io_kiocb」（型別安全），但**內容可能已經被重新配置成另一個請求**。這讓某些 lockless 路徑能安全地「樂觀讀」一個可能正在被釋放的 req（型別不會變、不會變成野指標指向的垃圾），但也意味著「同一塊記憶體可能已經是不同的請求」。這個半保證正是許多 io_uring UAF 的微妙之處：指標還指著合法的 io_kiocb 記憶體，但它早已不是你以為的那個請求了。（RCU 與 SLAB_TYPESAFE_BY_RCU 的機制見 kernel_internals [Ch 27 RCU](../kernel_internals/27-rcu.md)、[Ch 18 SLUB](../kernel_internals/18-slub-allocator.md)。）

拿到空的 req 後，`io_init_req()` 把 SQE 解析進去（Ch 23 詳解）。此刻設定的核心欄位：`opcode`、`flags`（從 SQE flags 複製）、`cqe.user_data`、`ctx`、`tctx`，最後呼叫 `def->prep(req, sqe)` 讓 opcode 把自己的私有欄位填進 `cmd`/`async_data`。誕生完成。

## REQ_F_* 旗標：請求的狀態機

`req->flags` 是一個 `io_req_flags_t`（64-bit），每個 bit 是一個 `REQ_F_*`。它既記錄**user 的請求屬性**，也記錄**請求當前走到哪、碰過什麼**。前 7 個 bit 和 user 的 `IOSQE_*` 數值對齊，可以直接複製：

```c
/* include/linux/io_uring_types.h */
enum {
	REQ_F_FIXED_FILE_BIT	= IOSQE_FIXED_FILE_BIT,
	REQ_F_IO_DRAIN_BIT	= IOSQE_IO_DRAIN_BIT,
	REQ_F_LINK_BIT		= IOSQE_IO_LINK_BIT,
	REQ_F_HARDLINK_BIT	= IOSQE_IO_HARDLINK_BIT,
	REQ_F_FORCE_ASYNC_BIT	= IOSQE_ASYNC_BIT,
	REQ_F_BUFFER_SELECT_BIT	= IOSQE_BUFFER_SELECT_BIT,
	REQ_F_CQE_SKIP_BIT	= IOSQE_CQE_SKIP_SUCCESS_BIT,

	/* 第一個 byte 被 user flags 佔了，往後移以免重疊 */
	REQ_F_FAIL_BIT		= 8,
	REQ_F_INFLIGHT_BIT,
	/* ... 一路到 __REQ_F_LAST_BIT ... */
};
```

挑生命週期中最關鍵的幾個旗標：

- **`REQ_F_REFCOUNT`**：這個請求有沒有啟用 atomic 引用計數（見下一節）。**沒設這個 bit，`req->refs` 根本不被使用**——這是 io_uring 的一個重要最佳化。
- **`REQ_F_INFLIGHT`**：請求被登記在 inflight 追蹤裡，teardown 時要可靠地等它或取消它。
- **`REQ_F_LINK` / `REQ_F_HARDLINK`**：這個請求後面還 link 著別的請求（`req->link` 有效）。
- **`REQ_F_POLLED` / `REQ_F_APOLL_MULTISHOT`**：請求走過了 poll handler（Ch 28）。
- **`REQ_F_ASYNC_DATA`**：`req->async_data` 已配置，清理時要 free 它。
- **`REQ_F_CREDS`**：`req->creds` 有效，清理時要 `put_cred`。
- **`REQ_F_NEED_CLEANUP`**：完成時要呼叫 `def->cleanup()`。
- **`REQ_F_CQE_SKIP`**：完成時不要貼 CQE（`IOSQE_CQE_SKIP_SUCCESS` 或內部釋放用）。
- **`REQ_F_BUFFER_SELECTED` / `REQ_F_BUFFER_RING`**：用了 provided buffer，完成時要歸還/commit（Ch 14/36）。

這些旗標不是獨立的開關，是**一台狀態機**：請求往前走，旗標一個個被設上，完成時 `io_clean_op()` 按旗標決定要清理什麼。讀任何一條請求路徑，都是在讀「這一步會檢查/設定哪些 `REQ_F_*`」。

## 引用計數：預設不計數的設計

這是 io_kiocb 最反直覺、也最容易出 bug 的地方。**大多數請求根本不用引用計數。**

一個簡單請求的生命週期是「單一擁有者」：提交路徑持有它，執行完直接完成回收，中間沒有第二方同時持有。這種情況計數是浪費，所以 io_uring **預設不啟用** `req->refs`——除非設了 `REQ_F_REFCOUNT`：

```c
/* io_uring/refs.h */
static inline bool req_ref_put_and_test(struct io_kiocb *req)
{
	if (likely(!(req->flags & REQ_F_REFCOUNT)))
		return true;    /* 沒啟用計數 → 直接當作「可以釋放了」 */

	WARN_ON_ONCE(req_ref_zero_or_close_to_overflow(req));
	return atomic_dec_and_test(&req->refs);
}

static inline void __io_req_set_refcount(struct io_kiocb *req, int nr)
{
	if (!(req->flags & REQ_F_REFCOUNT)) {
		req->flags |= REQ_F_REFCOUNT;
		atomic_set(&req->refs, nr);
	}
}
```

什麼時候才啟用？當請求會被**多方同時持有**：arm 了 poll（poll 子系統持一份、issue 路徑持一份）、退回 io-wq（worker 持一份、free_work 持一份，見下方 `io_wq_submit_work()` 的 `__io_req_set_refcount(req, 2)`）、linked timeout（兩個請求互指）。這些情境才呼叫 `io_req_set_refcount()` 把計數開起來，初值通常是 2。

`req_ref_put_and_test()` 回傳 true 代表「最後一個引用放掉了，可以釋放」。沒啟用計數時它無條件回 true——意思是「單一擁有者，放掉就是釋放」。

**這個設計的危險面**：如果某條路徑「以為」請求是單一擁有者（沒啟用計數）直接釋放，但另一條路徑其實還持有它——UAF。反過來，計數開了卻少 put 一次——記憶體洩漏 + 請求卡住。io_uring 的 refcount CVE 多半是「某個新增的執行路徑忘了這個請求已經/還沒啟用計數」。Part 4 的 Ch 32 整章在拆這類模式。

## 階段二與三：執行與完成（本章只鋪陳）

執行階段 `io_issue_sqe()` → `def->issue()` 是 Ch 24 的主題，完成階段 `io_req_complete_defer()` / `io_req_complete_post()` 是 Ch 25 的主題。這裡只點出和「物件生命週期」相關的事：

- 執行可能**不在原本的 context**。退回 io-wq 時，`io_wq_submit_work()`（Ch 24/26）會先確保計數開著（因為 worker 和 free_work 兩方持有）：

```c
/* io_uring/io_uring.c, io_wq_submit_work() 開頭 */
	/* one will be dropped by io_wq_free_work() after returning to io-wq */
	if (!(req->flags & REQ_F_REFCOUNT))
		__io_req_set_refcount(req, 2);
	else
		req_ref_get(req);
```

- 完成時 `req->cqe`（早就填好的 `res`/`user_data`）被搬進 CQ 環，然後請求進入回收。

## 階段四：回收——回到 cache 或 slab

完成後，請求若引用歸零，透過 `io_free_req()` 走回收。但注意：`io_free_req()` 不是直接 `kmem_cache_free`，而是排一個 task_work 把它導回正規完成路徑：

```c
/* io_uring/io_uring.c */
__cold void io_free_req(struct io_kiocb *req)
{
	/* refs were already put, restore them for io_req_task_complete() */
	req->flags &= ~REQ_F_REFCOUNT;
	/* we only want to free it, don't post CQEs */
	req->flags |= REQ_F_CQE_SKIP;
	req->io_task_work.func = io_req_task_complete;
	io_req_task_work_add(req);
}
```

正常的快路徑回收是 `io_req_add_to_cache()`——把請求推回 ctx 的 free_list，**不還給 slab**，留著下一個請求重用：

```c
/* io_uring/io_uring.c */
static inline void io_req_add_to_cache(struct io_kiocb *req, struct io_ring_ctx *ctx)
{
	if (IS_ENABLED(CONFIG_KASAN))
		io_poison_cached_req(req);
	wq_stack_add_head(&req->comp_list, &ctx->submit_state.free_list);
}
```

所以一個 io_kiocb 的典型生命週期裡，它**在 ctx 的 free_list 和「使用中」之間反覆循環**，很少真的回 slab。真正回 slab 要等 ctx 拆除時（Ch 30）。`CONFIG_KASAN` 下回收會「poison」物件——讓任何釋放後存取立刻被 KASAN 抓到，這是 Part 4 跑 PoC 時最重要的偵測工具。

**`async_data` 的回收是獨立的**：若 `REQ_F_ASYNC_DATA` 設了，`io_clean_op()` 會把它處理掉（free 或還回對應的 alloc_cache），然後清掉 flag。請求物件本體回 free_list，私有的 async 資料回它自己的 cache——兩套回收，別混。

## 底層機制：三層記憶體來源

一個「完整的請求」在 kernel 的記憶體其實來自三個地方：

1. **`io_kiocb` 本體**：`req_cachep` slab（`SLAB_TYPESAFE_BY_RCU`），經由 ctx free_list 快取重用。
2. **opcode 的小私有資料（≤56 bytes）**：疊在 `io_cmd_data` 裡，不另外配置。
3. **opcode 的大私有資料（iovec、msghdr…）**：`async_data`，從對應的 `io_alloc_cache`（`rw_cache`/`netmsg_cache`/`cmd_cache`…）拿。

這三層各有各的生命週期與回收路徑。UAF 可能發生在任一層：本體被提早釋放、`async_data` 被釋放後還被某路徑讀、provided buffer 被歸還後還被寫。讀 Part 4 時，第一個要問的永遠是「掛了的是哪一層的記憶體」。

### 把一個 recv 請求的欄位軌跡走一遍

抽象的欄位表不如一次具體的軌跡。追一個「recv 一個 socket，資料還沒到」的請求，看它一生中 `io_kiocb` 的哪些欄位被誰動：

| 時點 | 事件 | 動到的欄位 |
|---|---|---|
| 誕生 | `io_alloc_req` 從 free_list 取 | 整塊物件（上一個請求的殘值，`__GFP_ZERO` 配置時才清） |
| 誕生 | `io_init_req` | `opcode`、`flags`（從 SQE 複製）、`cqe.user_data`、`ctx`、`tctx` |
| 誕生 | `io_recvmsg_prep`（`net.c`） | `cmd` 疊的 `struct io_sr_msg`；大的 msghdr 配到 `async_data`，設 `REQ_F_ASYNC_DATA` |
| 首次 issue | `io_issue_sqe` → `io_assign_file` | `file`、`file_node` |
| 首次 issue | `io_recv` 回 `-EAGAIN`，`io_arm_poll_handler` | 啟用計數：`flags |= REQ_F_REFCOUNT`、`refs = 2`；配 `apoll`、設 `REQ_F_POLLED`、掛 `hash_node` 進 `cancel_table`；`poll_refs` 開始計數 |
| 掛起 | 等在 socket wait queue | （物件靜止，但 poll 與 issue 兩方各持一份 `refs`） |
| 就緒 | poll callback 排 task_work | `io_task_work.func`、`io_task_work.node` 推進 ctx 的 `work_llist` |
| 重試 | `io_poll_issue` → `io_recv` 讀到資料 | `cqe.res`（位元組數）、`cqe.flags` |
| 完成 | 填 CQE | `cqe` 整筆搬進 CQ；`refs` 遞減到 0 |
| 回收 | `io_clean_op` + `io_req_add_to_cache` | 按 `REQ_F_ASYNC_DATA` 釋放 `async_data`、按 `REQ_F_CREDS` put cred；本體回 `comp_list`/free_list |

看這張表會發現兩件事。第一，**同一個 `io_kiocb` 的欄位在不同階段由不同 context 寫**：誕生在提交 task、poll callback 在中斷 context、重試在 task_work。這就是為什麼那些 union 能成立（`hash_node`/`rcu_head` 不同階段用）、也是為什麼跨階段的欄位存取要格外小心同步。第二，**計數是在「arm poll」那一刻才開的**——在那之前它是單一擁有者（提交路徑），之後變成雙擁有者（poll + issue），`REQ_F_REFCOUNT` 精確地標記了這個轉變點。Part 4 的 race，追的往往就是「某條路徑在這個轉變點前後，對持有者數量的假設錯了」。

## 踩雷集錦

1. **以為每個 io_kiocb 都有引用計數**。預設沒有。`req->refs` 只在 `REQ_F_REFCOUNT` 設了才有意義。沒設時 `req_ref_put_and_test()` 無條件回 true。推理生命週期要先確認「這條路徑有沒有啟用計數」。
2. **把 `req->refs` 和 `req->poll_refs` 當同一個**。前者是請求整體計數，後者是 poll 子系統內部計數（Ch 28）。兩個 `atomic_t`，各管各的。
3. **忽略 `SLAB_TYPESAFE_BY_RCU` 的語意**。釋放後的 io_kiocb 記憶體在 RCU grace period 內「還是 io_kiocb」，但內容可能已是另一個請求。指標非野、型別安全，不代表「還是你那個請求」。這是許多 io_uring UAF 的核心微妙點。
4. **以為 `io_free_req()` 會立刻釋放**。它排 task_work，導回 `io_req_task_complete`，且標 `REQ_F_CQE_SKIP` 不貼 CQE。真正的快路徑回收是 `io_req_add_to_cache()`——回 free_list 重用，不回 slab。
5. **忘了 `async_data` 是分開回收的**。`io_kiocb` 回 free_list，但 `async_data` 要靠 `REQ_F_ASYNC_DATA` + `io_clean_op()` 處理。opcode 新增路徑若忘了設/清這個 flag，就是洩漏或 double-free。

## 對比與取捨：條件式 refcount 的代價

「預設不計數，多方持有才啟用」是 io_uring 刻意的取捨，值得和「一律計數」對照：

| 面向 | 條件式 refcount（io_uring） | 一律 atomic refcount |
|---|---|---|
| 簡單請求成本 | 零 atomic op（最常見的情況） | 每個請求至少一對 inc/dec |
| 程式碼複雜度 | 高：每條路徑要知道「現在計不計數」 | 低：無腦 get/put |
| bug 面 | 大：路徑誤判持有者數量 → UAF/洩漏 | 小：計數對了就安全 |
| 高並行吞吐 | 高：避開 atomic 熱點 | atomic 在 cacheline 上爭用 |

io_uring 選了前者，因為 request 是每秒數百萬次配置/釋放的物件，一對 atomic 在這個量級是可觀成本。代價是 Part 4 會反覆看到的那類 bug：某個新增的執行路徑（新 opcode、新 multishot 模式）誤判了「這個請求現在有幾個持有者」，少 put 一次洩漏、多 put 一次 UAF。效能的最佳化直接換成了審計的負擔——這條權衡是理解 io_uring 安全史的主線。

## 進階：io_kiocb 的 slab 強化與 CQE32

**hardened usercopy 標註。** 建立 `req_cachep` 時傳的 `kmem_cache_args` 不只是樣板：

```c
/* io_uring/io_uring.c, io_uring_init() */
	struct kmem_cache_args kmem_args = {
		.useroffset = offsetof(struct io_kiocb, cmd.data),
		.usersize = sizeof_field(struct io_kiocb, cmd.data),
		.freeptr_offset = offsetof(struct io_kiocb, work),
		.use_freeptr_offset = true,
	};
```

`useroffset`/`usersize` 告訴 `CONFIG_HARDENED_USERCOPY`：這個 slab 物件裡，**只有 `cmd.data` 那 56 bytes 允許和 user 記憶體互拷**（openat2 這類 op 要把 user 的結構 copy 進來）。碰到物件其他部位的 usercopy 會被擋下——縮小了「透過 io_kiocb 做 usercopy 攻擊」的面。`freeptr_offset = offsetof(…, work)` 則讓 SLUB 把 free list 指標藏在 `work` 欄位裡（物件空閒時 `work` 無意義），而不是放物件開頭——這和 `SLAB_TYPESAFE_BY_RCU` 配合，是 free list 指標強化的一部分。這幾行是 Part 4 談 heap 利用時必須理解的前提：攻擊者能污染的 offset、能觸發的 usercopy 範圍，都被這些標註框住。

**`big_cqe`：CQE32 的尾巴。** `io_kiocb` 結尾的 `struct io_big_cqe big_cqe { u64 extra1, extra2; }` 只在 `IORING_SETUP_CQE32`（Ch 22 的雙倍 CQE）時有意義——某些 op（如 `IORING_OP_URING_CMD`、zero-copy 相關）要回傳比 16 bytes 更多的結果，就把額外 16 bytes 暫存在這，完成時連同主 CQE 一起貼進 CQE32 的 CQ 環。沒開 CQE32 的 ring，這兩個欄位閒置。這也是為什麼 Ch 20 的 `rings_size()` 在 CQE32 時把 CQE 區算成兩倍。

**KASAN poison 作為偵測工具。** Ch 21 正文提過 `io_req_add_to_cache` 在 `CONFIG_KASAN` 下 poison 回收的請求。實務上這是跑 Part 4 PoC 的標配：開 KASAN 的 kernel 下，任何「請求釋放後又被存取」會立刻 panic 並印出釋放 stack 與存取 stack，把一個偶發、難重現的 UAF 變成一個確定、可讀的報告。Ch 40 的 fuzzing 與 Part 4 的 PoC 驗證都靠它。

## 本章重點整理

- `io_kiocb` 是「一個請求」的物件，一生分四階段：誕生（SQE→req，`io_init_req`+`prep`）、執行（可能跨 context）、完成（填 CQE）、回收。
- 從 `req_cachep`（`SLAB_TYPESAFE_BY_RCU`）批次配置，經 ctx 的 `free_list` 快取重用；每配置一個對 `ctx->refs` +1，把請求與 ctx 生命週期綁定。
- `REQ_F_*` 旗標是請求的狀態機：記錄屬性（fixed file、link）與走過的步驟（polled、need cleanup、buffer selected），完成時按旗標清理。
- **引用計數預設關閉**，只有多方持有（poll、io-wq、linked timeout）才 `REQ_F_REFCOUNT` + `io_req_set_refcount()` 開啟，初值常為 2。
- 記憶體三層：本體（slab/free_list）、小私有資料（`io_cmd_data` 56 bytes）、大私有資料（`async_data`/alloc_cache），各自回收。
- `SLAB_TYPESAFE_BY_RCU` + 條件式計數，是 io_uring 快，也是 io_uring UAF 多的同一個原因。

## 自我檢核

- [ ] 我能說出一個 io_kiocb 四個生命週期階段，各自呼叫的關鍵函式。
- [ ] 我能解釋為什麼大多數請求不用引用計數，以及什麼時候才啟用。
- [ ] 我知道 `SLAB_TYPESAFE_BY_RCU` 保證什麼、不保證什麼，以及它和 UAF 的關係。
- [ ] 我能分辨 `req->refs`、`req->poll_refs`、`ctx->refs` 三個計數各管什麼。
- [ ] 我能說出一個「完整請求」的記憶體來自哪三層、各怎麼回收。

## 延伸閱讀

- **`include/linux/io_uring_types.h`（`struct io_kiocb`、`REQ_F_*` 列舉）與 `io_uring/refs.h`** — 6.18 原始碼
  - **讀哪裡**：struct 的 union 佈局、`REQ_F_*` 全表、`refs.h` 的 `req_ref_put_and_test`
  - **和本章關聯**：本章就是這三段的導讀
- **kernel_internals [Ch 18 SLUB allocator](../kernel_internals/18-slub-allocator.md)、[Ch 27 RCU](../kernel_internals/27-rcu.md)**
  - **讀哪裡**：`kmem_cache_alloc_bulk`、`SLAB_TYPESAFE_BY_RCU` 的語意、RCU grace period
  - **為什麼必讀**：不懂這兩個，Part 4 的 UAF 無從談起
- **[Put an io_uring on it: Exploiting the Linux Kernel](https://chompie.rip/Blog+Posts/Put+an+io_uring+on+it+-+Exploiting+the+Linux+Kernel)** — chompie1337
  - **讀哪裡**：它如何把一個請求物件的 UAF 轉成利用原語；先看機制部分
  - **前提**：本章 + Ch 32 讀完再看
- **[commit f958d7b528b1（page ref overflow 檢查）](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/commit/?id=f958d7b528b1)**
  - **讀哪裡**：`refs.h` 註解提到「偷自 mm」的那個 overflow 檢查手法的出處
- **[LWN: The io_uring refcounting rework](https://lwn.net/Articles/)（搜尋 io_uring refcount）**
  - **讀哪裡**：條件式 refcount 的設計動機與演進

請求物件的一生我們走了一遍；接下來三章把「誕生」這一步放大。先從最前面開始：`io_uring_setup` 到底在 kernel 裡建了什麼，一個 ctx 和它的 ring 記憶體怎麼來的。

→ [Ch 22 io_uring_setup 內部：ring 建立、記憶體分配、fd 綁定](./22-setup-internals.md)
