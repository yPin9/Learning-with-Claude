# Ch 1 — 為什麼你看不懂：五道鴻溝的自我診斷

> **目標**：把「我看不懂這段 code」拆成五種性質完全不同的卡關——慣用法、間接、領域、脈絡、目標——能對一段沒看過的 kernel 函式逐行指出自己卡在哪一道，並知道每一道該用什麼方法、在本課哪幾章處理。
> **鴻溝**：本章定義全部五道鴻溝，並用一段真實的 v6.18 程式碼示範診斷。之後每一章都用這套語言。

## 為什麼需要這個？

很多人描述自己讀 kernel 的經驗，說法幾乎一樣：「C 我會寫，每個字我都認得，但隨便抓一段 kernel code，就是看不懂。」

這句話裡藏著一個錯誤的假設：**「看不懂」是一個問題**。它其實是五個問題疊在一起，而且五個問題的解法彼此毫不相干。卡在「`wait_event_interruptible_locked_irq` 是什麼」的人需要的是查巨集定義；卡在「這個函式到底是誰呼叫的」的人需要的是工具；卡在「`IOCB_NOWAIT` 為什麼存在」的人需要的是翻 git 歷史。如果你不知道自己卡在哪一道，你會用錯方法——對著一個脈絡問題反覆重讀程式碼，讀十遍也讀不出那三行是在修一個 2022 年的 deadlock，因為答案根本不在程式碼裡。

所以這門課的第一件事不是教你讀，而是教你**診斷**。

## 先回到起點：為什麼要讀碼

Spinellis 在《Code Reading》第 1 章開頭先問了一個看似多餘的問題：我們為什麼讀別人的程式碼？他列了六種動機，用今天的 kernel 世界重講一遍：

| 動機 | Spinellis 的意思（用我們的話） | 在 kernel 世界長什麼樣 |
|---|---|---|
| 程式碼作為文學（literature） | 讀好作品來學寫作，就像作家讀經典 | 讀 `lib/rbtree.c`、`include/linux/list.h` 學資料結構怎麼寫到極致 |
| 程式碼作為範本（exemplar） | 想做某件事，找一個已經做過的實作照著學 | 要寫一個字元裝置 driver，先讀一個同類 driver 的 probe 函式 |
| 維護（maintenance） | 修 bug、改功能之前，得先讀懂現有的東西 | 追一個 regression，從 oops 的 call trace 一路往回讀 |
| 演進（evolution） | 系統要長新功能，得先知道它現在的骨架 | 想給 pipe 加一個新的 `fcntl` 選項，先讀懂 `pipe_fcntl()` 與 ring 的設計 |
| 重用（reuse） | 判斷一段既有程式碼能不能拿來用、要怎麼用 | 決定你的子系統該用 `kfifo`、`xarray` 還是自己寫 ring buffer |
| 審查（inspection） | 為了找錯而讀：code review、安全稽核 | 在 mailing list 上 review patch；找漏洞 |

這張表的重點不在分類本身，而在 Spinellis 隱含的主張：**讀碼的方式取決於你為什麼讀**。為了學寫作而讀，你會慢慢品每一行；為了修 bug 而讀，你會跳過九成的程式碼直奔可疑路徑；為了審查而讀，你會對每一個錯誤處理分支起疑。沒有「正確的讀法」，只有「對這個目的正確的讀法」。這個主張會在第五道鴻溝（目標鴻溝）再出現一次。

## 先建立直覺：一份看不懂的手術紀錄

想像你是一位會讀中文、修過基礎生物的工程師，拿到一份外科手術紀錄，想知道「這台手術做了什麼」。你可能會卡在五種地方：

```
                     「我看不懂這份手術紀錄」
                                │
     ┌───────────────┬──────────┼────────────┬────────────────┐
     ▼               ▼          ▼            ▼                ▼
「s/p LAC        「依標準流程   「腹腔鏡下   「為什麼不照     「我到底想
 POD#2」         SOP-7 處理」   分離膽囊管」 慣例開腹？」     知道什麼？」
 每個字母都      流程在另一份   不懂解剖     理由寫在三年     從第一頁
 認得，整句不懂  文件，斷了     學           前的會診紀錄     讀到最後
     │               │          │            │                │
  慣用法鴻溝      間接鴻溝    領域鴻溝     脈絡鴻溝         目標鴻溝
```

五種卡法的共同點是「看不懂」，不同點是**缺的東西在哪裡**：縮寫的意思在字典裡；SOP-7 在另一份文件裡；解剖學在教科書裡；開刀的理由在別人的會診紀錄裡；而「你想知道什麼」在你自己腦子裡。

> 這個類比的限制：手術紀錄不會「執行」，程式碼會。所以程式碼多了一條手術紀錄沒有的出路——**讓它跑起來看**（動態追蹤、實驗）。這條出路對間接鴻溝和領域鴻溝特別有用，類比裡看不到。

## 五道鴻溝的正式定義

| # | 鴻溝 | 一句話症狀 | 缺的東西在哪裡 | 解法 | 本課主要章節 |
|---|---|---|---|---|---|
| 1 | **慣用法鴻溝** | 每個字都認得、整行看不懂 | 巨集定義、慣例、社群共識 | 查、認得 pattern | Part 2（Ch 8–14）、Part 3（Ch 15–22）、Ch 50 |
| 2 | **間接鴻溝** | 追到 `f->f_op->read_iter()` 就斷了 | 賦值點、註冊點、執行期的值 | 工具、找賦值點、動態追蹤 | Ch 28–29、Ch 32–34、Part 8（Ch 47–52） |
| 3 | **領域鴻溝** | 語法懂、不知道在幹嘛 | 作業系統與子系統的背景知識 | 補背景知識 | Part 4（Ch 23–27）、Part 7（Ch 41–46） |
| 4 | **脈絡鴻溝** | 不知道為什麼要寫這麼怪 | commit 訊息、mailing list、bug 報告 | git／mailing list 考古 | Part 6（Ch 35–40） |
| 5 | **目標鴻溝** | 沒帶問題，從頭硬讀 | 你自己的問題 | 提問驅動 | Ch 3–5、Ch 7 |

