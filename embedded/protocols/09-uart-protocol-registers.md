# Ch 9 — UART 協議原理與暫存器

> 目標：理解 UART（Universal Asynchronous Receiver-Transmitter，通用非同步收發器）的幀格式與電氣特性，掌握 ESP32 UART 暫存器，完成 register-level 非阻塞收發函式。

## UART 的核心設計：不要 clock 線

I2C 和 SPI 都需要時鐘線讓接收方知道什麼時候採樣。UART 不一樣：雙方事先約定 **baud rate（鮑率）**，各自用自己的時鐘源採樣，靠 start bit 同步每個 frame 的起始點。

代價是：baud rate 不匹配的誤差累積超過半個 bit 就會讀錯，而且沒有天然的多節點能力（想多接裝置要靠 RS-485 這層來解決）。

## UART Frame 時序圖

一個 UART frame（8N1：8 data bits，no parity，1 stop bit）：

```
線路空閒（idle）  start  D0  D1  D2  D3  D4  D5  D6  D7  stop  idle
                   bit                                       bit

高電位（1）: ‾‾‾‾\_____________________________/‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾
             idle   ^                      ^   ^
                    |                      |   stop bit (高)
                    start bit (低)         D7

             <-  1 bit period = 1/baud_rate  ->

資料位元傳輸順序：LSB first（D0 先送，D7 最後）
```

關鍵特性：

- **Idle state 是高電位（邏輯 1）**，start bit 是低電位，所以接收方靠下降沿偵測 frame 開始
- **資料位元 LSB first**：送 0xAB = 10101011b，wire 上順序是 1,1,0,1,0,1,0,1（D0 到 D7）
- **Stop bit 是高電位**，確保下一個 start bit 的下降沿能被偵測到

## Baud Rate 計算與誤差

```
UART_CLK_DIV = F_APB / baud_rate

ESP32 APB clock = 80 MHz（預設）

常見 baud rate：
  9600   → CLK_DIV = 80000000 / 9600   = 8333.33 → 整數 8333，誤差 0.004%
  115200 → CLK_DIV = 80000000 / 115200 = 694.44  → 整數 694，  誤差 0.06%
  921600 → CLK_DIV = 80000000 / 921600 = 86.80   → 整數 87，   誤差 0.23%
```

ESP32 CLK_DIV 暫存器有小數部分（bit field），可進一步降低誤差：

```
UART_CLKDIV_REG [19:4] = 整數部分（clkdiv）
UART_CLKDIV_REG [3:0]  = 小數部分 × 16（clkdiv_frag）

實際 divisor = clkdiv + clkdiv_frag / 16
115200：694.44 → clkdiv=694，clkdiv_frag = round(0.44 × 16) = 7
```

UART 的誤差容限：接收方在每個 bit 的中點採樣，允許的偏移是 ±50% bit period。兩端誤差加起來不超過 5% 通常沒問題。9600 baud 時，時序誤差可容許約 ±52 µs。

## Flow Control（流量控制）

| 類型 | 機制 | 說明 |
|------|------|------|
| 無流控 | 不額外控制 | 最常見，依賴 FIFO 和足夠快的處理 |
| RTS/CTS 硬體流控 | RTS=Request To Send，CTS=Clear To Send | 高速或資料量大時用，需額外兩條線 |
| XON/XOFF 軟體流控 | 傳送特殊控制字元 0x11/0x13 | 不能傳二進位資料（XON/XOFF 會被誤判） |

ESP32 UART 支援 RTS/CTS 硬體流控，透過 `UART_CONF1_REG` 設定。

## ESP32 UART 控制器

ESP32 有 3 個 UART 控制器：

| 控制器 | Base Address | 預設腳位 |
|--------|-------------|---------|
| UART0 | 0x3FF40000 | TX=GPIO1，RX=GPIO3（預設 debug log 用） |
| UART1 | 0x3FF50000 | TX=GPIO10，RX=GPIO9（Flash 用，慎用） |
| UART2 | 0x3FF6E000 | TX=GPIO17，RX=GPIO16 |

實際工程建議：UART0 留給 log 輸出，應用程式用 UART1 或 UART2。UART1 的預設 GPIO 和 Flash 重疊，記得用 GPIO Matrix 重新分配。

## 重要暫存器

以下 offset 相對於各 UART base address。

