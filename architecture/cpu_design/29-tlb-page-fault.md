# Ch 29 — TLB 設計 + page fault 與 pipeline 互動

> **目標**：Ch 28 的 walk 每次翻譯要 2 次記憶體存取，若每個 load/store/取指都這樣，虛擬記憶體會慢到不能用。TLB（Translation Lookaside Buffer）就是快取 walk 結果的那個東西。這章你會學 TLB 為什麼存在、它的結構（fully-associative + 替換）、SFENCE.VMA 為什麼必要、以及 page fault 怎麼觸發 trap 並 flush pipeline。用 C 模型真跑量到：強 locality 下 TLB hit rate 99.996%（只 4 次冷 miss）、working set 超過 TLB 容量時 thrashing 到 0%。
> **環境**：WSL + gcc 11.4。TLB hit/miss 數字皆真跑。本章接 Ch 28 的 Sv32 walk、Ch 27 的 stall、Ch 18 的 flush，是記憶體階層與 pipeline 的交會點。

## 為什麼需要 TLB

Ch 28 算過一筆帳：Sv32 一次位址翻譯要 walk 兩層，**2 次記憶體存取**（讀 pte1、pte0）。現在想想 pipeline 每一拍在做什麼：

- IF 級每拍取指 → 每拍一次位址翻譯（指令的 VA → PA）→ 2 次記憶體存取。
- MEM 級每個 load/store → 一次翻譯 → 2 次記憶體存取。

如果每次翻譯都真的去 walk，那**光是翻譯就把記憶體頻寬吃光**：原本一次取指 1 次記憶體存取，現在變 1（walk pte1）+ 1（walk pte0）+ 1（真的取指）= 3 次。load/store 同理。虛擬記憶體直接讓每次存取變 3 倍慢——這不可接受。

救星和 cache 是同一個思路：**locality**。程式的存取集中在少數幾個頁（迴圈在同一個 code page 跑、堆疊在同一個 stack page、資料在幾個 data page）。同一個頁會被反覆翻譯成同一個 PPN。**那就把「VPN → PPN」的翻譯結果快取起來**，下次翻同一個 VPN 直接查快取，不必再 walk。這個快取就是 TLB。

TLB 之於 page table，就像 cache 之於 DRAM：**用一個小而快的快取，接住大多數的翻譯，把慢的 walk 攤到接近零。**

## 先建立直覺：常查地址的便利貼

Ch 28 把 MMU 比作查對照表的郵局。但每封信都翻整本對照表（走進檔案室、翻兩層目錄）太慢。郵差的做法是：**把最近常查的幾條「信箱號 → 住址」寫在手邊的便利貼上**。

```
   便利貼（TLB）：
   ┌─────────────┬──────────────┐
   │ 信箱 100    │ → 中山路 5 號  │   ← 剛查過，記下來
   │ 信箱 250    │ → 民生路 12 號 │
   │ 信箱  77    │ → 忠孝路 8 號  │
   └─────────────┴──────────────┘
```

來一封信，郵差先瞄便利貼：在上面（TLB hit）→ 直接送，不進檔案室；不在（TLB miss）→ 才走進檔案室翻對照表（walk），翻完把結果**補一條到便利貼**（之後再來就命中）。便利貼小（幾格），寫滿了要擦掉一條騰位置（替換）。

TLB 就是這張便利貼：小（通常 8~64 條）、快（一拍查完）、存 VPN → PPN 的翻譯結果。程式有 locality，所以便利貼上那幾條就覆蓋了絕大多數存取。

## 核心概念：TLB 的結構

TLB 通常是 **fully-associative（全相聯）**——任何 VPN 可放任何一格，查的時候平行比對所有格的 VPN。為什麼全相聯？因為 TLB 很小（幾十條），全相聯的比較器成本可接受，而全相聯沒有 conflict miss（Ch 26），命中率最高。大一點的 L2 TLB 才用 set-associative。

每條 TLB entry 存：

```
   TLB entry：
   [ valid | VPN (20 bit) | PPN (22 bit) | 權限 flags (R/W/X/U) | ASID | G ]
     有效嗎   虛擬頁號        實體頁號         這頁能不能讀寫執行     行程 全域
```

