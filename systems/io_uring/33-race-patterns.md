# Ch 33 — 競態類漏洞：io-wq / task_work race

> **目標**：把 io_uring 的並發來源（io-wq worker、task_work、SQPOLL、multishot）逐一攤開，看清楚每一個「為什麼會產生 race window」，並歸納出幾種可在 code review 裡辨識的競態模式：user memory double-fetch、completion vs cancel、teardown race。這章給防禦者一套「看到什麼結構就該警覺」的直覺，延續 Ch 32 的 UAF——race 往往正是那個「錯誤的 free」被觸發的方式。

> **環境**：對照 Linux 6.18 `io_uring/`（WSL2 6.18.58.1）。本章是並發模式分析與原始碼對照，不含可執行攻擊碼。並發原語（lock、RCU、記憶體屏障）基礎見 [kernel_internals Ch 27](../kernel_internals/27-rcu.md) 與本 repo [kernel_pwn Ch 39–40](../../security/kernel_pwn/39-concurrency-model.md)。所有分析以建立防禦與審計能力為目的。

## 為什麼 race 是 io_uring 的結構性問題，而非偶發 bug

Ch 31 把「非同步多執行流」列為 io_uring 漏洞的頭號結構性原因。這一章我們把那句話展開到可操作的程度。傳統 syscall 的並發模型相對單純：一個 `read()` 從進 kernel 到返回，基本上活在一條 task context 裡。io_uring 不是——它刻意讓一個請求能在**四種不同的執行環境**裡被推進：

```
                    一個 io_kiocb 可能被這些 context 碰到
 ┌─────────────┐  ┌──────────────┐  ┌─────────────┐  ┌──────────────┐
 │ 提交 task   │  │ io-wq worker │  │ poll 回呼    │  │ task_work    │
 │ (syscall)   │  │ (kernel thr) │  │ (irq/softirq)│  │ (回 task 時) │
 └──────┬──────┘  └──────┬───────┘  └──────┬──────┘  └──────┬───────┘
        └────────────────┴─────────┬───────┴────────────────┘
                                    ▼
                   它們之中隨時可能有「兩條同時碰同一個 req」
                   → 這個重疊的時間段就是 race window
```

每多一個能碰請求的 context，可能的 context 兩兩組合就多一組，每一組都是一個潛在 race window。

這裡的組合爆炸是量化的：n 個 context 兩兩組合是 n(n-1)/2 組。四個 context 已經是 6 組要協調的重疊，再加上 SQPOLL（一條獨立 kernel thread 自己輪詢 SQ）和 multishot（一個請求反覆產生多個完成），要同時在腦中維持的「誰可能和誰重疊」就更多。**race 在 io_uring 裡不是「某個函式沒加鎖」的偶發疏忽，而是「這個子系統天生有很多條執行流要協調」的結構性後果。** 防禦者的任務不是消滅並發，而是學會辨識「哪些重疊沒有被正確序列化」。

## 先建立直覺：三種 race 模式

io_uring 的競態五花八門，但絕大多數落在三個模式裡。先用圖建立形狀，後面各給一節細節。

```
 模式 A：double-fetch (TOCTOU on 共享記憶體)
   kernel: 讀 user 欄位 → 檢查 → ……稍後……→ 再讀 → 使用
   user:                      ▲ 趁這裡改掉值
   後果：檢查的是舊值，使用的是新值 → 繞過驗證

 模式 B：completion vs cancel (兩條路爭同一個 req)
   路 1 (完成): 我要填 CQE 並釋放 req
   路 2 (取消): 我要把這個 req 拆掉
   若兩條同時進行且沒序列化 → double free / UAF

 模式 C：teardown race (ring 關閉 vs in-flight 請求)
   主路: ring 要關了，開始回收所有物件
   支路: 還有 worker / task_work 握著 in-flight req 在跑
   若回收和使用重疊 → UAF
```

這三個模式不是互斥的——一個真實 CVE 常常是「用模式 A 製造一個時間差，讓模式 B 的競態窗口被穩定命中」。但分開辨識能讓 review 有章法。那些最棘手的 io_uring race CVE，幾乎都是這種複合型：單獨一個模式可能只是「理論上有窗但難命中」，兩個疊起來才變成「穩定可觸發」。所以辨識時不只要問「這是哪個模式」，還要問「有沒有另一個模式能幫它把窗撐大」。

