# Ch 10 — IOCTL

> 目標：徹底理解 IOCTL code 的編碼格式、四種 Transfer Type 的差異和安全性含義，以及如何正確實作 IOCTL dispatch。

## 為什麼 IOCTL

ReadFile/WriteFile 適合串流型 I/O（像管道、序列埠）。IOCTL（DeviceIoControl）是驅動和用戶態溝通的**命令通道**：用戶傳一個命令碼 + 輸入緩衝區，驅動做事，寫入輸出緩衝區。

幾乎所有非串流型驅動通訊都用 IOCTL，包括：防毒軟體查詢引擎版本、遊戲反作弊發指令、Sysinternals 工具和驅動溝通。

IOCTL 也是**漏洞密度最高的攻擊面**（Ch 27 詳述）。

## IOCTL Code 格式

IOCTL code 是一個 32 位元整數，由四個欄位組成：

```
 31          16  15      14  13      2  1    0
┌──────────────┬──────────┬──────────┬───────┐
│ DeviceType   │  Access  │ Function │Method │
│  (16 bits)   │ (2 bits) │(12 bits) │(2 bits)│
└──────────────┴──────────┴──────────┴───────┘
```

| 欄位 | 含義 |
|------|------|
| DeviceType | 設備類型（FILE_DEVICE_UNKNOWN = 0x22，第三方驅動常用）|
| Access | FILE_ANY_ACCESS(0) / FILE_READ_ACCESS(1) / FILE_WRITE_ACCESS(2) |
| Function | 自定義命令號（0x000–0x7FF 微軟保留，0x800+ 第三方使用）|
| Method | **Transfer Type**（最重要，見下節）|

定義 IOCTL code 用 `CTL_CODE` 宏：

```c
// 格式：CTL_CODE(DeviceType, Function, Method, Access)
#define IOCTL_MY_QUERY \
    CTL_CODE(FILE_DEVICE_UNKNOWN, 0x800, METHOD_BUFFERED, FILE_ANY_ACCESS)

#define IOCTL_MY_COMMAND \
    CTL_CODE(FILE_DEVICE_UNKNOWN, 0x801, METHOD_BUFFERED, FILE_READ_DATA | FILE_WRITE_DATA)

// 分解看：IOCTL_MY_QUERY = (0x22 << 16) | (0 << 14) | (0x800 << 2) | 0
//                        = 0x00222000
```

## 四種 Transfer Type（Method）

**這是最重要的部分，也是最多漏洞的來源。**

### METHOD_BUFFERED（Method = 0）

最安全，最常用。I/O Manager 幫你管緩衝區：

```
用戶傳：InputBuffer(size) + OutputBuffer(size)
I/O Manager：
  1. 分配 max(InputSize, OutputSize) 的核心緩衝區
  2. 複製 InputBuffer → 核心緩衝區
  3. 把核心緩衝區地址放在 IRP→AssociatedIrp.SystemBuffer
  4. 驅動完成 IRP 後，I/O Manager 複製核心緩衝區 → OutputBuffer
```

驅動代碼：
```c
case IOCTL_MY_QUERY: {
    PIO_STACK_LOCATION stack = IoGetCurrentIrpStackLocation(Irp);
    ULONG inLen  = stack->Parameters.DeviceIoControl.InputBufferLength;
    ULONG outLen = stack->Parameters.DeviceIoControl.OutputBufferLength;
    PVOID buf    = Irp->AssociatedIrp.SystemBuffer;  // 輸入和輸出共用

    if (inLen < sizeof(MY_INPUT) || outLen < sizeof(MY_OUTPUT)) {
        status = STATUS_BUFFER_TOO_SMALL;
        break;
    }

    MY_INPUT*  in  = (MY_INPUT*)buf;
    MY_OUTPUT* out = (MY_OUTPUT*)buf;  // 注意：共用！先讀完 in 再寫 out

    ULONG command = in->command;  // 先讀 input
    out->result = DoSomething(command);  // 再寫 output

    Irp->IoStatus.Information = sizeof(MY_OUTPUT);  // 很重要！
    status = STATUS_SUCCESS;
    break;
}
```

### METHOD_IN_DIRECT（Method = 1）

輸入 Buffered，輸出 Direct（MDL）。用於大量輸出（驅動寫 DMA 資料到用戶緩衝區）：

