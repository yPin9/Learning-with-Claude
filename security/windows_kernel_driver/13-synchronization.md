# Ch 13 — 同步基元

> 目標：掌握 SpinLock、Mutex、Event、Semaphore 的使用場景和 IRQL 限制，選對同步工具避免死鎖和 BSOD。

## 為什麼核心同步更難

用戶態同步：死鎖最壞就是 deadlock，進程掛著。

核心態同步：
- 在 DISPATCH_LEVEL（SpinLock 持有中）休眠 → BSOD
- 在不正確的 IRQL 等待 Mutex → BSOD
- 同一個 CPU 重複獲取 SpinLock → 死鎖（CPU 永遠自旋，無法排程）

## 選擇同步工具的決策流程

```
是否需要在 DISPATCH_LEVEL（DPC/ISR）保護資料？
  ├── 是 → KSPIN_LOCK（SpinLock）
  └── 否 → 是否允許等待（阻塞）？
              ├── 否（但 PASSIVE_LEVEL）→ FAST_MUTEX
              └── 是 → 需要遞迴（同執行緒重複獲取）？
                          ├── 是 → ERESOURCE（資源鎖）
                          └── 否 → KMUTEX
```

對等待/通知使用：
- 單次觸發通知 → KEVENT（Notification Event）
- 計數式資源 → KSEMAPHORE

## SpinLock（KSPIN_LOCK）

**適用場景**：保護在 DPC / ISR 中也會存取的數據結構。

工作原理：持有者不放棄 CPU，其他嘗試獲取的 CPU 在循環中忙等（spin）。

```c
// 全域定義和初始化
KSPIN_LOCK gSpinLock;
// 在 DriverEntry 初始化
KeInitializeSpinLock(&gSpinLock);

// 持有 SpinLock（IRQL 升到 DISPATCH_LEVEL）
KIRQL oldIrql;
KeAcquireSpinLock(&gSpinLock, &oldIrql);

// ── CRITICAL SECTION ────────────────────────
// 這裡不能：
//   - 呼叫任何可能 sleep 的函式
//   - 存取分頁記憶體
//   - 發出中斷或 I/O
// ────────────────────────────────────────────

KeReleaseSpinLock(&gSpinLock, oldIrql);
// oldIrql 必須和 Acquire 時的 oldIrql 一致（恢復到原本 IRQL）
```

### 用在 DPC 中

```c
// DPC 中（IRQL = DISPATCH_LEVEL）
void MyDpcRoutine(PKDPC Dpc, PVOID Context, PVOID SysArg1, PVOID SysArg2)
{
    // 在 DPC 中必須用 KeAcquireSpinLockAtDpcLevel（不升 IRQL，已是 DL）
    KeAcquireSpinLockAtDpcLevel(&gSpinLock);
    // ... 存取共享資料 ...
    KeReleaseSpinLockFromDpcLevel(&gSpinLock);
}
```

## FAST_MUTEX

比 Mutex 輕量，不支援遞迴，在 `PASSIVE_LEVEL` 或 `APC_LEVEL` 使用。
比 SpinLock 重量，但不讓 CPU 自旋（等待時讓出 CPU）。

```c
FAST_MUTEX gFastMutex;
ExInitializeFastMutex(&gFastMutex);

// 在 PASSIVE_LEVEL
ExAcquireFastMutex(&gFastMutex);  // 可能阻塞，但 IRQL 升到 APC_LEVEL
// ... 臨界區 ...
ExReleaseFastMutex(&gFastMutex);

// 如果已知在 APC_LEVEL（如已 KeEnterCriticalRegion 後）
ExAcquireFastMutexUnsafe(&gFastMutex);
ExReleaseFastMutexUnsafe(&gFastMutex);
```

## KMUTEX（Kernel Mutex）

支援所有 Dispatcher Object 的等待機制，支援遞迴（同一執行緒可重複獲取，不死鎖）。

```c
KMUTEX gMutex;
KeInitializeMutex(&gMutex, 0);  // Level = 0（Priority Boost 相關）

// 獲取（可指定超時）
LARGE_INTEGER timeout;
timeout.QuadPart = -10000000;  // -1 秒（負數 = 相對時間，單位 100ns）

NTSTATUS status = KeWaitForSingleObject(
    &gMutex,
    Executive,      // WaitReason（排程器用途）
    KernelMode,
    FALSE,          // Alertable = FALSE（不被 APC 中斷）
    &timeout);      // NULL = 無限等待

if (status == STATUS_SUCCESS) {
    // ... 有 Mutex ...
    KeReleaseMutex(&gMutex, FALSE);
}
```

**絕對不在 DISPATCH_LEVEL 等待 Mutex**（等待需要排程，DISPATCH_LEVEL 排程被停用）。

## KEVENT：事件通知

