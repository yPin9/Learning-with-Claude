# Ch 19 — mm_struct、VMA、page fault handler

> **目標**：讀懂 kernel 怎麼用一個 `mm_struct` 描述一個 process 的整個虛擬位址空間、怎麼用一串 VMA 描述其中每一段區域，以及一次 page fault 從 CPU 丟出例外到 kernel 補上 PTE、重試指令的完整決策流程。學完你能自己在 gdb 停在 `handle_mm_fault`、看懂 `/proc/<pid>/maps` 每一行、並解釋一個 SIGSEGV 到底是「哪種 fault 走到哪一步被判死」。

前面 Ch 16 教了「虛擬位址怎麼透過 page table 翻成實體位址」，Ch 9 提到 `task_struct` 裡有個 `mm` 指標。這章把兩者接起來：**`mm_struct` 是那棵 page table 的擁有者兼管理者，VMA 是它的帳本**。CPU 硬體只認 page table，但 page table 是稀疏、扁平、沒有語意的——它不知道「這段是 code、那段是 heap、這頁該從哪個檔案讀進來」。這些語意全部記在 `mm_struct` 和它底下的 VMA 裡。

## 為什麼需要這個？

想像沒有 VMA、只有 page table 的世界。process 存取一個位址，MMU 走 page table 發現 PTE 是空的（present bit = 0），丟出 page fault。現在 kernel 手上只有「哪個位址、讀還是寫」兩個資訊。它該怎麼辦？

- 這位址是**合法但還沒配實體頁**（malloc 了一大塊但還沒碰）？那要配一頁補上。
- 是**檔案映射還沒讀進來**（mmap 了一個 `.so`，第一次執行到某頁）？那要去 page cache 找、找不到就發 I/O 讀磁碟。
- 是**Copy-on-Write 頁被寫**（fork 後子行程寫共享頁）？那要複製一份再寫。
- 是**被 swap 出去了**（Ch 22）？那要從 swap 讀回來。
- 還是根本**非法存取**（野指標）？那要送 SIGSEGV。

光憑 page table 的一個空 PTE，kernel 分不出這五種情況。它需要一份「這個位址空間應該長什麼樣」的藍圖——每一段合法區域的起訖、權限、背後是什麼。這份藍圖就是 VMA（virtual memory area）集合。**page table 是硬體看的快取，VMA 是 kernel 看的真相來源（source of truth）**。fault 發生時，kernel 拿 fault 位址去查 VMA：查不到 → 你在存不該存的地方 → SIGSEGV；查到了 → 依這個 VMA 的類型決定怎麼把 page table 補齊。

這就是 demand paging（Ch 20）的地基：**kernel 不預先配好所有頁，只在你真的碰到時才配**。而「碰到時才配」這個動作，就是 page fault handler 做的事。

## 先建立直覺

一個 process 的位址空間，在 kernel 裡由三層物件描述：

```
task_struct (Ch 9)
    │  ->mm
    ▼
mm_struct  ──── 一個 process 的完整位址空間，一份
    │  ->pgd ─────────────► page table 根（Ch 16，MMU 硬體走這個）
    │  ->mm_mt ──────────►  maple tree：所有 VMA 的容器（6.1 起）
    │  ->mmap_lock          保護整個位址空間的讀寫鎖
    │  start_code/end_code  各區段邊界（下面詳述）
    │  start_brk/brk        heap 起訖
    │  mmap_base            mmap 區的起點
    │
    └── maple tree of VMAs（依虛擬位址排序）：
        ┌──────────┬──────────┬──────────┬───────┬──────────┐
        │ [text]   │ [data]   │ [heap]   │ [mmap]│ [stack]  │
        │ r-x file │ rw- file │ rw- anon │ r-x .so│ rw- anon │
        └──────────┴──────────┴──────────┴───────┴──────────┘
         低位址 ──────────────────────────────────────► 高位址
        每一格 = 一個 vm_area_struct = /proc/<pid>/maps 裡的一行
```

三句話記住分工：

- **`mm_struct`：一個位址空間**。一個單執行緒 process 一個 `mm`。多執行緒共享同一個 `mm`（`CLONE_VM`，Ch 10）——這就是為什麼同一個 process 的 thread 看到同一片記憶體。
- **`vm_area_struct`（VMA）：位址空間裡一段連續、同質的區域**。同質指的是同樣的權限、同樣的 backing。code 段是一個 VMA、每個 mmap 是一個 VMA、heap 是一個 VMA。`/proc/<pid>/maps` 每一行就是一個 VMA。
- **page table：VMA 的硬體投影**。kernel 依 VMA 的規則，在 process 真的碰到某頁時才把對應 PTE 填好。

