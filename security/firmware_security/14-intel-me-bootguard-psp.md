# Ch 14 — Intel ME / AMT / BootGuard 與 AMD PSP

> **目標**：理解 x86 平台上比 SMM 更底層的兩個安全子系統——Intel ME（Ring -3）與 AMD PSP——的架構、歷史漏洞、以及 Intel BootGuard 如何把「誰可以改 BIOS」這件事用矽燒定（fuse）的方式鎖住。

**本章攻擊細節為理論描述或引用公開資料。** ME/PSP 執行於獨立處理器，需要 JTAG 或廠商工具才能真實驗證，一般研究環境無法重現完整利用鏈。攻擊手法部分明確標示「未實測」。

---

## 為什麼 ME 和 PSP 比 SMM 更難對付

SMM 是 Ring -2，但 SMM 程式碼終究跑在主 CPU 上，OS 的觀測工具（perf、JTAG、處理器 trace）在技術上可以接觸到它。Intel ME 和 AMD PSP 是**完全獨立的處理器**，有自己的 CPU 核心、SRAM、ROM、韌體映像，甚至自己的作業系統（ME 上跑 Minix 3 的衍生版本，或舊版 ThreadX RTOS）。

它們的特點是：

1. **在主 CPU 啟動之前就執行**（BootROM 跑在 CPU reset vector 之前的某個時間點）
2. **主 CPU 關閉時仍可運作**（只要 PCH 有電，ME 就活著——ATX 電源的 5V standby 夠用）
3. **無法被 OS 或 hypervisor 偵測到其執行狀態**（ME 的記憶體不在 E820 map 裡，主 CPU 看不到）
4. **網路遠端管理能力**（Intel AMT，Active Management Technology）

這就是「Ring -3」這個稱呼的來源：比 SMM 的 Ring -2 還低一層，且完全不在主 CPU 的特權架構內。

---

## Intel ME（Management Engine）架構

### 一張圖看懂 ME 的位置

```
  主機板上的電源線（ATX 5Vsb 常在）
         │
         ▼
  ┌─────────────────────────────────────────────────────┐
  │  PCH（Platform Controller Hub，南橋）               │
  │  ┌─────────────────────────────────────────────┐   │
  │  │  Intel ME 子系統                             │   │
  │  │  ├── 獨立 CPU 核心                          │   │
  │  │  │   早期（Gen 1-9）：ARC 600               │   │
  │  │  │   Gen 12+：Intel Atom（x86 Minute IA）   │   │
  │  │  ├── 獨立 SRAM（~1-4 MB，主 CPU 看不到）    │   │
  │  │  ├── ROM（不可修改的 BootROM）               │   │
  │  │  └── ME 韌體分區（存在 SPI flash 的 ME 區） │   │
  │  │                                              │   │
  │  │  ME 服務：AMT / Remote Management / PTT /   │   │
  │  │           身分認證 / HDCP / DRM              │   │
  │  └─────────────────────────────────────────────┘   │
  │                                                     │
  │  PCH 同時管理：SATA / USB / PCIe / LPC / SPI       │
  └─────────────────────────────────────────────────────┘
         │                          │
         ▼                          ▼
    主 CPU（Ring 0/3）          SPI Flash
    OS 在這裡跑                  ├── BIOS 分區
                                  ├── ME 分區（加密）
                                  └── PDR/GbE 等分區
```

主 CPU 透過 HECI（Host Embedded Controller Interface）和 ME 溝通，這是一個 PCI 裝置（Vendor ID 0x8086，Class 0x0780）。

### ME 韌體結構

ME 韌體存在 SPI flash 的 ME 分區（Flash Region 2，Descriptor 的 FLMSTR 欄位定義邊界）。結構大致如下：

```
SPI Flash ME 分區
├── Manifest（RSA 簽章過的 header，BootROM 驗章）
├── Code Partition（加密 + 壓縮）
│   ├── BUP（Bring-Up Manager）  ← ME 開機後先跑這個
│   ├── KERNEL（ME OS kernel）
│   ├── 各服務 module（AMT、PTT、DAL...）
│   └── DAL（Dynamic Application Loader，跑 Java applet）
└── Data Partition（設定、憑證、金鑰）
```

