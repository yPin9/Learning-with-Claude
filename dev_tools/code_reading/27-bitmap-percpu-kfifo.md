# Ch 27 — bitmap、per-cpu 變數與 kfifo

> **目標**：讀到 `DECLARE_BITMAP`、`set_bit`/`__set_bit`、`for_each_set_bit`、`cpumask` 時知道它們的成本與原子性差異；讀到 `DEFINE_PER_CPU`、`this_cpu_*`、`per_cpu_ptr` 時立刻聯想到「作者刻意讓每個 CPU 各寫各的，以避開共享與 lock」，並檢查周圍是否滿足 preemption 的前提；讀到 `in - out`、`& mask` 這種 ring buffer index 運算時，知道它為什麼在無號整數溢位後仍然正確、以及為什麼大小必須是 2 的冪。

> **鴻溝**：本章主要練**慣用法鴻溝**（`__set_bit` 和 `set_bit` 只差兩個底線、意義卻差很多）與**領域鴻溝**（per-cpu 背後的 cache coherence 成本、segment register）。

> **環境**：Linux kernel v6.18 原始碼與 `~/code_reading/build/vmlinux`（x86_64，`CONFIG_NR_CPUS=64`、`CONFIG_SMP=y`、`CONFIG_PREEMPT_DYNAMIC=y`）；user-space 範例 gcc 11.4、WSL Ubuntu 22.04 x86_64，16 個邏輯 CPU。

## 為什麼需要這個？

前四章的結構都在組織「物件」。這一章的三個工具處理的是更底層的形狀：

- **bitmap**：一大群布林值。哪些 CPU 在線上？哪些 RT 優先權等級有 task 在排隊？哪些 IRQ 號碼已被配置？用一個 bit 代表一個項目，64 個項目擠進一個 `unsigned long`，而且 CPU 有指令能一次找出第一個為 1 的 bit。
- **per-cpu 變數**：「這個計數器全系統共用」聽起來很合理，但在 64 核機器上，每一次 `counter++` 都要讓那條 cache line 在 64 個核之間搬來搬去。per-cpu 把一個變數變成 N 份，每個 CPU 只寫自己那份，要總數時再加起來。
- **kfifo / ring buffer**：生產者與消費者之間的固定大小緩衝。kernel 版本用了一個漂亮的 index 技巧，讓「滿」與「空」的判斷不需要額外欄位，單一生產者單一消費者時甚至不需要 lock。

三者的共同點是：**它們的存在本身就是設計意圖的訊號**。看到 per-cpu，作者在告訴你「這裡是熱路徑，我在避免共享」；看到 `__set_bit` 而不是 `set_bit`，作者在告訴你「這裡已經有別的東西保護了」。讀懂這些訊號，比讀懂 API 本身更重要。

## 先建立直覺

```
bitmap                         per-cpu                          kfifo（大小 8）
──────                         ───────                          ──────────────
unsigned long[2]               一個變數名，N 份實體              in、out 只增不減；取 slot 時 & 7
 word0: ...0011 (bit 0,1)      ┌─────────┐ ┌─────────┐          slot: 0 1 2 3 4 5 6 7
 word1: ...0001 (bit 64)       │ CPU0 的 │ │ CPU1 的 │                 ▲     ▲
                               │  copy   │ │  copy   │ ...           out&7  in&7
找第一個 1：一條 tzcnt 指令    └─────────┘ └─────────┘          len = in - out（即使溢位也對）
                               各寫各的 cache line，不互相干擾
```

per-cpu 的直覺可以用投票所想像：與其讓全國選民排隊到同一個票箱投票（共享計數器，所有人搶同一個鎖），不如每個投票所各放一個票箱，開票時再加總。投票（寫入）完全不互相等待，開票（讀總數）比較慢，但開票很少發生。**這個比喻在「寫多讀少」時成立；如果你每秒要開票一百萬次，per-cpu 反而更慢**。

## bitmap

### 定義與大小

`include/linux/types.h:10-11（v6.18）`：

```c
#define DECLARE_BITMAP(name,bits) \
	unsigned long name[BITS_TO_LONGS(bits)]
```

`BITS_TO_LONGS(nr)` 是 `__KERNEL_DIV_ROUND_UP(nr, BITS_PER_TYPE(long))`（`include/linux/bitops.h:11`），即向上取整。所以 `DECLARE_BITMAP(x, 100)` 在 64 位元上是 `unsigned long x[2]`，16 bytes，最後 28 個 bit 是用不到的尾巴。**bitmap 就是 `unsigned long` 陣列**，沒有任何 header，大小資訊不在資料裡——每個 bitmap 函式都要你另外傳 `nbits`。

`struct cpumask` 是 bitmap 最重要的使用者（`include/linux/cpumask_types.h:9（v6.18）`）：

```c
typedef struct cpumask { DECLARE_BITMAP(bits, NR_CPUS); } cpumask_t;
```

包一層 struct 是為了型別安全（不會把 cpumask 誤傳給別的 bitmap 函式）並讓它能整體賦值。`for_each_cpu` 最後展開成 `for_each_set_bit`（`include/linux/cpumask.h:376-377`），`for_each_online_cpu(cpu)` 就是「走訪 `cpu_online_mask` 裡所有為 1 的 bit」（`include/linux/cpumask.h:1119`）。

### 走訪：for_each_set_bit

`include/linux/find.h:585-586（v6.18）`：

```c
#define for_each_set_bit(bit, addr, size) \
	for ((bit) = 0; (bit) = find_next_bit((addr), (size), (bit)), (bit) < (size); (bit)++)
```

條件式用了逗號運算子（[Ch 9](./09-expressions-integers-bits.md)）：先把 `bit` 設成「從目前位置開始下一個為 1 的 bit」，再判斷有沒有超出範圍。`find_next_bit` 找不到時回傳 `size`，所以 **`find_*_bit` 的「找不到」不是 -1，而是等於 size**——讀碼時看到 `if (bit >= nbits)` 就是在判斷找不到。

