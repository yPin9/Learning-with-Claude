# Ch 28 — 虛擬記憶體與 MMU：Sv32 page walk

> **目標**：搞懂為什麼每個程式都以為自己獨佔整個位址空間、卻不會互相踩到——這是虛擬記憶體（virtual memory）在背後翻譯位址的功勞。這章你會學 virtual vs physical 位址、VM 解決的兩大問題（隔離與 relocation）、Sv32（RV32 的兩層分頁）的 PTE 格式與 satp CSR，然後**用 C 模型真跑一遍完整的 page table walk**：正常兩層走法翻出 PA、遇到未映射 PTE 觸發 page fault、以及 4 MiB superpage 一層就翻完。全程對照 `architecture/riscv` 課教的 Sv39 給你路標。這是深挖章。
> **環境**：WSL + gcc 11.4。walk 模型的每一步輸出皆真跑。本章的 walk 邏輯是 Ch 29 TLB 的基礎——TLB 就是快取這個 walk 的結果。

## 為什麼需要虛擬記憶體

到目前為止，我們的 core 用的都是**實體位址**：`lw x3, 0(x6)` 裡 x6 算出的位址，直接就是 DRAM 上那個 byte 的物理位置。單一程式、reset PC 固定 `0x80000000`，這樣沒問題。

但真實系統要同時跑很多程式，馬上撞牆：

1. **relocation（重定位）問題**：程式 A 編譯時假設自己從位址 `0x10000` 開始，程式 B 也假設自己從 `0x10000` 開始。它們不能同時用同一塊實體記憶體。難道每個程式都要編譯成不同的實體位址？那就無法「同一個 binary 到處跑」。
2. **isolation（隔離）問題**：程式 A 一個野指標寫到 `0x20000`，如果那剛好是程式 B 或作業系統的記憶體，A 就把 B/OS 搞爛了。沒有隔離，一個爛程式能拖垮整台機器。
3. **容量問題**：物理 DRAM 可能只有 4 GB，但你想跑一個「用到 8 GB」的程式（或很多程式加起來超過 4 GB）。實體位址做不到。

虛擬記憶體一次解決這三個：**每個程式活在自己的一整片「虛擬位址空間」裡，以為自己獨佔全部位址（例如 0 到 4 GB），而硬體（MMU）在每次存取時把虛擬位址（VA）翻譯成實體位址（PA）**。翻譯表由 OS 維護，每個程式一份。於是：

- relocation：A 和 B 都用虛擬位址 `0x10000`，但被翻到**不同**的實體位址，各安其位。
- isolation：A 的翻譯表裡沒有 B 的實體頁，A 根本翻不出 B 的位址——想踩也踩不到。
- 容量：虛擬空間可以比實體大，用不到的頁不必真的佔實體記憶體（甚至可以換到 disk，page fault 時再換回來）。

## 先建立直覺：郵政信箱 vs 真實住址

把虛擬位址想成**郵政信箱號碼**，實體位址想成**真實住址**。

你對外公布的地址是「郵政信箱 100 號」（虛擬位址）。別人寄信到信箱 100，郵局（MMU）查一張對照表，知道「信箱 100 → 實際送到中山路 5 號」（實體位址），再把信送過去。

- **relocation**：你搬家了（實體記憶體換位置），只要郵局改對照表「信箱 100 → 新住址」，對外公布的信箱號不變，寄信的人無感。
- **isolation**：你只能收到寄給「你的信箱」的信。別人的信箱號你不知道、也不在你的對照表裡，收不到也寄不了。
- **對照表的粒度**：不是一個 byte 一條對照（那表會大到爆），而是以**頁（page，通常 4 KiB）**為單位。「信箱 100 這一整區 → 中山路 5 號那一整棟」。頁內的偏移（offset）翻譯前後不變，只翻「哪一頁」。

MMU 就是那個郵局，page table 就是那張對照表，page fault 就是「寄到一個對照表裡沒有的信箱號」（OS 得介入處理）。

