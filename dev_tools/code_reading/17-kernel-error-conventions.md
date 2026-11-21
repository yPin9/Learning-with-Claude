# Ch 17 — kernel 的錯誤慣例：-errno、ERR_PTR、goto 階梯與 cleanup.h

> **目標**：讀到任何一個 kernel 函式，能立刻判斷它用哪一種錯誤回傳約定（`-errno`、NULL、`ERR_PTR`、或兩者皆可）、知道呼叫端該怎麼檢查；能解釋 `ERR_PTR` 為什麼可以把錯誤碼塞進指標；能讀懂 goto 錯誤階梯的反向釋放順序；能讀懂 v6.5 起出現的 `__free()`、`guard()`、`scoped_guard()`、`no_free_ptr()`，並把它們翻譯回舊的 goto 寫法。
> **鴻溝**：本章主要練**慣用法鴻溝**（`ERR_PTR`、goto 階梯、cleanup.h 都是 pattern），附帶**脈絡鴻溝**（cleanup.h 為什麼在 2023 年出現）。

> **環境**：Linux kernel v6.18；gcc 11.4、clang 14（WSL Ubuntu 22.04 x86_64）。本章 user-space 範例都在兩個編譯器上跑過。

## 為什麼需要這個？

C 沒有 exception。每個可能失敗的函式都要用**回傳值**告訴呼叫端「我失敗了，原因是什麼」，呼叫端也要在失敗時**手動**把已經拿到的資源一一還回去。user space 的慣例五花八門：回傳 -1 再看 `errno`、回傳 NULL、回傳 bool、用 out 參數……kernel 沒有 `errno` 這個全域變數（它是多執行緒、可搶佔、會被中斷打斷的環境，全域的錯誤狀態根本不可靠），所以它發展出一套自己的、全樹一致的約定。

讀碼時不懂這套約定，你會在每個函式的後半段迷路：一堆 `if (IS_ERR(x)) return PTR_ERR(x);`、一串 `goto err_free_foo;`、結尾三四個 label 堆在一起。這些其實都是**同一套規則**的不同面向，學會了就能用「略讀」的方式跳過它們，專注在函式真正的邏輯。

數字感：v6.18 的 `.c` 檔排除 `tools/` 後，`goto` 出現約 19 萬次；全樹 `.c`/`.h` 裡 `IS_ERR(` 約 3.4 萬次，`ERR_PTR(` 約 1.6 萬次。最常見的 label 名字依序是 `out:`（16214 次）、`err:`（3562）、`done:`、`error:`、`fail:`、`exit:`、`unlock:`、`out_unlock:`。這些數字是用 `rg` 粗估的（label 的 regex 會漏掉縮排不標準的寫法），但量級說明了一件事：**錯誤處理程式碼佔了 kernel 的很大一塊，讀得快它，整體閱讀速度就上來了。**

## 先建立直覺

把一個 kernel 函式想成「登山」：一路往上爬，每到一個營地就放下一些裝備（配置記憶體、拿鎖、註冊 callback）。如果在第三個營地遇到暴風雪要撤退，你必須**按相反順序**把第三、二、一營的裝備收回來，才能回到山下。

```
成功路徑（往下讀）                錯誤路徑（label 由下往上收）

  a = alloc_a();     ─────┐
  if (!a) return -ENOMEM; │
                          │
  b = alloc_b();          │        err_b:  ← 失敗在 c 時跳這裡
  if (!b) goto err_a; ─┐  │            free_b(b);
                       │  │        err_a:  ← 失敗在 b 時跳這裡
  c = alloc_c();       │  │            free_a(a);
  if (!c) goto err_b;  │  │            return -ENOMEM;
                       │  │
  return 0;            │  │
                       │  └─ 配置順序 a → b → c
                       └──── 釋放順序 c → b → a（label 反著排）
```

kernel 的錯誤處理就是三件事：

1. **怎麼回報失敗**：回傳整數用 `-errno`；回傳指標用 NULL 或 `ERR_PTR`。
2. **呼叫端怎麼檢查**：`if (ret)` / `if (ret < 0)` / `if (!p)` / `if (IS_ERR(p))`，取決於第 1 點。
3. **失敗時怎麼收拾**：goto 階梯（傳統），或 cleanup.h（v6.5 起）。

> 如果你對 goto 收尾的基本讀法還不熟，先回看 [Ch 8 — 控制流慣用法](./08-control-flow-idioms.md) 的「goto 收尾」一節；本章專注在 kernel 特有的約定。

## 約定一：回傳整數時，`0` 成功、負的 errno 失敗

```c
/* fs/pipe.c:990-992（v6.18） */
	error = create_pipe_files(files, flags);
	if (error)
		return error;
```

規則：

- `0` 表示成功。
- **負值**表示失敗，值是 errno 取負：`-ENOMEM`（−12）、`-EINVAL`（−22）、`-EFAULT`（−14）……
- 有些函式成功時回傳**非負的有用值**（讀了幾個 byte、新的 fd 號碼），這時檢查要寫 `if (ret < 0)`，不能寫 `if (ret)`。

```c
/* fs/pipe.c:994-997（v6.18） */
	error = get_unused_fd_flags(flags);
	if (error < 0)
		goto err_read_pipe;
	fdr = error;
```

`get_unused_fd_flags()` 成功時回傳 fd 號碼（可能是 0），所以這裡必須是 `< 0`。讀碼時看到 `if (ret)` 和 `if (ret < 0)` 混用，不是作者不一致，而是被呼叫的函式語意不同。

### errno 從哪來

一般的 errno 定義在 `include/uapi/asm-generic/errno-base.h` 和 `errno.h`（uapi，user space 也看得到），v6.18 最大的是 `EHWPOISON = 133`（`include/uapi/asm-generic/errno.h:121`）。kernel 另外有一批**只在 kernel 內部用**的 errno，從 512 開始：

```c
/* include/linux/errno.h:7-19（v6.18） */
/*
 * These should never be seen by user programs.  To return one of ERESTART*
 * codes, signal_pending() MUST be set.  Note that ptrace can observe these
 * at syscall exit tracing, but they will never be left for the debugged user
 * process to see.
 */
#define ERESTARTSYS	512
#define ERESTARTNOINTR	513
#define ERESTARTNOHAND	514	/* restart if no handler.. */
#define ENOIOCTLCMD	515	/* No ioctl command */
#define ERESTART_RESTARTBLOCK 516 /* restart by calling sys_restart_syscall */
#define EPROBE_DEFER	517	/* Driver requests probe retry */
```

