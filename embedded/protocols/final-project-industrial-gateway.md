# Final Project：工業感測器閘道器

把這門課學到的所有協議整合進一個完整的工業感測器閘道器。這個專案可以寫進履歷——它涵蓋了五種嵌入式通訊協議（Modbus RTU、I2C、CAN、LoRa、BLE），全部 register-level，不靠 HAL。

**前置章節**：全部 Ch 0–20

---

## 系統架構

```
[Modbus Slave 溫控器]
        |  RS-485 / Modbus RTU
        |
[ESP32 閘道器] ── I2C ──────> [BME280 本地感測器]
        |
        ├── CAN bus ──────────> [第二顆 ESP32 模擬 CAN 節點]
        |
        ├── LoRa (SX1276) ──> [第三顆 ESP32 遠端 LoRa 節點]
        |
        └── BLE (NimBLE) ───> [手機 App：nRF Connect]
```

閘道器本身跑在「主 ESP32」上。另外需要兩顆 ESP32 分別模擬 Modbus slave 和 CAN/LoRa 接收端——這兩顆的程式比較簡單，本專案會一起給出。

---

## 硬體清單

| 元件 | 數量 | 說明 |
|---|---|---|
| ESP32 DevKitC | 3 | 閘道器 × 1、CAN 接收節點 × 1、LoRa 遠端節點 × 1 |
| BME280 模組 | 1 | I2C，SDA=GPIO21，SCL=GPIO22 |
| MAX485 RS-485 收發模組 | 1 | UART1，DE/RE=GPIO4，TX=GPIO17，RX=GPIO16 |
| SN65HVD230 CAN 收發器 | 2 | 主閘道器和 CAN 節點各一顆，CAN TX=GPIO25，RX=GPIO26 |
| SX1276 LoRa 模組 | 2 | 主閘道器和 LoRa 遠端節點各一顆，接 VSPI |
| 120Ω 終端電阻 | 2 | CAN bus 兩端 |
| 第二顆 ESP32（Modbus slave 模擬器） | — | 選用，可用 PC Python 腳本取代 |

**主閘道器 GPIO 分配：**
```
GPIO4  : MAX485 DE/RE（RS-485 方向控制）
GPIO16 : UART1 RX（RS-485 → ESP32）
GPIO17 : UART1 TX（ESP32 → RS-485）
GPIO18 : SX1276 SCK
GPIO19 : SX1276 MISO
GPIO21 : BME280 SDA
GPIO22 : BME280 SCL
GPIO23 : SX1276 MOSI
GPIO25 : TWAI TX（CAN）
GPIO26 : TWAI RX（CAN）、SX1276 DIO0（共用 pin！需要分開，實際用 GPIO33 給 DIO0）
GPIO33 : SX1276 DIO0
GPIO5  : SX1276 NSS（CS）
GPIO14 : SX1276 RESET
```

---

## 三週開發計畫

### Phase 1（Week 1）：感測器採集層

**目標：** 閘道器能從 Modbus slave 和 BME280 讀到數值，並能建立結構化資料。

**需要實作的模組：**

1. **`modbus.c/h`** — Modbus RTU Master
   - 函式介面：
     ```c
     void modbus_init(int uart_num, int de_pin);
     int  modbus_read_holding_regs(uint8_t slave_id, uint16_t start_reg,
                                   uint16_t count, uint16_t *out);
     ```
   - 細節：RS-485 半雙工，需要在 TX 前拉高 DE/RE，TX 完成後切換回 RX 模式，接著等 slave response（timeout 200ms）
   - CRC 計算：Modbus CRC16，polynomial 0xA001（反向 LSB-first）

2. **`bme280.c/h`** — I2C BME280 驅動（可從 Practice A 移植）
   - 函式介面：
     ```c
     bool bme280_init(void);
     bool bme280_read(float *temp_c, float *humi_pct, float *pres_hpa);
     ```

3. **`sensor_data.h`** — 資料結構定義
   ```c
   typedef enum {
       SENSOR_SRC_MODBUS  = 0,
       SENSOR_SRC_BME280  = 1,
       SENSOR_SRC_CAN_RX  = 2,
       SENSOR_SRC_LORA_RX = 3,
   } sensor_src_t;

   typedef struct {
       uint32_t    timestamp;  /* FreeRTOS tick，ms */
       sensor_src_t source;
       float       temperature;
       float       humidity;
       float       pressure;   /* 僅 BME280 有效 */
       uint8_t     modbus_raw[4]; /* Modbus 原始 register 值 */
   } sensor_data_t;
   ```

4. **Phase 1 驗收：**
   - 每 5 秒在 UART0 印出：
     ```
     [T=5000] BME280: Temp=25.34C Humi=61.2% Pres=1013.2hPa
     [T=5000] Modbus: slave=0x01 reg=0x0000 val=0x00FA (25.0 deg)
     ```

---

### Phase 2（Week 2）：匯流排整合

**目標：** 每次讀到感測器資料後，發 CAN frame；每 30 秒發一次 LoRa 摘要。

**需要實作的模組：**

1. **`twai.c/h`** — CAN TWAI 驅動（從 Practice B 移植）
   - 函式介面：
     ```c
     void twai_drv_init(int tx_gpio, int rx_gpio);
     bool twai_send_sensor(uint16_t can_id, float value);
     bool twai_receive(can_frame_t *f, uint32_t timeout_ms);
     ```
   - CAN ID 分配：
     - `0x100`：BME280 溫度
     - `0x101`：BME280 濕度
     - `0x102`：BME280 氣壓
     - `0x110`：Modbus 溫控器溫度
   - Data 格式：4 bytes，IEEE 754 float，big-endian

2. **`lora.c/h`** — SX1276 LoRa 驅動（從 Practice C 移植）
   - 函式介面：
     ```c
     bool lora_init(void);
     bool lora_send(const uint8_t *payload, uint8_t len);
     uint8_t lora_recv(uint8_t *buf, uint8_t buf_size, int8_t *rssi_dbm);
     ```
   - LoRa 摘要格式（每 30 秒）：
     ```json
     {"ts":12345,"bme_t":25.3,"bme_h":61.2,"bme_p":1013.2,"mb_t":25.0}
     ```

3. **CAN 接收節點程式（第二顆 ESP32）：**
   - 接收所有 CAN frame，印出 ID 和 float 解碼後的數值
   - 期望輸出：
     ```
     CAN RX: ID=0x100 Temp=25.34C (src=BME280)
     CAN RX: ID=0x110 Temp=25.00C (src=Modbus)
     ```

