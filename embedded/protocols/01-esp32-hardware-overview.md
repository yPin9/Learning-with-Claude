# Ch 1 — ESP32 硬體概覽

> 目標：理解 ESP32 的核心架構、記憶體映射、peripheral base address、GPIO matrix、clock gating 機制。這些是後續所有 register-level 操作的基礎，沒有這張地圖，你只是在瞎猜位址。

---

## Xtensa LX6 雙核架構

ESP32 搭載兩顆 Tensilica（Tensilica）Xtensa LX6 CPU，你有 ARM Cortex-A 底子，用對比方式理解最快：

| 面向 | Xtensa LX6（ESP32） | ARM Cortex-A53（樹莓派類） |
|------|---------------------|--------------------------|
| ISA 類型 | RISC，可配置，有 QDSP 擴展 | RISC，ARMv8-A |
| 主頻 | 240 MHz（可降頻至 80/160） | 1+ GHz |
| 核心數 | 2（PRO_CPU / APP_CPU） | 4 |
| 硬體浮點 | 有（FPU，單精度） | 有（VFPv4 / NEON） |
| MMU | 無，MPU 概念用 PMS 實現 | 有完整 MMU |
| 中斷模型 | 32 個中斷來源，7 個優先級 | GIC（Generic Interrupt Controller） |
| 呼叫規範 | a0-a15 窗口暫存器，a0=RA | x0-x30，x30=LR |
| Endianness | Little-endian | Little-endian（LE 模式） |

ESP32 沒有 MMU，所以不跑 Linux，跑 FreeRTOS。兩顆核心共享一條 APB bus（Advanced Peripheral Bus）連接所有 peripheral，這條 bus 最高 80 MHz。

---

## 記憶體映射

```
位址範圍                  名稱           大小    說明
0x00000000 - 0x3F3FFFFF  (reserved)
0x3F400000 - 0x3F7FFFFF  DROM           4 MB    Flash 資料，read-only，可 cache
0x3F800000 - 0x3FBFFFFF  外部 SRAM      4 MB    PSRAM（選配），需接 SPI PSRAM
0x3FC00000 - 0x3FDFFFFF  (reserved)
0x3FE00000 - 0x3FEFFFFF  (reserved)
0x3FF00000 - 0x3FF0FFFF  DPORT          64 KB   系統控制暫存器（clock/reset）
0x3FF10000 - 0x3FF3FFFF  各 Peripheral  192 KB  APB peripheral 暫存器
0x3FF40000 - 0x3FF7FFFF  GPIO/IO MUX    256 KB  GPIO matrix 和 IO pad 設定
0x3FF80000 - 0x3FFFFFFF  RTC memory     512 KB  含 RTC fast/slow，deep sleep 保留
0x40000000 - 0x4005FFFF  Internal ROM   384 KB  bootloader 和 ROM 函式
0x40060000 - 0x4006FFFF  Internal SRAM0 64 KB   IRAM（指令 RAM，可執行）
0x40070000 - 0x4009FFFF  Internal SRAM1 192 KB  IRAM / DRAM overlay
0x400A0000 - 0x400AFFFF  (SRAM1 DRAM)
0x400C0000 - 0x400DFFFF  RTC fast mem   128 KB  指令可執行，deep sleep 保留
0x400D0000 - 0x400FFFFF  IROM           ~1 MB   Flash 程式碼，可 cache 執行
0x50000000 - 0x50001FFF  RTC slow mem   8 KB    ULP coprocessor 用
```

幾個重點：
- IRAM（Internal RAM）：程式碼放這裡執行不需要 cache，延遲固定。時間敏感的 ISR 要加 `IRAM_ATTR` 屬性。
- DRAM（Data RAM）：stack 和 heap 在這裡。
- DROM / IROM：Flash 透過 cache 對應到這個範圍，cache miss 有隨機延遲。
- RTC memory：進 deep sleep 後這塊保留，適合存 wakeup 計數器之類的狀態。

---

## Peripheral Base Address

