# Ch 31 — bootkit 構造

> **目標**：Ch 30 拆解了三條繞過鏈，本章回答下一個問題：繞過成功後怎麼持久化？系統性地走過 bootkit 的四種持久化層級——ESP 感染、SPI flash implant、runtime hook、MOK 濫用——並對照公開 bootkit（LoJax、MoonBounce、CosmicStrand、BlackLotus）的真實實作，建立「持久化強度 vs 偵測難度」的完整地圖。

---

## 持久化的本質問題

攻擊者在 UEFI 環境取得代碼執行後，面臨一個選擇：

**一次性利用**：這次開機做壞事（竊密、patch kernel），但下次開機一切復原。
**持久化**：每次開機前都執行攻擊者的程式碼，即使 OS 重灌也不死。

bootkit 的定義就是第二種。它的技術核心是「在 OS 啟動之前就執行」，讓任何 OS 層的防禦工具都失效——因為攻擊者的程式碼在 OS 防禦工具初始化之前就已經跑完了。

持久化的強度由一個問題決定：**攻擊者的程式碼儲存在哪裡、以及誰能清除那個位置？**

---

## 持久化層級概覽

```
層級                 存放位置              清除方法           重灌 OS 後存活？
─────────────────────────────────────────────────────────────────────────────
L1: NVRAM Boot Entry  UEFI NVRAM           清 NVRAM 或刪 entry    否（通常）
L2: ESP 感染          ESP 磁碟分區         格式化 ESP / 重裝       否
L3: SPI Flash Implant BIOS ROM（SPI chip） 重刷 ROM               是 ✓
L4: 嵌入韌體/ME       Intel ME / AMD PSP   需廠商工具             是 ✓（更難）
─────────────────────────────────────────────────────────────────────────────
```

L1/L2 是「軟體層持久化」，L3/L4 是「韌體層持久化」。blacklotus 主要是 L1+L2，LoJax/MoonBounce/CosmicStrand 是 L3。

---

## L1：NVRAM Boot Entry 持久化

### 機制

最簡單的持久化：在 UEFI NVRAM 中新增一個 Boot 項目，指向攻擊者的 EFI binary，並把它插入 `BootOrder` 的前端。

```c
/* 概念流程（EDK2 UEFI shell 等效操作）*/

/* 1. 在 ESP 上放置惡意 EFI */
// ESP:\EFI\legitimate-looking-dir\backdoor.efi

/* 2. 建立 Boot 項目 */
// 透過 EFI_LOAD_OPTION 結構定義一個指向 backdoor.efi 的 entry
// 透過 SetVariable 寫入 "Boot0099" NVRAM variable

/* 3. 更新 BootOrder，把 Boot0099 放在最前面 */
// 修改 "BootOrder" variable：{ 0x0099, 0x0000, 0x0001, ... }

/* 4. 重開機 */
// BDS 選擇 BootOrder[0] = Boot0099
// 載入 backdoor.efi → 執行 → 再 chain-load 真實 OS
```

### 被 BlackLotus 使用的方式

BlackLotus Stage 1 就是 ESP 上的惡意 bootloader，它透過 NVRAM 的 Boot Entry 確保自己每次開機都被選中：

```
BootOrder: [BlackLotus-Entry, Windows-Entry, ...]
                │
                ▼
ESP:\EFI\Microsoft\Boot\bootmgfw.efi（被替換為 BlackLotus Stage 1）
                │
                ▼
Stage 1 執行 → 觸發 CVE-2022-21894 → 載入 Stage 2 → ... → 真實 Windows
```

### 優缺點

```
優點：
  ✓ 不需要 SPI 寫入能力，OS admin 即可做
  ✓ 隱藏在合法的 Boot Entry 結構中
  ✓ 大多數使用者不知道如何檢查 NVRAM Boot Entry

缺點：
  ✗ UEFI 設定工具（efibootmgr / bcdedit）可以輕易看到並刪除
  ✗ 格式化 ESP 或重裝 OS 後消失（取決於重裝是否清 NVRAM）
  ✗ EDR/安全工具可以監控 BootOrder variable 的寫入
```

---