## 核心概念：Sv32 的位址切法

RISC-V 的分頁方案按虛擬位址寬度命名。RV32 用 **Sv32**（32-bit 虛擬位址、兩層 page table）；RV64 常用 **Sv39**（39-bit 虛擬位址、三層），你在 `architecture/riscv` 課學過的就是它。本課是 RV32，用 Sv32。

Sv32 把 32-bit 虛擬位址切成三段：

```
   32-bit 虛擬位址 (VA)：
   [ VPN[1] (10 bit) | VPN[0] (10 bit) | offset (12 bit) ]
     bit 31..22         bit 21..12        bit 11..0

   VPN = Virtual Page Number（虛擬頁號），分兩層各 10 bit
   offset = 頁內偏移，12 bit → 頁大小 2^12 = 4 KiB
```

實體位址（PA）在 Sv32 是 **34-bit**（RV32 的實體空間可比虛擬大！）：

```
   34-bit 實體位址 (PA)：
   [ PPN[1] (12 bit) | PPN[0] (10 bit) | offset (12 bit) ]
     bit 33..22         bit 21..12        bit 11..0

   PPN = Physical Page Number（實體頁號），22 bit
   offset = 頁內偏移，12 bit（和 VA 一樣，翻譯時原封不動搬過去）
```

**offset 12 bit 翻譯前後不變**——這是分頁的核心：翻譯只換「哪一頁」（VPN → PPN），頁內位置（offset）不動。所以對照表只需記 VPN → PPN 的對應，每頁一條。

對照 Sv39（RV64）：VA 是 `[VPN[2] | VPN[1] | VPN[0] | offset]`，三層各 9 bit、offset 12 bit，PA 56-bit。多一層是因為 39-bit 位址空間更大，兩層頁表裝不下所有對應，要三層。**Sv32 兩層、Sv39 三層，走法一樣，只是層數不同。** 你懂了 Sv32 這章，Sv39 就是多走一層。

## 核心概念：PTE 格式

page table 裡每一格叫 **PTE（Page Table Entry，頁表項）**。Sv32 的 PTE 是 32-bit：

```
   Sv32 PTE (32-bit)：
   [ PPN[1] (12) | PPN[0] (10) | RSW (2) | D | A | G | U | X | W | R | V ]
     bit 31..20     bit 19..10    9..8     7   6   5   4   3   2   1   0

   V : Valid    — 這條 PTE 有效嗎？0 = 無效（walk 到這裡 → page fault）
   R : Read     — 這頁可讀？
   W : Write    — 這頁可寫？
   X : eXecute  — 這頁可執行（放指令）？
   U : User     — user mode 可存取？（S mode 存取 U 頁受 SUM 控制）
   G : Global   — 全域映射（所有位址空間共用，不隨 satp 切換而失效）
   A : Accessed — 這頁被存取過（OS 用來做頁替換）
   D : Dirty    — 這頁被寫過（write-back 到 disk 時要用）
   RSW: 保留給 supervisor 軟體自用
   PPN: 這頁對應的實體頁號（或指向下一層 table 的實體頁號）
```

關鍵的 **R/W/X 三個 bit 決定這條 PTE 是「葉子」還是「指標」**：

- **R=W=X=0**：這條 PTE 是**指標（pointer）**，它的 PPN 指向**下一層 page table**。walk 要繼續往下一層。
- **R、W、X 至少一個為 1**：這條 PTE 是**葉子（leaf）**，它的 PPN 就是最終的實體頁號，walk 結束。

這個「R/W/X 全 0 就是指標，否則是葉子」的規則是 walk 何時停止的判準。葉子可以出現在任何一層——出現在最後一層是普通 4 KiB 頁，出現在**中間層**就是 superpage（下面範例三會示範）。

## 核心概念：satp CSR

