# Final Project — Minifilter EDR 原型

> 目標：把這門課學到的所有技術整合起來，實作一個具備實際偵測能力的 EDR 原型驅動，包含 Minifilter + 核心回調 + WFP + IOCTL 通訊介面。

## 專案目標

設計並實作 **NanoEDR**：

```
功能：
  [核心感測器]
  ✓ 進程建立/終止監控（PsSetCreateProcessNotifyRoutineEx）
  ✓ DLL 載入監控（PsSetLoadImageNotifyRoutine）  
  ✓ 檔案操作監控（Minifilter：建立/讀/寫）
  ✓ 網路連線監控（WFP ALE_AUTH_CONNECT）
  ✓ Handle 保護（ObRegisterCallbacks，可選）

  [IOCTL 介面]
  ✓ 用戶態服務讀取事件（Ring Buffer）
  ✓ 設定規則（攔截特定路徑/IP）
  ✓ 查詢統計

  [用戶態服務]
  ✓ 讀取核心事件 → 輸出到控制台或日誌
  ✓ 簡單規則引擎（黑名單進程名稱）
```

## 專案結構

```
NanoEDR/
├── driver/
│   ├── NanoEDR.c           ← DriverEntry + IOCTL + 公共代碼
│   ├── callbacks.c         ← Process/Thread/Image 回調
│   ├── minifilter.c        ← Minifilter 實作
│   ├── wfp.c              ← WFP Callout
│   ├── ringbuf.c          ← 共享 Ring Buffer
│   ├── NanoEDR.h          ← 共用結構定義
│   └── NanoEDR.inf        ← INF 安裝檔
└── service/
    ├── main.c             ← 用戶態服務
    └── NanoEDR.h          ← 共用結構（拷貝一份）
```

## 共用資料結構

```c
// NanoEDR.h（驅動和服務共用）
#pragma once

#define NANO_EDR_DEVICE_NAME    L"\\Device\\NanoEDR"
#define NANO_EDR_SYMBOLIC_NAME  L"\\DosDevices\\NanoEDR"

// IOCTL Codes
#define NANO_IOCTL_READ_EVENTS  CTL_CODE(FILE_DEVICE_UNKNOWN, 0x801, \
                                          METHOD_BUFFERED, FILE_READ_ACCESS)
#define NANO_IOCTL_SET_RULE     CTL_CODE(FILE_DEVICE_UNKNOWN, 0x802, \
                                          METHOD_BUFFERED, FILE_WRITE_ACCESS)
#define NANO_IOCTL_GET_STATS    CTL_CODE(FILE_DEVICE_UNKNOWN, 0x803, \
                                          METHOD_BUFFERED, FILE_READ_ACCESS)

// 事件類型
typedef enum _NANO_EVENT_TYPE {
    EventProcessCreate  = 1,
    EventProcessExit    = 2,
    EventImageLoad      = 3,
    EventFileCreate     = 4,
    EventFileWrite      = 5,
    EventNetConnect     = 6,
} NANO_EVENT_TYPE;

// 事件結構（固定大小，適合 Ring Buffer）
typedef struct _NANO_EVENT {
    NANO_EVENT_TYPE Type;
    ULONG64         Timestamp;   // KeQuerySystemTime
    ULONG64         Pid;
    ULONG64         Tid;
    union {
        struct {
            WCHAR   ImagePath[260];
            WCHAR   CommandLine[512];
            ULONG64 ParentPid;
        } Process;
        struct {
            WCHAR   ImagePath[260];
            ULONG64 LoadBase;
        } Image;
        struct {
            WCHAR   FilePath[260];
            ULONG   CreateOptions;
        } File;
        struct {
            ULONG32 RemoteAddr;  // Network byte order
            ULONG16 RemotePort;
            ULONG16 Proto;       // IPPROTO_TCP / IPPROTO_UDP
        } Network;
    };
} NANO_EVENT, *PNANO_EVENT;

// Ring Buffer Header（放在共享記憶體或 IOCTL 輸出）
#define RING_BUFFER_CAPACITY 256

typedef struct _NANO_RING_BUFFER {
    volatile LONG Head;                   // 寫指針（核心遞增）
    volatile LONG Tail;                   // 讀指針（用戶態遞增）
    NANO_EVENT Events[RING_BUFFER_CAPACITY];
} NANO_RING_BUFFER;

// 規則結構
typedef struct _NANO_RULE {
    ULONG RuleType;  // 0 = 封鎖進程, 1 = 封鎖 IP
    union {
        WCHAR   BlockedProcess[260];
        ULONG32 BlockedRemoteAddr;
    };
} NANO_RULE;

// 統計
typedef struct _NANO_STATS {
    ULONG64 ProcessEvents;
    ULONG64 FileEvents;
    ULONG64 NetworkEvents;
    ULONG64 BlockedConnections;
    ULONG64 BlockedProcesses;
} NANO_STATS;
```

