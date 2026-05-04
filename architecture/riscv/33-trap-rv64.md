# Ch 33 — Trap 完整流程（RV64 視角）：User → S-mode → M-mode Delegation 鏈

> 目標：能畫出完整的三層 trap delegation 流程；理解 medeleg/mideleg 的作用；能寫一個保存 64-bit context 的 S-mode trap entry。

---

## 33.1 RISC-V Trap 的四種類型

```
類型            觸發方式                   典型用途
-------         ---------                  ---------
Exception       指令執行時的同步事件        page fault, illegal inst
Interrupt       非同步，外部事件           timer, external interrupt
ecall           執行 ecall 指令            系統呼叫（U→S），SBI call（S→M）
ebreak          執行 ebreak 指令           debugger breakpoint
```

RISC-V 把 exception 和 interrupt 都叫做 trap，用 scause/mcause 的最高 bit 區分：

```
cause 最高 bit = 1：interrupt（非同步）
cause 最高 bit = 0：exception（同步）

RV64：cause 是 64-bit，最高 bit = bit 63
RV32：cause 是 32-bit，最高 bit = bit 31
```

---

## 33.2 Trap Delegation 機制

預設情況下，所有 trap 都進入 M-mode 處理（M-mode 是最高特權等級，捕獲一切）。

但跑 Linux 時，我們希望 S-mode 直接處理大多數 trap（page fault、syscall），不用每次都進 M-mode。這就是 **trap delegation**。

```
medeleg（Machine Exception Delegation Register）：
  bit N = 1 → exception cause N 委派給 S-mode 處理
  bit N = 0 → exception cause N 在 M-mode 處理（預設）

mideleg（Machine Interrupt Delegation Register）：
  bit N = 1 → interrupt cause N 委派給 S-mode
  bit N = 0 → interrupt cause N 在 M-mode 處理（預設）
```

典型的 Linux 初始化（由 OpenSBI 設定）：

```
medeleg 設為：
  bit 8  = 1  (U-mode ecall → S-mode)
  bit 12 = 1  (Instruction page fault → S-mode)
  bit 13 = 1  (Load page fault → S-mode)
  bit 15 = 1  (Store page fault → S-mode)
  bit 1  = 1  (Instruction access fault → S-mode)
  ...等

mideleg 設為：
  bit 1  = 1  (S-mode software interrupt → S-mode)
  bit 5  = 1  (S-mode timer interrupt → S-mode)
  bit 9  = 1  (S-mode external interrupt → S-mode)
```

**重要規則**：委派只能向下，不能向上。M-mode 可以把 trap 委派給 S-mode，但 S-mode 不能委派給 U-mode（U-mode 沒有 trap vector）。

---

## 33.3 三層 Trap 完整流程圖

```
U-mode 程式
  |
  | 觸發 exception/interrupt
  v
+------------------------------------------+
| 查 medeleg/mideleg                       |
|   若委派給 S-mode：                       |
|     sepc = 觸發指令 PC（或下一條）         |
|     scause = cause code                  |
|     stval = fault VA 或其他 aux info      |
|     sstatus.SPP = U（原 mode）            |
|     sstatus.SPIE = sstatus.SIE           |
|     sstatus.SIE = 0（關 S-mode interrupt）|
|     PC = stvec                           |
|                                          |
|   若不委派（M-mode 處理）：               |
|     mepc/mcause/mtval/mstatus 對應設定   |
|     PC = mtvec                           |
+------------------------------------------+
       |
       v（假設委派給 S-mode）
S-mode trap handler（stvec 指向的地址）
  |
  | 保存 64-bit context（所有暫存器）
  | 讀 scause，dispatch 到對應 handler
  |
  +--→ page fault handler
  +--→ syscall handler（ecall from U-mode）
  +--→ interrupt handler
  |
  | 部分情況需要進入 M-mode：
  | （S-mode 執行 ecall → M-mode）
  |   mepc = S-mode ecall 的 PC
  |   mcause = 9（ecall from S-mode）
  |   PC = mtvec
  v
M-mode handler（OpenSBI）
  |
  | 處理 SBI call（timer setup, IPI, etc.）
  | mret 回到 S-mode
  v
S-mode handler 繼續
  |
  | sret：回到 U-mode（PC = sepc，mode = sstatus.SPP）
  v
U-mode 程式繼續
```

---

## 33.4 64-bit Trap Frame Layout

保存所有 64-bit 暫存器到 kernel stack（每個 reg 8 bytes）：

