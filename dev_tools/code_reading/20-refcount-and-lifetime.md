# Ch 20 — refcount 與物件生命週期：kref、get/put 配對

> **目標**：讀到 `get_file()`、`fput()`、`dget()`、`kref_put(&x->kref, x_release)`、`refcount_inc_not_zero()` 時，能回答「這行之後誰多拿了一份參考、這份參考在哪裡還、最後一份還掉時誰負責拆物件」，並能用工具驗證 get/put 有沒有配對。以 `struct file` 為主線，從 v6.18 的 `fs/file_table.c` 與 `fs/file.c` 追完它的一生。

> **鴻溝**：本章主要練**慣用法鴻溝**（get/put 命名、`kref` + release callback）與**間接鴻溝**（release callback 和 `f_op->release` 是透過指標呼叫的，追到 `kref_put` 就斷）。`file_ref_t` 與 `SLAB_TYPESAFE_BY_RCU` 的部分也碰到**脈絡鴻溝**。

> **環境**：Linux v6.18 原始碼，x86_64 kernel 在 QEMU（TCG）中執行；用 ftrace 觀察 `__fput` 的呼叫路徑，用自己編的 module 觀察 `refcount_t` 的飽和行為。

## 為什麼需要這個？

user-space 的所有權通常是一棵樹：誰 `malloc` 誰 `free`，或者明確交給下一個人（[Ch 12](./12-memory-ownership-conventions.md)）。kernel 裡的物件卻是一張網：

- 一個 `struct file` 同時被 fd table 指著、被正在執行 `read()` 的 thread 拿著、被 epoll 掛著、被 `mmap` 出來的 VMA 引用；
- 這些持有者各自在不同 CPU 上、不同時間點放手；
- 沒有人知道自己是不是最後一個。

如果用「某個擁有者負責 free」的模型，你得在 free 之前確認所有人都放手了，這需要全域的 lock，而且擁有者本身可能先走。kernel 的答案是 **reference counting**：物件內嵌一個計數器，每個持有者 get 時加一、put 時減一，**減到零的那個人負責拆**。擁有者的概念被打散成「每一份參考都是一個擁有者」。

對讀碼的人而言，refcount 是生命週期的帳本。讀不懂它，你就沒辦法回答 kernel 裡最常被問的一類問題：「這個指標在這裡用安全嗎？會不會已經被 free 了？」——這也是 use-after-free 漏洞的問題。

## 先建立直覺

想像圖書館的一本書，借書卡上記著目前有幾個人借：

```
     get                put               put
 ┌────────┐  +1   ┌────────┐  -1   ┌────────┐  -1   ┌──────────────┐
 │ count=1│ ────► │ count=2│ ────► │ count=1│ ────► │ count=0      │
 │ (建立者)│       │ A 也借  │       │ A 還了  │       │ 最後一人還書  │
 └────────┘       └────────┘       └────────┘       │ → 呼叫release│
                                                    │ → 書被銷毀    │
                                                    └──────────────┘
```

三條規則：

1. **你手上有一份參考，物件就保證活著**。不需要拿任何 lock。
2. **要多給別人一份，你自己得先有一份**（從零開始 get 是錯的——那代表物件可能已經在被銷毀）。
3. **最後一個 put 的人負責銷毀**，而且銷毀的方式由物件自己定義（release callback）。

規則 2 有一個例外，也是本章最難的部分：從某個查找結構（hash table、fd table）裡**找到**一個物件時，你手上還沒有參考。這時用的是「**如果還沒歸零才加一**」——`refcount_inc_not_zero`。

## 命名慣例：看到 get/put 就該找另一半

kernel 的 refcount API 幾乎都遵守 `xxx_get` / `xxx_put` 配對命名，而且 **get 通常回傳同一個指標**，讓你可以寫 `p = foo_get(q);`：

| 物件 | 拿一份 | 還一份 | 最後一份還掉時 |
|---|---|---|---|
| `struct file` | `get_file(f)` | `fput(f)` | `__fput()` |
| `struct dentry` | `dget(d)` | `dput(d)` | `dentry_kill()` 等 |
| `struct kobject` | `kobject_get(k)` | `kobject_put(k)` | `kobject_release()` → `ktype->release` |
| `struct task_struct` | `get_task_struct(t)` | `put_task_struct(t)` | `__put_task_struct()` |
| `struct cred` | `get_cred(c)` | `put_cred(c)` | `__put_cred()` |
| `struct user_struct` | `get_current_user()` | `free_uid(u)` | 名字不對稱，但仍是一對 |
| `struct page` | `get_page(p)` | `put_page(p)` | 回到 buddy |
| 一般物件 | `kref_get(&x->kref)` | `kref_put(&x->kref, release)` | `release(kref)` |

兩個看起來像例外、其實是同一個模式的情況：

- `get_current_user()` 配 `free_uid()`——名字不對稱。[Ch 19](./19-kernel-memory-allocation.md) 引用的 `alloc_pipe_info` 就有這一對。遇到不對稱命名，讀 get 函式的本體，看它增加的是哪個欄位，再用 `rg` 找誰減少同一個欄位。
- `dget` 不是對 `refcount_t` 操作，而是 `lockref`：

```c
/* include/linux/dcache.h:347-352（v6.18） */
static inline struct dentry *dget(struct dentry *dentry)
{
	if (dentry)
		lockref_get(&dentry->d_lockref);
	return dentry;
}
```

