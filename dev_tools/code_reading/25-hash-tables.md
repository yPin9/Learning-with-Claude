# Ch 25 — hash table：hashtable.h 與 rhashtable

> **目標**：在 kernel 裡認出 hash table（不管它是用 `hashtable.h`、手刻的 `hlist_head[]`，還是 `rhashtable`），找出它的 key、hash 函式、bucket 數量與保護它的鎖；看懂 `hash_32()` 那個 `0x61C88647` 從哪裡來；並且養成一個反射動作——**看到 `hash_for_each_possible` 就去檢查迴圈內有沒有再比對 key**。

> **鴻溝**：本章主要練**慣用法鴻溝**（`hash_for_each_possible` 的真實語意）與**脈絡鴻溝**（magic number 的來歷、為什麼有兩套 hash table）。

> **環境**：Linux kernel v6.18 原始碼；user-space 範例 gcc 11.4、WSL Ubuntu 22.04 x86_64。

## 為什麼需要這個？

Ch 24 的 rbtree 給你 O(log n) 的有序查找。但很多查找根本不需要順序：「這個 work item 現在是哪個 worker 在跑？」「這個 IPC key 對應哪個 semaphore set？」「這個 inode 號碼在 inode cache 裡嗎？」這些都只是「給 key、拿物件」，hash table 平均 O(1) 更合適。

kernel 裡 hash table 的歷史很能說明 Spinellis 講的「同一個概念在大型專案裡會有很多份實作」。在 2012 年之前，每個子系統都自己宣告一個 `struct hlist_head foo_hash[N]`、自己寫 hash 函式、自己寫查找迴圈。Sasha Levin 在 2012 年送進 `include/linux/hashtable.h`（commit `d9b482c8ba19`，v3.7），commit message 只有一句目的：

```
This hashtable implementation is using hlist buckets to provide a simple
hashtable to prevent it from getting reimplemented all over the kernel.
```

標題自稱「a small and naive hashtable」：固定大小、不會自動擴張、沒有內建鎖。兩年後 Thomas Graf 送進 `lib/rhashtable.c`（commit `7e1e77636e36`，v3.17），是能自動擴縮、查找無鎖的版本。所以今天讀 kernel，你會同時碰到三種：

1. **手刻的 `struct hlist_head xxx[1 << N]`**——2012 年以前的程式碼，或作者想要客製化
2. **`hashtable.h`**——`DEFINE_HASHTABLE`、`hash_add`、`hash_for_each_possible`
3. **`rhashtable`**——`struct rhashtable_params`、`rhashtable_lookup_fast`

三者讀法不同，但底下的資料形狀高度相似。

## 先建立直覺

想像郵局的信件分類櫃：一排 8 格，每封信依收件人姓名算出一個格子號碼丟進去。找信時算出格子號碼，**打開那一格，裡面可能有好幾封信，要一封一封看收件人**。

```
 key = 9 ──hash_32(9, 3)──▶ 3
                             │
  bucket[0] ─▶ [42]          ▼
  bucket[1] ─▶ [3]       bucket[3] ─▶ [17] ─▶ [9] ─▶ [1] ─▶ NULL
  bucket[2]                           ▲
  ...                                 └── 同一格裡的其他信：key 不同，只是 hash 撞了
  bucket[6] ─▶ [2]
  bucket[7]
```

格子號碼只是**縮小搜尋範圍**，不是答案。這個直覺決定了本章最重要的讀碼規則。

## hashtable.h：一層很薄的巨集

`hashtable.h` 本身幾乎沒有邏輯，全部是把 Ch 23 的 hlist 包一層（`include/linux/hashtable.h:16-32（v6.18）`）：

```c
#define DEFINE_HASHTABLE(name, bits)						\
	struct hlist_head name[1 << (bits)] =					\
			{ [0 ... ((1 << (bits)) - 1)] = HLIST_HEAD_INIT }
...
#define DECLARE_HASHTABLE(name, bits)                                   	\
	struct hlist_head name[1 << (bits)]

#define HASH_SIZE(name) (ARRAY_SIZE(name))
#define HASH_BITS(name) ilog2(HASH_SIZE(name))

/* Use hash_32 when possible to allow for fast 32bit hashing in 64bit kernels. */
#define hash_min(val, bits)							\
	(sizeof(val) <= 4 ? hash_32(val, bits) : hash_long(val, bits))
```

讀這幾行要注意：

