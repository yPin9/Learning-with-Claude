# Ch 16 — BLE 協議堆疊

> 目標：建立 BLE（Bluetooth Low Energy）完整協議堆疊的心智模型，從 PHY 層到 GATT/GAP，理解 advertising、connection 建立流程、ATT 協議的 attribute 模型，以及安全配對機制。這些是實作 ESP32 BLE 的基礎。

---

## BLE vs Classic Bluetooth

BLE（Bluetooth Low Energy）和 Classic Bluetooth 共用 2.4GHz 頻段，但完全是不同的協議：

| 特性 | BLE | Classic Bluetooth |
|------|-----|-----------------|
| 功耗 | 極低（幾 mW，可用紐扣電池跑數年）| 較高（10~100mW）|
| 連接建立時間 | ~3ms | ~100ms |
| 峰值吞吐量 | ~1 Mbps（PHY 2M 模式）| 最高 ~3 Mbps（EDR）|
| 主要用途 | 感測器、遙控器、健康裝置 | 音頻（A2DP）、HID、Serial Port |
| 兩者可否共存 | ESP32 單一晶片同時支援 | 共存，需要協調排程 |

BLE 用 40 個 channel（每個 2MHz 寬）：37 個 data channel + 3 個 advertising channel（37、38、39）。

---

## 協議堆疊架構

```
  應用層（Application）
  ─────────────────────────────────────────────────────
  GAP（Generic Access Profile，通用存取設定檔）
       連接管理、廣播角色定義、安全模式
  ─────────────────────────────────────────────────────
  GATT（Generic Attribute Profile，通用屬性設定檔）
       服務（Service）/ 特徵值（Characteristic）模型
  ─────────────────────────────────────────────────────
  ATT（Attribute Protocol，屬性協議）
       Read / Write / Notify / Indicate 操作
  ─────────────────────────────────────────────────────
  L2CAP（Logical Link Control and Adaptation Protocol）
       封包分段重組，多路複用（ATT / SMP / SIG channels）
  ─────────────────────────────────────────────────────
  HCI（Host Controller Interface）
       Host 和 Controller 之間的標準介面
       （ESP32 用 VHCI，不走實體 UART/USB）
  ─────────────────────────────────────────────────────
  LL（Link Layer，鏈路層）
       狀態機、封包收發、加密、flow control
       CRC、白化（whitening）
  ─────────────────────────────────────────────────────
  PHY（Physical Layer）
       2.4GHz GFSK 調變
       1Mbps（LE 1M）/ 2Mbps（LE 2M）/ 125kbps or 500kbps（Coded，長距）
```

ESP32 的 BLE controller（LL + PHY）跑在獨立的 RF 核心，Host（L2CAP 以上）跑在 app CPU 上，兩者透過 **VHCI**（Virtual HCI）通訊。

---

## PHY 層

BLE 有三種 PHY 模式：

| PHY | 速率 | 範圍 | 特點 |
|-----|------|------|------|
| LE 1M | 1 Mbps | ~100m | 所有 BLE 裝置都支援的基線 |
| LE 2M | 2 Mbps | ~80m | BT 5.0+，較高吞吐量，功耗略增 |
| LE Coded（S=8）| 125 kbps | ~1km | 長距離，FEC 冗餘編碼 |
| LE Coded（S=2）| 500 kbps | ~400m | 長距離中速版本 |

ESP32 原版（不是 ESP32-S3）只支援 LE 1M。ESP32-S3/C3/C6 支援 LE 2M 和 LE Coded。

---

## Link Layer 狀態機

```
                 ┌──────────────┐
                 │   Standby    │ ← 初始狀態，不收不發
                 └──────────────┘
                  ↑           ↓
           停止廣播         開始廣播
                  │           ↓
                  │    ┌─────────────┐
                  │    │ Advertising │ ← 每隔一段時間在 37/38/39 channel 廣播 ADV 封包
                  │    └─────────────┘
                  │           │ 收到 CONNECT_IND
                  │           ↓
                  │    ┌─────────────┐    ┌────────────┐
                  │    │ Connection  │←──→│  Scanning  │ ← 掃描 ADV 封包
                  │    └─────────────┘    └────────────┘
                  │           │ 連接斷開
                  └───────────┘
```

---

## Advertising 封包格式

ADV_IND（connectable undirected advertising）是最常見的廣播類型：

```
PDU Header（2 bytes）           Payload（最多 37 bytes）
┌──────────────────────────┐   ┌─────────────────────────────────┐
│ PDU Type │ TxAdd │ RxAdd │   │ AdvA（6 bytes）│ AdvData（≤31 B）│
│  4 bits  │  1bit │  1bit │   │  廣播地址      │  廣播資料       │
└──────────────────────────┘   └─────────────────────────────────┘
PDU Type：0000 = ADV_IND
TxAdd：0=Public address，1=Random address
```

AdvData 裡可以包含多個 AD Structure：

```
AD Structure：Length（1B）+ Type（1B）+ Value（Length-1 bytes）
常見 AD Type：
  0x01：Flags（LE General Discoverable Mode，BR/EDR Not Supported）
  0x09：Complete Local Name（裝置名稱）
  0xFF：Manufacturer Specific Data
  0x03：Complete List of 16-bit Service UUIDs
```

---

## Connection 建立流程

