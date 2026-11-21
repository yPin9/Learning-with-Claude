# Ch 31 — 讀 RCU 程式碼：reader、updater 與 grace period

> **目標**：讀到 `rcu_read_lock()`、`rcu_dereference()`、`rcu_assign_pointer()`、`call_rcu()`、`kfree_rcu()`、`list_for_each_entry_rcu()` 時，能在腦中還原出「誰在讀、誰在改、舊資料什麼時候被回收」；理解 grace period 的直覺模型與它在 kernel 裡的實際含義；能用三個固定問題拆解任何一段 RCU 程式碼，並在 v6.18 的 `inet_protos[]`、timerfd cancel list、fd table 擴張三個真實例子上實際操作。
> **鴻溝**：本章主要練**領域鴻溝**（RCU 是一種要先懂模型才讀得懂的同步機制）與**慣用法鴻溝**（`__rcu`、`rcu_dereference_*` 一大家族），順帶一點**脈絡鴻溝**（為什麼這裡沒有 lock 卻是安全的）。

> **環境**：Linux kernel v6.18 原始碼（本課 build 的 `.config` 為 `CONFIG_TREE_RCU=y`、`CONFIG_PREEMPT_RCU=y`、`CONFIG_PROVE_RCU` 未開）；user-space 範例以 gcc 11.4 + pthread + C11 atomics 在 WSL2 上執行。本章**不講 RCU 的內部實作**（tree RCU 怎麼偵測 grace period 結束），只講讀碼需要的模型。

## 為什麼需要這個？

讀完上一章，你習慣了「共享資料一定有 lock」。然後你打開網路收包路徑，看到這段：

```c
	ipprot = rcu_dereference(inet_protos[protocol]);
	if (ipprot) {
		...
		ret = INDIRECT_CALL_2(ipprot->handler, tcp_v4_rcv, udp_rcv,
				      skb);
```

（`net/ipv4/ip_input.c:195-206（v6.18）`）

每一個收進來的 IP 封包都會讀 `inet_protos[]` 這個全域陣列，而它可以在執行中被改（載入或卸載一個協定 module）。這裡沒有 spinlock、沒有 mutex。如果讀到一半，另一顆 CPU 把 `ipprot` 指向的 struct 釋放了呢？

這就是 RCU（Read-Copy-Update）在解的問題，而它的解法讓很多人第一次讀時覺得「這不可能是對的」。v6.18 tree 裡 `rcu_read_lock()` 出現 4613 次、`rcu_dereference(` 2209 次、`synchronize_rcu()` 631 次、`call_rcu(` 529 次、`kfree_rcu(` 662 次。在 VFS 路徑查找、網路、process 管理、module 系統裡，RCU 是讀端的預設工具。讀不懂 RCU，這些子系統的熱路徑就全部讀不懂。

### 在 RCU 之前

讀多寫少的資料，傳統做法是 reader-writer lock：讀者之間不互斥，寫者獨佔。問題在於「讀者之間不互斥」不等於「讀者之間沒有成本」——每個讀者進出都要原子地更新 lock 裡的讀者計數，這個計數所在的 cache line 會在所有 CPU 之間來回搬。CPU 數越多，讀者越多，這條 cache line 越熱，最後讀端的擴展性被它卡住。

RCU 的想法是讓**讀者完全不寫任何共享的東西**。讀者不通知任何人「我在讀」；代價轉嫁給寫者：寫者不能原地修改，要做一份新的，換上去，然後**等所有可能還在看舊版的讀者都離開**，才能回收舊版。這個機制在 Linux 2.5 開發期間進入 kernel（`Documentation/RCU/whatisRCU.rst` 開頭的說法），理論源頭是 Paul McKenney 與 John Slingwine 1998 年的論文。

## 先建立直覺

把共享資料想成一本參考書，讀者是圖書館裡的人，更新者是出版社。

```
時間 ─────────────────────────────────────────────────────────────▶

書架上的指標:   [第 1 版] ────────────┐ 換上第 2 版
                                     └──────▶ [第 2 版] ─────────────────
                                     ▲
                              rcu_assign_pointer()

讀者 A:   |=== 拿到第 1 版，讀 ===|
讀者 B:          |======= 拿到第 1 版，讀很久 =======|
讀者 C:                                  |== 拿到第 2 版 ==|
                                     │                   │
                                     │◀── grace period ─▶│ ← 所有「換版前就開始讀」的人都讀完了
                                                         ▼
                                                  回收第 1 版（kfree）
```

規則只有三條：

1. **出版社不改舊書**。要改內容，就印一本新版，然後一次把書架上的指標換成新版（這一步是原子的：讀者拿到的要嘛是舊版、要嘛是新版，不會拿到印一半的）。
2. **讀者拿到哪一版就讀哪一版**。讀者 A、B 在換版前拿書，讀的是第 1 版；讀者 C 在換版後拿書，讀的是第 2 版。兩種都是「正確的」——RCU 允許不同讀者在同一時刻看到不同版本。
3. **舊版要等到「換版那一刻已經在讀的人」全部讀完，才能回收**。這段等待叫 **grace period（寬限期）**。換版之後才開始讀的人（讀者 C）不用等，他們根本看不到舊版。

**這個比喻的限制**：真實的 RCU 不知道每個讀者「讀完了沒」，它是靠觀察每顆 CPU 是否經過了一個「不可能還在讀端臨界區裡」的時刻（quiescent state，例如一次 context switch）來推斷的。所以 grace period 只保證「至少」等到舊讀者讀完，實際上往往等得更久（毫秒等級）。另外，比喻裡「讀者讀很久」在 kernel 裡是不允許的：讀端臨界區必須短，而且在一般的 RCU 裡不能睡。

> 如果你對 `READ_ONCE`、`smp_store_release` 與「發布」（publish）的意思還不熟，先回看 [Ch 21](./21-read-once-and-barriers.md)。RCU 的兩個核心巨集就是建在它們上面。

## 玩具 RCU：在 user space 看它保護了什麼