user-space 移植語意（實作是簡化版）跑一次：

```c
#include <stdio.h>
#include <limits.h>
/* 子集移植自 v6.18 include/linux/{types.h,bitops.h,find.h} 的語意（實作是簡化版） */
#define BITS_PER_LONG (sizeof(long) * CHAR_BIT)
#define BITS_TO_LONGS(nr) (((nr) + BITS_PER_LONG - 1) / BITS_PER_LONG)
#define DECLARE_BITMAP(name, bits) unsigned long name[BITS_TO_LONGS(bits)]
static void set_bit(unsigned nr, unsigned long *a) { a[nr / BITS_PER_LONG] |= 1UL << (nr % BITS_PER_LONG); }
static void clear_bit(unsigned nr, unsigned long *a) { a[nr / BITS_PER_LONG] &= ~(1UL << (nr % BITS_PER_LONG)); }
static unsigned long find_next_bit(const unsigned long *a, unsigned long size, unsigned long off)
{
	while (off < size) {
		unsigned long w = a[off / BITS_PER_LONG] >> (off % BITS_PER_LONG);
		if (w) { off += __builtin_ctzl(w); return off < size ? off : size; }
		off = (off / BITS_PER_LONG + 1) * BITS_PER_LONG;   /* 整個 word 是 0，跳下一個 word */
	}
	return size;
}
#define for_each_set_bit(bit, addr, size) \
	for ((bit) = 0; (bit) = find_next_bit((addr), (size), (bit)), (bit) < (size); (bit)++)

int main(void)
{
	DECLARE_BITMAP(online, 100) = {0};           /* 100 bit -> 2 個 unsigned long */
	printf("sizeof(online)=%zu bytes, BITS_TO_LONGS(100)=%zu\n", sizeof(online), BITS_TO_LONGS(100));
	int cpus[] = { 0, 1, 5, 63, 64, 99 };
	for (int i = 0; i < 6; i++) set_bit(cpus[i], online);
	clear_bit(5, online);
	printf("word[0]=%#018lx word[1]=%#018lx\n", online[0], online[1]);
	unsigned long cpu;
	printf("for_each_set_bit:");
	for_each_set_bit(cpu, online, 100) printf(" %lu", cpu);
	printf("\nfind_next_bit(off=2) = %lu, find_next_bit(off=100) = %lu (== size: not found)\n",
	       find_next_bit(online, 100, 2), find_next_bit(online, 100, 100));
	return 0;
}
```

```
$ gcc -O1 -Wall -o bitmap_demo bitmap_demo.c && ./bitmap_demo
sizeof(online)=16 bytes, BITS_TO_LONGS(100)=2
word[0]=0x8000000000000003 word[1]=0x0000000800000001
for_each_set_bit: 0 1 63 64 99
find_next_bit(off=2) = 63, find_next_bit(off=100) = 100 (== size: not found)
```

bit 0、1、63 在第一個 word（`0x8000000000000003`），bit 64 和 99 在第二個 word 的第 0 位與第 35 位（`0x0000000800000001`）。`find_next_bit(off=2)` 跳過整段 0，直接回 63——在真實 kernel 裡這一跳是一條 `tzcnt`/`bsf` 指令，不是逐 bit 檢查。

kernel 編譯出來的確如此。`nr_processes()` 的原始碼是 `for_each_possible_cpu(cpu) total += per_cpu(process_counts, cpu);`，看它在 vmlinux 裡的樣子（gdb `disassemble nr_processes`，節錄）：

```
   0xffffffff81299539 <+9>:	mov    0x1841028(%rip),%rdx        # 0xffffffff82ada568 <__cpu_possible_mask>
   ...
   0xffffffff81299548 <+24>:	tzcnt  %rdx,%rax
   ...
   0xffffffff81299568 <+56>:	mov    -0x7d5263c0(,%rax,8),%rax
   0xffffffff81299570 <+64>:	movslq %ecx,%rcx
   0xffffffff81299573 <+67>:	add    (%rax,%rsi,1),%r8d
   ...
   0xffffffff81299590 <+96>:	tzcnt  %rax,%rax
```

因為 `NR_CPUS=64`，整個 `cpu_possible_mask` 只有一個 word，編譯器把 `find_next_bit` 完全 inline 成 `tzcnt`（數尾端 0 的個數 = 最低的 1 在哪）。中間那行 `mov -0x7d5263c0(,%rax,8),%rax` 是從 `__per_cpu_offset[cpu]` 陣列取出該 CPU 的 per-cpu 基底——下一節會解釋。

### 真實例子：RT 排程器的 O(1) 挑選

RT 排程類有 100 個優先權（`MAX_RT_PRIO = 100`，`include/linux/sched/prio.h:16`），每個優先權一條 queue，再用一個 bitmap 記錄「哪些 queue 非空」（`kernel/sched/sched.h:304-311（v6.18）`）：

```c
/*
 * This is the priority-queue data structure of the RT scheduling class:
 */
struct rt_prio_array {
	DECLARE_BITMAP(bitmap, MAX_RT_PRIO+1); /* include 1 bit for delimiter */
	struct list_head queue[MAX_RT_PRIO];
};
```

挑下一個 task（`kernel/sched/rt.c:1665-1681（v6.18）`）：

```c
static struct sched_rt_entity *pick_next_rt_entity(struct rt_rq *rt_rq)
{
	struct rt_prio_array *array = &rt_rq->active;
	struct sched_rt_entity *next = NULL;
	struct list_head *queue;
	int idx;

	idx = sched_find_first_bit(array->bitmap);
	BUG_ON(idx >= MAX_RT_PRIO);

	queue = array->queue + idx;
	if (WARN_ON_ONCE(list_empty(queue)))
		return NULL;
	next = list_entry(queue->next, struct sched_rt_entity, run_list);

	return next;
}
```

