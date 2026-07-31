# Ch 17 — Physical memory：zone 與 buddy allocator

> **目標**：搞懂 kernel 怎麼看待每一塊實體記憶體——每個實體頁一個 `struct page`、頁被歸到 NUMA node 底下的 zone、zone 裡的空閒頁用 **buddy allocator** 按 order 分組管理。學完你能回答 Ch 6 留下的問題：`alloc_pages()` 往下走，最後是誰、用什麼演算法，把那幾個實體頁交到你手上；以及為什麼 kernel 幾十年來靠 buddy 這套「切半／合併」的把戲對抗外部碎片。

在 Ch 6 我們把記憶體配置的 API 全景走過一遍：`kmalloc` 拿小塊、`vmalloc` 拿虛擬連續、slab 管物件快取。但那一章刻意在 `alloc_pages()` 這一層打住——所有這些配置器，往下挖到最底，都在跟同一個東西要記憶體：**buddy allocator**。slab 從 buddy 批發整頁再零售物件、`vmalloc` 一頁一頁跟 buddy 拿再拼虛擬位址、`kmalloc` 大到一定程度直接走 buddy。這一章我們就下到那個底，看清楚實體記憶體管理的最底層長什麼樣。

## 為什麼需要這個？

先想一個看似簡單的問題：kernel 開機後拿到一大塊實體 RAM（比方 4 GB），它要怎麼「管理」這塊記憶體，好讓後面有人喊「給我 3 個連續的實體頁」時能快速答應、有人還回來時能收好？

最天真的做法是拿一個 free list，把所有空閒的頁串起來，配置時摘一個、釋放時掛回去。這對「一次要一頁」很好用，但一旦有人要**連續多頁**（DMA buffer、大的 kernel 結構、`vmalloc` 之外需要實體連續的場景）就完蛋：free list 不知道哪些頁在實體上相鄰，你得掃整條 list 去湊。更糟的是**外部碎片（external fragmentation）**——配了又放、放了又配之後，空閒頁散落各處，總量還有 2 GB，卻湊不出 4 個連續頁。這在會跑幾個月的 kernel 上是致命的。

所以真正的需求是：一套**能高效配置／釋放「2 的次方個連續頁」、並且主動把相鄰的空閒塊合併回大塊**的機制。這就是 buddy allocator 存在的理由。它不是最省記憶體的演算法（有內部碎片，見後面），但它把「找連續塊」和「對抗碎片」這兩件難事用一個優雅的資料結構同時解掉，而且配置／釋放都是接近 O(1)（實際是 O(order)，order 上限 10，等於常數）。

在 kernel 版圖裡它的定位是**實體記憶體配置的根**：

```
   kmalloc ─┐
   vmalloc ─┼──► alloc_pages() ──► buddy allocator ──► 實體頁 (struct page)
   slab    ─┘        (Ch 6 打住的地方)     (本章)
```

## 先建立直覺

在碰任何源碼之前，先把三個層次的心智模型建起來：**page → zone → buddy**。

### 第一層：每個實體頁一個 `struct page`

kernel 把實體記憶體切成固定大小的頁（x86_64 上是 4 KB）。**每一個實體頁，都對應一個 `struct page` 結構**（定義在 `include/linux/mm_types.h`）。這是 mm 子系統的原子——所有記憶體管理的操作，最後都落在「操作某個 `struct page`」上。

這些 `struct page` 排成一個大陣列（概念上叫 `mem_map`，現代 x86_64 用 `vmemmap`）。陣列的**索引就是 pfn（page frame number，頁框號）**——第 0 頁、第 1 頁、第 2 頁……pfn 乘上頁大小就是實體位址：

```
   pfn:        0        1        2        3        4     ...
              ┌────────┬────────┬────────┬────────┬────────┐
   實體 RAM   │ 4 KB   │ 4 KB   │ 4 KB   │ 4 KB   │ 4 KB   │ ...
              └────────┴────────┴────────┴────────┴────────┘
                  ▲        ▲        ▲        ▲        ▲
   vmemmap:  ┌────┴───┬────┴───┬────┴───┬────┴───┬────┴───┐
   struct    │page[0] │page[1] │page[2] │page[3] │page[4] │ ...
   page 陣列  └────────┴────────┴────────┴────────┴────────┘

   pfn ↔ struct page ↔ 實體位址 三者一一對應，互相 O(1) 換算
```

這個三向對應非常重要，因為 buddy 演算法要靠 pfn 的位元運算來找「兄弟」（後面會看到）。換算的巨集就是那幾個：`page_to_pfn()`、`pfn_to_page()`、`pfn_to_phys()`（或 `page_to_phys()`）。

