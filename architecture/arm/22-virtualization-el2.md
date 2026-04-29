# Ch 22 — 虛擬化擴展：EL2 與 stage-2 translation

> 目標：搞懂 ARM 虛擬化擴展（VHE / Stage 2 / vGIC）— hypervisor 跑在 EL2、guest 在 EL1、stage-2 page table 怎麼讓 guest 看到的 PA 變成 host 的 PA。KVM 與 Xen 怎麼用這套 ARM 硬體支持。

## 為什麼 ARM 要硬體虛擬化？

軟體虛擬化（trap-and-emulate）需要把 sensitive 指令陷出 → 模擬 → 返回。問題：

- **ARMv7 之前 ISA 不全 trappable**：有些 sensitive 指令在低 privilege 不會 trap，必須二進制翻譯
- **Page table walk 性能**：guest 有自己 page table，host 又一層，**shadow page table** 軟體實作極慢

ARMv7-A Virtualization Extensions（2010）+ ARMv8-A 改進，提供：

1. **EL2 (hypervisor)**：給 hypervisor 自己一個 privilege 層
2. **Stage-2 translation**：硬體 2-level page table walk
3. **vGIC**：硬體加速 interrupt 虛擬化
4. **vTimer**：硬體加速 timer 虛擬化
5. **Trap controls (HCR_EL2)**：精細控制哪些 op 陷到 EL2

## Stage-1 vs Stage-2

```
Guest VA ──[stage 1, controlled by guest]──→ Guest IPA (Intermediate PA)
                                              │
Guest IPA ──[stage 2, controlled by hypervisor]──→ Host PA
                                              │
                                              ▼
                                          DRAM / MMIO
```

- **Stage 1**：guest kernel 自己管，TTBR0_EL1 / TTBR1_EL1 指向 guest 的 page table
- **Stage 2**：hypervisor 管，VTTBR_EL2 指向另一份 page table，把 IPA → PA

CPU 走 stage-1 完得到 IPA → 再走 stage-2 得 PA → 拿到資料。**硬體一次 translation 走完兩級**，不需要 shadow page table。

## Stage-2 page table 格式

格式類似 stage-1（4 級、4K granule），但描述符稍不同：

```
S2 PT entry attributes 包含：
  S2AP[1:0]   stage-2 access permission
  MemAttr     memory attributes
  XN           execute never
  AF / nG / SH 與 stage-1 類似
```

注意 **S2 沒有 ASID**：因為 stage-2 是 hypervisor 對 guest 的視角，相當於整個 guest 一個「ASID」(VMID)。

## VMID：guest 的 ID

`VTTBR_EL2` 高 bit 帶 **VMID（Virtual Machine ID）**，類似 stage-1 的 ASID：

```
VTTBR_EL2:
 63          48 47               0
┌────────────┬───────────────────┐
│   VMID     │   PA of S2 L0     │
└────────────┴───────────────────┘
```

VMID 8-bit（ARMv8.0）或 16-bit（ARMv8.1+）。TLB 對應 stage-2 的 entries 帶 VMID 標記，多 guest 切換不用 flush。

## HCR_EL2：trap 控制

`HCR_EL2`（Hypervisor Configuration Register）控制「**哪些 guest 操作要 trap 到 EL2**」：

```
HCR_EL2.VM    = 1   開啟 stage-2
HCR_EL2.IMO   = 1   IRQ 路由到 EL2
HCR_EL2.FMO   = 1   FIQ 路由到 EL2
HCR_EL2.AMO   = 1   SError 路由到 EL2
HCR_EL2.TVM   = 1   trap virtual memory ops（guest 改 page table）
HCR_EL2.TGE   = 1   trap general exceptions
HCR_EL2.E2H   = 1   VHE (Virtualization Host Extension)
... 50+ bit
```

設定哪些 trap 開，hypervisor 就知道哪些事自己做、哪些放手讓 guest 做。

## VHE：Virtualization Host Extensions

ARMv8.1 加的關鍵特性。**讓 host kernel（如 Linux）直接跑在 EL2**，而不是 EL1。

```
ARMv8.0：
  Linux host kernel 在 EL1
  KVM 部分（hypervisor）在 EL2
  context switch 時 EL1 ↔ EL2 來回切，開銷大

ARMv8.1+ VHE：
  Linux host kernel 在 EL2
  KVM 進來時不用切 EL
  guest VM 跑在 EL1 / EL0
  完全消除「KVM 切換 EL」開銷
```

VHE 啟用方法：boot 時把 host kernel 從 EL3 啟動到 EL2，設 `HCR_EL2.E2H = 1`。Linux 5.x kernel 在所有支援 ARMv8.1+ 的系統都用 VHE。

**性能差異**：context switch、syscall 開銷顯著降低。AWS Graviton 2/3 跑 KVM guest 的成本比 ARMv8.0 系統低 30%+。

