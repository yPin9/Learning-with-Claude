# 練習 B — WinDbg 崩潰分析

> 目標：用 WinDbg 分析刻意製造的四種不同 BSOD，練習從 Dump 追溯根因的完整流程。

## 任務規格

用四個故意有 Bug 的驅動，各自觸發一種 BSOD，練習分析。

每個場景的流程：
1. 編譯並載入故意有 Bug 的驅動
2. 觸發 Bug → VM 藍屏
3. 在 VM 重開後，用 WinDbg 打開 `C:\Windows\MEMORY.DMP`
4. 用 `!analyze -v` + 其他命令找出根因
5. 修復 Bug，確認不再 BSOD

## 場景一：NULL Pointer Dereference（0x50）

### 有 Bug 的驅動

```c
// CrashDriver_NullPtr.c
#include <ntddk.h>

typedef struct _MY_CONTEXT {
    ULONG magic;
    ULONG data;
} MY_CONTEXT, *PMY_CONTEXT;

PMY_CONTEXT gCtx = NULL;  // 從未初始化

NTSTATUS DispatchDeviceControl(PDEVICE_OBJECT DevObj, PIRP Irp)
{
    // Bug: gCtx 是 NULL，這行會 NULL dereference
    ULONG val = gCtx->data;  // PAGE_FAULT_IN_NONPAGED_AREA
    
    Irp->IoStatus.Status      = STATUS_SUCCESS;
    Irp->IoStatus.Information = 0;
    IoCompleteRequest(Irp, IO_NO_INCREMENT);
    return STATUS_SUCCESS;
}

NTSTATUS DriverEntry(PDRIVER_OBJECT DrvObj, PUNICODE_STRING RegPath)
{
    // ... 建立設備，設定 DispatchDeviceControl ...
    DrvObj->DriverUnload = DriverUnload;
    DrvObj->MajorFunction[IRP_MJ_DEVICE_CONTROL] = DispatchDeviceControl;
    // gCtx 刻意不初始化
    return STATUS_SUCCESS;
}
```

### 觸發方式

用戶態呼叫 DeviceIoControl → BSOD。

### 分析目標

```
kd> !analyze -v
→ Bugcheck 0x50，Arg1 = 存取的地址（接近 0x0 = NULL dereference 的偏移）
→ FOLLOWUP_IP 指向 DispatchDeviceControl+offset
→ 找出是哪個指針是 NULL

kd> .trap <trap frame>
kd> r             ← 看哪個暫存器是 0（NULL）
kd> u rip         ← 看當前指令（mov rax, [rcx+8] 之類）
```

**預期根因**：`gCtx` 從未初始化，存取 `gCtx->data` = 存取地址 `0 + offsetof(data)` = 無效地址。

## 場景二：IRQL 違規（0xD1）

### 有 Bug 的驅動

```c
// CrashDriver_IRQL.c
KSPIN_LOCK gSpinLock;
PKEVENT    gEvent;      // 分頁記憶體！（KEVENT 應在非分頁池）

NTSTATUS DriverEntry(...)
{
    // Bug: KEVENT 分配在分頁池
    gEvent = (PKEVENT)ExAllocatePoolWithTag(PagedPool, sizeof(KEVENT), 'Evnt');
    KeInitializeEvent(gEvent, NotificationEvent, FALSE);
    KeInitializeSpinLock(&gSpinLock);
    // ...
}

// DPC Routine（IRQL = DISPATCH_LEVEL）
void MyDpc(PKDPC Dpc, PVOID Ctx, PVOID Arg1, PVOID Arg2)
{
    KIRQL irql;
    KeAcquireSpinLock(&gSpinLock, &irql);
    
    // Bug: 在 DISPATCH_LEVEL 存取分頁池的 gEvent
    // Page Fault 發生，無法排程 → BSOD
    KeSetEvent(gEvent, IO_NO_INCREMENT, FALSE);  // gEvent 在分頁池！
    
    KeReleaseSpinLock(&gSpinLock, irql);
}
```

### 分析目標

```
kd> !analyze -v
→ 0xD1 DRIVER_IRQL_NOT_LESS_OR_EQUAL
→ Arg2 = 2（DISPATCH_LEVEL）
→ FOLLOWUP_IP 指向 DPC 中的 KeSetEvent

kd> !pcr            ← 確認 IRQL = 2
kd> !pool <arg1 addr>  ← 確認問題地址是 PagedPool
```

