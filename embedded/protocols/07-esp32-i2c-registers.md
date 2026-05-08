# Ch 7 — ESP32 I2C 暫存器

> 目標：掌握 ESP32 I2C controller 的暫存器結構，特別是 command link 機制，能用純暫存器操作完成 I2C master 初始化與一筆完整的 write transaction。

## ESP32 I2C 控制器概覽

ESP32 有兩個獨立的 I2C controller：

| 控制器 | Base Address | 預設 GPIO（可透過 IO MUX 重新分配） |
|--------|-------------|-------------------------------------|
| I2C0 | `0x3FF53000` (`DR_REG_I2C_EXT_BASE`) | SDA=GPIO21, SCL=GPIO22 |
| I2C1 | `0x3FF67000` | 無預設，需手動設定 IO MUX |

ESP32 I2C controller 的設計和 STM32 或 Nordic 的做法很不一樣：它採用 **command link（指令連結）** 模式——把一整個 transaction 的操作序列先寫進 command register，再一次觸發執行。硬體自動處理 START、位址、資料、ACK、STOP 的時序，CPU 不用一個 bit 一個 bit 去控制。

## 暫存器地圖（關鍵子集）

所有 offset 相對於 base address（I2C0 = `0x3FF53000`）。

```c
#define DR_REG_I2C_EXT_BASE     0x3FF53000
#define DR_REG_I2C1_BASE        0x3FF67000

// 時序相關
#define I2C_SCL_LOW_PERIOD_REG  0x00  // SCL 低電位持續時間
#define I2C_CTR_REG             0x04  // 控制暫存器
#define I2C_SR_REG              0x08  // 狀態暫存器（唯讀）
#define I2C_TO_REG              0x0C  // timeout 計數
#define I2C_SLAVE_ADDR_REG      0x10  // slave 模式位址（master 模式不用）
#define I2C_RXFIFO_ST_REG       0x14  // RX FIFO 狀態
#define I2C_FIFO_CONF_REG       0x18  // FIFO 設定
#define I2C_DATA_APB_REG        0x1C  // FIFO 存取口（APB 方式）
#define I2C_INT_RAW_REG         0x20  // 原始中斷旗標
#define I2C_INT_CLR_REG         0x24  // 清除中斷
#define I2C_INT_ENA_REG         0x28  // 中斷致能
#define I2C_INT_STATUS_REG      0x2C  // 中斷狀態（遮罩後）
#define I2C_SDA_HOLD_REG        0x30  // SDA hold time
#define I2C_SDA_SAMPLE_REG      0x34  // SDA sample time
#define I2C_SCL_HIGH_PERIOD_REG 0x38  // SCL 高電位持續時間
#define I2C_SCL_START_HOLD_REG  0x40  // START 前 SCL hold
#define I2C_SCL_RSTART_SETUP_REG 0x44 // Repeated START setup
#define I2C_SCL_STOP_HOLD_REG   0x48  // STOP 前 hold
#define I2C_SCL_STOP_SETUP_REG  0x4C  // STOP setup
#define I2C_SCL_FILTER_CFG_REG  0x50  // SCL glitch filter
#define I2C_SDA_FILTER_CFG_REG  0x54  // SDA glitch filter
// Command registers: 16 個，offset 0x58 ~ 0x94
#define I2C_COMD0_REG           0x58
// I2C_COMDn_REG = I2C_COMD0_REG + n * 4  (n = 0..15)
```

## 時序暫存器與 Baud Rate 計算

ESP32 I2C 時序的基本時鐘源是 **APB clock（80 MHz）**。

### SCL 低電位 / 高電位時間

```
I2C_SCL_LOW_PERIOD_REG  [bit 13:0]  scl_low_period
I2C_SCL_HIGH_PERIOD_REG [bit 13:0]  scl_high_period

SCL period = (scl_low_period + 1 + scl_high_period + 1) / F_APB
```

計算 100 kHz 標準速率（APB = 80 MHz）：

```
目標 period = 1 / 100kHz = 10000 ns
APB 每 tick = 1 / 80MHz = 12.5 ns

每半週期 ticks = 5000 ns / 12.5 ns = 400 ticks
scl_low_period  = 400 - 1 = 399
scl_high_period = 400 - 1 = 399
```

