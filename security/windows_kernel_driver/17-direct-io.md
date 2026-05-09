# Ch 17 — 直接 I/O

> 目標：理解 Direct I/O 的零複製原理，掌握 MDL-based 大數據傳輸的驅動實作，以及 METHOD_IN_DIRECT 和 METHOD_OUT_DIRECT 的差異。

## 為什麼需要直接 I/O

Buffered I/O（Ch 10）對小數據（< 幾十 KB）很好：I/O Manager 幫你複製，簡單安全。

但磁碟驅動、網路驅動傳輸的是 MB 到 GB 的數據。每次都複製 = CPU 忙著搬運記憶體 = 吞吐量崩潰。

Direct I/O 的思路：**鎖定用戶緩衝區的物理頁，讓驅動直接讀寫這些物理頁**，省掉複製。

## Device Object 設定 Direct I/O

```c
// DriverEntry 中
gDeviceObject->Flags |= DO_DIRECT_IO;
gDeviceObject->Flags &= ~DO_BUFFERED_IO;  // 清除 Buffered IO flag
```

設定後，對這個設備的所有 Read/Write IRP，I/O Manager 都會：
1. 呼叫 `MmProbeAndLockPages` 鎖定用戶緩衝區
2. 建立 MDL，放入 `Irp->MdlAddress`
3. 完成後自動解鎖

## 直接 I/O Read Dispatch

```c
NTSTATUS DispatchReadDirect(PDEVICE_OBJECT DeviceObject, PIRP Irp)
{
    UNREFERENCED_PARAMETER(DeviceObject);
    
    PIO_STACK_LOCATION stack = IoGetCurrentIrpStackLocation(Irp);
    ULONG length = stack->Parameters.Read.Length;
    
    // Direct I/O：用 MdlAddress
    if (!Irp->MdlAddress) {
        Irp->IoStatus.Status = STATUS_INVALID_PARAMETER;
        IoCompleteRequest(Irp, IO_NO_INCREMENT);
        return STATUS_INVALID_PARAMETER;
    }
    
    // 取得核心可存取的虛擬地址
    PVOID buf = MmGetSystemAddressForMdlSafe(
        Irp->MdlAddress,
        NormalPagePriority | MdlMappingNoExecute);
    
    if (!buf) {
        Irp->IoStatus.Status = STATUS_INSUFFICIENT_RESOURCES;
        IoCompleteRequest(Irp, IO_NO_INCREMENT);
        return STATUS_INSUFFICIENT_RESOURCES;
    }
    
    // 把資料直接寫入用戶物理頁（零複製）
    ULONG toCopy = min(length, gDataLen);
    RtlCopyMemory(buf, gData, toCopy);
    
    Irp->IoStatus.Status      = STATUS_SUCCESS;
    Irp->IoStatus.Information = toCopy;
    IoCompleteRequest(Irp, IO_NO_INCREMENT);
    return STATUS_SUCCESS;
}
```

## IOCTL 的 METHOD_IN_DIRECT 和 METHOD_OUT_DIRECT

IOCTL（DeviceIoControl）也可以用 Direct I/O，透過 Method 欄位控制：

```
METHOD_IN_DIRECT (1):
  InputBuffer  → 核心緩衝區（Buffered，安全複製）
  OutputBuffer → MDL（鎖定的用戶物理頁）
  用途：驅動需要讀取用戶提供的大型輸出緩衝區

METHOD_OUT_DIRECT (2):
  InputBuffer  → MDL（鎖定的用戶物理頁）
  OutputBuffer → 核心緩衝區（Buffered）
  用途：驅動要向用戶傳輸大量資料（最常見）
```

METHOD_OUT_DIRECT 的 IOCTL Handler：

```c
// IOCTL_READ_LARGE_DATA 定義為 METHOD_OUT_DIRECT
case IOCTL_READ_LARGE_DATA: {
    PIO_STACK_LOCATION stack = IoGetCurrentIrpStackLocation(Irp);
    
    // 小型控制輸入（Buffered）
    PVOID inputBuf = Irp->AssociatedIrp.SystemBuffer;
    ULONG inputLen = stack->Parameters.DeviceIoControl.InputBufferLength;
    
    // 大型輸出緩衝區（Direct，用 MDL）
    if (!Irp->MdlAddress) {
        status = STATUS_INVALID_PARAMETER;
        break;
    }
    
    PVOID outputBuf = MmGetSystemAddressForMdlSafe(
        Irp->MdlAddress, NormalPagePriority | MdlMappingNoExecute);
    ULONG outputLen = stack->Parameters.DeviceIoControl.OutputBufferLength;
    
    if (!outputBuf) {
        status = STATUS_INSUFFICIENT_RESOURCES;
        break;
    }
    
    // 把大量數據寫入用戶緩衝區
    RtlCopyMemory(outputBuf, gLargeBuffer, min(outputLen, gLargeBufferSize));
    info = min(outputLen, gLargeBufferSize);
    status = STATUS_SUCCESS;
    break;
}
```

## DMA（Direct Memory Access）

真正的硬體驅動（網卡、NVMe 控制器）不是 CPU 複製，而是讓硬體 DMA Engine 直接讀寫記憶體。

MDL 提供了建立 DMA transfer 所需的物理頁地址列表（PFN 陣列）：

```c
// 取得 MDL 的物理地址（傳給 DMA Engine）
PHYSICAL_ADDRESS physAddr = MmGetPhysicalAddress(
    MmGetMdlVirtualAddress(mdl));  // MDL 起始的物理地址

// 或用 DMA Adapter API
DMA_ADAPTER* dmaAdapter; // 由 IoGetDmaAdapter 取得
// GetScatterGatherList, MapTransfer 等 API
```

DMA 細節超出這門課的範圍，但知道 MDL 是 DMA 的基礎很重要。

## 物理地址 vs 虛擬地址

```c
// 虛擬地址 → 物理地址
PHYSICAL_ADDRESS pa = MmGetPhysicalAddress(virtualPtr);

// 物理地址 → 核心虛擬地址（記憶體映射 I/O 用）
PVOID va = MmMapIoSpace(physicalAddress, length, MmNonCached);
// 用完後
MmUnmapIoSpace(va, length);
```

`MmMapIoSpace` 常用於映射硬體暫存器（MMIO），讓驅動透過虛擬地址讀寫硬體。

## 自我檢核

- [ ] Direct I/O 的目的：鎖定用戶物理頁，避免複製，適合大數據傳輸
- [ ] `DO_DIRECT_IO` flag：I/O Manager 自動建立 MDL，`Irp->MdlAddress` 非 NULL
- [ ] `MmGetSystemAddressForMdlSafe` 建立核心虛擬地址映射，必須加 `MdlMappingNoExecute`
- [ ] METHOD_OUT_DIRECT：輸出用 MDL（大型），輸入用 SystemBuffer（小型）
- [ ] MDL 的 PFN 陣列提供物理地址，是 DMA 的基礎

→ [Ch 18 非同步 I/O](./18-async-io.md)