一句話記住三者的分工：**模式 A 是「user 和 kernel 對共享記憶體的時間差」，模式 B 是「kernel 內兩條路對同一請求的爭用」，模式 C 是「回收和使用的重疊」。** A 的對手是 user、戰場在 SQE；B、C 的對手是 kernel 自己的另一條執行流、戰場在請求物件的生命週期。審計時先判斷「這段 code 的並發是和 user 爭、還是和另一條 kernel 路爭」，就能快速鎖定該用哪個模式去看。

## 模式 A：user memory double-fetch（SQE TOCTOU）

io_uring 的 SQE 存在 user 與 kernel **共享**的記憶體（Ch 4）。kernel 在處理一個請求時，如果從 SQE 讀同一個欄位兩次，user 可以用另一條執行緒在兩次讀之間改掉它——這就是 double-fetch / TOCTOU。Ch 31 引過 `io_get_sqe` 上方那段註解，核心一句是「we cannot rely on userspace always being a good citizen」。

防禦這類問題有兩道紀律，分析時要分清楚：

**紀律一：每個欄位只讀一次，用 `READ_ONCE`。** 看 `io_init_req`（`io_uring.c`）怎麼讀 SQE：

```c
req->opcode = opcode = READ_ONCE(sqe->opcode);
sqe_flags = READ_ONCE(sqe->flags);
req->cqe.user_data = READ_ONCE(sqe->user_data);
...
req->cqe.fd = READ_ONCE(sqe->fd);
```

`READ_ONCE` 的作用是「把欄位讀進一個本地變數，之後都用這個本地副本，不讓編譯器偷偷重新從共享記憶體載入」。只要驗證和使用都基於同一個 `READ_ONCE` 的結果，double-fetch 就關上了。

**紀律二：async 路徑要把整個 SQE 拷貝下來。** 問題在於有些請求不是當場處理完——它會被丟去 io-wq 稍後執行。那時原本的 SQE 記憶體（還在 user 手上）早就可能被改掉或覆用。6.18 的解法是 `io_req_sqe_copy`（`io_uring.c`）：

```c
static int io_req_sqe_copy(struct io_kiocb *req, unsigned int issue_flags)
{
	const struct io_cold_def *def = &io_cold_defs[req->opcode];

	if (req->flags & REQ_F_SQE_COPIED)
		return 0;
	req->flags |= REQ_F_SQE_COPIED;
	if (!def->sqe_copy)
		return 0;
	if (WARN_ON_ONCE(!(issue_flags & IO_URING_F_INLINE)))
		return -EFAULT;
	def->sqe_copy(req);
	return 0;
}
```

在請求轉入 async（例如 `io_queue_async` 裡）之前，把需要的 SQE 內容**拷進請求自己的記憶體**，之後 worker 用的是這份內部拷貝，user 再怎麼改原始 SQE 都影響不到。`REQ_F_SQE_COPIED` 保證只拷一次。

> **辨識要點**：審一個 opcode 的 prep/issue，問三件事——(1) 每個 SQE 欄位是不是只透過 `READ_ONCE` 讀一次？(2) 有沒有哪個欄位被讀了、檢查了、稍後又重讀一次？(3) 這個 opcode 會走 async 嗎？會的話它有沒有正確的 `sqe_copy`、且在轉 async 前被呼叫？任何一處「檢查用的值」可能不等於「使用的值」，就是 double-fetch 嫌疑。

## 模式 B：completion vs cancel——poll 的 ownership 機制

io_uring 最精巧也最容易出 race 的地方，是**同一個請求可能同時有「完成事件」和「取消請求」在爭著處理它**。最典型的是 poll：一個 poll 請求可能此刻正被 `io_poll_wake`（fd 就緒的回呼，在中斷/軟中斷 context）喚醒，同時 user 又下了一個 cancel，同時它的 task_work 又被排上——三條路都想碰同一個 `io_kiocb`。

6.18 用一套「ownership（所有權）」機制序列化這件事。核心是 `req->poll_refs` 這個 atomic，把「誰有權修改這個請求」編碼進去。看 `poll.c`：