`sched_find_first_bit` 是針對 100 bit 特化的版本（`include/asm-generic/bitops/sched.h:13-18（v6.18）`），64 位元上最多看兩個 word：

```c
static inline int sched_find_first_bit(const unsigned long *b)
{
#if BITS_PER_LONG == 64
	if (b[0])
		return __ffs(b[0]);
	return __ffs(b[1]) + 64;
```

注意它沒有檢查 `b[1]` 是不是 0——如果兩個 word 都是 0，`__ffs(0)` 結果未定義。這就是註解裡那個「delimiter」的用途：初始化時永遠把 bit 100 設成 1（`kernel/sched/rt.c:78-79`：`/* delimiter for bitsearch: */ __set_bit(MAX_RT_PRIO, array->bitmap);`），保證搜尋一定會停下來。**這是一個需要跨三個檔案才能看懂的不變式**：`sched.h` 的 `+1` 與註解、`rt.c` 的初始化、`bitops/sched.h` 沒有零檢查。讀碼時看到「陣列多開一格」，要去找那一格是誰、在哪裡被設定。

這個結構是「bitmap 索引 + 一組 list」的組合，維護規則是：enqueue 時 `list_add_tail` 然後 `__set_bit`（`rt.c:1347-1352`）；dequeue 時 `list_del_init`，如果該 queue 空了就 `__clear_bit`（`rt.c:1214-1217`）。**bitmap 是 list 狀態的摘要，兩者必須同步**——又是 Ch 24/26 那個「存彙總以換取快速查詢」的想法。

### `set_bit` 與 `__set_bit`：兩個底線的差別

上面全部用 `__set_bit`。它和 `set_bit` 的差別在 x86 實作上一目了然（`arch/x86/include/asm/bitops.h:52-63（v6.18）`）：

```c
arch_set_bit(long nr, volatile unsigned long *addr)
{
	if (__builtin_constant_p(nr)) {
		asm_inline volatile(LOCK_PREFIX "orb %b1,%0"
			: CONST_MASK_ADDR(nr, addr)
			: "iq" (CONST_MASK(nr))
			: "memory");
	} else {
		asm_inline volatile(LOCK_PREFIX __ASM_SIZE(bts) " %1,%0"
			: : RLONG_ADDR(addr), "Ir" (nr) : "memory");
	}
}
```

`set_bit` 帶 `LOCK_PREFIX`，是原子的 read-modify-write；`__set_bit` 是普通的 `|=`，兩個 CPU 同時對同一個 word 的不同 bit 做 `__set_bit`，可能有一個人的修改被覆蓋掉。

| | 原子？ | 何時用 | 讀碼時代表 |
|---|---|---|---|
| `set_bit`/`clear_bit`/`test_and_set_bit` | 是（x86 用 `lock` 前綴） | 多個 CPU 可能同時修改同一個 word | 這個 bitmap 沒有外部鎖，或鎖不涵蓋所有寫者 |
| `__set_bit`/`__clear_bit` | 否 | 已持有保護這個 bitmap 的鎖，或 bitmap 是私有的 | **作者宣稱這裡已經被保護了**，去找那把鎖 |

RT 排程器用 `__set_bit` 是因為整個 `rt_rq` 被 runqueue lock 保護。**看到 `__set_bit`，就應該能在附近找到一把鎖（或證明 bitmap 是 local 變數）；找不到就是可疑點**。這和 Ch 26 的 `__xa_alloc`、Ch 23 的 `__list_add` 是同一個「雙底線 = 呼叫者負責」的慣例。

## per-cpu 變數

### 為什麼存在：先看共享的代價

在 user-space 用 4 個執行緒各做 2000 萬次遞增，比較三種寫法：

```c
/* per-cpu 的 user-space 類比：N 個執行緒各自累加計數器。
 * shared   : 所有執行緒對同一個變數做 atomic add（kernel 裡等於 atomic_long_inc 全域計數器）
 * padded   : 每個執行緒一個槽，各佔一條 cache line（對應 DEFINE_PER_CPU 的效果）
 * unpadded : 每個執行緒一個槽但擠在同一條 cache line（false sharing） */
#include <stdio.h>
#include <pthread.h>
#include <stdatomic.h>
#include <time.h>
#define NT 4
#define ITERS 20000000UL
static atomic_ulong shared;
static struct { _Alignas(64) unsigned long v; } padded[NT];
static volatile unsigned long unpadded[NT];
static int mode;
static void *worker(void *arg)
{
	long id = (long)arg;
	for (unsigned long i = 0; i < ITERS; i++) {
		if (mode == 0) atomic_fetch_add_explicit(&shared, 1, memory_order_relaxed);
		else if (mode == 1) { padded[id].v++; __asm__ volatile("" ::: "memory"); }
		else unpadded[id]++;
	}
	return NULL;
}
static double run(int m)
{
	struct timespec a, b; pthread_t t[NT]; mode = m;
	clock_gettime(CLOCK_MONOTONIC, &a);
	for (long i = 0; i < NT; i++) pthread_create(&t[i], NULL, worker, (void *)i);
	for (int i = 0; i < NT; i++) pthread_join(t[i], NULL);
	clock_gettime(CLOCK_MONOTONIC, &b);
	return (b.tv_sec - a.tv_sec) + (b.tv_nsec - a.tv_nsec) / 1e9;
}
int main(void)
{
	const char *name[] = { "shared atomic", "per-thread padded", "per-thread unpadded" };
	for (int m = 0; m < 3; m++) printf("%-20s %.3f s\n", name[m], run(m));
	unsigned long sum = 0; for (int i = 0; i < NT; i++) sum += padded[i].v;
	printf("sum(padded)=%lu shared=%lu\n", sum, (unsigned long)shared);
	return 0;
}
```

