# Ch 19 — LoRa 原理

> 目標：理解 LoRa（Long Range）的調變技術 CSS 原理、SF/BW/CR 三個核心參數的 trade-off、link budget 計算、以及 LoRaWAN 協議在其上的角色。

---

## 什麼是 LoRa

LoRa 是 Semtech 開發並持有專利的物理層調變技術。它不是協議，是一種調變方式，和 FSK、OOK 站在同一層。LoRa 的核心是 CSS（Chirp Spread Spectrum，線性調頻展頻）。

LoRaWAN 才是運行在 LoRa 物理層上的 MAC（Media Access Control）層協議，由 LoRa Alliance 維護。這兩個詞常被混用，但分層關係必須清楚：

```
+-------------------------+
|       LoRaWAN           |  <-- MAC 層：尋址、加密、ADR
+-------------------------+
|         LoRa            |  <-- 物理層：CSS 調變
+-------------------------+
|     SX1276 RF 硬體       |  <-- 射頻晶片
+-------------------------+
```

---

## CSS 原理

CSS 的核心是 chirp（線性調頻信號）。一個 chirp 就是頻率在某個頻段內線性掃描的信號：

```
up-chirp（頻率從低到高）：

頻率
^  BW_high ---------/
|                  /
|                 /
|                /
|               /
|  BW_low -----/
+---------------------> 時間
     |<-- 一個 symbol -->|

down-chirp（頻率從高到低，用於同步標頭）：

頻率
^  BW_high \
|           \
|            \
|             \
|              \
|  BW_low       \------
+---------------------> 時間
```

資料透過 chirp 的「起始頻率偏移量」編碼。SF（Spreading Factor）決定一個 symbol 週期內有多少可用的頻率起點：

- SF7：2^7 = 128 個起始位置，一個 symbol 攜帶 7 bits
- SF12：2^12 = 4096 個起始位置，一個 symbol 攜帶 12 bits

接收端解調：把收到的信號和 down-chirp 做相關運算（dechirping），頻域出現尖銳峰值，峰值頻率對應傳送的資料值。CSS 的抗干擾能力來自展頻特性：能量散布整個頻段，窄帶干擾只汙染一小部分，相關積分後 SNR（Signal-to-Noise Ratio，信噪比）大幅改善，LoRa 可在 SNR = -20dB 的環境下正確解調。

---

## 三個關鍵參數

### SF（Spreading Factor，展頻因子）

| SF  | 每 symbol chips | 攜帶 bits | 相對速率  | 覆蓋距離（相對 SF7） |
|-----|----------------|-----------|-----------|---------------------|
| 7   | 128            | 7         | 1x（最快） | 1x                  |
| 8   | 256            | 8         | 1/2       | ~1.4x               |
| 9   | 512            | 9         | 1/4       | ~2x                 |
| 10  | 1024           | 10        | 1/8       | ~2.8x               |
| 11  | 2048           | 11        | 1/16      | ~4x                 |
| 12  | 4096           | 12        | 1/32（最慢）| ~5.6x             |

SF 每加 1：速率減半，距離加 40%。原因是 symbol 持續時間翻倍，接收端積分時間加倍，等效 SNR 提升 3dB。

Symbol 持續時間：`Ts = 2^SF / BW`

### BW（Bandwidth，頻寬）

BW 決定 chirp 掃過的頻率範圍，同時決定接收雜訊頻寬：

| BW（kHz） | 相對速率 | SF12 靈敏度 | 說明 |
|-----------|---------|------------|------|
| 125       | 1x      | -137 dBm   | 最常用，最遠距離 |
| 250       | 2x      | -134 dBm   | 中距離 |
| 500       | 4x      | -131 dBm   | 短距離、較高速率 |

BW 越大：速率越快，但接收端雜訊功率也越大（雜訊功率 ∝ BW），靈敏度下降。

### CR（Coding Rate，編碼率）

CR 是前向錯誤更正（FEC，Forward Error Correction）的比例：

| CR  | 資料比率 | 額外 overhead | 抗誤能力 |
|-----|---------|--------------|---------|
| 4/5 | 80%     | +25%         | 最低    |
| 4/6 | 67%     | +50%         | 中      |
| 4/7 | 57%     | +75%         | 高      |
| 4/8 | 50%     | +100%        | 最高    |

CR 對速率影響不大（相比 SF 和 BW），但在高雜訊環境下 4/8 明顯提升可靠性。實務上 4/5 最常用，只有環境特別惡劣才調高。

---

## 接收靈敏度

LoRa 靈敏度理論計算：

```
靈敏度 = -174 + NF + 10*log10(BW) + SNR_required

NF：接收器雜訊指數，SX1276 典型 6 dB
SNR_required：SF12 ≈ -20 dB，SF7 ≈ -7.5 dB

SF12 / BW=125kHz：
  = -174 + 6 + 51 + (-20) = -137 dBm

SF7 / BW=125kHz：
  = -174 + 6 + 51 + (-7.5) = -124.5 dBm
```

與其他協議比較：

