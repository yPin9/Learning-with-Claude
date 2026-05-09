# Ch 18 — 非同步 I/O

> 目標：從驅動視角理解 Overlapped I/O 和 IOCP 的機制，掌握用戶態非同步請求在核心中的完整生命週期。

## 非同步 I/O 的意義

同步 I/O：`ReadFile` 呼叫後執行緒阻塞，等驅動完成後才返回。

非同步 I/O（Overlapped）：`ReadFile` 立刻返回，執行緒繼續做其他事；完成後透過 Event 或 IOCP 通知。

```c
// 用戶態：Overlapped I/O
OVERLAPPED ov = {0};
ov.hEvent = CreateEvent(NULL, TRUE, FALSE, NULL);

ReadFile(h, buf, 4096, NULL, &ov);  // 立刻返回，不阻塞

// 做其他事...

// 等待完成
WaitForSingleObject(ov.hEvent, INFINITE);

DWORD bytesRead;
GetOverlappedResult(h, &ov, &bytesRead, FALSE);
```

## 驅動如何配合

當用戶呼叫非同步 ReadFile 時，I/O Manager 建立 IRP，標記為「Pending 可以」，送到驅動。

驅動可以選擇：

**選項 A：同步完成**（驅動處理完畢後立刻 IoCompleteRequest）
- 對 Overlapped 呼叫，I/O Manager 收到完成後自動觸發 Event
- 最簡單，驅動不需要特別處理

**選項 B：非同步完成**（驅動把 IRP 加入佇列，稍後完成）
- 必須先 `IoMarkIrpPending`，返回 `STATUS_PENDING`
- 工作完成後在工作執行緒 `IoCompleteRequest`

## WorkItem：安全的非同步工作

在 Completion Routine（DISPATCH_LEVEL）需要做 PASSIVE_LEVEL 工作（例如等待 Mutex、呼叫需要 PASSIVE_LEVEL 的 API），最安全的方式是排一個 WorkItem：

```c
// 定義 WorkItem 的 Context
typedef struct _WORKITEM_CONTEXT {
    PIO_WORKITEM WorkItem;
    PIRP         Irp;
    ULONG        DataToProcess;
} WORKITEM_CONTEXT, *PWORKITEM_CONTEXT;

// WorkItem 回調（PASSIVE_LEVEL）
void WorkItemRoutine(PDEVICE_OBJECT DeviceObject, PVOID Context)
{
    PWORKITEM_CONTEXT ctx = (PWORKITEM_CONTEXT)Context;
    PIRP irp = ctx->Irp;
    
    // 這裡是 PASSIVE_LEVEL，可以做任何事
    DoExpensiveWork(ctx->DataToProcess);
    
    // 完成 IRP
    irp->IoStatus.Status      = STATUS_SUCCESS;
    irp->IoStatus.Information = 0;
    IoCompleteRequest(irp, IO_DISK_INCREMENT);
    
    // 釋放 WorkItem 和 Context
    IoFreeWorkItem(ctx->WorkItem);
    ExFreePoolWithTag(ctx, 'WkIt');
}

// 在 Dispatch Routine 中排入 WorkItem
NTSTATUS DispatchRead(PDEVICE_OBJECT DeviceObject, PIRP Irp)
{
    // 分配 WorkItem Context
    PWORKITEM_CONTEXT ctx = ExAllocatePoolWithTag(
        NonPagedPoolNx, sizeof(WORKITEM_CONTEXT), 'WkIt');
    if (!ctx) {
        Irp->IoStatus.Status = STATUS_INSUFFICIENT_RESOURCES;
        IoCompleteRequest(Irp, IO_NO_INCREMENT);
        return STATUS_INSUFFICIENT_RESOURCES;
    }
    
    // 建立 WorkItem
    ctx->WorkItem = IoAllocateWorkItem(DeviceObject);
    if (!ctx->WorkItem) {
        ExFreePoolWithTag(ctx, 'WkIt');
        Irp->IoStatus.Status = STATUS_INSUFFICIENT_RESOURCES;
        IoCompleteRequest(Irp, IO_NO_INCREMENT);
        return STATUS_INSUFFICIENT_RESOURCES;
    }
    
    ctx->Irp = Irp;
    ctx->DataToProcess = 42;
    
    // 標記 IRP 為 Pending
    IoMarkIrpPending(Irp);
    
    // 排入系統工作執行緒（DelayedWorkQueue = 低優先級）
    IoQueueWorkItem(ctx->WorkItem, WorkItemRoutine, DelayedWorkQueue, ctx);
    
    return STATUS_PENDING;
}
```

## IOCP（I/O Completion Port）的核心視角

IOCP 是高性能伺服器的基礎（如 IIS、Nginx-on-Windows）。從驅動視角：

```
用戶態 CreateIoCompletionPort(fileHandle, ...)
→ 核心：把 File Object 和 Completion Port 綁定

用戶態 ReadFile(fileHandle, ..., OVERLAPPED)
→ 核心：建立 IRP，加入驅動佇列，返回 STATUS_PENDING

驅動完成 IRP（IoCompleteRequest）
→ I/O Manager 發現 File Object 綁定了 IOCP
→ 把完成封包（OVERLAPPED_ENTRY）投入 IOCP 佇列

用戶態 GetQueuedCompletionStatus(iocp, ...)
→ 從 IOCP 佇列取出封包，執行緒繼續處理
```

驅動本身不需要特別處理 IOCP——只要正確完成 IRP，I/O Manager 自動把完成通知投入 IOCP。

## 系統工作執行緒 vs 自建執行緒

**系統工作執行緒（IoQueueWorkItem）**：
- 優點：不需要管理執行緒生命週期
- 缺點：共享系統執行緒池，可能排隊等待；無法控制優先級

**自建執行緒（PsCreateSystemThread）**：
```c
// 建立一個系統執行緒
HANDLE threadHandle;
OBJECT_ATTRIBUTES oa;
InitializeObjectAttributes(&oa, NULL, OBJ_KERNEL_HANDLE, NULL, NULL);

PsCreateSystemThread(
    &threadHandle,
    THREAD_ALL_ACCESS,
    &oa,
    NULL,   // ProcessHandle（NULL = System 進程）
    NULL,   // ClientId
    MyThreadRoutine,
    (PVOID)context);

// 取得 PKTHREAD 指針（用於 KeWaitForSingleObject 等待執行緒結束）
ObReferenceObjectByHandle(threadHandle, THREAD_ALL_ACCESS, 
                          *PsThreadType, KernelMode, 
                          (PVOID*)&gThread, NULL);
ZwClose(threadHandle);  // 關閉 Handle（但物件仍被 ObReferenceObject 持有）
```

長期運行的後台任務（如監控 loop）用自建執行緒；短暫的工作用 WorkItem。

## 自我檢核

- [ ] 非同步 IRP：`IoMarkIrpPending` 標記 → 返回 `STATUS_PENDING` → 工作執行緒 `IoCompleteRequest`
- [ ] WorkItem：把 DISPATCH_LEVEL 工作委派到 PASSIVE_LEVEL 執行的安全方法
- [ ] `IoAllocateWorkItem` / `IoQueueWorkItem` / `IoFreeWorkItem` 三件套
- [ ] IOCP 是 I/O Manager 的機制，驅動不需要特別支援，正確完成 IRP 就好
- [ ] `PsCreateSystemThread` 建立長期後台執行緒；WorkItem 適合短暫一次性工作

→ [Ch 19 Filter Driver](./19-filter-driver.md)
