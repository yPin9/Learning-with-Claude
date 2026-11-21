# Ch 33 — 等待與喚醒：wait queue 與 completion

> **目標**：看到 `wait_event_interruptible(q, cond)`，能在腦中把它展開成「條件檢查 → `prepare_to_wait_event` → `schedule()` → 再檢查」的迴圈，說出每一步為什麼要在那個位置；看到一個 sleeper，能用 `rg` 找出所有可能叫醒它的 waker，反之亦然；分得清 wait queue、completion、folio 位元等待這三種常見形態。

> **鴻溝**：本章主要練**慣用法鴻溝**（`wait_event_*` 是一層層巨集，不展開看不懂它怎麼睡）與**間接鴻溝**（sleeper 和 waker 在不同函式、甚至不同 context，中間只靠一個 `wait_queue_head` 連起來）。

> **環境**：Linux kernel v6.18 原始碼（`~/code_reading/linux`）；巨集展開用 `make O=../build fs/pipe.i`；動態驗證用同版本 x86_64 build 在 QEMU（TCG）裡跑 ftrace。user-space 範例用 gcc 11.4 在 WSL Ubuntu 22.04 上跑。

## 為什麼需要這個？

[Ch 32](./32-async-irq-softirq-workqueue.md) 處理的是「事件來了，誰去執行 callback」。這一章處理反方向：**一個 task 主動停下來，等某件事發生**。`read(2)` 一個空的 pipe、等磁碟 I/O 完成、等另一個 kernel thread 初始化完畢——全都是「睡下去，等別人叫醒」。

讀這類 code 的困難在於，**睡和醒是兩段分開的程式碼**。`fs/pipe.c` 的 reader 睡在 `pipe->rd_wait` 上，叫醒它的是 writer 在 `anon_pipe_write()` 最後的一行 `wake_up_interruptible_sync_poll(&pipe->rd_wait, ...)`；讀 reader 的時候你看不到 writer，反之亦然。如果只讀其中一半，你會不知道 reader 什麼時候會醒、醒來時資料一定在不在。

kernel 早期的做法是 `sleep_on()` / `interruptible_sleep_on()`：呼叫它就睡，`wake_up()` 叫醒。問題是它**先檢查條件、再上 wait queue**，中間的空隙會漏掉 wakeup（本章「lost wakeup」一節會畫出來）。這組 API 在 v3.15 被整個移除（`git show v3.14:include/linux/wait.h` 還有 `interruptible_sleep_on`，v3.15 沒了）。今天的 `wait_event_*` 家族就是為了把「正確的等待順序」包成一個不會寫錯的巨集。

Spinellis 原書第 5 章談平行執行時，提醒讀者注意「誰在等誰」的同步點；user-space 對應的是 `pthread_cond_wait`。kernel 的 wait queue 就是 condition variable 的 kernel 版，連「醒來後要用 while 重檢條件」這條規則都一樣。

## 先建立直覺

想像一間診所的候診室：

- **候診室**（`wait_queue_head`）：一張名單加一把鎖。
- **病人坐下前**（`prepare_to_wait_event`）：先把名字寫上名單，並把自己的狀態改成「等待中」（`TASK_INTERRUPTIBLE`）。
- **寫完名字之後再看一次叫號板**（`if (condition) break`）：如果剛好叫到你，不用坐了。
- **坐下打瞌睡**（`schedule()`）：CPU 讓給別人。
- **護士叫號**（`wake_up`）：照名單一個個拍肩膀；病人醒來後**再看一次叫號板**，因為護士可能一次叫醒所有人（`wake_up_all`），但診間只有一間。

```
 sleeper (task A)                                waker (task B / irq / softirq)
 ───────────────                                 ─────────────────────────────
 if (cond) return;            快速路徑
 loop:
   prepare_to_wait_event(q)   ① 上名單 + state=INTERRUPTIBLE
   if (cond) break;           ② 寫完名字再檢查         cond = true;          ③ 先改條件
   schedule();                ④ 真的睡                 wake_up(q);           ⑤ 再叫醒
   if (cond) break;           ⑥ 醒來重檢                 └─ 對名單上每個人 try_to_wake_up
 finish_wait(q)               ⑦ 下名單 + state=RUNNING
```

**順序是整個機制的核心**：sleeper 是「先上名單、再檢查條件」，waker 是「先改條件、再叫醒」。只要雙方都照這個順序，不管兩邊怎麼交錯，sleeper 要嘛在 ② 看到條件為真，要嘛已經在名單上、會被 ⑤ 叫醒。

> 這個比喻的限制：真的候診室裡病人睡著才會被叫醒；kernel 裡 `wake_up` 可以發生在 ② 和 ④ 之間，那時 task 還沒睡，`try_to_wake_up` 會把它的 state 改回 `TASK_RUNNING`，於是 ④ 的 `schedule()` 不會真的讓它離開 runqueue。這是「不會漏」的真正理由，比喻表達不出來。

## 資料結構：一把鎖、一條 list

v6.18 的 wait queue 只有兩個結構（`include/linux/wait.h:14-38（v6.18）`）：

```c
typedef int (*wait_queue_func_t)(struct wait_queue_entry *wq_entry, unsigned mode, int flags, void *key);
int default_wake_function(struct wait_queue_entry *wq_entry, unsigned mode, int flags, void *key);

/* wait_queue_entry::flags */
#define WQ_FLAG_EXCLUSIVE	0x01
#define WQ_FLAG_WOKEN		0x02
#define WQ_FLAG_CUSTOM		0x04
#define WQ_FLAG_DONE		0x08
#define WQ_FLAG_PRIORITY	0x10

/*
 * A single wait-queue entry structure:
 */
struct wait_queue_entry {
	unsigned int		flags;
	void			*private;
	wait_queue_func_t	func;
	struct list_head	entry;
};

struct wait_queue_head {
	spinlock_t		lock;
	struct list_head	head;
};
typedef struct wait_queue_head wait_queue_head_t;
```

用 [Ch 23](./23-list-head-hlist.md) 的眼光看：`wait_queue_head.head` 是 list 頭，串的是 `struct wait_queue_entry`，經由 `entry` 成員串起來。每個 entry 帶兩個關鍵欄位：

- `private`：通常是要叫醒的 `task_struct *`。
- `func`：**wakeup 時要呼叫的函式**。這是一個 callback——wait queue 本身也是 [Ch 34](./34-notifiers-and-callbacks.md) 那種「註冊 callback、事件發生時逐一呼叫」的結構，只是預設的 callback 是「叫醒這個 task」。後面 folio 等待會看到自訂的 `func`。

