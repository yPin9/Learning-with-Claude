# 練習 B — CHIPSEC 稽核平台保護組態

> **目標**：用 CHIPSEC 對一台 x86 平台執行 common 模組群稽核，讀懂 BIOS_CNTL、SMRR、D_LCK、SPI Protected Ranges、Secure Boot、BootGuard 等暫存器的回傳值，判斷平台的韌體保護設定是否正確，並寫出一份結構化的稽核報告。

**本練習全程未實測。** CHIPSEC 需要：真實 x86 平台（非 VM，部分測試需 PCH 物理接觸）、Linux 或 Windows 的 root/Administrator 權限、CHIPSEC kernel driver 載入。QEMU/OVMF 環境僅支援部分 CHIPSEC 模組（模擬器不實作 PCH 暫存器，多數 common 模組會 FAIL 或錯誤回傳）。

所有指令、輸出範例、判讀邏輯均基於 CHIPSEC 原始碼和文件，屬於教學參考。

---

## 背景動機

CHIPSEC（Intel 開發，原 Advanced Threat Research 團隊，現開源）是目前最全面的 x86 平台韌體安全稽核框架。它透過：

- **直接讀取 PCH 暫存器**（PCI config space 存取）
- **MSR 存取**（透過 kernel driver 呼叫 RDMSR）
- **SPI flash 控制器暫存器讀取**
- **UEFI variable 讀取**

來驗證平台的韌體保護組態是否符合安全最佳實務。

它的核心價值：**每個 check 對應一個已知的攻擊手法**。FAIL 不只代表「設定不對」，更直接對應「這個漏洞可被利用」。

CHIPSEC 的 `common` 模組群涵蓋所有平台無關的共通檢查項，適合作為稽核起點。`platform` 模組群則針對特定晶片組做更深入的 check。

---

## 安裝步驟

**本段未實測，為安裝程序說明。** 建議在準備好的 Linux 測試機器或安全實驗環境執行，不建議在生產機器上安裝 kernel driver。

### Linux 安裝（推薦）

```bash
# 1. 安裝系統依賴
sudo apt-get install python3 python3-pip git linux-headers-$(uname -r)

# 2. clone CHIPSEC 原始碼
git clone https://github.com/chipsec/chipsec.git
cd chipsec

# 3. 安裝 Python 依賴
pip3 install -e .

# 4. 編譯並載入 kernel driver
# CHIPSEC 需要 kernel driver 才能存取 MSR、I/O port、PCI config
# UEFI Secure Boot 啟用時，driver 需要簽章（或停用 Secure Boot 載入）
sudo python3 chipsec/helper/linux/driver/Makefile  # 或參照官方 docs
sudo insmod chipsec/helper/linux/chipsec.ko

# 5. 驗證載入
lsmod | grep chipsec
```

**Secure Boot 的衝突**：如果目標機器啟用了 UEFI Secure Boot，CHIPSEC kernel driver 因為沒有符合 db 的簽章，載入會失敗（`insmod: ERROR: could not insert module ... Operation not permitted`）。選擇：
- 暫時停用 Secure Boot（會改變平台安全狀態，影響 Secure Boot check 的意義）
- 使用 CHIPSEC 的 DAL（Intel System Studio）後端，不需 kernel driver
- 在另一個已停用 Secure Boot 的測試機上跑

### Windows 安裝（理論）

```powershell
# 管理員 PowerShell
pip install chipsec

# CHIPSEC 會嘗試載入 WinDLL 或 kernel driver
# Win10/11 需要開啟 TestSigning 或使用已簽章的 driver
bcdedit /set testsigning on
# 重開機後執行
python chipsec_main.py
```

---

## 執行命令

### 全套 common 模組稽核

