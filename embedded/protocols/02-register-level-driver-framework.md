# Ch 2 — Register-Level 驅動框架

> 目標：建立一套可複用的 register-level 驅動結構，理解 ESP-IDF 的暫存器標頭檔組織，掌握 peripheral 初始化的標準流程、中斷掛載方式、以及 DMA 的基本概念。用 GPIO 輸出 toggle 作為完整範例。

---

## 三層驅動結構

```
+--------------------------------------------------+
|            應用層 (Application Layer)             |
|   app_main(), tasks, business logic              |
|   呼叫驅動 API，不直接碰硬體                       |
+--------------------------------------------------+
              |               |
              v               v
+--------------------------------------------------+
|           驅動 API 層 (Driver API Layer)          |
|   spi_init(), spi_transfer(), i2c_read()         |
|   封裝暫存器操作，提供單一職責的函式               |
|   錯誤回傳用 esp_err_t，timeout 用 FreeRTOS tick  |
+--------------------------------------------------+
              |               |
              v               v
+--------------------------------------------------+
|        暫存器抽象層 (Register Abstraction Layer)   |
|   REG_READ / REG_WRITE / SET_PERI_REG_BITS       |
|   soc/spi_reg.h, soc/i2c_reg.h, soc/gpio_reg.h  |
|   定義 base address 和 bit field 名稱             |
+--------------------------------------------------+
              |
              v
+--------------------------------------------------+
|              硬體 (Hardware)                      |
|   ESP32 SPI2 controller, I2C0 controller, ...    |
+--------------------------------------------------+
```

這三層分離的好處：改 pinout 只改暫存器抽象層，改協議邏輯只改驅動 API 層，應用層完全不動。

---

## ESP-IDF 暫存器標頭檔組織

ESP-IDF v5.x 的暫存器定義在：

```
components/soc/esp32/include/soc/
  spi_reg.h       -- SPI0/1/2/3 暫存器 offset 和 bit field
  i2c_reg.h       -- I2C0/1 暫存器
  gpio_reg.h      -- GPIO output/input/enable 暫存器
  gpio_sig_map.h  -- GPIO matrix 信號編號
  uart_reg.h      -- UART0/1/2 暫存器
  dport_reg.h     -- 系統 clock/reset 控制
  soc.h           -- base address 和通用 macro
```

每個 `*_reg.h` 的命名規則是固定的，以 `spi_reg.h` 為例：

```c
// 暫存器位址 = SPI_CMD_REG(i)，i 是 controller 編號（0-3）
#define SPI_CMD_REG(i)    (REG_SPI_BASE(i) + 0x000)

// Bit field 名稱：SPI_<欄位名>
#define SPI_USR           (BIT(18))   // 啟動傳輸

// Bit field 帶 shift：SPI_<欄位名>_S（shift 量）、SPI_<欄位名>_V（mask 值）
#define SPI_CLKCNT_N      0xFF        // mask
#define SPI_CLKCNT_N_S    12          // shift（bit 19:12）
```

查一個欄位的方式：
1. 先在 TRM（Technical Reference Manual，ESP32 TRM on espressif.com）找暫存器名和 bit field
2. 在對應的 `*_reg.h` 搜尋同名的 `#define`
3. 確認 `_V`（value mask）和 `_S`（shift）都存在

---

## Peripheral 初始化標準流程

以 GPIO 為例，完整的 4 步驟：

### 步驟 1：Reset Peripheral

把 peripheral 打到 reset 狀態，清除任何殘留設定：

```c
// DPORT_PERIP_RST_EN_REG：寫 1 = 進 reset
SET_PERI_REG_MASK(DPORT_PERIP_RST_EN_REG, DPORT_GPIO_RST);
// 短暫等待（幾個 clock cycle 就夠）
__asm__ __volatile__("nop; nop; nop; nop;");
CLEAR_PERI_REG_MASK(DPORT_PERIP_RST_EN_REG, DPORT_GPIO_RST);
```

GPIO 在 ESP32 上其實不需要 reset（boot 後已在已知狀態），但 SPI、I2C 這些在上電後狀態未定，一定要做。

### 步驟 2：開 Clock

