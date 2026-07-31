# Ch 20 — demand paging、CoW、reverse mapping

> **目標**：理解為什麼 `malloc` 一次配 1 GB 卻不佔實體記憶體、為什麼 `fork` 一個吃了幾 GB 的 process 卻瞬間回來、以及當系統要換出一個實體頁時，kernel 怎麼反查「到底是誰在映射它」。這三件事——demand paging、copy-on-write、reverse mapping——是 Linux 記憶體管理「懶惰到底、共享到底」哲學的三根支柱。學完你能在 gdb 停在 `do_wp_page` 看一次 CoW 分裂，並用 `/proc/<pid>/smaps` 觀察 RSS 何時才真的長大。

> **承接**：這章直接踩在 Ch 19（`mm_struct`、VMA、page fault handler）的肩膀上——page fault 是本章所有機制的**觸發點**，`handle_pte_fault` 怎麼分流到 `do_anonymous_page`／`do_wp_page` 是 Ch 19 建好的骨架。CoW 是 Ch 10（`fork`/`copy_process`）「為什麼 fork 快」的答案。reverse mapping 是 Ch 22（reclaim/swap/OOM）換頁的前置能力。

## 為什麼需要這個？

先問一個你每天都在做、卻從沒細想的事：

```c
char *p = malloc(1UL << 30);   // 要 1 GB
```

這行如果**當場**去跟實體記憶體要 1 GB 填進去，會發生兩件蠢事：一是你機器上多數 `malloc` 配了根本用不到那麼多（想想 `calloc` 一個大 buffer 只寫了前面幾 KB）；二是 `exec` 一個 200 MB 的執行檔，若開機瞬間把整個檔案讀進實體記憶體，啟動會慢到不可接受。

`fork` 更誇張。傳統教科書講 `fork` 是「複製整個 process 的位址空間給子行程」。一個 Chrome 分頁吃 2 GB，`fork` 出一個只為了馬上 `exec` 的子行程，難道要先老實複製 2 GB、再馬上丟掉？那 `fork+exec`（shell 每跑一個指令都在做的事）會慢到系統無法運作。

Linux 對這兩個問題的答案是同一個哲學：**能拖就拖、能共享就共享，直到真的躲不掉才做工**。

- **demand paging**：`malloc`/`mmap` 不配實體頁，只在 `mm_struct` 裡登記一段 VMA（「這段位址是合法的、屬性是可讀可寫」）。實體頁到「第一次真的存取」那一刻，用一次 page fault 換來。
- **copy-on-write（CoW）**：`fork` 不複製頁，parent 和 child **共享**同一批實體頁，但全部標成唯讀。誰先寫，誰就吃一個寫保護 fault，kernel 到那時才複製一份給他。沒人寫的頁（絕大多數）永遠共享，一個 byte 都不複製。

這兩招都把「配實體頁」這個昂貴動作推遲到最後一刻，而觸發點都是 page fault。這章講的就是 fault handler 落地到 mm 的那一段——以及一個藏在背後、沒有它上面兩招都會爆炸的機制：reverse mapping。

## 先建立直覺

把三個機制放在一張圖上，它們其實是同一件事的三個面向：

```
   正向映射 (forward mapping)：VA → PA，靠 page table
   ┌──────────────┐   walk page table   ┌──────────────┐
   │ 虛擬位址 VA   │ ──────────────────► │ 實體頁 PA     │
   └──────────────┘   (Ch 16 的主題)     └──────────────┘

   demand paging：第一次走到 VA 時，PTE 是空的 → fault → 現在才配 PA、填 PTE
   CoW：多個 VA 指向同一個 PA、都標唯讀 → 誰寫誰 fault → 複製出新 PA

   反向映射 (reverse mapping / rmap)：PA → 「哪些 VA 在映射我？」
   ┌──────────────┐   rmap 資料結構      ┌──────────────┐
   │ 實體頁 PA     │ ──────────────────► │ VA₁,VA₂,VA₃…  │
   └──────────────┘  (anon_vma / i_mmap) └──────────────┘
```

正向映射每個 process 都有（就是它自己的 page table）。反向映射是全系統唯一的一組結構，因為問題本身是全域的：「這個實體頁，全世界有誰在用？」

為什麼需要反向？因為 reclaim（Ch 22）要換出一個頁時，光知道「這個頁存了什麼」不夠，它得找到**所有指向這個頁的 PTE**，把它們一個一個清掉（或改成 swap entry），否則某個 process 還握著舊 PTE 繼續讀，資料就錯了。一個實體頁可能同時被 5 個 process 映射（共享庫、CoW、`mmap` 同一個檔）——rmap 就是那本「反查名冊」。

demand paging 和 CoW 之所以能成立，也全靠 rmap 在背後撐著：CoW 要複製頁時得知道 refcount、reclaim 要動這個頁時得能反查所有 mapper。三者是一體的。

