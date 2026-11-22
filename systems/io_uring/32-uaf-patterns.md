# Ch 32 — UAF 模式總論：引用計數陷阱

> **目標**：把 io_uring 的 use-after-free（UAF）從「某個 CVE 的細節」提升成「一組可辨識的通用模式」。我們要能在讀一段 io_uring 程式碼時，指著某一行說「這裡如果走 error path 會少一次 put，就是 UAF」。這章給防禦者一套辨識框架；真正的個案拆解留給 Ch 34–37。

> **環境**：對照 Linux 6.18 `io_uring/`（WSL2 6.18.58.1）。本章是模式歸納與原始碼對照，不含可執行攻擊碼；利用為何可能只在概念層點到。slab 細節橫向連 [kernel_internals Ch 18](../kernel_internals/18-slub-allocator.md)、RCU 連 [Ch 27](../kernel_internals/27-rcu.md)。所有分析以建立防禦與審計能力為目的。

## 為什麼 UAF 是 io_uring 的頭號敵人

Ch 31 說過，UAF 加 race 佔了 io_uring CVE 的絕大多數，而這兩者常常是同一枚硬幣——race 是觸發方式，UAF 是後果。這一章我們專注在「後果」這一面：一個物件被釋放後，還有另一條路握著指向它的指標並去碰它。io_uring 把它放大成系統性問題，因為（回顧 Ch 31 的原因二、三）它有**一大堆互相引用的 refcounted 物件**，而這些物件的所有權在**多條 context** 之間反覆轉移。

UAF 之所以是「頭號敵人」而不只是「其中一類」，是因為它的利用價值特別高。一個被釋放的 slab 物件，攻擊者可以用自己可控的物件去「佔」回那塊記憶體（reclaim / heap spray），於是「kernel 以為是 A 物件、實際內容是攻擊者寫的 B 物件」——從這裡到任意讀寫、再到提權，是 kernel exploitation 的標準流程（本 repo 的 [kernel_pwn Ch 18](../../security/kernel_pwn/18-uaf-double-free.md) 講得很細）。io_uring 的請求物件和資源物件又特別「好噴」，所以它的 UAF 特別值錢。我們這章的任務是**辨識**這些 UAF 怎麼產生，不是去建構利用。

## 先建立直覺：UAF 的三拍結構

所有 UAF，不分子系統，都是同一個三拍節奏：

```
   拍 1：free            拍 2：reclaim           拍 3：use
 ┌──────────────┐     ┌──────────────────┐    ┌──────────────────┐
 │ 物件 X 被釋放 │ ──► │ 那塊 slab 記憶體  │ ─► │ 另一條路用「舊的  │
 │ (refcount 歸 │     │ 被別的分配佔回    │    │  X 指標」去讀/寫  │
 │  零 or 提早)  │     │ (攻擊者可控內容)  │    │  → 讀到/寫到 B    │
 └──────────────┘     └──────────────────┘    └──────────────────┘
      ▲ bug 在這                                   ▲ 崩潰/被利用在這
```

防禦者要盯的是**拍 1**——「物件為什麼會在還有人用的時候被釋放」。拍 2、3 是攻擊者的事，我們無法阻止攻擊者噴 heap，但我們可以讓拍 1 不發生。io_uring 的拍 1 幾乎永遠是**引用計數算錯**：該加沒加、該減的減兩次、在錯的時機減。所以這章叫「引用計數陷阱」。

把這個三拍套到 io_uring 的具體物件上，就得到下面四個通用模式。先用一張表預覽，讓你讀每個模式時知道它在整體裡的位置：

| 模式 | 出錯的物件 | 典型 root cause | 主要觸發路徑 |
|---|---|---|---|
| ① req refcount | `io_kiocb` | 漏設 `REQ_F_REFCOUNT` 或 error path 多 put | 多方引用的請求 |
| ② async 生命週期 | `io_kiocb` + `async_data` | 接手 get / 歸還 put 失衡、flag 不同步 | io-wq、task_work、cancel |
| ③ rsrc node | `io_rsrc_node` | lookup/use race、新舊版本機制混淆 | registered file/buffer 查找 |
| ④ 共享 buffer | `io_mapped_ubuf` | 跨 ring 的 refcount 收支不平 | clone buffers、teardown |

