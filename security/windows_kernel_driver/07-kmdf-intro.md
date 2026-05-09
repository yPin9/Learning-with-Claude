# Ch 7 — KMDF 入門

> 目標：理解 KMDF（WDF）框架解決了 WDM 的哪些痛點，能用 KMDF 寫出和 Ch 6 功能相同但代碼量更少的驅動。

## 為什麼 WDM 不夠好

用 WDM 開發一個完整的 PnP 驅動（支援安全移除硬體、電源管理）需要實作幾百行 boilerplate：

```c
// WDM 的 IRP_MJ_POWER 處理（每個驅動都要寫這堆）
NTSTATUS DispatchPower(PDEVICE_OBJECT DeviceObject, PIRP Irp) {
    PIO_STACK_LOCATION stack = IoGetCurrentIrpStackLocation(Irp);
    switch (stack->MinorFunction) {
        case IRP_MN_SET_POWER: ...
        case IRP_MN_QUERY_POWER: ...
        case IRP_MN_POWER_SEQUENCE: ...
        case IRP_MN_WAIT_WAKE: ...
    }
    // 還要記得 PoStartNextPowerIrp
    // 還要記得 PoCallDriver 而不是 IoCallDriver
    // 漏掉任何一個 = BSOD 或設備功能異常
}
```

這是複製貼上的代碼，每個驅動都一樣，而且極容易寫錯。

**KMDF（Kernel-Mode Driver Framework）** 是微軟在 2006 年推出的框架，把 PnP、電源管理、IRP 生命週期、同步、記憶體管理全部封裝好，讓驅動作者只寫業務邏輯。

## WDF 物件模型

KMDF 引入自己的物件層次結構：

```
WDFDRIVER          ─── 頂層，對應 DRIVER_OBJECT
  └── WDFDEVICE    ─── 設備，對應 DEVICE_OBJECT
        ├── WDFQUEUE         ─── I/O 佇列（管理 Request 的流量）
        │     └── WDFREQUEST ─── 一個 I/O 請求（對應 IRP）
        ├── WDFINTERRUPT     ─── 中斷
        ├── WDFMEMORY        ─── 記憶體緩衝區
        ├── WDFSPINLOCK      ─── SpinLock
        └── WDFTIMER         ─── Timer
```

WDF 物件都有**父子關係**：父物件被刪除時，子物件自動被刪除。這解決了 WDM 最常見的資源洩漏問題。

所有 WDF 物件用 `WdfObjectDelete()` 或自動隨父物件清理。不需要手動追蹤和釋放。

## 第一個 KMDF 驅動

