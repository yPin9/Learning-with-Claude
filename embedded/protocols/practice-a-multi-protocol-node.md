# 練習 A：多協議感測器節點

整合 SPI、I2C、UART 三個協議到同一顆 ESP32 上，同時驅動 BME280 感測器和 W25Q32 SPI Flash，並透過 UART 輸出結構化 log。這道題的重點不是各協議本身，而是三個 peripheral 同時跑時怎麼不互相阻塞。

**前置章節**：Ch 5（SPI）、Ch 8（I2C BME280）、Ch 9（UART）

---

## 硬體需求

| 元件 | 數量 | 接法 |
|---|---|---|
| ESP32 DevKitC | 1 | 主控 |
| BME280 模組 | 1 | I2C：SDA=GPIO21、SCL=GPIO22 |
| W25Q32 SPI Flash | 1 | MOSI=GPIO23、MISO=GPIO19、CLK=GPIO18、CS=GPIO5 |
| USB-UART 連接電腦 | 1 | UART0：TX=GPIO1（預設） |

---

## 題目規格

### 任務一：I2C — BME280 讀取

每 2 秒讀一次 BME280，取得原始溫度和濕度。需要：
- 手動送出 BME280 的校正係數讀取序列（registers 0x88–0x9F、0xE1–0xE7）
- 套用 BME280 datasheet Section 4.2.3 的補償公式（整數版即可，不需浮點 DSP）
- 輸出單位：溫度 °C（float，精度 0.01），濕度 %（float，精度 0.1）

BME280 的 I2C 位址預設為 0x76（SDO 接 GND）。

### 任務二：SPI — W25Q32 Flash 寫入

每次讀完感測器後，把 8 bytes 資料寫入 W25Q32 的 address 0x001000。資料格式：

```
byte[0..3] : uint32_t timestamp（秒，big-endian）
byte[4..5] : int16_t  raw temperature（BME280 compensated × 100，big-endian）
byte[6..7] : uint16_t raw humidity（BME280 compensated × 10，big-endian）
```

寫入流程：
1. 送 Write Enable（0x06）
2. 等 WEL bit（Status Register bit 1）為 1
3. 送 Page Program（0x02）+ 24-bit address + 8 bytes data
4. 輪詢 Status Register（0x05）的 BUSY bit（bit 0），等寫完
5. 讀回 8 bytes 驗證（0x03）

### 任務三：UART — 結構化 log

每 2 秒把一行 log 輸出到 UART0（115200 8N1），格式：

```
[T=NNNNs] Temp=XX.XXC Humi=XX.X% -> Flash[0x001000] OK
```

UART 不能用 `printf`——要自己實作 register-level 輸出（往 UART_FIFO_REG 塞字元）。

### 非阻塞要求

三個任務用三個 FreeRTOS task 跑，不能有一個 task 的阻塞拖垮其他 task。SPI 操作和 I2C 操作不能混用同一個 mutex（各自一把鎖）。

---

## 期望輸出

接上序列埠監視器（115200），應該看到：

```
[T=0002s] Temp=25.34C Humi=61.2% -> Flash[0x001000] OK
[T=0004s] Temp=25.36C Humi=61.1% -> Flash[0x001000] OK
[T=0006s] Temp=25.35C Humi=61.3% -> Flash[0x001000] OK
```

如果 Flash 寫入失敗：

```
[T=0008s] Temp=25.37C Humi=61.0% -> Flash[0x001000] FAIL(WEL=0)
```

---

## 實作步驟

### Step 1：確認 SPI peripheral 初始化

ESP32 有兩個可用的 SPI master host：`HSPI_HOST`（SPI2）和 `VSPI_HOST`（SPI3）。選 VSPI。

需要設定的 SPI 暫存器（base address `0x3FF65000` for VSPI）：

```
SPI_CLOCK_REG    — 設定 SCK 頻率（W25Q32 最高 104 MHz，測試先用 10 MHz）
SPI_USER_REG     — 設定 MOSI/MISO 方向、CS 控制
SPI_CTRL_REG     — byte order（W25Q32 用 MSB first）
SPI_PIN_REG      — CS 極性（active low）
```

W25Q32 用 SPI mode 0（CPOL=0，CPHA=0）。

### Step 2：W25Q32 指令序列

每個指令：拉低 CS → 送指令位元組 → 送參數（如有）→ 送/收資料 → 拉高 CS。

讀 Status Register（0x05）的序列：
```
CS low
TX: 0x05
RX: 1 byte (status)
CS high
```

Page Program（0x02）的序列（address 0x001000，8 bytes）：
```
CS low
TX: 0x02 0x00 0x10 0x00 [8 bytes data]
CS high
```

注意 Page Program 之前必須先完成 Write Enable，且每次 Page Program 後 WEL 自動清零。

### Step 3：I2C BME280 初始化

BME280 上電後預設是 sleep mode。需要：
1. 讀 chip ID（register 0xD0），確認回傳 0x60
2. 寫 0xB6 到 register 0xE0（soft reset）
3. 等約 2ms
4. 讀校正係數（0x88–0x9F 共 26 bytes，0xE1–0xE7 共 7 bytes）
5. 設定 register 0xF2（humidity oversampling）= 0x01（×1）
6. 設定 register 0xF4（ctrl_meas）= 0x27（temp ×1，pressure ×1，normal mode）
7. 設定 register 0xF5（config）= 0xA0（standby 1000ms，filter off）

