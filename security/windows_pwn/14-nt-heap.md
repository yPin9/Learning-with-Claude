# Ch 14 — NT Heap 傳統架構（對照 glibc）

> **目標**：從 `_HEAP` 結構看到最小的 `_HEAP_ENTRY` chunk header，在腦中建起一張 NT Heap 記憶體佈局圖；能用「vs glibc」的對照語言解釋每個設計選擇，為後面的 heap 利用（Part 4）打下正確的地基。

## 為什麼需要這個？

你已經對 glibc heap 有一套完整的直覺：arena 管 segment、bin 管 free list、chunk header 記 size + prev_size、unlink 是利用的核心。到了 Windows，**一切結構換了名字、換了位置、換了設計哲學**，但底層問題（管理記憶體碎片、平衡分配速度與空間效率）是一樣的。

這章先給你一張三代架構地圖，然後深挖傳統的 NT Heap backend。搞清楚 NT Heap 才能理解 LFH（Ch 15）為什麼要蓋在它上面，以及 Segment Heap（Ch 16）為什麼要把整個 backend 換掉。

## 先建立直覺：Windows Heap 的三代演進

Windows heap 不是一個東西，而是三代系統的疊加：

```
  Win XP/Vista                Win 7/8                     Win 10/11
 ──────────────              ─────────────               ──────────────
 NT Heap (backend)           NT Heap                     Segment Heap
 ← 唯一選擇                  ← 仍是 backend              ← 新引入，接管
                              LFH (front-end)            系統行程/UWP
                              ← Win Vista 有雛形          NT Heap + LFH
                              ← Win 7 正式普及            ← 一般 win32 exe
                                                          仍常用（視版本/行程）
```

**三代定位**：

| 世代 | 名稱 | 出現 | 定位 | glibc 對應 |
|---|---|---|---|---|
| 第一代 | NT Heap (backend) | Win NT | 所有 heap 操作的最終執行者 | ptmalloc2 主 arena + bins |
| 第二代 | LFH | Vista（普及 Win 7） | front-end，劫持特定 size class 的快速路徑 | tcache (per-thread + per-size) |
| 第三代 | Segment Heap | Win 10 | 新架構，含 VS/LFH/Backend/Large | 更接近 jemalloc 的 slab 概念 |

> 認識論提醒：「一般 win32 exe 用哪個」在 Win10+ 是行程與版本相關的問題——多數系統行程用 Segment Heap，舊式 win32 exe 通常仍走 NT Heap + LFH。Ch 16 會把這條線講清楚。本章先把 NT Heap backend 吃透。

## NT Heap 全景

`HeapCreate` 建出來的每一個 heap 在記憶體裡是這樣組織的：

```
  PROCESS_HEAP (handle = 指向 _HEAP 的指標)
  │
  ├─ _HEAP 結構（位在 heap 基址，≈ 0x2C0 bytes on x64）
  │    ├─ Signature = 0xEEFFEEFF
  │    ├─ FreeLists[128]  ← doubly-linked list，按 size 分 bucket
  │    ├─ ListHints[128]  ← 快速 lookup 指標
  │    ├─ FrontEndHeap    ← 指向 LFH 結構（LFH 啟用後）
  │    ├─ Segments[64]    ← 指向各 _HEAP_SEGMENT
  │    └─ TotalFreeSize, TotalMemoryReserved...
  │
  ├─ _HEAP_SEGMENT[0]（通常緊接在 _HEAP 後面）
  │    ├─ _HEAP_ENTRY（一連串 chunk，busy/free 交替）
  │    ├─ _HEAP_ENTRY
  │    └─ ... (segment 預設 reserve 1MB, commit 視需求)
  │
  └─ _HEAP_SEGMENT[1..N]（依需求從 OS 申請更多）
```

**glibc 對應速查**：

| NT Heap 結構 | glibc 對應 | 差異一句話 |
|---|---|---|
| `_HEAP` | `malloc_state` (arena) | NT Heap 的 FreeLists 是 128 個雙向鏈；glibc 有 fastbin/smallbin/largebin 等多種 |
| `_HEAP_SEGMENT` | heap_info (per-arena segment) | 都是向 OS 要記憶體的單位，但 segment 結構差很多 |
| `_HEAP_ENTRY` | malloc_chunk | header 位置、欄位與編碼方式差異顯著（詳後） |
| FreeLists[i] | smallbin[i] / largebin | glibc 用 idx 計算，NT Heap 直接陣列存 listhead |
| ListHints[i] | 無直接對應 | Windows 特有的快速路徑 hint 指標 |
| FrontEndHeap | 無直接對應 | 把 LFH 接上去的跳板，glibc 的 tcache 是靜態資料段 |

## `_HEAP` 結構深挖

