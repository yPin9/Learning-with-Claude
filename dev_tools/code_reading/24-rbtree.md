# Ch 24 — 紅黑樹：rbtree 與 augmented tree

> **目標**：在陌生程式碼裡一眼認出「這段 `while` 迴圈是在 rbtree 上搜尋或找插入點」，從迴圈裡的比較式推回這棵樹的 key 是什麼；理解 kernel rbtree 為什麼要你自己寫比較、`rb_root_cached` 快取了什麼、augmented rbtree 在節點上多存的那個欄位是幹嘛的。

> **鴻溝**：本章主要練**慣用法鴻溝**（`**link`、`rb_link_node`、`rb_insert_color` 這組固定套路）與**領域鴻溝**（EEVDF 排程器為什麼要在樹上多存 `min_vruntime`）。

> **環境**：Linux kernel v6.18 原始碼；user-space 範例直接編譯 v6.18 的 `tools/lib/rbtree.c`（kernel `lib/rbtree.c` 的鏡像，差別只有 `EXPORT_SYMBOL`），gcc 11.4、WSL Ubuntu 22.04 x86_64。

## 為什麼需要這個？

Ch 23 的 list 有一個硬傷：查找是 O(n)，而且不排序。kernel 有很多地方需要「依某個 key 排好，並快速找到最小的、或找到某個值」：

- 排程器要在幾百個可執行的 task 裡挑出 deadline 最早、又符合資格的那一個。
- 高精度計時器（hrtimer）要找出最快到期的計時器。
- epoll 要在十萬個被監看的 fd 裡快速判斷「這個 fd 已經加過了沒」。
- v6.1 之前，每個 process 的 VMA 要依位址排序，page fault 時用位址查出所屬 VMA。

這些都是平衡二元搜尋樹的工作。kernel 選了紅黑樹，實作在 `lib/rbtree.c`，最早由 Andrea Arcangeli 在 1999 年寫成（`include/linux/rbtree.h` 檔頭至今仍保留他的署名）。

讀碼時 rbtree 帶來的困難和 list 不同：list 的走訪全部包在 `list_for_each_entry` 這類巨集裡，認得巨集就認得 list；**rbtree 的搜尋和插入沒有巨集，是每個使用者自己手寫的 `while` 迴圈**。你在 `fs/eventpoll.c` 和 `kernel/sched/fair.c` 看到的是兩段長得不一樣的程式碼，卻在做同一件事。這一章教你辨識那個共同的骨架。

## 先建立直覺

想像一本按姓氏排序的電話簿，但它不是一本書，而是一棵樹：每一頁上寫一個名字，左手邊撕下來的是「比我小的全部」、右手邊是「比我大的全部」。找人時從根開始，比一次就丟掉一半。

紅黑樹在這上面加了一條規則：每頁塗紅色或黑色，並維持「從根到任何葉子經過的黑頁數量一樣多、紅頁不能連續」。這保證最長路徑不超過最短路徑的兩倍，所以樹高永遠是 O(log n)。**讀碼時你幾乎不需要關心顏色**——上色和旋轉全部藏在 `rb_insert_color()` 和 `rb_erase()` 裡，使用者程式碼只處理「找位置」。

```
使用者寫的部分                              kernel lib/rbtree.c 提供的部分
─────────────────                          ─────────────────────────────
1. 從 root 往下走，比 key                   4. rb_insert_color()：上色、旋轉，恢復平衡
2. 記住 parent 與「要接在左還是右」         5. rb_erase()：刪除並恢復平衡
3. rb_link_node()：把新節點接上去           6. rb_first()/rb_next()：中序走訪
   （此時樹可能暫時不平衡）
```

這個分工是 kernel rbtree 最重要的設計決策，也是讀碼時最需要認得的形狀。

## 核心定義

節點只有三個欄位（`include/linux/rbtree_types.h:5-9（v6.18）`）：

```c
struct rb_node {
	unsigned long  __rb_parent_color;
	struct rb_node *rb_right;
	struct rb_node *rb_left;
} __attribute__((aligned(sizeof(long))));
/* The alignment might seem pointless, but allegedly CRIS needs it */
```

沒有 `color` 欄位，也沒有 `parent` 指標——兩者擠在 `__rb_parent_color` 一個 `unsigned long` 裡。因為 `rb_node` 至少對齊到 `sizeof(long)`（8 bytes），任何 `rb_node` 位址的最低 3 個 bit 一定是 0，kernel 就把顏色塞進最低位（`RB_RED` = 0、`RB_BLACK` = 1，`include/linux/rbtree_augmented.h:171-172（v6.18）`），取 parent 時再遮掉（`include/linux/rbtree.h:25（v6.18）`）：

```c
#define rb_parent(r)   ((struct rb_node *)((r)->__rb_parent_color & ~3))
```

