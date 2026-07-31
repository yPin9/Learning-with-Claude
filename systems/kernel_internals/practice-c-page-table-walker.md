# 練習 C — page table walker 模組

> **這是 Part 3（Ch 16–23）的整合練習。** 這八章你把「虛擬記憶體」整條線走完了：一個虛擬位址怎麼經四級 page table 翻成實體位址（Ch 16）、實體頁從 buddy 出來、每頁背後有個 `struct page`（Ch 17）、slub 怎麼切小塊（Ch 18）、每個 process 的位址空間是 `mm_struct` + 一堆 VMA、碰到沒映射的頁就觸發 page fault（Ch 19）、fault handler 怎麼 demand paging 補頁、寫 CoW 頁怎麼複製、rmap 怎麼反查（Ch 20）、TLB 怎麼快取翻譯結果（Ch 23）。這個練習把它們拼成一件能動手的事：**寫一個核心模組，給定 pid + 虛擬位址，親手走一遍那個 process 的 page table，把每一級 entry、flags、最終實體位址、以及該頁的 `struct page` 全印出來——你在 Ch 16 紙上畫的那張四級表，現在要用真實 process 的真實位址驗證它。**

## 背景與動機：為什麼要「親手走」而不是查 /proc

`/proc/<pid>/pagemap` 已經能告訴你一個虛擬頁對到哪個實體 frame，`/proc/<pid>/smaps` 已經能給你每個 VMA 的 RSS/PSS。那為什麼還要自己走 page table？

因為**查介面看到的是結果，走 page table 看到的是機制**。`pagemap` 給你一個 PFN，但它不會告訴你這個翻譯是走了四級還是三級（huge page 在 PMD 級就是葉子，少走一級）、每一級的 entry 上掛了哪些 flags（`_PAGE_RW`? `_PAGE_USER`? `_PAGE_NX`?）、這個 entry 是空的（demand paging 還沒 fault，Ch 20）還是真的映射了。這些正是 Ch 16 的靈魂，而它們**只在你自己拿 `pgd_offset`/`pud_offset`/`pmd_offset`/`pte_offset_map` 一級一級 offset 下去時才看得到**。

而且這個練習逼你把 Part 3 前半的資料結構串成一條真實的呼叫鏈，每一步都對應一章：

- 從 pid 找到 `task_struct`（Ch 9 的 `find_get_task_by_vpid`）
- 從 `task->mm` 拿到 `mm_struct`（Ch 19），這是位址空間的根
- 從 `mm->pgd` 開始，用 Ch 16 的 walk 巨集一級一級下降到 PTE
- 從 PTE 的 PFN 反查 `struct page`（Ch 17 的 `pfn_to_page`），看它的 refcount、mapcount、flags
- 沿路處理三種「非典型」：huge page（PMD/PUD 級就是葉子）、沒映射（entry 為空，demand paging 未觸發，Ch 20）、CoW（唯讀 PTE 但 VMA 可寫，Ch 20）

**這是整個 Part 3 最危險的一個練習**，因為你要碰別的 process 的 `mm`。碰錯了——沒拿 `mmap_read_lock` 就 walk、`pte_offset_map` 完忘了 `pte_unmap`、拿了 `task_struct` 的 refcount 忘了 `put`——輕則讀到垃圾，重則 race 到 `mm` 被釋放、直接 panic。這個練習的一半價值，就在於逼你把「碰別人記憶體要上的那一整套鎖與 refcount 儀式」做對。這比練習 B 的 tracepoint 更講究：那裡是被動收資料，這裡是主動去別的 process 的位址空間裡撈。

**全程在 Ch 0 的 QEMU + gdb 環境驗證。** 這個練習單核就夠（不像練習 B 需要 `-smp 4`），但你需要一個「拿了記憶體、且知道自己虛擬位址」的 user 程式當標靶——參考解答附一個 `victim.c`，它 mmap 一塊記憶體、印出位址和自己的 pid，然後停住等你查。

## 先建立心智模型

動手前，先把「一個虛擬位址怎麼被拆成四段 index，一級一級走下去」在腦中畫清楚。x86_64 的 4-level paging，48 位虛擬位址拆成 4 個 9-bit index + 12-bit page offset：

```
   虛擬位址 VA（48 bit 有效）
   ┌─────────┬─────────┬─────────┬─────────┬──────────────┐
   │ PGD idx │ PUD idx │ PMD idx │ PTE idx │  page offset │
   │ 9 bit   │ 9 bit   │ 9 bit   │ 9 bit   │   12 bit     │
   └────┬────┴────┬────┴────┬────┴────┬────┴──────┬───────┘
        │         │         │         │           │
   mm->pgd        │         │         │           │
        │  pgd_offset(mm,va)│         │           │
        ▼         │         │         │           │
   ┌─PGD─┐        │         │         │           │
   │ ... │──entry─┘ p4d_offset / pud_offset       │
   │pgd_t│        ▼         │         │           │
   └─────┘   ┌─PUD─┐        │         │           │
             │pud_t│─entry──┘ pmd_offset          │
             └─────┘        ▼         │           │
                       ┌─PMD─┐        │           │
                       │pmd_t│─entry──┘ pte_offset_map
                       └──┬──┘        ▼           │
              pmd_leaf()? │      ┌─PTE─┐          │
              是 → 2MB huge│      │pte_t│──PFN──┐  │
              頁，這裡就是 │      └─────┘       │  │
              葉子，不再下降│    pte_pfn(pte) → PFN │
                          │                     ▼  ▼
                          │              實體位址 PA = (PFN << 12) | offset
                          │                     │
                          │              pfn_to_page(PFN) → struct page（Ch 17）
                          └─── 空 entry？ → 這頁還沒 fault（Ch 20 demand paging）
```

五個關鍵認知，對上 Part 3 的章節：

