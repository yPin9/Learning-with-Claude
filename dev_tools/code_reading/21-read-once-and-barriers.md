# Ch 21 — READ_ONCE 與 memory barrier：那些看起來多餘的巨集

> **目標**：讀到 `READ_ONCE(pipe->head_tail)`、`smp_wmb()`、`smp_load_acquire(&pipe->head)` 時，能說出它在防什麼（編譯器的哪一種轉換、CPU 的哪一種重排），並且**找到它的另一半**——每一個 barrier 都和另一段程式碼裡的另一個 barrier 成對。這是原理深挖章：我們會在 user-space 讓編譯器和 CPU 當場犯錯給你看。

> **鴻溝**：本章主要練**領域鴻溝**（memory model 是一整套背景知識）與**脈絡鴻溝**（「這行為什麼要 `smp_rmb()`」的答案在另一個檔案的註解或 commit 裡）。附帶處理一個常見的**慣用法鴻溝**：`READ_ONCE` 看起來和普通讀取一模一樣。

> **環境**：Linux v6.18 原始碼；user-space 實驗在 WSL Ubuntu 22.04、gcc 11.4、16 核 x86_64；跨架構組語用 clang 14 `--target=aarch64-linux-gnu` 產生（只產生組語、不執行）。

## 為什麼需要這個？

第一次讀 kernel 的人常有這個疑問：

```c
/* fs/pipe.c:229-236（v6.18） */
/* Done while waiting without holding the pipe lock - thus the READ_ONCE() */
static inline bool pipe_readable(const struct pipe_inode_info *pipe)
{
	union pipe_index idx = { .head_tail = READ_ONCE(pipe->head_tail) };
	unsigned int writers = READ_ONCE(pipe->writers);

	return !pipe_empty(idx.head, idx.tail) || !writers;
}
```

`READ_ONCE(pipe->writers)` 和 `pipe->writers` 有什麼不同？讀一次不就是讀一次嗎？

答案是：在你寫的 C 和 CPU 實際執行的東西之間，有兩個會**合法地**改寫你程式的傢伙：

1. **編譯器**。C 標準允許編譯器做任何「在單執行緒下看不出差別」的轉換：把讀取提出迴圈、合併兩次讀、刪掉它認為多餘的寫、把一次 64-bit 寫拆成兩次 32-bit 寫。
2. **CPU**。現代 CPU 有 store buffer、亂序執行、多層 cache。一個 CPU 依序做的兩次記憶體存取，另一個 CPU 看到的順序可能是反的。

單執行緒程式完全感覺不到這兩件事——那正是規則的設計：「只要單執行緒結果不變，怎麼改都行」。但 kernel 到處是**不拿鎖的共享存取**：等待者不拿鎖輪詢條件、RCU reader、lockless 的 ring buffer。對這些程式碼，「單執行緒看不出差別」不夠。

`READ_ONCE` / `WRITE_ONCE` 管第一個傢伙（編譯器），`smp_*` barrier 管第二個（CPU，也連帶管編譯器）。拿鎖的程式碼通常不需要它們，因為 lock / unlock 本身就隱含了完整的 barrier——這是為什麼你在 `mutex_lock` 保護的臨界區裡很少看到 `READ_ONCE`，而 `pipe_readable` 的註解特地寫「without holding the pipe lock - thus the READ_ONCE()」。

## 先建立直覺

```
你寫的 C 程式                  編譯器看到的世界               CPU 實際做的
─────────────                ─────────────────            ─────────────
while (!stop) ;              「stop 在迴圈裡沒被改，        （根本沒有讀取，
                               讀一次就夠了」                  只剩一個無限迴圈）
                               → if (!stop) for(;;);

x = 1;                       「順序無所謂」                 store x 進 store buffer
r1 = y;                        （x、y 是不同變數）            load y 從 cache 讀
                                                             → 另一個 CPU 可能還看不到 x=1
                                                               就已經讀到 y 的舊值
```

兩層各自的解法：

| 問題出在 | 現象 | 解法 | 成本 |
|---|---|---|---|
| 編譯器 | 合併、提出迴圈、重讀、撕裂、刪除、發明存取 | `READ_ONCE` / `WRITE_ONCE`（volatile 存取）；`barrier()` | 幾乎為零，只是禁止最佳化 |
| CPU | 不同 CPU 看到的存取順序不同 | `smp_mb` / `smp_rmb` / `smp_wmb`；`smp_load_acquire` / `smp_store_release` | 依架構不同，從零到一條 fence 指令 |

一個 barrier 單獨存在沒有意義。它限制的是「**我這邊的順序**」，而這個順序只有在**另一個 CPU 用對應的 barrier 讀**時才有用。這是本章最重要的讀碼規則，後面會一直回來。

## 第一層：編譯器做了什麼（實驗）

### 實驗一：讀取被提出迴圈

```c
/* hoist.c */
#include <pthread.h>
#include <stdio.h>
#include <unistd.h>

#define READ_ONCE(x)  (*(const volatile __typeof__(x) *)&(x))
#define WRITE_ONCE(x, v) (*(volatile __typeof__(x) *)&(x) = (v))

int stop;                 /* 由另一條 thread 設成 1 */

void wait_plain(void)
{
	while (!stop)
		;               /* 普通讀取 */
}

void wait_once(void)
{
	while (!READ_ONCE(stop))
		;               /* 每圈都真的去讀記憶體 */
}

static void *setter(void *arg)
{
	sleep(1);
	WRITE_ONCE(stop, 1);
	return NULL;
}

int main(int argc, char **argv)
{
	pthread_t t;
	pthread_create(&t, NULL, setter, NULL);
	if (argc > 1)
		wait_once();
	else
		wait_plain();
	puts("loop exited");
	pthread_join(t, NULL);
	return 0;
}
```

先看組語（`gcc -O2 -S -fno-asynchronous-unwind-tables`，gcc 11.4，刪掉對齊指示）：

```
wait_plain:
	endbr64
	movl	stop(%rip), %eax      ; 讀一次
	testl	%eax, %eax
	jne	.L8
.L7:
	jmp	.L7                   ; 無限迴圈，裡面沒有任何讀取
.L8:
	ret

wait_once:
	endbr64
	leaq	stop(%rip), %rdx
.L10:
	movl	(%rdx), %eax          ; 每一圈都讀
	testl	%eax, %eax
	je	.L10
	ret
```

