# 練習 C：BLE + LoRa 橋接

用兩顆 ESP32 搭建一個 BLE-LoRa 橋接系統：ESP32-A 透過 LoRa 傳資料，ESP32-B 收到後透過 BLE GATT Notify 推給手機。這是 IoT 網關的典型場景，驗證你能跨協議整合並處理非同步事件。

**前置章節**：Ch 18（BLE GATT Service）、Ch 19（LoRa 原理）、Ch 20（SX1276 Register-Level LoRa 收發）

---

## 硬體需求

| 元件 | 數量 | 接法 |
|---|---|---|
| ESP32 DevKitC | 2 | ESP32-A（LoRa TX）、ESP32-B（LoRa RX + BLE Peripheral） |
| SX1276 LoRa 模組（TTGO 或 Hope RF95） | 2 | 各接一顆 |
| 手機（Android 或 iOS） | 1 | 安裝 nRF Connect |

**ESP32-A 和 ESP32-B SX1276 接線相同：**
```
SX1276  NSS (CS)  -> GPIO5
SX1276  SCK       -> GPIO18
SX1276  MOSI      -> GPIO23
SX1276  MISO      -> GPIO19
SX1276  DIO0      -> GPIO26  (RxDone / TxDone 中斷)
SX1276  RESET     -> GPIO14
SX1276  3V3       -> 3.3V
SX1276  GND       -> GND
```

注意：部分 SX1276 模組（TTGO LoRa32）的 NSS 接的是 GPIO18，OLED 也在同一條 SPI bus，引腳需要依你的板子調整。

---

## 系統架構

```
手機 (nRF Connect)
     |
     | BLE GATT Notify（每秒推送）
     |
ESP32-B （LoRa RX + BLE Peripheral）
     |
     | LoRa 915MHz（SX1276）
     |
ESP32-A （LoRa TX only）
```

ESP32-A 每 3 秒發一個 LoRa 封包，ESP32-B 收到後更新 BLE characteristic，手機看到的 value 會自動刷新。

---

## 題目規格

### ESP32-A 任務（LoRa TX）

- 每 3 秒發一個 LoRa 封包
- 封包內容（純文字，不超過 50 bytes）：
  ```
  {"node":"A","cnt":N}
  ```
  其中 N 是從 0 開始遞增的計數器
- LoRa 設定：
  - 頻率 915 MHz（或依你所在地區法規，亞洲常用 433 MHz）
  - Spreading Factor = 7（SF7）
  - Bandwidth = 125 kHz
  - Coding Rate = 4/5
  - TX Power = 14 dBm
  - Explicit Header Mode（帶 header）

### ESP32-B 任務（LoRa RX + BLE Peripheral）

1. **LoRa RX**（持續監聽）：
   - SX1276 進入 Continuous RX 模式（RegOpMode = 0x85）
   - 收到封包後讀出 payload + RSSI（RegPktRssiValue，reg 0x1A）
   - 把收到的內容加上 RSSI 組成新字串：
     ```
     {"node":"A","cnt":5,"rssi":-72}
     ```
   - 更新到 BLE characteristic value

2. **BLE Peripheral**（NimBLE stack）：
   - 廣播名稱：`LoRa-Bridge`
   - 自訂 Service UUID：`AA00`（16-bit）
   - Characteristic UUID：`AA01`（16-bit），屬性：READ + NOTIFY
   - 手機連上後，每收到一次 LoRa 封包就 Notify 一次
   - 若 3 個 LoRa 週期內沒有收到封包，Notify 內容改為：
     ```
     {"status":"no_signal","rssi":0}
     ```

### 距離量測任務

在四個不同距離分別傳 20 個封包，記錄：
- 每個封包的 RSSI（dBm）
- 接收成功/失敗

填寫表格：

| 距離 | 封包數 | 成功接收 | 平均 RSSI | 接收率 |
|---|---|---|---|---|
| 室內 5m | 20 | ? | ? | ? |
| 室內 10m | 20 | ? | ? | ? |
| 室外 50m | 20 | ? | ? | ? |
| 室外 100m | 20 | ? | ? | ? |

---

## 期望手機 nRF Connect 畫面

連接 `LoRa-Bridge` 後，在 Service `AA00` 下看到 Characteristic `AA01`：

```
Characteristic AA01
Properties: READ, NOTIFY
Value (Notify): {"node":"A","cnt":5,"rssi":-72}
Value (Notify): {"node":"A","cnt":6,"rssi":-71}
```

UART0 debug log（ESP32-B）：

```
LoRa RX: {"node":"A","cnt":1} RSSI=-68 dBm
LoRa RX: {"node":"A","cnt":2} RSSI=-69 dBm
BLE Notify: {"node":"A","cnt":2,"rssi":-69}
```

---

## 實作步驟

### Step 1：SX1276 SPI 初始化

SX1276 和 W25Q32 一樣走 SPI，但注意：
- SX1276 SPI mode 0（CPOL=0，CPHA=0），最高 10 MHz
- Register 讀寫：先送 1-byte address（寫：bit 7=1；讀：bit 7=0），再送/收 data
- 上電後先讀 RegVersion（0x42），確認回傳 0x12（SX1276）

