# Ch 23 — 實作：ESP32-S3 USB CDC

> 目標：了解 ESP32-S3 USB OTG 控制器的暫存器層次、為什麼 USB 實作用 TinyUSB 而非手刻，並用 TinyUSB 完成一個雙向 CDC 序列埠，Host 連上後可互相收發資料。

---

## ESP32-S3 USB OTG 控制器

ESP32-S3 內建的 USB OTG 控制器是 Synopsys DWC_OTG（DesignWare Cores USB 2.0 OTG），Full Speed（12 Mbps），支援 Device 和 Host 模式，暫存器基底位址 `0x60080000`（`DR_REG_USB_BASE`）。

### 控制器架構

```
USB D+/D- 差分線
       |
       v
  FS Transceiver（PHY）
       |
       v
+-------------------+
|   DWC_OTG Core    |
|                   |
|  Global Regs      |  <-- 全局設定（GOTGCTL, GAHBCFG, GUSBCFG）
|  Device Regs      |  <-- Device 模式設定（DCFG, DCTL, DSTS）
|  IN EP Regs       |  <-- TX Endpoint 控制（DIEPCTL0..n）
|  OUT EP Regs      |  <-- RX Endpoint 控制（DOEPCTL0..n）
|  FIFO Memory      |  <-- TX/RX FIFO（4KB 共享）
+-------------------+
       |
       v
    DMA / CPU（ESP32-S3 無 DMA，用 FIFO 直接 CPU 讀寫）
```

### 重要暫存器概覽

以下暫存器 offset 相對於 `DR_REG_USB_BASE`（0x60080000）：

| 暫存器 | Offset | 說明 |
|--------|--------|------|
| GOTGCTL | 0x0000 | OTG 控制與狀態（Host/Device 偵測） |
| GAHBCFG | 0x0008 | AHB 設定：Global interrupt enable，DMA 模式 |
| GUSBCFG | 0x000C | USB 設定：PHY 選擇、turnaround time |
| GINTSTS | 0x0014 | 全局中斷狀態（清除需寫 1） |
| GINTMSK | 0x0018 | 全局中斷遮罩 |
| GRXSTSP | 0x0020 | RX status pop（讀取 FIFO 狀態） |
| GRXFSIZ | 0x0024 | RX FIFO 大小 |
| DIEPTXF0 | 0x0028 | EP0 TX FIFO 大小和起始位址 |
| DIEPTXFn | 0x0104~| EP1..n TX FIFO 大小（n=1..3） |
| DCFG | 0x0800 | Device 設定：速度、FS 位址 |
| DCTL | 0x0804 | Device 控制：soft disconnect、remote wakeup |
| DSTS | 0x0808 | Device 狀態：連接速度、frame 號 |
| DIEPMSK | 0x0810 | IN endpoint 中斷遮罩 |
| DOEPMSK | 0x0814 | OUT endpoint 中斷遮罩 |
| DAINT | 0x0818 | 所有 endpoint 中斷狀態 |
| DIEPCTL0 | 0x0900 | IN EP0 控制：MPS、EP enable/disable |
| DOEPCTL0 | 0x0B00 | OUT EP0 控制 |
| DIEPCTLn | 0x0920~| IN EP1..n 控制（每個 0x20 間隔） |
| DOEPCTLn | 0x0B20~| OUT EP1..n 控制 |
| DTXFSTS0 | 0x0918 | EP0 TX FIFO 剩餘空間（word 數） |

FIFO 讀寫方式：TX FIFO 寫入 `0x60080000 + 0x1000 * (ep_num + 1)`，RX FIFO 讀取 `0x60080000 + 0x1000`。

---

## 為什麼用 TinyUSB 而不手刻

USB Device stack 的最小可用實作需要：

1. **FIFO 分配**：ESP32-S3 有 4KB 共享 FIFO，RX FIFO 和每個 EP 的 TX FIFO 大小要精確分配，分配錯了整個傳輸就卡住。
2. **EP0 State Machine**：Setup/Data/Status 三個 stage，每個 stage 有各自的 FIFO 和 ACK 邏輯，加上 ZLP（Zero Length Packet，某些 Control Transfer 的結束標誌）處理。
3. **11 種 Standard Device Request**：Get_Descriptor（含 Device/Config/String/Interface/Endpoint 各子類型）、Set_Address、Set_Configuration、Get/Set_Interface、Get/Set_Feature、Synch_Frame。每一種回應格式不同。
4. **CDC Class Request**：SET_LINE_CODING（設定鮑率/停止位/校驗）、GET_LINE_CODING、SET_CONTROL_LINE_STATE（RTS/DTR）。
5. **Buffer 管理**：Bulk OUT 的 FIFO 讀取、Bulk IN 的分包傳輸（超過 64 bytes 的 payload 要分多個 packet）。
6. **Host 相容性**：Windows 的 USB Host 在某些請求的處理上和 USB spec 有細節差異，需要特別處理。

