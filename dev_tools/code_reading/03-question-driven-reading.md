# Ch 3 — 帶著問題讀：提問驅動與假說驗證迴圈

> **目標**：能把「我想懂 X」這種模糊目標拆成可回答、可驗證、有終點的具體問題；能對每個問題跑「假說 → 預測 → 驗證 → 修正」迴圈；並在 v6.18 的 `fs/pipe.c` 上完整走一遍「寫到滿的 pipe 時，誰讓 writer 睡著、誰叫醒它」，每一步都有證據。
> **鴻溝**：本章主要練**目標鴻溝**；實例過程中會順便處理**間接鴻溝**（ops table）與**脈絡鴻溝**（git 考古）。

> **環境**：kernel 程式碼引用 v6.18；user-space 實驗在 WSL（kernel 6.18 系列）上跑，ftrace 實驗在 Ch 0 建好的 QEMU guest（自己 build 的 v6.18）裡跑。

## 為什麼需要這個？

「我想懂 pipe。」

這是一個值得做的目標，但它不是一個可以**開始**的目標。你打開 `fs/pipe.c`，1553 行。從第 1 行讀起？`pipe_lock_cmp_fn` 是什麼，跟 pipe 有什麼關係？`anon_pipe_buf_try_steal` 要讀懂嗎？`watch_queue` 是什麼，為什麼到處都是？讀到第 300 行，你已經忘了第 100 行在講什麼，而且你不知道讀到哪裡可以算「懂了」。

這是 Ch 1 的**目標鴻溝**。Ch 2 解釋了它為什麼致命：沒有假說，Brooks 式的由上而下理解無從啟動，你被迫用最費力的 program model 從頭組裝，工作記憶在第二個函式就滿了。Letovsky 的觀察則告訴我們熟手實際上怎麼做：**他們的閱讀過程是一連串的提問與猜想**。

本章把這件事變成一個可以刻意執行的流程。

## 先建立直覺：讀碼像做實驗，不像讀小說

讀小說，你從第一頁開始，作者負責安排資訊的順序。讀程式碼不是這樣——程式碼的順序是給**編譯器**排的，不是給讀者排的。函式在檔案裡的位置往往是歷史偶然，`fs/pipe.c` 把 `anon_pipe_read` 放在 `anon_pipe_write` 前面，不代表你該先讀它。

更好的心智模型是**科學家做實驗**：

```
          ┌──────────────────────────┐
          │ 問題                      │  「write 到滿的 pipe 時，誰讓 writer 睡著？」
          └────────────┬─────────────┘
                       ▼
          ┌──────────────────────────┐
     ┌───▶│ 假說                      │  「pipe_write() 裡有 wait_event」
     │    └────────────┬─────────────┘
     │                 ▼
     │    ┌──────────────────────────┐
     │    │ 預測                      │  「grep pipe_write 會找到它，裡面有 wait_event」
     │    └────────────┬─────────────┘
     │                 ▼
     │    ┌──────────────────────────┐
     │    │ 驗證                      │  讀碼 / grep / git log / 跑實驗
     │    └────────────┬─────────────┘
     │                 ▼
     │          證據符合預測？
     │          ╱            ╲
     │        否              是
     │        ▼                ▼
     │  ┌──────────┐   ┌──────────────────┐
     └──│ 修正假說  │   │ 暫時接受，往下一層 │──▶ 新的子問題
        └──────────┘   └──────────────────┘
```

關鍵是「**預測**」這一步。假說太模糊就無法驗證（「pipe 有某種等待機制」——當然有，但這句話沒辦法錯）。好的假說要能推出一個具體的、**可能被證明是錯的**預測：「如果我的假說對，那麼我在 X 處應該看到 Y」。看到了，假說暫時成立；沒看到，你學到了東西。

> 這個比喻的限制：科學實驗的對象不會因為你的假說而改變，但程式碼有版本。你的假說可能「對過」——對 v5.4 是對的，對 v6.18 是錯的。這正是為什麼預測要針對**你眼前這一版**去驗證，而不是靠記憶。下面的實例會遇到這種情況。

## 從模糊目標到可回答的問題

### 好問題的四個條件

| 條件 | 意思 | 反例 | 正例 |
|---|---|---|---|
| **可回答** | 答案存在於你能取得的資料裡 | 「pipe 設計得好不好？」 | 「pipe 的容量上限是多少，由什麼決定？」 |
| **可驗證** | 你能判斷一個答案是對還是錯 | 「pipe 大概怎麼運作？」 | 「writer 睡在哪個 wait queue 上？」 |
| **有終點** | 你知道讀到哪裡可以停 | 「pipe 的所有細節」 | 「找到讓 writer 醒來的那一行」 |
| **範圍小** | 答案涉及的程式碼在幾十到幾百行內 | 「VFS 怎麼處理所有檔案的 write？」 | 「`write(2)` 到 pipe 時，VFS 呼叫的是哪個函式？」 |

### 拆解：問題樹

把「我想懂 pipe」往下拆，每一層都比上一層具體，直到葉子節點滿足四個條件：

```
我想懂 pipe
├── 資料怎麼存？
│   ├── pipe 的緩衝區是什麼資料結構？
│   └── 容量上限是多少，由什麼決定？                    ← 葉子
├── 讀寫怎麼進行？
│   ├── write(2) 到 pipe 時，VFS 呼叫的是哪個函式？     ← 葉子
│   ├── 小量 write 會不會合併進同一頁？                  ← 葉子
│   └── 寫到滿的 pipe 時，誰讓 writer 睡著、誰叫醒它？  ← 葉子（本章主線）
├── 邊界情況？
│   ├── 所有 reader 都關掉時，writer 會怎樣？            ← 葉子（本章練習）
│   └── O_NONBLOCK 時寫到滿會怎樣？                      ← 葉子
└── 為什麼這樣設計？
    └── 為什麼 writer 只在「整頁被讀完」時才被叫醒？     ← 葉子（主線途中會冒出來）
```

注意最後一個問題：你在拆解時**不會知道**要問它。它是在追主線時才冒出來的。問題樹不是一次畫完的，而是邊讀邊長。

### 問題的類型

