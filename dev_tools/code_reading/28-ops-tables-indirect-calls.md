# Ch 28 — ops table 與間接呼叫：找出真正被呼叫的是誰

> **目標**：追到 `file->f_op->read_iter(...)` 這種間接呼叫時不再斷線。學完後你手上有五種找出「真正被呼叫者」的方法（靜態列舉、從建立點反推、Kconfig 縮小、ftrace/bpftrace 動態觀察、gdb 現場查看），知道每一種什麼時候用、會在哪裡騙你，也知道 kernel 為什麼在 Spectre 之後刻意減少間接呼叫（retpoline、`INDIRECT_CALL_*`、`static_call`）。
> **鴻溝**：本章主要練**間接鴻溝**，順帶碰到慣用法鴻溝（ops table 的寫法）與領域鴻溝（VFS 的物件生命週期）。

> **環境**：Linux kernel v6.18 原始碼（`~/code_reading/linux`），以 `O=~/code_reading/build` 建出的 x86_64 kernel；動態實驗在 QEMU 9.x 的 TCG 模式（本機 WSL 沒有可用的 KVM）裡跑 busybox initramfs；ripgrep 15.2.0、gcc 11.4、gdb 12。

## 為什麼需要這個？

讀 kernel 最常見的一次「斷線」長這樣。你從 `read(2)` 往下追，一路順利：`ksys_read` → `vfs_read` → `new_sync_read`，然後看到這一行：

```c
	ret = filp->f_op->read_iter(&kiocb, &iter);
```

（`fs/read_write.c:491（v6.18）`）

在編輯器裡按「跳到定義」，跳到的是 `struct file_operations` 裡的一個欄位宣告，不是函式。線索斷了。這不是你的問題——這行程式碼在**編譯期就沒有答案**。被呼叫的是誰，取決於 `filp` 這個物件當初是怎麼被建立的：它是 ext4 上的普通檔案、是 pipe、是 `/dev/zero`、是 `/proc/self/status`，答案各不相同。

C 沒有 class 與虛擬函式，kernel 用「裝滿函式指標的 struct」來做多型。這個手法早在 Unix V6 的 `cdevsw`/`bdevsw`（字元／區塊裝置切換表）就有了；Spinellis 在《Code Reading》第 5 章講函式指標時，也把它當作「讓程式碼與資料一起決定控制流」的代表。到了今天的 v6.18，光是 `struct file_operations` 的實例就有 1971 個（下面會實際算），其他 `*_operations` 型別的實例還有兩千多個。換句話說，**間接呼叫是 kernel 的常態，不是例外**。如果你沒有一套固定的追法，每遇到一次就卡一次。

Ch 1 把這叫做「間接鴻溝」。它和慣用法鴻溝不同：慣用法鴻溝查一次就懂，間接鴻溝是**資訊根本不在這一行**，你得去別的地方找。本章就是「去哪裡找」的完整清單。

## 先建立直覺

把間接呼叫想成一面牆上的插座。呼叫點（`f->f_op->read_iter(...)`）是插座，它只規定了形狀（函式簽章）；真正的電器（`anon_pipe_read`、`ext4_file_read_iter`……）是插頭。要知道插座現在接的是哪台電器，盯著插座看沒有用，你要找**是誰、在什麼時候把插頭插進去的**。

```
  建立點（插頭插進去）                     呼叫點（插座）
  ─────────────────────                   ──────────────────────
  pipe2()                                 read(fd)
   └ create_pipe_files()                   └ vfs_read()
      └ alloc_file_pseudo(...,                └ new_sync_read()
           &pipeanon_fops)                       └ f->f_op->read_iter(...)
             │                                            │
             ▼                                            │
      struct file { f_op ─────────────┐                   │
                                      ▼                   │
               pipeanon_fops = { .read_iter = anon_pipe_read, ... }
                                      ▲                   │
                                      └───────────────────┘
                                         執行時沿指標找到
```

五種方法其實是五個問題：

1. **全部有哪些插頭？**（靜態列舉）
2. **這個物件是誰插的？**（從建立點反推）
3. **這台機器上實際裝了哪些插頭？**（Kconfig／build 產物）
4. **跑起來時實際通電的是哪個？**（ftrace／bpftrace）
5. **此時此刻、這一個物件接的是哪個？**（gdb）

前三個是讀原始碼就能做的；後兩個要讓 kernel 跑起來。

> 如果你對函式指標與 designated initializer（`.read_iter = xxx` 這種寫法）還不熟，先回看 [Ch 10 — 指標慣用法](./10-pointer-idioms.md) 的函式指標一節。

## 最小模型：一個 user-space 的 ops table

先在 user space 做一個縮小版，看清楚形狀。`struct file_ops` 模仿 `struct file_operations`，`open_file()` 是建立點，`vfs_read()` 是呼叫點：

```c
/* ops.c — 迷你 ops table：模仿 struct file_operations 的形狀 */
#include <stdio.h>
#include <string.h>

struct file;
struct file_ops {
	long (*read)(struct file *f, char *buf, long n);
};
struct file {
	const struct file_ops *f_op;
	const char *name;
};

static long zero_read(struct file *f, char *buf, long n) { memset(buf, 0, n); return n; }
static long hello_read(struct file *f, char *buf, long n) { return snprintf(buf, n, "hello"); }

static const struct file_ops zero_fops  = { .read = zero_read  };
static const struct file_ops hello_fops = { .read = hello_read };

/* 「建立點」：決定這個物件的 f_op */
static void open_file(struct file *f, const char *name)
{
	f->name = name;
	f->f_op = strcmp(name, "zero") == 0 ? &zero_fops : &hello_fops;
}

/* 「呼叫點」：只看得到 f->f_op->read，看不到是誰 */
__attribute__((noinline)) long vfs_read(struct file *f, char *buf, long n)
{
	return f->f_op->read(f, buf, n);
}

#define INDIRECT_CALL_1(f, f1, ...) \
	({ __builtin_expect(f == f1, 1) ? f1(__VA_ARGS__) : f(__VA_ARGS__); })

__attribute__((noinline)) long vfs_read_wrapped(struct file *f, char *buf, long n)
{
	return INDIRECT_CALL_1(f->f_op->read, hello_read, f, buf, n);
}

int main(int argc, char **argv)
{
	struct file f;
	char buf[16];
	open_file(&f, argc > 1 ? argv[1] : "hello");
	long r = vfs_read(&f, buf, sizeof(buf));
	long r2 = vfs_read_wrapped(&f, buf, sizeof(buf));
	printf("%s: read -> %ld / %ld, first byte = %d\n", f.name, r, r2, buf[0]);
	return 0;
}
```

```
$ gcc -O2 -fcf-protection=none -o ops ops.c && ./ops hello && ./ops zero
hello: read -> 5 / 5, first byte = 104
zero: read -> 16 / 16, first byte = 0
```

