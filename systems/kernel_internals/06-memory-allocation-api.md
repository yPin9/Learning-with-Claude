# Ch 6 — 記憶體配置 API：kmalloc/vmalloc/slab/GFP

> **目標**：搞懂 kernel 裡「要一塊記憶體」有哪幾種 API、它們各自去底層哪裡拿、什麼場景該用哪個；並且真正理解 GFP flags——尤其 `GFP_KERNEL` vs `GFP_ATOMIC` 這個「這裡能不能睡」的判斷，是 Ch 2 執行環境的直接應用，選錯會死鎖或 crash。

## 為什麼需要這個？

在使用者空間你寫 `malloc(size)`，libc 幫你跟 kernel 要記憶體、切好、還你一個指標。你從來不用管「這塊記憶體實體上連不連續」「現在能不能睡著等別人釋放」。

進到 kernel 裡，這兩件事都得你自己扛，而且 **kernel 沒有 `malloc`**——`malloc` 是 libc 的東西，kernel 不連 libc。你手上只有一組更底層、語意更明確的配置 API，每個都在回答兩個問題：

1. **這塊記憶體要不要實體連續（physically contiguous）？** CPU 存取記憶體會經過 MMU 做虛擬→實體轉譯，所以 CPU 眼中「連續的虛擬位址」實體上可以是零散的，它不在乎。但 DMA 硬體不一樣——網卡、磁碟控制器直接對實體位址讀寫，**不認識 page table、不經過 MMU**。你給它一塊「虛擬連續但實體東一塊西一塊」的 buffer，它照著虛擬位址推算實體位址一路寫下去，就寫進別人的記憶體了（DMA 見 Ch 41）。所以只要記憶體會交給硬體 DMA，就必須實體連續。但實體連續很貴——記憶體用久了會碎片化，要湊一大塊連續的 page 可能湊不出來，即使空閒總量夠。
2. **現在這個 context 能不能睡（sleep）？** 配置記憶體時如果暫時沒有空閒 page，kernel 可以「等」——去做記憶體回收（reclaim，Ch 22）、把別的 page 換出去、睡著等 kswapd 生出空間。但「睡」這個動作在中斷處理常式裡、在持有 spinlock 時是**禁止**的（Ch 2 講過三種 context 的睡眠規則，Ch 25 講持鎖為何不能睡）。所以配置 API 得讓你講清楚「我這裡到底能不能睡」——這就是 GFP flags。

這一章我們把配置 API 的**選擇邏輯**講清楚。真正的底層——buddy allocator 怎麼從 zone 切 page（Ch 17）、slub 怎麼管小物件的 cache（Ch 18）——留到記憶體管理那個 Part 深挖。這章是「使用者視角」：你寫模組時每天都要下的決定。

## 先建立直覺

在看任何 API 之前，先把上面那兩個問題釘進腦子，因為整章的選擇都繞著它們轉：**（一）要不要實體連續？（二）現在能不能睡？** 第一題決定「用哪個 API」——要連續走 kmalloc/pages、不要連續才考慮 vmalloc。第二題決定「配什麼 GFP flag」——能睡用 `GFP_KERNEL`、不能睡用 `GFP_ATOMIC`。這兩題彼此獨立：你可以在中斷裡（不能睡）配一塊實體連續的小記憶體（`kmalloc(size, GFP_ATOMIC)`），也可以在 process context（能睡）配一大塊不連續的（`vmalloc`）。把配置 API 想成這兩個問題的答案表，後面每一節都是在填這張表的某一格。

有了這個框架，先在腦中畫出這張圖：**四個常用 API，各自通往三個不同的底層 allocator。**

```
   你在模組/子系統裡呼叫的 API              底層真正去哪裡拿記憶體
   ─────────────────────────              ────────────────────────────────

   kmalloc(size, gfp)  ──小物件──►  ┌──────────────────┐
   kzalloc(size, gfp)               │  slab / slub     │  管理 8B~8KB 的
        （= kmalloc + __GFP_ZERO）  │  (Ch 18)         │  小物件，切自 page
                                    └────────┬─────────┘
                                             │ 缺 page 時向下要
                                             ▼
   __get_free_pages(gfp, order) ─►  ┌──────────────────┐
   alloc_pages(gfp, order)          │  buddy allocator │  以 2^order 個 page
        （頁級，2^order 個 page）    │  + zone (Ch 17)  │  為單位切／合併
                                    └────────┬─────────┘
                                             │ 這是實體 page 的最終來源
                                             ▼
                                    ┌──────────────────┐
   vmalloc(size) ───大而不需連續──► │  vmalloc area     │  向 buddy 要一堆
        （虛擬連續，實體零散）        │  + 建 page table  │  「零散」的 page，
                                    └──────────────────┘  再在 vmalloc 虛擬區
                                                          串成「虛擬連續」

   kvmalloc(size, gfp) ─► 先試 kmalloc，失敗（太大/碎片）自動退回 vmalloc
```

三個底層 allocator 的分工：

- **buddy allocator（Ch 17）**：實體記憶體的最終發放者，單位是 page（x86_64 一頁 4 KB），一次給你 `2^order` 個連續 page。所有 page 最終都從這裡出。
- **slab/slub（Ch 18）**：架在 buddy 之上，專門切「比一頁小」的物件。你要 96 bytes，slub 從它預先向 buddy 要來的 page 裡切一塊給你，避免每次都跟 buddy 要一整頁而浪費。`kmalloc` 就是走這條路。
- **vmalloc area**：一段專門的 kernel 虛擬位址範圍。`vmalloc` 向 buddy 要一堆**不必相鄰**的 page，然後在這段虛擬位址裡幫你建 page table，把這些零散的 page 映射成「看起來連續」的一塊。代價是要動 page table、要走額外的 TLB。