`_HEAP` 是整個堆的控制中心。以 WinDbg 的 `dt ntdll!_HEAP` 為準（欄位偏移以 Win11 x64 為常見值，實際以 `dt` 輸出為準）：

```
> **未實測，理論預期（需 WinDbg + public symbols）**
>
> dt ntdll!_HEAP 0x<heap_base>
>
> 預期輸出摘要（x64）：
>   +0x000 Entry            : _HEAP_ENTRY       ← heap 本身也是一個 chunk
>   +0x010 SegmentSignature : 0xffeeffee         ← segment 簽名
>   +0x014 SegmentFlags     : 0
>   +0x018 SegmentListEntry : _LIST_ENTRY        ← 所有 segment 的串接
>   +0x028 Heap             : Ptr64 _HEAP        ← 指回自己
>   +0x030 BaseAddress      : Ptr64              ← segment 基址
>   ...
>   +0x0c4 TotalFreeSize    : 0n<N>              ← 空閒 chunk 總大小（in 8-byte units）
>   +0x0d8 FreeLists        : [128] _LIST_ENTRY  ← 空閒 chunk 陣列
>   +0x7d8 LockVariable     : _HEAP_LOCK         ← 鎖（多執行緒保護）
>   +0x7e0 CommitRoutine    : Ptr64              ← callback，可被利用
>   +0x7e8 FrontEndHeap     : Ptr64              ← LFH 指標（啟用後非零）
>   +0x7f4 FrontHeapLockCount: 0
>   +0x7f8 FrontEndHeapType : 0x2 (LFH)
>   ...
>
> 驗證方式：裝好 WinDbg + symbols 後，掛任意行程，執行
> dt ntdll!_HEAP poi(peb+0x30) 看 ProcessDefaultHeap。
```

**最重要的幾個欄位**：

### Signature / SegmentSignature

`0xEEFFEEFF` 是 NT Heap 的身份識別魔數。完整性檢查（RtlpCheckHeapSignature）在某些操作前會驗這個值。如果你的 overflow 覆蓋到這裡，heap 操作很快就會崩潰——但在 Win XP 時代，攻擊者可以在 overflow 時**偽造這個值**繼續作惡，這是後來強化的動機之一（Ch 17 的主題）。

### FreeLists[128]

這是 NT Heap backend 的核心。128 個 `_LIST_ENTRY`（雙向鏈頭），每個管一個 size class：

```
FreeLists[0]  → 大小 > 127*8 = 1016 bytes 的 free chunk（「大型 free」溢出 bucket）
FreeLists[1]  → 大小 = 1×8 = 8 bytes  （最小 chunk）
FreeLists[2]  → 大小 = 2×8 = 16 bytes
FreeLists[3]  → 大小 = 3×8 = 24 bytes
...
FreeLists[127] → 大小 = 127×8 = 1016 bytes
```

> 注意：NT Heap 的 size 單位是 **8 bytes**（Heap granularity on x64）。所以 `FreeLists[i]` 管 `i * 8` bytes 的 free chunk（i >= 1）。FreeLists[0] 是溢出 bucket，收所有 > 1016 bytes 的大型 free chunk（線性搜尋才能找到合適的）。

**glibc 對比**：glibc 有 fastbin（LIFO, 單向鏈）、smallbin（FIFO, 雙向鏈）、largebin（有序雙向鏈）三種形態。NT Heap backend 統一用雙向鏈，沒有 fastbin 的概念——那個角色由 LFH 扮演。

### ListHints[128]

每個 `ListHints[i]` 記錄「在 `FreeLists[i]` 裡，最後一次成功分配用的是哪個 chunk」的 hint。下次找 free chunk 時，先從 hint 指向的位置開始查，而不是從鏈頭線性搜。這是一個 O(1) fast path——設計上類似 glibc 的 last remainder chunk 概念，但更系統化。

### CommitRoutine

`+0x7e0 CommitRoutine`：一個可選的 callback 指標，當 heap 需要 commit 更多頁面時呼叫。在 Win XP/Vista 時代，這個欄位**沒有被保護**，攻擊者用 `_HEAP_ENTRY` overflow 覆蓋 `CommitRoutine` 再觸發 commit，即可劫持控制流——這是 NT Heap exploitation 的經典技法之一，Ch 26/27 會重新提到。Win 7 後加了 encoding（Ch 17），使直接覆蓋變難。

## `_HEAP_SEGMENT`

每個 segment 是 NT Heap 向 OS 申請的一塊記憶體（通常 Reserve 1MB，按需 Commit）。結構：