### Step 4：I2C register-level 發送

ESP32 I2C controller 使用命令佇列（command list）。每次操作要填充最多 16 個 command slot，每個 slot 格式：

```
[31:30] op_code  (0=RSTART, 1=WRITE, 2=READ, 3=STOP)
[29]    ack_check_en
[28]    ack_exp
[27]    ack_val
[23:8]  byte_num
```

一次完整的 I2C register read（single byte）需要的 command 序列：
```
RSTART → WRITE(addr<<1 | 0, ack_check) → WRITE(reg, ack_check)
→ RSTART → WRITE(addr<<1 | 1, ack_check) → READ(1, nack) → STOP
```

### Step 5：FreeRTOS task 架構

```c
// task 優先級配置
#define SENSOR_TASK_PRI   5
#define FLASH_TASK_PRI    4
#define UART_LOG_TASK_PRI 3

// 共享資料用 ringbuffer 或 mutex + struct
typedef struct {
    uint32_t timestamp;
    float    temperature;
    float    humidity;
    bool     flash_ok;
} sensor_record_t;
```

sensor_task 讀完後用 xQueueSend 把 record 推給 uart_task；flash_task 由 sensor_task 在同一個 loop 裡觸發（避免 queue 深度問題）。

---

## 參考解答

<details>
<summary>點開參考實作</summary>

