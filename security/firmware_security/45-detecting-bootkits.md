# Ch 45 — 偵測 bootkit 與供應鏈

> **目標**：全課防守收尾。從偵測工具、記憶體鑑識、供應鏈驗證、IoC 特徵到事件回應，建立一套「發現可疑韌體植入後能做什麼」的實戰框架，並對照 Ch 31（bootkit 構造）給出偵測能力 vs 持久化層級的清醒評估。

---

## 為什麼偵測如此困難

把 Ch 31（bootkit 構造）的知識反過來看就是答案：

```
bootkit 持久化層級（從易到難偵測）：
  Level 1: OS 層（登錄、系統程式替換）    ← EDR 能抓
  Level 2: 驅動 / Bootloader 層           ← EDR + Secure Boot 能抓
  Level 3: UEFI DXE module 植入           ← 少數工具能抓（chipsec、Binarly）
  Level 4: SMM 植入（SMRAM 後門）         ← 極難，chipsec 有限支援
  Level 5: ME / PSP 韌體植入             ← 幾乎無工具，ME JTAG 才能驗
  Level 6: SPI flash 實體替換            ← 完全脫離軟體偵測範圍
```

Level 3 以上的 bootkit（LoJax、MoonBounce、BlackLotus）對傳統 EDR 完全透明。本章的工具和方法，大多只能覆蓋到 Level 3-4，Level 5-6 是誠實的盲點。

---

## 工具一：fwupd / LVFS

### 韌體更新的供應鏈

LVFS（Linux Vendor Firmware Service，https://fwupd.org/lvfs/）是 Richard Hughes 主導的開源韌體更新生態系：

```
OEM（Dell / HP / Lenovo / AMD / Intel / ...）
  │  提交 firmware capsule（簽章）
  ▼
LVFS 服務器（驗章 + 儲存）
  │
  ▼ fwupd（Linux 端的更新客戶端）
  │  驗 OEM capsule 簽章
  │  送給 UEFI CapsuleUpdate 機制
  ▼
BIOS 更新完成
```

LVFS 的安全機制：
- 每個 firmware capsule 需要 LVFS 的 code-signing（額外的 transport 簽章）
- OEM 自己的 Secure Boot 簽章仍然要在設備端驗
- HSI（Host Security ID）評分：fwupd 的 `fwupdmgr security` 提供 HSI-0 到 HSI-5 的安全評分

```bash
# 在 Linux 上確認韌體更新來源和設備安全狀態
fwupdmgr get-devices        # 列出支援 fwupd 的設備
fwupdmgr security           # 看 HSI 評分，每個保護層級的狀態
fwupdmgr get-updates        # 有哪些待更新韌體

# HSI 評分含義（簡化）：
# HSI-0: 基礎（UEFI 有設 Secure Boot PK）
# HSI-1: UEFI 開啟 Secure Boot + BootGuard
# HSI-2: 韌體更新簽章驗証 + IOMMU
# HSI-3: SMM 鎖定 + BIOS_CNTL 設定
# HSI-4: MEI 安全狀態確認
# HSI-5: 供應鏈完整性（Pluton / 強 TPM）
```

### dbx 更新的重要性

UEFI Secure Boot 的 dbx（禁止清單）必須及時更新。fwupd 能自動推送 dbx 更新：

```bash
# 確認目前的 dbx 版本
fwupdmgr get-devices | grep dbx
# 更新 dbx（需 root）
fwupdmgr update
```

**為什麼 dbx 更新常被忽略**：很多使用者只更新 OS，不更新韌體。BlackLotus（Ch 30）利用的正是 dbx 未更新的 UEFI，哪怕 Windows 11 本身是最新的。

---

## 工具二：CHIPSEC

CHIPSEC（https://github.com/chipsec/chipsec）是 Intel 釋出的平台安全稽核框架，Practice B 已有操作，這裡著重在「偵測植入」的用法。

**本節所有 CHIPSEC 命令標「未實測」，需要 root + CHIPSEC kernel module（`chipsec_util.ko`）。**