`lockref` 把 spinlock 和計數器塞在同一個 64-bit 字裡，可以用一次 cmpxchg 在不拿鎖的情況下改計數。底層換了，**get/put 的契約沒換**——這就是讀碼時抓命名慣例的價值：你不需要先懂 lockref，就能讀懂 `dget`/`dput` 的語意。

另一個慣例：get 函式多半**容忍 NULL**（`dget`、`kobject_get` 都先檢查 `if (dentry)`、`if (kobj)`），put 函式也一樣。所以讀到 `dput(NULL)` 不是 bug，錯誤路徑經常靠這個省掉判斷。

## refcount_t：為什麼不用 atomic_t

早期 kernel 的計數器都是 `atomic_t`。問題在於 `atomic_t` 是通用整數，**會繞回**。如果某條路徑有 bug——每次呼叫多 get 一次、少 put 一次——攻擊者可以反覆觸發它，讓計數從 `INT_MAX` 繞到負數再繞回 0 附近。接著一次正常的 put 讓計數歸零，物件被 free，但實際上還有大量指標指著它：use-after-free。這不是理論，refcount 溢位是有實際 CVE 的漏洞類型。

`refcount_t` 是專門為計數設計的型別，核心差異是**飽和**（saturation）：

```c
/* include/linux/refcount.h:1-27（v6.18，節錄） */
/*
 * Variant of atomic_t specialized for reference counts.
 *
 * The interface matches the atomic_t interface (to aid in porting) but only
 * provides the few functions one should use for reference counting.
 *
 * Saturation semantics
 * ====================
 *
 * refcount_t differs from atomic_t in that the counter saturates at
 * REFCOUNT_SATURATED and will not move once there. This avoids wrapping the
 * counter and causing 'spurious' use-after-free issues. ...
 *
 * 	                           INT_MAX     REFCOUNT_SATURATED   UINT_MAX
 *   0                          (0x7fff_ffff)    (0xc000_0000)    (0xffff_ffff)
 *   +--------------------------------+----------------+----------------+
 *                                     <---------- bad value! ---------->
 */
```

```c
/* include/linux/refcount.h:115（v6.18） */
#define REFCOUNT_SATURATED	(INT_MIN / 2)
```

加法的實作是「先做、事後檢查」，避免在熱路徑上用 cmpxchg 迴圈：

```c
/* include/linux/refcount.h:280-292（v6.18） */
static inline __signed_wrap
void __refcount_add(int i, refcount_t *r, int *oldp)
{
	int old = atomic_fetch_add_relaxed(i, &r->refs);

	if (oldp)
		*oldp = old;

	if (unlikely(!old))
		refcount_warn_saturate(r, REFCOUNT_ADD_UAF);
	else if (unlikely(old < 0 || old + i < 0))
		refcount_warn_saturate(r, REFCOUNT_ADD_OVF);
}
```

讀這段時注意兩個細節：

- `old + i < 0` 在標準 C 裡是 signed overflow，屬於未定義行為；編譯器有權假設它不會發生而把整個條件刪掉。kernel 能這樣寫，是因為頂層 `Makefile` 有 `KBUILD_CFLAGS += -fno-strict-overflow`（v6.18 `Makefile:1065`），而 `__signed_wrap` 讓 UBSAN 不對這裡報警。我在 user-space 照抄這段邏輯時，沒加 `-fno-strict-overflow` 的 `-O2` 版本**真的偵測不到溢位**，加了才正確——kernel 的編譯選項是程式碼語意的一部分。
- `!old` 的檢查：對一個已經是 0 的 refcount 做加法，代表有人對「已在銷毀中」的物件 get。這直接標成 UAF。

### 歷史：什麼時候換的

```
$ c=$(git log --format=%h -- include/linux/refcount.h | tail -1)
$ git log -1 --format='%h %ad %an: %s' --date=short $c
f405df5de3170 2016-11-14 Peter Zijlstra: refcount_t: Introduce a special purpose refcount type
$ git tag --contains $c | sort -V | head -1
v4.11
```

同一天還有 `10383aea2f44 kref: Implement 'struct kref' using refcount_t`，所以從 v4.11 起 `kref` 底下就是 `refcount_t`。之後幾年是大規模的 `atomic_t` → `refcount_t` 轉換潮，例如 `task_struct.usage` 在 `ec1d281923cf`（進入 v5.1）才換。讀舊程式碼或舊版本時，看到 `atomic_t` 當計數器不代表錯，但如果你在審查**新**程式碼時看到 `atomic_inc`/`atomic_dec_and_test` 被用來管生命週期，可以問一句為什麼不用 `refcount_t`。

### 在 kernel 裡親眼看飽和

我們寫一個 module 在 QEMU 裡載入（僅在自己的 VM 中操作）：

```c
/* refdemo.c — 以 make -C ~/code_reading/linux O=~/code_reading/build M=$PWD modules 建置 */
#include <linux/module.h>
#include <linux/refcount.h>
#include <linux/atomic.h>
#include <linux/limits.h>

static int __init refdemo_init(void)
{
	atomic_t a = ATOMIC_INIT(INT_MAX);
	refcount_t r = REFCOUNT_INIT(INT_MAX);
	refcount_t z = REFCOUNT_INIT(1);

	atomic_inc(&a);
	pr_info("refdemo: atomic_t   INT_MAX+1 -> %d\n", atomic_read(&a));

	refcount_inc(&r);
	pr_info("refdemo: refcount_t INT_MAX+1 -> %d (0x%x)\n",
		refcount_read(&r), (unsigned int)refcount_read(&r));
	pr_info("refdemo: dec_and_test on saturated -> %d\n",
		refcount_dec_and_test(&r));

	pr_info("refdemo: dec_and_test 1->0 -> %d\n", refcount_dec_and_test(&z));
	refcount_inc(&z);   /* 已經歸零的物件又被 get：典型 UAF 前兆 */
	return 0;
}
static void __exit refdemo_exit(void) { }
module_init(refdemo_init);
module_exit(refdemo_exit);
MODULE_LICENSE("GPL");
```

