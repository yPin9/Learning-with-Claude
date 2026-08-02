# Ch 44 — 廠商緩解全景

> **目標**：把全課（Ch 1–43）出現過的廠商防線收斂成一張大圖，從矽到軟體逐層對應攻擊面，給一張「攻擊面 → 緩解 → 繞過現況」總表，讓你在做研究目標評估時能快速判斷哪一層還有空間。

---

## 先釐清問題框架

整門課的攻擊面分成六個層次：

```
層次 6：OS / bootloader（GRUB、Windows Boot Manager）
層次 5：UEFI 韌體主體（DXE、BDS、Secure Boot DB）
層次 4：SMM（Ring -2，晶片組管理模式）
層次 3：ME / PSP（Ring -3，獨立處理器）
層次 2：信任根 / 開機量測（BootGuard、TPM、TXT/DRTM）
層次 1：硬體 / SPI（SPI flash 實體存取、故障注入）
```

廠商的緩解幾乎都沿著這六層設計，但設計年代、實作品質、和廠商意願差異懸殊。

本章把 Intel、AMD、Microsoft、ARM、以及 UEFI 社群的緩解機制逐層剖析，最後給一張橫跨全課的總表。

---

## Intel 防線

### BootGuard：信任根燒入 CPU fuse

Ch 14 詳細講過，這裡只補緩解評估視角。

BootGuard 的核心主張：**SPI flash 可被竄改，但 CPU fuse 不可更改。**

```
CPU fuse（OEM 公鑰 hash，烙死在矽裡）
  │
  ▼ ACM（Intel 簽章，CPU ROM 驗）
  │
  ▼ IBB 驗 OEM 簽章
  │
  ▼ UEFI 其餘部分
```

**緩解有效的前提**：
- OEM 燒了 fuse（很多二手機、開發板沒燒）
- OEM 私鑰沒有洩漏（PKfail 2024 顯示多家廠商金鑰洩漏）
- ACM 本身沒有漏洞（有過 ACM bounds check 問題的歷史報告）

**繞過現況**：
- 未 fuse 平台 → 直接改 SPI，完全無效
- 私鑰洩漏（PKfail） → 攻擊者可簽任意 IBB
- 故障注入繞 ACM 驗章（Ch 34）
- 供應鏈植入（已簽章的惡意 IBB，在工廠燒 fuse 之前植入）

### BIOS Guard

BIOS Guard（Intel Platform Protected BIOS）是保護 SPI flash 寫入本身的機制，和 BootGuard 的「驗章」不同——它的目標是**防止 OS/SMM 軟體直接寫 SPI**。

```
軟體試圖寫 SPI flash
  │
  ▼ PCH BIOS_CNTL 寄存器
  │   BLE（BIOS Lock Enable）= 1
  │   → 寫入觸發 SMI
  │
  ▼ SMI handler 驗章 + 執行 BIOS Guard Protocol
  │   → 只有 Intel 簽章的 BIOS Guard script 才能操作 SPI
  │
  ▼ 若驗章失敗 → 拒絕寫入
```

BIOS Guard 和 BootGuard 的關係：
- BootGuard 驗「讀出來的 IBB 是否可信」
- BIOS Guard 阻止「惡意程式碼寫入 SPI」
- 兩者互補，但各有不同繞過方向

**BIOS Guard 繞過方向**：
- SMM exploit → 在 SMI handler 執行前劫持（但 SMM 若有 SMM Supervisor 就更難）
- SPI flash 實體讀寫（Ch 35）：焊接 CH341A 繞過所有軟體保護
- 寫入「主要 BIOS 區以外的區域」（某些平台 BIOS Guard 只保護 IBB 區域）

CHIPSEC `chipsec_main -m common.bios_guard` 可稽核 BIOS Guard 設定（未實測）。

### SMM Supervisor / SMM Code Check（SMM_Code_Chk）

這是 SMM（Ch 10-13）的核心防線，逐步演化中。

**舊防線：SMRR（SMM Range Register）**

SMRR 把 SMRAM 設成 WB（Write-Back）對主 CPU，對 SMM 以外存取回傳垃圾值。問題：SMRR 是 per-CPU 設定，多 CPU 環境的 BSP/AP 設定需要一致，歷史上有 AP 的 SMRR 沒設好的 bug。

