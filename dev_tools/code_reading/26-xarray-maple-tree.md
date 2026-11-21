# Ch 26 — xarray 與 maple tree：從 radix tree 演進而來

> **目標**：知道 page cache 的每一頁住在 `address_space::i_pages` 這個 xarray 裡、VMA 住在 `mm_struct::mm_mt` 這棵 maple tree 裡；看懂 `XA_STATE` + `xas_*` 迴圈的固定節奏（`xas_retry`、`xas_reload`、`xas_pause`）；並能說出 radix tree → xarray → maple tree 這條演進線上，每一步各解決了什麼問題。

> **鴻溝**：本章主要練**慣用法鴻溝**（`xas_retry`/`xas_reload` 這些看似多餘的呼叫）、**領域鴻溝**（page cache 與 VMA 的基本概念）與**脈絡鴻溝**（為什麼同一個 kernel 裡 radix tree、xarray、maple tree 並存）。

> **環境**：Linux kernel v6.18 原始碼與 `~/code_reading/build/vmlinux`（x86_64，含 DWARF/BTF，用 pahole 1.25 查 layout）；user-space 範例 gcc 11.4、WSL Ubuntu 22.04。

## 為什麼需要這個？

前三章的結構有一個共同點：**物件自己帶著掛勾**（`list_head`、`rb_node`、`hlist_node`）。這很適合「物件的集合」，但有一類問題不是這個形狀：

- page cache：「檔案的第 4100 頁現在在記憶體裡嗎？在哪？」key 是**頁索引**（檔案偏移 / 4096），一個 10 GB 的檔案有 260 萬個可能的 index，但可能只有幾百頁被快取，而且常常是連續的一段。
- VMA：「位址 `0x7f12_3456_7000` 落在哪個 mapping 裡？」key 是**一段位址範圍**。

這兩種都更像「一個巨大而稀疏的陣列」，而不是「一群物件」。用 rbtree 存，每頁一個節點，pointer chasing 嚴重；用 hash table，失去了「找下一個有快取的頁」這種順序操作。kernel 從很早就用 **radix tree** 處理 page cache（`lib/radix-tree.c` 在 git 歷史起點 v2.6.12 就已存在），用 rbtree 加 linked list 處理 VMA。

問題在於這兩套舊方案的 API 都不好用、也不好讀：radix tree 的 API 要使用者自己處理記憶體預配置（`radix_tree_preload`）、自己拿鎖、tag 和 entry 分開操作；VMA 同時維護 rbtree、linked list 和一個 per-thread 快取（`vmacache`），三者要保持一致。Matthew Wilcox 的 xarray 和 Liam Howlett 的 maple tree 分別重寫了這兩塊。今天讀 kernel，你會看到三種 API 並存，因為轉換不是一次完成的。

## 先建立直覺

**xarray** 想成一個長度 2^64 的指標陣列，但只為「有東西的那幾段」配置記憶體。它的內部是一棵每層 64 叉的樹：index 的每 6 個 bit 決定一層要走哪個 slot，像查電話號碼一樣，區碼、局碼、號碼一段一段往下。

```
index 4100 = 0b 000001 000000 000100
                 │      │      └── 第 3 層（shift=0）：slot 4
                 │      └── 第 2 層（shift=6）：slot 0
                 └── 第 1 層（shift=12）：slot 1

          xa_head
             │
        ┌────▼─────┐ shift=12
        │ 0 1 ... 63│
        └──┬─┬──────┘
           │ └────────────┐
     ┌─────▼────┐    ┌─────▼────┐ shift=6
     │ 0 1 ...  │    │ 0 ...    │
     └──────────┘    └──┬───────┘
                   ┌────▼─────┐ shift=0
                   │0 1 2 3 4 │ ← slot 4 存著 folio 指標
                   └──────────┘
```

**maple tree** 則是一棵 B-tree：每個節點存一串「分界點（pivot）」和對應的 slot，一個 slot 代表一整段 `[前一個 pivot + 1, 這個 pivot]` 的範圍。它天生就是為「不重疊的區間」設計的，VMA 正好就是一堆不重疊的位址區間。

```
maple 節點（簡化）： pivot:  0x3fff   0x7fff   0xbfff   ...
                   slot:   NULL     vma_A    vma_B    ...
                   意義：  [0,0x3fff] 沒有 mapping
                           [0x4000,0x7fff] → vma_A
                           [0x8000,0xbfff] → vma_B
```

兩者的共同點：**資料結構不嵌在物件裡**。物件（folio、VMA）不需要帶任何掛勾，樹的節點是 xarray/maple tree 自己配置的。這是和前三章最大的差別，也是讀碼時要調整的心智模型：找「這個物件被放進哪裡」時，你找的不是 `list_add(&obj->member, ...)`，而是 `xa_store(xa, index, obj, ...)` 或 `mas_store(...)`。

## xarray 的定義與 page cache

`struct xarray` 本身只有三個欄位（`include/linux/xarray.h:300-305（v6.18）`）：

```c
struct xarray {
	spinlock_t	xa_lock;
/* private: The rest of the data structure is not to be used directly. */
	gfp_t		xa_flags;
	void __rcu *	xa_head;
};
```

