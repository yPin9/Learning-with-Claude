# Ch 20 — Minifilter Driver

> 目標：掌握 FltMgr 框架的架構，能實作一個攔截檔案建立操作並記錄路徑的 Minifilter Driver。

## FltMgr：過濾管理員

Windows 用 **Filter Manager（fltmgr.sys）** 統一管理所有檔案系統過濾驅動。

FltMgr 自己插在 File System 的 Device Stack 上，所有 Minifilter 只和 FltMgr 溝通，不直接插 Stack：

```
用戶態 CreateFile()
    ↓
I/O Manager
    ↓
FltMgr（fltmgr.sys）← 統一入口
    ├── Minifilter A（Altitude 360000）← 按 Altitude 排序
    ├── Minifilter B（Altitude 320000）
    └── Minifilter C（Altitude 280000）
    ↓
File System（NTFS）
    ↓
磁碟驅動
```

**Altitude（海拔）**：每個 Minifilter 有一個 Altitude 數字，決定過濾順序。數字越大，攔截越早（越靠近用戶端）。

微軟維護了一個 Altitude 分配列表，防衝突。開發測試用 `360000`（備份過濾範圍）。

## Minifilter 骨架

```c
#include <fltKernel.h>

// Minifilter 實例
PFLT_FILTER   gFilterHandle = NULL;

// Callback 前向宣告
FLT_PREOP_CALLBACK_STATUS PreCreate(
    PFLT_CALLBACK_DATA Data,
    PCFLT_RELATED_OBJECTS FltObjects,
    PVOID* CompletionContext);

FLT_POSTOP_CALLBACK_STATUS PostCreate(
    PFLT_CALLBACK_DATA Data,
    PCFLT_RELATED_OBJECTS FltObjects,
    PVOID CompletionContext,
    FLT_POST_OPERATION_FLAGS Flags);

// 定義過濾哪些操作
const FLT_OPERATION_REGISTRATION Callbacks[] = {
    {
        IRP_MJ_CREATE,          // 過濾 CreateFile
        0,
        PreCreate,              // Pre-operation callback（在操作前）
        PostCreate              // Post-operation callback（在操作後）
    },
    { IRP_MJ_OPERATION_END }    // 陣列結尾標記
};

// 驅動和 FltMgr 的連接設定
const FLT_REGISTRATION FilterRegistration = {
    sizeof(FLT_REGISTRATION),   // Size
    FLT_REGISTRATION_VERSION,   // Version
    0,                          // Flags
    NULL,                       // ContextRegistration（使用 Context 時填）
    Callbacks,                  // OperationRegistration
    FilterUnload,               // FilterUnloadCallback
    NULL,                       // InstanceSetupCallback
    NULL,                       // InstanceQueryTeardownCallback
    NULL,                       // InstanceTeardownStartCallback
    NULL,                       // InstanceTeardownCompleteCallback
};

NTSTATUS DriverEntry(PDRIVER_OBJECT DriverObject, PUNICODE_STRING RegistryPath)
{
    NTSTATUS status;
    
    // 向 FltMgr 註冊
    status = FltRegisterFilter(DriverObject, &FilterRegistration, &gFilterHandle);
    if (!NT_SUCCESS(status)) return status;
    
    // 開始過濾（開始接收 callback）
    status = FltStartFiltering(gFilterHandle);
    if (!NT_SUCCESS(status)) {
        FltUnregisterFilter(gFilterHandle);
        return status;
    }
    
    return STATUS_SUCCESS;
}

NTSTATUS FilterUnload(FLT_FILTER_UNLOAD_FLAGS Flags)
{
    UNREFERENCED_PARAMETER(Flags);
    FltUnregisterFilter(gFilterHandle);
    return STATUS_SUCCESS;
}
```

## Pre-Operation Callback：在操作前攔截

```c
FLT_PREOP_CALLBACK_STATUS PreCreate(
    PFLT_CALLBACK_DATA    Data,
    PCFLT_RELATED_OBJECTS FltObjects,
    PVOID*                CompletionContext)
{
    UNREFERENCED_PARAMETER(FltObjects);
    *CompletionContext = NULL;

    // 忽略核心發出的 Create（只關心用戶態）
    if (Data->RequestorMode == KernelMode) {
        return FLT_PREOP_SUCCESS_NO_CALLBACK;  // 不呼叫 PostCreate
    }

    // 取得要開啟的檔案名稱
    PFLT_FILE_NAME_INFORMATION nameInfo;
    NTSTATUS status = FltGetFileNameInformation(
        Data,
        FLT_FILE_NAME_NORMALIZED | FLT_FILE_NAME_QUERY_DEFAULT,
        &nameInfo);
    
    if (!NT_SUCCESS(status)) {
        return FLT_PREOP_SUCCESS_WITH_CALLBACK;
    }

    // 解析名稱
    FltParseFileNameInformation(nameInfo);
    
    DbgPrint("[MiniFilter] Pre-Create: %wZ\n", &nameInfo->Name);
    
    // 可以在這裡攔截：return FLT_PREOP_COMPLETE 阻止操作
    // Data->IoStatus.Status = STATUS_ACCESS_DENIED;
    // return FLT_PREOP_COMPLETE;

    FltReleaseFileNameInformation(nameInfo);
    
    return FLT_PREOP_SUCCESS_WITH_CALLBACK;  // 繼續操作，並呼叫 PostCreate
}
```

### Pre-Operation 返回值