`struct page` 本身是 mm 裡最擠、最被摳門的結構——它有幾十億個實例（4 GB RAM 就有一百萬個），每個欄位都斤斤計較，所以裡面塞滿了 `union`，同一塊記憶體在「這頁是 buddy 空閒頁」「這頁在 page cache」「這頁是 slab」等不同用途下有不同意義。你現在不用讀懂它全部，只要記住：**它是每頁的元資料，buddy 用它的一部分欄位（`lru` list 掛進 free list、`private` 存 order）來串接空閒頁**。

### 第二層：頁被歸到 node → zone

不是所有實體頁都生而平等。兩個維度讓 kernel 必須把頁**分群**：

1. **NUMA（非統一記憶體存取）**：多路伺服器上，每顆 CPU 有自己「近」的記憶體，存取遠端 node 的記憶體慢。kernel 為每個 NUMA node 建一個 `pg_data_t`（`struct pglist_data`，`include/linux/mmzone.h`），配置時優先給本 node 的頁（接 Ch 15 的 NUMA balancing）。單路桌機／筆電只有一個 node。

2. **zone（區）**：就算在同一個 node 裡，不同位址範圍的頁**用途受限**。最經典的是老 DMA 裝置只能定址低位實體記憶體（ISA DMA 只能碰低 16 MB）。如果 kernel 把低位頁隨便發給一般配置，等到 DMA 裝置要低位頁時就湊不出來了。所以 kernel 把頁按位址範圍切成 zone：

```
   pg_data_t (NUMA node 0)
      │
      ├── ZONE_DMA      低 16 MB    ← 給只能定址低位的老 DMA 裝置（Ch 41）
      ├── ZONE_DMA32    低 4 GB     ← 給只能定址 32-bit 的 DMA 裝置
      ├── ZONE_NORMAL   4 GB 以上   ← 一般配置的主戰場
      ├── ZONE_MOVABLE  (虛擬 zone) ← 只放可搬移的頁，利於熱插拔/大頁
      └── ZONE_HIGHMEM  (32-bit 遺產，x86_64 已不用)
```

> **HIGHMEM 是 32-bit 的傷疤**：32-bit kernel 的虛擬位址空間只有 4 GB，kernel 直接映射不了超過約 896 MB 的實體記憶體，超過的部分（HIGHMEM）要臨時映射才能碰。x86_64 位址空間夠大、全部實體記憶體都能直接映射，所以 `ZONE_HIGHMEM` 在 64-bit 上是空的。你在 6.12 的 `enum zone_type`（`include/linux/mmzone.h`）還會看到它，那是為 32-bit 保留的歷史包袱，本課主線（x86_64）用不到，但看到別困惑。

**關鍵設計**：buddy allocator 不是「一個」，而是**每個 zone 一套**。每個 `struct zone`（`include/linux/mmzone.h`）裡都有自己的 free list 結構（`free_area`）。你喊 `alloc_pages(GFP_KERNEL, 0)`，GFP flag 決定了「能從哪些 zone 拿」（`GFP_DMA` → ZONE_DMA、`GFP_KERNEL` → 從 NORMAL 往下 fallback），然後在選中的 zone 的 buddy 裡配置。

### 第三層：buddy 怎麼在一個 zone 裡管空閒頁

這是本章的靈魂，下一節專門講。先給一句話直覺：**空閒頁按「大小」分組，大小只能是 2 的次方（1、2、4、8……1024 頁），這個指數叫 order。配置時把大塊切半、釋放時把相鄰的兄弟合併回大塊。**

## 底層機制：buddy allocator 怎麼運作

### free_area：按 order 分組的空閒清單

打開 `struct zone`（`include/linux/mmzone.h`），核心是這個陣列：

```c
struct free_area  free_area[NR_PAGE_ORDERS];
```

`NR_PAGE_ORDERS` 是 `MAX_PAGE_ORDER + 1`，在 x86_64 上 `MAX_PAGE_ORDER` 是 10，所以 order 從 0 到 10 共 11 個。order N 代表 **2^N 個連續頁**的塊：

| order | 頁數 | x86_64 大小 |
|---|---|---|
| 0 | 1 | 4 KB |
| 1 | 2 | 8 KB |
| 2 | 4 | 16 KB |
| ... | ... | ... |
| 10 | 1024 | 4 MB |

> 為什麼上限是 order 10（4 MB）？這是「連續實體記憶體最大保證單位」的權衡：再大很難維持不碎，且需求罕見（要更大用 `CMA` 或 `hugetlb` 另走機制）。這個上限在 6.x 由 `MAX_PAGE_ORDER` 定義（早期版本叫 `MAX_ORDER` 且語意是「數量」而非「最大 order」，6.x 改名並改語意，這是常見的踩雷點——見踩雷集錦）。

每個 `free_area`（`include/linux/mmzone.h`）裡是**按 migratetype 再分的一堆 list**：

```c
struct free_area {
    struct list_head  free_list[MIGRATE_TYPES];
    unsigned long     nr_free;
};
```

