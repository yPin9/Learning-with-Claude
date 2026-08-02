# Ch 35 — SPI 竄改 TOCTOU 與 cold boot

> **目標**：理解攻擊者如何透過 SPI flash 直讀寫（in-circuit 硬體工具）繞過韌體保護；掌握 SPI TOCTOU 的時序視窗機制與廠商如何用 write protect 和量測時機對付它；理解 cold boot attack 利用 DRAM remanence 洩露記憶體金鑰的物理原理；掌握 DMA 攻擊（PCILeech / Thunderspy）如何直讀 host RAM 繞 kernel；以及上述所有手法的對抗機制（IOMMU、TME/SME、BootGuard）。本章屬 Part 6 硬體攻擊，所有硬體操作均未實測。

---

## 攻擊面概覽：為什麼 SPI Flash 是聖杯

UEFI 韌體住在主機板上的 SPI NOR flash。韌體的信任鏈從這個晶片開始：CPU reset 後第一條指令從這個晶片的映射位址取出，BootGuard / Intel ACM 的 IBB（Initial Boot Block）量測也從這裡讀。

攻擊者如果能**直接讀寫這顆 SPI flash**，信任鏈的起點就在他手上：

```
信任鏈假設：
  CPU → 讀 SPI flash → 驗 ACM / IBB → 繼續開機

攻擊者的前提假設被打破：
  SPI flash 的內容在 boot 開始前就可以被竄改 → ACM 量測的是假內容
```

更微妙的攻擊是 **TOCTOU**：不需要在 boot 前竄改，只需要在「量測完、執行前」這個時間窗口替換 SPI 內容。這在 BootGuard 的威脅模型裡比想像中更有意義。

SPI 攻擊之外，本章還討論兩個物理層攻擊：**cold boot**（DRAM 斷電後記憶體保留，金鑰被讀取）和 **DMA attack**（透過 Thunderbolt/PCIe 直接讀寫 host RAM，繞 CPU 存取控制）。這三種攻擊的共同點：都在 CPU 的保護邏輯之外作業。

---

## SPI Flash In-Circuit 讀寫

### 硬體原語

主板上的 SPI NOR flash 通常是 SOP-8 或 SOIC-8 封裝（8 腳），焊在主板上，也可以是 Winbond W25Q 系列（W25Q128、W25Q256）等常見型號。

攻擊者不需要把 flash 拆下來——可以用 **SOP8 夾（clip）** 在晶片還焊在板上時直接連線（in-circuit），透過 CH341A USB-SPI programmer 或 Raspberry Pi 的 SPI 介面讀寫。

**工具鏈（均未實測）**：

| 工具 | 用途 | 備注 |
|------|------|------|
| CH341A USB programmer | 主流低成本選擇，< $10 | 支援 3.3V / 5V，小心電壓設定 |
| SOP8 測試夾（SOIC clip）| In-circuit 夾住 flash 引腳 | 接觸不良是常見問題 |
| flashrom | 開源 SPI flash 讀寫工具 | 支援 200+ flash 型號 |
| Raspberry Pi（SPI 接線）| 替代 CH341A，更靈活 | spidev kernel driver |
| Bus Pirate | 通用硬體除錯工具，支援 SPI | 速度較慢 |

**flashrom 基本用法（未實測，概念示範）**：

```bash
# 識別目標 flash 型號
flashrom -p ch341a_spi

# 完整讀出 flash 內容（UEFI 韌體 dump）
flashrom -p ch341a_spi -r bios_backup.bin

# 寫入修改過的韌體（危險操作！）
flashrom -p ch341a_spi -w bios_modified.bin

# 只寫入特定區域（需要 layout 檔）
flashrom -p ch341a_spi -l bios.layout -i bios_region -w bios_modified.bin
```

**SPI 電氣連線（概念圖，未實測）**：

```
SOP-8 / SOIC-8 引腳佈局（Winbond W25Qxxx）：

  ┌────────┐
1 ─ /CS    VCC ─ 8  (3.3V)
2 ─ MISO   /WP ─ 7  (Write Protect，接 VCC 解除保護)
3 ─ /WP    CLK ─ 6
4 ─ GND    MOSI ─ 5
  └────────┘

CH341A 連線對應：
  /CS  → CH341A CS
  MISO → CH341A MISO（SO）
  CLK  → CH341A SCK
  MOSI → CH341A MOSI（SI）
  VCC  → CH341A 3.3V 或外部 3.3V 電源
  GND  → CH341A GND

注意：主板已接電時，CH341A 不應同時供電（電壓衝突）
     → 標準做法是主板斷電、CH341A 獨立供電給 SPI flash
```

