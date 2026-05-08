# Ch 24 — Ethernet 原理

> 目標：理解 Ethernet frame 格式、MAC 和 PHY 的職責分工、RMII 介面信號、MDIO 管理匯流排，以及 ARP 的運作機制，為下一章 ESP32 EMAC register-level 實作打底。

---

## Ethernet Frame 格式

```
+----------+-----+----------+----------+-----------+-----------------+--------+
| Preamble | SFD | Dest MAC | Src MAC  | EtherType |    Payload      |  FCS   |
|  7 bytes | 1B  |  6 bytes |  6 bytes |  2 bytes  |  46~1500 bytes  | 4 bytes|
+----------+-----+----------+----------+-----------+-----------------+--------+

Preamble：7 個 0xAA bytes（10101010...），讓接收方同步 bit clock
SFD（Start Frame Delimiter）：0xAB（10101011），標記 frame 開始
Dest MAC：目標 MAC 位址，全 FF 為廣播
Src MAC：發送方 MAC 位址
EtherType：上層協議類型
  0x0800 = IPv4
  0x0806 = ARP
  0x86DD = IPv6
Payload：最小 46 bytes（若資料不足需 padding），最大 1500 bytes（MTU）
FCS（Frame Check Sequence）：CRC-32，由 MAC 自動計算和驗證
```

Preamble 和 SFD 由 PHY 和 MAC 自動處理，軟體填的 frame 從 Dest MAC 開始。FCS 通常也由 MAC 自動附加，不需要軟體計算。

---

## MAC vs PHY 分工

Ethernet 控制器分為兩個主要部分：

| 層 | 名稱 | 職責 |
|----|------|------|
| MAC（Media Access Control） | 數位邏輯 | Frame 封裝/解裝、FCS 計算/驗證、CSMA/CD、全雙工流量控制（Pause frame） |
| PHY（Physical Layer） | 類比電路 | 信號編碼（NRZ/Manchester/4B5B/8B10B）、差分對驅動、媒介感知、自動協商（Auto-negotiation） |

MAC 和 PHY 通常是兩個獨立晶片（也有整合版）。ESP32 內建 MAC（EMAC），但沒有 PHY，需要外接 PHY 晶片，如 LAN8720。

```
ESP32 EMAC（MAC）
      |
      | RMII 介面（7 pins）
      |
   LAN8720（PHY）
      |
      | 差分對（MDI/MDIX）
      |
  RJ-45 網路線
```

---

## MII 和 RMII

MII（Media Independent Interface）是 IEEE 802.3 定義的 MAC-PHY 標準介面，讓 MAC 不依賴 PHY 的實體層細節：

| 介面 | 資料線 | 時脈 | 備註 |
|------|--------|------|------|
| MII | 4-bit TX + 4-bit RX | 25 MHz（100Mbps）/ 2.5 MHz（10Mbps） | 16 pins，舊設計 |
| RMII（Reduced MII） | 2-bit TX + 2-bit RX | 50 MHz（統一） | 7 pins，ESP32 使用 |

RMII 信號（相對 PHY 角度命名）：

```
MAC（ESP32 EMAC）               PHY（LAN8720）
                                
  REF_CLK（50MHz）  ---------> REF_CLK（由 PHY 或外部晶振提供）
  TXD[1:0]          ---------> TXD[1:0]
  TX_EN             ---------> TX_EN
  RXD[1:0]         <--------- RXD[1:0]
  CRS_DV           <--------- CRS_DV（Carrier Sense / Data Valid）
                  
  MDC（管理時脈）   ---------> MDC
  MDIO（管理資料）  <--------> MDIO（雙向）
```

REF_CLK 通常由 LAN8720 的晶振輸出，提供給 EMAC。ESP32 RMII 的 REF_CLK 接 GPIO0（重要：GPIO0 有 bootstrap 功能，在下一章接線時注意）。

---

## RMII 傳輸時序

RMII 以 2 bits/cycle、50 MHz 傳輸，等效 100 Mbps：

```
TX 時序（MAC 發送給 PHY）：

REF_CLK  _|‾|_|‾|_|‾|_|‾|_|‾|_
TXD[1]   __|_X_X_X_X_X_X_X_X_|__   資料（每個時脈 2 bits）
TXD[0]   __|_X_X_X_X_X_X_X_X_|__
TX_EN    ___|‾‾‾‾‾‾‾‾‾‾‾‾‾‾|____   高電位期間資料有效

RX 時序（PHY 回傳給 MAC）：

REF_CLK  _|‾|_|‾|_|‾|_|‾|_|‾|_
CRS_DV   ___|‾‾‾‾‾‾‾‾‾‾‾‾‾‾|____   高電位 = 有效資料或 Carrier Sense
RXD[1:0] ___|_X_X_X_X_X_X_X|____   資料
```

---

## MDIO / MDC 管理介面

MDIO（Management Data Input/Output）是 MAC 和 PHY 之間的串行管理匯流排，用於讀寫 PHY 的設定暫存器：

- MDC：時脈，由 MAC 提供，最高 2.5 MHz
- MDIO：雙向資料，開漏極，上拉電阻