**SMM_Code_Chk_En：阻止 non-SMM 記憶體在 SMM 中執行**

```
SMM 模式下觸發 CALL/JMP 跳到 SMRAM 外部
  │  SMM_Code_Chk_En = 1（MSR 0x1D0, bit 4）
  │
  └─→ CPU 產生 #GP，SMI handler 被強制中止
```

這個機制防止「SMM callout」（Ch 12）——攻擊者在 non-SMRAM 記憶體植入 shellcode，然後誘使 SMI handler 跳過去執行。

**Runtime SMM Isolation（RSI / SMM Supervisor）**

現代 Intel（從 Alder Lake / Gen 12+ 開始）引入 SMM Supervisor 的概念：把 SMM 分成「有特權的 Supervisor 層」和「無特權的 User 層」。OEM 的 SMM driver 執行在 User 層，無法直接存取某些特權 MSR 和 SMRAM 管理寄存器。

```
SMM Supervisor 層（Intel 固定的 code）
  ├── 控制 SMRAM 的 page table 設定
  ├── 控制 MSR 存取白名單
  └── OEM SMM driver 執行在此層的 guest 環境中
        → OEM bug 的衝擊半徑被限制
```

**繞過現況**：SMM Supervisor 讓傳統的 SMM callout exploit（Ch 12-13）效益大幅降低，但它本身在部分平台的初始化有 race condition 問題，且 Supervisor 的程式碼本身也是攻擊面。

### Intel TXT（Trusted Execution Technology）

TXT 是 DRTM（Dynamic Root of Trust for Measurement），和 BootGuard 的 SRTM 不同：

- **SRTM（BootGuard）**：在開機一開始就建立信任，從 CPU reset 往後的整條鏈都要信任
- **DRTM（TXT）**：OS 已經在跑之後，在需要的時候「重新」建立信任環境（SINIT ACM + MLE）

```
OS 已啟動（此時 BIOS/bootloader 可能已被污染）
  │
  │  GETSEC[SENTER] 指令
  ▼
TXT 流程：
  1. 清空 CPU 快取，進入隔離模式
  2. 用 CPU 內建 ROM 驗 SINIT ACM（Intel 簽章）
  3. SINIT ACM 測量「Measured Launch Environment（MLE）」
     並記錄到 TPM PCR[17-22]
  4. 跳入 MLE（通常是 hypervisor 或安全 OS loader）
```

TXT 的防線在「已知 pre-OS 環境可能被污染的情況下，建立一個可驗證的執行環境」，這和 BootGuard 的方向不同，互補。

**繞過現況**：TXT 的歷史攻擊面包括：
- SINIT ACM 本身的漏洞（Integer overflow in ACM，Intel 有過修補）
- DRTM 完成後 DMA 攻擊（IOMMU 若未正確設定）
- TXT 只量測 MLE，不保護之後的 guest VM

---

## AMD 防線

### AMD PSP / SEV（Ch 14, Ch 41 呼應）

AMD PSP 提供 fTPM（firmware TPM）和 SEV。

**SEV（Secure Encrypted Virtualization）**：VM 記憶體被 PSP 管理的 AES 金鑰加密，hypervisor 看到的是密文。

```
SEV 演化：
  SEV（2016）    → 加密，但無完整性保護，hypervisor 可 replay/remap
  SEV-ES（2020） → 加入暫存器加密，防止 hypervisor 讀 VMCB
  SEV-SNP（2021）→ 加入記憶體完整性（RMP table），防止 hypervisor remap
```

**繞過現況**：
- SEV（無 SNP）："SEVurity"、"undeSEVed" 等論文展示 hypervisor 攻擊
- SEV-SNP：目前 side-channel 攻擊仍有研究空間（CPU cache timing）
- PSP fTPM 的 ECDSA timing side-channel（2023，AMD 已修補）

### AMD AGESA 的 SMM 保護

AMD 平台的 SMM 保護由 AGESA（AMD Generic Encapsulated Software Architecture）實作，概念和 Intel 的 SMM Supervisor 類似，但成熟度較低，歷史上有多個 SMM buffer overflow（透過 AGESA 的 SMM handler）被報告。

