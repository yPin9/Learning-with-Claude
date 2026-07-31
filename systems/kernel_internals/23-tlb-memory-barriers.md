# Ch 23 — TLB、memory barrier、cache coherence

> **目標**：理解硬體與 kernel 交界處的三件事——TLB 怎麼快取位址翻譯、改了 page table 為什麼要跨核 shootdown、多核共享記憶體為什麼會亂序而 kernel 用哪些 barrier 修正它。學完你能看懂 `smp_load_acquire` 為什麼在 x86 是 no-op 而 ARM64 是真指令，能用 perf 量 TLB miss、能寫出（並修掉）一段 false sharing 拖垮效能的程式。這章是 Part 4 整個同步子系統的地基。

## 為什麼需要這個？

前面 Part 3 我們把 mm 的靜態結構讀完了：page table（Ch 16）、buddy/slub（Ch 17–18）、VMA 與 page fault（Ch 19）、CoW/rmap（Ch 20）、page cache（Ch 21）、reclaim（Ch 22）。這些章有一個共同的隱含假設沒被戳破：**「改了 page table，CPU 下一次存取就會看到新的翻譯」**、以及**「一個 CPU 寫進記憶體的東西，另一個 CPU 馬上讀得到」**。

兩個假設在多核硬體上**都不成立**，而 kernel 花了大量程式碼在補這兩個洞：

- page table walk（Ch 16 那條 PGD→PUD→PMD→PTE 五層走訪）每次要讀四五次記憶體，太貴了。CPU 用 **TLB** 把翻譯結果快取起來。可是 reclaim 把一個 page 收走、CoW 把 PTE 改成唯讀（Ch 20）、`munmap` 拆掉一段映射之後，**別的 CPU 的 TLB 裡還存著舊翻譯**——它會拿舊的物理位址繼續讀寫，讀到已經被回收甚至被別人拿去用的記憶體。這是資料損毀，不是效能問題。kernel 必須**通知每一顆 CPU 把舊翻譯丟掉**，這就是 TLB shootdown。

- Ch 7 我們講過並行的痛，但當時停在「兩個 CPU 同時改一個變數會 race」。真相更深一層：就算你以為程式是照著寫的順序執行，**CPU 和編譯器都會重排記憶體存取**。單執行緒看不出來（硬體保證你自己看到的結果一致），但另一顆 CPU 看你的記憶體寫入時，順序可能完全不同。這就是為什麼「先寫 data，再把 ready flag 設 1」在 ARM64 上另一顆核可能先看到 ready=1 卻讀到舊的 data。**memory barrier** 就是強迫排序的工具。

這三件事——TLB、ordering、coherence——是硬體行為，不是 kernel 發明的。但 kernel 是**唯一要正面對付它們的軟體**，而且 x86 和 ARM64 在這裡的差異比任何其他子系統都大。這章把這條硬體與 kernel 的邊界講清楚。

## 先建立直覺

三個概念常被混在一起，先用一張圖把它們的職責分開：

```
   ┌──────────────────────────────────────────────────────────────────────┐
   │  CPU core                                                              │
   │                                                                        │
   │   VA ──►┌─────────┐ hit ──► PA          「翻譯快取」                    │
   │         │  TLB    │                     TLB：VA→PA 的快取              │
   │         └────┬────┘ miss                問題：改了 page table 要同步   │
   │              ▼                                                         │
   │         page table walk（Ch 16，慢）                                   │
   │                                                                        │
   │   load/store ──►┌──────────────┐        「排序」                       │
   │                 │ store buffer │        memory ordering：存取被重排    │
   │                 │  / 亂序執行   │        問題：多核看到的順序不一致     │
   │                 └──────┬───────┘        修正：memory barrier          │
   │                        ▼                                              │
   │   ┌────────┐   MESI 協議   ┌────────┐   「一致性」                     │
   │   │ L1 快取 │◄────────────►│ 其他核  │   cache coherence：同一 line    │
   │   └────────┘  硬體自動維護  │ 的快取  │   多核最終看到一致值            │
   │                            └────────┘   由硬體保證，不用你管           │
   └──────────────────────────────────────────────────────────────────────┘
```

三句話記住它們的分工：

- **TLB** 解決「翻譯太慢」，代價是「改了 page table 要主動讓 TLB 失效」。
- **cache coherence（MESI）**由硬體保證，你不用寫任何指令去維護它——它保證**同一條 cache line 的值最終所有核看到一致**。
- **memory ordering** 是 coherence **管不到**的：coherence 保證單一位址的最終一致，但**不同位址之間的可見順序**才是多核 bug 的來源，這要靠 memory barrier。

最容易搞混的是後兩者。記住這句話，本章一半的內容就懂了：**coherence ≠ ordering。coherence 管一個變數的最終值，ordering 管多個變數之間你看到的先後。** MESI 讓你不用擔心「讀到過期的 A」，但它不阻止你「先看到 B 的新值再看到 A 的新值」，即使程式是先寫 A 再寫 B。

## TLB：位址翻譯的快取

Ch 16 我們走過一次 page table walk：一個虛擬位址要經過 PGD→PUD→PMD→PTE 四層（x86_64 4-level），每層讀一次記憶體，最後才拿到物理位址。這代表**每一次記憶體存取，光是把 VA 翻成 PA 就要額外四次記憶體讀取**。沒有快取的話 CPU 大半時間都在走 page table。

TLB（Translation Lookaside Buffer，位址翻譯後備緩衝）是 CPU 內一塊小而快的快取，存的是「這個 VA 的頁 → 那個 PA 的頁」的直接對應。流程：