這是 [Ch 9](./09-expressions-integers-bits.md) 講過的「指標低位元借用」技巧，Ch 26 的 xarray 會把它用得更徹底。**讀碼時看到 `& ~3`、`& 1` 作用在一個指標轉成的整數上，第一個假設就應該是「低位元藏了 tag」**。

和 list 一樣，`rb_node` 是嵌入物件裡的，從節點回到物件用 `rb_entry`，它就是 `container_of`（`include/linux/rbtree.h:27`）。

## 為什麼 kernel rbtree 不收比較函式

`include/linux/rbtree.h` 的檔頭註解（`include/linux/rbtree.h:1-14（v6.18）`）：

```c
/*
  Red Black Trees
  (C) 1999  Andrea Arcangeli <andrea@suse.de>
  

  linux/include/linux/rbtree.h

  To use rbtrees you'll have to implement your own insert and search cores.
  This will avoid us to use callbacks and to drop drammatically performances.
  I know it's not the cleaner way,  but in C (not in C++) to get
  performances and genericity...

  See Documentation/core-api/rbtree.rst for documentation and samples.
*/
```

`Documentation/core-api/rbtree.rst` 講得更完整（「Linux implementation of red-black trees」一節）：

```
The Linux rbtree implementation is optimized for speed, and thus has one
less layer of indirection (and better cache locality) than more traditional
tree implementations.  Instead of using pointers to separate rb_node and data
structures, each instance of struct rb_node is embedded in the data structure
it organizes.  And instead of using a comparison callback function pointer,
users are expected to write their own tree search and insert functions
which call the provided rbtree functions.  Locking is also left up to the
user of the rbtree code.
```

把設計空間攤開看：

| 方案 | 怎麼比較 | 代價 |
|---|---|---|
| C 標準庫 `tsearch()` / glibc | 傳入 `int (*compar)(const void *, const void *)` | 每次比較一次間接呼叫；不能 inline；key 必須包成物件 |
| C++ `std::map<K, V, Compare>` | 比較子是型別參數，編譯期展開 | C 沒有 template |
| kernel rbtree（1999 年設計） | **使用者自己寫整個下降迴圈** | 每個使用者重複寫十幾行；寫錯（左右方向不一致）編譯器抓不到 |
| kernel `rb_find()`/`rb_add()`（v5.12 起） | `__always_inline` 函式收一個比較函式指標，編譯器在呼叫點展開並 inline 比較 | 依賴編譯器確實 inline；舊程式碼不會自動改寫 |

1999 年的 C 編譯器沒辦法保證把「透過函式指標呼叫的比較」inline 掉，而排程器、計時器這種熱路徑每次比較多一次間接跳躍是不能接受的（2018 年之後 Spectre 的 retpoline 讓間接呼叫更貴，這個理由反而更強了）。所以 Arcangeli 選擇把迴圈交給使用者。

這個設計在 v6.18 有了變化，**讀 v6.18 程式碼時兩種風格都會遇到**。Peter Zijlstra 在 2020 年加了一組通用 helper（commit `2d24dd5798d0`「rbtree: Add generic add and find helpers」，進入 v5.12）。以 `rb_find` 為例（`include/linux/rbtree.h:328-347（v6.18）`）：

```c
static __always_inline struct rb_node *
rb_find(const void *key, const struct rb_root *tree,
	int (*cmp)(const void *key, const struct rb_node *))
{
	struct rb_node *node = tree->rb_node;

	while (node) {
		int c = cmp(key, node);

		if (c < 0)
			node = node->rb_left;
		else if (c > 0)
			node = node->rb_right;
		else
			return node;
	}

	return NULL;
}
```

它表面上收了比較函式指標，但整個函式是 `__always_inline`，呼叫點傳入的通常是一個 `static inline` 函式，編譯器展開後函式指標會被常數傳播掉，比較直接 inline 進迴圈。**換句話說，它仍然是「每個使用者一份專屬迴圈」，只是由編譯器替你複製貼上**。舊程式碼（例如 eventpoll）沒有改寫，新程式碼（例如 EEVDF）用 helper。

## 辨識骨架：手寫的搜尋與插入迴圈

先看舊風格。epoll 用 rbtree 存「這個 epoll instance 監看了哪些 (file, fd)」，key 的比較函式在 `fs/eventpoll.c:358-363（v6.18）`：

```c
static inline int ep_cmp_ffd(struct epoll_filefd *p1,
			     struct epoll_filefd *p2)
{
	return (p1->file > p2->file ? +1:
	        (p1->file < p2->file ? -1 : p1->fd - p2->fd));
}
```

搜尋（`fs/eventpoll.c:1174-1196（v6.18）`）：