事件有兩種：
- **Notification Event（手動重設）**：`KeSetEvent` 後，所有等待的執行緒都被喚醒；需要手動 `KeResetEvent` 清除
- **Synchronization Event（自動重設）**：喚醒一個執行緒後自動清除

```c
KEVENT gEvent;

// 在 DriverEntry
KeInitializeEvent(&gEvent, NotificationEvent, FALSE);  // FALSE = 初始未觸發

// 生產者（例如 DPC 中）
KeSetEvent(&gEvent, IO_NO_INCREMENT, FALSE);
// 第三個參數 Wait = FALSE（呼叫者不會立刻等待這個 event）

// 消費者（工作執行緒）
KeWaitForSingleObject(&gEvent, Executive, KernelMode, FALSE, NULL);
// 喚醒後
KeResetEvent(&gEvent);  // Notification Event 需要手動清除

// Synchronization Event 範例（自動清除，只喚醒一個等待者）
KEVENT syncEvent;
KeInitializeEvent(&syncEvent, SynchronizationEvent, FALSE);
// KeSetEvent 後只有一個 KeWaitForSingleObject 返回，自動清除
```

## 等待多個物件

```c
PVOID waitObjects[2] = { &event1, &event2 };
NTSTATUS status = KeWaitForMultipleObjects(
    2,              // Count
    waitObjects,
    WaitAny,        // WaitAll 或 WaitAny
    Executive,
    KernelMode,
    FALSE,
    NULL,           // 無限等待
    NULL);          // WaitBlockArray（NULL = 小於等於 THREAD_WAIT_OBJECTS 個物件時自動分配）

if (status == STATUS_WAIT_0) {
    // event1 觸發
} else if (status == STATUS_WAIT_1) {
    // event2 觸發
}
```

## KSEMAPHORE：計數式信號量

```c
KSEMAPHORE gSema;
KeInitializeSemaphore(&gSema, 5, 10);  // 初始計數 5，最大 10

// 等待（計數 > 0 時返回，計數遞減 1）
KeWaitForSingleObject(&gSema, Executive, KernelMode, FALSE, NULL);

// 釋放（計數遞增）
KeReleaseSemaphore(&gSema, IO_NO_INCREMENT, 1, FALSE);
```

## ERESOURCE：支援讀寫共享的鎖

多個讀者可以同時持有；寫者獨佔。適合保護「頻繁讀、偶爾寫」的數據。

```c
ERESOURCE gResource;
ExInitializeResourceLite(&gResource);

// 讀取（共享模式，多個讀者可同時持有）
// 必須在 PASSIVE_LEVEL，且需要先 KeEnterCriticalRegion
KeEnterCriticalRegion();
ExAcquireResourceSharedLite(&gResource, TRUE);  // TRUE = wait

// ... 讀取數據 ...

ExReleaseResourceLite(&gResource);
KeLeaveCriticalRegion();

// 寫入（獨佔模式）
KeEnterCriticalRegion();
ExAcquireResourceExclusiveLite(&gResource, TRUE);

// ... 修改數據 ...

ExReleaseResourceLite(&gResource);
KeLeaveCriticalRegion();

// 清理（在 DriverUnload 中）
ExDeleteResourceLite(&gResource);
```

`KeEnterCriticalRegion` / `KeLeaveCriticalRegion` 停用正常 APC，防止 `ExAcquireResourceSharedLite` 等待期間被 APC 搶斷。

## 同步工具速查表

| 工具 | IRQL 限制 | 遞迴 | 等待/阻塞 | 適用場景 |
|------|-----------|------|----------|---------|
| KSPIN_LOCK | 任何（升到 DISPATCH_LEVEL）| 否 | 否（忙等）| DPC/ISR 共享資料 |
| FAST_MUTEX | ≤ APC_LEVEL | 否 | 是 | PASSIVE_LEVEL 輕量互斥 |
| KMUTEX | ≤ APC_LEVEL（等待時）| 是 | 是 | 支援超時的互斥 |
| ERESOURCE | PASSIVE_LEVEL | 是 | 是 | 多讀單寫 |
| KEVENT | 任何（KeSetEvent）| N/A | 是（等待時 ≤ DISPATCH_LEVEL）| 生產者消費者通知 |
| KSEMAPHORE | 同 KEVENT | N/A | 是 | 計數式資源 |

## 自我檢核

- [ ] SpinLock 持有期間不能 sleep、不能存取分頁記憶體、不能呼叫可能 sleep 的 API
- [ ] `KeAcquireSpinLock` + `KeReleaseSpinLock` 必須用同一個 `oldIrql`
- [ ] KMUTEX / KEVENT 等待必須在 IRQL ≤ APC_LEVEL（通常是 PASSIVE_LEVEL）
- [ ] ERESOURCE 使用前必須 `KeEnterCriticalRegion`（停用一般 APC）
- [ ] Notification Event 需手動 `KeResetEvent`；Synchronization Event 自動清除

→ [Ch 14 DPC 與 APC](./14-dpc-apc.md)
