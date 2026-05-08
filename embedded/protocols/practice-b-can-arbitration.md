# 練習 B：CAN 雙節點仲裁測試

在兩顆 ESP32 上用 TWAI register-level 驅動跑完整的 CAN 仲裁場景，並主動注入錯誤觀察 TEC/REC 上升過程。這道題驗證的是你對 CAN 仲裁機制和錯誤計數器的理解，不只是「能收發 frame」。

**前置章節**：Ch 13（CAN 原理）、Ch 14（ESP32 TWAI 暫存器）、Ch 15（TWAI 收發實作）

---

## 硬體需求

| 元件 | 數量 | 接法 |
|---|---|---|
| ESP32 DevKitC | 2 | Node A、Node B |
| SN65HVD230 CAN 收發器 | 2 | 各一顆，接在對應 ESP32 |
| 120Ω 終端電阻 | 2 | CAN_H 和 CAN_L 兩端各一顆 |
| 邏輯分析儀（選配） | 1 | 掛在 CAN_H/CAN_L 驗波形 |

**接線：**
```
ESP32 Node A
  GPIO4  -> SN65HVD230 #A (TXD)
  GPIO5  <- SN65HVD230 #A (RXD)
  3.3V   -> SN65HVD230 #A (3V3)
  GND    -> SN65HVD230 #A (GND)

ESP32 Node B
  GPIO4  -> SN65HVD230 #B (TXD)
  GPIO5  <- SN65HVD230 #B (RXD)
  3.3V   -> SN65HVD230 #B (3V3)
  GND    -> SN65HVD230 #B (GND)

SN65HVD230 #A CAN_H  ---[120Ω]---+--- SN65HVD230 #B CAN_H
SN65HVD230 #A CAN_L  ---[120Ω]---+--- SN65HVD230 #B CAN_L
```

---

## 題目規格

### Part 1：基礎收發

**Node A 任務：**
- 每 500ms 發一個 Standard Frame（11-bit ID）
- ID = 0x100，DLC = 8
- Data = 當前的 FreeRTOS tick count（uint32_t，放在 byte 0–3，big-endian；byte 4–7 填 0xAA）

**Node B 任務：**
- 持續監聽 CAN bus
- 收到 frame 後，透過 UART0（115200）印出：
  ```
  RX: ID=0x100 DLC=8 DATA=[00 00 00 01 AA AA AA AA]
  ```

驗收：Node B 序列埠每 500ms 出現一行，tick count 遞增。

### Part 2：仲裁測試

修改程式，讓 Node A 和 Node B 同時嘗試發送，看誰贏得仲裁：

- Node A 發：ID=0x050（低優先，數值較大），DLC=1，Data=[0x55]
- Node B 發：ID=0x030（高優先，數值較小），DLC=1，Data=[0xAA]
- 兩個 node 同時觸發發送（用 GPIO 中斷或 semaphore 同步）

CAN 仲裁規則：ID 數值越小越優先（dominant bit 勝出）。預期結果：
- ID=0x030 的 frame 先完成發送
- ID=0x050 的 frame 自動重試，在 0x030 完成後送出

**Node B 序列埠預期輸出：**
```
RX: ID=0x030 DLC=1 DATA=[AA] (arbitration winner)
RX: ID=0x050 DLC=1 DATA=[55] (retransmitted)
```

### Part 3：Error Injection

製造一個 ACK Error 觀察 TEC 上升：

1. 把 Node B 的 CAN 接收器暫時斷開（拔掉 SN65HVD230 #B 的 RXD 腳或直接斷 CAN_H/L）
2. Node A 持續發送 frame，因為 bus 上只有 Node A 自己，ACK slot 不會有人填
3. Node A 每次 ACK Error 後 TEC +8（CAN 規範）
4. Node A 讀取自己的 TWAI_TX_ERR_CNT_REG，印出 TEC 值
5. 當 TEC > 127，Node A 進入 Error Passive 狀態

**Node A 序列埠預期輸出：**
```
TX attempt #1: TEC=8   state=ERROR_ACTIVE
TX attempt #2: TEC=16  state=ERROR_ACTIVE
...
TX attempt #16: TEC=128 state=ERROR_PASSIVE
TX attempt #17: TEC=136 state=ERROR_PASSIVE
```

---

## 期望輸出

