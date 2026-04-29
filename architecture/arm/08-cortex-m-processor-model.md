# Ch 8 — Cortex-M 處理器模型：Thread/Handler、MSP/PSP

> 目標：搞懂 Cortex-M 上「mode」「privilege」「stack pointer」三個正交概念。這個處理器模型雖比 Cortex-A 簡單，但有自己一套 quirk，弄清楚後 Ch 9 reset、Ch 12 NVIC、final project 寫 RTOS 都會輕鬆很多。

## 三個正交概念

```
                  ┌────────────────┐
                  │ Operation Mode │  Thread mode / Handler mode
                  └────────────────┘
                  ┌────────────────┐
                  │   Privilege    │  Privileged / Unprivileged
                  └────────────────┘
                  ┌────────────────┐
                  │  Stack Pointer │  MSP / PSP
                  └────────────────┘
```

三個維度互相獨立，但有一些組合不被允許。

### Operation Mode

- **Thread mode**：執行普通程式碼。reset 後預設這裡。
- **Handler mode**：處理 exception / interrupt 時自動進入。

進入 Handler mode 的唯一方法 = **發生 exception**。退出 = **執行 exception return 序列**（後面講）。

### Privilege Level

- **Privileged**（特權）：能存取所有暫存器、設定，能執行所有指令。
- **Unprivileged**（非特權）：受限存取，跑 user thread 用。

關鍵限制：**Handler mode 永遠是 Privileged**（無法在 handler 內降權）。Thread mode 可以是 Privileged（預設）或 Unprivileged。

### Stack Pointer

Cortex-M 有 **兩個** SP 暫存器，都叫 R13 但實際是不同 register：

- **MSP**（Main SP）：reset 後預設用這個。Handler mode 永遠用 MSP。
- **PSP**（Process SP）：Thread mode 可選用。

切換哪一個用 SP 由 `CONTROL` register 決定（後面講）。

## 為什麼要兩個 SP？

主要是給 RTOS 用：

- **kernel / handler 用 MSP**：kernel stack 與 user task stack 隔離，避免 user task 棧溢位炸到 kernel。
- **每個 user task 用自己的 PSP**：context switch 只要切 PSP 與 task control block，MSP 不動。

如果你寫純 bare-metal、沒 RTOS、沒 isolation 需求，**全程用 MSP 就好**，跳過 PSP。

## CONTROL register：切換工具

```
CONTROL[2]   FPCA       FP Context Active（FPU 用了沒？）
CONTROL[1]   SPSEL      0 = MSP, 1 = PSP（僅 Thread mode 有效）
CONTROL[0]   nPRIV      0 = Privileged, 1 = Unprivileged（僅 Thread mode 有效）
```

存取方式：

```c
uint32_t ctrl = __get_CONTROL();   // CMSIS intrinsic
ctrl |= 0x2;                       // 設 SPSEL = 1, 用 PSP
__set_CONTROL(ctrl);
__ISB();                           // ISB 必加，下面會講
```

```asm
mrs  r0, control
orr  r0, r0, #2
msr  control, r0
isb                  ; 必須！
```

**ISB（Instruction Synchronization Barrier）必加**：CONTROL 改完後 CPU 流水線可能還在用舊值，ISB 把流水線丟掉重新抓。沒加會出隨機 bug。

## 從 reset 到第一條 main()

```
Power on
   │
   ▼
Reset_Handler 啟動（Handler mode、MSP、Privileged）
   │
   ├── 從 vector table 第 0 個 entry 載入 MSP 初值
   ├── 從第 1 個 entry 跳到 Reset_Handler
   ▼
Reset_Handler 執行 startup 工作
   │
   ├── copy .data
   ├── zero .bss
   ├── 呼叫 SystemInit()（FPU、PLL 等）
   ▼
進入 main()（仍在 Thread mode、MSP、Privileged）
   │
   ▼
有 RTOS 的場景：
  scheduler 啟動 → 切 PSP → Thread mode + PSP + (可選) Unprivileged
```

裸機程式整個過程都是 Thread + MSP + Privileged。RTOS 程式啟動後通常是 Thread + PSP + Unprivileged。

## 暫存器集

Cortex-M 的暫存器（v7-M 為例）：

```
通用：
  R0–R12      Low/High registers
  R13 = SP    （MSP 或 PSP，依 CONTROL[1]）
  R14 = LR    Link Register
  R15 = PC    Program Counter

Special:
  PSR = APSR + IPSR + EPSR
    APSR: N/Z/C/V flags
    IPSR: 當前 Exception Number
    EPSR: Thumb bit + IT state
  PRIMASK     全域中斷遮罩
  FAULTMASK   更高優先 fault 遮罩
  BASEPRI     優先權閾值遮罩
  CONTROL     上面那個
```

注意 **Cortex-M 沒有 CPSR**（AArch32 那種大狀態暫存器），改成拆開三個 PSR 子集，避免 CPSR 在 IRQ 上下被異動造成困擾。