ME 韌體是 **RSA 簽章 + Huffman 壓縮**（早期版本），後期轉為 LZ4 / 專有壓縮格式。Intel 不公開金鑰，ME 韌體無法被合法替換（理論上）。

ME 在 POST 之前完成初始化，之後進入待命狀態，主 CPU 的 reset vector 才被釋放（ME 通過一個硬體信號讓 PCH 放開 RESET# 線）。

### AMT（Active Management Technology）

AMT 是 ME 上跑的一個服務模組，提供遠端管理能力：

- 透過獨立的 802.1Q VLAN（不需 OS 配合）進行網路通訊
- 可遠端 KVM、電源控制、BIOS 設定存取、硬體 serial console
- 預設監聽 TCP 16992（HTTP）和 16993（HTTPS）
- 在 OS 關機時仍可運作（PCH 有 5Vsb）

這在企業環境是合法的 IT 管理功能。在資安研究者眼中，它是一個常在網路上暴露、又跑在主 CPU 完全看不到的地方的服務。

---

## ME 的歷史攻擊案例

### CVE-2017-5689：AMT 認證繞過

這是迄今影響最廣的 ME/AMT 漏洞，2017 年由 Embedi 研究員 Maksim Malyutin 發現，影響 Intel AMT 韌體版本 6.0 到 11.6。

**漏洞機制**（根據公開技術報告）：

AMT 的 Digest 認證流程中，比較使用者提供的 response hash 時有一個長度計算錯誤：

```
// 理論重建（非真實 AMT 原始碼）
// 正常流程：
expected_hash = compute_digest(username, realm, password, nonce, ...)
actual_hash   = parse_http_header("Authorization: Digest response=...")
if (memcmp(expected_hash, actual_hash, strlen(actual_hash)) == 0)
    // 認證通過

// 漏洞：strlen(actual_hash) 在 actual_hash 為空字串時返回 0
// memcmp 比較 0 個 byte，永遠返回 0（相等）
// 攻擊者送一個空的 response 欄位即可繞過認證
```

**實際影響**：攻擊者只需能連到 TCP 16992/16993，發送 Digest response 欄位為空（或長度為 0）的 HTTP 請求，即可獲得 AMT 完全存取權限，包括 KVM 和 IDE Redirection（可掛載虛擬 ISO 重灌系統）。

**CVE 資訊**：CVE-2017-5689，CVSS 9.8（Critical）。所有啟用 AMT 的企業級 Intel 平台（2010-2017 年間的主流商用電腦）。

**修補**：更新 ME 韌體到修補版本，或停用 AMT。

### Ring -3 rootkit 研究

2009 年，Joanna Rutkowska（Invisible Things Lab）在 Black Hat 展示了針對早期 Intel ME 前身（IPMI/IME）的 rootkit 概念。2013-2015 年間，多個研究团隊陸續分析 ME，發現：

- ME 有 JTAG 存取點（在某些平台）
- 早期版本的 ME 韌體更新流程可被竄改
- DAL 的 Java sandbox 有逃逸攻擊面

「Ring -3 rootkit」的概念是：在 ME 韌體中植入惡意模組，主 CPU 上的所有 OS 和 hypervisor 完全無法偵測或清除，因為 ME 的記憶體根本不在主 CPU 的位址空間裡。

**本段為研究摘要，具體植入步驟未實測。** 完整工具鏈（me_cleaner 等）主要用於移除 ME，而非植入後門。

### ME 停用：HAP bit 與 me_cleaner

Intel 在 NSA 要求下，提供了一個隱藏的「High Assurance Platform」（HAP）模式，透過 Flash Descriptor 的一個 bit 停用 ME 大部分功能（ME 進入精簡化狀態，只保持最低限度讓 CPU 開機）。

```
Flash Descriptor Region 0（FDR），offset 0x10，bit 20（HAP bit）
設為 1 → ME 進入 HAP/Alt ME 模式，大部分服務停用
```

`me_cleaner`（https://github.com/corna/me_cleaner）是一個 Python 工具，可以在 SPI flash image 中清除 ME 韌體的大部分分區、設定 HAP bit，讓 ME 進入最小化模式。這在特定硬體上可行，但可能讓系統無法正常開機（部分硬體依賴 ME 初始化）。

---

## Intel BootGuard

### 問題的本質