先在 user space 寫一個極簡版本，讓「等 grace period」與「不等」的差別變成可以量測的東西。資料是一個 `struct config`，不變式是 `a == b`；四個讀者 thread 不停地讀並檢查不變式；更新者每次做一份新版換上去，然後把舊版塗成 `a=-1, b=-2` 再 free（模擬記憶體被回收後挪作他用）。

```c
/* toyrcu.c — 玩具版 RCU：讀者不拿鎖，更新者「發新版、等舊讀者讀完、再回收」
 * 用法：./toyrcu wait   （正確：等 grace period 再 free）
 *       ./toyrcu nowait （錯誤：換完指標立刻 free）
 * 編譯：gcc -O2 -pthread -o toyrcu toyrcu.c
 */
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define NREADERS 4
#define NUPDATES 20000

struct config { long a, b; };          /* 不變式：a == b */

static _Atomic(struct config *) gp;    /* 相當於 kernel 的 struct config __rcu *gp */
static atomic_ulong gp_seq = 1;        /* grace period 序號 */
static atomic_ulong reader_seq[NREADERS]; /* 0 = 不在讀端臨界區 */
static atomic_int stop;
static atomic_long bad[NREADERS];

static void rcu_read_lock(int id)   { atomic_store(&reader_seq[id], atomic_load(&gp_seq)); }
static void rcu_read_unlock(int id) { atomic_store(&reader_seq[id], 0); }

/* 等到「呼叫當下已在讀端臨界區裡的讀者」全部離開 */
static void synchronize_rcu(void)
{
	unsigned long target = atomic_fetch_add(&gp_seq, 1) + 1;
	for (int i = 0; i < NREADERS; i++) {
		for (;;) {
			unsigned long s = atomic_load(&reader_seq[i]);
			if (s == 0 || s >= target)   /* 不在讀、或是新的讀者 */
				break;
		}
	}
}

static void *reader(void *arg)
{
	int id = (int)(long)arg;
	while (!atomic_load(&stop)) {
		rcu_read_lock(id);
		struct config *c = atomic_load(&gp);   /* rcu_dereference() */
		long a = c->a, b = c->b;
		if (a != b)                            /* 讀到被回收、被汙染的記憶體 */
			atomic_fetch_add(&bad[id], 1);
		rcu_read_unlock(id);
	}
	return NULL;
}

int main(int argc, char **argv)
{
	int wait = argc > 1 && strcmp(argv[1], "wait") == 0;
	pthread_t t[NREADERS];
	struct config *c = malloc(sizeof(*c));
	c->a = c->b = 0;
	atomic_store(&gp, c);

	for (long i = 0; i < NREADERS; i++)
		pthread_create(&t[i], NULL, reader, (void *)i);

	for (long v = 1; v <= NUPDATES; v++) {
		struct config *n = malloc(sizeof(*n));
		n->a = n->b = v;                       /* 先把新版填好 */
		struct config *old = atomic_exchange(&gp, n); /* rcu_assign_pointer() */
		if (wait)
			synchronize_rcu();             /* 等舊讀者讀完 */
		old->a = -1; old->b = -2;              /* 模擬 free 後被別人重用 */
		free(old);
	}
	atomic_store(&stop, 1);
	long total = 0;
	for (int i = 0; i < NREADERS; i++) {
		pthread_join(t[i], NULL);
		total += atomic_load(&bad[i]);
	}
	printf("mode=%s updates=%d inconsistent_reads=%ld\n",
	       wait ? "wait" : "nowait", NUPDATES, total);
	free(atomic_load(&gp));
	return 0;
}
```

```
$ gcc -O2 -Wall -pthread -o toyrcu toyrcu.c && for i in 1 2 3; do ./toyrcu nowait; ./toyrcu wait; done
mode=nowait updates=20000 inconsistent_reads=243
mode=wait updates=20000 inconsistent_reads=0
mode=nowait updates=20000 inconsistent_reads=261
mode=wait updates=20000 inconsistent_reads=0
mode=nowait updates=20000 inconsistent_reads=245
mode=wait updates=20000 inconsistent_reads=0
```

不等 grace period，兩萬次更新裡有兩百多次讀者讀到被汙染的資料；等了，零次。用 AddressSanitizer 看得更清楚：

```
$ gcc -O1 -g -pthread -fsanitize=address -o toyrcu_asan toyrcu.c
$ ./toyrcu_asan nowait 2>&1 | grep -E 'ERROR|READ of|#0|#1|freed by'
==334695==ERROR: AddressSanitizer: heap-use-after-free on address 0x502000000350 at pc 0x61462dae833d bp 0x700097ffedc0 sp 0x700097ffedb0
READ of size 8 at 0x502000000350 thread T1
    #0 0x61462dae833c in reader /home/ypp/code_reading/scratch/31/toyrcu.c:45
    #1 0x70009b6949e2 in start_thread nptl/pthread_create.c:442
freed by thread T0 here:
    #0 0x70009bab4537 in __interceptor_free ../../../../src/libsanitizer/asan/asan_malloc_linux.cpp:127
    #1 0x61462dae8582 in main /home/ypp/code_reading/scratch/31/toyrcu.c:71
$ ./toyrcu_asan wait 2>&1 | tail -1
mode=wait updates=20000 inconsistent_reads=0
```

第 45 行是讀者的 `long a = c->a, b = c->b;`，第 71 行是更新者的 `free(old);`。**RCU 防的就是這個 use-after-free**：讀者手上的指標，在讀者用完之前被寫者釋放。（`nowait` 版本是刻意的未定義行為，在你的機器上數字會不同，甚至可能 crash。）

這個玩具和 kernel 的差別，正好說明 kernel RCU 的設計重點：

