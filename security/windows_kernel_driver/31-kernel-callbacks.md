# Ch 31 — 核心回調機制

> 目標：掌握 Windows 核心回調 API（PsSetCreateProcessNotifyRoutine、PsSetCreateThreadNotifyRoutine、ObRegisterCallbacks），理解 EDR 怎麼用它們來監控進程和 Handle 操作。

## 核心回調總覽

Windows 核心提供一組「觀察點」——特定事件發生時，核心會主動呼叫所有已注冊的回調。

```
事件                     回調 API
─────────────────────────────────────────────────────────
進程建立/終止         PsSetCreateProcessNotifyRoutineEx
執行緒建立/終止       PsSetCreateThreadNotifyRoutine
映像載入（DLL/EXE）   PsSetLoadImageNotifyRoutine
物件 Handle 操作      ObRegisterCallbacks（OpenProcess 攔截）
登錄操作              CmRegisterCallback
```

這些是 EDR 的核心感測器。進程注入、隱藏進程、LSASS dump——都繞不開它們。

## PsSetCreateProcessNotifyRoutineEx

### 概念

每次 `NtCreateProcess` / `NtCreateUserProcess` 建立新進程（以及進程退出）時，核心遍歷回調陣列並呼叫所有已注冊的函式。

```c
// 回調函式簽名（Ex 版本）
VOID ProcessNotifyCallbackEx(
    PEPROCESS Process,
    HANDLE    ProcessId,
    PPS_CREATE_NOTIFY_INFO CreateInfo  // NULL = 進程終止
);

// CreateInfo 欄位（非 NULL = 進程建立）
typedef struct _PS_CREATE_NOTIFY_INFO {
    SIZE_T              Size;
    union { ULONG Flags; struct { ... } };
    HANDLE              ParentProcessId;
    CLIENT_ID           CreatingThreadId;
    struct _FILE_OBJECT *FileObject;
    PCUNICODE_STRING    ImageFileName;  // 完整路徑（可 NULL）
    PCUNICODE_STRING    CommandLine;    // 命令列（可 NULL）
    NTSTATUS            CreationStatus; // 可以設這個拒絕建立！
} PS_CREATE_NOTIFY_INFO;
```

### 攔截並阻止進程建立

```c
#include <ntddk.h>

VOID OnProcessNotify(
    PEPROCESS Process,
    HANDLE    ProcessId,
    PPS_CREATE_NOTIFY_INFO CreateInfo)
{
    UNREFERENCED_PARAMETER(Process);

    if (CreateInfo == NULL) {
        // 進程退出
        DbgPrint("[PROC] PID %llu exited\n", (ULONG64)ProcessId);
        return;
    }

    // 進程建立
    if (CreateInfo->ImageFileName) {
        DbgPrint("[PROC] Creating PID %llu: %wZ (cmd: %wZ)\n",
                 (ULONG64)ProcessId,
                 CreateInfo->ImageFileName,
                 CreateInfo->CommandLine ? CreateInfo->CommandLine : NULL);
    }

    // 阻止特定進程
    // 注意：只能在 CreateInfo 非 NULL 時設定 CreationStatus
    if (CreateInfo->ImageFileName) {
        // 簡單字串比對（實務上應該更嚴謹）
        static const WCHAR blocked[] = L"\\mimikatz.exe";
        UNICODE_STRING blockedStr;
        RtlInitUnicodeString(&blockedStr, blocked);
        
        if (RtlSuffixUnicodeString(&blockedStr, CreateInfo->ImageFileName, TRUE)) {
            DbgPrint("[PROC] Blocking mimikatz!\n");
            CreateInfo->CreationStatus = STATUS_ACCESS_DENIED;
        }
    }
}

// DriverEntry 中注冊
NTSTATUS status = PsSetCreateProcessNotifyRoutineEx(OnProcessNotify, FALSE);
// FALSE = 注冊；TRUE = 取消注冊

// DriverUnload 中取消注冊（否則 BSOD！）
PsSetCreateProcessNotifyRoutineEx(OnProcessNotify, TRUE);
```