### 偵測 UEFI 植入的關鍵模組

```bash
# 稽核整個平台保護組態（一次跑所有）
sudo python chipsec_main.py  # 未實測

# 掃描 UEFI 韌體可能的惡意植入特徵
sudo python chipsec_main.py -m tools.uefi.scan_blocked  # 未實測
# → 比對 blacklist（已知惡意 UEFI 模組的 hash）

# 對比 UEFI 模組白名單
sudo python chipsec_main.py -m tools.uefi.whitelist     # 未實測
# → 建立「已知良好」的 UEFI module hash 清單，之後比對差異

# SPI 讀保護狀態
sudo python chipsec_main.py -m common.spi_lock          # 未實測
sudo python chipsec_main.py -m common.spi_write_protection  # 未實測

# SMRR / SMM 鎖定
sudo python chipsec_main.py -m common.smrr              # 未實測
sudo python chipsec_main.py -m smm.smm_code_chk         # 未實測
```

### CHIPSEC 的局限

CHIPSEC 的偵測邏輯是：**比較現在看到的 UEFI module 和預期的 hash/結構**。

這帶來一個根本問題：

```
問題：如果 BIOS 已被植入，CHIPSEC 是從 BIOS 內執行的 OS 跑的
  │
  └── LoJax / MoonBounce 類植入可以 hook CHIPSEC 用的 SPI 讀取介面
      讓 CHIPSEC 看到的是「乾淨的 BIOS image」，而非真實內容

真正可靠的掃描需要：
  ├── 用外部 programmer（CH341A）直接讀 SPI flash（繞過任何 OS/firmware hook）
  └── 用乾淨的參考機或廠商原始 image 做 hash 比對
```

這是韌體鑑識的核心困境：**用被攻陷的系統偵測攻陷本身，是不可靠的**。

---

## 工具三：商用掃描器（Binarly / Eclypsium / ESET）

**本節為原理描述，商用工具未實測。**

### Binarly FwHunt

Binarly（https://fwcheck.binarly.io）提供免費的韌體掃描：

工作原理：
1. 上傳 BIOS binary（用 CH341A dump 或廠商更新包萃取）
2. Binarly 用 AI-assisted pattern matching + 已知 CVE signature 比對
3. 輸出報告：發現哪些 vulnerability、哪些 module 有已知問題

Binarly 發現的一些簽名（FWID）：
- **BRLY-2022-005**：InsydeH2O SMM handler 系列（影響多家 OEM）
- **LogoFAIL**（2023）：BMPcore / JPEG 解析器在 DXE 初始化期間的影像解析 overflow，存在於幾乎所有 BIOS 廠商

### Eclypsium Analyzer

Eclypsium 的商用掃描器做法類似，多了：
- UEFI module 行為分析（不只 hash，看模組的 Protocol 使用模式）
- Supply chain risk scoring（從模組 build 特徵推測 third-party component）

### ESET 韌體掃描（Consumer 產品）

ESET 在 2018 年發現 LoJax 後加入 UEFI 韌體掃描功能。掃描方法：
- 從 OS 用 SPI 讀取介面（Windows WMI / Linux sysfs）讀 UEFI flash 內容
- 解析 FV/FFS 結構（類 UEFITool），比對已知惡意模組的 hash
- **問題和 CHIPSEC 一樣**：進階植入可以 hook 這個讀取介面

---

## 記憶體鑑識：找韌體植入的 OS 痕跡

呼應 blue_team_dfir 課的記憶體分析，但聚焦在韌體植入留下的 OS 層痕跡。

### UEFI Runtime Service 的記憶體痕跡

UEFI 韌體在 OS 啟動後並不完全消失——Runtime Services（`GetVariable`, `SetVariable`, `GetTime` 等）仍然存在於記憶體，mapped 到一個特定的 EFI Runtime 區域。

植入的 DXE module 如果 hook 了 Runtime Services，可以在 OS 記憶體快照中看到：