## 核心驅動：ringbuf.c

```c
// ringbuf.c — 無鎖 Ring Buffer（核心生產者，用戶消費者）
#include "NanoEDR.h"

static NANO_RING_BUFFER gRingBuf = { 0 };
static NANO_STATS       gStats   = { 0 };

// 推入事件（核心呼叫，可能在 PASSIVE 或 APC_LEVEL）
void RingPushEvent(PNANO_EVENT evt)
{
    LONG head = gRingBuf.Head;
    LONG next = (head + 1) % RING_BUFFER_CAPACITY;
    
    // 若已滿（next == Tail），丟棄最舊的
    if (next == gRingBuf.Tail) {
        InterlockedIncrement(&gRingBuf.Tail);
    }
    
    RtlCopyMemory(&gRingBuf.Events[head], evt, sizeof(NANO_EVENT));
    InterlockedExchange(&gRingBuf.Head, next);
}

// 拉取事件（IOCTL 讀取時呼叫）
ULONG RingPopEvents(PNANO_EVENT outBuf, ULONG maxCount)
{
    ULONG count = 0;
    while (count < maxCount) {
        LONG tail = gRingBuf.Tail;
        LONG head = gRingBuf.Head;
        if (tail == head) break;  // 空了
        
        RtlCopyMemory(&outBuf[count], &gRingBuf.Events[tail], sizeof(NANO_EVENT));
        InterlockedExchange(&gRingBuf.Tail, (tail + 1) % RING_BUFFER_CAPACITY);
        count++;
    }
    return count;
}
```

## 核心驅動：callbacks.c

```c
// callbacks.c
#include <ntddk.h>
#include "NanoEDR.h"

// 阻擋規則（簡化：一個黑名單進程名稱）
static WCHAR gBlockedProcess[260] = L"";

void SetBlockedProcess(PCWSTR name)
{
    RtlStringCchCopyW(gBlockedProcess, 260, name);
}

// 進程回調
VOID ProcessCallback(PEPROCESS Process, HANDLE Pid, PPS_CREATE_NOTIFY_INFO Info)
{
    NANO_EVENT evt = { 0 };
    evt.Pid       = (ULONG64)Pid;
    evt.Tid       = (ULONG64)PsGetCurrentThreadId();
    KeQuerySystemTime((PLARGE_INTEGER)&evt.Timestamp);

    if (Info) {
        evt.Type = EventProcessCreate;
        evt.Process.ParentPid = (ULONG64)Info->ParentProcessId;

        if (Info->ImageFileName)
            RtlStringCchCopyNW(evt.Process.ImagePath, 260,
                               Info->ImageFileName->Buffer,
                               Info->ImageFileName->Length / sizeof(WCHAR));
        if (Info->CommandLine)
            RtlStringCchCopyNW(evt.Process.CommandLine, 512,
                               Info->CommandLine->Buffer,
                               Info->CommandLine->Length / sizeof(WCHAR));

        // 黑名單檢查
        if (gBlockedProcess[0] &&
            wcsstr(evt.Process.ImagePath, gBlockedProcess) != NULL) {
            DbgPrint("[NanoEDR] Blocking process: %S\n", evt.Process.ImagePath);
            Info->CreationStatus = STATUS_ACCESS_DENIED;
            InterlockedIncrement64((LONG64*)&gStats.BlockedProcesses);
        }

        InterlockedIncrement64((LONG64*)&gStats.ProcessEvents);
    } else {
        evt.Type = EventProcessExit;
    }

    RingPushEvent(&evt);
    UNREFERENCED_PARAMETER(Process);
}

// 映像載入回調
VOID ImageCallback(PUNICODE_STRING FullImageName, HANDLE Pid, PIMAGE_INFO ImageInfo)
{
    if (ImageInfo->SystemModeImage) return;  // 跳過核心映像

    NANO_EVENT evt = { 0 };
    evt.Type      = EventImageLoad;
    evt.Pid       = (ULONG64)Pid;
    evt.Image.LoadBase = (ULONG64)ImageInfo->ImageBase;
    KeQuerySystemTime((PLARGE_INTEGER)&evt.Timestamp);

    if (FullImageName)
        RtlStringCchCopyNW(evt.Image.ImagePath, 260,
                           FullImageName->Buffer,
                           FullImageName->Length / sizeof(WCHAR));

    RingPushEvent(&evt);
}
```