- **walk 的入口是 `mm->pgd`，不是某個全域表**（Ch 16/19）。每個 process 有自己的 `mm_struct`，`mm->pgd` 指向它私有的頂層 page table。kernel 空間高位映射所有 process 共享，但 user 位址走的是這個 process 自己的 `pgd`。所以第一步一定是「pid → task → task->mm → mm->pgd」，少一環就走錯表。
- **每一級可能就是葉子（huge page）**（Ch 16）。若 PMD entry 設了 `_PAGE_PSE`（`pmd_leaf(pmd)` 為真），它直接指向 2MB 大頁，PMD 就是葉子，**不能再 `pte_offset_map` 下去**（下面沒有 PTE 表，會讀到垃圾）。PUD 級同理（1GB）。walk 每級都要先問「這是葉子嗎」。
- **entry 可能是空的（`_none` / `!present`）**（Ch 20）。`mmap` 一塊記憶體不代表實體頁已配好——Linux 是 demand paging，第一次**碰**那個位址才觸發 page fault、配頁、填 PTE。所以 walk 一個「mmap 了但還沒碰過」的位址，會在某一級撞到 `pXd_none()`。這不是 bug，正是 demand paging 現形。每級都要先檢查 `_none`/`_bad`/`!present`，撞到就停回報未映射。
- **`struct page` 是實體頁的身份證**（Ch 17）。走到 PTE 拿到 PFN 後，`pfn_to_page(pfn)` 給你這頁的 `struct page`——它的 refcount（`page_count`）、被幾個 PTE 映射（`page_mapcount`，Ch 20 rmap 核心）、flags。CoW 共享的頁 `mapcount > 1`（fork 後父子共享），這是驗 CoW 的直接證據。
- **碰別人的 `mm` 要上鎖 + 抓 refcount，否則 race 到 UAF**（Ch 19）。walk 期間 target 可能正在 munmap、被 reclaim（Ch 22）換頁、或 exit 釋放 `mm`。所以必須：`find_get_task_by_vpid`（抓 task refcount）→ `get_task_mm`（抓 mm refcount）→ `mmap_read_lock(mm)`（擋並發 VMA 改動）→ walk → `mmap_read_unlock` → `mmput` → `put_task_struct`。漏一環都可能 crash，是本練習最硬的部分。

## 任務規格

### 主線任務：單一位址的 page table walk

寫一個核心模組 `ptwalk.ko`，行為如下。

**輸入介面**：透過 `/proc/ptwalk`（或 debugfs / module param）傳入 `pid` 和虛擬位址 `va`。推薦 `/proc/ptwalk`：write 一行 `<pid> <hex_va>` 進去觸發一次 walk，結果印到 `dmesg`（或存起來給 read 讀回）。這個 write handle 跑在 process context（你 `echo` 時 write syscall 進來），能睡、能 `mutex_lock`、能 `kmalloc(GFP_KERNEL)`——和練習 B 的 probe 不同，這裡的約束寬鬆。

**walk 流程**（核心，對上 Ch 16）：

1. 從 `pid` 找 `task_struct`：`find_get_task_by_vpid(pid)`（會抓 refcount，Ch 9）。找不到回 `-ESRCH`。
2. 拿 `mm`：`get_task_mm(task)`（抓 `mm` refcount；kernel thread 沒有 `mm`，回 NULL 要處理）。
3. `mmap_read_lock(mm)`（Ch 19，擋並發 VMA 改動）。
4. 從 `mm->pgd` 開始逐級 offset：`pgd_offset(mm, va)` → `p4d_offset(pgd, va)` → `pud_offset(p4d, va)` → `pmd_offset(pud, va)` → `pte_offset_map(pmd, va)`。**每一級都先檢查** `pXd_none()`（空）、`pXd_bad()`（壞）、以及 `pXd_leaf()`/`pXd_large()`（是不是 huge page 葉子）。
5. 每一級印出：這一級的 entry 原始值（`pgd_val`/`pud_val`/...）、解出來的 flags（present/rw/user/nx/accessed/dirty）、指向下一級表的實體位址。
6. 走到葉子（正常是 PTE，huge page 是 PMD/PUD）後：算 PFN（`pte_pfn`/`pmd_pfn`）、算最終實體位址 `PA = (PFN << PAGE_SHIFT) | (va & ~PAGE_MASK)`、`pfn_to_page(pfn)` 拿 `struct page`，印出它的 `page_count`、`page_mapcount`、關鍵 flags。
7. **`pte_offset_map` 成功的話，用完一定要 `pte_unmap(ptep)`**（見卡關提示 2）。
8. 收尾：`mmap_read_unlock(mm)` → `mmput(mm)` → `put_task_struct(task)`，順序和抓的順序相反。

**輸出**：對每一級印一行，格式類似（見期望輸出範例）：`PGD[idx] = 0x... (flags) -> next table PA 0x...`。最後印 PA + `struct page` 資訊。撞到空 entry 就印 `<not mapped at Lx level: demand paging not triggered>` 然後停。撞到 huge page 就印 `<huge page: PMD is leaf, 2MB>` 然後在 PMD 級算 PA。

### 進階任務：掃描整個位址空間（smaps-lite）

主線是查**一個**位址。進階把它擴成**掃整個 process 的位址空間**，對每個 VMA 統計映射狀態，輸出類似 `smaps` 的摘要——這才真正驗證你對 demand paging 和 CoW 的理解。

**遍歷 VMA**（Ch 19 的 maple tree）：v6.1 起 VMA 不再是紅黑樹 + 鏈表，改成 **maple tree**。用 `VMA_ITERATOR(vmi, mm, 0)` + `for_each_vma(vmi, vma)` 走遍所有 VMA（別再用舊教材的 `mm->mmap` 鏈表，v6.12 已經沒有那個欄位了——這是版本斷層，見卡關提示 5）。

**對每個 VMA**，走遍它涵蓋的每個虛擬頁（`vma->vm_start` 到 `vma->vm_end`，步進 `PAGE_SIZE`），對每頁 walk 到 PTE，分類統計：

- **present（真的映射了）**：walk 到底、PTE `pte_present()` 為真。這頁的 demand paging 已經觸發過。
- **not present（demand paging 未觸發）**：某一級撞到 `_none`，或 PTE 存在但 `!pte_present()`（可能被 swap 出去了，Ch 22）。這頁 mmap 了但還沒被碰過（或被換出）。
- **CoW 候選（唯讀共享）**：PTE 是**唯讀**（`!pte_write()`）但**所屬 VMA 是可寫的**（`vma->vm_flags & VM_WRITE`）。這個矛盾正是 CoW 的指紋（Ch 20）：VMA 說「這塊可寫」，但 PTE 被標唯讀，等你一寫就觸發 fault、複製一份、才給你可寫的私有副本。fork 之後父子的匿名頁全是這個狀態。

**輸出**：每個 VMA 一行摘要（起訖位址、權限、present 頁數 / 未映射頁數 / CoW 頁數），最後一行總計。對照 `/proc/<pid>/smaps` 的 `Rss`、`Private_Dirty` 等欄驗證你數對了。

### 驗收標準

| # | 檢查項 | 怎麼驗 |
|---|---|---|
| 1 | 模組 `insmod` 成功、`/proc/ptwalk` 出現 | `insmod ptwalk.ko; ls /proc/ptwalk` |
| 2 | 對 `victim` 已寫過的位址 walk，四級都印出、最後給出非零 PA 和 `struct page` | `echo "<pid> <va>" > /proc/ptwalk; dmesg \| tail` |
| 3 | 對「mmap 了但還沒碰」的位址 walk，正確報「not mapped / demand paging not triggered」 | victim mmap 後不寫，直接查那個位址 |
| 4 | 碰到 huge page（2MB）時，在 PMD 級判為葉子、不誤走 PTE、PA 算對 | 用 `MAP_HUGETLB` 或 THP 的 victim 查（見延伸挑戰） |
| 5 | `rmmod` 乾淨、無 crash、無 KASAN 報告 | `rmmod ptwalk` 後 `dmesg` 無異常 |
| 6 | 查不存在的 pid 回 `-ESRCH`、查 kernel thread（無 mm）優雅處理不 crash | `echo "999999 0x1000" > /proc/ptwalk` |
| 7 |（進階）掃整個位址空間，present/未映射/CoW 三類數字合理，present 頁數與 smaps 的 Rss 量級一致 | 跑進階版，對照 `cat /proc/<pid>/smaps` |
| 8 |（進階）fork victim 後，父子共享的匿名頁被正確標為 CoW（唯讀 PTE + 可寫 VMA） | fork 前後各掃一次對照 |

