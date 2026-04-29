# Ch 12 — NVIC：優先權、tail-chaining、late arrival

> 目標：搞懂 Cortex-M 的 Nested Vectored Interrupt Controller — 優先權編碼、preemption vs sub-priority、tail-chaining、late arrival、所有讓 NVIC 比 GIC 更適合 hard real-time 的設計細節。

## NVIC 在哪？做什麼？

NVIC 是 **Cortex-M 內建的中斷控制器**，跟 CPU 緊耦合（不像 Cortex-A 的 GIC 是獨立 IP）。它的任務：

- 接收 SoC 周邊的中斷訊號（peripheral IRQ）
- 排序、決定優先權、決定哪個 IRQ 先服務
- 觸發 CPU 進 Handler mode、自動 push 8 個 reg
- 處理 nesting、tail-chaining、late arrival
- 提供 SW trigger（pending bit 寫入）

Cortex-M3 的 NVIC 支援最多 **240 個外部中斷 + 16 個內部 system exception**。

```
┌────────────────────────────────────────────────────────┐
│                    Cortex-M Core                       │
│                                                        │
│   ┌──────────┐         ┌────────────┐                  │
│   │  ALU/    │ ◄────── │    NVIC    │ ◄────── Periph IRQs
│   │ Pipeline │  signal │ (240 IRQs) │ ◄────── SysTick
│   └──────────┘         │ priority   │ ◄────── PendSV
│                        │ pending    │ ◄────── SVCall
│                        └────────────┘                  │
└────────────────────────────────────────────────────────┘
```

## 優先權：數字越小越優先

```
0  = 最高優先
1
2
...
255 (or 7, 3 ... 看 chip 實作幾 bit)
```

**Cortex-M 規格說「最多 8 bit 優先權」，但實際 chip 通常 implement 3、4、5 bit**：

- STM32F4：4 bit（16 級）
- Cortex-M0：2 bit（4 級）
- nRF52：3 bit（8 級）

剩下的 bit「永遠讀作 0」。

## 設定 priority

```c
// CMSIS API
NVIC_SetPriority(SysTick_IRQn, 5);
NVIC_SetPriority(USART2_IRQn, 10);

// Direct register
NVIC->IP[USART2_IRQn] = (5 << (8 - __NVIC_PRIO_BITS));
                                // 重點：要 shift 到高 bit
                                // 假設只實作 4 bit，shift 4 位
```

優先權暫存器是 8-bit，但實作 N bit 時，**用法是 [7:8-N]**（高位元）。寫 `0x05` 在 4-bit 實作下實際讀回 `0x00`（因為 shift 後低 4 bit 沒實作）；寫 `0x50` 才是真的 priority 5。

CMSIS 的 `NVIC_SetPriority` 會幫你 shift，**不要手動寫 NVIC->IP**，容易踩雷。

## Preemption vs Sub-priority

Cortex-M 的 priority 可以拆成兩部分：

```
8 bit priority = N bit preemption | M bit sub-priority

例：4 bit priority、3 bit preempt + 1 bit sub
   priority = 0bAAA_B
              ^^^ ^
              prio sub
```

差別：

- **不同 preemption**：高的可以 **preempt** 低的（中斷服務中再插隊）
- **同 preemption 但 sub 不同**：**不能 preempt**，但決定**同時 pending 的順序**

設定切點用 `SCB->AIRCR`：

```c
NVIC_SetPriorityGrouping(3);
// PRIGROUP=3 → 4 bit preempt, 0 bit sub（全部能 preempt）
// PRIGROUP=4 → 3 bit preempt, 1 bit sub
// ...
// PRIGROUP=7 → 0 bit preempt, 8 bit sub（沒人能 preempt）
```

實務上多數人用 **全 preemption**（PRIGROUP=3），讓每個優先級都能搶。

## NVIC enable / pending / active

每個 IRQ 在 NVIC 有三種狀態：

| 狀態 | 意義 |
|---|---|
| **Enabled** | 由 NVIC->ISER 設，沒 enable 就算 trigger 也不會跳 ISR |
| **Pending** | 已觸發但還沒被服務（CPU 正在跑高優先 ISR） |
| **Active** | ISR 正在執行 |

控制 register：

```c
NVIC_EnableIRQ(USART2_IRQn);     // 寫 NVIC->ISER
NVIC_DisableIRQ(USART2_IRQn);    // 寫 NVIC->ICER
NVIC_SetPendingIRQ(USART2_IRQn); // 寫 NVIC->ISPR（軟體觸發）
NVIC_ClearPendingIRQ(USART2_IRQn); // 寫 NVIC->ICPR
NVIC_GetActive(USART2_IRQn);      // 讀 NVIC->IABR
```

軟體觸發（ISPR）很實用 — RTOS 用 PendSV 做 context switch 就是這個機制。

## Tail-chaining：連續 IRQ 省 stacking

正常 IRQ 流程：

```
IRQ A 進來 → push 8 reg → 執行 A → pop 8 reg → 回主程式
IRQ B 進來 → push 8 reg → 執行 B → pop 8 reg → 回主程式
```

正常情況 push + pop 各約 12 cycle。

**Tail-chaining**：A ISR 結束時，NVIC 發現 B 還在 pending，**直接從 A 跳 B，不 unstack 也不 restack**：