fault 發生時的一句話流程：**拿 fault 位址去 maple tree 找 VMA → 找不到就 segfault → 找到就照這個 VMA 的類型把 page table 補齊 → 回去重試那條指令**。

## mm_struct：位址空間的容器

定義在 `include/linux/mm_types.h` 的 `struct mm_struct`。它很大，我們挑出這章相關的欄位（欄位在 6.12 大多包在一個匿名 struct 裡，這裡按語意分組）：

```c
struct mm_struct {
    struct {
        struct maple_tree mm_mt;      // 所有 VMA 的容器（6.1 起，取代 rbtree+list）
        pgd_t *pgd;                   // page table 根（Ch 16），載入 CR3 的就是它
        atomic_t mm_users;            // 有幾個「使用者」（thread）在用這個位址空間
        atomic_t mm_count;            // 有幾個「參照」抓著這個 mm_struct 本身
        unsigned long mmap_base;      // mmap 區域的起始位址
        unsigned long task_size;      // 這個 process 的 user 位址空間上限
        // ── 區段邊界，給 /proc 和 core dump 用 ──
        unsigned long start_code, end_code;    // text 段起訖
        unsigned long start_data, end_data;    // data 段起訖
        unsigned long start_brk, brk;          // heap 起訖（brk() 移動的就是 brk）
        unsigned long start_stack;             // stack 起點
        unsigned long arg_start, arg_end;      // argv 區
        unsigned long env_start, env_end;      // envp 區
        struct rw_semaphore mmap_lock;         // 保護整個位址空間的讀寫鎖
        // ... 還有非常多欄位：rss 統計、context、exe_file 等
    } __randomize_layout;
};
```

幾個設計決策值得停下來想：

**`pgd` 就是硬體那棵 page table 的根**。context switch 到這個 process 時（Ch 14），`switch_mm()` 會把 `mm->pgd` 的實體位址載入 `CR3`（x86_64）/`TTBR0`（ARM64）。所以「切換位址空間」在硬體層就是換一個 `pgd`。同一個 process 的 thread 共享 `mm`，也就共享 `pgd`，也就共享同一棵 page table——這是「thread 之間記憶體互通」的根本原因，不是什麼額外機制。

**`mm_users` vs `mm_count`：兩個 refcount，很多人搞混**。
- `mm_users`：有幾個 thread 正在**使用**這個位址空間（把它當自己的記憶體在跑）。最後一個 thread 退出時歸零，此時可以拆掉所有 VMA、釋放 page table。
- `mm_count`：有幾個東西**抓著 `mm_struct` 這個物件本身**（不一定在用它的位址空間）。`mm_users > 0` 時 `mm_count` 至少為 1（所有 users 合起來算一個 count）。核心執行緒（kernel thread）沒有自己的位址空間，會臨時「借用」上一個 process 的 `mm`（`mmgrab`/`kthread_use_mm`），這時它加的是 `mm_count` 不是 `mm_users`——因為它沒真的在跑那些 user 記憶體，只是不想讓 `mm_struct` 被 free 掉。

為什麼要兩層？因為「位址空間可以拆了」和「`mm_struct` 這塊記憶體可以 free 了」是兩件事。有借用者還抓著時，位址空間（VMA、page table）已經可以拆，但 `mm_struct` 這個殼還不能 free。`mm_users` 到 0 觸發 `__mmput`（拆位址空間），`mm_count` 到 0 才 `free_mm`（free 那塊 struct）。搞錯這個，就是 use-after-free。相關函式：`include/linux/sched/mm.h` 的 `mmget`/`mmput`/`mmgrab`/`mmdrop`。

**區段邊界（`start_code` 等）大多是給人看的，不是給 fault handler 用的**。fault handler 判斷合法性靠 VMA，不靠這些邊界。`start_code`/`end_code`/`start_brk` 這些主要服務 `/proc/<pid>/stat`、core dump、`brk()` 系統呼叫的邊界檢查。別把它們當成位址空間的權威描述——權威描述是 VMA 集合。

## vm_area_struct：一段區域

定義同樣在 `include/linux/mm_types.h`，`struct vm_area_struct`。挑重點欄位：

```c
struct vm_area_struct {
    unsigned long vm_start;         // 區域起始虛擬位址（含）
    unsigned long vm_end;           // 區域結束虛擬位址（不含）
    struct mm_struct *vm_mm;        // 回指所屬的 mm
    pgprot_t vm_page_prot;          // 這段要寫進 PTE 的硬體權限位元
    unsigned long vm_flags;         // VM_READ / VM_WRITE / VM_EXEC / VM_SHARED ...
    struct file *vm_file;           // 檔案映射：背後的檔案；匿名映射：NULL
    unsigned long vm_pgoff;         // 檔案映射：從檔案的第幾頁開始映
    const struct vm_operations_struct *vm_ops;  // 這段的行為方法表（含 .fault）
    struct anon_vma *anon_vma;      // 匿名映射：反向映射用（Ch 20）
    // ... maple tree 節點、named interval 等
};
```