## 期望輸出範例

victim 程式先報自己的資訊：

```
/ # ./victim &
victim: pid=84
victim: mapped & touched buffer at va=0x7f3a2b4c1000
victim: sleeping, query me now...
```

對已寫過的位址 walk：

```
/ # echo "84 0x7f3a2b4c1000" > /proc/ptwalk
/ # dmesg | tail -n 14
ptwalk: === walk pid=84 va=0x00007f3a2b4c1000 ===
ptwalk:  PGD[254] = 0x00000001051c3067  [P|RW|US]      -> table PA 0x1051c3000
ptwalk:  PUD[169] = 0x0000000104e8a067  [P|RW|US]      -> table PA 0x104e8a000
ptwalk:  PMD[090] = 0x0000000103aa1067  [P|RW|US]      -> table PA 0x103aa1000
ptwalk:  PTE[193] = 0x800000010299d867  [P|RW|US|A|D|NX] -> PFN 0x10299d
ptwalk:  ---
ptwalk:  PA = 0x10299d000 (PFN 0x10299d, offset 0x000)
ptwalk:  struct page @ ffffea00040a6740
ptwalk:    _refcount = 3   mapcount = 1   (單一映射)
ptwalk:    flags: uptodate lru swapbacked  (anon page)
ptwalk: === done ===
```

四級全走到，最後給出 PA `0x10299d000` 和 `struct page`。這裡印的 `mapcount` 是 `page_mapcount(page)` 的回傳值，`= 1` 代表這頁只被一個 PTE 映射（底層 `_mapcount` 欄位從 -1 起算、`page_mapcount()` 幫你 +1，見卡關提示 4）。CoW 共享的頁這裡會 `> 1`。

對「mmap 了但沒碰」的位址：

```
/ # echo "84 0x7f3a2b4c2000" > /proc/ptwalk       # 隔壁頁，victim 沒寫過
/ # dmesg | tail -n 5
ptwalk: === walk pid=84 va=0x00007f3a2b4c2000 ===
ptwalk:  PGD[254] = 0x00000001051c3067  [P|RW|US]      -> table PA 0x1051c3000
ptwalk:  PUD[169] = 0x0000000104e8a067  [P|RW|US]      -> table PA 0x104e8a000
ptwalk:  PMD[090] = 0x0000000103aa1067  [P|RW|US]      -> table PA 0x103aa1000
ptwalk:  PTE[194] = 0x0000000000000000  <none: demand paging not triggered>
ptwalk: === done (unmapped) ===
```

PTE 是全 0（`pte_none`）——這頁 mmap 了但沒碰過，demand paging 還沒補頁。Ch 20 的核心結論在你眼前。

進階版掃整個位址空間的摘要：

```
/ # echo "scan 84" > /proc/ptwalk
/ # dmesg | tail
ptwalk: === address space scan pid=84 (comm=victim) ===
ptwalk:  vma 0x555b8e400000-0x555b8e401000 r-xp   present=1 unmapped=0 cow=0  [text]
ptwalk:  vma 0x555b8e600000-0x555b8e601000 rw-p   present=1 unmapped=0 cow=0  [data]
ptwalk:  vma 0x7f3a2b4c1000-0x7f3a2b5c1000 rw-p   present=1 unmapped=255 cow=0  [mmap anon 1MB]
ptwalk:  vma 0x7ffde1a00000-0x7ffde1a21000 rw-p   present=3 unmapped=30 cow=0  [stack]
ptwalk: === totals: present=8 unmapped=285 cow=0 ===
```

那塊 1MB 的匿名 mmap（256 頁）只有 1 頁 present、255 頁未映射——你只 touch 了第一頁，其餘 255 頁 demand paging 從沒觸發。這一行就是 demand paging 的鐵證。fork 之後再掃，那些頁會變成 `cow=...`。

## 卡關提示

1. **碰別人的 `mm` 的那一整套鎖 + refcount 儀式，漏一環都可能 crash**。正確順序：`find_get_task_by_vpid(pid)`（抓 task refcount）→ `get_task_mm(task)`（抓 mm refcount，回 NULL 表示是 kernel thread，要處理）→ `mmap_read_lock(mm)`（Ch 19，擋並發 VMA 改動）→ walk → `mmap_read_unlock(mm)` → `mmput(mm)`（放 mm refcount）→ `put_task_struct(task)`（放 task refcount）。**為什麼每一環都必要**：不抓 task refcount，target 可能在你 walk 到一半就 exit、`task` 變野指標；不抓 mm refcount，`mm` 可能被釋放（`mmput` 到 0 就 `__mmput` 掉整個位址空間）；不 `mmap_read_lock`，並發的 `munmap` 可能在你走到一半把 VMA 和它的 page table 拆掉，你 offset 到已釋放的表 → UAF。這是這個練習最容易 crash 的地方，KASAN 會在你漏鎖時報 use-after-free。

2. **`pte_offset_map` 一定要配 `pte_unmap`**。前四級（pgd/p4d/pud/pmd）的 offset 巨集只是算指標，不需要「解映射」。但 **`pte_offset_map(pmd, va)` 在某些配置（highmem、或 v6.5 起的 RCU-freed page table）下會 kmap 一個臨時映射**，用完必須 `pte_unmap(ptep)` 還回去，否則洩漏臨時映射槽、或在 preempt 計數上失衡。而且 v6.5 起 `pte_offset_map` **可能回傳 NULL**（如果這期間 pmd 被拆了），要檢查 NULL。標準寫法是 `pte_offset_map` 後立刻檢查 NULL，讀完 `pte` 值後馬上 `pte_unmap`，**不要**在持有 ptep 的期間做冗長的事。更穩的是用 `pte_offset_map_lock`（連 PTL 一起拿）再 `pte_unmap_unlock`。