Letovsky 的研究把讀碼時的提問分成幾類。我們用 kernel 的例子重新整理，加上每一類通常該用什麼手段回答：

| 類型 | 問什麼 | kernel 例子 | 主要手段 | 常牽涉的鴻溝 |
|---|---|---|---|---|
| **What** | 這是什麼？ | `pipe->max_usage` 代表什麼？ | 讀定義、讀註解、看它在哪裡被設定 | 慣用法、領域 |
| **How** | 這件事怎麼做到的？ | writer 怎麼知道 pipe 滿了？ | 讀碼、追控制流 | 間接 |
| **Why** | 為什麼這樣寫？ | 為什麼睡之前要先喚醒 reader？ | 推理、git log、mailing list | 脈絡、領域 |
| **Where** | 在哪裡發生？ | 喚醒 writer 的程式碼在哪一行？ | grep、cscope、找賦值點 | 間接 |
| **When** | 在什麼條件下發生？ | 讀者在什麼情況下才會喚醒 writer？ | 讀分支條件、跑實驗 | 領域 |
| **Whether** | 會不會發生？ | 讀 1 byte 會不會喚醒 writer？ | **跑實驗**最直接 | — |

兩個實用觀察：

1. **Why 問題最難，也最常被跳過**。What、How、Where 的答案在程式碼裡；Why 的答案常常不在。遇到 Why，先問自己「這是領域問題（我缺背景知識）還是脈絡問題（答案在歷史裡）」，再決定去讀書還是去翻 git。
2. **Whether 問題最適合用實驗回答**。讀碼可以推出「應該不會」，但推理可能錯。能跑就跑，十行 C 勝過十分鐘的推理。

## 完整實例：寫到滿的 pipe，誰讓 writer 睡著、誰叫醒它？

現在完整走一次。每一步都寫出假說、預測、證據、判定。**請跟著在你的 tree 上做**，不要只看。

### 步驟 1：VFS 呼叫的是哪個函式？

**假說 H1**：write(2) 到 pipe 時，最後會進到 `fs/pipe.c` 裡一個叫 `pipe_write()` 的函式。（這是很多舊教材、部落格和 LLM 都會告訴你的名字。）

**預測**：`rg` 會找到 `pipe_write` 的定義。

**驗證**：

```
$ cd ~/code_reading/linux
$ rg -n '^pipe_write|^anon_pipe_write|^fifo_pipe_write' fs/pipe.c
431:anon_pipe_write(struct kiocb *iocb, struct iov_iter *from)
604:fifo_pipe_write(struct kiocb *iocb, struct iov_iter *from)
```

**沒有 `pipe_write`。** 預測失敗——這是好事，你學到東西了。v6.18 有兩個：`anon_pipe_write` 和 `fifo_pipe_write`。哪一個是匿名 pipe（`pipe(2)` 建的那種）用的？這是一個間接鴻溝問題，答案在 ops table：

```c
/* fs/pipe.c:1257–1266（v6.18） */
static const struct file_operations pipeanon_fops = {
	.open		= fifo_open,
	.read_iter	= anon_pipe_read,
	.write_iter	= anon_pipe_write,
	.poll		= pipe_poll,
	.unlocked_ioctl	= pipe_ioctl,
	.release	= pipe_release,
	.fasync		= pipe_fasync,
	.splice_write	= iter_file_splice_write,
};
```

`pipe(2)` 建立檔案時用的是 `pipeanon_fops`（`fs/pipe.c:946`、`957` 的 `alloc_file_pseudo` / `alloc_file_clone`）。而 `fifo_pipe_write`（第 604 行）只是包一層：呼叫 `anon_pipe_write` 之後，額外更新檔案的修改時間。

**修正 H1**：匿名 pipe 的寫入走 `vfs_write → new_sync_write → f_op->write_iter → anon_pipe_write`。

這裡浮現一個 Why 子問題：「為什麼分成兩個？」不在主線上，但它花不了多少時間，而且示範了脈絡鴻溝的標準解法：

```
$ git log --oneline v6.14..v6.15 -- fs/pipe.c | grep -i -E 'anon|fops|time'
f017b0a4951f pipe: don't update {a,c,m}time for anonymous pipes
262b2fa99cbe pipe: introduce struct file_operations pipeanon_fops
```

`262b2fa99cbe`（Oleg Nesterov，2025-02）是準備步驟：新增 `pipeanon_fops`，讓匿名 pipe 不再和具名 FIFO 共用 `pipefifo_fops`（v6.18 裡 `pipefifo_fops` 只給 FIFO 用）。`f017b0a4951f` 才是真正改名的那一個：把 `pipe_read`／`pipe_write` 改成 `anon_pipe_read`／`anon_pipe_write`，另加 `fifo_pipe_read`／`fifo_pipe_write` 給 FIFO。它的 commit 訊息說明了理由：匿名 pipe 更新 atime/mtime/ctime 的成本不低而且幾乎沒人用，commit 訊息附了一個 benchmark，拿掉之後一個小量讀寫的測試總時間從約 13.9 秒降到約 9.4 秒。兩個 commit 都在 v6.15 進入主線。

**旁證**：在 WSL 上讓一個 writer 卡在滿的 pipe 上，問 kernel 它睡在哪個函式：

```
$ yes > >(sleep 3) &          # yes 寫得比 sleep 讀得快（sleep 根本不讀），pipe 很快就滿
$ cat /proc/$(pgrep -n -x yes)/wchan
anon_pipe_write
```

`wchan`（wait channel）是 process 睡著時所在的 kernel 函式。這一行同時確認了 H1 的修正版，也預告了步驟 3 的答案：writer 確實睡在 `anon_pipe_write` 裡。

### 步驟 2：「滿」是怎麼判斷的？

**假說 H2**：pipe 是一個環狀緩衝區，有 head 和 tail 兩個索引；「滿」就是已用 slot 數達到容量。

**預測**：會有一個類似 `pipe_full(head, tail, limit)` 的函式；容量存在 `pipe_inode_info` 的某個欄位。

**驗證**：

