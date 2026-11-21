# Ch 16 — container_of：kernel 最重要的一個巨集

> **目標**：能逐 token 解釋 v6.18 的 `container_of` 定義、畫出它背後的記憶體圖、在 user space 重現它（包括故意觸發型別檢查的編譯錯誤）、說出它從 2005 年到 2025 年怎麼演進，並且在讀碼時一眼認出 `list_entry`、`hlist_entry`、`rb_entry`、`to_xxx()` 這些衍生寫法其實都是它。
> **鴻溝**：本章主要練**慣用法鴻溝**（`container_of` 是 Ch 1 點名的頭號天書），附帶**脈絡鴻溝**（用 git 考古它的演進）。

> **環境**：Linux kernel v6.18（`~/code_reading/linux`）、v6.18 x86_64 build（`~/code_reading/build`）、gcc 11.4、clang 14、pahole 1.25。

## 為什麼需要這個？

讀 kernel 讀到這種程式碼，是每個人卡關的經典瞬間：

```c
/* fs/eventfd.c:89-94（v6.18） */
static void eventfd_free(struct kref *kref)
{
	struct eventfd_ctx *ctx = container_of(kref, struct eventfd_ctx, kref);

	eventfd_free_ctx(ctx);
}
```

函式拿到的是一個 `struct kref *`——一個只有引用計數的小 struct。但它要釋放的是整個 `struct eventfd_ctx`。從一個小 struct 的指標「變出」包住它的大 struct 指標，這就是 `container_of` 做的事。

為什麼 kernel 要這樣繞？因為 kernel 的通用元件（refcount、鏈結串列、紅黑樹、work queue、timer）都**不知道**自己被嵌在什麼 struct 裡。`kref_put()` 只知道 `struct kref`，`list_add()` 只知道 `struct list_head`。這種設計叫**侵入式（intrusive）資料結構**：不是讓容器去「裝」你的物件，而是把容器的節點**嵌進**你的物件。

對比 user space 常見的非侵入式設計：

```
非侵入式（glib GList、C++ std::list<T*>）     侵入式（kernel list_head）

  list node          your object               your object
  +--------+         +---------+               +---------------+
  | next --+--->     | pid     |               | pid           |
  | prev   |         | comm    |               | comm          |
  | data --+-------->| ...     |               | tasks: next --+---> 下一個物件的 tasks
  +--------+         +---------+               |        prev   |
                                               | ...           |
  每個節點額外一次配置，                        +---------------+
  走訪時多一次指標跳躍                          零額外配置；但走訪拿到的是
                                               &obj->tasks，要「倒推」回 obj
```

侵入式的好處：不需要額外配置節點（kernel 很多路徑不能睡、不能失敗）、一個物件可以同時掛在好幾個串列上（嵌好幾個 `list_head` 就好）、cache 友善。代價就是那個「倒推」動作，而 `container_of` 就是倒推的工具。

v6.18 排除 `tools/` 之後，`rg -c "\bcontainer_of\("` 數到 **21262 次、分布在 8229 個檔案**（其中 drivers/ 佔 16636 次）。這還不算 `list_entry`（1963 次）、`list_first_entry`（1736 次）這些包裝。它不是一個「進階技巧」，而是 kernel C 的基本詞彙。

## 先建立直覺

想像你在一棟大樓裡撿到一把鑰匙，鑰匙圈上寫著「3 樓、從電梯口往前走 24 步的那一間」。你不知道那間房間的門牌，但你知道：**房間起點 = 你撿到鑰匙的位置 − 24 步**。

`container_of(ptr, type, member)` 就是這個減法：

```
         低位址
           │
  ctx ───► ├──────────────────────┐ ◄── 我們想要的：struct 起點
           │ kref        (4 B)    │
           │ <hole>      (4 B)    │
           ├──────────────────────┤ ◄── offsetof(struct eventfd_ctx, wqh) = 8
           │ wqh         (24 B)   │
           ├──────────────────────┤
           │ count, flags, id ... │
           └──────────────────────┘
           │
         高位址

  container_of(p, T, m) = (T *)((char *)p - offsetof(T, m))
```

它**沒有**任何執行期查詢、沒有 metadata、沒有雜湊表，只有一次減法。偏移量在編譯期就是常數。

> 如果你對 struct 佈局與 padding 還不熟，先回看 [Ch 11 — struct、union 與 tagged union](./11-structs-unions-tagged.md) 的「讀懂資料的形狀」；本章會用 pahole 看真實的偏移量。

## v6.18 的定義，逐 token 解碼

以下從 v6.18 tree 原封不動複製：