四個模式的共同骨架都是「拍 1：某個 refcount 在某條路徑上算錯」，差別只在出錯的是哪一層物件、錯在哪種路徑。下面逐一拆。

## 模式一：req refcount 錯誤——「按需啟用」的代價

Ch 31 看過 io_uring 的請求 refcount 是**按需啟用**的：預設一個 `io_kiocb` 沒有 refcount，靠「同一刻只有一條路擁有它」撐著；只有會被多方引用時才呼叫 `io_req_set_refcount` 打開 `REQ_F_REFCOUNT`。再看一次 `refs.h` 的判斷邏輯：

```c
static inline bool req_ref_put_and_test(struct io_kiocb *req)
{
	if (likely(!(req->flags & REQ_F_REFCOUNT)))
		return true;   // 沒啟用 → 直接回報「這是最後一個 ref，可以釋放」

	WARN_ON_ONCE(req_ref_zero_or_close_to_overflow(req));
	return atomic_dec_and_test(&req->refs);
}
```

這裡藏著 io_uring 最經典的 UAF 陷阱。設想一條路徑讓請求**被第二方引用了**（例如同時掛在 poll 上又被排進 task_work），卻**忘了先** `io_req_set_refcount`。那麼：

- 第一方用完呼叫 `req_ref_put_and_test`，因為 `REQ_F_REFCOUNT` 沒開，它**立刻回 `true`**，請求被釋放。
- 第二方還握著指標，拿去用——UAF。

反過來，如果某條 error path 在 refcount 已啟用的情況下**多 put 一次**，物件也會提早歸零。看 `__io_prep_linked_timeout`，它是「哪些請求需要兩個 ref」的正面範例：

```c
/* linked timeouts should have two refs once prep'ed */
io_req_set_refcount(req);
__io_req_set_refcount(req->link, 2);
```

linked timeout 讓一個請求同時被「它自己的完成路徑」和「timeout 的 fire 路徑」引用，所以要兩個 ref。這段寫對了。但它也劃出了危險區：**任何新增的、會讓請求被兩方引用的機制，都必須在它自己的 prep 路徑上做同樣的事**，而且在**所有**退出路徑（成功、各種失敗）上收支平衡。漏一條，就是模式一的 UAF。

> **辨識要點（code review）**：看到一個請求被加進第二個資料結構（掛 list、排 work、存進別人的欄位），立刻往上找「這之前有沒有 `io_req_set_refcount` / `req_ref_get`」，往下找「每條退出路徑有沒有對應的 put」。不平衡就是嫌疑。

## 模式二：async 執行時物件已被釋放

這是 Ch 31 原因一（非同步多執行流）最直接的 UAF 形態。一個請求被丟給 io-wq worker 或排進 task_work 後，**提交路徑和執行路徑在時間上分離**。如果在這段窗裡，另一條路（例如 cancel、ring teardown）把請求或它依賴的物件釋放了，而 worker 稍後才醒來去碰——UAF。

6.18 用一個明確的手法擋這類問題：**執行前先把 ref 拿好**。回顧 Ch 31 引過的 `io_wq_submit_work`：

```c
/* one will be dropped by io_wq_free_work() after returning to io-wq */
if (!(req->flags & REQ_F_REFCOUNT))
	__io_req_set_refcount(req, 2);
else
	req_ref_get(req);
```

worker 一接手就確保有自己的一份 ref，這樣就算別人在它執行期間 put，物件也不會在它手上消失。對稱地，`io_wq_free_work` 在返回時 `req_ref_put_and_test`，收支才平。這個「接手即 get、歸還即 put」的成對出現，是 async 路徑正確性的骨架。**UAF 幾乎都發生在這個成對關係被某條 error path 打破的時候**——例如一條路徑提早 return，跳過了 get，或者重複走了 put。

另一個常被忽略的釋放點是 `io_clean_op`，它在請求釋放前做清理：

```c
static void io_clean_op(struct io_kiocb *req)
{
	if (unlikely(req->flags & REQ_F_BUFFER_SELECTED))
		io_kbuf_drop_legacy(req);
	...
	if (req->flags & REQ_F_CREDS)
		put_cred(req->creds);
	if (req->flags & REQ_F_ASYNC_DATA) {
		kfree(req->async_data);   // ← async_data 在這裡被釋放
		req->async_data = NULL;
	}
	req->flags &= ~IO_REQ_CLEAN_FLAGS;
}
```