```
   存取虛擬位址 0x7fff_1234_5000
        │
        ▼
   ┌─────────────┐   TLB hit（常見）
   │ 查 TLB      │──────────────────► 直接拿到 PA，1 cycle 內完成
   │ (VA頁→PA頁) │
   └──────┬──────┘
          │ TLB miss（少見但貴）
          ▼
   走完整 page table walk（讀 4 次記憶體）
          │
          ▼
   把結果填回 TLB（下次同一頁就 hit）
```

TLB 命中率通常極高（>99%），因為程式有 locality——你反覆存取的就那幾頁。但 TLB 很小（幾十到上千個 entry），且有個致命特性：**它存的是「這個 VA 對應這個 PA」，但沒存「這是哪個 process 的 VA」**。

這就出問題了。每個 process 有自己的 page table（Ch 16、Ch 19 的 `mm_struct`）：process A 的 VA `0x400000` 和 process B 的 VA `0x400000` 指向完全不同的物理頁。context switch（Ch 14）換 process 時會換 page table 根（x86 寫 `CR3`、ARM64 寫 `TTBR0_EL1`）。如果 TLB 裡還留著 A 的 `0x400000→PA_a`，切到 B 之後 B 存取 `0x400000` 就會 TLB hit 拿到 `PA_a`——**讀到別的 process 的記憶體**。

歷史上的解法很粗暴：**每次 context switch 換 CR3 時，硬體自動清空整個 TLB**。x86 寫 `CR3` 這個動作本身就會 flush 非 global 的 TLB entry。安全，但貴——切回來的 process 要重新一個一個 TLB miss 把翻譯填回去，這叫 TLB 的「冷啟動」懲罰。頻繁 context switch 的 workload 會被這個嚴重拖累。

### PCID / ASID：給翻譯貼上 process 標籤

現代解法是給 TLB entry 加一個 tag，標記「這條翻譯屬於哪個位址空間」。x86 叫 **PCID**（Process-Context Identifier，12-bit），ARM64 叫 **ASID**（Address Space Identifier）。有了 tag：

- TLB 查詢時要 **VA 和 tag 都對** 才算 hit。A 的 entry tag 是 1、B 是 2，B 存取 `0x400000` 查 tag=2 找不到 A 那條，自然不會誤命中。
- context switch 換位址空間時**不必清空 TLB**——換 CR3 時把 PCID 一起換掉，舊 process 的 entry 還留著但因為 tag 不符不會被誤用。切回來時它們還在，直接 hit，省掉冷啟動。

kernel 這邊：x86 的 PCID 管理在 `arch/x86/mm/tlb.c`,核心是 `switch_mm_irqs_off()`——它在 context switch 時決定給新 mm 分配哪個 PCID、要不要 flush。每個 CPU 維護一張小表（`cpu_tlbstate`，見 `arch/x86/include/asm/tlbflush.h`）記住「這顆 CPU 的 TLB 目前裝著哪幾個 mm 的翻譯、各用哪個 PCID」。PCID 只有 4096 個，不夠所有 process 用，所以是每 CPU 一個 6 個 slot 的小 LRU，回收時才 flush 那格。

> **架構差異先埋一顆**：ARM64 的 ASID 是硬體用來讓 TLBI（TLB invalidate）指令能廣播的關鍵。這點在 shootdown 那節會變成 x86 和 ARM64 最大的分歧。

### TLB reach：為什麼 huge page 有用

有個實用概念叫 **TLB reach（TLB 覆蓋範圍）**：TLB 有多少個 entry × 每個 entry 覆蓋多大 = 這顆 TLB 一次能翻譯多大的位址空間不 miss。假設 dTLB 有 64 個 entry、每個 4 KB 頁，reach 就只有 256 KB——你的工作集一超過這個大小，開始出現無法避免的 TLB miss（capacity miss），不管 locality 多好。

這直接解釋了 **huge page**（Ch 16 提過的 2 MB / 1 GB 大頁）為什麼是 mm 效能調校的重手段：一個 2 MB huge page 只佔**一條** TLB entry 卻覆蓋 512 個 4 KB 頁的範圍。同樣 64 個 entry，用 2 MB 頁 reach 從 256 KB 暴增到 128 MB。資料庫、JVM、大陣列數值運算這類大工作集 workload 開 THP（Transparent Huge Pages）或明確 `MAP_HUGETLB`，主要就是為了把 TLB miss 壓下去。你在動手練習會親手量到這個差異。

## TLB shootdown：改了 page table 之後的跨核惡夢

現在是本節重點，也是多核 mm 效能最痛的地方。

TLB 是**每顆 CPU 各自一份**。假設 process 有兩條執行緒分別跑在 CPU 0 和 CPU 1 上，共享同一個 `mm`（同一份 page table）。CPU 0 上的執行緒呼叫 `munmap`，或者 reclaim（Ch 22）決定把某個 page 換出、把它的 PTE 清掉。CPU 0 改了記憶體裡的 page table entry，也 flush 了**自己的** TLB。

問題：**CPU 1 的 TLB 裡可能還存著那條 PTE 的舊翻譯**。page table 在記憶體裡改了沒錯，但 CPU 1 不會自動去重讀 page table——它 TLB hit 就直接用舊的 PA。於是 CPU 1 拿著已經被 unmap（甚至已被 buddy 收回、重新分配給別人）的物理位址繼續讀寫。資料損毀。

所以改 page table 的 CPU **必須通知所有可能快取了這條翻譯的 CPU：把它從你的 TLB 丟掉**。這個「叫別人 flush TLB」的動作就是 **TLB shootdown**。

### x86：用 IPI 一顆一顆叫

x86 沒有「廣播 flush 給所有核」的指令。`INVLPG` 只 flush 執行它的那顆 CPU 的一條 entry。所以 x86 的 shootdown 靠 **IPI（Inter-Processor Interrupt，處理器間中斷）**：