```
$ gcc -O2 -Wall -pthread -o percpu_bench percpu_bench.c && ./percpu_bench && ./percpu_bench
shared atomic        0.780 s
per-thread padded    0.047 s
per-thread unpadded  0.359 s
sum(padded)=80000000 shared=80000000
shared atomic        0.823 s
per-thread padded    0.041 s
per-thread unpadded  0.393 s
sum(padded)=80000000 shared=80000000
```

**條件與誠實聲明**：這是 WSL2 上 16 個邏輯 CPU、4 個執行緒的結果，數字會隨 CPU 型號、核心拓撲、虛擬化層而變；它展示的是量級差異的方向，不是可以引用的效能數據。在這台機器上，共享的原子計數器比各寫各的慢約 17–20 倍；即使不用原子操作，只要槽位擠在同一條 cache line（false sharing），仍然慢約 8–9 倍。慢的原因不是「原子指令本身貴」，而是那條 cache line 必須在核之間來回轉移所有權（MESI 協定的 invalidation）。

per-cpu 變數就是 kernel 對這個問題的系統性解法：**每個 CPU 一份，彼此在不同的記憶體區域，寫入時完全不共享**。

### 定義與存取

```c
DEFINE_PER_CPU(unsigned long, process_counts) = 0;
```

這是 `kernel/fork.c:153（v6.18）`。`DEFINE_PER_CPU` 最終展開為帶 `__PCPU_ATTRS(sec)` 的普通變數定義（`include/linux/percpu-defs.h:102-114`），差別在 section：它被放進 `.data..percpu`（`include/asm-generic/percpu.h:68`）。開機時 kernel 為每個 CPU 複製一份這整個 section，並把每份的起點與原始位置的差值存進 `__per_cpu_offset[cpu]`。

所以「CPU n 的那一份」的位址是 `&process_counts + __per_cpu_offset[n]`，這就是 `per_cpu_ptr`（`include/linux/percpu-defs.h:237-241、273（v6.18）`）：

```c
#define per_cpu_ptr(ptr, cpu)						\
({									\
	__verify_pcpu_ptr(ptr);						\
	SHIFT_PERCPU_PTR((ptr), per_cpu_offset((cpu)));			\
})
...
#define per_cpu(var, cpu)	(*per_cpu_ptr(&(var), cpu))
```

**讀碼重點：`&process_counts` 本身不是一個可以直接解參考的有效位址**（它指向原始 section 的模板，不屬於任何 CPU），一定要經過 `per_cpu_ptr`/`this_cpu_ptr` 加上 offset。sparse 用 `__percpu` 標註來抓「直接解參考 per-cpu 指標」的錯誤（[Ch 18](./18-annotations-and-sparse.md)）。

加總的那一端（`kernel/fork.c:165-173（v6.18）`）：

```c
int nr_processes(void)
{
	int cpu;
	int total = 0;

	for_each_possible_cpu(cpu)
		total += per_cpu(process_counts, cpu);

	return total;
}
```

前面的反組譯已經看到：`for_each_possible_cpu` 變成 `tzcnt` 迴圈，`per_cpu(...)` 變成「從 `__per_cpu_offset` 陣列取 offset，加上 `process_counts` 的位址（`$0xffffffff83e59b70`，可用 `nm vmlinux | grep process_counts` 對上），讀出來加總」。注意這個加總**沒有拿任何鎖**——讀到的是一個「近似值」，某些 CPU 的計數可能正在改變。這對 `/proc/loadavg` 顯示 process 數量來說完全夠用。**per-cpu 計數器的讀取端通常就是這樣：便宜的寫、昂貴且不精確的讀**。

寫入端（`kernel/fork.c:2401（v6.18）`）：

```c
			__this_cpu_inc(process_counts);
```

在 vmlinux 裡它變成**一條指令**：

```
$ objdump -d --no-show-raw-insn --disassemble=copy_process vmlinux | grep "%gs:" | grep process_counts
ffffffff8129b936:	incq   %gs:0x2bbe232(%rip)        # ffffffff83e59b70 <process_counts>
```

x86_64 的 `%gs` segment base 被設成「目前 CPU 的 per-cpu 區域起點」，所以 `incq %gs:process_counts` 等於「把目前 CPU 那一份加一」。**沒有 `lock` 前綴**——不需要，因為沒有別的 CPU 會寫這個位置。`Documentation/core-api/this_cpu_ops.rst` 的「Inner working of this_cpu operations」一節正是這樣描述的。同一個函式裡你還會看到 `mov %gs:...<current_task>`：`current` 本身也是一個 per-cpu 變數。

### 為什麼 per-cpu 需要 preemption 的配合

`this_cpu_ops.rst` 解釋了 `this_cpu_*` 系列的關鍵性質：

```
This means that there are no atomicity issues between the calculation of
the offset and the operation on the data. Therefore it is not
necessary to disable preemption or interrupts to ensure that the
processor is not changed between the calculation of the address and
the operation on the data.
```

在 x86 上，「算出本 CPU 的位址」和「對它做加法」是同一條 `incq %gs:...` 指令，中間不可能被搶佔到別的 CPU。但不是所有架構都有這種 segment 定址；而且一旦你需要**多條指令**操作 per-cpu 資料（讀一個欄位、根據它改另一個欄位），問題就來了：

```
CPU 0 上執行的 task A：
  p = this_cpu_ptr(&stats);    // p 指向 CPU 0 的那份
  ─── 被搶佔，排程器把 A 遷移到 CPU 1 ───
  p->count++;                  // A 現在在 CPU 1 上，卻在寫 CPU 0 的資料
                               // 而 CPU 0 上新跑的 task B 也在寫同一份 → 競爭
```