```c
static struct epitem *ep_find(struct eventpoll *ep, struct file *file, int fd)
{
	int kcmp;
	struct rb_node *rbp;
	struct epitem *epi, *epir = NULL;
	struct epoll_filefd ffd;

	ep_set_ffd(&ffd, file, fd);
	for (rbp = ep->rbr.rb_root.rb_node; rbp; ) {
		epi = rb_entry(rbp, struct epitem, rbn);
		kcmp = ep_cmp_ffd(&ffd, &epi->ffd);
		if (kcmp > 0)
			rbp = rbp->rb_right;
		else if (kcmp < 0)
			rbp = rbp->rb_left;
		else {
			epir = epi;
			break;
		}
	}

	return epir;
}
```

插入（`fs/eventpoll.c:1385-1404（v6.18）`）：

```c
static void ep_rbtree_insert(struct eventpoll *ep, struct epitem *epi)
{
	int kcmp;
	struct rb_node **p = &ep->rbr.rb_root.rb_node, *parent = NULL;
	struct epitem *epic;
	bool leftmost = true;

	while (*p) {
		parent = *p;
		epic = rb_entry(parent, struct epitem, rbn);
		kcmp = ep_cmp_ffd(&epi->ffd, &epic->ffd);
		if (kcmp > 0) {
			p = &parent->rb_right;
			leftmost = false;
		} else
			p = &parent->rb_left;
	}
	rb_link_node(&epi->rbn, parent, p);
	rb_insert_color_cached(&epi->rbn, &ep->rbr, leftmost);
}
```

把這兩段抽象化，就是你在 kernel 各處都會看到的骨架：

```
搜尋骨架                                   插入骨架
──────────                                 ──────────
node = root->rb_node                       struct rb_node **link = &root->rb_node, *parent = NULL
while (node) {                             while (*link) {
    obj = rb_entry(node, T, member)            parent = *link
    c = compare(key, obj)                      obj = rb_entry(parent, T, member)
    if (c < 0)  node = node->rb_left           if (less(new, obj))  link = &parent->rb_left
    elif (c > 0) node = node->rb_right         else                 link = &parent->rb_right
    else return obj                        }
}                                          rb_link_node(&new->member, parent, link)
return NULL                                rb_insert_color(&new->member, root)
```

辨識特徵（beacon）：

1. **`->rb_left` / `->rb_right` 出現在 `while` 迴圈裡的賦值右邊**——這幾乎一定是 rbtree 下降。
2. **`struct rb_node **`**（指標的指標）——插入迴圈的招牌。它記住「要把新節點接在哪個欄位」，迴圈結束時 `*link` 一定是 `NULL`，`rb_link_node` 把它填上。和 Ch 23 的 `hlist::pprev` 是同一個「指向那個指標」的思路。
3. **迴圈後緊跟 `rb_link_node` + `rb_insert_color*`**——插入的收尾。
4. **`rb_entry(x, T, member)`** 的 `T` 與 `member` 告訴你樹上掛的是什麼、經由哪個成員——和 Ch 23 的 list 問題完全同構。

讀出這棵樹的 key：看迴圈裡被比較的是什麼。`ep_cmp_ffd` 先比 `file` 指標、再比 `fd`，所以 **epoll 樹的 key 是 (struct file *, int fd) 這一對**。這個發現有實際意義：同一個 `struct file` 用 `dup()` 出不同的 fd，可以各自加進同一個 epoll；同一個 fd 號碼關掉重開成不同 file，也是不同 key。[練習 D](./practice-d-data-structure-map.md) 會用 `/proc/self/fdinfo` 實際驗證這棵樹的排序。

## 實際跑：自己寫迴圈，順便故意寫錯

用 v6.18 的 `tools/lib/rbtree.c`（就是 `lib/rbtree.c` 去掉 `EXPORT_SYMBOL`，`diff` 只差 44 行，全是 export）直接編進 user-space 程式：