計算 400 kHz 快速速率：

```
目標 period = 1 / 400kHz = 2500 ns
每半週期 ticks = 1250 ns / 12.5 ns = 100 ticks
scl_low_period  = 100 - 1 = 99
scl_high_period = 100 - 1 = 99
```

實際上低電位通常比高電位稍長（I2C 規範允許不對稱），可以讓 scl_low_period 多幾個 tick。

### SDA Hold / Sample 時間

```
I2C_SDA_HOLD_REG   [bit 9:0]  sda_hold_time
    SDA 在 SCL 下降沿後保持的 ticks（資料轉換前的保護時間）

I2C_SDA_SAMPLE_REG [bit 9:0]  sda_sample_time
    SCL 上升沿後採樣 SDA 的 ticks（相對於 SCL 上升）
```

典型值：`sda_hold_time = 10`，`sda_sample_time = 10`（對 100 kHz 足夠）。

## CTR_REG 控制暫存器

```
I2C_CTR_REG [bit 0]  sda_force_out   : 1=SDA push-pull（不要用）, 0=open-drain
            [bit 1]  scl_force_out   : 1=SCL push-pull, 0=open-drain
            [bit 2]  sample_scl_level: SCL 哪個電位採樣 SDA，通常 0
            [bit 3]  rx_lsb_first    : 接收 LSB first，通常 0（MSB first）
            [bit 4]  tx_lsb_first    : 發送 LSB first，通常 0
            [bit 5]  ms_mode         : 1=master，0=slave
            [bit 6]  trans_start     : 寫 1 觸發傳輸（自動清零）
            [bit 7]  tx_full_ack     : TX FIFO 滿時的 ACK 行為
            [bit 11] clk_en          : 模組時鐘致能（必須設 1）
```

初始化時設 `ms_mode=1`、`clk_en=1`，其他保持 0（open-drain，MSB first）。

## INT_RAW_REG 中斷旗標

```
I2C_INT_RAW_REG:
  [bit 0]  RXFIFO_FULL_INT    : RX FIFO 達到水位線
  [bit 1]  TXFIFO_EMPTY_INT   : TX FIFO 低於水位線
  [bit 2]  RXFIFO_OVF_INT     : RX FIFO 溢位
  [bit 3]  END_DETECT_INT     : command list 執行到 END 指令
  [bit 4]  SLAVE_TRAN_COMP_INT: slave 傳輸完成
  [bit 5]  ARBITRATION_LOST_INT: 仲裁失敗
  [bit 6]  MASTER_TRAN_COMP_INT: （已廢棄，不用）
  [bit 7]  TRANS_COMPLETE_INT : 整個 transaction 完成（STOP 後）
  [bit 8]  TIME_OUT_INT       : clock stretching timeout
  [bit 9]  TRANS_START_INT    : transmission 開始
  [bit 10] ACK_ERR_INT        : 收到 NACK（關鍵！slave 不在線）
  [bit 11] RXFIFO_OVF_INT     : RX FIFO overflow（重複）
  [bit 12] TXFIFO_OVF_INT     : TX FIFO overflow
```

最重要的兩個：`TRANS_COMPLETE_INT`（傳輸成功結束）和 `ACK_ERR_INT`（收到 NACK）。

## Command Register 機制

這是 ESP32 I2C 最核心的設計。16 個 command register（COMD0~COMD15），每個 32 bit：

```
I2C_COMDn_REG 格式：
  [bit 10:0]  byte_num  : 要傳送 / 接收的 byte 數
  [bit 11]    ack_en    : 發送 ACK/NACK（WRITE 指令：0=不理，READ 指令：1=送 ACK，0=送 NACK）
  [bit 12]    ack_exp   : 預期收到的 ACK 值（0=ACK，1=NACK），不符則觸發 ACK_ERR
  [bit 13]    ack_val   : 送出的 ACK/NACK 值（0=ACK，1=NACK）
  [bit 14]    op_code   : 見下表（只用 3 bit，實際 bit 14:11）
  [bit 31]    done      : 硬體執行完此指令後自動設 1（軟體寫 0 清除）
```

實際 op_code 使用 bit [13:11]，定義如下：

