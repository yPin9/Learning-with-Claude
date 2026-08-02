# Ch 29 — 繞過類型學（x86 Secure Boot）

> **目標**：把 Ch21 建立的嵌入式 T1-T6 類型學系統性地展開到 x86 UEFI Secure Boot，建立一張有真實 CVE 對應的 x86 繞過類型總表。六個大類型逐一剖析機制、根本原因、代表案例，並說清楚每個類型的「繞過後站位」——攻擊者在哪個信任層取得控制。本章是 Ch30（具體利用鏈）的知識地圖，讀完後每個 CVE 都能快速歸類並預測利用路徑。

---

## 從嵌入式到 x86：類型學的移植

Ch21 的六個嵌入式繞過類型（T1 Fuse 未燒、T2 Debug Port、T3 驗簽邏輯錯誤、T4 Rollback 未防、T5 替代路徑、T6 公開金鑰）在 x86 UEFI 上有直接的類比，但具體表現不同。

x86 UEFI 的信任鏈更複雜：有 firmware 本身、有 Secure Boot 的金鑰階層（PK→KEK→db/dbx）、有 GRUB/shim/kernel 的 bootloader 鏈、有 SBAT 撤銷機制。對應的繞過類型也更細。

本章把 x86 Secure Boot 的繞過分成六大類型，每類配一個核心機制說明和真實 CVE 標記，**並給一個攻擊者的「站位」**——繞過之後，攻擊者控制的是什麼：

```
信任鏈站位（從高到低）：
  [firmware]    ← 控制這裡可以在 OS 重灌後持久化，SMM/SMRAM 完全控制
  [bootloader]  ← 控制 GRUB/shim，可以載入任意 kernel
  [kernel]      ← bypass Secure Boot 但僅到 kernel 層
  [OS]          ← 打到 ring 0 後才能做的 pre-boot 操作
```

---

## T1：Signed-but-Vulnerable（已簽章但有漏洞的 bootloader）

**這是 x86 Secure Boot 繞過的主流類型。**

### 機制

Secure Boot 驗的是 **簽章是否有效**，不驗 **binary 的程式碼是否安全**。只要一個有漏洞的版本被 Microsoft CA 簽過、放進 db，攻擊者就能：

1. 找到那個舊版有漏洞的 bootloader（GRUB、shim、Windows bootloader）
2. 利用漏洞在 firmware 驗簽之後、OS 啟動之前執行任意程式碼
3. Secure Boot 「通過了」——因為那個有漏洞的版本確實有合法簽章

```
Secure Boot 驗簽（db 命中，通過）
         │
         ▼
  載入有漏洞的 GRUB 2.04
         │
         │ ← 攻擊者觸發 CVE-2020-10713（BootHole）
         ▼
  GRUB config file 解析 overflow
  → 在 firmware Secure Boot 仍報「開啟」的情況下執行任意程式碼
         │
         ▼
  攻擊者站位：bootloader 層（可載任意 kernel、安裝 bootkit）
```

### 根本原因

**驗章 ≠ 驗安全**。簽章只保證「這個 binary 是被授權的人發布的」，不保證「這個 binary 沒有 buffer overflow」。只要 CA（Microsoft UEFI CA）的撤銷機制（dbx/SBAT）沒有把有漏洞的版本加黑名單，它就永遠能被 Secure Boot 接受。

### 代表案例

| CVE | 漏洞位置 | 漏洞類型 | 攻擊者站位 | 備注 |
|-----|---------|---------|-----------|------|
| CVE-2020-10713（BootHole） | GRUB 2.06 前所有版本 | grub.cfg 解析 buffer overflow | bootloader | 觸發在 grub.cfg 載入時，在 Secure Boot 驗完簽章後 |
| CVE-2021-3418 | GRUB 的 shim_lock verifier | 驗簽繞過（合法 binary 繞 shim 鎖）| bootloader | BootHole 修補過程引入的新 bug |
| CVE-2023-40547（BootHole 二代） | shim 的 HTTP boot 程式碼 | buffer overflow | bootloader | shim 18 以前，PE header 解析 |
| CVE-2022-28737 | shim 的 handle_image() | buffer overflow | bootloader | efi image 解析，可觸發在 Secure Boot 下 |
| CVE-2021-20225/20233 | GRUB menu 解析 | heap overflow | bootloader | 輸入超長字串 |
| CVE-2023-1049 | GRUB 的 command injection | 命令注入 | kernel | 需要 grub.cfg 可寫 |