`wait_plain` 被改寫成「讀一次 `stop`，如果是 0 就永遠空轉」——正是 `Documentation/memory-barriers.txt:1577` 描述的「merge successive loads」。在單執行緒的世界裡這完全正確：迴圈本體沒有改 `stop`，它不可能變。

跑跑看：

```
$ gcc -O2 -pthread -o hoist hoist.c
$ timeout 3 ./hoist; echo "exit=$?"
exit=124
$ timeout 3 ./hoist once; echo "exit=$?"
loop exited
exit=0
$ gcc -O0 -pthread -o hoist0 hoist.c
$ timeout 3 ./hoist0; echo "exit=$?"
loop exited
exit=0
```

`exit=124` 是 `timeout` 殺掉程式的回傳值：setter 一秒後就把 `stop` 設成 1 了，但主迴圈永遠看不到。`-O0` 沒有這個問題——這就是「debug build 好好的，release build 卡死」這類 bug 的來源，也是為什麼**不能用「我測過沒問題」來證明 lockless 程式碼正確**。

### 實驗二：合併寫入、合併讀取

```c
/* xform.c */
#define READ_ONCE(x)  (*(const volatile __typeof__(x) *)&(x))
#define WRITE_ONCE(x, v) (*(volatile __typeof__(x) *)&(x) = (v))

int flag;
unsigned long long big;

/* 1. 合併寫入：兩次寫入只剩最後一次 */
void store_twice_plain(void) { flag = 1; flag = 2; }
void store_twice_once(void)  { WRITE_ONCE(flag, 1); WRITE_ONCE(flag, 2); }

/* 2. 合併讀取：兩次讀取共用一次 load */
int load_twice_plain(void) { return flag + flag; }
int load_twice_once(void)  { return READ_ONCE(flag) + READ_ONCE(flag); }

/* 3. 撕裂：64-bit 常數寫入 */
void store_big_plain(void) { big = 0x0000000100000002ULL; }
void store_big_once(void)  { WRITE_ONCE(big, 0x0000000100000002ULL); }
```

x86_64、`gcc -O2` 的真實輸出：

```
store_twice_plain:
	movl	$2, flag(%rip)        ; flag = 1 消失了
	ret
store_twice_once:
	movl	$1, flag(%rip)
	movl	$2, flag(%rip)
	ret
load_twice_plain:
	movl	flag(%rip), %eax      ; 只讀一次
	addl	%eax, %eax
	ret
load_twice_once:
	movl	flag(%rip), %eax
	movl	flag(%rip), %edx      ; 讀兩次，兩次可能不同
	addl	%edx, %eax
	ret
store_big_plain:
	movabsq	$4294967298, %rax
	movq	%rax, big(%rip)
	ret
store_big_once:
	movabsq	$4294967298, %rax
	movq	%rax, big(%rip)
	ret
```

前兩組符合預期。`flag = 1` 被刪掉，如果另一個 CPU 在等「flag 變成 1」這個中間狀態，它永遠等不到。

第三組在 x86_64 上兩個版本一樣——64-bit 寫入本來就是一條 `movq`。換成 `-m32` 再編一次：

```
$ gcc -m32 -O2 -S -fno-asynchronous-unwind-tables -o - tear.c
store_big_plain:
	...
	movl	$2, big@GOTOFF(%eax)
	movl	$1, 4+big@GOTOFF(%eax)
store_big_once:
	...
	movl	$2, big@GOTOFF(%eax)
	movl	$1, 4+big@GOTOFF(%eax)
```

**連 `WRITE_ONCE` 版本也被拆成兩次 32-bit 寫入**。另一個 CPU 可能讀到「低半已更新、高半還沒」的值——這叫撕裂（tearing）。這不是 kernel 的 bug，`rwonce.h` 的註解老實承認了：

```c
/* include/asm-generic/rwonce.h:29-37（v6.18） */
/*
 * Yes, this permits 64-bit accesses on 32-bit architectures. These will
 * actually be atomic in some cases (namely Armv7 + LPAE), but for others we
 * rely on the access being split into 2x32-bit accesses for a 32-bit quantity
 * (e.g. a virtual address) and a strong prevailing wind.
 */
#define compiletime_assert_rwonce_type(t)					\
	compiletime_assert(__native_word(t) || sizeof(t) == sizeof(long long),	\
		"Unsupported access size for {READ,WRITE}_ONCE().")
```

**`READ_ONCE` 保證不撕裂的範圍是機器字長**（`__native_word`：char、short、int、long）。在 32-bit 架構上對 `u64` 做 `READ_ONCE` 能編過，但不保證原子；需要原子的 64-bit 值要用 `atomic64_t` 或 `u64_stats_sync` 一類的機制。讀碼時看到 32-bit 也要跑的程式碼對 64-bit 欄位 `READ_ONCE`，記得這個邊界。

### memory-barriers.txt 列出的完整清單

`Documentation/memory-barriers.txt` 的 COMPILER BARRIER 一節（第 1532 行起）列了編譯器「within its rights」可以做的轉換，我們實驗了其中三種。完整清單，讀碼時記得它們都存在：

| 轉換 | 原文行號（v6.18） | 會壞掉的 lockless 情境 |
|---|---|---|
| 重排不同變數的存取 | 1560 | 先寫資料再寫旗標，被調換 |
| 合併對同一變數的多次讀取 | 1577 | 輪詢迴圈變無限迴圈（實驗一） |
| 重讀變數（register 不夠時） | 1597 | `tmp = a; if (tmp) use(tmp);` 變成兩次讀 `a`，第二次可能是 0 |
| 省略讀取（它「知道」值） | 1627 | 另一個 CPU 改了值卻沒被讀到 |
| 省略寫入 | 1660 | 合併寫入（實驗二） |
| 發明寫入 | 1748 | 本來不寫的分支被插入寫入，覆蓋別人的值 |
| 撕裂 | 1800 附近 | 讀到半新半舊的值（實驗二 `-m32`） |