## L2：ESP 感染（換 bootmgfw / grub）

### 機制

直接替換 ESP 上的合法 bootloader，讓 UEFI 以為在執行正常的 Windows/Linux bootloader，實際上是攻擊者的 loader。

```
攻擊前：
ESP:\EFI\Microsoft\Boot\bootmgfw.efi   ← Microsoft 簽章，合法
ESP:\EFI\ubuntu\grubx64.efi           ← Canonical 簽章，合法

攻擊後：
ESP:\EFI\Microsoft\Boot\bootmgfw.efi   ← 被替換為有漏洞的舊版，或惡意版本
                                          （如果使用舊版，仍有合法簽章）
ESP:\EFI\ubuntu\grubx64.efi           ← 仍然是合法的，但 grub.cfg 被替換
```

### 兩種 ESP 感染策略

**策略 A：替換為有洞舊版（BlackLotus 手法）**

用有合法簽章但含漏洞的舊版 binary 替換——Secure Boot 驗章通過，但執行時觸發 CVE。

**策略 B：shim/MOK 路徑**

替換整個 shim chain：shim → MOK → grub → kernel。如果攻擊者能控制 MOK（Machine Owner Key）資料庫，就可以用自己的 key 簽的 grub 和 kernel。

```bash
# MokManager 操作（需要使用者確認，但如果已有 OS root）
# 把攻擊者的公鑰加入 MOK：
mokutil --import attacker_key.cer
# 重開機後 MokManager 會問「是否確認加入這個 key？」
# 攻擊者若能自動化這一步（例如 exploit MokManager 本身），就繞過了使用者確認
```

### 自 self-signed / shim MOK 濫用

一個關鍵認知：shim 的設計讓每個 distro maintainer（或任何 MOK 持有者）都可以用自己的 key 簽 GRUB/kernel。這是一個**刻意設計的信任代理**。

問題：如果攻擊者能把自己的 key 加入 MOK，他就成為了受信任的 "distro maintainer"——之後用這個 key 簽的任何 EFI 都被接受，即使 Secure Boot 啟用。

```
信任鏈（MOK 路徑）：
  UEFI db → shim（Microsoft 簽章）→ MokManager 管理 MOK
                                         ↓
                                    MOK db（本地 key 存在 NVRAM）
                                         ↓
                               shim 接受用 MOK key 簽的 grub/kernel

攻擊者插入：
  把攻擊者 key 加入 MOK → 之後的惡意 EFI 用這個 key 簽章 → shim 接受
```

### 偵測 ESP 感染

```bash
# Linux：比對 ESP 上 EFI binary 的 hash 與廠商已知版本
sha256sum /boot/efi/EFI/ubuntu/grubx64.efi
# 對比 Ubuntu 官方發布的 hash（可從 Ubuntu 的 security packages 取得）

# 或用 pesign 驗章，確認簽章是 distro 的 key 而不是未知 key
pesign -S -i /boot/efi/EFI/ubuntu/grubx64.efi

# Windows：
Get-FileHash "C:\Windows\Boot\EFI\bootmgfw.efi" -Algorithm SHA256
# 對比 Microsoft 官方的版本 hash
```

---

## L3：SPI Flash Implant（最頑強）

### 為什麼 SPI 是聖杯

UEFI ROM 儲存在主機板上的 SPI flash chip。OS 完全無法感知這個 chip 的存在——OS 看到的是記憶體映射的韌體，但 **SPI chip 的寫入需要 SPI 控制器的授權**，且正常情況下 BIOS_CNTL 的保護位元（SMM_BWP、BLE）讓 OS 無法寫入。

但如果：
1. 攻擊者已有 Ring -2（SMM）執行能力（Ch 13），可以清 SMM_BWP 後寫 SPI
2. 或者 BIOS_CNTL 保護不完整（SPI PR0-4 未設定，或有 CHIPSEC 能找到的漏洞）
3. 或者物理存取（Ch 35：SPI 竄改）

那麼攻擊者就能直接修改 UEFI ROM，把惡意 DXE driver 嵌入其中。之後每次開機，惡意 driver 從 ROM 直接被 DXE dispatcher 載入——完全在 OS 啟動之前，OS 層的任何防禦工具對此完全盲目。

