# Ch 24 — CoreSight：DAP、ETM、ITM、TPIU

> 目標：理解 ARM CoreSight 整套 debug 與 trace 架構 — DAP（debug access port）、ETM（embedded trace macrocell）、ITM（instrumentation trace）、TPIU（trace port interface unit）、ROM table。讀懂 manual 的 CoreSight section、知道 OpenOCD config 裡那些 component name 是什麼。

## CoreSight 是什麼

ARM 從 ARMv7 起把 debug + trace 重整成一整套 IP，叫 **CoreSight**。所有 Cortex chip 都用同一套 component，差別只在數量與配置。

```
Host (PC)
   │
   ▼ JTAG / SWD
┌────────────────────────────────────────────┐
│              SoC (CoreSight)               │
│                                            │
│  ┌─── DAP ────┐                            │
│  │ DP (debug  │                            │
│  │  port)     │ ← 從外部 JTAG/SWD 進來     │
│  │ AHB-AP     │ ← 對 system bus 讀寫       │
│  │ APB-AP     │ ← 對 debug bus 讀寫        │
│  └────────────┘                            │
│        │                                   │
│        ▼                                   │
│  ┌────────────────────────────────────┐    │
│  │           Debug bus (APB)          │    │
│  └────────────────────────────────────┘    │
│        │           │           │           │
│        ▼           ▼           ▼           │
│   ┌─────────┐  ┌────────┐  ┌────────┐      │
│   │   ETM   │  │  ITM   │  │  CTI   │ ...  │
│   │ (trace) │  │(instr) │  │(cross) │      │
│   └────┬────┘  └───┬────┘  └────────┘      │
│        │           │                       │
│        ▼           ▼                       │
│      ┌──────────────────┐                  │
│      │  Trace funnel    │                  │
│      └────────┬─────────┘                  │
│               ▼                            │
│        ┌──────────────┐                    │
│        │  ETB / ETF   │  on-chip buffer    │
│        │  TPIU        │  → external pins   │
│        └──────────────┘                    │
└────────────────────────────────────────────┘
```

## DAP (Debug Access Port)

**DAP 是 host 進入 SoC 的閘道**。host 透過 JTAG / SWD 跟 DAP 講話，DAP 替 host 讀寫 SoC 的 bus。

組成：

- **DP (Debug Port)**：物理介面，JTAG-DP 或 SW-DP
- **AP (Access Port)**：DAP 內部對應到不同 bus 的橋
  - **AHB-AP**：對 system memory bus（CPU 看到的 memory）
  - **APB-AP**：對 debug peripheral bus（CoreSight components）
  - **JTAG-AP**：對其他 JTAG-style 元件

host 的命令大致：「**透過 AHB-AP 讀 0x20000000 的 32-bit**」 → DAP 把它變成一筆 AHB transaction → 讀回 → 透過 SWD 回 host。

## ROM table：發現 SoC 結構

每個 CoreSight component 有 **identification register**（CIDR / PIDR），DAP 連到後可以 enumerate。但 component 散在 debug bus 各處 — host 怎麼找到它們？

答：**ROM table**。SoC 在 debug bus 某個位址放一個 table，列出所有 CoreSight component 的位址：

```c
// 簡化版概念
struct rom_table {
    uint32_t entries[64];   // 0xFFFFFFFF 結束
    uint32_t cidr_pidr[];
};

// 每個 entry 內有：
//   bit[31:12] = component 的 PA offset
//   bit[1] = format (32/64-bit)
//   bit[0] = present
```

OpenOCD 配置如 `dap create CHIP.dap -chain-position CHIP.cpu`，啟動時 OpenOCD 會 walk ROM table 找到 ETM / ITM / CTI 的位置。

## Cortex-M 的 debug 元件

```
┌───────────────────────────┐
│        Cortex-M3 Core     │
│  ┌─────────────────┐      │
│  │ DCB (debug ctl) │      │ HALT, STEP, IRQ control
│  └─────────────────┘      │
│  ┌─────────────────┐      │
│  │    FPB          │      │ Flash Patch & Breakpoint
│  └─────────────────┘      │
│  ┌─────────────────┐      │
│  │    DWT          │      │ Data Watchpoint & Trace
│  └─────────────────┘      │ + cycle counter, ITM events
│  ┌─────────────────┐      │
│  │    ITM          │      │ Instrumentation Trace
│  └─────────────────┘      │
│  ┌─────────────────┐      │
│  │  ETM (optional) │      │ Embedded Trace Macrocell
│  └─────────────────┘      │
│  ┌─────────────────┐      │
│  │  TPIU           │      │ Trace Port Interface Unit
│  └─────────────────┘      │
└───────────────────────────┘
```

**Cortex-M3 / M4 標配 FPB + DWT + ITM**，ETM 與 TPIU 可選（看 chip）。

## ETM：instruction trace

ETM 記錄「**CPU 執行了哪些指令**」。每個 cycle 出一筆 trace data：