### 為什麼 In-Circuit 讀寫有時失敗

SPI flash in-circuit 讀寫的最大問題是**總線爭用**：SPI flash 的 /CS 線可能被 PCH（Platform Controller Hub）或 BMC（Baseboard Management Controller）同時持有，造成讀寫結果不可靠。

```
典型 x86 主板 SPI 總線架構：

  CPU ────→ PCH ────→ SPI Flash
                ↑          ↑
              BMC（IPMI/iDRAC 等伺服器管理控制器）
                           │
                     in-circuit 攻擊者夾在這裡

問題：PCH 或 BMC 可能在你讀寫期間也在存取 flash
→ /CS 拉低訊號衝突，讀出 0xFF garbage
```

**對應手段**：拔除 PCH 的外部時鐘（或在特定 BIOS 設定停用 ME region 快取），讓 PCH 在 poweroff 狀態但 flash 有獨立供電。伺服器環境則需要先讓 BMC 停用 SPI 存取。

---

## SPI 軟體層保護

在討論 TOCTOU 攻擊前，需要先理解韌體保護的軟體層機制，這是 BootGuard 量測能有效還是被繞過的關鍵：

### Intel SPI Flash 保護機制

```
Intel PCH SPI Controller 的保護暫存器：

BIOS_CNTL（0xDC in LPC config space）：
  bit 5: SMM_BWP（BIOS Write Enable in SMM Only）
         = 1：只有 SMM 可以寫 SPI
  bit 1: BLE（BIOS Lock Enable）
         = 1：鎖定 BIOS_CNTL.BIOSWE 位元不可改
  bit 0: BIOSWE（BIOS Write Enable）
         = 0：禁止 OS 寫 SPI

PRx（Protected Range Registers）：
  PR0–PR4：定義 flash 的受保護範圍，設定後 BIOS region 禁寫
  一旦鎖閉，直到下次 reset 前無法改變
```

**CHIPSEC 確認 SPI 保護狀態（可在 WSL 模擬概念）**：

```bash
# CHIPSEC 檢查 SPI 保護（實際需要 root + 載入 chipsec kernel 模組）
sudo python chipsec_main.py -m common.spi_lock
sudo python chipsec_main.py -m common.bios_wp

# 預期輸出（保護到位的機器）：
# [*] BIOS region write protection is enabled
# [+] SPI Protected Ranges are set
```

---

## SPI TOCTOU：量測後替換的時序攻擊

### 核心概念

TOCTOU（Time-of-Check to Time-of-Use）在 SPI 攻擊的語境下指：

1. BootGuard ACM **量測**（check）BIOS flash 的 IBB（Initial Boot Block）
2. 量測完成，ACM 記錄測量值
3. **時間視窗**：ACM 量測完但 CPU 還沒從 BIOS 讀取並執行程式碼
4. 攻擊者在這個視窗**替換** SPI flash 內容（use）
5. CPU 執行的是替換後的惡意韌體，但 BootGuard 測量的是原始值

**這個時序視窗有多大？** 理論上存在，實際上非常小（微秒到毫秒級），而且需要物理接線。這是為什麼 TOCTOU SPI 攻擊在實際威脅模型中是「進階實驗室攻擊」而非「廣泛工具」。

### TOCTOU 時序 ASCII 圖

```
時間軸 ──────────────────────────────────────────────────────→

CPU 重置後活動：
  t=0ms   CPU Reset，PCH 啟動
  t=?ms   BootGuard ACM 載入，開始量測 SPI flash IBB
            ┌─────────────────────────────┐
            │  ACM 量測 IBB（讀 SPI）     │  ← 量測期間 SPI 繁忙，竄改會被發現
            └─────────────────────────────┘
  t=X ms  ACM 量測完成，PCR[0] 記錄 hash，ACM 報告結果給 CPU
            ↑
            │ ← 量測結束點（check 完成）
            │
  ─ ─ ─ ─ ─│─ ─ ─ ─ ─ ─ 攻擊視窗開始 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
            │
  t=X+Δ ms CPU 開始從 SPI flash 讀 BIOS 並執行
            ↑
            │ ← 執行開始（use 開始）

攻擊者動作（需要外部邏輯分析儀觸發）：
  偵測 ACM_DONE 訊號（GPIO、電流特徵）
  在 t=X 後立刻替換 SPI flash 的 IBB 部分
  CPU 讀到的是竄改後的惡意 BIOS

BootGuard 的量測結果：
  PCR[0] = hash(原始 IBB)  ← 正確的原始值
  但執行的是：惡意 BIOS    ← 不一致！

TPM Attestation 會發現此不一致，但 BIOS 已經在執行了。
```

