# Ch 7 — buddy allocator 與 page：order、migratetype、GFP、struct page

> **目標**：理解 Linux 怎麼管理「page」這個最底層的記憶體單位——buddy allocator 怎麼切分與合併連續頁面、`struct page` 怎麼描述一塊實體記憶體、GFP flag 和 migratetype 怎麼決定一次配置落在哪裡。這是 SLUB（Ch 8）向作業系統要記憶體的唯一管道，也是 cross-cache attack（Ch 22）與 page-level spray（Ch 25）的地基。

> **環境**：Linux 6.6 LTS、x86-64，`PAGE_SIZE = 4096`（`CONFIG_TRANSPARENT_HUGEPAGE` 等機制會引入 2MB/1GB 的 huge page，但 buddy allocator 操作的基本單位仍是 4KB page，這點本章末會說明）。

## 為什麼需要這個？

Ch 6 讓你知道 kernel 虛擬位址空間裡有哪些區域，但還沒回答一個更基本的問題：**當 kernel 某處呼叫「我要一塊記憶體」的時候，這塊記憶體從哪裡來、怎麼決定給你哪一塊?**

user-space 的你可能會說「malloc 不就是這樣嗎」，但 kernel 的情況更底層。`malloc()` 本身最終也要靠 kernel 給它記憶體（`brk()`/`mmap()`），而 kernel 內部的 `kmalloc()`（SLUB，下一章主角）也不是憑空生出記憶體——SLUB 本身需要向更底層的某個東西要一整塊、一整塊連續的實體頁面，再自己切成小 object 分給呼叫者。**這個更底層的東西就是 buddy allocator。**

不理解 buddy allocator，你會在 Part 3 卡在這些問題上：

- 為什麼「cross-cache attack」要跨過 slab cache，直接打 buddy 層級？這個攻擊面到底是什麼?
- 為什麼有些漏洞技巧要「噴」（spray）出一大堆 page，而不是噴普通的 kmalloc object?
- `GFP_KERNEL` 和 `GFP_ATOMIC` 這些 flag 到底在決定什麼?它們跟 migratetype 有什麼關係?

## 先建立直覺

把實體記憶體想成一條很長的紙帶，buddy allocator 的工作是管理「怎麼把這條紙帶切成小段借出去、借完了怎麼收回來黏回去」。

它的核心想法是：**永遠用 2 的次方個連續頁面作單位**。你不會跟它要 5 個 page，只能要 1、2、4、8、16... 個。這個「2 的次方」的指數叫 **order**：

```
order 0 :  [page]                              (1 個 page,   4 KB)
order 1 :  [page][page]                         (2 個 page,   8 KB)
order 2 :  [page][page][page][page]             (4 個 page,  16 KB)
order 3 :  8 個 page,  32 KB
  ...
order 10:  1024 個 page, 4 MB   （MAX_ORDER 的典型值，依版本/config 微調）
```

為什麼限制成 2 的次方？因為這樣切分和合併都可以用一個優雅的規則處理：**任何一塊 order-N 的記憶體，都可以恰好切成兩塊 order-(N-1)**，而這兩塊彼此互為「buddy（夥伴）」——只要兩個 buddy 都變成空的，就可以合併回一塊 order-N。這正是這個分配器叫 "buddy allocator" 的原因。

```
一塊 order-2（16 KB，4 個 page）：

┌───────────────────────────────┐
│         order-2 block          │
└───────────────────────────────┘
          切成兩個 buddy
┌───────────────┬───────────────┐
│  order-1 (左)  │  order-1 (右)  │   ← 這兩塊互為 buddy
└───────────────┴───────────────┘
      再切一次（只切左邊）
┌───────┬───────┬───────────────┐
│order-0│order-0│  order-1 (右)  │
└───────┴───────┴───────────────┘
  ↑這兩個 order-0 互為 buddy
```

如果左邊那兩個 order-0 都被釋放，buddy allocator 會把它們**合併**回一塊 order-1；如果這塊 order-1 又恰好跟右邊那塊 order-1 都是空的，繼續合併回 order-2。這個「借出去切小、收回來合併」的遞迴結構，就是整章要講的機制。

## 核心概念：`struct page` 與 `free_area`