```c
/* include/linux/container_of.h:1-41（v6.18） */
/* SPDX-License-Identifier: GPL-2.0 */
#ifndef _LINUX_CONTAINER_OF_H
#define _LINUX_CONTAINER_OF_H

#include <linux/build_bug.h>
#include <linux/stddef.h>

#define typeof_member(T, m)	typeof(((T*)0)->m)

/**
 * container_of - cast a member of a structure out to the containing structure
 * @ptr:	the pointer to the member.
 * @type:	the type of the container struct this is embedded in.
 * @member:	the name of the member within the struct.
 *
 * WARNING: any const qualifier of @ptr is lost.
 * Do not use container_of() in new code.
 */
#define container_of(ptr, type, member) ({				\
	void *__mptr = (void *)(ptr);					\
	static_assert(__same_type(*(ptr), ((type *)0)->member) ||	\
		      __same_type(*(ptr), void),			\
		      "pointer type mismatch in container_of()");	\
	((type *)(__mptr - offsetof(type, member))); })

/**
 * container_of_const - cast a member of a structure out to the containing
 *			structure and preserve the const-ness of the pointer
 * @ptr:		the pointer to the member
 * @type:		the type of the container struct this is embedded in.
 * @member:		the name of the member within the struct.
 *
 * Always prefer container_of_const() instead of container_of() in new code.
 */
#define container_of_const(ptr, type, member)				\
	_Generic(ptr,							\
		const typeof(*(ptr)) *: ((const type *)container_of(ptr, type, member)),\
		default: ((type *)container_of(ptr, type, member))	\
	)

#endif	/* _LINUX_CONTAINER_OF_H */
```

整份檔案 41 行。我們一行一行拆 `container_of`（第 19–24 行）。

### 第 19 行：`({`

statement expression（[Ch 15](./15-kernel-c-gnu-extensions.md)）。整個巨集是一個**有值的區塊**，值是最後一個運算式——第 24 行那個指標。用 `({ })` 而不是單純的 `(...)` 運算式，是因為中間要宣告區域變數 `__mptr` 並放一個 `static_assert`。

### 第 20 行：`void *__mptr = (void *)(ptr);`

- `(ptr)` 加括號：巨集參數一律括起來，防止 `container_of(a + 1, ...)` 這類展開後優先序錯亂。
- 轉成 `void *`：第 24 行要對它做位元組層級的減法。
- **`ptr` 只在這裡被求值一次**（第 21 行的 `*(ptr)` 在 `__same_type` 裡，而 `__same_type` 用的是 `typeof`，不求值）。所以 `container_of(next_item(), ...)` 是安全的。
- `__mptr` 用雙底線開頭，降低和呼叫端變數撞名的機率。

### 第 21–23 行：`static_assert(...)`，型別檢查

拆成三塊：

```
__same_type(*(ptr), ((type *)0)->member)
   │            │         │
   │            │         └─ 「假裝 0 位址有一個 type 物件，取它的 member」
   │            │            只用在 typeof 裡，所以不會真的解參考 NULL
   │            └─ ptr 指向的東西的型別
   └─ include/linux/compiler_types.h:537：
      __builtin_types_compatible_p(typeof(a), typeof(b))

|| __same_type(*(ptr), void)
   └─ 例外：允許傳 void * 進來（無法檢查，就放行）
```

`((type *)0)->member` 這個寫法看起來像在解參考 NULL，其實它只出現在 `typeof` 裡面，編譯器只拿它的**型別**，不產生任何讀取指令。同一個技巧也出現在第 8 行的 `typeof_member(T, m)`。

`static_assert` 是 kernel 自己包的（`include/linux/build_bug.h:77-78`），最後展開成 C11 的 `_Static_assert`。條件不成立時**編譯失敗**，訊息是 `"pointer type mismatch in container_of()"`。這一行在執行期不產生任何程式碼。

### 第 24 行：`((type *)(__mptr - offsetof(type, member))); })`

- `offsetof(type, member)`：`include/linux/stddef.h:16` 定義成 `__builtin_offsetof(TYPE, MEMBER)`，編譯期常數。
- `__mptr - offsetof(...)`：對 `void *` 做減法。**ISO C 不允許 `void *` 算術**；這是 GNU 擴充，把 `void *` 當成 `char *`（以 1 byte 為單位）處理。kernel 只在 `W=3` 才開 `-Wpointer-arith`（`scripts/Makefile.extrawarn:199`），平常不會警告。
- `(type *)`：最後轉成容器型別的指標。
- 結尾的 `;` 加 `})`：statement expression 的最後一個運算式陳述句，它的值就是整個巨集的值。

### 編譯完剩下什麼？

一次減法，而且通常和後續的位址計算合併掉。拿 ext4 的例子實際看：

```c
/* fs/ext4/ext4.h:1789-1792（v6.18） */
static inline struct ext4_inode_info *EXT4_I(struct inode *inode)
{
	return container_of(inode, struct ext4_inode_info, vfs_inode);
}

/* fs/ext4/super.c:1438-1446（v6.18） */
static void ext4_free_in_core_inode(struct inode *inode)
{
	fscrypt_free_inode(inode);
	if (!list_empty(&(EXT4_I(inode)->i_fc_list))) {
		pr_warn("%s: inode %ld still in fc list",
			__func__, inode->i_ino);
	}
	kmem_cache_free(ext4_inode_cachep, EXT4_I(inode));
}
```