### 為什麼這很難執行

```
困難點分析：

1. 時間精度需求：
   Δ = ACM 完成到 CPU 首次 fetch IBB 的時間差
   現代 CPU 可能 Δ < 1ms，需要 ns 級觸發精度

2. 觸發訊號：
   ACM_DONE 不是標準 GPIO，需要從電流特徵或
   PCH 的 SPI transaction 模式變化推斷

3. SPI 替換需要硬體：
   需要中間人硬體（SPI interposer）插在 PCH 和 flash 之間
   不是用 CH341A 直接夾住 flash

4. Replay 問題：
   如果替換的是整顆 flash，PCH 可能需要重新 power cycle
   如果只換部分 sector，需要知道 IBB 精確的 sector 邊界
```

### 廠商防禦：量測時機與 Write Protect

**BootGuard 對 SPI TOCTOU 的防禦邏輯**：

```
Intel BootGuard 的設計：
  → IBB 在 ACM 量測期間透過「密封讀」進行，期間 /WP 硬體鎖定
  → 量測完成後，PCH SPI Controller 把 IBB region 標為 read-only
    直到下個 BIOS_CNTL 解鎖序列
  → 若偵測到 SPI transaction 在 ACM 期間異常，ACM 停止並讓
    BootGuard 進入 shutdown 狀態（TPM event log 記錄）

SPI Write Protect（硬體層）：
  WP# 引腳（SOP-8 pin 3）：
    接 GND → flash 的 status register 可被修改（寫保護解除）
    接 VCC → flash 的 status register 鎖定，寫保護生效

  主板設計：PCH GPIO 控制 WP# 電位
    ACM 運行期間：PCH 強制 WP#=VCC（硬體寫保護）
    BIOS 更新期間：PCH 釋放 WP#（允許更新）
```

**這意味著 TOCTOU 的現實威脅模型**：

- 沒有 BootGuard（或 BootGuard 未啟用）的機器：SPI 竄改相對容易
- 啟用 BootGuard 的機器：SPI TOCTOU 的視窗存在但極難利用
- 真實攻擊更多走「BootGuard 量測前」（修改 flash，在 ACM 讀之前）或「不攻 BIOS，改攻 ME region」

---

## Cold Boot Attack：DRAM Remanence

### 物理原理

DRAM 是動態隨機存取記憶體：每個 bit 儲存在一個電容裡，電容會自然放電，所以需要每幾毫秒 refresh 一次。

**關鍵事實**：DRAM 斷電後，電容不會瞬間放電。記憶體的資料有一個「衰退時間」（decay time），在室溫下通常是：

- 開始顯著 bit flip：斷電後數秒到數十秒
- 資料完全消失：數分鐘（視溫度、電壓、DRAM 型號）

如果**降溫**（液態氮 -196°C），衰退時間大幅延長：

```
溫度 vs DRAM 資料保留時間（近似）：

  室溫 25°C：   斷電後 ~5-60 秒開始 bit flip
  冷藏  4°C：   斷電後 ~幾分鐘
  冷凍 -20°C：  斷電後 ~10-30 分鐘
  液態氮 -196°C：斷電後 ~數小時（bit flip 率 < 1%）

來源：Halderman et al. 2008 "Lest We Remember"
```

### Cold Boot Attack 完整流程（未實測）

```
攻擊前提：
  目標機器正在執行（BitLocker / LUKS 已解密，金鑰在 RAM 中）
  攻擊者有物理存取

步驟 1：降溫
  用液態氮（LN2）噴灑 DRAM 模組，迅速降至 -100°C 以下
  注意：DDR4 DIMM 拔插時要避免靜電，低溫時材料更脆

步驟 2：斷電並快速取出 DIMM
  對目標機器斷電（直接拔電）
  立刻（秒級）取出已降溫的 DIMM

步驟 3：插入攻擊者的機器
  把 DIMM 插入攻擊者準備好的另一台機器
  攻擊機器開機（或用 PXE boot 載入攻擊工具）

步驟 4：dump 記憶體
  攻擊工具（如 msramdump 或 freeze-probe）把整個 DIMM 內容 dump 到磁碟

步驟 5：金鑰提取
  用 bitleaker / aeskeyfind / findaes 在 dump 中搜尋 AES key schedule
  BitLocker 的 FVEK（Full Volume Encryption Key）在記憶體中有可識別特徵
```