「重讀」最陰險，因為它和直覺相反：你明明只讀一次，編譯器卻讀兩次。`READ_ONCE` 把它釘死成「恰好一次」——這就是名字裡 ONCE 的意思。

## READ_ONCE 的實作與歷史

```c
/* include/asm-generic/rwonce.h:39-62（v6.18） */
/*
 * Use __READ_ONCE() instead of READ_ONCE() if you do not require any
 * atomicity. Note that this may result in tears!
 */
#ifndef __READ_ONCE
#define __READ_ONCE(x)	(*(const volatile __unqual_scalar_typeof(x) *)&(x))
#endif

#define READ_ONCE(x)							\
({									\
	compiletime_assert_rwonce_type(x);				\
	__READ_ONCE(x);							\
})

#define __WRITE_ONCE(x, val)						\
do {									\
	*(volatile typeof(x) *)&(x) = (val);				\
} while (0)

#define WRITE_ONCE(x, val)						\
do {									\
	compiletime_assert_rwonce_type(x);				\
	__WRITE_ONCE(x, val);						\
} while (0)
```

用 [Ch 15](./15-kernel-c-gnu-extensions.md) 的工具逐片拆：

- `&(x)` 取位址，強制 `x` 是 lvalue（`READ_ONCE(a + b)` 編不過）。
- 轉成 `volatile T *` 再解參考：一次 **volatile 存取**。C 標準要求 volatile 存取不能被合併、刪除、發明，次數和順序（相對其他 volatile 存取）要照抽象機器執行——這正好封掉上表大部分的轉換。
- `__unqual_scalar_typeof(x)`（`include/linux/compiler_types.h:551`）：用 `_Generic` 把 `const int` 之類的限定詞拿掉，讓 `READ_ONCE` 的結果是一個普通的 `int` 右值，而不是 `const volatile int`。否則 `typeof(READ_ONCE(x)) tmp` 會宣告出一個 volatile 變數，之後對 `tmp` 的每次使用都變成記憶體存取。
- `({ ... })` statement expression 讓它能當運算式用；`WRITE_ONCE` 用 `do { } while (0)`（[Ch 13](./13-preprocessor-basics.md)），因為它不需要值。
- `compiletime_assert_rwonce_type`：大小不對直接編譯失敗。

歷史上它叫 `ACCESS_ONCE`：

```
$ git log --format='%h %ad %an %s' --date=short -i --grep='READ_ONCE\|ACCESS_ONCE' -- include/linux/compiler.h | tail -4
c5b19946eb76 2015-01-12 Christian Borntraeger kernel: Fix sparse warning for ACCESS_ONCE
927609d622a3 2014-11-25 Christian Borntraeger kernel: tighten rules for ACCESS ONCE
230fa253df63 2014-11-25 Christian Borntraeger kernel: Provide READ_ONCE and ASSIGN_ONCE
...
9c3cdc1f83a6 2008-05-10 Linus Torvalds Move ACCESS_ONCE() to <linux/compiler.h>
```

`ACCESS_ONCE(x)` 是 `(*(volatile typeof(x) *)&(x))`，讀寫共用一個巨集。`230fa253df63`（進入 v3.19）的 commit message 說明了為什麼要換：「ACCESS_ONCE does not work reliably on non-scalar types. For example gcc 4.6 and 4.7 might remove the volatile tag for such accesses during the SRA (scalar replacement of aggregates) step」——**編譯器 bug 讓 volatile 對 struct 失效**。於是拆成讀寫兩個巨集、改走 scalar 型別。`ACCESS_ONCE` 最後在 `b899a850431e`（Mark Rutland，進入 v4.15）被移除。讀 v4.15 以前的 kernel 或舊書時看到 `ACCESS_ONCE`，把它讀成 `READ_ONCE` / `WRITE_ONCE` 即可。

### 那為什麼不直接把變數宣告成 volatile？

`Documentation/process/volatile-considered-harmful.rst` 整篇在回答這個問題。精簡版：volatile 是**變數**的屬性，但「需不需要防最佳化」是**存取**的屬性。同一個欄位，拿鎖時存取不需要防（鎖已經是 barrier），不拿鎖時才需要。把整個變數宣告成 volatile 會讓拿鎖的存取也變慢，而且掩蓋了「哪些存取是 lockless 的」這個資訊。

對讀碼的人來說，這個選擇帶來一個好處：**`READ_ONCE`/`WRITE_ONCE` 是一個標記**，標出「這裡有一個不受鎖保護的並行存取」。LKMM 的文件把它叫做 marked access（`tools/memory-model/Documentation/access-marking.txt`）。看到它，你就知道作者在這裡考慮過並行，可以去找「和誰並行」。KCSAN（Kernel Concurrency Sanitizer）也靠這個標記區分「有意的 data race」和「bug」。

## 第二層：CPU 做了什麼（實驗）

編譯器乖了，CPU 呢？經典的 Store Buffering（SB）測試：

```
初始 x = y = 0

CPU 0              CPU 1
x = 1;             y = 1;
r1 = y;            r2 = x;

有沒有可能 r1 == 0 && r2 == 0？
```

直覺說不可能：總有一個 CPU 先寫，另一個應該讀得到。實測：

