# Ch 3 — 核心模式 vs 用戶模式

> 目標：深入理解 Ring 0/3 切換機制、IRQL 系統的意義，以及這些限制如何影響驅動代碼的寫法。

## Ring 機制

x86/x64 CPU 有 4 個 Privilege Level（Ring 0–3），Windows 只用 Ring 0 和 Ring 3：

```
Ring 0（Kernel Mode）：無限制存取所有資源
Ring 3（User Mode）  ：只能存取自己的虛擬記憶體，硬體操作需要 syscall
```

用戶態代碼嘗試執行特權指令（如 `in`/`out`、修改 CR3）會觸發 General Protection Fault（#GP），Windows 把它轉成 `STATUS_PRIVILEGED_INSTRUCTION` 例外，進程被殺。

驅動代碼跑在 Ring 0，沒有任何硬體保護邊界。寫 `*(PULONG)0 = 0xDEAD;`，立刻 BSOD。

## IRQL：比 Ring 更重要的概念

這是 Windows kernel 最難理解也最常犯錯的地方。

**IRQL（Interrupt Request Level）** 是一個 0–31 的數值，表示當前 CPU 的「中斷優先級」：

```
IRQL 31  HIGH_LEVEL      — 電源管理、NMI
IRQL 30  POWER_LEVEL
...
IRQL  2  DISPATCH_LEVEL  — DPC、排程器本身
IRQL  1  APC_LEVEL       — 非同步程序呼叫（APC）
IRQL  0  PASSIVE_LEVEL   — 一般用戶代碼和大部分驅動代碼
```

**關鍵規則：當 IRQL ≥ DISPATCH_LEVEL（2），排程器被停用。**

這意味著：
- 不能等待任何可能讓 CPU 讓出的東西（等待 mutex、等待 I/O 完成）
- 不能存取**分頁記憶體**（Page Fault 需要 I/O，而 I/O 需要排程）
- 不能呼叫任何「可能等待」的函式（否則 BSOD `DRIVER_IRQL_NOT_LESS_OR_EQUAL`）

所有 MSDN 函式文檔都有 IRQL 要求，這不是可選項目。

### IRQL 在哪個層級

```c
// 查詢當前 IRQL
KIRQL currentIrql = KeGetCurrentIrql();

// 升高 IRQL（極少用，通常是 KeAcquireSpinLock 自動做）
KIRQL oldIrql;
KeRaiseIrql(DISPATCH_LEVEL, &oldIrql);
// ... 做完事 ...
KeLowerIrql(oldIrql);  // 降回去
```

大部分驅動代碼在 `PASSIVE_LEVEL` 執行：DriverEntry、IRP dispatch routines（IRP_MJ_CREATE 等）。

DPC（Deferred Procedure Call）和中斷 ISR 在 `DISPATCH_LEVEL` 或更高，有嚴格限制。

### 常見 IRQL 違規 BSOD

| Bugcheck Code | 原因 |
|---|---|
| `DRIVER_IRQL_NOT_LESS_OR_EQUAL` | 在 DISPATCH_LEVEL 存取了分頁記憶體 |
| `IRQL_NOT_LESS_OR_EQUAL` | 類似，通常是驅動 bug |
| `IRQL_NOT_DISPATCH_LEVEL` | 呼叫了要求 DISPATCH_LEVEL 的函式但不在那個 IRQL |

Driver Verifier（Ch 23）可以在開發時自動偵測 IRQL 違規。

## 系統呼叫：Ring 3 → Ring 0

### 從 syscall 指令說起

```asm
; ntdll.dll 中 NtReadFile 的 stub（x64）
NtReadFile:
    mov    r10, rcx          ; 保存第一個參數（保護 rcx）
    mov    eax, 6            ; syscall number = 6 for NtReadFile
    syscall                  ; 進入 Ring 0
    ret
```

`syscall` 指令做了：
1. 把 RIP（返回地址）存到 RCX
2. 把 RFLAGS 存到 R11
3. 從 MSR `LSTAR` 讀取核心入口地址（`KiSystemCall64`）
4. 切換 CS 和 SS 到核心段（Ring 0）

### KiSystemCall64 之後

```
KiSystemCall64:
    swapgs                   ; 切換 GS.base（核心 PCR 結構）
    mov gs:[PcRsp0], rsp     ; 保存用戶態 RSP
    mov rsp, gs:[PcKernelRsp]; 切換到核心棧
    ... 保存通用暫存器 ...
    call nt!KiSystemServiceHandler
        → 從 SSDT 查 syscall number 6 → NtReadFile
        → 呼叫 nt!NtReadFile()
    ... 恢復暫存器 ...
    sysretq                  ; 回到 Ring 3
```

SSDT（System Service Descriptor Table）：

