# Ch 29 — 資源管理內部：io_rsrc_node、RCU 回收

> **目標**：搞懂 registered files / buffers 在 kernel 裡的內部表示（`io_rsrc_node`、`io_rsrc_data`、`io_mapped_ubuf`），它們的引用計數怎麼運作、什麼時候回收、update 怎麼同步。**本章有一個重要的版本更正**：io_uring 的 rsrc 子系統在 6.8–6.10 經歷了一次大改寫，6.18 的回收**不再**是舊設計的 percpu_ref + RCU 延遲釋放，而是改成「`uring_lock` 保護的同步整數引用計數 + alloc_cache 延遲重用」。我們會把兩代設計都講清楚、說明為什麼改，並誠實標出哪些是舊資料裡才有的東西。

> **環境**：kernel 6.18 原始碼（`v6.18`，commit `7d0a66e`）。對照 `io_uring/rsrc.c`、`io_uring/rsrc.h`、`include/linux/io_uring_types.h`。

## ⚠️ 版本更正：你可能讀過的「percpu_ref + RCU」是舊設計

很多 io_uring 的教材、CVE writeup、甚至本課規劃時的初始描述，都會講 rsrc 子系統用 **`percpu_ref` + RCU** 做資源回收：`io_rsrc_node` 掛 `percpu_ref`，歸零時透過 RCU callback（`io_rsrc_put_work` 之類）延遲釋放。**這在 6.18 已經不成立**。

Pavel Begunkov 在 6.8–6.10 間把整個 rsrc 子系統大幅簡化（commit 系列把 `io_rsrc_node` 從一個帶 percpu_ref、掛在 RCU 延遲釋放鏈上的重物件，改成一個只有幾個欄位、用普通 `int refs` 在 `uring_lock` 下增減的輕物件）。6.18 的 `io_rsrc_node` 長這樣（`io_uring/rsrc.h`）：

```c
struct io_rsrc_node {
	unsigned char			type;
	int				refs;

	u64 tag;
	union {
		unsigned long file_ptr;
		struct io_mapped_ubuf *buf;
	};
};
```

看到 `int refs` 了嗎？不是 `percpu_ref`，不是 `refcount_t`，就是一個**普通 int**。它靠「所有增減都在 `uring_lock`（一把 per-ctx 的 mutex）底下做」來保證原子性——根本不需要 RCU 或 percpu 的把戲。這是本章最重要的認知：**6.18 的 rsrc_node 回收是同步的、鎖保護的、不經 RCU grace period 的。**

那 RCU 在哪？RCU 在 6.18 的 io_uring 裡確實還在用，但**不是用在 rsrc_node 回收**——而是用在 request 物件的 `SLAB_TYPESAFE_BY_RCU`（Ch 27）、eventfd 的 `call_rcu`（Ch 25）、poll 的 waitqueue 遍歷（Ch 28），以及 ring teardown 尾端那個 `synchronize_rcu()`（Ch 30）。本章末尾會把「RCU 真正的角色」釐清，並說明為什麼舊 rsrc 設計要用 RCU、新設計怎麼不用了。先把這個更正放在最前面，免得你拿舊心智模型讀 6.18 原始碼整個對不上。

## 為什麼要有 registered 資源

先回顧 register 解決什麼（應用面 Ch 15）。每次 io_uring 操作一個 fd，kernel 要做 `fget`/`fput`（查 fd table、增減 file 引用）；每次操作一塊 user buffer，kernel 要 `get_user_pages`（pin 住、建頁表）。高頻操作同一組 fd/buffer 時，這些重複開銷很浪費。

**register** 讓你一次把 fd 或 buffer 登記進 kernel，之後用**索引**（而非 fd / 位址）引用它們。kernel 預先把 file 的引用抓好、buffer 的頁 pin 好，操作時直接用索引查表，省掉每次的 `fget`/`get_user_pages`。`io_rsrc_node` 就是「kernel 這邊替每個登記項保管的那份引用」。

## 先建立直覺：索引 → node → 真實資源