entry 通常活在 sleeper 的 **kernel stack** 上（`___wait_event` 裡的 `struct wait_queue_entry __wq_entry;`），所以睡完一定要 `finish_wait()` 把它從 list 拿掉——否則函式返回、stack frame 被覆寫，list 上就留下一個懸空指標。

## 解碼 `wait_event_interruptible`

外層巨集（`include/linux/wait.h:482-508（v6.18）`）：

```c
#define __wait_event_interruptible(wq_head, condition)				\
	___wait_event(wq_head, condition, TASK_INTERRUPTIBLE, 0, 0,		\
		      schedule())

...
#define wait_event_interruptible(wq_head, condition)				\
({										\
	int __ret = 0;								\
	might_sleep();								\
	if (!(condition))							\
		__ret = __wait_event_interruptible(wq_head, condition);		\
	__ret;									\
})
```

`({ ... })` 是 [Ch 15](./15-kernel-c-gnu-extensions.md) 講的 statement expression：整塊的值是最後一個運算式 `__ret`，所以 `wait_event_interruptible` 可以當函式用、回傳 0 或 `-ERESTARTSYS`。先檢查一次 `condition` 是快速路徑：條件已經成立就完全不碰 wait queue。

真正的迴圈在 `___wait_event`（`include/linux/wait.h:302-327（v6.18）`）：

```c
#define ___wait_event(wq_head, condition, state, exclusive, ret, cmd)		\
({										\
	__label__ __out;							\
	struct wait_queue_entry __wq_entry;					\
	long __ret = ret;	/* explicit shadow */				\
										\
	init_wait_entry(&__wq_entry, exclusive ? WQ_FLAG_EXCLUSIVE : 0);	\
	for (;;) {								\
		long __int = prepare_to_wait_event(&wq_head, &__wq_entry, state);\
										\
		if (condition)							\
			break;							\
										\
		if (___wait_is_interruptible(state) && __int) {			\
			__ret = __int;						\
			goto __out;						\
		}								\
										\
		cmd;								\
										\
		if (condition)							\
			break;							\
	}									\
	finish_wait(&wq_head, &__wq_entry);					\
__out:	__ret;									\
})
```

### 真的展開一次

與其在腦中展開，不如讓編譯器做。`fs/pipe.c` 的 `pipe_wait_readable()`（`fs/pipe.c:1073-1078（v6.18）`）：

```c
void pipe_wait_readable(struct pipe_inode_info *pipe)
{
	pipe_unlock(pipe);
	wait_event_interruptible(pipe->rd_wait, pipe_readable(pipe));
	pipe_lock(pipe);
}
```

在 `~/code_reading/linux` 下跑 `make O=../build fs/pipe.i`，從產出的 `../build/fs/pipe.i` 撈出這個函式（原本是一行，這裡手動換行縮排，內容一字未改）：

```c
void pipe_wait_readable(struct pipe_inode_info *pipe)
{
 pipe_unlock(pipe);
 ({ int __ret = 0;
    do { might_resched(); } while (0);
    if (!(pipe_readable(pipe)))
      __ret = ({ __label__ __out;
                 struct wait_queue_entry __wq_entry;
                 long __ret = 0;
                 init_wait_entry(&__wq_entry, 0 ? 0x01 : 0);
                 for (;;) {
                   long __int = prepare_to_wait_event(&pipe->rd_wait, &__wq_entry, 0x00000001);
                   if (pipe_readable(pipe)) break;
                   if ((!__builtin_constant_p(0x00000001) ||
                        (0x00000001 & (0x00000001 | 0x00000100))) && __int) {
                     __ret = __int; goto __out;
                   }
                   schedule();
                   if (pipe_readable(pipe)) break;
                 }
                 finish_wait(&pipe->rd_wait, &__wq_entry);
                 __out: __ret; });
    __ret; });
 pipe_lock(pipe);
}
```

逐項解碼：

| 展開後 | 來源 | 意義 |
|---|---|---|
| `might_resched()` | `might_sleep()` | 本 build 沒開 `CONFIG_DEBUG_ATOMIC_SLEEP`，所以只剩一個讓出點，不做 atomic 檢查 |
| `0 ? 0x01 : 0` | `exclusive ? WQ_FLAG_EXCLUSIVE : 0` | 非 exclusive 等待；`_exclusive` 版本這裡是 1 |
| `0x00000001` | `TASK_INTERRUPTIBLE` | `include/linux/sched.h:106（v6.18）` |
| `0x00000100` | `TASK_WAKEKILL` | `include/linux/sched.h:117（v6.18）` |
| `(!__builtin_constant_p(...) \|\| ...)` | `___wait_is_interruptible(state)` | state 是常數時編譯期就能算出真假；`TASK_UNINTERRUPTIBLE` 的版本這整個 `if` 會被編譯器刪掉 |
| `schedule()` | `cmd` 參數 | `_timeout` 版本這裡換成 `schedule_timeout(__ret)` |

`__ret` 出現兩次是故意的：外層 `int __ret` 是 `wait_event_interruptible` 的回傳值，內層 `long __ret` 是 `___wait_event` 自己的，註解寫的「explicit shadow」就是在說這個。讀巨集碰到同名變數時，先確定你看的是哪一層。

### 迴圈裡每一步的理由

`prepare_to_wait_event()`（`kernel/sched/wait.c:289-322（v6.18）`）：

```c
long prepare_to_wait_event(struct wait_queue_head *wq_head, struct wait_queue_entry *wq_entry, int state)
{
	unsigned long flags;
	long ret = 0;

	spin_lock_irqsave(&wq_head->lock, flags);
	if (signal_pending_state(state, current)) {
		...
		list_del_init(&wq_entry->entry);
		ret = -ERESTARTSYS;
	} else {
		if (list_empty(&wq_entry->entry)) {
			if (wq_entry->flags & WQ_FLAG_EXCLUSIVE)
				__add_wait_queue_entry_tail(wq_head, wq_entry);
			else
				__add_wait_queue(wq_head, wq_entry);
		}
		set_current_state(state);
	}
	spin_unlock_irqrestore(&wq_head->lock, flags);

	return ret;
}
```

