# Ch 5 — 從哪裡開始讀：入口點與「資料結構優先」

> **目標**：面對一個陌生的系統，知道第一刀從哪裡切：能說出 kernel 的七類入口點與它們的註冊方式，能從 strace、`/proc`、錯誤訊息反推到原始碼裡的入口，並且養成「先讀 struct，再讀函式」的習慣——用 `struct pipe_inode_info` 一個 struct 推出 pipe 的大致運作。
> **鴻溝**：本章主要練**目標鴻溝**（找到起點才有方向）與**間接鴻溝**（kernel 的入口大多是被註冊進去的 callback），並示範資料結構怎麼幫你跨過一部分**領域鴻溝**。

> **環境**：Linux kernel v6.18 原始碼（`~/code_reading/linux`）；觀察用的是 WSL 執行中的 kernel `6.18.58.1-microsoft-standard-WSL2`；strace（Ubuntu 22.04 套件版）、ripgrep、pahole、gcc 11.4。

## 為什麼需要這個？

user-space 程式有一個大家都知道的起點：`main()`。讀 FreeBSD 的 `wc`（[Ch 6](./06-warmup-bsd-wc.md)），打開 `wc.c` 找 `main`，往下讀就對了。

kernel 沒有這種起點。你可能會說「有啊，`start_kernel()`」——它確實是開機時 C 程式碼的第一站，但它只跑一次，做完初始化就退居幕後。你想知道的問題——「`read()` 一個 pipe 時發生什麼事」「網卡收到封包之後誰處理」「`/proc/meminfo` 的數字從哪來」——沒有一個會從 `start_kernel()` 一路追得到。

開機完成之後的 kernel，更像一個**巨大的事件處理函式庫**：它坐在那裡等事件發生——user space 呼叫 syscall、硬體發中斷、計時器到期、有人打開 `/proc` 底下的檔案——然後跑對應的處理函式。每一個「對應的處理函式」都是一個入口點。所以讀 kernel 的第一個問題不是「從哪一行開始」，而是「**我關心的事件，是從哪一扇門進來的？**」

第二個問題是：找到門之後，先看什麼？本章的答案是：**先看資料結構**。這個原則比 kernel 老得多，但在 kernel 裡特別好用。

## 先建立直覺

把 kernel 想成一棟沒有正門、但有很多側門的大樓。每扇門外面都有不同的訪客，門後面接到不同的部門：

```
       user space                 硬體                  kernel 自己
   ┌────────────────┐     ┌────────────────┐     ┌──────────────────┐
   │ read() write() │     │ 網卡、磁碟、    │     │ 計時器、開機程序、 │
   │ open /proc/... │     │ 鍵盤發出中斷    │     │ 背景執行緒        │
   └───────┬────────┘     └───────┬────────┘     └────────┬─────────┘
           │                      │                       │
  ┌────────▼───────┐   ┌──────────▼─────────┐   ┌─────────▼──────────┐
  │ SYSCALL_DEFINE │   │ request_irq()      │   │ *_initcall()        │
  │ file_operations│   │ 註冊的 handler      │   │ module_init()       │
  │ proc/sysfs show│   │ driver .probe      │   │ kthread_run()       │
  └────────┬───────┘   └──────────┬─────────┘   └─────────┬──────────┘
           └───────────────┬──────┴───────────────────────┘
                           ▼
              共享的資料結構（struct file、struct pipe_inode_info、…）
```

兩個重點：

1. **大部分的門是「註冊」出來的，不是寫死的**。driver 呼叫 `request_irq()` 把自己的函式登記到某個中斷號；檔案系統把一張 `file_operations` 表掛到 inode 上。這就是為什麼你追 `f->f_op->read_iter()` 會斷——門牌是執行期才釘上去的（間接鴻溝，[Ch 28](./28-ops-tables-indirect-calls.md)）。
2. **所有的門最後都通到同一批資料結構**。不管訪客從哪扇門進來，最終都是在讀寫那幾個 struct。這就是「資料結構優先」的根據：門很多，但房間沒那麼多。

## user-space：main() 也不一定是真正的起點

先把 user-space 的情況講清楚，因為 kernel 的情況是它的極端版。

- **簡單的命令列工具**（`wc`、`cat`、`uniq`）：`main()` 解析參數，然後一個迴圈做完所有事。入口就是 `main()`，從上往下讀可行。
- **事件驅動的伺服器**（Redis）：`main()` 做完初始化就進入事件迴圈，之後所有工作都發生在註冊進事件迴圈的 callback 裡。讀 `main()` 只會告訴你「它註冊了哪些 callback」，真正的邏輯要從 callback 開始讀。
- **函式庫**（Lua）：沒有 `main()`（`lua.c` 的 `main` 只是一個示範用的直譯器殼），入口是公開的 API 函式，每一個都是一扇門。

kernel 就是「事件驅動 + 函式庫」的混合體，而且門的數量是上萬扇。

## kernel 的七類入口點

下表的每一類都附上 v6.18 tree 裡的真實例子，以及用 ripgrep 粗略數出來的數量（`rg -c '<pattern>' -g '*.c' .`，數的是符合 pattern 的行數，包含所有架構，所以只能當數量級參考）：

