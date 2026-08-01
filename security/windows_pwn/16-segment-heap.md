# Ch 16 — Segment Heap（Win10+ 現代預設堆）

> **目標**：理解 Segment Heap 的四個元件（Backend、VS、LFH、Large）、它們各自的責任範圍與分工，以及它和 NT Heap + LFH 的根本架構差異；能判斷「這個行程用的是 Segment Heap 還是 NT Heap」，並知道這個判斷對 Part 4 heap 利用的影響。

## 為什麼需要這個？

你學完 NT Heap（Ch 14）和 LFH（Ch 15），有了一套完整的 Windows heap 知識——問題是，那套知識在現代 Windows 系統行程裡**可能是錯的**。

Win 10 引入了 Segment Heap，接管了一類行程的所有堆分配。如果你對著一個 Edge 瀏覽器行程或 `svchost.exe` 用 NT Heap 的眼光做分析，你查到的結構、算出的 bucket 全部對不上。

這章的目標是：
1. 搞清楚 Segment Heap 的設計——哪些行程用它、為什麼 Microsoft 要引入
2. 理解四個元件（Backend / VS / LFH / Large）各自管什麼
3. 知道和 NT Heap 的哪些地方不同，以及對 exploit 的影響
4. 能在開始分析一個目標前，先判斷它用的是哪種堆

> **本章主要基於 Mark Vincent Yason 的 Black Hat US 2016 論文《Windows 10 Segment Heap Internals》**，這是 Segment Heap 目前最完整的公開學術剖析。所有結構名稱和偏移以 Yason 的分析為基準，但 Windows 持續更新，細節偏移以 `dt ntdll!_SEGMENT_HEAP` 實際輸出為準。

## 先建立直覺：為什麼要換掉 NT Heap？

NT Heap + LFH 已經工作了幾十年，為什麼 Microsoft 要在 Win 10 引入 Segment Heap？

原因是**安全性**和**效能的結構性改進**：

**NT Heap 的問題**：
- chunk header（`_HEAP_ENTRY`）就在 user data 旁邊，overflow 一個位元組就可能破壞 header
- LFH 的 UserBlocks 共用同一塊記憶體，chunk 之間沒有隔離
- metadata（FreeLists 指標、CommitRoutine）和 user data 在同一個 address space 區域，有地址洩漏就可能直接定位
- 安全加固靠 XOR cookie（Ch 17），本質是 obfuscation，不是 isolation

**Segment Heap 的設計原則**：
- **元件隔離**：Backend、VS allocator、LFH 各自管理不同類型的分配，metadata 和 user data 更清楚地分離
- **更強的 integrity check**：每個元件有自己的 signature 和 check，不只靠 XOR
- **更細的 page-level 控制**：以 page（4KB）為單位管理 commit/decommit，減少記憶體浪費
- **對應現代 OS 安全**：和 CFG、Heap Randomization 等機制更緊密整合

**jemalloc 的影響**：Segment Heap 的架構（segment + slab 的雙層結構，metadata 和 data 的分離思路）明顯受到 jemalloc 影響。這和 Android 轉向 jemalloc（後來 Scudo）、FreeBSD 用 jemalloc 是同一個時代的設計思潮。

## 哪些行程用 Segment Heap？（關鍵問題）

> 認識論警告：「哪些行程用 Segment Heap」在不同 Windows 版本和更新下有漂移，這裡給的是常見原則，**以你環境的實際觀察為準**。

**確定用 Segment Heap 的**：
- **Modern/UWP/packaged apps**（如 Edge 的部分元件、Calculator、Windows Store apps）
- **多數系統行程**（`svchost.exe`、`lsass.exe`、`dwm.exe` 等）——從 Win 10 某版本後逐漸遷移
- 行程的 `PEB.NtGlobalFlag` 或特定環境變數設定可以強制啟用

**通常仍用 NT Heap 的**：
- **舊式 win32 exe**（不是 UWP/packaged，普通 C runtime 應用）
- 你自己用 `HeapCreate` 建的 heap——`HeapCreate` 始終建 NT Heap
- 用 `HEAP_NO_SERIALIZE` 等舊旗標建的 heap

**判斷方法**（分析目標前必做）：