| 協議 | 典型靈敏度 |
|------|-----------|
| WiFi 802.11g | -90 dBm |
| BLE 5.0 | -93 dBm |
| Zigbee | -100 dBm |
| LoRa SF7 | -125 dBm |
| LoRa SF12 | -137 dBm |

LoRa SF12 比 WiFi 好 47dB，等效路徑損耗容忍度多出 50,000 倍。

---

## Link Budget 計算

Link budget（鏈路預算）：判斷通訊鏈路能否工作的基本計算。

```
Link Margin = EIRP - Path Loss - Receiver Sensitivity

EIRP（Equivalent Isotropically Radiated Power）：
  = Tx Power (dBm) + Antenna Gain (dBi) - Cable/Connector Loss (dB)

Friis 自由空間路徑損耗（Path Loss）：
  PL = 32.45 + 20*log10(f_MHz) + 20*log10(d_km)

Link Margin > 0 才能通，越大越有餘裕
```

實際範例（SX1276，SF12，BW=125kHz，AS923=923MHz，距離 5km）：

```
Tx Power    = +20 dBm（PA_BOOST 最大）
Antenna     = +2 dBi（1/4 波長天線）
EIRP        = 22 dBm

Path Loss   = 32.45 + 20*log10(923) + 20*log10(5)
            = 32.45 + 59.30 + 13.98 = 105.7 dB

Sensitivity = -137 dBm

Link Margin = 22 - 105.7 - (-137) = 53.3 dB
```

53dB 的餘量很充裕。實際環境有反射、建物遮擋等損耗，通常另外預留 20dB fade margin，仍有 33dB 餘裕，覆蓋 5km 完全沒問題。

---

## LoRaWAN MAC 層

LoRaWAN 在物理層之上提供：

- 設備尋址：DevAddr（4 bytes 網路內位址）、DevEUI（8 bytes 全球唯一）
- 加密：AES-128，端到端，Network Session Key 和 App Session Key 分開
- 入網：OTAA（Over-The-Air Activation）或 ABP（Activation By Personalization）
- ADR（Adaptive Data Rate）：Network Server 根據 SNR 動態調整設備的 SF 和 Tx Power

### 設備類型

| 類別 | 下行接收窗口 | 延遲特性 | 功耗 | 典型應用 |
|------|------------|---------|------|---------|
| Class A | 上行後開兩個短窗口（RX1, RX2） | 最高延遲 | 最低 | 電池感測器 |
| Class B | 定時信標同步的週期性窗口 | 中等延遲 | 中   | 智慧水錶 |
| Class C | 持續監聽（發送期間除外） | 最低延遲 | 最高 | 有外部電源設備 |

Class A 是所有設備必須實作的基礎，B 和 C 是在 A 之上的延伸。Class C 設備除了在 TX 期間外都在監聽，幾乎無延遲，但連續接收耗電。

---

## 頻段規範

| 地區 | 頻段 | 典型通道 | 最大 Tx Power |
|------|------|---------|--------------|
| EU   | 868 MHz | 868.1, 868.3, 868.5 MHz 等 | 14 dBm（ETSI） |
| US   | 915 MHz | 902.3~914.9 MHz（64 ch） | 30 dBm（FCC） |
| AS923 | 923 MHz | 923.2, 923.4 MHz 等 | 16 dBm |

台灣適用 AS923 頻段，法規由 NCC 管理。SX1276 的 RF 頻率暫存器要設定為對應頻道（下一章實作）。

---

## 無線協議比較

| 協議 | 頻段 | 最大距離 | 資料速率 | 功耗 | 拓樸 | 適用場景 |
|------|------|---------|---------|------|------|---------|
| WiFi 802.11n | 2.4/5 GHz | ~100 m | 150 Mbps | 高 | 星狀 | 影像、大量資料傳輸 |
| BLE 5.0 | 2.4 GHz | ~100 m | 2 Mbps | 低 | 點對點/網狀 | 穿戴、近場控制 |
| Zigbee | 2.4 GHz | ~100 m | 250 kbps | 極低 | Mesh | 智慧家庭、燈控 |
| LoRa | 868/915/923 MHz | ~15 km | 0.3~27 kbps | 極低 | 星狀（LoRaWAN） | IoT 長距離監控 |

LoRa 在距離與功耗的組合上沒有對手。代價是速率極低，payload 通常只有數十 bytes，不適合影像或音訊。

---

## 自我檢核

- [ ] 能用自己的話解釋 chirp 是什麼，為什麼展頻能提升抗干擾能力
- [ ] 知道 SF 從 7 調到 8 時速率和距離各如何變化
- [ ] 能說明為什麼 BW=500kHz 比 BW=125kHz 靈敏度差
- [ ] 能手算一個簡單的 link budget
- [ ] 知道 LoRa 和 LoRaWAN 的分層關係
- [ ] 知道 Class A/B/C 的接收窗口差異
- [ ] 知道台灣用哪個頻段

三個參數 SF/BW/CR 不只是理論，下一章把它們直接寫進 SX1276 暫存器，看頻率如何計算、TX/RX 流程怎麼跑。

→ [Ch 20 實作：SPI → SX1276 Register-Level LoRa 收發](./20-lora-sx1276-transceive.md)
