# Ch 0 — 環境搭建

> 目標：把這門課會用到的工具一次裝齊。Cortex-M 與 Cortex-A 兩條線各自有獨立的 toolchain 與 emulator，再加上一整套除錯工具。動筆前先把環境理順，不然第三章開始會撞牆。

## 工具鏈總覽

我們會用到三類工具：**toolchain**（編譯）、**emulator / 板子**（執行）、**debugger / probe**（除錯）。先看一張總表，後面挨個解釋。

```
┌─────────────────┬──────────────────────────┬──────────────────────────┐
│                 │ Cortex-M（bare-metal）   │ Cortex-A（AArch64）      │
├─────────────────┼──────────────────────────┼──────────────────────────┤
│ Toolchain       │ arm-none-eabi-gcc        │ aarch64-none-elf-gcc     │
│                 │ (newlib, no libc OS)     │ aarch64-linux-gnu-gcc    │
├─────────────────┼──────────────────────────┼──────────────────────────┤
│ Emulator        │ qemu-system-arm          │ qemu-system-aarch64      │
│                 │   -M mps2-an385          │   -M virt -cpu cortex-a72│
├─────────────────┼──────────────────────────┼──────────────────────────┤
│ 實體板（建議）  │ STM32F4 Discovery        │ Raspberry Pi 4           │
│                 │ Raspberry Pi Pico (M0+)  │ Rock 5B / Pine64         │
├─────────────────┼──────────────────────────┼──────────────────────────┤
│ 除錯探針        │ ST-Link v2/v3, J-Link    │ J-Link, FT2232 JTAG      │
│                 │ CMSIS-DAP, DAPLink       │ Raspberry Pi GPIO JTAG   │
├─────────────────┼──────────────────────────┼──────────────────────────┤
│ Debug 軟體      │ OpenOCD / probe-rs /     │ OpenOCD,                 │
│                 │ pyOCD + arm-none-eabi-gdb│ aarch64-linux-gnu-gdb    │
└─────────────────┴──────────────────────────┴──────────────────────────┘
```

## 為什麼有這麼多 toolchain 變體？

讀者第一次裝會被搞糊塗：`arm-none-eabi-gcc`、`arm-linux-gnueabihf-gcc`、`aarch64-none-elf-gcc`、`aarch64-linux-gnu-gcc` — 看起來都是「ARM 的 gcc」，到底差在哪？

**target triple** 拆開看就清楚了：`<arch>-<vendor>-<os>-<abi>`

| Triple | arch | os | 給誰用？ |
|---|---|---|---|
| `arm-none-eabi-gcc` | 32-bit ARM (AArch32) | none（裸機） | Cortex-M、bare-metal Cortex-A |
| `arm-linux-gnueabihf-gcc` | 32-bit ARM | Linux | 32-bit ARM Linux user-space（樹莓派 1/2/Zero） |
| `aarch64-none-elf-gcc` | 64-bit ARM | none | bare-metal AArch64（QEMU virt 我們會用這個） |
| `aarch64-linux-gnu-gcc` | 64-bit ARM | Linux | AArch64 Linux user-space（樹莓派 4、Graviton） |

`none` 表示「沒有 OS」，編出來的 binary 只能在裸機跑、不會 link 任何 OS 提供的 libc syscall。`linux-gnu` 會 link glibc，能用 `printf` `malloc` 跟 OS 互動。

**這門課大量用 `arm-none-eabi-gcc` 與 `aarch64-none-elf-gcc`**，因為我們在寫底層東西。Linux 變體只在偶爾示範 user-space ARM 程式時用。

## Linux（Ubuntu / Debian）

最順的環境。一條命令搞定大宗：

```bash
sudo apt install \
    gcc-arm-none-eabi          \
    gcc-aarch64-linux-gnu      \
    gdb-multiarch              \
    qemu-system-arm            \
    qemu-system-misc           \
    openocd                    \
    libnewlib-arm-none-eabi    \
    libstdc++-arm-none-eabi-newlib
```

`aarch64-none-elf-gcc` Ubuntu apt 沒有預編。從 Arm 官方下載：

<https://developer.arm.com/downloads/-/arm-gnu-toolchain-downloads>

選 `AArch64 ELF bare-metal target (aarch64-none-elf)`，解壓到 `/opt/arm/aarch64-none-elf/`，把 `bin/` 加進 `PATH`。

**注意 `gdb-multiarch`**：這是一個能 debug 多種 architecture 的 gdb。`arm-none-eabi-gdb` 也能用，但 Ubuntu 從 22.04 開始 `arm-none-eabi-gdb` 套件被拆掉，多數人用 `gdb-multiarch` 配 `set arch arm` / `set arch aarch64`。

## macOS

```bash
brew install --cask gcc-arm-embedded
brew install qemu openocd
brew install aarch64-elf-gcc aarch64-elf-gdb   # 從 osx-cross tap
```