```c
#define I2C_CMD_RSTART  0x0   // 發出 START 或 Repeated START
#define I2C_CMD_WRITE   0x1   // 從 TX FIFO 取 byte_num 個 byte 送出
#define I2C_CMD_READ    0x2   // 接收 byte_num 個 byte 存入 RX FIFO
#define I2C_CMD_STOP    0x3   // 發出 STOP condition
#define I2C_CMD_END     0x4   // command list 結束（等 TX FIFO 有資料再繼續）
```

一筆 `[S][ADDR+W][ACK][DATA][ACK][P]` 的 command sequence：

```
COMD0: op=RSTART                          → 發 START
COMD1: op=WRITE, byte_num=1, ack_en=1     → 從 FIFO 送 1 byte（位址+W bit），等 ACK
COMD2: op=WRITE, byte_num=N, ack_en=1     → 送 N byte 資料，每 byte 等 ACK
COMD3: op=STOP                            → 發 STOP
```

在觸發傳輸之前，必須先把要送的資料寫進 TX FIFO（`I2C_DATA_APB_REG`）。

## FIFO 存取

```
I2C_DATA_APB_REG [bit 7:0] : 讀取此暫存器從 RX FIFO 取一 byte，
                              寫入此暫存器把一 byte 放進 TX FIFO
```

FIFO 深度 32 bytes（TX 和 RX 各 32）。`I2C_FIFO_CONF_REG` 可以設定水位線。

## 完整 I2C Master 初始化程式碼

