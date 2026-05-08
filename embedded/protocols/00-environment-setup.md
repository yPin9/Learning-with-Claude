# Ch 0 — 環境建置

> 目標：在你的機器上跑起 ESP-IDF v5.x，能 build/flash/monitor，能用 OpenOCD + GDB attach，能用邏輯分析儀抓到 GPIO toggle 的方波。這關不通，後面全部免談。

---

## 安裝 ESP-IDF v5.x

### Windows

官方推薦用 ESP-IDF Tools Installer，省掉手動設 PATH 的痛苦：

1. 下載 [ESP-IDF Tools Installer](https://dl.espressif.com/dl/esp-idf/)，選最新 v5.x 版本的 `.exe`
2. 安裝時勾選「Python」和「Git」（如果系統還沒裝）
3. 安裝完會在桌面建立「ESP-IDF 5.x CMD」捷徑，之後用這個 shell，不要用一般 cmd 或 PowerShell
4. 驗證：

```
idf.py --version
# 應顯示 ESP-IDF v5.x.x
```

手動安裝（進階，可以控制路徑）：

```batch
git clone --recursive https://github.com/espressif/esp-idf.git C:\esp\esp-idf
cd C:\esp\esp-idf
git checkout v5.2.1
git submodule update --init --recursive
install.bat esp32
```

然後每次開 shell 都要 source：

```batch
C:\esp\esp-idf\export.bat
```

### Linux

```bash
sudo apt install git wget flex bison gperf python3 python3-pip \
    python3-venv cmake ninja-build ccache libffi-dev libssl-dev \
    dfu-util libusb-1.0-0
git clone --recursive https://github.com/espressif/esp-idf.git ~/esp/esp-idf
cd ~/esp/esp-idf
git checkout v5.2.1
./install.sh esp32
source ./export.sh
```

把最後一行加進 `~/.bashrc` 或 `~/.zshrc`（視你的 shell 而定）：

```bash
alias get_idf='. ~/esp/esp-idf/export.sh'
```

---

## idf.py 基本指令

```bash
# 建立新專案（從範例複製）
idf.py create-project my_project
cd my_project

# 設定目標晶片
idf.py set-target esp32

# 進 menuconfig（設 baud rate、partition table、FreeRTOS tick rate 等）
idf.py menuconfig

# 編譯
idf.py build

# 燒錄（預設 /dev/ttyUSB0，Windows 是 COMx）
idf.py -p /dev/ttyUSB0 flash

# 打開 serial monitor（115200 baud，Ctrl+] 離開）
idf.py -p /dev/ttyUSB0 monitor

# 三合一（最常用）
idf.py -p /dev/ttyUSB0 flash monitor
```

Windows 上 port 換成 `COM3`（或你的裝置管理員顯示的編號）：

```
idf.py -p COM3 flash monitor
```

---

## OpenOCD + GDB attach（JTAG via USB）

ESP32 支援透過 USB 的 JTAG（ESP32-S3 有內建，原版 ESP32 需要外接 JTAG 介面，例如 ESP-Prog）。

### 接線（ESP32 + ESP-Prog）

```
ESP-Prog          ESP32
--------          -----
TDI       -->     GPIO12
TDO       <--     GPIO15
TCK       -->     GPIO13
TMS       -->     GPIO14
GND       ---     GND
3V3       ---     3V3 (可選，若板子自供電則不接)
```

### 啟動 OpenOCD

ESP-IDF 內建 OpenOCD，不用另外裝：

```bash
# Linux
openocd -f interface/ftdi/esp32_devkitj_v1.cfg -f target/esp32.cfg

# Windows（在 ESP-IDF CMD shell 裡）
openocd -f interface/ftdi/esp32_devkitj_v1.cfg -f target/esp32.cfg
```

成功會看到：

```
Info : Listening on port 3333 for gdb connections
Info : Listening on port 6666 for tcl connections
Info : Listening on port 4444 for telnet connections
```

### GDB 連線

另開一個 terminal：

```bash
xtensa-esp32-elf-gdb build/my_project.elf
```

進 GDB 後：

```
(gdb) target remote :3333
(gdb) monitor reset halt
(gdb) load
(gdb) break app_main
(gdb) continue
```

常用 GDB 指令回顧：

| 指令 | 說明 |
|------|------|
| `info registers` | 看所有暫存器 |
| `x/4wx 0x3FF44000` | 從位址 dump 4 個 word（hex） |
| `set {int}0x3FF44004 = 0x1` | 直接寫記憶體位址 |
| `stepi` | 單步執行一條指令 |
| `backtrace` | 印 call stack |

---

## minicom / idf.py monitor 看 UART log

### idf.py monitor

最方便，內建 decode backtrace：

```bash
idf.py -p /dev/ttyUSB0 monitor
```

`Ctrl+]` 離開。`Ctrl+T` `Ctrl+H` 看 help。

如果看到亂碼，先確認 baud rate。ESP32 預設是 115200，部分板子 reset 時會先跑 74880 baud 的 bootloader log，那段亂碼是正常的。

### minicom（Linux）

```bash
sudo minicom -D /dev/ttyUSB0 -b 115200
```

`Ctrl+A Z` 進選單，`Ctrl+A X` 離開。

記得關掉 hardware flow control（minicom 預設開著，接 ESP32 時通常要關）：

```
Ctrl+A O -> Serial port setup -> F (Hardware Flow Control: No)
```

---

## 邏輯分析儀設定（Saleae Logic 2）

### 硬體連接

Saleae Logic 2 接 GND 和你要量的信號線。建議一開始把 GPIO2 設成 toggle，接到 Channel 0。

Logic 2 取樣率建議：
- 抓 UART（115200 baud）：1 MHz 以上
- 抓 I2C（400 kHz）：4 MHz 以上
- 抓 SPI（1–10 MHz）：25 MHz 以上

### 新增 Decoder

1. 打開 Logic 2，連接裝置
2. 點右上角「Analyzers」→「+」
3. 選 `UART`、`I2C`、或 `SPI`
4. 設定對應 channel 和 baud rate / clock 極性

### UART Decoder 設定

| 欄位 | 值 |
|------|----|
| TX Channel | 你接 TX 的 channel |
| Baud Rate | 115200 |
| Bits per Frame | 8 |
| Stop Bits | 1 |
| Parity Bit | None |

### I2C Decoder 設定

| 欄位 | 值 |
|------|----|
| SDA Channel | 接 SDA 的 channel |
| SCL Channel | 接 SCL 的 channel |

### SPI Decoder 設定

| 欄位 | 值 |
|------|----|
| MOSI Channel | 接 MOSI 的 channel |
| MISO Channel | 接 MISO 的 channel |
| SCLK Channel | 接 SCLK 的 channel |
| Enable Channel | 接 CS 的 channel（active low） |
| CPOL | 依你的 SPI mode 設定（Mode 0 = 0） |
| CPHA | 依你的 SPI mode 設定（Mode 0 = 0） |

---

## 第一個測試：GPIO toggle

這個測試只有一個標準：邏輯分析儀抓到方波才算通。

### 程式碼

不用 GPIO driver，直接操作暫存器：

```c
#include <stdint.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "soc/gpio_reg.h"

// GPIO_ENABLE_REG  = 0x3FF44020  (GPIO 0-31 output enable)
// GPIO_OUT_W1TS_REG = 0x3FF44008  (Write-1-to-Set)
// GPIO_OUT_W1TC_REG = 0x3FF4400C  (Write-1-to-Clear)

void app_main(void)
{
    // 開啟 GPIO2 output enable（bit 2）
    REG_SET_BIT(GPIO_ENABLE_REG, (1u << 2));

    while (1) {
        REG_WRITE(GPIO_OUT_W1TS_REG, (1u << 2));   // GPIO2 high
        vTaskDelay(pdMS_TO_TICKS(500));

        REG_WRITE(GPIO_OUT_W1TC_REG, (1u << 2));   // GPIO2 low
        vTaskDelay(pdMS_TO_TICKS(500));
    }
}
```

`GPIO_OUT_W1TS_REG`（Write-1-to-Set）和 `GPIO_OUT_W1TC_REG`（Write-1-to-Clear）是 ESP32 的 atomic set/clear 暫存器，不需要 read-modify-write，天生沒有 race condition。這個模式在後面的章節會反覆出現。

### 預期波形

```
GPIO2
  _____       _____       _____
 |     |     |     |     |     |
_|     |_____|     |_____|     |_

 <500ms> <500ms> <500ms> <500ms>
```

邏輯分析儀 Channel 0 應該看到頻率 1 Hz、占空比 50% 的方波。

如果看不到：
- 確認 GND 有接（最常犯的錯）
- 確認針腳對應正確（DevKit 板子的 GPIO2 通常有標）
- GPIO2 有時被用作 bootstrap 腳，部分板子接了 LED，仍然可以 toggle

---

## 自我檢核

- [ ] `idf.py --version` 顯示 v5.x.x
- [ ] `idf.py build` 對空白專案成功編譯
- [ ] `idf.py flash monitor` 能燒錄並看到 serial output
- [ ] OpenOCD 啟動後顯示 `Listening on port 3333`
- [ ] GDB `target remote :3333` 連線成功，能下中斷點
- [ ] Logic 2 能連到邏輯分析儀，Analyzer 設定完成
- [ ] GPIO toggle 程式燒錄後，邏輯分析儀抓到 1 Hz 方波

環境建好了，下一章看 ESP32 的硬體結構，你需要知道自己在操作什麼。

→ [Ch 1 ESP32 硬體概覽](./01-esp32-hardware-overview.md)
