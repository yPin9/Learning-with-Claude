# Ch 50 — 把編譯器當讀碼工具：.i、.s、objdump、pahole

> **目標**：當原始碼騙你（巨集藏東西、`#ifdef` 藏程式、inline 藏呼叫、struct 的真實大小和你算的不一樣），會叫編譯器和 binutils 把真相吐出來：用 `.i` 看巨集全展開、用 `.s` 與 `objdump -dS` 看實際生成的指令、用 `pahole` 看真實 layout、用 `nm`/`readelf` 確認符號存不存在、用 AST dump 看編譯器怎麼理解一個運算式、用 `faddr2line` 把 oops 裡的 `func+0x1a/0x80` 對回原始碼行。
> **鴻溝**：本章主要練**慣用法鴻溝**（巨集與 GNU C 擴充展開後到底是什麼）與**間接鴻溝**（哪些呼叫是直接的、哪些是函式指標、哪些被 inline 掉了）。

> **環境**：WSL2 Ubuntu 22.04；gcc 11.4（建 kernel 用）、clang 14（AST dump 用）、GNU binutils 2.38（objdump、nm、readelf、addr2line）、pahole v1.25；kernel v6.18 x86_64 以 `O=~/code_reading/build` 建好（defconfig + kvm_guest + ftrace/kprobes/BPF/debugfs，含 DWARF5 debug info 與 BTF）；oops 是在 QEMU（TCG）裡用 `~/code_reading/qemu/run-script.sh` 實際觸發的。

## 為什麼需要這個？

Spinellis 在原書 10.6 節提過一個今天仍然被低估的想法：**編譯器是你手上最權威的讀碼工具**。原因很直接——你看到的原始碼和真正跑起來的程式之間，隔著前置處理器、型別系統、最佳化器、連結器四層轉換。你讀原始碼時是在**腦內模擬**這四層；模擬錯了，你就讀錯了。編譯器不用模擬，它就是那四層本身。

kernel 讓這件事比一般專案重要得多：

- 一行 `wait_event_interruptible_exclusive(pipe->rd_wait, pipe_readable(pipe))` 展開後是 577 個字元、含一個 `for (;;)` 迴圈、一個 `goto`、兩層 statement expression。
- 一行 `guard(mutex)(&lock);` 背後是一個 typedef、兩個 inline 函式、一個 `__attribute__((__cleanup__))` 變數。
- `struct pipe_inode_info` 有多大、哪些欄位擠在同一條 cache line，取決於 `.config`（`CONFIG_WATCH_QUEUE` 開不開會多出兩個欄位）。
- 你在原始碼裡看到的函式呼叫，有一半在 `-O2` 下被 inline 掉了，根本不存在於 vmlinux。

[Ch 49](./49-clangd-lsp.md) 的 clangd 已經能回答其中一部分（hover 看巨集展開），但它只回答你游標指的那一點。這一章把編譯器每一層的輸出都攤開，讓你可以**整片**地看、`grep`、`diff`。

## 先建立直覺

```
  fs/pipe.c（1,553 行）
      │  前置處理器（cpp）：展開巨集、include 標頭、依 .config 刪掉 #ifdef 區塊
      ▼
  fs/pipe.i（81,576 行）  ◀── make O=../build fs/pipe.i      「巨集到底是什麼？」
      │  編譯器前端 + 最佳化器：型別檢查、inline、分支配置
      ▼
  fs/pipe.s（組語 5,833 行）◀── make O=../build fs/pipe.s     「生成了什麼指令？哪個分支被當成常態？」
      │  組譯器
      ▼
  fs/pipe.o ──────────────── objdump -dS / -dr、readelf -s   「呼叫了誰？哪些被 inline 掉？」
      │  連結器（加上其他 ~3,000 個 .o）
      ▼
  vmlinux ────────────────── nm、pahole、addr2line/faddr2line  「符號在不在？struct 多大？oops 在哪一行？」
```

每一層都是一張**證詞**。原始碼是「作者說它做什麼」，每一層產物是「機器實際上做了什麼」。讀碼卡住時，往下翻一層，通常就有答案。

> 如果你對前置處理器的基本運作還不熟，先回看 [Ch 13 — 前置處理器基礎](./13-preprocessor-basics.md) 的「展開來看」一節；statement expression `({ ... })` 在 [Ch 15](./15-kernel-c-gnu-extensions.md)。

## 第一層：`.i`，巨集全展開

Kbuild 對每個 `.c` 都提供 `.i` 目標，用和正式編譯**完全相同的旗標**跑前置處理器：

```make
cmd_cpp_i_c       = $(CPP) $(c_flags) -o $@ $<
```
（`scripts/Makefile.build:185`（v6.18））

「完全相同的旗標」是重點：它會讀 `include/generated/autoconf.h`，所以展開結果是**你的 `.config`** 下的結果。

```console
$ cd ~/code_reading/linux
$ time make O=../build fs/pipe.i
  ...
make[1]: Leaving directory '/home/ypp/code_reading/build'
real	0m6.662s
$ wc -l fs/pipe.c ../build/fs/pipe.i
   1553 fs/pipe.c
  81576 ../build/fs/pipe.i
$ wc -c fs/pipe.c ../build/fs/pipe.i
  37727 fs/pipe.c
3396625 ../build/fs/pipe.i
$ grep '^# [0-9]* "' ../build/fs/pipe.i | awk '{print $3}' | sort -u | wc -l
718
```

**行數膨脹 52 倍、位元組膨脹 90 倍、拉進 718 個不同的檔案**（大部分是標頭）。這個數字本身就是一課：當你覺得「`fs/pipe.c` 才一千五百行，應該讀得完」，編譯器看到的其實是八萬行。你讀不懂的那些名字，有很大一部分定義在這 718 個檔案裡。

`.i` 檔開頭的 `# 數字 "檔名"` 叫 **linemarker**，告訴你下面幾行來自哪個檔案的第幾行：

```
# 0 "/home/ypp/code_reading/linux/fs/pipe.c"
# 1 "/home/ypp/code_reading/build//"
# 0 "<built-in>"
# 0 "<command-line>"
# 1 "/home/ypp/code_reading/linux/include/linux/compiler-version.h" 1
# 0 "<command-line>" 2
# 1 "/home/ypp/code_reading/linux/include/linux/kconfig.h" 1
```