```
   user 提交 op，帶 buf_index = 3
          │
          ▼
   ctx->buf_table (io_rsrc_data)
   ┌───────────────────────────────────┐
   │ nr = 8                             │
   │ nodes[] ──► [0][1][2][3][4]...     │
   └───────────────────┬───────────────┘
                       ▼ nodes[3]
          ┌─────────── io_rsrc_node ───────────┐
          │ type = IORING_RSRC_BUFFER          │
          │ refs = 2   ◄── uring_lock 保護      │
          │ tag  = <user 給的標籤>              │
          │ buf  ──────┐                        │
          └────────────┼────────────────────────┘
                       ▼
          ┌──────── io_mapped_ubuf ────────┐
          │ ubuf, len, nr_bvecs            │
          │ refs (refcount_t) ◄── 跨 node 共享用 │
          │ bvec[] ──► pinned pages        │
          │ release(priv)                  │
          └────────────────────────────────┘
```

兩層引用計數，角色不同（這是初學最容易混的）：

- **`io_rsrc_node.refs`（int）**：保護「這個 node 本身」。多少個 in-flight 請求正在用 node 3，就 +幾。`uring_lock` 保護。
- **`io_mapped_ubuf.refs`（refcount_t）**：保護「底下那塊 mapped buffer」。只有在 buffer **被多個 node 共享**（clone buffers，下詳）時才 >1。這是真正的 `refcount_t`，因為共享可能跨 ctx。

對 files 更簡單：node 的 union 用 `file_ptr` 直接存 `struct file *`（低位元還塞了 flags），沒有第二層。

## 結構：io_rsrc_data 與查表

`io_rsrc_data` 就是一張 node 指標表（`include/linux/io_uring_types.h`）：

```c
struct io_rsrc_data {
	unsigned int			nr;
	struct io_rsrc_node		**nodes;
};
```

ctx 裡有兩張：`ctx->file_table.data`（registered files）和 `ctx->buf_table`（registered buffers）。查表是 `io_rsrc_node_lookup`（`io_uring/rsrc.h`）：

```c
static inline struct io_rsrc_node *io_rsrc_node_lookup(struct io_rsrc_data *data,
						       int index)
{
	if (index < data->nr)
		return data->nodes[array_index_nospec(index, data->nr)];
	return NULL;
}
```

`array_index_nospec` 是 Spectre v1 緩解：確保即使 `index` 是攻擊者控制的，CPU 也不會投機越界讀。index 合法才回對應 node，否則 `NULL`。這個邊界檢查看似平凡，但 Part 4 會看到「index 驗證 + node 生命週期」正是多個 rsrc 類 CVE 的核心。

## 配置：`io_rsrc_node_alloc`

node 不是每次 `kmalloc`，而是從 per-ctx 的 **alloc_cache** 拿（`io_uring/rsrc.c`）：

```c
struct io_rsrc_node *io_rsrc_node_alloc(struct io_ring_ctx *ctx, int type)
{
	struct io_rsrc_node *node;

	node = io_cache_alloc(&ctx->node_cache, GFP_KERNEL);
	if (node) {
		node->type = type;
		node->refs = 1;
		node->tag = 0;
		node->file_ptr = 0;
	}
	return node;
}
```

`ctx->node_cache` 是一個 `io_alloc_cache`——一個 per-ctx 的小 freelist，快取最近釋放的 node，避免反覆進 slab allocator。新 node 誕生 `refs = 1`（建立者持有一份）。這個 alloc_cache 就是新設計取代舊 RCU 延遲釋放的東西：**它快取釋放的 node 供重用，而不是用 RCU grace period 延遲釋放**。下面回收一節會對照看。

## 引用與釋放：全在 uring_lock 下

拿 node 來用時 `refs++`。看 buffer 的例子（`io_uring/rsrc.c` 的 `io_find_buf_node`）：

