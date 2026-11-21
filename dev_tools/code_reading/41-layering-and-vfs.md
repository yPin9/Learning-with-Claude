# Ch 41 — 分層與抽象：VFS 作為範例

> **目標**：拿到任何一段檔案系統相關的 kernel 程式碼，能立刻說出「我現在在哪一層」、這一層對上承諾什麼、對下要求什麼；能把一次 `open()` + `read()` 從 syscall 一路追到 ext4 與 page cache，並在 `f_op->read_iter()` 這種斷點上找到真正被呼叫的函式。

> **鴻溝**：本章主要練**間接鴻溝**（ops table 讓呼叫鏈斷掉）與**領域鴻溝**（不知道 superblock／inode／dentry／file 各自代表什麼）。

> **環境**：Linux kernel v6.18 原始碼（`~/code_reading/linux`），WSL2 Ubuntu 22.04（WSL kernel `6.18.58.1-microsoft-standard-WSL2`），gcc 11.4。本章的 struct 大小取自本課 `~/code_reading/build` 的 x86_64 config，換 config 會變。

## 為什麼需要這個？

前面 Part 2–6 我們練的是「讀懂一個函式」。可是讀 kernel 很快就會撞上另一種卡關：每一行都看得懂，函式也追得下去，但追了二十層之後你不知道自己在哪裡。`vfs_read` 呼叫 `new_sync_read`，它呼叫 `filp->f_op->read_iter`，你用 Ch 28 的方法找到 `ext4_file_read_iter`，它又呼叫 `generic_file_read_iter`，接著 `filemap_read`、`a_ops->read_folio`……這些函式屬於誰？哪些是「所有檔案系統共用」的、哪些是「ext4 自己的」？如果你要改一個 bug，應該改哪一層？

Spinellis 在《Code Reading》第 9 章談「系統結構」（system structures）時，把分層（layering）列為最常見的架構之一：每一層只用下一層提供的介面，對上一層提供一組更抽象的服務。讀分層系統的關鍵不是讀完每一層，而是**先認出層的邊界**，因為邊界就是介面，介面就是你可以「黑箱」（Ch 4）的地方。

Linux 的 VFS（Virtual File System，虛擬檔案系統層）是 kernel 裡最典型、也最值得當範本的分層。它的歷史背景值得先講：早期 UNIX 只有一種檔案系統，`read()` 直接呼叫那個檔案系統的程式碼。1985 年前後 Sun 要讓 NFS 和本地 UFS 共存，Steve Kleiman 設計了 **vnode／VFS 架構**（USENIX 1986），在 syscall 和具體檔案系統之間插一層抽象介面，每個檔案系統填一張函式表。Linux 從一開始就借用這個想法。今天 v6.18 支援幾十種檔案系統，你的 WSL 上 `/proc/filesystems` 就列了 39 種，但 `read(2)` 的程式碼只有一份。

為什麼不選別的設計？

- **不分層，在 `read()` 裡 `switch (fs_type)`**：每加一個檔案系統就要改核心路徑，而且 `switch` 會散落到 open、read、write、stat、mmap……每一個 syscall。這是 Ch 45 會談的「用多型取代條件判斷」的反面。
- **每個檔案系統自己實作完整的 syscall**：重複程式碼爆炸，而且權限檢查、fsnotify、`f_pos` 鎖這些「所有檔案都要做」的事會被每個人各寫一遍、各錯一遍。

VFS 的選擇是中間路線：**通用層把所有檔案系統都要做的事做掉，把真正不同的部分留給 ops table**。讀懂這個切法，就讀懂了一半的 `fs/`。

## 先建立直覺

想像一家連鎖餐廳。櫃台（syscall 層）收單的方式全國統一；中央廚房（VFS 通用層）負責驗證、排隊、記帳、把大部分料理做好；每家分店（ext4、procfs、pipefs）只負責「自己那一道招牌菜」的特殊做法。如果某分店沒有招牌做法，就照中央廚房的標準食譜（`generic_*`）來。

```
   user space:      open("/a/b")          read(fd, buf, n)
 ─────────────────────────────────────────────────────────────────
   syscall 層       do_sys_openat2        ksys_read
                       │                     │
   VFS 通用層       路徑解析 (namei)       vfs_read
   (fs/*.c, mm/)    dentry cache          權限/範圍檢查
                    建立 struct file       ──► f_op->read_iter
                       │                     │
 ── ops table 邊界 ── i_op->lookup ──────── f_op ─────── a_ops ──────
                       │                     │             │
   具體檔案系統      ext4_lookup        ext4_file_read_iter  ext4_read_folio
   (fs/ext4/ ...)                          │ (多數情況)
                                           ▼
   通用預設實作                     generic_file_read_iter → filemap_read
   (mm/filemap.c)                           (page cache)
                                           │ cache miss
   block 層                                ▼  a_ops->readahead / read_folio
                                         submit bio → 磁碟
```

這張圖有兩件事要記住：

