# 練習 A — STM32 bare-metal 韌體（LED + UART + Timer）

> 目標：把 Ch 8–14 學的 Cortex-M 觀念全部串起來，**不用任何 HAL / CMSIS driver**，純手刻一個能跑的韌體。完成後你會知道 STM32CubeIDE 自動生成的程式碼究竟在做什麼。

## 任務規格

寫一個 STM32F407（或 QEMU mps2-an385 模擬 Cortex-M3）韌體，達到：

| 功能 | 規格 |
|---|---|
| LED 閃爍 | 1 Hz（亮 0.5s 滅 0.5s） |
| UART 輸出 | 115200 baud, 8N1，每秒印 `tick: <count>\n` |
| MPU | 在 0x00000000 設 NULL pointer protect (32 bytes no access) |
| 不用 HAL / CMSIS driver | 自己刻 register 操作 |
| startup + linker script | 自己寫，不抄 ST 模板 |

選擇平台：

- **路線 A — 實體 STM32F4 Discovery**：LED 是 PD12-15 任一，USART2 在 PA2/PA3 連 USB-TTL
- **路線 B — QEMU mps2-an385**：是 Arm Cortex-M3 模型，UART 在 0x40004000，沒實體 LED 但 stdout 看 UART 即可

兩條路都做，更紮實。

## 為什麼不用 HAL？

`STM32 HAL` 是 ST 包好的 driver layer，函式名長、初始化結構填一大堆欄位。寫過 STM32 的人知道 HAL 把人煩死。**手刻 register 反而更直觀** — 你看著 STM32F4 reference manual 的 GPIO chapter，知道 GPIOD->ODR bit 12 = 1 點亮 LED。

學完這個練習，看 HAL 你會知道它在做什麼，反過來覺得「HAL 過度封裝」。這是這個練習的另一個 deliverable。

## 期望輸出

UART（115200 8N1）：

```
booting...
init_clock_done
init_uart_done
init_systick_done
init_mpu_done
tick: 1
tick: 2
tick: 3
...
```

LED 同步 1 Hz 閃。

## 實作步驟建議

### Step 1：linker script 與 startup 骨架

寫 `stm32f4.ld`、`startup.c`，編出能進 main 的最小 binary。先不做 LED / UART / SysTick / MPU。

```c
// startup.c
extern uint32_t _stack_top;
extern uint32_t _sidata, _sdata, _edata, _sbss, _ebss;
void main(void);

void Reset_Handler(void) {
    uint32_t *src = &_sidata, *dst = &_sdata;
    while (dst < &_edata) *dst++ = *src++;
    dst = &_sbss;
    while (dst < &_ebss) *dst++ = 0;
    main();
    while (1);
}

void Default_Handler(void) { while (1); }

__attribute__((section(".isr_vector"), used))
void (* const vector_table[])(void) = {
    (void(*)(void))(&_stack_top),
    Reset_Handler,
    /* 其他 NVIC IRQ ... 先全 Default_Handler */
};
```

### Step 2：時鐘初始化

STM32F4 reset 後跑 16 MHz HSI。要拉到 168 MHz：

1. 開 HSE（外部晶體）8 MHz
2. 配 PLL：M=8, N=336, P=2 → 336/2 = 168 MHz
3. 等 PLL ready
4. switch SYSCLK 來源到 PLL
5. 配 AHB / APB1 / APB2 prescaler

QEMU mps2-an385 跳過這步（QEMU 不模擬時鐘，直接用 25 MHz 預設）。

### Step 3：GPIO LED 初始化

STM32F4 Discovery PD12 連綠 LED：

1. 開啟 RCC AHB1 enable 給 GPIOD：`RCC->AHB1ENR |= (1 << 3)`
2. 設 PD12 為 output：`GPIOD->MODER &= ~(3 << 24); GPIOD->MODER |= (1 << 24)`
3. ODR 控制亮滅：`GPIOD->ODR ^= (1 << 12)`

### Step 4：UART 初始化

USART2 在 PA2 (TX) / PA3 (RX)：

1. 開 RCC GPIOA、USART2 clock
2. 設 PA2/PA3 為 alternate function 7 (USART2)
3. USART2->BRR = APB1_clock / 115200（要算精確值，看 RM 16.3.4）
4. USART2->CR1 = TE | RE | UE

簡單 putchar：

```c
void uart_putc(char c) {
    while (!(USART2->SR & (1 << 7))) ;     // wait TXE
    USART2->DR = c;
}

void uart_puts(const char *s) {
    while (*s) uart_putc(*s++);
}
```

### Step 5：SysTick 1 ms tick

```c
volatile uint32_t ticks = 0;

void SysTick_Init(void) {
    SysTick->LOAD = 168000 - 1;      // 1 ms
    SysTick->VAL = 0;
    SysTick->CTRL = 7;                // CLKSOURCE | TICKINT | ENABLE
}

void SysTick_Handler(void) {
    ticks++;
}
```

⚠ 別忘了在 vector table 把 SysTick_Handler 放在 [15] 那格。

