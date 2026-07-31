# Ch 16 — 虛擬位址空間與 page table walk

> **目標**：看懂一個虛擬位址（virtual address / VA）怎麼被硬體 MMU 一級一級拆開、走過多級 page table、最後落到某個實體頁（physical page）；看懂 kernel 自己的位址空間佈局（哪裡放 direct map、哪裡放 vmalloc）；能用 `/proc/<pid>/maps`、`/proc/<pid>/pagemap` 和 gdb 手動走一遍 page table，把 VA→PA 的翻譯親眼算出來。x86_64 與 ARM64 並重——這是全課少數兩個架構都要拆到底的章節。

這章是 Part 3（記憶體管理）的地基。後面每一章——buddy（Ch 17）、slub（Ch 18）、VMA 與 page fault（Ch 19）、demand paging 與 CoW（Ch 20）、TLB（Ch 23）——都建立在「VA 怎麼變成 PA」這個機制上。如果這章的 page table walk 你腦中畫不出來，後面全部都是空中樓閣。

## 為什麼需要這個？

先問一個最基本的問題：你的程式裡有一個指標 `char *p = (char *)0x400000;`，CPU 執行 `*p` 時，`0x400000` 這個數字是什麼？

它**不是**某條記憶體匯流排上的實體位址。它是一個**虛擬位址**——一個每個 process 都能用、但意義各自不同的號碼。你的 process 的 `0x400000` 和隔壁 process 的 `0x400000` 指向完全不同的實體記憶體。中間那層把「process 眼中的位址」翻譯成「DRAM 上的位址」的機制，就是**虛擬記憶體（virtual memory）**，而執行翻譯的是 CPU 裡的 **MMU（Memory Management Unit）**，翻譯的依據是 kernel 幫每個 process 準備好的 **page table（分頁表）**。

沒有這層翻譯會怎樣？回想 8086 那個年代——所有程式共用同一個實體位址空間，A 程式一個野指標就能踩爛 B 程式甚至作業系統。虛擬記憶體一次解決了三個問題：

- **隔離（isolation）**：每個 process 有自己的位址空間，彼此看不到、踩不到。這是 process 之間的一道牆
- **抽象（abstraction）**：程式可以假設自己獨佔一大片連續位址（例如 128 TB），不用管實體 RAM 只有 16 GB、也不用管自己被載到 DRAM 的哪個角落
- **超額配置（over-commit）與 demand paging**：VA 可以先不對應任何實體頁，等你真的碰它時才分配（Ch 19/20）；用不到的頁可以換出到 swap（Ch 22）

代價是：每一次記憶體存取都要先翻譯。硬體用 MMU + TLB（Ch 23）把這個代價壓到近乎零，但翻譯的「規則表」——page table——是 kernel 的責任。這章就是講這張表長什麼樣、硬體怎麼查它、kernel 怎麼建它與走它。

## 先建立直覺

翻譯的核心想法：**別用一張大表，用一棵樹。**

最笨的做法是一張巨大的查找表，每個虛擬頁一筆，記著它對應哪個實體頁。64-bit 位址、4 KB 一頁，那是 $2^{52}$ 筆——光是表本身就塞不下任何記憶體。行不通。

真正的做法是**多級（multi-level）稀疏的樹**。把虛擬位址切成好幾段，每一段當作一級查找表的索引，一級指向下一級，最後一級才指向實體頁。樹的好處是**稀疏**：你沒用到的位址區間，對應的整棵子樹根本不用建，那筆上層 entry 留空即可。一個只用了幾 MB 的 process，page table 本身可能只佔幾十 KB。

x86_64 用 48-bit VA（預設）時是**四級**樹。把 48 位這樣切：

```
  63           48 47      39 38      30 29      21 20      12 11         0
  ┌─────────────┬──────────┬──────────┬──────────┬──────────┬───────────┐
  │ sign-extend │  PGD idx │  PUD idx │  PMD idx │  PTE idx │  page off │
  │  (bit 47 的  │  9 bits  │  9 bits  │  9 bits  │  9 bits  │  12 bits  │
  │   複製)      │  0..511  │  0..511  │  0..511  │  0..511  │  0..4095  │
  └─────────────┴──────────┴──────────┴──────────┴──────────┴───────────┘
        │            │          │          │          │           │
        │            │          │          │          │           └─► 頁內偏移（4 KB = 2^12）
        │            │          │          │          └─► 第 4 級表（PTE）的第幾筆
        │            │          │          └─► 第 3 級表（PMD）的第幾筆
        │            │          └─► 第 2 級表（PUD）的第幾筆
        │            └─► 第 1 級表（PGD）的第幾筆
        └─► 高 16 位必須是 bit47 的符號延伸，否則是 non-canonical 位址（硬體拒絕）
```

四段索引各 9 bits（因為一個表一頁 4 KB，一筆 entry 8 bytes，剛好 512 = $2^9$ 筆），加上最後 12 bits 的頁內偏移，$9 \times 4 + 12 = 48$。這不是巧合——**位址位數、頁大小、entry 大小三者互相決定**。改頁大小或改位址寬度，切法就變（ARM64 的 16K/64K granule 就是這樣算出不同的切法）。

多級樹省多少空間，算一筆帳就懂：一個 process 只用了 4 KB 的 heap + 4 KB 的 stack + 幾頁 text，共約 8 個頁散在不同區間。單一大表要 $2^{36}$ 筆（48 位 VA 去掉 12 位 offset）× 8 bytes = 512 GB，荒謬。多級樹呢？頂層 PGD 一頁（4 KB），每碰到一個有東西的位址區間才往下長一條路徑——最壞每個用到的頁各獨佔一條 PGD→PUD→PMD→PTE 路徑（4 頁），8 個頁也才幾十 KB。**用到才長**，這就是稀疏樹的全部好處。真實 process 的頁通常聚在一起，共用上層路徑，實際更省。

四級表的名字（Linux 的術語，由上而下）：

| 級 | Linux 術語 | 全名 | x86 硬體名 | 一筆管多大位址 |
|---|---|---|---|---|
| 1（最上層） | PGD | Page Global Directory | PML4 | 512 GB |
| 2 | PUD | Page Upper Directory | PDPT | 1 GB |
| 3 | PMD | Page Middle Directory | PD | 2 MB |
| 4（最下層） | PTE | Page Table Entry | PT | 4 KB |

（5 級模式多一層 P4D 夾在 PGD 和 PUD 之間，等一下講。）