1. 呼叫不是一路往下的。`ext4_file_read_iter` 往下走到 ext4 自己的程式碼之後，又**回頭呼叫通用層**的 `generic_file_read_iter`，通用層再透過 `a_ops` 呼叫回 ext4。這種「通用 → 具體 → 通用 → 具體」的三明治，是 kernel 分層和教科書分層最大的差別。
2. 每一條水平線都是一張 ops table。追到 `->` 就斷的地方，就是層的邊界。

> 如果你對 ops table 怎麼追還不熟，先回看 [Ch 28 — ops table 與間接呼叫](./28-ops-tables-indirect-calls.md)。本章假設你會用「找賦值點」的方法把 `f->f_op->read_iter` 還原成具體函式。

## 四大物件：VFS 的資料模型

照 [Ch 5](./05-entry-points-and-data-first.md) 的「資料結構優先」原則，讀 VFS 不要從函式開始，從四個物件開始。`Documentation/filesystems/vfs.rst` 的 Overview 也是這樣組織的：dentry、inode、file，然後是 superblock。

| 物件 | 代表什麼 | 生命週期 | 對應的 ops | 定義位置（v6.18） |
|---|---|---|---|---|
| `struct super_block` | 一個**已掛載的檔案系統實例** | mount 到 umount | `super_operations`（`s_op`） | `include/linux/fs.h:1445` |
| `struct inode` | 一個**檔案本體**（metadata：大小、權限、資料在哪） | 被任何人引用期間，可被 cache 住 | `inode_operations`（`i_op`）＋預設的 `file_operations`（`i_fop`） | `include/linux/fs.h:793` |
| `struct dentry` | **路徑中的一個名字**，把名字連到 inode | 在 dcache 裡，可以是 negative（名字不存在） | `dentry_operations`（`d_op`） | `include/linux/dcache.h:92` |
| `struct file` | 一次 **open 的結果**：位置、模式、用哪張 f_op | open 到最後一次 close | `file_operations`（`f_op`） | `include/linux/fs.h:1211` |

一個最容易搞混的關係：**一個 inode 可以有多個 dentry（硬連結），一個 dentry 可以被多個 file 開啟**；`f_pos`（讀到哪）屬於 file，不屬於 inode。兩個 process 各自 `open()` 同一個檔案，拿到兩個 `struct file`、共用一個 inode。

下面只摘每個 struct 中和「分層」直接有關的欄位，完整定義請在你的 tree 打開對照。

### superblock：檔案系統實例

```c
/* include/linux/fs.h:1445（v6.18，節錄） */
struct super_block {
	struct list_head	s_list;		/* Keep this first */
	dev_t			s_dev;		/* search index; _not_ kdev_t */
	...
	struct file_system_type	*s_type;
	const struct super_operations	*s_op;
	...
	unsigned long		s_magic;
	struct dentry		*s_root;
```

`s_type` 指回「這是哪一種檔案系統」（註冊在全域 `file_systems` 串列裡，Ch 45 會拿它當 registry 模式的例子），`s_op` 是這個實例的 ops，`s_root` 是掛載點根目錄的 dentry。`s_magic` 就是 `statfs(2)` 回給你的 `f_type`，下面的實驗會用到。

### inode：檔案本體與它的兩張表

```c
/* include/linux/fs.h:793（v6.18，節錄） */
struct inode {
	umode_t			i_mode;
	...
	const struct inode_operations	*i_op;
	struct super_block	*i_sb;
	struct address_space	*i_mapping;
	...
	union {
		const struct file_operations	*i_fop;	/* former ->i_op->default_file_ops */
		void (*free_inode)(struct inode *);
	};
	...
	struct address_space	i_data;
```

inode 身上掛著**三套** ops，分別服務三個不同的「上層」：

- `i_op`（`inode_operations`）：給**路徑解析和 metadata 操作**用——`lookup`、`create`、`unlink`、`permission`、`setattr`。這是「對目錄裡的名字做事」。
- `i_fop`（`file_operations`）：**不是 inode 自己用的**，是 open 時要複製到 `struct file` 的預設值。註解 `former ->i_op->default_file_ops` 透露了它的歷史：以前它就叫 default file ops。
- `i_mapping->a_ops`（`address_space_operations`）：給 **page cache** 用——怎麼把一頁從儲存裝置讀進來、怎麼寫回去。

### dentry：名字到 inode 的連結

```c
/* include/linux/dcache.h:92（v6.18，節錄） */
struct dentry {
	/* RCU lookup touched fields */
	unsigned int d_flags;		/* protected by d_lock */
	seqcount_spinlock_t d_seq;	/* per dentry seqlock */
	struct hlist_bl_node d_hash;	/* lookup hash list */
	struct dentry *d_parent;	/* parent directory */
	union {
	struct qstr __d_name;		/* for use ONLY in fs/dcache.c */
	const struct qstr d_name;
	};
	struct inode *d_inode;		/* Where the name belongs to - NULL is
					 * negative */
	...
	const struct dentry_operations *d_op;
	struct super_block *d_sb;	/* The root of the dentry tree */
```

