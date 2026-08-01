# Ch 15 — LFH (Low Fragmentation Heap)

> **目標**：搞清楚 LFH 為什麼存在、它覆蓋在 NT Heap backend 上面的哪一層、觸發門檻是什麼、bucket/UserBlocks 怎麼管分配；能解釋 Win 8 的 allocation randomization 為什麼讓 LFH 利用比 NT Heap backend 難，又難在哪裡——這是 Part 4 heap grooming 的認識論前提。

## 為什麼需要這個？

NT Heap backend（Ch 14）的 FreeLists 架構能工作，但有一個根本問題：**高頻分配/釋放同一個 size 的 chunk 時，效能很差、碎片很嚴重**。

想像瀏覽器引擎每秒分配並釋放幾千個同樣大小的 DOM node（比如 64 bytes）：

1. 每次分配都要找 `FreeLists[idx]`，遍歷雙向鏈
2. 每次釋放都要做 coalescing（向前向後合併）
3. 高度碎片化：busy/free chunk 交替排列，相同 size 的 free chunk 分散在整個 segment

這正是 glibc 引入 tcache（2.26）的相同動機：**per-size 的快速分配快取**。Windows 的答案是 LFH（Low Fragmentation Heap）。

## 先建立直覺：LFH 是什麼

LFH 是一個 **front-end allocator**：它攔截特定 size class 的分配請求，用自己的 bucket 機制快速服務，**繞過** NT Heap backend 的 FreeLists 路徑。只有在 LFH 沒有可用 slot 時，才退回去找 backend 要一塊大的（UserBlocks）。

```
  HeapAlloc(heap, 0, size)
       │
       ▼
  ┌─────────────────────────────────────────────┐
  │  LFH front-end                              │
  │  └─ 這個 size 的 LFH bucket 有空 slot？     │
  │     YES → 直接從 UserBlocks 取 slot（快）   │
  │     NO  → 向 NT Heap backend 申請新 UserBlocks │
  └─────────────────────────────────────────────┘
       │ （若 LFH 未啟用，或 size 超範圍）
       ▼
  NT Heap backend（FreeLists 查找，Ch 14 的路徑）
```

**glibc tcache 對比**：

| 維度 | LFH | glibc tcache |
|---|---|---|
| 適用 size | 1 ~ 16368 bytes（LFH 範圍） | 0 ~ 1032 bytes（tcache_bins = 64 個） |
| 啟用方式 | **自動觸發**（同 size 分配 18 次後） | **始終啟用**（glibc 2.26+，編譯進去） |
| 隨機化 | **Win 8+ 有 allocation randomization** | **無**（純 LIFO 順序） |
| 每 size 上限 | 視 UserBlocks 大小，可多個 UserBlocks | 每 bin 最多 7 個（預設，可調） |
| per-thread | **不是**（per-heap bucket） | **是**（per-thread tcache） |
| free 後合並 | **不合並**（slot 留在 UserBlocks） | **不合並**（留在 tcache bin） |
| 指標防護 | Win 8+ 有 safe linking | glibc 2.32+ 有 PROTECT_PTR（XOR mangling） |

最大的差異：**LFH 有 allocation randomization，tcache 沒有**。這讓 LFH 的 grooming 比 tcache 難——你無法預測下一個分配從 UserBlocks 的哪個 slot 取出。

## LFH 觸發門檻（Activation Threshold）

LFH 不是 heap 建立就啟用，而是**按需、per-size-class 觸發**：

**規則**：同一個 heap，對同一個 size class（bucket）進行 **約 18 次分配**後，Windows Heap Manager 會為這個 bucket 啟用 LFH。

> 精確值是 `0x11 = 17` 次（有資料說 18 次，文獻有出入；以你環境實測為準，閾值在此區間）。核心邏輯在 `ntdll!RtlpLowFragHeapAllocFromContext` — 有一個 per-bucket 的 allocation counter，到達閾值後呼叫 `RtlpLowFragHeapEnableHeapCache`。

觸發後：
1. 這個 size class 的後續分配全部走 LFH 路徑
2. LFH 向 NT Heap backend 申請一塊大型 chunk（叫做 UserBlocks），切成若干固定大小的 slot
3. 之後同 size 的 `HeapAlloc` 從 UserBlocks 的 slot 中取；`HeapFree` 把 slot 放回

**在 exploit 裡，這個門檻很重要**：你做 heap grooming（feng shui）時，往往先要刻意觸發 LFH，確保目標物件走 LFH 路徑，才能用 LFH 的 slot layout 做精確的相鄰佈置。Ch 28 的整個技法建立在「主動觸發 LFH 並控制 UserBlocks slot」上。