```c
/* SX1276 register 讀 */
uint8_t sx1276_read_reg(uint8_t addr)
{
    cs_low();
    spi_tx(addr & 0x7F); /* bit 7 = 0：read */
    uint8_t val = spi_rx();
    cs_high();
    return val;
}

/* SX1276 register 寫 */
void sx1276_write_reg(uint8_t addr, uint8_t val)
{
    cs_low();
    spi_tx(addr | 0x80); /* bit 7 = 1：write */
    spi_tx(val);
    cs_high();
}
```

### Step 2：SX1276 LoRa 初始化序列

必須先進入 Sleep Mode 才能切換到 LoRa mode：

```
RegOpMode (0x01) = 0x00  (Sleep, FSK mode)
RegOpMode (0x01) = 0x80  (Sleep, LoRa mode)  <- 切到 LoRa
RegOpMode (0x01) = 0x81  (Standby, LoRa mode)

設定頻率（915 MHz）：
  Frf = 915e6 / (32e6 / 2^19) = 915e6 / 61.035Hz/step = 14,995,456 = 0xE4C000
  RegFrfMsb (0x06) = 0xE4
  RegFrfMid (0x07) = 0xC0
  RegFrfLsb (0x08) = 0x00

設定 TX power：
  RegPaConfig (0x09) = 0x8F  (PA_BOOST, max power=2, output_power=15)
  RegPaDac    (0x4D) = 0x87  (高功率模式)

設定 BW / CR / SF：
  RegModemConfig1 (0x1D) = 0x72  (BW=125kHz, CR=4/5, Explicit Header)
  RegModemConfig2 (0x1E) = 0x74  (SF=7, CRC on)
  RegModemConfig3 (0x26) = 0x04  (LNA gain auto, AGC on)

Preamble length = 8：
  RegPreambleMsb (0x20) = 0x00
  RegPreambleLsb (0x21) = 0x08

DIO0 mapping = TxDone（TX 時）或 RxDone（RX 時）：
  RegDioMapping1 (0x40) = 0x00  (DIO0 = RxDone for RX / TxDone for TX 看 mode)
```

### Step 3：SX1276 TX 流程

```
1. 進入 Standby：RegOpMode = 0x81
2. 設定 FIFO TX base addr：RegFifoTxBaseAddr (0x0E) = 0x00
3. 設定 FIFO addr pointer：RegFifoAddrPtr (0x0D) = 0x00
4. 把 payload 逐 byte 寫入 RegFifo (0x00)
5. 設定 payload 長度：RegPayloadLength (0x22) = payload_len
6. 設定 DIO0 mapping = TxDone：RegDioMapping1 = 0x40
7. 進入 TX mode：RegOpMode = 0x83
8. 等 DIO0 高電平（TxDone）或輪詢 RegIrqFlags (0x12) bit 3（TxDone）
9. 清 IRQ：RegIrqFlags = 0x08
```

### Step 4：SX1276 Continuous RX 流程

```
1. 進入 Standby：RegOpMode = 0x81
2. 設定 FIFO RX base addr：RegFifoRxBaseAddr (0x0F) = 0x00
3. 設定 DIO0 mapping = RxDone：RegDioMapping1 = 0x00
4. 進入 Continuous RX：RegOpMode = 0x85
5. 輪詢 DIO0 電平或 RegIrqFlags bit 6（RxDone）
6. 收到後：
   a. 讀 RegIrqFlags，確認 bit 5（PayloadCrcError）= 0
   b. 讀 RegFifoRxCurrentAddr (0x10)，設定 RegFifoAddrPtr = 此值
   c. 讀 RegRxNbBytes (0x13)，得知 payload 長度
   d. 從 RegFifo 讀出 payload
   e. 讀 RSSI：RegPktRssiValue (0x1A)，實際 RSSI = -157 + 讀值（HF port）
   f. 清 IRQ：RegIrqFlags = 0xFF
   g. 繼續等（Continuous RX 不需要重新設定 mode）
```

RSSI 計算公式（SX1276 datasheet 3.5.5）：
- 915 MHz（HF port）：RSSI = -157 + RegPktRssiValue
- 433 MHz（LF port）：RSSI = -164 + RegPktRssiValue

### Step 5：NimBLE GATT Service 設定

ESP-IDF v5.x 使用 NimBLE 作為 BLE stack。自訂 GATT service 的設定方式：

```c
static const struct ble_gatt_svc_def gatt_svcs[] = {
    {
        .type = BLE_GATT_SVC_TYPE_PRIMARY,
        .uuid = BLE_UUID16_DECLARE(0xAA00),
        .characteristics = (struct ble_gatt_chr_def[]) {
            {
                .uuid = BLE_UUID16_DECLARE(0xAA01),
                .flags = BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_NOTIFY,
                .access_cb = lora_data_chr_access,
                .val_handle = &g_lora_chr_handle,
            },
            { 0 } /* 結尾 sentinel */
        },
    },
    { 0 }
};
```

