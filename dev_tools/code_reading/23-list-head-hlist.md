# Ch 23 — 侵入式鏈結串列：list_head 與 hlist

> **目標**：看到任何一個 `struct list_head` 成員，能回答「它串的是什麼型別的物件、經由哪個成員串的」，並在 kernel tree 裡找到這條 list 的所有插入點、走訪點與刪除點；同時理解 `hlist` 的 `pprev` 為什麼長那樣、`_safe` 與 `_rcu` 變體各自在保護什麼。

> **鴻溝**：本章主要練**慣用法鴻溝**（`list_for_each_entry` 的三個參數各代表什麼）與**間接鴻溝**（`list_head` 本身不帶型別，型別資訊散落在呼叫點，要靠工具找回來）。

> **環境**：Linux kernel v6.18 原始碼（`~/code_reading/linux`），user-space 範例用 gcc 11.4 在 WSL Ubuntu 22.04 x86_64 上跑。

## 為什麼需要這個？

Spinellis 在《Code Reading》第 4 章講資料結構時，有一個觀點貫穿整章：**程式碼是對資料的操作，先看懂資料，操作就變得可以預測**。這句話在 kernel 裡要再加一個註腳：kernel 的資料結構幾乎全部是**侵入式（intrusive）**的，而侵入式結構有一個讓初學者非常痛苦的特性——**型別資訊不在宣告裡**。

看這一段 `struct task_struct` 的宣告（`include/linux/sched.h:1080-1093（v6.18）`）：

```c
	/*
	 * Children/sibling form the list of natural children:
	 */
	struct list_head		children;
	struct list_head		sibling;
	struct task_struct		*group_leader;

	/*
	 * 'ptraced' is the list of tasks this task is using ptrace() on.
	 *
	 * This includes both natural children and PTRACE_ATTACH targets.
	 * 'ptrace_entry' is this task's link on the p->parent->ptraced list.
	 */
	struct list_head		ptraced;
	struct list_head		ptrace_entry;
```

四個成員全是 `struct list_head`。如果你習慣 user-space 的寫法，你會預期看到 `struct task_struct *children` 或 `List<Task> children`——型別直接寫在宣告上。這裡沒有。`children` 到底串著什麼？`sibling` 又是幹嘛的？兩個是同一條 list 的兩端，還是兩條不同的 list？宣告本身**不會告訴你**，運氣好有註解，運氣不好什麼都沒有。

這就是本章要解決的問題。答案藏在三個地方：`list_add*()` 的呼叫點、`list_for_each_entry()` 的第三個參數、以及 `list_entry()`/`container_of()` 的型別參數。學會去這三個地方找，你就能把一個「全部都是 `list_head`」的 struct 還原成一張有型別的圖。

在 list.h 出現之前，C 程式員怎麼寫鏈結串列？每種物件寫一套：`struct inode *i_next, *i_prev;`、`struct buffer_head *b_next;`……每一套都有自己的插入、刪除函式，每一套都可能有自己的 bug。`include/linux/list.h` 這套通用的雙向環狀串列在 Linux 的 git 歷史起點（v2.6.12，2005）之前就已存在，把「串列操作」和「物件型別」切開——串列操作只管 `struct list_head`，型別轉換交給 `container_of`。代價就是上面那個「宣告裡看不出型別」的問題。

## 先建立直覺

想像一間圖書館，每本書的書背上都縫了兩個掛勾（`next`、`prev`）。要把書排成一列，就把相鄰書的掛勾互相扣起來。**串列不擁有書，書自己帶著掛勾。**

非侵入式（user-space 教科書寫法）則是另一種做法：準備一串空的夾子（node），每個夾子夾著一本書的指標。書本身完全不知道自己被夾在哪。

```
非侵入式（教科書）                       侵入式（kernel list_head）

 head                                    head (struct list_head，不屬於任何物件)
  │                                       │
  ▼                                       ▼
┌──────┐   ┌──────┐   ┌──────┐          ┌──────────────┐   ┌──────────────┐
│ node │──▶│ node │──▶│ node │          │ struct task  │   │ struct task  │
│ data─┼┐  │ data─┼┐  │ data─┼┐         │  pid = 100   │   │  pid = 101   │
└──────┘│  └──────┘│  └──────┘│         │ ┌──────────┐ │   │ ┌──────────┐ │
        ▼          ▼          ▼         │ │ sibling  │◀┼───┼▶│ sibling  │ │
     [task]     [task]     [task]       │ │ next/prev│ │   │ │ next/prev│ │
                                        │ └──────────┘ │   │ └──────────┘ │
 每個節點多一次 malloc、多一次指標跳躍       └──────────────┘   └──────────────┘
                                         list_head 指向的是「另一個物件裡的 list_head」
                                         不是物件開頭 → 要用 container_of 倒推回物件
```

右圖有一個關鍵細節：**`next` 指向的是下一個物件內部的 `sibling` 成員，不是下一個物件的開頭**。所以從 `next` 拿到的指標，要減掉 `sibling` 在 struct 裡的位移，才能得到物件本身。這就是 `container_of`。

> 如果你對 `container_of` 的展開還不熟，先回看 [Ch 16 — container_of：kernel 最重要的一個巨集](./16-container-of.md)。本章把它當成已知工具。