一個 VMA 是「一段 `[vm_start, vm_end)` 的連續虛擬位址，全段共用一組權限（`vm_flags`）和同一個 backing（`vm_file` 或匿名）」。理解這句話你就懂了 `/proc/<pid>/maps` 的每一行。

**`vm_flags` vs `vm_page_prot`：軟體權限與硬體權限，別混**。
- `vm_flags` 是 kernel 的邏輯權限（`VM_READ`/`VM_WRITE`/`VM_EXEC`/`VM_SHARED`/`VM_MAYWRITE`...），fault handler 檢查合法性看這個。
- `vm_page_prot` 是要實際寫進 PTE 的硬體位元。兩者不總是一致：CoW 的頁在 `vm_flags` 有 `VM_WRITE`（邏輯上可寫），但 `vm_page_prot` 寫進 PTE 時是唯讀的（故意的，好讓寫入觸發 fault 去做複製，Ch 20）。**這個「邏輯可寫、硬體唯讀」的落差，正是 CoW 的機關**。

**檔案映射 vs 匿名映射，由 `vm_file` 是不是 NULL 區分**：
- `vm_file != NULL`：檔案映射（file-backed）。這段內容從 `vm_file` 這個檔案的 `vm_pgoff` 頁開始。fault 時去 page cache（Ch 21）找那頁；找不到就發 I/O 讀進來。程式的 text 段（映射執行檔）、`mmap` 一個檔案、動態函式庫都屬這類。
- `vm_file == NULL`：匿名映射（anonymous）。沒有檔案背書，內容初始全是 0。heap、stack、`mmap(MAP_ANONYMOUS)`、CoW 後私有化的頁屬這類。fault 時配一頁乾淨的零頁（或共享全域 zero page）。

**`vm_ops->fault`：這段區域「該怎麼被 fault 進來」的方法**。檔案系統各自實作（例如 ext4 的 `filemap_fault`），fault handler 會 callback 它。這是 kernel 典型的物件導向手法——`vm_operations_struct` 是虛擬函式表，不同 backing 掛不同的 `.fault`。匿名映射沒有 `vm_ops`，走一條固定的匿名路徑。

## VMA 的組織：從紅黑樹到 maple tree

一個 process 動輒幾十上百個 VMA（大型程式如瀏覽器可以上千）。fault handler 每次 fault 都要「拿一個位址、找出它落在哪個 VMA」——這是熱路徑，資料結構的選擇直接影響效能。

**6.1 之前：紅黑樹 + 雙向鏈結串列**。`mm->mm_rb` 是一棵按 `vm_start` 排序的紅黑樹（Ch 5），支援 O(log n) 的位址查找；同時 VMA 又串在一條鏈結串列上，方便循序走訪。兩套結構描述同一組 VMA，任何增刪都得同時維護兩邊——冗餘、易錯，而且紅黑樹的並行讀寫要靠 `mmap_lock` 這把大鎖擋住。

**6.1 起：maple tree（`mm->mm_mt`）**，取代上面兩者。maple tree 是一種 RCU-safe 的 B-tree 變體，專門存 range（區間），Ch 5 介紹過。換掉紅黑樹的理由：

- **RCU 讀**：maple tree 支援 lockless 讀（RCU-protected），讀者不必拿 `mmap_lock` 的寫端。這是後來 6.4 引入 **per-VMA lock**（fault 時只鎖單一 VMA、不鎖整個 `mm`）的地基，大幅降低多執行緒 page fault 的鎖競爭。
- **一套結構取代兩套**：maple tree 本身就能高效循序走訪，不必再額外掛一條鏈結串列。少一份冗餘就少一類 bug。
- **cache 友善**：B-tree 的節點把多個區間打包在一起，走訪時 cache locality 比紅黑樹（每個節點一個 VMA、指標到處跳）好。

查找的核心函式是 `mm/mmap.c`（6.12 部分邏輯移到 `mm/vma.c`）的 `find_vma(mm, addr)`：回傳「第一個 `vm_end > addr` 的 VMA」。注意這個語意的坑——它回傳的 VMA **不保證包含 addr**，只保證是 addr 右邊（含）最近的那個。所以呼叫端還要自己檢查 `addr >= vma->vm_start`；若不成立，代表 addr 落在兩個 VMA 之間的空洞（gap）——那就是非法位址。stack 的自動成長（`expand_stack`）也靠這個語意：fault 落在 stack VMA 下緣的空洞、且旁邊就是可成長的 stack VMA，就把 VMA 往下擴。