先忽略 migratetype（下一節講），把它想成「order N 的所有空閒塊，用各自的 `struct page.lru`（其實是 `buddy_list`）串成一條 doubly linked list」。畫出來：

```
   free_area[0] ──► [1頁]─[1頁]─[1頁]           nr_free=3
   free_area[1] ──► [2頁連續]─[2頁連續]          nr_free=2
   free_area[2] ──► [4頁連續]                    nr_free=1
   free_area[3] ──► (空)                         nr_free=0
   free_area[4] ──► [16頁連續]                   nr_free=1
      ...
   free_area[10]──► [1024頁連續]                 nr_free=1
```

每條 list 上掛的是「那個塊的第一個 `struct page`」（head page），順著它就能拿到整塊 2^order 個連續頁。

### 配置：找夠大的 order，切半降級（split / expand）

假設現在 `free_area` 長上面那樣，有人喊「給我 order-1（2 頁）」。核心函式是 `__rmqueue_smallest()`（`mm/page_alloc.c`）：

1. 從**要求的 order 開始往上找**第一個非空的 free list。order-1 的 list 非空（有兩個 2 頁塊），直接摘一個下來，`nr_free--`，回傳。皆大歡喜，O(1)。

但如果要求 order-1 而 order-1、order-2、order-3 都空、order-4 才有一個 16 頁塊呢？這時要**切**。這一步是 `expand()`（`mm/page_alloc.c`）幹的活。從 order-4 摘下那個 16 頁塊，一路切半降級，把切出來的另一半掛回下一層的 free list：

```
   要 order-1，但只有 order-4 有貨。從 order-4 切下來：

   order-4:  [────────── 16 頁 ──────────]   ← 摘下這塊
                    │ split
                    ▼
   order-3:  [── 8 頁 ──][── 8 頁 ──]
                │            └──► 掛回 free_area[3]（給別人用）
                │ split（左半繼續切）
                ▼
   order-2:  [4頁][4頁]
              │    └──► 掛回 free_area[2]
              │ split
              ▼
   order-1:  [2頁][2頁]
              │    └──► 掛回 free_area[1]
              ▼
           這 2 頁交給你 ✓

   淨結果：你拿到 2 頁，free_area[3]/[2]/[1] 各多一塊（切剩的半塊）
```

`expand()` 的迴圈本質就是：只要當前 order 比目標大，就對半切，較高位址那半掛回低一級的 free list，較低位址那半繼續往下切，直到切到目標 order 為止。這保證了「大塊只在真的需要時才被拆」，而拆下來的碎塊立刻可被更小的請求利用。

### 釋放：檢查 buddy，能合就合（coalesce）

釋放才是 buddy 的精髓、名字的由來。核心函式 `__free_one_page()`（`mm/page_alloc.c`）。當你還回一個 order-N 的塊，它不會傻傻地掛回 `free_area[N]` 就算了，而是問一句：**「我的 buddy（同 order 的雙胞胎兄弟）現在是不是也空閒？」** 如果是，就把兩個合併成一個 order-(N+1) 的塊，然後對這個大塊**再問一次**，一路往上合併。

「buddy」是誰？這是整個演算法最漂亮的一手。一個 order-N 塊的起始 pfn，它的 buddy 的起始 pfn 就是**把第 N 個 bit 翻轉（XOR）**：

```c
buddy_pfn = page_pfn ^ (1 << order);
```

因為塊都對齊到 2^order 邊界，一對兄弟的起始 pfn 只差在第 N 個 bit。這一個 XOR 就 O(1) 找到兄弟，不用搜尋。找到後還要驗證那個 buddy 確實是空閒的、且 order 相同（`buddy_order()` 從 `struct page.private` 讀 order，並檢查 `PageBuddy` 標記），才能合併。畫出來：

```
   釋放 pfn=4 的 order-0 頁（1 頁）：

   buddy_pfn = 4 ^ (1<<0) = 4 ^ 1 = 5
   pfn=5 空閒嗎？ 是 ──► 合併成 order-1 塊，起始 pfn = min(4,5) = 4

        [pfn4][pfn5]  ──合併──►  [── pfn4~5 order-1 ──]

   再問：這個 order-1 塊(pfn=4)的 buddy？
   buddy_pfn = 4 ^ (1<<1) = 4 ^ 2 = 6
   pfn=6 起始的 order-1 塊空閒嗎？ 否 ──► 停止合併

   最終：一個 order-1 塊掛進 free_area[1]
```

這個「釋放時主動合併」就是 buddy 對抗**外部碎片**的核心武器：碎片產生的當下就被回收成大塊，而不是等到需要大塊時才發現湊不出來。這是 buddy 相對純 free list 的決定性優勢。

### 為什麼是 XOR？——buddy 對齊的漂亮之處

