# 練習 A — KMDF 驅動 + IOCTL 控制介面

> 目標：把 Ch 6–10 學到的東西整合，用 KMDF 實作一個帶多個 IOCTL 命令的驅動，並寫對應的用戶態測試工具。

## 任務規格

實作 **ProcessInfoDriver**：讓用戶態程式查詢指定 PID 的基本進程資訊。

### 支援的 IOCTL

| IOCTL | 輸入 | 輸出 | 說明 |
|-------|------|------|------|
| `IOCTL_QUERY_PROCESS` | `ULONG pid` | `PROCESS_INFO` | 查詢 PID 的進程名稱和 PPID |
| `IOCTL_LIST_PROCESSES` | 無 | `PROCESS_LIST`（最多 64 筆） | 列出前 64 個進程 |
| `IOCTL_GET_DRIVER_VERSION` | 無 | `VERSION_INFO` | 查詢驅動版本 |

### 共用資料結構（driver_shared.h）

```c
#pragma pack(push, 1)

typedef struct _PROCESS_INFO {
    ULONG   Pid;
    ULONG   PPid;
    CHAR    ImageName[16];   // EPROCESS.ImageFileName 的長度
    BOOLEAN IsWow64;         // 32-bit process on 64-bit OS
} PROCESS_INFO;

typedef struct _PROCESS_LIST {
    ULONG        Count;
    PROCESS_INFO Entries[64];
} PROCESS_LIST;

typedef struct _VERSION_INFO {
    ULONG Major;
    ULONG Minor;
    CHAR  Date[16];
} VERSION_INFO;

typedef struct _QUERY_INPUT {
    ULONG Pid;
} QUERY_INPUT;

#pragma pack(pop)

#define IOCTL_QUERY_PROCESS     CTL_CODE(FILE_DEVICE_UNKNOWN, 0x800, METHOD_BUFFERED, FILE_ANY_ACCESS)
#define IOCTL_LIST_PROCESSES    CTL_CODE(FILE_DEVICE_UNKNOWN, 0x801, METHOD_BUFFERED, FILE_ANY_ACCESS)
#define IOCTL_GET_DRIVER_VERSION CTL_CODE(FILE_DEVICE_UNKNOWN, 0x802, METHOD_BUFFERED, FILE_ANY_ACCESS)
```

## 期望輸出

用戶態工具執行後：

```
ProcessInfo Driver v1.0 (2024-01-01)
--------------------------------------
Query PID 1234:
  Name:   notepad.exe
  PPID:   5678
  WoW64:  No

All processes (first 10 shown):
  [0] PID=4      PPID=0    System
  [1] PID=88     PPID=4    smss.exe
  [2] PID=400    PPID=376  csrss.exe
  ...
```

## 實作步驟

### Step 1：建立 KMDF 專案

VS 2022 → 新增專案 → Kernel Mode Driver (KMDF)

專案結構：
```
ProcessInfoDriver/
├── Driver.c       ← DriverEntry, EvtDeviceAdd
├── IoHandler.c    ← IOCTL dispatch handlers
├── ProcessUtil.c  ← 查詢進程資訊的核心邏輯
├── driver_shared.h ← 共用結構和 IOCTL code
└── ProcessInfoDriver.inf
```

### Step 2：ProcessUtil.c — 查詢進程資訊

用 `PsLookupProcessByProcessId()` 取得 `PEPROCESS`，再用官方 API 讀取資訊：

```c
// ProcessUtil.c
#include <ntddk.h>
#include "driver_shared.h"

NTSTATUS QueryProcessInfo(ULONG pid, PROCESS_INFO* out)
{
    PEPROCESS process;
    NTSTATUS status;
    
    // 從 PID 取得 EPROCESS（自動增加參考計數）
    status = PsLookupProcessByProcessId((HANDLE)(ULONG_PTR)pid, &process);
    if (!NT_SUCCESS(status)) return status;

    // 填入資訊（使用官方 API，不直接存取 EPROCESS 欄位）
    out->Pid  = pid;
    out->PPid = (ULONG)(ULONG_PTR)PsGetProcessInheritedFromUniqueProcessId(process);
    
    // 映像名稱（EPROCESS.ImageFileName，最多 15 字元）
    PUCHAR imageName = PsGetProcessImageFileName(process);
    RtlStringCbCopyA(out->ImageName, sizeof(out->ImageName), 
                     (PCSTR)imageName);
    
    // 是否 WoW64 進程
    out->IsWow64 = (PsGetProcessWow64Process(process) != NULL);
    
    // 釋放參考（和 PsLookupProcessByProcessId 配對）
    ObDereferenceObject(process);
    
    return STATUS_SUCCESS;
}
```