底層走訪 maple tree 用的是 `VMA_ITERATOR` / `for_each_vma()` 這組 macro（`include/linux/mm.h`），內部包的是 maple tree 的 `mas_*` 狀態機。你在源碼看到 `vma_iter_*` 就是在操作這棵 maple tree。

## 底層機制：一次 page fault 從頭到尾

這是本章的靈魂。CPU 存取一個虛擬位址，MMU 走 page table 發現 PTE 有問題（不存在、或權限不符），觸發硬體例外。x86_64 上是 `#PF`（vector 14），控制權跳進 kernel 的 fault 入口。

x86_64 的入口是 `arch/x86/mm/fault.c` 的 `do_user_addr_fault()`（由更外層的 `exc_page_fault()` 分流而來——先分「是 user 位址還是 kernel 位址的 fault」）。硬體會在 `CR2` 暫存器留下**出錯的位址**，並在 error code 裡留下**出錯的原因位元**（是讀還是寫、是不是權限問題、是 user 還是 kernel mode）。fault handler 拿這兩樣東西開始決策。

架構相關的入口做完初步分流後，呼叫架構無關的核心 `mm/memory.c` 的 `handle_mm_fault(vma, addr, flags, regs)`。整個決策流程：

```
       CPU 存取虛擬位址 addr（讀/寫/執行）
                  │
          MMU 走 page table
                  │
      PTE present? 權限符合?
         │              │
        是             否 → 觸發 #PF 硬體例外，CR2 = addr
   正常存取，              │
   handler 不介入   arch/x86/mm/fault.c: exc_page_fault → do_user_addr_fault
                          │
              ┌───────────┴────────────┐
              │ find_vma(mm, addr)     │
              │ 找 addr 落在哪個 VMA   │
              └───────────┬────────────┘
                          │
           ┌──────────────┴───────────────┐
       沒有 VMA / addr < vma->vm_start   有 VMA 且包含 addr
       （且不是可成長的 stack gap）          │
           │                       ┌───────┴─────────┐
     ✗ 非法位址                    │ 檢查權限：       │
     bad_area → SIGSEGV            │ 寫? vma 有       │
     （dmesg 印 segfault）         │ VM_WRITE 嗎?     │
                                   │ 執行? VM_EXEC?   │
                                   └───────┬─────────┘
                                     ┌─────┴──────┐
                                  權限不符      權限 OK
                                     │             │
                              ✗ 寫唯讀等      handle_mm_fault(vma, addr, ...)
                              → SIGSEGV             │
                                        ┌──────────┴──────────────────┐
                                        │ 走多層 page table，缺哪層補哪層 │
                                        │ 最後到 handle_pte_fault()      │
                                        └──────────┬──────────────────┘
                        ┌──────────────────────────┼──────────────────────────┐
                   PTE 是空的                  PTE 標了 swap             PTE present 但
                   (從沒配過)                       │                  寫一個唯讀頁 (CoW)
                        │                     do_swap_page()                  │
              ┌─────────┴────────┐            從 swap 讀回 (Ch22)     do_wp_page()
          匿名 VMA           檔案 VMA          = major fault          複製一份再寫
        do_anonymous_page  do_fault→                                 (Ch 20)
        配零頁            vm_ops->fault
        = minor fault     去 page cache 找
                          有→minor / 沒有→發I/O major
                                        │
                                   全部成功後：set_pte，填好 PTE
                                        │
                              回到 user space，重試那條指令
                                （這次 MMU 走 page table 成功）
```

拆開來看幾個關鍵決策點：

**第一關：`find_vma` 查得到嗎？** 查不到（或查到的 VMA 不包含 addr、且不是可成長的 stack）→ `bad_area` → 送 `SIGSEGV`。這就是野指標、存取 NULL、stack overflow 撞到不可成長區域的下場。這一步 dmesg 會印出 `segfault at <addr> ip <ip> sp <sp> error <code>`——error code 的位元告訴你是讀是寫、user 還 kernel。

**第二關：權限對嗎？** 查到 VMA 了，但你想做的事這個 VMA 不允許——例如寫一個沒有 `VM_WRITE` 的段（改字串常數）、或在沒有 `VM_EXEC` 的頁上執行（DEP/NX 擋 shellcode）——同樣 `SIGSEGV`。這一關是 W^X、NX bit 這些防護在 kernel 側的落點。