## 核心驅動：minifilter.c

```c
// minifilter.c
#include <fltKernel.h>
#include "NanoEDR.h"

PFLT_FILTER gFltHandle = NULL;

FLT_PREOP_CALLBACK_STATUS PreCreateCallback(
    PFLT_CALLBACK_DATA    Data,
    PCFLT_RELATED_OBJECTS FltObjects,
    PVOID                *CompletionContext)
{
    UNREFERENCED_PARAMETER(FltObjects);
    UNREFERENCED_PARAMETER(CompletionContext);

    // 只記錄非系統進程的檔案建立
    HANDLE pid = PsGetCurrentProcessId();
    if ((ULONG64)pid <= 4) return FLT_PREOP_SUCCESS_NO_CALLBACK;

    FLT_FILE_NAME_INFORMATION *nameInfo;
    NTSTATUS status = FltGetFileNameInformation(
        Data,
        FLT_FILE_NAME_NORMALIZED | FLT_FILE_NAME_QUERY_DEFAULT,
        &nameInfo);

    if (NT_SUCCESS(status)) {
        FltParseFileNameInformation(nameInfo);

        NANO_EVENT evt = { 0 };
        evt.Type = EventFileCreate;
        evt.Pid  = (ULONG64)pid;
        KeQuerySystemTime((PLARGE_INTEGER)&evt.Timestamp);
        RtlStringCchCopyNW(evt.File.FilePath, 260,
                           nameInfo->Name.Buffer,
                           nameInfo->Name.Length / sizeof(WCHAR));
        evt.File.CreateOptions =
            Data->Iopb->Parameters.Create.Options & 0x00FFFFFF;

        RingPushEvent(&evt);
        InterlockedIncrement64((LONG64*)&gStats.FileEvents);

        FltReleaseFileNameInformation(nameInfo);
    }

    return FLT_PREOP_SUCCESS_NO_CALLBACK;
}

// FLT_OPERATION_REGISTRATION 陣列
static const FLT_OPERATION_REGISTRATION Callbacks[] = {
    { IRP_MJ_CREATE, 0, PreCreateCallback, NULL },
    { IRP_MJ_OPERATION_END }
};

static const FLT_REGISTRATION FilterRegistration = {
    sizeof(FLT_REGISTRATION),
    FLT_REGISTRATION_VERSION,
    0,
    NULL,  // Context Registration
    Callbacks,
    NanoEDRUnload,  // FilterUnloadCallback
    NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
};

NTSTATUS RegisterMinifilter(PDRIVER_OBJECT DriverObject)
{
    NTSTATUS status = FltRegisterFilter(DriverObject, &FilterRegistration, &gFltHandle);
    if (!NT_SUCCESS(status)) return status;

    return FltStartFiltering(gFltHandle);
}

VOID UnregisterMinifilter(void)
{
    if (gFltHandle) {
        FltUnregisterFilter(gFltHandle);
        gFltHandle = NULL;
    }
}
```