### `struct page`：每個 4KB page 的身分證

kernel 裡每一個實體 page frame，都對應一個 `struct page`（6.6 之後很多路徑逐漸遷移到更精簡的 `struct folio`，但 `struct page` 仍是底層表示，folio 概念本身建立在它之上）。這個結構存在 Ch 6 提過的 vmemmap 區，透過 `pfn_to_page()` 可以從 page frame number 算出對應的 `struct page` 位址（因為 vmemmap 也是線性映射，這個轉換和 `__va()`/`__pa()` 一樣是簡單公式）。

`struct page` 本身是一個高度 overlay 的結構（同一段記憶體依照這個 page 目前的用途，重疊存放不同欄位——它才幾十 bytes，但要同時能表示「這是一個 slab object」「這是一個 page cache page」「這是一個 buddy free page」等多種身分）。對 buddy allocator 最關鍵的幾個欄位（概念示意，實際以你當前 kernel 版本的 `include/linux/mm_types.h` 為準）：

```c
struct page {
    unsigned long flags;        /* 狀態 bit：是否 locked、是否 dirty、所屬 zone/node 等 */
    union {
        struct list_head lru;   /* 一般 page：掛在某個 LRU list */
        struct {                 /* buddy free page 的用法 */
            unsigned long private;  /* 存 order 資訊 */
        };
    };
    atomic_t _refcount;          /* 參照計數 */
    /* ... 當這個 page 被 SLUB 拿去當 slab 使用時，
           這塊記憶體會被重新解讀成 struct slab（Ch 8 細講） */
};
```

**這就是為什麼理解 `struct page` 的 overlay 特性很重要**：同一個 `struct page`，在它是「自由的 buddy page」時存一種資料，被 SLUB 拿走當 slab 用時存另一種資料（`struct slab`，6.6 把這部分拆得更清楚，獨立出一個和 `struct page` union 在一起的 `struct slab` 型別）。一個 page 生命週期中的「身分」是會變的。

### `free_area`：buddy 的帳本

每個記憶體 zone（下面會講 zone）維護一個 `free_area[MAX_ORDER + 1]` 陣列，每個元素是一個該 order 的 free list：

```c
struct free_area {
    struct list_head free_list[MIGRATE_TYPES];
    unsigned long nr_free;
};
```

注意 `free_list` 本身又依照 **migratetype**（下面講）分成多條 list。也就是說，buddy allocator 真正維護的帳本結構大概是：

```
zone
 └─ free_area[0]  (order 0, 4KB)
      ├─ free_list[MIGRATE_UNMOVABLE]    ──▶ page ──▶ page ──▶ ...
      ├─ free_list[MIGRATE_MOVABLE]      ──▶ page ──▶ page ──▶ ...
      └─ free_list[MIGRATE_RECLAIMABLE]  ──▶ page ──▶ ...
 └─ free_area[1]  (order 1, 8KB)
      ├─ free_list[MIGRATE_UNMOVABLE]    ──▶ ...
      └─ ...
 └─ ...
 └─ free_area[MAX_ORDER] (order 最大值, 通常 4MB 一塊)
```

一次 `alloc_pages(order)` 的查找邏輯，概念上是：

1. 到 `free_area[order]` 對應 migratetype 的 list 找，有就直接拿一塊走。
2. 沒有的話，往更高的 order 找（`free_area[order+1]`），找到就切一半：一半回傳，另一半（它的 buddy）放回 `free_area[order]`。
3. 持續往上找直到 `MAX_ORDER`；如果連最高 order 都沒有，就觸發 reclaim（回收 page cache）或進入 OOM 處理路徑。

這個「找不到就往上層借、借到了就切一半」的過程叫 **splitting**；釋放時的「buddy 都空就合併回上一層」叫 **coalescing**。

## 底層機制：buddy 位址計算與 zone

### 怎麼知道誰是誰的 buddy？

這是整個演算法最漂亮的部分：給定一個 page frame number（PFN）和它目前的 order，它的 buddy 的 PFN 可以純粹用 **XOR** 算出來，不需要額外存一個指標：

```c
/* 概念示意 */
unsigned long buddy_pfn = pfn ^ (1UL << order);
```