```c
/* sb.c — Store Buffering litmus：x86 唯一允許的重排（store→load） */
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>

#define READ_ONCE(x)  (*(const volatile __typeof__(x) *)&(x))
#define WRITE_ONCE(x, v) (*(volatile __typeof__(x) *)&(x) = (v))
#define barrier()  __asm__ __volatile__("" ::: "memory")
#define smp_mb()   __asm__ __volatile__("lock addl $0,-4(%%rsp)" ::: "memory", "cc")

#define ITERS 1000000
static int x, y;
static volatile int r1, r2;   /* 結果也要 volatile，避免量測程式本身被編譯器重排 */
static volatile int go0, go1, done0, done1;
static int use_mb;

static void *t0(void *a)
{
	for (int i = 0; i < ITERS; i++) {
		while (go0 != i + 1) ;          /* 等主執行緒放行 */
		WRITE_ONCE(x, 1);
		if (use_mb) smp_mb(); else barrier();
		r1 = READ_ONCE(y);
		done0 = i + 1;
	}
	return NULL;
}

static void *t1(void *a)
{
	for (int i = 0; i < ITERS; i++) {
		while (go1 != i + 1) ;
		WRITE_ONCE(y, 1);
		if (use_mb) smp_mb(); else barrier();
		r2 = READ_ONCE(x);
		done1 = i + 1;
	}
	return NULL;
}

int main(int argc, char **argv)
{
	pthread_t a, b;
	long both_zero = 0;
	use_mb = argc > 1;
	pthread_create(&a, NULL, t0, NULL);
	pthread_create(&b, NULL, t1, NULL);
	for (int i = 0; i < ITERS; i++) {
		x = y = 0;
		__sync_synchronize();
		go0 = i + 1; go1 = i + 1;
		while (done0 != i + 1 || done1 != i + 1) ;
		if (r1 == 0 && r2 == 0)
			both_zero++;
	}
	pthread_join(a, NULL); pthread_join(b, NULL);
	printf("%s: r1==0 && r2==0 出現 %ld / %d 次\n",
	       use_mb ? "smp_mb()" : "barrier()", both_zero, ITERS);
	return 0;
}
```

```
$ gcc -O2 -pthread -o sb sb.c
$ for i in 1 2 3; do ./sb; ./sb mb; done
barrier(): r1==0 && r2==0 出現 547991 / 1000000 次
smp_mb(): r1==0 && r2==0 出現 0 / 1000000 次
barrier(): r1==0 && r2==0 出現 605094 / 1000000 次
smp_mb(): r1==0 && r2==0 出現 0 / 1000000 次
barrier(): r1==0 && r2==0 出現 525602 / 1000000 次
smp_mb(): r1==0 && r2==0 出現 0 / 1000000 次
```

「不可能」的結果出現了一半以上的次數。`barrier()` 已經保證編譯器沒有調換（它是 compiler barrier），所以這是 **CPU** 做的：每個 CPU 把 `x = 1` 放進自己的 store buffer，還沒刷到 cache，就從 cache 讀了 `y` 的舊值。換成 `smp_mb()`（kernel x86 的實作 `lock addl $0,-4(%rsp)`，會清空 store buffer）之後一次都沒出現。次數多寡和機器、負載有關，這裡的比例**只說明「會發生」，不代表任何機器上的固定機率**。

順帶一提，這個測試程式本身第一版就有並行 bug：我一開始把 `r1`、`r2` 宣告成普通 `int`，結果 `smp_mb()` 版本也出現了 11、12 次「不可能」的結果——不是 CPU 的錯，是編譯器把 `r1 = ...` 的寫入和 `done0 = ...` 的 volatile 寫入調換了，主執行緒讀到上一輪的 `r1`。加上 volatile 後歸零。**寫並行測試的程式碼本身也需要 marked access**，這種錯誤非常容易犯。

### x86 和 ARM 的差別

x86 的記憶體模型叫 TSO（Total Store Order），只允許「store 之後的 load 被提前」這一種重排——就是 SB 測到的那種。所以在 x86 上：

```c
/* arch/x86/include/asm/barrier.h:50-73（v6.18，節錄） */
#define __dma_rmb()	barrier()
#define __dma_wmb()	barrier()

#define __smp_mb()	asm volatile("lock addl $0,-4(%%" _ASM_SP ")" ::: "memory", "cc")

#define __smp_rmb()	dma_rmb()
#define __smp_wmb()	barrier()
...
#define __smp_store_release(p, v)					\
do {									\
	compiletime_assert_atomic_type(*p);				\
	barrier();							\
	WRITE_ONCE(*p, v);						\
} while (0)

#define __smp_load_acquire(p)						\
({									\
	typeof(*p) ___p1 = READ_ONCE(*p);				\
	compiletime_assert_atomic_type(*p);				\
	barrier();							\
	___p1;								\
})
```

`smp_rmb`、`smp_wmb`、`smp_load_acquire`、`smp_store_release` 在 x86 上**全都只是 compiler barrier**，不產生任何指令。只有 `smp_mb` 是真的 fence。

ARM64 弱得多，四種重排都允許：

```c
/* arch/arm64/include/asm/barrier.h:126-128（v6.18） */
#define __smp_mb()	dmb(ish)
#define __smp_rmb()	dmb(ishld)
#define __smp_wmb()	dmb(ishst)
```

而 acquire/release 對應專用的 `ldar` / `stlr` 指令（同一個檔案第 139–188 行）。用 C11 atomics 寫一個等價的生產者／消費者，看兩個架構產生的組語：

```c
/* acqrel.c — 用 C11 atomics 模擬 smp_store_release / smp_load_acquire */
int data, flag;
void producer(void)
{
	data = 42;
	__atomic_store_n(&flag, 1, __ATOMIC_RELEASE);
}
int consumer(void)
{
	if (__atomic_load_n(&flag, __ATOMIC_ACQUIRE))
		return data;
	return -1;
}
void full_fence(void) { __atomic_thread_fence(__ATOMIC_SEQ_CST); }
```

```
== x86_64 (gcc -O2)                    == aarch64 (clang -O2，節錄)
producer:                              producer:
	movl	$42, data(%rip)               str	w9, [x8, :lo12:data]
	movl	$1, flag(%rip)                stlr	w10, [x11]        ; store-release
	ret                                   ret
consumer:                              consumer:
	movl	flag(%rip), %eax              ldar	w9, [x9]          ; load-acquire
	testl	%eax, %eax                    ldr	w8, [x8, :lo12:data]
	movl	$-1, %eax                     ...
	cmovne	data(%rip), %eax
	ret
full_fence:                            full_fence:
	lock orq	$0, (%rsp)                 dmb	ish
```

x86 上 release/acquire 就是普通的 `mov`；ARM64 上是不同的指令。這是兩個重要的讀碼結論：

1. **在 x86 上測不出來的 barrier bug，在 ARM 上會出事**。漏掉 `smp_wmb` 的程式碼在 x86 筆電上跑一年都沒問題。
2. **barrier 的語意要用「最弱的架構」來想**。kernel 的 barrier API 是可攜的抽象，它的語意由 LKMM 定義，不是由 x86 定義。