Notify 用 `ble_gatts_notify_custom()`，傳入 `om`（os_mbuf）。

### Step 6：FreeRTOS task 架構（ESP32-B）

```
lora_rx_task  -> 輪詢 SX1276 DIO0，收到後更新 g_lora_payload[] 和 g_last_rssi
ble_notify_task -> 每秒一次，呼叫 ble_gatts_notify_custom() 推給已連線的 central
watchdog_check  -> 在 lora_rx_task 裡：若超過 9 秒沒收到，設 g_no_signal = true
```

兩個 task 共用 `g_lora_payload`，需要 mutex 保護。

---

## 參考解答

<details>
<summary>點開參考實作</summary>

```c
/*
 * practice_c_ble_lora_bridge.c
 *
 * 編譯兩份：
 *   NODE_A=1 idf.py build  -> ESP32-A (LoRa TX only)
 *   NODE_B=1 idf.py build  -> ESP32-B (LoRa RX + BLE)
 *
 * 依賴：ESP-IDF v5.x，NimBLE 已在 sdkconfig 啟用
 *   CONFIG_BT_ENABLED=y
 *   CONFIG_BT_NIMBLE_ENABLED=y
 */

#include <stdint.h>
#include <string.h>
#include <stdbool.h>
#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "driver/gpio.h"

/* NimBLE headers */
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "host/ble_hs.h"
#include "host/util/util.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"

/* ================================================================
 * SPI (VSPI) for SX1276 — 複用 Practice A 的 SPI 驅動骨架
 * ================================================================ */

#define SPI3_BASE           0x3FF65000UL
#define SPI_CMD_REG(b)      (*(volatile uint32_t *)((b) + 0x00))
#define SPI_MOSI_DLEN_REG(b)(*(volatile uint32_t *)((b) + 0x28))
#define SPI_MISO_DLEN_REG(b)(*(volatile uint32_t *)((b) + 0x2C))
#define SPI_CMD_USR         (1UL << 18)
#define SPI_USER_REG(b)     (*(volatile uint32_t *)((b) + 0x1C))
#define SPI_CLOCK_REG(b)    (*(volatile uint32_t *)((b) + 0x18))
#define SPI_CTRL_REG(b)     (*(volatile uint32_t *)((b) + 0x08))
#define SPI_W0_REG(b)       (*(volatile uint32_t *)((b) + 0x80))

#define DPORT_PERIP_CLK_EN  (*(volatile uint32_t *)0x3FF0001CUL)
#define DPORT_PERIP_RST_EN  (*(volatile uint32_t *)0x3FF00020UL)
#define DPORT_SPI3_CLK_EN   (1UL << 27)

#define GPIO_FUNC_OUT_SEL(n)(*(volatile uint32_t *)(0x3FF44530UL + (n)*4))
#define GPIO_FUNC_IN_SEL(n) (*(volatile uint32_t *)(0x3FF44130UL + (n)*4))
#define GPIO_ENABLE_W1TS    (*(volatile uint32_t *)0x3FF44024UL)
#define GPIO_OUT_W1TS_REG   (*(volatile uint32_t *)0x3FF44008UL)
#define GPIO_OUT_W1TC_REG   (*(volatile uint32_t *)0x3FF4400CUL)

#define SX1276_CS_PIN    5
#define SX1276_CLK_PIN   18
#define SX1276_MOSI_PIN  23
#define SX1276_MISO_PIN  19
#define SX1276_DIO0_PIN  26
#define SX1276_RST_PIN   14

#define VSPI_CLK_OUT_IDX  63
#define VSPI_MOSI_OUT_IDX 100
#define VSPI_MISO_IN_IDX  101

static void spi_init_lora(void)
{
    DPORT_PERIP_CLK_EN |=  DPORT_SPI3_CLK_EN;
    DPORT_PERIP_RST_EN &= ~DPORT_SPI3_CLK_EN;

    GPIO_FUNC_OUT_SEL(SX1276_CLK_PIN)  = VSPI_CLK_OUT_IDX;
    GPIO_ENABLE_W1TS = (1UL << SX1276_CLK_PIN);
    GPIO_FUNC_OUT_SEL(SX1276_MOSI_PIN) = VSPI_MOSI_OUT_IDX;
    GPIO_ENABLE_W1TS = (1UL << SX1276_MOSI_PIN);
    GPIO_FUNC_IN_SEL(VSPI_MISO_IN_IDX) = SX1276_MISO_PIN;
    GPIO_FUNC_OUT_SEL(SX1276_CS_PIN)   = 0x100;
    GPIO_ENABLE_W1TS = (1UL << SX1276_CS_PIN);
    GPIO_OUT_W1TS_REG = (1UL << SX1276_CS_PIN); /* CS 預設高 */

    /* RST pin：output，先高後低再高 reset */
    GPIO_FUNC_OUT_SEL(SX1276_RST_PIN) = 0x100;
    GPIO_ENABLE_W1TS = (1UL << SX1276_RST_PIN);
    GPIO_OUT_W1TC_REG = (1UL << SX1276_RST_PIN);
    vTaskDelay(pdMS_TO_TICKS(10));
    GPIO_OUT_W1TS_REG = (1UL << SX1276_RST_PIN);
    vTaskDelay(pdMS_TO_TICKS(10));

    /* 10 MHz clock（保守） */
    SPI_CLOCK_REG(SPI3_BASE) = (7UL << 18) | (1UL << 12) | 1UL;
    SPI_USER_REG(SPI3_BASE)  = (1UL << 27) | (1UL << 28);
    SPI_CTRL_REG(SPI3_BASE)  = 0;
}

static uint8_t spi_byte(uint8_t tx)
{
    volatile uint32_t *w = &SPI_W0_REG(SPI3_BASE);
    *w = tx;
    SPI_MOSI_DLEN_REG(SPI3_BASE) = 7;
    SPI_MISO_DLEN_REG(SPI3_BASE) = 7;
    SPI_CMD_REG(SPI3_BASE) |= SPI_CMD_USR;
    while (SPI_CMD_REG(SPI3_BASE) & SPI_CMD_USR) {}
    return (uint8_t)(*w & 0xFF);
}

static inline void lora_cs_low(void)  { GPIO_OUT_W1TC_REG = (1UL << SX1276_CS_PIN); }
static inline void lora_cs_high(void) { GPIO_OUT_W1TS_REG = (1UL << SX1276_CS_PIN); }

/* ================================================================
 * SX1276 register 存取
 * ================================================================ */

static uint8_t sx1276_read(uint8_t reg)
{
    lora_cs_low();
    spi_byte(reg & 0x7F);
    uint8_t val = spi_byte(0x00);
    lora_cs_high();
    return val;
}

static void sx1276_write(uint8_t reg, uint8_t val)
{
    lora_cs_low();
    spi_byte(reg | 0x80);
    spi_byte(val);
    lora_cs_high();
}

/* 把 buf[0..len-1] 寫入 SX1276 FIFO（burst write） */
static void sx1276_write_fifo(const uint8_t *buf, uint8_t len)
{
    lora_cs_low();
    spi_byte(0x80); /* reg 0x00 (FIFO) | 0x80 (write) */
    for (int i = 0; i < len; i++) spi_byte(buf[i]);
    lora_cs_high();
}

static void sx1276_read_fifo(uint8_t *buf, uint8_t len)
{
    lora_cs_low();
    spi_byte(0x00); /* reg 0x00 (FIFO) | 0x00 (read) */
    for (int i = 0; i < len; i++) buf[i] = spi_byte(0x00);
    lora_cs_high();
}

/* ================================================================
 * SX1276 LoRa 初始化
 * ================================================================ */

#define SX1276_REG_FIFO          0x00
#define SX1276_REG_OP_MODE       0x01
#define SX1276_REG_FRF_MSB       0x06
#define SX1276_REG_FRF_MID       0x07
#define SX1276_REG_FRF_LSB       0x08
#define SX1276_REG_PA_CONFIG     0x09
#define SX1276_REG_PA_DAC        0x4D
#define SX1276_REG_FIFO_ADDR_PTR 0x0D
#define SX1276_REG_FIFO_TX_BASE  0x0E
#define SX1276_REG_FIFO_RX_BASE  0x0F
#define SX1276_REG_FIFO_RX_CUR   0x10
#define SX1276_REG_IRQ_FLAGS     0x12
#define SX1276_REG_RX_NB_BYTES   0x13
#define SX1276_REG_PKT_RSSI_VAL  0x1A
#define SX1276_REG_MODEM_CFG1    0x1D
#define SX1276_REG_MODEM_CFG2    0x1E
#define SX1276_REG_PREAMBLE_MSB  0x20
#define SX1276_REG_PREAMBLE_LSB  0x21
#define SX1276_REG_PAYLOAD_LEN   0x22
#define SX1276_REG_MODEM_CFG3    0x26
#define SX1276_REG_DIO_MAPPING1  0x40
#define SX1276_REG_VERSION       0x42

#define LORA_FREQ_915MHZ_MSB  0xE4
#define LORA_FREQ_915MHZ_MID  0xC0
#define LORA_FREQ_915MHZ_LSB  0x00
/* 433 MHz 改用：0x6C 0x80 0x00 */

static bool sx1276_init(void)
{
    uint8_t ver = sx1276_read(SX1276_REG_VERSION);
    if (ver != 0x12) return false; /* 不是 SX1276 */

    /* Sleep mode，切到 LoRa */
    sx1276_write(SX1276_REG_OP_MODE, 0x00); /* Sleep FSK */
    vTaskDelay(pdMS_TO_TICKS(10));
    sx1276_write(SX1276_REG_OP_MODE, 0x80); /* Sleep LoRa */
    vTaskDelay(pdMS_TO_TICKS(10));

    /* 頻率 915 MHz */
    sx1276_write(SX1276_REG_FRF_MSB, LORA_FREQ_915MHZ_MSB);
    sx1276_write(SX1276_REG_FRF_MID, LORA_FREQ_915MHZ_MID);
    sx1276_write(SX1276_REG_FRF_LSB, LORA_FREQ_915MHZ_LSB);

    /* TX Power：PA_BOOST，14 dBm */
    sx1276_write(SX1276_REG_PA_CONFIG, 0x8C); /* PA_BOOST, OutputPower=12 -> ~14dBm */
    sx1276_write(SX1276_REG_PA_DAC,    0x84); /* default（0x84 = normal power） */

    /* BW=125kHz, CR=4/5, Explicit Header */
    sx1276_write(SX1276_REG_MODEM_CFG1, 0x72);
    /* SF=7, CRC on */
    sx1276_write(SX1276_REG_MODEM_CFG2, 0x74);
    /* AGC on */
    sx1276_write(SX1276_REG_MODEM_CFG3, 0x04);

    /* Preamble = 8 symbols */
    sx1276_write(SX1276_REG_PREAMBLE_MSB, 0x00);
    sx1276_write(SX1276_REG_PREAMBLE_LSB, 0x08);

    /* FIFO base addr */
    sx1276_write(SX1276_REG_FIFO_TX_BASE, 0x00);
    sx1276_write(SX1276_REG_FIFO_RX_BASE, 0x00);

    /* 進 Standby */
    sx1276_write(SX1276_REG_OP_MODE, 0x81);
    return true;
}

/* ================================================================
 * SX1276 TX（輪詢模式）
 * ================================================================ */

static bool sx1276_tx(const uint8_t *payload, uint8_t len)
{
    sx1276_write(SX1276_REG_OP_MODE, 0x81); /* Standby */
    sx1276_write(SX1276_REG_FIFO_ADDR_PTR, 0x00);
    sx1276_write_fifo(payload, len);
    sx1276_write(SX1276_REG_PAYLOAD_LEN, len);

    /* DIO0 = TxDone */
    sx1276_write(SX1276_REG_DIO_MAPPING1, 0x40);

    /* 清 IRQ */
    sx1276_write(SX1276_REG_IRQ_FLAGS, 0xFF);

    /* TX mode */
    sx1276_write(SX1276_REG_OP_MODE, 0x83);

    /* 等 TxDone（最長 3 秒） */
    uint32_t timeout = 3000;
    while (timeout--) {
        uint8_t irq = sx1276_read(SX1276_REG_IRQ_FLAGS);
        if (irq & 0x08) { /* TxDone */
            sx1276_write(SX1276_REG_IRQ_FLAGS, 0x08);
            sx1276_write(SX1276_REG_OP_MODE, 0x81); /* 回 Standby */
            return true;
        }
        vTaskDelay(1);
    }
    return false;
}

/* ================================================================
 * SX1276 Continuous RX（輪詢 IRQ flag）
 * ================================================================ */

static void sx1276_start_rx(void)
{
    sx1276_write(SX1276_REG_OP_MODE, 0x81); /* Standby */
    sx1276_write(SX1276_REG_FIFO_ADDR_PTR, 0x00);
    sx1276_write(SX1276_REG_DIO_MAPPING1, 0x00); /* DIO0 = RxDone */
    sx1276_write(SX1276_REG_IRQ_FLAGS, 0xFF);    /* 清 IRQ */
    sx1276_write(SX1276_REG_OP_MODE, 0x85);      /* Continuous RX */
}

/* 輪詢一次，如果有資料就填到 buf，回傳長度（0=無資料） */
static uint8_t sx1276_poll_rx(uint8_t *buf, uint8_t buf_size, int8_t *rssi_dbm)
{
    uint8_t irq = sx1276_read(SX1276_REG_IRQ_FLAGS);
    if (!(irq & 0x40)) return 0; /* RxDone not set */

    /* CRC error check */
    if (irq & 0x20) {
        sx1276_write(SX1276_REG_IRQ_FLAGS, 0xFF);
        return 0;
    }

    uint8_t cur_addr = sx1276_read(SX1276_REG_FIFO_RX_CUR);
    uint8_t nb_bytes = sx1276_read(SX1276_REG_RX_NB_BYTES);
    if (nb_bytes > buf_size) nb_bytes = buf_size;

    sx1276_write(SX1276_REG_FIFO_ADDR_PTR, cur_addr);
    sx1276_read_fifo(buf, nb_bytes);

    uint8_t raw_rssi = sx1276_read(SX1276_REG_PKT_RSSI_VAL);
    *rssi_dbm = (int8_t)(-157 + raw_rssi); /* HF port (915MHz) */

    sx1276_write(SX1276_REG_IRQ_FLAGS, 0xFF);
    return nb_bytes;
}

/* ================================================================
 * UART0 輸出（register-level）
 * ================================================================ */

#define UART0_FIFO_REG   (*(volatile uint32_t *)0x3FF40000UL)
#define UART0_STATUS_REG (*(volatile uint32_t *)0x3FF4001CUL)

static void u_putc(char c)
{
    while (((UART0_STATUS_REG >> 16) & 0xFF) >= 127) {}
    UART0_FIFO_REG = (uint8_t)c;
}
static void u_puts(const char *s) { while (*s) u_putc(*s++); }
static void u_puti(int32_t v)
{
    if (v < 0) { u_putc('-'); v = -v; }
    char buf[12]; int len = 0;
    if (!v) { u_putc('0'); return; }
    while (v) { buf[len++] = '0' + v % 10; v /= 10; }
    for (int i = len-1; i >= 0; i--) u_putc(buf[i]);
}

/* ================================================================
 * 共享狀態（LoRa RX -> BLE Notify）
 * ================================================================ */

#define PAYLOAD_BUF_SIZE 80

static char            g_notify_payload[PAYLOAD_BUF_SIZE];
static SemaphoreHandle_t g_payload_mutex;
static volatile bool   g_new_data = false;
static volatile bool   g_no_signal = false;

/* ================================================================
 * NimBLE BLE Peripheral（ESP32-B only）
 * ================================================================ */

#ifdef NODE_B

static uint16_t g_lora_chr_handle;
static uint16_t g_conn_handle = BLE_HS_CONN_HANDLE_NONE;

static int lora_data_chr_access(uint16_t conn_handle, uint16_t attr_handle,
                                 struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    if (ctxt->op == BLE_GATT_ACCESS_OP_READ_CHR) {
        xSemaphoreTake(g_payload_mutex, portMAX_DELAY);
        int rc = os_mbuf_append(ctxt->om, g_notify_payload, strlen(g_notify_payload));
        xSemaphoreGive(g_payload_mutex);
        return rc == 0 ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
    }
    return BLE_ATT_ERR_UNLIKELY;
}

static const struct ble_gatt_svc_def g_gatt_svcs[] = {
    {
        .type = BLE_GATT_SVC_TYPE_PRIMARY,
        .uuid = BLE_UUID16_DECLARE(0xAA00),
        .characteristics = (struct ble_gatt_chr_def[]) {
            {
                .uuid       = BLE_UUID16_DECLARE(0xAA01),
                .flags      = BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_NOTIFY,
                .access_cb  = lora_data_chr_access,
                .val_handle = &g_lora_chr_handle,
            },
            { 0 }
        },
    },
    { 0 }
};

static int gap_event_handler(struct ble_gap_event *event, void *arg)
{
    switch (event->type) {
    case BLE_GAP_EVENT_CONNECT:
        if (event->connect.status == 0) {
            g_conn_handle = event->connect.conn_handle;
            u_puts("BLE: connected\r\n");
        } else {
            g_conn_handle = BLE_HS_CONN_HANDLE_NONE;
            /* 重新廣播 */
            ble_app_advertise();
        }
        break;
    case BLE_GAP_EVENT_DISCONNECT:
        g_conn_handle = BLE_HS_CONN_HANDLE_NONE;
        u_puts("BLE: disconnected\r\n");
        ble_app_advertise();
        break;
    default:
        break;
    }
    return 0;
}

/* 前向宣告 */
static void ble_app_advertise(void);

static void ble_app_on_sync(void)
{
    ble_hs_id_infer_auto(0, NULL);
    ble_app_advertise();
}

static void ble_app_advertise(void)
{
    struct ble_gap_adv_params adv_params = {0};
    struct ble_hs_adv_fields fields = {0};
    const char *name = "LoRa-Bridge";

    fields.flags            = BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP;
    fields.name             = (uint8_t *)name;
    fields.name_len         = strlen(name);
    fields.name_is_complete = 1;
    ble_gap_adv_set_fields(&fields);

    adv_params.conn_mode = BLE_GAP_CONN_MODE_UND;
    adv_params.disc_mode = BLE_GAP_DISC_MODE_GEN;
    ble_gap_adv_start(BLE_OWN_ADDR_PUBLIC, NULL, BLE_HS_FOREVER,
                       &adv_params, gap_event_handler, NULL);
}

/* BLE host task（NimBLE 需要獨立 task） */
static void ble_host_task(void *arg)
{
    nimble_port_run();
    nimble_port_freertos_deinit();
}

/* BLE Notify task：每收到新 LoRa 資料就 Notify */
static void ble_notify_task(void *arg)
{
    (void)arg;
    for (;;) {
        /* 等新資料或超時（1 秒） */
        vTaskDelay(pdMS_TO_TICKS(1000));

        if (g_conn_handle == BLE_HS_CONN_HANDLE_NONE) continue;

        xSemaphoreTake(g_payload_mutex, portMAX_DELAY);
        size_t plen = strlen(g_notify_payload);
        struct os_mbuf *om = ble_hs_mbuf_from_flat(g_notify_payload, plen);
        xSemaphoreGive(g_payload_mutex);

        if (om) {
            ble_gatts_notify_custom(g_conn_handle, g_lora_chr_handle, om);
        }
    }
}

/* LoRa RX task（ESP32-B） */
static void lora_rx_task(void *arg)
{
    (void)arg;
    uint8_t buf[PAYLOAD_BUF_SIZE];
    int8_t  rssi;
    uint32_t last_rx_tick = 0;
    const uint32_t SIGNAL_TIMEOUT_TICKS = pdMS_TO_TICKS(9000); /* 3 個週期沒收到 */

    sx1276_start_rx();

    for (;;) {
        uint8_t len = sx1276_poll_rx(buf, sizeof(buf) - 1, &rssi);
        if (len > 0) {
            buf[len] = '\0';
            last_rx_tick = xTaskGetTickCount();
            g_no_signal  = false;

            /* 組 notify payload：{"node":"A","cnt":5,"rssi":-72} */
            /* 把收到的 JSON 去掉最後的 '}'，補上 ,"rssi":xxx} */
            char combined[PAYLOAD_BUF_SIZE];
            int slen = (int)len;
            /* 找最後一個 '}' */
            while (slen > 0 && buf[slen - 1] != '}') slen--;
            if (slen > 0) slen--; /* 去掉 '}' */

            /* 組字串 */
            int n = 0;
            for (int i = 0; i < slen && n < (int)sizeof(combined) - 20; i++) {
                combined[n++] = buf[i];
            }
            /* 加 ,"rssi":xxx} */
            combined[n++] = ',';
            combined[n++] = '"'; combined[n++] = 'r'; combined[n++] = 's';
            combined[n++] = 's'; combined[n++] = 'i'; combined[n++] = '"';
            combined[n++] = ':';
            /* 轉 rssi 數字（可能是負數） */
            char rssi_str[8]; int rssi_len = 0;
            int32_t rv = rssi;
            if (rv < 0) { rssi_str[rssi_len++] = '-'; rv = -rv; }
            char tmp[6]; int tl = 0;
            if (!rv) tmp[tl++] = '0';
            else while (rv) { tmp[tl++] = '0' + rv % 10; rv /= 10; }
            for (int i = tl - 1; i >= 0; i--) rssi_str[rssi_len++] = tmp[i];
            for (int i = 0; i < rssi_len; i++) combined[n++] = rssi_str[i];
            combined[n++] = '}';
            combined[n]   = '\0';

            xSemaphoreTake(g_payload_mutex, portMAX_DELAY);
            strncpy(g_notify_payload, combined, PAYLOAD_BUF_SIZE - 1);
            g_notify_payload[PAYLOAD_BUF_SIZE - 1] = '\0';
            xSemaphoreGive(g_payload_mutex);

            u_puts("LoRa RX: ");
            u_puts((char *)buf);
            u_puts(" RSSI=");
            u_puti(rssi);
            u_puts(" dBm\r\n");
        } else {
            /* 檢查 no-signal */
            uint32_t now = xTaskGetTickCount();
            if ((now - last_rx_tick) > SIGNAL_TIMEOUT_TICKS) {
                if (!g_no_signal) {
                    g_no_signal = true;
                    xSemaphoreTake(g_payload_mutex, portMAX_DELAY);
                    strncpy(g_notify_payload,
                            "{\"status\":\"no_signal\",\"rssi\":0}",
                            PAYLOAD_BUF_SIZE - 1);
                    xSemaphoreGive(g_payload_mutex);
                }
            }
        }
        vTaskDelay(pdMS_TO_TICKS(50)); /* 50ms 輪詢間隔 */
    }
}

#endif /* NODE_B */

/* ================================================================
 * LoRa TX task（ESP32-A）
 * ================================================================ */

#ifdef NODE_A

static void lora_tx_task(void *arg)
{
    (void)arg;
    uint32_t cnt = 0;
    char payload[64];

    for (;;) {
        cnt++;
        /* 組 payload：{"node":"A","cnt":N} */
        /* 簡單的整數轉字串，不依賴 snprintf */
        const char prefix[] = "{\"node\":\"A\",\"cnt\":";
        const char suffix[] = "}";
        int n = 0;
        for (int i = 0; prefix[i]; i++) payload[n++] = prefix[i];
        /* 轉 cnt */
        char numstr[12]; int nlen = 0;
        uint32_t v = cnt;
        if (!v) numstr[nlen++] = '0';
        else while (v) { numstr[nlen++] = '0' + v % 10; v /= 10; }
        for (int i = nlen - 1; i >= 0; i--) payload[n++] = numstr[i];
        for (int i = 0; suffix[i]; i++) payload[n++] = suffix[i];
        payload[n] = '\0';

        u_puts("LoRa TX: ");
        u_puts(payload);
        u_puts("\r\n");

        bool ok = sx1276_tx((uint8_t *)payload, n);
        u_puts(ok ? "TX OK\r\n" : "TX TIMEOUT\r\n");

        vTaskDelay(pdMS_TO_TICKS(3000));
    }
}

#endif /* NODE_A */

/* ================================================================
 * app_main
 * ================================================================ */

void app_main(void)
{
    spi_init_lora();

    if (!sx1276_init()) {
        u_puts("SX1276 init FAIL (version mismatch)\r\n");
        return;
    }
    u_puts("SX1276 init OK\r\n");

#ifdef NODE_A
    u_puts("=== LoRa Node A (TX) ===\r\n");
    xTaskCreate(lora_tx_task, "lora_tx", 4096, NULL, 5, NULL);

#elif defined(NODE_B)
    u_puts("=== LoRa Node B (RX + BLE) ===\r\n");
    g_payload_mutex = xSemaphoreCreateMutex();
    strncpy(g_notify_payload, "{\"status\":\"waiting\"}", PAYLOAD_BUF_SIZE - 1);

    /* 初始化 NimBLE */
    nimble_port_init();
    ble_svc_gap_init();
    ble_svc_gatt_init();
    ble_gatts_count_cfg(g_gatt_svcs);
    ble_gatts_add_svcs(g_gatt_svcs);
    ble_hs_cfg.sync_cb = ble_app_on_sync;
    nimble_port_freertos_init(ble_host_task);

    xTaskCreate(lora_rx_task,    "lora_rx",   4096, NULL, 6, NULL);
    xTaskCreate(ble_notify_task, "ble_notify", 4096, NULL, 4, NULL);

#else
    #error "Define NODE_A or NODE_B"
#endif
}
```

