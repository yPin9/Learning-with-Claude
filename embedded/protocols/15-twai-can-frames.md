# Ch 15 — 實作：TWAI Register-Level → CAN Frame 收發

> 目標：寫出完整可跑的 CAN frame 收發程式，接線讓兩顆 ESP32 互相通訊，驗證仲裁機制，並排查常見的接線和 bit timing 問題。

---

## 硬體接線

兩個節點的接線方式（每個節點各一顆 ESP32 + 一顆 SN65HVD230）：

```
Node A                                          Node B
ESP32       SN65HVD230                          SN65HVD230       ESP32
GPIO21 ─── TXD    CANH ─────────────────────── CANH    TXD ─── GPIO21
GPIO22 ─── RXD    CANL ─────────────────────── CANL    RXD ─── GPIO22
3.3V ───── VCC         │                     │         VCC ──── 3.3V
GND ─────── GND        │                     │          GND ─── GND
                       │ 120Ω 終端電阻         │ 120Ω 終端電阻
                       └── CAN_H 到 CAN_L ────┘
```

**終端電阻是必要的**：CAN bus 兩端各一顆 120Ω，接在 CAN_H 和 CAN_L 之間。沒有終端電阻，信號會在 bus 末端反射，導致 bit 採樣錯誤，frame 發不出去或持續出現 CRC error。

SN65HVD230 的 RS（Rate Select）腳位：拉到 GND = 高速模式（最高 1Mbps），拉到 3.3V = 斜率控制模式（降低 EMI，用於低速場景）。一般實驗用拉 GND。

---

## 發送函式

```c
#include <stdint.h>
#include <string.h>

/* 沿用 Ch 14 的 macro 定義 */
#define TWAI_BASE           0x3FF6B000UL
#define TWAI_STATUS_REG     (*(volatile uint32_t *)(TWAI_BASE + 0x008))
#define TWAI_CMD_REG        (*(volatile uint32_t *)(TWAI_BASE + 0x004))
#define TWAI_STATUS_TBS     (1 << 2)   /* TX Buffer Status：1=空閒 */
#define TWAI_STATUS_ES      (1 << 6)   /* Error Status */
#define TWAI_STATUS_BS      (1 << 7)   /* Bus Status（Bus Off）*/
#define TWAI_CMD_TR         (1 << 0)   /* Transmit Request */
#define TWAI_CMD_RRB        (1 << 2)   /* Release Receive Buffer */
#define TWAI_STATUS_RBS     (1 << 0)   /* RX Buffer Status：1=有資料 */

/* TX/RX buffer 暫存器 */
#define TWAI_TX_BUF(n)      (*(volatile uint32_t *)(TWAI_BASE + 0x040 + (n) * 4))
#define TWAI_RX_BUF(n)      (*(volatile uint32_t *)(TWAI_BASE + 0x040 + (n) * 4))

/*
 * twai_send_frame - 發送一個 Standard CAN Data Frame（CAN 2.0A）
 *
 * @id:   11-bit CAN ID（0x000 ~ 0x7FF）
 * @dlc:  Data Length Code（0 ~ 8）
 * @data: 資料指標，dlc 個 byte
 *
 * 回傳 0 成功，-1 TX buffer 忙或 Bus Off
 */
int twai_send_frame(uint32_t id, uint8_t dlc, const uint8_t *data)
{
    /* 1. 確認 TX buffer 空閒（TBS=1）且 bus 不在 Off 狀態 */
    if (!(TWAI_STATUS_REG & TWAI_STATUS_TBS)) {
        return -1;  /* TX buffer 忙，上一個 frame 還沒發完 */
    }
    if (TWAI_STATUS_REG & TWAI_STATUS_BS) {
        return -1;  /* Bus Off，需要手動恢復 */
    }

    /* 2. 寫入 Frame Information byte（TX_BUF_0）
     *    bit 7：FF=0（Standard Frame）
     *    bit 6：RTR=0（Data Frame）
     *    bits[3:0]：DLC
     */
    TWAI_TX_BUF(0) = (uint8_t)(dlc & 0x0F);  /* FF=0, RTR=0, DLC */

    /* 3. 寫入 11-bit ID
     *    TX_BUF_1 = ID[10:3]（高 8 bits）
     *    TX_BUF_2 = ID[2:0] 左移到 bits[7:5]，其餘為 0
     */
    TWAI_TX_BUF(1) = (uint8_t)((id >> 3) & 0xFF);
    TWAI_TX_BUF(2) = (uint8_t)((id & 0x07) << 5);

    /* 4. 寫入資料 byte（TX_BUF_3 起）*/
    for (uint8_t i = 0; i < dlc && i < 8; i++) {
        TWAI_TX_BUF(3 + i) = data[i];
    }

    /* 5. 設定 TR（Transmit Request）bit 觸發發送 */
    TWAI_CMD_REG = TWAI_CMD_TR;

    return 0;
}

/*
 * twai_send_frame_blocking - 阻塞等待 TX buffer 空閒後發送
 * 最多等待 timeout_us 微秒，超時回傳 -1
 */
int twai_send_frame_blocking(uint32_t id, uint8_t dlc, const uint8_t *data,
                              uint32_t timeout_us)
{
    uint32_t start = esp_timer_get_time();  /* esp-idf 提供的 64-bit us timer */

    while (!(TWAI_STATUS_REG & TWAI_STATUS_TBS)) {
        if ((uint32_t)(esp_timer_get_time() - start) > timeout_us) {
            return -1;
        }
    }
    return twai_send_frame(id, dlc, data);
}
```

