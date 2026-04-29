# Ch 15 — A profile 處理器模型：Exception Level 0–3

> 目標：搞懂 Cortex-A 的 Exception Level（EL0/1/2/3）— 它們各自做什麼、能幹嘛、不能幹嘛、怎麼切換。這是進 Part 3 所有後面章節的地基，沒搞清楚 EL，看 MMU、TrustZone、Virtualization 都會迷路。

## 從 mode 到 EL：架構升級

```
ARMv7-A 用 mode：
   USR / FIQ / IRQ / SVC / ABT / UND / SYS / HYP / MON
   9 種模式，靠 CPSR.M 切換。
   觀念混亂，特權與用途纏在一起。

ARMv8-A 重整成 4 個 Exception Level：
   EL0  最低權，user space
   EL1  kernel
   EL2  hypervisor
   EL3  secure monitor / firmware
```

**EL 數字越大越特權**，但 EL 不是必選 — EL2、EL3 都可選實作。沒有 hypervisor 的 SoC 可能只有 EL0/EL1（罕見）；多數現代 SoC 全 4 個都有。

## 各 EL 跑什麼

```
┌──────────────────────────────────────────────┐
│ EL0  Linux app, Android 應用, JVM, ...         │ user
├──────────────────────────────────────────────┤
│ EL1  Linux kernel, KVM guest kernel, RTOS    │ kernel
├──────────────────────────────────────────────┤
│ EL2  Hypervisor (KVM host, Xen, Hyper-V)     │ hypervisor
├──────────────────────────────────────────────┤
│ EL3  Secure Monitor: ARM Trusted Firmware-A  │ firmware
│      OP-TEE 在 secure EL1 / secure EL0       │
└──────────────────────────────────────────────┘
```

**EL3 = ARM 的「ring -1」** — 最先執行的 firmware（Boot ROM 之後馬上是 EL3 firmware）。Linux 從沒在 EL3 跑，只有 ARM Trusted Firmware-A 之類 firmware 在那。

## 切 EL 的方向：只能升不能降（自由）

進入更高 EL（更特權）**只能透過 exception**：

```
EL0 ──svc──→ EL1
EL1 ──hvc──→ EL2
任意 ──smc──→ EL3
任意例外 → 升 EL（看 routing 配置）
```

從更高 EL 回到較低 EL **只能透過 ERET**：

```
EL3 ──eret──→ 降到 SPSR_EL3 指定的 EL
EL2 ──eret──→ 降到 SPSR_EL2 指定的 EL
EL1 ──eret──→ 降到 SPSR_EL1 指定的 EL
```

**ERET 不是「降一級」**，是「**回到例外發生前的 EL**」。如果 SPSR_EL2 顯示「來自 EL0」，ERET 從 EL2 回 EL0；如果是「來自 EL1」就回 EL1。

## Boot 流程：從 EL3 一路降下來

典型 ARMv8-A 系統開機：

```
1. Boot ROM (EL3)
   └→ load BL1 (ARM TF-A first-stage)
2. BL1 (EL3)
   └→ load BL2 (TF-A platform init)
3. BL2 (EL1)
   └→ load BL31 (Secure Monitor) + BL33 (U-Boot)
4. BL31 (EL3) 常駐 — 處理 SMC call
5. U-Boot / EDK2 (EL2 or EL1)
   └→ load Linux kernel
6. Linux kernel (EL1)
   └→ load init (EL0)
```

每一步都用 ERET 把控制交給下一個 EL 的程式，自己「下車」。**多數 firmware 故事在這條 boot chain 上展開**。

## EL 與 system register

每個 EL 有自己的 system register 套組：

```
SCTLR_EL0 ─ 不存在（EL0 不直接配 SCTLR）
SCTLR_EL1   給 EL1 / EL0 共用
SCTLR_EL2   給 EL2 用
SCTLR_EL3   給 EL3 用

VBAR_EL1, VBAR_EL2, VBAR_EL3   各自向量表基底
TTBR0_EL1, TTBR1_EL1   EL1 / EL0 共用 page table
TTBR0_EL2              EL2 自己一份
TTBR0_EL3              EL3 自己一份
```

從 EL0 看不到 SCTLR_EL2、TTBR0_EL3 — 直接讀會 trap。從 EL2 可以讀寫 SCTLR_EL1 (有 routing 條件)，反過來不行。

**權限規則**：高 EL 可以「上下其手」管低 EL，反向不行。Hypervisor 從 EL2 改 guest kernel 在 EL1 看到的 register 完全合法。

## Stack Pointer：每 EL 一個

```
SP_EL0    給 EL0 用
SP_EL1    給 EL1 用
SP_EL2    給 EL2 用
SP_EL3    給 EL3 用

PSTATE.SP 選擇位元：在 EL1+ 可以選用 SP_EL0 還是 SP_ELx
```