注意 Apple Silicon Mac 本身就是 ARM，但你不會直接執行 cortex-M 韌體在 Mac 上。**toolchain 是 cross-compiler**，編出來的 binary 拿去 QEMU 或實體板跑。

## Windows

兩條路：

### 路線 A：WSL2（推薦）

裝 Ubuntu on WSL2，照 Linux 那一節做。**OpenOCD 連 USB probe 在 WSL2 上不直觀**（USB passthrough 要用 `usbipd-win`），但這個課大部分章節用 QEMU，不一定需要 USB。

```powershell
winget install -e --id Microsoft.WSL
wsl --install -d Ubuntu
```

### 路線 B：原生 Windows

- arm-none-eabi-gcc：<https://developer.arm.com/downloads/-/gnu-rm>
- QEMU for Windows：<https://qemu.weilnetz.de/>
- OpenOCD 預編：<https://github.com/xpack-dev-tools/openocd-xpack>

之後章節範例用 bash 語法，Windows 原生路線你要自己換成 PowerShell。

## 驗證安裝

跑下面這幾個命令，每個都要看到版本資訊：

```bash
arm-none-eabi-gcc --version
aarch64-none-elf-gcc --version    # 或 aarch64-linux-gnu-gcc
qemu-system-arm --version
qemu-system-aarch64 --version
openocd --version
gdb-multiarch --version
```

最簡測試 — 編一支 hello.c 給 Cortex-M：

```c
// hello.c
int main(void) { while (1); }
```

```bash
arm-none-eabi-gcc -mcpu=cortex-m3 -mthumb -nostdlib hello.c -o hello.elf
arm-none-eabi-objdump -d hello.elf | head -20
```

看到 Thumb 指令的反組譯就成功了。這個 binary 還不能跑（沒有 startup、vector table），Ch 9 會補。

## QEMU 機型選擇

QEMU 對 ARM 支援極廣，但選錯機型會卡住。本課用這幾個：

| 用途 | 命令 | 說明 |
|---|---|---|
| Cortex-M3 bare-metal | `qemu-system-arm -M mps2-an385` | Arm 官方 evaluation 板 model，有 UART、Timer |
| Cortex-A AArch64 | `qemu-system-aarch64 -M virt -cpu cortex-a72` | 通用 virtio 機型，啟動 Linux 也常用 |
| Cortex-A AArch64 + EL3 | 同上 + `-machine secure=on,virtualization=on` | 練習 B 從 EL3 降到 EL1 要用 |

`-M virt` 是 QEMU 自己定義的機型，**不對應任何真實硬體**，但 device tree、PL011 UART、GIC 都齊，是學 ARM 最方便的試驗場。

## 除錯探針：你需要哪個？

如果你只用 QEMU，跳過這節 — QEMU 內建 `-s -S` 就是 GDB server。

買實體板的話：

| 探針 | 價格區間 | 支援協定 | 建議 |
|---|---|---|---|
| ST-Link v2 clone | < $10 | SWD（限 STM32） | STM32 開發夠用，限 STM32 系列 |
| J-Link EDU mini | ~$60 | JTAG / SWD | 商用首選，OpenOCD/JLinkGDBServer 都吃 |
| CMSIS-DAP（DAPLink） | < $20 | SWD / JTAG | Arm 官方標準，pyOCD 原生支援 |
| FT2232 module | ~$15 | JTAG | 自己接線、Cortex-A JTAG 常見選 |
| Pi 4 + GPIO | 已有 | JTAG (SWJ-DP) | 最便宜方式 debug 另一片 Pi |

**新手建議**：STM32F4 Discovery 板上自帶 ST-Link v2，買一塊就同時拿到開發板與探針，是這門課首選。

## 一個常見誤解

「ARM 那麼多 cortex 型號，每個都要學嗎？」

不用。**ISA 是共通的**，差別只在週邊（NVIC 跟 GIC、TIMER 編號、私有暫存器位址）。本課心法是：**用 ISA + 架構手冊吃下大宗，週邊讀對應 SoC 的 reference manual**。學一片 STM32F4，你就摸得了 STM32F1 / F7 / H7 95% 的東西。

## 自我檢核

- [ ] 我能說出 `arm-none-eabi-gcc` 跟 `aarch64-linux-gnu-gcc` 的差別
- [ ] 我裝好了 `qemu-system-arm` 與 `qemu-system-aarch64`
- [ ] 我能跑 `arm-none-eabi-gcc --version` 且看到輸出
- [ ] 我知道 QEMU 的 `-M mps2-an385` 跟 `-M virt` 各對應哪一條線
- [ ] 我知道（如果有買板子）我手上的探針是哪種協定

下一章我們先把 ARM 的歷史與 ISA 家族梳一遍，知道現在的 Cortex-A/R/M 是怎麼演化來的，再開始啃 ISA。

→ [Ch 1 ARM 全貌](./01-arm-overview.md)