```c
// DPORT_PERIP_CLK_EN_REG：寫 1 = 開 clock
SET_PERI_REG_MASK(DPORT_PERIP_CLK_EN_REG, DPORT_GPIO_CLK_EN);
```

### 步驟 3：設 GPIO Matrix Routing

把 peripheral 信號接到實體 pad：

```c
// 設定 GPIO pad 的 drive strength 和 pull（IO_MUX）
// IO_MUX_GPIO2_REG：設 function 為 GPIO matrix 模式（func 2）
REG_WRITE(IO_MUX_GPIO2_REG,
          (2 << MCU_SEL_S) |     // 選 GPIO matrix（func 2）
          (2 << FUN_DRV_S) |     // drive strength = 中等
          FUN_IE);               // 如果要輸入，開 input enable

// GPIO matrix routing：GPIO2 輸出由 SIG_GPIO_OUT_IDX 控制
// SIG_GPIO_OUT_IDX = 256，表示「由 GPIO_OUT_REG 直接控制」
REG_WRITE(GPIO_FUNC2_OUT_SEL_CFG_REG, SIG_GPIO_OUT_IDX);
```

### 步驟 4：設定暫存器

這步才是 peripheral 的實際設定，每個 peripheral 不同，後續章節細說。

---

## Interrupt 掛載

ESP-IDF 的中斷分配用 `esp_intr_alloc`（這個 API 可以用，它本身不是 HAL protocol driver）：

```c
#include "esp_intr_alloc.h"
#include "soc/spi_reg.h"

static intr_handle_t spi2_intr_handle;

static void IRAM_ATTR spi2_isr(void *arg)
{
    // 1. 讀中斷狀態暫存器
    uint32_t status = REG_READ(SPI_SLAVE_REG(2));

    // 2. 清除中斷（先清才能收下一個）
    REG_WRITE(SPI_SLAVE_REG(2), status & ~SPI_TRANS_DONE);

    // 3. 讀 FIFO / 做業務邏輯
    uint32_t data = REG_READ(SPI_W0_REG(2));
    // ... 處理資料，通知 task
}

void spi2_interrupt_init(void)
{
    // ETS_SPI2_INTR_SOURCE：SPI2 的中斷來源編號
    // ESP_INTR_FLAG_IRAM：handler 在 IRAM，flash cache miss 不影響
    esp_intr_alloc(ETS_SPI2_INTR_SOURCE,
                   ESP_INTR_FLAG_IRAM,
                   spi2_isr,
                   NULL,
                   &spi2_intr_handle);
}
```

ISR 的鐵律：
- 加 `IRAM_ATTR`，放 IRAM，否則 flash cache miss 可能導致 WDT 觸發
- 盡快離開，耗時操作用 `xQueueSendFromISR` 交給 task
- 清中斷 flag 要在讀資料之前（或同時），否則可能漏中斷

---

## DMA 概念

為什麼 SPI、I2C 傳大量資料要用 DMA？

```
無 DMA（CPU polling）：
  CPU:  寫TX -> 等 -> 寫TX -> 等 -> 寫TX -> 等 ...
        (CPU 被 peripheral 速度綁住，無法做其他事)

有 DMA：
  CPU:  設定 descriptor -> 啟動 -> 做其他事 ...
  DMA:  自動搬 TX buffer -> peripheral FIFO
  中斷: 傳輸完成 -> 通知 CPU
```

ESP32 的 DMA controller（稱為 GDMA 或 SPI DMA）用 linked list descriptor 描述要搬的資料：

```c
// DMA descriptor 結構（ESP32 SPI DMA 用，4-byte aligned）
typedef struct {
    uint32_t size    : 12;  // buffer 大小（bytes）
    uint32_t length  : 12;  // 實際有效資料長度
    uint32_t reserved: 6;
    uint32_t eof     : 1;   // 1 = 這是最後一個 descriptor
    uint32_t owner   : 1;   // 1 = DMA 擁有（不能 CPU 存取）
    uint8_t  *buf;          // 指向資料 buffer
    void     *next;         // 下一個 descriptor（NULL = 結束）
} dma_descriptor_t;
```

使用 DMA 的注意事項：
- Buffer 必須 4-byte aligned，且要在 DMA 可存取的記憶體範圍（不能用 stack！）
- `owner = 1` 期間 CPU 不能碰 buffer，等 DMA 做完（owner 回 0）才能讀
- 傳輸完成看 SPI_IN_SUC_EOF_INT 或 SPI_TRANS_DONE_INT

