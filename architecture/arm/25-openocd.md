# Ch 25 — OpenOCD 架構與 config

> 目標：吃透 OpenOCD — 它是什麼、為什麼是 ARM 嵌入式 debug 的事實標準、config 檔的層次（interface / target / board）、Tcl interface 怎麼用、GDB server 怎麼跑、常用 monitor 命令。

## OpenOCD 是什麼

**OpenOCD = Open On-Chip Debugger**，一個 **多功能的 daemon**：

- 一邊：透過 USB → 探針 → JTAG/SWD 跟 SoC 講話
- 另一邊：開 TCP port 給 GDB / Tcl client / Telnet 連線

```
┌─────────┐     TCP 3333    ┌──────────┐    USB    ┌─────┐    JTAG/SWD    ┌─────┐
│  GDB    │ ──────────────→ │ OpenOCD  │ ────────→ │Probe│ ─────────────→ │ SoC │
└─────────┘                 │          │           └─────┘                └─────┘
                            │          │
┌─────────┐     TCP 4444    │          │
│ Telnet  │ ──────────────→ │          │
└─────────┘                 │          │
                            │          │
┌─────────┐     TCP 6666    │          │
│   Tcl   │ ──────────────→ │          │
└─────────┘                 └──────────┘
```

三個 port：
- **3333**：GDB Remote Serial Protocol
- **4444**：Telnet（人類用的命令列）
- **6666**：Tcl（程式自動化）

## 啟動

```bash
openocd -f interface/stlink.cfg -f target/stm32f4x.cfg
```

兩個 `-f`：
- **interface**：探針配置（stlink、jlink、cmsis-dap、ftdi）
- **target**：晶片配置（stm32f4x、cortex_m3、imx6）

OpenOCD 啟動後印一堆 log，最後等 client 連。

## Config 檔的層次

OpenOCD 把配置切成三層：

```
interface/    探針相關（怎麼跟探針講話）
   stlink.cfg
   jlink.cfg
   cmsis-dap.cfg
   ftdi/*.cfg  (各家 FTDI module)

board/        板子相關（探針 + 晶片組合 + 額外 init）
   st_nucleo_f4.cfg
   raspberrypi-pico.cfg
   stm32f4discovery.cfg

target/       晶片相關
   stm32f4x.cfg
   nrf52.cfg
   imx6.cfg
   cortex_m3.cfg (generic)
```

慣例：用 `board/<name>.cfg` 一次配好（推薦）。或 `interface/X.cfg + target/Y.cfg` 自由組合。

`board/<name>.cfg` 內部通常 `source [find interface/...] [find target/...]` 加額外 board-specific 設定。

## 一份 stm32f4discovery.cfg 內容

```tcl
# Discovery 板子內建 ST-Link
source [find interface/stlink.cfg]

transport select hla_swd

source [find target/stm32f4x.cfg]

reset_config srst_only
```

幾行做了三件事：
1. 用 ST-Link 為探針
2. 走 SWD transport
3. 載入 STM32F4 的 target 配置（會自動偵測 chip ID、配 flash bank）

## 常用 monitor 命令

連到 OpenOCD 4444 telnet，或在 GDB 用 `monitor <cmd>`：

```
> halt              # 停 CPU
> resume            # 繼續
> step              # 單步
> reset halt        # reset 並停在 reset vector
> reg               # 看暫存器
> reg pc 0x08000000 # 寫 PC
> mdw 0x20000000 16 # 讀 16 個 word
> mww 0x40020C14 0x1000  # 寫 word
> flash write_image erase firmware.elf
> flash erase_sector 0 0 11
> arm semihosting enable
> itm port 0 on
> tpiu config internal /tmp/swo.log uart off 168000000 2000000
```

每個都對應某個 internal 操作。

## 從 GDB 用 OpenOCD

GDB 端：

```
(gdb) target remote :3333
(gdb) monitor reset halt        # 透過 GDB 執行 OpenOCD 命令
(gdb) load                      # 透過 GDB 燒 flash
(gdb) b main
(gdb) c
```

完整 startup sequence 通常進 `.gdbinit` 或 `arm-none-eabi-gdb -x init.gdb`：

```
target remote :3333
monitor reset halt
load
b main
c
```

## Flash 操作

OpenOCD 內建大量 flash driver（STM32 / nRF52 / SAMD / 各種）：

```
> flash banks
#0 : stm32f4x.flash (stm32f4x) at 0x08000000, size 0x00100000, ...
> flash erase_sector 0 0 11      # bank 0, sector 0–11
> flash write_image erase firmware.bin 0x08000000
> flash verify_image firmware.bin 0x08000000
```

對 user：等同 `st-flash write` 但 OpenOCD 一個工具搞定。

