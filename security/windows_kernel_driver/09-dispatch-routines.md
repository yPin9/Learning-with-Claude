# Ch 9 — Dispatch Routines

> 目標：實作 IRP_MJ_READ 和 IRP_MJ_WRITE dispatch routine，理解非同步 IRP 處理（IoMarkIrpPending）的正確模式。

## Dispatch Table 的 28 個 Slot

`DRIVER_OBJECT.MajorFunction[IRP_MJ_MAXIMUM_FUNCTION]` 是一個有 28 個函式指針的陣列：

```c
// 常用的 Major Function Code
#define IRP_MJ_CREATE                   0x00   // CreateFile()
#define IRP_MJ_CREATE_NAMED_PIPE        0x01
#define IRP_MJ_CLOSE                    0x02   // CloseHandle()
#define IRP_MJ_READ                     0x03   // ReadFile()
#define IRP_MJ_WRITE                    0x04   // WriteFile()
#define IRP_MJ_QUERY_INFORMATION        0x05
#define IRP_MJ_SET_INFORMATION          0x06
#define IRP_MJ_DEVICE_CONTROL           0x0E   // DeviceIoControl()
#define IRP_MJ_INTERNAL_DEVICE_CONTROL  0x0F   // 核心間呼叫
#define IRP_MJ_SHUTDOWN                 0x10
#define IRP_MJ_CLEANUP                  0x12   // 最後一個 Handle 關閉前（FILE_OBJECT 清理）
#define IRP_MJ_PNP                      0x1B   // PnP 事件
#define IRP_MJ_POWER                    0x16   // 電源管理
```

沒有設定 handler 的 slot 預設是 `IopInvalidDeviceRequest`，回傳 `STATUS_INVALID_DEVICE_REQUEST`。

## IRP_MJ_CREATE 和 IRP_MJ_CLOSE

最簡單的兩個，用來管理「開啟計數」：

```c
// 追蹤開啟次數（生產代碼用 InterlockedIncrement 做執行緒安全）
LONG gOpenCount = 0;

NTSTATUS DispatchCreate(PDEVICE_OBJECT DeviceObject, PIRP Irp)
{
    UNREFERENCED_PARAMETER(DeviceObject);
    
    InterlockedIncrement(&gOpenCount);
    DbgPrint("[Driver] Opened. Count: %d\n", gOpenCount);
    
    Irp->IoStatus.Status      = STATUS_SUCCESS;
    Irp->IoStatus.Information = 0;
    IoCompleteRequest(Irp, IO_NO_INCREMENT);
    return STATUS_SUCCESS;
}

NTSTATUS DispatchClose(PDEVICE_OBJECT DeviceObject, PIRP Irp)
{
    UNREFERENCED_PARAMETER(DeviceObject);
    
    InterlockedDecrement(&gOpenCount);
    DbgPrint("[Driver] Closed. Count: %d\n", gOpenCount);
    
    Irp->IoStatus.Status      = STATUS_SUCCESS;
    Irp->IoStatus.Information = 0;
    IoCompleteRequest(Irp, IO_NO_INCREMENT);
    return STATUS_SUCCESS;
}
```

### IRP_MJ_CLEANUP vs IRP_MJ_CLOSE

容易混淆：
- `IRP_MJ_CLEANUP`：**最後一個 Handle 關閉時**（File Object 的最後一次 CloseHandle），用於清理掛起的 I/O
- `IRP_MJ_CLOSE`：**File Object 的參考計數歸零時**（可能在 Cleanup 之後很久才發生，因為核心內部也可能持有參考）

順序：最後一個 `CloseHandle()` → `IRP_MJ_CLEANUP` → (等待所有核心參考釋放) → `IRP_MJ_CLOSE`

## IRP_MJ_READ：同步版本

Buffered I/O 模式下，`AssociatedIrp.SystemBuffer` 是 I/O Manager 準備好的核心緩衝區：

```c
NTSTATUS DispatchRead(PDEVICE_OBJECT DeviceObject, PIRP Irp)
{
    UNREFERENCED_PARAMETER(DeviceObject);
    
    PIO_STACK_LOCATION stack = IoGetCurrentIrpStackLocation(Irp);
    ULONG  bytesRequested = stack->Parameters.Read.Length;
    PVOID  buffer         = Irp->AssociatedIrp.SystemBuffer;
    
    if (buffer == NULL || bytesRequested == 0) {
        Irp->IoStatus.Status      = STATUS_INVALID_PARAMETER;
        Irp->IoStatus.Information = 0;
        IoCompleteRequest(Irp, IO_NO_INCREMENT);
        return STATUS_INVALID_PARAMETER;
    }
    
    // 在緩衝區裡填入資料
    static const CHAR msg[] = "Hello from kernel!\n";
    ULONG bytesToCopy = min(bytesRequested, sizeof(msg) - 1);
    
    RtlCopyMemory(buffer, msg, bytesToCopy);
    
    // IoStatus.Information = 實際傳輸的 bytes（ReadFile 的 lpNumberOfBytesRead）
    Irp->IoStatus.Status      = STATUS_SUCCESS;
    Irp->IoStatus.Information = bytesToCopy;
    IoCompleteRequest(Irp, IO_NO_INCREMENT);
    return STATUS_SUCCESS;
}
```

## IRP_MJ_WRITE：同步版本