---

## 接收函式

```c
/* CAN frame 接收結果的結構 */
typedef struct {
    uint32_t id;        /* 11-bit ID */
    uint8_t  dlc;       /* Data Length Code */
    uint8_t  rtr;       /* 1=Remote Frame，0=Data Frame */
    uint8_t  data[8];   /* 資料（最多 8 byte）*/
} twai_frame_t;

/*
 * twai_receive_frame - polling 方式接收一個 frame
 *
 * 回傳 0 成功，-1 沒有資料
 * 注意：讀完必須呼叫，讓硬體釋放 RX buffer
 */
int twai_receive_frame(twai_frame_t *out)
{
    /* 1. 檢查 RX Buffer Status（RBS=1 表示有資料）*/
    if (!(TWAI_STATUS_REG & TWAI_STATUS_RBS)) {
        return -1;
    }

    /* 2. 讀取 Frame Information byte */
    uint8_t fi = (uint8_t)(TWAI_RX_BUF(0) & 0xFF);
    /* FF bit（bit 7）= 0 代表 Standard Frame，1 代表 Extended */
    /* RTR bit（bit 6）*/
    out->rtr = (fi >> 6) & 0x01;
    out->dlc = fi & 0x0F;

    /* 3. 重建 11-bit ID */
    uint8_t id_high = (uint8_t)(TWAI_RX_BUF(1) & 0xFF);  /* ID[10:3] */
    uint8_t id_low  = (uint8_t)(TWAI_RX_BUF(2) & 0xFF);  /* bits[7:5] = ID[2:0] */
    out->id = ((uint32_t)id_high << 3) | (id_low >> 5);

    /* 4. 讀取資料 bytes */
    uint8_t len = (out->dlc > 8) ? 8 : out->dlc;
    for (uint8_t i = 0; i < len; i++) {
        out->data[i] = (uint8_t)(TWAI_RX_BUF(3 + i) & 0xFF);
    }

    /* 5. 釋放 RX buffer（設 RRB bit），讓硬體可以接收下一個 frame */
    TWAI_CMD_REG = TWAI_CMD_RRB;

    return 0;
}
```

---

## Acceptance Filter 設定：只接受特定 ID 範圍

只接受 ID 0x100 ~ 0x1FF（最高位是 1，其餘不管）：

