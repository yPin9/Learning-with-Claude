# Ch 33 — WFP 網路過濾

> 目標：理解 Windows Filtering Platform（WFP）的架構，用 Callout Driver 實作封包過濾和網路監控，並了解惡意軟體如何用 WFP 做流量劫持。

## WFP 架構概覽

WFP（Windows Filtering Platform）是 Vista 引入的標準網路過濾框架，取代了舊版的 NDIS 包過濾（較低層）和 LSP（用戶態，容易被濫用）。

```
用戶態進程（HTTP 請求）
       ↓
 User-Mode API（WinSock / WinHTTP）
       ↓
    TCP/IP Stack（tcpip.sys）
       ↓
 ┌─────────────────────────────────────┐
 │        WFP Filter Engine           │  ← Base Filtering Engine (BFE)
 │   ┌──────────────────────────────┐  │
 │   │  Filter Layer (FWPM_LAYER_*) │  │  ← 多個攔截層
 │   │   INBOUND / OUTBOUND         │  │
 │   └──────────────────────────────┘  │
 └─────────────────────────────────────┘
       ↓
 Callout Driver（你的驅動）
       ↓
   Network Interface Card (NIC)
```

WFP 在多個「Layer」上運作，每個 Layer 代表 TCP/IP Stack 的不同位置：

| Layer 名稱 | 位置 | 常見用途 |
|-----------|------|---------|
| `FWPM_LAYER_ALE_AUTH_CONNECT_V4` | 連線建立時 | 防火牆：允許/拒絕連線 |
| `FWPM_LAYER_ALE_AUTH_RECV_ACCEPT_V4` | 接受連線時 | 防火牆：入站過濾 |
| `FWPM_LAYER_STREAM_V4` | TCP 資料流 | 內容過濾（DPI） |
| `FWPM_LAYER_DATAGRAM_DATA_V4` | UDP 資料 | UDP 過濾 |
| `FWPM_LAYER_OUTBOUND_IPPACKET_V4` | IP 封包出站 | 封包修改 |
| `FWPM_LAYER_INBOUND_IPPACKET_V4` | IP 封包入站 | 封包修改 |

## 實作 Callout Driver

### 步驟 1：DriverEntry 中注冊 Callout 和 Filter