- 玩具的讀者**有寫共享記憶體**（`reader_seq[id]`），這正是 RCU 要避免的成本。kernel 的 tree RCU 在 `CONFIG_PREEMPT_RCU` 未開時，`rcu_read_lock()` 底層只是 `preempt_disable()`（`include/linux/rcupdate.h:91-94`），不碰任何共享變數；開了 `PREEMPT_RCU`（本課的 build 就是）則是遞增 `current` 這個 task 自己的巢狀計數（`kernel/rcu/tree_plugin.h:412-420`），也只寫 per-task 的私有資料。
- 玩具的 `synchronize_rcu` 在空轉。kernel 的 `synchronize_rcu()` 會睡，由 RCU 的背景 kthread 追蹤每顆 CPU 的 quiescent state。
- 玩具每次更新都等一次 grace period。kernel 鼓勵用 `call_rcu()`／`kfree_rcu()` 非同步回收，把許多回收請求合併到同一個 grace period。

## RCU 的 API：讀者三件、寫者三件

```
讀者端（reader）                          寫者端（updater）
─────────────────                         ───────────────────────────────
rcu_read_lock();                          new = kmalloc(...);  填好 *new
p = rcu_dereference(gp);  ← 取指標        old = rcu_dereference_protected(gp, lockdep_is_held(&lock));
... 使用 *p ...                           rcu_assign_pointer(gp, new);  ← 發布
rcu_read_unlock();                        synchronize_rcu(); kfree(old);   ← 同步等
                                          或 call_rcu(&old->rcu, cb);      ← 非同步
                                          或 kfree_rcu(old, rcu);          ← 非同步，最常見
```

### `rcu_dereference()`：讀者取指標

```c
#define __rcu_dereference_check(p, local, c, space) \
({ \
	/* Dependency order vs. p above. */ \
	typeof(*p) *local = (typeof(*p) *__force)READ_ONCE(p); \
	RCU_LOCKDEP_WARN(!(c), "suspicious rcu_dereference_check() usage"); \
	rcu_check_sparse(p, space); \
	((typeof(*p) __force __kernel *)(local)); \
})
...
#define rcu_dereference_check(p, c) \
	__rcu_dereference_check((p), __UNIQUE_ID(rcu), \
				(c) || rcu_read_lock_held(), __rcu)
...
#define rcu_dereference(p) rcu_dereference_check(p, 0)
```

（`include/linux/rcupdate.h:529-536, 679-681, 770（v6.18）`）

拆開來只有三件事：

1. `READ_ONCE(p)`：讀一次指標，不讓編譯器重讀或拆開讀。之後透過這個指標讀到的欄位，在依賴順序上保證看到的是「發布時已經填好的內容」。
2. `RCU_LOCKDEP_WARN(...)`：開 `CONFIG_PROVE_RCU` 時，如果你不在 `rcu_read_lock()` 臨界區裡（且條件 `c` 不成立），執行期印警告。
3. `rcu_check_sparse(p, __rcu)`：sparse 檢查 `p` 有 `__rcu` 標註，並把標註剝掉回傳普通指標。

### `rcu_assign_pointer()`：寫者發布

```c
#define rcu_assign_pointer(p, v)					      \
do {									      \
	uintptr_t _r_a_p__v = (uintptr_t)(v);				      \
	rcu_check_sparse(p, __rcu);					      \
									      \
	if (__builtin_constant_p(v) && (_r_a_p__v) == (uintptr_t)NULL)	      \
		WRITE_ONCE((p), (typeof(p))(_r_a_p__v));		      \
	else								      \
		smp_store_release(&p, RCU_INITIALIZER((typeof(p))_r_a_p__v)); \
} while (0)
```

（`include/linux/rcupdate.h:588-597（v6.18）`）

核心是 `smp_store_release`：保證「新版的內容寫完」發生在「指標換上去」之前，讀者只要拿到新指標，就一定看得到填好的內容。如果寫的是常數 NULL，沒有內容需要先寫完，就退化成較便宜的 `WRITE_ONCE`。這就是比喻裡「印好才上架」的機器層面意思。

### `__rcu`：型別上的記號

`struct net_protocol __rcu *inet_protos[MAX_INET_PROTOS]` 裡的 `__rcu` 在一般編譯時是空的，在 sparse 底下會把指標放進另一個「位址空間」，直接解參考會被 sparse 抱怨，必須經過 `rcu_dereference` 系列或 `rcu_assign_pointer`。[Ch 18](./18-annotations-and-sparse.md) 介紹過它。讀碼時，`__rcu` 是最好的入口：**搜一個 struct 定義裡的 `__rcu`，就找到了所有被 RCU 保護的指標**，接著問下面三個問題。

### 回收：同步或非同步

- `synchronize_rcu()`：睡到一個 grace period 結束才返回。呼叫者必須在可以睡的情境。
- `call_rcu(&obj->rcu_head, callback)`：登記一個 callback，grace period 結束後由 RCU 呼叫。立刻返回，可以在不能睡的地方用。`struct rcu_head` 要嵌在被回收的物件裡，callback 用 `container_of`（[Ch 16](./16-container-of.md)）找回物件。
- `kfree_rcu(obj, rcu_head 欄位名)`：最常見的特例，「grace period 後 kfree 它」，連 callback 都不用寫。

## 讀 RCU 程式碼的三個問題

拿到任何一段 RCU 程式碼，固定問這三題：

1. **讀端臨界區在哪？** 找 `rcu_read_lock()`／`rcu_read_unlock()`（或等價物：`_bh`、`_sched` 變體、持有寫者的 lock、或呼叫者保證）。臨界區裡透過 `rcu_dereference` 拿到的指標，**只在臨界區內有效**。
2. **誰是 updater？寫者之間怎麼互斥？** 找 `rcu_assign_pointer`、`list_add_rcu`／`list_del_rcu`、`cmpxchg` 這類發布點。RCU **不處理寫者之間的衝突**，寫者之間一定有另一套機制（spinlock、mutex、cmpxchg、或「只有單一寫者」的設計保證）。
3. **舊資料何時、由誰釋放？** 找 `synchronize_rcu`、`call_rcu`、`kfree_rcu`。釋放必須在「換掉指標（或從 list 移除）」**之後**，並且隔了一個 grace period。