## 底層機制：allocation counter 和 LFH 啟用路徑

LFH 啟用不是一個 global flag，而是 **per-bucket 的 counter** 達到門檻後呼叫一個內部函數。機制在 ntdll 裡：

```
  RtlAllocateHeap(heap, flags, size)
       ↓
  計算 bucket index (idx)
       ↓
  Heap->FrontEndHeap 是否存在？（LFH 全局結構有沒有）
    YES → 取 _HEAP_BUCKET[idx].UseAffinity
          UseAffinity.Depth++ （per-bucket counter）
          if (Depth >= 0x11 = 17):
              RtlpLowFragHeapEnableHeapCache(heap, idx)
              ← 這一步才真正建 UserBlocks，啟用 LFH for this bucket
    NO → 走 backend FreeLists
```

> 確切 counter 值（17 vs 18）取決於 Windows 版本，文獻有出入；以你環境 WinDbg 跟蹤 `ntdll!RtlpLowFragHeapEnableHeapCache` 的呼叫點為準。原則：同 size 約 17-18 次分配後觸發。

**counter 的特性**：
- counter 只計 LFH 範圍內的 size（1-16368 bytes）的分配
- counter 不會因 free 而減少——它是 allocation 計數，不是 live object 計數
- 一旦 LFH 啟用某個 bucket，那個 bucket 就永遠走 LFH（不會關掉回去）

對 exploit 的意義：如果你在 grooming 前期用其他 size 做大量分配（不同 bucket），不會影響目標 bucket 的 counter。但如果你用目標 size 做前期測試，可能意外觸發 LFH，讓後面的 grooming 行為和預期不同。這是一個容易踩到的坑。

## Python 實測：觀察 LFH 觸發

以下可在本機直接跑（真實輸出）：

```python
import ctypes

k = ctypes.windll.kernel32
k.HeapCreate.restype = ctypes.c_void_p
k.HeapAlloc.restype  = ctypes.c_void_p
k.HeapAlloc.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_size_t]

h = k.HeapCreate(0, 0, 0)
HEAP_ZERO_MEMORY = 8

# Phase 1：前 18 次分配（NT Heap backend，地址應連續遞增）
pre_lfh = []
for i in range(18):
    p = k.HeapAlloc(h, HEAP_ZERO_MEMORY, 0x40)
    pre_lfh.append(p)

# Phase 2：後續分配（LFH 已啟用，地址應隨機化）
post_lfh = []
for i in range(10):
    p = k.HeapAlloc(h, HEAP_ZERO_MEMORY, 0x40)
    post_lfh.append(p)

print("NT Heap backend (pre-LFH), first 5:")
for i in range(5):
    diff = hex(pre_lfh[i]-pre_lfh[i-1]) if i > 0 else "---"
    print(f"  [{i:2d}] 0x{pre_lfh[i]:016X}  diff={diff}")

print()
print("LFH active (post-LFH), 10 allocs:")
for i in range(10):
    diff = hex(post_lfh[i]-post_lfh[i-1]) if i > 0 else "---"
    print(f"  [{i:2d}] 0x{post_lfh[i]:016X}  diff={diff}")
```

**本機實測輸出**（Win11 x64）：

```
NT Heap backend (pre-LFH), first 5:
  [ 0] 0x000001F2995F0860  diff=---
  [ 1] 0x000001F2995F08B0  diff=0x50
  [ 2] 0x000001F2995F0900  diff=0x50
  [ 3] 0x000001F2995F0950  diff=0x50
  [ 4] 0x000001F2995F09A0  diff=0x50

LFH active (post-LFH), 10 allocs:
  [ 0] 0x000001F2995F32C0  diff=---
  [ 1] 0x000001F2995F3900  diff=0x640
  [ 2] 0x000001F2995F3860  diff=-0xa0
  [ 3] 0x000001F2995F3310  diff=-0x550
  [ 4] 0x000001F2995F3400  diff=0xf0
  [ 5] 0x000001F2995F37C0  diff=0x3c0
  [ 6] 0x000001F2995F35E0  diff=-0x1e0
  [ 7] 0x000001F2995F3540  diff=-0xa0
  [ 8] 0x000001F2995F38B0  diff=0x370
  [ 9] 0x000001F2995F3810  diff=-0xa0
```

**解讀**：

- **Pre-LFH**：每個 diff 都是 `0x50`（0x40 request + 0x10 header = 0x50 chunk 大小），完全線性遞增——這是 NT Heap backend 從 segment 線性推進的特徵
- **Post-LFH**：diff 有正有負，範圍大（-0x550 到 +0x640），明顯是在 UserBlocks 裡隨機取 slot——**allocation randomization 在起作用**