## vGIC：interrupt 虛擬化

GIC（Generic Interrupt Controller）給 ARM 多核做 IRQ。虛擬化版叫 **vGIC**：

- physical IRQ 進 GICv3 → routed to EL2
- hypervisor 決定要 inject 給哪個 guest
- 設定 GICv3 的 LR (List Registers)
- guest 看到的就是「自己收到 IRQ」

vGIC 硬體支持讓**多數 IRQ 不需要 hypervisor 介入**：guest 自己 handle，end-of-interrupt 也直接由 hardware 寫 GICR_*。**只在需要 inject virtual IRQ 時 hypervisor 才動**。

## vTimer：timer 虛擬化

ARM 提供 generic timer（CNTPCT_EL0、CNTV_*）。虛擬化下：

- **Physical Counter**：host 看到
- **Virtual Counter**：guest 看到（host 算 offset 給 guest）
- **CNTVOFF_EL2**：host 設 offset 讓 guest 認為時間從 0 開始

guest 寫 timer 不需要 trap，硬體自動 redirect。**guest 自己讀寫 vCounter / vTimer，零 trap**。

## KVM-arm64 的工作流程

```c
// 簡化版 KVM 內部流程
ioctl(KVM_RUN):
    // 進到 KVM kernel
    save host state
    load guest state
    set HCR_EL2 trap bits
    eret to guest (EL1)

    // guest 跑直到 trap
    // trap 到 EL2 KVM handler
    switch (esr_class):
        case HVC: // guest 主動 hypercall
            handle_hypercall()
        case Stage2 fault: // guest 存取沒 mapped IPA
            page_fault_handler()
        case ...
    eret 回 guest 或 return user space (qemu)
```

QEMU 在 user space（EL0）發 ioctl 到 kernel KVM 模組（EL2 with VHE），KVM 切到 guest 執行，trap 處理交回 KVM。

## Stage-2 fault：handle MMIO 與 paging

guest 存取 IPA 沒 mapped（hypervisor 沒給）→ stage-2 fault → trap 到 EL2。hypervisor 處理：

- **MMIO emulation**：fault address 對應一個 virtual device，hypervisor 模擬寫入
- **Lazy paging / ballooning**：hypervisor 給一塊新 host page、map 進 guest stage-2
- **Migration / live migration**：trap copy-on-write 行為

`ESR_EL2` 中的 EC = 0x24 (DataAbort 從 lower EL) 或 0x20（IFsec），ISS 給細節。

## Nested Virtualization

ARMv8.3 支援部分 nested virtualization；ARMv8.4 完整支持（NV2）。

```
L0 hypervisor（host）  EL2
  └── L1 guest hypervisor  EL2 (faked)
        └── L2 guest        EL1
```

實作上 L0 hypervisor 讓 L1 hypervisor 跑在 EL1，但「呈現 EL2」的視角。L1 改 HCR_EL2 → trap → L0 模擬。

KVM 對 ARMv8.4 的 nested 仍在 develop，2024 起逐漸 production。對 cloud（VM in VM）有用。

## 一個常見誤解

「ARM 虛擬化是不是和 x86 VT-x / AMD-V 一樣？」

概念類似（trap-and-emulate + 二級分頁），實作不同：

| | x86 VT-x | ARM virtualization |
|---|---|---|
| Privilege 層 | VMX root / non-root（覆蓋 ring 0/3） | EL2 (host) / EL1 (guest) — 正交於 EL0/1 |
| 二級分頁 | EPT (Extended Page Tables) | Stage-2 translation |
| Interrupt | APICv | vGIC |
| Timer | TSC offset | CNTVOFF_EL2 |
| Host 能否在 root mode | 是（Linux KVM kernel 在 root） | 是（VHE 開啟後 Linux 在 EL2） |

ARM 設計「**EL 層級**」的概念比 x86 「mode + ring」更乾淨，但實際差異對 hypervisor 開發者主要是 API 不同。

## 自我檢核

- [ ] 我能畫出 stage-1 與 stage-2 的兩級轉換
- [ ] 我能解釋 VMID 的目的
- [ ] 我能說明 HCR_EL2 是什麼，列出三個常用 trap bit
- [ ] 我能解釋 VHE 解決什麼問題
- [ ] 我能說出 vGIC 與 vTimer 的硬體支持給 guest 帶來什麼
- [ ] 我能描述 KVM 從 user space ioctl 到 guest 執行的流程

到這裡 Part 3 章節結束。下一個是練習 B — QEMU virt aarch64，從 EL3 一路降到 EL1，開 MMU 印 hello。

→ [練習 B：QEMU virt aarch64 從 EL3 降到 EL1 開 MMU](./practice-b-aarch64-el3-to-el1.md)
