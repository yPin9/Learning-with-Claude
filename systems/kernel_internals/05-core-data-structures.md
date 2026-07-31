# Ch 5 — Kernel 核心資料結構：list_head/rbtree/xarray

> **目標**：讀懂 kernel 裡到處都是的三個共用資料結構——侵入式雙向鏈結串列 `list_head`、紅黑樹 `rb_node`、以及取代 radix tree 的 `xarray`。搞懂它們（尤其是 `container_of` 這把鑰匙），你才有辦法讀任何一個子系統的源碼，因為 task_struct、VMA、page cache、timer 全都掛在這些結構上。

## 為什麼需要這個？

你打開 `kernel/sched/core.c`、`mm/vma.c`、`fs/inode.c` 任何一個檔案，五分鐘內一定會撞到 `list_add`、`rb_insert_color`、`list_for_each_entry`、`container_of` 這些東西。它們不是某個子系統的內部細節，是**整個 kernel 共用的地基**。不先把地基讀懂，你讀排程器會卡在「`&rq->cfs_tasks` 這個 list 到底裝了什麼」，讀 mm 會卡在「VMA 樹怎麼查」。

而 kernel 的資料結構，和你在 user space 寫的**不一樣**。在 user space 你寫一個鏈結串列大概是這樣：

```c
struct node {
    struct node *next;
    int data;              // 資料塞在節點裡
};
```

節點「擁有」資料。要放不同型別的資料就得為每種型別各寫一份 list，或者用 `void *data` 外加一次 `malloc`。kernel 反過來做：**節點嵌在你的資料結構裡**，而不是資料塞在節點裡。這個反轉（intrusive / 侵入式）是本章的靈魂，它同時解釋了為什麼需要 `container_of` 這個看起來很怪的巨集。

先把三個結構的分工講清楚，免得混淆：

- **`list_head`**：雙向鏈結串列。用於「一堆同類物件排成一列、常從頭尾增刪、常整串遍歷」——例如一個 CPU runqueue 上所有 task、一個目錄下所有 dentry。
- **`rb_node`（紅黑樹）**：平衡二元搜尋樹。用於「要按 key 排序、要 O(log n) 找最小/查範圍」——例如 CFS 按 vruntime 排 task、timer 按到期時間排。
- **`xarray`**：以整數為索引的稀疏陣列（底層是壓縮基數樹）。用於「用整數 index 對到指標」——最典型就是 page cache 把「檔案第幾頁」對到 `struct page`。

## 先建立直覺：侵入式串列

侵入式（intrusive）的核心巧思一句話：**串列的「連接件」是你資料結構的一個成員欄位，串列本身不知道也不在乎你的資料長什麼樣。**

`struct list_head` 定義在 `include/linux/list.h`（實際結構在 `include/linux/types.h`），簡單到不可思議：

```c
struct list_head {
    struct list_head *next, *prev;
};
```

裡面**沒有任何 data 指標**。它只有指向前後的兩根指標。你想把某個物件掛上串列，就在那個物件裡塞一個 `list_head` 成員：

```c
struct task_struct {
    ...
    struct list_head    tasks;       // 掛到「所有 task」的全域串列
    ...
    struct list_head    sibling;     // 同時掛到「我父行程的子行程」串列
    ...
};
```

注意 `task_struct` 裡有**不只一個** `list_head`。這是侵入式的第一個大好處：**同一個物件可以同時掛在好幾條串列上**，各用一個 `list_head` 成員。user space 那種「data 塞在 node 裡」的做法要做到這件事，得把同一份 data 複製或多包一層。

畫成圖，一條有頭的雙向環狀串列長這樣（kernel 的 list 都是**環狀**的，head 的 prev 指向最後一個節點）：

```
     ┌──────────────────────────────────────────────────┐
     │                                                    │
     ▼                                                    │
  ┌──────┐      ┌───────────────┐      ┌───────────────┐  │
  │ HEAD │─────►│ task A         │─────►│ task B         │─┘
  │(list_│ next │ ...            │ next │ ...            │ next
  │ head)│◄─────│ .tasks(list_  │◄─────│ .tasks(list_  │
  └──────┘ prev │  head 成員)   │ prev │  head 成員)   │
     ▲          └───────────────┘      └───────────────┘
     │  這裡的 next/prev 指的是「另一個 list_head」，
     │  不是「另一個 task_struct」——記住這點，container_of 才講得通
```