1. **在 wait queue 的 lock 下上名單並設 state**。`set_current_state()` 內含 memory barrier（[Ch 21](./21-read-once-and-barriers.md)），保證「我已經是等待狀態」這個寫入，在之後讀 `condition` 之前對其他 CPU 可見。
2. **`list_empty(&wq_entry->entry)` 才加入**：迴圈第二圈以後 entry 可能已經被 `autoremove_wake_function` 拿掉（被叫醒了），也可能還在（spurious wakeup），這個檢查讓同一個函式在兩種情況都正確。
3. **exclusive 加在尾巴、非 exclusive 加在頭**：wakeup 從頭開始走，所以非 exclusive 的全部會被叫醒，exclusive 的只叫醒指定數量（見下一節）。
4. **有 signal 就回傳 `-ERESTARTSYS`**：`wait_event_interruptible` 的呼叫者要把它往上傳，syscall 層會依 `SA_RESTART` 決定重啟或回傳 `-EINTR` 給 user space。

`finish_wait()`（`kernel/sched/wait.c:375-398（v6.18）`）把 state 設回 `TASK_RUNNING`，並在 entry 還在 list 上時把它拿掉。`init_wait_entry()`（`kernel/sched/wait.c:280-286（v6.18）`）把 `func` 設成 `autoremove_wake_function`：被叫醒時順便把自己從 list 移除，這樣 waker 第二次 `wake_up` 時就不會重複叫醒同一個 task。

## 為什麼條件一定要在迴圈裡重檢

### 理由一：lost wakeup（為什麼要先上名單再檢查）

如果反過來寫——這就是舊 `sleep_on()` 的形狀：

```
 sleeper                                  waker
 if (!cond) {                             
     ┌─────── 空隙 ───────┐
     │                      │             cond = true;
     │                      │             wake_up(q);   ← 名單是空的，沒有人被叫醒
     └──────────────────────┘
     add_to_queue(q); schedule();         
 }                                        → sleeper 永遠睡下去
```

`___wait_event` 把順序改成「先 `prepare_to_wait_event`（上名單 + 設 state），再檢查 `condition`」，空隙就消失了。

### 理由二：spurious wakeup 與「被搶走」

醒來不代表條件成立：

- `wake_up_all()` 叫醒所有人，但資源只有一份。
- `wake_up` 的 key 過濾不精確（例如同一個 wait queue 上同時有等讀和等寫的人）。
- signal 或其他路徑讓 `schedule()` 提早返回。

這個問題在 user space 的 condition variable 一模一樣。下面的程式讓兩個 consumer 等同一個條件，producer 只生產一個 item 但 broadcast：

```c
/* ifwhile.c: 等待條件用 if 還是 while？兩個 consumer 等一個 item */
#include <pthread.h>
#include <stdio.h>
#include <unistd.h>

static pthread_mutex_t m = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t  c = PTHREAD_COND_INITIALIZER;
static int items, waiting, use_while;

static void *consumer(void *arg)
{
	pthread_mutex_lock(&m);
	waiting++;
	if (use_while) {
		while (items == 0)
			pthread_cond_wait(&c, &m);
	} else {
		if (items == 0)             /* 錯誤示範：醒來後不重檢 */
			pthread_cond_wait(&c, &m);
	}
	if (items == 0)
		printf("  consumer %ld: woke up but items == 0  <-- BUG\n", (long)arg);
	else {
		items--;
		printf("  consumer %ld: got item\n", (long)arg);
	}
	pthread_mutex_unlock(&m);
	return NULL;
}

static void run(int w)
{
	pthread_t t[2];
	use_while = w; items = 0; waiting = 0;
	printf("%s:\n", w ? "while (cond) wait" : "if (cond) wait");
	for (long i = 0; i < 2; i++)
		pthread_create(&t[i], NULL, consumer, (void *)i);
	for (;;) {                          /* 等兩個都睡下去 */
		pthread_mutex_lock(&m);
		int ok = (waiting == 2);
		pthread_mutex_unlock(&m);
		if (ok) break;
		usleep(1000);
	}
	usleep(10000);
	pthread_mutex_lock(&m);
	items = 1;                          /* 只生產一個 */
	pthread_cond_broadcast(&c);         /* 像 wake_up_all：兩個都叫醒 */
	pthread_mutex_unlock(&m);
	if (w) {                            /* while 版：第二個會再睡回去，補一個讓它結束 */
		usleep(10000);
		pthread_mutex_lock(&m);
		items++;
		pthread_cond_signal(&c);
		pthread_mutex_unlock(&m);
	}
	pthread_join(t[0], NULL);
	pthread_join(t[1], NULL);
}

int main(void)
{
	run(0);
	run(1);
	return 0;
}
```

```
$ gcc -O2 -pthread -o ifwhile ifwhile.c && ./ifwhile
if (cond) wait:
  consumer 0: got item
  consumer 1: woke up but items == 0  <-- BUG
while (cond) wait:
  consumer 1: got item
  consumer 0: got item
```

`if` 版的第二個 consumer 醒來時 item 已經被第一個拿走了，它卻照樣往下走。`while` 版的第二個 consumer 醒來發現沒東西，乖乖睡回去，直到第二個 item 出現。`___wait_event` 的 `for (;;)` 加上 `schedule()` 之後的 `if (condition) break`，就是 kernel 版的 `while`。

**讀碼推論**：`wait_event*` 返回 0 時，`condition` 在**那一刻**為真；但如果條件涉及共享資料而 sleeper 醒來後沒有拿 lock，條件可能立刻又變假。pipe 的 reader 醒來後會 `mutex_lock(&pipe->mutex)` 再重新判斷（`anon_pipe_read` 的外層迴圈），這是同一個原則的第二層。

## wake_up 家族

`include/linux/wait.h:221-247（v6.18）`：

```c
#define wake_up(x)			__wake_up(x, TASK_NORMAL, 1, NULL)
#define wake_up_nr(x, nr)		__wake_up(x, TASK_NORMAL, nr, NULL)
#define wake_up_all(x)			__wake_up(x, TASK_NORMAL, 0, NULL)
#define wake_up_locked(x)		__wake_up_locked((x), TASK_NORMAL, 1)
#define wake_up_all_locked(x)		__wake_up_locked((x), TASK_NORMAL, 0)
#define wake_up_sync(x)			__wake_up_sync(x, TASK_NORMAL)

#define wake_up_interruptible(x)	__wake_up(x, TASK_INTERRUPTIBLE, 1, NULL)
#define wake_up_interruptible_nr(x, nr)	__wake_up(x, TASK_INTERRUPTIBLE, nr, NULL)
#define wake_up_interruptible_all(x)	__wake_up(x, TASK_INTERRUPTIBLE, 0, NULL)
#define wake_up_interruptible_sync(x)	__wake_up_sync((x), TASK_INTERRUPTIBLE)
...
#define wake_up_interruptible_poll(x, m)					\
	__wake_up(x, TASK_INTERRUPTIBLE, 1, poll_to_key(m))
#define wake_up_interruptible_sync_poll(x, m)					\
	__wake_up_sync_key((x), TASK_INTERRUPTIBLE, poll_to_key(m))
```

