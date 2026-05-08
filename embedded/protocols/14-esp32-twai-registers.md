# Ch 14 — ESP32 TWAI 暫存器

> 目標：搞清楚 ESP32 的 TWAI（Two-Wire Automotive Interface）controller 每個關鍵暫存器的 offset 和 bit field，能手算 500kbps 的 bit timing 參數，並寫出正確的 register-level 初始化流程。

---

## TWAI 是什麼

ESP32 內建的 CAN 2.0 controller，Espressif 把它叫做 **TWAI**（Two-Wire Automotive Interface）。名字不同，協議完全相容 CAN 2.0A/B。

**重要**：TWAI controller 只處理 CAN 協議邏輯，不產生差分信號。你必須外接收發器（transceiver）才能上 CAN bus：

```
ESP32              收發器              CAN Bus
GPIO_TX ──────── TXD    CANH ────── CAN_H
GPIO_RX ──────── RXD    CANL ────── CAN_L
3.3V ──────────── VCC
GND ─────────────GND
```

常見收發器選擇：

| 型號 | 電壓 | 特點 |
|------|------|------|
| SN65HVD230 | 3.3V | 直接接 ESP32，不需電平轉換，推薦 |
| MCP2551 | 5V | 需要電平轉換，或用分壓電路接 RXD |
| TJA1050 | 5V | 高速（1Mbps），工業用，同樣需要電平轉換 |

---

## 暫存器基地址與 Reset Mode 原則

TWAI controller 的基地址：`0x3FF6B000`

**最重要的規則**：大多數暫存器只能在 Reset Mode 下寫入。初始化流程的第一步和最後一步都是操作 `TWAI_MODE_REG`。

---

## 關鍵暫存器詳解

### TWAI_MODE_REG（offset 0x000）

```
bit 7      bit 3      bit 2      bit 1       bit 0
  SM         AFM       STM        LOM         RM
Sleep Mode  AccFilter  Self Test  Listen Only  Reset Mode
```

| Bit | 名稱 | 說明 |
|-----|------|------|
| 0 | `RM`（Reset Mode）| 1=進入 reset mode，0=正常工作。初始化必須先設 1，完成後清 0 |
| 1 | `LOM`（Listen Only Mode）| 1=只監聽，不發 ACK，不發 error frame。抓封包用 |
| 2 | `STM`（Self Test Mode）| 1=自測，TX 自動 ACK 自己，不需要外部節點 |
| 3 | `AFM`（Acceptance Filter Mode）| 0=32-bit single filter，1=16-bit dual filter |

初始化時序：設 RM=1 → 設定其他暫存器 → 清 RM=0。

### TWAI_BUS_TIMING_0_REG（offset 0x018）

```
bits [15:14]  bits [13:8]    bits [5:0]
  SJW          BRP[5:0]      (reserved in upper byte)
Sync Jump     Baud Rate
Width         Prescaler
```

實際欄位（8-bit register，在 ESP32 TRM 的實際描述是 16-bit 寬）：

| Bit | 名稱 | 說明 |
|-----|------|------|
| [13:8] | `BAUD_PRESC`（Baud Rate Prescaler）| CAN clock = APB / (2 × (BAUD_PRESC + 1)) |
| [15:14] | `SYNC_JUMP_WIDTH`（SJW）| 重同步跳躍寬度，0~3 對應 1~4 個 TQ |

### TWAI_BUS_TIMING_1_REG（offset 0x01C）

| Bit | 名稱 | 說明 |
|-----|------|------|
| [3:0] | `TIME_SEG1`（TSEG1）| Phase Segment 1，1~16 個 TQ |
| [6:4] | `TIME_SEG2`（TSEG2）| Phase Segment 2，1~8 個 TQ |
| [7] | `TRIPLE_SAMPLING` | 1=每個 bit 採樣三次取多數，噪訊環境用 |

---

## Bit Timing 計算（500kbps 範例）

CAN bit timing 把一個 bit 時間（Tbit）分成四個 segment：

```
Tbit = Sync Seg + Prop Seg + Phase Seg1 + Phase Seg2

    Sync  Prop   Phase1    Phase2
     │    │──────│─────────│──────│
     1TQ  1~8TQ  1~8TQ     1~8TQ
          ├─── TSEG1 ──────┤
     採樣點在 Phase1 結尾
```

計算步驟（APB clock = 80MHz，目標 500kbps）：