所以 per-cpu 的 API 分三層，讀碼時要分辨：

| API | 前提 | 讀碼時代表 |
|---|---|---|
| `this_cpu_inc(x)` / `this_cpu_add` 等 | 無；API 自己保證對搶佔與中斷安全 | 單一操作，作者不想管 preemption |
| `__this_cpu_inc(x)` 等 | **呼叫者必須已關閉 preemption**（通常也要確保中斷不會碰同一個變數） | 去找外層的 `preempt_disable`、`local_irq_disable`、`spin_lock_irq` |
| `get_cpu_var(x)` … `put_cpu_var(x)` | 巨集自己 `preempt_disable`/`preempt_enable` | 中間是一段多指令的 per-cpu 臨界區 |
| `per_cpu(x, cpu)` / `per_cpu_ptr(p, cpu)` | 存取**指定** CPU 的那份；通常是讀取或初始化 | 跨 CPU 存取，可能需要額外同步 |

`get_cpu_var` 的定義把這個前提寫得很明白（`include/linux/percpu-defs.h:279-293（v6.18）`）：

```c
#define get_cpu_var(var)						\
(*({									\
	preempt_disable();						\
	this_cpu_ptr(&var);						\
}))
...
#define put_cpu_var(var)						\
do {									\
	(void)&(var);							\
	preempt_enable();						\
} while (0)
```

回到 `copy_process`：它用的是 `__this_cpu_inc`，所以照規則要找外層的保護。往上看，`kernel/fork.c:2327` 是 `write_lock_irq(&tasklist_lock);`，一直到 `2418` 才 `write_unlock_irq`。關中斷就等於不可能被搶佔，前提成立。`fork.c:137` 的註解「Protected counters by write_lock_irq(&tasklist_lock)」也印證了這一點。開 `CONFIG_DEBUG_PREEMPT` 時，`__this_cpu_*` 會呼叫 `__this_cpu_preempt_check()`（`include/linux/percpu-defs.h:315`、`lib/smp_processor_id.c:62-65`），在前提不成立時印出警告。

**讀碼心法**：看到 per-cpu，先問兩個問題——(1) 作者在避免什麼共享？（通常是熱路徑上的計數器、快取、free list）(2) 前提怎麼滿足？（用的是自帶保護的 `this_cpu_*`，還是 `__this_cpu_*` 搭配外層的 preempt/irq 保護）。第二個問題答不出來，就可能是 bug。

per-cpu 不只用於計數器。slab allocator 的 per-cpu 物件快取、page allocator 的 per-cpu page list（`pcp`）、網路的 per-cpu backlog queue、`current` 背後的 per-cpu 變數 `current_task`，全是同一個思路：**把共享資料切成每 CPU 一份，熱路徑只碰自己那份，冷路徑（加總、搬移、回收）才跨 CPU**。eventpoll 裡的 `percpu_counter_inc(&ep->user->epoll_watches)`（[練習 D](./practice-d-data-structure-map.md) 會看到）則是 per-cpu 計數器的封裝版：`lib/percpu_counter.c` 讓每個 CPU 先在本地累積，超過門檻才併入全域總數。

## kfifo 與 ring buffer 的 index 技巧

### 資料形狀

`include/linux/kfifo.h:49-55（v6.18）`：

```c
struct __kfifo {
	unsigned int	in;
	unsigned int	out;
	unsigned int	mask;
	unsigned int	esize;
	void		*data;
};
```

只有 `in`（寫入總數）、`out`（讀出總數）、`mask`（大小減一）。**沒有 `count` 欄位，也沒有 `full` 旗標**。長度是 `in - out`（`include/linux/kfifo.h:238-242`）：

```c
#define kfifo_len(fifo) \
({ \
	typeof((fifo) + 1) __tmpl = (fifo); \
	__tmpl->kfifo.in - __tmpl->kfifo.out; \
})
```

剩餘空間（`lib/kfifo.c:20-23（v6.18）`）：

```c
static inline unsigned int kfifo_unused(struct __kfifo *fifo)
{
	return (fifo->mask + 1) - (fifo->in - fifo->out);
}
```

`in` 和 `out` **只增不減，從不歸零**，讓它們在 `UINT_MAX` 自然溢位繞回 0。取實際 slot 時才 `& mask`（`lib/kfifo.c:90-112（v6.18）`，`kfifo_copy_in` 節錄）：

```c
	off &= fifo->mask;
	...
	l = min(len, size - off);

	memcpy(fifo->data + off, src, l);
	memcpy(fifo->data, src + l, len - l);
	/*
	 * make sure that the data in the fifo is up to date before
	 * incrementing the fifo->in index counter
	 */
	smp_wmb();
```

以及 `__kfifo_in`（`lib/kfifo.c:114-126`）：

```c
unsigned int __kfifo_in(struct __kfifo *fifo,
		const void *buf, unsigned int len)
{
	unsigned int l;

	l = kfifo_unused(fifo);
	if (len > l)
		len = l;

	kfifo_copy_in(fifo, buf, len, fifo->in);
	fifo->in += len;
	return len;
}
```

### 為什麼溢位後還是對的

C 標準規定無號整數運算是 mod 2^N 的。只要「真實長度」永遠不超過 2^32，`in - out` 在 mod 2^32 下算出來的就是真實長度，不管 `in` 有沒有先繞回 0。例如 `in = 2`（已經繞過一圈）、`out = 4294967293`：`2 - 4294967293 = 5 (mod 2^32)`。

而 `& mask` 要正確，前提是 **2^32 能被 buffer 大小整除**——否則 index 從 `UINT_MAX` 繞回 0 的那一刻，`index % size` 會跳號。2^32 只能被 2 的冪整除，所以 buffer 大小必須是 2 的冪。`__kfifo_alloc` 的註解直接寫了（`lib/kfifo.c:28-32（v6.18）`）：