要在 8 萬行裡找到你關心的那一行，搜 linemarker：

```console
$ grep -n '^# 388 ' ../build/fs/pipe.i
17332:# 388 "/home/ypp/code_reading/linux/include/linux/seqlock.h"
80495:# 388 "/home/ypp/code_reading/linux/fs/pipe.c"
```

### 範例一：`wait_event_interruptible_exclusive` 展開後

原始碼（`fs/pipe.c:388`（v6.18））：

```c
		if (wait_event_interruptible_exclusive(pipe->rd_wait, pipe_readable(pipe)) < 0)
			return -ERESTARTSYS;
```

`.i` 檔第 80496 行（一整行 577 個字元，這裡折行顯示）：

```c
  if (({ int __ret = 0; do { might_resched(); } while (0); if (!(pipe_readable(pipe))) __ret = ({ __label__ __out;
  struct wait_queue_entry __wq_entry; long __ret = 0; init_wait_entry(&__wq_entry, 1 ? 0x01 : 0); for (;;) {
  long __int = prepare_to_wait_event(&pipe->rd_wait, &__wq_entry, 0x00000001); if (pipe_readable(pipe)) break;
  if ((!__builtin_constant_p(0x00000001) || (0x00000001 & (0x00000001 | 0x00000100))) && __int) { __ret = __int;
  goto __out; } schedule(); if (pipe_readable(pipe)) break; } finish_wait(&pipe->rd_wait, &__wq_entry);
  __out: __ret; }); __ret; }) < 0)
```

把它翻成人話，這個「巨集」其實是一個完整的等待迴圈：

1. 先檢查條件 `pipe_readable(pipe)`，成立就不用等（快速路徑）。
2. 否則建一個 `wait_queue_entry`（`1 ? 0x01 : 0` 是 `WQ_FLAG_EXCLUSIVE`，因為呼叫的是 `_exclusive` 版本），進入 `for (;;)`。
3. 每圈呼叫 `prepare_to_wait_event()` 把自己掛上 `pipe->rd_wait`、狀態設成 `0x00000001`（`TASK_INTERRUPTIBLE`），再檢查一次條件，然後 `schedule()` 讓出 CPU。
4. 被訊號打斷時 `prepare_to_wait_event()` 回傳非零，`goto __out` 帶著錯誤碼離開；最後 `finish_wait()` 把自己從佇列拿下來。

幾個細節只有展開後才看得到：`might_sleep()` 在這個 config 下變成 `do { might_resched(); } while (0)`（沒開 `CONFIG_DEBUG_ATOMIC_SLEEP`）；`__label__ __out` 是 GNU C 的區域 label，讓同一個函式裡展開多次也不會撞名；`TASK_INTERRUPTIBLE` 已經變成數字常數，`__builtin_constant_p(0x00000001)` 在編譯期就決定了那個 `if` 的一半。[Ch 33](./33-wait-queues-completion.md) 會從語意上講 wait queue，這裡你已經看到它的骨架。

### 範例二：`guard(mutex)` 展開後

v6.18 的 kernel 大量使用 `cleanup.h` 的 scope-based 鎖（[Ch 17](./17-kernel-error-conventions.md) 介紹過）。`fs/pipe.c` 沒有用，換一個短函式：

```c
static ssize_t profiling_store(struct kobject *kobj,
				   struct kobj_attribute *attr,
				   const char *buf, size_t count)
{
	int ret;
	static DEFINE_MUTEX(lock);

	/* ... */
	guard(mutex)(&lock);
	if (prof_on)
		return -EEXIST;
```
（`kernel/ksysfs.c:90-104`（v6.18），節錄）

`make O=../build kernel/ksysfs.i` 之後，`profiling_store` 變成：

```c
static ssize_t profiling_store(struct kobject *kobj,
       struct kobj_attribute *attr,
       const char *buf, size_t count)
{
 int ret;
 static struct mutex lock = { .owner = { (0) } , .wait_lock = (raw_spinlock_t) { .raw_lock = { { .val = { (0) } } }, } , .wait_list = { &(lock.wait_list), &(lock.wait_list) } };

 class_mutex_t __UNIQUE_ID_guard503 __attribute__((__cleanup__(class_mutex_destructor))) = class_mutex_constructor(&lock);
 if (prof_on)
  return -17;
```

`guard(mutex)(&lock)` 變成一個型別為 `class_mutex_t`、名字由 `__UNIQUE_ID` 產生的區域變數，初始值是 `class_mutex_constructor(&lock)`，並帶著 `__cleanup__(class_mutex_destructor)` 屬性——**gcc 保證這個變數離開 scope 時（包括 `return -17` 那一行）會呼叫 destructor**。這兩個函式從哪來？同一個 `.i` 檔第 16017 行，`DEFINE_GUARD(mutex, ...)` 展開的結果（節錄、排版過）：

```c
typedef struct mutex * class_mutex_t;
static inline ... void class_mutex_destructor(struct mutex * *p)
{ struct mutex * _T = *p; if (!({ unsigned long _rc = ( unsigned long)(_T);
  __builtin_expect(!!((_rc - 1) >= -4095 - 1), 0); })) { mutex_unlock(_T); }; }
static inline ... struct mutex * class_mutex_constructor(struct mutex * _T)
{ struct mutex * t = ({ mutex_lock(_T); _T; }); return t; };
```

constructor 呼叫 `mutex_lock`，destructor 在指標不是 NULL 也不是 `ERR_PTR` 時呼叫 `mutex_unlock`。原始碼裡 `guard(mutex)` 這一行**完全看不出來**「函式每一個 return 前都會 unlock」，展開之後一目了然。巨集的源頭在 `include/linux/cleanup.h:378-380`（`DEFINE_GUARD`）與 `:400-401`（`guard`），以及 `include/linux/mutex.h:228` 的 `DEFINE_GUARD(mutex, struct mutex *, mutex_lock(_T), mutex_unlock(_T))`。

### 讀 `.i` 的實用技巧