```c
/* include/linux/pipe_fs_i.h:184–209（v6.18），節錄 */
static inline unsigned int pipe_occupancy(unsigned int head, unsigned int tail)
{
	return (pipe_index_t)(head - tail);
}
...
static inline bool pipe_full(unsigned int head, unsigned int tail,
			     unsigned int limit)
{
	return pipe_occupancy(head, tail) >= limit;
}
```

`anon_pipe_write` 第 510 行呼叫 `pipe_full(head, pipe->tail, pipe->max_usage)`。`max_usage` 在 `alloc_pipe_info()` 裡設定：

```c
/* fs/pipe.c:795、824（v6.18） */
	unsigned long pipe_bufs = PIPE_DEF_BUFFERS;
	...
		pipe->max_usage = pipe_bufs;
```

`PIPE_DEF_BUFFERS` 是 16（`include/linux/pipe_fs_i.h:5`）。每個 slot 是一個 `struct pipe_buffer`，指向一個 page。

**H2 成立**，並且推出一個可以實驗的預測：預設容量 = 16 個 slot × 每 slot 一頁（x86-64 上 `PAGE_SIZE` 是 4096）= **65536 bytes**。另一個細節：`head - tail` 是無號數相減再轉型，所以 head 和 tail 可以一直往上加、自然溢位，不需要取模——取模在存取時才做（`pipe_buf()` 裡的 `slot & (pipe->ring_size - 1)`，這也是為什麼 ring 大小必須是 2 的冪）。

驗證容量的實驗放在步驟 4 的程式裡一起做。

### 步驟 3：writer 怎麼睡著？

**假說 H3**：在 `anon_pipe_write` 裡，pipe 滿了就在某個 wait queue 上睡，條件是「不滿了」。

**預測**：會看到 `wait_event*(pipe->wr_wait, ...)` 之類的呼叫，條件和 `pipe_full` 相關。

**驗證**：`anon_pipe_write` 的主迴圈從第 500 行開始。前半（第 509–548 行）處理「沒滿就配一頁、複製、`head + 1`」；滿了就掉到後半：

```c
/* fs/pipe.c:550–577（v6.18） */
		/* Wait for buffer space to become available. */
		if ((filp->f_flags & O_NONBLOCK) ||
		    (iocb->ki_flags & IOCB_NOWAIT)) {
			if (!ret)
				ret = -EAGAIN;
			break;
		}
		if (signal_pending(current)) {
			if (!ret)
				ret = -ERESTARTSYS;
			break;
		}

		/*
		 * We're going to release the pipe lock and wait for more
		 * space. We wake up any readers if necessary, and then
		 * after waiting we need to re-check whether the pipe
		 * become empty while we dropped the lock.
		 */
		mutex_unlock(&pipe->mutex);
		if (was_empty)
			wake_up_interruptible_sync_poll(&pipe->rd_wait, EPOLLIN | EPOLLRDNORM);
		kill_fasync(&pipe->fasync_readers, SIGIO, POLL_IN);
		wait_event_interruptible_exclusive(pipe->wr_wait, pipe_writable(pipe));
		mutex_lock(&pipe->mutex);
		was_empty = pipe_is_empty(pipe);
		wake_next_writer = true;
	}
```

**H3 成立**，答案在第 573 行：`wait_event_interruptible_exclusive(pipe->wr_wait, pipe_writable(pipe))`。條件 `pipe_writable()`（第 421–428 行）是「不滿，**或者**已經沒有 reader 了」——後半句很重要，練習題會用到。

證據還帶出三個 H3 沒預料到的細節，每一個都是新的子問題：

1. **睡之前先放掉 `pipe->mutex`**（第 569 行）。這是必然的：抱著 mutex 睡，reader 就拿不到 mutex、永遠讀不了，雙方互等。
2. **睡之前，如果 pipe 原本是空的，先叫醒 reader**（第 570–571 行）。不然可能 reader 也在睡、等著「有資料」，writer 卻在睡、等著「有空間」。
3. **是 `_exclusive` 的等待**。多個 writer 同時睡在 `wr_wait` 上時，一次喚醒只叫醒其中一個（機制見 Ch 2 練習的 `__wake_up_common`）。為什麼？這是 Why 問題，步驟 5 回答。

### 步驟 4：誰叫醒 writer？——一個會失敗的假說

**假說 H4**：reader 每次成功讀到資料後，都會喚醒 `wr_wait` 上的 writer。

**預測**：`anon_pipe_read` 裡，讀完資料之後會無條件 `wake_up(&pipe->wr_wait)`。

**驗證**：

```
$ rg -n 'wr_wait' fs/pipe.c
399:		wake_up_interruptible_sync_poll(&pipe->wr_wait, EPOLLOUT | EPOLLWRNORM);
573:		wait_event_interruptible_exclusive(pipe->wr_wait, pipe_writable(pipe));
599:		wake_up_interruptible_sync_poll(&pipe->wr_wait, EPOLLOUT | EPOLLWRNORM);
678:		poll_wait(filp, &pipe->wr_wait, wait);
738:		wake_up_interruptible_all(&pipe->wr_wait);
822:		init_waitqueue_head(&pipe->wr_wait);
1083:	wait_event_interruptible(pipe->wr_wait, pipe_writable(pipe));
1229:		wake_up_interruptible(&pipe->wr_wait);
1356:	wake_up_interruptible(&pipe->wr_wait);
```

這一步用的是 Ch 2 第 6 節的建議：**在問題範圍內系統性**——把所有碰 `wr_wait` 的地方列出來，而不是只看碰巧讀到的那一處。九處，分類一下：第 822 行是初始化；第 573 行是步驟 3 已經看過的等待；第 678 行在 `pipe_poll`（給 poll/epoll 用，不是 `read(2)` 的路徑）；第 1083 行是 `pipe_wait_writable()`（給 splice 等其他路徑用的另一個等待點）；第 1229 行在 `fifo_open`（只有具名 FIFO 會走）；第 1356 行在 `pipe_resize_ring`（`F_SETPIPE_SZ` 擴大容量時）；第 738 行在 `pipe_release`（關檔，練習題會用到）；第 599 行是 writer 自己喚醒下一個 writer。跟主線「reader 讀完資料」有關的只有第 399 行：