### Node B 序列埠（Part 1）
```
RX: ID=0x100 DLC=8 DATA=[00 00 00 01 AA AA AA AA]
RX: ID=0x100 DLC=8 DATA=[00 00 00 02 AA AA AA AA]
RX: ID=0x100 DLC=8 DATA=[00 00 00 03 AA AA AA AA]
```

### Node B 序列埠（Part 2）
```
RX: ID=0x030 DLC=1 DATA=[AA] (arbitration winner)
RX: ID=0x050 DLC=1 DATA=[55] (retransmitted)
```

### Node A 序列埠（Part 3）
```
TWAI Error: TEC=8   REC=0 state=ERROR_ACTIVE
TWAI Error: TEC=16  REC=0 state=ERROR_ACTIVE
TWAI Error: TEC=128 REC=0 state=ERROR_PASSIVE
```

---

## 實作步驟

### Step 1：TWAI 暫存器概覽

ESP32 TWAI controller 的 base address 是 `0x3FF6B000`。關鍵暫存器：

```
偏移  暫存器名稱                用途
0x00  TWAI_MODE_REG             工作模式（Normal/Listen-only/Self-test）
0x04  TWAI_CMD_REG              命令（TX request、abort、release buffer）
0x08  TWAI_STATUS_REG           狀態（TBS/RBS/DOS/TSSS/TCS/RS/TS/BS）
0x0C  TWAI_INTR_REG             中斷狀態（RO）
0x10  TWAI_INTR_ENA_REG         中斷使能
0x14  TWAI_BUS_TIMING_0_REG     BRP + SJW
0x18  TWAI_BUS_TIMING_1_REG     TSEG1 + TSEG2 + SAM
0x44  TWAI_TX_ERR_CNT_REG       TEC（Transmit Error Counter）
0x48  TWAI_RX_ERR_CNT_REG       REC（Receive Error Counter）
0x4C  TWAI_ERR_CODE_CAP_REG     錯誤碼捕捉
0x50  TWAI_ARB_LOST_CAP_REG     仲裁丟失捕捉
```

TX buffer（Standard Frame，11-bit ID）：
```
0x54  TWAI_DATA_0_REG  [7:6]=FF [5:4]=DLC [3]=RTR [2:0]=id[10:8]（實際上只有[2:0]）
                         FF=Frame Format (0=Standard, 1=Extended)
0x58  TWAI_DATA_1_REG  id[7:0]
0x5C  TWAI_DATA_2_REG  data[0]
...
0x78  TWAI_DATA_10_REG data[7]
```

### Step 2：TWAI 初始化流程

TWAI 需要先進入 Reset Mode 才能設定 timing：

```
1. 寫 TWAI_MODE_REG = 0x01（Reset Mode）
2. 設定 TWAI_BUS_TIMING_0_REG（BRP + SJW）
3. 設定 TWAI_BUS_TIMING_1_REG（TSEG1 + TSEG2）
4. 設定 acceptance filter（0x60–0x6C，全接受：AMR=0xFFFFFFFF）
5. 清除中斷：讀 TWAI_INTR_REG 一次
6. 離開 Reset Mode：寫 TWAI_MODE_REG = 0x00（Normal Mode）
```

CAN 500kbps @ APB=80MHz 的 timing：
```
BRP = 8  ->  time quanta = 1 / (80MHz / (8+1) / 2) = 225ns（實際計算見 TRM）
TQ 總數 = TSEG1 + TSEG2 + 1（sync segment）= 8 + 3 + 1 = 12
波特率 = 80MHz / 2 / (BRP+1) / TQ = 500kHz  ≈ 500kbps（需微調）
```

ESP32 TRM 第 35 章有完整的 CAN timing 計算表，建議對照 oscilloscope 微調。

### Step 3：發送 Standard Frame

發送前先確認 TWAI_STATUS_REG 的 TBS（Transmit Buffer Status）bit 為 1（buffer 空閒）。

```c
/* 填入 frame */
TWAI_DATA_0 = (0 << 7) | (0 << 6) | (dlc << 4) | (0 << 3) | ((id >> 8) & 0x7);
TWAI_DATA_1 = id & 0xFF;
TWAI_DATA_2 = data[0];
/* ... 填完 dlc 個 data bytes */

/* 發送命令 */
TWAI_CMD_REG = 0x01; /* TR (Transmit Request) */
```

發完後輪詢 TWAI_STATUS_REG 的 TCS（Transmission Complete Status）bit。

