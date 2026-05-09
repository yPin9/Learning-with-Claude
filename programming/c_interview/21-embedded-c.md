# Ch 21 — 嵌入式 C 模式

> 目標：掌握嵌入式開發的核心 C 技巧：volatile + MMIO、位元操作、固定寬度型別、ISR 限制，以及 linker script 基礎。

## 嵌入式 C 的思維差異

嵌入式 C 和一般 C 的本質差異：

1. **記憶體是有限且確定的**：不能 malloc，用靜態分配
2. **硬體暫存器是 memory-mapped**：必須用 volatile 才能正確讀寫
3. **中斷隨時可能打斷**：共享資料必須 atomic 或關中斷保護
4. **沒有 OS 的 libc**：printf 要 retarget、時鐘要自己管

---

## volatile + MMIO

Memory-mapped I/O：硬體暫存器被映射到特定記憶體地址。不用 volatile，編譯器可能把重複讀取優化掉：

```c
// 錯誤：沒有 volatile
uint32_t *uart_sr = (uint32_t *)0x40011004;   // UART Status Register
while (!(*uart_sr & 0x80)) ;   // 等待 TX empty bit
// 編譯器：*uart_sr 在迴圈裡從未被「本程式碼」修改
// → 優化成 if (!initial_value) { while(1); } → 無窮迴圈！

// 正確：volatile 指標
volatile uint32_t *uart_sr = (volatile uint32_t *)0x40011004;
while (!(*uart_sr & 0x80)) ;   // 每次迴圈都真正讀記憶體
```

**CMSIS 風格（Cortex-M 標準）**：

```c
// 通常在設備頭檔定義整個周邊的暫存器 struct：
typedef struct {
    volatile uint32_t SR;    // 0x00：Status Register
    volatile uint32_t DR;    // 0x04：Data Register
    volatile uint32_t BRR;   // 0x08：Baud Rate Register
    volatile uint32_t CR1;   // 0x0C：Control Register 1
} USART_TypeDef;

#define USART1  ((USART_TypeDef *)0x40011000)

// 使用：
USART1->CR1 |= (1 << 13);         // 啟用 USART
while (!(USART1->SR & (1 << 7))); // 等待 TXE
USART1->DR = 'A';                 // 傳送字元
```

---

## 位元操作慣用法

```c
// 設定位元（Set bit N）：
reg |= (1U << N);

// 清除位元（Clear bit N）：
reg &= ~(1U << N);

// 切換位元（Toggle bit N）：
reg ^= (1U << N);

// 測試位元（Test bit N）：
if (reg & (1U << N)) { /* bit N is set */ }

// 設定多個位元的欄位（field）：
#define FIELD_MASK  (0x3U << 4)   // bit 4:5
#define FIELD_VAL   (0x2U << 4)
reg = (reg & ~FIELD_MASK) | FIELD_VAL;  // 清除欄位再設定新值
```

**用 1U 而不是 1**：`1 << 31` 是 UB（有號整數左移到 sign bit），`1U << 31` 是 well-defined。

---

## 固定寬度型別

永遠用 `<stdint.h>` 的型別，不要用 `int`：

```c
#include <stdint.h>

uint8_t  byte_val;   // 暫存器的一個 byte
uint16_t half_word;  // 16-bit 暫存器
uint32_t reg_val;    // 32-bit 暫存器
int32_t  signed_val; // 有號 32-bit（ADC 的有號值）

// 常數也要加後綴避免提升問題：
uint32_t mask = 0xFFFF0000UL;   // UL = unsigned long（32-bit 系統）
uint64_t big  = 0x100000000ULL; // ULL = unsigned long long
```

---

## ISR（中斷服務程式）的限制

```c
// ISR 標記（GCC ARM）：
void TIM2_IRQHandler(void) __attribute__((interrupt("IRQ")));

void TIM2_IRQHandler(void) {
    // 1. 不能用 malloc（non-reentrant，動態記憶體分配不安全）
    // 2. 不能用 printf（可能有 lock，且時間不確定）
    // 3. 不能 sleep 或 block
    // 4. 執行時間要盡量短

    // ISR 和主程式共享的資料必須是 volatile：
    if (TIM2->SR & TIM_SR_UIF) {
        TIM2->SR &= ~TIM_SR_UIF;    // 清除中斷旗標
        g_tick_count++;              // 要宣告成 volatile uint32_t g_tick_count
    }
}
```

**多字節共享資料的保護**：

```c
// 主程式讀 64-bit 計數器時，若中斷可能修改它：
uint32_t hi1 = g_tick_hi;
uint32_t lo  = g_tick_lo;
uint32_t hi2 = g_tick_hi;
if (hi1 != hi2) {   // 若讀到一半被中斷修改了
    lo = 0;         // 重讀（簡單的 retry 邏輯）
}
// 或：關中斷後讀，再開中斷
```

---

## Linker Script 基礎

嵌入式系統的記憶體佈局由 linker script（`.ld`）決定：

```
/* 簡化版 STM32F4 linker script */
MEMORY {
    FLASH (rx)  : ORIGIN = 0x08000000, LENGTH = 512K   /* 唯讀 Flash */
    RAM   (rwx) : ORIGIN = 0x20000000, LENGTH = 128K   /* SRAM */
}

SECTIONS {
    .text : {
        KEEP(*(.isr_vector))    /* 中斷向量表必須在 Flash 最前面 */
        *(.text .text.*)        /* 程式碼 */
        *(.rodata .rodata.*)    /* 唯讀資料（字串常數等） */
    } > FLASH

    .data : {
        _data_start = .;
        *(.data .data.*)        /* 初始化的全域變數 */
        _data_end = .;
    } > RAM AT > FLASH          /* 存在 Flash，啟動時 copy 到 RAM */

    .bss : {
        _bss_start = .;
        *(.bss .bss.*)          /* 未初始化全域變數（啟動時清零）*/
        _bss_end = .;
    } > RAM
}
```

啟動代碼（startup.s）讀 `_data_start`、`_data_end` 把 Flash 中的初始值複製到 RAM，並清零 `.bss`。

---

## 常用嵌入式 C 特性

```c
// 把變數放到特定 section：
__attribute__((section(".ccmram"))) uint8_t fast_buf[1024];

// 不要移除的函式（避免 DCE 刪除 ISR）：
__attribute__((used)) void HardFault_Handler(void) { while(1); }

// 強制 inline（嵌入式對時序敏感的地方）：
__attribute__((always_inline)) static inline void delay_us(uint32_t us) { ... }

// 按 N bytes 對齊（DMA 通常需要 word 對齊）：
__attribute__((aligned(4))) uint8_t dma_buf[256];
```

---

## 自我檢核

- [ ] 能解釋為什麼 MMIO 暫存器必須用 volatile 指標存取
- [ ] 知道位元操作的四種基本模式（set/clear/toggle/test）
- [ ] 知道 ISR 裡不能用 malloc / printf / block 的原因
- [ ] 知道 linker script 的 `.data` section 為什麼同時屬於 Flash 和 RAM

→ [Ch 22 C11 並行：_Atomic 與 pthread](./22-c11-concurrency.md)