同一行 `f->f_op->read(...)`，命令列參數不同，跑到不同函式。光讀 `vfs_read` 永遠回答不了「它呼叫誰」，答案在 `open_file`。這就是整章的核心觀念：**間接呼叫的答案在建立點，不在呼叫點**。`vfs_read_wrapped` 和 `INDIRECT_CALL_1` 先放著，「底層機制」一節會用到。

## 五種方法總覽

| 方法 | 回答的問題 | 需要什麼 | 優點 | 會騙你的地方 |
|---|---|---|---|---|
| 1. 靜態列舉（`rg`） | 全部可能是誰 | 原始碼 | 快、完整、不用跑 | 候選太多；巨集生成的漏掉；不知道哪個會被選中 |
| 2. 從建立點反推 | 這種物件是誰設的 | 原始碼 + 領域知識 | 精準、能解釋「為什麼」 | 可能有多段設定（`replace_fops`） |
| 3. Kconfig／build 縮小 | 這台機器上有哪些 | `.config` 或 build 產物 | 砍掉一大半候選 | 啟發式會誤判；module 要另外看 |
| 4. 動態追蹤（ftrace／bpftrace） | 實際跑了哪個 | 能跑的 kernel + root | 鐵證；順便看到整條路徑 | 只看到你觸發的路徑；inline 函式看不到 |
| 5. gdb 現場查看 | 這一個物件現在接誰 | QEMU + vmlinux（含 debug info） | 能印出任何欄位 | 設定成本最高；會改變時序 |

實務上的順序通常是 1 → 2 →（必要時）3 → 4/5。下面逐一實作。

## 方法一：靜態列舉——把所有插頭列出來

最直接的問法：整個 tree 裡，誰把函式指派給 `.read_iter`？

```
$ cd ~/code_reading/linux
$ rg -n '\.read_iter\s*=' --type c . | wc -l
99
$ rg -l '\.read_iter\s*=' --type c . | wc -l
79
$ rg -o --no-filename '\.read_iter\s*=\s*\w+' --type c . | sed 's/.*=\s*//' | sort -u | wc -l
62
```

99 處指派、分布在 79 個檔案、62 個不同的函式。看看最常見的是誰：

```
$ rg -o --no-filename '\.read_iter\s*=\s*\w+' --type c . | sed 's/.*=\s*//' | sort | uniq -c | sort -rn | head -6
     23 generic_file_read_iter
      4 seq_read_iter
      2 v9fs_file_read_iter
      2 tty_read
      2 read_iter_zero
      2 proc_reg_read_iter
```

這張排行榜本身就是資訊：23 個檔案系統直接用 `generic_file_read_iter`（走 page cache 的通用實作），所以「普通檔案的 read」大多會落到它或它的包裝（ext4 的 `ext4_file_read_iter` 最後也會呼叫它）。如果你的問題是「讀普通檔案的資料從哪來」，這一步就已經把範圍縮到 page cache 了——這是練習 E 的起點。

> **注意 `rg` 的一個坑**：在 `bash -s <<'EOF'` 這種 stdin 被佔用的環境裡，`rg pattern` 不帶路徑時會去讀 stdin 而不是搜目前目錄，結果是安靜地回傳 0 筆。本章所有指令都明確帶了 `.`。這個坑我們在寫本章時真的踩到了。

也反過來列出所有**呼叫點**，看看有誰會走這個間接呼叫：

```
$ rg -n -- '->read_iter\(' --type c . | grep -v '^\./Documentation' | head
./io_uring/rw.c:837:		return file->f_op->read_iter(&rw->kiocb, iter);
./fs/nfs/localio.c:642:		status = filp->f_op->read_iter(&iocb->kiocb, &iocb->iters[i]);
./fs/read_write.c:491:	ret = filp->f_op->read_iter(&kiocb, &iter);
./fs/read_write.c:530:	ret = file->f_op->read_iter(&kiocb, &iter);
./fs/read_write.c:825:		ret = filp->f_op->read_iter(&kiocb, iter);
./fs/read_write.c:888:	ret = file->f_op->read_iter(iocb, iter);
./fs/splice.c:363:	ret = in->f_op->read_iter(&kiocb, &to);
./fs/aio.c:1603:		aio_rw_done(req, file->f_op->read_iter(req, &iter));
./drivers/target/target_core_file.c:304:		ret = file->f_op->read_iter(&aio_cmd->iocb, &iter);
./drivers/block/zloop.c:486:		ret = zone->file->f_op->read_iter(&cmd->iocb, &iter);
```

這張表告訴你：`read(2)`、`readv(2)`、`io_uring`、`aio`、`splice`、loop device 都會打到同一組實作。改一個 `read_iter`，影響的是全部這些入口。

### 靜態列舉的三個盲點

**盲點一：巨集生成的 ops table 搜不到。** `include/linux/seq_file.h` 的 `DEFINE_SHOW_ATTRIBUTE` 會幫你生出一整個 `file_operations`：

```c
#define DEFINE_SHOW_ATTRIBUTE(__name)					\
static int __name ## _open(struct inode *inode, struct file *file)	\
{									\
	return single_open(file, __name ## _show, inode->i_private);	\
}									\
									\
static const struct file_operations __name ## _fops = {			\
	.owner		= THIS_MODULE,					\
	.open		= __name ## _open,				\
	.read		= seq_read,					\
	.llseek		= seq_lseek,					\
	.release	= single_release,				\
}
```

（`include/linux/seq_file.h:207-219（v6.18）`）

這個巨集在 tree 裡被用了 716 次，每一次都生出一個 `.read = seq_read` 的 ops table，而你的 `rg '\.read\s*='` 在呼叫處完全看不到它們。遇到 `xxx_fops` 找不到定義時，先 `rg 'DEFINE_\w+\(xxx\)'`，這是 [Ch 22 — kernel 巨集解碼大全](./22-kernel-macro-decoding.md) 的 `DEFINE_*` 家族。

**盲點二：同一個 ops table 可能同時有 `.read` 和 `.read_iter`，而呼叫端有優先順序。** 看 `vfs_read` 怎麼選：

```c
	if (file->f_op->read)
		ret = file->f_op->read(file, buf, count, pos);
	else if (file->f_op->read_iter)
		ret = new_sync_read(file, buf, count, pos);
	else
		ret = -EINVAL;
```

（`fs/read_write.c:569-574（v6.18）`）

再看 `/dev/zero`：

```c
static const struct file_operations zero_fops = {
	.llseek		= zero_lseek,
	.write		= write_zero,
	.read_iter	= read_iter_zero,
	.read		= read_zero,
	.write_iter	= write_iter_zero,
	...
```

（`drivers/char/mem.c:663-668（v6.18）`）

