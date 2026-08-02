# Ch 1 — 為什麼攻韌體：Ring -2/-3 的世界

> **目標**：從威脅模型的角度說清楚「為什麼韌體植入對 APT 那麼有吸引力」，並用真實 bootkit 的歷史時間軸理解攻擊技術的演進邏輯。本章純概念，不需要動手環境。

---

## Ring 模型往下挖

大多數的攻擊者在 Ring 0 停下來。你打爆 kernel，你就是系統的王。但 x86 架構在 Ring 0 之下還有兩層，而且它們比 kernel 更早啟動、更難被觀測：

```
  ┌─────────────────────────────────────────────┐
  │ Ring 3 — User space                         │
  │  瀏覽器、Office、Shell                       │
  ├─────────────────────────────────────────────┤
  │ Ring 0 — Kernel                             │
  │  Linux kernel / Windows NT kernel           │
  │  大多數 rootkit 活在這裡                    │
  ├─────────────────────────────────────────────┤
  │ Ring -1 — Hypervisor (VMX root)             │
  │  KVM、Hyper-V、VMware ESXi                  │
  │  虛擬機逃逸的目標層（vm_escape 課）         │
  ├─────────────────────────────────────────────┤
  │ Ring -2 — SMM（System Management Mode）     │
  │  CPU 進入 SMI handler 時切到此模式           │
  │  SMRAM 不可被 OS/hypervisor 直接讀寫         │
  │  Ring 0 的程式碼在 SMM 執行時被凍結         │
  ├─────────────────────────────────────────────┤
  │ Ring -3 — ME / PSP / BMC（Out-of-band）     │
  │  Intel Management Engine（x86）             │
  │  AMD Platform Security Processor（AMD）     │
  │  完全獨立的嵌入式核心，OS 關機後仍可運行    │
  └─────────────────────────────────────────────┘
```

這張圖裡有幾件事值得仔細看：

**SMM（Ring -2）** 是 CPU 的「緊急維護模式」。發出 SMI（System Management Interrupt）後，CPU 儲存所有暫存器狀態、跳進 SMRAM 執行 SMI handler，執行完再恢復。SMRAM 的位址範圍受到晶片組的硬體鎖保護（SMRR/D_LCK），正常情況下 OS 或 hypervisor 都無法讀寫。如果你能在 SMRAM 放入惡意程式碼，你的 payload 會在比 kernel 更高的特權下、且對 OS 完全不可見的狀態裡執行。

**ME / PSP（Ring -3）** 是主機板上獨立的嵌入式核心，有自己的 CPU（Intel ME 是 ARC/SPARC，PSP 是 ARM Cortex-M）、RAM、Flash。它在主機板通電後幾乎立刻啟動，甚至在 BIOS POST 之前。即使 OS 關機，只要 ATX 電源的 5V standby 還在，ME 就還在跑。這是「Ring -3」這個非正式名稱的由來——它比 SMM 更早、更獨立、更難觸碰。

---

## bootkit 與 rootkit 的本質差異

這兩個詞常被混用，但它們的生存層次和持久性機制截然不同：

| 維度 | Rootkit | Bootkit |
|---|---|---|
| 典型生存層 | Ring 0（kernel 模組/驅動） | Ring -2 或韌體層（UEFI、MBR/VBR） |
| 植入時機 | OS 運行後 | OS 啟動之前，甚至韌體階段 |
| 持久性 | 磁碟檔案（module、driver） | NVRAM / SPI Flash / ESS（ESP） |
| 重灌 OS 後能否存活 | 不能（格式化磁碟就死） | **可以**（植在 UEFI 模組裡，磁碟無關） |
| AV/EDR 能否偵測 | 有機會（kernel level hook） | 非常困難（在 OS 之前執行） |
| 代表案例 | Azazel、Necurs | LoJax、MoonBounce、BlackLotus |

bootkit 的核心優勢只有一個：**它活在 OS 啟動之前**。這意味著：