### Step 3：列舉進程

在核心列舉所有進程要遍歷 `PsActiveProcessHead` 連結串列，或使用未公開的 `PsGetNextProcess()`（WDK 沒有文件，但常用）：

```c
NTSTATUS ListAllProcesses(PROCESS_LIST* out)
{
    out->Count = 0;
    
    PEPROCESS process = PsInitialSystemProcess;
    
    if (!process) return STATUS_UNSUCCESSFUL;

    // 增加參考
    ObReferenceObject(process);
    
    do {
        if (out->Count >= 64) break;
        
        PROCESS_INFO* entry = &out->Entries[out->Count];
        entry->Pid  = (ULONG)(ULONG_PTR)PsGetProcessId(process);
        entry->PPid = (ULONG)(ULONG_PTR)PsGetProcessInheritedFromUniqueProcessId(process);
        
        PUCHAR name = PsGetProcessImageFileName(process);
        RtlStringCbCopyA(entry->ImageName, sizeof(entry->ImageName), (PCSTR)name);
        
        entry->IsWow64 = (PsGetProcessWow64Process(process) != NULL);
        
        out->Count++;
        
        // 移到下一個進程
        PEPROCESS next = PsGetNextProcess(process);
        ObDereferenceObject(process);
        process = next;
        
    } while (process != NULL && process != PsInitialSystemProcess);
    
    if (process) ObDereferenceObject(process);
    
    return STATUS_SUCCESS;
}
```

> 提示：`PsGetNextProcess()` 在 WDK 標頭裡沒有宣告，需要自己宣告：
> ```c
> PEPROCESS PsGetNextProcess(PEPROCESS Process);
> ```
> 記住每次呼叫都會增加下一個進程的參考計數，必須 `ObDereferenceObject` 釋放。

### Step 4：IOCTL Dispatch

```c
// IoHandler.c
void EvtIoDeviceControl(
    WDFQUEUE Queue, WDFREQUEST Request,
    size_t OutputBufferLength, size_t InputBufferLength,
    ULONG IoControlCode)
{
    NTSTATUS status;
    size_t   info = 0;
    
    switch (IoControlCode) {
        case IOCTL_QUERY_PROCESS: {
            QUERY_INPUT* input;
            PROCESS_INFO* output;
            
            status = WdfRequestRetrieveInputBuffer(
                Request, sizeof(QUERY_INPUT), (PVOID*)&input, NULL);
            if (!NT_SUCCESS(status)) break;
            
            status = WdfRequestRetrieveOutputBuffer(
                Request, sizeof(PROCESS_INFO), (PVOID*)&output, NULL);
            if (!NT_SUCCESS(status)) break;
            
            status = QueryProcessInfo(input->Pid, output);
            if (NT_SUCCESS(status)) info = sizeof(PROCESS_INFO);
            break;
        }
        
        case IOCTL_LIST_PROCESSES: {
            PROCESS_LIST* output;
            status = WdfRequestRetrieveOutputBuffer(
                Request, sizeof(PROCESS_LIST), (PVOID*)&output, NULL);
            if (!NT_SUCCESS(status)) break;
            
            status = ListAllProcesses(output);
            if (NT_SUCCESS(status)) info = sizeof(PROCESS_LIST);
            break;
        }
        
        case IOCTL_GET_DRIVER_VERSION: {
            VERSION_INFO* output;
            status = WdfRequestRetrieveOutputBuffer(
                Request, sizeof(VERSION_INFO), (PVOID*)&output, NULL);
            if (!NT_SUCCESS(status)) break;
            
            output->Major = 1;
            output->Minor = 0;
            RtlStringCbCopyA(output->Date, sizeof(output->Date), __DATE__);
            info = sizeof(VERSION_INFO);
            status = STATUS_SUCCESS;
            break;
        }
        
        default:
            status = STATUS_INVALID_DEVICE_REQUEST;
            break;
    }
    
    WdfRequestCompleteWithInformation(Request, status, info);
}
```

### Step 5：用戶態工具