記住這個對照：**同一個東西，Linux 給它中性名字（PGD/PUD/PMD/PTE），x86 硬體手冊給另一組名字（PML4/PDPT/PD/PT），ARM64 又叫 level 0/1/2/3。** 三套術語講的是同一棵樹的同一級。Linux 選中性名字，是因為它要用同一套 `pgd_offset`/`pud_offset`/... 巨集同時支援 x86、ARM64、RISC-V 等所有架構。

## kernel 的虛擬位址空間佈局

先看整片 64-bit 位址空間怎麼分。48-bit VA 下，canonical 位址被劈成兩半，中間一大塊 non-canonical 的洞：

```
  x86_64, 48-bit VA（4 級 page table）位址空間佈局：

  0x0000_0000_0000_0000  ┌────────────────────────────────┐  ← user space 起點
                         │                                │
                         │   使用者空間（每個 process 不同）  │  128 TB
                         │   text/data/heap/mmap/stack     │
                         │                                │
  0x0000_7fff_ffff_ffff  └────────────────────────────────┘  ← user space 頂（TASK_SIZE 附近）
                         ╔════════════════════════════════╗
                         ║   non-canonical 巨洞（硬體禁用）  ║  約 16 EB 的洞
                         ╚════════════════════════════════╝
  0xffff_8000_0000_0000  ┌────────────────────────────────┐  ← kernel space 起點（高半部）
                         │   direct map / physmap          │  最大 64 TB
                         │   （所有實體 RAM 的線性映射）      │  page_offset_base
                         ├────────────────────────────────┤
                         │   vmalloc / ioremap area        │  32 TB
                         ├────────────────────────────────┤
                         │   vmemmap（struct page 陣列）     │  1 TB
                         ├────────────────────────────────┤
                         │   ... KASAN shadow、cpu_entry... │
                         ├────────────────────────────────┤
                         │   kernel text 映射、modules      │
                         │   fixmap（固定映射區）            │
  0xffff_ffff_ffff_ffff  └────────────────────────────────┘
```

（權威版本在 `Documentation/arch/x86/x86_64/mm.rst`，各區的實際基底位址在 `arch/x86/include/asm/pgtable_64_types.h`，別背數字、要會查。）

幾個關鍵區，每一個對應後面某一章：

- **user space（低半部，0 ~ 0x7fff_ffff_ffff）**：128 TB，每個 process 各自獨立。程式的 text/data/heap/mmap/stack 都在這。這片的映射由 `mm_struct` 裡的 VMA 描述（Ch 19）。
- **direct map / physmap（`page_offset_base` 起）**：把**全部**實體 RAM 一對一線性映射到 kernel 空間的一段連續 VA。這是 kernel 最常用的一塊——`kmalloc` 拿到的位址、`__va()`/`__pa()` 互轉走的就是這裡。有了它，kernel 想碰任何一個實體位址，加個固定 offset（`PAGE_OFFSET`）就有對應的 VA，不必為每塊實體記憶體另外建映射。這是 Ch 6/17 的地基。
- **vmalloc area**：`vmalloc()` 配置的**虛擬上連續、實體上零散**的記憶體落在這（Ch 6）。它需要獨立一區，因為它的 VA 和 PA 沒有固定 offset 關係，得逐頁建 page table。
- **vmemmap**：kernel 為每個實體頁框（page frame）維護一個 `struct page`（Ch 17）。這些 `struct page` 排成一個大陣列放在 vmemmap 區，讓「PFN → struct page」變成單純的陣列索引。
- **fixmap（固定映射區）**：一組編譯期就決定 VA、開機早期就要用的特殊映射（例如早期 console、APIC 暫存器）。

> **關鍵事實**：kernel 空間（高半部）的映射，**每個 process 的 page table 都一樣**。稍後「kernel 高半部映射為何共享」一節專門講這件事——它是 syscall 能快速進 kernel 的前提，也是 Meltdown（KPTI）攻防的戰場。

> **KASLR 的一環**：Ch 0 為了 gdb 好用關掉了 `RANDOMIZE_BASE`。正式環境開著時，它不只隨機化 kernel text 的載入位址，還隨機化 direct map、vmalloc、vmemmap 這幾區的**基底**（`page_offset_base` 等變數在開機時被加一個隨機 offset，`arch/x86/mm/kaslr.c`）。這就是為什麼你在 `kernel_pwn` 課裡，光知道一個 kernel 指標還不夠，得先洩漏出 `page_offset_base` 才能把任意 kernel 指標換算成 direct map 位置。攻防雙方都在這張佈局圖上打。

## 多級 page table：一個 VA 怎麼被拆開

把上面的直覺落到 code。給一個虛擬位址 `va`，Linux 用一組巨集逐級取出索引。以 x86_64 為例，定義在 `arch/x86/include/asm/pgtable.h` 與 `arch/x86/include/asm/pgtable_types.h`：

- `pgd_index(va)` = `(va >> PGDIR_SHIFT) & (PTRS_PER_PGD - 1)` —— `PGDIR_SHIFT = 39`，`PTRS_PER_PGD = 512`
- `pud_index(va)` = `(va >> PUD_SHIFT) & (PTRS_PER_PUD - 1)` —— `PUD_SHIFT = 30`
- `pmd_index(va)` = `(va >> PMD_SHIFT) & (PTRS_PER_PMD - 1)` —— `PMD_SHIFT = 21`
- `pte_index(va)` = `(va >> PAGE_SHIFT) & (PTRS_PER_PTE - 1)` —— `PAGE_SHIFT = 12`

就是把 VA 右移到那一級的位置、再遮罩出低 9 位。這四個 shift 常數（39/30/21/12）你只要記住「每級差 9、最低是 12（4 KB 頁）」就能推出來。

而**走一級**的動作是這組 `*_offset` 巨集，它們把「上一級的 entry」+「這一級的索引」算出「這一級 entry 的位址」：

```c
/* 概念形狀，實際定義見 arch/x86/include/asm/pgtable.h 與 include/linux/pgtable.h */
pgd = pgd_offset(mm, va);        /* 從 mm->pgd 取 PGD 這一級的 entry */
p4d = p4d_offset(pgd, va);       /* 4 級模式下 P4D 被 folded，這步是 no-op */
pud = pud_offset(p4d, va);       /* 用 pgd/p4d entry 指到的 PUD 表 + pud_index */
pmd = pmd_offset(pud, va);       /* 再往下到 PMD 表 */
pte = pte_offset_kernel(pmd, va);/* 最後一級，取出 PTE */
```