- **用 linemarker 定位**：`grep -n '^# <行號> ".*<檔名>"'` 找到原始碼那一行在 `.i` 的位置，再往下看一兩行。
- **用 `clang-format` 或 `indent` 排版**：`.i` 裡巨集展開常常是一整行。把那一行存成檔案，跑 `clang-format`，可讀性立刻不同。
- **diff 兩個 config**：同一個檔案在兩個 `.config` 下各產生一份 `.i`，`diff` 就是「這個選項到底改了什麼程式碼」的精確答案。
- **`-dM` 列出所有巨集定義**：`gcc -E -dM` 加上同樣的旗標，會輸出前置處理結束時所有生效的 `#define`，可以確認某個巨集最後的定義是哪一個。

## 第二層：`.s` 與 objdump，實際生成的指令

### `.s`：帶註解的組語

```make
cmd_cc_s_c = $(CC) $(filter-out $(DEBUG_CFLAGS) $(CC_FLAGS_LTO), $(c_flags)) -fverbose-asm -S -o $@ $<
```
（`scripts/Makefile.build:179`（v6.18））

`-fverbose-asm` 讓 gcc 在組語旁邊註明原始碼行與變數名：

```console
$ make O=../build fs/pipe.s
$ wc -l ../build/fs/pipe.s
5833 ../build/fs/pipe.s
$ sed -n 460,495p ../build/fs/pipe.s
anon_pipe_read:
	endbr64
1:	call	__fentry__
	.section __mcount_loc, "a",@progbits
	.quad 1b
	.previous
	pushq	%r15	#
	...
# /home/ypp/code_reading/linux/fs/pipe.c:273: 	struct pipe_inode_info *pipe = filp->private_data;
	movq	24(%rbx), %r15	# filp_76->private_data, pipe
# /home/ypp/code_reading/linux/include/linux/uio.h:314: 	return i->count;
	movq	%gs:__ref_stack_chk_guard(%rip), %rax	# __ref_stack_chk_guard, tmp235
	movq	%rax, 80(%rsp)	# tmp235, D.68449
	movq	24(%rsi), %rax	# MEM[(const struct iov_iter *)to_72(D)].D.41693.D.41692.count, tmp235
	...
# /home/ypp/code_reading/linux/fs/pipe.c:278: 	if (unlikely(total_len == 0))
	testq	%rax, %rax	# total_len
	je	.L77	#,
# /home/ypp/code_reading/linux/fs/pipe.c:282: 	mutex_lock(&pipe->mutex);
	movq	%r15, %rdi	# pipe,
# /home/ypp/code_reading/linux/fs/pipe.c:281: 	ret = 0;
	xorl	%r12d, %r12d	# <retval>
# /home/ypp/code_reading/linux/fs/pipe.c:282: 	mutex_lock(&pipe->mutex);
	call	mutex_lock	#
```

這一小段就回答了好幾個讀碼問題：

- **函式開頭的 `call __fentry__`** 加上 `__mcount_loc` 區段：這是 ftrace 的掛勾點（`-pg -mfentry -mrecord-mcount` 產生的）。[Ch 51](./51-dynamic-tracing.md) 的 function tracer 就是在執行期把這個 call 改成跳到 tracer。
- **`24(%rbx)` 就是 `filp->private_data`**：offset 24。想驗證？下面的 pahole 一節教你查。
- **`iov_iter_count(to)` 被 inline 了**：它變成一行 `movq 24(%rsi), %rax`，原始碼行標的是 `uio.h:314`。
- **`unlikely(total_len == 0)`**：`unlikely(x)` 是 `__builtin_expect(!!(x), 0)`（`include/linux/compiler.h:77`（v6.18））。生成的程式碼是 `je .L77`——**條件成立時跳走**，條件不成立（常態）時直接 fall through 到 `mutex_lock`。這就是 `likely`/`unlikely` 的作用：把常態路徑排成一條直線，讓 CPU 的指令擷取不用跳（[Ch 18](./18-annotations-and-sparse.md) 講過這個標註的語意）。`.L77` 在函式尾端，下面 objdump 那節會看到它的位址。
- **stack protector**：`%gs:__ref_stack_chk_guard` 是 stack canary，來自 `-fstack-protector-strong` 和 `-mstack-protector-guard-reg=gs`。

### `objdump -dS`：組語對照原始碼

`.s` 是編譯器的輸出，`objdump` 讀的是**組譯後的 `.o`**（或 vmlinux），兩者可以互相印證。`-S` 穿插原始碼、`-l` 標行號、`--disassemble=<符號>` 只反組譯一個函式（binutils 2.32 起支援）：

```console
$ cd ~/code_reading/build
$ objdump -d --no-show-raw-insn -S -l --disassemble=anon_pipe_read fs/pipe.o | sed -n 7,20p
0000000000000240 <anon_pipe_read>:
anon_pipe_read():
/home/ypp/code_reading/linux/fs/pipe.c:270
	return tail;
}

static ssize_t
anon_pipe_read(struct kiocb *iocb, struct iov_iter *to)
{
     240:	endbr64
     244:	call   249 <anon_pipe_read+0x9>
     249:	push   %r15
	...
/home/ypp/code_reading/linux/fs/pipe.c:278
	if (unlikely(total_len == 0))
     282:	test   %rax,%rax
     285:	je     5d7 <anon_pipe_read+0x397>
```

注意 `244: call 249`：在 `.o` 裡，外部符號的位址還沒填，objdump 顯示的是「呼叫下一條指令」。加 `-r` 印出 relocation 才看得出它其實是 `__fentry__`。`je 5d7` 跳到 `+0x397`——函式總長 0x3dd，這是在尾端的冷路徑：

```console
$ objdump -dr --no-show-raw-insn --disassemble=anon_pipe_read fs/pipe.o | grep -E '^\s+5d7:' -A1
     5d7:	xor    %r12d,%r12d
     5da:	jmp    38b <anon_pipe_read+0x14b>
```

`total_len == 0` 的路徑就是「`ret = 0`，跳回共用的 return 序列」。

### 誰被 inline、誰是函式指標

把 `anon_pipe_read` 裡所有 `call` 的 relocation 目標統計出來：

```console
$ objdump -dr --no-show-raw-insn --disassemble=anon_pipe_read fs/pipe.o \
    | grep -A1 -E 'call' | grep -E 'R_X86_64_(PLT32|PC32)' | awk '{print $3}' | sort | uniq -c
      1 __SCT__might_resched-0x4
      1 __fentry__-0x4
      1 __stack_chk_fail-0x4
      3 __wake_up_sync_key-0x4
      3 __x86_indirect_thunk_rax-0x4
      1 copy_page_to_iter-0x4
      1 finish_wait-0x4
      1 init_wait_entry-0x4
      1 kill_fasync-0x4
      2 mutex_lock-0x4
      3 mutex_unlock-0x4
      1 prepare_to_wait_event-0x4
      1 schedule-0x4
```

