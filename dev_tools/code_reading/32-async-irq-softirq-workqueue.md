# Ch 32 — 非同步控制流：中斷、softirq、workqueue、timer

> **目標**：看到任何一個 kernel 函式，能回答三個問題：**它在什麼 context 執行**（hardirq、softirq、process context 的哪一種 thread）、**誰觸發它**（註冊點在哪、排程點在哪）、**它能不能睡**。並且能從 `INIT_WORK`、`timer_setup`、`request_threaded_irq`、`open_softirq` 這些註冊點，反推出 callback 的執行環境，再用 ftrace 在 QEMU 裡親眼確認。

> **鴻溝**：本章主要練**間接鴻溝**（callback 的註冊點和執行點分在兩個地方，靜態閱讀時呼叫鏈直接斷掉）與**領域鴻溝**（不知道 hardirq / softirq / kworker 的差別，就看不懂為什麼某段 code 用 `spin_lock_irqsave` 而另一段用 `mutex`）。

> **環境**：Linux kernel v6.18 原始碼（`~/code_reading/linux`）；動態驗證用同版本 x86_64 build（`x86_64_defconfig` + ftrace，`CONFIG_PREEMPT_VOLUNTARY=y`、`CONFIG_PREEMPT_DYNAMIC=y`、`CONFIG_HZ=1000`、**沒有**開 `CONFIG_PREEMPT_RT`），QEMU TCG 開機、busybox initramfs。user-space 範例用 gcc 11.4 在 WSL Ubuntu 22.04 上跑。

## 為什麼需要這個？

Spinellis 在原書第 5 章談「進階控制流」時，處理的是 user-space 的三種非線性流程：signal handler、平行執行（thread）、nonlocal jump（`setjmp`/`longjmp`）。他的核心觀察是：**這些程式碼的執行順序不是由原始碼的排列決定的**，讀者必須在腦中另外建一個「什麼事件會讓這段 code 跑起來」的模型。

kernel 把這個問題放大了好幾倍。一個 user-space 程式只有一種 signal handler 環境；kernel 裡至少有五種執行環境，各自有不同的規則：

- **hardirq**：硬體中斷進來，CPU 當下正在跑誰就「借」誰的 stack 來跑 handler。中斷是關的，不能睡。
- **softirq**：hardirq 結束時順手處理的「下半部」，也可能被丟給 `ksoftirqd` thread。不能睡。
- **tasklet**：建在 softirq 上的舊 API，**已標為 deprecated**。
- **workqueue**：把工作丟給 `kworker` kernel thread，在 process context 執行，可以睡。
- **threaded irq / kthread**：專屬的 kernel thread，可以睡。
- **timer**：`timer_list` 在 softirq 執行，`hrtimer` 預設在 hardirq 執行。

這對讀碼的直接影響是：你在 driver 裡看到 `static void foo_work(struct work_struct *work)`，**用 `rg foo_work` 找不到任何直接呼叫它的地方**。它只出現在一行 `INIT_WORK(&dev->work, foo_work);` 裡，然後真正的呼叫發生在 `kernel/workqueue.c` 的一行 `worker->current_func(work);`——那行程式碼對所有 work 都一樣，不帶任何線索。這就是[Ch 28](./28-ops-tables-indirect-calls.md) 講的間接鴻溝，只是更難：ops table 至少還有一個 struct 型別可以追，非同步 callback 的「呼叫者」是一個排程器。

在 workqueue 出現之前，2.4 時代的 kernel 用的是 bottom half（BH）和 task queue；2.6 早期的 workqueue 每建一個就配一組 thread，機器上動輒上千個 kernel thread。v2.6.36 換成 concurrency-managed workqueue（cmwq，`alloc_workqueue` 就是那時加入的，用 `git show v2.6.36:include/linux/workqueue.h` 可以確認），v6.9 又加了 `WQ_BH`，讓 workqueue 也能在 softirq 執行，目標是取代 tasklet。這段演進史解釋了為什麼你今天讀 kernel 會同時看到四五種「延後執行」的寫法——它們不是同時設計的，是一層層疊上去的。

## 先建立直覺

把一個 CPU 想成一位廚師。他手上正在切菜（process context，某個 task 在跑 syscall）。

- **門鈴響了**（hardirq）：他必須立刻放下刀去應門，但只能做最少的事——簽收包裹、把它放在門口。這段時間他不能離開門口（不能睡）、不能接其他門鈴（中斷關閉）。
- **回到廚房之前**，他順手把門口的包裹拆一拆（softirq，在 `irq_exit` 時執行）。還是不能離開去睡午覺，但門鈴可以再響。包裹太多時，他會叫一個幫手專門拆（`ksoftirqd`）。
- **有些包裹要組裝很久**，例如要等零件寄到。這種事他寫在便條上丟給助理（workqueue）。助理是一個獨立的人（`kworker` thread），可以等、可以睡、可以排隊。
- **計時器**（timer）就是設好的鬧鐘：鬧鐘響的那一刻也算一種門鈴。

```
   時間 ─────────────────────────────────────────────────────────────▶

   task: read(2) ──┐                                   ┌── read(2) 繼續
                    │ 硬體中斷                           │
   hardirq          └─► handler (irqs off, 借 task 的 stack)
                           │ raise_softirq / queue_work / wake irq thread
                           ▼
   softirq (irq_exit)      handle_softirqs()  ── 太多 ──► ksoftirqd/N (thread)
                           │
                           ▼
   kworker/N:M (thread)    process_one_work() ─► foo_work()   ← 可以睡
   irq/NN-name (thread)    irq_thread() ─► thread_fn()        ← 可以睡
```

**這張圖的重點是：上半部的三行都在「別人的時間」裡執行**。hardirq 和 softirq 並沒有自己的 task，它們出現在 ftrace 輸出時，`TASK-PID` 欄位顯示的是當下被打斷的那個 task（本章後面會看到 `sh-69`、`sleep-75` 在跑 timer 中斷）。這是讀 trace 時最容易誤判的地方。

> 這個類比在「不能睡」這一點上成立；但它沒有表達出 softirq 可以在**另一顆 CPU 上平行執行同一個 handler** 這件事（廚師只有一個人，但 kernel 有 N 個 CPU），讀 softirq handler 的 locking 時要記得這點。

## Context 判斷表：讀碼時第一個要查的表