```c
inline struct io_rsrc_node *io_find_buf_node(struct io_kiocb *req,
					     unsigned issue_flags)
{
	struct io_ring_ctx *ctx = req->ctx;
	struct io_rsrc_node *node;

	if (req->flags & REQ_F_BUF_NODE)
		return req->buf_node;
	req->flags |= REQ_F_BUF_NODE;

	io_ring_submit_lock(ctx, issue_flags);
	node = io_rsrc_node_lookup(&ctx->buf_table, req->buf_index);
	if (node) {
		node->refs++;
		req->buf_node = node;
		io_ring_submit_unlock(ctx, issue_flags);
		return node;
	}
	...
}
```

`io_ring_submit_lock` 就是拿 `uring_lock`。`node->refs++` 在鎖內做——這就是為什麼 `refs` 可以是普通 int：**鎖保證了序列化**。請求記下 `req->buf_node` 和 `REQ_F_BUF_NODE` flag，之後完成時放掉這份引用。

放掉用 `io_put_rsrc_node`（`io_uring/rsrc.h`）：

```c
static inline void io_put_rsrc_node(struct io_ring_ctx *ctx, struct io_rsrc_node *node)
{
	lockdep_assert_held(&ctx->uring_lock);
	if (!--node->refs)
		io_free_rsrc_node(ctx, node);
}
```

`lockdep_assert_held(&ctx->uring_lock)` 白紙黑字寫明：**放引用必須持 uring_lock**。`--refs` 歸零就**當場同步**釋放，不排 RCU、不延遲。這是和舊設計最大的行為差異。

## 回收：`io_free_rsrc_node`

refs 歸零進這裡（`io_uring/rsrc.c`）：

```c
void io_free_rsrc_node(struct io_ring_ctx *ctx, struct io_rsrc_node *node)
{
	if (node->tag)
		io_post_aux_cqe(ctx, node->tag, 0, 0);

	switch (node->type) {
	case IORING_RSRC_FILE:
		fput(io_slot_file(node));
		break;
	case IORING_RSRC_BUFFER:
		io_buffer_unmap(ctx, node->buf);
		break;
	default:
		WARN_ON_ONCE(1);
		break;
	}

	io_cache_free(&ctx->node_cache, node);
}
```

三件事：

1. **tag 通知**：如果 node 有 `tag`（user register 時給的標籤），`io_post_aux_cqe` 吐一筆 CQE 通知 user「這個資源真的被回收了」。這是 user 得知「我 unregister 的 buffer 已經沒有 in-flight 請求在用、真的釋放了」的機制。
2. **放真實資源**：file 就 `fput`，buffer 就 `io_buffer_unmap`。
3. **node 回 cache**：`io_cache_free` 把 node 送回 `node_cache` 供重用（放不下才真的 `kfree`）。

`io_buffer_unmap` 處理第二層引用（`io_uring/rsrc.c`）：

```c
static void io_buffer_unmap(struct io_ring_ctx *ctx, struct io_mapped_ubuf *imu)
{
	if (unlikely(refcount_read(&imu->refs) > 1)) {
		if (!refcount_dec_and_test(&imu->refs))
			return;
	}

	if (imu->acct_pages)
		io_unaccount_mem(ctx, imu->acct_pages);
	imu->release(imu->priv);
	io_free_imu(ctx, imu);
}
```

這裡才是 `refcount_t`：如果 `imu->refs > 1`（buffer 被多個 node 共享），只 `dec`，最後一個持有者才真的 unmap。`imu->release(imu->priv)` 是個 callback——不同來源的 buffer（user pages、kernel buffer）有不同的釋放方式，release 指標讓回收泛化。

## update 的同步：原子換 node

registered 資源可以動態更新（`IORING_REGISTER_FILES_UPDATE` 等，Ch 15）。更新一個 slot 不是「改原 node」，而是**換一個新 node 進去、放掉舊的**。`io_reset_rsrc_node`（`io_uring/rsrc.h`）：

```c
static inline bool io_reset_rsrc_node(struct io_ring_ctx *ctx,
				      struct io_rsrc_data *data, int index)
{
	struct io_rsrc_node *node = data->nodes[index];

	if (!node)
		return false;
	io_put_rsrc_node(ctx, node);
	data->nodes[index] = NULL;
	return true;
}
```

