# Ch 6 — WDM 骨架

> 目標：理解 WDM 驅動的完整生命週期，寫出一個帶設備物件和符號連結的骨架驅動，能從用戶態開啟它。

## WDM 生命週期

```
載入（sc start / PnP）
    → DriverEntry()       ← 初始化，建立 Device Object
    → IRP_MJ_CREATE       ← 用戶態 CreateFile() 時
    → IRP_MJ_CLOSE        ← 用戶態 CloseHandle() 時
    → IRP_MJ_READ         ← ReadFile()
    → IRP_MJ_WRITE        ← WriteFile()
    → IRP_MJ_DEVICE_CTRL  ← DeviceIoControl()
    → ...（其他 dispatch routines）
卸載（sc stop）
    → DriverUnload()      ← 清理，刪除 Device Object
```

IOCTL 是驅動和用戶態溝通最常用的方式（Ch 10 詳述）。

## DriverEntry

`DriverEntry` 是驅動的入口點，類似 `main()`，但返回 NTSTATUS：

```c
NTSTATUS DriverEntry(
    PDRIVER_OBJECT  DriverObject,    // 這個驅動的 DRIVER_OBJECT
    PUNICODE_STRING RegistryPath     // 驅動在 Registry 的路徑
);
```

**它只呼叫一次**，在驅動載入時。需要做：
1. 設定 `DriverObject->DriverUnload`
2. 設定 Dispatch Routines（IRP handlers）
3. 建立 Device Object
4. 建立符號連結

## 完整 WDM 骨架

