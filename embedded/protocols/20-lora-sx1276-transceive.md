# Ch 20 — 實作：SPI → SX1276 Register-Level LoRa 收發

> 目標：接在 Ch 5 SX1276 SPI 驅動基礎上，完成完整的 LoRa TX/RX 流程：設定暫存器、FIFO 操作、DIO0 中斷處理，最後跑兩節點 PING/PONG 收發測試。

---

## 前置條件

這一章假設你已經有可用的 SX1276 SPI 讀寫函式：

```c
uint8_t sx1276_read_reg(uint8_t addr);
void    sx1276_write_reg(uint8_t addr, uint8_t val);
void    sx1276_write_fifo(const uint8_t *buf, uint8_t len);
void    sx1276_read_fifo(uint8_t *buf, uint8_t len);
```

SPI 時序：CPOL=0, CPHA=0，CS 低電位有效，寫入時 addr[7]=1，讀取時 addr[7]=0。

---

## SX1276 LoRa 重要暫存器

以下只列 LoRa 收發必要的暫存器，完整清單查 SX1276 datasheet Section 6.4：

| 暫存器名稱 | 位址 | 說明 |
|-----------|------|------|
| RegOpMode | 0x01 | 操作模式：SLEEP/STDBY/FSTX/TX/FSRX/RXCONT/RXSINGLE/CAD |
| RegFrMsb | 0x06 | RF 頻率[23:16] |
| RegFrMid | 0x07 | RF 頻率[15:8] |
| RegFrLsb | 0x08 | RF 頻率[7:0] |
| RegPaConfig | 0x09 | 功率放大器設定 |
| RegFifoAddrPtr | 0x0D | FIFO SPI 存取指標 |
| RegFifoTxBaseAddr | 0x0E | TX FIFO 基底位址（通常設 0x00） |
| RegFifoRxBaseAddr | 0x0F | RX FIFO 基底位址（通常設 0x00） |
| RegFifoRxCurrentAddr | 0x10 | 最後收到封包的 FIFO 起始位址 |
| RegIrqFlags | 0x12 | 中斷旗標（TxDone=bit3, RxDone=bit6, CrcError=bit5） |
| RegRxNbBytes | 0x13 | 最後收到封包的 byte 數 |
| RegModemConfig1 | 0x1D | BW[7:4]、CodingRate[3:1]、ImplicitHeaderModeOn[0] |
| RegModemConfig2 | 0x1E | SpreadingFactor[7:4]、TxContinuousMode[3]、RxPayloadCrcOn[2] |
| RegPreambleMsb | 0x20 | Preamble 長度高位元組 |
| RegPreambleLsb | 0x21 | Preamble 長度低位元組（預設 0x08，8 個 preamble symbols） |
| RegPayloadLength | 0x22 | TX payload 長度；Implicit Header 模式下的 RX 預期長度 |
| RegModemConfig3 | 0x26 | LowDataRateOptimize[3]（SF11/SF12 + BW=125kHz 必須開） |
| RegPktSnrValue | 0x19 | 最後收到封包的 SNR（單位 0.25 dB，有號數） |
| RegPktRssiValue | 0x1A | 最後收到封包的 RSSI |
| RegVersion | 0x42 | 晶片版本，SX1276 = 0x12 |

### RegOpMode 模式位元定義

```
bit[7]:   LongRangeMode  — 1=LoRa mode（必須在 SLEEP 模式下切換）
bit[6]:   AccessSharedReg
bit[3]:   LowFrequencyModeOn — 0=高頻模式（>600MHz 用這個）
bit[2:0]: Mode
  000 = SLEEP
  001 = STDBY（待機）
  010 = FSTX（頻率合成TX）
  011 = TX（發送，完成後自動回 STDBY）
  100 = FSRX
  101 = RXCONTINUOUS（持續接收）
  110 = RXSINGLE（接收單個封包後回 STDBY）
  111 = CAD（Channel Activity Detection）
```

### 頻率計算