下面在三個真實例子上照做，難度遞增。

## 例子一：`inet_protos[]`——最小的 RCU 陣列

```c
struct net_protocol __rcu *inet_protos[MAX_INET_PROTOS] __read_mostly;
EXPORT_SYMBOL(inet_protos);
...
int inet_add_protocol(const struct net_protocol *prot, unsigned char protocol)
{
	return !cmpxchg((const struct net_protocol **)&inet_protos[protocol],
			NULL, prot) ? 0 : -1;
}
...
int inet_del_protocol(const struct net_protocol *prot, unsigned char protocol)
{
	int ret;

	ret = (cmpxchg((const struct net_protocol **)&inet_protos[protocol],
		       prot, NULL) == prot) ? 0 : -1;

	synchronize_net();

	return ret;
}
```

（`net/ipv4/protocol.c:27-56（v6.18）`，節錄）

**問題一：讀端臨界區在哪？** 開頭引用的 `ip_protocol_deliver_rcu()` 用 `rcu_dereference(inet_protos[protocol])` 取指標，但它自己沒有 `rcu_read_lock()`。函式名的 `_rcu` 後綴是提示：**呼叫者負責**。往上一層：

```c
	rcu_read_lock();
	ip_protocol_deliver_rcu(net, skb, ip_hdr(skb)->protocol);
	rcu_read_unlock();
```

（`net/ipv4/ip_input.c:238-240（v6.18）`）

臨界區包住了整個協定分派，包括呼叫 `ipprot->handler`——所以 `tcp_v4_rcv` 整個執行期間，`ipprot` 指向的 `struct net_protocol` 都保證不會被釋放。

**問題二：誰是 updater？寫者怎麼互斥？** `inet_add_protocol`／`inet_del_protocol`。寫者之間沒有 lock，用的是 `cmpxchg`：只有在格子是 NULL 時才能放進去，只有在格子確實是自己時才能拿掉。`cmpxchg` 是完整的記憶體屏障，所以它同時扮演了 `rcu_assign_pointer` 的發布角色（這裡沒用 `rcu_assign_pointer` 巨集，型別轉換 `(const struct net_protocol **)` 就是為了繞過 `__rcu` 標註）。

**問題三：舊資料何時釋放？** `inet_del_protocol` 在清空格子之後呼叫 `synchronize_net()`：

```c
void synchronize_net(void)
{
	might_sleep();
	if (from_cleanup_net() || rtnl_is_locked())
		synchronize_rcu_expedited();
	else
		synchronize_rcu();
}
```

（`net/core/dev.c:12113-12120（v6.18）`）

它就是 `synchronize_rcu()`（在持有 RTNL 時改用較快、但對系統較吵的 expedited 版本）。注意這裡沒有 `kfree`：`struct net_protocol` 通常是 module 裡的 `static const` 物件，「釋放」指的是 module 被卸載、它的記憶體消失。`inet_del_protocol` 返回時，保證沒有任何 CPU 還在執行舊的 `handler`，module 才能安全地卸載。**RCU 保護的不只是 `kfree`，而是「指標指向的東西消失」這件事。**

## 例子二：timerfd 的 cancel list——`_rcu` list 變體

上一章分析過 timerfd 的鎖，留下一個尾巴：`cancel_list` 的讀者沒有拿鎖。現在補上。

```c
void timerfd_clock_was_set(void)
{
	ktime_t moffs = ktime_mono_to_real(0);
	struct timerfd_ctx *ctx;
	unsigned long flags;

	rcu_read_lock();
	list_for_each_entry_rcu(ctx, &cancel_list, clist) {
		if (!ctx->might_cancel)
			continue;
		spin_lock_irqsave(&ctx->wqh.lock, flags);
		if (ctx->moffs != moffs) {
			ctx->moffs = KTIME_MAX;
			ctx->ticks++;
			wake_up_locked_poll(&ctx->wqh, EPOLLIN);
		}
		spin_unlock_irqrestore(&ctx->wqh.lock, flags);
	}
	rcu_read_unlock();
}
```

（`fs/timerfd.c:95-114（v6.18）`）

**問題一**：讀端臨界區就是 `rcu_read_lock()` 到 `rcu_read_unlock()`，裡面走訪整個 list。臨界區裡拿了 spinlock 沒關係（spinlock 不會睡）；不能做的是睡眠（一般 RCU 下）。

**問題二**：寫者是 `timerfd_setup_cancel` 的 `list_add_rcu` 與 `__timerfd_remove_cancel` 的 `list_del_rcu`（程式碼見 [Ch 30](./30-reading-locking-code.md) 範例二），都在全域 `cancel_lock` 底下。**寫者之間用 spinlock 互斥，讀者用 RCU**——這是 kernel 裡最常見的 RCU 搭配。

**問題三**：

```c
static int timerfd_release(struct inode *inode, struct file *file)
{
	struct timerfd_ctx *ctx = file->private_data;

	timerfd_remove_cancel(ctx);

	if (isalarm(ctx))
		alarm_cancel(&ctx->t.alarm);
	else
		hrtimer_cancel(&ctx->t.tmr);
	kfree_rcu(ctx, rcu);
	return 0;
}
```

（`fs/timerfd.c:232-244（v6.18）`）

順序完全符合規則：先從 list 移除（`timerfd_remove_cancel` → `list_del_rcu`），再用 `kfree_rcu(ctx, rcu)` 延後釋放。第二個參數 `rcu` 是 `struct timerfd_ctx` 裡那個 `struct rcu_head rcu;` 欄位的名字——[Ch 30](./30-reading-locking-code.md) 讀 struct 時那個看起來沒人用的欄位，答案在這裡。

### `_rcu` list 變體為什麼存在

普通的 `list_del` 會把被移除節點的 `next`、`prev` 塗成毒值（`LIST_POISON1/2`），讓之後誤用的人立刻 crash。RCU 版本不能這樣做：