拿這張表對照原始碼，可以讀出三件原始碼不會告訴你的事：

1. **被 inline 掉的**：原始碼裡呼叫了 `pipe_buf_confirm`（`include/linux/pipe_fs_i.h:290` 的 `static inline`）、`pipe_update_tail`、`pipe_readable`（`fs/pipe.c:238`、`:230` 的 `static inline`）、`iov_iter_count`、`pipe_empty`、`pipe_full`——**表裡一個都沒有**。它們不存在於 vmlinux，`nm vmlinux | grep -w pipe_update_tail` 什麼都找不到，ftrace 也追不到它們。讀碼時把它們當成「原地展開的程式碼」，而不是「一次函式呼叫」。
2. **函式指標呼叫**：`__x86_indirect_thunk_rax` 出現 3 次。這是 retpoline（Spectre v2 緩解）的 thunk：每一個透過暫存器的間接呼叫都被改成 `call __x86_indirect_thunk_rax`。所以**每一次 `call __x86_indirect_thunk_rax` 都是一個函式指標呼叫**。用 `addr2line -i` 對這 3 個呼叫位址（`.o` 裡的 0x2f8、0x3df、0x4a4）查 inline 鏈：

```console
$ for a in 2f8 3df 4a4; do addr2line -i -e fs/pipe.o 0x$a | sed 's#/home/ypp/code_reading/linux/##'; echo --; done
include/linux/pipe_fs_i.h:295
fs/pipe.c:336
--
include/linux/pipe_fs_i.h:282
fs/pipe.c:242
fs/pipe.c:361
--
include/linux/pipe_fs_i.h:282
fs/pipe.c:242
fs/pipe.c:361
--
```

第一個是 `pipe_buf_confirm()` 裡的 `return buf->ops->confirm(pipe, buf);`（`pipe_fs_i.h:295`），在 `fs/pipe.c:336` 被 inline 進來；後兩個都是 `pipe_buf_release()` 裡的 `ops->release(pipe, buf);`（`pipe_fs_i.h:282`），經由 `pipe_update_tail()`（`pipe.c:242`）inline 進 `pipe.c:361`。原始碼裡只有一個呼叫點，**編譯器把它複製成兩份**放在不同的路徑上。所以這個函式在原始碼層有 2 個間接呼叫點、在 binary 層有 3 個間接 call 指令。這是在 binary 層找間接鴻溝的捷徑。
3. **巨集展開出來的呼叫**：`init_wait_entry`、`prepare_to_wait_event`、`schedule`、`finish_wait` 全部來自第一層看過的 `wait_event_interruptible_exclusive`。`__SCT__might_resched` 是 static call（`might_resched()` 在 `PREEMPT_DYNAMIC` 下經由 static call 實作）。

## 第三層：pahole，真實的 struct layout

[Ch 11](./11-structs-unions-tagged.md) 教你讀 struct 定義，但定義上看不出三件事：每個欄位的 offset、padding 在哪、哪些欄位擠在同一條 cache line。pahole 從 DWARF debug info 讀出編譯器實際排出來的 layout：

```console
$ time pahole -C pipe_inode_info vmlinux
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
	unsigned int               ring_size;            /*    92     4 */
	unsigned int               nr_accounted;         /*    96     4 */
	unsigned int               readers;              /*   100     4 */
	unsigned int               writers;              /*   104     4 */
	unsigned int               files;                /*   108     4 */
	unsigned int               r_counter;            /*   112     4 */
	unsigned int               w_counter;            /*   116     4 */
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
real	0m1.850s
```

對照原始碼（`include/linux/pipe_fs_i.h:86-118`（v6.18）），pahole 告訴你：

- **欄位比原始碼少兩個**：原始碼裡的 `note_loss` 和 `watch_queue` 包在 `#ifdef CONFIG_WATCH_QUEUE` 裡，這個 build 的 `.config` 是 `# CONFIG_WATCH_QUEUE is not set`。pahole 顯示的是**這個 kernel**的 struct，不是原始碼的 struct。
- **`struct mutex` 是 32 bytes**：因為沒開 `CONFIG_DEBUG_MUTEXES`。開了 lockdep，它會大得多，整個 struct 的 cache line 分布都會變。
- **`head`/`tail` 在 offset 80–87，跨在第一條 cache line 的後段**；`pipe_index_t` 在 64 位元是 `unsigned int`（原始碼 `#ifdef CONFIG_64BIT` 那段）。union 讓 `head_tail` 可以用一次 8-byte 讀取同時拿到兩者——這就是原始碼註解「This has to match the 'union pipe_index' above」的用意。
- **一個 7-byte hole**：`poll_usage` 是 `bool`，下一個欄位是 8-byte 對齊的指標陣列。開了 `CONFIG_WATCH_QUEUE` 時 `note_loss`（也是 `bool`）會填進這個洞裡。

同樣的方法看 `pipe_buffer`，可以驗證 `.s` 裡看到的 offset：

```console
$ pahole -C pipe_buffer vmlinux
struct pipe_buffer {
	struct page *              page;                 /*     0     8 */
	unsigned int               offset;               /*     8     4 */
	unsigned int               len;                  /*    12     4 */
	const struct pipe_buf_operations  * ops;         /*    16     8 */
	unsigned int               flags;                /*    24     4 */

	/* XXX 4 bytes hole, try to pack */

	long unsigned int          private;              /*    32     8 */

	/* size: 40, cachelines: 1, members: 6 */
	/* sum members: 36, holes: 1, sum holes: 4 */
	/* last cacheline: 40 bytes */
};
```

讀組語時看到 `mov 0x10(%rax),%rdx` 接著一個 indirect call，查表就知道 `0x10` 是 `ops`——那是在呼叫 `buf->ops->xxx`。pahole 把組語裡的數字翻回欄位名，是讀 binary 時最常用的對照表。

## 第四層：nm 與 readelf，符號到底在不在

「這個函式有被編進我的 kernel 嗎？」是 [Ch 36](./36-kconfig-kbuild-ifdef.md) 的核心問題，最終的仲裁者是符號表：