update slot 3 的流程：在 `uring_lock` 下，`io_reset_rsrc_node` 放掉 `nodes[3]` 的舊 node（refs--，若歸零就回收），`nodes[3] = NULL`，再把新 node 放進去。關鍵：**舊 node 的 refs 可能還 >0**（有 in-flight 請求正用著它）。這時放一份引用只是 `refs--`，舊 node **不會立刻消失**——那些 in-flight 請求完成時各自 `io_put_rsrc_node`，最後一個才真正釋放舊 node。

這就是正確性的核心：**update 換掉 slot 指標，但舊 node 活到最後一個使用者放手**。新請求查表拿到新 node，舊請求手上還是舊 node，兩者各自計數、各自回收，互不干擾。整個過程靠 `uring_lock` 序列化，不需 RCU。

## 串起來：一塊 buffer 的完整一生

把前面的零件接成一條時間線。看 register 的核心骨架（`io_uring/rsrc.c`，以 bvec 註冊路徑為例，buffer register 骨架相同）：

```c
	io_ring_submit_lock(ctx, issue_flags);
	if (index >= data->nr) { ret = -EINVAL; goto unlock; }
	index = array_index_nospec(index, data->nr);
	if (data->nodes[index]) { ret = -EBUSY; goto unlock; }

	node = io_rsrc_node_alloc(ctx, IORING_RSRC_BUFFER);
	...
	imu = io_alloc_imu(ctx, blk_rq_nr_phys_segments(rq));
	...
	refcount_set(&imu->refs, 1);
	imu->release = release;
	...
	node->buf = imu;
	data->nodes[index] = node;
unlock:
```

跟著一塊 buffer 走一遍（假設登記在 slot 3）：

1. **register**：持 `uring_lock`，`index` 驗證 + `array_index_nospec`，確認 slot 3 是空的（否則 `-EBUSY`）。`io_rsrc_node_alloc` 配 node（`refs = 1`），`io_alloc_imu` 配 imu（`refcount_set(&imu->refs, 1)`），pin 好的 pages 填進 `imu->bvec[]`，`node->buf = imu`，`data->nodes[3] = node`。此刻:node.refs=1(表 slot 持有)、imu.refs=1。
2. **被請求引用**：一個 `IORING_OP_READ_FIXED` 帶 `buf_index=3` 提交。`io_find_buf_node` 在 `uring_lock` 下 `io_rsrc_node_lookup` 拿到 node 3，`node->refs++`（變 2），`req->buf_node = node`。此刻 node.refs=2（slot 1 份 + 這個請求 1 份）。
3. **請求完成**：完成路徑（Ch 25）放掉請求那份引用，`io_put_rsrc_node` → `--node->refs`（回 2→1）。沒歸零，node 還活著（slot 還持有）。
4. **unregister**（或 ring teardown，Ch 30）：`io_reset_rsrc_node` 放掉 slot 那份，`--node->refs`（1→0）→ 歸零 → `io_free_rsrc_node`：tag CQE 通知、`io_buffer_unmap`（imu.refs 1→0，真正 unpin pages、`imu->release`）、node 回 `node_cache`。

關鍵觀察：node.refs 在步驟 2–3 之間短暫是 2，正是「slot 持有 + in-flight 請求持有」。如果 unregister 發生在步驟 2 和 3 之間（請求還在飛），步驟 4 的 `--refs` 只會把 2 變 1，**node 不死**——等步驟 3 那個請求完成放手，才真正回收。這就是 Ch 29 整章在守的東西：**資源活到最後一個使用者放手**，無論那是 slot 還是 in-flight 請求。

## 底層機制：同步回收 vs 舊的 RCU 延遲回收

把兩代設計並排，才看得出 6.18 改了什麼、為什麼改。