鎖是內建的，這和 rbtree/hashtable「鎖自備」不同。page cache 就是一個 xarray，嵌在 `struct address_space` 裡（`include/linux/fs.h:506-526（v6.18）`，節錄）：

```c
struct address_space {
	struct inode		*host;
	struct xarray		i_pages;
	struct rw_semaphore	invalidate_lock;
	gfp_t			gfp_mask;
	atomic_t		i_mmap_writable;
#ifdef CONFIG_READ_ONLY_THP_FOR_FS
	/* number of thp, only for non-shmem files */
	atomic_t		nr_thps;
#endif
	struct rb_root_cached	i_mmap;
	unsigned long		nrpages;
	...
```

每個 inode 有一個 `address_space`（`inode->i_mapping`），它的 `i_pages` 以**頁索引**為 key、存 `struct folio *`。順便注意下一個欄位 `i_mmap`：它是 Ch 24 的 interval tree，記錄「哪些 VMA 映射了這個檔案」。**一個 struct 裡並排著三種資料結構**（xarray、augmented rbtree、`i_private_list` 這個 list_head），讀 kernel 時這很常見，每一個欄位都值得問「它用什麼結構、key 是什麼、誰保護它」。kernel-doc 註解（`include/linux/fs.h:484-505`）把保護關係寫得很清楚，例如「@nrpages: Number of page entries, protected by the i_pages lock」。

`i_pages` 從什麼時候是 xarray？查歷史：

```
$ git show v4.19:include/linux/fs.h | grep -n "i_pages"
408:	struct radix_tree_root	i_pages;	/* cached pages */
$ git show v4.20:include/linux/fs.h | grep -n "i_pages;"
426:	struct xarray		i_pages;
```

v4.20（2018 年底）換成 xarray。這一輪的 commit 由 Wilcox 署名，一系列叫「page cache: Convert ... to XArray」（例如 `74d609585d8b`「page cache: Add and replace pages using the XArray」）。xarray 本身的第一個 commit 是 `f8d5d0cc145c`「xarray: Add definition of struct xarray」（同樣進入 v4.20）。

那 radix tree 去哪了？它沒有消失，而是**被重新實作在 xarray 之上**（`include/linux/radix-tree.h:25（v6.18）`）：

```c
#define radix_tree_root		xarray
```

所以 v6.18 裡如果你看到某段老程式碼還在用 `radix_tree_lookup()`，它底下跑的其實是 xarray 的節點。這是大型專案典型的漸進遷移：**先讓新結構能扮演舊結構，再一個使用者一個使用者地改 API**。

## 實際看節點：pahole 與一個玩具模型

先看真實的 xarray 節點長什麼樣（從建好的 v6.18 vmlinux 讀 DWARF）：

```
$ pahole -C xa_node ~/code_reading/build/vmlinux
struct xa_node {
	unsigned char              shift;                /*     0     1 */
	unsigned char              offset;               /*     1     1 */
	unsigned char              count;                /*     2     1 */
	unsigned char              nr_values;            /*     3     1 */

	/* XXX 4 bytes hole, try to pack */

	struct xa_node *           parent;               /*     8     8 */
	struct xarray *            array;                /*    16     8 */
	union {
		struct list_head   private_list;         /*    24    16 */
		struct callback_head callback_head __attribute__((__aligned__(8))); /*    24    16 */
	} __attribute__((__aligned__(8)));               /*    24    16 */
	void *                     slots[64];            /*    40   512 */
	/* --- cacheline 8 boundary (512 bytes) was 40 bytes ago --- */
	union {
		long unsigned int  tags[3][1];           /*   552    24 */
		long unsigned int  marks[3][1];          /*   552    24 */
	};                                               /*   552    24 */

	/* size: 576, cachelines: 9, members: 9 */
	/* sum members: 572, holes: 1, sum holes: 4 */
	/* forced alignments: 1 */
} __attribute__((__aligned__(8)));
```

對照原始碼的常數（`include/linux/xarray.h:1153-1157（v6.18）`）：

```c
#ifndef XA_CHUNK_SHIFT
#define XA_CHUNK_SHIFT		(IS_ENABLED(CONFIG_BASE_SMALL) ? 4 : 6)
#endif
#define XA_CHUNK_SIZE		(1UL << XA_CHUNK_SHIFT)
#define XA_CHUNK_MASK		(XA_CHUNK_SIZE - 1)
```

`slots[64]` 就是 2^6；`shift` 欄位記錄這個節點處理 index 的哪 6 個 bit；`marks[3][1]` 是三組各 64 bit 的 bitmap（Ch 27），每個 slot 一個 bit，page cache 用它們標記 dirty/writeback（下一節）。`private_list` 和 `callback_head` 共用空間，又是「互斥狀態共用欄位」的 union pattern（Ch 25 的 `worker` 見過）：節點活著時可能掛在 workingset 的 list 上，要釋放時改用 `callback_head` 走 RCU 延遲釋放。

要感受「index 怎麼被拆開」，下面是一個**玩具模型**，**不是** `lib/xarray.c`，只重現三件事：6 bit 一層的拆解、樹高隨最大 index 長高、entry 低位元的型別標記。

