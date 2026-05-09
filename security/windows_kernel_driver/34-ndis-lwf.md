# Ch 34 — NDIS 輕量篩選

> 目標：理解 NDIS（Network Driver Interface Specification）協議層，掌握 LWF（Lightweight Filter）驅動的架構，能在封包進出 NIC 時攔截和修改。

## NDIS 架構

NDIS 是 Windows 網路驅動的基礎框架，定義了 Protocol Driver、Intermediate Driver 和 Miniport Driver 之間的介面。

```
應用程式（TCP/UDP）
       ↓
  Protocol Driver（tcpip.sys、ndisprot.sys 等）
       ↓
  ┌────────────────────────────────────────┐
  │       NDIS Filter Stack               │
  │  LWF 1（最上層，最後看到）             │
  │  LWF 2                                │
  │  LWF 3（最下層，最先看到）             │
  └────────────────────────────────────────┘
       ↓
  Miniport Driver（網卡驅動）
       ↓
  Physical NIC
```

**LWF vs WFP**：
- LWF 在 NDIS 層（比 IP 層更低），看到原始的乙太網路幀（包含 Ethernet header）
- WFP 在 IP 層以上，更容易操作 TCP/UDP 連線語義
- LWF 更適合做：封包複製（tap）、VLAN tagging、加密通道
- WFP 更適合做：連線過濾、應用程式識別

## LWF 驅動框架

### DriverEntry

```c
#include <ndis.h>

// LWF 驅動的全域 Handle
NDIS_HANDLE gFilterDriverHandle = NULL;

NTSTATUS DriverEntry(PDRIVER_OBJECT DriverObject, PUNICODE_STRING RegistryPath)
{
    NDIS_FILTER_DRIVER_CHARACTERISTICS chars = { 0 };

    chars.Header.Type     = NDIS_OBJECT_TYPE_FILTER_DRIVER_CHARACTERISTICS;
    chars.Header.Revision = NDIS_FILTER_CHARACTERISTICS_REVISION_3;
    chars.Header.Size     = NDIS_SIZEOF_FILTER_DRIVER_CHARACTERISTICS_REVISION_3;

    chars.MajorNdisVersion = NDIS_FILTER_MAJOR_VERSION;  // 6
    chars.MinorNdisVersion = NDIS_FILTER_MINOR_VERSION;  // 85（Win 10）

    chars.FriendlyName = NDIS_STRING_CONST("MyLWF");
    chars.UniqueName   = NDIS_STRING_CONST("{A1B2C3D4-...}");  // 唯一 GUID 字串
    chars.ServiceName  = NDIS_STRING_CONST("MyLwfService");

    // 必要的回調
    chars.AttachHandler  = FilterAttach;
    chars.DetachHandler  = FilterDetach;
    chars.RestartHandler = FilterRestart;
    chars.PauseHandler   = FilterPause;

    // 封包處理回調（選配，不設定就 passthrough）
    chars.SendNetBufferListsHandler    = FilterSendNetBufferLists;    // 出站
    chars.ReceiveNetBufferListsHandler = FilterReceiveNetBufferLists; // 入站

    NTSTATUS status = NdisFRegisterFilterDriver(
        DriverObject,
        (NDIS_HANDLE)DriverObject,
        &chars,
        &gFilterDriverHandle);

    return NT_SUCCESS(status) ? STATUS_SUCCESS : status;
}
```

### Attach / Detach（每個網卡一次）

```c
// 每個網卡插上 LWF 時呼叫
NDIS_STATUS FilterAttach(
    NDIS_HANDLE NdisFilterHandle,
    NDIS_HANDLE FilterDriverContext,
    PNDIS_FILTER_ATTACH_PARAMETERS AttachParameters)
{
    // 分配 Filter Context（每個網卡一份）
    PFILTER_CONTEXT ctx = ExAllocatePoolWithTag(
        NonPagedPoolNx, sizeof(FILTER_CONTEXT), 'FwlF');
    if (!ctx) return NDIS_STATUS_RESOURCES;

    RtlZeroMemory(ctx, sizeof(FILTER_CONTEXT));
    ctx->FilterHandle = NdisFilterHandle;

    // 設定這個 Filter 的特性
    NDIS_FILTER_ATTRIBUTES attr = { 0 };
    attr.Header.Revision = NDIS_FILTER_ATTRIBUTES_REVISION_1;
    attr.Header.Size     = NDIS_SIZEOF_FILTER_ATTRIBUTES_REVISION_1;
    attr.Header.Type     = NDIS_OBJECT_TYPE_FILTER_ATTRIBUTES;
    attr.Flags           = 0;

    NDIS_STATUS status = NdisFSetAttributes(NdisFilterHandle, ctx, &attr);
    if (status != NDIS_STATUS_SUCCESS) {
        ExFreePoolWithTag(ctx, 'FwlF');
        return status;
    }

    DbgPrint("[LWF] Attached to adapter: %wZ\n",
             AttachParameters->BaseMiniportName);
    return NDIS_STATUS_SUCCESS;
}

VOID FilterDetach(NDIS_HANDLE FilterModuleContext)
{
    PFILTER_CONTEXT ctx = (PFILTER_CONTEXT)FilterModuleContext;
    ExFreePoolWithTag(ctx, 'FwlF');
    DbgPrint("[LWF] Detached\n");
}
```

### 出站封包攔截