```c
#define UART0_BASE  0x3FF40000UL
#define UART1_BASE  0x3FF50000UL
#define UART2_BASE  0x3FF6E000UL

#define UART_FIFO_REG     0x00  // TX/RX FIFO 存取（bit 7:0）
#define UART_INT_RAW_REG  0x04  // 原始中斷旗標
#define UART_INT_ST_REG   0x08  // 中斷狀態（遮罩後）
#define UART_INT_ENA_REG  0x0C  // 中斷致能
#define UART_INT_CLR_REG  0x10  // 清除中斷（寫 1 清）
#define UART_CLKDIV_REG   0x14  // 鮑率分頻器
#define UART_AUTOBAUD_REG 0x18  // 自動波特率偵測（通常不用）
#define UART_STATUS_REG   0x1C  // 狀態暫存器（FIFO 計數等）
#define UART_CONF0_REG    0x20  // 幀格式、loopback 等
#define UART_CONF1_REG    0x24  // FIFO 水位、流控設定
#define UART_LOWPULSE_REG 0x28  // 自動波特率測量值
#define UART_HIGHPULSE_REG 0x2C // 自動波特率測量值
#define UART_RXD_CNT_REG  0x30  // 自動波特率計數
#define UART_AT_CMD_PRECNT_REG 0x34 // AT CMD 模式
#define UART_AT_CMD_POSTCNT_REG 0x38
#define UART_AT_CMD_GAPTOUT_REG 0x3C
#define UART_AT_CMD_CHAR_REG   0x40
#define UART_MEM_CONF_REG      0x58  // FIFO 擴充（最大 128 bytes）
#define UART_MEM_TX_STATUS_REG 0x5C
#define UART_MEM_RX_STATUS_REG 0x60
#define UART_MEM_CNT_STATUS_REG 0x64
```

### UART_CLKDIV_REG（0x14）

```
[19:4]  clkdiv       整數分頻值
[3:0]   clkdiv_frag  小數分頻（× 1/16）
```

### UART_CONF0_REG（0x20）幀格式

```
[1:0]   parity     00=偶同位，01=奇同位（需搭配 bit2）
[2]     parity_en  1=啟用同位檢查
[4:3]   bit_num    00=5bit，01=6bit，10=7bit，11=8bit（最常用 11）
[5]     stop_bit_num  0=1 stop bit，1=1.5，2=2
[7]     loopback   1=TX 接回 RX（測試用）
[14]    uart_rxd_inv  RX 訊號反相
[15]    uart_cts_inv  CTS 訊號反相
[16]    uart_dsr_inv
[17]    uart_txd_inv  TX 訊號反相
[18]    uart_rts_inv
[19]    uart_dtr_inv
[22]    rs485_en       RS-485 模式（Ch 10 詳述）
[23]    rs485_tx_rx_en 半雙工 TX/RX 切換
```

8N1 設定：`bit_num=11，parity_en=0，stop_bit_num=0` → `CONF0 = 0x0000_000C`

### UART_STATUS_REG（0x1C）

```
[7:0]   rxfifo_cnt   RX FIFO 目前 byte 數
[8]     st_urx_out   接收狀態機狀態
[13:8]  （保留）
[23:16] txfifo_cnt   TX FIFO 目前 byte 數
```

### UART_INT_RAW_REG（0x04）

```
[0]  RXFIFO_FULL_INT    RX FIFO 達水位線
[1]  TXFIFO_EMPTY_INT   TX FIFO 低於水位線
[2]  PARITY_ERR_INT     同位錯誤
[3]  FRM_ERR_INT        幀格式錯誤（stop bit 不是高電位）
[4]  RXFIFO_OVF_INT     RX FIFO 溢位（讀不夠快）
[5]  DSR_CHG_INT
[6]  CTS_CHG_INT
[7]  BRK_DET_INT        Break condition 偵測
[8]  RXFIFO_TOUT_INT    RX FIFO timeout（收到資料後一段時間沒有新資料）
[9]  SW_XON_INT
[10] SW_XOFF_INT
[11] GLITCH_DET_INT     毛刺偵測
[12] TX_BRK_DONE_INT
[13] TX_BRK_IDLE_DONE_INT
[14] TX_DONE_INT        TX 完成（所有 byte 從 FIFO 送出，shift register 空了）
```

## 完整初始化與非阻塞收發