用 pahole 查我們這份 build 裡 `vfs_inode` 的偏移，再看反組譯：

```
$ pahole -C ext4_inode_info ~/code_reading/build/vmlinux | grep vfs_inode
	struct inode               vfs_inode __attribute__((__aligned__(8))); /*   272   592 */

$ objdump -d --no-show-raw-insn --disassemble=ext4_free_in_core_inode ~/code_reading/build/vmlinux
...
ffffffff816abe21:	mov    0x2841820(%rip),%rdi        # ffffffff83eed648 <ext4_inode_cachep>
ffffffff816abe28:	lea    -0x110(%rbx),%rsi
ffffffff816abe2f:	pop    %rbx
ffffffff816abe30:	jmp    ffffffff81553a10 <kmem_cache_free>
```

`0x110 = 272`。`EXT4_I(inode)` 整個巨集、型別檢查、statement expression，最後只剩一條 `lea -0x110(%rbx),%rsi`——「把 inode 指標減 272，當成 kmem_cache_free 的第二個參數」。**這就是零成本抽象**：可讀性的包裝在編譯期全部消失。

（偏移量 272 取決於 `.config`：開關不同的 ext4 選項，`ext4_inode_info` 前面的欄位就不同。這也是為什麼你**永遠不該**在程式碼裡寫死偏移量。）

## 另一個真實例子：offset 為 0 的情況

```
$ pahole -C eventfd_ctx ~/code_reading/build/vmlinux
struct eventfd_ctx {
	struct kref                kref;                 /*     0     4 */

	/* XXX 4 bytes hole, try to pack */

	wait_queue_head_t          wqh;                  /*     8    24 */
	__u64                      count;                /*    32     8 */
	unsigned int               flags;                /*    40     4 */
	int                        id;                   /*    44     4 */

	/* size: 48, cachelines: 1, members: 5 */
	/* sum members: 44, holes: 1, sum holes: 4 */
	/* last cacheline: 48 bytes */
};
```

`kref` 在 offset 0，所以 `eventfd_free()` 裡的 `container_of(kref, struct eventfd_ctx, kref)` 減的是 0，數值上等於直接轉型。那為什麼不直接寫 `(struct eventfd_ctx *)kref`？

1. **型別檢查**：`container_of` 會確認 `kref` 真的是 `struct kref *`；直接轉型什麼都不查。
2. **對重排免疫**：哪天有人在 `kref` 前面加一個欄位，`container_of` 自動跟著變，直接轉型會默默壞掉。
3. **表達意圖**：讀者看到 `container_of` 就知道「這是從嵌入成員倒推容器」，看到轉型只會想「這兩個型別有什麼關係？」

## user space 重現：跑起來看

把 v6.18 的定義原樣搬進 user space（`__same_type` 來自 compiler_types.h:537，`static_assert` 用 `<assert.h>` 的版本）：

```c
/* cof.c */
#include <stdio.h>
#include <stddef.h>
#include <assert.h>

/* ---- 從 v6.18 搬過來的定義（include/linux/compiler_types.h、container_of.h） ---- */
#define __same_type(a, b) __builtin_types_compatible_p(typeof(a), typeof(b))

#define container_of(ptr, type, member) ({				\
	void *__mptr = (void *)(ptr);					\
	static_assert(__same_type(*(ptr), ((type *)0)->member) ||	\
		      __same_type(*(ptr), void),			\
		      "pointer type mismatch in container_of()");	\
	((type *)(__mptr - offsetof(type, member))); })

#define container_of_const(ptr, type, member)				\
	_Generic(ptr,							\
		const typeof(*(ptr)) *: ((const type *)container_of(ptr, type, member)),\
		default: ((type *)container_of(ptr, type, member))	\
	)
/* ------------------------------------------------------------------------------- */

struct list_head { struct list_head *next, *prev; };

struct task {
	int pid;                 /* offset 0  */
	char comm[16];           /* offset 4  */
	struct list_head tasks;  /* offset 24（4+16=20，再對齊到 8） */
	long state;              /* offset 40 */
};

int main(void)
{
	struct task t = { .pid = 42, .comm = "bash", .state = 1 };
	struct list_head *lh = &t.tasks;          /* 手上只有「成員」的指標 */

	printf("&t            = %p\n", (void *)&t);
	printf("&t.tasks (lh) = %p\n", (void *)lh);
	printf("offsetof(struct task, tasks) = %zu\n", offsetof(struct task, tasks));

	struct task *back = container_of(lh, struct task, tasks);
	printf("container_of  = %p  pid=%d comm=%s\n", (void *)back, back->pid, back->comm);

	/* 誤讀：以為它是轉型。直接轉型會差一個 offset */
	struct task *wrong = (struct task *)lh;
	printf("(struct task *)lh = %p  pid=%d  <- 讀到的是 list_head 的位元組\n",
	       (void *)wrong, wrong->pid);

	/* const 版本保留 const */
	const struct list_head *clh = &t.tasks;
	const struct task *ct = container_of_const(clh, struct task, tasks);
	printf("container_of_const keeps const: pid=%d\n", ct->pid);

	/* 邊界：成員指標是 NULL 時，結果不是 NULL */
	struct list_head *none = NULL;
	struct task *bogus = container_of(none, struct task, tasks);
	printf("container_of(NULL) = %p (不是 NULL！)\n", (void *)bogus);
	return 0;
}
```