讀碼時看到 `-ERESTARTSYS`，意思是「被 signal 打斷了，syscall 層會決定要重新執行還是回 `-EINTR`」；看到 `-EPROBE_DEFER`，意思是「driver 依賴的東西還沒準備好，driver core 晚點再試」。這些值**不應該**漏到 user space。

### syscall 邊界：kernel 回傳 `-9`，glibc 給你 `-1` 加 `errno = 9`

`-errno` 約定一路延伸到 syscall 的回傳值。用 inline asm 直接發 `syscall` 指令、繞過 glibc，就能看到 kernel 真正回傳的東西：

```c
/* rawsys.c */
#include <stdio.h>
#include <errno.h>
#include <string.h>
#include <unistd.h>
#include <sys/syscall.h>

/* 直接用 syscall 指令，繞過 glibc，看 kernel 真正回傳什麼 */
static long raw_syscall3(long nr, long a, long b, long c)
{
	long ret;
	asm volatile ("syscall"
		      : "=a"(ret)
		      : "a"(nr), "D"(a), "S"(b), "d"(c)
		      : "rcx", "r11", "memory");
	return ret;
}

int main(void)
{
	char buf[8];

	long r = raw_syscall3(SYS_read, -1, (long)buf, sizeof(buf));
	printf("raw read(-1)        -> %ld\n", r);

	r = raw_syscall3(SYS_read, 0, (long)0x10, sizeof(buf));  /* 不合法的 user 位址 */
	printf("raw read(0, 0x10)   -> %ld\n", r);

	errno = 0;
	ssize_t g = read(-1, buf, sizeof(buf));
	printf("glibc read(-1)      -> %zd, errno=%d (%s)\n", g, errno, strerror(errno));
	return 0;
}
```

```
$ gcc -O2 -Wall rawsys.c -o rawsys && ./rawsys <<< "hello"
raw read(-1)        -> -9
raw read(0, 0x10)   -> -14
glibc read(-1)      -> -1, errno=9 (Bad file descriptor)
```

x86-64 的 syscall ABI 把 nr 放 `rax`、前三個參數放 `rdi`/`rsi`/`rdx`，`syscall` 指令會破壞 `rcx`、`r11`。kernel 回傳 `-9`（`-EBADF`）和 `-14`（`-EFAULT`）；glibc 的 `read()` wrapper 把負值轉成 `-1` 並設定 `errno`。

一個有趣的邊界：同一支程式用 `./rawsys < /dev/null` 跑，第二行會印出 `0` 而不是 `-14`——stdin 已經 EOF，kernel 沒有東西要複製，根本沒去碰那個壞掉的位址（實測）。**錯誤是在「實際存取」時才被發現的**，這是 Ch 18 `__user` 指標的伏筆。

## 約定二：回傳指標時，三種情況

### (a) 失敗回傳 NULL

最典型的是記憶體配置：

```c
/* fs/eventfd.c:393-395（v6.18） */
	ctx = kmalloc(sizeof(*ctx), GFP_KERNEL);
	if (!ctx)
		return -ENOMEM;
```

`kmalloc` 失敗的原因只有一個（沒記憶體），所以不需要錯誤碼，NULL 就夠了。呼叫端自己決定要回報 `-ENOMEM`。

### (b) 失敗回傳 `ERR_PTR(-errno)`

失敗原因不只一種時，函式把錯誤碼**編碼進指標**：

```c
/* fs/pipe.c:944-951（v6.18） */
	f = alloc_file_pseudo(inode, pipe_mnt, "",
				O_WRONLY | (flags & (O_NONBLOCK | O_DIRECT)),
				&pipeanon_fops);
	if (IS_ERR(f)) {
		free_pipe_info(inode->i_pipe);
		iput(inode);
		return PTR_ERR(f);
	}
```

`IS_ERR(f)` 問「這個指標其實是錯誤碼嗎？」，`PTR_ERR(f)` 把它解碼回 `long`。產生端：

```c
/* fs/eventfd.c:348-354（v6.18） */
struct eventfd_ctx *eventfd_ctx_fdget(int fd)
{
	CLASS(fd, f)(fd);
	if (fd_empty(f))
		return ERR_PTR(-EBADF);
	return eventfd_ctx_fileget(fd_file(f));
}
```

（`CLASS(fd, f)(fd)` 是 cleanup.h 的寫法，本章後半會拆。）`eventfd_ctx_fileget()` 自己也可能回傳 `ERR_PTR(-EINVAL)`（fs/eventfd.c:370-371），錯誤就這樣沿著呼叫鏈原封不動地往上傳。

### (c) NULL 和 ERR_PTR 都可能

最麻煩的一種。VFS 的 `->lookup` 就是：

```
/* fs/dcache.c:3056-3057（v6.18），d_splice_alias() 的註解 */
 * If a dentry was found and moved, then it is returned.  Otherwise NULL
 * is returned.  This matches the expected return value of ->lookup.
```

而它在其他路徑上又會回傳 `ERR_PTR`。這裡 NULL 不是錯誤，而是「成功，但沒有別的 dentry 要給你，用你傳進來的那個」。呼叫端必須分三路處理：`IS_ERR` → 錯誤；NULL → 用原本的；其他 → 用回傳的。

檢查兩者的工具是 `IS_ERR_OR_NULL()`，v6.18 用了約 1600 次。它常見於「釋放函式」：

```c
/* include/linux/slab.h:512（v6.18） */
DEFINE_FREE(kfree, void *, if (!IS_ERR_OR_NULL(_T)) kfree(_T))
```

這行是 cleanup.h 的 `__free(kfree)` 定義：變數裡不管是 NULL 還是 ERR_PTR，離開 scope 時都不會被 `kfree`。

### 讀碼時怎麼知道是哪一種？

1. **讀函式的 kernel-doc 註解**，找 `Return:` 段落。
2. 沒有註解就**讀函式本體**，找所有 `return` 敘述。
3. **看呼叫端怎麼檢查**：大多數呼叫端是對的，`rg -n "= foo\(" -A2` 掃一下，多數人用 `IS_ERR` 那就是 ERR_PTR 型。

**最常見的 bug** 就是混用：對 ERR_PTR 型函式檢查 `if (!p)`（錯誤指標不是 NULL，會被當成有效指標使用），或對 NULL 型函式檢查 `IS_ERR(p)`（NULL 不是錯誤指標，之後解參考 NULL）。審 patch 的人會專門盯這個。

## `ERR_PTR` 的實作：為什麼 4095 個位址可以拿來編碼錯誤

v6.18 的 `include/linux/err.h` 核心部分：