`pgd_offset(mm, va)` 展開約是 `(mm)->pgd + pgd_index(va)`——`mm->pgd` 是這個 process 的頂層表基底（`struct mm_struct` 的欄位，Ch 19），加上索引就是那一筆 PGD entry 的 VA。往下每一級，`pud_offset(pgd, va)` 要先從 `*pgd` 這筆 entry 裡取出「下一級表的實體位址」，透過 direct map 轉成 VA，再加 `pud_index(va)`。一級一級，最後 `pte_offset_kernel` 給你那個 PTE，`*pte` 裡就有目標實體頁的頁框號 + flags。

**5 級 page table（LA57，57-bit VA）** 就是在 PGD 和 PUD 之間多插一級 P4D，讓 user space 從 128 TB 擴到 128 PB。切法變成 $9 \times 5 + 12 = 57$。Linux 的巧妙之處：4 級模式下 P4D 那一級被**摺疊（folded）**——`p4d_offset` 直接回傳傳進去的 pgd 指標，等於 no-op，一份程式碼同時支援 4 級和 5 級。5 級是否啟用由開機時 CPU 能力 + `CONFIG_X86_5LEVEL` 決定，多數桌面/伺服器現在仍跑 4 級。這也是為什麼 Linux 早在硬體出來前就把 `p4d` 那一層加進通用巨集——為了未來擴展不用再改一次全樹。

## 底層機制：MMU 硬體怎麼走這棵樹

上面是 kernel **軟體**走 page table（為了讀/改映射）。但每次記憶體存取時，**硬體 MMU** 自己也在走同一棵樹，而且沒有那些巨集——它直接讀實體記憶體。走的起點是 **CR3**（x86）這個暫存器，裡面存著當前 process 的頂層表（PGD/PML4）的**實體位址**。

一次完整的 walk（TLB miss 時）：

```
  CPU 要存取 VA = 0x0000_5555_5555_6789（假設 48-bit，4 級）

  拆位：  PGD=0b010101010=170  PUD=...  PMD=...  PTE=...  off=0x789

  ┌──────┐  讀 CR3 → PGD 表實體基底
  │ CR3  │───────────────┐
  └──────┘               ▼
                  ┌──────────────┐  index=170
                  │  PGD (PML4)  │──┐ entry 內含 PUD 表的 PFN + flags
                  │  512 entries │  │
                  └──────────────┘  ▼
                          ┌──────────────┐  index=PUD_idx
                          │  PUD (PDPT)  │──┐
                          └──────────────┘  ▼
                                  ┌──────────────┐  index=PMD_idx
                                  │  PMD  (PD)   │──┐
                                  └──────────────┘  ▼
                                          ┌──────────────┐  index=PTE_idx
                                          │  PTE  (PT)   │──┐  取出目標頁 PFN
                                          └──────────────┘  ▼
                                                  ┌──────────────────────┐
                                                  │ 實體頁基底 (PFN<<12)   │ + off(0x789)
                                                  │  = 最終實體位址        │
                                                  └──────────────────────┘
```

重點觀察：

- **一次 walk = 4 次記憶體讀取**（4 級模式；5 級是 5 次），才換到 1 次真正的資料存取。這很貴，所以有 **TLB（Translation Lookaside Buffer）** 快取「VA 頁 → PA 頁」的結果，命中就跳過整個 walk（Ch 23 專講）。MMU 只在 TLB miss 時才走這棵樹（x86 是硬體自動走，稱 hardware page table walker；有些架構如舊 MIPS 是軟體填 TLB）。
- **每一級 entry 裡存的是下一級表的實體位址（PFN），不是 VA**。因為 MMU 在做的正是「把 VA 翻成 PA」，它不能依賴 VA。這也是為什麼 CR3 存的是實體位址。
- **中途任一級 entry 的 present bit = 0**，walk 立刻中止，MMU 觸發 **page fault**（#PF），控制權交給 kernel 的 fault handler（Ch 19）。Demand paging、CoW、swap-in 全靠這個中止機制。

**huge page**：如果 PMD 這一級的 entry 設了 **PS（Page Size）bit**，walk 就**在 PMD 停住**——這筆 entry 不指向 PTE 表，而是直接指向一個 **2 MB 的大頁**，低 21 位全當偏移。同理 PUD 級設 PS bit = **1 GB 大頁**。好處是省一級 walk、而且一筆 TLB entry 覆蓋 2 MB/1 GB，大幅降低 TLB miss（THP、hugetlbfs 就靠這個，Ch 23 深入）。壞處是內部碎片與 CoW 粒度變粗。

## page table entry：那 64 bits 裡有什麼

一筆 PTE 是 64 bits，一部分是實體頁框號（PFN，通常 bit 12~51），其餘是 flags。x86_64 的關鍵 flag（定義在 `arch/x86/include/asm/pgtable_types.h`，`_PAGE_*`）：

| bit | flag | 意義 | 誰設它、誰讀它 |
|---|---|---|---|
| 0 | `_PAGE_PRESENT` | 這筆映射有效嗎 | 清 0 → walk 中止觸發 #PF（demand paging/swap 的關鍵） |
| 1 | `_PAGE_RW` | 可寫嗎（0 = 唯讀） | CoW 靠把它清 0：寫唯讀頁 → #PF → kernel 複製（Ch 20） |
| 2 | `_PAGE_USER` | user 模式可存取嗎 | 0 = 只有 kernel（ring 0）能碰。kernel 高半部映射就靠這位擋住 user |
| 5 | `_PAGE_ACCESSED` | 被存取過（MMU 自動設 1） | reclaim 掃它決定哪些頁「冷」可以換出（Ch 22） |
| 6 | `_PAGE_DIRTY` | 被寫過（MMU 自動設 1） | writeback 靠它知道哪些 page cache 頁要寫回磁碟（Ch 21） |
| 7 | `_PAGE_PSE` | huge page（PMD/PUD 級） | 設 1 → walk 在這級停住，直接是大頁 |
| 63 | `_PAGE_NX` | No-eXecute（不可執行） | data/stack 頁設 1，擋 shellcode 執行（W^X）。kernel_pwn 課裡繞的就是它 |

`_PAGE_ACCESSED` 和 `_PAGE_DIRTY` 是**硬體幫你設的**——MMU 在 walk 命中時自動把 accessed 設 1，寫入時把 dirty 設 1。kernel 只負責讀它們、必要時清 0。這是硬體與軟體協作的漂亮例子：reclaim 演算法（Ch 22）不需要攔截每次存取，只要週期性掃 accessed bit 就能近似 LRU。

## PFN、struct page 與 direct map：走完表之後那個 PA 是什麼