### Step 4：接收 frame

當 TWAI_STATUS_REG 的 RBS（Receive Buffer Status）bit = 1，表示有 frame 等著讀：

```c
uint8_t info = TWAI_DATA_0;  /* [2:0] = id[10:8]，[3] = RTR，[6:4] = DLC */
uint8_t id_lo = TWAI_DATA_1; /* id[7:0] */
uint8_t dlc   = (info >> 4) & 0xF;
uint16_t id   = ((uint16_t)(info & 0x7) << 8) | id_lo;
/* 讀 data bytes：TWAI_DATA_2 .. TWAI_DATA_2+dlc-1 */
/* 釋放 RX buffer */
TWAI_CMD_REG = 0x04; /* RRB (Release Receive Buffer) */
```

### Step 5：讀取錯誤計數器

TEC 和 REC 直接讀暫存器：

```c
uint8_t tec = TWAI_TX_ERR_CNT_REG & 0xFF;
uint8_t rec = TWAI_RX_ERR_CNT_REG & 0xFF;

/* Error state 判斷 */
/* TEC > 255 or REC > 255：Bus Off */
/* TEC > 127 or REC > 127：Error Passive */
/* 否則：Error Active */
```

TWAI_ERR_CODE_CAP_REG 的 [4:2] bits 記錄最後一次錯誤的類型：
- 000 = Bit Error
- 001 = Form Error
- 010 = Stuff Error
- 011 = Other Error
- 100 = CRC Error
- 101 = Frame Error
- 110 = ACK Error
- 111 = Bus Error

---

## 參考解答

<details>
<summary>點開參考實作</summary>

