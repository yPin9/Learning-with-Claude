# Ch 12 — 實作：UART Register-Level → Modbus RTU Master

> 目標：不依賴任何 Modbus library，用 register-level UART 和 Ch 10~11 的知識實作完整的 Modbus RTU master，包含幀組建、CRC、DE/RE 切換、response 解析與錯誤處理。

## 設計決策

在動手寫之前先確認幾件事：

1. **不用 HAL、不用 FreeRTOS queue**：所有 UART 操作都是直接打暫存器，用 polling。
2. **Timeout 策略**：等 response 的 timeout 設為 `3 × (幀長度 × bit_period) + 3.5 character_time`，保守但安全。
3. **CRC 驗證方式**：整幀（含收到的 CRC）算完應為 0x0000，而非單獨抽出 CRC 比較。
4. **錯誤碼設計**：負數回傳，方便和正常回傳的 byte count 區分。

## 函式介面

```c
// 錯誤碼
#define MODBUS_OK            0
#define MODBUS_ERR_NORESP   -1   // 沒有收到回應（timeout）
#define MODBUS_ERR_CRC      -2   // CRC 錯誤
#define MODBUS_ERR_EXCEPT   -3   // slave 回傳 Exception Response
#define MODBUS_ERR_OVERFLOW -4   // 回應超過緩衝區
#define MODBUS_ERR_BADRESP  -5   // 回應格式錯誤（長度不對）

/**
 * FC 03：Read Holding Registers
 * @param slave_addr  slave 位址（0x01 ~ 0xF7）
 * @param start_reg   起始暫存器位址（0-based）
 * @param count       要讀的暫存器數量（1 ~ 125）
 * @param out_buf     輸出緩衝區，每個 uint16_t 對應一個暫存器值
 * @return MODBUS_OK 或負數錯誤碼
 */
int modbus_read_holding_regs(uint8_t slave_addr, uint16_t start_reg,
                              uint16_t count, uint16_t *out_buf);

/**
 * FC 06：Write Single Register
 * @param slave_addr  slave 位址
 * @param reg_addr    暫存器位址
 * @param value       要寫入的值（16-bit）
 * @return MODBUS_OK 或負數錯誤碼
 */
int modbus_write_single_reg(uint8_t slave_addr, uint16_t reg_addr,
                             uint16_t value);
```

## 完整實作

