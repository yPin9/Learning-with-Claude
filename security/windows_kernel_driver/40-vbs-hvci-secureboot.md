# Ch 40 — VBS、HVCI 與安全啟動

> 目標：理解 VBS（Virtualization Based Security）、HVCI（Hypervisor-Protected Code Integrity）和 Secure Boot 的運作原理，以及為什麼它們代表著現代 Windows 安全的最後防線。

## 信任鏈概覽

傳統 Windows 的安全假設：如果核心（Ring 0）被攻陷，一切都完了。

VBS 打破了這個假設，引入了更高特權層（Ring -1 / VMX Root）來保護 Ring 0 自身：

```
──────────────────────────────────────────── 硬體
  UEFI Secure Boot（驗證 Bootloader 簽章）
──────────────────────────────────────────── 啟動信任鏈
  Windows Boot Manager / Hyper-V Hypervisor
──────────────────────────────────────────── VMX Root（Ring -1）
  ┌─────────────────────────────────────┐
  │         Secure World（VTL 1）       │  ← HVCI 在此驗證核心
  │   Secure Kernel                     │
  │   Credential Guard（LSA Isolated）  │
  │   ...                               │
  └─────────────────────────────────────┘
──────────────────────────────────────────── VTL 1 / VTL 0 邊界
  ┌─────────────────────────────────────┐
  │         Normal World（VTL 0）       │
  │   Ring 0：Windows Kernel            │  ← 攻擊者能到這裡
  │   Ring 3：Applications              │
  └─────────────────────────────────────┘
──────────────────────────────────────────── VTL = Virtual Trust Level
```

## Secure Boot

**作用**：防止在 UEFI 階段載入未簽章的 Bootloader（防止 Bootkit）。

```
UEFI Firmware（自帶 Secure Boot Database：db/dbx/kek/pk）
       ↓ 驗證簽章
Windows Boot Manager（bootmgfw.efi）
       ↓ 驗證
Windows OS Loader（winload.efi）
       ↓ 驗證
ntoskrnl.exe 的完整性（Measured Boot）
       ↓
系統啟動
```

**如果 Secure Boot 被繞過**（UEFI 漏洞或 Enroll 了攻擊者的 Key）：
- 攻擊者可以放 Bootkit，在 Windows 核心載入前就控制系統
- 整個 VBS/HVCI 信任鏈從根部斷裂

**BlackLotus（CVE-2022-21894）**：2022 年 Bootkit，繞過 Secure Boot，是近年最嚴重的案例之一。

## VBS（Virtualization Based Security）

VBS 利用 CPU 虛擬化（Intel VT-x / AMD-V）建立兩個隔離的「世界」（VTL）：

- **VTL 0**（Normal World）：普通 Windows 核心和應用跑在這裡
- **VTL 1**（Secure World）：Secure Kernel 和受保護的進程

```c
// VTL 0 的核心（Ring 0）無法直接讀取 VTL 1 的記憶體
// 因為 Hypervisor 管理 EPT（Extended Page Table），設定了 VTL 邊界
// 即使 VTL 0 Ring 0 執行 RDMSR / MOV CR3 也無法跨越 VTL 邊界
```

**VTL 1 的元件**：
- Secure Kernel
- Credential Guard（保護 NT Hash、Kerberos Ticket 在 LSA 中）
- HVCI Engine
- Device Guard

## HVCI（Hypervisor-Protected Code Integrity）

HVCI 解決的問題：即使攻擊者有 Ring 0，也無法執行未簽章的核心代碼。

### HVCI 的運作機制

```
傳統 DSE（無 HVCI）：
  NtLoadDriver → CI.dll 驗證簽章 → 把驅動映射到核心 → 可執行

HVCI：
  NtLoadDriver → CI.dll 驗證 → 呼叫 VTL 1（SKCI）複驗
                                      ↓
                                 Secure Kernel 驗證簽章有效
                                      ↓
                                 設定 EPT：此記憶體頁可執行
                                      ↓
  僅通過 HVCI 認可的頁面才有 X bit（EPT 可執行）
```

**關鍵**：EPT（Extended Page Table）由 Hypervisor（VTL 1 以上）控制。VTL 0 的核心即使修改自己的頁表（CR3），也無法改變 EPT 的 X 位。

所以：
- 攻擊者用任意寫把 `ci!g_CiEnabled` 改成 0？**沒用**——HVCI 用 EPT 強制 X bit，不依賴 g_CiEnabled
- 攻擊者 patch ntoskrnl 代碼？**沒用**——核心代碼頁面在 EPT 中是唯讀（Read-only + Execute），無法寫入

### HVCI 保護的具體範圍

