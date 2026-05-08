# Ch 22 — USB 協議原理

> 目標：理解 USB 2.0 的拓樸架構、enumeration 流程、Descriptor 樹狀結構、四種 Endpoint 類型，以及 CDC 如何讓設備模擬序列埠，為下一章 ESP32-S3 USB CDC 實作打底。

---

## 硬體需求：先看這裡

Classic ESP32 沒有 USB OTG 控制器，無法做 USB Device。你需要：

| 晶片 | USB OTG | 備註 |
|------|---------|------|
| ESP32（classic） | 無 | 只有 USB-UART 橋（CH340/CP2102，不是 USB OTG） |
| ESP32-S2 | 有（Full Speed） | 支援 USB Device |
| ESP32-S3 | 有（Full Speed） | 支援 USB Device，推薦用這個 |
| ESP32-P4 | 有（High Speed） | 最新款，480 Mbps |

本章和下一章都基於 ESP32-S3。

---

## USB 2.0 速度等級

| 等級 | 速率 | 應用 |
|------|------|------|
| Low Speed（LS） | 1.5 Mbps | 滑鼠、鍵盤 |
| Full Speed（FS） | 12 Mbps | CDC、HID、Audio |
| High Speed（HS） | 480 Mbps | 儲存裝置、高速資料傳輸 |

ESP32-S3 支援 Full Speed（12 Mbps），對 CDC 序列埠完全夠用。

---

## USB 拓樸

USB 不是 bus，是樹狀結構：

```
Host（PC）
    |
    +--- Root Hub
            |
            +--- Hub（選配）
            |        |
            |        +--- Device A（鍵盤）
            |        +--- Device B（滑鼠）
            |
            +--- Device C（你的 ESP32-S3）
            |
            +--- Device D（USB Hub 展開的 Device）
```

關鍵特性：
- Host 唯一，Host 主動發起所有傳輸
- Device 只能回應，不能主動發起（Interrupt IN 例外：設備告知 Host 它有資料）
- 最多 127 個設備（7-bit device address）
- Hub 透明，Host 視所有 Device 在同一個虛擬 bus 上

---

## Enumeration 流程

Enumeration（枚舉）是設備插入後 Host 識別和設定設備的過程：

```
Device 插入 USB 口
         |
         v
Host 偵測到電壓變化（D+ 或 D- 上拉）
         |
         v
Host 發出 USB Reset（SE0 信號，至少 10ms）
         |
         v
Device 重置為 Address 0，進入 Default 狀態
         |
         v
Host 發 Get_Device_Descriptor（取前 8 bytes）
  目的：得知 EP0 的 MaxPacketSize
         |
         v
Host 發 Set_Address（分配新 Address，1~127）
         |
         v
Device 確認後開始用新位址回應
         |
         v
Host 發 Get_Device_Descriptor（取完整 18 bytes）
         |
         v
Host 發 Get_Configuration_Descriptor
  （取 Configuration + Interface + Endpoint 全部）
         |
         v
Host 發 Set_Configuration（選擇 Configuration 1）
         |
         v
設備就緒，Host 載入對應的驅動程式（CDC = usbser.sys / cdc_acm）
```

整個 enumeration 是一系列 Control Transfer（EP0）。Host 控制一切，Device 只是回應。

---

## Descriptor 樹狀結構

USB 設備的描述資訊以 Descriptor（描述符）的樹狀結構組織：

```
Device Descriptor（1個）
    |
    +-- Configuration Descriptor（可多個，通常1個）
            |
            +-- Interface Descriptor（可多個）
                    |
                    +-- Endpoint Descriptor（可多個，除了EP0）
                    +-- Class-specific Descriptor（如 CDC 的 Functional Descriptor）
```

| Descriptor 類型 | 關鍵 field | 說明 |
|----------------|-----------|------|
| Device | bDeviceClass, bDeviceSubClass, idVendor, idProduct, bNumConfigurations | 設備識別與類別 |
| Configuration | bNumInterfaces, bConfigurationValue, bmAttributes（自供電/匯流排供電）, bMaxPower | 設定選項 |
| Interface | bInterfaceNumber, bAlternateSetting, bNumEndpoints, bInterfaceClass | 功能介面 |
| Endpoint | bEndpointAddress（含方向bit）, bmAttributes（傳輸類型）, wMaxPacketSize, bInterval | 端點屬性 |

Descriptor 是唯讀靜態資料，Host 在 enumeration 時讀取，之後不再讀（除非設備 reset）。

---

## Endpoint 類型

EP0 是所有設備必有的 Control Endpoint，用於 enumeration 和控制命令。其他 Endpoint 依應用需求選擇：