（`acqrel.c` 用的是 C11 atomics 而不是 kernel 巨集，因為 kernel 巨集離不開 kernel header。kernel 的 ARM64 實作確實用 `stlr`/`ldar`，上面引用的 barrier.h 行號可以對照。）

## barrier 家族速查

| API | 保證 | 典型配對 |
|---|---|---|
| `barrier()` | 只擋編譯器，不擋 CPU | 同一 CPU 上 process 與 interrupt 之間 |
| `smp_mb()` | 前面所有存取 先於 後面所有存取 | 另一個 `smp_mb()`（SB 型、等待／喚醒） |
| `smp_wmb()` | 前面的 store 先於後面的 store | 讀端的 `smp_rmb()` / acquire / 位址相依 |
| `smp_rmb()` | 前面的 load 先於後面的 load | 寫端的 `smp_wmb()` / release |
| `smp_store_release(p, v)` | 前面所有存取 先於 這次 store | `smp_load_acquire(p)` 讀同一個變數 |
| `smp_load_acquire(p)` | 這次 load 先於 後面所有存取 | `smp_store_release(p, v)` |
| `smp_store_mb(var, v)` | store 後接 `smp_mb()` | 另一個 `smp_mb()` |
| `rcu_assign_pointer` / `rcu_dereference` | release / 位址相依 | 彼此（[Ch 31](./31-reading-rcu-code.md)） |
| lock / unlock | acquire / release | 同一把鎖的 unlock / lock |

`smp_` 前綴的意思是「只在 SMP 有作用」：`CONFIG_SMP=n` 時它們退化成 `barrier()`。沒有 `smp_` 前綴的 `mb()`/`rmb()`/`wmb()` 是給裝置 I/O 用的（CPU 和 DMA 裝置之間），在單核上也要生效，讀 driver 時會看到。

## 成對：讀碼時看到 barrier，去找它的另一半

`Documentation/memory-barriers.txt` 的「SMP BARRIER PAIRING」一節（第 939 行）講得很重：

> When dealing with CPU-CPU interactions, certain types of memory barrier should always be paired.  A lack of appropriate pairing is almost certainly an error.

kernel 用兩種方式幫你找另一半：

**一、註解慣例**。`scripts/checkpatch.pl` 會檢查：

```perl
# scripts/checkpatch.pl:6671-6697（v6.18，節錄）
# check for memory barriers without a comment.
		...
		if ($line =~ /\b(?:$all_barriers)\s*\(/) {
			if (!ctx_has_comment($first_line, $linenr)) {
				WARN("MEMORY_BARRIER",
				     "memory barrier without comment\n" . $herecurr);
			}
		}
```

`$all_barriers` 涵蓋 `mb`、`rmb`、`wmb`、`smp_*` 與 `virt_*` 版本、`smp_store_release`、`smp_load_acquire`、`smp_mb__before_atomic` 等。所以新程式碼裡的 barrier 幾乎都附了一行註解，而好的註解會**直接寫出配對在哪裡**。

**二、同一個變數**。barrier 保護的是一組變數的順序。用 `rg` 找同一個欄位的另一端存取，通常就找到另一半。

### 例一：release/acquire，跨兩個檔案

`anon_pipe_read` 讀 `pipe->head`（這個函式在 v6.15 以前叫 `pipe_read`，下面 `kernel/watch_queue.c` 的註解還留著舊名）：

```c
/* fs/pipe.c:292-295（v6.18） */
	for (;;) {
		/* Read ->head with a barrier vs post_one_notification() */
		unsigned int head = smp_load_acquire(&pipe->head);
		unsigned int tail = pipe->tail;
```

註解指名了另一半：`post_one_notification()`。它不在 `fs/pipe.c`，在 `kernel/watch_queue.c`（`rg -n 'post_one_notification' kernel/` 找到）：

```c
/* kernel/watch_queue.c:126-133（v6.18） */
	buf = pipe_buf(pipe, head);
	buf->page = page;
	buf->private = (unsigned long)wqueue;
	buf->ops = &watch_queue_pipe_buf_ops;
	buf->offset = offset;
	buf->len = len;
	buf->flags = PIPE_BUF_FLAG_WHOLE;
	smp_store_release(&pipe->head, head + 1); /* vs pipe_read() */
```

兩邊註解互相指向對方。現在可以讀出完整的協定：

```
post_one_notification (寫端)               anon_pipe_read (讀端)
───────────────────────────               ────────────────
填好 buf->page / ops / offset / len ...
smp_store_release(&pipe->head, head+1) ─┐
                                        └─► head = smp_load_acquire(&pipe->head)
                                            讀 pipe_buf(pipe, tail) 的內容
```

保證：如果讀端看到了新的 `head`，它**一定也看到**寫端在 release 之前填進 `buf` 的所有欄位。少了任一邊，讀端可能看到 `head` 前進了、`buf->ops` 卻還是舊的——呼叫一個垃圾函式指標。為什麼這裡需要 barrier 而 `pipe_write` 不需要？因為 `pipe_write` 和 `pipe_read` 都拿 `pipe->mutex`，而 `post_one_notification` 只拿 `pipe->rd_wait.lock`（spinlock）——它和 `pipe_read` **沒有共用的鎖**。這個「為什麼」要靠讀兩邊各自拿了什麼鎖才推得出來，是 [Ch 30](./30-reading-locking-code.md) 的技巧。

這正好是 LKMM 裡的 message passing（MP）模式，tree 裡有對應的 litmus test：

```
// tools/memory-model/litmus-tests/MP+pooncerelease+poacquireonce.litmus（v6.18）
C MP+pooncerelease+poacquireonce

(*
 * Result: Never
 *
 * This litmus test demonstrates that smp_store_release() and
 * smp_load_acquire() provide sufficient ordering for the message-passing
 * pattern.
 *)

{}

P0(int *buf, int *flag) // Producer
{
	WRITE_ONCE(*buf, 1);
	smp_store_release(flag, 1);
}

P1(int *buf, int *flag) // Consumer
{
	int r0;
	int r1;

	r0 = smp_load_acquire(flag);
	r1 = READ_ONCE(*buf);
}

exists (1:r0=1 /\ 1:r1=0) (* Bad outcome. *)
```

