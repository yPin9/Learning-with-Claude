# Ch 8 — SLUB allocator 深挖：kmalloc-N cache、freelist、object 生命週期

> **目標**：把 SLUB 內部從頭拆開——`kmem_cache` 結構、`kmalloc-N` size class 怎麼對齊、per-cpu fast path 怎麼在無鎖的情況下運作、freelist 怎麼靠 object 裡嵌的指標串起來、一次 `kmalloc`/`kfree` 完整路徑怎麼走、以及為什麼 SLUB 上的 UAF 幾乎等於「免費重拿同一塊記憶體」。這是本書最關鍵的一章，Part 3 全部站在這裡。

> **環境**：Linux 6.6 LTS、x86-64。SLUB 是 6.5 之後**唯一**的 slab allocator——`CONFIG_SLAB`（舊的 SLAB allocator）已經在 kernel 開發上游走向移除：**依查證，2023 年 11 月有一輪「移除 SLAB allocator」的 patch series 送出，先把 `CONFIG_SLAB` 改名為 deprecated 讓使用者在 `make oldconfig` 時被提示切到 SLUB，再逐步刪除原始碼**；確切在哪個版本完全消失，請以你自己 kernel 原始碼裡還有沒有 `mm/slab.c` 這個檔案為準（6.6 LTS 上大概率已經只剩 SLUB，但本書不對這個版本號下絕對斷言）。本章完全不討論 SLAB，只在需要對照時簡短提及。

## 為什麼需要這個？

你可能寫過 user-space 的 heap exploitation，對 `malloc()`/`free()`、tcache、fastbin、chunk header 這些名詞不陌生。現在要先打掉一個預設：**SLUB 不是「kernel 版的 glibc heap」，它是另一套完全不同的設計**，連「物件旁邊有沒有 header、header 長什麼樣」這種最基本的問題答案都不一樣。

為什麼要花一整章從頭教？因為後面 Part 3（Ch 17–25）幾乎每一招都要你答得出這些問題：

- 我 `kmalloc(48, GFP_KERNEL)`，這個 48 bytes 最終落在哪個 cache？旁邊會是什麼大小的物件？
- 我 `kfree()` 一個物件之後，立刻再 `kmalloc()` 同樣大小，拿到的是不是同一塊記憶體？為什麼？
- freelist 上那個「下一個空閒物件在哪裡」的指標，存在記憶體的哪個位置？這個位置本身是不是攻擊者能控制的？
- 為什麼「heap spray」在 kernel 裡討論的不是「填滿 tcache」，而是「填滿某個 size class 的 slab」？

這些問題的答案都在 SLUB 的內部結構裡，而且**都和具體的 kernel 版本掛鉤**——SLUB 這幾年因為 hardening 需求（Ch 9）改動頻繁，freelist pointer 存放位置、是否加密，都隨版本/config 而不同。本章把 6.6 LTS 基準上的機制講清楚，之後遇到別的版本，你會知道該去查哪個函式確認差異。

## 先建立直覺

把 SLUB 想成一個專門賣「固定尺碼衣服」的倉庫系統，而不是像 `malloc()` 那樣「任意裁切一塊布料」：

```
你要 48 bytes 的記憶體？

glibc malloc 的心態：
  「我去我手上的布料堆（heap）裡找一塊夠大的，
   裁一塊 48+header bytes 給你，剩下的料子留著下次用。」

SLUB 的心態：
  「48 bytes？你被分到『48 號尺碼區』（kmalloc-64 cache，
   因為 48 會被捨入到下一個尺碼）。
   這個尺碼區本來就已經事先切好一整排一樣大的格子，
   我直接給你排隊最前面那一格的鑰匙。」
```

這個「事先切好一整排固定大小的格子」的區域，就叫一個 **slab**。每個 slab 屬於一個特定的 **kmem_cache**（例如 `kmalloc-64`），裡面所有格子（object）大小完全相同。拿（alloc）跟還（free）的動作，本質上就是「從一個清單裡拿一個格子的鑰匙」或「把鑰匙放回清單」——這份清單就是 **freelist**，而且（這是本章最重要的機制）**這份清單不是額外用一塊記憶體存的，它直接借用每個「目前是空的」格子本身的頭幾個 bytes，存著『下一個空格子在哪裡』的指標**：

```
一個 slab（假設是 kmalloc-64，每格 64 bytes）：

┌─────────┬─────────┬─────────┬─────────┬─────────┐
│ object 0│ object 1│ object 2│ object 3│ object 4│
│ (在用)   │ (空閒)   │ (在用)   │ (空閒)   │ (空閒)   │
└─────────┴─────────┴─────────┴─────────┴─────────┘
             │                   │         │
             └─ freepointer ────▶└─ fp ───▶└─ fp ──▶ NULL

freelist 的「清單」= object 1 → object 3 → object 4 → NULL
這條鏈完全靠「空格子裡存的指標」串起來，不額外佔記憶體
```