```c
/*
 * practice_b_can_arbitration.c
 *
 * 編譯兩份：
 *   NODE_A=1 idf.py build  -> flash 到 Node A
 *   NODE_B=1 idf.py build  -> flash 到 Node B
 *
 * 在 CMakeLists.txt 加：
 *   if(DEFINED ENV{NODE_A})
 *       target_compile_definitions(${COMPONENT_TARGET} PUBLIC NODE_A=1)
 *   elseif(DEFINED ENV{NODE_B})
 *       target_compile_definitions(${COMPONENT_TARGET} PUBLIC NODE_B=1)
 *   endif()
 */

#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"

/* ================================================================
 * TWAI 暫存器定義（ESP32 TRM Rev 3.3，Chapter 35）
 * ================================================================ */

#define TWAI_BASE           0x3FF6B000UL

#define TWAI_MODE_REG       (*(volatile uint32_t *)(TWAI_BASE + 0x00))
#define TWAI_CMD_REG        (*(volatile uint32_t *)(TWAI_BASE + 0x04))
#define TWAI_STATUS_REG     (*(volatile uint32_t *)(TWAI_BASE + 0x08))
#define TWAI_INTR_REG       (*(volatile uint32_t *)(TWAI_BASE + 0x0C))
#define TWAI_INTR_ENA_REG   (*(volatile uint32_t *)(TWAI_BASE + 0x10))
#define TWAI_BUS_TIMING_0   (*(volatile uint32_t *)(TWAI_BASE + 0x14))
#define TWAI_BUS_TIMING_1   (*(volatile uint32_t *)(TWAI_BASE + 0x18))
#define TWAI_ARB_LOST_CAP   (*(volatile uint32_t *)(TWAI_BASE + 0x2C))
#define TWAI_ERR_CODE_CAP   (*(volatile uint32_t *)(TWAI_BASE + 0x30))
/* Acceptance filter（Single Filter Mode） */
#define TWAI_ACR0           (*(volatile uint32_t *)(TWAI_BASE + 0x40))
#define TWAI_ACR1           (*(volatile uint32_t *)(TWAI_BASE + 0x44))
#define TWAI_ACR2           (*(volatile uint32_t *)(TWAI_BASE + 0x48))
#define TWAI_ACR3           (*(volatile uint32_t *)(TWAI_BASE + 0x4C))
#define TWAI_AMR0           (*(volatile uint32_t *)(TWAI_BASE + 0x50))
#define TWAI_AMR1           (*(volatile uint32_t *)(TWAI_BASE + 0x54))
#define TWAI_AMR2           (*(volatile uint32_t *)(TWAI_BASE + 0x58))
#define TWAI_AMR3           (*(volatile uint32_t *)(TWAI_BASE + 0x5C))
#define TWAI_TX_ERR_CNT     (*(volatile uint32_t *)(TWAI_BASE + 0x60))
#define TWAI_RX_ERR_CNT     (*(volatile uint32_t *)(TWAI_BASE + 0x64))
#define TWAI_RX_MSG_CNT     (*(volatile uint32_t *)(TWAI_BASE + 0x68))
/* TX/RX buffer（偏移 0x80–0xA0，Standard Frame layout） */
#define TWAI_BUF(n)         (*(volatile uint32_t *)(TWAI_BASE + 0x80 + (n)*4))

/* STATUS bits */
#define TWAI_STS_BS     (1 << 7) /* Bus Status：1=bus-off */
#define TWAI_STS_ES     (1 << 6) /* Error Status：1=error */
#define TWAI_STS_TS     (1 << 5) /* Transmit Status：1=transmitting */
#define TWAI_STS_RS     (1 << 4) /* Receive Status：1=receiving */
#define TWAI_STS_TCS    (1 << 3) /* TX Complete Status */
#define TWAI_STS_TBS    (1 << 2) /* TX Buffer Status：1=free */
#define TWAI_STS_DOS    (1 << 1) /* Data Overrun Status */
#define TWAI_STS_RBS    (1 << 0) /* RX Buffer Status：1=frame waiting */

/* CMD bits */
#define TWAI_CMD_TR     (1 << 0) /* Transmit Request */
#define TWAI_CMD_AT     (1 << 1) /* Abort Transmission */
#define TWAI_CMD_RRB    (1 << 2) /* Release RX Buffer */
#define TWAI_CMD_CDO    (1 << 3) /* Clear Data Overrun */
#define TWAI_CMD_SRR    (1 << 4) /* Self Reception Request */

/* DPORT 時鐘 */
#define DPORT_PERIP_CLK_EN  (*(volatile uint32_t *)0x3FF0001CUL)
#define DPORT_PERIP_RST_EN  (*(volatile uint32_t *)0x3FF00020UL)
#define DPORT_TWAI_CLK_EN   (1UL << 12)

/* GPIO matrix */
#define GPIO_FUNC_OUT_SEL(n)(*(volatile uint32_t *)(0x3FF44530UL + (n)*4))
#define GPIO_FUNC_IN_SEL(n) (*(volatile uint32_t *)(0x3FF44130UL + (n)*4))
#define GPIO_ENABLE_W1TS    (*(volatile uint32_t *)0x3FF44024UL)
#define GPIO_PIN_REG(n)     (*(volatile uint32_t *)(0x3FF44088UL + (n)*4))

/* TWAI TX signal = 74，TWAI RX signal input = 74 */
#define TWAI_TX_IDX  74
#define TWAI_RX_IDX  74

#define CAN_TX_GPIO  4
#define CAN_RX_GPIO  5

/* ================================================================
 * UART0 輸出（register-level）
 * ================================================================ */
#define UART0_FIFO      (*(volatile uint32_t *)0x3FF40000UL)
#define UART0_STATUS    (*(volatile uint32_t *)0x3FF4001CUL)

static void u_putc(char c)
{
    while (((UART0_STATUS >> 16) & 0xFF) >= 127) {}
    UART0_FIFO = (uint8_t)c;
}
static void u_puts(const char *s) { while (*s) u_putc(*s++); }
static void u_puthex(uint32_t v, int nib)
{
    for (int i = nib - 1; i >= 0; i--) {
        uint8_t d = (v >> (i * 4)) & 0xF;
        u_putc(d < 10 ? '0' + d : 'A' + d - 10);
    }
}
static void u_putuint(uint32_t v, int w)
{
    char buf[12]; int len = 0;
    if (!v) buf[len++] = '0';
    else while (v) { buf[len++] = '0' + v % 10; v /= 10; }
    while (len < w) { u_putc(' '); w--; }
    for (int i = len-1; i >= 0; i--) u_putc(buf[i]);
}

/* ================================================================
 * TWAI 初始化
 * ================================================================ */

typedef enum {
    CAN_STATE_ERROR_ACTIVE,
    CAN_STATE_ERROR_PASSIVE,
    CAN_STATE_BUS_OFF
} can_state_t;

static void twai_gpio_init(void)
{
    /* TX */
    GPIO_FUNC_OUT_SEL(CAN_TX_GPIO) = TWAI_TX_IDX;
    GPIO_ENABLE_W1TS = (1UL << CAN_TX_GPIO);
    /* RX */
    GPIO_FUNC_IN_SEL(TWAI_RX_IDX) = CAN_RX_GPIO;
    /* RX pin input enable（GPIO_PIN_REG 不用動，Input always on） */
}

static void twai_init(void)
{
    DPORT_PERIP_CLK_EN |=  DPORT_TWAI_CLK_EN;
    DPORT_PERIP_RST_EN &= ~DPORT_TWAI_CLK_EN;

    twai_gpio_init();

    /* 進入 Reset Mode 才能寫 timing */
    TWAI_MODE_REG = 0x01;

    /*
     * CAN 500kbps @ APB=80MHz
     *
     * BRP（Baud Rate Prescaler）= 8，時鐘頻率 = 80M / (2*(8+1)) = 4.444 MHz
     * 每個 TQ = 1/4.444M ≈ 225 ns
     * TQ 總數 = 1（Sync）+ TSEG1 + TSEG2 = 1 + 12 + 3 = 16
     * 實際波特率 = 4.444M / 16 ≈ 277.7 kbps
     *
     * 要精確 500kbps：
     *   TQ freq = 500k * 16 TQ = 8 MHz
     *   BRP = 80M / (2*8M) - 1 = 4 (BRP+1=5)
     *   BUS_TIMING_0 = (0 << 6) | 4  (SJW=1, BRP=4)
     *   BUS_TIMING_1 = (0 << 7) | (2 << 4) | 12  (SAM=0, TSEG2=2, TSEG1=12)
     *   -> 500k * (1+12+3) = 8M OK
     */
    TWAI_BUS_TIMING_0 = (0x00 << 6) | 0x04; /* SJW=1TQ, BRP=4 */
    TWAI_BUS_TIMING_1 = (0 << 7) | (2 << 4) | 12; /* SAM=1x, TSEG2=3, TSEG1=12 */

    /* Acceptance filter：接受所有 frame（AMR 全 1 = don't care） */
    TWAI_ACR0 = 0x00; TWAI_ACR1 = 0x00;
    TWAI_ACR2 = 0x00; TWAI_ACR3 = 0x00;
    TWAI_AMR0 = 0xFF; TWAI_AMR1 = 0xFF;
    TWAI_AMR2 = 0xFF; TWAI_AMR3 = 0xFF;

    /* 清中斷，不使能任何中斷（輪詢模式） */
    (void)TWAI_INTR_REG;
    TWAI_INTR_ENA_REG = 0x00;

    /* 離開 Reset Mode，進入 Normal Mode */
    TWAI_MODE_REG = 0x00;
}

/* ================================================================
 * CAN frame 收發
 * ================================================================ */

typedef struct {
    uint32_t id;    /* 11-bit Standard ID */
    uint8_t  dlc;
    uint8_t  data[8];
    bool     rtr;
} can_frame_t;

static bool twai_transmit(const can_frame_t *f, uint32_t timeout_ms)
{
    /* 等 TX buffer 空閒 */
    uint32_t t = timeout_ms;
    while (!(TWAI_STATUS_REG & TWAI_STS_TBS)) {
        if (!t--) return false;
        vTaskDelay(1);
    }

    /*
     * Standard Frame Buffer Layout（TRM 35.4.5）：
     * BUF[0] bit[7:6]=00（Standard）, [5:4]=DLC, [3]=RTR, [2:0]=ID[10:8]
     * BUF[1] = ID[7:0]
     * BUF[2..1+DLC] = data bytes
     */
    TWAI_BUF(0) = ((uint32_t)(f->dlc & 0xF) << 4) |
                  ((uint32_t)(f->rtr ? 1 : 0) << 3) |
                  ((f->id >> 8) & 0x7);
    TWAI_BUF(1) = f->id & 0xFF;
    for (int i = 0; i < f->dlc && i < 8; i++) {
        TWAI_BUF(2 + i) = f->data[i];
    }

    /* 發送 */
    TWAI_CMD_REG = TWAI_CMD_TR;
    return true;
}

/* 等待 TX 完成（TCS=1 或發生錯誤） */
static bool twai_tx_wait(uint32_t timeout_ms)
{
    uint32_t t = timeout_ms;
    while (t--) {
        uint32_t sts = TWAI_STATUS_REG;
        if (sts & TWAI_STS_TCS) return true;  /* 完成 */
        if (sts & TWAI_STS_BS)  return false; /* bus-off */
        vTaskDelay(1);
    }
    return false;
}

static bool twai_receive(can_frame_t *f, uint32_t timeout_ms)
{
    uint32_t t = timeout_ms;
    while (!(TWAI_STATUS_REG & TWAI_STS_RBS)) {
        if (!t--) return false;
        vTaskDelay(1);
    }

    uint32_t b0 = TWAI_BUF(0);
    uint32_t b1 = TWAI_BUF(1);

    f->id  = ((b0 & 0x7) << 8) | (b1 & 0xFF);
    f->dlc = (b0 >> 4) & 0xF;
    f->rtr = (b0 >> 3) & 0x1;
    for (int i = 0; i < f->dlc && i < 8; i++) {
        f->data[i] = (uint8_t)TWAI_BUF(2 + i);
    }

    /* 釋放 RX buffer */
    TWAI_CMD_REG = TWAI_CMD_RRB;
    return true;
}

static can_state_t twai_get_state(void)
{
    uint8_t tec = TWAI_TX_ERR_CNT & 0xFF;
    uint8_t rec = TWAI_RX_ERR_CNT & 0xFF;
    uint32_t sts = TWAI_STATUS_REG;
    if (sts & TWAI_STS_BS) return CAN_STATE_BUS_OFF;
    if (tec > 127 || rec > 127) return CAN_STATE_ERROR_PASSIVE;
    return CAN_STATE_ERROR_ACTIVE;
}

/* ================================================================
 * Part 1：基礎收發 task
 * ================================================================ */

#ifdef NODE_A

static void part1_tx_task(void *arg)
{
    (void)arg;
    uint32_t cnt = 0;
    can_frame_t f = {
        .id  = 0x100,
        .dlc = 8,
        .rtr = false,
    };
    for (;;) {
        cnt++;
        f.data[0] = (cnt >> 24) & 0xFF;
        f.data[1] = (cnt >> 16) & 0xFF;
        f.data[2] = (cnt >>  8) & 0xFF;
        f.data[3] = (cnt)       & 0xFF;
        f.data[4] = f.data[5] = f.data[6] = f.data[7] = 0xAA;

        twai_transmit(&f, 100);
        twai_tx_wait(50);
        vTaskDelay(pdMS_TO_TICKS(500));
    }
}

#endif /* NODE_A */

#ifdef NODE_B

static void part1_rx_task(void *arg)
{
    (void)arg;
    can_frame_t f;
    for (;;) {
        if (twai_receive(&f, 1000)) {
            u_puts("RX: ID=0x");
            u_puthex(f.id, 3);
            u_puts(" DLC=");
            u_putc('0' + f.dlc);
            u_puts(" DATA=[");
            for (int i = 0; i < f.dlc; i++) {
                u_puthex(f.data[i], 2);
                if (i < f.dlc - 1) u_putc(' ');
            }
            u_puts("]\r\n");
        }
    }
}

#endif /* NODE_B */

/* ================================================================
 * Part 2：仲裁測試
 * ================================================================ */

/*
 * 仲裁測試設計：
 * Node A 和 Node B 各自在收到對方的「仲裁開始」信號後立刻觸發 TX。
 * 這裡簡化為：兩個 node 上電後同時等待 3 秒，再同時發送。
 * 實際驗證要用邏輯分析儀看波形，程式端只能觀察誰的 TX 先完成。
 */

#ifdef NODE_A

static void part2_arb_task(void *arg)
{
    (void)arg;
    vTaskDelay(pdMS_TO_TICKS(3000)); /* 同步等待 */

    can_frame_t f_a = { .id=0x050, .dlc=1, .rtr=false, .data={0x55} };

    for (;;) {
        u_puts("Node A: sending ID=0x050\r\n");
        twai_transmit(&f_a, 200);
        bool ok = twai_tx_wait(100);
        if (ok) {
            u_puts("Node A: ID=0x050 TX complete\r\n");
        } else {
            u_puts("Node A: ID=0x050 lost arb or error, retry pending\r\n");
        }
        vTaskDelay(pdMS_TO_TICKS(2000));
    }
}

#endif /* NODE_A */

#ifdef NODE_B

static void part2_arb_rx_task(void *arg)
{
    (void)arg;
    vTaskDelay(pdMS_TO_TICKS(3000)); /* 同步等待 */

    /* Node B 發 ID=0x030，同時接收（優先級高，應該贏仲裁） */
    can_frame_t f_b = { .id=0x030, .dlc=1, .rtr=false, .data={0xAA} };
    can_frame_t rx;

    for (;;) {
        u_puts("Node B: sending ID=0x030\r\n");
        twai_transmit(&f_b, 200);
        bool ok = twai_tx_wait(100);
        if (ok) u_puts("Node B: ID=0x030 TX complete (arbitration winner)\r\n");

        /* 接收 Node A 的 retry */
        if (twai_receive(&rx, 300)) {
            u_puts("Node B RX: ID=0x");
            u_puthex(rx.id, 3);
            u_puts(" DATA=[");
            u_puthex(rx.data[0], 2);
            if (rx.id == 0x050) u_puts("] (retransmitted)\r\n");
            else if (rx.id == 0x030) u_puts("] (arbitration winner)\r\n");
            else u_puts("]\r\n");
        }
        vTaskDelay(pdMS_TO_TICKS(2000));
    }
}

#endif /* NODE_B */

/* ================================================================
 * Part 3：Error Injection（Node A 單獨跑，斷開 Node B 的接收器）
 * ================================================================ */

#ifdef NODE_A

static void part3_error_task(void *arg)
{
    (void)arg;
    /* 等使用者按下 BOOT 鍵（GPIO0）作為觸發，或直接 delay 5 秒 */
    u_puts("Part 3: Error Injection — disconnect Node B CAN, then observe TEC\r\n");
    vTaskDelay(pdMS_TO_TICKS(5000));

    can_frame_t f = { .id=0x200, .dlc=1, .rtr=false, .data={0xFF} };
    int attempt = 0;

    for (;;) {
        attempt++;
        twai_transmit(&f, 200);
        vTaskDelay(pdMS_TO_TICKS(10)); /* 等 ACK timeout */

        uint8_t tec = TWAI_TX_ERR_CNT & 0xFF;
        uint8_t rec = TWAI_RX_ERR_CNT & 0xFF;
        can_state_t st = twai_get_state();

        /* 讀取錯誤碼 */
        uint32_t ecc = TWAI_ERR_CODE_CAP;
        uint8_t err_type = (ecc >> 4) & 0x7; /* bit[6:4] */
        const char *err_str;
        switch (err_type) {
            case 0: err_str = "Bit";   break;
            case 2: err_str = "Stuff"; break;
            case 4: err_str = "CRC";   break;
            case 6: err_str = "ACK";   break;
            default: err_str = "Other";
        }

        u_puts("TX attempt #");
        u_putuint(attempt, 3);
        u_puts(": TEC=");
        u_putuint(tec, 3);
        u_puts(" REC=");
        u_putuint(rec, 1);
        u_puts(" ErrType=");
        u_puts(err_str);
        u_puts(" state=");
        switch (st) {
            case CAN_STATE_ERROR_ACTIVE:  u_puts("ERROR_ACTIVE\r\n");  break;
            case CAN_STATE_ERROR_PASSIVE: u_puts("ERROR_PASSIVE\r\n"); break;
            case CAN_STATE_BUS_OFF:
                u_puts("BUS_OFF\r\n");
                /* Bus Off 後需要 128 個 11-consecutive-recessive-bit 序列才能恢復
                   可以用 software reset 重新初始化 */
                TWAI_MODE_REG = 0x01; /* reset */
                vTaskDelay(pdMS_TO_TICKS(100));
                TWAI_MODE_REG = 0x00; /* 重新進 Normal */
                break;
        }

        vTaskDelay(pdMS_TO_TICKS(200));
    }
}

#endif /* NODE_A */

/* ================================================================
 * app_main
 * ================================================================ */

void app_main(void)
{
    twai_init();

#if defined(NODE_A)
    u_puts("=== CAN Node A ===\r\n");
    /*
     * 選擇要跑哪個 Part：
     * Part 1：xTaskCreate(part1_tx_task, ...)
     * Part 2：xTaskCreate(part2_arb_task, ...)
     * Part 3：xTaskCreate(part3_error_task, ...)
     */
    /* 預設跑 Part 1 */
    xTaskCreate(part1_tx_task, "can_tx", 2048, NULL, 5, NULL);
    /* 取消以下註解切換到 Part 2 或 3 */
    /* xTaskCreate(part2_arb_task,   "can_arb",  2048, NULL, 5, NULL); */
    /* xTaskCreate(part3_error_task, "can_err",  2048, NULL, 5, NULL); */

#elif defined(NODE_B)
    u_puts("=== CAN Node B ===\r\n");
    xTaskCreate(part1_rx_task, "can_rx", 2048, NULL, 5, NULL);
    /* xTaskCreate(part2_arb_rx_task, "can_arb_rx", 2048, NULL, 5, NULL); */

#else
    #error "Define NODE_A or NODE_B"
#endif
}
```

