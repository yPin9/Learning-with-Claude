# Ch 22 — io_uring_setup 內部：ring 建立、記憶體分配、fd 綁定

> **目標**：把 Ch 2 user 端那一個 `io_uring_setup(8, &p)` 的 kernel 另一面全部攤開。參數怎麼驗證與正規化、`io_ring_ctx` 怎麼配置、SQ/CQ 環的共享記憶體怎麼分配、anon fd 怎麼建立並綁上 ctx、各種 `IORING_SETUP_*` flag 怎麼改變這一切。讀完你能把 Ch 2 回填的每一個 `sq_off`/`cq_off` offset，對應到 kernel 哪一行寫的。

> **環境**：kernel 6.18 原始碼，`io_uring/io_uring.c`（`io_uring_setup` 第 3963 行、`io_uring_create` 第 3787 行、`io_allocate_scq_urings` 第 3596 行）。

## 為什麼 setup 這一步值得細讀？

`io_uring_setup` 看起來只是「建個 ring」，但它一次做完了四件彼此牽連的事：**決定這個 ring 的全部性質**（從 flags 推導一堆行為旗標）、**分配一個和 user 共享的記憶體佈局**（SQ/CQ 環 + SQE 陣列，Ch 2 user 端 mmap 的就是它）、**建立一個 anon inode fd**（讓 user 之後能 mmap 和 enter）、**把 ctx 掛上這個 fd**。

這四件事的順序和錯誤處理極其講究——任何一步失敗，前面配置的全部要乾淨回收，而此時 ring 可能已經部分可見。這一章也是理解 Ch 2 那張「三次 mmap」圖的終點：你會看到 kernel 這邊 `rings_size()` 怎麼算出那塊記憶體、offset 怎麼回填。它同時是 Part 4 的起點——一個攻擊者能控制的最早入口就是這些參數。

## 先建立直覺：setup 的五個步驟

```
   io_uring_setup(entries, params)           [SYSCALL_DEFINE2]
        │  copy_from_user(p), 檢查 resv[] 全 0, 檢查 p.flags
        ▼
   io_uring_allowed()                        權限閘：io_uring_disabled / LSM
        │
        ▼
   io_uring_create(entries, &p, params)      ── 主流程 ──
        │
        ├─(1) io_uring_sanitise_params()     flag 組合合法性
        ├─(2) io_uring_fill_params()         entries → sq/cq_entries, 回填 offset
        ├─(3) io_ring_ctx_alloc()            配 ctx、初始化鎖/list/cache (Ch 20)
        │      · 從 flags 推導 task_complete/lockless_cq/... 等旗標
        ├─(4) io_allocate_scq_urings()       分配共享記憶體（rings + sqes）
        │      io_sq_offload_create()        SQPOLL 的話，起 kernel thread
        │      copy_to_user(params)          把回填好的 p（含 offset）還給 user
        ├─(5) io_uring_get_file()            建 anon fd，file->private_data = ctx
        │      __io_uring_add_tctx_node()    把 ctx 綁進 current 的 io_uring_task
        └───── io_uring_install_fd()         安裝 fd，回傳給 user
```

注意兩個設計決定：**fd 是最後才裝的**（`install_fd` 在最末尾），確保 user 拿到 fd 時 ctx 已完全就緒，沒有「拿到 fd 卻還沒建好」的窗口；**`copy_to_user` 在 fd 之前**，因為 offset 資訊必須在 user 能用 fd 之前就備好。下面逐步看。

## 入口：syscall 與權限閘

```c
/* io_uring/io_uring.c */
SYSCALL_DEFINE2(io_uring_setup, u32, entries,
		struct io_uring_params __user *, params)
{
	int ret;

	ret = io_uring_allowed();
	if (ret)
		return ret;

	return io_uring_setup(entries, params);
}
```

syscall 入口只做一件事就轉給 `io_uring_setup()`：**先過權限閘**。`io_uring_allowed()` 正是 Ch 0 那個 `io_uring_disabled` sysctl 的執法點：