## demand paging：VMA 先行，實體頁後到

回顧 Ch 19：`mmap`（`malloc` 底層對大塊記憶體也是走 `mmap`）做的事是在 `mm_struct` 的 VMA 樹（6.1 起是 maple tree）裡插一個 `struct vm_area_struct`，記錄 `[vm_start, vm_end)`、權限 `vm_flags`、以及背後是匿名記憶體還是某個檔案。**它不碰 page table，也不配任何實體頁**。

所以 `malloc(1UL<<30)` 回來後，那 1 GB 位址的每一個 PTE 都還是空的（`pte_none()` 為真）。第一次你寫 `p[0] = 1`：

```
   p[0] = 1
     │  CPU MMU walk page table → PTE 是空的
     ▼
   #PF (page fault)  ── 硬體丟出 (Ch 19)
     │
     ▼
   handle_mm_fault → ... → handle_pte_fault
     │  pte_none() && VMA 是匿名 → do_anonymous_page
     ▼
   do_anonymous_page:  配一個實體頁、建 PTE、掛 rmap
```

分流的邏輯在 `mm/memory.c` 的 `handle_pte_fault()`：PTE 是空的、且 VMA 沒有 `vm_ops->fault`（匿名映射），就走 `do_anonymous_page()`；有 `vm_ops`（檔案映射）就走 `do_fault()`（Ch 21 page cache 的入口）。這章聚焦匿名頁與 CoW 這條線。

**lazy allocation 為什麼是好設計**，具體省在哪：

- **省實體記憶體**：配了不一定用。稀疏使用的大 buffer、被 `fork` 出來卻馬上 `exec` 的位址空間、程式裡從沒走到的分支——這些頁一輩子不會被觸碰，也就一輩子不佔實體頁。
- **快 `exec`**：`execve` 建好新的 VMA（指向執行檔），不預讀任何 code/data 頁。程式一跑，執行到哪個頁才 fault 進哪個頁（這就是 major fault vs minor fault 的來源——檔案頁第一次要從磁碟讀，是 major fault）。
- **overcommit**：既然配了不一定給，kernel 乾脆允許「承諾的虛擬記憶體總量 > 實體記憶體 + swap」。這叫 overcommit。

### overcommit：開空頭支票

`/proc/sys/vm/overcommit_memory` 控制策略：

| 值 | 名稱 | 行為 |
|---|---|---|
| `0`（預設） | heuristic | kernel 用啟發式判斷，明顯離譜的大配置才拒絕，一般都放行 |
| `1` | always | 一律答應，`malloc` 幾乎不會回 NULL |
| `2` | never | 嚴格會計，承諾總量超過 `swap + RAM * overcommit_ratio` 就當場拒絕 |

預設的 `0` 意味著 `malloc` 幾乎總是成功，但那是**空頭支票**——實際的實體頁要等你 fault 進去才兌現。如果全系統的行程哪天真的把承諾的頁都寫滿、實體記憶體 + swap 兌現不了，就輪到 **OOM killer** 出場挑一個 process 殺掉（Ch 22）。這是「overcommit 的帳，最後由 OOM 來付」的因果鏈——很多「記憶體明明夠卻被 kill」的困惑，根源在這裡。

### 匿名頁第一次 fault：`do_anonymous_page` 與零頁優化

`do_anonymous_page()`（`mm/memory.c`）藏了一個漂亮的優化。它先看這次 fault 是讀還是寫（`vmf->flags & FAULT_FLAG_WRITE`）：

- **讀 fault**：你只是讀一塊剛配好、還沒寫過的匿名記憶體——內容保證是全 0。kernel 沒必要為此配一個真的實體頁，直接把 PTE 指向全系統共享的一個 **zero page**（`ZERO_PAGE` / `my_zero_pfn`），並標成**唯讀**。成本是零實體頁。
- **寫 fault**：這時才透過 `alloc_anon_folio()` 配一個真的（清零的）folio，建可寫 PTE，並呼叫 `folio_add_new_anon_rmap()` 把它掛進反向映射。

```
   讀一塊沒寫過的匿名頁：
     PTE ──► [ ZERO_PAGE ]  ← 全系統共享、唯讀。100 個 process 讀各自的
             (read-only)       零頁，全都指向同一個實體頁，省到底

   之後對同一位址寫：
     寫 → 對唯讀的 ZERO_PAGE 觸發寫保護 fault → 配真頁、複製（其實是清零）
     PTE ──► [ 新的匿名頁 ]  ← 這時才佔一個實體頁
             (read-write)
```

看出來沒有——**零頁優化本質上就是一種 CoW**：先讓大家唯讀共享一個零頁，誰要寫誰才分裂。這是理解下一節 CoW 的最佳暖身。

## Copy-on-Write：fork 快的真正原因

`fork` 走到 `copy_process`（Ch 10）→ `dup_mm` → `copy_page_range`。這裡是關鍵：`copy_page_range` **不複製任何實體頁**，它做的是：