```c
/* toyxa.c：xarray 節點結構的「玩具模型」。不是 lib/xarray.c，
 * 只重現三件事：6-bit 一層的 index 拆解、樹高隨最大 index 長高、entry 低位元的型別標記。 */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#define CHUNK_SHIFT 6                       /* 對應 XA_CHUNK_SHIFT（非 BASE_SMALL） */
#define CHUNK_SIZE  (1UL << CHUNK_SHIFT)
#define CHUNK_MASK  (CHUNK_SIZE - 1)

struct node { unsigned char shift; void *slots[CHUNK_SIZE]; };
struct toyxa { void *head; unsigned char height_shift; int nnodes; };

/* entry 編碼：低 2 bit 01/11 = value entry；10 = internal（這裡用來標記 node） */
static void *mk_value(unsigned long v) { return (void *)((v << 1) | 1); }
static int is_value(void *e) { return (uintptr_t)e & 1; }
static void *mk_node(struct node *n) { return (void *)((uintptr_t)n | 2); }
static int is_node(void *e) { return ((uintptr_t)e & 3) == 2; }
static struct node *to_node(void *e) { return (struct node *)((uintptr_t)e - 2); }

static unsigned long max_index(unsigned char shift) {
	return shift >= 64 ? ~0UL : (CHUNK_SIZE << shift) - 1; }

static void store(struct toyxa *xa, unsigned long index, void *entry)
{
	if (!xa->head) { xa->head = NULL; }
	/* 樹不夠高就在頂端加一層 */
	while (xa->head && index > max_index(xa->height_shift)) {
		struct node *n = calloc(1, sizeof(*n)); xa->nnodes++;
		n->shift = xa->height_shift + CHUNK_SHIFT;
		n->slots[0] = xa->head; xa->head = mk_node(n); xa->height_shift = n->shift;
	}
	if (!xa->head) {                        /* 第一個元素 */
		xa->height_shift = 0;
		while (index > max_index(xa->height_shift)) xa->height_shift += CHUNK_SHIFT;
		struct node *n = calloc(1, sizeof(*n)); xa->nnodes++;
		n->shift = xa->height_shift; xa->head = mk_node(n);
	}
	void **slot = &xa->head;
	while (is_node(*slot)) {
		struct node *n = to_node(*slot);
		unsigned off = (index >> n->shift) & CHUNK_MASK;
		slot = &n->slots[off];
		if (n->shift && !*slot) {
			struct node *c = calloc(1, sizeof(*c)); xa->nnodes++;
			c->shift = n->shift - CHUNK_SHIFT; *slot = mk_node(c);
		}
		if (!n->shift) break;
	}
	*slot = entry;
}

static void *load(struct toyxa *xa, unsigned long index, int verbose)
{
	void *e = xa->head;
	if (index > max_index(xa->height_shift)) return NULL;
	while (is_node(e)) {
		struct node *n = to_node(e);
		unsigned off = (index >> n->shift) & CHUNK_MASK;
		if (verbose) printf("  [shift=%2u] slot %2u\n", n->shift, off);
		e = n->slots[off];
	}
	return e;
}

int main(void)
{
	struct toyxa xa = {0};
	static long pageA, pageB, pageC;        /* 假裝是 struct folio，對齊 >= 4 */
	unsigned long idx[] = { 0, 1, 63, 64, 4100 };
	void *ent[] = { &pageA, &pageB, mk_value(7), &pageC, mk_value(12345) };
	for (int i = 0; i < 5; i++) {
		store(&xa, idx[i], ent[i]);
		printf("store index %-5lu -> height_shift=%2u nodes=%d\n",
		       idx[i], xa.height_shift, xa.nnodes);
	}
	printf("load(4100):\n");
	void *e = load(&xa, 4100, 1);
	printf("  -> %s %lu\n", is_value(e) ? "value" : "ptr", (uintptr_t)e >> 1);
	printf("load(64):\n");
	e = load(&xa, 64, 1);
	printf("  -> %s %s\n", is_value(e) ? "value" : "ptr", e == &pageC ? "(&pageC)" : "?");
	printf("load(5000) -> %p\n", load(&xa, 5000, 0));
	printf("4100 = %lu*4096 + %lu*64 + %lu\n", 4100UL >> 12, (4100UL >> 6) & 63, 4100UL & 63);
	return 0;
}
```

```
$ gcc -O1 -Wall -o toyxa toyxa.c && ./toyxa
store index 0     -> height_shift= 0 nodes=1
store index 1     -> height_shift= 0 nodes=1
store index 63    -> height_shift= 0 nodes=1
store index 64    -> height_shift= 6 nodes=3
store index 4100  -> height_shift=12 nodes=6
load(4100):
  [shift=12] slot  1
  [shift= 6] slot  0
  [shift= 0] slot  4
  -> value 12345
load(64):
  [shift=12] slot  0
  [shift= 6] slot  1
  [shift= 0] slot  0
  -> ptr (&pageC)
load(5000) -> (nil)
4100 = 1*4096 + 0*64 + 4
```

觀察：

- index 0–63 用一個節點就夠；存入 64 時樹長高一層（頂端加一個 `shift=6` 的節點，舊根變成它的 slot 0）；存入 4100 時再長一層。**樹高只取決於最大 index，不取決於元素數量**。這就是 xarray 文件說的「efficient when the indices used are densely clustered」——page cache 的頁索引正是連續成段的。
- 找 4100 走了三層，每層用 `(index >> shift) & 63` 選 slot。讀 `lib/xarray.c` 時看到 `get_offset(index, node)`，就是這個運算。
- 和真實 xarray 的差異（認識論誠實）：真實版本**只有 index 0 一個元素時，直接把 entry 存在 `xa_head`，完全不配置節點**；刪除時會把樹縮矮；節點有 `count`/`nr_values` 計數；還有 multi-index entry（一個 entry 佔一整段 index，大 folio 用得到）。這些玩具都沒做。