```c
#define IO_POLL_CANCEL_FLAG	BIT(31)
#define IO_POLL_RETRY_FLAG	BIT(30)
#define IO_POLL_REF_MASK	GENMASK(29, 0)

/*
 * If refs part of ->poll_refs (see IO_POLL_REF_MASK) is 0, it's free. We can
 * bump it and acquire ownership. It's disallowed to modify requests while not
 * [holding ownership].
 */
static inline bool io_poll_get_ownership(struct io_kiocb *req)
{
	if (unlikely(atomic_read(&req->poll_refs) >= IO_POLL_REF_BIAS))
		return io_poll_get_ownership_slowpath(req);
	return !(atomic_fetch_inc(&req->poll_refs) & IO_POLL_REF_MASK);
}
```

機制是這樣運作的：`poll_refs` 的低 30 bit 是一個計數器，高位是 CANCEL 與 RETRY 旗標。任何想碰這個請求的路，都先 `atomic_fetch_inc`；**如果它是把計數從 0 拉到 1 的那一個，就取得 ownership**，有權處理請求；否則它只是留下了「我也想碰」的痕跡（計數 +1），然後退開，由當前 owner 處理完後檢查計數有沒有變、要不要再跑一輪。cancel 不直接動請求，而是 `atomic_or(IO_POLL_CANCEL_FLAG, ...)` 設旗標，等 owner 看到旗標後代為拆除。這就是那句關鍵不變量：**「不持有 ownership 時不准修改請求。」**

這套機制寫對了，它把「completion vs cancel vs task_work」三方爭用收斂成「永遠只有一個 owner 在動請求」。但它也精確地劃出了 race 的危險區：

- **任何新增的、會碰 poll 請求的路徑，都必須走 `io_poll_get_ownership` 這道門。** 繞過它直接改 `req->flags` 或釋放請求，就破壞了不變量 → double free / UAF。
- **owner 處理完要正確地 `atomic_sub` 並檢查殘餘計數**（`poll.c` 的主迴圈 `do { ... } while (atomic_sub_return(v, &req->poll_refs) & IO_POLL_REF_MASK)`）。少減、多減、或漏掉「減完發現別人又來過、要再跑一輪」的檢查，都會讓請求被提早釋放或永遠不釋放。

double poll（一個 fd 用多個 waitqueue，read 一個、write 一個）讓事情更複雜。`io_poll_double_prepare`（`poll.c`）的註解直接點名這個 race：

```c
/*
 * poll arm might not hold ownership and so race for req->flags with
 * io_poll_wake(). There is only one poll entry queued, serialise with
 * it by taking its head lock. ...
 */
if (head) {
	spin_lock_irq(&head->lock);
	req->flags |= REQ_F_DOUBLE_POLL;
	...
	spin_unlock_irq(&head->lock);
}
```

這裡在說：arm double poll 的這條路可能**還沒拿到 ownership**，卻要改 `req->flags`，而 `io_poll_wake` 可能同時也在動這個請求；解法是借用 waitqueue head 的 spinlock 來序列化。這是一段「開發者明知有 race、小心翼翼繞過去」的程式碼——對防禦者，這種註解是金礦：它標出了一個歷史上出過問題、至今仍脆弱的點。Ch 35 的 timeout/poll race 案例就圍著這類機制打轉。

### 走一遍：ownership 的 retry 迴圈怎麼擋住 race

光知道「有 ownership 機制」不夠，看它**怎麼收尾**才懂這套設計的精髓。`io_poll_check_events`（`poll.c`）的主迴圈是 owner 處理完一個 poll 事件後決定「要不要再跑一輪」的地方，骨架如下：

```c
do {
	v = atomic_read(&req->poll_refs);
	...
	if (v & IO_POLL_CANCEL_FLAG)
		return -ECANCELED;        // ① 有人設了 cancel 旗標 → 代為取消
	...
	/* 處理這次事件：vfs_poll、填 CQE、或 multishot 重新 issue */
	...
	v &= IO_POLL_REF_MASK;
	/*
	 * Release all references, retry if someone tried to restart
	 * task_work while we were executing it.
	 */
} while (atomic_sub_return(v, &req->poll_refs) & IO_POLL_REF_MASK);
```

這個迴圈的收尾是整套機制的關鍵，拆開看：