```
   CPU 0（改了 page table 的人）              CPU 1、CPU 2、CPU 3
   ─────────────────────────────            ────────────────────
   1. 改記憶體裡的 PTE
   2. flush 自己的 TLB（INVLPG）
   3. 算出「哪些 CPU 可能有這條翻譯」
        （mm_cpumask：這個 mm 跑過哪些 CPU）
   4. 送 IPI ──────────────────────────►    收到中斷，跳進
        │                                    flush_tlb_func()
        │                                    執行 INVLPG flush 掉
        │                                    回 ACK ◄──────────┐
   5. 忙等所有 CPU ACK ◄─────────────────────────────────────┘
   6. 全部 ACK 了才繼續（此時全系統沒人有舊翻譯）
```

程式碼在 `arch/x86/mm/tlb.c`：發起端是 `flush_tlb_mm_range()`，它決定 flush 範圍（單頁用 `INVLPG` 逐頁、範圍太大就整個 mm flush），透過 `smp_call_function_many()` 送 IPI；被叫的 CPU 在中斷裡跑 `flush_tlb_func()`。發起的 CPU 要**同步等待所有目標 ACK** 才能往下走——因為它必須確定「全世界沒人再用舊翻譯」才能安全地把那個物理頁還給 buddy。

這個「送中斷 + 忙等所有核回應」在核多的時候非常貴：一次 `munmap` 可能要打斷幾十顆 CPU 正在做的事、等它們全部處理完。這是多核 server 上大記憶體 workload 的著名瓶頸，也是為什麼 kernel 拼命想**縮小 shootdown 的範圍**（只叫 `mm_cpumask` 裡真的跑過這個 mm 的 CPU，而不是全部）和**批次化**（`tlb_gather_mmu`/`mmu_gather`，把一批 page table 改動累積起來一次 shootdown，見 `mm/mmu_gather.c`）。

### ARM64：硬體幫你廣播，不用 IPI

這裡是 x86 和 ARM64 最重要的架構分歧之一。ARM64 有專門的 TLB invalidate 指令 **`TLBI`**，而且它可以帶 **`IS`（Inner Shareable）** 修飾——`TLBI VAE1IS`（invalidate by VA，含 ASID，broadcast 到 inner shareable domain）這條指令**由硬體廣播到同一個 shareable domain 裡的所有 CPU**，讓它們都 flush 對應的 TLB entry。發起的 CPU 不需要送 IPI、不需要中斷別的核、不需要忙等 ACK——硬體的一致性互連（interconnect）負責把 invalidate 傳過去，配一條 `DSB`（Data Synchronization Barrier）確保完成。

```
   x86 shootdown：              ARM64 shootdown：
   ─────────────────           ─────────────────
   軟體送 IPI                   一條 TLBI ...IS 指令
   每個核跳進中斷 handler       硬體互連自動廣播
   軟體忙等 ACK                 DSB 等硬體完成
   → 貴、隨核數線性變差         → 便宜、不打斷其他核的執行
```

ARM64 的實作在 `arch/arm64/include/asm/tlbflush.h`（`flush_tlb_mm`、`__flush_tlb_range` 等 inline，直接發 `TLBI` 指令）與 `arch/arm64/mm/`。這也是為什麼 ARM64 的 ASID 這麼重要：`TLBI` 廣播時要靠 ASID 精準命中「這個位址空間」的翻譯，才能只 flush 相關 entry。

> **面試會問**：「x86 和 ARM64 的 TLB shootdown 差在哪？」答案的核心是——**x86 靠軟體 IPI 逐核通知並等 ACK，ARM64 有 `TLBI ...IS` 由硬體廣播、不需要 IPI**。這讓 ARM64 在核很多時的 mm 密集 workload 上，理論上 shootdown 成本低很多。這是硬體 ISA 設計把一個軟體痛點吸收掉的經典例子。

## memory ordering：CPU 為什麼不照你寫的順序

換到本章第二大主題。先看一個會嚇到人的例子——經典的 store buffer / message passing 問題：

```c
   int data = 0;
   int ready = 0;

   // CPU 0（生產者）              // CPU 1（消費者）
   data = 42;                      while (ready == 0)
   ready = 1;                          ;            // 等 ready
                                   printf("%d\n", data);   // 印出什麼？
```

你的直覺：CPU 1 看到 `ready==1` 之後跳出迴圈，`data` 一定是 42。**在 ARM64 上，它可能印出 0。**

原因是 CPU（和編譯器）會重排記憶體存取。單執行緒下這樣重排沒問題（你自己看到的結果保證一致），但另一顆 CPU 看你的寫入時，**看到的順序可能跟你寫的順序不同**：

```
   CPU 0 的程式順序          CPU 1 可能看到的順序（ARM64）
   ─────────────────         ──────────────────────────────
   store data = 42           store ready = 1     ◄── 先看到這個！
   store ready = 1           store data = 42     ◄── 後看到

   → CPU 1 在 ready=1、data 還是 0 的窗口裡讀了 data
```

為什麼會重排？兩個來源，都要防：

1. **編譯器重排**：編譯器為了優化會把不相干的 load/store 挪位、合併、或乾脆從暫存器讀不重新 load。上面的 `while (ready == 0);` 編譯器甚至可能把 `ready` 讀進暫存器一次就不再讀，變成無窮迴圈。

2. **CPU 重排**：CPU 有 store buffer（寫入先進 buffer 不立刻進快取）、亂序執行、投機執行。`data=42` 的寫入可能還卡在 store buffer 裡，`ready=1` 卻先被別的核看到。