guest 內 `busybox insmod /bin/refdemo.ko` 之後的 dmesg（真實輸出，stack trace 只保留模組那一行）：

```
[    2.257131] refdemo: atomic_t   INT_MAX+1 -> -2147483648
[    2.257257] refcount_t: saturated; leaking memory.
[    2.257497] WARNING: CPU: 0 PID: 71 at lib/refcount.c:22 refcount_warn_saturate+0x57/0x130
[    2.259305]  refdemo_init+0x133/0xff0 [refdemo]
[    2.260339] refdemo: refcount_t INT_MAX+1 -> -1073741824 (0xc0000000)
[    2.260384] refcount_t: underflow; use-after-free.
[    2.260414] WARNING: CPU: 0 PID: 71 at lib/refcount.c:28 refcount_warn_saturate+0xd0/0x130
[    2.260568]  refdemo_init+0x16c/0xff0 [refdemo]
[    2.260885] refdemo: dec_and_test on saturated -> 0
[    2.260916] refdemo: dec_and_test 1->0 -> 1
[    2.260946] refcount_t: addition on 0; use-after-free.
[    2.260973] WARNING: CPU: 0 PID: 71 at lib/refcount.c:25 refcount_warn_saturate+0x7e/0x130
```

逐行對照：

- `atomic_t` 默默繞到 `INT_MIN`，沒有任何警告。
- `refcount_t` 被釘在 `0xc0000000`（`REFCOUNT_SATURATED`），並印出 WARNING。
- 對飽和值做 `dec_and_test` 回傳 0——**物件永遠不會被 free**。kernel 選擇洩漏而不是 UAF。注意它印的是「underflow」而不是「saturated」：飽和值在 signed 下是負數，`__refcount_sub_and_test` 看到 `old <= 0` 就走 UAF 警告分支（`include/linux/refcount.h:399`）。訊息措辭有點誤導，但行為正確。
- 對 0 做 `inc`：「addition on 0; use-after-free」。在真實程式碼裡看到這個 WARNING，代表某處有人在物件死後還拿著指標。

## kref：refcount + release callback

`kref` 是在 `refcount_t` 外面包一層，強制你提供 release 函式：

```c
/* include/linux/kref.h:43-69（v6.18） */
static inline void kref_get(struct kref *kref)
{
	refcount_inc(&kref->refcount);
}

/**
 * kref_put - Decrement refcount for object
 * @kref: Object
 * @release: Pointer to the function that will clean up the object when the
 *	     last reference to the object is released.
 *
 * Decrement the refcount, and if 0, call @release.  The caller may not
 * pass NULL or kfree() as the release function.
 ...
 */
static inline int kref_put(struct kref *kref, void (*release)(struct kref *kref))
{
	if (refcount_dec_and_test(&kref->refcount)) {
		release(kref);
		return 1;
	}
	return 0;
}
```

release callback 的典型寫法用 [Ch 16](./16-container-of.md) 的 `container_of` 從 `kref` 回到外層物件。`kobject` 是 kref 最大的使用者：

```c
/* lib/kobject.c:730-739（v6.18） */
void kobject_put(struct kobject *kobj)
{
	if (kobj) {
		if (!kobj->state_initialized)
			WARN(1, KERN_WARNING
				"kobject: '%s' (%p): is not initialized, yet kobject_put() is being called.\n",
			     kobject_name(kobj), kobj);
		kref_put(&kobj->kref, kobject_release);
	}
}
```

這裡是**間接鴻溝**的第一層：`kobject_release` 最後會呼叫 `kobj->ktype->release`，那是每一種 kobject 自己填的函式指標。要知道「最後一個 put 會做什麼」，你得找到這個 kobject 是用哪個 `kobj_type` 初始化的——那是 [Ch 28](./28-ops-tables-indirect-calls.md) 的找賦值點技巧，[Ch 42](./42-object-oriented-c.md) 會把 kobject 當成 C 的物件系統完整讀一遍。

`kref_put` 的註解說「may not pass NULL or kfree() as the release function」。`kfree` 不行的原因是 `kref` 通常不在物件開頭，`kfree(kref)` 會傳錯位址；就算剛好在開頭，release 函式也是你記錄「這個物件怎麼拆」的唯一地方，直接給 `kfree` 等於放棄這個機會。

## 底層機制：追 struct file 的一生

這是本章的主線。我們要回答：一個 `struct file` 從哪裡生、誰拿著它、最後誰拆它。

### 欄位：f_ref 與一個生命週期 union

```c
/* include/linux/fs.h:1211-1252（v6.18，節錄） */
struct file {
	spinlock_t			f_lock;
	fmode_t				f_mode;
	const struct file_operations	*f_op;
	...
	union {
		struct callback_head	f_task_work;
		struct llist_node	f_llist;
		struct file_ra_state	f_ra;
		freeptr_t		f_freeptr;
	};
	file_ref_t			f_ref;
	/* --- cacheline 3 boundary (192 bytes) --- */
} __randomize_layout
  __attribute__((aligned(4)));	/* lest something weird decides that 2 is OK */
```

