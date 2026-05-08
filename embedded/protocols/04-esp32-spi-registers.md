# Ch 4 — ESP32 SPI 暫存器

> 目標：搞清楚 ESP32 SPI2（HSPI）每個關鍵暫存器的 offset 和 bit field，寫出完整的 register-level SPI master 初始化和單次傳輸函式。不碰 `spi_bus_initialize`，不碰 `spi_device_transmit`。

---

## ESP32 SPI Controller 配置

ESP32 有 4 個 SPI controller，但只有 2 個你能用：

| Controller | 別名 | Base Address | 用途 |
|-----------|------|-------------|------|
| SPI0 | - | `0x3FF43000` | 連接 Flash，**不要動** |
| SPI1 | - | `0x3FF42000` | 連接 Flash，**不要動** |
| SPI2 | HSPI | `0x3FF64000` | 通用，本課程主要用這個 |
| SPI3 | VSPI | `0x3FF65000` | 通用，可用 |

SPI0/1 由 bootloader 和 Flash 驅動獨占，你動了就是在玩火。後面所有範例都用 SPI2。

`soc/spi_reg.h` 定義的 macro：

```c
#define REG_SPI_BASE(i)  (DR_REG_SPI1_BASE + (((i) > 1) ? \
    (((i) - 1) * 0x1000) : 0))
// SPI2 base = DR_REG_SPI1_BASE + 1 * 0x1000 = 0x3FF42000 + 0x1000
//           = 0x3FF43000 ... 等等，這對嗎？
// 實際上 ESP-IDF 直接用 DR_REG_SPI2_BASE = 0x3FF64000
// 就直接用 DR_REG_SPI2_BASE，不要用那個 macro，容易算錯
```

直接用常數最保險：

```c
#define HSPI_BASE  DR_REG_SPI2_BASE   // 0x3FF64000
```

---

## 關鍵暫存器說明

### SPI_CMD_REG（offset 0x000）

```
Bit 31-19: reserved
Bit 18:    SPI_USR    -- 寫 1 啟動一次傳輸，傳輸完硬體自動清為 0
Bit 17-0:  reserved（部分是 SPI slave 模式用）
```

```c
#define SPI_CMD_REG(base)   ((base) + 0x000)
#define SPI_USR             (1u << 18)

// 啟動傳輸
REG_SET_BIT(SPI_CMD_REG(HSPI_BASE), SPI_USR);

// 等傳輸完成（polling）
while (REG_READ(SPI_CMD_REG(HSPI_BASE)) & SPI_USR) {}
```

### SPI_CLOCK_REG（offset 0x018）

這個暫存器決定 SPI clock 頻率，是最容易算錯的一個。

```
Bit 31:    SPI_CLK_EQU_SYSCLK  -- 1 = SPI clock = APB clock（80 MHz），此時其他欄位忽略
Bit 29-18: SPI_CLKDIV_PRE      -- 預分頻（12 bits），實際分頻值 = 此值 + 1
Bit 17-12: SPI_CLKCNT_N        -- 每個 SPI clock 的 APB cycle 數減 1
Bit 11-6:  SPI_CLKCNT_H        -- high 維持 cycle 數減 1（通常設為 (N+1)/2 - 1）
Bit 5-0:   SPI_CLKCNT_L        -- low 維持 cycle 數減 1（通常設為 N）
```

Baud rate 計算公式：

```
f_spi = f_apb / ((SPI_CLKDIV_PRE + 1) * (SPI_CLKCNT_N + 1))
f_apb = 80 MHz（APB clock 預設）

例：想要 1 MHz SPI clock
  f_spi = 80 MHz / (pre+1) / (N+1) = 1 MHz
  令 pre=0（CLKDIV_PRE=0），則 N+1 = 80，N = 79
  CLKCNT_H = (79+1)/2 - 1 = 39
  CLKCNT_L = 79

例：想要 10 MHz SPI clock
  80 / (N+1) = 10，N = 7
  CLKCNT_H = 3
  CLKCNT_L = 7
```

