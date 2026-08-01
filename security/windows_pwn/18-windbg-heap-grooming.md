# Ch 18 — 用 WinDbg !heap 觀測與 heap grooming 基礎

> **目標**：學會用 WinDbg 的 `!heap` 系列指令把 heap 的內部狀態照出來，知道每個指令的用途和預期輸出長相；同時建立 heap grooming（heap feng shui）的心智模型——為什麼要控制 heap 佈局、用什麼操作控、控到什麼程度才算「可利用」。這章是 Part 4 精確利用的前置地基。

> **環境**：WinDbg（WinDbgX 或 cdb + public symbols 已設定）。所有 `!heap` 指令輸出均標 **未實測，理論預期**，因本機 cdb 尚未就位。每個指令附有「裝好後請這樣驗」的說明。對照工具：`gdb` + `pwndbg`（你已熟悉）。

## 為什麼需要這個？

你在 glibc heap exploit 時做的第一件事是什麼？`pwndbg` 裡打 `heap`、`bins`、`tcache`，看當前佈局。打 Windows heap 也不例外——你需要能把 chunk 位址、freelist 狀態、LFH bucket 的哪幾個 slot 還空著全部照出來，才能知道：

- 現在 heap 的狀態是不是你「理論計算」的樣子
- 你的 spray/grooming 有沒有奏效
- 你偽造的 fake chunk 放在哪、相鄰的東西是誰

`pwndbg` 對 glibc 的透視程度，`!heap` 系列對 NT Heap 做到同樣的事——差別是 Windows 的指令更分散、選項更多，你要記的不是「這個指令的所有 flag」，而是「每個場景用哪個指令、預期輸出是什麼格式」。

## 先建立直覺：heap 觀察工具的對應關係

```
  gdb + pwndbg                  WinDbg !heap 系列
  ─────────────────────────────────────────────────
  heap                    ↔     !heap -s         （summary，所有 heap handle）
  heap -v                 ↔     !heap -stat       （統計：每個 size class 用了多少）
  bins                    ↔     !heap -flt s <sz> （找特定大小的 free chunk）
  tcache                  ↔     !heap -l          （看 LFH bucket）
  p &main_arena           ↔     dt ntdll!_HEAP <handle>
  p *chunk_ptr            ↔     dt ntdll!_HEAP_ENTRY <chunk_ptr-0x10>
  x/gx addr               ↔     dq addr L<count>
  ─────────────────────────────────────────────────
  主要差距：
  - WinDbg 有 symbols，能直接 dt 結構；pwndbg 靠 libc offsets
  - !heap 是 heap-aware extension，不只是 raw memory dump
  - glibc 的 freelist 是全域的，NT Heap 的 freelist 是 per-heap-handle 的
```

## !heap -s：所有 heap 的 summary

**用途**：列出當前 process 的所有 heap handle（一個 process 通常有好幾個），看每個 heap 的基址、大小、flags。這是每次 heap 除錯的第一步。

**指令**：

```
!heap -s
```

**預期輸出（未實測，理論預期）**：

```
NtGlobalFlag enables following debugging aids for new heaps:
    heap handle: 0x00000000007a0000  Flags: 0x00000002  Uncommitted ranges: 8
    heap handle: 0x0000000000680000  Flags: 0x00001000  Uncommitted ranges: 2
    heap handle: 0x0000000000e40000  Flags: 0x00001000  Uncommitted ranges: 1

Process Default Heap: 0x00000000007a0000
```

**解讀**：

- 每行一個 heap handle，這個值就是 `_HEAP*` 指標，傳給 `HeapAlloc`/`HeapFree` 的那個
- `Process Default Heap` 就是 `GetProcessHeap()` 的回傳值，也是 `malloc/free` 預設使用的那個
- `Flags: 0x00001000` 代表這個 heap 啟用了 LFH（`HEAP_LFH_ENABLED = 0x1000`，不同版本旗標值略有差異）
- 每次跑程式，handle 基址都因 ASLR 不同；除錯期間可用 `!address` 或 `-noaslr` 工具固定基址