OpenOCD 還能寫 hex / elf / bin 三種格式，自動依 ELF 的 LMA 配置 flash region。

## 多核 debug

Cortex-A SoC 多核，OpenOCD 配置：

```tcl
# target.cfg 簡化
target create $_TARGETNAME.cpu0 cortex_a -dap $_CHIPNAME.dap -coreid 0
target create $_TARGETNAME.cpu1 cortex_a -dap $_CHIPNAME.dap -coreid 1
target create $_TARGETNAME.cpu2 cortex_a -dap $_CHIPNAME.dap -coreid 2
target create $_TARGETNAME.cpu3 cortex_a -dap $_CHIPNAME.dap -coreid 3

target smp $_TARGETNAME.cpu0 $_TARGETNAME.cpu1 $_TARGETNAME.cpu2 $_TARGETNAME.cpu3
```

GDB connect 後可以 `info threads` 看四核狀態，每核一個 GDB thread。

## adv：Tcl scripting

OpenOCD 的命令系統內建 **Tcl interpreter**。你可以寫 Tcl 函式自動化：

```tcl
proc dump_perf_counters {} {
    set count [mrw 0xE0001004]    # DWT->CYCCNT
    echo "Cycles: $count"
}

proc reset_and_test {} {
    reset halt
    load_image firmware.elf
    resume
    sleep 1000
    halt
    dump_perf_counters
}
```

從 telnet 4444：

```
> reset_and_test
Cycles: 168000000
```

或從 6666 Tcl port 程式化呼叫。**做 CI auto-test 嵌入式韌體必備**。

## Reset 種類

OpenOCD 支援幾種 reset：

```
reset run       # reset 後直接讓 CPU 跑
reset halt      # reset 後停在 reset vector（debug 起點）
reset init      # reset + 跑 reset-init.cfg 裡的初始化（PLL 等）
```

`reset_config` 命令配 reset 行為：

```tcl
reset_config srst_only         # 只 SRST，沒 TRST
reset_config srst_pulls_trst   # SRST 信號也 trigger TRST
reset_config trst_and_srst     # 都用
reset_config none              # 完全不 reset（CPU 從現狀 debug）
```

不同板子寫法不同 — 配錯會 reset 不乾淨或 hang。

## 常見配置陷阱

- **transport 沒選**：SWJ-DP 預設 JTAG，要 `transport select swd` 切
- **clock 太快**：`adapter speed 4000` (kHz)。SWD 通常 1-10 MHz，太快會丟 packet
- **target 沒識別**：chip ID 對不上預設值，要用 `cortex_m -dap ...` 手動配
- **flash 沒識別**：custom chip 要寫自己的 flash driver

```bash
openocd -d3 -f openocd.cfg     # debug 模式，看詳細 log
```

## probe-rs 與 pyOCD：替代品

OpenOCD 是 C 寫的、配置 Tcl 學習曲線陡。新工具：

- **probe-rs**（Rust 寫）：簡單命令列、Cargo 整合、現代 UX
- **pyOCD**（Python 寫）：CMSIS-DAP 為主、Python script 友好

兩者對 ARM Cortex-M 已經足夠用。**Cortex-A 與多核仍 OpenOCD 為王**。

```bash
# probe-rs 用法
cargo install probe-rs --features cli
probe-rs run --chip STM32F407VGTx firmware.elf
```

## 一個常見誤解

「我用 ST-Link 為什麼要 OpenOCD？ST-Link 不是有自己 driver？」

**有兩條路**：

1. **OpenOCD + ST-Link**：OpenOCD 把 ST-Link 當通用 SWD probe
2. **ST 官方 ST-LINK GDB Server**：ST 自家專屬 server

兩者都能用。差別：
- OpenOCD 跨平台、跨 chip、可 script、開源
- ST-LINK GDB Server 只 STM32、但跟 STM32CubeIDE 整合好

對學習 + 自由組合，OpenOCD 是首選。

## 自我檢核

- [ ] 我能說出 OpenOCD 三個 TCP port 各自用途
- [ ] 我能寫一個 stm32f4discovery 的 board cfg
- [ ] 我能用 monitor 命令燒 flash、reset、看 register
- [ ] 我能寫一個 .gdbinit 自動 connect + load + 跑
- [ ] 我能在 OpenOCD Tcl 寫一個自動 perf measurement script
- [ ] 我能比較 OpenOCD 與 probe-rs / pyOCD 的選擇情境

下一章看 GDB Remote Serial Protocol — OpenOCD 與 GDB 之間 wire 上長什麼樣，懂這個才能寫自己的 stub 或 debug 「why GDB 沒反應」。

→ [Ch 26 GDB Remote Serial Protocol](./26-gdb-rsp.md)