RF 頻率透過三個 8-bit 暫存器設定，計算公式：

```c
// Fxosc = 32 MHz（SX1276 晶振頻率）
// RegFr = Frequency / Fxosc * 2^19

#define FXOSC     32000000UL
#define FREQ_STEP (FXOSC / (1 << 19))  // = 61.035 Hz/step

uint32_t freq_to_reg(uint32_t freq_hz) {
    return (uint32_t)((uint64_t)freq_hz * (1 << 19) / FXOSC);
}

// AS923 第一個通道 923.2 MHz：
// RegFr = 923200000 * 524288 / 32000000 = 15,138,816 = 0xE72000
```

### RegModemConfig1 / Config2 欄位

```
RegModemConfig1 (0x1D):
  bit[7:4] = BW：0000=7.8kHz, ..., 0111=125kHz, 1000=250kHz, 1001=500kHz
  bit[3:1] = CodingRate：001=4/5, 010=4/6, 011=4/7, 100=4/8
  bit[0]   = ImplicitHeaderModeOn：0=Explicit（有 header），1=Implicit

RegModemConfig2 (0x1E):
  bit[7:4] = SpreadingFactor：0111=SF7, 1100=SF12
  bit[3]   = TxContinuousMode：0=正常，1=連續 TX（測試用）
  bit[2]   = RxPayloadCrcOn：1=開啟 CRC 驗證（強烈建議開）
  bit[1:0] = SymbTimeout[9:8]（搭配 RegSymbTimeoutLsb 設 RxSingle 超時）
```

---

## 初始化序列

```c
#include <stdint.h>
#include "sx1276_spi.h"  // 提供 sx1276_read_reg / sx1276_write_reg

/* LoRa 操作模式常數 */
#define MODE_SLEEP      0x00
#define MODE_STDBY      0x01
#define MODE_TX         0x03
#define MODE_RXCONT     0x05
#define LORA_FLAG       0x80   /* bit7 = LongRangeMode */

/* IRQ 旗標 bit mask */
#define IRQ_TXDONE      (1 << 3)
#define IRQ_RXDONE      (1 << 6)
#define IRQ_CRCERR      (1 << 5)
#define IRQ_RXTO        (1 << 7)

/* 確認 SX1276 存在 */
int sx1276_check_version(void) {
    uint8_t ver = sx1276_read_reg(0x42);  /* RegVersion */
    return (ver == 0x12) ? 0 : -1;
}

/* 設定 RF 頻率：以 Hz 為單位 */
static void set_frequency(uint32_t freq_hz) {
    uint32_t frf = (uint32_t)(((uint64_t)freq_hz << 19) / 32000000UL);
    sx1276_write_reg(0x06, (frf >> 16) & 0xFF);  /* RegFrMsb */
    sx1276_write_reg(0x07, (frf >>  8) & 0xFF);  /* RegFrMid */
    sx1276_write_reg(0x08,  frf        & 0xFF);  /* RegFrLsb */
}

int sx1276_lora_init(void) {
    /* 1. 驗證晶片 */
    if (sx1276_check_version() != 0) return -1;

    /* 2. 進入 SLEEP 模式後切換到 LoRa mode */
    sx1276_write_reg(0x01, MODE_SLEEP);
    /* 等待穩定，至少 1ms */
    vTaskDelay(pdMS_TO_TICKS(5));
    sx1276_write_reg(0x01, LORA_FLAG | MODE_SLEEP);

    /* 3. 設定 FIFO 基底位址 */
    sx1276_write_reg(0x0E, 0x00);   /* RegFifoTxBaseAddr = 0 */
    sx1276_write_reg(0x0F, 0x00);   /* RegFifoRxBaseAddr = 0 */

    /* 4. 設定頻率：AS923 923.2 MHz */
    set_frequency(923200000UL);

    /* 5. 功率：PA_BOOST，+17 dBm
     *    RegPaConfig[7]=1 使用 PA_BOOST 腳
     *    MaxPower=7, OutputPower=15 -> 17 dBm
     */
    sx1276_write_reg(0x09, 0x8F);

    /* 6. Modem 設定：SF9, BW=125kHz, CR=4/5, Explicit Header, CRC on
     *    RegModemConfig1: BW=0111(125kHz), CR=001(4/5), IH=0
     *    0111_001_0 = 0x72
     *    RegModemConfig2: SF=9(1001), TxCont=0, CRC=1
     *    1001_0_1_00 = 0x94
     */
    sx1276_write_reg(0x1D, 0x72);
    sx1276_write_reg(0x1E, 0x94);

    /* 7. Preamble 長度：8 symbols（預設即可）*/
    sx1276_write_reg(0x20, 0x00);
    sx1276_write_reg(0x21, 0x08);

    /* 8. LowDataRateOptimize：SF9+BW125 不需要開（SF11/12 才開）*/
    sx1276_write_reg(0x26, 0x00);

    /* 9. 回到 STDBY */
    sx1276_write_reg(0x01, LORA_FLAG | MODE_STDBY);

    return 0;
}
```