| 執行環境 | 典型註冊點 | 典型觸發點 | 執行者（ftrace 看到的 task） | 能睡？ | 被誰搶占 | 和 process context 共享資料要用 |
|---|---|---|---|---|---|---|
| hardirq | `request_irq(irq, handler, ...)` | 硬體 | 當下被打斷的 task 或 `<idle>` | **不能** | 只有 NMI | `spin_lock_irqsave` |
| softirq | `open_softirq(NR, fn)`（固定 10 個） | `raise_softirq()` | 被打斷的 task，或 `ksoftirqd/N` | **不能** | hardirq | `spin_lock_bh` |
| tasklet（deprecated） | `tasklet_setup(t, fn)` | `tasklet_schedule()` | 同 softirq | **不能** | hardirq | `spin_lock_bh` |
| BH workqueue（v6.9+） | `INIT_WORK` + `system_bh_wq` | `queue_work()` | 同 softirq | **不能** | hardirq | `spin_lock_bh` |
| `timer_list` | `timer_setup(t, fn, flags)` | `mod_timer()` | 同 softirq（`TIMER_SOFTIRQ`） | **不能** | hardirq | `spin_lock_bh` / `irqsave` |
| `hrtimer` | `hrtimer_setup(t, fn, clk, mode)` | `hrtimer_start()` | hardirq（預設）或 softirq（`_SOFT` mode） | **不能** | 依 mode | 依 mode |
| workqueue | `INIT_WORK(&w, fn)` / `INIT_DELAYED_WORK` | `queue_work()` / `schedule_work()` | `kworker/N:M` | **可以** | 一般 task 規則 | `mutex` 或任何 lock |
| threaded irq | `request_threaded_irq(irq, NULL, thread_fn, ...)` | hardirq 回 `IRQ_WAKE_THREAD` | `irq/NN-name`（SCHED_FIFO） | **可以** | 一般 task 規則 | `mutex` |
| kthread | `kthread_run(fn, data, name)` | 建立即跑 | 自己的名字 | **可以** | 一般 task 規則 | 任何 |

最右邊那一欄是讀 locking code 的鑰匙：**一個 lock 的變體洩漏了它保護的資料會被哪些 context 碰到**。看到 `spin_lock_irqsave(&ts->lock, flags)`，你應該立刻推論「這把 lock 保護的東西會在 hardirq（或關中斷的環境）裡被碰」；看到 `spin_lock_bh`，推論「softirq 會碰」；看到 `mutex_lock`，推論「所有使用者都在可睡的 context」。[Ch 30](./30-reading-locking-code.md) 講過 lock 規則，這張表把它和執行環境接起來。

## 底層機制：kernel 怎麼知道自己在哪個 context？

答案是一個整數：`preempt_count`。每個 CPU（x86 上是 per-CPU 變數）有一個計數器，不同的位元段記錄不同的巢狀深度。v6.18 的配置寫在註解裡（`include/linux/preempt.h:14-32（v6.18）`）：

```c
/*
 * We put the hardirq and softirq counter into the preemption
 * counter. The bitmask has the following meaning:
 *
 * - bits 0-7 are the preemption count (max preemption depth: 256)
 * - bits 8-15 are the softirq count (max # of softirqs: 256)
 *
 * The hardirq count could in theory be the same as the number of
 * interrupts in the system, but we run all interrupt handlers with
 * interrupts disabled, so we cannot have nesting interrupts. Though
 * there are a few palaeontologic drivers which reenable interrupts in
 * the handler, so we need more than one bit here.
 *
 *         PREEMPT_MASK:	0x000000ff
 *         SOFTIRQ_MASK:	0x0000ff00
 *         HARDIRQ_MASK:	0x000f0000
 *             NMI_MASK:	0x00f00000
 * PREEMPT_NEED_RESCHED:	0x80000000
 */
```

所有「我在哪」的巨集都是對這個整數做 mask（`include/linux/preempt.h:117-143（v6.18）`，非 RT 分支）：

```c
#define in_nmi()		(nmi_count())
#define in_hardirq()		(hardirq_count())
#define in_serving_softirq()	(softirq_count() & SOFTIRQ_OFFSET)
#ifdef CONFIG_PREEMPT_RT
# define in_task()		(!((preempt_count() & (NMI_MASK | HARDIRQ_MASK)) | in_serving_softirq()))
#else
# define in_task()		(!(preempt_count() & (NMI_MASK | HARDIRQ_MASK | SOFTIRQ_OFFSET)))
#endif

/*
 * The following macros are deprecated and should not be used in new code:
 * in_irq()       - Obsolete version of in_hardirq()
 * in_softirq()   - We have BH disabled, or are processing softirqs
 * in_interrupt() - We're in NMI,IRQ,SoftIRQ context or have BH disabled
 */
#define in_irq()		(hardirq_count())
#define in_softirq()		(softirq_count())
#define in_interrupt()		(irq_count())
```

注意 kernel 自己把 `in_interrupt()` 標成 deprecated，理由寫在註解最後半句：**「or have BH disabled」**。softirq 段的 8 個位元有兩種用途：`local_bh_disable()` 一次加 `SOFTIRQ_DISABLE_OFFSET`（= `2 * SOFTIRQ_OFFSET`，`include/linux/preempt.h:55（v6.18）`），真的在跑 softirq 時加 `SOFTIRQ_OFFSET`（= `0x100`）。所以「只是關了 BH 的 process context」也會讓 `in_interrupt()` 回傳真。

我們把這套 mask 原封不動搬到 user space，餵幾個典型值看看：

```c
/* pcdecode.c: 用 v6.18 include/linux/preempt.h 的位元配置解碼 preempt_count */
#include <stdio.h>
#include <stdlib.h>

#define PREEMPT_BITS  8
#define SOFTIRQ_BITS  8
#define HARDIRQ_BITS  4
#define NMI_BITS      4
#define PREEMPT_SHIFT 0
#define SOFTIRQ_SHIFT (PREEMPT_SHIFT + PREEMPT_BITS)
#define HARDIRQ_SHIFT (SOFTIRQ_SHIFT + SOFTIRQ_BITS)
#define NMI_SHIFT     (HARDIRQ_SHIFT + HARDIRQ_BITS)
#define __IRQ_MASK(x) ((1UL << (x)) - 1)
#define PREEMPT_MASK  (__IRQ_MASK(PREEMPT_BITS) << PREEMPT_SHIFT)
#define SOFTIRQ_MASK  (__IRQ_MASK(SOFTIRQ_BITS) << SOFTIRQ_SHIFT)
#define HARDIRQ_MASK  (__IRQ_MASK(HARDIRQ_BITS) << HARDIRQ_SHIFT)
#define NMI_MASK      (__IRQ_MASK(NMI_BITS) << NMI_SHIFT)
#define SOFTIRQ_OFFSET (1UL << SOFTIRQ_SHIFT)

static void decode(unsigned long pc)
{
	int in_task = !(pc & (NMI_MASK | HARDIRQ_MASK | SOFTIRQ_OFFSET));
	int in_interrupt = !!(pc & (NMI_MASK | HARDIRQ_MASK | SOFTIRQ_MASK));
	printf("0x%08lx  preempt=%lu softirq=%lu hardirq=%lu nmi=%lu | "
	       "in_task=%d in_interrupt=%d serving_softirq=%d bh_disabled_only=%d\n",
	       pc, pc & PREEMPT_MASK, (pc & SOFTIRQ_MASK) >> SOFTIRQ_SHIFT,
	       (pc & HARDIRQ_MASK) >> HARDIRQ_SHIFT, (pc & NMI_MASK) >> NMI_SHIFT,
	       in_task, in_interrupt, !!(pc & SOFTIRQ_OFFSET),
	       (pc & SOFTIRQ_MASK) && !(pc & SOFTIRQ_OFFSET));
}

int main(int argc, char **argv)
{
	for (int i = 1; i < argc; i++)
		decode(strtoul(argv[i], NULL, 0));
	return 0;
}
```

