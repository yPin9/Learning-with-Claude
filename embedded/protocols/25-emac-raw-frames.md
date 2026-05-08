# Ch 25 — 實作：ESP32 EMAC Register-Level

> 目標：直接操作 ESP32 EMAC 暫存器，設定 DMA descriptor ring，初始化 LAN8720 PHY，發送和接收 raw Ethernet frame，最後用 Wireshark 在區網上抓到 ESP32 發出的 ARP request。

---

## EMAC 控制器架構

ESP32 內建的 EMAC（Ethernet MAC）基於 DesignWare Ethernet QoS（DWC_ETHER_QOS），暫存器基底位址 `0x3FF69000`（`DR_REG_EMAC_BASE`）。

```
+------------------------------------------+
|              ESP32 EMAC                  |
|                                          |
|  +------------------+  +--------------+ |
|  |   DMA Engine     |  |   MAC Core   | |
|  |  TX DMA ring     |  |  FCS 計算    | |
|  |  RX DMA ring     |  |  Flow ctrl   | |
|  |  AHB Bus Master  |  |  RMII 介面   | |
|  +--------+---------+  +------+-------+ |
|           |                   |          |
+-----------|-------------------|-----------+
            |                   |
       系統 SRAM           RMII 腳位
    （DMA descriptor）    （GPIO0,18~27）
```

DMA engine 直接從 SRAM 讀取 TX frame、寫入 RX frame，MAC core 處理 frame 格式和實體介面。軟體只需要維護 DMA descriptor ring，不需要逐 byte 操作。

---

## DMA Descriptor 結構

這是 EMAC 驅動的核心資料結構，硬體用 linked list 管理 TX 和 RX buffer：

```c
/* EMAC DMA Descriptor（每個 16 bytes） */
typedef struct emac_dma_desc {
    volatile uint32_t Status;               /* 狀態和控制 bits */
    volatile uint32_t ControlBufferSize;    /* 緩衝區大小控制 */
    volatile uint32_t Buffer1Addr;          /* 第一個 buffer 的位址 */
    volatile uint32_t Buffer2NextDescAddr;  /* 第二個 buffer 或下一個 descriptor 的位址 */
} __attribute__((aligned(4))) emac_dma_desc_t;

/* Status 欄位關鍵 bit（TX descriptor）：
 *   bit31 = OWN：1=DMA 擁有，0=CPU 擁有
 *   bit29 = IC：Interrupt on Completion（TX 完成時觸發中斷）
 *   bit28 = LS：Last Segment（payload 最後一個 descriptor）
 *   bit27 = FS：First Segment（payload 第一個 descriptor）
 *   bit26 = DC：Disable CRC（不用，讓 MAC 自動加）
 *   bit20 = TER：TX End of Ring（鏈表最後一個，下一個回第一個）
 */

/* Status 欄位關鍵 bit（RX descriptor）：
 *   bit31 = OWN：1=DMA 擁有，0=CPU 可讀
 *   bit8  = LS：Last Descriptor（frame 的最後一個）
 *   bit9  = FS：First Descriptor
 *   bit15 = ES：Error Summary（任何錯誤）
 *   bit3  = RE：Receive Error
 *   bit0  = EXC = Excessive Collision（TX descriptor 的碰撞錯誤）
 */

/* ControlBufferSize 欄位（RX descriptor）：
 *   bit14 = RER：RX End of Ring
 *   bit10:0 = RBS1：Buffer1 大小（bytes）
 */
```

---

## 重要暫存器

以下 offset 相對於 `DR_REG_EMAC_BASE`（`0x3FF69000`）：