```
1. 每個 bit 時間 = 1 / 500kbps = 2000ns

2. 選 TQ（Time Quantum）數量，常用 16 TQ / bit（CiA 推薦）：
   TQ = 2000ns / 16 = 125ns

3. 計算 BAUD_PRESC：
   TQ = 2 × (BAUD_PRESC + 1) / APB_CLK
   125ns = 2 × (BAUD_PRESC + 1) / 80MHz
   BAUD_PRESC + 1 = 125ns × 80MHz / 2 = 5
   BAUD_PRESC = 4

4. 分配 TSEG1 和 TSEG2：
   16 TQ 中扣掉固定的 1 TQ Sync Seg = 15 TQ 給 TSEG1 + TSEG2
   採樣點設在 75%（CiA 建議）：
   採樣點位置 = (1 + TSEG1) / 16 = 0.75
   1 + TSEG1 = 12，所以 TSEG1 = 11
   TSEG2 = 15 - 11 = 4（但 TSEG2 欄位值 = 實際 TQ 數 - 1）
   寫入值：TSEG1 = 11 - 1 = 10，TSEG2 = 4 - 1 = 3

5. SJW 通常設 1 TQ，寫入值 = 0

暫存器值：
   BUS_TIMING_0：BAUD_PRESC = 4，SJW = 0
   BUS_TIMING_1：TSEG1 = 10，TSEG2 = 3，TRIPLE_SAMPLING = 0
```

**注意**：ESP32 TRM 裡 TSEG1/TSEG2 的 register 值是 actual TQ - 1。上面的計算已經做了這個轉換。

---

## TX Buffer 格式（TWAI_TX_BUF_REG 系列，offset 0x040~0x05C）

```
TWAI_TX_BUF_0：Frame Information（FI byte）
  bit 7：FF（Frame Format）0=Standard，1=Extended
  bit 6：RTR（Remote Transmission Request）
  bits[3:0]：DLC（Data Length Code）

TWAI_TX_BUF_1：Standard ID 高 8 bits（ID[10:3]）
TWAI_TX_BUF_2：bits[7:5] = ID[2:0]，其餘填 0
TWAI_TX_BUF_3~A：Data byte 0~7（DLC 決定幾個有效）
```

11-bit ID 的拆分方式：

```c
// Standard Frame（CAN 2.0A）
tx_buf[1] = (id >> 3) & 0xFF;          // ID[10:3]
tx_buf[2] = (id & 0x07) << 5;          // ID[2:0] 放在高 3 bit
```

---

## RX Buffer 格式（TWAI_RX_BUF_REG 系列，offset 0x060~0x07C）

格式與 TX Buffer 相同，讀出後必須設 `RRB`（Release Receive Buffer）bit 才能接收下一個 frame。

---

## Acceptance Filter（接收過濾器）

TWAI 有硬體 acceptance filter，在 Reset Mode 下設定：

```
暫存器：
  TWAI_ACC_CODE_0_REG（offset 0x040）：期望的 ID 樣板
  TWAI_ACC_CODE_1_REG（offset 0x044）
  TWAI_ACC_CODE_2_REG（offset 0x048）
  TWAI_ACC_CODE_3_REG（offset 0x04C）
  TWAI_ACC_MASK_0_REG（offset 0x050）：1=不管（don't care），0=必須符合
  TWAI_ACC_MASK_1_REG（offset 0x054）
  TWAI_ACC_MASK_2_REG（offset 0x058）
  TWAI_ACC_MASK_3_REG（offset 0x05C）
```

Single Filter 模式（AFM=0，預設）下，32-bit code 和 32-bit mask 對應的位元如下：

```
位元 [31:21]：Standard ID[10:0]
位元 [20]：RTR bit
位元 [19:16]：（保留）
位元 [15:0]：Data byte 0 和 byte 1（前兩個 data byte）
```

接收條件：`(收到的位元 XOR code) AND (NOT mask) == 0`

只接受 ID=0x100 的範例（11-bit ID，mask 只管 ID 欄位）：

```c
// code：ID 0x100 左移 21 bits 到 bit[31:21]
uint32_t code = 0x100 << 21;  // = 0x80000000
uint32_t mask = 0x001FFFFF;   // bit[20:0] 全部 don't care（不管 RTR 和 data）
```

---

## TWAI_STATUS_REG（offset 0x008）

| Bit | 名稱 | 意義 |
|-----|------|------|
| 0 | `RBS`（RX Buffer Status）| 1=RX buffer 有資料等待讀取 |
| 1 | `DOS`（Data Overrun Status）| 1=RX buffer 溢出（frame 被丟棄）|
| 2 | `TBS`（TX Buffer Status）| 1=TX buffer 空閒，可以寫入新 frame |
| 3 | `TCS`（TX Complete Status）| 1=上一個 TX 已完成 |
| 4 | `RS`（Receive Status）| 1=正在接收 frame |
| 5 | `TS`（Transmit Status）| 1=正在發送 frame |
| 6 | `ES`（Error Status）| 1=TEC 或 REC 超過 warning limit（96）|
| 7 | `BS`（Bus Status）| 1=Bus Off 狀態 |