```c
/* fs/pipe.c:398–399（v6.18） */
	if (wake_writer)
		wake_up_interruptible_sync_poll(&pipe->wr_wait, EPOLLOUT | EPOLLWRNORM);
```

**有條件**。`wake_writer` 在哪裡被設成 true？

```c
/* fs/pipe.c:359–362（v6.18），在 anon_pipe_read 的讀取迴圈裡 */
			if (!buf->len) {
				wake_writer |= pipe_full(head, tail, pipe->max_usage);
				tail = pipe_update_tail(pipe, buf, tail);
			}
```

**H4 被推翻。** reader 只在兩個條件**同時**成立時喚醒 writer：

- 某個 slot 被**整個讀完**（`!buf->len`），tail 前進、真的空出一個 slot；
- 而且讀之前 pipe 是**滿的**（`pipe_full(head, tail, ...)` 用的是推進 tail 之前的值）。

函式開頭的註解（第 284–291 行）也寫明了：*We only wake up writers if the pipe was full when we started reading and it is no longer full after reading to avoid unnecessary wakeups.*

**修正 H4**：reader 讀到某個 slot 被清空、且之前是滿的，才喚醒一個 writer。

修正後的假說推出一個**可以被實驗推翻**的預測：在滿的 pipe 上，reader 讀走 1 byte 時 writer **不會**醒（slot 還剩 4095 bytes）；讀走同一頁剩下的 4095 bytes 時，writer **會**醒。這是一個 Whether 問題，讀碼推不如跑一次：

```c
/* pipe_full.c — gcc -O2 -Wall -pthread -o pipe_full pipe_full.c */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

static int fd[2];
static atomic_int writer_done;

static void *writer(void *arg)
{
    (void)arg;
    char c = 'W';
    ssize_t n = write(fd[1], &c, 1);      /* 管子已滿：這裡應該睡著 */
    printf("  [writer] write() 回來了，回傳 %zd\n", n);
    atomic_store(&writer_done, 1);
    return NULL;
}

static void drain(size_t len)
{
    char buf[4096];
    ssize_t n = read(fd[0], buf, len);
    usleep(200 * 1000);                   /* 給 writer 200ms 醒來的機會 */
    printf("讀走 %4zd bytes 後，writer 醒了嗎？%s\n",
           n, atomic_load(&writer_done) ? "醒了" : "還在睡");
}

int main(void)
{
    char page[4096];
    memset(page, 'A', sizeof(page));
    if (pipe(fd) < 0) { perror("pipe"); return 1; }
    printf("F_GETPIPE_SZ = %d\n", fcntl(fd[1], F_GETPIPE_SZ));

    /* 1. 用 non-blocking 一頁一頁塞到滿 */
    fcntl(fd[1], F_SETFL, O_NONBLOCK);
    size_t total = 0;
    for (;;) {
        ssize_t n = write(fd[1], page, sizeof(page));
        if (n < 0) { printf("塞到 %zu bytes 時 write 回 -1，errno=%s\n",
                            total, strerrorname_np(errno)); break; }
        total += n;
    }
    /* 2. 切回 blocking，讓另一個 thread 寫 1 byte */
    fcntl(fd[1], F_SETFL, 0);
    pthread_t t;
    pthread_create(&t, NULL, writer, NULL);
    usleep(200 * 1000);
    printf("writer 已啟動 200ms，醒了嗎？%s\n",
           atomic_load(&writer_done) ? "醒了" : "還在睡");

    /* 3. 先讀 1 byte，再讀完同一頁剩下的 4095 bytes */
    drain(1);
    drain(4095);
    pthread_join(t, NULL);
    return 0;
}
```

WSL 上的實際輸出：

```
F_GETPIPE_SZ = 65536
塞到 65536 bytes 時 write 回 -1，errno=EAGAIN
writer 已啟動 200ms，醒了嗎？還在睡
讀走    1 bytes 後，writer 醒了嗎？還在睡
  [writer] write() 回來了，回傳 1
讀走 4095 bytes 後，writer 醒了嗎？醒了
```

前兩行確認了步驟 2 推出的容量 65536（16 × 4096）；O_NONBLOCK 寫到滿回 `EAGAIN`，對應第 551–555 行。後三行確認了修正後的 H4：讀 1 byte，writer 不動；讀完整頁，writer 醒來。

幾個實驗設計上的說明：

- 為什麼 writer 寫 1 byte 也會睡？因為 pipe 裡 16 個 slot 都是滿的 4096 bytes。第 477–498 行的「合併進最後一個 buffer」邏輯要求 `offset + chars <= PAGE_SIZE`，最後一頁已經滿了，合併不進去；接著主迴圈發現 `pipe_full`，只能睡。
- 200ms 的 `usleep` 是「給對方足夠時間」的粗略做法，不是嚴格的同步。在極度忙碌的機器上，「還在睡」這個判斷理論上可能因為 writer 還沒被排程到而誤判成立。要嚴格證明 writer 是被**那一次**讀喚醒的，需要看 kernel 內部——下一個實驗。

### 步驟 4b：用 ftrace 直接看喚醒

user-space 實驗看到的是**結果**。要看到**機制**——writer 確實睡在 `prepare_to_wait_event`、確實是 `anon_pipe_read` 的那一次喚醒叫醒它——就要到 kernel 裡看。在 QEMU guest 裡用 ftrace 的 function tracer，只追幾個關鍵函式（ftrace 的用法在 Ch 51 細講，這裡先看結果）：

```sh
#!/bin/sh
# guest.sh — 用 ~/code_reading/qemu/run-script.sh guest.sh pipe_full_static 執行
T=/sys/kernel/tracing
echo 'anon_pipe_write anon_pipe_read prepare_to_wait_event __wake_up_sync_key finish_wait' > $T/set_ftrace_filter
echo function > $T/current_tracer
echo 1 > $T/tracing_on
/bin/pipe_full_static
echo 0 > $T/tracing_on
grep pipe_full $T/trace | grep -v '^#' | awk '{ $3=""; $4=""; print }' | tail -25
```