關鍵認知：串列上的 `next`/`prev` 指到的是**下一個節點的 `list_head` 成員**，不是下一個 `task_struct` 的開頭。所以當你手上拿著一根 `list_head *`，你指的是某個 task 中間某個欄位的位址，不是那個 task 的開頭。要拿回整個 task，你需要「從成員位址回推外層結構起始位址」——這就是 `container_of` 要解決的問題。

## container_of：讀 kernel code 的鑰匙

這個巨集在 `include/linux/container_of.h`。它是整個 kernel 最重要的一個巨集，值得逐行拆。v6.12 的定義是：

```c
#define container_of(ptr, type, member) ({                              \
    void *__mptr = (void *)(ptr);                                       \
    static_assert(__same_type(*(ptr), ((type *)0)->member) ||           \
                  __same_type(*(ptr), void),                            \
                  "pointer type mismatch in container_of()");           \
    ((type *)(__mptr - offsetof(type, member))); })
```

**要解決的問題**，用一句話講：你手上有 `ptr`，它指向 `type` 結構裡某個叫 `member` 的成員；你想拿到整個 `type` 結構的起始位址。

拆開來看：

**1. `offsetof(type, member)` 是核心。** 它算出 `member` 在 `type` 裡的位元組偏移量。定義（`include/linux/stddef.h`，最終落到 compiler builtin）概念上等同：

```c
#define offsetof(TYPE, MEMBER)  ((size_t)&((TYPE *)0)->MEMBER)
```

它的伎倆是：假裝有一個 `type` 物件放在位址 0，那麼取它 `member` 成員的位址，得到的數值就正好是「member 距離結構開頭幾個位元組」。因為起點是 0，成員位址 = 偏移量。這裡不會真的存取記憶體（只取位址不解參考），所以拿 NULL 當基底是安全的。現代 compiler 直接提供 `__builtin_offsetof`，但原理就是這個。

**2. `__mptr - offsetof(...)` 做回推。** 你手上的 `ptr` 指向 member，member 在結構裡的偏移是 `offsetof`。那麼「結構起點 = member 位址 − 偏移量」。這就是那行 `__mptr - offsetof(type, member)`。用圖說：

```
   結構起點                    member 位址(=你手上的 ptr)
      │                              │
      ▼                              ▼
      ┌──────────┬──────────┬────────────────┬─────────┐
      │ 欄位 1   │ 欄位 2   │ member(list_head)│ 欄位 4  │
      └──────────┴──────────┴────────────────┴─────────┘
      │◄──────── offsetof(type, member) ─────►│
      │                                       │
      └── ptr − offsetof = 這裡（結構起點）───┘
```

**3. `void *__mptr = (void *)(ptr);`** 先把 `ptr` 轉成 `void *` 存起來。為什麼要先存進一個變數？兩個原因：一是避免 `ptr` 是個有副作用的運算式時被求值兩次（巨集衛生）；二是轉成 `void *` 後做指標減法是以「位元組」為單位（`void *` 算術是 GCC 擴充，1 單位 = 1 byte），偏移量才對得上。

**4. `static_assert(__same_type(...))`** 是型別安全檢查（`include/linux/build_bug.h`、`compiler.h`）。它在編譯期確認：你傳進來的 `ptr` 的型別，真的和 `type` 裡那個 `member` 的型別一致。如果你不小心把指向 `sibling` 的指標，配上 `member=tasks` 去 `container_of`，這個 assert 會在編譯期擋下來（除非 `ptr` 是 `void *`，那就放行）。這是為什麼 kernel 敢大量用它——型別打錯編不過，不會 runtime 才炸。

**5. 最外層 `({ ... })`** 是 GCC 的 statement expression（語句運算式），讓一整段有區域變數的程式碼能當成一個「值」用在等號右邊。整個巨集求值的結果就是最後那行 `((type *)(...))`。

把這五點串起來：`container_of(ptr, type, member)` = 「拿著指向某結構內某成員的指標，安全地（編譯期查型別）換算回整個結構的指標」。**kernel 裡幾乎每個從 list_head / rb_node / hlist_node 回推物件的地方，底層都是這個巨集。** 你在 gdb 裡看到一根 `list_head *`，想知道它屬於哪個 task，心裡做的就是 `container_of`。