```
$ gcc -O2 -Wall -o pcdecode pcdecode.c && ./pcdecode 0 0x1 0x200 0x100 0x10000 0x10100 0x100000
0x00000000  preempt=0 softirq=0 hardirq=0 nmi=0 | in_task=1 in_interrupt=0 serving_softirq=0 bh_disabled_only=0
0x00000001  preempt=1 softirq=0 hardirq=0 nmi=0 | in_task=1 in_interrupt=0 serving_softirq=0 bh_disabled_only=0
0x00000200  preempt=0 softirq=2 hardirq=0 nmi=0 | in_task=1 in_interrupt=1 serving_softirq=0 bh_disabled_only=1
0x00000100  preempt=0 softirq=1 hardirq=0 nmi=0 | in_task=0 in_interrupt=1 serving_softirq=1 bh_disabled_only=0
0x00010000  preempt=0 softirq=0 hardirq=1 nmi=0 | in_task=0 in_interrupt=1 serving_softirq=0 bh_disabled_only=0
0x00010100  preempt=0 softirq=1 hardirq=1 nmi=0 | in_task=0 in_interrupt=1 serving_softirq=1 bh_disabled_only=0
0x00100000  preempt=0 softirq=0 hardirq=0 nmi=1 | in_task=0 in_interrupt=1 serving_softirq=0 bh_disabled_only=0
```

第三行就是陷阱：`0x200`（一次 `local_bh_disable()`）**同時**讓 `in_task()` 和 `in_interrupt()` 為真。舊 code 裡 `if (in_interrupt()) use_GFP_ATOMIC(); else might_sleep_and_use_GFP_KERNEL();` 這種寫法，在 BH 關閉的 process context 裡會選 `GFP_ATOMIC`——不會出錯，但判斷依據其實不對。讀到 `in_interrupt()` 時，先問「作者真正想問的是哪一個」：通常是 `in_task()` 或 `in_hardirq()`。

第二行（`preempt=1`）是另一個常見誤解：`in_task()` 為真**不代表可以睡**。拿著 spinlock 時 preempt count 不為 0，`in_atomic()`（`preempt_count() != 0`，`include/linux/preempt.h:186（v6.18）`）為真，這時睡下去就是 bug。「在 task context」和「可以睡」是兩件事：**可以睡 = in_task() 且 preempt_count 為 0 且中斷沒關**。

## softirq：固定十種，hardirq 結束時順手跑

softirq 是一張固定大小的表，v6.18 只有 10 個 slot（`include/linux/interrupt.h:547-561（v6.18）`）：

```c
enum
{
	HI_SOFTIRQ=0,
	TIMER_SOFTIRQ,
	NET_TX_SOFTIRQ,
	NET_RX_SOFTIRQ,
	BLOCK_SOFTIRQ,
	IRQ_POLL_SOFTIRQ,
	TASKLET_SOFTIRQ,
	SCHED_SOFTIRQ,
	HRTIMER_SOFTIRQ,
	RCU_SOFTIRQ,    /* Preferable RCU should always be the last softirq */

	NR_SOFTIRQS
};
```

註冊點是 `open_softirq()`，它就是一行陣列賦值（`kernel/softirq.c:793-796（v6.18）`）：

```c
void open_softirq(int nr, void (*action)(void))
{
	softirq_vec[nr].action = action;
}
```

> 版本註記：`action` 的型別在 v6.12 之前是 `void (*)(struct softirq_action *)`，v6.12 改成無參數（用 `git show v6.11:include/linux/interrupt.h` 與 v6.12 比對可確認）。讀舊 patch 或舊書時會看到帶參數的版本。

因為只有 10 個，**全 tree 的 softirq handler 可以用一個 `rg` 列完**——這是 softirq 唯一好讀的地方：

```
$ rg -n 'open_softirq\(' -g '*.c' . | grep -v 'void open_softirq'
./block/blk-mq.c:5218:	open_softirq(BLOCK_SOFTIRQ, blk_done_softirq);
./net/core/dev.c:13063:	open_softirq(NET_TX_SOFTIRQ, net_tx_action);
./net/core/dev.c:13064:	open_softirq(NET_RX_SOFTIRQ, net_rx_action);
./kernel/sched/fair.c:13720:	open_softirq(SCHED_SOFTIRQ, sched_balance_softirq);
./kernel/time/timer.c:2578:	open_softirq(TIMER_SOFTIRQ, run_timer_softirq);
./kernel/time/hrtimer.c:2330:	open_softirq(HRTIMER_SOFTIRQ, hrtimer_run_softirq);
./kernel/softirq.c:1046:	open_softirq(TASKLET_SOFTIRQ, tasklet_action);
./kernel/softirq.c:1047:	open_softirq(HI_SOFTIRQ, tasklet_hi_action);
./kernel/rcu/tree.c:4879:		open_softirq(RCU_SOFTIRQ, rcu_core_si);
./kernel/rcu/tiny.c:251:	open_softirq(RCU_SOFTIRQ, rcu_process_callbacks);
./lib/irq_poll.c:214:	open_softirq(IRQ_POLL_SOFTIRQ, irq_poll_softirq);
```

執行點在 hardirq 退出時。`__irq_exit_rcu()` 先把 hardirq 計數減掉，再看有沒有 pending 的 softirq（`kernel/softirq.c:713-723（v6.18）`）：

```c
static inline void __irq_exit_rcu(void)
{
#ifndef __ARCH_IRQ_EXIT_IRQS_DISABLED
	local_irq_disable();
#else
	lockdep_assert_irqs_disabled();
#endif
	account_hardirq_exit(current);
	preempt_count_sub(HARDIRQ_OFFSET);
	if (!in_interrupt() && local_softirq_pending())
		invoke_softirq();
```

`!in_interrupt()` 這個條件保證 softirq 不會巢狀：如果這次中斷打斷的本來就是 softirq（或 BH 關閉的區段），就不在這裡跑，等外層結束再說。真正的迴圈在 `handle_softirqs()`（`kernel/softirq.c:579-652（v6.18）`），核心片段：