多花一句解釋這個 XOR 為什麼成立，因為它是理解整個演算法的鑰匙。order-N 的塊永遠對齊到 2^N pfn 邊界（0、2^N、2×2^N……）。所以一對相鄰、能合併的兄弟塊，它們的起始 pfn 一定是「除了第 N 個 bit，其餘 bit 全同」。翻轉第 N bit 就在兩兄弟間來回。而且合併後大塊的起始 pfn 是兩者較小的那個（第 N bit 為 0 那個），自然又對齊到 2^(N+1) 邊界——遞迴合併得以成立。這套位元對齊是 buddy 能做到 O(1) 找兄弟、無需任何搜尋的根本原因。

## migratetype 與抗碎片

上面刻意跳過了 `free_area[N]` 裡為什麼要按 `MIGRATE_TYPES` 再分一層。這是 2.6 後期加進來、對抗**碎片化**的關鍵設計，值得單獨講。

buddy 的合併只在「兄弟同時空閒」時才發生。但實務上有些頁**幾乎不可能被搬走或回收**——例如 kernel 自己的資料結構（一個指標指著它，你不能偷偷搬它）。這種「釘死」的頁像地雷一樣散布，會擋住合併：明明周圍都空了，就因為中間卡一個不可搬移的 kernel 頁，湊不出大塊。

kernel 的對策是**按可搬移性把頁分類**，同類的頁放一起，讓「難搞的頁」聚成堆、不去汙染「好搬的頁」的區域。主要幾種（`include/linux/mmzone.h` 的 `enum migratetype`）：

| migratetype | 意思 | 例子 |
|---|---|---|
| `MIGRATE_UNMOVABLE` | 搬不動 | kernel 內部資料、slab、DMA buffer |
| `MIGRATE_MOVABLE` | 可搬移 | user space 匿名頁、page cache（改個映射就能搬） |
| `MIGRATE_RECLAIMABLE` | 可回收 | 有 backing store 的頁，能丟掉重讀 |
| `MIGRATE_PCPTYPES` | （分界，見下）| |
| `MIGRATE_CMA` / `MIGRATE_ISOLATE` | CMA 專用 / 隔離中 | 大塊連續配置、記憶體熱插拔 |

配置時，`GFP` flag 帶的 `__GFP_MOVABLE` 等資訊會轉成 migratetype（`gfp_migratetype()`），優先從**同 migratetype** 的 free list 拿。同類聚集之後，可搬移的頁擠在一起，需要大連續塊時（大頁、CMA）可以把那一整區的頁**搬走騰空**（memory compaction，記憶體壓實），這才有本錢生出 order-9、order-10 的大塊。這是 THP（透明大頁）能運作的地基。

如果目標 migratetype 沒貨怎麼辦？走 fallback（`mm/page_alloc.c` 的 `fallbacks` 陣列與 `__rmqueue_fallback()`），按預設順序去別的 migratetype 借，同時盡量「偷一整塊」而非零頁，避免把好區弄髒。

### per-CPU page lists（pcp）：熱頁快取免鎖

還有一層在 buddy 之上的快取，直接接 Ch 7 的 per-CPU 主題。單頁（order-0）的配置／釋放是所有需求裡最頻繁的（page fault 一次要一頁）。每次都去動 zone 的 buddy 就得抓 zone 的鎖（`zone->lock`），在多核上是熱點。

kernel 的解法是每個 CPU 一份 **per-CPU pages（pcp）**列表（`struct per_cpu_pages`，`include/linux/mmzone.h`）。配置 order-0 時先看本 CPU 的 pcp 有沒有現貨，有就直接拿——**不用抓 zone 鎖**，因為別的 CPU 碰不到你這份（只要關搶佔／關本地中斷即可，見 Ch 7）。釋放單頁也先丟回 pcp，攢到一批（`batch`）或超過上限（`high`）才一次性倒回 buddy。

```
   alloc order-0:
       本 CPU pcp 有貨？ ──是──► 直接拿，免 zone->lock（快路徑）
                          └否──► 從 buddy 批一批（batch 個）到 pcp，再拿

   free order-0:
       丟回本 CPU pcp
       pcp 數量 > high？ ──是──► 倒一批（batch 個）回 buddy
```

pcp 也帶來一個「熱快取（cache-hot）」的紅利：你剛釋放的頁很可能還在 CPU cache 裡，馬上又配給同一顆 CPU，命中率高。這是 Ch 7 per-CPU 設計哲學在 mm 熱路徑上最典型的落地。`/proc/zoneinfo` 裡每個 zone 底下的 `pagesets` 就是這些 pcp。

## watermark 與 reclaim 觸發

zone 不能等到頁用光才開始回收——那時已經來不及，中斷處理常式（atomic context）配不到頁會直接出事。所以每個 zone 設三條**水位線（watermark）**（`struct zone` 的 `_watermark[]`，`include/linux/mmzone.h`）：