```
Peripheral（廣播者）                              Central（掃描者）
      │                                                 │
      │──── ADV_IND（每 100ms 廣播）─────────────────→│
      │                                                 │ 決定連接
      │←─── CONNECT_IND（包含 connection params）──────│
      │                                                 │
      ├─── connection 建立（使用 data channels 輪詢）──┤
      │                                                 │
      │←─── LL Data PDU（ATT/L2CAP 封包）─────────────│
      │──── LL Data PDU（response）────────────────────→│
```

CONNECT_IND 攜帶的關鍵參數：
- **connInterval**：連接間隔，7.5ms ~ 4s（單位 1.25ms）
- **connLatency**（Slave Latency）：peripheral 可以跳過幾個連接事件
- **supervisionTimeout**：超時後認為連接斷開，單位 10ms

---

## ATT 協議：Attribute 模型

ATT 是 BLE 應用資料傳輸的核心協議。每個 attribute 有四個欄位：

| 欄位 | 大小 | 說明 |
|------|------|------|
| Handle | 2 bytes | 在這個連接中唯一標識一個 attribute，從 0x0001 開始依序遞增 |
| UUID | 2 或 16 bytes | 屬性的類型，16-bit 是 Bluetooth SIG 分配，128-bit 是自定義 |
| Value | 0~512 bytes | 實際資料 |
| Permissions | 邏輯標誌 | 可讀/可寫/需要加密/需要認證 |

ATT 操作類型：

| 操作 | 方向 | 說明 |
|------|------|------|
| Read Request / Response | C → S / S → C | Client 讀取 Server 的 attribute value |
| Write Request / Response | C → S / S → C | Client 寫入，Server 回 ACK |
| Write Command | C → S | Client 寫入，不需要 ACK（Write Without Response）|
| Handle Value Notification | S → C | Server 主動推送，不需要 Client ACK |
| Handle Value Indication / Confirmation | S → C / C → S | Server 主動推送，需要 Client 回確認 |

---

## GATT 層次模型

GATT 在 ATT 的 attribute 上建立更有結構的模型：

```
Profile
└── Service（服務）
    ├── Service Declaration attribute（UUID: 0x2800）
    ├── Characteristic（特徵值）
    │   ├── Characteristic Declaration attribute（UUID: 0x2803）
    │   │     value = Properties（1B）+ Value Handle（2B）+ UUID
    │   ├── Characteristic Value attribute（實際資料）
    │   └── Descriptor（描述符）
    │       └── CCCD（0x2902）：Client 寫入 0x0001 啟用 Notification
    └── Characteristic（另一個）
        └── ...
```

Characteristic Properties（在 Declaration 裡的 1 byte）：

| Bit | 名稱 | 說明 |
|-----|------|------|
| 1 | BROADCAST | 可以放在 advertising data 裡 |
| 2 | READ | Client 可以讀 |
| 4 | WRITE_NO_RESP | Write Without Response |
| 8 | WRITE | Write with Response |
| 16 | NOTIFY | Server 可以 Notify（需要 CCCD）|
| 32 | INDICATE | Server 可以 Indicate（需要 CCCD，需 ACK）|

---

## UUID：16-bit vs 128-bit

```
16-bit UUID（SIG Assigned）：
  0x1800：Generic Access Service
  0x1801：Generic Attribute Service
  0x180F：Battery Service
  0x2A19：Battery Level Characteristic
  0x2902：Client Characteristic Configuration Descriptor（CCCD）

128-bit UUID（自定義）：
  格式：xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
  例如：12345678-1234-5678-1234-56789ABCDEF0

128-bit UUID 在 BLE 封包裡佔 16 bytes，16-bit UUID 只佔 2 bytes。
自定義 Service/Characteristic 必須用 128-bit UUID。
```

---

## Security：Pairing、Bonding、LE Secure Connections

| 概念 | 說明 |
|------|------|
| Pairing（配對）| 兩個裝置交換 key，建立加密連接。一次性過程 |
| Bonding（綁定）| Pairing 完成後把 key 存入 flash，下次重連不用重新配對 |
| LE Legacy Pairing | BT 4.x 配對，基於 TK（Temporary Key），有已知弱點 |
| LE Secure Connections | BT 4.2+，基於 ECDH，防 MITM 攻擊 |

配對模式（IO Capability 決定）：

| IO Capability | 對應配對方式 | 安全等級 |
|--------------|------------|---------|
| NoInput NoOutput | Just Works（自動配對，無驗證）| 低 |
| DisplayOnly | Passkey Entry（一方顯示，另一方輸入）| 中 |
| DisplayYesNo | Numeric Comparison（兩方各自確認 6 位數）| 高 |
| KeyboardOnly | Passkey Entry | 中 |
| KeyboardDisplay | Numeric Comparison | 高 |

---

## 自我檢核

- [ ] 能從記憶中畫出 BLE 協議堆疊的每一層，說明各層的職責
- [ ] 能解釋 Link Layer 的 Standby / Advertising / Scanning / Connection 狀態轉換
- [ ] 能說明 ATT attribute 的四個欄位，以及五種操作的方向和是否需要 ACK
- [ ] 能用層次結構描述 GATT：Profile → Service → Characteristic → Descriptor
- [ ] 能解釋 CCCD 的用途：Client 如何告訴 Server 自己要訂閱 Notification
- [ ] 知道 Pairing 和 Bonding 的差別，以及 LE Secure Connections 比 Legacy 安全在哪裡

下一章進入 ESP32 具體的 NimBLE stack，看 controller 初始化和 VHCI 架構。

→ [Ch 17 ESP32 BLE 底層](./17-esp32-ble-internals.md)