這就是為什麼理解 SLUB 必須先建立這個圖像：**一個「空閒」的 object，它的記憶體內容不是垂直的、歸零的，它的頭幾個 bytes 存著下一個空閒 object 的位址**。如果你能透過某個漏洞寫壞這個指標（UAF、heap overflow），你就是在直接竄改 allocator 下一次要給誰記憶體的決策——這是 Part 3 幾乎所有 heap corruption 技巧的起點。

## 核心概念：`kmem_cache` 與 `kmalloc-N` 系列

### kmem_cache：一個 size class 的管理者

每個 `kmalloc-N` cache，在 kernel 內部對應一個 `struct kmem_cache`（概念示意，實際欄位以 `include/linux/slub_def.h` 為準）：

```c
struct kmem_cache {
    struct kmem_cache_cpu __percpu *cpu_slab;  /* 每個 CPU 自己的 fast path 狀態 */
    slab_flags_t flags;
    unsigned long min_partial;
    int size;            /* 這個 cache 的 object 大小（含 padding） */
    int object_size;     /* 呼叫者要求的原始大小 */
    int offset;           /* freepointer 存放的位置（offset from object start） */
    int cpu_partial;      /* per-cpu partial list 上限 */
    struct kmem_cache_order_objects oo; /* 一個 slab 用幾個 order 的頁面、裝多少個 object */
    void (*ctor)(void *); /* 建構子，物件第一次配置出來時可能被呼叫 */
    unsigned int inuse;    /* metadata 大小（redzone 等 debug 用途，Ch 9 會談） */
    unsigned int align;
    const char *name;     /* 就是 /proc/slabinfo 看到的 "kmalloc-64" 這種名字 */
    struct list_head list; /* 掛在全域的 cache list 上 */
    struct kmem_cache_node *node[MAX_NUMNODES]; /* 每個 NUMA node 一份管理結構 */
};
```

幾個欄位值得先點出來，後面機制講解會一直回來用：

- **`cpu_slab`**：每個 CPU 核心專屬的一份狀態（下面「per-cpu fast path」整節都在講它）。
- **`offset`**：freepointer 在 object 裡存放的位置——不一定是 object 的最開頭，這點下面「freelist 怎麼串」會解釋為什麼。
- **`node[]`**：每個 NUMA node 各自維護自己的 partial slab list（Ch 7 提過 NUMA 下 direct map 的對應關係，這裡是分配器層級的對應）。

### kmalloc-N 系列：size 怎麼對齊到 cache

你呼叫 `kmalloc(n, GFP_KERNEL)`，`n` 不是直接拿去配置的大小——SLUB 把 `n` 捨入（round up）到一個固定的 size class，這個 size class 就是 cache 的名字。6.6 的通用 size class 序列（小尺寸部分，依查證取自 kernel `mm/slab_common.c` 的 `kmalloc_caches` 初始化邏輯與 `include/linux/slab.h` 的 `kmalloc_index()`）大致是：

| 呼叫 `kmalloc(n, ...)` 的 `n` 範圍 | 落在哪個 cache | 實際 object 大小 |
|---|---|---|
| 1 ~ 8 | `kmalloc-8` | 8 bytes |
| 9 ~ 16 | `kmalloc-16` | 16 bytes |
| 17 ~ 32 | `kmalloc-32` | 32 bytes |
| 33 ~ 64 | `kmalloc-64` | 64 bytes |
| 65 ~ 96 | `kmalloc-96` | 96 bytes（非 2 的次方的特例尺寸，歷史上用來減少 64→128 之間的浪費） |
| 97 ~ 128 | `kmalloc-128` | 128 bytes |
| 129 ~ 192 | `kmalloc-192` | 192 bytes（同樣是特例尺寸） |
| 193 ~ 256 | `kmalloc-256` | 256 bytes |
| ... | 之後都是 2 的次方 | `kmalloc-512`、`kmalloc-1024`、`kmalloc-2048`、`kmalloc-4096`、`kmalloc-8192` |

> **降級措辭**：96 與 192 這兩個特例尺寸,是否存在、存在的確切條件（和 `KMALLOC_MIN_SIZE` 相關，某些架構/config 下的最小對齊要求會讓它們被跳過）**隨 kernel 版本有過調整**，依查證確認 2019 年前後有過一輪針對這兩個尺寸的 patch（引入過 `KMALLOC_SIZE_96_EXIST` / `KMALLOC_SIZE_192_EXIST` 這類條件判斷），但沒有查到 6.6 當下的逐行確認。**這張表的精確性請以你自己 kernel 開機後讀 `/proc/slabinfo`（或 `cat /sys/kernel/slab/kmalloc-*/..` 系列 sysfs 節點）實際列出的 cache 名稱為準**，這也正是練習 A 要你做的第一件事。

最小的 size class（`KMALLOC_MIN_SIZE`）由架構的最小對齊需求決定——x86-64 上是 8 bytes（即使你 `kmalloc(1, ...)`，也會拿到一個 8 bytes 的格子，因為硬體要求某種程度的對齊存取效率）。超過一個上限（`KMALLOC_MAX_CACHE_SIZE`，大尺寸的最後一個 cache 通常落在幾個 KB 的範圍）的配置，不再經過固定 cache，直接呼叫 Ch 7 講的 `alloc_pages()`（`kmalloc_large()` 路徑），回傳一塊剛好夠大的連續頁面——這代表非常大的 `kmalloc()` 其實是跳過 SLUB、直接操作 buddy allocator 的。

