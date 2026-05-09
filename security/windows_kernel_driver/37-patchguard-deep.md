# Ch 37 — PatchGuard 深入

> 目標：理解 PatchGuard（KPP）的工作原理、保護範圍、觸發機制，以及為什麼現代繞過越來越難。

## PatchGuard 是什麼

PatchGuard（Kernel Patch Protection，KPP）是 Windows x64 Vista 引入的機制，定期驗證核心關鍵資料結構的完整性。

**設計目標**：不是阻止攻擊者修改（有 Ring 0 的人能做任何事），而是**事後偵測**並強制 Bugcheck（0x109）。

對比 HVCI（VBS）：
- PatchGuard：在同一個特權級（Ring 0）自我檢查，可以被有 Ring 0 的攻擊者繞過
- HVCI：在 Ring -1（Hypervisor）驗證，攻擊者的 Ring 0 無法欺騙

## 保護的範圍

PatchGuard **保護**：

| 對象 | 說明 |
|-----|-----|
| SSDT（System Service Descriptor Table）| Inline hook 或表替換 |
| IDT（Interrupt Descriptor Table）| IDT hook |
| GDT（Global Descriptor Table）| 新 Segment Descriptor 插入 |
| MSR（Model Specific Registers）| `LSTAR`（syscall handler）等 |
| `ntoskrnl.exe` 代碼頁面 | Inline patch |
| `win32k.sys` 代碼頁面 | 同上 |
| `hal.dll` 代碼頁面 | 同上 |
| `ntfs.sys` 等核心驅動 | 選擇性保護 |

PatchGuard **不保護**（重要！）：

| 對象 | 說明 |
|-----|-----|
| EPROCESS.Token | Token 竊取安全繞過 |
| EPROCESS.ActiveProcessLinks | DKOM 隱藏進程安全 |
| DRIVER_OBJECT.MajorFunction | 部分驅動 IRP hook |
| HalDispatchTable（Data Section） | 舊版 exploit 的目標 |
| 用戶自己驅動的代碼 | 你改自己的代碼 KPP 不管 |

## 工作機制（推測，非公開文件）

PatchGuard 的實作是混淆的，研究者從逆向分析得出以下理解：

```
KPP 初始化（Boot 時）：
1. 複製需要保護的資料到加密的「快照」（snapshot）
2. 在隨機偏移後開始第一次驗證計時器

定期驗證（通常每隔 5-10 分鐘，但時間隨機）：
1. 解密快照
2. 比較當前核心狀態與快照
3. 如果不一致 → KeBugCheckEx(0x109, ...)
```

為了對抗靜態分析，KPP 代碼：
- 被 **XOR 加密**存放在 ntoskrnl 的特定 Section
- 用 **多個分散的執行路徑**到達同一個驗證邏輯
- 定時器的間隔**隨機化**（避免攻擊者精確計算窗口）
- 在某些 DPC 回調或 Timer 回調中執行

## Bugcheck 0x109 詳解

```
CRITICAL_STRUCTURE_CORRUPTION

Parameter 1：被保護結構的類型代碼
Parameter 2：保護的 CRC / 快照值
Parameter 3：實際讀到的值
Parameter 4：被修改的結構地址

常見 Parameter 1 值：
0x3 = 處理器 IDT
0x4 = 處理器 GDT
0x5 = 處理器 IDT Entry（Type/DPL 改變）
0x6 = MSR（如 LSTAR）
0x7 = SSDT（KiServiceTable）
0x8 = ntoskrnl 代碼 Section
0x101 = 大型結構完整性錯誤
```

## 歷史繞過技術

### 1. 計時器窗口（Timer Race）

思路：在 KPP 兩次驗證之間修改 → 使用 → 恢復。

問題：驗證間隔隨機（可能幾秒到幾分鐘），不可靠。

### 2. 破壞 KPP Context

找到 KPP 自身的工作結構，破壞它讓 KPP 無法啟動驗證。

問題：Windows 更新不斷改變 KPP 的混淆方式；現代版本分散了 KPP Context，找起來非常難。

### 3. 不觸碰保護範圍

**最可靠的方式**：根本不修改 KPP 保護的東西。

Token 竊取不修改 SSDT → KPP 不觸發。  
DKOM 修改 ActiveProcessLinks → KPP 不觸發。  
在自己驅動的 Pool 執行 Shellcode → KPP 不觸發。

現代核心 exploit 傾向於用「KPP 不保護的結構」達到目標，而不是嘗試繞過 KPP。

### 4. 虛擬機器層繞過（歷史案例）

在虛擬機器裡攔截 CPU 指令，讓 KPP 的驗證讀到「假的」正確快照。

問題：需要 Ring -1 存取（Hypervisor），現代 Windows + HVCI 更難。

## 現代 Windows 的 PatchGuard 強度

```
Windows 7（x64）：
    → KPP 相對固定，逆向後繞過案例較多

Windows 10 2004+：
    → KPP Context 更分散，混淆更強
    → 結合 HVCI（Ch 40）後，即使 Ring 0 也難繞過

Windows 11：
    → HVCI 預設開啟（符合需求的硬體）
    → PatchGuard 是備用防線（HVCI 掛掉時）
```

## WinDbg 查看 PatchGuard 活動

```
; 查 0x109 bugcheck 分析
kd> !analyze -v

; 直接看 KPP 相關的記憶體（研究用，混淆難直接查）
; 從 ntoskrnl PDB 搜 KiFilterFiberContext（已知 KPP 入口之一）
kd> x nt!KiFilterFiberContext
```

## 實際開發建議

**合法驅動開發者**：
- 不要 hook SSDT（有合法替代方案：Callbacks、Minifilter）
- 不要 Inline patch 核心函式（同上）
- 如果要監控進程：`PsSetCreateProcessNotifyRoutineEx`
- 如果要監控檔案：Minifilter
- 不要碰 IDT/GDT/MSR

**安全研究者**：
- 選擇不在 KPP 保護範圍的攻擊路徑（Token 竊取、DKOM、Pool 執行）
- 在 VM 裡測試（Bugcheck 0x109 會重啟）
- 關掉 VM 的 KPP 可以用 test mode + 特殊 boot 設定

## 自我檢核

- [ ] KPP 保護：SSDT、IDT、GDT、MSR、ntoskrnl 代碼頁面；**不保護** EPROCESS.Token、ActiveProcessLinks
- [ ] KPP 工作方式：Boot 時拍快照，定期（隨機間隔）比對，不一致 → Bugcheck 0x109
- [ ] Bugcheck 0x109 Parameter 1 = 0x7 = SSDT 被修改；0x8 = ntoskrnl 代碼頁面被修改
- [ ] 現代繞過策略：不碰 KPP 保護範圍，用 Token 竊取 / DKOM 等「安全區域」
- [ ] HVCI（Ch 40）比 KPP 更強：Ring -1 層驗證，Ring 0 無法欺騙

→ [Ch 38 EDR 驅動架構](./38-edr-driver-arch.md)