進入 ELx 時 **CPU 自動切到 SP_ELx**，不用 OS 自己管。但 EL1 跑 task 時很多 OS 用 SP_EL0 給 user task、SP_EL1 給 kernel 中斷時切過去 — 這是 SP 選擇位元的用途。

對比 Cortex-M 的 MSP/PSP 兩個 SP，EL 模型有 4 個 SP 但概念類似。

## 例外 routing：誰處理誰

當 EL0 發生 IRQ，要去 EL1、EL2 還是 EL3？這個由 **routing register** 決定：

```
SCR_EL3.IRQ   1 = IRQ 路由到 EL3
HCR_EL2.IMO   1 = IRQ 路由到 EL2

如果都不 set，IRQ 留在 EL1 處理
```

實務組合：

- **裸機系統**：EL3 routing 全開，EL3 firmware 處理一切
- **Linux on Cortex-A**（無 hypervisor）：EL3 firmware 設好就 ERET 到 EL1，IRQ 留 EL1，TF-A 只處理 SMC
- **KVM host**：EL2 跑 KVM hypervisor，IRQ 路由到 EL2，KVM 注入給 guest（EL1）
- **iOS / Android**：EL3 跑 TEE OS（Trusty / iBoot），EL2 跑 hypervisor (Hyper-V on Apple)，EL1 跑 kernel

## Hypervisor 的 trap-and-emulate

EL2 的核心能力：把 guest kernel 試圖做的「敏感操作」trap 到 EL2，hypervisor 模擬一個結果回去：

```
Guest kernel (EL1) 執行 mrs x0, sctlr_el1
→ 設定 HCR_EL2.TVM = 1 觸發 trap 到 EL2
→ Hypervisor 處理：給一個假的 SCTLR 值
→ ERET 回 EL1，guest 以為自己在控制硬體
```

KVM、Xen、VMware 等都靠這個機制。**ARM 的虛擬化是硬體加速 trap-and-emulate** — Ch 22 詳述。

## 實際看 EL：CurrentEL register

```asm
mrs  x0, currentel
lsr  x0, x0, #2       ; current EL 在 bit[3:2]
```

x0 = 0 / 1 / 2 / 3 顯示目前 EL。寫 boot 程式 debug 時超實用。

C 端：

```c
uint64_t current_el = 0;
asm volatile("mrs %0, currentel" : "=r"(current_el));
current_el >>= 2;
```

## 從 EL3 降到 EL1 的 idiom

bare-metal 教學常見的 boot：

```asm
boot:
    /* 檢查目前 EL */
    mrs  x0, currentel
    lsr  x0, x0, #2
    cmp  x0, #3
    bne  not_in_el3
    /* 我們在 EL3，要降到 EL1 */

    /* 設好 EL1 的 stack pointer */
    msr  sp_el1, ...
    /* 設好 EL1 的 vector base */
    adr  x0, vectors_el1
    msr  vbar_el1, x0
    /* SCR_EL3：EL2 / EL1 是 non-secure、ERET 用 AArch64 */
    mov  x0, #0x531
    msr  scr_el3, x0
    /* 把 SPSR_EL3 設為「來自 EL1, IRQ masked」 */
    mov  x0, #(0xC5 | (1 << 6) | (1 << 7))
    msr  spsr_el3, x0
    /* ELR_EL3 = 我們要到 EL1 的位置 */
    adr  x0, el1_entry
    msr  elr_el3, x0
    /* 飛 */
    eret

el1_entry:
    /* 現在在 EL1 */
    ...
```

Practice B 會走這條路。

## 一個常見誤解

「為什麼 ARM 要 4 個 EL，x86 兩個 ring 不夠用？」

x86 的 ring 0/3 + 後來補的 VMX root mode + SMM mode，**其實也有 4 個 privilege level**。ARM 的 EL0/1/2/3 只是把這些**正交化**到單一機制。

EL 的好處：每個 EL 有獨立 system register、SP、page table、向量表，**清楚分層**。x86 VMX root mode 的 VMCS 是另一套機制，加上 SMM 又是另一套，比較碎。

ARM 設計把「特權層級」做成一級 concept；x86 一路演化堆疊。這也是 ARM 在伺服器、雲端虛擬化漂亮的原因之一。

## 自我檢核

- [ ] 我能說出 EL0/1/2/3 各自典型用途
- [ ] 我能解釋為什麼進高 EL 只能透過 exception
- [ ] 我能說出 ERET 是「回原 EL」不是「降一級」
- [ ] 我能畫出 ARM v8-A 典型 boot chain（BL1/2/31/33）
- [ ] 我能解釋 IRQ routing 與 SCR_EL3、HCR_EL2 的關係
- [ ] 我能寫出讀 CurrentEL 的指令

下一章看 AArch64 MMU 與分頁 — page table walk、granule、TTBR、Address space。

→ [Ch 16 AArch64 MMU 與分頁](./16-aarch64-mmu.md)
