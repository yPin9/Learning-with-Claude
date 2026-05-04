# Ch 28 — Sv39：三層頁表結構、PTE 格式、satp CSR 設定

> 目標：能手動分解 Sv39 虛擬地址；能解讀 PTE 的每個 bit；能用 C code 建立最小的 Sv39 identity mapping 並在 QEMU 上驗證。

---

## 28.1 Sv39 地址空間概觀

```
Virtual Address：39 bits 有效，高位必須是符號延伸（canonical form）
Physical Address：56 bits

地址空間大小：
  使用者：0x0000000000000000 – 0x0000003FFFFFFFFF（256 GiB）
  核心：  0xFFFFFFC000000000 – 0xFFFFFFFFFFFFFFFF（256 GiB）
```

非 canonical 地址（bit 38 和高位不一致）在 Sv39 下會產生 page fault。

---

## 28.2 VA 分解

```
Sv39 虛擬地址（64-bit 暫存器，但只有低 39 bit 有效）：

  63      39 38  30 29  21 20  12 11        0
  +--------+---------+---------+---------+-----------+
  | sext39 |  VPN[2] |  VPN[1] |  VPN[0] |  offset   |
  +--------+---------+---------+---------+-----------+
             9 bits    9 bits    9 bits    12 bits

VPN[2]：root（L2）page table 的索引，0–511
VPN[1]：L1 page table 的索引，0–511
VPN[0]：L0（leaf）page table 的索引，0–511
offset：page 內的位元組偏移，0–4095
```

分解範例（VA = 0x0000000080200ABC）：

```
0x0000000080200ABC
= 0000 0000 0000 0000 0000 0000 1000 0000 0010 0000 0000 1010 1011 1100

VPN[2] = bits [38:30] = 000000010  = 2
VPN[1] = bits [29:21] = 000000001  = 1
VPN[0] = bits [20:12] = 000000000  = 0
offset = bits [11:0]  = 0xABC
```

---

## 28.3 三層頁表走法

```
satp.PPN × 4096
      |
      v
 Root Page Table（L2，512 個 PTE）
 index = VPN[2]
      |
      +--→ L1 Page Table（512 個 PTE）
            index = VPN[1]
                  |
                  +--→ L0 Page Table / Leaf（512 個 PTE）
                        index = VPN[0]
                              |
                              +--→ PTE.PPN × 4096 + offset
                                         = Physical Address
```

每層頁表大小：512 個 PTE × 8 bytes = 4096 bytes = 1 page。

---

## 28.4 PTE 格式（64-bit）

```
  63  54 53              10 9  8  7  6  5  4  3  2  1  0
  +------+------------------+----+---+---+---+---+---+---+---+
  | Res. |    PPN [43:0]    |RSW | D | A | G | U | X | W | R | V |
  +------+------------------+----+---+---+---+---+---+---+---+---+
   10 bit      44 bits       2b  1b  1b  1b  1b  1b  1b  1b  1b

V  (bit 0):  Valid。0 = 此 PTE 無效，任何存取都 page fault
R  (bit 1):  Read。1 = 此 page 可讀
W  (bit 2):  Write。1 = 此 page 可寫
X  (bit 3):  eXecute。1 = 此 page 可執行
U  (bit 4):  User。1 = U-mode 可存取；0 = 只有 S-mode 可存取
G  (bit 5):  Global。1 = 此 mapping 在所有 ASID 都有效（不因 TLB flush 消失）
A  (bit 6):  Accessed。硬體在存取此 page 時設為 1（用於 LRU/swap）
D  (bit 7):  Dirty。硬體在寫入此 page 時設為 1
RSW(9:8):    Reserved for Software（OS 可以自由使用）
PPN(53:10):  Physical Page Number（44 bits）→ PA = PPN × 4096

Res.(63:54): 保留，必須為 0
```

**Leaf vs Non-leaf PTE 的判別**：

```
if (R == 0 && W == 0 && X == 0):
    non-leaf PTE → PPN 指向下一層頁表
else:
    leaf PTE → PPN 指向實際的 data page
```

注意：W=1 但 R=0 是保留的（不合法，hardware raise 頁表格式錯誤）。

---

## 28.5 大頁（Superpage）

Sv39 支援兩種大頁：

```
Megapage（2 MiB）：在 L1（第 2 層）放 leaf PTE
  VA 的低 21 bits（VPN[0] + offset）都是 PA 的 offset
  PTE.PPN[0] 必須是 0（對齊到 2 MiB）

Gigapage（1 GiB）：在 L2（root，第 3 層）放 leaf PTE
  VA 的低 30 bits（VPN[1] + VPN[0] + offset）都是 PA 的 offset
  PTE.PPN[1:0] 必須是 0（對齊到 1 GiB）
```

大頁的 PTE 格式相同，只是在非葉節點層放了一個 leaf PTE（R/W/X 非全 0）。