```c
#include <stdint.h>
#include "esp_system.h"   // 只用於 portENTER/EXIT_CRITICAL，可自行替換

// 暫存器存取 macro
#define REG_WRITE(addr, val)  (*((volatile uint32_t *)(addr)) = (val))
#define REG_READ(addr)        (*((volatile uint32_t *)(addr)))
#define REG_SET_BIT(addr, b)  REG_WRITE((addr), REG_READ(addr) | (b))
#define REG_CLR_BIT(addr, b)  REG_WRITE((addr), REG_READ(addr) & ~(b))

#define I2C0_BASE   0x3FF53000UL
#define I2C1_BASE   0x3FF67000UL

// 各暫存器 offset
#define I2C_SCL_LOW_PERIOD_REG  0x00
#define I2C_CTR_REG             0x04
#define I2C_TO_REG              0x0C
#define I2C_INT_CLR_REG         0x24
#define I2C_INT_ENA_REG         0x28
#define I2C_SDA_HOLD_REG        0x30
#define I2C_SDA_SAMPLE_REG      0x34
#define I2C_SCL_HIGH_PERIOD_REG 0x38
#define I2C_SCL_START_HOLD_REG  0x40
#define I2C_SCL_RSTART_SETUP_REG 0x44
#define I2C_SCL_STOP_HOLD_REG   0x48
#define I2C_SCL_STOP_SETUP_REG  0x4C
#define I2C_SCL_FILTER_CFG_REG  0x50
#define I2C_SDA_FILTER_CFG_REG  0x54
#define I2C_COMD0_REG           0x58

// GPIO IO_MUX 相關（簡化：假設 GPIO21=SDA, GPIO22=SCL for I2C0）
#define GPIO_FUNC_OUT_SEL_CFG_REG(n)  (0x3FF44530UL + (n) * 4)
#define GPIO_FUNC_IN_SEL_CFG_REG(sig) (0x3FF49130UL + (sig) * 4)
#define GPIO_PIN_REG(n)               (0x3FF44088UL + (n) * 4)
#define GPIO_ENABLE_W1TS_REG          0x3FF44020UL

// I2C signal 編號（見 ESP32 TRM Table "GPIO Matrix"）
#define I2C0_SDA_OUT_SIG   27
#define I2C0_SDA_IN_SIG    28
#define I2C0_SCL_OUT_SIG   29
#define I2C0_SCL_IN_SIG    30

// APB clock 80 MHz
#define APB_CLK_HZ  80000000UL

static uint32_t i2c_base_addr(int port) {
    return (port == 0) ? I2C0_BASE : I2C1_BASE;
}

static void i2c_gpio_init(int sda_gpio, int scl_gpio, int port) {
    uint32_t sda_out = (port == 0) ? I2C0_SDA_OUT_SIG : 37;
    uint32_t sda_in  = (port == 0) ? I2C0_SDA_IN_SIG  : 38;
    uint32_t scl_out = (port == 0) ? I2C0_SCL_OUT_SIG : 39;
    uint32_t scl_in  = (port == 0) ? I2C0_SCL_IN_SIG  : 40;

    // 把 GPIO 路由到 I2C 訊號（output）
    REG_WRITE(GPIO_FUNC_OUT_SEL_CFG_REG(sda_gpio), sda_out);
    REG_WRITE(GPIO_FUNC_OUT_SEL_CFG_REG(scl_gpio), scl_out);
    // 把 GPIO 路由到 I2C 訊號（input）
    // GPIO Matrix input: bit[5:0]=GPIO num, bit6=invert
    REG_WRITE(GPIO_FUNC_IN_SEL_CFG_REG(sda_in), (uint32_t)sda_gpio | (1 << 6));
    REG_WRITE(GPIO_FUNC_IN_SEL_CFG_REG(scl_in), (uint32_t)scl_gpio | (1 << 6));
    // 開 GPIO output（open-drain 模式由 I2C controller 控制）
    REG_WRITE(GPIO_ENABLE_W1TS_REG, (1 << sda_gpio) | (1 << scl_gpio));
    // 設 GPIO pin 為 open-drain：GPIO_PIN_REG[2]=pad_driver=1
    REG_SET_BIT(GPIO_PIN_REG(sda_gpio), (1 << 2));
    REG_SET_BIT(GPIO_PIN_REG(scl_gpio), (1 << 2));
}

void i2c_master_init(int port, int sda_gpio, int scl_gpio, uint32_t freq_hz) {
    uint32_t base = i2c_base_addr(port);
    uint32_t half_period = (APB_CLK_HZ / freq_hz / 2) - 1;

    // 1. 致能 I2C peripheral 時鐘
    // DPORT_PERIP_CLK_EN_REG bit 7=I2C0, bit 16=I2C1
    uint32_t clk_bit = (port == 0) ? (1 << 7) : (1 << 16);
    REG_SET_BIT(0x3FF000C0, clk_bit);   // DPORT_PERIP_CLK_EN_REG
    // Reset
    REG_SET_BIT(0x3FF000C4, clk_bit);   // DPORT_PERIP_RST_EN_REG
    REG_CLR_BIT(0x3FF000C4, clk_bit);

    // 2. 設定 GPIO
    i2c_gpio_init(sda_gpio, scl_gpio, port);

    // 3. SCL 時序
    REG_WRITE(base + I2C_SCL_LOW_PERIOD_REG,  half_period);
    REG_WRITE(base + I2C_SCL_HIGH_PERIOD_REG, half_period);

    // 4. SDA hold/sample
    REG_WRITE(base + I2C_SDA_HOLD_REG,   10);
    REG_WRITE(base + I2C_SDA_SAMPLE_REG, 10);

    // 5. START/STOP 時序（使用 half_period 作為保守值）
    REG_WRITE(base + I2C_SCL_START_HOLD_REG,   half_period);
    REG_WRITE(base + I2C_SCL_RSTART_SETUP_REG, half_period);
    REG_WRITE(base + I2C_SCL_STOP_HOLD_REG,    half_period);
    REG_WRITE(base + I2C_SCL_STOP_SETUP_REG,   half_period);

    // 6. Clock stretching timeout（20000 ticks @ 80 MHz ≈ 0.25 ms）
    REG_WRITE(base + I2C_TO_REG, 20000);

    // 7. Glitch filter（可選，建議開啟）
    REG_WRITE(base + I2C_SCL_FILTER_CFG_REG, (1 << 4) | 7);
    REG_WRITE(base + I2C_SDA_FILTER_CFG_REG, (1 << 4) | 7);

    // 8. 清除所有中斷，不使用中斷（polling 模式）
    REG_WRITE(base + I2C_INT_CLR_REG, 0xFFFF);
    REG_WRITE(base + I2C_INT_ENA_REG, 0);

    // 9. 設定為 master，open-drain，MSB first，致能時鐘
    // CTR_REG: ms_mode=bit5=1, clk_en=bit11=1
    REG_WRITE(base + I2C_CTR_REG, (1 << 5) | (1 << 11));
}
```