```
> **未實測（需 WinDbg + symbols）**
>
> # 看 PEB 的 ProcessDefaultHeap 是不是 SEGMENT_HEAP
> dt ntdll!_SEGMENT_HEAP poi(peb+0x30)
> # 如果輸出的 Signature 是 0xDDEEDDEE，就是 Segment Heap
> # 如果是 0xEEFFEEFF，就是 NT Heap
>
> # 或直接看 PEB 的 HeapCompatibilityMode
> dt ntdll!_PEB poi($peb)
> # HeapCompatibilityMode = 2 → Segment Heap
```

```python
# Python 替代方案：讀 heap 基址的前幾個 bytes 看 signature
import ctypes
k = ctypes.windll.kernel32
k.GetProcessHeap.restype = ctypes.c_void_p
h = k.GetProcessHeap()

# 讀 heap 起點的 16 bytes，看 signature 欄位
hdr = (ctypes.c_uint32 * 4).from_address(h)
# NT Heap signature: 0xEEFFEEFF（在 _HEAP 的 SegmentSignature 欄位）
# Segment Heap signature: 0xDDEEDDEE（在 _SEGMENT_HEAP.Signature 欄位）
# 注意：signature 不是在偏移 0，要找對欄位位置
print(f"Heap base: 0x{h:016X}")
print(f"First 4 DWORDs: {[hex(hdr[i]) for i in range(4)]}")
# 這個腳本不能直接判斷（signature 在不同 offset），用 WinDbg dt 更可靠
```

## Segment Heap 的四個元件

Segment Heap 不是一個 allocator，而是**四個 allocator 的協調者**：

```
  HeapAlloc(heap, 0, size)
       │
       ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │  _SEGMENT_HEAP 分配路由                                          │
  │                                                                   │
  │  size <= 0x400 (1KB) ?                                           │
  │    → 查 LFH bucket                                               │
  │       LFH 有 slot → LFH front-end 服務                          │
  │       LFH 無 slot 或未啟用 → VS allocator                       │
  │                                                                   │
  │  0x400 < size <= 0xF000 (60KB) ?                                │
  │    → VS (Variable Size) allocator                                │
  │                                                                   │
  │  size > 0xF000 ?                                                 │
  │    → Large block allocator（直接 VirtualAlloc）                  │
  │                                                                   │
  │  所有 allocator 最終都向 Backend 要 pages                        │
  └─────────────────────────────────────────────────────────────────┘
```

> size 邊界是常見值（基於 Yason BH2016），可能隨 Windows 版本調整；以實測為準。

### 元件 1：Backend（Segment 管理者）

Backend 管的是**頁面（page）層面的 commit/decommit**，是其他三個元件的供應商。

```
  _SEGMENT_HEAP
   └─ SegContexts[]         ← 多個 _HEAP_SEG_CONTEXT（一般 2 個）
       └─ _HEAP_SEG_CONTEXT
           ├─ SegmentListHead  ← 所有 segment 的雙向鏈
           └─ Segments[] → _HEAP_PAGE_SEGMENT → _HEAP_PAGE_RANGE_DESCRIPTOR[]
                                                  （每個描述一個 page range 的狀態）
```

Backend 的核心概念：

```
  _HEAP_PAGE_SEGMENT（VS/Backend 的分配單元）：
  ┌───────────────────────────────────────────────────────────────┐
  │  Segment header（含 _HEAP_SEG_CONTEXT 指標、signature 等）   │
  ├───────────────────────────────────────────────────────────────┤
  │  Page Range Descriptors（256 個，每個描述 1 個或多個 pages）  │
  │  [PRD 0] [PRD 1] [PRD 2] ... [PRD 255]                       │
  ├───────────────────────────────────────────────────────────────┤
  │  Actual pages（最多 256 個 4KB pages = 1MB per segment）      │
  │  [page 0][page 1][page 2] ... [page 255]                     │
  └───────────────────────────────────────────────────────────────┘

  _HEAP_PAGE_RANGE_DESCRIPTOR（每個 PRD 8 bytes）：
    +0x00 TreeNode  : _RTL_BALANCED_NODE ← AVL 樹節點（size-based）
    +0x18 RangeSize : UCHAR ← 這個 range 佔幾個 pages
    +0x19 RangeFlags: UCHAR ← busy=1, vs-chunk=2, lfh-subsegment=4...
    +0x1C UnitOffset: USHORT ← chunk 在 segment 內的 offset
```

**Backend 分配流程**：從 AVL 樹（按 range size 排序）找到合適的 free page range，commit 需要的頁面，分給 VS 或 LFH。