```c
/* io_uring/io_uring.c */
static inline int io_uring_allowed(void)
{
	int disabled = READ_ONCE(sysctl_io_uring_disabled);
	kgid_t io_uring_group;

	if (disabled == 2)
		return -EPERM;

	if (disabled == 0 || capable(CAP_SYS_ADMIN))
		goto allowed_lsm;

	io_uring_group = make_kgid(&init_user_ns, sysctl_io_uring_group);
	if (!gid_valid(io_uring_group))
		return -EPERM;

	if (!in_group_p(io_uring_group))
		return -EPERM;

allowed_lsm:
	return security_uring_allowed();
}
```

三層：`disabled == 2` 全系統禁用直接 `-EPERM`；`disabled == 1` 時只有 `CAP_SYS_ADMIN` 或屬於 `io_uring_group` 的能用；最後還要過 LSM 的 `security_uring_allowed()` 鉤子（SELinux/AppArmor 可以在這卡）。這是 Ch 39 防禦章的機制根源——io_uring 的「全系統關閉」就在這幾行。

`io_uring_setup()`（非 syscall 的那個 static 版本）接著 `copy_from_user` 讀進 `params`，檢查保留欄位 `resv[]` 全為 0、`p.flags` 不含未知 bit（`~IORING_SETUP_FLAGS`），然後呼叫 `io_uring_create()`。

## 步驟一：參數合法性——io_uring_sanitise_params

flag 不是隨便組合的，很多互斥或互相依賴。`io_uring_sanitise_params()` 把這些規則集中執法：

```c
/* io_uring/io_uring.c（節錄） */
static int io_uring_sanitise_params(struct io_uring_params *p)
{
	unsigned flags = p->flags;

	/* There is no way to mmap rings without a real fd */
	if ((flags & IORING_SETUP_REGISTERED_FD_ONLY) &&
	    !(flags & IORING_SETUP_NO_MMAP))
		return -EINVAL;

	if (flags & IORING_SETUP_SQPOLL) {
		/* IPI related flags don't make sense with SQPOLL */
		if (flags & (IORING_SETUP_COOP_TASKRUN |
			     IORING_SETUP_TASKRUN_FLAG |
			     IORING_SETUP_DEFER_TASKRUN))
			return -EINVAL;
	}
	/* ... */
	/*
	 * For DEFER_TASKRUN we require the completion task to be the same as
	 * the submission task. This implies that there is only one submitter.
	 */
	if ((flags & IORING_SETUP_DEFER_TASKRUN) &&
	    !(flags & IORING_SETUP_SINGLE_ISSUER))
		return -EINVAL;
	/* ... */
	return 0;
}
```

這段把 Ch 9/27 會談的那些組合規則寫死：SQPOLL 和 COOP/DEFER_TASKRUN 不相容（SQPOLL 由 kernel thread 提交，IPI 省不省無意義）；**DEFER_TASKRUN 必須搭 SINGLE_ISSUER**（因為「完成回到提交者 task」隱含「只有一個提交者」）。你在 user 端拿到 `-EINVAL` 卻參數看起來都對時，十之八九是撞到這裡的組合規則。

## 步驟二：正規化與回填 offset——io_uring_fill_params

這是和 Ch 2 直接對接的一步。它做兩件事：把 `entries` 正規化成 2 的次方的 `sq_entries`/`cq_entries`，以及**回填所有 offset**。