直覺上理解：一塊 order 的記憶體區塊，它的起始 PFN 一定是 `2^order` 對齊的。對齊之後，這塊區塊和它的 buddy 之間，只差在「對齊邊界那一個 bit」是 0 還是 1——左半邊那個 bit 是 0，右半邊是 1，其他 bit 完全相同。所以 XOR 那個 bit，就從左半邊的 PFN 算出右半邊的 PFN，反過來也一樣。這個設計讓 buddy 查找變成一個 O(1) 的位元運算，不需要遍歷任何結構。

### Zone：記憶體不是只有一種

實體記憶體在 kernel 眼中不是一塊均質的大陸，而是分成幾個 **zone**，buddy allocator 的 `free_area` 其實是「每個 zone 各自一份」。x86-64 常見的 zone（以你的機器實際記憶體大小與 config 決定是否存在）：

| Zone | 典型範圍 | 用途 |
|---|---|---|
| `ZONE_DMA` | 低於 16MB 的實體記憶體 | 給只能定址低位址的老舊 DMA 裝置用（現代機器很少真的需要，但 x86 相容性保留） |
| `ZONE_DMA32` | 低於 4GB | 給只支援 32-bit 定址的裝置 DMA 用 |
| `ZONE_NORMAL` | 4GB 以上（在有 `ZONE_DMA32` 的架構上） | 一般用途記憶體，絕大多數 kmalloc/page cache 配置都落在這裡 |
| `ZONE_MOVABLE` | 視 config 與開機參數 | 給可以被遷移的記憶體（支援記憶體 hot-remove、減少碎片化），不一定每台機器都啟用 |

一次配置會先決定「要從哪個 zone 要」，這個決定主要受 GFP flag 影響（下面講）。

### GFP flags：配置請求的「附加條件」

`GFP_*`（Get Free Pages）flag 是呼叫 `alloc_pages()` / `kmalloc()` 時傳的第二個參數，描述這次配置的「條件」而不是「大小」。幾個最常見、你在後面章節會反覆看到的：

| Flag | 意義 |
|---|---|
| `GFP_KERNEL` | 最常見的一般配置；**可以睡眠**（如果記憶體不夠，允許觸發 reclaim、等待記憶體釋放），所以只能在允許 sleep 的 context 呼叫（不能在 spinlock 內、不能在 interrupt handler 裡） |
| `GFP_ATOMIC` | **不能睡眠**；用在 interrupt handler、拿著 spinlock 的時候。拿不到記憶體就直接失敗，不會等 |
| `GFP_KERNEL_ACCOUNT` | 在 `GFP_KERNEL` 基礎上加上 **memcg 記帳**：這次配置會被計入呼叫者所屬 cgroup 的記憶體用量。Ch 9 會講這個 flag 怎麼讓 kmalloc 配置被導向獨立的 `kmalloc-cg-N` cache，影響你噴物件時能不能和其他物件共用同一個 cache |
| `__GFP_ZERO` | 配置到的記憶體清零（`kzalloc()` 內部就是 `kmalloc()` 加這個 flag） |
| `__GFP_DMA` / `__GFP_DMA32` | 要求從對應的 zone 配置 |
| `__GFP_NOWARN` | 配置失敗時不要印 kernel warning（有些 fuzzing/exploit 場景故意用這個避免洗版 log） |

**`GFP_KERNEL` vs `GFP_ATOMIC` 的睡眠限制**，背後的原因是：拿不到足夠記憶體時，`GFP_KERNEL` 允許 kernel 去做比較花時間的事（把一些 page cache 寫回磁碟、觸發 direct reclaim、甚至在極端情況下等待 OOM killer 處理），這些動作可能讓目前這個 thread 被排程器換下去。如果你是在 interrupt context 或已經拿著 spinlock，被換下去是不允許的（會造成死結或更糟的狀況），所以必須用不會睡眠的 `GFP_ATOMIC`，代價是它更容易直接失敗。

### migratetype：GFP 怎麼對應到它

每次配置除了決定 zone，還會決定 **migratetype**——這塊記憶體「能不能被搬動」的分類：