**glibc 對比**：glibc 的 segment 管理（heap_info）沒有頁粒度的 PRD 概念，主要靠 mprotect 和 mmap 的頁對齊分配。Segment Heap 的 PRD 讓每個分配的頁面狀態都有明確的 metadata，比 glibc 的方式更細膩——也讓 Segment Heap 更難「從 metadata 的洩漏直接算出 user data 位址」。

### 元件 2：Variable Size (VS) Allocator

VS 管理 **非 LFH 路徑的中等大小分配**（約 1KB ~ 60KB）。

VS 的核心結構：

```
  _HEAP_VS_CONTEXT（在 _SEGMENT_HEAP 內）：
    +0x000 Lock                : _RTL_SRWLOCK
    +0x008 SubsegmentList      : _LIST_ENTRY ← 所有 VS subsegment 的鏈
    +0x018 TotalCommittedUnits : ULONG64
    +0x020 FreeChunkTree       : _RTL_RB_TREE ← 按大小排序的 free chunk 紅黑樹
    +0x030 DelayFreeList        : _SLIST_HEADER
    ...
```

VS 的 free chunk 用**紅黑樹**（`_RTL_RB_TREE`）管理，按大小排序——這和 NT Heap 的 FreeLists 陣列完全不同，更接近 glibc 的 largebin（largebin 用跳表/有序雙向鏈，VS 用紅黑樹）。

VS chunk 的 header（`_HEAP_VS_CHUNK_HEADER`）：

```
  _HEAP_VS_CHUNK_HEADER（VS chunk 的 header，8 bytes，XOR 加密）：
    +0x000 Sizes   : _HEAP_VS_CHUNK_HEADER_SIZE
                       +0x000 MemoryCost  : 1 bit   ← overhead/cost
                       +0x000 UnsafeSize  : 15 bit  ← chunk 大小（in 8-byte units）
                       +0x002 UnsafePrevSize : 15 bit ← 前 chunk 大小
                       +0x004 Allocated   : 1 bit   ← busy/free bit
    +0x004 EncodedSegmentPageOffset : ULONG ← 加密的 page offset（指向所在 segment）
```

VS chunk header 也是 XOR 加密（和 NT Heap 的 cookie 不同的機制，key 來自 VS context 的隨機值）。

```
  VS chunk 在記憶體裡的佈局：

  ┌──────────────────────────────────┐
  │  _HEAP_VS_CHUNK_HEADER（8 bytes）│  ← 在 user_ptr - 0x08
  ├──────────────────────────────────┤  ← user_ptr（HeapAlloc 回傳）
  │  user data                       │
  └──────────────────────────────────┘
  ↑ next chunk = user_ptr - 0x08 + (UnsafeSize * 8)
```

注意：VS chunk header 只有 **8 bytes**（不是 NT Heap 的 16 bytes），意味著 VS 的 header overhead 更小。

### 元件 3：LFH（和 NT Heap 的 LFH 類似但不同）

Segment Heap 也有 LFH，負責小型（≤ 1KB）分配的快取。

概念上和 NT Heap 的 LFH（Ch 15）相似：per-size bucket、UserBlocks、BusyBitmap。但有幾個差異：

- UserBlocks 的 metadata（subsegment header）和 NT Heap 的格式不同
- 和 VS allocator 的互動路徑不同（VS 是 LFH 的後備，不是 NT Heap backend）
- 觸發門檻相同（約 18 次分配）
- Allocation randomization 也存在（Win 8 後延續）

```
  Segment Heap LFH 的層次：
  _HEAP_LFH_CONTEXT（在 _SEGMENT_HEAP 內）
   └─ Buckets[128]
       └─ _HEAP_LFH_BUCKET
           └─ _HEAP_LFH_SUBSEGMENT_OWNER
               └─ _HEAP_LFH_SUBSEGMENT（≈ NT Heap 的 UserBlocks）
                   ├─ Header（含 BusyBitmap）
                   └─ slots[]
```

Segment Heap 的 LFH subsegment header 有獨立的 **signature（0x19880 ...）**，和 NT Heap 的 `0xF0E0D0C0` 不同。

### 元件 4：Large Block Allocator

超過約 60KB 的分配，Segment Heap 直接走 **VirtualAlloc**，並用一個 `_HEAP_LARGE_ALLOC_DATA` 結構做 tracking：