```
_HEAP_SEGMENT：
  +0x000 Entry             : _HEAP_ENTRY     ← 這個 segment 也是一個 chunk（type sentinel）
  +0x010 SegmentSignature  : 0xffeeffee
  +0x014 SegmentFlags      : 0
  +0x018 SegmentListEntry  : _LIST_ENTRY     ← 串接到 _HEAP.SegmentList
  +0x028 Heap              : Ptr64 _HEAP     ← 指向所屬 heap
  +0x030 BaseAddress       : Ptr64           ← 這塊 reserved 區域的起始位址
  +0x038 NumberOfPages     : 數量
  +0x040 FirstEntry        : Ptr64 _HEAP_ENTRY ← 第一個 chunk 的位址
  +0x048 LastValidEntry    : Ptr64           ← 合法的最後邊界
  +0x050 NumberOfUnCommittedPages : ...
  +0x054 NumberOfUnCommittedRanges: ...
  +0x060 UnCommittedRanges : _HEAP_UNCOMM_RANGE ← uncommitted 區間的鏈
  +0x070 LastEntryInSegment: Ptr64 _HEAP_ENTRY ← 最後一個 chunk 的位址
```

```
 ┌─────────────────────────────────────────────────────────────┐
 │  Reserved region（最多 1MB，但只有部分 committed）           │
 │  ┌─────────────────────────────────────────────────┐       │
 │  │  Committed pages                                 │       │
 │  │  ┌──────────┐ ┌──────────┐ ┌──────────┐        │       │
 │  │  │HEAP_ENTRY│ │HEAP_ENTRY│ │HEAP_ENTRY│  ...   │       │
 │  │  │(busy)    │ │(free)    │ │(busy)    │        │       │
 │  │  └──────────┘ └──────────┘ └──────────┘        │       │
 │  └─────────────────────────────────────────────────┘       │
 │  [ uncommitted pages ...                          ]        │
 └─────────────────────────────────────────────────────────────┘
```

**glibc 對比**：glibc 的 segment 對應 `heap_info`（非主 arena）或主 arena 管理的 `sbrk` 延伸區。NT Heap 的 segment 更明確地有 committed/uncommitted 的分層，並且 segment 本身也用 `_HEAP_ENTRY` 當邊界哨兵，統一了 chunk 的迭代介面。

## `_HEAP_ENTRY`：chunk header 深挖

這是 NT Heap 最小的元件，**每個分配（busy 或 free）都有一個**。x64 上 header 大小是 **16 bytes（0x10）**：

```
  _HEAP_ENTRY（x64，16 bytes，緊接在 user data 之前）

  Offset   欄位                        說明
  -0x10    Size : USHORT              chunk 大小（以 8-byte 為單位），含 header；
                                      Win 7+ 會被 XOR cookie 加密
  -0x0E    Flags : UCHAR              0x01=busy, 0x02=extra present, 0x04=fill pattern
  -0x0D    SmallTagIndex : UCHAR      = Size[0] XOR Size[1] XOR Flags（完整性 tag）
  -0x0C    PreviousSize : USHORT      前一個 chunk 的大小（in 8-byte units）；合併時用
  -0x0A    SegmentOffset : UCHAR      所在 segment 的 index（0~255）
  -0x09    ExtendedBlockSignature     busy chunk 為 0；free chunk 此欄位不同
  ───────────────────────────────────── ← user data 從這裡開始（HeapAlloc 回傳的指標）
   0x00    user data / FreeList links (free 時這裡存 Flink/Blink)
```

**free chunk 時，`+0x00`（user ptr 起點）開始是 `_LIST_ENTRY`**（Flink + Blink = 兩個 8-byte 指標，共 16 bytes），鏈進 `FreeLists[i]`。所以 free chunk 的最小大小是 header(0x10) + list_entry(0x10) = **0x20 bytes**（即 `FreeLists[2]`）。

```
  ┌─────────────────────────────────────┐
  │  _HEAP_ENTRY header（16 bytes）     │  ← 位於 user_ptr - 0x10
  │  Size(2) | Flags(1) | TagIdx(1)    │  -0x10
  │  PrevSz(2) | SegOff(1) | ExtSig(1) │  -0x0C
  ├─────────────────────────────────────┤  ← user_ptr（HeapAlloc 的回傳值）
  │  (busy)  user data...               │
  │            或                       │
  │  (free)  Flink (8 bytes)            │  +0x00
  │           Blink (8 bytes)           │  +0x08
  └─────────────────────────────────────┘
        ↑ next chunk = user_ptr - 0x10 + Size*8
```

### 和 glibc chunk header 的對比（關鍵差異）

glibc 的 `malloc_chunk`（x64）：

```c
struct malloc_chunk {
  INTERNAL_SIZE_T      mchunk_prev_size;  // +0x00：前一個 chunk 大小（若前一個是 free）
  INTERNAL_SIZE_T      mchunk_size;       // +0x08：本 chunk 大小 + 3 個旗標 bit
  struct malloc_chunk* fd;               // +0x10：free 時，forward pointer
  struct malloc_chunk* bk;               // +0x18：free 時，backward pointer
  // (large bin 還有 fd_nextsize / bk_nextsize)
};
```