**記憶體中的金鑰特徵（可用 aeskeyfind 掃描）**：

```bash
# aeskeyfind 掃描記憶體 dump，找 AES key schedule（軟體工具，可在 WSL 跑）
# 安裝
sudo apt-get install aeskeyfind

# 掃描（memory.raw 是 DRAM dump）
aeskeyfind memory.raw

# 輸出：找到的 128/256 bit AES key candidates
# 格式：key bytes + 置信度 + 在 dump 中的 offset

# findaes 也有類似功能（針對展開的 key schedule）
findaes memory.raw
```

### BitLocker / LUKS 金鑰在記憶體中的位置

```
BitLocker 記憶體金鑰布局（Windows）：

  FVEK（Full Volume Encryption Key）：256-bit AES-XTS key
    → 儲存在 lsass.exe / bitlocker driver 的記憶體中
    → 以「key schedule」形式（展開的 round key），有固定結構
    → aeskeyfind 正是搜尋這個結構

  VMK（Volume Master Key）：也在記憶體中
    → 用於加解密 FVEK
    → 比 FVEK 更穩定的目標

LUKS 記憶體金鑰布局（Linux）：
  dm-crypt 的 master key 在 kernel keyring 中
  位於 kernel heap，比 userland 更難找
  但 /proc/keys 可以顯示 key description（不顯示值）
```

### Cold Boot 的現實限制

這個攻擊在 2008 年 Princeton 的論文（Halderman et al.）發表時引起廣泛關注，但實際難度比聽起來高：

- **降溫窗口窄**：從斷電到插入攻擊機器，全程需要在幾秒到幾十秒內完成
- **DIMM 規格相容性**：攻擊者的機器要能讀 SPD 並正確初始化被移動的 DIMM
- **現代 DDR4/DDR5 的改進**：記憶體在斷電時的 "scrambling" 電路讓 bit pattern 更均勻，harder to identify key material
- **ECC 記憶體**：錯誤校正讓部分 bit flip 被修正，但攻擊仍可能成功

---

## DMA Attack：Thunderbolt / PCIe 直讀 RAM

### 原理：繞過 CPU，直接存取記憶體

DMA（Direct Memory Access）是讓外設（網卡、GPU、NVMe）不通過 CPU 直接讀寫主記憶體的機制。這是效能必需的——但如果一個惡意外設（或被攻擊者控制的外設）有 DMA 能力，它可以**直接讀取 RAM 的任意位置，包括 OS kernel 的記憶體**。

```
正常 DMA 流程：
  OS 驅動程式 → 設定 DMA descriptor（記憶體位址 + 長度）
  PCIe 設備   → 根據 descriptor 直接讀寫指定記憶體
  CPU         → 不參與傳輸（只設定和通知完成）

攻擊用 DMA：
  攻擊者的 PCIe 設備（或 Thunderbolt 卡）
    → 發送惡意 DMA request，指向任意 host 記憶體位址
    → 讀取 kernel 記憶體（含金鑰、credential、code）
    → 寫入 kernel 記憶體（修改驗簽結果、注入 shellcode）
  
  沒有 IOMMU 的系統：
    → PCIe 設備可以存取全部實體記憶體，無任何限制
```

### Thunderspy / Thunderbolt DMA（未實測）

2020 年 Björn Ruytenberg（Eindhoven University）公布的 Thunderspy 研究：

- Thunderbolt 3 的安全層（SL）機制有設計缺陷
- 攻擊者可以偽造 Thunderbolt controller firmware，讓 host 接受未授權的 Thunderbolt 設備
- 一旦設備被接受，可以透過 DMA 讀取 host 的全部 RAM

```
Thunderspy 利用鏈（概念，未實測）：

  步驟 1：物理存取目標機器
  步驟 2：打開後蓋，接觸 Thunderbolt controller（Intel JHL 系列）
  步驟 3：用 SPI programmer 讀取 Thunderbolt 控制器的 SPI flash
            → dump controller firmware
  步驟 4：修改 firmware，移除 security level check
  步驟 5：寫回修改後的 firmware
  步驟 6：用惡意 Thunderbolt 設備（或 PCILeech + 惡意固件）
            接上目標機器的 Thunderbolt port
  步驟 7：設備被接受（因控制器 firmware 被修改）
  步驟 8：發送任意 DMA request → 讀取 host RAM
```