---

## TX 發送流程

```
TX 流程：

STDBY
  |
  v
設 FifoAddrPtr = FifoTxBaseAddr（0x0D = 0x0E 的值）
  |
  v
SPI burst 寫入 payload 進 FIFO
  |
  v
設 RegPayloadLength = payload 長度
  |
  v
切換到 TX 模式（RegOpMode = LORA | MODE_TX）
  |
  v
等待 DIO0 中斷（TxDone bit）或 polling RegIrqFlags
  |
  v
清除 IRQ flags（RegIrqFlags 寫入欲清的 bit）
  |
  v
回到 STDBY
```

```c
int sx1276_send(const uint8_t *payload, uint8_t len) {
    if (len == 0 || len > 255) return -1;

    /* 確保在 STDBY */
    sx1276_write_reg(0x01, LORA_FLAG | MODE_STDBY);

    /* 清除所有 IRQ flags */
    sx1276_write_reg(0x12, 0xFF);

    /* 設定 FIFO 寫入指標到 TX base addr */
    uint8_t tx_base = sx1276_read_reg(0x0E);   /* RegFifoTxBaseAddr */
    sx1276_write_reg(0x0D, tx_base);            /* RegFifoAddrPtr */

    /* 將 payload 寫入 FIFO */
    sx1276_write_fifo(payload, len);

    /* 設定 payload 長度 */
    sx1276_write_reg(0x22, len);   /* RegPayloadLength */

    /* 切換到 TX 模式，完成後硬體自動回 STDBY */
    sx1276_write_reg(0x01, LORA_FLAG | MODE_TX);

    /* Polling 等待 TxDone（實際應用改為中斷） */
    uint32_t timeout = 5000;   /* 5 秒超時 */
    while (timeout--) {
        uint8_t irq = sx1276_read_reg(0x12);   /* RegIrqFlags */
        if (irq & IRQ_TXDONE) {
            sx1276_write_reg(0x12, IRQ_TXDONE); /* 清 TxDone */
            return 0;
        }
        vTaskDelay(pdMS_TO_TICKS(1));
    }
    return -2;  /* timeout */
}
```

---

## RX 接收流程

```
RX 流程（RXCONTINUOUS 模式）：

STDBY
  |
  v
設 FifoAddrPtr = FifoRxBaseAddr
  |
  v
切換到 RXCONTINUOUS 模式
  |
  v
等待 DIO0 中斷（RxDone bit）
  |
  v
讀 RegIrqFlags：確認 RxDone=1，檢查 CrcError
  |
  v
讀 RegFifoRxCurrentAddr → 設到 RegFifoAddrPtr
  |
  v
讀 RegRxNbBytes 取得長度
  |
  v
SPI burst 讀取 FIFO payload
  |
  v
讀 RSSI 和 SNR（RegPktRssiValue, RegPktSnrValue）
  |
  v
清除 IRQ flags
```