- **VPN → PPN**：翻譯的核心。查時拿 VA 的 VPN 比對所有 entry 的 VPN，命中就拿它的 PPN + offset 組出 PA，一拍完成。
- **權限 flags**：從 PTE 抄來的 R/W/X/U，命中時順便做權限檢查（這次存取合不合法），不必再 walk。
- **ASID（Address Space ID）**：來自 satp。標記這條翻譯屬於哪個行程，讓 context switch 時不必清空整個 TLB——只有 ASID 對得上的 entry 才算命中。
- **G（Global）**：全域映射（kernel 常用），不隨 ASID 切換失效。

查 TLB 的邏輯（概念）：

```
   TLB lookup(VA, current_ASID)：
     VPN = VA[31:12]
     for each entry：
       if entry.valid && entry.VPN == VPN && (entry.G || entry.ASID == current_ASID)：
         hit! PA = entry.PPN * 4096 + VA[11:0]  （順便查權限）
     miss → 觸發 walk（Ch 28），把結果填回 TLB
```

替換策略（TLB 滿了填新的踢誰）通常用 **FIFO 或 pseudo-LRU**（Ch 26 講過）——TLB 小，簡單策略就夠。本章的 C 模型用 FIFO。

## 範例一：強 locality 下 TLB 幾乎全命中

用 C 模型的 TLB（8 entry、fully-associative、FIFO 替換），跑一個典型的「迴圈在少數頁上跑」場景：只碰 4 個頁（一個 code page、兩個 data page、一個 stack page），做 10 萬次翻譯。TLB miss 時做一次 walk（記 2 次記憶體存取）。真跑：

```
=== 場景 A：4 個 page 的迴圈（強 locality）===
translations = 100000
TLB hits     = 99996
TLB misses   = 4
TLB hit rate = 99.996%
walk 記憶體存取 = 8 （有 TLB）
若無 TLB，每次都 walk = 200000 次記憶體存取
TLB 省下 = 100.0% 的 walk 存取
```

**10 萬次翻譯，只 miss 4 次，hit rate 99.996%。** 為什麼恰好 4 次？4 個頁各在第一次被碰時 miss 一次（compulsory，把它的翻譯 walk 出來填進 TLB），之後 99996 次全部命中（4 個頁的翻譯都在 8-entry TLB 裡，塞得下，不互踢）。

看那筆帳的威力：
- **有 TLB**：4 次 miss × 2 次記憶體存取 = **8 次** walk 存取。
- **沒 TLB**：10 萬次翻譯 × 2 = **20 萬次** walk 存取。

TLB 省下了 99.996% 的 walk 存取——把「每次翻譯 +2 次記憶體存取」壓成「幾乎 0」。這就是為什麼虛擬記憶體開了之後程式沒有慢 3 倍：locality 讓 TLB 命中率逼近 100%，walk 的代價被攤到可忽略。**TLB 是讓虛擬記憶體可用的關鍵，不是可有可無的最佳化。**

## 範例二：working set 超過 TLB 容量，thrashing

TLB 只有 8 entry。如果程式同時碰的頁超過 8 個，會怎樣？跑一個輪流碰 64 個頁的場景（`0x90000000` 起，每次 +4 KiB，繞 64 頁循環）：

```
=== 場景 B：64 個 page 輪流（超過 8-entry TLB）===
TLB hit rate = 0.000%  (working set > TLB → 幾乎全 miss)
TLB misses   = 100000
```

**hit rate 掉到 0%，10 萬次全 miss。** 為什麼這麼慘？8-entry TLB 用 FIFO，輪流碰 64 個頁時：碰頁 0 填進 TLB、碰頁 1…7 填滿、碰頁 8 踢掉頁 0、…繞一圈回到頁 0 時它早被踢掉了，又 miss。**working set（64 頁）遠超 TLB 容量（8）→ 每條 entry 還沒被重用就被踢掉 → 全 miss。**