**使用 sdkconfig 啟用 NimBLE：**

```
# sdkconfig 裡加入（或 idf.py menuconfig 裡設定）
CONFIG_BT_ENABLED=y
CONFIG_BT_NIMBLE_ENABLED=y
CONFIG_BT_CONTROLLER_ENABLED=y
```

**CMakeLists.txt 加入 NimBLE 依賴：**

```cmake
idf_component_register(
    SRCS "main.c"
    INCLUDE_DIRS "."
    REQUIRES bt nvs_flash esp_wifi freertos driver
)
```

**RSSI 計算補充：**

SX1276 RegPktRssiValue 是封包接收時的 snapshot RSSI，公式取決於使用哪個頻段：
- 915 MHz（HF port，> 860 MHz）：RSSI (dBm) = -157 + RegPktRssiValue
- 433 MHz（LF port，< 860 MHz）：RSSI (dBm) = -164 + RegPktRssiValue

注意 SNR 很低時（RegPktSnrValue bit 7 = 1，負 SNR），需要額外的補償：
RSSI = -157 + RegPktRssiValue + RegPktSnrValue / 4（RegPktSnrValue 是有符號數，右移 2）

</details>

---

## 距離量測記錄表

執行量測時，ESP32-A 每 3 秒發一個封包，跑 20 個封包（約 60 秒）。在不同位置記錄：