1. 把 parent 的 page table 逐項複製給 child（複製的是 PTE 這些**指標**，不是它們指向的資料頁）。
2. 對每一個可寫的、私有的匿名 PTE，把 parent 和 child 兩邊**都改成唯讀**，並設一個 `_PAGE_SOFT_DIRTY` 之外的軟體標記讓 fault handler 知道「這是 CoW 頁，不是真的唯讀」。
3. 把每個被共享的實體頁的 refcount（`folio->_refcount`）加一——因為現在多一個 page table 指向它。

於是 `fork` 的成本從「複製 N 個資料頁」降到「複製 page table + 改標記」，跟 process 用了多少記憶體幾乎無關。這就是 `fork` 快的全部秘密。

但不是每個 VMA 都套 CoW，`copy_page_range` 會看 VMA 的 `vm_flags`（Ch 19）分類處理：

- **私有可寫的匿名/檔案映射**（`MAP_PRIVATE`）：套 CoW，雙方標唯讀，就是上面講的主線。
- **共享映射**（`MAP_SHARED`）：本來語意就是「大家看到同一份、互相可見」，所以**不套 CoW**，兩邊 PTE 保持可寫、繼續指向同一頁——這正是 `MAP_SHARED` 用來做 process 間共享記憶體的原理。
- **本來就唯讀的映射**（如程式碼段）：反正誰都不會寫，複製 PTE 即可，連「標唯讀」都不用做，也永遠不會 CoW 分裂。

換句話說，CoW 只發生在「私有 + 可寫」的頁上。看懂這點，你就懂為什麼兩個 process 用 `MAP_SHARED` 映射同一塊記憶體時寫入彼此可見（沒 CoW），而 `fork` 出來的私有堆積寫入互不可見（有 CoW）——差別全在 `vm_flags` 那一個 `VM_SHARED` bit。

### CoW 三態

```
   ── fork 前 ──────────────────────────────────
     parent VA ──► [ page X ]  (RW)   refcount=1

   ── fork 後（共享唯讀）───────────────────────
     parent VA ──┐
                 ├──► [ page X ]  (RO, 標 CoW)   refcount=2
     child  VA ──┘
     兩邊都能讀，讀不會 fault；page X 一個 byte 都沒複製

   ── 其中一方寫（分裂）──────────────────────
     child 寫 child_va[0] = 1
       → 對唯讀 PTE 觸發寫保護 fault
       → do_wp_page → 發現 refcount>1 且是 CoW 頁 → 複製
     parent VA ──► [ page X ]  (回復 RW)   refcount=1
     child  VA ──► [ page X' ] (RW, 全新)  refcount=1
     只有「被寫的那一個頁」被複製，其餘沒動的頁繼續共享
```

注意最後一態的細節：分裂後 parent 那邊的 page X 如果 refcount 掉回 1（沒別人共享了），fault handler 會**直接把它的 PTE 恢復成可寫**，連複製都省了——這叫 reuse。也就是說，寫的那一方拿新頁，沒寫但獨佔的那一方原地解禁。

### `do_wp_page`：寫保護 fault 的落點

寫一個唯讀的 CoW 頁，硬體丟出 page fault，`handle_pte_fault` 看到「PTE 存在但唯讀、fault 是寫」，分流到 `do_wp_page()`（`mm/memory.c`）。它的決策核心：

```
   do_wp_page(vmf):
     folio = 這個 PTE 指向的 folio
     if 這個 folio 我可以獨佔重用（refcount/mapcount 判定只有我一個 mapper）:
         wp_page_reuse()        # 不複製，直接把 PTE 改回可寫
     else:
         wp_page_copy()         # 複製一份新頁給寫的人
```

`wp_page_copy()` 做的事：配一個新 folio、把舊頁內容 `copy_user_highpage` 複製過去、用 `folio_add_new_anon_rmap()` 給新頁建反向映射、把 fault process 的 PTE 改指向新頁且可寫、對舊 folio 的 mapcount/refcount 減一。舊頁少了一個 mapper，若歸零就可被 reclaim 回收。

「我能不能獨佔重用」這個判斷比看起來難，牽涉 `_refcount`（有幾個引用，含暫時的 GUP pin）和 `_mapcount`（有幾個 page table 映射）的精確語意，也是 Dirty COW 這類漏洞的溫床——見下面「進階」。

### CoW 的 refcount 管理：`_refcount` vs `_mapcount`

一個 `struct folio`（6.x 起取代裸 `struct page` 當 mm 的操作單位）身上有兩個計數，初學最容易搞混：

| 欄位 | 語意 | 誰動它 |
|---|---|---|
| `folio->_refcount` | **總引用數**：page table 映射 + kernel 內部暫時持有（GUP pin、reclaim 掃描中、page cache 持有…） | `folio_get`/`folio_put` |
| `folio->_mapcount` | **被幾個 process 的 page table 映射**（用於判斷 CoW 是否要複製） | rmap 的 add/remove |