```c
/* io_uring/io_uring.c（節錄） */
int io_uring_fill_params(unsigned entries, struct io_uring_params *p)
{
	if (!entries)
		return -EINVAL;
	if (entries > IORING_MAX_ENTRIES) {
		if (!(p->flags & IORING_SETUP_CLAMP))
			return -EINVAL;
		entries = IORING_MAX_ENTRIES;
	}

	p->sq_entries = roundup_pow_of_two(entries);
	if (p->flags & IORING_SETUP_CQSIZE) {
		if (!p->cq_entries)
			return -EINVAL;
		/* ... clamp 與 roundup ... */
		if (p->cq_entries < p->sq_entries)
			return -EINVAL;
	} else {
		p->cq_entries = 2 * p->sq_entries;
	}

	p->sq_off.head = offsetof(struct io_rings, sq.head);
	p->sq_off.tail = offsetof(struct io_rings, sq.tail);
	p->sq_off.ring_mask = offsetof(struct io_rings, sq_ring_mask);
	p->sq_off.ring_entries = offsetof(struct io_rings, sq_ring_entries);
	p->sq_off.flags = offsetof(struct io_rings, sq_flags);
	p->sq_off.dropped = offsetof(struct io_rings, sq_dropped);
	/* ... */
	p->cq_off.head = offsetof(struct io_rings, cq.head);
	p->cq_off.tail = offsetof(struct io_rings, cq.tail);
	p->cq_off.ring_mask = offsetof(struct io_rings, cq_ring_mask);
	p->cq_off.ring_entries = offsetof(struct io_rings, cq_ring_entries);
	p->cq_off.overflow = offsetof(struct io_rings, cq_overflow);
	p->cq_off.cqes = offsetof(struct io_rings, cqes);
	p->cq_off.flags = offsetof(struct io_rings, cq_flags);
	/* ... */
	return 0;
}
```

**Ch 2 的謎底在這**。Ch 2 說「kernel 回填 offset，你永遠用 `p.sq_off.tail` 定位、不硬編」——kernel 這邊填的就是 `offsetof(struct io_rings, sq.tail)`。user 的 `sq_ptr + p.sq_off.tail` 之所以總能指到正確欄位，是因為 `sq_off.tail` 就是 `struct io_rings` 裡 `sq.tail` 的真實偏移。kernel 哪天改了 `io_rings` 佈局，`offsetof` 自動算出新值回填，user code 不用改——這就是 io_uring ABI 向前相容的實作。

另外兩個細節：**CQ 預設是 SQ 的兩倍**（`p->cq_entries = 2 * p->sq_entries`），因為 SQE 提交後就釋放，應用可以跑比 SQ 深度更高的 in-flight 數，CQ 給多一點空間緩衝（Ch 2 的 raw 版也看到 `cq_entries` 比 `sq_entries` 大）。`IORING_SETUP_CQSIZE` 讓應用自訂 CQ 大小。`entries` 超過 `IORING_MAX_ENTRIES` 時，沒設 `CLAMP` 就 `-EINVAL`，設了就夾到上限。

## 步驟三：配置 ctx 並推導行為旗標——io_uring_create

`io_ring_ctx_alloc()`（Ch 20 看過，配 ctx、初始化所有鎖/list/alloc_cache）之後，`io_uring_create()` 有一整段**從 flags 推導 fast-path 旗標**的邏輯——這正是 Ch 20 說的「1-bit 旗標是推導快取」的出處：

```c
/* io_uring/io_uring.c, io_uring_create()（節錄） */
	ctx = io_ring_ctx_alloc(p);
	if (!ctx)
		return -ENOMEM;

	ctx->clockid = CLOCK_MONOTONIC;

	if (!(ctx->flags & IORING_SETUP_NO_SQARRAY))
		static_branch_inc(&io_key_has_sqarray);

	if ((ctx->flags & IORING_SETUP_DEFER_TASKRUN) &&
	    !(ctx->flags & IORING_SETUP_IOPOLL) &&
	    !(ctx->flags & IORING_SETUP_SQPOLL))
		ctx->task_complete = true;

	if (ctx->task_complete || (ctx->flags & IORING_SETUP_IOPOLL))
		ctx->lockless_cq = true;

	if (!ctx->task_complete)
		ctx->poll_activated = true;

	if (ctx->flags & IORING_SETUP_IOPOLL &&
	    !(ctx->flags & IORING_SETUP_SQPOLL))
		ctx->syscall_iopoll = 1;

	ctx->compat = in_compat_syscall();
	/* ... */

	if (ctx->flags & (IORING_SETUP_SQPOLL|IORING_SETUP_COOP_TASKRUN))
		ctx->notify_method = TWA_SIGNAL_NO_IPI;
	else
		ctx->notify_method = TWA_SIGNAL;

	mmgrab(current->mm);
	ctx->mm_account = current->mm;
```

