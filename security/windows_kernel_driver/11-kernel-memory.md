# Ch 11 — 核心記憶體模型

> 目標：掌握分頁池與非分頁池的差異、IRQL 對記憶體存取的限制，以及安全的核心記憶體分配和釋放模式。

## 兩種記憶體池

### 非分頁池（NonPagedPool）

永遠在實體記憶體中，不會被換到磁碟（Paging File）。

**任何 IRQL 都可存取**，包括 DISPATCH_LEVEL 和中斷 ISR。

代價：珍貴的實體記憶體資源。Windows 對 NonPagedPool 的用量有上限（系統 RAM 的 ~25%）。

### 分頁池（PagedPool）

可以被換到磁碟。**只能在 IRQL < DISPATCH_LEVEL（PASSIVE_LEVEL 或 APC_LEVEL）存取**。

在 DISPATCH_LEVEL 存取分頁池 → Page Fault → 排程器已停用 → BSOD `DRIVER_IRQL_NOT_LESS_OR_EQUAL`。

## 選哪個池

```
必須用 NonPagedPool：
  - 在 DPC / ISR 中用到的數據結構
  - SpinLock 保護的數據（SpinLock 跑在 DISPATCH_LEVEL）
  - IRP 完成常式中用到的結構（可能在 DISPATCH_LEVEL 被呼叫）
  - 任何「不知道在什麼 IRQL 下被存取」的東西
  
可以用 PagedPool：
  - 只在 PASSIVE_LEVEL 存取的緩衝區
  - 設定/配置資料（在 DriverEntry 或 IOCTL handler 中讀取）
  - 任何可以確定 IRQL < 2 的使用場景
```

記憶體不足時，PagedPool 可以換出，不佔 RAM；NonPagedPool 無法換出，優先用 PagedPool。

## 分配 API

### ExAllocatePoolWithTag（主要 API）

```c
PVOID ExAllocatePoolWithTag(
    POOL_TYPE PoolType,   // NonPagedPool / PagedPool / NonPagedPoolNx
    SIZE_T    NumberOfBytes,
    ULONG     Tag         // 4 字元標籤，用 'Tag!' 格式，Little-Endian
);
```

**Pool Tag** 是你的驅動的識別碼，方便用 WinDbg / PoolMon 追蹤記憶體洩漏：

```c
#define MY_POOL_TAG 'DvrM'  // 'MvrD' in memory（Little-Endian）

PVOID buf = ExAllocatePoolWithTag(NonPagedPool, 1024, MY_POOL_TAG);
if (buf == NULL) {
    // 記憶體不足，必須處理！
    return STATUS_INSUFFICIENT_RESOURCES;
}
```

**從 Windows 10 2004 起**，`NonPagedPool` 被棄用（但仍可用），改用 `NonPagedPoolNx`（Non-executable，防 shellcode 在堆積執行）。新代碼用 `NonPagedPoolNx`。

### 釋放

```c
ExFreePoolWithTag(buf, MY_POOL_TAG);
buf = NULL;  // 好習慣：釋放後清 NULL
```

和 Tag 必須和 `ExAllocatePoolWithTag` 的 Tag 一致（Driver Verifier 會檢查）。

**絕對不要**：
- `ExFreePool` 一個從來沒分配過的指針
- Double-free（釋放兩次）
- 釋放後繼續使用（Use-After-Free）—— 這是 kernel UAF 漏洞的來源

## 記憶體初始化

```c
// ExAllocatePool 分配的記憶體內容未初始化（可能有舊數據）
PVOID buf = ExAllocatePoolWithTag(NonPagedPool, 256, MY_TAG);
if (!buf) return STATUS_INSUFFICIENT_RESOURCES;
RtlZeroMemory(buf, 256);  // 清零，避免資訊洩漏
```

**切忌**把未初始化的核心緩衝區直接複製給用戶態——資訊洩漏（infoleak）漏洞的常見成因。

## 字串和緩衝區操作

核心禁止使用 C 標準庫的字串函式（`strcpy`、`sprintf`），改用 RtlString 系列：