**pwndbg 對應**：`pwndbg` 的 `heap` 不需要指定 heap handle，因為 glibc 只有一個全域 `main_arena`；Windows 這個 per-handle 的概念是額外複雜度，習慣之。

## !heap -stat：size class 統計

**用途**：看某個 heap handle 裡每個 size class 分配了多少 chunk、用了多少記憶體。用來確認 LFH 有沒有被觸發（分配數超過閾值後特定 size class 就會顯示 LFH enabled）。

**指令**：

```
!heap -stat -h <heap_handle>
```

（`-h` 後接 `!heap -s` 拿到的任意一個 handle；省略 `-h` 則看 default heap）

**預期輸出（未實測，理論預期）**：

```
heap @ 00000000007a0000
group-by: TOTSIZE max-display: 20
    size     #blocks  total(bytes)  percentage
    0x28       1a00      40800         18.3%
    0x50        800      25600         11.5%
    0xb8        320       e100          6.3%
    ...
    LFH enabled for sizes: 0x28, 0x50, 0xb8
```

**解讀**：

- `size` 欄是 user-visible 的分配大小（不含 header）
- 某個 size 出現在 `LFH enabled` 那行，代表這個 bucket 已切換到 LFH 管理
- NT Heap 的 LFH 啟用閾值：同一 size 連續分配超過 18 次（預設）就觸發 LFH 轉換
- 這個指令是 grooming 前的必看——確認你操作的 size 是走 LFH 路還是 Backend 路

## !heap -flt s：找特定大小的 free chunk

**用途**：在整個 heap 裡搜尋大小符合 `<size>` 的空閒 chunk，列出它們的位址。打 heap 時你需要知道「現在有沒有適合的空洞讓我的 fake chunk 落進去」。

**指令**：

```
!heap -flt s <size_in_hex>    （size 含 header；通常你要找 user_size + 0x10）
```

或更精確地鎖定特定 heap：

```
!heap -flt s <size> -h <heap_handle>
```

**預期輸出（未實測，理論預期）**：

```
_HEAP @ 7a0000
    HEAP_ENTRY Size Prev  Flags    UserPtr UserSize - state
      007a1a30 0005 0000  [00]     007a1a40    00030 - free
      007a3c80 0005 0000  [00]     007a3c90    00030 - free
      007a9010 0005 0002  [00]     007a9020    00030 - free
```

**解讀**：

- `HEAP_ENTRY` 是 chunk header 的位址（`UserPtr = HEAP_ENTRY + 0x10`）
- `Size` 欄是 **encoded 後再解算的** size（以 8-byte granularity 為單位，這裡 `0x0005 * 8 = 40 = 0x28 bytes`）
- `state` 欄：`free` / `busy` / `LFH`
- 找到的是 freelist 上的 chunk；LFH 路徑的 free chunk 不一定在這裡（LFH 有自己的 bitmap）

**pwndbg 對應**：`bins` 指令列出 fastbin/unsortedbin/smallbin 等的 free chunk；`!heap -flt s` 是功能上最接近的對應，但搜尋邏輯是 size-based scan，不是 per-bin 遍歷。

## !heap -x：查詢特定位址的 chunk

**用途**：給一個任意位址，問 WinDbg「這個位址屬於哪個 heap 的哪個 chunk？」。你有一個 user pointer，想知道它的完整 chunk 資訊（header 偏移、大小、狀態）時用這個。

**指令**：

```
!heap -x <user_ptr>
```

**預期輸出（未實測，理論預期）**：

```
Entry     User      Heap      Segment       Size  PrevSize  Unused    Flags
-----------------------------------------------------------------------------
007a2a40  007a2a50  007a0000  007a1000      0050      0028       10    busy
```

**解讀**：

- `Entry`：header 位址（`= user_ptr - 0x10`）
- `User`：你傳進去的 ptr（user data 起始）
- `Heap`：所屬 heap handle
- `Segment`：所屬 segment（backend 的概念，LFH chunk 這欄顯示方式不同）
- `Size`：chunk 總大小（bytes）
- `Unused`：alignment padding（`UnusedBytes` 欄位）
- `Flags`：`busy` / `free` / `LFH`