實務上你很少直接寫 `container_of`，而是用它包出來的便利巨集，最常見的是 `list_entry`（`include/linux/list.h`）：

```c
#define list_entry(ptr, type, member)  container_of(ptr, type, member)
```

`list_entry` 就是 `container_of` 換個名字，語意是「這個 list 節點屬於哪個外層物件」。

## 遍歷：list_for_each_entry

有了 `list_entry`，遍歷串列的標準寫法是 `list_for_each_entry`（`include/linux/list.h`）。它把「走 next 指標」+「每步 container_of 回推物件」包成一個 for 迴圈：

```c
struct task_struct *task;

list_for_each_entry(task, &some_list_head, tasks) {
    // 這裡 task 已經是回推好的 struct task_struct *，直接用
    pr_info("pid=%d\n", task->pid);
}
```

它展開後概念上是：

```c
for (task = list_entry((head)->next, typeof(*task), tasks);   // 第一個
     &task->tasks != (head);                                   // 還沒繞回 head?
     task = list_entry(task->tasks.next, typeof(*task), tasks))// 下一個
```

三個關鍵：起點是 `head->next`（head 本身是哨兵、不裝資料，跳過它）；終止條件是「當前節點的 list_head 成員位址等於 head」（環狀，繞一圈回到 head 就停）；每步用 `list_entry` 把 `list_head *` 換算回 `task *`。

> **重要陷阱**：`list_for_each_entry` 在迴圈體裡**不能刪除當前節點**——刪了之後 `task->tasks.next` 就失效，下一步會存取已釋放的記憶體。要邊走邊刪，得用 `list_for_each_entry_safe`，它多用一個暫存變數先記住下一個節點。這是 kernel list 最常見的 use-after-free 來源之一，Ch 27（RCU）會講另一種無鎖遍歷的解法。

## 為什麼選侵入式？對比 user space 的做法

把侵入式的好處集中講清楚，這是理解 kernel 資料結構哲學的核心：

| 面向 | user space「data 塞在 node」 | kernel 侵入式「node 嵌在 data」 |
|---|---|---|
| 記憶體配置 | 掛上串列要多一次 `malloc(node)` | **零額外配置**——list_head 是物件的一部分，物件在了節點就在了 |
| 一物件掛多串列 | 要複製或多包一層 | **同物件放多個 list_head 成員即可**（task 同時在 all-tasks、children、runqueue 上）|
| 失敗處理 | `list_add` 可能因 malloc 失敗 | `list_add` **不會失敗**（不配置記憶體），簡化錯誤路徑 |
| 型別泛用 | 用 `void *data` 犧牲型別安全 | list 程式碼完全型別無關，`container_of` 編譯期查型別 |
| cache 局部性 | node 和 data 可能在不同 cache line | node 就在 data 裡，走到節點時資料已在附近 |

「`list_add` 不會失敗」這點在 kernel 裡價值極高。kernel 的錯誤處理路徑（goto 清理鏈）已經夠複雜，如果連「把物件掛上串列」都可能 OOM 失敗，每個掛載點都要多一條清理路徑。侵入式讓這件事變成純指標操作，永不失敗。代價是：物件的生命週期和它的 list_head 綁死，你不能「先建 list 再決定放什麼」，物件本身得先存在。對 kernel 來說這個取捨幾乎永遠划算。

## 紅黑樹：rb_node

當你需要的不是「排成一列」而是「按 key 排序、快速找最小值或查範圍」，list 的 O(n) 查找就不夠了，這時 kernel 用紅黑樹。定義在 `include/linux/rbtree.h`：

```c
struct rb_node {
    unsigned long  __rb_parent_color;   // parent 指標 + 顏色 塞在一起
    struct rb_node *rb_right;
    struct rb_node *rb_left;
};

struct rb_root {
    struct rb_node *rb_node;             // 樹根
};
```

它一樣是**侵入式**的——`rb_node` 嵌在你的物件裡，回推物件一樣用 `container_of`（習慣上寫成 `rb_entry`，就是 `container_of` 的別名）。`__rb_parent_color` 是個小把戲：因為 `rb_node` 對齊到至少 4 bytes，parent 指標最低位一定是 0，於是拿最低位存「紅/黑」顏色，省一個欄位。

**誰用紅黑樹**：