注意欄位的排列：註解寫著 `RCU lookup touched fields`，最前面那一群是路徑查找的熱路徑會碰的欄位，被刻意擠在第一條 cache line。這是 Ch 21、Ch 31 讀過的「欄位順序本身就是設計」的又一個例子。`d_inode == NULL` 代表 **negative dentry**：cache 住「這個名字不存在」，下次 `stat` 一個不存在的檔案就不用再問檔案系統。

### file：一次 open

```c
/* include/linux/fs.h:1211（v6.18，節錄） */
struct file {
	spinlock_t			f_lock;
	fmode_t				f_mode;
	const struct file_operations	*f_op;
	struct address_space		*f_mapping;
	void				*private_data;
	struct inode			*f_inode;
	...
	loff_t				f_pos;
```

`f_op` 通常等於 `inode->i_fop`，但**可以不同**——character device 的 `open` 會把 `f_op` 換成真正 driver 的那張表（`chrdev_open` 做的就是這件事）。所以讀碼時看到 `file->f_op`，不要假設它一定是檔案系統的 f_op。

本課 build（x86_64，課程 config）用 `pahole` 量出來的大小：`super_block` 1280 bytes、`inode` 592、`dentry` 192、`file` 184。dentry 和 file 被刻意壓在 3 條 cache line 以內，因為系統裡它們的數量是百萬級的。

## 一次 open + read 的完整旅程

接下來用一條真實的路徑把四個物件串起來。先用 user space 看「同一個 read，不同的檔案系統」：

```c
/* samread.c — 同一個 read(2)，底下是三個完全不同的檔案系統 */
#include <stdio.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/vfs.h>
#include <sys/stat.h>

static void probe(const char *label, int fd)
{
    struct statfs sfs;
    struct stat st;
    char buf[32];

    fstatfs(fd, &sfs);
    fstat(fd, &st);
    ssize_t n = read(fd, buf, sizeof(buf) - 1);
    buf[n > 0 ? n : 0] = '\0';
    for (char *p = buf; *p; p++)
        if (*p == '\n') *p = ' ';
    printf("%-10s f_type=0x%08lx ino=%-10lu read=%2zd \"%.20s\"\n",
           label, (unsigned long)sfs.f_type, (unsigned long)st.st_ino, n, buf);
}

int main(void)
{
    int fd, p[2];

    fd = open("demo.txt", O_RDONLY);       /* ext4 上的普通檔 */
    probe("ext4", fd);
    close(fd);

    fd = open("/proc/version", O_RDONLY);  /* procfs：內容是當場產生的 */
    probe("procfs", fd);
    close(fd);

    if (pipe(p) < 0 || write(p[1], "hello pipe\n", 11) != 11)  /* pipefs */
        return 1;
    probe("pipefs", p[0]);
    return 0;
}
```

```
$ echo "hello from ext4" > demo.txt
$ gcc -Wall -O2 -o samread samread.c && ./samread
ext4       f_type=0x0000ef53 ino=422558     read=16 "hello from ext4 "
procfs     f_type=0x00009fa0 ino=4026532023 read=31 "Linux version 6.18.5"
pipefs     f_type=0x50495045 ino=3471438    read=11 "hello pipe "
```

三個 `f_type` 就是三個 superblock 的 `s_magic`，可以在 `include/uapi/linux/magic.h` 查到：`EXT4_SUPER_MAGIC 0xEF53`、`PROC_SUPER_MAGIC 0x9fa0`、`PIPEFS_MAGIC 0x50495045`（ASCII 的 "PIPE"）。同一份 `vfs_read`，背後是三張不同的 `f_op`：ext4 的資料在磁碟和 page cache 裡，procfs 的資料是讀的當下由 kernel 函式印出來的（`seq_file`，Ch 45），pipe 根本沒有「檔案」，資料在 ring buffer 裡。

### open：從路徑到 struct file

```
do_sys_openat2            fs/open.c:1420   配 fd 號、複製檔名
 └ do_filp_open           fs/namei.c:4153
    └ path_openat         fs/namei.c:4114  ← 配置空的 struct file
       ├ link_path_walk / walk_component    逐段解析路徑，查 dcache
       │   └ (cache miss) __lookup_slow      fs/namei.c:1789
       │        └ inode->i_op->lookup(...)   fs/namei.c:1816  ── 進入具體 fs
       └ do_open          fs/namei.c:3931
          └ vfs_open      fs/open.c:1092
             └ do_dentry_open  fs/open.c:903
                  f->f_op = fops_get(inode->i_fop)   fs/open.c:936
                  open = f->f_op->open; open(inode, f)  fs/open.c:963–965
```

`do_dentry_open` 裡這兩段值得逐字讀，因為它就是「通用層」與「具體層」交接的那一刻：