```
  _HEAP_LARGE_ALLOC_DATA：
    +0x000 TreeNode   : _RTL_BALANCED_NODE ← 掛在 large alloc 的 AVL 樹
    +0x018 VirtualAddress : ULONG64        ← VirtualAlloc 的基址
    +0x020 UnusedBytes    : ULONG64        ← 多分配的 trailing bytes
```

和 NT Heap 的大型分配行為相同（都是 VirtualAlloc），但 tracking 機制不同（NT Heap 沒有 large alloc 的 AVL 樹，只是分配後放棄追蹤）。

## `_SEGMENT_HEAP` 結構概覽

```
  _SEGMENT_HEAP（位在 heap 基址）：
    +0x000 TotalReservedPages   : ULONG64       ← 總 reserved pages
    +0x008 TotalCommittedPages  : ULONG64
    +0x010 Signature            : ULONG         ← 0xDDEEDDEE
    +0x014 GlobalFlags          : ULONG
    +0x018 FreeCommittedPages   : ULONG64
    +0x020 Interceptor          : ULONG64       ← hook point（如 page heap）
    +0x028 ProcessHeapListIndex : USHORT
    +0x02a GlobalLockCount      : SHORT
    +0x02c GlobalLockOwner      : ULONG
    ...
    +0x040 LargeAllocMetadata   : _RTL_RB_TREE  ← Large block 的 AVL 樹
    +0x050 TotalLargeCommitted  : ULONG64
    +0x070 SegContexts[2]       : _HEAP_SEG_CONTEXT ← Backend 的 segment 管理
    +0x1B0 VsContext            : _HEAP_VS_CONTEXT  ← VS allocator
    +0x2C0 LfhContext           : _HEAP_LFH_CONTEXT ← LFH
    ...
```

> 偏移以 Yason BH2016 論文為準（Win 10 初始版）；Win 11 的實際偏移以 `dt ntdll!_SEGMENT_HEAP` 為準。

```
  Signature = 0xDDEEDDEE（Segment Heap 識別碼）
  VS 另有 XOR key 保護 chunk header
  LFH subsegment 有獨立 signature
  → 比 NT Heap 的 0xEEFFEEFF 更多層識別
```

## 架構對比圖

```
  NT Heap + LFH                     Segment Heap
  ──────────────────────────────    ──────────────────────────────────────
  _HEAP                             _SEGMENT_HEAP
  │  FreeLists[128]                 │  SegContexts[]（Backend）
  │  ListHints[128]                 │  VsContext（VS allocator）
  │  CommitRoutine（危）            │  LfhContext（LFH）
  │  FrontEndHeap → LFH             │  LargeAllocMetadata（Large）
  │                                 │
  └─ _HEAP_SEGMENT                  └─ _HEAP_PAGE_SEGMENT（Backend）
      │  chunks 線性排列                 │  PRD[]（page range descriptors）
      └─ _HEAP_ENTRY（16 bytes）         └─ pages（按 PRD 管理）
                                              ↓ VS subsegment 在這裡
                                         _HEAP_VS_SUBSEGMENT
                                              └─ _HEAP_VS_CHUNK（8 bytes header）
                                                                ↓ LFH subsegment
                                                         _HEAP_LFH_SUBSEGMENT
                                                              └─ slots + BusyBitmap

  LFH（NT Heap）                    LFH（Segment Heap）
  _HEAP_SUBSEGMENT                  _HEAP_LFH_SUBSEGMENT
  UserBlocks + BusyBitmap           相似架構，但不同結構名稱和 signature
  Signature: 0xF0E0D0C0             不同 signature，header 細節不同
```

## 和 NT Heap 的安全差異

從 exploit 視角看，Segment Heap 做了哪些讓攻擊者更難過的改變：

| 面向 | NT Heap + LFH | Segment Heap |
|---|---|---|
| Metadata 位置 | FreeLists 在 _HEAP 結構（和 user data 同一 region） | SegContexts/VsContext/LfhContext 在 _SEGMENT_HEAP（分離但同一 heap）|
| Chunk header 大小 | 16 bytes（NT Heap）/ LFH slot 也有 16 bytes | VS chunk: 8 bytes；LFH subsegment header 格式不同 |
| Header 加密 | XOR cookie（單層，key 來自 heap cookie） | VS header: 另一種 XOR（key 不同）；多層 signature |
| CommitRoutine | 存在（歷史利用目標） | 無此欄位（Interceptor 有不同語意，且有保護） |
| large alloc tracking | 無（放任） | _HEAP_LARGE_ALLOC_DATA + AVL 樹（追蹤可驗） |
| PRD（page range descriptor） | 無 | 有（每個頁面的狀態有 descriptor，完整性可查） |
| Integrity check 層數 | Signature + XOR cookie | 每個 subsystem 有獨立 signature + 更多 checks |

