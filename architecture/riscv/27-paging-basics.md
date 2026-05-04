# Ch 27 — 分頁機制基礎：地址翻譯概念、Physical / Virtual 分離的理由

> 目標：理解虛擬記憶體存在的原因；能解釋 page table 在地址翻譯中的角色；知道 RISC-V 支援哪些分頁模式及 satp 的作用。

---

## 27.1 為什麼需要虛擬記憶體

在沒有虛擬記憶體的系統上，每個程式直接存取物理 RAM。這帶來幾個問題：

**問題 1：多程序互相干擾**
程式 A 可以直接讀寫程式 B 的記憶體。一個 bug 就能砸掉整個系統。

**問題 2：地址空間碎片化**
程式 A 用了 0x1000–0x5000，程式 B 只能從 0x5000 開始。如果程式 A 想擴展，沒有連續空間。

**問題 3：每個程式看到的地址不一樣**
Linker 把程式 link 到固定地址，不同程式不能 link 到同一個地址——但 shared library 想要有固定的 code offset（PIC 之前的世界）。

**虛擬記憶體的解法**：每個程式有自己的 **virtual address space（虛擬地址空間）**。程式看到的地址（Virtual Address, VA）和 RAM 的實際位置（Physical Address, PA）是分開的，由硬體（MMU）在每次存取時動態翻譯。

---

## 27.2 Physical Address vs Virtual Address

```
程式碼裡的指標：0x0000000000401000   ← Virtual Address (VA)
         |
         | MMU 翻譯（查 page table）
         v
實際 RAM 位址：  0x0000000083FA1000   ← Physical Address (PA)
```

VA 是 CPU core 的概念，PA 是記憶體匯流排上的訊號。MMU（Memory Management Unit）就是做這個翻譯的硬體。

---

## 27.3 Paging vs Segmentation

兩種管理記憶體的方式：

**Segmentation（分段）**：把記憶體切成大小不固定的段（segment），每個段有 base + limit。x86 的 descriptor table 就是這個。問題：外部碎片（兩個段之間的 gap）。

**Paging（分頁）**：把 VA 和 PA 都切成固定大小的 **page（頁面）**。每個 VA page 可以對應到任意一個 PA page frame。問題：內部碎片（page 裡用不完的空間），但通常比外部碎片好管理。

RISC-V 只支援 paging，沒有 segmentation。

---

## 27.4 Page 的基本概念

**Page size**：4 KiB（4096 bytes = 2^12 bytes）。這是 RISC-V 分頁的基本單位。

**Page number**：地址的高位部分，標識哪個 page。
**Page offset**：地址的低 12 bit，在 page 內的位置（0–4095）。

```
64-bit Virtual Address 的分解：
bits [63:12]   = Virtual Page Number (VPN)
bits [11:0]    = Page Offset

範例：VA = 0x0000000000401ABC
  VPN    = 0x401     (bits 23:12，這只是 Sv32 的概念，Sv39/48 更長)
  offset = 0xABC
```

翻譯只翻譯 VPN（哪個 page），offset 直接複製到 PA。

---

## 27.5 Page Table 的基本結構

Page table 是一個存在記憶體裡的查表結構。最簡單的形式是一維陣列：

```
Index = VPN
Value = PPN（Physical Page Number）

page_table[VPN] → PPN
PA = PPN * 4096 + offset
```

但是：如果 VA 是 64-bit，VPN 是 52-bit，陣列要有 2^52 個 entry——佔 4 PiB，根本放不下。

解法：**多層頁表（multi-level page table）**，只為實際用到的 VA 範圍配置頁表頁面。

---

## 27.6 地址翻譯流程（概念）

```
VA
 |
 +--[VPN 高位]--→ Level 1 Page Table (root)
                  |
                  +--[VPN 中位]--→ Level 2 Page Table
                                   |
                                   +--[VPN 低位]--→ Level 3 Page Table (leaf)
                                                    |
                                                    +--[PPN]--→ PA
                                                               |
                                                               + offset
                                                               |
                                                               = Final PA
```