```console
$ nm vmlinux | grep -wE 'anon_pipe_read|fifo_pipe_read|pipe_readable|pipe_double_lock|__x64_sys_pipe2|do_pipe2|watch_queue_init|free_pipe_info'
ffffffff815946d0 T __x64_sys_pipe2
ffffffff81592740 t anon_pipe_read
ffffffff81594520 t do_pipe2
ffffffff81592b30 t fifo_pipe_read
ffffffff81593db0 T free_pipe_info
ffffffff81593a20 T pipe_double_lock
$ ls kernel/watch_queue.o
ls: cannot access 'kernel/watch_queue.o': No such file or directory
```

讀法：

- **大寫 `T`** 是全域符號（可以被其他檔案呼叫），**小寫 `t`** 是 `static` 函式。`anon_pipe_read` 是 `t`，所以只有 `fs/pipe.c` 自己能直接呼叫它；外界只能透過 `pipeanon_fops` 這張表——符號表本身就在告訴你「這裡有間接呼叫」。
- **`pipe_readable` 不在**：它是 `static inline`，全部被 inline 掉了。
- **`watch_queue_init` 不在**，`kernel/watch_queue.o` 也不存在：`kernel/Makefile:139` 寫的是 `obj-$(CONFIG_WATCH_QUEUE) += watch_queue.o`，選項沒開，整個檔案沒有編譯。
- **`__x64_sys_pipe2` 在**：這是 `SYSCALL_DEFINE2(pipe2, ...)` 拼出來的名字（[Ch 29](./29-syscall-entry-paths.md)），原始碼裡找不到，符號表裡有。

`readelf -s` 看單一 `.o` 的符號表，多了**大小**欄位：

```console
$ readelf -sW fs/pipe.o | awk 'NR<4 || /anon_pipe_read|do_pipe2|free_pipe_info|__x64_sys_pipe2/'

Symbol table '.symtab' contains 168 entries:
   Num:    Value          Size Type    Bind   Vis      Ndx Name
    15: 0000000000000240   989 FUNC    LOCAL  DEFAULT    1 anon_pipe_read
    44: 0000000000002020   236 FUNC    LOCAL  DEFAULT    1 do_pipe2
   131: 00000000000018b0   168 FUNC    GLOBAL DEFAULT    1 free_pipe_info
   147: 00000000000021d0    31 FUNC    GLOBAL DEFAULT    1 __x64_sys_pipe2
```

`anon_pipe_read` 989 bytes（0x3dd）——記住這個數字，下一節它會讓 faddr2line 失敗一次。

## 第五層：AST dump 與 tree dump，編譯器怎麼理解一個運算式

kernel 的 TU 太大，AST dump 會是幾百 MB；這一招適合把一段看不懂的 kernel 慣用法**抽出來**，放進 user-space 小程式裡讓編譯器解釋。以 [Ch 16](./16-container-of.md) 的 `container_of` 為例：

```c
// ast.c
#include <stddef.h>

#define container_of(ptr, type, member) \
	((type *)((char *)(ptr) - offsetof(type, member)))

struct list_head { struct list_head *next, *prev; };
struct item { int key; struct list_head node; };

int key_of(struct list_head *n)
{
	return container_of(n, struct item, node)->key;
}
```

（這是簡化版；kernel 的 `container_of` 多了型別檢查，見 Ch 16。）

clang 的 AST dump：

```console
$ clang-14 -fsyntax-only -Xclang -ast-dump -fno-color-diagnostics ast.c | sed -n '/FunctionDecl.*key_of/,$p'
`-FunctionDecl 0x2d9dae10 <line:9:1, line:12:1> line:9:5 key_of 'int (struct list_head *)'
  |-ParmVarDecl 0x2d9dad10 <col:12, col:30> col:30 used n 'struct list_head *'
  `-CompoundStmt 0x2d9db290 <line:10:1, line:12:1>
    `-ReturnStmt 0x2d9db280 <line:11:2, col:45>
      `-ImplicitCastExpr 0x2d9db268 <line:4:2, line:11:45> 'int' <LValueToRValue>
        `-MemberExpr 0x2d9db238 <line:4:2, line:11:45> 'int' lvalue ->key 0x2d9dac28
          `-ParenExpr 0x2d9db218 <line:4:2, col:51> 'struct item *'
            `-CStyleCastExpr 0x2d9db1f0 <col:3, col:50> 'struct item *' <BitCast>
              `-ParenExpr 0x2d9db1d0 <col:11, col:50> 'char *'
                `-BinaryOperator 0x2d9db108 <col:12, /usr/lib/llvm-14/lib/clang/14.0.0/include/stddef.h:104:47> 'char *' '-'
                  |-CStyleCastExpr 0x2d9daf90 <ast.c:4:12, col:24> 'char *' <BitCast>
                  | `-ImplicitCastExpr 0x2d9daf78 <col:20, col:24> 'struct list_head *' <LValueToRValue> part_of_explicit_cast
                  |   `-ParenExpr 0x2d9daf58 <col:20, col:24> 'struct list_head *' lvalue
                  |     `-DeclRefExpr 0x2d9daf00 <line:11:22> 'struct list_head *' lvalue ParmVar 0x2d9dad10 'n' 'struct list_head *'
                  `-OffsetOfExpr 0x2d9db010 </usr/lib/llvm-14/lib/clang/14.0.0/include/stddef.h:104:24, col:47> 'unsigned long'
```

由下往上讀，就是編譯器的理解：拿參數 `n`（`DeclRefExpr`）→ 轉成 `char *` → 減去 `offsetof`（`OffsetOfExpr`）→ 結果轉成 `struct item *` → 取 `->key`。每個節點都標了型別，括號的意義、隱式轉換在哪裡發生，全部明確寫出來。位置欄位 `<line:4:2, line:11:45>` 同時指向巨集定義（第 4 行）與呼叫點（第 11 行），告訴你這個節點是巨集展開的產物。

gcc 的 `-fdump-tree-*` 看的是另一個角度——最佳化前後的中間表示：

```console
$ gcc -O2 -c -fdump-tree-original -fdump-tree-optimized ast.c && ls ast.c.*
ast.c.005t.original
ast.c.244t.optimized
$ sed -n '/key_of/,$p' ast.c.005t.original
;; Function key_of (null)
;; enabled by -tree-original