3. **每一級都要先問「是不是葉子（huge page）」再決定要不要下降**。順序是：先 `pXd_none()`（空就停，未映射）→ `pXd_bad()`（壞就停）→ `pXd_leaf(pXd)` 或 `pXd_large(pXd)`（是葉子就在這級算 PA，**不要**再 offset 下一級）。如果你無腦一路 `pte_offset_map` 到底，碰到 2MB huge page（PMD 是葉子，下面根本沒有 PTE 表）時，你會把「大頁的資料」當成「PTE 表」來 offset，讀到的是頁面內容不是 page table entry，PA 全錯還可能讀到不該讀的。`pmd_leaf`/`pmd_large` 的判斷（本質是檢查 `_PAGE_PSE` 位）是 huge page 正確處理的關鍵。

4. **`struct page` 的 mapcount 從 -1/0 起算，別讀成「沒被映射」**。`page_mapcount(page)` 對「被一個 PTE 映射」的頁回傳 **1**（不是想像中的 1 起跳很直覺），但底層 `_mapcount` 欄位是從 **-1** 起算的（-1 = 沒被任何 PTE 映射），`page_mapcount` 幫你 +1 了。CoW 共享的頁（fork 後父子共享）`page_mapcount > 1`。另外 `page_count(page)`（refcount）和 mapcount 是兩回事：refcount 是「有幾個地方持有這頁的引用」（含 page cache、GUP、mapcount），mapcount 是「有幾個 PTE 指向它」。驗 CoW 看 mapcount，別看 refcount。

5. **VMA 已經是 maple tree，不是鏈表——別用 `mm->mmap`**。v6.1 起 VMA 的組織從「紅黑樹 + `mm->mmap` 單向鏈表」改成 **maple tree**。v6.12 上 `struct mm_struct` **沒有 `mmap` 欄位了**，抄舊教材寫 `for (vma = mm->mmap; vma; vma = vma->vm_next)` 會直接編譯失敗（`vm_next` 也沒了）。正確做法是 `VMA_ITERATOR(vmi, mm, start_addr)` 配 `for_each_vma(vmi, vma)`（宣告在 `include/linux/mm.h`）。這是 Ch 19 特別標的版本斷層，maple tree 是 v6.1 記憶體管理的大改動。

## 分步實作建議

1. **先把 refcount 儀式弄對，不 walk**。`.proc_write` 解析 pid，`find_get_task_by_vpid` → `get_task_mm` → 印 `mm->pgd`，然後**立刻**補上 `mmput` + `put_task_struct`。故意查 kernel thread（pid 2 `kthreadd`），確認 `get_task_mm` 回 NULL 時優雅處理。這步把最容易 crash 的地方先弄對。
2. **加 walk 到 PTE，先只處理「四級都在」**。`mmap_read_lock` 後逐級 offset，每級加 `pXd_none`/`pXd_bad` 檢查（撞到印 unmapped 停下），`pte_offset_map` 後檢查 NULL、讀完 `pte_unmap`。用 victim 已寫過的位址測，四級應全印出。
3. **加 flags + PA + struct page**。解每級 entry 的 present/rw/user/nx/accessed/dirty 位。走到 PTE 後 `pte_pfn` → PA → `pfn_to_page` → 印 `page_count`/`page_mapcount`/flags。
4. **加 huge page 處理**。PMD/PUD 級加 `pmd_leaf`/`pud_leaf` 判斷，是葉子就在該級算 PA、**不再** `pte_offset_map`。用 `MAP_HUGETLB` 的 victim 測。
5. **做進階：VMA 掃描**。`VMA_ITERATOR` + `for_each_vma` 遍歷，對每 VMA 逐頁 walk，統計 present/unmapped/CoW，對照 `/proc/<pid>/smaps` 的 Rss。fork victim 後再掃看 CoW 頁出現。

## 完整參考解答

<details>
<summary>點開看完整可編譯解答（ptwalk.c + Makefile + victim.c + 測試腳本）</summary>

### `ptwalk.c`（主線 + 進階掃描）