```
IRQ A 進來 → push 8 reg → 執行 A → 直接跳 B（6 cycle） → 執行 B → pop 8 reg
```

省一次 unstack + 一次 stack，**省約 12 cycle**。對 IRQ 密集的場景（高速 UART、CAN bus）累積很顯著。

這是 NVIC 自動做的，**不需要程式設計者寫程式碼**。

## Late arrival：高優先 IRQ 半路插隊

A ISR 還沒開始執行（仍在 stacking），這時 B 比 A 優先權高、進來了：

```
IRQ A 進來 → 開始 push 8 reg → push 中 IRQ B 進來
NVIC 發現 B 優先權高 → push 完後直接執行 B（不執行 A）
B 結束 → tail-chain 到 A
```

**省一次 stacking**，不必 push 完 A 又 push 一次給 B。

這個是「late arrival」一詞的由來：B 「晚到」但仍能搶 A 的執行。

## 巢狀中斷：高優先搶低優先

```c
void TIM2_IRQHandler(void) {       // priority 5
    // ... 跑到一半 ...
    // 此時 USART2 IRQ (priority 2) 觸發
    // → NVIC push 一次（保護 TIM2 上下文）
    // → 跳 USART2 IRQ
    // ...
}
```

**Cortex-M 自動支援 nested IRQ**（NVIC 名字裡的 N）。同優先權不互搶，高優先會 preempt 低優先。

對程式設計者：寫 ISR 預設要假設**可能被高優先 ISR 打斷**。共用變數要 atomic 或 mask。

## Critical section 寫法

短段不希望被打斷：

```c
__disable_irq();   // PRIMASK = 1
shared_state = compute_new();
__enable_irq();
```

但這會 mask 所有 IRQ 包括 hard real-time。RTOS 通常用 BASEPRI：

```c
__set_BASEPRI(0x40);    // mask 優先權 ≥ 0x40 的 IRQ
                        // 高優先 IRQ（優先權 < 0x40）仍能跑
do_critical_thing();
__set_BASEPRI(0);
```

這保證了 RTOS scheduler 不被低優先 IRQ 干擾，但 hard real-time IRQ（例如馬達控制）仍即時。

## SysTick + PendSV：RTOS 的兩根支柱

兩個系統 exception 是 RTOS 的核心 idiom：

- **SysTick**：定時 tick（後章詳細）
- **PendSV**：「pendable service」— 軟體可 trigger 的低優先 exception

PendSV 用法：

```c
// 在 ISR 內想做 context switch，但不能在這裡做（時間長）
// 觸發 PendSV，等 ISR 都跑完才執行
SCB->ICSR = SCB_ICSR_PENDSVSET_Msk;

// PendSV_Handler 做 context switch
void PendSV_Handler(void) {
    // 把當前 task 的 reg push 到 task stack
    // 切到新 task 的 stack
    // 載入新 task 的 reg
}
```

PendSV 永遠設 **最低優先權**，這樣它一定**等所有正常 IRQ 跑完才執行**，避免在 high-priority IRQ 內被插隊做 context switch（會搞死 stack）。

Final project 的 RTOS 會展開這個。

## NVIC vs GIC：Cortex-A 的中斷世界

簡短對比，下一 part 會展開：

| | NVIC (Cortex-M) | GIC (Cortex-A) |
|---|---|---|
| 位置 | 在 CPU 核裡 | 獨立 IP，外掛 |
| IRQ 數量 | ≤ 240 | ≤ 1020 (GIC v2) / 更多 (v3/v4) |
| 優先權 | 256 級 (理論) | 256 級 |
| HW stacking | 有 | 沒有 |
| Latency | 12 cycle 可預測 | 100+ cycle 不可預測 |
| 多核 | 單核設計 | 多核 + IPI 完整支援 |

GIC 為「多核 SMP + 通用伺服器」設計；NVIC 為「單核 + hard real-time」設計。各有 sweet spot。

## 一個常見誤解

「IRQ 觸發後 push 暫存器是不是 RTOS 該做的事？」

**不是**。Cortex-M 的 NVIC 自動把 8 個 caller-saved reg push 到 SP，**ISR 入口時 R0-R3、R12 已經保存好了**。RTOS 只負責：

1. 在 PendSV 內把 callee-saved (R4-R11) 也 push 到 task stack
2. 切換 PSP 到新 task
3. 從新 task stack pop callee-saved
4. exception return 時 NVIC 自動 unstack caller-saved

**比 Cortex-A 的 RTOS 簡單得多**。Linux kernel 為了 ARMv8 寫一大堆 stack 處理 asm；嵌入式 RTOS 的 PendSV handler 通常 < 30 條指令。

## 自我檢核

- [ ] 我能說出 NVIC 優先權數字越小越優先
- [ ] 我能解釋為什麼設 priority 要 shift 到高 bit
- [ ] 我能區分 preemption priority 與 sub-priority
- [ ] 我能描述 tail-chaining 與 late arrival 各省什麼
- [ ] 我能寫一個用 BASEPRI 的 critical section
- [ ] 我能解釋 PendSV 為什麼設最低優先權

下一章 SysTick + 低功耗：SysTick 怎麼配、WFI 進 sleep、tickless idle。

→ [Ch 13 SysTick 與低功耗](./13-systick-and-sleep.md)