---

## Microsoft 防線：Secured-core PC

Secured-core PC 是目前廠商緩解的整合最好的架構，把多個分散的機制整合成一個完整框架。

**本節 Secured-core 真機環境未實測。以下為文件與公開報告分析。**

### Secured-core PC 的四個支柱

```
Secured-core PC
  ├── 1. DRTM（依賴 Intel TXT 或 AMD SKINIT）
  │       OS 啟動時用 DRTM 建立可驗證的起點
  │
  ├── 2. VBS（Virtualization-Based Security）
  │       Hyper-V 建立 VTL0（正常 OS）和 VTL1（安全環境）的隔離
  │
  ├── 3. HVCI（Hypervisor-Protected Code Integrity）
  │       驅動程式程式碼完整性由 VTL1 的 hypervisor 強制執行
  │       → 惡意 kernel driver 無法執行（Ring 0 exploit 影響大降）
  │
  └── 4. System Guard（Secure Launch + Runtime Attestation）
          DRTM 建立信任後，System Guard 持續監控
          Runtime Attestation 可對遠端驗證平台完整性
```

### DRTM 在 Secured-core PC 的角色

Windows Secure Launch（System Guard Secure Launch）流程：

```
系統啟動完成（UEFI → bootloader → Windows kernel）
  │
  │  VBS/Hyper-V 啟動時觸發 DRTM
  │
  ▼
  Intel TXT GETSEC[SENTER] / AMD SKINIT
  │  → 隔離環境，量測 Hyper-V 的 core components
  │  → 記錄到 TPM PCR[17+]
  ▼
Hyper-V 在量測後啟動，VTL1 建立
  │
  ▼
VSM（Virtual Secure Mode）啟動
  ├── Credential Guard（LSASS 記憶體在 VTL1）
  ├── Device Guard → 演化為 HVCI
  └── TPM-based attestation
```

為什麼 DRTM 比 SRTM 重要？因為 BootGuard（SRTM）保護的是「開機鏈」，但如果 UEFI 本身被植入後門（例如 BlackLotus，Ch 30）並持久化，SRTM 的測量值也會包含惡意程式碼——TPM 只是量測，不阻止。DRTM 讓 Windows 可以「繞過被污染的 UEFI，重新建立一個乾淨的信任起點」。

### Pluton 安全處理器

微軟的 Pluton（2020 年宣布，AMD Ryzen 6000 / Intel 12th Gen 部分機型有整合）：

```
傳統架構：
  CPU ←────────── PCIe/LPC ──────────── TPM 晶片
        攻擊面：bus sniffing（Ch 40）、fake TPM

Pluton 架構：
  CPU die 內整合安全處理器
  └── 不走外部 bus → bus sniffing 無效
      韌體由 Windows Update 更新（微軟直接控制）
      金鑰儲存在 CPU die 內部
```

Pluton 的主要優點：
- 消除 TPM bus sniffing 攻擊面（Ch 40 的 Logic Analyzer 攻擊完全失效）
- 微軟可直接更新 Pluton 韌體，不依賴 OEM 時程

**繞過現況**：Pluton 目前研究較少（部署量還不夠大），已知關注點：
- Pluton 韌體本身的攻擊面（ARM-based，類 TrustZone 架構）
- 微軟集中控制更新 → 政治 / 供應鏈風險（不同威脅模型下的考量）

---

## ARM 防線

### TF-A Measured/Verified Boot（Ch 15-16）

```
ARM 開機鏈（有完整防線時）：
  BL1（BootROM，不可修改）
    │ 驗 BL2 簽章
    ▼
  BL2（可信的 bootloader 階段）
    │ 驗 BL31（Secure Monitor）、BL33（UEFI/U-Boot）
    ▼
  BL31（EL3 Secure Monitor，ATF runtime）
    │ 設定 TrustZone 邊界，啟動 BL32（OP-TEE）
    ▼
  BL33（Non-secure world：U-Boot / UEFI）
    │ AVB（Android Verified Boot）
    ▼
  Android/Linux kernel
```