| migratetype | 意義 | 典型使用者 |
|---|---|---|
| `MIGRATE_UNMOVABLE` | 配置出去之後位置固定不能動 | kernel 內部資料結構、大多數 `kmalloc()`（SLUB 的 slab 本身對 buddy 而言通常是 unmovable） |
| `MIGRATE_MOVABLE` | 可以被 kernel 搬到別的 physical frame（配合 page migration 機制） | 大多數 user-space 的匿名記憶體、page cache |
| `MIGRATE_RECLAIMABLE` | 內容可以被直接丟棄重算，不需要搬移，需要時直接回收 | 某些 slab cache（例如可重建的 dentry/inode cache）、部分檔案系統的 metadata |

分類的意義是**對抗記憶體碎片化**：如果 unmovable 和 movable 的頁面混在一起散落各處，久了之後就很難再湊出大塊連續記憶體（因為中間卡著幾個「釘住不能動」的 unmovable page）。buddy allocator 刻意把不同 migratetype 的配置導向實體記憶體裡不同的區域（`pageblock`，一塊固定大小、通常對齊到較大 order 的區域），讓 unmovable 的配置盡量集中在一起，movable 的集中在另一邊。這樣當系統需要湊出大塊連續記憶體（例如給 huge page 用）時，只要把某個區域裡所有 movable 的頁面搬走，就能清出一塊乾淨連續的空間，而不用擔心中間卡著搬不動的頁面。

`GFP_KERNEL` 這種一般配置預設落在 `MIGRATE_UNMOVABLE`；user-space 分配的匿名記憶體走 `MIGRATE_MOVABLE`。這個分類對 exploit 開發有實際影響：**如果你要噴的物件最終要落在跟某個目標物件同一個 pageblock / 同一批實體記憶體附近，migratetype 不同代表它們可能被導向不同的實體區域，影響 spray 的精確度**，這是 Ch 25（USMA 與 page-level spray）要處理的問題之一。

## 範例：配置路徑追蹤

### 範例一：最基本的 `alloc_pages()`

下面是一個觀測用 kernel module，示範怎麼直接呼叫 buddy allocator 層的 API，而不經過 SLUB：

```c
// buddy_demo.c
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/gfp.h>
#include <linux/mm.h>

static int __init buddy_demo_init(void)
{
    struct page *page;
    void *addr;
    unsigned int order = 2; /* 2^2 = 4 個 page = 16 KB */

    /* 直接向 buddy allocator 要一塊 order-2 的連續記憶體 */
    page = alloc_pages(GFP_KERNEL, order);
    if (!page) {
        pr_err("buddy_demo: alloc_pages failed\n");
        return -ENOMEM;
    }

    addr = page_address(page);
    pr_info("buddy_demo: got order-%u block, page=%px pfn=%lu virt=%px phys=%llx\n",
            order, page, page_to_pfn(page), addr,
            (unsigned long long)page_to_phys(page));

    /* 用掉它：寫一個 pattern，確認這 16KB 真的可寫 */
    memset(addr, 0x41, PAGE_SIZE << order);

    free_pages((unsigned long)addr, order);
    pr_info("buddy_demo: freed order-%u block\n", order);

    return 0;
}

static void __exit buddy_demo_exit(void)
{
    pr_info("buddy_demo: unloaded\n");
}

module_init(buddy_demo_init);
module_exit(buddy_demo_exit);
MODULE_LICENSE("GPL");
```

```makefile
# Makefile
obj-m += buddy_demo.o

KDIR ?= /lib/modules/$(shell uname -r)/build

all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules

clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean
```

> **未實測，理論預期行為**：以上 module 在本環境無法編譯 kernel module 並載入實機/QEMU 驗證。預期行為：`dmesg` 會印出一個 pfn 與 virtual address，且 `virt - page_offset_base`（Ch 6）換算應該等於 `phys`（因為 direct map 是線性映射）。驗證方式：在你自己的 QEMU（Linux 6.6 LTS，x86-64）上 `make && insmod buddy_demo.ko && dmesg | tail`，用計算機核對 `virt` 減去你在 Ch 6 練習量到的 `page_offset_base` 是否等於印出的 `phys`。

`alloc_pages()` 回傳的是一個 `struct page *`，不是虛擬位址——這是 buddy allocator 介面和你熟悉的 `malloc()` 最大的不同：它操作的是「page」這個概念,拿到手上先是一個描述符，要透過 `page_address()` 才能拿到可以直接讀寫的虛擬位址（這一步本質上就是查 direct map）。