**舊設計（~6.7 以前，你在舊 writeup 讀到的）**：
- `io_rsrc_node` 掛 `percpu_ref`（per-CPU 計數，讀多寫少時極快）。
- node 歸零 → 不能當場釋放（可能有 reader 在 RCU 讀區用著）→ 排進一條延遲釋放鏈 → RCU callback / work 在 grace period 後才真正 free。
- 查表/用 node 在 `rcu_read_lock()` 保護下，不必拿 `uring_lock`。
- 好處：引用的熱路徑無鎖、可擴展。壞處：**極其複雜**，node 生命週期橫跨 RCU grace period，update/cancel/teardown 的同步難到爆，是 CVE 的重災區。

**新設計（6.8–6.10 改寫後，6.18 現況）**：
- `io_rsrc_node` 只有 `int refs`，所有增減在 `uring_lock` 下。
- 歸零當場同步 `io_free_rsrc_node`，node 回 alloc_cache。
- 查表/用 node 都在 `uring_lock` 下。
- 好處：**簡單、好推理、好審計**，生命週期不跨 grace period。壞處：引用熱路徑要拿 mutex——但 io_uring 本來提交路徑就持 `uring_lock`（Ch 23），所以幾乎沒有額外成本。

取捨的本質：舊設計為了「無鎖可擴展」付出了「複雜到容易出 UAF」的代價；新設計發現「反正提交路徑都持 uring_lock 了，直接在鎖下同步計數，簡單又夠快」。這是 io_uring 社群近年「用簡化換安全」趨勢的典型——很多 rsrc 類 CVE 的 root cause 在舊的 RCU 延遲回收，改寫後整類問題消失。Part 4 看舊 CVE 時，要記得「這個 bug 所在的機制在 6.18 可能已經不存在了」。

## RCU 在 6.18 io_uring 的真正角色

既然 rsrc 不用 RCU 了，釐清 RCU 現在到底守什麼（`grep -rn "rcu" io_uring/` 的結果分類）：

| 用途 | 位置 | 守什麼 |
|---|---|---|
| request 型別安全 | req cache 的 `SLAB_TYPESAFE_BY_RCU`（Ch 27） | lock-free 讀「可能已釋放的 req」時，記憶體仍是同型別 req |
| eventfd 回收 | `call_rcu(&ev_fd->rcu, ...)`（Ch 25） | eventfd context 在 reader 退出 grace period 後才釋放 |
| poll waitqueue | `io_poll.c` 的 `rcu_read_lock`（Ch 28） | 遍歷/操作 waitqueue 時防 waitqueue 被釋放 |
| io-wq worker 查找 | `io-wq.c` 多處 `rcu_read_lock` | 無鎖遍歷 worker free_list |
| teardown 收尾 | `synchronize_rcu()`（Ch 30） | 等所有 `io_req_local_work_add` 的 RCU 讀區結束才拆 ctx |

所以若要橫向連結 RCU 概念，去看 [../kernel_internals/27-rcu.md](../kernel_internals/27-rcu.md) 理解「為什麼 lock-free 讀需要 grace period」——但要帶著正確認知：**6.18 的 rsrc_node 回收不在這張表上**。理解 RCU 對讀懂 Ch 27 的 `SLAB_TYPESAFE_BY_RCU` 和 Ch 30 的 `synchronize_rcu()` 仍然必要，只是別再把它套到 rsrc_node。

## clone buffers：第二層 refcount 的唯一用武之地

`io_mapped_ubuf.refs`（那個真正的 `refcount_t`）平常都是 1，只有一個功能讓它 >1：**buffer cloning**（`IORING_REGISTER_CLONE_BUFFERS`）。你可以把一個 ctx 的 registered buffers「複製」到另一個 ctx——但不是真的複製那些 pinned pages（太貴），而是讓兩個 ctx 的 node 指向**同一塊 `io_mapped_ubuf`**，`imu->refs++`。`io_uring/rsrc.c` 的 clone 路徑：

```c
			src_node->refs++;
			...
			dst_node = io_rsrc_node_alloc(ctx, IORING_RSRC_BUFFER);
```

兩個 ctx 各自有獨立的 node（各自 `int refs`，各自 uring_lock 保護），但底下共享一個 `imu`（`refcount_t refs` 協調跨 ctx 的釋放）。這就是為什麼要兩層計數：**node 層是 per-ctx 的用量計數，imu 層是跨 ctx 的共享計數**。`io_buffer_unmap` 那個 `refs > 1` 的判斷就是給 clone 場景的——共享時只 dec，獨享時才 unmap。

