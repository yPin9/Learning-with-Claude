# Ch 8 — 控制流慣用法：goto 收尾、early return、迴圈與 switch 的讀法

> **目標**：看到 `goto out_free_uid`、`for (;;)` 裡塞滿 `break`、`switch` 裡一排 `fallthrough;`、`do { } while (0)` 時，不再逐行模擬，而是一眼認出「這是哪一種形狀」，並知道這個形狀告訴你的**不變量**（invariant）是什麼：哪些資源此刻已經拿到、鎖還在不在手上、函式從哪幾個出口離開。
> **鴻溝**：本章主要練**慣用法鴻溝**，順帶用 git 考古補一點**脈絡鴻溝**（`fallthrough` 為什麼是巨集）。

> **環境**：gcc 11.4、clang 14（WSL Ubuntu 22.04，x86_64）；kernel 原始碼固定在 v6.18，Redis 7.2.17，Lua 5.4.9。範例程式都放在 `~/code_reading/scratch/08/`。

## 為什麼需要這個？

Spinellis 原書第 2 章花了整章講「基本程式元素」：迴圈、條件、`switch`、`goto`。2003 年寫給讀 NetBSD 的人看，今天看起來好像太基礎——誰不會讀 `for` 迴圈？

問題不在讀不讀得懂一個 `for`，而在**讀得快不快**。打開 `fs/pipe.c` 的 `anon_pipe_read()`，你會看到一個約 100 行的 `for (;;)`，裡面有 9 個 `break`、1 個 `continue`、1 個 `return`。新手的讀法是逐行模擬：「如果 head != tail，進來，chars 大於 total_len 嗎……」，讀到第三個 `break` 就忘了第一個 `break` 之後鎖到底放了沒。熟手的讀法是先認形狀：「`for (;;)` 前面 `mutex_lock`、後面 `mutex_unlock`，所以裡面每個 `break` 都是『帶著鎖離開迴圈，交給下面那一個 unlock』；只有那個 `return -ERESTARTSYS` 例外，它前面一定先放了鎖。」一句話把 11 個出口全部歸類完畢，剩下的注意力才能拿去讀真正的邏輯。

這就是 Ch 2 講的 **chunking**：熟手把一整段程式碼當成一個「塊」記住，而不是十幾行獨立的敘述。控制流慣用法是最早、最常出現的一批塊。

> 如果你對 chunking 和 beacon 還不熟，先回看 [Ch 2 — 程式理解的認知模型](./02-program-comprehension-models.md)。

## 先建立直覺：控制流是「形狀」，形狀帶著承諾

把一個函式想像成一棟有很多出口的建築。每一種慣用法都是一種**建築規範**，它承諾了某件事：

```
慣用法                  它承諾的事（讀碼時可以直接相信）
──────────────────────  ─────────────────────────────────────────────
early return / guard    走過這幾行之後，參數已經合法；後面不用再擔心
goto 收尾階梯           標籤由下往上 = 資源由後往前；跳到 out_X 代表 X 之前的都拿到了
for (;;) + break        迴圈只有一個「正常出口」= 迴圈後面那一行；所有 break 匯合到那裡
switch 裡的 break       只離開 switch，不離開外層迴圈
fallthrough;            「我是故意的」，不是忘了 break
do { } while (0)        這個巨集可以當成一個普通敘述用，後面加分號不會出事
```

讀碼時你的工作是：**先辨認形狀，套用承諾，然後只在承諾被打破的地方停下來細讀**。承諾被打破的地方往往就是 bug，或是作者刻意的例外（例如 `anon_pipe_read()` 那個先 unlock 再 return 的出口）。

## goto 收尾階梯（cleanup ladder）

### 從一個可跑的例子開始

先寫一個 user-space 版本，配置三個資源，可以指定第幾個失敗：

```c
/* ~/code_reading/scratch/08/ladder.c（節錄核心） */
static int setup(int fail_at)
{
	void *buf, *ring, *file;
	int err = -1;

	buf = acquire("buf", 1, fail_at);
	if (!buf)
		goto out;
	ring = acquire("ring", 2, fail_at);
	if (!ring)
		goto out_free_buf;
	file = acquire("file", 3, fail_at);
	if (!file)
		goto out_free_ring;

	printf("  ...all set, using them...\n");
	release("file", file);   /* 示範用：正常路徑也釋放，免得 leak */
	err = 0;

out_free_ring:
	release("ring", ring);
out_free_buf:
	release("buf", buf);
out:
	return err;
}
```

`acquire()` 在 `idx == fail_at` 時回傳 `NULL`，其他時候 `malloc(16)` 並印出訊息。用 `gcc -Wall -Wextra -O2 -fsanitize=address -g ladder.c -o ladder` 編譯，跑出來：

```
fail_at=0
  acquire buf
  acquire ring
  acquire file
  ...all set, using them...
  release file
  release ring
  release buf
  => return 0
fail_at=1
  acquire buf    FAILED
  => return -1
fail_at=2
  acquire buf
  acquire ring   FAILED
  release buf
  => return -1
fail_at=3
  acquire buf
  acquire ring
  acquire file   FAILED
  release ring
  release buf
  => return -1
```

ASan 沒有回報任何 leak。這個形狀的規則有三條：

1. **配置順序由上往下，標籤順序由下往上**。最後配置的資源，它的釋放標籤寫在最上面。
2. **標籤名稱寫的是「要釋放什麼」**，不是「發生什麼錯」。`out_free_buf` 的意思是「從釋放 buf 開始往下做」。
3. **失敗時跳到「前一個成功資源」的標籤**。`file` 失敗 → 跳 `out_free_ring`，因為 ring 是最後一個成功拿到的。

第 3 條是讀碼時最有用的推論：**看到 `goto out_free_ring`，你立刻知道此時 ring 和 buf 都已經拿到了**，不用往回翻。

