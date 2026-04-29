# Ch 23 — JTAG vs SWD 硬體層

> 目標：搞懂 JTAG 與 SWD 兩種除錯實體層介面 — 各幾條線、訊號協定、TAP state machine、daisy chain、為什麼 ARM 要發明 SWD。後面所有 GDB / OpenOCD / probe 都建在這層之上。

## 為什麼要除錯介面？

CPU 跑著時，你**怎麼知道 PC、怎麼讀暫存器、怎麼設斷點**？

- 軟體 debugger（gdbserver in Linux）：用 OS 機制（ptrace），需要 OS
- bare-metal / kernel debug：CPU 自己沒 stop / 看 register 的 syscall

解：**內建一條獨立的 debug interface**，直接連 CPU 的 debug 邏輯，不靠 software。**JTAG / SWD 就是這個 interface**。

## JTAG：原本是測試介面

JTAG = **Joint Test Action Group**，1990 年代訂的 IEEE 1149.1 標準。本意是「**生產線上測試 PCB 連接**」 — 用 boundary scan 確認焊點通不通。後來被 IC 廠借去做 debug。

JTAG 4 條 + 1 選擇：

```
TCK   Test Clock        Host → Target，所有訊號的時脈
TMS   Test Mode Select  Host → Target，控制 state machine
TDI   Test Data In      Host → Target，傳資料進 IC
TDO   Test Data Out     Target → Host，IC 的回傳資料
TRST  Test Reset (opt)  Host → Target，硬重置 TAP
```

5 線（含 GND）算是基本配置。pin 多但簡單可靠。

## TAP state machine

JTAG 核心是 **TAP (Test Access Port) state machine**：16 個狀態，由 TMS 控制：

```
                    Test-Logic-Reset (TLR)
                             │ TMS=0
                             ▼
                       Run-Test/Idle
                          │  TMS=1
                          ▼
              ┌────── Select-DR-Scan ──── TMS=1 ──→ Select-IR-Scan ──→ ...
              │                                      │
              │ TMS=0                                 │ TMS=0
              ▼                                       ▼
          Capture-DR                              Capture-IR
              │                                       │
              │ TMS=0                                 │ TMS=0
              ▼                                       ▼
           Shift-DR                                Shift-IR
            (loop, TMS=0)                         (loop, TMS=0)
              │                                       │
              ▼                                       ▼
            ...                                     ...
```

實務上**用 OpenOCD 你不直接寫 TAP state**，但要 debug bizarre JTAG issue 時得知道 TAP 在哪。

## Daisy chain：多 IC 接同一條 JTAG

JTAG 可以**串接多個 IC**：

```
Host ──TDI──→ Chip A ──→ Chip B ──→ Chip C ──TDO──→ Host
                              ↑共用 TCK、TMS
```

Host 把資料 shift in，每個 chip 都收到同樣 TMS / TCK 訊號，但 TDI/TDO 是串行通過。要選擇哪個 chip 受控時，其他 chip 要進 BYPASS mode（資料原樣 pass）。

實務上單 chip 的 dev board（STM32 Discovery）不需要 daisy chain，但 SoC 內部有多核時，每個核可能是 chain 上的一個 TAP。

## SWD：ARM 自己發明的 2-line 替代

ARM 看到 JTAG 4 線太多 pin（小封裝 MCU 沒 pin 浪費），2007 年發布 **SWD (Serial Wire Debug)**：

```
SWCLK   Serial Wire Clock
SWDIO   Bidirectional data line
```

**只 2 條線**。SWD 物理層是雙向序列協定：

```
Host: ──SWCLK──→ Target
       ←──SWDIO──→ (host write 或 target read，根據 packet 方向)
```

每個 packet 包含 start / read-write / address / parity / data 等欄位。Host 與 target 共用 SWDIO 線，方向用 turnaround bit 切換。

**SWD 是 ARM 專用**：x86 / RISC-V / MIPS 各有自己的 debug 介面。但因為 ARM Cortex 是 MCU 主流，SWD 變成嵌入式默認協定。

## SWJ-DP：兩者並存

很多 ARM SoC 的 debug pin 是 **SWJ-DP（Serial Wire/JTAG Debug Port）**，**同樣的 pin 既可走 JTAG 也可走 SWD**：

```
JTAG:  TCK = SWCLK pin
       TMS = SWDIO pin
       TDI、TDO 留著
SWD:   只用 SWCLK + SWDIO
       (TDI/TDO 可拿來做別的用途)
```

切換靠**特殊的 magic sequence**：host 先 toggle 50+ TCK with TMS 高，再發 16-bit 0x79E7 → target 切到 SWD mode。OpenOCD 自動處理。

## SWO：trace 輸出的單線

SWD 還有個 sister pin 叫 **SWO (Serial Wire Output)**：