逐條對應 Ch 20 的欄位：`task_complete`（DEFER_TASKRUN + 非 IOPOLL + 非 SQPOLL）→ `lockless_cq`（task_complete 或 IOPOLL，完成端免鎖）→ `notify_method`（COOP/SQPOLL 省 IPI）。這整段是「setup 時把昂貴的 flag 判斷一次算完、存成 1-bit」的範例——熱路徑就不用每次重算。`static_branch_inc(&io_key_has_sqarray)` 更進一步：用 static key（code patching）讓「有沒有 sq_array」這個判斷在熱路徑幾乎零成本（Ch 23 的 `io_get_sqe()` 會用到）。

`mmgrab(current->mm)` 抓住當前的 `mm_struct` 純為帳務（Ch 20 說過，process 退出時 mm 先走，但還要能 unaccount pinned memory）。

SINGLE_ISSUER 且非 R_DISABLED 時，這裡還會記下唯一的提交者：`ctx->submitter_task = get_task_struct(current)`。

## 步驟四：分配共享記憶體——io_allocate_scq_urings

這是「三次 mmap 映射的那塊記憶體」在 kernel 的分配處。它分兩塊：rings（SQ/CQ metadata + CQE 陣列 + 選配的 sq_array）和 sqes（SQE 本體陣列）。

```c
/* io_uring/io_uring.c（節錄） */
static __cold int io_allocate_scq_urings(struct io_ring_ctx *ctx,
					 struct io_uring_params *p)
{
	struct io_uring_region_desc rd;
	struct io_rings *rings;
	size_t size, sq_array_offset;
	int ret;

	ctx->sq_entries = p->sq_entries;
	ctx->cq_entries = p->cq_entries;

	size = rings_size(ctx->flags, p->sq_entries, p->cq_entries,
			  &sq_array_offset);
	if (size == SIZE_MAX)
		return -EOVERFLOW;

	memset(&rd, 0, sizeof(rd));
	rd.size = PAGE_ALIGN(size);
	if (ctx->flags & IORING_SETUP_NO_MMAP) {
		rd.user_addr = p->cq_off.user_addr;
		rd.flags |= IORING_MEM_REGION_TYPE_USER;
	}
	ret = io_create_region(ctx, &ctx->ring_region, &rd, IORING_OFF_CQ_RING);
	if (ret)
		return ret;
	ctx->rings = rings = io_region_get_ptr(&ctx->ring_region);

	if (!(ctx->flags & IORING_SETUP_NO_SQARRAY))
		ctx->sq_array = (u32 *)((char *)rings + sq_array_offset);
	rings->sq_ring_mask = p->sq_entries - 1;
	rings->cq_ring_mask = p->cq_entries - 1;
	rings->sq_ring_entries = p->sq_entries;
	rings->cq_ring_entries = p->cq_entries;

	if (p->flags & IORING_SETUP_SQE128)
		size = array_size(2 * sizeof(struct io_uring_sqe), p->sq_entries);
	else
		size = array_size(sizeof(struct io_uring_sqe), p->sq_entries);
	/* ... overflow 檢查 ... */

	memset(&rd, 0, sizeof(rd));
	rd.size = PAGE_ALIGN(size);
	/* ... NO_MMAP 處理 ... */
	ret = io_create_region(ctx, &ctx->sq_region, &rd, IORING_OFF_SQES);
	if (ret) {
		io_rings_free(ctx);
		return ret;
	}
	ctx->sq_sqes = io_region_get_ptr(&ctx->sq_region);
	return 0;
}
```

重點：

- **`rings_size()` 算出 rings 區的大小**，並透過 `sq_array_offset` 回報 sq_array 放在這塊的哪裡。看它的身體就懂佈局：