```c
/* include/linux/err.h:10-18（v6.18） */
/*
 * Kernel pointers have redundant information, so we can use a
 * scheme where we can return either an error code or a normal
 * pointer with the same return value.
 *
 * This should be a per-architecture thing, to allow different
 * error and pointer decisions.
 */
#define MAX_ERRNO	4095

/* include/linux/err.h:28 */
#define IS_ERR_VALUE(x) unlikely((unsigned long)(void *)(x) >= (unsigned long)-MAX_ERRNO)

/* include/linux/err.h:39-42 */
static inline void * __must_check ERR_PTR(long error)
{
	return (void *) error;
}

/* include/linux/err.h:55-58 */
static inline long __must_check PTR_ERR(__force const void *ptr)
{
	return (long) ptr;
}

/* include/linux/err.h:68-71 */
static inline bool __must_check IS_ERR(__force const void *ptr)
{
	return IS_ERR_VALUE((unsigned long)ptr);
}

/* include/linux/err.h:82-85 */
static inline bool __must_check IS_ERR_OR_NULL(__force const void *ptr)
{
	return unlikely(!ptr) || IS_ERR_VALUE((unsigned long)ptr);
}
```

`ERR_PTR` 和 `PTR_ERR` 只是轉型，**沒有任何運算**。祕密全在 `IS_ERR_VALUE` 的比較：

```
64-bit 位址空間（unsigned 看）

0x0000000000000000  ┌──────────────────┐ ← NULL
                    │ user space ...   │
                    │ (non-canonical)  │
0xffff800000000000  │ kernel space ... │
                    │                  │
0xfffffffffffff000  ├──────────────────┤ ← 最後一個 page 的開頭（-4096）
0xfffffffffffff001  │ -4095 = -MAX_ERRNO│ ┐
       ...          │       ...        │ │ IS_ERR 為真的區間
0xffffffffffffffea  │ -22 = -EINVAL    │ │ 共 4095 個值
0xffffffffffffffff  │ -1  = -EPERM     │ ┘
                    └──────────────────┘
```

為什麼這段位址可以安全挪用？

1. **最高的那個 page 永遠不會有合法的 kernel 物件**。x86-64 的 kernel 記憶體佈局文件把最頂端的 2 MB 列為未使用（`Documentation/arch/x86/x86_64/mm.rst:83`：`ffffffffffe00000 | -2 MB | ffffffffffffffff | 2 MB | ... unused hole`）。err.h 的註解說「This should be a per-architecture thing」，但實務上所有架構都沿用這個 generic 定義，因為「位址空間最後一頁不放東西」在每個架構上都成立。
2. **4095 夠大**：errno 最大才 133，kernel 內部的特殊值也只到 500 多，離 4095 很遠。
3. **4095 = 4096 − 1**：剛好是一個 4 KB page 減一，讓錯誤區間完全落在最後一頁裡。

檢查只要一次 unsigned 比較，代價極低；而且回傳型別不用改，函式簽名維持「回傳指標」。

user-space 重現：

```c
/* errptr.c */
#include <stdio.h>
#include <stdlib.h>
#include <stdbool.h>
#include <errno.h>
#include <string.h>

/* ---- 仿 include/linux/err.h（v6.18） ---- */
#define MAX_ERRNO 4095
#define unlikely(x) __builtin_expect(!!(x), 0)
#define IS_ERR_VALUE(x) unlikely((unsigned long)(void *)(x) >= (unsigned long)-MAX_ERRNO)
static inline void *ERR_PTR(long error)          { return (void *)error; }
static inline long  PTR_ERR(const void *ptr)     { return (long)ptr; }
static inline bool  IS_ERR(const void *ptr)      { return IS_ERR_VALUE((unsigned long)ptr); }
static inline bool  IS_ERR_OR_NULL(const void *ptr)
{ return unlikely(!ptr) || IS_ERR_VALUE((unsigned long)ptr); }
/* ---------------------------------------- */

struct conn { int fd; char name[16]; };

/* 回傳「物件指標」或「編碼過的錯誤」，一個回傳值帶兩種資訊 */
static struct conn *conn_open(const char *name)
{
	if (!name || !*name)
		return ERR_PTR(-EINVAL);
	if (strlen(name) >= 16)
		return ERR_PTR(-ENAMETOOLONG);
	struct conn *c = malloc(sizeof(*c));
	if (!c)
		return ERR_PTR(-ENOMEM);
	c->fd = 3;
	strcpy(c->name, name);
	return c;
}

static void try(const char *name)
{
	struct conn *c = conn_open(name);
	if (IS_ERR(c)) {
		printf("%-24s -> ptr=%p err=%ld (%s)\n", name ? name : "(null)",
		       (void *)c, PTR_ERR(c), strerror((int)-PTR_ERR(c)));
		return;
	}
	printf("%-24s -> ok, c->name=%s\n", name, c->name);
	free(c);
}

int main(void)
{
	printf("-MAX_ERRNO as address = %#lx\n", (unsigned long)-MAX_ERRNO);
	printf("ERR_PTR(-1)           = %p\n", ERR_PTR(-1));
	printf("ERR_PTR(-EINVAL)      = %p\n", ERR_PTR(-EINVAL));

	try("db0");
	try("");
	try("a-name-that-is-way-too-long");

	/* 邊界：-4095 還算錯誤，-4096 已經「看起來像一個合法指標」 */
	printf("IS_ERR(ERR_PTR(-4095)) = %d\n", IS_ERR(ERR_PTR(-4095)));
	printf("IS_ERR(ERR_PTR(-4096)) = %d\n", IS_ERR(ERR_PTR(-4096)));

	/* 三種回傳約定對 NULL 的反應 */
	printf("IS_ERR(NULL)=%d  IS_ERR_OR_NULL(NULL)=%d\n", IS_ERR(NULL), IS_ERR_OR_NULL(NULL));
	return 0;
}
```

```
$ gcc -std=gnu11 -Wall -O2 errptr.c -o errptr && ./errptr
-MAX_ERRNO as address = 0xfffffffffffff001
ERR_PTR(-1)           = 0xffffffffffffffff
ERR_PTR(-EINVAL)      = 0xffffffffffffffea
db0                      -> ok, c->name=db0
                         -> ptr=0xffffffffffffffea err=-22 (Invalid argument)
a-name-that-is-way-too-long -> ptr=0xffffffffffffffdc err=-36 (File name too long)
IS_ERR(ERR_PTR(-4095)) = 1
IS_ERR(ERR_PTR(-4096)) = 0
IS_ERR(NULL)=0  IS_ERR_OR_NULL(NULL)=1
```