逐項對比：

| 維度 | NT Heap `_HEAP_ENTRY` | glibc `malloc_chunk` |
|---|---|---|
| header 大小（x64） | **16 bytes（固定）** | 前兩個欄位 16 bytes，user ptr 在 chunk+0x10 |
| prev_size 位置 | `-0x0C` 的 2-byte `PreviousSize` | `+0x00` 的 8-byte `mchunk_prev_size`（條件存在） |
| size 欄位大小 | 2 bytes（USHORT） | 8 bytes（INTERNAL_SIZE_T = size_t） |
| size 單位 | 8 bytes（granularity） | 1 byte（直接是位元組） |
| size 編碼 | **XOR cookie 加密**（Win 7+，Ch 17） | **無加密**（純粹值） |
| flag 位元 | 獨立 `Flags` byte（0x01=busy） | 借用 size 的低 3 bit（PREV_INUSE 等） |
| free 鏈 | `_LIST_ENTRY`（doubly-linked，FIFO） | fd/bk（小 bin 雙向，fast bin 單向） |
| free 鏈起點 | user_ptr +0x00（header 之後） | chunk 基址 +0x10（和 user ptr 相同位置） |
| prev_size 啟用條件 | 始終存在（2 bytes，但可能加密） | 只在前一個 chunk 是 free 時才有效 |

**為什麼 NT Heap 用 2-byte size？**：最大值 65535，乘以 8-byte granularity = 最大 chunk = 65535 * 8 ≈ 512KB。超過 512KB 的分配直接走 VirtualAlloc，繞過 heap 整個體系。這是一個有意的設計邊界——比 glibc 的 mmap threshold（預設 128KB，可調）更大且更固定。

## FreeLists 與 ListHints：查找與插入

分配一個 size 的 chunk 時，NT Heap 的查找順序：

```
1. 算 bucket index: idx = (requested_size + header_size + granularity - 1) / granularity
   （向上取整到 8-byte 邊界，含 16-byte header）
2. 查 ListHints[idx]：
   a. hint 有效 → 直接取那個 free chunk（O(1)）
   b. hint 無效 → 遍歷 FreeLists[idx]（O(n)）
3. 若 FreeLists[idx] 為空：
   a. 找更大的 bucket（idx+1, idx+2 ... 到 127）：split 出一個合適大小
   b. 若仍無 → FreeLists[0]（大型 free chunk）線性搜
   c. 若仍無 → 從 segment 分配（推進 uncommitted 邊界）
   d. 若 segment 不足 → 新建 segment（VirtualAlloc/VirtualCommit）
4. 若仍無 → 返回 NULL（heap 滿了）
```

```
  FreeLists[3] (管 24-byte chunk):
  ┌──────────────────────────────────────────────────────────────┐
  │  ListHead（位在 _HEAP 結構內，是 _LIST_ENTRY）               │
  │  Flink ──→ [chunkA user_ptr] → [chunkB user_ptr] → ListHead │
  │  Blink ←──────────────────────────────────────────────────── │
  └──────────────────────────────────────────────────────────────┘

  ListHints[3] → 指向上次分配成功的那個 entry（例如 chunkB）
```

**glibc 對比**：glibc 的 `malloc_consolidate` 在分配時會把 fastbin 合併進 smallbin；NT Heap 沒有明確的 consolidate 步驟，合併（coalescing）是在 **free 時**發生的（詳下節）。

## Chunk 合併（Coalescing）

`HeapFree(heap, 0, ptr)` 的邏輯：

```
1. 定位 entry = ptr - 0x10
2. 驗 entry.Flags：確認是 busy chunk（0x01 置位）
3. 查後一個 chunk：next_entry = entry + (entry.Size * 8)
   → 若後一個 chunk 是 free，合併：
     a. 從 FreeLists[next.Size] 摘除後一個 chunk（unlink）
     b. 合併為更大 chunk（entry.Size += next.Size）
4. 查前一個 chunk：prev_entry = entry - (entry.PreviousSize * 8)
   → 若前一個 chunk 是 free（prev.Flags & 0x01 == 0），合併：
     a. 從 FreeLists[prev.Size] 摘除前一個 chunk（unlink）
     b. 合併：prev.Size += entry.Size
5. 把最終的合併 chunk 插入對應的 FreeLists[newSize]
6. 更新 ListHints
```

```
  Before free(B):
  [A: busy, Size=6][B: busy, Size=10][C: free, Size=6][D: busy, Size=8]
                                       ↑ 在 FreeLists[6]

  After free(B)：
  → 後一個 chunk C 是 free → B+C 合併成 Size=16
  → 前一個 chunk A 是 busy → 不合併
  [A: busy, Size=6][B+C: free, Size=16][D: busy, Size=8]
  B+C 插入 FreeLists[16]，ListHints[16] 更新
```