這一段輸出是本章最重要的實驗：用兩個列清楚地顯示了 backend vs LFH 行為的根本差異。

## LFH 架構：Bucket、SubSegment、UserBlocks

### Bucket（大小分類）

LFH 把所有 size 分成 **128 個 bucket**（和 NT Heap backend 的 FreeLists 對應，但不完全相同）：

```
Bucket 1:   1–8 bytes
Bucket 2:   9–16 bytes
Bucket 3:   17–24 bytes
...
Bucket 128: 15921–16368 bytes（最大 LFH 管理範圍）
```

> 超過 16368 bytes 的分配不走 LFH，回 NT Heap backend 處理（FreeLists[0] 的大型 chunk）。LFH 的上限比 NT Heap backend 大，因為 LFH 本身也從 backend 申請 UserBlocks，所以對 backend 來說那只是一次普通的大型分配。

每個 bucket 有一個 `_HEAP_BUCKET` 結構，包含：
- bucket 的 size class
- allocation counter（計到 18 次觸發 LFH）
- 指向 `_HEAP_LOCAL_DATA` 的指標（per-processor 的本地資料，減少鎖競爭）

### _HEAP_LOCAL_DATA 與 SubSegment

LFH 是 **per-processor**（非 per-thread）的：每個邏輯 CPU 有自己的 `_HEAP_LOCAL_DATA`，減少多核心競爭。

```
  _HEAP (heap 控制中心)
   └─ LFH 結構 (_HEAP_LFH_CONTEXT 或等價結構)
       └─ LocalData[0..n-1]  ← n = 邏輯 CPU 數量
           └─ SubSegmentZones[bucket]
               └─ _HEAP_SUBSEGMENT (UserBlocks 的管理者)
                   └─ UserBlocks (實際存 slot 的大塊記憶體)
```

### _HEAP_SUBSEGMENT 與 UserBlocks

這是 LFH 最核心的兩個結構：

```
  _HEAP_SUBSEGMENT：
    +0x000 LocalInfo        : Ptr64 _HEAP_LOCAL_SEGMENT_INFO
    +0x008 UserBlocks       : Ptr64 _HEAP_USERDATA_HEADER ← 指向 UserBlocks
    +0x010 AggregateExchg   : _INTERLOCK_SEQ              ← 原子操作用的計數/指標
    +0x018 BlockSize        : USHORT                      ← 每個 slot 的大小
    +0x01a Flags            : USHORT
    +0x01c BlockCount       : USHORT                      ← UserBlocks 裡有幾個 slot
    +0x01e SizeIndex        : UCHAR                       ← 對應的 bucket index
    ...
```

```
  _HEAP_USERDATA_HEADER（UserBlocks 起始）：
    +0x000 SubSegment       : Ptr64 _HEAP_SUBSEGMENT ← 指回 SubSegment
    +0x008 Reserved         : Ptr64
    +0x010 SizeIndexAndPadding: ULONG
    +0x014 Signature        : ULONG  (= 0xF0E0D0C0，用於完整性驗證)
    +0x018 EncodedOffsets   : _HEAP_USERDATA_OFFSETS ← 加密的 slot 位址資訊
    +0x020 BusyBitmap       : _RTL_BITMAP            ← 每個 bit 對應一個 slot（1=busy）
    ...
    後面緊跟著 slot 區域（每個 slot = BlockSize bytes）
```

視覺圖：

```
  UserBlocks（從 NT Heap backend 申請的一大塊）：
  ┌────────────────────────────────────────────────────────────────┐
  │  _HEAP_USERDATA_HEADER（約 0x20 bytes）                        │
  │  Signature=0xF0E0D0C0  BusyBitmap=1111011101...               │
  ├────────────────────────────────────────────────────────────────┤
  │  Slot 0  │  Slot 1  │  Slot 2  │  Slot 3  │  Slot 4  │ ...   │
  │  busy    │  free    │  busy    │  busy    │  free    │       │
  │ (0x40 B) │ (0x40 B) │ (0x40 B) │ (0x40 B) │ (0x40 B) │       │
  └────────────────────────────────────────────────────────────────┘
  BusyBitmap:  1         0         1         1         0 ...
  （1=busy，0=free；哪個 bit 是 0 就是可用 slot）
```

**glibc tcache 對比**：glibc tcache 用的是 per-thread 的單向鏈（`tcache_entry` 有 `next` 指標和 `key`），每次 free 把 chunk 頭插進鏈，alloc 從鏈頭取——完全 LIFO，沒有 bitmap。LFH 用 bitmap 管 slot 的 busy/free 狀態，不把空閒 slot 鏈起來（所以沒有 next 指標可以偽造）。

