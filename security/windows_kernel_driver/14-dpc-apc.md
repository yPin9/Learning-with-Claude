# Ch 14 — DPC 與 APC

> 目標：理解 DPC 和 APC 的執行時機、IRQL 限制，以及如何正確在驅動中使用它們做延遲處理。

## 問題：中斷 ISR 不能做太多事

當硬體中斷發生，CPU 在高 IRQL（`DIRQL`）執行 ISR（Interrupt Service Routine）。

在高 IRQL，不能：等待、做 I/O、呼叫大部分 kernel API。ISR 必須極短，只做「確認中斷發生、讀取最少數據、清除中斷標誌」。

中斷後的「真正工作」（處理數據、完成 IRP）怎麼辦？

答案是：**延遲**到較低 IRQL 執行。

## DPC（Deferred Procedure Call）

DPC 是排隊在 `DISPATCH_LEVEL`（IRQL = 2）執行的函式。

ISR 觸發後，把「剩餘工作」排入 DPC 佇列，ISR 立刻返回。等中斷結束、IRQL 降回 `DISPATCH_LEVEL` 後，核心執行 DPC 佇列裡的函式。

```
中斷發生 (IRQL → DIRQL)
  → ISR 執行（極短）
    → KeInsertQueueDpc（把 DPC 加入佇列）
  → ISR 返回
IRQL 降到 DISPATCH_LEVEL
  → 系統執行 DPC 佇列
    → MyDpcRoutine 執行（IRQL = DISPATCH_LEVEL）
```

### DPC 使用方式

```c
KDPC    gDpc;
BOOLEAN gDpcReady = FALSE;

// 在 DriverEntry 初始化 DPC
KeInitializeDpc(&gDpc, MyDpcRoutine, NULL);

// DPC 函式（IRQL = DISPATCH_LEVEL）
void MyDpcRoutine(
    PKDPC   Dpc,
    PVOID   DeferredContext,  // KeInitializeDpc 的第三個參數
    PVOID   SystemArgument1,  // KeInsertQueueDpc 的參數
    PVOID   SystemArgument2)
{
    // 這裡 IRQL = DISPATCH_LEVEL
    // 可以：操作非分頁池、呼叫 DL 以下的 API
    // 不能：等待 Mutex/Event、存取分頁記憶體
    
    ULONG data = (ULONG)(ULONG_PTR)SystemArgument1;
    DbgPrint("[DPC] Processing: %lu\n", data);
}

// ISR 或其他高 IRQL 代碼中
void MyIsr()
{
    KeInsertQueueDpc(&gDpc, (PVOID)someData, NULL);
    // DPC 排入佇列，稍後在 DISPATCH_LEVEL 執行
}
```

### Timer + DPC

最常見的 DPC 用途之一是 Timer 回調（定時器）：

```c
KTIMER     gTimer;
KDPC       gTimerDpc;

// 初始化
KeInitializeTimer(&gTimer);
KeInitializeDpc(&gTimerDpc, TimerDpcRoutine, NULL);

// 設置定時器（每 1 秒觸發一次）
LARGE_INTEGER interval;
interval.QuadPart = -10000000LL;  // -1 秒（100ns 單位，負 = 相對）
KeSetTimerEx(&gTimer, interval, 1000, &gTimerDpc);  // 1000ms 週期

// 停止
KeCancelTimer(&gTimer);

void TimerDpcRoutine(PKDPC Dpc, PVOID Context, PVOID Arg1, PVOID Arg2)
{
    // 每秒執行一次
    DbgPrint("[Timer] Tick\n");
    
    // 可以在這裡做輪詢、更新統計等輕量工作
    // 但不能等待！
}
```

## APC（Asynchronous Procedure Call）

APC 是在目標執行緒的 `APC_LEVEL`（IRQL = 1）執行的函式，有兩種：

- **Kernel APC（KIRQL = APC_LEVEL）**：在目標執行緒的核心棧上執行
- **User APC（在用戶態）**：在目標執行緒返回用戶態後執行（只在 Alertable 狀態）

### Kernel APC 的典型用途

1. **I/O 完成**：`IoCompleteRequest` 內部用 Kernel APC 通知發出請求的執行緒
2. **DLL 注入**（惡意軟體/安全工具）：把 Kernel APC 排入目標執行緒，讓它執行任意核心代碼