**對 exploit 開發的實際影響**：

1. **沒有 CommitRoutine**：NT Heap 最經典的 callback 利用路徑不存在了
2. **VS chunk header 只有 8 bytes**：overflow 到下一個 chunk 的距離更短（更容易碰到 header），但 header 加密方式不同，偽造難度不同
3. **LFH subsegment 的 signature**：overflow 覆蓋 LFH subsegment header 時，如果破壞了 signature，接下來的 LFH 操作會 AV 或 check fail——需要正確計算加密後的 signature 才能偽造
4. **PRD 完整性**：Backend 的 page range 有 PRD 保護，直接篡改分配邊界更難不被偵測

## 判斷目標行程用哪種 Heap（流程）

在開始 exploit 開發前：

```
步驟 1：確認目標行程類型
  - UWP / packaged app → 幾乎可以確定 Segment Heap
  - 系統行程 (svchost/lsass/dwm) → 大概率 Segment Heap（Win 10 1703+）
  - 普通 win32 exe → NT Heap + LFH（除非有特殊設定）

步驟 2：用 WinDbg 確認（未實測）
  cdb -c "dt ntdll!_SEGMENT_HEAP poi(poi(peb+0x30)+0x10); q" target.exe
  # 如果 Signature 欄位 = 0xDDEEDDEE → Segment Heap
  # 如果 crash 或 offset 不對 → NT Heap

步驟 3：用 Python 觀察分配行為（實測可用）
  # 看 ProcessHeap 基址起點的幾個 DWORD，找 signature
  # 或觀察分配地址的規律（Segment Heap 的 VS 地址在 page 邊界附近）

步驟 4：確認後，用對應章節的知識
  NT Heap → Ch 14 + Ch 15 + Ch 17
  Segment Heap → Ch 16 + Ch 17（Segment Heap 的加密機制）
```

## Python 觀察：Segment Heap 的行為特徵

在本機的 python.exe 行程（通常用 NT Heap），以下可觀察基本 heap 特性：

```python
import ctypes

k = ctypes.windll.kernel32
k.GetProcessHeap.restype = ctypes.c_void_p

# 觀察 ProcessHeap 的 Signature
h = k.GetProcessHeap()
sig = ctypes.c_uint32.from_address(h + 0x10).value  # NT Heap SegmentSignature offset
print(f"ProcessHeap: 0x{h:016X}")
print(f"Value at h+0x10: 0x{sig:08X}")
# NT Heap:      0xFFFEEFFEE 或 0xEEFFEEFF（視欄位位置）
# Segment Heap: 0xDDEEDDEE（在 _SEGMENT_HEAP.Signature = +0x010）
```

> 注意：這段 Python 嘗試讀 `h+0x10`，但不知道目標 heap 的 signature 是在哪個精確 offset。NT Heap 的 `SegmentSignature` 在 `+0x010`（Win11 x64 常見），Segment Heap 的 `Signature` 也在 `+0x010`——所以 `0xDDEEDDEE` 出現就是 Segment Heap。以 WinDbg 的 `dt` 為最終仲裁。

## 底層機制：VS Allocator 的 Red-Black Tree

VS allocator 最有趣的設計是用**紅黑樹**（`_RTL_RB_TREE`）管理 free chunk，而不是 NT Heap 的固定 FreeLists 陣列。

```
  VS FreeChunkTree（按 chunk 大小排序的紅黑樹）：

         [128KB free]
        /             \
  [64KB free]       [256KB free]
   /      \
 [32KB] [48KB]

  → HeapAlloc(VS, 50KB) → 找 >= 50KB 的最小 node → [64KB]
    拆成 [50KB busy] + [14KB free]，[14KB] 重新插樹
```

**glibc 對比**：glibc 的 largebin 用有序雙向鏈 + `fd_nextsize`/`bk_nextsize` 的跳表結構，允許 O(log n) 查找。VS 用的 `_RTL_RB_TREE` 是 Windows 標準的紅黑樹，O(log n) 查找，實現更乾淨。

