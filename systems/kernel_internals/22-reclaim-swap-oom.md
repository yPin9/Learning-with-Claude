# Ch 22 — reclaim：kswapd、swap、OOM killer

> **目標**：理解記憶體不夠時 kernel 怎麼「騰空間」——哪些頁能回收、LRU 怎麼近似頁老化、kswapd 背景回收 vs direct reclaim 同步回收的差別、匿名頁怎麼 swap out、shrinker 怎麼縮 slab、以及最後一道防線 OOM killer 怎麼選一個 process 殺。學完你能自己觸發一次完整的「壓力 → reclaim → swap → OOM」，並讀懂 dmesg 的 OOM 報告與 vmstat 的 si/so。

## 為什麼需要這個？

前面幾章我們一路在「配置」記憶體：buddy allocator 從 zone 切出實體頁（Ch 17）、slub 管小物件（Ch 18）、page fault 按需求把頁掛進行程位址空間（Ch 19/20）、page cache 把檔案內容留在記憶體裡加速（Ch 21）。這些機制有一個共同前提：**有空閒頁可以拿**。

問題是實體記憶體是有限的，而 kernel 的策略是**盡量把 RAM 用滿**——空著的 RAM 是浪費，page cache 會一路吃到幾乎沒有 free page 為止。所以「記憶體快用完」不是異常，是**穩態**。真正的問題是：當某個配置請求來了，free page 低於水位（Ch 17 的 watermark），kernel 得當場生出一些頁來。它去哪裡生？

答案是**回收（reclaim）**：找一些「現在還佔著頁、但其實可以放掉」的頁，把它們的內容處理掉（或存到別處），把實體頁還給 buddy allocator。這章就是講這套回收機制——它是 mm 子系統裡最像「作業系統在做資源決策」的部分，充滿取捨與啟發式規則（heuristic），而且它的最後一步——挑一個 process 殺掉——是整個 kernel 裡少數會直接、可見地傷害使用者程式的行為。

## 先建立直覺

先問一個根本問題：**一個實體頁，什麼情況下「可以放掉」？** 關鍵在於「這頁的內容，別的地方有沒有副本，或能不能重建」。按這個標準分三類：

```
   一個實體頁，能不能回收？看它的內容從哪來、能不能重建
   ─────────────────────────────────────────────────────────

   ┌── clean file page ──────── 檔案內容的快取，且未被改過
   │   磁碟上就是原本，記憶體這份沒動過
   │   → 直接丟！要用時再從磁碟讀回（Ch 21 page cache）
   │   回收成本：幾乎為零（改 page table，free 頁）
   │
   ├── dirty file page ──────── 檔案內容的快取，但被寫過、還沒落盤
   │   磁碟那份是舊的，記憶體這份才是最新
   │   → 先 writeback（寫回磁碟），變 clean，才能丟（Ch 21）
   │   回收成本：一次磁碟寫入（慢）
   │
   └── anonymous page ───────── 沒有對應檔案（malloc、stack、heap）
       磁碟上根本沒有它的副本
       → 沒地方丟！只能寫進 swap 空間，pte 改成 swap entry
       回收成本：一次磁碟寫入 + 未來換回還要一次讀入
```

還有第四類：**unevictable（不可回收）**——被 `mlock` 鎖住的頁、正在 DMA 的頁、kernel 自己的核心資料結構等，這些回收不了，直接排除在候選之外。

有了「哪些頁能回收」，下一個問題是**該回收哪一頁**。理想上要回收「最不會馬上被用到的頁」（最接近未來不再存取的頁），但 kernel 看不到未來。退而求其次，用過去近似未來：**最久沒被存取的頁，最可能繼續不被存取**——這就是 LRU（Least Recently Used，最近最少使用）。

但嚴格 LRU 太貴：每次存取一頁都要把它移到串列頭，這在每秒數百萬次記憶體存取的規模下是災難。所以 kernel 用的是**近似 LRU**，下一節細講。

最後把整條路徑串起來：

```
   記憶體壓力來了（配置時 free 低於 watermark）
   ────────────────────────────────────────────
                    │
        ┌───────────┴────────────┐
        ▼                         ▼
   低於 low watermark         配置當場沒頁
   喚醒 kswapd                （已跌破 min）
   （背景、非同步）            direct reclaim
        │                     （前景、同步、卡住配置者）
        └───────────┬────────────┘
                    ▼
            shrink_node / shrink_lruvec   （mm/vmscan.c）
                    │
        ┌───────────┼───────────────┬──────────────┐
        ▼           ▼               ▼              ▼
   掃 inactive   clean file     dirty file      anon page
   LRU list     → 直接 free    → writeback     → swap out
   （用 rmap                    再 free          pte→swap entry
    Ch 20 解除                                   （mm/swapfile.c）
    映射）
                    │
              也叫 shrinker 縮 slab
              （dentry/inode cache…）
                    │
                    ▼
          回收夠了？ ──是──► 配置成功，繼續
                    │
                   否（掃了好幾輪還是不夠）
                    ▼
              out_of_memory()          （mm/oom_kill.c）
              選一個 process 殺（oom_score 最高）
```