```c
/* fs/open.c:936（v6.18） */
	f->f_op = fops_get(inode->i_fop);
	if (WARN_ON(!f->f_op)) {
		error = -ENODEV;
		goto cleanup_all;
	}

	error = security_file_open(f);
	...
	/* normally all 3 are set; ->open() can clear them if needed */
	f->f_mode |= FMODE_LSEEK | FMODE_PREAD | FMODE_PWRITE;
	if (!open)
		open = f->f_op->open;
	if (open) {
		error = open(inode, f);
		if (error)
			goto cleanup_all;
	}
	f->f_mode |= FMODE_OPENED;
	if ((f->f_mode & FMODE_READ) &&
	     likely(f->f_op->read || f->f_op->read_iter))
		f->f_mode |= FMODE_CAN_READ;
```

讀這段要看出三件事：

1. **f_op 從 inode 複製過來**——這就是 Ch 28「找賦值點」要找的地方之一。要知道某個檔案的 `f_op` 是誰，往上找 `inode->i_fop` 在哪被設；ext4 在 `fs/ext4/inode.c:5451`（`__ext4_iget`）依 `i_mode` 設成 `&ext4_file_operations`，ramfs 在 `fs/ramfs/inode.c:75`。
2. **通用層的檢查夾在前後**：LSM（`security_file_open`）、fsnotify、lease 在呼叫 `->open` **之前**做完。檔案系統不需要、也不能跳過它們。這就是分層的價值：安全檢查只有一個地方。
3. **`->open` 是可選的**：`if (open)`。沒有 `open` 的檔案系統（例如 ramfs 的普通檔）什麼都不用做。`FMODE_CAN_READ` 是根據「f_op 有沒有 read 或 read_iter」算出來的能力旗標，等一下 `vfs_read` 會檢查它。

### read：從 fd 到 page cache

```c
/* fs/read_write.c:704（v6.18） */
ssize_t ksys_read(unsigned int fd, char __user *buf, size_t count)
{
	CLASS(fd_pos, f)(fd);
	ssize_t ret = -EBADF;

	if (!fd_empty(f)) {
		loff_t pos, *ppos = file_ppos(fd_file(f));
		if (ppos) {
			pos = *ppos;
			ppos = &pos;
		}
		ret = vfs_read(fd_file(f), buf, count, ppos);
		if (ret >= 0 && ppos)
			fd_file(f)->f_pos = pos;
	}
	return ret;
}
```

`CLASS(fd_pos, f)(fd)` 是 v6.5 之後的 `cleanup.h` 寫法（Ch 17、Ch 45），等價於「`fdget_pos(fd)`，離開 scope 時自動 `fdput_pos`」。這一層只處理 fd → `struct file` 與 `f_pos` 的讀寫，然後交給 `vfs_read`：

```c
/* fs/read_write.c:552（v6.18） */
ssize_t vfs_read(struct file *file, char __user *buf, size_t count, loff_t *pos)
{
	ssize_t ret;

	if (!(file->f_mode & FMODE_READ))
		return -EBADF;
	if (!(file->f_mode & FMODE_CAN_READ))
		return -EINVAL;
	if (unlikely(!access_ok(buf, count)))
		return -EFAULT;

	ret = rw_verify_area(READ, file, pos, count);
	if (ret)
		return ret;
	if (count > MAX_RW_COUNT)
		count =  MAX_RW_COUNT;

	if (file->f_op->read)
		ret = file->f_op->read(file, buf, count, pos);
	else if (file->f_op->read_iter)
		ret = new_sync_read(file, buf, count, pos);
	else
		ret = -EINVAL;
	if (ret > 0) {
		fsnotify_access(file);
		add_rchar(current, ret);
	}
	inc_syscr(current);
	return ret;
}
```

這是**分層邊界的標準長相**，以後在 kernel 任何子系統看到同樣形狀，都可以套同一套讀法：

```
[通用前置檢查]  →  [分派到 ops，可能有多個候選與退路]  →  [通用後置處理]
 FMODE、access_ok     ->read 優先，否則 ->read_iter        fsnotify、統計
 rw_verify_area       都沒有就 -EINVAL
```

`->read` 和 `->read_iter` 兩個入口並存是歷史遺產：`read_iter`（以 `struct kiocb` + `struct iov_iter` 為參數）是後來加的統一介面，能同時服務 `read`、`readv`、`aio`、`io_uring`。新的檔案系統只實作 `read_iter`，`new_sync_read` 負責把舊式呼叫轉接過去：

```c
/* fs/read_write.c:481（v6.18） */
static ssize_t new_sync_read(struct file *filp, char __user *buf, size_t len, loff_t *ppos)
{
	struct kiocb kiocb;
	struct iov_iter iter;
	ssize_t ret;

	init_sync_kiocb(&kiocb, filp);
	kiocb.ki_pos = (ppos ? *ppos : 0);
	iov_iter_ubuf(&iter, ITER_DEST, buf, len);

	ret = filp->f_op->read_iter(&kiocb, &iter);
	BUG_ON(ret == -EIOCBQUEUED);
	if (ppos)
		*ppos = kiocb.ki_pos;
	return ret;
}
```