MMU 從哪找到「第一層 page table」在哪？靠 **satp（Supervisor Address Translation and Protection）CSR**。Sv32 的 satp（32-bit）：

```
   satp (Sv32, 32-bit)：
   [ MODE (1 bit) | ASID (9 bit) | PPN (22 bit) ]
     bit 31          bit 30..22     bit 21..0

   MODE : 0 = Bare（不分頁，VA=PA）；1 = Sv32（啟用兩層分頁）
   ASID : Address Space ID，區分不同行程的位址空間（讓 TLB 不必每次切換都全清，Ch 29）
   PPN  : root（第一層）page table 的實體頁號
```

satp.PPN 指向 **root page table（L1 table）** 的實體位置。OS 切換行程（context switch）時，把新行程的 root page table 位址寫進 satp.PPN——一寫，整個位址空間就換了。這是虛擬記憶體「每個行程一份對照表」的落實：改 satp = 換一整張對照表。

satp.MODE=0（Bare）時不分頁，VA 直接當 PA——這正是我們前 27 章 core 的狀態（沒開 VM）。MODE=1 才啟用 Sv32。

## 底層機制：page table walk 的完整流程

有了 satp（root table 在哪）、VA（要翻的位址）、PTE 格式（怎麼讀每一格），walk 的流程是：

```
   輸入：satp.PPN（root table 實體頁號）、VA
   VPN[1] = VA[31:22]，VPN[0] = VA[21:12]，offset = VA[11:0]

   1. a = satp.PPN * 4096            （root table 的實體起始位址）
      讀 pte1 = mem[a + VPN[1]*4]     （第一層，用 VPN[1] 當索引）
   2. if (pte1.V == 0)               → PAGE FAULT（無效）
   3. if (pte1 是葉子, R|W|X != 0)   → superpage，翻完（見範例三）
   4. 否則 pte1 是指標：
      a = pte1.PPN * 4096            （第二層 table 的實體起始位址）
      讀 pte0 = mem[a + VPN[0]*4]     （第二層，用 VPN[0] 當索引）
   5. if (pte0.V == 0)               → PAGE FAULT
   6. if (pte0 不是葉子)             → PAGE FAULT（最後一層必須是葉子）
   7. PA = pte0.PPN * 4096 + offset  （葉子的 PPN + 原 offset = 最終實體位址）
```

每一步「讀一個 PTE」都是一次**真實的記憶體存取**（PTE 存在實體記憶體裡）。所以 Sv32 一次翻譯要 **2 次記憶體存取**（讀 pte1、讀 pte0），Sv39 要 3 次。這就是為什麼要 TLB（Ch 29）——不然每個 load/store 都要多 2~3 次記憶體存取去 walk，慢死。

## 範例一：正常兩層 walk 翻出 PA

我們用 C 模型把 walk 一步步跑出來。一塊假的實體記憶體 `phys[]`，佈局：root table 在 PA `0x1000`（PPN=1）、L0 table 在 PA `0x2000`（PPN=2）、data page 在 PA `0x5000`（PPN=5）。建兩條 PTE：`root[1]` 指向 L0 table（指標，R=W=X=0），`L0[1]` 是葉子指向 data page（R|W|X|V）。翻 VA `0x00401abc`（VPN[1]=1、VPN[0]=1、offset=0xabc）。真跑：

```
=== 案例 1：正常兩層 walk ===
VA = 0x00401abc -> VPN[1]=1 VPN[0]=1 offset=0xabc
  L1: table@0x00001000 [idx 1] -> PTE=0x00000801 (V=1 R=0 W=0 X=0)
  L0: table@0x00002000 [idx 1] -> PTE=0x0000140f (V=1 R=1 W=1 X=1)
  -> leaf: PPN=0x00005, PA = 0x00005abc
結果 PA = 0x00005abc  (預期 0x5000|0xabc = 0x00005abc)
```