## 核心驅動：NanoEDR.c（主驅動）

```c
// NanoEDR.c
#include <ntddk.h>
#include <wdm.h>
#include "NanoEDR.h"

PDEVICE_OBJECT gDeviceObject = NULL;

NTSTATUS DispatchIoctl(PDEVICE_OBJECT DevObj, PIRP Irp)
{
    UNREFERENCED_PARAMETER(DevObj);
    PIO_STACK_LOCATION stack = IoGetCurrentIrpStackLocation(Irp);
    ULONG code = stack->Parameters.DeviceIoControl.IoControlCode;
    NTSTATUS status = STATUS_SUCCESS;
    ULONG_PTR info = 0;

    switch (code) {
    case NANO_IOCTL_READ_EVENTS: {
        PVOID outBuf = Irp->AssociatedIrp.SystemBuffer;
        ULONG outLen = stack->Parameters.DeviceIoControl.OutputBufferLength;
        ULONG maxEvents = outLen / sizeof(NANO_EVENT);
        
        if (maxEvents == 0) { status = STATUS_BUFFER_TOO_SMALL; break; }
        
        ULONG count = RingPopEvents((PNANO_EVENT)outBuf, maxEvents);
        info = count * sizeof(NANO_EVENT);
        break;
    }
    case NANO_IOCTL_SET_RULE: {
        PNANO_RULE rule = (PNANO_RULE)Irp->AssociatedIrp.SystemBuffer;
        if (stack->Parameters.DeviceIoControl.InputBufferLength < sizeof(NANO_RULE)) {
            status = STATUS_BUFFER_TOO_SMALL; break;
        }
        if (rule->RuleType == 0)
            SetBlockedProcess(rule->BlockedProcess);
        break;
    }
    case NANO_IOCTL_GET_STATS: {
        PVOID outBuf = Irp->AssociatedIrp.SystemBuffer;
        ULONG outLen = stack->Parameters.DeviceIoControl.OutputBufferLength;
        if (outLen < sizeof(NANO_STATS)) { status = STATUS_BUFFER_TOO_SMALL; break; }
        RtlCopyMemory(outBuf, &gStats, sizeof(NANO_STATS));
        info = sizeof(NANO_STATS);
        break;
    }
    default:
        status = STATUS_INVALID_DEVICE_REQUEST;
    }

    Irp->IoStatus.Status      = status;
    Irp->IoStatus.Information = info;
    IoCompleteRequest(Irp, IO_NO_INCREMENT);
    return status;
}

NTSTATUS DispatchCreateClose(PDEVICE_OBJECT DevObj, PIRP Irp)
{
    UNREFERENCED_PARAMETER(DevObj);
    Irp->IoStatus.Status      = STATUS_SUCCESS;
    Irp->IoStatus.Information = 0;
    IoCompleteRequest(Irp, IO_NO_INCREMENT);
    return STATUS_SUCCESS;
}

VOID DriverUnload(PDRIVER_OBJECT DriverObject)
{
    UNREFERENCED_PARAMETER(DriverObject);
    
    PsSetCreateProcessNotifyRoutineEx(ProcessCallback, TRUE);
    PsRemoveLoadImageNotifyRoutine(ImageCallback);
    UnregisterMinifilter();
    UnregisterWfpCallout();

    UNICODE_STRING symLink = RTL_CONSTANT_STRING(NANO_EDR_SYMBOLIC_NAME);
    IoDeleteSymbolicLink(&symLink);
    if (gDeviceObject)
        IoDeleteDevice(gDeviceObject);

    DbgPrint("[NanoEDR] Unloaded\n");
}

NTSTATUS DriverEntry(PDRIVER_OBJECT DriverObject, PUNICODE_STRING RegistryPath)
{
    UNREFERENCED_PARAMETER(RegistryPath);
    NTSTATUS status;

    // 建立 Device 和 SymLink
    UNICODE_STRING devName = RTL_CONSTANT_STRING(NANO_EDR_DEVICE_NAME);
    status = IoCreateDevice(DriverObject, 0, &devName,
                            FILE_DEVICE_UNKNOWN, 0, FALSE, &gDeviceObject);
    if (!NT_SUCCESS(status)) return status;

    UNICODE_STRING symLink = RTL_CONSTANT_STRING(NANO_EDR_SYMBOLIC_NAME);
    status = IoCreateSymbolicLink(&symLink, &devName);
    if (!NT_SUCCESS(status)) { IoDeleteDevice(gDeviceObject); return status; }

    gDeviceObject->Flags |= DO_BUFFERED_IO;
    gDeviceObject->Flags &= ~DO_DEVICE_INITIALIZING;

    // 設定 Dispatch
    DriverObject->DriverUnload                         = DriverUnload;
    DriverObject->MajorFunction[IRP_MJ_CREATE]         = DispatchCreateClose;
    DriverObject->MajorFunction[IRP_MJ_CLOSE]          = DispatchCreateClose;
    DriverObject->MajorFunction[IRP_MJ_DEVICE_CONTROL] = DispatchIoctl;

    // 注冊感測器
    status = PsSetCreateProcessNotifyRoutineEx(ProcessCallback, FALSE);
    if (!NT_SUCCESS(status)) goto cleanup;

    status = PsSetLoadImageNotifyRoutine(ImageCallback);
    if (!NT_SUCCESS(status)) goto cleanup;

    status = RegisterMinifilter(DriverObject);
    if (!NT_SUCCESS(status)) goto cleanup;

    status = RegisterWfpCallout(gDeviceObject);
    if (!NT_SUCCESS(status)) goto cleanup;

    DbgPrint("[NanoEDR] Loaded, all sensors active\n");
    return STATUS_SUCCESS;

cleanup:
    DriverUnload(DriverObject);
    return status;
}
```