`pipe_full_static` 是同一支程式用 `gcc -O2 -static -pthread` 編的版本（guest 裡沒有 libc）。`wait_event_interruptible_exclusive` 展開後呼叫的是 `prepare_to_wait_event()`（`include/linux/wait.h:310`，在 `___wait_event` 巨集裡），`wake_up_interruptible_sync_poll` 展開成 `__wake_up_sync_key()`（`include/linux/wait.h:246–247`），所以 filter 要寫這兩個名字，而不是巨集名。實際輸出（awk 拿掉了 flag 與時間戳欄位）：

```
pipe_full_stati-70 [000]   anon_pipe_write <-vfs_write
pipe_full_stati-70 [000]   __wake_up_sync_key <-anon_pipe_write
pipe_full_stati-70 [000]   anon_pipe_write <-vfs_write
...（同樣的 anon_pipe_write 再 14 行，共 17 次）
pipe_full_stati-70 [000]   anon_pipe_write <-vfs_write
pipe_full_stati-71 [001]   anon_pipe_write <-vfs_write
pipe_full_stati-71 [001]   prepare_to_wait_event <-anon_pipe_write
pipe_full_stati-70 [000]   anon_pipe_read <-vfs_read
pipe_full_stati-70 [000]   anon_pipe_read <-vfs_read
pipe_full_stati-70 [000]   __wake_up_sync_key <-anon_pipe_read
pipe_full_stati-71 [001]   finish_wait <-anon_pipe_write
pipe_full_stati-70 [000]   __wake_up_sync_key <-do_notify_parent
```

逐行對照：

- tid 70 是主執行緒，tid 71 是 writer 執行緒。
- 70 呼叫了 17 次 `anon_pipe_write`：16 次成功各塞一頁，第 17 次回 `EAGAIN`。只有**第一次**之後出現 `__wake_up_sync_key`——因為只有第一次寫入時 pipe 原本是空的（第 595 行 `if (was_empty || pipe->poll_usage)`）。
- 71 進 `anon_pipe_write`，接著 `prepare_to_wait_event`：**writer 睡著了**（步驟 3 的第 573 行）。
- 70 第一次 `anon_pipe_read`（讀 1 byte）：**後面沒有喚醒**。
- 70 第二次 `anon_pipe_read`（讀 4095 bytes）：緊接著 `__wake_up_sync_key <-anon_pipe_read`，然後 71 出現 `finish_wait`——**writer 被這一次讀叫醒，離開等待**。
- 最後一行的 `do_notify_parent` 是 process 結束時通知父行程，和 pipe 無關。

kernel 內部的證據和 user-space 實驗、讀碼推理三方一致。

### 步驟 5：為什麼要這樣設計？

主線問題已經回答了。但途中累積了兩個 Why：「為什麼只在 slot 清空且原本滿時才喚醒？」「為什麼用 exclusive 等待？」這兩個都是脈絡鴻溝，去 git 歷史找：

```
$ git log --format='%h %ad %an %s' --date=short -- fs/pipe.c | grep -i -E 'wake|exclusive'
aaec5a95d596 2025-01-02 Oleg Nesterov pipe_read: don't wake up the writer if the pipe is still full
e95aada4cb93 2023-12-01 Lukas Schauer pipe: wakeup wr_wait after setting max_usage
3b844826b6c6 2021-08-05 Linus Torvalds pipe: avoid unnecessary EPOLLET wakeups under normal loads
3a34b13a88ca 2021-07-30 Linus Torvalds pipe: make pipe writes always wake up readers
472e5b056f00 2020-10-01 Linus Torvalds pipe: remove pipe_wait() and fix wakeup race with splice
6551d5c56eb0 2020-02-18 Linus Torvalds pipe: make sure to wake up everybody when the last reader/writer closes
0ddad21d3e99 2019-12-09 Linus Torvalds pipe: use exclusive waits when reading or writing
f467a6a66419 2019-12-07 Linus Torvalds pipe: fix and clarify pipe read wakeup logic
1b6b26ae7053 2019-12-07 Linus Torvalds pipe: fix and clarify pipe write wakeup logic
...（更早的 2019、2013、2011、2007 年 commit 略）
```

（`git log -- <path>` 用到的只有 commit 和 tree，在 blobless clone 上也是瞬間完成，見 Ch 0。）

- **`aaec5a95d596`（v6.14）**：Oleg Nesterov 指出，如果讀完之後 pipe 仍然是滿的，喚醒 writer 毫無意義——writer 醒來檢查 `pipe_writable()`，發現還是滿的，又睡回去。白白多一次 context switch。這個 commit 讓 reader 只在「真的釋放了一個 slot、且之前是滿的」時才喚醒。這正是步驟 4 推翻 H4 的那段程式碼的由來。
- **`0ddad21d3e99`（v5.6）**：Linus Torvalds 把 pipe 的讀寫兩邊改成分開的 wait queue 加上 exclusive 等待，理由是避免 **thundering herd**（一次喚醒叫醒一大群，結果只有一個能做事，其他全部白醒）。commit 訊息舉的例子很具體：**GNU make 的 jobserver** 用一個 pipe 當「令牌桶」——每個 job 開始前從 pipe 讀一個 byte（拿令牌），結束時寫回一個 byte（還令牌）。`make -j64` 時可能有幾十個 process 同時睡在 pipe 上等令牌，而每次只有一個令牌被歸還。
- 順帶一提 **`3a34b13a88ca`（v5.14）**：一個「設計得更乾淨反而出事」的故事。2019 年的重寫讓 writer 只在 pipe 原本為空時才喚醒 reader，結果某些 Android 函式庫誤用了 epoll 的 edge-triggered 語意、依賴「每次寫都會觸發」，升級到 5.10 之後全部壞掉。依照 kernel「不能讓 user-space 壞掉」的規則，Linus 加回了多餘的喚醒。結果一週後又發現這讓 hackbench 在大機器上明顯退步，於是 **`3b844826b6c6`（同樣 v5.14）** 改成折衷：只有被 epoll 用過的 pipe 才做那個多餘的喚醒，用一個新欄位 `poll_usage` 標記。今天第 595 行條件裡的 `pipe->poll_usage`，就是這段來回拉鋸留下的痕跡。（如果你不知道是哪個 commit 加的，可以用 `timeout 300 git log -S poll_usage --oneline v5.10..v5.15 -- fs/pipe.c` 找；限定路徑和版本範圍，在 blobless clone 上才不會跑很久，見 Ch 0。）