兩個讀點：

1. 計數器是 `file_ref_t f_ref`，不是 `refcount_t`。v6.12 以前它叫 `atomic_long_t f_count`（`git show v6.12:include/linux/fs.h` 可以看到），v6.13 換成專用的 `file_ref_t`（commit `08ef26ea9ab3 fs: add file_ref` 與 `90ee6ed776c0 fs: port files to file_ref`，作者 Christian Brauner）。所以**網路上大部分講 `f_count` 的文章，對 v6.13 以後的 kernel 已經過時**。
2. 那個 union 是生命週期資訊：`f_ra`（readahead 狀態）只在檔案開著時有用；`f_task_work` 和 `f_llist` 只在**最後一個 put 之後**、排隊等待 `__fput` 時用；`f_freeptr` 只在物件回到 slab 後用。四者的生命週期互不重疊，所以可以共用空間。**union 成員的選擇本身就是在告訴你物件的狀態機**（[Ch 11](./11-structs-unions-tagged.md) 的 tagged union 觀念，只是這裡的 tag 是「現在處在哪個生命階段」）。

### 誕生：alloc_empty_file → init_file

```c
/* fs/file_table.c:194-199（v6.18，init_file 內） */
	/*
	 * We're SLAB_TYPESAFE_BY_RCU so initialize f_ref last. While
	 * fget-rcu pattern users need to be able to handle spurious
	 * refcount bumps we should reinitialize the reused file first.
	 */
	file_ref_init(&f->f_ref, 1);
```

出生時計數為 1，這一份參考屬於呼叫 `alloc_empty_file` 的人。註解裡的「initialize f_ref last」是脈絡：因為 `filp` cache 是 `SLAB_TYPESAFE_BY_RCU`（[Ch 19](./19-kernel-memory-allocation.md) 的 `files_init`），一塊剛被 free 的 `struct file` 記憶體可能立刻被重用成新的 file，而另一個 CPU 上的 lockless 查找可能**還拿著舊指標**正要對它 get。所以必須先把其他欄位都設好，最後才讓計數變成「活的」。

### 交接：fd_install 吃掉一份參考

`open(2)` 的最後一步是把 file 放進 fd table：

```c
/* fs/file.c:645-652（v6.18） */
/**
 * fd_install - install a file pointer in the fd array
 * @fd: file descriptor to install the file in
 * @file: the file to install
 *
 * This consumes the "file" refcount, so callers should treat it
 * as if they had called fput(file).
 */
void fd_install(unsigned int fd, struct file *file)
```

「This consumes the "file" refcount」是讀碼時必須抓到的一句話：呼叫 `fd_install` 之後，那一份參考**不再屬於你**，而屬於 fd table。如果你在 `fd_install` 之後還用 `file`，你是在借 fd table 的參考——而另一個 thread 隨時可以 `close(fd)` 把它拿走。這類「交出參考的函式」在 kernel 很多，kernel-doc 通常會寫「consumes」或「steals」一類的字眼，讀到要停下來。

### 借用：fdget 與 borrowed/cloned

`read(2)` 需要暫時拿到 file。v6.18 的寫法：

```c
/* fs/read_write.c:704-720（v6.18，節錄） */
ssize_t ksys_read(unsigned int fd, char __user *buf, size_t count)
{
	CLASS(fd_pos, f)(fd);
	ssize_t ret = -EBADF;

	if (!fd_empty(f)) {
		...
		ret = vfs_read(fd_file(f), buf, count, ppos);
		...
	}
	return ret;
}
```

`CLASS(fd_pos, f)(fd)` 是 `cleanup.h` 的 class（[Ch 17](./17-kernel-error-conventions.md)），等同「`f = fdget_pos(fd)`，離開 scope 時自動 `fdput_pos(f)`」。有趣的是 `struct fd` 的設計：

```c
/* include/linux/file.h:38-63（v6.18，節錄） */
struct fd {
	unsigned long word;
};
#define FDPUT_FPUT       1
#define FDPUT_POS_UNLOCK 2

#define fd_file(f) ((struct file *)((f).word & ~(FDPUT_FPUT|FDPUT_POS_UNLOCK)))
...
static inline void fdput(struct fd fd)
{
	if (unlikely(fd.word & FDPUT_FPUT))
		fput(fd_file(fd));
}
```

指標的低兩個 bit 被拿來存旗標（`struct file` 至少 4-byte 對齊，就是 `fs.h` 那行 `aligned(4)` 註解的用意）。`FDPUT_FPUT` 表示「我真的拿了一份參考，結束時要 `fput`」。什麼時候**不拿**參考？

```c
/* fs/file.c:1153-1178（v6.18，節錄） */
static inline struct fd __fget_light(unsigned int fd, fmode_t mask)
{
	struct files_struct *files = current->files;
	struct file *file;

	/*
	 * If another thread is concurrently calling close_fd() followed
	 * by put_files_struct(), we must not observe the old table
	 * entry combined with the new refcount - otherwise we could
	 * return a file that is concurrently being freed.
	 *
	 * atomic_read_acquire() pairs with atomic_dec_and_test() in
	 * put_files_struct().
	 */
	if (likely(atomic_read_acquire(&files->count) == 1)) {
		file = files_lookup_fd_raw(files, fd);
		if (!file || unlikely(file->f_mode & mask))
			return EMPTY_FD;
		return BORROWED_FD(file);
	} else {
		file = __fget_files(files, fd, mask);
		if (!file)
			return EMPTY_FD;
		return CLONED_FD(file);
	}
}
```