### 攻擊路徑

```
需要條件：
  ① 目標系統的 db 裡有 "Microsoft Corporation UEFI CA 2011"（99% 的 x86 系統）
  ② 能提供一個被 Microsoft CA 簽章的有漏洞 GRUB/shim binary
     （shim 和 GRUB 的歷史版本都能從 distro 的歷史 package 取得）
  ③ 能讓那個有漏洞的 binary 被執行（需要本地 root 修改 BootOrder 或 grub.cfg）

攻擊步驟：
  1. 取得 OS ring 0（kernel exploit 或其他手段）
  2. 把有漏洞的舊版 shim/grub 放到 ESP 的 \EFI\BOOT\
  3. 修改 BootOrder（NV+BS+RT，無 AT，OS ring 0 可改）指向它
  4. 建立惡意 grub.cfg（Secure Boot 開啟時 GRUB 從 $prefix/grub.cfg 讀，
     但 grub.cfg 本身不在 Secure Boot 驗章範圍內——這本身也是個設計問題）
  5. 重開機 → Secure Boot 驗通有漏洞的 GRUB → GRUB 讀 grub.cfg → overflow → bootkit
```

---

## T2：db 驗證邏輯 Bug

### 機制

UEFI firmware 本身的 Secure Boot 驗簽實作有 bug，讓攻擊者可以送一個「格式正確但語意異常」的輸入，繞過驗章邏輯。這類 bug 存在於 edk2、AMI、Insyde、Phoenix 等韌體的驗簽程式碼。

```
攻擊者的惡意 EFI binary：
  ├── PE header（正常格式）
  └── 嵌入的 WIN_CERTIFICATE（CMS SignedData）
      └── 構造特殊的格式觸發 parser bug
             │
             ▼
      韌體的 DxeImageVerificationLib.c 解析異常
      → 走到「驗通」分支，但沒有真的驗
      → 或走到「例外處理」分支，exception 時預設允許
```

### 根本原因

`DxeImageVerificationLib` 和其呼叫的 `BaseCryptLib`（OpenSSL 的封裝）處理邊界情況時的防禦性不足。常見問題：

- **Null cert list**：CMS SignedData 的 certificates 欄位為空時，驗簽函式回傳「沒有找到能驗的 cert，跳過」而非「沒有可信 cert，拒絕」
- **ASN.1 long-form length**：DER 長度編碼的 long-form（多 byte）解析錯誤，導致 parser 讀到錯誤的資料起點
- **SignedData contentInfo 格式**：signed data 的 eContentType 或 encapContentInfo 解析異常

### 代表案例

| CVE | 韌體 / 實作 | 漏洞類型 | 攻擊者站位 |
|-----|-----------|---------|-----------|
| CVE-2019-14562 | edk2 BaseCryptLib（DXEImageVerification） | Integer overflow in DER parsing | firmware（EFI 執行層）|
| CVE-2021-28210 | edk2 NetworkPkg（PXE）| Buffer overflow → 繞 verification | firmware |
| CVE-2022-4020 | Insyde SecureBoot implementation | UEFI Secure Boot bypass via malformed PE header | firmware |
| LogoFAIL（2023，Binarly） | AMI/Insyde/Phoenix 的 BMP/JPEG/GIF logo 解析 | Image parser overflow，在 DXE 階段執行 | firmware（Ring -2 準備期）|
| PixieFail（CVE-2023-45229 等） | edk2 NetworkPkg（PXE/DHCPv6）| 多個 parser overflow | firmware（boot 前）|

### LogoFAIL 的特殊性

LogoFAIL 嚴格說是 T2 的變體：漏洞不在 PE/COFF 驗簽邏輯，而在 **logo image 解析器**（BMP/JPEG/PNG/GIF），在 DXE 階段被呼叫（顯示廠商 logo 時），而這個階段的記憶體在 Secure Boot 驗完 EFI 之前就被執行。攻擊者把惡意的 logo image 放進 ESP 的特定位置，或竄改 NVRAM 裡的 logo variable。

```
DXE 階段：
  [UEFI logo 解析器] ← 攻擊者放惡意 BMP/JPEG 到 ESP
         │
         ▼（overflow → code exec）
  執行任意程式碼（此時在 DXE，Secure Boot 驗簽根本還沒跑到）
         │
         ▼
  攻擊者站位：firmware 層（比 Secure Boot 更早）
```