這是一個 **adapter**（Ch 45）：把 `(buf, len, ppos)` 包裝成 `(kiocb, iov_iter)`。在這裡追碼斷掉是正常的，下一步用 `f_op` 的賦值點：ext4 的表在 `fs/ext4/file.c:963`：

```c
/* fs/ext4/file.c:963（v6.18） */
const struct file_operations ext4_file_operations = {
	.llseek		= ext4_llseek,
	.read_iter	= ext4_file_read_iter,
	.write_iter	= ext4_file_write_iter,
	...
	.mmap_prepare	= ext4_file_mmap_prepare,
	.open		= ext4_file_open,
	...
```

```c
/* fs/ext4/file.c:130（v6.18） */
static ssize_t ext4_file_read_iter(struct kiocb *iocb, struct iov_iter *to)
{
	struct inode *inode = file_inode(iocb->ki_filp);

	if (unlikely(ext4_forced_shutdown(inode->i_sb)))
		return -EIO;

	if (!iov_iter_count(to))
		return 0; /* skip atime */

#ifdef CONFIG_FS_DAX
	if (IS_DAX(inode))
		return ext4_dax_read_iter(iocb, to);
#endif
	if (iocb->ki_flags & IOCB_DIRECT)
		return ext4_dio_read_iter(iocb, to);

	return generic_file_read_iter(iocb, to);
}
```

ext4 的 `read_iter` 只做了 ext4 特有的事：檢查檔案系統是否被強制關閉、DAX、direct I/O 走自己的路徑。**一般的 buffered read 全部交回通用層** `generic_file_read_iter`（`mm/filemap.c:2898`），它最後呼叫 `filemap_read`（`mm/filemap.c:2711`）在 page cache 裡找頁；找不到時透過 `mapping->a_ops->read_folio`（例如 `mm/filemap.c:2531`）或 readahead 的 `aops->readahead`（`mm/readahead.c:162`）回頭叫 ext4 的 `ext4_read_folio`／`ext4_readahead`（`fs/ext4/inode.c:3976` 的 `ext4_aops`）。

> page cache 本身怎麼運作屬於領域知識，[練習 E](./practice-e-trace-read-syscall.md) 會把這條路徑再往下追到 folio 與 readahead。本章的重點是**邊界**，不是 page cache 的內部。

## 通用預設實作 vs 檔案系統覆寫

把 ext4 和 ramfs 的 `file_operations` 擺在一起，分層的設計意圖就很清楚：

```c
/* fs/ramfs/file-mmu.c:41（v6.18） */
const struct file_operations ramfs_file_operations = {
	.read_iter	= generic_file_read_iter,
	.write_iter	= generic_file_write_iter,
	.mmap_prepare	= generic_file_mmap_prepare,
	.fsync		= noop_fsync,
	.splice_read	= filemap_splice_read,
	.splice_write	= iter_file_splice_write,
	.llseek		= generic_file_llseek,
	.get_unmapped_area	= ramfs_mmu_get_unmapped_area,
};
```

ramfs 的資料**本來就只存在 page cache 裡**，所以它的 `read_iter` 直接填 `generic_file_read_iter`，`fsync` 填 `noop_fsync`（沒有東西要寫回）。整張表幾乎都是通用層提供的零件。

| 欄位 | ext4 | ramfs | 通用層提供的零件 |
|---|---|---|---|
| `read_iter` | `ext4_file_read_iter`（包一層再呼叫 generic） | `generic_file_read_iter` | `generic_file_read_iter`、`filemap_read` |
| `llseek` | `ext4_llseek` | `generic_file_llseek` | `generic_file_llseek`、`noop_llseek`（`fs/read_write.c:324`） |
| `fsync` | `ext4_sync_file` | `noop_fsync` | `noop_fsync`（`fs/libfs.c:1623`） |
| `splice_read` | `ext4_file_splice_read`（包一層再呼叫 `filemap_splice_read`） | `filemap_splice_read` | `filemap_splice_read` |
| 目錄的 `read` | `generic_read_dir` | `generic_read_dir`（經 `simple_dir_operations`） | `generic_read_dir` 回傳 `-EISDIR` |

規律是：**檔案系統只覆寫它真的不同的地方，而且常常是「先做自己的事，再呼叫通用版本」**。這就是 Ch 45 會正式命名的 template method 模式。整個 `fs/libfs.c` 就是「給簡單檔案系統用的零件包」：`simple_dir_operations`（`fs/libfs.c:230`）、`simple_lookup`、`simple_setattr`……sysfs、debugfs、ramfs 都大量用它。

最後一列可以直接驗證。對目錄 `read(2)`，ext4 和 procfs 都會回 `EISDIR`，因為兩者的目錄 `f_op->read` 都指向同一個通用函式：

```c
/* fs/libfs.c:224（v6.18） */
ssize_t generic_read_dir(struct file *filp, char __user *buf, size_t siz, loff_t *ppos)
{
	return -EISDIR;
}
```