```bash
# 執行所有 common 模組（需 root，建議存 log）
sudo python3 chipsec_main.py 2>&1 | tee chipsec_full_report.txt

# 只跑 common 模組群（不跑 platform-specific）
sudo python3 chipsec_main.py -m common

# 個別模組執行（各自說明如下）
sudo python3 chipsec_main.py -m common.bios_wp        # BIOS 寫保護
sudo python3 chipsec_main.py -m common.smrr           # SMRR
sudo python3 chipsec_main.py -m common.bios_kbrd_buffer  # BIOS keyboard buffer
sudo python3 chipsec_main.py -m common.secureboot.variables  # Secure Boot
sudo python3 chipsec_main.py -m common.bootguard      # BootGuard
sudo python3 chipsec_main.py -m common.spi_lock       # SPI 鎖定
sudo python3 chipsec_main.py -m common.smm_dma        # SMM DMA 保護

# SPI flash 資訊查詢（不跑 check，只讀資訊）
sudo python3 chipsec_util.py spi info
sudo python3 chipsec_util.py spi dump spi_flash.bin  # dump SPI flash（謹慎使用）
```

---

## 範例輸出解讀

以下輸出為**擬真範例**，模擬一台「保護設定存在問題」的假設平台，逐行判讀。

### common.bios_wp 輸出範例

```
[*] running module: chipsec.modules.common.bios_wp
[*] Module path: /usr/lib/python3/dist-packages/chipsec/modules/common/bios_wp.py

[*] BIOS Control (BIOS_CNTL) register:
[*]   BDF 0:31:0 offset DC = 0xAA
[*]   [bit 0] BIOSWE (BIOS Write Enable)      = 0 (Disabled) <- 正常，BIOS 寫保護啟用
[*]   [bit 1] BLE (BIOS Lock Enable)          = 1 (Enabled)  <- 正常，鎖啟用
[*]   [bit 5] SMM_BWP (SMM BIOS Write Protect)= 0 (Disabled) <- 問題！SMM 不阻擋 BIOS 寫入

[-] FAILED: BIOS region write protection is not enabled properly
    BIOSWE=0, BLE=1 but SMM_BWP=0
    SMM code can write to SPI flash without triggering SMI
    RISK: BIOS can be overwritten from ring-0 or from SMM without SMM_BWP protection
```

**逐行判讀**：
- `BIOSWE=0`：BIOS Write Enable 位清零，表示 PCH 不允許直接寫 SPI flash。這是對的。
- `BLE=1`：BIOS Lock Enable 位設起，嘗試設定 BIOSWE=1 會觸發 SMI，讓 SMM handler 有機會做二次驗證。
- `SMM_BWP=0`：**問題所在。** SMM_BWP（bit 5）清零代表即使在 SMM 內部（Ring -2）也沒有 SPI 寫保護。只有 SMM_BWP=1 時，OS 和 ring-0 的 BIOS 寫嘗試才會被 SMM 攔截；若 SMM 本身被攻擊（Ch 13 的場景），也需要 SPI Protected Ranges 才能保護 BIOS 分區。

### common.smrr 輸出範例

```
[*] running module: chipsec.modules.common.smrr

[*] Checking SMRR range base programming:
[*]   IA32_SMRR_PHYSBASE MSR (0x1F2) = 0xAD000006
[*]   SMRR Base: 0xAD000000
[*]   SMRR Type: 6 (WB - Write Back)

[*] Checking SMRR range mask programming:
[*]   IA32_SMRR_PHYSMASK MSR (0x1F3) = 0xFF000800
[*]   SMRR Mask: 0xFF000000
[*]   SMRR Valid (bit 11) = 1 (Enabled)

[+] PASSED: SMRR protection is properly configured
    SMRAM range: 0xAD000000 - 0xADFFFFFF (16 MB)
    SMRR is active: non-SMM access to this range returns FFh
```

**逐行判讀**：
- `SMRR Base: 0xAD000000`：SMRAM 從 0xAD000000 開始
- `SMRR Type: 6 (WB)`：記憶體類型 Write Back，正確（不應是 UC/WC）
- `SMRR Valid (bit 11) = 1`：SMRR 已啟用，non-SMM 模式存取此範圍回傳 `0xFF`
- `PASSED`：平台的 SMRR 設定正確，OS 無法直接讀取 SMRAM

### common.spi_lock 輸出範例（含 Protected Ranges）