這是「誰持有參考」推理的漂亮示範：如果 fd table 沒有被其他 thread 共用（`files->count == 1`），那麼在這個 syscall 執行期間，**沒有別人能 close 這個 fd**——fd table 那一份參考保證 file 活著，我們借用它就好，省掉一次原子操作。只要 table 被共用，就老老實實 get 一份（`CLONED_FD`）。函式上方的註解列了借用的三個前提（不能在 syscall 結束後還記著、不能在中間 `filp_close`、不能在中間 clone），每一條都是在保證「fd table 那份參考在借用期間不會消失」。

### 死亡：fput → __fput

```c
/* fs/file_table.c:542-546（v6.18） */
void fput(struct file *file)
{
	if (unlikely(file_ref_put(&file->f_ref)))
		__fput_deferred(file);
}
```

`file_ref_put` 回傳 true 代表「這是最後一份」。但 `fput` 不直接拆物件，而是**延後**：

```c
/* fs/file_table.c:518-540（v6.18） */
static void __fput_deferred(struct file *file)
{
	struct task_struct *task = current;

	if (unlikely(!(file->f_mode & (FMODE_BACKING | FMODE_OPENED)))) {
		file_free(file);
		return;
	}

	if (likely(!in_interrupt() && !(task->flags & PF_KTHREAD))) {
		init_task_work(&file->f_task_work, ____fput);
		if (!task_work_add(task, &file->f_task_work, TWA_RESUME))
			return;
		/*
		 * After this task has run exit_task_work(),
		 * task_work_add() will fail.  Fall through to delayed
		 * fput to avoid leaking *file.
		 */
	}

	if (llist_add(&file->f_llist, &delayed_fput_list))
		schedule_delayed_work(&delayed_fput_work, 1);
}
```

為什麼要延後？因為 `__fput` 要做的事很重：

```c
/* fs/file_table.c:442-483（v6.18，節錄） */
static void __fput(struct file *file)
{
	...
	might_sleep();

	fsnotify_close(file);
	eventpoll_release(file);
	locks_remove_file(file);
	...
	if (file->f_op->release)
		file->f_op->release(inode, file);
	...
	fops_put(file->f_op);
	file_f_owner_release(file);
	put_file_access(file);
	dput(dentry);
	...
	mntput(mnt);
out:
	file_free(file);
}
```

`might_sleep()` 說明它會睡，而 `fput` 可能在任何 context 被呼叫——拿著 spinlock、在 IRQ 裡。所以 `fput` 把真正的拆解排到兩個安全的地方之一：

- **task_work**：掛在當前 task 上，task 返回 user-space 前執行（`TWA_RESUME`）。這時沒有 kernel lock、可以睡。
- **delayed work**：IRQ 或 kernel thread 沒有「返回 user-space」這個時機，就丟給 workqueue。

這也解釋了 union 為什麼放 `f_task_work` 和 `f_llist`：它們正是這兩條延後路徑的掛鉤。

`__fput` 自己又是一長串 put：`dput(dentry)`、`mntput(mnt)`、`fops_put(file->f_op)`（放掉 module 參考）、`put_cred`（在 `file_free` 裡）。**一個物件死亡時，它對別人持有的參考也一併還掉**——這就是參考形成的網，release 函式是網上的邊被拆掉的地方。`file->f_op->release(inode, file)` 是間接呼叫，pipe 的話會走到 `pipe_release`，那是 [Final Project](./final-project-pipe-eventfd-guided-reading.md) 會讀到的地方。

### 用 ftrace 驗證兩條死亡路徑

光讀程式碼，我們推論出 `__fput` 可能從 task_work 被叫到。驗證一下：在 QEMU guest 裡對 `__fput` 下 function tracer 並打開 stack trace，然後執行 `cat /proc/version`：

```sh
T=/sys/kernel/tracing
echo '__fput' > $T/set_ftrace_filter
echo function > $T/current_tracer
echo 1 > $T/options/func_stack_trace
echo 1 > $T/tracing_on
cat /proc/version > /dev/null
echo 0 > $T/tracing_on
```

真實輸出（節錄 `cat` 這個 process 的兩筆）：

```
cat-69 [000] ..... 2.596503: __fput <-__x64_sys_close
cat-69 [000] ..... 2.596594: <stack trace>
 => __fput
 => __x64_sys_close
 => do_syscall_64
 => entry_SYSCALL_64_after_hwframe
cat-69 [000] ..... 2.596985: __fput <-task_work_run
cat-69 [000] ..... 2.597021: <stack trace>
 => __fput
 => task_work_run
 => do_exit
 => do_group_exit
 => __x64_sys_exit_group
```

兩條路徑都出現了，而且第一條**不是**我們預期的 task_work：`cat` 自己 `close()` 檔案時，`__fput` 直接在 `close` syscall 裡同步執行。回去讀 `close` 就明白了：

```c
/* fs/open.c:1574-1590（v6.18，節錄） */
SYSCALL_DEFINE1(close, unsigned int, fd)
{
	...
	file = file_close_fd(fd);
	if (!file)
		return -EBADF;

	retval = filp_flush(file, current->files);

	/*
	 * We're returning to user space. Don't bother
	 * with any delayed fput() cases.
	 */
	fput_close_sync(file);
```

