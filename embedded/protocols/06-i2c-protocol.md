# Ch 6 — I2C 協議原理

> 目標：理解 I2C（Inter-Integrated Circuit）的電氣特性與協議層行為，包含時序、位址機制、仲裁，以及在實際除錯時最容易踩到的坑。

## 兩條線的哲學

I2C 只用兩條訊號線完成多主多從的匯流排通訊：

- **SDA（Serial Data Line，串列資料線）**：傳輸資料位元
- **SCL（Serial Clock Line，串列時鐘線）**：主機驅動時鐘

兩條線都是 **open-drain（開汲極）** 輸出：驅動器只能把線拉低，不能主動拉高。線路靠 **上拉電阻（pull-up resistor）** 拉至 VCC。這個設計讓多個裝置可以掛在同一條線上，任何一個拉低都有效，不會發生兩個輸出互打的短路問題。

典型上拉值：

| 速率模式 | 最大速率 | 建議上拉電阻 |
|----------|----------|------------|
| Standard Mode | 100 kHz | 4.7 kΩ |
| Fast Mode | 400 kHz | 2.2 kΩ |
| Fast Mode Plus | 1 MHz | 1 kΩ |

上拉電阻選錯是 I2C 硬體問題的第一大來源，後面會細說。

## 完整 I2C 傳輸時序圖

以下是主機對從機位址 0x3C 執行一筆 write（寫入 1 byte 資料 0xAB）的完整波形：

```
      START                ADDRESS BYTE (0x3C = 0111100b, W=0)               ACK
        |    A6  A5  A4  A3  A2  A1  A0  W                                    |
SCL:  ‾‾\_  _‾_ _‾_ _‾_ _‾_ _‾_ _‾_ _‾_ _‾_  __________________________ _‾_ _
SDA:  ‾‾‾\__  0   1   1   1   1   0   0   0    \_________________________/ sla\_

            DATA BYTE (0xAB = 10101011b)                                    ACK  STOP
             D7  D6  D5  D4  D3  D2  D1  D0                                  |    |
SCL:  _‾_ _‾_ _‾_ _‾_ _‾_ _‾_ _‾_ _‾_ _‾_  ___________________________ _‾_ _  _‾‾‾
SDA:    1   0   1   0   1   0   1   1    \_________________________/ sla\_ /‾‾‾
```

關鍵規則：**SCL 為高電位期間，SDA 必須保持穩定**。SDA 只能在 SCL 低電位期間改變，唯一例外是 START 和 STOP condition。

用更直觀的 bit 序列表示完整幀結構：

```
[S][A6 A5 A4 A3 A2 A1 A0][R/W][ACK][D7 D6 D5 D4 D3 D2 D1 D0][ACK][P]
 ^                                ^                                   ^
START                           9th clk                             STOP
```

## START / STOP Condition

**START condition**：SCL 為高電位時，SDA 由高拉低。
**STOP condition**：SCL 為高電位時，SDA 由低拉高。

```
START:                       STOP:
SCL:  ‾‾‾‾‾‾‾‾‾‾‾‾‾         SCL:  ___‾‾‾‾‾‾‾‾‾‾‾
SDA:  ‾‾‾‾\________               SDA:  _______/‾‾‾‾‾
               ^                              ^
          SDA falls                      SDA rises
       (while SCL high)              (while SCL high)
```

**Repeated START（重複起始，Sr）**：不送 STOP 直接送另一個 START。常見於 read 操作：

```
[S][ADDR+W][ACK][REG_ADDR][ACK][Sr][ADDR+R][ACK][DATA][NACK][P]
                                 ^
                           Repeated START
                        (no STOP before this)
```

Repeated START 讓 master 在 write register 位址之後直接切換到 read 方向，中間不釋放 bus，避免被其他 master 搶走。

## ACK / NACK 機制

每傳送 8 個位元之後，第 9 個時鐘週期是 ACK/NACK slot：

- **ACK（Acknowledge，確認）**：接收方把 SDA 拉低，表示成功收到。
- **NACK（Not Acknowledge，非確認）**：接收方不動作，SDA 靠上拉保持高，表示失敗或不接受。

```
     bit8 (D0)          ACK slot
SCL: ____‾‾‾‾____   ____‾‾‾‾____
SDA: ___/  D0  \___ / ACK  \___
                    ^         ^
               receiver      line stays high = NACK
               pulls low = ACK
```

master 在 read 最後一個 byte 時要主動送 NACK，通知 slave 停止輸出，這是規範要求，不是可選項。

## 7-bit vs 10-bit 位址模式

### 7-bit 位址（最常見）

位址共 7 bit，理論 128 個，扣掉保留位址實際可用約 112 個。

```
第一個 byte 在 wire 上的格式：
 ┌────┬────┬────┬────┬────┬────┬────┬─────┐
 │ A6 │ A5 │ A4 │ A3 │ A2 │ A1 │ A0 │ R/W │
 └────┴────┴────┴────┴────┴────┴────┴─────┘
  MSB                                  LSB
  R/W: 0 = Write, 1 = Read
```

注意：I2C 規範和很多 datasheet 標示的 7-bit 位址是右對齊（不含 R/W bit）。在程式碼裡 `addr << 1 | rw` 才是 wire 上的第一個 byte。