walk 的終點是一個 **PFN（Page Frame Number，實體頁框號）**——實體位址除以頁大小。但 kernel 管理實體記憶體時，很少直接拿 PFN 這個裸數字辦事，它為**每一個實體頁框都配一個 `struct page`**（`include/linux/mm_types.h`，Ch 17 解剖）記著這頁的狀態（refcount、是否 dirty、屬於哪個 mapping、在哪個 LRU list）。於是有三個東西要能互轉：

```
   PFN  ◄──────►  struct page *  ◄──────►  kernel VA（direct map）
    │                  │                        │
 pte_pfn(pte)      pfn_to_page(pfn)          page_to_virt(page)
                   page_to_pfn(page)         virt_to_page(va)
```

- **PFN ↔ struct page**：靠 **vmemmap**。所有 `struct page` 排成一個大陣列放在 vmemmap 那一區（前面佈局圖），`pfn_to_page(pfn)` 幾乎就是 `vmemmap + pfn`——一個陣列索引。這就是為什麼 vmemmap 要獨立佔一大塊連續 VA：它讓「PFN → struct page」變成 O(1) 加法，而不用查表。
- **struct page ↔ kernel VA**：靠 **direct map**。`struct page` 描述的那個實體頁，在 direct map 上有一個固定 VA（`PAGE_OFFSET + phys`），`page_to_virt()`/`page_address()` 給你它。`kmalloc` 回傳的正是這種 direct-map VA。
- **VA ↔ PA**：direct map 上 `__pa(va) = va - PAGE_OFFSET`、`__va(pa) = pa + PAGE_OFFSET`，純加減法。**但只對 direct map 成立**——vmalloc/ioremap 的 VA 不能這樣算（見「進階」）。

把這串接起來：你走 page table 得到 PTE → `pte_pfn()` 拿 PFN → `pfn_to_page()`（走 vmemmap）拿 `struct page` → `page_to_virt()`（走 direct map）拿 kernel 能讀寫的 VA。**這條鏈是 mm 子系統每天在走的路**，後面 buddy（Ch 17）配置的就是 `struct page`、rmap（Ch 20）反查的就是「這個 page 被哪些 PTE 映射」。

## CR3 / TTBR：換一個暫存器就換整個位址空間

回到 Ch 14 的 context switch。當排程器決定從 process A 切到 B，除了換暫存器和 kernel stack，還得**換整個位址空間**——否則 B 會用 A 的 page table，看到 A 的記憶體。

做法極簡潔：**改 CR3，指向 B 的頂層 page table 實體位址。** 一個 `mov` 到 CR3，整棵翻譯樹就換了，A 的 user 映射瞬間全部失效、B 的全部生效。這發生在 `switch_mm_irqs_off()`（`arch/x86/mm/tlb.c`）裡，由 `context_switch()`（`kernel/sched/core.c`）呼叫——正是 Ch 14 那條路徑上的一步。

代價：換 CR3 通常**沖掉 TLB**（因為 TLB 裡快取的是舊 process 的翻譯，對新 process 是垃圾）。這很貴，所以 x86 引入 **PCID（Process Context ID）**：給 CR3 帶一個 ID tag，切回舊 process 時 TLB 裡它的 entry 還在，不必重填。ARM64 對應機制叫 **ASID（Address Space ID）**，放在 TTBR 裡。這是 Ch 23 的主題。

一個容易忽略但重要的細節：**切到 kernel thread（沒有自己的 user 位址空間）時不換 CR3**。kernel thread 的 `task_struct->mm` 是 NULL，它借用前一個 process 的位址空間（`active_mm`），因為它只碰 kernel 高半部（那半到處都一樣），根本不需要換頁表——省掉一次昂貴的 CR3 切換和 TLB 效應。這叫 **lazy TLB**，`switch_mm` 裡對 `mm == NULL` 有特判（Ch 14 的 `context_switch` 走 `enter_lazy_tlb` 那條）。「為什麼設計成這樣」的典型答案：能不換頁表就別換，TLB 太貴。

還有 **TLB shootdown**：如果一個 process 的頁表在多核上被多個 CPU 快取進各自的 TLB，而你改了（或撤掉）一筆映射，光沖自己這顆 CPU 的 TLB 不夠——得**通知其他 CPU 也沖掉那筆**（x86 靠 IPI，`flush_tlb_mm_range`；ARM64 有 `TLBI` 廣播指令，硬體幫你散）。這是 SMP（Ch 15）下修改 page table 的隱藏成本，unmap 大量記憶體慢，一部分就慢在這。Ch 23 專講。

**ARM64 的分割設計**比 x86 更乾淨：它有**兩個**頂層暫存器：

- **TTBR0_EL1** 管低位址（user space）——context switch 時換它
- **TTBR1_EL1** 管高位址（kernel space）——**開機設定後幾乎不動**

VA 的最高位決定用哪個 TTBR：高位是 0（0x0000...）走 TTBR0，高位是 1（0xffff...）走 TTBR1。硬體從架構層就把 user/kernel 分到兩個表根。x86 沒有這種分割，user 和 kernel 共用同一個 CR3 指向的表（靠 `_PAGE_USER` flag 區分權限）——這個差異正是下一節 KPTI 為什麼在 x86 上特別麻煩的伏筆。

## ARM64 的走表與 descriptor：同一棵樹，不同格式

ARM64 的 page table 概念上和 x86 一模一樣（多級稀疏樹、entry 存下一級 PFN、葉子可以在中間級停），但格式與可調參數不同，值得單獨拆一次——因為你的職涯線（MTK 韌體）幾乎都在 ARM 上跑。

**granule 決定切法。** ARM64 的頁大小叫 **translation granule**，可選 4 KB / 16 KB / 64 KB，由 `TCR_EL1`（Translation Control Register）設定。Linux 這邊對應 `CONFIG_ARM64_4K_PAGES` / `16K` / `64K`。granule 一改，每級索引的位元數就變：

- **4 KB granule**：一表 4 KB、一筆 descriptor 8 bytes → 512 筆 → 每級 9 bits。和 x86 一樣，48-bit VA 走 4 級（level 0/1/2/3 對應 Linux 的 PGD/PUD/PMD/PTE）。這是 Linux 桌面/伺服器與多數 Android 的預設。
- **64 KB granule**：一表 64 KB → 8192 筆 → 每級 13 bits。42-bit VA 只要 2 級、48-bit 也只要 3 級——**級數更少、walk 更淺、TLB 覆蓋更大**，代價是頁更大、內部碎片更多。某些伺服器/嵌入式場景會選它。
- **16 KB granule**：折衷，每級 11 bits，48-bit 走 4 級。Apple 生態偏好這個。