### Step 6：MPU NULL protect

```c
void MPU_Init(void) {
    MPU->RNR = 0;                                    // region 0
    MPU->RBAR = 0x00000000;                          // base
    MPU->RASR = (1 << 0)                             // ENABLE
              | (4 << 1)                             // SIZE = 32 bytes (2^(4+1))
              | (0 << 24)                            // AP = no access
              | (1 << 28);                           // XN = 1
    MPU->CTRL = 1;                                    // ENABLE MPU
    __DSB();
    __ISB();
}
```

測試：在 main 結尾故意 `*(volatile int *)0 = 1;`，應該觸發 MemManage。

### Step 7：main 主迴圈

```c
int main(void) {
    init_clock();
    uart_puts("booting...\r\n");
    init_uart();         /* 已經能用 uart_puts 是因為 init_uart 把 USART2 開好 */
    uart_puts("init_uart_done\r\n");
    init_gpio();
    SysTick_Init();
    uart_puts("init_systick_done\r\n");
    MPU_Init();
    uart_puts("init_mpu_done\r\n");

    uint32_t last_blink = 0;
    uint32_t last_print = 0;
    uint32_t print_count = 1;
    while (1) {
        if (ticks - last_blink >= 500) {
            GPIOD->ODR ^= (1 << 12);
            last_blink = ticks;
        }
        if (ticks - last_print >= 1000) {
            uart_puts("tick: ");
            print_uint(print_count++);
            uart_puts("\r\n");
            last_print = ticks;
        }
    }
}
```

`print_uint` 自己刻一個 itoa 換成字串再 `uart_puts`，不要用 `printf`（沒 newlib 連 link 都 link 不過）。

## 編譯與燒寫

```bash
arm-none-eabi-gcc \
    -mcpu=cortex-m4 -mthumb -mfpu=fpv4-sp-d16 -mfloat-abi=hard \
    -nostdlib -O2 -Wall \
    -T stm32f4.ld \
    startup.c main.c -o firmware.elf

arm-none-eabi-objcopy -O binary firmware.elf firmware.bin

# Discovery 燒寫
st-flash write firmware.bin 0x08000000

# 或 QEMU
qemu-system-arm -M mps2-an385 -kernel firmware.elf -nographic
```

## 完整參考解答

**先自己寫過再看。**

<details>
<summary>linker script (stm32f4.ld)</summary>

```ld
ENTRY(Reset_Handler)

MEMORY
{
    FLASH (rx)  : ORIGIN = 0x08000000, LENGTH = 1024K
    RAM   (rwx) : ORIGIN = 0x20000000, LENGTH = 128K
}

SECTIONS
{
    .isr_vector :
    {
        KEEP(*(.isr_vector))
    } > FLASH

    .text :
    {
        *(.text*)
        *(.rodata*)
        _etext = .;
    } > FLASH

    .data : AT(_etext)
    {
        _sdata = .;
        *(.data*)
        _edata = .;
    } > RAM

    _sidata = LOADADDR(.data);

    .bss :
    {
        _sbss = .;
        *(.bss*)
        *(COMMON)
        _ebss = .;
    } > RAM

    _stack_top = ORIGIN(RAM) + LENGTH(RAM);
}
```

</details>

<details>
<summary>main.c 完整實作（STM32F4 Discovery）</summary>