| 暫存器名稱 | Offset | 說明 |
|-----------|--------|------|
| EMAC_DMABUSMODE_REG | 0x1000 | DMA bus mode：bit0=soft reset，bit16=混合burst |
| EMAC_DMATXPOLLDEMAND_REG | 0x1004 | 寫任意值：通知 DMA 有新 TX descriptor |
| EMAC_DMARXPOLLDEMAND_REG | 0x1008 | 寫任意值：通知 DMA 繼續 RX |
| EMAC_DMARXBASEADDR_REG | 0x100C | RX descriptor list 起始位址 |
| EMAC_DMATXBASEADDR_REG | 0x1010 | TX descriptor list 起始位址 |
| EMAC_DMASTATUS_REG | 0x1014 | DMA 中斷狀態（寫 1 清除） |
| EMAC_DMAOPERATION_MODE_REG | 0x1018 | 啟動 TX/RX DMA（bit13=ST，bit1=SR） |
| EMAC_DMAIN_EN_REG | 0x101C | DMA 中斷使能 |
| EMAC_GMACCONFIG_REG | 0x0000 | MAC 設定：Duplex(bit11)、Speed(bit14)、CRC offload(bit10) |
| EMAC_GMACFF_REG | 0x0004 | Frame filter 設定（接受廣播、混雜模式等） |
| EMAC_GMACGMIIADDR_REG | 0x0010 | MDIO 位址暫存器（PHY 位址、暫存器位址、讀/寫、Busy） |
| EMAC_GMACGMIIDATA_REG | 0x0014 | MDIO 資料暫存器 |
| EMAC_GMACFC_REG | 0x0018 | Flow control（Pause frame） |
| EMAC_GMACADDR0HIGH_REG | 0x0040 | MAC 位址高 16 bits（bytes 4, 5） |
| EMAC_GMACADDR0LOW_REG | 0x0044 | MAC 位址低 32 bits（bytes 0~3） |

---

## MDIO 讀寫函式

所有 PHY 操作都透過 MDIO：

```c
#include <stdint.h>
#include "soc/emac_reg.h"  /* ESP-IDF 提供的暫存器定義 */

/* GMACGMIIADDR 欄位 */
#define MDIO_BUSY       (1 << 0)
#define MDIO_WRITE      (1 << 1)    /* 0=Read, 1=Write */
#define MDIO_CLK_DIV    (4 << 2)    /* CSR clock range：APB 160MHz → MDC ~2.5MHz */
#define MDIO_REGADDR(r) ((r) << 6)
#define MDIO_PHYADDR(p) ((p) << 11)

#define EMAC_BASE       0x3FF69000UL
#define REG(off)        (*((volatile uint32_t *)(EMAC_BASE + (off))))

static void mdio_write(uint8_t phy_addr, uint8_t reg_addr, uint16_t data) {
    REG(0x0014) = data;   /* GMACGMIIDATA */
    REG(0x0010) = MDIO_PHYADDR(phy_addr) |
                  MDIO_REGADDR(reg_addr) |
                  MDIO_CLK_DIV |
                  MDIO_WRITE |
                  MDIO_BUSY;
    /* 等待 BUSY 清除（通常 < 10us） */
    while (REG(0x0010) & MDIO_BUSY) {}
}

static uint16_t mdio_read(uint8_t phy_addr, uint8_t reg_addr) {
    REG(0x0010) = MDIO_PHYADDR(phy_addr) |
                  MDIO_REGADDR(reg_addr) |
                  MDIO_CLK_DIV |
                  MDIO_BUSY;   /* 不設 WRITE bit = 讀 */
    while (REG(0x0010) & MDIO_BUSY) {}
    return (uint16_t)(REG(0x0014) & 0xFFFF);
}
```

---

## LAN8720 初始化