### 10-bit 位址

用兩個 byte 傳位址，第一個 byte 固定前 5 bit 為 `11110`：

```
第一個 byte：1 1 1 1 0 A9 A8 R/W
第二個 byte：A7 A6 A5 A4 A3 A2 A1 A0
```

10-bit 模式在一般嵌入式場景幾乎不用。

### 保留位址

| 位址 | 用途 |
|------|------|
| 0x00 | General Call（廣播，所有裝置都必須回應） |
| 0x01 | CBUS 相容位址 |
| 0x04 ~ 0x07 | High-speed master code（3.4 MHz） |
| 0x78 ~ 0x7B | 10-bit 位址前導碼 |
| 0x7C ~ 0x7F | Device ID / Alert Response 等 |

寫 I2C scanner 時要跳過 0x00 和 0x78~0x7F，不然邏輯分析儀會看到一堆奇怪的 bus traffic。

## Clock Stretching（時鐘延展）

slave 可以在 SCL 的低電位期間繼續拉住 SCL，強迫 master 等待：

```
master 想在此上升：
SCL: ____‾‾‾‾‾‾‾‾‾‾‾‾‾____‾‾‾‾‾‾‾‾‾
          |<-slave holds->|
          |  SCL low here |
```

master 在釋放 SCL 之後必須確認 SCL 真的變高，不能用固定 delay 假設時序。這個機制讓需要做 ADC 轉換或 EEPROM write 的 slave 能主動要求 master 暫停。

ESP32 作為 master 時，有 `I2C_SCL_ST_TO_REG` 設定 clock stretching 最大等待時間，超過產生 timeout 中斷。

## Multi-master 仲裁

多個 master 同時發起傳輸時，I2C 採用**非破壞性仲裁（non-destructive arbitration）**：

```
Master A 送：1 1 0 1 ...
Master B 送：1 1 0 0 ...
Bus 實際：   1 1 0 ?

第 4 個 bit：
  A 送 1（SDA 高），B 送 0（SDA 低）
  Bus 被 B 拉低 = 0
  A 採樣 SDA 看到 0 ≠ 自己送的 1 → A 放棄
  B 繼續傳輸，感知不到剛才有競爭
```

規則：誰先送 0 誰贏。結果是 bus 位址較小（數值較低）的 master 自然優先，且資料沒有損壞。輸家退出後等到 STOP condition 才重試。

## 常見問題：上拉電阻選錯

上拉電阻影響波形邊緣速率，是最容易踩的硬體坑：

**電阻太大（例如 10 kΩ 用在 400 kHz）**：
- RC 充電太慢，SCL / SDA 上升沿鈍化
- 邏輯分析儀看到梯形波而非方波
- 高速時 SDA 還沒穩定就被採樣，讀到錯誤位元

**電阻太小（例如 470 Ω 在 3.3V 系統）**：
- ACK slot 時 slave 的 open-drain 驅動器需要灌更多電流才能拉低 SDA
- SDA 可能只降到 0.8V 不到 0.4V，不符合 logic low 規格
- 表現為 NACK storm：master 一直看到 NACK，但 slave 其實都在線上
- 靜態功耗上升（VCC / R 的持續電流）

計算參考（Fast Mode，slave open-drain 最大 sink current 3 mA）：

```
R_min = V_OL_max / I_OL = 0.4V / 3mA ≈ 133 Ω

R_max 受 bus 電容限制（Fast Mode 要求上升時間 ≤ 300 ns）：
R_max = t_rise / (0.8473 × C_bus)
若 C_bus = 100 pF → R_max ≈ 300ns / (0.8473 × 100pF) ≈ 3.5 kΩ
```

實際工程結論：400 kHz 用 **2.2 kΩ** 最保險，100 kHz 用 **4.7 kΩ**。

## 邏輯分析儀驗證要點

| 檢查項目 | 判斷標準 |
|----------|---------|
| 取樣率 | 100 kHz I2C 至少 4 MHz，400 kHz 至少 16 MHz |
| START condition | SDA 下降沿必須在 SCL 高電位期間 |
| ACK bit 電壓 | SDA 低於 0.4V 才算有效 ACK |
| SCL 週期均勻性 | 不均勻不代表問題，可能是 clock stretching |
| SDA 在 SCL 高期間是否穩定 | 任何抖動都是上拉或 SI 問題 |

## 自我檢核

- [ ] 能解釋為何 I2C 使用 open-drain 而不是 push-pull 輸出
- [ ] 能畫出 START、ACK、NACK、STOP 在時序圖上的位置
- [ ] 知道 7-bit 位址在 wire 上的排列方式（MSB first，R/W 在最後）
- [ ] 理解仲裁機制，能解釋哪個 master 獲勝以及為何資料不損壞
- [ ] 能根據速率和 bus 電容計算上拉電阻的合理範圍
- [ ] 知道 clock stretching 對 master 實作的要求

下一章進入 ESP32 I2C controller 的實際暫存器，把這裡學的時序概念對應到硬體設定值。

→ [Ch 7 ESP32 I2C 暫存器](./07-esp32-i2c-registers.md)