| 類型 | 怎麼辨認 | v6.18 例子 | 粗略數量 |
|---|---|---|---|
| 1. syscall | `SYSCALL_DEFINEn(name, ...)` | `fs/pipe.c:1054` `SYSCALL_DEFINE2(pipe2, ...)` | 630（另有 168 個 `COMPAT_SYSCALL_DEFINE`） |
| 2. 初始化 | `module_init()`、`*_initcall()` | `fs/pipe.c:1553` `fs_initcall(init_pipe_fs)` | `module_init` 3092；`fs_initcall` 127；`subsys_initcall` 588 |
| 3. driver probe | `struct *_driver` 的 `.probe =` | `drivers/char/hw_random/mxc-rnga.c:190` | `.probe =` 10452 |
| 4. 中斷 handler | `request_irq()` 系列的第二個參數 | `drivers/misc/dummy-irq.c:39` | `request_irq` 系列呼叫 4185 |
| 5. ops 回呼 | `struct xxx_operations` 的成員賦值 | `fs/pipe.c:1257` `pipeanon_fops` | `struct file_operations X =` 1966 |
| 6. kernel thread | `kthread_run()`、`kthread_create()` | `mm/khugepaged.c:2680` | 355 |
| 7. `/proc`、`/sys` handler | `proc_create*()`、`DEVICE_ATTR*()` | `fs/proc/uptime.c:45` | `proc_create*` 275；`DEVICE_ATTR*` 4592 |

這些數字本身不重要，重要的是比例感：**syscall 只有幾百個，但 probe、ops、sysfs 屬性是上萬個**。大部分 kernel 程式碼不是被 syscall 直接呼叫，而是透過註冊的 callback 被間接呼叫。

### 一個 60 行的檔案，三種入口

`drivers/misc/dummy-irq.c` 整個檔案只有 60 行，卻同時示範了初始化入口和中斷入口（`drivers/misc/dummy-irq.c:18-54`，v6.18）：

```c
static int irq = -1;

static irqreturn_t dummy_interrupt(int irq, void *dev_id)
{
	static int count = 0;

	if (count == 0) {
		printk(KERN_INFO "dummy-irq: interrupt occurred on IRQ %d\n",
				irq);
		count++;
	}

	return IRQ_NONE;
}

static int __init dummy_irq_init(void)
{
	if (irq < 0) {
		printk(KERN_ERR "dummy-irq: no IRQ given.  Use irq=N\n");
		return -EIO;
	}
	if (request_irq(irq, &dummy_interrupt, IRQF_SHARED, "dummy_irq", &irq)) {
		printk(KERN_ERR "dummy-irq: cannot register IRQ %d\n", irq);
		return -EIO;
	}
	printk(KERN_INFO "dummy-irq: registered for IRQ %d\n", irq);
	return 0;
}

static void __exit dummy_irq_exit(void)
{
	printk(KERN_INFO "dummy-irq unloaded\n");
	free_irq(irq, &irq);
}

module_init(dummy_irq_init);
module_exit(dummy_irq_exit);
```

讀的順序是**從下往上**：

1. 檔案最底下的 `module_init(dummy_irq_init)` 是門牌：module 載入時（或內建時在開機的 initcall 階段）會呼叫 `dummy_irq_init`。
2. `dummy_irq_init` 裡的 `request_irq(irq, &dummy_interrupt, ...)` 又釘了一個門牌：之後這個中斷號一觸發，就會跑 `dummy_interrupt`。
3. `dummy_interrupt` 是真正的事件處理邏輯，它**從來沒有被任何程式碼直接呼叫**。你用 `rg 'dummy_interrupt\('` 找呼叫者是找不到的，唯一的線索是 `request_irq` 那一行把它的位址傳了出去。

這個「門牌在下面、邏輯在上面、中間靠註冊串起來」的形狀，在整個 kernel 裡反覆出現。習慣從檔案底部的 `module_init`、`module_platform_driver`、`fs_initcall` 開始讀，是讀 driver 的第一個好習慣。

### syscall：最好找的門

syscall 是最容易定位的入口，因為它有固定的巨集：

```c
SYSCALL_DEFINE2(pipe2, int __user *, fildes, int, flags)
{
	return do_pipe2(fildes, flags);
}

SYSCALL_DEFINE1(pipe, int __user *, fildes)
{
	return do_pipe2(fildes, 0);
}
```

（`fs/pipe.c:1054-1062`，v6.18）。`SYSCALL_DEFINE` 後面的數字是參數個數，參數以「型別, 名字」成對列出。這個巨集在 x86-64 上會展開成 `__x64_sys_pipe2`、`__se_sys_pipe2`、`__do_sys_pipe2` 好幾個函式（定義在 `arch/x86/include/asm/syscall_wrapper.h`），[Ch 29](./29-syscall-entry-paths.md) 會完整拆開。現在記住一件事就好：**syscall 的本體幾乎都很短，馬上轉呼叫一個 `do_xxx` 或 `ksys_xxx`**，真正的邏輯在那裡。

## 從使用者看得到的行為反推入口

知道了有哪些門，下一步是從你觀察到的現象，找出它是從哪扇門進來的。三個最常用的配方：

### 配方 A：strace 看到 syscall → 找 SYSCALL_DEFINE

問題：shell 執行 `echo hi | cat` 時，那條 pipe 是怎麼建立的？