```c
/* 啟動持續接收模式 */
void sx1276_start_rx(void) {
    sx1276_write_reg(0x01, LORA_FLAG | MODE_STDBY);
    sx1276_write_reg(0x12, 0xFF);  /* 清所有 IRQ */

    uint8_t rx_base = sx1276_read_reg(0x0F);   /* RegFifoRxBaseAddr */
    sx1276_write_reg(0x0D, rx_base);

    /* 切換到持續接收 */
    sx1276_write_reg(0x01, LORA_FLAG | MODE_RXCONT);
}

/* 收到封包時呼叫（從 ISR 或 polling 觸發） */
int sx1276_recv(uint8_t *buf, uint8_t *out_len) {
    uint8_t irq = sx1276_read_reg(0x12);

    if (!(irq & IRQ_RXDONE)) return -1;  /* 還沒收到 */

    if (irq & IRQ_CRCERR) {
        sx1276_write_reg(0x12, IRQ_RXDONE | IRQ_CRCERR);
        return -2;  /* CRC error */
    }

    /* 取得封包在 FIFO 中的位置和長度 */
    uint8_t fifo_addr = sx1276_read_reg(0x10); /* RegFifoRxCurrentAddr */
    uint8_t nb_bytes  = sx1276_read_reg(0x13); /* RegRxNbBytes */

    sx1276_write_reg(0x0D, fifo_addr);          /* RegFifoAddrPtr */
    sx1276_read_fifo(buf, nb_bytes);
    *out_len = nb_bytes;

    sx1276_write_reg(0x12, IRQ_RXDONE);         /* 清 RxDone */
    return 0;
}

/* 讀取最後封包的 RSSI 和 SNR */
void sx1276_get_pkt_status(int16_t *rssi, int8_t *snr) {
    uint8_t raw_snr  = sx1276_read_reg(0x19);  /* RegPktSnrValue */
    uint8_t raw_rssi = sx1276_read_reg(0x1A);  /* RegPktRssiValue */

    /* SNR：有號 byte，單位 0.25 dB */
    *snr = (int8_t)raw_snr / 4;

    /* RSSI 修正：高頻模式 RSSI = -157 + RegPktRssiValue
     * 若 SNR < 0，需額外修正（見 datasheet） */
    if (*snr < 0) {
        *rssi = -157 + raw_rssi + *snr;
    } else {
        *rssi = -157 + raw_rssi;
    }
}
```

---

## DIO0 中斷配置

DIO0 接 GPIO，TxDone 和 RxDone 時觸發，比 polling 省 CPU：

```c
#include "driver/gpio.h"

#define DIO0_GPIO  GPIO_NUM_26

static volatile bool pkt_ready = false;

static void IRAM_ATTR dio0_isr(void *arg) {
    pkt_ready = true;
}

void sx1276_gpio_init(void) {
    gpio_config_t cfg = {
        .pin_bit_mask = (1ULL << DIO0_GPIO),
        .mode         = GPIO_MODE_INPUT,
        .pull_up_en   = GPIO_PULLDOWN_ONLY,
        .pull_down_en = GPIO_PULLDOWN_ONLY,
        .intr_type    = GPIO_INTR_POSEDGE,    /* DIO0 上升沿觸發 */
    };
    gpio_config(&cfg);
    gpio_install_isr_service(0);
    gpio_isr_handler_add(DIO0_GPIO, dio0_isr, NULL);
}

/* DIO0 的功能要用 RegDioMapping1 (0x40) 設定：
 * TX 模式時映射 TxDone：[7:6] = 01
 * RX 模式時映射 RxDone：[7:6] = 00（預設）
 */
```

---

## 兩節點 PING/PONG 測試

Node A（發送）：