- **CFS 排程器**（Ch 12）：把 runqueue 上的 task 按 `vruntime` 排進紅黑樹，`pick_next_task` 取最左節點（最小 vruntime）就是下一個要跑的。EEVDF（Ch 13，6.6 起取代 CFS）也用紅黑樹，換成按 virtual deadline 排。
- **timer / hrtimer**（Ch 32）：hrtimer 按到期時間排在紅黑樹（實際是 `timerqueue`，底層 rbtree），最左節點是最快到期的。
- **VMA（歷史）**：6.1 之前 `mm_struct` 用紅黑樹管理 VMA，6.1 起改用 maple tree（下面談），這是 kernel 資料結構演進的一個經典案例。
- ext4 的 extent、I/O 排程器等等處處可見。

**kernel 怎麼用它——重點在這裡**：kernel 的 rbtree **不幫你做搜尋和插入**。它只提供「重新平衡」的機制（`rb_insert_color`、`rb_erase`），**比較邏輯要你自己寫**。原因是 rbtree 完全不知道你的 key 是什麼型別、怎麼比大小。標準插入寫法長這樣（`Documentation/core-api/rbtree.rst` 的範式）：

```c
static bool my_insert(struct rb_root *root, struct my_node *data)
{
    struct rb_node **new = &root->rb_node, *parent = NULL;

    // 1. 自己寫的比較迴圈：從樹根往下走到該插入的葉子位置
    while (*new) {
        struct my_node *this = rb_entry(*new, struct my_node, node);
        parent = *new;
        if (data->key < this->key)
            new = &(*new)->rb_left;      // 往左
        else if (data->key > this->key)
            new = &(*new)->rb_right;     // 往右
        else
            return false;                // key 重複，不插
    }

    // 2. 把新節點接到找到的位置
    rb_link_node(&data->node, parent, new);
    // 3. 交給 kernel 做紅黑樹重平衡（旋轉、變色）
    rb_insert_color(&data->node, root);
    return true;
}
```

看清楚分工：**你負責走樹找位置（步驟 1，你的 key 你最懂）+ 掛上去（步驟 2）；kernel 負責維持紅黑樹性質（步驟 3 的旋轉變色）。** 這個設計把「型別相關的比較」和「型別無關的平衡演算法」乾淨切開，和 list 讓 `container_of` 處理型別是同一種哲學。

本課不推導紅黑樹的平衡演算法（那是演算法課的事，`rbtree.rst` 也直說「請去讀 CLRS」）。你要記住的是：**在 kernel 裡看到 rbtree，先找它的插入函式，比較迴圈會告訴你這棵樹是按什麼 key 排的**——這比背五種旋轉情況有用得多。

## xarray：取代 radix tree 的 index→pointer 映射

第三個結構解決另一類問題：**用一個整數 index 對到一個指標**，而且 index 可能很稀疏（0、5、9999 都有值，中間大片是空的）。定義在 `include/linux/xarray.h`。

歷史脈絡：以前 kernel 用 **radix tree**（`lib/radix-tree.c`）做這件事，但它的 API 難用、鎖要呼叫者自己管、還有一堆坑。4.20 起 Matthew Wilcox 用 xarray 把 radix tree 包成一個乾淨的門面（底層還是那棵壓縮基數樹），內建自己的 spinlock，API 直觀很多。**新程式碼一律用 xarray，radix tree 被視為 legacy。**

最典型的使用者是 **page cache**（Ch 21）。每個檔案（`struct address_space`）有一個 `struct xarray i_pages`，把「檔案的第幾頁（index）」對到「那頁的 `struct folio`/`page`」。你 `read()` 一個檔案時，kernel 先拿 offset 算出頁 index，去這個 xarray 查有沒有快取，有就直接回、沒有才去 disk 讀——這是 page cache 命中的核心查表動作。

基本 API 極簡：

```c
struct xarray things;
xa_init(&things);                      // 初始化

// 存：把 index 42 對到 obj 指標。GFP_KERNEL 是配置內部節點時用的 flag(Ch 6)
xa_store(&things, 42, obj, GFP_KERNEL);

// 取：拿 index 42 的指標，沒有就回 NULL
void *p = xa_load(&things, 42);

// 刪：等同 store NULL
xa_erase(&things, 42);
```