```
InputBuffer  → 核心緩衝區（同 Buffered）
OutputBuffer → I/O Manager 建立 MDL，鎖定物理頁
```

```c
PVOID inputBuf = Irp->AssociatedIrp.SystemBuffer;
PVOID outputBuf = MmGetSystemAddressForMdlSafe(Irp->MdlAddress, NormalPagePriority);
```

### METHOD_OUT_DIRECT（Method = 2）

輸入 Direct（MDL），輸出 Buffered。罕見用法，用於驅動讀取用戶大緩衝區：

### METHOD_NEITHER（Method = 3）

**最危險的模式**。I/O Manager 什麼都不做，直接把用戶態指針傳給驅動：

```c
PVOID inputBuf = stack->Parameters.DeviceIoControl.Type3InputBuffer;
// 這是用戶態虛擬地址！！！
// 1. 它可能是 NULL
// 2. 它可能指向核心地址（攻擊者能讀寫核心記憶體！）
// 3. 它可能在你存取時被用戶端 munmap 掉（TOCTOU）
```

正確處理 METHOD_NEITHER 需要：
```c
PVOID inputBuf = stack->Parameters.DeviceIoControl.Type3InputBuffer;
ULONG inLen    = stack->Parameters.DeviceIoControl.InputBufferLength;

// 1. 確認是用戶態地址（不是核心地址）
if (inputBuf >= MmUserProbeAddress) {
    return STATUS_ACCESS_VIOLATION;
}

// 2. ProbeForRead + __try/__except
__try {
    ProbeForRead(inputBuf, inLen, 1);
    // 複製一份到核心，避免 TOCTOU
    RtlCopyMemory(kernelBuf, inputBuf, inLen);
} __except (EXCEPTION_EXECUTE_HANDLER) {
    return STATUS_ACCESS_VIOLATION;
}
```

METHOD_NEITHER 的漏洞驅動讓攻擊者能傳入 `0xFFFF...` 之類的核心地址，驅動把它當輸入/輸出緩衝區直接讀寫，造成任意核心讀寫。

## 完整 IOCTL Dispatch 實作

```c
// ─── IOCTL Code 定義 ─────────────────────────────────
#define IOCTL_GET_VERSION   CTL_CODE(FILE_DEVICE_UNKNOWN, 0x800, METHOD_BUFFERED, FILE_ANY_ACCESS)
#define IOCTL_SET_CONFIG    CTL_CODE(FILE_DEVICE_UNKNOWN, 0x801, METHOD_BUFFERED, FILE_WRITE_DATA)
#define IOCTL_GET_STATS     CTL_CODE(FILE_DEVICE_UNKNOWN, 0x802, METHOD_BUFFERED, FILE_READ_DATA)

// ─── 共用結構（同時包含在驅動和用戶態程式裡）────────────
#pragma pack(push, 1)
typedef struct _VERSION_INFO {
    ULONG Major;
    ULONG Minor;
    CHAR  BuildString[32];
} VERSION_INFO;

typedef struct _DRIVER_CONFIG {
    ULONG LogLevel;
    BOOLEAN EnableMonitoring;
} DRIVER_CONFIG;

typedef struct _DRIVER_STATS {
    ULONG64 RequestCount;
    ULONG64 ErrorCount;
} DRIVER_STATS;
#pragma pack(pop)

// ─── 全域狀態 ─────────────────────────────────────────
static DRIVER_CONFIG gConfig = {0};
static DRIVER_STATS  gStats  = {0};

// ─── IOCTL Dispatch ───────────────────────────────────
NTSTATUS DispatchDeviceControl(PDEVICE_OBJECT DeviceObject, PIRP Irp)
{
    UNREFERENCED_PARAMETER(DeviceObject);

    PIO_STACK_LOCATION stack = IoGetCurrentIrpStackLocation(Irp);
    ULONG ioCode = stack->Parameters.DeviceIoControl.IoControlCode;
    ULONG inLen  = stack->Parameters.DeviceIoControl.InputBufferLength;
    ULONG outLen = stack->Parameters.DeviceIoControl.OutputBufferLength;
    PVOID buf    = Irp->AssociatedIrp.SystemBuffer;

    NTSTATUS status = STATUS_SUCCESS;
    ULONG_PTR info  = 0;

    InterlockedIncrement64((LONG64*)&gStats.RequestCount);

    switch (ioCode) {
        case IOCTL_GET_VERSION: {
            if (outLen < sizeof(VERSION_INFO)) {
                status = STATUS_BUFFER_TOO_SMALL;
                break;
            }
            VERSION_INFO* ver = (VERSION_INFO*)buf;
            ver->Major = 1;
            ver->Minor = 0;
            RtlStringCbCopyA(ver->BuildString, sizeof(ver->BuildString), "Debug");
            info = sizeof(VERSION_INFO);
            break;
        }
        
        case IOCTL_SET_CONFIG: {
            if (inLen < sizeof(DRIVER_CONFIG)) {
                status = STATUS_BUFFER_TOO_SMALL;
                break;
            }
            DRIVER_CONFIG* cfg = (DRIVER_CONFIG*)buf;
            // 驗證輸入
            if (cfg->LogLevel > 5) {
                status = STATUS_INVALID_PARAMETER;
                break;
            }
            gConfig = *cfg;
            DbgPrint("[Driver] Config updated. LogLevel=%d\n", gConfig.LogLevel);
            info = 0;
            break;
        }
        
        case IOCTL_GET_STATS: {
            if (outLen < sizeof(DRIVER_STATS)) {
                status = STATUS_BUFFER_TOO_SMALL;
                break;
            }
            *(DRIVER_STATS*)buf = gStats;
            info = sizeof(DRIVER_STATS);
            break;
        }
        
        default:
            status = STATUS_INVALID_DEVICE_REQUEST;
            InterlockedIncrement64((LONG64*)&gStats.ErrorCount);
            break;
    }

    Irp->IoStatus.Status      = status;
    Irp->IoStatus.Information = info;
    IoCompleteRequest(Irp, IO_NO_INCREMENT);
    return status;
}
```