{
  return ((struct item *) n + 18446744073709551608)->key;
}
$ sed -n '/key_of/,$p' ast.c.244t.optimized
int key_of (struct list_head * n)
{
  int _3;

  <bb 2> [local count: 1073741824]:
  _3 = MEM[(struct item *)n_1(D) + -8B].key;
  return _3;
}
$ objdump -d --no-show-raw-insn ast.o | sed -n '/<key_of>/,$p'
0000000000000000 <key_of>:
   0:	endbr64
   4:	mov    -0x8(%rdi),%eax
   7:	ret
```

三層證詞一致：`offsetof(struct item, node)` 是 8（`int key` 佔 4、加 4 bytes padding 讓 `node` 對齊到 8），`18446744073709551608` 是 2⁶⁴−8，也就是 −8 的無號表示；最佳化後整個 `container_of` 變成**一條** `mov -0x8(%rdi),%eax`。「`container_of` 很昂貴嗎？」——不，它是零成本的位址減法，這是看指令才敢下的結論。

## 第六層：addr2line 與 faddr2line，把 oops 對回原始碼

### 一個真的 oops

在 QEMU 裡用 sysrq 觸發 panic（`echo c > /proc/sysrq-trigger`，guest 腳本交給 `run-script.sh` 執行）：

```
[    2.716470] sysrq: Trigger a crash
[    2.717671] Kernel panic - not syncing: sysrq triggered crash
[    2.718286] CPU: 0 UID: 0 PID: 69 Comm: sh Not tainted 6.18.0 #1 PREEMPT(voluntary)
[    2.718927] Call Trace:
[    2.719624]  <TASK>
[    2.719935]  vpanic+0x354/0x360
[    2.720225]  panic+0x5b/0x60
[    2.720321]  sysrq_handle_crash+0x1a/0x20
[    2.720439]  __handle_sysrq+0x97/0x180
[    2.720532]  write_sysrq_trigger+0x59/0x80
[    2.720630]  proc_reg_write+0x59/0xa0
[    2.720726]  vfs_write+0xf8/0x420
[    2.720820]  ? handle_mm_fault+0xde/0x240
[    2.720925]  ksys_write+0x65/0xe0
[    2.721009]  do_syscall_64+0xa4/0x280
[    2.721103]  entry_SYSCALL_64_after_hwframe+0x77/0x7f
```

每一行的格式是 `函式+偏移/函式大小`。偏移是**相對函式開頭**的位元組數（不受 KASLR 影響），大小用來在同名符號之間消歧義。行首的 `?` 表示 unwinder 不確定這個位址是真的呼叫框架，可能只是 stack 上的殘留值。

### faddr2line：先失敗一次

`scripts/faddr2line` 吃的就是這個格式。先拿上一節 `readelf` 量到的大小（0x3dd）試：

```console
$ ~/code_reading/linux/scripts/faddr2line vmlinux anon_pipe_read+0x45/0x3dd
skipping anon_pipe_read address at 0xffffffff81592785 due to size mismatch (0x3dd != 0x3e0)
no match for anon_pipe_read+0x45/0x3dd
```

失敗了。vmlinux 裡 kallsyms 記錄的大小是 0x3e0，不是 `.o` 裡的 0x3dd：kallsyms 算大小的方式是「到下一個符號的距離」，而 `anon_pipe_read`（0xffffffff81592740）後面的下一個符號是 0xffffffff81592b20 的 `__pfx_fifo_pipe_read`（`-fpatchable-function-entry=16,16` 在每個函式前面留 16 bytes 填充，並為它產生 `__pfx_` 符號；加上 `-falign-functions=16` 對齊），中間多了 3 bytes。**oops 印的是 kallsyms 的大小**，所以直接複製 oops 裡的字串最保險。換成正確的大小：

```console
$ time ~/code_reading/linux/scripts/faddr2line vmlinux anon_pipe_read+0x45/0x3e0
anon_pipe_read+0x45/0x3e0:
anon_pipe_read at fs/pipe.c:278
real	0m17.263s
$ ~/code_reading/linux/scripts/faddr2line vmlinux anon_pipe_read+0x35/0x3e0
anon_pipe_read+0x35/0x3e0:
iov_iter_count at include/linux/uio.h:314
(inlined by) anon_pipe_read at fs/pipe.c:271
```

第二個查詢展示了 faddr2line 最有價值的能力：**inline 鏈**。`+0x35` 這條指令在原始碼上屬於 `iov_iter_count()` 的 `return i->count;`，而 `iov_iter_count` 是被 inline 進 `anon_pipe_read` 第 271 行的。oops 只會說「`anon_pipe_read+0x35`」，faddr2line 告訴你真正出事的是哪個 inline 函式的哪一行。它花了 17 秒，因為要對整個 vmlinux（含 debug info）跑 `nm` 和 `readelf`。

對上面那個真實 oops：

```console
$ ~/code_reading/linux/scripts/faddr2line vmlinux sysrq_handle_crash+0x1a/0x20 write_sysrq_trigger+0x59/0x80 vfs_write+0xf8/0x420
sysrq_handle_crash+0x1a/0x20:
sysrq_handle_crash at drivers/tty/sysrq.c:154

write_sysrq_trigger+0x59/0x80:
write_sysrq_trigger at drivers/tty/sysrq.c:1223