### SPI Implant 的實作流程

```
前提：Ring 0 + SMM exploitation（或物理存取）
│
▼
步驟 1：讀取目前 SPI ROM 內容
  從 SMM 內部：透過 SPI flash 的 Read 命令讀取全部 ROM
  或物理存取：CH341A 讀取 SPI chip

步驟 2：解析 UEFI FV（Firmware Volume）結構
  找到一個合適的 FFS（File System）項目來插入惡意 driver
  或替換現有的 DXE driver（確保大小不超過原有空間）

步驟 3：建構惡意 DXE driver
  功能：在 DXE 階段 hook runtime services（見 practice-a）
  或：安裝後門 SMI handler
  或：修改 Secure Boot 驗章邏輯

步驟 4：修改 FV，插入惡意 driver
  更新 FFS header（size、type、checksum）
  更新 FV header（checksum）
  確保 DEPEX 讓 driver 在適當時機被載入

步驟 5：把修改後的 ROM 寫回 SPI chip
  從 SMM 內部：清 BIOS_CNTL.SMM_BWP，發送 SPI Write Enable + Page Program 命令
  物理：CH341A 直接寫入

步驟 6：重開機
  DXE dispatcher 自動載入我們的惡意 driver
  惡意 driver 在 OS 啟動前執行，完成持久化
```

### LoJax：第一個公開的 UEFI SPI Rootkit

2018 年，ESET 發現 **LoJax**（APT28/Fancy Bear 開發），是有記錄的第一個在野的 UEFI SPI rootkit。它的工作原理：

```
感染階段（需要 OS 層 admin 或 exploit）：
  1. 讀取目前 UEFI ROM
  2. 在 FV 中找到空間插入惡意 DXE driver
  3. 利用已知的 BIOS 寫保護漏洞，或完全不受保護的 SPI 設定，寫回 ROM
     （LoJax 分析顯示某些目標機器的 SPI 完全沒有 PR0-4 保護）

持久化後：
  每次開機，LoJax 的 DXE driver 從 ROM 被載入
  → 監控 OS 是否安裝了某個「感染」標記
  → 如果標記消失（OS 重灌），重新感染系統（下載並安裝其他 malware）
```

LoJax 的 DXE driver 功能相對簡單：確保 OS 層的另一個 malware（rpcnetp.exe）持續存在。真正的武器是 OS 層的 malware，UEFI rootkit 只是確保重灌後它能自動重裝。

### MoonBounce：更進一步的 SPI Implant

2022 年，Kaspersky 發現 **MoonBounce**，比 LoJax 更先進：

```
LoJax：          新增一個 DXE driver 到 FV
MoonBounce：     修改現有的 CORE_DXE（核心 DXE 元件）的程式碼
                 → 比較難偵測（沒有多出一個 driver）
                 → 修改的是 DXE dispatcher 的早期執行路徑
                 → 更早執行，在其他 driver 之前
```

MoonBounce 的手法：patch 現有 FV 中的 `CORE_DXE.efi`，在其 entry point 插入 hook，讓它在初始化時把惡意 payload 注入 OS 開機流程的記憶體中（shellcode injection into ntoskrnl 的記憶體映射）。

### CosmicStrand：EFI 韌體中的 Bootstage Hook

2022 年，Kaspersky 另外發現 **CosmicStrand**，影響特定 ASUS 和 Gigabyte 主機板的 UEFI ROM：

```
CosmicStrand 的特色：
  1. hook DXE dispatcher 的 hook，讓惡意程式碼在每個 DXE driver 載入前執行
  2. 在 Windows kernel 載入時，patch ntoskrnl.exe 的記憶體映射
     （在 kernel 執行之前修改它的 text section）
  3. 被 patch 的 ntoskrnl 在 OS 啟動時載入攻擊者的 kernel driver
  4. Kernel driver 建立 backdoor 連線

執行鏈：
SPI ROM → DXE dispatcher hook → ntoskrnl patch → OS kernel → backdoor driver
```

CosmicStrand 的發現點：UEFI ROM 中某個 DXE driver 的 entry point 被改了幾個 bytes，指向一塊額外插入的程式碼。