```
$ strace -f -e trace=pipe,pipe2,clone,clone3,dup2,execve,read,write sh -c 'echo hi | cat'
execve("/usr/bin/sh", ["sh", "-c", "echo hi | cat"], 0x7ffcb8894d68 /* 22 vars */) = 0
pipe2([3, 4], 0)                        = 0
clone(child_stack=NULL, flags=CLONE_CHILD_CLEARTID|CLONE_CHILD_SETTID|SIGCHLDstrace: Process 287825 attached
, child_tidptr=0x7fe3aed66a10) = 287825
[pid 287825] dup2(4, 1)                 = 1
[pid 287824] clone(child_stack=NULL, flags=CLONE_CHILD_CLEARTID|CLONE_CHILD_SETTID|SIGCHLD <unfinished ...>
[pid 287825] write(1, "hi\n", 3)        = 3
...
[pid 287826] dup2(3, 0 <unfinished ...>
...
[pid 287826] read(0, "hi\n", 131072)    = 3
```

（節錄；省略了 `cat` 載入 locale 檔案的 read。）幾件事一目了然：shell 呼叫的是 `pipe2` 而不是 `pipe`，拿到 fd 3（讀端）和 fd 4（寫端）；左邊的子行程把 fd 4 `dup2` 成 stdout，右邊的子行程把 fd 3 `dup2` 成 stdin。現在去 kernel 找門：

```
$ rg -n 'SYSCALL_DEFINE\d\((pipe2?|read|write|renameat2),' -g '*.c' .
./fs/read_write.c:722:SYSCALL_DEFINE3(read, unsigned int, fd, char __user *, buf, size_t, count)
./fs/read_write.c:746:SYSCALL_DEFINE3(write, unsigned int, fd, const char __user *, buf,
./fs/namei.c:5395:SYSCALL_DEFINE5(renameat2, int, olddfd, const char __user *, oldname,
./fs/pipe.c:1054:SYSCALL_DEFINE2(pipe2, int __user *, fildes, int, flags)
./fs/pipe.c:1059:SYSCALL_DEFINE1(pipe, int __user *, fildes)
```

注意 pattern 裡的 `SYSCALL_DEFINE\d\(pipe2,`——名字後面緊接逗號或右括號。直接搜 `pipe2` 會撈到幾百個無關結果。

> 如果你想知道 strace 為什麼能看到這些，以及它和 ftrace 的差別，那是 [Ch 51](./51-dynamic-tracing.md) 的主題。這裡把它當黑箱：它在 syscall 的進出口攔截，印出名字、參數、回傳值。

### 配方 B：`/proc` 或 `/sys` 的檔名 → 搜字串常數

問題：`cat /proc/uptime` 的兩個數字是怎麼算出來的？

```
$ strace -e trace=openat,read cat /proc/uptime 2>&1 | grep uptime
openat(AT_FDCWD, "/proc/uptime", O_RDONLY) = 3
read(3, "127174.26 2020278.76\n", 131072) = 21
```

strace 只告訴你 `cat` 呼叫了 `openat` 和 `read`，這兩個 syscall 是通用的，往下追會碰到 VFS 的間接呼叫。更快的方法是：**`/proc` 底下的每個檔名，在原始碼裡一定是一個字串常數**。

```
$ rg -n 'proc_create_single\("(meminfo|version|uptime|loadavg|cmdline)"' fs/proc
fs/proc/loadavg.c:33:	pde = proc_create_single("loadavg", 0, NULL, loadavg_proc_show);
fs/proc/meminfo.c:177:	pde = proc_create_single("meminfo", 0, NULL, meminfo_proc_show);
fs/proc/version.c:23:	pde = proc_create_single("version", 0, NULL, version_proc_show);
fs/proc/uptime.c:45:	pde = proc_create_single("uptime", 0, NULL, uptime_proc_show);
fs/proc/cmdline.c:19:	pde = proc_create_single("cmdline", 0, NULL, cmdline_proc_show);
```

`fs/proc/uptime.c:41-49`（v6.18）：

```c
static int __init proc_uptime_init(void)
{
	struct proc_dir_entry *pde;

	pde = proc_create_single("uptime", 0, NULL, uptime_proc_show);
	pde_make_permanent(pde);
	return 0;
}
fs_initcall(proc_uptime_init);
```

又是同一個形狀：最底下 `fs_initcall` 是門牌，它註冊了 `uptime_proc_show` 這個 callback，真正的邏輯在 `uptime_proc_show`（`fs/proc/uptime.c:12-39`）：第一個數字是開機時間（`ktime_get_boottime_ts64`），第二個是所有 CPU 的 idle 時間加總——這也是為什麼在多核機器上第二個數字比第一個大（上面的輸出裡 2020278 比 127174 大了約 16 倍，和這台機器的 16 核吻合）。

### 配方 C：錯誤訊息或 dmesg → 搜訊息字串

kernel log 裡的每一行訊息，在原始碼裡幾乎都是一個字串常數。看到 `dummy-irq: no IRQ given.  Use irq=N`，直接搜：

```
$ rg -n 'no IRQ given' drivers/
```

就會落在 `drivers/misc/dummy-irq.c:36`。兩個小技巧：訊息裡有 `%d`、`%s` 的部分要拿掉，只搜固定的那一段；太長的訊息可能被拆成好幾行字串（kernel coding style 不鼓勵拆，但舊程式碼裡有），搜不到時換一段較短的子字串。

### 配方之後：從入口往內追，直到第一個斷點

找到入口之後，往內追幾層就會碰到間接呼叫。以 `read(2)` 為例（`fs/read_write.c`，v6.18）：

```c
SYSCALL_DEFINE3(read, unsigned int, fd, char __user *, buf, size_t, count)
{
	return ksys_read(fd, buf, count);
}
```