```c
// ptwalk.c — page table walker 模組（練習 C）
// 透過 /proc/ptwalk 接收指令：
//   echo "<pid> <hexva>" > /proc/ptwalk   → walk 單一位址（結果印到 dmesg）
//   echo "scan <pid>"    > /proc/ptwalk   → 掃整個位址空間，統計 present/unmapped/cow
#include <linux/init.h>
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/proc_fs.h>
#include <linux/uaccess.h>         // copy_from_user
#include <linux/sched.h>           // task_struct
#include <linux/sched/mm.h>        // get_task_mm / mmput
#include <linux/sched/task.h>      // put_task_struct
#include <linux/pid.h>             // find_get_task_by_vpid
#include <linux/mm.h>              // mm_struct / VMA_ITERATOR / for_each_vma
#include <linux/mmap_lock.h>       // mmap_read_lock / mmap_read_unlock
#include <linux/pgtable.h>         // pXd_offset / pXd_none / pXd_leaf ...
#include <linux/highmem.h>         // pte_offset_map / pte_unmap（部分配置）
#include <asm/pgtable.h>

// 把 x86_64 PTE 常見 flags 解成人看得懂的字串
static void fmt_flags(unsigned long v, char *buf, size_t n)
{
    // 這些 _PAGE_* 定義在 arch/x86/include/asm/pgtable_types.h
    snprintf(buf, n, "[%s%s%s%s%s%s]",
             (v & _PAGE_PRESENT)  ? "P|"  : "",
             (v & _PAGE_RW)       ? "RW|" : "RO|",
             (v & _PAGE_USER)     ? "US|" : "SU|",
             (v & _PAGE_ACCESSED) ? "A|"  : "",
             (v & _PAGE_DIRTY)    ? "D|"  : "",
             (v & _PAGE_NX)       ? "NX"  : "");
}

// ---- 主線：walk 單一虛擬位址 ----
// 假設呼叫者已 get_task_mm + mmap_read_lock（在原子性上這個 walk 不睡）
static void do_walk_locked(struct mm_struct *mm, unsigned long va)
{
    pgd_t *pgd; p4d_t *p4d; pud_t *pud; pmd_t *pmd; pte_t *ptep, pte;
    char fb[48];
    unsigned long pfn, pa, off = va & ~PAGE_MASK;
    struct page *page;

    pgd = pgd_offset(mm, va);
    if (pgd_none(*pgd) || pgd_bad(*pgd)) {
        pr_info("ptwalk:  PGD[%03lu] = 0x%016lx  <none: demand paging not triggered>\n",
                pgd_index(va), pgd_val(*pgd));
        return;
    }
    fmt_flags(pgd_val(*pgd), fb, sizeof(fb));
    pr_info("ptwalk:  PGD[%03lu] = 0x%016lx  %-14s -> table PA 0x%lx\n",
            pgd_index(va), pgd_val(*pgd), fb, (unsigned long)pgd_val(*pgd) & PTE_PFN_MASK);

    // p4d：4-level 下摺疊（p4d == pgd）；保留這步讓 5-level(la57) 也對
    p4d = p4d_offset(pgd, va);
    if (p4d_none(*p4d) || p4d_bad(*p4d)) { pr_info("ptwalk:  P4D <none>\n"); return; }

    pud = pud_offset(p4d, va);
    if (pud_none(*pud) || pud_bad(*pud)) {
        pr_info("ptwalk:  PUD[%03lu] = 0x%016lx  <none: demand paging not triggered>\n",
                pud_index(va), pud_val(*pud));
        return;
    }
    fmt_flags(pud_val(*pud), fb, sizeof(fb));
    pr_info("ptwalk:  PUD[%03lu] = 0x%016lx  %-14s -> table PA 0x%lx\n",
            pud_index(va), pud_val(*pud), fb, (unsigned long)pud_val(*pud) & PTE_PFN_MASK);
    if (pud_leaf(*pud)) {            // 1GB huge page：PUD 就是葉子
        pfn = pud_pfn(*pud);
        pa  = (pfn << PAGE_SHIFT) | (va & ~PUD_MASK);
        pr_info("ptwalk:  <huge page: PUD is leaf, 1GB> PA = 0x%lx\n", pa);
        return;
    }

    pmd = pmd_offset(pud, va);
    if (pmd_none(*pmd) || pmd_bad(*pmd)) {
        pr_info("ptwalk:  PMD[%03lu] = 0x%016lx  <none: demand paging not triggered>\n",
                pmd_index(va), pmd_val(*pmd));
        return;
    }
    fmt_flags(pmd_val(*pmd), fb, sizeof(fb));
    pr_info("ptwalk:  PMD[%03lu] = 0x%016lx  %-14s -> table PA 0x%lx\n",
            pmd_index(va), pmd_val(*pmd), fb, (unsigned long)pmd_val(*pmd) & PTE_PFN_MASK);
    if (pmd_leaf(*pmd)) {            // 2MB huge page：PMD 就是葉子，別再 pte_offset_map
        pfn = pmd_pfn(*pmd);
        pa  = (pfn << PAGE_SHIFT) | (va & ~PMD_MASK);
        pr_info("ptwalk:  <huge page: PMD is leaf, 2MB> PA = 0x%lx\n", pa);
        page = pfn_to_page(pfn);
        pr_info("ptwalk:  struct page @ %px  _refcount=%d mapcount=%d\n",
                page, page_count(page), page_mapcount(page));
        return;
    }

    // 走到最後一級。pte_offset_map 可能回 NULL（v6.5+ RCU-freed pmd）
    ptep = pte_offset_map(pmd, va);
    if (!ptep) {
        pr_info("ptwalk:  <pte_offset_map returned NULL: pmd changed under us>\n");
        return;
    }
    pte = *ptep;
    pte_unmap(ptep);                // ★ 讀出值後立刻 unmap（卡關提示 2）

    if (pte_none(pte)) {
        pr_info("ptwalk:  PTE[%03lu] = 0x%016lx  <none: demand paging not triggered>\n",
                pte_index(va), pte_val(pte));
        return;
    }
    if (!pte_present(pte)) {         // 存在但不在記憶體：可能被 swap 出（Ch 22）
        pr_info("ptwalk:  PTE[%03lu] = 0x%016lx  <not present: swapped out?>\n",
                pte_index(va), pte_val(pte));
        return;
    }
    fmt_flags(pte_val(pte), fb, sizeof(fb));
    pfn = pte_pfn(pte);
    pr_info("ptwalk:  PTE[%03lu] = 0x%016lx  %-14s -> PFN 0x%lx\n",
            pte_index(va), pte_val(pte), fb, pfn);

    pa = (pfn << PAGE_SHIFT) | off;
    pr_info("ptwalk:  ---\n");
    pr_info("ptwalk:  PA = 0x%lx (PFN 0x%lx, offset 0x%03lx)\n", pa, pfn, off);

    if (pfn_valid(pfn)) {
        page = pfn_to_page(pfn);
        pr_info("ptwalk:  struct page @ %px\n", page);
        pr_info("ptwalk:    _refcount = %d   mapcount = %d\n",
                page_count(page), page_mapcount(page));   // mapcount = page_mapcount()（已 +1）
        // CoW 指紋：PTE 唯讀但這頁被多個 PTE 映射
        if (!pte_write(pte) && page_mapcount(page) > 1)
            pr_info("ptwalk:    (CoW candidate: readonly PTE, mapcount>1)\n");
    } else {
        pr_info("ptwalk:  (PFN not valid: special/reserved mapping)\n");
    }
}

// 主線入口：抓 task/mm、上鎖、walk、按相反順序放回（卡關提示 1）
static int walk_one(pid_t pid, unsigned long va)
{
    struct task_struct *task;
    struct mm_struct *mm;

    task = find_get_task_by_vpid(pid);      // 抓 task refcount
    if (!task) {
        pr_info("ptwalk: no such pid %d\n", pid);
        return -ESRCH;
    }
    mm = get_task_mm(task);                  // 抓 mm refcount；kernel thread 回 NULL
    if (!mm) {
        pr_info("ptwalk: pid %d has no mm (kernel thread?)\n", pid);
        put_task_struct(task);
        return -EINVAL;
    }

    pr_info("ptwalk: === walk pid=%d va=0x%016lx ===\n", pid, va);
    mmap_read_lock(mm);                      // 擋並發 munmap/VMA 改動（Ch 19）
    do_walk_locked(mm, va);
    mmap_read_unlock(mm);
    pr_info("ptwalk: === done ===\n");

    mmput(mm);                               // 放 mm refcount（相反順序）
    put_task_struct(task);                   // 放 task refcount
    return 0;
}

// ---- 進階：掃整個位址空間，統計 present/unmapped/cow ----
// classify_page：逐頁 walk 只分三類（huge page 直接計 present）。
// 各級 none/bad/leaf 的判斷邏輯同 do_walk_locked，這裡只保留計數骨架。
static void classify_page(struct mm_struct *mm, struct vm_area_struct *vma,
                          unsigned long va, u64 *present, u64 *unmapped, u64 *cow)
{
    pgd_t *pgd = pgd_offset(mm, va);
    p4d_t *p4d; pud_t *pud; pmd_t *pmd; pte_t *ptep, pte;

    if (pgd_none(*pgd) || pgd_bad(*pgd)) { (*unmapped)++; return; }
    p4d = p4d_offset(pgd, va);
    if (p4d_none(*p4d) || p4d_bad(*p4d)) { (*unmapped)++; return; }
    pud = pud_offset(p4d, va);
    if (pud_none(*pud) || pud_bad(*pud)) { (*unmapped)++; return; }
    if (pud_leaf(*pud)) { (*present)++; return; }          // 1GB huge → present
    pmd = pmd_offset(pud, va);
    if (pmd_none(*pmd) || pmd_bad(*pmd)) { (*unmapped)++; return; }
    if (pmd_leaf(*pmd)) { (*present)++; return; }           // 2MB huge → present

    ptep = pte_offset_map(pmd, va);
    if (!ptep) { (*unmapped)++; return; }
    pte = *ptep;
    pte_unmap(ptep);
    if (pte_none(pte) || !pte_present(pte)) { (*unmapped)++; return; }
    (*present)++;
    if ((vma->vm_flags & VM_WRITE) && !pte_write(pte))     // CoW 指紋：VMA 可寫但 PTE 唯讀
        (*cow)++;
}

static int scan_addrspace(pid_t pid)
{
    struct task_struct *task;
    struct mm_struct *mm;
    struct vm_area_struct *vma;
    u64 tp = 0, tu = 0, tc = 0;

    task = find_get_task_by_vpid(pid);
    if (!task) return -ESRCH;
    mm = get_task_mm(task);
    if (!mm) { put_task_struct(task); return -EINVAL; }

    pr_info("ptwalk: === address space scan pid=%d (comm=%s) ===\n", pid, task->comm);
    mmap_read_lock(mm);
    {
        VMA_ITERATOR(vmi, mm, 0);            // maple tree 迭代器（Ch 19，卡關提示 5）
        for_each_vma(vmi, vma) {
            u64 p = 0, u = 0, c = 0;
            unsigned long va;
            for (va = vma->vm_start; va < vma->vm_end; va += PAGE_SIZE)
                classify_page(mm, vma, va, &p, &u, &c);
            pr_info("ptwalk:  vma 0x%lx-0x%lx %c%c%c%c  present=%llu unmapped=%llu cow=%llu\n",
                    vma->vm_start, vma->vm_end,
                    (vma->vm_flags & VM_READ)  ? 'r' : '-',
                    (vma->vm_flags & VM_WRITE) ? 'w' : '-',
                    (vma->vm_flags & VM_EXEC)  ? 'x' : '-',
                    (vma->vm_flags & VM_SHARED)? 's' : 'p',
                    p, u, c);
            tp += p; tu += u; tc += c;
        }
    }
    mmap_read_unlock(mm);
    pr_info("ptwalk: === totals: present=%llu unmapped=%llu cow=%llu ===\n", tp, tu, tc);

    mmput(mm);
    put_task_struct(task);
    return 0;
}

// ---- /proc/ptwalk 的 write 介面（process context，能睡）----
static ssize_t ptwalk_write(struct file *f, const char __user *ubuf,
                            size_t len, loff_t *off)
{
    char kbuf[64];
    pid_t pid;
    unsigned long va;

    if (len >= sizeof(kbuf))
        return -EINVAL;
    if (copy_from_user(kbuf, ubuf, len))   // 這裡能 copy_from_user（能睡的 context）
        return -EFAULT;
    kbuf[len] = '\0';

    if (sscanf(kbuf, "scan %d", &pid) == 1) {
        scan_addrspace(pid);
        return len;
    }
    if (sscanf(kbuf, "%d %lx", &pid, &va) == 2) {
        walk_one(pid, va);
        return len;
    }
    pr_info("ptwalk: usage: '<pid> <hexva>' or 'scan <pid>'\n");
    return -EINVAL;
}

static const struct proc_ops ptwalk_pops = {
    .proc_write = ptwalk_write,
    .proc_lseek = noop_llseek,
};

static struct proc_dir_entry *ptwalk_entry;

static int __init ptwalk_init(void)
{
    ptwalk_entry = proc_create("ptwalk", 0222, NULL, &ptwalk_pops);  // 0222 = 只寫
    if (!ptwalk_entry)
        return -ENOMEM;
    pr_info("ptwalk: ready. echo '<pid> <hexva>' or 'scan <pid>' > /proc/ptwalk\n");
    return 0;
}

static void __exit ptwalk_exit(void)
{
    proc_remove(ptwalk_entry);
    pr_info("ptwalk: unloaded\n");
}

module_init(ptwalk_init);
module_exit(ptwalk_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Practice C: page table walker");
MODULE_AUTHOR("kernel_internals");
```