4. **Phase 2 驗收：**
   - 第二顆 ESP32 的序列埠收到所有 CAN frame，接收率 100%
   - 每 30 秒 UART0 印出 `LoRa TX: {json...} OK`

---

### Phase 3（Week 3）：對外介面

**目標：** 透過 BLE 讓手機能即時看到感測器資料，並能查詢歷史。

**需要實作的模組：**

1. **`ble_service.c/h`** — NimBLE GATT Service
   - 函式介面：
     ```c
     void ble_service_init(void);
     void ble_service_update(const sensor_data_t *data);
     ```
   - Service UUID：`BB00`（16-bit）
   - Characteristic 定義：

     | UUID | 屬性 | 說明 |
     |---|---|---|
     | `BB01` | READ + NOTIFY | 即時感測器資料（JSON，每 5 秒更新） |
     | `BB02` | READ | 歷史資料筆數（uint32_t，little-endian） |
     | `BB03` | WRITE + NOTIFY | 查詢指令（Write 0x01 = 請求歷史，Notify 回傳） |

   - 即時資料 JSON：
     ```json
     {"ts":12345,"bme_t":25.3,"bme_h":61.2,"mb_t":25.0,"src":"LIVE"}
     ```
   - 手機 Write `BB03` = `0x01` 後，以多個 Notify 回傳歷史記錄（每次一筆）

2. **UART log 格式化：**
   - 所有感測器事件輸出結構化 log，讓 Python 腳本能 parse：
     ```
     [EVT] ts=12345 src=BME280 temp=25.34 humi=61.2 pres=1013.2
     [EVT] ts=12345 src=Modbus temp=25.00
     [ERR] ts=12345 src=Modbus timeout
     [CAN] ts=12345 id=0x100 val=25.34
     [BLE] ts=12345 connected
     [LORA] ts=12345 tx_ok len=65
     ```

---

## 驗收標準

| 測試項目 | 通過條件 |
|---|---|
| Modbus 讀取 | 每 5 秒成功讀到數值，CRC 無錯誤，連續 10 次 |
| BME280 讀取 | 溫度誤差 < 1°C，每次讀取成功，連續 10 次 |
| CAN 收發 | 第二顆 ESP32 接收率 100%，持續 5 分鐘 |
| LoRa 傳輸 | 30m 空曠環境封包接收率 > 95%，20 個封包 |
| BLE Notify | 手機每 5 秒收到更新，不斷線，持續 5 分鐘 |
| BLE 歷史查詢 | Write `0x01` 後 5 秒內收到所有歷史 Notify |
| UART log | 所有事件有對應的 `[EVT]` / `[ERR]` log，無遺漏 |

---

## 程式碼架構

```
gateway/
├── main/
│   ├── main.c           # 初始化 + FreeRTOS tasks
│   ├── modbus.c
│   ├── modbus.h
│   ├── bme280.c
│   ├── bme280.h
│   ├── twai.c
│   ├── twai.h
│   ├── lora.c
│   ├── lora.h
│   ├── ble_service.c
│   ├── ble_service.h
│   └── sensor_data.h
└── CMakeLists.txt

can_node/                # 第二顆 ESP32（CAN 接收節點）
├── main/
│   └── main.c
└── CMakeLists.txt

lora_node/               # 第三顆 ESP32（LoRa 遠端節點）
├── main/
│   └── main.c
└── CMakeLists.txt
```

---

## 參考解答

<details>
<summary>點開參考實作</summary>

以下是各模組的完整實作。

---

### sensor_data.h

```c
#pragma once
#include <stdint.h>
#include <stdbool.h>

typedef enum {
    SENSOR_SRC_MODBUS  = 0,
    SENSOR_SRC_BME280  = 1,
    SENSOR_SRC_CAN_RX  = 2,
    SENSOR_SRC_LORA_RX = 3,
} sensor_src_t;

typedef struct {
    uint32_t     timestamp;
    sensor_src_t source;
    float        temperature;
    float        humidity;
    float        pressure;
    bool         valid;
} sensor_data_t;

#define SENSOR_HISTORY_SIZE 64
```

---

### modbus.c

