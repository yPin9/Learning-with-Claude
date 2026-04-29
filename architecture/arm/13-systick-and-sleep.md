# Ch 13 — SysTick 與低功耗

> 目標：搞定 Cortex-M 的 SysTick timer 與 sleep 機制（WFI / WFE）。SysTick 是 RTOS scheduler 的心跳，sleep 模式是嵌入式低功耗設計的核心。一起講因為 tickless idle 把它們綁在一起。

## SysTick：每 Cortex-M 必有的 timer

SysTick 是 Cortex-M architecture 規定的 **內建 24-bit downcounter**，每個 chip 都有，**不依賴 SoC 廠**。這是讓 RTOS 跨 chip portable 的關鍵。

```
Reload value (24 bit)
     │
     ▼
Counter: counts down every cycle / pre-divided clock
     │
     ▼
0 → 觸發 SysTick exception, counter 重新 load reload value
```

四個暫存器（在 SCB region）：

| Register | 用途 |
|---|---|
| `SysTick->CTRL` | enable / IRQ enable / clock source |
| `SysTick->LOAD` | reload value（counter 從這個值往下數） |
| `SysTick->VAL` | current counter value（讀寫，寫任意值都重置） |
| `SysTick->CALIB` | implementation 相關，校正用 |

CTRL 的 bit：

```
[0]  ENABLE     啟用 counter
[1]  TICKINT    enable SysTick exception
[2]  CLKSOURCE  0 = 外部 ref clock，1 = processor clock
[16] COUNTFLAG  counter 從 1 → 0 reload 時 set，讀就 clear
```

## 設一個 1 ms tick

假設 system clock 168 MHz：

```c
void SysTick_Init_1ms(void) {
    SysTick->LOAD = 168000 - 1;       // 168 MHz / 1000 = 168000 cycles per ms
    SysTick->VAL  = 0;                 // clear current
    SysTick->CTRL = (1 << 2)           // CLKSOURCE = processor clock
                  | (1 << 1)           // TICKINT = enable IRQ
                  | (1 << 0);          // ENABLE
}

volatile uint32_t ticks = 0;

void SysTick_Handler(void) {
    ticks++;
}
```

CMSIS 提供更乾淨的 helper：

```c
SysTick_Config(SystemCoreClock / 1000);   // 1 ms tick
```

它自動 set `LOAD = N-1`、設定優先權為最低（0xFF）、enable 全部 bits。

## 24-bit 限制：怎麼做長計時

24 bit = 0x00FFFFFF = 16,777,215。168 MHz 下 = 約 100 ms 滿載。

**做長計時的常見 idiom**：把 SysTick 設短週期（如 1 ms），ISR 內累加：

```c
volatile uint64_t ticks = 0;
void SysTick_Handler(void) { ticks++; }

uint64_t millis(void) { return ticks; }
```

要更高解析度（µs / ns）通常用 SoC 的 TIM peripheral（STM32 TIM2 是 32-bit），SysTick 留給 RTOS scheduler。

## DWT->CYCCNT：cycle-accurate timer

Cortex-M3+ 有 **Data Watchpoint and Trace (DWT)** 模組，內含 32-bit cycle counter：

```c
void DWT_Init(void) {
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CYCCNT = 0;
    DWT->CTRL  |= DWT_CTRL_CYCCNTENA_Msk;
}

uint32_t cycles = DWT->CYCCNT;     // 直接讀
```

每個 CPU cycle +1。32 bit 在 168 MHz 下 25 秒滿載 — overflow 接受不影響短時間 measurement（用減法取差就好）。

profile / 測 latency 就靠這個，比 SysTick 精準得多。

## Sleep 模式：WFI / WFE

```asm
wfi      ; Wait For Interrupt
wfe      ; Wait For Event
```

兩條指令都讓 CPU 進入低功耗 sleep。差別：

| | WFI | WFE |
|---|---|---|
| 醒來條件 | IRQ pending | Event flag set 或 IRQ |
| 用途 | 一般 idle | 多核 spinlock 等待 |
| Event flag | 不看 | 看 |

WFE 與 SEV (Send Event) 配對：

```asm
; CPU0
wfe                ; 等 event

; CPU1
sev                ; 觸發 event，叫醒所有 WFE
```

Cortex-M 是單核，WFE 用得少。Cortex-A 多核 spinlock 等待會用 WFE 省電。