這叫 **TLB thrashing**，和 Ch 26 的 cache capacity miss 是同一種病（working set > 容量）。後果：每次翻譯都退化成一次完整 walk，虛擬記憶體的翻譯代價全額付出。真實系統遇到 TLB thrashing（例如掃一個超大陣列、隨機存取巨大 hash table）效能會顯著下降。

解法對應 Ch 28 的 superpage：**用大頁減少 TLB 壓力**。一條 TLB entry 若映射 4 MiB superpage（而非 4 KiB 頁），同樣 8 個 entry 就能覆蓋 32 MiB 而非 32 KiB——working set 相對 TLB 的比例大幅下降，thrashing 緩解。這是資料庫、JVM 這類大記憶體應用愛用 huge page 的原因。

## 核心概念：SFENCE.VMA 為什麼必要

TLB 快取了翻譯結果，但翻譯結果會變——OS 可能改 page table（換頁、改權限、回收頁）。改了 page table 之後，**TLB 裡的舊翻譯就過時了**，但硬體不知道 OS 改了什麼（TLB 不會自動跟 page table 同步）。

這就是 `SFENCE.VMA` 指令的用途：**告訴硬體「我改了 page table，把 TLB 相關的舊翻譯作廢」**。

```
   OS 改 page table 的流程：
   1. 寫新的 PTE（改 memory 裡的 page table）
   2. SFENCE.VMA rs1, rs2    ← 作廢 TLB 裡對應的翻譯
      rs1 = 0：作廢所有 VA 的翻譯；否則只作廢 rs1 這個 VA 的
      rs2 = 0：作廢所有 ASID；否則只作廢 rs2 這個 ASID 的
```

為什麼不讓硬體自動同步？因為 page table 在記憶體裡，TLB 不可能監看每一次記憶體寫（那太貴）。RISC-V 的設計哲學是**明確**：OS 改了表就得自己下 SFENCE.VMA 通知硬體，硬體才作廢對應 TLB entry。這是軟硬體契約的一部分。

漏下 SFENCE.VMA 的後果很隱蔽也很致命：OS 明明改了權限（例如把一頁改成唯讀），但 TLB 裡還快取著舊的「可寫」翻譯，程式繼續用舊翻譯寫那頁——安全漏洞或資料損毀，而且難重現（要 TLB 剛好還快取著舊值）。這是寫 OS/hypervisor 的經典大坑。

`SFENCE.VMA` 執行時，pipeline 通常要**排空（drain）並 flush**——確保它之前的存取都用舊翻譯做完、它之後的存取都用新翻譯，中間不能混。這連到下一段的 pipeline 互動。

## 底層機制：page fault 怎麼 flush pipeline

TLB miss 觸發 walk（Ch 28 的 PTW），walk 若翻不出（PTE 無效或權限不符）就是 **page fault**。page fault 是一種 exception，要進 trap（Part 5 詳講），對 pipeline 的即時影響是 **flush**——和 Ch 18 的 branch misprediction flush 同一套機制。

想想 page fault 發生的位置：
- **指令 page fault**：IF 級取指時翻譯失敗（要執行的指令所在的頁沒映射）。這條指令根本不該執行。
- **load/store page fault**：MEM 級存取時翻譯失敗（load/store 的資料頁沒映射或權限不符）。

處理流程（以 load page fault 為例）：

```
   MEM 級 load，TLB miss → walk → page fault
   1. 標記這條指令有 exception（page fault，cause = load page fault）
   2. 讓它繼續走到 pipeline 末端（WB 級）——precise exception 要求：
      這條指令之前的指令全部正常完成、之後的全部作廢
   3. flush 這條指令之後的所有級（IF/ID/EX 裡比它晚的指令，清成 bubble）
   4. 把 fault 的 PC 存進 mepc/sepc、cause 存進 mcause/scause、
      出錯的 VA 存進 mtval/stval
   5. 跳到 trap handler（mtvec/stvec 指的位址）
   6. OS handler 處理（換頁/配頁/或 SIGSEGV），處理完 sret 回到 mepc 重試
```

關鍵是 **precise exception（精確例外）**：trap 發生時，pipeline 的狀態必須「乾淨」——出錯指令之前的都做完、之後的都當沒發生。這樣 OS 處理完能從出錯那條指令**精確地重試**（demand paging 換頁後重跑那條 load，這次 TLB 有了就成功）。要做到 precise，就得像 Ch 18 flush 分支後續指令一樣，把 fault 指令之後的全部 flush 掉。

