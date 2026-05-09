# Ch 36 — ETW 核心追蹤

> 目標：理解 ETW（Event Tracing for Windows）的核心層架構，掌握如何在驅動中發送事件，以及 EDR 怎麼用 ETW 感測核心行為——還有攻擊者怎麼破壞 ETW 感測。

## ETW 架構

ETW 是 Windows 的高效能事件追蹤框架。分三個角色：

```
Provider（發送者）                    Consumer（接收者）
─────────────────                    ───────────────────
你的驅動                              WPP Trace viewer
ntoskrnl（內建 provider）             Logman / Tracerpt
win32k.sys                           WinDbg !wmitrace
Microsoft-Windows-Kernel-Process     EDR（Sysmon、Defender）
Microsoft-Windows-Kernel-File        PerfView / WPA
         ↓                                  ↑
         └─────────── ETW Session ──────────┘
                      (Kernel Logger 等)
```

ETW 提供了**環形緩衝區**設計：Provider 寫入事件到核心緩衝區，Consumer 非同步讀取，極低 overhead（即使 Consumer 不存在，Provider 的 `Write` 只是一個條件分支）。

## 使用 WPP Tracing（驅動最常用）

WPP（Windows Pre-Processor）是對 ETW 的宏封裝，在 Build Time 生成 trace 代碼。

### 步驟 1：定義 Provider 和事件（.h 檔）

```c
// trace.h — 手寫或由 tracewpp 工具生成
// Provider GUID 由你決定（一個獨一無二的 GUID）
// {A1B2C3D4-1234-5678-ABCD-123456789ABC}
#define WPP_CONTROL_GUIDS \
    WPP_DEFINE_CONTROL_GUID(DriverTraceGuid, \
        (A1B2C3D4,1234,5678,AB,CD,12,34,56,78,9A,BC), \
        WPP_DEFINE_BIT(TRACE_INIT)   \
        WPP_DEFINE_BIT(TRACE_IOCTL)  \
        WPP_DEFINE_BIT(TRACE_ERROR)  \
    )

// 定義 trace 宏
#define TraceInit(fmt, ...)  \
    WPP_SFENTRY1(WPP_BIT_TRACE_INIT, , fmt, __VA_ARGS__)
#define TraceIoctl(fmt, ...) \
    WPP_SFENTRY1(WPP_BIT_TRACE_IOCTL, , fmt, __VA_ARGS__)
#define TraceError(fmt, ...) \
    WPP_SFENTRY1(WPP_BIT_TRACE_ERROR, , fmt, __VA_ARGS__)
```

### 步驟 2：在代碼中使用

```c
// DriverEntry.c
#include "trace.h"
#include "DriverEntry.tmh"  // WPP 生成的標頭

NTSTATUS DriverEntry(PDRIVER_OBJECT DriverObj, PUNICODE_STRING RegistryPath)
{
    // 啟動 WPP Tracing
    WPP_INIT_TRACING(DriverObj, RegistryPath);
    
    TraceInit("DriverEntry started, BuildDate=%s", __DATE__);
    
    // ... 驅動初始化 ...
    
    TraceInit("DriverEntry complete, status=0x%08X", status);
    return status;
}

VOID DriverUnload(PDRIVER_OBJECT DriverObj)
{
    TraceInit("DriverUnload called");
    
    // 停止 WPP Tracing（必須在 Unload 中呼叫）
    WPP_CLEANUP(DriverObj);
}
```

### 步驟 3：收集 Trace

```powershell
# 開始收集（用 GUID 指定 Provider）
logman start MyTrace -p {A1B2C3D4-1234-5678-ABCD-123456789ABC} -o C:\trace.etl -ets

# 停止
logman stop MyTrace -ets

# 轉換成可讀格式（需要 PDB 和 tmf 文件）
tracefmt C:\trace.etl -pdb MyDriver.pdb -o C:\trace.txt
```

## ETW Provider 直接 API（不用 WPP）

對更複雜的事件模型，可以直接使用 ETW 核心 API：

```c
#include <ntddk.h>
#include <wmilib.h>  // 或直接用 EtwWrite

// 定義 Event Descriptor
static const EVENT_DESCRIPTOR ProcessEvent = {
    .Id      = 1,     // Event ID
    .Version = 0,
    .Channel = 0,
    .Level   = TRACE_LEVEL_INFORMATION,
    .Opcode  = 0,
    .Task    = 0,
    .Keyword = 0x1
};

// Provider 注冊 Handle
REGHANDLE gEtwHandle = 0;

// Provider GUID
static const GUID ProviderGuid = {
    0xa1b2c3d4, 0x1234, 0x5678,
    {0xab, 0xcd, 0x12, 0x34, 0x56, 0x78, 0x9a, 0xbc}
};

// 注冊 Provider
EtwRegister(&ProviderGuid, NULL, NULL, &gEtwHandle);

// 發送事件（IRQL < DISPATCH_LEVEL）
void LogProcessCreation(HANDLE pid, PUNICODE_STRING imageName)
{
    EVENT_DATA_DESCRIPTOR desc[2];
    
    ULONG64 pidVal = (ULONG64)pid;
    EventDataDescCreate(&desc[0], &pidVal, sizeof(ULONG64));
    EventDataDescCreate(&desc[1],
                        imageName->Buffer,
                        imageName->Length);
    
    EtwWrite(gEtwHandle, &ProcessEvent, NULL, 2, desc);
}

// DriverUnload
EtwUnregister(gEtwHandle);
```