注意它靠 flag（`REQ_F_ASYNC_DATA`、`REQ_F_CREDS`…）決定清理什麼。**如果一條 async 路徑在設定/清除這些 flag 時和實際的物件狀態不同步**，清理就會錯位：該釋放的沒釋放（洩漏），或不該釋放的被釋放（double free / UAF）。flag 與物件狀態的一致性，是這類 bug 的核心。

> **辨識要點**：追一個會走 async 的 opcode 時，列出它用到的每個 `REQ_F_*` flag，確認「設 flag」和「配置物件」成對、「清 flag」和「釋放物件」成對，而且在所有 error path 上都成對。

## 模式三：rsrc node 的延遲釋放與「曾經的 RCU」

registered files / buffers（Ch 15、29）透過一層 `io_rsrc_node` 中介。6.18 的 node 釋放邏輯在 `rsrc.h`：

```c
static inline void io_put_rsrc_node(struct io_ring_ctx *ctx, struct io_rsrc_node *node)
{
	lockdep_assert_held(&ctx->uring_lock);
	if (!--node->refs)
		io_free_rsrc_node(ctx, node);
}
```

這裡有個對防禦者極重要、也極容易記錯的**版本事實**：**6.18 的 `io_rsrc_node.refs` 是一個普通 `int`，由 `uring_lock` 保護，不是 RCU、也不是 `percpu_ref`。** 看 `rsrc.h` 的結構定義就知道：

```c
struct io_rsrc_node {
	unsigned char			type;
	int				refs;        // ← 普通 int，非 refcount_t、非 RCU
	u64 tag;
	union {
		unsigned long file_ptr;
		struct io_mapped_ubuf *buf;
	};
};
```

為什麼要特別點這件事？因為**早期的 io_uring 資源管理用的是 `percpu_ref` + RCU + 一個 flush workqueue 的複雜延遲釋放機制**，那套機制本身製造過 UAF（node 在 RCU grace period 的時機問題）。社群後來（約 6.4–6.10 一連串重構）把它**大幅簡化**成現在這個「lock 保護的 int」。如果你讀的是舊版分析文章或舊 CVE，看到 RCU 延遲釋放 rsrc node，別把那套機制套到 6.18——會誤判生命週期。

這帶出模式三的兩個辨識點：

1. **lookup 後有沒有立刻 get？** 看 `io_find_buf_node`（`rsrc.c`）：

```c
node = io_rsrc_node_lookup(&ctx->buf_table, req->buf_index);
if (node) {
	node->refs++;              // ← lookup 成功立刻 +1，而且在 lock 內
	req->buf_node = node;
	...
}
```

`lookup → node->refs++` 必須在同一個 `uring_lock` 臨界區內原子完成。如果有路徑「先 lookup、放鎖、再 get」，中間 node 可能被 `io_reset_rsrc_node` 換掉並釋放——classic lookup/use race 轉 UAF。

2. **node 和 buffer 本體的兩層 refcount 要分清。** node 是中介，真正的 buffer 是 `io_mapped_ubuf`，它有自己的 `refcount_t refs`。一個 ubuf 可以被多個 node 共享（下個模式會看到 clone），所以「釋放 node」不等於「釋放 buffer」。混淆這兩層，會對「這次 put 到底釋放了什麼」判斷錯誤。

> **版本提醒**：分析 rsrc 相關 CVE 時，第一件事是確認 CVE 針對的 kernel 版本用的是舊 RCU 機制還是新 lock 機制。兩者的 root cause 分析完全不同。

## 模式四：buffer 共享與 double free

`io_mapped_ubuf` 可以被跨 ring 共享。`IORING_REGISTER_CLONE_BUFFERS` 讓一個 ring 把另一個 ring 的 registered buffer「複製」過來——實際上是共享同一份 ubuf，只是各自建 node 指向它。看 `rsrc.c` 的 clone 路徑：