就算 BIOS 有簽章驗證，如果 SPI flash 被替換，或者有人在製造環節竄改了 BIOS，誰來驗「第一段執行的 BIOS 程式碼」的合法性？

UEFI Secure Boot 驗的是 bootloader 和 OS，但它自己的第一段程式碼（Initial Boot Block，IBB）呢？UEFI 韌體本身的完整性，在沒有 BootGuard 的情況下，只能靠 SPI flash 的寫保護（軟體可關閉）和 TPM 的 PCR[0] 測量（測量但不阻止）。

Intel BootGuard 的答案：**把信任根（Root of Trust for Verification）燒進 CPU 的 fuse，使其不可更改。**

### BootGuard 架構

```
開機流程（有 BootGuard）：
                                           ┌─────────────────┐
CPU Reset                                  │   CPU ACM ROM   │
    │                                      │  （在 CPU die   │
    │                                      │   裡，唯讀）     │
    │                                      └────────┬────────┘
    ▼                                               │ 驗章 ACM
┌────────────────────────┐    ACM 執行              │
│  ACM（Authenticated    │◄─────────────────────────┘
│  Code Module）         │
│  Intel 簽章的二進位碼  │
│  從 SPI flash 載入     │
│  在隔離的 CPU 快取執行 │
└────────────────────────┘
    │
    │ 用 OEM public key hash（燒在 CPU fuse）驗 IBB 簽章
    ▼
┌────────────────────────┐
│  IBB（Initial Boot     │ ← SPI flash 的第一段 BIOS 程式碼
│  Block）               │   必須由 OEM 私鑰簽章
│  BIOS 的起點           │
└────────────────────────┘
    │
    │ IBB 驗完後才交給正常 UEFI 流程
    ▼
  UEFI DXE / BDS → Secure Boot → bootloader → OS
```

ACM（Authenticated Code Module）是 Intel 製作、OEM 嵌入 SPI flash 的一個特殊二進位模組，CPU 在早期啟動階段用 CPU 內建 ROM 驗它的 Intel 簽章，然後讓它執行。ACM 接著讀 OEM 公鑰 hash 的 fuse 值，驗 IBB 的 OEM 簽章。

### BootGuard Profile

BootGuard 有三種 profile，由 fuse 決定（一旦燒入不可更改）：

| Profile | 動作 | 效果 |
|---------|------|------|
| Disabled | 不驗 IBB | 等同沒有 BootGuard |
| Measured Boot Only | 把 IBB hash 記入 TPM PCR[0] | 有測量，但驗證失敗不阻止開機 |
| Verified Boot | 驗 IBB 簽章，失敗則關機 | 真正的信任鏈起點 |
| Measured + Verified | 兩者都做 | 最強，企業/高安全需求 |

**Key Manifest（KM）** 和 **Boot Policy Manifest（BPM）** 是 SPI flash 中存放 OEM 公鑰與 IBB 範圍定義的結構，由 ACM 讀取並驗證。

### fuse 的意義

BootGuard 的 OEM public key hash（或 KM 的 hash）和 Profile 設定被燒進 CPU package 的 OTP（One-Time Programmable）fuse。這意味著：

- 一旦 fuse 燒入，攻擊者即使能寫 SPI flash，也無法把一個沒有對應私鑰簽章的 IBB 讓 BootGuard 接受
- 燒入也意味著 OEM 自己以後更新 BIOS 也必須用同一把私鑰（或 KM 允許的替代金鑰）
- **未 fuse 的平台**（BootGuard Profile = Disabled 或根本沒燒）等同沒有保護——許多 OEM 在開發階段的測試機器就是這種狀態

### BootGuard 繞過思路（理論）

**本段為理論分析，未實測。**

1. **OEM 未 fuse 的平台**：買二手商用電腦時，早期型號的 BootGuard fuse 可能未燒。直接改 SPI flash 即可，BootGuard 不阻止。CHIPSEC 的 `chipsec_main -m common.bios_guard` 可以檢查 fuse 狀態。

2. **拿到 OEM 私鑰**：如果 OEM 的私鑰被洩漏（例如供應鏈攻擊、OEM 內部人員），攻擊者可以對任意 IBB 簽章，通過 BootGuard 驗證。PKfail（Binarly 2024）發現多家 OEM 的 Secure Boot 金鑰在公開的 GitHub repo 或測試環境中暴露，邏輯上類似。