- AV/EDR 的 early-launch driver 還沒跑，惡意程式碼已經執行過了。
- OS 的記憶體完整性機制（Windows HVCI、Linux IMA）在 bootkit 已執行後才初始化。
- 格式化、重灌 OS 對植入韌體 Flash 的 bootkit 沒有任何影響。

---

## 真實案例時間軸

韌體攻擊不是理論，是有真實武器化案例的領域。以下時間軸展示技術演進的軌跡：

### 2007 — IceLord BIOS Rootkit

最早被公開演示的 BIOS rootkit 之一，中國安全研究員 IceLord（龍飛）在 Phrack 類論壇發表。目標是 AWARD BIOS（Legacy BIOS，非 UEFI）。技術：修改 BIOS ROM 映像、插入 ISA Option ROM 程式碼、重新燒錄 SPI Flash。影響：理論上在 OS 之前執行，但被後來的 UEFI 安全機制（Secure Boot）封殺的主要對象就是這類攻擊。意義：證明「軟體手段修改韌體」這條路可行，開啟了後續工具化的道路。

### 2015 — Hacking Team UEFI rootkit（CVE 世代）

義大利監控軟體公司 Hacking Team 在 2015 年被駭，洩漏的 400 GB 資料中包含一個 UEFI rootkit 的完整原始碼。

這是第一個有完整原始碼洩漏的 UEFI rootkit，研究者因此能詳細分析它如何：
- 在 DXE 階段掛鉤 `gBS->ExitBootServices()`（Boot Services 指標竄改）
- 在 OS loader 移交控制前植入 dropper
- 使用 UEFI variable 保存設定，跨開機持久

這個洩漏事件讓整個安全社群第一次有機會直接研究 UEFI 惡意程式碼，加速了後續攻防工具（CHIPSEC、UEFITool）的發展。

### 2018 — LoJax（APT28 / Sednit）

LoJax 是第一個被確認**在野（in-the-wild）使用的 UEFI rootkit**，由 ESET 研究人員發現並分析，歸因於俄羅斯情報組織 APT28（Sednit/Fancy Bear）。

攻擊鏈：
1. 使用 RWEverything（合法的驅動）讀取目標機器的 SPI Flash 內容
2. 修改讀回的韌體映像，插入惡意 DXE driver
3. 用相同驅動重新寫入 SPI Flash（繞過 BIOS_CNTL 的寫保護——目標機器的 BIOS_CNTL.BIOSWE 沒有鎖死）
4. 惡意 DXE driver 在每次開機時於 DXE 階段執行，把 dropper 寫入磁碟的 Windows 目錄
5. 即使重灌 Windows 也無效，因為 dropper 從韌體重新投放

技術指標：LoJax 的 DXE driver 體積非常小（< 10 KB），功能單一（就是投放 dropper），設計目標是持久化而非功能豐富。

LoJax 的出現讓政府和企業安全團隊意識到：**UEFI rootkit 不再只是 PoC，是現實的 APT 武器**。

### 2020 — MosaicRegressor

卡巴斯基(Kaspersky)在 2020 年發表對 MosaicRegressor 的分析。這是被歸因於一個中文語系 APT 組織的 UEFI rootkit 框架，目標包括外交機構和非政府組織（主要在亞洲和非洲）。

MosaicRegressor 的技術特點：
- 比 LoJax 更完整的框架，有多個 DXE 模組協同工作
- 使用 UEFI variable 在模組間傳遞設定和 payload
- 植入機制同樣依賴對 SPI Flash 的直接寫入

### 2022 — MoonBounce（Winnti / APT41）

卡巴斯基在 2022 年初發表 MoonBounce 分析，這是技術上最精密的 UEFI bootkit 之一，歸因於 Winnti（APT41）。

MoonBounce 的技術突破：**植入點是 CORE_DXE**，而不是獨立的 DXE driver。