```c
/*
 * practice_a_multi_protocol_node.c
 *
 * 硬體接線：
 *   BME280  SDA=GPIO21  SCL=GPIO22  (I2C addr 0x76)
 *   W25Q32  MOSI=GPIO23 MISO=GPIO19 CLK=GPIO18 CS=GPIO5
 *   UART0   TX=GPIO1 (預設，接 USB-UART)
 *
 * 編譯：ESP-IDF v5.x，idf.py build
 */

#include <stdint.h>
#include <string.h>
#include <stdbool.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "freertos/queue.h"
#include "driver/gpio.h"

/* ================================================================
 * 暫存器基底位址
 * ================================================================ */

/* VSPI (SPI3) — ESP32 TRM Table 26 */
#define SPI3_BASE           0x3FF65000UL
#define SPI_CMD_REG(b)      (*(volatile uint32_t *)((b) + 0x00))
#define SPI_ADDR_REG(b)     (*(volatile uint32_t *)((b) + 0x04))
#define SPI_CTRL_REG(b)     (*(volatile uint32_t *)((b) + 0x08))
#define SPI_CTRL1_REG(b)    (*(volatile uint32_t *)((b) + 0x0C))
#define SPI_RD_STATUS_REG(b)(*(volatile uint32_t *)((b) + 0x10))
#define SPI_CTRL2_REG(b)    (*(volatile uint32_t *)((b) + 0x14))
#define SPI_CLOCK_REG(b)    (*(volatile uint32_t *)((b) + 0x18))
#define SPI_USER_REG(b)     (*(volatile uint32_t *)((b) + 0x1C))
#define SPI_USER1_REG(b)    (*(volatile uint32_t *)((b) + 0x20))
#define SPI_USER2_REG(b)    (*(volatile uint32_t *)((b) + 0x24))
#define SPI_MOSI_DLEN_REG(b)(*(volatile uint32_t *)((b) + 0x28))
#define SPI_MISO_DLEN_REG(b)(*(volatile uint32_t *)((b) + 0x2C))
#define SPI_PIN_REG(b)      (*(volatile uint32_t *)((b) + 0x34))
#define SPI_W0_REG(b)       (*(volatile uint32_t *)((b) + 0x80))
#define SPI_CMD_USR         (1UL << 18)

/* I2C0 — ESP32 TRM Table 30 */
#define I2C0_BASE           0x3FF53000UL
#define I2C_SCL_LOW_PERIOD_REG(b)   (*(volatile uint32_t *)((b) + 0x00))
#define I2C_CTR_REG(b)              (*(volatile uint32_t *)((b) + 0x04))
#define I2C_SR_REG(b)               (*(volatile uint32_t *)((b) + 0x08))
#define I2C_TO_REG(b)               (*(volatile uint32_t *)((b) + 0x0C))
#define I2C_SLAVE_ADDR_REG(b)       (*(volatile uint32_t *)((b) + 0x10))
#define I2C_RXFIFO_ST_REG(b)        (*(volatile uint32_t *)((b) + 0x14))
#define I2C_FIFO_CONF_REG(b)        (*(volatile uint32_t *)((b) + 0x18))
#define I2C_FIFO_DATA_REG(b)        (*(volatile uint32_t *)((b) + 0x1C))
#define I2C_INT_RAW_REG(b)          (*(volatile uint32_t *)((b) + 0x20))
#define I2C_INT_CLR_REG(b)          (*(volatile uint32_t *)((b) + 0x24))
#define I2C_INT_ENA_REG(b)          (*(volatile uint32_t *)((b) + 0x28))
#define I2C_INT_STATUS_REG(b)       (*(volatile uint32_t *)((b) + 0x2C))
#define I2C_SCL_HIGH_PERIOD_REG(b)  (*(volatile uint32_t *)((b) + 0x38))
#define I2C_SCL_START_HOLD_REG(b)   (*(volatile uint32_t *)((b) + 0x40))
#define I2C_SCL_RSTART_SETUP_REG(b) (*(volatile uint32_t *)((b) + 0x44))
#define I2C_SCL_STOP_HOLD_REG(b)    (*(volatile uint32_t *)((b) + 0x48))
#define I2C_SCL_STOP_SETUP_REG(b)   (*(volatile uint32_t *)((b) + 0x4C))
#define I2C_SCL_FILTER_CFG_REG(b)   (*(volatile uint32_t *)((b) + 0x50))
#define I2C_SDA_FILTER_CFG_REG(b)   (*(volatile uint32_t *)((b) + 0x54))
#define I2C_COMD0_REG(b)            (*(volatile uint32_t *)((b) + 0x58))

/* UART0 */
#define UART0_BASE          0x3FF40000UL
#define UART_FIFO_REG(b)    (*(volatile uint32_t *)((b) + 0x00))
#define UART_STATUS_REG(b)  (*(volatile uint32_t *)((b) + 0x1C))
#define UART_TXFIFO_CNT_M   0x000000FFUL
#define UART_TXFIFO_CNT_S   16

/* DPORT / 時鐘使能 */
#define DPORT_PERIP_CLK_EN_REG  (*(volatile uint32_t *)0x3FF0001CUL)
#define DPORT_PERIP_RST_EN_REG  (*(volatile uint32_t *)0x3FF00020UL)
#define DPORT_SPI3_CLK_EN   (1UL << 27)  /* VSPI */
#define DPORT_I2C0_CLK_EN   (1UL << 7)

/* GPIO IOMUX — 只需設 output/input enable，這裡用 GPIO matrix */
#define GPIO_OUT_REG        (*(volatile uint32_t *)0x3FF44004UL)
#define GPIO_OUT_W1TS_REG   (*(volatile uint32_t *)0x3FF44008UL)
#define GPIO_OUT_W1TC_REG   (*(volatile uint32_t *)0x3FF4400CUL)
#define GPIO_ENABLE_REG     (*(volatile uint32_t *)0x3FF44020UL)
#define GPIO_ENABLE_W1TS    (*(volatile uint32_t *)0x3FF44024UL)
#define GPIO_IN_REG         (*(volatile uint32_t *)0x3FF4403CUL)
#define GPIO_FUNC_OUT_SEL(n)(*(volatile uint32_t *)(0x3FF44530UL + (n)*4))
#define GPIO_FUNC_IN_SEL(n) (*(volatile uint32_t *)(0x3FF44130UL + (n)*4))

/* ================================================================
 * SPI (VSPI) 驅動 — W25Q32
 * ================================================================ */

#define W25Q32_CS_PIN   5
#define W25Q32_CLK_PIN  18
#define W25Q32_MOSI_PIN 23
#define W25Q32_MISO_PIN 19

/* VSPI signal index（GPIO matrix） */
#define VSPI_CLK_OUT_IDX   63
#define VSPI_MOSI_OUT_IDX  100
#define VSPI_MISO_IN_IDX   101
#define VSPI_CS0_OUT_IDX   102

static SemaphoreHandle_t g_spi_mutex;

static void spi_init(void)
{
    /* 時鐘使能 */
    DPORT_PERIP_CLK_EN_REG |= DPORT_SPI3_CLK_EN;
    DPORT_PERIP_RST_EN_REG &= ~DPORT_SPI3_CLK_EN;

    /* GPIO matrix：VSPI 信號路由 */
    /* CLK output */
    GPIO_FUNC_OUT_SEL(W25Q32_CLK_PIN)  = VSPI_CLK_OUT_IDX;
    GPIO_ENABLE_W1TS = (1UL << W25Q32_CLK_PIN);
    /* MOSI output */
    GPIO_FUNC_OUT_SEL(W25Q32_MOSI_PIN) = VSPI_MOSI_OUT_IDX;
    GPIO_ENABLE_W1TS = (1UL << W25Q32_MOSI_PIN);
    /* MISO input */
    GPIO_FUNC_IN_SEL(VSPI_MISO_IN_IDX) = W25Q32_MISO_PIN;
    /* CS：用軟體 GPIO 控制（不走 SPI CS 自動模式，方便多 byte 指令） */
    GPIO_FUNC_OUT_SEL(W25Q32_CS_PIN)   = 0x100; /* GPIO_MATRIX_CONST_ONE = software GPIO */
    GPIO_ENABLE_W1TS = (1UL << W25Q32_CS_PIN);
    GPIO_OUT_W1TS_REG = (1UL << W25Q32_CS_PIN); /* 預設 CS 高 */

    /* SPI clock：APB=80MHz，pre-divider=8 → 10MHz */
    /* SPI_CLOCK_REG: [5:0]=clkcnt_L, [11:6]=clkcnt_H, [17:12]=clkcnt_N, [18]=clkdiv_pre */
    /* 80 MHz / (8+1) / (1+1) ≈ 4.4 MHz，保守設定 */
    SPI_CLOCK_REG(SPI3_BASE) = (1UL << 31) | /* clk_equ_sysclk=0 */
                                (7UL << 18)  | /* clkdiv_pre = 7 (÷8) */
                                (1UL << 12)  | /* clkcnt_N = 1 (÷2) */
                                (0UL << 6)   | /* clkcnt_H = 0 */
                                (1UL);         /* clkcnt_L = 1 */

    /* SPI mode 0：CPOL=0 CPHA=0 */
    SPI_PIN_REG(SPI3_BASE)  &= ~((1UL << 29) | (1UL << 8)); /* CK_IDLE_EDGE=0, CK_OUT_EDGE=0 */

    /* USER_REG：MSB first，全雙工，不用 address phase（手動把 cmd+addr 都放 MOSI） */
    SPI_USER_REG(SPI3_BASE) = (1UL << 27) | /* USR_MOSI */
                               (1UL << 28);  /* USR_MISO */

    /* CTRL_REG：WR/RD bit order = MSB first（預設，bit 25/26 = 0） */
    SPI_CTRL_REG(SPI3_BASE) = 0;

    g_spi_mutex = xSemaphoreCreateMutex();
}

/* 低階：送 tx_len bytes（tx_buf），收 rx_len bytes（rx_buf）
   CS 由呼叫者控制（cs_low/cs_high） */
static void spi_transfer(const uint8_t *tx_buf, int tx_len,
                          uint8_t *rx_buf,       int rx_len)
{
    int total = tx_len + rx_len;
    /* 把所有要送的 byte 組進 W0–W15（max 64 bytes） */
    uint32_t words[(64 / 4) + 1] = {0};
    for (int i = 0; i < tx_len; i++) {
        words[i / 4] |= ((uint32_t)tx_buf[i]) << ((i % 4) * 8);
    }
    /* 填入 tx data word */
    volatile uint32_t *w_reg = &SPI_W0_REG(SPI3_BASE);
    for (int i = 0; i < (total + 3) / 4; i++) {
        w_reg[i] = words[i];
    }

    SPI_MOSI_DLEN_REG(SPI3_BASE) = (total * 8) - 1;
    SPI_MISO_DLEN_REG(SPI3_BASE) = (total * 8) - 1;
    SPI_CMD_REG(SPI3_BASE) |= SPI_CMD_USR;
    while (SPI_CMD_REG(SPI3_BASE) & SPI_CMD_USR) {}

    /* 把收到的 bytes 讀出（rx_buf 對應 tx_len 之後的部分） */
    if (rx_buf && rx_len > 0) {
        for (int i = 0; i < rx_len; i++) {
            int idx = tx_len + i;
            rx_buf[i] = (uint8_t)(w_reg[idx / 4] >> ((idx % 4) * 8));
        }
    }
}

static inline void cs_low(void)  { GPIO_OUT_W1TC_REG = (1UL << W25Q32_CS_PIN); }
static inline void cs_high(void) { GPIO_OUT_W1TS_REG = (1UL << W25Q32_CS_PIN); }

/* W25Q32：讀 Status Register */
static uint8_t w25q32_read_status(void)
{
    uint8_t cmd = 0x05, status = 0;
    cs_low();
    spi_transfer(&cmd, 1, &status, 1);
    cs_high();
    return status;
}

/* W25Q32：Write Enable */
static void w25q32_write_enable(void)
{
    uint8_t cmd = 0x06;
    cs_low();
    spi_transfer(&cmd, 1, NULL, 0);
    cs_high();
    /* 等 WEL bit */
    uint32_t timeout = 1000;
    while (!(w25q32_read_status() & 0x02) && timeout--) {
        vTaskDelay(pdMS_TO_TICKS(1));
    }
}

/* W25Q32：等 BUSY 清零 */
static bool w25q32_wait_busy(uint32_t timeout_ms)
{
    while (timeout_ms--) {
        if (!(w25q32_read_status() & 0x01)) return true;
        vTaskDelay(pdMS_TO_TICKS(1));
    }
    return false;
}

/* W25Q32：Page Program，addr 必須是 page-aligned（256B 邊界） */
static bool w25q32_page_program(uint32_t addr, const uint8_t *data, uint8_t len)
{
    if (len == 0 || len > 8) return false;

    w25q32_write_enable();

    uint8_t cmd[4 + 8];
    cmd[0] = 0x02;
    cmd[1] = (addr >> 16) & 0xFF;
    cmd[2] = (addr >> 8)  & 0xFF;
    cmd[3] = (addr)       & 0xFF;
    memcpy(&cmd[4], data, len);

    cs_low();
    spi_transfer(cmd, 4 + len, NULL, 0);
    cs_high();

    return w25q32_wait_busy(50);
}

/* W25Q32：讀資料 */
static void w25q32_read_data(uint32_t addr, uint8_t *buf, uint8_t len)
{
    uint8_t cmd[4];
    cmd[0] = 0x03;
    cmd[1] = (addr >> 16) & 0xFF;
    cmd[2] = (addr >> 8)  & 0xFF;
    cmd[3] = (addr)       & 0xFF;
    cs_low();
    spi_transfer(cmd, 4, buf, len);
    cs_high();
}

/* ================================================================
 * I2C0 驅動 — BME280
 * ================================================================ */

#define BME280_I2C_ADDR  0x76
#define I2C_APB_CLK_HZ   80000000UL
#define I2C_FREQ_HZ      400000UL   /* Fast mode */

/* I2C command op codes */
#define I2C_OP_RSTART  0
#define I2C_OP_WRITE   1
#define I2C_OP_READ    2
#define I2C_OP_STOP    3

#define I2C_CMD(op, ack_en, ack_exp, ack_val, bnum) \
    (((uint32_t)(op) << 11) | \
     ((ack_en)  ? (1UL << 10) : 0) | \
     ((ack_exp) ? (1UL << 9)  : 0) | \
     ((ack_val) ? (1UL << 8)  : 0) | \
     ((bnum) & 0xFF))

static SemaphoreHandle_t g_i2c_mutex;

/* I2C 校正係數 */
typedef struct {
    uint16_t dig_T1; int16_t dig_T2; int16_t dig_T3;
    uint16_t dig_H1; int16_t dig_H2; uint8_t dig_H3;
    int16_t  dig_H4; int16_t dig_H5; int8_t  dig_H6;
} bme280_calib_t;

static bme280_calib_t g_bme280_calib;
static int32_t        g_t_fine;

static void i2c_init(void)
{
    DPORT_PERIP_CLK_EN_REG |= DPORT_I2C0_CLK_EN;
    DPORT_PERIP_RST_EN_REG &= ~DPORT_I2C0_CLK_EN;

    /* GPIO matrix：SDA=GPIO21，SCL=GPIO22 */
    /* I2C SDA signal index = 30(out) / 31(in)，SCL = 29(out) / 29(in) */
    /* 設定 open-drain + pull-up（用 GPIO_PIN_REG） */
    #define GPIO_PIN_REG(n) (*(volatile uint32_t *)(0x3FF44088UL + (n)*4))
    GPIO_PIN_REG(21) |= (1UL << 2); /* pad_driver = 1 (open drain) */
    GPIO_PIN_REG(22) |= (1UL << 2);
    /* 路由 */
    GPIO_FUNC_OUT_SEL(21) = 30;  /* I2C_SDA_OUT_IDX */
    GPIO_FUNC_OUT_SEL(22) = 29;  /* I2C_SCL_OUT_IDX */
    GPIO_FUNC_IN_SEL(31)  = 21;  /* I2C_SDA_IN_IDX */
    GPIO_FUNC_IN_SEL(29)  = 22;  /* I2C_SCL_IN_IDX */
    GPIO_ENABLE_W1TS = (1UL << 21) | (1UL << 22);

    /* 400kHz timing（APB=80MHz）
       period = APB / freq = 200 cycles total
       SCL_LOW ≈ 125 cycles，SCL_HIGH ≈ 62 cycles（不對稱，見 I2C spec） */
    I2C_SCL_LOW_PERIOD_REG(I2C0_BASE)   = 125;
    I2C_SCL_HIGH_PERIOD_REG(I2C0_BASE)  = 62;
    I2C_SCL_START_HOLD_REG(I2C0_BASE)   = 30;
    I2C_SCL_RSTART_SETUP_REG(I2C0_BASE) = 30;
    I2C_SCL_STOP_HOLD_REG(I2C0_BASE)    = 30;
    I2C_SCL_STOP_SETUP_REG(I2C0_BASE)   = 30;
    /* glitch filter */
    I2C_SCL_FILTER_CFG_REG(I2C0_BASE)   = (1UL << 4) | 7;
    I2C_SDA_FILTER_CFG_REG(I2C0_BASE)   = (1UL << 4) | 7;

    /* CTR：master mode，MSB first，non-fifo mode */
    I2C_CTR_REG(I2C0_BASE) = (1UL << 4)  | /* ms_mode=1 (master) */
                               (1UL << 6)  | /* sda_force_out=1 */
                               (1UL << 7);   /* scl_force_out=1 */

    /* timeout = 32000 APB cycles */
    I2C_TO_REG(I2C0_BASE) = 32000;

    /* clear FIFO */
    I2C_FIFO_CONF_REG(I2C0_BASE) |= (1UL << 10) | (1UL << 12);
    I2C_FIFO_CONF_REG(I2C0_BASE) &= ~((1UL << 10) | (1UL << 12));

    g_i2c_mutex = xSemaphoreCreateMutex();
}

/* 等 I2C trans_start 完成（INT_RAW bit 7 = trans_complete 或 bit 1 = arbitration_lost） */
static bool i2c_wait_done(uint32_t timeout_ms)
{
    uint32_t t = timeout_ms * 1000; /* 粗略迴圈 */
    while (t--) {
        uint32_t raw = I2C_INT_RAW_REG(I2C0_BASE);
        if (raw & (1UL << 7)) {      /* trans_complete */
            I2C_INT_CLR_REG(I2C0_BASE) = raw;
            return true;
        }
        if (raw & (1UL << 1)) {      /* arb_lost */
            I2C_INT_CLR_REG(I2C0_BASE) = raw;
            return false;
        }
        /* 每 100 迴圈讓出一次 */
        if ((t % 1000) == 0) vTaskDelay(1);
    }
    return false;
}

/* I2C write：寫 wbuf[0..wlen-1] 到 slave addr 的某 register */
static bool i2c_write_reg(uint8_t addr, uint8_t reg, const uint8_t *data, uint8_t dlen)
{
    volatile uint32_t *cmd = &I2C_COMD0_REG(I2C0_BASE);
    int slot = 0;

    /* FIFO：先塞 addr+W，再塞 reg，再塞 data */
    I2C_FIFO_DATA_REG(I2C0_BASE) = (addr << 1) | 0; /* write */
    I2C_FIFO_DATA_REG(I2C0_BASE) = reg;
    for (int i = 0; i < dlen; i++) {
        I2C_FIFO_DATA_REG(I2C0_BASE) = data[i];
    }

    cmd[slot++] = I2C_CMD(I2C_OP_RSTART, 0, 0, 0, 0);
    cmd[slot++] = I2C_CMD(I2C_OP_WRITE,  1, 0, 0, 2 + dlen); /* addr + reg + data */
    cmd[slot++] = I2C_CMD(I2C_OP_STOP,   0, 0, 0, 0);

    I2C_INT_CLR_REG(I2C0_BASE) = 0xFFFFFFFF;
    I2C_CTR_REG(I2C0_BASE) |= (1UL << 5); /* trans_start */

    return i2c_wait_done(10);
}

/* I2C read：從 slave addr 的 reg 讀 rlen bytes 到 rbuf */
static bool i2c_read_reg(uint8_t addr, uint8_t reg, uint8_t *rbuf, uint8_t rlen)
{
    volatile uint32_t *cmd = &I2C_COMD0_REG(I2C0_BASE);
    int slot = 0;

    I2C_FIFO_DATA_REG(I2C0_BASE) = (addr << 1) | 0; /* write phase：送 reg addr */
    I2C_FIFO_DATA_REG(I2C0_BASE) = reg;
    I2C_FIFO_DATA_REG(I2C0_BASE) = (addr << 1) | 1; /* read phase */

    cmd[slot++] = I2C_CMD(I2C_OP_RSTART, 0, 0, 0, 0);
    cmd[slot++] = I2C_CMD(I2C_OP_WRITE,  1, 0, 0, 2); /* addr+reg */
    cmd[slot++] = I2C_CMD(I2C_OP_RSTART, 0, 0, 0, 0);
    cmd[slot++] = I2C_CMD(I2C_OP_WRITE,  1, 0, 0, 1); /* addr+R */
    if (rlen > 1) {
        cmd[slot++] = I2C_CMD(I2C_OP_READ, 1, 0, 0, rlen - 1); /* ACK 前 rlen-1 bytes */
    }
    cmd[slot++] = I2C_CMD(I2C_OP_READ,   1, 0, 1, 1); /* 最後一 byte：NACK */
    cmd[slot++] = I2C_CMD(I2C_OP_STOP,   0, 0, 0, 0);

    I2C_INT_CLR_REG(I2C0_BASE) = 0xFFFFFFFF;
    I2C_CTR_REG(I2C0_BASE) |= (1UL << 5);

    if (!i2c_wait_done(10)) return false;

    /* 從 RX FIFO 讀出 */
    for (int i = 0; i < rlen; i++) {
        rbuf[i] = (uint8_t)(I2C_FIFO_DATA_REG(I2C0_BASE) & 0xFF);
    }
    return true;
}

/* BME280 補償公式（整數版，從 datasheet 4.2.3 移植） */
static int32_t bme280_compensate_temp(int32_t adc_T)
{
    int32_t var1, var2;
    var1 = ((((adc_T >> 3) - ((int32_t)g_bme280_calib.dig_T1 << 1))) *
             ((int32_t)g_bme280_calib.dig_T2)) >> 11;
    var2 = (((((adc_T >> 4) - ((int32_t)g_bme280_calib.dig_T1)) *
              ((adc_T >> 4) - ((int32_t)g_bme280_calib.dig_T1))) >> 12) *
             ((int32_t)g_bme280_calib.dig_T3)) >> 14;
    g_t_fine = var1 + var2;
    return (g_t_fine * 5 + 128) >> 8; /* 單位：0.01 °C */
}

static uint32_t bme280_compensate_humi(int32_t adc_H)
{
    int32_t v;
    v = (g_t_fine - 76800);
    v = (((((adc_H << 14) - (((int32_t)g_bme280_calib.dig_H4) << 20) -
             (((int32_t)g_bme280_calib.dig_H5) * v)) + (16384)) >> 15) *
          (((((((v * ((int32_t)g_bme280_calib.dig_H6)) >> 10) *
               (((v * ((int32_t)g_bme280_calib.dig_H3)) >> 11) + (32768))) >> 10) +
              (2097152)) * ((int32_t)g_bme280_calib.dig_H2) + 8192) >> 14));
    v = (v - (((((v >> 15) * (v >> 15)) >> 7) * ((int32_t)g_bme280_calib.dig_H1)) >> 4));
    v = (v < 0 ? 0 : v);
    v = (v > 419430400 ? 419430400 : v);
    return (uint32_t)(v >> 12); /* 單位：1/1024 %RH */
}

static bool bme280_init(void)
{
    uint8_t chip_id = 0;
    uint8_t buf[26];

    xSemaphoreTake(g_i2c_mutex, portMAX_DELAY);

    /* 確認 chip ID */
    i2c_read_reg(BME280_I2C_ADDR, 0xD0, &chip_id, 1);
    if (chip_id != 0x60) {
        xSemaphoreGive(g_i2c_mutex);
        return false;
    }

    /* soft reset */
    uint8_t rst = 0xB6;
    i2c_write_reg(BME280_I2C_ADDR, 0xE0, &rst, 1);
    vTaskDelay(pdMS_TO_TICKS(5));

    /* 讀 trim T1-T3，P1-P9 (0x88–0x9F) */
    i2c_read_reg(BME280_I2C_ADDR, 0x88, buf, 26);
    g_bme280_calib.dig_T1 = (uint16_t)(buf[1] << 8 | buf[0]);
    g_bme280_calib.dig_T2 = (int16_t) (buf[3] << 8 | buf[2]);
    g_bme280_calib.dig_T3 = (int16_t) (buf[5] << 8 | buf[4]);

    /* 讀 trim H (0xE1–0xE7) */
    i2c_read_reg(BME280_I2C_ADDR, 0xA1, buf, 1);
    g_bme280_calib.dig_H1 = buf[0];
    i2c_read_reg(BME280_I2C_ADDR, 0xE1, buf, 7);
    g_bme280_calib.dig_H2 = (int16_t)(buf[1] << 8 | buf[0]);
    g_bme280_calib.dig_H3 = buf[2];
    g_bme280_calib.dig_H4 = (int16_t)(((int8_t)buf[3] << 4) | (buf[4] & 0x0F));
    g_bme280_calib.dig_H5 = (int16_t)(((int8_t)buf[5] << 4) | (buf[4] >> 4));
    g_bme280_calib.dig_H6 = (int8_t)buf[6];

    /* 設定 oversampling 和 mode */
    uint8_t hum_os  = 0x01; /* humidity ×1 */
    uint8_t meas_os = 0x27; /* temp×1, pres×1, normal mode */
    uint8_t config  = 0xA0; /* standby 1000ms, filter off */
    i2c_write_reg(BME280_I2C_ADDR, 0xF2, &hum_os,  1);
    i2c_write_reg(BME280_I2C_ADDR, 0xF4, &meas_os, 1);
    i2c_write_reg(BME280_I2C_ADDR, 0xF5, &config,  1);

    xSemaphoreGive(g_i2c_mutex);
    return true;
}

static bool bme280_read(float *temp_c, float *humi_pct)
{
    uint8_t raw[8];

    xSemaphoreTake(g_i2c_mutex, portMAX_DELAY);
    bool ok = i2c_read_reg(BME280_I2C_ADDR, 0xF7, raw, 8);
    xSemaphoreGive(g_i2c_mutex);

    if (!ok) return false;

    /* raw[3..5] = temp (20-bit)，raw[6..7] = humidity (16-bit) */
    int32_t adc_T = ((int32_t)raw[3] << 12) | ((int32_t)raw[4] << 4) | (raw[5] >> 4);
    int32_t adc_H = ((int32_t)raw[6] << 8)  | raw[7];

    int32_t  t = bme280_compensate_temp(adc_T);
    uint32_t h = bme280_compensate_humi(adc_H);

    *temp_c   = (float)t / 100.0f;
    *humi_pct = (float)h / 1024.0f;
    return true;
}

/* ================================================================
 * UART0 register-level 輸出
 * ================================================================ */

/* UART0 TX FIFO 深度 = 128 bytes */
static void uart_putchar(char c)
{
    /* 等 TX FIFO 不滿（cnt < 128） */
    while (((UART_STATUS_REG(UART0_BASE) >> UART_TXFIFO_CNT_S) & UART_TXFIFO_CNT_M) >= 127) {}
    UART_FIFO_REG(UART0_BASE) = (uint8_t)c;
}

static void uart_puts(const char *s)
{
    while (*s) uart_putchar(*s++);
}

/* 簡易整數轉字串（無 printf 依賴） */
static void uart_put_uint(uint32_t v, int width, char pad)
{
    char buf[12];
    int  len = 0;
    if (v == 0) { buf[len++] = '0'; }
    else {
        while (v) { buf[len++] = '0' + (v % 10); v /= 10; }
    }
    while (len < width) { uart_putchar(pad); width--; }
    for (int i = len - 1; i >= 0; i--) uart_putchar(buf[i]);
}

static void uart_put_float1(float v) /* 精度 0.1 */
{
    int32_t iv = (int32_t)(v * 10.0f + 0.5f);
    uart_put_uint(iv / 10, 1, ' ');
    uart_putchar('.');
    uart_put_uint(iv % 10, 1, '0');
}

static void uart_put_float2(float v) /* 精度 0.01 */
{
    int32_t iv = (int32_t)(v * 100.0f + 0.5f);
    uart_put_uint(iv / 100, 1, ' ');
    uart_putchar('.');
    uart_put_uint(iv % 100, 2, '0');
}

/* ================================================================
 * 共享資料
 * ================================================================ */

typedef struct {
    uint32_t timestamp;   /* 秒 */
    float    temperature; /* °C */
    float    humidity;    /* % */
    bool     flash_ok;
} sensor_record_t;

static QueueHandle_t    g_log_queue;   /* sensor_task -> uart_task */
static uint32_t         g_tick_sec = 0;

/* ================================================================
 * FreeRTOS Tasks
 * ================================================================ */

/* sensor_task：每 2 秒讀 BME280 + 寫 Flash，結果送到 log queue */
static void sensor_task(void *arg)
{
    (void)arg;
    vTaskDelay(pdMS_TO_TICKS(500)); /* 等其他 peripheral 穩定 */

    for (;;) {
        sensor_record_t rec = {0};
        rec.timestamp = g_tick_sec;

        /* 讀 BME280 */
        if (!bme280_read(&rec.temperature, &rec.humidity)) {
            rec.temperature = 0.0f;
            rec.humidity    = 0.0f;
        }

        /* 組 8-byte payload */
        uint8_t payload[8];
        uint32_t ts_be = rec.timestamp;
        payload[0] = (ts_be >> 24) & 0xFF;
        payload[1] = (ts_be >> 16) & 0xFF;
        payload[2] = (ts_be >>  8) & 0xFF;
        payload[3] = (ts_be)       & 0xFF;
        int16_t  t_raw = (int16_t)(rec.temperature * 100.0f);
        uint16_t h_raw = (uint16_t)(rec.humidity   * 10.0f);
        payload[4] = (t_raw >> 8) & 0xFF;
        payload[5] = (t_raw)      & 0xFF;
        payload[6] = (h_raw >> 8) & 0xFF;
        payload[7] = (h_raw)      & 0xFF;

        /* 寫 W25Q32 */
        xSemaphoreTake(g_spi_mutex, portMAX_DELAY);
        rec.flash_ok = w25q32_page_program(0x001000, payload, 8);

        /* 讀回驗證 */
        if (rec.flash_ok) {
            uint8_t verify[8] = {0};
            w25q32_read_data(0x001000, verify, 8);
            rec.flash_ok = (memcmp(payload, verify, 8) == 0);
        }
        xSemaphoreGive(g_spi_mutex);

        xQueueSend(g_log_queue, &rec, 0);

        g_tick_sec += 2;
        vTaskDelay(pdMS_TO_TICKS(2000));
    }
}

/* uart_task：等 queue，輸出 log */
static void uart_task(void *arg)
{
    (void)arg;
    sensor_record_t rec;

    for (;;) {
        if (xQueueReceive(g_log_queue, &rec, portMAX_DELAY) == pdTRUE) {
            uart_puts("[T=");
            uart_put_uint(rec.timestamp, 4, '0');
            uart_puts("s] Temp=");
            uart_put_float2(rec.temperature);
            uart_puts("C Humi=");
            uart_put_float1(rec.humidity);
            uart_puts("% -> Flash[0x001000] ");
            uart_puts(rec.flash_ok ? "OK" : "FAIL");
            uart_puts("\r\n");
        }
    }
}

/* ================================================================
 * app_main
 * ================================================================ */

void app_main(void)
{
    /* UART0 在 ESP-IDF 裡預設已由 esp_rom_uart 初始化為 115200，
       這裡不另外重設（直接往 FIFO 寫即可） */

    spi_init();
    i2c_init();

    if (!bme280_init()) {
        uart_puts("BME280 init FAIL\r\n");
        /* 繼續執行，溫濕度會是 0.0 */
    }

    g_log_queue = xQueueCreate(4, sizeof(sensor_record_t));

    xTaskCreate(sensor_task,   "sensor",   4096, NULL, 5, NULL);
    xTaskCreate(uart_task,     "uart_log", 2048, NULL, 3, NULL);
}
```

