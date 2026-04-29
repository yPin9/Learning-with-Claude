# Ch 16 — AArch64 MMU 與分頁

> 目標：徹底搞懂 AArch64 分頁機制 — granule（4 KB / 16 KB / 64 KB）、級數、TTBR0/TTBR1、VA bits、page table descriptor 格式、attribute 與 MAIR。能自己手刻一份 page table。

## VA、PA、IPA：三個位址空間

AArch64 處理三類位址：

- **VA** (Virtual Address)：CPU 看到的、軟體用的
- **IPA** (Intermediate Physical Address)：guest 看到的「實體」（其實是另一層 VA）
- **PA** (Physical Address)：真實 DRAM 的位址

```
無虛擬化：
  VA --[stage 1 translation]--> PA

有虛擬化（guest）：
  guest VA --[stage 1, in guest]--> guest IPA
  guest IPA --[stage 2, in hypervisor]--> host PA
```

本章先講 stage 1（VA → PA）。Ch 22 講 stage 2。

## TTBR0 與 TTBR1：兩半位址空間

```
       ┌─────────────────────────┐ 0xFFFFFFFFFFFFFFFF
       │                         │
       │   TTBR1 (kernel)        │  高半段
       │                         │
       ├─────────────────────────┤ ~ 0xFFFF000000000000
       │       不可用            │  中間 hole
       ├─────────────────────────┤ ~ 0x0000FFFFFFFFFFFF
       │                         │
       │   TTBR0 (user)          │  低半段
       │                         │
       └─────────────────────────┘ 0x0000000000000000
```

AArch64 把 64-bit VA 分成兩半：

- **低半段 VA** 用 `TTBR0_EL1` 指向的 page table
- **高半段 VA** 用 `TTBR1_EL1` 指向的 page table
- **中間是 unmapped hole**（VA bits 沒覆蓋的範圍）

對 Linux：user space 在低半（TTBR0）、kernel 在高半（TTBR1）。**context switch 只切 TTBR0**，TTBR1 全 process 共用。這是 AArch64 對比 ARMv7-A 巨大的優化（後者要 split address space）。

## VA bits：能用多少 VA？

`TCR_EL1.T0SZ` 與 `T1SZ` 控制：

```
T0SZ = 16  →  VA bits = 64 - 16 = 48 bits  →  256 TB user space
T0SZ = 25  →  VA bits = 39 bits           →  512 GB user space
T1SZ 同樣
```

實作允許範圍 16–39 (4KB granule)。Linux 通常用 **48-bit VA**（256 TB），夠未來幾十年。

ARMv8.2 增加 **52-bit VA**（4 PB）支援。

## Granule：page 大小三選一

**Granule** 是「最小 page 大小」。AArch64 支援三種：

| Granule | 級數 (4-level) | VA bits | 對齊 |
|---|---|---|---|
| **4 KB** | 4 | 48 (max) | 4 KB |
| **16 KB** | 4 | 48 | 16 KB |
| **64 KB** | 3 | 52 | 64 KB |

Linux 編譯時選一種，全程不換。Ubuntu/Fedora 預設 4 KB；Apple Silicon macOS 用 16 KB。

**granule 越大**：page table 更小、TLB 更密、但內部碎片更多（不需要 4K 也得分一整 page）。**granule 小**反之。Apple 選 16K 是 trade-off：fewer TLB miss，碎片可接受。

## 4 KB granule 的 4 級分頁

48-bit VA 拆成：

```
 47        39 38       30 29       21 20      12 11      0
┌────────────┬───────────┬───────────┬──────────┬────────┐
│  L0 index  │ L1 index  │ L2 index  │ L3 index │ offset │
│   9 bits   │  9 bits   │  9 bits   │  9 bits  │ 12 bits│
└────────────┴───────────┴───────────┴──────────┴────────┘
```

每一級 9 bit = 512 entries 的 table（每 entry 8 bytes，table 共 4 KB — 剛好一個 page）。

```
TTBR0 ─→ L0 table (512 entries)
            ├─ entry[i] ─→ L1 table
                              ├─ entry[j] ─→ L2 table
                                                ├─ entry[k] ─→ L3 table
                                                                  ├─ entry[l] ─→ PA + offset
```

最終 PA = `entry[l] 中的 PA[47:12]` || `VA[11:0]`。

## Block descriptor：跳過級數

不是每個葉節點都要走完 4 級：可以在中間級用 **block descriptor**，直接 map 一大塊。

```
L1 block: 1 GB 大 page (4 KB granule)
L2 block: 2 MB 大 page
L3 entry: 4 KB 普通 page (只有這級不能 block)
```

**HugeTLB / hugepage 就是這個**：Linux 給 application 一個 2 MB 或 1 GB block，少 TLB miss。

## Page table descriptor 格式

每個 entry 是 64 bit：

```
 63          54 53 52 51 ...    12 11      2 1 0
┌──────────────┬──┬──┬──────────────┬────────┬───┐
│   高位元     │XN│PXN│              │ ... │ V │
│   attribute  │  │  │   PA[47:12]  │ attr │ T │
└──────────────┴──┴──┴──────────────┴────────┴───┘

bit[0]    Valid (V)：0 = 無效（fault）
bit[1]    Type (T)：0 = block, 1 = table（中間級）
                    在 L3 永遠 1（page descriptor）
bit[10]   AF (Access Flag)：訪問過設 1
bit[11]   nG (not Global)：1 = ASID-tagged
bit[7:6]  AP[2:1]：Access Permission
bit[8:7]  SH：Shareability domain
bit[4:2]  AttrIndx：MAIR_ELx index
bit[53]   PXN：Privileged Execute Never
bit[54]   UXN：Unprivileged Execute Never
```