每一層頁表的 entry 叫做 **PTE（Page Table Entry）**。葉節點（leaf）的 PTE 包含實際的 PPN，非葉節點的 PTE 指向下一層頁表的物理地址。

---

## 27.7 TLB 的作用

每次記憶體存取都查頁表要走 2–4 次記憶體存取——太慢。**TLB（Translation Lookaside Buffer）** 是一個在 CPU 內的快取，存放最近用過的 VA→PA 翻譯。

```
VA
 |
 +--→ TLB 查表
       |
  命中（hit）──→ PA（直接）
       |
  未命中（miss）──→ 硬體 page table walker
                    ──→ 更新 TLB
                    ──→ PA
```

TLB 命中通常只需要 1 cycle。TLB miss 要走頁表，數十到數百 cycle。程式的空間局部性決定 TLB 命中率。

---

## 27.8 RISC-V 分頁模式一覽

```
MODE 值   名稱    VA 位元   PA 位元   頁表層數   地址空間大小
------    ------  -------   -------   --------   -----------
0         Bare    N/A       N/A       0          無翻譯（物理地址）
1-7       reserved
8         Sv32    32-bit    34-bit    2          4 GiB VA（RV32 only）
9         Sv39    39-bit    56-bit    3          512 GiB VA
10        Sv48    48-bit    56-bit    4          256 TiB VA
11        Sv57    57-bit    56-bit    5          128 PiB VA
12-15     reserved
```

RISC-V 的分頁模式由 satp CSR 的 MODE 欄位控制。

---

## 27.9 satp CSR

**satp（Supervisor Address Translation and Protection）** CSR 控制 S-mode 的分頁：

```
RV64 的 satp（64-bit CSR）：
bits [63:60] = MODE   (4-bit)  分頁模式
bits [59:44] = ASID   (16-bit) Address Space ID
bits [43:0]  = PPN    (44-bit) root page table 的 Physical Page Number
```

設定 satp 就啟動分頁：

```c
// 設定 Sv39，root page table 在物理地址 root_pa
uint64_t satp_val = (9ULL << 60)               // MODE = Sv39
                  | (0ULL << 44)               // ASID = 0
                  | (root_pa >> 12);           // PPN
asm volatile ("csrw satp, %0" :: "r"(satp_val));
asm volatile ("sfence.vma zero, zero");        // 刷 TLB
```

在 Bare mode（MODE=0），所有記憶體存取都是物理地址，不翻譯。CPU 剛開機、OpenSBI 初始化時就在這個模式。

---

## 27.10 Kernel 地址空間的典型佈局

Linux on RV64（Sv39/Sv48）的地址空間：

```
0x0000000000000000                        使用者空間（低位）
  ...使用者程式的 text, data, heap, stack
0x0000003FFFFFFFFF    ← Sv39 使用者上限

...（非正規地址，不能用）...

0xFFFFFFC000000000    ← kernel 空間下界（Sv39）
  ...kernel text, data
  ...vmalloc 區域
  ...direct mapping of all physical memory
0xFFFFFFFFFFFFFFFF
```

Sv48 因為 VA 更大，分界線會不一樣，但上/下分割的概念相同。

---

## 自我檢核

- [ ] 能說出虛擬記憶體解決了哪三個主要問題
- [ ] 能畫出 VA → TLB → PA 或 VA → page table walk → PA 的流程
- [ ] 知道 RISC-V 支援哪幾種分頁模式（Bare/Sv32/Sv39/Sv48/Sv57）
- [ ] 能說出 satp CSR 的 MODE、ASID、PPN 欄位的位置
- [ ] 知道 4 KiB page 的 offset 是幾位（12 bit）

→ [Ch 28 — Sv39 三層頁表](28-sv39-pagetable.md)