逐步看：
- **切位**：`0x00401abc` → VPN[1]=1、VPN[0]=1、offset=0xabc。
- **L1（第一層）**：root table 在 `0x1000`，用 VPN[1]=1 當索引，讀到 `PTE=0x00000801`。它 V=1（有效），R=W=X=0（**指標**，不是葉子），所以要往下一層。它的 PPN=2（`0x801 >> 10 = 2`），指向 L0 table 在 `0x2000`。
- **L0（第二層）**：L0 table 在 `0x2000`，用 VPN[0]=1 當索引，讀到 `PTE=0x0000140f`。它 V=1、R=W=X=1（**葉子**！），PPN=5（`0x140f >> 10 = 5`），指向 data page 在 `0x5000`。
- **組 PA**：葉子 PPN=5 → 實體頁起始 `0x5000`，加上原 offset `0xabc`，得 **PA = 0x00005abc**。和預期完全一致。

這就是一次完整的兩層 walk：VPN[1] 索引第一層找到指標、VPN[0] 索引第二層找到葉子、葉子 PPN + offset = 實體位址。兩次記憶體存取（讀 pte1、pte0）。

## 範例二：未映射的 PTE 觸發 page fault

翻一個沒建立對應的位址 `0x00402000`（VPN[1]=1、VPN[0]=2）。`root[1]` 一樣指向 L0 table，但 `L0[2]` 從沒被設過（是 0，V=0）。真跑：

```
=== 案例 2：page fault（L0 PTE 未映射）===
VA = 0x00402000 -> VPN[1]=1 VPN[0]=2 offset=0x000
  L1: table@0x00001000 [idx 1] -> PTE=0x00000801 (V=1 R=0 W=0 X=0)
  L0: table@0x00002000 [idx 2] -> PTE=0x00000000 (V=0 R=0 W=0 X=0)
  -> PAGE FAULT: L0 PTE invalid
```

- L1 一樣走到 L0 table（`root[1]` 是有效指標）。
- L0 用 VPN[0]=2 當索引，讀到 `PTE=0x00000000`——**V=0，無效**。這個虛擬頁沒有對應的實體頁。walk 到此觸發 **page fault**。

page fault 不是「壞事」，而是虛擬記憶體的**正常機制**。硬體翻不出來時，不會亂給一個位址（那會讀寫到亂七八糟的地方），而是產生一個 exception（trap），把控制權交給 OS。OS 的 page fault handler 決定怎麼辦：

- 這頁其實被換到 disk 了 → 從 disk 換回實體記憶體、建好 PTE、重試這條指令（demand paging）。
- 這是合法但還沒配置的頁（例如 stack 成長、mmap 的 lazy allocation）→ 配一個實體頁、建 PTE、重試。
- 這是真的非法存取（野指標）→ 送 SIGSEGV 殺掉行程（就是你熟悉的 segfault）。

硬體只負責「翻不出來就 trap」，怎麼處理是 OS 的事。這個 trap 怎麼進出 pipeline、怎麼 flush，是 Ch 29（page fault 與 pipeline 互動）和 Part 5（trap 機制）的主題。

## 範例三：4 MiB superpage，一層就翻完

Sv32 的葉子可以出現在**第一層**——這時它對應一整個 **4 MiB 的大頁（superpage）**，而不是 4 KiB 的普通頁。因為第一層一格涵蓋 `2^22 = 4 MiB` 的虛擬空間（VPN[0] 和 offset 共 22 bit 全被這一格罩住）。

建 `root[2]` 為葉子（R|W|X|V，PPN 對齊到 4 MiB），翻 VA `0x00800123`（VPN[1]=2）。真跑：

```
=== 案例 3：4 MiB superpage（L1 直接是 leaf）===
VA = 0x00800123 -> VPN[1]=2 VPN[0]=0 offset=0x123
  L1: table@0x00001000 [idx 2] -> PTE=0x0010000f (V=1 R=1 W=1 X=1)
  -> L1 is a leaf (4 MiB superpage)
結果 PA = 0x00400123
```