粗略關係：`_refcount >= _mapcount + (其它非映射的引用)`。CoW 判斷「能否 reuse」要同時看兩者，因為若有人透過 GUP（`get_user_pages`，例如 O_DIRECT I/O、RDMA）pin 住這個頁，即使 mapcount 是 1，也不能貿然重用——那個 pin 住的 DMA 可能正在寫這個頁。這正是歷史上一大票 CoW 正確性 bug（含 Dirty COW 的親戚）的來源，也是 6.x 引入 `folio_maybe_dma_pinned()` 這類精細判斷的原因。

## 底層機制：reverse mapping 怎麼運作

現在講那個撐起前面一切、卻最少人真懂的機制。

**問題**：reclaim 要換出實體頁 X。X 目前被哪些 process 的哪些 PTE 映射？必須全部找到、逐一清掉（或改 swap entry），一個都不能漏。正向的 page table 幫不上忙——它是「給 VA 找 PA」，我們要的是反過來。

Linux 對匿名頁和檔案頁用**兩套不同的 rmap 結構**，因為兩者的「共享模式」本質不同。

### 匿名頁的 rmap：`anon_vma` 與 `anon_vma_chain`

匿名頁的共享來自 `fork`：一個 process fork 出子孫，同一個匿名頁可能被整棵行程樹共享。Linux 用 `struct anon_vma`（`include/linux/rmap.h`）當「這一族匿名 VMA 的集合點」，每個 folio 的 `folio->mapping` 欄位（低位標記為匿名）指向它所屬的 `anon_vma`。

難點在 `fork`：child 的 VMA 需要能反查到 parent 的舊頁，但又要能有自己新分裂出來的頁。Linux 的解法是 `struct anon_vma_chain`（AVC）——一個把 VMA 和 anon_vma **多對多**接起來的中介結構：

```
   一個實體匿名頁 folio
     folio->mapping ──► anon_vma (root)
                          │  interval tree (rb_root)
                          │  掛著一串 anon_vma_chain
                          ▼
         AVC ──► VMA_parent   AVC ──► VMA_child1   AVC ──► VMA_child2
          │                                            
          └─ 每個 AVC 記「這個 anon_vma 被哪個 VMA 映射」
```

`fork` 時 `anon_vma_fork()`（`mm/rmap.c`）為 child 的 VMA 建新的 anon_vma 並用 AVC 把它鏈回 parent 的 anon_vma，形成一棵 anon_vma 樹。反查一個匿名頁的所有 mapper，就是從 folio 的 root anon_vma 出發，走它的 interval tree、對每個相關 VMA 用頁的偏移量算出 VA、再查該 VMA 所屬 `mm` 的 page table 拿到 PTE。第一次要建立 anon_vma 的入口是 `__anon_vma_prepare()`。

為什麼要這麼繞？因為若讓每個匿名頁直接掛一串「所有映射它的 VMA」的鏈，`fork` 一次就要更新海量的頁，`fork` 又會變慢。anon_vma 樹讓 `fork` 只動 VMA 層級的少量結構，代價是反查時要多走一層樹——這是 kernel 在「fork 要快」和「reclaim 反查要準」之間的經典取捨。

具體走一遍反查。假設 parent 配了一段匿名 VMA、寫進去一頁 folio F（此時 refcount=1、只有 parent 映射），然後 fork 出 child、child 又 fork 出孫。reclaim 現在要換出 F，`rmap_walk` 對 F 的處理是：

```
   folio F ─ folio->mapping ─► anon_vma_root  (parent 那一族的根)
                                    │  遍歷 root 的 interval tree
                                    │  (key = folio 在 VMA 裡的 index/偏移)
      ┌─────────────────────────────┼──────────────────────────────┐
      ▼                             ▼                              ▼
   VMA_parent                    VMA_child                     VMA_grandchild
   算出 VA=vma->vm_start          同一 index 算出各自的 VA         同一 index 算出各自的 VA
   walk parent 的 mm page table   walk child 的 mm page table     walk 孫的 mm page table
   → 拿到那個 PTE → 拆掉/改 swap    → 拿到 PTE（若還沒 CoW 分裂）   → 拿到 PTE
```

關鍵在那個 index：F 在每個共享它的 VMA 裡都落在**同一個相對偏移**（因為 CoW 只是把整段位址空間平移共享），所以 rmap 用「folio 的 index」當 interval tree 的查詢 key，就能對每個 VMA 反算出對應的 VA，再各自 walk 那個 `mm` 的 page table 拿 PTE。若某個 child 早已把這頁 CoW 分裂掉，它的 PTE 已指向別的 folio，`rmap_walk` 在那個 VMA 上會發現 PTE 不指向 F、跳過即可。這也是為什麼 rmap 反查後還要**逐一驗證 PTE 真的指向這個 folio**，不能盲信結構。