下面逐一展開。

### 1. 慣用法鴻溝（idiom gap）

**定義**：程式碼用了某個社群約定俗成的寫法——巨集、命名慣例、控制流套路——你認得每一個 token，卻不認得它們組合起來的「詞」。

**典型觸發**：`container_of(ptr, type, member)`、`ERR_PTR(-ENOMEM)` / `IS_ERR()`、`do { ... } while (0)`、`goto out_free;` 階梯、`likely()` / `unlikely()`、`-ERESTARTSYS`、`READ_ONCE()`、`__user`。

**症狀**：你能逐字念出這一行，但說不出它的意圖。或者更糟：你以為你懂了，其實理解錯了（例如以為 `wait_event_*_locked()` 睡覺時一直抱著 lock）。

**自我檢測問題**：
- 這一行裡有沒有我說不出「它展開後是什麼」的巨集？
- 這個名字（`__foo`、`foo_locked`、`do_foo`）的前綴、後綴在這個專案裡有沒有約定意義？
- 我能不能用一句白話說出這一行的**意圖**，而不是逐字翻譯？

**為什麼會有這道鴻溝**：慣用法是專案內部的壓縮語言。熟手看到 `goto out_unlock` 一眼就知道「錯誤路徑，會先解鎖再返回」，因為他看過一千次；新手要讀完整個錯誤處理階梯才能拼出同樣的資訊。好消息是慣用法的數量是**有限的**——kernel 常見的大概幾百個，認得之後永遠有效。

### 2. 間接鴻溝（indirection gap）

**定義**：控制流或資料流經過了一層執行期才決定的間接——函式指標、ops table、callback、notifier 鏈、巨集產生的函式名——靜態閱讀追到這裡就斷了。

**典型觸發**：`file->f_op->read_iter(...)`、`curr->func(curr, mode, wake_flags, key)`、`SYSCALL_DEFINE3(write, ...)`（真正的符號名不在原始碼裡）、`module_init(foo_init)`、`INIT_WORK(&w, handler)`。

**症狀**：你想知道「這裡呼叫的是誰」或「這個函式是誰呼叫的」，`grep` 函式名卻只找到一處賦值、零處呼叫；或者找到兩百個候選人，不知道哪個才是。

**自我檢測問題**：
- 我追丟的那一跳，是「直接呼叫」還是「透過指標」？
- 這個函式是 `static` 的嗎？如果是，而且檔案裡沒有人直接呼叫它，它一定是被當成指標存進某個 struct 了。
- 我能不能讓程式跑起來，直接看實際走了哪條路？

**為什麼會有這道鴻溝**：kernel 用 C 實作物件導向與多型（Ch 42）。VFS 不知道底下是 ext4、pipe 還是 eventfd，它只知道呼叫 `f_op->read_iter`。這個設計讓 kernel 能擴充，代價是**呼叫關係被延後到執行期才決定**，靜態文本裡看不出來。Letovsky 與 Soloway 在 1986 年就指出，這類「分散在多處的計畫」（delocalized plans）是理解錯誤的主要來源之一（Ch 2 細講）。

### 3. 領域鴻溝（domain gap）

**定義**：語法和控制流都看得懂，但不知道程式碼在操作的那些東西是什麼、為什麼要這樣操作，因為缺少問題領域的知識。

**典型觸發**：page cache、`struct kiocb` / `iov_iter`、wait queue、RCU grace period、TLB shootdown、softirq、NAPI、memory cgroup 計帳（`GFP_KERNEL_ACCOUNT`）。

**症狀**：你能把一段程式碼逐行翻成中文，但翻完的中文你也看不懂。「把 head 加一、把 page 放進 buf、喚醒 rd_wait 上的人」——每一句都懂，合起來不知道為什麼。

**自我檢測問題**：
- 這段程式碼裡的**名詞**（struct、欄位名）我能說出它在現實中代表什麼嗎？
- 如果把這段程式碼刪掉，系統會出什麼問題？我說得出來嗎？
- 這個子系統有沒有一份 `Documentation/` 或 LWN 文章，我還沒讀？

**為什麼會有這道鴻溝**：程式碼是領域知識的**實作**，不是領域知識的**解說**。kernel 開發者假設讀者知道什麼是 page cache，就像醫生的紀錄假設讀者懂解剖學。這道鴻溝沒有捷徑，只能補——但可以補得有效率（Ch 46）。

### 4. 脈絡鴻溝（context gap）

**定義**：你看懂了程式碼做什麼，但不懂它**為什麼**這樣寫——為什麼多了這一個檢查、為什麼不用更直接的寫法、為什麼這兩行順序不能換。答案不在程式碼裡，在它的歷史裡。

**典型觸發**：看起來多餘的旗標設定、看起來多餘的 barrier、一個奇怪的特例分支、一段註解寫著「don't remove this」。

**症狀**：你心想「這行刪掉應該也沒差吧」或「這寫得好怪，為什麼不……」。這個念頭本身就是脈絡鴻溝的警報。

**自我檢測問題**：
- 這一行是哪個 commit 加進來的？commit 訊息怎麼說？（`git blame`、`git log -L`）
- 有沒有 `Fixes:` tag 或 `Link:` 指向 mailing list 討論？
- 如果我把它「簡化」掉，會不會重新引入某個已經被修過的 bug？

**為什麼會有這道鴻溝**：每一行 kernel 程式碼都是一次協商的結果——效能、正確性、相容性、某個 Android 函式庫的錯誤用法（Ch 3 會看到真實例子）。協商過程寫在 commit 訊息與 mailing list，**不在**程式碼註解裡。kernel 的好處是這些紀錄幾乎全部公開，壞處是你得自己去挖。

### 5. 目標鴻溝（goal gap）