```c
#define PHY_ADDR        1       /* LAN8720 PHYAD0 腳決定，通常 0 或 1 */
#define PHY_REG_BMCR    0x00    /* Basic Mode Control Register */
#define PHY_REG_BMSR    0x01    /* Basic Mode Status Register */
#define PHY_REG_PHYID1  0x02
#define PHY_REG_PHYID2  0x03

/* BMCR bit */
#define BMCR_RESET          (1 << 15)
#define BMCR_AUTONEG_ENABLE (1 << 12)
#define BMCR_AUTONEG_RESTART (1 << 9)

/* BMSR bit */
#define BMSR_LINK_STATUS    (1 << 2)
#define BMSR_AUTONEG_DONE   (1 << 5)

static int lan8720_init(void) {
    /* 1. 確認 PHY ID */
    uint16_t id1 = mdio_read(PHY_ADDR, PHY_REG_PHYID1);
    uint16_t id2 = mdio_read(PHY_ADDR, PHY_REG_PHYID2);
    if (id1 != 0x0007 || (id2 & 0xFFF0) != 0xC0F0) {
        /* LAN8720 ID：0x0007C0F? */
        return -1;  /* PHY 不存在或接線錯誤 */
    }

    /* 2. Soft reset */
    mdio_write(PHY_ADDR, PHY_REG_BMCR, BMCR_RESET);
    uint32_t timeout = 1000;
    while ((mdio_read(PHY_ADDR, PHY_REG_BMCR) & BMCR_RESET) && timeout--) {
        vTaskDelay(pdMS_TO_TICKS(1));
    }
    if (!timeout) return -2;

    /* 3. 啟用 auto-negotiation 並重新觸發 */
    mdio_write(PHY_ADDR, PHY_REG_BMCR,
               BMCR_AUTONEG_ENABLE | BMCR_AUTONEG_RESTART);

    /* 4. 等待 link up（最多 5 秒） */
    timeout = 5000;
    while (timeout--) {
        uint16_t bmsr = mdio_read(PHY_ADDR, PHY_REG_BMSR);
        if ((bmsr & BMSR_LINK_STATUS) && (bmsr & BMSR_AUTONEG_DONE)) {
            return 0;  /* Link up */
        }
        vTaskDelay(pdMS_TO_TICKS(1));
    }
    return -3;  /* Link up timeout */
}
```

---

## EMAC 初始化