關鍵直覺一句話：**kmalloc 給你的位址在「direct map」區（虛擬和實體差一個固定 offset，所以實體連續）；vmalloc 給你的位址在「vmalloc 區」（每頁自己映射，實體零散）。** 這個差異等下動手時會親眼看到位址範圍完全不同。

> 如果你上過 `kernel_pwn`：你在那門課「喬 slab、湊 cross-cache、打 UAF」操作的那個 heap，就是本章 `kmalloc` 底下的 slub。差別是那門課從攻擊方看「怎麼把 free list 弄成我要的形狀」，這門課從設計方看「這些 API 為什麼長這樣、各自去哪拿記憶體」。同一個東西，兩個視角，合起來你才真的懂它。

## kmalloc / kfree：預設選項

`include/linux/slab.h`。這是你 90% 情況會用的 API：

```c
void *p = kmalloc(size, GFP_KERNEL);
if (!p)
        return -ENOMEM;      // 一定要檢查！kernel 配置失敗回 NULL
/* ... 用 p ... */
kfree(p);                    // 配了就要放，否則 leak（Ch 53 kmemleak 抓）
```

`kmalloc` 的性格：

- **實體連續**：回傳的位址落在 direct map，虛擬連續 ⇒ 實體也連續。可以安全交給需要實體位址的硬體（配 DMA API 用，Ch 41）。
- **快**：走 slub，熱路徑上多半從 per-CPU 的 cache 直接切一塊出來，不碰鎖（per-CPU 見 Ch 7）。
- **有大小上限**：這是它最重要的限制。`kmalloc` 底下是一組固定尺寸的 slab cache（`kmalloc-64`、`kmalloc-128`、`kmalloc-256`……見 `/proc/slabinfo`）。超過 `KMALLOC_MAX_CACHE_SIZE`（在多數 x86_64 config 下是兩頁，即 8 KB）的請求，`kmalloc` 不再走 cache，而是直接轉去向 buddy 要 page；再大到超過 buddy 能給的最大 order（`MAX_PAGE_ORDER`，預設讓單次上限約 4 MB）就會失敗回 NULL。

**為什麼有上限、為什麼要實體連續**，是同一件事的兩面：要一塊「實體連續」的大記憶體，等於要 buddy 湊出一長串相鄰的空閒 page。系統跑久了記憶體碎片化，湊 8 KB 連續還好，湊 1 MB 連續可能就湊不出來——即使空閒記憶體總量夠。所以「需要大塊、但不需要實體連續」的場景，就是 `vmalloc` 存在的理由。

> **對齊**：`kmalloc` 保證回傳位址至少對齊到 `ARCH_KMALLOC_MINALIGN`（x86_64 上通常 8 或 16 bytes），而且對於是 2 的次方的 size，會對齊到該 size。需要 page 對齊就別用 kmalloc，用 `__get_free_pages`（回傳必然是 page 對齊）。

`kzalloc(size, gfp)` 就是 `kmalloc(size, gfp | __GFP_ZERO)`——配好順便清零。要清零就用它，別自己 `kmalloc` 完再 `memset`（多一次掃過、也容易忘）。

**源碼路徑速覽（給想追下去的人）**：`kmalloc` 在 `include/linux/slab.h` 是個 `static inline`。對編譯期就是常數的 size，它會用 `kmalloc_index()` 在編譯期算出該落哪個 cache（`kmalloc-64`/`kmalloc-128`……），直接呼叫 `__kmalloc_cache_noprof()`；size 是變數時走 `__kmalloc_noprof()`（`mm/slub.c`；6.12 因記憶體配置 profiling 重構，這些內部函式都帶了 `_noprof` 後綴，舊名 `kmalloc_trace`/`__kmalloc`）。超過 `KMALLOC_MAX_CACHE_SIZE` 的請求則轉呼叫 `kmalloc_large()` → 直接找 buddy 拿整批 page。也就是說 `kmalloc` 是個「小的走 slub、大的自動繞過 slub 走 buddy」的 façade。這條路的內部（per-CPU slab、free list、redzone）是 Ch 18 的主題，這裡先知道入口在 `slab.h`、大小分流的門檻是 `KMALLOC_MAX_CACHE_SIZE`。

## vmalloc / vfree：大而不需連續

`include/linux/vmalloc.h`，實作在 `mm/vmalloc.c` 的 `__vmalloc_node_range()`。

```c
void *big = vmalloc(4 * 1024 * 1024);   // 4 MB，kmalloc 幾乎不可能給
if (!big)
        return -ENOMEM;
/* ... */
vfree(big);
```

`vmalloc` 的性格，基本上和 `kmalloc` 相反：

- **虛擬連續、實體零散**：向 buddy 逐頁（或小批）要 page，這些 page 實體位址東一塊西一塊，vmalloc 在 vmalloc 虛擬區裡建 page table 把它們縫成連續。**因此絕對不能把 vmalloc 的 buffer 拿去做需要實體連續的 DMA。**
- **慢**：要動 page table、每次跨頁存取可能多一次 TLB miss。配置本身也比 kmalloc 慢（要建映射）。
- **能給大塊**：正是它存在的理由。碎片化的系統上，湊「一堆零散 page」遠比湊「一長串連續 page」容易。
- **一定會睡**：vmalloc 內部要配 page table、可能觸發 reclaim，**只能在能睡的 context 呼叫**（process context），不能在中斷或持 spinlock 時用。它甚至沒有給你選 `GFP_ATOMIC` 的餘地。

什麼時候真的該用 vmalloc？大型 buffer 且**硬體不碰它**：模組本身的 text/data（模組載入器就是用 vmalloc 區，Ch 8）、大型 hash table、`/proc` 產生的大塊臨時緩衝。日常資料結構請優先 kmalloc——vmalloc 的 TLB 與 page table 成本不是免費的。

一個常被忽略的成本：vmalloc 區的每一頁都要在 kernel page table 裡有對應的 PTE，配置時要建、釋放時要拆，還可能要做 TLB flush（Ch 23）。大量小的 vmalloc 會把 vmalloc 區的位址空間和 page table 撐得很碎——所以「拿 vmalloc 當 kmalloc 用來配一堆小物件」是反模式。vmalloc 是給「單筆就很大」的配置用的。

