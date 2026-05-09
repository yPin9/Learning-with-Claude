# Ch 12 — MDL

> 目標：理解 MDL（Memory Descriptor List）的結構與用途，掌握鎖定用戶緩衝區和建立核心映射的正確流程。

## 為什麼需要 MDL

`ExAllocatePoolWithTag` 分配的是**虛擬記憶體**。虛擬地址和實體地址之間隔著頁表，同一塊虛擬記憶體的實體頁可能不連續。

某些操作需要知道**實體地址**：
- DMA（Direct Memory Access）：硬體需要實體地址直接寫記憶體
- 大數據傳輸：避免複製，讓核心直接存取用戶緩衝區的物理頁
- 共享記憶體：把同一塊實體記憶體映射到多個虛擬地址

**MDL（Memory Descriptor List）** 是 Windows 核心描述一段虛擬記憶體對應的實體頁列表的數據結構。

## MDL 結構

```c
typedef struct _MDL {
    struct _MDL* Next;       // MDL 鏈（多個 MDL 組成鏈表）
    CSHORT       Size;       // MDL 結構本身的大小
    CSHORT       MdlFlags;   // 狀態 flags
    struct _EPROCESS* Process;  // 所屬進程（NULL = 核心）
    PVOID        MappedSystemVa;  // 核心虛擬地址映射（如果有）
    PVOID        StartVa;    // 虛擬地址的起始頁
    ULONG        ByteCount;  // 描述的字節數
    ULONG        ByteOffset; // 起始頁內的偏移
    // MDL 之後緊跟著 PFN 陣列（物理頁幀號）
} MDL, *PMDL;
```

## 典型 MDL 使用流程

### 場景：把用戶緩衝區鎖定並映射到核心

```c
NTSTATUS LockAndMapUserBuffer(
    PVOID  userBuffer,
    SIZE_T bufferSize,
    PMDL*  outMdl,
    PVOID* outKernelVa)
{
    // 1. 分配 MDL（描述用戶緩衝區的物理頁）
    PMDL mdl = IoAllocateMdl(
        userBuffer,     // 用戶態虛擬地址
        (ULONG)bufferSize,
        FALSE,          // SecondaryBuffer = FALSE（主 MDL）
        FALSE,          // ChargeQuota
        NULL);          // 不附加到 IRP

    if (!mdl) return STATUS_INSUFFICIENT_RESOURCES;

    // 2. 鎖定物理頁（防止 OS 把這些頁換出或重用）
    __try {
        MmProbeAndLockPages(
            mdl,
            UserMode,    // 確認是用戶態地址
            IoReadAccess); // 我要讀它（也可以 IoWriteAccess, IoModifyAccess）
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        IoFreeMdl(mdl);
        return GetExceptionCode();
    }

    // 3. 建立核心虛擬地址映射
    PVOID kernelVa = MmGetSystemAddressForMdlSafe(
        mdl, 
        NormalPagePriority | MdlMappingNoExecute);  // 不允許執行（安全）
    
    if (!kernelVa) {
        MmUnlockPages(mdl);
        IoFreeMdl(mdl);
        return STATUS_INSUFFICIENT_RESOURCES;
    }

    *outMdl = mdl;
    *outKernelVa = kernelVa;
    return STATUS_SUCCESS;
}

void UnlockAndFreeBuffer(PMDL mdl)
{
    // 順序很重要：先 UnlockPages，再 FreeMdl
    MmUnlockPages(mdl);
    IoFreeMdl(mdl);
}
```

### 存取映射後的緩衝區

```c
// 映射完成後，kernelVa 可以像普通指針一樣存取
// 對它的讀寫直接操作實體記憶體（繞過用戶態頁表）
PULONG data = (PULONG)kernelVa;
for (ULONG i = 0; i < bufferSize / sizeof(ULONG); i++) {
    data[i] = i * 2;  // 直接寫入用戶緩衝區的物理頁
}
```

## IRP 中的 MDL：Direct I/O

Ch 10 提到 Direct I/O（`DO_DIRECT_IO` flag）時，I/O Manager 自動建立 MDL：