- **hash table 就是一個 `hlist_head` 陣列**。`DEFINE_HASHTABLE(foo, 6)` 展開成 `struct hlist_head foo[64]`。所以 grep `DEFINE_HASHTABLE` 找不到時，也要 grep `struct hlist_head \w+\[`。
- **`[0 ... N] = ...`** 是 GNU C 的 designated range initializer（[Ch 15](./15-kernel-c-gnu-extensions.md)），把每個 bucket 初始化成空。
- **大小一定是 2 的冪**，參數給的是 bits 而不是 bucket 數。`HASH_BITS` 用 `ilog2` 反推，而且因為 `name` 是陣列，`ARRAY_SIZE` 在編譯期就算得出來——**這也是為什麼 `hash_add` 的第一個參數必須是陣列本身，不能是指標**。傳一個 `struct hlist_head *` 進去，`ARRAY_SIZE` 會算錯（kernel 的 `ARRAY_SIZE` 對指標會觸發編譯錯誤，算是保護）。

插入與查找（`include/linux/hashtable.h:60-61、165-166（v6.18）`）：

```c
#define hash_add(hashtable, node, key)						\
	hlist_add_head(node, &hashtable[hash_min(key, HASH_BITS(hashtable))])
...
#define hash_for_each_possible(name, obj, member, key)			\
	hlist_for_each_entry(obj, &name[hash_min(key, HASH_BITS(name))], member)
```

`hash_add` 就是「算出 bucket，`hlist_add_head` 進去」。注意它**不檢查重複**——同一個 key 加兩次，bucket 裡就有兩個。

## 本章最重要的一條讀碼規則

看 `hash_for_each_possible` 的 kernel-doc（`include/linux/hashtable.h:157-164（v6.18）`）：

```c
/**
 * hash_for_each_possible - iterate over all possible objects hashing to the
 * same bucket
 * @name: hashtable to iterate
 * @obj: the type * to use as a loop cursor for each entry
 * @member: the name of the hlist_node within the struct
 * @key: the key of the objects to iterate over
 */
```

關鍵字是 **possible**：它走訪的是「hash 到同一個 bucket 的所有物件」，**不是**「key 等於這個值的物件」。展開後它就是一個 `hlist_for_each_entry`，迴圈不知道 key 是什麼，也沒辦法知道——`hash_add` 根本沒把 key 存進任何地方，key 是物件自己的某個欄位，只有使用者知道是哪個。

所以**每一個正確的 `hash_for_each_possible` 迴圈，本體第一件事一定是比對 key**。看 workqueue 的例子（`kernel/workqueue.c:1115-1127（v6.18）`）：

```c
static struct worker *find_worker_executing_work(struct worker_pool *pool,
						 struct work_struct *work)
{
	struct worker *worker;

	hash_for_each_possible(pool->busy_hash, worker, hentry,
			       (unsigned long)work)
		if (worker->current_work == work &&
		    worker->current_func == work->func)
			return worker;

	return NULL;
}
```

和 kcov（`kernel/kcov.c:106-116（v6.18）`）：

```c
/* Must be called with kcov_remote_lock locked. */
static struct kcov_remote *kcov_remote_find(u64 handle)
{
	struct kcov_remote *remote;

	hash_for_each_possible(kcov_remote_map, remote, hnode, handle) {
		if (remote->handle == handle)
			return remote;
	}
	return NULL;
}
```

這兩段同時告訴你**這張表的 key 是什麼**：workqueue 的 `busy_hash` 用 `work` 指標當 hash key，比對時看 `current_work`（還多比一個 `current_func`，函式上方的長註解解釋了原因：work item 被 free 後同一個位址可能被重用，只比位址會誤判）；kcov 的 key 是 `handle`。**從 `hash_for_each_possible` 的第四個參數和迴圈內的 `if`，你可以反推出這張 hash table 的 key 和「相等」的定義**——這是讀陌生 hash table 最快的入口。

反過來，如果你看到一個 `hash_for_each_possible` 迴圈本體沒有比對 key，就直接 `return` 第一個元素，那幾乎一定是 bug（除非 key 空間小到保證不碰撞，而那種情況通常不會用 hash table）。

## 實際跑：碰撞、key 比對與 hlist_del

把 `hashtable.h` 用到的部分移植到 user-space，刻意用 8 個 bucket 讓碰撞發生：