### 檔案頁的 rmap：`address_space->i_mmap`

檔案頁（page cache，Ch 21）的共享模式不同：多個 process `mmap` 同一個檔案（例如所有動態連結程式都映射 `libc.so`），它們映射的是同一個 `struct address_space`（一個檔案一個，掛在 inode 上）裡的同一批頁。

所以檔案頁的反查簡單直接：`folio->mapping` 指向該檔案的 `address_space`，而 `address_space->i_mmap` 是一棵 interval tree（`rb_root_cached`），收錄「所有映射這個檔案的 VMA」。給定一個檔案頁，知道它在檔案裡的偏移，就能在 `i_mmap` 樹裡查出「哪些 VMA 映射到涵蓋這個偏移的範圍」，再各自算 VA、查 PTE。

### 統一入口：`rmap_walk` 與 folio

不管匿名還是檔案，reclaim/migration 要「對這個 folio 的每一個 mapper 做某件事」，都走同一個抽象入口 `rmap_walk()`（`mm/rmap.c`）。它看 folio 是匿名還是檔案，分派到 `rmap_walk_anon`（走 anon_vma）或 `rmap_walk_file`（走 i_mmap），對每個 mapper 呼叫一個 callback。三個最重要的使用者：

- **`try_to_unmap()`**：reclaim 要換出頁時，對每個 mapper 把 PTE 拆掉、匿名頁改寫成 swap entry。這是 rmap 存在的頭號理由。
- **`folio_referenced()`**：掃描每個 mapper 的 PTE 的 accessed bit，判斷這個頁最近有沒有被用過（LRU 老化用，Ch 22）。注意 6.x 已經把舊的 `page_referenced` 換成 folio 版；本課釘死 v6.12，看到舊書寫 `page_referenced` 要知道它是同一個東西的前身。
- **page migration / `try_to_migrate()`**：NUMA 平衡、記憶體壓縮要搬頁時，同樣得反查所有 mapper 改指向新頁。

rmap 的 add/remove 也全面 folio 化了：新匿名頁掛 `folio_add_new_anon_rmap()`、既有匿名頁多一個映射用 `folio_add_anon_rmap_ptes()`、檔案頁用 `folio_add_file_rmap_ptes()`、移除映射用 `folio_remove_rmap_ptes()`（都在 `mm/rmap.c`）。名字裡的 `_ptes` 複數是因為一個 folio 可能橫跨多個 PTE（large folio / THP）。

### 為什麼 rmap 難

一句話：**因為共享是多對多、而且動態變化**。同一個實體頁可能被不同 process、透過不同 VMA、以不同偏移映射；`fork` 會突然讓一個頁多出一整棵子孫；CoW 分裂會讓一個頁的 mapper 減少；migration 會讓所有 mapper 同時改指向。rmap 結構必須在這些操作下始終能「給一個 folio，準確列出當下所有 mapper」，而且要快到不拖垮 `fork` 和 reclaim。anon_vma 樹 + i_mmap interval tree 這套設計，就是為了同時滿足這幾個互相拉扯的需求。

## 動手：親眼看見 CoW 與 demand paging

### 觀察一：CoW 之下 RSS 何時才長大

RSS（Resident Set Size）是「這個 process 實際佔了多少實體頁」。demand paging + CoW 的直接後果是：配了記憶體 RSS 不漲，**寫下去才漲**。

```c
// cow_rss.c  ——  觀察 fork 後寫前寫後 RSS 的變化
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/wait.h>

#define N (256UL * 1024 * 1024)   // 256 MB

static void show_rss(const char *tag) {
    char cmd[128];
    snprintf(cmd, sizeof cmd,
             "grep -H VmRSS /proc/%d/status | sed 's/^/[%s] /'",
             getpid(), tag);
    system(cmd);
}

int main(void) {
    char *buf = malloc(N);
    show_rss("malloc 後(還沒寫)");     // RSS 幾乎沒動——只有 VMA，沒實體頁
    memset(buf, 1, N);                 // 寫遍整塊 → 每頁 fault 進真頁
    show_rss("memset 後(parent)");     // RSS 漲了約 256 MB

    pid_t pid = fork();
    if (pid == 0) {
        show_rss("fork 後(child, 寫前)");   // child RSS 看似高，但頁是共享唯讀的
        // 只寫前一半
        memset(buf, 2, N / 2);
        show_rss("寫一半後(child)");        // 被寫的那一半分裂成 child 私有頁
        _exit(0);
    }
    wait(NULL);
    return 0;
}
```

```bash
gcc -O0 cow_rss.c -o cow_rss && ./cow_rss
```

要更精確看「共享 vs 私有」的分佈，用 `smaps`：

