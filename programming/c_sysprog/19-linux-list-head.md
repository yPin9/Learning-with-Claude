# Ch 19 — Linux list_head、container_of、list_sort 與洗牌

> **目標**：看懂 Linux 核心「把節點放進資料」的侵入式（intrusive）串列設計；能從 `offsetof` 與規格書條文推導 `container_of` 為什麼正確、哪裡踩在 ISO C 的灰色地帶；能把 `list.h` 搬到使用者空間實跑，並讀懂 `lib/list_sort.c` 的 bottom-up merge 與 2:1 合併規則；能寫出無偏差的 Fisher-Yates 洗牌。
> **對應原文**：[你所不知道的 C 語言：鏈結串列和非連續記憶體篇](https://hackmd.io/@sysprog/c-linked-list) 的「Linux 核心的 struct list_head」「簡化的實作」「Linux 核心的 list_sort 實作」「Fisher-Yates shuffle」等節

> **環境**：WSL Ubuntu 22.04 x86_64，gcc 11.4，glibc 2.35。核心原始碼的描述以 GitHub 上 torvalds/linux 的 master 為準，版本起點標注於文中；我們沒有在核心中實跑，使用者空間移植版則全部實跑。

## 為什麼需要這個？

[Ch 18](./18-linked-list-pointer-to-pointer.md) 的串列是「教科書式」的：節點型別裡放一個 `int value`。真實系統的資料千變萬化——行程、inode、網路封包、核心模組——如果每種資料都要寫一份 `task_list_add`、`inode_list_add`，程式碼會爆炸。

傳統的泛型作法有二種：

1. **節點存 `void *data`**（GLib 的 `GList`、多數教科書的「泛型串列」）：節點與資料分開配置，每個元素要二次 `malloc`；走訪時先讀節點、再跟著 `data` 指標讀資料，多一次可能的 cache miss；從資料找回它所在的節點要搜尋整條串列。
2. **用巨集生成型別專屬程式碼**（BSD `<sys/queue.h>`）：效率好，但每種型別產生一份程式碼。

Linux 核心選了第三條路：**不把資料放進節點，而是把節點放進資料**。任何結構體只要嵌入一個 `struct list_head` 成員，就能用同一組函式操作；需要外層資料時，用 `container_of` 從成員位址反推結構體位址。這招的威力在核心裡隨處可見：`struct task_struct` 本身就有十幾個 `struct list_head` 成員，同一個行程同時串在好幾條串列上。

## 先建立直覺

把結構體想成一個貨櫃，`struct list_head` 是焊在貨櫃側面的一對掛鉤（`prev`、`next`）。串列只是把一排貨櫃的掛鉤用鐵鍊串起來，鐵鍊**只連掛鉤，不連貨櫃本體**。沿著鐵鍊走到某個掛鉤時，只要知道「掛鉤焊在貨櫃側面第幾公分」，往回量那段距離就是貨櫃的起點——這就是 `container_of`。

```
      struct task (48 bytes)              struct task
 ┌─────────────────────────┐        ┌─────────────────────────┐
 │ pid        (offset 0)   │        │ pid                     │
 │ name[12]   (offset 4)   │        │ name[12]                │
 │ run_node   (offset 16) ─┼──next─▶│ run_node                │──▶ ...
 │ {next,prev}             │◀─prev──┼ {next,prev}             │
 │ all_node   (offset 32)  │        │ all_node                │
 └─────────────────────────┘        └─────────────────────────┘
        ▲
        │ container_of(&run_node, struct task, run_node)
        └── = (char *)&run_node - 16
```

而且整條串列是**雙向環狀、附哨兵**：頭（`head`）是一個獨立的 `struct list_head`，不屬於任何貨櫃；空串列時它的 `next` 與 `prev` 都指向自己。Ch 18 說過，哨兵讓每個 `prev`、`next` 永遠有效，插入刪除不再有特例。

```
  空串列：              有三個元素：
  ┌──────┐            ┌──────┐   ┌─────┐   ┌─────┐   ┌─────┐
  │ head │◀┐          │ head │◀─▶│ cat │◀─▶│ eat │◀─▶│ fat │
  └──────┘ │          └──────┘   └─────┘   └─────┘   └─────┘
   │  ▲    │             ▲                               │
   └──┘────┘             └───────────────────────────────┘
  next=prev=&head
```

## struct list_head 與侵入式設計

核心的定義在 [`include/linux/types.h`](https://github.com/torvalds/linux/blob/master/include/linux/types.h)，只有二個指標：

```c
struct list_head {
    struct list_head *next, *prev;
};
```

侵入式設計帶來三個好處：

- **一次配置**：節點就在資料裡，不需要另外配置節點。
- **$O(1)$ 移除**：手上有物件指標，就有它的 `list_head`，直接 `list_del`，不必先搜尋。這對核心至關重要：例如行程結束時要把它從各種串列移除，不能每條串列都掃一遍。
- **同時在多條串列上**：嵌入幾個 `list_head` 就能同時掛幾條。

代價也要說清楚：**結構體必須預先知道自己會被放進哪些串列**（要事先宣告成員），而 `container_of` 依賴 GNU C 擴充與一段 ISO C 沒明說可以的指標運算（下一節）。GLib 的 `GList` 正好相反：資料型別不用改，可以放進任意多條串列，代價是每個元素一次額外配置與一次額外間接。FreeRTOS 的 `ListItem_t` 走中間路線：節點內放一個 `pvOwner` 直接存外層物件指標，多花一個指標換來不依賴 `offsetof`。

### 核心裡的真實用法

- 所有行程以 `task_struct.tasks` 串成以 `init_task` 為頭的環狀串列，`for_each_process` 從 `init_task` 繞一圈；`children`（頭）與 `sibling`（節點）表達行程樹——**同一個結構體在不同串列分別扮演頭與節點**。
- 每個 `struct super_block` 以 `s_inodes` 串起它的所有 inode，節點是 `struct inode` 的 `i_sb_list`。
- 已載入的模組以 `struct module` 的 `list` 成員串在 `kernel/module/main.c` 的全域 `LIST_HEAD(modules)` 上。

二個常見變體：**hlist**（`struct hlist_head` 只有一個 `first` 指標，頭的大小減半，用在雜湊表的 bucket 陣列；節點的 `pprev` 是 Ch 18 指標的指標手法）與 **llist**（`include/linux/llist.h`，單向、以 `cmpxchg` 加入節點的 lock-free 串列，[Ch 20](./20-lock-free-linked-list.md) 會講到 CAS）。

## container_of 與 offsetof：從推導到規格書

### 一道減法

```c
#define container_of(ptr, type, member) \
    ((type *) ((char *) (ptr) - offsetof(type, member)))
```

推導只有三步：

1. 外層物件位址為 $A$，成員 `member` 在物件內的位元組偏移量為 $d$，則成員位址為 $A + d$。
2. 手上有 `ptr`，它的值是 $A + d$。
3. 所以 $A = \text{ptr} - d$。

$d$ 由 `offsetof(type, member)` 提供。C11 7.19 §3 定義 `offsetof` 展開為 `size_t` 型別的整數常數運算式，值為成員距結構體開頭的**位元組**偏移量。

### 為什麼一定要先轉 `char *`：故意弄錯

指標減法的單位是「所指型別的大小」（[Ch 3](./03-pointer-basics.md) 的指標算術）。直接對 `struct list_head *` 減 16，實際會往回退 $16 \times \text{sizeof(struct list\_head)} = 16 \times 16 = 256$ bytes。實驗：

```c
#include <stdio.h>
#include <stdint.h>
#include "list.h"

struct task {
    int pid;
    char name[12];
    struct list_head run_node;   /* 掛在 run queue 上 */
    struct list_head all_node;   /* 掛在「所有 task」串列上 */
};

/* 錯誤版：忘了先轉成 char * */
#define bad_container_of(ptr, type, member) \
    ((type *) ((ptr) - offsetof(type, member)))

int main(void)
{
    printf("offsetof pid=%zu name=%zu run_node=%zu all_node=%zu sizeof=%zu\n",
           offsetof(struct task, pid), offsetof(struct task, name),
           offsetof(struct task, run_node), offsetof(struct task, all_node),
           sizeof(struct task));

    struct task t = {.pid = 42, .name = "init"};
    struct list_head *rn = &t.run_node, *an = &t.all_node;
    struct task *a = container_of(rn, struct task, run_node);
    struct task *b = container_of(an, struct task, all_node);
    printf("&t=%p  from run_node=%p  from all_node=%p  pid=%d\n",
           (void *) &t, (void *) a, (void *) b, b->pid);

    struct task *bad = bad_container_of(rn, struct task, run_node);
    printf("bad=%p  (差了 %td bytes)\n", (void *) bad,
           (char *) &t - (char *) bad);

    /* 同一物件同時在二條串列上 */
    LIST_HEAD(runq);
    LIST_HEAD(all);
    struct task u = {.pid = 7, .name = "sshd"};
    list_add_tail(&t.all_node, &all);
    list_add_tail(&u.all_node, &all);
    list_add_tail(&u.run_node, &runq);   /* 只有 u 可執行 */
    struct task *it;
    printf("all :");
    list_for_each_entry (it, &all, all_node)
        printf(" %d(%s)", it->pid, it->name);
    printf("\nrunq:");
    list_for_each_entry (it, &runq, run_node)
        printf(" %d(%s)", it->pid, it->name);
    printf("\n");
    return 0;
}
```

```
$ gcc -Wall -Wextra -g -fsanitize=address,undefined cof.c -o cof && ./cof
offsetof pid=0 name=4 run_node=16 all_node=32 sizeof=48
&t=0x7ffd462bac90  from run_node=0x7ffd462bac90  from all_node=0x7ffd462bac90  pid=42
bad=0x7ffd462baba0  (差了 240 bytes)
all : 42(init) 7(sshd)
runq: 7(sshd)
```

（位址每次執行不同。）`run_node` 在偏移 16：`pid` 4 bytes、`name` 12 bytes，剛好 16，`struct list_head` 要求 8 bytes 對齊，不需 padding（對齊規則見 [Ch 16](./16-alignment-and-padding.md)）。錯誤版退了 256 bytes，相對於 `&t` 是 $256 - 16 = 240$。更糟的是，**gcc 對錯誤版沒有任何警告**，加了 `-Wall -Wextra` 也一樣——這種 bug 只能靠理解與測試抓。

### 型別檢查版：讓編譯器替你把關

核心與 sysprog21/linux-list 用的是 GNU C statement expression 版本：

```c
#define container_of(ptr, type, member)                            \
    __extension__({                                                \
        const __typeof__(((type *) 0)->member) *pmember__ = (ptr); \
        (type *) ((char *) pmember__ - offsetof(type, member));    \
    })
```

- `({ ... })`（statement expression）讓巨集裡能宣告區域變數，最後一個運算式的值就是整個運算式的值；`__extension__` 讓 `-pedantic` 不對它抱怨。
- `((type *) 0)->member` 只出現在 `__typeof__` 裡，**不會被求值**，只是借來取得成員型別，因此不會真的解參考空指標。
- 把 `ptr` 指定給「指向成員型別的指標」，型別不符就會觸發警告。傳錯成員試試：

```
$ gcc -Wall -Wextra -c cof_type.c
list.h:14:61: warning: initialization of ‘const struct list_head *’ from incompatible pointer type ‘int *’ [-Wincompatible-pointer-types]
   14 |         const __typeof__(((type *) 0)->member) *pmember__ = (ptr); \
cof_type.c:5:12: note: in expansion of macro ‘container_of’
    5 |     return container_of(&t->pid, struct task, run_node);  /* 傳錯成員 */
```

現行核心更進一步，在 [`include/linux/container_of.h`](https://github.com/torvalds/linux/blob/master/include/linux/container_of.h)（v5.16 起獨立成檔）以 `static_assert` 與 `__same_type` 把它變成編譯**錯誤**；v6.2 起另有保留 `const` 的 `container_of_const`。巨集與 statement expression 的細節見 [Ch 27](./27-preprocessor-basics.md)。

### 規格書的灰色地帶

`container_of` 的正確性靠三條規則，其中一條 ISO C 沒有明說：

1. **`offsetof` 的值是位元組偏移**（C11 7.19 §3）——沒問題。
2. **物件指標轉成字元指標指向該物件的最低位址位元組，連續遞增可走遍該物件**（C11 6.3.2.3 §7）。注意它說的是「該物件」：`(char *) &t.run_node` 的「物件」是 `run_node` 這個成員，往回減到外層結構體開頭，等於把「該物件」解讀為外層物件。ISO C 的文字沒有明言可以這樣延伸；GCC、Clang 與 Linux 核心都把它當作外層物件內的位移，這是業界一致仰賴的實際行為。
3. **若成員恰為第一個成員**，C11 6.7.2.1 §15 保證 "A pointer to a structure object, suitably converted, points to its initial member ... and vice versa"，此時直接轉型即可，連減法都不需要，完全符合標準。

還有一個更明確的 UB 藏在走訪巨集裡，下一節講。

## 把 list.h 搬到使用者空間

核心的 `list.h` 依賴 `WRITE_ONCE`、`__always_inline` 等核心巨集，不能直接在使用者空間編譯。[sysprog21/linux-list](https://github.com/sysprog21/linux-list) 抽出了可獨立編譯的版本。本章實驗用的是一個更小的子集（保留核心語意，省略 `WRITE_ONCE`、poison 與完整性檢查），核心部分如下：

```c
static inline void INIT_LIST_HEAD(struct list_head *head)
{
    head->next = head;
    head->prev = head;
}

static inline void list_add(struct list_head *node, struct list_head *head)
{
    struct list_head *next = head->next;
    next->prev = node;
    node->next = next;
    node->prev = head;
    head->next = node;
}

static inline void list_add_tail(struct list_head *node, struct list_head *head)
{
    struct list_head *prev = head->prev;
    prev->next = node;
    node->next = head;
    node->prev = prev;
    head->prev = node;
}

static inline void list_del(struct list_head *node)
{
    node->next->prev = node->prev;
    node->prev->next = node->next;
}

static inline int list_empty(const struct list_head *head)
{
    return head->next == head;
}
```

`list_add` 插在 `head` 之後（當 stack 用），`list_add_tail` 插在 `head` 之前——在環上，「head 之前」就是尾端（當 queue 用）。把 `list_add` 的四行對照下圖，注意**沒有任何 `if`**：

```
插入前：  head ⇄ A               插入 N 到 head 之後：
          ┌────┐   ┌───┐          ┌────┐   ┌───┐   ┌───┐
          │head│⇄  │ A │    →     │head│⇄  │ N │⇄  │ A │
          └────┘   └───┘          └────┘   └───┘   └───┘
  1. next = head->next  (= A)
  2. A->prev    = N
  3. N->next    = A
  4. N->prev    = head
  5. head->next = N
空串列時 A 就是 head 自己，四行照樣成立。
```

`list_splice`、`list_cut_position`、`list_move_tail` 等其餘操作在 sysprog21/linux-list 裡都有，語意與限制整理如下：

| 操作 | 語意 | 限制 |
|---|---|---|
| `LIST_HEAD`、`INIT_LIST_HEAD` | `next`、`prev` 指向自己，即空串列 | 前者用於宣告，後者用於已配置的物件 |
| `list_add`、`list_add_tail` | 插入 `head` 之後／之前 | `node` 不可已在其他串列上 |
| `list_del` | 前後節點互指，略過 `node` | 不釋放記憶體；之後 `node` 的指標無意義 |
| `list_del_init` | 移除後 `node` 自成空串列 | 之後可再 `list_empty`、重新加入 |
| `list_move`、`list_move_tail` | 移除後插入另一串列開頭／結尾 | 二串列可為同一條 |
| `list_splice`、`list_splice_tail` | `list` 整段接到 `head` 之後／之前 | `list` 的頭仍指著已移走的節點，須重新初始化 |
| `list_for_each`、`list_for_each_entry` | 從 `head->next` 走到回到 `head` | 迴圈中**不可**移除目前節點 |
| `list_for_each_safe`、`list_for_each_entry_safe` | 事先以 `safe` 記住下一個 | 只允許移除目前節點 |

### list_for_each_entry 與它的 UB

```c
#define list_for_each_entry(entry, head, member)                       \
    for (entry = list_first_entry(head, __typeof__(*entry), member);   \
         &entry->member != (head);                                     \
         entry = list_entry(entry->member.next, __typeof__(*entry), member))
```

它必須是巨集：`entry` 的型別每次都不同，`member` 是成員**名稱**而不是值，函式做不到（[Ch 27](./27-preprocessor-basics.md)）。終止條件 `&entry->member != (head)` 藏著一個細節：最後一輪，`entry` 是對 `head` 套用 `container_of` 得到的——但 `head` 沒有嵌在任何外層結構體裡，這個 `entry` 不指向有效物件。程式只拿它算 `&entry->member`（結果就是 `head`）來比較，沒有存取其他欄位，所以實務上不會出事。然而依 C11 6.5.6 §7–8，獨立的 `head` 被視為長度 1 的陣列，`(char *) head - offsetof(...)` 落在這個物件之前，**指標運算本身就是未定義行為**。Linux 核心仰賴 GCC 與 Clang 的實際行為。（現行核心把這個比較包成 `list_entry_is_head()`，v5.10 起；那是可讀性上的整理，底層的指標運算並沒有改變。）

### `_safe` 不是裝飾：一個會 segfault 的 UAF

刪除所有奇數，用一般版走訪：

```c
static void remove_odd(struct list_head *head)
{
    item_t *it;
    list_for_each_entry (it, head, list) {   /* 錯：迴圈中刪除目前節點 */
        if (it->value & 1) {
            list_del(&it->list);
            free(it);
        }
    }
}
```

```
$ gcc -Wall -Wextra -g -fsanitize=address -DBUGGY foreach_uaf.c -o fe_bug && ./fe_bug
==12682==ERROR: AddressSanitizer: heap-use-after-free on address 0x503000000048 ...
READ of size 8 at 0x503000000048 thread T0
    #0 0x5b93710d6568 in remove_odd /home/ypp/c_sysprog_lab/p3b/foreach_uaf.c:15
...
0x503000000048 is located 8 bytes inside of 24-byte region [0x503000000040,0x503000000058)
freed by thread T0 here:
    #0 0x71b7fc8b4537 in __interceptor_free ...
    #1 0x5b93710d6543 in remove_odd /home/ypp/c_sysprog_lab/p3b/foreach_uaf.c:21
```

`free(it)` 之後，迴圈的遞增運算式讀 `it->list.next`——偏移 8 正是 `list.next`（`int value` 加 4 bytes padding）。不開 ASan 直接跑：

```
$ gcc -Wall -Wextra -g -DBUGGY foreach_uaf.c -o fe_bug2 && ./fe_bug2
Segmentation fault (core dumped)
```

為什麼不是「碰巧能跑」？回想 [Ch 17](./17-malloc-internals.md)：glibc 2.35 把小 chunk 放進 tcache 時，在 user data 的第一個 word 寫 `next`、第二個 word 寫 `key`。我們的 `list.next` 就在第二個 word，`free` 之後它被 `key` 蓋掉，迴圈沿著垃圾指標走下去就崩潰了。（這是對 glibc 2.35 行為的解釋；其他配置器上可能「看起來正常」，那更危險。）改用 `_safe` 版本，事先把下一個節點存進 `safe`：

```
left: 2 4 6  empty=1
```

核心本身用另一種方式讓這類 bug 早點爆：`list_del` 把被刪節點的 `next`、`prev` 填成 poison 值 `LIST_POISON1`、`LIST_POISON2`（`0x100`、`0x122` 加上 `POISON_POINTER_DELTA`，x86-64 預設 `0xdead000000000000`），沿著已刪節點走訪會立刻踩到未映射的位址。`CONFIG_DEBUG_LIST` 與 v6.6 起的 `CONFIG_LIST_HARDENED` 則在每次加入／刪除前檢查 `next->prev == prev` 等一致性，發現串列毀損就拒絕更新並回報——這對資安很重要，因為改寫串列指標是經典的漏洞利用原語（unlink attack，見 [Ch 17](./17-malloc-internals.md)）。

### `list_add` 為什麼有 `WRITE_ONCE`

核心的 `__list_add()` 最後一個指定寫成 `WRITE_ONCE(prev->next, new)`，`list_empty()` 寫成 `READ_ONCE(head->next) == head`。理由見 Paul E. McKenney 在 v4.5 的 commit："Code that does lockless emptiness testing of non-RCU lists is relying on the list-addition code to write the list head's ->next pointer atomically." 不持 lock 的 `list_empty()` 只讀 `head->next`；寫入端必須保證這個指標是**單次**寫入，讀端看到的不是舊值就是新值，而不是編譯器拆開、合併或重複的中間狀態。`WRITE_ONCE` 的本體是透過 `volatile` lvalue 的一次存取（`*(volatile typeof(x) *)&(x) = (val)`）。但它只約束編譯器，**不提供 CPU 層級的記憶體順序**；要讓讀端透過新指標看到節點已初始化的內容，得用 release 語意，這是 `rculist.h` 的 `list_add_rcu` 改用 `rcu_assign_pointer()` 的原因。記憶體順序是 [Ch 20](./20-lock-free-linked-list.md) 的主題。

## list_sort：bottom-up merge 的工程極致

[`lib/list_sort.c`](https://github.com/torvalds/linux/blob/master/lib/list_sort.c) 是核心通用的串列排序，被 XFS、Btrfs、ext4、RAID5、blk-mq、DRM 等數十處呼叫，常見用途是在送出 I/O 前依磁碟位址排序，讓區塊層更容易合併相鄰請求。介面：

```c
typedef int (*list_cmp_func_t)(void *, const struct list_head *,
                               const struct list_head *);
void list_sort(void *priv, struct list_head *head, list_cmp_func_t cmp);
```

`cmp` 只需回答「`a` 是否該排在 `b` 之後」（回傳 > 0 表示是）。因為 `list_sort` 穩定，且總是以輸入中較早的元素當 `a`，所以不必區分 `a < b` 與 `a == b`，回傳布林值 `ea->key > eb->key` 就夠。

### 設計限制：為什麼不能直接用 Ch 18 的版本

核心的排序有幾個硬限制，每一條都塑造了最終設計：

| 限制 | 排除了什麼 | `list_sort` 的回應 |
|---|---|---|
| 不能配置記憶體（可能在不能睡眠的情境呼叫） | natural merge sort 的 run 陣列 | 把 pending 子串列串在節點的 `prev` 上 |
| stack 很小（核心 stack 通常 8–16 KiB） | 深遞迴、大陣列 | 不遞迴；不用 64 格陣列 |
| `cmp` 經函式指標呼叫，`CONFIG_RETPOLINE` 下很貴 | 比較次數多的作法 | 2:1 延後合併，減少比較 |
| 串列可能超出 cache | 收尾時走訪超長串列 | 盡早合併，同時限制不平衡度 |

現行演算法來自 George Spelvin 在 v5.2（2019）的修改，動機正是 retpoline 讓間接呼叫變貴。

### 資料結構：借用 `prev`

排序一開始先拆掉環：`head->prev->next = NULL`，之後每個已排序子串列只用 `next` 串成以 `NULL` 結尾的單向串列。空出來的 `prev` 另作他用：**子串列第一個節點的 `prev` 指向下一個（較舊的）子串列**，`pending` 指向最新的子串列：

```
處理完 5 個節點後（e1..e5 為輸入次序）：

pending
   │
   ▼
 [e5] ──prev──▶ [e3 → e4] ──prev──▶ [e1 → e2] ──prev──▶ NULL
 1 個節點        2 個節點             2 個節點
```

子串列的大小不另外記錄：都是 2 的冪，由計數 `count` 的位元樣式推得。

### 合併時機：2:1 規則

Ch 18 的二進位計數器是 fully eager：一出現二個同大小的子串列就立刻合併。問題在 $n$ 略大於 2 的冪時收尾極不平衡（1024 對 4）。`list_sort` 改成延後：二個大小為 $2^k$ 的子串列，**要等它們後面又累積了 $2^k$ 個節點才合併**。原始碼註解：

> This mergesort is as eager as possible while always performing at least 2:1 balanced merges. Given two pending sublists of size 2^k, they are merged to a size-2^(k+1) list as soon as we have 2^k following elements.

實作上的判斷只看 `count`：每輪找 `count` 最低的 0 位元（位置 $k$），若 `count` 不是 $2^k - 1$ 就合併二個 $2^k$ 子串列，再把下一個輸入節點加入 pending。

```c
do {
    size_t bits;
    struct list_head **tail = &pending;

    /* 找 count 最低的 0 位元；每跳過一個 1，tail 沿 prev 前進一個子串列 */
    for (bits = count; bits & 1; bits >>= 1)
        tail = &(*tail)->prev;
    if (likely(bits)) {                       /* count 不是 2^k - 1 */
        struct list_head *a = *tail, *b = a->prev;
        a = merge(priv, cmp, b, a);           /* 較舊的 b 當第一個參數：穩定 */
        a->prev = b->prev;
        *tail = a;                            /* 間接指標寫回，無特例 */
    }
    list->prev = pending;                     /* 把下一個輸入節點推入 pending */
    pending = list;
    list = list->next;
    pending->next = NULL;
    count++;
} while (list);
```

`tail` 是指標的指標——合併結果直接寫回 `*tail`，不論它是 `pending` 本身還是某個節點的 `prev`，又是 Ch 18 的手法。`merge()` 本身也是 Ch 18 `mergeTwoLists` 的間接指標版本，相等時取 `a`（註解寫著 "important for sort stability"）。

輸入耗盡後由小到大合併所有 pending，最後一次交給 `merge_final()`：它在合併的同時重建 `prev` 並把尾端接回 `head`，恢復環狀雙向串列，省下一趟修補走訪。

### 在使用者空間實跑

我們依上述演算法在使用者空間重寫了 `list_sort`（`merge`、`merge_final` 與主迴圈，加上比較次數計數），並以 `verify()` 檢查「已排序、相等鍵值保持原次序、每個 `next->prev == p`」三件事。開 ASan/UBSan：

```
$ gcc -Wall -Wextra -g -fsanitize=address,undefined list_sort.c -o ls && ./ls
n=     0 ok=1 cmp=0
n=     1 ok=1 cmp=0
n=     2 ok=1 cmp=1
n=     3 ok=1 cmp=3
n=  1028 ok=1 cmp=9018
n=100000 ok=1 cmp=1542573
```

打開追蹤印出 16 個節點時每輪是否合併：

```
$ ./ls t
count= 0 (no merge)
count= 1 (no merge)
count= 2 merge
count= 3 (no merge)
count= 4 merge
count= 5 merge
count= 6 merge
count= 7 (no merge)
count= 8 merge
...
count=14 merge
count=15 (no merge)
```

不合併的輪次恰好是 `count` 為 $2^k - 1$ 的 0、1、3、7、15；其餘 11 輪各合併一次，收尾再把剩下的 5 個子串列（大小 1、1、2、4、8）併 4 次，共 $11 + 4 = 15 = n - 1$ 次合併。完整的 pending 狀態變化表：

| `count` | 二進位 | 合併 | pending（新 → 舊） |
|--:|:-:|:-:|:--|
| 0 | `0000` | | 1 |
| 1 | `0001` | | 1, 1 |
| 2 | `0010` | 1 + 1 | 1, 2 |
| 3 | `0011` | | 1, 1, 2 |
| 4 | `0100` | 1 + 1 | 1, 2, 2 |
| 5 | `0101` | 2 + 2 | 1, 1, 4 |
| 6 | `0110` | 1 + 1 | 1, 2, 4 |
| 7 | `0111` | | 1, 1, 2, 4 |
| 8 | `1000` | 1 + 1 | 1, 2, 2, 4 |

看 `count = 5` 這一輪：被合併的二個大小 2 的子串列是 e1..e4，在它們之後已經有 e5，加上本輪推入的 e6，正好累積 2 個——2:1 規則的「等後面有 $2^k$ 個節點才合併」。

### 比較次數：三種合併順序的實測

把 `list_sort`、Ch 18 的 fully eager 計數器、以及已知長度的 top-down（對半切）放在一起，共用同一個 `merge()`，每種 $n$ 用 64 組隨機排列取平均：

```
$ ./cmpcount 1028
n=1028, 64 random permutations, average comparisons:
  list_sort   8991.4
  fully eager 9768.7
  top-down    8990.6
$ ./cmpcount 1024
  list_sort   8949.5
  fully eager 8949.5
  top-down    8949.5
$ ./cmpcount 1536
  list_sort   14438.6
  fully eager 14438.6
  top-down    14339.8
```

（輸出略去標題行。）解讀：

- $n = 1024$（2 的冪）時三者的合併樹完全相同，比較次數一致。
- $n = 1028$ 時 fully eager 多約 8.6%，就是收尾的 1024 對 4 在作怪；`list_sort` 與 top-down 幾乎一樣。原文引用 Spelvin 的數字是 9769 對 8988，與我們的結果相符。
- $n = 1536 = 3 \cdot 2^9$ 時 `list_sort` 的最後一次合併必為 1024 對 512（2:1），top-down 是 768 對 768，所以 top-down 平均略少。這正是「2:1 是這類作法能保證的最緊界限」的實例。

### 為什麼是 2:1：證明骨架與最佳性

設收尾時某個作為較舊一方的子串列 $P$ 大小為 $2^j$，比它新的節點共 $a$ 個（收尾時另一方恰好是這 $a$ 個節點併成的串列）。可以證明 $2^{j-1} \le a < 2^{j+1}$：

- **上界**：如果 $a \ge 2^{j+1}$，代表 $P$ 後面某個同大小 $2^j$ 的子串列之後又累積了 $2^j$ 個節點，那時 2:1 規則早就會把它們合併掉。
- **下界**：$P$ 是由二個 $2^{j-1}$ 的子串列在「其後累積 $2^{j-1}$ 個節點」那一輪合併而成，這些節點始終比 $P$ 新。

所以收尾的每次合併比例都在 1:2 到 2:1 之間（原始碼註解："varies from 2^(k-1) ... to 2^(k+1) - 1"），迴圈中的合併則都是等大小的。原文更進一步推得：`list_sort` 的合併樹就是遞迴地把 $n$ 切成「$(n/3, 2n/3]$ 中唯一的 2 的冪」與其餘部分，葉節點全落在相鄰二層。依 Golin 與 Sedgewick 的結果，這代表**最差情況比較次數** $W(n) = n\lceil \log_2 n \rceil - 2^{\lceil \log_2 n \rceil} + 1$ 達到二路線性合併排序的下限（$n = 1028$ 時為 9261）；**平均**比較次數則略遜於 top-down，代價是收尾少走訪一趟長串列、更 cache 友善。

### 演進摘要

| 版本 | 變更 |
|:--|:--|
| v2.6.33（2010） | Dave Chinner 以 UBIFS 的版本建立通用 `lib/list_sort.c`（每輪走訪整條串列） |
| v2.6.34（2010） | Don Mullis 改為深度優先，`part[]` 陣列存 pending、相同大小立即合併（即 fully eager） |
| v5.2（2019） | George Spelvin 以 `prev` 鏈結取代 `part[]`，改為 2:1 延後合併 |
| v5.13（2021） | Sami Tolvanen 引入 `list_cmp_func_t`，`cmp` 參數改 `const`（修正 CFI 檢查到的函式指標型別不符） |

v5.13 這一條值得一提：舊版把呼叫端的 `int (*)(void *, struct list_head *, struct list_head *)` 轉型成參數帶 `const` 的型別再呼叫。依 C11 6.5.2.2 §9，透過不相容型別的函式指標呼叫是未定義行為，Control-Flow Integrity（CFI）在執行期攔下的正是這種型別不符——規格書的抽象條文，在核心的資安強化中變成了實際的崩潰。更新的歷史（v6.x 之後的修改）見原文的「演進」表。

### 順帶一提：鏈結串列上的 quick sort

原文也示範了 `list_head` 版 quick sort：以第一個節點為 pivot，用 `list_move_tail` 把節點分到 `less`、`greater` 二條串列，遞迴後 `list_splice` 接回。因為節點按原次序移入子串列，它**是穩定的**（陣列版 quick sort 靠交換分割，通常不穩定）。但鏈結串列無法隨機取 pivot，已排序輸入會退化成 $O(n^2)$、遞迴深度 $n$——這是核心選擇 merge sort 的原因之一。

## Fisher-Yates shuffle 與 modulo bias

洗牌要產生一個**均勻**的隨機排列：$n!$ 種排列機率相同。

### 從紙筆到 Durstenfeld

Fisher 與 Yates 在 1938 年描述的紙筆方法：每次從剩下的 $m$ 個數字中隨機選第 $k$ 個劃掉、抄到結果序列。每步都要數到第 $k$ 個未劃掉的，$O(n^2)$。Durstenfeld 在 1964 年的 *CACM* Algorithm 235 改成：把選中的元素與目前範圍的最後一個交換，範圍縮小 1，$O(n)$、就地完成。Knuth 在 TAOCP 第 2 卷 3.4.2 節列為 Algorithm P，所以也叫 Knuth shuffle：

```c
for (i = n - 1; i > 0; i--) {
    j = rand_below(i + 1);    /* j 均勻分布於 [0, i]，注意包含 i */
    swap(a[i], a[j]);
}
```

**為什麼均勻**：最後一格有 $n$ 種等機率的選擇，倒數第二格有 $n-1$ 種……共 $n!$ 條等機率的路徑，且每條路徑產生不同的排列，所以每種排列機率恰為 $1/n!$。

### 在鏈結串列上

$O(n)$ 的前提是隨機存取，鏈結串列沒有。二種取捨：照 Fisher-Yates 原始作法每次走到第 $k$ 個節點再 `list_move_tail`，$O(n^2)$、零額外記憶體；或像下面這樣，先把節點位址收集到陣列、在陣列上洗牌、再依新次序 `list_add_tail` 串回來，$O(n)$、代價是 $O(n)$ 記憶體與可能失敗的配置。**只動指標、不搬資料**，節點的位址（身分）不變，其他資料結構對它的參照依然有效：

```c
static int list_shuffle(struct list_head *head)
{
    struct list_head *node, **v;
    size_t n = 0, i;
    list_for_each (node, head)
        n++;
    if (n < 2)
        return 0;
    if (n > UINT32_MAX || n > SIZE_MAX / sizeof(*v))   /* 防止乘法回繞 */
        return -1;
    v = malloc(n * sizeof(*v));
    if (!v)
        return -1;
    i = 0;
    list_for_each (node, head)
        v[i++] = node;
    for (i = n - 1; i > 0; i--) {
        size_t j = rand_below((uint32_t) (i + 1));
        struct list_head *tmp = v[i];
        v[i] = v[j];
        v[j] = tmp;
    }
    INIT_LIST_HEAD(head);
    for (i = 0; i < n; i++)
        list_add_tail(v[i], head);
    free(v);
    return 0;
}
```

`n > SIZE_MAX / sizeof(*v)` 的檢查：`n * sizeof(*v)` 以 `size_t` 計算，超出範圍會回繞成小值（無號運算不溢位，而是取模，C11 6.2.5 §9），`malloc` 配到過小的空間卻不報錯，之後的寫入就是 heap overflow。這是 [Ch 22](./22-integer-overflow.md) integer overflow 的典型樣態。

### 驗證：卡方檢定，以及一個差一的 bug

4 張牌有 24 種排列。每次都從 `0 1 2 3` 開始洗 240 萬次，統計各排列出現次數，算卡方統計量 $\chi^2 = \sum (O_i - E_i)^2 / E_i$。自由度 23、顯著水準 0.05 的臨界值是 35.17：

```
$ gcc -O2 -Wall -Wextra shuffle.c -o shuffle && ./shuffle
Durstenfeld: distinct permutations=24  chi2=19.86 (df=23, 0.05 critical=35.17)
Sattolo bug: distinct permutations= 6  chi2=7200026.13 (df=23, 0.05 critical=35.17)
```

正確版 $\chi^2 = 19.86 < 35.17$，無法拒絕「均勻」。第二行是把 `rand_below(i + 1)` 誤寫成 `rand_below(i)`（$j \in [0, i)$）：元素永遠不會留在原位，只產生 $(n-1)! = 6$ 種循環排列（cyclic permutation）。這其實是 Sandra Sattolo 1986 年發表的演算法，有它的用途（產生單一循環），但**不是**均勻洗牌。

一個實驗上的陷阱：我們第一次寫這個測試時，忘了每次都從 `0 1 2 3` 重新開始，而是對同一條串列連續洗牌。結果 Sattolo 版也出現了全部 24 種排列——因為多個循環排列的合成可以是任何排列。**測試要測的是「從固定初始狀態出發的分布」**，否則連明顯錯誤的演算法都會通過。

### modulo bias 與拒絕取樣

演算法均勻的前提是 `rand_below(n)` 均勻。`rand() % n` 不是：產生器輸出 $M$ 種值，$M$ 不是 $n$ 的倍數時，前 $M \bmod n$ 個餘數各多出一個來源。C 標準只要求 `RAND_MAX` 至少 32767（Microsoft CRT 就是這個值）。模擬一個 15 位元的產生器取 `% 20000`：

```
rand15 % 20000: P(v<12768) per value=6.1e-05  P(v>=12768) per value=3.05e-05  ratio=1.999
```

$32768 \bmod 20000 = 12768$，所以 0..12767 每個值有二個來源（$r$ 與 $r + 20000$），其餘只有一個，機率恰為二倍。glibc 的 `RAND_MAX` 是 $2^{31} - 1$，$n$ 很小時偏差微小，但 $n$ 接近 $2^{31}$ 時同樣嚴重。

拒絕取樣（rejection sampling）可以完全消除偏差：

```c
/* 均勻分布於 [0, n)，n > 0 */
static uint32_t rand_below(uint32_t n)
{
    uint32_t threshold = (uint32_t) -n % n; /* 2^32 mod n */
    for (;;) {
        uint32_t r = prng_u32();
        if (r >= threshold)
            return r % n;
    }
}
```

- `(uint32_t) -n` 等於 $2^{32} - n$：`int` 不比 32 位元寬時，`-n` 以無號數計算、取模 $2^{32}$（C11 6.2.5 §9）；`int` 較寬時 `n` 被提升為有號數、`-n` 為負，轉回 `uint32_t` 由 C11 6.3.1.3 §2 處理，結果相同。少了轉型，後者的平台上 `-n % n` 是有號運算，結果為 0，門檻形同虛設。
- $(2^{32} - n) \bmod n = 2^{32} \bmod n$。丟掉小於它的輸出後，剩下的值的個數恰為 $n$ 的倍數，每個餘數的來源數相同。
- 被丟棄的機率小於 $n / 2^{32}$，迴圈期望次數恆小於 2。

OpenBSD 的 `arc4random_uniform()` 用同樣的門檻；Linux 核心 v6.2 起的 `get_random_u32_below()` 採用 Daniel Lemire 的乘法法，以乘法取代大多數除法。

範例裡的 `prng_u32` 是 splitmix64，**可重現但不具密碼學安全性**，適合測試與模擬。需要不可預測性（例如防止攻擊者預測記憶體配置）時必須用 CSPRNG，例如 `getrandom(2)` 或 glibc 2.36 起的 `arc4random_uniform()`。還有一個常被忽略的限制：52 張牌有 $52! \approx 2^{225.6}$ 種排列，用 `srand(time(NULL))` 播種的產生器最多 $2^{32}$ 種起點，絕大多數排列永遠不會出現。

### 核心裡的洗牌

- `mm/slab_common.c` 在 `CONFIG_SLAB_FREELIST_RANDOM` 下以 `freelist_randomize()` 打亂 slab 的 free list 次序，註解直接寫 "Fisher-Yates shuffle"，隨機數取自 `get_random_u32_below(i + 1)`——目的是讓攻擊者難以預測相鄰配置的物件位址，是 heap exploitation 的緩解措施。
- `mm/shuffle.c`（v5.2）打亂 page allocator 初始 free list，註解坦承不修正 modulo bias："To keep the implementation simple it does not attempt to correct for sources of bias in the distribution, like modulo bias or pseudo-random number generator bias."——用途不涉及安全，簡單優先。
- `scripts/gcc-plugins/randomize_layout_plugin.c`（randstruct）在編譯期打亂結構體成員順序，用的是以建置種子初始化的 PRNG，**刻意可重現**：同一份核心的每個編譯單元必須看到相同的結構體配置。

## 對比與取捨

| 方案 | 優點 | 缺點 | 適用情境 |
|---|---|---|---|
| 侵入式 `list_head` | 一次配置、$O(1)$ 移除、可同時在多條串列 | 型別要預先嵌入成員；依賴 `container_of` | 核心、效能敏感的 C 程式 |
| `void *data`（GList） | 不改資料型別、可放任意多條串列 | 二次配置、多一次間接、從資料找節點要搜尋 | 應用程式、資料型別不歸你管 |
| BSD `<sys/queue.h>` 巨集 | 型別安全、侵入式 | 每種型別生成一份程式碼、巨集難除錯 | BSD 系統、偏好型別安全 |
| `list_sort`（2:1） | 不配置、不遞迴、最差比較次數最佳 | 平均略多於 top-down；程式較難懂 | 核心、不能配置記憶體的情境 |
| top-down merge sort | 平均比較次數最少 | 要先知道長度或找中點 | 長度已知（`std::list`） |
| natural merge / Timsort | 有序輸入極快 | 要記錄 run 長度；隨機輸入多付比較 | 輸入常部分有序 |
| Durstenfeld（陣列輔助） | $O(n)$ | $O(n)$ 記憶體、配置可能失敗 | 一般情境 |
| Fisher-Yates 原始（走訪） | 零額外記憶體 | $O(n^2)$ | 小串列或不能配置 |

## 踩雷集錦

1. **`container_of` 減法沒轉 `char *`**：位移會乘上 `sizeof(*ptr)`。gcc 不會警告，結果是安靜的錯誤位址。
2. **在 `list_for_each_entry` 裡刪除目前節點**：遞增時讀已釋放節點的 `next`，在 glibc 上 tcache 會蓋掉它而直接 segfault；換個配置器可能「看起來正常」，更難抓。要刪就用 `_safe`。
3. **以為 `_safe` 什麼都能刪**：它只保護「目前節點」。若迴圈中刪掉的是 `safe` 指向的下一個節點，照樣 UAF。
4. **`list_del` 後拿節點當空串列用**：`list_del` 不重設節點，`list_empty(node)` 的結果無意義；要用 `list_del_init`。
5. **`list_splice` 後繼續用來源頭**：來源頭仍指著已搬走的節點，要 `INIT_LIST_HEAD` 或改用 `list_splice_init`。
6. **比較函式寫成 `return a->key - b->key`**：鍵值是 `int` 時相減可能有號溢位，是未定義行為（C11 6.5 §5）。寫成 `(a > b) - (a < b)`；`list_sort` 的 `cmp` 甚至只要回傳 `a > b`。
7. **洗牌用 `rand() % n`、`j` 範圍寫成 `[0, i)`、每次洗牌都 `srand(time(NULL))`**：分別造成 modulo bias、Sattolo（只有循環排列）、同一秒內結果相同。

## 進階：再往深一層

- **讀 `lib/list_sort.c` 的原始註解**：Spelvin 的註解本身就是一篇小論文，包括為什麼 `merge_final` 要合併時重建 `prev`、以及 cache 行為的分析（"it will avoid cache thrashing as long as 3*2^k elements can fit into the cache"）。
- **讀 `lib/list_debug.c`**：看 `__list_add_valid_or_report` 檢查哪些不變量，再對照 [Ch 17](./17-malloc-internals.md) 的 unlink attack，思考「串列完整性檢查」擋住了哪一類漏洞利用。
- **hlist 與雜湊表**：`include/linux/hashtable.h` 的 `DEFINE_HASHTABLE` 宣告 `struct hlist_head` 陣列。試著解釋為什麼 bucket 頭只用一個指標（頭的數量遠多於節點時，省一半記憶體），以及 `pprev` 為什麼是 `struct hlist_node **` 而不是 `struct hlist_node *`。
- **RCU 串列**：`include/linux/rculist.h` 的 `list_add_rcu` 用 `rcu_assign_pointer` 發布節點，讀端用 `list_for_each_entry_rcu` 不持 lock 走訪——它是 [Ch 20](./20-lock-free-linked-list.md) 記憶體回收問題的核心解法之一。
- **面試常見追問**：「`container_of` 裡的 `((type *)0)->member` 不是解參考空指標嗎？」——它只出現在 `typeof` 與 `offsetof` 的語境裡，不會被求值。

## 動手練習

1. 把本章的 `list.h` 子集加上 `list_cut_position` 與 `list_splice_init`，寫測試覆蓋空串列、單一節點、切在頭、切在尾四種情況，用 ASan 跑。
2. 打開 `LIST_POISONING`（`-DLIST_POISONING`），重跑 `foreach_uaf.c` 的錯誤版但**不** `free`，觀察沿著 poison 指標走訪時的崩潰位址是 `0x100` 還是 `0x122`，解釋為什麼。
3. 在 `list_sort` 的追蹤輸出中加印每輪 pending 中各子串列的大小，驗證本章的狀態表，並延伸到 `count = 15`。
4. 用 `cmpcount` 掃 $n = 1025..2048$，畫出 `list_sort` 與 fully eager 的比較次數差，找出差距最大與最小的 $n$，用 2:1 規則解釋。
5. 把 `rand_below` 改成 `prng_u32() % n`，對 $n = 3 \cdot 2^{30}$ 統計前 $2^{30}$ 個餘數與其餘的出現頻率比，預期是多少？

## 本章重點整理

- 侵入式串列把節點放進資料，換來一次配置、$O(1)$ 移除、多重歸屬；代價是型別要預先嵌入成員。
- `container_of` = 成員位址（轉 `char *`）減 `offsetof`；GNU 版以 `typeof` 做型別檢查；「從成員退回外層物件」是 ISO C 沒明說但業界一致仰賴的行為，走訪巨集的終止條件則是明確的 UB。
- 在一般版 `list_for_each_entry` 裡刪除節點是 UAF；`_safe` 只保護目前節點。
- `list_sort` 借用 `prev` 串 pending、用 `count` 的位元決定合併時機，2:1 延後合併讓最差比較次數達到二路合併排序的下限，而且不配置、不遞迴。
- 均勻洗牌需要均勻的 `rand_below`：`% n` 有 modulo bias，拒絕取樣可消除；PRNG 與 CSPRNG 用途不同。

## 自我檢核

- [ ] 不看書寫出 `container_of` 的二個版本，並解釋為什麼一定要轉 `char *`、`typeof` 版多做了什麼檢查。
- [ ] 如果有人說「`list_for_each_entry` 完全符合 ISO C」，你能指出哪一步不是，以及為什麼核心還是這樣寫嗎？
- [ ] 用自己的話說明 `list_sort` 為什麼要把 pending 串在 `prev` 上，而不是用 Ch 18 的 64 格陣列。
- [ ] 手算 `count` 從 0 到 8 時 pending 的變化，說出哪幾輪不合併、為什麼。
- [ ] 解釋為什麼 $n = 1536$ 時 `list_sort` 的比較次數比 top-down 多，而 $n = 1028$ 時幾乎一樣。
- [ ] 說明 `(uint32_t) -n % n` 的數學意義，以及少了 `(uint32_t)` 會在什麼平台上出錯。
- [ ] 說出一個你**不會**用侵入式串列的情境。

## 延伸閱讀

### 論文

- **[Queue-mergesort](https://doi.org/10.1016/0020-0190(93)90088-Q)** — Mordecai J. Golin, Robert Sedgewick，Information Processing Letters 48（1993）
  - **核心貢獻**：證明線性合併的 merge sort 中，最差比較次數最少若且唯若合併樹的葉節點落在相鄰二層，並提出 queue-mergesort。
  - **讀哪裡**：最差情況的定理與證明（篇幅短）。
  - **前提**：merge sort、合併樹與外部路徑長度的概念。
  - **和本章的關聯**：`list_sort` 最差比較次數最佳的理論依據。
- **[Algorithm 235: Random permutation](https://doi.org/10.1145/364520.364540)** — Richard Durstenfeld，CACM 7(7)（1964）
  - **核心貢獻**：Fisher-Yates 的 $O(n)$ 就地版本，只有幾行 ALGOL。
  - **讀哪裡**：全文一頁。
  - **和本章的關聯**：`list_shuffle` 的演算法出處。

### 部落格 / 技術文章

- **[Linked Lists in Linux（核心文件）](https://docs.kernel.org/core-api/list.html)** — Linux kernel documentation
  - **這篇說什麼**：官方的 `list_head` 教學，從嵌入、走訪到 `_safe` 版本，附圖。
  - **讀哪裡**：全文；特別是 "Traversing whilst removing nodes" 一節。
  - **為什麼值得讀**：權威、與現行核心同步。
- **[Linux kernel design patterns - part 2](https://lwn.net/Articles/336255/)** — Neil Brown（LWN，2009）
  - **這篇說什麼**：從設計模式角度解釋 `list_head`、hlist 與 embedded anchor 的取捨。
  - **讀哪裡**：Linked lists 與 hlist 段落。
  - **前提**：本章的 `container_of`。
- **[Fast Random Integer Generation in an Interval](https://arxiv.org/abs/1805.10941)** — Daniel Lemire（ACM TOMACS，2019）
  - **這篇說什麼**：以乘法取代多數除法的無偏差 `rand_below`，並與 OpenBSD 的門檻法比較效能。
  - **讀哪裡**：Section 3–4（演算法與正確性），benchmark 可略讀。
  - **和本章的關聯**：Linux `get_random_u32_below()` 的方法。
- **[Linux 核心原始程式碼巨集：container_of](https://hackmd.io/@sysprog/linux-macro-containerof)** — jserv（sysprog）
  - **這篇說什麼**：`container_of` 的完整推導、`typeof` 型別檢查與 `const` 問題。
  - **讀哪裡**：全文。

### 官方文件 / Spec / 原始碼

- **[`lib/list_sort.c`](https://github.com/torvalds/linux/blob/master/lib/list_sort.c)** 與 **[`include/linux/list.h`](https://github.com/torvalds/linux/blob/master/include/linux/list.h)**
  - **讀哪裡**：`list_sort.c` 開頭的長註解與 `merge_final`；`list.h` 的 `__list_add`、`__list_del_entry`、`list_for_each_entry_safe`。
  - **注意**：`list.h` 有大量 RCU 與 debug 相關的程式碼，第一次讀先跳過。
- **[sysprog21/linux-list](https://github.com/sysprog21/linux-list)**
  - **讀哪裡**：`include/list.h` 與 `examples/`（quick-sort、insert-sort 等）。
  - **和本章的關聯**：本章使用者空間子集的來源，可直接 `make` 跑範例。
- **[N1570](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf)**：6.3.2.3 §7（轉字元指標）、6.5.6 §7–8（指標加減的範圍）、6.7.2.1 §15（指向第一個成員）、7.19（`offsetof`）、6.2.5 §9（無號運算取模）。

### 書籍

- **《Linux Kernel Development》（3rd ed.）** — Robert Love（Addison-Wesley，2010）
  - **這本書的定位**：把 `list_head` 放回整個核心架構中看。
  - **讀哪幾章**：第 6 章 Kernel Data Structures（串列、佇列、map、紅黑樹）。書以 2.6.34 為基準，`list_head` 的 API 至今大致相同，但 debug／hardening 機制是後來加的。

這兩章的串列都假設同一時間只有一個執行緒在改它。一旦多個執行緒同時插入、刪除，`list_add` 的四行指定之間隨時可能被插隊。下一章看如何不用 lock，只靠一道原子的 compare-and-swap 維持串列的正確性，以及為什麼「刪除」比「插入」難得多。

→ [Ch 20 non-blocking 鏈結串列：CAS 與 C11 atomics](./20-lock-free-linked-list.md)