### entry 的低位元：value、internal 與 pointer

玩具裡的 `mk_value`、`is_node` 直接照抄 kernel 的編碼（`include/linux/xarray.h:58-62、83-86、173-176（v6.18）`）：

```c
static inline void *xa_mk_value(unsigned long v)
{
	WARN_ON((long)v < 0);
	return (void *)((v << 1) | 1);
}
...
static inline bool xa_is_value(const void *entry)
{
	return (unsigned long)entry & 1;
}
...
static inline bool xa_is_internal(const void *entry)
{
	return ((unsigned long)entry & 3) == 2;
}
```

一個 slot 裡的 `void *` 有三種身分，靠低 2 位區分：

| 低 2 位 | 身分 | page cache 裡的意義 |
|---|---|---|
| `00` | 真正的指標 | `struct folio *` |
| `x1` | value entry（整數左移一位） | **shadow entry**：頁被回收後留下的「它曾經在這裡」記錄，給 workingset 偵測用；shmem 的 swap entry |
| `10` | internal entry | 指向下一層節點、retry 標記、zero 標記等 xarray 內部用途 |

這就是為什麼 xarray 文件要求「Normal pointers ... must be 4-byte aligned」——低 2 位要留給標記。也解釋了下面 `filemap_get_entry` 裡那個 `xa_is_value(folio)` 判斷：**從 page cache 拿出來的東西不一定是 folio**。讀碼時看到變數名叫 `folio` 卻先檢查 `xa_is_value`，不要以為多此一舉。

## 普通 API：xa_load / xa_store

普通 API 自己處理鎖與 RCU。`xa_load` 的完整實作（`lib/xarray.c:1612-1624（v6.18）`）：

```c
void *xa_load(struct xarray *xa, unsigned long index)
{
	XA_STATE(xas, xa, index);
	void *entry;

	rcu_read_lock();
	do {
		entry = xa_zero_to_null(xas_load(&xas));
	} while (xas_retry(&xas, entry));
	rcu_read_unlock();

	return entry;
}
```

kernel-doc 寫「Context: Any context. Takes and releases the RCU lock.」；`xa_store` 則是「Takes and releases the xa_lock. May sleep if the @gfp flags permit.」（`lib/xarray.c:1713-1728`）。**普通 API 的 kernel-doc 一定會寫 Context，讀碼時那一行就告訴你能不能在中斷裡呼叫、會不會睡**。

一個典型的普通 API 使用者是 mount ID 配置（`fs/namespace.c:79、219-234（v6.18）`）：

```c
static DEFINE_XARRAY_FLAGS(mnt_id_xa, XA_FLAGS_ALLOC);
...
static int mnt_alloc_id(struct mount *mnt)
{
	int res;

	xa_lock(&mnt_id_xa);
	res = __xa_alloc(&mnt_id_xa, &mnt->mnt_id, mnt, XA_LIMIT(1, INT_MAX), GFP_KERNEL);
	if (!res)
		mnt->mnt_id_unique = ++mnt_id_ctr;
	xa_unlock(&mnt_id_xa);
	return res;
}

static void mnt_free_id(struct mount *mnt)
{
	xa_erase(&mnt_id_xa, mnt->mnt_id);
}
```

`XA_FLAGS_ALLOC` 讓 xarray 當作 ID 配置器：`__xa_alloc` 找一個空的 index、存入 `mnt`，並把 index 寫回 `mnt->mnt_id`。這取代了舊的 IDR（`include/linux/idr.h` 今天也是建在 xarray 上）。注意前綴：`xa_alloc` 自己拿鎖，`__xa_alloc` 要求呼叫者已持有 `xa_lock`——這裡用雙底線版本，是為了讓 `mnt_id_unique` 的遞增和 ID 配置在同一個臨界區內。**kernel 的 `__` 前綴常常代表「呼叫者負責鎖」**，Ch 23 的 `__list_add` 是同一個慣例。

## 進階 API：XA_STATE 與 xas_* 迴圈

普通 API 每次呼叫都從根往下走。如果你要連續處理一段 index（例如「把這個檔案第 100 到 200 頁全部找出來」），每次重走是浪費。進階 API 讓你在 stack 上放一個**游標** `struct xa_state`，記住目前走到哪個節點，下一次操作從那裡繼續。`Documentation/core-api/xarray.rst` 的「Advanced API」一節：

```
The advanced API is based around the xa_state.  This is an opaque data
structure which you declare on the stack using the XA_STATE() macro.
This macro initialises the xa_state ready to start walking around the
XArray.  It is used as a cursor to maintain the position in the XArray
and let you compose various operations together without having to restart
from the top every time.  The contents of the xa_state are protected by
the rcu_read_lock() or the xas_lock().  If you need to drop whichever of
those locks is protecting your state and tree, you must call xas_pause()
so that future calls do not rely on the parts of the state which were
left unprotected.
```