```c
/* rbdemo.c：用 v6.18 tools/lib/rbtree.c 示範「自己寫搜尋與插入迴圈」 */
#include <stdio.h>
#include <stdlib.h>
#include <linux/rbtree.h>

struct timer {                 /* 假想的計時器：依 expires 排序 */
	unsigned long expires;
	const char *name;
	struct rb_node node;       /* 嵌入式節點，跟 list_head 一樣 */
};

static struct rb_root_cached timers = RB_ROOT_CACHED;

/* 插入迴圈：使用者自己寫比較；相同 expires 一律往右，保持穩定順序 */
static void timer_insert(struct timer *t)
{
	struct rb_node **link = &timers.rb_root.rb_node, *parent = NULL;
	bool leftmost = true;

	while (*link) {
		struct timer *cur = rb_entry(*link, struct timer, node);
		parent = *link;
		if (t->expires < cur->expires) {
			link = &parent->rb_left;
		} else {
			link = &parent->rb_right;
			leftmost = false;
		}
	}
	rb_link_node(&t->node, parent, link);
	rb_insert_color_cached(&t->node, &timers, leftmost);
}

/* 搜尋迴圈：同樣是使用者自己寫 */
static struct timer *timer_find(unsigned long expires)
{
	struct rb_node *n = timers.rb_root.rb_node;
	while (n) {
		struct timer *cur = rb_entry(n, struct timer, node);
		if (expires < cur->expires)
			n = n->rb_left;
		else if (expires > cur->expires)
			n = n->rb_right;
		else
			return cur;
	}
	return NULL;
}

/* 故意寫錯：搜尋方向和插入方向相反 */
static struct timer *timer_find_buggy(unsigned long expires)
{
	struct rb_node *n = timers.rb_root.rb_node;
	while (n) {
		struct timer *cur = rb_entry(n, struct timer, node);
		if (expires > cur->expires)
			n = n->rb_left;
		else if (expires < cur->expires)
			n = n->rb_right;
		else
			return cur;
	}
	return NULL;
}

int main(void)
{
	unsigned long keys[] = { 50, 20, 80, 10, 30, 70, 90, 60, 40 };
	const char *names[] = { "e", "b", "h", "a", "c", "g", "i", "f", "d" };
	for (int i = 0; i < 9; i++) {
		struct timer *t = malloc(sizeof(*t));
		t->expires = keys[i]; t->name = names[i];
		timer_insert(t);
	}

	printf("in-order:");
	for (struct rb_node *n = rb_first_cached(&timers); n; n = rb_next(n))
		printf(" %lu", rb_entry(n, struct timer, node)->expires);
	printf("\nleftmost (O(1)): %lu\n",
	       rb_entry(rb_first_cached(&timers), struct timer, node)->expires);

	struct rb_node *root = timers.rb_root.rb_node;
	struct timer *rt = rb_entry(root, struct timer, node);
	printf("root=%lu  __rb_parent_color=%#lx (low bit = color: %s)\n",
	       rt->expires, root->__rb_parent_color,
	       (root->__rb_parent_color & 1) ? "black" : "red");
	struct rb_node *l = root->rb_left;
	printf("root->left=%lu parent_color=%#lx  rb_parent()==root? %d  color=%s\n",
	       rb_entry(l, struct timer, node)->expires, l->__rb_parent_color,
	       rb_parent(l) == root, (l->__rb_parent_color & 1) ? "black" : "red");

	unsigned long q[] = { 30, 60, 55 };
	for (int i = 0; i < 3; i++) {
		struct timer *f = timer_find(q[i]), *g = timer_find_buggy(q[i]);
		printf("find(%lu): %s   buggy_find(%lu): %s\n", q[i],
		       f ? f->name : "NULL", q[i], g ? g->name : "NULL");
	}
	return 0;
}
```

```
$ L=~/code_reading/linux/tools
$ gcc -O1 -Wall -I$L/include -o rbdemo rbdemo.c $L/lib/rbtree.c && ./rbdemo
in-order: 10 20 30 40 50 60 70 80 90
leftmost (O(1)): 10
root=50  __rb_parent_color=0x1 (low bit = color: black)
root->left=20 parent_color=0x57e857a3f2b0  rb_parent()==root? 1  color=red
find(30): c   buggy_find(30): NULL
find(60): f   buggy_find(60): NULL
find(55): NULL   buggy_find(55): NULL
```

從輸出可以讀到四件事：

1. 中序走訪（`rb_first` + `rb_next`）自然得到排序結果，這是二元搜尋樹的定義。
2. root 的 `__rb_parent_color` 是 `0x1`：parent 為 `NULL`（高位全 0）、顏色是黑（低位 1）。紅黑樹的根永遠是黑的。
3. root 左孩子的 `__rb_parent_color` = `0x57e857a3f2b0`，最低位是 0（紅），遮掉低 2 位後等於 root 的位址。一個欄位存兩件事，親眼看到了。
4. **方向寫反的搜尋，所有存在的 key 都找不到**，而且不會 crash、不會有任何警告。這就是「把迴圈交給使用者」的代價。在 kernel 裡這類 bug 的表現會是「明明加進去了卻找不到」——例如 epoll 對同一個 fd 重複 `EPOLL_CTL_ADD` 沒有回 `EEXIST`。讀碼時遇到手寫的 rbtree，**一定要把搜尋迴圈和插入迴圈並排，核對比較方向一致**。

## `rb_root_cached`：快取最左節點

`include/linux/rbtree_types.h:16-29（v6.18）`：

```c
/*
 * Leftmost-cached rbtrees.
 *
 * We do not cache the rightmost node based on footprint
 * size vs number of potential users that could benefit
 * from O(1) rb_last(). Just not worth it, users that want
 * this feature can always implement the logic explicitly.
 * Furthermore, users that want to cache both pointers may
 * find it a bit asymmetric, but that's ok.
 */
struct rb_root_cached {
	struct rb_root rb_root;
	struct rb_node *rb_leftmost;
};
```