「Result: Never」表示 LKMM 證明壞結果不可能發生。用 `herd7` 工具可以機械地驗證這類 litmus test；**本課環境沒有安裝 herd7，這個驗證未實測**。要跑的話，照 `tools/memory-model/README` 的說明安裝 herdtools7（OCaml 套件），然後在 `tools/memory-model/` 下執行 `herd7 -conf linux-kernel.cfg litmus-tests/MP+pooncerelease+poacquireonce.litmus`。

### 例二：wmb/rmb，旗標與指標

```c
/* fs/file.c:660-676（v6.18，fd_install 節錄） */
	rcu_read_lock_sched();

	if (unlikely(files->resize_in_progress)) {
		rcu_read_unlock_sched();
		spin_lock(&files->file_lock);
		fdt = files_fdtable(files);
		...
		spin_unlock(&files->file_lock);
		return;
	}
	/* coupled with smp_wmb() in expand_fdtable() */
	smp_rmb();
	fdt = rcu_dereference_sched(files->fdt);
	...
	rcu_assign_pointer(fdt->fd[fd], file);
	rcu_read_unlock_sched();
```

```c
/* fs/file.c:270-278（v6.18，expand_fdtable 節錄） */
	cur_fdt = files_fdtable(files);
	BUG_ON(nr < cur_fdt->max_fds);
	copy_fdtable(new_fdt, cur_fdt);
	rcu_assign_pointer(files->fdt, new_fdt);
	if (cur_fdt != &files->fdtab)
		call_rcu(&cur_fdt->rcu, free_fdtable_rcu);
	/* coupled with smp_rmb() in fd_install() */
	smp_wmb();
	return 0;
```

這一對比較難讀，因為 `smp_wmb()` 後面那個 store 不在這個函式裡。往上找呼叫者 `expand_files`：

```c
/* fs/file.c:314-316（v6.18） */
	files->resize_in_progress = true;
	error = expand_fdtable(files, nr);
	files->resize_in_progress = false;
```

把兩邊攤平：

```
expand_files / expand_fdtable（寫端）       fd_install（讀端，不拿 file_lock）
─────────────────────────────────        ──────────────────────────────
resize_in_progress = true
...（synchronize_rcu 等舊 reader 走光）
files->fdt = new_fdt
smp_wmb()
resize_in_progress = false   ───────►    讀 resize_in_progress，看到 false
                                          smp_rmb()
                                          fdt = files->fdt  → 保證是 new_fdt
```

保證：讀端如果看到 `resize_in_progress == false` 是**這次擴張結束後**寫的那個 false，它接下來讀 `files->fdt` 一定是新表，不會把 file 裝進即將被丟掉的舊表。至於「讀端看到的 false 是擴張開始**之前**的那個」的情況，由寫端的 `synchronize_rcu()` 處理：它等所有 `rcu_read_lock_sched()` 區段結束才開始複製表格（`fs/file.c:261-263` 那段註解：「make sure all fd_install() have seen resize_in_progress or have finished their rcu_read_lock_sched() section」）。

這個例子示範了一個實際的讀碼困難：**barrier 保護的那個 store 可能在呼叫者裡**。註解「coupled with ...」幫你找到另一個函式，但兩邊各自保護哪些存取，要你自己把呼叫鏈攤平。

### 例三：smp_mb 對 smp_mb，等待與喚醒

```c
/* include/linux/wait.h:95-128（v6.18，節錄） */
/**
 * waitqueue_active -- locklessly test for waiters on the queue
 ...
 * Use either while holding wait_queue_head::lock or when used for wakeups
 * with an extra smp_mb() like::
 *
 *      CPU0 - waker                    CPU1 - waiter
 *
 *                                      for (;;) {
 *      @cond = true;                     prepare_to_wait(&wq_head, &wait, state);
 *      smp_mb();                         // smp_mb() from set_current_state()
 *      if (waitqueue_active(wq_head))         if (@cond)
 *        wake_up(wq_head);                      break;
 *                                        schedule();
 *                                      }
 *                                      finish_wait(&wq_head, &wait);
 *
 * Because without the explicit smp_mb() it's possible for the
 * waitqueue_active() load to get hoisted over the @cond store such that we'll
 * observe an empty wait list while the waiter might not observe @cond.
 */
```

這就是我們剛才實測的 SB 模式：waker 「寫 cond、讀 wait list」，waiter 「寫 wait list、讀 cond」。沒有兩個 `smp_mb()`，兩邊可能都讀到舊值——waker 以為沒人在等所以不喚醒，waiter 以為條件沒成立所以去睡：**lost wakeup**，程式永遠卡住。release/acquire 擋不住 store→load 的重排，這種模式一定要用 full barrier。waiter 那邊的 `smp_mb()` 藏在 `set_current_state()` 裡面，所以讀 waiter 程式碼時你**看不到**它——這是另一個讀碼陷阱：有些 barrier 是隱含在 API 裡的。[Ch 33](./33-wait-queues-completion.md) 會完整讀 wait queue。

`kernel/sched/wait.c:415-462` 的 `wait_woken` / `woken_wake_function` 把這件事做到極致：註解裡畫了兩欄、標了 A、B、C 三個 barrier，並逐一說明誰配誰（「The below executes an smp_mb(), which matches with the smp_mb() (C) in woken_wake_function()」）。這是 kernel 裡 barrier 註解的範本，讀一次就知道好的配對註解長什麼樣。

## 讀碼流程：遇到 barrier 怎麼辦

```
看到 READ_ONCE / WRITE_ONCE
   └─► 這是一個 lockless 的並行存取。問：和誰並行？
        用 rg 找同一欄位的其他存取，看哪些拿鎖、哪些沒拿

看到 smp_* barrier
   1. 讀它的註解，找「pairs with / coupled with / vs」
   2. 沒註解 → rg 同一組變數在其他地方的存取，找另一端的 barrier
   3. 把兩邊攤平成兩欄（像上面的圖），標出：
        寫端：barrier 前寫了什麼、barrier 後寫了什麼
        讀端：barrier 前讀了什麼、barrier 後讀了什麼
   4. 寫下保證：「如果讀端看到 X 的新值，它一定看到 Y 的新值」
   5. 判斷模式：MP（release/acquire 或 wmb/rmb 足夠）
                SB（需要兩邊 smp_mb）
   6. 找不到另一半 → 可能是 bug，也可能另一半是隱含的
      （lock/unlock、set_current_state、atomic 的 _return 版本、rcu_*）
```