利用視角：紅黑樹的節點（`_RTL_BALANCED_NODE`）嵌在 free chunk 的 user data 區域裡。如果 overflow 覆蓋了相鄰 free chunk 的 `_RTL_BALANCED_NODE`，下一次 VS 操作會用偽造的樹節點做 rotation/rebalance，可能帶來類似 glibc largebin unlink 的 write primitive——但 Windows 的紅黑樹操作有自己的 check，難度比 glibc 的 unlink 高。

## Segment Heap 的完整性檢查清單

> **未實測（需 WinDbg + symbols 驗證）**；以 Yason 論文描述的機制為準：

```
_SEGMENT_HEAP：
  - Signature = 0xDDEEDDEE（任何 heap 操作前驗）

_HEAP_PAGE_SEGMENT：
  - ListContext 指向 _HEAP_SEG_CONTEXT（指標驗）

_HEAP_PAGE_RANGE_DESCRIPTOR：
  - RangeFlags 合法值範圍

_HEAP_VS_CHUNK_HEADER：
  - UnsafeSize XOR key = UnsafeSize XOR RandomKey（random key 來自 VS context）
  - EncodedSegmentPageOffset XOR 驗

_HEAP_LFH_SUBSEGMENT：
  - Signature 欄位
  - BlockOffsets XOR 加密

_HEAP_LARGE_ALLOC_DATA：
  - VirtualAddress 4KB 對齊
  - UnusedBytes < VirtualAlloc 大小
```

每個元件的獨立 check 讓 Segment Heap 的 heap corruption 更難悄悄進行——在 NT Heap 時代，一個 overflow 覆蓋 header 可能讓 heap 繼續工作一段時間才崩，Segment Heap 的 check 更早期、更明確地 AV。

## 對比與取捨

| 維度 | NT Heap + LFH | Segment Heap |
|---|---|---|
| 設計年代 | 1990s（NT Heap），2006（LFH Vista） | 2015（Win 10） |
| 主要用途 | 一般 win32 exe（現仍大量存在） | 系統行程、UWP（Win 10+） |
| 分配路由 | LFH（small）/ FreeLists（other）/ VirtualAlloc（big） | LFH（small）/ VS（medium）/ VirtualAlloc（big） |
| Metadata 設計 | FreeLists 陣列（O(1) 查，固定大小） | 紅黑樹（O(log n) 查，任意大小） |
| Chunk header 大小 | 16 bytes（固定） | VS: 8 bytes；LFH: 無獨立 per-slot header |
| 完整性保護 | XOR cookie + Signature（兩層） | 多個 Signature + 多個 XOR key（per-subsystem） |
| CommitRoutine 利用 | 歷史技法（Win 7 前）；Win 7 後難直接用 | 無此欄位 |
| 研究資料豐富度 | 多（十幾年積累） | 較少（主要靠 Yason BH2016 + 後續研究者）|
| CTF 出現頻率（現況） | 仍是主流（win32 exe 靶） | 逐漸增多（Modern app 靶） |

## 踩雷集錦

1. **「Win 10 就是 Segment Heap，CH 14/15 的知識廢了」**：錯。普通 win32 exe（你用 gcc/MSVC 編的靶）在 Win 11 上大概率仍走 NT Heap + LFH。Segment Heap 主要接管系統行程和 UWP app。CTF 的 Windows pwn 題目多數仍是 NT Heap 靶，因為靶通常是普通 win32 exe。

2. **「VS allocator 的 chunk header 和 NT Heap 的 _HEAP_ENTRY 格式相同」**：錯。VS chunk header 是 `_HEAP_VS_CHUNK_HEADER`（8 bytes），用不同的 XOR key 加密，欄位語意不同。直接套 NT Heap 的 header 解析腳本在 VS chunk 上，數字全部錯。

3. **「Segment Heap 就是更安全的 NT Heap，所以 overflow 必然不可利用」**：錯。更多的 integrity check 讓利用更難，但不是不可能——Saar Amar 和 Connor McGarr 等研究者已有 Segment Heap 的 exploit 技法。關鍵是要搞清楚哪個元件的 check 可以繞過，以及哪個元件的元數據可以從 info leak 重建。

4. **「_SEGMENT_HEAP 的 Signature 在偏移 0x00」**：錯。`_SEGMENT_HEAP.Signature` 在 `+0x010`（Win 10 原始版）。不要和 NT Heap 的 `_HEAP.SegmentSignature`（也在 `+0x010`）搞混——它們的 offset 巧合相同，但結構不同，signature 值也不同（`0xEEFFEEFF` vs `0xDDEEDDEE`）。