`close(2)` 本來就在可睡的 syscall context、馬上要回 user-space，延後沒有意義，所以用同步版本 `fput_close_sync`（v6.15 起的最佳化，commit `e83588458f65`）。第二筆是 process 結束時，沒被明確 close 的 fd 在 `do_exit` 裡由 task_work 清掉。

這個小實驗示範了 [Ch 3](./03-question-driven-reading.md) 的假說驗證迴圈：讀碼提出假說（`__fput` 從 task_work 來）→ 動態觀察 → 發現假說不完整 → 回頭讀碼找到原因（`close` 用同步版本）。

## 拿參考的難題：refcount_inc_not_zero 與 _rcu 查找

回到規則 2 的例外。fd table 是用 RCU 保護的（[Ch 31](./31-reading-rcu-code.md)），多 thread 的 process 查 fd 時不拿鎖。問題：你從表裡讀到一個 `struct file *`，在你 get 之前，另一個 thread 可能已經 close 了它、計數歸零、甚至記憶體已被重用成另一個 file。

`refcount_inc_not_zero` 是一般解：

```c
/* include/linux/refcount.h:320-336（v6.18，節錄） */
/**
 * refcount_inc_not_zero - increment a refcount unless it is 0
 ...
 * Provides no memory ordering, it is assumed the caller has guaranteed the
 * object memory to be stable (RCU, etc.). ...
 */
static inline __must_check bool refcount_inc_not_zero(refcount_t *r)
```

注意 `__must_check`（[Ch 18](./18-annotations-and-sparse.md)）：忽略回傳值就是 bug，因為失敗代表物件已死。也注意「it is assumed the caller has guaranteed the object memory to be stable (RCU, etc.)」——`inc_not_zero` 只保證「不從 0 復活」，**不保證記憶體還在**。記憶體還在，要靠 RCU：物件被 free 之前要等一個 grace period，所以在 `rcu_read_lock()` 內，你讀到的指標指向的記憶體至少不會被還給系統。

`struct file` 更進一步，因為 `SLAB_TYPESAFE_BY_RCU` 只保證「記憶體還是一個 `struct file`」，**不保證是同一個 file**。看 fd 查找怎麼處理：

```c
/* fs/file.c:989-1050（v6.18，節錄） */
static inline struct file *__fget_files_rcu(struct files_struct *files,
       unsigned int fd, fmode_t mask)
{
	for (;;) {
		struct file *file;
		struct fdtable *fdt = rcu_dereference_raw(files->fdt);
		struct file __rcu **fdentry;
		...
		file = rcu_dereference_raw(*fdentry);
		file = (void *)(nospec_mask & (unsigned long)file);
		if (unlikely(!file))
			return NULL;

		/*
		 * Ok, we have a file pointer that was valid at
		 * some point, but it might have become stale since.
		 *
		 * We need to confirm it by incrementing the refcount
		 * and then check the lookup again.
		 ...
		 */
		if (unlikely(!file_ref_get(&file->f_ref)))
			continue;

		/*
		 * Such a race can take two forms:
		 *
		 *  (a) the file ref already went down to zero and the
		 *      file hasn't been reused yet or the file count
		 *      isn't zero but the file has already been reused.
		 *
		 *  (b) the file table entry has changed under us.
		 ...
		 * If so, we need to put our ref and try again.
		 */
		if (unlikely(file != rcu_dereference_raw(*fdentry)) ||
		    unlikely(rcu_dereference_raw(files->fdt) != fdt)) {
			fput(file);
			continue;
		}
		...
```

三步驟：**讀指標 → 嘗試 get → 重讀指標確認沒變**。如果 get 成功但重讀發現表格裡的指標變了，代表我們 get 到的可能是一個被重用的、別人的 file，馬上 `fput` 還掉並重試。這就是 `SLAB_TYPESAFE_BY_RCU` 的標準讀法：**get 成功之後還要驗明正身**。在 [Ch 19](./19-kernel-memory-allocation.md) 看到 cache flag 時我們說「一定有 lockless 查找路徑、必須重新驗證身分」，這裡就是那條路徑。

`file_ref_get` 本身也和 `refcount_inc_not_zero` 不同，它無條件加一，靠「dead zone」容忍對已死物件的誤加：

```c
/* include/linux/file_ref.h:78-109（v6.18，節錄） */
static __always_inline __must_check bool file_ref_get(file_ref_t *ref)
{
	/*
	 * Unconditionally increase the reference count with full
	 * ordering. The saturation and dead zones provide enough
	 * tolerance for this.
	 *
	 * If this indicates negative the file in question the fail can
	 * be freed and immediately reused due to SLAB_TYPSAFE_BY_RCU.
	 ...
	 */
	return !atomic_long_add_negative(1, &ref->refcnt);
}
```

無條件 `add` 比 `inc_not_zero` 的 cmpxchg 迴圈便宜，在多 thread 頻繁 `read()` 同一個 fd 的情況下差異明顯——這就是 v6.13 換掉 `atomic_long_t` 的動機（詳見 `08ef26ea9ab3` 的 commit message）。

## 讀碼時怎麼驗證 get/put 配對

給你一個函式，怎麼確認它沒有洩漏或多放參考？