這個比喻有一個限制：真正的 kernel list 是**環狀**的，head 本身也是一個 `list_head`，最後一個元素的 `next` 繞回 head。所以「走到底」的判斷不是 `next == NULL`，而是「回到 head 了沒」。這點在後面讀 `list_for_each_entry` 的終止條件時很重要。

## list_head 的全部定義

整個資料結構只有兩個指標（`include/linux/types.h:199-201（v6.18）`）：

```c
struct list_head {
	struct list_head *next, *prev;
};
```

插入的核心是 `__list_add`，`list_add` 和 `list_add_tail` 只是決定插在 head 的哪一邊（`include/linux/list.h:154-191（v6.18）`）：

```c
static inline void __list_add(struct list_head *new,
			      struct list_head *prev,
			      struct list_head *next)
{
	if (!__list_add_valid(new, prev, next))
		return;

	next->prev = new;
	new->next = next;
	new->prev = prev;
	WRITE_ONCE(prev->next, new);
}

static inline void list_add(struct list_head *new, struct list_head *head)
{
	__list_add(new, head, head->next);
}

static inline void list_add_tail(struct list_head *new, struct list_head *head)
{
	__list_add(new, head->prev, head);
}
```

讀這段時注意三件事：

1. **`list_add` 插在 head 後面（變成第一個）、`list_add_tail` 插在 head 前面（因為環狀，head 前面就是最後一個）**。原始碼註解直接寫了用途：前者適合做 stack、後者適合做 queue。讀碼時看到哪一個，就知道作者要的是 LIFO 還是 FIFO 語意。
2. **`__list_add_valid` 是 `CONFIG_LIST_HARDENED` 的檢查點**。沒開這個選項時它直接 `return true`（`include/linux/list.h:136-146（v6.18）`那一段 `#else` 分支），編譯後完全消失。
3. **最後一行用 `WRITE_ONCE`**：讓「新節點已經接上」這個寫入對無鎖讀者是單一、不被撕裂的寫入。這是為了 RCU 版本與 `list_empty_careful()` 之類的無鎖檢查鋪路，見 [Ch 21 — READ_ONCE 與 memory barrier](./21-read-once-and-barriers.md)。

刪除（`include/linux/list.h:201-239（v6.18）`）：

```c
static inline void __list_del(struct list_head * prev, struct list_head * next)
{
	next->prev = prev;
	WRITE_ONCE(prev->next, next);
}
...
static inline void list_del(struct list_head *entry)
{
	__list_del_entry(entry);
	entry->next = LIST_POISON1;
	entry->prev = LIST_POISON2;
}
```

刪除後 `next`/`prev` 不是設成 `NULL`，而是設成**毒指標**（`include/linux/poison.h:23-24（v6.18）`）：

```c
#define LIST_POISON1  ((void *) 0x100 + POISON_POINTER_DELTA)
#define LIST_POISON2  ((void *) 0x122 + POISON_POINTER_DELTA)
```

`POISON_POINTER_DELTA` 在 x86_64 上是 `CONFIG_ILLEGAL_POINTER_VALUE`，預設 `0xdead000000000000`（`arch/x86/Kconfig:1584-1587（v6.18）`）。所以 x86_64 上刪除過的節點，`next` 會是 `0xdead000000000100`。**你在 kernel oops 裡看到這個值，就知道是有人在 `list_del` 之後還在用這個節點**——這是一個 beacon（[Ch 2](./02-program-comprehension-models.md) 的術語：一眼就能讓你聯想到某種行為的線索）。

為什麼不設 `NULL`？因為 `NULL` 解參考也會 crash，但你分不出是「從沒初始化」還是「刪掉之後又用」。毒值把兩者區分開。另外 `0xdead...` 開頭的位址在 x86_64 是 non-canonical address，任何存取都一定 fault，不會意外落在合法記憶體上。

`list_del` 之後如果還想讓 `list_empty(&entry)` 回傳 true（例如物件之後會再被加回某條 list），要用 `list_del_init`。**讀碼時看到 `list_del_init` 而不是 `list_del`，通常代表這個節點之後會被拿來判斷「我在不在 list 上」**。eventpoll 的 `ep_is_linked()` 就是靠這個（[練習 D](./practice-d-data-structure-map.md) 會看到）。

## 走訪巨集：三個參數各是什麼

讀 kernel 最常碰到的就是這個（`include/linux/list.h:772-784（v6.18）`）：

```c
#define list_entry_is_head(pos, head, member)				\
	list_is_head(&pos->member, (head))

/**
 * list_for_each_entry	-	iterate over list of given type
 * @pos:	the type * to use as a loop cursor.
 * @head:	the head for your list.
 * @member:	the name of the list_head within the struct.
 */
#define list_for_each_entry(pos, head, member)				\
	for (pos = list_first_entry(head, typeof(*pos), member);	\
	     !list_entry_is_head(pos, head, member);			\
	     pos = list_next_entry(pos, member))
```

拆開來：

| 參數 | 是什麼 | 讀碼時告訴你什麼 |
|---|---|---|
| `pos` | 迴圈游標，型別是 `T *` | **list 裡物件的型別 T**（看 `pos` 的宣告） |
| `head` | 一個 `struct list_head *`，list 的錨點 | 這條 list 屬於誰（`&parent->children` → 屬於 parent） |
| `member` | `T` 裡面的哪個 `list_head` 成員 | **物件是經由哪個成員掛上這條 list 的** |

第三個參數就是本章開頭那個問題的答案。看 `kernel/exit.c:712（v6.18）`：