**第三關（VMA 和權限都 OK，才進 `handle_mm_fault`）：這頁為什麼還沒 ready？** 走過 P4D/PUD/PMD 各層 page table（缺哪層就配哪層的 table），最後到 `handle_pte_fault()`，看那個 PTE：
- **PTE 全空（從沒配過）**：demand paging 首次觸碰。
  - 匿名 VMA → `do_anonymous_page()`：配一頁（寫入才配實體頁，唯讀讀取可先指向共享 zero page），填 PTE。**minor fault**（不碰磁碟）。
  - 檔案 VMA → `do_fault()` → callback `vma->vm_ops->fault`（如 `filemap_fault`）：去 page cache 找那頁。在 cache → minor fault；不在 → 發 I/O 讀磁碟 → **major fault**。
- **PTE 標記為 swap entry**：頁被換出去了（Ch 22）→ `do_swap_page()` 從 swap 讀回。要碰磁碟 → **major fault**。
- **PTE present 但你在寫一個標了唯讀的頁**：這是 CoW（Ch 20）。→ `do_wp_page()` 複製一份、把新頁設成可寫、更新 PTE。通常 minor（除非要先把來源頁 swap in）。

**收尾：填好 PTE，回 user space 重試指令**。這裡是 fault 機制最優雅的地方——handler 不「模擬」那條出錯的指令，它只是把 page table 補好，然後讓 CPU **重新執行同一條指令**。第二次 MMU 走 page table 就成功了，process 完全不知道剛剛繞了一圈進 kernel。這也是為什麼 page fault 對程式是透明的。

### major vs minor fault

分野只有一個問題：**這次 fault 要不要等磁碟 I/O？**

- **minor fault**：不碰磁碟。頁其實已經在實體記憶體，只是這個 process 的 page table 還沒指過去——配個零頁、指向已在 page cache 的頁、CoW 複製，都算 minor。
- **major fault**：要發 I/O 從磁碟/swap 讀。第一次執行到某個 `.so` 的某頁、讀一個沒被 cache 的 mmap 檔、swap in——都是 major。major fault 貴（磁碟延遲，SSD 也是微秒起跳，比 minor 的奈秒級慢好幾個數量級）。

`perf stat` 分開統計這兩者。一個程式若 major fault 暴增，通常代表記憶體不足在瘋狂 swap，或 working set 撐不進 RAM——這是效能診斷的重要訊號。統計數字累在 `task_struct` 的 `maj_flt`/`min_flt`，`/proc/<pid>/stat` 也看得到。

## 動手：觀察位址空間與 fault

以下在 Ch 0 的 QEMU 環境（或直接在你的 Linux host / WSL2）操作。前四個用 `/proc` 和 `perf`，最後一個用 gdb 停在 kernel。

### 1. `/proc/<pid>/maps`：一行一個 VMA

跑一個會睡著的 process，看它的位址空間：

```bash
sleep 1000 &
cat /proc/$!/maps
```

輸出每行就是一個 VMA：

```
555555554000-555555555000 r-xp 00000000 08:01 1234   /usr/bin/sleep      ← text，可執行
555555755000-555555756000 rw-p 00001000 08:01 1234   /usr/bin/sleep      ← data，可寫
5555559a2000-5555559c3000 rw-p 00000000 00:00 0      [heap]              ← 匿名 heap
7ffff7a00000-7ffff7bce000 r-xp 00000000 08:01 5678   /lib/.../libc.so.6  ← libc text
7ffffffde000-7ffffffff000 rw-p 00000000 00:00 0      [stack]             ← 匿名 stack
```

逐欄對到結構：`vm_start-vm_end` 就是 VMA 的起訖；`r-xp` 是 `vm_flags`（r/w/x 加 p=private 或 s=shared）；接著是 `vm_pgoff`、裝置、inode、最後是 `vm_file` 的路徑（匿名的沒有路徑，或標 `[heap]`/`[stack]`）。**能看懂這張表，你就在腦中還原了整棵 VMA maple tree**。

### 2. `/proc/<pid>/smaps`：每個 VMA 的細節

`smaps` 把每個 VMA 展開成一整段統計：

```bash
cat /proc/$!/smaps | head -30
```

會看到每段的 `Rss`（實際佔了多少實體記憶體）、`Pss`（按共享比例攤分的佔用）、`Shared_Clean`/`Private_Dirty` 等。這裡最能體會 demand paging：一個 VMA 的 `Size`（虛擬大小）常常遠大於 `Rss`（實際配的實體頁）——**你 mmap 了一大片，但只有真的碰過的頁才有 Rss**。`Private_Dirty` 高的匿名段就是被寫過、CoW 複製出來的私有頁。

### 3. `perf stat`：數 major/minor fault

```bash
perf stat -e page-faults,minor-faults,major-faults ./your_program
```

跑一個第一次載入的大程式，major-faults 會有一些（第一次把 code 讀進 page cache）；馬上再跑一次，major-faults 幾乎歸零（已被 cache），minor-faults 仍在（page table 每次都要重建）。這個對比直接示範了 minor（不碰磁碟）和 major（碰磁碟）的差別。

