# Ch 30 — TLB 管理：SFENCE.VMA、TLB Shootdown、ASID

> 目標：精確理解 `sfence.vma` 四種形式的語意；知道什麼操作之後必須刷 TLB；理解 ASID 如何減少 context switch 的開銷。

---

## 30.1 TLB 的作用與結構

TLB（Translation Lookaside Buffer，快速位址翻譯緩衝）是頁表翻譯的快取。每個 TLB entry 通常包含：

```
TLB entry：
  VA（虛擬頁號）
  ASID
  PPN（對應的物理頁號）
  Permission bits（R/W/X/U）
  G（Global flag）
```

TLB 是 CPU-private 的——每個 hart（hardware thread）有自己的 TLB。TLB 的命中/未命中對程式是透明的，但對效能影響顯著。

---

## 30.2 SFENCE.VMA 語意

```
sfence.vma rs1, rs2
```

這條指令保證：之前對頁表的所有寫入，對後續的頁表 walk 可見。同時刷新對應的 TLB entries。

四種形式：

| rs1   | rs2   | 作用                                          |
|-------|-------|---------------------------------------------|
| x0    | x0    | 刷新所有 TLB entries（全局刷新，最重）           |
| rs1≠x0| x0    | 只刷新 VA = rs1 的 TLB entry（所有 ASID）       |
| x0    | rs2≠x0| 只刷新 ASID = rs2 的所有 TLB entries           |
| rs1≠x0| rs2≠x0| 只刷新 (VA=rs1, ASID=rs2) 的 TLB entry        |

**精確語意**：rs1 裡放的是一個虛擬地址（不是 VPN，是完整 VA，硬體取 [VALEN-1:12] 部分）。rs2 裡放的是 ASID 值。

---

## 30.3 什麼操作後必須 sfence.vma

規則：任何修改了頁表的操作，在硬體正確看到新 mapping 之前，必須執行 `sfence.vma`。

```
操作                              需要的 sfence.vma
-----                             -------------------
修改 PTE（寫入頁表）               至少 sfence.vma rs1=VA, rs2=ASID
建立新的 mapping（新 PTE V=0→1）   sfence.vma（或全刷）
撤銷 mapping（PTE V=1→0）          sfence.vma，VA 的 TLB entry
修改 satp（換頁表 root）           sfence.vma zero, zero（全刷）
context switch（換 ASID）          見 30.4
```

不需要 sfence.vma 的操作：讀 PTE（只讀，不改）。

---

## 30.4 ASID：Address Space Identifier

Context switch 時，下一個 process 的頁表和 ASID 不同。沒有 ASID 的話，每次 context switch 都要 `sfence.vma zero, zero`（全刷），把前一個 process 的所有 TLB entries 清掉。

**ASID 的作用**：每個 process 有自己的 ASID。TLB entry 標記 ASID，只有當前 ASID 的 entry 才會被使用。換 process 時只需要切換 satp.ASID，不刷 TLB，前一個 process 的 entry 留在 TLB 裡（下次換回來時直接用）。

```
satp 的 ASID 欄位（bits [59:44]，16 bits）
  理論上支援 0–65535 個不同 ASID
  實際硬體的 ASID 寬度由 sstateen0 或直接寫 satp 後讀回確認
```

**Global mapping**：PTE 的 G bit = 1 的 mapping 在所有 ASID 下都有效。Kernel 的頁表通常把 kernel space 設為 Global，這樣 context switch 不用刷 kernel 的 TLB entries。

---

## 30.5 Linux 的 ASID 管理

Linux 的 ASID 分配（arch/riscv/mm/context.c）：

```
每個 process 有一個 mm->context.id（包含 ASID 和 generation number）
ASID 有限（例如只有 16-bit），用完了要做 ASID rollover：
  1. 遞增 generation counter
  2. 全刷 TLB（sfence.vma zero, zero）
  3. 從 0 開始重新分配 ASID
```

Context switch 時的操作（簡化）：

```c
void switch_mm(struct mm_struct *next_mm) {
    unsigned long asid = get_or_assign_asid(next_mm);
    // 設定新的 satp（包含新的 PPN 和 ASID）
    csr_write(CSR_SATP, (MODE_SV48 << 60) | (asid << 44) | (pgd_pfn(next_mm->pgd)));
    // 如果 ASID 是舊的（同一 generation），不需要 sfence.vma
    // 如果是新分配的，已經在全刷 TLB 時清掉了
}
```

---

## 30.6 TLB Shootdown in SMP

在多核（SMP）系統上，每個 hart 有獨立的 TLB。當 hart 0 修改了一個 mapping，hart 1 的 TLB 裡可能還快取著舊的翻譯。

這個問題叫做 **TLB shootdown**：hart 0 需要「射殺」其他 hart 的 TLB entries。

```
RISC-V 的 sfence.vma 只影響當前 hart 的 TLB。
  要刷其他 hart 的 TLB，只能通過 IPI（Inter-Processor Interrupt）。
```

Linux 的 TLB shootdown 流程：

```
1. hart 0 修改 PTE
2. hart 0 發送 IPI 給所有相關 hart（sbi_remote_sfence_vma）
3. 每個收到 IPI 的 hart 執行 sfence.vma
4. hart 0 等待所有 IPI 回應
5. 繼續
```

SBI（Supervisor Binary Interface）提供的 RFENCE extension：

```
sbi_remote_sfence_vma(hart_mask, start_va, size)
  → 讓指定的 hart 集合執行 sfence.vma
```

---

## 30.7 Linux 的 flush_tlb_* 對照

| Linux 函式                  | RISC-V 實作                            |
|---------------------------|---------------------------------------|
| `flush_tlb_all()`         | `sfence.vma zero, zero`（本 hart）+ IPI |
| `flush_tlb_mm(mm)`        | `sfence.vma zero, asid`               |
| `flush_tlb_page(vma, va)` | `sfence.vma va, asid`                 |
| `flush_tlb_range(...)`    | 逐頁 `sfence.vma va, asid` 或全刷       |
| `flush_tlb_kernel_range`  | `sfence.vma zero, zero`（kernel space）|

---

## 30.8 常見 Bug：忘了 sfence.vma

```c
// 修改 PTE
l0_pt[vpn0] = new_pte;

// 忘了 sfence.vma！
// 接下來的存取可能還用舊的 TLB 快取，不是新的 PTE
uint64_t *va_ptr = (uint64_t *)va;
*va_ptr = 42;   // 可能寫到錯誤的物理地址
```

這個 bug 在 QEMU 上常常不會出現（QEMU 的 TLB 模擬通常比較寬鬆），在真實硬體上才爆。Debug 時如果只在真實硬體復現、QEMU 沒事，第一個懷疑就是缺少 sfence.vma。

另一個常見的忘記地點：在 fork/exec 換頁表時，只換了 satp 但沒有 sfence.vma，導致新 process 的前幾次存取用了舊的翻譯。

---

## 自我檢核

- [ ] 能說出 `sfence.vma zero, zero` 和 `sfence.vma rs1, rs2` 的差別
- [ ] 知道為什麼 context switch 時用 ASID 可以不需要 sfence.vma
- [ ] 能說出 TLB shootdown 的流程（修改 PTE → IPI → 遠端 hart sfence.vma）
- [ ] 知道 G bit（Global）的作用
- [ ] 能說出哪種 bug 在 QEMU 不出現但真實硬體爆（缺 sfence.vma）

→ [Ch 31 — Page Fault 處理](31-page-fault.md)