```
$ gcc -std=gnu11 -Wall -O0 cof.c -o cof && ./cof
&t            = 0x7ffc3053b5d0
&t.tasks (lh) = 0x7ffc3053b5e8
offsetof(struct task, tasks) = 24
container_of  = 0x7ffc3053b5d0  pid=42 comm=bash
(struct task *)lh = 0x7ffc3053b5e8  pid=0  <- 讀到的是 list_head 的位元組
container_of_const keeps const: pid=42
container_of(NULL) = 0xffffffffffffffe8 (不是 NULL！)
```

clang 14 輸出相同（位址不同，差值相同）。對照記憶體圖：

```
0x...5d0  ┌──────────────┐ ◄── &t、container_of 的結果
          │ pid = 42     │  +0
          │ comm "bash"  │  +4 .. +19
          │ <pad 4>      │  +20 .. +23
0x...5e8  ├──────────────┤ ◄── lh = &t.tasks
          │ tasks.next   │  +24   ← (struct task *)lh 把這裡當成 pid 讀，得到 0
          │ tasks.prev   │  +32
          ├──────────────┤
          │ state = 1    │  +40
          └──────────────┘
          0x5e8 - 0x5d0 = 0x18 = 24 = offsetof(struct task, tasks)
```

`container_of(NULL)` 得到 `0xffffffffffffffe8` = −24：它忠實地做了減法。這點很重要，後面「踩雷」會再談。

## 故意弄壞：型別檢查長什麼樣

把一個 `long *` 傳給期待 `struct list_head` 成員的 `container_of`：

```c
/* cof_bad.c（定義同上，省略） */
struct list_head { struct list_head *next, *prev; };
struct task { int pid; struct list_head tasks; struct list_head children; long state; };

struct task *bad1(long *p)
{
	return container_of(p, struct task, tasks);     /* long* 對上 list_head 成員 */
}
```

gcc 11.4：

```
$ gcc -std=gnu11 -Wall -c cof_bad.c
In file included from cof_bad.c:2:
cof_bad.c: In function ‘bad1’:
cof_bad.c:7:9: error: static assertion failed: "pointer type mismatch in container_of()"
    7 |         static_assert(__same_type(*(ptr), ((type *)0)->member) ||       \
      |         ^~~~~~~~~~~~~
cof_bad.c:17:16: note: in expansion of macro ‘container_of’
   17 |         return container_of(p, struct task, tasks);     /* long* 對上 list_head 成員 */
      |                ^~~~~~~~~~~~
```

clang 14 的訊息更直接，把比對的兩個型別都印出來了：

```
$ clang -std=gnu11 -Wall -c cof_bad.c
cof_bad.c:17:9: error: static_assert failed due to requirement '__builtin_types_compatible_p(long, struct list_head) || __builtin_types_compatible_p(long, void)' "pointer type mismatch in container_of()"
        return container_of(p, struct task, tasks);     /* long* 對上 list_head 成員 */
               ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
```

讀 kernel build log 時看到 `pointer type mismatch in container_of()`，就知道是第一個參數的型別和 `member` 的型別對不上。clang 的訊息直接告訴你兩邊各是什麼型別，這是 kernel 開發者喜歡用 clang 做第二輪編譯的原因之一。

### 型別檢查**擋不住**的錯

型別檢查只比對「型別」，不比對「是不是你以為的那個成員」。一個 struct 裡有兩個同型別成員時：

```c
/* cof_member.c（定義同上，省略） */
struct list_head { struct list_head *next, *prev; };
struct task {
	int pid;
	struct list_head tasks;      /* offset 8  */
	struct list_head children;   /* offset 24 */
};

int main(void)
{
	struct task t = { .pid = 42 };
	struct list_head *lh = &t.children;              /* 拿到的是 children */

	struct task *ok  = container_of(lh, struct task, children);
	struct task *bad = container_of(lh, struct task, tasks);   /* 型別相同，成員寫錯 */
	printf("&t=%p ok=%p bad=%p (差 %td bytes)\n",
	       (void *)&t, (void *)ok, (void *)bad, (char *)bad - (char *)&t);
	printf("ok->pid=%d bad->pid=%d\n", ok->pid, bad->pid);

	const struct list_head *clh = &t.tasks;
	struct task *mut = container_of(clh, struct task, tasks);  /* const 被丟掉，沒有警告 */
	mut->pid = 7;
	printf("t.pid after write through 'const' path = %d\n", t.pid);
	return 0;
}
```

```
$ gcc -std=gnu11 -Wall -Wextra -O0 cof_member.c -o m && ./m
&t=0x7ffe41cbe9a0 ok=0x7ffe41cbe9a0 bad=0x7ffe41cbe9b0 (差 16 bytes)
ok->pid=42 bad->pid=0
t.pid after write through 'const' path = 7
```