如果你只搜 `.read_iter`，會以為 `read(2)` 讀 `/dev/zero` 跑的是 `read_iter_zero`。錯——`vfs_read` 先檢查 `->read`，跑的是 `read_zero`。`read_iter_zero` 只有 `readv(2)`、`io_uring` 這類只認 `read_iter` 的路徑才會用到。方法四會用 ftrace 實際證明這一點。**列舉出候選之後，一定要回到呼叫點看選擇邏輯。**

**盲點三：多層間接。** procfs 的 `proc_reg_read_iter` 自己又是一個轉接頭，它會再去呼叫 `pde->proc_ops->proc_read_iter`（`include/linux/proc_fs.h:39` 宣告了這個欄位）。追到 `proc_reg_read_iter` 時不要以為到底了，同樣的五種方法要在下一層再用一次。

## 方法二：從建立點反推——誰把插頭插進去

方法一給你 62 個候選。如果你手上的問題很具體——「讀 pipe 的時候跑的是哪個」——更有效率的做法是反過來問：**pipe 的 `struct file` 是誰建立的，它在建立時把 `f_op` 設成什麼？**

### 範例一：匿名 pipe

從 syscall 入口開始（syscall 入口怎麼找是 [Ch 29](./29-syscall-entry-paths.md) 的主題）：`pipe2(2)` → `__do_pipe_flags()` → `create_pipe_files()`。

```c
int create_pipe_files(struct file **res, int flags)
{
	struct inode *inode = get_pipe_inode();
	struct file *f;
	int error;
	...
	f = alloc_file_pseudo(inode, pipe_mnt, "",
				O_WRONLY | (flags & (O_NONBLOCK | O_DIRECT)),
				&pipeanon_fops);
	...
	res[0] = alloc_file_clone(f, O_RDONLY | (flags & O_NONBLOCK),
				  &pipeanon_fops);
```

（`fs/pipe.c:926-957（v6.18）`，中間省略錯誤處理）

插頭就在這裡：寫端與讀端兩個 `struct file` 都拿到 `&pipeanon_fops`。順著 `alloc_file_pseudo` 往下（`fs/file_table.c:371`）會到 `alloc_file` → `file_init_path`，最後是這一行：

```c
	file->f_op = fop;
```

（`fs/file_table.c:337（v6.18）`）

再去看 `pipeanon_fops` 本身：

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

（`fs/pipe.c:1257-1266（v6.18）`）

答案：`anon_pipe_read`。從問題到答案，沒有用到任何動態工具。

這裡有一個**版本陷阱**，正好示範為什麼本課堅持引用 v6.18 實際的 tree：很多舊教材（以及你記憶裡的版本）會說 pipe 用的是 `pipefifo_fops`。在 v6.18，`pipefifo_fops`（`.read_iter = fifo_pipe_read`）只給**具名 FIFO**（`mkfifo` 建出來的檔案）用，匿名 pipe 有自己的 `pipeanon_fops`。這個拆分是哪時候發生的？用 Ch 37 的考古法：

```
$ git log --oneline -S pipeanon_fops v6.10..v6.18 -- fs/pipe.c
262b2fa99cbe pipe: introduce struct file_operations pipeanon_fops
$ git tag --contains 262b2fa99cbe | sort -V | head -1
v6.15
```

commit 訊息寫著 "So that fifos and anonymous pipes could have different f_op methods. Preparation to simplify the next patch."（Oleg Nesterov，2025-02）。它說的 "next patch" 是 `f017b0a4951f`（"pipe: don't update {a,c,m}time for anonymous pipes"，同樣進了 v6.15）：這個 patch 把原本的 `pipe_read`/`pipe_write` 改名為 `anon_pipe_read`/`anon_pipe_write`，再為 FIFO 包一層會更新時間戳的 `fifo_pipe_read`/`fifo_pipe_write`：

```c
static ssize_t
fifo_pipe_read(struct kiocb *iocb, struct iov_iter *to)
{
	int ret = anon_pipe_read(iocb, to);
	if (ret > 0)
		file_accessed(iocb->ki_filp);
	return ret;
}
```

（`fs/pipe.c:406-413（v6.18）`）

所以 v6.14 以前的答案是「`pipefifo_fops`，`.read_iter = pipe_read`」；v6.15 起匿名 pipe 是「`pipeanon_fops`，`.read_iter = anon_pipe_read`」。如果你腦中記的是 `pipe_read`，在 v6.18 的 tree 裡 `rg 'pipe_read\('` 會找到一堆 `anon_pipe_read`、`fifo_pipe_read`，就是找不到你記得的那個名字——**記憶中的函式名已經過時**，這是讀 kernel 的日常。同一個問題在不同版本答案不同，這也是為什麼「記得答案」不如「會找答案」。

具名 FIFO 的 `f_op` 又是誰設的？搜 `&pipefifo_fops`：

```
$ rg -n 'pipefifo_fops|pipeanon_fops' . --type c
./fs/pipe.c:884:static const struct file_operations pipeanon_fops;
./fs/pipe.c:903:	inode->i_fop = &pipeanon_fops;
./fs/pipe.c:946:				&pipeanon_fops);
./fs/pipe.c:957:				  &pipeanon_fops);
./fs/pipe.c:1123:	bool is_pipe = inode->i_fop == &pipeanon_fops;
./fs/pipe.c:1246:const struct file_operations pipefifo_fops = {
./fs/pipe.c:1257:static const struct file_operations pipeanon_fops = {
./fs/pipe.c:1421:	if (file->f_op != &pipefifo_fops && file->f_op != &pipeanon_fops)
./fs/inode.c:2586:		inode->i_fop = &pipefifo_fops;
```

`fs/inode.c:2586` 在 `init_special_inode()` 裡：檔案系統讀到一個 `S_IFIFO` 型別的 inode，就把 `i_fop` 設成 `pipefifo_fops`。這是第二種建立模式——**先設在 inode 上，open 時才抄到 file**。

### 範例二：普通檔案——兩段式設定

普通檔案沒有 `alloc_file_pseudo`，`f_op` 是在 `open(2)` 時從 inode 抄過來的：

```c
	f->f_op = fops_get(inode->i_fop);
```

（`fs/open.c:936（v6.18）`，在 `do_dentry_open()` 裡）

所以要再往上一層問：**inode 的 `i_fop` 是誰設的？** 答案是各檔案系統把 inode 從磁碟讀進來的時候。ext4 的 `__ext4_iget()`：

```c
	if (S_ISREG(inode->i_mode)) {
		inode->i_op = &ext4_file_inode_operations;
		inode->i_fop = &ext4_file_operations;
		ext4_set_aops(inode);
	} else if (S_ISDIR(inode->i_mode)) {
		inode->i_op = &ext4_dir_inode_operations;
		inode->i_fop = &ext4_dir_operations;
```

（`fs/ext4/inode.c:5449-5455（v6.18）`）

這給你一個通用的搜尋模式：`rg 'i_fop\s*=' fs/` 在 v6.18 有 223 處，每一處都是「某種 inode 拿到某組 file_operations」的建立點。