「頁大小 × entry 大小 × 位址寬度 三者互相決定切法」這句在「先建立直覺」埋的伏筆，ARM64 的可變 granule 是最乾淨的證明——同一個架構，換個 granule 就是一組全新的 shift 常數與級數。

**descriptor 格式不同。** ARM64 不叫 PTE flag，叫 **descriptor** 裡的欄位（Armv8-A 手冊 D5 章 / Linux 的 `arch/arm64/include/asm/pgtable-hwdef.h`，`PTE_*`）。對照 x86：

| 功能 | x86_64 flag | ARM64 descriptor 欄位 |
|---|---|---|
| present / 有效 | `_PAGE_PRESENT`（bit 0） | descriptor bit[0] valid + bit[1] 型別（table/block/page） |
| 可寫 | `_PAGE_RW` | `AP[2:1]`（access permission，含 read-only/EL 權限一起編碼） |
| user 可存取 | `_PAGE_USER` | `AP` 裡的 EL0 權限位 |
| 不可執行 | `_PAGE_NX`（bit 63） | **兩個** bit：`UXN`（EL0 不可執行）+ `PXN`（EL1/kernel 不可執行） |
| accessed | `_PAGE_ACCESSED` | `AF`（Access Flag，舊硬體要軟體管、Armv8.1 起可硬體自動設） |
| dirty | `_PAGE_DIRTY` | 靠 `DBM`（Dirty Bit Modifier）+ AP 做 software/hardware dirty |
| huge/大頁 | `_PAGE_PSE` | descriptor 型別填 **block**（level 1 = 1 GB、level 2 = 2 MB，4K granule） |

兩個特別要記：（a）ARM64 把「不可執行」拆成 **UXN + PXN** 兩位，能分別禁止 user 和 kernel 執行某頁——這比 x86 單一 NX 更細，SMEP/SMAP 那類「kernel 不准執行/存取 user 頁」的保護在 ARM64 是靠 PXN/PAN 做的。（b）ARM64 的 huge page 不叫「PS bit」，是把 descriptor 型別從 `table`（指向下一級）改成 `block`（直接是大頁）——語意一樣，走表在 block descriptor 停住，只是編碼方式不同。

**走表巨集完全共用。** 儘管格式差這麼多，你在 `mm/` 通用 code 看到的 `pgd_offset`/`pmd_leaf`/`pte_pfn` 在 ARM64 和 x86 是**同一份呼叫**——各架構在 `arch/arm64/include/asm/pgtable.h` 把這些巨集實作成解析自己的 descriptor 格式。這就是為什麼 Linux 能用一份 mm 通用邏輯同時服務兩個差這麼多的硬體。

## kernel 高半部映射為何共享，以及 KPTI 為何拆開它

**問題**：為什麼每個 process 的 page table 高半部（kernel 空間那半）都一模一樣？

因為 syscall 和中斷發生時，CPU 是在**當前 process 的 page table 上**進 kernel 的——它不會為了進 kernel 而先換 CR3。如果 kernel 的映射不在當前 page table 裡，進 kernel 的第一條指令就會 page fault，而 fault handler 本身也在 kernel……死循環。所以 kernel 必須**在每一個 process 的 page table 高半部都有一份完整映射**，這樣不論在哪個 process 上下文，一跳進 kernel（`_PAGE_USER=0` 擋住 user 直接碰）就能立刻執行。

Linux 的做法：`init_mm`（`mm/init-mm.c`）持有 kernel 那半的 master page table，每個新 process 建 page table 時，**把 kernel 高半部的 PGD entry 直接拷貝過來**（`arch/x86/mm/pgtable.c` 的相關流程，早期版本是 `clone_pgd_range`）。所以「共享」在實作上是「每個 process 的頂層表都指向同一批 kernel 中低層表」。

**這個共享正是 Meltdown（2018）的攻擊面。** Meltdown 利用 CPU 亂序執行的側通道，讓 user 態程式碼「偷看」到那些 `_PAGE_USER=0`、本該碰不到的 kernel 頁的內容——因為那些 kernel 映射就躺在同一張 page table 裡，權限檢查在亂序推測執行時來不及擋住。

緩解方案 **KPTI（Kernel Page-Table Isolation，`CONFIG_PAGE_TABLE_ISOLATION`）**：既然共享是禍根，那就**拆開**。KPTI 給每個 process 兩套 page table：

- **user 態跑的那套**：只映射 kernel 極少數必要的 trampoline（syscall/中斷入口那幾頁），其餘 kernel 空間**完全不映射**——偷看也偷不到，因為根本沒映射
- **kernel 態跑的那套**：完整 kernel 映射（和以前一樣）

進 kernel（syscall/中斷）時在 trampoline 裡切到 kernel 那套 CR3，返回 user 時切回 user 那套。代價是**每次系統呼叫多兩次 CR3 切換 + TLB 效應**，這是 Meltdown 緩解帶來的著名效能損失（syscall 密集的 workload 可見個位數到雙位數百分比退化，視 CPU 有無 PCID 而定）。

ARM64 的情況不同。它 TTBR0/TTBR1 天生分離，理論上 user 態跑時 TTBR1（kernel）根本沒被那條 walk 路徑用到——但實際上早期 Cortex 核心的推測執行一樣有變體漏洞（Spectre/Meltdown 家族的部分變體），所以 ARM64 也實作了 `kpti`（`arch/arm64/` 的 unmap-kernel-at-el0），只在**受影響的 CPU 型號**上開啟（`kpti` 可由 `arm64.nokpti` 關）。設計上比 x86 乾淨、代價相對小，因為它本來就有兩個表根、切 TTBR0 而非整個 CR3。判斷「這顆核心要不要開 kpti」的白名單在 kernel 的 CPU errata 機制裡——這是韌體/BSP 工程師會實際碰到的：換一顆 SoC，kpti 開不開由核心型號決定。

> 你在 `kernel_pwn` 課裡處理過的「KPTI trampoline」「切 CR3」，來源就在這。從防禦方看，它是硬體側通道逼出來的軟體隔離；從攻擊方看，它是你要繞過或利用的邊界。

## 動手：親眼把 VA 翻成 PA

三個層次的觀測，從使用者空間到 gdb 手動走表。

### 1. `/proc/<pid>/maps`：看 VA 佈局

```bash
cat /proc/self/maps
```