---

## T3：NVRAM / Setup Mode 攻擊

### 機制

Ch 5 詳述了 NVRAM 攻擊，這裡聚焦 Secure Boot 脈絡：

**路徑 A：Setup Mode 誘導**

如果攻擊者能讓系統回到 Setup Mode（PK 被清除），就能部署自己的 PK/KEK/db，讓自己的 EFI binary 被 Secure Boot 信任。

```
條件：能訪問 BIOS UI（物理訪問）
  或：找到可以觸發「刪除 PK」的韌體 bug
  或：有 IPMI/BMC 管理介面
         │
         ▼
系統回到 Setup Mode（SetupMode=1）
         │
         ▼
部署自己的 PK → KEK → db（在 Setup Mode 下不需簽章）
         │
         ▼
攻擊者的 db 裡放自己的 cert，接受自己簽章的任何 EFI binary
         │
         ▼
攻擊者站位：firmware / Secure Boot 信任根
```

**路徑 B：非 AT variable 竄改（BootOrder 劫持）**

這個在 Ch 5 講過，但這裡強調：BootOrder 被竄改讓有漏洞的 GRUB 先執行，組合成 T1+T3 的利用鏈。

**路徑 C：NVRAM variable 內容注入**

某些廠商韌體的 DXE driver 在開機時從 NVRAM 讀設定，沒有對讀出的資料做充分驗證。攻擊者把惡意資料寫進那些 variable（沒有 AT 保護），觸發 DXE driver 的 parser bug。

### 代表案例

| CVE / 事件 | 攻擊路徑 | 攻擊者站位 |
|-----------|---------|-----------|
| CVE-2022-3430 等（Lenovo）| 廠商 DXE driver 讀 NVRAM variable 時 buffer overflow | firmware |
| CVE-2021-41840（InsydeH2O）| SMM driver 從 NVRAM 讀 config 時不安全 | Ring -2（SMM）|
| PKfail（2024，Binarly）| 洩露的 PK 私鑰允許任意更新 PK/KEK/db | Secure Boot 信任根完全失效 |
| CVE-2016-3287（Broadcom）| NVRAM variable 注入導致 SMI handler 受控 | Ring -2 |

### PKfail 的系統性問題

PKfail 嚴格說是 T6（公開金鑰）和 T3 的交叉：因為 PK 私鑰洩露（T6），攻擊者可以用它簽任意的 `PK.auth` 或 `KEK.auth`，透過 `SetVariable("PK", ...)` 直接換掉 PK（T3 路徑）。這讓 NVRAM / Secure Boot 信任根完全失效。

---

## T4：Rollback / 降級攻擊

### 機制

UEFI Secure Boot 沒有像 AVB 那樣的原生 rollback protection。dbx 是「事後補救」機制：一個 binary 有漏洞了，才加進 dbx。但**在 dbx 更新前，舊版有漏洞的 binary 仍然被 db 信任**。

x86 的降級攻擊有兩個維度：

**維度一：bootloader 版本降級**

```
dbx 尚未更新（或攻擊者讓 dbx 更新失敗）：
  old_grub_2.04（有 BootHole CVE）在 db 白名單
         │
         ▼
  攻擊者把 old_grub_2.04 放到 ESP，修改 BootOrder
         │
         ▼
  Secure Boot 驗章通過（它確實有合法簽章）
  但舊版 GRUB 有 overflow，被利用
```

**維度二：韌體版本降級（UEFI Capsule Rollback）**

某些平台的 capsule update 機制沒有 rollback 保護，或 rollback 保護儲存在可被竄改的位置。攻擊者讓系統裝回舊版韌體（舊版韌體有 SMM 漏洞或更少的安全加固）。

```
條件：capsule update 機制接受比當前版本舊的 capsule
  → 刷回舊版 UEFI → 舊版有 SMM bug → 利用 SMM bug 取得 Ring -2
```

**維度三：BlackLotus 的 MOVIng（合法降級 + 利用）**

BlackLotus 利用的 CVE-2022-21894（baton drop）是一個精心設計的降級攻擊：微軟在 2022 年修補了一個 Windows Boot Manager 的 Secure Boot bypass，但舊版（有漏洞的）Windows bootmgfw.efi 仍然有合法的 Microsoft 簽章，且不在 dbx 裡（因為 Microsoft 沒有更新 dbx——更新 dbx 會讓大量 Windows 系統的 recovery 環境失效）。