### 範例三：字元裝置——插頭會被換掉

`/dev/zero` 這類字元裝置更曲折。`init_special_inode()` 給 `S_IFCHR` 的 inode 設的是 `def_chr_fops`，它的 `.open` 是 `chrdev_open`，而 `chrdev_open` 會在 open 時**把 `f_op` 整個換掉**：

```c
	fops = fops_get(p->ops);
	if (!fops)
		goto out_cdev_put;

	replace_fops(filp, fops);
	if (filp->f_op->open) {
		ret = filp->f_op->open(inode, filp);
```

（`fs/char_dev.c:408-414（v6.18）`）

`p->ops` 來自 driver 註冊 cdev 時給的 ops。所以字元裝置的建立點有三段：inode 初始化（`def_chr_fops`）→ `chrdev_open` 換成 driver 的 ops → 有些 driver（例如 `drivers/char/mem.c` 的 `memory_open`）在自己的 `.open` 裡**再換一次**，依 minor number 選 `zero_fops`、`full_fops`……。搜 `replace_fops\(` 就能找到所有「中途換插頭」的地方。

方法二的心法整理：

```
這個 struct file 是怎麼來的？
 ├─ alloc_file_pseudo / anon_inode_getfile(...)  → fops 是參數，直接看呼叫處
 ├─ open(2) 一般路徑 → f_op = inode->i_fop
 │    └─ inode->i_fop 誰設？ → rg 'i_fop\s*=' 該檔案系統目錄
 └─ .open 裡呼叫 replace_fops() → 再往下追一層
```

## 方法三：用 Kconfig 與 build 產物縮小範圍

方法一列出的 79 個檔案裡，很多根本沒被編進你的 kernel（s390 的 hypfs、USB gadget、你沒開的檔案系統）。拿 `.config` 篩一次可以砍掉大半。

我們寫了一個啟發式腳本：對每個 `.c`，沿著目錄往上找 Makefile 裡 `obj-$(CONFIG_X) += foo.o` 或 `obj-$(CONFIG_X) += dir/` 那一行，再查 `.config` 裡 `CONFIG_X` 是不是 `y` 或 `m`：

```python
#!/usr/bin/env python3
"""給一串 .c 路徑，粗略判斷它在某個 .config 下會不會被編進去。
啟發式：沿目錄往上，找 Makefile 裡提到 <name>.o 或 <dir>/ 的那一行，
抓出 obj-$(CONFIG_X) 的 X；全部 =y/=m 才算會編。"""
import re, sys, os
src, cfgpath = sys.argv[1], sys.argv[2]
cfg = {}
for line in open(cfgpath):
    m = re.match(r'(CONFIG_\w+)=(.*)', line)
    if m: cfg[m.group(1)] = m.group(2)

def gate(makefile, token):
    try: text = open(makefile).read().replace('\\\n', ' ')
    except FileNotFoundError: return None
    for line in text.splitlines():
        if re.search(r'(^|\s)' + re.escape(token) + r'(\s|$)', line):
            m = re.match(r'\s*[\w-]*obj-\$\((CONFIG_\w+)\)', line)
            if m: return m.group(1)
            if re.match(r'\s*(obj-y|[\w-]+-y|[\w-]+-objs)\b', line): return 'y'
    return '?'

def built(path):
    parts = path.split('/')
    name = parts[-1][:-2] + '.o'
    gates = []
    d = '/'.join(parts[:-1])
    tok = name
    while d:
        g = gate(os.path.join(src, d, 'Makefile'), tok)
        if g is None: g = gate(os.path.join(src, d, 'Kbuild'), tok)
        if g is None: g = '?'
        gates.append((d, tok, g))
        tok = d.split('/')[-1] + '/'
        d = '/'.join(d.split('/')[:-1])
    ok = all(g in ('y', '?') or cfg.get(g) in ('y', 'm') for _, _, g in gates)
    return ok, [g for _, _, g in gates if g not in ('y', '?')]

yes = no = 0
for p in sys.stdin.read().split():
    ok, gs = built(p.lstrip('./'))
    print(('BUILT ' if ok else 'skip  ') + p + '  ' + ' '.join(gs))
    yes += ok; no += (not ok)
print(f'# built={yes} skipped={no}')
```

```
$ rg -l '\.read_iter\s*=' --type c . | grep -v '^./tools/' | sort > files.txt
$ python3 kconfig_filter.py . ~/code_reading/build/.config < files.txt > out.txt
$ tail -1 out.txt
# built=27 skipped=52
$ grep skip out.txt | head -4
skip  ./arch/s390/hypfs/inode.c  CONFIG_S390_HYPFS_FS
skip  ./drivers/block/ublk_drv.c  CONFIG_BLK_DEV_UBLK
skip  ./drivers/net/tap.c  CONFIG_TAP
skip  ./drivers/net/tun.c  CONFIG_TUN
```

79 個候選砍到 27 個。但啟發式就是啟發式。既然 build 已經跑完，可以拿**真正存在的 `.o` 檔**當標準答案對一次：

```
$ while read f; do f=${f#./}; [ -f ~/code_reading/build/${f%.c}.o ] && echo "$f"; done < files.txt | wc -l
24
$ comm -23 heuristic.txt objs.txt      # 啟發式說有、實際沒有
drivers/gpu/drm/display/drm_dp_aux_dev.c
fs/hostfs/hostfs_kern.c
fs/ramfs/file-nommu.c
```

三個誤判，各有原因，每一個都值得看一眼：

- `fs/ramfs/file-nommu.c`：Makefile 寫的是 `file-mmu-y := file-nommu.o` 然後 `file-mmu-$(CONFIG_MMU) := file-mmu.o`——後一行**覆寫**前一行。腳本不懂 make 的變數覆寫語意。
- `fs/hostfs/`：`fs/Makefile` 裡是 `obj-y += hostfs/`，目錄一定會進去，但目錄裡的 `hostfs_kern.o` 受 `CONFIG_HOSTFS` 控制（那是 User-Mode Linux 專用的）。腳本在這層的比對沒抓到。
- `drm_dp_aux_dev.c`：受 `drm_display_helper-$(CONFIG_DRM_DISPLAY_DP_AUX_CHARDEV)` 控制，這種「組合成某個複合物件的成員」的寫法腳本沒處理。