### PCILeech：DMA 攻擊工具框架（未實測）

PCILeech 是公開的 DMA 攻擊工具，支援多種 FPGA 硬體（如 ScreamerM2、ZDMA 卡）插入目標機的 M.2 或 PCIe 槽：

```bash
# PCILeech 基本用法（需要支援的 FPGA 硬體，未實測）

# 讀取目標機全部 RAM（8GB 範圍）
pcileech dump -out memory.raw -length 0x200000000

# 在 RAM 中搜尋特定 pattern（例如 NT kernel 特徵）
pcileech search -sig "\x4d\x5a\x90\x00" -out pe_headers.txt

# 透過 DMA 直接呼叫 kernel function（極進階，需要準確偏移）
pcileech kmdload -kmd WINXP_X86

# 讀取特定 Windows 進程的記憶體
pcileech ps -pid 4  # lsass.exe
```

**PCILeech 生態系統**（軟體層可在 WSL 研究架構，不需要硬體）：

```
MemProcFS（PCILeech 的上層框架）：
  → 把 DMA dump 掛載成虛擬檔案系統
  → /proc/<pid>/heap/：進程 heap
  → /proc/<pid>/files/：打開的檔案
  → /forensic/：各種自動化分析
  → /sys/memory/：實體記憶體映射

研究用途：
  → 不需要 Thunderbolt，可以用 memory dump file 跑 MemProcFS
  → 分析 Windows 記憶體的工具，不限於攻擊
```

### DMA + Cold Boot 的組合思路

```
進階攻擊鏈（未實測，純概念）：

  目標：一台執行 BitLocker 的筆電，螢幕鎖定（OS 登入但鎖屏）

  選項 A（Cold Boot）：
    液態氮 → 拔 DIMM → 插攻擊機 → aeskeyfind
    需要：物理接觸 DIMM + 拆機 + 攻擊機 + 低溫工具

  選項 B（DMA via Thunderbolt）：
    PCILeech 設備插 Thunderbolt port → dump RAM
    需要：PCILeech FPGA 硬體（$100-$300） + Thunderbolt port

  選項 B 門檻更低（不需要拆機），且速度更快（RAM dump in seconds）
  選項 A 適用於目標機沒有 Thunderbolt 的情況
```

---

## 對抗手段

### IOMMU / VT-d：硬體地址空間隔離

IOMMU（Input/Output Memory Management Unit）是 DMA 攻擊最重要的防禦機制。Intel 稱之為 VT-d（Virtualization Technology for Directed I/O），AMD 稱之為 AMD-Vi。

```
IOMMU 工作原理：

  沒有 IOMMU：
    PCIe 設備 → 發出 DMA request 到實體位址 0x1234000
    記憶體控制器 → 直接服務請求，不問是否授權
    → 任何 PCIe 設備可以讀寫所有記憶體

  有 IOMMU：
    PCIe 設備 → 發出 DMA request 到「設備看到的」位址 0x1234000
    IOMMU → 查 page table（每個 PCIe 設備有自己的映射）
           → 若 0x1234000 不在允許範圍，IOMMU 拒絕並產生 fault
           → 若允許，翻譯成實體位址後才發給記憶體控制器

  效果：
    惡意 PCIe 設備只能存取 OS 明確分配給它的記憶體範圍
    無法讀取 kernel 記憶體、其他進程記憶體、金鑰
```

**Linux IOMMU 配置**（可在 WSL 參考概念）：

```bash
# 確認 IOMMU 是否啟用（在真實 Linux 機器上）
dmesg | grep -i iommu
# 預期：AMD-Vi: IOMMU enabled / DMAR: IOMMU enabled

# 確認 VT-d 在 grub 啟用
cat /proc/cmdline | grep iommu
# 應該看到：intel_iommu=on 或 amd_iommu=on

# 查 IOMMU group（每個 group 的設備共享 page table）
ls /sys/kernel/iommu_groups/
for g in /sys/kernel/iommu_groups/*; do
  echo "Group $(basename $g):"; ls $g/devices/; done

# 查特定設備的 IOMMU 映射（需要 root）
cat /sys/bus/pci/devices/0000:00:1d.0/iommu_group/type
```