## __get_free_pages / alloc_pages：頁級，直接對 buddy

當你要的就是「整數個 page」，跳過 slab 直接找 buddy：

```c
#include <linux/gfp.h>
/* 要 2^order 個連續 page；order=0 就是一頁 */
unsigned long addr = __get_free_pages(GFP_KERNEL, 2);   // 2^2 = 4 頁 = 16 KB
if (!addr)
        return -ENOMEM;
/* addr 是可直接解參考的 kernel 虛擬位址 */
free_pages(addr, 2);                                     // order 要對得上！
```

- `__get_free_pages(gfp, order)` 回傳**虛擬位址**（direct map 內，實體連續、page 對齊）。
- `alloc_pages(gfp, order)` 回傳的是 **`struct page *`**（Ch 17 的核心結構），不是位址；要解參考得再 `page_address()`。需要對 page 本身做事（放進 page cache、給 mmap）用這個；只是要一塊 page 對齊的連續記憶體用 `__get_free_pages`。
- **`order` 不是 byte 數，是「2 的指數」**：`order=0` 一頁、`order=3` 八頁。`free` 時的 order 必須和 alloc 時完全一致，否則 buddy 的 free list 會被你搞爛（這是常見的難查 bug）。

幾個常見的便捷變體，看到別困惑：`__get_free_page(gfp)`（單數，沒有 `s`）= `__get_free_pages(gfp, 0)`，只要一頁；`get_zeroed_page(gfp)` = 要一頁且清零；`free_page(addr)` = `free_pages(addr, 0)`。單頁是最常見的需求，所以有專門的簡寫。

什麼時候用頁級 API 而不是 kmalloc？你就是要 page 對齊、或要整頁來映射（自己建 mapping、實作 mmap、做 ring buffer）。一般小物件不要用——會浪費（要 100 bytes 卻拿一整頁）。

## kvmalloc：先 kmalloc 失敗退 vmalloc

`mm/util.c` 的 `kvmalloc_node()`。這是「我要一塊中大型記憶體，連不連續我不在乎（不做 DMA），但我不想每次都吃 vmalloc 的慢」的答案：

```c
void *p = kvmalloc(size, GFP_KERNEL);   // 先試 kmalloc
if (!p)
        return -ENOMEM;
/* ... */
kvfree(p);                              // 對應 kvfree，它會判斷 p 在哪個區、正確釋放
```

`kvmalloc` 的策略：**先試 `kmalloc`（快、實體連續），如果因為太大或碎片化拿不到，自動退回 `vmalloc`。** 所以 size 小時你享受 kmalloc 的速度，size 大或系統緊時你仍拿得到記憶體（退到 vmalloc）。內部還會對「明顯太大」的請求（超過門檻）直接走 vmalloc，避免對 slub 施加不合理壓力。

用它的**前提**：這塊記憶體不會拿去做需要實體連續的事（因為可能落在 vmalloc 區）。kernel 裡很多「大小由 userspace 或設定決定、不確定會多大」的配置都改用了 kvmalloc（例如某些 syscall 的參數 buffer），就是為了「小的時候快、大的時候不炸」。釋放**一律用 `kvfree`**，別自己判斷該 kfree 還 vfree。

## 幾個常一起出現的配套 API

主線之外，有幾個你在真實模組裡很快會撞到的配套函式，值得先認個臉：

- **`krealloc(p, new_size, gfp)`**（`mm/slub.c`）：像 userspace 的 `realloc`——擴大/縮小一塊 `kmalloc` 來的記憶體，內容盡量保留。注意它可能回傳**新位址**（原塊放不下就配新塊+搬資料+free 舊塊），所以務必用回傳值覆蓋原指標，別繼續用舊的（否則 UAF）。失敗回 NULL 且**不動原塊**，所以 `p = krealloc(p, ...)` 這種寫法會在失敗時 leak 原塊——嚴謹寫法要用暫存指標接。
- **`ksize(p)`**（`mm/slub.c`）：問「這塊 `kmalloc` 出來的記憶體實際上有多大」。因為 slub 是按固定尺寸 cache 切的，你要 130 bytes 實際拿到 192 bytes，`ksize` 會回 192。少數想「既然多給了就用滿」的場景有用，但別依賴它做邊界——超過你當初要的 size 去寫是壞習慣。
- **`kmemdup(src, len, gfp)` / `kstrdup(s, gfp)`**：配一塊 + 複製，一步到位，比 `kmalloc` 完再 `memcpy` 少出錯。複製 userspace 傳來的 buffer 時常用。
- **`devm_kmalloc(dev, size, gfp)` / `devm_kzalloc(...)`**（`drivers/base/devres.c`）：**受管（managed）配置**，把這塊記憶體綁在一個 `struct device` 的生命週期上——裝置 detach / driver `remove` 時自動釋放，你不用寫對應的 free。寫驅動（Ch 37 起）時大量使用，能消掉一整類「錯誤路徑忘了釋放」的 bug。代價是它只適合綁裝置生命週期的配置，不是萬用替代 kmalloc。

這些不是主角，但看到它們時要知道：`krealloc` 會換位址、`ksize` 揭露內部碎片、`devm_*` 幫你自動 free。

還有一組你會在集合/陣列配置看到的：`kcalloc(n, size, gfp)` 和 `kmalloc_array(n, size, gfp)`。它們配的是「n 個 size」的陣列，關鍵是**內建溢位檢查**——`n * size` 若溢位 `size_t`，函式回 NULL 而不是配一塊小到爆的 buffer（那會導致後續寫越界，是經典漏洞）。所以配陣列時用 `kmalloc_array`/`kcalloc`，別自己算 `kmalloc(n * size, ...)`——後者的乘法可能悄悄溢位。`kcalloc` 額外清零（= `kmalloc_array` + `__GFP_ZERO`）。這是「讓 API 幫你擋掉一整類安全 bug」的例子，能用就用。