**幾個設計決定的理由**：抓 task/mm、`mmap_read_lock`、相反順序放回的儀式見卡關提示 1；`pte_offset_map` 讀值後立刻 `pte_unmap`（並檢查 NULL）見卡關提示 2；每級先判 `pXd_leaf` 再下降見卡關提示 3。掃描版把 huge page 直接計為 present（想精確可乘它涵蓋的頁數）。`/proc/ptwalk` 用 0222（只寫）是因為它是「觸發器」介面——寫指令進去、結果去 `dmesg` 看；想 read 讀回就存 buffer 加 `.proc_read`（延伸挑戰 2）。

### `Makefile`

```makefile
# 注意：recipe 行首是 Tab 不是空白
obj-m += ptwalk.o

KDIR := /path/to/your/linux-6.12      # 指向你 Ch 0 build 的源碼樹

all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules
clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean
```

```bash
make
ls ptwalk.ko
cp ptwalk.ko initramfs/     # 放進 Ch 0 的 initramfs，重打包 cpio
```

### `victim.c`（測試標靶：mmap 一塊記憶體、印出 va 與 pid、停住等查）

```c
// victim.c — 給 ptwalk 查的標靶
// mmap 一塊 1MB 匿名記憶體，只 touch 第一頁（其餘頁 demand paging 不觸發），
// 印出 pid 和 buffer 位址，然後睡著等你查。
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/mman.h>

#define SZ (1024 * 1024)     // 1MB = 256 頁

int main(void)
{
    char *buf = mmap(NULL, SZ, PROT_READ | PROT_WRITE,
                     MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (buf == MAP_FAILED) { perror("mmap"); return 1; }

    buf[0] = 'A';            // 只碰第一頁 → 只有第一頁 demand paging 觸發

    printf("victim: pid=%d\n", getpid());
    printf("victim: mapped & touched buffer at va=%p\n", buf);
    printf("victim: page 0 touched, pages 1..255 NOT touched\n");
    printf("victim: sleeping, query me now...\n");
    fflush(stdout);

    for (;;) pause();        // 睡死，讓 va 穩定不變，方便你查
    return 0;
}
```

編：`gcc -static -O0 -o victim victim.c`，放進 initramfs。`-O0` 避免編譯器優化掉 `buf[0]='A'`。

### huge page 版 victim（測驗收 #4）

測 PMD leaf 路徑要一塊 2MB 大頁。把 victim 的 mmap 改成 `MAP_PRIVATE|MAP_ANONYMOUS|MAP_HUGETLB`、size 改 `2*1024*1024`、`b[0]='H'`（開機前 `echo 8 > /proc/sys/vm/nr_hugepages` 預留大頁）。若拿不到大頁，THP（透明大頁）也會產生 PMD leaf，但何時合併不可控，`MAP_HUGETLB` 較穩。

### 跑法（在 QEMU 的 busybox shell 裡）