```
   空閒頁多  ┌─────────────── 充裕 ───────────────┐
            │  WMARK_HIGH  ← kswapd 回收到這裡就收工
            │  WMARK_LOW   ← 空閒頁跌破這裡：叫醒 kswapd 背景回收
            │  WMARK_MIN   ← 跌破這裡：配置者自己同步回收（direct reclaim）
   空閒頁少  └─ 低於 min ──► 只剩緊急保留，快 OOM ─┘
```

- 空閒頁 **> low**：太平，`get_page_from_freelist()`（`mm/page_alloc.c`）直接發頁。
- 跌破 **low**：喚醒 **kswapd**（每個 node 一條 kernel thread），在背景把頁換出／回收（接 Ch 22），目標是把空閒頁補回 **high** 以上。這是非同步的，配置者不必等。
- 跌破 **min**：情況緊急，配置的那個 task 自己下去做 **direct reclaim**——在配置路徑上同步回收，會卡住呼叫者。這就是「明明只是 `kmalloc` 卻莫名很慢」的一種來源。
- `WMARK_MIN` 以下還有一小塊**緊急保留**，只有帶 `__GFP_HIGH`／atomic context 的配置能動用；真的連這都不夠，`__alloc_pages_slowpath()` 最後會叫出 **OOM killer**（Ch 22）。

`get_page_from_freelist()` 對每個候選 zone 用 `zone_watermark_ok()` 檢查「扣掉這次配置後還在 watermark 之上嗎」，過了才發。這條 watermark 檢查是 **fast path**（`__alloc_pages()` 開頭）與 **slow path**（`__alloc_pages_slowpath()`，喚醒 kswapd、compaction、direct reclaim、OOM）的分水嶺。

把整條配置路徑串起來（接 Ch 6 的 `alloc_pages`）：

```
   alloc_pages(gfp, order)
      └─► __alloc_pages()                         mm/page_alloc.c
            ├─ get_page_from_freelist()  ← fast path
            │     每個 zone: zone_watermark_ok()?
            │        └─ rmqueue()  ← order-0 先試 pcp，否則 __rmqueue_smallest()/expand()
            └─ (fast path 失敗) __alloc_pages_slowpath()
                  喚醒 kswapd → compaction → direct reclaim → OOM
```

## 動手：觀察 buddy 的呼吸

### 讀 /proc/buddyinfo

這個檔直接把每個 zone 的 `free_area` 印出來，一欄一個 order：

```
$ cat /proc/buddyinfo
Node 0, zone   DMA      1    1    1    0    2    1    1    0    1    1    3
Node 0, zone   DMA32  120  200  180   90   40   20   10    5    3    1    8
Node 0, zone  Normal  340  510  220  110   55   30   18    9    4    2   12
                       ▲    ▲    ▲                              ...     ▲
                    order0 order1 order2                            order10
```

每個數字是那個 zone 那個 order 現有幾個空閒塊，對應 `free_area[order].nr_free`。左邊（小 order）數字大代表小塊多、可能碎片化；右邊（大 order）還有數字代表大連續塊還湊得出來。跑一陣子的機器右側常會歸零，這就是碎片化的直接證據。

### 讀 /proc/zoneinfo

看 zone 的全貌，尤其 watermark 和 pcp：

```
$ grep -A20 "zone   Normal" /proc/zoneinfo
Node 0, zone   Normal
  pages free     420531
        min      11719      ← WMARK_MIN
        low      14648      ← WMARK_LOW
        high     17578      ← WMARK_HIGH
        ...
  pagesets                  ← 這底下就是 per-CPU pages（pcp）
    cpu: 0
      count: 183            ← 這顆 CPU pcp 目前快取幾頁
      high:  186
      batch: 63             ← 一次和 buddy 批發/倒回 63 頁
```

把 `pages free` 和三條 watermark 對照，就能判斷這個 zone 現在是太平、該叫 kswapd、還是快 OOM 了。

### 寫模組：alloc_pages 各 order，看 buddyinfo 變化

最能建立體感的實驗——自己配一大塊，看 `/proc/buddyinfo` 少一塊、放掉再長回來：