**定義**：你沒有帶著具體問題，而是打開檔案從第一行讀起，期待讀完就懂了。

**典型觸發**：「我想懂 pipe」「我想讀懂 scheduler」「隨便抓一段來讀讀看」。

**症狀**：讀了二十分鐘，記不得前面讀過什麼；每個函式都很重要又都不重要；不知道什麼時候可以停；讀完之後說不出自己學到什麼。

**自我檢測問題**：
- 我現在讀這段 code，是為了回答哪一個問題？能寫成一句以問號結尾的話嗎？
- 讀到哪裡，我就可以說「這個問題答完了」？
- 這個檔案裡，哪些部分和我的問題**無關**、可以先跳過？

**為什麼會有這道鴻溝**：人類的工作記憶很小（Ch 2 講 chunking 與認知負荷）。沒有問題當錨點，讀到的資訊沒有地方掛，讀完就散。熟手讀 kernel 從來不是從頭讀，他們帶著問題跳著讀——連 maintainer 讀自己子系統以外的程式碼也是這樣。

**目標鴻溝是最上游的一道**。它不擋住你看懂某一行，但它決定你要不要看那一行。先填平它，其他四道鴻溝的工作量會縮小一個數量級：你只要為**跟問題有關**的那幾行處理慣用法、間接、領域與脈絡。

## 示範診斷：逐行解剖 `eventfd_read()`

光有定義不夠。我們拿一段真實的 v6.18 程式碼，逐行標出每一處卡在哪一道鴻溝。

先交代背景，讓領域鴻溝不至於從第一行就把你擋住：**eventfd** 是一個只裝著一個 64 位元計數器的檔案描述子。`write(2)` 把一個值加到計數器上，`read(2)` 把計數器讀出來並歸零（或在 semaphore 模式下只減一）；計數器為 0 時 `read` 會阻塞。它常被當成輕量的事件通知機制，例如 io_uring、KVM、vhost 用它通知 user-space「有事發生了」。

下面是 eventfd 的 read 實作，原封不動從 v6.18 複製：

```c
/* fs/eventfd.c:214–245（v6.18） */
static ssize_t eventfd_read(struct kiocb *iocb, struct iov_iter *to)
{
	struct file *file = iocb->ki_filp;
	struct eventfd_ctx *ctx = file->private_data;
	__u64 ucnt = 0;

	if (iov_iter_count(to) < sizeof(ucnt))
		return -EINVAL;
	spin_lock_irq(&ctx->wqh.lock);
	if (!ctx->count) {
		if ((file->f_flags & O_NONBLOCK) ||
		    (iocb->ki_flags & IOCB_NOWAIT)) {
			spin_unlock_irq(&ctx->wqh.lock);
			return -EAGAIN;
		}

		if (wait_event_interruptible_locked_irq(ctx->wqh, ctx->count)) {
			spin_unlock_irq(&ctx->wqh.lock);
			return -ERESTARTSYS;
		}
	}
	eventfd_ctx_do_read(ctx, &ucnt);
	current->in_eventfd = 1;
	if (waitqueue_active(&ctx->wqh))
		wake_up_locked_poll(&ctx->wqh, EPOLLOUT);
	current->in_eventfd = 0;
	spin_unlock_irq(&ctx->wqh.lock);
	if (unlikely(copy_to_iter(&ucnt, sizeof(ucnt), to) != sizeof(ucnt)))
		return -EFAULT;

	return sizeof(ucnt);
}
```

32 行，沒有一個你不認得的 C 語法。現在逐行診斷。

### 診斷表

| 行 | 程式碼 | 卡在哪裡 | 鴻溝 |
|---|---|---|---|
| 214 | `static ssize_t eventfd_read(...)` | 檔案裡沒有任何地方呼叫它，它怎麼被執行的？ | **間接** |
| 214 | `struct kiocb *iocb, struct iov_iter *to` | 為什麼 read 不是 `(char __user *buf, size_t len)`？這兩個 struct 是什麼？ | **領域** |
| 217 | `file->private_data` | 這個 `void *` 什麼時候被設成 `eventfd_ctx`？ | **慣用法** + **間接** |
| 220 | `iov_iter_count(to) < sizeof(ucnt)` | 為什麼 buffer 小於 8 bytes 就是錯？ | **領域**（eventfd 的 ABI） |
| 222 | `spin_lock_irq(&ctx->wqh.lock)` | 為什麼用 wait queue 自己的 lock 保護 `count`？為什麼要 `_irq`？ | **慣用法** + **脈絡** |
| 225 | `iocb->ki_flags & IOCB_NOWAIT` | `O_NONBLOCK` 不就夠了？為什麼還有第二個「不要阻塞」旗標？ | **脈絡** |
| 230 | `wait_event_interruptible_locked_irq(...)` | 抱著 spinlock 睡覺？這不是 kernel 的大忌嗎？ | **慣用法** |
| 232 | `return -ERESTARTSYS` | user-space 的 errno 列表裡沒有這個值 | **慣用法** |
| 235 | `eventfd_ctx_do_read(ctx, &ucnt)` | 讀出來的值是 count 全部還是 1？ | **領域**（EFD_SEMAPHORE 語意） |
| 236, 239 | `current->in_eventfd = 1;` … `= 0;` | 這兩行看起來什麼都沒做，刪掉會怎樣？ | **脈絡** |
| 237 | `waitqueue_active(&ctx->wqh)` | 為什麼喚醒前要先檢查？不檢查直接 wake 會錯嗎？ | **慣用法** + **領域** |
| 238 | `wake_up_locked_poll(..., EPOLLOUT)` | read 為什麼要喚醒「可寫」的人？ | **領域** |
| 241 | `unlikely(copy_to_iter(...) != ...)` | `unlikely` 改變語意嗎？ | **慣用法** |
| 全部 | — | 我讀這段是想知道什麼？ | **目標** |

一段 32 行的函式，十四個卡點，五道鴻溝全部出現。這不是特例，這是 kernel 程式碼的常態。接下來示範每一類卡點**用什麼方法解開**——注意方法完全不同。