### 故意弄壞：跳錯一層

把 `file` 失敗時的 `goto out_free_ring` 改成 `goto out_free_buf`：

```
ladder_bug.c: In function ‘setup’:
ladder_bug.c:40:1: warning: label ‘out_free_ring’ defined but not used [-Wunused-label]
   40 | out_free_ring:
      | ^~~~~~~~~~~~~
```

gcc 只抱怨標籤沒被用到——這是個好線索，但如果其他地方還有人跳到 `out_free_ring`，連這個警告都不會有。執行時 `fail_at=3` 的輸出變成：

```
fail_at=3
  acquire buf
  acquire ring
  acquire file   FAILED
  release buf
  => return -1
```

`release ring` 不見了。LeakSanitizer 在程式結束時抓到：

```
==326150==ERROR: LeakSanitizer: detected memory leaks

Direct leak of 16 byte(s) in 1 object(s) allocated from:
    #0 0x74cd228b4887 in __interceptor_malloc ../../../../src/libsanitizer/asan/asan_malloc_linux.cpp:145
    #1 0x5f9dfdd8328d in acquire /home/ypp/code_reading/scratch/08/ladder_bug.c:12
    #2 0x5f9dfdd83324 in setup /home/ypp/code_reading/scratch/08/ladder_bug.c:29
```

這就是 code review 時要對的帳：**每一個 `goto` 的目標標籤，必須剛好釋放「到目前為止拿到的所有東西」，不多不少**。多了是 double free 或釋放未初始化指標，少了是 leak。

### kernel 裡的階梯：`__do_pipe_flags()`

```c
/* fs/pipe.c:982（v6.18） */
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

套用三條規則讀一遍：

- 第一個 `if` 是 guard clause（下一節講），還沒拿到任何資源，直接 `return`。
- `create_pipe_files()` 失敗也直接 `return`——它自己負責清乾淨（這是另一條約定：**失敗的函式不留下半成品**，Ch 17 會再談）。
- 拿到 `files[0..1]` 之後，失敗改用 `goto`。第二個 fd 失敗跳 `err_fdr`，它的意思是「fdr 拿到了，先還 fdr，再往下還 files」。
- 成功路徑在標籤之前 `return 0`，**不會**掉進清理區。和上面 user-space 例子「成功路徑也穿過標籤」是兩種變體，讀碼時要分辨：看標籤上方最後一行是不是 `return`。

注意 `error` 這個變數一路被重複使用：它先裝 `create_pipe_files()` 的錯誤碼，再裝 fd 號碼（非負）或錯誤碼（負）。這是 kernel 裡 `int` 回傳值「負數是 -errno、非負是有效值」慣例的直接結果（Ch 17）。

### 更細的變體：成功路徑提早 return，清理區共用

```c
/* fs/pipe.c:792（v6.18），節錄 */
struct pipe_inode_info *alloc_pipe_info(void)
{
	...
	pipe = kzalloc(sizeof(struct pipe_inode_info), GFP_KERNEL_ACCOUNT);
	if (pipe == NULL)
		goto out_free_uid;
	...
	if (too_many_pipe_buffers_hard(user_bufs) && pipe_is_unprivileged_user())
		goto out_revert_acct;

	pipe->bufs = kcalloc(pipe_bufs, sizeof(struct pipe_buffer),
			     GFP_KERNEL_ACCOUNT);

	if (pipe->bufs) {
		init_waitqueue_head(&pipe->rd_wait);
		...
		return pipe;
	}

out_revert_acct:
	(void) account_pipe_buffers(user, pipe_bufs, 0);
	kfree(pipe);
out_free_uid:
	free_uid(user);
	return NULL;
}
```

這裡有個值得停一下的地方：`kcalloc()` 失敗時**沒有** `goto`，而是讓 `if (pipe->bufs)` 不成立，自然往下掉進 `out_revert_acct`。這是「成功路徑包在 if 裡提早 return，失敗路徑順流而下」的寫法。第一次看會覺得少了一個 `goto`，認出形狀之後就知道它等價於 `if (!pipe->bufs) goto out_revert_acct;`。

### Redis 的變體：單一錯誤標籤 + 記錄錯誤位置

```c
/* redis-7.2.17/src/rdb.c:1474 */
    if (rdbSaveRio(req,&rdb,&error,rdbflags,rsi) == C_ERR) {
        errno = error;
        err_op = "rdbSaveRio";
        goto werr;
    }

    /* Make sure data will not remain on the OS's output buffers */
    if (fflush(fp)) { err_op = "fflush"; goto werr; }
    if (fsync(fileno(fp))) { err_op = "fsync"; goto werr; }
    ...
    if (fclose(fp)) { fp = NULL; err_op = "fclose"; goto werr; }

    return C_OK;

werr:
    saved_errno = errno;
    serverLog(LL_WARNING,"Write error while saving DB to the disk(%s): %s", err_op, strerror(errno));
    if (fp) fclose(fp);
    unlink(filename);
    errno = saved_errno;
    return C_ERR;