```
[*] running module: chipsec.modules.common.spi_lock

[*] SPI Flash Controller Configuration:
[*]   BIOS_HSFSTS (Hardware Sequencing Flash Status): 0x8000F800
[*]   FLOCKDN (Flash Descriptor Lock Down) = 1 (Locked)  <- 正常

[*] SPI Protected Ranges:
[*]   PR0: 0x00000000 - 0x00000000 (disabled)
[*]   PR1: 0x00700000 - 0x007FFFFF (enabled, read-only)  <- BIOS 保護範圍
[*]   PR2: 0x00000000 - 0x00000000 (disabled)
[*]   PR3: 0x00000000 - 0x00000000 (disabled)
[*]   PR4: 0x00000000 - 0x00000000 (disabled)

[*] BIOS region in SPI flash: 0x00700000 - 0x00FFFFFF

[!] WARNING: PR1 only covers partial BIOS region (0x007FFFFF < 0x00FFFFFF)
    Upper BIOS area (0x00800000 - 0x00FFFFFF) is NOT write-protected by SPI PR
    
[-] FAILED: BIOS region is not fully covered by SPI Protected Ranges
    An attacker with ring-0 access can overwrite the unprotected portion
```

**逐行判讀**：
- `FLOCKDN=1`：SPI Flash Descriptor Lock Down，表示 Flash Descriptor 本身被鎖定，無法在執行時被修改
- `PR1: 0x00700000 - 0x007FFFFF`：SPI Protected Range 1 保護了 BIOS 分區的一部分
- `WARNING`：BIOS 分區的上半段（0x00800000-0x00FFFFFF）未在任何 PR 中，攻擊者（ring-0 或清掉 BIOSWE 後）可以寫入這個範圍
- **攻擊可行性**：BIOS 分區只有部分受 SPI PR 保護時，攻擊者可選擇在未保護的範圍植入後門 DXE driver

### common.bootguard 輸出範例

```
[*] running module: chipsec.modules.common.bootguard

[*] BootGuard Configuration:
[*]   MSR_BOOT_GUARD_SACM_INFO (0x13A) = 0x0000000000000000

[!] BootGuard is NOT enabled on this platform
    MSR_BOOT_GUARD_SACM_INFO = 0: ACM not executed, no BootGuard protection

[-] FAILED: Platform does not have BootGuard enabled
    RISK: An attacker with SPI write access can replace IBB without detection
    No hardware-enforced verification of initial BIOS boot block
```

**逐行判讀**：
- `MSR_BOOT_GUARD_SACM_INFO = 0`：ACM 未執行或 BootGuard fuse 未燒
- `FAILED`：這台機器沒有 BootGuard 保護，任何能寫 SPI flash 的攻擊者可以植入惡意 IBB，且無法被偵測

### common.secureboot.variables 輸出範例

```
[*] running module: chipsec.modules.common.secureboot.variables

[*] Secure Boot Configuration:
[*]   SecureBoot variable: 0x01 (Enabled)
[*]   SetupMode   variable: 0x00 (Not in Setup Mode)

[*] PK (Platform Key): present (1 certificate)
[*] KEK (Key Exchange Key): present (2 certificates)
[*] db (Signature Database): present (87 entries)
[*] dbx (Forbidden Signatures): present (392 entries)

[+] PASSED: UEFI Secure Boot is properly configured
    PK/KEK/db/dbx all present, SecureBoot=1, SetupMode=0
```

**逐行判讀**：
- `SecureBoot=1`：Secure Boot 已啟用
- `SetupMode=0`：不在 Setup Mode（Setup Mode 下可以任意修改 PK/KEK/db）
- `db: 87 entries`：包含 Microsoft 等 OEM 簽章的信任清單
- `dbx: 392 entries`：撤銷清單，高數字代表有跟上近期的 CVE 撤銷更新（BlackLotus/BootHole 等）
- `PASSED`：Secure Boot 基本設定正確

---

## 檢查項意義與 FAIL 攻擊可行性對照表

