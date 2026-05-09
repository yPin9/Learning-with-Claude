# Ch 15 — Lookaside List

> 目標：理解 Lookaside List 為什麼比 ExAllocatePool 快，掌握 NPAGED_LOOKASIDE_LIST 和 PAGED_LOOKASIDE_LIST 的正確使用。

## 問題：頻繁分配/釋放相同大小的結構體

驅動如果對每個 IRP 都呼叫 `ExAllocatePoolWithTag` / `ExFreePoolWithTag`，有幾個問題：

1. **性能**：每次分配都要鎖定 Pool 的全域鎖，多 CPU 競爭
2. **碎片化**：頻繁分配釋放同大小物件，Pool 會碎片化
3. **可擴展性差**：Pool 鎖是全域的，高頻分配是瓶頸

**Lookaside List**：每個 CPU 有自己的本地快取。釋放的物件不還給 Pool，而是放進每個 CPU 的本地快取。下次分配先從本地快取取，不需要鎖定 Pool。

```
ExAllocatePool:  [鎖定 Pool 全域鎖] → 找可用塊 → [釋放鎖]  每次都這樣

LookasideList:   [本地快取有？] → 直接取（無鎖）
                 [本地快取空？] → 呼叫 ExAllocatePool（只在快取用完時）
```

結果：高頻分配/釋放的性能可以提升 3–5 倍。

## 兩種 Lookaside List

| | NPAGED_LOOKASIDE_LIST | PAGED_LOOKASIDE_LIST |
|---|---|---|
| Pool 類型 | NonPagedPoolNx | PagedPool |
| 可在 IRQL | 任何（包括 DISPATCH_LEVEL）| 只能 PASSIVE_LEVEL |
| 使用場景 | DPC/ISR 中頻繁分配的結構 | 只在 PASSIVE_LEVEL 的結構 |

## 使用方式

```c
// ─── 全域 Lookaside List ─────────────────────────────
NPAGED_LOOKASIDE_LIST gRequestLookaside;

// 在 DriverEntry 初始化
ExInitializeNPagedLookasideList(
    &gRequestLookaside,
    NULL,               // Allocate callback（NULL = 用 ExAllocatePoolWithTag）
    NULL,               // Free callback（NULL = 用 ExFreePoolWithTag）
    0,                  // Flags（0 即可）
    sizeof(MY_REQUEST), // 每個物件的大小（固定！）
    'RqLs',             // Pool Tag
    0);                 // Depth（0 = 系統自動決定每個 CPU 快取多少個）

// ─── 分配 ────────────────────────────────────────────
PMY_REQUEST req = (PMY_REQUEST)ExAllocateFromNPagedLookasideList(
    &gRequestLookaside);

if (!req) {
    // Lookaside List 最終會呼叫 ExAllocatePoolWithTag
    // 如果 Pool 耗盡，才返回 NULL
    return STATUS_INSUFFICIENT_RESOURCES;
}

RtlZeroMemory(req, sizeof(MY_REQUEST));

// ─── 釋放（不是 ExFreePool！）────────────────────────
ExFreeToNPagedLookasideList(&gRequestLookaside, req);
req = NULL;

// ─── 在 DriverUnload 中刪除 Lookaside List ──────────
ExDeleteNPagedLookasideList(&gRequestLookaside);
```

## Paged Lookaside List（PASSIVE_LEVEL 使用）

```c
PAGED_LOOKASIDE_LIST gBufferLookaside;

// 初始化（通常在 DriverEntry）
ExInitializePagedLookasideList(
    &gBufferLookaside,
    NULL, NULL, 0,
    sizeof(MY_BUFFER),
    'BufL',
    0);

// 分配（只在 PASSIVE_LEVEL）
PMY_BUFFER buf = (PMY_BUFFER)ExAllocateFromPagedLookasideList(
    &gBufferLookaside);

// 釋放
ExFreeToPagedLookasideList(&gBufferLookaside, buf);

// 清理
ExDeletePagedLookasideList(&gBufferLookaside);
```

## 自定義 Allocate/Free Callback

如果你的結構分配後需要額外初始化，可以提供自定義 Allocate callback：

```c
PVOID MyAllocate(POOL_TYPE PoolType, SIZE_T NumberOfBytes, ULONG Tag)
{
    PVOID p = ExAllocatePoolWithTag(PoolType, NumberOfBytes, Tag);
    if (p) {
        RtlZeroMemory(p, NumberOfBytes);
        // 做額外初始化
    }
    return p;
}

void MyFree(PVOID Buffer)
{
    // 做清理
    RtlSecureZeroMemory(Buffer, sizeof(MY_REQUEST));  // 安全清零（編譯器不優化掉）
    ExFreePoolWithTag(Buffer, 'RqLs');
}

ExInitializeNPagedLookasideList(
    &gRequestLookaside,
    MyAllocate,  // ← 自定義
    MyFree,      // ← 自定義
    0, sizeof(MY_REQUEST), 'RqLs', 0);
```

## Lookaside List 的限制

1. **固定大小**：只能分配和初始化時指定的大小。不能用 Lookaside List 分配變長結構。

2. **必須 `ExDelete*` 清理**：在 DriverUnload 中忘記 `ExDeleteNPagedLookasideList` → 佔用的 NonPaged Pool 釋放不了。

3. **不是安全的跨進程共享**：Lookaside List 是 per-driver 的，不要在不信任的代碼之間共享。

## 什麼時候用 Lookaside List vs ExAllocatePool

| 情況 | 用什麼 |
|------|--------|
| 每個 IRP 分配一個 context 結構（高頻） | Lookaside List |
| 偶爾分配大緩衝區（低頻） | ExAllocatePoolWithTag |
| 大小不固定 | ExAllocatePoolWithTag |
| 結構大小固定且每秒分配 > 100 次 | Lookaside List |

## 性能對比實驗

```c
// 測試：分配 10 萬次，比較時間
LARGE_INTEGER start, end, freq;

KeQueryPerformanceCounter(&start);
for (int i = 0; i < 100000; i++) {
    PVOID p = ExAllocatePoolWithTag(NonPagedPoolNx, sizeof(MY), 'Test');
    ExFreePoolWithTag(p, 'Test');
}
KeQueryPerformanceCounter(&end);
// ...

KeQueryPerformanceCounter(&start);
for (int i = 0; i < 100000; i++) {
    PVOID p = ExAllocateFromNPagedLookasideList(&list);
    ExFreeToNPagedLookasideList(&list, p);
}
KeQueryPerformanceCounter(&end);
// Lookaside 通常快 3-5x
```

## 自我檢核

- [ ] Lookaside List 的加速原理：per-CPU 快取，避免 Pool 全域鎖競爭
- [ ] `ExInitializeNPagedLookasideList` 的 5 個主要參數：Alloc/Free callback、Flags、Size、Tag
- [ ] 分配用 `ExAllocateFromNPagedLookasideList`，釋放用 `ExFreeToNPagedLookasideList`（不是 ExFreePool！）
- [ ] `ExDeleteNPagedLookasideList` 在 DriverUnload 中必須呼叫
- [ ] Lookaside List 只適合固定大小的高頻分配

→ [Ch 16 IRP 完成常式與取消](./16-irp-cancel.md)