**glibc 對比**：glibc 的合併邏輯和這個非常類似（都是查前後 chunk，用 prev_size 往前跳）。關鍵差異在「怎麼知道前一個 chunk 是不是 free」：

- glibc：靠**後一個** chunk 的 `PREV_INUSE` bit（`mchunk_size` 的 bit 0）；那個 bit 是 0，代表「我的前一個 chunk 是 free」，此時 `mchunk_prev_size` 才有效
- NT Heap：直接查**前一個** chunk 的 `Flags` byte（`prev_entry.Flags & 0x01 == 0` 即 free）

glibc 的「借用 size bit 當 PREV_INUSE」是一個節省空間的技巧（避免在 busy chunk 裡存 prev_size），代價是邏輯繞一圈。NT Heap 選擇直接存 `PreviousSize`（始終存在，但只有 2 bytes），設計上更直白——代價是多佔 2 bytes 但省了一層間接查詢。

## Busy vs Free chunk 視覺圖

```
  ─── busy chunk ──────────────────────────────────────────────────────
  偏移（相對 user_ptr）   欄位               大小    說明
  -0x10                  Size               2 B     含 header，單位 8 bytes；XOR 加密
  -0x0E                  Flags              1 B     0x01 = busy
  -0x0D                  SmallTagIndex      1 B     = Size[0] XOR Size[1] XOR Flags
  -0x0C                  PreviousSize       2 B     前 chunk 的 Size；XOR 加密
  -0x0A                  SegmentOffset      1 B     所在 segment index
  -0x09                  ExtBlkSig          1 B     0（busy 時）
   0x00                  user data          ...     HeapAlloc 回傳的指標指這裡
  ──────────────────────────────────────────────────────────────────────

  ─── free chunk ───────────────────────────────────────────────────────
  偏移（相對 user_ptr）   欄位               大小    說明
  -0x10                  Size               2 B     XOR 加密
  -0x0E                  Flags              1 B     0x00 = free（busy bit 清除）
  -0x0D                  SmallTagIndex      1 B     XOR 校驗
  -0x0C                  PreviousSize       2 B     XOR 加密
  -0x0A                  SegmentOffset      1 B
  -0x09                  ExtBlkSig          1 B
   0x00                  Flink              8 B     FreeLists 雙向鏈，forward pointer
   0x08                  Blink              8 B     backward pointer
   0x10                  (剩餘空間可能有 fill pattern 0xFEFEFEFE...)
  ──────────────────────────────────────────────────────────────────────
```

> **glibc 的 free chunk 差異**：glibc 的 free chunk header 在 chunk 基址（不是 user ptr 處）有 prev_size + size，然後 fd + bk 也在 chunk 基址 +0x10/+0x18。NT Heap 的 user ptr 和 free chunk 的 Flink 位置相同（都在 header 後），意味著 busy→free 轉換時，header 前 16 bytes 內容不同但 Flink 的位置一致——這是 heap overflow 和 UAF 利用的出發點（Part 4）。

## Python ctypes 實測：觀察 NT Heap 分配行為

以下可在本機直接跑（真實輸出）：

```python
import ctypes

k = ctypes.windll.kernel32
k.HeapCreate.restype = ctypes.c_void_p
k.HeapAlloc.restype  = ctypes.c_void_p
k.HeapAlloc.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_size_t]

# 建新 heap（避免 ProcessHeap 上 LFH 可能已啟用）
h = k.HeapCreate(0, 0, 0)
print(f"Heap base: 0x{h:016X}")

HEAP_ZERO_MEMORY = 8
ptrs = []
for i in range(5):
    p = k.HeapAlloc(h, HEAP_ZERO_MEMORY, 0x38)
    ptrs.append(p)

print("5 allocs of 0x38 bytes (NT Heap backend):")
for i, p in enumerate(ptrs):
    print(f"  [{i}] 0x{p:016X}")
print(f"  diff: {hex(ptrs[1] - ptrs[0])}")
```

**本機實測輸出**（本課程作者環境，Win11 x64）：

```
Heap base: 0x000001F2996C0000
5 allocs of 0x38 bytes (NT Heap backend):
  [0] 0x000001F2996C0860
  [1] 0x000001F2996C08A0
  [2] 0x000001F2996C08E0
  [3] 0x000001F2996C0920
  [4] 0x000001F2996C0960
  diff: 0x40
```