最後一步常常讓人卡住。隱含 barrier 的常見來源：

- **lock / unlock**：acquire / release 語意。
- **回傳值的 atomic 操作**（`atomic_inc_return`、`atomic_dec_and_test`、`xchg`、`cmpxchg` 成功時）：完整 barrier；不回傳值的（`atomic_inc`）沒有任何順序保證，要靠 `smp_mb__before_atomic()` / `smp_mb__after_atomic()`。`Documentation/atomic_t.txt` 有完整規則。
- **`refcount_dec_and_test`**：release，成功時 acquire（[Ch 20](./20-refcount-and-lifetime.md) 引用過它的 kernel-doc）。
- **`set_current_state()`**：內含 `smp_mb()`。
- **`wake_up_*()` 成功喚醒時**：完整 barrier。

## 對比與取捨

| 方案 | 適用情境 | 優點 | 缺點 |
|---|---|---|---|
| 拿鎖 | 預設選擇 | 不用想 memory model | 有爭用時慢，不能在某些 context 用 |
| `READ_ONCE`/`WRITE_ONCE` | 單一變數的 lockless 存取，不需要和其他變數排序 | 幾乎零成本 | 不提供跨變數順序 |
| release / acquire | MP 模式：發佈一組資料再發旗標 | 在弱架構上比 full barrier 便宜 | 擋不住 store→load（SB） |
| `smp_wmb` / `smp_rmb` | 和 release/acquire 類似，但順序作用在 barrier 位置而不是特定變數 | 可以一次排序多個 store / load | 寫端讀端都要記得放 |
| `smp_mb` | SB 模式、等待／喚醒 | 最強 | 最貴（x86 上也是真的指令） |
| atomic 有回傳值的操作 | 計數 + 順序一起要 | 一個操作兩個用途 | 語意細節多 |
| RCU | 讀多寫少的指標發佈 | reader 幾乎零成本 | 只適合特定資料形狀 |

## 踩雷集錦

1. **「READ_ONCE 是 memory barrier」**：不是。它只擋編譯器對**這一次存取**的變形，不保證和其他變數的順序，也不擋 CPU 重排（rwonce.h 開頭的註解：「The compiler is also forbidden from reordering successive instances of READ_ONCE and WRITE_ONCE, but only when the compiler is aware of some particular ordering」——連編譯器的重排都只是有限度地禁止）。需要順序就要 barrier 或 acquire/release。
2. **「x86 上不用 barrier」**：在 x86 上 `smp_rmb`/`smp_wmb` 確實不產生指令，但**它們仍是 compiler barrier**，拿掉之後編譯器可以調換存取；而且同一份程式碼要在 ARM、RISC-V、POWER 上跑。「我在 x86 上測過」對 barrier 程式碼不構成證據。
3. **「多放幾個 barrier 比較安全」**：沒有配對的 barrier 不提供任何保證，只增加成本和讀者的困惑。review 時多出來的 barrier 會被要求說明配對或刪除。
4. **「release/acquire 可以取代所有 smp_mb」**：SB 模式不行。我們的實測顯示 store→load 重排在 x86 上就會發生，而 release/acquire 在 x86 上是普通 `mov`，擋不住。
5. **「volatile 讓程式碼 thread-safe」**：volatile 只約束編譯器，對 CPU 重排沒有作用，也不提供原子的 read-modify-write（`v++` 仍然是讀、加、寫三步）。這是 user-space 程式設計師最常帶進 kernel 的誤解。
6. **「32-bit 上 READ_ONCE(u64) 是原子的」**：不是，見實驗二的 `-m32`。

## 進階：再往深一層

- **LKMM（Linux Kernel Memory Model）**：`tools/memory-model/` 是 kernel 記憶體模型的形式化定義，用 cat 語言寫成（`linux-kernel.cat`、`linux-kernel.bell`），可以用 herd7 對 litmus test 求所有可能結果。入門順序建議：`Documentation/README` → `simple.txt` → `ordering.txt` → `recipes.txt`；`explanation.txt` 是完整的理論說明，很長，留到最後。
- **control dependency 與 address dependency**：讀到 `if (READ_ONCE(x)) WRITE_ONCE(y, 1);` 這種「靠 if 排序」的程式碼，看 `tools/memory-model/Documentation/control-dependencies.txt`。這類程式碼極易被編譯器最佳化破壞，kernel 社群對它的態度是「能不用就不用」。
- **KCSAN**：`CONFIG_KCSAN` 打開後，kernel 在執行期偵測沒有標記的並行存取。它的報告是找「該加 READ_ONCE 卻沒加」的主要來源，近幾年大量 `READ_ONCE`/`WRITE_ONCE` 的 patch 都標著 `Reported-by: syzbot` + KCSAN。讀到一個看起來多餘的 `READ_ONCE`，用 `git log -L` 追它的引入 commit，很可能就是一份 KCSAN 報告（[Ch 37](./37-git-archaeology.md)）。
- **`smp_mb__before_atomic()` / `smp_mb__after_atomic()`**：在 x86 上是空的（`arch/x86/include/asm/barrier.h:75-76`，因為 x86 的 atomic 指令本身就序列化），在其他架構上是 fence。

## 動手練習

1. 用 `rg -n 'smp_wmb\(\)' fs/ | head -20`，挑三個，照「讀碼流程」找出它們的配對。有沒有找不到另一半的？是隱含的嗎？
2. 把 `hoist.c` 的 `wait_plain` 改成迴圈裡呼叫一個外部函式（例如 `sched_yield()`），重新看 `-O2` 組語：讀取還會被提出迴圈嗎？為什麼？（提示：編譯器不知道外部函式會不會改 `stop`。這也是為什麼「迴圈裡有函式呼叫」的 bug 比較少見，而不是比較不會發生。）
3. 把 `sb.c` 改成 MP 模式（P0 寫 data 再寫 flag，P1 讀 flag 再讀 data），在 x86 上只用 `barrier()` 跑一百萬次，記錄壞結果的次數。和 SB 的結果比較，對照 TSO 的規則解釋。
4. 讀 `kernel/sched/wait.c:415-462`，把 A、B、C 三個 barrier 各自配對的對象和保證寫成兩欄表。