### 對齊規則背後的理由：為什麼不直接給你剛好的大小？

這是一個值得停下來問的設計問題：如果你要 48 bytes，為什麼不直接切一塊 48 bytes 給你，而要捨入到 64？

- **減少 cache 數量**：如果每個不同的 byte 數都開一個獨立 cache，系統裡會有成千上萬種 size class，管理成本（每個 cache 都要有自己的 slab、partial list、per-cpu 狀態）遠高於收益。用固定的幾何級數（加上少數特例）覆蓋整個範圍，用少量浪費換取管理簡單。
- **方便合併（cache merging，Ch 9 細講）**：如果兩個不同 subsystem 都申請「差不多大小」的物件，捨入到同一個 size class 之後，它們天生就會共用同一個 cache——這對攻擊者是重要資訊，因為代表你能用 subsystem A 的物件去「污染」subsystem B 常用的 cache，這正是後面 heap grooming 的基礎假設之一。
- **對齊存取效率**：固定幾何級數的大小天生符合常見的硬體對齊要求（cache line、DMA 對齊），不需要每個 size class 另外計算 padding 規則。

## 底層機制：per-cpu fast path——無鎖的核心

這是整個 SLUB 設計裡最值得花時間理解的部分。SLUB 的高效能，建立在**大多數 kmalloc/kfree 完全不需要搶任何鎖**這件事上。

### `kmem_cache_cpu`：每個 CPU 自己的小抽屜

```c
struct kmem_cache_cpu {
    void **freelist;       /* 這個 CPU 目前手上這塊 slab 的 freelist 頭 */
    unsigned long tid;      /* transaction id，用來偵測並行修改 */
    struct slab *slab;      /* 這個 CPU 目前「擁有」的那塊 slab */
    struct slab *partial;   /* 這個 CPU 私藏的一小批「半滿」slab（cpu_partial 機制） */
};
```

直覺理解：**每個 CPU 核心手上握著一塊自己專屬的 slab**，這塊 slab 的 freelist 頭就放在 `kmem_cache_cpu->freelist` 這個欄位裡，隨時可以直接讀寫，不需要問任何人。

### 一次 `kmalloc()` 的 fast path

```
kmalloc(64, GFP_KERNEL) 呼叫進來
        │
        ▼
查表：64 bytes → kmalloc-64 這個 kmem_cache
        │
        ▼
讀取「這個 CPU」的 kmem_cache_cpu->freelist
        │
   ┌────┴────┐
  非 NULL        NULL（這個 CPU 手上的 slab 已經榨乾了）
   │                │
   ▼                ▼
把 freelist 指向的        走 slow path：
object 直接回傳,          向 partial list 要一塊新 slab，
freelist 往前移到         或向 buddy allocator 要一塊全新頁面
下一個空閒 object          （見下一節）
（用 cmpxchg 確保這個
 過程沒被其他東西打斷）
```

**這整段 fast path 完全沒有 spinlock**——它靠 `this_cpu_cmpxchg`（一種 CPU 層級的 compare-and-swap，保證「讀 freelist、算出下一個值、寫回去」這整個動作對同一個 CPU 上的並行存取是原子的）取代傳統的鎖。這是 SLUB 名字裡 "unqueued"（U）的由來之一：它刻意避開了舊 SLAB allocator 大量使用的 per-cpu queue 加鎖機制，換成這種更輕量的 CAS 操作。

為什麼只需要處理「同一個 CPU 上的並行」而不是「所有 CPU 之間的並行」？因為 `kmem_cache_cpu` 是 per-cpu 變數，正常情況下只有擁有這個 CPU 的 thread 會去動它。真正麻烦的並行情況是：這個 thread 在 fast path 執行途中被**中斷**（interrupt handler 也可能呼叫 `kmalloc()`，用的是同一個 per-cpu 結構），或者**被搶占後排程到別的 CPU 上**（這種情況下 `tid` 欄位會偵測到不一致，fast path 失敗後退回 slow path 重試）——`cmpxchg` 加上 `tid` 檢查,就是用來保證這些邊界情況下不會讀到一半被打斷的髒資料。

### slow path：partial list 與向 buddy 要新 slab

當目前 CPU 手上的 slab 被掏空（freelist 變成 NULL），SLUB 不會立刻去跟 buddy allocator 要新頁面,而是先檢查**per-node partial list**：

```
struct kmem_cache_node {
    spinlock_t list_lock;
    struct list_head partial;   /* 「半滿」的 slab：
                                    有些 object 在用、有些空閒，
                                    但沒有被任何 CPU 單獨持有 */
    atomic_long_t nr_slabs;
    atomic_long_t total_objects;
};
```

slow path 大致流程：