### 代表案例

| CVE / 事件 | 降級目標 | 攻擊者站位 |
|-----------|---------|-----------|
| CVE-2022-21894（baton drop）| 舊版 Windows Boot Manager | bootloader（直接繞 Secure Boot）|
| CVE-2023-24932（BlackLotus 修補）| 修補前的 winload.efi 可繞 SB | bootloader |
| CVE-2020-10713（BootHole）+ dbx 未更新 | GRUB 2.04 | bootloader |
| Insyde FW rollback（CVE-2022-32268）| 舊版 Insyde firmware | firmware 層 |

---

## T5：Config / Parser 解析漏洞

### 機制

Secure Boot 驗 **binary**，不驗 **設定檔**。bootloader 在驗完簽章後，還會讀一堆設定檔，而這些設定檔通常不在 Secure Boot 的驗章範圍內：

```
Secure Boot 驗通 grubx64.efi
         │
         ▼
  GRUB 讀 /boot/grub/grub.cfg  ← 不驗章（只是文字檔）
  GRUB 讀 /boot/grub/fonts/    ← 不驗章
  GRUB 顯示 logo               ← 不驗章
         │
         ▼
  任何解析 grub.cfg 的 parser bug
  都在「Secure Boot 已通過」之後執行
```

類似地，UEFI 韌體讀 logo image（ESP 上的 BMP/JPEG，或 NVRAM 裡的 logo variable）時，logo parser 不在 Secure Boot 驗章流程裡。

### 子類型 5a：grub.cfg 解析

grub.cfg 的 GRUB 指令解析有多個歷史 bug（CVE-2021-20225 heap overflow in grub_malloc、CVE-2021-20233 unicode 字串處理等）。Secure Boot 開啟時，攻擊者若能控制 grub.cfg（需要 OS root 寫 /boot/grub/grub.cfg），就能在 bootloader 層執行任意程式碼。

### 子類型 5b：Logo image（LogoFAIL）

Binarly（2023）發現多個 OEM 的韌體在 DXE 階段解析 logo image 時有 overflow。更嚴重的是，這個解析在 Secure Boot 驗 EFI binary 之前，所以繞過了 Secure Boot。

```
攻擊路徑：
  修改 ESP 上的 logo BMP（OS root 可以寫 ESP）
    或竄改 NVRAM 的 logo variable（OS ring 0 可呼叫 SetVariable）
         │
         ▼
  下次開機，DXE 階段韌體讀 logo，觸發 BMP/JPEG parser overflow
         │
         ▼
  攻擊者站位：DXE（在 Secure Boot 驗簽之前執行）
```

### 子類型 5c：PXE / HTTP Boot 協定解析

edk2 的 NetworkPkg 處理 PXE/DHCPv4/DHCPv6/HTTP Boot 的協定解析有多個 bug（PixieFail：CVE-2023-45229, 45230, 45231, 45232, 45233, 45234, 45235）。這些 bug 在 PXE 開機路徑被觸發，而 PXE 開機通常在 Secure Boot 驗任何 binary 之前。

### 代表案例

| CVE | 解析目標 | 觸發時機 vs Secure Boot | 攻擊者站位 |
|-----|---------|----------------------|-----------|
| CVE-2020-10713（BootHole）| grub.cfg | SB 驗章通過後 | bootloader |
| CVE-2021-20225/20233 | grub menu 字串 | SB 驗章通過後 | bootloader |
| LogoFAIL（2023）| BMP/JPEG/PNG/GIF logo | SB 驗章之前（DXE 階段）| firmware |
| PixieFail（2023）| PXE DHCP/HTTP 封包 | SB 驗章之前（PXE 開機）| firmware |
| CVE-2023-40547 | shim HTTP boot PE header | SB 驗章中 | bootloader |

---

## T6：廠商測試 / 共用金鑰（PKfail 和變體）

### 機制

整個 Secure Boot 的強度等於 PK/KEK 私鑰的保密性。私鑰洩露或被預設共用，整個信任鏈崩潰：