```c
	list_for_each_entry(p, &father->children, sibling) {
```

`p` 是 `struct task_struct *`，`head` 是 `&father->children`，`member` 是 `sibling`。翻譯成人話：**`father->children` 是一條串著 `task_struct` 的 list，每個 task 經由自己的 `sibling` 成員掛在上面**。所以 `children` 是 head（錨點），`sibling` 是 node（掛勾）——同一個 struct 裡的兩個 `list_head`，一個扮演「我的小孩們」，一個扮演「我作為別人的小孩」。

注意終止條件不是 `pos != NULL`，而是 `&pos->member != head`。最後一次迭代結束時 `pos` 指向一個**假物件**——把 head 本身當成 `sibling` 成員去 `container_of` 出來的位址，它不是任何真實的 `task_struct`。所以：**迴圈跑完之後，不要用 `pos`**。這是 2022 年 kernel 社群大掃除過的一類 bug（「list iterator used after loop」），後來有人提議把游標宣告移進迴圈內，目前 v6.18 仍是舊式語意，讀碼時要自己留意。

## 實際跑：user-space 移植 list.h 子集

把 list.h 最核心的十幾行移植到 user-space（去掉 `WRITE_ONCE` 與 hardening 檢查，語意相同），模擬 `task_struct` 的 `tasks`/`children`/`sibling` 三條 list：

```c
/* mini_list.h（節錄）：從 Linux v6.18 include/linux/list.h 移植 */
#define container_of(ptr, type, member) \
	((type *)((char *)(ptr) - offsetof(type, member)))
struct list_head { struct list_head *next, *prev; };
#define LIST_HEAD_INIT(name) { &(name), &(name) }
#define LIST_HEAD(name) struct list_head name = LIST_HEAD_INIT(name)
#define LIST_POISON1 ((void *)0x100)
#define LIST_POISON2 ((void *)0x122)
static inline void INIT_LIST_HEAD(struct list_head *l) { l->next = l; l->prev = l; }
static inline void __list_add(struct list_head *new, struct list_head *prev, struct list_head *next)
{ next->prev = new; new->next = next; new->prev = prev; prev->next = new; }
static inline void list_add(struct list_head *new, struct list_head *head) { __list_add(new, head, head->next); }
static inline void list_add_tail(struct list_head *new, struct list_head *head) { __list_add(new, head->prev, head); }
static inline void __list_del(struct list_head *prev, struct list_head *next) { next->prev = prev; prev->next = next; }
static inline void list_del(struct list_head *e)
{ __list_del(e->prev, e->next); e->next = LIST_POISON1; e->prev = LIST_POISON2; }
static inline int list_empty(const struct list_head *h) { return h->next == h; }
#define list_entry(ptr, type, member) container_of(ptr, type, member)
#define list_first_entry(ptr, type, member) list_entry((ptr)->next, type, member)
#define list_next_entry(pos, member) list_entry((pos)->member.next, typeof(*(pos)), member)
#define list_entry_is_head(pos, head, member) (&(pos)->member == (head))
#define list_for_each_entry(pos, head, member) \
	for (pos = list_first_entry(head, typeof(*pos), member); \
	     !list_entry_is_head(pos, head, member); \
	     pos = list_next_entry(pos, member))
#define list_for_each_entry_safe(pos, n, head, member) \
	for (pos = list_first_entry(head, typeof(*pos), member), \
	     n = list_next_entry(pos, member); \
	     !list_entry_is_head(pos, head, member); \
	     pos = n, n = list_next_entry(n, member))
```

```c
/* demo.c */
#include <stdio.h>
#include <stdlib.h>
#include "mini_list.h"

/* 一個 task 同時掛在兩條 list 上：全域的 all_tasks（經由 tasks）
 * 與某個 parent 的 children（經由 sibling）。對照 v6.18 task_struct。 */
struct task {
	int pid;
	const char *comm;
	struct list_head tasks;     /* 串進 all_tasks */
	struct list_head children;  /* 「我」是 head：串我的小孩 */
	struct list_head sibling;   /* 「我」是 node：掛在 parent->children 上 */
};

static LIST_HEAD(all_tasks);

static struct task *spawn(int pid, const char *comm, struct task *parent)
{
	struct task *t = calloc(1, sizeof(*t));
	t->pid = pid; t->comm = comm;
	INIT_LIST_HEAD(&t->children);
	list_add_tail(&t->tasks, &all_tasks);
	if (parent)
		list_add_tail(&t->sibling, &parent->children);
	else
		INIT_LIST_HEAD(&t->sibling);
	return t;
}

int main(int argc, char **argv)
{
	setvbuf(stdout, NULL, _IONBF, 0);
	struct task *init = spawn(1, "init", NULL);
	struct task *sh   = spawn(100, "sh", init);
	spawn(101, "sshd", init);
	spawn(200, "vim", sh);
	spawn(201, "make", sh);
	struct task *t, *n;

	printf("offsetof(tasks)=%zu sibling=%zu children=%zu sizeof=%zu\n",
	       offsetof(struct task, tasks), offsetof(struct task, sibling),
	       offsetof(struct task, children), sizeof(struct task));

	printf("all_tasks:");
	list_for_each_entry(t, &all_tasks, tasks)
		printf(" %d(%s)", t->pid, t->comm);
	printf("\ninit's children:");
	list_for_each_entry(t, &init->children, sibling)
		printf(" %d(%s)", t->pid, t->comm);
	printf("\nsh's children:");
	list_for_each_entry(t, &sh->children, sibling)
		printf(" %d(%s)", t->pid, t->comm);
	printf("\n");

	if (argc > 1 && argv[1][0] == 'w') {
		/* 用錯 member：拿 children 串的 list，卻用 tasks 去解 */
		printf("WRONG member:");
		int k = 0;
		list_for_each_entry(t, &sh->children, tasks) {
			printf(" pid=%d", t->pid);
			if (++k == 3) break;
		}
		printf("\n");
	}
	if (argc > 1 && argv[1][0] == 'u') {
		/* 在非 safe 迴圈裡 list_del */
		list_for_each_entry(t, &all_tasks, tasks) {
			printf("del %d\n", t->pid);
			list_del(&t->tasks);
		}
	}
	list_for_each_entry_safe(t, n, &all_tasks, tasks) {
		list_del(&t->tasks);
		free(t);
	}
	printf("all_tasks empty? %d\n", list_empty(&all_tasks));
	return 0;
}
```