| 模組 | 檢查暫存器 / 設定 | 正確狀態 | FAIL 代表什麼攻擊可行 |
|------|-----------------|---------|---------------------|
| `common.bios_wp` | BIOS_CNTL.BIOSWE (bit 0) | 0（寫保護啟用） | ring-0 或 SMM 可直接寫 SPI flash，植入 bootkit |
| `common.bios_wp` | BIOS_CNTL.BLE (bit 1) | 1（Lock Enable） | 寫 BIOSWE=1 不觸發 SMI，BIOS 寫保護可被輕易繞過 |
| `common.bios_wp` | BIOS_CNTL.SMM_BWP (bit 5) | 1（SMM BIOS Write Protect） | 即使從 SMM 內部也能清掉 BIOSWE，Ch 13 的 SMM shellcode 可刷 SPI |
| `common.smrr` | IA32_SMRR_PHYSMASK.Valid (bit 11) | 1（啟用） | ring-0 可直接讀取 SMRAM 內容（dump SMRAM 秘密），Ch 12-13 的攻擊更容易落地 |
| `common.spi_lock` | SPI PR0-4 覆蓋 BIOS 範圍 | BIOS 分區全部覆蓋 | 未被 PR 保護的 BIOS 分區可被 ring-0 覆寫（即使 BIOSWE=0 被清掉後） |
| `common.spi_lock` | FLOCKDN | 1（鎖定） | Flash Descriptor 可被修改，攻擊者可改變各分區的存取權限 |
| `common.bootguard` | BootGuard fuse | SACM_INFO != 0 | 攻擊者可替換 IBB，植入永久 bootkit；BootGuard 不存在 |
| `common.secureboot.variables` | SecureBoot=1, SetupMode=0 | 兩者都符合 | SetupMode=1：任何人可替換 PK/KEK，完全控制 Secure Boot 信任清單 |
| `common.secureboot.variables` | dbx 是否含最新撤銷 | 含 BlackLotus 等 CVE 撤銷條目 | 舊 dbx 讓已撤銷的 bootloader（如 BlackLotus 利用的版本）仍可通過 Secure Boot |
| `common.smm_dma` | VT-d SMRAM DMA 保護 | SMRAM 範圍加入 DMAR 保護 | DMA 攻擊可直接寫 SMRAM（Ch 11 的 DMA 攻擊向量） |
| `common.ia32cfg` | IA32_FEATURE_CONTROL lock | Lock bit=1 | 攻擊者可在 OS 中關閉 VMX/SGX 功能，或強行修改特性控制暫存器 |

---

## 稽核報告骨架

下面是一份結構化稽核報告的骨架，對應本練習的輸出。

```markdown
# 平台韌體安全稽核報告

**稽核日期**：YYYY-MM-DD  
**目標平台**：廠牌 / 型號 / BIOS 版本  
**稽核工具**：CHIPSEC vX.Y.Z  
**稽核人員**：  
**環境說明**：Linux 5.x.x，root 權限，CHIPSEC kernel driver 載入  

---

## 執行摘要

| 風險等級 | 項目數 |
|---------|-------|
| PASS    | N     |
| WARNING | N     |
| FAIL    | N     |

**整體評估**：[高風險 / 中風險 / 低風險]  
**主要問題**：條列 FAIL 項目的一行摘要

---

## 詳細結果

### 1. BIOS 寫保護（common.bios_wp）

**結果**：[PASS / FAIL]

| 暫存器 | 期望值 | 實際值 | 狀態 |
|-------|-------|-------|------|
| BIOSWE | 0 | X | PASS/FAIL |
| BLE    | 1 | X | PASS/FAIL |
| SMM_BWP| 1 | X | PASS/FAIL |

**風險說明**：[描述 FAIL 允許的攻擊手法]  
**建議修正**：[更新 BIOS / 聯繫 OEM / 在 BIOS setup 調整]

---

### 2. SMRR 保護（common.smrr）

**結果**：[PASS / FAIL]

| MSR | 期望值 | 實際值 | 狀態 |
|----|-------|-------|------|
| IA32_SMRR_PHYSBASE | 合法基址 | 0xXXXXXXXX | PASS/FAIL |
| IA32_SMRR_PHYSMASK.Valid | 1 | X | PASS/FAIL |

**風險說明**：...

---

### 3. SPI Protected Ranges（common.spi_lock）

**結果**：[PASS / FAIL]

| PR | 範圍 | 狀態 |
|----|------|------|
| PR0 | 0x... - 0x... | 啟用/停用 |
| PR1 | 0x... - 0x... | 啟用/停用 |
...

**BIOS 分區覆蓋率**：X%  
**風險說明**：...

---

### 4. Intel BootGuard（common.bootguard）

**結果**：[PASS / FAIL / NOT_SUPPORTED]

**SACM_INFO 值**：0x...  
**BootGuard Profile**：[Disabled / Measured / Verified / Measured+Verified]  
**風險說明**：...

---

### 5. UEFI Secure Boot（common.secureboot.variables）

**結果**：[PASS / FAIL]

| 變數 | 值 | 狀態 |
|-----|---|------|
| SecureBoot | 1 | PASS |
| SetupMode  | 0 | PASS |
| PK 憑證數  | N | - |
| dbx 條目數 | N | - |

**風險說明**：...

---

## 修補優先順序

| 優先度 | 項目 | 修補難度 | 建議行動 |
|-------|------|---------|---------|
| 緊急  | SMM_BWP=0 | 中（BIOS 設定） | 聯繫 OEM 確認是否有 BIOS 更新 |
| 高   | BootGuard 未啟用 | 高（需 fuse，通常無法事後修改） | 評估使用其他緩解（完整 SPI PR） |
| 中   | SPI PR 覆蓋不完整 | 中（BIOS 設定） | 更新 BIOS 或在 BIOS setup 調整 |
...

---

## 原始輸出

<details>
<summary>common.bios_wp 完整輸出</summary>

```
（貼上 chipsec 的原始 terminal 輸出）
```

</details>

<details>
<summary>common.smrr 完整輸出</summary>

```
（貼上原始輸出）
```

</details>

（其餘模組類推）
```