**防線強度評估**：
- BL1（BootROM）是固化在晶片的，是整個 ARM 信任鏈的錨點
- BL31 的 SMC 介面（Secure Monitor Call）是攻擊面（TF-A 歷史 CVE 集中在此）
- fuse 未燒 / 測試金鑰 → 整條鏈無效（Ch 21 類型學）

### Android AVB / Rollback Protection

Android Verified Boot 2.0（AVB）是 Ch 19 的主題。防線要點：

```
Rollback Protection 需要三個條件同時成立：
  1. partition 帶 rollback_index
  2. RPMB / fuse 儲存最小允許版本
  3. TEE 正確驗證（且失敗時不 fallback）
```

現況：廠商實作品質差異大。大廠（Google Pixel、Samsung）較完整，許多中低端 Android 仍用 eMMC misc 分區存 rollback counter（Ch 21 T4 直接適用）。

---

## UEFI 社群防線

### UEFI 2.7+ NX/Memory Protection

UEFI 規範 2.7 後加入：
- **NX for Data（non-executable data pages）**：UEFI data sections 標記 non-executable，防止 data-only 注入後執行
- **NX for Heap**：Pool 分配的記憶體預設 NX
- **Stack Canary**：部分 EDK2 build 支援

```
UEFI Memory Attribute Protocol：
  gDS->GetMemorySpaceMap() 取得所有記憶體區間
  屬性設定：EFI_MEMORY_RO（唯讀）、EFI_MEMORY_XP（NX）

DXE Foundation 啟動後（Decompressor 執行完）：
  SetMemorySpaceAttributes() 把 code sections 設 RO+X
  data sections 設 NX
```

**繞過現況**：許多 OEM 的 EDK2 build 關掉這些保護（`PcdDxeNxMemoryProtectionPolicy = 0`），因為舊的 OEM driver 假設 data 可執行。CHIPSEC 可以掃這個設定（`chipsec_main -m common.uefi.access_uefispec`）。

### SMM Page Protection

SMM Page Protection 是 EDK2 StandaloneMmPkg 和 MdeModulePkg 的機制：SMM 進入點建立獨立的 page table，SMRAM 以外的記憶體全部設 non-executable。

Ch 12 講的「SMM callout」在 SMM Page Protection 啟用後，跳到 SMRAM 外部執行就會觸發 #PF，讓 callout 直接崩潰（攻擊者拿不到執行流）。

**繞過現況**：攻擊面從「callout 到 non-SMRAM code」轉為「在 SMRAM 內找 ROP gadget」，難度大增但不是不可能。

### dbx / SBAT 撤銷（Ch 32）

UEFI Secure Boot 的撤銷機制：
- **dbx**：SHA-256 / RSA pubkey hash 黑名單，在 DXE 載入 EFI 前查
- **SBAT**：GRUB2 / shim 引入的基於 Generation Number 的撤銷，可以批次撤銷一整個版本系列

BootHole（CVE-2020-10713）之後 SBAT 才引入，設計目標是解決「dbx 要列一堆 hash、體積爆炸」的問題。

---

## 攻擊面 → 緩解 → 繞過現況 總表