```c
restart:
	/* Reset the pending bitmask before enabling irqs */
	set_softirq_pending(0);

	local_irq_enable();

	h = softirq_vec;

	while ((softirq_bit = ffs(pending))) {
		...
		trace_softirq_entry(vec_nr);
		h->action();
		trace_softirq_exit(vec_nr);
		...
	}
	...
	local_irq_disable();

	pending = local_softirq_pending();
	if (pending) {
		if (time_before(jiffies, end) && !need_resched() &&
		    --max_restart)
			goto restart;

		wakeup_softirqd();
	}
```

兩個讀碼重點：

1. **`local_irq_enable()` 在迴圈之前**：softirq handler 執行時中斷是開的，所以 hardirq 可以打斷 softirq——這就是判斷表裡「被 hardirq 搶占」的出處。
2. **上限與 ksoftirqd**：`MAX_SOFTIRQ_TIME` 是 `msecs_to_jiffies(2)`、`MAX_SOFTIRQ_RESTART` 是 10（`kernel/softirq.c:543-544（v6.18）`），註解說這兩個數字是「established via experimentation」。超過就 `wakeup_softirqd()`，剩下的交給 per-CPU 的 `ksoftirqd/N` thread。所以同一個 softirq handler 有時在被打斷的 task 身上跑，有時在 `ksoftirqd` 裡跑，**它的程式碼必須對兩種情況都正確**——兩種都不能睡。

## 實測：在 QEMU 裡看 context 旗標

ftrace 的 `function` tracer 在每一行輸出一組旗標，其中第三個字元就是 context。我們在 guest 裡對幾個代表性函式下 filter：

```sh
#!/bin/sh
T=/sys/kernel/tracing
echo 0 > $T/tracing_on
echo 'vmstat_update run_timer_softirq rcu_core_si process_one_work hrtimer_interrupt' > $T/set_ftrace_filter
echo function > $T/current_tracer
echo > $T/trace
echo 1 > $T/tracing_on
sleep 3
echo 0 > $T/tracing_on
grep -v '^#' $T/trace | grep -E 'process_one_work|vmstat_update' | head -8
grep -v '^#' $T/trace | grep -E 'hrtimer_interrupt' | head -2
grep -v '^#' $T/trace | grep -E 'run_timer_softirq' | head -2
grep '^#' $T/trace | tail -8
```

用 `~/code_reading/qemu/run-script.sh guest2.sh` 跑出來的真實輸出：

```
     kworker/1:1-38      [001] d..1.     4.017312: process_one_work <-worker_thread
     kworker/1:1-38      [001] .....     4.017344: vmstat_update <-process_one_work
     kworker/0:0-9       [000] d..1.     4.513655: process_one_work <-worker_thread
     kworker/0:0-9       [000] d..1.     4.513693: process_one_work <-worker_thread
     kworker/0:0-9       [000] .....     4.513696: vmstat_update <-process_one_work
     kworker/0:0-9       [000] d..1.     5.144912: process_one_work <-worker_thread
     kworker/0:0-9       [000] d..1.     5.145285: process_one_work <-worker_thread
     kworker/0:0-9       [000] d..1.     5.952634: process_one_work <-worker_thread
              sh-70      [001] d.h..     2.938236: hrtimer_interrupt <-__sysvec_apic_timer_interrupt
              sh-70      [001] d.h..     2.939121: hrtimer_interrupt <-__sysvec_apic_timer_interrupt
           sleep-71      [000] ..s..     2.947209: run_timer_softirq <-handle_softirqs
          <idle>-0       [000] ..s1.     2.951216: run_timer_softirq <-handle_softirqs
#                                _-----=> irqs-off/BH-disabled
#                               / _----=> need-resched
#                              | / _---=> hardirq/softirq
#                              || / _--=> preempt-depth
#                              ||| / _-=> migrate-disable
#                              |||| /     delay
#           TASK-PID     CPU#  |||||  TIMESTAMP  FUNCTION
#              | |         |   |||||     |         |
```

逐行解碼：

- `hrtimer_interrupt` 的旗標是 `d.h..`：`d` = 中斷關閉，`h` = hardirq。TASK 欄位是 `sh-70`——**不是 sh 呼叫了 hrtimer_interrupt**，是 timer 中斷剛好打斷了 sh。
- `run_timer_softirq` 是 `..s..`：`s` = softirq，`d` 不見了（`handle_softirqs` 開了中斷）。它借用的是 `sleep-71` 或 `<idle>-0`。
- `vmstat_update` 是 `.....`：五個點，完全乾淨的 process context，執行者是 `kworker/0:0`。呼叫者 `process_one_work` 被呼叫時是 `d..1.`（拿著 pool 的 raw spinlock、中斷關），等它呼叫到 work function 時已經全部放掉——這和下一節讀到的原始碼完全一致。

同一個 guest 裡 `cat /proc/softirqs` 的輸出（開機後約 3 秒）：

```
                    CPU0       CPU1       
          HI:          0          0
       TIMER:         92         62
      NET_TX:          0          0
      NET_RX:          0          0
       BLOCK:          0          8
    IRQ_POLL:          0          0
     TASKLET:          1          0
       SCHED:        155        205
     HRTIMER:          0          0
      RCU:        225        451
```

順序和 enum 一模一樣——`/proc/softirqs` 就是 `softirq_to_name[]` 陣列（`kernel/softirq.c:64（v6.18）`）照順序印出來。

## workqueue：註冊點與執行點分離的標準案例

`INIT_WORK` 展開後只做一件有意義的事：把函式指標存進 `work->func`（`include/linux/workqueue.h:283-290、301-309（v6.18）`，lockdep 版本）：

```c
#define __INIT_WORK_KEY(_work, _func, _onstack, _key)			\
	do {								\
		__init_work((_work), _onstack);				\
		(_work)->data = (atomic_long_t) WORK_DATA_INIT();	\
		lockdep_init_map(&(_work)->lockdep_map, "(work_completion)"#_work, (_key), 0); \
		INIT_LIST_HEAD(&(_work)->entry);			\
		(_work)->func = (_func);				\
	} while (0)
...
#define INIT_WORK(_work, _func)						\
	__INIT_WORK((_work), (_func), 0)
```

`struct work_struct` 本身也很小（`include/linux/workqueue_types.h:13-23（v6.18）`）：

```c
typedef void (*work_func_t)(struct work_struct *work);
...
struct work_struct {
	atomic_long_t data;
	struct list_head entry;
	work_func_t func;
#ifdef CONFIG_LOCKDEP
	struct lockdep_map lockdep_map;
#endif
};
```

`schedule_work()` 把它丟到 `system_percpu_wq`（`include/linux/workqueue.h:728-731（v6.18）`）：

```c
static inline bool schedule_work(struct work_struct *work)
{
	return queue_work(system_percpu_wq, work);
}
```

> 版本註記：`system_percpu_wq` 是 v6.17 才有的名字（`git show v6.16:include/linux/workqueue.h` 裡沒有）；v6.18 的 header 還保留 `system_wq` 並註明「use system_percpu_wq, this will be removed」（`include/linux/workqueue.h:460（v6.18）`）。讀 v6.16 以前的 code 看到 `system_wq`，是同一個東西。