這三個 commit 的內容**沒有一個字**寫在 `anon_pipe_read` 或 `anon_pipe_write` 的程式碼裡。

### 這次追蹤的完整紀錄

| # | 假說 | 預測 | 證據 | 判定 |
|---|---|---|---|---|
| H1 | write 進到 `pipe_write()` | `rg` 找得到定義 | 找不到；只有 `anon_pipe_write`、`fifo_pipe_write`；`pipeanon_fops.write_iter`；`wchan` 顯示 `anon_pipe_write` | **推翻並修正** |
| H2 | 環狀緩衝區，`pipe_full` 比較已用 slot 與容量 | 有 `pipe_full()`；容量 16 頁 = 65536 | `pipe_fs_i.h:205`、`PIPE_DEF_BUFFERS=16`；實驗 `F_GETPIPE_SZ=65536` | 成立 |
| H3 | 滿了就在 `wr_wait` 上睡 | 有 `wait_event*(wr_wait, ...)` | `pipe.c:573`；ftrace 看到 `prepare_to_wait_event <-anon_pipe_write` | 成立，並發現三個新細節 |
| H4 | reader 每次讀都喚醒 writer | 無條件 `wake_up(wr_wait)` | `pipe.c:359–360、398–399` 有條件；實驗：讀 1 byte 不醒、讀完整頁才醒；ftrace 確認 | **推翻並修正** |
| Why | 為什麼這樣設計 | 歷史裡有理由 | `aaec5a95d596`、`0ddad21d3e99`、`3a34b13a88ca` | 找到 |

四個假說推翻了兩個。這不是失敗，**被推翻的假說正是這次閱讀最有價值的部分**：H1 讓你知道舊知識過期了，H4 讓你發現了一個精細的喚醒條件和它背後的歷史。如果你只是從頭讀到尾，你不會在第 360 行停下來，因為你沒有一個預期讓它顯得特別。

而整個過程讀了多少程式碼？`anon_pipe_write` 約 170 行裡的 40 行、`anon_pipe_read` 裡的十幾行、`pipe_fs_i.h` 的兩個 inline 函式、一個 ops table。`fs/pipe.c` 的 1553 行裡，九成以上沒碰——**而且不需要碰**。

## 驗證手段清單

| 手段 | 能回答什麼 | 成本 | 證據強度 | 限制 |
|---|---|---|---|---|
| **讀碼** | What、How、When | 低到中 | 中：你的閱讀可能出錯 | 間接處會斷；慣用法會誤讀 |
| **grep / rg** | Where；「這個欄位在哪裡被讀寫」 | 很低 | 中：找得到的一定存在，找不到不代表不存在（巨集、token pasting） | 純文字，不懂語意 |
| **cscope / clangd** | Where；呼叫者與被呼叫者 | 低（設定好之後） | 中高 | 函式指標仍然追不到執行期的值 |
| **git log / blame** | Why；「什麼時候、誰、為什麼加的」 | 低到中（blobless 第一次慢） | 高：commit 訊息是作者自己寫的 | 不是每個 commit 都寫清楚理由 |
| **lore.kernel.org** | Why；被拒絕的替代方案 | 中 | 高 | 討論可能很長、分散在多個版本的 patch |
| **user-space 實驗** | Whether；可觀察的行為 | 低到中 | 高（對可觀察行為） | 看不到 kernel 內部；時序類實驗不嚴格 |
| **`/proc`**（`wchan`、`stack`、`status`） | Where（睡在哪） | 很低 | 高 | 部分需要 root（`/proc/<pid>/stack`） |
| **ftrace / bpftrace** | How、Where、When（執行期） | 中（需要 root 或 VM） | 很高：直接看 kernel 實際走的路 | 要知道追哪些函式；inline 掉的函式追不到 |
| **QEMU + gdb** | 任何事，到單一指令的粒度 | 高 | 很高 | 慢；改變時序 |

經驗法則：**用最便宜的手段先試，證據不夠強再升級**。這次實例的升級路徑是：讀碼 → grep → `/proc` → user-space 實驗 → git log → ftrace，每一步只在前一步留下疑問時才往上走。

## 問題清單模板

每次開始讀一段陌生程式碼，先開一個這樣的筆記檔（Ch 7 會把它擴充成完整的讀碼筆記）：

```markdown
# 讀碼紀錄：<主題>（v6.18）

## 大目標
<一句話，可以模糊：「我想懂 pipe 的阻塞行為」>

## 問題樹
- [ ] Q1：<具體問題>（類型：what/how/why/where/when/whether）
  - [ ] Q1.1：<子問題>
- [ ] Q2：...

## 假說紀錄
### H1（回答 Q1）
- 假說：
- 預測：如果假說對，我應該在 ____ 看到 ____
- 驗證手段：讀碼 / rg / git log / 實驗 / ftrace
- 證據：<檔案:行號（v6.18）、指令輸出、commit hash>
- 判定：成立 / 推翻 / 修正為 ____

## 黑箱清單（暫時不追的東西）
- <函式名>：我假設它 <做什麼>，若之後有矛盾再回來

## 冒出來的新問題
- <讀的過程中浮現、但不在主線上的問題>

## 結論
<用自己的話、不含識別字地回答大目標。寫不出來表示還沒懂。>
```

模板裡最常被省略、也最重要的兩欄是**預測**和**黑箱清單**。沒寫預測，假說就無法被推翻，你會不知不覺地把任何證據都解讀成「符合假說」。沒有黑箱清單，你會在每個不相關的函式上分心。

## 對比與取捨

| 讀法 | 怎麼做 | 適合 | 不適合 |
|---|---|---|---|
| **線性閱讀** | 從檔案第一行讀到最後一行 | 很短的檔案（< 200 行）；要做 code review 的 diff | 任何超過幾百行的程式碼；kernel |
| **探索式瀏覽** | 沒有特定問題，到處點點看 | 剛接觸一個專案、想建立第一印象（給自己 30 分鐘上限） | 想回答具體問題；想真正理解某個機制 |
| **提問驅動** | 帶著具體問題，只讀相關路徑 | 幾乎所有讀碼任務 | 問題本身問錯時會走錯路（需要定期回頭檢查問題樹） |