```c
/*
 * modbus.c — Modbus RTU Master，UART register-level，RS-485 半雙工
 */
#include "modbus.h"
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

/* UART1 暫存器（base 0x3FF50000） */
#define UART1_BASE          0x3FF50000UL
#define UART_FIFO_REG(b)    (*(volatile uint32_t *)((b) + 0x00))
#define UART_STATUS_REG(b)  (*(volatile uint32_t *)((b) + 0x1C))
#define UART_CONF0_REG(b)   (*(volatile uint32_t *)((b) + 0x20))
#define UART_CONF1_REG(b)   (*(volatile uint32_t *)((b) + 0x24))
#define UART_CLKDIV_REG(b)  (*(volatile uint32_t *)((b) + 0x14))
#define UART_INT_CLR_REG(b) (*(volatile uint32_t *)((b) + 0x10))
#define UART_INT_RAW_REG(b) (*(volatile uint32_t *)((b) + 0x04))
#define UART_RXFIFO_CNT(b)  ((UART_STATUS_REG(b)) & 0xFF)
#define UART_TXFIFO_CNT(b)  (((UART_STATUS_REG(b)) >> 16) & 0xFF)

#define DPORT_PERIP_CLK_EN  (*(volatile uint32_t *)0x3FF0001CUL)
#define DPORT_PERIP_RST_EN  (*(volatile uint32_t *)0x3FF00020UL)
#define DPORT_UART1_CLK_EN  (1UL << 5)

#define GPIO_FUNC_OUT_SEL(n)(*(volatile uint32_t *)(0x3FF44530UL + (n)*4))
#define GPIO_FUNC_IN_SEL(n) (*(volatile uint32_t *)(0x3FF44130UL + (n)*4))
#define GPIO_ENABLE_W1TS    (*(volatile uint32_t *)0x3FF44024UL)
#define GPIO_OUT_W1TS_REG   (*(volatile uint32_t *)0x3FF44008UL)
#define GPIO_OUT_W1TC_REG   (*(volatile uint32_t *)0x3FF4400CUL)

/* UART1 TX=signal 24，RX=signal 24（in） */
#define UART1_TX_IDX  24
#define UART1_RX_IDX  24

static int g_de_pin = -1;

static void de_tx(void) { GPIO_OUT_W1TS_REG = (1UL << g_de_pin); }
static void de_rx(void) { GPIO_OUT_W1TC_REG = (1UL << g_de_pin); }

void modbus_init(int uart_num, int de_pin)
{
    (void)uart_num; /* 固定用 UART1 */
    g_de_pin = de_pin;

    DPORT_PERIP_CLK_EN |=  DPORT_UART1_CLK_EN;
    DPORT_PERIP_RST_EN &= ~DPORT_UART1_CLK_EN;

    /* GPIO routing：TX=GPIO17，RX=GPIO16 */
    GPIO_FUNC_OUT_SEL(17)      = UART1_TX_IDX;
    GPIO_ENABLE_W1TS           = (1UL << 17);
    GPIO_FUNC_IN_SEL(UART1_RX_IDX) = 16;

    /* DE/RE pin：output，預設 RX 模式（low） */
    GPIO_FUNC_OUT_SEL(de_pin)  = 0x100;
    GPIO_ENABLE_W1TS           = (1UL << de_pin);
    de_rx();

    /* 115200 baud，8N1（APB=80MHz）
       clkdiv = 80000000 / 115200 ≈ 694 */
    UART_CLKDIV_REG(UART1_BASE) = 694;
    UART_CONF0_REG(UART1_BASE)  = 0x60000; /* 8N1：bit_num=3(8bit), stop_bit_num=1, parity=none */
}

/* Modbus CRC16（polynomial 0xA001，LSB-first） */
static uint16_t modbus_crc16(const uint8_t *buf, int len)
{
    uint16_t crc = 0xFFFF;
    for (int i = 0; i < len; i++) {
        crc ^= buf[i];
        for (int j = 0; j < 8; j++) {
            if (crc & 0x0001) crc = (crc >> 1) ^ 0xA001;
            else              crc >>= 1;
        }
    }
    return crc;
}

static void uart1_flush_rx(void)
{
    while (UART_RXFIFO_CNT(UART1_BASE)) {
        (void)UART_FIFO_REG(UART1_BASE);
    }
}

static void uart1_send(const uint8_t *buf, int len)
{
    for (int i = 0; i < len; i++) {
        while (UART_TXFIFO_CNT(UART1_BASE) >= 127) {}
        UART_FIFO_REG(UART1_BASE) = buf[i];
    }
    /* 等 TX 空（UART_STATUS bit 14 = tx_done，或等 TXFIFO_CNT == 0） */
    while (UART_TXFIFO_CNT(UART1_BASE) > 0) {}
    /* 再等 1 byte time：約 87us @ 115200 */
    vTaskDelay(1);
}

static int uart1_recv(uint8_t *buf, int max_len, uint32_t timeout_ms)
{
    int n = 0;
    uint32_t t = timeout_ms;
    while (n < max_len && t > 0) {
        if (UART_RXFIFO_CNT(UART1_BASE) > 0) {
            buf[n++] = (uint8_t)UART_FIFO_REG(UART1_BASE);
            t = timeout_ms; /* 有資料就重置 timeout */
        } else {
            vTaskDelay(1);
            t--;
        }
    }
    return n;
}

int modbus_read_holding_regs(uint8_t slave_id, uint16_t start_reg,
                              uint16_t count, uint16_t *out)
{
    /* 組 Modbus RTU 請求：[slave_id][FC=03][addr_hi][addr_lo][cnt_hi][cnt_lo][CRC_lo][CRC_hi] */
    uint8_t req[8];
    req[0] = slave_id;
    req[1] = 0x03; /* FC03: Read Holding Registers */
    req[2] = (start_reg >> 8) & 0xFF;
    req[3] = start_reg & 0xFF;
    req[4] = (count >> 8) & 0xFF;
    req[5] = count & 0xFF;
    uint16_t crc = modbus_crc16(req, 6);
    req[6] = crc & 0xFF;
    req[7] = (crc >> 8) & 0xFF;

    uart1_flush_rx();
    de_tx();
    uart1_send(req, 8);
    de_rx();

    /* 等 response：[slave_id][FC][byte_cnt][data...][CRC_lo][CRC_hi]
       正常長度 = 3 + count*2 + 2 */
    int expected = 3 + count * 2 + 2;
    uint8_t resp[256];
    int n = uart1_recv(resp, expected, 200);

    if (n < expected) return -1; /* timeout */
    if (resp[0] != slave_id) return -2;
    if (resp[1] != 0x03)     return -3; /* exception code */
    if (resp[2] != count * 2) return -4;

    uint16_t resp_crc = modbus_crc16(resp, n - 2);
    uint16_t recv_crc = (uint16_t)(resp[n-1] << 8 | resp[n-2]);
    if (resp_crc != recv_crc) return -5; /* CRC error */

    for (int i = 0; i < (int)count; i++) {
        out[i] = (uint16_t)(resp[3 + i*2] << 8 | resp[3 + i*2 + 1]);
    }
    return 0;
}
```

---

### twai.c