```

Redis 這裡只有一個資源（`fp`），所以不需要階梯，只要一個 `werr`。兩個細節值得認：

- `err_op` 在跳之前先記下「在哪一步出錯」，清理區統一印 log。這是把錯誤脈絡帶進共用出口的常見手法。
- `fclose` 失敗時先 `fp = NULL` 再跳，因為 `fclose` 不論成敗都已經釋放了 `FILE`，清理區的 `if (fp) fclose(fp)` 不能再關一次。**清理區用「指標是否為 NULL」判斷資源狀態**，和階梯式「用跳到哪個標籤判斷」是兩種流派。

### 為什麼不用別的寫法？

| 寫法 | 問題 |
|---|---|
| 巢狀 `if`（成功往內縮） | 三個資源就縮三層，主邏輯被擠到右邊；清理碼散在各層的 `else` |
| 每個失敗點各自寫完整清理 | 清理碼重複 N 次，新增一個資源要改 N 處，最容易漏 |
| 旗標變數 + 一個出口 | 多了狀態變數要追，讀碼時要在腦中模擬旗標值 |
| C++ RAII / defer | C 沒有；kernel 從 v6.5 起有 `__cleanup` 為基礎的 `cleanup.h`（`guard()`、`__free()`），見 Ch 17 |

Dijkstra 1968 年那封 "Go To Statement Considered Harmful" 反對的是用 `goto` 當主要控制結構、跳來跳去織成義大利麵。**只往前跳、只跳到函式尾端的清理區**，是 kernel coding style（`Documentation/process/coding-style.rst` 第 7 節 "Centralized exiting of functions"）明文推薦的用法。讀碼時你會發現 kernel 的 `goto` 幾乎全是這種，往回跳的只有少數 `retry:` / `again:`。

`cleanup.h` 是 Peter Zijlstra 在 2023 年加的（commit `54da6a092431`，`git describe --contains` 顯示進入 v6.5-rc1）。v6.18 tree 裡 `guard(mutex)` 已經出現 2364 次，所以新程式碼裡你會越來越常看到**沒有階梯的函式**——鎖和記憶體在變數離開作用域時由編譯器插入的清理碼釋放。讀這種程式碼時，出口的帳要改成「看宣告處」而不是「看函式尾巴」。

## early return 與 guard clause

guard clause 是函式開頭那一串「不合法就走人」的檢查：

```c
/* fs/eventfd.c:390（v6.18），do_eventfd() 開頭 */
	if (flags & ~EFD_FLAGS_SET)
		return -EINVAL;

	ctx = kmalloc(sizeof(*ctx), GFP_KERNEL);
	if (!ctx)
		return -ENOMEM;
```

Redis 的 `setrangeCommand()` 是同樣形狀：

```c
/* redis-7.2.17/src/t_string.c:442 */
    if (getLongFromObjectOrReply(c,c->argv[2],&offset,NULL) != C_OK)
        return;

    if (offset < 0) {
        addReplyError(c,"offset is out of range");
        return;
    }
```

讀法：**guard clause 是前置條件（precondition）的清單**。讀完它們，你就拿到了「後面程式碼可以假設的事」。讀 `setrangeCommand` 後半時，你不需要再擔心 `offset` 是負的。

guard clause 和 goto 階梯的分界很清楚：**還沒拿到任何資源之前用 `return`，拿到之後用 `goto`**。`__do_pipe_flags()` 是教科書範例：前兩個失敗 `return`，後兩個 `goto`。看到一個函式在拿了資源之後還直接 `return`，就要停下來檢查它是不是漏了釋放。

## `do { } while (0)`：讓巨集變成一個敘述

這個形狀在 kernel、Redis（`atomicvar.h` 一整排）都很常見。它的存在理由只有一個：**讓多敘述的巨集在任何能放「一個敘述」的地方都能安全使用**。我們用三個版本實驗。

**版本 1：裸的兩個敘述**

```c
#define LOG_AND_COUNT(msg)  printf("log: %s\n", msg); count++
...
	if (error)
		LOG_AND_COUNT("something failed");
	printf("error=%d count=%d\n", error, count);
```

gcc 11 很聰明地警告了：

```
macro_naked.c:4:29: warning: macro expands to multiple statements [-Wmultistatement-macros]
    4 | #define LOG_AND_COUNT(msg)  printf("log: %s\n", msg); count++
...
macro_naked.c:11:9: note: some parts of macro expansion are not guarded by this ‘if’ clause
```

執行結果是 `error=0 count=1`——`error` 是 0，`count` 卻被加了。展開後 `count++` 不在 `if` 裡。

**版本 2：用大括號包起來**

```c
#define SWAP(a, b)  { int __t = (a); (a) = (b); (b) = __t; }
...
	if (x < y)
		SWAP(x, y);
	else
		printf("already ordered\n");
```

編譯直接失敗：

```
macro_brace.c: In function ‘main’:
macro_brace.c:12:9: error: ‘else’ without a previous ‘if’
   12 |         else
      |         ^~~~
```

展開後是 `if (x < y) { ... };  else`——那個分號自成一個空敘述，`if` 已經結束了，`else` 找不到主人。

有趣的是，Lua 5.4.9 自己就用這種寫法：

```c
/* lua-5.4.9/lobject.h:118 */
#define setobj(L,obj1,obj2) \
	{ TValue *io1=(obj1); const TValue *io2=(obj2); \
          io1->value_ = io2->value_; settt_(io1, io2->tt_); \
	  checkliveness(L,io1); lua_assert(!isnonstrictnil(io1)); }
```

Lua 能這樣寫，是因為它的程式碼慣例裡從不把 `setobj` 放在沒有大括號的 `if ... else` 之間——一個專案內部的約定。讀 Lua 時看到這種巨集不用驚訝，但自己寫巨集時別學。

**版本 3：`do { } while (0)`**

```c
#define SWAP(a, b)  do { int __t = (a); (a) = (b); (b) = __t; } while (0)
```

兩個情境都正確，輸出 `x=2 y=1 count=0`。kernel 的 `swap()` 就是這樣寫的：

```c
/* include/linux/minmax.h:307（v6.18） */
#define swap(a, b) \
	do { typeof(a) __tmp = (a); (a) = (b); (b) = __tmp; } while (0)