這張圖是本章骨架。接下來每一節填一塊。

## LRU 與頁老化：kernel 怎麼近似「最久沒用」

Kernel 不維護一條嚴格排序的 LRU 串列，而是用**兩條串列 + 二次機會**來近似。核心結構是 `struct lruvec`（定義在 `include/linux/mmzone.h`），它其實掛著**多條** LRU：

```
   struct lruvec（每個 memcg × 每個 node 一份）
   ─────────────────────────────────────────────
     LRU_INACTIVE_ANON   ─┐
     LRU_ACTIVE_ANON      ├── 匿名頁的 active / inactive
     LRU_INACTIVE_FILE   ─┐
     LRU_ACTIVE_FILE      ├── file 頁的 active / inactive
     LRU_UNEVICTABLE       ── 鎖住、回收不了的頁
```

`enum lru_list`（`include/linux/mmzone.h`）列出這幾條。為什麼要 anon 和 file 分開？因為兩者回收成本差很多（file clean 頁幾乎免費，anon 頁一定要 swap），kernel 想分開控制回收比例——這就是 `swappiness` 的作用（後面講）。

**active / inactive 的角色**，用「二次機會（second chance）」的心智模型理解最清楚：

```
   一頁的一生（file 頁為例）
   ──────────────────────────────────────────────

   新讀入 ──► inactive list 尾端
                  │
        被存取到？（page table 的 accessed bit 被 CPU 設起來）
          │是                        │否（一直待著）
          ▼                          ▼
     升級到 active list          慢慢往 inactive 頭移動
     （「這頁還熱，別急著回收」）    （list 頭 = 最接近被回收）
          │                          │
     一段時間沒再被存取              reclaim 掃到它
          ▼                          ▼
     降級回 inactive             回收（clean 直接丟 / dirty 先 writeback）
```

回收時 kernel **只掃 inactive list**。一頁要被回收，得先從 active 降到 inactive、在 inactive 待到被掃到、且那期間沒被再次存取。這給了熱頁「二次機會」——被存取過就升 active，逃過這一輪。實作在 `mm/vmscan.c` 的 `shrink_active_list()`（把 active 降級到 inactive）和 `shrink_inactive_list()`（掃 inactive 真正回收）。

「有沒有被存取」怎麼判斷？靠 page table entry 的 **accessed bit**（x86 的 PTE `_PAGE_ACCESSED`，ARM64 的 AF）——CPU 在存取一頁時硬體會把它設起來。kernel 掃描時檢查並清掉這個 bit（`folio_referenced()`，`mm/rmap.c`；舊名 `page_referenced` 已在 6.x 改為 folio 版，這裡就用到 Ch 20 的 rmap 反查所有映射這頁的 pte），下次再掃如果又被設起來，就知道這頁在兩次掃描之間被存取過。這是硬體幫 kernel 做的「近似時間戳」，成本遠低於嚴格 LRU 的每次存取都動串列。

**refault detection 與 workingset**：光靠 active/inactive 有個盲點——如果一頁被回收後**馬上又被要回來**（refault，回收得太早了），單純的 LRU 看不出這個錯誤。6.x 的 kernel 在回收 file 頁時記下一個「回收時的時間戳」（用 eviction 計數編碼進 xarray 的 shadow entry），頁被 refault 回來時比對，如果間隔很短，就知道這頁其實還在 workingset 裡、回收錯了，於是提高 file 頁的保護、少回收一點。這套在 `mm/workingset.c`。你不用背細節，但要有這個概念：**現代 reclaim 不是盲目 LRU，它會從「回收後馬上又要回來」的錯誤中學習**。

> **認識論誠實**：上面講的是傳統雙串列 LRU（active/inactive），這是 v6.12 的**預設**。但 6.1 起 kernel 引入了 **MGLRU（Multi-Gen LRU，多世代 LRU）**，用多個「世代（generation）」取代兩條串列，號稱回收決策更準、掃描更省。它要靠 `CONFIG_LRU_GEN` 開啟、且需在 `/sys/kernel/mm/lru_gen/enabled` 打開才生效，預設不開。本章講傳統 LRU 因為它是預設路徑、且概念更基礎；MGLRU 是同一個問題的另一種答案，`Documentation/admin-guide/mm/multigen_lru.rst` 有官方說明。知道它存在、知道它改寫了本節的串列結構就夠了，本課不深入。