---

## 28.6 最小 Sv39 頁表的 C 實作（baremetal QEMU）

以下 code 建立一個 identity mapping（VA = PA）：把 0x80200000 這個 page 映射到自己。

```c
// sv39_minimal.c
#include <stdint.h>

// 靜態分配 3 個 page（各 4096 bytes，8-byte aligned）
static uint64_t root_pt[512]  __attribute__((aligned(4096)));
static uint64_t l1_pt[512]    __attribute__((aligned(4096)));
static uint64_t l0_pt[512]    __attribute__((aligned(4096)));

#define PTE_V (1ULL << 0)
#define PTE_R (1ULL << 1)
#define PTE_W (1ULL << 2)
#define PTE_X (1ULL << 3)
#define PTE_U (1ULL << 4)
#define PTE_G (1ULL << 5)
#define PTE_A (1ULL << 6)
#define PTE_D (1ULL << 7)

// PPN 放在 bit [53:10]，因此 PA 要右移 12 再左移 10
#define PA_TO_PPN(pa)  (((uint64_t)(pa) >> 12) << 10)

void setup_sv39(void) {
    uint64_t va = 0x80200000UL;   // 想要 map 的 VA（等於 PA）

    // 分解 VA
    uint64_t vpn2 = (va >> 30) & 0x1FF;   // = 2（因為 0x80200000 >> 30 = 2）
    uint64_t vpn1 = (va >> 21) & 0x1FF;   // = 1
    uint64_t vpn0 = (va >> 12) & 0x1FF;   // = 0

    // L2（root）的 entry 指向 l1_pt
    uint64_t l1_pa = (uint64_t)l1_pt;
    root_pt[vpn2] = PA_TO_PPN(l1_pa) | PTE_V;  // non-leaf：R=W=X=0

    // L1 的 entry 指向 l0_pt
    uint64_t l0_pa = (uint64_t)l0_pt;
    l1_pt[vpn1] = PA_TO_PPN(l0_pa) | PTE_V;    // non-leaf

    // L0（leaf）的 entry：VA 0x80200000 → PA 0x80200000
    uint64_t page_pa = 0x80200000UL;
    l0_pt[vpn0] = PA_TO_PPN(page_pa) | PTE_V | PTE_R | PTE_W | PTE_X
                  | PTE_A | PTE_D;

    // 設定 satp：MODE=Sv39(9), ASID=0, PPN=root_pt 的物理地址
    uint64_t root_pa = (uint64_t)root_pt;
    uint64_t satp = (9ULL << 60) | (root_pa >> 12);

    // 寫入 satp 並刷 TLB
    __asm__ volatile (
        "csrw satp, %0\n\t"
        "sfence.vma zero, zero\n\t"
        :: "r"(satp) : "memory"
    );
}
```

**重要細節**：

1. `PA_TO_PPN` 把物理地址右移 12（去掉 page offset），再左移 10（放到 PTE 的 PPN 欄位起始位元）。
2. Non-leaf PTE 的 R=W=X=0，只有 V=1。
3. Leaf PTE 需要設定 A 和 D bit，否則某些硬體實作會在第一次存取時 page fault（讓 OS 來設定），QEMU 通常不需要，但設了保險。
4. 這個 baremetal code 假設程式本身載入在 identity-mapped 的範圍內，否則 csrw satp 後下一條指令就 page fault 了。

---

## 28.7 Page Table Walk 的完整驗證步驟

```
1. 讀 satp，取得 MODE 和 root PPN
2. root_pa = satp.PPN × 4096
3. 讀 root_pa[VPN[2] × 8]（root_pt[vpn2]）→ 得到 l1_pte
4. 確認 l1_pte.V == 1，且 l1_pte 是 non-leaf（R=W=X=0）
5. l1_pa = l1_pte.PPN × 4096
6. 讀 l1_pa[VPN[1] × 8] → l0_pte
7. 確認 l0_pte.V == 1，且是 non-leaf
8. l0_pa = l0_pte.PPN × 4096
9. 讀 l0_pa[VPN[0] × 8] → leaf_pte
10. 確認 leaf_pte.V == 1，且是 leaf（R/W/X 至少一個是 1）
11. pa = leaf_pte.PPN × 4096 + va.offset
```

---

## 自我檢核

- [ ] 能手動分解 0x0000000081000ABC 的 VPN[2], VPN[1], VPN[0], offset
- [ ] 能說出 Sv39 non-leaf PTE 和 leaf PTE 的判別方式（R/W/X 是否全為 0）
- [ ] 知道 PTE 的 PPN 欄位從第幾個 bit 開始（bit 10）
- [ ] 能說出 Megapage 和 Gigapage 的頁面大小
- [ ] 能從 C code 中指出 `PA_TO_PPN` 為什麼右移 12 再左移 10

→ [Ch 29 — Sv48 / Sv57](29-sv48-sv57.md)