很多使用者真正想要的是「最小的那個」：最早到期的計時器、deadline 最早的 task。在普通 rbtree 上找最小值要從根一路往左走，O(log n)；快取起來就是 O(1)（`#define rb_first_cached(root) (root)->rb_leftmost`，`include/linux/rbtree.h:106（v6.18）`）。

維護成本落在插入迴圈上：你得在下降時自己追蹤「我有沒有往右走過」，只要往右走過一次，新節點就不可能是最左。所以 cached 版插入迴圈多了一個 `bool leftmost` 變數，往右時設成 `false`，最後傳給 `rb_insert_color_cached()`（`include/linux/rbtree.h:108-115（v6.18）`）：

```c
static inline void rb_insert_color_cached(struct rb_node *node,
					  struct rb_root_cached *root,
					  bool leftmost)
{
	if (leftmost)
		root->rb_leftmost = node;
	rb_insert_color(node, &root->rb_root);
}
```

**讀碼時看到插入迴圈裡有 `leftmost = false`，就知道是 cached 樹**，也知道作者在乎「最小值」。`rb_root_cached` 由 Davidlohr Bueso 在 2017 年整合進核心 rbtree（commit `cd9e61ed1eeb`「rbtree: cache leftmost node internally」，v4.14）；在那之前，CFS 等使用者各自在自己的 struct 裡存一個 `rb_leftmost` 指標，同一套邏輯散落在好幾個子系統。

## 真實例子：EEVDF 的 `tasks_timeline`

v6.6 起，Linux 的公平排程類從 CFS 換成 EEVDF（Earliest Eligible Virtual Deadline First），但資料結構仍然是一棵 cached rbtree，掛在每個 CPU runqueue 的 `struct cfs_rq` 上（`kernel/sched/sched.h:691（v6.18）`）：

```c
	struct rb_root_cached	tasks_timeline;
```

樹上掛的是 `struct sched_entity`，經由 `run_node` 成員（`include/linux/sched.h:573`：`struct rb_node run_node;`）。從節點回到物件的轉換被包成一個巨集（`kernel/sched/fair.c:560-561（v6.18）`）：

```c
#define __node_2_se(node) \
	rb_entry((node), struct sched_entity, run_node)
```

**讀碼提示**：子系統常常把 `rb_entry(x, T, member)` 包成 `__node_2_xxx()`、`to_xxx()` 這類短名字。grep `rb_entry.*run_node` 找不到東西時，改 grep `run_node`。

key 是什麼？看比較函式（`kernel/sched/fair.c:545-553、796-799（v6.18）`）：

```c
static inline bool entity_before(const struct sched_entity *a,
				 const struct sched_entity *b)
{
	/*
	 * Tiebreak on vruntime seems unnecessary since it can
	 * hardly happen.
	 */
	return (s64)(a->deadline - b->deadline) < 0;
}
...
static inline bool __entity_less(struct rb_node *a, const struct rb_node *b)
{
	return entity_before(__node_2_se(a), __node_2_se(b));
}
```

樹按 **virtual deadline** 排序。注意比較寫成 `(s64)(a - b) < 0` 而不是 `a < b`：`deadline` 是 `u64`，持續遞增可能繞回，相減後轉成有號數比較，能在繞回時仍得到正確順序（[Ch 9](./09-expressions-integers-bits.md) 的整數技巧；Ch 27 的 kfifo 會用同一個思路）。

插入用的是新風格 helper（`kernel/sched/fair.c:848-855（v6.18）`）：

```c
static void __enqueue_entity(struct cfs_rq *cfs_rq, struct sched_entity *se)
{
	avg_vruntime_add(cfs_rq, se);
	se->min_vruntime = se->vruntime;
	se->min_slice = se->slice;
	rb_add_augmented_cached(&se->run_node, &cfs_rq->tasks_timeline,
				__entity_less, &min_vruntime_cb);
}
```

這裡沒有手寫迴圈，因為 `rb_add_augmented_cached` 內部就是上面那個插入骨架。但它多了兩個東西：`augmented` 和 `&min_vruntime_cb`。這就是下一節。

## Augmented rbtree：在每個節點上多存一個「子樹摘要」

EEVDF 的挑選規則是：**在所有 eligible（`vruntime` 不超過平均值，也就是「沒有欠時間」）的 entity 中，挑 deadline 最早的**。樹按 deadline 排序，最左的 deadline 最早，但它不一定 eligible。如果只靠排序，最壞情況要一個一個往右檢查，O(n)。

解法是讓每個節點多存一個欄位：**以我為根的子樹中，最小的 `vruntime` 是多少**（`se->min_vruntime`）。有了它，下降時看左子樹的 `min_vruntime` 就能立刻知道「左邊有沒有任何 eligible 的 entity」，沒有就整棵左子樹跳過。這叫 augmented tree：**節點除了 key 之外，還存一個由子樹計算出來、可以在旋轉時局部更新的值**。