1. **列出所有 get**：包括名字裡有 get 的、回傳「新參考」的查找函式（`fget`、`lookup_*`、`find_get_*`、`*_get_*`）、以及 `alloc_*` 回傳的初始參考。
2. **對每一條離開函式的路徑**（每個 `return`、每個 `goto` 標籤），確認：每份參考要嘛被 put、要嘛被**交出去**（存進某個結構、回傳給呼叫者、傳給「consumes」的函式）。
3. **交出去的要寫下來**：「這份參考從此由 X 持有，在 Y 釋放」。找不到 Y 就是可疑點。
4. **cleanup.h 會改變這個檢查**：`CLASS(fd, f)`、`__free(fput)`（`include/linux/file.h:97` 的 `DEFINE_FREE(fput, ...)`）讓 put 隱形地發生在 scope 結尾。看到它們，put 的位置是「宣告所在的 `}`」，交出去要找 `no_free_ptr` / `return_ptr` / `take_fd` 這類轉移。

工具上，`rg -n 'get_file|fput|fget' fs/xxx.c` 先列出所有呼叫點，再按函式分組。對一個 bug 假說（「這條錯誤路徑忘了 put」），可以用 [Ch 37](./37-git-archaeology.md) 的 pickaxe 找有沒有人修過類似的東西：refcount leak 的修補 commit 通常有 `Fixes:` tag，訊息裡有 "leak" 或 "missing put"。

動態上，refcount 錯誤最直接的證據就是剛才看到的 `refcount_t: addition on 0; use-after-free.` 一類 WARNING，以及 KASAN 的 use-after-free 報告。

## 對比與取捨

| 機制 | 優點 | 缺點 | 讀碼時代表什麼 |
|---|---|---|---|
| `atomic_t` 當計數 | 通用 | 會繞回，溢位即 UAF | 舊程式碼，或不是生命週期用途的計數 |
| `refcount_t` | 飽和、WARN | 比 atomic 多一點檢查成本 | 標準的生命週期計數 |
| `kref` | 強制 release callback | 多一層包裝 | 物件有自己的拆解邏輯 |
| `lockref`（dentry） | 計數和鎖合併，lockless 快速路徑 | 專用 | 熱到需要特化的物件 |
| `file_ref_t` | 無條件加、dead zone 容忍、適合 `TYPESAFE_BY_RCU` | 專用、語意更微妙 | 只有 `struct file` 用 |
| `percpu_ref` | get/put 幾乎零成本（per-CPU） | 切換到 atomic 模式時要一個 RCU grace period | 極熱的物件（cgroup、block request queue） |
| 不計數，靠 RCU | reader 零成本 | 只能讀，不能跨睡眠持有 | 讀多寫少的查找結構 |

## 踩雷集錦

1. **「拿到指標就等於拿到參考」**：錯。從全域結構讀到的指標只是一個**觀察**，不是參考。你要嘛在保護那個結構的 lock / RCU read section 內使用它，要嘛把它轉成參考（get / `inc_not_zero`）才能帶出去。
2. **「refcount_inc 一定安全」**：對一個你**沒有**參考的物件 `refcount_inc` 是錯的，即使當下計數不是 0——你檢查和加一之間它可能歸零。從查找結構拿參考要用 `inc_not_zero` 並檢查回傳值。
3. **「get 和 put 要在同一個函式」**：不必。參考可以跨函式、跨 thread、跨 syscall 傳遞。配對檢查是對**每一份參考**做，不是對每個函式做。`fd_install` 就是在 `open` 裡拿、在 `close` 裡還。
4. **「最後一個 put 會同步把物件拆掉」**：`fput` 就不是。拆解可能延後到 task_work 或 workqueue，所以「`fput` 返回」不代表 `->release` 已經跑完。依賴這個順序的程式碼要用同步版本（`__fput_sync`），而 `fs/file_table.c:555` 的註解明說「don't blindly convert fput() by kernel thread to that」。
5. **「`SLAB_TYPESAFE_BY_RCU` 等於 RCU 延後 free」**：不一樣。`kfree_rcu` 保證 grace period 內記憶體**還是同一個物件**；`TYPESAFE_BY_RCU` 只保證**還是同型別的某個物件**，可以被立即重用。後者一定要配合 get 後重新驗證。

## 進階：再往深一層

- **percpu_ref**：`include/linux/percpu-refcount.h`。平常每個 CPU 有自己的計數器，get/put 只動本地；要殺掉物件時切換成單一 atomic 計數。讀 block layer 和 cgroup 時會大量遇到。
- **rcuref**：`include/linux/rcuref.h`，`file_ref` 的設計靈感來源（`file_ref.h` 開頭的註解這樣說）。給「RCU 保護、要頻繁 get」的物件用，例如路由表項。
- **refcount 和 lockdep 的交互**：`kref_put_mutex` / `kref_put_lock`（`include/linux/kref.h` 第 71 行起）處理「release 需要從查找結構移除物件、而移除需要拿鎖」的情況，避免「最後一個 put 的人沒拿鎖、另一個 thread 同時在查找」的競態。讀到這兩個函式時，配合 `kref_get_unless_zero` 的註解一起看。
- **安全角度**：refcount 漏洞（多 put 一次、漏 get 一次）是 kernel UAF 最常見的成因之一。[kernel_pwn 課](../../security/kernel_pwn/README.md)從利用面講同一件事；讀碼時的 get/put 配對檢查，正是在找這類 bug。

## 動手練習