- owner 進來時假設自己有 `v` 個 ref（通常是 1）。處理完後用 `atomic_sub_return(v, ...)` **一次把這 `v` 個全減掉**，並讀回減完後的值。
- **如果減完後 `IO_POLL_REF_MASK` 的部分還非 0**，代表「我在處理這次事件的期間，又有別人（新的 wake、cancel、task_work）來 `atomic_fetch_inc` 過」——也就是有新的事件或請求在我忙的時候進來了。這時迴圈**再跑一輪**，把那個新進來的也處理掉。
- 只有當減完後計數歸零，才真正退出——此時保證「沒有任何未處理的並發請求遺留」。

這就是 ownership 機制擋住 race 的具體手法：**它不用鎖把並發擋在門外，而是讓並發的請求留下計數痕跡，由當前 owner 在收尾時檢查「我忙的時候有沒有人來過」，有就再處理一輪。** 開頭 `if (v & IO_POLL_CANCEL_FLAG)` 的檢查，正是「cancel 不直接動請求、只設旗標、由 owner 代為執行」那條路的落點。

對防禦者，這個迴圈示範了一個深刻的點：**正確性不在「加了鎖」，而在「減 ref 和檢查殘餘」這組操作的原子性與完整性**。任何改動如果破壞了「減 `v`、檢查殘餘、歸零才退出」這個不變量——例如少減、提早 return 沒走到 `atomic_sub_return`、或在迴圈中途釋放了請求——就會讓一個並發請求被遺漏或讓請求被提早釋放。審一個 poll 相關的 patch，第一件事就是確認它沒破壞這個收尾迴圈的完整性。

> **辨識要點**：看到一段 code 直接讀寫 poll 請求的 `flags`、直接釋放一個可能掛在 poll 上的請求、或在中斷 context 碰請求，立刻問「它持有 ownership 嗎？走了 `io_poll_get_ownership` 沒有？還是借了某個 lock？」沒有明確的序列化，就是模式 B 嫌疑。還要確認任何退出路徑都不繞過那個 `atomic_sub_return` 收尾。

## 模式 C：teardown race

ring 關閉（`io_ring_ctx` 釋放）時，可能還有一堆 in-flight 請求散落在 io-wq worker、task_work queue、poll waitqueue 裡。teardown 要把這些全部安全地收回來——這本身是個極難的並發問題，因為「開始回收物件」和「某條支路還在用那個物件」可能重疊。

io_uring 用幾道機制協調這件事，審計時要認得它們：

**worker 與 ring 的 refcount。** io-wq 的 worker 有自己的 `refcount_t ref`（`io-wq.c`），`io_worker_get` / `io_worker_release` 成對；整個 wq 也有 `atomic_t worker_refs`。teardown 要等這些歸零才能真正拆掉 worker pool。任何一條路在 worker 還活著時就釋放了它依賴的物件，就是 teardown UAF。

**fallback 路徑。** task_work 正常在 owning task 回到 kernel 時執行，但如果 task 正在消失（exit），task_work 無處可跑，io_uring 有 fallback 機制（`io_fallback_req_func` / `io_fallback_tw`，`io_uring.c`）把這些請求導到一個 workqueue 上善後。fallback 是 error/teardown 路徑，正是 Ch 32 說的「refcount 最容易算錯的地方」——它要在一個異常狀態下維持和正常路徑一致的生命週期收支。

**cancel-all。** teardown 會對所有 in-flight 請求下 cancel（`io_uring_try_cancel_requests` 一類），而 cancel 本身又要和正在完成的請求爭用（模式 B）。於是 teardown race 常常**疊加** completion vs cancel race——這也是為什麼 teardown 路徑的 bug 特別難重現、也特別難審。

> **辨識要點**：teardown / cancel / exit 路徑是 race 與 UAF 的共同高發區。審計時特別盯：(1) 回收物件前是否確保所有持有者（worker、task_work、poll entry）都已放手或被正確等待；(2) fallback 路徑的 refcount 收支是否和正常路徑一致；(3) cancel 是否正確走了 ownership 機制而非蠻力釋放。

## 底層機制：並發來源逐一點名

把 io_uring 的每個並發來源和它製造的 race window 對應起來，形成一張審計時的「警覺清單」：