CORE_DXE 是 EDK II 架構中最核心的 DXE 元件之一，負責初始化整個 DXE 基礎設施（包含 Boot Services、Runtime Services 表、所有 protocol）。把惡意程式碼嵌入 CORE_DXE 的意義：

- 惡意程式碼在所有其他 DXE driver 之前執行
- 更難被基於 driver GUID 的偵測工具發現（因為沒有新增 GUID，而是改了既有模組）
- 更難被 UEFITool 的 diff 發現（需要對 CORE_DXE 做 binary diff）

MoonBounce 的發現推動了韌體完整性監控工具的進一步發展，Intel Boot Guard 的「完整性保護」場景也因此更多被討論。

### 2021–2022 — ESPecter 與 CosmicStrand

- **ESPecter**（ESET，2021）：植入在 EFI 系統分割區(EFI System Partition, ESP) 的 Windows Boot Manager（bootmgfw.efi）中，而非 UEFI 韌體本身。技術門檻比修改 SPI Flash 低，但同樣能在 OS 之前執行惡意程式碼，且重灌 OS 後 ESP 上的惡意 bootmgfw.efi 仍存在。
- **CosmicStrand**（卡巴斯基，2022）：植入 UEFI DXE 階段，歸因於中文語系 APT，主要目標在中國、越南、伊朗。技術上接近 MoonBounce，但利用的是韌體中的 hook pointer 竄改而非直接插入新 DXE module。

### 2023 — BlackLotus（CVE-2022-21894）

BlackLotus 是截至 2023 年已知**第一個公開且可購買的（crimeware）能繞過 Windows Secure Boot 的 UEFI bootkit**，由 ESET 發表分析，實際銷售記錄在地下論壇可追溯到 2022 年底。

BlackLotus 的技術核心：利用 CVE-2022-21894（Baton Drop），這是 Windows Boot Manager 的一個邏輯漏洞，讓攻擊者能使用已被 dbx 撤銷的舊版 bootmgr.efi，繞過 UEFI Secure Boot 的撤銷機制（dbx 是記錄「不信任的已知 hash」的黑名單，這個漏洞讓黑名單失效）。

BlackLotus 完整利用鏈：
```
攻擊者投放 installer
       │
       ▼
安裝舊版（易受攻擊的）bootmgfw.efi 至 ESP
       │  （利用 Baton Drop 繞過 dbx 撤銷檢查）
       ▼
替換 bootloader，載入惡意 kernel driver（關閉 HVCI、BitLocker）
       │
       ▼
植入到 ESP 的 /EFI/Microsoft/Boot/ 目錄，重開機後持久
```

BlackLotus 的出現代表：UEFI bootkit 已從 APT 專屬工具演進為**商品化犯罪工具（crimeware）**，門檻大幅降低。

---

## SPI Flash 寫入路徑：攻擊者怎麼把東西植進韌體？

理解攻擊手法前，先看韌體儲存在哪裡以及如何被讀寫。

x86 主機板上的 BIOS/UEFI 韌體存在 SPI NOR Flash 晶片裡（通常是 Winbond 或 Macronix 的 16–32 MB 晶片），透過 SPI（Serial Peripheral Interface）匯流排連接到晶片組（PCH）。

```
CPU ── DMI ── PCH ── SPI bus ── SPI Flash (NOR, 16–32 MB)
                                  │
                              BIOS ROM image
                              (SEC+PEI+DXE+BDS 全在這)
```

正常情況下，OS 無法直接存取 SPI Flash——晶片組的 SPI controller 受到 BIOS 的保護設定鎖住：

- **BIOS_CNTL.BIOSWE（BIOS Write Enable）**：0 = 寫入鎖定
- **BIOS_CNTL.BLE（BIOS Lock Enable）**：1 = 防止軟體把 BIOSWE 改回 1
- **PR0–PR4（Protected Range registers）**：對 Flash 指定位址範圍的細粒度讀/寫保護

攻擊者的植入路徑有三條，難度遞減：