```c
// KmdfSkel.c
#include <wdf.h>

// ─── 自定義 Context（替代 WDM 的 Device Extension）────
typedef struct _DEVICE_CONTEXT {
    ULONG privateData;
    // 驅動私有資料
} DEVICE_CONTEXT, *PDEVICE_CONTEXT;

// WDF 宏：定義 Context 存取器函式
WDF_DECLARE_CONTEXT_TYPE_WITH_NAME(DEVICE_CONTEXT, GetDeviceContext)

// ─── 前向宣告 ────────────────────────────────────────
EVT_WDF_DRIVER_DEVICE_ADD       EvtDeviceAdd;
EVT_WDF_IO_QUEUE_IO_DEVICE_CONTROL EvtIoDeviceControl;

// ─── DriverEntry ─────────────────────────────────────
NTSTATUS DriverEntry(
    PDRIVER_OBJECT  DriverObject,
    PUNICODE_STRING RegistryPath)
{
    WDF_DRIVER_CONFIG config;

    // WDF_DRIVER_CONFIG 填入 EvtDeviceAdd callback
    WDF_DRIVER_CONFIG_INIT(&config, EvtDeviceAdd);

    // WdfDriverCreate：建立 WDFDRIVER 物件，替代手動設定 DriverObject
    return WdfDriverCreate(DriverObject, RegistryPath, 
                           WDF_NO_OBJECT_ATTRIBUTES,
                           &config, WDF_NO_HANDLE);
    // WDF 自動處理 DriverUnload：WDFDRIVER 被刪時自動清理
}

// ─── EvtDeviceAdd：每次發現新設備時呼叫 ────────────────
NTSTATUS EvtDeviceAdd(
    WDFDRIVER       Driver,
    PWDFDEVICE_INIT DeviceInit)
{
    UNREFERENCED_PARAMETER(Driver);

    NTSTATUS           status;
    WDFDEVICE          device;
    WDF_OBJECT_ATTRIBUTES deviceAttributes;

    // 設定 Device Context 的大小和型別
    WDF_OBJECT_ATTRIBUTES_INIT_CONTEXT_TYPE(&deviceAttributes, DEVICE_CONTEXT);

    // 建立設備（WDF 自動建立 Device Object + 符號連結，如果需要）
    status = WdfDeviceCreate(&DeviceInit, &deviceAttributes, &device);
    if (!NT_SUCCESS(status)) return status;

    // 建立設備介面（讓用戶態能找到它，比 IoCreateSymbolicLink 更現代）
    // 需要 GUID（用 guidgen.exe 生成）
    // status = WdfDeviceCreateDeviceInterface(device, &SKEL_DEVICE_INTERFACE, NULL);

    // 建立預設 I/O 佇列
    WDF_IO_QUEUE_CONFIG queueConfig;
    WDF_IO_QUEUE_CONFIG_INIT_DEFAULT_QUEUE(
        &queueConfig, 
        WdfIoQueueDispatchParallel);  // 並行分發（vs Sequential）

    queueConfig.EvtIoDeviceControl = EvtIoDeviceControl;

    WDFQUEUE queue;
    status = WdfIoQueueCreate(device, &queueConfig,
                              WDF_NO_OBJECT_ATTRIBUTES, &queue);
    return status;
}

// ─── IOCTL Handler ───────────────────────────────────
void EvtIoDeviceControl(
    WDFQUEUE   Queue,
    WDFREQUEST Request,
    size_t     OutputBufferLength,
    size_t     InputBufferLength,
    ULONG      IoControlCode)
{
    UNREFERENCED_PARAMETER(Queue);
    UNREFERENCED_PARAMETER(OutputBufferLength);
    UNREFERENCED_PARAMETER(InputBufferLength);

    DbgPrint("[KmdfSkel] IOCTL: 0x%X\n", IoControlCode);

    // 完成 Request
    WdfRequestComplete(Request, STATUS_INVALID_DEVICE_REQUEST);
}
```

對比 Ch 6 的 WDM 版本：
- 沒有手動建立 Device Object
- 沒有手動建立 Symbolic Link（用 Device Interface 替代）
- 沒有 DriverUnload（WDF 自動清理）
- 沒有 `IoCompleteRequest`（用 `WdfRequestComplete`）
- PnP 和電源管理全部由 WDF 處理

## I/O 佇列（WDFQUEUE）

KMDF 最重要的概念之一。用戶態的每個 I/O 請求（ReadFile、WriteFile、DeviceIoControl）進來後，先進 Queue 排隊，再按設定的分發策略叫你的 callback。

### 分發策略

```c
// 並行（Parallel）：同時可以有多個請求在你的 callback 裡
WDF_IO_QUEUE_CONFIG_INIT_DEFAULT_QUEUE(&config, WdfIoQueueDispatchParallel);

// 序列（Sequential）：一次只有一個請求
WDF_IO_QUEUE_CONFIG_INIT_DEFAULT_QUEUE(&config, WdfIoQueueDispatchSequential);

// 手動（Manual）：請求進來後不自動給你，要你自己呼叫 WdfIoQueueRetrieveNextRequest()
WDF_IO_QUEUE_CONFIG_INIT(&config, WdfIoQueueDispatchManual);
```

Parallel 是最常用的，但需要你自己做同步（如果多個請求存取同一資源）。

Sequential 由 WDF 保證串行，適合有狀態的操作。

## WDFREQUEST：存取輸入輸出緩衝區

KMDF 用 `WDFREQUEST` 替代 IRP。取緩衝區的 API：