### 解開間接鴻溝：它是誰呼叫的？

`eventfd_read` 是 `static`，所以呼叫者一定在同一個檔案裡。grep：

```
$ rg -n -w 'eventfd_read' --type c .
./fs/eventfd.c:214:static ssize_t eventfd_read(struct kiocb *iocb, struct iov_iter *to)
./fs/eventfd.c:308:	.read_iter	= eventfd_read,
```

整棵 kernel 只有兩處：定義，和一個 struct 初始化。這就是間接鴻溝的指紋——**函式從未被直接呼叫，只被存進一個欄位**。

```c
/* fs/eventfd.c:302–311（v6.18） */
static const struct file_operations eventfd_fops = {
#ifdef CONFIG_PROC_FS
	.show_fdinfo	= eventfd_show_fdinfo,
#endif
	.release	= eventfd_release,
	.poll		= eventfd_poll,
	.read_iter	= eventfd_read,
	.write		= eventfd_write,
	.llseek		= noop_llseek,
};
```

然後找誰呼叫 `->read_iter`：

```c
/* fs/read_write.c:569–574（v6.18），在 vfs_read() 裡 */
	if (file->f_op->read)
		ret = file->f_op->read(file, buf, count, pos);
	else if (file->f_op->read_iter)
		ret = new_sync_read(file, buf, count, pos);
	else
		ret = -EINVAL;
```

`new_sync_read()`（`fs/read_write.c:481`）把 user 的 `buf` 和 `len` 包成一個 `kiocb` 加一個 `iov_iter`，然後在第 491 行呼叫 `filp->f_op->read_iter(&kiocb, &iter)`。鏈條補齊了：

```
read(2) → ksys_read → vfs_read → new_sync_read → f_op->read_iter → eventfd_read
```

而 `file->private_data` 和 `f_op` 是在建立 eventfd 時一起設定的：`do_eventfd()`（`fs/eventfd.c:409`）呼叫 `anon_inode_getfile_fmode("[eventfd]", &eventfd_fops, ctx, flags, FMODE_NOWAIT)`，把 `eventfd_fops` 和 `ctx` 一起掛到新的 `struct file` 上。這是「找賦值點」的標準動作，Ch 28 會把它系統化。

### 解開領域鴻溝：kiocb 和 iov_iter 是什麼

`struct kiocb`（kernel I/O control block）描述**一次 I/O 操作**：哪個檔案（`ki_filp`）、從哪個位置（`ki_pos`）、帶什麼旗標（`ki_flags`），非同步完成時要呼叫誰（`ki_complete`）。`struct iov_iter` 描述**資料要搬到哪裡**：它可以是一塊 user buffer、一組 iovec、一串 page，呼叫者不用管。`read_iter` 這個介面就是為了讓同一份實作同時服務 `read(2)`、`readv(2)`、aio、io_uring。

要確認 eventfd 的 ABI，最直接的方法是跑一次。下面這支程式逐一觸發第 220、224–227、180 行的分支：

```c
/* efd.c — gcc -O2 -Wall -o efd efd.c */
#define _GNU_SOURCE
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/eventfd.h>
#include <unistd.h>

static void try_read(int fd, size_t len, const char *label)
{
    uint64_t v = 0;
    ssize_t n = read(fd, &v, len);
    if (n < 0) printf("%-28s → -1, errno=%s\n", label, strerrorname_np(errno));
    else       printf("%-28s → %zd bytes, 值=%llu\n", label, n, (unsigned long long)v);
}

int main(void)
{
    uint64_t three = 3;

    int fd = eventfd(0, EFD_NONBLOCK);
    try_read(fd, 4, "buffer 只給 4 bytes");          /* 第 220 行 */
    try_read(fd, 8, "count==0 且 O_NONBLOCK");       /* 第 224–227 行 */
    if (write(fd, &three, 8) != 8) return 1;
    try_read(fd, 8, "寫入 3 之後（一般模式）");     /* 一次全拿走 */
    close(fd);

    fd = eventfd(0, EFD_NONBLOCK | EFD_SEMAPHORE);
    if (write(fd, &three, 8) != 8) return 1;
    for (int i = 0; i < 4; i++)
        try_read(fd, 8, "寫入 3 之後（SEMAPHORE）");  /* 第 180 行 */
    return 0;
}
```

在 WSL（kernel 6.18 系列）上的實際輸出：

```
buffer 只給 4 bytes        → -1, errno=EINVAL
count==0 且 O_NONBLOCK      → -1, errno=EAGAIN
寫入 3 之後（一般模式） → 8 bytes, 值=3
寫入 3 之後（SEMAPHORE） → 8 bytes, 值=1
寫入 3 之後（SEMAPHORE） → 8 bytes, 值=1
寫入 3 之後（SEMAPHORE） → 8 bytes, 值=1
寫入 3 之後（SEMAPHORE） → -1, errno=EAGAIN
```

最後四行對應第 180 行 `eventfd_ctx_do_read()` 裡的那個三元運算：`*cnt = ((ctx->flags & EFD_SEMAPHORE) && ctx->count) ? 1 : ctx->count;`。讀碼得到的預測，被實驗確認了。（中文欄位對不齊是因為 `printf` 的寬度以 byte 計，一個中文字佔 3 bytes——這是 user-space 的小插曲，不影響結論。）

至於第 238 行為什麼 read 要喚醒 `EPOLLOUT`：`eventfd_write()`（`fs/eventfd.c:262–266`）在計數器快溢位時會睡在**同一個** `ctx->wqh` 上等空間。read 把計數器減少了，所以可能有 writer 可以繼續——讀者和寫者共用一條 wait queue，用 poll key（`EPOLLIN` / `EPOLLOUT`）區分要叫醒誰。wait queue 的完整模型在 Ch 33。

### 解開慣用法鴻溝：抱著 spinlock 睡覺？