flush 的接線沿用 Ch 18：把各級 pipeline register 的 flush 致能拉起（清成 NOP），同時把 PC 導向 trap vector。差別只在觸發源：branch misprediction 是「猜錯了」，page fault 是「翻譯失敗」，但「清掉錯誤路徑上的指令」的動作一樣。

> page fault 和 branch flush 的一個差別：branch 猜錯後重跑的是**正確路徑**的新指令；page fault 是 OS 處理完後**重跑同一條**出錯指令（sret 回到 mepc）。所以 page fault 要精確保存出錯 PC（mepc），branch flush 不用。

## 核心概念：TLB + cache + pipeline 三者怎麼串

把記憶體階層這幾章串起來，一次 load 的完整路徑（開了 VM）：

```
   MEM 級 load，VA 到手
   1. 查 TLB：VA 的 VPN → PPN
      hit  → 一拍拿到 PA，繼續
      miss → walk（Ch 28，多拍，stall pipeline）→ 填 TLB → 得 PA
             walk 失敗 → page fault → flush + trap（Ch 18 機制）
   2. 有了 PA，查 D-cache（Ch 27）：
      hit  → 一拍拿到資料
      miss → refill（多拍，stall pipeline）→ 得資料
   3. 資料回 CPU，pipeline 繼續
```

翻譯（TLB/walk）和資料存取（cache）是**串接**的兩道關卡，各自可能 hit（快）或 miss（慢，stall）。最壞情況：TLB miss + walk 的 PTE 也 cache miss + 最後資料也 cache miss——一次 load 卡幾百拍。最好情況：TLB hit + cache hit——一拍完成。locality 讓大多數時候是最好情況。

**VIPT 最佳化**（Ch 26 提過）：讓 TLB 翻譯和 cache 查詢**平行**跑，而非串接。用 VA 的低位（page offset，翻譯前後不變）當 cache index 先查 cache，同時 TLB 翻出實體 tag，最後用實體 tag 比對——把兩道關卡疊在一拍。這是真實 L1 的標準做法，代價是 cache 大小受 page 大小限制。

## 對比取捨

| 面向 | 選項 | 取捨 |
|---|---|---|
| TLB 關聯度 | fully-associative | 小 TLB 全相聯：無 conflict、命中率高，比較器隨容量變貴 |
| | set-associative | 大 TLB（L2 TLB）用，省比較器，有 conflict miss |
| TLB 替換 | FIFO / pseudo-LRU | TLB 小，簡單策略夠用（本章 FIFO） |
| 減 TLB miss | 大頁 / superpage | 一條 entry 覆蓋更多空間，緩解 thrashing，但粒度粗 |
| TLB 一致性 | 軟體 SFENCE.VMA | 明確、便宜（不用硬體監看記憶體），但漏下就出錯 |
| 翻譯與 cache | 串接（PIPT） | 簡單，但翻譯 + 查 cache 兩拍 |
| | 平行（VIPT） | 一拍完成，但 cache 大小受 page size 限制 |

## 踩雷區

**雷 1：以為 TLB 會自動跟 page table 同步。**
- 錯誤直覺：「OS 改了 page table，TLB 應該自動更新啊」。
- 正確認識：TLB **不會**自動跟 page table 同步——page table 在記憶體裡，TLB 監看不了每次記憶體寫（太貴）。OS 改了 page table **必須**自己下 `SFENCE.VMA` 作廢對應 TLB entry，硬體才會重新 walk。漏下 SFENCE.VMA 是 OS/hypervisor 的經典大坑：程式繼續用 TLB 裡的舊翻譯，導致寫唯讀頁、存取已回收頁——安全漏洞或資料損毀，而且因為要「TLB 剛好還快取舊值」才觸發，極難重現。