維護規則寫在 `kernel/sched/fair.c:821-843（v6.18）`：

```c
/*
 * se->min_vruntime = min(se->vruntime, {left,right}->min_vruntime)
 */
static inline bool min_vruntime_update(struct sched_entity *se, bool exit)
{
	u64 old_min_vruntime = se->min_vruntime;
	u64 old_min_slice = se->min_slice;
	struct rb_node *node = &se->run_node;

	se->min_vruntime = se->vruntime;
	__min_vruntime_update(se, node->rb_right);
	__min_vruntime_update(se, node->rb_left);

	se->min_slice = se->slice;
	__min_slice_update(se, node->rb_right);
	__min_slice_update(se, node->rb_left);

	return se->min_vruntime == old_min_vruntime &&
	       se->min_slice == old_min_slice;
}

RB_DECLARE_CALLBACKS(static, min_vruntime_cb, struct sched_entity,
		     run_node, min_vruntime, min_vruntime_update);
```

`RB_DECLARE_CALLBACKS` 是 `include/linux/rbtree_augmented.h:89-100（v6.18）` 定義的模板巨集，參數意義直接寫在註解裡：

```c
/*
 * Template for declaring augmented rbtree callbacks (generic case)
 *
 * RBSTATIC:    'static' or empty
 * RBNAME:      name of the rb_augment_callbacks structure
 * RBSTRUCT:    struct type of the tree nodes
 * RBFIELD:     name of struct rb_node field within RBSTRUCT
 * RBAUGMENTED: name of field within RBSTRUCT holding data for subtree
 * RBCOMPUTE:   name of function that recomputes the RBAUGMENTED data
 */
```

它展開後產生三個 callback（propagate、copy、rotate），讓 `lib/rbtree.c` 在每次旋轉或刪除時呼叫，把 `min_vruntime` 沿著被影響的路徑重算。這是 [Ch 14](./14-macro-metaprogramming.md) 講的「巨集生成程式碼」的典型應用：**讀碼時 grep `min_vruntime_cb_propagate` 會找不到定義，因為它是 `RB_DECLARE_CALLBACKS` 用 token pasting 造出來的**。

有了這個欄位，挑選函式就能剪枝（`kernel/sched/fair.c:970-997（v6.18）`，`__pick_eevdf` 的核心）：

```c
	/* Heap search for the EEVD entity */
	while (node) {
		struct rb_node *left = node->rb_left;

		/*
		 * Eligible entities in left subtree are always better
		 * choices, since they have earlier deadlines.
		 */
		if (left && vruntime_eligible(cfs_rq,
					__node_2_se(left)->min_vruntime)) {
			node = left;
			continue;
		}

		se = __node_2_se(node);

		/*
		 * The left subtree either is empty or has no eligible
		 * entity, so check the current node since it is the one
		 * with earliest deadline that might be eligible.
		 */
		if (entity_eligible(cfs_rq, se)) {
			best = se;
			break;
		}

		node = node->rb_right;
	}
```

用本章的辨識法讀它：`while (node)`、`node->rb_left`、`node->rb_right`——這是 rbtree 下降。但比較的不是 key（deadline），而是 augmented 欄位（`left->min_vruntime`）。**看到下降迴圈裡比較的是子節點的某個「彙總」欄位，而不是要找的 key，就是 augmented tree 查詢**。原始碼註解叫它「Heap search」，因為 `min_vruntime` 在樹上滿足 heap 性質（父節點的值 ≤ 子樹所有值）。

## Interval tree：augmented rbtree 最常見的特化

另一個常見的 augmented 用途是**區間樹**：節點存一個區間 `[start, last]`，augmented 欄位存「子樹中最大的 `last`」，查詢「哪些區間和 `[a, b]` 重疊」時能剪掉整棵子樹。通用版定義在 `include/linux/interval_tree.h:7-12（v6.18）`：

```c
struct interval_tree_node {
	struct rb_node rb;
	unsigned long start;	/* Start of interval */
	unsigned long last;	/* Last location _in_ interval */
	unsigned long __subtree_last;
};
```

但 mm 子系統用的是模板巨集 `INTERVAL_TREE_DEFINE`（`include/linux/interval_tree_generic.h:28`），直接把 VMA 變成區間樹節點（`mm/interval_tree.c:23-25（v6.18）`）：

```c
INTERVAL_TREE_DEFINE(struct vm_area_struct, shared.rb,
		     unsigned long, shared.rb_subtree_last,
		     vma_start_pgoff, vma_last_pgoff, /* empty */, vma_interval_tree)
```

這棵樹的 root 就是 Ch 26 會看到的 `struct address_space` 裡的 `struct rb_root_cached i_mmap;`（`include/linux/fs.h:516（v6.18）`）。用途：一個檔案的某一頁被換出時，kernel 要找出**所有把這一頁 mmap 進來的 VMA**（可能跨很多 process），這是一個區間重疊查詢。讀碼時看到 `vma_interval_tree_foreach`（`include/linux/mm.h:3384`），就知道它在 i_mmap 這棵區間樹上找重疊的 VMA。