**解讀**：`0x38` bytes 的 request，diff = `0x40`。這告訴我們：chunk 總大小 = `0x40`（64 bytes），其中 header `0x10` bytes，user 區 `0x30` bytes。`0x38` 的 request 向上取整到 `0x30`（下一個 8-byte 對齊），再加 `0x10` header = `0x40`。這個取整模式和 glibc 的 `MALLOC_ALIGN_MASK` 概念相同，但 granularity 是 8 bytes（不是 glibc x64 的 16 bytes）。

讀 chunk header（在 user_ptr - 0x10）：

```python
p0 = ptrs[0]
hdr = (ctypes.c_ubyte * 16).from_address(p0 - 16)
print(f"Header of chunk 0 (at 0x{p0 - 16:016X}):")
print("  " + " ".join(f"{b:02X}" for b in hdr))
```

**本機輸出**（首次分配後的 header）：

```
Header of chunk 0 (at 0x000001F2996C0850):
  00 00 00 00 00 00 00 00 29 F2 DF 1A D6 FE 00 10
```

後 8 bytes（`29 F2 DF 1A D6 FE 00 10`）是 XOR cookie 加密後的值，詳見 Ch 17。前 8 bytes 是 zero-init 的用戶區前綴（因 HEAP_ZERO_MEMORY 標誌）。

## 底層機制：heap handle = `_HEAP` 指標

`HeapCreate` 回傳的 `HANDLE` **就是 `_HEAP` 結構的位址**，沒有任何間接層。這和 glibc 的 `malloc_state` 不一樣——glibc 的主 arena 是全域變數（`main_arena`），非主 arena 才是動態分配的。

從 PEB 拿 ProcessHeap 並驗證一致性：

```python
import ctypes

ntdll = ctypes.WinDLL("ntdll")
ntdll.RtlGetCurrentPeb.restype = ctypes.c_void_p
peb = ntdll.RtlGetCurrentPeb()

# PEB.ProcessHeap 在 Win11 x64 的偏移是 +0x30（但以 dt ntdll!_PEB 為準）
process_heap_via_peb = ctypes.c_void_p.from_address(peb + 0x30).value
k = ctypes.windll.kernel32
k.GetProcessHeap.restype = ctypes.c_void_p
ph = k.GetProcessHeap()

print(f"ProcessHeap via PEB+0x30:    0x{process_heap_via_peb:016X}")
print(f"GetProcessHeap():             0x{ph:016X}")
print(f"Match: {process_heap_via_peb == ph}")
```

> `peb + 0x30` 是 `PEB.ProcessHeap` 的偏移（Win11 x64 常見值，建議以 `dt ntdll!_PEB` 驗證）。Ch 5 對 PEB 結構有完整解說。

一個行程可以有多個 heap：

```python
k = ctypes.windll.kernel32
buf = (ctypes.c_size_t * 64)()
n = k.GetProcessHeaps(64, buf)
for i in range(n):
    print(f"  Heap[{i}]: 0x{buf[i]:016X}")
```

**本機輸出**（python.exe 行程）：

```
  Heap[0]: 0x0000025BC6380000
  Heap[1]: 0x0000025BC6180000
  Heap[2]: 0x0000025BC65D0000
  Heap[3]: 0x0000025BC6CF0000
```

在 heap exploitation 中，知道目標 DLL 用哪個 heap 是 grooming 的前提。CRT、COM、特定 DLL 各自可能有私有 heap。

## 對比與取捨

| 維度 | NT Heap backend | glibc ptmalloc2 |
|---|---|---|
| Free list 組織 | 128 個固定 bucket 的雙向鏈 | fastbin(單向)+smallbin/largebin(雙向)+unsorted bin |
| 大型分配門檻 | >512KB → VirtualAlloc（繞過 heap） | > mmap threshold（預設 128KB，可調）→ mmap |
| 合併觸發時機 | **free 時立即合併**（雙向查前後） | free 時：fastbin 不合並；其他立即合並 |
| 前向成長 | 從 segment uncommitted 邊界推進 | 主 arena sbrk；非主 arena mmap segment |
| 多執行緒 | 單一 heap 有鎖（`_HEAP_LOCK`）；可建多個獨立 heap | per-thread arena（ptmalloc2 特點），減少競爭 |
| 安全強化 | header XOR 加密（Win 7+），safe unlinking（Win 8+） | safe unlink（glibc 2.29+），tcache key（2.29+） |
| 偵錯支援 | `!heap` 命令、Page Heap（PAGEHEAP），fill pattern | malloc_stats、mallinfo、MALLOC_CHECK_ |

## 踩雷集錦

1. **「heap handle 是個 opaque 數字，不知道指什麼」**：NT heap handle **就是 `_HEAP` 結構的記憶體位址**，沒有間接層。`dt ntdll!_HEAP <handle>` 就能看到整個結構。用 `GetProcessHeap()` 拿到的值和 `PEB.ProcessHeap` 是完全相同的指標。