```c
/* hashdemo.c */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <stddef.h>
/* --- 從 v6.18 include/linux/{types.h,list.h,hash.h,hashtable.h} 移植的子集 --- */
#define container_of(ptr, type, member) ((type *)((char *)(ptr) - offsetof(type, member)))
struct hlist_head { struct hlist_node *first; };
struct hlist_node { struct hlist_node *next, **pprev; };
static void hlist_add_head(struct hlist_node *n, struct hlist_head *h)
{
	struct hlist_node *first = h->first;
	n->next = first;
	if (first) first->pprev = &n->next;
	h->first = n;
	n->pprev = &h->first;
}
static void hlist_del(struct hlist_node *n)   /* 不需要知道 head 是誰 */
{
	struct hlist_node *next = n->next, **pprev = n->pprev;
	*pprev = next;
	if (next) next->pprev = pprev;
}
#define hlist_entry_safe(ptr, type, member) \
	({ typeof(ptr) ____ptr = (ptr); ____ptr ? container_of(____ptr, type, member) : NULL; })
#define hlist_for_each_entry(pos, head, member) \
	for (pos = hlist_entry_safe((head)->first, typeof(*(pos)), member); pos; \
	     pos = hlist_entry_safe((pos)->member.next, typeof(*(pos)), member))
#define GOLDEN_RATIO_32 0x61C88647
static inline uint32_t hash_32(uint32_t val, unsigned int bits) { return val * GOLDEN_RATIO_32 >> (32 - bits); }
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define DEFINE_HASHTABLE(name, bits) \
	struct hlist_head name[1 << (bits)] = { [0 ... ((1 << (bits)) - 1)] = { NULL } }
#define HASH_BITS(name) (__builtin_ctz(ARRAY_SIZE(name)))
#define hash_add(ht, node, key) hlist_add_head(node, &ht[hash_32(key, HASH_BITS(ht))])
#define hash_for_each_possible(ht, obj, member, key) \
	hlist_for_each_entry(obj, &ht[hash_32(key, HASH_BITS(ht))], member)
/* ------------------------------------------------------------------------ */

struct conn { uint32_t id; const char *peer; struct hlist_node node; };
static DEFINE_HASHTABLE(conns, 3);                 /* 8 個 bucket */

static struct conn *find_right(uint32_t id)
{
	struct conn *c;
	hash_for_each_possible(conns, c, node, id)
		if (c->id == id)                           /* 一定要再比 key */
			return c;
	return NULL;
}
static struct conn *find_wrong(uint32_t id)
{
	struct conn *c;
	hash_for_each_possible(conns, c, node, id)
		return c;                                  /* bucket 第一個就當答案 */
	return NULL;
}

int main(void)
{
	/* (1) 為什麼取高位：key 是 64 對齊的位址（低 6 bit 全 0） */
	unsigned lo[8] = {0}, hi[8] = {0};
	for (uint32_t i = 0; i < 1024; i++) {
		uint32_t addr = 0x10000 + i * 64;
		lo[addr & 7]++;                            /* 天真的 mod 8 */
		hi[hash_32(addr, 3)]++;                    /* kernel hash_32 */
	}
	printf("addr & 7    :"); for (int b = 0; b < 8; b++) printf(" %4u", lo[b]); printf("\n");
	printf("hash_32(.,3):"); for (int b = 0; b < 8; b++) printf(" %4u", hi[b]); printf("\n");
	uint32_t low_bits[8] = {0};
	for (uint32_t i = 0; i < 1024; i++)
		low_bits[(0x10000 + i * 64) * GOLDEN_RATIO_32 & 7]++;
	printf("(x*G) & 7   :"); for (int b = 0; b < 8; b++) printf(" %4u", low_bits[b]); printf("\n");

	/* (2) 碰撞與 key 比對 */
	static struct conn cs[] = { {1, "10.0.0.1"}, {2, "10.0.0.2"}, {3, "10.0.0.3"},
	                            {9, "10.0.0.9"}, {17, "10.0.0.17"}, {42, "10.0.0.42"} };
	for (size_t i = 0; i < ARRAY_SIZE(cs); i++) {
		hash_add(conns, &cs[i].node, cs[i].id);
		printf("id=%-2u -> bucket %u\n", cs[i].id, hash_32(cs[i].id, 3));
	}
	uint32_t q[] = { 9, 17, 5 };
	for (int i = 0; i < 3; i++) {
		struct conn *r = find_right(q[i]), *w = find_wrong(q[i]);
		printf("lookup %-2u: right=%s  wrong=%s\n", q[i],
		       r ? r->peer : "NULL", w ? w->peer : "NULL");
	}
	/* (3) hlist_del 不需要 head：刪掉 bucket 的第一個元素 */
	hlist_del(&cs[5].node);
	printf("after del 42: find(42)=%s\n", find_right(42) ? "found" : "NULL");
	return 0;
}
```