```

`do { ... } while (0)` 在語法上是**一個需要分號結尾的敘述**，剛好吃掉呼叫端寫的那個分號。迴圈只跑一次，編譯器在 `-O0` 以上都會把它最佳化成直線碼，沒有執行期成本。

讀碼時的結論：**看到 `do { ... } while (0)` 就把外殼忽略，只看大括號裡面**。它不是迴圈。唯一的例外是裡面有 `break`——在 `do { } while (0)` 裡 `break` 等於「跳到巨集尾端」，有些舊程式碼拿它當 goto 的替代品。

GNU C 的另一個選擇是 statement expression `({ ... })`，它還能產生值（`min()`、`FIELD_GET()` 都用它），這在 [Ch 15](./15-kernel-c-gnu-extensions.md) 講。

## 迴圈慣用法

### `for (;;)`：無限迴圈，出口在裡面

`for (;;)` 和 `while (1)` 等價，kernel 和 Lua 偏好前者（K&R 時代的習慣，有人念成 "forever"）。讀到它要立刻做的事是：**找出所有出口並分類**。

回到開頭說的 `anon_pipe_read()`：

```c
/* fs/pipe.c:281（v6.18），節錄骨架 */
	ret = 0;
	mutex_lock(&pipe->mutex);
	...
	for (;;) {
		unsigned int head = smp_load_acquire(&pipe->head);
		unsigned int tail = pipe->tail;
		...
		if (!pipe_empty(head, tail)) {
			...
			if (chars > total_len) {
				if (buf->flags & PIPE_BUF_FLAG_WHOLE) {
					if (ret == 0)
						ret = -ENOBUFS;
					break;
				}
				chars = total_len;
			}
			...
			total_len -= chars;
			if (!total_len)
				break;	/* common path: read succeeded */
			if (!pipe_empty(head, tail))	/* More to do? */
				continue;
		}

		if (!pipe->writers)
			break;
		if (ret)
			break;
		if ((filp->f_flags & O_NONBLOCK) ||
		    (iocb->ki_flags & IOCB_NOWAIT)) {
			ret = -EAGAIN;
			break;
		}
		mutex_unlock(&pipe->mutex);
		...
		if (wait_event_interruptible_exclusive(pipe->rd_wait, pipe_readable(pipe)) < 0)
			return -ERESTARTSYS;

		wake_next_reader = true;
		mutex_lock(&pipe->mutex);
	}
	if (pipe_is_empty(pipe))
		wake_next_reader = false;
	mutex_unlock(&pipe->mutex);
```

出口分類：

```
出口                         鎖的狀態       意義
───────────────────────────  ────────────   ──────────────────────────────
break（9 處）                持有           交給迴圈後的 mutex_unlock
continue                     持有           buffer 還有資料，再讀一輪
return -ERESTARTSYS          已釋放         被 signal 打斷；上面剛 unlock
迴圈底部自然回頂             持有（剛 lock）睡醒了，重新檢查
```

另一個要認的小形狀是 `if (ret == 0) ret = -ENOBUFS;`（或 `if (!ret) ret = error;`）。意思是：**已經讀到一些資料就回報讀到的量，什麼都沒讀到才回報錯誤**。這是 `read(2)` 的部分成功語意，在 kernel 的 read/write 路徑到處都是。

### sentinel 迴圈：走到「哨兵」為止

陣列不帶長度、用一個特殊元素當結尾，迴圈條件就是「還沒碰到哨兵」：

```c
/* lua-5.4.9/lauxlib.c:955 */
LUALIB_API void luaL_setfuncs (lua_State *L, const luaL_Reg *l, int nup) {
  luaL_checkstack(L, nup, "too many upvalues");
  for (; l->name != NULL; l++) {  /* fill the table with given functions */
    ...
    lua_setfield(L, -(nup + 2), l->name);
  }
  lua_pop(L, nup);  /* remove upvalues */
}
```

`for` 的初始化欄位是空的（`l` 是參數，已經指向開頭），條件是 `l->name != NULL`，遞增是指標往前走一格。呼叫端的陣列最後一定有一個 `{NULL, NULL}`。C 字串本身就是最普遍的 sentinel 迴圈（`'\0'` 是哨兵），kernel `lib/string.c` 的 `strlen()` 就是 `for (sc = s; *sc != '\0'; ++sc)`。指標走訪的細節在 [Ch 10](./10-pointer-idioms.md)。

### `while (n--)` 與 `while (--n)`：差一次

```c
/* ~/code_reading/scratch/08/loops.c 節錄 */
	n = 3; while (n--) printf(" %d", n);
	n = 3; while (--n) printf(" %d", n);
```

```
while (n--)  : 2 1 0   (結束時 n=-1)
while (--n)  : 2 1   (結束時 n=0)
```

`while (n--)` 先測試舊值再減：跑 n 次，迴圈體看到的是 n-1 到 0，結束後 n 變成 -1（如果是 unsigned 就是最大值）。`while (--n)` 先減再測：只跑 n-1 次。kernel 的 `drop_links()` 用前者走訪一個堆疊：

```c
/* fs/namei.c:731（v6.18） */
static void drop_links(struct nameidata *nd)
{
	int i = nd->depth;
	while (i--) {
		struct saved *last = nd->stack + i;
		do_delayed_call(&last->done);
		clear_delayed_call(&last->done);
	}
}
```

讀法：`i` 從 `depth` 開始，迴圈體裡看到 `depth-1, ..., 0`，也就是**從堆疊頂端往底部倒著處理**。`while (i--)` 是「反向走訪 [0, i)」的慣用寫法。

### 反向清理迴圈：`while (--i >= 0)`

goto 階梯遇到「迴圈裡配置了 N 個東西」時，清理區就變成反向迴圈：

```c
/* kernel/watch_queue.c:284（v6.18） */
	for (i = 0; i < nr_pages; i++) {
		pages[i] = alloc_page(GFP_KERNEL);
		if (!pages[i])
			goto error_p;
		pages[i]->private = i * WATCH_QUEUE_NOTES_PER_PAGE;
	}

	bitmap = bitmap_alloc(nr_notes, GFP_KERNEL);
	if (!bitmap)
		goto error_p;
	...
	return 0;