執行點在 `process_one_work()`，`kworker` thread 從 pool 的 worklist 拿出 work，呼叫那個指標（`kernel/workqueue.c:3262-3285（v6.18）`）：

```c
	trace_workqueue_execute_start(work);
	worker->current_func(work);
	/*
	 * While we must be careful to not use "work" after this, the trace
	 * point will only record its address.
	 */
	trace_workqueue_execute_end(work, worker->current_func);

	lock_map_release(&lockdep_map);
	if (!bh_draining)
		lock_map_release(pwq->wq->lockdep_map);

	if (unlikely((worker->task && in_atomic()) ||
		     lockdep_depth(current) != lockdep_start_depth ||
		     rcu_preempt_depth() != rcu_start_depth)) {
		pr_err("BUG: workqueue leaked atomic, lock or RCU: %s[%d]\n"
```

這段 code 給讀者兩個訊號：第一，work function 結束後 kernel 會檢查你有沒有「漏放」lock、preempt 或 RCU，代表 work function 的契約是「進來時乾淨、出去時也要乾淨」；第二，`worker->task &&` 這個前提——BH workqueue 的 worker 沒有 task，在 softirq 執行，那種 work 本來就在 atomic context。

**讀碼技巧**：看到 `INIT_WORK(&x->work, foo_fn)`，在筆記上寫下：

> `foo_fn`：process context，執行者 `kworker`，**可以睡**；觸發點 = 所有 `queue_work(..., &x->work)` / `schedule_work(&x->work)`；取消點 = `cancel_work_sync(&x->work)`。`foo_fn` 內的第一行一定是 `container_of(work, struct X, work)`。

例外：如果那個 work 被 queue 到 `system_bh_wq`、`system_bh_highpri_wq` 或用 `WQ_BH` 建的 workqueue（v6.9 加入），它在 softirq 執行，不能睡。所以完整的推論要看**觸發點用的是哪個 workqueue**，不是只看 `INIT_WORK`。

## 完整案例：一個 driver 同時用了三種非同步機制

`drivers/input/touchscreen/tsc200x-core.c`（597 行）是很好的教材：一個觸控螢幕控制器，用了 threaded irq、timer_list、delayed work 三種非同步機制，加上兩種 lock。先用 rg 把所有註冊點和觸發點撈出來：

```
$ rg -n 'INIT_|timer_setup|request_threaded_irq|schedule_|mod_timer|timer_delete|cancel_|mutex_lock|spin_lock|disable_irq|usleep' drivers/input/touchscreen/tsc200x-core.c
187:		mod_timer(&ts->penup_timer,
221:		usleep_range(100, 500); /* only 10us required */
231:	guard(disable_irq)(&ts->irq);
233:	timer_delete_sync(&ts->penup_timer);
234:	cancel_delayed_work_sync(&ts->esd_work);
244:		schedule_delayed_work(&ts->esd_work,
391:		scoped_guard(disable_irq, &ts->irq) {
392:			timer_delete_sync(&ts->penup_timer);
401:	schedule_delayed_work(&ts->esd_work,
478:	spin_lock_init(&ts->lock);
479:	timer_setup(&ts->penup_timer, tsc200x_penup_timer, 0);
481:	INIT_DELAYED_WORK(&ts->esd_work, tsc200x_esd_work);
534:	error = devm_request_threaded_irq(dev, irq, NULL, tsc200x_irq_thread,
```

（輸出經過刪減，只留和本章有關的行。）三個註冊點（第 479、481、534 行）告訴我們三個 callback 的 context：

| callback | 註冊點 | context | 能睡？ |
|---|---|---|---|
| `tsc200x_irq_thread` | `devm_request_threaded_irq(..., NULL, tsc200x_irq_thread, IRQF_ONESHOT, ...)`（534） | `irq/NN-tsc200x` thread | 可以 |
| `tsc200x_penup_timer` | `timer_setup(&ts->penup_timer, ...)`（479） | `TIMER_SOFTIRQ` | 不能 |
| `tsc200x_esd_work` | `INIT_DELAYED_WORK(&ts->esd_work, ...)`（481） | `kworker` | 可以 |

有了這張表，再讀 callback 本體就每一行都說得通。irq thread 的尾巴（`drivers/input/touchscreen/tsc200x-core.c:185-193（v6.18）`）：

```c
	scoped_guard(spinlock_irqsave, &ts->lock) {
		tsc200x_update_pen_state(ts, tsdata.x, tsdata.y, pressure);
		mod_timer(&ts->penup_timer,
			  jiffies + msecs_to_jiffies(TSC200X_PENUP_TIME_MS));
	}
```

timer callback（`drivers/input/touchscreen/tsc200x-core.c:196-202（v6.18）`）：

```c
static void tsc200x_penup_timer(struct timer_list *t)
{
	struct tsc200x *ts = timer_container_of(ts, t, penup_timer);

	guard(spinlock_irqsave)(&ts->lock);
	tsc200x_update_pen_state(ts, 0, 0, 0);
}
```

- `ts->lock` 是 spinlock 而不是 mutex：因為 timer callback 在 softirq，**不能睡**，而它和 irq thread 共享 pen state。共享資料的兩端只要有一端不能睡，就只能用 spinlock。
- irq thread 本身可以睡（它前面呼叫的 `regmap_bulk_read` 走 I2C 或 SPI——這個 core 同時給 `tsc2004.c`（I2C）和 `tsc2005.c`（SPI）用，兩種 bus 傳輸都會睡），但進 `ts->lock` 時不能用單純的 `spin_lock`：timer softirq 可能在同一顆 CPU 上打斷持鎖中的 thread，再去搶同一把鎖，造成自己等自己的死鎖。就本檔所見，會碰 `ts->lock` 的最「硬」的 context 是 softirq，理論上 `spin_lock_bh` 已經夠；作者選了 `irqsave`，代價是多關一小段中斷，好處是不必去證明沒有 hardirq 會碰它。
- 「抬筆」這件事沒有硬體中斷可以通知，所以每收到一筆座標就把 timer 往後推（`mod_timer`），超過 `TSC200X_PENUP_TIME_MS` 沒有新中斷，timer 觸發，判定抬筆。這是 timer 在 driver 裡最常見的用法：**用「沒事發生」當事件**。

delayed work（`drivers/input/touchscreen/tsc200x-core.c:358-404（v6.18）`，節錄）是一個看門狗：

```c
static void tsc200x_esd_work(struct work_struct *work)
{
	struct tsc200x *ts = container_of(work, struct tsc200x, esd_work.work);
	...
	scoped_guard(mutex_try, &ts->mutex) {
		...
		error = regmap_read(ts->regmap, TSC200X_REG_CFR0, &r);
		...
		scoped_guard(disable_irq, &ts->irq) {
			timer_delete_sync(&ts->penup_timer);
			tsc200x_update_pen_state(ts, 0, 0, 0);
			tsc200x_reset(ts);
		}

		tsc200x_start_scan(ts);
	}

	/* re-arm the watchdog */
	schedule_delayed_work(&ts->esd_work,
			      round_jiffies_relative(
					msecs_to_jiffies(ts->esd_timeout)));
}
```