```c
	/*
	 * round up to the next power of 2, since our 'let the indices
	 * wrap' technique works only in this case.
	 */
	size = roundup_pow_of_two(size);
```

用 user-space 重現，並且故意把起點放在 `UINT_MAX` 附近，逼 index 溢位：

```c
/* 重現 v6.18 lib/kfifo.c 的 index 技巧：in/out 是只增不減的 unsigned int，
 * 讓它自然溢位；取 slot 時才 & mask。 */
#include <stdio.h>
#include <string.h>
#include <limits.h>

struct fifo { unsigned int in, out, mask; char data[8]; };   /* 8 = 2^3 */

static unsigned int fifo_len(struct fifo *f)    { return f->in - f->out; }
static unsigned int fifo_unused(struct fifo *f) { return (f->mask + 1) - (f->in - f->out); }

static unsigned int fifo_in(struct fifo *f, const char *buf, unsigned int len)
{
	unsigned int size = f->mask + 1, off, l;
	if (len > fifo_unused(f)) len = fifo_unused(f);
	off = f->in & f->mask;
	l = len < size - off ? len : size - off;          /* 第一段：到陣列尾端 */
	memcpy(f->data + off, buf, l);
	memcpy(f->data, buf + l, len - l);                /* 第二段：繞回開頭 */
	f->in += len;                                     /* 可能從 UINT_MAX 溢位到 0 */
	return len;
}
static unsigned int fifo_out(struct fifo *f, char *buf, unsigned int len)
{
	unsigned int size = f->mask + 1, off, l;
	if (len > fifo_len(f)) len = fifo_len(f);
	off = f->out & f->mask;
	l = len < size - off ? len : size - off;
	memcpy(buf, f->data + off, l);
	memcpy(buf + l, f->data, len - l);
	f->out += len;
	return len;
}

int main(void)
{
	/* 故意把起點放在 UINT_MAX 附近，逼 in/out 溢位 */
	struct fifo f = { .in = UINT_MAX - 2, .out = UINT_MAX - 2, .mask = 7 };
	char buf[16];
	unsigned int n;

	n = fifo_in(&f, "ABCDE", 5);
	printf("in 5  -> wrote %u  in=%u out=%u len=%u\n", n, f.in, f.out, fifo_len(&f));
	n = fifo_in(&f, "FGHIJK", 6);
	printf("in 6  -> wrote %u  in=%u out=%u len=%u (full)\n", n, f.in, f.out, fifo_len(&f));
	n = fifo_out(&f, buf, 4); buf[n] = 0;
	printf("out 4 -> \"%s\"  in=%u out=%u len=%u\n", buf, f.in, f.out, fifo_len(&f));
	n = fifo_in(&f, "XYZ", 3);
	printf("in 3  -> wrote %u  in=%u out=%u len=%u\n", n, f.in, f.out, fifo_len(&f));
	n = fifo_out(&f, buf, 16); buf[n] = 0;
	printf("out * -> \"%s\"  len=%u\n", buf, fifo_len(&f));

	/* 為什麼一定要 2 的冪：size=6 時改用 % 6，跨過 2^32 那一刻 slot 會跳號 */
	printf("\nsize=6, index %% 6 across the wrap:\n");
	unsigned int idx = UINT_MAX - 2;
	for (int i = 0; i < 6; i++, idx++)
		printf("  index=%10u  %%6=%u   &7=%u\n", idx, idx % 6, idx & 7);
	return 0;
}
```

```
$ gcc -O1 -Wall -o kfifo_demo kfifo_demo.c && ./kfifo_demo
in 5  -> wrote 5  in=2 out=4294967293 len=5
in 6  -> wrote 3  in=5 out=4294967293 len=8 (full)
out 4 -> "ABCD"  in=5 out=1 len=4
in 3  -> wrote 3  in=8 out=1 len=7
out * -> "EFGHXYZ"  len=0

size=6, index % 6 across the wrap:
  index=4294967293  %6=1   &7=5
  index=4294967294  %6=2   &7=6
  index=4294967295  %6=3   &7=7
  index=         0  %6=0   &7=0
  index=         1  %6=1   &7=1
  index=         2  %6=2   &7=2
```

讀這個輸出：

- 第一次寫入後 `in` 已經溢位成 2，`out` 還是 4294967293，但 `len = 5` 正確。
- 寫 6 個只寫進 3 個，因為 `kfifo_unused` 算出只剩 3 格；`len = 8` 就是滿了。**`in - out == size` 表示滿、`in == out` 表示空**，兩者不會混淆——這是「只增不減」帶來的好處。傳統的「index 在 0..size−1 之間打轉」寫法裡，滿和空都是 `in == out`，必須犧牲一格或另存一個 count。
- 資料跨越陣列尾端時，`kfifo_copy_in` 用兩次 `memcpy` 處理（`l` 是第一段長度，`len - l` 是繞回開頭的第二段）。輸出 `"EFGHXYZ"` 的順序完全正確。
- 最後一段是失敗示範：如果大小是 6 並用 `% 6` 取 slot，index 從 4294967295 繞到 0 的那一步，slot 從 3 直接跳到 0，**跳過了 4 和 5**。用 `& 7` 則連續無縫（5、6、7、0、1、2）。2^32 = 4294967296 不是 6 的倍數，這就是原因。

### 無鎖的條件

kfifo.h 檔頭的鎖說明（`include/linux/kfifo.h:30-36（v6.18）`）：

```c
 * Note about locking: There is no locking required until only one reader
 * and one writer is using the fifo and no kfifo_reset() will be called.
 * kfifo_reset_out() can be safely used, until it will be only called
 * in the reader thread.
 * For multiple writer and one reader there is only a need to lock the writer.
 * And vice versa for only one writer and multiple reader there is only a need
 * to lock the reader.
```