```c
// buddy_probe.c —— 配置不同 order 的頁，觀察對 buddyinfo 的影響
#include <linux/init.h>
#include <linux/module.h>
#include <linux/gfp.h>
#include <linux/mm.h>

static int order = 4;                 // 預設配 order-4（16 頁 = 64 KB）
module_param(order, int, 0644);
MODULE_PARM_DESC(order, "配置的 order（0~10）");

static struct page *pg;

static int __init bp_init(void)
{
    unsigned long pfn;

    // GFP_KERNEL：可睡眠、可觸發 reclaim 的一般 kernel 配置
    pg = alloc_pages(GFP_KERNEL, order);
    if (!pg) {
        pr_err("buddy_probe: alloc_pages order=%d 失敗\n", order);
        return -ENOMEM;
    }

    pfn = page_to_pfn(pg);
    pr_info("buddy_probe: 配到 order=%d，%lu 頁，起始 pfn=%lu, 實體位址=0x%llx\n",
            order, 1UL << order, pfn, (unsigned long long)page_to_phys(pg));
    pr_info("buddy_probe: 現在去看 /proc/buddyinfo，order=%d 那欄應該少了（或引發 split）\n",
            order);
    return 0;
}

static void __exit bp_exit(void)
{
    __free_pages(pg, order);          // 還回去，觸發 buddy 合併
    pr_info("buddy_probe: 已 free order=%d，去看 buddyinfo 應該長回來（或合併升級）\n",
            order);
}

module_init(bp_init);
module_exit(bp_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("觀察 buddy allocator 對 buddyinfo 的影響");
```

實驗流程（在 Ch 0 的 QEMU 環境或任何測試機）：

```bash
# 載入前先拍一張 buddyinfo
cat /proc/buddyinfo

insmod buddy_probe.ko order=8      # 配一個 order-8（256 頁 = 1 MB）大塊
cat /proc/buddyinfo                # 對照：order-8（或更高被切）那欄變動
dmesg | tail -3                    # 看印出的 pfn / 實體位址

rmmod buddy_probe                  # 釋放，觸發合併
cat /proc/buddyinfo                # 對照：大塊長回來
```

觀察重點：配 order-8 時如果該 order 剛好有貨，`/proc/buddyinfo` 的 order-8 欄會 −1；如果沒貨，你會看到更高 order −1、而中間幾個 order +1（這就是 `expand()` 的 split 把切剩的半塊掛回各層）。`rmmod` 釋放後如果兄弟空閒，會看到低 order 減少、高 order 增加（合併升級）。你親眼看到了 split 與 merge。

> 進階玩法：跑一個迴圈連續 `insmod`／`rmmod` 不同 order，或改成一次配置多塊再交錯釋放，人為製造碎片，看 `/proc/buddyinfo` 右側大 order 怎麼歸零——你正在手動重現外部碎片化。

## 對比與取捨

| 配置策略 | 找連續塊 | 抗外部碎片 | 內部碎片 | kernel 用在哪 |
|---|---|---|---|---|
| 純 free list | 慢（要掃湊） | 差（不主動合併） | 無 | （不用） |
| **buddy** | 快（按 order 分組） | 好（釋放即合併） | 有（只能 2^N，要 5 頁得配 8 頁） | 實體頁配置的根 |
| slab/slub（Ch 18） | — | — | 小（物件緊密排） | 小物件、固定大小結構 |
| bitmap allocator | O(n) 掃 | 差 | 無 | 早期／簡單場景 |

buddy 的代價是**內部碎片（internal fragmentation）**：因為只能配 2^N 頁，你要 5 頁它得給你 8 頁（order-3），浪費 3 頁。這是它換來「快速找連續塊 + 主動抗外部碎片」付的稅。所以 kernel 不直接拿 buddy 給小配置——那樣浪費太大——而是讓 **slab/slub（Ch 18）從 buddy 批發整頁、再把頁切成密排的小物件零售**。buddy 管「頁級批發」，slab 管「物件級零售」，分工明確。

## 踩雷集錦

1. **`MAX_ORDER` 在 6.x 改名又改語意**：舊 kernel（≤6.7 左右）`MAX_ORDER` 的值是「order 的**數量**」（11），迴圈寫 `for (order = 0; order < MAX_ORDER; order++)`。6.x 改成 `MAX_PAGE_ORDER`（值 10，是「**最大** order」）並新增 `NR_PAGE_ORDERS`（= 11，數量）。你拿舊教材／舊 patch 對 6.12 源碼會對不上，且邊界差一。以 6.12 的 `include/linux/mmzone.h` 為準。

2. **以為 buddy 給你的是「歸零」或「連續虛擬位址」的記憶體**：`alloc_pages()` 回的是 `struct page *`，內容**未清零**（要清零用 `__GFP_ZERO` 或 `get_zeroed_page()`）。而且它保證的是**實體連續**，你要虛擬位址得自己 `page_address()`／`kmap`。想要虛擬連續但實體不必連續，那是 `vmalloc`（Ch 6），走的機制不同。

3. **把 order 當成「頁數」傳**：`alloc_pages(gfp, 4)` 是 **order-4 = 16 頁**，不是 4 頁。要 4 頁應該傳 order-2。這個一時眼花會多配 4 倍記憶體。`get_order(size)` 幫你把 byte 數換成 order。

4. **在 atomic context 用 `GFP_KERNEL` 配置**：`GFP_KERNEL` 允許睡眠、允許觸發 direct reclaim（會睡）。在中斷處理常式、持 spinlock 時這麼配會踩「scheduling while atomic」。那些場景要用 `GFP_ATOMIC`——它不睡、只從緊急保留拿，但更容易失敗（接 Ch 6、Ch 25）。