## Win 8 的 Allocation Randomization（利用影響最深的改變）

Win 8 以前的 LFH：分配 slot 是**順序的**（類似 tcache 的 LIFO）。攻擊者可以精確預測下一個分配落在 UserBlocks 的哪個 slot，feng shui 極其精準。

Win 8 起的 LFH：從 UserBlocks 取 slot 時，使用**隨機起點**（基於 PRNG，seed 來自 heap 初始化時的隨機值）。

```
  Win 7 LFH slot 分配（順序）：
  [slot0 busy][slot1 busy][slot2 busy][slot3 free ← 下一個]...
  → 攻擊者可以 100% 預測「下一個分配就是 slot3」

  Win 8+ LFH slot 分配（隨機化）：
  [slot0 busy][slot2 free][slot5 free][slot8 free]...
  → 隨機掃描 bitmap，找第一個 free slot
  → 攻擊者「可能」取到 slot2、slot5、或 slot8，無法完全確定
```

**對 exploit 的影響**：

1. **feng shui 精準度下降**：你做了一堆 spray/free 把 UserBlocks 填滿特定佈局，但 victim alloc 落在哪個 slot 有不確定性
2. **不是完全不可利用**：研究者（Saar Amar 等）發展了「概率性 grooming」技法——把 UserBlocks 填滿到只剩一個 free slot，就把不確定性消去（就算 PRNG 隨機跳，也只有一個 slot 是 free 的）
3. **需要更多 alloc 操作**：精準 grooming 需要精確控制 UserBlocks 的 busy/free 比例，這是 Ch 28 的核心技術

**glibc tcache 無隨機化**：tcache 的 alloc 是純 LIFO，沒有任何隨機化。這讓 tcache 利用（House of Spirit, tcache dup）的 slot 精準度比 LFH 高——這是 glibc 堆利用在精準度上佔優的原因之一。

## LFH 的 _HEAP_ENTRY 與 Chunk Header 差異

LFH 管理的 slot **也有** `_HEAP_ENTRY` header（16 bytes），但格式略有不同：

```
  LFH 的 chunk header（在 user_ptr - 0x10）：
  +0x00  Size         : USHORT  ← 對 LFH slot，這欄位的語意改變
                                   （不再直接是 chunk 大小；是 SubSegment offset 的編碼）
  +0x02  Flags        : UCHAR   ← 0x01 = busy（和 backend 相同）
  +0x03  SmallTagIndex: UCHAR
  +0x04  PreviousSize : USHORT  ← 在 LFH slot 語意不同
  +0x06  SegmentOffset: UCHAR
  +0x07  ExtendedBlockSignature: UCHAR ← LFH slot 這裡是 0x80（區分 LFH vs backend）
```

**關鍵識別符**：`ExtendedBlockSignature = 0x80` 代表「這個 chunk 是 LFH slot，不是 NT Heap backend chunk」。Heap manager 用這個 bit 決定 free 時走哪條路徑。

Overflow 到 LFH slot 的 header 時，覆蓋到 `ExtendedBlockSignature` 欄位是要注意的——如果把 `0x80` 清成 `0x00`，free 這個 chunk 時 Heap Manager 可能走 backend 路徑，行為和預期不同，是 exploit crash 的常見原因。

## LFH 的 Free 行為

`HeapFree` 一個 LFH slot：

```
1. 識別：ExtendedBlockSignature = 0x80 → 走 LFH 路徑
2. 定位 UserBlocks：從 chunk header 找到所屬 SubSegment 的 UserBlocks
3. 更新 BusyBitmap：對應 bit 清 0（slot 變 free）
4. 不做 coalescing（LFH slot 大小固定，不需要合並鄰近 slot）
5. SubSegment 的 free slot 計數更新
```

**不做 coalescing** 是 LFH 的關鍵特性——LFH slot 大小固定，不需要也不應該合並（合並就破壞了 UserBlocks 的固定 slot layout）。這也意味著：
- LFH 的 slot 碎片是「結構性」的（大量分散的小 free slot），而非 NT Heap backend 那種可合並的連續 free block
- 對利用者來說：free 一個 slot 後，bitmap 上那個 bit 是 0，**下次 alloc 可能（機率性）取回同一個 slot** ——UAF 的基礎

## LFH 的 Bucket 到 Size 的對應關係

LFH 的 128 個 bucket 不是等距的。bucket index 到 size class 的對應大致如下：