| 攻擊面 | 課程對應 | 緩解機制 | 緩解強度 | 現況繞過方向 |
|--------|---------|---------|---------|------------|
| IBB / 初始 BIOS 程式碼竄改 | Ch 14 | Intel BootGuard (Verified Boot) | 高（fuse 已燒時） | OEM 未燒 fuse；私鑰洩漏（PKfail）；故障注入 |
| SPI flash 軟體寫入 | Ch 5/35 | Intel BIOS Guard；SPI PR 寫保護 | 中 | SMM exploit 繞過 SMI handler；實體焊接 |
| SMM callout / 指標竄改 | Ch 12 | SMM_Code_Chk_En；SMM Page Protection | 高 | SMRAM 內 ROP；Supervisor 初始化 race |
| SMM Supervisor 繞過 | Ch 13 | Intel RSI / SMM Supervisor | 中（新機型） | Supervisor code 本身攻擊面；部分 OEM 未啟用 |
| ME 遠端認證繞過 | Ch 14 | ME 韌體更新；AMT 停用；VLAN 隔離 | 高（已修補版本） | 未更新 ME；內網 AMT 存取 |
| ME 被植入後門 | Ch 14 | BootGuard 含 ME 分區？（部分平台）；HAP bit | 低-中 | HAP 不消除 BootROM；需 ME JTAG |
| PSP 漏洞 | Ch 14 | AGESA 更新；SEV-SNP | 中 | AGESA SMM buffer overflow 歷史；SNP side-channel |
| DXE driver 注入 | Ch 4 | UEFI NX Memory Protection；Secure Boot DB | 中 | OEM 多關保護；UEFI shell 繞 db |
| Secure Boot 繞過 | Ch 28-32 | dbx/SBAT；BootGuard（鏈上層） | 中（dbx 需及時更新） | BlackLotus rollback；BootHole 類型 |
| SMM variable 竄改 | Ch 5 | SMM-based variable 存取限制；Secure Flash | 中 | variable 攻擊面仍有 CVE（BRLY 系列）|
| ARM TF-A SMC 攻擊 | Ch 16 | TF-A SMC 白名單；EL3 hardening | 中 | TF-A CVE（SMC handler OOB）|
| AVB rollback bypass | Ch 19 | RPMB + TEE rollback；fuse | 低-高（廠商差異大）| rollback 存 misc（T4）；TEE 初始化失敗 |
| bootkit 持久化 | Ch 31 | DRTM（TXT/SKINIT）；Secured-core | 中-高（需 DRTM）| BIOS update 持久；VBS 未啟用 |
| TPM bus sniffing | Ch 40 | Pluton（整合 CPU die）；encrypted bus | 低（外置 TPM）/ 高（Pluton）| 外置 LPC/SPI TPM 仍可 sniff |
| SPI TOCTOU | Ch 35 | BIOS Guard；SPI flash 寫保護 | 中 | 多 CPU 時序攻擊；glitch ACM |
| 故障注入繞驗章 | Ch 34 | 無純軟體防線（需硬體對策）；電壓監控 | 低 | ChipWhisperer glitch；EMI injection |
| 供應鏈 IBB 植入 | Ch 14/26 | fuse 燒入時機（植入在 fuse 前無效）；SBOM | 中 | 工廠環節植入已簽章惡意 IBB |
| UEFI 後門 / 韌體 diff | Ch 26 | Binarly/Eclypsium 掃描；LVFS | 低（被動偵測）| LogoFAIL；OEM diff 難以自動比對 |

---

## 防線的整合視圖

把所有防線放回六個層次：

```
層次 6：OS / bootloader
  ├─ Windows Secure Boot（db/dbx/SBAT）
  ├─ GRUB SBAT generation
  └─ Android AVB + RPMB rollback

層次 5：UEFI 韌體主體
  ├─ UEFI NX Memory Protection（DXE/PEI）
  ├─ SMM Page Protection（SMRAM 不可執行外部）
  ├─ Secure Flash（BIOS Guard Protocol）
  └─ SPI PR / BIOS_CNTL 寫保護

層次 4：SMM（Ring -2）
  ├─ SMM_Code_Chk_En（callout 變 #GP）
  ├─ SMRR（SMRAM 對外 cache 屬性隔離）
  ├─ SMM Supervisor / Runtime SMM Isolation
  └─ CHIPSEC 模組可稽核（practice-b）

層次 3：ME / PSP（Ring -3）
  ├─ ME 韌體簽章（Intel 私鑰，無法第三方刷）
  ├─ AMD PSP 韌體簽章（AMD 私鑰）
  ├─ HAP bit / me_cleaner（停用 ME 大部分功能）
  └─ HECI / PSP Mailbox 的 kernel filter

層次 2：信任根 / 量測
  ├─ Intel BootGuard（SRTM，fuse 不可更改）
  ├─ Intel TXT + AMD SKINIT（DRTM，OS 後建立）
  ├─ Microsoft Secured-core PC（整合 DRTM + VBS + HVCI）
  ├─ Microsoft Pluton（消除 TPM bus sniffing）
  └─ ARM TF-A BL1（BootROM，chip 固化）

層次 1：硬體 / SPI
  ├─ SPI flash 寫保護（硬體 WP pin）
  ├─電壓監控 / 防 glitch 電路（部分 HSM / 工業板）
  └─ 無純軟體防線（物理存取 = game over in most cases）
```