第 230 行是最容易誤讀的一行。kernel 的鐵律是「持有 spinlock 時不能睡」，這裡卻在 `spin_lock_irq` 之後呼叫一個會讓 process 睡著的 `wait_event_*`。

解法是查定義，一路查到底：

```c
/* include/linux/wait.h:846–848（v6.18） */
#define wait_event_interruptible_locked_irq(wq, condition)			\
	((condition)								\
	 ? 0 : __wait_event_interruptible_locked(wq, condition, 0, do_wait_intr_irq))
```

`__wait_event_interruptible_locked`（同檔第 779 行）是一個迴圈，每一輪呼叫 `fn`，也就是 `do_wait_intr_irq`：

```c
/* kernel/sched/wait.c:349–363（v6.18） */
int do_wait_intr_irq(wait_queue_head_t *wq, wait_queue_entry_t *wait)
{
	if (likely(list_empty(&wait->entry)))
		__add_wait_queue_entry_tail(wq, wait);

	set_current_state(TASK_INTERRUPTIBLE);
	if (signal_pending(current))
		return -ERESTARTSYS;

	spin_unlock_irq(&wq->lock);
	schedule();
	spin_lock_irq(&wq->lock);

	return 0;
}
```

答案在第 358–360 行：**它在 `schedule()` 之前把 lock 放掉，醒來後再拿回來**。名字裡的 `_locked` 意思是「呼叫時必須已經持有 wq->lock，返回時仍然持有」，而不是「睡覺時一直持有」。`_irq` 後綴則表示這個 lock 是用 `spin_lock_irq` 拿的，放掉和拿回時也要配對地開關中斷。這是 kernel 命名慣例的典型：**後綴承載語意**，不認得後綴就會誤讀。

第 232 行的 `-ERESTARTSYS` 也是慣用法，定義在 `include/linux/errno.h:14`，值是 512，上方註解明說 *These should never be seen by user programs*。它告訴 syscall 返回路徑：「我被 signal 打斷了，如果 signal handler 設了 `SA_RESTART`，請自動重啟這個 syscall；否則轉成 `-EINTR` 給 user」。user-space 永遠看不到 512。

第 241 行的 `unlikely()` 不改變語意，它是給編譯器的分支預測提示（展開成 `__builtin_expect`），讓正常路徑的指令排在一起。讀碼時可以把它當成不存在，但它順便告訴你「作者認為這個分支是錯誤路徑」——這是免費的資訊。Ch 18 講這類標註。

### 解開脈絡鴻溝：那兩行 `in_eventfd` 是幹嘛的

第 236 和 239 行把 `current->in_eventfd` 設成 1 再設回 0，中間只有一個喚醒動作。純讀程式碼，你會覺得它可有可無。這正是脈絡鴻溝的警報：**看起來多餘的程式碼，通常有一段歷史**。

先看它還在哪裡被讀：

```
$ rg -n 'in_eventfd' include/ fs/
fs/eventfd.c:68:	if (WARN_ON_ONCE(current->in_eventfd))
fs/eventfd.c:72:	current->in_eventfd = 1;
...
include/linux/sched.h:1040:	unsigned			in_eventfd:1;
include/linux/eventfd.h:45:	return !current->in_eventfd;
```

`eventfd_signal_mask()`（第 56 行起）在它為 1 時直接拒絕執行，註解寫著 *Deadlock or stack overflow issues can happen if we recurse here through waitqueue wakeup handlers*。為什麼會遞迴？答案在 git 歷史裡。用不需要下載舊 blob 的指令先列出這個檔案的 commit：

```
$ git log --oneline v5.0..v6.18 -- fs/eventfd.c
...
9f0deaa12d83 eventfd: guard wake_up in eventfd fs calls as well
b542e383d8c0 eventfd: Make signal recursion protection a task bit
...
12aceb89b0bc eventfd: convert to f_op->read_iter()
b5e683d5cab8 eventfd: track eventfd_signal() recursion depth
...
```

三個 commit 串成一個完整的故事（`git tag --contains` 查到的首次發布版本標在括號裡）：

1. **`b5e683d5cab8`（Jens Axboe，2020-02，v5.6）**：aio 和 io_uring 的使用情境可能造成 `eventfd_signal()` 遞迴呼叫自己——wakeup callback 又去 signal 另一個（或同一個）eventfd——結果是重複拿同一把 wait queue lock 而 deadlock，或者通知鏈太深把 stack 撐爆。當時用一個 per-CPU 計數器偵測遞迴。
2. **`b542e383d8c0`（Thomas Gleixner，2021-07，v5.15）**：per-CPU 計數器在 PREEMPT_RT kernel 上不成立，因為 RT 下 `spin_lock_irqsave()` 不關搶佔。改成 per-task 的一個 bit，也就是今天的 `task_struct::in_eventfd`。
3. **`9f0deaa12d83`（Dylan Yudaken，2022-08，v6.1）**：只在 `eventfd_signal()` 裡防遞迴不夠，user-space 的 `read`/`write` 觸發的喚醒也可能經由 aio 的 poll callback 繞回 `eventfd_signal()`。commit 訊息附了一段**用 libaio 就能 deadlock 的十幾行 user-space 程式**。這個 commit 把保護擴大到 `eventfd_read()` 和 `eventfd_write()`，並把欄位從 `in_eventfd_signal` 改名為 `in_eventfd`。

所以那兩行的意思是：「我正在喚醒等待者；如果某個等待者的 callback 在這期間又試圖 signal 一個 eventfd，請拒絕，不然會 deadlock」。io_uring、aio、vduse 在 signal 前會先問 `eventfd_signal_allowed()`（`include/linux/eventfd.h:43`），被拒絕時改成延後到安全的時機再做。

這些資訊**一個字都不在 `eventfd_read()` 的程式碼或註解裡**。不考古，讀一百遍也讀不出來。