以及最重要的一句：「No locking is done for you by the advanced API」。

### 讀碼範例一：單點查找的 retry 節奏

page cache 最核心的查找（`mm/filemap.c:1894-1923（v6.18）`）：

```c
void *filemap_get_entry(struct address_space *mapping, pgoff_t index)
{
	XA_STATE(xas, &mapping->i_pages, index);
	struct folio *folio;

	rcu_read_lock();
repeat:
	xas_reset(&xas);
	folio = xas_load(&xas);
	if (xas_retry(&xas, folio))
		goto repeat;
	/*
	 * A shadow entry of a recently evicted page, or a swap entry from
	 * shmem/tmpfs.  Return it without attempting to raise page count.
	 */
	if (!folio || xa_is_value(folio))
		goto out;

	if (!folio_try_get(folio))
		goto repeat;

	if (unlikely(folio != xas_reload(&xas))) {
		folio_put(folio);
		goto repeat;
	}
out:
	rcu_read_unlock();

	return folio;
}
```

第一次讀這段，大部分人會覺得有三個地方「多餘」。每一個都是無鎖讀者對抗並行寫者的必要步驟：

1. **`xas_retry`**：RCU 讀者可能在寫者正在改樹時讀到一個 internal 的 retry entry（寫者把節點搬走時留下的標記）。`xas_retry`（`include/linux/xarray.h:1532-1540`）看到這種 entry 就 `xas_reset` 並回傳 true，讓你重走。
2. **`folio_try_get` 失敗就重來**：RCU 只保證 folio 的記憶體還沒被釋放，不保證它還活著。refcount 已經是 0 的 folio 正在被回收，不能拿。
3. **`xas_reload` 再讀一次並比較**：在「讀到指標」和「refcount 加一」之間，這頁可能已經被移出 page cache、又被別的檔案重用。拿到引用之後再確認一次「這個 slot 裡還是它」，不是就放掉重來。

這是 RCU + refcount 的標準 lookup 模板（[Ch 20](./20-refcount-and-lifetime.md) 的 `try_get` 和 [Ch 31](./31-reading-rcu-code.md) 的 RCU 讀者規則合在一起）。**讀碼時認出這個形狀，就可以把三個 `goto repeat` 一起當成「無鎖查找的重試樣板」略過，專注在真正的業務邏輯**——這是 [Ch 4](./04-selective-reading.md) 選擇性閱讀的實際應用。

### 讀碼範例二：範圍走訪

`xas_for_each` 的定義（`include/linux/xarray.h:1817-1819（v6.18）`）：

```c
#define xas_for_each(xas, entry, max) \
	for (entry = xas_find(xas, max); entry; \
	     entry = xas_next_entry(xas, max))
```

它跳過空 slot，只回傳有東西的 entry。實際用法（`mm/filemap.c:646-668（v6.18）`）：

```c
bool filemap_range_has_writeback(struct address_space *mapping,
				 loff_t start_byte, loff_t end_byte)
{
	XA_STATE(xas, &mapping->i_pages, start_byte >> PAGE_SHIFT);
	pgoff_t max = end_byte >> PAGE_SHIFT;
	struct folio *folio;

	if (end_byte < start_byte)
		return false;

	rcu_read_lock();
	xas_for_each(&xas, folio, max) {
		if (xas_retry(&xas, folio))
			continue;
		if (xa_is_value(folio))
			continue;
		if (folio_test_dirty(folio) || folio_test_locked(folio) ||
				folio_test_writeback(folio))
			break;
	}
	rcu_read_unlock();
	return folio != NULL;
}
```

辨識 `xas_*` 迴圈的 checklist：

```
1. XA_STATE(xas, &某個xarray, 起始index)     → 這個迴圈操作哪個 xarray、從哪開始
2. 外面包的是 rcu_read_lock 還是 xas_lock    → 唯讀還是會修改
3. 迴圈頭 xas_for_each / xas_for_each_marked / xas_find
                                              → 全部走訪，還是只走有某個 mark 的
4. 迴圈內前兩行 xas_retry + xa_is_value       → 樣板，可略過
5. 迴圈內有 xas_pause()                       → 作者要在中途放鎖（例如 cond_resched），
                                                 下一輪會從 pause 的位置重新找
6. 迴圈後讀游標變數                           → 和 list_for_each_entry 不同，這裡跑完時
                                                 entry 是 NULL，用 `folio != NULL` 判斷有沒有 break 是安全的
```

第 6 點和 Ch 23 的踩雷正好相反：`xas_for_each` 是以 `entry` 為 `NULL` 終止的，所以迴圈後檢查它是合法的慣用法，上面這段就這麼做。

### Marks：每個 slot 附帶的三個 bit

page cache 用 xarray 的 mark 記錄頁的狀態（`include/linux/fs.h:534-536（v6.18）`）：

```c
#define PAGECACHE_TAG_DIRTY	XA_MARK_0
#define PAGECACHE_TAG_WRITEBACK	XA_MARK_1
#define PAGECACHE_TAG_TOWRITE	XA_MARK_2
```