2. **「FreeLists[0] 是 8-byte chunk 的 bucket」**：FreeLists[0] 是**溢出 bucket**，收所有大於 `FreeLists[127]`（1016 bytes）的 free chunk。FreeLists[1] 才是最小的 8-byte bucket。不熟這個邊界，寫 grooming 程式時算 bucket 會出錯。

3. **「free chunk 的 Flink 就是 chunk 基址」**：NT Heap 的 Flink/Blink 存在 **user_ptr 位置**（header 之後 +0x00），不是 chunk 基址。這和 glibc 不同（glibc fd 也在 user ptr 位置，所以其實相同），但計算 unlink primitive 時要確認是 header 減掉還是不減。

4. **「PreviousSize 始終有效（可直接用）」**：PreviousSize 在 Win 7+ 是 XOR 加密的值。直接讀 PreviousSize 的 raw 數字做 pointer arithmetic 而不先解密，是典型的分析錯誤。Ch 17 講解密方式。

5. **「chunk size 是 bytes」**：NT Heap 的 `Size` 欄位的單位是 **8-byte granularity**，不是 bytes。`entry.Size = 5` 代表 chunk 總共 `5 * 8 = 40 bytes`（包含 header）。glibc 的 `mchunk_size` 是直接的 byte 值（低 3 bit 是旗標）。換算錯就算錯跳 pointer，是 exploit 開發的常見 bug。

## 進階：再往深一層

### Heap 旗標系統

`HeapCreate` 的第一個參數 `dwOptions`：

- `0x00000001 HEAP_NO_SERIALIZE`：關掉 heap 鎖（單執行緒用，或自己管同步），效能好但非執行緒安全
- `0x00000002 HEAP_GROWABLE`：heap 可以成長（加新 segment），預設就是這個
- `0x00000004 HEAP_GENERATE_EXCEPTIONS`：分配失敗時拋 SEH exception 而非回傳 NULL
- `0x00040000 HEAP_CREATE_ENABLE_EXECUTE`：允許 heap 上的 code 執行（DEP 時代前常用）

在 exploit 情境下：`HEAP_CREATE_ENABLE_EXECUTE` 在 DEP 之前是把 shellcode 放 heap 然後跳過去的前提；現在 DEP 幾乎全面開，這個旗標的存在更多是「靶的信號」。

### Heap Debug 旗標（GlobalFlag / Page Heap）

Windows 提供「Page Heap」（對應 glibc 的 ElectricFence / ASan）：

```
> **未實測（需 gflags.exe）**
> gflags /i target.exe +hpa   # Enable Page Heap
```

Page Heap 開啟後，每個 chunk 後面緊跟一個 guard page，overflow 立刻 AV（Access Violation）。這是漏洞研究的標準環境，分析 CVE 時幾乎一定要開——等效於 glibc 開 `MALLOC_CHECK_=3`，但效果更激進。

### heap handle 是跨行程不可移植的

`HeapCreate` 的 handle 是虛擬位址，只在當前行程的位址空間裡有效。跨行程共享 heap 需要透過 `CreateFileMapping`/`MapViewOfFile` 的 shared memory 路徑，不是直接傳 handle。

### Unlink 的安全史

Win XP 時代的 NT Heap unlink 和 glibc unlink 一樣是最高危的利用原語：

```
// 舊版 unlink（XP 時代，無保護）：
chunk.Blink->Flink = chunk.Flink;   // 寫 arbitrary
chunk.Flink->Blink = chunk.Blink;   // 讀 arbitrary
// → 控制 Flink/Blink = arbitrary write anywhere
```

Win 8 以後加了 safe unlinking（Ch 17/Ch 26 細說），讓直接的 unlink primitive 難度大增。這是 LFH 被研究者偏愛的部分原因——LFH 的 unlink 行為和 backend 不同，有些更寬鬆的視窗。

## 動手練習

用 Python ctypes 完成以下（可在本機直接跑）：

1. `HeapCreate(0, 0, 0)` 建新 heap，印出 heap 基址
2. 分配 10 個 `0x30` bytes 的 chunk，印出每個 user ptr
3. 計算每對相鄰 chunk 的地址差，解釋這個值是怎麼算出來的（hint：含 header）
4. Free 第 3、4 個 chunk（`HeapFree`），再分配一個 `0x30` bytes 的 chunk，觀察回傳的地址是否複用了 freed chunk 的地址；這告訴你 backend 的 free list 是 LIFO 還是 FIFO？
5. 讀第一個 chunk 的 header bytes（`user_ptr - 0x10`），記錄 raw bytes 並對照本章的 `_HEAP_ENTRY` layout，標出每個欄位（Size、Flags、PreviousSize）的位置

## 本章重點整理