兩個問題，gcc 和 clang 加上 `-Wall -Wextra` 都**一聲不吭**：

1. **成員寫錯**：`bad` 比真正的物件起點高 16 bytes（`offsetof(children) − offsetof(tasks) = 24 − 8`），`bad->pid` 讀到的是 `tasks.prev` 的低 32 位元。在 kernel 裡，這種 bug 的症狀通常是「某個欄位的值莫名其妙」或者 KASAN 報越界，離真正的錯誤點很遠。
2. **const 被丟掉**：我們從一個 `const struct list_head *` 拿到可寫的 `struct task *`，然後寫進去了。這正是 container_of.h:16 註解「WARNING: any const qualifier of @ptr is lost.」說的事，也是 `container_of_const` 存在的理由。

讀碼時的對策：看到 `container_of(x, T, m)`，**去確認 `x` 是從 `T` 的哪個成員來的**。如果 `T` 有好幾個同型別成員（例如一個 struct 同時掛在兩個 list 上），這個檢查是必要的。

## `container_of_const`：用 `_Generic` 保留 const

第 35–39 行：

```c
#define container_of_const(ptr, type, member)				\
	_Generic(ptr,							\
		const typeof(*(ptr)) *: ((const type *)container_of(ptr, type, member)),\
		default: ((type *)container_of(ptr, type, member))	\
	)
```

`_Generic`（C11）在編譯期根據 `ptr` 的型別選分支：

```
ptr 的型別                       匹配的分支                 結果型別
const struct list_head *    →    const typeof(*(ptr)) *    const struct task *
struct list_head *          →    default                   struct task *
```

`const typeof(*(ptr)) *` 是個巧妙的寫法：不管 `ptr` 原本有沒有 const，`typeof(*(ptr))` 拿到的是被指物型別（可能帶 const），前面再加一個 const（重複的 const 會合併），就構成「指向 const 版本的指標」型別。只有當 `ptr` 本身就是指向 const 時，它才會和這個分支精確匹配。

它是 Greg Kroah-Hartman 在 2022 年 12 月加的（commit `64f6a5d1922b`，進 v6.2），commit message 說得很白：container_of 不保留 const，會讓傳入 const 指標的程式碼「拿回一個非 const 指標，然後在資料上亂塗（scribble all over the data）」。截至 v6.18，`container_of_const(` 在 141 個檔案出現 191 次，相對於 `container_of` 的兩萬多次，遷移才剛開始。而 2025 年的 commit `35cff7af7598`（Sakari Ailus，進 v6.17）在 container_of 的註解加上「Do not use container_of() in new code.」——方向很明確，只是存量太大。

## 演進史：用 git 考古

這是 [Ch 37](./37-git-archaeology.md) 手法的預告。kernel 的 git 歷史從 v2.6.12-rc2（2005 年）開始，那時 `container_of` 已經存在於 `include/linux/kernel.h`。用 `git show <tag>:<path>` 看各版本的定義：

```
$ git show v2.6.12:include/linux/kernel.h | grep -n -A3 "^#define container_of("
267:#define container_of(ptr, type, member) ({			\
268-        const typeof( ((type *)0)->member ) *__mptr = (ptr);	\
269-        (type *)( (char *)__mptr - offsetof(type,member) );})
```

2005 年的版本只有兩行。它也有型別檢查，只是**隱含的**：`__mptr` 宣告成「指向 member 型別的 const 指標」，用 `ptr` 初始化時，如果型別不相容，gcc 會給出 `initialization from incompatible pointer type` **警告**（不是錯誤）。

```
$ git show v4.13:include/linux/kernel.h | grep -n -A6 "^#define container_of("
858:#define container_of(ptr, type, member) ({				\
859-	void *__mptr = (void *)(ptr);					\
860-	BUILD_BUG_ON_MSG(!__same_type(*(ptr), ((type *)0)->member) &&	\
861-			 !__same_type(*(ptr), void),			\
862-			 "pointer type mismatch in container_of()");	\
863-	((type *)(__mptr - offsetof(type, member))); })
```

v4.13 換成**顯式**的編譯期檢查。用 pickaxe 找是哪個 commit 引入這段字串：

```
$ git log --format='%h %ad %an %s' --date=short -S'pointer type mismatch in container_of' v5.16 -- include/linux/kernel.h
d2a8ebbf8192 2021-11-08 Andy Shevchenko kernel.h: split out container_of() and typeof_member() macros
05e6557b8ed83 2018-03-29 NeilBrown staging: lustre: add container_of_safe()
c7acec713d14c 2017-07-12 Ian Abbott kernel.h: handle pointers to arrays better in container_of()
```

（blobless clone 第一次跑 `-S` 會邊跑邊下載 blob，在本環境花了好幾分鐘。）