實際打洞時，`!heap -x <uaf_ptr>` 讓你確認那個被 free 的 chunk 現在什麼狀態。

## dt ntdll!_HEAP：看 heap 結構本體

**用途**：直接 dump `_HEAP` 結構的所有欄位。最重要的用途是看 `Encoding`（XOR key，Ch 17）、LFH 指標、freelist 頭。

**指令**：

```
dt ntdll!_HEAP <heap_handle>
```

**預期輸出（未實測，理論預期，節選關鍵欄位）**：

```
ntdll!_HEAP
   +0x000 Entry            : _HEAP_ENTRY
   +0x010 SegmentSignature : 0xffeeffee
   +0x014 SegmentFlags     : 0
   +0x018 SegmentListEntry : _LIST_ENTRY
   +0x028 Heap             : 0x00000000`007a0000 _HEAP   <- 指回自己（可用來驗 heap base）
   ...
   +0x07c Encoding         : _HEAP_ENTRY              <- XOR key（Ch 17 說的）
   ...
   +0x0f8 FreeLists        : _LIST_ENTRY              <- backend freelist 頭
   ...
   +0x158 FrontEndHeap     : 0x00000000`007a9000 Void <- LFH 指標（若 LFH 啟用）
   +0x15c FrontHeapLockCount : 0
   +0x160 FrontEndHeapType : 0x2 ''               <- 0x2 = LFH（0x1 = LAL，舊版）
```

**解讀**：

- `SegmentSignature: 0xffeeffee` 是 NT Heap 的魔數，只要看到這個你就確定這是一個合法 `_HEAP`
- `Encoding` 欄就是 Ch 17 說的 XOR key，整個 `_HEAP_ENTRY` 大小的值
- `FrontEndHeap` 非 NULL + `FrontEndHeapType == 0x2` 代表 LFH 已啟用
- `FreeLists` 是雙向鏈表的頭，backend 的空閒 chunk 掛在這裡

## dt ntdll!_HEAP_ENTRY：看 chunk header

**用途**：直接展開一個 chunk 的 header 結構。注意你看到的是**記憶體裡的 encoded 值**，不是真實的 Size/Flags——要搭配 `!heap -x` 看 decoded 後的結果。

**指令**：

```
dt ntdll!_HEAP_ENTRY <chunk_ptr - 0x10>
```

（chunk_ptr 是 user 拿到的指標，header 在它前面 0x10 bytes）

**預期輸出（未實測，理論預期）**：

```
ntdll!_HEAP_ENTRY
   +0x000 Size             : 0x4f2a       <- encoded！不是真實大小
   +0x002 PreviousSize     : 0x9c31       <- encoded！
   +0x004 SmallTagIndex    : 0x7e         <- checksum
   +0x005 Flags            : 0xb3         <- encoded！
   +0x006 UnusedBytes      : 0x19
   +0x007 ExtendedBlockSignature : 0x0 ''
```

**解讀**：

- 這裡的 `Size` / `PreviousSize` / `Flags` 都是 encoded 過的，不要直接解讀它們的字面值
- 搭配 `!heap -x <ptr>` 拿 WinDbg 幫你 decoded 的真實數值
- 想手動驗算：`dt ntdll!_HEAP <heap_handle> Encoding` 拿 key，然後 XOR 這裡的值

## !heap -l：找出 LFH bucket 資訊

**用途**：列出特定 heap 的 LFH bucket 狀態，包含哪些 size 啟用了 LFH、每個 bucket 的 subsegment 位址、目前用量。

**指令**：

```
!heap -l <heap_handle>
```

或詳細模式：

```
!heap -v <heap_handle>
```

**預期輸出（未實測，理論預期，節選）**：

```
LFH Bucket 5 (size 0x28):
    Subsegment @ 007b2000 (UserBlocks @ 007b2010)
      Bitmap: [1 1 0 1 0 0 0 0 ...]   <- 1=busy, 0=free
      Slot 0: 007b2010 [busy]
      Slot 1: 007b2038 [busy]
      Slot 2: 007b2060 [free]
      Slot 3: 007b2088 [busy]
      ...
LFH Bucket 9 (size 0x50):
    Subsegment @ 007c4000 (UserBlocks @ 007c4010)
      ...
```