---

## 哪個廠商的防線最完整？

從攻擊研究者的視角給個直接評估：

**最強**：Microsoft Secured-core PC（Dell / HP 高階商用機，Lenovo ThinkPad 高端線）
- DRTM + VBS + HVCI 三層疊加
- Pluton（若有）消除 TPM 物理攻擊
- 問題：需要 OEM 全套啟用，消費機型多有缺項

**次強**：Apple Silicon（M1/M2/M3，本課不在範圍但值得對照）
- 自研 SoC，BootROM 完全整合，不依賴 OEM 可選 fuse
- Secure Enclave 完全整合在 die
- 代價：封閉性，研究難度高

**中等**：主流 Intel + BootGuard 已燒 fuse 的商用機
- BootGuard 有效但孤立（沒有 DRTM 的平台 Secure Boot 被 bypass 就結束了）
- 很多消費機沒有 Secured-core 認證

**最弱**：無 fuse 燒入的裝置、嵌入式 / IoT 廠商
- 研究空間最大
- 真實 bootkit 目標（工業控制、路由器、嵌入式）

---

## 踩雷

1. **「BootGuard 開著」不等於「整條信任鏈安全」**：BootGuard 只保護 IBB。IBB 後面的 DXE driver（Ch 4）、UEFI variable（Ch 5）、Secure Boot DB 的撤銷（Ch 32）各有獨立問題。打通一個不代表打通全部。

2. **BIOS Guard ≠ BootGuard**：名字像但機制不同。BIOS Guard 是「防止軟體寫 SPI」，BootGuard 是「驗 IBB 簽章」。兩個都沒啟用的平台（常見於舊 OEM）才是最脆弱的。

3. **Secured-core 的 DRTM 需要 BIOS 配合**：如果 UEFI 本身有 bug（LogoFAIL 類型，Ch 30），DRTM 啟動前的視窗可以被利用。DRTM 之後的測量才可信，之前的不行。

4. **SMM Supervisor 的「隔離」不是沙盒**：OEM SMM driver 跑在 User 層，但 User 層仍在 Ring -2，仍有 SMRAM 的部分存取能力。Supervisor 限制的是「特定 MSR 和 SMRAM 管理操作」，不是全面沙盒。

5. **Pluton 的普及率還很低**：2024 年底能買到的 Pluton 設備仍是少數。大多數企業的端點仍用外置 discrete TPM，bus sniffing 仍是有效攻擊（Ch 40）。

6. **SEV-SNP 不等於 SEV**：很多資料提 AMD SEV 時混用，SEV（無 SNP）已有多個完整繞過論文，SEV-SNP 研究還在進行中。評估 AMD 平台時要確認 SNP 是否啟用。

---

## 進階延伸

- **Binarly FwHunt / FWID**（https://fwcheck.binarly.io）：免費韌體掃描服務，上傳 BIOS binary，自動比對已知 CVE 和弱點模式。比 CHIPSEC 更適合快速初步評估。

- **Microsoft Secured-core PC 規格**（https://aka.ms/SCPCwp）：官方白皮書，列出每項防線的硬體要求和配置測試方法，是評估目標機器「缺哪條防線」的最佳 checklist。

- **Platform Security Summit 系列演講**（Intel/AMD/Microsoft 聯辦）：每年更新，把最新緩解和研究社群反應都有整合。2022-2024 年的內容最直接反映 BlackLotus 之後的業界回應。

---

## 動手練習

**以下練習以 QEMU + OVMF 環境為主，Secured-core 真機項目標「未實測」。**

1. **確認 OVMF 的 NX 保護狀態**：啟動帶 Secure Boot 的 OVMF，進 UEFI Shell，執行 `memmap` 看記憶體屬性。確認 DXE code 區是否標 `EFI_MEMORY_WP`（write-protected）而非只有 `EFI_MEMORY_RUNTIME`。

2. **CHIPSEC SMM 保護稽核（未實測）**：在真實 Linux 上（非 VM）安裝 CHIPSEC，執行：
   ```bash
   # 未實測，需 root + CHIPSEC kernel module
   sudo python chipsec_main.py -m common.ia_untrusted
   sudo python chipsec_main.py -m smm.smm_code_chk
   ```
   理解輸出中 `SMM_Code_Chk_En`、`D_OPEN`、`SMRR_ENABLE` 各 bit 的意義。