clang 14 輸出相同。注意最後一行：**`IS_ERR(NULL)` 是 0**。這就是「對 NULL 型函式用 `IS_ERR` 檢查」的 bug 為什麼危險——NULL 會溜過去。

（在 kernel debug 時看到 oops 訊息裡的位址是 `fffffffffffffff2` 這類值，就是有人把 ERR_PTR 當真指標解參考了：`0xfff...f2` = −14 = `-EFAULT`。這是讀 crash log 的一個實用技巧。）

err.h 還有幾個工具：`ERR_CAST()`（err.h:94，把一個錯誤指標轉成另一個型別的錯誤指標，表達「我只是轉傳錯誤」的意圖）、`PTR_ERR_OR_ZERO()`（err.h:117，錯誤指標回傳錯誤碼，否則回 0，用在「回傳 int 的函式要轉傳一個指標的錯誤」）。注意每一個都標了 `__must_check`（Ch 18），忘記檢查回傳值會有編譯警告。

## goto 錯誤階梯：反向釋放

### 一個完整的真實例子

```c
/* fs/pipe.c:982-1015（v6.18） */
static int __do_pipe_flags(int *fd, struct file **files, int flags)
{
	int error;
	int fdw, fdr;

	if (flags & ~(O_CLOEXEC | O_NONBLOCK | O_DIRECT | O_NOTIFICATION_PIPE))
		return -EINVAL;

	error = create_pipe_files(files, flags);
	if (error)
		return error;

	error = get_unused_fd_flags(flags);
	if (error < 0)
		goto err_read_pipe;
	fdr = error;

	error = get_unused_fd_flags(flags);
	if (error < 0)
		goto err_fdr;
	fdw = error;

	audit_fd_pair(fdr, fdw);
	fd[0] = fdr;
	fd[1] = fdw;
	return 0;

 err_fdr:
	put_unused_fd(fdr);
 err_read_pipe:
	fput(files[0]);
	fput(files[1]);
	return error;
}
```

讀法：

```
取得資源（由上往下）            失敗時跳到的 label         label 做的事（由上往下執行，fall through）
1. 檢查 flags                    直接 return -EINVAL        （還沒拿任何東西）
2. create_pipe_files → files     直接 return error          （拿失敗了，沒東西要還）
3. get_unused_fd → fdr           goto err_read_pipe  ──┐    fput(files[0..1])
4. get_unused_fd → fdw           goto err_fdr ──┐      │
                                                └─► put_unused_fd(fdr)
                                                       └─► fput(files[0..1]) → return
```

三條規則讓你能快速驗證這種程式碼：

1. **label 依「取得順序」反向排列**：最後取得的資源，它的 label 在最上面。
2. **label 之間 fall through**：跳到上面的 label，會依序執行到下面所有 label，把更早取得的資源也一起還掉。
3. **label 名字描述「要清理什麼」**：`err_fdr` 清 `fdr`、`err_read_pipe` 清 pipe 檔案。

讀碼技巧：**對每個 `goto X`，確認「在這一行之前取得的所有資源」正好等於「從 X 一路 fall through 到 return 會釋放的資源」**。多一個就是 double free，少一個就是 leak。

### 命名慣例

`Documentation/process/coding-style.rst` 第 7 節「Centralized exiting of functions」（526 行起）明確要求：

> Choose label names which say what the goto does or why the goto exists. An example of a good name could be ``out_free_buffer:`` if the goto frees ``buffer``. Avoid using GW-BASIC names like ``err1:`` and ``err2:``, ...

它同時點名了一種常見 bug，叫「one err bugs」：所有失敗路徑都跳到同一個 `err:` label，然後在 label 裡對可能還是 NULL 的指標呼叫釋放函式。拆成多個 label 就是為了避免這個。

實務上你會看到幾種流派：

| 風格 | 例子 | 意義 |
|---|---|---|
| `err_<要清的東西>` | `err_fdr`、`err_read_pipe` | 失敗路徑，清某資源 |
| `out_<動作>` | `out_unlock`、`out_free` | 成功和失敗共用的出口 |
| 單一 `out:` / `err:` | `goto out;` | 只有一種清理動作時 |
| `fail_<東西>` | `fail_free_reader` | 和 `err_` 同義，子系統偏好不同 |

`out_unlock` 這類 label 特別值得注意：它通常是**成功路徑也會經過的**，例如函式結尾 `ret = 0; out_unlock: mutex_unlock(&m); return ret;`。

### 為什麼不用巢狀 if？

```c
a = alloc_a();
if (a) {
	b = alloc_b();
	if (b) {
		c = alloc_c();
		if (c) {
			/* 真正的工作在第四層縮排 */
		}
		free_b(b);
	}
	free_a(a);
}
```

資源一多，縮排就爆了，而且真正的工作被埋在最深處。kernel 的縮排是 8 格 tab，三層巢狀就吃掉 24 欄。coding-style.rst 列的理由是：無條件跳躍容易理解、減少巢狀、修改時不會漏改某個出口。

## cleanup.h：讓編譯器幫你收拾

### 歷史

goto 階梯可讀、可驗證，但**寫起來容易錯**：加一個新資源，就要在正確位置插一個 label、檢查每個 goto 是否還指對地方。2023 年 Peter Zijlstra 提交了 commit `54da6a092431`（"locking: Introduce __cleanup() based infrastructure"，作者日期 2023-05-26，進 v6.5），利用 Ch 15 介紹的 `__attribute__((cleanup))` 建立了 `include/linux/cleanup.h`。commit message 只有三點：

```
 - simple auto-release pointers using __free()
 - 'classes' with constructor and destructor semantics for
   scope-based resource management.
 - lock guards based on the above classes.
```

檔案開頭的 DOC 註解（cleanup.h:9-17）把動機講得很清楚：「The "goto error" pattern is notorious for introducing subtle resource leaks.」使用文件 `Documentation/core-api/cleanup.rst` 是 Dan Williams 在 v6.12 補上的（commit `d5934e76316e`）。

普及速度很快：v6.18 排除 `tools/` 後，`guard(` 約 4900 次、`scoped_guard(` 約 1450 次、`__free(` 約 1150 次。和 19 萬個 `goto` 相比還是少數，但新程式碼和 refactor 大量採用，**你讀 2024 年後的程式碼一定會遇到**。

### `__free()`、`no_free_ptr()`、`return_ptr()`