ESP32 的 peripheral 全部掛在 APB 上，base address 都定義在 ESP-IDF 的 `components/soc/esp32/include/soc/soc.h` 和各個 `*_reg.h`：

```c
// 常用 peripheral base address（從 soc.h）
#define DR_REG_DPORT_BASE     0x3FF00000
#define DR_REG_RSA_BASE       0x3FF02000
#define DR_REG_SHA_BASE       0x3FF03000
#define DR_REG_I2S0_BASE      0x3FF4F000
#define DR_REG_UART_BASE      0x3FF40000
#define DR_REG_UART1_BASE     0x3FF50000
#define DR_REG_UART2_BASE     0x3FF6E000
#define DR_REG_SPI1_BASE      0x3FF42000   // Flash SPI，不要碰
#define DR_REG_SPI2_BASE      0x3FF64000   // HSPI，這個用
#define DR_REG_SPI3_BASE      0x3FF65000   // VSPI
#define DR_REG_I2C_EXT_BASE   0x3FF53000   // I2C0
#define DR_REG_I2C_EXT1_BASE  0x3FF67000   // I2C1
#define DR_REG_GPIO_BASE      0x3FF44000
#define DR_REG_IO_MUX_BASE    0x3FF49000
#define DR_REG_TWAI_BASE      0x3FF6B000   // CAN/TWAI
#define DR_REG_RTC_BASE       0x3FF48000
#define DR_REG_EMAC_BASE      0x3FF69000
```

---

## volatile pointer 存取 Peripheral 的寫法

有 ARM 底子就懂：peripheral 暫存器必須用 `volatile`，否則編譯器可能優化掉讀/寫。

```c
// 最原始的寫法
#define SPI2_CMD_REG  (*(volatile uint32_t *)(DR_REG_SPI2_BASE + 0x00))
#define SPI2_ADDR_REG (*(volatile uint32_t *)(DR_REG_SPI2_BASE + 0x04))

// 直接操作
SPI2_CMD_REG = (1u << 18);   // 設 SPI_USR bit 啟動傳輸
uint32_t val = SPI2_CMD_REG; // 讀回確認

// 或者不定義 macro，直接算位址
volatile uint32_t *spi2_cmd = (volatile uint32_t *)(DR_REG_SPI2_BASE + 0x00);
*spi2_cmd = (1u << 18);
```

---

## ESP-IDF 的 REG_READ / REG_WRITE / SET_PERI_REG_BITS macro

ESP-IDF 幫你包好了幾個 macro，定義在 `components/soc/include/soc/soc.h`：

```c
// 讀一個 32-bit peripheral 暫存器
#define REG_READ(addr)       (*(volatile uint32_t *)(addr))

// 寫一個 32-bit peripheral 暫存器
#define REG_WRITE(addr, val) (*(volatile uint32_t *)(addr)) = (val)

// 設定特定 bit（read-modify-write）
#define REG_SET_BIT(reg, bit)   (*(volatile uint32_t *)(reg) |= (bit))

// 清除特定 bit（read-modify-write）
#define REG_CLR_BIT(reg, bit)   (*(volatile uint32_t *)(reg) &= ~(bit))

// 設定 bit field（mask + value）
// SET_PERI_REG_BITS(reg, mask, value, shift)
// 例如設 SPI_CLKCNT_N 欄位（bits 19:12，共 8 bits）為 3：
// SET_PERI_REG_BITS(SPI_CLOCK_REG(2), SPI_CLKCNT_N, 3, SPI_CLKCNT_N_S)
#define SET_PERI_REG_BITS(reg, bit_map, value, shift) \
    (REG_WRITE((reg), \
        (REG_READ(reg) & ~((bit_map) << (shift))) | \
        (((value) & (bit_map)) << (shift))))

// 讀取 bit field
#define GET_PERI_REG_BITS2(reg, mask, shift) \
    ((REG_READ(reg) >> (shift)) & (mask))
```

這些 macro 本質上就是 `volatile uint32_t *` 存取，沒有魔法。用 `REG_WRITE` 比裸寫 `(*(volatile uint32_t *)...)` 可讀性高，本課程混用兩種寫法。