```
$ gcc -O1 -Wall -o hashdemo hashdemo.c && ./hashdemo
addr & 7    : 1024    0    0    0    0    0    0    0
hash_32(.,3):  127  129  129  126  129  127  128  129
(x*G) & 7   : 1024    0    0    0    0    0    0    0
id=1  -> bucket 3
id=2  -> bucket 6
id=3  -> bucket 1
id=9  -> bucket 3
id=17 -> bucket 3
id=42 -> bucket 0
lookup 9 : right=10.0.0.9  wrong=10.0.0.17
lookup 17: right=10.0.0.17  wrong=10.0.0.17
lookup 5 : right=NULL  wrong=NULL
```

逐段讀：

**(2) 碰撞**：id 1、9、17 全落在 bucket 3。`hlist_add_head` 插在最前面，所以 bucket 3 的順序是 17 → 9 → 1。查 9 時，錯誤版本拿到 bucket 第一個（17），**回傳了一個 key 完全不同的物件**，而且沒有任何錯誤訊號。查 17 時剛好答對——這是最危險的情況：測試可能剛好全過，因為測試資料沒有碰撞。

**(3) `hlist_del`**：42 是 bucket 0 唯一的元素，刪除時 `hlist_del` 只拿到 node 本身，靠 `pprev` 直接改掉 `bucket[0].first`。這就是 Ch 23 講的 `pprev` 設計在 hash table 裡的實際用處：從物件指標出發刪除，不需要重算 hash 去找 bucket。

**(1)** 留到下一節解釋。

## hash_32 與那個 magic number

`include/linux/hash.h:24-42（v6.18）`：

```c
/*
 * This hash multiplies the input by a large odd number and takes the
 * high bits.  Since multiplication propagates changes to the most
 * significant end only, it is essential that the high bits of the
 * product be used for the hash value.
 *
 * Chuck Lever verified the effectiveness of this technique:
 * http://www.citi.umich.edu/techreports/reports/citi-tr-00-1.pdf
 *
 * Although a random odd number will do, it turns out that the golden
 * ratio phi = (sqrt(5)-1)/2, or its negative, has particularly nice
 * properties.  (See Knuth vol 3, section 6.4, exercise 9.)
 *
 * These are the negative, (1 - phi) = phi**2 = (3 - sqrt(5))/2,
 * which is very slightly easier to multiply by and makes no
 * difference to the hash distribution.
 */
#define GOLDEN_RATIO_32 0x61C88647
#define GOLDEN_RATIO_64 0x61C8864680B583EBull
```

以及實際的 hash（`include/linux/hash.h:65-69（v6.18）`）：

```c
static inline u32 hash_32(u32 val, unsigned int bits)
{
	/* High bits are more random, so use them. */
	return __hash_32(val) >> (32 - bits);
}
```

這叫**乘法雜湊（multiplicative hashing）**，Knuth《TAOCP》第 3 卷 6.4 節的經典方法：`h(x) = (x * A mod 2^w) >> (w - bits)`。三個要點，每一個都能從原始碼註解和上面的實驗驗證：

**1. 常數從哪來**。用 Python 算一下 `(1 - φ) × 2^32` 與 `× 2^64`（φ = (√5 − 1)/2）：

```
$ python3 -c "
from decimal import Decimal, getcontext
getcontext().prec=50
phi=(Decimal(5).sqrt()-1)/2
v32=(1-phi)*2**32; v64=(1-phi)*2**64
print('2^32*(1-phi)=',v32, hex(int(v32)), hex(int(v32+Decimal('0.5'))))
print('2^64*(1-phi)=',v64, hex(int(v64)), hex(int(v64+Decimal('0.5'))))
print('2^32*phi=', hex(int(phi*2**32)))"
2^32*(1-phi)= 1640531526.5027697035224152292074153782202739977257 0x61c88646 0x61c88647
2^64*(1-phi)= 7046029254386353130.0483894123781930501435475043911 0x61c8864680b583ea 0x61c8864680b583ea
2^32*phi= 0x9e3779b9
```

32 位元版本四捨五入得到 `0x61C88647`，與原始碼一致。64 位元版本四捨五入得到 `...83EA`，原始碼卻是 `...83EB`——差 1。原因是乘法雜湊要求 **A 必須是奇數**（奇數與 2^64 互質，`x → x*A mod 2^64` 才是一對一映射，不會平白製造碰撞），`...83EA` 是偶數，所以取了最近的奇數。32 位元版本 `0x61C88647` 本身就是奇數，剛好不用調整。