```c
#define SPI_CLOCK_REG(base)     ((base) + 0x018)
#define SPI_CLK_EQU_SYSCLK_S   31
#define SPI_CLKDIV_PRE_S        18
#define SPI_CLKCNT_N_S          12
#define SPI_CLKCNT_H_S          6
#define SPI_CLKCNT_L_S          0

// 設定 1 MHz SPI clock（APB 80 MHz）
static void spi_set_clock(uint32_t base, uint32_t hz)
{
    uint32_t n = (80000000 / hz) - 1;
    uint32_t h = (n + 1) / 2 - 1;
    REG_WRITE(SPI_CLOCK_REG(base),
              (0u  << SPI_CLK_EQU_SYSCLK_S) |  // 不走直通
              (0u  << SPI_CLKDIV_PRE_S)     |  // 預分頻 = 1
              (n   << SPI_CLKCNT_N_S)       |
              (h   << SPI_CLKCNT_H_S)       |
              (n   << SPI_CLKCNT_L_S));
}
```

### SPI_USER_REG（offset 0x01C）

控制這次傳輸要用哪些 phase（address、dummy、data）：

```
Bit 31: SPI_USR_COMMAND  -- 是否有 command phase（通常 0）
Bit 30: SPI_ADDR         -- 是否有 address phase（通常 0，除非做 Flash 操作）
Bit 29: SPI_USR_DUMMY    -- 是否有 dummy cycle（0）
Bit 27: SPI_USR_MOSI     -- 是否有 MOSI data phase（TX：設 1）
Bit 26: SPI_USR_MISO     -- 是否有 MISO data phase（RX：設 1）
Bit 25: SPI_USR_DUMMY_IDLE -- dummy cycle 期間 SCLK 停（通常 0）
Bit 12: SPI_CK_OUT_EDGE  -- CPHA 設定：0 = CPHA=0，1 = CPHA=1
Bit 10: SPI_CS_HOLD      -- CS 在傳輸後多維持幾個 cycle（設 1 比較安全）
Bit 9:  SPI_CS_SETUP     -- CS 在傳輸前多維持幾個 cycle（設 1 比較安全）
```

```c
#define SPI_USER_REG(base)     ((base) + 0x01C)
#define SPI_USR_MOSI_S         27
#define SPI_USR_MISO_S         26
#define SPI_CK_OUT_EDGE_S      12
#define SPI_CS_HOLD_S          10
#define SPI_CS_SETUP_S         9

// Mode 0（CPHA=0）的 USER_REG，TX + RX
#define SPI_USER_TX_RX  \
    ((1u << SPI_USR_MOSI_S) | \
     (1u << SPI_USR_MISO_S) | \
     (0u << SPI_CK_OUT_EDGE_S) | \
     (1u << SPI_CS_HOLD_S)  | \
     (1u << SPI_CS_SETUP_S))
```

### SPI_USER1_REG（offset 0x020）

設定這次傳輸的 bit 數：

```
Bit 31-27: SPI_USR_MOSI_BITLEN  -- MOSI 要送的 bit 數減 1（最多 511，即 64 bytes）
Bit 26-22: SPI_USR_MISO_BITLEN  -- MISO 要收的 bit 數減 1
```

```c
#define SPI_USER1_REG(base)         ((base) + 0x020)
#define SPI_USR_MOSI_BITLEN_S       27
#define SPI_USR_MISO_BITLEN_S       22

// 設定傳輸 8 bits TX + 8 bits RX
REG_WRITE(SPI_USER1_REG(HSPI_BASE),
          ((8 - 1) << SPI_USR_MOSI_BITLEN_S) |
          ((8 - 1) << SPI_USR_MISO_BITLEN_S));
```

### SPI_PIN_REG（offset 0x034）

設定 CPOL 和 CS 極性：

```
Bit 29: SPI_CS2_DIS  -- 1 = 不用 CS2（一般設 1）
Bit 28: SPI_CS1_DIS  -- 1 = 不用 CS1（一般設 1）
Bit 27: SPI_CS0_DIS  -- 0 = 使用 CS0（一般設 0）
Bit 6:  SPI_CK_IDLE_EDGE -- CPOL 設定：0 = CPOL=0（idle low），1 = CPOL=1（idle high）
Bit 2:  SPI_MASTER_CS_POL -- 0 = CS active low（正常），1 = CS active high
```