```c
/* io_uring/io_uring.c（節錄） */
unsigned long rings_size(unsigned int flags, unsigned int sq_entries,
			 unsigned int cq_entries, size_t *sq_offset)
{
	struct io_rings *rings;
	size_t off, sq_array_size;

	off = struct_size(rings, cqes, cq_entries);   /* io_rings + cq_entries 個 CQE */
	if (off == SIZE_MAX)
		return SIZE_MAX;
	if (flags & IORING_SETUP_CQE32) {             /* CQE32：CQE 大一倍 */
		if (check_shl_overflow(off, 1, &off))
			return SIZE_MAX;
	}
	/* ... CQE_MIXED 檢查 ... */
#ifdef CONFIG_SMP
	off = ALIGN(off, SMP_CACHE_BYTES);
#endif
	if (flags & IORING_SETUP_NO_SQARRAY) {
		*sq_offset = SIZE_MAX;                 /* 不要 sq_array */
		return off;
	}
	*sq_offset = off;                             /* sq_array 接在 CQE 陣列後面 */
	sq_array_size = array_size(sizeof(u32), sq_entries);
	/* ... overflow 檢查後 off += sq_array_size ... */
```

這就是 Ch 2 那塊 `IORING_OFF_SQ_RING` / `IORING_OFF_CQ_RING` 記憶體的真實佈局：一個 `struct io_rings`（含 SQ/CQ 的 head/tail/mask/flags）+ 緊接 `cq_entries` 個 CQE + （選配）`sq_entries` 個 u32 的 sq_array。整塊 `PAGE_ALIGN` 後用 `io_create_region()` 分配。注意到處都是 `check_shl_overflow`/`array_size`/`SIZE_MAX` 的溢位檢查——因為 size 直接來自 user 控制的 entries，一個整數溢位就是 Part 4 的攻擊面。

- **SQE 陣列另外一塊**（`ctx->sq_region` / `ctx->sq_sqes`），對應 Ch 2 的第三次 mmap `IORING_OFF_SQES`。SQE128 模式下每個 SQE 佔兩倍。
- **`io_create_region()`** 是 6.18 統一的 ring 記憶體抽象（`io_mapped_region`，在 `memmap.c`）。它根據 `IORING_OFF_*` 這個 key 建立一塊可被 user mmap 的 region。`IORING_SETUP_NO_MMAP` 時改用 user 自己提供的記憶體（`user_addr`），kernel 不分配只 pin——這是 6.x 的進階選項，讓應用能用大頁或自管記憶體。

分配完，`io_uring_create()` 回填 `p->sq_off.array`（sq_array 相對 rings 的偏移），然後 `io_sq_offload_create()`（SQPOLL 才真的起 thread）、`p->features = IORING_FEAT_FLAGS`（告訴 user 這顆 kernel 支援的 feature 集，Ch 2 檢查的 `IORING_FEAT_SINGLE_MMAP` 就在其中），最後 `copy_to_user(params, p, …)` 把全部回填好的參數還給 user。

## 步驟五：建 fd、綁 ctx、最後安裝

```c
/* io_uring/io_uring.c（節錄） */
static struct file *io_uring_get_file(struct io_ring_ctx *ctx)
{
	/* Create a new inode so that the LSM can block the creation. */
	return anon_inode_create_getfile("[io_uring]", &io_uring_fops, ctx,
					 O_RDWR | O_CLOEXEC, NULL);
}
```

ring fd 是一個 **anon inode**，`file->private_data` 就是 ctx，`f_op` 是 `io_uring_fops`。這一步把「fd」和「ctx」綁死：之後任何 `io_uring_enter(fd, …)` 都能 `file->private_data` 拿回 ctx（Ch 23 的 enter 就這麼做）。用 `anon_inode_create_getfile`（而非舊的 `anon_inode_getfile`）是為了**讓 LSM 能攔截這個 inode 的建立**——又一個 Ch 39 的防禦掛點。

`io_uring_fops` 只提供少數幾個操作：

```c
/* io_uring/io_uring.c */
static const struct file_operations io_uring_fops = {
	.release	= io_uring_release,
	.mmap		= io_uring_mmap,
	.get_unmapped_area = io_uring_get_unmapped_area,
	.poll		= io_uring_poll,
#ifdef CONFIG_PROC_FS
	.show_fdinfo	= io_uring_show_fdinfo,
#endif
};
```