```c
/**
 * list_del_rcu - deletes entry from list without re-initialization
 ...
 * In particular, it means that we can not poison the forward
 * pointers that may still be used for walking the list.
 ...
 * Note that the caller is not permitted to immediately free
 * the newly deleted entry.  Instead, either synchronize_rcu()
 * or call_rcu() must be used to defer freeing until an RCU
 * grace period has elapsed.
 */
static inline void list_del_rcu(struct list_head *entry)
{
	__list_del_entry(entry);
	entry->prev = LIST_POISON2;
}
```

（`include/linux/rculist.h:152-180（v6.18）`，節錄）

一個讀者可能正好停在被刪除的節點上，下一步要沿著它的 `next` 走。所以 `next` 必須保持有效（指回原本 list 的下一個節點），只有讀者不會用到的 `prev` 才塗毒。`list_add_rcu` 則用 `rcu_assign_pointer` 發布新節點，保證讀者走到它時內容已經填好。`list_for_each_entry_rcu` 用 `list_entry_rcu`（底層是 `READ_ONCE`）取每個 `next`（`include/linux/rculist.h:446-450`）。

讀碼規則：**一條 list 只要有一個讀者用 `_rcu` 走訪，所有寫者都必須用 `_rcu` 版本修改，並延後釋放節點。** 看到同一條 list 混用 `list_del` 與 `list_for_each_entry_rcu`，十之八九是 bug。[Ch 23](./23-list-head-hlist.md) 的 `list_head` 與 `hlist` 都有對應的 `_rcu` 版本（`hlist_add_head_rcu`、`hlist_for_each_entry_rcu`……）。

## 例子三：fd table 擴張——RCU 保護「整張表的替換」

每個行程的 fd 表是 `struct files_struct`，真正的陣列在 `struct fdtable` 裡：

```c
struct fdtable {
	unsigned int max_fds;
	struct file __rcu **fd;      /* current fd array */
	unsigned long *close_on_exec;
	unsigned long *open_fds;
	unsigned long *full_fds_bits;
	struct rcu_head rcu;
};
...
struct files_struct {
  /*
   * read mostly part
   */
	atomic_t count;
	bool resize_in_progress;
	wait_queue_head_t resize_wait;

	struct fdtable __rcu *fdt;
	struct fdtable fdtab;
  /*
   * written part on a separate cache line in SMP
   */
	spinlock_t file_lock ____cacheline_aligned_in_smp;
	unsigned int next_fd;
	...
	struct file __rcu * fd_array[NR_OPEN_DEFAULT];
};
```

（`include/linux/fdtable.h:26-57（v6.18）`，節錄）

兩個 `__rcu` 指出兩層 RCU 保護：`files->fdt`（指向目前使用中的那張 `fdtable`）與 `fdt->fd[i]`（每個 fd 對應的 `struct file *`）。`struct rcu_head rcu` 告訴你 `fdtable` 會被延後釋放。註解把 struct 分成 "read mostly" 與 "written part"，並讓 `file_lock` 獨佔一條 cache line：讀者常碰的欄位與寫者常碰的欄位分開，避免讀者被寫者的 cache line 搬動拖慢。這是 RCU 設計思路延伸到資料佈局的例子。

行程打開的 fd 超過目前表的容量時，`expand_fdtable` 要換一張更大的表：

```c
static int expand_fdtable(struct files_struct *files, unsigned int nr)
	__releases(files->file_lock)
	__acquires(files->file_lock)
{
	struct fdtable *new_fdt, *cur_fdt;

	spin_unlock(&files->file_lock);
	new_fdt = alloc_fdtable(nr + 1);

	/* make sure all fd_install() have seen resize_in_progress
	 * or have finished their rcu_read_lock_sched() section.
	 */
	if (atomic_read(&files->count) > 1)
		synchronize_rcu();

	spin_lock(&files->file_lock);
	if (IS_ERR(new_fdt))
		return PTR_ERR(new_fdt);
	cur_fdt = files_fdtable(files);
	BUG_ON(nr < cur_fdt->max_fds);
	copy_fdtable(new_fdt, cur_fdt);
	rcu_assign_pointer(files->fdt, new_fdt);
	if (cur_fdt != &files->fdtab)
		call_rcu(&cur_fdt->rcu, free_fdtable_rcu);
	/* coupled with smp_wmb() in fd_install() */
	smp_wmb();
	return 0;
}
```

（`fs/file.c:252-279（v6.18）`）

這是教科書等級的 RCU 更新，名字裡的 Read-Copy-Update 三個字都出現了：

- **Copy**：`alloc_fdtable` 配一張新表，`copy_fdtable(new_fdt, cur_fdt)` 把舊表內容抄過去。
- **Update**：`rcu_assign_pointer(files->fdt, new_fdt)` 一次換上新表。換之前，讀者看到的是完整的舊表；換之後是完整的新表。
- **延後回收**：`call_rcu(&cur_fdt->rcu, free_fdtable_rcu)`。為什麼用非同步的 `call_rcu` 而不是 `synchronize_rcu()` + `kfree`？因為此刻持有 `file_lock` 這個 spinlock，不能睡。回收函式：

```c
static void __free_fdtable(struct fdtable *fdt)
{
	kvfree(fdt->fd);
	kvfree(fdt->open_fds);
	kfree(fdt);
}

static void free_fdtable_rcu(struct rcu_head *rcu)
{
	__free_fdtable(container_of(rcu, struct fdtable, rcu));
}
```

（`fs/file.c:103-113（v6.18）`）

`container_of` 從嵌入的 `rcu_head` 找回整個 `fdtable`。`if (cur_fdt != &files->fdtab)` 也值得注意：最初的那張表是**內嵌在 `files_struct` 裡的**（`struct fdtable fdtab;`），不是另外配置的，所以不能 free。

同一個函式裡還有一個 `synchronize_rcu()`，用途不同：它在**配置新表之後、換表之前**，而且只在 fd 表被多個 thread 共享時才做（`atomic_read(&files->count) > 1`）。註解說它在等所有 `fd_install()` 要嘛看到 `resize_in_progress` 旗標、要嘛已經結束它們的 `rcu_read_lock_sched()` 區段。看 `fd_install`：