```c
/*
 * twai.c — CAN TWAI driver（從 Practice B 精簡移植）
 */
#include "twai.h"
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define TWAI_BASE           0x3FF6B000UL
#define TWAI_MODE_REG       (*(volatile uint32_t *)(TWAI_BASE + 0x00))
#define TWAI_CMD_REG        (*(volatile uint32_t *)(TWAI_BASE + 0x04))
#define TWAI_STATUS_REG     (*(volatile uint32_t *)(TWAI_BASE + 0x08))
#define TWAI_INTR_REG       (*(volatile uint32_t *)(TWAI_BASE + 0x0C))
#define TWAI_INTR_ENA_REG   (*(volatile uint32_t *)(TWAI_BASE + 0x10))
#define TWAI_BUS_TIMING_0   (*(volatile uint32_t *)(TWAI_BASE + 0x14))
#define TWAI_BUS_TIMING_1   (*(volatile uint32_t *)(TWAI_BASE + 0x18))
#define TWAI_AMR0           (*(volatile uint32_t *)(TWAI_BASE + 0x50))
#define TWAI_AMR1           (*(volatile uint32_t *)(TWAI_BASE + 0x54))
#define TWAI_AMR2           (*(volatile uint32_t *)(TWAI_BASE + 0x58))
#define TWAI_AMR3           (*(volatile uint32_t *)(TWAI_BASE + 0x5C))
#define TWAI_ACR0           (*(volatile uint32_t *)(TWAI_BASE + 0x40))
#define TWAI_ACR1           (*(volatile uint32_t *)(TWAI_BASE + 0x44))
#define TWAI_ACR2           (*(volatile uint32_t *)(TWAI_BASE + 0x48))
#define TWAI_ACR3           (*(volatile uint32_t *)(TWAI_BASE + 0x4C))
#define TWAI_TX_ERR_CNT     (*(volatile uint32_t *)(TWAI_BASE + 0x60))
#define TWAI_BUF(n)         (*(volatile uint32_t *)(TWAI_BASE + 0x80 + (n)*4))
#define TWAI_STS_TBS        (1 << 2)
#define TWAI_STS_RBS        (1 << 0)
#define TWAI_STS_TCS        (1 << 3)
#define TWAI_STS_BS         (1 << 7)
#define TWAI_CMD_TR         (1 << 0)
#define TWAI_CMD_RRB        (1 << 2)

#define DPORT_PERIP_CLK_EN  (*(volatile uint32_t *)0x3FF0001CUL)
#define DPORT_PERIP_RST_EN  (*(volatile uint32_t *)0x3FF00020UL)
#define DPORT_TWAI_CLK_EN   (1UL << 12)
#define GPIO_FUNC_OUT_SEL(n)(*(volatile uint32_t *)(0x3FF44530UL + (n)*4))
#define GPIO_FUNC_IN_SEL(n) (*(volatile uint32_t *)(0x3FF44130UL + (n)*4))
#define GPIO_ENABLE_W1TS    (*(volatile uint32_t *)0x3FF44024UL)
#define TWAI_TX_IDX         74
#define TWAI_RX_IDX         74

void twai_drv_init(int tx_gpio, int rx_gpio)
{
    DPORT_PERIP_CLK_EN |=  DPORT_TWAI_CLK_EN;
    DPORT_PERIP_RST_EN &= ~DPORT_TWAI_CLK_EN;

    GPIO_FUNC_OUT_SEL(tx_gpio) = TWAI_TX_IDX;
    GPIO_ENABLE_W1TS = (1UL << tx_gpio);
    GPIO_FUNC_IN_SEL(TWAI_RX_IDX) = rx_gpio;

    TWAI_MODE_REG = 0x01; /* Reset */
    TWAI_BUS_TIMING_0 = (0x00 << 6) | 0x04; /* 500kbps */
    TWAI_BUS_TIMING_1 = (0 << 7) | (2 << 4) | 12;
    TWAI_ACR0 = TWAI_ACR1 = TWAI_ACR2 = TWAI_ACR3 = 0x00;
    TWAI_AMR0 = TWAI_AMR1 = TWAI_AMR2 = TWAI_AMR3 = 0xFF;
    (void)TWAI_INTR_REG;
    TWAI_INTR_ENA_REG = 0x00;
    TWAI_MODE_REG = 0x00; /* Normal */
}

bool twai_send_sensor(uint16_t can_id, float value)
{
    /* 等 TX buffer 空閒 */
    uint32_t t = 100;
    while (!(TWAI_STATUS_REG & TWAI_STS_TBS)) {
        if (!t--) return false;
        vTaskDelay(1);
    }

    /* float 轉 4-byte big-endian */
    union { float f; uint8_t b[4]; } u;
    u.f = value;
    uint8_t data[4] = { u.b[3], u.b[2], u.b[1], u.b[0] }; /* big-endian */

    TWAI_BUF(0) = (4 << 4) | ((can_id >> 8) & 0x7); /* DLC=4 */
    TWAI_BUF(1) = can_id & 0xFF;
    TWAI_BUF(2) = data[0];
    TWAI_BUF(3) = data[1];
    TWAI_BUF(4) = data[2];
    TWAI_BUF(5) = data[3];
    TWAI_CMD_REG = TWAI_CMD_TR;

    /* 等完成 */
    t = 50;
    while (t--) {
        if (TWAI_STATUS_REG & TWAI_STS_TCS) return true;
        if (TWAI_STATUS_REG & TWAI_STS_BS)  return false;
        vTaskDelay(1);
    }
    return false;
}

bool twai_drv_receive(can_frame_t *f, uint32_t timeout_ms)
{
    uint32_t t = timeout_ms;
    while (!(TWAI_STATUS_REG & TWAI_STS_RBS)) {
        if (!t--) return false;
        vTaskDelay(1);
    }
    uint32_t b0 = TWAI_BUF(0), b1 = TWAI_BUF(1);
    f->id  = ((b0 & 0x7) << 8) | (b1 & 0xFF);
    f->dlc = (b0 >> 4) & 0xF;
    f->rtr = (b0 >> 3) & 0x1;
    for (int i = 0; i < f->dlc && i < 8; i++) f->data[i] = (uint8_t)TWAI_BUF(2 + i);
    TWAI_CMD_REG = TWAI_CMD_RRB;
    return true;
}
```

---

### lora.c