## CMSIS macro

```c
__WFI();       // = inline asm wfi
__WFE();       // = wfe
__SEV();       // = sev
```

## 三種 Sleep 深度（SCB->SCR + SoC 特定）

ARM 規格只規定兩種「sleep state」：

- **Sleep**：clock stop，IRQ 立刻醒
- **Deep sleep**：更深，可能停 PLL，醒得慢

`SCB->SCR` 控制：

```c
SCB->SCR &= ~SCB_SCR_SLEEPDEEP_Msk;   // 普通 sleep
SCB->SCR |= SCB_SCR_SLEEPDEEP_Msk;    // deep sleep
```

但 **SoC 廠通常加更多模式**（STM32：Sleep / Stop 0/1/2 / Standby / Shutdown），耗電從毫安到奈安級不等。SoC 特定 macro 例：

```c
HAL_PWR_EnterSLEEPMode(...);
HAL_PWR_EnterSTOPMode(...);
HAL_PWR_EnterSTANDBYMode(...);
```

## SLEEPONEXIT：ISR 退出自動回 sleep

```c
SCB->SCR |= SCB_SCR_SLEEPONEXIT_Msk;
```

設這個 bit 後，ISR 結束時 CPU 自動回 sleep（不用在 main loop 寫 WFI）。

idiom：

```c
int main(void) {
    init_stuff();
    SCB->SCR |= SCB_SCR_SLEEPONEXIT_Msk;
    __WFI();              // 第一次 sleep
    /* 之後 IRQ 結束就自動回 sleep，main 永不返回 */
}
```

純 IRQ-driven 韌體（感測器 wake → 處理 → sleep）超適合。

## Tickless idle：RTOS 的省電 trick

普通 RTOS 每 1 ms 喚醒 SysTick → 跑 scheduler → 沒事繼續 sleep。但 **每個 SysTick wake 都耗能**。

**Tickless idle**：scheduler 算出「下一次 task ready 還要 N ms」，**重新設 SysTick LOAD = N**，sleep 整段：

```c
void enter_tickless_idle(uint32_t expected_idle_ms) {
    SysTick->CTRL &= ~1;                            // stop
    SysTick->LOAD = SystemCoreClock / 1000 * expected_idle_ms - 1;
    SysTick->VAL  = 0;
    SysTick->CTRL |= 1;                             // restart
    __WFI();
    /* 醒來時可能是 SysTick 完，也可能是其他 IRQ 提早醒 */
    /* 計算實際睡了多久，把 OS tick 補上 */
}
```

FreeRTOS / Zephyr 都有 `configUSE_TICKLESS_IDLE` 開這個機制。**省電效果 5–20×**，對電池供電裝置巨大。

## DWT 的其他能力

DWT 不只 CYCCNT，還有：

- **Comparator**：4 個（M3）/ 多個（M7+），可設 watchpoint
- **PC sampling**：定期記 PC，做 statistical profiling
- **Exception trace**：記每個 exception entry/exit
- **Event counter**：記 cycle / fold / sleep 等事件

GDB + OpenOCD 可以拉這些 trace 出來，Ch 24 / 29 會展開。

## 一個常見誤解

「SysTick 只能拿來做 RTOS tick 嗎？」

不是。**只是 RTOS 慣用而已**。你可以拿 SysTick 做：

- 一般 timer（計時、led 閃）
- 1-shot 倒數（`LOAD = X; ENABLE; 跑完 disable`）
- 短延遲（busy-wait `while ((SysTick->CTRL & 0x10000) == 0);`）

但 SysTick 只有一個，被 RTOS 占了就不能他用。要其他 timer 用 SoC 周邊（TIM2/3/4...）。

## 自我檢核

- [ ] 我能設一個 1 ms 週期的 SysTick
- [ ] 我能解釋 24-bit 限制怎麼做長計時
- [ ] 我能用 DWT->CYCCNT 測一段 code 的 cycle
- [ ] 我能說出 WFI 與 WFE 的差別
- [ ] 我能寫 SLEEPONEXIT idiom
- [ ] 我能解釋 tickless idle 的概念與省電效果

下一章看 Cortex-M 的 MPU — 簡化版的 memory protection，沒 MMU 但可以做 process isolation。

→ [Ch 14 MPU（Cortex-M 版）](./14-mpu-cortex-m.md)