`xa_store`/`xa_load`/`xa_erase` 內部自己上鎖（xarray 內建 spinlock），所以簡單情境你不用管同步。要在一次操作裡做複雜事（例如「查到才改」）則用 `xa_lock`/`xa_unlock` 手動包住，配 `__xa_store` 這類「已上鎖」版本。xarray 還支援對 entry 打 tag（例如 page cache 用 tag 標記「髒頁」「正在回寫的頁」），這在 Ch 21 writeback 會遇到。

為什麼不用 hash table 就好？因為 xarray 保持 **index 有序**，能高效做「找出所有 index 在 [a, b] 範圍內的 entry」（`xa_for_each_range`）——page cache 要「回寫這個檔案第 100 到 200 頁」正是範圍操作，hash table 做不到。

## 順帶認識：hlist 與 maple tree

還有兩個你會頻繁撞到、值得先建立印象的結構：

**hlist（雜湊桶專用的串列）**，`include/linux/list.h`：

```c
struct hlist_head { struct hlist_node *first; };            // 只有一根指標!
struct hlist_node { struct hlist_node *next, **pprev; };
```

`hlist` 和 `list_head` 的差別在 **head 只有一根指標**（`first`），不是 `list_head` 的兩根（next+prev）。為什麼？因為它專門給**雜湊表的桶**用：一張 hash table 有幾千幾萬個桶（每個桶一個 `hlist_head`），每個桶省一根指標，整張表就省下可觀記憶體。代價是 hlist 不是環狀、不能 O(1) 從尾端操作，但雜湊桶只需要「從桶頭插入、遍歷這個桶」，用不到雙向環狀的能力。`hlist_node` 那個怪怪的 `**pprev`（指向「指向自己的那根指標」的指標）是為了讓刪除節點時不必特判「我是不是第一個」。file descriptor table、dcache、PID hash 等大量雜湊表都用 hlist。

**maple tree**，`include/linux/maple_tree.h`：6.1 起 `mm_struct` 用它取代紅黑樹來管理 VMA（Ch 19 會正面碰到）。它是一棵為現代 CPU cache 優化的 B-tree 變種——每個節點裝多個 key（不像 rbtree 每節點一個），走訪時 cache 命中率高，還原生支援「範圍」為 key（VMA 本來就是位址區間 [start, end)，天生適合範圍樹）。你現在只要記住：**看到 `mm->mm_mt` 或 `mas_*`、`vma_iter_*` 系列函式，那是 maple tree 在管 VMA**，細節留到 Ch 19。這是繼「radix tree → xarray」之後，kernel 資料結構持續演進的又一例——沒有哪個結構是永恆的，設計是跟著硬體和需求走的。

## 動手：自己建一個 list_head 串列並用 container_of 取回物件

把本章最核心的兩件事——侵入式串列 + `container_of` 回推——寫成一個可編譯的模組。這個模組建一條裝「動物」的串列，遍歷印出，卸載時清乾淨。

```c
// animal_list.c —— 侵入式串列 + container_of 示範
#include <linux/init.h>
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/slab.h>      // kmalloc/kfree（Ch 6）
#include <linux/list.h>      // list_head 與所有 list_* 巨集

struct animal {
    int              id;
    const char      *name;
    struct list_head list;   // <── 侵入式：串列連接件嵌在物件裡
};

// 串列的頭。LIST_HEAD 巨集宣告並初始化一個空的環狀串列頭
static LIST_HEAD(zoo);

static struct animal *make_animal(int id, const char *name)
{
    struct animal *a = kmalloc(sizeof(*a), GFP_KERNEL);
    if (!a)
        return NULL;
    a->id = id;
    a->name = name;
    INIT_LIST_HEAD(&a->list);          // 把自己的 list_head 初始化成單節點環
    return a;
}

static int __init zoo_init(void)
{
    struct animal *a;
    const char *names[] = { "cat", "dog", "owl" };
    int i;

    // 1. 建三隻動物，掛到 zoo 串列尾端
    for (i = 0; i < ARRAY_SIZE(names); i++) {
        a = make_animal(i + 1, names[i]);
        if (!a)
            return -ENOMEM;            // 失敗清理見「踩雷」討論，這裡從簡
        list_add_tail(&a->list, &zoo); // 把 a->list 掛到 zoo 尾巴
    }

    // 2. 遍歷：list_for_each_entry 幫我們對每個節點做好 container_of
    pr_info("zoo: walking the list\n");
    list_for_each_entry(a, &zoo, list) {
        // a 已是回推好的 struct animal *，直接用
        pr_info("  animal #%d = %s\n", a->id, a->name);
    }

    // 3. 手動示範 container_of：拿到第一個節點的 list_head，自己回推
    {
        struct list_head *first = zoo.next;      // 一根 list_head *
        struct animal *back = container_of(first, struct animal, list);
        pr_info("container_of recovered: #%d %s (list_head@%px, animal@%px)\n",
                back->id, back->name, first, back);
        // list_head 位址和 animal 起始位址相差 offsetof(struct animal, list)
    }
    return 0;
}

static void __exit zoo_exit(void)
{
    struct animal *a, *tmp;

    // 邊走邊刪 → 一定要用 _safe 版本，否則 kfree 後 a->list.next 失效
    list_for_each_entry_safe(a, tmp, &zoo, list) {
        list_del(&a->list);            // 先從串列拔掉
        pr_info("zoo: freeing %s\n", a->name);
        kfree(a);                      // 再釋放物件
    }
}

module_init(zoo_init);
module_exit(zoo_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Ch5: intrusive list + container_of demo");
```