| 距離 | 發送次數 | 成功接收 | 最低 RSSI | 最高 RSSI | 平均 RSSI | 接收率 |
|---|---|---|---|---|---|---|
| 室內 5m | 20 | | | | | |
| 室內 10m | 20 | | | | | |
| 室外 50m | 20 | | | | | |
| 室外 100m | 20 | | | | | |

SF7 BW125 在空曠環境理論靈敏度約 -123 dBm，室外 100m 的 RSSI 應該還差很遠——如果 RSSI 已到 -110 dBm 以下，換成 SF9 或 SF12 提高接收靈敏度（代價是 airtime 更長）。

---

## 測試用例

### TC-01：SX1276 版本確認

- 上電後序列埠應該印出 `SX1276 init OK`
- 如果印出 `FAIL`：確認接線，特別是 CS 和 RST 引腳

### TC-02：ESP32-A LoRa TX

- 序列埠應該每 3 秒印出 `LoRa TX: {"node":"A","cnt":N}` 和 `TX OK`
- 如果 `TX TIMEOUT`：確認 DIO0 接線，或改成輪詢 RegIrqFlags 不靠 DIO0

### TC-03：ESP32-B LoRa RX + UART log

- 序列埠應該每 3 秒出現 `LoRa RX: {"node":"A","cnt":N} RSSI=-XX dBm`