`.mmap` 是 user 那三次 mmap 的入口（Ch 2）；`.poll` 讓 ring fd 自己能被 epoll/poll（Ch 18）；`.show_fdinfo` 是 `/proc/<pid>/fdinfo`（Ch 19 提的 `fdinfo.c`）；`.release` 是 fd 關閉時的拆除入口（Ch 30）。注意**沒有 `.read`/`.write`**——ring fd 不是拿來讀寫的，一切透過 mmap + enter。

建好 file 後，`__io_uring_add_tctx_node()` 把這個 ctx 登記進 `current->io_uring`（per-task 的 `io_uring_task`，Ch 21 看過它的 registered_rings 等欄位），然後**最後一步**才裝 fd：

```c
	if (p->flags & IORING_SETUP_REGISTERED_FD_ONLY)
		ret = io_ring_add_registered_file(tctx, file, 0, IO_RINGFD_REG_MAX);
	else
		ret = io_uring_install_fd(file);
```

`io_uring_install_fd()` 就是 `get_unused_fd_flags` + `fd_install`。註解講明白為什麼放最後：「Install ring fd as the very last thing, so we don't risk someone having closed it before we finish setup」——fd 一旦可見，另一個 thread 就可能 close 它觸發拆除，所以要等 ctx 全部就緒才讓 fd 曝光。`REGISTERED_FD_ONLY` 模式則連真正的 fd 都不給，只放進 tctx 的 registered 陣列（Ch 3 的 registered ring fd）。

任何一步失敗都跳 `err:` 走 `io_ring_ctx_wait_and_kill(ctx)` 把半成品乾淨拆掉（Ch 30）。

## 底層機制：為什麼是共享記憶體而非 copy

setup 的核心產物是「一塊 user 和 kernel 共享的記憶體」。這是 io_uring 相對舊 AIO 的根本差異：舊 `io_submit(2)` 每次要把 iocb 從 user 複製進 kernel。io_uring 讓 SQ/CQ 環和 SQE 陣列**直接共享物理頁**（Ch 2 的 `MAP_SHARED`），kernel 用 `READ_ONCE` 就地讀 user 寫的 SQE，不複製。`io_create_region()` 分配的頁，之後透過 `io_uring_mmap`（`.mmap` fop）映射回 user 空間，兩邊看同一份。

代價是**信任邊界的改變**：SQE 在共享記憶體裡，user 可以在 kernel 讀的「同時」改它。所以 kernel 讀 SQE 一律用 `READ_ONCE`（Ch 23 的 `io_init_req` 滿滿都是），而且「讀過就定案」——驗證過的值不能假設之後再讀還一樣。這是 io_uring 一條反覆出現的安全紀律，也是數個 TOCTOU 類 bug 的來源（Part 4）。

## 對比與取捨：flag 如何改變這一切

同一個 `io_uring_setup`，不同 flag 走出非常不同的路：

| flag | 對 setup 的影響 |
|---|---|
| `IORING_SETUP_SQPOLL` | 步驟四後 `io_sq_offload_create()` 起一條 kernel thread 輪詢 SQ（Ch 27/sqpoll.c） |
| `IORING_SETUP_DEFER_TASKRUN` | 要求 SINGLE_ISSUER；設 `task_complete`/`lockless_cq`，完成端走無鎖路徑 |
| `IORING_SETUP_CQE32` / `SQE128` | 記憶體分配加倍（`rings_size`/scq_urings 的 `*2`） |
| `IORING_SETUP_NO_SQARRAY` | 不分配 sq_array，SQ index 1:1 對應 |
| `IORING_SETUP_NO_MMAP` | kernel 不分配 ring 記憶體，pin user 提供的 |
| `IORING_SETUP_R_DISABLED` | ring 建好但停用，要 `io_uring_register(ENABLE_RINGS)` 才能用（Ch 39） |
| `IORING_SETUP_REGISTERED_FD_ONLY` | 不給真 fd，只放 tctx registered 陣列 |