TinyUSB 把以上全部封裝好，在 ESP32-S2/S3 上有官方維護的 port（`tinyusb_driver`）。工業界的嵌入式 USB 幾乎沒有人從暫存器手刻——這不是偷懶，是工程判斷。

---

## ESP-IDF 中的 TinyUSB

ESP-IDF 提供 `tinyusb` component，直接在 `idf.py menuconfig` 啟用：

```
Component config → TinyUSB Stack
  [*] Enable TinyUSB Stack
  [*] CDC
```

### tusb_config.h

在 `main/` 目錄建立 `tusb_config.h`：

```c
#ifndef TUSB_CONFIG_H
#define TUSB_CONFIG_H

/* MCU 和 OS */
#define CFG_TUSB_MCU     OPT_MCU_ESP32S3
#define CFG_TUSB_OS      OPT_OS_FREERTOS
#define CFG_TUSB_RHPORT0_MODE  OPT_MODE_DEVICE | OPT_MODE_FULL_SPEED

/* 開啟 CDC */
#define CFG_TUD_CDC      1
#define CFG_TUD_CDC_RX_BUFSIZE   512
#define CFG_TUD_CDC_TX_BUFSIZE   512

/* 其他類別關閉 */
#define CFG_TUD_MSC      0
#define CFG_TUD_HID      0
#define CFG_TUD_MIDI     0
#define CFG_TUD_VENDOR   0

#endif /* TUSB_CONFIG_H */
```

### USB Descriptor 定義

這是最繁瑣的部分，但 TinyUSB 提供 macro 讓它可讀：

```c
#include "tusb.h"

/* Device Descriptor */
static const tusb_desc_device_t desc_device = {
    .bLength            = sizeof(tusb_desc_device_t),
    .bDescriptorType    = TUSB_DESC_DEVICE,
    .bcdUSB             = 0x0200,       /* USB 2.0 */
    .bDeviceClass       = TUSB_CLASS_MISC,
    .bDeviceSubClass    = MISC_SUBCLASS_COMMON,
    .bDeviceProtocol    = MISC_PROTOCOL_IAD,  /* IAD = Interface Association Descriptor */
    .bMaxPacketSize0    = CFG_TUD_ENDPOINT0_SIZE,   /* EP0 = 64 bytes */
    .idVendor           = 0x303A,       /* Espressif VID（測試用） */
    .idProduct          = 0x4001,
    .bcdDevice          = 0x0100,
    .iManufacturer      = 0x01,
    .iProduct           = 0x02,
    .iSerialNumber      = 0x03,
    .bNumConfigurations = 0x01,
};

/* Descriptor 回調（TinyUSB 呼叫） */
uint8_t const *tud_descriptor_device_cb(void) {
    return (uint8_t const *)&desc_device;
}

/* Configuration Descriptor：CDC 需要 IAD + 2 個 Interface */
#define CONFIG_TOTAL_LEN  (TUD_CONFIG_DESC_LEN + TUD_CDC_DESC_LEN)
#define EPNUM_CDC_NOTIF   0x81   /* IN, Interrupt, EP1 */
#define EPNUM_CDC_OUT     0x02   /* OUT, Bulk, EP2 */
#define EPNUM_CDC_IN      0x82   /* IN,  Bulk, EP2 */

static uint8_t const desc_config[] = {
    /* Configuration Descriptor */
    TUD_CONFIG_DESCRIPTOR(1,    /* config number */
                          2,    /* interface count */
                          0,    /* string index */
                          CONFIG_TOTAL_LEN,
                          TUSB_DESC_CONFIG_ATT_REMOTE_WAKEUP,
                          100), /* mA */

    /* CDC Interface（含 IAD、Control Interface、Data Interface） */
    TUD_CDC_DESCRIPTOR(0,               /* Interface number: CDC Control = 0 */
                       4,               /* string index */
                       EPNUM_CDC_NOTIF, /* Notification EP（Interrupt IN） */
                       8,               /* Notification EP Max Packet Size */
                       EPNUM_CDC_OUT,   /* Data OUT EP */
                       EPNUM_CDC_IN,    /* Data IN EP */
                       64),             /* Data EP Max Packet Size */
};

uint8_t const *tud_descriptor_configuration_cb(uint8_t index) {
    (void)index;
    return desc_config;
}

/* String Descriptor */
static char const *string_desc_arr[] = {
    (const char[]){0x09, 0x04},  /* 0: 語言 ID（英文） */
    "Espressif Systems",          /* 1: Manufacturer */
    "ESP32-S3 CDC Demo",          /* 2: Product */
    "123456",                     /* 3: Serial Number */
    "TinyUSB CDC",                /* 4: CDC Interface */
};

uint16_t const *tud_descriptor_string_cb(uint8_t index, uint16_t langid) {
    static uint16_t desc_str[32];
    uint8_t chr_count;

    if (index == 0) {
        memcpy(&desc_str[1], string_desc_arr[0], 2);
        chr_count = 1;
    } else {
        const char *str = string_desc_arr[index];
        chr_count = strlen(str);
        if (chr_count > 31) chr_count = 31;
        for (uint8_t i = 0; i < chr_count; i++) {
            desc_str[1 + i] = str[i];
        }
    }
    desc_str[0] = (TUSB_DESC_STRING << 8) | (2 * chr_count + 2);
    return desc_str;
}
```