你可能在別處看過 `0x9e3779b9`（例如 TEA 加密、Boost 的 `hash_combine`）——那是 φ × 2^32。註解說 kernel 選 1 − φ 是因為「very slightly easier to multiply by」，對分布沒有影響。另外註解開頭提到的 `GOLDEN_RATIO_PRIME` 這個舊名字（`include/linux/hash.h:9-12`）說明了一段歷史：舊版用的是質數，後來發現「the previous primes were actively bad for hashing」才換成現在的常數，名字保留下來。這是典型的**脈絡鴻溝**——光看名字會以為它是質數。

**2. 為什麼取高位**。乘法只會把低位的變化往高位傳，不會往低位傳：輸出的第 k 位只取決於輸入的第 0 到 k 位。實驗 (1) 用 1024 個 64-byte 對齊的位址當 key（低 6 位全為 0，這正是 kernel 拿指標當 key 時的真實情況）：

- `addr & 7`（天真取模）：1024 個全部擠進 bucket 0。
- `(addr * G) & 7`（乘完取**低**位）：一樣全部擠進 bucket 0——因為輸入低 6 位是 0，乘積低 6 位也必然是 0。
- `hash_32(addr, 3)`（乘完取**高** 3 位）：127 到 129，近乎完美均勻。

所以 `>> (32 - bits)` 不是隨便寫的，它是整個方法成立的前提。workqueue 的 `busy_hash` 拿 `work_struct` 指標當 key，靠的就是這個性質。

**3. 為什麼 bucket 數必須是 2 的冪**。取高 `bits` 位等價於除以 2^(32−bits)，結果自然落在 [0, 2^bits)。這也是 `DEFINE_HASHTABLE` 參數給 bits 而不是 bucket 數的原因。

`hash_min` 會依 key 的大小選 `hash_32` 或 `hash_long`（64 位元系統上是 `hash_64`）。指標當 key 時用 `hash_ptr`（`include/linux/hash.h:85-88`），它就是 `hash_long((unsigned long)ptr, bits)`。

**認識論提醒**：乘法雜湊對「有規律的整數或指標」分布很好，但它**不是密碼學雜湊**，攻擊者如果能控制 key，可以刻意製造碰撞讓某個 bucket 變長（hash flooding）。這也是為什麼網路子系統處理外部可控的 key（例如連線的四元組）時，用的是帶隨機種子的 `jhash`/`siphash`，而不是 `hash_32`。

## 手刻 hash table 與 struct 裡的 union

workqueue 的 `busy_hash` 定義在 `struct worker_pool` 裡（`kernel/workqueue.c:213-214（v6.18）`）：

```c
	/* a workers is either on busy_hash or idle_list, or the manager */
	DECLARE_HASHTABLE(busy_hash, BUSY_WORKER_HASH_ORDER);
```

`BUSY_WORKER_HASH_ORDER = 6`（`kernel/workqueue.c:109`，註解寫「64 pointers」）。被掛上去的 `struct worker`，掛勾是這樣宣告的（`kernel/workqueue_internal.h:24-29（v6.18）`）：

```c
struct worker {
	/* on idle list while idle, on busy hash table while busy */
	union {
		struct list_head	entry;	/* L: while idle */
		struct hlist_node	hentry;	/* L: while busy */
	};
```

這是一個值得記住的 pattern：**一個物件在兩種互斥狀態下分別待在兩種容器裡，就用 union 讓兩個掛勾共用記憶體**。讀碼時看到這種 union，立刻可以推出一條狀態不變式：「worker 不是在 idle_list 上，就是在 busy_hash 上，不可能同時」。註解裡的 `L:` 是 workqueue 自己的鎖標註慣例（`pool->lock` 保護），檔頭有說明——[Ch 30](./30-reading-locking-code.md) 會談這類標註怎麼讀。Ch 26 會看到 `xa_node` 和 Ch 24 的 `epitem`（`rb_node` 與 `rcu_head` 共用）用同樣手法。

插入點在 `kernel/workqueue.c:3189`：

```c
	hash_add(pool->busy_hash, &worker->hentry, (unsigned long)work);
```

用 Ch 23 的方法交叉核對：插入用的 member 是 `hentry`、key 是 `(unsigned long)work`；查找時 `hash_for_each_possible(pool->busy_hash, worker, hentry, (unsigned long)work)`——member 與 key 運算式完全一致。**插入和查找的 key 運算式必須一模一樣**（包括型別轉換），否則 `hash_min` 可能因為 `sizeof(val)` 不同選到不同的 hash 函式。

## rhashtable：會自己長大、讀者不拿鎖

`hashtable.h` 的大小在編譯期固定。如果元素數量從 10 個長到 100 萬個，64 個 bucket 的平均鏈長會變成一萬多，O(1) 退化成 O(n)。rhashtable 解決這個問題：