```
$ python3 -c 'import os; fd=os.open(".", os.O_RDONLY); os.read(fd, 10)'
IsADirectoryError: [Errno 21] Is a directory
$ python3 -c 'import os; fd=os.open("/proc", os.O_RDONLY); os.read(fd, 10)'
IsADirectoryError: [Errno 21] Is a directory
```

這個例子的教學價值在於：`EISDIR` 這個錯誤碼**不是**在 `vfs_read` 裡判斷「是不是目錄」產生的，而是 ops table 裡填了一個「永遠失敗」的函式。如果你在 `vfs_read` 裡找 `S_ISDIR` 會找不到，因為「目錄不能 read」這條規則被編碼在**資料**（ops table 的內容）裡，而不是**控制流**裡。這是讀 kernel 最需要的觀念轉換之一。

## 讀碼技巧：判斷你在哪一層

拿到一個不熟的檔案系統函式，用下面的檢查表定位。

### 線索一：檔案在哪個目錄

| 路徑 | 層 |
|---|---|
| `fs/read_write.c`、`fs/open.c`、`fs/namei.c`、`fs/dcache.c`、`fs/inode.c`、`fs/super.c` | VFS 通用層 |
| `fs/libfs.c` | 給簡單檔案系統的通用零件 |
| `mm/filemap.c`、`mm/readahead.c` | page cache（被 VFS 和檔案系統共同使用） |
| `fs/ext4/`、`fs/btrfs/`、`fs/proc/`、`fs/ramfs/` … | 具體檔案系統 |
| `block/` | block 層，在檔案系統之下 |

### 線索二：函式名稱前綴

`vfs_*` 是 VFS 給 kernel 其他部分呼叫的入口（例如 nfsd、overlayfs 會呼叫 `vfs_read`）；`generic_*`、`simple_*`、`filemap_*`、`noop_*` 是通用預設；`ext4_*`、`proc_*` 是具體實作。這是 [Ch 2](./02-program-comprehension-models.md) 的 **beacon**：看到前綴就能猜出層級，不用打開函式。

### 線索三：它被誰怎麼呼叫

這是最可靠的判準。

- **往上看呼叫者**：如果一個函式**只被**透過 ops table 呼叫（`rg` 找不到直接呼叫，只找到 `.read_iter = xxx`），它就是「下層實作」。用 `rg -n 'ext4_file_read_iter'` 試試：只有定義和 `ext4_file_operations` 裡那一行。
- **往下看 ops**：如果一個函式體裡出現 `->f_op->`、`->i_op->`、`->a_ops->`、`->s_op->`，它就是「上層」，正在把工作分派給下層。`vfs_read`、`do_dentry_open`、`__lookup_slow` 都是這種。
- **兩者都有**：`generic_file_read_iter` 被當成 `.read_iter` 填進表（像下層），體內又呼叫 `mapping->a_ops->direct_IO`（像上層）。它是夾在中間的**通用零件**，是三明治的那片吐司。

```
$ cd ~/code_reading/linux
$ rg -n 'ext4_file_read_iter'
fs/ext4/file.c:130:static ssize_t ext4_file_read_iter(struct kiocb *iocb, struct iov_iter *to)
fs/ext4/file.c:965:	.read_iter	= ext4_file_read_iter,
```

### 線索四：分層違規要特別注意

好的分層下，具體檔案系統不應該呼叫另一個具體檔案系統，VFS 通用層不應該知道 ext4 的內部 struct。當你看到違規（例如 `fs/overlayfs/` 呼叫 `vfs_*` 去操作底下另一個檔案系統），通常有特別理由：overlayfs 本身就是「疊在其他檔案系統上的檔案系統」，它在架構上同時扮演上層和下層。讀到這種地方，先畫圖再讀。

## 對比與取捨

| 方案 | 優點 | 缺點 | kernel 裡的例子 |
|---|---|---|---|
| 嚴格分層（每層只呼叫下一層） | 好理解，可以單獨替換一層 | 效能差：常見路徑要穿過每一層 | 教科書 OSI 模型；kernel 很少嚴格遵守 |
| 分層 + 通用零件（VFS 的做法） | 檔案系統可以挑零件組裝，常見路徑短 | 呼叫鏈會在層間來回，第一次讀容易迷路 | `generic_file_read_iter`、`libfs` |
| 單一大 switch | 一個地方看完所有情況 | 每加一種類型就改核心程式碼 | 早期 UNIX、很多小型嵌入式 OS |
| 外掛式（user-space 檔案系統，FUSE） | 檔案系統 bug 不會讓 kernel 掛掉 | 每次操作多一次 context switch | `fs/fuse/` 本身是一個 VFS 下層，把請求轉給 user space |

VFS 選擇第二種，代價是我們讀碼時要接受「呼叫鏈會回頭」。Spinellis 原書在討論分層時也提醒，真實系統的分層通常不是乾淨的，常有跨層呼叫與為了效能開的捷徑。kernel 是極端例子：它要求常見路徑（例如 dcache 命中的 RCU path walk）完全不碰鎖、不呼叫任何檔案系統程式碼，這種最佳化本身就是為了**繞過**分層。