### 範例二：order 太大，配置失敗的邊界情況

```c
// buddy_fail_demo.c（只列關鍵部分）
static int __init buddy_fail_demo_init(void)
{
    struct page *page;
    unsigned int order = 11; /* 故意超過典型 MAX_ORDER */

    page = alloc_pages(GFP_KERNEL | __GFP_NOWARN, order);
    if (!page) {
        pr_info("buddy_fail_demo: alloc_pages(order=%u) failed as expected\n", order);
        return 0;
    }

    pr_warn("buddy_fail_demo: unexpectedly succeeded?!\n");
    free_pages((unsigned long)page_address(page), order);
    return 0;
}
```

> **未實測，理論預期行為**：`MAX_ORDER` 的實際值隨 kernel 版本與架構微調（6.6 的預設值可以在 `include/linux/mmzone.h` 查到當前定義；歷史上在 10 左右徘徊，即最大一塊約 4MB），本章不假設一個絕對數字。預期行為是：order 一旦超過 `MAX_ORDER`，`alloc_pages()` 在編譯期或執行期就會失敗/告警，因為 buddy 系統本身的 `free_area` 陣列大小就是用 `MAX_ORDER` 定義的，物理上無法表示更高的 order。驗證方式：在你的 kernel 原始碼裡 `grep -rn "define MAX_ORDER" include/linux/` 或 `grep -rn "MAX_PAGE_ORDER"`（部分版本改了巨集名稱），對照你實際編譯的數字再調整上面的範例 order 值，故意設成「剛好超過」來觀察失敗訊息。

這個失敗案例要傳達的直覺：buddy allocator **不是萬能大塊記憶體來源**。它的設計目標是管理整個系統的實體記憶體分配與回收，不是給你一次要幾百 MB 連續記憶體用——那種需求要用 vmalloc（犧牲「實體連續」換取「不受 MAX_ORDER 限制」），或者 `CMA`（Contiguous Memory Allocator，給真的需要大塊連續實體記憶體的硬體裝置用，超出本書範圍）。

## order、位元組大小與常見配置的對照

| order | page 數 | 大小 | 典型用途 |
|---|---|---|---|
| 0 | 1 | 4 KB | 絕大多數單一 kmalloc slab 的底層配置（小 size class） |
| 1 | 2 | 8 KB | 部分中等 size class 的 slab |
| 2 | 4 | 16 KB | 較大 size class 的 slab，或 `kmalloc(8192, ...)` 以上但未達 `kmalloc_large` 門檔的配置 |
| 3 | 8 | 32 KB | | 
| 9 | 512 | 2 MB | 恰好對齊一個 **transparent huge page**（THP）的大小，不是巧合——THP 的設計就是建立在 buddy allocator 能給出 order-9 連續區塊的能力上 |

> 注意：`kmalloc()` 超過某個門檔（實作上有 `KMALLOC_MAX_CACHE_SIZE` 這類常數界定）的大配置，會直接繞過 SLUB cache，走 `kmalloc_large()` 路徑，本質上就是直接呼叫 `alloc_pages()` 拿一塊夠大的連續記憶體再包一層 header。這是 Ch 8 會重新提到的細節——理解它需要先懂本章的 buddy 機制。

## 底層機制：SLUB 如何向 buddy 借地

這是連接到下一章最重要的一段。SLUB **不會**自己管理實體記憶體的取得與釋放——它的每一個 slab（一批同 size 的 object 所在的那塊記憶體）本身，都是向 buddy allocator 要來的一塊 order-N 連續頁面：

```
使用者呼叫 kmalloc(64, GFP_KERNEL)
        │
        ▼
SLUB 查表：64 bytes 對應 kmalloc-64 這個 cache
        │
        ▼
kmalloc-64 目前有空的 slab（之前配置過，裡面還有空 object）？
        │
   ┌────┴────┐
  有           沒有
   │            │
   ▼            ▼
直接從這個    向 buddy allocator 要一塊新的 order-N 頁面
slab 切一個    （N 由這個 cache 的 object size 決定，
object 出去     讓一個 slab 裡能塞下足夠多個 object）
                 │
                 ▼
            把這塊頁面切成一堆同大小的 object，
            串成 freelist（Ch 8 細講這個串接方式），
            再從裡面切一個給呼叫者
```