| 並發來源 | 執行 context | 製造的 race window | 主要序列化機制 |
|---|---|---|---|
| **io-wq worker** | 獨立 kernel thread | 提交 vs 執行、執行 vs cancel/teardown | 接手即 `req_ref_get`（Ch 32 模式二）、worker refcount |
| **task_work** | owning task 回 kernel 時 | 排入 vs 執行、執行 vs exit | llist 原子操作、fallback 路徑 |
| **SQPOLL thread** | 專屬 kernel thread 輪詢 SQ | 無 `io_uring_enter` 的提交 vs user 改 SQE | `READ_ONCE` + 單一 submitter 不變量 |
| **poll 回呼** | 中斷/軟中斷 | completion vs cancel vs task_work | `poll_refs` ownership 機制 |
| **multishot** | 重複觸發 | 多次完成彼此、完成 vs 重新 arm | ownership + buffer 選取序列化 |

兩個特別容易被低估的來源：

- **SQPOLL** 讓 kernel thread 自己輪詢 SQ tail，連 `io_uring_enter` 都省了。這放大了 double-fetch 的窗——user 可以在 SQPOLL thread 讀 SQE 的同時改它，而且沒有 syscall 邊界當同步點。io_uring 靠 `READ_ONCE` 和「single submitter」的設計假設（`IORING_SETUP_SINGLE_ISSUER` 等，Ch 27）來收斂，但這也意味著**違反這些假設的新路徑特別危險**。SQPOLL 建立還要過 `security_uring_sqpoll()` LSM hook（`sqpoll.c`），這點 Ch 39 會用到。

- **multishot** 讓一個請求（accept、recv、poll）反覆產生多個 CQE 而不重新提交。這代表一個請求**長時間存活並反覆被處理**，每一次觸發都是一次「碰請求」，和 cancel/teardown 的重疊窗口因此被拉得很長。multishot 配 provided buffer 又多一層 buffer 選取的並發——這類組合路徑是 Ch 31 原因四（新機制交互）的典型高危區。

把這兩個對照一般的 oneshot 請求就更清楚：oneshot 請求的「存活期」短——提交、執行、完成、釋放，和 cancel 的重疊窗小。multishot 和 SQPOLL 各自從不同方向拉大了窗——multishot 拉長**請求的存活期**（和 cancel/teardown 重疊更久），SQPOLL 拉長**user 與 kernel 對 SQE 的並發期**（沒有 syscall 當同步點）。審計一個 opcode 時，先問「它支援 multishot 嗎？會在 SQPOLL 下用嗎？」——答案若都是否，它的 race 面相對可控；若有一個是，它的 race 窗就比直覺想的大得多，要用對應模式重點看。

## 底層機制延伸：task_work 的 llist 與 fallback

task_work 是 io_uring 最核心的並發原語之一，值得單獨看一眼它的無鎖設計，因為它直接示範了「用更複雜的機制換效能」這個取捨。task_work 的佇列是一個**無鎖單向鏈結串列**（lockless list，`llist`）。排入用 `llist_add`（一個 atomic），執行時用 `llist_del_all` 一次性把整串摘下來。看 `tctx_task_work_run`（`io_uring.c`）：

```c
struct llist_node *tctx_task_work_run(struct io_uring_task *tctx,
				      unsigned int max_entries, unsigned int *count)
{
	struct llist_node *node;

	if (unlikely(current->flags & PF_EXITING)) {
		io_fallback_tw(tctx, true);    // task 正在消失 → 走 fallback
		return NULL;
	}

	node = llist_del_all(&tctx->task_list);   // 無鎖摘下整串
	if (node) {
		node = llist_reverse_order(node);  // llist 是 LIFO，反轉回 FIFO
		node = io_handle_tw_list(node, count, max_entries);
	}
	...
}
```

這裡有三個對防禦者有意義的細節：

1. **`llist` 是後進先出（LIFO）。** `llist_add` 把新節點插在頭部，所以摘下來的順序和排入順序相反。io_uring 用 `llist_reverse_order` 反轉回 FIFO。**如果某條路徑依賴 task_work 的執行順序卻忘了這個反轉語意，就會有順序相關的 bug。** 這類「順序假設」是審計時容易漏的隱性不變量。