## 底層機制：GFP flags 到底在說什麼

GFP = **Get Free Pages**。每個配置 API 的最後一個參數 `gfp_t gfp` 不是「配多少」，而是「**用什麼態度去配**」——尤其是「拿不到時你准我做什麼」。定義在 `include/linux/gfp_types.h`。

先看兩個最底層、決定「能不能睡」的 modifier flag：

- `__GFP_DIRECT_RECLAIM`：准我**在當前這個呼叫裡直接去做回收**——這動作會睡（要等 I/O 把 dirty page 寫回、等別的 task）。
- `__GFP_KSWAPD_RECLAIM`：准我**叫醒背景的 kswapd** 去慢慢回收，但我自己不等、不睡。

常用的 GFP 就是這些 modifier 的組合。記住這張對照表勝過背定義：

```
   flag            能睡?  誰去回收?          典型 context          拿不到時
   ───────────     ────  ──────────────     ──────────────────    ──────────────
   GFP_KERNEL      能睡   自己直接回收+kswapd process context      等到有為止(除非真的OOM)
                         (DIRECT | KSWAPD)  (能睡的一般路徑)

   GFP_ATOMIC      不睡   只叫醒 kswapd      中斷/持 spinlock       立刻回 NULL
                         (只 KSWAPD_RECLAIM  (不能睡的 context)     (但有 emergency
                          + __GFP_HIGH)                            reserve 可動用)

   GFP_NOWAIT      不睡   只叫醒 kswapd      不想睡、也不想動用      立刻回 NULL
                         (只 KSWAPD_RECLAIM  emergency reserve      (比 ATOMIC 更保守)
                          無 __GFP_HIGH)     的場合
```

再一個獨立的 content modifier，可疊加在上面任何一個：

- `__GFP_ZERO`：配到的記憶體先清零（`kzalloc` 就是 `kmalloc | __GFP_ZERO`）。

**這三個的差別，本質是 Ch 2「這個 context 能不能睡」的直接編碼：**

- **`GFP_KERNEL`**：`__GFP_DIRECT_RECLAIM | __GFP_KSWAPD_RECLAIM | __GFP_IO | __GFP_FS`。它准 kernel「為了湊出記憶體可以睡」——沒空閒 page 就當場去回收、去等 I/O、睡著等。所以它幾乎不會回 NULL（除非系統真的到 OOM）。**代價：呼叫者必須在能睡的 context（process context，沒持 spinlock）。** 這是你在模組 `init`、syscall 處理、workqueue 裡的預設選擇。

- **`GFP_ATOMIC`**：`__GFP_HIGH | __GFP_KSWAPD_RECLAIM`。關鍵是**沒有 `__GFP_DIRECT_RECLAIM`**——它保證「配置過程絕不睡」。拿不到就立刻回 NULL，頂多叫醒 kswapd 之後慢慢補。`__GFP_HIGH` 讓它在緊急時可以動用一部分保留給關鍵路徑的 emergency reserve。**用在你不能睡的地方：中斷處理常式（hardirq/softirq）、持有 spinlock 的臨界區。** 見 Ch 25——持 spinlock 時睡著，別的 CPU 又在自旋等這把鎖，就是經典死鎖。

- **`GFP_NOWAIT`**：`__GFP_KSWAPD_RECLAIM`（6.12 起還帶 `__GFP_NOWARN`）。比 `GFP_ATOMIC` 更保守——一樣不睡，但**連 emergency reserve 都不動用**（沒有 `__GFP_HIGH`）。用在「配不到也無所謂、我有 fallback、且不想吃掉緊急保留」的地方。

**一句判斷法則：問自己「我現在能不能睡？」** 能睡 → `GFP_KERNEL`；不能睡（在中斷裡、或持著 spinlock）→ `GFP_ATOMIC`。這個判斷你在 Ch 2 已經學過怎麼做，這裡只是把答案填進 gfp 參數。

在真實源碼裡驗證這個直覺：去 Bootlin 搜 `GFP_ATOMIC`，你會發現它幾乎只出現在網路收包路徑（softirq 裡配 `sk_buff`，Ch 44）、中斷 handler、以及各種持著 spinlock 的臨界區——都是「不能睡」的地方。而絕大多數的一般路徑用的是 `GFP_KERNEL`。這不是巧合：`GFP_ATOMIC` 是「被迫不能睡」時的無奈選擇，不是效能優化。能睡就該讓它睡（去回收記憶體），配置成功率才高。

> **為什麼中斷/持鎖時睡會死鎖**（接 Ch 25）：睡 = 讓出 CPU 給排程器挑下一個 task。但（1）中斷 context 沒有一個「可被喚醒的 task」身分，排程器根本不知道回來時要恢復誰，直接 `BUG`；（2）你持著的 spinlock，別的 CPU 正在忙等（busy-wait）它，你睡過去這鎖就永遠不放，那顆 CPU 原地自旋到天荒地老。所以 `GFP_KERNEL`（可能睡）在這些地方是禁忌——kernel 開了 `CONFIG_DEBUG_ATOMIC_SLEEP` 會當場噴 `sleeping function called from invalid context` 警告，我們等下會親手觸發它。

### GFP_KERNEL 拿不到 page 時，那個「睡」到底在等什麼

把「`GFP_KERNEL` 幾乎不失敗、但可能睡」這句話拆開看。當 buddy 的 free list 一時湊不出你要的 page，帶 `__GFP_DIRECT_RECLAIM` 的請求會走進**回收路徑**（Ch 22 深入），大致是：