```
5555_55554000-5555_55555000 r-xp ... /bin/cat      ← text（可讀可執行）
5555_55755000-5555_55756000 r--p ...               ← rodata
5555_55756000-5555_55757000 rw-p ...               ← data/bss（可寫）
7ffff7...                    ...     /lib/.../libc  ← 共享庫
7ffff...                     rw-p    [stack]        ← 使用者堆疊
```

每一行是一個 **VMA（Ch 19）**，`r-xp`/`rw-p` 對應到 PTE 的 `_PAGE_RW`/`_PAGE_NX`。注意所有位址都在低半部（user space），最高位是 0，符合我們前面的佈局圖。想看更細的每頁狀態（present/swapped/dirty、是否 huge），讀 `/proc/<pid>/smaps` 和 kernel 附的 `tools/vm/page-types`（能統計整台機器的頁框依 flag 分類）。

### 2. `/proc/<pid>/pagemap`：查 VA → PA（PFN）

`pagemap` 讓 user 態能查某個 VA 對應的實體頁框號（每個 VA 頁一筆 8-byte entry）：

```c
/* 概念：讀 /proc/self/pagemap 的 (vaddr/PAGE_SIZE)*8 偏移處那 8 bytes */
uint64_t offset = (vaddr / 4096) * 8;
pread(fd, &entry, 8, offset);
if (entry & (1ULL << 63)) {          /* bit63 = present */
    uint64_t pfn = entry & ((1ULL << 55) - 1);   /* bit0..54 = PFN */
    uint64_t phys = pfn * 4096 + (vaddr & 0xfff);
    printf("VA %#lx -> PA %#lx\n", vaddr, phys);
}
```

（新 kernel 需 `CAP_SYS_ADMIN` 才看得到真實 PFN，否則出於 Rowhammer/側通道防護會回傳 0——這本身就是安全考量的例子。）這一步你就把「VA 走完整棵樹得到 PA」的結果拿到手了，只是由 kernel 代你走。

### 3. gdb 手動走 page table（練習 C 會做完整版）

在 Ch 0 的 QEMU + gdb 環境裡，你可以**自己扮演 MMU**。以 kernel direct map 上一個 VA 為例（kernel VA 走的表根對 x86 是 CR3、對走 kernel 邏輯位址用 `init_mm.pgd`）：

```gdb
(gdb) p/x $cr3                          # 頂層 PGD 的實體位址（低 12 位是 flags/PCID）
(gdb) set $va = 0xffff888000001000      # 一個 direct map 上的 kernel VA（範例）
# 手算索引：
(gdb) p/x ($va >> 39) & 0x1ff           # PGD index
(gdb) p/x ($va >> 30) & 0x1ff           # PUD index
(gdb) p/x ($va >> 21) & 0x1ff           # PMD index
(gdb) p/x ($va >> 12) & 0x1ff           # PTE index
# 從 CR3 基底 + PGD_index*8 讀出 PGD entry，取 PFN，往下一級...
# （kernel VA 可用 __va(phys) = phys + PAGE_OFFSET 把每級表的實體位址轉回 gdb 能讀的 VA）
```

`vmlinux-gdb.py` 提供的 `lx-*` 指令、以及你自己寫的模組（下面）能把這流程自動化。**手動走一次**，你就永遠忘不了「4 級、每級 9 bits、entry 存 PFN 不存 VA」。

> 生產環境查 live kernel 的頁表，工程師常用 `crash`（配 vmcore/crash dump）或 `drgn`（Python 直接讀 live kernel 記憶體、內建 page table walk helper）。這門課用 gdb + QEMU 是為了學機制；上線後除錯（例如追一個 kernel 記憶體損毀）`drgn` 寫幾行就能把某個 VA 的完整 walk 路徑印出來，值得認識。

### 4. 寫一個模組走 page table

kernel 內走表用前面那組巨集，或用官方的 `walk_page_range()`（`mm/pagewalk.c`，接受一組 callback，每碰到一級呼叫你）。最小手動版：

```c
/* 概念骨架；完整可跑版在練習 C。走 current 的 mm 上某個 user VA */
struct mm_struct *mm = current->mm;
pgd_t *pgd; p4d_t *p4d; pud_t *pud; pmd_t *pmd; pte_t *pte;

pgd = pgd_offset(mm, va);
if (pgd_none(*pgd) || pgd_bad(*pgd)) goto out;
p4d = p4d_offset(pgd, va);              /* 4 級模式下是 no-op */
pud = pud_offset(p4d, va);
if (pud_none(*pud)) goto out;
if (pud_leaf(*pud)) { /* 1 GB huge page，就停在這 */ }
pmd = pmd_offset(pud, va);
if (pmd_none(*pmd)) goto out;
if (pmd_leaf(*pmd)) { /* 2 MB huge page，停在這 */ }
pte = pte_offset_map(pmd, va);          /* 最後一級；用完要 pte_unmap */
if (pte && pte_present(*pte)) {
    unsigned long pfn = pte_pfn(*pte);
    unsigned long phys = (pfn << PAGE_SHIFT) | (va & ~PAGE_MASK);
    pr_info("VA %#lx -> PA %#lx (pte=%#lx)\n", va, phys, pte_val(*pte));
    pte_unmap(pte);
}
out: ;
```

`pmd_leaf()`/`pud_leaf()`（新 kernel 統一命名，舊版是 `pmd_large()`）判斷這一級是不是 huge page 的葉子——這就是為什麼走表 code 每一級都要檢查一次「停在這了嗎」。

## 對比與取捨：x86_64 vs ARM64

| 面向 | x86_64 | ARM64（AArch64） |
|---|---|---|
| 頂層暫存器 | 單一 **CR3**（user+kernel 共用一張表） | **TTBR0_EL1**（user）+ **TTBR1_EL1**（kernel），架構層分離 |
| user/kernel 分界 | 靠 `_PAGE_USER` flag + canonical hole | 靠 VA 最高位選 TTBR0/TTBR1 |
| 級數 | 4 級（48-bit）或 5 級（57-bit，LA57） | 由 granule + VA 寬度決定，通常 4 級（level 0~3） |
| 級的術語 | PGD/PUD/PMD/PTE（Linux）＝ PML4/PDPT/PD/PT（硬體） | level 0/1/2/3 |
| 頁大小（granule） | 固定 4 KB（huge = 2 MB/1 GB） | 可選 **4 KB / 16 KB / 64 KB**，切法隨之改變 |
| ASID/PCID | PCID（選用） | ASID（TTBR 內建，較早成熟） |
| page fault 給誰 | #PF 例外，`do_page_fault` | data/instruction abort，`do_mem_abort` |
| 走表巨集 | 同一套 `pgd_offset`/`pud_offset`/... | **同一套**（Linux 的抽象讓兩者共用） |