```c
// Device Object 設定 Direct I/O 模式
gDeviceObject->Flags |= DO_DIRECT_IO;
gDeviceObject->Flags &= ~DO_BUFFERED_IO;

// Dispatch Read handler
NTSTATUS DispatchRead(PDEVICE_OBJECT DeviceObject, PIRP Irp)
{
    // I/O Manager 已建立 MDL，鎖定用戶讀取緩衝區
    PMDL mdl = Irp->MdlAddress;
    if (!mdl) {
        Irp->IoStatus.Status = STATUS_INVALID_PARAMETER;
        IoCompleteRequest(Irp, IO_NO_INCREMENT);
        return STATUS_INVALID_PARAMETER;
    }

    // 建立核心映射
    PVOID buf = MmGetSystemAddressForMdlSafe(mdl, 
                    NormalPagePriority | MdlMappingNoExecute);
    if (!buf) {
        Irp->IoStatus.Status = STATUS_INSUFFICIENT_RESOURCES;
        IoCompleteRequest(Irp, IO_NO_INCREMENT);
        return STATUS_INSUFFICIENT_RESOURCES;
    }

    PIO_STACK_LOCATION stack = IoGetCurrentIrpStackLocation(Irp);
    ULONG length = stack->Parameters.Read.Length;

    // 直接寫入用戶緩衝區（零複製）
    static const CHAR data[] = "Direct I/O data";
    ULONG toCopy = min(length, sizeof(data) - 1);
    RtlCopyMemory(buf, data, toCopy);

    Irp->IoStatus.Status      = STATUS_SUCCESS;
    Irp->IoStatus.Information = toCopy;
    IoCompleteRequest(Irp, IO_NO_INCREMENT);
    return STATUS_SUCCESS;
    // 注意：不需要 MmUnlockPages，I/O Manager 在 IoCompleteRequest 後自動處理
}
```

## 自己建立 MDL 共享核心記憶體給用戶態

高性能場景：驅動和用戶態共享一塊記憶體，避免每次 IOCTL 都複製資料。

```c
// 在 DriverEntry 分配並共享
PVOID  gSharedKernelVa = NULL;
PMDL   gSharedMdl      = NULL;

NTSTATUS SetupSharedMemory()
{
    // 1. 在非分頁池分配記憶體
    gSharedKernelVa = ExAllocatePoolWithTag(NonPagedPoolNx, PAGE_SIZE, 'Shrd');
    if (!gSharedKernelVa) return STATUS_INSUFFICIENT_RESOURCES;
    RtlZeroMemory(gSharedKernelVa, PAGE_SIZE);

    // 2. 建立 MDL 描述這塊記憶體
    gSharedMdl = IoAllocateMdl(gSharedKernelVa, PAGE_SIZE, FALSE, FALSE, NULL);
    if (!gSharedMdl) {
        ExFreePoolWithTag(gSharedKernelVa, 'Shrd');
        return STATUS_INSUFFICIENT_RESOURCES;
    }

    // 3. 建立 MDL（核心記憶體不需要 MmProbeAndLockPages，用 MmBuildMdlForNonPagedPool）
    MmBuildMdlForNonPagedPool(gSharedMdl);
    
    return STATUS_SUCCESS;
}

// 在 IOCTL handler 中把共享記憶體映射到用戶態
NTSTATUS HandleShareMemory(PIRP Irp)
{
    // 把 MDL 描述的實體頁映射到當前進程的用戶態虛擬空間
    PVOID userVa = MmMapLockedPagesSpecifyCache(
        gSharedMdl,
        UserMode,           // 映射到用戶空間
        MmNonCached,
        NULL,               // 讓系統選地址
        FALSE,
        NormalPagePriority);
    
    if (!userVa) return STATUS_INSUFFICIENT_RESOURCES;

    // 把用戶態地址返回給用戶
    *(PVOID*)Irp->AssociatedIrp.SystemBuffer = userVa;
    Irp->IoStatus.Information = sizeof(PVOID);
    return STATUS_SUCCESS;
}
```

## MDL Flags 常見值

| Flag | 含義 |
|------|------|
| `MDL_MAPPED_TO_SYSTEM_VA` | 已用 `MmGetSystemAddressForMdlSafe` 映射 |
| `MDL_PAGES_LOCKED` | 物理頁已用 `MmProbeAndLockPages` 鎖定 |
| `MDL_SOURCE_IS_NONPAGED_POOL` | 來自非分頁池（`MmBuildMdlForNonPagedPool` 設定）|
| `MDL_IO_PAGE_READ` | 由 I/O Manager 建立（不要自己手動解鎖）|

## 常見錯誤

**1. 不加 `__try/__except` 就呼叫 `MmProbeAndLockPages`**

用戶傳進來的地址可能是無效的，`MmProbeAndLockPages` 會拋出例外。不捕捉就 BSOD。

**2. 忘記 `MmUnlockPages` 就 `IoFreeMdl`**

`IoFreeMdl` 不會自動解鎖物理頁。鎖定的頁永遠無法被回收 → 記憶體洩漏。

**3. `MmGetSystemAddressForMdlSafe` 返回 NULL 沒有處理**

虛擬地址空間可能耗盡（PTE 空間），返回 NULL 是正常的，必須處理。

## 自我檢核

- [ ] MDL 描述虛擬記憶體對應的物理頁列表，記錄物理頁幀號（PFN）
- [ ] `IoAllocateMdl` → `MmProbeAndLockPages` → `MmGetSystemAddressForMdlSafe` 的正確順序
- [ ] 清理順序：`MmUnlockPages` 先於 `IoFreeMdl`（順序錯誤是常見 bug）
- [ ] Direct I/O 模式：I/O Manager 自動建立 MDL，不需要手動鎖頁
- [ ] `MmBuildMdlForNonPagedPool`：核心記憶體的 MDL 初始化方式

→ [Ch 13 同步基元](./13-synchronization.md)