**仲裁觀察說明**

Part 2 的仲裁驗證，純靠序列埠輸出有侷限性：你看到「TX complete」的順序不一定等於 bus 上的仲裁順序，因為兩個 node 的序列埠是獨立的。正確的驗證方式：

1. 用邏輯分析儀抓 CAN_H 的差分波形
2. 解碼後看兩個 frame 的起始時間戳
3. ID=0x030 的 Frame Start of Frame（SOF）應該只出現一次
4. ID=0x050 的 SOF 會出現兩次（第一次仲裁失敗後 retry）

如果沒有邏輯分析儀，可以在 Node A 的 `part2_arb_task` 裡，傳送完 TX request 後立刻讀 `TWAI_ARB_LOST_CAP_REG`：如果 bit[4] 有效（ARB_LOST_INT 產生過），表示 Node A 確實在仲裁中輸了。

**TEC 計數器行為說明**

- 每次 ACK Error：TEC += 8
- 每次成功 TX：TEC -= 1（最小為 0）
- TEC > 127：進入 Error Passive，TX Error Flag 從 Active Error Flag（6 dominant bits）改為 Passive Error Flag（6 recessive bits），對 bus 影響較小
- TEC > 255（或 = 256）：進入 Bus Off，controller 停止參與 bus 活動