**注意事項**

1. W25Q32 的 Page Program 每次最多 256 bytes，且不能跨 page 邊界（256 byte 邊界）。
   address 0x001000 是一個 page 開頭，8 bytes 完全沒問題。

2. Page Program 之前如果同一個 page 有舊資料且不是 0xFF，結果是 AND 寫入（只能把 1 寫成 0）。
   正式使用要先 Sector Erase（0x20）把整個 4K sector 清成 0xFF。
   練習題只跑一次可以跳過 Erase，但要驗證 verify pass 才算真的 OK。

3. I2C_COMD0_REG 是 command slot 0，ESP32 共有 16 個 slot（offset 0x58–0x94）。
   上面的 Read 序列用了 6 個 slot（RSTART + WRITE + RSTART + WRITE + READ + STOP），
   在 16 個 slot 內沒問題，但如果要一次讀超過 32 bytes，需要拆成多段。

4. BME280 normal mode 下，sensor 每 1 秒自動刷新一次（standby 1000ms）。
   每 2 秒讀一次不會讀到 stale data。如果要更快，改 0xF5 的 standby 時間設定。

</details>

---

## 測試用例

### TC-01：正常讀寫

- 接好硬體，`idf.py flash monitor`
- 預期：序列埠每 2 秒出現一行 `Flash[0x001000] OK`
- 溫度值應在合理範圍（室溫 20–35°C）