- L1 用 VPN[1]=2 當索引，讀到 `PTE=0x0010000f`——V=1、R=W=X=1（**葉子，但在第一層**！）。
- 葉子在第一層 → 這是 4 MiB superpage。walk **一層就結束**（只 1 次記憶體存取，比普通頁少一次）。
- 組 PA：superpage 的 PPN 對齊 4 MiB，加上 VA 低 22 bit（VPN[0]+offset）當頁內偏移，得 **PA = 0x00400123**。

superpage 的好處：(1) 一格 PTE 就映射 4 MiB，省 page table 空間（不用建整個第二層）；(2) walk 少一層，快；(3) 一條 TLB entry（Ch 29）就覆蓋 4 MiB，大幅減少 TLB miss。用於 kernel 映射、大塊連續記憶體（huge page）。代價是粒度粗——4 MiB 要嘛全映射要嘛不映射，浪費也以 4 MiB 為單位。Sv39 還有 2 MiB 和 1 GiB 兩種 superpage（葉子在第二、第一層）。

## 對比取捨：Sv32 vs Sv39，分頁 vs 不分頁

| 面向 | Sv32（本課，RV32） | Sv39（RV64，riscv 課） |
|---|---|---|
| VA 寬度 | 32-bit | 39-bit |
| 層數 | 2 層 | 3 層 |
| VPN 每層 | 10 bit | 9 bit |
| PA 寬度 | 34-bit | 56-bit |
| PTE 大小 | 4 B | 8 B |
| superpage | 4 MiB（葉在 L1） | 2 MiB / 1 GiB |
| walk 記憶體存取 | 2 次 | 3 次 |

| 面向 | 分頁（Sv32/39） | 不分頁（Bare，前 27 章） |
|---|---|---|
| 隔離 | 有（各行程一張表） | 無（全共用實體空間） |
| relocation | 透明（改表即可） | 無（位址寫死） |
| 每次存取代價 | +2~3 次 walk（靠 TLB 攤平） | 0 |
| 適用 | 多工作業系統 | 單一程式、嵌入式裸機、bootloader |

一句話：**分頁用「每次存取多幾次 walk」換來「隔離 + relocation + 超額容量」**，TLB（Ch 29）把 walk 的代價攤到接近零，讓這筆交易划算。裸機/bootloader 不需要這些，就用 Bare（VA=PA），簡單直接——這就是我們 core 開機時的狀態。

## 踩雷區

**雷 1：以為 offset 也要翻譯。**
- 錯誤直覺：「整個 32-bit VA 都要透過 page table 翻成 PA」。
- 正確認識：只翻 **VPN → PPN（哪一頁）**，**offset（頁內位置）原封不動搬過去**。Sv32 offset 12 bit，VA 和 PA 的低 12 bit完全相同。這是分頁能用「每頁一條」的小表搞定的根本原因——若連 offset 都要翻，那表要一個 byte 一條，大到不可能。範例一的 `offset=0xabc` 在 VA 和 PA 裡都是 `0xabc`，沒變。

**雷 2：搞不清 PTE 什麼時候是「指標」什麼時候是「葉子」。**
- 錯誤直覺：「第一層一定是指標、最後一層一定是葉子」。
- 正確認識：判準是 **R/W/X 三個 bit**，不是層數。R=W=X=0 → 指標（指向下一層 table）；R|W|X != 0 → 葉子（PPN 就是最終實體頁）。葉子**可以出現在任何一層**——出現在最後一層是普通頁，出現在中間層就是 superpage（範例三第一層就是葉子 = 4 MiB 大頁）。把「層數」和「葉/指標」綁死，你就理解不了 superpage，也會在 walk 邏輯裡寫錯停止條件。