---

## L4：Hook 開機流程與 Runtime Services

### UEFI Runtime Hook 的持久化應用

如果攻擊者不能（或不想）改 SPI ROM，還有一個選項：把惡意 DXE driver 植入後，hook `gRT->ExitBootServices`，在 OS 接手的那一刻執行最後的 payload。

這個 hook 不持久——下次重開機惡意 driver 不在。但可以組合：

```
持久化 + Hook 組合：
  ESP 植入 → 每次開機執行惡意 EFI
  惡意 EFI hook gBS->ExitBootServices → 在 OS 啟動前最後一刻做事
                                    （例如：patch kernel、設定 SMRAM 後門）
```

### UEFI Runtime Services 的持久 hook

另一個選項：hook `gRT->SetVariable`（見 practice-a）或 `gRT->GetVariable`，讓攻擊者的程式碼在 **OS 啟動後**每次 EFI variable 操作時被呼叫。

這種 hook 存活於 OS 啟動後（因為 Runtime Services 跨越 ExitBootServices），但它儲存在哪裡？

- 如果惡意 driver 在 ESP：OS 重啟後 hook 就消失（ESP 的 driver 不被重新載入）
- 如果惡意 driver 在 SPI ROM：hook 持久化

所以 runtime hook 必須搭配 SPI implant 才能真正持久化。

---

## 持久化強度 vs 偵測難度總表

```
┌────────────────────┬──────────────┬──────────┬────────────┬────────────┐
│ 持久化層級          │ 重灌 OS 存活 │ 需要能力 │ 偵測工具   │ 清除難度   │
├────────────────────┼──────────────┼──────────┼────────────┼────────────┤
│ L1: NVRAM Boot     │ 通常否       │ OS admin │ efibootmgr │ efibootmgr │
│     Entry          │              │          │ CHIPSEC    │ 刪 entry   │
├────────────────────┼──────────────┼──────────┼────────────┼────────────┤
│ L2a: ESP 替換      │ 否（重裝清） │ OS admin │ hash 比對  │ 重裝 OS    │
│      bootloader    │              │          │ pesign     │ 覆蓋 ESP   │
├────────────────────┼──────────────┼────────────┼──────────┼────────────┤
│ L2b: MOK 污染      │ 否（清 MOK）│ OS root  │ mokutil    │ mokutil    │
│                    │              │          │ --list-enrolled  │ --delete │
├────────────────────┼──────────────┼──────────┼────────────┼────────────┤
│ L3: SPI Implant    │ 是 ✓         │ SMM 或   │ CHIPSEC    │ 重刷 ROM   │
│     (DXE driver)   │              │ 物理存取 │ 韌體 hash  │ 需要硬體   │
├────────────────────┼──────────────┼──────────┼────────────┼────────────┤
│ L3+: 修改現有 FV   │ 是 ✓         │ 同上     │ 難：無多餘 │ 更難       │
│      module        │              │          │ DXE driver │            │
├────────────────────┼──────────────┼──────────┼────────────┼────────────┤
│ L4: ME/PSP         │ 是 ✓✓        │ Ring -3  │ 幾乎無法   │ 廠商 RMA   │
│     implant        │              │ 漏洞     │ 偵測       │ 幾乎無法   │
└────────────────────┴──────────────┴──────────┴────────────┴────────────┘
```

---

## 真實 bootkit 對照圖

```
                     ESP 感染   SPI Implant  Runtime Hook  持久化強度
LoJax（2018）          否          是 ✓           否          ★★★★
MoonBounce（2022）     否          是 ✓（patch）   否          ★★★★★
CosmicStrand（2022）   否          是 ✓（hook）    是（OS boot）★★★★★
BlackLotus（2023）     是 ✓        否              是（kernel） ★★★
BootHole 概念          是 ✓        否（可選）      否           ★★

★ = 越高越難清除
```

---

## BlackLotus 的 Self-Staging 架構

BlackLotus 最精妙的設計是它的 **self-staging**——在初次安裝後，bootkit 本身會確保自己各個 Stage 的完整性，並在被破壞後自我修復：