```c
dst_node = io_rsrc_node_alloc(ctx, IORING_RSRC_BUFFER);
...
refcount_inc(&src_node->buf->refs);   // ← 共享 buffer，refcount +1
dst_node->buf = src_node->buf;
```

釋放端對稱地在 `io_buffer_unmap`：

```c
static void io_buffer_unmap(struct io_ring_ctx *ctx, struct io_mapped_ubuf *imu)
{
	if (unlikely(refcount_read(&imu->refs) > 1)) {
		if (!refcount_dec_and_test(&imu->refs))
			return;        // 還有別人用，不釋放
	}
	...
	imu->release(imu->priv);
	io_free_imu(ctx, imu);     // 最後一個才真正釋放
}
```

這段寫對了：共享時 `refcount_inc`，釋放時 `refcount_dec_and_test`，只有最後一個 holder 才真正 free。**double free 的風險在於這個不變量被破壞**——例如 clone 路徑在某條 error path 上已經 `refcount_inc` 了卻又走了釋放、或者兩個 ring 的 teardown 對同一個 ubuf 各自以為自己是最後一個。buffer 跨 ring 共享把單一 ring 的生命週期問題變成**跨 ring** 的問題，審計時要同時盯兩個 `io_ring_ctx` 的 teardown 順序。

> **辨識要點**：凡是「一個物件可被多個擁有者共享」（ubuf、某些 notif、registered ring）的地方，double free 的嫌疑就存在。檢查 clone/share 路徑的 `refcount_inc` 和所有釋放路徑的 `refcount_dec_and_test` 是否**嚴格配對**，尤其在 error path 和雙方 teardown 交錯時。

## 走一遍：從 `io_free_req` 看「誰負責最後釋放」

四個模式都圍著「誰在什麼時候釋放物件」轉。看一眼 6.18 請求釋放的實際落點 `io_free_req`（`io_uring.c`），能把這件事收束成一個清晰的心智模型：