**PKfail（2024，Binarly）**：
- edk2 reference design 包含一個測試用的 `PKCS12` 金鑰對（`TestCert.pk12`，私鑰公開在 edk2 GitHub repo）
- 多家 OEM（AMI、Phoenix、Insyde 的客戶）在量產韌體裡用了這把測試 PK
- 攻擊者可以下載那把私鑰，自己簽任意 `PK.auth`，用 `SetVariable("PK", ...)` 替換 PK
- 替換後可以換 KEK → 換 db → 讓自己的 EFI binary 被信任

```
edk2 GitHub 上的 TestCert.pk12（私鑰公開）
         │
         ▼
攻擊者下載私鑰
         │
         ▼
簽一個 PK.auth（把自己的 cert 設為 PK）
         │
         ▼
呼叫 SetVariable("PK", ..., PK.auth)
（用 edk2 的 TestCert 私鑰簽 → 韌體驗通，因為 firmware 的 PK 就是這把 TestCert）
         │
         ▼
現在自己掌控 PK → 掌控整個 Secure Boot 信任鏈
```

**Microsoft UEFI CA 的廣泛性問題**：
嚴格說不算「金鑰洩露」，但 Microsoft UEFI CA 的廣泛存在（幾乎所有 x86 PC 的 db 裡都有），讓它成為一個巨大的攻擊面：任何被 Microsoft UEFI CA 簽過的 EFI binary（有漏洞的 GRUB、有漏洞的 shim、有漏洞的 Windows Boot Manager）都能被 Secure Boot 信任。CA 本身沒有洩露，但它的廣泛信任等效於「任何有漏洞的 signed binary 都是 T1 的攻擊素材」。

### 代表案例

| CVE / 事件 | 洩露 / 共用類型 | 影響 | 攻擊者站位 |
|-----------|--------------|------|-----------|
| PKfail（2024）| edk2 TestCert PK 私鑰公開 | 約 10% 全球 PC（AMI/Insyde/Phoenix 客戶）| Secure Boot 信任根 |
| 多家 Android OEM signing key leak（2022-2023）| Platform signing key 洩露 | APK 簽章（類比，不是 UEFI）| 應用層信任根 |
| ASUS UEFI（2023，Binarly）| Test key 在量產固件 | 特定 ASUS 型號 | Secure Boot 信任根 |
| Qualcomm Secure Boot test key（2018）| SoC vendor test key | ARM 嵌入式（T6 嵌入式版）| bootloader 信任根 |

---

## x86 Secure Boot 繞過類型總表

| 類型 | 根本原因 | 需要的前提 | 需要 OS root？ | 攻擊者站位 | 代表 CVE / 事件 | 對應 Ch21 嵌入式類型 |
|------|---------|-----------|--------------|-----------|---------------|---------------------|
| **T1: signed-but-vulnerable** | 驗章≠驗安全，舊版有漏洞 binary 仍在 db | 能執行有漏洞的 signed binary（需修改 BootOrder 或 grub.cfg → 需 OS root） | 是 | bootloader | BootHole (CVE-2020-10713), shim CVE-2023-40547, baton drop | T3（驗簽邏輯錯誤）+ T4（rollback 未防）|
| **T2: db 驗證邏輯 bug** | UEFI firmware 的 PE 驗簽 parser bug | 能讓 UEFI 載入惡意 EFI（通常需 OS root 或物理訪問） | 通常是 | firmware | CVE-2019-14562, LogoFAIL（logo parser variant）, PixieFail | T3（驗簽邏輯錯誤）|
| **T3: NVRAM / Setup Mode** | Setup Mode 下無 PK 保護；非 AT variable 可改 | 物理訪問（Setup Mode）或 OS root（BootOrder 竄改）| 部分是 | firmware / bootloader | PKfail（T3+T6）, CVE-2022-3430, CVE-2021-41840 | T1（Fuse 未燒：等效）|
| **T4: rollback / 降級** | dbx 沒有覆蓋所有有漏洞版本；capsule 無 rollback 保護 | OS root（替換 bootloader）或物理訪問（capsule）| 是 | bootloader / firmware | CVE-2022-21894 (BlackLotus), GRUB 降級 + BootHole | T4（Rollback 未防）|
| **T5: config / parser 解析** | SB 驗 binary 不驗設定檔和資料檔 | grub.cfg：OS root；logo：OS root 或物理；PXE：網路攻擊 | 部分 | bootloader / firmware | CVE-2020-10713（grub.cfg 向量），LogoFAIL（logo 向量），PixieFail（PXE）| T5（替代路徑）|
| **T6: 廠商測試 / 共用 key** | 私鑰保密性失敗；測試 key 出廠 | 知道私鑰（公開或洩露）+ OS 呼叫 SetVariable | 是（SetVariable 需 OS 層） | Secure Boot 信任根 | PKfail (2024), ASUS test key, edk2 TestCert | T6（公開金鑰）|