名字是四個維度的組合，讀的時候拆開看：

| 片段 | 意義 | 讀碼推論 |
|---|---|---|
| `_interruptible` | mode = `TASK_INTERRUPTIBLE`：只叫醒可中斷睡眠的 task | 配對的 sleeper 用 `wait_event_interruptible*`；`TASK_UNINTERRUPTIBLE` 的 sleeper 不會被它叫醒 |
| 不帶 `_interruptible` | mode = `TASK_NORMAL` = INTERRUPTIBLE \| UNINTERRUPTIBLE | 兩種都叫 |
| 第三個參數 `1` / `nr` / `0` | 最多叫醒幾個 **exclusive** waiter；`0` = 全部 | 非 exclusive waiter 不受這個數字限制，一律叫醒 |
| `_all` | nr = 0 | 常見於「狀態改變、大家都要重新看」，如 pipe 關閉 |
| `_sync` | `WF_SYNC`：提示 scheduler「waker 馬上要睡了，被叫醒的 task 可以留在本 CPU」 | 效能提示，不影響正確性 |
| `_poll(x, m)` | 傳一個 key（epoll 事件遮罩）給每個 entry 的 `func` | 等待者可以用 key 過濾，例如 epoll 只對 `EPOLLIN` 感興趣 |
| `_locked` | 呼叫者已經拿著 `wq_head->lock` | 往上找 `spin_lock(&q->lock)` |

`__wake_up` 最後走到 `__wake_up_common()`（`kernel/sched/wait.c:92-116（v6.18）`）：

```c
static int __wake_up_common(struct wait_queue_head *wq_head, unsigned int mode,
			int nr_exclusive, int wake_flags, void *key)
{
	wait_queue_entry_t *curr, *next;

	lockdep_assert_held(&wq_head->lock);

	curr = list_first_entry(&wq_head->head, wait_queue_entry_t, entry);

	if (&curr->entry == &wq_head->head)
		return nr_exclusive;

	list_for_each_entry_safe_from(curr, next, &wq_head->head, entry) {
		unsigned flags = curr->flags;
		int ret;

		ret = curr->func(curr, mode, wake_flags, key);
		if (ret < 0)
			break;
		if (ret && (flags & WQ_FLAG_EXCLUSIVE) && !--nr_exclusive)
			break;
	}

	return nr_exclusive;
}
```

三個觀察：

1. 用 `_safe` 走訪，因為 `curr->func`（`autoremove_wake_function`）會把 `curr` 從 list 刪掉（[Ch 23](./23-list-head-hlist.md) 講過 `_safe` 的用途）。
2. **`curr->func(...)` 是間接呼叫**。預設的 `default_wake_function()`（`kernel/sched/core.c:7264-7270（v6.18）`）就是 `try_to_wake_up(curr->private, mode, wake_flags)`；但任何人都可以塞自己的 `func`。
3. exclusive 的 break 條件解釋了為什麼 pipe 的 reader/writer 用 `wait_event_interruptible_exclusive`：一次只叫醒一個 reader，避免一個 byte 進來、十個 reader 全部醒來搶（thundering herd）。代價是**被叫醒的那個如果沒把資料讀完，有責任叫醒下一個**——這就是 `anon_pipe_read` 裡 `wake_next_reader` 的由來，後面會讀到。

## 完整例子：配對 pipe 的 sleeper 與 waker

`struct pipe_inode_info` 有兩條 wait queue（`include/linux/pipe_fs_i.h:64-65、88（v6.18）`）：

```c
 *	@rd_wait: reader wait point in case of empty pipe
 *	@wr_wait: writer wait point in case of full pipe
...
	wait_queue_head_t rd_wait, wr_wait;
```

### 第一步：撈出所有碰 `rd_wait` 的地方

先示範一個失敗的搜尋。`rg -n 'rd_wait' -g '*.c' -g '*.h' .` 在 v6.18 tree 會吐出一大堆不相干的結果：`litex_mmc_sdcard_wait_done`、`discard_wait_queue`、`hw_guard_wait`、xillybus 自己的 `channel->rd_wait`……因為 `rd_wait` 是子字串，也是很常見的成員名。**用「型別 + 成員」的寫法縮小**：

```
$ rg -c 'pipe->rd_wait\b' -g '*.c' .
./fs/pipe.c:17
./fs/coredump.c:2
./fs/splice.c:2
./kernel/watch_queue.c:4
$ rg -n 'pipe->rd_wait\b' -g '*.c' . | grep -v '^./fs/pipe.c'
./fs/coredump.c:594:	wake_up_interruptible_sync(&pipe->rd_wait);
./fs/coredump.c:602:	wait_event_interruptible(pipe->rd_wait, pipe->readers == 1);
./kernel/watch_queue.c:107:	spin_lock_irq(&pipe->rd_wait.lock);
./kernel/watch_queue.c:136:		spin_unlock_irq(&pipe->rd_wait.lock);
./kernel/watch_queue.c:139:	wake_up_interruptible_sync_poll_locked(&pipe->rd_wait, EPOLLIN | EPOLLRDNORM);
./kernel/watch_queue.c:143:	spin_unlock_irq(&pipe->rd_wait.lock);
./fs/splice.c:181:	if (waitqueue_active(&pipe->rd_wait))
./fs/splice.c:182:		wake_up_interruptible(&pipe->rd_wait);
```

（變數名不一定都叫 `pipe`；要更完整，可以用 [Ch 49](./49-clangd-lsp.md) 的 clangd「find references」對 `pipe_inode_info::rd_wait` 這個欄位查，它不看變數名。）

把 `fs/pipe.c` 的 17 處按角色分類：