```c
/* include/linux/cleanup.h:210-213（v6.18） */
#define DEFINE_FREE(_name, _type, _free) \
	static inline void __free_##_name(void *p) { _type _T = *(_type *)p; _free; }

#define __free(_name)	__cleanup(__free_##_name)

/* include/linux/cleanup.h:215-230 */
#define __get_and_null(p, nullvalue)   \
	({                                  \
		__auto_type __ptr = &(p);   \
		__auto_type __val = *__ptr; \
		*__ptr = nullvalue;         \
		__val;                      \
	})

static inline __must_check
const volatile void * __must_check_fn(const volatile void *val)
{ return val; }

#define no_free_ptr(p) \
	((typeof(p)) __must_check_fn((__force const volatile void *)__get_and_null(p, NULL)))

#define return_ptr(p)	return no_free_ptr(p)
```

翻譯：

- `DEFINE_FREE(kfree, void *, if (!IS_ERR_OR_NULL(_T)) kfree(_T))`（slab.h:512）產生一個函式 `__free_kfree(void *p)`，它把 `p` 指向的變數取出來叫 `_T`，然後執行清理運算式。
- `struct foo *x __free(kfree) = ...;` 展開成 `struct foo *x __attribute__((cleanup(__free_kfree))) = ...;`——`x` 離開 scope 時，編譯器自動呼叫 `__free_kfree(&x)`。
- `no_free_ptr(x)`：**讀出 `x` 的值，然後把 `x` 設成 NULL**。cleanup 函式看到 NULL 就不做事，所以物件的所有權「交出去」了。`__must_check_fn` 讓你忘記使用回傳值時會有警告（不然物件就漏了）。
- `return_ptr(x)` = `return no_free_ptr(x);`。

### 新舊對照一：`alloc_cgroup_ns()`

v6.1 的寫法（`git show v6.1:kernel/cgroup/namespace.c`，第 22–38 行）：

```c
static struct cgroup_namespace *alloc_cgroup_ns(void)
{
	struct cgroup_namespace *new_ns;
	int ret;

	new_ns = kzalloc(sizeof(struct cgroup_namespace), GFP_KERNEL_ACCOUNT);
	if (!new_ns)
		return ERR_PTR(-ENOMEM);
	ret = ns_alloc_inum(&new_ns->ns);
	if (ret) {
		kfree(new_ns);
		return ERR_PTR(ret);
	}
	refcount_set(&new_ns->ns.count, 1);
	new_ns->ns.ops = &cgroupns_operations;
	return new_ns;
}
```

v6.18 的寫法：

```c
/* kernel/cgroup/namespace.c:22-35（v6.18） */
static struct cgroup_namespace *alloc_cgroup_ns(void)
{
	struct cgroup_namespace *new_ns __free(kfree) = NULL;
	int ret;

	new_ns = kzalloc(sizeof(struct cgroup_namespace), GFP_KERNEL_ACCOUNT);
	if (!new_ns)
		return ERR_PTR(-ENOMEM);
	ret = ns_common_init(new_ns);
	if (ret)
		return ERR_PTR(ret);
	ns_tree_add(new_ns);
	return no_free_ptr(new_ns);
}
```

（中間還有 2025 年 Christian Brauner 的 namespace 重構，`ns_alloc_inum` 變成了 `ns_common_init`；`git log v6.1..v6.18 -- kernel/cgroup/namespace.c` 可以看到那一串。我們只關注錯誤處理的變化。）

差別在第二個失敗點：舊版要記得 `kfree(new_ns)`；新版直接 `return ERR_PTR(ret)`，編譯器在這個 return 前插入 `__free_kfree(&new_ns)`。成功時 `no_free_ptr(new_ns)` 把變數清成 NULL，cleanup 就什麼都不做，物件交給呼叫端。

**讀法**：看到 `__free(X)`，在腦中把它翻成「這個函式的**每一個** return 前面都有一行 `X(變數)`，除非變數已經被 `no_free_ptr` 拿走」。

### `guard()` 與 `scoped_guard()`：自動解鎖

```c
/* include/linux/cleanup.h:378-380（v6.18） */
#define DEFINE_GUARD(_name, _type, _lock, _unlock) \
	DEFINE_CLASS(_name, _type, if (!__GUARD_IS_ERR(_T)) { _unlock; }, ({ _lock; _T; }), _type _T); \
	DEFINE_CLASS_IS_GUARD(_name)

/* include/linux/cleanup.h:400-401 */
#define guard(_name) \
	CLASS(_name, __UNIQUE_ID(guard))
```

`guard(mutex)(&m);` 宣告一個看不見名字的變數（`__UNIQUE_ID` 生成），初始化時呼叫 lock，離開 scope 時呼叫 unlock。**鎖的範圍是「從 guard 那一行到所在的 `}`」**，不是到函式結尾。

新舊對照二——Steven Rostedt 2025 年的 commit `f0d8cbc8ccc6`（"ring-buffer: Simplify reset_disabled_cpu_buffer() with use of guard()"，進 v6.16），diff 本身就是最好的教材：

```diff
 /* Must have disabled the cpu buffer then done a synchronize_rcu */
 static void reset_disabled_cpu_buffer(struct ring_buffer_per_cpu *cpu_buffer)
 {
-	unsigned long flags;
-
-	raw_spin_lock_irqsave(&cpu_buffer->reader_lock, flags);
+	guard(raw_spinlock_irqsave)(&cpu_buffer->reader_lock);
 
 	if (RB_WARN_ON(cpu_buffer, local_read(&cpu_buffer->committing)))
-		goto out;
+		return;
 
 	arch_spin_lock(&cpu_buffer->lock);
 
 	rb_reset_cpu(cpu_buffer);
 
 	arch_spin_unlock(&cpu_buffer->lock);
-
- out:
-	raw_spin_unlock_irqrestore(&cpu_buffer->reader_lock, flags);
 }
```

（v6.18 裡這個函式在 `kernel/trace/ring_buffer.c:6178`。）`flags` 變數、`goto out`、`out:` label、unlock 呼叫全部消失。注意 `arch_spin_lock` / `arch_spin_unlock` **沒有**被轉換——這是最底層的 arch spinlock，沒有定義 guard，而且它的範圍很短。

`scoped_guard` 是「只在接下來這個區塊持鎖」：

```c
scoped_guard(mutex, &m) {
	/* 只有這裡持有 m */
}
/* 這裡已經解鎖 */
```

它的實作（cleanup.h:419-429）是一個只跑一次的 `for` 迴圈加上 `goto`/`break` 的技巧，展開後很難讀。讀碼時記住語意就好：**大括號就是臨界區**。

### `CLASS()`：通用的建構/解構