```sh
insmod /ptwalk.ko
./victim &                        # 印出它的 pid 和 va
# 從 victim 輸出抄 pid 和 va，然後：
echo "<pid> <va>"        > /proc/ptwalk   # 查已 touch 的第一頁 → 四級全印
echo "<pid> <va+0x1000>" > /proc/ptwalk   # 查沒 touch 的第二頁 → unmapped
echo "scan <pid>"        > /proc/ptwalk   # 掃整個位址空間
dmesg | tail -n 20
```

</details>

## 測試用例表

| 測試 | 操作 | 期望結果 | 對應驗收 |
|---|---|---|---|
| 載入 | `insmod ptwalk.ko` | 回 0；`/proc/ptwalk` 存在 | #1 |
| 已映射頁 | 對 victim 已 touch 的 va walk | 四級全印、PA 非零、`struct page` 有值 | #2 |
| 未映射頁 | 對 victim mmap 但沒 touch 的 va（第一頁 +0x1000）walk | PTE（或某級）為 none，報 demand paging not triggered | #3 |
| huge page | 對 `victim_huge` 的 va walk | PMD 判為 leaf、報 2MB、PA 算對、不誤走 PTE | #4 |
| 不存在的 pid | `echo "999999 0x1000" > /proc/ptwalk` | 回 `-ESRCH`、`dmesg` 報 no such pid、不 crash | #6 |
| kernel thread | `echo "2 0xffffffff81000000" > /proc/ptwalk`（pid 2 = kthreadd）| 報 no mm、不 crash | #6 |
| 整體掃描 | `echo "scan <pid>" > /proc/ptwalk` | 每 VMA 一行、present/unmapped/cow 合理，那塊 1MB anon 顯示 present=1 unmapped=255 | #7 |
| 對照 smaps | 掃描後對照 `cat /proc/<pid>/smaps` 的 Rss | present 頁數 × 4KB 與 Rss 量級一致 | #7 |
| CoW 驗證 | fork victim，父子各掃一次 | fork 後匿名頁變 cow（唯讀 PTE + 可寫 VMA） | #8 |
| 卸載 | `rmmod ptwalk` | `/proc/ptwalk` 消失、`dmesg` 無異常 | #5 |
| KASAN 檢查 | 開 KASAN 跑一輪查詢-掃描-卸載 | 無 use-after-free 報告 | #5 |
| 漏鎖對照 | 把 `mmap_read_lock`/`get_task_mm` 註解掉，並發 munmap 時查 | KASAN 報 UAF（證明鎖與 refcount 必要） | 邊界 |

> **要看到「漏鎖 race」得先弄壞它**：想確認 `get_task_mm` + `mmap_read_lock` 真的有用，把它們註解掉重編，然後一邊反覆 `echo "<pid> <va>"` 查、一邊讓 victim 反覆 mmap/munmap（或直接 kill victim 讓它 exit），開 KASAN 的 kernel 會偶發報 use-after-free（你 walk 到已釋放的 page table 或 mm）。加回鎖與 refcount 就穩。這是 Ch 19 「碰別人的 mm 要上鎖」的活教材。

## 卡關時的 gdb 用法

延續 Ch 0 的 QEMU + gdb（`-s`）。`insmod` 後 `lx-symbols` 載模組符號，停進你的 walk：

```gdb
(gdb) lx-symbols
(gdb) break do_walk_locked
(gdb) continue
```

回 QEMU `echo "<pid> <va>" > /proc/ptwalk` 觸發，gdb 停進 `do_walk_locked`。一級一級看：

```gdb
(gdb) print/x pgd_val(*pgd_offset(mm, va))    # 印 PGD entry 原始值
(gdb) print pgd_index(va)                      # 這個 va 落在 PGD 的哪個 index
(gdb) next                                     # 單步下降各級
(gdb) print/x pmd_val(*pmd)                    # PMD entry
(gdb) print pmd_leaf(*pmd)                     # 是不是 huge page 葉子
(gdb) print/x pte_val(pte)                     # 最終 PTE
(gdb) print pte_pfn(pte)                        # PFN
(gdb) print pfn_to_page(pte_pfn(pte))->_refcount  # struct page 的 refcount
```

把這個和 Ch 16 的 gdb 實驗接起來：那章你可能是用 `lx-current` 看 kernel 自己的映射，這裡你是**用真實 user process 的 mm** 走完整條 user 位址翻譯。想親眼看「demand paging 補頁」，在 victim 還沒 touch 某頁時查（PTE 是 none），然後讓 victim touch 那頁（改 victim 加一行 `buf[4096]='B'`），再查同一位址——你會看到同一個 PTE 從 `0x0`（none）變成一個有 PFN 的值。**這就是 Ch 20 的 page fault 在你眼前把 PTE 填上了。** 你甚至可以 `break handle_mm_fault` 停在 fault handler 裡，看它一步步把那個 none PTE 變成 present。

## 踩雷集錦

1. **碰別人的 `mm` 不上鎖 / 不抓 refcount → UAF panic**。本練習最致命的陷阱。target 可能在你 walk 到一半就 munmap、被 reclaim 換頁、或 exit 釋放 `mm`。必須 `find_get_task_by_vpid` → `get_task_mm` → `mmap_read_lock` → walk → 相反順序放回。漏 `mmap_read_lock` 會 offset 到正被拆除的 page table，漏 `mmput` 洩漏 mm refcount。KASAN 開著時漏鎖會報 use-after-free。

2. **`pte_offset_map` 忘了 `pte_unmap` 或沒檢查 NULL**。它在部分配置會建臨時映射，用完必須 `pte_unmap`；且 v6.5 起可能回 NULL（pmd 被拆），不檢查就解參考 NULL → oops。標準寫法：`ptep = pte_offset_map(pmd, va); if (!ptep) return; pte = *ptep; pte_unmap(ptep);`。

3. **碰到 huge page 還無腦走到 PTE**。2MB 大頁 PMD 就是葉子，下面沒有 PTE 表。不判 `pmd_leaf`/`pmd_large` 就 `pte_offset_map(pmd, va)`，你把大頁的資料內容當 PTE 表 offset，讀到的 PFN 是垃圾，PA 全錯。每一級 offset 下一級之前，先問 `pXd_leaf`。1GB 頁在 PUD 級同理。

4. **把 `page_mapcount` 和 `page_count`（refcount）搞混來驗 CoW**。驗 CoW 共享看的是 **mapcount**（有幾個 PTE 指向這頁），fork 後父子共享同一實體頁 → mapcount > 1。refcount 是「有幾個引用」，含 page cache、GUP 等，數字對不上 CoW 語義。而且 mapcount 底層 `_mapcount` 從 -1 起算，`page_mapcount()` 幫你 +1，別讀成「沒映射」。