第 225 行 `IOCB_NOWAIT` 也是同一類：commit `12aceb89b0bc`（Jens Axboe，2020-05，v5.8）把 eventfd 從 `->read` 改成 `->read_iter`，訊息寫明理由——`O_NONBLOCK` 是**檔案**的屬性，io_uring 需要的是「**這一次** I/O 不要阻塞」，那只能透過 `kiocb` 的旗標傳進來，而舊的 `->read` 介面沒有 `kiocb`。這同時解釋了第 214 行為什麼參數是 `kiocb` 和 `iov_iter`：那是一次介面遷移的結果。

### 解開目標鴻溝：你到底想知道什麼

前面四類卡點加起來，要全部解完得花上一兩個小時。但如果你一開始就帶著問題，大部分可以不解：

| 你的問題 | 需要解的卡點 | 可以暫時跳過的 |
|---|---|---|
| 「計數器為 0 時 read 怎麼阻塞？」 | 222、230、232 | 214 的 kiocb、236–239 的 in_eventfd、241 |
| 「semaphore 模式和一般模式差在哪？」 | 235（追進 `eventfd_ctx_do_read`） | 幾乎全部其他行 |
| 「read 和 write 怎麼互相喚醒？」 | 237、238、加上 `eventfd_write` 的對應行 | 220、232、241 |
| 「為什麼 io_uring 讀 eventfd 不會 deadlock？」 | 225、236–239，加上考古 | 220、235、241 |

同一段 32 行程式碼，四個問題，四條完全不同的閱讀路徑。**問題決定了哪些鴻溝值得填**。這是 Ch 3 的主題。

## 診斷流程圖

讀到卡住時，照這張圖走一遍：

```
                     讀到一處看不懂
                           │
                           ▼
            ┌──────────────────────────────┐
            │ 我能寫出「我讀這段是為了回答 │──否──▶ 目標鴻溝
            │ ____？」這句話嗎？           │        先寫下問題（Ch 3）
            └──────────────────────────────┘        再判斷這處跟問題有沒有關
                           │是                       無關 → 標黑箱、跳過（Ch 4）
                           ▼
            ┌──────────────────────────────┐
            │ 這處跟我的問題有關嗎？       │──否──▶ 標成黑箱，繼續讀
            └──────────────────────────────┘
                           │是
                           ▼
            ┌──────────────────────────────┐
            │ 有沒有我說不出展開結果的     │──是──▶ 慣用法鴻溝
            │ 巨集、或有特殊含義的命名？   │        查定義、看 .i（Ch 13, 22, 50）
            └──────────────────────────────┘
                           │否
                           ▼
            ┌──────────────────────────────┐
            │ 呼叫鏈是不是在函式指標、     │──是──▶ 間接鴻溝
            │ ops table、callback 處斷掉？ │        找賦值點、動態追蹤（Ch 28, 51）
            └──────────────────────────────┘
                           │否
                           ▼
            ┌──────────────────────────────┐
            │ 每行都能翻成中文，但翻完的   │──是──▶ 領域鴻溝
            │ 中文我也不懂它在幹嘛？       │        補背景：Documentation、LWN（Ch 46）
            └──────────────────────────────┘
                           │否
                           ▼
            ┌──────────────────────────────┐
            │ 我懂它在做什麼，但覺得       │──是──▶ 脈絡鴻溝
            │ 「為什麼要這樣寫？」         │        git blame / log -L / lore（Ch 37, 38）
            └──────────────────────────────┘
                           │否
                           ▼
                  其實你已經懂了。寫進筆記（Ch 7）
```

順序是刻意的。**目標鴻溝放最前面**，因為它決定後面四道要不要處理。慣用法放在間接之前，因為很多「斷掉的呼叫鏈」其實是巨集造成的（例如 `SYSCALL_DEFINE3` 藏起了真正的函式名），先展開巨集，間接可能自己就消失了。領域放在脈絡之前，因為「為什麼這樣寫」的答案常常需要領域知識才看得懂——commit 訊息說「RT 下 `spin_lock_irqsave` 不關搶佔」，你得先知道搶佔是什麼。

## 鴻溝之間的交互

五道鴻溝不是互相獨立的，實際讀碼時它們常常糾纏：

- **慣用法 × 間接**：`SYSCALL_DEFINE3(write, ...)` 既是慣用法（巨集），又製造了間接（真正的符號 `__x64_sys_write` 不在原始碼裡，grep 不到）。Ch 0 的 ctags 就被它騙了。
- **領域 × 脈絡**：`in_eventfd` 的考古結果裡出現「PREEMPT_RT」「per-CPU 變數」，沒有這些領域知識，commit 訊息讀了也是白讀。
- **目標 × 一切**：沒有目標時，每一道鴻溝看起來都一樣緊急；有目標時，多數卡點可以合法地跳過。

常見的**誤診**：

| 你以為卡在 | 實際卡在 | 怎麼發現 |
|---|---|---|
| 領域（「我不懂 VFS」） | 間接（只是不知道 `read_iter` 指向誰） | grep 一下賦值點，問題就消失了 |
| 慣用法（「這巨集好難」） | 目標（這個巨集跟你的問題根本無關） | 問自己「不懂這個巨集會影響我回答問題嗎？」 |
| 脈絡（「這寫法好怪」） | 慣用法（這是 kernel 標準寫法，到處都是） | `rg` 一下，發現有一千處同樣的寫法 |
| 間接（「追不到是誰呼叫的」） | 領域（它是中斷處理函式，本來就沒有呼叫者） | 先補「這個子系統的事件從哪裡來」 |

## 對比：其他人怎麼分類讀碼困難