## 對比與取捨

| 結構 | 查找 | 有序走訪 | 最小值 | 記憶體/節點 | kernel 典型用途 |
|---|---|---|---|---|---|
| `list_head` | O(n) | 只有插入順序 | O(n)（除非維持排序） | 2 指標 | 小集合、FIFO |
| rbtree | O(log n) | 是 | O(log n) | 3 字（parent+color 合併） | 需要排序的動態集合 |
| `rb_root_cached` | O(log n) | 是 | **O(1)** | root 多 1 指標 | 排程器、hrtimer、epoll |
| augmented rbtree | O(log n) | 是 | — | 每節點多一個彙總欄位 | EEVDF、interval tree |
| hash table（Ch 25） | 平均 O(1) | 否 | O(n) | 1–2 指標 | 純 key 查找 |
| maple tree（Ch 26） | O(log n)，高扇出 | 是 | — | B-tree 節點 256 bytes | VMA（v6.1 起取代 rbtree） |

rbtree.rst 自己也說明為什麼選紅黑樹而不選 AVL：插入最多兩次旋轉、刪除最多三次，最壞情況的修改成本有界，查找稍慢但仍是 O(log n)。對 kernel 這種「修改和查詢一樣頻繁、且在意最壞延遲」的場景，這個取捨合理。

## 踩雷集錦

1. **以為 kernel rbtree 有 `rb_insert(tree, node, cmp)` 這種 API**：有 `rb_add`/`rb_find`（v5.12 起），但大量既有程式碼仍是手寫迴圈。讀碼時兩種都要認得；找「這棵樹的插入點」時，grep `rb_link_node` 和 `rb_add` 都要。
2. **搜尋與插入的比較方向不一致**：上面的 `buggy_find` 示範了，不會 crash、只是找不到。審 code 時並排比對。
3. **`rb_erase` 之後節點還「看起來在樹上」**：`rb_erase` 不會清掉被刪節點的指標。需要「我在不在樹上」語意時，刪除後要 `RB_CLEAR_NODE`，判斷時用 `RB_EMPTY_NODE`（`include/linux/rbtree.h:31-35`）。這和 list 的 `list_del` vs `list_del_init` 是同一件事。
4. **rbtree 不含鎖**：rbtree.rst 明說「Locking is also left up to the user」。每棵樹都有一把外部的鎖（epoll 是 `ep->mtx`，`ep_find` 的註解寫著「must be called with "mtx" held」；cfs_rq 是 runqueue 的 `rq->__lock`）。讀碼時要找出是哪一把。
5. **以為 `rb_find_rcu` 可以拿來做無鎖查找**：它的註解（`include/linux/rbtree.h:349-357`）明講「tree descent vs concurrent tree rotations is unsound and can result in false-negatives」。旋轉期間無鎖讀者可能錯過存在的節點，只適合「找不到也沒關係、會再用有鎖路徑重試」的場合，或搭配 seqcount 使用。

## 進階：再往深一層

- **`rb_add_augmented_cached` 為什麼能同時是 cached 與 augmented**：讀 `include/linux/rbtree_augmented.h:60-87`，它在插入骨架上同時維護 `leftmost` 和呼叫 `augment->propagate`。對照本章的骨架圖，你應該能逐行對上。
- **`lib/rbtree.c` 的旋轉實作**：檔案不到 600 行，註解畫了每種情況的 ASCII 圖。想真正理解紅黑樹再讀；讀 kernel 使用者程式碼時不需要。
- **latch tree（`include/linux/rbtree_latch.h`）**：兩棵 rbtree 加 seqcount，讓讀者完全無鎖且不會漏掉節點，用在 module 位址查找。它是「rbtree 不能直接 RCU」問題的一個解法。
- **EEVDF 的理論背景**：Stoica 與 Abdel-Wahab 1995 年的技術報告。想讀懂 `avg_vruntime`、`entity_eligible` 的數學，需要它。

## 動手練習

1. 在 `rbdemo.c` 加一個 `timer_find_first_ge(x)`：找出 `expires >= x` 的最小節點。提示：它的迴圈和 `ep_find` 很像，但找到候選時不能 `break`，要記下來繼續往左走。對照 v6.0 的 `find_vma`（`git show v6.0:mm/mmap.c`，約 2253 行），它就是這個寫法。
2. 在 v6.18 tree 找出 `struct hrtimer_clock_base` 用的是 `rb_root_cached` 還是 `timerqueue_head`。順著 `timerqueue_add` 讀下去，回答它的 key 是什麼、比較函式在哪。
3. 把 `rbdemo.c` 的插入改成用 `rb_add_cached()` 與一個 `less` 函式，確認輸出一致。再用 `gcc -O2 -S` 看 `less` 有沒有被 inline 進迴圈。