| 角色 | 位置（v6.18） | 程式碼 |
|---|---|---|
| sleeper（exclusive） | `fs/pipe.c:388` | `wait_event_interruptible_exclusive(pipe->rd_wait, pipe_readable(pipe))` |
| sleeper（舊式） | `fs/pipe.c:1076` | `wait_event_interruptible(pipe->rd_wait, pipe_readable(pipe))`（`pipe_wait_readable`，splice 用） |
| sleeper（手寫迴圈） | `fs/pipe.c:1105-1108` | `prepare_to_wait` / `schedule` / `finish_wait`（`wait_for_partner`，FIFO open） |
| waker：寫入了資料 | `fs/pipe.c:571、596` | `wake_up_interruptible_sync_poll(&pipe->rd_wait, EPOLLIN \| EPOLLRDNORM)` |
| waker：接力叫下一個 reader | `fs/pipe.c:401` | 同上，在 `anon_pipe_read` 結尾 |
| waker：pipe 關閉 | `fs/pipe.c:737` | `wake_up_interruptible_all(&pipe->rd_wait)` |
| poll 註冊 | `fs/pipe.c:676` | `poll_wait(filp, &pipe->rd_wait, wait)` |
| 借用它的 lock | `fs/pipe.c:250、256` | `spin_lock_irq(&pipe->rd_wait.lock)`（watch queue 的情況） |

**一張配對表把間接鴻溝填起來了**：reader 睡在 388 行，能叫醒它的只有 writer（571、596）、上一個 reader（401）、關閉（737）、splice（`fs/splice.c:182`）、watch queue（`kernel/watch_queue.c:139`）、coredump（`fs/coredump.c:594`）這幾處。

### 第二步：檢查條件和 waker 的對應

sleeper 的條件（`fs/pipe.c:229-236（v6.18）`）：

```c
/* Done while waiting without holding the pipe lock - thus the READ_ONCE() */
static inline bool pipe_readable(const struct pipe_inode_info *pipe)
{
	union pipe_index idx = { .head_tail = READ_ONCE(pipe->head_tail) };
	unsigned int writers = READ_ONCE(pipe->writers);

	return !pipe_empty(idx.head, idx.tail) || !writers;
}
```

註解那句「Done while waiting without holding the pipe lock」是關鍵：條件在 `pipe->mutex` 外被評估（呼叫 `wait_event` 前已經 `mutex_unlock`），所以要 `READ_ONCE`（[Ch 21](./21-read-once-and-barriers.md)）。條件有兩個分支——「有資料」或「沒有 writer 了」——對應的 waker 也應該有兩類：寫入資料的 writer、以及關閉時的 `wake_up_interruptible_all`。逐一核對，每個讓條件變真的地方都有一個 wake_up，**這就是讀 wait queue code 的檢查方法**：列出條件中每一個變數，確認每個改它的地方後面都跟著 wake_up。少一個就是 bug（task 永遠睡下去）。

### 第三步：讀 reader 的接力

`anon_pipe_read` 的等待與結尾（`fs/pipe.c:388-403（v6.18）`）：

```c
		if (wait_event_interruptible_exclusive(pipe->rd_wait, pipe_readable(pipe)) < 0)
			return -ERESTARTSYS;

		wake_next_reader = true;
		mutex_lock(&pipe->mutex);
	}
	if (pipe_is_empty(pipe))
		wake_next_reader = false;
	mutex_unlock(&pipe->mutex);

	if (wake_writer)
		wake_up_interruptible_sync_poll(&pipe->wr_wait, EPOLLOUT | EPOLLWRNORM);
	if (wake_next_reader)
		wake_up_interruptible_sync_poll(&pipe->rd_wait, EPOLLIN | EPOLLRDNORM);
	kill_fasync(&pipe->fasync_writers, SIGIO, POLL_OUT);
	return ret;
```

因為用的是 exclusive wait，writer 一次只叫醒一個 reader。被叫醒的 reader 讀完後如果 pipe 裡還有資料（`!pipe_is_empty`），它負責再叫醒下一個。注意 wake_up 都在 `mutex_unlock` **之後**：被叫醒的 task 第一件事就是 `mutex_lock`，如果 waker 還拿著 mutex，被叫醒的人只會立刻再睡一次。

`pipe_wait_readable()` 上方的註解（`fs/pipe.c:1064-1071（v6.18）`）直接稱它為「the stupid "wait for pipe to be readable or writable" model」，因為它用非 exclusive 等待，沒有接力邏輯——作者用註解告訴你「正確的寫法請看 pipe_read/write」。注意註解裡的名字已經過時：v6.15 起匿名 pipe 的讀寫函式改名為 `anon_pipe_read` / `anon_pipe_write`（commit f017b0a4951f「pipe: don't update {a,c,m}time for anonymous pipes」把匿名 pipe 拆出自己的 fops 時改的名，`git tag --contains` 顯示最早在 v6.15），掛在 `pipeanon_fops`（`fs/pipe.c:1257（v6.18）`）上；`pipefifo_fops` 只給具名 FIFO 用。**註解不會跟著重構更新**，這是讀碼時「信程式碼、不全信註解」的一個小例子。讀 kernel 時，這種帶情緒的註解通常是在標示歷史包袱。

## 實測：在 QEMU 裡看一個 reader 睡下去、被叫醒

測試程式：parent 對空 pipe `read(2)`，child 200 ms 後寫一個 byte。

```c
/* pipewait.c: parent 在空 pipe 上 read(2) 睡著，child 200ms 後寫入把它叫醒 */
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

static void wr(const char *path, const char *s)
{
	int fd = open(path, O_WRONLY | O_TRUNC);
	if (fd < 0 || write(fd, s, strlen(s)) < 0) { perror(path); exit(1); }
	close(fd);
}

int main(void)
{
	int p[2];
	char c, pid[32];
	if (pipe(p) < 0) { perror("pipe"); return 1; }
	pid_t child = fork();
	if (child == 0) {               /* waker */
		usleep(200 * 1000);
		write(p[1], "x", 1);
		_exit(0);
	}
	snprintf(pid, sizeof pid, "%d", getpid());
	wr("/sys/kernel/tracing/set_ftrace_pid", pid);
	wr("/sys/kernel/tracing/tracing_on", "1");
	ssize_t n = read(p[0], &c, 1);  /* sleeper：pipe 是空的，會睡 */
	wr("/sys/kernel/tracing/tracing_on", "0");
	waitpid(child, NULL, 0);
	printf("parent=%d child=%d read=%zd byte='%c'\n", getpid(), child, n, c);
	return 0;
}
```

guest 腳本把 function_graph 的根設在 `anon_pipe_read`，並打開 `sched_switch`、`sched_wakeup` 事件：

```sh
echo anon_pipe_read > $T/set_graph_function
echo function_graph > $T/current_tracer
echo 1 > $T/options/funcgraph-tail
echo 1 > $T/options/funcgraph-proc
echo 1 > $T/events/sched/sched_switch/enable
echo 1 > $T/events/sched/sched_wakeup/enable
echo > $T/trace
/bin/pipewait
cat $T/trace
```