```c
#define SPI_PIN_REG(base)       ((base) + 0x034)
#define SPI_CK_IDLE_EDGE_S      6
#define SPI_CS2_DIS_S           29
#define SPI_CS1_DIS_S           28
#define SPI_CS0_DIS_S           27

// Mode 0：CPOL=0，使用 CS0
REG_WRITE(SPI_PIN_REG(HSPI_BASE),
          (1u << SPI_CS2_DIS_S) |
          (1u << SPI_CS1_DIS_S) |
          (0u << SPI_CS0_DIS_S) |
          (0u << SPI_CK_IDLE_EDGE_S));  // CPOL=0
```

### SPI_W0_REG ~ SPI_W15_REG（offset 0x080 ~ 0x0BC）

TX 和 RX 共用這 16 個 32-bit 暫存器（512 bits = 64 bytes 緩衝區）。

傳輸前把資料寫進 `SPI_W0_REG`，傳輸完後從 `SPI_W0_REG` 讀回收到的資料。

```c
#define SPI_W0_REG(base)   ((base) + 0x080)
// SPI_Wn_REG(base, n) = (base) + 0x080 + (n) * 4

// 送一個 byte（8 bits，存在 SPI_W0 的 bit 7:0）
REG_WRITE(SPI_W0_REG(HSPI_BASE), (uint32_t)tx_byte);

// 傳輸完後讀回收到的 byte
uint8_t rx = (uint8_t)(REG_READ(SPI_W0_REG(HSPI_BASE)) & 0xFF);
```

---

## 完整 SPI Master 初始化（Register-Level）

```c
#include "soc/spi_reg.h"
#include "soc/gpio_reg.h"
#include "soc/io_mux_reg.h"
#include "soc/dport_reg.h"
#include "soc/gpio_sig_map.h"

// SPI2（HSPI）腳位（可以透過 GPIO matrix 任意換）
#define HSPI_SCLK_PIN   18
#define HSPI_MOSI_PIN   23
#define HSPI_MISO_PIN   19
#define HSPI_CS_PIN     5

#define HSPI_BASE  DR_REG_SPI2_BASE

static void spi2_gpio_init(void)
{
    // 四個 pad 全部設為 GPIO matrix 模式（MCU_SEL=2）
    const uint32_t gpio_cfg_out = (2u << MCU_SEL_S) | (2u << FUN_DRV_S);
    const uint32_t gpio_cfg_in  = (2u << MCU_SEL_S) | (2u << FUN_DRV_S) | FUN_IE;

    REG_WRITE(GPIO_PIN_MUX_REG[HSPI_SCLK_PIN], gpio_cfg_out);
    REG_WRITE(GPIO_PIN_MUX_REG[HSPI_MOSI_PIN], gpio_cfg_out);
    REG_WRITE(GPIO_PIN_MUX_REG[HSPI_MISO_PIN], gpio_cfg_in);
    REG_WRITE(GPIO_PIN_MUX_REG[HSPI_CS_PIN],   gpio_cfg_out);

    // GPIO matrix：把 SPI2 的各信號接到對應 GPIO
    // 輸出信號（SPI -> GPIO pad）
    REG_WRITE(GPIO_FUNC0_OUT_SEL_CFG_REG + HSPI_SCLK_PIN * 4, HSPICLK_OUT_IDX);
    REG_WRITE(GPIO_FUNC0_OUT_SEL_CFG_REG + HSPI_MOSI_PIN * 4, HSPID_OUT_IDX);
    REG_WRITE(GPIO_FUNC0_OUT_SEL_CFG_REG + HSPI_CS_PIN   * 4, HSPICS0_OUT_IDX);

    // 輸入信號（GPIO pad -> SPI）
    REG_WRITE(GPIO_FUNC_IN_SEL_CFG_REG(HSPIQ_IN_IDX), HSPI_MISO_PIN);
}

void spi2_init(uint32_t clock_hz)
{
    // Step 1: clock enable + deassert reset
    SET_PERI_REG_MASK(DPORT_PERIP_CLK_EN_REG, DPORT_SPI2_CLK_EN);
    CLEAR_PERI_REG_MASK(DPORT_PERIP_RST_EN_REG, DPORT_SPI2_RST);

    // Step 2: GPIO matrix
    spi2_gpio_init();

    // Step 3: Master mode（clear SLAVE bit），禁用 DMA（no in_link/out_link）
    REG_WRITE(SPI_SLAVE_REG(HSPI_BASE), 0);

    // Step 4: Clock
    uint32_t n = (80000000u / clock_hz) - 1;
    uint32_t h = (n + 1) / 2 - 1;
    REG_WRITE(SPI_CLOCK_REG(HSPI_BASE),
              (0u << 31) |         // SPI_CLK_EQU_SYSCLK = 0
              (0u << 18) |         // CLKDIV_PRE = 0
              (n  << 12) |         // CLKCNT_N
              (h  << 6)  |         // CLKCNT_H
              (n  << 0));          // CLKCNT_L

    // Step 5: PIN（CPOL=0 for Mode 0/1；CPOL=1 for Mode 2/3）
    REG_WRITE(SPI_PIN_REG(HSPI_BASE),
              (1u << 29) |   // CS2_DIS
              (1u << 28) |   // CS1_DIS
              (0u << 27) |   // CS0_EN
              (0u << 6));    // CK_IDLE_EDGE = 0 (CPOL=0)

    // Step 6: USER（CS_SETUP, CS_HOLD，CPHA=0）
    REG_WRITE(SPI_USER_REG(HSPI_BASE),
              (1u << 9)  |   // CS_SETUP
              (1u << 10) |   // CS_HOLD
              (0u << 12));   // CK_OUT_EDGE = 0 (CPHA=0)
}
```