**路徑一：SPI programmer 硬體存取**（物理實體）
- 用 CH341A 等 SPI programmer 直接夾住 Flash 晶片的腳位（或拆下晶片）
- 讀出映像、修改、重新寫回，完全繞過任何軟體保護
- 需要實體存取目標機器，是 nation-state 供應鏈污染的手段
- **本段未實測，為理論預期行為**（需要 CH341A 硬體與夾具）

**路徑二：利用未鎖定的 BIOS 寫保護**（LoJax 的方法）
- 用帶有 kernel 驅動的工具（RWEverything、rwmem、Intel MEI）直接讀寫 PCH 的 SPI controller MMIO 暫存器
- 如果廠商沒有設定 BIOS_CNTL.BLE = 1，攻擊者可以把 BIOSWE 改成 1，然後寫入 Flash
- CHIPSEC 的 `chipsec_main -m common.bios_wp` 會掃描這個設定是否正確鎖定
- 這是所有「軟體植入韌體」攻擊的核心前提

**路徑三：合法的韌體更新流程（capsule update）**（供應鏈污染）
- 韌體廠商提供的 capsule update 工具（fwupdmgr、Windows 的 WinFlash）有權限寫入 SPI Flash
- 如果 capsule 簽章驗證有漏洞（或攻擊者持有廠商金鑰——如 2024 PKfail），可投放惡意 capsule
- 這也是 OS-to-firmware 的合法信任邊界，Ch 6（capsule update）和 Ch 9（runtime 信任邊界）詳細討論

---

## 為什麼韌體植入是 APT 的首選？

站在攻擊者的角度梳理動機：

**持久性（Persistence）**：這是最核心的吸引力。APT 的目標通常是長期潛伏（months to years），而 OS 層的 rootkit 有太多消除途徑（EDR 掃描、重灌 OS、磁碟取證）。韌體植入在 SPI Flash 裡，格式化磁碟、重灌 OS、更換硬碟都不管用，只有更換主機板或重新燒錄乾淨的韌體才能清除。

**隱蔽性（Stealth）**：OS 運行時，UEFI 相關的記憶體區段（SMRAM、韌體運行時服務）對一般 OS 工具不可見。EDR 的 sensor 在 kernel 層，看不到在更早階段執行的程式碼。現有的韌體掃描工具（Windows Defender 的 firmware scanner、CHIPSEC）覆蓋率有限，且部署率遠低於 AV/EDR。

**供應鏈潛力（Supply Chain）**：韌體更新通道（capsule update）如果被污染，可以在出廠前或韌體更新時植入，受害者收到的機器或更新包裡就已包含惡意程式碼，連「攻進目標機器再植入」的步驟都省了。2024 年的 PKfail 漏洞（Binarly 發現）就是廠商用測試金鑰簽了出廠韌體，讓攻擊者能偽造合法更新。

**規避現有防禦（Defense Evasion）**：Secure Boot 設計用來防止未簽名的 OS loader 執行，但它本身的信任根（db/dbx/KEK/PK）的管理就是攻擊面（Part 5 整章在談這個）。HVCI、VBS 保護的是 kernel 層，對 UEFI 層的攻擊沒有直接防禦效果。

---

## 威脅模型：誰在攻，誰會被攻？

```
攻擊者分類       典型能力                         代表案例
──────────────────────────────────────────────────────────────
Nation-state    實體存取 + SPI programmer,        LoJax (APT28)
APT             zero-day SMM exploit,             MoonBounce (APT41)
                韌體供應鏈污染                    CosmicStrand

高端 crimeware  dbx bypass（已知 CVE）,            BlackLotus
                購買 signed bootloader

安全研究者      QEMU/OVMF 環境,                   本課所有實驗
（包含你）      公開 CVE PoC,
                逆向 EDK II 原始碼
```

被攻目標的特徵：

- 高價值目標（政府機構、國防承包商、人權組織）
- 使用老舊韌體、沒有 Secure Boot、或廠商已停止更新的設備
- 有物理存取風險的高層人員設備（會議、邊境）