`ksys_read()`（`fs/read_write.c:704-720`）把 fd 換成 `struct file`，呼叫 `vfs_read()`；`vfs_read()`（`fs/read_write.c:552-581`）做完權限與範圍檢查後：

```c
	if (file->f_op->read)
		ret = file->f_op->read(file, buf, count, pos);
	else if (file->f_op->read_iter)
		ret = new_sync_read(file, buf, count, pos);
```

而 `new_sync_read()`（`fs/read_write.c:481-496`）裡是 `filp->f_op->read_iter(&kiocb, &iter);`。追到這裡，靜態閱讀斷了：`f_op` 指向哪張表，要看這個 `struct file` 是誰建立的。對 pipe 來說，答案在 `create_pipe_files()` 傳給 `alloc_file_pseudo()` 的 `&pipeanon_fops`（`fs/pipe.c:944-946`），而那張表在 `fs/pipe.c:1257-1266`：

```c
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

**`anon_pipe_read` 就是 pipe 的 `read` 入口點，類型 5（ops 回呼）**。它沒有任何直接呼叫者，只在這張表裡被提到。「找到 ops 表的賦值點」這個技巧，[Ch 28](./28-ops-tables-indirect-calls.md) 會系統化。

## 資料結構優先：Show me your tables

找到了 `anon_pipe_read`，接下來讀它？先等一下。

Fred Brooks 在《人月神話》（*The Mythical Man-Month*, 1975）第 9 章「Ten Pounds in a Five-Pound Sack」裡寫過一句後來被反覆引用的話，大意是：只給我看流程圖、把表格藏起來，我會一直困惑；給我看你的表格，我通常就不需要流程圖了，流程會變得一目瞭然。他說的「表格」（tables）就是今天的資料結構。

同樣的觀念在不同年代被不同人講過好幾次：Rob Pike 在〈Notes on Programming in C〉（1989）的第五條規則是「Data dominates」——資料結構選對了，演算法幾乎會自己浮現；Linus Torvalds 在 2006 年一封 git mailing list 的郵件裡也說過類似的話：差勁的程式設計師擔心程式碼，好的程式設計師擔心資料結構和它們之間的關係。

為什麼這個原則對讀碼特別有效？

- **函式是動詞，struct 是名詞**。動詞要搭配名詞才有意義；先知道有哪些名詞，讀到動詞時你就知道它在改變什麼。
- **struct 是狀態的完整清單**。一個 struct 有 18 個欄位，代表這個物件的所有狀態就在這 18 個欄位裡；函式不管寫得多複雜，都只能在這 18 個欄位上做轉換。讀完 struct，你等於拿到了函式的「值域」。
- **struct 比函式穩定**。函式常常被重構、拆分、改名；核心 struct 的欄位變動慢得多。今天讀懂的 struct，三年後大致還成立。
- **struct 定義集中，函式分散**。`struct pipe_inode_info` 只有一個定義，用它的函式散在 `fs/pipe.c`、`fs/splice.c`、`kernel/watch_queue.c` 好幾個檔案裡。

## 示範：只讀一個 struct，推出 pipe 怎麼運作

規則：**這一節只准讀 `include/linux/pipe_fs_i.h`，不准打開 `fs/pipe.c`**。看看光靠資料結構能推出多少，然後再去驗證。

先看 `struct pipe_inode_info` 本體（`include/linux/pipe_fs_i.h:86-119`，v6.18）：

```c
struct pipe_inode_info {
	struct mutex mutex;
	wait_queue_head_t rd_wait, wr_wait;

	/* This has to match the 'union pipe_index' above */
	union {
		unsigned long head_tail;
		struct {
			pipe_index_t head;
			pipe_index_t tail;
		};
	};