```
BlackLotus 初次安裝（需 Windows admin）：
  1. 把有洞的舊版 bootmgfw.efi 放到 ESP（或 VHD）
  2. 在 NVRAM 建立 Boot Entry 指向它
  3. 把 Stage 1 loader 放到 ESP 的隱藏目錄

每次開機：
  Stage 1 執行
  └── 驗證 Stage 2（winload.efi）的完整性
      ├── 完整：繼續載入
      └── 不完整：從 Stage 1 的加密 payload 重建 Stage 2
              （self-healing：即使 Windows Defender 刪了 Stage 2，Stage 1 重建它）

Stage 2 → Stage 3（kernel driver）
  Stage 3 的任務：
  ├── 停用 Windows Defender 的 early launch
  ├── 停用 Virtualization Based Security (VBS)
  ├── 建立 HTTP C2 通道
  └── 持續監控 Stage 1/2 的完整性（再加一層 self-healing）
```

self-healing 機制讓 BlackLotus 比普通 bootkit 更難清除：即使你清掉了 Stage 2 或 Stage 3，Stage 1 會在下次開機時重新建立它們。

---

## 建構最小 bootkit PoC 的思路

（以下為教學性質，說明原理，不提供完整武器化程式碼）

一個最小的「ESP 持久化」bootkit 的邏輯框架：

```c
/* 概念：最小 bootkit loader（教學用途，說明機制）*/

EFI_STATUS EFIAPI BootkitEntry(
    IN EFI_HANDLE        ImageHandle,
    IN EFI_SYSTEM_TABLE  *SystemTable)
{
    /* 步驟 1：安裝 ExitBootServices hook，捕獲 OS 接手的時機 */
    gOrigExitBootServices = gBS->ExitBootServices;
    gBS->ExitBootServices = HookedExitBootServices;
    /* 更新 gBS CRC32 */

    /* 步驟 2：Chain-load 合法的 OS bootloader */
    /* 讓使用者看到正常開機流程，不引起懷疑 */
    EFI_HANDLE LegitLoaderHandle;
    LoadImage(TRUE,
              ImageHandle,
              L"\\EFI\\Microsoft\\Boot\\bootmgfw_orig.efi",  /* 備份的合法版本 */
              NULL, 0,
              &LegitLoaderHandle);
    StartImage(LegitLoaderHandle, NULL, NULL);

    return EFI_SUCCESS;
}

VOID EFIAPI HookedExitBootServices(
    IN EFI_HANDLE  ImageHandle,
    IN UINTN       MapKey)
{
    /* OS 即將接手，這是最後的機會在 UEFI 環境做事 */
    /* 例如：patch kernel 的記憶體映射 */
    PatchKernelInMemory();

    /* 呼叫原始 ExitBootServices */
    gOrigExitBootServices(ImageHandle, MapKey);
}
```

真實的 bootkit 在這個框架上加入：錯誤處理、反偵測、加密 payload、C2 通訊、self-healing 等。

---

## 防禦角度

### BootGuard：保護 ROM 不被竄改

Intel BootGuard（或 AMD PSB）是對抗 SPI Implant 的最強防線：

```
BootGuard 流程：
  CPU 開機 → CPU 內建的 ACM（Authenticated Code Module）
  → ACM 驗章 UEFI ROM 的初始 block（IBB，Initial Boot Block）
  → 驗章用的 key 在 CPU 的 fuse 中（無法修改）
  → 如果 IBB 驗章失敗 → 機器拒絕開機（或靜默 reset）

效果：SPI Implant 修改了 ROM → IBB 驗章失敗 → 開機失敗
      攻擊者要植入 SPI Implant 必須同時找到 BootGuard 的繞過
```

**BootGuard 不保護整個 ROM**：只保護 IBB（通常是 SEC + PEI 早期程式碼）。後期的 DXE FV 可能不在 BootGuard 的保護範圍內。這是 LoJax 能在某些機器上成功的原因——那些機器的 BootGuard 可能沒有啟用，或保護的範圍不包含 DXE FV。

### Secure Boot + BootGuard 的組合