```bash
# 讓 child 在寫之前暫停（自行加個 getchar()），另開終端：
cat /proc/<child_pid>/smaps | grep -A20 'heap\|00'  # 看那段匿名 VMA
# 關注這幾行：
#   Rss:           被算進 RSS 的量
#   Shared_Clean / Shared_Dirty:  和別人共享的頁（CoW 未分裂時在這）
#   Private_Clean / Private_Dirty: 自己獨佔的頁（CoW 分裂後跑到這）
```

你會看到：child 剛 `fork` 完，那塊記憶體大量落在 **Shared_Dirty**（和 parent 共享）；寫了一半之後，被寫的那半移到 **Private_Dirty**（分裂出的私有頁），沒寫的那半還留在 Shared。這就是 CoW 三態圖的實測版。

一個常讓人算錯的地方：`fork` 後 parent 和 child 的 `VmRSS` **加起來會遠大於實際佔用的實體記憶體**，因為共享的那些頁在兩邊的 RSS 都被計了一次。想知道「真正花掉多少實體記憶體」不能把各 process 的 RSS 相加，要看 `smaps` 的 `Pss`（Proportional Set Size）——共享頁的量會除以共享它的 process 數再計入。`Rss` 騙你、`Pss` 才誠實，這是排查記憶體用量時的常見坑。

### 觀察二：minor fault 計數

每次 demand paging / CoW 分裂都是一次 minor fault（沒碰磁碟）。用 `/usr/bin/time -v` 或 `perf` 數：

```bash
/usr/bin/time -v ./cow_rss 2>&1 | grep -i 'page faults'
#   Minor (reclaiming a frame) page faults: 131xxx   ← 約 = 寫過的頁數
#   Major (requiring I/O) page faults: 0             ← 匿名頁不碰磁碟，major=0

perf stat -e minor-faults,major-faults ./cow_rss
```

`vmtouch` 可以看一個檔案有多少頁真的常駐在 page cache（檔案頁 demand paging 的視角），配 `mmap` 一個大檔實驗 major fault：

```bash
vmtouch -v somebigfile        # 顯示常駐比例；剛開機時多半是 0%
```

### 觀察三：gdb 停在 `do_wp_page` 看一次分裂

按 Ch 0 架好 QEMU + gdb，在 QEMU 裡跑一個會 fork 後寫的小程式，host 上：

```gdb
(gdb) break do_wp_page
(gdb) continue
# QEMU 裡的程式一寫 CoW 頁就停在這
(gdb) print vmf->address              # 觸發的虛擬位址
(gdb) print vmf->orig_pte             # 那個唯讀 PTE
(gdb) bt                              # 看呼叫鏈：handle_pte_fault → do_wp_page
(gdb) break wp_page_copy
(gdb) continue                        # 若走到這，代表判定要複製（refcount>1）
```

停在 `wp_page_copy` 就是「這一頁正在被複製給寫的人」的那一刻。對照原始碼看它怎麼配新 folio、`folio_add_new_anon_rmap` 掛 rmap、改 PTE——你在讀 CoW 的心臟。

## 對比與取捨

| 方案 | 立即配實體頁 | demand paging + CoW |
|---|---|---|
| `malloc` 大 buffer | 當場佔滿實體記憶體 | 只建 VMA，用到才配 |
| `fork` 一個大 process | 複製整個位址空間 | 只複製 page table + 標唯讀 |
| 寫時延遲 | 無延遲，但前期成本高 | 每次首寫多一次 minor fault |
| 記憶體帳 | 誠實但浪費 | 高效但要靠 overcommit + OOM 兜底 |
| 反查一個頁的 mapper | 不需要（各自獨立） | **必須有 rmap**，否則沒法安全 reclaim |

取捨的本質：demand paging + CoW 把成本從「配置時」搬到「首次寫入時」，並用 rmap 這個額外結構換來「頁可共享、可安全回收」。代價是複雜度（anon_vma 樹、refcount/mapcount 語意）和 overcommit 帶來的 OOM 風險。對通用 OS 而言這筆帳非常划算——但對硬即時系統，這種不可預期的 fault 延遲是要靠 `mlock`/`mlockall` 主動關掉的（見踩雷）。

## 踩雷集錦

1. **「`malloc` 成功就代表記憶體到手了」——錯**。`malloc` 回非 NULL 只代表 VMA 建好、overcommit 放行了那張空頭支票。實體頁要等你**寫**下去、fault 進來才兌現。真的沒記憶體時，可能不是 `malloc` 回 NULL，而是之後某次寫入 fault 時系統觸發 OOM killer。想確保拿到就 `mlock` 它。

2. **「`fork` 之後 parent 和 child 記憶體互不影響是因為各自複製了一份」——半錯**。它們是**共享**同一批實體頁的，只是標了唯讀；「互不影響」是 CoW 在誰寫時偷偷分裂做到的假象。理解這點你才會懂為什麼 `fork` 一個 20 GB 的 process 幾乎瞬間完成，也才會懂為什麼 fork 後 child 大量寫入時 RSS 會暴漲（每寫一頁就分裂一頁）。