```c
/* include/linux/cleanup.h:275-291（v6.18），節錄 */
#define DEFINE_CLASS(_name, _type, _exit, _init, _init_args...)		\
typedef _type class_##_name##_t;					\
static inline void class_##_name##_destructor(_type *p)			\
{ _type _T = *p; _exit; }						\
static inline _type class_##_name##_constructor(_init_args)		\
{ _type t = _init; return t; }

#define CLASS(_name, var)						\
	class_##_name##_t var __cleanup(class_##_name##_destructor) =	\
		class_##_name##_constructor
```

前面看過的 `CLASS(fd, f)(fd);`（fs/eventfd.c:350）對應的定義是 `include/linux/file.h:83` 的 `DEFINE_CLASS(fd, struct fd, fdput(_T), fdget(fd), int fd)`。展開後就是：宣告變數 `f`，型別是 `class_fd_t`（即 `struct fd`），用 `class_fd_constructor(fd)`（內容是 `fdget(fd)`）初始化，離開 scope 時呼叫 `class_fd_destructor(&f)`（內容是 `fdput`）。cleanup.h:266-268 的註解也是拿 `fdget` 當範例。`guard()` 本身也是用 `CLASS` 建出來的。

### 混合寫法：讀碼時會遇到的中間態

```c
/* kernel/trace/ring_buffer.c:2324-2336（v6.18），節錄 */
static struct ring_buffer_per_cpu *
rb_allocate_cpu_buffer(struct trace_buffer *buffer, long nr_pages, int cpu)
{
	struct ring_buffer_per_cpu *cpu_buffer __free(kfree) = NULL;
	...
	cpu_buffer = kzalloc_node(ALIGN(sizeof(*cpu_buffer), cache_line_size()),
				  GFP_KERNEL, cpu_to_node(cpu));
	if (!cpu_buffer)
		return NULL;

/* ring_buffer.c:2376-2377、2385-2387 */
		if (!page)
			goto fail_free_reader;
	...
	ret = rb_allocate_pages(cpu_buffer, nr_pages);
	if (ret < 0)
		goto fail_free_reader;

/* ring_buffer.c:2419-2424 */
	return_ptr(cpu_buffer);

 fail_free_reader:
	free_buffer_page(cpu_buffer->reader_page);

	return NULL;
}
```

這個函式**同時**用了 `__free(kfree)` 和 goto：`cpu_buffer` 本身由 cleanup 自動釋放，但 `reader_page` 還是靠 `goto fail_free_reader` 手動釋放。cleanup.h:148-153 的註解寫著「the expectation is that usage of "goto" and cleanup helpers is never mixed in the same function」——這裡違反了這個期望，但它是正確的，因為 goto 都是**往後跳、跳出**變數的 scope，沒有跳進去。讀碼時遇到混合寫法，要分開追：哪些資源是 cleanup 管的、哪些是 label 管的。

### 為什麼文件要你不要混用 goto：一個實測

goto **跳進**一個 cleanup 變數的 scope、跳過它的初始化，會怎樣？

```c
/* goto_mix.c */
#include <stdlib.h>
static void freep(void *p) { free(*(void **)p); }

int f(int x)
{
	if (x)
		goto out;
	char *buf __attribute__((cleanup(freep))) = malloc(16);
	buf[0] = 0;
out:
	return 0;
}
```

```
$ gcc -std=gnu11 -Wall -c goto_mix.c; echo "exit=$?"
exit=0
$ clang -std=gnu11 -Wall -c goto_mix.c; echo "exit=$?"
goto_mix.c:7:3: error: cannot jump from this goto statement to its label
                goto out;
                ^
goto_mix.c:8:8: note: jump bypasses initialization of variable with __attribute__((cleanup))
        char *buf __attribute__((cleanup(freep))) = malloc(16);
              ^
1 error generated.
exit=1
```

**gcc 11 一聲不吭地編過了**。`x != 0` 時，`return 0` 前會對一個**從未初始化**的 `buf` 呼叫 `free`——未定義行為。clang 直接拒絕。這是 kernel 開發者常用 clang 再編一次的原因之一，也是 cleanup.h 文件要求「一個函式裡，要嘛全部用 cleanup，要嘛全部用 goto」的理由。

### 另一個陷阱：宣告順序就是釋放順序

cleanup.h:80-146 的註解舉了一個例子：

```c
	struct object *obj __free(remove_free) = NULL;
	int err;

	guard(mutex)(&lock);
	obj = alloc_add();
	...
	if (err)
		return err; // remove_free() called without the lock!!
```

cleanup 變數依**宣告的反序**清理（GCC 文件：「last defined, first cleanup」）。`obj` 先宣告、`guard` 後宣告，所以 return 時**先解鎖、再** `remove_free(obj)`——而 `remove_free` 需要持鎖。修法是把 `obj` 的宣告移到 `guard` 之後，一行宣告加初始化。所以 cleanup.h 風格的程式碼常常**把變數宣告散在函式中間**，這和傳統 kernel「變數宣告集中在開頭」的習慣不同，讀碼時不要覺得奇怪。

## user space 重現 cleanup.h

把 cleanup.h 的核心縮成幾十行，配上 pthread mutex：