```
Windows 記憶體中的 UEFI Runtime 痕跡：
  1. System Table 位址（UEFI 啟動留下，可從 HKLM\SYSTEM\... 找到基礎位址）
  2. Runtime Services Table 中的函式指標是否被改過
  3. EFI_RUNTIME_DRIVER（MZ 開頭）在記憶體中的存在

Volatility3 分析方向（呼應 blue_team_dfir Ch3）：
  vol -f memory.dmp windows.uefi_scan  # 尋找 UEFI runtime code
  vol -f memory.dmp windows.pe_scan    # 找 MZ/PE header，比對已知 UEFI module
```

### MoonBounce 的 OS 層落地痕跡

MoonBounce（Ch 31）的植入鏈：
```
SMRAM 中的惡意 SmmCallout handler
  │ （在 OS 啟動時觸發）
  ▼
修改 Windows kernel loader 的記憶體（injector）
  │
  ▼
注入惡意 kernel driver（.sys）
  │
  ▼
惡意 driver 連接 C2（network beacon）
```

OS 層的 IoC（可以用 EDR / Volatility 找到）：
- 記憶體中有未對應到磁碟檔案的 kernel module（`hideval` / `IntelUpdate.exe` 系列）
- 可疑的 SMI callback 地址（指向 SMRAM 內，OS 讀不到）
- SMRAM 讀取嘗試（觸發 #GP，系統 log 的異常 machine check）

### 使用 Volatility3 找韌體植入痕跡

```bash
# 概念性命令（需要適合的 Volatility3 profile）
# 找記憶體中的異常 PE（可能是 UEFI module 注入的 kernel 元件）
vol -f memory.raw windows.malfind | grep -E "MZ|PE"

# 找進程的異常記憶體映射（未對應磁碟）
vol -f memory.raw windows.vadinfo --pid <pid> | grep "VadS"

# 找 SMRAM 相關的記憶體保護異常
vol -f memory.raw windows.bigpools | grep -i "smm"
```

**現實限制**：MoonBounce 和 CosmicStrand 的 kernel injection 元件刻意避免磁碟痕跡（fileless），Volatility 能抓到 in-memory artifact，但需要知道找什麼——IoC 比對是關鍵。

---

## IoC：已知 bootkit 的偵測特徵

### LoJax（2018，ESET 發現）

**來源**：LoJack 合法 anti-theft software 的武器化版本。俄羅斯 APT28 使用。

**植入點**：UEFI SPI flash，作為 DXE driver 注入。

```
偵測 IoC：
  UEFI 模組：
    GUID: {84d00d14-5c70-41cc-a8af-6e64c0d2f1f7}（已知 LoJax DXE module）
    Module 大小：~8KB，不尋常地小
    無對應的 PDB / 無廠商字串

  OS 層：
    rpcnetp.exe / rpcnetp.dll 在 %WINDIR%\System32\
    SVC 名稱："LoJack" 或偽造的系統服務名

  網路：
    DNS 查詢：*.1.nsc2svc.com（LoJax 的 C2 domain）
    HTTP POST 到 80/443，user-agent 含特定字串
```

CHIPSEC 白名單稽核可發現 LoJax 的 DXE module（GUID 匹配），但需要預先建立白名單。

### MoonBounce（2022，Kaspersky 發現）

**植入點**：SMRAM（SMM module），難度比 LoJax 高一級。中國 APT41 相關。

```
偵測 IoC：
  UEFI / 記憶體層：
    修改 CORE_DXE 的 LocateProtocol hook（正常版本無此 hook）
    SMRAM 中存在未知的 SmiHandler 地址
    （chipsec -m smm.smram_lock 可能偵測異常，但不保證）

  OS 層：
    記憶體中存在沒有磁碟對應的 kernel driver（SCSI miniport 偽裝）
    EventLog：WHEA / MCA 錯誤（SMM 存取異常）
    網路：連接特定 IP 範圍（APT41 已知 C2 infra，TI feeds 有收錄）

  YARA 規則（概念）：
    rule MoonBounce_UEFI {
        strings:
            $hook = { 48 8B 0D ?? ?? ?? ?? 48 85 C9 74 }  // LocateProtocol hook pattern
            $smm_marker = "SMRAM" nocase
        condition:
            uint16(0) == 0x5A4D and $hook and $smm_marker
    }
```