| 框架 | 分類方式 | 和五道鴻溝的關係 |
|---|---|---|
| Spinellis《Code Reading》 | 依**程式碼的構造**分章：控制流、資料型別、資料結構、大型專案、架構、工具 | 原書的章節大致對應慣用法與間接；本課沿用它的骨架，但把「讀者卡在哪」提到前面 |
| Brooks（1983） | 理解是在**領域知識**與**程式碼知識**之間建立映射 | 領域鴻溝就是 Brooks 說的「領域端缺知識」；Ch 2 細講 |
| Shneiderman & Mayer（1979） | **syntactic**（語法、慣例）vs **semantic**（概念）知識 | 慣用法鴻溝偏 syntactic，領域鴻溝偏 semantic |
| 本課的五道鴻溝 | 依**缺的東西在哪裡**分類 | 直接導向解法：字典、工具、教科書、歷史、自己 |

五道鴻溝的設計原則是**可操作**：每一道都對應一個明確的「下一步該做什麼」。這比「理解是困難的」或「kernel 很複雜」有用得多。

## 踩雷集錦

1. **「我 C 不夠好，所以看不懂」**：在 `eventfd_read()` 的十四個卡點裡，沒有一個是 C 語言本身的問題。如果你寫得出 linked list 和函式指標，你的 C 已經夠用；你缺的是慣用法、領域與脈絡，這些不在 C 教科書裡。
2. **「看不懂就多讀幾遍」**：對慣用法鴻溝有一點用（讀多了會認得），對間接、脈絡鴻溝完全沒用——答案不在你正在讀的這段文字裡。`in_eventfd` 讀一百遍也不會告訴你 libaio 的 deadlock。
3. **「刪掉應該沒差」**：這是脈絡鴻溝最危險的症狀。kernel 裡看起來多餘的程式碼，有相當比例是某個 bug fix 的成果。先 `git log -L` 再下結論。
4. **把 `_locked` 讀成「一直持有 lock」**：慣用法的誤讀比不懂更糟，因為你不知道自己錯了。不確定的巨集，查定義查到底，或看 `.i`。
5. **想一次填平所有鴻溝**：五道鴻溝都填完才算「懂」，這個標準會讓你永遠讀不完一個函式。帶著問題，只填跟問題有關的；其他的標成黑箱（Ch 4）。

## 進階：再往深一層

- **用鴻溝來估時間**：讀一段陌生程式碼前，先掃一眼，估計每道鴻溝的密度。慣用法多→預留查資料的時間；間接多→先準備好 cscope / ftrace；領域陌生→先花半小時讀 Documentation 或 LWN 再碰程式碼；脈絡疑點多→預留考古時間（在 blobless clone 上第一次 blame 會慢，見 Ch 0）。
- **用鴻溝來寫 code review 意見**：審查別人的 patch 時，你的困惑也可以分類。「這個巨集是什麼」是慣用法問題，可以自己查；「為什麼要這樣改」是脈絡問題，應該要求作者寫進 commit 訊息。好的 commit 訊息就是在幫未來的讀者填脈絡鴻溝。
- **反過來用：寫好讀的 code**：知道讀者會卡在哪五個地方，你寫 code 時就知道該在哪裡留線索——間接呼叫旁邊註明可能的實作、怪寫法旁邊寫 `/* see commit xxx */`、commit 訊息寫清楚「為什麼」。kernel 的 `Documentation/process/submitting-patches.rst` 對 commit 訊息的要求，本質上就是要求作者替讀者填脈絡鴻溝。

## 動手練習

用本章的方法診斷 `eventfd_write()`。先自己做，再看解答。

```c
/* fs/eventfd.c:247–280（v6.18） */
static ssize_t eventfd_write(struct file *file, const char __user *buf, size_t count,
			     loff_t *ppos)
{
	struct eventfd_ctx *ctx = file->private_data;
	ssize_t res;
	__u64 ucnt;

	if (count != sizeof(ucnt))
		return -EINVAL;
	if (copy_from_user(&ucnt, buf, sizeof(ucnt)))
		return -EFAULT;
	if (ucnt == ULLONG_MAX)
		return -EINVAL;
	spin_lock_irq(&ctx->wqh.lock);
	res = -EAGAIN;
	if (ULLONG_MAX - ctx->count > ucnt)
		res = sizeof(ucnt);
	else if (!(file->f_flags & O_NONBLOCK)) {
		res = wait_event_interruptible_locked_irq(ctx->wqh,
				ULLONG_MAX - ctx->count > ucnt);
		if (!res)
			res = sizeof(ucnt);
	}
	if (likely(res > 0)) {
		ctx->count += ucnt;
		current->in_eventfd = 1;
		if (waitqueue_active(&ctx->wqh))
			wake_up_locked_poll(&ctx->wqh, EPOLLIN);
		current->in_eventfd = 0;
	}
	spin_unlock_irq(&ctx->wqh.lock);

	return res;
}
```

任務：
1. 列出至少八個卡點，每個標上鴻溝。
2. 它和 `eventfd_read()` 的介面不一樣（`->write` 而非 `->write_iter`），這對 io_uring 有什麼影響？這屬於哪道鴻溝？用什麼方法驗證？
3. 寫下一個具體問題，並標出為了回答它你**不需要**讀的行。

<details>
<summary>參考解答</summary>

**卡點與鴻溝**（不只一種正確答案）：

| 行 | 卡點 | 鴻溝 |
|---|---|---|
| 247 | 沒有人直接呼叫它，經由 `eventfd_fops.write`（第 309 行）被 `vfs_write` 呼叫 | 間接 |
| 247 | `const char __user *buf` 的 `__user` 是什麼 | 慣用法（sparse 標註，Ch 18） |
| 254 | 為什麼必須剛好 8 bytes，比 read 的「至少 8」更嚴格 | 領域（ABI）+ 脈絡（commit `d31563b5f9bb` 收緊了檢查） |
| 256 | `copy_from_user` 為什麼不能直接 `*buf` | 慣用法 + 領域（user/kernel 位址空間） |
| 258 | 為什麼 `ULLONG_MAX` 不能寫 | 領域：`ULLONG_MAX` 被保留來表示溢位，`eventfd_poll` 會回報 `EPOLLERR` |
| 262 | `ULLONG_MAX - ctx->count > ucnt` 為什麼不寫成 `ctx->count + ucnt < ULLONG_MAX` | 慣用法（避免加法溢位的標準寫法，Ch 9） |
| 265 | `wait_event_interruptible_locked_irq` 睡覺時放 lock | 慣用法 |
| 270 | `likely(res > 0)` | 慣用法 |
| 272–275 | `in_eventfd` | 脈絡（commit `9f0deaa12d83`） |
| 274 | write 喚醒 `EPOLLIN` | 領域（喚醒等著讀的人） |