```
  kmalloc(size, GFP_KERNEL)
        │  free list 有現貨？──── 有 ──► 立刻回，沒睡
        │                        └─ 沒有 ▼
        │  __GFP_DIRECT_RECLAIM 准我當場回收
        ▼
  try_to_free_pages()  ── 掃 LRU、把 clean page 丟掉、把 dirty page 寫回磁碟
        │                                          │
        │                                    寫回是 I/O，要「等」──► 睡在這裡
        ▼
  回收出足夠 page ──► 回頭配置、成功回傳
        │
        └─ 怎麼回收都不夠 ──► 喚醒 OOM killer 挑一個 process 殺掉（Ch 22）
```

看清楚：`GFP_KERNEL` 的「不失敗」是拿「肯睡、肯等 I/O、必要時觸發 OOM」換來的。這也解釋了為什麼中斷/持鎖處不能用它——你根本沒有本錢停在 `try_to_free_pages()` 裡等磁碟寫回。而 `GFP_ATOMIC` 走的是完全不同的短路徑：free list 沒現貨就頂多動一下 emergency reserve，還是沒有就立刻 NULL，一步都不進回收。這一節先建立「GFP 選擇會一路影響到走不走回收路徑」的整體感，細節留 Ch 22。

## 對比與取捨

| API | 底層去哪 | 實體連續 | 大小上限 | 對齊 | 可用 context | 回傳 |
|---|---|---|---|---|---|---|
| `kmalloc`/`kzalloc` | slab/slub (Ch 18) | 是 | ~8 KB 走 cache，再大轉 buddy（上限約 4 MB） | ≥ MINALIGN；2^n size 對齊到 size | 依 gfp（可 ATOMIC） | 虛擬位址 |
| `vmalloc` | buddy + 建 page table | **否** | 很大（受 vmalloc 區大小限） | page 對齊 | **只能能睡的 context** | 虛擬位址 |
| `__get_free_pages` | buddy (Ch 17) | 是 | `2^MAX_PAGE_ORDER` 頁 | page 對齊 | 依 gfp（可 ATOMIC） | 虛擬位址 |
| `alloc_pages` | buddy (Ch 17) | 是 | 同上 | page 對齊 | 依 gfp（可 ATOMIC） | `struct page *` |
| `kvmalloc` | 先 slab 再 vmalloc | **不保證** | 很大 | 視落點 | **只能能睡的 context** | 虛擬位址 |

選擇的決策樹：

1. **要交給硬體做 DMA / 需要實體連續？** → `kmalloc`（小）或 `__get_free_pages`（要 page 對齊/整頁）。不能用 vmalloc / kvmalloc。
2. **一般資料結構、小於幾 KB、不需 page 對齊？** → `kmalloc`（要清零 `kzalloc`）。這是預設。
3. **需要很大、且不做 DMA、大小不確定？** → `kvmalloc`（小快、大不炸）。
4. **就是要整數個 page、page 對齊？** → `__get_free_pages`（要位址）或 `alloc_pages`（要 struct page）。
5. **超大、明知不連續也行、且確定在能睡的 context？** → `vmalloc`。

把這棵樹壓成兩句話：**預設用 `kmalloc`（要清零就 `kzalloc`）；只有「大 + 不做 DMA」才需要考慮 `kvmalloc`/`vmalloc`，「要整頁」才用 `__get_free_pages`。** 絕大多數模組程式碼一輩子只需要 `kmalloc`/`kzalloc`/`kfree` 這三個。其餘 API 是為特定需求存在的，不是每次配置都要在五個裡挑——先問「有沒有 DMA、有沒有很大、能不能睡」，答案通常直接指向 `kmalloc`。

## 動手：親眼看四種配置去了哪

寫一個模組，把四種 API 各配一次，印出位址，看它們落在完全不同的區間。

```c
// memtour.c
#include <linux/init.h>
#include <linux/module.h>
#include <linux/slab.h>
#include <linux/vmalloc.h>
#include <linux/gfp.h>

static int __init memtour_init(void)
{
        void *k, *v, *kv;
        unsigned long pg;

        k  = kmalloc(128, GFP_KERNEL);
        v  = vmalloc(4 * 1024 * 1024);          // 4 MB
        pg = __get_free_pages(GFP_KERNEL, 2);   // 4 頁
        kv = kvmalloc(1024 * 1024, GFP_KERNEL); // 1 MB

        if (!k || !v || !pg || !kv) {
                pr_err("memtour: some alloc failed\n");
                goto out;   // 真實模組這裡要逐一檢查+釋放，示範從簡
        }

        pr_info("memtour: kmalloc(128)        = %px\n", k);
        pr_info("memtour: __get_free_pages(o2) = %px\n", (void *)pg);
        pr_info("memtour: vmalloc(4M)          = %px\n", v);
        pr_info("memtour: kvmalloc(1M)         = %px\n", kv);
out:
        kfree(k);
        vfree(v);
        free_pages(pg, 2);   // order 2，要和 alloc 對上
        kvfree(kv);
        return 0;
}

static void __exit memtour_exit(void) { pr_info("memtour: bye\n"); }

module_init(memtour_init);
module_exit(memtour_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Tour of kernel allocation APIs");
```

> `%px` 印未經雜湊的原始指標（`%p` 預設會雜湊化以防資訊洩漏）。**只在除錯用 `%px`，正式程式碼別留**——把 kernel 位址印給 userspace 是 `kernel_pwn` 課裡用來繞過 KASLR 的漏洞來源。

編、載入、看：

```
/ # insmod /memtour.ko
/ # dmesg | tail -5
memtour: kmalloc(128)        = 0xffff8880xxxxxxxx   <- direct map 區
memtour: __get_free_pages(o2) = 0xffff8880xxxxxxxx   <- 也在 direct map
memtour: vmalloc(4M)          = 0xffffc900xxxxxxxx   <- vmalloc 區，完全不同段
memtour: kvmalloc(1M)         = 0xffff8880xxxxxxxx   <- 1M 還進得了 kmalloc,落 direct map
```