**Thunderbolt 的 IOMMU 問題**：

```
Thunderbolt 的 DMA 攻擊能成功，部分原因是：

  → 早期 Thunderbolt（TB 1/2/3）在設備插入時，OS 有窗口期
    沒有把 IOMMU 保護立刻套用到新設備
  → TB 的認證機制在 SL2/SL3 下是軟體實作，可被修改 firmware 繞過
  → Windows 預設不開啟完整的 Thunderbolt DMA 保護（需要手動設定）

Windows 對策（Kernel DMA Protection）：
  設定 → 裝置安全性 → 核心 DMA 保護
  需要：Windows 10 1803+、韌體支援、IOMMU 啟用
  啟用後：Thunderbolt 設備在認證通過前不給 DMA 存取
```

### Memory Encryption：TME / SME

即使 IOMMU 被繞過，DRAM 加密可以讓竊取的記憶體內容無法使用：

| 技術 | 全名 | 平台 | 說明 |
|------|------|------|------|
| TME | Total Memory Encryption | Intel（Ice Lake+） | CPU 上 AES-128-XTS 全記憶體加密，key 在 boot 時由硬體生成 |
| SME | Secure Memory Encryption | AMD（EPYC Zen+） | 類似 TME，可選擇性啟用（C-bit per page） |
| sEME | Secure Encrypted Virtualization Memory Encryption | AMD EPYC | VM 記憶體加密，hypervisor 也看不到明文 |
| MKTME | Multi-Key Total Memory Encryption | Intel | 每個 VM / 進程可用不同 AES key |

```
TME 對 Cold Boot 的防禦效果：

  沒有 TME：
    DRAM 存的是明文（或 AES-XTS 的密文，key 也在記憶體中）
    Cold boot dump → aeskeyfind → 找到 BitLocker key → 解密磁碟

  有 TME：
    DRAM 存的是 AES-128-XTS 加密後的密文
    加密 key 在 CPU 的 Key Encryption Engine（KEE）中，從不離開晶片
    Cold boot dump → 看到的是密文 → 沒有 CPU 就算有 key material 也無用
    → Cold boot 失效

  注意：TME 保護的是「離開 CPU 的那一段路（CPU→DRAM）」
       若攻擊者能讀 CPU 暫存器（例如透過 SMM），TME 不保護
```

### BootGuard 對 SPI 的量測防禦

```
Intel BootGuard 的完整 SPI 保護架構：

  1. IBB Hash 在 Key Manifest 中（燒入 PCH fuse）
     → 攻擊者不能修改 Key Manifest（fuse 是 OTP）
     → 不能替換 Key Manifest 的內容

  2. ACM 驗 Key Manifest signature（BIOS Guard Key）
     → Key 由 Intel 燒入 CPU fuse，ACM 直接讀 CPU fuse
     → 攻擊者無法偽造 BIOS Guard Key

  3. ACM 量測 IBB → 比對 Key Manifest 裡的 hash
     → 量測期間 SPI Write Protect 硬體啟用（PCH GPIO 控制）

  4. 量測後，PCH 對 BIOS region 設定 Read-Only（PR register）
     → OS / DXE driver 無法寫 BIOS region

  整條鏈上，SPI TOCTOU 的唯一視窗：
    ACM 量測完後 → CPU 第一次 fetch IBB 前
    這個視窗由 PCH 的「IBB region cache」進一步壓縮
    → 現代平台幾乎沒有可利用的 TOCTOU 視窗
```

---

## 踩雷

1. **SPI in-circuit 讀到全 0xFF 不是成功是失敗**：全 0xFF 是 flash 擦除後的狀態，或者是讀寫總線衝突造成的無效資料。確認 CH341A 接線正確、主板完全斷電、VCC 穩定在 3.3V 後再試。

2. **Cold boot 攻擊的液態氮用量遠比想像中多**：一個 DIMM（140mm×30mm）需要降到 -100°C 以下，表面積大、熱容量大。噴一秒是不夠的，需要持續澆淋 5-10 秒，且要均勻覆蓋整個 DIMM。氮氣揮發快，戶外或通風環境操作。

3. **攻擊機不一定能正確初始化被移動的 DIMM**：現代 DDR4/DDR5 的 SPD（Serial Presence Detect）有廠商特定的 timing table，攻擊機的 BIOS 未必能識別，會卡在 MemTest 或直接不開機。需要預先確認攻擊機的相容性。

