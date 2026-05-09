# Ch 8 — IRP 基礎

> 目標：深入理解 IRP 的結構、I/O Stack Location 的角色，以及 Device Stack 的 IRP 傳遞機制。

## IRP 是什麼

**IRP（I/O Request Packet）** 是 Windows I/O 子系統的核心數據結構——每個 I/O 操作（讀/寫/IOCTL/PnP/Power）都對應一個 IRP。

類比：Linux 的 `struct bio`（塊設備 I/O）或 `struct sk_buff`（網路），但 IRP 更通用，覆蓋所有 I/O 類型。

```
用戶態 ReadFile(handle, buf, 4096, ...)
          ↓
I/O Manager 分配並初始化一個 IRP
          ↓
IRP 沿 Device Stack 向下傳遞
          ↓
最底層的驅動完成操作
          ↓
IRP 的完成狀態沿 Stack 向上返回
          ↓
用戶態 ReadFile 返回
```

## IRP 結構

```c
typedef struct _IRP {
    // ── 基本資訊 ─────────────────────────────────────
    CSHORT              Type;          // = IO_TYPE_IRP（確認是 IRP 不是其他東西）
    USHORT              Size;          // 結構大小（包含 IO_STACK_LOCATION 陣列）
    
    // ── 記憶體緩衝區 ────────────────────────────────
    PMDL                MdlAddress;    // Buffered I/O: NULL; Direct I/O: MDL 指針
    ULONG               Flags;
    union {
        struct _IRP*    MasterIrp;     // Associated IRP（用於分散/聚合 I/O）
        PVOID           SystemBuffer;  // Buffered I/O 的核心緩衝區
    } AssociatedIrp;
    
    // ── 連結 ────────────────────────────────────────
    LIST_ENTRY          ThreadListEntry;  // 串接到執行緒的 IRP 列表
    
    // ── 完成狀態 ─────────────────────────────────────
    IO_STATUS_BLOCK     IoStatus;       // Status + Information（已傳輸的 bytes）
    
    KPROCESSOR_MODE     RequestorMode;  // KernelMode 或 UserMode
    BOOLEAN             PendingReturned; // 是否 IoMarkIrpPending 被呼叫過
    
    // ── 完成常式 ─────────────────────────────────────
    PDRIVER_CANCEL      CancelRoutine; // 如果設備支援取消
    PVOID               UserBuffer;    // 用戶態緩衝區（Direct I/O 的目標）
    
    // ── 尾部（實際上是彈性陣列）───────────────────────
    union {
        struct {
            // 用於非同步 I/O
            KDEVICE_QUEUE_ENTRY DeviceQueueEntry;
            PVOID              DriverContext[4];  // 驅動私有
            PETHREAD           Thread;            // 發出請求的執行緒
            LIST_ENTRY         ListEntry;
            PIO_STACK_LOCATION CurrentStackLocation;  // ← 當前 Stack Location 指針
        } Overlay;
    } Tail;
    
    // 緊接著 IRP 頭部之後：IO_STACK_LOCATION 陣列
    // （大小 = Device Stack 深度）
} IRP;
```

最重要的兩個部分：`IoStatus` 和 `CurrentStackLocation`。

## IO_STACK_LOCATION：每個驅動的「工作單」

**IRP 是請求本身，IO_STACK_LOCATION 是給每層驅動的具體指示。**

Device Stack 有幾層驅動，IRP 裡就有幾個 IO_STACK_LOCATION：

```
Device Stack（從上到下）:
  Filter Driver    → IO_STACK_LOCATION[2]（CurrentStackLocation）
  Function Driver  → IO_STACK_LOCATION[1]（低一層）
  Bus Driver       → IO_STACK_LOCATION[0]（底層）
```

IRP 從上層傳到下層時，`CurrentStackLocation` 指針遞減（向較低的 slot 移動）。

```c
typedef struct _IO_STACK_LOCATION {
    UCHAR MajorFunction;    // IRP_MJ_READ, IRP_MJ_WRITE, IRP_MJ_DEVICE_CONTROL...
    UCHAR MinorFunction;    // 例如 IRP_MN_START_DEVICE (PnP 的子命令)
    UCHAR Flags;
    
    // 每個 MajorFunction 各自的參數
    union {
        // IRP_MJ_READ / IRP_MJ_WRITE
        struct {
            ULONG Length;           // 要讀/寫的 bytes 數
            ULONG_PTR Key;
            LARGE_INTEGER ByteOffset; // 偏移（對 File 有意義）
        } Read;
        struct {
            ULONG Length;
            ULONG_PTR Key;
            LARGE_INTEGER ByteOffset;
        } Write;
        
        // IRP_MJ_DEVICE_CONTROL / IRP_MJ_INTERNAL_DEVICE_CONTROL
        struct {
            ULONG OutputBufferLength;
            ULONG InputBufferLength;
            ULONG IoControlCode;    // IOCTL code
            PVOID Type3InputBuffer; // METHOD_NEITHER 才用
        } DeviceIoControl;
        
        // IRP_MJ_PNP
        struct {
            union _POWER_STATE  PowerState;
            ...
        } Power;
        
        // 其他...
    } Parameters;
    
    PDEVICE_OBJECT  DeviceObject;    // 這層 Stack 對應的設備
    PFILE_OBJECT    FileObject;      // 關聯的 File Object
    
    // Completion Routine（這層驅動設定，IRP 完成時被呼叫）
    PIO_COMPLETION_ROUTINE CompletionRoutine;
    PVOID Context;                   // 傳給 CompletionRoutine 的參數
    
} IO_STACK_LOCATION;
```