`c7acec713d14`（Ian Abbott，進 v4.13）的 commit message 解釋了動機：當 `ptr` 指向一個**陣列**成員時，舊寫法的 `const typeof(...) *__mptr` 會變成「指向 const 陣列的指標」，在 ISO C 裡和「指向非 const 陣列的指標」不相容，GCC 4.9 會因此對完全正確的程式碼發出警告。改成先轉 `void *`、再用 `__same_type` 顯式比對，同時解決了誤報，也把「型別不符」從警告升級成**編譯失敗**。

之後在 `include/linux/container_of.h` 這個獨立檔案裡的歷史：

```
$ git log --format='%h %ad %an %s' --date=short v6.18 -- include/linux/container_of.h
35cff7af7598 2025-05-20 Sakari Ailus container_of: Document container_of() is not to be used in new code
2b8e35337605 2023-01-30 Andy Shevchenko container_of: Update header inclusions
64f6a5d1922b 2022-12-05 Greg Kroah-Hartman container_of: add container_of_const() that preserves const-ness of the pointer
7376e561fd2e 2022-10-24 Sakari Ailus linux/container_of.h: Warn about loss of constness
848dba781f19 2022-10-24 Greg Kroah-Hartman container_of: remove container_of_safe()
e1edc277e6f6 2021-11-08 Rasmus Villemoes linux/container_of.h: switch to static_assert
d2a8ebbf8192 2021-11-08 Andy Shevchenko kernel.h: split out container_of() and typeof_member() macros
```

整理成時間線（`git describe --contains` 查版本）：

| 時間 | 版本 | 事件 |
|---|---|---|
| ≤2005 | ≤v2.6.12 | 兩行版本，隱含型別檢查（只是警告） |
| 2017-07 | v4.13 | `c7acec713d14`：改成 `void *` + `BUILD_BUG_ON_MSG(__same_type)`，型別不符變編譯錯誤 |
| 2021-11 | v5.16 | `d2a8ebbf8192`：從龐大的 `kernel.h` 拆到獨立的 `container_of.h`（減少 include 依賴） |
| 2021-11 | v5.16 | `e1edc277e6f6`：`BUILD_BUG_ON_MSG` 換成 `static_assert` |
| 2022-10 | v6.2 | 移除 `container_of_safe()`；註解加上 const 遺失警告 |
| 2022-12 | v6.2 | `64f6a5d1922b`：新增 `container_of_const()` |
| 2025-05 | v6.17 | `35cff7af7598`：註解改成「新程式碼不要用 container_of()」 |

`e1edc277e6f6` 的 commit message 值得一讀，它比較了兩種錯誤訊息：`BUILD_BUG_ON` 要等最佳化器跑完、靠 `__attribute__((error))` 的函式呼叫沒被消掉才觸發，訊息是 `call to '__compiletime_assert_2' declared with attribute error`；`_Static_assert` 在前端就判定，訊息直接是 `static assertion failed: "pointer type mismatch in container_of()"`。我們上面在 user space 看到的就是後者。

## 衍生巨集：都是同一個東西

讀 kernel 時，下面這些名字**全部**直接展開成 `container_of`：

```c
/* include/linux/list.h:608-609（v6.18） */
#define list_entry(ptr, type, member) \
	container_of(ptr, type, member)

/* include/linux/list.h:619-620 */
#define list_first_entry(ptr, type, member) \
	list_entry((ptr)->next, type, member)

/* include/linux/list.h:1146 */
#define hlist_entry(ptr, type, member) container_of(ptr,type,member)

/* include/linux/rbtree.h:28 */
#define	rb_entry(ptr, type, member) container_of(ptr, type, member)

/* include/linux/llist.h:109-110 */
#define llist_entry(ptr, type, member)		\
	container_of(ptr, type, member)

/* include/linux/rculist.h:363-364 */
#define list_entry_rcu(ptr, type, member) \
	container_of(READ_ONCE(ptr), type, member)
```

為什麼要有這麼多別名？**為了讀碼**。`rb_entry(node, struct foo, rb)` 讓讀者立刻知道 `node` 是紅黑樹節點；`list_entry` 告訴你這是鏈結串列。功能相同，語意標籤不同。`list_entry_rcu` 則多包了一層 `READ_ONCE`（[Ch 21](./21-read-once-and-barriers.md)），因為 RCU 走訪時指標可能被並行修改。

`hlist_entry_safe` 是少數加了 NULL 檢查的變體：

```c
/* include/linux/list.h:1155-1157（v6.18） */
#define hlist_entry_safe(ptr, type, member) \
	({ typeof(ptr) ____ptr = (ptr); \
	   ____ptr ? hlist_entry(____ptr, type, member) : NULL; \
	})
```

它存在的原因就是上面實驗的 `container_of(NULL) = −24`：hlist 走到尾端時節點指標是 NULL，直接 `container_of` 會得到一個「看起來不是 NULL」的垃圾指標，後續的 `pos != NULL` 判斷就失效了。

另一個超常見的包裝是**型別專屬的 `to_xxx()` / `XXX_I()` 函式**：