看位址前綴：`kmalloc` 和 `__get_free_pages` 都在 `0xffff8880...`（direct map，實體連續）；`vmalloc` 跳到 `0xffffc900...`（vmalloc 區）。這就是「實體連續 vs 實體零散」在位址上的直接證據。x86_64 各記憶體區的位址佈局見 `Documentation/arch/x86/x86_64/mm.rst`。

再對照 `/proc/meminfo` 看整體變化：

```
/ # grep -E 'VmallocUsed|Slab|MemFree' /proc/meminfo
```

載入前後各看一次：`VmallocUsed` 會漲約 4 MB（你 vmalloc 的量）、`Slab` 反映 slab 佔用。`cat /proc/slabinfo | grep kmalloc` 能看到 `kmalloc-128` 等各尺寸 cache 的使用量——你 `kmalloc(128)` 就從 `kmalloc-128` 這個 cache 切出來（Ch 18 詳解）。

### 用 gdb 停在 kmalloc，看它怎麼分流

把 Ch 0 的 QEMU + gdb 環境接上，親眼看「小走 slub、大轉 buddy」這個分流。在 host 的 gdb 裡：

```gdb
(gdb) break __kmalloc_noprof           # size 是變數時的入口（mm/slub.c；6.12 因 alloc profiling 改名，舊名 __kmalloc）
(gdb) continue
```

回到 QEMU `insmod /memtour.ko`，gdb 會停在 `__kmalloc_noprof`。`backtrace` 看是誰呼叫進來的、`print size` 看這次要多少。再對照 `vmalloc`：

```gdb
(gdb) break vmalloc
(gdb) continue
(gdb) backtrace                        # 看 vmalloc → __vmalloc_node_range 的呼叫鏈
```

你會看到 `vmalloc` 內部一路走到 `__vmalloc_node_range()`（`mm/vmalloc.c`），裡面既向 buddy 要零散 page、又去建 page table——這正是「vmalloc 比 kmalloc 慢」的具體來源。用 gdb 看一次，比讀十遍描述都清楚。

### 故意在 spinlock 裡用 GFP_KERNEL，看 kernel 罵你

這是本章最值得動手的部分——把「不能睡的地方用了會睡的配置」這個錯誤親手觸發。前提：build kernel 時開了 `CONFIG_DEBUG_ATOMIC_SLEEP=y`（Ch 0 的除錯 config 建議開）。

```c
#include <linux/spinlock.h>

static DEFINE_SPINLOCK(my_lock);

static int __init badgfp_init(void)
{
        void *p;
        spin_lock(&my_lock);              // 進入 atomic context（不可睡）
        p = kmalloc(128, GFP_KERNEL);     // 錯！GFP_KERNEL 可能睡
        spin_unlock(&my_lock);
        kfree(p);
        return 0;
}
```

`insmod` 後 `dmesg` 會看到類似：

```
BUG: sleeping function called from invalid context at mm/page_alloc.c:...
in_atomic(): 1, irqs_disabled(): 0 ...
```

kernel 明白告訴你：你在 atomic context（因為持著 spinlock）呼叫了一個可能睡的函式。**正確修法**：改成 `kmalloc(128, GFP_ATOMIC)`；或者更好——把配置移到鎖外面，只在鎖內做真正需要保護的短操作（持鎖時間越短越好，Ch 25）。這個警告以後你會在別人的 driver 裡反覆遇到，現在先自己制造一次，印象最深。

## 踩雷集錦

1. **錯誤直覺：「vmalloc 的 buffer 可以拿去做 DMA，反正也是連續的一塊」→ 正確：vmalloc 只是虛擬連續，實體零散。** 交給不認識 page table 的硬體做 DMA，它會照虛擬位址去讀實體，踩到別人的記憶體。DMA buffer 一律走 kmalloc / `dma_alloc_coherent`（Ch 41），永遠不要 vmalloc。

2. **錯誤直覺：「中斷處理裡配記憶體跟平常一樣用 GFP_KERNEL」→ 正確：中斷/持 spinlock 只能 GFP_ATOMIC。** `GFP_KERNEL` 可能睡，睡在中斷 context 直接 `BUG`，睡在持鎖時死鎖。判斷法則就一句：能不能睡？不能睡就 `GFP_ATOMIC`。開 `CONFIG_DEBUG_ATOMIC_SLEEP` 讓 kernel 幫你抓。

3. **錯誤直覺：「kmalloc 幾乎不會失敗，不用檢查回傳值」→ 正確：一定檢查 NULL。** `GFP_ATOMIC` 拿不到就回 NULL；大 size 也可能失敗。不檢查就解參考 = NULL pointer deref，kernel oops。這和 userspace `malloc` 一樣要檢查，只是失敗代價更大（帶走整個系統）。

4. **錯誤直覺：「配了就配了，模組卸載時系統會幫我清」→ 正確：kernel 沒有 per-process 自動回收，leak 就永久 leak。** userspace 程式結束，kernel 回收它所有記憶體；但 kernel 自己 leak 的記憶體，除非重開機否則不會回來。每個 `kmalloc` 都要配對 `kfree`，每條錯誤 return 路徑都要放乾淨（`goto err_free` 是慣用模式）。Ch 53 的 kmemleak 專門抓這個。

5. **錯誤直覺：「free_pages 的第二個參數隨便填，反正在放記憶體」→ 正確：order 必須和 alloc 時完全一致。** buddy 靠 order 知道你還幾頁、怎麼跟兄弟合併。order 填錯，free list 記帳就錯，後果是之後某次不相干的配置炸掉——極難回溯的 bug。`alloc_pages`/`__get_free_pages` 的 order 要和對應的 `free` 一字不差。

6. **double free / UAF**：`kfree` 同一指標兩次，或 `kfree` 後繼續用（use-after-free）。slub 的 free list 會被污染，輕則資料損壞、重則被利用成提權（這正是 `kernel_pwn` 課攻擊 slub 的入口）。`kfree` 後把指標設 NULL 是好習慣（`kfree(NULL)` 是安全的 no-op）。開 `CONFIG_KASAN`（Ch 53）能在第一時間抓到 UAF/double-free。