## reclaim 路徑：shrink_node 怎麼掃

回收的主幹在 `mm/vmscan.c`。不管是 kswapd 還是 direct reclaim，最終都收斂到同一組函式：

```
   do_try_to_free_pages()            ← reclaim 的總入口（direct reclaim 走這）
       └─ shrink_zones()
           └─ shrink_node()          ← 針對一個 NUMA node 回收
               ├─ shrink_lruvec()    ← 掃這個 node（× memcg）的各條 LRU
               │    ├─ shrink_list(INACTIVE_ANON / ACTIVE_ANON …)
               │    │    └─ shrink_inactive_list()  ← 真正回收頁的地方
               │    │         └─ shrink_folio_list()  ← 逐頁決定命運
               │    └─ （依 swappiness 決定 anon vs file 各掃多少）
               └─ shrink_slab()      ← 呼叫所有註冊的 shrinker 縮 kernel 物件
```

`shrink_folio_list()`（v6.x 已從 page 改用 folio，folio 是一組連續頁的抽象）是**逐頁決定命運**的核心迴圈。對每一頁大致做這些事：

1. 用 `folio_check_references()` 檢查最近有沒有被存取（accessed bit / rmap），有的話升 active、跳過
2. 如果是**匿名頁**且沒 swap slot，配一個 swap slot（`add_to_swap()`），把它加進 swap cache
3. 用 **rmap（Ch 20 的 `try_to_unmap()`）** 反查所有映射這頁的 pte，逐一解除映射——把 pte 改成 swap entry（anon）或直接清空（file）
4. 如果頁是 **dirty**，發起 writeback（Ch 21）把內容寫回（file → 檔案；anon → swap）。注意：reclaim 路徑上遇到 dirty 頁通常是**啟動非同步 writeback 後先跳過這頁**，而不是同步等它寫完——同步等會讓 reclaim 卡在 I/O 上
5. 頁變 clean 且沒人映射了，從 page cache / swap cache 移除，`free` 回 buddy allocator

這裡把前三章全用上了：**rmap（Ch 20）反查映射、writeback（Ch 21）處理 dirty、buddy（Ch 17）收回頁**。reclaim 是這些機制的消費者，也是它們存在的理由之一。

## kswapd vs direct reclaim：背景 vs 前景

回收有兩個觸發時機，差別是**誰在等**，這對延遲影響巨大。

**kswapd**（`mm/vmscan.c` 的 `kswapd()`）是背景回收 kernel thread，**每個 NUMA node 一個**（`kswapd0`、`kswapd1`…，你可以 `ps aux | grep kswapd` 看到）。它的工作循環：

```
   kswapd 的一生（睡 → 醒 → 回收 → 再睡）
   ──────────────────────────────────────────

   平時：睡在 waitqueue 上，不佔 CPU
     │
     │  某次配置發現 free < low watermark
     ▼
   被 wakeup_kswapd() 叫醒
     │
     ▼
   balance_pgdat()：回收，直到 free 回到 high watermark 以上
     │  （回收到 high 而非只到 low，是為了「一次補足」，
     │    避免剛回到 low 又馬上被叫醒，來回震盪）
     ▼
   回到 high 以上 → 再去睡
```

關鍵：kswapd 是**非同步**的。它在背景把 free page 補起來，配置記憶體的 process **不需要等它**——process 拿到頁繼續跑，kswapd 在旁邊默默回收。這是理想情況。

**direct reclaim**（`__alloc_pages_slowpath()` 裡呼叫 `__alloc_pages_direct_reclaim()`，`mm/page_alloc.c`）是**前景、同步**的回收。當配置壓力大到 kswapd 補不上、free 已經跌破 min watermark，配置的 process 拿不到頁，這時它**自己下場回收**——在配置的呼叫路徑裡直接跑 `do_try_to_free_pages()`，回收到夠了才拿到頁繼續。

```
   兩種回收的延遲差異（誰在等？）
   ─────────────────────────────────────────────────────

   kswapd（背景）：
     process:  malloc ──► 拿到頁 ──► 繼續跑（沒感覺）
     kswapd:            └► 背景慢慢回收

   direct reclaim（前景）：
     process:  malloc ──►【卡住！自己回收，掃 LRU、等 writeback】──► 拿到頁 ──► 繼續
                          ↑ 這段就是延遲尖峰（latency spike）
```

**direct reclaim 是效能問題的常見來源**。它讓一個原本只是想 `malloc` 的 process 突然背上「掃 LRU + 可能等 I/O」的工作，造成無法預期的延遲尖峰。生產環境看到「偶爾某個請求慢了幾百毫秒」，direct reclaim 是頭號嫌疑犯。`/proc/vmstat` 的 `allocstall_*` 計數就是 direct reclaim 發生的次數，`pgscan_direct` vs `pgscan_kswapd` 對比能看出回收壓力是背景扛還是前景扛。理想狀態是 kswapd 扛下絕大多數，direct reclaim 幾乎不發生。