	unsigned int max_usage;
	unsigned int ring_size;
	unsigned int nr_accounted;
	unsigned int readers;
	unsigned int writers;
	unsigned int files;
	unsigned int r_counter;
	unsigned int w_counter;
	bool poll_usage;
#ifdef CONFIG_WATCH_QUEUE
	bool note_loss;
#endif
	struct page *tmp_page[2];
	struct fasync_struct *fasync_readers;
	struct fasync_struct *fasync_writers;
	struct pipe_buffer *bufs;
	struct user_struct *user;
#ifdef CONFIG_WATCH_QUEUE
	struct watch_queue *watch_queue;
#endif
};
```

以及 `bufs` 指向的元素型別（`include/linux/pipe_fs_i.h:26-32`）：

```c
struct pipe_buffer {
	struct page *page;
	unsigned int offset, len;
	const struct pipe_buf_operations *ops;
	unsigned int flags;
	unsigned long private;
};
```

再加上檔案最上面的一個常數（`include/linux/pipe_fs_i.h:5`）：

```c
#define PIPE_DEF_BUFFERS	16
```

### 逐欄位推論

| 欄位 | 推論 | 信心 |
|---|---|---|
| `mutex` | 只有一把鎖保護整個 pipe；讀寫端會互相排隊 | 高（kernel-doc 寫「mutex protecting the whole thing」） |
| `rd_wait`, `wr_wait` | 兩個 wait queue：讀的人在 pipe 空的時候睡在 `rd_wait`，寫的人在滿的時候睡在 `wr_wait` | 高（名字 + kernel-doc） |
| `head`, `tail` + `bufs` + `ring_size` | `bufs` 是一個**環狀陣列**（ring buffer），寫入推進 `head`，讀取推進 `tail` | 高 |
| `head_tail` union | `head` 和 `tail` 可以被**一次原子地讀出來**（合成一個 `unsigned long`），代表有地方在不拿鎖的情況下要同時看兩者 | 中（推測；要到 `.c` 驗證） |
| `pipe_buffer` 有 `page`, `offset`, `len` | pipe 的資料**不是一塊連續的 byte 陣列**，而是一串 page，每個 slot 指向一個 page 裡的一段 | 高 |
| `PIPE_DEF_BUFFERS 16` | 預設 16 個 slot；若每個 slot 一個 page（4096 bytes），預設容量 64 KiB | 中（要驗證） |
| `max_usage` vs `ring_size` | 兩個不同的上限：陣列有多大 vs 允許用多少——容量可以調整（猜：`fcntl(F_SETPIPE_SZ)`） | 中 |
| `readers`, `writers` | 記錄還有幾個讀端、寫端開著：`writers == 0` 時讀端應該讀到 EOF；`readers == 0` 時寫端應該收到 `EPIPE`/`SIGPIPE` | 高（對應 POSIX 語意） |
| `r_counter`, `w_counter` | 和 `readers`/`writers` 分開的「計數器」，猜是給具名 FIFO 在 `open()` 時互相等待用的 | 低 |
| `fasync_readers/writers` | 支援 `O_ASYNC`/`SIGIO` 通知 | 高（`fasync` 是 kernel 慣用名） |
| `user`, `nr_accounted` | 每個使用者能用的 pipe buffer 有配額 | 中 |
| `tmp_page[2]` | 快取剛釋放的 page，下次寫入不用重新配置 | 中 |
| `#ifdef CONFIG_WATCH_QUEUE` | 有一種特殊的 pipe 用來傳通知（watch queue），不是每個 kernel 都編進去 | 中 |

一個 struct，18 個欄位，推出了 pipe 的鎖策略、阻塞機制、儲存格式、容量模型、EOF/EPIPE 語意、配額。**還沒讀任何一個函式。**

其中最有價值的推論是第 5 列：「資料存在 page 裡，以 slot 為單位」。它導出一個可以實驗的預測：**pipe 的容量是以 slot 計，不是以 byte 計**。如果每次 write 都佔用一個新 slot、而且只寫 1 byte，那 16 個 slot 只能裝 16 bytes。

### 驗證推論：先看程式碼

現在可以打開 `fs/pipe.c` 了。挑幾個推論來對：

`head_tail` 那個 union 的用途（`fs/pipe.c:229-236`，v6.18）：

```c
/* Done while waiting without holding the pipe lock - thus the READ_ONCE() */
static inline bool pipe_readable(const struct pipe_inode_info *pipe)
{
	union pipe_index idx = { .head_tail = READ_ONCE(pipe->head_tail) };
	unsigned int writers = READ_ONCE(pipe->writers);

	return !pipe_empty(idx.head, idx.tail) || !writers;
}
```

推論正確：這是在**不持有 mutex** 的等待期間檢查「可不可以讀」，所以要一次讀出 head 和 tail，避免讀到不一致的一對。`|| !writers` 也證實了「沒有寫端就算可讀（會讀到 EOF）」。（`READ_ONCE` 是什麼，[Ch 21](./21-read-once-and-barriers.md) 會講。）

ring buffer 的索引方式（`include/linux/pipe_fs_i.h:243-247`）：

```c
static inline struct pipe_buffer *pipe_buf(const struct pipe_inode_info *pipe,
					   unsigned int slot)
{
	return &pipe->bufs[slot & (pipe->ring_size - 1)];
}
```

`slot & (ring_size - 1)` 是「對 2 的冪次取餘數」的位元寫法，代表 `ring_size` 一定是 2 的冪次（kernel-doc 也寫了「should be a power of 2」）。`head` 和 `tail` 一直往上加、從不歸零，用的時候才遮罩——這是 ring buffer 的經典寫法，好處是 `head - tail` 永遠等於使用中的 slot 數，不用特別處理繞回的情況。

預設 16 個 slot（`fs/pipe.c:795` 的 `pipe_bufs = PIPE_DEF_BUFFERS`，以及 `fs/pipe.c:817-826`）：

```c
	pipe->bufs = kcalloc(pipe_bufs, sizeof(struct pipe_buffer),
			     GFP_KERNEL_ACCOUNT);

	if (pipe->bufs) {
		init_waitqueue_head(&pipe->rd_wait);
		init_waitqueue_head(&pipe->wr_wait);
		pipe->r_counter = pipe->w_counter = 1;
		pipe->max_usage = pipe_bufs;
		pipe->ring_size = pipe_bufs;
		pipe->nr_accounted = pipe_bufs;
```

匿名 pipe 建立時，`readers` 和 `writers` 都是 1（`fs/pipe.c:900-903`）：

```c
	inode->i_pipe = pipe;
	pipe->files = 2;
	pipe->readers = pipe->writers = 1;
	inode->i_fop = &pipeanon_fops;
```

### 驗證推論：再用實驗

最後用一個 user-space 程式驗證「容量以 slot 計」。做法是把 pipe 設成 nonblocking，一直寫到 `EAGAIN`，看塞進去多少：