```c
/*
 * lora.c — SX1276 LoRa（從 Practice C 精簡移植）
 */
#include "lora.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

/* 複用 Practice C 的 SPI 基礎設施（spi_init_lora, sx1276_read, sx1276_write 等）
   此處直接引用，不重複貼出 */
extern void     spi_init_lora(void);
extern uint8_t  sx1276_read(uint8_t reg);
extern void     sx1276_write(uint8_t reg, uint8_t val);
extern void     sx1276_write_fifo(const uint8_t *buf, uint8_t len);
extern void     sx1276_read_fifo(uint8_t *buf, uint8_t len);

#define SX1276_REG_OP_MODE      0x01
#define SX1276_REG_FRF_MSB      0x06
#define SX1276_REG_FRF_MID      0x07
#define SX1276_REG_FRF_LSB      0x08
#define SX1276_REG_PA_CONFIG    0x09
#define SX1276_REG_FIFO_TX_BASE 0x0E
#define SX1276_REG_FIFO_RX_BASE 0x0F
#define SX1276_REG_FIFO_RX_CUR  0x10
#define SX1276_REG_IRQ_FLAGS    0x12
#define SX1276_REG_RX_NB_BYTES  0x13
#define SX1276_REG_PKT_RSSI_VAL 0x1A
#define SX1276_REG_MODEM_CFG1   0x1D
#define SX1276_REG_MODEM_CFG2   0x1E
#define SX1276_REG_PREAMBLE_MSB 0x20
#define SX1276_REG_PREAMBLE_LSB 0x21
#define SX1276_REG_PAYLOAD_LEN  0x22
#define SX1276_REG_MODEM_CFG3   0x26
#define SX1276_REG_DIO_MAPPING1 0x40
#define SX1276_REG_VERSION      0x42
#define SX1276_REG_FIFO_ADDR    0x0D

bool lora_init(void)
{
    spi_init_lora();
    if (sx1276_read(SX1276_REG_VERSION) != 0x12) return false;

    sx1276_write(SX1276_REG_OP_MODE, 0x00);
    vTaskDelay(pdMS_TO_TICKS(10));
    sx1276_write(SX1276_REG_OP_MODE, 0x80);
    vTaskDelay(pdMS_TO_TICKS(10));

    sx1276_write(SX1276_REG_FRF_MSB, 0xE4);
    sx1276_write(SX1276_REG_FRF_MID, 0xC0);
    sx1276_write(SX1276_REG_FRF_LSB, 0x00);
    sx1276_write(SX1276_REG_PA_CONFIG,    0x8C);
    sx1276_write(SX1276_REG_MODEM_CFG1,   0x72);
    sx1276_write(SX1276_REG_MODEM_CFG2,   0x74);
    sx1276_write(SX1276_REG_MODEM_CFG3,   0x04);
    sx1276_write(SX1276_REG_PREAMBLE_MSB, 0x00);
    sx1276_write(SX1276_REG_PREAMBLE_LSB, 0x08);
    sx1276_write(SX1276_REG_FIFO_TX_BASE, 0x00);
    sx1276_write(SX1276_REG_FIFO_RX_BASE, 0x00);
    sx1276_write(SX1276_REG_OP_MODE,      0x81); /* Standby */
    return true;
}

bool lora_send(const uint8_t *payload, uint8_t len)
{
    sx1276_write(SX1276_REG_OP_MODE,    0x81);
    sx1276_write(SX1276_REG_FIFO_ADDR,  0x00);
    sx1276_write_fifo(payload, len);
    sx1276_write(SX1276_REG_PAYLOAD_LEN, len);
    sx1276_write(SX1276_REG_DIO_MAPPING1, 0x40);
    sx1276_write(SX1276_REG_IRQ_FLAGS, 0xFF);
    sx1276_write(SX1276_REG_OP_MODE,   0x83); /* TX */

    uint32_t t = 3000;
    while (t--) {
        if (sx1276_read(SX1276_REG_IRQ_FLAGS) & 0x08) {
            sx1276_write(SX1276_REG_IRQ_FLAGS, 0x08);
            sx1276_write(SX1276_REG_OP_MODE, 0x81);
            return true;
        }
        vTaskDelay(1);
    }
    return false;
}

void lora_start_rx(void)
{
    sx1276_write(SX1276_REG_OP_MODE,    0x81);
    sx1276_write(SX1276_REG_FIFO_ADDR,  0x00);
    sx1276_write(SX1276_REG_DIO_MAPPING1, 0x00);
    sx1276_write(SX1276_REG_IRQ_FLAGS, 0xFF);
    sx1276_write(SX1276_REG_OP_MODE,   0x85); /* Continuous RX */
}

uint8_t lora_recv(uint8_t *buf, uint8_t buf_size, int8_t *rssi_dbm)
{
    uint8_t irq = sx1276_read(SX1276_REG_IRQ_FLAGS);
    if (!(irq & 0x40)) return 0;
    if (irq & 0x20)  { sx1276_write(SX1276_REG_IRQ_FLAGS, 0xFF); return 0; }

    uint8_t cur = sx1276_read(SX1276_REG_FIFO_RX_CUR);
    uint8_t nb  = sx1276_read(SX1276_REG_RX_NB_BYTES);
    if (nb > buf_size) nb = buf_size;
    sx1276_write(SX1276_REG_FIFO_ADDR, cur);
    sx1276_read_fifo(buf, nb);
    *rssi_dbm = (int8_t)(-157 + sx1276_read(SX1276_REG_PKT_RSSI_VAL));
    sx1276_write(SX1276_REG_IRQ_FLAGS, 0xFF);
    return nb;
}
```

---

### ble_service.c