```
  Bucket  1–32:  每 bucket 管 8 bytes（granularity = 8）
                 bucket 1 = 1–8 bytes
                 bucket 2 = 9–16 bytes
                 ...
                 bucket 32 = 249–256 bytes

  Bucket 33–48: 每 bucket 管 16 bytes（granularity = 16）
                 bucket 33 = 257–272 bytes
                 ...
                 bucket 48 = 497–512 bytes

  Bucket 49–64: 每 bucket 管 32 bytes（granularity = 32）
                 ...

  (每段 granularity 加倍，直到 bucket 128 = ~16368 bytes)
```

這種「可變 granularity」的 bucket 設計，讓小型分配的 bucket 更密集（少浪費），大型分配的 bucket 更稀疏（夠覆蓋），和 jemalloc 的 size class 設計哲學相似。

**對 exploit 的影響**：你分配 `0x40` 和 `0x48` bytes 的 chunk，未必在同一個 LFH bucket——bucket 1-32 的 granularity 是 8，所以 `0x40`（64）在 bucket 8，`0x48`（72）在 bucket 9，它們分別是不同的 UserBlocks。做 grooming 時，要精確知道目標 object 的 size 落在哪個 bucket，才能用同 bucket 的分配填充 UserBlocks 旁邊的 slot。

## LFH 與 backend 的互動：誰管誰

一個常見的誤解是 LFH「獨立於」NT Heap backend。實際上的關係是：

```
  LFH 需要新 UserBlocks？
       ↓
  向 NT Heap backend 申請一大塊 chunk
  （用 HeapAlloc 的內部路徑，分配 ~64KB 的 backend chunk）
       ↓
  這個 backend chunk 就是 UserBlocks
  LFH 在上面切 slot，用 BusyBitmap 管

  HeapFree 一個 LFH slot：
       ↓
  更新 BusyBitmap（這個 slot 的 bit 清 0）
  NOT 還給 backend
       ↓
  如果整個 UserBlocks 都 free（bitmap 全 0）：
       ↓
  整個 UserBlocks 才還給 backend（HeapFree 給 backend 那個大 chunk）
```

這個「整個 UserBlocks 才還」的設計是 LFH 對碎片問題的解法：用空間換時間，把同 size 的 slot 集中在同一個 UserBlocks 裡，只要還有任何一個 busy slot，整塊記憶體都不會被回收。

**對 UAF 的意義**：一個 LFH slot 被 free 後，它所在的 UserBlocks 仍然 committed、仍然有效記憶體——你的 dangling pointer 指向的位址不會因為 free 一個 slot 而變成 unmapped memory（除非整個 UserBlocks 都還給 backend 且 backend 也 decommit）。這讓 LFH UAF 的利用窗口比 mmap-based 分配（glibc 大型 chunk 被 munmap 後指標立刻失效）穩定得多。

## LFH 與 glibc tcache 的完整對比

| 維度 | LFH（Win 8+） | glibc tcache（2.26+） |
|---|---|---|
| 觸發方式 | 自動（同 size 18 次分配後） | 始終啟用（global） |
| per-thread vs per-CPU | per-processor（_HEAP_LOCAL_DATA） | per-thread（tcache_perthread_struct） |
| 最大管理 size | ~16368 bytes（128 buckets） | 1032 bytes（64 bins） |
| slot 結構 | bitmap 管 busy/free | 單向 next 指標鏈 |
| 分配順序 | 隨機化（PRNG 選 free slot） | LIFO（從鏈頭取） |
| 釋放後 coalescing | 不做 | 不做（留在 tcache） |
| 指標保護 | 無顯式 next 指標（bitmap 模式） | glibc 2.32+ PROTECT_PTR（XOR） |
| UAF 利用難度 | 較高（隨機化 + bitmap，無明顯指標） | 較低（修改 next 指標即可控分配） |
| 利用技法主線 | 概率性 grooming + bitmap 操控 | tcache dup / House of Spirit |

## LFH 的多個 UserBlocks：SubSegment 的生命週期

一個 LFH bucket 不只有一個 UserBlocks。隨著分配增加，LFH 可能申請多個 UserBlocks，全部串在 `_HEAP_LOCAL_SEGMENT_INFO` 的鏈表上：

```
  _HEAP_LOCAL_SEGMENT_INFO（每個 bucket 一個）：
    +0x000 Hint       : Ptr64 _HEAP_SUBSEGMENT ← 當前優先使用的 subsegment
    +0x008 ActiveSubsegment : Ptr64 _HEAP_SUBSEGMENT ← 正在分配的 subsegment
    +0x010 CachedItems: Ptr64 _HEAP_SUBSEGMENT[16] ← 快取的（填滿的）subsegments
    +0x090 SListHeader : _SLIST_HEADER ← 所有 subsegment 的 lock-free 鏈

  SubSegment 的狀態機：
  新建 → ActiveSubsegment（正在分配 slot）
       → 填滿時進 CachedItems（等待有 slot 被 free）
       → CachedItems 某個 slot 被 free → 重新變 ActiveSubsegment
       → 所有 slot 都 free → 還給 backend（dealloc UserBlocks）
```