mark 不只存在葉節點，每個內部節點的 mark bitmap 也記錄「我底下有沒有任何 slot 帶這個 mark」。所以 writeback 要找「這個檔案所有 dirty 的頁」時，`xas_for_each_marked(&xas, folio, max, PAGECACHE_TAG_DIRTY)` 可以整棵子樹跳過——又是 Ch 24 augmented tree 的思路：**在內部節點存子樹彙總，換取剪枝**。

## Maple tree：取代 VMA 的 rbtree

v6.0 的 `struct mm_struct` 裡，VMA 由三樣東西同時追蹤：

```
$ git show v6.0:include/linux/mm_types.h | grep -n -E "mm_rb|vmacache_seqnum|struct vm_area_struct \*mmap;"
488:		struct vm_area_struct *mmap;		/* list of VMAs */
489:		struct rb_root mm_rb;
490:		u64 vmacache_seqnum;                   /* per-thread vmacache */
$ git show v6.1:include/linux/mm_types.h | grep -n -E "mm_rb|mm_mt|struct vm_area_struct \*mmap;"
514:		struct maple_tree mm_mt;
```

v6.1 一口氣換掉三個，只剩 `mm_mt`。對應的 commit 都是 Liam R. Howlett 署名、日期 2022-09-06 的一個系列：

```
54a611b60590 Maple Tree: add new data structure
d4af56c5c7c6 mm: start tracking VMAs with maple tree
524e00b36e8c mm: remove rb tree.
7964cf8caa4d mm: remove vmacache
763ecb035029 mm: remove the vma linked list
```

`git describe --contains 524e00b36e8c` 給出 `v6.1-rc1~...`，確認是 v6.1 合併。之後 `3dd443254941`「mm: enable maple tree RCU mode by default」在 v6.3 讓 VMA 的 maple tree 預設開 RCU，為 v6.4 的 per-VMA lock（page fault 時不必拿整個 `mmap_lock`）鋪路。v6.18 的旗標（`include/linux/mm_types.h:1273-1274`）：

```c
#define MM_MT_FLAGS	(MT_FLAGS_ALLOC_RANGE | MT_FLAGS_LOCK_EXTERN | \
			 MT_FLAGS_USE_RCU)
```

`LOCK_EXTERN` 表示不用 maple tree 內建的 spinlock，而是由外部的 `mmap_lock` 保護寫入；`USE_RCU` 讓讀者可以無鎖；`ALLOC_RANGE` 支援「找一段夠大的空隙」（`mmap` 找空位址用）。

為什麼要換？對比同一個函式的前後版本最清楚。v6.0 的 `find_vma`（`git show v6.0:mm/mmap.c`，約 2253 行）：

```c
struct vm_area_struct *find_vma(struct mm_struct *mm, unsigned long addr)
{
	struct rb_node *rb_node;
	struct vm_area_struct *vma;

	mmap_assert_locked(mm);
	/* Check the cache first. */
	vma = vmacache_find(mm, addr);
	if (likely(vma))
		return vma;

	rb_node = mm->mm_rb.rb_node;

	while (rb_node) {
		struct vm_area_struct *tmp;

		tmp = rb_entry(rb_node, struct vm_area_struct, vm_rb);

		if (tmp->vm_end > addr) {
			vma = tmp;
			if (tmp->vm_start <= addr)
				break;
			rb_node = rb_node->rb_left;
		} else
			rb_node = rb_node->rb_right;
	}

	if (vma)
		vmacache_update(addr, vma);
	...
```

你應該能用 Ch 24 的辨識法一眼認出那個 rbtree 下降迴圈。v6.18（`mm/mmap.c:904-910（v6.18）`）：

```c
struct vm_area_struct *find_vma(struct mm_struct *mm, unsigned long addr)
{
	unsigned long index = addr;

	mmap_assert_locked(mm);
	return mt_find(&mm->mm_mt, &index, ULONG_MAX);
}
```

二十幾行變成一行，`vmacache` 也不需要了。maple_tree.rst 的 Overview 說它「does not require a user written search method」——正是 Ch 24 講的 rbtree 痛點。更深層的理由是 cache 效率：rbtree 每層一個節點、每個節點一次 cache miss，數千個 VMA 要走十幾層；maple tree 的節點是 256 bytes，一次讀進好幾條 cache line、做十幾個 pivot 比較，樹高大約是 log₁₆ n。看實際節點：

```
$ pahole -C maple_range_64 ~/code_reading/build/vmlinux
struct maple_range_64 {
	struct maple_pnode *       parent;               /*     0     8 */
	long unsigned int          pivot[15];            /*     8   120 */
	/* --- cacheline 2 boundary (128 bytes) --- */
	union {
		void *             slot[16];             /*   128   128 */
		struct {
			void *     pad[15];              /*   128   120 */
			/* --- cacheline 3 boundary (192 bytes) was 56 bytes ago --- */
			struct maple_metadata meta;      /*   248     2 */
		};                                       /*   128   128 */
	};                                               /*   128   128 */

	/* size: 256, cachelines: 4, members: 3 */
};
```

15 個 pivot 切出 16 段範圍，正好是 `MAPLE_RANGE64_SLOTS`。但如果你直接 grep 這個常數，會看到兩個值：`include/linux/maple_tree.h:27-39（v6.18）` 用 `#if defined(CONFIG_64BIT) || ...` 分成兩支，64 位元那支是 16（第 30 行），`#else` 的 32 位元那支是 32（第 36 行，指標只有 4 bytes，同樣 256 bytes 能塞兩倍）。**讀到 `#if` 兩支的常數時，先確認自己在哪一支**，pahole 看的是實際編譯出來的那一支（[Ch 36](./36-kconfig-kbuild-ifdef.md)、[Ch 50](./50-compiler-as-reading-tool.md)）。