```c
/*
 * ble_service.c — NimBLE GATT Service
 */
#include "ble_service.h"
#include "sensor_data.h"
#include <string.h>
#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "host/ble_hs.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"

#define BLE_SVC_UUID    0xBB00
#define BLE_CHR_LIVE    0xBB01
#define BLE_CHR_COUNT   0xBB02
#define BLE_CHR_QUERY   0xBB03

static uint16_t g_chr_live_handle;
static uint16_t g_chr_count_handle;
static uint16_t g_chr_query_handle;
static uint16_t g_conn_handle = BLE_HS_CONN_HANDLE_NONE;

/* 歷史資料 ringbuffer */
static sensor_data_t g_history[SENSOR_HISTORY_SIZE];
static volatile int  g_history_head = 0;
static volatile int  g_history_count = 0;
static SemaphoreHandle_t g_hist_mutex;

/* 即時 JSON payload */
static char     g_live_json[128];
static SemaphoreHandle_t g_json_mutex;

/* 把 float 轉成小數點一位字串，寫到 dst，回傳寫了多少 byte */
static int ftoa1(float v, char *dst)
{
    int32_t iv = (int32_t)(v * 10.0f + (v >= 0 ? 0.5f : -0.5f));
    int n = 0;
    if (iv < 0) { dst[n++] = '-'; iv = -iv; }
    /* 整數部分 */
    char buf[12]; int blen = 0;
    int32_t whole = iv / 10;
    if (!whole) buf[blen++] = '0';
    else { int32_t w2 = whole; while (w2) { buf[blen++] = '0' + w2 % 10; w2 /= 10; } }
    for (int i = blen-1; i >= 0; i--) dst[n++] = buf[i];
    dst[n++] = '.';
    dst[n++] = '0' + (iv % 10);
    return n;
}

static void build_live_json(const sensor_data_t *d)
{
    char buf[128];
    int n = 0;
    const char *src_str = (d->source == SENSOR_SRC_BME280) ? "BME280" : "Modbus";
    /* {"ts":XXXXX,"bme_t":XX.X,"bme_h":XX.X,"mb_t":XX.X,"src":"XXXX"} */
    const char p1[] = "{\"ts\":";
    for (int i = 0; p1[i]; i++) buf[n++] = p1[i];
    /* timestamp */
    uint32_t ts = d->timestamp;
    char tmp[12]; int tl = 0;
    if (!ts) tmp[tl++] = '0';
    else { uint32_t v = ts; while (v) { tmp[tl++] = '0' + v % 10; v /= 10; } }
    for (int i = tl-1; i >= 0; i--) buf[n++] = tmp[i];
    const char p2[] = ",\"temp\":";
    for (int i = 0; p2[i]; i++) buf[n++] = p2[i];
    n += ftoa1(d->temperature, buf + n);
    const char p3[] = ",\"humi\":";
    for (int i = 0; p3[i]; i++) buf[n++] = p3[i];
    n += ftoa1(d->humidity, buf + n);
    const char p4[] = ",\"src\":\"";
    for (int i = 0; p4[i]; i++) buf[n++] = p4[i];
    for (int i = 0; src_str[i]; i++) buf[n++] = src_str[i];
    buf[n++] = '"'; buf[n++] = '}'; buf[n] = '\0';

    xSemaphoreTake(g_json_mutex, portMAX_DELAY);
    strncpy(g_live_json, buf, sizeof(g_live_json) - 1);
    xSemaphoreGive(g_json_mutex);
}

static int chr_access_cb(uint16_t conn_handle, uint16_t attr_handle,
                          struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    uint16_t uuid = ble_uuid_u16(ctxt->chr->uuid);

    if (ctxt->op == BLE_GATT_ACCESS_OP_READ_CHR) {
        if (uuid == BLE_CHR_LIVE) {
            xSemaphoreTake(g_json_mutex, portMAX_DELAY);
            int rc = os_mbuf_append(ctxt->om, g_live_json, strlen(g_live_json));
            xSemaphoreGive(g_json_mutex);
            return rc == 0 ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
        }
        if (uuid == BLE_CHR_COUNT) {
            uint32_t cnt = (uint32_t)g_history_count;
            return os_mbuf_append(ctxt->om, &cnt, 4) == 0 ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
        }
    }

    if (ctxt->op == BLE_GATT_ACCESS_OP_WRITE_CHR && uuid == BLE_CHR_QUERY) {
        uint8_t cmd = 0;
        if (os_mbuf_copydata(ctxt->om, 0, 1, &cmd) == 0 && cmd == 0x01) {
            /* 把歷史資料逐筆 Notify 給 client */
            if (g_conn_handle != BLE_HS_CONN_HANDLE_NONE) {
                xSemaphoreTake(g_hist_mutex, portMAX_DELAY);
                int cnt = g_history_count;
                int head = g_history_head;
                xSemaphoreGive(g_hist_mutex);

                for (int i = 0; i < cnt; i++) {
                    int idx = (head - cnt + i + SENSOR_HISTORY_SIZE) % SENSOR_HISTORY_SIZE;
                    char entry[64];
                    int n = 0;
                    entry[n++] = '{';
                    const char *label = "\"t\":";
                    for (int j = 0; label[j]; j++) entry[n++] = label[j];
                    n += ftoa1(g_history[idx].temperature, entry + n);
                    entry[n++] = '}'; entry[n] = '\0';
                    struct os_mbuf *om = ble_hs_mbuf_from_flat(entry, n);
                    if (om) ble_gatts_notify_custom(g_conn_handle, g_chr_query_handle, om);
                    vTaskDelay(pdMS_TO_TICKS(20)); /* 避免 BLE 擁塞 */
                }
            }
        }
        return 0;
    }
    return BLE_ATT_ERR_UNLIKELY;
}

static const struct ble_gatt_svc_def g_gatt_svcs[] = {
    {
        .type = BLE_GATT_SVC_TYPE_PRIMARY,
        .uuid = BLE_UUID16_DECLARE(BLE_SVC_UUID),
        .characteristics = (struct ble_gatt_chr_def[]) {
            { .uuid=BLE_UUID16_DECLARE(BLE_CHR_LIVE),
              .flags=BLE_GATT_CHR_F_READ|BLE_GATT_CHR_F_NOTIFY,
              .access_cb=chr_access_cb, .val_handle=&g_chr_live_handle },
            { .uuid=BLE_UUID16_DECLARE(BLE_CHR_COUNT),
              .flags=BLE_GATT_CHR_F_READ,
              .access_cb=chr_access_cb, .val_handle=&g_chr_count_handle },
            { .uuid=BLE_UUID16_DECLARE(BLE_CHR_QUERY),
              .flags=BLE_GATT_CHR_F_WRITE|BLE_GATT_CHR_F_NOTIFY,
              .access_cb=chr_access_cb, .val_handle=&g_chr_query_handle },
            { 0 }
        },
    },
    { 0 }
};

static int gap_event(struct ble_gap_event *ev, void *arg)
{
    if (ev->type == BLE_GAP_EVENT_CONNECT) {
        g_conn_handle = (ev->connect.status == 0) ?
                         ev->connect.conn_handle : BLE_HS_CONN_HANDLE_NONE;
        if (ev->connect.status != 0) ble_service_advertise();
    } else if (ev->type == BLE_GAP_EVENT_DISCONNECT) {
        g_conn_handle = BLE_HS_CONN_HANDLE_NONE;
        ble_service_advertise();
    }
    return 0;
}

void ble_service_advertise(void)
{
    struct ble_gap_adv_params p = {
        .conn_mode = BLE_GAP_CONN_MODE_UND,
        .disc_mode = BLE_GAP_DISC_MODE_GEN,
    };
    struct ble_hs_adv_fields f = {0};
    const char *name = "SensorGW";
    f.flags = BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP;
    f.name = (uint8_t *)name; f.name_len = strlen(name); f.name_is_complete = 1;
    ble_gap_adv_set_fields(&f);
    ble_gap_adv_start(BLE_OWN_ADDR_PUBLIC, NULL, BLE_HS_FOREVER, &p, gap_event, NULL);
}

static void ble_on_sync(void)
{
    ble_hs_id_infer_auto(0, NULL);
    ble_service_advertise();
}

static void ble_host_task(void *arg)
{
    nimble_port_run();
    nimble_port_freertos_deinit();
}

void ble_service_init(void)
{
    g_hist_mutex = xSemaphoreCreateMutex();
    g_json_mutex = xSemaphoreCreateMutex();
    strncpy(g_live_json, "{\"status\":\"init\"}", sizeof(g_live_json) - 1);

    nimble_port_init();
    ble_svc_gap_init();
    ble_svc_gatt_init();
    ble_gatts_count_cfg(g_gatt_svcs);
    ble_gatts_add_svcs(g_gatt_svcs);
    ble_hs_cfg.sync_cb = ble_on_sync;
    nimble_port_freertos_init(ble_host_task);
}

void ble_service_update(const sensor_data_t *data)
{
    /* 更新即時 JSON */
    build_live_json(data);

    /* 加入歷史 */
    xSemaphoreTake(g_hist_mutex, portMAX_DELAY);
    g_history[g_history_head % SENSOR_HISTORY_SIZE] = *data;
    g_history_head = (g_history_head + 1) % SENSOR_HISTORY_SIZE;
    if (g_history_count < SENSOR_HISTORY_SIZE) g_history_count++;
    xSemaphoreGive(g_hist_mutex);

    /* Notify 即時資料 */
    if (g_conn_handle != BLE_HS_CONN_HANDLE_NONE) {
        xSemaphoreTake(g_json_mutex, portMAX_DELAY);
        size_t len = strlen(g_live_json);
        struct os_mbuf *om = ble_hs_mbuf_from_flat(g_live_json, len);
        xSemaphoreGive(g_json_mutex);
        if (om) ble_gatts_notify_custom(g_conn_handle, g_chr_live_handle, om);
    }
}
```