7. **錯誤直覺：「不確定能不能睡，那用 GFP_ATOMIC 最安全」→ 正確：GFP_ATOMIC 是給真的不能睡的地方，不是保險。** `GFP_ATOMIC` 不進 direct reclaim、容易配失敗（回 NULL），還會動用有限的 emergency reserve——濫用它讓真正需要 reserve 的關鍵路徑拿不到。在能睡的 process context 就老實用 `GFP_KERNEL`。判斷「能不能睡」不是憑感覺，是看你現在的 context（Ch 2）：在中斷 handler、在持 spinlock/rcu_read_lock 的臨界區才不能睡，其餘一般路徑都能。

8. **錯誤直覺：「krealloc 失敗了，原本的指標還在，沒事」→ 正確：`p = krealloc(p, ...)` 失敗時 p 被覆寫成 NULL，原塊 leak。** `krealloc` 失敗回 NULL 但**不釋放原塊**，直接覆寫原指標就把原塊弄丟了。要用暫存指標接：`tmp = krealloc(p, ...); if (!tmp) { /* p 還有效 */ } else p = tmp;`。

## 進階：再往深一層

- **`kmalloc` 的 size→cache 對應不是連續的**：cache 尺寸是 `8, 16, 32, 64, 96, 128, 192, 256...`（2 的次方加上 96/192 這種中間檔）。你要 130 bytes 會落到 `kmalloc-192`，浪費 62 bytes（內部碎片）。對大量、固定大小的物件，自己建專屬 slab cache（`kmem_cache_create`，Ch 18）比通用 kmalloc 省，而且能對齊、加 constructor。
- **NUMA 版本**：`kmalloc_node` / `alloc_pages_node` 可以指定從哪個 NUMA node 配，讓記憶體靠近會用它的 CPU（Ch 15）。不指定就用當前 node。韌體/嵌入式多半單 node，但伺服器 kernel 程式碼到處是 `_node` 變體。
- **`GFP_NOIO` / `GFP_NOFS`**：在 I/O path 或檔案系統內部配置時，要避免「回收時又觸發 I/O / 進 fs，遞迴回自己造成死鎖」。這兩個 flag 遮掉回收路徑裡的 I/O / fs 動作。寫 block/fs driver（Ch 34/36）會碰到。
- **`__GFP_NOWARN` / `__GFP_NORETRY` / `__GFP_RETRY_MAYFAIL`**：微調「失敗時要不要印警告、要不要重試」。大配置想自己處理失敗、不想 kernel 印一大串 warning 時會加 `__GFP_NOWARN`。
- **`GFP_DMA` / `GFP_DMA32`**：限定從低位實體位址的 zone（Ch 17）配置，給只能定址低位址的老硬體用（例如某些裝置只能 DMA 到 32-bit 位址空間）。現代裝置多半用 `dma_alloc_coherent` + `dma_mask`（Ch 41）處理定址範圍，直接用 `GFP_DMA` 的場景越來越少，但讀老 driver 會看到。
- **配置與釋放的對稱性是一種紀律**：`kmalloc↔kfree`、`vmalloc↔vfree`、`__get_free_pages↔free_pages`、`alloc_pages↔__free_pages`、`kvmalloc↔kvfree`、`kmem_cache_alloc↔kmem_cache_free`。混用（例如 `vfree` 一塊 `kmalloc` 來的記憶體）行為未定義、多半 crash。唯二例外：`kfree`/`vfree`/`kvfree` 對 NULL 都是安全 no-op，寫 error path 時不用先判斷。
- **面試常問**：「`kmalloc` 和 `vmalloc` 差在哪、各自何時用」是必考題；答題骨架就是本章那張對比表——連續性、大小、速度、能否睡、能否 DMA。追問「`GFP_KERNEL` 和 `GFP_ATOMIC` 差在哪」時，答「能不能睡（direct reclaim）」並舉「中斷/持 spinlock 只能 ATOMIC」，比背 flag 定義有說服力。

## 動手練習

1. **位址區間對照**：跑 `memtour.ko`，把四個位址抄下來，對照 `Documentation/arch/x86/x86_64/mm.rst` 的位址佈局，確認 kmalloc/`__get_free_pages` 落在 direct map、vmalloc 落在 vmalloc 區。用 gdb `p/x` 這些位址也可以。
2. **觸發並修好 atomic sleep 警告**：跑 `badgfp` 模組，在 `dmesg` 看到 `sleeping function called from invalid context`；然後把 `GFP_KERNEL` 改成 `GFP_ATOMIC` 重編，確認警告消失。再試「把配置移出鎖外」的修法，比較兩種修法。
3. **逼出 kmalloc 上限**：寫個迴圈 `kmalloc(size, GFP_KERNEL | __GFP_NOWARN)`，size 從 4 KB 倍增到 16 MB，記錄哪個 size 開始回 NULL；再把同一組 size 改用 `vmalloc` 跑，看 vmalloc 能給到多大。體會「連續 vs 零散」在可配大小上的差距。
4. **kvmalloc 的退回行為**：`kvmalloc` 一個小 size（比如 1 KB）和一個大 size（比如 32 MB），各印位址，看小的落 direct map（走了 kmalloc）、大的落 vmalloc 區（退回了 vmalloc）。
5. **(選) leak 給 kmemleak 看**：故意 `kmalloc` 不 `kfree`，開 `CONFIG_DEBUG_KMEMLEAK`，`echo scan > /sys/kernel/debug/kmemleak` 後 `cat` 它，看 kmemleak 報出你漏掉的那塊（Ch 53 深入）。
6. **量內部碎片**：對一組 size（如 24、100、130、200 bytes）各 `kmalloc` 一塊，用 `ksize()` 印出實際拿到多大，算出每筆浪費多少。你會直觀看到「130 → 192，浪費 62 bytes」這種 slab 尺寸階梯造成的內部碎片，理解 Ch 18 為什麼要做專屬 cache。
7. **(選) double-free 給 KASAN 抓**：開 `CONFIG_KASAN`，寫一個 `kfree(p); kfree(p);` 的模組，看 KASAN 報 `double-free or invalid-free`，讀它印出的 allocation/free 兩處 stack trace——這正是 `kernel_pwn` 課裡你要從防禦方理解的那個原語（Ch 53 深入）。