```c
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

這段藏著兩個對防禦者很重要的觀念：

1. **「釋放」本身也被排成 task_work。** `io_free_req` 不直接 `kmem_cache_free`，而是把請求排進 task_work，交回 owning task context 去完成清理與釋放。這呼應 Ch 31 原因一——連釋放都走非同步路徑。**這代表「free 這個動作」和「其他碰這個請求的動作」之間，也存在排程上的先後與並發**，釋放不是一個瞬間完成的原子事件。審計時不能假設「呼叫了釋放函式 = 物件立刻沒了」。

2. **`REQ_F_CQE_SKIP` 標示「只釋放、不發 CQE」。** 這個 flag 的存在提醒我們：同一個「結束一個請求」的動作，依路徑不同有「發 CQE + 釋放」和「只釋放（例如被 cancel、被 link 失敗連帶取消）」兩種。**flag 決定走哪一種，而 flag 的設定時機如果和實際狀態不同步，就會錯發或漏發 CQE、錯釋放或漏釋放**——這正是模式二講的「flag 與物件狀態一致性」在釋放端的體現。

把這個觀察和四個模式接起來，得到一句可以帶走的總結：**io_uring 裡「物件被釋放」從來不是一個簡單的同步事件，而是一個由 refcount 決定時機、由 flag 決定方式、經常跨 context 非同步完成的過程。** UAF 的 root cause，永遠是這個過程裡某個「時機」或「方式」被算錯。這就是為什麼審計要同時盯 refcount（時機）和 `REQ_F_*` flag（方式）兩條線。

## 底層機制：slab 與 cross-cache，UAF 為何能被穩定利用

前面都在講拍 1（為什麼 free）。這一節概念性地補拍 2（reclaim），讓你理解為什麼 io_uring 的 UAF 普遍「可利用性高」——這是防禦評估時要懂的，不是要你去打。

`io_kiocb` 從一個專屬 slab cache 配置。看 `io_uring.c` 的 init：

```c
struct kmem_cache_args kmem_args = {
	.useroffset = offsetof(struct io_kiocb, cmd.data),
	.usersize = sizeof_field(struct io_kiocb, cmd.data),
	...
};
```

請求物件有固定大小、從可預測的 cache 來，這對攻擊者是好消息：他知道一個被釋放的 `io_kiocb` 會落在哪個 cache，就能用同大小的可控物件去 reclaim。當目標物件在專屬 cache（不和一般 kmalloc 混用）時，攻擊者還會用 **cross-cache** 技巧——把整個 slab page 釋放回 buddy allocator，再讓它被重新分配給另一個 cache，從而「跨 cache」地佔回那塊物理記憶體。cross-cache 的機制本課不展開，本 repo [kernel_pwn Ch 22](../../security/kernel_pwn/22-cross-cache.md) 有完整拆解，slab 基礎在 [kernel_internals Ch 18](../kernel_internals/18-slub-allocator.md)。

防禦者需要從這裡帶走的結論有兩個：

1. **「專屬 cache」不等於「安全」。** 它提高了 reclaim 的門檻，但 cross-cache 繞得過。不要因為一個物件有 dedicated cache 就把它的 UAF 評為低危。
2. **緩解措施作用在拍 2，不是拍 1。** `CONFIG_SLAB_FREELIST_HARDENED`、`init_on_free`、random kmalloc caches 這些讓 reclaim 更難、更不穩定，但它們**不修復 bug 本身**。真正的修復永遠是讓拍 1（錯誤的 free）不發生。Ch 39 談緩解時會再回到這個分工。

## 6.18 的防禦性設計：poison 與 WARN

值得注意 6.18 自己加了偵測性的防線。釋放一個快取的請求時會 poison 關鍵指標（`io_uring.c`）：

```c
static void io_poison_cached_req(struct io_kiocb *req)
{
	req->ctx = IO_URING_PTR_POISON;
	req->tctx = IO_URING_PTR_POISON;
	req->file = IO_URING_PTR_POISON;
	...
}
```

poison 的用意是：如果有哪條路徑在請求被快取回收後還去 deref 這些欄位，會立刻踩到一個明顯無效的指標而崩潰，而不是悄悄讀到殘值被利用。配上 `refs.h` 裡大量的 `WARN_ON_ONCE(!(req->flags & REQ_F_REFCOUNT))`，kernel 等於在易錯點上埋了一排哨兵。對防禦者，這些哨兵是**雙重訊號**：它們標出了開發者認為「這裡很容易錯」的位置——也就是你 review 時該多看兩眼的位置。

## 對比與取捨：io_uring UAF vs 一般子系統 UAF

| 面向 | 一般子系統 UAF | io_uring UAF |
|---|---|---|
| 觸發 | 常是單一路徑的 double free / 懸空指標 | 多由**跨 context 競態**觸發（配 Ch 33） |
| refcount 模型 | 通常每物件固定有 | 請求 refcount **按需啟用**，判斷更易錯 |
| 物件層次 | 多半單層 | 多層糾纏（req → node → ubuf），跨層算錯 |
| reclaim 難度 | 視物件而定 | 請求/資源物件**好噴**，可利用性偏高 |
| 修復位置 | error path 補 put/get | 同左，但常要同時顧多條 async 路徑 |

取捨的本質：io_uring 用「按需 refcount + 多層物件 + 多 context」換效能，把 UAF 的**辨識難度**和**利用價值**同時推高。這解釋了為什麼它的 UAF 又多又值錢。

## 踩雷集錦（分析時常見誤解）

1. **假設 `io_kiocb` 一定有 refcount。** 預設沒有。分析一次 put 前，先確認 `REQ_F_REFCOUNT` 這條路徑上有沒有被設過。假設它有，你會把「正常的最後一次釋放」誤讀成「提早釋放」，反之亦然。

2. **把 6.18 的 rsrc node 當成 RCU 延遲釋放。** 6.18 是 `uring_lock` 保護的普通 `int refs`，舊版才是 `percpu_ref` + RCU。讀舊 CVE 或舊文章時務必對齊版本，否則整個生命週期分析都偏。

3. **以為 double free 只會發生在同一個 ring。** 共享物件（`io_mapped_ubuf` 透過 clone 跨 ring）讓 double free 可以跨 `io_ring_ctx`。只盯一個 ring 的 teardown，會漏掉跨 ring 的收支不平。

4. **把「有 dedicated slab cache」當成「UAF 不可利用」。** cross-cache 技巧專門繞這個。dedicated cache 提高門檻，不等於封死利用。評估危害時別因此降級。

5. **盯 happy path 找 UAF。** io_uring 的 UAF 幾乎都在 error path（分配失敗、cancel、fd 失效、teardown）上——那是 get/put 最容易失衡的地方。`goto fail` 之後的幾行，才是該逐行數 refcount 的地方。

6. **把 `WARN_ON_ONCE` 當噪音略過。** 它們標的正是開發者認定的易錯點。看到一處 `WARN_ON_ONCE` 圍著 refcount，等於看到一個「這裡歷史上錯過」的路牌。

7. **把「洩漏（leak）」和「UAF」當成無關的兩回事。** 它們常是同一個 refcount 錯誤的兩面：少了一次 put → 物件永不釋放（leak）；多了一次 put 或少了一次 get → 物件提早釋放（UAF）。審計時看到一條路徑的 refcount 收支不平，先別急著判定是哪一種——先確認「它偏多還偏少」，方向決定了它是 leak 還是 UAF，但「不平衡」本身就是 bug。一個只造成 leak 的 patch，往往和一個造成 UAF 的 bug 共用同一塊易錯邏輯。

## 進階：把四個模式變成一份審計清單

拿到一段 io_uring 程式碼，按模式逐項問：

1. **（模式一）** 這個請求會不會被第二方引用？有的話，引用前有 `io_req_set_refcount` 嗎？每條退出路徑有對應 put 嗎？
2. **（模式二）** 它會走 async 嗎？接手處有 get、歸還處有 put 嗎？用到的 `REQ_F_*` flag 與物件配置/釋放成對嗎？
3. **（模式三）** 它查 rsrc node 嗎？`lookup → refs++` 在同一個 lock 臨界區嗎？node 與 ubuf 兩層分清了嗎？版本是新 lock 還是舊 RCU？
4. **（模式四）** 牽涉可共享物件嗎？所有 share 的 `refcount_inc` 和所有釋放的 `dec_and_test` 嚴格配對嗎？跨 ring teardown 順序安全嗎？

這四問走一遍，io_uring 絕大多數 UAF 的嫌疑點都會浮出來。Ch 34、35 的個案就是這份清單的實戰應用。

搭配一個機械化的起手式——**grep refcount 原語，由原語反推生命週期熱點**：

```bash
grep -rn 'req_ref_get\|req_ref_put\|io_req_set_refcount' io_uring/  # 請求 refcount（模式一、二）
grep -rn 'refs++\|--node->refs\|io_put_rsrc_node'        io_uring/  # rsrc node（模式三）
grep -rn 'refcount_inc\|refcount_dec_and_test'           io_uring/  # 共享物件（模式四）
```

每一個 get 都該能在同一條邏輯路徑上找到配對的 put。審計的機械部分就是「把每個 get 的所有退出路徑的 put 都數一遍」——grep 出 get/put 的全部落點，是這件事的第一步。

### 審計時優先盯的三種路徑

清單是全面的，但時間有限時，UAF 的命中率在這三種路徑上最高，值得優先掃：

1. **error path（`goto fail` / `goto err` 之後）**：這是全書反覆強調的第一熱點。正常路徑的 get/put 通常被測試與 fuzzer 洗過，而 error path 走到的機率低、組合多、最容易漏審。看到一個函式裡有 `goto`，先跳到 label 處往回讀每條路的 refcount 收支。
2. **cancel 與 teardown 路徑**：這些路徑要在「請求可能正被別處使用」的前提下釋放它，天生要和完成路徑爭用（接 Ch 33）。它們是「race 觸發 UAF」最常見的落點。
3. **新舊機制的交界**：例如「provided buffer 配 multishot」「registered buffer 配 clone」這種組合路徑（Ch 31 原因四）。單獨一個機制的生命週期可能很乾淨，兩個機制交互時「誰負責釋放共享物件」的歸屬常常沒被想清楚。

把這三種路徑當成審計的「優先掃描區」，能在有限時間裡最大化找到 UAF 的機率。這也和 Ch 40 的審計方法論直接接上。

## 本章重點整理

- UAF 的通用三拍：**free → reclaim → use**。防禦者只能也只需攻拍 1——讓「錯誤的 free」不發生；io_uring 的拍 1 幾乎永遠是**引用計數算錯**。
- 四個通用模式：**① req refcount 按需啟用的陷阱**、**② async 執行時物件已釋放（接手 get / 歸還 put 失衡）**、**③ rsrc node lookup/use race 與版本差異（6.18 是 lock 保護的 int，非 RCU）**、**④ 共享 buffer 的跨 ring double free**。
- 多數 io_uring UAF 落在 **error path**；審計時逐行數 `goto fail` 之後的 get/put 收支。
- slab/cross-cache 作用在拍 2，決定**可利用性**不決定 bug 是否存在；dedicated cache 提高門檻但不封死利用，緩解措施不等於修復。
- 6.18 的 `io_poison_*` 與 `WARN_ON_ONCE` 既是防線，也是「這裡易錯」的路牌。

## 自我檢核

- [ ] 我能畫出 UAF 三拍，並說明防禦者為什麼只攻拍 1。
- [ ] 我能解釋「按需啟用 refcount」如何同時造成「漏設 → 提早釋放」和「多 put → 提早釋放」兩種 UAF。
- [ ] 我能說出 6.18 的 `io_rsrc_node` refcount 機制，以及它和舊版 RCU 機制的差異，並說明為什麼這個差異對分析舊 CVE 很重要。
- [ ] 我能解釋 `io_mapped_ubuf` 跨 ring 共享為什麼把 double free 變成跨 `io_ring_ctx` 的問題。
- [ ] 我能用四個模式的審計清單，對一段陌生的 io_uring 程式碼列出 UAF 嫌疑點。

## 延伸閱讀

### 一手原始碼（最終仲裁）

- **[Linux 6.18 `io_uring/refs.h`](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/io_uring/refs.h)** 與 **[`rsrc.c` / `rsrc.h`](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/io_uring/rsrc.c)**
  - **讀哪裡**：`req_ref_put_and_test` 的 `REQ_F_REFCOUNT` 分支、`io_put_rsrc_node`、`io_find_buf_node` 的 `lookup → refs++`、`io_buffer_unmap` 的 `refcount_dec_and_test`
  - **和本章關聯**：四個模式的原始碼依據全在這兩個檔案

### 理解 reclaim（拍 2）

- **本 repo [kernel_pwn Ch 18 UAF / Double Free](../../security/kernel_pwn/18-uaf-double-free.md)** 與 **[Ch 22 Cross-cache](../../security/kernel_pwn/22-cross-cache.md)**
  - **讀哪裡**：SLUB freelist 如何被 reclaim 佔回、cross-cache 怎麼跨 dedicated cache
  - **和本章關聯**：補足本章「底層機制」一節刻意不展開的攻擊方細節
- **本 repo [kernel_internals Ch 18 SLUB allocator](../kernel_internals/18-slub-allocator.md)**
  - **讀哪裡**：kmalloc-N cache 與物件生命週期；理解 `io_kiocb` 的 dedicated cache 落在哪

### 資源管理重構脈絡

- **[LWN io_uring 資源管理相關報導](https://lwn.net/Kernel/Index/#io_uring)**（搜 rsrc / registered buffers）
  - **讀哪裡**：rsrc node 從 percpu_ref/RCU 簡化為 lock 保護的那幾次重構
  - **用途**：分析舊 rsrc CVE 時，先在這裡對齊「那個版本用的是哪套機制」

### 利用價值的真實範例

- **[Put an io_uring on it: Exploiting the Linux Kernel](https://chompie.rip/Blog+Posts/Put+an+io_uring+on+it+-+Exploiting+the+Linux+Kernel)** — chompie1337
  - **讀哪裡**：它如何把一個 io_uring UAF 從拍 1 推到穩定提權；只看「為什麼可利用」的論述，理解防禦評估時的危害判斷

---

我們把 UAF 拆成了四個可辨識的模式，核心都落在「引用計數在某條路徑上算錯」。但 UAF 的**觸發**——為什麼那個錯誤的 free 會剛好在另一方還握著指標時發生——往往是一個競態。下一章我們轉到硬幣的另一面：io_uring 的並發來源如何製造 race window，以及防禦者怎麼在 code review 裡認出這些窗。

→ [Ch 33 競態類漏洞：io-wq / task_work race](./33-race-patterns.md)