```
struct pt_regs {
    uint64_t epc;     // sepc（saved PC）
    uint64_t ra;      // x1
    uint64_t sp;      // x2
    uint64_t gp;      // x3
    uint64_t tp;      // x4
    uint64_t t0;      // x5
    uint64_t t1;      // x6
    uint64_t t2;      // x7
    uint64_t s0;      // x8 / fp
    uint64_t s1;      // x9
    uint64_t a0;      // x10（syscall arg 1 / return value）
    uint64_t a1;      // x11
    uint64_t a2;      // x12
    uint64_t a3;      // x13
    uint64_t a4;      // x14
    uint64_t a5;      // x15
    uint64_t a6;      // x16
    uint64_t a7;      // x17（syscall number）
    uint64_t s2;      // x18
    ...
    uint64_t s11;     // x27
    uint64_t t3;      // x28
    uint64_t t4;      // x29
    uint64_t t5;      // x30
    uint64_t t6;      // x31
    uint64_t status;  // sstatus
    uint64_t cause;   // scause
    uint64_t tval;    // stval
    uint64_t orig_a0; // 原始 a0（syscall restart 用）
};
// 總大小 = 36 × 8 = 288 bytes
```

---

## 33.5 S-mode Trap Entry Assembly

Linux arch/riscv/kernel/entry.S 的簡化版：

```asm
.globl _trap_entry
_trap_entry:
    # 此時還在用 user stack（sp 是 user 的 sp）
    # 先換到 kernel stack（用 sscratch swap）
    csrrw  sp, sscratch, sp     # sp ↔ sscratch
                                # 現在 sp = kernel stack top
                                # sscratch = 原來的 user sp

    # 在 kernel stack 上分配 trap frame
    addi   sp, sp, -(36*8)      # 36 個 64-bit 暫存器

    # 保存所有通用暫存器
    sd     x1,   1*8(sp)    # ra
    sd     x3,   3*8(sp)    # gp
    sd     x4,   4*8(sp)    # tp
    sd     x5,   5*8(sp)    # t0
    # ... 省略其他暫存器
    sd     x31, 31*8(sp)    # t6

    # 把 user 的 sp 從 sscratch 取回來，保存到 trap frame
    csrr   t0, sscratch
    sd     t0, 2*8(sp)      # 保存 user sp

    # 保存 CSR
    csrr   t0, sepc
    sd     t0, 0*8(sp)      # epc
    csrr   t0, sstatus
    sd     t0, 32*8(sp)
    csrr   t0, scause
    sd     t0, 33*8(sp)
    csrr   t0, stval
    sd     t0, 34*8(sp)

    # 呼叫 C 語言的 trap handler
    mv     a0, sp            # 第一個參數：pt_regs 指標
    call   do_trap

    # 恢復暫存器...（省略）
    sret
```

---

## 33.6 Interrupt Delivery：PLIC

外部中斷（External Interrupt）由 PLIC（Platform Level Interrupt Controller）管理：

```
外部設備（如 UART, PCIe）
  |
  | IRQ signal
  v
PLIC（Platform Level Interrupt Controller）
  |
  | 仲裁、優先級
  v
PLIC → 向對應的 hart 發送 external interrupt
  |
  | 若委派給 S-mode（mideleg.SEI=1）
  v
S-mode external interrupt（scause = 0x8000000000000009）
  |
  v
trap handler 讀 PLIC claim register
  → 確定是哪個 IRQ
  → 呼叫對應的 device driver interrupt handler
  → 寫 PLIC complete register
```

PLIC 的 claim/complete 機制在 Ch 36 的實驗環境中可以直接觀察（`/proc/interrupts`）。

---

## 33.7 sstatus.SIE 與 Nested Interrupt

進入 S-mode trap handler 時，硬體自動把 `sstatus.SIE` 清為 0，防止 interrupt 嵌套。

如果 kernel 決定允許中斷嵌套（例如在慢速的 I/O 等待中），可以手動把 `sstatus.SIE` 設回 1：

```c
// Linux 的 local_irq_enable()
static inline void local_irq_enable(void) {
    csr_set(sstatus, SR_SIE);  // SR_SIE = 1 << 1
}

// local_irq_disable()
static inline void local_irq_disable(void) {
    csr_clear(sstatus, SR_SIE);
}
```

在 nested interrupt 中，進入 handler 之前硬體會再把 SPIE/SPP 更新，但 sepc/scause/stval 會被覆蓋。所以 trap handler 的 entry code 必須把這些 CSR 馬上保存到 stack，然後才能 re-enable interrupt。

---

## 自我檢核

- [ ] 能說出 medeleg/mideleg 各控制什麼（exception vs interrupt delegation）
- [ ] 能從 scause 的最高 bit 判斷是 interrupt 還是 exception
- [ ] 知道進入 S-mode trap 時硬體自動設定哪些 CSR（sepc、scause、stval、sstatus）
- [ ] 能說出為什麼 trap entry assembly 要保存 sscratch 裡的值（user sp）
- [ ] 知道 sstatus.SIE 在進入 trap 時被硬體清為 0 的原因

→ [Ch 34 — OpenSBI → Linux 啟動流程](34-opensbi-linux-boot.md)