4. **IOMMU 啟用不代表 Thunderbolt DMA 就安全**：IOMMU group 的粒度有問題——同一個 IOMMU group 的所有設備共享 page table。如果 Thunderbolt controller 和一個 PCI bridge 在同一個 group，攻擊者設備可以通過 bridge 繞 IOMMU 限制。

5. **PCILeech 的 FPGA 硬體需要 PCIe x1 或 M.2 slot**：不是所有筆電都有這個 slot 暴露在外。有些 M.2 slot 是 SATA 不是 PCIe，根本不支援 DMA。插之前確認 slot 類型。

6. **TME 開啟後 cold boot dump 還是有研究價值**：即使無法直接提取 BitLocker key，dump 仍然包含加密後的 kernel 記憶體結構。結合 CPU microarchitecture 的側信道可以找到 TME key（理論，已有 2022 年 Fraunhofer 的研究展示在特定 SGX 場景）。

7. **SPI TOCTOU 的時序視窗在虛擬化環境更大**：VMM（Virtual Machine Monitor）層有 SPI 模擬，TOCTOU 視窗在某些 hypervisor 配置中可能延長到 ms 級。雲端/伺服器環境的 SPI TOCTOU 威脅模型和 client 端不同。

---

## 進階延伸

- **SPI interposer 設計**：真正的 TOCTOU 硬體攻擊需要一個「中間人」PCB 插在主板 SPI flash 和 PCH 之間，即時捕獲 SPI transaction 並替換特定 sector 的回應。這類 interposer 的設計（FPGA + SPI sniffer）在 BlackHat 議題和學術論文都有，是 Ch 34 ChipWhisperer 工具鏈的自然延伸。

- **AMD PSP 的 SPI 保護差異**：Intel 的 BootGuard 和 AMD 的 Platform Secure Boot（PSB）都依賴 SPI，但 PSB 的量測機制和 PCH 的 SPI Lock 整合方式不同，某些 AMD 平台對 TOCTOU 的防護窗口比 Intel 寬。Quarkslab 2021 年有 AMD PSP bypass 的研究值得對照。

- **冷靜思考 Cold Boot 的當代有效性**：2024 年大部分高端筆電（ThinkPad、MacBook）都有 TME/SME 或 Apple T2/M1 的統一加密記憶體，Cold Boot 的實際有效性已大幅下降。但工業控制系統、嵌入式伺服器的 DRAM 通常沒有加密，Cold Boot 在這些場景仍是有效威脅。

---

## 動手練習

本章硬體攻擊（SPI programmer、cold boot、Thunderbolt DMA）均未實測，提供以下軟體層模擬練習：

### 練習一：flashrom 紙上規劃（SPI dump 流程）

假設你有一台沒有 BootGuard 的舊主機板（如 Z97 系列），規劃以下步驟並說明每個決策點：

1. 識別主板上的 SPI flash 型號（看 silkscreen / 用 flashrom `-p ch341a_spi` 探測）
2. 確認 BIOS region 的起始位址和大小（flashrom 的 layout 功能）
3. 讀出完整 flash 備份（讀三次，用 `diff` 確認三份一致，排除讀取干擾）
4. 用 `binwalk -e bios_backup.bin` 解包，確認能找到 UEFI FV
5. 用 UEFITool 替換一個不影響開機的模組（如 logo），重新打包
6. 寫回並驗證（讀回 + diff）

說明在每個步驟中，什麼情況下應該停止操作（例如：讀三次不一致 → 接觸不良，停止勿寫）。

### 練習二：aeskeyfind 分析一個假 RAM dump

在 WSL / Linux 上執行：

```bash
# 安裝工具
sudo apt-get install aeskeyfind

# 建立一個假的 "memory dump"：包含已知 AES key 的 pattern
python3 -c "
import struct, os
# 製造一個 AES-128 key schedule（手動展開）
key = bytes([0x2b,0x7e,0x15,0x16,0x28,0xae,0xd2,0xa6,
             0xab,0xf7,0x15,0x88,0x09,0xcf,0x4f,0x3c])
# 簡單 key schedule（只是前幾個 round key 的示意）
padding = os.urandom(1024)
with open('/tmp/fake_dump.raw', 'wb') as f:
    f.write(padding)
    f.write(key * 11)  # AES-128 有 11 個 round key（真實 schedule 更複雜）
    f.write(os.urandom(1024))
"

# 嘗試找 AES key
aeskeyfind /tmp/fake_dump.raw
# 觀察：是否找到你埋入的 key？輸出格式是什麼？

# 思考：真實 BitLocker key 在 RAM 中的格式和這個有什麼不同？
```