```c
#define NUM_TX_DESC     4
#define NUM_RX_DESC     4
#define FRAME_BUF_SIZE  1600    /* 大於 MTU 1500 + 14 (header) + 4 (FCS) */

/* DMA Descriptor 和 buffer 必須在能被 DMA 存取的 SRAM */
static emac_dma_desc_t tx_desc[NUM_TX_DESC]  __attribute__((aligned(4)));
static emac_dma_desc_t rx_desc[NUM_RX_DESC]  __attribute__((aligned(4)));
static uint8_t tx_buf[NUM_TX_DESC][FRAME_BUF_SIZE] __attribute__((aligned(4)));
static uint8_t rx_buf[NUM_RX_DESC][FRAME_BUF_SIZE] __attribute__((aligned(4)));

static void emac_init_descriptors(void) {
    /* 初始化 TX descriptor ring */
    for (int i = 0; i < NUM_TX_DESC; i++) {
        tx_desc[i].Status = 0;   /* OWN=0，CPU 擁有 */
        tx_desc[i].ControlBufferSize = 0;
        tx_desc[i].Buffer1Addr = (uint32_t)tx_buf[i];
        /* 鏈表：最後一個指回第一個（TER bit 也可以） */
        tx_desc[i].Buffer2NextDescAddr = (uint32_t)&tx_desc[(i + 1) % NUM_TX_DESC];
    }
    /* 最後一個設 TER（TX End of Ring） */
    tx_desc[NUM_TX_DESC - 1].Status |= (1 << 21);  /* TER bit */

    /* 初始化 RX descriptor ring */
    for (int i = 0; i < NUM_RX_DESC; i++) {
        rx_desc[i].Status = (1u << 31);  /* OWN=1，DMA 擁有，可以填充 */
        rx_desc[i].ControlBufferSize = FRAME_BUF_SIZE & 0x7FF;  /* RBS1 */
        rx_desc[i].Buffer1Addr = (uint32_t)rx_buf[i];
        rx_desc[i].Buffer2NextDescAddr = (uint32_t)&rx_desc[(i + 1) % NUM_RX_DESC];
    }
    rx_desc[NUM_RX_DESC - 1].ControlBufferSize |= (1 << 15); /* RER bit */
}

static int emac_init(const uint8_t mac_addr[6]) {
    /* 1. Soft reset DMA */
    REG(0x1000) = 1;   /* DMABUSMODE：bit0=SWR */
    uint32_t timeout = 1000;
    while ((REG(0x1000) & 1) && timeout--) { vTaskDelay(pdMS_TO_TICKS(1)); }
    if (!timeout) return -1;

    /* 2. 設定 MAC 位址 */
    REG(0x0044) = (mac_addr[3] << 24) | (mac_addr[2] << 16) |
                  (mac_addr[1] << 8)  | (mac_addr[0]);
    REG(0x0040) = (1u << 31) |           /* Address Enable */
                  (mac_addr[5] << 8)  |
                  (mac_addr[4]);

    /* 3. MAC 設定：full duplex、100Mbps、checksum offload on */
    REG(0x0000) = (1 << 11) |   /* DM：Full Duplex */
                  (1 << 14) |   /* FES：Fast Ethernet Speed（100Mbps） */
                  (1 << 10) |   /* IPC：IP checksum offload */
                  (1 << 3)  |   /* TE：Transmitter Enable */
                  (1 << 2);     /* RE：Receiver Enable */

    /* 4. Frame filter：接受廣播、接受 unicast（預設） */
    REG(0x0004) = 0;   /* 預設：只收給自己的和廣播 */

    /* 5. 初始化 DMA descriptor */
    emac_init_descriptors();

    /* 6. 設定 DMA descriptor 基底位址 */
    REG(0x100C) = (uint32_t)rx_desc;   /* DMARXBASEADDR */
    REG(0x1010) = (uint32_t)tx_desc;   /* DMATXBASEADDR */

    /* 7. DMA bus mode：增強型 descriptor，4-beat burst */
    REG(0x1000) = (1 << 7) | (4 << 8);  /* DE=1, PBL=4 */

    /* 8. 啟動 TX 和 RX DMA */
    REG(0x1018) = (1 << 13) |   /* ST：Start/Stop TX DMA */
                  (1 << 1);     /* SR：Start/Stop RX DMA */

    return 0;
}
```

---

## 發送 Raw Ethernet Frame

```
TX 流程：

找到 OWN=0 的 TX descriptor
  |
  v
把 frame 資料複製到 tx_buf[i]
  |
  v
設定 descriptor：Buffer1Addr、size、FS=1、LS=1
  |
  v
設定 OWN=1（交給 DMA）
  |
  v
寫 DMATXPOLLDEMAND 通知 DMA
  |
  v
等待 OWN 回到 0（DMA 完成）
```

```c
/* TX descriptor 的 Status bits */
#define TX_OWN  (1u << 31)
#define TX_IC   (1u << 30)   /* Interrupt on Completion */
#define TX_LS   (1u << 29)   /* Last Segment */
#define TX_FS   (1u << 28)   /* First Segment */
#define TX_TER  (1u << 21)   /* TX End of Ring */

static int tx_curr = 0;     /* 目前使用的 TX descriptor index */

int emac_send_frame(const uint8_t *frame, uint16_t len) {
    if (len > FRAME_BUF_SIZE) return -1;

    /* 等待目前 descriptor 不在 DMA 手上 */
    uint32_t timeout = 1000;
    while ((tx_desc[tx_curr].Status & TX_OWN) && timeout--) {
        vTaskDelay(pdMS_TO_TICKS(1));
    }
    if (!timeout) return -2;  /* TX busy timeout */

    /* 複製 frame 資料 */
    memcpy(tx_buf[tx_curr], frame, len);

    /* 設定 descriptor（保留 TER bit 不動） */
    uint32_t ter = tx_desc[tx_curr].Status & TX_TER;
    tx_desc[tx_curr].ControlBufferSize = len & 0x7FF;  /* TBS1 */
    tx_desc[tx_curr].Buffer1Addr = (uint32_t)tx_buf[tx_curr];
    /* 設 OWN=1、FS=1、LS=1 交給 DMA */
    tx_desc[tx_curr].Status = TX_OWN | TX_IC | TX_LS | TX_FS | ter;

    /* 通知 DMA */
    REG(0x1004) = 1;   /* DMATXPOLLDEMAND：任意值啟動 */

    tx_curr = (tx_curr + 1) % NUM_TX_DESC;
    return 0;
}
```