5. **看到 `/proc/buddyinfo` 小 order 一堆、大 order 全 0 就以為記憶體不夠**：這是**碎片化**不是**用光**。空閒頁總量可能還很多，只是湊不出大連續塊。這時 `alloc_pages` 要高 order 會失敗或觸發 compaction，但 `free` 顯示還有很多可用記憶體——這正是「記憶體夠卻配不到大塊」的經典現象，理解 buddy 才能診斷它。

## 進階：再往深一層

- **compound page 與 folio（6.x 的新抽象）**：一組連續頁若當成「一個大單位」用（大頁、slab、hugetlb），過去用 **compound page**——head page 記整組大小、tail page 指回 head，靠 `struct page` 的 flag 標記。但 `struct page` 到底是「單頁」還是「一組頁的 head」長期混淆、bug 溫床。6.x 引入 **folio**（`struct folio`，`include/linux/mm_types.h`）：型別上就是「一個 head page，保證不是 tail page」，把「一組連續頁」變成一等公民。很多 mm API 正逐步從 `struct page *` 遷到 `struct folio *`（page cache、reclaim 先行）。你現在只要知道：folio ≈「型別安全版的 compound page」，動機是消滅 head/tail 混淆，不是新的配置演算法。buddy 底層依舊按 order 發連續頁，folio 是上層怎麼「看待」這組頁。（LWN 對 folio 有一整系列文章，見延伸閱讀。）

- **`__alloc_pages_slowpath()` 的階梯**：fast path 失敗後不是直接 OOM，而是一級級加碼——喚醒 kswapd、嘗試 memory compaction（把 movable 頁搬走騰大塊）、direct reclaim（同步回收）、最後才 OOM。面試常問「配置一頁最壞會發生什麼」，答案就是這條階梯（`mm/page_alloc.c`）。

- **`alloc_pages` 為什麼要 zone fallback**：`GFP_KERNEL` 從 ZONE_NORMAL 開始，不夠會 fallback 到 ZONE_DMA32／DMA（zonelist，`pg_data_t` 的 `node_zonelists`）。反過來 `GFP_DMA` 只能待在 ZONE_DMA——低位記憶體稀缺，濫用 `GFP_DMA` 會把珍貴的 DMA zone 榨乾，害真正需要 DMA 的裝置配不到（接 Ch 41）。

- **CMA（Contiguous Memory Allocator）**：buddy 的 order 上限 10（4 MB），但某些裝置（相機、GPU）要幾十 MB 的實體連續記憶體。CMA 預留一塊標記為 `MIGRATE_CMA` 的區域，平時可借給 movable 配置，需要時把裡面的頁搬走騰出大連續塊。這是 migratetype 分類的一個殺手級應用，韌體工程師（你的職涯線）常會碰到。

## 動手練習

1. **手算 buddy**：假設一個 zone 只有 pfn 0~15（16 頁）全空。依序配置：order-0、order-2、order-1，各回傳哪些 pfn？畫出每步後 `free_area[0..4]` 的狀態。然後把 order-2 那塊放回，說出會不會觸發合併、合併到哪個 order。（提示：`buddy_pfn = pfn ^ (1<<order)`，先確認對齊。）

2. **gdb 停在配置路徑**：照 Ch 0 起 QEMU + gdb，`break __rmqueue_smallest`，在 QEMU 裡 `insmod` 上面的 `buddy_probe.ko`，`backtrace` 看 `alloc_pages` → `__alloc_pages` → `get_page_from_freelist` → `rmqueue` → `__rmqueue_smallest` 整條呼叫鏈。`print order` 確認你要的 order。再 `break expand` 看有沒有觸發 split。

3. **製造並觀察碎片**：寫一個模組（或改 `buddy_probe`）配置大量 order-0 頁、交錯釋放一半，反覆做幾輪，每輪 dump 一次 `/proc/buddyinfo`。目標是看到高 order 欄位逐漸歸零。這讓你親手把一個健康 zone 「碎」掉。

4. **驗證 XOR 找 buddy**：在模組裡 `alloc_pages(GFP_KERNEL, 1)` 拿一個 order-1 塊，印出 `page_to_pfn(pg)` 和 `page_to_pfn(pg) ^ (1 << 1)`，確認後者確實是這塊 buddy 的起始 pfn（且與前者只差第 1 個 bit）。

## 本章重點整理

