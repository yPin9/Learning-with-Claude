# Ch 13 — CAN 協議原理

> 目標：理解 CAN（Controller Area Network）的電氣特性、Frame 結構、仲裁機制、Bit stuffing，以及錯誤偵測與容錯狀態機。這些是讀 ESP32 TWAI 暫存器之前的必備基礎。

---

## 為什麼 CAN 在汽車和工控活這麼久

早期汽車用一根線對一個感測器——煞車 ECU 要讀輪速感測器，就直接拉一條線。車子越複雜，線束越重。Bosch 在 1986 年設計 CAN，核心設計決策如下：

| 問題 | CAN 的解法 |
|------|-----------|
| 多個控制器搶同一條 bus | 非破壞性仲裁，沒有 master |
| 工廠環境噪訊大 | 差分信號（CAN_H / CAN_L），共模噪訊相消 |
| 任意節點故障 | 錯誤計數器 + Bus Off 機制，壞節點自動退出 |
| 實時性要求 | 優先級高的訊息（低 ID）優先佔 bus |

不需要 master 是關鍵：任何節點都可以在 bus 空閒時開始發送，仲裁在電氣層完成，不需要軟體協調。

---

## 差分信號：CAN_H 和 CAN_L

CAN bus 只有兩條線。收發器把邏輯電平轉成差分信號：

```
電壓（V）
 4.5 ──── CAN_H（Dominant）
 3.5                        ──── CAN_H（Recessive）
 2.5 ──────────────────── 中點電壓 ─────────────────
 1.5                        ──── CAN_L（Recessive）
 0.5 ──── CAN_L（Dominant）

Dominant（邏輯 0）：CAN_H ≈ 3.5V，CAN_L ≈ 1.5V，差值 ≈ 2V
Recessive（邏輯 1）：CAN_H = CAN_L ≈ 2.5V，差值 ≈ 0V
```

| 狀態 | CAN_H | CAN_L | 差值 | 邏輯值 |
|------|-------|-------|------|--------|
| Dominant（顯性）| ~3.5V | ~1.5V | ~2V | 0 |
| Recessive（隱性）| ~2.5V | ~2.5V | ~0V | 1 |

**Dominant 勝出規則**：多個節點同時驅動 bus 時，只要有一個節點發 Dominant，bus 就是 Dominant。這是仲裁的物理基礎——Dominant 是線與（Wired-AND）邏輯。

---

## CAN 2.0A vs 2.0B

| 規格 | ID 長度 | 最大 ID 數 | 典型用途 |
|------|---------|-----------|---------|
| CAN 2.0A（Standard Frame）| 11-bit | 2048 | 傳統汽車 ECU |
| CAN 2.0B（Extended Frame）| 29-bit | 5.4 億 | 工控、J1939 重型車 |

Extended Frame 用 SRR（Substitute Remote Request）bit 和 IDE（Identifier Extension）bit 區分。標準 frame 的 IDE=0，擴展 frame 的 IDE=1。

---

## CAN Data Frame 完整時序

```
  SOF  ┌── Arbitration Field ──┐ ┌─ Control ─┐ ┌─── Data Field ───┐ ┌── CRC Field ──┐ ACK  EOF
   │   │  ID[10:3] │ID[2:0]│RTR│ │IDE│r0│DLC │ │  Byte0 ... ByteN │ │ 15-bit │ DEL │  │    │
  1bit  │  8 bits   │3 bits │1b │ │1b │1b│4b  │ │  0 to 8 bytes    │ │  CRC   │ 1b  │  2b  7b
        └───────────────────────┘ └───────────┘ └──────────────────┘ └───────────────┘
```

各欄位說明：

- **SOF（Start of Frame）**：1 個 Dominant bit，通知所有節點 bus 開始有人傳。
- **Arbitration Field**：ID[10:0]（11-bit）+ RTR（0=Data Frame，1=Remote Frame）。ID 越小，優先級越高。
- **Control Field**：IDE（Standard=0）+ r0（保留，固定 0）+ DLC[3:0]（資料長度，0~8）。
- **Data Field**：0 到 8 個 byte。DLC=0 時此欄位不存在。
- **CRC Field**：15-bit CRC 值 + 1-bit CRC Delimiter（Recessive）。多項式 x^15 + x^14 + x^10 + x^8 + x^7 + x^4 + x^3 + 1。
- **ACK Field**：ACK Slot（發送方送 Recessive，接收方若收到正確送 Dominant 覆蓋）+ ACK Delimiter（Recessive）。
- **EOF（End of Frame）**：7 個連續 Recessive bit。

---

## 仲裁機制：非破壞性

仲裁發生在 Arbitration Field 期間。所有節點同時發送自己的 ID，同時監聽 bus 實際電平：

```
時間軸  →  bit10  bit9  bit8  bit7  bit6  ...
Node A（ID=0x100，二進制 100000000）：  1  0  0  0  0  ...
Node B（ID=0x080，二進制 010000000）：  0  1  0  0  0  ...
Bus 實際電平：                          0  ...
                                        ↑
                    Node A 發 Recessive（1），但 bus 是 Dominant（0）
                    Node A 讀回與自己發的不同 → 輸了，立刻停止
                    Node B 發 Dominant（0），bus 也是 Dominant → 繼續
```

