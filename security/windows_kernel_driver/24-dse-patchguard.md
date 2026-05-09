# Ch 24 — DSE + PatchGuard

> 目標：理解驅動簽章強制（DSE）的機制和繞過歷史，掌握 PatchGuard（KPP）的保護範圍和觸發條件。

## Driver Signature Enforcement（DSE）

Windows Vista 64 位元起，所有 kernel driver 必須有**有效的數位簽章**才能載入。

### 簽章驗證流程

```
sc start MyDriver
→ I/O Manager 呼叫 SeValidateImageData
→ 驗證 PE 的數位簽章
→ 驗證簽名鏈到受信任的 CA
→ 失敗 → STATUS_INVALID_IMAGE_HASH（錯誤碼 1275）
→ 成功 → 載入驅動
```

### 合法的繞過方式（開發期）

**Test Signing Mode**（Ch 0 已設定）：允許自簽名驅動，桌面有浮水印。

**WHQL 簽章**：送 Microsoft 認證，通常是正式發布的驅動。

**EV Code Signing Certificate**：企業用，需要購買，有 HSM 要求。

**Debug Mode**：透過 WinDbg KD 連線時，可用 `.kdfiles` 載入未簽名驅動。

### DSE 的實現

DSE 的核心是一個全域變數 `nt!g_CiEnabled`（Windows 早期）或 `ci.dll!g_CiOptions`：

```
// 舊版 Windows（Vista–Win7）
g_CiEnabled = 0  → 停用驗證
g_CiEnabled = 1  → 啟用驗證

// 攻擊者早期的 bypass：在核心裡把 g_CiEnabled 改成 0
// 結果：BSOD（PatchGuard 發現被修改）或成功（取決於時機）
```

現代 Windows 把驗證狀態放在 `ci.dll` 並用 VBS/HVCI 保護（Ch 40）。

### DSE Bypass 技術演進

1. **TDL4 rootkit（2010）**：在 MBR 層繞過，在 Windows 啟動前注入未簽名代碼
2. **SetWindowsHookEx bypass（2012）**：利用 win32k.sys 的 Hook 機制
3. **BYOVD（Bring Your Own Vulnerable Driver）**：帶一個有漏洞的已簽名驅動，用漏洞載入自己的未簽名驅動。**現在最常用的技術**（Ch 35 詳述）
4. **CVE-2022-21882 等**：win32k.sys 漏洞讓你在啟動 DSE 驗證前注入代碼

## PatchGuard（Kernel Patch Protection，KPP）

PatchGuard 是 Windows Vista 64 位元起的核心完整性保護機制，防止對核心代碼和關鍵結構的修改。

**PatchGuard 保護的範圍：**

```
核心代碼（.text section）：ntoskrnl.exe、hal.dll、win32k.sys
SSDT（System Service Descriptor Table）
IDT（Interrupt Descriptor Table）
GDT（Global Descriptor Table）
核心堆疊
EPROCESS 的某些欄位
MSR 暫存器（IA32_LSTAR、IA32_SYSENTER_EIP）
```

**PatchGuard 的工作方式（簡化）：**

```
1. 系統啟動時，PatchGuard 初始化，記錄受保護結構的 Hash
2. 定期（隨機間隔，幾分鐘到幾十分鐘）用 DPC/APC/WorkItem 喚醒
3. 重新計算 Hash，和記錄比較
4. 如果不一致 → KeBugCheckEx(0x109, ...) → BSOD
```

Bugcheck `0x109 CRITICAL_STRUCTURE_CORRUPTION`：PatchGuard 發現保護的結構被修改。

### PatchGuard 保護的不是什麼

PatchGuard **不保護**：
- EPROCESS 的大部分欄位（Token、PID 等）→ Token 竊取不觸發 KPP
- Driver Object 的 Dispatch Table（部分版本會检查）
- 分頁記憶體（不能隨機 Hash 分頁內容，頁面可能換出）

所以 DKOM 攻擊（修改 EPROCESS）不會直接觸發 PatchGuard。

### PatchGuard Bypass 歷史

1. **計時攻擊**：PatchGuard 是定期檢查，不是即時的。修改核心 → 做壞事 → 還原核心，在 PatchGuard 下次檢查前完成
2. **Hook PatchGuard 本身**：在 PatchGuard 檢查前把 Hook 移除（極其複雜）
3. **BYOVD + 禁用 PatchGuard**（現在主流）：載入漏洞驅動，禁用 PatchGuard 的定期器

## 對 Driver 開發者的影響

1. **不能直接 Patch 核心代碼**（SSDT Hook、Inline Hook）：觸發 KPP 崩潰
2. **不能修改 IDT**：觸發 KPP
3. **Minifilter / Callback 是合法的攔截方式**：透過官方 API 而非直接 Patch

EDR 廠商必須在不觸發 KPP 的前提下實現監控，這就是為什麼 Ch 31（Kernel Callbacks）如此重要。

## 在 WinDbg 觀察 PatchGuard

```
kd> bp nt!KeBugCheckEx ".echo PatchGuard triggered!; kb; g"
← 設置一個「觀察但不停住」的斷點在 KeBugCheckEx
← 如果 PatchGuard 觸發，打印 call stack 後繼續

kd> x nt!KiVerifyXcptRoutine*   ← 找 PatchGuard 相關函式（名稱是刻意模糊的）
```

PatchGuard 的代碼被刻意混淆，函式名無意義，執行時解壓縮自身。這是反分析設計。

## 自我檢核

- [ ] DSE：64 位元 Windows 要求驅動有效數位簽章，`STATUS_INVALID_IMAGE_HASH` 是載入失敗的錯誤碼
- [ ] Test Signing Mode 允許自簽名驅動（桌面浮水印）
- [ ] BYOVD：帶有漏洞的**已簽名**驅動，利用其漏洞達到 DSE bypass
- [ ] PatchGuard 保護：核心代碼 Hash、SSDT、IDT、GDT、MSR（定期驗證，不是即時）
- [ ] Bugcheck `0x109` = PatchGuard 發現結構被修改
- [ ] PatchGuard **不保護** EPROCESS 大部分欄位，Token 竊取不觸發 KPP

→ [Ch 25 Token、Privilege 與 ACL](./25-token-acl.md)