2. **`PF_EXITING` 的 fallback 是 race/UAF 高發區。** 當 owning task 正在退出，task_work 無處可跑，走 `io_fallback_tw` → `__io_fallback_tw` 把請求導到一個 per-ctx 的 `fallback_llist` 上，由 delayed workqueue 善後。看 `__io_fallback_tw` 裡成對的 `percpu_ref_get` / `percpu_ref_put(&last_ctx->refs)`——這是在異常狀態下維持 ctx 生命週期的關鍵。這條 fallback 路徑正是 Ch 32 說的「error path 上 refcount 最容易算錯」的典型，也是 teardown race（模式 C）的常見觸發地。

3. **`io_handle_tw_list` 按 ctx 分組持鎖。** 它遍歷整串請求，發現 `req->ctx` 換了就 `mutex_lock(&ctx->uring_lock)` + `percpu_ref_get(&ctx->refs)`。這代表一批 task_work 可能橫跨多個 ring，鎖是按 ctx 切換的——**跨 ctx 的 task_work 批次，是跨 ring 生命週期問題（如 Ch 32 模式四的跨 ring double free）的另一條路徑**。

這一眼看下來，一個「排 task_work」的動作背後是：無鎖 llist + LIFO 語意 + exit fallback + 跨 ctx 分組持鎖。每一層都是為效能而複雜化，每一層都是潛在的 race 來源。這就是下一節要講的取捨的具體化。

## 對比與取捨：為什麼不乾脆加大鎖

一個自然的問題：既然 race 這麼多，為什麼 io_uring 不乾脆用一把大鎖把所有東西串起來？答案就是 io_uring 存在的理由——**效能**。

| 做法 | 正確性 | 效能 | io_uring 的選擇 |
|---|---|---|---|
| 一把大鎖序列化所有操作 | 容易寫對 | 扼殺並發，失去 io_uring 意義 | 不採用 |
| 細粒度 lock + atomic ownership + 無鎖資料結構 | 極難寫對 | 高 | **採用** |

io_uring 選了「細粒度、大量 atomic、ownership 機制、llist 無鎖佇列」這條最難寫對的路，因為它的整個賣點就是「不要讓鎖成為吞吐量的瓶頸」。`poll_refs` 的 ownership 機制、`io_get_sqe` 的 `READ_ONCE`、task_work 的 llist，全都是「用更複雜的無鎖/細鎖設計換效能」的產物。**這個取捨直接解釋了為什麼 io_uring 的 race bug 又多又難找：它刻意站在並發正確性最難的那一端。** 防禦者理解這個取捨，就不會期待「總有一天把鎖加對就沒 race 了」——race 風險是這個設計哲學的內建成本。

## 踩雷集錦（分析時常見誤解）

1. **以為 `READ_ONCE` 就徹底解決 double-fetch。** `READ_ONCE` 只保證單次讀不被編譯器拆開或重載。如果一條路徑讀了欄位、檢查、稍後**又**從 user 記憶體重讀一次，中間 user 改了值，照樣 double-fetch。真正的防線是「驗證和使用基於同一份副本」，async 路徑還要 `sqe_copy`。

2. **把 poll 請求的 `flags` 當普通欄位讀寫。** poll 請求的修改受 ownership 機制保護。任何不持有 ownership 就動 `req->flags` 或釋放請求的 code，都違反核心不變量。看到直接操作就要追它的序列化來源。

3. **低估 SQPOLL 放大的 TOCTOU 窗。** 沒有 `io_uring_enter` 當同步點，SQPOLL thread 和 user 對 SQE 的並發是連續的。分析 SQPOLL 路徑要假設 user「隨時在改 SQE」，而非「提交後就不動」。

4. **以為 multishot 只是「省一次提交」。** 它讓請求長期存活並反覆被處理，把和 cancel/teardown 的重疊窗口拉長，還疊加 buffer 選取的並發。multishot 路徑的 race 面比 oneshot 大得多。

5. **只在 happy path 找 race。** teardown、cancel、exit、fallback 這些異常路徑才是 race（和 UAF）的高發區，因為它們要在非正常狀態下維持和正常路徑一致的序列化與 refcount 收支。

6. **把「有 `WARN_ON_ONCE` / 有長註解」的地方當成「已經處理好、不用看」。** 恰恰相反——那些是開發者標記「這裡有 race、我小心處理過」的點，也就是最脆弱、最該在新 patch 裡重點複審的點。