`gcc -static` 編好後用 `run-script.sh guest.sh ./pipewait` 跑。輸出（刪掉開頭一段剛好打進來的 timer 中斷與 `_raw_spin_*` 行，其餘照抄）：

```
parent=71 child=72 read=1 byte='x'
 0)   pipewai-71   |               |  anon_pipe_read() {
 ...（timer 中斷，略）
 0)   pipewai-71   |               |    mutex_lock() {
 0)   pipewai-71   |   1.312 us    |      __cond_resched();
 0)   pipewai-71   |   5.821 us    |    } /* mutex_lock */
 0)   pipewai-71   |   1.172 us    |    mutex_unlock();
 0)   pipewai-71   |   0.662 us    |    __cond_resched();
 0)   pipewai-71   |   1.082 us    |    init_wait_entry();
 0)   pipewai-71   |               |    prepare_to_wait_event() {
 0)   pipewai-71   |   6.502 us    |    } /* prepare_to_wait_event */
 0)   pipewai-71   |               |    schedule() {
 ...
 0)   pipewai-71   |               |      __traceiter_sched_switch() {
 0)   pipewai-71   |               |        /* sched_switch: prev_comm=pipewait prev_pid=71 prev_prio=120 prev_state=S ==> next_comm=swapper/0 next_pid=0 next_prio=120 */
 ...
 0)    <idle>-0    |               |      /* sched_wakeup: comm=pipewait pid=71 prio=120 target_cpu=000 */
 0)    <idle>-0    |               |      /* sched_switch: prev_comm=swapper/0 prev_pid=0 prev_prio=120 prev_state=R ==> next_comm=pipewait next_pid=71 next_prio=120 */
 0)    <idle>-0    =>   pipewai-71  
 0)   pipewai-71   |               |      finish_task_switch.isra.0() {
 0)   pipewai-71   | + 10.911 us   |      } /* finish_task_switch.isra.0 */
 0)   pipewai-71   | @ 198892.4 us |    } /* schedule */
 0)   pipewai-71   |   2.024 us    |    finish_wait();
 0)   pipewai-71   |               |    mutex_lock() {
 0)   pipewai-71   |   0.902 us    |      __cond_resched();
 0)   pipewai-71   |   3.266 us    |    } /* mutex_lock */
 0)   pipewai-71   | + 29.005 us   |    anon_pipe_buf_release();
 0)   pipewai-71   |   1.252 us    |    mutex_unlock();
 0)   pipewai-71   |   1.052 us    |    kill_fasync();
 0)   pipewai-71   | @ 200104.8 us |  } /* anon_pipe_read */
```

和展開後的巨集逐行對照：`mutex_unlock` → `init_wait_entry` → `prepare_to_wait_event` → `schedule()`（`prev_state=S` 就是 `TASK_INTERRUPTIBLE`，ps 顯示的 S 狀態）→ 約 199 ms 後醒來（`@` 表示超過 100 ms）→ `finish_wait` → `mutex_lock` 重新拿鎖。`pipe_readable()` 是 inline 函式，所以 trace 裡看不到條件檢查本身。

有一個細節值得停下來看：`sched_wakeup` 這一行記在 **CPU 0 的 `<idle>` 身上**，而不是 waker（child, pid 72，在 CPU 1）。因為 parent 睡在 CPU 0，而 CPU 0 正閒著，`try_to_wake_up` 會走「把 wakeup 排進目標 CPU 的 wake list、發 IPI」的路徑（`ttwu_queue_wakelist`，練習 E 的 trace 裡出現過），真正把 task 放回 runqueue 的動作在目標 CPU 上做。**所以「誰叫醒我」不能只看 sched_wakeup 那一行的 TASK 欄位**；要知道 waker 是誰，靜態配對表比 trace 可靠，或者在 `__wake_up_sync_key` 上另外下 function filter。

## 自訂 wake function：folio 位元等待

練習 E 追 `read(2)` 時，冷 cache 的讀取會睡在 page cache folio 上等 I/O 完成。那條路徑沒有用 `wait_event`，而是 `folio_wait_bit_common()`（`mm/filemap.c:1251（v6.18）`），它示範了 wait queue 更進階的用法：

```c
	wait_queue_head_t *q = folio_waitqueue(folio);
	...
	init_wait(wait);
	wait->func = wake_page_function;
	wait_page.folio = folio;
	wait_page.bit_nr = bit_nr;
```

- **wait queue 不在 folio 裡**。每個 folio 都放一個 `wait_queue_head` 太浪費（16 bytes 以上 × 幾百萬個 page），所以 `folio_waitqueue()` 把 folio 指標 hash 進一張全域表：`static wait_queue_head_t folio_wait_table[PAGE_WAIT_TABLE_SIZE]`（`mm/filemap.c:1075（v6.18）`），`PAGE_WAIT_TABLE_BITS` 是 8，所以有 256 個 bucket（`mm/filemap.c:1073-1074（v6.18）`）。
- **因此同一個 wait queue 上會有等不同 folio 的人**。`wake_page_function` 是自訂的 `func`，被呼叫時先比對 key 裡的 folio 和 bit，不是自己等的就回傳 0、不叫醒。這就是 `__wake_up_common` 裡 `curr->func(curr, mode, wake_flags, key)` 那個 `key` 參數存在的理由。
- waker 是 I/O 完成路徑上的 `folio_end_read()`（`mm/filemap.c:1531-1544（v6.18）`），它清掉 `PG_locked`、設好 `PG_uptodate`，如果 folio 有 waiters 旗標就呼叫 `folio_wake_bit()`。ext4 的 bio 完成函式 `__read_end_io()`（`fs/ext4/readpage.c:69-78（v6.18）`）對 bio 裡每個 folio 呼叫它。

讀到 `wait->func = xxx` 時，記得那是 wait queue 的 callback 掛勾，`xxx` 的 context 是 **waker 的 context**——可能是中斷或 softirq，不能睡。

## completion：一次性事件的專用工具

很多等待其實只是「等某件事做完一次」。這種情況用 wait queue 要自己準備一個 flag 和條件；`struct completion` 把兩者包在一起（`include/linux/completion.h:26-29（v6.18）`）：

```c
struct completion {
	unsigned int done;
	struct swait_queue_head wait;
};
```