```
SWCLK   ← Host
SWDIO   ↔ bidirectional
SWO     → Host (target 單向輸出，trace 用)
```

SWO 是 **target → host 的單向 async UART**，用來輸出 ITM trace（Ch 29）、ETM trace 之類。常見 baud：4 MBps、12 MBps。

## TPIU：parallel trace

更高頻 trace 走 **TPIU (Trace Port Interface Unit)**：4–32 條 trace pin 同步輸出。給高速 ETM 用，但需要昂貴 trace probe（J-Trace、Lauterbach）。一般 dev board 不用。

```
TRACECLK
TRACEDATA[0]
TRACEDATA[1]
...
TRACEDATA[31] (最寬)
```

bandwidth：32 線 × 200 MHz × 2 (DDR) = 12.8 GB/s 級。給跑全速 instruction trace 用。

## 探針硬體簡介

```
+──────── JTAG / SWD bus ─────────+
│                                 │
└──── Debug probe ──── USB ──── Host
                                  │
                            OpenOCD / pyOCD / JLinkGDBServer
```

幾種常見探針：

| Probe | 介面 | 廠商 | 軟體 |
|---|---|---|---|
| **ST-Link v2** | SWD / JTAG (限 STM32) | ST | OpenOCD, ST 工具 |
| **J-Link** | SWD / JTAG | Segger | JLinkGDBServer, OpenOCD |
| **CMSIS-DAP / DAPLink** | SWD / JTAG | ARM 標準 | OpenOCD, pyOCD |
| **FT2232 module** | JTAG | FTDI 通用 | OpenOCD |
| **Black Magic Probe** | SWD / JTAG | open-source | 內建 GDB server |

**J-Link 是最強**（速度快、穩定、各廠 chip 支援廣），但商用版貴（$400+）。**EDU 版 $60** 給學生用。

**CMSIS-DAP / DAPLink 是 ARM 推的開放標準**，未來主流。Raspberry Pi Debug Probe 基於這個。

## ARM Reset：SRST / nRESET

JTAG / SWD 通常**不直接 reset CPU**，而是讓 host 透過 debug command 控制 reset：

- **SRST (System Reset)**：硬重置整個 SoC（含 peripheral）
- **JTAG TRST**：只 reset TAP state machine
- **「reset halt」**：reset + 停在 reset vector（debug 起點）

不同探針 / chip 對 reset 行為有差異。OpenOCD 配置裡的 `reset_config srst_pulls_trst` 之類就是處理這些 quirk。

## Pin map 實戰：找對線

Cortex 標準 10-pin SWD connector：

```
    1 - VTREF (target VDD reference)
    2 - SWDIO / TMS
    3 - GND
    4 - SWCLK / TCK
    5 - GND
    6 - SWO (optional)
    7 - KEY (notch)
    8 - NC / TDI (JTAG mode)
    9 - GNDDetect
   10 - nRESET
```

板子 silkscreen 通常標示 SWD pin。**接錯方向會燒探針或 board** — 仔細看 pin 1 標記。

## 一個常見誤解

「SWD 比 JTAG 快還慢？」

兩者**clock speed 上限差不多**（10-50 MHz）。SWD 因為**雙向用一條線**，理論 throughput 比 JTAG 4 線稍低。但實際 debug session 大多 limit 在 USB 來回延遲，不在 wire 速度。

ARM 為什麼選 SWD：**省 pin，pin 是錢**（小封裝節省 2 個 pin = 晶片成本降）。debug speed 不是主要考量。

## 一些有趣的故事

- **Black Magic Probe**：open-source 探針，**自己內建 GDB server**（不需要 OpenOCD），跑在 STM32 上 debug 別的 chip。設計優雅
- **DAPLink onboard**：很多 dev board（Microbit、Pico W debug、各家 eval kit）內建 DAPLink — 一顆 USB chip 模擬 USB serial + USB MSC（拖檔燒錄）+ CMSIS-DAP，**插上 USB 就能 debug**
- **J-Link 的 license 故事**：Segger 嚴格區分 EDU / 商用 license，license server 會檢查使用情境。社群有 reverse-engineered J-Link 的故事，很 spicy

## 自我檢核

- [ ] 我能列出 JTAG 的 4 條訊號線
- [ ] 我能解釋 SWD 與 JTAG 各幾條線、為什麼 ARM 設計 SWD
- [ ] 我能說出 TAP state machine 是什麼以及 daisy chain 概念
- [ ] 我能比較 SWO 與 TPIU 的差別
- [ ] 我能說出三種常見除錯探針與差異
- [ ] 我能找到板子上的 SWD connector pin 1

下一章看 CoreSight — ARM 自己一整套 trace 與 debug IP，DAP、ETM、ITM、TPIU 全部成員。

→ [Ch 24 CoreSight：DAP、ETM、ITM、TPIU](./24-coresight.md)