```c
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

/* 用 nonblocking write 一直塞，直到 EAGAIN，回報塞進去幾 bytes、幾次 write */
static void fill(const char *what, int flags, size_t chunk)
{
	int p[2];
	char buf[8192] = {0};
	size_t total = 0, n = 0;

	pipe2(p, O_NONBLOCK | flags);
	for (;;) {
		ssize_t r = write(p[1], buf, chunk);
		if (r < 0) break;
		total += r; n++;
	}
	printf("%-28s F_GETPIPE_SZ=%d  filled=%zu bytes in %zu writes (%s)\n",
	       what, fcntl(p[1], F_GETPIPE_SZ), total, n, strerror(errno));
	close(p[0]); close(p[1]);
}

int main(void)
{
	fill("normal, 1-byte writes", 0, 1);
	fill("normal, 4096-byte writes", 0, 4096);
	fill("O_DIRECT, 1-byte writes", O_DIRECT, 1);
	fill("O_DIRECT, 4096-byte writes", O_DIRECT, 4096);

	/* readers == 0 時寫入：EPIPE（SIGPIPE 先忽略掉，否則 process 直接死） */
	int p[2];
	signal(SIGPIPE, SIG_IGN);
	pipe(p);
	close(p[0]);
	ssize_t r = write(p[1], "x", 1);
	printf("write with no readers -> %zd (%s)\n", r, strerror(errno));
	return 0;
}
```

```
$ gcc -Wall -o pipecap pipecap.c && ./pipecap
normal, 1-byte writes        F_GETPIPE_SZ=65536  filled=65536 bytes in 65536 writes (Resource temporarily unavailable)
normal, 4096-byte writes     F_GETPIPE_SZ=65536  filled=65536 bytes in 16 writes (Resource temporarily unavailable)
O_DIRECT, 1-byte writes      F_GETPIPE_SZ=65536  filled=16 bytes in 16 writes (Resource temporarily unavailable)
O_DIRECT, 4096-byte writes   F_GETPIPE_SZ=65536  filled=65536 bytes in 16 writes (Resource temporarily unavailable)
write with no readers -> -1 (Broken pipe)
```

逐行解讀：

- 預設容量 65536 = 16 slot × 4096 bytes，推論正確（這台機器的 page size 是 4096，`getconf PAGESIZE` 確認過）。
- **普通 pipe 寫 1 byte 寫了 65536 次才滿**：小的 write 會被合併進同一個 page（`pipe_buffer` 的 `PIPE_BUF_FLAG_CAN_MERGE`），所以容量看起來是以 byte 計。
- **O_DIRECT（packet 模式）寫 1 byte，16 次就滿了，總共只裝了 16 bytes**：packet 模式下每次 write 都是獨立的 slot、不能合併。容量以 slot 計的本質在這裡露了出來——這是只讀 struct 就能做出的預測。
- `readers == 0` 時寫入得到 `EPIPE`，推論正確。

### 順便看一眼真實的記憶體佈局

WSL 的 kernel 有內建 BTF（BPF Type Format）型別資訊，`pahole` 可以直接讀出執行中 kernel 的 struct 佈局：

```
$ pahole -C pipe_inode_info /sys/kernel/btf/vmlinux
struct pipe_inode_info {
	struct mutex               mutex;                /*     0    32 */
	wait_queue_head_t          rd_wait;              /*    32    24 */
	wait_queue_head_t          wr_wait;              /*    56    24 */
	/* --- cacheline 1 boundary (64 bytes) was 16 bytes ago --- */
	union {
		long unsigned int  head_tail;            /*    80     8 */
		struct {
			pipe_index_t head;               /*    80     4 */
			pipe_index_t tail;               /*    84     4 */
		};                                       /*    80     8 */
	};                                               /*    80     8 */
	unsigned int               max_usage;            /*    88     4 */
	...
	bool                       poll_usage;           /*   120     1 */

	/* XXX 7 bytes hole, try to pack */

	/* --- cacheline 2 boundary (128 bytes) --- */
	struct page *              tmp_page[2];          /*   128    16 */
	struct fasync_struct *     fasync_readers;       /*   144     8 */
	struct fasync_struct *     fasync_writers;       /*   152     8 */
	struct pipe_buffer *       bufs;                 /*   160     8 */
	struct user_struct *       user;                 /*   168     8 */

	/* size: 176, cachelines: 3, members: 18 */
	/* sum members: 169, holes: 1, sum holes: 7 */
	/* last cacheline: 48 bytes */
};
```

注意：**`note_loss` 和 `watch_queue` 不見了**。WSL 的 kernel 沒有開 `CONFIG_WATCH_QUEUE`，所以那兩個 `#ifdef` 包住的欄位根本沒被編進去。原始碼裡看到的 struct 不等於執行中的 struct——哪些程式碼真的會被編進去，是 [Ch 36](./36-kconfig-kbuild-ifdef.md) 的主題。（這裡讀的是 WSL kernel 的 BTF，不是我們自己編的 v6.18；兩者的 config 不同。）

## 從 struct 到函式：資料結構是讀函式的索引

有了上面的模型，再去讀 `anon_pipe_read()` 的主迴圈，你會發現它幾乎是在「照著 struct 念」：取 `head` 和 `tail`、`pipe_empty()` 判斷、`pipe_buf(pipe, tail)` 拿 slot、從 `buf->page` 的 `buf->offset` 開始複製 `buf->len` bytes、讀完推進 `tail`、空了就睡在 `rd_wait` 上。每一行都在操作你已經認識的欄位。這就是 Brooks 說的「流程會變得一目瞭然」。[Ch 7](./07-reading-notes-and-diagrams.md) 會帶你完整讀一遍這個函式並做成筆記。