**解讀**：

- 每個 LFH bucket 對應一個 size class（bucket index 和 size 的對應是 Part 2 Ch 15 細講的那張表）
- `UserBlocks` 是整個 subsegment 裡 user data 的起始，slot 之間緊密排列
- Bitmap 裡的 `0`（free）就是你的 spray 目標
- 這就是你在 grooming 後要用 `!heap -l` 確認的輸出：「我的 free hole 在 slot X，相鄰是我控制的 busy chunk」

## heap grooming 基礎概念

### 為什麼要 grooming？

glibc 的 heap exploit 裡，你已經做過 grooming，只是可能叫不同名字：

- House of Orange：把 top chunk 弄得恰好在 unsorted bin 裡、然後 UAF 打它的 fd
- tcache poisoning：控 tcache 鏈表讓 malloc 回傳任意位址

Windows heap 的問題一樣：你有一個 UAF 或 overflow 原語，但 heap 是隨機佈局的——你需要讓「我的 victim chunk」和「我能寫的 chunk」恰好相鄰，並且知道 victim 的確切位址。

**grooming 的目標**：把 heap 從「隨機」狀態控制成「可預測」狀態，讓你的原語能命中你想要的目標。

### 三個基本操作

```
操作 1：填滿（fill）
  目的：把現有的空洞全部用掉，讓之後的分配落在 heap 末端（新映射的記憶體）
  方法：分配大量同 size 的物件，直到 freelist/LFH bitmap 清空

操作 2：打洞（punch holes）
  目的：在填滿後有選擇地 free 某些物件，在可預測位置留下空洞
  方法：按特定 pattern 釋放（例如：free 所有偶數 index 的 chunk）

操作 3：觸發分配到洞（trigger）
  目的：讓目標物件（victim）落進你預留的洞
  方法：觸發漏洞路徑分配特定 size 的物件，它應該落入 freelist/LFH 最近的那個洞
```

這個序列就是 heap feng shui（堆風水）——不是玄學，是可重複、可預測的工程操作。

### NT Heap grooming 的特殊考量

**LFH 路徑**（size <= 0x4000 且分配次數 > 閾值）：

```
LFH 分配的是 bitmap slot，不是 freelist chunk。
Spray → Free → Trigger 的步驟：

  1. spray 64 個 size=0x50 的物件（觸發 LFH 啟用）
  2. free 第 32–47 個（在 bitmap 中間打洞）
  3. 觸發漏洞：漏洞程式碼分配 size=0x50 → 落入 bitmap 的第一個空洞（idx 32）
  4. 用 !heap -l 確認 idx 32 的 slot 狀態
```

**Backend 路徑**（非 LFH 的大 chunk 或剛啟動時）：

```
Backend 用 freelist，行為類似 glibc 的 smallbin：
  First-fit 分配 → 找最小夠大的 free chunk
  
  1. 分配 heap_spray_obj[0..N]（size = target_size）
  2. free heap_spray_obj[some_index]（製造 free chunk）
  3. 漏洞分配同 size → 落入剛 free 的 chunk（first-fit 行為）
```

**跨路徑的陷阱**：你 spray 時 LFH 還沒觸發（走 backend），spray 後 LFH 啟用了，後續分配走 LFH——兩條路的佈局邏輯完全不同，grooming 前必須先用 `!heap -stat` 確認走哪條路。

### spray 設計原則

```
  好的 spray 物件：
  ┌──────────────────────────────────────┐
  │  1. 大小精確 = target_size           │
  │  2. 你完全控制它的內容               │
  │  3. 你能在任意時間釋放任意一個       │
  │  4. 釋放後 heap manager 不會把它合併│
  │     回 backend（LFH chunk 不會 coalesce）│
  └──────────────────────────────────────┘
  
  常用 spray 物件（瀏覽器 exploit 情境）：
  - DOM 節點（固定大小的 C++ 物件）
  - ArrayBuffer backing store
  - String / JavaScript object
  
  常用 spray 物件（Win32 API 情境）：
  - BITMAP / GDI 物件（kernel 層，Part 7 才碰）
  - BSTR / SAFEARRAY（COM 物件）
  - 自訂 struct 的 HeapAlloc
```