`mutex`、`regmap_read`（SPI）、`timer_delete_sync`（會等 timer callback 跑完）、`usleep_range`（在 `tsc200x_reset` 裡）——全是會睡的操作，**只有在 process context 才合法**。`container_of(work, struct tsc200x, esd_work.work)` 中的 `.work` 是因為 `struct delayed_work` 把 `work_struct` 包在裡面（`include/linux/workqueue.h:114（v6.18）`）；delayed work 的計時是靠一個內嵌的 `timer_list`，時間到了 timer callback（softirq）再把 work 排進 workqueue。一個 `schedule_delayed_work` 背後其實走過 softirq 和 kworker 兩個 context。

## threaded irq：把 hardirq 縮到一行

`devm_request_threaded_irq(dev, irq, NULL, tsc200x_irq_thread, IRQF_ONESHOT, ...)` 的第三個參數（hardirq handler）是 `NULL`。kernel 會幫你填一個預設的 primary handler（`kernel/irq/manage.c:976-979（v6.18）`）：

```c
static irqreturn_t irq_default_primary_handler(int irq, void *dev_id)
{
	return IRQ_WAKE_THREAD;
}
```

hardirq 層看到 `IRQ_WAKE_THREAD` 就去喚醒 thread（`kernel/irq/handle.c:212-224（v6.18）`，在 `__handle_irq_event_percpu` 裡）：

```c
		switch (res) {
		case IRQ_WAKE_THREAD:
			/*
			 * Catch drivers which return WAKE_THREAD but
			 * did not set up a thread function
			 */
			if (unlikely(!action->thread_fn)) {
				warn_no_thread(irq, action);
				break;
			}

			__irq_wake_thread(desc, action);
			break;
```

thread 本身是用 `kthread_create(irq_thread, new, "irq/%d-%s", irq, ...)` 建的（`kernel/irq/manage.c:1392（v6.18）`），所以 `ps` 裡會看到 `irq/34-tsc200x` 這種名字，主迴圈在 `irq_thread()`（`kernel/irq/manage.c:1232-1263（v6.18）`）：

```c
	sched_set_fifo(current);
	...
	while (!irq_wait_for_interrupt(desc, action)) {
		irqreturn_t action_ret;

		action_ret = handler_fn(desc, action);
		if (action_ret == IRQ_WAKE_THREAD)
			irq_wake_secondary(desc, action);

		wake_threads_waitq(desc);
	}
```

`sched_set_fifo()` 說明 irq thread 是即時優先權（SCHED_FIFO），比一般 task 優先。`IRQF_ONESHOT` 則是讓中斷線在 thread 跑完前保持 mask——level-triggered 的中斷如果 hardirq 一回來就重新開啟，會立刻再進來，形成 interrupt storm。

threaded irq 是 v2.6.30 加入的（`git show v2.6.29:kernel/irq/manage.c` 裡沒有 `request_threaded_irq`）。它也是 tasklet 被淘汰的主因之一。

## tasklet 的現況與 BH workqueue

`include/linux/interrupt.h:665-685（v6.18）` 開頭就寫：

```c
/* Tasklets --- multithreaded analogue of BHs.

   This API is deprecated. Please consider using threaded IRQs instead:
   https://lore.kernel.org/lkml/20200716081538.2sivhkj4hcyrusem@linutronix.de
```

但 deprecated 不等於消失：v6.18 tree 裡還有 198 個 `.c` 檔用到 `tasklet_init`、`tasklet_setup` 或 `DECLARE_TASKLET`（`rg -l 'tasklet_init|tasklet_setup|DECLARE_TASKLET' -g '*.c' . | wc -l`）。v6.9 加入的 BH workqueue 是官方指定的遷移目標，`kernel/workqueue.c:3619-3628（v6.18）` 的註解寫得很直白：

```c
/*
 * TODO: Convert all tasklet users to workqueue and use softirq directly.
 *
 * This is currently called from tasklet[_hi]action() and thus is also called
 * whenever there are tasklets to run. Let's do an early exit if there's nothing
 * queued. Once conversion from tasklet is complete, the need_more_worker() test
 * can be dropped.
```

也就是說，**v6.18 的 BH workqueue 還是寄生在 `TASKLET_SOFTIRQ` 上執行的**。讀碼時看到 `tasklet_schedule()`，context 判斷和 softirq 一樣：不能睡、同一個 tasklet 不會在兩顆 CPU 上同時跑（這是它和裸 softirq 唯一的差別）。

## timer：timer_list 與 hrtimer

`struct timer_list` 的 callback 欄位就叫 `function`（`include/linux/timer_types.h:8-21（v6.18）`）：

```c
struct timer_list {
	/*
	 * All fields that change during normal runtime grouped to the
	 * same cacheline
	 */
	struct hlist_node	entry;
	unsigned long		expires;
	void			(*function)(struct timer_list *);
	u32			flags;
	...
};
```

執行點在 `TIMER_SOFTIRQ` 的 handler 裡，最終是 `call_timer_fn()` 中的一行 `fn(timer);`（`kernel/time/timer.c:1748（v6.18）`）。

API 演進是讀舊 code 的雷區，用 `git show vX:include/linux/timer.h` 查過的時間點：

| 寫法 | 狀態 |
|---|---|
| `init_timer()` + `timer.data = (unsigned long)ptr` | v4.14 前的舊 API，callback 參數是 `unsigned long` |
| `timer_setup(t, fn, flags)` + `from_timer()` | v4.14 加入 `timer_setup` |
| `timer_container_of()` | v6.16 加入，v6.18 有 679 個檔案使用，`from_timer` 只剩 4 個 |
| `del_timer_sync()` → `timer_delete_sync()` | 新名字，tsc200x 已經用新名 |

`hrtimer` 的 callback 回傳 `enum hrtimer_restart`（`HRTIMER_NORESTART` 或 `HRTIMER_RESTART`），可以自我重新排程。初始化用 `hrtimer_setup()`（v6.13 加入，v6.18 tree 裡已經沒有 `hrtimer_init()` 的呼叫者）。它的 context 由 mode 決定（`include/linux/hrtimer.h:30-33（v6.18）`）：

```c
 * HRTIMER_MODE_SOFT		- Timer callback function will be executed in
 *				  soft irq context
 * HRTIMER_MODE_HARD		- Timer callback function will be executed in
 *				  hard irq context even on PREEMPT_RT.
```

沒帶 `_SOFT` 的 hrtimer 在非 RT kernel 上於 hardirq 執行。所以讀 hrtimer callback 時要回頭看 `hrtimer_setup` / `hrtimer_start` 的 mode 參數，這是同一種 timer 卻有兩種 context 的唯一例子。