## 踩雷集錦

1. **以為 `f_op` 一定是檔案系統的**：很多人看到 `file->f_op->read_iter` 就去 `fs/` 找。但對 character device，`chrdev_open` 會把 `f_op` 換成 driver 的表；對 socket，`f_op` 是 `socket_file_ops`。正確做法是先確認這個 file 是怎麼被 open 的（它的 inode 是什麼類型），再找賦值點。
2. **以為 inode 就是磁碟上的 inode**：`struct inode` 是 VFS 的**記憶體內**物件，對 procfs、pipefs 來說磁碟上根本沒有對應物。ext4 磁碟上的 inode 是 `struct ext4_inode`，記憶體裡的 ext4 inode 是 `struct ext4_inode_info`，它**嵌入**了一個 VFS 的 `struct inode`（用 `EXT4_I()` 這個 `container_of` 包裝互轉）。這是 Ch 42 的 struct 嵌入繼承。
3. **在通用層找特定行為的判斷式**：像 `EISDIR` 的例子，很多「規則」不在 `if` 裡，而在 ops table 填了什麼。找不到判斷式時，改去看表。
4. **把 `->read` 和 `->read_iter` 當成二選一的歷史問題忽略掉**：`vfs_read` 是 `->read` 優先。如果一個 driver 兩個都填，`read(2)` 走 `->read`，`readv(2)` 走 `->read_iter`，行為可能不一致。讀 driver 時兩個都要看。
5. **忘記 negative dentry**：追 `stat("/no/such/file")` 時以為每次都會呼叫檔案系統的 `->lookup`。第二次之後 dcache 裡有 negative dentry，檔案系統完全不會被呼叫。用動態追蹤（Ch 51）驗證時，這會讓你以為追錯了。

## 進階：再往深一層

- **路徑解析的兩種模式**：`fs/namei.c` 的 path walk 有 RCU-walk（不拿 reference、不拿鎖，用 `d_seq` seqcount 驗證）和 REF-walk（失敗時退回的慢路徑）。`Documentation/filesystems/path-lookup.rst` 是 Neil Brown 寫的長文，從頭解釋這個設計，讀它之前先讀 Ch 31（RCU）。
- **`mmap_prepare`**：你可能注意到 v6.18 的 ext4、ramfs 表裡都是 `.mmap_prepare` 而不是傳統的 `.mmap`。這是 mm 子系統近期把 `->mmap` 往「先準備描述、再由核心建 VMA」的方向重構的一部分。遇到這種新欄位，用 Ch 37 的 `git log -L` 或 `git blame` 對著 `include/linux/fs.h` 找它被加入的 commit，讀 commit message 理解動機。（注意：本課的 linux tree 是 blobless clone，`git log -S` 這類 pickaxe 搜尋會觸發大量 blob 下載，請改用 `git blame` 限定行範圍。）
- **疊加式檔案系統**：overlayfs、ecryptfs 同時是上層也是下層，讀它們時建議畫兩張圖：「它對 VFS 呈現什麼」與「它對底下的檔案系統做什麼」。
- **常見的面試問題**：「兩個 process `open` 同一個檔案、各自 `read`，`f_pos` 會互相影響嗎？那 `fork` 之後呢？」答案取決於是否共用同一個 `struct file`。`fork` 複製的是 fd table，兩個 fd 指向同一個 `struct file`，所以共用 `f_pos`；這也是 `ksys_read` 要用 `fdget_pos` 拿 `f_pos_lock` 的原因。

## 動手練習

1. **找 procfs 的 read 路徑**：`/proc/version` 的 `f_op->read_iter` 是誰？提示：從 `fs/proc/version.c` 開始，看它用什麼 API 註冊，再追到 `fs/proc/inode.c` 裡 procfs 自己的 `file_operations`（或 `proc_ops`）。畫出它和 ext4 路徑在哪一點分岔。
2. **三種檔案系統的 `i_fop` 賦值點**：用 `rg -n 'i_fop = '` 分別在 `fs/ext4/`、`fs/ramfs/`、`fs/pipe.c` 找出 `i_fop` 被設定的位置，記下每個檔案系統依 `i_mode` 分了幾種情況。
3. **找一個跳過 `generic_file_read_iter` 的檔案系統**：在 `fs/` 底下找一個 `read_iter` 完全不呼叫 `generic_file_read_iter` 的檔案系統（提示：網路檔案系統或 FUSE），想想它為什麼不能用 page cache 的通用路徑。
4. **（需要 QEMU guest，本段未實測）** 用 Ch 51 的 ftrace `function_graph`，對 `vfs_read` 設 filter 後在 guest 裡 `cat` 一個 ext4 檔案和一個 `/proc` 檔案，比對兩份 call graph 在哪一層分岔。預期：ext4 會出現 `ext4_file_read_iter → generic_file_read_iter → filemap_read`，procfs 會出現 `seq_read_iter`。

