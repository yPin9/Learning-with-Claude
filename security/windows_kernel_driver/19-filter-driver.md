# Ch 19 — Filter Driver

> 目標：理解傳統 Filter Driver 的 Device Stack 插入機制，以及為什麼 Minifilter 取代了它。

## Filter Driver 的概念

Filter Driver 是插在 Device Stack 中間的驅動，攔截流過的 IRP、做修改或記錄，再傳給下一層：

```
Application
    ↓ ReadFile()
I/O Manager
    ↓
Upper Filter（你的 Filter）← 在這裡攔截
    ↓ IoCallDriver
Function Driver（原本的驅動）
    ↓ IoCallDriver
Lower Filter
    ↓
Bus Driver
```

典型用途：加密、壓縮、存取日誌、防毒掃描、磁碟過濾。

## 插入 Device Stack

Filter Driver 用 `IoAttachDeviceToDeviceStack` 把自己的 Device Object 插在現有設備上面：

```c
PDEVICE_OBJECT gUpperDevice = NULL;  // Filter 自己的 Device Object
PDEVICE_OBJECT gLowerDevice = NULL;  // 被過濾的 Device Object（IoAttach 的返回值）

NTSTATUS FilterAddDevice(PDRIVER_OBJECT DriverObject, PDEVICE_OBJECT PhysicalDevice)
{
    NTSTATUS status;
    
    // 建立 Filter 自己的 Device Object
    status = IoCreateDevice(
        DriverObject,
        sizeof(FILTER_DEVICE_EXTENSION),
        NULL,           // 匿名（Filter 不需要有名字）
        PhysicalDevice->DeviceType,
        PhysicalDevice->Characteristics,
        FALSE,
        &gUpperDevice);
    
    if (!NT_SUCCESS(status)) return status;
    
    // 繼承下層的 Flags（Buffered/Direct IO 設定必須一致）
    gUpperDevice->Flags |= PhysicalDevice->Flags & (DO_BUFFERED_IO | DO_DIRECT_IO);
    
    // 初始化 Device Extension
    PFILTER_DEVICE_EXTENSION ext = gUpperDevice->DeviceExtension;
    ext->PhysicalDevice = PhysicalDevice;
    
    // 把 Filter 插到 Device Stack 上層
    // 返回值是原來 Stack 頂部的 Device（成為我們的 LowerDevice）
    gLowerDevice = IoAttachDeviceToDeviceStack(gUpperDevice, PhysicalDevice);
    if (!gLowerDevice) {
        IoDeleteDevice(gUpperDevice);
        return STATUS_NO_SUCH_DEVICE;
    }
    
    gUpperDevice->Flags &= ~DO_DEVICE_INITIALIZING;
    return STATUS_SUCCESS;
}
```

## IRP 傳遞

Filter Driver 的每個 dispatch routine 都要傳 IRP 給下一層：

```c
// 對不感興趣的 IRP，直接傳下去（不能只是忽略）
NTSTATUS FilterPassThrough(PDEVICE_OBJECT DeviceObject, PIRP Irp)
{
    PFILTER_DEVICE_EXTENSION ext = DeviceObject->DeviceExtension;
    
    // 跳過當前 Stack Location（移到下一層的 slot）
    IoSkipCurrentIrpStackLocation(Irp);
    
    // 傳給下一層
    return IoCallDriver(ext->LowerDevice, Irp);
}

// 對感興趣的 IRP（例如 Read），攔截後傳下去
NTSTATUS FilterRead(PDEVICE_OBJECT DeviceObject, PIRP Irp)
{
    PFILTER_DEVICE_EXTENSION ext = DeviceObject->DeviceExtension;
    PIO_STACK_LOCATION stack = IoGetCurrentIrpStackLocation(Irp);
    
    DbgPrint("[Filter] Read: %lu bytes\n", stack->Parameters.Read.Length);
    
    // 設定 Completion Routine（想看到結果）
    IoCopyCurrentIrpStackLocationToNext(Irp);
    IoSetCompletionRoutine(Irp, FilterReadCompletion, NULL, TRUE, TRUE, TRUE);
    
    return IoCallDriver(ext->LowerDevice, Irp);
}

NTSTATUS FilterReadCompletion(PDEVICE_OBJECT DeviceObject, PIRP Irp, PVOID Context)
{
    DbgPrint("[Filter] Read completed: 0x%X, %llu bytes\n",
             Irp->IoStatus.Status, Irp->IoStatus.Information);
    
    if (Irp->PendingReturned) IoMarkIrpPending(Irp);
    return STATUS_CONTINUE_COMPLETION;
}
```

**`IoSkipCurrentIrpStackLocation` vs `IoCopyCurrentIrpStackLocationToNext`**：
- `IoSkip`：不設 Completion Routine，直接跳過（移動指針，下層看到同一個 stack slot）
- `IoCopy`：複製當前 slot 到下一個 slot，再設 Completion Routine

## 傳統 Filter Driver 的問題

傳統 Filter Driver 有嚴重的互動問題：

1. **安裝順序不確定**：多個 Filter 的插入順序由 Registry 決定，可能衝突
2. **IRP 洩漏**：Filter 攔截 IRP 後忘記傳給下層或完成，整個 Stack 卡死
3. **卸載困難**：有 IRP 在 Stack 時不能卸載，否則崩潰
4. **和 NTFS 衝突**：直接過濾 File System 的 Stack 非常危險

這些問題促成了 **Minifilter**（Ch 20）的設計：微軟推薦的現代檔案系統過濾方式。

## 傳統 Filter 的適用場景

傳統 Filter Driver 仍然有用的場景：
- 非檔案系統設備（USB、串口、磁碟）
- 網路介面過濾（WFP 之前）
- 攔截特定的設備 IOCTL

## 自我檢核

- [ ] Filter Driver 用 `IoAttachDeviceToDeviceStack` 插入 Device Stack 頂部
- [ ] 傳下去用 `IoSkipCurrentIrpStackLocation`（無 Completion）或 `IoCopyCurrentIrpStackLocationToNext`（有 Completion）
- [ ] Filter 必須設定和下層一致的 `DO_BUFFERED_IO` / `DO_DIRECT_IO` flags
- [ ] 傳統 Filter 的三大問題：安裝順序衝突、IRP 洩漏、卸載困難
- [ ] 檔案系統過濾用 Minifilter，不用傳統 Filter

→ [Ch 20 Minifilter Driver](./20-minifilter.md)