Makefile 與 Ch 0 相同（`obj-m += animal_list.o`，`KDIR` 指向你 build 的 6.12 源碼樹）。放進 initramfs 或直接在 QEMU 裡的 rootfs：

```
/ # insmod animal_list.ko
zoo: walking the list
  animal #1 = cat
  animal #2 = dog
  animal #3 = owl
container_of recovered: #1 cat (list_head@ffff..., animal@ffff...)
/ # rmmod animal_list
zoo: freeing cat
zoo: freeing dog
zoo: freeing owl
/ # dmesg | tail
```

看那行 `container_of recovered`：`list_head@` 印的是 `zoo.next`（第一個節點的 list_head 位址），`animal@` 印的是回推出的物件位址，兩者的差就是 `offsetof(struct animal, list)`。這一步把整章的抽象變成你螢幕上兩個具體位址的減法。

> 想在 gdb 裡看得更清楚：`insmod` 後 `b zoo_init`（先 `lx-symbols` 載模組符號），停在遍歷前，`p zoo`、`p *(struct animal *)((char *)zoo.next - 16)`——手動算 offset 回推，體會 `container_of` 到底做了什麼。（16 是 `id`+`name` 在 64-bit 下的偏移，實際請以 `p &((struct animal *)0)->list` 為準。）

## 對比與取捨：三個結構各自的地盤

| 結構 | 底層 | 查找 | 最擅長的操作 | 典型使用者 |
|---|---|---|---|---|
| `list_head` | 雙向環狀鏈結 | O(n) | 頭尾增刪、整串遍歷、一物件掛多串 | runqueue task 串、dentry LRU、各種等待佇列 |
| `hlist` | 單向頭鏈結 | O(桶長) | 雜湊桶內插入/遍歷（省一根指標） | fd table、dcache、PID hash |
| `rbtree` | 紅黑樹 | O(log n) | 按 key 排序、取最小、範圍查 | CFS/EEVDF、hrtimer、ext4 extent |
| `xarray` | 壓縮基數樹 | O(log n) | 整數 index→指標、稀疏、範圍+tag | page cache、IDR、IRQ 描述符表 |
| `maple tree` | cache 優化 B-tree | O(log n) | 範圍 key（區間）、cache 友善 | VMA 樹（6.1+） |

選型的直覺：**要排成一列 → list；要按數值 key 排序取最小 → rbtree；要用整數當索引查指標 → xarray；要用位址「區間」當 key → maple tree。** 而不管哪一種，回推物件都是 `container_of` 家族（`list_entry` / `rb_entry` / `hlist_entry`）。

## 踩雷集錦

1. **「錯誤直覺：list 節點的 next 指向下一個物件」→ 正確：next 指向下一個物件的 list_head 成員。** 這是讀 kernel list 最根本的誤解。手上一根 `list_head *` 不是物件指標，要 `container_of` 才拿得到物件。gdb 裡直接 `p *(struct task_struct *)some_list_ptr` 會印出一堆垃圾，因為你把 list_head 的位址當成 task 開頭了。

2. **「錯誤直覺：`list_for_each_entry` 裡可以 kfree 當前節點」→ 正確：不行，要用 `_safe` 版。** 一般版在算下一步時會讀 `pos->member.next`，而你已經把 `pos` 釋放了，這是典型 use-after-free。KASAN（Ch 53）會抓到，但你該一開始就用 `list_for_each_entry_safe`。