### BlackLotus（2023，ESET 發現）

**植入點**：MBR + UEFI bootloader 替換（不是 SPI flash 植入，而是 UEFI boot entry 竄改）。

```
植入機制（Ch 30 詳細講過）：
  1. 利用 CVE-2022-21894（Secure Boot bypass）降級 Windows Boot Manager
  2. 用惡意 bootkit 替換 Windows Boot Manager（shimbase.efi）
  3. 建立持久化 UEFI boot entry 指向惡意 loader
  4. 繞過 HVCI、停用 Defender

偵測 IoC：
  UEFI 層：
    額外的 EFI boot entry（efibootmgr 或 bcdedit 列出）
    %EFI%\Microsoft\Boot\ 中出現非預期的 .efi 檔案
    boot*.efi 的 hash 不匹配 Microsoft 已知版本

  OS 層：
    %WINDIR%\System32\drivers\ 中出現 wdboot.sys 偽裝
    Defender 的 registry 鍵被刪除（HKLM\SYSTEM\CurrentControlSet\Services\WinDefend）
    EventID 4616 + 4719（audit policy 被改）
    驅動程式強制停用（bcdedit /set TESTSIGNING ON 的遺跡）

  網路：
    DNS: *.getupdates.net（已知 C2）
    HTTP beaconing 每 5 分鐘一次，payload 加密

ESET 已有 IoC 完整列表：
  https://github.com/eset/malware-ioc/tree/master/blacklotus
```

**偵測重點**：BlackLotus 的 SPI flash 不是植入點，相較 MoonBounce 更「淺」，但它的 Secure Boot bypass 讓 EFI boot entry 替換可以持久化，Windows Defender 掃不到（Defender 被它自己停用了）。

---

## 供應鏈偵測

### 韌體來源驗證

多層次的供應鏈驗證：

```
層次 1：來源（Transport Integrity）
  └── HTTPS + TLS → 基本，但 PKI 可被攻擊
  └── LVFS code-signing → OEM 提交時加上 LVFS 簽章

層次 2：廠商簽章（OEM Integrity）
  └── UEFI capsule 的 OEM RSA 簽章
  └── 設備端 BIOS 更新前驗章

層次 3：內容比對（Content Verification）
  └── 和廠商官方 hash 比對（廠商需要公布 hash）
  └── Binarly FwHunt 比對已知良好版本
  └── 自己建立 reference hash（首次安裝時建立基準）

層次 4：建構可追溯性（Build Provenance）
  └── Reproducible Build（見下節）
  └── SBOM（Software Bill of Materials）
```

### SBOM for Firmware（韌體軟體物料清單）

SBOM 的概念從軟體供應鏈延伸到韌體：

```
韌體 SBOM 理想內容：
  ├── Component: OpenSSL 3.0.8（UEFI CryptoPkg）
  ├── Component: GRUB 2.06（bootloader，來自 upstream commit X）
  ├── Component: OEM SMM Driver v2.3（私有，來自 OEM 內部）
  └── Build system: EDK2 commit abc123，GCC 12.3

工具：
  sbomdiff: 比對兩個韌體版本的 SBOM 差異
  tern: 從 UEFI firmware image 嘗試抽取 SBOM（能力有限，私有模組不透明）
  syft / grype: 主要用於容器/OS，概念可延伸
```

**現實**：韌體 SBOM 目前嚴重缺乏標準化，大多數 OEM 不公布。美國 CISA / NIST SSDF 正在推動規範，但 2024 年底強制要求還未落地。研究者只能靠工具嘗試逆向推導。

### Reproducible Firmware Build

