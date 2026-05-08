# Ch 5 — 實作：SPI → SX1276 LoRa 模組初始化

> 目標：把 Ch 4 的 SPI2 驅動接到 SX1276 LoRa 模組，完成讀寫暫存器函式，跑通初始化序列——讀回 RegVersion = 0x12 是第一個里程碑。邏輯分析儀驗波形是必做的，不是選項。

---

## SX1276 SPI 協議格式

SX1276 的 SPI 協議非常簡單，每次傳輸固定 2 bytes：

```
第 1 byte：位址 byte
  Bit 7:   W/R  -- 1 = 寫，0 = 讀
  Bit 6:0: addr -- 暫存器位址（7 bits，0x00–0x7F）

第 2 byte：資料 byte
  寫操作：master 送出要寫入的值
  讀操作：slave 送出暫存器內容，master 送 0x00（dummy）
```

時序圖：

```
寫操作：寫 0x42 位址，寫入值 0xAB
CS    ‾‾|_____________________________|‾‾
SCLK     |‾|_|‾|_|‾|_|‾|‾|‾|_|‾|_|‾|_|‾|_|
MOSI     [1][0][1][0][0][0][1][0][1][0][1][0][1][0][1][1]
          ^ addr byte: 0xC2 (W=1, addr=0x42)   ^ data: 0xAB

讀操作：讀 0x42 位址
CS    ‾‾|_____________________________|‾‾
SCLK     |‾|_|‾|_|‾|_|‾|_|‾|_|‾|_|‾|_|‾|_|
MOSI     [0][1][0][0][0][0][1][0][0][0][0][0][0][0][0][0]
          ^ addr byte: 0x42 (W=0, addr=0x42)   ^ dummy
MISO     [x][x][x][x][x][x][x][x][r][r][r][r][r][r][r][r]
                                   ^ 暫存器實際值（8 bits）
```

關鍵：SX1276 使用 SPI Mode 0（CPOL=0, CPHA=0），這正是 Ch 4 設定的預設值。

---

## 讀寫暫存器函式

接線建議：

| SX1276 腳位 | ESP32 GPIO |
|------------|-----------|
| SCK  | GPIO18（HSPI_SCLK） |
| MOSI | GPIO23（HSPI_MOSI） |
| MISO | GPIO19（HSPI_MISO） |
| NSS（CS）| GPIO5 |
| RESET | GPIO14 |
| DIO0 | GPIO26（中斷，暫時不接） |
| 3.3V | 3.3V |
| GND | GND |

```c
#include <stdint.h>
#include "soc/gpio_reg.h"
#include "soc/spi_reg.h"
#include "soc/dport_reg.h"
#include "soc/gpio_sig_map.h"
#include "soc/io_mux_reg.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define HSPI_BASE    DR_REG_SPI2_BASE
#define SX1276_CS    5    // GPIO5
#define SX1276_RESET 14   // GPIO14

// --- CS 控制（手動控制，不靠 SPI controller 的自動 CS）
// 為什麼手動？因為某些 SX1276 指令需要 CS 在多個 byte 之間保持低，
// 而 ESP32 SPI 的自動 CS 每次傳輸結束就會拉高。
static inline void sx1276_cs_low(void)
{
    REG_WRITE(GPIO_OUT_W1TC_REG, (1u << SX1276_CS));
}

static inline void sx1276_cs_high(void)
{
    REG_WRITE(GPIO_OUT_W1TS_REG, (1u << SX1276_CS));
}

// 只送 1 byte，只收 1 byte，不動 CS（呼叫者管 CS）
static uint8_t spi2_xfer_byte(uint8_t tx)
{
    // USR_MOSI + USR_MISO，8 bits
    REG_WRITE(SPI_USER_REG(HSPI_BASE),
              (1u << 27) | (1u << 26) | (1u << 10) | (1u << 9));
    REG_WRITE(SPI_USER1_REG(HSPI_BASE),
              (7u << 27) | (7u << 22));
    REG_WRITE(SPI_W0_REG(HSPI_BASE), (uint32_t)tx);
    REG_SET_BIT(SPI_CMD_REG(HSPI_BASE), SPI_USR);
    while (REG_READ(SPI_CMD_REG(HSPI_BASE)) & SPI_USR) {}
    return (uint8_t)(REG_READ(SPI_W0_REG(HSPI_BASE)) & 0xFF);
}

// 寫暫存器：addr（bit7=1） + val
void sx1276_write_reg(uint8_t addr, uint8_t val)
{
    sx1276_cs_low();
    spi2_xfer_byte(addr | 0x80);   // bit7=1：寫操作
    spi2_xfer_byte(val);
    sx1276_cs_high();
}

// 讀暫存器：addr（bit7=0） + dummy
uint8_t sx1276_read_reg(uint8_t addr)
{
    sx1276_cs_low();
    spi2_xfer_byte(addr & 0x7F);   // bit7=0：讀操作
    uint8_t val = spi2_xfer_byte(0x00);  // dummy byte，讀取 MISO
    sx1276_cs_high();
    return val;
}
```

---

## SX1276 初始化序列