## 取得當前 Stack Location

```c
NTSTATUS DispatchRead(PDEVICE_OBJECT DeviceObject, PIRP Irp)
{
    // 取得當前層的 Stack Location
    PIO_STACK_LOCATION stack = IoGetCurrentIrpStackLocation(Irp);
    
    ULONG bytesToRead = stack->Parameters.Read.Length;
    LARGE_INTEGER offset = stack->Parameters.Read.ByteOffset;
    
    DbgPrint("Read request: %lu bytes at offset %lld\n",
             bytesToRead, offset.QuadPart);
    
    // ...
}
```

## Device Stack 與 IRP 傳遞

典型的 Device Stack 有多層驅動疊在一起：

```
IRP 進來
    ↓
Upper Filter Driver（攔截/修改）
    ↓（IoCallDriver → 傳給下一層）
Function Driver（真正的業務邏輯）
    ↓（IoCallDriver → 傳給下一層）
Lower Filter Driver
    ↓（IoCallDriver → 傳給最底層）
Bus Driver（底層硬體 I/O）
    ↓（IoCompleteRequest → 開始往上完成）
Lower Filter Completion Routine
    ↓
Function Driver Completion Routine
    ↓
Upper Filter Completion Routine
    ↓
IRP 完成，用戶態 ReadFile 返回
```

**Filter Driver** 的典型做法——攔截、修改，然後傳給下一層：

```c
NTSTATUS FilterDispatchRead(PDEVICE_OBJECT DeviceObject, PIRP Irp)
{
    PDEVICE_EXTENSION ext = DeviceObject->DeviceExtension;
    
    // 設定 Completion Routine（可選）
    IoCopyCurrentIrpStackLocationToNext(Irp);
    IoSetCompletionRoutine(Irp, FilterReadCompletion, NULL, TRUE, TRUE, TRUE);
    
    // 傳給下一層 Device（ext->LowerDeviceObject）
    return IoCallDriver(ext->LowerDeviceObject, Irp);
    // 注意：IoCallDriver 之後，IRP 可能已被完成或仍在處理，不要再存取它
}

NTSTATUS FilterReadCompletion(
    PDEVICE_OBJECT DeviceObject, PIRP Irp, PVOID Context)
{
    // IRP 從下層返回，可以看結果並修改
    DbgPrint("Read completed, status: 0x%X, bytes: %llu\n",
             Irp->IoStatus.Status, Irp->IoStatus.Information);
    
    // 返回 STATUS_CONTINUE_COMPLETION 繼續往上完成
    return STATUS_CONTINUE_COMPLETION;
}
```

## 三種 I/O 模式

IRP 攜帶用戶緩衝區的方式有三種，由 Device Object 的 Flags 決定：

### Buffered I/O（最安全，最常用）

```
DO_BUFFERED_IO flag 設定時：
I/O Manager 分配核心緩衝區，從用戶緩衝區複製進去
IRP→AssociatedIrp.SystemBuffer 指向核心緩衝區
（用戶緩衝區地址在 IRP→UserBuffer）
完成時，I/O Manager 把核心緩衝區複製回用戶緩衝區
```

驅動只需存取 `SystemBuffer`，I/O Manager 保證它可安全存取。

### Direct I/O（高效，用於大數據）

```
DO_DIRECT_IO flag 設定時：
I/O Manager 把用戶緩衝區的物理頁鎖住，建立 MDL
IRP→MdlAddress 指向 MDL
驅動用 MmGetSystemAddressForMdlSafe() 把 MDL 映射到核心地址
完成時，I/O Manager 解鎖物理頁
```

避免了複製，適合大量資料傳輸。

### Neither I/O（危險，需要小心）

```
沒有設定任何 flag 時：
IRP→UserBuffer = 用戶態虛擬地址（直接！）
驅動必須自己驗證和鎖定
```

這是最危險的模式。IOCTL 的 `METHOD_NEITHER` 用這個，是許多 IOCTL 漏洞的根源（Ch 27 詳述）。

## 在 WinDbg 檢視 IRP

設個 breakpoint 在 dispatch routine，然後 `!irp` 查看：

```
kd> !irp ffffe00012345678
Irp is active with 3 stacks 2 is current (= 0xffffe000aabb0000)
 No mdl for this Irp
 Thread 00000000:  Irp stack trace.
     cmd  flg cl Device   File     Completion-Context
>[IRP_MJ_DEVICE_CONTROL(e), N 0]
          ffffe00011223344 ffffe00099887766 00000000-00000000    
        \Driver\MyDriver
        Parameters: 000012340000abcd 00000010 00000010 00000000

  [IRP_MJ_DEVICE_CONTROL(e), N 0]
          ...
```

## 自我檢核

- [ ] IRP 結構的三個關鍵部分：`AssociatedIrp.SystemBuffer`、`IoStatus`、Stack Location 陣列
- [ ] `IoGetCurrentIrpStackLocation()` 取得當前層的參數（MajorFunction、Parameters）
- [ ] Device Stack 多層驅動共用一個 IRP，每層有自己的 Stack Location
- [ ] `IoCallDriver()` 把 IRP 傳給下一層；`IoCompleteRequest()` 從底層往上完成
- [ ] Buffered I/O：I/O Manager 複製緩衝區；Direct I/O：MDL 鎖定物理頁；Neither：危險的直接指針
- [ ] `IoCopyCurrentIrpStackLocationToNext` + `IoSetCompletionRoutine` = Filter Driver 的基本模式

→ [Ch 9 Dispatch Routines](./09-dispatch-routines.md)
