# Ch 7 — 系統指令與例外進入

> 目標：理解 ARM 的系統指令族 — `SVC` `HVC` `SMC` `BRK` `UDF` `WFI` `WFE` — 以及它們和例外（exception）系統的關係。這章是把 Part 1（共通 ISA）銜接到 Part 2/3（M 與 A 的處理器模型）的橋。

## 例外（exception）是什麼

ARM 的「**exception**」涵蓋的範圍比 x86 廣，包含：

- **Reset**（上電 / 重置）
- **Interrupt**（外部中斷：IRQ、FIQ、NMI）
- **Synchronous exception**（同步例外，由指令觸發）
  - `SVC` / `HVC` / `SMC` — 主動陷入（system call / hypervisor call / secure call）
  - undefined instruction、prefetch abort、data abort、alignment fault
  - `BRK` debug exception
- **SError**（系統錯誤，非同步、通常是 bus error）

x86 的「interrupt」、「exception」、「trap」、「fault」、「abort」是 ARM 的「exception」一個傘下分類。

## 主動陷入指令：SVC / HVC / SMC

```asm
svc  #0          ; Supervisor Call (system call)
hvc  #0          ; Hypervisor Call
smc  #0          ; Secure Monitor Call
```

立即數 `#0` 是「呼叫號」，只是給 handler 區分用，**對 CPU 行為沒影響**（CPU 不解析這個數字，handler 自己讀回看）。

各自陷到不同 EL：

```
EL0 (應用)  ──svc #0─→  EL1 (kernel handler，VBAR_EL1)
EL1 (kernel)──hvc #0─→  EL2 (hypervisor，VBAR_EL2)
任何 EL    ──smc #0─→  EL3 (secure monitor，VBAR_EL3)
```

幾個重點：

- **EL0 不能用 SMC**（不能直接呼叫 secure world，要透過 kernel）
- **HVC 在沒有 hypervisor 的系統會 trap 到 EL2 / EL3 的 handler**，通常 boot loader 會處理
- **SVC 是「常規 system call」**：Linux 在 AArch64 用 `svc #0` 取代 AArch32 的 `swi`

舊 AArch32 / Cortex-M：`SWI`（SoftWare Interrupt）是 `SVC` 的舊名。現在統一叫 SVC。

## 系統呼叫怎麼走（Linux 範例）

```c
write(1, "hi\n", 3);
```

AArch64 Linux user-space：

```asm
mov  x8, #64        ; syscall number (write)
mov  x0, #1         ; fd
adr  x1, str        ; buf
mov  x2, #3         ; count
svc  #0             ; trap into kernel
                    ; 回來時 x0 = retval
```

差別於 x86_64 的 `syscall` 指令：x86_64 把 syscall number 放 RAX；AArch64 放 X8（不是 X0，因為 X0 用於回傳，需保留）。

## BRK：debug 用的 trap

```asm
brk  #0xABCD       ; 陷入 debug monitor 或除錯器
```

對 debugger 是「軟體斷點」(Ch 27 會展開)。對沒接 debugger 的系統會觸發 debug exception，handler 在 VBAR。

GDB 軟體斷點的實作：把目標位址的指令暫時換成 `BRK #1`（或 Cortex-M 的 `BKPT`），CPU 執行到這就 trap，GDB 拿回控制權。

## UDF：故意撞牆

```asm
udf  #0            ; Undefined instruction，CPU 必觸發例外
```

UDF (Undefined) 是「保證觸發 undefined instruction exception」的指令。用途：

- **assertion failure**：`__builtin_trap()` 在 ARM 通常編成 UDF
- **unreachable**：編譯器在 known unreachable 點放 UDF 防止 fall-through
- **故意 trap 給 handler**：和 BRK 類似但走 undefined 路線

Cortex-M 沒有 UDF，但有相同效果的指令（特定保留 encoding）。

## WFI / WFE：等中斷或事件

```asm
wfi              ; Wait For Interrupt — 進入低功耗，等中斷喚醒
wfe              ; Wait For Event — 等 SEV (send event) 或中斷
sev              ; Send Event — 喚醒同個 cluster 的 WFE
```

兩者都讓 CPU 進入低功耗 idle，差別：

- `WFI` 只能被中斷喚醒
- `WFE` 可以被中斷 OR `SEV` 喚醒，後者用於核間同步（**spinlock 等待時用 WFE 比死轉省電**）

Cortex-A 的 idle loop 大致：

```c
while (1) {
    asm volatile("wfi");   // 等中斷
}
```

Cortex-M 也支援，但通常加 `__WFI()` macro。

## 例外進入：當例外發生 CPU 做什麼

以 AArch64 為例，當任何 exception 發生：