```c
/*
 * twai_set_acceptance_filter - 設定 acceptance filter
 * 必須在 Reset Mode 下呼叫（TWAI_MODE_REG 的 RM bit = 1）
 *
 * 只接受 Standard Frame（11-bit ID），single filter 模式
 * code[31:21] = ID[10:0]，code[20] = RTR，bits[15:0] = data byte 0/1（我們設 don't care）
 */
void twai_set_acceptance_filter_single(uint32_t id_pattern, uint32_t id_mask)
{
    /* 把 11-bit ID 放到 32-bit code 的 bits[31:21] */
    uint32_t code32 = (id_pattern & 0x7FF) << 21;
    /* mask 的邏輯：1 = don't care，0 = 必須符合 */
    /* id_mask 的 1 代表這個 ID bit 不管，轉換成暫存器 mask */
    uint32_t mask32 = ((~id_mask) & 0x7FF) << 21;
    mask32 = ~mask32;  /* 暫存器 mask：1=don't care，所以邏輯反轉 */
    /* bit[20]（RTR）和 bits[15:0]（data）全部 don't care */
    mask32 |= (1 << 20) | 0x0000FFFF;

    /* 寫入 4 個 byte（大端序，code[31:24] 放 ACC_CODE_0）*/
    #define TWAI_ACC_CODE_0  (*(volatile uint32_t *)(TWAI_BASE + 0x040))
    #define TWAI_ACC_CODE_1  (*(volatile uint32_t *)(TWAI_BASE + 0x044))
    #define TWAI_ACC_CODE_2  (*(volatile uint32_t *)(TWAI_BASE + 0x048))
    #define TWAI_ACC_CODE_3  (*(volatile uint32_t *)(TWAI_BASE + 0x04C))
    #define TWAI_ACC_MASK_0  (*(volatile uint32_t *)(TWAI_BASE + 0x050))
    #define TWAI_ACC_MASK_1  (*(volatile uint32_t *)(TWAI_BASE + 0x054))
    #define TWAI_ACC_MASK_2  (*(volatile uint32_t *)(TWAI_BASE + 0x058))
    #define TWAI_ACC_MASK_3  (*(volatile uint32_t *)(TWAI_BASE + 0x05C))

    TWAI_ACC_CODE_0 = (code32 >> 24) & 0xFF;
    TWAI_ACC_CODE_1 = (code32 >> 16) & 0xFF;
    TWAI_ACC_CODE_2 = (code32 >>  8) & 0xFF;
    TWAI_ACC_CODE_3 = (code32      ) & 0xFF;

    TWAI_ACC_MASK_0 = (mask32 >> 24) & 0xFF;
    TWAI_ACC_MASK_1 = (mask32 >> 16) & 0xFF;
    TWAI_ACC_MASK_2 = (mask32 >>  8) & 0xFF;
    TWAI_ACC_MASK_3 = (mask32      ) & 0xFF;
}
```

---

## 完整 main：Node A 發，Node B 收

```c
/* ── Node A（發送方）main.c ─────────────────────────────── */
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_timer.h"

extern void twai_init_500kbps(int tx_gpio, int rx_gpio);
extern int  twai_send_frame_blocking(uint32_t id, uint8_t dlc,
                                     const uint8_t *data, uint32_t timeout_us);

static const char *TAG = "NODE_A";

void app_main(void)
{
    twai_init_500kbps(21, 22);  /* TX=GPIO21, RX=GPIO22 */

    uint8_t counter = 0;
    while (1) {
        uint8_t payload[4] = {
            counter,
            counter + 1,
            counter + 2,
            counter + 3,
        };

        int ret = twai_send_frame_blocking(0x100, 4, payload, 10000);
        if (ret == 0) {
            ESP_LOGI(TAG, "Sent ID=0x100 data=[%d %d %d %d]",
                     payload[0], payload[1], payload[2], payload[3]);
        } else {
            ESP_LOGW(TAG, "Send failed (bus off or timeout)");
        }
        counter++;
        vTaskDelay(pdMS_TO_TICKS(100));
    }
}

/* ── Node B（接收方）main.c ─────────────────────────────── */
/* （另一顆 ESP32 燒這份 firmware）*/
void app_main_node_b(void)
{
    twai_init_500kbps(21, 22);

    twai_frame_t frame;
    while (1) {
        if (twai_receive_frame(&frame) == 0) {
            ESP_LOGI("NODE_B", "Recv ID=0x%03X DLC=%d RTR=%d data=[%d %d %d %d]",
                     frame.id, frame.dlc, frame.rtr,
                     frame.data[0], frame.data[1],
                     frame.data[2], frame.data[3]);
        }
        vTaskDelay(pdMS_TO_TICKS(1));
    }
}
```