```c
#include <ntddk.h>
#include <wdm.h>
#include <fwpsk.h>   // WFP kernel API
#include <fwpmk.h>   // WFP management API

// Callout GUID（每個 Callout 需要唯一的 GUID）
// {A5A46C2A-E888-4C6E-A5D4-1234567890AB}
static const GUID CALLOUT_GUID = {
    0xa5a46c2a, 0xe888, 0x4c6e,
    { 0xa5, 0xd4, 0x12, 0x34, 0x56, 0x78, 0x90, 0xab }
};

static UINT32  gCalloutId  = 0;
static HANDLE  gEngineHandle = NULL;

// 核心過濾 Callout 函式
void NTAPI ClassifyFn(
    const FWPS_INCOMING_VALUES0          *inFixedValues,
    const FWPS_INCOMING_METADATA_VALUES0 *inMetaValues,
    void                                 *layerData,
    const void                           *classifyContext,
    const FWPS_FILTER3                   *filter,
    UINT64                               flowContext,
    FWPS_CLASSIFY_OUT0                   *classifyOut)
{
    UNREFERENCED_PARAMETER(classifyContext);
    UNREFERENCED_PARAMETER(flowContext);
    UNREFERENCED_PARAMETER(filter);

    // 從 inFixedValues 取得連線資訊
    // 以 ALE_AUTH_CONNECT_V4 層為例
    UINT32 localAddr  = inFixedValues->incomingValue[
        FWPS_FIELD_ALE_AUTH_CONNECT_V4_IP_LOCAL_ADDRESS].value.uint32;
    UINT32 remoteAddr = inFixedValues->incomingValue[
        FWPS_FIELD_ALE_AUTH_CONNECT_V4_IP_REMOTE_ADDRESS].value.uint32;
    UINT16 remotePort = inFixedValues->incomingValue[
        FWPS_FIELD_ALE_AUTH_CONNECT_V4_IP_REMOTE_PORT].value.uint16;
    UINT32 pid        = (UINT32)(ULONG_PTR)inMetaValues->processId;

    DbgPrint("[WFP] PID %u connect: %u.%u.%u.%u → %u.%u.%u.%u:%u\n",
             pid,
             (localAddr >> 24) & 0xFF, (localAddr >> 16) & 0xFF,
             (localAddr >> 8)  & 0xFF,  localAddr & 0xFF,
             (remoteAddr >> 24) & 0xFF, (remoteAddr >> 16) & 0xFF,
             (remoteAddr >> 8)  & 0xFF,  remoteAddr & 0xFF,
             RtlUshortByteSwap(remotePort));  // Port 是 big-endian

    // 允許繼續：
    classifyOut->actionType = FWP_ACTION_PERMIT;

    // 拒絕（封鎖）：
    // if (remotePort == RtlUshortByteSwap(4444)) {  // 封鎖 4444 port
    //     classifyOut->actionType = FWP_ACTION_BLOCK;
    //     classifyOut->rights &= ~FWPS_RIGHT_ACTION_WRITE;
    // }
}

NTSTATUS NTAPI NotifyFn(
    FWPS_CALLOUT_NOTIFY_TYPE notifyType,
    const GUID              *filterKey,
    FWPS_FILTER3            *filter)
{
    UNREFERENCED_PARAMETER(notifyType);
    UNREFERENCED_PARAMETER(filterKey);
    UNREFERENCED_PARAMETER(filter);
    return STATUS_SUCCESS;
}

void NTAPI FlowDeleteFn(UINT16 layerId, UINT32 calloutId, UINT64 flowContext)
{
    UNREFERENCED_PARAMETER(layerId);
    UNREFERENCED_PARAMETER(calloutId);
    UNREFERENCED_PARAMETER(flowContext);
}
```

### 步驟 2：向 WFP Engine 注冊

```c
NTSTATUS RegisterCallout(PDEVICE_OBJECT DeviceObject)
{
    // 1. 注冊 Callout（Kernel 層）
    FWPS_CALLOUT3 callout = { 0 };
    callout.calloutKey    = CALLOUT_GUID;
    callout.classifyFn    = ClassifyFn;
    callout.notifyFn      = NotifyFn;
    callout.flowDeleteFn  = FlowDeleteFn;

    NTSTATUS status = FwpsCalloutRegister3(DeviceObject, &callout, &gCalloutId);
    if (!NT_SUCCESS(status)) {
        DbgPrint("[WFP] FwpsCalloutRegister3 failed: %08X\n", status);
        return status;
    }

    // 2. 開啟 WFP Engine（Management 層）
    status = FwpmEngineOpen0(NULL, RPC_C_AUTHN_WINNT, NULL, NULL, &gEngineHandle);
    if (!NT_SUCCESS(status)) return status;

    // 3. 開始 Transaction
    status = FwpmTransactionBegin0(gEngineHandle, 0);
    if (!NT_SUCCESS(status)) return status;

    // 4. 注冊 Callout 到 Management（FWPM）
    FWPM_CALLOUT0 fwpmCallout = { 0 };
    fwpmCallout.calloutKey   = CALLOUT_GUID;
    fwpmCallout.displayData.name        = L"MonitorCallout";
    fwpmCallout.displayData.description = L"Connection monitor";
    fwpmCallout.applicableLayer         = FWPM_LAYER_ALE_AUTH_CONNECT_V4;

    status = FwpmCalloutAdd0(gEngineHandle, &fwpmCallout, NULL, NULL);
    if (!NT_SUCCESS(status)) { FwpmTransactionAbort0(gEngineHandle); return status; }

    // 5. 加入 Filter（把 Callout 掛到 Layer 上）
    FWPM_FILTER0 filter = { 0 };
    GUID filterGuid;
    ExUuidCreate(&filterGuid);

    filter.filterKey        = filterGuid;
    filter.displayData.name = L"MonitorFilter";
    filter.layerKey         = FWPM_LAYER_ALE_AUTH_CONNECT_V4;
    filter.action.type      = FWP_ACTION_CALLOUT_INSPECTION;  // 只看，不 block
    filter.action.calloutKey = CALLOUT_GUID;
    filter.weight.type      = FWP_EMPTY;  // 預設權重

    // 不加 Condition = 匹配所有連線
    filter.numFilterConditions = 0;

    UINT64 filterId;
    status = FwpmFilterAdd0(gEngineHandle, &filter, NULL, &filterId);
    if (!NT_SUCCESS(status)) { FwpmTransactionAbort0(gEngineHandle); return status; }

    // 6. Commit
    status = FwpmTransactionCommit0(gEngineHandle);
    return status;
}

// DriverUnload
void UnregisterCallout(void)
{
    if (gEngineHandle) {
        FwpmEngineClose0(gEngineHandle);
        gEngineHandle = NULL;
    }
    if (gCalloutId) {
        FwpsCalloutUnregisterById0(gCalloutId);
        gCalloutId = 0;
    }
}
```