值得多看一眼 CPU 為什麼重排——這不是 CPU 設計者亂搞，而是效能的必然。寫入記憶體很慢（要走 MESI 拿到 line 的寫權限），所以每顆核有一個 **store buffer**：store 先塞進 buffer 就讓 CPU 繼續往下跑，buffer 在背景慢慢把值寫進快取。對稱地，讀入端有 **invalidate queue**：別的核送來的 invalidate 訊息先排進 queue、晚點才真正讓自己的 line 失效。這兩個 buffer 就是重排的物理來源——store buffer 讓「你的 store 對別人晚可見」，invalidate queue 讓「別人的 store 對你晚可見」。memory barrier 的本質，就是**強迫把 store buffer 排空、把 invalidate queue 處理完**，讓那一刻之前的存取真的落地、對別人可見。x86 的 store buffer 存在但被設計成不允許 store-store 重排（TSO），ARM64 的更寬鬆——這就是兩個記憶體模型的硬體根源。

### x86 是 TSO，ARM64 是 weakly ordered — 本課最重要的架構差異之一

不同 CPU 架構「允許多少重排」差很多，這叫 **memory model（記憶體模型）**：

- **x86 是 TSO（Total Store Order，全存儲定序）**：相對強。硬體保證 load-load、load-store、store-store **都不重排**，**唯一**允許的是 **store-load 重排**（一個 store 後面的 load 可以跑到 store 前面，因為 store 卡在 store buffer）。所以上面那個 message passing 例子在 x86 上**不會出錯**——`data=42; ready=1` 兩個 store 不會被 x86 重排，CPU 1 看到 `ready=1` 時 `data` 一定是 42。

- **ARM64 是 weakly ordered（弱定序）**：幾乎所有重排都被允許——load-load、load-store、store-store、store-load 全都可能亂。所以那個例子在 ARM64 上**真的會壞**，必須加 barrier。

```
   x86 (TSO)：                        ARM64 (weak)：
   ─────────                          ─────────────
   store→store  不重排 ✓              store→store  可能重排 ✗
   load →load   不重排 ✓              load →load   可能重排 ✗
   load →store  不重排 ✓              load →store  可能重排 ✗
   store→load   可能重排 ✗（唯一）     store→load   可能重排 ✗
```

這個差異的後果很實際：**一段在 x86 上跑得好好的並行 kernel 程式，搬到 ARM64 可能就 race**——因為 x86 的強記憶體模型「意外地」幫你擋掉了大部分重排，你少加的 barrier 在 x86 上沒事，到 ARM64 就爆。這是移植 kernel 程式碼到 ARM 時最陰險的一類 bug。正確的寫法是**不管在哪個架構都加對 barrier**，讓 kernel 的 barrier API 在各架構上編成該架構需要的指令（x86 需要的地方少，很多 barrier 編成 no-op；ARM64 編成真的 `dmb`/`dsb`）。

## memory barrier：kernel 的 barrier API

barrier 就是「強迫排序」的工具。kernel 把它分成兩層，因為重排有兩個來源（編譯器 + CPU）：

### 第一層：擋編譯器重排 — READ_ONCE / WRITE_ONCE

`include/linux/compiler.h` 的 `READ_ONCE(x)` / `WRITE_ONCE(x, val)`（底層是 `__READ_ONCE`，用 `volatile` 存取加編譯器 barrier）做兩件事：

- 保證這個存取**真的發生一次記憶體讀/寫**，不被編譯器優化掉或快取進暫存器（修掉上面 `while(ready==0)` 變無窮迴圈的問題）。
- 阻止編譯器把這個存取跟前後的存取重排。

它**只擋編譯器，不擋 CPU**。在 x86 上因為 TSO 夠強，很多情況 `READ_ONCE`/`WRITE_ONCE` 就足夠了；在 ARM64 上還需要下一層。`barrier()`（純編譯器 barrier，`asm volatile("" ::: "memory")`）是更粗的版本，擋所有跨越它的編譯器重排但不管 CPU。

### 第二層：擋 CPU 重排 — smp_mb 家族

`include/asm-generic/barrier.h` 定義了 SMP barrier 家族（各架構在自己的 `arch/*/include/asm/barrier.h` 覆寫成真指令）：

| API | 保證 | x86 (TSO) | ARM64 (weak) |
|---|---|---|---|
| `smp_mb()` | 全序（前面所有 load/store 對後面所有 load/store 都排好） | `lock; addl`（或 `mfence`） | `dmb ish` |
| `smp_rmb()` | read barrier：前面的 load 對後面的 load 排好 | **no-op**（`barrier()`） | `dmb ishld` |
| `smp_wmb()` | write barrier：前面的 store 對後面的 store 排好 | **no-op**（`barrier()`） | `dmb ishst` |
| `smp_load_acquire(p)` | 這個 load 之後的存取不會被排到它前面 | 只需編譯器 barrier | `ldar` |
| `smp_store_release(p, v)` | 這個 store 之前的存取不會被排到它後面 | 只需編譯器 barrier | `stlr` |

**為什麼 x86 上 `smp_rmb`/`smp_wmb` 是 no-op**：TSO 本來就保證 store-store 和 load-load 不重排，所以在 x86 上要求「前面的 store 對後面的 store 排好」是硬體已經免費提供的——kernel 只要放一個編譯器 barrier 擋編譯器就夠，不需要 CPU 指令。到 ARM64，硬體不保證，就得編成真的 `dmb`。這完美解釋了本章開頭埋的那句話。而 `smp_mb()` 即使在 x86 也要真指令，因為它要擋的是**唯一被 TSO 允許的 store-load 重排**。

### acquire / release：最實用的一對

`smp_load_acquire` / `smp_store_release` 是實務上最常用的一對，語意來自「臨界區」的概念：