```c
// 複製記憶體
RtlCopyMemory(dst, src, size);
RtlZeroMemory(buf, size);
RtlFillMemory(buf, size, 0xCC);

// 安全字串（自動防止緩衝區溢出）
CHAR  ansi[64];
WCHAR wide[64];
RtlStringCbCopyA(ansi, sizeof(ansi), "source");    // 安全，不溢出
RtlStringCbCopyW(wide, sizeof(wide), L"source");
RtlStringCbPrintfA(ansi, sizeof(ansi), "val=%d", 42);

// UNICODE_STRING 操作
UNICODE_STRING us;
RtlInitUnicodeString(&us, L"Hello");   // 不分配記憶體
RtlCopyUnicodeString(&dst, &src);      // 複製（dst 要有足夠空間）
RtlUnicodeStringToAnsiString(&ansi, &us, TRUE);  // TRUE = 自動分配
RtlFreeAnsiString(&ansi);             // 用完要釋放
```

**UNICODE_STRING 的注意事項**：
```c
typedef struct _UNICODE_STRING {
    USHORT Length;         // 目前字串長度（bytes，不含 NULL）
    USHORT MaximumLength;  // Buffer 的總大小（bytes）
    PWSTR  Buffer;         // 字串緩衝區（不一定以 NULL 結尾！）
} UNICODE_STRING;
```

`UNICODE_STRING.Buffer` 不保證以 NULL 結尾。不能用 `wcslen(us.Buffer)` 或 `wprintf(us.Buffer)`，要用 `%wZ` 格式說明符：

```c
DbgPrint("String: %wZ\n", &unicodeString);  // 正確
DbgPrint("String: %ws\n", unicodeString.Buffer);  // 危險：可能沒有 NULL 終止
```

## 記憶體壓力與防禦

### 永遠處理分配失敗

```c
// 錯誤：忽略 NULL 返回值
PVOID buf = ExAllocatePoolWithTag(NonPagedPool, 4096, 'Tag!');
buf[0] = 1;  // NULL dereference if allocation failed！

// 正確
PVOID buf = ExAllocatePoolWithTag(NonPagedPool, 4096, 'Tag!');
if (!buf) {
    DbgPrint("Memory allocation failed\n");
    return STATUS_INSUFFICIENT_RESOURCES;
}
```

### 記憶體洩漏偵測

在 WinDbg 查看特定 Tag 的記憶體佔用：

```
kd> !poolused 2 DvrM
 Tag  Type     Allocs        Frees        Diff      Bytes      Per Alloc
 DvrM Nonp       1500         1498           2       8192           4096
```

`Diff = Allocs - Frees = 2` 表示有 2 次分配沒有對應的釋放。

也可以用 Sysinternals 的 **PoolMon** 工具在跑驅動時監控 pool tag 使用量。

## 大塊記憶體：MmAllocateContiguousMemory

有時需要物理連續的記憶體（DMA 用）：

```c
// 分配 4MB 物理連續記憶體
PHYSICAL_ADDRESS lowAddr = {0};
PHYSICAL_ADDRESS highAddr;
highAddr.QuadPart = 0xFFFFFFFF;  // 4GB 以下

PVOID contiguous = MmAllocateContiguousMemory(4 * 1024 * 1024, highAddr);
if (!contiguous) return STATUS_INSUFFICIENT_RESOURCES;

// 使用
// ...

// 釋放
MmFreeContiguousMemory(contiguous);
```

這類記憶體很昂貴（連碎片化都不行），只在必要時使用。

## WinDbg 記憶體分析

```
kd> !pool ffffe00012345678    ← 查某個地址所在的 Pool 資訊
kd> !poolused                 ← 所有 tag 的分配統計
kd> !heap -s                  ← 核心堆積使用統計（系統堆積）
kd> !vm                       ← 系統虛擬記憶體統計
```

## 自我檢核

- [ ] NonPagedPool：永遠在 RAM，任何 IRQL 可存取；PagedPool：可換出，只能在 IRQL < 2 存取
- [ ] `ExAllocatePoolWithTag` 的 3 個參數：類型、大小、4 字元 Tag
- [ ] 分配後必須檢查 NULL，返回 `STATUS_INSUFFICIENT_RESOURCES`
- [ ] 新代碼用 `NonPagedPoolNx`（non-executable）而不是 `NonPagedPool`
- [ ] 輸出給用戶態的緩衝區必須先 `RtlZeroMemory`（防止資訊洩漏）
- [ ] `UNICODE_STRING.Buffer` 不保證 NULL 結尾，用 `%wZ` 打印

→ [Ch 12 MDL](./12-mdl.md)