- **自動擴縮**：元素數超過表大小的 75% 時擴成兩倍，啟用 `automatic_shrinking` 時低於 30% 會縮小（`include/linux/rhashtable.h:178-199（v6.18）`，`rht_grow_above_75`/`rht_shrink_below_30`）。實際的 rehash 在 workqueue 裡非同步進行（`lib/rhashtable.c:414` 的 `rht_deferred_worker`）。
- **RCU 無鎖查找**：讀者只需 `rcu_read_lock()`，可以和 rehash 同時進行。
- **鎖藏在 bucket 指標裡**：每個 bucket 的鎖是 bucket 指標的 bit 0（bit spinlock），不需要另外的鎖陣列。

演算法基礎是 Triplett、McKenney、Walpole 的 relativistic hash table 論文——Graf 的 commit message 直接引用了它（見延伸閱讀）。

讀 rhashtable 的使用者，重點是那個 `rhashtable_params`。以 System V IPC 為例（`ipc/util.c:101-106（v6.18）`）：

```c
static const struct rhashtable_params ipc_kht_params = {
	.head_offset		= offsetof(struct kern_ipc_perm, khtnode),
	.key_offset		= offsetof(struct kern_ipc_perm, key),
	.key_len		= sizeof_field(struct kern_ipc_perm, key),
	.automatic_shrinking	= true,
};
```

這四行把「這張表的形狀」全部宣告出來，比 `hashtable.h` 好讀得多：

| 欄位 | 告訴你什麼 |
|---|---|
| `head_offset` | 物件型別是 `struct kern_ipc_perm`，掛勾成員是 `khtnode`（型別 `struct rhash_head`，`include/linux/ipc.h:25`） |
| `key_offset` + `key_len` | key 是物件裡的 `key` 欄位，比較方式是逐 byte `memcmp` |
| `automatic_shrinking` | 元素變少時會縮表 |
| （沒寫 `obj_cmpfn`） | 用預設的位元組比較；有寫的話，那個函式就是「相等」的定義 |

所以 rhashtable **不會有「忘了比 key」的問題**——比較由框架依 `params` 執行。看 `__rhashtable_lookup` 的核心迴圈（`include/linux/rhashtable.h:605-618（v6.18）`）：

```c
	do {
		rht_for_each_rcu_from(he, rht_ptr_rcu(bkt), tbl, hash) {
			if (params.obj_cmpfn ?
			    params.obj_cmpfn(&arg, rht_obj(ht, he)) :
			    rhashtable_compare(&arg, rht_obj(ht, he)))
				continue;
			return he;
		}
		/* An object might have been moved to a different hash chain,
		 * while we walk along it - better check and retry.
		 */
	} while (he != RHT_NULLS_MARKER(bkt));

	/* Ensure we see any new tables. */
	smp_rmb();

	tbl = rht_dereference_rcu(tbl->future_tbl, ht);
	if (unlikely(tbl))
		goto restart;
```

這段值得多看兩眼，因為它示範了無鎖資料結構的典型讀法：

- **鏈尾不是 `NULL`，而是 `RHT_NULLS_MARKER(bkt)`**——一個最低位設為 1、其餘位元存著 bucket 位址的假指標（`include/linux/rhashtable.h:30-37` 的註解，概念來自 `include/linux/list_nulls.h`）。rehash 時物件可能被搬到別的鏈，讀者走到鏈尾發現 marker 不是自己出發的那個 bucket，就知道「我被帶到別條鏈了」，重來一次。
- **`future_tbl`**：rehash 期間新舊兩張表同時存在，舊表找不到就去新表找。
- 呼叫端（`ipc/util.c:176`）用 `rhashtable_lookup_fast(&ids->key_ht, &key, ipc_kht_params)`，`params` 是 `const` 並以值傳入，配合 `__always_inline` 讓編譯器把 `key_len`、`obj_cmpfn` 等全部常數化——和 Ch 24 的 `rb_find` 是同一個技巧。

Graf 的 commit message 也解釋了為什麼不沿用 `hlist`：

```
Objects to be hashed must include a struct rhash_head. The reason for not
using the existing struct hlist_head is that the expansion and shrinking
will have two buckets point to a single entry which would lead in obscure
reverse chaining behaviour.
```

擴縮過程中，一個元素會暫時被新舊兩個 bucket 指到，`hlist` 的 `pprev` 只能指回一個前驅，會破掉。所以 `struct rhash_head` 只有一個 `next`（`include/linux/rhashtable-types.h:18-20`）。**代價是刪除不能像 hlist 一樣 O(1) 從物件出發**，必須從 bucket 頭走到該元素。