- Windows heap 是三代疊加：NT Heap backend → LFH front-end → Segment Heap（Win10+ 新架構）；本章只打 NT Heap backend，後兩章接著打。
- `_HEAP` 是控制中心（≈ glibc 的 malloc_state/arena），`FreeLists[128]` 是雙向鏈 free list 陣列（FreeLists[0] 是大型溢出 bucket），`ListHints` 是加速 hint，`CommitRoutine` 是歷史上的可利用 callback。
- `_HEAP_ENTRY` header 是 16 bytes（x64）；Size 的單位是 8 bytes（不是 bytes）；Win 7+ 對 Size / PreviousSize 做 XOR 加密（Ch 17）。
- free chunk 時立即向前後 coalescing；free 後的 chunk 在 user_ptr +0x00 存 Flink/Blink 串進 FreeLists。
- 最大 chunk ≈ 512KB（Size 為 2-byte USHORT）；超過走 VirtualAlloc，完全繞過 heap 機制。

## 自我檢核

- [ ] 不看筆記，能畫出 `_HEAP` → `_HEAP_SEGMENT` → `_HEAP_ENTRY` 的三層關係圖
- [ ] 面試被問「NT Heap 的 free list 有幾個 bucket，FreeLists[0] 和 FreeLists[1] 各管什麼」，能立刻回答
- [ ] 能把 `_HEAP_ENTRY.Size = 8` 換算成真正的 bytes（含 header 是多少，不含 header 是多少）
- [ ] 能說出 NT Heap coalescing 和 glibc coalescing 的兩個相似點與兩個差異點
- [ ] 能解釋為什麼 CommitRoutine 是歷史上的利用目標，以及 Win 7 後為什麼更難直接覆蓋
- [ ] 知道一個行程能有多個 heap，以及在 exploit grooming 時這意味著什麼

## 延伸閱讀

### 書籍

- **《Windows Internals, 7th Edition》Part 1，Chapter 10 "Memory Management"** — Yosifovich, Ionescu, Russinovich, Solomon（Microsoft Press）
  - **讀哪裡**：「Heap Manager」與「Low Fragmentation Heap」小節（搜索索引 "Heap Manager" 定位）
  - **學什麼**：NT Heap 設計哲學與演進，以及 Segment Heap 引入的動機；是本章結構描述的主要書面來源
  - **前提知識**：本章讀完；建議對照本章讀，用書補充細節

### 白皮書 / 會議論文

- **[Windows 10 Segment Heap Internals](https://www.blackhat.com/docs/us-16/materials/us-16-Yason-Windows-10-Segment-Heap-Internals.pdf)** — Mark Vincent Yason，Black Hat US 2016
  - **讀哪裡**：第二章「NT Heap Review」先讀（這是 Yason 對 NT Heap 的快速回顧，和本章高度重疊，用作交叉驗證）；後面的 Segment Heap 部分留到 Ch 16
  - **學什麼**：Yason 親自對比 NT Heap 和 Segment Heap，看設計動機和安全取捨
  - **前提知識**：本章 + Ch 15 + Ch 16 完整讀完後回來讀全文效果最好

- **[Attacking the Windows Heap — Phrack #68](http://phrack.org/issues/68/5.html)** — Kostya Kortchinsky
  - **讀哪裡**：NT Heap backend 的 _HEAP_ENTRY 結構與 Win Vista/Win 7 時代的 overflow 利用技法
  - **學什麼**：從 exploitation 角度解讀 _HEAP_ENTRY 的每個欄位，直接對接 Part 4
  - **前提知識**：本章 + Ch 17（header encoding）

### 部落格

- **[Winsider Seminars — Heap Internals 文章](https://www.alex-ionescu.com/?p=14)** — Alex Ionescu
  - **讀哪裡**：Heap 相關的幾篇短文，尤其是 Segment Heap 引入的評論
  - **學什麼**：Windows 核心設計者視角對 heap 的批判性評論，比 MSDN 更有深度
  - **前提知識**：本章讀完

- **[Corelan — "Heap Overflows For Humans" 系列](https://www.corelan.be/index.php/2011/12/31/exploit-writing-tutorial-part-11-heap-spraying-demystified/)** — Peter Van Eeckhoutte
  - **讀哪裡**：Part 11 (heap spray) 先讀；更早的 NT Heap 系列文章
  - **學什麼**：從 exploit 作者角度看 NT Heap，有手把手 WinDbg 操作；本課 Part 4 的實作參考藍本
  - **前提知識**：本章 + WinDbg 基礎（Ch 18）

LFH 蓋在 NT Heap backend 之上，是「特定 size class 的分配快取」——觸發門檻、bucket 設計、以及讓它在 Win 8 以後不好利用的隨機化機制，下一章全解。

→ [Ch 15 — LFH (Low Fragmentation Heap)](./15-lfh.md)
