# Ch 11 — Modbus RTU 協議

> 目標：理解 Modbus RTU 的幀格式、常用 Function Code，掌握 CRC-16/Modbus 計算，以及 RTU 的幀邊界判斷機制，為 Ch 12 的完整實作打底。

## Modbus 的定位

Modbus 是**應用層（application layer）**協議，不是物理層也不是傳輸層。它定義的是「如何讀寫工業裝置上的資料點」，可以跑在不同的底層：

| 底層 | 模式名稱 |
|------|---------|
| RS-485（或 RS-232） | Modbus RTU（最常見）|
| TCP/IP | Modbus TCP |
| ASCII 文字格式 | Modbus ASCII（老系統，幾乎不再使用）|

Modbus 是 1979 年 Modicon（現在的 Schneider）定義的，現在仍是工業界最通用的設備通訊協議。幾乎所有 PLC、變頻器、電表、溫控器都支援 Modbus RTU。

## RTU Frame 格式

```
┌──────────────┬──────────────┬──────────────────┬────────────────┐
│  Slave Addr  │ Function Code│      Data        │   CRC-16       │
│   1 byte     │   1 byte     │    N bytes       │   2 bytes (LE) │
└──────────────┴──────────────┴──────────────────┴────────────────┘

幀邊界：3.5 character time 的靜默判斷
CRC：CRC-16/Modbus，Little-Endian（低 byte 先）
```

- **Slave Address**：0x01 ~ 0xF7，0x00 是廣播（broadcast，slave 不回應）
- **Function Code**：定義操作類型
- **Data**：根據 FC 和方向不同，格式各異
- **CRC-16**：整個幀（位址 + FC + Data）的 checksum，不含 CRC 本身

## 常用 Function Code

| FC（hex） | FC（dec） | 名稱 | 操作對象 |
|-----------|-----------|------|---------|
| 0x01 | 1 | Read Coils | 讀取線圈（1-bit 輸出）|
| 0x02 | 2 | Read Discrete Inputs | 讀取離散輸入（1-bit 輸入）|
| 0x03 | 3 | Read Holding Registers | 讀取保持暫存器（16-bit 讀寫）|
| 0x04 | 4 | Read Input Registers | 讀取輸入暫存器（16-bit 唯讀）|
| 0x05 | 5 | Write Single Coil | 寫入單一線圈 |
| 0x06 | 6 | Write Single Register | 寫入單一暫存器 |
| 0x10 | 16 | Write Multiple Registers | 連續寫入多個暫存器 |
| 0x17 | 23 | Read/Write Multiple Registers | 同時讀寫（較少見）|

FC 03（Read Holding Registers）佔了工業應用的 80%，是最重要的。

## FC 03 封包格式

### Request（Master → Slave）

```
byte 0: Slave Address      e.g. 0x01
byte 1: Function Code      0x03
byte 2: Starting Register  High byte  e.g. 0x00（暫存器 0x0064 = 100）
byte 3: Starting Register  Low byte   e.g. 0x64
byte 4: Quantity of Regs   High byte  e.g. 0x00（讀 2 個暫存器）
byte 5: Quantity of Regs   Low byte   e.g. 0x02
byte 6: CRC Low byte
byte 7: CRC High byte
```

範例：對 slave 0x01 從暫存器 0x0064 開始讀 2 個暫存器：

```
01 03 00 64 00 02 XX XX   （XX = CRC）
```

### Response（Slave → Master）

```
byte 0: Slave Address      0x01
byte 1: Function Code      0x03
byte 2: Byte Count         0x04（2 registers × 2 bytes = 4）
byte 3: Register 1 High    e.g. 0x01
byte 4: Register 1 Low     e.g. 0xF4   → 0x01F4 = 500
byte 5: Register 2 High    e.g. 0x00
byte 6: Register 2 Low     e.g. 0x64   → 0x0064 = 100
byte 7: CRC Low
byte 8: CRC High
```

暫存器值是 **Big-Endian**（高 byte 先）。CRC 本身是 **Little-Endian**（低 byte 先）。這兩個 endianness 方向相反，容易搞混。

## FC 06 Write Single Register