### 步驟 0：準備 GPIO

CS 和 RESET 腳位設為輸出，CS 預設拉高（deselect），RESET 預設拉高（非 reset 狀態）：

```c
static void sx1276_gpio_init(void)
{
    // CS 和 RESET 設為 output
    REG_SET_BIT(GPIO_ENABLE_REG, (1u << SX1276_CS) | (1u << SX1276_RESET));

    // IO_MUX：GPIO matrix 模式
    REG_WRITE(GPIO_PIN_MUX_REG[SX1276_CS],
              (2u << MCU_SEL_S) | (2u << FUN_DRV_S));
    REG_WRITE(GPIO_PIN_MUX_REG[SX1276_RESET],
              (2u << MCU_SEL_S) | (2u << FUN_DRV_S));

    // GPIO matrix output = SIG_GPIO_OUT_IDX（由 GPIO_OUT_REG 控制）
    REG_WRITE(GPIO_FUNC0_OUT_SEL_CFG_REG + SX1276_CS    * 4, SIG_GPIO_OUT_IDX);
    REG_WRITE(GPIO_FUNC0_OUT_SEL_CFG_REG + SX1276_RESET * 4, SIG_GPIO_OUT_IDX);

    // 預設狀態
    sx1276_cs_high();
    REG_WRITE(GPIO_OUT_W1TS_REG, (1u << SX1276_RESET));  // RESET high（放開）
}
```

### 步驟 1：Reset SX1276

SX1276 的 RESET 是 active-low，拉低至少 100 µs 再放開，等待 5 ms 讓晶片完成 POR（Power-on Reset）：

```c
static void sx1276_reset(void)
{
    REG_WRITE(GPIO_OUT_W1TC_REG, (1u << SX1276_RESET));  // RESET low
    vTaskDelay(pdMS_TO_TICKS(1));                        // 1 ms > 100 µs
    REG_WRITE(GPIO_OUT_W1TS_REG, (1u << SX1276_RESET));  // RESET high
    vTaskDelay(pdMS_TO_TICKS(5));                        // 等 POR 完成
}
```

### 步驟 2：驗證 RegVersion

SX1276 的 RegVersion（位址 `0x42`）在 chip revision 1 上固定回傳 `0x12`。這是驗證 SPI 通訊有沒有問題的第一關：

```c
// RegVersion = 0x42
#define SX1276_REG_VERSION  0x42

static esp_err_t sx1276_check_version(void)
{
    uint8_t ver = sx1276_read_reg(SX1276_REG_VERSION);
    if (ver != 0x12) {
        // 讀回 0xFF 通常是 CPOL/CPHA 設錯
        // 讀回 0x00 通常是 MISO 線沒接或接錯
        printf("SX1276 version mismatch: got 0x%02X, expected 0x12\n", ver);
        return ESP_FAIL;
    }
    printf("SX1276 version: 0x%02X (OK)\n", ver);
    return ESP_OK;
}
```

### 步驟 3：進入 Sleep mode

SX1276 上電後預設是 Standby mode。要切換 LoRa mode，必須先進 Sleep mode（datasheet 規定）：

```c
// RegOpMode = 0x01
// Bit 7:    LongRangeMode  -- 0 = FSK/OOK，1 = LoRa
// Bit 6:    AccessSharedReg（LoRa mode 下設 0）
// Bit 2:0:  Mode  -- 000 = Sleep，001 = Standby，011 = TX，101 = RX continuous
#define SX1276_REG_OP_MODE   0x01
#define SX1276_MODE_SLEEP    0x00
#define SX1276_MODE_STANDBY  0x01
#define SX1276_LORA_MODE     0x80

static void sx1276_set_sleep(void)
{
    sx1276_write_reg(SX1276_REG_OP_MODE, SX1276_MODE_SLEEP);
    vTaskDelay(pdMS_TO_TICKS(1));
}
```

### 步驟 4：切換到 LoRa mode

LoRa mode bit（bit 7）只能在 Sleep mode 下設定：

```c
static void sx1276_set_lora_mode(void)
{
    // 確保在 Sleep mode，然後設 LongRangeMode = 1
    sx1276_write_reg(SX1276_REG_OP_MODE, SX1276_LORA_MODE | SX1276_MODE_SLEEP);
    vTaskDelay(pdMS_TO_TICKS(1));

    // 回到 Standby
    sx1276_write_reg(SX1276_REG_OP_MODE, SX1276_LORA_MODE | SX1276_MODE_STANDBY);
    vTaskDelay(pdMS_TO_TICKS(1));

    // 驗證模式設定成功
    uint8_t mode = sx1276_read_reg(SX1276_REG_OP_MODE);
    printf("OpMode after LoRa set: 0x%02X\n", mode);
    // 應得 0x81（LongRangeMode=1, Mode=001 Standby）
}
```

### 步驟 5：設定頻率（915 MHz）

SX1276 的頻率由三個暫存器設定（RegFrMsb/Mid/Lsb）：

```
f_rf = (f_xosc * Frf) / 2^19
f_xosc = 32 MHz（SX1276 的晶振）
Frf 是 24-bit 整數

915 MHz -> Frf = 915e6 * 2^19 / 32e6 = 915 * 524288 / 32 = 14991360 = 0xE4C000
```