## 動態實證：中斷「借」走了 read(2) 的時間

練習 E 會用 function_graph 追 `read(2)`。在那次實驗裡，我們把 graph 根設在 `ksys_read`，結果第一行就不是 VFS：

```
 1)               |  ksys_read() {
 1) ! 271.747 us  |    irq_enter_rcu();
 1)               |    __sysvec_apic_timer_interrupt() {
 1)               |      hrtimer_interrupt() {
 ...
 1)               |          tick_nohz_handler() {
 ...
 1) ! 786.719 us  |      } /* hrtimer_interrupt */
 1) ! 793.532 us  |    } /* __sysvec_apic_timer_interrupt */
 1)               |    irq_exit_rcu() {
 1) + 44.493 us   |      idle_cpu();
 1) + 49.823 us   |    } /* irq_exit_rcu */
 1)   5.140 us    |    fdget_pos();
 1)               |    vfs_read() {
```

`hrtimer_interrupt` 在縮排上看起來是 `ksys_read` 的「子函式」，但 `ksys_read` 原始碼裡根本沒有呼叫它。真相是：timer 中斷剛好在 `ksys_read` 進入後打斷了這個 task，function_graph 照著 CPU 上實際發生的順序記錄，於是中斷被畫在 read 底下。另一次實驗（練習 E 的冷 cache ext4 讀取）裡，`irq_exit_rcu` 底下還出現了 `handle_softirqs() → run_timer_softirq()` 和 `rcu_core_si()`——softirq 在 hardirq 退出時執行，同樣借用 read 的 stack。

**讀 trace 的規則**：function_graph 裡看到 `irq_enter_rcu` / `__sysvec_*` / `handle_softirqs`，它和外層函式之間**沒有呼叫關係**，只是時間上的巢狀。要隱藏它們，打開 `options/funcgraph-irqs` 設為 0（但 softirq 仍可能出現，見練習 E）。

## 對比與取捨：該把工作丟到哪裡？

| 需求 | 選擇 | 不選其他的理由 |
|---|---|---|
| 硬體要求立刻 ack | hardirq handler | 唯一能做到微秒內回應的地方 |
| 每秒百萬次、per-CPU、不能睡（網路收包） | softirq | workqueue 的 context switch 成本太高；但 softirq 只有 10 個 slot，新增要過 maintainer 那一關 |
| driver 中斷後要讀 I2C/SPI | threaded irq | I2C/SPI 傳輸會睡，hardirq/softirq 都不行 |
| 隨時想到就丟、要睡、不急 | workqueue | 不用自己管 thread 生命週期 |
| 需要長時間迴圈、自己的節奏 | kthread | workqueue 的 worker 是共用的，長期佔用會餓死別的 work（可以用 `system_long_wq` 緩解） |
| 「X 毫秒後如果沒事就...」 | timer_list | jiffies 精度（HZ=1000 時 1ms）足夠，成本低 |
| 微秒等級精度 | hrtimer | timer_list 精度不夠 |
| 原本用 tasklet 的新 code | BH workqueue 或 threaded irq | tasklet 已 deprecated |

## 踩雷集錦

1. **「TASK 欄位顯示 `sh`，所以這是 sh 呼叫的」**：錯。hardirq 和 softirq 借用被打斷的 task，ftrace 的 TASK 欄位只代表「當時 CPU 上的 current」。看旗標欄的 `h` / `s` 才能判斷 context。
2. **「`in_task()` 為真就可以睡」**：錯。拿著 spinlock、`preempt_disable()`、`rcu_read_lock()`（非 preemptible RCU）時都不能睡。可以睡的條件是 `preemptible()`（`preempt_count() == 0 && !irqs_disabled()`，`include/linux/preempt.h:227（v6.18）`）加上沒有拿 RCU read lock。
3. **「`in_interrupt()` 可以拿來判斷要不要用 `GFP_ATOMIC`」**：它在 BH 關閉的 process context 也回傳真（本章 pcdecode 的 `0x200`）。kernel 自己標它為 deprecated。新 code 應該由呼叫者用參數告訴你 context，而不是在函式裡猜。
4. **「`might_sleep()` 會幫我抓 bug」**：只有開了 `CONFIG_DEBUG_ATOMIC_SLEEP` 才會印 `BUG: sleeping function called from invalid context`（字串在 `kernel/sched/core.c:8904（v6.18）`）。本課的 build 沒開（`.config` 裡是 `# CONFIG_DEBUG_ATOMIC_SLEEP is not set`），我們在 `fs/pipe.i` 裡看到 `might_sleep()` 展開成 `might_resched()`——只是一個讓出 CPU 的點，不檢查任何東西。要抓 atomic sleep bug，自己編一個開了這個選項的 kernel。
5. **「work function 跑完，`struct` 就可以 free 了」**：work 可能已經被 queue 但還沒跑，或正在另一顆 CPU 上跑。釋放前要 `cancel_work_sync()` / `cancel_delayed_work_sync()`（tsc200x 的 `__tsc200x_disable` 第 233–234 行就是在做這件事，而且順序是先停 timer 再停 work）。少了這一步是 use-after-free 的經典來源，[kernel_pwn](../../security/kernel_pwn/README.md) 課裡有不少案例。

## 進階：再往深一層

- **PREEMPT_RT 改變了一切**：開了 `CONFIG_PREEMPT_RT` 之後，大部分 hardirq handler 被強制 thread 化（`force_irqthreads()`），softirq 在 task context 執行、可被搶占，`spinlock_t` 變成可睡的 rt_mutex。本章所有「不能睡」的規則在 RT 上要重新檢視。`preempt.h` 裡每個 `#ifdef CONFIG_PREEMPT_RT` 分支都是線索：例如 RT 的 `softirq_count()` 讀的是 `current->softirq_disable_cnt`，不是 `preempt_count`。
- **cmwq 的 concurrency management**：同一個 per-CPU pool 的 kworker，在一個 work 睡著時 kernel 會喚醒另一個 worker 接手（`kick_pool()`），這就是「concurrency-managed」的意思。讀 `kernel/workqueue.c` 的 `worker_thread()` 與 `wq_worker_sleeping()` 可以看到它怎麼和 scheduler 掛勾。
- **NAPI**：網路 driver 的 hardirq 只做 `napi_schedule()`，真正收包在 `NET_RX_SOFTIRQ` 的 `net_rx_action()` 裡輪詢。這是 softirq 最重要的使用者，Ch 56 冷讀網路收包路徑會走一次。
- **面試題**：「為什麼 softirq handler 可以在多顆 CPU 上同時執行，而 tasklet 不行？這對 handler 的 locking 有什麼影響？」答案在本章 tasklet 一節與 `tasklet_action_common()`（`kernel/softirq.c:903（v6.18）`）的 `TASKLET_STATE_RUN` 檢查。