### 用 WinDbg 驗證 grooming

grooming 的黃金驗證流程：

```
步驟 1：spray 前
  !heap -l <handle>  →  看 LFH bitmap 的初始狀態

步驟 2：spray 後
  !heap -l <handle>  →  所有 slot 應該都是 busy

步驟 3：punch holes 後
  !heap -l <handle>  →  特定 index 的 slot 應該是 free

步驟 4：trigger 後
  !heap -l <handle>  →  確認 victim chunk 落在哪個 slot
  !heap -x <victim_ptr>  →  確認 victim 的鄰居是誰

步驟 5：驗相鄰關係
  dq <victim_ptr - 0x50> L10  →  dump victim 前後的記憶體，確認相鄰物件符合預期
```

## 底層機制：WinDbg !heap 指令完整圖

```
  !heap 指令決策樹
  ─────────────────────────────────────────────────────────────────
  我想要...                           用這個指令
  ─────────────────────────────────────────────────────────────────
  所有 heap handle 的 overview        !heap -s
  特定 heap 的 size class 統計        !heap -stat -h <handle>
  找特定大小的 free chunk 位址        !heap -flt s <size>
  查一個 ptr 的 chunk 資訊            !heap -x <ptr>
  看 LFH bucket 的 bitmap             !heap -l <handle>
  全部 chunk 逐一 dump（慢）          !heap -v <handle>
  看 _HEAP 結構本體                   dt ntdll!_HEAP <handle>
  看 chunk header（raw，未 decode）   dt ntdll!_HEAP_ENTRY <ptr-0x10>
  看 freelist 鏈表                    dt ntdll!_LIST_ENTRY <handle+0xf8>
  dump raw memory                     dq <addr> L<count>
  ─────────────────────────────────────────────────────────────────
```

## 對比 gdb + pwndbg

| 任務 | gdb + pwndbg | WinDbg |
|---|---|---|
| 所有 heap 概況 | `heap` | `!heap -s` |
| freelist 狀態 | `bins` | `!heap -flt s <size>` |
| tcache 狀態 | `tcache` | `!heap -l <handle>`（LFH bitmap）|
| chunk 詳情 | `malloc_chunk <ptr>` | `!heap -x <ptr>` + `dt _HEAP_ENTRY`|
| heap 結構 | `p main_arena` | `dt ntdll!_HEAP <handle>` |
| 原始 memory | `x/gx addr` | `dq addr L<count>` |
| 結構體展開 | 靠 debug info 或 ptype | `dt ntdll!_STRUCTURE addr`（有 symbols）|

**最大差異**：WinDbg 有官方 symbols，`dt ntdll!_HEAP` 等於 pwndbg 的 `malloc_chunk` 但精度更高、欄位更完整。代價是：Windows heap 有 encoding，你看到的 raw header 是加密的，`!heap -x` 才給 decoded 值。

## 踩雷集錦

1. **「!heap 不需要加 handle，它自己知道 default heap」**：有些子指令預設用 default heap，有些要指定 `-h`。保險起見一律加 `-h <handle>`，`<handle>` 從 `!heap -s` 拿。

2. **「dt ntdll!_HEAP_ENTRY 顯示的 Size 就是真實大小」**：不是。那是 encoded 後的值。真實大小用 `!heap -x`，或手動 XOR `Heap->Encoding` 解算。把 encoded 的 `0x4f2a` 當成「chunk 大小是 0x4f2a * 8」——你的 grooming 計算就全錯了。

3. **「spray 完 LFH 就一定啟用了」**：LFH 啟用有分配次數閾值（預設 18 次），而且只對**同一 size** 的分配計數。你 spray 了 100 個不同 size 的物件，每個 size 各 1 個，LFH 一個也沒啟用。