正常執行：

```
$ gcc -O0 -g -Wall -o demo demo.c && ./demo
offsetof(tasks)=16 sibling=48 children=32 sizeof=64
all_tasks: 1(init) 100(sh) 101(sshd) 200(vim) 201(make)
init's children: 100(sh) 101(sshd)
sh's children: 200(vim) 201(make)
all_tasks empty? 1
```

同一個 `struct task` 物件同時出現在 `all_tasks` 和某個 `children` 上，**沒有任何額外配置**。這是侵入式設計最大的好處：一個物件要掛幾條 list，就嵌幾個 `list_head`。

### 失敗一：用錯 member

把 `sh->children` 的走訪寫成用 `tasks` 解：

```
$ ./demo wrong
...
WRONG member: pid=-1303632976 pid=-1303632896
all_tasks empty? 1
```

編譯器**完全沒有警告**——`tasks` 和 `sibling` 都是 `struct list_head`，型別一樣。`container_of` 減掉的是 `tasks` 的位移 16，但 list 上的指標其實指向 `sibling`（位移 48），算出來的「物件」整整偏了 32 bytes，`pid` 讀到的是隔壁欄位的指標低 32 位元（每次跑數字都不同，因為是 heap 位址）。這種 bug 在 kernel 裡一樣存在，編譯器幫不上忙，這也是為什麼**讀碼時一定要核對「插入時用的 member」和「走訪時用的 member」是同一個**。

### 失敗二：在非 safe 迴圈裡刪除

```
$ ./demo unsafe
...
del 1
Segmentation fault (core dumped)
exit=139
```

用 gdb 看當下的游標：

```
$ gdb -q -batch -ex run -ex bt -ex 'p t' --args ./demo unsafe
del 1

Program received signal SIGSEGV, Segmentation fault.
0x000055555555568c in main (argc=2, argv=0x7fffffffe238) at demo.c:68
68				printf("del %d\n", t->pid);
#0  0x000055555555568c in main (argc=2, argv=0x7fffffffe238) at demo.c:68
$1 = (struct task *) 0xf0
```

`0xf0` 是怎麼來的？第一輪刪掉 init 後，它的 `tasks.next` 被設成毒值 `0x100`；迴圈的 step 表達式 `list_next_entry(pos, tasks)` 去讀 `pos->tasks.next`（= `0x100`），再減掉 `offsetof(tasks)` = 16，得到 `0x100 - 0x10 = 0xf0`。在 kernel 裡同一件事會變成 `0xdead0000000000f0`，oops 訊息裡看到這個值，你就能反推「某個 `list_for_each_entry` 迴圈裡有人刪了當前元素，而且 `list_head` 成員位移是 0x10」。

## `_safe` 變體：它保護的是什麼，不保護什麼

```c
#define list_for_each_entry_safe(pos, n, head, member)			\
	for (pos = list_first_entry(head, typeof(*pos), member),	\
		n = list_next_entry(pos, member);			\
	     !list_entry_is_head(pos, head, member); 			\
	     pos = n, n = list_next_entry(n, member))
```

（`include/linux/list.h:868-872（v6.18）`）

`_safe` 的唯一差別是**在進入迴圈本體之前，先把下一個元素存進 `n`**。所以本體裡刪掉（甚至 free 掉）`pos` 不會影響前進。

一個非常普遍的誤解是「`_safe` = thread-safe」。**不是**。它只保護「本迴圈自己刪除當前元素」這一種情況。如果另一個 CPU 同時刪掉的是 `n`，`_safe` 一樣會踩到毒指標。並行保護是 lock 或 RCU 的工作，和 `_safe` 無關。

讀碼時看到 `_safe`，你可以預期**迴圈本體裡有 `list_del`、`list_move` 或 free**。反過來，如果看到 `_safe` 但本體裡什麼都沒刪，那通常是作者保守寫法，不代表有特殊意義。`kernel/ptrace.c:597-604（v6.18）` 是典型用法：

```c
	list_for_each_entry_safe(p, n, &tracer->ptraced, ptrace_entry) {
		if (unlikely(p->ptrace & PT_EXITKILL))
			send_sig_info(SIGKILL, SEND_SIG_PRIV, p);

		if (__ptrace_detach(tracer, p))
			list_add(&p->ptrace_entry, dead);
	}
```