---

## 如果卡住

1. **`insmod: ERROR: could not insert module chipsec.ko: Operation not permitted`**  
   原因是 UEFI Secure Boot 啟用時，未簽章的 kernel module 無法載入。解法：在 BIOS setup 暫時停用 Secure Boot 執行稽核；或改用 `--helper` 選項指定 DAL 後端（需要 Intel System Studio）。注意停用 Secure Boot 後，`common.secureboot.variables` 的結果不代表正常運作狀態，需在報告中說明。

2. **多數 common 模組回傳 `[?] SKIPPED` 或 `UNKNOWN`**  
   這通常發生在 QEMU/VM 環境，模擬器沒有實作 PCH 的 PCI config space 暫存器（讀到全 0xFF 或 0x00）。CHIPSEC 發現讀值不合理時會標 UNKNOWN 而非 FAIL。解法：必須在真實硬體上執行；若目的只是學習判讀，可用本練習的擬真輸出範例練習。

3. **`common.bootguard` 回傳 `NOT_APPLICABLE`**  
   部分舊平台（Haswell 之前）沒有 BootGuard 支援，MSR_BOOT_GUARD_SACM_INFO 不存在，RDMSR 觸發 GP fault，CHIPSEC 標記為 NOT_APPLICABLE。這不代表 PASS，只代表這項保護在此平台根本不存在。報告中應記錄為「平台不支援 BootGuard，無硬體信任根保護 IBB」。

---

## 實作步驟

### Step 1：準備環境

確認你的測試機器（需要真實硬體）：
- x86_64 平台，有 UEFI 韌體
- Linux 系統（Ubuntu 22.04 或 Debian 12 建議），已安裝 Python 3.8+
- root 帳號可用（`sudo -i` 或直接 root 登入）
- 網路連線（下載 CHIPSEC）

如果沒有閒置的 x86 機器，可考慮：
- 購買二手商用電腦（Thinkpad T 系列、Dell Latitude，便宜且有企業級韌體）
- 用 Raspberry Pi 跑 ARM 版（但 common 模組針對 x86，多數不適用）

### Step 2：安裝 CHIPSEC

```bash
# clone 最新版
git clone https://github.com/chipsec/chipsec.git
cd chipsec

# 安裝 Python 套件
sudo pip3 install -e .

# 如果有 kernel module 編譯問題（kernel headers 不符）
sudo apt-get install linux-headers-$(uname -r) build-essential

# 嘗試直接執行（某些設定下 Python helper 不需 kernel module）
sudo python3 chipsec_main.py --helper linuxnative
```

### Step 3：執行稽核