**對 grooming 的影響**：如果一個 bucket 同時有多個 UserBlocks，你做 spray 時分配的 object 可能散落在不同的 UserBlocks 裡。要做精準 grooming，你需要先確認「目標 object 和攻擊 object 在同一個 UserBlocks」——判斷方法是看地址是否落在同一個 ~64KB 區間。

這也是為什麼 LFH exploit 通常先做一輪「大量 alloc/free 把舊 UserBlocks 清掉，再 alloc 足夠多次確保在同一個新 UserBlocks 裡」的準備步驟。

## LFH 的 HeapAlloc HEAP_NO_SERIALIZE 旗標影響

`HeapCreate(HEAP_NO_SERIALIZE, ...)` 建的 heap，LFH 的行為略有不同：

- 沒有 `_HEAP_LOCAL_DATA` 的 per-processor 機制（無需 CPU affinity 考量）
- `_HEAP_SUBSEGMENT` 的 `AggregateExchg` 不走原子操作路徑
- 分配速度更快（省掉鎖和 interlocked 操作）

在 exploit 開發的測試環境裡，用 `HEAP_NO_SERIALIZE` 可以讓 LFH 行為更穩定（多線程競爭造成的非確定性消失），方便理解純機制。但正式環境的靶幾乎都是 serialize heap，記得最終要在 serialize 版驗。

## LFH 在 WinDbg 裡的觀察（未實測）

以下是裝好 WinDbg + public symbols 後，觀察 LFH 狀態的標準指令：