（原文的「until」意思是「as long as」。）單一生產者、單一消費者時不需要鎖，因為**`in` 只有生產者寫、`out` 只有消費者寫**，各自擁有一個欄位。唯一要保證的是順序：生產者必須先把資料寫進 buffer、再推進 `in`，否則消費者可能看到 `in` 變大了，卻讀到還沒寫完的資料。這就是 `kfifo_copy_in` 結尾那個 `smp_wmb()` 的作用（[Ch 21](./21-read-once-and-barriers.md)）。

### 同一個技巧在 pipe 裡

[Final Project](./final-project-pipe-eventfd-guided-reading.md) 要讀的 `fs/pipe.c`，它的 ring 用的是同一套 index 運算（`include/linux/pipe_fs_i.h:184-209（v6.18）`）：

```c
static inline unsigned int pipe_occupancy(unsigned int head, unsigned int tail)
{
	return (pipe_index_t)(head - tail);
}
...
static inline bool pipe_empty(unsigned int head, unsigned int tail)
{
	return !pipe_occupancy(head, tail);
}
...
static inline bool pipe_full(unsigned int head, unsigned int tail,
			     unsigned int limit)
{
	return pipe_occupancy(head, tail) >= limit;
}
```

`head`/`tail` 就是 `in`/`out`，`head - tail` 就是 `kfifo_len`，而 `pipe_inode_info::ring_size` 的註解寫著「should be a power of 2」。**認得這個技巧之後，你在任何 ring buffer 程式碼裡看到 `head - tail` 與 `& (size - 1)`，都不必再推一次正確性**——這正是 [Ch 2](./02-program-comprehension-models.md) 說的 chunking：把一整段推理壓縮成一個你已經熟悉的單位。

## 對比與取捨

| 工具 | 解決的問題 | 成本 | 讀碼時的訊號 |
|---|---|---|---|
| bitmap | 大量布林狀態、快速找第一個 | 每 bit 1 bit；大小要另外記 | 搭配 list 陣列時，bitmap 是「哪些非空」的摘要 |
| `set_bit` | 並行修改同一 word | `lock` 前綴，cache line 爭用 | 沒有外部鎖 |
| `__set_bit` | 已有保護時的快速修改 | 無 | 有外部鎖，去找它 |
| per-cpu | 熱路徑上的共享寫入 | 記憶體 × CPU 數；讀總數要走訪所有 CPU、且是近似值 | 熱路徑、刻意避免共享與 lock |
| atomic 全域計數器 | 需要精確、即時的總數 | 每次寫都爭用 cache line | 讀比寫重要，或寫入不頻繁 |
| kfifo | SPSC 緩衝、無需 count 欄位 | 大小必須 2 的冪 | `in - out`、`& mask`；SPSC 時無鎖 |

## 踩雷集錦

1. **以為 `__set_bit` 是「內部版本」所以比較好**：它是非原子版本。在沒有鎖保護的情況下用它，會在高並行時遺失更新。讀到 `__` 版本要去找保護它的鎖。
2. **以為 `find_first_bit` 找不到時回傳 -1**：它回傳 `size`。用 `if (bit < 0)` 判斷找不到永遠不會成立。
3. **以為 per-cpu 讀到的總數是精確的**：`nr_processes()` 這類加總不拿鎖，讀的是各 CPU 當下的值。需要精確值的地方不能用 per-cpu 計數器，或要另外同步。
4. **在可搶佔的 context 用 `__this_cpu_*` 或手動 `this_cpu_ptr` 做多步操作**：task 可能在兩步之間被遷移到別的 CPU。要嘛用單步的 `this_cpu_*`，要嘛用 `get_cpu_var`/`preempt_disable` 包起來。
5. **把 ring buffer 大小設成非 2 的冪並沿用 `& mask` 或 `%`**：index 溢位的那一刻 slot 會跳號，表現為極偶發的資料錯亂（要寫滿 2^32 次才會觸發第一次）。
6. **以為 kfifo 在多生產者時也無鎖**：只有 SPSC 無鎖。多個寫者要鎖寫端。

## 進階：再往深一層

- **`percpu_counter` 與 `percpu_ref`**：`lib/percpu_counter.c` 是「per-cpu 本地累積、超過 batch 才併入全域」的計數器；`percpu_ref` 是 per-cpu 版本的 refcount，切換到 atomic 模式後才能歸零（[Ch 20](./20-refcount-and-lifetime.md) 的延伸）。eventpoll 用前者限制每個使用者的 watch 數量。
- **動態 per-cpu 配置**：`alloc_percpu(type)` 回傳 `type __percpu *`，存取方式與靜態的完全相同。讀到 `__percpu` 標註的 struct 成員，就知道它是動態 per-cpu。
- **`local_lock_t`**：`include/linux/local_lock.h`。在 `PREEMPT_RT` 下 `preempt_disable` 保護 per-cpu 資料的做法會出問題，`local_lock` 讓這個保護關係變得明確可檢查。Ch 25 的 kcov 程式碼裡 `struct kcov_percpu_data` 就有一個 `local_lock_t lock`。
- **`ring_buffer`（ftrace）與 `io_uring` 的 SQ/CQ ring**：都是 per-cpu 或共享記憶體上的 ring，用的是同一套 head/tail 自然溢位技巧，只是加上 memory-mapped 給 user-space 讀。
- **bitmap 的大小上限與 `cpumask_var_t`**：`NR_CPUS` 很大時（例如 8192），cpumask 有 1 KB，放在 stack 上太大。`CONFIG_CPUMASK_OFFSTACK` 讓 `cpumask_var_t` 變成指標、動態配置（`include/linux/cpumask_types.h:61-64`）。讀到 `alloc_cpumask_var` 就是這個原因。