5. **用舊教材的 `mm->mmap` 鏈表遍歷 VMA，編譯直接失敗**。v6.1 起 VMA 是 maple tree，`struct mm_struct` 沒有 `mmap` 欄位、`vm_area_struct` 沒有 `vm_next`。抄 `for (vma = mm->mmap; vma; vma = vma->vm_next)` 會編不過。用 `VMA_ITERATOR(vmi, mm, 0)` + `for_each_vma(vmi, vma)`。這是 Ch 19 標記的 maple tree 版本斷層。

## 延伸挑戰

1. **驗 CoW：fork 前後查同一 VA 看 PA 變化**。改 victim：`buf[0]='A'` 後 `printf` 位址、`fork()`。父子都停住。先查父的那個 va（拿到 PA 和 mapcount）——fork 後父子共享，mapcount 應為 2、PTE 唯讀（CoW 標記）。然後讓子 process **寫** `buf[0]='B'`（觸發 CoW fault），再查子的同一 va——PA 應**變了**（copy 出新頁）、新頁 mapcount 回 1、PTE 變可寫。你親眼看到 CoW「一寫就分家」。這是 Ch 20 最精華的實驗。

2. **加 read 介面把結果讀回**（不只印 dmesg）：把 walk 結果存進一個 per-open buffer 或全域 buffer，加 `.proc_read` + seq_file，`cat /proc/ptwalk` 讀回上次 walk 的結果。比翻 `dmesg` 乾淨。

3. **反查：給 PFN 找誰映射它（rmap）**。進階到 Ch 20 的 rmap：拿到一個 `struct page`（`pfn_to_page`），用 `rmap_walk` 或手動走 `page->mapping`（anon_vma / address_space）找出「有哪些 VMA、哪些 process 映射了這個實體頁」。這是反方向的 walk——從實體頁找回所有虛擬映射，正是 reclaim（Ch 22）換頁時要做的事。

4. **量 walk 的成本，對照 TLB**（Ch 23）：連續 walk 同一位址 N 次計時，再對照「CPU 硬體 walk + TLB」的成本。你的軟體 walk 每次都爬四級記憶體，硬體有 TLB 快取翻譯結果——用這個對比理解 Ch 23 為什麼 TLB 是效能命脈。

5. **處理 swap entry**（Ch 22）：PTE `!pte_present()` 但 `!pte_none()` 時，它可能是個 swap entry（頁被換出到 swap）。解析 `pte_to_swp_entry(pte)` 拿到 swap type + offset，印出「這頁在 swap 的哪裡」。這把 Ch 22 的 reclaim/swap 和 page table 接起來。

6. **支援 5-level paging**（la57）：現代 x86_64 可能開 5-level（多一級 P4D 真的有作用）。目前 `p4d_offset` 在 4-level 下是摺疊的（p4d == pgd）。在開 `CONFIG_X86_5LEVEL` 且 CPU 支援 la57 的環境測，看 P4D 級真的參與 walk。

## 自我檢核

- [ ] 不看解答，能說出「碰別人的 mm」要做的完整儀式（`find_get_task_by_vpid` → `get_task_mm` → `mmap_read_lock` → walk → 相反順序放回），並解釋每一環防的是什麼 race（Ch 9/19）
- [ ] 能解釋為什麼 `pte_offset_map` 要配 `pte_unmap`、且 v6.5 起要檢查 NULL 回傳（Ch 16）
- [ ] 能說出每一級 walk 要先判 `pXd_none`（未映射）、再判 `pXd_leaf`（huge page）、才決定下降；並解釋不判 leaf 碰到 2MB 大頁會怎麼錯（Ch 16）
- [ ] 能解釋「mmap 了但 walk 撞到 none PTE」代表什麼——demand paging 還沒觸發（Ch 20）
- [ ] 能說清驗 CoW 為什麼看 `page_mapcount` 而非 `page_count`，以及「唯讀 PTE + 可寫 VMA」為什麼是 CoW 的指紋（Ch 20）
- [ ] 能解釋 v6.1 起 VMA 為什麼不能再用 `mm->mmap` 鏈表遍歷，要用 `VMA_ITERATOR`/`for_each_vma`（Ch 19 的 maple tree）
- [ ] 能從 PTE 的 PFN 算出最終實體位址 `PA = (PFN << PAGE_SHIFT) | (va & ~PAGE_MASK)`，並解釋 page offset 那 12 位的來歷（Ch 16）
- [ ] 面試被問「怎麼判斷一個虛擬位址有沒有被實際映射」，能答出「walk 到 PTE 看 `pte_present`，中途撞 `_none` 就是沒映射（demand paging 未觸發）」
- [ ] 能用 `lx-symbols` + `break do_walk_locked` 停進 walk，`print pmd_leaf(*pmd)`、`print pte_pfn(pte)` 看每級狀態

## 這個練習把哪些章拼在了一起

- **Ch 9 task_struct**：`find_get_task_by_vpid` 從 pid 找 task、`task->mm` 取位址空間根、refcount 的抓/放
- **Ch 16 虛擬記憶體與 page table**：整個練習的骨幹——`pgd_offset`/`pud_offset`/`pmd_offset`/`pte_offset_map` 的逐級 walk、flags 解析、huge page 的 leaf 判斷、PFN→PA 計算
- **Ch 17 buddy allocator**：`pfn_to_page` 從 PFN 拿 `struct page`，讀它的 refcount/mapcount/flags——每個實體頁背後那個身份證
- **Ch 19 mm_struct/VMA/page fault**：`get_task_mm`/`mmap_read_lock` 的鎖與 refcount 儀式、maple tree 的 `VMA_ITERATOR`/`for_each_vma` 遍歷 VMA
- **Ch 20 demand paging/CoW/rmap**：撞到 none PTE = demand paging 未觸發、「唯讀 PTE + 可寫 VMA」= CoW 指紋、mapcount>1 = 共享、fork 前後 PA 變化的實驗
- **Ch 22 reclaim/swap**：`!pte_present` 但 `!pte_none` 的 PTE 可能是被換出的 swap entry（延伸挑戰）
- **Ch 23 TLB**：你手動 walk 的每一步，就是 CPU 硬體 page table walker 在做的事；TLB 快取的正是這個 walk 的結果（延伸挑戰量成本）

做完這個練習，你手上有一個能把「任意 process 的任意虛擬位址」一路翻到實體頁、看清每一級 entry 與 flags、還能掃全位址空間驗證 demand paging 和 CoW 的工具——你不只讀懂了 Ch 16 那張四級表，還親手拿真實位址走通了它，並碰過了「動別人記憶體」的每一個安全陷阱。Part 3 的記憶體翻譯到此完整。接下來 Part 4 換一個維度：前面碰到的所有「並發」——per-CPU 計數、`this_cpu_inc`、`mmap_read_lock`——底層都建立在一組更原始的東西上：原子操作與 memory ordering。我們從「一個 `count++` 為什麼在多核上是錯的」開始，把並行的地基挖到最底。

→ [Ch 24 atomic 操作與 memory ordering](./24-atomics-memory-ordering.md)