```
目前 CPU 的 freelist 空了
        │
        ▼
查這個 NUMA node 的 kmem_cache_node->partial list
（這一步需要搶 list_lock，因為 partial list 是多個 CPU 共享的）
        │
   ┌────┴────┐
  有半滿 slab       完全沒有
   │                  │
   ▼                  ▼
把這塊 slab 搬進    向 buddy allocator 要一塊全新的
目前 CPU 的          order-N 連續頁面（Ch 7 的機制），
kmem_cache_cpu,     切成一排全新的 object，串出一條全新
繼續走 fast path    freelist，變成這個 CPU 的新 slab
```

還有一層「full list」：如果一個 slab 裡所有 object 都被用光（freelist 變空），它會從 partial list 移除（因為對 partial list 而言它已經沒有可拿的東西）。6.6 的 SLUB 對「完全滿的 slab」通常**不**另外維護一個顯式的 full list（取決於 debug 設定），它們單純就是「存在但不在任何 free 相關清單上」，靠 `struct slab` 自己的計數欄位判斷是否已滿;只有當某個 object 被 `kfree()`、讓它從全滿變成有空位時，它才會重新被放回 partial list。

### `cpu_partial`：CPU 私藏的緩衝，減少搶鎖頻率

為了避免「每次目前 slab 空了都要搶 `list_lock`」這種高頻率鎖競爭，SLUB 讓每個 CPU 額外私藏一小批（數量受 `cpu_partial` 限制）半滿的 slab，放在 `kmem_cache_cpu->partial`。當目前 slab 用完時，優先從這個私藏清單拿，拿不到才真的去搶全域 `list_lock`。這是另一層「盡量讓常見路徑不用碰共享鎖」的設計，呼應 Ch 7 提過的 buddy allocator per-cpu pageset 同一個母題。

## 底層機制：freelist 怎麼串——freepointer 位置

### 最簡單情況：freepointer 就放在 object 開頭

預設情況下（沒有特殊 debug flag、物件沒有自己的建構子需要用到物件開頭那塊記憶體），SLUB 把下一個空閒物件的指標，直接寫在**這個空閒物件本身的最開頭幾個 bytes**：

```
一個空閒的 kmalloc-64 object（64 bytes）：

┌────────────────┬──────────────────────────────────┐
│ next freelist 指標 │          剩下沒用到的空間            │
│   (8 bytes)       │        (56 bytes，內容不確定)        │
└────────────────┴──────────────────────────────────┘
offset 0                                        offset 63
```

這解釋了 `kmem_cache->offset` 欄位的意義：大多數情況下它是 0（freepointer 放在最前面），但**如果這個 cache 的物件有 constructor（`ctor`），而這個 constructor 需要在物件配置出來、還沒被呼叫者寫入任何東西之前，就把某些欄位初始化好（例如某些 kernel 內部結構，物件一配置出來就要有合法的 lock 或 list_head 初始狀態)**，SLUB 就不能把 freepointer 疊在這塊記憶體上（會破壞 constructor 初始化的內容）,這時 `offset` 會被設成一個非 0 的值，把 freepointer 藏到物件的別的位置（通常是物件尾端）。

### `set_freepointer()` / `get_freepointer()`：讀寫的統一入口

SLUB 內部所有讀寫 freepointer 的地方，都走這兩個函式（概念示意，依查證確認核心邏輯取自 `mm/slub.c`）：

```c
/* 概念示意版本（未加 hardening） */
static inline void set_freepointer(struct kmem_cache *s, void *object, void *fp)
{
    unsigned long freeptr_addr = (unsigned long)object + s->offset;
    *(void **)freeptr_addr = fp;
}

static inline void *get_freepointer(struct kmem_cache *s, void *object)
{
    unsigned long freeptr_addr = (unsigned long)object + s->offset;
    return *(void **)freeptr_addr;
}
```

這兩個函式之所以重要，是因為它們是 `CONFIG_SLAB_FREELIST_HARDENED`（Ch 9 的主角）要動手的地方——hardened 版本不會直接存原始指標，而是存一個經過 XOR 混淆的值。本章先只看未加固的版本，下一章再加上這層混淆。

## 底層機制：`struct slab`——6.6 把 slab metadata 獨立出來

Ch 7 提過 `struct page` 是高度 overlay 的結構，同一塊記憶體依照用途被重新解讀。2022 年前後（依查證，這輪重構的動機來自 Matthew Wilcox 主導的 `struct page` 拆分工作，目標是讓各個 subsystem 不再直接戳 `struct page` 裡和自己無關的欄位），SLUB 相關的 metadata 被整理進一個獨立命名的型別 **`struct slab`**，概念示意：

```c
struct slab {
    unsigned long __page_flags;   /* 與 struct page 共用記憶體佈局的開頭部分 */
    struct kmem_cache *slab_cache;
    union {
        struct list_head slab_list;   /* 掛在 partial / full 相關清單上 */
        /* per-cpu 情況下的欄位 */
    };
    void *freelist;        /* 這塊 slab 自己的 freelist 頭（還沒被某個 CPU 獨占前的狀態） */
    union {
        unsigned long counters;
        struct {
            unsigned inuse:16;   /* 目前用了幾個 object */
            unsigned objects:15; /* 這塊 slab 總共幾個 object */
            unsigned frozen:1;    /* 是否被某個 CPU 的 kmem_cache_cpu 獨占 */
        };
    };
};
```