## 動手練習

1. 在 `~/code_reading/linux` 用 `rg -n 'INIT_WORK\(' fs/ext4/` 找出 ext4 的所有 work，選一個，寫出它的觸發點與能否睡眠。提示：`fs/ext4/readpage.c` 的 `bio_post_read_processing()` 會在 bio 完成時（通常在中斷或 softirq 的 completion 路徑）把解密工作丟給 workqueue，因為解密不能在那個 context 做。
2. 在 QEMU guest 裡把 `set_ftrace_filter` 換成 `blk_mq_complete_request`（或你在練習 E 的 trace 裡看到的 block 完成函式），讀一個檔案，觀察它的旗標是 `h` 還是 `s`。
3. 修改 pcdecode，加入 `preemptible()` 的判斷（需要另一個參數表示中斷是否關閉），餵 `0x0` + 中斷關閉，確認輸出為「不能睡」。

## 本章重點整理

- kernel 用 `preempt_count` 的位元段記錄 context；`in_task()`、`in_hardirq()`、`in_serving_softirq()` 是現在推薦的判斷，`in_interrupt()` 已 deprecated。
- 非同步 callback 的註冊點（`INIT_WORK`、`timer_setup`、`request_threaded_irq`、`open_softirq`）決定了它的執行環境；看到註冊點就把 context 寫進筆記。
- hardirq / softirq / timer_list 不能睡；workqueue / threaded irq / kthread 可以；hrtimer 和 BH workqueue 依參數而定。
- lock 的變體（`_irqsave`、`_bh`、mutex）反映了共享資料被哪些 context 碰到。
- function_graph 裡的中斷是時間上的巢狀，不是呼叫關係。

## 自我檢核

- [ ] 不看筆記，畫出一次硬體中斷從進入到 kworker 執行 work function 的時間軸，標出每一段能不能睡。
- [ ] 有人在 code review 寫「這裡用 `in_interrupt()` 判斷就好」，你能用 preempt_count 的位元配置說明它錯在哪嗎？
- [ ] 給你一個陌生 driver 的 callback 函式名稱，你會用哪三個 `rg` 指令找出它的 context？
- [ ] 解釋 tsc200x 為什麼 timer 和 irq thread 之間用 spinlock，而 esd work 裡可以用 mutex。
- [ ] 說出 `hrtimer` 和 `timer_list` 在 context 上的差別，以及你要看哪個參數才能確定。
- [ ] 為什麼 deprecated 的 tasklet 在 v6.18 還有近兩百個檔案在用？這對你讀 driver 有什麼影響？

## 延伸閱讀

### 官方文件

- **[Concurrency Managed Workqueue (cmwq)](https://docs.kernel.org/core-api/workqueue.html)**（v6.18 tree：`Documentation/core-api/workqueue.rst`）
  - **讀哪裡**：「Why Concurrency Managed Workqueue?」與「Workqueue Attributes」兩節；`WQ_BH`、`WQ_UNBOUND`、`max_active` 的語意都在這裡。
  - **和本章的關聯**：本章只講 `INIT_WORK` 到 `process_one_work` 的路徑，這份文件解釋 pool、worker 數量怎麼決定。
  - **前提**：讀完本章 workqueue 一節即可。
- **[Unreliable Guide To Hacking The Linux Kernel](https://docs.kernel.org/kernel-hacking/hacking.html)**（`Documentation/kernel-hacking/hacking.rst`）— Rusty Russell
  - **讀哪裡**：「The Players」整節（User Context、Hardware Interrupts、Software Interrupt Context）。這是本章 context 判斷表的原始版本，短而精準。
  - **注意**：寫作年代早，tasklet 被寫成推薦做法；配合本章的現況說明讀。
- **[Unreliable Guide To Locking](https://docs.kernel.org/kernel-hacking/locking.html)**（`Documentation/kernel-hacking/locking.rst`）
  - **讀哪裡**：「Locking Between User Context and Softirqs」到「Hard IRQ Context」幾節，以及最後的「Table of Minimum Requirements」——那張表就是本章最右欄的完整版。
- **[Linux generic IRQ handling](https://docs.kernel.org/core-api/genericirq.html)**（`Documentation/core-api/genericirq.rst`）
  - **讀哪裡**：「Abstraction layers」與 flow handler 各節；想懂 `__handle_irq_event_percpu` 之上那層 `handle_level_irq` / `handle_edge_irq` 時讀。
- **[hrtimers - subsystem for high-resolution kernel timers](https://docs.kernel.org/timers/hrtimers.html)**（`Documentation/timers/hrtimers.rst`）
  - **讀哪裡**：開頭的設計動機——為什麼 timer wheel 不適合高精度計時。

### 書籍

- **《Linux Device Drivers》3rd ed.** — Corbet, Rubini, Kroah-Hartman（O'Reilly, 2005；[LWN 上有免費全文](https://lwn.net/Kernel/LDD3/)）
  - **讀哪幾章**：Ch 7「Time, Delays, and Deferred Work」與 Ch 10「Interrupt Handling」。
  - **注意**：2.6.10 時代，timer API（`init_timer`）、workqueue API 都已改變，threaded irq 還不存在；讀它的「為什麼」，API 以本章為準。
- **《Linux Kernel Development》3rd ed.** — Robert Love（Addison-Wesley, 2010）
  - **讀哪幾章**：Ch 7「Interrupts and Interrupt Handlers」、Ch 8「Bottom Halves and Deferring Work」。Ch 8 的 softirq / tasklet / workqueue 比較表是經典，版本是 2.6.34（cmwq 剛進來、tasklet 還沒 deprecated）。
- **《Code Reading》** — Diomidis Spinellis（2003）
  - **讀哪幾章**：第 5 章「Advanced Control Flow」的 signal 與 thread 兩節。對照本章，你會看到 user-space 的 signal handler 和 kernel 的 hardirq 有一樣的「不能做什麼」清單問題。

### 技術文章

- **LWN 的 workqueue 與 tasklet 系列**（Jonathan Corbet 等）
  - **怎麼找**：在 [LWN Kernel Index](https://lwn.net/Kernel/Index/) 的「Workqueues」「Tasklets」「Interrupts/Threaded」條目下，按年份可以看到 2010 年 cmwq 合併、2024 年 BH workqueue 取代 tasklet 的討論。
  - **為什麼值得讀**：本章「為什麼有四五種延後執行機制」的歷史，LWN 有當時的第一手報導；讀 BH workqueue 那篇能理解為什麼 tasklet 的語意（同一 tasklet 不並行）讓轉換變困難。

這一章的 callback 都在等「時間到」或「中斷來」。下一章處理另一種等待：一個 task 主動睡下去，等另一個 context 把它叫醒。

→ [Ch 33 等待與喚醒：wait queue 與 completion](./33-wait-queues-completion.md)