## 對比與取捨

| | 手刻 `hlist_head[]` | `hashtable.h` | `rhashtable` |
|---|---|---|---|
| 大小 | 固定 | 固定（編譯期，2 的冪） | 動態擴縮 |
| key 比對 | 使用者在迴圈裡寫 | 使用者在迴圈裡寫 | 框架依 `params` 做 |
| 鎖 | 使用者自備 | 使用者自備（有 `_rcu` 變體） | 內建 bucket 鎖；查找走 RCU |
| 節點 | `hlist_node`（2 指標） | `hlist_node` | `rhash_head`（1 指標） |
| 從物件 O(1) 刪除 | 可以 | 可以 | 不行，要走鏈 |
| 讀碼難度 | 要找 hash 函式在哪 | 中等，記得找 key 比對 | `params` 一目了然；內部實作難 |
| 適用 | 舊程式碼、特殊需求 | 元素數量可預估、小表 | 元素數量變化大、讀多寫少 |

rbtree 與 hash table 的選擇，rbtree.rst 第一段就講了：hash table「must be tuned for a specific size and hash function」，rbtree「scale gracefully storing arbitrary keys」並且有序。rhashtable 補上了「tuned for a specific size」這個缺口，但仍然無序。

## 踩雷集錦

1. **以為 `hash_for_each_possible` 只會走到 key 相等的物件**：它走整個 bucket。迴圈內沒比 key 就是 bug，而且只在碰撞時才發作，測試很難抓到。
2. **插入和查找的 key 運算式不一致**：例如插入時 `hash_add(t, &o->n, o->id)`（`u32`），查找時傳 `(u64)id`。`hash_min` 依 `sizeof` 選 `hash_32` 或 `hash_long`，兩者結果不同，永遠找不到。
3. **把 `hash_add` 的第一個參數寫成指標**：`hashtable.h` 依賴陣列大小算 `HASH_BITS`。對動態配置的表（`struct hlist_head *tbl = kmalloc(...)`）不能用這組巨集，要自己算 bucket（這也是很多子系統仍然手刻的原因）。
4. **以為 `hash_32` 對任何 key 都夠好**：對外部可控的 key 有 hash flooding 風險。網路程式碼用 `jhash`/`siphash` 加隨機種子是有理由的。
5. **以為 rhashtable 的 `remove` 和 hlist 一樣便宜**：`rhash_head` 是單向鏈，刪除要從 bucket 頭找前驅。

## 進階：再往深一層

- **`rhltable`**：rhashtable 的變體，允許同一個 key 對應多個物件（`include/linux/rhashtable-types.h` 的 `struct rhlist_head`）。看到 `rhltable_lookup` 就知道 key 不唯一。
- **dcache 的 hash**：`fs/dcache.c` 的 `dentry_hashtable` 是開機時依記憶體大小配置的大表，用 `hlist_bl`（bit-lock hlist：把鎖塞在 head 指標的 bit 0）。它是「固定大小但大到夠用」路線的極致，值得和 rhashtable 對照讀。
- **`hash_for_each_possible_rcu` 的 `cond` 參數**：它允許傳一個 lockdep 運算式，告訴 RCU 檢查器「我沒拿 `rcu_read_lock` 但拿了某把鎖，所以也是安全的」。讀到這個參數時，它直接告訴你這張表的寫者鎖是哪一把。
- **`lib/test_hash.c`**：kernel 對 `hash_32`/`hash_64` 的自我測試，`hash.h` 註解裡說 `_generic` 版本的存在就是為了讓這個測試能比對各架構的最佳化版本。

## 動手練習

1. 把 `hashdemo.c` 的 bucket 數改成 `DEFINE_HASHTABLE(conns, 1)`（2 個 bucket），重跑 `find_wrong`，數一數答錯的比例。再改成 `bits = 10`，答錯還會發生嗎？為什麼「bucket 夠多所以碰撞很少」不能當作省略 key 比對的理由？
2. 在 v6.18 tree 裡用 `rg -n 'hash_for_each_possible\(' kernel/ fs/ mm/` 列出所有使用點，挑三個，各自回答：key 是什麼、比對 key 的那一行在哪、這張表被哪把鎖保護。
3. 讀 `ipc/util.c` 的 `ipc_addid`，找出 `rhashtable_insert_fast` 失敗時的錯誤處理。插入重複 key 會怎樣？（提示：看 `rhashtable_insert_fast` 的回傳值說明。）

## 本章重點整理