---

## 接收 Raw Ethernet Frame

```c
static int rx_curr = 0;     /* 目前 RX descriptor index */

/* RX descriptor Status bits */
#define RX_OWN  (1u << 31)
#define RX_ES   (1u << 15)  /* Error Summary */
#define RX_FS   (1u << 9)   /* First Descriptor */
#define RX_LS   (1u << 8)   /* Last Descriptor */
#define RX_FL_SHIFT 16      /* Frame Length，bits [29:16] */

int emac_recv_frame(uint8_t *buf, uint16_t *out_len) {
    /* 檢查 OWN：0 表示 DMA 已填好資料，CPU 可以讀 */
    if (rx_desc[rx_curr].Status & RX_OWN) {
        return -1;  /* 沒有新封包 */
    }

    uint32_t status = rx_desc[rx_curr].Status;

    if (status & RX_ES) {
        /* 有錯誤，釋放 descriptor 繼續 */
        rx_desc[rx_curr].Status = RX_OWN;
        REG(0x1008) = 1;   /* DMARXPOLLDEMAND */
        rx_curr = (rx_curr + 1) % NUM_RX_DESC;
        return -2;
    }

    /* 取得 frame 長度（含 FCS 4 bytes） */
    uint16_t frame_len = (status >> RX_FL_SHIFT) & 0x3FFF;
    /* 減掉 FCS */
    if (frame_len > 4) frame_len -= 4;

    if (frame_len > FRAME_BUF_SIZE) frame_len = FRAME_BUF_SIZE;

    memcpy(buf, rx_buf[rx_curr], frame_len);
    *out_len = frame_len;

    /* 釋放 descriptor：設 OWN=1 還給 DMA */
    rx_desc[rx_curr].Status = RX_OWN;
    REG(0x1008) = 1;

    rx_curr = (rx_curr + 1) % NUM_RX_DESC;
    return 0;
}
```

---

## 完整測試：發 ARP Request

組裝一個 ARP request，讓 Wireshark 在區網上看到：