```c
/* include/linux/cgroup_namespace.h:18-21（v6.18） */
static inline struct cgroup_namespace *to_cg_ns(struct ns_common *ns)
{
	return container_of(ns, struct cgroup_namespace, ns);
}
```

`rg -c "^static inline struct \w+ \*to_\w+\("` 粗估 v6.18 有一千多個這種函式（這個 regex 會漏掉多行宣告和非 `to_` 開頭的，例如上面的 `EXT4_I`），driver model 裡的 `to_platform_device()`、`to_pci_dev()` 都是。讀碼時看到 `to_foo(x)`，第一個假設就是「`container_of(x, struct foo, 某成員)`」，按 gd（或 `rg "to_foo\("`）跳過去確認是哪個成員即可。這個「struct 嵌入 + container_of」組合就是 kernel 的**繼承**，[Ch 42](./42-object-oriented-c.md) 會把它當物件導向來讀。

## 對比與取捨

| 方案 | 優點 | 缺點 | kernel 裡的地位 |
|---|---|---|---|
| `container_of` | 零成本、型別檢查、對欄位重排免疫 | 丟 const；同型別成員寫錯不會被抓 | 2 萬多處，主力 |
| `container_of_const` | 以上全部 + 保留 const | 稍長；需要 C11 `_Generic` | 新程式碼推薦，約 190 處 |
| 直接轉型 `(T *)p` | 最短 | 只在 offset 0 時正確；無型別檢查；欄位重排就壞 | coding review 會被退 |
| 把成員放第一個 + 轉型 | 某些 C 物件系統的慣例（GObject） | 一個物件只能有一個「父類」 | kernel 不依賴這個 |
| 在節點存 back pointer | 直觀 | 每個節點多 8 bytes、要維護一致性 | 少數地方（需要 back pointer 的語意時） |

## 踩雷集錦

1. **以為 container_of 是轉型**：它是**減法**。只有成員在 offset 0 時，數值上才和轉型相同。本章實驗：`(struct task *)lh` 比正確位址高 24 bytes，讀到 `pid=0`。
2. **以為型別檢查能抓所有錯**：同型別的不同成員（`tasks` vs `children`）完全抓不到，結果偏移 16 bytes、沒有任何警告。讀碼時要確認成員名字和指標的來源一致。
3. **對可能為 NULL 的指標用 container_of**：結果是 `−offsetof`（實測 `0xffffffffffffffe8`），不是 NULL。後面的 `if (!obj)` 判斷永遠為假。要嘛先檢查 NULL，要嘛用 `hlist_entry_safe` / `list_first_entry_or_null` 這類變體。
4. **以為它保留 const**：container_of.h:16 自己就警告了。從 const 指標拿到可寫指標然後寫入，編譯器不會阻止你（實測 `t.pid` 被改成 7）。新程式碼用 `container_of_const`。
5. **把巨集名字看成函式**：`container_of(ptr, type, member)` 的第二、三個參數是**型別名**和**成員名**，不是值；你不能傳變數進去。讀碼時看到 `container_of(work, struct foo, work)`，第一個 `work` 是變數、第二個 `work` 是成員名，同名只是慣例。

## 進階：再往深一層

- **在 kernel 原始碼裡搜「誰的成員被 container_of 回來」**：`rg -n "container_of\([^,]+, struct eventfd_ctx," ~/code_reading/linux` 找出所有從某個 struct 的成員倒推回來的地方，就能知道這個 struct 被嵌進了哪些通用機制（kref、wait queue、work……）。這是畫資料結構地圖的好方法（[練習 D](./practice-d-data-structure-map.md)）。
- **callback 的 `container_of` 模式**：work queue、timer、RCU callback 都只傳一個指向嵌入成員的指標給你的 callback，你的 callback 第一行幾乎一定是 `container_of`。這是 kernel 版的「closure」——[Ch 32](./32-async-irq-softirq-workqueue.md) 會大量看到。
- **`__randomize_layout` 的影響**：開啟 `CONFIG_RANDSTRUCT` 時，標了 `__randomize_layout` 的 struct（例如 `struct file`、`struct task_struct` 的一部分）欄位順序在編譯時被打亂。`container_of` 完全不受影響，因為 `offsetof` 是編譯期算的；寫死偏移量的程式碼（包括某些 exploit）就會失效。
- **`struct_group()`**：`include/linux/stddef.h` 裡有一個讓一組欄位同時能被整體存取與個別存取的巨集，和 `container_of` 一樣是「用 C 的佈局規則做抽象」的例子，可以延伸閱讀。

## 動手練習