最值得記的一點：**Linux 用同一套 `p*_offset`/`p*_none`/`p*_leaf` 巨集抽象掉所有架構差異**，各架構在自己的 `arch/*/include/asm/pgtable*.h` 把巨集實作成符合硬體格式。所以你讀 `mm/` 下的通用 code（memory.c、rmap.c、gup.c）看到的走表邏輯，x86 和 ARM64 是同一份——差異全被關進 arch 目錄。這是理解 kernel mm 的一個大解放：**通用 mm 邏輯只有一份，架構細節在 arch/。**

granule 這格特別提醒：ARM64 選 64 KB granule 時，一頁 64 KB，一個表 512 筆 → 每級索引 13 bits，切法變成完全不同的 shift 常數。頁大小、entry 大小、位址寬度三者互相決定切法——這在「先建立直覺」就埋了，ARM64 的可變 granule 是它最好的例證。

## 踩雷集錦

1. **錯誤直覺：「指標存的就是實體位址」。** 正確：user 和 kernel C code 裡拿到的**幾乎都是 VA**。只有極少數地方（DMA、page table entry 內部、CR3/TTBR）碰實體位址。`kmalloc` 回傳 VA、`&some_var` 是 VA。把 VA 當實體位址傳給硬體（如 DMA）會踩爛記憶體——這是驅動開發（Ch 41）的經典 bug。

2. **錯誤直覺：「page table 是一張表」。** 正確：是一棵**多級稀疏樹**。一個 process 的 page table 不是一大塊，而是散在多個 4 KB 頁裡、由 entry 互相指向的樹。沒用到的位址區間對應的子樹根本不存在。

3. **錯誤直覺：「每級 entry 裡存的是下一級表的 VA」。** 正確：存的是**實體位址（PFN）**。因為 MMU 在做 VA→PA 翻譯，它不能依賴還沒翻譯好的 VA。kernel 軟體走表時才用 direct map（`__va`）把那些實體位址臨時轉回 VA 來讀。

4. **錯誤直覺：「huge page 只是把頁變大」。** 正確：huge page 是 walk **提早在 PMD/PUD 級停住**——那一級的 entry 設了 PS bit，直接指向 2 MB/1 GB 大頁，少走一到兩級、且一筆 TLB 覆蓋更大範圍。它改變的是 walk 的深度，不只是頁大小。

5. **錯誤直覺：「進 kernel 要先換 page table」。** 正確（KPTI 關閉時）：syscall/中斷在**當前 process 的 page table 上**直接進 kernel，因為 kernel 高半部在每個 process 的表裡都有一份共享映射。KPTI 開啟後才會在 trampoline 切 CR3——而那正是為了擋 Meltdown 而付出的額外代價。

6. **錯誤直覺：「改了 page table，這顆 CPU 沖 TLB 就好」。** 正確（SMP 下）：這個 mm 可能被多核同時在跑，別的 CPU 的 TLB 裡還快取著舊映射。你得做 **TLB shootdown**——用 IPI（x86）或 `TLBI` 廣播（ARM64）叫其他 CPU 也沖掉。只沖自己會讓別的核繼續用作廢的舊翻譯，是很難查的記憶體損毀 bug。

7. **錯誤直覺：「`__pa()` 對任何 kernel 位址都成立」。** 正確：`__pa()`/`__va()` 只對 **direct map** 上的位址成立。對 vmalloc、ioremap、模組載入區的 VA 用 `__pa()` 會拿到垃圾——那些區的 VA 和 PA 沒有固定 offset，得用 `vmalloc_to_page()` 逐頁查。

8. **錯誤直覺：「48-bit VA 表示我有 $2^{48}$ 連續可用的位址」。** 正確：那 256 TB 被劈成 user 低半部（128 TB）和 kernel 高半部（128 TB），中間隔著一個硬體強制的 non-canonical 巨洞。你不能給 user 一個橫跨 canonical hole 的映射，`mmap` 一個 non-canonical 位址硬體會直接 #GP。「64 位」是暫存器寬度，不是實際可定址空間——48（或 57）位才是。

## 進階：再往深一層

- **canonical / non-canonical 位址**：48-bit 模式下，bit 48~63 必須是 bit 47 的符號延伸，否則是 non-canonical，硬體直接拒（#GP）。這就是為什麼 user space 頂在 `0x0000_7fff_...`、kernel 從 `0xffff_8000_...` 起——中間那 16 EB 的洞是硬體強制的，不是 Linux 挑的。面試常問「為什麼 64-bit 只用 48 位」：因為做滿 64 位的 page table 樹太深太貴，48 位（256 TB）在當時綽綽有餘，硬體只實作到那。

- **`__pa()` / `__va()` 的邊界**：這對巨集只對 **direct map** 上的位址成立（線性 offset 關係）。vmalloc、ioremap 的位址不能用 `__pa()`（它們 VA/PA 沒固定 offset），要用 `vmalloc_to_page()` 之類逐頁查。搞混會拿到垃圾實體位址。

- **five-level 的實際啟用**：`CONFIG_X86_5LEVEL` + CPU 支援 + 開機沒被 `no5lvl` 關掉，才會跑 5 級。多數環境仍 4 級。程式碼靠 P4D folding 同時支援兩者，你寫走表 code 一定要照顧 `p4d_*`，否則在 5 級機器上會漏一級。

- **paging-structure caches（walk cache）**：TLB 快取的是「VA 頁 → PA 頁」的最終結果，但現代 CPU 還另外快取**中間級**的 entry（PML4/PDPT/PD 這些內部節點），叫 paging-structure caches。這樣 TLB miss 時不必每次都從 CR3 重走 4 級，可能只走最後一兩級。所以「TLB miss」的成本不是固定 4 次記憶體讀，實務上常更少。改 page table 時這些 cache 也要一併失效，是 TLB 管理更細的一層。

- **面試高頻**：（a）畫出 48-bit VA 走 4 級到 PA 的圖；（b）context switch 換 CR3 為何要沖 TLB、PCID/lazy-TLB 怎麼救；（c）Meltdown 為何存在、KPTI 怎麼緩解、代價是什麼；（d）huge page 的 walk 差異與 TLB 收益；（e）多級樹相對單一大表為何省空間。這幾題你能白板講清楚，這章就到位了。

## 動手練習

1. **手算一個 VA**：取 `va = 0x0000_5555_1234_5678`，手動算出 PGD/PUD/PMD/PTE 四個索引和頁內偏移，寫下來。再用 gdb 的 `p/x ($va>>39)&0x1ff` 系列驗證你算對了。目標：不看筆記能拆位。