1. 在 `fs/file_table.c` 找到 `alloc_file_pseudo`，追它回傳的 file 在 `fs/pipe.c` 的 `create_pipe_files` 裡怎麼被交出去、錯誤路徑怎麼還。寫下每一份參考的持有者。
2. 用 `rg -n 'refcount_inc_not_zero|kref_get_unless_zero' kernel/ | head`，挑一個，往上找它處在哪一個 RCU read section 或 lock 內，說明為什麼這裡不能用普通的 `refcount_inc`。
3. 改寫本章的 ftrace 腳本，改成追 `pipe_release`，在 guest 裡跑 `echo hi | cat`，看 pipe 的讀寫兩端各在哪條路徑上被放掉。

## 本章重點整理

- refcount 把「一個擁有者」打散成「每一份參考都是擁有者」；最後一個 put 負責拆。
- get/put 命名配對是讀碼的抓手；名字不對稱時讀 get 本體找它動的欄位。
- `refcount_t`（v4.11 起）飽和不繞回，寧可洩漏也不 UAF；kernel 用 `-fno-strict-overflow` 讓那行溢位檢查成立。
- `struct file` 的計數是 v6.13 起的 `file_ref_t`；`fput` 的拆解延後到 task_work 或 workqueue，`close(2)` 走同步版本。
- 從查找結構拿參考要用 `inc_not_zero` 類函式並檢查結果；`SLAB_TYPESAFE_BY_RCU` 還要在 get 後重新驗證身分。

## 自我檢核

- [ ] 用自己的話解釋為什麼 `atomic_t` 的繞回會變成 use-after-free，而 `refcount_t` 的飽和會變成洩漏。這兩個後果哪個比較好？為什麼？
- [ ] 不看書，畫出 `struct file` 從 `alloc_empty_file` 到 `file_free` 的路徑，標出每個交接點（`fd_install`、`fdget`、`fput`），以及 `__fput` 可能從哪兩條延後路徑被呼叫。
- [ ] 解釋 `__fget_light` 在什麼條件下可以不拿參考，以及它列出的三條前提各自在防什麼。
- [ ] 有人在 review 裡寫：「這裡用 `refcount_inc` 就好，反正 count 一定大於 0。」你要問他哪個問題？
- [ ] `SLAB_TYPESAFE_BY_RCU` 和 `kfree_rcu` 的差別是什麼？為什麼前者要求查找方在 get 之後重讀指標？

## 延伸閱讀

### 官方文件

- **[Adding reference counters (krefs) to kernel objects](https://docs.kernel.org/core-api/kref.html)**（tree 內：`Documentation/core-api/kref.rst`）
  - **讀哪裡**：全文不長。重點是三條規則（「If you make a non-temporary copy of a pointer…」那段）以及後半講 `kref_get_unless_zero` 和查找結構的範例。
  - **和本章的關聯**：本章「先建立直覺」三條規則的官方版本，附完整範例程式。
- **[refcount_t API compared to atomic_t](https://docs.kernel.org/core-api/refcount-vs-atomic.html)**（tree 內：`Documentation/core-api/refcount-vs-atomic.rst`）
  - **讀哪裡**：逐一對照每個 refcount 函式和對應 atomic 函式的 memory ordering 差異。
  - **前提**：先讀完 [Ch 21](./21-read-once-and-barriers.md) 的 acquire/release 會比較好懂。
  - **和本章的關聯**：解釋 `refcount_dec_and_test` 為什麼「Provides release memory ordering … and provides an acquire ordering on success」。
- **`include/linux/file_ref.h` 與 `fs/file.c` 中 `__fget_files_rcu` 的註解**（v6.18 tree 內）
  - **讀哪裡**：`file_ref.h` 開頭的 zone 圖、`file_ref_get` 內的長註解；`__fget_files_rcu` 的兩段 race 說明。
  - **為什麼值得讀**：這是目前 kernel 裡把「TYPESAFE_BY_RCU + refcount」講得最仔細的第一手材料。

### 部落格 / 技術文章

- **LWN.net 的 refcount_t 系列報導**（作者 Jonathan Corbet 等，2016–2017；在 [LWN Kernel Index](https://lwn.net/Kernel/Index/) 的 "Reference counting" 條目下）
  - **這系列說什麼**：refcount_t 的引入動機與轉換過程的討論。就我所知，這批工作屬於 Kernel Self Protection Project 把 PaX/grsecurity 的 refcount 加固思路帶進主線的一環；細節請以 LWN 原文為準。
  - **讀哪裡**：從 Index 的 "Reference counting" 分類找 2016–2017 的文章。
  - **為什麼值得讀**：補上本章 `git log` 查不到的「為什麼當時有人反對」這層脈絡。

### 書籍

- **《Linux Device Drivers》（3rd ed.）** — Corbet, Rubini, Kroah-Hartman（O'Reilly, 2005）
  - **讀哪幾章**：第 14 章「The Linux Device Model」中 kobject 與 kref 的部分。API 細節（例如 kobject 的初始化函式）已改，但「kobject 的 release 由 ktype 決定、最後一個 put 觸發」的模型沒變。
  - **注意**：書是 2.6.10 時代，`refcount_t` 還不存在；當作 kobject 生命週期的概念導覽讀。

refcount 解決了「物件活多久」，但沒有解決「兩個 CPU 同時讀寫同一個欄位時，彼此看到什麼」。`refcount_dec_and_test` 的 kernel-doc 一直在講 release / acquire ordering，下一章就把這些詞拆開。

→ [Ch 21 READ_ONCE 與 memory barrier：那些看起來多餘的巨集](./21-read-once-and-barriers.md)