---

## 各類型的「先決條件金字塔」

類型的危險程度不只看站位，也看**攻擊者需要什麼前提**：

```
前提最低（最危險）
    ▲
    │  T6（知道私鑰）→ 完全遠端可能（若有 SetVariable API 暴露到遠端）
    │  T2（db 驗簽 bug）→ 若 UEFI 處理外部輸入（PXE/USB），不需 OS root
    │  T5（PXE 解析）→ 同一子網路攻擊者可觸發，不需任何本地訪問
    │  T5（logo/grub.cfg）→ 需 OS root 寫 ESP
    │  T1（signed-but-vulnerable）→ 需 OS root 修改 BootOrder
    │  T3（BootOrder 竄改）→ 需 OS root
    │  T4（bootloader 降級）→ 需 OS root
    │  T3（Setup Mode）→ 需物理訪問 BIOS UI 或 IPMI
前提最高（最難）
```

---

## 組合攻擊：類型不是獨立的

真實利用鏈幾乎從不只用一個類型：

```
BlackLotus（2023）：
  T6（廠商的 Windows bootmgfw.efi 私鑰 = Microsoft，沒洩露，但簽過有漏洞版本）
  + T4（baton drop CVE-2022-21894：降級到有 SB bypass 的舊版 winload.efi）
  + T1（那個舊版 winload 有合法簽章，db 信任它）
  = 繞過 Secure Boot → 植入 bootkit → Ring -1 持久化

BootHole 利用鏈：
  T5（grub.cfg 向量：Secure Boot 驗 GRUB binary，不驗 grub.cfg）
  + T1（GRUB 2.04 有合法簽章，db 信任它）
  + T3（修改 BootOrder 讓有漏洞的 GRUB 先執行）
  = 在 Secure Boot 狀態「開啟」的情況下執行任意程式碼

LogoFAIL（2023）：
  T5（logo 向量：ESP 上的 BMP 不在 Secure Boot 驗章範圍）
  + T2（logo image parser overflow）
  = 完全繞過 Secure Boot（在 SB 驗簽之前執行），更底層的站位
```

---

## 對應防禦措施

| 類型 | 主要防禦 | 現狀 / 缺口 |
|------|---------|-----------|
| T1 | dbx 及時更新；SBAT 機制讓個別有漏洞版本可被撤銷而不影響整個 CA | dbx 更新依賴 Microsoft 推 Windows Update；SBAT 需要 shim/distro 配合更新 |
| T2 | edk2 程式碼審計；fuzz testing（Binarly FwHunt、OSS-Fuzz）| parser 面積太大，不斷有新洞 |
| T3 | 禁止遠端管理界面清 PK；OEM 不用 test key（PKfail 修補）| PKfail 修補需要 OEM 發韌體更新，許多裝置不會收到更新 |
| T4 | dbx 積極更新；capsule update 加 rollback 保護（設定最小版本）| 激進的 dbx 更新會破壞 recovery 環境，OEM 保守 |
| T5 | 驗章 grub.cfg（shim lock verifier）；logo 解析器沙箱化或強化 | grub.cfg 驗章實作困難（需要 GPG 或 PKCS7）；logo 沙箱複雜 |
| T6 | PKI ceremony 用 HSM；不把測試 key 帶進量產；dbx 撤銷受影響裝置的憑證 | PKfail 受影響裝置估計超過 200 種型號，修補覆蓋率低 |

---

## 類型學 vs Ch 30 的銜接

Ch 30 會把三個最重要的真實案例逐步拆解：

```
Ch 30 案例 A：BootHole（CVE-2020-10713）
  類型：T1 + T5 + T3（需要 OS root 改 BootOrder 和 grub.cfg）
  重點：grub.cfg 不驗章的設計問題 + SBAT 的引入

Ch 30 案例 B：BlackLotus（2023）
  類型：T1 + T4（CVE-2022-21894 baton drop）
  重點：降級攻擊 + dbx 不敢積極更新的政治困境 + bootkit 持久化

Ch 30 案例 C：LogoFAIL（2023）
  類型：T2 + T5（logo parser，在 SB 驗簽之前執行）
  重點：UEFI 驗簽之前的 DXE 程式碼面積問題 + 多廠商影響
```