3. **`container_of` 的 `member` 傳錯。** 一個物件有多個 `list_head`（如 task 的 `tasks` 和 `sibling`），你從 `sibling` 串列拿到節點卻用 `member=tasks` 回推，算出的物件位址整個偏掉。v6.12 的 `static_assert` 型別檢查能擋掉「型別不同」的錯配，但如果兩個成員剛好同型別（都是 `list_head`），它擋不住——這種 bug 只能靠你自己對齊「從哪條串列來、用哪個成員回推」。

4. **對 rbtree 期待它幫你搜尋。** kernel rbtree 沒有 `rb_search`。查找和插入的比較迴圈**都要你自己寫**（見上面 `my_insert`）。找不到現成 search 函式不是你漏看，是設計就這樣——它只管平衡，不管你的 key 怎麼比。

5. **忘了 rbtree 插入的兩段式：`rb_link_node` 之後一定要 `rb_insert_color`。** 只 `rb_link_node`（掛上去）不 `rb_insert_color`（重平衡），樹會失衡甚至違反紅黑樹性質，後續 `rb_erase` 行為未定義。這兩個是一對，缺一不可。

## 進階：再往深一層

- **`list_empty` / poison 值**：`list_del` 之後，kernel 會把被刪節點的 next/prev 設成 `LIST_POISON1`/`LIST_POISON2`（`include/linux/poison.h`，是刻意選的非法位址如 `0x100`）。如果你 double-delete 或存取已刪節點，會直接 fault 在那個明顯的 poison 位址上，比讀到隨機垃圾好 debug。看到 oops 的位址是 `0xdead...` 或 `0x100/0x122`，八成是動了已刪的 list 節點。

- **RCU 版的 list**：`list_add_rcu` / `list_for_each_entry_rcu`（Ch 27）讓「讀者不上鎖」和「寫者修改串列」並行安全。它的秘訣是修改指標時用 `rcu_assign_pointer`（含 memory barrier）保證讀者看到的節點永遠是一致的。kernel 大量熱路徑（如 dcache 查找）靠這個做到近乎零成本的讀。

- **面試常問**：「為什麼 kernel 用侵入式串列？」——標準答案就是本章那張表：零額外配置、`list_add` 不會失敗、一物件多串、型別安全靠 `container_of` 編譯期檢查。能再補一句「代價是物件生命週期和 list_head 綁死」會顯得你真的懂取捨。

- **`container_of` 的可攜性**：它依賴 `void *` 指標算術（GCC 擴充）和 statement expression，是 GNU C 而非標準 C。這也是為什麼 kernel 明確只支援 GCC/Clang（帶 GNU 擴充模式），不追求 ANSI C 可攜。你在 user space 想用這招，把 GCC 擴充的部分換成標準寫法即可（很多 C 專案自己 `#define container_of`）。

## 動手練習

1. **量 offset**：改上面的模組，用 `pr_info("offset=%zu\n", offsetof(struct animal, list))` 印出 `list` 成員的偏移，再和「`list_head` 位址 − `animal` 位址」比對，確認兩者相等。把 `list` 成員移到 struct 最前面，看 offset 變 0、`container_of` 變成什麼都不減。

2. **弄壞它**：把 `zoo_exit` 的 `list_for_each_entry_safe` 換成一般的 `list_for_each_entry`（仍在迴圈裡 `kfree`），開 KASAN config 重編 kernel，`rmmod` 看 KASAN 報 use-after-free。這是踩雷 2 的實證。

3. **加一棵 rbtree**：把 animal 額外掛進一棵按 `id` 排序的紅黑樹，自己寫 `my_insert` 的比較迴圈，然後用 `rb_first`(`include/linux/rbtree.h`) 取最小 id 印出。這是 Ch 12 CFS 取 leftmost task 的縮小版。

4. **gdb 觀察真的 kernel list**：QEMU 開機停在 shell 後，gdb `Ctrl-C` 中斷，`p init_task.tasks`，然後手動 `container_of` 回推下一個 task（`p *(struct task_struct *)((char *)init_task.tasks.next - offsetof的值)`），對照 `lx-ps` 的輸出。

## 本章重點整理