- **release**（`smp_store_release`，配「解鎖」或「發佈資料」）：保證**它之前的所有存取**都在這個 store 之前完成、對別人可見。
- **acquire**（`smp_load_acquire`，配「上鎖」或「接收資料」）：保證**它之後的所有存取**都在這個 load 之後才發生。

用它們改寫開頭的 message passing，就正確了（跨架構都對）：

```c
   // CPU 0（生產者）                    // CPU 1（消費者）
   data = 42;                            while (smp_load_acquire(&ready) == 0)
   smp_store_release(&ready, 1);             ;
   //  ^ 保證 data=42 先於 ready=1        printf("%d\n", data);  // 保證看到 42
   //    對 CPU 1 可見                    //  ^ acquire 保證這行讀 data 排在
                                          //    看到 ready==1 之後
```

release/acquire 配對比 full `smp_mb()` 便宜（單向 barrier，ARM64 用 `stlr`/`ldar` 單指令搞定，不用 full `dmb`），語意又剛好對應大多數「發佈—消費」場景，所以是 kernel 裡最主流的寫法。Ch 24 的 atomic、Ch 25–28 的各種鎖，內部都建在這對語意上——鎖的 acquire 就是 acquire barrier，unlock 就是 release barrier。

一個看得到的真實用例：kernel 發佈一個新配置好的物件、讓別人透過指標看到它時，用的就是這對語意。發佈端 `smp_store_release(&global_ptr, obj)`（保證 `obj` 的所有欄位都填好才讓指標可見），讀取端 `p = smp_load_acquire(&global_ptr); use(p->field)`（保證看到非 NULL 指標後讀的欄位都是新值）。少了這對，ARM64 上讀取端可能拿到非 NULL 指標卻讀到還沒初始化完的欄位。RCU（Ch 27）的 `rcu_assign_pointer`/`rcu_dereference` 底層就是這個模式的特化版，這也是為什麼那對 API 能無鎖發佈資料。

> **順帶一提 control dependency**：ARM64 這種弱模型下有個微妙點——「讀到一個值後，用它做條件分支」這件事本身**不保證**後面的 load 排在前面的 load 之後（CPU 可以投機執行分支兩邊）。所以 `if (READ_ONCE(x)) { y = READ_ONCE(z); }` 這種靠控制流的依賴，不能當 barrier 用，`Documentation/memory-barriers.txt` 有整節在講 control dependency 的陷阱。要排序就用明確的 barrier，別依賴 if。

## cache coherence：MESI 與 false sharing

最後一塊。前面說 coherence 由硬體保證，這節說清楚它保證什麼、以及它**免費幫你**之外會**背刺你**的地方。

現代多核每顆有自己的 L1/L2 快取。同一個記憶體位址可能同時被好幾顆核快取。**cache coherence** 就是硬體協議，保證這些副本不會不一致——你不會讀到某顆核快取裡過期的值。最常見的協議是 **MESI**，每條 cache line 有四種狀態：

- **M（Modified）**：這顆核改過，是唯一的最新副本，記憶體是舊的。
- **E（Exclusive）**：只有這顆核有，且和記憶體一致。
- **S（Shared）**：多顆核都有唯讀副本，和記憶體一致。
- **I（Invalid）**：這條 line 失效了，不能用。

當一顆核要**寫**一條 line，它得先讓其他核那條 line 進入 I（invalidate 它們的副本），自己升到 M。這樣任何時刻最多一顆核有寫權限，讀的核看到的一定是最新——這就是 coherence 的保證：**針對單一 cache line 的最終一致性**。你不用寫任何指令維護它，硬體自動跑 MESI。

跟著一次「兩核搶同一條 line 的寫權限」走一遍，你就懂 false sharing 為什麼那麼貴：

```
   初始：兩核都沒這條 line
   CPU0 讀 → 沒別人有 → line 進 CPU0，狀態 E（獨佔）
   CPU1 也讀 → CPU0 降成 S，CPU1 也是 S（兩核共享唯讀）
   CPU0 要寫 → 送 invalidate → CPU1 那條變 I → CPU0 升 M（唯一寫者）
   CPU1 要寫 → 它是 I，得先 read-for-ownership 把 line 從 CPU0 搶回
             → CPU0 的 M 被搶走變 I（值先寫回或轉交）→ CPU1 升 M
   CPU0 又要寫 → 重複上一步，line 再彈回來……
```

關鍵觀察：**每一次「換一顆核寫」都要一輪跨核的 invalidate + 搬 line**，這是 coherence 協議的固定成本。兩核輪流寫同一條 line 時，這條 line 就在 M 狀態下於兩核之間反覆搬家（cache line bouncing / ping-pong），每次寫都退化成一次跨核往返。這就是下面 false sharing 的機制底層。

再強調一次本章的主軸：**MESI 保證的是「單一 line 的值一致」，不是「多個 line 之間的可見順序」**。順序是 memory ordering 的事，要 barrier。這兩件事獨立——這就是 coherence ≠ ordering 的硬體層原因。

### false sharing：coherence 的效能背刺

MESI 免費幫你維護一致，但它的粒度是**整條 cache line（通常 64 bytes）**，不是單一變數。這帶來一個陰險的效能問題——**false sharing（偽共享）**：

```
   一條 64-byte cache line
   ┌───────────────────────────────────────────────┐
   │  counter_a (CPU0 狂寫)  │  counter_b (CPU1 狂寫) │
   └───────────────────────────────────────────────┘
        ▲                          ▲
     兩個變數邏輯上毫不相干，但落在同一條 line

   後果：CPU0 寫 counter_a → 讓 CPU1 那條 line 變 I
        CPU1 要寫 counter_b → 先把 line 從 CPU0 搶回來（變 I）
        → line 在兩核之間反覆彈來彈去（ping-pong）
        → 每次寫都變成跨核快取同步，慢幾十倍
```