```c
void node_a_task(void *arg) {
    uint8_t ping[] = "PING";
    uint8_t buf[64];
    uint8_t len;
    int16_t rssi;
    int8_t  snr;

    sx1276_lora_init();
    sx1276_gpio_init();

    while (1) {
        /* 發送 PING */
        ESP_LOGI("LORA", "Sending PING...");
        sx1276_send(ping, sizeof(ping) - 1);

        /* 等待 PONG（最多 3 秒） */
        sx1276_start_rx();
        uint32_t t = xTaskGetTickCount();
        while (xTaskGetTickCount() - t < pdMS_TO_TICKS(3000)) {
            if (pkt_ready) {
                pkt_ready = false;
                if (sx1276_recv(buf, &len) == 0) {
                    buf[len] = '\0';
                    sx1276_get_pkt_status(&rssi, &snr);
                    ESP_LOGI("LORA", "Got: %s  RSSI=%d dBm  SNR=%d dB",
                             buf, rssi, snr);
                }
                break;
            }
            vTaskDelay(pdMS_TO_TICKS(10));
        }
        /* 每秒發一次 */
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}
```

Node B（回應）：

```c
void node_b_task(void *arg) {
    uint8_t pong[] = "PONG";
    uint8_t buf[64];
    uint8_t len;
    int16_t rssi;
    int8_t  snr;

    sx1276_lora_init();
    sx1276_gpio_init();
    sx1276_start_rx();

    while (1) {
        if (pkt_ready) {
            pkt_ready = false;
            if (sx1276_recv(buf, &len) == 0) {
                buf[len] = '\0';
                sx1276_get_pkt_status(&rssi, &snr);
                ESP_LOGI("LORA", "Got: %s  RSSI=%d dBm  SNR=%d dB",
                         buf, rssi, snr);

                if (len == 4 && buf[0] == 'P' && buf[1] == 'I') {
                    vTaskDelay(pdMS_TO_TICKS(50));  /* 短暫等待，避免 TX overlap */
                    sx1276_send(pong, sizeof(pong) - 1);
                    sx1276_start_rx();
                }
            } else {
                /* CRC error 或其他錯誤，重新進入 RX */
                sx1276_start_rx();
            }
        }
        vTaskDelay(pdMS_TO_TICKS(5));
    }
}
```

預期序列埠輸出（Node A）：

```
I (1000) LORA: Sending PING...
I (1350) LORA: Got: PONG  RSSI=-75 dBm  SNR=8 dB
I (2000) LORA: Sending PING...
I (2350) LORA: Got: PONG  RSSI=-75 dBm  SNR=8 dB
```

---

## 常見問題

| 現象 | 可能原因 | 排查方式 |
|------|---------|---------|
| RegVersion 不是 0x12 | SPI 接線錯誤或 CS 未低電位 | 用邏輯分析儀看 SPI 波形 |
| TxDone 永遠不觸發 | 頻率設錯、PA_BOOST 和天線腳不符 | 確認 SX1276 是 PA_BOOST 還是 RFO 版本 |
| CRC Error 一直出現 | 兩端 SF/BW/CR 設定不一致 | 確認兩端 RegModemConfig1/2 值相同 |
| RSSI 很差（< -120 dBm）| 天線沒接或頻率偏移 | 確認天線，用頻譜分析儀看輸出頻率 |
| SNR 很低 | 距離太遠或干擾 | 換高 SF，或把兩板靠近測試 |

---

## 自我檢核

- [ ] 能計算 923.2 MHz 對應的 RegFr 值（三個暫存器各填什麼）
- [ ] 能說明 RegModemConfig1=0x72 對應的 BW、CR 設定
- [ ] 知道為什麼切換到 LoRa mode 必須先進 SLEEP 模式
- [ ] TX 流程：FIFO 指標設定在哪一步，為什麼
- [ ] RX 流程：RxDone 後讀哪個暫存器取得封包在 FIFO 的位置
- [ ] 能解釋 RSSI 計算的修正項來自哪裡（低 SNR 情況）
- [ ] DIO0 ISR 為什麼標記 IRAM_ATTR

LoRa 用的是 Sub-GHz ISM 頻段，IEEE 802.15.4 在 2.4GHz 做出完全不同的設計，Zigbee 就建在上面。

→ [Ch 21 Zigbee 原理與實作](./21-zigbee.md)