**這就是 cross-cache attack（Ch 22）存在的理論基礎**：如果你能透過某種手法，讓某個 kmalloc cache 把它的 slab 釋放回 buddy allocator（而不是留著備用），然後讓另一個完全不同的 kmalloc cache（甚至是直接呼叫 `alloc_pages()` 的某個 subsystem）剛好借到同一塊剛釋放的實體頁面，你就能跨越「cache 隔離」把一個 cache 裡的 UAF 轉移成另一個 cache 裡的物件覆寫。這招能成立的前提，正是「所有 kmalloc cache 最終都共用同一個 buddy allocator」——它們表面上互相隔離，底層的實體記憶體來源其實是同一個池子。

## 底層機制：watermark 與 kswapd——什麼時候觸發回收

buddy allocator 不是「有要就給，沒有就失敗」這麼單純，每個 zone 還維護三道 **watermark（水位線）**，決定什麼時候要提前介入：

```
zone 的可用記憶體量（由高到低）
┌─────────────────────────────────┐
│          充裕區間                 │  高於 high watermark：一切正常
├─────────────────────────────────┤  ← high watermark
│       該開始回收了                 │  低於 high、高於 low：
│                                   │  喚醒 kswapd 背景回收，但配置請求仍直接放行
├─────────────────────────────────┤  ← low watermark
│      配置請求可能被迫參與回收         │  低於 low、高於 min：
│                                   │  GFP_KERNEL 配置可能觸發 direct reclaim
├─────────────────────────────────┤  ← min watermark
│  只留給最緊急的配置（如 GFP_ATOMIC）  │  低於 min：一般配置直接失敗
└─────────────────────────────────┘
```

- **`kswapd`**：一個常駐背景 kernel thread，當某個 zone 的可用記憶體跌破 high watermark，就會被喚醒，非同步地回收乾淨的 page cache、把 dirty page 寫回磁碟，試圖在「配置請求真正卡住」之前就把水位線拉回去。
- **direct reclaim**：如果配置發生得比 `kswapd` 回收的速度還快，記憶體跌到 low watermark 以下，`GFP_KERNEL` 這類允許睡眠的配置請求會**自己跳下去做回收工作**（這就是為什麼 `GFP_KERNEL` 配置偶爾會「卡一下」——它可能正在同步地幫系統清記憶體，不是單純等待）。
- **`GFP_ATOMIC` 的特殊待遇**：因為它不能睡眠、不能等待回收，kernel 會保留一小塊「緊急預留」只給它用，對應 min watermark 這條底線。這也是為什麁瘋狂地在 interrupt context 配置記憶體是危險的——預留量用盡之後，`GFP_ATOMIC` 的配置會直接失敗，沒有任何退路。

理解 watermark 對 exploit 開發有個實際影響：**大量 heap spray 會把系統記憶體壓力推高**，如果 spray 的量級接近讓 zone 跌破 watermark，你可能會觀察到配置變慢、甚至部分 spray 請求失敗，這不是你的程式碼有 bug，是系統在背景做 reclaim。寫穩定的 exploit（Ch 45 會細講）時，監控記憶體壓力、控制 spray 節奏，是避免這類不穩定性的常見手段。

## 對比與取捨

| 配置方式 | 虛實映射 | 粒度 | 典型用途 | Exploit 相關性 |
|---|---|---|---|---|
| `alloc_pages()`（buddy 直接配） | 線性（direct map） | 2^order 個 page | 大塊、需要實體連續的配置；SLUB 的底層來源 | cross-cache、某些 page-level spray（AF_PACKET pgv 等）的目標層級 |
| `kmalloc()`（SLUB） | 線性（direct map，因為最終也是從 buddy 要來的 page） | 任意小 size（8 bytes 起） | 絕大多數 kernel 內部小物件配置 | Part 3 幾乎全部技巧的主戰場，Ch 8 細講 |
| `vmalloc()` | 非線性（獨立頁表） | 任意大小，按 page 對齊 | 需要「虛擬連續但不要求實體連續」的大塊配置 | 較少是直接攻擊目標，但影響某些 spray 策略的位址穩定性 |