```c
#define SX1276_REG_FR_MSB   0x06
#define SX1276_REG_FR_MID   0x07
#define SX1276_REG_FR_LSB   0x08

static void sx1276_set_frequency_915mhz(void)
{
    // Frf = 915e6 * 2^19 / 32e6 = 0xE4C000
    sx1276_write_reg(SX1276_REG_FR_MSB, 0xE4);
    sx1276_write_reg(SX1276_REG_FR_MID, 0xC0);
    sx1276_write_reg(SX1276_REG_FR_LSB, 0x00);
}
```

台灣 LoRa 頻段是 AS923（920–923 MHz），如果要用 923 MHz：
`Frf = 923e6 * 2^19 / 32e6 = 0xE6D000`

### 步驟 6：設定 TX Power

RegPaConfig（`0x09`）：

```
Bit 7:    PaSelect  -- 0 = RFO pin（最高 14 dBm），1 = PA_BOOST pin（最高 20 dBm）
Bit 6:4:  MaxPower  -- RFO 最大輸出功率選擇（通常 0x7）
Bit 3:0:  OutputPower -- 輸出功率（RFO 時 Pout = MaxPower - 15 + OutputPower）
```

```c
#define SX1276_REG_PA_CONFIG  0x09

static void sx1276_set_tx_power(int8_t dbm)
{
    // 使用 PA_BOOST（+20 dBm 最大），PaSelect=1
    // 實際功率 = 2 + OutputPower（PA_BOOST 模式）
    // 17 dBm -> OutputPower = 15 = 0x0F
    uint8_t output_power;
    if (dbm < 2) dbm = 2;
    if (dbm > 17) dbm = 17;
    output_power = (uint8_t)(dbm - 2);

    sx1276_write_reg(SX1276_REG_PA_CONFIG,
                     0x80 |           // PaSelect = 1 (PA_BOOST)
                     (0x7 << 4) |     // MaxPower = 7
                     output_power);
}
```

### 完整初始化呼叫順序

```c
void sx1276_init(void)
{
    // 先初始化 SPI2（1 MHz，Mode 0）
    spi2_init(1000000);

    // 再初始化 GPIO
    sx1276_gpio_init();

    // SX1276 初始化序列
    sx1276_reset();

    if (sx1276_check_version() != ESP_OK) {
        printf("SX1276 not found!\n");
        return;
    }

    sx1276_set_sleep();
    sx1276_set_lora_mode();
    sx1276_set_frequency_915mhz();
    sx1276_set_tx_power(14);

    printf("SX1276 init OK, ready in LoRa Standby mode\n");
}
```

---

## 邏輯分析儀預期波形

用 Logic 2 的 SPI decoder（CPOL=0, CPHA=0）抓 `sx1276_check_version()` 的波形：

```
讀 RegVersion（addr=0x42, W=0）：
CS    ‾‾‾|___________________________|‾‾‾
SCLK        ||||||||||||||||||||||||
MOSI     0x42 (01000010)  0x00 (dummy)
MISO     0xXX (don't care) 0x12

Logic 2 SPI decoder 應顯示：
  Frame 1:  MOSI=0x42  MISO=0x--
  Frame 2:  MOSI=0x00  MISO=0x12
```

---

## 常見失敗模式

| 現象 | 可能原因 | 解法 |
|------|---------|------|
| RegVersion 讀回 0xFF | CPOL/CPHA 設錯，slave 時脈邊緣不對 | 確認用 Mode 0，邏輯分析儀看 SCLK idle 電位 |
| RegVersion 讀回 0x00 | MISO 沒接或 GPIO matrix routing 沒設 | 量 MISO 針腳，確認 GPIO_FUNC_IN_SEL 設定 |
| RegVersion 讀回隨機值 | VCC 不穩，或 GND 沒共地 | 量板子 3.3V，確認 ESP32 GND 和 SX1276 GND 共接 |
| CS 沒動作 | CS 用了 SPI controller 的自動 CS，但 GPIO matrix 沒設正確 | 改手動控制 CS，確認 GPIO_OUT_W1TS/W1TC |
| 初始化後 OpMode 讀回 0x00 | 沒有在 Sleep mode 下設 LoRa bit | 確認步驟順序：先 Sleep 再設 bit7 |

---

## 自我檢核

- [ ] 能說出 SX1276 SPI 位址 byte 的 bit7 代表什麼
- [ ] `sx1276_read_reg(0x42)` 傳輸期間，MOSI 第一個 byte 的值是多少（十六進位）
- [ ] 知道為什麼 LoRa mode bit 只能在 Sleep mode 下設定
- [ ] 能手算 923 MHz 對應的 Frf 值
- [ ] 邏輯分析儀看到 RegVersion 讀操作，MISO 回傳 0x12
- [ ] 排查過至少一種常見失敗模式

SPI 這條線打通了，接下來看 I2C——協議更複雜，但只用兩條線。

→ [Ch 6 I2C 協議原理](./06-i2c-protocol.md)