3. **攻擊 ACM 本身**：ACM 是 Intel 簽章的，但如果 ACM 本身有漏洞（歷史上有 ACM 的 bounds check 問題被報告），可能在 ACM 執行階段劫持控制流，在 BootGuard 驗章邏輯執行前植入 rootkit。

4. **物理攻擊（BIOS 晶片拆卸替換）**：BootGuard Verified Boot 的啟動鏈從 fuse 出發，但如果攻擊者能物理替換 SPI flash 晶片並植入後門 IBB，然後用 voltage fault injection 繞過 ACM 驗章，BootGuard 形同虛設。此路徑需要物理存取，參見 Part 6。

5. **供應鏈攻擊：在 OEM 生產線植入**：這是國家級攻擊者的玩法。如果在 OEM 工廠植入惡意 BIOS（在 fuse 燒入之前），那個惡意版本就帶有合法的 OEM 簽章。

---

## AMD PSP（Platform Security Processor）

### 架構對比

AMD PSP（在新平台改名為 AMD Security Processor，ASP）是 AMD 在 Zen 架構（2017 年後）引入的安全子系統，概念對應 Intel ME，但設計有所不同。

| 特性 | Intel ME | AMD PSP/ASP |
|------|---------|------------|
| 處理器架構 | ARC（舊）/ Atom（新） | ARM Cortex-A5（TrustZone capable） |
| 位置 | PCH（南橋） | 整合在 CPU die 內 |
| BootROM | 不可修改（CPU 內部 ROM 驗 ME） | BootROM 在 SoC 內，但部分可讀 |
| 韌體來源 | SPI flash ME 分區 | SPI flash PSP 分區 |
| 加密 | RSA 簽章 + 專有壓縮 | RSA 簽章（AMD 私鑰） |
| 主機通訊 | HECI（PCI 裝置） | PSP Mailbox（MMIO） |
| 遠端管理 | AMT（完整） | 無（PSP 無 AMT 等同功能） |
| 安全功能 | PTT（fTPM）、DRM、DAL | fTPM、SEV（Secure Encrypted Virtualization）、SMU |

### PSP 的主要功能

```
PSP 開機流程（理論）：
─────────────────────────────────────────────
CPU Power On
  │
  ▼
PSP BootROM 執行
  ├── 驗 PSP 韌體（SPI flash PSP 分區）的 AMD 簽章
  ├── 驗 AGESA（AMD Generic Encapsulated Software Architecture）
  │   → AGESA 是初始化記憶體、PCIe、CPU 的韌體
  ├── 設定 SEV（若啟用，加密 VM 記憶體）
  └── 釋放 x86 主 CPU，交出執行權

PSP 持續服務：
  ├── fTPM（Firmware TPM，PSP 模擬 TPM 2.0 功能）
  ├── 金鑰管理（Secure Key Storage）
  └── SMU（System Management Unit，電源管理）
─────────────────────────────────────────────
```

### PSP 的漏洞歷史

相比 Intel ME，AMD PSP 的公開研究較少，但已有數個重要案例：

**2018 年 CTS-Labs 報告**（爭議性）：  
CTS-Labs 發表報告聲稱發現 AMD PSP 的多個漏洞（「Ryzenfall」、「Masterkey」、「Chimera」等），但公告方式跳過了正常 responsible disclosure，報告在 24 小時後公開。AMD 最終承認其中部分問題真實存在，並發布 AGESA 更新。技術細節被指誇大，需要本地 admin 或 ring-0 才能觸發，並非「遠端任意 RCE」級別。

**2023-2024 PSP 韌體解包研究**：  
研究員使用公開的 AMD 韌體更新包（BIOS 廠商提供）解包 PSP 分區，對 Cortex-A5 的 ARM 程式碼做靜態分析，發現數個 AGESA 的 SMM handler 有 buffer overflow 問題（部分影響 AGESA 的 SMM 實作，間接影響 PSP 初始化路徑）。