- kernel hash table 有三種形態：手刻 `hlist_head[]`、`hashtable.h`、`rhashtable`；前兩者底層都是 Ch 23 的 hlist。
- `hash_for_each_possible` 走訪整個 bucket，**迴圈內必須再比對 key**；從那個 `if` 可以反推 key 與相等的定義。
- `hash_32` 是 Knuth 乘法雜湊：乘上 (1 − φ) × 2^32 的奇數近似 `0x61C88647`，取高位；取低位或天真取模對對齊的指標完全失效。
- rhashtable 用 `params` 宣告 key 與掛勾，自動擴縮、RCU 查找、bucket 指標內嵌鎖，代價是單向鏈。
- union 共用掛勾（`worker::entry`/`hentry`）暗示一條「狀態互斥」的不變式。

## 自我檢核

- [ ] 不看筆記，解釋為什麼乘法雜湊要取高位而不是低位，並舉一個取低位會失敗的具體 key 分布。
- [ ] 有人在 code review 寫了一個 `hash_for_each_possible` 迴圈直接 `return obj;`，你會怎麼留言解釋問題？
- [ ] `GOLDEN_RATIO_64` 為什麼是 `...83EB` 而不是四捨五入的 `...83EA`？
- [ ] 給你一個 `struct rhashtable_params`，你能從中讀出哪四件事？
- [ ] 想一個你寫過的 hash table，它用的是哪種 hash 函式？key 可以被外部控制嗎？

## 延伸閱讀

### 論文

- **[Resizable, Scalable, Concurrent Hash Tables via Relativistic Programming](https://www.usenix.org/legacy/event/atc11/tech/final_files/Triplett.pdf)** — Josh Triplett, Paul E. McKenney, Jonathan Walpole，USENIX ATC（2011）
  - **核心貢獻**：在讀者完全不拿鎖（RCU）的前提下，讓 hash table 能擴張與收縮，關鍵是利用「等一個 grace period」讓新舊鏈在搬移過程中對讀者始終一致。
  - **讀哪裡**：Section 3（演算法）與其中的 expand/shrink 圖解；效能評估可略讀。
  - **和本章的關聯**：rhashtable 的 commit message 直接引用這篇（就是上面的 URL），`future_tbl` 與 nulls marker 的設計要從這裡理解。
- **Linux Kernel Hash Table Behavior: Analysis and Improvements** — Chuck Lever（University of Michigan CITI 技術報告 00-1，2000；標題以報告本身為準）
  - **核心貢獻**：用實際的 kernel 資料（inode、page cache 等）比較各種 hash 函式，驗證乘法雜湊在 kernel 工作負載上的效果。
  - **讀哪裡**：結論與實驗表格。`include/linux/hash.h` 註解裡的 URL 指向它；該網址年代久遠，若失效可用標題搜尋。

### 書籍

- **《The Art of Computer Programming, Vol. 3: Sorting and Searching》2nd ed.** — Donald Knuth（Addison-Wesley, 1998）
  - **讀哪幾章**：6.4 節「Hashing」的乘法雜湊段落與習題 9——`hash.h` 註解明確指向這裡。說明黃金比例為什麼讓連續 key 散得最開（與三距離定理有關）。
  - **前提**：需要一點數論耐心；只想知道結論的話，讀本章就夠。

### 官方文件 / 原始碼

- **v6.18 `include/linux/rhashtable.h` 與 `lib/rhashtable.c`**
  - **讀哪裡**：`rhashtable.h:30-45` 的註解（bucket 鎖用 BIT(0)、鏈尾的 nulls marker 存的是 bucket 位址）與 `RHT_ELASTICITY` 的說明（鏈長超過 16 視為遭受攻擊、強制 rehash），以及 `lib/rhashtable.c` 的 `rhashtable_rehash_one`。kernel 目前沒有獨立的 rhashtable `.rst` 文件，原始碼註解就是文件。
- **LWN：Relativistic hash tables, part 1: Algorithms** — Jonathan Corbet（2014 年 9 月）
  - **這篇說什麼**：rhashtable 合併前後的介紹，把論文的擴張/收縮步驟畫成圖，比論文好入門。
  - **讀哪裡**：整篇（還有 part 2 講實作）；在 LWN Kernel Index 的「Hash tables」項下可找到。

hash table 和 rbtree 都是「物件帶著掛勾，結構把掛勾串起來」。下一章的 xarray 換了一個思路：它不嵌在物件裡，而是一個以整數 index 為鍵的巨大稀疏陣列——page cache 的每一頁都住在這裡。

→ [Ch 26 xarray 與 maple tree：從 radix tree 演進而來](./26-xarray-maple-tree.md)