Ch 0-5 用 polling 方式（不用 DMA），Ch 8 的 I2C BME280 範例再引入 DMA。

---

## 完整範例：Register-Level GPIO Output Toggle

從 clock enable 到 GPIO bit 操作，一行 HAL 都不用：

```c
#include <stdint.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "soc/dport_reg.h"
#include "soc/gpio_reg.h"
#include "soc/io_mux_reg.h"

#define MY_GPIO   2   // 使用 GPIO2

static void gpio_output_init(uint32_t gpio_num)
{
    // Step 1: GPIO peripheral clock（其實 GPIO 時鐘在 boot 後就開著，
    //         但良好習慣還是明確開啟）
    SET_PERI_REG_MASK(DPORT_PERIP_CLK_EN_REG, DPORT_GPIO_CLK_EN);
    CLEAR_PERI_REG_MASK(DPORT_PERIP_RST_EN_REG, DPORT_GPIO_RST);

    // Step 2: IO_MUX 設定這個 pad 為 GPIO matrix 模式
    // IO_MUX_GPIO2_REG：MCU_SEL=2（GPIO matrix），DRV=2（12 mA），不開 pull
    REG_WRITE(GPIO_PIN_MUX_REG[gpio_num],
              (2u << MCU_SEL_S) |
              (2u << FUN_DRV_S));

    // Step 3: GPIO matrix 輸出源 = SIG_GPIO_OUT_IDX（即 GPIO_OUT_REG 直接控制）
    REG_WRITE(GPIO_FUNC0_OUT_SEL_CFG_REG + (gpio_num * 4),
              SIG_GPIO_OUT_IDX);

    // Step 4: 開啟輸出 enable（GPIO_ENABLE_REG，bit N = GPIO N）
    if (gpio_num < 32) {
        REG_SET_BIT(GPIO_ENABLE_REG, (1u << gpio_num));
    } else {
        REG_SET_BIT(GPIO_ENABLE1_REG, (1u << (gpio_num - 32)));
    }
}

static inline void gpio_set(uint32_t gpio_num)
{
    // W1TS：Write-1-to-Set，atomic，不需要 read-modify-write
    if (gpio_num < 32) {
        REG_WRITE(GPIO_OUT_W1TS_REG, (1u << gpio_num));
    } else {
        REG_WRITE(GPIO_OUT1_W1TS_REG, (1u << (gpio_num - 32)));
    }
}

static inline void gpio_clear(uint32_t gpio_num)
{
    // W1TC：Write-1-to-Clear，atomic
    if (gpio_num < 32) {
        REG_WRITE(GPIO_OUT_W1TC_REG, (1u << gpio_num));
    } else {
        REG_WRITE(GPIO_OUT1_W1TC_REG, (1u << (gpio_num - 32)));
    }
}

void app_main(void)
{
    gpio_output_init(MY_GPIO);

    while (1) {
        gpio_set(MY_GPIO);
        vTaskDelay(pdMS_TO_TICKS(500));
        gpio_clear(MY_GPIO);
        vTaskDelay(pdMS_TO_TICKS(500));
    }
}
```

這個範例比 Ch 0 的版本更完整：明確做了 clock enable、IO_MUX 設定、GPIO matrix routing，每一步都有意圖。後續 SPI、I2C 的初始化程式碼會用相同的結構，只是把 Step 4 換成對應 peripheral 的暫存器設定。

---

## 自我檢核

- [ ] 能畫出三層驅動結構圖，說明每層的職責
- [ ] 知道 `soc/spi_reg.h` 裡的命名規則（`_V` 和 `_S` 的意義）
- [ ] 能說出 peripheral 初始化的 4 個步驟，不靠筆記
- [ ] 知道 ISR 為什麼要加 `IRAM_ATTR`
- [ ] 理解 DMA descriptor 的 `owner` bit 的用途
- [ ] 把上面的 GPIO 範例燒進板子，邏輯分析儀驗証方波

框架建好了，接下來正式進入第一個通訊協議：SPI。

→ [Ch 3 SPI 協議原理](./03-spi-protocol.md)