**雷 2：以為 TLB miss 就是 page fault。**
- 錯誤直覺：「TLB 查不到 = 翻譯失敗 = page fault」。
- 正確認識：TLB miss 和 page fault 是兩回事。TLB miss 只是「這個翻譯還沒快取」——去 walk 一趟，**walk 成功**就填進 TLB 繼續跑，程式無感（只是慢幾拍）。只有 **walk 也翻不出來**（PTE 無效、權限不符）才是 page fault，才 trap。範例一那 4 次 TLB miss 全都 walk 成功、不是 page fault。把 TLB miss 當 page fault，你會以為每次 miss 都要 trap（那就慢死了），完全誤解 TLB 的運作。

**雷 3：page fault 不做 precise exception。**
- 錯誤直覺：「page fault 就跳 handler，pipeline 裡其他指令隨它去」。
- 正確認識：page fault 必須是 **precise**——出錯指令之前的指令全部正常完成、之後的全部作廢（flush），這樣 OS 處理完（換頁）能從出錯那條**精確重試**。若不 precise：出錯指令後面的指令已經改了狀態（寫了暫存器/記憶體），OS 重試出錯指令時，那些後面指令的效果殘留著——重試會算錯，或後面指令被執行兩次。demand paging 完全依賴 precise exception 才能「fault → 換頁 → 無縫重試」。這是為什麼 page fault 要像 branch flush 一樣清掉後續指令，並精確保存 mepc。

**雷 4：忘記 walk 本身的記憶體存取也可能 cache miss。**
- 錯誤直覺：「TLB miss 就 walk 兩次記憶體存取，固定成本」。
- 正確認識：walk 讀的 PTE 也存在記憶體裡，也走 cache——PTE 可能 cache hit（快）也可能 cache miss（要上 DRAM，慢）。所以一次 TLB miss 的真實成本是浮動的：2 次 PTE 存取，每次可能 1 拍（cache hit）到 上百拍（cache miss）。最壞情況 TLB miss + PTE cache miss + 資料 cache miss 疊起來卡很久。真實 core 有 page walk cache（專門快取中間層 PTE）緩解這點。低估 walk 成本會讓你的效能模型嚴重失準。

## 進階延伸

- **多級 TLB（L1 TLB + L2 TLB）**：像 cache 分層，TLB 也分層。L1 TLB 極小（幾~幾十條）、極快（一拍）、常分 iTLB/dTLB；L1 TLB miss 查更大的 L2 TLB（幾百~上千條，慢一點）；L2 TLB 也 miss 才真的 walk。這把「TLB 要小才快」和「TLB 要大才少 miss」的矛盾用分層化解，和 L1/L2 cache 同構。
- **ASID 與 context switch 效能**：沒有 ASID 時，每次 context switch（換行程 = 換 satp）都要 SFENCE.VMA 清空整個 TLB，新行程一開始全 TLB miss，很痛。ASID 讓每條 TLB entry 標記所屬行程，切換時舊行程的 entry 不必清（ASID 不符自然不命中），切回來還在——大幅減少切換的 TLB 冷啟動代價。這是 satp 有 ASID 欄位的理由（Ch 28）。
- **TLB shootdown（多核的麻煩）**：多核時每顆核有自己的 TLB。核 A 的 OS 改了 page table 並 SFENCE.VMA 清了自己的 TLB，但核 B 的 TLB 可能還快取著同一條舊翻譯——A 得透過 IPI（inter-processor interrupt）通知 B 也清（TLB shootdown）。這是多核 VM 的一大效能與正確性難題，單核（本課主線）沒有，但你想做 SMP 就繞不開。
- **把 TLB 接進 core 的 SystemVerilog**：本章 C 模型的 TLB 翻成 RTL：一個 fully-associative 的小陣列（valid/VPN/PPN/flags），查時平行比對（一堆 `==` 或做成 CAM），miss 時觸發 Ch 28 的 PTW 狀態機（stall pipeline 等 walk），walk 成功填 TLB、失敗拉 page fault 訊號（接 Ch 18 flush + Part 5 trap）。SFENCE.VMA 指令解碼後清 TLB valid bit。這是把 Ch 28+29 完整接進 core 的整合工作，也是 Part 5 trap 機制的前置。

## 本章重點整理