**SEV 的記憶體加密繞過**：  
AMD SEV（Secure Encrypted Virtualization）讓 VM 的記憶體被 PSP 管理的金鑰加密，hypervisor 看不到 VM 內容。但研究（"SEVurity", "undeSEVed" 等論文）顯示早期 SEV（不含 SEV-SNP）的完整性保護不足，hypervisor 可以在加密 VM 的記憶體上做 replay 或 remap 攻擊，達到任意讀寫效果。

---

## ME vs BootGuard vs PSP 功能對照

```
信任鏈層次（Intel 平台）：
┌─────────────────────────────────────────────────────────────┐
│  CPU fuse（OEM public key hash）—— 不可更改                  │
│  這是整個 trust chain 的起點，矽燒的                         │
└───────────────────────┬─────────────────────────────────────┘
                        │ ACM 驗章
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  Intel ACM（CPU ROM 驗，Intel 簽章）                         │
│  → 驗 IBB 的 OEM 簽章                                        │
└───────────────────────┬─────────────────────────────────────┘
                        │ IBB 通過驗章
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  IBB → UEFI DXE → Secure Boot（db 驗 EFI）→ OS             │
└─────────────────────────────────────────────────────────────┘

ME 在整個流程中的角色：
  ├── ME BootROM 驗 ME 韌體（SPI flash 的 ME 分區）
  ├── 協助 PCH 初始化（ME 是 PCH 的大腦）
  ├── 釋放 CPU reset 信號（某些平台 ME 控制 CPU 的 PLTRST#）
  └── 之後提供持續服務（AMT、PTT、fTPM delegation...）
```

---

## ME/PSP 的防禦與緩解

| 威脅 | 緩解措施 | 說明 |
|------|---------|------|
| AMT 認證繞過（CVE-2017-5689 類） | 更新 ME 韌體；停用 AMT；防火牆封 TCP 16992/16993 | 最直接的緩解，企業 IT 應設 VLAN 隔離 AMT 流量 |
| ME 韌體被篡改 | BootGuard Verified Boot（驗 ME 分區？部分平台）；SPI PR 保護 ME 分區 | ME 分區的寫保護由 Flash Descriptor 的 FLMSTR 欄位控制 |
| ME 被用作持久後門 | me_cleaner（HAP bit）停用 ME；硬體設計：ME bypass 硬體開關 | LibreBoot/coreboot 平台努力的方向；非所有硬體支援 |
| PSP 韌體漏洞 | AMD AGESA 更新（BIOS 更新）；SEV-SNP（加入完整性保護） | 需要 OEM 及時發布更新 |
| BootGuard fuse 未燒 | OEM 在生產線確保燒 fuse；CHIPSEC 稽核 BootGuard 狀態 | 消費者無法事後修改；buying refurbished 有風險 |
| 私鑰洩漏 | 金鑰管理流程；硬體 HSM 存放私鑰；快速 key rotation + 撤銷 | Secure Boot/BootGuard 的共同軟肋 |

---

## 踩雷

1. **把 ME 和 BIOS 混淆**：ME 韌體存在 SPI flash 的 ME 分區，BIOS 在 BIOS 分區，兩者由 Flash Descriptor 的 FLMSTR（Flash Master access rights）分別保護。BIOS 更新工具通常**不動** ME 分區，ME 更新需要專用的 FWUpdate 工具。誤以為「刷 BIOS = 更新了 ME」導致 AMT 漏洞長期未修。

2. **BootGuard 的 Profile 要看 fuse，不是看 BIOS 設定**：BIOS setup 裡的 BootGuard 選項（如果有）通常只在第一次 fuse 燒入前有效。一旦 fuse 燒入，Profile 就固定了，BIOS 設定改不了。很多研究者在測試平台看到 BIOS 有這個選項，誤以為可以動態切換。

3. **HAP bit 停用 ME 不等於零風險**：me_cleaner 設 HAP bit 後，ME 進入精簡模式，但 BootROM 仍然執行，部分 ME 模組（如 BUP）仍然跑完。HAP 只是讓大部分 ME 服務（AMT、DAL 等）不啟動，ME 核心的存在和攻擊面仍未完全消除。

4. **PSP 不等於 Intel TXT**：Intel TXT（Trusted Execution Technology，DRTM）和 BootGuard（SRTM）是不同的技術。TXT 是在 OS 執行後才啟動的「後期」信任根，BootGuard 是「前期」（SRTM）信任根。AMD 的 SKINIT 指令對應 Intel TXT，PSP 是較接近 ME 的概念。