2. **pagemap 驗 CoW**：寫一個 C 程式 `fork()` 前後、以及子行程寫入某頁前後，各查一次該 VA 的 PFN（讀 `/proc/self/pagemap`，需 `sudo`）。你應該看到 fork 後父子 PFN 相同（共享），子行程一寫就變成不同 PFN（CoW 觸發，Ch 20 的預告）。

3. **gdb 走一次 kernel VA**：在 QEMU 裡停下，取一個 direct map 上的 kernel VA，從 CR3 開始一級一級讀出每級 entry、取 PFN、`__va` 轉回、再讀下一級，直到 PTE，算出最終 PA。和 `virt_to_phys` 的結果對照。這是練習 C 的核心，先手動走一遍。

4. **模組印一個 user VA 的翻譯**：寫個模組（或 kprobe），對 `current->mm` 上某個 user VA 用 `pgd_offset`→...→`pte_offset_map` 走到底，`pr_info` 印出 PA 和 PTE 的各個 flag（present/rw/user/nx）。故意傳一個沒 touch 過的 VA，觀察它在哪一級 `*_none()` 為真——那就是 demand paging「還沒建映射」的樣子。

5. **弄出一個 huge page**：`mmap` 一塊 `MAP_HUGETLB` 記憶體（或開 THP），用練習 4 的模組走它，觀察 walk 在 `pmd_leaf()` 為真時停住、PFN 對應 2 MB 對齊的實體位址。對照普通頁走到 PTE 才停。

## 本章重點整理

- 虛擬記憶體讓每個 process 有獨立、抽象、可 over-commit 的位址空間；VA→PA 的翻譯由 MMU 執行、由 kernel 建的 **多級稀疏 page table** 描述。
- x86_64 用 4 級（PGD/PUD/PMD/PTE，48-bit，$9{+}9{+}9{+}9{+}12$）或 5 級（+P4D，57-bit）；ARM64 用 level 0~3 並靠 TTBR0/TTBR1 從架構層分離 user/kernel。Linux 用同一套 `p*_offset` 巨集抽象所有架構。
- MMU 從 CR3/TTBR 起逐級 walk，entry 存下一級表的**實體 PFN**；任一級 present=0 就觸發 page fault（demand paging 的機制）；PMD/PUD 級設 PS bit = huge page，walk 提早停住。
- kernel 高半部映射在每個 process 的 page table 裡**共享**（syscall 快速進 kernel 的前提）；KPTI 為擋 Meltdown 把 user/kernel 表拆開，代價是每次 syscall 多切 CR3。

## 自我檢核

- [ ] 不看筆記，能把一個 48-bit VA 畫成 $9{+}9{+}9{+}9{+}12$ 走 4 級 page table 到實體頁的圖
- [ ] 能解釋為什麼 page table 用多級樹而不是單一大表（稀疏 → 省空間）
- [ ] 能說出每級 entry 裡存的是下一級表的**實體位址**而非 VA，以及為什麼
- [ ] 面試被問「context switch 為何換 CR3、為何連帶要處理 TLB」，你能答出 PCID/ASID 的角色
- [ ] 能解釋 kernel 高半部為何在每個 process 表裡共享，以及 KPTI 為何 Meltdown 逼它拆開、代價是什麼
- [ ] 能用 `/proc/<pid>/maps` + `/proc/<pid>/pagemap` 或 gdb 把一個 VA 翻成 PA
- [ ] 說得出 huge page 在 walk 上和普通頁的差別（哪一級停、TLB 收益）

## 延伸閱讀

### 官方文件

- **[Documentation/arch/x86/x86_64/mm.rst](https://www.kernel.org/doc/html/latest/arch/x86/x86_64/mm.html)**
  - **讀哪裡**：整篇，是 x86_64 kernel 位址空間佈局的**權威地圖**，4 級與 5 級的每個區（direct map / vmalloc / vmemmap / KASAN shadow）的位址範圍都在這
  - **和本章關聯**：本章的佈局圖是它的簡化版；要查某個 kernel VA 落在哪一區、direct map 到底多大，回來查這篇（別背數字）

- **[Documentation/mm/page_tables.rst](https://www.kernel.org/doc/html/latest/mm/page_tables.html)**
  - **讀哪裡**：整篇，講 Linux 通用 page table 抽象（PGD/P4D/PUD/PMD/PTE、folding、`p*_offset` 巨集）
  - **能學到什麼**：本章「同一套巨集跨架構」那條線的官方說明，配 `include/linux/pgtable.h` 一起讀

### 論文 / 一手資料

- **[Meltdown 論文（Lipp et al., 2018）](https://meltdownattack.com/meltdown.pdf)**
  - **讀哪裡**：前半的攻擊原理 + KAISER/KPTI 緩解那節
  - **為什麼值得讀**：本章「kernel 高半部共享 → Meltdown → KPTI」整條因果的一手來源；讀完你會懂 KPTI 不是憑空設計、而是硬體側通道逼出來的

- **[LWN: KAISER: hiding the kernel from user space](https://lwn.net/Articles/738975/)** — Jonathan Corbet
  - **讀哪裡**：整篇（不長）
  - **和本章關聯**：KPTI 進主線的一手記錄，講清楚「拆開 page table」在實作上怎麼做、效能代價從哪來；比論文更貼 Linux 實作

### 書籍

- **《Understanding the Linux Virtual Memory Manager》** — Mel Gorman（Prentice Hall, 2004）
  - **定位**：mm 子系統的經典深挖；page table 管理那幾章把本章講的樹結構、`p*_offset` 家族推到極致
  - **注意**：講的是 2.6，P4D/5 級那層還沒有，但多級 walk 的骨架至今適用；細節以 6.12 源碼為準

- **《Intel® 64 and IA-32 Architectures Software Developer's Manual》Vol. 3, 「Paging」章** — Intel
  - **定位**：x86 硬體側的權威——PTE 每個 bit、CR3 格式、hardware page walker 行為、PCID 都在這
  - **讀哪裡**：Paging 那一章的 4-level / 5-level paging 小節，配本章的 PTE flag 表對照著看

實體記憶體怎麼被分配成一頁一頁、供 page table 指向的頁框從哪來——那是 buddy allocator 的工作。下一章我們往下鑽一層，看 kernel 怎麼管理實體頁框與 zone。

→ [Ch 17 Physical memory：zone 與 buddy allocator](./17-buddy-allocator.md)