```c
KAPC gApc;

void ApcKernelRoutine(
    PRKAPC   Apc,
    PKNORMAL_ROUTINE* NormalRoutine,
    PVOID*   NormalContext,
    PVOID*   SystemArgument1,
    PVOID*   SystemArgument2)
{
    // 核心 APC 例程（IRQL = APC_LEVEL）
    // 可以修改 *NormalRoutine 來控制用戶 APC 的執行
    DbgPrint("[APC] Kernel APC executed\n");
}

void ApcNormalRoutine(
    PVOID NormalContext,
    PVOID SystemArgument1,
    PVOID SystemArgument2)
{
    // 用戶模式 APC 例程（IRQL = PASSIVE_LEVEL，執行緒在用戶態）
    DbgPrint("[APC] User APC executed in user mode\n");
}

// 初始化並排入 APC
KeInitializeApc(
    &gApc,
    KeGetCurrentThread(),  // 目標執行緒
    OriginalApcEnvironment,
    ApcKernelRoutine,
    NULL,                  // RundownRoutine（執行緒終止時清理）
    ApcNormalRoutine,      // NULL = 只有核心 APC，沒有用戶 APC
    KernelMode,
    NULL);

KeInsertQueueApc(&gApc, NULL, NULL, IO_NO_INCREMENT);
```

### APC 在 DLL 注入中的使用

`KeInsertQueueApc` 可以把一個 APC 排入**任意執行緒**（只要你有 PETHREAD 指針）。如果 NormalRoutine 指向 `LoadLibraryW`（用戶態的地址），當目標執行緒進入 Alertable Wait 時，就會執行 LoadLibrary——這是核心層的 DLL 注入技術。

```c
// 注入 DLL 到目標進程的某個執行緒
PETHREAD targetThread = ...; // 用 PsLookupThreadByThreadId 取得
PVOID    loadLibW     = ...; // 在目標進程空間中 LoadLibraryW 的地址

KeInitializeApc(&apc, targetThread, OriginalApcEnvironment,
                KernelRoutine, NULL, 
                (PKNORMAL_ROUTINE)loadLibW,  // 指向用戶態 LoadLibraryW
                UserMode, dllPathInTarget);   // DLL 路徑作為參數

KeInsertQueueApc(&apc, NULL, NULL, IO_NO_INCREMENT);
// 目標執行緒下一次 AlertableWait 時執行
```

這是 Windows 安全工具（Citrix、防毒）和惡意軟體都用過的技術。

## Critical Region vs Guarded Region

- `KeEnterCriticalRegion()`：停用普通 Kernel APC（但不停用特殊 Kernel APC）
- `KeEnterGuardedRegion()`：停用所有 Kernel APC（包括特殊 APC）

在持有 ERESOURCE 時必須進入 Critical Region，防止 APC 在持有鎖的執行緒上執行並嘗試再次獲取鎖（死鎖）。

## DPC vs APC 比較

| | DPC | APC |
|---|-----|-----|
| IRQL | DISPATCH_LEVEL（2） | APC_LEVEL（1）或 PASSIVE_LEVEL（用戶 APC）|
| 在哪個執行緒 | 任意 CPU（不綁定執行緒）| 特定執行緒的上下文 |
| 能等待 | 否 | Kernel APC 否；User APC 可以 |
| 主要用途 | ISR 後延遲處理、Timer 回調 | I/O 完成通知、DLL 注入 |

## 在 WinDbg 查看 DPC

```
kd> !dpcs    ← 查看當前 DPC 佇列（通常是空的，DPC 執行很快）

kd> dt nt!_KDPC
   +0x000 Type             : UChar
   +0x001 Importance       : UChar
   +0x002 Number           : Uint2B
   +0x008 DpcListEntry     : _SINGLE_LIST_ENTRY
   +0x010 ProcessorHistory : Uint8B
   +0x018 DeferredRoutine  : Ptr64     void  ← 你的 DPC 函式
   +0x020 DeferredContext  : Ptr64 Void
   +0x028 SystemArgument1  : Ptr64 Void
   +0x030 SystemArgument2  : Ptr64 Void
   +0x038 DpcData          : Ptr64 Void
```

## 自我檢核

- [ ] DPC 的觸發時機：ISR 完成後，IRQL 降到 DISPATCH_LEVEL 時執行
- [ ] DPC 的限制：不能等待、不能存取分頁記憶體（DISPATCH_LEVEL 限制）
- [ ] `KeSetTimerEx` 設定週期性 DPC（定時器）
- [ ] Kernel APC 在目標執行緒的 APC_LEVEL 執行；User APC 在用戶態（Alertable Wait 後）
- [ ] `KeEnterCriticalRegion` 停用普通 Kernel APC，持有 ERESOURCE 時必用

→ [Ch 15 Lookaside List](./15-lookaside-list.md)