| 保護對象 | 無 HVCI | 有 HVCI |
|---------|---------|---------|
| 核心代碼頁面 | Ring 0 可寫 | EPT 唯讀，Ring 0 無法修改 |
| 未簽章驅動載入 | 靠 DSE（可 patch g_CiEnabled 繞過） | SKCI 在 VTL 1 驗證，無法繞過 |
| Token / EPROCESS | 不保護（PatchGuard 也不保護）| **不保護**（VTL 0 仍可修改） |
| ntdll.dll（用戶態）| 不保護 | 不保護 |

**注意**：HVCI 主要保護「代碼完整性」，不保護所有核心資料結構。Token 竊取在有 HVCI 的系統上仍然可行——只是無法執行未簽章的惡意驅動代碼。

## Credential Guard

VTL 1 中的 LSA Isolated Process，保護 NTLM Hash 和 Kerberos TGT 不被 mimikatz 竊取：

```
傳統（無 Credential Guard）：
  mimikatz → OpenProcess(lsass) → ReadProcessMemory → 拿到 Hash

有 Credential Guard：
  NTLM Hash 和 Kerberos TGT 存在 VTL 1 的 LSAIso 中
  lsass（VTL 0）只有加密後的版本 + 拿給 LSAIso 解密的能力
  mimikatz ReadProcessMemory → 只拿到加密 Blob，無法解密（Key 在 VTL 1）
```

## 查看 VBS / HVCI 狀態

```powershell
# 查看 VBS 狀態
msinfo32 → System Summary → "Virtualization-based security"

# 命令列
Get-ComputerInfo -Property "DeviceGuard*"

# WMI
Get-WmiObject -Namespace root\Microsoft\Windows\DeviceGuard -Class Win32_DeviceGuard

# 輸出 SecurityServicesRunning 中包含：
# 1 = Credential Guard
# 2 = HVCI
```

```
kd> !VirtualizationBasedSecurity
; WinDbg 查看 VBS 狀態

kd> !msrread 0x10
; 查 IA32_FEATURE_CONTROL MSR（是否啟動虛擬化）
```

## 安全啟動 + VBS 的攻擊面

即使有完整的 Secure Boot + VBS + HVCI，仍有攻擊面：

1. **UEFI 漏洞**：直接攻擊 UEFI 韌體，在信任鏈建立前就控制
2. **PCIe / DMA 攻擊**：惡意 PCIe 裝置直接存取物理記憶體，繞過 EPT（需要 IOMMU/VT-d 對應保護）
3. **Secure Kernel 漏洞**：VTL 1 的 Secure Kernel 本身如果有漏洞（攻擊面非常小，但存在）
4. **微架構攻擊**：Spectre/Meltdown 類漏洞在 VTL 邊界洩漏記憶體
5. **人為管理疏失**：Secure Boot Keys 洩漏、測試機關閉 HVCI

## 硬體需求

HVCI 需要：
- CPU：Intel VT-x + EPT，或 AMD-V + NPT（Nested Page Tables）
- 第二層地址轉換（SLAT）
- IOMMU（Intel VT-d / AMD-Vi）防 DMA 攻擊
- TPM 2.0（Measured Boot + Attestation）

Windows 11 強制要求這些硬體，部分原因就是為了讓 VBS/HVCI 預設開啟。

## 課程小結

這門課從 NT 架構到現代安全機制走了完整的一遍：

```
基礎層（Ch 1–5）：NT 架構、IRQL、IRP、EPROCESS
驅動開發（Ch 6–20）：WDM、KMDF、IOCTL、記憶體、同步、Filter
調試（Ch 21–23）：WinDbg、BSOD、Driver Verifier
安全機制（Ch 24–25）：DSE、PatchGuard、Token/ACL
漏洞利用（Ch 26–30）：IOCTL 漏洞、任意寫利用、Pool 利用、現代緩解
進階攻擊面（Ch 31–37）：Callbacks、DKOM、WFP、NDIS、BYOVD、ETW、PatchGuard
EDR 生態（Ch 38–40）：EDR 架構、Anti-EDR、VBS/HVCI/Secure Boot
```

## 自我檢核

- [ ] VTL 0 / VTL 1：VTL 0 是普通核心，VTL 1 是 Secure Kernel；EPT 邊界由 Hypervisor 管控
- [ ] HVCI 核心：EPT 控制 X bit，未簽章驅動無法取得可執行頁面，Ring 0 也繞不過
- [ ] Credential Guard：NTLM Hash 在 VTL 1，mimikatz 只能拿到加密 Blob
- [ ] HVCI **不保護** Token / EPROCESS 資料欄位（只保護代碼完整性）
- [ ] 攻擊面：UEFI 漏洞（信任鏈根部）/ DMA 攻擊（需要 IOMMU 對應防護）

→ [練習 C：IOCTL 漏洞利用模擬](./practice-c-ioctl-exploit.md)