4. **「grooming 在 debug build 裡也能用、但 release 下佈局不同」**：debug build 的 heap 有 guard page、填充 pattern（0xCDCDCDCD = uninitialized）、額外的 heap header——佈局和 release build 差很多。在 debug build 測試 grooming 沒有意義，一定要在 release 下驗。

5. **「!heap -v 可以拿來 dump 所有 chunk，很方便」**：可以，但對大 heap 它會跑非常久（每個 chunk 都 walk）。先用 `!heap -flt` 縮範圍，只有確定要全掃時才用 `-v`。

## 進階：再往深一層

### TTD（Time Travel Debugging）配合 heap 分析

Ch 41 會詳細講 TTD，但 heap grooming 的情境下，TTD 特別有用：你可以錄一段 spray + trigger 的執行，然後倒帶到 trigger 前，逐指令看分配序列和 bitmap 狀態的變化。對調 LFH 分配隨機化（randomization）這種「我以為我打到那個 slot，但其實沒有」的問題，TTD 可以精確重播，避免靠肉眼猜。

### 用 Python ctypes 做 grooming 骨架

不需要真實漏洞，你可以用 ctypes 寫一個 grooming skeleton 練習 spray / punch / verify 的操作：

```python
import ctypes, time

kernel32 = ctypes.WinDLL("kernel32")
heap = kernel32.GetProcessHeap()

TARGET_SIZE = 0x50
SPRAY_COUNT = 64

# phase 1: spray
ptrs = []
for i in range(SPRAY_COUNT):
    p = kernel32.HeapAlloc(heap, 0, TARGET_SIZE)
    ptrs.append(p)
    
print(f"Sprayed {SPRAY_COUNT} chunks of size 0x{TARGET_SIZE:x}")
print(f"First: 0x{ptrs[0]:016x}, Last: 0x{ptrs[-1]:016x}")
print(f"Stride: 0x{(ptrs[1] - ptrs[0]):x}")  # LFH: 應該等於 TARGET_SIZE + 0x10

# phase 2: punch holes（釋放偶數 index）
for i in range(0, SPRAY_COUNT, 2):
    kernel32.HeapFree(heap, 0, ptrs[i])

print("Holes punched at even indices. Attach WinDbg now and run:")
print(f"  !heap -l {heap:#x}")
input("Press Enter to continue...")

# phase 3: trigger 分配到洞
victim = kernel32.HeapAlloc(heap, 0, TARGET_SIZE)
print(f"Victim allocated at: 0x{victim:016x}")
# 驗：victim 應該在 ptrs[0] 的位址（LFH 的 first-fit-ish 行為）
```

這段能跑（不需要 WinDbg），在 `input()` 暫停時掛上 WinDbg，用 `!heap -l <heap>` 看 bitmap，印證洞在預期位置。

### LFH randomization 對 grooming 的影響

LFH 有一個 slot 隨機化機制：分配新 slot 時不是按 bitmap index 順序走，而是隨機 pick 一個 free slot。這讓「spray N 個 → free 第 K 個 → trigger 必然落在 slot K」的假設不成立。

繞法：分配夠多（把整個 subsegment 填滿後開第二個 subsegment），然後在新 subsegment 打洞。新 subsegment 的 bitmap 全空，隨機選出的 first slot 機率上是可預測的。Ch 28 有完整的 LFH grooming 實戰，這裡先建立「randomization 存在、需要用 subsegment boundary 控制」的直覺。

## 動手練習

裝好 WinDbg 後，在一個真實程式（任意一個 Windows exe 都行）上跑以下序列，並截圖/記錄輸出：

1. 用 WinDbg attach 任意一個 process（`windbg -p <pid>` 或啟動時 attach）
2. 跑 `!heap -s`，記下 default heap handle
3. 跑 `!heap -stat -h <handle>`，找出最多 block 的那個 size
4. 跑 `!heap -flt s <size>`，列出那個 size 的 free chunk
5. 取第一個 `UserPtr`，跑 `!heap -x <UserPtr>`，確認 `State: free`
6. 跑 `dt ntdll!_HEAP_ENTRY <UserPtr-0x10>`，記錄 encoded 的 Size 值
7. 跑 `dt ntdll!_HEAP <handle> Encoding`，拿到 XOR key
8. 手算：XOR encoded Size 和 key 的對應位元組，看看是否還原成合理的大小值