## 一筆 I2C Write Transaction

```c
#define I2C_INT_RAW_REG  0x20
#define I2C_DATA_APB_REG 0x1C
#define I2C_FIFO_CONF_REG 0x18

#define I2C_TRANS_COMPLETE_INT (1 << 7)
#define I2C_ACK_ERR_INT        (1 << 10)

// command 格式：op_code 在 bit 13:11
#define I2C_CMD(op, byte_num, ack_en, ack_exp, ack_val) \
    (((op) << 11) | ((byte_num) & 0xFF) | \
     ((ack_en) << 8) | ((ack_exp) << 9) | ((ack_val) << 10))

#define CMD_RSTART  0
#define CMD_WRITE   1
#define CMD_READ    2
#define CMD_STOP    3

// 回傳 0=成功, -1=NACK, -2=timeout
int i2c_write(int port, uint8_t slave_addr,
              const uint8_t *data, size_t len) {
    uint32_t base = i2c_base_addr(port);
    size_t i;

    // 1. 清除中斷旗標
    REG_WRITE(base + I2C_INT_CLR_REG, 0xFFFF);

    // 2. 重置 FIFO
    REG_SET_BIT(base + I2C_FIFO_CONF_REG, (1 << 12) | (1 << 13));
    REG_CLR_BIT(base + I2C_FIFO_CONF_REG, (1 << 12) | (1 << 13));

    // 3. 填入 TX FIFO：先放位址 byte（7-bit addr << 1 | 0=Write），再放資料
    REG_WRITE(base + I2C_DATA_APB_REG, (slave_addr << 1) | 0);
    for (i = 0; i < len; i++) {
        REG_WRITE(base + I2C_DATA_APB_REG, data[i]);
    }

    // 4. 設定 command sequence
    volatile uint32_t *comd = (volatile uint32_t *)(base + I2C_COMD0_REG);
    comd[0] = I2C_CMD(CMD_RSTART, 0, 0, 0, 0);          // START
    comd[1] = I2C_CMD(CMD_WRITE, 1, 1, 0, 0);            // 位址 byte（ack_exp=0=ACK）
    comd[2] = I2C_CMD(CMD_WRITE, (int)len, 1, 0, 0);     // 資料
    comd[3] = I2C_CMD(CMD_STOP, 0, 0, 0, 0);             // STOP

    // 5. 觸發傳輸：CTR_REG bit6 = trans_start
    REG_SET_BIT(base + I2C_CTR_REG, (1 << 6));

    // 6. Polling 等完成（最多等 50000 次迴圈，約幾 ms）
    uint32_t timeout = 50000;
    uint32_t status;
    while (timeout--) {
        status = REG_READ(base + I2C_INT_RAW_REG);
        if (status & I2C_TRANS_COMPLETE_INT) {
            REG_WRITE(base + I2C_INT_CLR_REG, 0xFFFF);
            return 0;
        }
        if (status & I2C_ACK_ERR_INT) {
            REG_WRITE(base + I2C_INT_CLR_REG, 0xFFFF);
            return -1;   // NACK：slave 不在線或位址錯誤
        }
    }
    return -2;  // timeout
}
```

## 自我檢核

- [ ] 能說出 ESP32 兩個 I2C controller 的 base address
- [ ] 理解 command link 機制，知道為何要先填 FIFO 再設 command
- [ ] 能計算 100 kHz 和 400 kHz 時 `scl_low_period` / `scl_high_period` 的值
- [ ] 知道 `ACK_ERR_INT` 觸發代表什麼，如何與 `TRANS_COMPLETE_INT` 區分
- [ ] 能解釋 `ack_exp`、`ack_en`、`ack_val` 三個 command field 各自的用途
- [ ] 能追蹤 `i2c_write()` 的每一步，對應到 Ch 6 的時序圖

下一章用這個 driver 讀取 BME280 溫濕壓感測器，把協議知識和真實硬體接起來。

→ [Ch 8 實作：I2C → BME280 溫濕壓感測器](./08-i2c-bme280.md)