### 4. 故意 segfault，用 dmesg 看 fault 資訊

```c
// segv.c — 故意寫一個唯讀位址
int main(void) {
    char *p = (char *)0xdeadbeef;   // 沒有 VMA 的位址
    *p = 42;                         // 寫入 → find_vma 找不到 → SIGSEGV
    return 0;
}
```

```bash
gcc segv.c -o segv && ./segv     # Segmentation fault (core dumped)
dmesg | tail -1
# segv[12345]: segfault at deadbeef ip 000055... sp 00007ff... error 6 in segv[...]
```

`error 6` = binary `110`：bit1（write）+ bit2（user mode），bit0（present）為 0 表示 PTE 不存在。對照的正是 fault handler 走到 `bad_area` 那條路——`find_vma` 對 `0xdeadbeef` 查不到 VMA，直接判 SIGSEGV。你剛剛親手走了流程圖左邊那條分支。

### 5. gdb 停在 `handle_mm_fault`

在 QEMU + gdb 環境（Ch 0 Step 7），停在核心 fault handler：

```gdb
(gdb) break handle_mm_fault
(gdb) continue
```

然後在 QEMU 的 shell 跑任何會碰新記憶體的指令（例如 `ls`），gdb 會停下。看它的參數：

```gdb
(gdb) print vma->vm_start
(gdb) print vma->vm_end
(gdb) print/x vma->vm_flags        // 對照 VM_READ(0x1)/VM_WRITE(0x2)/VM_EXEC(0x4)
(gdb) print address                // 出錯的位址
(gdb) backtrace                    // 看它從 do_user_addr_fault 一路怎麼進來
```

`backtrace` 會顯示 `exc_page_fault → do_user_addr_fault → handle_mm_fault` 這條鏈——你在源碼讀到的流程，現在在真實執行中攤在眼前。再 `break do_anonymous_page` 和 `break do_wp_page`，就能分別停在「配匿名頁」和「CoW 複製」兩條分支上，親眼確認流程圖右邊每條路徑。

## 對比與取捨

| 主題 | 選項 A | 選項 B | 取捨 |
|---|---|---|---|
| VMA 容器（6.1 前後） | rbtree + linked list | **maple tree** | maple tree 支援 RCU lockless 讀、一套結構取代兩套、cache 友善；是 per-VMA lock 的地基。舊法冗餘且要大鎖 |
| fault 鎖粒度 | `mmap_lock` 鎖整個 mm | **per-VMA lock**（6.4 起）| per-VMA 只鎖單一 VMA，多執行緒同時 fault 不同區域不互卡，大幅降競爭。fallback 到 `mmap_lock` 處理複雜情況 |
| 首次觸碰的配頁時機 | 預先配好（pre-fault） | **demand paging**（碰到才配）| demand paging 省記憶體、加快啟動（不必先配完），代價是首次觸碰要進一次 kernel。`MAP_POPULATE` 可要求預先配 |
| 匿名頁初值 | 每次配新零頁 | **共享 zero page**（唯讀時）| 唯讀的匿名頁全指向同一個全域零頁，省記憶體；一旦寫入才 CoW 出私有頁 |
| minor vs major fault | — | — | minor 不碰磁碟（奈秒級），major 要 I/O（微秒起跳）；效能診斷第一眼看 major/minor 比例 |

## 踩雷集錦

1. **以為 `/proc/maps` 的大小就是實際記憶體用量**。錯。maps 顯示的是**虛擬**大小（VMA 的 `vm_end - vm_start`）。一個 process 可以 mmap 幾 GB 的虛擬空間卻只真的用幾 MB 實體記憶體——因為 demand paging，沒碰過的頁根本沒配。要看實際佔用去 `smaps` 的 `Rss`/`Pss`，或 `/proc/<pid>/status` 的 `VmRSS`。

2. **以為 page fault 都是壞事**。每個程式一啟動就有大量 fault——那是 demand paging 在正常運作，code、data、stack 全靠 minor fault 逐頁配進來。fault 本身是機制不是錯誤。要擔心的是 **major fault 異常偏高**（狂 swap）或**莫名的 SIGSEGV**（非法存取）。

3. **`find_vma` 回傳的 VMA 不一定包含你的位址**。它回傳「第一個 `vm_end > addr`」的 VMA，可能整個在 addr 右邊。呼叫端必須再檢查 `addr >= vma->vm_start`。忘了這個檢查，你會把落在空洞裡的非法位址誤當成合法。

4. **混淆 `vm_flags` 和 `vm_page_prot`**。`vm_flags` 是 kernel 的邏輯權限（判合法性用），`vm_page_prot` 是實際寫進 PTE 的硬體位元。CoW 頁「邏輯可寫、硬體唯讀」正是靠兩者不一致——寫入時硬體唯讀觸發 fault，kernel 才去複製。把兩者當同一個東西，就理解不了 CoW 為什麼靠 fault 觸發。