error_p:
	while (--i >= 0)
		__free_page(pages[i]);
	kfree(pages);
```

這段的精妙處在於 `error_p` 有**兩個入口**，`i` 的值不同：

- 從迴圈內跳來：`pages[i]` 配置失敗，`i` 指向那個失敗的格子。`--i` 先退一格，剛好只釋放 `0..i-1`。
- 從 `bitmap_alloc` 失敗跳來：迴圈已經跑完，`i == nr_pages`。`--i` 從 `nr_pages-1` 開始，釋放全部。

同一個 `while (--i >= 0)` 兩種情況都對。這行能成立的前提是 `i` 是 **signed**（第 243 行宣告 `int ret, i, nr_pages;`）。如果是 `size_t`，`--i >= 0` 永遠成立，就會無窮迴圈。

### 故意弄壞：unsigned 反向迴圈

```c
	for (size_t i = n - 1; i >= 0; i--) {   /* BUG：size_t 永遠 >= 0 */
		printf("i=%zu\n", i);
		if (++guard == 5) { printf("...停不下來，強制中止\n"); break; }
	}
```

`-Wextra` 會抓到（gcc 把它歸在 `-Wtype-limits`）：

```
rev_bug.c:10:34: warning: comparison of unsigned expression in ‘>= 0’ is always true [-Wtype-limits]
   10 |         for (size_t i = n - 1; i >= 0; i--) {   /* BUG：size_t 永遠 >= 0 */
      |                                  ^~
i=2
i=1
i=0
i=18446744073709551615
i=18446744073709551614
...停不下來，強制中止
```

`0 - 1` 對 `size_t` 來說是 2^64 − 1 = 18446744073709551615。unsigned 的正確反向寫法是把遞減放進條件：`for (size_t j = 3; j-- > 0; )`，實測輸出 `a[2]=30 a[1]=20 a[0]=10`。看到這個「條件裡有 `--`、遞增欄位空著」的怪形狀，就知道作者在處理 unsigned 反向走訪。整數陷阱的完整討論在 [Ch 9](./09-expressions-integers-bits.md)。

### 巨集包裝的迴圈

kernel 大量用巨集把迴圈包起來，`list_for_each_entry` 是代表：

```c
/* include/linux/list.h:781（v6.18） */
#define list_for_each_entry(pos, head, member)				\
	for (pos = list_first_entry(head, typeof(*pos), member);	\
	     !list_entry_is_head(pos, head, member);			\
	     pos = list_next_entry(pos, member))
```

讀碼原則：**`for_each` 開頭的巨集一律當成 `for` 迴圈讀**，裡面的 `break` / `continue` 語意和一般 `for` 完全相同。要知道它怎麼走訪，展開一次就好（Ch 13、Ch 22、Ch 23）。

## `switch`：break、fall-through 與狀態機

### `break` 只離開最內層

`switch` 裡的 `break` 離開 `switch`，`for` 裡的 `break` 離開 `for`。當 `switch` 在迴圈裡面，`break` 只離開 `switch`，迴圈繼續。這條規則每個人都知道，但在讀 lexer 這種「`for (;;)` 包 `switch`」的程式碼時，它決定了整個函式的語意。

Lua 的詞法分析器：

```c
/* lua-5.4.9/llex.c:445 */
static int llex (LexState *ls, SemInfo *seminfo) {
  luaZ_resetbuffer(ls->buff);
  for (;;) {
    switch (ls->current) {
      case '\n': case '\r': {  /* line breaks */
        inclinenumber(ls);
        break;
      }
      case ' ': case '\f': case '\t': case '\v': {  /* spaces */
        next(ls);
        break;
      }
      case '-': {  /* '-' or '--' (comment) */
        next(ls);
        if (ls->current != '-') return '-';
        /* else is a comment */
        ...
        while (!currIsNewline(ls) && ls->current != EOZ)
          next(ls);  /* skip until end of line (or end of file) */
        break;
      }
      ...
```

一旦認出這個形狀，整個 `llex` 的語意可以用兩句話說完：**`break` = 吃掉輸入、繼續掃；`return` = 一個 token 完成**。空白、換行、註解都 `break`，所以它們不產生 token。

我們寫一個縮小版驗證這個理解（`lexer.c`，結構照抄 `llex`），輸入 `"x = 42 - y -- comment\n+ 7"`：

```
NAME '=' NUM '-' NAME '+' NUM EOF
```

註解整段消失，`-` 單獨出現時是一個 token——和 `llex` 的行為一致。

### 跳出兩層：goto found

`break` 不能一次跳出兩層。實驗（`nested.c`）：在 3×4 的格子裡找 7（位於 r=1, c=2）：

```
只用 break：r=3 c=4（外層照跑完）
goto found：r=1 c=2
```

只用 `break` 時，內層停了，外層繼續跑完，`r`、`c` 變成迴圈結束時的值。kernel 和大多數 C 專案用 `goto found` / `goto next` 解決，或把搜尋抽成一個函式用 `return`。讀碼時看到迴圈裡的 `goto`，先確認它是往前跳（離開迴圈）還是往回跳（`retry:` 重來），兩者意義完全不同。

### fall-through：從註解到 `fallthrough;`

`switch` 的 `case` 沒寫 `break` 就會「掉」進下一個 `case`。這是 C 從 B 語言繼承的設計，有時很好用：

```c
/* include/linux/jhash.h:88（v6.18），jhash() 處理最後不足 12 bytes 的部分 */
	switch (length) {
	case 12: c += (u32)k[11]<<24;	fallthrough;
	case 11: c += (u32)k[10]<<16;	fallthrough;
	case 10: c += (u32)k[9]<<8;	fallthrough;
	case 9:  c += k[8];		fallthrough;
	...
	case 2:  a += (u32)k[1]<<8;	fallthrough;
	case 1:  a += k[0];
		 __jhash_final(a, b, c);
		 break;
	case 0: /* Nothing left to add */
		break;
	}
```

剩幾個 byte 就從第幾個 `case` 進去，一路掉到 `case 1`。這是「用 switch 當成可變起點的直線碼」，和 Duff's device 同一個家族。

Redis 的 SipHash 做同樣的事，但用的是註解：

```c
/* redis-7.2.17/src/siphash.c:156 */
    switch (left) {
    case 7: b |= ((uint64_t)in[6]) << 48; /* fall-thru */
    case 6: b |= ((uint64_t)in[5]) << 40; /* fall-thru */
    ...
    case 1: b |= ((uint64_t)in[0]); break;
    case 0: break;
    }
```

問題在於：**忘了寫 `break` 和故意不寫，在原始碼上長得一模一樣**。kernel 文件 `Documentation/process/deprecated.rst` 第 220 行附近直接說，因為 missing break 造成的缺陷太多（引用 CWE-484），kernel 不再允許隱式 fall-through，所有 `case` 區塊必須以 `break;`、`fallthrough;`、`continue;`、`goto <label>;` 或 `return [expression];` 結尾。

`fallthrough` 這個「偽關鍵字」定義在：

```c
/* include/linux/compiler_attributes.h:203（v6.18） */
/*
 * Add the pseudo keyword 'fallthrough' so case statement blocks
 * must end with any of these keywords:
 *   break;
 *   fallthrough;
 *   continue;
 *   goto <label>;
 *   return [expression];
 *
 *  gcc: https://gcc.gnu.org/onlinedocs/gcc/Statement-Attributes.html#Statement-Attributes
 */
#if __has_attribute(__fallthrough__)
# define fallthrough                    __attribute__((__fallthrough__))
#else
# define fallthrough                    do {} while (0)  /* fallthrough */
#endif
```

**考古**：`git blame` 這幾行，指向 Joe Perches 的 commit `294f69e662d1`（2019-10-05），標題 "compiler_attributes.h: Add 'fallthrough' pseudo keyword for switch/case use"，`git describe --contains` 顯示它在 `v5.4-rc3~24`，也就是 v5.4 週期合入。commit message 說明了兩件事：GCC 從 7.1 起在 C 模式支援 `__fallthrough__` attribute；不支援的舊編譯器退化成空的 `do {} while (0)`。之後幾個版本，kernel 陸續把數千個 `/* fall through */` 註解機械式換成 `fallthrough;`。在 v6.18 tree 裡，`git grep` 數到 `.c` 檔中 `fallthrough;` 出現 4706 次，殘留的 fall-through 註解（不分大小寫比對 `/* fall through`）還有 154 處，分散在 `drivers/`、`tools/`、`arch/` 等目錄。

> 這段考古用的指令是 `git blame -L 203,216 include/linux/compiler_attributes.h` 和 `git describe --contains 294f69e662d1`，方法在 [Ch 37](./37-git-archaeology.md) 詳講。注意在 blobless clone 上對整個歷史跑 `git log -S` 會觸發大量 blob 下載，先用 `blame` 定位到 commit 再 `show` 比較快。

為什麼不直接用註解？因為編譯器對註解的辨識是**有等級的**。kernel 在 `init/Kconfig:936` 設定 `CC_IMPLICIT_FALLTHROUGH`，gcc 用的是 `-Wimplicit-fallthrough=5`，這個等級**完全不看註解**，只認 attribute。我們實驗（`ft.c`，`case 3` 用 `fallthrough;`，`case 2` 用 Redis 風格的 `/* fall-thru */`，`case 1` 什麼都不寫）：

```
== -Wimplicit-fallthrough=3
== -Wimplicit-fallthrough=5
ft.c: In function ‘tail_bytes’:
ft.c:19:19: warning: this statement may fall through [-Wimplicit-fallthrough=]
   19 |         case 2: t |= (unsigned)k[1] << 8;       /* fall-thru */
      |                 ~~^~~~~~~~~~~~~~~~~~~~~~
ft.c:20:9: note: here
   20 |         case 1: t |= k[0];
      |         ^~~~
```

等級 3 接受 `/* fall-thru */` 這種註解，等級 5 不接受。另一個實測觀察：gcc 11 對 `case 1` 掉進只有 `break` 的 `case 0` **沒有警告**，clang 14 的 `-Wimplicit-fallthrough` 則兩處都報：

```
ft.c:20:2: warning: unannotated fall-through between switch labels [-Wimplicit-fallthrough]
...
ft.c:21:2: warning: unannotated fall-through between switch labels [-Wimplicit-fallthrough]
        case 0: break;   /* 注意：case 1 沒寫任何標記 */
```

讀碼結論：**在 kernel 裡，沒有 `fallthrough;` 卻掉下去的 `case`，就是可疑的**；在 Redis、Lua 這類 user-space 專案裡，fall-through 還是用註解標示，你要靠註解和上下文判斷。

### `switch` 當狀態機

把一個「目前狀態」變數放進 `switch`，每個 `case` 處理完之後改寫狀態變數，外面再包一個迴圈——這是最常見的手寫狀態機。`llex` 某種程度上就是一個以「目前字元」為狀態的狀態機。

Lua 的直譯器主迴圈是另一個極端：

```c
/* lua-5.4.9/lvm.c:1154 */
#define vmdispatch(o)	switch(o)
#define vmcase(l)	case l:
#define vmbreak		break
...
  for (;;) {
    Instruction i;  /* instruction being executed */
    vmfetch();
    ...
    vmdispatch (GET_OPCODE(i)) {
      vmcase(OP_MOVE) {
        StkId ra = RA(i);
        setobjs2s(L, ra, RB(i));
        vmbreak;
      }
```

乍看是普通的 `for (;;) { switch }`，但 `lvm.c:38` 寫著在 GCC 相容編譯器上 `LUA_USE_JUMPTABLE` 預設為 1，此時 `#include "ljumptab.h"` 會把三個巨集重新定義：

```c
/* lua-5.4.9/ljumptab.h */
#define vmdispatch(x)     goto *disptab[x];

#define vmcase(l)     L_##l:

#define vmbreak		vmfetch(); vmdispatch(GET_OPCODE(i));
```

`switch` 變成 GNU C 的 computed goto（`goto *ptr` 搭配 `&&label` 取標籤位址），每個 opcode 處理完直接跳到下一個 opcode 的標籤，不回到迴圈頂端。**同一份原始碼，依編譯器不同有兩種控制流**。這是慣用法鴻溝和巨集鴻溝疊在一起的典型例子：不展開巨集，你會以為 `vmbreak` 是 `break`。讀到這種「控制流關鍵字被巨集包起來」的程式碼，第一件事就是 `grep '#define vmbreak'`，看它有幾個定義、在什麼條件下生效。

## 對比與取捨

| 慣用法 | 讀碼時的快速判讀 | 常見變體 | 要停下來細讀的訊號 |
|---|---|---|---|
| guard clause | 前置條件清單 | `if (!x) return -EINVAL;` | 拿到資源之後還有 `return` |
| goto 階梯 | 標籤由下往上 = 資源由後往前 | 單一 `werr` + NULL 檢查（Redis） | 標籤沒被用到、跳的目標和已配置的資源對不上 |
| `cleanup.h` | 釋放寫在宣告處 | `guard(mutex)(&m);`、`__free(kfree)` | 函式回傳了一個本來該被自動釋放的指標（要找 `no_free_ptr()`） |
| `for (;;)` | 出口全在裡面，逐一分類 | `while (1)` | 有出口不經過迴圈後的共用收尾 |
| `while (i--)` | 反向走訪 `[0, i)` | `while (--i >= 0)` | 計數器是 unsigned |
| `fallthrough;` | 故意的 | `/* fall through */` 註解 | kernel 裡沒標記的 fall-through |
| `do {} while (0)` | 忽略外殼 | `({ ... })` statement expression | 裡面有 `break` |

## 踩雷集錦

1. **「goto 是壞習慣，看到就代表程式碼品質差」**：kernel 的 `goto` 幾乎全是往前跳到清理區，這是 coding style 明文推薦的寫法。真正要警覺的是**往回跳**的 `goto`（`retry:`），它代表一個隱藏的迴圈，要找出它的終止條件。
2. **「標籤名稱描述的是錯誤種類」**：`err_fdr` 不是「fdr 出錯了」，是「從釋放 fdr 開始清」。用錯誤種類的思路讀，你會在第二個 `goto` 就搞混。
3. **「`switch` 裡的 `break` 會離開外層迴圈」**：不會。`llex` 整個函式的語意就建立在這條規則上。想跳出迴圈要用 `return` 或 `goto`。
4. **「`while (n--)` 和 `while (--n)` 差不多」**：差一次迭代，而且結束後 `n` 的值不同（-1 vs 0）。unsigned 的話，`while (n--)` 結束後 `n` 是最大值，之後若再拿 `n` 當索引就越界。
5. **「`do { } while (0)` 是迴圈，可能跑很多次」**：條件是常數 0，只跑一次，編譯器會把外殼最佳化掉。它純粹是語法工具。
6. **「Lua 的 `vmbreak` 就是 `break`」**：在 gcc 上不是。巨集名稱長得像關鍵字時，一定要查它的所有定義。

## 進階：再往深一層

- **讀出口的工具化**：最便宜的工具是 `grep -n 'goto out_revert_acct' fs/pipe.c`，列出一個標籤的所有顯式來源。但拿 `alloc_pipe_info` 試就會發現盲點：`out_revert_acct` 還有一個**自然掉落**的入口（`kcalloc` 失敗時從 `if (pipe->bufs)` 底下流下來），任何「搜 goto」的工具都看不到它。控制流程圖（下一條）才看得到。
- **編譯器怎麼看你的控制流**：`gcc -O2 -S` 看 `do {} while (0)` 和 `for (;;)` 產生的組語，你會發現前者完全沒有跳轉指令。用 `-fdump-tree-cfg` 可以拿到函式的控制流程圖（CFG），對超長函式很有用（Ch 50）。
- **Coccinelle**：kernel 用 semantic patch 工具做大規模的模式檢查，`scripts/coccinelle/` 底下按類別分成 `api/`、`free/`、`locks/`、`null/` 等目錄，`free/` 和 `locks/` 裡的規則就在描述「配置/上鎖之後的出口」這類形狀。讀規則本身就是學慣用法的好方法（Ch 47 會再碰到它）。
- **面試題**：「為什麼 kernel 不用 `[[fallthrough]]`？」答案在 `deprecated.rst`：C17/C18 的 `[[fallthrough]]` 語法在當時的 C 編譯器、靜態分析器、IDE 支援度還不夠普遍，所以先用巨集包 GNU attribute，之後要換只改一處定義。

## 動手練習

1. 打開 `fs/pipe.c` 的 `fifo_open()`（1121 行起），它有 `err_rd`、`err_wr`、`err` 三個標籤，而且 `err_rd` 和 `err_wr` 最後都 `goto err`。畫出每個 `goto` 來源 → 目標的表，標出每個來源點「此時持有哪些東西」。
2. 在 `ladder.c` 加第四個資源 `lock`，放在 `buf` 和 `ring` 之間，修改標籤與 `goto`，用 `fail_at=0..4` 跑一遍，確認 ASan 沒有回報。然後故意漏改一處，看 LeakSanitizer 抓不抓得到。
3. 用 `gcc -E` 展開 `lvm.c`（加 `-DLUA_USE_JUMPTABLE=0` 和不加各一次），比較 `OP_MOVE` 那一段展開後的差異。
4. 在 v6.18 tree 裡 `git grep -n "while (i--)" -- kernel/ mm/`，挑三處，判斷每一處是「反向走訪」還是「錯誤清理」。

## 本章重點整理

- 控制流慣用法是**形狀**，每個形狀帶著承諾；讀碼時先套承諾，只在承諾被打破的地方細讀。
- goto 階梯：配置由上往下、標籤由下往上；跳到 `out_X` 代表 X 之前的資源都已持有。還沒拿資源用 `return`，拿了之後用 `goto`。
- `for (;;)` 要把所有出口分類，特別注意鎖的狀態；`switch` 裡的 `break` 只離開 `switch`。
- `fallthrough` 是 v5.4 週期加入的偽關鍵字巨集，kernel 用 `-Wimplicit-fallthrough=5` 強制只認 attribute。
- `do { } while (0)` 讓多敘述巨集變成一個敘述；看到就忽略外殼。

## 自我檢核

- [ ] 不看本章，能在白板上寫出一個三資源的 goto 階梯，並說出「看到 `goto out_free_ring` 能推論出什麼」
- [ ] 能解釋 `kernel/watch_queue.c` 的 `error_p` 為什麼兩個入口都正確，以及把 `i` 改成 `unsigned` 會怎樣
- [ ] 有人問「`do { } while (0)` 跟直接用大括號包差在哪」，你能舉出 `if/else` 的反例
- [ ] 能說出 `anon_pipe_read()` 裡哪一個出口不經過迴圈後的 `mutex_unlock`，以及為什麼它沒有問題
- [ ] 能說明 `-Wimplicit-fallthrough=3` 和 `=5` 的差別，以及 kernel 為什麼選 5
- [ ] 讀 Lua `lvm.c` 時，能說出 `vmbreak` 在 gcc 和非 gcc 編譯器上分別展開成什麼

## 延伸閱讀

### 書籍

- **《Code Reading: The Open Source Perspective》** — Diomidis Spinellis（Addison-Wesley, 2003）
  - **讀哪裡**：第 2 章「Basic Programming Elements」，講迴圈、條件、`switch`、`goto` 等基本控制結構的讀法。
  - **能學到什麼**：原書用 NetBSD 的例子講「把控制結構當成可辨識的模式」，本章是它的現代 kernel 版。例子雖舊，方法沒變。
  - **前提**：會寫 C。

### 官方文件

- **Linux kernel coding style**（v6.18 tree 的 `Documentation/process/coding-style.rst`，線上版在 docs.kernel.org 的 process/coding-style）
  - **讀哪裡**：第 7 節 "Centralized exiting of functions"，說明 goto 收尾的理由與標籤命名建議（用描述性名稱而非 `err1`、`err2`）。
  - **能學到什麼**：maintainer 自己講為什麼用 goto，以及他們眼中的 "one err bugs"。
  - **前提**：無。

- **Deprecated Interfaces, Language Features, Attributes, and Conventions**（v6.18 tree 的 `Documentation/process/deprecated.rst`）
  - **讀哪裡**："Implicit switch case fall-through" 一節（約 200–236 行）。
  - **能學到什麼**：kernel 禁止隱式 fall-through 的完整理由，以及 `fallthrough` 偽關鍵字的官方說明。同一份文件還列了其他被淘汰的寫法，讀碼時看到舊寫法可以對照。
  - **前提**：無。

- **GCC Manual — Statement Attributes 與 `-Wimplicit-fallthrough`**（gcc.gnu.org 線上手冊，Warning Options 一節）
  - **讀哪裡**：`-Wimplicit-fallthrough=n` 的說明，列出 0–5 每個等級接受哪些註解寫法。
  - **能學到什麼**：理解為什麼 Redis 的 `/* fall-thru */` 在等級 3 被接受、等級 5 不被接受。
  - **前提**：本章的 `ft.c` 實驗。

### 論文 / 經典文章

- **"Go To Statement Considered Harmful"** — Edsger W. Dijkstra，Communications of the ACM 11(3)（1968）
  - **核心論點**：無限制的 goto 讓程式的動態行為難以從靜態文字推斷。
  - **和本章的關聯**：讀完你會發現 kernel 的 goto 收尾恰好**不**違反 Dijkstra 的論點——它只往前跳、只跳到一個集中出口，程式文字和執行順序仍然對應。
  - **讀哪裡**：全文只有一頁多，整篇讀。

- **"Structured Programming with go to Statements"** — Donald E. Knuth，ACM Computing Surveys 6(4)（1974）
  - **核心論點**：回應 Dijkstra，論證某些 goto 用法（包括錯誤出口、跳出巢狀迴圈）讓程式更清楚。
  - **讀哪裡**：前兩節（Introduction 與 Elimination of go to statements）；後面關於效率的部分可略讀。
  - **和本章的關聯**：本章 `goto found` 和清理階梯的理論辯護就在這裡。

控制流讀懂之後，下一個卡住你的通常是 `if` 條件本身：一串 `&`、`|`、`~`、`!!` 混在一起，或一個看似正確的比較因為 signed/unsigned 而永遠成立。

→ [Ch 9 運算式、整數與位元：flags、mask、整數提升的陷阱](./09-expressions-integers-bits.md)