這也是 Part 4 一類 CVE 的來源：clone 讓 buffer 跨 ctx 共享，兩層計數的同步一旦有洞，就是跨 ctx 的 UAF（Ch 37）。

## 對比與取捨

| 面向 | file node | buffer node |
|---|---|---|
| 第二層 | 無（union 直接存 `file_ptr`） | `io_mapped_ubuf`（可跨 node 共享） |
| 回收真實資源 | `fput` | `io_buffer_unmap`（可能只 dec imu refs） |
| refs 型別 | node 的 `int refs`（uring_lock） | node `int refs` + imu `refcount_t`（共享） |
| 共享場景 | 無 | clone buffers 跨 ctx 共享 imu |

兩者共用 node/data 框架，差別只在「node 底下指的東西」。這個統一框架（`io_rsrc_node` + `io_rsrc_data`）讓 files 和 buffers 共享同一套查表、計數、update、回收邏輯，是 6.18 改寫的另一個簡化收穫——舊設計 files 和 buffers 各有一套，現在合流。

## 踩雷集錦

1. **拿「percpu_ref + RCU 延遲回收」讀 6.18 rsrc**：最大的坑。6.18 的 `io_rsrc_node` 是 `int refs` + `uring_lock` 同步回收，**沒有** percpu_ref、**不經** RCU grace period。舊 writeup、舊 CVE 分析講的那套在 6.18 不成立。

2. **混淆兩層引用計數**：`node->refs`（int，per-ctx 用量，uring_lock 保護）和 `imu->refs`（refcount_t，跨 ctx 共享，只在 clone 時 >1）是**不同東西**。以為「buffer 的引用計數」只有一個，會看不懂 clone 和 unmap 的邏輯。

3. **以為 update 會立刻釋放舊 node**：不會。`io_reset_rsrc_node` 只 `refs--`，舊 node 活到最後一個 in-flight 請求放手。新舊請求各用各的 node。沒懂這點，會誤以為 update 後舊請求會踩到釋放的記憶體——恰恰相反，這個設計就是為了防那個。

4. **在沒持 uring_lock 時碰 node refs**：`io_put_rsrc_node` 有 `lockdep_assert_held(&uring_lock)`。所有 node 引用增減都必須在鎖內。這是新設計正確性的全部基礎，繞過它就是未定義行為。

5. **忽略 `array_index_nospec`**：`io_rsrc_node_lookup` 的邊界檢查不只是防越界，還防 Spectre v1。index 來自 user，驗證 + nospec 缺一不可。Part 4 的 rsrc CVE 很多卡在「index 驗證和 node 生命週期的交互」。

6. **以為 tag CQE 是操作結果**：`io_free_rsrc_node` 裡 `io_post_aux_cqe(ctx, node->tag, 0, 0)` 吐的是「資源已回收」通知，不是某個 I/O 操作的結果。user 要能分辨 tag CQE 和一般 op CQE（靠 user_data / tag 區分）。

## 本章重點整理

- **版本更正**：6.18 的 `io_rsrc_node` 用**普通 `int refs` + `uring_lock` 同步回收**，**不是**舊設計的 `percpu_ref` + RCU 延遲回收（那套在 6.8–6.10 被改寫移除）。拿舊心智模型讀 6.18 會整個對不上。
- 結構：`io_rsrc_data`（node 指標表，ctx 有 file_table 和 buf_table 兩張）→ `io_rsrc_node`（type/refs/tag/union）→ files 直接存 `file_ptr`，buffers 指 `io_mapped_ubuf`（含 pinned pages、release callback）。
- **兩層引用計數**：`node->refs`（int，per-ctx 用量，uring_lock 保護）；`io_mapped_ubuf.refs`（refcount_t，只在 **clone buffers 跨 ctx 共享**時 >1）。
- 配置從 per-ctx `alloc_cache`（`node_cache`/`imu_cache`）拿，回收也回 cache——**這是新設計取代舊 RCU 延遲釋放的機制**。
- update 換 node 不改原 node：`io_reset_rsrc_node` 放舊、填新，舊 node 活到最後一個 in-flight 使用者放手。全程 `uring_lock` 序列化。
- 回收 `io_free_rsrc_node`：tag CQE 通知 → `fput` / `io_buffer_unmap` → node 回 cache。
- RCU 在 6.18 io_uring 仍用於 req 型別安全、eventfd、poll、io-wq、teardown，但**不用於 rsrc_node 回收**。