- **每個實體頁一個 `struct page`**（`include/linux/mm_types.h`），排成 `vmemmap` 陣列，索引是 pfn；page ↔ pfn ↔ 實體位址三者 O(1) 互換，是 buddy 位元運算的基礎。
- 頁按 **NUMA node（`pg_data_t`）→ zone（`struct zone`）** 分群；zone（DMA/DMA32/NORMAL/MOVABLE，HIGHMEM 是 32-bit 遺產）解決「某些頁只能給特定用途」的定址限制。**buddy 是每個 zone 一套**。
- **buddy allocator**（`mm/page_alloc.c`）把空閒頁按 order（2^0~2^10 頁）分組成 `free_area[]`：配置時 `__rmqueue_smallest()`／`expand()` 切半降級，釋放時 `__free_one_page()` 靠 `pfn ^ (1<<order)` 找兄弟、能合就往上合。這套「釋放即合併」是抗**外部碎片**的核心；代價是只能配 2^N 頁的**內部碎片**。
- **migratetype** 把頁按可搬移性分堆抗碎片、**pcp** 給 order-0 熱頁免鎖快取（接 Ch 7）、**watermark**（min/low/high）決定何時叫醒 kswapd 或做 direct reclaim（接 Ch 22）。

## 自我檢核

- [ ] 不看筆記，能畫出 `expand()` 把一個 order-4 塊切到 order-1 給你、切剩的塊掛回各層 free list 的過程
- [ ] 能解釋 `buddy_pfn = pfn ^ (1 << order)` 為什麼能 O(1) 找到兄弟，以及為什麼合併後起始 pfn 仍對齊
- [ ] 能說出 zone 存在的理由（至少講出 DMA 定址限制這個原因），以及為什麼 HIGHMEM 在 x86_64 是空的
- [ ] 面試被問「free 顯示記憶體還很多，為什麼 `alloc_pages` 高 order 會失敗」，你能用外部碎片 + buddy free_area 解釋
- [ ] 能說出 migratetype、pcp、watermark 各自解決什麼問題，並指出它們分別接到本課哪一章
- [ ] 能寫一個模組配置指定 order 的頁，並在 `/proc/buddyinfo` 看到對應變化

## 延伸閱讀

### 官方文件與源碼

- **`mm/page_alloc.c`（v6.12）** — [Bootlin elixir](https://elixir.bootlin.com/linux/v6.12/source/mm/page_alloc.c)
  - **讀哪裡**：`__alloc_pages`、`get_page_from_freelist`、`rmqueue`、`__rmqueue_smallest`、`expand`、`__free_one_page` 這幾個函式。配 gdb 停在 `__rmqueue_smallest`／`expand` 對照著讀，split/merge 邏輯會立刻清楚
  - **前提**：跟完本章、Ch 0 的 gdb 環境

- **`include/linux/mmzone.h`（v6.12）** — [Bootlin elixir](https://elixir.bootlin.com/linux/v6.12/source/include/linux/mmzone.h)
  - **讀哪裡**：`struct zone`、`struct free_area`、`struct per_cpu_pages`、`enum zone_type`、`enum migratetype`、`NR_PAGE_ORDERS`/`MAX_PAGE_ORDER`
  - **能學到什麼**：本章講的所有結構的第一手定義；zone、free_area、pcp、watermark 全在這一個檔

- **[Documentation/mm/physical_memory.rst](https://www.kernel.org/doc/html/latest/mm/physical_memory.html)** — kernel 官方
  - **讀哪裡**：node/zone/page 的組織那幾節，是設計者對本章前半的權威敘述
  - **和本章關聯**：補足本章為篇幅省略的 zone 初始化、zonelist 細節

### 文章 / 書

- **[LWN：Folios 系列](https://lwn.net/Articles/849538/)** — Matthew Wilcox / Jonathan Corbet
  - **讀哪裡**："A memory-folio update" 及後續幾篇；想懂 folio 為何引入、和 compound page 的關係，這是一手解說
  - **前提**：知道 compound page 概念（本章進階節有簡介）

- **《Understanding the Linux Virtual Memory Manager》** — Mel Gorman（2004）
  - **這本書的定位**：mm 子系統最深的專著；第 6 章「Physical Page Allocation」把 buddy 的每個函式逐行拆開
  - **注意**：對應舊 kernel（2.6），`MAX_ORDER` 語意、folio 等都還沒有，讀「演算法骨架」為主，細節以 6.12 源碼校正

- **《Linux Kernel Development, 3rd Ed.》** — Robert Love，第 12 章「Memory Management」
  - **讀哪裡**：Pages / Zones / `alloc_pages` 系列 / slab 的白話介紹，是本章與 Ch 18 的最佳輕量補充
  - **注意**：舊 kernel，概念不過時但函式細節以 6.12 為準

buddy 把「整頁」批發給你了，但 kernel 裡絕大多數配置是小物件（幾十 bytes 的 `task_struct` 欄位、`dentry`、網路封包描述符），一頁一頁拿太浪費。下一章我們看 **slab/slub**：它怎麼從 buddy 拿整頁、再切成密排的小物件零售，把內部碎片壓到最小。

→ [Ch 18 slab/slub allocator 內部](./18-slub-allocator.md)