```
> **未實測，理論預期（需 WinDbg + _NT_SYMBOL_PATH 設好）**

!heap -stat -h <heap_handle>
# 輸出：各 bucket 的分配次數、free 次數、LFH 是否啟用

!heap -flt s <size>
# 輸出：在所有 heap 裡找這個 size 的 busy/free chunk

!heap -x <user_ptr>
# 輸出：某個分配的詳細資訊（所在 heap、bucket、busy/free、大小）

# LFH subsegment 的詳細結構（知道 subsegment 位址後）：
dt ntdll!_HEAP_SUBSEGMENT <subsegment_addr>
dt ntdll!_HEAP_USERDATA_HEADER <userblocks_addr>

# 期望輸出片段（LFH bucket 8 的 UserBlocks，slot size 0x50）：
# _HEAP_USERDATA_HEADER
#   +0x000 SubSegment    : 0x000001f2`995f1234 _HEAP_SUBSEGMENT
#   +0x014 Signature     : 0xf0e0d0c0
#   +0x020 BusyBitmap    : _RTL_BITMAP
#     ...
```

在 exploit 開發時，`!heap -x <ptr>` 是你確認「這個 ptr 到底在哪個 bucket、是不是 LFH」最快的方法——比手動計算 bucket index 可靠。

## 踩雷集錦

1. **「LFH 啟用了整個 heap 就都走 LFH」**：錯。LFH 是 **per-size-class** 觸發的。同一個 heap 裡，0x40 bytes 的分配可能走 LFH，0x80 bytes 的分配可能還在 NT Heap backend（沒到門檻）。混在一起分配不同 size 時，要逐 bucket 確認 LFH 狀態。

2. **「LFH 隨機化讓 heap 利用不可能」**：錯。Win 8+ 的隨機化只是讓**不填滿 UserBlocks 的情況下**分配落點不確定。Saar Amar 等人的技法：把 UserBlocks 填滿到只剩一個 free slot，下一次分配必定取那個 slot——確定性回來了。

3. **「LFH slot 的 chunk header 和 backend 完全相同」**：不完全對。LFH slot 的 `ExtendedBlockSignature = 0x80`（backend 是 0），`Size`/`PreviousSize` 欄位的語意也不同。寫 overflow exploit 時如果覆蓋了 `ExtendedBlockSignature`，free 路徑可能走錯，導致 crash 或 heap corruption 而不是乾淨的利用。

4. **「free LFH slot 後，slot 的記憶體立刻被 OS 回收」**：錯。LFH slot 屬於 UserBlocks，UserBlocks 本身是從 backend 分配的一整塊。free 一個 slot 只是把 BusyBitmap 的 bit 清 0，記憶體仍然 committed 在行程的位址空間裡——這就是 UAF 的前提。

5. **「tcache 有 PROTECT_PTR，LFH 更不安全」**：比較維度錯。LFH 沒有鏈式 next 指標，bitmap 模式本身就沒有「偽造 next 取 arbitrary chunk」的路徑，和 tcache dup 是完全不同的攻擊面。LFH 的利用通常是 slot 相鄰性（兩個相鄰 slot 的 overflow）或 spray（用大量分配控制 UserBlocks 佈局）。

## 對比與取捨：LFH vs NT Heap Backend vs glibc tcache

| 面向 | NT Heap backend | LFH | glibc tcache |
|---|---|---|---|
| 適用 size | 任何（到 512KB） | 1–16368 bytes（自動選擇） | 0–1032 bytes（固定開啟） |
| 分配速度 | O(1) 到 O(n)（線性搜尋 FreeLists） | O(1)（bitmap 找 free slot） | O(1)（取鏈頭） |
| 碎片控制 | 靠 coalescing（事後合並） | slot 固定大小（結構性防碎片） | slot 固定大小 + 有上限（max 7） |
| 記憶體用量 | 緊湊（無浪費 slot） | 可能 overcommit（UserBlocks 有空 slot） | 緊湊（只 cache 已 free 的） |
| 並發性 | heap lock（serialize） | per-CPU local data（減少競爭） | per-thread（無共享） |
| 利用歷史 | unlink / CommitRoutine overwrite | grooming / UAF spray | tcache dup / House of Spirit |
| 現代防護 | XOR cookie + safe unlink | bitmap（無鏈式指標）+ allocation PRNG | PROTECT_PTR（2.32+）+ key |

這張表格濃縮了為什麼學 LFH 對 exploit 很重要：**它的安全模型（bitmap，無 next 指標，隨機化）和 tcache（next 指標，LIFO）是完全不同的體系**，不能套用同一套技法。

## 進階：再往深一層

### UserBlocks 的大小選擇

一個 UserBlocks 到底幾個 slot？Heap Manager 會根據 bucket 的 BlockSize 和一個預設策略決定，大致是：

```
UserBlocks 的目標大小 ≈ 64KB（0x10000）
slot 數量 = 64KB / BlockSize（向下取整）
```

對 0x40 bytes 的 slot：`0x10000 / 0x50 ≈ 512 個 slot`（含 header 的實際 slot 大小是 0x50，所以 `0x10000 / 0x50 = 327.68 ≈ 327`）。

> 未實測（精確計算需看 WinDbg `!heap -x <addr>`）。以你環境實測為準。

這個數字對 grooming 很重要：如果 UserBlocks 有 327 個 slot，你需要佔用 326 個才能讓最後一個 slot 的分配有確定性。這是 LFH exploit 需要大量 spray 的根本原因。

### _HEAP_USERDATA_OFFSETS 的加密

`_HEAP_USERDATA_HEADER.EncodedOffsets` 欄位存的是加密後的 SubSegment 指標和 offset 資訊。這個加密和 Ch 17 的 header cookie 是不同的機制，但動機相同：防止直接覆蓋指標後不被偵測。

在 exploit 裡，如果你 overflow 到 UserBlocks header，覆蓋了 `EncodedOffsets`，接下來任何 LFH 操作都可能因解密失敗而 AV——在精心構造的 exploit 裡，要麼繞開 header，要麼計算出正確的加密值（需要知道加密 key，通常是 heap 基址相關的隨機值）。

### LFH 和 Segment Heap 的關係

Segment Heap（Ch 16）也有自己的 LFH 元件，但實作和 NT Heap 的 LFH 不完全相同。主要差異：Segment Heap 的 LFH 用不同的 subsegment 管理結構，且和 Variable Size（VS）allocator 的互動不同。在 exploit 分析時，先確認目標行程用的是哪種堆，再套對應的 LFH 知識。

### LFH 的安全歷史

Win Vista：LFH 引入，**無隨機化**，slot 順序分配
Win 8：加入 **allocation randomization**（PRNG 選 free slot），是 NT Heap 安全演進的重要里程碑
Win 10（Segment Heap）：LFH 元件進一步整合進 Segment Heap，並加上更多完整性檢查（Ch 16/Ch 17）

## 動手練習

用 Python ctypes 完成以下（可在本機直接跑）：

1. 建一個新 heap，對 size `0x40` 做 25 次分配，記錄每個 user ptr
2. 用地址差觀察：前幾次是線性的（backend），後幾次是隨機的（LFH）——找出精確的轉折點在第幾次分配（理論上約第 18 次）
3. 建另一個新 heap，對 **兩個不同的 size**（`0x40` 和 `0x80`）交替分配，各做 30 次，觀察：
   - `0x40` 的分配是否觸發了 LFH？
   - `0x80` 的分配是否觸發了 LFH？
   - 兩個 size 的 LFH 是獨立的（各自有各自的 bucket）嗎？
4. 觀察 LFH 啟用後的地址範圍：後 10 次 LFH 分配的最小和最大地址相差多少？試估算 UserBlocks 大約多大

## 本章重點整理

- LFH 是 **front-end allocator**，蓋在 NT Heap backend 之上，攔截特定 size class 的分配請求；NT Heap backend 是 LFH 的「倉庫」（LFH 向它申請 UserBlocks）
- **觸發門檻**：同 size 約 18 次分配後，那個 bucket 的 LFH 自動啟用；是 per-size-class 的，不是 per-heap
- **UserBlocks**：LFH 從 backend 申請的大型 chunk，切成固定大小的 slot；BusyBitmap 管理每個 slot 的 busy/free 狀態
- **Win 8+ allocation randomization**：LFH 取 free slot 的順序不再線性，基於 PRNG；使 feng shui 難度大增，但「只留一個 free slot」的技法可恢復確定性
- **LFH 不做 coalescing**：slot 大小固定，free 後只更新 bitmap，不合並——這是 UAF 和 slot-相鄰 overflow 的前提

## 自我檢核

- [ ] 不看筆記，能說出 LFH 觸發的門檻（大約幾次分配），以及這個門檻是 per-heap 還是 per-size-class 的
- [ ] 能解釋 UserBlocks 的 BusyBitmap 是什麼，以及「只留一個 free slot」如何克服 Win 8 的隨機化
- [ ] 面試被問「LFH slot 和 NT Heap backend chunk 的 header 有什麼差異」，能說出 `ExtendedBlockSignature = 0x80` 這個識別點
- [ ] 能說出 LFH 和 glibc tcache 最重要的兩個差異（一個關於觸發方式，一個關於分配順序）
- [ ] 知道為什麼 LFH 的 exploit 通常需要大量的 spray 操作，而 tcache 通常不需要

## 延伸閱讀

### 白皮書 / 會議論文

- **[Windows 10 Segment Heap Internals](https://www.blackhat.com/docs/us-16/materials/us-16-Yason-Windows-10-Segment-Heap-Internals.pdf)** — Mark Vincent Yason，Black Hat US 2016
  - **讀哪裡**：第三章 "Low Fragmentation Heap"（Yason 對 LFH 的完整描述，包含 UserBlocks layout 和 allocation randomization 的技術細節）
  - **學什麼**：LFH 結構的權威描述；本章所有結構名稱和 layout 的最終依據
  - **前提知識**：本章讀完

- **[Exploiting the Windows Heap (Defcon 26)](https://github.com/nicowillis/Attacking-the-Windows-Heap)** — Nicolas Willis
  - **讀哪裡**：LFH grooming 技法，以及「填滿 UserBlocks」的概率性方法
  - **學什麼**：把 LFH 的學術描述轉成實際 exploit 開發的步驟
  - **前提知識**：本章 + Ch 14 + Ch 17

### 部落格

- **[Saar Amar — "Heap Overflows Using the Windows LFH"](https://saarlab.com/wp-content/uploads/2021/02/LFH_Heap_Exploitation_Techniques.pdf)**（或等效資源）
  - **讀哪裡**：LFH UserBlocks 精準佈置技法，Win 10 環境下的演示
  - **學什麼**：研究者視角的 LFH grooming 實戰；本課 Ch 28 的主要技術參考
  - **前提知識**：本章全部 + Ch 17（header encoding 要懂）

- **[Connor McGarr — "Heap Exploitation Primitives"](https://connormcgarr.github.io/)** — Connor McGarr
  - **讀哪裡**：heap 利用原語系列，LFH 相關部分
  - **學什麼**：Windows heap 利用的現代技法（Win 10/11），包含 LFH 在 Segment Heap 環境下的行為
  - **前提知識**：Ch 14 + 本章 + Ch 16

- **[j00ru — Windows security research blog](https://j00ru.vexillium.org/)** — Mateusz Jurczyk
  - **讀哪裡**：搜索 heap 相關文章；j00ru 的 heap corruption 研究是 Windows 安全研究圈的標準
  - **前提知識**：本章讀完，有一定 exploit 背景

Segment Heap 是 Win 10 引入的新架構，在現代系統行程中取代了 NT Heap + LFH 的組合——它有四個元件（Backend、VS、LFH、Large），更清晰的安全邊界，以及更複雜的利用難度。

→ [Ch 16 — Segment Heap（Win10+ 現代預設堆）](./16-segment-heap.md)