## 對比與取捨

| 起點策略 | 適合 | 不適合 |
|---|---|---|
| 從 `main()` 往下讀 | 小型命令列工具（`wc`、`cat`） | 事件驅動程式、函式庫、kernel |
| 從入口點往內追 | 你有一個具體的行為問題（「`read` 時發生什麼」） | 你想理解整個子系統的設計 |
| 資料結構優先 | 理解一個子系統的整體模型；有清楚核心 struct 的程式碼 | 演算法密集、狀態很少的程式碼（例如壓縮、加密核心迴圈），那裡的難點在計算，不在狀態 |
| 從測試讀起 | 有完整 selftests/KUnit 的子系統（[Ch 40](./40-tests-as-specification.md)） | 沒有測試的舊程式碼 |

實務上是組合使用：**用入口點找到「從哪扇門進來」，用資料結構理解「房間裡有什麼」，再回到入口點順著函式走一遍。**

## 踩雷集錦

1. **「kernel 的 main 是 `start_kernel()`，從那裡開始讀」**：`start_kernel()`（`init/main.c`）只在開機時跑一次，負責初始化各子系統。你關心的執行期行為幾乎都不是從它開始的。它適合回答「開機順序」這類問題，不適合當一般讀碼的起點。
2. **「搜 `sys_pipe2` 就能找到 syscall 的定義」**：搜不到定義。`rg '\bsys_pipe2\b'` 只會找到 syscall 表（例如 `arch/x86/entry/syscalls/syscall_64.tbl:305` 的 `293	common	pipe2			sys_pipe2`）和標頭宣告；函式本體是 `SYSCALL_DEFINE2(pipe2, ...)` 巨集產生的。要搜巨集的寫法。
3. **「strace 看到的名字就是 kernel 裡的名字」**：不一定。程式碼裡寫 `pipe()`，glibc 可能幫你換成 `pipe2`；寫 `stat()`，strace 看到的是 `newfstatat`；寫 `open()`，看到的是 `openat`。**以 strace 的輸出為準去搜 kernel**，不要以你在 C 程式碼裡呼叫的函式名為準。
4. **「struct 定義上的欄位都存在」**：`#ifdef CONFIG_...` 包住的欄位不一定被編進去。上面的 pahole 輸出裡，`note_loss` 和 `watch_queue` 就不存在。讀 struct 時看到 `#ifdef`，要記得問「我的 config 有沒有開這個」。
5. **「kernel-doc 一定和欄位同步」**：`struct pipe_inode_info` 的 kernel-doc 寫 `@tmp_page: cached released page`（單數），但欄位早已是 `tmp_page[2]`。用 `git log v6.14..v6.15 -- fs/pipe.c` 可以找到 Mateusz Guzik 的 commit `46af8e2406c2`「pipe: cache 2 pages instead of 1」（2025-03-04），程式碼改了，註解沒跟上。註解是假說的來源，不是證據。

## 進階：再往深一層

- **用 pahole 讀你自己編的 kernel**：如果有帶 debug info 的 `vmlinux`，`pahole -C <struct> vmlinux` 給的是你那份 config 下的真實佈局，比讀原始碼裡的 `#ifdef` 可靠（[Ch 50](./50-compiler-as-reading-tool.md)）。
- **反查「誰在用這個 struct」**：`rg -l 'struct pipe_inode_info' -g '*.c' .` 在 v6.18 列出 39 個檔案，可以快速畫出一個 struct 的「勢力範圍」：除了 `fs/pipe.c`，`fs/splice.c`（出現 44 次，比 `fs/pipe.c` 的 36 次還多）、`kernel/watch_queue.c`，甚至 `net/ipv4/tcp.c`、`fs/fuse/dev.c` 都會碰它——因為 splice 讓 pipe 變成很多子系統之間搬資料的中介。這是 [練習 D](./practice-d-data-structure-map.md) 的起點。
- **用 ftrace 確認入口真的被呼叫**：靜態找到的入口是假說；在 QEMU guest 裡用 `function_graph` tracer 對 `anon_pipe_read` 設 filter，再跑 `echo hi | cat`，就能看到它真的被呼叫、以及它往下呼叫了誰。**本段未實測，為理論預期行為**；具體操作見 [Ch 51](./51-dynamic-tracing.md)。
- **入口點的完整清單**：x86-64 的 syscall 表在 `arch/x86/entry/syscalls/syscall_64.tbl`，每一行是「編號、ABI、名字、進入點」。想知道 kernel 的「syscall 門」總共有哪些，這個檔案就是目錄。

## 動手練習

1. **配方 A**：用 strace 觀察 `ls` 執行時呼叫的 syscall（`strace -c ls` 可以看統計），挑一個你沒見過的 syscall，用 `rg 'SYSCALL_DEFINE\d\(<name>,'` 找到它的定義，說出它轉呼叫了哪個 `do_`/`ksys_` 函式。
2. **配方 B**：`/proc/loadavg` 的五個欄位分別是什麼？從 `fs/proc/loadavg.c:33` 出發，只讀 `loadavg_proc_show` 回答。
3. **資料結構優先**：不讀任何函式，只讀 `fs/eventfd.c:30` 起的 `struct eventfd_ctx` 定義（v6.18；它定義在 `.c` 裡而不是標頭，代表外部只能透過 `include/linux/eventfd.h` 宣告的函式操作它），寫下你對 eventfd 運作方式的五條推論，標上信心高中低。然後打開 `eventfd_read()`（`fs/eventfd.c:214`）驗證。（這個 struct 會在 [Final Project](./final-project-pipe-eventfd-guided-reading.md) 再出現。）
4. **反向實驗**：修改上面的 `pipecap.c`，先用 `fcntl(p[1], F_SETPIPE_SZ, 4096)` 把 pipe 縮小，再跑 O_DIRECT 1-byte 測試。根據 `max_usage` 的推論，你預期會塞進幾 bytes？跑跑看。