```c
#include <stdint.h>
#include <stddef.h>
#include <string.h>

// ─── 暫存器存取 ─────────────────────────────────────────────────
#define REG_WRITE(addr, val)  (*((volatile uint32_t *)(addr)) = (val))
#define REG_READ(addr)        (*((volatile uint32_t *)(addr)))
#define REG_SET_BIT(addr, b)  REG_WRITE((addr), REG_READ(addr) | (b))
#define REG_CLR_BIT(addr, b)  REG_WRITE((addr), REG_READ(addr) & ~(b))

// ─── UART2 暫存器（RS-485 使用 UART2）──────────────────────────
#define UART2_BASE          0x3FF6E000UL
#define UART_FIFO_REG       0x00
#define UART_INT_RAW_REG    0x04
#define UART_INT_CLR_REG    0x10
#define UART_STATUS_REG     0x1C
#define TX_DONE_INT         (1u << 14)

// ─── DE GPIO（MAX485 Driver Enable）────────────────────────────
#define RS485_DE_GPIO       4
#define GPIO_OUT_W1TS_REG   0x3FF44008UL
#define GPIO_OUT_W1TC_REG   0x3FF4400CUL

// ─── Modbus 配置 ─────────────────────────────────────────────────
#define MODBUS_BAUD_RATE    9600
// 9600 baud：1 bit = 104.2 µs，10 bit = 1.042 ms，3.5 char = ~3.65 ms
// 用迴圈計數模擬 timeout（每次迴圈約 2~4 ns @ 240 MHz）
// 保守估計：per-byte timeout = 20000 迴圈（~0.05 ms，遠小於 1.042 ms）
// 整幀 timeout = 200000 迴圈（~0.5 ms per byte，足夠 9600 baud）
#define MODBUS_PER_BYTE_TIMEOUT  200000UL
#define MODBUS_TX_GUARD_LOOP     500UL    // DE 拉低前的 guard（2 bit period）

// ─── CRC-16/Modbus 查表 ──────────────────────────────────────────
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

static uint16_t modbus_crc16(const uint8_t *data, size_t len) {
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < len; i++) {
        crc = (crc >> 8) ^ crc16_table[(crc ^ data[i]) & 0xFF];
    }
    return crc;
}

// ─── 底層 RS-485 收發（polling）─────────────────────────────────
static void de_high(void) { REG_WRITE(GPIO_OUT_W1TS_REG, 1u << RS485_DE_GPIO); }
static void de_low(void)  { REG_WRITE(GPIO_OUT_W1TC_REG, 1u << RS485_DE_GPIO); }

// 發送幀（含 DE 控制）
static void rs485_send_frame(const uint8_t *frame, size_t len) {
    uint32_t base = UART2_BASE;
    size_t i;

    REG_WRITE(base + UART_INT_CLR_REG, TX_DONE_INT);
    __asm__ __volatile__("nop; nop; nop; nop;");
    de_high();
    __asm__ __volatile__("nop; nop; nop; nop;");

    for (i = 0; i < len; i++) {
        while (((REG_READ(base + UART_STATUS_REG) >> 16) & 0xFF) >= 127) {}
        REG_WRITE(base + UART_FIFO_REG, frame[i]);
    }

    // 等 TX shift register 完全空
    uint32_t t = 2000000;
    while (!(REG_READ(base + UART_INT_RAW_REG) & TX_DONE_INT)) {
        if (--t == 0) break;
    }
    REG_WRITE(base + UART_INT_CLR_REG, TX_DONE_INT);

    // guard：等 2 bit period 再拉低 DE（9600 baud = 208 µs ≈ busy loop）
    volatile uint32_t guard = MODBUS_TX_GUARD_LOOP;
    while (guard--) {}
    de_low();
}

// 接收 len 個 bytes，帶 per-byte timeout
// 回傳實際收到的 byte 數
static size_t rs485_recv(uint8_t *buf, size_t len, uint32_t per_byte_timeout) {
    uint32_t base = UART2_BASE;
    size_t count = 0;
    while (count < len) {
        uint32_t t = per_byte_timeout;
        while ((REG_READ(base + UART_STATUS_REG) & 0xFF) == 0) {
            if (--t == 0) goto done;
        }
        buf[count++] = (uint8_t)(REG_READ(base + UART_FIFO_REG) & 0xFF);
    }
done:
    return count;
}

// ─── 清空 RX FIFO（發送前先清乾淨）─────────────────────────────
static void flush_rx_fifo(void) {
    uint32_t base = UART2_BASE;
    while (REG_READ(base + UART_STATUS_REG) & 0xFF) {
        (void)REG_READ(base + UART_FIFO_REG);
    }
}

// ─── FC 03：Read Holding Registers ──────────────────────────────
int modbus_read_holding_regs(uint8_t slave_addr, uint16_t start_reg,
                              uint16_t count, uint16_t *out_buf) {
    uint8_t req[8];
    uint8_t resp[256];
    uint16_t crc;
    size_t expected_resp_len;
    size_t received;
    size_t i;

    if (count == 0 || count > 125) return MODBUS_ERR_BADRESP;

    // 1. 組 request frame
    req[0] = slave_addr;
    req[1] = 0x03;                        // FC 03
    req[2] = (uint8_t)(start_reg >> 8);   // Starting Address Hi
    req[3] = (uint8_t)(start_reg & 0xFF); // Starting Address Lo
    req[4] = (uint8_t)(count >> 8);       // Quantity Hi
    req[5] = (uint8_t)(count & 0xFF);     // Quantity Lo
    crc = modbus_crc16(req, 6);
    req[6] = (uint8_t)(crc & 0xFF);       // CRC Lo
    req[7] = (uint8_t)(crc >> 8);         // CRC Hi

    // 2. 計算預期回應長度：1（addr）+ 1（FC）+ 1（byte count）+
    //    count×2（data）+ 2（CRC）= count*2 + 5
    expected_resp_len = (size_t)count * 2 + 5;
    if (expected_resp_len > sizeof(resp)) return MODBUS_ERR_OVERFLOW;

    // 3. 清空 RX FIFO，送出 request
    flush_rx_fifo();
    rs485_send_frame(req, 8);

    // 4. 接收 response
    received = rs485_recv(resp, expected_resp_len, MODBUS_PER_BYTE_TIMEOUT);
    if (received < expected_resp_len) return MODBUS_ERR_NORESP;

    // 5. 驗證 CRC（整幀算完應為 0）
    if (modbus_crc16(resp, expected_resp_len) != 0x0000) return MODBUS_ERR_CRC;

    // 6. 檢查是否為 Exception Response（FC bit 7 = 1）
    if (resp[1] & 0x80) return MODBUS_ERR_EXCEPT;

    // 7. 基本格式驗證
    if (resp[0] != slave_addr) return MODBUS_ERR_BADRESP;
    if (resp[1] != 0x03)       return MODBUS_ERR_BADRESP;
    if (resp[2] != (uint8_t)(count * 2)) return MODBUS_ERR_BADRESP;

    // 8. 解析暫存器值（Big-Endian）
    for (i = 0; i < (size_t)count; i++) {
        out_buf[i] = (uint16_t)(((uint16_t)resp[3 + i * 2] << 8) |
                                 resp[3 + i * 2 + 1]);
    }
    return MODBUS_OK;
}

// ─── FC 06：Write Single Register ───────────────────────────────
int modbus_write_single_reg(uint8_t slave_addr, uint16_t reg_addr,
                             uint16_t value) {
    uint8_t req[8];
    uint8_t resp[8];
    uint16_t crc;
    size_t received;

    // 1. 組 request frame
    req[0] = slave_addr;
    req[1] = 0x06;                         // FC 06
    req[2] = (uint8_t)(reg_addr >> 8);
    req[3] = (uint8_t)(reg_addr & 0xFF);
    req[4] = (uint8_t)(value >> 8);
    req[5] = (uint8_t)(value & 0xFF);
    crc = modbus_crc16(req, 6);
    req[6] = (uint8_t)(crc & 0xFF);
    req[7] = (uint8_t)(crc >> 8);

    // 2. 清空 RX FIFO，送出 request
    flush_rx_fifo();
    rs485_send_frame(req, 8);

    // 3. 接收 response（FC 06 正常回應 = echo，固定 8 bytes）
    received = rs485_recv(resp, 8, MODBUS_PER_BYTE_TIMEOUT);
    if (received < 8) return MODBUS_ERR_NORESP;

    // 4. 驗證
    if (modbus_crc16(resp, 8) != 0x0000) return MODBUS_ERR_CRC;
    if (resp[1] & 0x80) return MODBUS_ERR_EXCEPT;
    if (memcmp(req, resp, 6) != 0) return MODBUS_ERR_BADRESP;

    return MODBUS_OK;
}
```