## 封包修改（Packet Injection）

光過濾還不夠——有些應用需要改封包再送出（NAT、透明代理）。

WFP 提供注入 API：

```c
// 建立注入 Handle（在 DriverEntry）
HANDLE gInjectionHandle;
FwpsInjectionHandleCreate0(AF_INET, FWPS_INJECTION_TYPE_NETWORK, &gInjectionHandle);

// 修改封包後重新注入（在 ClassifyFn 中）
void InjectModifiedPacket(
    NET_BUFFER_LIST *nbl,
    COMPARTMENT_ID   compartmentId)
{
    // Clone 封包
    NET_BUFFER_LIST *clonedNbl = NULL;
    FwpsAllocateCloneNetBufferList0(nbl, NULL, NULL, 0, &clonedNbl);
    
    // 修改 clone 的內容（例如改 IP header）
    // ... 省略修改邏輯 ...
    
    // 注入回 Stack
    FwpsInjectNetworkSendAsync0(
        gInjectionHandle,
        NULL,          // classify handle
        0,             // flags
        compartmentId,
        clonedNbl,
        NULL,          // completionFn（可以是 NULL）
        NULL);

    // 丟棄原始封包
    // classifyOut->actionType = FWP_ACTION_BLOCK;
}
```

## 惡意軟體的 WFP 應用

WFP 是合法的核心 API，但惡意軟體也拿來用：

1. **流量劫持**：注冊 Callout + 把目標連線重導到自己的代理（C2 隱藏）
2. **防火牆繞過**：注冊高優先權 Filter，在防毒/EDR 的 Filter 之前 Permit 自己的流量
3. **隱身流量**：攔截所有到 C2 server 的封包，讓 Wireshark 看不到（因為 Wireshark 在 NDIS 層，WFP 可以在 IP 層攔截後重注入）

## netsh 工具

```cmd
# 查看所有 WFP Filter
netsh wfp show filters

# 查看所有 Callout
netsh wfp show callouts

# 匯出 WFP 狀態（XML）
netsh wfp show state

# 查看 BFE 服務
sc query bfe
```

## 自我檢核

- [ ] WFP Layer：`ALE_AUTH_CONNECT` 攔截連線建立；`STREAM` 攔截 TCP 資料；`INBOUND/OUTBOUND_IPPACKET` 攔截 IP 封包
- [ ] `ClassifyFn` 設 `FWP_ACTION_BLOCK` 拒絕；`FWP_ACTION_CALLOUT_INSPECTION` 只監控不干預
- [ ] 注冊流程：`FwpsCalloutRegister3` (kernel) → `FwpmCalloutAdd0` (management) → `FwpmFilterAdd0` (bind to layer)
- [ ] `FwpsInjectNetworkSendAsync0` 重注入修改後的封包
- [ ] DriverUnload：`FwpmEngineClose0` + `FwpsCalloutUnregisterById0`

→ [Ch 34 NDIS 輕量篩選](./34-ndis-lwf.md)