---

## TWAI_INT_RAW_REG（offset 0x004）與 TWAI_INT_ENA_REG（offset 0x010）

| Bit | 名稱 | 意義 |
|-----|------|------|
| 0 | `RX_INT` | 收到 frame |
| 1 | `TX_INT` | TX 完成 |
| 2 | `ERR_WARN_INT` | 錯誤計數超過 warning limit |
| 3 | `DATA_OVRUN_INT` | RX buffer 溢出 |
| 5 | `ERR_PASSIVE_INT` | 進入或離開 Error Passive 狀態 |
| 7 | `BUS_ERR_INT` | Bus Error 發生 |
| 8 | `ARB_LOST_INT` | 仲裁輸了 |

---

## 完整初始化程式碼

```c
#include <stdint.h>
#include "esp_err.h"
#include "driver/gpio.h"

/* TWAI controller 基地址 */
#define TWAI_BASE   0x3FF6B000UL

/* 暫存器 offset */
#define TWAI_MODE_REG       (*(volatile uint32_t *)(TWAI_BASE + 0x000))
#define TWAI_CMD_REG        (*(volatile uint32_t *)(TWAI_BASE + 0x004))
#define TWAI_STATUS_REG     (*(volatile uint32_t *)(TWAI_BASE + 0x008))
#define TWAI_INT_RAW_REG    (*(volatile uint32_t *)(TWAI_BASE + 0x00C))
#define TWAI_INT_ENA_REG    (*(volatile uint32_t *)(TWAI_BASE + 0x010))
#define TWAI_BUS_TIMING_0   (*(volatile uint32_t *)(TWAI_BASE + 0x018))
#define TWAI_BUS_TIMING_1   (*(volatile uint32_t *)(TWAI_BASE + 0x01C))
#define TWAI_ARB_LOST_CAP   (*(volatile uint32_t *)(TWAI_BASE + 0x02C))
#define TWAI_ERR_CODE_CAP   (*(volatile uint32_t *)(TWAI_BASE + 0x030))
#define TWAI_ERR_WARNING    (*(volatile uint32_t *)(TWAI_BASE + 0x034))
#define TWAI_RX_ERR_CNT     (*(volatile uint32_t *)(TWAI_BASE + 0x038))
#define TWAI_TX_ERR_CNT     (*(volatile uint32_t *)(TWAI_BASE + 0x03C))
#define TWAI_ACC_CODE_0     (*(volatile uint32_t *)(TWAI_BASE + 0x040))
#define TWAI_ACC_CODE_1     (*(volatile uint32_t *)(TWAI_BASE + 0x044))
#define TWAI_ACC_CODE_2     (*(volatile uint32_t *)(TWAI_BASE + 0x048))
#define TWAI_ACC_CODE_3     (*(volatile uint32_t *)(TWAI_BASE + 0x04C))
#define TWAI_ACC_MASK_0     (*(volatile uint32_t *)(TWAI_BASE + 0x050))
#define TWAI_ACC_MASK_1     (*(volatile uint32_t *)(TWAI_BASE + 0x054))
#define TWAI_ACC_MASK_2     (*(volatile uint32_t *)(TWAI_BASE + 0x058))
#define TWAI_ACC_MASK_3     (*(volatile uint32_t *)(TWAI_BASE + 0x05C))

/* TWAI_MODE_REG bit 定義 */
#define TWAI_MODE_RM    (1 << 0)  /* Reset Mode */
#define TWAI_MODE_LOM   (1 << 1)  /* Listen Only Mode */
#define TWAI_MODE_STM   (1 << 2)  /* Self Test Mode */
#define TWAI_MODE_AFM   (1 << 3)  /* Acceptance Filter Mode（dual filter）*/

/* TWAI_CMD_REG bit 定義 */
#define TWAI_CMD_TR     (1 << 0)  /* Transmit Request */
#define TWAI_CMD_AT     (1 << 1)  /* Abort Transmission */
#define TWAI_CMD_RRB    (1 << 2)  /* Release Receive Buffer */
#define TWAI_CMD_CDO    (1 << 3)  /* Clear Data Overrun */
#define TWAI_CMD_SRR    (1 << 4)  /* Self Reception Request（自測用）*/

/* TWAI_STATUS_REG bit 定義 */
#define TWAI_STATUS_RBS (1 << 0)  /* RX Buffer Status */
#define TWAI_STATUS_TBS (1 << 2)  /* TX Buffer Status */
#define TWAI_STATUS_ES  (1 << 6)  /* Error Status */
#define TWAI_STATUS_BS  (1 << 7)  /* Bus Status（Bus Off）*/

/* 使能 TWAI peripheral clock（透過 DPORT 暫存器）*/
#define DPORT_PERIP_CLK_EN_REG  (*(volatile uint32_t *)0x3FF000C0)
#define DPORT_PERIP_RST_EN_REG  (*(volatile uint32_t *)0x3FF000C4)
#define DPORT_CAN_CLK_EN        (1 << 13)
#define DPORT_CAN_RST           (1 << 13)

/* TX/RX buffer 暫存器，TWAI_BASE + 0x040 起為 TX/RX buffer（因共用，由模式決定）*/
/* 在 Normal Mode 下 0x040 起為 TX buffer；在收到 frame 後 RX buffer 透過 0x060 offset 讀取 */
/* 但實際上 ESP32 TRM 說明 TX 和 RX buffer 共用 0x040~0x05C，方向由讀寫決定 */
#define TWAI_TX_BUF(n)  (*(volatile uint32_t *)(TWAI_BASE + 0x040 + (n) * 4))
#define TWAI_RX_BUF(n)  (*(volatile uint32_t *)(TWAI_BASE + 0x040 + (n) * 4))

void twai_init_500kbps(gpio_num_t tx_gpio, gpio_num_t rx_gpio)
{
    /* 1. 使能 peripheral clock，並 reset controller */
    DPORT_PERIP_CLK_EN_REG |= DPORT_CAN_CLK_EN;
    DPORT_PERIP_RST_EN_REG |= DPORT_CAN_RST;
    DPORT_PERIP_RST_EN_REG &= ~DPORT_CAN_RST;

    /* 2. 設定 GPIO matrix：TX 和 RX pin */
    /* TWAI_TX_IDX = 80, TWAI_RX_IDX = 81（ESP32 GPIO matrix 信號編號）*/
    gpio_set_direction(tx_gpio, GPIO_MODE_OUTPUT);
    gpio_set_direction(rx_gpio, GPIO_MODE_INPUT);
    gpio_matrix_out(tx_gpio, 80, false, false);  /* TWAI_TX_IDX */
    gpio_matrix_in(rx_gpio, 81, false);           /* TWAI_RX_IDX */

    /* 3. 進入 Reset Mode（必須在 RM=1 才能設定以下暫存器）*/
    TWAI_MODE_REG = TWAI_MODE_RM;

    /* 4. 設定 Bit Timing（500kbps，APB=80MHz，16 TQ/bit）
     *    BAUD_PRESC = 4，SJW = 0（1 TQ）
     *    TSEG1 = 10（11 TQ），TSEG2 = 3（4 TQ）
     *    採樣點 = (1 + 11) / 16 = 75%
     */
    TWAI_BUS_TIMING_0 = (0 << 14) | (4 << 0);   /* SJW=0, BRP=4 */
    TWAI_BUS_TIMING_1 = (0 << 7)  |              /* TRIPLE_SAMPLING=0 */
                        (3 << 4)  |              /* TSEG2=3 */
                        (10 << 0);               /* TSEG1=10 */

    /* 5. Acceptance Filter：接受所有 frame（mask 全 1 = 全部 don't care）*/
    TWAI_ACC_CODE_0 = 0x00;
    TWAI_ACC_CODE_1 = 0x00;
    TWAI_ACC_CODE_2 = 0x00;
    TWAI_ACC_CODE_3 = 0x00;
    TWAI_ACC_MASK_0 = 0xFF;
    TWAI_ACC_MASK_1 = 0xFF;
    TWAI_ACC_MASK_2 = 0xFF;
    TWAI_ACC_MASK_3 = 0xFF;

    /* 6. 使能中斷（TX 完成 + RX 收到）*/
    TWAI_INT_ENA_REG = (1 << 0) | (1 << 1);  /* RX_INT_ENA | TX_INT_ENA */

    /* 7. 離開 Reset Mode，開始正常工作 */
    TWAI_MODE_REG = 0;
}
```

---

## 自我檢核

- [ ] 能說明為什麼需要外接收發器，以及 SN65HVD230 和 MCP2551 的差異
- [ ] 知道 TWAI_MODE_REG 的 RM bit 的作用，以及哪些暫存器必須在 RM=1 時設定
- [ ] 能用 APB=80MHz 手算出 500kbps 所需的 BAUD_PRESC、TSEG1、TSEG2 值
- [ ] 能解釋 Acceptance Filter 的 code/mask 邏輯，並寫出只接受特定 ID 的設定
- [ ] 能讀懂 TWAI_STATUS_REG 的 TBS 和 RBS bit 的含義
- [ ] 能把上面的初始化程式碼改成 250kbps（APB 一樣 80MHz）

下一章用這些暫存器寫完整的 frame 收發函式，並接線讓兩個節點互相通訊。

→ [Ch 15 實作：TWAI Register-Level → CAN Frame 收發](./15-twai-can-frames.md)