「Reproducible Build」讓任何人可以從相同 source code + build 環境重現完全相同的 binary（bit-for-bit），從而驗證分發的 binary 沒有被竄改。

```
Reproducible Firmware 的挑戰：
  1. 建構時間戳記（PE header 的 TimeDateStamp）→ 需要固定為 0 或已知值
  2. 隨機化 GUID（某些 EDK2 工具產生隨機 FV GUID）→ 需要固定 seed
  3. 私有 OEM 元件（SMM driver、SPI flash 解碼器）→ 無 source，無法重現
  4. 工具鏈版本（GCC / Clang 版本影響 codegen）→ 需要 Docker 鎖版

支援 Reproducible Build 的韌體：
  └── coreboot（Ch 18）：目標之一，已有 CI 驗証
  └── LinuxBoot（Trammell Hudson 的 u-root 為基礎）
  └── Dasharo（based on coreboot，特別強調供應鏈透明度）

主流 OEM UEFI（InsydeH2O / AMI / Phoenix）：目前無法重現
```

---

## 偵測能力 vs bootkit 持久化層級對照表

呼應 Ch 31 的 bootkit 構造層次：

| 持久化層級 | 代表案例 | EDR | 記憶體鑑識 | CHIPSEC | 外部 SPI dump | fwupd / LVFS | 備註 |
|-----------|---------|-----|----------|---------|-------------|-------------|------|
| OS layer（登錄、系統程式） | 大多數 rootkit | ✓ | ✓ | ✗ | ✗ | ✗ | EDR 可靠覆蓋 |
| Bootloader 替換（EFI partition） | BlackLotus（前半） | △ | ✓（efi hash）| ✗ | ✗ | △（dbx 更新後）| efi hash 需主動監控 |
| UEFI DXE module 植入（SPI flash） | LoJax | ✗ | △（OS artifact）| ✓（白名單比對）| ✓ | ✗（無 LVFS 比對）| CHIPSEC + 外部 dump 組合 |
| SMM module 植入（SMRAM） | MoonBounce | ✗ | △（kernel artifact）| △（限制）| △（SMRAM 不在 SPI 一般可見範圍）| ✗ | 最難偵測；需要 SMM JTAG |
| ME / PSP 韌體植入 | 理論（Ring -3 rootkit）| ✗ | ✗ | ✗ | ✗（ME 區加密）| ✗ | 現有工具幾乎無能為力 |
| SPI flash 實體替換 | 國家級供應鏈 | ✗ | ✗ | △（外跑比對）| ✓ | ✗ | 外部 programmer 是最後手段 |

圖例：✓ 能偵測、△ 部分/間接偵測、✗ 無能力

---

## 事件回應：發現 SPI 植入後怎麼辦

這是最難的情境——你確認（或高度懷疑）設備的韌體被植入了。

### Step 1：取證優先，不要輕易重刷

```
發現異常 → 立刻備份！
  ├── CH341A 讀 SPI flash，保存完整 .bin（植入後的 image）
  ├── 記憶體 dump（winpmem / LiME）
  ├── 網路 pcap（若仍在線且安全）
  └── 系統 log（Windows Event Log / Linux syslog）

原因：重刷後植入消失 = 鑑識證據消失
      而且如果是 SMM/ME 層植入，重刷 BIOS 不一定能清除
```

### Step 2：評估植入深度

```
問題清單：
  □ EFI partition 有沒有不明 .efi？
    → efibootmgr / bcdedit 列出所有 boot entry
    → ls /boot/efi/EFI/（Linux）或 %EFI% 目錄

  □ SPI flash dump 和官方 image 的 diff 是什麼？
    → UEFITool 解析，比對 module GUID 清單
    → Binarly FwHunt 上傳 dump

  □ CHIPSEC 有沒有異常？（需要乾淨的 OS）
    → 用 live CD / USB 跑 CHIPSEC

  □ 有沒有 kernel level artifact？
    → Volatility3 記憶體分析
    → 找沒有磁碟對應的 kernel module
```

### Step 3：選擇清除策略