兩個執行緒改的是**不同變數**，邏輯上零競爭，但因為兩個變數擠在同一條 cache line，MESI 協議會讓這條 line 在兩顆核之間不停地 invalidate/搶回——每一次寫都退化成一次跨核快取一致性流量。程式邏輯完全正確，效能卻爛得莫名其妙。

kernel 對這個問題非常敏感，處處在防：

- per-CPU 變數（Ch 7）本來就是為了讓每顆核寫自己的副本、不碰別人的 line。
- 熱點結構體用 `____cacheline_aligned`（`include/linux/cache.h`）強制對齊到 cache line 邊界，讓不同核常寫的欄位落在不同 line。你在 `struct rq`（runqueue，Ch 11）、per-CPU 計數器等地方到處看得到。
- 反過來，唯讀的熱資料會用 `__read_mostly` 集中放，避免和常寫的欄位共用 line。

這正是 `perf_bench` 課裡量效能時最愛抓的一類 bug：CPU 沒滿載、演算法沒問題，但吞吐上不去——十之八九是 false sharing 或 cache line 彈跳。

## 動手：量 TLB miss 與重現 false sharing

### 用 perf 看 dTLB miss

在你的 host（或 QEMU 裡如果 perf 可用）上，找一個會大量隨機存取記憶體的程式（例如遍歷一個遠大於 TLB 覆蓋範圍的陣列），量它的 data TLB load miss：

```bash
perf stat -e dTLB-loads,dTLB-load-misses,iTLB-load-misses ./your_program
```

輸出裡 `dTLB-load-misses` 佔 `dTLB-loads` 的比例就是 dTLB miss 率。順序存取一大塊記憶體 miss 率會很低（locality 好），跳著存取（stride 大於一頁）會飆高。想看 TLB 的威力，比較「順序遍歷 1 GB 陣列」和「隨機跳頁遍歷同樣大小」的 miss 數——後者高好幾個數量級。這也是為什麼 huge page（2 MB/1 GB 頁，一條 TLB entry 覆蓋更大範圍）能大幅降低 TLB miss，是 mm 效能調校的常見手段。

### 重現並修掉 false sharing

寫一段最小程式，直接量出 false sharing 的代價：

```c
// false_sharing.c  —  編譯：gcc -O2 -pthread false_sharing.c -o fs
#include <pthread.h>
#include <stdio.h>
#include <stdint.h>

#define ITERS 200000000UL

// 版本 A：兩個 counter 擠同一條 cache line（false sharing）
struct { uint64_t a; uint64_t b; } shared;

// 版本 B：把 b 推到下一條 line（64-byte 對齊）
struct { uint64_t a; uint64_t pad[7]; uint64_t b; } padded;

void *bump_a(void *p) { for (uint64_t i=0;i<ITERS;i++) ((volatile uint64_t*)p)[0]++; return 0; }
void *bump_b(void *p) { for (uint64_t i=0;i<ITERS;i++) *(volatile uint64_t*)p += 1; return 0; }

int main(void) {
    pthread_t t1, t2;
    // 先跑 false sharing 版
    pthread_create(&t1,0,bump_a,&shared.a);
    pthread_create(&t2,0,bump_b,&shared.b);
    pthread_join(t1,0); pthread_join(t2,0);
    return 0;
}
```

把上面跑一次（`shared`，兩個 counter 同 line），再改成用 `padded`（`b` 被 `pad[7]` 推到下一條 64-byte line），各用 `perf stat -e cache-misses,cache-references ./fs` 量時間與 cache miss。**你會看到對齊之後快好幾倍**，且 `cache-misses` 大幅下降——這就是那條 line 不再在兩核之間 ping-pong 的證據。這個對比是理解「為什麼 kernel 到處 `____cacheline_aligned`」最直接的方式。

### 讀 kernel 官方的 memory ordering 聖經

真正想搞懂 barrier，繞不開 `Documentation/memory-barriers.txt`。這份文件是 Paul McKenney 等人維護的 kernel 記憶體模型權威說明。第一次讀不用全懂，先讀「ABSTRACT MEMORY ACCESS MODEL」「WHAT ARE MEMORY BARRIERS?」和「SMP BARRIER PAIRING」三節——barrier 幾乎總是**成對**出現（一邊 release 配另一邊 acquire、一邊 wmb 配另一邊 rmb），單獨放一個 barrier 通常是錯的，這節講透為什麼。

## 對比與取捨

| 面向 | x86_64 | ARM64 |
|---|---|---|
| 記憶體模型 | TSO（強）：只允許 store-load 重排 | weakly ordered（弱）：幾乎全部可重排 |
| `smp_rmb`/`smp_wmb` | no-op（只需編譯器 barrier） | 真指令 `dmb ishld` / `dmb ishst` |
| `smp_mb` | `lock; addl` 或 `mfence` | `dmb ish` |
| acquire/release | 只需編譯器 barrier | 單指令 `ldar` / `stlr` |
| TLB shootdown | 軟體 IPI 逐核通知 + 忙等 ACK | 硬體 `TLBI ...IS` 廣播 + `DSB` |
| context switch TLB tag | PCID（12-bit，每 CPU 6-slot LRU） | ASID |
| 移植風險 | 少加 barrier 常「意外正確」 | 同樣程式碼常暴露 race |