`struct slab` 在記憶體佈局上**仍然疊在同一塊 `struct page` 的空間上**（它不是另外配置的結構，是同一塊 vmemmap 區記憶體的另一種解讀方式），這個重構的目的是**型別安全**：讓編譯器能檢查「你是不是在用一個 `struct page *` 的介面，誤用了只該屬於 slab 的欄位」，減少 kernel 開發過程中的邏輯錯誤,而不是改變底層的實際記憶體配置方式。對 exploit 開發者而言，重點是記住：**一塊 slab 的識別資訊（屬於哪個 cache、目前用了幾個、freelist 在哪）本身，也是存在一塊「有結構、可能被攻擊面影響」的 kernel 記憶體裡**——雖然 vmemmap 區本身通常不是直接的覆寫目標（它受到的保護和一般資料不同），理解這個結構有助於看懂 `/proc/slabinfo`、crash dump 分析工具（crash、drgn）裡印出的欄位對應到什麼。

> **版本提醒**：`struct slab` 這個獨立命名在較舊的 kernel（例如 5.x 系列前期）原始碼裡看不到，相關欄位直接混在 `struct page` 裡用不同名字表示。如果你對照的是舊版 kernel 原始碼或舊的 writeup，看到的是 `page->freelist`、`page->inuse` 這種寫法，邏輯等價，只是還沒拆出獨立型別。

## 底層機制：NUMA node 怎麼選、per-node 結構的意義

如果你的機器（或 QEMU 設定）有多個 NUMA node，SLUB 預設會優先從**呼叫者目前執行所在的 CPU 所屬的 NUMA node**要記憶體（概念上類似「就近取貨」，減少跨 node 存取記憶體的延遲）。`kmalloc_node()` 這個變體允許呼叫者明確指定要哪個 node，一般的 `kmalloc()` 則是透過 `numa_node_id()` 取得目前 CPU 的 node 再轉呼叫。

```
kmalloc(64, GFP_KERNEL) 在 CPU 3（屬於 NUMA node 1）上執行
        │
        ▼
轉呼叫 kmalloc_node(64, GFP_KERNEL, node=1)
        │
        ▼
查 kmem_cache->node[1] 這個 kmem_cache_node 結構的 partial list
        │
        ▼
（找不到的話向 buddy allocator 要屬於 node 1 的實體頁面）
```

這件事對 heap grooming 的實際影響：**如果你的 exploit 在多核心、多 NUMA node 的目標機器上跑，噴出去的物件可能散落在不同 node 各自的 partial list 裡**，和你分析單 node（或你自己測試用的單 NUMA QEMU）環境時觀察到的行為不完全一致。真實世界的 kernelCTF 題目通常用單 NUMA node 設定跑（簡化複雜度），但如果你的目標是真實多 socket 伺服器，這是一個要納入考量的變因。CTF 以外的真實環境分析，`numactl --hardware` 可以看到機器的 NUMA 拓樸。

## 範例一：一次完整的 kmalloc → kfree → kmalloc 追蹤

這是驗證「SLUB 的 LIFO 行為」最直接的方式——寫一個 kernel module，連續配置、釋放、再配置，觀察位址：

```c
// slub_lifo_demo.c
#include <linux/module.h>
#include <linux/slab.h>
#include <linux/kernel.h>

static int __init slub_lifo_demo_init(void)
{
    void *a, *b, *c;

    a = kmalloc(64, GFP_KERNEL);
    b = kmalloc(64, GFP_KERNEL);
    c = kmalloc(64, GFP_KERNEL);
    pr_info("slub_lifo: a=%px b=%px c=%px\n", a, b, c);

    kfree(b);                       /* 把中間那塊放回 freelist */
    void *d = kmalloc(64, GFP_KERNEL); /* 預期：d == b（LIFO，最後放回的先被拿走） */
    pr_info("slub_lifo: after kfree(b), new kmalloc -> d=%px (expect d == b)\n", d);

    kfree(a);
    kfree(c);
    kfree(d);
    return 0;
}

static void __exit slub_lifo_demo_exit(void) { }

module_init(slub_lifo_demo_init);
module_exit(slub_lifo_demo_exit);
MODULE_LICENSE("GPL");
```