```c
void fd_install(unsigned int fd, struct file *file)
{
	struct files_struct *files = current->files;
	struct fdtable *fdt;

	if (WARN_ON_ONCE(unlikely(file->f_mode & FMODE_BACKING)))
		return;

	rcu_read_lock_sched();

	if (unlikely(files->resize_in_progress)) {
		rcu_read_unlock_sched();
		spin_lock(&files->file_lock);
		fdt = files_fdtable(files);
		VFS_BUG_ON(rcu_access_pointer(fdt->fd[fd]) != NULL);
		rcu_assign_pointer(fdt->fd[fd], file);
		spin_unlock(&files->file_lock);
		return;
	}
	/* coupled with smp_rmb() in expand_fdtable() */
	smp_rmb();
	fdt = rcu_dereference_sched(files->fdt);
	VFS_BUG_ON(rcu_access_pointer(fdt->fd[fd]) != NULL);
	rcu_assign_pointer(fdt->fd[fd], file);
	rcu_read_unlock_sched();
}
```

（`fs/file.c:652-677（v6.18）`）

這裡 RCU 被用在一個比較少見的方向：**讀端臨界區裡在寫**。`fd_install` 在 RCU 讀端拿到目前的表，然後往裡面寫一個 `struct file *`。危險是：如果它寫進的是**即將被換掉的舊表**，而 `copy_fdtable` 已經抄完了，這筆寫入就遺失了。所以擴張的一方先設 `resize_in_progress`（在 `expand_files` 裡，`fs/file.c:314`），再 `synchronize_rcu()` 等所有「在設旗標之前就進入快速路徑」的 `fd_install` 做完；之後進來的 `fd_install` 都會看到旗標，改走拿 `file_lock` 的慢速路徑。**grace period 在這裡被當成一道「等所有舊的快速路徑排空」的柵欄**——同一個原語，比「延後 free」更進階的用法。

（註解寫的是 `rcu_read_lock_sched()`，而等待用的是 `synchronize_rcu()`：自 v4.20 起 RCU 的幾種 flavor 已合併（例如 commit `45975c7d21a1` "rcu: Define RCU-sched API in terms of RCU for Tree RCU PREEMPT builds"），`synchronize_rcu()` 也會等 `_sched` 讀端臨界區。這屬於 RCU 內部演進的細節，讀碼時知道兩者在今天的 kernel 裡可以互相等待即可。）

### 讀者這一側：`files_fdtable` 與 `_check` 變體

```c
#define rcu_dereference_check_fdtable(files, fdtfd) \
	rcu_dereference_check((fdtfd), lockdep_is_held(&(files)->file_lock))

#define files_fdtable(files) \
	rcu_dereference_check_fdtable((files), (files)->fdt)
```

（`include/linux/fdtable.h:63-67（v6.18）`）

`files_fdtable()` 取 `files->fdt` 時，允許兩種合法情境：在 RCU 讀端臨界區裡，**或**持有 `file_lock`。持有寫者的 lock 時，表不可能被換掉，所以不需要 `rcu_read_lock()`。這個「`rcu_dereference_check(p, lockdep_is_held(&lock))`」是讀碼時非常有用的線索：**第二個參數直接告訴你寫者用的是哪把 lock**——把 Ch 30「這把 lock 保護什麼」的問題反過來回答了。

真正的 fd 查找讀者是 `__fget_files_rcu`（`fs/file.c:989-1063`），它比較長，這裡只看骨架：在 `rcu_read_lock()` 底下用 `rcu_dereference_raw` 取表和 `struct file *`，然後 `file_ref_get()` 嘗試取得參考計數，成功後**再讀一次**表格項目與 `files->fdt`，確認沒變才回傳；變了就 `fput` 並重來。

為什麼拿到指標、加了參考計數還要重新檢查？這揭露了 RCU 保證的**邊界**：RCU 只保證「讀端臨界區裡拿到的指標，指向的記憶體不會被釋放回系統」。`struct file` 的 slab 是 `SLAB_TYPESAFE_BY_RCU`（`fs/file_table.c:597`），意思是記憶體在 grace period 內不會變成別種物件，但**可以被重新配置成另一個 `struct file`**。所以你手上的指標仍然指向一個合法的 `struct file`，但可能已經不是你要的那一個了。`file_table.c:195-197` 的註解直接說了：使用 fget-rcu 模式的人必須能處理「多餘的參考計數遞增」。

讀碼結論：**RCU 保護「記憶體還在」，不保護「物件還是原來那個」或「物件還活著」**。後兩者要配合 refcount 與重新檢查。[Ch 20](./20-refcount-and-lifetime.md) 講過 refcount 的 `get`/`put` 配對；RCU + refcount + 重新檢查，是 kernel 查找路徑上最常見的三件套。

## `rcu_dereference` 家族速查

| 巨集 | 用在哪裡 | 讀碼時的訊息 |
|---|---|---|
| `rcu_dereference(p)` | `rcu_read_lock()` 臨界區內 | 標準讀者 |
| `rcu_dereference_check(p, c)` | 臨界區內，**或**條件 `c` 成立（通常是持有某把 lock） | `c` 告訴你寫者的 lock |
| `rcu_dereference_protected(p, c)` | 只有寫者、持有 lock 時 | 這裡是 updater，不會和其他寫者並行；省掉 `READ_ONCE` |
| `rcu_dereference_raw(p)` | 作者自己保證安全，關掉 lockdep 檢查 | 要看周圍的註解理解為什麼安全（`__fget_files_rcu`） |
| `rcu_dereference_bh(p)` / `_sched(p)` | 搭配 `rcu_read_lock_bh()` / `_sched()` | 讀端用的是關 softirq / 關搶占的變體 |
| `rcu_access_pointer(p)` | 只比較指標值、不解參考（例如判斷是否為 NULL） | 這裡不會讀指標指向的內容 |