5. **混淆 `mm_users` 和 `mm_count`**。thread 退出減 `mm_users`；`mm_users` 到 0 拆位址空間（VMA、page table）。但 `mm_struct` 這個殼由 `mm_count` 管——kernel thread 借用 `mm`（`kthread_use_mm`）加的是 `mm_count`。這兩層 refcount 分開，正是為了讓「位址空間可以拆」和「struct 可以 free」解耦。搞錯就是 UAF。

## 進階：再往深一層

- **per-VMA lock（6.4 起）**：傳統上 fault 要拿 `mmap_lock`（一把保護整個 `mm` 的讀寫鎖），多執行緒同時 fault 會嚴重競爭。6.4 引入 per-VMA lock：fault handler 先試著只鎖 fault 位址所在的那**一個 VMA**（`lock_vma_under_rcu()`，靠 maple tree 的 RCU 讀找到 VMA），成功就不必碰 `mmap_lock`。碰到需要修改 VMA 結構的複雜情況才 fallback 回 `mmap_lock`。這是近年 mm 子系統對多核心擴展性的重要改進，源頭就是換成 maple tree 才做得到。LWN 有專文。

- **VMA 的合併與分裂**。`mmap`/`mprotect`/`munmap` 會讓 VMA 分裂或合併。相鄰、權限相同、backing 連續的兩個 VMA 會被 `vma_merge()` 合成一個（省結構、省查找）；`mprotect` 改中間一段權限則會把一個 VMA 切成三個。`/proc/maps` 行數會隨這些操作變化——這解釋了為什麼有時候你 `mmap` 兩次卻只看到一行。

- **THP（Transparent Huge Pages）**。fault 時 kernel 可以一次配一個 2MB 的 huge page 而非 4KB，減少 fault 次數和 TLB miss（Ch 23）。`do_huge_pmd_anonymous_page()` 是匿名 THP 的 fault 路徑。代價是內部碎片和 CoW 時要整塊複製。

- **面試常問**：「malloc 一大塊記憶體後馬上 free，實體記憶體有沒有被佔用？」答案是**幾乎沒有**——malloc 只是（透過 `brk`/`mmap`）建立/擴大了 VMA，實體頁要等你真的寫入才由 page fault 配上（demand paging）。你 malloc 完沒碰就 free，多數頁從沒被 fault 進來過。這題考的就是本章的 VMA 與 demand paging 分離。

## 動手練習

1. **對照 maps 和 smaps 讀懂一個真實 process**。挑一個跑著的程式（例如你的 shell），`cat /proc/<pid>/maps` 數出有幾個 VMA、哪些是 file-backed（有路徑）哪些是匿名（`[heap]`/`[stack]`/無路徑）。再看 `smaps`，找一個 `Size` 遠大於 `Rss` 的 VMA，解釋為什麼（demand paging）。

2. **用 gdb 分別停在三條 fault 分支**。在 QEMU + gdb 裡同時 break `do_anonymous_page`（匿名頁）、`do_fault`（檔案頁）、`do_wp_page`（CoW）。跑一個 fork 後子行程寫共享變數的小程式，觀察哪條先被觸發、`backtrace` 各自長什麼樣。把三條路徑和本章流程圖對起來。

3. **弄壞它，看 dmesg**。分別寫三個會 segfault 的程式：(a) 寫 NULL、(b) 執行一個 data 段的位址（沒 `VM_EXEC`）、(c) 寫一個 `mmap(PROT_READ)` 出來的唯讀頁。各自 `dmesg | tail` 看 error code 的位元差異（present/write/exec 位元），對照流程圖判斷各自死在哪一關（`bad_area` 還是權限檢查）。

4. **量 major/minor**。寫一個 mmap 一個大檔案然後循序讀過全部的程式，`perf stat -e minor-faults,major-faults` 跑兩次。解釋為什麼第一次 major 多、第二次幾乎沒有（page cache），以及為什麼 minor 兩次都在。

## 本章重點整理