## swap：匿名頁換出去哪裡

Clean file 頁可以直接丟（磁碟有副本），但**匿名頁沒有磁碟副本**——它是 `malloc` 的 heap、是 stack、是沒有檔案背景的 `mmap(MAP_ANONYMOUS)`。要回收它，只能先把內容存到一塊專門的磁碟空間：**swap**（swap partition 或 swap file）。

swap 的資料結構（`mm/swapfile.c`、`include/linux/swap.h`）：

```
   swap 空間被切成固定大小的 swap slot（每個 = 一頁）
   ───────────────────────────────────────────────────

   swap out 一頁匿名頁：
     1. 配一個 swap slot（get_swap_page）
     2. 頁內容寫進那個 slot（一次磁碟寫）
     3. 原本的 pte【不再指向實體頁】，改成 swap entry：
          pte: [ swap_type | swap_offset | (present bit = 0) ]
              └ 記「在哪個 swap 裝置的哪個 slot」，但 present=0
     4. 實體頁 free 回 buddy

   process 之後又存取這個位址：
     → CPU 發現 pte present=0 → page fault
     → do_swap_page()（mm/memory.c，Ch 19 的 fault handler 分支）
     → 從 pte 解出 swap entry → 從 swap 讀回內容 → 配新實體頁
     → 修好 pte（present=1）→ 重跑那條指令
```

注意第 3 步的巧思：**pte 本身變成「這頁在 swap 的哪裡」的紀錄**。pte 的 present bit 清 0，CPU 存取時就會 fault；剩下的 bit 拿來存 swap type（哪個 swap 裝置）和 swap offset（第幾個 slot）。所以不需要額外的表來記「哪頁被換出去了」——pte 自己就是那張表。這是 Ch 19 `do_swap_page` 換回路徑的另一半，兩章接起來才是完整的 swap 循環。

**swap cache**：在 swap out / swap in 的過渡期，一頁可能「同時在記憶體、也在 swap 裡有 slot」。swap cache（`mm/swap_state.c`）追蹤這些頁，避免同一頁被重複 swap、也讓多個共享該頁的 process 換回時能命中同一份。

**swappiness**（`/proc/sys/vm/swappiness`，預設 60）：這個旋鈕調的是**回收時 anon 和 file 頁的傾向比例**。值域 0–200（6.4 起上限從 100 提到 200）：

- 高（例如 100+）：更願意 swap out 匿名頁，多留 file cache
- 低（例如 10）：盡量不 swap，優先回收 file 頁
- 0：幾乎不主動 swap 匿名頁（但記憶體真的很緊時仍可能 swap）

常見誤解是「swappiness=0 就完全不 swap」——不對，它只是強烈傾向不 swap，`vmscan.c` 的 `get_scan_count()` 在極端壓力下仍可能換出匿名頁以避免 OOM。真正完全關 swap 是 `swapoff -a`。

## shrinker：回收 kernel 自己的可回收物件

到目前為止講的都是「使用者資料的頁」（file cache、anon）。但 kernel 自己也吃了很多記憶體在**可回收的快取物件**上，最典型的是 **dentry cache 和 inode cache**（Ch 33 VFS）——你 `ls` 過的每個目錄項、開過的每個 inode 都可能被 cache 著，這些是 slab 上的 reclaimable 物件（Ch 18 的 `SLAB_RECLAIM_ACCOUNT`）。

這些物件不在 LRU 上，vmscan 掃 LRU 掃不到它們。機制是 **shrinker**：任何管理可回收快取的子系統，用 `register_shrinker()`（`mm/shrinker.c`）註冊一個 callback，reclaim 時 `shrink_slab()` 會呼叫所有註冊的 shrinker，請它們「縮小自己」。

```
   shrinker 的兩段式介面（struct shrinker）
   ────────────────────────────────────────
     .count_objects()  → 回報「我手上有幾個可回收物件」
     .scan_objects()   → 「請你回收 N 個」，實際去釋放

   shrink_slab() 依 count 回報的數量、按比例分配該回收多少，
   再呼叫 scan 去回收。回收壓力越大，要求各 shrinker 縮越多。
```

VFS 的 dentry/inode shrinker 在 `fs/dcache.c` / `fs/inode.c`；你也會在許多 driver、filesystem 看到 shrinker（GEM buffer、XFS 的 buffer cache 等）。重點認識：**reclaim 不只回收使用者頁，也會透過 shrinker 縮 kernel 的 slab 快取**，兩條路並行。`/proc/sys/vm/vfs_cache_pressure` 調的就是 kernel 回收 dentry/inode 的積極程度。