3. **「讀一塊剛配的匿名記憶體會佔實體頁」——錯**。只讀不寫的話，它指向全系統共享的 `ZERO_PAGE`，唯讀，零實體頁成本。RSS 也不會因為純讀而漲。這就是為什麼 `calloc` 一個巨大陣列卻只讀，幾乎不吃記憶體。

4. **`_refcount` 和 `_mapcount` 混為一談**。`_mapcount` 是「幾個 page table 映射我」，`_refcount` 是「總共幾個引用（含 GUP pin、reclaim 掃描等非映射引用）」。CoW 判斷能否 reuse 要看兩者——只看 mapcount 而忽略 GUP pin，正是歷史上多起 CoW 資料損毀 bug 的根源。

5. **以為 rmap 是「每個頁掛一條所有 mapper 的鏈」**。匿名頁其實走 anon_vma 樹（為了 `fork` 快），反查要多繞一層；檔案頁走 `i_mmap` interval tree。直覺上的「一個頁一條 mapper 鏈」會讓 `fork` 慢到不可用，kernel 沒這樣做。

## 進階：再往深一層

- **Dirty COW（CVE-2016-5195）**：一個經典的 CoW race。攻擊者用一個 thread 反覆對 `/proc/self/mem` 或私有 CoW 映射寫（觸發 `do_wp_page` 準備複製），另一個 thread 用 `madvise(MADV_DONTNEED)` 把剛分裂的頁丟掉，製造出「寫進了本該唯讀的原始檔案頁」的 race，達成對唯讀檔案（如 `/etc/passwd`、setuid binary）的提權寫入。修法是引入 `FOLL_COW`／後來重構 GUP 的 CoW 語意。這條線接 `kernel_pwn` 的提權技巧和 `oscp` 的 Linux 本地提權——當年這是打遍天下的 exploit。理解本章的 CoW refcount/mapcount 語意，就能理解那個 race 為什麼存在。

- **large folio / THP 對 rmap 的衝擊**：一個 folio 可能是多個連續 4K 頁組成的大頁，rmap 的 `_ptes` 版本函式（`folio_add_anon_rmap_ptes` 等）就是為此存在——一次操作可能對應多個 PTE。折開一個大 folio（split）時 rmap 也要跟著拆，是 mm 最複雜的路徑之一。

- **`madvise` 主動控制 demand paging**：`MADV_WILLNEED`（預告要用、鼓勵預讀）、`MADV_DONTNEED`（丟棄、下次存取重新 fault）、`MADV_FREE`（延遲釋放、可被 reclaim 回收但寫入前還能救回）。這些是使用者空間主動介入 demand paging 策略的旋鈕。

- **面試常問**：「`fork` 為什麼快？」（CoW，只複製 page table）「一個實體頁被換出時 kernel 怎麼知道誰在用它？」（rmap：anon_vma / i_mmap）「`malloc` 大塊記憶體會馬上佔實體記憶體嗎？」（不會，demand paging，寫才配）「讀零頁和寫零頁差在哪？」（讀共享 ZERO_PAGE、寫觸發分裂配真頁）。這四題答得清楚，代表你真的懂這章。

## 動手練習

1. **CoW 三態實測**：跑上面的 `cow_rss.c`，在 child 寫之前加 `getchar()` 暫停，用 `/proc/<pid>/smaps` 記錄那段匿名 VMA 的 `Shared_Dirty` 與 `Private_Dirty`；讓 child 寫一半後再記一次。畫出「寫前全 Shared → 寫後一半移到 Private」的變化，對照本章 CoW 三態圖。

2. **證明零頁優化存在**：配一大塊匿名記憶體，只**讀**（`volatile char c = buf[i]` 遍歷），用 `/usr/bin/time -v` 看 minor faults 和 VmRSS 幾乎不漲；再改成**寫**同一塊，看 minor faults 跳到約等於頁數、RSS 漲滿。解釋差異來自 `do_anonymous_page` 的讀寫分流。

3. **gdb 抓一次 CoW 分裂**：在 QEMU 裡 `break do_wp_page`、`break wp_page_copy`，跑一個 fork 後寫的程式，記錄兩個中斷點各觸發幾次、`vmf->address` 是什麼，用 `bt` 畫出從硬體 fault 到 `wp_page_copy` 的完整呼叫鏈。

4. **玩壞 overcommit**：把 `/proc/sys/vm/overcommit_memory` 設成 `2`（never），再跑一個一次 `malloc` 遠超實體 + swap 的程式，觀察 `malloc` 這次**直接回 NULL**（而非事後 OOM）。改回 `0` 再跑，觀察 `malloc` 成功但寫入時可能觸發 OOM。對比兩種策略把「拒絕」發生的時機從哪搬到哪。

## 本章重點整理

