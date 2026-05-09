# Ch 16 — IRP 完成常式與取消

> 目標：理解 Completion Routine 在 Device Stack 中的傳遞流程，掌握 Cancel-Safe Queue 替代手動 CancelRoutine 的現代做法。

## Completion Routine 的觸發時機

Filter Driver 把 IRP 傳給下層後，可以設置 Completion Routine，讓 IRP 從下層「往上冒泡」時呼叫自己：

```c
// 在傳給下層之前設定
IoCopyCurrentIrpStackLocationToNext(Irp);
IoSetCompletionRoutine(
    Irp,
    MyCompletionRoutine,
    (PVOID)context,     // 傳給 Completion Routine 的資料
    TRUE,               // InvokeOnSuccess
    TRUE,               // InvokeOnError
    TRUE);              // InvokeOnCancel

return IoCallDriver(NextDevice, Irp);
```

Completion Routine 的 IRQL 可以是任何值（取決於下層的完成環境），通常是 `DISPATCH_LEVEL`。所以：
- 不能等待 Mutex 或 Event
- 不能存取分頁記憶體
- 如果需要做更多工作，從 Completion Routine 排一個 DPC 或 WorkItem

### 返回值

```c
NTSTATUS MyCompletionRoutine(
    PDEVICE_OBJECT DeviceObject, PIRP Irp, PVOID Context)
{
    if (Irp->PendingReturned) {
        // 如果下層把 IRP 標記為 Pending，我們也要保留這個標記
        IoMarkIrpPending(Irp);
    }
    
    DbgPrint("IRP completed, status: 0x%X\n", Irp->IoStatus.Status);
    
    // 返回 STATUS_CONTINUE_COMPLETION → 繼續往上冒泡
    // 返回 STATUS_MORE_PROCESSING_REQUIRED → 停止冒泡（由我自己稍後完成）
    return STATUS_CONTINUE_COMPLETION;
}
```

## IRP 取消（Cancellation）

用戶呼叫 `CancelIo()` 或關閉 Handle 時，Windows 把掛起的 IRP 標記為取消。

### 傳統 CancelRoutine（危險）

```c
// 設定 CancelRoutine（在把 IRP 加入佇列前）
IoSetCancelRoutine(Irp, MyCancelRoutine);

// CancelRoutine 在 DISPATCH_LEVEL、持有 CancelSpinLock 時呼叫
void MyCancelRoutine(PDEVICE_OBJECT DeviceObject, PIRP Irp)
{
    // 必須釋放 CancelSpinLock（傳進來時已持有）
    IoReleaseCancelSpinLock(Irp->CancelIrql);
    
    // 從佇列移除此 IRP（需要你自己用另一個 SpinLock 保護佇列）
    RemoveEntryList(&Irp->Tail.Overlay.ListEntry);
    
    // 完成 IRP（取消狀態）
    Irp->IoStatus.Status      = STATUS_CANCELLED;
    Irp->IoStatus.Information = 0;
    IoCompleteRequest(Irp, IO_NO_INCREMENT);
}
```

這個模式有一個難以避免的競爭條件（在設 CancelRoutine 和加入佇列之間）。現代驅動改用 Cancel-Safe Queue。

## Cancel-Safe Queue（現代做法）

Windows XP 起提供 `IoCsq*` 系列 API，把取消的複雜度封裝好：

```c
#include <csq.h>

// 需要實作的 Callback（框架呼叫你的函式管理佇列）
IO_CSQ_INSERT_IRP    CsqInsertIrp;
IO_CSQ_REMOVE_IRP    CsqRemoveIrp;
IO_CSQ_PEEK_NEXT_IRP CsqPeekNextIrp;
IO_CSQ_ACQUIRE_LOCK  CsqAcquireLock;
IO_CSQ_RELEASE_LOCK  CsqReleaseLock;
IO_CSQ_COMPLETE_CANCELED_IRP CsqCompleteCanceledIrp;

// 你的佇列結構
LIST_ENTRY  gIrpQueue;
KSPIN_LOCK  gIrpQueueLock;
IO_CSQ      gCsq;

// 初始化
IoCsqInitialize(
    &gCsq,
    CsqInsertIrp,
    CsqRemoveIrp,
    CsqPeekNextIrp,
    CsqAcquireLock,
    CsqReleaseLock,
    CsqCompleteCanceledIrp);

// 實作（最小化範例）
void CsqInsertIrp(PIO_CSQ Csq, PIRP Irp) {
    InsertTailList(&gIrpQueue, &Irp->Tail.Overlay.ListEntry);
}

void CsqRemoveIrp(PIO_CSQ Csq, PIRP Irp) {
    RemoveEntryList(&Irp->Tail.Overlay.ListEntry);
}

PIRP CsqPeekNextIrp(PIO_CSQ Csq, PIRP Irp, PVOID PeekContext) {
    if (Irp == NULL) {
        if (IsListEmpty(&gIrpQueue)) return NULL;
        return CONTAINING_RECORD(gIrpQueue.Flink, IRP, Tail.Overlay.ListEntry);
    }
    PLIST_ENTRY next = Irp->Tail.Overlay.ListEntry.Flink;
    if (next == &gIrpQueue) return NULL;
    return CONTAINING_RECORD(next, IRP, Tail.Overlay.ListEntry);
}

void CsqAcquireLock(PIO_CSQ Csq, PKIRQL Irql) {
    KeAcquireSpinLock(&gIrpQueueLock, Irql);
}

void CsqReleaseLock(PIO_CSQ Csq, KIRQL Irql) {
    KeReleaseSpinLock(&gIrpQueueLock, Irql);
}

void CsqCompleteCanceledIrp(PIO_CSQ Csq, PIRP Irp) {
    Irp->IoStatus.Status      = STATUS_CANCELLED;
    Irp->IoStatus.Information = 0;
    IoCompleteRequest(Irp, IO_NO_INCREMENT);
}

// 使用（在 Dispatch Routine 中）
IoCsqInsertIrp(&gCsq, Irp, NULL);  // IRP 安全入佇列，取消自動處理

// 取出（在 Worker Thread 中）
PIRP irp = IoCsqRemoveNextIrp(&gCsq, NULL);
if (irp) {
    // 處理 IRP
    irp->IoStatus.Status      = STATUS_SUCCESS;
    irp->IoStatus.Information = 0;
    IoCompleteRequest(irp, IO_NO_INCREMENT);
}
```

Cancel-Safe Queue 的核心保證：IRP 在佇列中被取消，`CsqCompleteCanceledIrp` 自動被呼叫，你不需要手動處理取消競爭。

## 自我檢核

- [ ] Completion Routine 的三個觸發條件 flag：InvokeOnSuccess/Error/Cancel
- [ ] `Irp->PendingReturned` 為 TRUE 時，Completion Routine 必須呼叫 `IoMarkIrpPending`
- [ ] 傳統 CancelRoutine 有固有競爭條件，新代碼用 `IoCsqInitialize`
- [ ] Cancel-Safe Queue 的 6 個 callback：Insert、Remove、PeekNext、Lock、Unlock、CompleteCanceled
- [ ] `STATUS_MORE_PROCESSING_REQUIRED`：Completion Routine 返回此值，IRP 停止向上冒泡

→ [Ch 17 直接 I/O](./17-direct-io.md)
