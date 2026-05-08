# Ch 3 — SPI 協議原理

> 目標：徹底搞清楚 SPI 的時序邏輯，包括四條信號線的角色、CPOL/CPHA 四種模式的差異、以及為什麼 Mode 0 是 default 的原因。不懂時序，後面的暫存器設定就是瞎填。

---

## 四條信號線

SPI（Serial Peripheral Interface）由摩托羅拉（Motorola）發明，四條線：

| 信號 | 全名 | 方向 | 說明 |
|------|------|------|------|
| SCLK | Serial Clock | Master → Slave | 時脈，由 master 產生 |
| MOSI | Master Out Slave In | Master → Slave | master 傳資料給 slave |
| MISO | Master In Slave Out | Slave → Master | slave 回傳資料給 master |
| CS（或 SS）| Chip Select（Slave Select）| Master → Slave | 低電位有效，選擇哪個 slave |

CS 是 active-low（低電位啟用）。原因：拉高 CS 線只需要 pull-up 電阻，斷電時自動 deselect，避免誤觸發。

---

## 完整 SPI 傳輸時序圖

以 Mode 0（CPOL=0, CPHA=0），傳送 8-bit 資料 `0xA5`（`10100101b`）為例：

```
CS    ‾‾‾‾|_____________________________________________|‾‾‾‾
            ^                                         ^
            CS 下降沿，選中 slave                      CS 上升沿，釋放 slave

SCLK  ‾‾‾‾|__|‾‾|__|‾‾|__|‾‾|__|‾‾|__|‾‾|__|‾‾|__|‾‾‾‾
            ^ ^  ^ ^  ^ ^  ^ ^  ^ ^  ^ ^  ^ ^  ^ ^
            | |  | |  | |  | |  | |  | |  | |  | |
            驅動  採樣  驅動  採樣 ...（共 8 個 clock cycle）

MOSI  ‾‾‾‾|_________|‾‾‾|_______|‾‾‾|___|‾‾‾|_|‾‾‾‾‾‾
              bit7=1  bit6=0 bit5=1 bit4=0 bit3=0 bit2=1 bit1=0 bit0=1
              (MSB first)

MISO  ...（slave 同時送出回應，格式相同）
```

關鍵點：
1. CS 下降後，MOSI 先準備好 bit7（MSB）
2. SCLK 上升沿（第一個 clock 的上升沿）：master 和 slave 都採樣對方的資料
3. SCLK 下降沿：雙方切換到下一個 bit
4. 8 個 clock 後，CS 拉高，傳輸結束

---

## CPOL / CPHA 四種模式

CPOL（Clock Polarity，時脈極性）和 CPHA（Clock Phase，時脈相位）各有兩個值，組合出 4 種 Mode：

```
CPOL=0：SCLK idle 狀態為 low（不傳輸時 SCLK = 0）
CPOL=1：SCLK idle 狀態為 high（不傳輸時 SCLK = 1）

CPHA=0：在 SCLK 的第一個沿（leading edge）採樣
CPHA=1：在 SCLK 的第二個沿（trailing edge）採樣
```

### Mode 0（CPOL=0, CPHA=0）—— 最常見

```
CS    ‾‾‾|_________________________________|‾‾‾

SCLK  _____|‾|_|‾|_|‾|_|‾|_|‾|_|‾|_|‾|_|___
           ^ ^ ^ ^ ...（上升沿採樣，下降沿驅動）
           採 驅 採 驅

MOSI  ____XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
      bit7  bit6  bit5  bit4  bit3  bit2  bit1  bit0
```

idle = 0，上升沿採樣（leading = rising edge），資料在下降沿前穩定。

### Mode 1（CPOL=0, CPHA=1）

```
SCLK  _____|‾|_|‾|_|‾|_|‾|_|‾|_|‾|_|‾|_|___
           ^ ^ ^ ^ ...（下降沿採樣，上升沿驅動）
           驅 採 驅 採
```

idle = 0，下降沿採樣（trailing = falling edge）。

### Mode 2（CPOL=1, CPHA=0）

```
SCLK  ‾‾‾‾‾|_|‾|_|‾|_|‾|_|‾|_|‾|_|‾|_|‾‾‾‾‾
            ^ ^ ^ ^ ...（下降沿採樣，上升沿驅動）
```

idle = 1，下降沿採樣（leading = falling edge when CPOL=1）。

### Mode 3（CPOL=1, CPHA=1）

```
SCLK  ‾‾‾‾‾|_|‾|_|‾|_|‾|_|‾|_|‾|_|‾|_|‾‾‾‾‾
              ^ ^ ^ ^ ...（上升沿採樣，下降沿驅動）
              採 驅 採 驅
```

idle = 1，上升沿採樣（trailing = rising edge when CPOL=1）。

### 四種模式對比表