`done` 是計數，`wait` 是簡化版的 wait queue（simple wait queue，`swait`。`include/linux/swait.h` 開頭的註解列出它刻意拿掉的東西：沒有非 exclusive 模式（所有 waiter 都是 exclusive，`swake_up_one` 只叫醒一個）、不支援自訂 wake callback；換來的是 wakeup 成本有上限，可以用 raw spinlock 保護，PREEMPT_RT 上也能在 hardirq 裡使用）。等待端的迴圈（`kernel/sched/completion.c:86-108（v6.18）`）：

```c
	if (!x->done) {
		DECLARE_SWAITQUEUE(wait);

		do {
			if (signal_pending_state(state, current)) {
				timeout = -ERESTARTSYS;
				break;
			}
			__prepare_to_swait(&x->wait, &wait);
			__set_current_state(state);
			raw_spin_unlock_irq(&x->wait.lock);
			timeout = action(timeout);
			raw_spin_lock_irq(&x->wait.lock);
		} while (!x->done && timeout);
		__finish_swait(&x->wait, &wait);
		if (!x->done)
			return timeout;
	}
	if (x->done != UINT_MAX)
		x->done--;
	return timeout ?: 1;
```

形狀和 `___wait_event` 一樣：上名單、設 state、放鎖睡覺（`action` 通常是 `schedule_timeout`）、拿鎖、`while (!x->done)` 重檢。差別是條件固定為 `done`，而且檢查在 `x->wait.lock` 保護下做，所以不需要 `READ_ONCE` 之類的技巧。喚醒端 `complete()` 在同一把鎖下 `done++` 再 `swake_up_locked`（`kernel/sched/completion.c:21-31（v6.18）`）。`complete_all()` 把 `done` 設成 `UINT_MAX`，之後所有等待者都直接通過——這就是 `if (x->done != UINT_MAX)` 那行的意思。

真實例子：建立 kernel thread。`__kthread_create_on_node()`（`kernel/kthread.c:503-545（v6.18）`，節錄）：

```c
	DECLARE_COMPLETION_ONSTACK(done);
	...
	create->done = &done;
	...
	spin_lock(&kthread_create_lock);
	list_add_tail(&create->list, &kthread_create_list);
	spin_unlock(&kthread_create_lock);

	wake_up_process(kthreadd_task);
	/*
	 * Wait for completion in killable state, for I might be chosen by
	 * the OOM killer while kthreadd is trying to allocate memory for
	 * new kernel thread.
	 */
	if (unlikely(wait_for_completion_killable(&done))) {
```

新 thread 在 `kthread()`（`kernel/kthread.c:411` 起）裡設好自己之後呼叫 `complete(done)`（`kernel/kthread.c:450（v6.18）`）。三個讀碼重點：

1. **`DECLARE_COMPLETION_ONSTACK`**：completion 在呼叫者的 stack 上，所以呼叫者被 kill 提早返回時，對方不能再碰它。`kthread()` 裡的 `done = xchg(&create->done, NULL)`（`kernel/kthread.c:425`）就是在處理這個 race：誰先把指標換成 NULL，誰負責清理。
2. **`_killable`**：只對致命 signal 醒來。註解解釋了為什麼不用 uninterruptible：OOM killer 要能殺掉它。
3. **配對方式**：找 completion 的 waker，`rg` 結構成員名（`create->done`、`->done`）或 completion 變數名，找 `complete(`。

## 對比與取捨

| 工具 | 條件 | 適用 | 讀碼時的配對線索 |
|---|---|---|---|
| `wait_event*` + `wake_up*` | 任意 C 運算式 | 狀態可能反覆變化（pipe 有沒有資料） | 同一個 `wait_queue_head` 成員 |
| 手寫 `prepare_to_wait` / `schedule` / `finish_wait` | 任意 | 等待中間要做 `wait_event` 表達不了的事（如放鎖、拿鎖） | 同上；注意手寫版要自己保證順序 |
| `wait_woken` / `woken_wake_function` | 任意 | 網路 socket 等「醒來要先檢查 `WQ_FLAG_WOKEN`」的情況 | `DEFINE_WAIT_FUNC(wait, woken_wake_function)` |
| `completion` | 固定（`done`） | 一次性事件：初始化完成、I/O 完成、thread 啟動 | `complete(&x)` 與 `wait_for_completion*(&x)` |
| folio / bit wait | 某個 bit 被清掉 | page cache I/O | `folio_wait_*` 與 `folio_end_read` / `folio_unlock` |
| mutex | 「拿到鎖」 | 互斥，不是等事件 | 不要用 mutex 當事件通知（`mutex_unlock` 必須由持有者呼叫） |

## 踩雷集錦

1. **「`wake_up` 會讓條件變真」**：不會。`wake_up` 只是叫醒，條件要 waker 自己先改。讀 waker 時確認「改條件」在「wake_up」之前；兩者順序反了就有 race。
2. **「被叫醒就代表條件成立」**：本章 ifwhile 範例的 `if` 版就是這個錯誤。`wait_event` 的迴圈幫你處理了，手寫等待迴圈時最容易漏。
3. **「`wake_up_interruptible` 會叫醒所有在這個 queue 上睡的 task」**：只叫醒 `TASK_INTERRUPTIBLE` 的，而且 exclusive waiter 只叫一個。sleeper 用 `wait_event_killable`（`TASK_KILLABLE` = `TASK_WAKEKILL | TASK_UNINTERRUPTIBLE`）的話，`wake_up_interruptible` 叫不醒它。配對時要核對 mode。
4. **「在 hardirq 裡可以 `wait_event`」**：絕對不行，它會 `schedule()`。但**在 hardirq 裡 `wake_up` 是可以的**，因為 `__wake_up_common_lock` 用 `spin_lock_irqsave`。所以 waker 可能在任何 context，sleeper 只能在可睡的 context。
5. **「搜 `rd_wait` 就能找到全部 waker」**：成員名是子字串，搜尋會混進大量雜訊；變數名也不一定叫 `pipe`。用「`->成員名\b`」縮小，或用 clangd 對欄位查 references。

## 進階：再往深一層