---

## GPIO Matrix

這是 ESP32 設計上很聰明的一個功能。傳統微控器，每個 GPIO 只能連到固定的 peripheral（例如 SPI2 的 MOSI 一定是 GPIO13）。ESP32 的 GPIO matrix 打破這個限制：

```
任意 GPIO pad
     |
     v
+------------------+
|   GPIO Matrix    |   <-- 軟體可配置的交換矩陣
|  (輸入信號路由)   |
|  (輸出信號路由)   |
+------------------+
     |
     v
任意 Peripheral 信號
（SPI2_MOSI, I2C0_SDA, UART1_TX, ...）
```

具體實現：
- 每個 GPIO pad 有一個 `GPIO_FUNCn_OUT_SEL` 欄位，決定哪個 peripheral 信號輸出到這個 pad
- 每個 peripheral 輸入信號有一個 `GPIO_FUNCx_IN_SEL_CFG_REG`，決定從哪個 GPIO pad 讀入

```c
// 例：把 SPI2 的 MOSI 信號（信號編號 263）路由到 GPIO23
// GPIO_FUNC23_OUT_SEL_CFG_REG 設定 GPIO23 的輸出來源
#define SPIOUTMOSI_IDX   263   // 定義在 soc/gpio_sig_map.h
REG_WRITE(GPIO_FUNC23_OUT_SEL_CFG_REG, SPIOUTMOSI_IDX);

// 把 GPIO19 讀入給 SPI2 的 MISO 信號
// GPIO_FUNC_IN_SEL_CFG_REG(SPIQ_IN_IDX) 設定 MISO 的輸入來源
#define SPIQ_IN_IDX      262
REG_WRITE(GPIO_FUNC_IN_SEL_CFG_REG(SPIQ_IN_IDX), 19);  // 從 GPIO19 讀
```

所有信號編號定義在 `soc/gpio_sig_map.h`，有幾百個。

這個設計的代價：GPIO matrix 有幾十 ns 的額外延遲，對高速 SPI（>26 MHz）不利，此時要改用 IO_MUX 直通（bypass GPIO matrix）。

---

## Clock Gating

ESP32 預設大部分 peripheral 的時鐘是關著的（省電），使用前必須先開。控制暫存器在 DPORT（`0x3FF00000`）：

```c
// soc/dport_reg.h 裡定義
// 開 SPI2（HSPI）的時鐘
SET_PERI_REG_MASK(DPORT_PERIP_CLK_EN_REG, DPORT_SPI2_CLK_EN);

// 解除 SPI2 的 reset（reset 狀態下暫存器寫入無效）
CLEAR_PERI_REG_MASK(DPORT_PERIP_RST_EN_REG, DPORT_SPI2_RST);

// 開 I2C0 的時鐘
SET_PERI_REG_MASK(DPORT_PERIP_CLK_EN_REG, DPORT_I2C_EXT0_CLK_EN);
CLEAR_PERI_REG_MASK(DPORT_PERIP_RST_EN_REG, DPORT_I2C_EXT0_RST);
```

忘記開 clock，暫存器讀回全 0 或全 1，寫進去沒效，是常見的卡關原因。在有 ARM 底子的你看來，這跟 ARM 的 RCC/APSRST 機制是同樣的概念。

---

## 自我檢核

- [ ] 能說出 IRAM 和 DROM 的差別，以及 `IRAM_ATTR` 的用途
- [ ] 查到 SPI2、I2C0、GPIO 的 base address
- [ ] 能手寫 `volatile uint32_t *` 讀寫一個 peripheral 暫存器
- [ ] 理解 `REG_WRITE` 和 `SET_PERI_REG_BITS` 的展開結果
- [ ] 知道 GPIO matrix 的作用，以及繞過它用 IO_MUX 的原因
- [ ] 知道操作 SPI2 前要先對 DPORT 開 clock 和解 reset

有了這份地圖，接下來建立一套可複用的 register-level 驅動框架。

→ [Ch 2 Register-Level 驅動框架](./02-register-level-driver-framework.md)