vfs_write+0xf8/0x420:
vfs_write at fs/read_write.c:684
```

```c
static void sysrq_handle_crash(u8 key)
{
	/* release the RCU read lock before crashing */
	rcu_read_unlock();

	panic("sysrq triggered crash\n");
}
```
（`drivers/tty/sysrq.c:149-155`（v6.18））

154 行就是 `panic(...)`。`vfs_write` 的 684 行是 `ret = file->f_op->write(file, buf, count, pos);`——又是一個函式指標呼叫，下一層 `proc_reg_write` 就是它的實際目標。**oops 的 call trace 是唯一能直接告訴你「函式指標這次指向誰」的靜態證據**，faddr2line 讓你把每一層都釘到原始碼行。

注意 call trace 裡的位址是**返回位址**（`call` 指令的下一條）。多數情況下它對應的行就是呼叫那一行；但如果呼叫是函式的最後一條指令（例如呼叫 noreturn 的 `panic`），返回位址可能落在函式尾端的填充上，對到的行會偏一點。讀 trace 時把它理解成「在這一行附近發出的呼叫」。

### addr2line 與 decode_stacktrace.sh

有絕對位址時（例如 `nokaslr` 開機，或已經從 `/proc/kallsyms` 算出位址），可以直接用 binutils 的 `addr2line`：

```console
$ printf '%x\n' $((0xffffffff81592740+0x45))
ffffffff81592785
$ addr2line -f -i -p -e vmlinux 0xffffffff81592785
anon_pipe_read at /home/ypp/code_reading/linux/fs/pipe.c:278
```

但 KASLR 開啟時 oops 裡的絕對位址每次開機都不同，這是 faddr2line 用 `函式+偏移` 而不是絕對位址的原因（腳本開頭的註解：「addr2line doesn't work with KASLR addresses」）。

整段 trace 要一次解，用 `scripts/decode_stacktrace.sh`：

```console
$ ~/code_reading/linux/scripts/decode_stacktrace.sh ~/code_reading/build/vmlinux ~/code_reading/linux < trace.txt
[    2.719935]  vpanic (kernel/panic.c:490)
[    2.720225]  panic (kernel/panic.c:626)
[    2.720321]  sysrq_handle_crash (drivers/tty/sysrq.c:154)
[    2.720439]  __handle_sysrq (include/linux/rcupdate.h:899 drivers/tty/sysrq.c:634)
[    2.720532]  write_sysrq_trigger (drivers/tty/sysrq.c:1223)
[    2.720630]  proc_reg_write (arch/x86/include/asm/atomic.h:85 include/linux/atomic/atomic-arch-fallback.h:787 include/linux/atomic/atomic-arch-fallback.h:1238 include/linux/atomic/atomic-instrumented.h:611 fs/proc/inode.c:205 fs/proc/inode.c:343)
[    2.720726]  vfs_write (fs/read_write.c:684)
...
```

5.8 秒解完整段。`proc_reg_write` 那一行列出了**六層 inline 鏈**：從 `fs/proc/inode.c:343` 進入、經過 205 行，一路 inline 到 `atomic.h:85`——原始碼裡看起來是一個普通函式，實際上返回位址落在一段被 inline 的 atomic 操作裡。v6.18 的 `Documentation/admin-guide/bug-hunting.rst` 有介紹 `decode_stacktrace.sh` 與用 gdb 查位址的做法。

## 對比與取捨

| 工具 | 問題 | 需要什麼 | 成本 |
|---|---|---|---|
| `make X.i` | 巨集展開成什麼？`#ifdef` 留下哪些？ | 設定好的 build 目錄 | 數秒；輸出 8 萬行要會搜 |
| `make X.s` | 生成什麼指令？分支怎麼排？ | 同上 | 數秒 |
| `objdump -dS`/`-dr` | 呼叫了誰？inline 了什麼？間接呼叫在哪？ | `.o` 或 vmlinux（`-S` 要 debug info） | 即時 |
| `pahole -C` | struct 多大、offset、hole、cache line | 有 DWARF 或 BTF 的 vmlinux | 約 2 秒 |
| `nm`/`readelf -s` | 符號存不存在、static 或 global、多大 | vmlinux / `.o` | 即時 |
| AST / tree dump | 編譯器怎麼解析、最佳化一個運算式 | 抽成 user-space 小例子 | 即時；不適合整個 kernel TU |
| `faddr2line` / `decode_stacktrace.sh` | oops 的每一層在哪一行、經過哪些 inline | **和出事 kernel 完全相同**的 vmlinux（含 debug info） | 數秒到 20 秒 |

和 [Ch 49](./49-clangd-lsp.md) 的 clangd 比：clangd 適合**互動地**問單一位置；這一章的工具適合**整批地**看一個檔案、一個函式的完整產物，而且產物可以 `grep`、`diff`、存進筆記。兩者的答案應該一致——不一致時，相信編譯器產物（clangd 是 clang 前端，你的 kernel 是 gcc 編的）。

## 踩雷集錦

1. **在 source tree 裡跑 `make fs/pipe.i`**：沒有 `O=` 時 Kbuild 會把輸出寫進 source tree（還可能要求你先設定 in-tree `.config`）。一律 `make O=<build> fs/pipe.i`，輸出在 build 目錄。
2. **拿別的 config 的 `.i`/pahole 結果來推論**：`struct pipe_inode_info` 是 176 bytes 是**這個 config**的事實；開了 `CONFIG_WATCH_QUEUE` 或 lockdep 就不是。引用 layout 時要附上 config。
3. **faddr2line 的大小用 `.o` 的值**：`.o` 的 `st_size` 和 kallsyms 的大小可能差幾個 bytes（對齊填充），faddr2line 會以 size mismatch 拒絕。直接複製 oops 裡的 `func+off/size`。
4. **vmlinux 和出事的 kernel 不是同一次 build**：重編過（即使原始碼沒變，旗標或工具鏈版本不同）位址就可能偏移，faddr2line 會給你一個看似合理的錯誤行號。保留每次 build 的 vmlinux。
5. **以為原始碼裡的函式呼叫都存在於 binary**：`static inline` 幾乎都被 inline 掉，ftrace 追不到、`nm` 找不到、gdb 不能對它下一般斷點。用 objdump 的呼叫統計確認。
6. **把 `__x86_indirect_thunk_rax` 當成一個真的函式去讀**：它是 retpoline thunk，每個 `call` 到它的地方，原始碼裡都是一個函式指標呼叫。

## 進階：再往深一層

**`gcc -E -dD` 與 `-dM`。** 在 `.i` 的旗標上加 `-dD` 會保留 `#define` 本身，讓你在展開結果旁邊看到巨集定義；`-dM` 只輸出所有最終生效的巨集。想知道「`might_sleep` 在我的 config 下最後被定義成什麼」，`-dM | grep might_sleep` 最快。

**BTF 與 `bpftool btf dump`。** 這個 vmlinux 帶有 BTF（緊湊的型別資訊，給 BPF 用），`pahole` 也能讀 BTF。在沒有完整 DWARF 的發行版 kernel 上，`/sys/kernel/btf/vmlinux` 是查 struct layout 的唯一來源（本章用 DWARF，未實測 BTF 路徑）。