```
植入在 EFI partition（Level 2）：
  → 重新安裝 bootloader，更新 dbx
  → 追加 Secure Boot 重新 enroll key
  → 相對安全

植入在 DXE module（Level 3，LoJax 類）：
  → 重刷原廠 BIOS（從官網下載，驗 hash）
  → 用 CH341A 直接刷（繞過 OS 的任何 hook）
  → 更新後再次 dump + 比對，確認清除

植入在 SMM / ME（Level 4-5）：
  → 重刷 BIOS「不足以」清除 SMRAM 植入（SMRAM 在重啟後會被 UEFI 重新初始化）
  → 實際上：換板（motherboard replacement）是最確定的方案
  → 如果是 ME 層：停用 ME（HAP bit / me_cleaner）能降低風險，但不能完全確認清除

植入在 SPI flash 實體（Level 6）：
  → 需要拆 SPI 晶片 + 換新晶片，刷入原廠 image
  → 或整板替換
```

### Step 4：重新建立信任

```
清除後的確認流程：
  1. 外部 SPI dump → 和官方 image bit-for-bit 比對（或 hash 比對）
  2. fwupd security → 確認 HSI 評分
  3. CHIPSEC → 確認所有保護位元正確
  4. Secure Boot → 重新 enroll PK/KEK/db，清理任何不明 certificate
  5. TPM → PCR reset + 重新建立 measured boot baseline
  6. 網路監控 → 觀察 2 週，確認無 C2 beaconing
```

---

## 供應鏈的預防性監控

發生之前的持續性監控，成本比事後取證低。

### 韌體基準線（Baseline）

```bash
# 在已知乾淨的狀態下建立基準線
# 方法 1：外部 SPI dump（最可靠，需硬體）
# CH341A + flashrom：
flashrom -p ch341a_spi -r baseline_$(date +%Y%m%d).bin  # 未實測，需硬體

# 方法 2：CHIPSEC SPI 讀取（OS 內，有被 hook 的風險）
sudo python chipsec_util.py spi dump bios.bin  # 未實測

# 方法 3：廠商提供的 capsule 解包
# 從 LVFS 下載最新更新包，用 UEFITool NE 解包，建立 module hash 清單
```

### UEFI Module 監控（OS 層）

```python
# 概念性腳本：列出 UEFI module 和對應 hash（需要 UEFI shell 或 CHIPSEC）
# 可以定期跑並 diff

# UEFI Shell 命令（在 UEFI shell 環境中）：
# dh -v  → 列出所有 DXE handle 和 protocol，可識別已載入的 UEFI module

# Linux 下（若有 efivar）：
import subprocess
result = subprocess.run(['efivar', '--list'], capture_output=True)
# → 列出 NVRAM variable，監控不明 variable（LoJax 用 variable 存 payload）
```

### 告警觸發條件

在 SIEM / 監控框架中加入：

```yaml
# 韌體相關告警規則（Sigma 概念）
title: UEFI Runtime Variable Modification
logsource:
  product: windows
  service: system
detection:
  keywords:
    - EventID 1796  # Bitlocker UEFI variable change
    - "NVRAM"
    - "SetVariable"  # 若有 kernel-level UEFI variable audit log
condition: keywords
falsepositives:
  - Legitimate BIOS update
  - Secure Boot key enrollment
level: medium

---
title: Unexpected EFI Partition File Modification
logsource:
  product: windows
  service: security
detection:
  selection:
    EventID: 4663  # File access auditing
    ObjectName|contains: '\EFI\Microsoft\Boot\'
    AccessMask: '0x2'  # Write access
condition: selection
level: high
```

---

## 踩雷

1. **CHIPSEC 在被植入的系統上不可信**：這是最重要的警告。進階 bootkit 可以 hook SPI 讀取 API，讓 CHIPSEC 看到假的乾淨 image。唯一可靠的方法是**外部 CH341A dump**。