```c
/* mini_cleanup.c */
#include <stdio.h>
#include <stdlib.h>
#include <pthread.h>
#include <errno.h>

/* ---- 仿 include/linux/cleanup.h（v6.18）的精簡版 ---- */
#define __cleanup(func) __attribute__((__cleanup__(func)))

#define DEFINE_FREE(_name, _type, _free) \
	static inline void __free_##_name(void *p) { _type _T = *(_type *)p; _free; }
#define __free(_name) __cleanup(__free_##_name)

#define __get_and_null(p, nullvalue) ({ \
	__auto_type __ptr = &(p);       \
	__auto_type __val = *__ptr;     \
	*__ptr = nullvalue;             \
	__val; })
#define no_free_ptr(p)  ((typeof(p))__get_and_null(p, NULL))
#define return_ptr(p)   return no_free_ptr(p)

#define DEFINE_CLASS(_name, _type, _exit, _init, _init_args...)       \
typedef _type class_##_name##_t;                                      \
static inline void class_##_name##_destructor(_type *p)               \
{ _type _T = *p; _exit; }                                             \
static inline _type class_##_name##_constructor(_init_args)           \
{ _type t = _init; return t; }
#define CLASS(_name, var) \
	class_##_name##_t var __attribute__((unused)) \
		__cleanup(class_##_name##_destructor) = \
		class_##_name##_constructor
#define DEFINE_GUARD(_name, _type, _lock, _unlock) \
	DEFINE_CLASS(_name, _type, if (_T) { _unlock; }, ({ _lock; _T; }), _type _T)
#define __PASTE2(a, b) a##b
#define __PASTE(a, b) __PASTE2(a, b)
#define guard(_name) CLASS(_name, __PASTE(__guard_, __COUNTER__))
/* --------------------------------------------------------- */

static void my_free(void *p) { printf("    kfree(%s)\n", p ? "obj" : "NULL"); free(p); }
DEFINE_FREE(kfree, void *, if (_T) my_free(_T))

static void my_lock(pthread_mutex_t *m)   { pthread_mutex_lock(m);   puts("    lock"); }
static void my_unlock(pthread_mutex_t *m) { pthread_mutex_unlock(m); puts("    unlock"); }
DEFINE_GUARD(mutex, pthread_mutex_t *, my_lock(_T), my_unlock(_T))

static pthread_mutex_t big_lock = PTHREAD_MUTEX_INITIALIZER;
struct obj { int id; };

/* 成功時把物件交出去（no_free_ptr），失敗時自動釋放 */
static struct obj *obj_create(int id)
{
	struct obj *o __free(kfree) = malloc(sizeof(*o));
	if (!o)
		return NULL;
	if (id < 0) {
		puts("    bad id, return NULL (o 會被自動釋放)");
		return NULL;
	}
	o->id = id;
	return_ptr(o);         /* o 被設成 NULL，cleanup 看到 NULL 就不做事 */
}

static int update(int id)
{
	guard(mutex)(&big_lock);
	if (id == 0) {
		puts("    early return -EINVAL");
		return -EINVAL;         /* unlock 由編譯器在這裡插入 */
	}
	puts("    updating");
	return 0;
}

int main(void)
{
	puts("obj_create(7):");
	struct obj *o = obj_create(7);
	printf("    got id=%d\n", o->id);
	free(o);
	puts("obj_create(-1):");
	obj_create(-1);
	puts("update(1):");
	update(1);
	puts("update(0):");
	printf("    ret=%d\n", update(0));
	return 0;
}
```

```
$ gcc -std=gnu11 -Wall -O2 -pthread mini_cleanup.c -o mini_cleanup && ./mini_cleanup
obj_create(7):
    got id=7
obj_create(-1):
    bad id, return NULL (o 會被自動釋放)
    kfree(obj)
update(1):
    lock
    updating
    unlock
update(0):
    lock
    early return -EINVAL
    unlock
    ret=-22
```

觀察：`obj_create(7)` 沒有印出 `kfree`（`return_ptr` 把 `o` 清成 NULL，`DEFINE_FREE` 的 `if (_T)` 擋掉了）；`obj_create(-1)` 在 return 時自動 `kfree`；`update(0)` 的提早 return 也觸發了 unlock。

和 kernel 版的差異（刻意簡化）：沒有 `__must_check_fn`、guard 沒有處理 `ERR_PTR` 形式的條件鎖。另外 `CLASS` 裡我加了 `__attribute__((unused))`：沒有它的話，clang 14 會對 `guard()` 產生的隱形變數報 `-Wunused-variable` 警告（實測），gcc 11 則不會。

## 對比與取捨

| 面向 | goto 階梯 | cleanup.h |
|---|---|---|
| 加新資源 | 要插 label、檢查所有 goto | 宣告時加 `__free(x)` |
| 可見性 | 清理動作明寫在函式尾端 | 清理動作藏在宣告的 attribute 裡 |
| 讀碼成本 | 要對 goto 和 label 做配對 | 要記住每個變數的 cleanup 和宣告順序 |
| 編譯器支援 | 任何 C 編譯器 | 依賴 GNU `cleanup` attribute |
| 陷阱 | 漏 label、one err bugs | 宣告順序、goto 跳進 scope（gcc 不擋） |
| 所有權轉移 | 成功路徑直接 return，不經過 label | 要明確 `no_free_ptr` / `return_ptr` |
| kernel 現況 | 約 19 萬個 goto，存量主力 | v6.5 起，新程式碼與 refactor 的趨勢 |

錯誤回傳方式的取捨：

| 約定 | 何時用 | 呼叫端檢查 |
|---|---|---|
| `int`：0 / `-errno` | 大多數動作型函式 | `if (ret)` 或 `if (ret < 0)` |
| 指標：NULL | 失敗原因只有一種（配置） | `if (!p)` |
| 指標：`ERR_PTR` | 失敗原因多種 | `if (IS_ERR(p)) return PTR_ERR(p);` |
| 指標：NULL 或 `ERR_PTR` | NULL 有正常語意（「沒有」） | 先 `IS_ERR`、再判 NULL |

## 踩雷集錦

1. **用 `!p` 檢查 ERR_PTR 型函式**：錯誤指標不是 NULL，會被當成有效指標，之後解參考時 oops 在 `0xffffffffffffffxx` 附近的位址。反過來用 `IS_ERR` 檢查 NULL 型函式也一樣危險（實測 `IS_ERR(NULL) = 0`）。
2. **對 `ret` 寫 `if (ret)` 但函式成功會回傳正值**：例如 `get_unused_fd_flags` 回傳 fd 號碼。要看被呼叫函式的語意，決定 `if (ret)` 還是 `if (ret < 0)`。
3. **以為 label 順序無所謂**：label 是 fall through 的，順序就是釋放順序。把 `err_fdr` 和 `err_read_pipe` 對調，失敗在第二個 fd 時就不會釋放第一個 fd。
4. **以為 `guard()` 的鎖持有到函式結尾**：它持有到**所在區塊**的 `}`。放在 `if` 區塊裡的 guard，出了 `if` 就解鎖了（cleanup.h:51-61 的例子）。
5. **cleanup 變數宣告在 guard 之前**：清理順序是宣告的反序，可能在解鎖之後才執行需要鎖的清理（cleanup.h:80-146 的例子）。
6. **在用了 `__free` 的函式裡 goto 跳進變數 scope**：gcc 11 不報錯，執行時對未初始化的指標呼叫清理函式。clang 會擋（本章實測）。

## 進階：再往深一層

- **`scoped_cond_guard` 與 `ACQUIRE()`**：條件鎖（`mutex_trylock`、`mutex_lock_interruptible`）怎麼和 guard 結合，見 cleanup.h:440-443 與開頭 DOC 裡的 `ACQUIRE` 範例。這部分在 v6.18 還在演進（`git log -- include/linux/cleanup.h` 有 26 個 commit），讀的時候以你手上的版本為準。
- **`IS_ERR_PCPU`、`IOMEM_ERR_PTR`**：err.h:45-74 裡有給 `__percpu`、`__iomem` 指標用的變體，它們存在是因為 sparse 的位址空間檢查（Ch 18）——一般的 `ERR_PTR` 回傳 `void *`，直接當 `__iomem` 指標用 sparse 會警告。
- **Rust 的對照**：kernel 的 Rust 部分用 `Result<T, Error>` 和 `Drop` 做同樣的事。cleanup.h 可以看成是 C 這邊往 RAII 方向的一步。
- **想統計某個子系統轉換到 cleanup.h 的進度**：`git log --oneline -i --grep='guard' v6.5..v6.18 -- kernel/irq/` 會列出 genirq 整批轉換成 lock guard 的 commit（Thomas Gleixner，2025），配合 `git show` 看 diff，是練習「新舊寫法互譯」的好材料。