---

## 單次傳輸函式

```c
// 傳輸一個 byte（全雙工：同時送 tx_byte，收到 rx_byte）
// 使用 SPI2，polling 等待完成
static void spi2_transfer(uint8_t tx_byte, uint8_t *rx_byte)
{
    // 設定本次傳輸：TX + RX 各 8 bits
    REG_WRITE(SPI_USER_REG(HSPI_BASE),
              (1u << 27) |   // USR_MOSI = 1
              (1u << 26) |   // USR_MISO = 1
              (1u << 10) |   // CS_HOLD
              (1u << 9));    // CS_SETUP

    REG_WRITE(SPI_USER1_REG(HSPI_BASE),
              ((8u - 1u) << 27) |   // MOSI_BITLEN = 7（送 8 bits）
              ((8u - 1u) << 22));   // MISO_BITLEN = 7（收 8 bits）

    // 把 TX data 放進 W0（LSB 對齊到 bit0）
    REG_WRITE(SPI_W0_REG(HSPI_BASE), (uint32_t)tx_byte);

    // 啟動傳輸
    REG_SET_BIT(SPI_CMD_REG(HSPI_BASE), SPI_USR);

    // 等傳輸完成（SPI_USR bit 自動清零）
    while (REG_READ(SPI_CMD_REG(HSPI_BASE)) & SPI_USR) {}

    // 讀回收到的資料
    if (rx_byte) {
        *rx_byte = (uint8_t)(REG_READ(SPI_W0_REG(HSPI_BASE)) & 0xFF);
    }
}
```

這個函式很直白，沒有隱藏邏輯。每次傳輸的流程就是：填 USER_REG → 填 USER1_REG → 填 W0 → 拉 SPI_USR → 等 SPI_USR 清 → 讀 W0。

---

## 自我檢核

- [ ] 能說出 SPI0/1 為什麼不能碰
- [ ] 能解釋 `SPI_CLKCNT_N` 和 APB clock 的關係，手算 1 MHz 的設定值
- [ ] 知道 CPHA 設定在 `SPI_USER_REG` 的哪個 bit，CPOL 在哪個 bit
- [ ] 能說出 `SPI_W0_REG` 的用途，以及 TX 和 RX 為什麼共用
- [ ] 把 `spi2_init` + `spi2_transfer` 燒進板子，用邏輯分析儀確認 SCLK 頻率正確、CS 極性正確

暫存器搞定了，下一章把 SPI 驅動接到真實裝置：SX1276 LoRa 模組。

→ [Ch 5 實作：SPI → SX1276 LoRa 模組初始化](./05-spi-sx1276-init.md)