5. **「Segment Heap 的 LFH 和 NT Heap 的 LFH 可以套同一套 grooming 技法」**：不完全對。整體概念相同（UserBlocks / 隨機化），但 subsegment header 格式不同，計算 BusyBitmap 位址的方式不同，偽造 subsegment header 的加密也不同。寫 grooming 程式前要先確認目標是哪種 LFH。

## 進階：再往深一層

### VS Allocator 的 Delayed Free

VS allocator 有一個 `DelayFreeList`（`_SLIST_HEADER`）：free 的 VS chunk 不是立刻插進紅黑樹，而是先放進 lock-free 的 `DelayFreeList`。等下一次 VS 操作時，才批量把 `DelayFreeList` 的 chunk 真正插進 FreeChunkTree。

這和 glibc 的 `tcache_put` 後還要在 free 路徑檢查 consolidate 有點像。延遲操作的目的是減少紅黑樹的寫入競爭（多線程場景）。

對 exploit 的影響：如果你 free 一個 VS chunk 然後立刻觀察 FreeChunkTree，chunk 可能還不在樹裡（在 DelayFreeList）——這讓「free 後讀樹節點指標」的 info leak 時機更難控制。

### Backend 的 Page Coalescing

Backend 在釋放 page range 時會嘗試合並相鄰的 free page range（比 NT Heap 更激進的 decommit），以減少記憶體使用。對 exploit 的影響：如果你靠 spray 在 heap 裡建立了精心設計的佈局，然後 free 大量 VS chunk，Backend 的 coalescing 可能合並掉你的佈局，破壞 grooming 假設。

### 行程啟動時的 Heap 類型決定

Windows 用 **HeapCompatibilityMode** 決定一個行程用哪種 heap。這個值來自多個地方：

1. 行程的 manifest（packaged app → Segment Heap）
2. `HKEY_LOCAL_MACHINE\System\CurrentControlSet\Control\Session Manager\HeapSegmentReserveSize` 等 registry key
3. 父行程的繼承

Debug 場合可以強制：

```
> **未實測（理論）**
> 設定環境變數 _NO_HEAP_ALLOC=1 可能影響 heap 類型選擇（版本相關）
> 或用 gflags 強制 Page Heap（不改變 NT Heap / Segment Heap 的選擇，只加 debug layer）
```

### Segment Heap 的 exploitation 研究現況

公開的 Segment Heap exploit 技法（截至論文撰寫時 2025-2026）：

- **VS chunk overflow → 偽造 _RTL_BALANCED_NODE → write primitive**：類似 glibc largebin attack，但需要繞過 XOR encoding
- **LFH subsegment spray → 控制 slot 佈局**：和 NT Heap LFH grooming 概念相同，但 subsegment header 格式不同
- **Large alloc tracking → AVL tree corruption**：超大型分配的 tracking 節點可能被 overflow 覆蓋

主要研究者：Saar Amar（Microsoft MSRC）、Connor McGarr、Mark Vincent Yason（IBM X-Force）。

## 動手練習

> 以下練習用於驗證「你的目標用哪種 heap」的判斷能力：

1. 建一個簡單的 C 程式（用 mingw 編），分配幾個 chunk，用 Python 讀 ProcessHeap 的 `+0x010` 偏移，看 signature 值；對比 NT Heap 的 `0xEEFFEEFF` 和 Segment Heap 的 `0xDDEEDDEE`

2. （需 WinDbg）對 `notepad.exe` 掛 WinDbg，用 `!heap -stat` 列出所有 heap 及其類型；嘗試找出哪些是 NT Heap、哪些是 Segment Heap

3. 查 Yason BH2016 論文的 Fig. 7（VS allocator subsegment layout），在本章的架構圖上對應每個元件；確認你能解釋「HeapAlloc(h, 0, 0x200) 在 Segment Heap 走哪條路徑」

4. 思考題：一個行程有 NT Heap（自己的私有 heap，用 HeapCreate 建的）和 Segment Heap（ProcessDefaultHeap）。你做 heap grooming 時，如果目標 DLL 用 ProcessDefaultHeap，而 PoC 用 HeapCreate 建的私有 heap 做 spray，這樣的 grooming 有意義嗎？為什麼？

## 本章重點整理