`__ptrace_detach` 裡面會把 `p` 從 `ptraced` 上拿下來，接著 `list_add` 把同一個 `ptrace_entry` 掛到另一條叫 `dead` 的 list 上。用普通版本的話，`p->ptrace_entry.next` 已經指向 `dead` 那條 list，迴圈會跑到別人家去。

## 讀碼的關鍵問題：這個 list_head 串的是什麼？

回到本章的核心技能。拿到一個沒有註解的 `struct list_head foo;`，照這個順序找：

```
步驟 1：找插入點          rg 'list_(add|add_tail|move|move_tail|splice\w*)\(.*->foo\b'
         → 第一個參數 &X->bar 告訴你：型別 X 經由成員 bar 掛上 foo

步驟 2：找走訪點          rg 'list_for_each_entry\w*\(.*->foo,'
         → pos 的型別 = 物件型別；第三個參數 = 掛勾成員

步驟 3：找直接取元素      rg 'list_(first|last)_entry\(.*->foo,'
         → 第二個參數就是型別名稱，最直接的證據

步驟 4：交叉核對          插入用的 member 和走訪用的 member 必須一致；不一致就是 bug 或你找錯了
```

用 `task_struct::children` 實際跑一次（`rg` 在 [Ch 47](./47-grep-and-regex-search.md) 會深入）：

```
$ rg -n 'list_add\w*\(.*->children\)' kernel/fork.c kernel/exit.c kernel/ptrace.c
kernel/fork.c:2396:			list_add_tail(&p->sibling, &p->real_parent->children);
```

一行就確定：`children` 上掛的是 `task_struct`，經由 `sibling`。再反查 `sibling` 這個成員還被誰動過：

```
$ rg -n '(list_\w+)\(&\w+->sibling' kernel/
kernel/cgroup/cgroup.c:5636:	list_del_rcu(&css->sibling);
kernel/cgroup/cgroup.c:5820:	list_add_tail_rcu(&css->sibling, &parent_css->children);
kernel/cgroup/cgroup.c:5830:	list_del_rcu(&css->sibling);
kernel/fork.c:2396:			list_add_tail(&p->sibling, &p->real_parent->children);
kernel/events/core.c:2100:	list_add_tail(&event->sibling_list, &group_leader->sibling_list);
...
kernel/exit.c:150:		list_del_init(&p->sibling);
kernel/sched/core.c:9137:	list_add_rcu(&tg->siblings, &parent->children);
```

這裡有一個教訓：**同名成員在不同 struct 裡是完全不同的東西**。`css->sibling`（cgroup）、`tg->siblings`（task group）和 `p->sibling`（task）只是名字像。grep 結果要用變數的型別過濾——`p` 在 fork.c/exit.c 是 `task_struct *`，`css` 是 `cgroup_subsys_state *`。這正是 clangd 這類「懂型別」的工具比 grep 強的地方（[Ch 49](./49-clangd-lsp.md)）。

還有一個陷阱：**插入不只有 `list_add`**。`kernel/exit.c:730（v6.18）`：

```c
	list_splice_tail_init(&father->children, &reaper->children);
```

父行程死掉時，整串小孩被一次搬到 reaper 底下。如果你只 grep `list_add`，會漏掉這條路徑，以為小孩只會在 fork 時被加入。完整的「會改變 list 成員」的 API 至少包括 `list_add*`、`list_move*`、`list_splice*`、`list_replace*`、`list_cut_*`，以及它們的 `_rcu` 版本。

把 `task_struct` 的 list 關係畫出來：

```
              init_task.tasks  ◀──(tasks)── 每個 thread group leader（全系統一條，RCU 保護）
                                            kernel/fork.c:2397 list_add_tail_rcu

   parent task_struct
   ┌───────────────────┐
   │ children (head) ──┼──▶ child1.sibling ──▶ child2.sibling ──▶ (回到 parent.children)
   │ sibling  (node) ──┼──▶ 掛在 parent->real_parent->children 上
   │ ptraced  (head) ──┼──▶ tracee1.ptrace_entry ──▶ ...
   │ ptrace_entry(node)┼──▶ 掛在 tracer->ptraced 上
   │ tasks    (node) ──┼──▶ 掛在 init_task.tasks 上
   └───────────────────┘
   保護：children/sibling 由 tasklist_lock 寫鎖保護（fork.c 那段就在 write_lock_irq 底下）
```

最後那行「保護」不是可有可無的註腳。**每一條 list 都必須有人保護，讀碼時要把「這條 list 被哪把鎖保護」跟「這條 list 串什麼」一起記下來**。這會在 [Ch 30](./30-reading-locking-code.md) 系統化，也是[練習 D](./practice-d-data-structure-map.md) 的評分重點之一。

## hlist：為什麼 head 只有一個指標，node 卻有 `**pprev`

hash table 的每個 bucket 都需要一個 head。如果 bucket 數量是 2^16，用 `list_head`（兩個指標，16 bytes）當 head 要 1 MB，其中一半的 `prev` 幾乎沒用——hash bucket 很少需要 O(1) 取尾巴。於是有了 `hlist`（`include/linux/types.h:203-209（v6.18）`）：

```c
struct hlist_head {
	struct hlist_node *first;
};

struct hlist_node {
	struct hlist_node *next, **pprev;
};
```

list.h 的註解寫得很直白（`include/linux/list.h:937-940（v6.18）`）：