### TC-02：I2C 線路斷開

- 把 SDA 線拔掉後重新上電
- 預期：`BME280 init FAIL`，之後每行顯示 `Temp=0.00C Humi=0.0%`，Flash 寫入仍然執行

### TC-03：Flash CS 線斷開

- 把 CS 線拔掉
- 預期：`Flash[0x001000] FAIL`（read_status timeout 或 verify 不吻合）

### TC-04：多任務不互相阻塞驗證

- 用邏輯分析儀同時掛在 I2C SCL 和 SPI CLK 上
- 預期：I2C 和 SPI 波形不重疊（有 mutex 保護），UART 輸出時序穩定，不抖動

---

## 自我檢核

1. BME280 的補償公式輸出單位是什麼？你有沒有除以正確的係數？
2. W25Q32 Page Program 前有沒有確認 WEL bit 已拉高？如果沒有，BUSY 不會升起來還是資料寫不進去？
3. 三個 peripheral 各自用了不同的 mutex，為什麼不能共用一把？
4. `uart_task` 收到 queue item 後才輸出，而不是 `sensor_task` 直接寫 UART，這樣設計的好處是什麼？
5. 如果把 `sensor_task` 的優先級降到 1，`uart_task` 的優先級升到 6，會有什麼問題？

---

→ [練習 B：CAN 雙節點仲裁測試](./practice-b-can-arbitration.md)