**預期根因**：`gEvent` 在 PagedPool，DPC 在 DISPATCH_LEVEL 存取 → Page Fault。

修復：把 `gEvent` 改為 `NonPagedPoolNx`。

## 場景三：Double Free（Verifier 捉到，0xC4）

### 有 Bug 的驅動

```c
// CrashDriver_DoubleFree.c
PVOID gBuffer = NULL;

NTSTATUS DispatchDeviceControl(PDEVICE_OBJECT DevObj, PIRP Irp)
{
    PIO_STACK_LOCATION stack = IoGetCurrentIrpStackLocation(Irp);
    
    if (stack->Parameters.DeviceIoControl.IoControlCode == IOCTL_ALLOC) {
        gBuffer = ExAllocatePoolWithTag(NonPagedPoolNx, 512, 'Buf!');
    }
    else if (stack->Parameters.DeviceIoControl.IoControlCode == IOCTL_FREE) {
        ExFreePoolWithTag(gBuffer, 'Buf!');
        // Bug: 沒有設 gBuffer = NULL
    }
    else if (stack->Parameters.DeviceIoControl.IoControlCode == IOCTL_FREE_AGAIN) {
        // Bug: 再次釋放 gBuffer（已是無效指針）
        ExFreePoolWithTag(gBuffer, 'Buf!');  // Double Free！
    }
    
    Irp->IoStatus.Status = STATUS_SUCCESS;
    IoCompleteRequest(Irp, IO_NO_INCREMENT);
    return STATUS_SUCCESS;
}
```

### 前提：啟用 Driver Verifier

```cmd
verifier /standard /driver CrashDriver_DoubleFree.sys
reboot
```

### 分析目標

```
kd> !analyze -v
→ 0xC4 DRIVER_VERIFIER_DETECTED_VIOLATION
→ Arg1 = 0x51（释放已釋放的記憶體）
→ FOLLOWUP_MODULE: CrashDriver_DoubleFree

kd> !pool <arg2>    ← 查看雙重釋放的記憶體塊
→ 確認是 'Buf!' tag 的記憶體，且已標記為 freed
```

**預期根因**：釋放後沒有清 NULL，第二次釋放使用了懸空指針。

修復：
```c
ExFreePoolWithTag(gBuffer, 'Buf!');
gBuffer = NULL;  // 加這行
```

## 場景四：IRP Double Complete（0x44）

### 有 Bug 的驅動

```c
// CrashDriver_IRP.c
NTSTATUS DispatchRead(PDEVICE_OBJECT DevObj, PIRP Irp)
{
    // 第一次完成
    Irp->IoStatus.Status      = STATUS_SUCCESS;
    Irp->IoStatus.Information = 0;
    IoCompleteRequest(Irp, IO_NO_INCREMENT);
    
    // Bug: 繼續存取已完成的 IRP，第二次完成
    // （例如在錯誤路徑忘記 return）
    if (someCondition) {
        Irp->IoStatus.Status = STATUS_UNSUCCESSFUL;
        IoCompleteRequest(Irp, IO_NO_INCREMENT);  // BSOD 在這裡
    }
    
    return STATUS_SUCCESS;
}
```

### 分析目標

```
kd> !analyze -v
→ 0x44 MULTIPLE_IRP_COMPLETE_REQUESTS
→ Arg1 = IRP 地址
→ call stack 包含兩次 IoCompleteRequest

kd> !irp <irp addr>   ← 查看 IRP 狀態
```

**預期根因**：錯誤路徑忘記 `return`，導致 IRP 被 Complete 兩次。

## 分析紀錄格式

完成每個場景後，填寫：

```
場景 N：<名稱>
Bugcheck Code: 0xXX
Arg1: <值>
問題驅動: <驅動名>
問題函式: <函式名+偏移>
根因: <一句話描述>
修復: <一句話描述>
WinDbg 命令用到的: !analyze -v, !pool, ...
```

## 自我檢核

- [ ] 四個場景都成功觸發 BSOD 並從 Dump 分析出根因
- [ ] 能用 `.trap` / `.cxr` 切換到崩潰時的正確執行環境
- [ ] 能用 `!pool` 確認記憶體的 Pool 類型（Paged vs NonPaged）
- [ ] 能識別 Verifier 觸發的 0xC4（Violation Code 0x51 = Double Free）
- [ ] 修復後確認驅動正常載入、執行測試無 BSOD

→ [Ch 26 Windows 核心漏洞概覽](./26-kernel-vuln-overview.md)