```
ETM packet 編碼：
  branch         "下一個分支跳哪去"
  exception      "發生例外"
  context ID     "進入哪個 process / ASID"
  cycle count    "走過幾 cycle"
```

不直接記 PC（資料量太大），而是記**控制流變化**。Host 拿 trace 配合 ELF 反組譯重建完整 instruction stream。

ETM 對抓「**為什麼程式跑到這條 wild branch**」「**performance hot spot**」極有用。Lauterbach、SEGGER J-Trace 是高階工具。

ETMv4（2013 起）支援 instruction trace 與 data trace；data trace 在 Cortex-M3 + 沒有，要 Cortex-A 才有。

## ITM：軟體 instrumentation

ITM 是讓 **程式自己寫 trace 訊息** 的機制：

```c
ITM->PORT[0].u8 = 'h';       // 寫 port 0
ITM->PORT[0].u8 = 'i';
ITM->PORT[0].u8 = '\n';
```

寫 ITM port 的 data 透過 SWO 或 TPIU 出來，host 端 OpenOCD 抓回來看。

**比 UART printf 快得多**：寫 ITM 是 register write（幾 ns），UART printf 要等 baud rate（115200 下幾百 µs）。對性能敏感 / 即時除錯不可少。

ITM 共 32 個 port，慣例：
- Port 0：printf
- Port 1–31：自定義（trace event、profiling marker、custom data）

DWT 還能配置 ITM emit「PC sample / exception entry / cycle counter」等 hardware event，不用程式碼觸發。

## TPIU：把 trace 推出晶片

trace data 要送出晶片，TPIU 負責 multiplexing + serialization：

```
ETM ──┐
      ├──[funnel]──→ TPIU ──→ SWO (1 line) 或 TRACECLK + TRACEDATA[N] (parallel)
ITM ──┘
```

TPIU 把多個 trace source（ETM、ITM、可能還有 PMU）合成一條序流，出 SWO 或 trace pins。

OpenOCD 配 SWO：

```tcl
itm port 0 on
tpiu config internal /tmp/swo.log uart off 168000000 2000000
```

設定 SWO 速率 2 MHz、CPU clock 168 MHz、輸出到 file。OpenOCD 會配 TPIU、ITM 並 dump SWO 到檔案。

## ETB / ETF：on-chip trace buffer

trace pins 出晶片要 trace probe（貴！）。**ETB (Embedded Trace Buffer)** / **ETF (Embedded Trace FIFO)** 把 trace 暫存在 on-chip SRAM：

```
ETM/ITM ──→ TPIU ──→ ETB (32–64 KB SRAM)
                          │
                          └──[host read via DAP]──→ host
```

Host 透過 DAP 讀 ETB 內容，**不需要 trace probe**。代價：buffer 容量小，只能存幾百 ms 的 trace。對「**抓 crash 前最後幾百 µs**」夠用。

## CTI：cross-trigger interface

CTI 讓不同 CoreSight component 互相觸發：

```
某個 watchpoint 命中 → CTI 通知 ETM 開始 trace
ETM 收到 trace pause 條件 → CTI 通知 CPU halt
另一個核 halt → CTI 通知這個核也 halt（多核 sync）
```

寫 SMP debug、做 conditional trace 必用 CTI。OpenOCD 多核 config 大多有 CTI 設定。

## 跨 SoC 一致性

ARM CoreSight 厲害的地方是 **跨 SoC 一致**：所有 ARM Cortex 都用同一套 DAP / ITM / FPB / ETM 規範。OpenOCD 對任何 ARM chip 大致一份相同的 debug 邏輯，差別只在 register 位址（從 ROM table 找）。

這也是為什麼 OpenOCD 一個工具就能 debug 從 STM32 到 Cortex-A78 的 SoC — 軟體寫一次，硬體 ARM 統一規格。

## 一個常見誤解

「ETM trace 要用 Lauterbach 那種 $20k 工具才能用嗎？」

**不一定**。基本 ETM trace 透過 ETB（on-chip）或 SWO（限 ITM、不含 ETM 高頻 trace）就能用普通 OpenOCD 抓。**只有 high-throughput 連續 trace 才需要昂貴 trace probe**。

Linux perf / Coresight tracer 就是用 ETB 抓 instruction trace 做 sample profiling，普通開發板就行。

## 自我檢核

- [ ] 我能畫出 CoreSight 的整體架構圖
- [ ] 我能說出 DP 與 AP 的角色
- [ ] 我能解釋 ROM table 怎麼讓 host 發現 component
- [ ] 我能列出 Cortex-M 標配的 debug component
- [ ] 我能說出 ETM 與 ITM 的差別
- [ ] 我能解釋 ETB 為什麼能省 trace probe

下一章看 OpenOCD — 前面這些 hardware 怎麼透過 OpenOCD 抽象出來給 GDB / Tcl client 用。

→ [Ch 25 OpenOCD 架構與 config](./25-openocd.md)