5. **CVE-2017-5689 的影響範圍比多數人預期大**：AMT 不需要被 IT 設定為「啟用」才有漏洞。只要平台帶 AMT 硬體（Intel vPro 或企業級晶片組），即使 IT 沒有在 BIOS 主動啟用 AMT 功能，仍然可能在 provisioning 狀態中暴露 TCP 16992 端口（取決於 ME 版本和設定）。這讓企業的資產清查工作更複雜。

---

## 進階延伸

### Minix 3 inside ME

2017 年，Jason Donenfeld 發現 Intel ME Gen 11+ 的內部 OS 是 Minix 3 的修改版（由 VxWorks 換過來），這引發了廣泛的安全討論——因為 Minix 作者（Andrew Tanenbaum）表示他對此事毫不知情。ME 裡的 Minix 3 帶有完整的 TCP/IP stack、VFS、driver 框架，讓「ME 是一個不透明的 Ring -3 小 OS」的形象更加具體。

### TOCTOU in ME firmware update

ME 韌體更新流程（透過 HECI 的 FWUpdate protocol）存在 TOCTOU 攻擊面：更新過程需要先驗章，再寫入。在簽章驗証和實際寫入之間，如果有辦法替換記憶體中的 image，可能繞過簽章驗証。具體是否可行依 ME 版本和 PCH 設計而定，現代版本有對此加固。

### AMD fTPM side-channel（TPM-Fail 類）

AMD fTPM 實作在 PSP 的 ARM Cortex-A5 上，2023 年有研究發現 AMD fTPM 的 ECDSA 實作有時間側信道（timing side-channel），可在本地攻擊中從 TPM 操作時間推斷私鑰（影響基於 fTPM 的 BitLocker 金鑰保護）。AMD 發布了更新修復。

---

## 動手練習

**以下為研究路線圖，需對應工具與真實硬體。**

1. **ME 版本識別與 HECI 存取**：在 Linux 上，`lspci | grep MEI` 找 HECI 裝置。`cat /sys/class/mei/mei0/fw_status` 可讀取 ME 狀態 register。安裝 `intelmetool`（https://github.com/zamaudio/intelmetool）可讀取 ME 版本資訊。確認你的平台 ME 版本是否在 CVE-2017-5689 影響範圍內。

2. **BootGuard fuse 查詢**：執行 CHIPSEC（需 root/kernel driver）：
   ```bash
   # 理論命令，未實測
   sudo python chipsec_main.py -m common.bootguard
   ```
   查看輸出中的 `BIOS_GUARD_EN`、`BOOT_GUARD_ACM_STATUS`，確認你的平台是否燒了 fuse、是哪個 Profile。

3. **ME 韌體解包（離線分析）**：從 LVFS 下載你的機器廠商提供的 BIOS 更新包，用 `UEFITool NE` 找 ME 分區，再用 `MEAnalyzer`（https://github.com/platomav/MEAnalyzer）解析 ME 分區 header，確認 ME 版本和 SKU。（ME 分區本身是加密的，無法直接讀取程式碼。）

4. **AMT 曝露掃描**：在你自己的網路上，用 nmap 掃描是否有主機開著 TCP 16992/16993：
   ```bash
   nmap -sV -p 16992,16993 192.168.1.0/24
   ```
   如果發現有機器回應，進一步確認 ME 版本（HTTP 回應 header 通常含版本資訊），評估是否受 CVE-2017-5689 影響。

---

## 本章重點

- Intel ME 是跑在 PCH 的獨立處理器（ARC 或 Atom），有自己的 OS 和網路 stack，主 CPU 完全看不透；AMT 是其中最大的攻擊面
- CVE-2017-5689 是 AMT 認證繞過，Digest response 為空時因長度計算錯誤讓 memcmp 永遠成功，CVSS 9.8
- BootGuard 把 OEM 公鑰 hash 燒進 CPU fuse，讓「誰能簽 IBB」這件事不可更改，這是 x86 SRTM 的起點
- BootGuard 的效力取決於 OEM 有沒有燒 fuse、私鑰有沒有保管好；許多二手商用機和開發板沒燒
- AMD PSP 是 ARM Cortex-A5，整合在 CPU die，提供 fTPM 和 SEV；概念對應 ME，但無 AMT
- ME 停用的最實際方案是 HAP bit + me_cleaner，但不消除 BootROM 層級的攻擊面