```c
#include <stdint.h>
#include <stddef.h>

#define REG_WRITE(addr, val)  (*((volatile uint32_t *)(addr)) = (val))
#define REG_READ(addr)        (*((volatile uint32_t *)(addr)))
#define REG_SET_BIT(addr, b)  REG_WRITE((addr), REG_READ(addr) | (b))
#define REG_CLR_BIT(addr, b)  REG_WRITE((addr), REG_READ(addr) & ~(b))

static const uint32_t uart_base_table[3] = {
    0x3FF40000UL,
    0x3FF50000UL,
    0x3FF6E000UL,
};

#define APB_CLK_HZ 80000000UL

// DPORT clock / reset 暫存器位元（UART0=bit2, UART1=bit5, UART2=bit23）
static const uint32_t uart_clk_bits[3] = { (1<<2), (1<<5), (1<<23) };

void uart_init(int port, uint32_t baud_rate) {
    uint32_t base = uart_base_table[port];
    uint32_t cb   = uart_clk_bits[port];

    // 1. 致能 peripheral 時鐘
    REG_SET_BIT(0x3FF000C0, cb);   // DPORT_PERIP_CLK_EN_REG
    REG_SET_BIT(0x3FF000C4, cb);   // DPORT_PERIP_RST_EN_REG（reset）
    REG_CLR_BIT(0x3FF000C4, cb);

    // 2. 設定 baud rate（含小數分頻）
    uint32_t clkdiv_int  = APB_CLK_HZ / baud_rate;
    uint32_t remainder   = APB_CLK_HZ % baud_rate;
    uint32_t clkdiv_frag = (remainder * 16 + baud_rate / 2) / baud_rate;
    REG_WRITE(base + UART_CLKDIV_REG,
              (clkdiv_int << 4) | (clkdiv_frag & 0xF));

    // 3. 幀格式：8N1（bit_num=3, no parity, 1 stop bit）
    REG_WRITE(base + UART_CONF0_REG, (3 << 3));

    // 4. FIFO 水位：RX 水位=1（任何資料就產生中斷，polling 模式不重要）
    //    TX 水位=10
    REG_WRITE(base + UART_CONF1_REG, (10 << 16) | (1 << 0));

    // 5. 清除並停用所有中斷（polling 模式）
    REG_WRITE(base + UART_INT_CLR_REG, 0xFFFF);
    REG_WRITE(base + UART_INT_ENA_REG, 0);

    // 注意：GPIO 路由需要透過 GPIO Matrix 單獨設定，
    // 這裡假設呼叫者已經設好對應 GPIO 的 func_sel。
}

// 寫入一個 byte 到 TX FIFO（如果 FIFO 滿就等）
void uart_write_byte(int port, uint8_t byte) {
    uint32_t base = uart_base_table[port];
    // 等 TX FIFO 不滿（txfifo_cnt < 128）
    while (((REG_READ(base + UART_STATUS_REG) >> 16) & 0xFF) >= 127) {}
    REG_WRITE(base + UART_FIFO_REG, byte);
}

// 送出 len 個 bytes，阻塞直到全部進 FIFO
void uart_write(int port, const uint8_t *data, size_t len) {
    for (size_t i = 0; i < len; i++) {
        uart_write_byte(port, data[i]);
    }
}

// 等待 TX FIFO 和 shift register 全部空（確保送完）
void uart_flush_tx(int port) {
    uint32_t base = uart_base_table[port];
    // TX_DONE_INT：TX 完全空
    while (!(REG_READ(base + UART_INT_RAW_REG) & (1 << 14))) {}
    REG_WRITE(base + UART_INT_CLR_REG, (1 << 14));
}

// 非阻塞讀取：有資料就回傳，沒有就回傳 -1
int uart_read_byte_nonblock(int port) {
    uint32_t base = uart_base_table[port];
    if ((REG_READ(base + UART_STATUS_REG) & 0xFF) == 0) {
        return -1;  // FIFO 空
    }
    return (int)(REG_READ(base + UART_FIFO_REG) & 0xFF);
}

// 帶 timeout 的讀取（timeout 單位：迴圈次數，約 1 = 幾十 ns）
int uart_read_byte_timeout(int port, uint32_t timeout) {
    uint32_t base = uart_base_table[port];
    while (timeout--) {
        if ((REG_READ(base + UART_STATUS_REG) & 0xFF) != 0) {
            return (int)(REG_READ(base + UART_FIFO_REG) & 0xFF);
        }
    }
    return -1;  // timeout
}

// 讀 len 個 bytes，帶 timeout（per-byte timeout）
// 回傳實際讀到的 byte 數
size_t uart_read(int port, uint8_t *buf, size_t len, uint32_t per_byte_timeout) {
    size_t count = 0;
    while (count < len) {
        int b = uart_read_byte_timeout(port, per_byte_timeout);
        if (b < 0) break;
        buf[count++] = (uint8_t)b;
    }
    return count;
}
```

## UART0 GPIO 設定補充

UART0 預設 TX=GPIO1，RX=GPIO3，這是 ROM bootloader 和 `esp_log` 預設使用的腳位，不要動：

```c
// 不要動 UART0 的 GPIO，讓它繼續當 log port
// UART2 TX=GPIO17, RX=GPIO16 是乾淨的應用 UART
// GPIO17 → UART2 TX signal（func_sel via IO_MUX or GPIO Matrix）
```

若要在 UART2 使用非預設 GPIO（例如 GPIO25/GPIO26），需要透過 GPIO Matrix 設定 signal routing，方法與 I2C 的做法相同。

## 自我檢核

- [ ] 能畫出 8N1 UART frame，標出 idle、start、8 data bits（LSB first）、stop
- [ ] 能計算 115200 baud 在 80 MHz APB 時的 `clkdiv` 和 `clkdiv_frag` 值
- [ ] 知道 `RXFIFO_OVF_INT` 的原因，以及避免溢位的做法
- [ ] 理解 `uart_flush_tx()` 為何要等 `TX_DONE_INT` 而非只等 FIFO 空
- [ ] 能解釋 RTS/CTS 硬體流控的握手流程
- [ ] 知道 UART0 不能隨意改 GPIO 的原因

下一章用 RS-485 擴展 UART，讓它能跑差分信號、長距離、多節點。

→ [Ch 10 RS-485 差分信號](./10-rs485.md)