```c
 * Double linked lists with a single pointer list head.
 * Mostly useful for hash tables where the two pointer list head is
 * too wasteful.
 * You lose the ability to access the tail in O(1).
```

head 省成一個指標之後，問題來了：第一個 node 的「前一個」是誰？是 `hlist_head`，它和 `hlist_node` **型別不同**。如果 node 用 `struct hlist_node *prev`，第一個 node 的 `prev` 沒辦法指向 head，刪除時就得特判「我是不是第一個？是的話要去改 head->first」——而刪除函式手上根本沒有 head 的指標。

`pprev` 的解法是：**不指向前一個節點，而是指向「前一個節點裡那個指向我的指標」**。對第一個 node 來說，那個指標是 `head->first`；對其他 node 來說，是前一個 node 的 `next`。兩者型別都是 `struct hlist_node *`，所以 `pprev` 的型別一律是 `struct hlist_node **`。

```
 hlist_head            node A                    node B
┌─────────┐         ┌──────────────┐         ┌──────────────┐
│ first ──┼────────▶│ next ────────┼────────▶│ next = NULL  │
└────▲────┘         │ pprev        │         │ pprev        │
     │              └──┬─────▲─────┘         └──┬───────────┘
     └─────────────────┘     │                  │
     A.pprev = &head.first   └──────────────────┘
                             B.pprev = &A.next
```

刪除變成完全不需要知道自己是不是第一個（`include/linux/list.h:987-996（v6.18）`）：

```c
static inline void __hlist_del(struct hlist_node *n)
{
	struct hlist_node *next = n->next;
	struct hlist_node **pprev = n->pprev;

	WRITE_ONCE(*pprev, next);
	if (next)
		WRITE_ONCE(next->pprev, pprev);
}
```

`*pprev = next` 一行同時涵蓋「改 head->first」和「改前一個的 next」兩種情況。這個技巧不是 kernel 發明的，Linus 在 2016 年 TED 訪談裡談「good taste」時，舉的就是用指標的指標消除「刪除第一個節點」特例的鏈結串列例子；[Ch 10 — 指標慣用法](./10-pointer-idioms.md)講過同一個 pattern 的 user-space 版本。

還有兩個 hlist 和 list_head 的行為差異，讀碼時容易誤判：

| | `list_head` | `hlist` |
|---|---|---|
| 環狀？ | 是，最後一個的 `next` 回到 head | 否，最後一個的 `next` 是 `NULL` |
| 走訪終止條件 | `&pos->member != head` | `pos != NULL`（見 `hlist_for_each_entry`，`list.h:1166-1169`） |
| 「不在任何 list 上」 | `list_empty(&node)`（需 `INIT_LIST_HEAD`/`list_del_init`） | `hlist_unhashed(&node)`，即 `pprev == NULL` |
| O(1) 取尾 | 可以（`head->prev`） | 不行 |
| head 大小 | 2 個指標 | 1 個指標 |

Ch 25 的 `hashtable.h` 整個建在 hlist 上；[練習 D](./practice-d-data-structure-map.md) 的 eventpoll 用 hlist 把「一個 file 被哪些 epoll 監看」串起來（`epitem::fllink`）。

## `_rcu` 變體：讀者不拿鎖時，寫者要多守什麼規矩

`task_struct::tasks` 是全系統的 process list，`for_each_process` 走的就是它（`include/linux/sched/signal.h:636-640（v6.18）`）：

```c
#define next_task(p) \
	list_entry_rcu((p)->tasks.next, struct task_struct, tasks)
...
#define for_each_process(p) \
	for (p = &init_task ; (p = next_task(p)) != &init_task ; )
```

讀這條 list 的人（例如 `ps` 背後的 `/proc` 走訪）非常多，如果每次都拿 `tasklist_lock`，fork/exit 會被讀者拖慢。所以它是 RCU list：**讀者只拿 `rcu_read_lock()`，寫者照常拿鎖，但要用 `_rcu` 版本的增刪**。fork 時（`kernel/fork.c:2396-2397（v6.18）`）：

```c
			list_add_tail(&p->sibling, &p->real_parent->children);
			list_add_tail_rcu(&p->tasks, &init_task.tasks);
```

同一個 task、同一段程式碼，兩條 list 用了不同的 API：`children` 只有持鎖者讀，用普通版；`tasks` 有無鎖讀者，用 `_rcu` 版。**讀碼時這個差異就是一個訊號：用 `_rcu` 增刪的 list，一定存在不拿鎖的讀者，去找它們**。

`_rcu` 版本到底多做了什麼？對照 `__list_add`，看 `include/linux/rculist.h:97-107（v6.18）`：

```c
static inline void __list_add_rcu(struct list_head *new,
		struct list_head *prev, struct list_head *next)
{
	if (!__list_add_valid(new, prev, next))
		return;

	new->next = next;
	new->prev = prev;
	rcu_assign_pointer(list_next_rcu(prev), new);
	next->prev = new;
}
```

順序被刻意排過：**先把新節點自己的 `next`/`prev` 填好，再用 `rcu_assign_pointer`（帶 release 語意）把它發布到 `prev->next`**。無鎖讀者沿著 `next` 走，只要看得到新節點，就保證看得到它填好的 `next`。普通版 `__list_add` 沒有這個保證。

刪除則更微妙（`include/linux/rculist.h:176-180（v6.18）`）：

```c
static inline void list_del_rcu(struct list_head *entry)
{
	__list_del_entry(entry);
	entry->prev = LIST_POISON2;
}
```