```c
// 每次有 NBL（Net Buffer List）要送出時呼叫
VOID FilterSendNetBufferLists(
    NDIS_HANDLE        FilterModuleContext,
    PNET_BUFFER_LIST   NetBufferLists,
    NDIS_PORT_NUMBER   PortNumber,
    ULONG              SendFlags)
{
    PFILTER_CONTEXT ctx = (PFILTER_CONTEXT)FilterModuleContext;

    // 遍歷 NBL 鏈
    for (PNET_BUFFER_LIST nbl = NetBufferLists; nbl != NULL; nbl = NET_BUFFER_LIST_NEXT_NBL(nbl)) {
        // 取得第一個 NB 的資料
        PNET_BUFFER nb = NET_BUFFER_LIST_FIRST_NB(nbl);
        
        // 取得可讀資料指針（對 contiguous buffer 有效）
        PUCHAR data = NdisGetDataBuffer(nb, sizeof(ETHERNET_HEADER), NULL, 1, 0);
        if (data) {
            PETHERNET_HEADER eth = (PETHERNET_HEADER)data;
            DbgPrint("[LWF] OUT ETH type: %04X\n", RtlUshortByteSwap(eth->EtherType));
        }
    }

    // 放行（傳給下層）
    NdisFSendNetBufferLists(
        ctx->FilterHandle,
        NetBufferLists,
        PortNumber,
        SendFlags);
}
```

### 入站封包攔截

```c
// 每次有 NBL 從下層（NIC）來時呼叫
VOID FilterReceiveNetBufferLists(
    NDIS_HANDLE      FilterModuleContext,
    PNET_BUFFER_LIST NetBufferLists,
    NDIS_PORT_NUMBER PortNumber,
    ULONG            NumberOfNetBufferLists,
    ULONG            ReceiveFlags)
{
    PFILTER_CONTEXT ctx = (PFILTER_CONTEXT)FilterModuleContext;

    // 遍歷並記錄（簡化版：全部放行）
    DbgPrint("[LWF] IN %u NBLs\n", NumberOfNetBufferLists);

    // 放行（傳給上層）
    NdisFIndicateReceiveNetBufferLists(
        ctx->FilterHandle,
        NetBufferLists,
        PortNumber,
        NumberOfNetBufferLists,
        ReceiveFlags);
}
```

## INF 安裝設定

LWF 需要 INF 檔定義 `FilterClass`、`FilterMediaTypes` 和 `FilterRunType`：

```ini
[Version]
Class=NetService
ClassGUID={4D36E974-E325-11CE-BFC1-08002BE10318}

[MyLwf.Service]
ServiceBinary=%12%\mylwf.sys
ServiceType=1           ; SERVICE_KERNEL_DRIVER
StartType=1             ; SERVICE_SYSTEM_START
ErrorControl=1

[MyLwf.Ndi]
HKR, Ndi,FilterClass,,"ms_firewall_lower"
HKR, Ndi,FilterDeviceInfId,,"MyLwf"
HKR, Ndi,FilterRunType,0x00010001,2  ; 2 = Optional
HKR, Ndi\Interfaces,UpperRange,,"noupper"
HKR, Ndi\Interfaces,LowerRange,,"ndis5,ethernet"
```

安裝：`netcfg -l mylwf.inf -c s -i MyLwfService`

## NET_BUFFER_LIST 結構

NBL 是 NDIS 中封包的核心資料結構，理解它是 LWF 開發的基礎：

```
NET_BUFFER_LIST
├── pNext                  → 下一個 NBL（鏈）
├── FirstNetBuffer         → NET_BUFFER 鏈（通常一個 NBL 對應多個 NB）
├── Context                → 每個 NBL 的私有上下文
└── NET_BUFFER
    ├── pNext              → 下一個 NB
    ├── MdlChain           → MDL 鏈（描述記憶體）
    ├── DataOffset         → 資料起始偏移（Ethernet header 可能在 MDL 中間）
    └── DataLength         → 有效資料長度
```

`NdisGetDataBuffer(NB, sizeof(T), backupBuf, align, offset)` — 如果資料是連續的，返回指向原始 MDL 的指針；如果分散，複製到 backupBuf 再返回。

## LWF 與 WFP 選用指南

| 需求 | 選用 |
|-----|------|
| 封包複製（TAP、監聽全部流量） | LWF |
| 以乙太網路幀為單位操作 | LWF |
| 連線過濾（允許/拒絕特定 IP:Port） | WFP |
| 取得進程資訊（哪個 PID 發的封包） | WFP（ALE 層有 ProcessId） |
| 動態封包修改 + 注入 | WFP（FwpsInject*）更安全 |
| 在 NIC 以上最低層 | LWF |

## 自我檢核

- [ ] LWF 位於 NDIS 層（比 WFP/IP 層低），看到完整乙太網路幀
- [ ] `NdisFRegisterFilterDriver` → `FilterAttach` → `FilterRestart` → 進入工作狀態
- [ ] `FilterSendNetBufferLists`（出站） / `FilterReceiveNetBufferLists`（入站）：最後必須呼叫 `NdisFSendNetBufferLists` / `NdisFIndicateReceiveNetBufferLists` 傳給下/上層
- [ ] `NdisGetDataBuffer` 取得連續資料指針；NBL/NB/MDL 三層結構
- [ ] 封包複製/TAP 選 LWF；連線級過濾選 WFP

→ [Ch 35 BYOVD 攻擊](./35-byovd.md)