## 本章重點整理

- kernel rbtree 是侵入式的，而且把搜尋/插入迴圈交給使用者；`lib/rbtree.c` 只負責重新平衡。
- 辨識骨架：`while` 裡出現 `->rb_left`/`->rb_right`、插入用 `struct rb_node **link`、收尾是 `rb_link_node` + `rb_insert_color*`。迴圈裡比較的東西就是 key。
- v5.12 起有 `rb_add`/`rb_find` 這類 `__always_inline` helper，本質仍是每個使用者一份專屬迴圈。
- `rb_root_cached` 讓最小值 O(1)，插入迴圈裡的 `leftmost = false` 是它的標記。
- augmented tree 在節點上存子樹彙總值以便剪枝；EEVDF 用它存 `min_vruntime`，interval tree 用它存 `__subtree_last`。

## 自我檢核

- [ ] 不看原始碼，寫出 rbtree 插入迴圈的骨架，並解釋為什麼 `link` 是 `struct rb_node **`。
- [ ] 有人問「kernel 的 rbtree 為什麼不像 qsort 一樣收比較函式？」，你能講出 1999 年的理由和 2020 年之後的變化嗎？
- [ ] 給你一段陌生的下降迴圈，你怎麼判斷它是普通查找、cached 插入，還是 augmented 查詢？
- [ ] 說明 EEVDF 為什麼不能只靠「按 deadline 排序」就挑出下一個 task，augmented 欄位解決了什麼。
- [ ] epoll 的 rbtree key 是什麼？這個選擇對 `dup()` 出來的 fd 有什麼影響？

## 延伸閱讀

### 官方文件

- **[Red-black Trees (rbtree) in Linux](https://docs.kernel.org/core-api/rbtree.html)**（v6.18 tree：`Documentation/core-api/rbtree.rst`）
  - **讀哪裡**：「Searching for a value」「Inserting data」兩節給出標準迴圈範本；「Cached rbtrees」與「Support for Augmented rbtrees」兩節對應本章後半。
  - **前提**：會二元搜尋樹的概念即可。
- **[EEVDF Scheduler](https://docs.kernel.org/scheduler/sched-eevdf.html)**（`Documentation/scheduler/sched-eevdf.rst`）
  - **讀哪裡**：整篇（很短）。說明 eligible、lag、virtual deadline 的定義，讀完再回來看 `__pick_eevdf` 會清楚很多。

### 論文

- **Earliest Eligible Virtual Deadline First: A Flexible and Accurate Mechanism for Proportional Share Resource Allocation** — Ion Stoica, Hussein Abdel-Wahab（Old Dominion University 技術報告 TR-95-22，1995）
  - **核心貢獻**：提出以「虛擬時間 + 資格判定 + 虛擬截止時間」做比例分享排程，並證明 lag 有界。
  - **讀哪裡**：第 2–4 節的演算法定義；證明可略。
  - **和本章的關聯**：`tasks_timeline` 的排序鍵（deadline）與 augmented 欄位（min vruntime）就是論文裡「eligible + earliest deadline」兩個條件的資料結構實現。

### 部落格 / 技術文章

- **LWN：[Trees II: red-black trees](https://lwn.net/Articles/184495/)** — Rob Landley（2006）
  - **這篇說什麼**：rbtree.rst 自己引用的那篇。用 kernel 的 API 講紅黑樹怎麼用、為什麼要自己寫迴圈。
  - **注意**：API 是 2.6.17 時代的，沒有 `rb_root_cached` 和 helper，但骨架完全一樣。
- **LWN：An EEVDF CPU scheduler for Linux** — Jonathan Corbet（2023 年 3 月）
  - **這篇說什麼**：Peter Zijlstra 提出 EEVDF patch series 時的介紹，解釋為什麼要從 CFS 換過去、lag 與 eligibility 的直覺。
  - **讀哪裡**：整篇；可在 LWN Kernel Index 的「Scheduler/EEVDF」項下找到。

### 書籍

- **《Introduction to Algorithms》（CLRS）** — Cormen, Leiserson, Rivest, Stein（MIT Press，第 3 或第 4 版）
  - **讀哪幾章**：第 13 章（紅黑樹）與第 14 章「Augmenting Data Structures」（第 4 版改為第 17 章），interval tree 的經典講法就在那裡。本章的 augmented 概念直接對應。

rbtree 解決了「有序」，但如果你要的只是「給 key 找物件」，連 O(log n) 都嫌慢。下一章的 hash table 建在 Ch 23 的 hlist 上，而讀它時最容易犯的錯，恰好和 bucket 的設計有關。

→ [Ch 25 hash table：hashtable.h 與 rhashtable](./25-hash-tables.md)