3. **Secured-core PC 自我診斷（未實測，需 Windows 11 真機）**：
   ```powershell
   # 確認 VBS 狀態
   Get-CimInstance -ClassName Win32_DeviceGuard -Namespace root/Microsoft/Windows/DeviceGuard
   # SecurityServicesRunning = 2 → HVCI 啟用
   # VirtualizationBasedSecurityStatus = 2 → VBS 啟用
   ```

4. **對照 PKfail 受影響清單**：去 Binarly 的 PKfail 公開 IoC 頁面，把你手邊或實驗室的設備型號比對，確認是否在受影響名單內（看 UEFI DB 的 PK/KEK 金鑰 hash 是否匹配洩漏清單）。

---

## 本章重點

- Intel 防線分三層：BootGuard（信任根 fuse）→ BIOS Guard（SPI 寫保護）→ SMM Supervisor（callout 防禦）；三個獨立機制，缺一不補一
- AMD 對應：PSP（類 ME）+ SEV-SNP（VM 記憶體完整性）；AGESA SMM 的歷史弱點是重點
- Microsoft Secured-core PC 把 DRTM + VBS + HVCI 整合成一個框架，是目前最完整的 x86 緩解組合；Pluton 進一步消除 TPM 物理攻擊面
- ARM 的防線從 BootROM（chip 固化）往上；TF-A 和 AVB 的品質取決於廠商實作
- UEFI 社群防線（NX、SMM Page Protection、SBAT）都有「OEM 是否真的開了」的問題
- 攻擊研究的視角：每一層的緩解都有對應繞過，但組合多層後成本急劇升高——最終防線的弱點往往不是技術，而是「廠商有沒有真的全部啟用」

---

## 自我檢核

- [ ] 能說出 BootGuard 和 BIOS Guard 的本質差異（驗章 vs 寫保護）
- [ ] 能解釋 SRTM 和 DRTM 的分工，以及各自在 Secured-core PC 的角色
- [ ] 知道 SMM_Code_Chk_En 防的是什麼，以及為什麼 SMM Supervisor 之後攻擊難度改變
- [ ] 能說出 SEV / SEV-ES / SEV-SNP 三個版本的防線差異
- [ ] 能把全課的至少五個攻擊章節（Ch 5/12/14/30/40）對應到本章總表的緩解欄位
- [ ] 知道 Pluton 的設計目標和它消除的具體攻擊面（TPM bus sniffing）
- [ ] 理解「廠商有沒有真的全部啟用」為什麼比「技術上有緩解」更重要

---

## 延伸閱讀

1. **"Platform Security Assessment" — Binarly（2022–2024 系列部落格）**
   讀哪裡：https://www.binarly.io/blog，特別是 PKfail（2024）和 LogoFAIL（2023）報告
   學什麼：真實 OEM 廠商防線的落地品質評估，了解哪些廠商真的有開哪些防線，哪些只有「規格上支援」
   關聯：本章總表的「現況繞過方向」欄位，所有 Binarly 案例都落在「廠商未正確設定」這類

2. **"Microsoft Secured-core PC: A Deep Dive" — Microsoft Security Blog**
   讀哪裡：https://aka.ms/SCPCwp；配合 MSRC 的 Virtualization-Based Security (VBS) 說明
   學什麼：DRTM/VBS/HVCI/System Guard 四個支柱的具體啟用條件、硬體需求、和驗證方法
   關聯：直接對應本章「Secured-core PC」一節，是評估 Windows 目標機器最重要的 reference

3. **"Attacking and Defending Firmware: The 2023 State of Play" — Eclypsium**
   讀哪裡：https://eclypsium.com/research/（每年的 annual state of firmware security）
   學什麼：整個韌體安全生態的年度快照，包含廠商緩解部署率（多少比例的商用機實際開了 BootGuard）
   關聯：本章「哪個廠商的防線最完整」的量化資料來源，讓本章評估從定性變成定量

→ [下一章](./45-detecting-bootkits.md)