```c
// 簡化版
typedef struct _KSERVICE_TABLE_DESCRIPTOR {
    PULONG_PTR Base;      // 指向函式指針陣列
    PULONG    Count;
    ULONG     Limit;      // 最大 syscall number
    PUCHAR    Number;     // 參數計數
} KSERVICE_TABLE_DESCRIPTOR;

extern KSERVICE_TABLE_DESCRIPTOR KeServiceDescriptorTable[];
// KeServiceDescriptorTable[0] = ntoskrnl.exe 的 syscall table
// KeServiceDescriptorTable[1] = win32k.sys 的 syscall table（GUI 呼叫）
```

早年 rootkit 直接修改 SSDT 做 hook（把函式指針換成自己的）。現在 PatchGuard 會定期驗證 SSDT 內容，發現被修改就 BSOD。

```
WinDbg 查 SSDT：
kd> dps nt!KeServiceDescriptorTable L4
```

## 用戶態 vs 核心態記憶體的邊界

驅動很常需要讀寫**用戶態**傳進來的緩衝區。這裡有個陷阱：

```c
// 危險！永遠不要這樣寫
NTSTATUS BadDispatch(PDEVICE_OBJECT DeviceObject, PIRP Irp) {
    PVOID userBuffer = Irp->AssociatedIrp.SystemBuffer;
    
    // 直接存取，完全沒有驗證
    ULONG value = *(PULONG)userBuffer;  // 用戶可以傳任意地址！
    ...
}
```

正確做法：用 `ProbeForRead` / `ProbeForWrite` 驗證地址是用戶態且可訪問：

```c
__try {
    ProbeForRead(userBuffer, sizeof(ULONG), sizeof(ULONG));
    ULONG value = *(PULONG)userBuffer;
} __except (EXCEPTION_EXECUTE_HANDLER) {
    return STATUS_ACCESS_VIOLATION;
}
```

或者使用 IRP 的 Buffered I/O 模式（I/O Manager 幫你複製緩衝區），這是 IOCTL 最常用的方式（Ch 10 詳述）。

## 棧的差異

每個執行緒有**兩個棧**：
- 用戶態棧（User Stack）：預設 1MB，可增長
- 核心態棧（Kernel Stack）：**固定 12KB（64 位元）**，不能增長

核心棧只有 12KB 非常小。深遞迴在核心裡是禁忌，BSOD `KERNEL_STACK_OVERFLOW`。

```c
// 危險：核心棧上的大陣列
NTSTATUS DispatchSomething(PDEVICE_OBJECT DeviceObject, PIRP Irp) {
    UCHAR buffer[8192];  // 8KB，幾乎把核心棧用完了
    // → 非常容易 KERNEL_STACK_OVERFLOW
}

// 正確：從 Pool 分配
PUCHAR buffer = ExAllocatePoolWithTag(NonPagedPool, 8192, 'BufT');
if (!buffer) return STATUS_INSUFFICIENT_RESOURCES;
// ... 用完 ...
ExFreePoolWithTag(buffer, 'BufT');
```

## WoW64：32 位元進程在 64 位元 Windows

64 位元 Windows 支援跑 32 位元進程（`wow64.dll` + `wow64cpu.dll`）。32 位元進程使用 `int 0x2E`（舊式）或 `wow64` 轉換層。

從驅動視角：接到 WoW64 進程的 IOCTL，緩衝區裡的指針是 32 位元的。要特別處理，不然 pointer truncation BSOD。

判斷方式：

```c
PsGetCurrentProcessWow64Process()  // 非 NULL 表示是 WoW64 進程
```

## 實際驗證：IRQL 切換

在 WinDbg 可以直接查當前 IRQL：

```
kd> !pcr
KPCR for Processor 0 at fffff80012340000:
    ...
    CurrentIrql: 0   ← PASSIVE_LEVEL
```

設個 breakpoint 在 DPC 函式：
```
kd> bp nt!KeFlushQueuedDpcs
kd> g
```
斷在 DPC 執行時，`!pcr` 會顯示 `CurrentIrql: 2`（DISPATCH_LEVEL）。

## 自我檢核

- [ ] Ring 0 和 Ring 3 的差異：特權指令存取權限
- [ ] IRQL 的意義：數值越高，中斷越不能打斷當前代碼
- [ ] IRQL ≥ DISPATCH_LEVEL 的三個限制：不能等待、不能存取分頁記憶體、不能呼叫 pageable 函式
- [ ] syscall 指令的流程：LSTAR → KiSystemCall64 → SSDT → 核心函式
- [ ] 核心棧只有 12KB，不能在棧上放大緩衝區
- [ ] 存取用戶態緩衝區必須用 `ProbeForRead/Write` + `__try/__except`

→ [Ch 4 NT 物件模型](./04-object-model.md)