## EXC_RETURN：handler 返回的魔法值

當 exception 發生：

```
HW 自動把 R0, R1, R2, R3, R12, LR, PC, xPSR 八個 reg push 到目前 SP
LR 被設為一個特殊的 EXC_RETURN 值（不是函式返回位址）
PC 跳到 ISR
```

`EXC_RETURN` 編碼了「返回時要怎麼處理」：

| 值 | 意義 |
|---|---|
| `0xFFFFFFF1` | Handler mode → Handler mode（巢狀 ISR） |
| `0xFFFFFFF9` | Handler mode → Thread mode + MSP |
| `0xFFFFFFFD` | Handler mode → Thread mode + PSP |
| `0xFFFFFFE1` | 同上但 FPU active（多存 FPU regs） |
| `0xFFFFFFE9` | 同上但 FPU active + MSP |
| `0xFFFFFFED` | 同上但 FPU active + PSP |

ISR 結束時執行 `bx lr`（把 LR 載入 PC），CPU 看 LR 高 bits 全 1 就知道這是 exception return，自動 unstack 並切回原狀態。

**這是 Cortex-M 的天才設計**：handler 看起來就像普通 C 函式，編譯器產生的 `bx lr` 自動觸發 exception return。寫 ISR 不用組語也能寫。

## 寫一個最簡 ISR

```c
void SysTick_Handler(void) {     // 名字必須對應 vector table
    ticks++;                      // 普通 C 程式
}                                 // 結尾編譯器自動 bx lr
```

對比 Cortex-A 寫 IRQ handler：

```asm
; AArch64
irq_handler:
    sub   sp, sp, #256
    stp   x0, x1, [sp]
    stp   x2, x3, [sp, #16]
    ; ... 手動存 30 個 reg ...
    bl    c_irq_handler
    ; ... 手動還原 ...
    eret
```

差別非常大：M 把 stacking 推給硬體；A 全靠軟體。M 的設計犧牲一點靈活換取**寫起來像普通函式 + 預測 IRQ latency**。

## IRQ latency：可預測的 12 cycle

Cortex-M3 IRQ latency 規格：

- Stack push 8 reg：12 cycle（Cortex-M3）
- Tail-chaining（連續 IRQ）：6 cycle
- Late arrival（高優先 IRQ 半路插隊）：6 cycle

**對 hard real-time 是巨大的賣點**：你能保證最壞情況。Cortex-A 由於 cache miss、TLB miss、軟體 stacking，IRQ latency 動輒幾百 cycle，對需要 µs 反應的場合不適合 — 這就是 R profile 出現的理由（Cortex-R 是 A-like 但有 deterministic 的東西）。

## 中斷遮罩三件套

Cortex-M 的中斷控制有三個 bit / register：

- **PRIMASK**：1 bit。設 1 全域 mask 所有中斷（除 NMI 與 HardFault）。等於 `cli`/`sti`。
- **FAULTMASK**：1 bit。比 PRIMASK 還狠，連 HardFault 都 mask（只有 NMI 能進）。
- **BASEPRI**：8 bit。**只 mask 優先權數字 ≥ BASEPRI 的中斷**。能精準切中。

設定方式：

```c
__disable_irq();   // 設 PRIMASK = 1
__enable_irq();    // PRIMASK = 0
__set_BASEPRI(0x40);  // mask priority ≥ 0x40 的中斷
```

寫 critical section 時 `__disable_irq()` 是最簡單；寫 RTOS scheduler 通常用 BASEPRI 留 高優先 IRQ 給 hard real-time event。

## 一個常見誤解

「Cortex-M 的 Handler mode 跟 Cortex-A 的 EL1 是不是一樣？」

不是。Cortex-A 的 EL 是個架構級的 privilege 概念（EL0–3 各自有獨立 page table、system register），Cortex-M 的 Handler mode 只是「現在在處理 exception」的狀態旗。**Handler mode 和 Thread mode 的特權其實一樣（都 Privileged）**，差別只在 SP 與 IRQ 的 escape 機制。

更貼近的對應是：Cortex-M Handler mode ≈ Cortex-A 的「正在執行 exception handler」這個臨時狀態，而 Cortex-A EL1 / EL0 沒有 Cortex-M 對應物（M 不分 user / kernel）。

## 自我檢核

- [ ] 我能說出 Thread / Handler mode 的差別與如何進入
- [ ] 我能說出 MSP 與 PSP 何時各自被使用
- [ ] 我能畫出 CONTROL register 的位元意義
- [ ] 我能解釋 EXC_RETURN 的概念與為什麼 ISR 可以是普通 C 函式
- [ ] 我能說出 PRIMASK / FAULTMASK / BASEPRI 的差別
- [ ] 我能比較 Cortex-M 的硬體 stacking 與 Cortex-A 的軟體 stacking

下一章看 reset 流程與向量表 — Cortex-M 上電到第一條 main() 中間發生的事。

→ [Ch 9 Reset 流程與向量表](./09-reset-and-vector-table.md)