探索式瀏覽不是壞事——Ch 5 會講怎麼有效率地建立第一印象。它的問題是**沒有終點**。給它一個時間上限，時間到了就從瀏覽中整理出三個具體問題，切換到提問驅動。

## 踩雷集錦

1. **假說沒有預測**：「pipe 有某種等待機制」不是可驗證的假說，任何證據都符合它。要逼自己寫出「如果對，我應該在哪裡看到什麼」。
2. **只找支持假說的證據**（confirmation bias）：H4 如果只讀到第 398 行 `wake_up_interruptible_sync_poll(&pipe->wr_wait, ...)` 就停，你會以為假說成立。系統性地列出所有讀寫 `wr_wait` 的地方、追到 `wake_writer` 在哪裡被設定，才看得到條件。
3. **相信記憶中的函式名**：H1 的失敗不是特例。kernel 的函式會改名、拆分、合併。你的記憶、舊部落格、LLM 的訓練資料，都可能停在某個舊版本。**永遠對眼前的 tree 驗證**。
4. **問題太大**：「pipe 怎麼運作」會讓你回到從頭讀的老路。問題大到你說不出「讀到哪裡可以停」，就要再拆。
5. **主線被支線吃掉**：「為什麼分成 `anon_pipe_write` 和 `fifo_pipe_write`」很有趣，但它不在主線上。本章花了兩行指令就回答它，是因為答案剛好很便宜。如果它需要讀一小時，正確做法是記進「冒出來的新問題」，回到主線。

## 進階：再往深一層

- **多重工作假說**：地質學家 T. C. Chamberlin 在 1890 年的〈The Method of Multiple Working Hypotheses〉主張同時保持多個互相競爭的假說，而不是愛上第一個。讀碼時的對應做法：步驟 4 如果同時提出「H4a：每次讀都喚醒」「H4b：只在空出 slot 時喚醒」「H4c：只在從滿變不滿時喚醒」，你會主動去找能**區分**它們的證據（讀 1 byte vs 讀 4096 bytes 的實驗正好能區分 H4a 和 H4b/H4c），而不是找到一條符合的證據就停。
- **把假說寫成可執行的測試**：本章的 `pipe_full.c` 本質上是把一個假說寫成程式。更進一步，可以寫成 kernel selftest 的形式（`tools/testing/selftests/`），讓它成為 kernel 回歸測試的一部分。Ch 40 會講怎麼把現有的 selftest 當成規格來讀——它們就是別人寫好的「已驗證假說」。
- **用動態追蹤產生假說，而不只是驗證**：本章是先有假說再用 ftrace 驗證。反過來也行：對一個完全陌生的路徑，先用 `function_graph` tracer 錄下完整的呼叫樹，再從呼叫樹裡挑出可疑的函式提出假說。這對間接鴻溝特別有效，Ch 51 示範。

## 動手練習

**問題**：pipe 已經滿了，一個 writer 睡在 `anon_pipe_write` 裡。這時唯一的 reader 把 read end 關掉了。writer 會怎樣？

請用本章的迴圈作答：寫下假說和預測，在 v6.18 tree 上找證據，最後用一個 user-space 實驗驗證。提示：`pipe_writable()` 的條件有兩半；關檔時呼叫的是 `pipeanon_fops` 的哪個成員？

<details>
<summary>參考解答</summary>

**假說**：writer 會被叫醒，然後因為沒有 reader 而收到 `SIGPIPE`、`write` 回傳 `-EPIPE`。

**預測**：
1. 關閉檔案的路徑會喚醒 `wr_wait`。
2. writer 醒來後的檢查會發現 `pipe->readers == 0`。

**證據**：

- `pipeanon_fops.release = pipe_release`（`fs/pipe.c:1263`）。`pipe_release()`（第 725–746 行）把 `pipe->readers` 減一，然後：

  ```c
  /* fs/pipe.c:735–741（v6.18） */
  	/* Was that the last reader or writer, but not the other side? */
  	if (!pipe->readers != !pipe->writers) {
  		wake_up_interruptible_all(&pipe->rd_wait);
  		wake_up_interruptible_all(&pipe->wr_wait);
  		...
  	}
  ```

  注意是 `_all`：不管 exclusive 與否，全部叫醒。這合理：沒有 reader 之後，**每一個** writer 都需要知道。（為什麼 `wake_up_interruptible_all` 能叫醒 exclusive 等待者？因為它傳給 `__wake_up_common` 的 `nr_exclusive` 是 0，而第 111 行的 `!--nr_exclusive` 在 0 減一之後永遠不會等於 0。這段歷史在 commit `6551d5c56eb0`「pipe: make sure to wake up everybody when the last reader/writer closes」。）
- writer 等待的條件 `pipe_writable()`（第 421–428 行）是 `!pipe_full(...) || !READ_ONCE(pipe->readers)`，後半成立，`wait_event` 返回。
- 回到迴圈開頭第 501–507 行：`!pipe->readers` → 若沒有設 `IOCB_NOSIGNAL` 就 `send_sig(SIGPIPE, current, 0)`，`ret = -EPIPE`。

**實驗**（WSL 上實際執行）：

```c
/* pipe_close.c — gcc -O2 -Wall -pthread -o pipe_close pipe_close.c */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

static int fd[2];

static void *writer(void *arg)
{
    (void)arg;
    char c = 'W';
    ssize_t n = write(fd[1], &c, 1);      /* 管子已滿：睡著 */
    printf("[writer] write() 回 %zd，errno=%s\n", n, n < 0 ? strerrorname_np(errno) : "-");
    return NULL;
}

int main(void)
{
    char page[4096] = {0};
    signal(SIGPIPE, SIG_IGN);             /* 不然整個 process 會被 SIGPIPE 殺掉 */
    if (pipe(fd) < 0) return 1;
    fcntl(fd[1], F_SETFL, O_NONBLOCK);
    while (write(fd[1], page, sizeof(page)) > 0)
        ;
    fcntl(fd[1], F_SETFL, 0);
    pthread_t t;
    pthread_create(&t, NULL, writer, NULL);
    usleep(200 * 1000);
    printf("關掉唯一的 read end\n");
    close(fd[0]);
    pthread_join(t, NULL);
    return 0;
}
```

