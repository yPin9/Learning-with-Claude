# Ch 38 — EDR 驅動架構

> 目標：理解現代 EDR（Endpoint Detection & Response）在核心層的架構，把前幾章學的技術組合起來，看懂一個完整 EDR 的感測器設計。

## EDR 的威脅模型

EDR 面對的對手：
- 低技術攻擊者：用公開工具（mimikatz、Cobalt Strike 預設設定）
- 中技術攻擊者：自訂工具、規避已知簽名特徵
- 高技術攻擊者（APT）：BYOVD、核心 exploit、針對 EDR 的 bypass

EDR 的策略：**縱深防禦**——即使某個感測器被繞過，其他感測器仍能看到行為痕跡。

## EDR 核心層感測器全貌

```
                    ┌─────────────────────────────────────────┐
                    │             EDR 用戶態服務               │
                    │  事件相關、行為分析、ML 推斷、回應動作    │
                    └──────────────────┬──────────────────────┘
                                       │ IOCTL / 共享記憶體
                    ┌──────────────────▼──────────────────────┐
                    │           EDR 核心驅動                    │
                    │                                          │
                    │  ┌────────────────────────────────────┐  │
                    │  │ PsSetCreateProcessNotifyRoutineEx  │  │ ← 進程建立/終止
                    │  │ PsSetCreateThreadNotifyRoutine     │  │ ← 執行緒建立（注入偵測）
                    │  │ PsSetLoadImageNotifyRoutine        │  │ ← DLL 載入
                    │  │ ObRegisterCallbacks                │  │ ← Handle 操作保護
                    │  ├────────────────────────────────────┤  │
                    │  │ Minifilter（FltMgr, Altitude ~3x） │  │ ← 檔案操作
                    │  ├────────────────────────────────────┤  │
                    │  │ WFP Callout                        │  │ ← 網路連線
                    │  ├────────────────────────────────────┤  │
                    │  │ ETW Provider / Consumer            │  │ ← 補充感測
                    │  └────────────────────────────────────┘  │
                    └─────────────────────────────────────────┘
```

## 進程建立感測器

```c
// 建立時記錄完整資訊，送到用戶態分析
VOID ProcessCallback(PEPROCESS Process, HANDLE Pid, PPS_CREATE_NOTIFY_INFO CreateInfo)
{
    if (CreateInfo == NULL) {
        // 進程終止：清理 Process Context
        RemoveProcessContext(Pid);
        return;
    }

    PROCESS_EVENT evt = { 0 };
    evt.Type      = EVENT_PROCESS_CREATE;
    evt.Pid       = (ULONG64)Pid;
    evt.ParentPid = (ULONG64)CreateInfo->ParentProcessId;
    
    if (CreateInfo->ImageFileName)
        RtlCopyUnicodeString(&evt.ImagePath, CreateInfo->ImageFileName);
    if (CreateInfo->CommandLine)
        RtlCopyUnicodeString(&evt.CommandLine, CreateInfo->CommandLine);

    // 取得 Process Token 資訊（提權偵測）
    evt.IsElevated = IsElevatedProcess(Process);

    // 送到共享記憶體 Ring Buffer → 用戶態服務讀取
    PushEvent(&gEventRingBuffer, &evt);
}
```

## 行程注入偵測

```c
// 執行緒建立回調：偵測 CreateRemoteThread
VOID ThreadCallback(HANDLE Pid, HANDLE Tid, BOOLEAN Create)
{
    if (!Create) return;
    
    // 建立執行緒的進程 ≠ 目標進程的 PID → 可能是遠端執行緒注入
    HANDLE creatorPid = PsGetCurrentProcessId();
    if (creatorPid != Pid) {
        INJECTION_ALERT alert = {
            .TargetPid   = (ULONG64)Pid,
            .CreatorPid  = (ULONG64)creatorPid,
            .ThreadId    = (ULONG64)Tid,
            .Type        = INJECT_REMOTE_THREAD
        };
        RaiseAlert(&alert);
    }
}
```

## 記憶體保護監控

EDR 也監控 `NtProtectVirtualMemory`（`PAGE_EXECUTE_READWRITE`）和 `NtAllocateVirtualMemory`（`MEM_COMMIT + PAGE_EXECUTE`）。

在用戶態 Hook（Userland Hooking）層可以做，但核心層更可靠：

```c
// Minifilter 或 ObCallback 層監控
// 但 VirtualMemory 操作是通過 syscall，
// 核心層最好用 ETW Microsoft-Windows-Kernel-Process 的 VIRTUAL_ALLOC 事件監控
// 或在 ntdll 的 Ntdll Hook 層（用戶態 DLL 注入到所有進程）
```

**用戶態 DLL 注入**：EDR 用 `PsSetLoadImageNotifyRoutine` 偵測 ntdll 載入，然後把自己的 DLL 注入目標進程，從用戶態 Hook `NtProtectVirtualMemory` 等系統呼叫。

## Minifilter 感測器