**雷 3：忘記每次 walk 都是真實記憶體存取。**
- 錯誤直覺：「page table 是硬體內部的東西，查它不花時間」。
- 正確認識：page table 存在**實體記憶體（DRAM）**裡，MMU walk 時每讀一個 PTE 就是一次真實的記憶體存取。Sv32 兩層 = 每次翻譯 2 次記憶體存取，Sv39 三層 = 3 次。這些存取本身也可能 cache miss（PTE 不在 cache 就得上 DRAM）。所以**沒有 TLB 的話，每個 load/store 都要多付 2~3 次記憶體存取**——這是 Ch 29 TLB 存在的全部理由。低估 walk 的成本，你就理解不了為什麼 TLB 是必需品而非最佳化。

**雷 4：以為 page fault 一定是程式的錯。**
- 錯誤直覺：「page fault = segfault = 程式壞了」。
- 正確認識：page fault 是虛擬記憶體的**正常運作機制**，大多數 fault 是被 OS 悄悄處理掉、程式繼續跑的：頁被換到 disk（換回來就好）、lazy allocation（第一次碰才配實體頁）、copy-on-write（fork 後第一次寫才複製）。只有「存取真的非法的位址」才變成 SIGSEGV 殺掉你。硬體只管「翻不出來就 trap」，是 OS 的 handler 判斷這個 fault 是該救回來還是該殺。範例二的 fault 在真實系統可能觸發一次 demand paging，程式根本不知道發生過。

## 進階延伸

- **A/D bit 的軟硬體分工**：PTE 的 Accessed（存取過）和 Dirty（寫過）bit 是 OS 做頁替換和 write-back 的依據。有些實作硬體會在 walk 時自動設 A/D（存取就設 A、寫就設 D），有些要求硬體遇到 A=0 或（寫時）D=0 就 page fault，讓 OS 軟體去設——RISC-V 兩種都允許（`menvcfg.ADUE` 控制）。這是硬體 walker 設計時要決定的一個細節，本章 C 模型沒實作 A/D 更新，真硬體要考慮。
- **權限檢查與 U/SUM/MXR**：walk 翻出 PA 只是第一步，還要檢查權限——這次存取是讀/寫/取指？當前 privilege mode（U/S/M，Ch 33）能不能存取這頁（看 PTE 的 R/W/X/U 和 mstatus 的 SUM/MXR bit）？權限不符也是 page fault（但 cause 不同：instruction/load/store page fault）。本章只做位址翻譯，權限檢查是 Ch 29/Part 5 的內容。
- **硬體 page table walker（PTW）長怎樣**：本章用 C 模型 walk，真 core 裡是一個硬體狀態機 PTW，TLB miss 時它自動去記憶體 walk（不需要軟體介入，RISC-V 是硬體 walk；有些 ISA 如早期 MIPS 是軟體 walk，TLB miss 觸發 trap 讓 OS 走）。PTW 通常有自己的小 cache（page walk cache）快取中間層 PTE。把本章的 C walk 邏輯翻成 SystemVerilog 狀態機（IDLE → READ_L1 → READ_L0 → DONE/FAULT），接上 Ch 27 的記憶體介面，就是一個能跑的 PTW——這是接進 core 的自然練習。
- **對照 Sv39 補完 RV64 圖像**：`architecture/riscv` 課教的 Sv39 是三層 9-bit VPN、8-byte PTE、56-bit PA。走法和 Sv32 一模一樣，只是多走一層 VPN[2]。你把本章的兩層 walk 想像成「Sv39 少一層」，或反過來「Sv39 是 Sv32 前面再插一層」，兩者就串起來了。RV64 Linux 實際用 Sv39/Sv48/Sv57，層數隨位址空間需求選。

## 本章重點整理