## OOM killer：救不了就殺

如果 kswapd 和 direct reclaim 都掃了、shrinker 也縮了，`do_try_to_free_pages()` 反覆嘗試仍生不出足夠的頁——記憶體真的用完了。這時 kernel 走到最後一步：`out_of_memory()`（`mm/oom_kill.c`），**挑一個 process 殺掉**，把它的記憶體全部釋放出來救全系統。

**為什麼要殺 process，不是讓 malloc 失敗？** 因為 Linux 預設 **overcommit**——`malloc` 回傳成功不代表實體頁真的存在，頁是等你第一次寫入時才 fault 進來（Ch 19/20 的 demand paging）。所以真正「沒記憶體」是發生在 page fault 的當下，那時那條指令已經在跑、沒有乾淨的失敗路徑可退。overcommit 讓系統能超額承諾（多數程式配了記憶體用不滿），代價就是承諾兌現不了時得靠 OOM killer 收拾。

**怎麼選要殺誰？** 核心是 **oom_score**：大致正比於「殺了它能釋放多少記憶體」——`oom_badness()`（`mm/oom_kill.c`）主要看 process 的 RSS（`get_mm_rss()`）+ swap + page table 用量。**吃記憶體最多的通常最先死**，因為殺它回本最多。可調整：

- `/proc/<pid>/oom_score`：kernel 算出來的最終分數（讀）
- `/proc/<pid>/oom_score_adj`：−1000 到 +1000 的調整值（寫）。設 **−1000** 等於「永不選我」（豁免）；設高正值等於「有事先殺我」。系統關鍵服務（sshd、資料庫）常設負值保命

**OOM 的爭議**：OOM killer 靠啟發式選 victim，它**不知道哪個 process 對你最重要**。經典災難是「跑到一半的資料庫因為它 RSS 最大被殺，而真正該死的是失控的批次任務」。而且被殺的 process 沒有機會善後（不是 signal 那種可捕捉的結束，是被 `SIGKILL`）。這就是為什麼許多人寧可**關掉 overcommit**（`vm.overcommit_memory=2`）讓 `malloc` 老實失敗、或用 cgroup 精確限制，也不想把命運交給 OOM 的猜測。

**cgroup memcg OOM**：這是現代（尤其容器）最重要的一環。cgroup v2（Ch 50）的 memory controller 給每個 cgroup 設 `memory.max`。當一個 cgroup 內的記憶體用量撞到上限，會先在**這個 cgroup 內部**做 reclaim；還是不夠就觸發 **memcg OOM**——只殺這個 cgroup 裡的 process，不動系統其他部分。這正是 `docker run -m 512m` 背後的機制：容器超過記憶體限制時，被殺的是容器裡的 process，host 和別的容器不受影響（Ch 50 細講）。`memory.events` 的 `oom` / `oom_kill` 計數就是 memcg OOM 發生的紀錄——你在 k8s 看到 pod 的 `OOMKilled` 狀態，源頭就是這裡。

## 動手：觸發一次 reclaim → swap → OOM

在 Ch 0 的 QEMU 環境裡（記得給它一塊 swap，否則直接跳到 OOM），我們親手把記憶體逼到爆。先確保有 swap 和觀測工具。

**準備 swap**（在 QEMU 的 initramfs shell 裡，或用一個 rootfs 較完整的 image）：

```bash
# 做一個 128MB 的 swap file 並啟用
dd if=/dev/zero of=/swapfile bs=1M count=128
mkswap /swapfile
swapon /swapfile
free -m                 # 應該看到 Swap: total 有值
cat /proc/swaps         # 確認 swap 裝置在線
```

**寫一個吃記憶體的程式**（比 stress 更能控制，也看得懂在做什麼）：

```c
// eatmem.c —— 每次配 10MB 並「寫入」（強迫 fault 進實體頁），直到系統受不了
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(void)
{
    size_t chunk = 10 * 1024 * 1024;   // 10 MB
    long total = 0;
    for (;;) {
        char *p = malloc(chunk);
        if (!p) { printf("malloc failed at %ld MB\n", total); break; }
        memset(p, 1, chunk);            // 關鍵：寫入才會真的佔實體頁（demand paging, Ch 20）
        total += 10;
        printf("allocated %ld MB\n", total);
        fflush(stdout);
        usleep(100000);                 // 放慢，方便另一個終端機觀測
    }
    pause();
    return 0;
}
```

`memset` 是重點：光 `malloc` 不寫入，因為 overcommit + demand paging，實體頁根本不會配（Ch 20）。寫入才會 fault、才會真的吃掉實體記憶體、才會壓出 reclaim。