```c
// SkelDriver.c
#include <ntddk.h>

// ─── 常數定義 ──────────────────────────────────────────
#define DEVICE_NAME     L"\\Device\\SkelDriver"
#define SYMLINK_NAME    L"\\DosDevices\\SkelDriver"
#define POOL_TAG        'lekS'

// ─── 前向宣告 ──────────────────────────────────────────
DRIVER_UNLOAD       SkelDriverUnload;
DRIVER_DISPATCH     SkelDispatchCreate;
DRIVER_DISPATCH     SkelDispatchClose;
DRIVER_DISPATCH     SkelDispatchDeviceControl;

// 全域設備物件（生產代碼應放在 Device Extension，不要用全域）
PDEVICE_OBJECT  gDeviceObject = NULL;

// ─── DriverEntry ───────────────────────────────────────
NTSTATUS DriverEntry(
    PDRIVER_OBJECT  DriverObject,
    PUNICODE_STRING RegistryPath)
{
    UNREFERENCED_PARAMETER(RegistryPath);

    NTSTATUS         status;
    UNICODE_STRING   deviceName  = RTL_CONSTANT_STRING(DEVICE_NAME);
    UNICODE_STRING   symlinkName = RTL_CONSTANT_STRING(SYMLINK_NAME);
    BOOLEAN          symlinkCreated = FALSE;

    DbgPrint("[SkelDriver] DriverEntry called\n");

    // 1. 設定 Unload handler
    DriverObject->DriverUnload = SkelDriverUnload;

    // 2. 設定 Dispatch Routines
    //    未設定的 handler 預設是 IopInvalidDeviceRequest（返回 STATUS_INVALID_DEVICE_REQUEST）
    DriverObject->MajorFunction[IRP_MJ_CREATE]         = SkelDispatchCreate;
    DriverObject->MajorFunction[IRP_MJ_CLOSE]          = SkelDispatchClose;
    DriverObject->MajorFunction[IRP_MJ_DEVICE_CONTROL] = SkelDispatchDeviceControl;

    // 3. 建立 Device Object
    status = IoCreateDevice(
        DriverObject,               // 所屬 Driver
        0,                          // Device Extension 大小（後面詳述）
        &deviceName,                // 設備名稱（核心命名空間）
        FILE_DEVICE_UNKNOWN,        // 設備類型（自定義用 UNKNOWN）
        FILE_DEVICE_SECURE_OPEN,    // 設備特性 flag
        FALSE,                      // Exclusive（同時只有一個 Handle）
        &gDeviceObject);

    if (!NT_SUCCESS(status)) {
        DbgPrint("[SkelDriver] IoCreateDevice failed: 0x%X\n", status);
        return status;
    }

    // 設定 I/O 模式（Buffered I/O：I/O Manager 幫我們複製用戶緩衝區）
    gDeviceObject->Flags |= DO_BUFFERED_IO;
    // 清除 DO_DEVICE_INITIALIZING（告訴 I/O Manager 初始化完成）
    gDeviceObject->Flags &= ~DO_DEVICE_INITIALIZING;

    // 4. 建立符號連結（讓用戶態能用 \\.\SkelDriver 存取）
    status = IoCreateSymbolicLink(&symlinkName, &deviceName);
    if (!NT_SUCCESS(status)) {
        DbgPrint("[SkelDriver] IoCreateSymbolicLink failed: 0x%X\n", status);
        IoDeleteDevice(gDeviceObject);
        gDeviceObject = NULL;
        return status;
    }

    DbgPrint("[SkelDriver] Initialized successfully\n");
    return STATUS_SUCCESS;
}

// ─── DriverUnload ───────────────────────────────────────
void SkelDriverUnload(PDRIVER_OBJECT DriverObject)
{
    UNREFERENCED_PARAMETER(DriverObject);

    UNICODE_STRING symlinkName = RTL_CONSTANT_STRING(SYMLINK_NAME);

    DbgPrint("[SkelDriver] Unloading\n");

    // 先刪符號連結，再刪設備物件（順序很重要）
    IoDeleteSymbolicLink(&symlinkName);
    
    if (gDeviceObject) {
        IoDeleteDevice(gDeviceObject);
        gDeviceObject = NULL;
    }
}

// ─── IRP_MJ_CREATE：用戶態 CreateFile() 時呼叫 ─────────
NTSTATUS SkelDispatchCreate(PDEVICE_OBJECT DeviceObject, PIRP Irp)
{
    UNREFERENCED_PARAMETER(DeviceObject);
    DbgPrint("[SkelDriver] IRP_MJ_CREATE\n");

    // 完成 IRP：成功
    Irp->IoStatus.Status      = STATUS_SUCCESS;
    Irp->IoStatus.Information = 0;
    IoCompleteRequest(Irp, IO_NO_INCREMENT);
    return STATUS_SUCCESS;
}

// ─── IRP_MJ_CLOSE：CloseHandle() 時呼叫 ────────────────
NTSTATUS SkelDispatchClose(PDEVICE_OBJECT DeviceObject, PIRP Irp)
{
    UNREFERENCED_PARAMETER(DeviceObject);
    DbgPrint("[SkelDriver] IRP_MJ_CLOSE\n");

    Irp->IoStatus.Status      = STATUS_SUCCESS;
    Irp->IoStatus.Information = 0;
    IoCompleteRequest(Irp, IO_NO_INCREMENT);
    return STATUS_SUCCESS;
}

// ─── IRP_MJ_DEVICE_CONTROL：DeviceIoControl() 時呼叫 ────
NTSTATUS SkelDispatchDeviceControl(PDEVICE_OBJECT DeviceObject, PIRP Irp)
{
    UNREFERENCED_PARAMETER(DeviceObject);

    PIO_STACK_LOCATION ioStack = IoGetCurrentIrpStackLocation(Irp);
    ULONG ioControlCode = ioStack->Parameters.DeviceIoControl.IoControlCode;

    DbgPrint("[SkelDriver] IOCTL: 0x%X\n", ioControlCode);

    // 目前全部返回不支援
    Irp->IoStatus.Status      = STATUS_INVALID_DEVICE_REQUEST;
    Irp->IoStatus.Information = 0;
    IoCompleteRequest(Irp, IO_NO_INCREMENT);
    return STATUS_INVALID_DEVICE_REQUEST;
}
```

## 關鍵 API 說明

### IoCreateDevice

```c
NTSTATUS IoCreateDevice(
    PDRIVER_OBJECT  DriverObject,
    ULONG           DeviceExtensionSize,  // 驅動私有資料的大小
    PUNICODE_STRING DeviceName,           // NULL = 匿名設備
    DEVICE_TYPE     DeviceType,
    ULONG           DeviceCharacteristics,
    BOOLEAN         Exclusive,
    PDEVICE_OBJECT* DeviceObject          // 輸出
);
```