> **未實測，理論預期行為**：本環境無法編譯/載入 kernel module。預期輸出會類似：
> ```
> slub_lifo: a=ffff8880... b=ffff8880...(a+40) c=ffff8880...(a+80)
> slub_lifo: after kfree(b), new kmalloc -> d=ffff8880...  (與 b 相同)
> ```
> 預期 `d == b`，原因是 fast path 的設計：`kfree(b)` 把 `b` 推回目前 CPU 的 freelist 最前端（變成下一個要被拿走的 object），只要期間沒有其他執行緒搶先在同一個 CPU 上對同一個 cache 做 `kmalloc`，下一次 `kmalloc(64, ...)` 就會直接從 freelist 頭部拿到剛放回去的 `b`。這就是「SLUB 上 UAF 幾乎等於免費重拿」的來源：**釋放後立刻用同大小重新配置，大概率拿到同一塊記憶體**。驗證方式：在你自己 QEMU（Linux 6.6 LTS）上 `insmod` 這個 module 後 `dmesg`，比對 `b` 與 `d` 的位址是否相同；為了提高重現率，建議在單核 QEMU（`-smp 1`）上跑，避免被排程到別的 CPU 的 per-cpu slab 干擾觀察。

### 範例二：邊界情況——cache 空了，位址就不連續了

```c
// slub_boundary_demo.c（關鍵片段）
#define N 2000  /* 故意配置超過一個 slab 能裝的數量，逼 SLUB 跨 slab */

static int __init slub_boundary_demo_init(void)
{
    void **ptrs = kmalloc_array(N, sizeof(void *), GFP_KERNEL);
    int i, jumps = 0;

    for (i = 0; i < N; i++)
        ptrs[i] = kmalloc(64, GFP_KERNEL);

    for (i = 1; i < N; i++) {
        long diff = (long)ptrs[i] - (long)ptrs[i - 1];
        if (diff != 64 && diff != -64) {
            /* 位址差不是一個 object 大小，代表跨到了不同的 slab
               （不同 slab 彼此在實體/虛擬記憶體上不保證相鄰） */
            jumps++;
        }
    }
    pr_info("slub_boundary: observed %d non-contiguous jumps across %d allocations\n",
            jumps, N);

    for (i = 0; i < N; i++)
        kfree(ptrs[i]);
    kfree(ptrs);
    return 0;
}
```

> **未實測，理論預期行為**：預期 `jumps` 會是一個明顯大於 0 的數字——因為一個 order-N 的 slab 只能裝固定數量的 kmalloc-64 object（例如一個 order-0 的 4KB slab，扣掉 metadata 大概能裝 60 幾個 64-byte object），配置超過這個數量後，SLUB 會去 partial list 或 buddy allocator 要新的 slab，而新 slab 的位址和舊 slab之間**沒有任何保證的相鄰關係**。這個邊界案例要打破一個常見誤解：「同一個 cache 配置出來的東西，位址一定連續」——只在「還在同一塊 slab 裡」的範圍內近似成立，跨 slab 之後這個假設直接失效，heap grooming（Ch 20）很大一部分工作就是在處理這種跨 slab 的不確定性。

## 對比與取捨：SLUB vs glibc heap（給有 user-space heap 經驗的讀者）

| 維度 | glibc heap（tcache / fastbin / smallbin...） | SLUB |
|---|---|---|
| 物件大小管理 | 每個 chunk 有 header（含 size、flag），大小可以是任意對齊值 | 固定 size class（`kmalloc-N`），物件本身通常**沒有**額外的 per-object header（除非開 debug flag） |
| 空閒物件怎麼串 | tcache/fastbin 用物件開頭存 `next` 指標（概念上和 SLUB 像），但同時有多種 bin 並存、規則複雜 | 單一 freelist 機制，但依 per-cpu / per-node 分成好幾層（fast path freelist、cpu partial、node partial） |
| 合併/切分相鄰空閒塊 | 有（fastbin 不做，但 unsorted bin/smallbin 會 consolidate） | **沒有**這種機制——SLUB 的 object 大小固定，不會把兩個相鄰空 object 合併成一個大 object；「合併」發生在更底層的 buddy allocator，單位是整塊 slab 而不是 object |
| LIFO 行為 | tcache 是 LIFO；其他 bin 不一定 | fast path 的 per-cpu freelist 是 LIFO |
| Double free 檢測 | tcache 在較新 glibc 有簡單的 double-free 檢查 | 開啟 `CONFIG_SLAB_FREELIST_HARDENED` 後有一定程度的健全性檢查（Ch 9），baseline SLUB 本身對 double free 的防禦很薄弱 |
| 攻擊者最常打的目標 | tcache next 指標、chunk size 欄位、unlink 相關 metadata | freelist 指標（freepointer）、物件被 UAF 後被重新解讀的其他 kernel 結構欄位（type confusion） |

如果你是從 glibc heap 過來的讀者，**這張表最重要的一行是「合併/切分」那一行**——不要把 glibc 的 chunk consolidation 直覺帶進 SLUB，SLUB 完全沒有這個概念，它的「大小」維度在物件配置出來那一刻就固定死了，唯一會變動的是「哪些物件目前空閒」。

## 範例三：進階——用 `/proc/slabinfo` 對照你剛才的推論

```bash
# 先看某個已知 size class 目前的狀態
$ sudo cat /proc/slabinfo | grep "^kmalloc-64 "
kmalloc-64           18432  18432     64   64    1 : tunables ...
#                     │       │        │    │    │
#                   active  可用     每個   每個  每個 slab
#                   objects objects  object slab  用幾個頁面
#                   總數    總數     大小   裝幾個 (order)
```