- **demand paging**：`malloc`/`mmap` 只建 VMA、不配實體頁；第一次存取才 page fault → `do_anonymous_page` 配頁填 PTE。省記憶體、快 `exec`，代價是 overcommit 的帳最終由 OOM 付。
- **零頁優化**：讀一塊沒寫過的匿名記憶體共享唯讀 `ZERO_PAGE`，寫才分裂配真頁——本質就是 CoW。
- **CoW**：`fork` 只複製 page table 並把雙方標唯讀（`copy_page_range`），誰寫誰吃 `do_wp_page`／`wp_page_copy` 複製一份。這是 `fork` 快的全部原因。判斷能否 reuse 要同時看 `_refcount` 與 `_mapcount`。
- **rmap**：給一個 folio 反查所有 mapper。匿名頁走 `anon_vma` + `anon_vma_chain` 樹（為 `fork` 快而設計），檔案頁走 `address_space->i_mmap` interval tree；統一入口 `rmap_walk`，reclaim 靠 `try_to_unmap` 拆掉每個 mapper 的 PTE。

## 自我檢核

- [ ] 不看筆記，能解釋為什麼 `malloc(1GB)` 成功但 VmRSS 沒漲，以及 RSS 何時才會漲
- [ ] 能說清 `fork` 之後 parent/child 到底共享什麼、CoW 在哪一刻分裂、分裂時哪一方拿新頁
- [ ] 能區分 `folio->_refcount` 與 `folio->_mapcount` 的語意，並說出為什麼 CoW 判斷兩個都要看
- [ ] 能解釋「reclaim 換出一個實體頁前為什麼非有 rmap 不可」，以及匿名頁和檔案頁 rmap 結構為何不同
- [ ] 面試被問「fork 為什麼快 / 一個頁被換出時怎麼找到所有 mapper」，能各用兩三句話答清楚
- [ ] 能在 gdb 停在 `do_wp_page`，讀懂它 reuse vs copy 的分流

## 延伸閱讀

### 官方文件與原始碼

- **`mm/memory.c`（v6.12）** — [`do_anonymous_page` / `do_wp_page` / `wp_page_copy` / `handle_pte_fault`](https://elixir.bootlin.com/linux/v6.12/source/mm/memory.c)
  - **讀哪裡**：`do_anonymous_page` 看讀寫分流與 `ZERO_PAGE`；`do_wp_page` 看 reuse vs copy 的判斷；`wp_page_copy` 看複製 + 掛 rmap 的完整動作
  - **和本章的關聯**：這是 demand paging 與 CoW 落地的檔案，本章每個機制都能在這裡對上原始碼

- **`mm/rmap.c`（v6.12）** — [`rmap_walk` / `try_to_unmap` / `folio_referenced` / `folio_add_new_anon_rmap` / `anon_vma_fork`](https://elixir.bootlin.com/linux/v6.12/source/mm/rmap.c)
  - **讀哪裡**：檔案開頭那段解釋 anon_vma 鎖序與設計的長註解（是理解 rmap 最好的一手材料）；`anon_vma_fork` 看 `fork` 怎麼建 anon_vma 樹
  - **前提**：先讀懂 Ch 19 的 VMA / `mm_struct`

- **[Documentation/mm/ 的 physical_memory / process_addrs](https://www.kernel.org/doc/html/latest/mm/)** — kernel 官方 mm 文件
  - **能學到什麼**：folio、rmap、page table 各主題的設計者說明，補足本章的簡化

### 文章 / 書籍

- **[LWN: "The case for the folio"](https://lwn.net/Articles/849538/) 與 folio 系列** — Jonathan Corbet
  - **為什麼讀**：本章大量 API 已 folio 化（`folio_add_*_rmap`、`folio_referenced`），這系列解釋為什麼從 `struct page` 換到 `struct folio`，讀完你會懂那些 `_ptes` 後綴的由來
  - **前提**：知道 `struct page` 是什麼（Ch 17）

- **《Understanding the Linux Virtual Memory Manager》** — Mel Gorman
  - **讀哪裡**：CoW、reverse mapping、page fault 相關章節
  - **注意**：講的是較舊 kernel（rmap 是早期物件式設計），anon_vma 樹和 folio 是後來演進；架構直覺仍極有價值，細節以 v6.12 源碼為準

- **[Dirty COW 官方說明頁](https://dirtycow.ninja/) 與 [CVE-2016-5195](https://nvd.nist.gov/vuln/detail/CVE-2016-5195)**
  - **為什麼讀**：把本章的 CoW refcount/mapcount 語意連到一個真實、著名的提權漏洞
  - **接哪門課**：`kernel_pwn`（提權）、`oscp`（Linux 本地提權）

demand paging 讓匿名頁按需而來，但檔案的內容從哪來、寫回去又怎麼延遲？下一章進入 page cache 與 writeback——檔案頁的 demand paging（`do_fault` 那條線）在那裡完整展開。

→ [Ch 21 page cache 與 writeback](./21-page-cache-writeback.md)