一句話總結取捨：**x86 用強記憶體模型和軟體 shootdown，換來「少想一點也常對」但「shootdown 貴」；ARM64 用弱記憶體模型和硬體廣播 shootdown，換來「shootdown 便宜、核多更省」但「barrier 一個都不能少」。** 這是兩種 ISA 哲學的縮影，也是 kernel 為什麼要把 barrier 全部包成架構無關 API 的根本原因——讓一份程式碼在兩種哲學上都正確。

## 踩雷集錦

1. **「有 cache coherence 就不用 barrier」**——錯得最離譜的一條。coherence（MESI）保證的是**單一 cache line 的最終一致**，管不到**不同變數之間你看到的順序**。message passing 那個例子每個變數都由 MESI 保證一致，但順序照樣錯，非加 barrier 不可。coherence ≠ ordering，背下來。

2. **「x86 記憶體模型強，所以不用管 barrier」**——x86 只是**恰好**幫你擋掉多數重排，不是不需要。你少加的 barrier 在 x86 上沒事，搬到 ARM64 就 race。正確心態是「barrier 照加，讓它在 x86 上編成 no-op」，而不是「x86 上省略」。這是 kernel 程式碼移植到 ARM 最常見的一類 bug 來源。

3. **改了 page table 卻只 flush 自己的 TLB**——單核測沒事，上多核就資料損毀。任何 unmap / 改權限 / reclaim 動到別的 CPU 也可能快取的 PTE，都必須做 TLB shootdown 通知所有相關 CPU。kernel 幫你在 `mmu_gather`/`flush_tlb_*` 裡處理了，但你若自己動 page table（例如寫自訂 mm 模組）就得自己負責。

4. **忘了 `READ_ONCE`/`WRITE_ONCE`，被編譯器優化掉**——`while (flag) ;` 這種等待迴圈，編譯器可能把 `flag` 讀進暫存器一次就不再 load，變成無窮迴圈。共享變數的存取一定走 `READ_ONCE`/`WRITE_ONCE`，這是 kernel 的硬規矩，不只效能問題，是正確性問題。

5. **以為 false sharing 是「邏輯 race」**——它不是。兩個執行緒改**不同變數**，程式邏輯 100% 正確，加鎖也沒用（本來就沒真正競爭），純粹是兩變數擠同一條 cache line 造成的 MESI ping-pong。解法是**對齊**（`____cacheline_aligned`/padding）把它們分到不同 line，不是加同步。診斷靠 `perf`，不是讀程式碼邏輯。

## 進階：再往深一層

- **`INVLPG` vs full flush 的取捨**：shootdown 逐頁 `INVLPG` 精準但一頁一條指令，範圍大時不如整個 flush（重填 TLB 反而快）。`arch/x86/mm/tlb.c` 有個 `tlb_single_page_flush_ceiling` 閾值決定何時切成 full flush。這是「精準但多次」vs「粗暴但一次」的典型工程權衡。

- **lazy TLB**：kernel thread（沒有自己的 user 位址空間）context switch 進來時，可以不換 page table、借用前一個 process 的 mm（`active_mm`），避免無謂的 TLB flush。這叫 lazy TLB mode，`switch_mm` 和 `enter_lazy_tlb` 相關。核多時這也影響 shootdown 要不要打斷這顆核。

- **memory ordering 的形式化模型**：kernel 有一份可執行的記憶體模型 `tools/memory-model/`（LKMM，Linux Kernel Memory Model），用 `herd7` 工具可以**形式化驗證**一段 barrier 用法對不對。這是 Paul McKenney 團隊的成果，比讀文件更硬核。想確認自己寫的 lock-free 程式在弱記憶體模型下正不正確，這是唯一嚴謹的辦法。

- **DMA 與 barrier**：跟裝置打交道（Ch 41 DMA）時，CPU 和裝置對記憶體的可見順序又是另一組 barrier（`dma_rmb`/`dma_wmb`、`mb()` 而非 `smp_mb()`）——`smp_*` 只管 CPU 之間，跨到裝置要用不帶 `smp_` 的版本。這在寫驅動時是常見坑。

- **面試高頻**：「解釋 acquire/release 語意並舉一個 kernel 用例」「為什麼 x86 上 spinlock 的 unlock 幾乎不用 barrier 但 ARM64 要」「false sharing 怎麼診斷怎麼修」——這三題答得清楚，代表你真的懂這章。

## 動手練習

1. **量 TLB 的威力**：寫兩個版本遍歷 1 GB 陣列——一個順序、一個以大於一頁（>4 KB）的 stride 隨機跳。用 `perf stat -e dTLB-load-misses` 比較兩者的 miss 數，解釋差異。加碼：用 huge page（`madvise(MADV_HUGEPAGE)` 或 `mmap` 帶 `MAP_HUGETLB`）再量一次，看 miss 掉多少。

2. **重現 false sharing 並修掉**：把本章的 `false_sharing.c` 跑起來，量 `shared`（同 line）和 `padded`（分 line）兩版的執行時間與 `cache-misses`。寫下加速幾倍、cache-miss 降多少。這是你之後在任何效能問題裡辨認 false sharing 的肌肉記憶。

3. **在 gdb 裡看 TLB flush 路徑**：QEMU + gdb（Ch 0 環境）裡 `break flush_tlb_mm_range`，在 QEMU shell 裡跑一個會 `munmap` 的程式（或狂 `malloc`/`free` 大塊），看它停下，`backtrace` 看是誰觸發了 shootdown、`mm_cpumask` 裡有哪些 CPU。這把「改 page table → shootdown」的抽象變成你看得到的呼叫鏈。

4. **弄壞它（思想實驗 + 驗證）**：把開頭 message passing 例子的 `smp_store_release`/`smp_load_acquire` 換成普通賦值，在**ARM64**（`qemu-system-aarch64` 或真的 ARM 機器）上跑多執行緒版本，嘗試觀察到 `data` 讀成 0 的窗口。x86 上你很難重現（TSO 擋掉了）——這個「x86 重現不了、ARM64 一下就爆」的對比，本身就是本章最重要那條架構差異的親身證據。