**重要**：回調在建立進程的執行緒上下文執行，IRQL = PASSIVE_LEVEL。可以等待（Wait），但不要做耗時操作——拖慢整個系統。

## PsSetCreateThreadNotifyRoutine

```c
// 執行緒回調：每次執行緒建立 / 終止
VOID OnThreadNotify(
    HANDLE ProcessId,
    HANDLE ThreadId,
    BOOLEAN Create)  // TRUE = 建立，FALSE = 終止
{
    if (Create) {
        // 偵測進程注入：執行緒建立在「不是當前進程」的 ProcessId
        HANDLE currentPid = PsGetCurrentProcessId();
        if (ProcessId != currentPid) {
            DbgPrint("[THREAD] Remote thread: PID %llu created thread in PID %llu\n",
                     (ULONG64)currentPid, (ULONG64)ProcessId);
            // 可能是 CreateRemoteThread 注入！
        }
    }
}

PsSetCreateThreadNotifyRoutine(OnThreadNotify);
// 取消注冊：
PsRemoveCreateThreadNotifyRoutine(OnThreadNotify);
```

## PsSetLoadImageNotifyRoutine

```c
// 映像（EXE/DLL）載入回調
// 每次 DLL 被 map 到進程位址空間時觸發
VOID OnLoadImage(
    PUNICODE_STRING FullImageName,  // 可能 NULL
    HANDLE          ProcessId,
    PIMAGE_INFO     ImageInfo)
{
    // ImageInfo->ImageBase = 載入基址
    // ImageInfo->ImageSize = 映像大小
    // ImageInfo->SystemModeImage = TRUE 表示核心映像
    
    if (FullImageName && !ImageInfo->SystemModeImage) {
        DbgPrint("[IMG] PID %llu loaded: %wZ at %p\n",
                 (ULONG64)ProcessId,
                 FullImageName,
                 ImageInfo->ImageBase);
    }
}

PsSetLoadImageNotifyRoutine(OnLoadImage);
// 取消：
PsRemoveLoadImageNotifyRoutine(OnLoadImage);
```

## ObRegisterCallbacks — Handle 操作攔截

這是最強的回調：攔截 `OpenProcess` / `OpenThread`，可以**降低呼叫者拿到的 Access Mask**。

EDR 用這個保護 LSASS（`MiniDumpWriteDump` 需要 `PROCESS_VM_READ`）。

### 注冊結構

```c
#include <ntddk.h>

OB_PREOP_CALLBACK_STATUS OnPreOpenProcess(
    PVOID RegistrationContext,
    POB_PRE_OPERATION_INFORMATION OperationInfo)
{
    UNREFERENCED_PARAMETER(RegistrationContext);

    if (OperationInfo->ObjectType != *PsProcessType)
        return OB_PREOP_SUCCESS;

    // 取得目標進程
    PEPROCESS targetProcess = (PEPROCESS)OperationInfo->Object;
    HANDLE    targetPid     = PsGetProcessId(targetProcess);

    // 保護特定 PID（例如 lsass.exe）
    if (targetPid == gProtectedPid) {
        // 從 Access Mask 移除危險權限
        if (OperationInfo->Operation == OB_OPERATION_HANDLE_CREATE) {
            OperationInfo->Parameters->CreateHandleInformation.DesiredAccess &=
                ~(PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_CREATE_THREAD);
        } else {  // OB_OPERATION_HANDLE_DUPLICATE
            OperationInfo->Parameters->DuplicateHandleInformation.DesiredAccess &=
                ~(PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_CREATE_THREAD);
        }
        DbgPrint("[OB] Stripped dangerous access from handle to PID %llu\n",
                 (ULONG64)targetPid);
    }

    return OB_PREOP_SUCCESS;
}

// 注冊
OB_CALLBACK_REGISTRATION reg   = { 0 };
OB_OPERATION_REGISTRATION ops  = { 0 };
UNICODE_STRING             altitude;

RtlInitUnicodeString(&altitude, L"360123");  // 測試用 Altitude（任意數字）

ops.ObjectType         = PsProcessType;
ops.Operations         = OB_OPERATION_HANDLE_CREATE | OB_OPERATION_HANDLE_DUPLICATE;
ops.PreOperation       = OnPreOpenProcess;
ops.PostOperation      = NULL;

reg.Version            = OB_FLT_REGISTRATION_VERSION;
reg.OperationRegistrationCount = 1;
reg.Altitude           = altitude;
reg.RegistrationContext = NULL;
reg.OperationRegistration = &ops;

PVOID gObHandle = NULL;
NTSTATUS status = ObRegisterCallbacks(&reg, &gObHandle);

// DriverUnload：
if (gObHandle) {
    ObUnRegisterCallbacks(gObHandle);
    gObHandle = NULL;
}
```