`fd_install` 裡的 `VFS_BUG_ON(rcu_access_pointer(fdt->fd[fd]) != NULL)` 是 `rcu_access_pointer` 的典型用法：只想知道格子是不是空的，不讀 `struct file` 的內容。

## 對比與取捨

| 機制 | 讀者成本 | 讀者會擋寫者嗎 | 寫者成本 | 讀者看到的資料 | 適用情境 |
|---|---|---|---|---|---|
| spinlock / mutex | 一次原子操作 + cache line 競爭 | 會 | 低 | 永遠最新 | 讀寫比例均衡 |
| rwlock / rwsem | 原子更新讀者計數（cache line 會被搬動） | 會 | 低 | 永遠最新 | 讀多寫少、讀端臨界區長 |
| seqcount | 兩次讀序號，偶爾重試 | 不會（讀者重試） | 低 | 一致的快照 | 資料小、可以安全重讀 |
| RCU | 接近零（不寫共享記憶體） | 不會 | 高（複製 + 等 grace period） | 舊版或新版，都是完整一致的 | 讀遠多於寫、能容忍短暫讀到舊版 |

RCU 的最後一欄是它的代價：**讀者可能在新版發布後，仍然看到舊版一小段時間**。對路由表、協定表、fd 表這類資料沒問題；對「改完必須立刻讓所有人看到」的資料就不適用。另外 RCU 不適合寫很頻繁的資料：每次寫都要複製，舊版又要等 grace period 才釋放，記憶體用量可能堆積。

## 踩雷集錦

1. **「`rcu_read_lock()` 是一把鎖，它會擋住寫者」**：不會。它不阻止任何人修改指標，只阻止**舊資料被釋放**。在讀端臨界區裡讀兩次 `rcu_dereference(gp)`，可能拿到兩個不同的版本。需要前後一致，就只取一次指標，存在區域變數裡用。
2. **「離開 `rcu_read_unlock()` 之後，指標還可以繼續用」**：不行。臨界區結束後，grace period 可能隨時結束、舊版隨時被 free。要在臨界區外使用，必須在裡面取得參考計數（`__fget_files_rcu` 的做法）。
3. **「RCU 保護了物件的內容，所以讀到的欄位一定是對的」**：RCU 保證記憶體還在；在 `SLAB_TYPESAFE_BY_RCU` 的 slab 上，連「還是同一個物件」都不保證。看到 `rcu_dereference` 後緊接著 refcount + 重新檢查，就是在補這個缺口。
4. **「寫者之間不用加鎖，因為有 RCU」**：RCU 只處理讀者與寫者之間的並行。兩個寫者同時 `list_add_rcu` 會把 list 弄壞。每段 RCU 程式碼都要找到寫者之間的互斥機制（timerfd 的 `cancel_lock`、`inet_protos` 的 `cmpxchg`、fd table 的 `file_lock`）。
5. **「`list_del_rcu` 之後節點就可以 free 了」**：要等一個 grace period（`kfree_rcu`、`call_rcu`、或 `synchronize_rcu` 之後再 free）。直接 free 就是本章玩具程式的 `nowait` 模式。
6. **「在讀端臨界區裡可以睡」**：一般 RCU 不行（`CONFIG_PREEMPT_RCU` 允許被搶占，但仍不允許主動睡眠）。需要在讀端睡眠的場合，kernel 用 SRCU（Sleepable RCU），API 是 `srcu_read_lock(&ss)`，讀碼時看到 `srcu_` 前綴就知道讀端可能很長。

## 進階：再往深一層

- **SRCU 與 Tasks RCU**：SRCU 的讀端可以睡、每個使用者有自己的 `srcu_struct`；Tasks RCU 用於 ftrace、BPF trampoline 這類「程式碼本身要被替換」的場合。`Documentation/RCU/whatisRCU.rst` 第 8 節的完整 API 列表把各種 flavor 並排列出。
- **`CONFIG_PROVE_RCU`**：開了它，`rcu_dereference` 在不合法的情境會印出 "suspicious rcu_dereference_check() usage"。本課 build 沒開，**這類警告本章未實測**；`Documentation/RCU/lockdep-splat.rst` 有真實的範例輸出與解讀。在自己的測試 kernel 上開它，是驗證你對某段 RCU 程式碼理解的最快方法。
- **`hlist_nulls` 與 `SLAB_TYPESAFE_BY_RCU`**：網路堆疊的 socket 查找把例子三的「重新檢查」推到極致，用 nulls 標記偵測「走到一半，節點被搬到別條鏈上」。`Documentation/RCU/rculist_nulls.rst` 說明它的原理。
- **RCU 怎麼知道 grace period 結束了**：tree RCU 把 CPU 組成樹狀結構回報 quiescent state，`Documentation/RCU/Design/` 底下有 Paul McKenney 的設計文件。這是領域知識，讀「用 RCU 的程式碼」不需要它，讀「RCU 本身的程式碼」才需要。
- **面試題**：「RCU 和讀寫鎖差在哪？」本章的對比表與三個問題，加上「讀者不寫共享記憶體」這一句核心，就是一個能接受追問的答案。

## 動手練習

1. 對 `struct files_struct` 與 `struct fdtable` 做一次「三個問題」：把 `__fget_files_rcu`（讀者）、`expand_fdtable`／`fd_install`（寫者）、`free_fdtable_rcu`（回收）各自標在一張圖上。
2. 在 `net/core/dev.c` 裡找 `ptype_all` 或 `ptype_base` 的讀者與寫者，回答三個問題。寫者之間用什麼互斥？
3. 修改 `toyrcu.c`：讓讀者在讀端臨界區裡讀兩次 `gp`，記錄兩次指標不同的次數。這驗證了踩雷第 1 條。
4. 在 `toyrcu.c` 加一個 `call_rcu` 風格的延後回收：更新者把舊版放進一個待回收佇列，每累積 100 個才做一次 `synchronize_rcu()` 並一起 free。比較兩種模式跑完 20000 次更新的時間。