```bash
# 執行完整稽核，存 log
sudo python3 chipsec_main.py 2>&1 | tee ~/chipsec_$(hostname)_$(date +%Y%m%d).txt

# 只跑本章重點的模組
for module in common.bios_wp common.smrr common.spi_lock common.bootguard \
              common.secureboot.variables common.smm_dma; do
    echo "=== $module ===" >> ~/chipsec_targeted.txt
    sudo python3 chipsec_main.py -m $module 2>&1 >> ~/chipsec_targeted.txt
done
```

### Step 4：解讀輸出

對照本練習的「檢查項意義與 FAIL 攻擊可行性對照表」，逐一判讀每個 PASS/FAIL：
- PASS：記錄為保護正常
- FAIL：確認對應的攻擊可行性，評估修補難度
- WARNING：介於中間，記錄但評估是否實際可被利用

注意辨別「FAIL 但攻擊需要前提條件」（例如 SMM_BWP=0 的攻擊需要先 root + SMM exploit 才能利用）和「FAIL 直接可被利用」（例如 Secure Boot SetupMode=1 任何人都能改 PK）。

### Step 5：撰寫報告

使用本練習的報告骨架，填入你的結果：
- Executive Summary：整體風險評估（1-2 段）
- 詳細結果：每個模組的結果表格 + 風險說明
- 修補優先順序：FAIL 項目按風險高低排序
- 原始輸出：用 `<details>` 折疊，保留完整 terminal 輸出供驗證

---

## 延伸挑戰：自寫一個 CHIPSEC 模組

CHIPSEC 模組是標準 Python class，繼承 `BaseModule`，實作 `run()` 方法。了解如何自寫一個模組，就能把任何韌體研究發現轉換成可重用的稽核工具。

以下是一個最小範例模組，檢查 `BIOS_CNTL` 的 bit 2（`SME_SMI_LOCK`，某些平台有，鎖定 SMBASE）：

```python
# chipsec/modules/research/check_sme_smi_lock.py
# 自訂模組：檢查 BIOS_CNTL 的 SME_SMI_LOCK bit（bit 2，平台 specific）
# 本段為教學範例，實際效果依平台而定

from chipsec.module_common import BaseModule, ModuleResult, MTAG_BIOS
from chipsec.library.returncode import ModuleResult as Res

TAGS = [MTAG_BIOS]

class check_sme_smi_lock(BaseModule):
    def __init__(self):
        super(check_sme_smi_lock, self).__init__()

    def is_supported(self) -> bool:
        return True  # 宣告支援所有平台（實際應加平台判斷）

    def run(self) -> int:
        self.logger.start_test("SME_SMI_LOCK (BIOS_CNTL bit 2)")
        
        # 讀取 PCI 0:31:0 offset 0xDC（BIOS_CNTL register）
        # chipsec 的 pci helper 封裝了 PCI config space 存取
        try:
            bios_cntl = self.cs.pci.read_byte(0, 31, 0, 0xDC)
        except Exception as e:
            self.logger.error(f"Failed to read BIOS_CNTL: {e}")
            return ModuleResult.ERROR

        sme_smi_lock = (bios_cntl >> 2) & 1  # bit 2

        self.logger.log(f"BIOS_CNTL = 0x{bios_cntl:02X}")
        self.logger.log(f"  SME_SMI_LOCK (bit 2) = {sme_smi_lock}")

        if sme_smi_lock == 1:
            self.logger.test_passed()
            self.logger.log_good("SME_SMI_LOCK is set: SMBASE cannot be changed after lock")
            return ModuleResult.PASSED
        else:
            self.logger.test_failed()
            self.logger.log_bad("SME_SMI_LOCK is clear: SMBASE relocation attack may be possible")
            return ModuleResult.FAILED
```

執行自訂模組：
```bash
# 把 check_sme_smi_lock.py 放到 chipsec/modules/research/
sudo python3 chipsec_main.py -m research.check_sme_smi_lock
```

延伸挑戰方向：
- 寫一個模組，讀取所有 SW SMI handler 的 SwSmiNumber（透過 UEFI variable 或 SPI dump 解析）
- 寫一個模組，驗證 Intel ME 的 HECI 裝置是否暴露（存在於 PCI bus 上），及其韌體版本
- 寫一個模組，讀取 SPI flash 的 Flash Descriptor 的 FLMSTR（確認 ME 分區是否只有 ME 可寫）