欄位意義（依 `/proc/slabinfo` 的標準格式，`<name> <active_objs> <num_objs> <objsize> <objperslab> <pagesperslab>`）：

- `active_objs`：目前「在用」的 object 數。
- `num_objs`：這個 cache 目前總共配置出來的 object 數（含空閒的）。
- `objsize`：這個 cache 每個 object 的大小。
- `objperslab`：一塊 slab 裝幾個 object——用這個數字反推一塊 slab 的大小（`objperslab * objsize` 大致等於 `pagesperslab * PAGE_SIZE`，差額是 metadata）。
- `pagesperslab`：一塊 slab 用幾個實體頁面（也就是 Ch 7 的 order 概念，`pagesperslab` 是 `2^order`）。

練習 A 會要你親手跑這個指令，對照你 module 印出來的位址與 object 數量變化，驗證本章講的每一個機制。

## 踩雷集錦

1. **錯誤直覺：「kfree 之後記憶體內容會被清空」**。預設行為完全不是這樣——`kfree()` 只是把這個 object 推回 freelist（寫入 freepointer），**物件原本的資料內容原封不動留在記憶體裡**，直到下一次被重新配置、呼叫者自己覆寫它。這正是 UAF 可以讀出敏感殘留資料的原因。只有開啟 `init_on_free`（kernel boot 參數或 config，6.6 上是可選的 hardening 機制，Ch 9 稍微提及）才會在釋放時主動清零。

2. **錯誤直覺：「同一個 kmalloc size，不同次呼叫一定落在同一個 cache」**。大小落在哪個 cache 只看捨入後的 size class，和「誰呼叫」「用什麼 GFP flag」都可能有關——`GFP_KERNEL_ACCOUNT` 會被導向獨立的 `kmalloc-cg-N` 系列（Ch 9），和不帶這個 flag 的同大小配置落在不同 cache。確認兩個配置是否真的共用 cache，要同時看大小**和** flag。

3. **邊界情況：fast path 不是「永遠免費」**。fast path 的 cmpxchg 在高並發、該 CPU 剛好被中斷打斷的情況下**會失敗並重試**，重試多次失敗後還是會退回 slow path。單執行緒、低負載下你看到的「幾乎零成本」不代表在高度並發的真實系統下完全沒有競爭——Part 6（並發章節）會回來討論這種競爭窗口如何被利用來擴大 race condition。

4. **錯誤直覺：「freepointer 永遠在 object 的第 0 個 byte」**。只有在沒有 constructor 需要用到那塊記憶體的「一般」情況下才是如此。某些 cache（有 `ctor` 的）會把 `offset` 設成非 0，freepointer 被藏到別處。如果你在分析一個不熟的 cache 時假設 offset 永遠是 0，覆寫 primitive 設計出來的 payload 可能完全打不中 freepointer。

5. **效能/正確性混淆：「per-cpu slab 代表這塊記憶體『屬於』某個 CPU，其他 CPU 存取會出錯」**。不是這樣——`kmem_cache_cpu` 只是**管理權**（誰負責從這塊 slab 切 object 出去）屬於某個 CPU，記憶體本身依然是全域可被任何 CPU 讀寫的一般 kernel 記憶體（透過 direct map，Ch 6）。另一個 CPU 上執行的程式碼完全可以合法地讀寫同一個 object 的內容（例如透過一個共享的指標），per-cpu 的設計只是為了減少「配置/釋放的簿記」上的鎖競爭,不是記憶體存取權限的隔離機制。

## 進階：再往深一層

- **`kmem_cache_alloc_bulk()` / `kmem_cache_free_bulk()`**：SLUB 提供批次配置/釋放的介面，一次處理多個 object，減少重複進出 fast path 的開銷。某些高頻率的 kernel 子系統（網路封包處理）會用這組 API，理解它有助於看懂一些效能敏感路徑的 spray 行為差異。
- **`kmem_cache_create()` 的自訂 cache**：不是所有 kernel 物件都走 `kmalloc-N` 通用系列——很多 subsystem 會用 `kmem_cache_create()` 建立自己專屬的 dedicated cache（例如 `task_struct`、`sighand_struct` 這類固定大小、高頻率配置的結構）。這些 dedicated cache 不會和 `kmalloc-N` 合併（Ch 9 的 cache merge 規則），是 cross-cache attack（Ch 22）要特別處理的一類目標。
- **`SLUB_CPU_PARTIAL`**：如果你想精確控制 heap grooming 的行為，深入理解 `cpu_partial` 的上限計算邏輯（它和 object 大小、slab order 有關）是進階但值得investment的方向，Ch 20 會回來用到。

## 動手練習

在繼續 Ch 9 之前，先用紙筆（不看任何工具）畫出以下情境的記憶體狀態圖：

1. 連續 `kmalloc(64)` 五次，依次叫 A B C D E。
2. `kfree(C)`，再 `kfree(A)`。
3. 下一次 `kmalloc(64)` 預期拿到誰？再下一次呢？