## 內建 ETW Provider：Microsoft-Windows-Kernel-Process

Windows 核心有大量內建的 ETW Provider，不需要額外驅動就能監聽。

```powershell
# 列出所有可用 Provider
logman query providers

# 監聽核心進程事件
logman start KernelTrace \
    -p "Microsoft-Windows-Kernel-Process" 0x10 \
    -o C:\kernel.etl -ets

# 監聽核心檔案 I/O
logman start FileTrace \
    -p "Microsoft-Windows-Kernel-File" 0xFF \
    -o C:\file.etl -ets
```

重要的內建 Provider：

| Provider | 事件 |
|---------|------|
| `Microsoft-Windows-Kernel-Process` | 進程/執行緒建立終止 |
| `Microsoft-Windows-Kernel-File` | 檔案操作（Create/Read/Write/Delete） |
| `Microsoft-Windows-Kernel-Registry` | 登錄操作 |
| `Microsoft-Windows-Kernel-Network` | 網路連線（比 WFP 更高層） |
| `Microsoft-Windows-Security-Auditing` | 安全稽核（需要特殊權限） |

## EDR 如何用 ETW

現代 EDR（Defender、CrowdStrike 等）的核心感測器組合：

```
PsSetCreateProcessNotifyRoutineEx  ← 進程建立（可拒絕）
PsSetLoadImageNotifyRoutine        ← DLL 載入
ObRegisterCallbacks                ← Handle 操作
ETW Microsoft-Windows-Kernel-*    ← 網路/檔案/登錄（更輕量）
Minifilter（FltMgr）               ← 檔案操作（可攔截）
WFP Callout                        ← 網路連線（可攔截）
```

ETW 的優點是**極低 overhead**，缺點是**攻擊者可以破壞**。

## 攻擊者如何破壞 ETW

### ETW Patch（移除事件寫入）

```c
// EtwWrite 在 ntoskrnl.exe 中是導出函式
// 找到它的地址，patch 成 ret 指令

PVOID etwWrite = MmGetSystemRoutineAddress(
    &(UNICODE_STRING)RTL_CONSTANT_STRING(L"EtwWrite"));

// CR0 關閉寫保護後（需要先繞過 SMEP/PatchGuard）
UCHAR retOpcode = 0xC3;  // RET
RtlCopyMemory(etwWrite, &retOpcode, 1);
// 現在所有 ETW 事件寫入都直接返回，不記錄任何事件
```

**風險**：這是核心代碼頁面修改，PatchGuard 監控 ntoskrnl 代碼完整性。雖然 EtwWrite 本身可能不在監控範圍，但代碼頁面修改仍然有 HVCI 阻止的風險。

### 針對特定 Provider 的 Session 破壞

每個 ETW Session（如 EDR 的 WDSecurityEvents）有一個 Buffer Chain。攻擊者可以：
1. 找到 Session 的 `ETW_GUID_ENTRY`
2. 修改其 `SessionContext`，讓事件都丟棄
3. 或者直接關閉 Session Handle

實際上比 patch EtwWrite 更精準，只破壞特定 EDR 的 Session。

### 繞過（不破壞）

更隱蔽的方式：不破壞 ETW，而是讓操作本身不觸發 ETW 事件：

- 用 `NtMapViewOfSection` + 直接操作記憶體注入，而非 `NtCreateRemoteThread`（後者觸發 Thread Create 事件）
- 用 `NtQueueApcThread` 執行代碼，不建立新執行緒

## 自我檢核

- [ ] WPP Tracing：`WPP_INIT_TRACING` / `WPP_CLEANUP`；在 Build Time 生成 trace 宏
- [ ] `EtwWrite(handle, descriptor, NULL, count, dataDesc[])`：直接發送 ETW 事件
- [ ] EDR 用多種感測器組合：Callbacks + ETW + Minifilter + WFP
- [ ] ETW Patch：把 `EtwWrite` 第一個位元組改成 `0xC3`（ret）→ 所有事件寫入靜默
- [ ] 更隱蔽：不建立遠端執行緒，用 APC/Section Map 規避執行緒建立事件

→ [Ch 37 PatchGuard 深入](./37-patchguard-deep.md)