2. **重刷 BIOS 不等於清除 SMM 植入**：SMRAM 在每次開機時由 UEFI 重新初始化，重刷 BIOS 其實是「換了初始化 SMRAM 的 UEFI」。如果惡意 UEFI 已被刷掉，SMRAM 植入就沒了。但如果 SPI flash 的惡意 DXE 沒有被清除，下次開機時它又會重新植入 SMRAM。所以「完整清除」必須先清 SPI 層，再重建 SMRAM。

3. **BlackLotus 的 dbx bypass 需要特定條件**：BlackLotus 能繞過 Secure Boot 的前提是 dbx 沒有收錄它用的 boot manager hash。一旦 dbx 更新（Microsoft 已發布），同樣的 binary 就無法再用。但「已經植入」的機器，dbx 更新後它可以不再用那個 binary，改用其他持久化機制。偵測和清除是兩件事。

4. **SBOM 目前的實用性有限**：現有韌體 SBOM 工具能提取的資訊大多來自 open-source 元件；OEM 的 private SMM driver 是黑盒，SBOM 工具看不到裡面。不要對 SBOM 抱過高期待，它是方向正確但尚未成熟的工具。

5. **「換板」雖然激進，但有時是唯一選項**：威脅模型如果是國家級攻擊者，ME 層植入是可能的。此時的「清除」選項只有：（a）換板，（b）確認關掉 ME（HAP bit），（c）接受「清除不完全」的殘餘風險。這不是偏執，是合理的風險評估。

6. **OS 層 IoC 很重要，但不要只靠 OS 層**：BlackLotus 有 OS 層 IoC（Defender 被停、特定服務被刪），但這些 IoC 在 bootkit 成功執行後才出現。更好的方法是在 EFI partition 層做 hash 監控，這比等到 OS 異常才偵測要早。

---

## 進階延伸

- **Trammell Hudson 的 Heads（https://osresearch.net）**：開源的 measured boot 韌體，設計目標是讓每次開機的信任鏈可被終端使用者獨立驗証。比 Secure Boot 更透明，代價是只支援特定硬體。和本課 Part 7 的 measured boot 概念完全對應，但有完整的 user-facing 工具。

- **Binarly 的 FWID / FwHunt Rule**（https://github.com/binarly-io/FwHunt）：開源的韌體掃描規則格式，類似 YARA 但專為韌體模組設計。可以自己寫規則描述可疑 UEFI module 的特徵（大小、GUID 模式、字串），然後用 FwHunt 引擎比對。是自行建立韌體 IoC 偵測能力的最佳起點。

- **EFIXplorer（Ghidra + IDA 外掛）**：Part 4 講過，但從偵測視角更有用的是它的自動分析功能——批次分析多個 UEFI module，標注已知 Protocol 和 Guid，快速發現「沒有對應到任何正常功能的 GUID」，這就是 LoJax 類植入的特徵。

---

## 動手練習

**主要練習在 QEMU + OVMF + UEFITool 環境，SPI dump 相關需要真實硬體。**

1. **建立 UEFI Module 基準線**：
   - 下載你的設備廠商的 BIOS 更新 capsule（LVFS 或廠商網站）
   - 用 UEFITool NE 解包，列出所有 FFS file（GUID + 大小 + 類型）
   - 把清單存成文字，作為「已知良好」基準線
   - 下一次更新後重新做一遍，diff 看哪些模組變了、哪些模組是新的

2. **BlackLotus IoC 比對**：
   - 在 Windows 測試環境：`bcdedit /enum all` 列出所有 boot entry，確認沒有不明 entry
   - `Get-ChildItem "C:\Windows\Boot\EFI" | Get-FileHash -Algorithm SHA256` 計算所有 EFI 的 hash
   - 和 Microsoft 公布的已知良好 hash 清單比對（MSRC 公告附的 hash）

3. **fwupd security 評分**（Linux 環境）：
   ```bash
   sudo fwupdmgr security --force
   ```
   閱讀每個 HSI 評分項目，找出你的設備「哪些保護沒有開」，和本章及 Ch 44 的防線清單對應，理解「缺了這條保護意味著什麼攻擊路徑是開的」。