1. 在 `cof.c` 裡的 `struct task` 開頭加一個 `char pad[100];`，重編、重跑，確認 `container_of` 仍然正確、`(struct task *)lh` 的錯誤更離譜。
2. 寫一個最小的侵入式鏈結串列：`struct list_head`、`list_add()`、`list_for_each()`，把三個 `struct task` 串起來，用 `container_of` 印出每個 `pid`。然後讓每個 `task` 同時掛在**兩條**串列上（`tasks` 和 `children`），故意在走訪 `children` 串列時寫成 `container_of(pos, struct task, tasks)`，觀察輸出。
3. 在 kernel tree 裡找三個 `to_xxx()` 函式，分別說出它們從哪個成員倒推回哪個 struct：`rg -n "^static inline struct \w+ \*to_\w+\(" include/linux/ | head`。
4. 用 `git log -L '/define container_of(/,+6:include/linux/container_of.h' v6.18` 看這幾行的逐次變化（`-L` 會跟到檔案建立為止，不會跨過 v5.16 的檔案搬遷——想想看為什麼，以及要怎麼接回 kernel.h 的歷史）。

## 本章重點整理

- `container_of(ptr, type, member)` = `(type *)((char *)ptr − offsetof(type, member))`，外加一個編譯期型別檢查；編譯後只剩一條減法（實測 `lea -0x110(%rbx)`）。
- 它是侵入式資料結構的倒推工具：kref、list_head、rb_node、work_struct 嵌在你的 struct 裡，callback 拿到的是成員指標。
- 型別檢查從 v4.13 起是硬錯誤，但抓不到「同型別、不同成員」和「const 遺失」；NULL 進去不會是 NULL 出來。
- 新程式碼用 `container_of_const`（v6.2 起）；`list_entry`、`rb_entry`、`hlist_entry`、`to_xxx()` 都是它的語意別名。

## 自我檢核

- [ ] 不看筆記，在白紙上寫出 `container_of` 的核心減法，並解釋為什麼要先轉成 `void *`（或 `char *`）。
- [ ] 有人在面試問你「`((type *)0)->member` 不是解參考 NULL 嗎？為什麼不會 crash？」，你怎麼回答？
- [ ] 能說出 `container_of` 型別檢查**抓得到**和**抓不到**的錯各一個例子。
- [ ] 能解釋 `hlist_entry_safe` 為什麼需要存在。
- [ ] 能說出 2017 年 `c7acec713d14` 和 2022 年 `64f6a5d1922b` 各自解決了什麼問題。
- [ ] 想一個你讀過的 user-space 專案（Redis、Lua、glib……），它是用侵入式還是非侵入式的串列？為什麼？

## 延伸閱讀

### 原始碼與 commit

- **`include/linux/container_of.h`、`include/linux/list.h`（v6.18）**
  - **讀哪裡**：container_of.h 全文（41 行）；list.h 的 `list_entry` 到 `list_for_each_entry` 一段（約 600–800 行）。
  - **能學到什麼**：`list_for_each_entry` 怎麼把 `container_of` 包進迴圈，是 [Ch 23](./23-list-head-hlist.md) 的預習。
- **commit `c7acec713d14`、`e1edc277e6f6`、`64f6a5d1922b`**（`git show <hash>`）
  - **讀哪裡**：commit message 全文。
  - **能學到什麼**：三個 commit 分別示範了「修誤報」「改善錯誤訊息」「補型別安全」三種演進動機，是讀 commit message 學設計理由的好範本。前提：會 `git show`。

### 部落格 / 技術文章

- **"container_of()"** — Greg Kroah-Hartman（kroah.com 部落格，2005）
  - **這篇說什麼**：Greg KH 用 driver model 的例子解釋 `container_of` 的原理，篇幅短，是很多人第一次讀懂它的那篇。
  - **讀哪裡**：整篇。注意它展示的是 2005 年的兩行版本，型別檢查的部分要以本章的 v6.18 為準。
  - **為什麼值得讀**：作者是 driver core 與 stable kernel 的維護者，也是 2022 年 `container_of_const` 的作者。

### 官方文件

- **[Linked Lists in Linux](https://docs.kernel.org/core-api/list.html)** — Nicolas Frattaroli（`Documentation/core-api/list.rst`，v6.18 tree 內）
  - **讀哪裡**：開頭的 Introduction（約第 60 行起解釋 container_of 模式）、第 227 行起 `list_entry()` 的說明；檔尾 775 行起是 list.h 的完整 kernel-doc API。
  - **能學到什麼**：官方文件用範例解釋侵入式串列與 `container_of`，並逐一說明 `_or_null`、`_safe` 變體的 NULL 行為。前提：讀完本章。

### 書籍

- **《Linux Kernel Development》（3rd ed.）** — Robert Love（Addison-Wesley, 2010）
  - **這本書的定位**：第 6 章「Kernel Data Structures」用 linked list 引出 `container_of`，是最經典的教科書解釋。
  - **讀哪幾章**：第 6 章前半（Linked Lists）。版本停在 2.6.34，巨集定義是舊的兩行版，但侵入式設計的論述完全適用。

`container_of` 告訴你「怎麼從成員找回物件」；下一章處理另一個每個函式都會遇到的問題：函式失敗時，kernel 怎麼告訴你、又怎麼收拾殘局。

→ [Ch 17 kernel 的錯誤慣例：-errno、ERR_PTR、goto 階梯與 cleanup.h](./17-kernel-error-conventions.md)