## 用戶態服務：service/main.c

```c
#include <windows.h>
#include <stdio.h>
#include "NanoEDR.h"

int main(int argc, char* argv[])
{
    HANDLE hDev = CreateFile(L"\\\\.\\NanoEDR",
                             GENERIC_READ | GENERIC_WRITE,
                             0, NULL, OPEN_EXISTING, 0, NULL);
    if (hDev == INVALID_HANDLE_VALUE) {
        printf("[-] Cannot open NanoEDR: %lu\n", GetLastError());
        return 1;
    }
    printf("[*] NanoEDR connected\n");

    // 設定阻擋規則（示範：阻擋 notepad.exe）
    if (argc > 1 && strcmp(argv[1], "--block") == 0 && argc > 2) {
        NANO_RULE rule = { 0 };
        rule.RuleType = 0;
        MultiByteToWideChar(CP_ACP, 0, argv[2], -1,
                            rule.BlockedProcess, 260);
        DWORD bytes;
        DeviceIoControl(hDev, NANO_IOCTL_SET_RULE,
                        &rule, sizeof(rule), NULL, 0, &bytes, NULL);
        printf("[*] Block rule set: %s\n", argv[2]);
    }

    // 主循環：輪詢事件
    NANO_EVENT events[64];
    while (1) {
        DWORD bytes = 0;
        BOOL ok = DeviceIoControl(hDev, NANO_IOCTL_READ_EVENTS,
                                  NULL, 0,
                                  events, sizeof(events),
                                  &bytes, NULL);
        if (!ok) { printf("[-] Read error: %lu\n", GetLastError()); break; }

        ULONG count = bytes / sizeof(NANO_EVENT);
        for (ULONG i = 0; i < count; i++) {
            PNANO_EVENT e = &events[i];
            switch (e->Type) {
            case EventProcessCreate:
                printf("[PROC+] PID=%llu PPID=%llu %S\n",
                       e->Pid, e->Process.ParentPid, e->Process.ImagePath);
                break;
            case EventProcessExit:
                printf("[PROC-] PID=%llu\n", e->Pid);
                break;
            case EventImageLoad:
                printf("[IMG]   PID=%llu base=%016llX %S\n",
                       e->Pid, e->Image.LoadBase, e->Image.ImagePath);
                break;
            case EventFileCreate:
                printf("[FILE]  PID=%llu %S\n", e->Pid, e->File.FilePath);
                break;
            case EventNetConnect:
                printf("[NET]   PID=%llu → %u.%u.%u.%u:%u\n",
                       e->Pid,
                       (e->Network.RemoteAddr >> 24) & 0xFF,
                       (e->Network.RemoteAddr >> 16) & 0xFF,
                       (e->Network.RemoteAddr >> 8)  & 0xFF,
                        e->Network.RemoteAddr & 0xFF,
                       RtlUshortByteSwap(e->Network.RemotePort));
                break;
            }
        }

        Sleep(100);  // 100ms 輪詢
    }

    CloseHandle(hDev);
    return 0;
}
```