**一邊跑，一邊在另一個終端機觀測**：

```bash
# 看 swap in / swap out（si/so 欄，單位 KB/s）
vmstat 1
# procs -----------memory----------  ---swap--   ...
#  r  b   swpd   free   buff  cache   si   so    ...
#              ↑ 用了多少 swap        ↑    ↑ 開始跳非零 = 正在 swap out

# 看 free / swap 用量
watch -n1 free -m

# 看某個 process 的 oom_score 隨用量升高
watch -n1 'cat /proc/$(pgrep eatmem)/oom_score'
```

**你會依序看到**：

1. free 一路降，`cache` 先被回收（clean file 頁最先犧牲）
2. vmstat 的 `so`（swap out）開始跳非零——匿名頁被換出去 swap 了，`swpd` 上升
3. 系統開始卡（direct reclaim 在前景掃，配置變慢）
4. swap 也快滿、實在生不出頁 → **OOM killer 出手**

**讀 OOM 報告**（`dmesg` 或 QEMU console）：

```bash
dmesg | tail -40
```

你會看到類似（節錄關鍵幾行）：

```
[  ...] eatmem invoked oom-killer: gfp_mask=0x..., order=0, oom_score_adj=0
[  ...] Mem-Info:                          ← 各 zone 的 free/watermark 現況
[  ...] Tasks state (memory values in pages):
[  ...] [  pid ]   uid  ...  rss  ... oom_score_adj name
[  ...] [   123]     0  ... 30000 ...             0 eatmem   ← 候選清單，rss 最大
[  ...] Out of memory: Killed process 123 (eatmem)
        total-vm:..., anon-rss:..., file-rss:..., ...
```

逐行讀懂：**誰觸發**（`eatmem invoked oom-killer`）、**現況**（Mem-Info 的各 zone watermark）、**候選清單**（Tasks state，列出每個 process 的 rss 與 oom_score_adj）、**判決**（`Killed process ... (eatmem)`）。這份報告是生產環境排查 OOM 的第一手證據——先看被殺的是不是「該死的那個」，再看是全域 OOM 還是某個 memcg 的 OOM（有 memcg OOM 會多印 cgroup 資訊）。

**進階觀測**：`cat /proc/vmstat | grep -E 'pgscan|pgsteal|allocstall|pswpout|pswpin'`——`pgscan_kswapd` vs `pgscan_direct` 看回收壓力被誰扛、`allocstall_*` 看 direct reclaim 發生幾次、`pswpout/pswpin` 看 swap 進出總量。

## 對比與取捨

| 回收方式 | 觸發 | 誰在等 | 延遲影響 | 對應源碼 |
|---|---|---|---|---|
| kswapd（背景） | free < low watermark | 沒人（非同步） | 幾乎無感 | `kswapd()` `balance_pgdat()` |
| direct reclaim（前景） | 配置當場沒頁（< min） | 配置的 process | 延遲尖峰 | `__alloc_pages_direct_reclaim()` |
| shrinker | 隨 reclaim 一起 | 視同上 | 縮 slab 快取 | `shrink_slab()` |
| OOM killer | reclaim 徹底失敗 | 全系統（殺 process） | 一個 process 死 | `out_of_memory()` |

| 頁類型 | 回收成本 | 怎麼回收 |
|---|---|---|
| clean file page | 最低（改 pte、free） | 直接丟，用時重讀 |
| dirty file page | 中（一次寫） | writeback → 變 clean → 丟 |
| anonymous page | 高（寫 + 未來讀回） | swap out，pte → swap entry |
| unevictable | 不可回收 | 排除（mlock、DMA、核心結構） |

| 旋鈕 | 位置 | 調什麼 |
|---|---|---|
| `swappiness` | `/proc/sys/vm/swappiness` | anon vs file 回收傾向（0–200，預設 60） |
| `vfs_cache_pressure` | `/proc/sys/vm/vfs_cache_pressure` | 回收 dentry/inode 的積極度 |
| `oom_score_adj` | `/proc/<pid>/oom_score_adj` | 某 process 被 OOM 選中的傾向（−1000 豁免） |
| `overcommit_memory` | `/proc/sys/vm/overcommit_memory` | 是否超額承諾（2 = 嚴格、malloc 老實失敗） |

## 踩雷集錦

1. **「free 很低 = 記憶體快爆了」——錯**。Linux 故意把 RAM 吃滿當 page cache，`free` 欄常年很低是**正常**。真正該看的是 `available`（`free -m` 的 available 欄），它估算「不 swap、不傷害效能的情況下還能拿到多少」。cache 隨時可回收，不算「用掉」。用 `free` 低來判斷記憶體壓力會嚇死自己。