### CDC 收發實作

```c
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "tusb.h"

#define TAG "USB_CDC"

/* CDC 接收回調（TinyUSB 在有資料時呼叫） */
void tud_cdc_rx_cb(uint8_t itf) {
    uint8_t buf[64];
    uint32_t count = tud_cdc_read(buf, sizeof(buf));

    /* Echo back：收到什麼就回什麼 */
    if (count > 0) {
        tud_cdc_write(buf, count);
        tud_cdc_write_flush();
        ESP_LOGI(TAG, "Echo %lu bytes", count);
    }
}

/* CDC 連線狀態回調 */
void tud_cdc_line_state_cb(uint8_t itf, bool dtr, bool rts) {
    if (dtr) {
        ESP_LOGI(TAG, "Host connected (DTR set)");
    } else {
        ESP_LOGI(TAG, "Host disconnected (DTR cleared)");
    }
}

/* USB 事件任務 */
static void usb_device_task(void *arg) {
    while (1) {
        /* tud_task() 處理所有 USB 事件：enumeration、收發、回調 */
        tud_task();
        /* 不能 vTaskDelay 太長，否則 Host 等待 ACK 超時 */
        taskYIELD();
    }
}

/* 每秒主動發送一行資料 */
static void cdc_send_task(void *arg) {
    uint32_t count = 0;
    while (1) {
        if (tud_cdc_connected()) {
            char msg[64];
            int len = snprintf(msg, sizeof(msg),
                               "ESP32-S3 CDC tick: %lu\r\n", count++);
            tud_cdc_write(msg, len);
            tud_cdc_write_flush();
        }
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}

void app_main(void) {
    /* 初始化 TinyUSB */
    tud_init(BOARD_TUD_RHPORT);

    /* USB 事件任務：高優先度，確保 USB 及時回應 */
    xTaskCreate(usb_device_task, "usb_device", 4096, NULL, 10, NULL);

    /* 定期發送任務 */
    xTaskCreate(cdc_send_task, "cdc_send", 2048, NULL, 5, NULL);
}
```

---

## 測試步驟

1. 用 USB 線連接 ESP32-S3 的 USB OTG 腳（GPIO19=D-，GPIO20=D+）到 PC。
2. 燒錄後重開，PC 的裝置管理員（Windows）或 `dmesg`（Linux）應出現 CDC ACM 裝置：
   ```
   # Linux:
   [  123.456] usb 1-1: new full-speed USB device number 4
   [  123.567] cdc_acm 1-1:1.0: ttyACM0: USB ACM device
   ```
3. 開啟 minicom 或 PuTTY，連接到 `/dev/ttyACM0`（或 `COM5` 等），鮑率任意（CDC 忽略 line coding）。
4. 應看到每秒一行 `ESP32-S3 CDC tick: N`。
5. 輸入任意字元，應看到 echo 回來。

---

## 常見問題

| 現象 | 可能原因 | 排查方式 |
|------|---------|---------|
| 裝置管理員看不到裝置 | USB 線只有充電功能（無資料線） | 換有資料線的 USB 線 |
| 看到「Unknown Device」 | Descriptor 格式錯誤 | 檢查 `CONFIG_TOTAL_LEN` 計算是否正確 |
| 連接後立刻斷開 | FIFO 大小設定錯誤 | 確認 `CFG_TUD_CDC_RX_BUFSIZE` 設定 |
| 資料亂碼 | CDC TX flush 沒呼叫 | 確認每次 write 後呼叫 `tud_cdc_write_flush()` |
| `tud_task()` 沒有跑 | USB 任務優先度太低被搶佔 | 把 USB 任務優先度設最高（`configMAX_PRIORITIES - 1`） |

---

## 自我檢核

- [ ] 知道 ESP32-S3 USB OTG 的暫存器基底位址
- [ ] 能說明 DWC_OTG 的 FIFO 架構（TX/RX FIFO 如何共享）
- [ ] 能解釋為什麼 USB 不純手刻（至少列出 3 個原因）
- [ ] 知道 `tusb_config.h` 中 `CFG_TUD_CDC` 和 Buffer 大小的設定
- [ ] 知道 CDC Descriptor 需要哪幾層（IAD、Control Interface、Data Interface）
- [ ] 能解釋 `tud_task()` 在主迴圈中的角色
- [ ] 知道 `tud_cdc_write_flush()` 為什麼必要

USB 是純數字介面，Ethernet 在物理層完全不同——類比差分信號、PHY 晶片、Manchester 編碼。下一章看 Ethernet 的電氣和協議基礎。

→ [Ch 24 Ethernet 原理](./24-ethernet-protocol.md)