**只毒化 `prev`，不毒化 `next`**。因為此刻可能有讀者正停在這個節點上，它下一步要讀 `entry->next` 繼續走；如果把 `next` 毒掉，讀者就會 crash。被刪的節點要等一個 grace period（所有既存讀者都離開）之後才能 free——這就是為什麼 `exit.c` 裡 task 的釋放走 `call_rcu`/`put_task_struct_rcu_user` 這類延遲路徑。RCU 的完整讀法在 [Ch 31](./31-reading-rcu-code.md)。

在 v6.18 的 `kernel/`、`fs/`、`mm/` 三個目錄裡數一下三種走訪的使用次數（`rg -c` 加總）：

```
list_for_each_entry(       1348
list_for_each_entry_safe    644
list_for_each_entry_rcu     238
```

（`list_for_each_entry(` 這個 pattern 不會匹配到 `_safe` 和 `_rcu`，因為它要求名字後面緊接 `(`。）

## 對比與取捨

| 方案 | 優點 | 缺點 | kernel 裡的位置 |
|---|---|---|---|
| 侵入式 `list_head` | 零額外配置；一個物件可掛多條 list；給物件指標就能 O(1) 刪除 | 宣告看不出型別；member 用錯編譯器不報錯；物件生命週期要自己管 | 幾乎所有地方 |
| 非侵入式 `struct node { void *data; }` | 宣告清楚；同一物件可被任意多個容器參照而不改物件定義 | 每次插入多一次配置（在 atomic context 可能失敗）；多一次指標跳躍；要刪除得先找到 node | kernel 幾乎不用 |
| BSD `sys/queue.h`（`TAILQ_ENTRY` 等） | 同樣侵入式，但巨集參數帶型別名，宣告時看得出元素型別 | 每種 list 各自展開一份結構定義；巨集更多 | FreeBSD/OpenBSD kernel，Linux 不用 |
| 單向 `llist`（`include/linux/llist.h`） | 無鎖 push/del_all，適合多生產者單消費者 | 只能單向、不能從中間刪除 | irq_work、部分 RCU 路徑 |
| `hlist` | head 只有一個指標 | 不能 O(1) 取尾；非環狀 | hash bucket |

為什麼 kernel 堅持侵入式？最根本的理由是**配置失敗的處理**：在中斷 context 或持有 spinlock 時，`kmalloc(GFP_ATOMIC)` 可能失敗；如果「把物件加進 list」這個動作本身需要配置，那每個 `list_add` 都要變成可能失敗的操作，錯誤處理會爆炸。侵入式設計把配置合併到物件本身的配置裡，物件配置成功，它就一定能被放進任何一條 list。

## 踩雷集錦

1. **以為 `_safe` 等於 thread-safe**：它只保護「本迴圈體內刪除 `pos`」這一種情況。並行修改要靠 lock 或 RCU，跟 `_safe` 無關。
2. **迴圈結束後使用游標**：`list_for_each_entry` 正常跑完時 `pos` 指向由 head 算出來的假物件。讀碼時看到迴圈後面有 `if (pos->...)` 而前面沒有 `break` 保證，就是可疑點。正確寫法是在迴圈內用另一個變數記下找到的元素（`ep_find` 的 `epir` 就是這樣，見 Ch 24）。
3. **以為 `list_head` 成員名稱決定了它是 head 還是 node**：`children` 是 head、`sibling` 是 node，這只是命名習慣。唯一可靠的判斷方式是看它出現在 `list_add(new, head)` 的哪個位置、或 `list_for_each_entry` 的第二還是第三個參數。
4. **只 grep `list_add`**：會漏掉 `list_splice*`、`list_move*`、`list_replace*` 帶來的成員變動。`task_struct::children` 的 reparent 路徑就只出現在 `list_splice_tail_init`。
5. **`list_del` 之後用 `list_empty` 判斷**：`list_del` 會把指標毒化，`list_empty(&entry)` 結果未定義。要判斷「在不在 list 上」，刪除時必須用 `list_del_init`。
6. **同名成員混為一談**：`p->sibling`、`css->sibling`、`tg->siblings` 屬於三種不同 struct。grep 結果要用變數型別過濾。

## 進階：再往深一層

- **`CONFIG_LIST_HARDENED` 與 `CONFIG_DEBUG_LIST`**：開啟後 `__list_add_valid` 會檢查 `prev->next == next` 等不變式，list 被破壞時直接報錯而不是默默寫壞記憶體。這是 kernel exploitation 防禦的一環——傳統的「unlink 攻擊」靠的就是讓 `__list_del` 對任意位址寫值。[`kernel_pwn`](../../security/kernel_pwn/README.md) 課有對應章節。
- **`list_bidir_del_rcu`**：v6.14 才加入的變體（commit `67d676bb135c`，為了 mount namespace 的無鎖查找；`rculist.h:183` 起），允許 RCU 讀者反向走訪。讀到不熟的變體時，直接讀它和基本版的 diff，通常只差一兩行。
- **`list_lru`、`plist`（priority list）、`llist`**：都是 list_head 家族的特化版本。看到 `struct plist_node pushable_tasks`（就在 `task_struct::tasks` 下一行）時，知道它是「按優先權排序的 list」，操作成本是 O(優先權種類數)。
- **list 的 cache 行為**：走訪 list 是 pointer chasing，每一步都可能 cache miss。這是為什麼 kernel 在熱路徑上逐漸用 xarray、maple tree 取代長 list（Ch 26）。