## 使用範例

```c
// 假設 uart_init() 和 rs485_init() 已在 main() 呼叫完畢
// 讀取 slave 0x01 的暫存器 100~101（共 2 個）

uint16_t regs[2] = {0};
int ret = modbus_read_holding_regs(0x01, 100, 2, regs);
if (ret == MODBUS_OK) {
    // regs[0] = 暫存器 100 的值
    // regs[1] = 暫存器 101 的值
} else if (ret == MODBUS_ERR_NORESP) {
    // slave 不在線，或 timeout 太短
} else if (ret == MODBUS_ERR_CRC) {
    // 資料損壞，可能是線路雜訊或終端電阻問題
}

// 寫入 slave 0x01 的暫存器 200，值設為 0x1234
ret = modbus_write_single_reg(0x01, 200, 0x1234);
```

## 用 Modbus Slave 模擬器測試

沒有真實硬體時，可以用電腦模擬 Modbus Slave：

**工具**：
- **Modbus Slave**（Windows，試用版即可）：模擬一個 slave 裝置
- **USB-RS485 轉換器**：連接 ESP32 RS-485 和電腦

**設定步驟**：
1. 連接 ESP32 RS-485 A/B 到 USB-RS485 A/B（注意 A 對 A，B 對 B）
2. 開啟 Modbus Slave，設定 COM port 和 9600 8N1，slave address = 1
3. 建立幾個 Holding Register（FC 03 區域），填入測試值
4. 執行 `modbus_read_holding_regs(0x01, 0, 4, buf)`
5. 驗證讀回的值與模擬器設定的值一致

## 常見錯誤排查

| 症狀 | 根本原因 |
|------|---------|
| 始終 `MODBUS_ERR_NORESP` | DE 沒拉高導致沒送出；或 timeout 太短；或 A/B 接反 |
| `MODBUS_ERR_CRC` 偶發 | 線路雜訊，加終端電阻；或 DE 拉低太早截斷 stop bit |
| `MODBUS_ERR_CRC` 必發 | CRC byte order 弄反（req[6]/req[7] 順序錯）|
| 讀到的值全部錯位 | response 解析時 index 算錯，或 Big-Endian 處理錯 |
| FC 06 response 驗證失敗 | slave 的 echo 和 request 不完全相同（某些 slave 行為特殊）|
| TX 送出但收不到 response | DE 拉低太早，自己的 TX 尾端被截，slave 看到 framing error |

CRC byte order 是最常踩的坑，記住：**CRC 低 byte 先放 `req[6]`，高 byte 放 `req[7]`**，不要反了。

## 自我檢核

- [ ] 能追蹤 `modbus_read_holding_regs()` 的每個步驟，對應 Ch 11 的幀格式
- [ ] 知道為何整幀（含 CRC）計算 CRC 應得 0x0000
- [ ] 能解釋 `flush_rx_fifo()` 的必要性（前一幀的殘留資料會污染新回應）
- [ ] 理解 response 長度公式 `count × 2 + 5` 的由來
- [ ] 能說明 DE 時序在 `rs485_send_frame()` 裡的處理邏輯
- [ ] 知道 Exception Response 的判斷方式，以及如何取得 exception code

下一個實作練習把 I2C、UART/RS-485、Modbus 整合進一個多協議感測器節點。

→ [練習 A：多協議感測器節點](./practice-a-multi-protocol-node.md)