在 Part 3 的實驗中，Node B 斷線後 Node A 每次 TX 都會因為 ACK slot 沒人回應而觸發 ACK Error，TEC 以 +8 遞增，約 16 次後（TEC=128）進入 Error Passive。

</details>

---

## 測試用例

### TC-01：Part 1 正常收發

- 兩顆 ESP32 都 flash，Node A 燒 `NODE_A=1` 版本，Node B 燒 `NODE_B=1` 版本
- 用兩個序列埠監視器同時觀察
- 預期：Node B 每 500ms 收到一行，tick count 從 1 開始遞增

### TC-02：CAN 總線斷開（無終端電阻）

- 拆掉其中一顆 120Ω 終端電阻
- 預期：高速 CAN（500kbps）在無終端電阻時反射信號嚴重，會看到偶發的 CRC Error 或 Stuff Error，Node B 有時收不到 frame

### TC-03：Part 2 仲裁確認（需要邏輯分析儀）

- 設定好仲裁測試版本，同時 flash 兩個 node
- 邏輯分析儀觸發條件：CAN SOF（CAN_H 出現 dominant 電位）
- 預期：兩個 SOF 幾乎同時，但最終只有一個 frame 完整傳輸（ID=0x030），另一個在仲裁 bit 輸掉後自動重試

### TC-04：Part 3 TEC 遞增

- Node A 燒 Part 3 版本，Node B 的 CAN 接收器斷線
- 預期：TEC 每次 +8，16 次後印出 `state=ERROR_PASSIVE`

---

## 自我檢核

1. CAN 仲裁是 bitwise OR 競爭：dominant bit（0）勝出 recessive bit（1）。ID=0x030（0b00000110000）和 ID=0x050（0b01010000000）在哪一個 bit 分出勝負？
2. Error Passive 狀態下，Node 還可以發送 frame 嗎？和 Bus Off 的差別在哪？
3. TEC 計數器何時會往下降？如果 Node A 傳了一個成功的 frame 後，TEC 從 128 降到多少？
4. `TWAI_CMD_REG = TWAI_CMD_RRB` 這個操作如果忘記執行，RX FIFO 會怎樣？
5. Single Filter Mode 和 Dual Filter Mode 的差別是什麼？本練習用的是哪一種？

---

→ [練習 C：BLE + LoRa 橋接](./practice-c-ble-lora-bridge.md)