```c
void EvtIoDeviceControl(
    WDFQUEUE Queue, WDFREQUEST Request,
    size_t OutputBufferLength, size_t InputBufferLength,
    ULONG IoControlCode)
{
    NTSTATUS status;
    
    // 取輸入緩衝區（Buffered I/O 模式）
    PVOID inputBuffer;
    size_t inputLen;
    status = WdfRequestRetrieveInputBuffer(Request, sizeof(MY_INPUT), &inputBuffer, &inputLen);
    if (!NT_SUCCESS(status)) {
        WdfRequestComplete(Request, status);
        return;
    }
    
    // 取輸出緩衝區
    PVOID outputBuffer;
    size_t outputLen;
    status = WdfRequestRetrieveOutputBuffer(Request, sizeof(MY_OUTPUT), &outputBuffer, &outputLen);
    if (!NT_SUCCESS(status)) {
        WdfRequestComplete(Request, status);
        return;
    }
    
    // 填入輸出
    MY_OUTPUT* out = (MY_OUTPUT*)outputBuffer;
    out->result = 42;
    
    // 完成請求，告訴 I/O Manager 輸出了多少 bytes
    WdfRequestCompleteWithInformation(Request, STATUS_SUCCESS, sizeof(MY_OUTPUT));
}
```

## Context 的使用

WDF 物件的 Context 是附在物件上的私有資料：

```c
// 建立設備時
WDF_OBJECT_ATTRIBUTES_INIT_CONTEXT_TYPE(&attrs, DEVICE_CONTEXT);
WdfDeviceCreate(&DeviceInit, &attrs, &device);

// 使用 Context（透過宏生成的存取函式）
PDEVICE_CONTEXT ctx = GetDeviceContext(device);
ctx->privateData = 100;

// 在 IOCTL handler 裡拿設備
WDFDEVICE device = WdfIoQueueGetDevice(Queue);
PDEVICE_CONTEXT ctx = GetDeviceContext(device);
```

Request 也可以有自己的 Context，用來在異步 I/O 的 Completion Routine 裡傳資料。

## KMDF vs WDM 對照表

| 概念 | WDM | KMDF |
|------|-----|------|
| 驅動頂層 | `PDRIVER_OBJECT` | `WDFDRIVER` |
| 設備 | `PDEVICE_OBJECT` | `WDFDEVICE` |
| I/O 請求 | `PIRP` | `WDFREQUEST` |
| 私有資料 | Device Extension | Object Context |
| I/O 分發 | MajorFunction dispatch table | WDFQUEUE + callbacks |
| 完成請求 | `IoCompleteRequest()` | `WdfRequestComplete()` |
| 取緩衝區 | 手動算 IRP stack location | `WdfRequestRetrieveInputBuffer()` |
| PnP/Power | 必須手動實作 | WDF 自動處理 |
| 清理資源 | `DriverUnload` 手動 | 父物件刪除自動清理 |

## 何時用 WDM vs KMDF

**用 WDM**：
- 學習底層原理（IRP 結構、I/O stack）
- 寫 Filter Driver（需要精確控制 IRP 傳遞）
- 維護 legacy 驅動

**用 KMDF**：
- 新的功能性驅動
- 需要 PnP / 電源管理
- 希望減少 bug（WDF 幫你處理很多 edge case）

安全研究（Ch 26 以後）通常研究的是 WDM 驅動的漏洞，因為第三方驅動和早期驅動大多是 WDM。

## 自我檢核

- [ ] WDFDRIVER → WDFDEVICE → WDFQUEUE → WDFREQUEST 的層次關係
- [ ] WDF 物件父子關係：父刪除時子自動清理
- [ ] `WDF_IO_QUEUE_CONFIG_INIT_DEFAULT_QUEUE` 設定佇列分發策略
- [ ] `WdfRequestRetrieveInputBuffer` / `WdfRequestRetrieveOutputBuffer` 取緩衝區
- [ ] `WdfRequestComplete` vs `WdfRequestCompleteWithInformation`（後者告訴輸出長度）
- [ ] `WDF_DECLARE_CONTEXT_TYPE_WITH_NAME` 生成 Context 存取函式

→ [Ch 8 IRP 基礎](./08-irp-basics.md)