4. **LoJax GUID 搜尋練習**：
   - 在 QEMU + OVMF 環境啟動 UEFI Shell
   - 執行 `dh -v` 列出所有 DXE handle
   - 搜尋 GUID `84d00d14-5c70-41cc-a8af-6e64c0d2f1f7`（LoJax 已知 GUID）
   - 練習如果在真實環境找到不認識的 GUID 時的下一步（查 Binarly FwHunt、CHIPSEC whitelist、edk2 source）

---

## 本章重點

- 偵測能力天花板：Level 3（DXE 植入）靠 CHIPSEC + 外部 SPI dump，Level 4（SMM）已很困難，Level 5（ME）幾乎無工具
- CHIPSEC 和商用掃描器的共同侷限：在被攻陷的 OS 上跑掃描，結果不可靠；外部 CH341A dump 是基準
- LoJax / MoonBounce / BlackLotus 各有不同持久化層和不同 IoC 特徵，不要混用偵測方法
- 事件回應優先取證：先 dump SPI、記憶體、log，再考慮清除；重刷 BIOS 之前先備份植入狀態
- 供應鏈防線：SBOM + Reproducible Build 是方向，但現在大多數 OEM 韌體無法做到；LVFS 是目前最好的 transport integrity 機制
- fwupd HSI 評分 + dbx 及時更新是最低成本的持續監控手段

---

## 自我檢核

- [ ] 能說出六個持久化層級和對應的偵測工具（EDR / 記憶體鑑識 / CHIPSEC / 外部 dump）
- [ ] 知道 LoJax、MoonBounce、BlackLotus 各自的植入點和 OS 層 IoC 特徵
- [ ] 理解「在被攻陷系統上跑 CHIPSEC 不可信」的根本原因，和解法（外部 SPI dump）
- [ ] 能說出發現韌體植入後的事件回應步驟（取證 → 評估深度 → 清除策略 → 重建信任）
- [ ] 知道韌體 SBOM 和 Reproducible Build 的限制和現況
- [ ] 能解釋為什麼 dbx 及時更新對 BlackLotus 偵測很重要，以及「偵測」和「清除」的差異

---

## 延伸閱讀

1. **"LoJax: First UEFI rootkit found in the wild" — ESET（2018）**
   讀哪裡：https://www.welivesecurity.com/2018/09/27/lojax-first-uefi-rootkit-found-wild/
   學什麼：第一個被確認的野外 UEFI rootkit 的完整分析，UEFI module 植入的具體技術，和 CHIPSEC 如何偵測——是本章所有「UEFI DXE 植入偵測」討論的一手案例
   關聯：本章 IoC 節的 LoJax 部分，以及 Ch 31（bootkit 構造）的對稱讀法

2. **"BlackLotus UEFI Bootkit: Myth Confirmed" — ESET（2023）**
   讀哪裡：https://www.welivesecurity.com/2023/03/01/blacklotus-uefi-bootkit-myth-confirmed/
   學什麼：第一個在野外繞過 Windows 11 Secure Boot 的 bootkit，植入機制（EFI partition 替換）、HVCI 停用手法、OS 層 IoC，以及 Microsoft 的 dbx 應對措施
   關聯：本章 IoC 節的 BlackLotus 部分，以及全課防守收尾的最重要案例——把 Ch 28-32（Secure Boot 繞過）和本章偵測串起來

3. **"Firmware Supply Chain Security: SBOM and Beyond" — NIST（NISTIR 8315）**
   讀哪裡：https://csrc.nist.gov/publications/detail/nistir/8315/final
   學什麼：美國政府框架對韌體供應鏈安全的需求定義，SBOM 在韌體的應用挑戰，以及 Reproducible Build 和 code signing 的要求
   關聯：本章供應鏈偵測節，也是韌體安全從「研究者找洞」轉向「工程師建防線」的政策框架

→ [Final Project](./final-project-firmware-security-report.md)