---

## 踩雷

1. **T1 不是「Secure Boot 沒開」**：用有漏洞的 GRUB 繞過是在 Secure Boot 「開著」的情況下發生的。Secure Boot 驗章通過了，但驗的那個 binary 本身有 overflow。「Secure Boot 開啟」和「系統安全」之間有一道鴻溝，T1 就活在這道鴻溝裡。

2. **T5 的 grub.cfg 不驗章是設計決策，不是疏忽**：grub.cfg 是用戶設定，驗章需要使用者把 GPG key 嵌入 GRUB binary——這對多數 distro 的設計模型來說太複雜。GRUB secure boot locked 模式（`GRUB_ENABLE_CRYPTODISK`）存在，但幾乎沒有 distro 預設啟用。

3. **T6 的 PKfail 修補不是換 variable 就好**：受影響裝置的 PK 是用 edk2 TestCert 簽的。要修補，需要發韌體更新把「目前被 TestCert PK 保護的 KEK/db 也一起換掉」的更新。攻擊者在更新前可以搶先替換 PK，讓修補更新失效。所以修補需要很謹慎的時序和驗證流程。

4. **T4 的 baton drop 為什麼 dbx 沒有更新**：Microsoft 在 CVE-2022-21894 修補時，沒有把有漏洞的 `bootmgfw.efi` 加進 dbx，因為那樣做會讓大量使用 Windows Recovery 的用戶開不了機（recovery 環境裡的舊版 binary 也會被擋）。BlackLotus 正是利用了這個「Microsoft 不敢更新 dbx」的政策困境。

5. **T2 的 LogoFAIL 不需要 Secure Boot 開著**：LogoFAIL 在 DXE 階段執行，此時 Secure Boot 根本還沒跑到驗簽步驟。所以 LogoFAIL 對「Secure Boot 開著」和「Secure Boot 關著」的系統都有效。

6. **攻擊者的前提「OS root」不等於「完全控制」**：T1/T3/T4 需要 OS ring 0 來修改 BootOrder 或 ESP 上的檔案。但 OS ring 0 是很多漏洞研究的起點（kernel exploit 取得 ring 0 後想要持久化）。從「我有 OS root」到「我有 pre-boot 持久化」，Secure Boot bypass 是必要的一步。

---

## 進階延伸

- **SBAT（Secure Boot Advanced Targeting）**：BootHole 後 Microsoft + Red Hat 引入的撤銷機制。SBAT 是 PE binary 裡的一個 `.sbat` section，包含組件名稱和版本號。韌體維護一個 `SbatLevel` variable，列出每個組件的最低允許版本，不用 hash 就能批量撤銷有漏洞的 GRUB/shim 版本。Ch 32 詳解。

- **UEFI Secure Boot "shim review" 流程**：Red Hat 主導的 shim 審核流程（`github.com/rhboot/shim-review`），各 distro 的 shim 需要通過審核才能讓 Microsoft 簽章。這個流程本身的安全性（審核標準、reviewer 的信任）是 T6 類型的機構性問題。

- **Bootkit 的 pre-boot 持久化**：繞過 Secure Boot（T1-T6 任一）只是第一步，接下來是 Ch 31 的 bootkit 構造——如何在 bootloader 層鉤住 OS 載入流程，讓 OS 啟動後 bootkit 的 driver 仍在記憶體裡執行，且 OS 無法偵測。

---

## 動手練習

1. 在 OVMF secboot 環境（Ch 28 建立的）裡，嘗試 T6 的 PKfail 最小重現：用 Ch 28 自製的 snakeoil PK 私鑰，簽一個假的 `PK.auth` 把 PK 替換成另一個你掌控的 cert，然後用新 PK 對應的私鑰簽一個 `DB.auth` 擴展 db，最後讓一個用新 cert 簽的 EFI binary 通過 Secure Boot。觀察整個信任鏈被「重新部署」的過程。