取捨的主軸是「彈性 vs 效能 vs 安全」。DEFER_TASKRUN 犧牲多提交者換無鎖完成；NO_MMAP 換來自管記憶體但 user 要自己確保合法；R_DISABLED 讓你先建 ring 再在開放前鎖定 restrictions，是 sandbox 友善設計。

## 踩雷集錦

1. **以為 `entries` 就是你拿到的 SQ 深度**。它會被 `roundup_pow_of_two` 進位，CQ 預設是它的兩倍。user 端一定要讀回填後的 `p.sq_entries`/`p.cq_entries`，別假設等於你傳進去的值。
2. **把 `-EINVAL` 都當成「參數值錯」**。很多 `-EINVAL` 是 **flag 組合**撞到 `io_uring_sanitise_params()`（如 DEFER_TASKRUN 沒配 SINGLE_ISSUER、SQPOLL 配 COOP_TASKRUN）。值對不代表組合對。
3. **硬編 `struct io_rings` 的佈局**。kernel 用 `offsetof` 回填 offset 正是要你別硬編。Ch 2 一再強調，這裡是它的 kernel 根源。
4. **忽略 fd 是最後才裝的**。setup 中途失敗時 fd 還不存在，user 不會拿到半成品 fd。但反過來：fd 一裝上就可能被別的 thread close，所以 kernel 內部對「fd 可見之後」的假設很保守。
5. **以為 ring 記憶體一定由 kernel 分配**。`IORING_SETUP_NO_MMAP` 下是 user 提供、kernel pin。讀 `io_allocate_scq_urings` 要注意那兩個 `if (flags & IORING_SETUP_NO_MMAP)` 分支。
6. **把整數溢位檢查當噪音跳過**。`rings_size()` 裡的 `check_shl_overflow`/`SIZE_MAX`/`array_size` 全是因為 size 源自 user entries。這些正是 Part 4 要盯的地方，不是可略過的樣板。

## 進階：用 setup flag 搭一個最小 sandbox

setup 的 flag 不只調效能，也是 io_uring 的沙箱化基礎。一個常見的加固模式是「先建停用的 ring、鎖好 restrictions、再啟用」：

1. `io_uring_setup` 帶 `IORING_SETUP_R_DISABLED`——ring 建好但停用，此時 `io_uring_enter` 會因 `ctx->flags & IORING_SETUP_R_DISABLED` 直接回 `-EBADFD`（Ch 23 的 enter 開頭就檢查它）。
2. `io_uring_register(IORING_REGISTER_RESTRICTIONS, …)` 設定 `ctx->restrictions`——限定這個 ring 只能用哪些 opcode、哪些 sqe flag（Ch 20 的 `restrictions` 欄位、Ch 23 `io_check_restriction` 的執法）。
3. `io_uring_register(IORING_REGISTER_ENABLE_RINGS)` 才真正啟用。

這讓一段程式碼能把 io_uring 的攻擊面縮到「只剩我需要的那幾個 op」，即使它後來被攻陷，攻擊者也只能用被允許的 opcode。`R_DISABLED` 時 `io_uring_create()` 不會立刻設 `submitter_task`（正文那段 `!(ctx->flags & IORING_SETUP_R_DISABLED)` 的條件），因為提交者要等 enable 時才確定。這是 Ch 39 防禦章會展開的機制。

另一個進階選項 `IORING_SETUP_NO_MMAP`：ring 記憶體由 user 提供、kernel 只 pin。它的用途是讓應用用 **huge pages** 當 ring backing——減少 TLB miss，對高頻提交/完成的 ring 有實測效益。代價是 user 要自己保證那塊記憶體合法、對齊、生命週期夠長；`io_allocate_scq_urings` 裡那兩個 `if (flags & IORING_SETUP_NO_MMAP)` 分支就是處理「用 user 的 `user_addr` 而非 kernel 自己分配」。這也擴大了攻擊面——kernel 要信任一塊 user 指定的記憶體，驗證不足就是漏洞，所以 `io_create_region` 對 `IORING_MEM_REGION_TYPE_USER` 的檢查特別嚴。