## 本章重點整理

- RCU：寫者不改舊版，做新版、原子換上（`rcu_assign_pointer`），等一個 grace period 讓舊讀者讀完，再回收舊版（`synchronize_rcu`／`call_rcu`／`kfree_rcu`）。讀者只用 `rcu_read_lock()` + `rcu_dereference()`，不寫任何共享記憶體。
- 讀 RCU 程式碼的三個問題：讀端臨界區在哪、誰是 updater 以及寫者之間怎麼互斥、舊資料何時由誰釋放。
- `__rcu` 標出所有受 RCU 保護的指標；`rcu_dereference_check(p, lockdep_is_held(&l))` 的第二個參數告訴你寫者的 lock。
- RCU 保護「記憶體還在」，不保護「物件還是原來那個」；查找路徑上常見 RCU + refcount + 重新檢查三件套。一條 list 只要有 `_rcu` 讀者，所有寫者都要用 `_rcu` 版本並延後釋放。

## 自我檢核

- [ ] 不看筆記，用「出版社與讀者」的比喻解釋 grace period，並說出這個比喻在哪裡不準確。
- [ ] 拿到一段你沒看過的 RCU 程式碼，能在五分鐘內回答三個問題，並指出寫者之間的互斥機制嗎？
- [ ] 能解釋 `expand_fdtable` 裡兩個 RCU 操作（`synchronize_rcu` 與 `call_rcu`）各自在保護什麼，以及為什麼一個同步、一個非同步嗎？
- [ ] 有人說「我在 `rcu_read_lock()` 底下拿到 `struct file *`，所以它一定是我要的那個 file」，你能用 `SLAB_TYPESAFE_BY_RCU` 解釋他錯在哪嗎？
- [ ] 如果面試官問「為什麼 `list_del_rcu` 不把 `next` 塗毒」，你能畫圖回答嗎？

## 延伸閱讀

### 官方文件

- **Documentation/RCU/whatisRCU.rst**（[docs.kernel.org/RCU/whatisRCU.html](https://docs.kernel.org/RCU/whatisRCU.html)）
  - **讀哪裡**：第 1 節「RCU OVERVIEW」、第 2 節「WHAT IS RCU'S CORE API?」（本章的 API 就是這五個核心函式）、第 3 節的範例；第 5 節「WHAT ARE SOME SIMPLE IMPLEMENTATIONS OF RCU?」有兩個玩具實作，和本章的 `toyrcu.c` 互相對照。
  - **能學到什麼**：官方的 RCU 入門，作者就是 Paul McKenney。文件開頭列出 LWN「What is RCU」系列與歷年 API 總表的連結。
  - **前提**：本章的直覺模型。
- **Documentation/RCU/listRCU.rst** 與 **Documentation/RCU/rcu_dereference.rst**
  - **讀哪裡**：listRCU 的「Example 1: Read-mostly list: Deferred Destruction」；rcu_dereference.rst 整篇（講編譯器最佳化可能怎麼破壞 `rcu_dereference` 回傳的依賴鏈）。
  - **能學到什麼**：前者用一系列真實模式（Example 1 是走訪所有行程的 `task_struct::tasks` list）把 `_rcu` list 的用法走一遍；後者解釋「拿到指標後不要拿它做奇怪的運算」背後的原因。
- **Documentation/RCU/checklist.rst**
  - **讀哪裡**：整份清單。它是寫給「寫 RCU 程式碼的人」的審查清單，反過來就是讀者的檢查表：每一條都是一個可以在 code review 時問的問題。

### 技術文章

- **[What is RCU, Fundamentally?](https://lwn.net/Articles/262464/)** — Paul McKenney、Jonathan Walpole（LWN，2007）
  - **這篇說什麼**：用三個基本機制（發布-訂閱、等待既有讀者、維護多個版本）解釋 RCU，附 Quick Quiz。
  - **讀哪裡**：整篇；重點在「Wait For Pre-Existing RCU Readers to Complete」一節的時序圖。
  - **為什麼值得讀**：這是 `whatisRCU.rst` 推薦的第一篇；同系列的 Part 2（Usage）與 Part 3（API）連結在 `whatisRCU.rst` 開頭。

### 論文

- **Read-Copy Update: Using Execution History to Solve Concurrency Problems** — Paul E. McKenney、John D. Slingwine，Parallel and Distributed Computing and Systems（PDCS，1998）
  - **核心貢獻**：提出 RCU 的原始形式——利用「執行歷史」（每顆 CPU 是否經過 quiescent state）判斷舊資料何時可以安全回收，讓讀者完全不用同步。
  - **讀哪裡**：前兩節的問題陳述與基本機制；效能數據是 1990 年代 Sequent 機器上的，可以略讀。
  - **和本章的關聯**：本章「讀者不寫共享記憶體、寫者等 grace period」的模型就是這篇論文的核心。

### 書籍

- **《Is Parallel Programming Hard, And, If So, What Can You Do About It?》** — Paul E. McKenney（免費線上書，[kernel.org/pub/linux/kernel/people/paulmck/perfbook/perfbook.html](https://kernel.org/pub/linux/kernel/people/paulmck/perfbook/perfbook.html)）
  - **讀哪幾章**：「Deferred Processing」一章的 RCU 小節（從 reference counting、hazard pointer、sequence lock 一路比較到 RCU）。
  - **定位**：讀完 Ch 30–31 後最好的下一步；它把 RCU 放回「延遲處理」這個更大的設計空間裡，解釋什麼時候 RCU 是對的工具、什麼時候不是。持續更新，以最新版為準。

RCU 的 `call_rcu` callback、timerfd 的 hrtimer callback、`synchronize_rcu` 背後的 kthread，都是「不在呼叫者的控制流裡執行」的程式碼。下一章處理這種非同步控制流：中斷、softirq、workqueue 與 timer。

→ [Ch 32 非同步控制流：中斷、softirq、workqueue、timer](./32-async-irq-softirq-workqueue.md)