2. **「swappiness=0 就永不 swap」——錯**。它只是強烈傾向不 swap file 頁優先，記憶體真的緊時 `get_scan_count()` 仍可能換出匿名頁以避免 OOM。要完全沒有 swap 是 `swapoff -a`（但那樣一沒記憶體就直接 OOM，少了緩衝）。

3. **「OOM 一定殺吃記憶體最多的」——大方向對，但可被 `oom_score_adj` 推翻**。設了負值的 process（如 sshd）就算 RSS 大也會被跳過；memcg OOM 只在**該 cgroup 內**選 victim，不看全系統誰最大。看到「被殺的不是最大的那個」先查 `oom_score_adj` 和是不是 memcg OOM。

4. **「reclaim 遇到 dirty 頁會同步等它寫完」——通常不會**。reclaim 路徑上對 dirty 頁多半是**啟動非同步 writeback 後先跳過**，繼續掃別的頁，避免卡在 I/O。真正把 dirty 頁刷回磁碟主要靠 writeback kernel thread（Ch 21）。如果 reclaim 掃了一圈全是還沒寫回的 dirty 頁（`nr_dirty` 很高），才會被迫等 —— 這時系統會非常卡，是 dirty ratio 沒調好的徵兆。

5. **「malloc 成功 = 記憶體到手」——錯，這正是 OOM 存在的原因**。overcommit + demand paging 下，`malloc` 成功只是「登記了一段位址」，實體頁要等你寫入時 fault 才配。所以真正的「記憶體不足」發生在 page fault 當下，那時沒有乾淨的失敗路徑，只能 OOM。想讓 `malloc` 誠實失敗要 `vm.overcommit_memory=2`。

## 進階：再往深一層

- **PSI（Pressure Stall Information）**：`/proc/pressure/memory`（`kernel/sched/psi.c`）量化「有多少時間 process 因為等記憶體而停擺」。比起看 free/swap 這些「狀態量」，PSI 直接量「痛感」——`some`/`full` 兩個指標告訴你記憶體壓力對執行的實際傷害。生產環境用 PSI 做早期預警（在 OOM 前就擴容/降載）比看 free 準得多。

- **早期 OOM handler：`oomd` / `systemd-oomd`**：kernel 的 OOM killer 出手時系統往往已經卡到不行（一直在 direct reclaim + swap thrashing）。userspace 的 `systemd-oomd` 讀 PSI，在 kernel OOM **之前**就主動殺 cgroup，用可控的方式提前止血。這是「與其等 kernel 猜，不如 userspace 按策略殺」的思路。

- **swap thrashing**：記憶體嚴重不足但還沒到 OOM 時，系統可能陷入「換出一頁、馬上又要換回、又換出別頁」的死循環，CPU 幾乎全花在 swap I/O 上，系統看起來像當機但沒 OOM。這種「活著但沒在幹活」有時比乾脆 OOM 更糟——這也是 `systemd-oomd`/PSI 想解決的問題。

- **面試常問**：「記憶體不足時 kernel 做什麼？」——按順序答：回收 clean file page（免費）→ writeback dirty file page → swap out anon page → shrinker 縮 slab → 都不夠才 OOM killer。「kswapd 和 direct reclaim 差在哪？」——背景非同步 vs 前景同步，後者是延遲尖峰來源。「為什麼有 OOM killer？」——overcommit + demand paging 讓真正的記憶體耗盡發生在 fault 當下，沒有乾淨失敗路徑。能把這三題串起來講，就抓到本章骨架了。

## 動手練習

1. **完整跑一次 reclaim → swap → OOM**：照「動手」節建 swap、跑 `eatmem`，用 `vmstat 1` 抓到 `so` 第一次跳非零的瞬間，記下那時 `free -m` 的數字。然後讓它跑到 OOM，`dmesg` 存下完整 OOM 報告，逐行標註每一段在講什麼（誰觸發 / Mem-Info / 候選清單 / 判決）。

2. **保護一個 process 不被殺**：開兩個 `eatmem`，把其中一個的 `oom_score_adj` 設成 `-1000`（`echo -1000 > /proc/<pid>/oom_score_adj`），再逼 OOM，確認被殺的是**沒設豁免**的那個。反過來把一個設成 `1000`，確認它先死。

3. **調 swappiness 看行為變化**：`swappiness=0` 和 `swappiness=100` 各跑一次 `eatmem`，對比 `vmstat` 的 `so` 開始跳的時機、以及 `si/so` 的量。體會這個旋鈕怎麼改變 anon vs file 的回收傾向。

4. **gdb 停在回收路徑**（延續 Ch 0 環境）：`break shrink_node`，跑 `eatmem` 觸發回收，停下後 `backtrace` 看是 kswapd 叫進來的還是 direct reclaim（呼叫堆疊裡有 `__alloc_pages_slowpath` 就是 direct reclaim）。再 `break out_of_memory`，看 OOM 真的被呼叫到時的堆疊。