### 讀 maple tree 程式碼

maple tree 的 API 和 xarray 是同一套設計語言，讀過 xarray 就能讀它：

| xarray | maple tree | 用途 |
|---|---|---|
| `xa_load(xa, i)` | `mtree_load(mt, i)` | 單點查 |
| `XA_STATE(xas, xa, i)` | `MA_STATE(mas, mt, first, end)` | 游標 |
| `xas_for_each(&xas, e, max)` | `mas_for_each(&mas, e, max)` | 範圍走訪 |
| `xas_pause` | `mas_pause` | 中途放鎖 |

mm 在 maple tree 上又包了一層 VMA 專用的迭代器（`include/linux/mm.h:925-932、986-987（v6.18）`）：

```c
static inline struct vm_area_struct *vma_next(struct vma_iterator *vmi)
{
	/*
	 * Uses mas_find() to get the first VMA when the iterator starts.
	 * Calling mas_next() could skip the first entry.
	 */
	return mas_find(&vmi->mas, ULONG_MAX);
}
...
#define for_each_vma(__vmi, __vma)					\
	while (((__vma) = vma_next(&(__vmi))) != NULL)
```

所以 v6.18 讀 mm 程式碼時，**`VMA_ITERATOR(vmi, mm, addr)` + `for_each_vma(vmi, vma)` 就是「走訪這個 process 的所有 VMA」**，取代了 v6.0 的 `for (vma = mm->mmap; vma; vma = vma->vm_next)`。讀舊文章或舊 patch 時看到 `vm_next`，要知道它在 v6.1 已經不存在了。

## 對比與取捨

| | radix tree（v4.19 以前） | xarray | rbtree | maple tree |
|---|---|---|---|---|
| key | 整數 index | 整數 index | 任意可比較的 key | 整數範圍 |
| 是否嵌入物件 | 否 | 否 | 是（`rb_node`） | 否 |
| 鎖 | 使用者自備 | 內建 `xa_lock`（可用進階 API 自行管理） | 使用者自備 | 內建或外部鎖 |
| 記憶體配置 | 使用者要 `radix_tree_preload` | API 內部處理（`xas_nomem`） | 物件配置時一起 | API 內部處理 |
| RCU 讀者 | 支援 | 支援 | 不安全（旋轉） | 支援 |
| 範圍語意 | 無 | multi-index entry | 需 augmented | 原生 |
| 典型用途 | — | page cache、ID 配置、各種 index→物件 | 排程、計時器、epoll | VMA |

xarray 文件也說了它**不適合**的情況：「hashing the object and using the hash as the index will not perform well」——把 hash 值當 index 會讓 index 散佈在整個 2^64 空間，每個元素都要一整條路徑的節點。這種需求請用 Ch 25 的 hash table。

## 踩雷集錦

1. **以為從 `i_pages` 拿出來的一定是 `struct folio *`**：可能是 shadow entry 或 swap entry（value entry）。每個走訪 page cache 的迴圈都要先過 `xa_is_value`。
2. **以為 `xas_retry`/`xas_reload` 是防禦性多餘程式碼而刪掉**：它們是 RCU 無鎖查找正確性的一部分。刪掉後只會在高並行壓力下偶爾出錯，極難重現。
3. **用 xarray 存未對齊的指標或 `ERR_PTR`**：文件明說 pointer 必須 4-byte 對齊、不支援 `IS_ERR()` 指標（會和 value/internal entry 衝突）。存函式指標在某些架構上也不保證對齊。
4. **讀舊資料時以為 VMA 還有 `vm_next`/`mm_rb`**：v6.1 起全部移除。LWN 2022 年以前的 mm 文章、LKD 第三版的 VMA 章節都是舊模型。
5. **混用普通 API 與進階 API 的鎖語意**：`xa_store` 自己拿 `xa_lock`；在已經 `xas_lock` 的臨界區裡呼叫它會自鎖死。持鎖時要用 `__xa_store` 或 `xas_store`。

## 進階：再往深一層

- **multi-index entry 與大 folio**：一個 2 MB 的 folio 在 page cache 裡佔 512 個 index，xarray 用一個 multi-index entry 表示（`XA_STATE_ORDER`）。讀 `mm/filemap.c` 的 `__filemap_add_folio` 時會看到 `xas_set_order`。
- **`xas_nomem` 迴圈**：修改 xarray 時的標準模板是「拿鎖 → 嘗試 → 放鎖 → `xas_nomem` 配置記憶體 → 重來」。文件 Advanced API 一節有完整說明；看懂它，你就理解為什麼 xarray 不需要 radix tree 那套 preload。
- **maple tree 的節點類型**：`maple_range_64`（範圍節點）、`maple_arange_64`（多存每個子樹的最大空隙，給 `mmap` 找空位）、`maple_dense`。`arange` 又是一個 augmented 的例子。
- **per-VMA lock**：v6.4 起 page fault 先嘗試 `lock_vma_under_rcu()`，在 RCU 下用 maple tree 找 VMA、只鎖那一個 VMA。這是 maple tree RCU 模式最重要的收穫；x86 的入口在 `arch/x86/mm/fault.c:1327（v6.18）`，實作在 `mm/mmap_lock.c:224`。