## 動手練習

1. 在 v6.18 tree 找 `struct super_block` 的 `s_inodes` 成員。用本章的四步驟回答：它串的是什麼型別、經由哪個成員、被哪把鎖保護（提示：看 `fs/inode.c` 裡 `inode_sb_list_add`）。
2. 把 `demo.c` 的 `list_del` 改成 `list_del_init`，再跑 `./demo unsafe`。它還會 crash 嗎？會無窮迴圈嗎？先預測，再跑，然後解釋。
3. 在 user-space 實作 `hlist` 的 `hlist_add_before`，讓它在「插在第一個節點前面」時不需要特判。實作完再對照 `include/linux/list.h` 的版本。

## 本章重點整理

- `list_head` 是侵入式的：物件自帶掛勾，串列不擁有物件；代價是型別資訊不在宣告裡。
- 回答「串的是什麼」：找 `list_add*` 呼叫點的第一個參數、`list_for_each_entry` 的游標型別與第三個參數，並交叉核對。
- 毒指標 `0xdead000000000100`/`0x122` 是「刪除後還在用」的 beacon。
- `_safe` 只保護迴圈體內刪除自己；`_rcu` 代表存在無鎖讀者。
- `hlist` 用 `**pprev` 指向「指向我的那個指標」，消除了第一個節點的特例，換來一半的 head 空間。

## 自我檢核

- [ ] 不看筆記，畫出 `list_add_tail(&new, head)` 前後四個指標的變化，並說出為什麼最後一個寫入要用 `WRITE_ONCE`。
- [ ] 有人問你「`hlist_node` 為什麼不用 `prev` 而用 `pprev`？」，你能在 30 秒內講清楚嗎？
- [ ] 在 oops 裡看到 `RIP` 存取 `0xdead0000000000f8`，你能推出哪兩件事？
- [ ] 說明為什麼 fork 時 `sibling` 用 `list_add_tail`、`tasks` 卻用 `list_add_tail_rcu`。
- [ ] 想一個你工作中的 C 程式碼，它的鏈結串列是侵入式還是非侵入式？換成另一種會遇到什麼問題？

## 延伸閱讀

### 官方文件

- **[Linked Lists in Linux](https://docs.kernel.org/core-api/list.html)**（v6.18 tree：`Documentation/core-api/list.rst`）
  - **讀哪裡**：整篇。它用一個 `struct clown` / `clown_car` 的例子把 list API 從頭講到尾，含 `list_cut_position`、`list_splice` 這些本章只點到的操作。
  - **前提**：會 C 指標即可；讀完本章再讀，會覺得它是本章的 API 速查表。
- **[What is RCU? — "RCU-protected lists"](https://docs.kernel.org/RCU/whatisRCU.html)**（`Documentation/RCU/whatisRCU.rst`）
  - **讀哪裡**：第 6 節「ANALOGY WITH READER-WRITER LOCKING」，用 `list_for_each_entry_rcu`/`list_del_rcu` 對照 rwlock 版本，說明為什麼刪除後要等 grace period 才能 free。
  - **和本章的關聯**：本章 `_rcu` 一節的完整背景；Ch 31 會再深入。

### 部落格 / 技術文章

- **Linux Kernel Linked List Explained** — Kulesh Shanmugasundaram（原載 NYU Poly 個人頁，網址多次搬遷，以標題搜尋）
  - **這篇說什麼**：早年把 list.h 抽到 user-space 並逐行解說的經典文章，很多人第一次搞懂 `container_of` 就是讀這篇。
  - **注意**：基於 2.4/2.6 時代的 list.h，`list_for_each_entry` 的寫法和 v6.18 略有不同（舊版用 `prefetch`、沒有 `list_entry_is_head`），概念完全適用。
- **LWN：Linux kernel design patterns（三篇系列，part 2）** — Neil Brown（LWN.net，2009 年 6 月；在 [LWN Kernel Index](https://lwn.net/Kernel/Index/) 的 Development model/Patterns 項下可找到）
  - **這篇說什麼**：從設計模式角度談 kernel 的 list、hlist、rbtree 為什麼都是侵入式，以及「為什麼不寫泛型容器」。
  - **讀哪裡**：「Linked lists」與「Hash tables」兩節。
  - **為什麼值得讀**：作者是長期 kernel 維護者（md/RAID、NFS），這系列是 kernel 資料結構設計理由講得最好的文章之一。

### 書籍

- **《Linux Kernel Development》3rd ed.** — Robert Love（Addison-Wesley, 2010）
  - **讀哪幾章**：第 6 章「Kernel Data Structures」的 Linked Lists 一節，有完整圖解。版本是 2.6.34，API 名稱至今幾乎沒變。
- **《Code Reading》** — Diomidis Spinellis（2003）
  - **讀哪幾章**：第 4 章「C Data Structures」4.7 節（Linked Lists）。它用 BSD 的 `sys/queue.h` 示範，正好和本章的 list_head 形成對照。

list 讓你能把一群物件串起來，但它不排序、查找是 O(n)。當 kernel 需要「按某個 key 排序並快速找到最小值」時，它換成另一個同樣侵入式、同樣要你自己寫迴圈的結構。

→ [Ch 24 紅黑樹：rbtree 與 augmented tree](./24-rbtree.md)