**Device Extension**：每個 Device Object 後面可以附一塊私有記憶體，用來存驅動自己的資料（替代全域變數）：

```c
typedef struct _DEVICE_EXTENSION {
    PDEVICE_OBJECT  DeviceObject;
    UNICODE_STRING  DeviceName;
    UNICODE_STRING  SymLinkName;
    // 其他驅動私有資料
} DEVICE_EXTENSION, *PDEVICE_EXTENSION;

// 建立時指定大小
IoCreateDevice(DriverObject, sizeof(DEVICE_EXTENSION), ...);

// 使用
PDEVICE_EXTENSION ext = (PDEVICE_EXTENSION)DeviceObject->DeviceExtension;
```

**生產代碼必須用 Device Extension，不用全域變數**。全域變數在多設備的驅動中會出 bug。

### IoCompleteRequest

**每個 IRP 必須被 Complete 恰好一次**。忘記呼叫 `IoCompleteRequest` → I/O 永遠掛著；呼叫超過一次 → BSOD。

```c
IoCompleteRequest(Irp, IO_NO_INCREMENT);
// 第二個參數：Priority Boost，用 IO_NO_INCREMENT 即可
// IO_DISK_INCREMENT, IO_NETWORK_INCREMENT 用在特定設備
```

## 用戶態測試程式

```c
// test_skel.c（用戶態）
#include <windows.h>
#include <stdio.h>

int main() {
    // 開啟驅動（對應 IRP_MJ_CREATE）
    HANDLE h = CreateFile(
        L"\\\\.\\SkelDriver",
        GENERIC_READ | GENERIC_WRITE,
        0, NULL,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,
        NULL);

    if (h == INVALID_HANDLE_VALUE) {
        printf("CreateFile failed: %d\n", GetLastError());
        return 1;
    }

    printf("Driver opened successfully!\n");

    // 發一個 IOCTL（目前驅動會回 ERROR_INVALID_FUNCTION）
    DWORD bytesReturned;
    DeviceIoControl(h, 0x12345678, NULL, 0, NULL, 0, &bytesReturned, NULL);
    printf("IOCTL result: %d\n", GetLastError());

    // 關閉（對應 IRP_MJ_CLOSE）
    CloseHandle(h);
    printf("Done\n");
    return 0;
}
```

WinDbg 輸出應該是：
```
[SkelDriver] IRP_MJ_CREATE
[SkelDriver] IOCTL: 0x12345678
[SkelDriver] IRP_MJ_CLOSE
```

## .inf 和 .cat 文件（生產驅動）

開發時用 `sc create` 直接安裝，省略 INF。生產驅動需要 `.inf`（安裝腳本）和 `.cat`（數位簽章目錄）。這門課以開發和研究為主，跳過 INF 的詳細說明。

## 常見錯誤

**忘記清除 DO_DEVICE_INITIALIZING**：

Device Object 建立後預設帶有 `DO_DEVICE_INITIALIZING` flag，表示「還在初始化」。某些 I/O 操作在這個 flag 存在時會被拒絕。需要手動清除：

```c
gDeviceObject->Flags &= ~DO_DEVICE_INITIALIZING;
```

**DriverUnload 沒有清理**：

如果 `DriverUnload` 沒有刪除 Device Object 和 Symbolic Link，下次再 `sc start` 時 `IoCreateDevice` / `IoCreateSymbolicLink` 會返回 `STATUS_OBJECT_NAME_COLLISION`。

## 自我檢核

- [ ] `DriverEntry` 的三個主要任務：設定 Unload / Dispatch Routines / 建立 Device + Symlink
- [ ] `DO_BUFFERED_IO` 讓 I/O Manager 幫你複製用戶緩衝區
- [ ] 清除 `DO_DEVICE_INITIALIZING` flag
- [ ] 每個 IRP 必須 `IoCompleteRequest` 恰好一次
- [ ] Device Extension 是驅動私有資料的正確存放位置，不用全域變數
- [ ] `DriverUnload` 必須反向清理：先 `IoDeleteSymbolicLink`，再 `IoDeleteDevice`

→ [Ch 7 KMDF 入門](./07-kmdf-intro.md)