## MAIR_EL1：Memory attribute 對照表

PT entry 的 AttrIndx 是個 **3-bit 索引**，指向 MAIR_EL1 的 8 個 byte 之一：

```
MAIR_EL1 (64-bit)
┌──────┬──────┬──────┬──────┬──────┬──────┬──────┬──────┐
│Attr7 │Attr6 │Attr5 │Attr4 │Attr3 │Attr2 │Attr1 │Attr0 │
└──────┴──────┴──────┴──────┴──────┴──────┴──────┴──────┘
 8-bit  8-bit  8-bit  8-bit  8-bit  8-bit  8-bit  8-bit
```

每個 8-bit byte 編碼一種 memory type（normal / device / cacheable / write-through / ...）。每個 PT entry 的 AttrIndx 選哪個 byte。

範例：

```c
MAIR_EL1 = (0xFF << 0)   // Attr0 = 0xFF: Normal Memory, WriteBack RA WA, inner+outer
         | (0x00 << 8)   // Attr1 = 0x00: Device-nGnRnE
         | (0x44 << 16)  // Attr2 = 0x44: Normal Memory, NonCacheable
         | ...
```

PT entry 設 AttrIndx = 0 表示「我是普通可 cache 記憶體」、AttrIndx = 1 表示「我是 device」、…。

## TLB：要不知道也得知道

CPU 不會每次都走 4 級 — **TLB（Translation Lookaside Buffer）**快取最近用過的 VA→PA 對應：

- **L1 TLB**：每核獨立，數十 entry，1 cycle hit
- **L2 TLB**：共享、上千 entry
- **TLB miss**：硬體自動走 page table，可能十幾 cycle

寫好 TLB-friendly 程式（locality 好、ASID 用對）能把 MMU 開銷降到接近 0。

Ch 17 細說 ASID 與 TLB 互動。

## 啟用 MMU 的步驟

寫一份 bare-metal 程式碼啟動 MMU 大致：

```c
1. 建好 page table（at least L0+L1+L2+L3）
2. 設 TTBR0_EL1 = 你的 L0 table 物理位址
3. 設 TCR_EL1（T0SZ、granule、cacheability、shareability）
4. 設 MAIR_EL1（memory type 對照表）
5. ISB (instruction barrier)
6. 設 SCTLR_EL1.M = 1（M = MMU enable）
7. ISB（重要！pipeline 要清掉，不然下一條指令還可能用舊 mapping）
```

在第 6 步之前後**位址會切換**（從 PA 變 VA）— 所以打開 MMU 的 code 必須 identity-mapped（VA == PA）才能跨過 enable 點。Practice B 會展開。

## 一張對照圖：x86_64 vs AArch64 分頁

| | x86_64 | AArch64 (4K granule) |
|---|---|---|
| 級數 | 4 (PML4 → PDPT → PD → PT) | 4 (L0→L1→L2→L3) |
| Page size | 4K, 2M, 1G | 4K, 2M, 1G |
| VA bits | 48 / 57 (with 5-level) | 48 / 52 |
| 高低半切換 | 用 PML4 entries 的高半（CR3 一個 root） | 用兩個 root：TTBR0 / TTBR1 |
| Global page | PT entry G bit | PT entry nG bit (反向意義) |
| ASID | PCID (12 bit) | ASID (8 / 16 bit) |
| SMEP / SMAP | x86 names | PAN / UAO |
| NX bit | yes | UXN / PXN（更精細） |

整體**極為相似**。x86 與 AArch64 的分頁結構是一個世代收斂的成果，差異主要在細節（兩個 root、ASID 位數、attr 編碼）。

## 一個常見誤解

「每個 process 都有自己一份 kernel page table copy 嗎？」

**不是**。ARM 與 x86 都用同一套 trick：kernel 範圍的 PT entries **只要在 root 裡共用**就好。

- x86：每個 process 的 PML4 中、kernel 那 256 個 entries 全 process 一致（kernel 一改，所有 process 都改）
- AArch64：直接用 TTBR1，所有 process 共用，**根本不用同步**

AArch64 的設計**更乾淨**：TTBR0 換、TTBR1 不換 = process 切換、kernel 視野不變。

## 自我檢核

- [ ] 我能畫出 4-level page table 對 48-bit VA 的拆解圖
- [ ] 我能說出 4K / 16K / 64K granule 對 page table 大小、級數的影響
- [ ] 我能解釋 TTBR0 / TTBR1 為什麼 ARM 設計成兩個
- [ ] 我能說出 PT entry 中 AF、nG、AP、SH 的意義
- [ ] 我能解釋 MAIR_EL1 的 indirection 機制
- [ ] 我能列出啟用 MMU 的步驟並說明為什麼最後一條 ISB 不可省

下一章看 ASID、TLB、context switch — 為什麼 process 切換不用 invalidate 整個 TLB。

→ [Ch 17 ASID、TLB、context switch](./17-asid-tlb-context-switch.md)