```c
// procinfo.c（用戶態工具，編譯為普通 Win32 Console App）
#include <windows.h>
#include <stdio.h>
#include "driver_shared.h"  // 共用結構

int main(int argc, char* argv[]) {
    HANDLE h = CreateFile(L"\\\\.\\ProcessInfoDriver",
                          GENERIC_READ | GENERIC_WRITE,
                          0, NULL, OPEN_EXISTING,
                          FILE_ATTRIBUTE_NORMAL, NULL);
    
    if (h == INVALID_HANDLE_VALUE) {
        printf("Cannot open driver: %d\n", GetLastError());
        printf("Is the driver loaded? Run 'sc start ProcessInfoDriver'\n");
        return 1;
    }
    
    // 取驅動版本
    VERSION_INFO ver = {0};
    DWORD bytes;
    DeviceIoControl(h, IOCTL_GET_DRIVER_VERSION,
                    NULL, 0, &ver, sizeof(ver), &bytes, NULL);
    printf("ProcessInfo Driver v%d.%d (%s)\n\n", ver.Major, ver.Minor, ver.Date);
    
    // 如果有命令列參數，查詢特定 PID
    if (argc >= 2) {
        ULONG pid = (ULONG)atoi(argv[1]);
        QUERY_INPUT input = { pid };
        PROCESS_INFO info = {0};
        
        if (DeviceIoControl(h, IOCTL_QUERY_PROCESS,
                            &input, sizeof(input),
                            &info, sizeof(info),
                            &bytes, NULL)) {
            printf("PID %lu:\n", pid);
            printf("  Name:   %s\n", info.ImageName);
            printf("  PPID:   %lu\n", info.PPid);
            printf("  WoW64:  %s\n", info.IsWow64 ? "Yes" : "No");
        } else {
            printf("Query PID %lu failed: %d\n", pid, GetLastError());
        }
    }
    
    // 列出所有進程
    printf("\nAll processes:\n");
    PROCESS_LIST* list = (PROCESS_LIST*)HeapAlloc(GetProcessHeap(), 0, sizeof(PROCESS_LIST));
    if (list && DeviceIoControl(h, IOCTL_LIST_PROCESSES,
                                NULL, 0, list, sizeof(PROCESS_LIST),
                                &bytes, NULL)) {
        for (ULONG i = 0; i < list->Count; i++) {
            printf("  [%3d] PID=%-6lu PPID=%-6lu %s\n",
                   i, list->Entries[i].Pid, list->Entries[i].PPid,
                   list->Entries[i].ImageName);
        }
    }
    if (list) HeapFree(GetProcessHeap(), 0, list);
    
    CloseHandle(h);
    return 0;
}
```

## 參考解答重點

<details>
<summary>點開後看關鍵細節（寫完再看）</summary>

1. **`PsLookupProcessByProcessId` 返回的 EPROCESS 必須 `ObDereferenceObject` 釋放**。忘記這步就是核心記憶體洩漏，Driver Verifier 會捉到。

2. **PROCESS_LIST 是 ~2KB 的大結構，在棧上放會 KERNEL_STACK_OVERFLOW**。用 `ExAllocatePoolWithTag` 分配，或讓 WDF 幫你管（輸出緩衝區是用戶提供的，不在棧上）。

3. **`PsGetNextProcess` 不在 WDK 公開 API 裡，替代方案**是用 `ZwQuerySystemInformation(SystemProcessInformation, ...)` 從用戶態取列表，或自己走 `ActiveProcessLinks`（需要知道偏移）。

4. **KMDF 的設備介面 GUID**：用 `WdfDeviceCreateDeviceInterface` 替代 `IoCreateSymbolicLink` 更現代，但需要 `guidgen.exe` 生成 GUID 並在 INF 裡聲明。

5. **`WdfRequestCompleteWithInformation` vs `WdfRequestComplete`**：前者設置 `IoStatus.Information`，對輸出型 IOCTL 必須用前者，否則用戶端收到 0 bytes。

</details>

## 進階挑戰

1. **加入 IOCTL_INJECT_DLL**（僅在自己的測試進程中）：在指定 PID 的進程中分配記憶體並注入 shellcode（APC 注入或 `ZwCreateThreadEx`）。注意：這需要 `SeDebugPrivilege`，也是很多安全工具做的事。

2. **加入存取控制**：只允許 SYSTEM 或 Administrators 開啟驅動（用 `WdmlibIoCreateDeviceSecure` 的 SDDL 字串）。

3. **加入 Driver Verifier 測試**：開啟 Driver Verifier 的 Special Pool 和 IRQL Checking，確認驅動在壓力下不崩潰。

## 自我檢核

- [ ] KMDF `EvtDeviceAdd` + `WdfIoQueueCreate` + `EvtIoDeviceControl` 的串接關係
- [ ] `PsLookupProcessByProcessId` + `ObDereferenceObject` 必須配對
- [ ] `WdfRequestRetrieveInputBuffer` / `WdfRequestRetrieveOutputBuffer` 的第二個參數是「最小長度」保護
- [ ] `WdfRequestCompleteWithInformation` 告知 I/O Manager 輸出了多少 bytes
- [ ] 用戶態 `CreateFile("\\\\.\\DriverName")` 對應驅動的 `IRP_MJ_CREATE`

→ [Ch 11 核心記憶體模型](./11-kernel-memory.md)