### TC-04：手機 nRF Connect BLE 連線

- 打開 nRF Connect，掃描應看到 `LoRa-Bridge`
- 連線後找到 Service `0xAA00`，Characteristic `0xAA01`
- 點 Subscribe Notifications，應每隔幾秒收到包含 RSSI 的 JSON

### TC-05：斷訊測試

- 關掉 ESP32-A 或把它移到超出範圍的距離
- 等待 9 秒後，BLE Notify 的值應變為 `{"status":"no_signal","rssi":0}`

---

## 自我檢核

1. SX1276 在 LoRa 模式下，RegOpMode 的 bit 7 是什麼意義？如果不設 bit 7，RegFrfMsb 寫進去的頻率是哪個模式的頻率？
2. TX 之前為什麼要先設定 `RegFifoAddrPtr = RegFifoTxBaseAddr`？不設的話會發生什麼事？
3. `sx1276_poll_rx` 裡面，如果 `PayloadCrcError` bit 被設起來，你應該丟棄這個封包還是繼續讀 FIFO？如果繼續讀會影響下一個封包嗎？
4. BLE Notify 和 BLE Indicate 的差別是什麼？這個練習用 Notify 而不是 Indicate，在什麼場景下你需要改成 Indicate？
5. 為什麼 LoRa 的 SF（Spreading Factor）越高，傳輸距離越遠，但速率越低？這兩者的 trade-off 和你在 CAN 裡調 BRP 的 trade-off 有什麼相似之處？

---

→ [Final Project：工業感測器閘道器](./final-project-industrial-gateway.md)