## 本章重點整理

- VFS 用四個物件建模：superblock（檔案系統實例）、inode（檔案本體）、dentry（名字）、file（一次 open），每個都帶自己的 ops table。
- 分層邊界的固定長相是「通用前置檢查 → 分派到 ops → 通用後置處理」；看到 `->xxx_op->` 就是在跨層。
- kernel 的分層不是單向的：具體檔案系統常常「先做自己的事，再呼叫通用零件」，通用零件又透過 `a_ops` 回頭叫具體實作。
- 很多規則被編碼在 ops table 的**內容**裡（例如 `generic_read_dir` 回 `-EISDIR`），在控制流裡找不到。
- 定位層級的三個線索：檔案路徑、函式前綴（`vfs_`/`generic_`/`ext4_`）、它被直接呼叫還是只被 ops table 引用。

## 自我檢核

- [ ] 不看筆記，畫出 `open()` + `read()` 從 syscall 到 `ext4_read_folio` 經過的函式，並在圖上標出每一條 ops table 邊界。
- [ ] 如果有人問你「為什麼 `struct file` 的 f_op 不直接用 inode 的就好，要複製一份」，你能舉出至少一種 f_op 和 i_fop 不同的情況嗎？
- [ ] 能解釋為什麼 ramfs 的 `read_iter` 可以直接填 `generic_file_read_iter`，而 ext4 要包一層？
- [ ] 隨便打開 `fs/` 底下一個你沒讀過的函式，能在一分鐘內說出它是通用層、通用零件、還是具體實作，並說出你的判斷依據？
- [ ] 想一個你工作中用過的分層系統（網路框架、ORM、HAL），它的「ops table 邊界」在哪裡？有沒有「通用零件 + 覆寫」的結構？

## 延伸閱讀

### 官方文件

- **[Overview of the Linux Virtual File System](https://docs.kernel.org/filesystems/vfs.html)**（tree 內：`Documentation/filesystems/vfs.rst`）
  - **讀哪裡**：Introduction 到 Registering and Mounting a Filesystem 先讀，建立四大物件的整體圖；接著把 `struct file_operations`、`struct inode_operations`、`struct address_space_operations` 三節當字典，讀 code 時遇到某個 ops 欄位就回來查它的語意與鎖規則。
  - **注意**：文件裡的欄位列表偶爾落後於 `fs.h`（例如新欄位還沒補文件），以 v6.18 原始碼為準。
- **[Pathname lookup](https://docs.kernel.org/filesystems/path-lookup.html)**（tree 內：`Documentation/filesystems/path-lookup.rst`）
  - **讀哪裡**：前半解釋 path walk 的整體設計與 dcache，後半的 RCU-walk 需要先讀完 Ch 31。
  - **和本章的關聯**：本章 open 路徑裡 `link_path_walk` 那一格的完整展開。

### 論文

- **Vnodes: An Architecture for Multiple File System Types in Sun UNIX** — S. R. Kleiman，USENIX Summer Conference（1986）
  - **核心貢獻**：提出 vnode/vfs 抽象層，讓 UFS、NFS 等多種檔案系統共存於同一個 UNIX kernel，是今天 Linux VFS 的思想源頭。
  - **讀哪裡**：設計目標與 vnode 操作列表那兩節，拿來和 v6.18 的 `inode_operations`／`file_operations` 對照，會發現很多欄位在 1986 年就有了。
- **On the Criteria To Be Used in Decomposing Systems into Modules** — D. L. Parnas，Communications of the ACM（1972）
  - **核心貢獻**：主張以「隱藏會變動的設計決策」作為模組切分準則，而不是依處理流程切分。
  - **和本章的關聯**：VFS 的每一張 ops table 背後就是一個被隱藏的決策（資料存在哪、怎麼查名字）。讀過這篇，你會用「這一層在隱藏什麼」來理解任何分層。

### 書籍

- **《Code Reading: The Open Source Perspective》** — Diomidis Spinellis（Addison-Wesley, 2003）
  - **讀哪幾章**：Ch 9.1「System Structures」中談分層（layered architectures）的部分。原書例子是網路協定堆疊與 BSD kernel，觀念和本章一致。
- **《Linux Kernel Development》（3rd ed.）** — Robert Love（Addison-Wesley, 2010）
  - **讀哪幾章**：Ch 13「The Virtual Filesystem」。基於 2.6.34，函式名稱有不少已改（例如 `->read_iter`、folio 都是之後才有），但四大物件的概念解釋清楚，適合當入門背景；細節一律以 v6.18 原始碼為準。

分層讓我們知道「呼叫會在哪裡斷」，但沒說明那些 ops table 和 `EXT4_I()` 這類轉換在 C 語言層面到底是什麼機制。下一章把它攤開：C 沒有 class，kernel 怎麼做出封裝、繼承和多型。

→ [Ch 42 C 語言裡的物件導向：struct 嵌入、vtable、kobject](./42-object-oriented-c.md)