一般消費者在現階段被 UEFI bootkit 打的機率極低，但「研究這個領域」的你需要理解整個威脅模型，因為這決定了你在 Ch 42–45（防守）要在哪裡建立偵測點。

---

## 防禦演進：攻守軍備競賽

了解攻擊史之後，也要知道防守方這些年做了什麼，因為後面 Part 5/8 的防守章節都建立在這個演進脈絡上：

**2011–2013 — Secure Boot 導入（UEFI 2.3.1）**：
- 驗 OS loader 簽章才能執行，阻擋未簽名的 bootloader
- 主要針對的威脅：MBR/VBR rootkit（Mebroot、TDL 系列）
- 不足：驗的是 OS 層，韌體本身沒有被驗證（沒有 BootGuard 時 SEC/PEI 沒有信任鏈）

**2013–2015 — Intel Boot Guard 推出**：
- 把信任根從軟體推到硬體 fuse，ACM 在 BIOS POST 之前驗 IBB
- 第一次在 x86 上真正實現「硬體 root of trust」
- 不足：OEM 出廠設定決定是否啟用；許多廠商未燒 fuse（或燒了 Measured 模式而非 Verified 模式）

**2015 — CHIPSEC 公開發布**：
- Intel ATR 把平台安全稽核工具開源，任何人都能掃描 SMM 保護、BIOS 寫保護是否正確設定
- 對攻擊者：提供了 PoC 框架；對防守者：提供了自動化稽核手段

**2020–2022 — dbx 更新與 SBAT 機制**：
- BootHole（CVE-2020-10713）之後，業界意識到 dbx 黑名單更新的基礎設施嚴重不足
- SBAT（Secure Boot Advanced Targeting）是 Linux 生態的新撤銷機制，比 dbx 更精細（按元件版本，不按 binary hash），從 GRUB 2.06 開始採用

**2023 — BlackLotus 之後的反應**：
- UEFI Security Response Team（UEFI-SRT）跨廠商加速 dbx 更新
- Microsoft 釋出 CVE-2022-21894 修補，並宣布更積極的 SBAT 更新策略
- 但要真正把所有存在漏洞的舊版 bootmgr.efi 加入 dbx 而不破壞合法的雙開機設定，仍然是個未解的工程挑戰

**2024 — PKfail（Binarly）**：
- Binarly 發現多家 OEM 廠商的量產韌體使用了測試金鑰（AMI Aptio 的範例 PK），這個私鑰甚至在 2023 年就已在網路上洩漏
- 影響：攻擊者可用洩漏的私鑰簽任意 bootloader 並繞過 Secure Boot
- 教訓：信任根的金鑰管理是供應鏈安全的核心問題，不是技術問題，而是流程問題

---

## 踩雷集錦

**錯誤直覺：「只要啟用 Secure Boot 就安全了」**
→ 正確認識：Secure Boot 驗的是 OS loader 的簽章，但前提是 db/dbx/KEK/PK 這些信任根本身沒有被污染、撤銷清單（dbx）有及時更新、韌體實作本身沒有漏洞。BlackLotus 利用的 Baton Drop（CVE-2022-21894）就是在 Secure Boot 開啟的狀態下繞過的。信任 Secure Boot 的前提是信任整個 Secure Boot 的實作鏈，而這條鏈有很多環。

**錯誤直覺：「格式化磁碟後重灌 OS 就清除了 bootkit」**
→ 正確認識：植入在 SPI Flash（韌體本身）裡的 bootkit（如 LoJax、MoonBounce）與磁碟無關。格式化只清了磁碟；SPI Flash 是主機板上獨立的 NOR Flash 晶片，需要用 SPI programmer 硬體或韌體更新流程才能重新刷寫。植入在 ESP 的 bootkit（如 ESPecter、BlackLotus）確實會被磁碟格式化清除，但 UEFI 韌體本身植入的不行。