## 動手練習

1. 在 v6.18 tree 找 `kernel/time/timer.c` 的 `pending_map`（`DECLARE_BITMAP(pending_map, WHEEL_SIZE)`）。它是哪個 list 陣列的摘要？找出 set 與 clear 它的地方，回答保護它的是哪把鎖、用的是 `__set_bit` 還是 `set_bit`。
2. 把 `kfifo_demo.c` 的 `data[8]` 與 `mask = 7` 改成 `data[6]`，`& f->mask` 改成 `% 6`，起點維持 `UINT_MAX - 2`。實際跑出資料錯亂的輸出，並解釋哪一個 byte 被寫到了錯的位置。
3. 用 `objdump -d --disassemble=<某函式> ~/code_reading/build/vmlinux | grep %gs:` 找一個用 `this_cpu_add` 而不是 `__this_cpu_add` 的地方，比較兩者產生的指令有沒有差別。在 x86 上為什麼可能一樣？在沒有 segment 定址的架構上呢？（提示：讀 `include/asm-generic/percpu.h` 的 generic 實作。）

## 本章重點整理

- bitmap 是無 header 的 `unsigned long` 陣列；`find_*_bit` 找不到回傳 `size`；常與 list 陣列搭配，作為「哪些非空」的摘要（RT 排程器的 delimiter bit 是一個跨檔案的不變式）。
- `set_bit` 原子（x86 帶 `lock`），`__set_bit` 非原子；雙底線代表「呼叫者已提供保護」。
- per-cpu 變數放在 `.data..percpu`，每 CPU 一份；x86 上 `this_cpu_inc` 是一條 `incq %gs:...`。看到 per-cpu = 作者在熱路徑上刻意避免共享。
- `__this_cpu_*` 與多步 per-cpu 操作需要呼叫者關閉 preemption；讀碼時要找出外層的保護。
- kfifo 的 `in`/`out` 只增不減、讓它自然溢位；`in - out` 永遠是正確長度，`& mask` 要求大小是 2 的冪。SPSC 時無鎖，靠 `smp_wmb` 保證資料先於 index 可見。

## 自我檢核

- [ ] 不看筆記，解釋為什麼 `in - out` 在 `in` 已經溢位、`out` 還沒溢位時仍然得到正確長度，以及為什麼這個技巧要求大小是 2 的冪。
- [ ] 有人在 code review 把 `__set_bit` 改成 `set_bit`「比較安全」，你會怎麼評論？反過來改呢？
- [ ] 看到 `__this_cpu_inc(x)`，你會往哪裡找它的前提條件？在 `copy_process` 裡前提是怎麼滿足的？
- [ ] 解釋為什麼 `nr_processes()` 可以不拿鎖，以及它付出了什麼代價。
- [ ] 想一個你工作中的全域計數器或統計值。把它改成 per-thread/per-cpu 會帶來什麼好處和什麼新問題？

## 延伸閱讀

### 官方文件

- **[this_cpu operations](https://docs.kernel.org/core-api/this_cpu_ops.html)**（v6.18 tree：`Documentation/core-api/this_cpu_ops.rst`）— Christoph Lameter, Pranith Kumar
  - **讀哪裡**：開頭兩段與「Inner working of this_cpu operations」（segment register 的說明），以及「Remote access to per cpu data」一節（跨 CPU 寫 per-cpu 資料的風險）。
  - **前提**：知道 x86 segment register 的概念即可。
- **[Circular Buffers](https://docs.kernel.org/core-api/circular-buffers.html)**（`Documentation/core-api/circular-buffers.rst`）— David Howells, Paul E. McKenney
  - **讀哪裡**：整篇。用 `CIRC_CNT`/`CIRC_SPACE` 巨集講 2 的冪 ring buffer，並詳細說明生產者/消費者各自需要哪些 memory barrier，是 kfifo `smp_wmb` 的完整理論背景。

### 書籍

- **《Is Parallel Programming Hard, And, If So, What Can You Do About It?》** — Paul E. McKenney（免費線上書，持續更新）
  - **讀哪幾章**：第 5 章「Counting」。從共享原子計數器一路講到 per-thread 計數器、統計計數器、近似上限計數器，正是本章 per-cpu 一節與 benchmark 的理論版本，而且有在真實多核機器上的量測。
  - **前提**：會 C 與基本的執行緒概念。
- **《What Every Programmer Should Know About Memory》** — Ulrich Drepper（2007，PDF 免費）
  - **讀哪幾章**：第 3.3.4 節（多處理器支援、cache line 的 MESI 狀態轉移）與第 6.4 節（多執行緒最佳化、false sharing）。解釋為什麼本章 benchmark 的 unpadded 版本也會慢。
  - **注意**：2007 年的硬體數據已過時，但機制與結論仍然適用。

### 部落格 / 技術文章

- **LWN 的 kfifo 與 per-cpu 相關報導** — LWN Kernel Index「kfifo」與「Per-CPU variables」項下
  - **這些文章說什麼**：2009 年 Stefani Seibold 重寫 kfifo API 的討論、per-cpu 配置器演進（Tejun Heo 的 percpu allocator）。
  - **讀哪裡**：從 [LWN Kernel Index](https://lwn.net/Kernel/Index/) 找對應項目，依時間順序讀。

Part 4 到這裡結束。你現在有一套詞彙：侵入式 list/hlist、手寫迴圈的 rbtree、要再比 key 的 hash bucket、不嵌入物件的 xarray/maple tree、bitmap 摘要、per-cpu 分片、自然溢位的 ring index。下一個練習要你把它們全部用在一個真實子系統上，畫出一張完整的資料結構地圖。

→ [練習 D：替一個子系統畫出完整的資料結構地圖](./practice-d-data-structure-map.md)