---

## 本練習重點

- CHIPSEC 的每個 check 對應一個已知攻擊手法：FAIL 不只是「設定錯」，是「這條攻擊路徑開著」
- BIOS_CNTL 三個關鍵 bit（BIOSWE/BLE/SMM_BWP）要一起看；只有 BLE=1 但 SMM_BWP=0 仍然不安全
- SMRR Valid=1 是防 SMM 側信道攻擊的第一道線；FAIL 代表 OS 可直接讀 SMRAM
- SPI Protected Ranges 必須完整覆蓋 BIOS 分區；部分覆蓋等同沒保護
- BootGuard FAIL 通常無法事後修補（fuse 問題），只能用其他緩解（完整 SPI PR）降低風險
- 稽核報告應清楚標明 FAIL 的可利用條件（直接可利用 vs 需要前提），避免過度渲染或低估

---

## 自我檢核

- [ ] 我能說出執行 CHIPSEC 需要什麼前提（root、kernel driver、真實硬體）
- [ ] 我能解讀 BIOS_CNTL 的三個 bit（BIOSWE/BLE/SMM_BWP）各自的意義和正確值
- [ ] 我能說出 SMRR Valid bit FAIL 代表什麼攻擊可行（ring-0 讀 SMRAM）
- [ ] 我能解釋 SPI Protected Ranges 的作用，以及部分覆蓋為什麼不夠
- [ ] 我能說出 BootGuard FAIL 和 Secure Boot SetupMode=1 各自代表什麼風險
- [ ] 我能描述 CHIPSEC 模組的基本結構（繼承 BaseModule，實作 run()，呼叫 pci.read_byte 等）

---

## 延伸閱讀

1. **CHIPSEC 官方文件與模組說明** — https://chipsec.github.io/  
   讀哪裡：`modules/` 目錄下每個 `.py` 的 docstring；特別是 `common/bios_wp.py`、`common/smrr.py`  
   學什麼：每個模組讀哪些暫存器、判斷邏輯是什麼、對應哪些攻擊手法  
   關聯：直接對應本練習所有 check 項目，是最重要的一手文件

2. **"Attacking and Defending BIOS in 2015"** — Bulygin et al. (RECon 2015)  
   讀哪裡：https://www.c7zero.info/stuff/AttackingAndDefendingBIOS-RECon2015.pdf  
   學什麼：SMRR、BIOS_CNTL、SPI PR 的攻防邏輯；CHIPSEC 的設計哲學就來自這篇報告  
   關聯：為本練習所有檢查項提供攻擊動機和防禦設計背景

3. **Intel Platform Security Report** — Intel（搜尋「Intel Platform Security Assessment」）  
   讀哪裡：Intel 的平台安全白皮書系列（PDFs，可在 Intel 官方 security center 取得）  
   學什麼：每個暫存器的官方定義、合規要求、以及 Intel 推薦的設定值  
   關聯：對應本練習的「正確狀態」欄，是稽核報告的引用來源

4. **"BIOS Guard and SPI Protected Range Deep Dive"** — 任意 edk2-devel 或 Intel TianoCore 討論串  
   讀哪裡：https://edk2.groups.io/g/devel，搜尋 "SPI protected ranges" 或 "BIOS_CNTL SMM_BWP"  
   學什麼：OEM 和 EDK2 開發者如何討論這些 bit 的正確設定，以及踩坑案例  
   關聯：對應本練習的踩雷（為什麼 OEM 常忘記設 SMM_BWP）

5. **CHIPSEC 模組開發指南** — https://chipsec.github.io/development/Writing-a-Module.html  
   讀哪裡：完整教學頁  
   學什麼：BaseModule API，如何存取 PCI/MSR/UEFI variable，如何設定 TAGS 和回傳值  
   關聯：對應本練習的「延伸挑戰：自寫模組」

---

→ [Ch 15 ARM 開機信任鏈：BL1→BL2→BL31→BL33](./15-arm-boot-chain.md)