---

## 自我檢核

- [ ] 我能解釋 Intel ME 為什麼叫 Ring -3，以及它和主 CPU 的關係（獨立 CPU，HECI 通訊，不在 E820 map）
- [ ] 我能描述 CVE-2017-5689 的根本原因（strlen 為 0 的 memcmp，Digest 認證繞過）
- [ ] 我能說出 BootGuard 的信任根在哪裡（CPU fuse），以及 ACM、IBB 在啟動流程中的順序
- [ ] 我能說出 BootGuard 三個 Profile 的差異（Disabled / Measured / Verified）
- [ ] 我能解釋「OEM 未燒 fuse」的意義，以及 CHIPSEC 如何確認 fuse 狀態
- [ ] 我能描述 AMD PSP 的架構（ARM Cortex-A5，fTPM，SEV，位在 CPU die）
- [ ] 我能說出 HAP bit 是什麼，以及它停用 ME 的限制（BUP 仍跑，BootROM 仍執行）

---

## 延伸閱讀

1. **"Intel ME: The Way of the Static Analysis"** — Mark Ermolov & Maxim Goryachy (Black Hat Europe 2017)  
   讀哪裡：完整 slide deck（https://www.blackhat.com/docs/eu-17/materials/eu-17-Ermolov-Intel-ME-The-Way-Of-The-Static-Analysis.pdf）  
   學什麼：ME 韌體結構的逆向方法，Huffman 解壓縮，BUP/KERNEL 模組拆解；是公開 ME 逆向最完整的教材  
   關聯：本章「ME 韌體結構」一節的深化，配合 MEAnalyzer 使用

2. **CVE-2017-5689 原始技術報告** — Embedi (Maksim Malyutin)  
   讀哪裡：https://embedi.org/blog/bypassing-intel-amt-authentication/ 或 Intel 的 SA-00075 公告  
   學什麼：漏洞的精確技術細節，認證繞過的 HTTP request 格式，影響版本列表  
   關聯：本章「CVE-2017-5689」一節的一手來源

3. **"How the NSA Compromises Intel ME" (HAP bit research)** — Ermolov & Goryachy (CCC 2019 / 公開部落格)  
   讀哪裡：https://www.blackhat.com/docs/eu-17/materials/eu-17-Ermolov-Intel-ME-The-Way-Of-The-Static-Analysis.pdf 的後續更新，以及相關部落格  
   學什麼：HAP bit 的發現過程，Flash Descriptor 如何控制 ME 模式，me_cleaner 的工作原理  
   關聯：本章「ME 停用：HAP bit」一節

4. **Intel BootGuard Overview** — EDK2 Wiki / Intel Platform Security  
   讀哪裡：https://github.com/tianocore/tianocore.github.io/wiki/BootGuard 與 Intel Firmware Support Package (Intel FSP) 文件  
   學什麼：KM、BPM 結構定義，ACM 的執行模型，不同 Profile 的硬體行為  
   關聯：本章「BootGuard 架構」的技術細節深化

5. **AMD Secure Processor (PSP) Architecture** — AMD 白皮書 + "Zenbleed and Friends" 系列研究  
   讀哪裡：AMD Security White Papers（https://www.amd.com/en/technologies/pro-security）；Tavis Ormandy 的 Zenbleed 分析  
   學什麼：PSP 與 AMD CPU 的整合方式，SEV-SNP 的完整性保護設計，fTPM 的時間側信道問題  
   關聯：本章「AMD PSP」一節，以及 Ch 40 TPM 攻擊

6. **"PKfail: Untrusted Platform Keys on Enterprise Devices"** — Binarly 2024  
   讀哪裡：https://www.binarly.io/blog/pkfail  
   學什麼：OEM 私鑰洩漏的真實案例，供應鏈金鑰管理失敗如何讓 Secure Boot / BootGuard 形同虛設  
   關聯：本章「BootGuard 繞過思路：拿到 OEM 私鑰」，以及 Ch 30 真實利用鏈

---

→ [練習 B：CHIPSEC 稽核平台保護組態](./practice-b-chipsec-audit.md)