這八步就是每次 heap exploit 開始時的「定向」操作。

## 本章重點整理

- `!heap -s` 是起點，拿到所有 heap handle；`!heap -stat` 確認 size class 走 LFH 還是 Backend；`!heap -flt s` 找 free chunk；`!heap -x` 查 chunk 詳情。
- `dt ntdll!_HEAP_ENTRY` 看的是 encoded 後的 raw 值，真實 header 要用 `!heap -x` 或手動 XOR `Heap->Encoding`。
- Heap grooming 三步驟：**填滿（spray）→ 打洞（punch）→ 觸發分配（trigger）**，每步都要用 `!heap -l` 驗 LFH bitmap 確認符合預期。
- LFH 有 slot randomization，pure spray 不能保證 deterministic slot assignment，需要 subsegment boundary 控制策略（Ch 28 細講）。

## 自我檢核

- [ ] 不看表，能說出「我想找大小 0x60 的 free chunk」應該用哪個指令、預期輸出格式是什麼
- [ ] 知道 `dt ntdll!_HEAP_ENTRY` 的 Size 欄和 `!heap -x` 的 Size 欄哪個是真實值、為什麼不同
- [ ] 能說出 heap grooming 的三步驟，以及每步結束後要用什麼指令驗證
- [ ] 面試被問「Windows heap exploit 為什麼需要 spray？和 glibc 有什麼差異？」能給出 2 分鐘的回答
- [ ] 知道 LFH randomization 是什麼、它打亂了什麼假設、粗略的繞法方向是什麼

## 延伸閱讀

### 論文 / 白皮書

- **[Windows 10 Segment Heap Internals](https://www.blackhat.com/docs/us-16/materials/us-16-Yason-Windows-10-Segment-Heap-Internals.pdf)** — Mark Vincent Yason，BH US 2016
  - **讀哪裡**：第 5 節的 debugging 技巧，包含用 WinDbg 觀察 Segment Heap 的指令（本章是 NT Heap，但觀察方法類似）
  - **前提知識**：本章 + Ch 16 Segment Heap

- **[Heap Feng Shui in JavaScript](https://www.blackhat.com/presentations/bh-europe-07/Sotirov/Presentation/bh-eu-07-sotirov-apr19.pdf)** — Alexander Sotirov，BH Europe 2007
  - **讀哪裡**：第 2–4 節的 spray / defrag / trigger 框架
  - **和本章的關聯**：heap grooming 這個名詞的原始論文，雖然是 IE 的 JavaScript heap，原則完全適用 Win32 heap
  - **前提知識**：基本 heap 概念（Ch 14–15）

### 官方文件

- **[WinDbg !heap command reference — Microsoft Learn](https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/-heap)**
  - **讀哪裡**：所有 `-s` / `-stat` / `-flt` / `-x` / `-v` 的選項說明
  - **和本章的關聯**：本章每個指令的「詳細選項」都在這裡，作為 reference 查表

### 部落格

- **[Corelan — Heap Spraying Demystified](https://www.corelan.be/index.php/2011/12/31/exploit-writing-tutorial-part-11-heap-spraying-demystified/)** — Peter Van Eeckhoutte
  - **讀哪裡**：整篇，spray 的邏輯 + 在 WinDbg 裡驗的步驟
  - **和本章的關聯**：grooming 那節的實作細節，配本章 WinDbg 指令一起讀最有感

- **[Connor McGarr — Heap Grooming](https://connormcgarr.github.io/)**
  - **讀哪裡**：site 上所有 heap 系列文，尤其是 LFH grooming 和 subsegment randomization
  - **前提知識**：本章 + Ch 28 之後

→ [練習 B — 用 WinDbg 追一次 LFH 分配，畫出 bucket 布局](./practice-b-lfh-tracing.md)