- kernel 的資料結構是**侵入式**的：連接件（`list_head`/`rb_node`）嵌在你的物件裡，而非物件塞在節點裡。好處是零額外配置、`list_add` 不會失敗、一物件可掛多條串列、型別安全。
- **`container_of(ptr, type, member)`** 是讀 kernel 的鑰匙：用 `offsetof` 算成員偏移，把「指向成員的指標」減去偏移換算回「整個結構的指標」，`static_assert` 在編譯期查型別。`list_entry`/`rb_entry`/`hlist_entry` 都是它的別名。
- rbtree（`rb_node`）給「按 key 排序取最小/查範圍」用，但**比較和搜尋要你自己寫迴圈**，kernel 只提供平衡（`rb_insert_color`/`rb_erase`）；xarray（取代 radix tree）給「整數 index→指標」用，`xa_load`/`xa_store` 且內建鎖，page cache 靠它。
- 沒有永恆的結構：radix tree→xarray、VMA 的 rbtree→maple tree，都是跟著硬體與需求演進的。看到 `mm->mm_mt` 是 maple tree，看到 hlist 是雜湊桶。

## 自我檢核

- [ ] 不看筆記，能逐行講出 `container_of` 為什麼能從成員指標回推結構指標（`offsetof` 在做什麼）
- [ ] 能解釋「侵入式 vs. user space 把 data 塞 node」至少三個具體差異，並說出侵入式的代價
- [ ] 拿到一根 `list_head *`，知道它不是物件指標，能說出怎麼變成物件指標
- [ ] 能說出 list / rbtree / xarray 各自最適合什麼查找模式，看到一段源碼能判斷它為什麼選這個結構
- [ ] 面試被問「kernel rbtree 為什麼沒有 search 函式」，你能答出「比較邏輯型別相關、要呼叫者自己寫，kernel 只負責型別無關的平衡」
- [ ] 能寫出、載入一個自建 list_head 串列的模組，並用 `container_of` 取回物件

## 延伸閱讀

### 官方文件

- **[Documentation/core-api/rbtree.rst](https://www.kernel.org/doc/html/latest/core-api/rbtree.html)**
  - **讀哪裡**：整篇。這是設計者親自寫的 rbtree 使用指南，本章 `my_insert` 的範式直接來自它
  - **能學到什麼**：為什麼 kernel rbtree 把搜尋/插入交給呼叫者、`rb_link_node`+`rb_insert_color` 兩段式的正確用法、`rb_first`/`rb_next` 遍歷

- **[Documentation/core-api/xarray.rst](https://www.kernel.org/doc/html/latest/core-api/xarray.html)**
  - **讀哪裡**：「Normal API」和「Locking」兩節
  - **能學到什麼**：`xa_load`/`xa_store`/`xa_erase` 的正確用法、內建鎖與手動鎖的分界、tag 機制（Ch 21 page cache writeback 會用到）

### 文章

- **[Trees I: Radix trees / The XArray](https://lwn.net/Articles/745073/)** — Jonathan Corbet, LWN.net
  - **為什麼讀**：xarray 進主線時 LWN 的一手解說，講清楚它為什麼要取代 radix tree、API 怎麼變乾淨。本章「radix→xarray」的脈絡出自這裡
  - **前提**：知道什麼是 index→pointer 映射即可

- **[The maple tree](https://lwn.net/Articles/845507/)** — LWN.net
  - **為什麼讀**：maple tree 取代 VMA rbtree 的動機與設計，讀完 Ch 19 之前先建立印象很有幫助
  - **讀哪裡**：前半的動機部分（為什麼 rbtree 不夠好、B-tree 變種怎麼贏在 cache）

### 書籍

- **《Linux Kernel Development, 3rd Ed.》** — Robert Love，第 6 章「Kernel Data Structures」
  - **定位**：list/rbtree/queue/map 的白話總覽，和本章互補。它講的 idr/radix 較舊，xarray/maple tree 以本章與 LWN 為準
  - **注意**：書的 kernel 版本舊，`container_of` 與侵入式哲學不變，具體 API 對 6.12 源碼

搞懂了這些共用結構，你已經有了讀任何子系統的鑰匙。下一章我們往下走一層，看這些物件本身是從哪來的——`kmalloc`、`vmalloc`、slab、以及那個到處都要傳的 `GFP_KERNEL` flag 到底控制什麼。

→ [Ch 6 記憶體配置 API：kmalloc/vmalloc/slab/GFP](./06-memory-allocation-api.md)