---

### main.c

```c
/*
 * main.c — 工業感測器閘道器主程式
 */
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sensor_data.h"
#include "modbus.h"
#include "bme280.h"
#include "twai.h"
#include "lora.h"
#include "ble_service.h"

/* UART0 log（register-level） */
#define UART0_FIFO   (*(volatile uint32_t *)0x3FF40000UL)
#define UART0_STATUS (*(volatile uint32_t *)0x3FF4001CUL)

static void u_putc(char c) { while(((UART0_STATUS>>16)&0xFF)>=127){} UART0_FIFO=(uint8_t)c; }
static void u_puts(const char *s) { while(*s) u_putc(*s++); }
static void u_puti(int32_t v)
{
    if (v < 0) { u_putc('-'); v = -v; }
    char b[12]; int l = 0;
    if (!v) { u_putc('0'); return; }
    while (v) { b[l++] = '0' + v%10; v /= 10; }
    for (int i = l-1; i >= 0; i--) u_putc(b[i]);
}

/* LoRa 摘要 JSON builder（無 sprintf 依賴） */
static int build_lora_summary(char *dst, size_t dst_size,
                               const sensor_data_t *bme,
                               const sensor_data_t *mb,
                               uint32_t ts)
{
    /* {"ts":XXXXX,"bme_t":XX.X,"bme_h":XX.X,"bme_p":XXXX.X,"mb_t":XX.X} */
    char tmp[8];
    int n = 0;
    #define APPEND(s) do { const char *_s=(s); while(*_s && n<(int)dst_size-2) dst[n++]=*_s++; } while(0)
    APPEND("{\"ts\":");
    /* ts */
    { uint32_t v=ts; char b[12]; int l=0;
      if(!v){b[l++]='0';} else while(v){b[l++]='0'+v%10;v/=10;}
      for(int i=l-1;i>=0;i--) if(n<(int)dst_size-2) dst[n++]=b[i]; }
    APPEND(",\"bme_t\":");
    { int32_t v=(int32_t)(bme->temperature*10+0.5f);
      char b[8]; int l=0;
      if(v<0){if(n<(int)dst_size-2)dst[n++]='-';v=-v;}
      int32_t w=v/10,frac=v%10;
      if(!w){b[l++]='0';}else{int32_t x=w;while(x){b[l++]='0'+x%10;x/=10;}}
      for(int i=l-1;i>=0;i--)if(n<(int)dst_size-2)dst[n++]=b[i];
      if(n<(int)dst_size-2)dst[n++]='.';
      if(n<(int)dst_size-2)dst[n++]='0'+frac; }
    APPEND(",\"bme_h\":");
    { int32_t v=(int32_t)(bme->humidity*10+0.5f);
      char b[8]; int l=0; int32_t w=v/10,fr=v%10;
      if(!w){b[l++]='0';}else{int32_t x=w;while(x){b[l++]='0'+x%10;x/=10;}}
      for(int i=l-1;i>=0;i--)if(n<(int)dst_size-2)dst[n++]=b[i];
      if(n<(int)dst_size-2)dst[n++]='.';
      if(n<(int)dst_size-2)dst[n++]='0'+fr; }
    APPEND(",\"mb_t\":");
    { int32_t v=(int32_t)(mb->temperature*10+0.5f);
      char b[8]; int l=0;
      if(v<0){if(n<(int)dst_size-2)dst[n++]='-';v=-v;}
      int32_t w=v/10,fr=v%10;
      if(!w){b[l++]='0';}else{int32_t x=w;while(x){b[l++]='0'+x%10;x/=10;}}
      for(int i=l-1;i>=0;i--)if(n<(int)dst_size-2)dst[n++]=b[i];
      if(n<(int)dst_size-2)dst[n++]='.';
      if(n<(int)dst_size-2)dst[n++]='0'+fr; }
    if(n<(int)dst_size-1)dst[n++]='}';
    dst[n]='\0';
    return n;
    #undef APPEND
}

/* ================================================================
 * 主感測器 task
 * ================================================================ */

static volatile sensor_data_t g_bme_data  = {0};
static volatile sensor_data_t g_mb_data   = {0};
static volatile uint32_t      g_lora_timer = 0;

static void sensor_task(void *arg)
{
    (void)arg;
    for (;;) {
        uint32_t ts = xTaskGetTickCount();

        /* --- BME280 --- */
        sensor_data_t bme = { .timestamp=ts, .source=SENSOR_SRC_BME280 };
        if (bme280_read(&bme.temperature, &bme.humidity, &bme.pressure)) {
            bme.valid = true;
            g_bme_data = bme;
            u_puts("[EVT] ts="); u_puti(ts);
            u_puts(" src=BME280 temp="); u_puti((int32_t)(bme.temperature*10));
            u_puts(" humi="); u_puti((int32_t)(bme.humidity*10));
            u_puts("\r\n");
            /* CAN TX：BME280 溫度、濕度、氣壓 */
            twai_send_sensor(0x100, bme.temperature);
            twai_send_sensor(0x101, bme.humidity);
            twai_send_sensor(0x102, bme.pressure);
            /* BLE update */
            ble_service_update(&bme);
        } else {
            u_puts("[ERR] ts="); u_puti(ts); u_puts(" src=BME280 read_fail\r\n");
        }

        /* --- Modbus --- */
        uint16_t regs[1] = {0};
        int rc = modbus_read_holding_regs(0x01, 0x0000, 1, regs);
        if (rc == 0) {
            sensor_data_t mb = { .timestamp=ts, .source=SENSOR_SRC_MODBUS,
                                  .temperature = regs[0] / 10.0f,
                                  .valid=true };
            g_mb_data = mb;
            u_puts("[EVT] ts="); u_puti(ts);
            u_puts(" src=Modbus temp="); u_puti((int32_t)(mb.temperature*10));
            u_puts("\r\n");
            twai_send_sensor(0x110, mb.temperature);
            ble_service_update(&mb);
        } else {
            u_puts("[ERR] ts="); u_puti(ts);
            u_puts(" src=Modbus rc="); u_puti(rc); u_puts("\r\n");
        }

        /* --- LoRa TX（每 30 秒） --- */
        g_lora_timer++;
        if (g_lora_timer >= 6) { /* 6 × 5s = 30s */
            g_lora_timer = 0;
            char summary[128];
            sensor_data_t bme_snap = g_bme_data;
            sensor_data_t mb_snap  = g_mb_data;
            int slen = build_lora_summary(summary, sizeof(summary), &bme_snap, &mb_snap, ts);
            bool ok = lora_send((uint8_t *)summary, (uint8_t)slen);
            u_puts("[LORA] ts="); u_puti(ts);
            u_puts(ok ? " tx_ok len=" : " tx_fail len=");
            u_puti(slen); u_puts("\r\n");
        }

        vTaskDelay(pdMS_TO_TICKS(5000));
    }
}

/* ================================================================
 * CAN 接收節點（第二顆 ESP32）
 * ================================================================ */
/*
 * can_node/main/main.c：
 *
 * #include "twai.h"
 * void app_main(void) {
 *     twai_drv_init(25, 26);
 *     can_frame_t f;
 *     for (;;) {
 *         if (twai_drv_receive(&f, 1000)) {
 *             // 把 data[0..3] 解成 float（big-endian）
 *             union { uint8_t b[4]; float f; } u;
 *             u.b[3]=f.data[0]; u.b[2]=f.data[1];
 *             u.b[1]=f.data[2]; u.b[0]=f.data[3];
 *             // 印出
 *         }
 *     }
 * }
 */

/* ================================================================
 * app_main
 * ================================================================ */

void app_main(void)
{
    u_puts("=== Industrial Sensor Gateway ===\r\n");

    /* 初始化各模組 */
    modbus_init(1, 4);           /* UART1, DE_PIN=GPIO4 */
    if (!bme280_init()) {
        u_puts("[WARN] BME280 init failed\r\n");
    }
    twai_drv_init(25, 26);       /* CAN TX=GPIO25, RX=GPIO26 */
    if (!lora_init()) {
        u_puts("[WARN] LoRa init failed\r\n");
    }
    lora_start_rx();             /* 閘道器預設進入 RX（也可視需求切 TX） */
    ble_service_init();

    u_puts("[SYS] All peripherals initialized\r\n");

    /* 啟動主 task */
    xTaskCreate(sensor_task, "sensor", 8192, NULL, 5, NULL);
}
```