## 本章重點整理

- kernel 沒有 `malloc`。四個常用 API 各有性格：`kmalloc`（小、實體連續、快、走 slab）、`vmalloc`（大、虛擬連續實體零散、慢、只能能睡時用）、`__get_free_pages`/`alloc_pages`（頁級、直接 buddy）、`kvmalloc`（先 kmalloc 失敗退 vmalloc）。
- 選 API 先問兩題：**要不要實體連續（做 DMA 就要）**、**現在能不能睡**。前者決定 kmalloc/pages vs vmalloc，後者決定 GFP flag。
- GFP flag 的核心是「能不能睡」：`GFP_KERNEL`（能睡、會 direct reclaim、幾乎不失敗，用在 process context）vs `GFP_ATOMIC`（不睡、拿不到回 NULL，用在中斷/持 spinlock）。這是 Ch 2 context 規則的直接編碼。
- 配了必 free、order 要對、回傳值必檢查 NULL、vmalloc 不做 DMA、`kfree` 後設 NULL 防 UAF——這五條省掉大半難查的記憶體 bug。
- 配置/釋放函式要成對（`kmalloc↔kfree`、`vmalloc↔vfree`、`kvmalloc↔kvfree`、`__get_free_pages↔free_pages`），混用行為未定義；配陣列用 `kmalloc_array`/`kcalloc` 借它的溢位檢查。

## 自我檢核

- [ ] 不看筆記，能說出 `kmalloc` 和 `vmalloc` 的五個差異（連續性、大小、速度、能否睡、能否 DMA）
- [ ] 能解釋「為什麼在中斷處理常式或持 spinlock 時只能用 `GFP_ATOMIC`」——講得出「睡了會怎樣」
- [ ] 面試被問「`GFP_KERNEL` 和 `GFP_ATOMIC` 的本質差別」，你的一句話答案是什麼？
- [ ] 能說出 `kmalloc` 為什麼有大小上限、這上限和「實體連續」的關係
- [ ] 知道 `kvmalloc` 的策略，以及用它的前提（不做 DMA）
- [ ] 能親手觸發 `sleeping function called from invalid context` 並用兩種方式修好
- [ ] 能說出配陣列時為什麼該用 `kmalloc_array`/`kcalloc` 而不是自己算 `kmalloc(n * size, ...)`

## 延伸閱讀

### 官方文件

- **[Documentation/core-api/memory-allocation.rst](https://www.kernel.org/doc/html/latest/core-api/memory-allocation.html)**
  - **讀哪裡**：整篇。這是 kernel 官方「該用哪個配置 API、該用哪個 GFP flag」的決策指南，和本章的對比表互為印證
  - **和本章的關聯**：本章的選擇邏輯就是這篇的展開；遇到不確定該用哪個 flag 時回來查這篇的決策樹

- **[Documentation/core-api/mm-api.rst](https://www.kernel.org/doc/html/latest/core-api/mm-api.html)**
  - **讀哪裡**：`kmalloc`/`kvmalloc`/`vmalloc`/`__get_free_pages` 各函式的參數與語意
  - **能學到什麼**：每個 API 的精確 contract（對齊保證、失敗行為），寫真實模組時的權威參考

- **`include/linux/gfp_types.h`（Bootlin 看 [v6.12](https://elixir.bootlin.com/linux/v6.12/source/include/linux/gfp_types.h)）**
  - **讀哪裡**：`GFP_KERNEL`/`GFP_ATOMIC`/`GFP_NOWAIT` 的定義，看它們是哪些 `__GFP_*` 的 OR
  - **為什麼值得讀**：親眼確認「`GFP_KERNEL` 有 `__GFP_DIRECT_RECLAIM`、`GFP_ATOMIC` 沒有」——這一個位元就是「能不能睡」的全部差別

### 書籍

- **《Linux Kernel Development, 3rd Ed.》** — Robert Love，第 12 章「Memory Management」
  - **這章的定位**：把 kmalloc/vmalloc/gfp/slab 用白話串起來的最佳補充；概念層講得比源碼註解清楚
  - **注意**：講較舊 kernel，slub 細節和 6.12 有出入，但 kmalloc/vmalloc 的取捨與 GFP 語意至今適用

- **《Understanding the Linux Virtual Memory Manager》** — Mel Gorman
  - **讀哪裡**：slab allocator 與 page 配置那幾章
  - **前提**：這是把 mm 推到極致的深水區，適合本章讀完、進到 Ch 17/18 之前或之後回來加深

### 文章

- **[LWN: The GFP_ flags（搜 "GFP flags" on lwn.net）](https://lwn.net/Kernel/Index/)**
  - **讀哪裡**：LWN Kernel index 裡關於 memory allocation / GFP 的系列文
  - **為什麼值得讀**：GFP flag 的語意在歷史上多次調整（`GFP_ATOMIC` 的 reserve 行為、`__GFP_DIRECT_RECLAIM` 的引入），LWN 記錄了「為什麼這樣改」，比只看當前定義更懂設計意圖

配置記憶體是每個子系統的地基動作，但你有沒有注意到——`kmalloc` 在熱路徑上「多半從 per-CPU cache 直接切一塊，不碰鎖」？那個「per-CPU、不碰鎖」正是 kernel 對付並行的核心手法。下一章我們就來看 per-CPU 變數與 kernel 的並行本質。

→ [Ch 7 per-CPU 變數與 kernel 的並行本質](./07-per-cpu-and-concurrency.md)