## 自我檢核

- [ ] 我能說出 6.18 的 `io_rsrc_node` 怎麼計數、怎麼回收，以及它和舊 percpu_ref/RCU 設計的差別
- [ ] 我能分清 `node->refs` 和 `imu->refs` 的角色，並說出後者什麼時候才 >1
- [ ] 我能描述一個 registered buffer 從 register、被請求引用、update、到真正釋放的完整路徑
- [ ] 我能解釋為什麼 update 後舊 in-flight 請求不會踩到釋放的 node
- [ ] 我知道 6.18 io_uring 的 RCU 實際用在哪幾處（不含 rsrc_node）
- [ ] 我能說出 clone buffers 為什麼需要兩層計數

## 延伸閱讀

### 必讀

- **Linux 6.18 原始碼 `io_uring/rsrc.c` 與 `rsrc.h`** — <https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/io_uring/rsrc.c?h=v6.18>
  - **讀哪裡**：`io_rsrc_node_alloc`、`io_free_rsrc_node`、`io_put_rsrc_node`、`io_reset_rsrc_node`、`io_buffer_unmap`
  - **和本章關聯**：本章所有結構/函式名都可在此 grep 到；親眼確認「`int refs`、無 percpu_ref」

### 改寫的歷史（核對本章的版本更正）

- **kernel git log 搜 `io_rsrc_node` 的改寫 commit 系列**（約 v6.8–v6.10，作者 Pavel Begunkov）
  - **讀哪裡**：commit message 說明「為什麼把 percpu_ref/RCU rsrc_node 改成同步 int refs」
  - **和本章關聯**：這是本章版本更正的一手依據；也讓你讀舊 CVE 時知道「這個機制還在不在」

### 底層機制（repo 內）

- **[../kernel_internals/27-rcu.md](../kernel_internals/27-rcu.md)**
  - **讀哪裡**：RCU grace period 與 `SLAB_TYPESAFE_BY_RCU`
  - **和本章關聯**：理解**舊** rsrc 設計為何用 RCU、以及 6.18 RCU 真正的用武之地（req、teardown）；**但別把 RCU 套回 6.18 的 rsrc_node**

- **[../kernel_internals/18-slub-allocator.md](../kernel_internals/18-slub-allocator.md)**
  - **讀哪裡**：物件生命週期、freelist、`SLAB_TYPESAFE_BY_RCU`
  - **和本章關聯**：`alloc_cache` 是 io_uring 在 slab 之上自建的 per-ctx 快取，先懂 slab 才懂它省什麼

### 安全（前瞻 Part 4）

- **[Understanding and Detecting Harmful Memory-based Vulnerabilities in io_uring](https://arxiv.org)**（搜標題）
  - **讀哪裡**：rsrc 生命週期相關的漏洞分類
  - **注意**：留意論文對應的 kernel 版本——rsrc 類 bug 很多在舊的 percpu_ref/RCU 設計，6.18 改寫後整類可能已消失

rsrc 的回收是「資源沒人用了就放」。但還有一類更劇烈的「放」：整個 ring 要拆掉的時候——process exit、`close(ring_fd)`、或主動 cancel 所有 in-flight 請求。那時引用計數要以正確順序歸零、in-flight 請求要被取消、percpu_ref 要 kill——順序錯一步就是 UAF。這是下一章，也是 Part 4 攻擊面的直接前奏。

→ [Ch 30 cancel 與 teardown：引用計數、ring 拆除](./30-cancel-teardown.md)