## 動手練習

1. 把 `__do_pipe_flags`（fs/pipe.c:982-1015）改寫成**巢狀 if** 版本（紙上寫即可），比較兩者的行數與最深縮排。
2. 把 `mini_cleanup.c` 的 `obj_create` 裡的 `return_ptr(o);` 改成 `return o;`，重跑，觀察輸出（你會看到 use-after-free：物件被 `kfree` 之後才交給呼叫端）。再用 `gcc -fsanitize=address` 編一次，讓 ASan 把它抓出來。
3. 在 v6.18 tree 找一個用 `scoped_guard` 的函式（`rg -n "scoped_guard\(" kernel/ | head`），用 `git log -L` 找到它被轉換的 commit，把轉換前的 goto 版本和轉換後的版本並排，標出每個 `goto` 對應到新版的哪個 `}`。
4. 用 `rawsys.c` 試試 `SYS_open` 一個不存在的路徑，看 kernel 回傳什麼（提示：`-ENOENT` = −2）。

## 本章重點整理

- 整數回傳：0 成功、`-errno` 失敗；成功值可能是正數時檢查 `< 0`。512 以上是 kernel 內部 errno，不應漏到 user space。
- 指標回傳有三種約定：NULL、`ERR_PTR`、兩者皆可；讀 kernel-doc 的 `Return:` 或看函式本體確認，檢查方式要對應。
- `ERR_PTR` 把 −1..−4095 映射到位址空間最後一頁（`MAX_ERRNO = 4095`），那裡永遠不會有合法物件；`IS_ERR` 只是一次 unsigned 比較。
- goto 階梯：label 依取得順序反排、彼此 fall through、名字描述要清理什麼。
- cleanup.h（v6.5，Peter Zijlstra）：`__free` 自動釋放、`no_free_ptr`/`return_ptr` 交出所有權、`guard`/`scoped_guard` 自動解鎖、`CLASS` 通用建構解構；注意宣告順序與不要和 goto 混用。

## 自我檢核

- [ ] 不看筆記，解釋為什麼 `IS_ERR(NULL)` 是 false，以及這對呼叫端代表什麼。
- [ ] 有人問「`ERR_PTR(-ENOMEM)` 為什麼不會和某個真的 kernel 指標撞在一起？」，你能在一分鐘內講清楚（包含 4095 這個數字的來源）。
- [ ] 給你一個有四個 label 的 goto 階梯，你能在不執行的情況下驗證每個 goto 釋放的資源是否正確。
- [ ] 能把一段 `guard(mutex)(&m); ... return -EINVAL; ... return 0;` 翻譯回等價的 goto 版本。
- [ ] 能說出 cleanup.h 的兩個陷阱，以及為什麼 gcc 不會幫你抓其中一個。
- [ ] 想一個你寫過的 user-space C 函式，它的錯誤處理用的是哪一種風格？如果換成 cleanup attribute，會更好還是更糟？

## 延伸閱讀

### 官方文件

- **`Documentation/process/coding-style.rst` 第 7 節「Centralized exiting of functions」**（docs.kernel.org/process/coding-style.html）
  - **讀哪裡**：v6.18 tree 的 526–580 行左右。
  - **能學到什麼**：goto 收尾的官方理由、label 命名規則、「one err bugs」的例子。篇幅短，讀完就能拿去審自己的程式碼。
- **[Scope-based Cleanup Helpers](https://docs.kernel.org/core-api/cleanup.html)**（`Documentation/core-api/cleanup.rst`）
  - **讀哪裡**：整頁；它直接把 cleanup.h 開頭的 DOC 註解渲染出來，包含 LIFO 順序的 bug 範例。
  - **能學到什麼**：本章 cleanup.h 各段的官方版本；遇到新出現的 helper（例如 `ACQUIRE`）時以這頁為準。前提：讀完本章與 Ch 15 的 `cleanup` attribute。

### 原始碼與 commit

- **`include/linux/err.h`、`include/linux/cleanup.h`（v6.18）**
  - **讀哪裡**：err.h 全文（約 130 行）；cleanup.h 先讀 1–260 行（DOC 與 `__free` 系列），`DEFINE_GUARD` 之後的巨集技巧可以留到你遇到再查。
- **commit `54da6a092431`（Peter Zijlstra，v6.5）與 `f0d8cbc8ccc6`（Steven Rostedt，v6.16）**
  - **讀哪裡**：`git show <hash>`。前者看 cleanup.h 最初的樣子（比 v6.18 短很多），後者是最小、最清楚的 goto → guard 轉換範例。

### 技術文章

- **"Scope-based resource management for the kernel"** — Jonathan Corbet（LWN.net，2023）
  - **這篇說什麼**：Peter Zijlstra 的 patch series 剛提出時的介紹與 mailing list 上的爭論（包含 Linus 對語法的意見）。
  - **為什麼值得讀**：這是脈絡鴻溝的典型素材——為什麼 kernel 社群在 30 年後才接受 RAII 風格、反對的理由是什麼。在 LWN 的 Kernel Index 用「cleanup」或「scope-based」搜尋即可找到。前提：讀完本章 cleanup.h 一節。

### 書籍

- **《Linux Device Drivers》（3rd ed.）** — Corbet, Rubini, Kroah-Hartman（O'Reilly, 2005）
  - **這本書的定位**：第 2 章「Error Handling During Initialization」一節是 goto 錯誤階梯最經典的教學版本，用 module 初始化當例子。
  - **讀哪幾章**：第 2 章該節（兩三頁）。版本是 2.6.10，API 細節已過時，但 goto 階梯的寫法與讀法完全沒變。免費線上版由 LWN 提供。

學會讀錯誤處理之後，函式簽名裡還有一類你會直接略過的東西：`__user`、`__rcu`、`__must_check`……它們在編譯時不產生任何程式碼，卻是讀碼時最有價值的資訊。

→ [Ch 18 編譯器標註與 sparse：__user、__rcu、__must_check、likely](./18-annotations-and-sparse.md)