```c
#include <stdint.h>

/* ─── Register definitions（最小） ─── */
#define RCC_BASE      0x40023800
#define GPIOA_BASE    0x40020000
#define GPIOD_BASE    0x40020C00
#define USART2_BASE   0x40004400

#define RCC_CR        (*(volatile uint32_t *)(RCC_BASE + 0x00))
#define RCC_PLLCFGR   (*(volatile uint32_t *)(RCC_BASE + 0x04))
#define RCC_CFGR      (*(volatile uint32_t *)(RCC_BASE + 0x08))
#define RCC_AHB1ENR   (*(volatile uint32_t *)(RCC_BASE + 0x30))
#define RCC_APB1ENR   (*(volatile uint32_t *)(RCC_BASE + 0x40))

#define GPIOA_MODER   (*(volatile uint32_t *)(GPIOA_BASE + 0x00))
#define GPIOA_AFRL    (*(volatile uint32_t *)(GPIOA_BASE + 0x20))
#define GPIOD_MODER   (*(volatile uint32_t *)(GPIOD_BASE + 0x00))
#define GPIOD_ODR     (*(volatile uint32_t *)(GPIOD_BASE + 0x14))

#define USART_SR      (*(volatile uint32_t *)(USART2_BASE + 0x00))
#define USART_DR      (*(volatile uint32_t *)(USART2_BASE + 0x04))
#define USART_BRR     (*(volatile uint32_t *)(USART2_BASE + 0x08))
#define USART_CR1     (*(volatile uint32_t *)(USART2_BASE + 0x0C))

#define SYSTICK_CTRL  (*(volatile uint32_t *)0xE000E010)
#define SYSTICK_LOAD  (*(volatile uint32_t *)0xE000E014)
#define SYSTICK_VAL   (*(volatile uint32_t *)0xE000E018)

#define MPU_CTRL      (*(volatile uint32_t *)0xE000ED94)
#define MPU_RNR       (*(volatile uint32_t *)0xE000ED98)
#define MPU_RBAR      (*(volatile uint32_t *)0xE000ED9C)
#define MPU_RASR      (*(volatile uint32_t *)0xE000EDA0)

volatile uint32_t ticks = 0;

void SysTick_Handler(void) { ticks++; }

static void init_clock(void) {
    /* HSE on */
    RCC_CR |= (1 << 16);
    while (!(RCC_CR & (1 << 17))) ;
    /* PLL: M=8, N=336, P=2, src=HSE */
    RCC_PLLCFGR = 8 | (336 << 6) | (0 << 16) | (1 << 22);
    RCC_CR |= (1 << 24);
    while (!(RCC_CR & (1 << 25))) ;
    /* AHB=/1, APB1=/4, APB2=/2 */
    RCC_CFGR = (5 << 10) | (4 << 13) | (2);   /* SW = PLL */
    while (((RCC_CFGR >> 2) & 3) != 2) ;
}

static void init_gpio(void) {
    RCC_AHB1ENR |= (1 << 3);                  /* GPIOD */
    GPIOD_MODER &= ~(3UL << 24);
    GPIOD_MODER |=  (1UL << 24);              /* PD12 output */
}

static void init_uart(void) {
    RCC_AHB1ENR |= (1 << 0);                  /* GPIOA */
    RCC_APB1ENR |= (1 << 17);                 /* USART2 */
    GPIOA_MODER &= ~((3UL << 4) | (3UL << 6));
    GPIOA_MODER |=  ((2UL << 4) | (2UL << 6)); /* PA2/PA3 AF */
    GPIOA_AFRL  |=  (7UL << 8) | (7UL << 12);  /* AF7 = USART2 */
    /* APB1 = 42 MHz, baud 115200 */
    USART_BRR = 0x16C;                          /* 42M / 115200 = 364.58... */
    USART_CR1 = (1 << 13) | (1 << 3) | (1 << 2); /* UE | TE | RE */
}

static void uart_putc(char c) {
    while (!(USART_SR & (1 << 7))) ;
    USART_DR = c;
}

static void uart_puts(const char *s) {
    while (*s) uart_putc(*s++);
}

static void uart_putu(uint32_t x) {
    char buf[11], *p = buf + sizeof(buf);
    *--p = 0;
    if (!x) *--p = '0';
    while (x) { *--p = '0' + x % 10; x /= 10; }
    uart_puts(p);
}

static void init_systick(void) {
    SYSTICK_LOAD = 168000 - 1;
    SYSTICK_VAL  = 0;
    SYSTICK_CTRL = 7;
}

static void init_mpu(void) {
    MPU_RNR  = 0;
    MPU_RBAR = 0x00000000;
    MPU_RASR = (1 << 0) | (4 << 1) | (1 << 28);  /* enable, 32B, XN, AP=000 */
    MPU_CTRL = 1;
    __asm volatile("dsb; isb");
}

int main(void) {
    init_clock();
    init_gpio();
    init_uart();
    uart_puts("booting...\r\n");
    init_systick();
    uart_puts("init_systick_done\r\n");
    init_mpu();
    uart_puts("init_mpu_done\r\n");

    uint32_t last_blink = 0, last_print = 0, count = 1;
    while (1) {
        if (ticks - last_blink >= 500) {
            GPIOD_ODR ^= (1 << 12);
            last_blink = ticks;
        }
        if (ticks - last_print >= 1000) {
            uart_puts("tick: ");
            uart_putu(count++);
            uart_puts("\r\n");
            last_print = ticks;
        }
    }
}
```

</details>

## 測試用例

1. **常規啟動**：上電應印 `booting...` 至 `init_mpu_done`，然後每秒 `tick: N`，LED 1 Hz 閃
2. **NULL 寫測試**：在 `init_mpu` 後加 `*(volatile int*)0 = 1;`，應觸發 MemManage（系統 hang，可用 GDB 抓到 PC 在那行）
3. **拔線測試**：把 UART 線拔了再插回，應從拔線當下開始的 tick 繼續印（沒 backlog）
4. **時鐘錯誤**：故意把 PLL 配錯，UART baud 算出來會錯，亂碼。改回 168 MHz 應正常

## 自我檢核

- [ ] 我能寫一份不抄模板的 startup.c
- [ ] 我能寫對應的 linker script
- [ ] 我能直接用 register 操作 GPIO / USART / SysTick / MPU
- [ ] 我能把所有 Part 2 的概念串起來在實機跑通
- [ ] 我能用 GDB 抓 NULL pointer write 觸發 MemManage 的 PC

下一個 Part 進 Cortex-A — Exception Level、MMU、cache、TrustZone。世界完全不一樣。

→ [Ch 15 A profile 處理器模型：Exception Level 0–3](./15-exception-levels.md)