5. **讀 `Documentation/memory-barriers.txt` 的 SMP BARRIER PAIRING**：讀完能不能解釋「為什麼 barrier 幾乎總是成對出現」「單獨放一個 `smp_wmb` 為什麼通常是 bug」。

## 本章重點整理

- **TLB** 快取 VA→PA 翻譯避免昂貴的 page table walk；context switch 換位址空間本會讓 TLB 失效，**PCID（x86）/ASID（ARM64）** 給翻譯貼 process 標籤避免全 flush。
- **TLB shootdown**：改了 page table 後別的 CPU 的 TLB 還有舊翻譯，必須通知它們 flush。**x86 用軟體 IPI 逐核通知並忙等 ACK（貴）；ARM64 用 `TLBI ...IS` 由硬體廣播（便宜、不打斷別核）**——本章最重要的架構差異之一。
- **memory ordering**：CPU/編譯器會重排存取，多核下另一顆核看到的順序可能不同。**x86 是 TSO（強，只有 store-load 重排）；ARM64 是 weakly ordered（幾乎全可重排）**——所以 x86 上「意外正確」的程式到 ARM64 常 race。
- **barrier API** 兩層：`READ_ONCE`/`WRITE_ONCE` 擋編譯器；`smp_mb`/`smp_rmb`/`smp_wmb`/`smp_load_acquire`/`smp_store_release` 擋 CPU。很多 barrier 在 x86 是 no-op、在 ARM64 是真指令。**coherence（MESI）≠ ordering**：前者保證單一 line 最終一致、後者保證跨變數可見順序。**false sharing** 是 coherence 的效能背刺，靠對齊解決。

## 自我檢核

- [ ] 不看筆記，能解釋 PCID/ASID 存在的理由，以及沒有它 context switch 會發生什麼
- [ ] 能講清楚 TLB shootdown 是什麼、為什麼非做不可，以及 x86（IPI）和 ARM64（`TLBI ...IS`）的做法差在哪
- [ ] 面試被問「x86 和 ARM64 的記憶體模型差在哪、對寫並行程式有什麼影響」，你能答出 TSO vs weak、以及「x86 意外正確、ARM64 暴露 race」
- [ ] 能解釋為什麼 `smp_wmb` 在 x86 是 no-op 但 ARM64 是真指令
- [ ] 能用一句話說清 coherence 和 ordering 的分工，並解釋 false sharing 為什麼是 coherence 問題不是邏輯 race
- [ ] 能寫出（或看懂）用 `smp_store_release`/`smp_load_acquire` 正確傳遞資料的 message passing

## 延伸閱讀

### 官方文件

- **[Documentation/memory-barriers.txt](https://www.kernel.org/doc/html/latest/staging/index.html)（源碼樹根下同名檔）**
  - **讀哪裡**：先讀「WHAT ARE MEMORY BARRIERS?」與「SMP BARRIER PAIRING」；進階再讀 acquire/release 與 control dependency 章節
  - **為什麼**：這是 kernel 記憶體模型的權威文件，本章 barrier 部分的一切都能在這裡找到更嚴謹的說法。第一次讀不用全懂，配本章對照

- **[tools/memory-model/（LKMM，Linux Kernel Memory Model）](https://elixir.bootlin.com/linux/v6.12/source/tools/memory-model)**
  - **讀哪裡**：`README` 和 `Documentation/explanation.txt`
  - **能學到什麼**：kernel 記憶體模型的可執行形式化版本，用 `herd7` 能驗證 barrier 用法對錯。想嚴謹驗證 lock-free 程式碼，這是唯一正路
  - **前提**：本章讀完，且對 ordering 有痛過的經驗再來

### 論文 / 經典文章

- **[Paul McKenney, "Memory Barriers: a Hardware View for Software Hackers"](http://www.rdrop.com/users/paulmck/scalability/paper/whymb.2010.07.23a.pdf)**
  - **讀哪裡**：整篇，尤其 store buffer 與 invalidate queue 那幾節
  - **為什麼**：從硬體角度解釋「為什麼需要 barrier」，把 store buffer、MESI、重排的因果講得比任何文件都清楚。本章「CPU 為什麼重排」的完整硬體版

- **[Ulrich Drepper, "What Every Programmer Should Know About Memory"](https://people.freebsd.org/~lstewart/articles/cpumemory.pdf)**
  - **讀哪裡**：第 3 章（CPU caches）、第 6 章（優化）談 false sharing 與 cache line 的部分
  - **為什麼**：cache、coherence、false sharing 的權威長文；`perf_bench` 課也引它。理解本章 MESI/false sharing 一節的最佳補充

### 書籍

- **《Is Parallel Programming Hard, And, If So, What Can You Do About It?》** — Paul McKenney（線上免費，perfbook）
  - **這本書的定位**：並行程式設計的聖經，memory ordering、barrier、RCU（Ch 27 主角）都出自作者之手
  - **讀哪裡**：Chapter 15（Advanced Synchronization: Memory Ordering）對應本章，配 memory-barriers.txt 讀

有了 TLB、barrier、coherence 這三塊硬體地基，我們終於能正面攻 kernel 的同步子系統了。下一章從最基本的 atomic 操作與 memory ordering 開始——你會看到 `atomic_t`、`cmpxchg`、以及本章的 acquire/release 語意如何變成一個個具體的原子指令，成為所有鎖的基石。

→ [Ch 24 atomic 操作與 memory ordering](./24-atomics-memory-ordering.md)