- Segment Heap 是 Win 10 引入的新架構，主要接管系統行程和 UWP app；普通 win32 exe 在 Win 10/11 仍多數走 NT Heap + LFH——**用哪種 heap 是行程級別的決定，不是全局的**
- 四個元件分工：**LFH**（≤ 1KB，快取）→ **VS**（1KB~60KB，紅黑樹管理）→ **Large**（>60KB，VirtualAlloc）；全部從 **Backend**（page-level segment 管理）取 pages
- VS chunk header 只有 **8 bytes**（比 NT Heap 的 16 bytes 小），但 XOR 加密方式不同
- Segment Heap **沒有 CommitRoutine**（NT Heap 的歷史利用目標被刪除），但有更多 per-subsystem 的 integrity check
- 分析目標前先判斷 heap 類型：Signature `0xDDEEDDEE` = Segment Heap，`0xEEFFEEFF` = NT Heap

## 自我檢核

- [ ] 不看筆記，能說出 Segment Heap 的四個元件名稱和各自的 size 範圍（大約）
- [ ] 能用一句話解釋 VS allocator 為什麼用紅黑樹而不是 FreeLists 陣列
- [ ] 知道 NT Heap 和 Segment Heap 的 signature 各是什麼（用於判斷 heap 類型）
- [ ] 面試被問「Win11 的 Segment Heap 上要怎麼做 heap grooming」，能說出和 NT Heap + LFH grooming 的核心差異（至少兩點）
- [ ] 知道為什麼「普通 CTF 的 Windows pwn 靶通常仍用 NT Heap」
- [ ] 能解釋「VS chunk header 的 EncodedSegmentPageOffset」的用途，以及 overflow 破壞它後會發生什麼

## 延伸閱讀

### 白皮書 / 會議論文

- **[Windows 10 Segment Heap Internals](https://www.blackhat.com/docs/us-16/materials/us-16-Yason-Windows-10-Segment-Heap-Internals.pdf)** — Mark Vincent Yason，Black Hat US 2016
  - **讀哪裡**：全文（72 頁）；本章是這篇的摘要導讀，原文有完整的結構 dump 和 WinDbg 輸出
  - **學什麼**：Segment Heap 的權威描述，本章所有結構名稱的一手來源；必讀
  - **前提知識**：Ch 14 + Ch 15（NT Heap + LFH）讀完後，和本章對照讀效果最好

- **[MSRC Blog — Heap Exploitation Mitigations in Windows 10 Creators Update](https://msrc.microsoft.com/blog/2017/06/heap-exploitation-mitigations-in-windows-10-creators-update/)** — Microsoft MSRC
  - **讀哪裡**：全文（部落格文章，約 15 分鐘閱讀）
  - **學什麼**：Microsoft 官方視角的 Segment Heap 安全強化說明；和 Yason 論文互補（一個是研究者反向分析，一個是官方說明）
  - **前提知識**：本章讀完

### 部落格

- **[Saar Amar — Windows Heap Internals and Exploitation](https://github.com/saaramar/Deterministic_LFH)** — Saar Amar（Microsoft MSRC）
  - **讀哪裡**：Deterministic LFH 技法的 repo 和配套文章（BH Asia 2020）
  - **學什麼**：從 exploit 研究者視角看如何克服 LFH 隨機化，在 Segment Heap 環境下的 grooming 技法；直接對接 Ch 28
  - **前提知識**：Ch 15（LFH）+ 本章

- **[Connor McGarr — "Heap Exploitation on Windows"](https://connormcgarr.github.io/heap-overflow-series-part-1/)** — Connor McGarr
  - **讀哪裡**：Heap overflow series（多篇），Segment Heap 相關部分
  - **學什麼**：Win 10/11 現代 heap 環境下的 exploitation 技法，VS allocator 的 overflow primitive
  - **前提知識**：Ch 14 + Ch 15 + 本章

### 書籍

- **《Windows Internals, 7th Edition》** — Yosifovich, Ionescu, Russinovich, Solomon
  - **讀哪裡**：Memory Management 章節的 Segment Heap 部分（第 7 版起有收錄，但不如 Yason BH2016 詳細）
  - **學什麼**：官方視角的 Segment Heap 架構定位；和 Yason 論文配合讀

heap metadata 的 XOR encoding 和完整性檢查是 NT Heap 和 Segment Heap 共有的安全防線，下一章把編碼機制和各種完整性驗證點全部拆開，搞清楚哪些可以繞過、哪些不行。

→ [Ch 17 — heap metadata encoding 與完整性檢查](./17-heap-metadata-encoding.md)