- **VM 解決三個問題**：relocation（各行程用同樣虛擬位址、翻到不同實體）、isolation（翻不出別人的頁就踩不到）、超額容量（虛擬空間可比實體大，用 disk 補）。
- **只翻頁不翻 offset**：VA 切成 `[VPN[1] | VPN[0] | offset]`，翻譯把 VPN → PPN，offset（頁內 12 bit）原封不動。頁大小 4 KiB。
- **PTE 的 R/W/X 決定葉/指標**：全 0 = 指標（指下一層 table）、非 0 = 葉子（PPN 是最終實體頁）。葉子在中間層 = superpage。
- **satp CSR**：MODE（Bare/Sv32）+ ASID + root table 的 PPN。改 satp = 換一整張對照表（context switch）。
- **walk 真跑**：正常兩層 → PA `0x00005abc`（2 次記憶體存取）；未映射 PTE（V=0）→ page fault；第一層葉子 → 4 MiB superpage，PA `0x00400123`（1 次存取）。
- **Sv32 兩層、Sv39 三層**，走法相同層數不同。每次 walk 都是真實記憶體存取——這是 TLB 存在的理由。

## 自我檢核

- [ ] 我能說出虛擬記憶體解決的三個問題，並用「郵政信箱」類比解釋隔離與 relocation。
- [ ] 我能把一個 Sv32 VA（例如 `0x00401abc`）切成 VPN[1]/VPN[0]/offset，並說出各幾個 bit。
- [ ] 我能看 PTE 的 R/W/X 判斷它是指標還是葉子，並解釋 superpage 是葉子出現在中間層。
- [ ] 我能追出範例一兩層 walk 的每一步，說明為什麼 L1 是指標、L0 是葉子、PA 怎麼組出來。
- [ ] 我能解釋範例二 page fault 為什麼發生（V=0）、以及 OS 可能怎麼處理（不一定是 segfault）。
- [ ] 我能說出 Sv32 一次 walk 幾次記憶體存取、為什麼這是 TLB 存在的理由，以及 Sv32 和 Sv39 的差別。

## 延伸閱讀

- **[RISC-V Privileged Spec](https://riscv.org/technical/specifications/) 第 10 節「Supervisor-Level ISA」的 Sv32 部分（10.3.1）**：權威來源。它精確定義 satp 格式、Sv32 PTE 的每個 bit、walk 演算法（那段有名的 8 步 pseudo-code），以及各種 fault 的觸發條件。本章的 walk 流程就是它的白話版，實作 PTW 時以它為最終仲裁。搭配 `architecture/riscv` 課的 Sv39 一起讀。
- **《Computer Organization and Design, RISC-V Edition》(Patterson & Hennessy) 第 5.7 節「Virtual Memory」**：教科書版本，把 VM 的動機（relocation/protection）、page table、page fault、TLB 一氣呵成講完。它的 page table 圖和「VM 就是 cache 的另一種形式（disk 是後盾）」的類比很有啟發，幫你把 Ch 25 的階層觀念延伸到 VM。
- **《Operating Systems: Three Easy Pieces》(Arpaci-Dusseau) 的 Paging 章節（18~20 章，免費線上）**：從 OS 角度講分頁——page table 怎麼建、multi-level page table 為什麼省空間、page fault handler 怎麼寫、demand paging/swap 怎麼運作。硬體（本章）只做翻譯，這幾章補上「OS 那半邊」，讓你懂範例二 fault 之後 OS 到底做了什麼。文字白話、範例清楚，強烈推薦。
- **《Digital Design and Computer Architecture, RISC-V Edition》(Harris & Harris) 第 8.4 節「Virtual Memory」**：從硬體/HDL 角度講 MMU、page table walk、TLB 怎麼接成電路。它把 walk 畫成狀態機，正好是你把本章 C 模型翻成 SystemVerilog PTW 的藍圖。

下一章我們解決「每次存取都要 walk 太慢」的問題：加一個 TLB 快取 walk 結果，讓大多數翻譯一拍完成；並看 page fault 怎麼觸發 trap、怎麼 flush pipeline，把翻譯機制真正接進 core。

→ [Ch 29 TLB 設計 + page fault 與 pipeline 互動](./29-tlb-page-fault.md)