**diff 兩個 kernel 的 `.s`。** 想知道一個 patch 對生成程式碼的影響，對 patch 前後各 `make X.s`，然後 `diff`。kernel 社群在討論 micro-optimization 時常用 `scripts/bloat-o-meter` 比較兩個 vmlinux 的符號大小差異，是同一個思路的工具化版本。

**把 faddr2line 接到 gdb。** [Ch 52](./52-qemu-gdb-kernel.md) 會在 QEMU 裡掛 gdb；`list *(anon_pipe_read+0x45)` 和 faddr2line 給的是同一個答案，但 gdb 還能讓你在那裡下斷點、看當下的暫存器。

## 動手練習

1. 對 `fs/eventfd.c` 產生 `.i`，找出 `eventfd_read` 裡任何一個 `spin_lock_irq` 的展開結果，寫下它在你的 config 下最終呼叫了哪個函式。
2. 用 objdump 的呼叫統計，列出 `anon_pipe_write`（`fs/pipe.c`）裡被 inline 掉的函式，以及它有幾個間接呼叫。對照原始碼，指出每一個間接呼叫對應哪一行。
3. 用 pahole 看 `struct file`，找出 `f_op` 的 offset。然後在 `fs/read_write.c` 的 `.s` 裡找 `new_sync_read`，確認它讀 `filp->f_op` 時用的是同一個 offset，再找到緊接著的 `read_iter` 欄位讀取（offset 是多少？用 pahole 看 `struct file_operations` 驗證）。
4. 在 QEMU 裡觸發 `echo l > /proc/sysrq-trigger`（印出所有 CPU 的 backtrace，不會 panic），挑三行用 faddr2line 解碼，至少找到一個有 `(inlined by)` 的。
5. 把 [Ch 15](./15-kernel-c-gnu-extensions.md) 的某一個 statement expression 巨集抽成 user-space 小程式，用 `clang -Xclang -ast-dump` 看它的 AST，找出 `StmtExpr` 節點。

## 本章重點整理

- 原始碼是作者的說法，編譯器每一層的產物是機器的實際行為；讀不懂時往下翻一層。
- `.i` 給你巨集全展開與 `#ifdef` 篩選後的結果（`fs/pipe.c` 膨脹 52 倍）；`.s`/objdump 給你 inline、分支配置與間接呼叫的真相。
- `pahole` 給的是**這個 config** 的真實 layout；`nm`/`readelf` 是「有沒有編進去」的最終仲裁。
- AST / tree dump 適合把 kernel 慣用法抽成小例子讓編譯器解釋。
- `faddr2line` 用 `func+off/size` 對回原始碼行並展開 inline 鏈；大小要用 kallsyms（oops 裡）的值。

## 自我檢核

- [ ] 不看筆記，說出 `make O=../build fs/pipe.i` 和直接 `gcc -E fs/pipe.c` 的差別，以及為什麼後者的結果不能拿來讀 kernel。
- [ ] 給你一個函式的 objdump 輸出，你怎麼判斷原始碼裡的某個呼叫被 inline 了、某個呼叫是函式指標？
- [ ] 同事說「`struct pipe_inode_info` 有 20 個欄位、184 bytes」，你的 pahole 說 18 個欄位、176 bytes。你會問他什麼？
- [ ] faddr2line 回你 `size mismatch`，可能的原因有哪些？你下一步做什麼？
- [ ] 解釋為什麼 oops 的 call trace 能回答「這次函式指標指向誰」，而 clangd 不能。

## 延伸閱讀

- **《Code Reading: The Open Source Perspective》10.6 節 The Compiler as a Code-Reading Tool** — Diomidis Spinellis（Addison-Wesley, 2003）
  - **讀哪裡**：整節，以及 10.8（Runtime Tools）的開頭。
  - **學到什麼**：用編譯器警告、前置處理輸出、組語輸出幫助理解程式的原始想法；本章是它在 kernel 與現代工具鏈上的展開。
  - **前提**：無。
- **v6.18 `Documentation/admin-guide/bug-hunting.rst`**（docs.kernel.org 上的「Bug hunting」）
  - **讀哪裡**：「Finding the bug's location」一節，包括 `decode_stacktrace.sh` 與 gdb `list *address` 的做法。
  - **學到什麼**：kernel 官方建議的 oops 解碼流程，以及沒有 debug info 時怎麼退而求其次。
  - **前提**：看過一個 oops。
- **v6.18 `scripts/faddr2line` 原始碼開頭的註解**
  - **讀哪裡**：前 40 行左右的使用說明與範例。
  - **學到什麼**：為什麼用 `func+off/size` 而不是絕對位址（KASLR）、size 欄位怎麼消歧義重名符號、inline 鏈的輸出格式。
  - **前提**：無。
- **[Poke-a-hole and friends](https://lwn.net/Articles/335942/)** — Arnaldo Carvalho de Melo（LWN, 2009）
  - **讀哪裡**：整篇，特別是 hole 與 cache line 的範例輸出解說。
  - **學到什麼**：pahole 作者親自說明這個工具的設計目的——找 struct 裡的洞、重排欄位節省 cache line；本章的 `pipe_inode_info` 輸出就是它的典型用法。
  - **前提**：知道 cache line 與對齊是什麼。
- **[Clang 文件：Introduction to the Clang AST](https://clang.llvm.org/docs/IntroductionToTheClangAST.html)** — LLVM 專案
  - **讀哪裡**：「Examining the AST」一節。
  - **學到什麼**：`-ast-dump` 輸出裡每一欄的意義（節點類型、位址、source range、型別）；讀完就看得懂本章 `container_of` 那棵樹的每個欄位。
  - **前提**：會 C。
- **GCC 手冊「Developer Options」一節**（`gcc.gnu.org/onlinedocs/gcc/Developer-Options.html`）
  - **讀哪裡**：`-fdump-tree-*` 與 `-fdump-rtl-*` 的說明。
  - **學到什麼**：每個 dump 對應最佳化流程的哪一個 pass；想看「inline 決策在哪一步做的」時從這裡開始。
  - **前提**：大致知道編譯器有前端、中端、後端。

靜態的產物告訴你「程式碼是什麼」，但函式指標這次指向誰、哪條路徑真的被走過，最直接的答案在執行期。下一章讓 kernel 自己告訴你它跑了什麼。

→ [Ch 51 動態觀察：ftrace function_graph 與 bpftrace](./51-dynamic-tracing.md)