```
關掉唯一的 read end
[writer] write() 回 -1，errno=EPIPE
```

假說成立。延伸思考：如果不 `signal(SIGPIPE, SIG_IGN)`，會發生什麼事？（`SIGPIPE` 的預設動作是終止 process，所以你只會看到第一行。這就是為什麼 `yes | head -1` 裡的 `yes` 會安靜地結束。）

</details>

## 本章重點整理

- 「我想懂 X」不是可以開始的問題。拆成**可回答、可驗證、有終點、範圍小**的葉子問題，問題樹邊讀邊長。
- 問題有類型：what、how、why、where、when、whether。Why 的答案常不在程式碼裡（去翻 git），Whether 最適合用實驗回答。
- 迴圈是**假說 → 預測 → 驗證 → 修正**。預測是關鍵：它讓假說能被推翻。
- pipe 實例：writer 在 `anon_pipe_write` 第 573 行睡在 `wr_wait` 上；reader 只在「某個 slot 被讀空、且之前是滿的」時才在第 399 行叫醒它。四個假說推翻兩個，被推翻的正是收穫最大的地方。
- 驗證手段由便宜到昂貴：讀碼、grep、`/proc`、user-space 實驗、git log、ftrace、gdb。證據不夠強才升級。

## 自我檢核

- [ ] 不看本章，能把「我想懂 eventfd」拆成至少五個滿足四條件的葉子問題
- [ ] 能說明「假說」和「預測」的差別，並舉一個沒有預測、因此無法被推翻的假說
- [ ] 能憑記憶說出 pipe 的 writer 在什麼條件下會被 reader 叫醒，並說出一個能區分「每次讀都喚醒」和「slot 清空才喚醒」的實驗
- [ ] 如果有人跟你說「pipe 的寫入函式是 `pipe_write()`」，你會怎麼回應？用什麼指令在十秒內確認
- [ ] 能說出 GNU make jobserver 和 pipe 的 exclusive 等待有什麼關係，以及你是從哪裡知道的
- [ ] 想一個你最近想讀懂的程式（任何專案），寫出它的問題樹和第一個假說；你的第一個驗證手段會是什麼

## 延伸閱讀

### 論文

- **Cognitive processes in program comprehension** — Stanley Letovsky（Empirical Studies of Programmers 第一屆研討會，1986；期刊版刊於 Journal of Systems and Software 7(4)，1987）
  - **核心貢獻**：用 think-aloud 觀察專業程式設計師，把理解過程描述為一連串的提問（why/how/what 等）與猜想
  - **讀哪裡**：提問與猜想的分類，以及受試者出聲思考的逐字範例
  - **和本章的關聯**：本章「問題的類型」一節的理論來源；你會在逐字紀錄裡看到熟手怎麼自然地做假說驗證
- **[Towards a theory of the comprehension of computer programs](https://doi.org/10.1016/S0020-7373(83)80031-5)** — Ruven Brooks，International Journal of Man-Machine Studies 18(6)（1983）
  - **核心貢獻**：理解是假說驅動的；主要假說往下拆成可驗證的子假說
  - **讀哪裡**：假說的產生與分解那部分
  - **和本章的關聯**：本章的問題樹就是 Brooks 假說分解的操作版
- **The Method of Multiple Working Hypotheses** — T. C. Chamberlin（Science，1890；1965 年 Science 重刊）
  - **核心貢獻**：主張同時維持多個互相競爭的假說，以對抗「愛上自己第一個解釋」的偏誤
  - **讀哪裡**：整篇（短）；雖然談的是地質學，論證完全適用於讀碼與除錯
  - **和本章的關聯**：「進階」一節；避免踩雷第 2 條的 confirmation bias

### 官方文件

- **[pipe(7) man page](https://man7.org/linux/man-pages/man7/pipe.7.html)**
  - **讀哪裡**：〈Pipe capacity〉與〈I/O on pipes and FIFOs〉兩節
  - **能學到什麼**：65536 的容量、`F_GETPIPE_SZ` / `F_SETPIPE_SZ`、`SIGPIPE` 與 `EPIPE` 的 user-space 契約——本章每個實驗的預期行為都寫在這裡
  - **前提知識**：會用 `pipe(2)`
- **[ftrace（docs.kernel.org）](https://docs.kernel.org/trace/ftrace.html)**（tree 內：`Documentation/trace/ftrace.rst`）
  - **讀哪裡**：`set_ftrace_filter` 與 function tracer 的段落
  - **能學到什麼**：步驟 4b 那個 guest 腳本每一行在做什麼
  - **前提知識**：知道 tracefs 掛在 `/sys/kernel/tracing`；完整用法見 Ch 51

### 原始資料：本章引用的 commit

- **`0ddad21d3e99` pipe: use exclusive waits when reading or writing**（Linus Torvalds，2019，v5.6）
  - **讀哪裡**：`git show 0ddad21d3e99` 的 commit 訊息，包含 GNU make jobserver 的說明和一個 64 個 process 傳遞令牌的測試程式
  - **為什麼值得讀**：這是「Why 問題的答案在 commit 訊息裡」最好的範例之一，而且訊息本身就是一個可以跑的實驗
- **`aaec5a95d596` pipe_read: don't wake up the writer if the pipe is still full**（Oleg Nesterov，2025，v6.14）
  - **讀哪裡**：commit 訊息與 `Link:` 指向的 lore 討論串
  - **為什麼值得讀**：短，邏輯清楚，直接對應本章被推翻的 H4

你已經會帶著問題讀了。但即使問題夠具體，路徑上還是會遇到大量跟問題無關的程式碼——`watch_queue`、`splice`、計帳邏輯。下一章講怎麼合法地跳過它們：黑箱、略讀，以及接受只懂 80%。

→ [Ch 4 選擇性閱讀：黑箱、略讀，以及接受只懂 80%](./04-selective-reading.md)