| Mode | CPOL | CPHA | Idle SCLK | 採樣沿 | 常見用途 |
|------|------|------|-----------|--------|---------|
| 0 | 0 | 0 | Low | 上升沿 | SX1276 LoRa、SD card、多數感測器 |
| 1 | 0 | 1 | Low | 下降沿 | 部分 ADC |
| 2 | 1 | 0 | High | 下降沿 | 少數 display controller |
| 3 | 1 | 1 | High | 上升沿 | MAX6675 溫度感測器、部分 DAC |

Mode 0 是實際上的預設，datasheet 沒特別說明的話十之八九是 Mode 0。

---

## Full-duplex vs Half-duplex vs Simplex

SPI 天生是 full-duplex（全雙工）協議：每個 clock cycle，master 送一個 bit（MOSI），slave 也同時回一個 bit（MISO）。兩條資料線獨立，不干擾。

```
Full-duplex（標準 SPI）：
  MOSI: →→→→→→→→  (master to slave)
  MISO: ←←←←←←←←  (slave to master)
  同時進行，4 條線

Half-duplex（省線，共用一條 IO）：
  SDA/SDIO: →→→→  then  ←←←←
  不能同時收發，需要控制 IO 方向，3 條線（SCLK + CS + SDIO）
  常見於 SSD1306 OLED（僅 TX，MISO 直接不接）

Simplex（單向，最簡）：
  只有 MOSI 或只有 MISO，資料流單方向
  常見：WS2812B LED strip（只有 data in，沒有回應）
```

---

## Multi-slave：CS 信號的角色

多個 slave 共用 SCLK / MOSI / MISO，每個 slave 有獨立的 CS 線：

```
Master           Slave A
  SCLK ─────────── SCLK
  MOSI ─────────── MOSI
  MISO ─────────── MISO
  CS_A ─────────── CS
                
             ──── Slave B
                   SCLK
                   MOSI
                   MISO
  CS_B ─────────── CS
```

同一時間只能啟動一個 slave 的 CS（拉低）。如果同時拉低兩個 CS，兩個 slave 會同時把資料丟到 MISO，造成總線衝突。

Daisy-chain 接法（菊鏈）是另一種多 slave 方案：只用一個 CS，slave 的 MISO 接到下一個 slave 的 MOSI，資料一路串接。優點：少用 GPIO。缺點：傳輸長度固定，寫入時 slave 才知道自己的資料，延遲高。74HC595 shift register 就是這樣用。

---

## 速度限制

SPI 沒有協議定義最高速率，實際限制來自：

| 限制因素 | 說明 |
|---------|------|
| Slave 的最高 SCLK | 看 datasheet（例如 SX1276 最高 10 MHz） |
| PCB 走線長度 | 走線愈長，寄生電容愈大，邊緣抖動愈嚴重 |
| GPIO matrix 延遲 | ESP32 GPIO matrix 加幾十 ns，10 MHz 以上建議用 IO_MUX 直通 |
| Logic level 轉換 | 如果 master 3.3V、slave 5V，需要電位轉換，轉換電路有頻寬限制 |

ESP32 SPI2 理論上跑到 80 MHz，但走 GPIO matrix 最高約 26 MHz，走 IO_MUX 可到 40 MHz。

---

## SPI 與 I2C 比較

| 面向 | SPI | I2C |
|------|-----|-----|
| 信號線數 | 4 條（CS、SCLK、MOSI、MISO） | 2 條（SCL、SDA） |
| 速率 | 1–80 MHz（典型 1–10 MHz） | 100/400 kHz，Fast+ 1 MHz |
| 全雙工 | 是 | 否（半雙工） |
| 多 slave | 每個 slave 需要一條 CS | 7-bit 位址，最多 127 個 slave |
| 協議複雜度 | 低，沒有位址，沒有 ACK | 有 START/STOP/ACK/NACK |
| 拉長線 | 較好（推挽驅動） | 較差（開漏 + pull-up） |
| 短路保護 | 無（推挽互短會燒） | 有（開漏，只能拉低） |
| 典型用途 | Flash、SD、顯示器、RF 模組 | 感測器、EEPROM、RTC |

選哪個？速率要求高、全雙工、或 slave 少用 SPI；GPIO 腳稀缺、或 slave 多用 I2C。

---

## 自我檢核

- [ ] 能不看圖說出四條信號線的名稱和方向
- [ ] 能在紙上畫出 Mode 0 一個完整 byte 的時序圖（CS、SCLK、MOSI）
- [ ] 能說出 CPOL 和 CPHA 各自控制什麼
- [ ] 知道 Mode 0 和 Mode 3 的採樣沿分別是哪個沿
- [ ] 能解釋 full-duplex 的 SPI 為什麼可以同時收發
- [ ] 知道 ESP32 GPIO matrix 對 SPI 速率的影響

協議搞清楚了，下一章直接看 ESP32 SPI2 的暫存器，準備動手寫驅動。

→ [Ch 4 ESP32 SPI 暫存器](./04-esp32-spi-registers.md)