---

## 仲裁驗證：兩個節點同時發不同 ID

在兩顆 ESP32 上各自設定不同的 ID，讓它們在同一時刻嘗試發送：

```c
/* Node A（ID=0x200，優先級低）和 Node B（ID=0x100，優先級高）同時觸發發送 */
/* Node B 應該贏得仲裁，Node A 的訊息晚一個 bit time 後重試 */

/* 驗證方法：
 * 1. 兩個節點都接收，印出收到的訊息
 * 2. 用邏輯分析儀同時抓 Node A 的 TX pin 和 bus 電平
 * 3. 仲裁期間 Node A TX 還在發，但 bus 電平（Node B 的 Dominant）覆蓋了它
 * 4. Node A 讀回不同 → 退出仲裁，ARB_LOST_INT 應該觸發
 */

/* 監控仲裁輸贏（讀 Arbitration Lost Capture Register）*/
#define TWAI_ARB_LOST_CAP  (*(volatile uint32_t *)(TWAI_BASE + 0x02C))
/* bits[4:0]：仲裁輸掉的 bit position（相對 frame 起始）*/
```

---

## 常見問題排查

| 症狀 | 可能原因 | 排查步驟 |
|------|---------|---------|
| 發送後 TCS 一直是 0，TX 沒完成 | 沒有接終端電阻，信號無法穩定 | 量 CAN_H/CAN_L 波形，沒有終端電阻時 Dominant 電平會偏低 |
| 持續 CRC error，REC 一直累積 | TSEG1/TSEG2 設錯，採樣點不對 | 用邏輯分析儀解碼，看 CAN 解碼器報告的 bit timing |
| Frame 偶爾發出去，偶爾 Bus Off | 只有一端有終端電阻，或兩端各 60Ω 反而對 | 兩端各 120Ω，並聯後 60Ω 是正確的，不要用一顆 60Ω 代替 |
| Node B 收不到，但 Node A 顯示發成功 | Acceptance Filter 設太嚴，ID 被過濾掉 | 先把 mask 全設 0xFF（接受所有），確認基本通訊正常 |
| 邏輯分析儀看到 Stuff Error | 兩端 baud rate 不一致，或 crystal 誤差大 | 確認兩顆 ESP32 的 APB clock 設定相同，計算 BRP/TSEG 再對齊 |
| SN65HVD230 發燙 | RS 腳未接，可能進入 high-slope 模式或 standby | RS 接 GND，進入 normal high-speed 模式 |

---

## 邏輯分析儀驗波形

用邏輯分析儀（8 channel，採樣率 ≥ 4MHz）同時抓兩個 channel：
- CH0：CAN_H（單端量，絕對電壓）
- CH1：CAN_L（單端電壓）
- CH0 - CH1 差分：大多數分析儀軟體（Saleae Logic）可以直接算差值

解碼設定：CAN 2.0，Bit Rate 500kbps，選擇差分輸入或 CH0 作為 digital。

正常波形特徵：
- Dominant 時 CAN_H ≈ 3.5V，CAN_L ≈ 1.5V，差值 2V
- Recessive 時兩條線都在 2.5V，差值接近 0
- EOF 是 7 個連續 Recessive bit，在示波器上是一段高電平平台

---

## 自我檢核

- [ ] 能解釋 FI byte 的 FF/RTR/DLC bit 各自的位置和意義
- [ ] 能說明 11-bit ID 如何拆分寫入 TX_BUF_1 和 TX_BUF_2（不看程式碼）
- [ ] 接兩顆 ESP32 + SN65HVD230，燒上程式，確認 Node A 發、Node B 收的 log 正常
- [ ] 把兩端的 120Ω 終端電阻拔一顆，觀察錯誤率有什麼變化
- [ ] 用邏輯分析儀解碼 CAN，確認 ID、DLC、Data 都正確
- [ ] 讓兩個節點同時發不同 ID，確認低 ID 的訊息先完整出現

下一章進入 BLE 協議堆疊，從 PHY 到 GATT 把整個 stack 的結構建立清楚。

→ [練習 B：CAN 雙節點仲裁測試](./practice-b-can-arbitration.md)