- **`mm_struct` 是一個 process 的完整位址空間**，擁有 page table 根（`pgd`）和所有 VMA（`mm_mt` maple tree）。多執行緒共享一個 `mm`（`CLONE_VM`）。`mm_users`（用位址空間的 thread 數）和 `mm_count`（抓著 struct 的參照數）是兩層 refcount，別搞混。
- **VMA（`vm_area_struct`）是一段連續、同權限、同 backing 的虛擬區域**，`/proc/<pid>/maps` 每行一個。`vm_file` 是否為 NULL 區分檔案映射與匿名映射；`vm_flags`（邏輯權限）和 `vm_page_prot`（硬體 PTE 位元）不總是一致，CoW 靠這個落差運作。
- **VMA 6.1 起用 maple tree（`mm_mt`）** 取代 rbtree+list，換來 RCU lockless 讀、一套結構、cache 友善，是 per-VMA lock 的地基。查找用 `find_vma`（回傳第一個 `vm_end > addr`，不保證包含 addr）。
- **page fault handler 的決策鏈**：`do_user_addr_fault` → `find_vma`（查不到→SIGSEGV）→ 權限檢查（不符→SIGSEGV）→ `handle_mm_fault` → 依 PTE 狀態走 demand paging（匿名/檔案）、CoW（`do_wp_page`）或 swap in（`do_swap_page`），填好 PTE 後回 user space **重試指令**。major fault 碰磁碟、minor 不碰。

## 自我檢核

- [ ] 不看筆記，能畫出 `task_struct → mm_struct → maple tree of VMAs → page table` 的關係，並說出各層負責什麼
- [ ] 能解釋 `mm_users` 和 `mm_count` 為什麼要分兩層，各自到 0 時發生什麼
- [ ] 能說出一次寫入 fault 走完整條決策鏈：從 `find_vma` 到最後填 PTE 重試指令，中間有哪些分岔（segfault/demand paging/CoW/swap in）
- [ ] 面試被問「malloc 一大塊沒碰，實體記憶體用了嗎」，能用 VMA + demand paging 答清楚
- [ ] 能解釋 major 和 minor fault 的差別，以及為什麼 6.1 把 VMA 從紅黑樹換成 maple tree
- [ ] 能看著 `/proc/<pid>/maps` 一行，說出它對應的 VMA 的 `vm_start`/`vm_end`/`vm_flags`/是否 file-backed

## 延伸閱讀

### 官方文件與源碼

- **[Documentation/mm/ 目錄](https://www.kernel.org/doc/html/latest/mm/)** — kernel 官方 mm 文件
  - **讀哪裡**：`process_addrs`（位址空間與 `mmap_lock`/per-VMA lock 的設計）、`vmalloced-kernel-stacks` 之外先看總覽
  - **和本章的關聯**：per-VMA lock 和 `mmap_lock` 的規則寫得比源碼註解完整，讀完流程圖右邊分支再回來看鎖

- **[`mm/memory.c` 的 `handle_mm_fault` 與 `handle_pte_fault`](https://elixir.bootlin.com/linux/v6.12/source/mm/memory.c)** — Bootlin，選 v6.12
  - **讀哪裡**：`handle_mm_fault` → `__handle_mm_fault` → `handle_pte_fault` 這條主幹，看它怎麼依 PTE 狀態分派到 `do_anonymous_page`/`do_fault`/`do_wp_page`/`do_swap_page`
  - **為什麼值得讀**：本章流程圖的權威版本就是這幾個函式；配 gdb 停在這裡對照著讀最有效

### LWN 文章

- **[Introducing maple trees](https://lwn.net/Articles/845507/)** — Jonathan Corbet, LWN
  - **讀哪裡**：整篇。解釋 maple tree 是什麼、為什麼要用它取代 rbtree 存 VMA
  - **前提**：讀過 Ch 5 的 rbtree/資料結構

- **[Per-VMA locks](https://lwn.net/Articles/906852/)** — LWN
  - **能學到什麼**：為什麼 `mmap_lock` 是多執行緒 fault 的瓶頸、per-VMA lock 怎麼靠 maple tree 的 RCU 讀繞過它。呼應本章「進階」一節

### 書籍

- **《Understanding the Linux Virtual Memory Manager》** — Mel Gorman
  - **這本書的定位**：把 mm 子系統講到極致的經典。VMA、address space、page fault 各有專章
  - **注意**：以較舊 kernel 為底（無 maple tree、無 per-VMA lock），架構骨架仍準確，新機制以本章的 6.12 源碼補上

- **《Linux Kernel Development, 3rd Ed.》** — Robert Love，第 15 章「The Process Address Space」
  - **和本章的關聯**：`mm_struct`/VMA/`find_vma`/page fault 的白話版；當本章源碼讀累了的緩衝，但 maple tree 部分它沒有（時代較早）

我們已經知道 fault handler 查到 VMA、決定「該配頁了」——但「配一頁匿名頁」「fork 後 CoW 複製」「一個實體頁被哪些 process 映射」這些動作的細節，還沒展開。下一章深入 demand paging 的三條路徑、CoW 的完整機制，以及讓 kernel「反查一個實體頁被誰用」的 reverse mapping（rmap）。

→ [Ch 20 demand paging、CoW、reverse mapping](./20-demand-paging-cow-rmap.md)