```
1. 把 PSTATE 存到 SPSR_ELx
2. 把 PC 存到 ELR_ELx
3. 把 ESR_ELx 寫入例外原因
4. 切換到目標 EL
5. PC = VBAR_ELx + offset
6. 用 SP_ELx（不再用 SP_EL0）
7. 遮蔽 IRQ/FIQ/SError（DAIF 全設）
```

`VBAR_ELx`（Vector Base Address Register）是個指向 **2KB 對齊的向量表**：

```
VBAR_ELx + 0x000   Synchronous exception (current EL, SP_EL0)
VBAR_ELx + 0x080   IRQ
VBAR_ELx + 0x100   FIQ
VBAR_ELx + 0x180   SError

VBAR_ELx + 0x200   Synchronous (current EL, SP_ELx)
VBAR_ELx + 0x280   IRQ
... 共 16 種 entry，每種佔 0x80 bytes (32 條指令)
```

每個 entry 只給 32 條指令，所以實際 handler 要先在那 32 條指令內跳到 C handler。

## ESR_ELx：例外原因

`ESR_ELx`（Exception Syndrome Register）給 handler 看「發生什麼事」：

```
ESR_ELx[31:26]  EC (Exception Class)：例外類別
ESR_ELx[25]     IL：是否 32-bit 指令
ESR_ELx[24:0]   ISS：類別相關 syndrome
```

EC 列舉（不完整）：

| EC | 例外類別 |
|---|---|
| 0x00 | Unknown |
| 0x15 | SVC from AArch64 |
| 0x16 | HVC from AArch64 |
| 0x17 | SMC from AArch64 |
| 0x21 | Instruction abort current EL |
| 0x25 | Data abort current EL |
| 0x3C | BRK in AArch64 |

handler 第一步通常讀 ESR、`switch (EC)` 分發到具體 handler。

## 從 handler 返回：ERET

```asm
eret             ; Exception Return: PC = ELR; PSTATE = SPSR
```

`ERET` 把 ELR_ELx 載回 PC、SPSR_ELx 載回 PSTATE，**等於 atomic 還原**例外發生前的狀態。

x86 對應的是 `IRET`。但 ERET 簡單很多，因為 ARM 把保存內容拆給 ELR / SPSR / SP_ELx 三個 register，不像 x86 把所有東西塞到 stack。

## Cortex-M 的不同：例外是 IRQ-like

Cortex-M **完全沒有 EL 概念**，只有 Thread mode / Handler mode。例外處理由 **NVIC + 自動 hardware stacking** 完成：

```
Exception happens →
  CPU 自動把 R0-R3, R12, LR, PC, xPSR 八個 reg push 到當前 SP →
  load NVIC.VTOR + (irq_number * 4) 為 PC →
  進 Handler mode

Handler return（用 LR 的特殊 EXC_RETURN 值）→
  Hardware unstack →
  返回原來的執行緒
```

所以 Cortex-M 的「IRQ handler」**可以直接用普通 C 函式寫**（編譯器自動加 unstacking 邏輯），不需要 naked attribute。Ch 9 / 12 會展開。

這是 A 與 M 在例外處理上的最大差異：**A 是手動（HW 只動 ELR/SPSR/PC，其他靠軟體）**，**M 是自動（HW push 一堆 reg）**。設計目標不同：A 是性能與多樣性（kernel 寫 handler 自己決定 spill 什麼），M 是即時性與簡單（IRQ latency 可預測）。

## 一個常見誤解

「SVC 是不是和 x86 syscall 一樣？」

行為上是。但 ARM 的 `SVC` 編碼可以帶 16-bit 立即數（`svc #0xCAFE`），早期確實有 OS 用這個立即數區分 syscall。**Linux 不用**：Linux 永遠 `svc #0`，syscall number 在 X8。立即數其他 OS（古早 RISC OS / 某些 RTOS）可能會用。

## 自我檢核

- [ ] 我能說出 SVC / HVC / SMC 各陷到哪個 EL
- [ ] 我能列舉 AArch64 同步例外的常見類別
- [ ] 我能解釋 VBAR 與 ELR / SPSR / ESR 的角色
- [ ] 我能說明為什麼 Cortex-A 例外向量每個 entry 只有 32 條指令
- [ ] 我能解釋 WFI 與 WFE 的差別
- [ ] 我能對比 Cortex-A 的「軟體 stacking」與 Cortex-M 的「硬體 stacking」差別

到這裡 Part 1（共通 ISA 基礎）告一段落。下一章開始 Part 2，我們進 Cortex-M 的世界 — 從處理器模型（Thread/Handler、MSP/PSP）開始。

→ [Ch 8 Cortex-M 處理器模型：Thread/Handler、MSP/PSP](./08-cortex-m-processor-model.md)