## 動手練習

1. 在 v6.18 tree 用 `rg -n 'xas_for_each_marked\(' mm/` 找出 writeback 走訪 dirty 頁的地方，回答：外面拿的是 RCU 還是 `xas_lock`？迴圈內有沒有 `xas_pause`？為什麼需要它？
2. 修改 `toyxa.c`，讓 index 0 單獨存在時不配置節點、直接存進 `head`（模仿真實 xarray）。存入 index 1 時要怎麼把它「下放」進新節點？
3. 用 `pahole -C maple_arange_64 ~/code_reading/build/vmlinux` 看它比 `maple_range_64` 多了什麼欄位，對照 `include/linux/maple_tree.h` 的註解，說明那個欄位怎麼幫 `mmap` 找空隙。

## 本章重點整理

- xarray 是以整數 index 為鍵的稀疏陣列，內部是 64 叉樹；page cache 的 `address_space::i_pages` 從 v4.20 起是 xarray，radix tree 在 v6.18 只是 xarray 的別名。
- slot 裡的 `void *` 用低 2 位區分 pointer、value entry（shadow/swap）、internal entry；讀 page cache 一定要先過 `xa_is_value`。
- `XA_STATE` + `xas_*` 是游標式進階 API；`xas_retry`、`folio_try_get`、`xas_reload` 是 RCU 無鎖查找的固定樣板，可以整組辨識後略過。
- maple tree 是為不重疊範圍設計的 RCU-safe B-tree，v6.1 用它一次取代 VMA 的 rbtree、linked list 與 vmacache；`for_each_vma` 取代了 `vm_next`。
- 這三種結構都不嵌在物件裡：找「物件被放進哪」時要找 `xa_store`/`mas_store`，不是 `list_add` 式的掛勾。

## 自我檢核

- [ ] 不看筆記，說明 index 4100 在 64 叉的 xarray 裡會經過哪三個 slot，以及為什麼存入 index 64 會讓樹長高。
- [ ] 解釋 `filemap_get_entry` 裡三個 `goto repeat` 各自在防範什麼並行情境。
- [ ] 有人問「既然 xarray 這麼好，為什麼 epoll 不用 xarray 存 fd？」，你怎麼回答？（提示：epoll 的 key 是什麼？）
- [ ] 讀一篇 2019 年的 mm 文章，裡面提到 `mm->mmap` 與 `vma->vm_next`，你要怎麼把它翻譯成 v6.18 的資料結構？
- [ ] `__xa_alloc` 和 `xa_alloc` 差在哪？`mnt_alloc_id` 為什麼選前者？

## 延伸閱讀

### 官方文件

- **[XArray](https://docs.kernel.org/core-api/xarray.html)**（v6.18 tree：`Documentation/core-api/xarray.rst`）
  - **讀哪裡**：Overview（value entry、對齊要求）、Normal API、「Advanced API」與「Internal Entries」。本章的 retry/zero entry 在 Internal Entries 有完整表格。
  - **前提**：本章讀完即可；Locking 一節要配合 Ch 30/31。
- **[Maple Tree](https://docs.kernel.org/core-api/maple_tree.html)**（`Documentation/core-api/maple_tree.rst`）
  - **讀哪裡**：Overview 與 Normal API；Advanced API 的 `ma_state` 一節和 xarray 對照著讀。
  - **注意**：文件作者就是 Howlett 與 Wilcox，用語和 xarray 文件一致。

### 部落格 / 技術文章

- **LWN：The XArray data structure** — Jonathan Corbet（2018）與 Wilcox 在 LCA/LSFMM 的相關報導
  - **這篇說什麼**：xarray 合併前後的設計動機——radix tree API 的哪些地方讓使用者一直寫錯。
  - **讀哪裡**：在 LWN Kernel Index 的「XArray」項下按時間順序讀前兩三篇。
- **LWN：Introducing maple trees** — Jonathan Corbet（2021 年 2 月）
  - **這篇說什麼**：maple tree 第一次提出時的介紹，包含 VMA 為什麼需要 range-based、RCU-safe 結構，以及和 rbtree + vmacache 的效能比較動機。
  - **讀哪裡**：整篇；Kernel Index「Maple trees」項下還有後續合併與 per-VMA lock 的文章。

### 書籍

- **《Understanding the Linux Kernel》3rd ed.** — Bovet & Cesati（O'Reilly, 2005）
  - **讀哪幾章**：第 15 章「The Page Cache」講 radix tree 版本的 page cache。資料結構已過時，但「為什麼 page cache 需要以 (inode, index) 為鍵、需要 tag 找 dirty page」的領域背景至今適用——正好補本章的**領域鴻溝**。

前四章的結構都在處理「一群物件怎麼組織」。最後一章換個方向：當問題變成「一群 bit」、「每個 CPU 各一份」、「生產者和消費者之間的一段緩衝」時，kernel 有三個更小、更快、但讀碼時同樣容易誤解的工具。

→ [Ch 27 bitmap、per-cpu 變數與 kfifo](./27-bitmap-percpu-kfifo.md)