## 踩雷集錦

1. **錯誤直覺：「buddy allocator 跟 malloc 一樣，可以要任意 byte 數」**。buddy 的介面單位是「page 的 2 的次方個數」，不是 byte。想要任意 byte 數的小配置，是 SLUB 這一層在幫你做切分，buddy 本身完全不知道「64 bytes」這種概念。

2. **錯誤直覺：「合併（coalescing）一定會馬上發生」**。只有當一個 buddy 對被釋放、且它的夥伴也剛好是空的時候才會合併；如果夥伴還在使用中，這塊記憶體就一直以小 order 的狀態留在 free list 裡，等到某次它的夥伴也被釋放才有機會合併。長時間運作下的系統如果配置模式很破碎，高 order 的連續記憶體可能變得稀少——這正是 migratetype 分區與 `MIGRATE_MOVABLE` 可搬移機制存在的原因。

3. **邊界情況：order 超過 `MAX_ORDER` 不是「變慢」，是直接失敗**。很多人以為配置大小只影響速度，實際上 buddy allocator 的 `free_area` 陣列大小是編譯期固定的，超過上限的 order 根本沒有對應的 free list 可查，`alloc_pages()` 會在參數檢查階段就失敗（或觸發 `WARN_ON`，依版本而定），不會嘗試用某種慢速路徑硬湊出來。

4. **效能/安全誤解：「`GFP_ATOMIC` 比 `GFP_KERNEL` 快，所以能用就盡量用」**。`GFP_ATOMIC` 的差異不是「快」，是「不能睡眠」，代價是**更容易直接失敗**（因為它不被允許觸發比較花時間的 reclaim 路徑）。在允許睡眠的 context 濫用 `GFP_ATOMIC`，只會讓你的配置在記憶體壓力下更容易莫名其妙失敗，沒有任何速度上的好處可言——這個概念混淆在 CTF writeup 裡偶爾會看到，要小心別學錯。

5. **錯誤直覺：「`struct page` 是一直固定意義的結構，就是『描述一個 page』」**。`struct page` 的欄位依照這個 page 目前的「身分」（buddy 的空閒頁、SLUB 的 slab、page cache 的一頁、匿名記憶體的一頁……）而重新解讀同一段記憶體（union 的方式疊加）。讀 kernel 原始碼時看到 `struct page` 的某個欄位，一定要先確認「在這個 code path 下，這個 page 的身分是什麼」，不能脫離語境直接套用。

## 進階：再往深一層

- **page migration 與 compaction**：當系統需要湊出大塊連續記憶體（例如要用 THP）但目前破碎嚴重，kernel 會主動觸發 `MIGRATE_MOVABLE` 頁面的搬移（把內容複製到別處、更新對應的 page table），騰出連續空間。這個機制（`mm/compaction.c`）是 buddy allocator 配合 migratetype 分類才能運作的進階應用，值得在理解本章後去讀原始碼。
- **per-cpu pageset（pcp list）**：為了減少高頻率小配置（order 0）對全域 zone lock 的競爭，buddy allocator 在每個 CPU 上維護一份小快取（`per_cpu_pages`），優先從這裡拿/還 order-0 page，只有快取用盡或超量才去動全域的 `free_area`。這是 SLUB 的 per-cpu fast path（Ch 8 的靈魂）在更底層的對應設計，兩層都在做同一件事：**盡量讓常見路徑不用搶全域鎖**。
- **`CMA`（Contiguous Memory Allocator）**：給必須要「開機前就預留、保證連續」的實體記憶體場景（某些硬體 DMA 限制嚴格的裝置）用，建立在 buddy 之上但有獨立的保留區域管理邏輯，是想理解嵌入式/驅動安全攻擊面時的下一步。

## 動手練習

把 Ch 6 練習寫的 module 擴充：配置 order 0、1、2、4 四種大小各一塊，分別印出 `page_to_pfn()` 的值，觀察：

1. 同一次呼叫裡,四個 pfn 之間有沒有看出「2 的次方對齊」的痕跡(例如 order-2 配置到的 pfn 是不是 4 的倍數)?
2. 把這四塊都釋放後,再配置一塊 order-3,它的 pfn 和前面order-2那塊是否有關聯(有沒有剛好是前面某兩塊合併後的結果)?