7. **以為「加了 `smp_*` barrier 就沒 race 了」。** 記憶體屏障解決的是「多核對記憶體操作的可見順序」，它**不**解決「兩條路都有權修改同一物件」的所有權問題。io_uring 的 race 大多是後者（ownership / 生命週期），靠的是 `poll_refs` 這類 ownership 機制與正確的 get/put，不是 barrier。看到一個 race 修復只動了 barrier，要特別警覺它到底修的是可見性還是所有權——兩者搞混，bug 會以為修了其實沒修。

## 進階：在 commit 裡嗅出 race 修復

讀 io_uring 的 patch 時，這些訊號強烈暗示它在修一個 race：

- commit message 出現 `race`、`UAF`、`use-after-free`、`double`、`ownership`、`teardown`、`cancel`、`concurrent`。
- diff 動到 `atomic_*`、`READ_ONCE`/`WRITE_ONCE`、`smp_*` barrier、`spin_lock` 的範圍、或 `poll_refs` / `req->refs` 的 get/put 位置。
- diff 在某條 error/cancel/teardown 路徑上補了一個 get 或移動了一個 put。
- 新增或移動了 `sqe_copy` 的呼叫點，或改了某個 `REQ_F_*` flag 的設定時機。

看到這些，就用本章三個模式去定位：它修的是 double-fetch（A）、completion/cancel 爭用（B）、還是 teardown（C）？這個歸類能讓你在幾分鐘內抓到一個陌生 race 修復的本質。Ch 35 的案例拆解、Ch 40 的「讀 patch 學 bug」都會反覆用到這個嗅覺。

補一個實務上很有用的審計起手式：**grep 並發原語，由原語反推 race 熱點**。io_uring 裡 race 最密集的地方，往往就是 atomic 與 ownership 操作最密集的地方。幾個高價值的搜尋：

```bash
grep -rn 'poll_refs'        io_uring/     # ownership 機制的全部落點（模式 B 熱區）
grep -rn 'atomic_or\|atomic_fetch' io_uring/  # 旗標式的並發通知（cancel/retry）
grep -rn 'llist_'           io_uring/     # task_work 的無鎖佇列（順序與 fallback）
grep -rn 'smp_load_acquire\|smp_store_release' io_uring/  # 需配對推敲的 barrier
```

這些 grep 的結果就是「這個子系統裡並發正確性最吃緊的地方」的清單。從清單出發，比從頭讀檔有效率得多——race 不會平均分佈，它集中在這些原語周圍。這也呼應本章的核心論點：io_uring 的 race 是結構性的，而結構的骨架就是這些 atomic / ownership / 無鎖佇列原語。

### race 為什麼比 UAF 更難找、更難修

值得多說一句 race 的特殊難處，這影響防禦者對 race 類 CVE 的風險評估：

- **難重現**：race 依賴精確的時序，同一個 bug 可能跑一萬次才命中一次。這讓 race 的測試覆蓋天生比記憶體 bug 差——fuzzer 可能跑過那段 code 卻從沒撞上那個窗。評估一個 race CVE 時，別因為「很難觸發」就輕視它：攻擊者有 userfaultfd、FUSE、調度操控等拉窗技巧（本 repo kernel_pwn Ch 41）能把一次性的機率變成穩定命中。
- **難確認修對**：修一個 race 常常是「加一個 ref、挪一個 lock、補一個 barrier」，而要證明「這樣就沒有任何殘餘 race」極難——因為你要論證的是「所有可能的交錯都安全」，而非「這一條路徑對」。這也是為什麼 io_uring 的同一塊 race-prone 程式碼（poll、cancel、teardown）會被反覆修補：一次修可能只堵了一個交錯，另一個交錯後來才被發現。
- **容易修出新 race**：改並發程式碼時，一個「順手」的 lock 範圍調整可能解開另一個不變量。這是為什麼 race 修復的 patch 特別值得用本章模式仔細複審——它既在修 race，也可能在製造 race。

這三點合起來說明：race 類漏洞是 io_uring 裡最「黏」的一類——難找、難修對、難一次修乾淨。防禦者對 poll/cancel/teardown 這幾塊要有「它永遠可能還有下一個 race」的長期警覺。

## 本章重點整理