**限制**：ObRegisterCallbacks 要求驅動有有效的 Code Integrity（強制代碼簽章）。測試機上開 Test Signing 才能通過。

## CmRegisterCallback — 登錄監控

```c
// 登錄回調（簡化版）
NTSTATUS OnRegistryCallback(
    PVOID CallbackContext,
    PVOID Argument1,
    PVOID Argument2)
{
    REG_NOTIFY_CLASS notifyClass = (REG_NOTIFY_CLASS)(ULONG_PTR)Argument1;

    if (notifyClass == RegNtPreSetValueKey) {
        PREG_SET_VALUE_KEY_INFORMATION info = 
            (PREG_SET_VALUE_KEY_INFORMATION)Argument2;
        
        DbgPrint("[REG] SetValue: %wZ\n", info->ValueName);
        
        // 阻止寫入：return STATUS_ACCESS_DENIED
    }

    return STATUS_SUCCESS;
}

LARGE_INTEGER cookie;
CmRegisterCallback(OnRegistryCallback, NULL, &cookie);

// 取消：
CmUnRegisterCallback(cookie);
```

## 回調陣列的限制

Windows 對每種回調類型有上限：

| 回調類型 | 最大數量 |
|---------|---------|
| Process notify | 64 |
| Thread notify | 64 |
| Load image notify | 64 |
| Ob callbacks | 無文件記載的上限 |

超過上限 → `STATUS_INSUFFICIENT_RESOURCES`。

## Anti-EDR 視角：繞過回調

EDR 依賴這些回調監控。繞過方式（見 Ch 39 詳述）：

1. **直接移除回調**：找回調陣列地址，清空 EDR 的函式指針
2. **KernelCallback Patch**：修改 `PspCreateProcessNotifyRoutine` 陣列
3. **繞過觸發點**：
   - 不用 `CreateProcess` → 直接呼叫 `NtCreateUserProcess` 變體
   - `NtCreateSection` + `NtMapViewOfSection` + 直接建立 ThreadContext 繞過 notify
4. **PPL（Protected Process Light）**：如果攻擊者取得 PPL，某些回調不觸發

現實中 EDR 知道自己的回調可能被移除，會在 Ring 3 同步。

## 自我檢核

- [ ] `PsSetCreateProcessNotifyRoutineEx`：建立時 `CreateInfo` 非 NULL，可設 `CreationStatus` 拒絕
- [ ] `PsSetCreateThreadNotifyRoutine`：遠端執行緒建立（注入偵測）= ProcessId ≠ 當前 PID
- [ ] `ObRegisterCallbacks`：Pre-Op 降低 Access Mask → 保護 lsass PROCESS_VM_READ
- [ ] 所有回調 **必須在 DriverUnload 取消**，否則核心留著野指針 → BSOD
- [ ] Anti-EDR：直接修改 `PspCreateProcessNotifyRoutine` 陣列清空回調

→ [Ch 32 DKOM 與進程隱藏](./32-dkom-process-hiding.md)