```
Request:
  byte 0: Slave Address  0x01
  byte 1: FC             0x06
  byte 2: Register High  0x00
  byte 3: Register Low   0x64
  byte 4: Value High     0x00
  byte 5: Value Low      0x64   → 寫入 100
  byte 6: CRC Low
  byte 7: CRC High

Response（正常回應是 echo，與 request 完全相同）：
  01 06 00 64 00 64 XX XX
```

## CRC-16/Modbus 計算

Modbus 使用 CRC-16（多項式 0x8005，反轉輸入輸出）：

- 初始值：0xFFFF
- 多項式：0x8005（反轉 bit order 後為 0xA001）
- 輸入反轉（Reflect Input）：是
- 輸出反轉（Reflect Output）：是
- XOR 輸出：0x0000

查表法是標準做法，比逐 bit 計算快很多：

```c
// CRC-16/Modbus 查表法
// 256 個 16-bit 值，預先計算好
static const uint16_t crc16_table[256] = {
    0x0000, 0xC0C1, 0xC181, 0x0140, 0xC301, 0x03C0, 0x0280, 0xC241,
    0xC601, 0x06C0, 0x0780, 0xC741, 0x0500, 0xC5C1, 0xC481, 0x0440,
    0xCC01, 0x0CC0, 0x0D80, 0xCD41, 0x0F00, 0xCFC1, 0xCE81, 0x0E40,
    0x0A00, 0xCAC1, 0xCB81, 0x0B40, 0xC901, 0x09C0, 0x0880, 0xC841,
    0xD801, 0x18C0, 0x1980, 0xD941, 0x1B00, 0xDBC1, 0xDA81, 0x1A40,
    0x1E00, 0xDEC1, 0xDF81, 0x1F40, 0xDD01, 0x1DC0, 0x1C80, 0xDC41,
    0x1400, 0xD4C1, 0xD581, 0x1540, 0xD701, 0x17C0, 0x1680, 0xD641,
    0xD201, 0x12C0, 0x1380, 0xD341, 0x1100, 0xD1C1, 0xD081, 0x1040,
    0xF001, 0x30C0, 0x3180, 0xF141, 0x3300, 0xF3C1, 0xF281, 0x3240,
    0x3600, 0xF6C1, 0xF781, 0x3740, 0xF501, 0x35C0, 0x3480, 0xF441,
    0x3C00, 0xFCC1, 0xFD81, 0x3D40, 0xFF01, 0x3FC0, 0x3E80, 0xFE41,
    0xFA01, 0x3AC0, 0x3B80, 0xFB41, 0x3900, 0xF9C1, 0xF881, 0x3840,
    0x2800, 0xE8C1, 0xE981, 0x2940, 0xEB01, 0x2BC0, 0x2A80, 0xEA41,
    0xEE01, 0x2EC0, 0x2F80, 0xEF41, 0x2D00, 0xEDC1, 0xEC81, 0x2C40,
    0xE401, 0x24C0, 0x2580, 0xE541, 0x2700, 0xE7C1, 0xE681, 0x2640,
    0x2200, 0xE2C1, 0xE381, 0x2340, 0xE101, 0x21C0, 0x2080, 0xE041,
    0xA001, 0x60C0, 0x6180, 0xA141, 0x6300, 0xA3C1, 0xA281, 0x6240,
    0x6600, 0xA6C1, 0xA781, 0x6740, 0xA501, 0x65C0, 0x6480, 0xA441,
    0x6C00, 0xACC1, 0xAD81, 0x6D40, 0xAF01, 0x6FC0, 0x6E80, 0xAE41,
    0xAA01, 0x6AC0, 0x6B80, 0xAB41, 0x6900, 0xA9C1, 0xA881, 0x6840,
    0x7800, 0xB8C1, 0xB981, 0x7940, 0xBB01, 0x7BC0, 0x7A80, 0xBA41,
    0xBE01, 0x7EC0, 0x7F80, 0xBF41, 0x7D00, 0xBDC1, 0xBC81, 0x7C40,
    0x7400, 0xB4C1, 0xB581, 0x7540, 0xB701, 0x77C0, 0x7680, 0xB641,
    0xB201, 0x72C0, 0x7380, 0xB341, 0x7100, 0xB1C1, 0xB081, 0x7040,
    0x5000, 0x90C1, 0x9181, 0x5140, 0x9301, 0x53C0, 0x5280, 0x9241,
    0x9601, 0x56C0, 0x5780, 0x9741, 0x5500, 0x95C1, 0x9481, 0x5440,
    0x9C01, 0x5CC0, 0x5D80, 0x9D41, 0x5F00, 0x9FC1, 0x9E81, 0x5E40,
    0x5A00, 0x9AC1, 0x9B81, 0x5B40, 0x9901, 0x59C0, 0x5880, 0x9841,
    0x8801, 0x48C0, 0x4980, 0x8941, 0x4B00, 0x8BC1, 0x8A81, 0x4A40,
    0x4E00, 0x8EC1, 0x8F81, 0x4F40, 0x8D01, 0x4DC0, 0x4C80, 0x8C41,
    0x4400, 0x84C1, 0x8581, 0x4540, 0x8701, 0x47C0, 0x4680, 0x8641,
    0x8201, 0x42C0, 0x4380, 0x8341, 0x4100, 0x81C1, 0x8081, 0x4040,
};

uint16_t modbus_crc16(const uint8_t *data, size_t len) {
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < len; i++) {
        crc = (crc >> 8) ^ crc16_table[(crc ^ data[i]) & 0xFF];
    }
    return crc;
}
```