## 用戶態測試程式

```c
// test_ioctl.c（用戶態）
#include <windows.h>
#include <stdio.h>

// 複製驅動的 IOCTL 定義（生產專案用共用 header）
#define IOCTL_GET_VERSION   CTL_CODE(0x22, 0x800, 0, 0)
// ...

int main() {
    HANDLE h = CreateFile(L"\\\\.\\MyDriver",
                          GENERIC_READ | GENERIC_WRITE,
                          0, NULL, OPEN_EXISTING,
                          FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) {
        printf("Error: %d\n", GetLastError());
        return 1;
    }

    // 取版本
    typedef struct { ULONG Major, Minor; char Build[32]; } VERSION;
    VERSION ver = {0};
    DWORD bytes;
    
    if (DeviceIoControl(h, IOCTL_GET_VERSION,
                        NULL, 0,
                        &ver, sizeof(ver),
                        &bytes, NULL)) {
        printf("Version: %d.%d (%s)\n", ver.Major, ver.Minor, ver.Build);
    } else {
        printf("IOCTL failed: %d\n", GetLastError());
    }

    CloseHandle(h);
    return 0;
}
```

## 安全性小結

| Transfer Type | I/O Manager 的保護 | 安全等級 |
|---------------|-------------------|---------|
| METHOD_BUFFERED | 完整複製、地址驗證 | 最安全 |
| METHOD_IN_DIRECT | MDL 鎖頁、物理保護 | 安全 |
| METHOD_OUT_DIRECT | MDL 鎖頁 | 安全 |
| METHOD_NEITHER | **完全不保護** | 危險 |

永遠優先用 METHOD_BUFFERED，除非有明確的性能需求且你完全理解 METHOD_NEITHER 的風險。

## 自我檢核

- [ ] IOCTL code 的四個欄位：DeviceType / Access / Function / Method
- [ ] `CTL_CODE` 宏的用法
- [ ] METHOD_BUFFERED：共用 SystemBuffer，先讀 input 再寫 output，設 `IoStatus.Information`
- [ ] METHOD_NEITHER：I/O Manager 不做任何保護，需要 `ProbeForRead` + `__try/__except`
- [ ] IOCTL dispatch 必須驗證 `InputBufferLength` 和 `OutputBufferLength`
- [ ] METHOD_NEITHER + 缺少驗證 = IOCTL 漏洞的根源（Ch 27 詳述）

→ [練習 A：KMDF 驅動 + IOCTL 控制介面](./practice-a-kmdf-ioctl.md)