**第 2 題**：`->write` 沒有 `kiocb`，所以拿不到 `IOCB_NOWAIT`；第 264 行只檢查 `O_NONBLOCK`。這是**脈絡鴻溝**（為什麼 read 改了 write 沒改）加一點**領域鴻溝**（io_uring 怎麼處理不支援 nowait 的檔案）。驗證方法：`git log --oneline -- fs/eventfd.c` 找有沒有對應的 write 轉換 commit（在 v6.18 之前沒有）；再到 `io_uring/rw.c` 讀它遇到沒有 `write_iter` 的檔案時怎麼辦。後者留給 Part 5 之後再挑戰。

**第 3 題**範例：問題「計數器快溢位時，blocking write 在等什麼條件？」需要讀 262–268；不需要讀 254–259 的參數檢查、270–276 的喚醒與 `in_eventfd`。

</details>

## 本章重點整理

- 「看不懂」是五個不同的問題：**慣用法**（認得字不認得詞）、**間接**（呼叫鏈斷在指標）、**領域**（不懂背景）、**脈絡**（不懂為什麼）、**目標**（沒帶問題）。
- 五道鴻溝的差別在**缺的東西在哪裡**：字典、工具與執行期、教科書、歷史紀錄、你自己的問題。所以解法完全不同。
- 一段 32 行的 `eventfd_read()` 就有十四個卡點、五道鴻溝全部出現；`in_eventfd` 那兩行的理由只存在於三個 commit 訊息裡。
- 目標鴻溝最上游：先寫下問題，只填跟問題有關的鴻溝，其他標黑箱。
- 讀碼的方式取決於你為什麼讀（Spinellis 的六種動機）。

## 自我檢核

- [ ] 不看本章，能說出五道鴻溝的名稱、各自的一句話症狀，以及「缺的東西在哪裡」
- [ ] 拿一段你最近讀不懂的程式碼（任何專案都可以），能逐行標出卡點屬於哪道鴻溝
- [ ] 能解釋為什麼 `eventfd_read` 在整棵 kernel 裡 grep 不到任何呼叫者，以及這屬於哪道鴻溝、下一步該怎麼做
- [ ] 如果有人說「`wait_event_interruptible_locked_irq` 抱著 spinlock 睡覺是 bug」，你能怎麼反駁？證據在哪個檔案
- [ ] 能說出 `current->in_eventfd` 存在的理由，並說明你是怎麼知道的（而不是從本章背下來的）
- [ ] 想一個你最近讀碼讀到一半放棄的經驗：當時其實卡在哪一道？如果用診斷流程圖，第一步會做什麼不同的事？

## 延伸閱讀

### 書籍

- **《Code Reading: The Open Source Perspective》** — Diomidis Spinellis（Addison-Wesley, 2003）
  - **這本書的定位**：整門課的骨架。第 1 章〈Introduction〉的 1.1 節講讀碼的六種動機，本章的動機表就是以它為底改寫
  - **讀哪幾章**：第 1 章全章（約 20 頁）；附錄的 maxims 清單可以先瀏覽，Ch 58 會回來整理
  - **前提知識**：會 C；書中例子是 NetBSD 與 Apache，年代久遠但方法論不受影響

### 論文

- **[Towards a theory of the comprehension of computer programs](https://doi.org/10.1016/S0020-7373(83)80031-5)** — Ruven Brooks，International Journal of Man-Machine Studies 18(6)（1983）
  - **核心貢獻**：把程式理解描述為在「問題領域」與「程式碼」之間逐層建立映射的過程，由假說驅動
  - **讀哪裡**：前半的理論模型；實驗細節可以跳過
  - **和本章的關聯**：領域鴻溝就是 Brooks 模型中「領域端知識不足」的那一側；Ch 2 會完整展開
- **Delocalized Plans and Program Comprehension** — Stanley Letovsky & Elliot Soloway，IEEE Software 3(3)（1986）
  - **核心貢獻**：觀察專業程式設計師維護程式時的錯誤，發現當一個「計畫」的程式碼分散在多處時，讀者最容易誤解
  - **讀哪裡**：整篇（短）；重點在他們分析的錯誤案例
  - **和本章的關聯**：間接鴻溝的理論源頭——ops table 就是 kernel 版的 delocalized plan

### 官方文件

- **[eventfd(2) man page](https://man7.org/linux/man-pages/man2/eventfd.2.html)**
  - **讀哪裡**：DESCRIPTION 裡 `read(2)`、`write(2)` 兩段，以及 `EFD_SEMAPHORE`
  - **能學到什麼**：本章示範診斷的那段程式碼的 user-space 契約；第 220、258 行的 `EINVAL` 都寫在這裡
  - **前提知識**：會用 `read`/`write` 系統呼叫
- **[Submitting patches（docs.kernel.org）](https://docs.kernel.org/process/submitting-patches.html)**（tree 內：`Documentation/process/submitting-patches.rst`）
  - **讀哪裡**：〈Describe your changes〉一節
  - **能學到什麼**：kernel 社群要求 commit 訊息寫什麼——也就是脈絡鴻溝的答案通常會出現在哪裡、長什麼樣子
  - **前提知識**：無

五道鴻溝告訴你卡在哪裡，但還沒解釋**為什麼**熟手不會卡在同樣的地方。下一章看認知科學怎麼描述程式理解：熟手腦中到底多了什麼。

→ [Ch 2 程式理解的認知模型：chunking、beacons、top-down 與 bottom-up](./02-program-comprehension-models.md)