## 測試場景

### 場景 1：進程監控

```
執行 NanoEDRSvc.exe
打開 PowerShell，執行 notepad.exe
觀察輸出：[PROC+] PID=xxxx PPID=xxxx C:\Windows\System32\notepad.exe
```

### 場景 2：進程阻擋

```
執行 NanoEDRSvc.exe --block notepad.exe
嘗試打開 notepad.exe
預期：進程建立失敗（Access Denied）
```

### 場景 3：網路連線監控

```
執行 NanoEDRSvc.exe
執行 curl http://example.com
觀察輸出：[NET] PID=xxxx → 93.184.216.34:80
```

### 場景 4：檔案操作（敏感路徑）

```
執行 NanoEDRSvc.exe
從 cmd 執行 type C:\Windows\System32\config\SAM
觀察輸出：[FILE] PID=xxxx C:\Windows\System32\config\SAM
```

## 加分功能（選做）

1. **WFP IP 封鎖**：加入 `NANO_IOCTL_SET_RULE RuleType=1` → WFP ClassifyFn 回傳 `FWP_ACTION_BLOCK`
2. **共享記憶體**：用 MDL 映射 RingBuffer 到用戶態，改成 0ms 延遲的真正推送（ZeroLatency）
3. **ObRegisterCallbacks**：保護 NanoEDRSvc.exe 進程，阻止 TerminateProcess
4. **簽章驗證**：在 `ImageCallback` 中用 `CiValidateImageHeader`（非公開，需要逆向）驗證 DLL 簽章
5. **告警分級**：在用戶態服務加入規則引擎，對行為序列打分，高分告警

## 自我檢核

- [ ] Minifilter + Callbacks + WFP 三種感測器共存，各司其職
- [ ] Ring Buffer 核心/用戶態共用：InterlockedExchange 保證寫指針安全
- [ ] IOCTL Buffered I/O：Read Events 用 OutputBuffer；Set Rule 用 InputBuffer
- [ ] DriverUnload 必須取消所有回調（Ps*、ObUnRegister、FltUnregister、WFP Unregister）
- [ ] 進程阻擋：`CreateInfo->CreationStatus = STATUS_ACCESS_DENIED`

---

恭喜完成 Windows Kernel Driver 全課程。

從 NT 架構到第一個 DriverEntry，從 IRP 到 Minifilter，從 Token 竊取到 VBS/HVCI——你現在有能力讀懂核心層的攻防，也有能力設計和實作一個真實的 EDR 感測器。

下一步：找一台裝了 HEVD 的 Win 10 VM，把 practice-c 的 exploit 實際跑起來，再把 NanoEDR 裝進去看它能不能偵測到你自己的攻擊行為。

→ 回到 [課程地圖](./README.md)