```c
NTSTATUS DispatchWrite(PDEVICE_OBJECT DeviceObject, PIRP Irp)
{
    UNREFERENCED_PARAMETER(DeviceObject);
    
    PIO_STACK_LOCATION stack = IoGetCurrentIrpStackLocation(Irp);
    ULONG  bytesWritten = stack->Parameters.Write.Length;
    PVOID  buffer       = Irp->AssociatedIrp.SystemBuffer;
    
    // 印出用戶寫進來的內容（確保不超過 MAX_PATH）
    if (buffer && bytesWritten > 0 && bytesWritten < 512) {
        CHAR localBuf[513] = {0};
        RtlCopyMemory(localBuf, buffer, min(bytesWritten, 512));
        DbgPrint("[Driver] Write: %s\n", localBuf);
    }
    
    Irp->IoStatus.Status      = STATUS_SUCCESS;
    Irp->IoStatus.Information = bytesWritten;  // 告訴用戶態寫了多少
    IoCompleteRequest(Irp, IO_NO_INCREMENT);
    return STATUS_SUCCESS;
}
```

## 非同步 IRP：IoMarkIrpPending

上面的例子都是**同步**：在 dispatch routine 裡直接完成 IRP。

但有些操作（等待硬體回應、等待特定條件）不能在 dispatch routine 裡阻塞——因為可能在 DISPATCH_LEVEL 被呼叫。

**非同步模式**：dispatch routine 標記 IRP 為 Pending，放進佇列，返回 `STATUS_PENDING`；另一個執行緒稍後完成它。

```c
// 全域佇列（簡化版，生產代碼用 cancel-safe queue）
LIST_ENTRY  gPendingIrpQueue;
KSPIN_LOCK  gQueueLock;

NTSTATUS DispatchRead(PDEVICE_OBJECT DeviceObject, PIRP Irp)
{
    UNREFERENCED_PARAMETER(DeviceObject);
    
    // 1. 標記 IRP 為 Pending（必須在 IoCallDriver 返回 STATUS_PENDING 之前）
    IoMarkIrpPending(Irp);
    
    // 2. 把 IRP 加入佇列
    KIRQL oldIrql;
    KeAcquireSpinLock(&gQueueLock, &oldIrql);
    InsertTailList(&gPendingIrpQueue, &Irp->Tail.Overlay.ListEntry);
    KeReleaseSpinLock(&gQueueLock, oldIrql);
    
    // 3. 通知工作執行緒（這裡略）
    
    // 4. 返回 STATUS_PENDING（非常重要！）
    return STATUS_PENDING;
}

// 工作執行緒（另一個執行緒）
void WorkerThread(PVOID context)
{
    while (gRunning) {
        // 等待有工作
        KeWaitForSingleObject(&gWorkEvent, Executive, KernelMode, FALSE, NULL);
        
        // 取出 IRP
        KIRQL oldIrql;
        KeAcquireSpinLock(&gQueueLock, &oldIrql);
        if (IsListEmpty(&gPendingIrpQueue)) {
            KeReleaseSpinLock(&gQueueLock, oldIrql);
            continue;
        }
        PLIST_ENTRY entry = RemoveHeadList(&gPendingIrpQueue);
        KeReleaseSpinLock(&gQueueLock, oldIrql);
        
        PIRP irp = CONTAINING_RECORD(entry, IRP, Tail.Overlay.ListEntry);
        
        // 完成 IRP
        irp->IoStatus.Status      = STATUS_SUCCESS;
        irp->IoStatus.Information = 0;
        IoCompleteRequest(irp, IO_DISK_INCREMENT);
    }
}
```

### IoMarkIrpPending 的黃金規則

```
if (dispatch routine returns STATUS_PENDING):
    必須先呼叫過 IoMarkIrpPending(Irp)
    
if (已呼叫 IoMarkIrpPending):
    dispatch routine 必須返回 STATUS_PENDING
    （即使在標記後馬上完成了 IRP）
```

違反這個規則 → BSOD `MULTIPLE_IRP_COMPLETE_REQUESTS` 或記憶體踩踏。

## 常見 Dispatch Routine 錯誤

**錯誤一：存取完成後的 IRP**

```c
// 錯誤！
IoCallDriver(lowerDevice, Irp);  // IRP 可能已被下層完成
Irp->IoStatus.Status = ...;      // Use-After-Free！
```

**錯誤二：Completion Routine 返回 STATUS_CONTINUE_COMPLETION 後還存取 IRP**

```c
NTSTATUS Completion(PDEVICE_OBJECT DevObj, PIRP Irp, PVOID Ctx) {
    NTSTATUS status = Irp->IoStatus.Status;  // OK，在返回之前
    return STATUS_CONTINUE_COMPLETION;
    // IRP 在這行之後被釋放，不能再存取
}
```

**錯誤三：忘記 `IoStatus.Information`**

很多驅動只設了 `IoStatus.Status = STATUS_SUCCESS`，忘了設 `Information`。
對 `IRP_MJ_READ`，`Information` 是 ReadFile 返回的 `bytesRead`，不設就是 0，用戶端以為讀到 0 bytes。

## 在 WinDbg 設 Dispatch Routine 斷點

```
kd> bp DriverName!DispatchRead
kd> g
```

或更精準地用函式指針：

```
kd> dt nt!_DRIVER_OBJECT <drvobj_addr> MajorFunction
kd> bp <MajorFunction[3] 的值>  ← [3] = IRP_MJ_READ
```

## 自我檢核

- [ ] `IRP_MJ_CLEANUP` vs `IRP_MJ_CLOSE` 的觸發時機差異
- [ ] Buffered I/O 的 Read handler：從 `SystemBuffer` 填資料，`IoStatus.Information` = 實際 bytes
- [ ] 非同步 IRP 三步驟：`IoMarkIrpPending` → 加佇列 → 返回 `STATUS_PENDING`
- [ ] IoMarkIrpPending 黃金規則：標記後必須返回 STATUS_PENDING，不管之後有沒有馬上完成
- [ ] `IoCallDriver` 之後不能再存取 IRP（可能已被完成）

→ [Ch 10 IOCTL](./10-ioctl.md)