**注意：bme280.c 直接移植 Practice A 的版本，I2C 暫存器操作相同，僅增加 pressure 輸出：**

```c
/* bme280_read 額外回傳 pressure（float hPa） */
static uint32_t bme280_compensate_pres(int32_t adc_P)
{
    /* datasheet 4.2.3 pressure compensation formula（integer 64-bit 版） */
    int64_t var1, var2, p;
    var1 = ((int64_t)g_t_fine) - 128000;
    var2 = var1 * var1 * (int64_t)g_bme280_calib.dig_P6;
    var2 = var2 + ((var1 * (int64_t)g_bme280_calib.dig_P5) << 17);
    var2 = var2 + (((int64_t)g_bme280_calib.dig_P4) << 35);
    var1 = ((var1 * var1 * (int64_t)g_bme280_calib.dig_P3) >> 8) +
           ((var1 * (int64_t)g_bme280_calib.dig_P2) << 12);
    var1 = (((int64_t)1 << 47) + var1) * ((int64_t)g_bme280_calib.dig_P1) >> 33;
    if (var1 == 0) return 0;
    p = 1048576 - adc_P;
    p = (((p << 31) - var2) * 3125) / var1;
    var1 = (((int64_t)g_bme280_calib.dig_P9) * (p >> 13) * (p >> 13)) >> 25;
    var2 = (((int64_t)g_bme280_calib.dig_P8) * p) >> 19;
    p = ((p + var1 + var2) >> 8) + (((int64_t)g_bme280_calib.dig_P7) << 4);
    return (uint32_t)p; /* 單位：Pa × 256 */
}
```

</details>

---

## 驗收 Checklist

依序完成以下項目，打勾確認：

**Phase 1**
- [ ] UART0 log 每 5 秒出現 BME280 資料行
- [ ] UART0 log 每 5 秒出現 Modbus 資料行（或 timeout 錯誤行）
- [ ] 拔掉 Modbus slave，log 出現 `[ERR] src=Modbus timeout`，系統不崩潰

**Phase 2**
- [ ] 第二顆 ESP32 序列埠收到 ID=0x100/0x101/0x102/0x110 四種 CAN frame
- [ ] CAN frame 的 float 解碼值和主閘道器 UART log 一致
- [ ] UART0 每 30 秒出現 `[LORA] tx_ok` 行

**Phase 3**
- [ ] 手機 nRF Connect 找到廣播名稱 `SensorGW`
- [ ] 連線後 Characteristic `BB01` 每 5 秒 Notify 一次 JSON
- [ ] Read Characteristic `BB02` 回傳正確的歷史筆數（uint32_t）
- [ ] Write `0x01` 到 Characteristic `BB03`，5 秒內收到所有歷史 Notify
- [ ] 手機斷線後閘道器重新廣播，手機再次連線成功

---

## 延伸挑戰

完成基本驗收後，有以下方向可以繼續深挖：

1. **Modbus slave 模擬**：用第四顆 ESP32（或 PC Python + pyserial）跑一個 Modbus slave，回應 FC03 請求，讓主閘道器讀到真實的數字而不是 timeout。

2. **LoRa 雙向**：讓主閘道器在收到手機 Write `BB03` 後，也透過 LoRa 把指令轉發給遠端節點，實現 LoRa 下行控制。

3. **CAN filter**：改 TWAI acceptance filter，讓主閘道器只接收 ID=0x110–0x11F 的 frame，過濾掉自己發的 0x100–0x102。

4. **Flash 儲存**：把 sensor history 改寫到 SPI Flash（W25Q32），不靠 PSRAM，在斷電後保留歷史資料。

5. **UART log Python parser**：寫一個 Python 腳本接收 UART log，即時畫出溫度/濕度折線圖（matplotlib），做成簡易的本機 HMI。