```c
// 監控敏感路徑（SAM hive、credential files 等）
FLT_PREOP_CALLBACK_STATUS PreReadCallback(
    PFLT_CALLBACK_DATA    Data,
    PCFLT_RELATED_OBJECTS FltObjects,
    PVOID                *CompletionContext)
{
    FLT_FILE_NAME_INFORMATION *nameInfo;
    NTSTATUS status = FltGetFileNameInformation(Data,
        FLT_FILE_NAME_NORMALIZED | FLT_FILE_NAME_QUERY_DEFAULT,
        &nameInfo);
    
    if (NT_SUCCESS(status)) {
        FltParseFileNameInformation(nameInfo);
        
        // 偵測 LSASS dump 或 SAM hive 存取
        static const WCHAR sam[]    = L"\\SAM";
        static const WCHAR ntds[]   = L"\\NTDS.dit";
        
        UNICODE_STRING samStr, ntdsStr;
        RtlInitUnicodeString(&samStr, sam);
        RtlInitUnicodeString(&ntdsStr, ntds);
        
        if (RtlSuffixUnicodeString(&samStr, &nameInfo->Name, TRUE) ||
            RtlSuffixUnicodeString(&ntdsStr, &nameInfo->Name, TRUE)) {
            
            DbgPrint("[EDR] Sensitive file access: %wZ by PID %llu\n",
                     &nameInfo->Name, (ULONG64)PsGetCurrentProcessId());
            // 查詢呼叫者是否可疑
        }
        
        FltReleaseFileNameInformation(nameInfo);
    }
    
    return FLT_PREOP_SUCCESS_NO_CALLBACK;
}
```

## WFP 感測器（連線記錄）

```c
// 記錄所有出站連線（ip:port + pid）
void NTAPI ConnectClassify(
    const FWPS_INCOMING_VALUES0          *vals,
    const FWPS_INCOMING_METADATA_VALUES0 *meta,
    void *layerData, const void *ctx,
    const FWPS_FILTER3 *filter, UINT64 flowCtx,
    FWPS_CLASSIFY_OUT0 *out)
{
    NETWORK_EVENT evt = {
        .Pid        = (ULONG32)meta->processId,
        .LocalAddr  = vals->incomingValue[FWPS_FIELD_ALE_AUTH_CONNECT_V4_IP_LOCAL_ADDRESS].value.uint32,
        .RemoteAddr = vals->incomingValue[FWPS_FIELD_ALE_AUTH_CONNECT_V4_IP_REMOTE_ADDRESS].value.uint32,
        .RemotePort = RtlUshortByteSwap(
            vals->incomingValue[FWPS_FIELD_ALE_AUTH_CONNECT_V4_IP_REMOTE_PORT].value.uint16),
    };
    
    // 比對 C2 IoC（Indicators of Compromise）
    if (IsKnownC2(&evt.RemoteAddr)) {
        evt.Suspicious = TRUE;
        out->actionType = FWP_ACTION_BLOCK;  // 直接封鎖！
    } else {
        out->actionType = FWP_ACTION_PERMIT;
    }
    
    PushNetworkEvent(&gNetRingBuffer, &evt);
}
```

## 核心 → 用戶態通訊

EDR 核心驅動需要把事件傳給用戶態分析服務。常見方法：

```
1. IoCompletion Port（IOCP）：
   用戶態 ReadFile（阻塞）→ 核心完成 IRP → 事件到達
   優點：低延遲，標準 Windows API

2. 共享記憶體 Ring Buffer：
   核心 ExAllocatePool → MmAllocateMdl → MmMapLockedPages(UserMode)
   用戶態 映射相同物理頁面 → 零複製讀事件
   優點：極高吞吐（Sysmon 用此方式）

3. ETW Session：
   核心發送 ETW 事件 → 用戶態 StartTrace + ProcessTrace 讀取
   優點：解耦合，多個 Consumer 可以同時訂閱
```

## PPL（Protected Process Light）保護 EDR 進程

EDR 服務通常以 PPL 方式啟動，防止普通進程用 `TerminateProcess` 殺死它：

```c
// EDR 服務安裝時設定 PPL 屬性（需要代碼簽章認證）
// 用戶態無法 OpenProcess 取得 PROCESS_TERMINATE 權限

// 但 ObRegisterCallbacks 的 Handle 降權進一步保護：
// 即使有核心 exploit，也難以通過 Handle 殺死 PPL 進程
```

## 自我檢核

- [ ] EDR 核心感測器組合：Callbacks + Minifilter + WFP + ETW（縱深防禦）
- [ ] 進程注入偵測：`ThreadCallback` 中 `creatorPid ≠ targetPid` = 遠端執行緒
- [ ] 核心→用戶態通訊：IOCP（低延遲）/ 共享記憶體（高吞吐）/ ETW（解耦合）
- [ ] PPL 保護 EDR 進程：普通進程無法 TerminateProcess；ObCallback 進一步降低 Handle 權限
- [ ] Minifilter 偵測 SAM/NTDS.dit 存取（Credential 竊取指標）

→ [Ch 39 Anti-EDR 技術](./39-anti-edr.md)