| 返回值 | 含義 |
|--------|------|
| `FLT_PREOP_SUCCESS_WITH_CALLBACK` | 繼續，完成後呼叫 PostOp |
| `FLT_PREOP_SUCCESS_NO_CALLBACK` | 繼續，但不呼叫 PostOp（更快）|
| `FLT_PREOP_COMPLETE` | 阻止操作，直接完成（用 `Data->IoStatus` 設結果）|
| `FLT_PREOP_PENDING` | 非同步處理，稍後呼叫 `FltCompletePendedPreOperation` |

## Post-Operation Callback：在操作完成後

```c
FLT_POSTOP_CALLBACK_STATUS PostCreate(
    PFLT_CALLBACK_DATA    Data,
    PCFLT_RELATED_OBJECTS FltObjects,
    PVOID                 CompletionContext,
    FLT_POST_OPERATION_FLAGS Flags)
{
    UNREFERENCED_PARAMETER(CompletionContext);
    
    // Flags 包含 FLTFL_POST_OPERATION_DRAINING 表示驅動正在卸載
    if (FLT_IS_IRP_OPERATION(Data) == FALSE) {
        return FLT_POSTOP_FINISHED_PROCESSING;
    }

    // 操作成功才記錄
    if (!NT_SUCCESS(Data->IoStatus.Status)) {
        return FLT_POSTOP_FINISHED_PROCESSING;
    }

    // 取得成功開啟的檔案名
    PFLT_FILE_NAME_INFORMATION nameInfo;
    if (NT_SUCCESS(FltGetFileNameInformation(Data,
        FLT_FILE_NAME_NORMALIZED | FLT_FILE_NAME_QUERY_DEFAULT,
        &nameInfo))) {
        FltParseFileNameInformation(nameInfo);
        DbgPrint("[MiniFilter] File opened: %wZ\n", &nameInfo->Name);
        FltReleaseFileNameInformation(nameInfo);
    }

    return FLT_POSTOP_FINISHED_PROCESSING;
}
```

## Minifilter Context

Context 讓你在不同 callback 之間傳遞資訊，或把資訊附加在 File / Instance / Volume 上：

```c
// 定義 Stream Context（附在 File Object 上）
typedef struct _MY_STREAM_CONTEXT {
    ULONG     AccessCount;
    BOOLEAN   IsMonitored;
} MY_STREAM_CONTEXT, *PMY_STREAM_CONTEXT;

// Context 定義
const FLT_CONTEXT_REGISTRATION ContextRegistration[] = {
    {
        FLT_STREAM_CONTEXT,          // 附在 Stream（File Object）上
        0,                           // Flags
        NULL,                        // ContextCleanupCallback
        sizeof(MY_STREAM_CONTEXT),   // Size
        'CtxM'                       // Pool Tag
    },
    { FLT_CONTEXT_END }
};

// 在 Pre-Create 中分配 Context
PMY_STREAM_CONTEXT ctx = NULL;
FltAllocateContext(gFilterHandle, FLT_STREAM_CONTEXT, 
                   sizeof(MY_STREAM_CONTEXT), PagedPool, (PFLT_CONTEXT*)&ctx);
if (ctx) {
    ctx->AccessCount = 0;
    ctx->IsMonitored = TRUE;
    FltSetStreamContext(FltObjects->Instance, FltObjects->FileObject,
                        FLT_SET_CONTEXT_REPLACE_IF_EXISTS, ctx, NULL);
    FltReleaseContext(ctx);  // 設置後釋放我們的參考
}

// 在 Post-Create 中取回
PMY_STREAM_CONTEXT ctx = NULL;
FltGetStreamContext(FltObjects->Instance, FltObjects->FileObject, (PFLT_CONTEXT*)&ctx);
if (ctx) {
    ctx->AccessCount++;
    FltReleaseContext(ctx);  // 必須釋放
}
```

## 安裝 Minifilter

Minifilter 需要 INF 檔案，或在 Registry 手動設定：

```
HKLM\SYSTEM\CurrentControlSet\Services\MyMinifilter
  Type       = 2           (SERVICE_FILE_SYSTEM_DRIVER)
  Start      = 3           (SERVICE_DEMAND_START)
  ImagePath  = System32\drivers\MyMinifilter.sys

HKLM\SYSTEM\CurrentControlSet\Services\MyMinifilter\Instances
  DefaultInstance = "MyMinifilter Instance"

HKLM\SYSTEM\CurrentControlSet\Services\MyMinifilter\Instances\MyMinifilter Instance
  Altitude = "360000"
  Flags    = 0
```

啟動：
```powershell
fltMC load MyMinifilter    # 載入
fltMC unload MyMinifilter  # 卸載
fltMC query                # 查看所有 Minifilter 和 Altitude
```

## 自我檢核

- [ ] Minifilter 透過 FltMgr 間接過濾，不直接插 Device Stack
- [ ] Altitude 決定過濾順序，數字大 = 先攔截（接近用戶態）
- [ ] `FltRegisterFilter` → `FltStartFiltering` 的順序
- [ ] Pre-Op 返回 `FLT_PREOP_COMPLETE` 可以阻止操作（存取控制）
- [ ] `FltGetFileNameInformation` + `FltParseFileNameInformation` 取得路徑
- [ ] Context 管理：`FltAllocateContext`/`FltSetStreamContext`/`FltReleaseContext` 必須配對

→ [Ch 21 WinDbg 核心調試](./21-windbg.md)