MDIO Frame 格式（22 bits address 模式）：

```
PRE(32) | ST(2) | OP(2) | PHYAD(5) | REGAD(5) | TA(2) | DATA(16)

PRE：32 個 1，同步用（preamble）
ST：00=Clause 45，01=Clause 22（ESP32 用 Clause 22）
OP：10=讀，01=寫
PHYAD：PHY 位址（0~31，LAN8720 通常 0 或 1）
REGAD：PHY 內部暫存器位址（0~31）
TA：turnaround（讀=高阻抗後 PHY 驅動，寫=10）
DATA：16 bits 資料
```

PHY 的標準暫存器（IEEE 802.3 定義）：

| 暫存器 | 位址 | 說明 |
|--------|------|------|
| BMCR | 0x00 | Basic Mode Control：auto-negotiation enable、soft reset |
| BMSR | 0x01 | Basic Mode Status：link status、速度能力 |
| PHYIDR1/2 | 0x02/03 | PHY ID（LAN8720 = 0x0007 / 0xC0F0~C0FF） |
| ANAR | 0x04 | Auto-Negotiation Advertisement：支援的速度和 duplex |
| ANLPAR | 0x05 | Auto-Negotiation Link Partner Ability |

LAN8720 特有的額外暫存器在 0x11~0x1F，但標準初始化只需要基本暫存器。

---

## ARP（Address Resolution Protocol）

IP 層知道目標 IP，但 Ethernet frame 需要填 MAC 位址。ARP 解決 IP → MAC 的映射：

```
ARP Request（廣播）：
  Src MAC = 發送方 MAC
  Dst MAC = FF:FF:FF:FF:FF:FF（廣播）
  EtherType = 0x0806（ARP）
  Payload：「誰是 192.168.1.100？我是 192.168.1.1（MAC = AA:BB:CC:DD:EE:FF）」

ARP Reply（單播）：
  Src MAC = 目標方 MAC
  Dst MAC = 請求方 MAC
  EtherType = 0x0806
  Payload：「192.168.1.100 是我，MAC = 11:22:33:44:55:66」
```

收到 Reply 後，發送方把 IP→MAC 的映射存入 ARP cache，後續不需要再廣播。ARP cache 有 TTL（Time To Live），過期重新查詢。

不需要 ARP 的情況：廣播 IP（255.255.255.255 直接用廣播 MAC）、IP multicast（224.0.0.x 對應到 01:00:5E:xx:xx:xx 的 MAC）。

---

## LAN8720 PHY 模組

LAN8720 是 Microchip（前身 SMSC）出的 10/100BASE-T RMII PHY，是 ESP32 Ethernet 應用最常見的外接 PHY：

| 規格 | 值 |
|------|----|
| 介面 | RMII |
| 速率 | 10/100 Mbps，自動協商 |
| 工作電壓 | 3.3V（I/O），1.2V（核心，內建 LDO） |
| MDIO PHY 位址 | 由 RXER/PHYAD0 腳決定（通常 0 或 1） |
| REF_CLK 來源 | 內建 50MHz 晶振，或外部提供 |
| 典型 3.3V 電流 | ~70 mA（活動）/ ~8 mA（省電） |

LAN8720 模組（拆封就有 RJ45）在 Aliexpress / 露天上有賣，帶焊好的 50MHz 晶振，連接 ESP32 只需要 RMII 7 條線 + MDC/MDIO 2 條線，共 9 條。

---

## ESP32 腳位對應（參考）

| 信號 | ESP32 GPIO | 備註 |
|------|-----------|------|
| REF_CLK | GPIO0 | Bootstrap 腳，注意開機時序 |
| TX_EN | GPIO21 | |
| TXD0 | GPIO19 | |
| TXD1 | GPIO22 | |
| RXD0 | GPIO25 | |
| RXD1 | GPIO26 | |
| CRS_DV | GPIO27 | |
| MDC | GPIO23 | |
| MDIO | GPIO18 | |

GPIO0 在 ESP32 開機時決定 boot mode（接地 = download mode，浮空/上拉 = 正常開機）。LAN8720 的 REF_CLK 在開機瞬間必須稍有延遲才輸出，通常問題不大，但若開機進 download mode 可能需要用 50Ω 電阻串聯。

---

## 自我檢核

- [ ] 能說明 Ethernet frame 從 Preamble 到 FCS 各欄位的用途
- [ ] 知道 MAC 和 PHY 各負責什麼，為什麼 ESP32 需要外接 PHY
- [ ] 能說明 RMII 和 MII 的差異（幾條線、時脈頻率）
- [ ] 知道 MDIO 是什麼，用來做什麼
- [ ] 能說明 ARP 的 Request/Reply 流程
- [ ] 知道 LAN8720 PHY 的 PHYIDR1/2 值
- [ ] 記住 ESP32 REF_CLK 用 GPIO0，這個腳有什麼要注意的

Ethernet 原理清楚後，下一章直接打 EMAC 暫存器，設定 DMA descriptor ring，發出第一個 raw Ethernet frame，Wireshark 抓包驗證。

→ [Ch 25 實作：ESP32 EMAC Register-Level](./25-emac-raw-frames.md)