結論：**有 build 產物時，以 `.o` 為準；沒有時，用 `.config` 粗篩，但要知道它會多報。** Kbuild 的完整規則在 [Ch 36 — Kconfig、Kbuild 與 #ifdef](./36-kconfig-kbuild-ifdef.md)。另外別忘了 `=m` 的 module：它們有被編，但要等 module 載入後那組 ops 才會出現在執行中的 kernel 裡。

## 方法四：動態追蹤——讓 kernel 自己告訴你

靜態方法給你「可能」，動態方法給你「實際」。ftrace 的 `function_graph` tracer 會把函式的呼叫樹畫出來，間接呼叫的目標會直接以名字出現。完整的 ftrace 教學在 [Ch 51 — 動態觀察](./51-dynamic-tracing.md)，這裡示範它怎麼解間接鴻溝。

**實驗環境**：用本課 build 出的 v6.18 bzImage，配一個 busybox initramfs，在 QEMU（TCG 模式）裡以 root 跑下面這段 `/init`：

```sh
#!/bin/sh
mount -t proc proc /proc
mount -t sysfs sys /sys
mount -t tracefs nodev /sys/kernel/tracing
mount -t devtmpfs dev /dev 2>/dev/null
T=/sys/kernel/tracing
echo "=== demo1: pipe ==="
echo function_graph > $T/current_tracer
echo vfs_read > $T/set_graph_function     # 只畫以 vfs_read 為根的呼叫樹
echo 3 > $T/max_graph_depth
echo 1 > $T/options/function-fork          # 子行程也要追（pipe 兩端是兩個行程）
echo 0 > $T/tracing_on; echo > $T/trace
sh -c "echo \$\$ > $T/set_ftrace_pid; echo 1 > $T/tracing_on; echo hello | cat > /dev/null; echo 0 > $T/tracing_on"
grep -v '^#' $T/trace | head -40
echo "=== demo2: /dev/zero ==="
echo > $T/trace; echo > $T/set_ftrace_pid
sh -c "echo \$\$ > $T/set_ftrace_pid; echo 1 > $T/tracing_on; dd if=/dev/zero of=/dev/null bs=16 count=1 2>/dev/null; echo 0 > $T/tracing_on"
grep -v '^#' $T/trace | head -30
poweroff -f
```

```
$ qemu-system-x86_64 -accel tcg -cpu max -m 1G -smp 1 -nographic -no-reboot \
    -kernel ~/code_reading/build/arch/x86/boot/bzImage -initrd initramfs.cpio.gz \
    -append "console=ttyS0 quiet nokaslr panic=-1"
=== demo1: pipe ===
 0)               |  vfs_read() {
 0)               |    rw_verify_area() {
 0) + 19.426 us   |      security_file_permission();
 0) + 44.824 us   |    }
 0)               |    anon_pipe_read() {
 0)   3.847 us    |      mutex_lock();
 0) + 17.863 us   |      lock_mm_and_find_vma();
 0) ! 316.270 us  |      handle_mm_fault();
 0)   3.076 us    |      up_read();
 0) + 33.652 us   |      anon_pipe_buf_release();
 0)   2.515 us    |      mutex_unlock();
 0)   2.335 us    |      kill_fasync();
 0) ! 649.191 us  |    }
 0) ! 804.211 us  |  }
 ...
=== demo2: /dev/zero ===
 0)               |  vfs_read() {
 0)               |    rw_verify_area() {
 0) + 12.703 us   |      security_file_permission();
 0) + 28.754 us   |    }
 0)               |    read_zero() {
 0)   1.473 us    |      __cond_resched();
 0) + 74.599 us   |    }
 0) ! 156.051 us  |  }
```

（時間欄位在 TCG 模擬下沒有參考價值，只看呼叫樹的形狀。）

三件事從這份輸出直接讀出來：

1. pipe 的 `read_iter` 是 `anon_pipe_read`——和方法二的推論一致，現在有了鐵證。
2. `/dev/zero` 跑的是 `read_zero`，**不是** `read_iter_zero`。方法一盲點二的推論被證實了。
3. `new_sync_read` 沒有出現在樹上：`vfs_read` 直接呼叫 `anon_pipe_read`。原因是 `new_sync_read` 是 `static` 函式，被編譯器 inline 進 `vfs_read` 了。**動態追蹤看不到被 inline 的函式**，這是它的盲點；讀 trace 時看到「跳了一層」不要慌，回原始碼找 static 小函式。

pipe 讀取底下那個 `handle_mm_fault` 也很有意思：`cat` 的使用者緩衝區第一次被寫入時觸發了 page fault，這是 `copy_to_user` 在拷貝途中發生的。這種東西靜態讀碼幾乎不可能預料到。

### bpftrace 版本（本段未實測，為理論預期行為）

本課的 initramfs 沒有 bpftrace（它需要完整的 user space 與 BTF），所以下面的指令沒有在本環境實際跑過。在一台有 root 與 bpftrace 的 Linux 上，可以用 kprobe 在 `vfs_read` 入口統計「每一組 ops 被讀了幾次」：

```
# bpftrace -e 'kprobe:vfs_read {
    @fops[ksym(((struct file *)arg0)->f_op)] = count();
  }'
```

預期輸出是一張以符號名為 key 的直方圖，例如 `@fops[ext4_file_operations]: 1234`、`@fops[pipeanon_fops]: 56`。要直接看 `read_iter` 目標，把 `->f_op` 換成 `->f_op->read_iter`。驗證方法：在 v6.18 以上、`CONFIG_DEBUG_INFO_BTF=y` 的 kernel 上執行，同時在另一個終端機跑 `cat /etc/passwd` 與 `echo x | cat`，看直方圖是否出現對應的 ops。

## 方法五：gdb 現場查看——這一個物件接的是誰

ftrace 告訴你「跑了誰」，gdb 讓你在停下來的那一刻**看任何欄位**。完整的 QEMU + gdb 流程在 [Ch 52](./52-qemu-gdb-kernel.md)；這裡只示範它怎麼回答間接呼叫的問題。

QEMU 加上 `-gdb tcp::12828 -S`（開 gdb stub、開機前先停住），gdb 這邊用一個指令檔，在 `vfs_read` 下斷點，每次停下來就印出這個 file 的名字、它的 `f_op` 指向哪個符號、`read` 與 `read_iter` 各指向誰，然後繼續：

```
set pagination off
set confirm off
target remote :12828
break vfs_read
commands
silent
printf "vfs_read: file=%s\n", file->f_path.dentry->d_name.name
info symbol file->f_op
info symbol file->f_op->read
info symbol file->f_op->read_iter
continue
end
continue
```

```
$ gdb -q -batch -iex 'set auto-load safe-path /' -x gdbcmds ~/code_reading/build/vmlinux
Breakpoint 1 at 0xffffffff81585880: file /home/ypp/code_reading/linux/fs/read_write.c, line 553.
vfs_read: file=init
shmem_file_operations in section .rodata
No symbol matches file->f_op->read.
shmem_file_read_iter in section .text
vfs_read: file=
pipeanon_fops in section .rodata
No symbol matches file->f_op->read.
anon_pipe_read in section .text
...
vfs_read: file=trace
tracing_fops in section .rodata
seq_read in section .text
seq_read_iter in section .text
...
vfs_read: file=zero
zero_fops in section .rodata
read_zero in section .text
read_iter_zero in section .text
```

這份輸出比 ftrace 多給了好幾個線索：

- 第一次 `vfs_read` 是 kernel 在讀 `/init` 本身。initramfs 解開後的根檔案系統是 tmpfs，所以 ops 是 `shmem_file_operations`——如果你以為根目錄一定是 ext4，這裡會被糾正。
- pipe 的檔名是空字串，對應到 `alloc_file_pseudo(inode, pipe_mnt, "", ...)` 那個 `""` 參數。
- `info symbol` 對 NULL 指標回 "No symbol matches"，等於直接告訴你「這組 ops 沒有 `.read`」。
- `tracing_fops` 與 `zero_fops` 都同時有 `.read` 和 `.read_iter`，依 `vfs_read` 的規則會走 `.read`。

`info symbol <指標>` 是讀間接呼叫最好用的 gdb 指令：它把一個位址反查成符號名，不管那個位址是函式還是一個 ops table。

## 把五種方法串成一個流程

```
看到 x->ops->fn(...) 斷線
        │
        ▼
有沒有具體情境？（「讀 pipe 時」「打開 /dev/foo 時」）
   │有                                   │沒有，想知道全貌
   ▼                                     ▼
方法二：找建立點                      方法一：rg '\.fn\s*=' 列舉
 (alloc_file_* / i_fop / replace_fops)   │
   │                                     ▼
   │                               方法三：.config / .o 篩掉沒編的
   │                                     │
   └──────────────┬──────────────────────┘
                  ▼
        回呼叫點看選擇邏輯（.read 先於 .read_iter？有 NULL 檢查？）
                  │
          還是不確定 / 多層轉接
                  ▼
        方法四：ftrace function_graph 跑一次
        方法五：gdb 停在呼叫點，info symbol x->ops->fn
```

## 底層機制：間接呼叫在機器上長什麼樣，為什麼 kernel 想少用它

回到 `ops.c`，看編譯器產生什麼。普通的 `-O2`：

```
$ gcc -O2 -fcf-protection=none -S -o plain.s ops.c
vfs_read:
	movq	(%rdi), %rax
	jmp	*(%rax)
```

兩條指令：從 `f` 讀出 `f_op`，再從 `f_op` 讀出函式位址並跳過去（因為是尾呼叫，所以是 `jmp` 不是 `call`）。`jmp *(%rax)` 就是**間接分支**：CPU 在讀到記憶體之前不知道要跳去哪，只能靠分支預測器**猜**，先投機執行猜到的目標。

2018 年公開的 Spectre variant 2（Branch Target Injection）就利用這一點：攻擊者可以訓練分支預測器，讓 kernel 的間接分支被投機執行到攻擊者挑選的程式碼片段（gadget），再透過 cache 旁通道把資料漏出來。Google 提出的軟體緩解 **retpoline** 把每個間接分支換成一個 thunk，讓投機執行掉進一個無害的無窮迴圈。打開 gcc 的對應選項：

```
$ gcc -O2 -fcf-protection=none -mindirect-branch=thunk-extern -mfunction-return=keep -S -o retpo.s ops.c
vfs_read:
	movq	(%rdi), %rax
	movq	(%rax), %rax
	jmp	__x86_indirect_thunk_rax
```

間接跳躍變成了跳去 `__x86_indirect_thunk_rax`。kernel 開了 `CONFIG_MITIGATION_RETPOLINE=y`（v6.18 x86 預設為 y，見 `arch/x86/Kconfig:2460`）時，每一個 `f->f_op->xxx()` 都是這樣編譯的。我們 build 的 kernel 也一樣：

```
$ objdump -dr --no-show-raw-insn ~/code_reading/build/fs/read_write.o | awk '/<vfs_read>:/,/^$/' | grep thunk
			27d9: R_X86_64_PLT32	__x86_indirect_thunk_rax-0x4
			2812: R_X86_64_PLT32	__x86_return_thunk-0x4
			2935: R_X86_64_PLT32	__x86_indirect_thunk_rax-0x4
```

`vfs_read` 裡兩個 `__x86_indirect_thunk_rax`：一個是 `->read`，一個是 inline 進來的 `new_sync_read` 裡的 `->read_iter`。thunk 讓每次間接呼叫都多付一筆代價（開機時 kernel 依 CPU 能力，可能把 thunk 改寫成其他序列，代價因 CPU 而異；具體數字請以你的硬體實測為準）。在每秒呼叫幾百萬次的熱路徑上，這筆代價累積起來很可觀，於是 kernel 社群發展出幾招**把間接呼叫變回直接呼叫**的技巧。讀碼時你會不斷看到它們。

### 招式一：`INDIRECT_CALL_*`——猜最常見的幾個

```c
#ifdef CONFIG_MITIGATION_RETPOLINE

/*
 * INDIRECT_CALL_$NR - wrapper for indirect calls with $NR known builtin
 *  @f: function pointer
 *  @f$NR: builtin functions names, up to $NR of them
 *  @__VA_ARGS__: arguments for @f
 *
 * Avoid retpoline overhead for known builtin, checking @f vs each of them and
 * eventually invoking directly the builtin function. The functions are checked
 * in the given order. Fallback to the indirect call.
 */
#define INDIRECT_CALL_1(f, f1, ...)					\
	({								\
		likely(f == f1) ? f1(__VA_ARGS__) : f(__VA_ARGS__);	\
	})
#define INDIRECT_CALL_2(f, f2, f1, ...)					\
	({								\
		likely(f == f2) ? f2(__VA_ARGS__) :			\
				  INDIRECT_CALL_1(f, f1, __VA_ARGS__);	\
	})
...
#else
#define INDIRECT_CALL_1(f, f1, ...) f(__VA_ARGS__)
#define INDIRECT_CALL_2(f, f2, f1, ...) f(__VA_ARGS__)
```

（`include/linux/indirect_call_wrapper.h:5-43（v6.18）`）

想法很樸素：先比較函式指標是不是最常見的那幾個，是的話就**直接呼叫**（直接呼叫不需要 thunk），不是才走間接呼叫。沒開 retpoline 時，巨集退化成普通的 `f(...)`。這是 2018 年底加入的（commit `283c16a2dfd3`，v5.0），在網路堆疊用得最多，v6.18 tree 裡共 79 處。最經典的一處在 IPv4 收包：

```c
	ipprot = rcu_dereference(inet_protos[protocol]);
	if (ipprot) {
		...
		ret = INDIRECT_CALL_2(ipprot->handler, tcp_v4_rcv, udp_rcv,
				      skb);
```

（`net/ipv4/ip_input.c:195-206（v6.18）`）

對讀碼的人來說，`INDIRECT_CALL_2(ptr, a, b, ...)` 是一份**免費的答案**：作者已經告訴你「這個間接呼叫九成九是 `a` 或 `b`」。看到它，方法一到五都可以先省下來。回頭看 `ops.c` 的 `vfs_read_wrapped` 在 retpoline 下的組語，可以看到 `cmpq %rdx, %rax` 比較後直接 inline 了 `hello_read` 的內容（`call snprintf`），只有比較失敗才 `jmp __x86_indirect_thunk_rax`。

（`inet_protos[protocol]` 前面那個 `rcu_dereference` 是 [Ch 31](./31-reading-rcu-code.md) 的主角。）

### 招式二：`static_call`——在執行時改寫成直接呼叫

```c
/*
 * Static call support
 *
 * Static calls use code patching to hard-code function pointers into direct
 * branch instructions. They give the flexibility of function pointers, but
 * with improved performance. This is especially important for cases where
 * retpolines would otherwise be used, as retpolines can significantly impact
 * performance.
```

（`include/linux/static_call.h:5-12（v6.18）`）

`static_call` 適合「目標在開機或設定時決定一次，之後很少改」的情境。`DEFINE_STATIC_CALL(name, func)` 定義一個呼叫點，`static_call(name)(args)` 呼叫它，`static_call_update(name, newfunc)` 會**改寫機器碼**，把呼叫點的直接 `call` 指令目標換掉。2020 年加入（commit `115284d89a43`，v5.10）。KVM 是大用戶，它把整組 `kvm_x86_ops`（Intel VMX 或 AMD SVM 二選一）全部轉成 static call：

```c
#define kvm_x86_call(func) static_call(kvm_x86_##func)
```

（`arch/x86/include/asm/kvm_host.h:1994（v6.18）`）

讀碼時遇到 `static_call(foo)(...)` 或 `kvm_x86_call(foo)(...)`，找被呼叫者的方法是搜 `static_call_update(foo,` 與 `DEFINE_STATIC_CALL(foo,`——前者是換插頭的地方，後者給你預設值。KVM 的情況則是找 `kvm_ops_update()` 如何用 `vmx_x86_ops` 或 `svm_x86_ops` 填入。

### 招式三：乾脆把函式指標表換成 switch

syscall 分派原本是用 `sys_call_table[nr](regs)` 的間接呼叫。2024 年因應 BHI（Branch History Injection）漏洞，Linus 本人把它改成一個巨大的 `switch`（commit `1e3ad78334a6`，v6.9），commit 訊息說：條件分支也會被預測，但預測錯的後果只是「投機執行到錯的 syscall」，比投機執行到隨機 gadget 可控得多。這段在 [Ch 29](./29-syscall-entry-paths.md) 會完整展開。

**讀碼上的意義**：看到 kernel 熱路徑上有奇怪的 `if (f == a) a(); else f();`、`static_call`、或是本來應該是查表卻寫成 `switch` 的地方，通常不是作者不會寫程式，而是 Spectre 之後的效能補償。這是一個典型的脈絡鴻溝——不知道背景就會覺得程式碼很怪。

## 對比與取捨

| 機制 | 執行時能換目標嗎 | retpoline 代價 | 讀碼時怎麼找目標 | 典型場景 |
|---|---|---|---|---|
| 普通 ops table（`x->ops->fn()`） | 每個物件不同 | 每次都付 | 本章五種方法 | VFS、driver、幾乎所有子系統 |
| `INDIRECT_CALL_N` | 是（fallback 走間接） | 猜中時不付 | 巨集參數直接列出常見目標 | 網路收發包熱路徑 |
| `static_call` | 是，但全域一次改寫 | 不付 | 搜 `static_call_update(name,` | KVM、scheduler 的 preempt 模式、perf |
| `switch` 分派 | 否（編譯期固定） | 不付 | 看 switch 的 case（可能由巨集生成） | syscall 分派 |
| 直接呼叫 | 否 | 不付 | 跳到定義即可 | 大部分程式碼 |

## 踩雷集錦

1. **「搜到 `.read_iter = foo` 就代表 `read(2)` 會跑 `foo`」**：錯。`vfs_read` 會先用 `.read`。`/dev/zero` 的 `zero_fops` 同時有兩者，`read(2)` 跑的是 `read_zero`。列出候選後，一定要回呼叫點確認選擇邏輯與 NULL 檢查。
2. **「pipe 的 ops 是 `pipefifo_fops`，讀取函式是 `pipe_read`」**：v6.14 以前對，v6.15 起匿名 pipe 改用 `pipeanon_fops`，`pipe_read`/`pipe_write` 也改名成 `anon_pipe_read`/`anon_pipe_write`（commit `f017b0a4951f`）。你的記憶、舊書、LLM 的回答都可能停在舊版本。以你手上的 tree 為準，必要時 `git log -S` 查。
3. **「ftrace 沒看到 `new_sync_read`，所以它沒被呼叫」**：它被 inline 了。function tracer 只能看到有獨立函式本體、有 `__fentry__` 掛鉤的函式。trace 跳層時，回原始碼找 `static`／`static inline` 小函式。
4. **「`f_op` 在 open 之後就固定了」**：大多數時候是，但 `chrdev_open` 與一些 driver 的 `.open` 會呼叫 `replace_fops()` 換掉它。追字元裝置一定要追進 `.open`。
5. **「Kconfig 篩完的清單就是實際編進去的」**：啟發式會多報（本章實測 27 個中有 3 個誤判）。有 build 產物時以 `.o` 為準；module 要確認有載入。
6. **「這個 `if (f == tcp_v4_rcv) tcp_v4_rcv(...)` 是多餘的程式碼」**：它是 `INDIRECT_CALL_*` 展開後的樣子，用來閃避 retpoline 代價。刪掉不會錯，但熱路徑會變慢。

## 進階：再往深一層

- **不是只有 `file_operations`**。用同一個 `rg` 統計所有 ops 型別的實例數，v6.18 前幾名是 `file_operations`（1971）、`dev_pm_ops`（1119）、`clk_ops`（874）、`snd_soc_dai_ops`（586）、`net_device_ops`（543）。每一種的「建立點」模式不同：`net_device_ops` 在 driver 的 probe 裡設給 `netdev->netdev_ops`；`dev_pm_ops` 透過 `struct device_driver` 的 `.pm` 欄位掛上。[Ch 43 — driver model](./43-driver-model.md) 會把 probe 時的建立點講完整。
- **callback 不一定放在 ops table**。notifier chain、`struct work_struct` 的 `func`、timer 的 callback 都是「把函式指標存在資料結構裡，晚點再叫」。追法相同（找誰把函式指標寫進去），但建立點通常是某個 `register_*` 或 `INIT_*` 巨集。見 [Ch 32](./32-async-irq-softirq-workqueue.md) 與 [Ch 34](./34-notifiers-and-callbacks.md)。
- **clangd 的「Find References」**對 ops table 有用：對 `anon_pipe_read` 找 references，會找到 `pipeanon_fops` 的初始化那一行，等於自動完成方法二的反方向。設定見 [Ch 49](./49-clangd-lsp.md)。
- **自動化**：把方法一與方法三寫成一支工具——給一個 `ops 型別 + 欄位名`，輸出「有編進去的實作清單」——就是 [練習 G](./practice-g-ops-callback-finder.md) 的題目。
- **面試題**：「C 語言怎麼做多型？代價是什麼？」答案的完整版就是本章：struct of function pointers、間接分支、分支預測、Spectre v2、retpoline、以及 kernel 的三種補償。

## 動手練習

1. 對 `.write_iter` 重做一次方法一。比較候選數量與排行榜第一名，和 `.read_iter` 有什麼差別？為什麼？
2. 用方法二追出 `eventfd(2)` 建立的 file 用的是哪組 ops（提示：從 `SYSCALL_DEFINE2(eventfd2,` 開始，找 `anon_inode_getfile` 的參數）。這也是 final project 的前導。
3. 在 `ops.c` 再加一組 `upper_fops`，然後故意讓 `open_file` 對未知名稱回傳 `f_op = NULL`，看程式怎麼崩潰。再對照 `fs/open.c:937` 那行 `if (WARN_ON(!f->f_op))`，想想 kernel 為什麼要在建立點就檢查。
4. 修改方法五的 gdb 指令檔，改成在 `vfs_write` 下斷點，觀察 `echo hello | cat` 時寫端用的是哪個函式。

## 本章重點整理

- 間接呼叫的答案在**建立點**，不在呼叫點。追 `x->ops->fn()` 時，先問「這個 `x` 是怎麼來的」。
- 五種方法：`rg` 列舉全部候選 → 從建立點反推 → 用 `.config`／`.o` 篩掉沒編的 → ftrace／bpftrace 看實際執行 → gdb `info symbol` 看單一物件。
- 每種方法都有盲點：巨集生成的 ops 搜不到、`.read` 優先於 `.read_iter`、inline 函式不出現在 trace、`replace_fops` 中途換插頭、啟發式篩選會多報。
- retpoline 讓每次間接呼叫變貴，所以 kernel 熱路徑上會出現 `INDIRECT_CALL_*`、`static_call`、switch 分派這些「把間接變直接」的寫法。看到它們時，它們本身往往就是答案。

## 自我檢核

- [ ] 不看筆記，能說出五種找「真正被呼叫者」的方法，以及每一種最大的盲點嗎？
- [ ] 如果有人說「`read(2)` 讀 `/dev/zero` 跑的是 `read_iter_zero`」，你能用兩種不同的方法證明他錯了嗎？
- [ ] 能解釋為什麼 ftrace 的呼叫樹裡 `vfs_read` 底下直接是 `anon_pipe_read`，中間少了一層嗎？
- [ ] 能用自己的話說明 retpoline 解決什麼問題、它的代價從哪來，以及 `INDIRECT_CALL_2` 怎麼閃避這個代價嗎？
- [ ] 拿一個你沒追過的 ops 欄位（例如 `struct inode_operations` 的 `.lookup`），能在 15 分鐘內用方法一＋方法二找出 ext4 的實作與它被設定的地方嗎？

## 延伸閱讀

### 官方文件

- **Documentation/filesystems/vfs.rst**（[docs.kernel.org/filesystems/vfs.html](https://docs.kernel.org/filesystems/vfs.html)）
  - **讀哪裡**：「struct file_operations」一節，逐一說明每個欄位的語意與呼叫時機；「Opening a File」一段講 `f_op` 怎麼從 inode 抄到 file。
  - **能學到什麼**：補足本章刻意跳過的領域知識——每個 ops 欄位「應該」做什麼。知道語意之後，讀各檔案系統的實作會快很多。
  - **前提**：知道 inode、dentry、file 三者的區別（[Ch 41](./41-layering-and-vfs.md) 會講）。
- **Documentation/admin-guide/hw-vuln/spectre.rst**（[docs.kernel.org/admin-guide/hw-vuln/spectre.html](https://docs.kernel.org/admin-guide/hw-vuln/spectre.html)）
  - **讀哪裡**：「Spectre variant 2 (Branch Target Injection)」與「Mitigation control on the kernel command line」兩節。
  - **能學到什麼**：retpoline、IBRS、eIBRS 各在什麼 CPU 上啟用，為什麼間接分支是攻擊面。文末參考資料 [9] 是 Google 的 retpoline 原始說明。
  - **前提**：知道什麼是分支預測與投機執行（CSAPP 第 4–5 章程度）。
- **Documentation/trace/ftrace.rst**（[docs.kernel.org/trace/ftrace.html](https://docs.kernel.org/trace/ftrace.html)）
  - **讀哪裡**：「function_graph」、「set_graph_function」、「set_ftrace_pid」與 `options/function-fork` 的說明。
  - **能學到什麼**：本章方法四用到的每個控制檔的完整語意；[Ch 51](./51-dynamic-tracing.md) 會以它為主軸。

### 技術文章

- **Retpoline: a software construct for preventing branch-target-injection** — Paul Turner（Google，2018；[support.google.com/faqs/answer/7625886](https://support.google.com/faqs/answer/7625886)，kernel 的 spectre.rst 參考資料 [9] 指向這裡）
  - **這篇說什麼**：retpoline 的原始設計說明，逐步解釋 thunk 怎麼讓投機執行困在 `pause; lfence` 迴圈裡。
  - **讀哪裡**：整篇不長，重點在 "Retpoline sequence" 的組語與註解。
  - **為什麼值得讀**：看完你就會理解 `__x86_indirect_thunk_rax` 裡到底是什麼，以及 `INDIRECT_CALL_*` 省下的是什麼。

### 書籍

- **《Code Reading: The Open Source Perspective》** — Diomidis Spinellis（Addison-Wesley，2003）
  - **讀哪幾章**：第 5 章「Advanced Control Flow」講函式指標與 callback 的讀法；第 9 章講架構時提到以表驅動的設計。例子是 NetBSD，但「控制流由資料決定」的觀念與本章完全一致。
  - **定位**：本課骨架。讀完本章再回去看原書第 5 章，你會發現 2003 年的 `cdevsw` 和 2025 年的 `pipeanon_fops` 是同一個模式。
- **《Linux Kernel Development》（3rd ed.）** — Robert Love（Addison-Wesley，2010）
  - **讀哪幾章**：第 13 章「The Virtual Filesystem」，講 VFS 四大物件與它們的 operations 表。
  - **注意**：書是 2.6.34 時代，`read`/`write` 與 `read_iter` 的分工、`file_operations` 欄位都已改變；拿它建立物件模型，細節以 v6.18 原始碼為準。

找到被呼叫者之後，下一個問題是：使用者空間的 `read()` 到底怎麼進到 `vfs_read` 的？那段路上也有一張「函式指標表」，而且它在 v6.9 被改寫成了 switch。

→ [Ch 29 syscall 入口與路徑：從 SYSCALL_DEFINE 開始追](./29-syscall-entry-paths.md)