- **`TASK_IDLE`**：`TASK_UNINTERRUPTIBLE | TASK_NOLOAD`（`include/linux/sched.h:139（v6.18）`）。不可中斷但不算進 load average，kernel thread 長時間等待時常用，`wait_event_idle()` 就是它。
- **`wait_event_interruptible_locked`**：條件必須在 `wq_head->lock` 下檢查時使用，呼叫者進來時已持有鎖。watch queue 借用 `pipe->rd_wait.lock` 保護資料就是同一種思路——wait queue 的 lock 不只保護名單。
- **epoll 的回呼**：`poll_wait(filp, &pipe->rd_wait, wait)` 會在 `rd_wait` 上掛一個 entry，它的 `func` 是 epoll 的 `ep_poll_callback`，不叫醒任何 task，而是把 fd 加進 ready list。這是「wait queue 其實是 callback list」最好的例子，`fs/eventpoll.c` 值得配 final project 一起讀。
- **lost wakeup 的實戰版本**：搜尋 lore.kernel.org 上 `"lost wakeup"` 的 patch，會看到很多「waker 用 `waitqueue_active()` 檢查是否有人在等、省掉 wake_up」導致的 bug。`fs/splice.c:181` 就有這種寫法，`waitqueue_active()` 的 kernel-doc（`include/linux/wait.h`）用一大段註解說明它需要配一個 memory barrier 才安全——讀那段註解是理解 [Ch 21](./21-read-once-and-barriers.md) barrier 配對的好練習。

## 動手練習

1. 把 pipewait.c 改成兩個 reader（fork 兩次）、一個 writer 寫兩個 byte，在 trace 裡找 `wake_next_reader` 接力的證據（第二個 reader 是被誰叫醒的）。
2. 用 `make O=../build kernel/kthread.i` 展開 `wait_for_completion_killable` 的呼叫點，對照本章的 `do_wait_for_common`，找出 `action` 實際是哪個函式。
3. 在 v6.18 tree 裡挑一個 driver 的 `wait_event_interruptible_timeout`，寫出它的條件、所有 waker、每個 waker 的 context（用 [Ch 32](./32-async-irq-softirq-workqueue.md) 的判斷表）。

## 本章重點整理

- wait queue = 一把 spinlock + 一條 `wait_queue_entry` list；每個 entry 帶 task 指標和一個 wake callback。
- `wait_event*` 展開後是「`prepare_to_wait_event` → 檢查條件 → `schedule()` → 再檢查」的 `for (;;)` 迴圈；先上名單再檢查，避免 lost wakeup；醒來重檢，處理 spurious wakeup 與被搶走。
- `wake_up*` 的名字由 mode、數量、sync、key、locked 組成；exclusive waiter 有接力責任。
- 讀碼配對：從 `wait_queue_head` 成員出發列出所有 sleeper 與 waker，核對條件中每個變數的修改點都跟著 wake_up。
- completion 是條件固定的 wait queue，用於一次性事件；folio 等待是用 hash 表與自訂 wake function 的進階形態。

## 自我檢核

- [ ] 不看原始碼，寫出 `___wait_event` 迴圈的五個步驟，並說明把「檢查條件」移到 `prepare_to_wait_event` 之前會出什麼事。
- [ ] 面試官問「kernel 的 wait queue 和 pthread condition variable 有什麼異同？」你會怎麼答？至少說出兩個相同點、兩個差異。
- [ ] 解釋為什麼 pipe reader 用 exclusive wait，而這個選擇讓 `anon_pipe_read` 多了什麼責任。
- [ ] trace 裡 `sched_wakeup` 出現在 `<idle>` 身上，你怎麼判斷真正的 waker？
- [ ] 一個 driver 的 sleeper 用 `wait_event_killable`，waker 用 `wake_up_interruptible`。這段 code 有什麼問題？
- [ ] folio 的 wait queue 為什麼要 hash 成 256 個 bucket，而不是每個 folio 一個？這讓 wake function 必須多做什麼？

## 延伸閱讀

### 官方文件

- **[Completions - "wait for completion" barrier APIs](https://docs.kernel.org/scheduler/completion.html)**（v6.18 tree：`Documentation/scheduler/completion.rst`）
  - **讀哪裡**：整篇，特別是「Usage」與「Waiting for completions」兩節，列出所有 `wait_for_completion_*` 變體和它們能不能在 atomic context 呼叫。
  - **和本章的關聯**：補完本章 completion 一節沒列的 `_timeout`、`_io`、`try_wait_for_completion`。
- **`include/linux/wait.h` 本身**
  - **讀哪裡**：`waitqueue_active()` 與 `wq_has_sleeper()` 前面的長註解；它是 kernel 裡把「為什麼需要 barrier」寫得最具體的地方之一。
  - **前提**：讀過 [Ch 21](./21-read-once-and-barriers.md)。

### 書籍

- **《Linux Device Drivers》3rd ed.** — Corbet, Rubini, Kroah-Hartman（O'Reilly, 2005；[LWN 免費全文](https://lwn.net/Kernel/LDD3/)）
  - **讀哪幾章**：Ch 6「Advanced Char Driver Operations」的「Blocking I/O」一節，有 scull pipe 的完整 sleeper/waker 範例，以及「manual sleeps」的寫法。
  - **注意**：書中仍介紹 `sleep_on`（當時還沒移除）並警告不要用；API 名稱以本章為準。
- **《Linux Kernel Development》3rd ed.** — Robert Love（2010）
  - **讀哪幾章**：Ch 4「Process Scheduling」的「Sleeping and Waking Up」一節，有一張 wait queue 的狀態轉換圖，配本章的迴圈解碼讀。
- **《Operating Systems: Three Easy Pieces》** — Remzi & Andrea Arpaci-Dusseau（免費線上版）
  - **讀哪幾章**：第 30 章「Condition Variables」。它用 producer/consumer 一步步推出「為什麼要用 while」，和本章 ifwhile 範例是同一個論證，寫得更細。

### 技術文章

- **LWN 的 wait queue 相關文章**
  - **怎麼找**：在 [LWN Kernel Index](https://lwn.net/Kernel/Index/) 的「Wait queues」條目下，可以找到 simple wait queue（swait）合併時的討論，解釋為什麼 completion 從一般 wait queue 換成 swait。
  - **為什麼值得讀**：swait 的限制（不支援自訂 wake function、`swake_up_all` 不能在中斷關閉時對大量 waiter 使用）都是 PREEMPT_RT 的延遲需求逼出來的，LWN 有當時的取捨討論。

sleeper 和 waker 之間靠一個 `wait_queue_head` 連起來，wait queue 本身又是「一串 callback」。下一章把這個模式推廣：kernel 裡大量的「先註冊、事件發生時被呼叫」的結構，以及怎麼從 callback 反查到觸發它的地方。

→ [Ch 34 notifier 與 callback 註冊模式](./34-notifiers-and-callbacks.md)