- io_uring 的 race 是**結構性**的：它刻意讓一個請求能在提交 task、io-wq、poll 回呼、task_work 四種 context（再加 SQPOLL、multishot）被推進，每個重疊都是潛在 window。
- 三種可辨識模式：**A double-fetch（SQE TOCTOU，防線是 `READ_ONCE` + async 的 `sqe_copy`）**、**B completion vs cancel（poll 的 `poll_refs` ownership 機制，不變量是「不持 ownership 不改請求」）**、**C teardown race（回收 vs in-flight，常疊加 B）**。
- SQPOLL 放大 double-fetch 窗（無 syscall 同步點）；multishot 拉長與 cancel/teardown 的重疊窗。兩者都是高危但易被低估的並發來源。
- io_uring 選擇「細鎖 + atomic ownership + 無鎖佇列」而非大鎖，是用並發正確性的難度換效能——race 風險是這個設計哲學的內建成本。
- 高發區永遠是 error / cancel / teardown / fallback 路徑；`WARN_ON_ONCE` 與長註解是「這裡有 race」的路牌，不是「已安全」的保證。

## 自我檢核

- [ ] 我能說出 io_uring 的五個並發來源，並各對應一個它製造的 race window。
- [ ] 我能解釋 double-fetch 的兩道防線（`READ_ONCE` 與 `sqe_copy`），以及各自擋住什麼、擋不住什麼。
- [ ] 我能用自己的話描述 `poll_refs` 的 ownership 機制，並說出它維持的核心不變量。
- [ ] 我能解釋 SQPOLL 和 multishot 為什麼分別放大/拉長 race window。
- [ ] 讀一個 io_uring race 修復的 commit，我能把它歸到 A/B/C 三模式之一並定位改動的本質。

## 延伸閱讀

### 一手原始碼（最終仲裁）

- **[Linux 6.18 `io_uring/poll.c`](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/io_uring/poll.c)**
  - **讀哪裡**：`io_poll_get_ownership`、`poll_refs` 的 flag 定義、`io_poll_double_prepare` 的 race 註解、主迴圈的 `atomic_sub_return`
  - **和本章關聯**：模式 B 的全部機制依據
- **[`io_uring.c` 的 `io_get_sqe` / `io_req_sqe_copy`](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/io_uring/io_uring.c)**
  - **讀哪裡**：`io_get_sqe` 上方的 double-fetch 註解、`io_init_req` 的 `READ_ONCE` 群、`io_req_sqe_copy` 與 `REQ_F_SQE_COPIED`
  - **和本章關聯**：模式 A 的全部機制依據

### 並發基礎

- **本 repo [kernel_pwn Ch 39 並發模型](../../security/kernel_pwn/39-concurrency-model.md) 與 [Ch 40 race condition 漏洞](../../security/kernel_pwn/40-race-conditions.md)**
  - **讀哪裡**：TOCTOU、double-fetch、refcount race 的通用原理；本章是它在 io_uring 的具體化
- **本 repo [kernel_internals Ch 27 RCU](../kernel_internals/27-rcu.md)**
  - **讀哪裡**：RCU read-side 與 grace period；理解 `io_poll_double_prepare` 註解裡「head is RCU protected」的背景

### race 的拉窗技巧（理解攻擊方時機）

- **本 repo [kernel_pwn Ch 41 拉長 race window](../../security/kernel_pwn/41-widening-race-window.md)**
  - **讀哪裡**：userfaultfd / FUSE / 調度操控如何把一個理論 race 變成穩定可命中；理解防禦評估時「這個 race 有多好打」

### 設計脈絡

- **[LWN：io_uring poll / task_work 相關報導](https://lwn.net/Kernel/Index/#io_uring)**
  - **讀哪裡**：ownership 機制與 task_work 演進的報導，理解本章取捨一節的設計背景

---

我們把 io_uring 的並發來源和三種 race 模式拆清楚了。到這裡，Ch 31 的地圖、Ch 32 的 UAF 模式、Ch 33 的 race 模式，構成了讀懂任何一個 io_uring 記憶體漏洞所需的全部框架。接下來的 Ch 34–37 用四個真實 CVE 案例把這套框架跑實戰——第一個就是「fixed buffer type confusion 如何變成 UAF」，正好同時用到 Ch 32 的模式三和本章的序列化視角。

→ [Ch 34 CVE 拆解一：fixed buffer type confusion → UAF](./34-cve-case-one.md)