## 本章重點整理

- 編譯器可以合法地合併、提出、重讀、撕裂、刪除、發明記憶體存取；`READ_ONCE`/`WRITE_ONCE` 用 volatile 存取把它們封住，範圍限於機器字長。
- CPU 也會重排：x86 只允許 store→load，ARM 幾乎都允許；`smp_*` barrier 是可攜的抽象，語意由 LKMM 定義。
- barrier 必須成對；kernel 要求 barrier 附註解，好的註解直接寫出另一半在哪。
- 讀 barrier 的方法：找另一半、攤平成兩欄、寫出「看到 X 就一定看到 Y」的保證、判斷 MP 或 SB 模式。
- 找不到另一半時，檢查隱含 barrier：lock、有回傳值的 atomic、`set_current_state`、RCU API。

## 自我檢核

- [ ] 不看書，列出編譯器可以對普通記憶體存取做的至少五種轉換，各舉一個會在 lockless 程式碼裡出事的情境。
- [ ] 解釋為什麼 `READ_ONCE` 要用 `__unqual_scalar_typeof`，而不是直接 `*(volatile typeof(x) *)&(x)`。
- [ ] 用 SB 實驗的結果，向一個同事解釋「為什麼 release/acquire 不能用在等待／喚醒」。
- [ ] 拿 `fd_install` 和 `expand_fdtable` 那一對，不看書畫出兩欄圖，並說明 `synchronize_rcu()` 在這個協定裡負責哪一種情況。
- [ ] 有人在 code review 裡說「這裡加個 `smp_mb()` 保險一點」。你會問他哪兩個問題？

## 延伸閱讀

### 官方文件

- **[Linux Kernel Memory Barriers](https://docs.kernel.org/core-api/wrappers/memory-barriers.html)**（tree 內：`Documentation/memory-barriers.txt`）
  - **讀哪裡**：「WHAT ARE MEMORY BARRIERS?」到「SMP BARRIER PAIRING」（第 939 行附近）是核心；「COMPILER BARRIER」（第 1532 行起）是本章第一層的完整版。「EXAMPLE USES」之後可以跳過。
  - **注意**：這份文件很長，風格是條列警告，不是循序教學。第一次讀建議搭配本章的實驗結果。
  - **前提**：知道 cache 和 store buffer 的存在即可。
- **`tools/memory-model/Documentation/`**（v6.18 tree 內）
  - **讀哪裡**：`simple.txt`（給「只想寫對程式」的人）、`ordering.txt`（各種 ordering 原語的分類）、`recipes.txt`（常見模式的標準寫法，MP、SB、LB 都在裡面）、`access-marking.txt`（什麼時候該用 `READ_ONCE`）。
  - **和本章的關聯**：本章的 barrier 速查表與「讀碼流程」是這幾份文件的濃縮；遇到本章沒涵蓋的模式，查 `recipes.txt`。
- **[volatile considered harmful](https://docs.kernel.org/process/volatile-considered-harmful.html)**（tree 內：`Documentation/process/volatile-considered-harmful.rst`）
  - **讀哪裡**：全文，很短。
  - **和本章的關聯**：解釋 kernel 為什麼用 `READ_ONCE` 而不是 volatile 變數。

### 論文

- **Frightening Small Children and Disconcerting Grown-ups: Concurrency in the Linux Kernel** — Jade Alglave, Luc Maranget, Paul E. McKenney, Andrea Parri, Alan Stern，ASPLOS 2018（論文頁面與工具：[diy.inria.fr/linux](http://diy.inria.fr/linux/)；出處見 tree 內 `tools/memory-model/Documentation/references.txt`）
  - **核心貢獻**：LKMM 的正式論文。用 cat 語言形式化定義 kernel 的記憶體模型，並用 herd7 對 litmus test 求解；這套模型就是 `tools/memory-model/` 的內容。
  - **讀哪裡**：前三節（動機、LKMM 的直覺說明、範例）；形式化定義的章節可以先跳過。
  - **前提**：本章的 MP、SB 模式。

### 部落格 / 技術文章

- **[Who's afraid of a big bad optimizing compiler?](https://lwn.net/Articles/793253/)** — Jade Alglave、Will Deacon、Paul E. McKenney、Alan Stern、Peter Zijlstra 等 12 位（LWN.net，2019；續篇 [Calibrating your fear of big bad optimizing compilers](https://lwn.net/Articles/799218/)）
  - **這篇說什麼**：逐一列舉編譯器對普通存取能做的轉換（load tearing、store fusing、invented loads……），每一種配一個 kernel 情境，並說明 `READ_ONCE`/`WRITE_ONCE` 如何擋住它。
  - **讀哪裡**：整篇。它是本章第一層的加長版，例子比 memory-barriers.txt 更貼近真實 kernel 程式碼。
  - **為什麼值得讀**：作者群就是 LKMM 的維護者；連結取自 v6.18 tree 的 `tools/memory-model/Documentation/references.txt`。

### 書籍

- **《Is Parallel Programming Hard, And, If So, What Can You Do About It?》** — Paul E. McKenney（持續更新的免費電子書，kernel.org 上有 PDF）
  - **讀哪幾章**：「Memory Ordering」那一章（各版章號不同，以目錄為準）。從硬體為什麼重排講起，到 Linux 的 barrier API 與 LKMM。
  - **這本書的定位**：作者是 RCU 的作者與 LKMM 的共同作者。本章讀完想把 memory ordering 讀到能自己推導的程度，這是下一站。

barrier 和 `READ_ONCE` 在 kernel 裡經常被巨集包起來：`smp_load_acquire` 本身就是一層層的巨集。下一章把 kernel 裡最常見的巨集家族一次拆完。

→ [Ch 22 kernel 巨集解碼大全：for_each、DEFINE_*、module_init、EXPORT_SYMBOL](./22-kernel-macro-decoding.md)