> **未實測，理論預期行為**：以上行為高度依賴系統當下的記憶體狀態與其他並行配置,不保證每次重現一致結果,說明合併與配置順序本身受系統狀態影響——這正是這個練習想讓你體會的現實:buddy allocator 的行為不是「決定性」到你能精確預測每一次配置的位址,這也是為什麼真實的 heap grooming(Ch 20)需要額外手法去「清空」系統狀態,製造可預測的環境。

## 本章重點整理

- buddy allocator 用「order（2 的次方個 page）」為單位管理實體記憶體,切分(splitting)與合併(coalescing)靠 buddy PFN 的 XOR 關係達成 O(1) 查找。
- `struct page` 是每個 page 的身分證,欄位依照該 page 目前的用途被重新解讀(union)。
- GFP flag 決定配置的睡眠行為、是否記帳(memcg);migratetype 決定配置的「可搬移性」分類,目的是對抗記憶體碎片化。
- **SLUB 的每個 slab,底層都是向 buddy allocator 要來的一塊連續頁面**——這是 cross-cache attack 的理論基礎。

## 自我檢核

- [ ] 不看筆記,你能解釋「buddy」這個名字從哪裡來,以及兩個 buddy 之間的 PFN 關係是什麼運算?
- [ ] 如果有人問你「為什麼 kernel 不乾脆讓 buddy allocator 支援任意 byte 數的配置,省得多一層 SLUB?」,你會怎麼回答?
- [ ] 你能不能說出 `GFP_KERNEL` 和 `GFP_ATOMIC` 的本質差異,並舉一個「用錯會出事」的具體場景?
- [ ] migratetype 的三種分類,各自對應什麼樣的記憶體內容,為什麼要分開管理?

## 延伸閱讀

### 官方文件

- **[Documentation/admin-guide/mm/concepts.rst](https://www.kernel.org/doc/html/latest/admin-guide/mm/concepts.html)**
  - **讀哪裡**："Zones"、"Page cache" 小節
  - **和本章的關聯**：補充本章 zone 分類的官方說明,以及 page cache 如何疊在 buddy allocator 之上運作

### 部落格 / 技術文章

- **[Linux Memory Management Documentation（kernel.org）之 Page Allocation 相關章節](https://www.kernel.org/doc/gorman/html/understand/)**
  - **這篇說什麼**：Mel Gorman 早年寫的 Linux VM 內部機制長文(涵蓋較舊版本,但 buddy 核心演算法至今沿用同一套思路)
  - **讀哪裡**：Chapter 6"Physical Page Allocation"
  - **為什麼值得讀**：作者是長年的 Linux MM maintainer,這份文件是許多後續教材的原始出處;數字與函式名稱會對不上新版本,但演算法邏輯是本章內容的更深入版本

- **[pawnyable — Linux Kernel 的 heap / page 相關章節](https://pawnyable.cafe/linux-kernel/)**
  - **這篇說什麼**：用具體 CTF 題目示範 page-level spray 與 buddy 層的攻擊手法
  - **讀哪裡**：涉及 page-level primitives 的章節
  - **為什麼值得讀**：把本章的理論和 Ch 25 USMA 之類的實戰技巧串起來,提前建立「這些抽象概念最後打在哪裡」的畫面

### 書籍

- **《Understanding the Linux Kernel》（3rd ed.）** — Daniel P. Bovet & Marco Cesati（O'Reilly, 2005）
  - **這本書的定位**：涵蓋到 2.6 kernel,buddy allocator 的核心資料結構命名已經有變動(例如舊版用 `zone->free_area[order].free_list` 單一 list,沒有按 migratetype 再分類),但演算法骨架與本章一致,適合當第二個角度的解釋來源
  - **讀哪幾章**：第 8 章"Memory Management"

下一章進入整本書最關鍵的一章:SLUB 怎麼把這些從 buddy 借來的連續頁面,切成你每天在用的 `kmalloc()` object,以及這個過程裡藏著多少後面所有堆利用技巧的根。

→ [Ch 8 SLUB allocator 深挖](./08-slub-allocator.md)