- **TLB = 快取翻譯結果**：Sv32 每次翻譯要 walk 2 次記憶體存取，TLB 把 VPN → PPN 的結果快取起來，靠 locality 讓大多數翻譯一拍命中。TLB 之於 page table，如 cache 之於 DRAM。
- **結構**：fully-associative（小、無 conflict）、存 VPN/PPN/權限/ASID/G，FIFO 或 pseudo-LRU 替換。
- **真跑數字**：4 個頁的強 locality 場景，10 萬次翻譯只 4 次 miss，hit rate 99.996%，省下 99.996% 的 walk 存取；64 頁超過 8-entry TLB，thrashing 到 0% hit rate。
- **SFENCE.VMA**：TLB 不自動跟 page table 同步，OS 改表後必須下 SFENCE.VMA 作廢舊翻譯，漏下是經典大坑。
- **TLB miss ≠ page fault**：miss 是「還沒快取」（walk 一趟填進去就好），page fault 是「walk 也翻不出來」（才 trap）。
- **page fault 要 precise + flush**：沿用 Ch 18 flush 機制清掉出錯指令後續，精確保存 mepc，讓 OS 處理完能無縫重試（demand paging 的基礎）。
- **完整路徑**：load = 查 TLB（miss 則 walk）翻出 PA → 查 D-cache（miss 則 refill）拿資料，兩道關卡串接（VIPT 可平行）。

## 自我檢核

- [ ] 我能算出「沒 TLB 時虛擬記憶體讓每次存取慢幾倍」，並解釋 TLB 靠 locality 怎麼把它攤平。
- [ ] 我能說出 TLB 的結構（為什麼 fully-associative）、一條 entry 存什麼、ASID 的作用。
- [ ] 我能解釋範例一為什麼恰好 4 次 miss、範例二為什麼 thrashing 到 0%，以及大頁怎麼緩解 thrashing。
- [ ] 我能說清楚 SFENCE.VMA 為什麼必要、漏下會怎樣。
- [ ] 我能區分 TLB miss 和 page fault，說出各自的後果。
- [ ] 我能解釋 page fault 為什麼要 precise exception + flush、跟 Ch 18 branch flush 的異同。

## 延伸閱讀

- **[RISC-V Privileged Spec](https://riscv.org/technical/specifications/) 的 SFENCE.VMA（第 10.2 節「Supervisor Memory-Management Fence Instruction」）與 exception 定義**：權威來源。SFENCE.VMA 的 rs1/rs2 語意、什麼時候必須下、TLB 一致性的精確契約全在這。fault cause code（instruction/load/store page fault 各自的 mcause 值）也在特權 spec，是你實作 page fault trap 的依據。
- **《Computer Organization and Design, RISC-V Edition》(Patterson & Hennessy) 第 5.7 節 TLB 部分**：教科書版本，把 TLB 當成「page table 的 cache」講，附 TLB + cache 一起查的流程圖（含 TLB miss/page fault 的分支），正好對應本章的完整路徑。讀它把 TLB 放進整個記憶體階層的圖裡。
- **《Operating Systems: Three Easy Pieces》(Arpaci-Dusseau) 第 19 章「Paging: Faster Translations (TLBs)」**：從 OS/硬體交界講 TLB 為什麼存在、TLB miss 怎麼處理（硬體 walk vs 軟體 walk）、context switch 與 ASID、TLB thrashing。它的 array-access TLB 命中率分析就是本章範例一二的原型，白話又深入，強烈推薦。
- **Bhattacharjee & Lustig《Architectural and Operating System Support for Virtual Memory》(Synthesis Lectures)**：想深入 TLB 的專書。多級 TLB、TLB shootdown、superpage、page walk cache 這些本章進階延伸提到的主題，它都有完整章節。你做完 core 想把 VM 子系統做到接近真實，這是最集中的一份參考。

下一章我們離開翻譯，處理 core 怎麼和外面的世界溝通：AXI4-Lite 標準總線、valid/ready handshake、memory-mapped I/O——讓 core 能讀寫一個 UART/LED 暫存器，真跑一次 bus transaction。

→ [Ch 30 AXI4-Lite 總線：memory-mapped I/O](./30-axi-bus-mmio.md)