**錯誤直覺：「SMM 是核心保護，攻擊者不可能碰到它」**
→ 正確認識：SMM 的安全完全依賴晶片組設定（SMRR、D_LCK、BIOS_CNTL）是否被正確鎖定，以及 SMI handler 的程式碼本身是否有漏洞。CHIPSEC 的測試會定期發現廠商沒有正確設定這些保護的韌體。SMI handler 的程式碼缺陷（callout、指標竄改）在 Part 2 會詳細分析。

---

## 進階延伸

本章所有案例在以下資源有更詳細的一手分析：

- **LoJax 分析**：ESET 的一手報告 "LoJax: First UEFI rootkit found in the wild"，附有完整的 binary 分析和 YARA 規則。
- **MoonBounce**：卡巴斯基 "MoonBounce: the dark side of UEFI firmware" 報告，詳細說明 CORE_DXE 植入技術，是 Part 4 逆向章節的必讀背景。
- **BlackLotus**：ESET "BlackLotus UEFI bootkit: Myth confirmed" 及後續 "BlackLotus UEFI bootkit: Full technical details"，包含 CVE-2022-21894 的詳細利用機制，Ch 30 逐步拆它的利用鏈。

---

## 本章重點

- Ring -2（SMM）和 Ring -3（ME/PSP）的程式碼在 OS 之前執行，且對 OS 不可見——這是韌體攻擊的核心價值主張。
- bootkit 與 rootkit 的關鍵差異是**生存層次**與**持久性機制**，重灌 OS 能殺 rootkit，但殺不了植在 SPI Flash 的 bootkit。
- 真實 UEFI bootkit 的演進路徑：Legacy BIOS rootkit（2007）→ UEFI DXE rootkit（2015 洩漏）→ 野外部署 APT 武器（2018 LoJax）→ 精密核心模組植入（2022 MoonBounce）→ 商品化 crimeware（2023 BlackLotus）。
- APT 選擇韌體植入的三個主要動機：持久性（重灌不死）、隱蔽性（OS 工具看不到）、供應鏈污染潛力。
- Secure Boot 不是萬靈丹——它的安全性依賴信任根完整、撤銷清單更新、以及實作本身無漏洞，三個條件缺一都可能被繞過。

## 自我檢核

- [ ] 我能在不看圖的情況下，說出 Ring 3/0/-1/-2/-3 各是什麼層次、誰住在那裡
- [ ] 我能解釋 bootkit 與 rootkit 的差異，並舉出各自一個實例
- [ ] 我能說出 LoJax 的攻擊鏈（從取得 Flash 內容到持久化）的每一步
- [ ] 我能解釋為什麼 MoonBounce 選擇植入 CORE_DXE 而非加一個新 DXE driver
- [ ] 我能說出 BlackLotus 利用的 CVE 編號與繞過的機制是什麼

## 延伸閱讀

1. **《Rootkits and Bootkits》— Matrosov, Rodionov, Bratus（No Starch, 2019）**
   - 讀第 10–14 章（UEFI bootkit 部分）；這本書對每個歷史案例的技術細節描述是目前最完整的，且直接分析二進位，不是泛泛介紹。本課 Part 5 大量借鑑此書的分析框架。

2. **ESET Research 部落格：[welivesecurity.com](https://www.welivesecurity.com)**
   - 搜尋 "UEFI bootkit"，看 LoJax / ESPecter / BlackLotus 的一手報告；它們是本章時間軸的一手資料來源，且包含 IoC（入侵指標）和 YARA 規則，是看「真實攻擊長什麼樣」最直接的窗口。

3. **Kaspersky Securelist：[securelist.com](https://securelist.com)**
   - 搜尋 "MoonBounce" 和 "CosmicStrand"；卡巴斯基的韌體研究報告在技術深度上與 ESET 並列頂尖，MoonBounce 的 CORE_DXE 分析是理解「如何最小化修改韌體同時最大化持久性」的最佳案例。

---

→ [Ch 2 信任鏈解剖：全課地圖](./02-chain-of-trust-anatomy.md)