5. **關 swap 看差別**：`swapoff -a` 後再跑 `eatmem`，你會發現沒有 swap 緩衝，OOM 來得又快又猛（沒有匿名頁可換出，reclaim 只能靠 file 頁，很快就撐不住）。對比有 swap 時多撐了多久。

## 本章重點整理

- 頁能不能回收看「內容能不能重建」：clean file 頁直接丟、dirty file 頁先 writeback、匿名頁得 swap out（pte 變 swap entry），unevictable 排除。
- kernel 用近似 LRU（active/inactive 雙串列 × anon/file，`struct lruvec`）+ 二次機會 + refault detection 來決定回收誰，避免嚴格 LRU 的成本；MGLRU 是 6.1 起的替代方案（預設不開）。
- kswapd 是背景非同步回收（補到 high watermark 才睡），direct reclaim 是前景同步回收（配置者自己下場，延遲尖峰的頭號來源）。
- 回收徹底失敗才 OOM killer（`out_of_memory()` 按 oom_score 選 victim），這是 overcommit + demand paging 的必然代價；cgroup memcg OOM 只殺該 cgroup，是容器記憶體限制的底層（Ch 50）。

## 自我檢核

- [ ] 不看筆記，能畫出「記憶體壓力 → kswapd/direct reclaim → 掃 LRU → writeback/swap → 仍不足 → OOM」整條路徑
- [ ] 能解釋為什麼 kernel 用 active/inactive 雙串列而不是嚴格 LRU，「二次機會」是什麼意思
- [ ] 面試被問「kswapd 和 direct reclaim 差在哪」，能講清楚背景 vs 前景、以及為什麼 direct reclaim 是延遲尖峰來源
- [ ] 能說出匿名頁 swap out 後 pte 變成什麼、下次存取怎麼換回（接 Ch 19 `do_swap_page`）
- [ ] 能解釋「為什麼需要 OOM killer 而不是讓 malloc 失敗」，並說出 overcommit 的角色
- [ ] 看到 `free` 顯示記憶體快滿，能判斷該看 `available` 而非 `free` 欄，並解釋為什麼

## 延伸閱讀

### 官方文件

- **[Documentation/admin-guide/mm/concepts.rst](https://www.kernel.org/doc/html/latest/admin-guide/mm/concepts.html)**
  - **讀哪裡**：「Reclaim」「Swap」「OOM killer」三節
  - **和本章的關聯**：官方對本章所有概念的權威簡述，術語以它為準；讀完本章回來對照，確認自己的心智模型沒偏

- **[Documentation/admin-guide/mm/multigen_lru.rst](https://www.kernel.org/doc/html/latest/admin-guide/mm/multigen_lru.html)**
  - **讀哪裡**：整篇（不長）
  - **能學到什麼**：MGLRU 怎麼用多世代取代傳統雙串列 LRU、怎麼啟用、為什麼號稱更準。本章只點到，想深入從這裡開始

### LWN 文章

- **[The OOM killer](https://lwn.net/Articles/317814/)** 及後續系列 — Jonathan Corbet
  - **讀哪裡**：OOM 選 victim 的啟發式演進史
  - **為什麼值得讀**：OOM killer 的 heuristic 改過很多次，每次改都有血淋淋的動機（誰不該被殺卻被殺了）。讀它能理解為什麼 `oom_badness()` 長現在這樣，以及這套猜測為何永遠有爭議

- **[Multi-generational LRU](https://lwn.net/Articles/856931/)** — LWN
  - **讀哪裡**：MGLRU 的設計動機與早期爭議
  - **前提**：讀完本章的傳統 LRU 一節，才知道 MGLRU 在改什麼

### 書籍

- **《Understanding the Linux Virtual Memory Manager》** — Mel Gorman（2004，免費 PDF）
  - **這本書的定位**：Linux VM 的經典專著，reclaim / LRU / swap 的機制講得最透
  - **注意**：對應的是 2.6 早期 kernel，`struct` 名稱和分層（尤其 memcg、folio、MGLRU）都已大改，但**核心思想**（為何近似 LRU、swap entry 設計、reclaim 分類）到今天仍然成立。當「原理書」讀，別當「v6.12 源碼對照」讀

回收與 swap 講完，我們把 mm 的最後一塊硬體介面補上：TLB（page table 的快取）、memory barrier、以及多核之間的 cache coherence——這些是「改了 page table 之後，怎麼確保每顆 CPU 都看到最新映射」的底層保證。

→ [Ch 23 TLB、memory barrier 與 cache coherence](./23-tlb-memory-barriers.md)