2. 在 Ch 28 的 OVMF 環境裡模擬 T4：先 enroll 一套 key（PK/KEK/db），用 db cert 簽一個 EFI binary A；然後建立第二套 db，只包含另一個 cert；把第二套 db 更新上去（需要 KEK 私鑰）。觀察原本能開機的 EFI binary A 現在是否被擋下（它用的是舊 db 裡的 cert）。這模擬了 db 更新後舊版 binary 被撤銷的行為。

3. 讀 Binarly 的 [LogoFAIL 技術報告](https://binarly.io/blog/)，找出 BMP parser 的 overflow 在 edk2 source 的對應位置（提示：搜 `MdeModulePkg` 的 `BmpSupportLib` 或 `Logo.c`），確認 overflow 發生在哪個函式，並說明為什麼它在 Secure Boot 驗簽之前執行。

---

## 本章重點

- x86 Secure Boot 六大繞過類型：T1（signed-but-vulnerable）、T2（db 驗簽 bug）、T3（NVRAM/Setup Mode）、T4（rollback/降級）、T5（config/parser 解析）、T6（廠商測試/共用 key）
- T1 是最主流類型，利用「驗章通過 ≠ 程式碼安全」的根本矛盾
- 每個類型都有對應的「前提條件」和「攻擊者站位」，站位越高（firmware > bootloader）持久化越深
- dbx 的「事後黑名單」機制是 T4 的根本原因：直到有漏洞的 binary 被加進 dbx 之前，它永遠被信任
- T5 的 grub.cfg 不驗章是設計決策，不是疏忽，但造就了整個類型的攻擊面
- PKfail（T6）示範了即使技術上正確的機制，管理層面的失敗（測試 key 出廠）就能讓整個信任鏈崩潰
- 真實攻擊是類型的組合：BlackLotus = T1+T4，BootHole 利用鏈 = T1+T5+T3，LogoFAIL = T2+T5

---

## 自我檢核

- [ ] 能說出六個 x86 Secure Boot 繞過類型，並各舉一個真實 CVE 或事件
- [ ] 能解釋 T1（signed-but-vulnerable）的根本矛盾，並說明為什麼「Secure Boot 開著」的系統仍可能被 BootHole 攻擊
- [ ] 能說明 T5 的 grub.cfg 向量：為什麼 grub.cfg 不在 Secure Boot 驗章範圍？
- [ ] 知道 LogoFAIL 為什麼屬於 T2+T5，並能說明它在 Secure Boot 驗簽之前執行的原因
- [ ] 能描述 PKfail 的完整攻擊步驟：從洩露的私鑰到替換 PK 到讓自己的 binary 通過 Secure Boot
- [ ] 理解 T4（baton drop）為什麼 Microsoft 沒有更新 dbx，以及這個政策決定的後果
- [ ] 能把 BlackLotus 和 BootHole 分別對應到本章的類型組合

---

## 延伸閱讀

1. **Binarly, "PKfail: Untrusted Platform Keys Undermine Secure Boot on UEFI Ecosystem"（2024）**（binarly.io/blog）
   讀哪裡：技術分析章節，重點是「如何識別受影響韌體」和「攻擊步驟示範」
   學什麼：T6 類型的完整案例，以及 PKI ceremony 失敗如何讓技術上正確的機制完全失效
   關聯：直接對應本章 T6 和 T3 的交叉，接 Ch 32（dbx/SBAT 撤銷的局限性）

2. **ESET Research, "BlackLotus UEFI bootkit: Myth confirmed"（2023）**（welivesecurity.com）
   讀哪裡：完整技術報告，重點是 CVE-2022-21894（baton drop）的利用方式和 bootkit 持久化機制
   學什麼：T1+T4 的組合攻擊，以及「dbx 沒有更新」的政策原因與攻擊者的計算
   關聯：直接接 Ch 30（真實利用鏈），是 BlackLotus 章節的預讀材料

3. **Binarly, "LogoFAIL: Security Implications of Image Parsing During System Boot"（2023）**（binarly.io/blog 或 BHEU 2023 簡報）
   讀哪裡：技術報告或 Black Hat Europe 2023 的演講投影片
   學什麼：T2+T5 的交叉，DXE 階段的 parser 執行在 Secure Boot 驗簽之前，以及如何從 ESP 的 logo 檔觸發 overflow
   關聯：對應本章 T2/T5，和 Ch 30 的 LogoFAIL 案例分析形成完整的「機制→利用→防禦」脈絡

→ [下一章](./30-real-bypass-chains.md)