## 本章重點整理

- kernel 沒有執行期的 `main()`；它是一個事件處理函式庫，入口點分七類：syscall、initcall/module_init、driver probe、中斷 handler、ops 回呼、kthread、`/proc`/`/sys` handler。
- 大部分入口是註冊出來的 callback，門牌通常在檔案底部（`module_init`、`fs_initcall`、ops 表）。
- 從行為反推入口三配方：strace → `SYSCALL_DEFINE`；`/proc` 檔名 → 字串常數；log 訊息 → 字串常數。
- 資料結構優先：struct 是狀態的完整清單，讀完 struct 再讀函式，函式就變成「在這些欄位上做轉換」。
- 從 `struct pipe_inode_info` 一個 struct 可以推出鎖、阻塞、ring buffer、容量、EOF/EPIPE 語意，而且能做出「O_DIRECT 1-byte 只裝 16 bytes」這種可實驗的預測。

## 自我檢核

- [ ] 不看表格，列出 kernel 的七類入口點，每類說出一個辨認用的巨集或函式名。
- [ ] 如果有人給你一個 driver 的 `.c` 檔，你會先看檔案的哪個部分？為什麼？
- [ ] 一個 kernel 函式用 `rg 'func_name\('` 找不到任何呼叫者。列出三種可能的原因。
- [ ] 用自己的話解釋：為什麼「容量以 slot 計」這個預測，只讀 struct 就能做出來？要讀到哪兩個 struct 的哪些欄位？
- [ ] 想一個你正在用的 user-space 程式（例如你寫過的 server），它的「入口點」有哪幾類？它的核心資料結構是哪一個？

## 延伸閱讀

### 書籍

- **《The Mythical Man-Month: Essays on Software Engineering》** — Frederick P. Brooks Jr.（Addison-Wesley, 1975；1995 年二十週年紀念版）
  - **讀哪幾章**：第 9 章「Ten Pounds in a Five-Pound Sack」，「Show me your tables」那段出自這裡。
  - **這本書的定位**：軟體工程經典。第 9 章本身在談空間與表示法的取捨，「資料表示是程式設計的本質」是那一章的結論。不需要背景知識。
- **《Code Reading: The Open Source Perspective》** — Diomidis Spinellis（Addison-Wesley, 2003）
  - **讀哪幾章**：第 4 章「C Data Structures」。
  - **和本章的關聯**：原書用整整一章講怎麼從 C 的資料結構（陣列、鏈結串列、樹、圖）辨認程式意圖；本章的「逐欄位推論」是同一個方法套在 kernel 上。

### 文章

- **Notes on Programming in C** — Rob Pike（1989）
  - **讀哪裡**：最後的「Complexity」一節，列了五條規則；第五條「Data dominates」和本章直接相關。
  - **為什麼值得讀**：短（十幾分鐘讀完），作者是 Unix、Plan 9、Go 的作者之一；「資料結構選對了，演算法會自己浮現」這個觀點講得最精簡的版本。
- **Anatomy of a system call**（part 1、part 2）— David Drysdale，LWN.net（2014）
  - **這篇說什麼**：從 user space 的 syscall 指令一路追到 `SYSCALL_DEFINE` 展開後的函式，解釋 syscall 表怎麼產生。
  - **讀哪裡**：part 1 講 x86-64 的 syscall 路徑與 `SYSCALL_DEFINEn` 巨集。2014 年的文章，巨集細節在 2018 年之後有變（commit `fa697140f9a2` 讓 x86-64 改用 `struct pt_regs` 傳參、新增 `arch/x86/include/asm/syscall_wrapper.h`），概念仍然正確；細節以 v6.18 原始碼為準。前提：知道 user mode / kernel mode 的區別。

### 官方文件

- **Overview of the Linux Virtual File System**（`Documentation/filesystems/vfs.rst`）
  - **讀哪裡**：「struct file_operations」小節，列出每個成員的語意。
  - **能學到什麼**：類型 5 入口點（ops 回呼）裡最常見的一張表；讀完你會知道 `read_iter`、`poll`、`release` 分別在什麼時候被呼叫。前提：知道 inode、dentry、file 三者的關係。
- **Device drivers**（`Documentation/driver-api/driver-model/driver.rst`）
  - **讀哪裡**：`probe` 回呼的說明。
  - **能學到什麼**：類型 3 入口點的完整語意——`probe` 什麼時候被呼叫、回傳值代表什麼。[Ch 43](./43-driver-model.md) 會深入。

入口點和資料結構都有了，該把整套心法從頭到尾跑一次。下一章拿一個完整、小巧、真實的程式——FreeBSD 的 `wc`——當暖身。

→ [Ch 6 暖身：用全套心法讀完 FreeBSD wc](./06-warmup-bsd-wc.md)