```
完整信任鏈（有 BootGuard + Secure Boot 的機器）：

CPU fuse ──(BootGuard)──▶ SEC/PEI（IBB 驗章）
                │
                ▼
        DXE（UEFI 環境，BootGuard 保護較弱）
                │
                ▼
        BDS → Secure Boot 驗章 bootloader
                │
                ▼
        OS bootloader（合法簽章）
                │
                ▼
        OS kernel

攻擊面：
  BootGuard 保護 IBB，但 DXE 可能被攻擊（CosmicStrand/MoonBounce）
  Secure Boot 驗 bootloader，但 grub.cfg/logo 不被驗（BootHole/LogoFAIL）
  兩者組合才能真正縮小攻擊面
```

---

## 踩雷

1. **以為 SPI Implant 需要物理存取**：LoJax 是純軟體感染——它利用 BIOS 寫保護配置不足（無 PR0-4 保護），透過 OS ring-0 直接寫 SPI。不要假設 SPI 攻擊必然需要拆機接燒錄器。CHIPSEC 稽核 SPI 保護是第一步。

2. **認為重灌 OS 能清掉 SPI Implant**：這是 SPI Implant 的設計目的。重灌 OS 只是換了 `C:\Windows`，SPI flash 上的 UEFI ROM 完全不受影響。清除 SPI Implant 需要重刷 ROM，而且要確保 reflash 的來源是乾淨的。

3. **忽略 MOK 的持久性**：MOK 存在 NVRAM，重裝 OS 後如果沒有清除 MOK，攻擊者的 key 仍然在。使用者通常不知道如何清 MOK，而且清掉之後可能影響合法的雙開機設定。

4. **把 self-healing 當成「永久存活」**：BlackLotus 的 self-healing 有弱點——如果 Stage 1 被刪除，Stage 2/3 就不會被重建。正確的清除順序是先清 Stage 1（NVRAM Boot Entry + ESP loader），再清 Stage 2/3。

5. **低估 VHD boot 路徑**：BlackLotus 初始安裝使用 VHD，讓惡意的 ESP 存在於一個 .vhd 檔案內，而不是真實的 ESP 分區。傳統的 ESP 掃描工具可能找不到。

6. **誤以為 UEFI Secure Boot key 刪了就安全**：如果攻擊者已經有 SPI Implant，它可以在開機時重新設定 Secure Boot DB/DBX，讓自己的 key 再次加回去。清 Secure Boot key 而不清 ROM 是無效的。

---

## 進階延伸

- **Measured Boot 作為偵測手段**：即使攻擊者能持久化，只要 TPM 的 PCR 值改變（被 implant 污染），遠端證明（remote attestation）就能偵測到。這是 Ch 43 的主題。SPI Implant 會改變 PCR[0]（BIOS measurement），讓 sealed key（Ch 39）失效。

- **BootGuard key manifest 洩漏**：如果 OEM 的 BootGuard BPM（Boot Policy Manifest）私鑰洩漏，攻擊者可以簽章惡意的 IBB，讓 BootGuard 也失效。Intel 在 2022 年的 Intel Alder Lake BIOS 原始碼洩漏中，部分 BootGuard key material 可能隨之暴露——這是研究者關注的開放問題。

- **UEFI Capsule 作為 Implant 入口**：正規的 UEFI capsule update 機制是用來更新 BIOS 的。如果 capsule 驗章邏輯有洞（CVE in capsule update），攻擊者可以用 capsule 機制直接刷入惡意 ROM——完全不需要 OS level SPI 存取。這是 Ch 6 的延伸應用。

---

## 動手練習

### 練習：NVRAM Boot Entry 注入（OVMF 環境）

```bash
# 在 QEMU OVMF 環境（WSL 可跑）：
# 1. 建立一個簡單的 "backdoor" EFI（只印出一行字）
# 2. 放到 ESP

# 進入 UEFI Shell 後：
# 查看目前 Boot Order：
bcfg boot dump

# 新增一個 Boot Entry 指向我們的 EFI：
bcfg boot add 0 fs0:\backdoor.efi "BackdoorEntry"

# 把它移到 BootOrder 第一位：
bcfg boot mv 0 0

# 重開機：確認 backdoor.efi 在合法 OS 前執行
# 再次進入 shell 用 bcfg boot rm 清除它
```