CRC 附加到幀末的方式：**低 byte 先，高 byte 後**（Little-Endian）：

```c
uint16_t crc = modbus_crc16(frame, frame_len);
frame[frame_len]     = (uint8_t)(crc & 0xFF);    // CRC Low
frame[frame_len + 1] = (uint8_t)(crc >> 8);      // CRC High
```

驗證接收幀的 CRC：對整個幀（含 CRC 兩個 byte）計算 CRC，結果應該是 0x0000（這是 CRC 自身檢驗的特性）。

## Exception Response（異常回應）

Slave 無法執行請求時，回傳 Exception Response：

```
byte 0: Slave Address
byte 1: Function Code | 0x80   （例如 FC 03 異常 = 0x83）
byte 2: Exception Code
byte 3: CRC Low
byte 4: CRC High
```

| Exception Code | 名稱 | 說明 |
|----------------|------|------|
| 0x01 | Illegal Function | 不支援這個 FC |
| 0x02 | Illegal Data Address | 要求的暫存器位址不存在 |
| 0x03 | Illegal Data Value | 資料值超出範圍 |
| 0x04 | Slave Device Failure | 裝置內部錯誤 |

Master 判斷是否為 Exception Response：收到的 FC byte bit 7 是否為 1。

## RTU 幀邊界：3.5 Character Time

RTU 模式沒有明確的 length 欄位，幀邊界靠**靜默時間**判斷：兩幀之間必須有至少 **3.5 個 character time** 的靜默（bus 無活動）。

Character time = 1 start + 8 data + 1 stop = 10 bit period。

```
9600 baud：1 bit = 104.2 µs，10 bit = 1.042 ms
3.5 character = 3.5 × 1.042 ms ≈ 3.646 ms

在接收到一個 byte 後，若超過 3.5 character time 沒有下一個 byte 到來，
則判定上一幀結束。
```

| Baud Rate | 3.5 character time |
|-----------|-------------------|
| 9600 | ~3.65 ms |
| 19200 | ~1.82 ms |
| 115200 | ~303 µs |

實作上，Modbus 規範規定 baud rate > 19200 bps 時可以固定用 **1.75 ms** 作為幀間靜默（不必嚴格計算）。低速時則必須精確計算。

## 自我檢核

- [ ] 能說出 RTU frame 的四個部分及各自的長度
- [ ] 能徒手寫出 FC 03 request 的完整 8 個 byte（給定 slave addr 和 register）
- [ ] 理解暫存器值為 Big-Endian，CRC 為 Little-Endian，不混淆
- [ ] 能解釋 CRC 驗證公式（整幀算完應得 0x0000）
- [ ] 知道 Exception Response 的識別方法（FC | 0x80）
- [ ] 能計算 9600 baud 時的 3.5 character time

下一章把所有片段組合起來，實作完整的 Modbus RTU master。

→ [Ch 12 實作：UART Register-Level → Modbus RTU Master](./12-uart-modbus-master.md)