退出的節點變成被動監聽，等 bus 空閒（EOF + Intermission）後再重試。整個仲裁過程不破壞 bus 資料，勝者的訊息完整傳出。這就是「非破壞性」的意思。

---

## Bit Stuffing

CAN 用 NRZ（Non-Return-to-Zero）編碼，接收端靠邊緣重新同步時鐘。問題：長串相同 bit 沒有邊緣，接收端會失去同步。

規則：在 SOF 到 CRC Field 之間，連續 5 個相同 bit 後強制插入一個相反的 Stuff bit。

```
原始資料：  1  1  1  1  1  0  0  0  0  0  1
                    5 個 1                5 個 0
插入後：    1  1  1  1  1  0  0  0  0  0  0  1
                             ↑ stuff(0)       ↑ stuff(1)
```

接收端用同樣的邏輯去除 Stuff bit。如果在應該是 Stuff bit 的位置看到相同 bit（而非相反），就是 Stuff error。

CRC Delimiter、ACK、EOF 欄位不做 stuffing，這些欄位有固定格式，用 Form error 另外檢查。

---

## 四種 Frame 類型

| Frame 類型 | 特徵 | 用途 |
|-----------|------|------|
| Data Frame | RTR=0，有 Data Field | 正常資料傳輸 |
| Remote Frame | RTR=1，沒有 Data Field | 請求對方主動發送某個 ID 的 data |
| Error Frame | 6~12 個 Dominant bit + 8 個 Recessive | 通知 bus 發生錯誤 |
| Overload Frame | 結構類似 Error Frame | 接收節點要求延遲，在 Intermission 期間發送 |

Remote Frame 在現代設計裡幾乎不用——誰來回應沒有標準，多個節點可能同時回應導致混亂。

---

## 五種錯誤偵測機制

| 錯誤類型 | 偵測方式 | 能抓到什麼 |
|---------|---------|-----------|
| CRC Error | 接收端重算 CRC，與 CRC Field 比對 | Data Field 資料被翻轉 |
| Bit Error | 發送節點監聽自己發出的 bit，讀回不符 | 本節點 TX 訊號損壞 |
| Stuff Error | 連續 6 個相同 bit（違反 stuffing 規則）| 同步失敗或信號嚴重失真 |
| Form Error | 固定格式欄位收到錯誤值（CRC DEL、ACK DEL、EOF）| Frame 格式損壞 |
| ACK Error | 發送節點在 ACK Slot 讀回 Recessive | 沒有任何接收節點收到訊息 |

這五種機制相互補充：CRC 抓資料錯誤，Bit error 抓傳輸錯誤，Stuff error 抓同步問題，Form error 抓格式問題，ACK error 抓孤立節點。

---

## 錯誤容錯：TEC / REC 與三態狀態機

每個 CAN 節點維護兩個 8-bit 計數器：
- **TEC**（Transmit Error Counter，傳送錯誤計數器）
- **REC**（Receive Error Counter，接收錯誤計數器）

計數規則（簡化版）：

| 事件 | 計數器變化 |
|------|-----------|
| 發送節點偵測到錯誤 | TEC += 8 |
| 接收節點偵測到錯誤 | REC += 1 |
| 成功發送一個 frame | TEC -= 1 |
| 成功接收一個 frame | REC -= 1（最低為 0）|

狀態機：

```
       ┌──────────────────────────────┐
       │         Error Active         │  TEC ≤ 127 且 REC ≤ 127
       │  正常工作狀態                │
       │  Error Flag = 6 個 Dominant  │
       └──────────────────────────────┘
                        │ TEC > 127 或 REC > 127
                        ↓
       ┌──────────────────────────────┐
       │         Error Passive        │  仍可通訊
       │  Error Flag = 6 個 Recessive │  但發的 Error Flag 不破壞 bus
       │  重試之間需等 Suspend 時間   │  其他節點看不到它的抗議
       └──────────────────────────────┘
                        │ TEC > 255
                        ↓
       ┌──────────────────────────────┐
       │           Bus Off            │  從 bus 完全斷開
       │  停止一切收發                │
       │  恢復：128 × 11 個 Recessive │  或由軟體主動恢復
       └──────────────────────────────┘
```

Bus Off 是最終保護機制：持續出錯的節點不能無限占用 bus 資源，也不能持續污染 bus 上的訊號。

---

## 自我檢核

- [ ] 能解釋差分信號的電壓值，以及為什麼多節點同時驅動時 Dominant 一定贏
- [ ] 能從頭畫出完整 CAN 2.0A Data Frame 的每個欄位及位元寬度（不看筆記）
- [ ] 能用兩個節點同時發送不同 ID 的例子，逐 bit 說明仲裁過程
- [ ] 能說明 Bit stuffing 的觸發條件、插入位置，以及接收端怎麼處理
- [ ] 能列出五種錯誤偵測機制，各自偵測什麼情況
- [ ] 能說明 TEC/REC 計數器的增減規則，以及三個狀態的轉換條件

下一章把這些概念對應到 ESP32 的 TWAI controller 暫存器，開始動手設定 bit timing 和 acceptance filter。

→ [Ch 14 ESP32 TWAI 暫存器](./14-esp32-twai-registers.md)