| 類型 | 方向 | 最大封包大小（FS） | 保證延遲 | 錯誤重傳 | 典型應用 |
|------|------|-----------------|---------|---------|---------|
| Control | 雙向 | 64 bytes | 無保證 | 有 | Enumeration、設定命令 |
| Bulk | IN 或 OUT | 64 bytes | 無保證 | 有 | 大量資料、USB Storage、CDC 資料 |
| Interrupt | IN 或 OUT | 64 bytes | 有（1~255ms） | 有 | HID（鍵盤、滑鼠）、CDC 通知 |
| Isochronous | IN 或 OUT | 1023 bytes | 固定頻寬 | 無 | USB Audio、USB Video |

Bulk：填滿頻寬，但 Host 可能在忙時延遲傳輸，不保證何時到。  
Interrupt：Host 每隔 bInterval ms 就輪詢一次，最大延遲有保證。  
Isochronous：每個 frame（1ms）都分配固定頻寬，封包丟了不重傳，用於即時串流。

---

## USB 封包格式

USB 傳輸的基本單位是封包（Packet），每個 frame（1ms，FS）以 SOF 開始：

```
  SOF Token        Token Packet        Data Packet      Handshake
+----------+   +----------------+   +----------+---+   +-------+
| SOF(PID) |   | PID | ADDR | EP|   | PID | DATA  |   |  PID  |
| Frame No |   | (SETUP/IN/OUT) |   | + CRC16   |   |ACK/NAK|
+----------+   +----------------+   +-----------+   | /STALL|
                                                     +-------+

PID = Packet IDentifier（封包類型識別碼）
```

一次 Control Transfer（如 Get_Device_Descriptor）包含三個階段：
1. **Setup Stage**：Host 送 SETUP Token + 8 bytes setup data
2. **Data Stage**：Host 送 IN Token，Device 回 data
3. **Status Stage**：確認完成

---

## CDC（Communications Device Class）

CDC 讓 USB 設備在 Host 上被識別為序列埠（Windows 看到 COM port，Linux 看到 /dev/ttyACM0）：

```
CDC 使用兩個 Interface：

Interface 0（CDC Control Interface）
  bInterfaceClass = 0x02（CDC）
  Endpoint: Interrupt IN（用於發送串列埠狀態通知，如 line state 改變）

Interface 1（CDC Data Interface）
  bInterfaceClass = 0x0A（CDC Data）
  Endpoint: Bulk IN（Device → Host，讀資料）
  Endpoint: Bulk OUT（Host → Device，寫資料）
```

CDC Class-specific Descriptor 包含 CDC Header、CDC Union（定義 Control 和 Data interface 的關係）、Call Management 等 functional descriptor。

Host 上的 CDC ACM（Abstract Control Model）驅動處理 Interrupt EP 的 SerialState 通知，讓應用程式可以讀取 DSR、DCD 等虛擬硬體信號。

---

## 為什麼不純手刻

USB enumeration 只是開始。完整 USB Device stack 需要：

1. FIFO 管理（EP0 的 setup/data/status stage 各自的 FIFO）
2. 正確處理 EP0 error recovery（STALL、NAK 的 state machine）
3. Standard device request 全部實作（Get_Descriptor 各類型、Set/Get_Configuration、Set/Get_Interface、Get_Status 等共 11 種）
4. CDC class-specific request（SET_LINE_CODING、GET_LINE_CODING、SET_CONTROL_LINE_STATE 等）
5. 應付 Host 各種不按規格來的行為（Windows / macOS / Linux 的 USB Host stack 行為各有細節差異）

這大概是 3,000~5,000 行精心除錯過的 C code。TinyUSB 已經做了這些，且在數百種嵌入式硬體上驗證過。下一章直接用 TinyUSB。

---

## 自我檢核

- [ ] 知道 Classic ESP32 不能做 USB Device，要用 S2 或 S3
- [ ] 能說明 USB 樹狀拓樸，Host 和 Device 的主從關係
- [ ] 能描述 Enumeration 的主要步驟
- [ ] 知道 Device/Configuration/Interface/Endpoint Descriptor 各層的關係
- [ ] 能說明 Bulk、Interrupt、Isochronous 三種 Endpoint 的差異和應用場景
- [ ] 知道 CDC 用幾個 Interface、哪些 Endpoint
- [ ] 能解釋 Host 看到 /dev/ttyACM0 背後的 CDC 機制

這一章是原理，下一章進入 ESP32-S3 的實際控制器和 TinyUSB 的實作。

→ [Ch 23 實作：ESP32-S3 USB CDC](./23-usb-cdc.md)