### 練習三：IOMMU 狀態確認

在真實 Linux 機器（非 WSL）上確認 IOMMU 狀態：

```bash
# 確認 IOMMU 支援
dmesg | grep -iE "iommu|vt-d|amd-vi"

# 確認 IOMMU group 粒度（group 越多越好，粒度越細）
find /sys/kernel/iommu_groups -maxdepth 1 -type d | wc -l

# 確認 Thunderbolt 設備在哪個 IOMMU group
lspci | grep -i thunderbolt
# 然後找它的 IOMMU group
```

---

## 本章重點

- SPI flash in-circuit 讀寫（CH341A + SOP8 夾 + flashrom）是韌體 dump 與竄改的基礎工具，in-circuit 的主要問題是總線爭用
- SPI TOCTOU（量測後替換）理論存在但實際視窗極小；現代 BootGuard + PCH Write Protect 硬體組合幾乎消除這個視窗
- Cold Boot Attack 利用 DRAM remanence，液態氮降溫延長資料保留時間，aeskeyfind 可在 dump 中搜尋 AES key schedule；TME/SME 是有效對抗
- DMA attack（PCILeech / Thunderspy）繞 CPU 直讀 RAM，IOMMU/VT-d 是主要防禦機制，但 IOMMU group 粒度和 TB 的認證機制是弱點
- 本章所有硬體操作均未實測；軟體工具（aeskeyfind、flashrom 規劃）可在 WSL 研究

---

## 自我檢核

- [ ] 能畫出 SPI flash 的 SOP-8 引腳圖，說明 /WP 在攻擊時需要接到哪裡
- [ ] 能解釋 SPI TOCTOU 的時序視窗，以及 BootGuard 如何讓這個視窗幾乎為零
- [ ] 能說出 cold boot 攻擊的完整步驟（降溫 → 斷電 → 移 DIMM → dump → aeskeyfind）
- [ ] 知道 TME 加密的是哪一段路徑（CPU→DRAM），以及它為何讓 cold boot 失效
- [ ] 能解釋 IOMMU 如何隔離 PCIe 設備的 DMA 存取，以及 Thunderbolt IOMMU group 的弱點
- [ ] 理解 PCILeech 的工具定位（DMA 攻擊框架），以及 MemProcFS 可以在無硬體情況下用 dump 做什麼

---

## 延伸閱讀

1. **"Lest We Remember: Cold Boot Attacks on Encryption Keys" — Halderman et al.（USENIX Security 2008）**
   讀哪裡：原始論文（citp.princeton.edu/pub/coldboot.pdf），GitHub 上有對應工具 cold-boot-attack
   學什麼：DRAM remanence 的精確量測數據（不同溫度、廠商的 bit flip 率）、金鑰識別演算法的設計、以及為什麼 AES key schedule 有如此可識別的結構
   關聯：cold boot 理論的第一手出處，aeskeyfind 的原始論文，直接支撐本章金鑰提取段落

2. **"Thunderspy: When Lightning Strikes Thrice: Breaking Thunderbolt 3 Security" — Ruytenberg（S&P 2020 / IEEE）**
   讀哪裡：thunderspy.io 官網有完整 paper PDF 和 PoC 影片，也可在 IEEE Xplore 取得
   學什麼：Thunderbolt SL2/SL3 認證機制的具體缺陷、SPI programmer 修改 TB controller firmware 的攻擊流程、以及 Kernel DMA Protection 為何能防禦
   關聯：本章 Thunderspy 小節的第一手來源，接 Ch 36 debug 介面的「PCIe/Thunderbolt DMA over debug」主題

3. **"PLATYPUS: Software-based Power Side-Channel Attacks on x86" — Lipp et al.（S&P 2021）**
   讀哪裡：platypusattack.com，論文 PDF + PoC code
   學什麼：從軟體層（RAPL energy interface）讀取 CPU 的能耗，推算 AES 運算的 key bits——這是 TME 仍在 CPU 內部的脆弱性的具體展示；即使 TME 加密了 DRAM，CPU 內部的 side-channel 可能洩露金鑰
   關聯：TME 防禦 cold boot 的反例，說明「加密了 DRAM 不等於金鑰安全」，接 Ch 40（TPM 攻擊）的測量欺騙主題

→ [下一章](./36-debug-interfaces.md)