畫完後對照本章「per-cpu fast path」那一節的 LIFO 邏輯驗證你的答案（答案：先拿到 A，因為 A 是最後被放回 freelist 的；再下一次拿到 C）。這個練習會在練習 A 用真實 module 驗證一次。

## 本章重點整理

- SLUB 用固定的 `kmalloc-N` size class 管理物件，不做跨物件的合併/切分，和 glibc heap 的 chunk consolidation 概念完全不同。
- 效能核心是 **per-cpu fast path**：每個 CPU 持有自己的 slab 與 freelist，靠 `cmpxchg` + `tid` 做無鎖的並行保護；slow path 才會動到 per-node partial list 的 spinlock，或往 buddy allocator 要新頁面。
- freelist 靠**物件本身**存放 freepointer（通常在 offset 0，但有 constructor 的 cache 會移到別處），這個設計讓「覆寫一個空閒物件」直接等於「竄改 allocator 的下一次配置決策」。
- SLUB 的 LIFO 行為，讓「釋放後立刻用同大小重新配置」在單 CPU、無干擾情況下大概率拿到同一塊記憶體——這是 UAF 利用的核心前提，但不是 100% 保證，受並行與 cache 狀態影響。

## 自我檢核

- [ ] 不看筆記，能不能完整畫出一次 `kmalloc()` fast path 的判斷流程，包含它什麼時候會退回 slow path？
- [ ] 如果有人問你「SLUB 的 freelist 存在哪裡，為什麼不另外配置一塊記憶體存管理資訊？」，你會怎麼回答，這個設計換來了什麼、犧牲了什麼？
- [ ] 你能不能解釋「為什麼 kfree 之後立刻 kmalloc 同大小，大概率拿到同一塊記憶體」，並說出至少一個這個假設會失效的情境？
- [ ] 回想你曾經分析過的某個 user-space heap exploit，如果把同樣的漏洞換到 kernel 的 SLUB 上，哪些步驟需要重新設計？

## 延伸閱讀

### 論文 / 原始設計文件

- **[The Slab Allocator: An Object-Caching Kernel Memory Allocator](https://www.usenix.org/legacy/publications/library/proceedings/bos94/full_papers/bonwick.pdf)** — Jeff Bonwick，USENIX Summer 1994 Technical Conference
  - **核心貢獻**：SLAB 概念的原始論文（Solaris），奠定了「object caching」這個設計思路——SLUB 是這個思路在 Linux 上的後繼實作，理解原始動機有助於理解 SLUB 為什麼長這樣
  - **讀哪裡**：前半段關於 "object caching" 與 cache 設計動機的部分；具體資料結構章節可以跳過，因為 SLUB 的實作已經和原始 SLAB 差異很大
  - **和本章的關聯**：解釋「為什麼要有固定大小的 cache」這個最根本的設計哲學

### 部落格 / 技術文章

- **[pawnyable — Linux Kernel：SLUB 相關章節](https://pawnyable.cafe/linux-kernel/)**
  - **這篇說什麼**：用具體 CTF 題目配合圖解，示範 SLUB 的 freelist 結構與常見漏洞模式
  - **讀哪裡**：涉及 heap/SLUB 內部結構的章節
  - **為什麼值得讀**：目前中文/日文圈對 SLUB 攻擊面講得最細的教材之一，和本章的理論可以互相驗證

- **[Attacking the Linux Kernel（lrh2000）](https://lrh2000.com/)**
  - **這篇說什麼**：深入現代 SLUB 內部機制與對應的利用技巧，含 per-cpu 結構的細節討論
  - **讀哪裡**：SLUB 內部機制相關的系列文章
  - **為什麼值得讀**：作者對 SLUB 原始碼的追蹤深度很高，適合在本章之後進一步確認版本差異細節

### 官方文件 / 原始碼

- **[`mm/slub.c`（kernel.org Elixir，v6.6）](https://elixir.bootlin.com/linux/v6.6/source/mm/slub.c)**
  - **讀哪裡**：`kmem_cache_alloc`、`slab_alloc_node`、`___slab_alloc`（fast/slow path 分界）、`set_freepointer`/`get_freepointer`
  - **和本章的關聯**：本章所有「概念示意」程式碼的真正第一手來源；版本更新後函式名稱可能調整，用 Elixir 對照你實際使用的版本號

### 讀完本章之後

- **[CONFIG_SLAB_VIRTUAL 相關討論與 KSPP wiki](https://kernsec.org/wiki/index.php/Kernel_Self_Protection_Project)** —— 下一章會提到的新一代 SLUB hardening 方向，理解本章的 freelist 機制後，能更快看懂這類「重新設計記憶體佈局以防堵利用」的提案在解決什麼問題

本章建立的 fast path、freelist、cache 對齊這套心智模型，是整本書 Part 3 的語言——下一章我們看 kernel 社群怎麼在這套機制上加裝各種鎖，讓「覆寫 freepointer」這招變得沒那麼好用。

→ [Ch 9 SLUB hardening](./09-slub-hardening.md)