```c
/* ARP Request frame 格式：14 bytes Ethernet header + 28 bytes ARP */
static void build_arp_request(uint8_t *frame,
                               const uint8_t src_mac[6],
                               uint32_t src_ip,
                               uint32_t target_ip) {
    /* Ethernet Header */
    memset(frame, 0xFF, 6);                /* Dest MAC：廣播 */
    memcpy(frame + 6, src_mac, 6);          /* Src MAC */
    frame[12] = 0x08; frame[13] = 0x06;    /* EtherType：ARP */

    /* ARP Payload（28 bytes） */
    uint8_t *arp = frame + 14;
    arp[0] = 0x00; arp[1] = 0x01;          /* Hardware type：Ethernet */
    arp[2] = 0x08; arp[3] = 0x00;          /* Protocol type：IPv4 */
    arp[4] = 0x06;                          /* HW addr len */
    arp[5] = 0x04;                          /* Protocol addr len */
    arp[6] = 0x00; arp[7] = 0x01;          /* Operation：Request */
    memcpy(arp + 8, src_mac, 6);            /* Sender MAC */
    arp[14] = (src_ip >> 24) & 0xFF;       /* Sender IP */
    arp[15] = (src_ip >> 16) & 0xFF;
    arp[16] = (src_ip >>  8) & 0xFF;
    arp[17] =  src_ip        & 0xFF;
    memset(arp + 18, 0, 6);                /* Target MAC：unknown */
    arp[24] = (target_ip >> 24) & 0xFF;    /* Target IP */
    arp[25] = (target_ip >> 16) & 0xFF;
    arp[26] = (target_ip >>  8) & 0xFF;
    arp[27] =  target_ip        & 0xFF;
}

void app_main(void) {
    const uint8_t my_mac[6] = {0xDE, 0xAD, 0xBE, 0xEF, 0x00, 0x01};
    uint32_t my_ip     = 0xC0A80164;  /* 192.168.1.100 */
    uint32_t target_ip = 0xC0A80101;  /* 192.168.1.1 */

    /* 初始化 RMII clock、GPIO、MDIO */
    /* ...（硬體 GPIO matrix 設定，篇幅略，參考 ESP-IDF emac 範例） */

    if (lan8720_init() != 0) {
        ESP_LOGE("EMAC", "PHY init failed");
        return;
    }

    if (emac_init(my_mac) != 0) {
        ESP_LOGE("EMAC", "EMAC init failed");
        return;
    }

    uint8_t frame[42];  /* 14 header + 28 ARP */
    build_arp_request(frame, my_mac, my_ip, target_ip);

    while (1) {
        int ret = emac_send_frame(frame, sizeof(frame));
        if (ret == 0) {
            ESP_LOGI("EMAC", "ARP Request sent: who has 192.168.1.1?");
        }
        vTaskDelay(pdMS_TO_TICKS(3000));  /* 每 3 秒發一次 */
    }
}
```

Wireshark 過濾器用 `arp`，應看到：

```
No.  Time    Source            Destination  Protocol  Length  Info
1    0.000   de:ad:be:ef:00:01 Broadcast    ARP       42      Who has 192.168.1.1? Tell 192.168.1.100
4    3.001   de:ad:be:ef:00:01 Broadcast    ARP       42      Who has 192.168.1.1? Tell 192.168.1.100
```

---

## 常見問題

| 現象 | 可能原因 | 排查方式 |
|------|---------|---------|
| PHY ID 讀到 0xFFFF | MDIO/MDC 接線錯誤或 PHY 沒上電 | 確認 3.3V 供電，用示波器看 MDC 時脈 |
| Link 永遠沒有 up | REF_CLK 沒有 50MHz 輸出 | 用示波器量 GPIO0，應有 50MHz 方波 |
| TX 發出去 Wireshark 抓不到 | TX 未啟動（DMAOPERATION_MODE ST bit 未設） | 確認 REG(0x1018) 有 bit13 |
| RX descriptor OWN 不回 0 | RX DMA 未啟動或 descriptor 設定錯誤 | 確認 SR bit，確認 RX descriptor OWN 初始為 1 |
| Frame 長度異常 | FRAME_BUF_SIZE 太小 | 設成 1600 bytes |

---

## 自我檢核

- [ ] 能說明 DMA descriptor 的 OWN bit 機制（CPU 和 DMA 如何交接 buffer）
- [ ] 知道 TX descriptor 的 FS/LS bit 代表什麼，為什麼 single-frame 兩個都要設
- [ ] 能說明 TER bit 的作用，沒有 TER 鏈表會怎樣
- [ ] 能解釋 MDIO_BUSY polling 的作用
- [ ] 知道 DMATXPOLLDEMAND 和 DMARXPOLLDEMAND 各在什麼時候要寫
- [ ] 能手組一個 28-byte ARP payload（各欄位填什麼）
- [ ] 知道 REF_CLK 接在 GPIO0 有什麼開機時序問題

九種協議都覆蓋了——SPI、I2C、UART、RS-485、Modbus、CAN、BLE、LoRa/Zigbee、USB、Ethernet。最終章把學過的協議整合進一個實際的工業感測器閘道器，把所有技能串在一起。

→ [Final Project：工業感測器閘道器](./final-project-industrial-gateway.md)