## 本章重點整理

- `io_uring_setup` 五步：權限閘（`io_uring_allowed`）→ 參數合法性（`sanitise_params`）→ 正規化+回填 offset（`fill_params`）→ 配 ctx 並推導行為旗標（`io_ring_ctx_alloc` + create）→ 分配共享記憶體（`io_allocate_scq_urings`）→ 建 anon fd 綁 ctx（`io_uring_get_file` + `install_fd`）。
- `fill_params` 用 `offsetof(struct io_rings, …)` 回填 `sq_off`/`cq_off`，這是 Ch 2「用 offset 定位、不硬編」的 kernel 根源；CQ 預設為 SQ 的兩倍。
- 共享記憶體佈局由 `rings_size()` 決定：`io_rings` + CQE 陣列 +（選配）sq_array 為一塊，SQE 陣列為另一塊，對應 Ch 2 的三次 mmap。
- ctx 的 1-bit 行為旗標（`task_complete`/`lockless_cq`/`syscall_iopoll`/`notify_method`）在 create 時從 flags 一次推導快取。
- ring fd 是 anon inode，`private_data` = ctx、`f_op` = `io_uring_fops`（只有 mmap/poll/fdinfo/release），**最後才安裝**避免半成品曝光；LSM 可在 inode 建立時攔截。
- 共享記憶體把信任邊界移進 ring：kernel 讀 SQE 一律 `READ_ONCE`，size 計算處處防整數溢位——Part 4 的攻擊面起點。

## 自我檢核

- [ ] 我能說出 setup 的五個步驟與它們的順序，以及為什麼 fd 最後才裝。
- [ ] 我能把 Ch 2 user 端的 `p.sq_off.tail` 對應到 kernel 哪一行（`offsetof`）。
- [ ] 我能解釋 `rings_size()` 算出的那塊記憶體包含哪些東西。
- [ ] 我知道 `task_complete`/`lockless_cq` 是在 setup 的哪一步、依什麼條件設定的。
- [ ] 我能說出 ring fd 的 `f_op` 為什麼沒有 read/write，以及 `private_data` 指向什麼。

## 延伸閱讀

- **`io_uring/io_uring.c`（`io_uring_create`、`io_allocate_scq_urings`、`io_uring_fill_params`、`rings_size`）** — 6.18
  - **讀哪裡**：本章引用的四個函式全文，特別注意 `rings_size` 的每個溢位檢查
- **本課 [Ch 2 三十分鐘全貌](./02-thirty-minute-tour.md)、[Ch 3 三個 syscall](./03-three-syscalls.md)、[Ch 4 共享記憶體環](./04-shared-ring-memory.md)**
  - **讀哪裡**：user 端的 setup + 三次 mmap + offset 使用，和本章一體兩面對照讀
- **[io_uring_setup(2) man page](https://man7.org/linux/man-pages/man2/io_uring_setup.2.html)**
  - **讀哪裡**：`io_uring_params`、各 `IORING_SETUP_*` flag 的 user 視角定義
- **[Efficient IO with io_uring](https://kernel.dk/io_uring.pdf)** — Jens Axboe
  - **讀哪裡**：ring 記憶體與 mmap 佈局一節；共享記憶體設計的第一手說明
- **kernel_internals [Ch 33 VFS objects](../kernel_internals/33-vfs-objects.md)、[Ch 4 syscall 機制](../kernel_internals/04-syscall-mechanism.md)**
  - **讀哪裡**：anon inode / file / f_op 的機制、syscall 進入點——本章 fd 綁定與 `SYSCALL_DEFINE` 的底層

ring 建好了，fd 拿到了，記憶體也共享了。下一章跟著一個 `io_uring_enter`，看 kernel 怎麼從 SQ 裡把 SQE 一個個取出、解析成 `io_kiocb`、再透過 opcode dispatch 表送去執行。

→ [Ch 23 提交路徑：io_submit_sqes 與 opcode dispatch](./23-submission-path.md)