### 練習：用 efibootmgr 偵測可疑 Boot Entry

```bash
# Linux 上（WSL 不支援 NVRAM 操作，需要真實 Linux）：
sudo efibootmgr -v
# 輸出所有 Boot Entry，人工審查是否有不認識的項目
# 特別注意：
#   ├── 指向 ESP 以外位置的 entry（如 VHD 內部）
#   ├── 路徑包含不常見目錄名稱的 entry
#   └── 簽章無法驗證的 EFI binary

# 驗章檢查：
pesign -S -i /boot/efi/EFI/ubuntu/grubx64.efi
# 應該看到 Canonical 的簽章
# 如果看到陌生的 key，就有問題
```

---

## 本章重點

- **四種持久化層級**：NVRAM Boot Entry（L1）、ESP 感染（L2）、SPI Implant（L3）、ME/PSP（L4）——強度遞增，清除難度也遞增
- **SPI Implant 是真正的持久化**：OS 重灌不死，只有重刷 ROM 才能清除；LoJax/MoonBounce/CosmicStrand 是三個公開的真實案例
- **BlackLotus 主要是 L2**：聰明之處是用「有漏洞的合法簽章舊版 binary」作為 ESP 感染的載體，而非用未簽章 binary
- **self-healing 架構**：BlackLotus 的 Stage 1/2/3 互相監控並修復，增加清除難度
- **BootGuard 保護 ROM，Secure Boot 保護 bootloader**：兩者是不同防線，各有缺口，組合起來才能縮小攻擊面
- **誠實標注**：SPI 直接寫入操作（真機植入）需要真實 x86 平台 + ring-0 + SMM exploitation，本章描述的是已公開研究的技術原理

---

## 自我檢核

- [ ] 能說出四種持久化層級，並說明每種在 OS 重灌後是否存活
- [ ] 能解釋 LoJax 為什麼是「第一個在野 UEFI SPI rootkit」以及它的功能（確保 OS 層 malware 在重灌後重裝）
- [ ] 能區分 MoonBounce（patch 現有 FV module）和 LoJax（新增 DXE driver）的差異
- [ ] 能說出 BlackLotus 的三個 Stage 及每個 Stage 的功能
- [ ] 理解 self-healing 的概念及其最弱點（Stage 1 被刪時失效）
- [ ] 能說出 BootGuard 保護的範圍（IBB）及其限制（DXE FV 可能不在保護範圍）
- [ ] 理解 MOK 的信任代理機制，以及為何 MOK 污染是一種持久化手段

---

## 延伸閱讀

1. **"LoJax: First UEFI Rootkit Found in the Wild" — ESET（2018）**
   讀哪裡：`welivesecurity.com` 搜尋 LoJax，完整白皮書（PDF）
   學什麼：第一個在野 UEFI SPI rootkit 的感染流程、DXE driver 植入機制、SPI 寫保護缺口分析
   關聯：本章 L3 SPI Implant 的最佳教學案例；直接對應 Ch 14 的 BootGuard 為何是防禦答案

2. **"MoonBounce: The Dark Side of UEFI Firmware" — Kaspersky（2022）**
   讀哪裡：`securelist.com` 搜尋 MoonBounce，技術分析報告
   學什麼：patch 現有 DXE module（而非新增）的隱蔽技術；inMemory injection 到 ntoskrnl 的機制
   關聯：本章「修改現有 FV module」的進階版本；理解為何 L3+ 比 L3 更難偵測

3. **《Rootkits and Bootkits》— Matrosov, Rodionov, Bratus（No Starch, 2019）**
   讀哪裡：第 9-13 章（UEFI rootkit 演進，從 MBR bootkit 到 UEFI SPI implant）
   學什麼：bootkit 技術的歷史演進，理解為什麼每次安全機制升級都催生更強的 bootkit
   關聯：本章所有持久化層級的完整歷史背景；搭配本課 Ch 01 的「為什麼攻韌體」看更有深度

→ [下一章](./32-dbx-sbat-revocation.md)
