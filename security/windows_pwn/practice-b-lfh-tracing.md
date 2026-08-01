# 練習 B — 用 WinDbg 追一次 LFH 分配，畫出 bucket 布局

> **目標**：親手觀察 NT Heap 從 backend 切換到 LFH 的過程，在 WinDbg 裡找出特定 size 的 LFH bucket 和 subsegment，畫出 UserBlocks 的 slot 佈局圖，並初步驗証 LFH 的 slot randomization 行為。這是 Part 4 精確 heap grooming 的前置實戰。

> **環境**：Windows 11 x64；WinDbg（WinDbgX 或 cdb + public symbols）；mingw-w64 GCC 14（本機已有，位於 `C:\msys64\ucrt64\bin`）。所有 WinDbg 輸出均標 **未實測，理論預期**，因本機 cdb 尚未就位。C 程式用 mingw 編，可在本機真跑。

## 背景：LFH 的觸發機制是什麼？

NT Heap 預設走 backend（freelist 管理的傳統配置器）。但如果同一個 size class 被分配超過一定次數（**閾值：18 次**，正確值依版本，以常見 Win10/11 為準），heap manager 判斷這個 size class 屬於「高頻小物件」，就把它轉移給 LFH（Low Fragmentation Heap）管理。

轉移是**單向且 per-size-class** 的：一旦 LFH 接管了 0x50 的分配，後續所有 0x50 的 `HeapAlloc` 都走 LFH bitmap 路徑，直到 process 退出。

LFH 內部的記憶體單位是 **subsegment**，每個 subsegment 有一個 `_LFH_HEAP_SUBSEGMENT` 結構和緊接在後的 `_HEAP_USERDATA_HEADER`（俗稱 UserBlocks）。UserBlocks 是一塊連續記憶體，切成等大的 slots，每個 slot 對應一個 chunk，用 bitmap 追蹤 busy/free 狀態。

本練習的核心任務就是：**用 WinDbg 把這個 subsegment 的具體位址和 slot 狀態照出來，手畫成佈局圖。**

## 任務規格

### 要寫的程式

檔名：`lfh_trigger.c`

程式邏輯：

```
1. 連續分配 32 個 size = 0x38 的 chunk（比 18 多，確保觸發 LFH）
2. 暫停（等你掛上 WinDbg 或按 Enter）
3. 再分配 32 個同 size 的 chunk（現在走 LFH）
4. 暫停（讓你觀察 LFH bitmap）
5. 釋放所有偶數 index 的 chunk（打洞）
6. 暫停（觀察 bitmap 有洞的狀態）
7. 再分配 8 個 chunk（觀察 LFH 怎麼填洞）
8. 最終暫停，讓你最後一次照相
```

要求：記錄每個 `HeapAlloc` 回傳的指標（至少前 64 個），計算相鄰指標的 stride（步長），觀察 LFH 分配是否按順序或隨機。

### 要完成的觀察任務

1. **用 `!heap -s` 找到 default heap handle**
2. **用 `!heap -stat` 確認 size 0x38 的 LFH 啟用狀態**（看 `LFH enabled` 那行）
3. **用 `!heap -l <handle>` 找到 bucket index**（對應 size 0x38 的 bucket）
4. **用 `dt ntdll!_LFH_HEAP_SUBSEGMENT <addr>` 展開 subsegment 結構**
5. **用 `dq <UserBlocks_addr> L20` dump UserBlocks 開頭**
6. **畫出 bitmap**：哪些 slot 是 busy，哪些是 free
7. **計算 slot stride**（兩個相鄰 slot 的位址差），驗算 = user_size + header_size

## 程式碼骨架

```c
/* lfh_trigger.c
   用途：觸發 LFH，配合 WinDbg 觀察 LFH bucket 佈局
   編譯（mingw）：gcc -o lfh_trigger.exe lfh_trigger.c
*/
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>

#define TARGET_SIZE  0x38     /* 目標分配大小（user data bytes） */
#define PHASE1_COUNT 32       /* 第一波：確保超過 LFH 閾值 */
#define PHASE2_COUNT 32       /* 第二波：LFH 已啟用時再分配 */

static void *ptrs[PHASE1_COUNT + PHASE2_COUNT];
static int   total = 0;

static void pause_and_show(const char *msg, HANDLE heap) {
    printf("\n=== %s ===\n", msg);
    printf("heap handle = 0x%p\n", heap);
    printf("Total allocated so far: %d\n", total);
    if (total > 0) {
        printf("Last ptr: 0x%p\n", ptrs[total - 1]);
        if (total > 1) {
            ptrdiff_t stride = (char*)ptrs[total-1] - (char*)ptrs[total-2];
            printf("Stride from last two: 0x%tx\n", stride);
        }
    }
    printf("WinDbg hint: !heap -stat -h 0x%p\n", heap);
    printf("Press Enter to continue...\n");
    getchar();
}

int main(void) {
    HANDLE heap = GetProcessHeap();
    printf("Default heap: 0x%p\n", heap);
    printf("PID: %lu\n", GetCurrentProcessId());
    printf("[Tip] Attach WinDbg: windbg -p %lu\n\n", GetCurrentProcessId());

    /* ─── Phase 1：觸發 LFH ─── */
    printf("[Phase 1] Allocating %d chunks of size 0x%x to trigger LFH...\n",
           PHASE1_COUNT, TARGET_SIZE);
    for (int i = 0; i < PHASE1_COUNT; i++) {
        ptrs[total++] = HeapAlloc(heap, HEAP_ZERO_MEMORY, TARGET_SIZE);
        if (!ptrs[total-1]) { fprintf(stderr, "HeapAlloc failed\n"); return 1; }
    }
    pause_and_show("Phase 1 done — LFH should now be enabled for this size", heap);

    /* ─── Phase 2：LFH 路徑分配 ─── */
    printf("[Phase 2] Allocating %d more chunks (LFH path)...\n", PHASE2_COUNT);
    int phase2_start = total;
    for (int i = 0; i < PHASE2_COUNT; i++) {
        ptrs[total++] = HeapAlloc(heap, HEAP_ZERO_MEMORY, TARGET_SIZE);
        if (!ptrs[total-1]) { fprintf(stderr, "HeapAlloc failed\n"); return 1; }
        printf("  [%2d] 0x%p", total-1, ptrs[total-1]);
        if (total > 1)
            printf("  stride=0x%tx", (char*)ptrs[total-1] - (char*)ptrs[total-2]);
        putchar('\n');
    }
    pause_and_show("Phase 2 done — observe LFH bitmap with !heap -l", heap);

    /* ─── Phase 3：打洞 ─── */
    printf("[Phase 3] Freeing even-indexed chunks (punching holes)...\n");
    for (int i = 0; i < total; i += 2) {
        HeapFree(heap, 0, ptrs[i]);
        ptrs[i] = NULL;
        printf("  freed ptrs[%d]\n", i);
    }
    pause_and_show("Phase 3 done — observe bitmap holes with !heap -l", heap);

    /* ─── Phase 4：填洞，觀察隨機性 ─── */
    printf("[Phase 4] Allocating 8 new chunks — watch where they land...\n");
    void *new_ptrs[8];
    for (int i = 0; i < 8; i++) {
        new_ptrs[i] = HeapAlloc(heap, HEAP_ZERO_MEMORY, TARGET_SIZE);
        printf("  new[%d] = 0x%p\n", i, new_ptrs[i]);
    }
    pause_and_show("Phase 4 done — did new chunks land in holes? Ordered or random?", heap);

    /* 清理 */
    for (int i = 0; i < total; i++)
        if (ptrs[i]) HeapFree(heap, 0, ptrs[i]);
    for (int i = 0; i < 8; i++)
        if (new_ptrs[i]) HeapFree(heap, 0, new_ptrs[i]);

    printf("Done. Heap cleaned up.\n");
    return 0;
}
```

**編譯**（本機 mingw，真實可跑）：

```bash
# 在 MSYS2 shell 或加了 C:\msys64\ucrt64\bin 的 PowerShell 裡：
gcc -o lfh_trigger.exe lfh_trigger.c -lkernel32
./lfh_trigger.exe
```

## 期望觀察（完整步驟 + 預期 WinDbg 輸出）

### Step 1：程式啟動，記下 heap handle 和 PID

程式啟動後會印出：

```
Default heap: 0x00000000007A0000    <- 這就是 _HEAP* 指標
PID: 12345
[Tip] Attach WinDbg: windbg -p 12345
```

另開一個 PowerShell，attach WinDbg：

```bat
windbg -p 12345
```

或用 WinDbgX GUI：File → Attach to process → 輸入 PID。

### Step 2：Phase 1 暫停時 — 確認 LFH 啟用

> **未實測，理論預期**

在 WinDbg 按下 `g`（continue）讓程式到第一個 pause。然後：

```
!heap -stat -h 0x00000000007A0000
```

預期輸出（節選）：

```
heap @ 00000000007A0000
group-by: TOTSIZE max-display: 20
    size     #blocks  total(bytes)  percentage
    ...
    0x38        0020       700          ...
    ...
    LFH enabled for sizes: 0x28, 0x38, ...   <- 0x38 應出現在這裡
```

關鍵：`0x38` 出現在 `LFH enabled for sizes` 那行。如果沒出現，分配次數還不夠，回 Phase 1 多分配幾個（18 次是最低閾值，實際可能稍高）。

### Step 3：Phase 2 暫停時 — 找 LFH bucket 和 subsegment

> **未實測，理論預期**

```
!heap -l 0x00000000007A0000
```

預期輸出（節選，找 size=0x38 的 bucket）：

```
...
LFH Bucket 6 (UserBlockSize 0x40, BlockCount 0x80):
    Subsegment @ 00000000007B5000 (UserBlocks @ 00000000007B5040)
      Free Slots: [2] [4] [6] [8] ...        <- 如果 Phase 1 有 free
      Busy Slots: [0] [1] [3] [5] ...
...
```

注意：

- **Bucket index 和 size 的對應**：NT Heap 的 LFH bucket table 把 size 0x01–0x80（以 8 bytes 為單位）對應到 bucket 0–127。size 0x38 = 56 bytes = 7 * 8，對應 bucket index 約為 6（以 8-byte granularity 為單位的 size/8）。精確 mapping 在 `_HEAP_BUCKET_COUNT_THRESHOLD` 表，用 WinDbg `dt ntdll!_HEAP FreeListUsage` 找。
- `UserBlockSize 0x40`：LFH 分配的每個 slot 大小 = user_size（0x38）+ header（0x08 or 0x10，版本相關），對齊後得到 0x40
- `UserBlocks @` 後的位址就是 bitmap 和 slot 資料的起點

找到 subsegment 位址後，展開結構：

```
dt ntdll!_LFH_HEAP_SUBSEGMENT 00000000007B5000
```

> **未實測，理論預期** 預期輸出（節選）：

```
ntdll!_LFH_HEAP_SUBSEGMENT
   +0x000 ListEntry        : _SLIST_ENTRY
   +0x008 UserBlocks       : 0x00000000`007b5040 _HEAP_USERDATA_HEADER
   +0x010 AggregateExchg   : _INTERLOCK_SEQ
   +0x014 BlockSize        : 0x40              <- slot 大小（bytes，含 header）
   +0x016 BlockCount       : 0x80              <- 總 slot 數
   +0x018 SizeIndex        : 0x6               <- bucket index
   +0x019 BucketIndex      : 0x6
   ...
```

### Step 4：dump UserBlocks，畫 slot 佈局

> **未實測，理論預期**

```
dq 00000000007B5040 L20
```

預期輸出（前幾個 QWORD，slot stride = 0x40）：

```
00000000`007b5040  00000000`007b5060 00000000`00000000   <- slot 0 header（encoded）
00000000`007b5050  cdcdcdcd`cdcdcdcd cdcdcdcd`cdcdcdcd   <- slot 0 user data（0xCD = HEAP_NO_ZERO 下的 uninitialized 標記，debug build）
00000000`007b5060  00000000`007b5080 00000000`00000001   <- slot 1 header
00000000`007b5070  00000000`00000000 00000000`00000000   <- slot 1 user data（HEAP_ZERO_MEMORY 清零）
...
```

> 注意：我們用了 `HEAP_ZERO_MEMORY`，所以 user data 應該全是 0。header 部分是 encoded 的值（Ch 17 說過），看起來像隨機數。

### Step 5：手畫 slot 佈局圖

根據 `!heap -l` 的 bitmap 輸出，手畫一張這樣的圖：

```
UserBlocks @ 0x007B5040
  stride = 0x40 bytes per slot
 ┌──────────────────────────────────────────────────────────────────┐
 │ slot 0  │ slot 1  │ slot 2  │ slot 3  │ slot 4  │ slot 5  │ ... │
 │ 007B5040│ 007B5080│ 007B50C0│ 007B5100│ 007B5140│ 007B5180│ ... │
 │  BUSY   │  BUSY   │  BUSY   │  BUSY   │  BUSY   │  BUSY   │ ... │
 └──────────────────────────────────────────────────────────────────┘
  ↑ Phase 2 分配完，全部 busy
```

Phase 3 打洞後：

```
 ┌──────────────────────────────────────────────────────────────────┐
 │ slot 0  │ slot 1  │ slot 2  │ slot 3  │ slot 4  │ slot 5  │ ... │
 │  FREE   │  BUSY   │  FREE   │  BUSY   │  FREE   │  BUSY   │ ... │
 └──────────────────────────────────────────────────────────────────┘
  ↑ free 了偶數 index，奇偶間隔洞
```

Phase 4 填洞後（觀察 new_ptrs[] 的值）：

```
 ┌──────────────────────────────────────────────────────────────────┐
 │ slot 0  │ slot 1  │ slot 2  │ slot 3  │ slot 4  │ slot 5  │ ... │
 │  BUSY*  │  BUSY   │  BUSY*  │  BUSY   │  FREE   │  BUSY   │ ... │
 └──────────────────────────────────────────────────────────────────┘
  ↑ BUSY* = Phase 4 新分配的；LFH randomization 下不一定按 slot 0, 2, 4... 順序填
```

**重要觀察**：Phase 4 的 8 個新 chunk 落在哪些 slot？是按順序（0, 2, 4...）填，還是隨機（例如 6, 0, 4, 2...）？這就是 LFH randomization 的直接觀測。

### Step 6：驗算 slot stride

從程式輸出（Phase 2 的 stride log）：

```
  [32] 0x007B5050  stride=0x40
  [33] 0x007B5090  stride=0x40
  [34] 0x007B50D0  stride=0x40
  ...
```

stride 應該等於 `UserBlockSize`（從 `dt ntdll!_LFH_HEAP_SUBSEGMENT` 的 `BlockSize` 欄）。這個值就是 **LFH slot 大小**，= user_size 向上對齊 + LFH chunk header。

**驗算**：`0x38`（user size）+ `0x08`（LFH header，不同版本可能是 0x10）= `0x40`，向上對齊到 heap granularity（8 bytes）= `0x40`。符合 stride 觀測。

## 卡住提示

### WinDbg 沒印出 _LFH_HEAP_SUBSEGMENT，說 symbol not found

先確認 `_NT_SYMBOL_PATH` 有設：

```powershell
$env:_NT_SYMBOL_PATH
# 應該顯示 srv*C:\symbols*https://msdl.microsoft.com/download/symbols
```

沒設的話：

```powershell
[Environment]::SetEnvironmentVariable("_NT_SYMBOL_PATH",
  "srv*C:\symbols*https://msdl.microsoft.com/download/symbols", "User")
```

設完要重開 WinDbg。第一次 symbols 下載需要連外網，下載後 `C:\symbols` 快取，之後離線可用。

### !heap -l 沒看到 size 0x38 的 LFH bucket

可能 LFH 還沒觸發。確認方式：`!heap -stat` 看 size 0x38 那行是否出現在 `LFH enabled` 裡。沒出現的話，讓程式繼續多分配幾次（或直接把 `PHASE1_COUNT` 改成 64 再重跑）。

### stride 不是 0x40，而是更大的值（例如 0x50 或 0x60）

LFH 的 chunk header 大小依版本（`_HEAP_ENTRY` 在 LFH 路徑的 union 形式），有些版本是 0x08，有些是 0x10。stride 應該是 user_size + header_size 的 **對齊後** 值。用 `dt ntdll!_LFH_HEAP_SUBSEGMENT <addr>` 的 `BlockSize` 欄為準，那是 manager 自己算的。

### Phase 4 的新 chunk 根本沒落在洞裡，而是在一個全新的位址

LFH 可能開了第二個 subsegment。這發生在第一個 subsegment 的 bitmap 快滿了（busy 比率 > 某閾值）時。用 `!heap -l` 確認這個 size 有幾個 subsegment，找到你的新 chunk 屬於哪個 subsegment。

### Phase 2 的 stride 不規則，不是固定的 0x40

這就是 LFH slot randomization 的表現。LFH 不是按 bitmap 從頭 scan 找 free slot，而是用一個隨機起始 index。Phase 2 的 stride 不穩定是正常的——你觀察到的就是隨機化。記錄下來，這是重要的觀察結果。

## 完整參考解答（含預期 WinDbg 輸出）

**先認真嘗試，再看這裡。** 把你的觀察記在筆記裡，再對照。

<details>
<summary>點開參考解答</summary>

### 編譯和執行（本機真實輸出）

```bat
REM 在 MSYS2 UCRT64 shell
gcc -o lfh_trigger.exe lfh_trigger.c -lkernel32
./lfh_trigger.exe
```

預期程式輸出（示意，位址每次不同）：

```
Default heap: 0x00000000007A0000
PID: 14256
[Tip] Attach WinDbg: windbg -p 14256

[Phase 1] Allocating 32 chunks of size 0x38 to trigger LFH...

=== Phase 1 done — LFH should now be enabled for this size ===
heap handle = 0x00000000007A0000
Total allocated so far: 32
Last ptr: 0x00000000007A2BC0
Stride from last two: 0x58             <- Phase 1 走 backend，stride 不固定
WinDbg hint: !heap -stat -h 0x00000000007A0000
Press Enter to continue...
```

### Phase 1 暫停時的 WinDbg 操作

> **未實測，理論預期**

```
0:000> !heap -stat -h 0x7a0000
heap @ 00000000007a0000
group-by: TOTSIZE max-display: 20
    size     #blocks  total(bytes)  percentage
    0x38        0020       700          12.4%
    0x28        0010       280           4.9%
    ...
    LFH enabled for sizes: 0x28, 0x38
```

`0x38` 已出現在 LFH enabled 清單。

### Phase 2 暫停時的 WinDbg 操作

> **未實測，理論預期**

```
0:000> !heap -l 0x7a0000
...
LFH Bucket 6 (UserBlockSize 0x40, BlockCount 0x80):
    Subsegment @ 00000000007b5000
      UserBlocks @ 00000000007b5040
      Bitmap (first 16 slots):
        [0]=1 [1]=1 [2]=1 [3]=0 [4]=1 [5]=1 [6]=0 [7]=1
        [8]=1 [9]=0 [a]=1 [b]=1 [c]=0 [d]=1 [e]=1 [f]=0
        ... (1=busy, 0=free)
```

注意 bitmap 裡有散落的 `0`（free）——這些是 LFH randomization 選 slot 時跳過的位置，或者 Phase 1 釋放的中間 chunk。你的 Phase 2 分配（64 個）應該把大部分 slot 填滿。

```
0:000> dt ntdll!_LFH_HEAP_SUBSEGMENT 00000000007b5000
ntdll!_LFH_HEAP_SUBSEGMENT
   +0x000 ListEntry        : _SLIST_ENTRY { 0x00000000`007b6000 }
   +0x008 UserBlocks       : 0x00000000`007b5040 _HEAP_USERDATA_HEADER
   +0x010 AggregateExchg   : _INTERLOCK_SEQ
   +0x014 BlockSize        : 0x40           <- slot 大小 0x40 bytes
   +0x016 BlockCount       : 0x80           <- 總共 128 slots
   +0x018 SizeIndex        : 0x6            <- 對應 size class 0x38
```

```
0:000> dq 00000000007b5040 L10
00000000`007b5040  00000000`007b5060 00000000`00000001  <- slot 0: header + flag
00000000`007b5050  00000000`00000000 00000000`00000000  <- slot 0: user data（zero）
00000000`007b5060  00000000`007b5040 00000000`00000001  <- slot 1: header
00000000`007b5070  00000000`00000000 00000000`00000000  <- slot 1: user data
00000000`007b5080  00000000`007b50a0 00000000`00000000  <- slot 2: header（free）
00000000`007b5090  00000000`00000000 00000000`00000000  <- slot 2: user data
...
```

注意 free slot（bitmap 裡 `0`）的 header 和 busy slot 有差異（`SubSegmentCode` 不同）。

### Phase 3 暫停時：bitmap 有洞

> **未實測，理論預期**

```
0:000> !heap -l 0x7a0000
...
LFH Bucket 6 (UserBlockSize 0x40, BlockCount 0x80):
    Subsegment @ 00000000007b5000
      Bitmap (first 16 slots):
        [0]=0 [1]=1 [2]=0 [3]=1 [4]=0 [5]=1 [6]=0 [7]=1    <- 奇偶相間的洞
        [8]=0 [9]=1 [a]=0 [b]=1 [c]=0 [d]=1 [e]=0 [f]=1
```

漂亮的奇偶間隔 bitmap——這就是 punch holes 的效果。

### Phase 4：觀察 LFH randomization

Phase 4 新分配的 8 個 chunk 的位址（預期示意）：

```
  new[0] = 0x007B50C0    <- slot 4（不是按順序從 slot 0 開始！）
  new[1] = 0x007B5200    <- slot 8
  new[2] = 0x007B5040    <- slot 0
  new[3] = 0x007B5280    <- slot 10
  new[4] = 0x007B5140    <- slot 6
  new[5] = 0x007B5100    <- slot 2（...繼續隨機）
  new[6] = 0x007B5180    <- slot ...
  new[7] = 0x007B5240    <- slot ...
```

觀察：新 chunk **不按 slot 0, 2, 4... 的順序填洞**，而是按 LFH 內部的隨機 bitmap scan 順序。這就是 randomization 的直接觀測。

**關鍵結論**：LFH randomization 打破了「free slot K → 下一次分配回 slot K」的假設。grooming 時要靠「控制 subsegment boundary、填滿整個 subsegment」的技巧，而不是依賴按順序填洞（Ch 28 細講）。

### 完整 slot 佈局圖（Phase 4 後）

```
UserBlocks @ 0x007B5040   BlockSize = 0x40   BlockCount = 0x80
 slot  address      state    owner
─────────────────────────────────────────────────
  0    007B5040     BUSY*    Phase 4 new[2]
  1    007B5080     BUSY     Phase 2 ptrs[1]
  2    007B50C0     BUSY*    Phase 4 new[5]
  3    007B5100     BUSY     Phase 2 ptrs[3]
  4    007B5140     BUSY*    Phase 4 new[0]
  5    007B5180     BUSY     Phase 2 ptrs[5]
  6    007B51C0     BUSY*    Phase 4 new[4]
  7    007B5200     BUSY     Phase 2 ptrs[7]
  8    007B5240     BUSY*    Phase 4 new[1]
  9    007B5280     BUSY     Phase 2 ptrs[9]
  10   007B52C0     BUSY*    Phase 4 new[3]
  ...
  16   007B5440     FREE     (Phase 4 只分配了 8 個，之後的洞還在)
  17   007B5480     BUSY     Phase 2 ptrs[17]
  18   007B54C0     FREE
  ...
─────────────────────────────────────────────────
BUSY* = Phase 4 新分配；BUSY = Phase 2 留下的；FREE = 剩餘的洞
```

</details>

## 驗收清單

完成後，確認你能回答以下所有問題：

- [ ] `!heap -stat` 指令看完，我知道 size 0x38 有沒有啟用 LFH（`LFH enabled` 那行）
- [ ] `!heap -l` 指令找到了 size 0x38 對應的 LFH bucket index 和 subsegment 位址
- [ ] `dt ntdll!_LFH_HEAP_SUBSEGMENT` 展開後，我能說出 `BlockSize` 和 `BlockCount` 是什麼意思
- [ ] 手畫的 slot 佈局圖包含：subsegment 起址、每個 slot 的位址和 stride、busy/free 狀態
- [ ] 我觀察到 Phase 4 的新 chunk 是否按順序落洞，並說出這說明了 LFH 的什麼特性
- [ ] 能計算 slot stride = user_size + header_size（對齊後），並和 `!heap -l` 的 UserBlockSize 對照
- [ ] 能說出：如果我的 exploit 要讓 victim chunk 落在 slot K，LFH randomization 對我造成什麼困難

## 延伸挑戰：觀察 LFH randomization 的統計性質

不滿足只做一次觀察？試試這個延伸：

### 挑戰 1：填滿一整個 subsegment，觸發第二個 subsegment

把 Phase 2 的分配數改成 128（`PHASE2_COUNT = 128`，超過 `BlockCount = 0x80`），觀察：

- 第一個 subsegment 填滿後，第 129 個分配去哪了？
- `!heap -l` 裡這個 size 應該出現第二個 subsegment
- 第二個 subsegment 的 `UserBlocks` 位址和第一個差多遠？

### 挑戰 2：統計 randomization 的分布

修改程式，在 Phase 4 分配 32 個新 chunk（把所有洞填滿），記錄每個 chunk 落在哪個 slot index。計算：

- 平均要分配幾次才能填 slot 0？
- slot 0 被第一個分配命中的機率是多少？

（理論上：如果有 32 個 free slot，每次隨機選，命中 slot 0 的機率 = 1/32；但 LFH 的實際 randomization 算法比「純隨機選」更複雜，實測結果可能不符合均勻分布。）

### 挑戰 3：觀察 LFH 和 backend 的 stride 差異

把 `TARGET_SIZE` 改成 `0x500`（超過 LFH 上限），同樣 spray 32 個。觀察：

- backend 分配的 stride 是否固定？
- `!heap -flt s <size>` 找 free chunk 的格式和 LFH 有什麼不同？
- backend 的 first-fit vs LFH 的 randomized bitmap：哪個對 grooming 更好控制？

## 自我檢核

- [ ] 不看筆記，能從 zero 說出「LFH 觸發的條件」和「觸發後分配行為如何改變」
- [ ] 面試被問「Windows LFH 的 subsegment 是什麼」，能用一分鐘解釋清楚並說出 bitmap 的角色
- [ ] 能說出 `!heap -stat`、`!heap -l`、`!heap -x` 三個指令各自回答什麼問題
- [ ] 理解為什麼「free slot K → 下一次分配必然落在 slot K」這個假設在 LFH 下是錯的
- [ ] 知道 LFH randomization 和 glibc 的 tcache（先入先出）在攻擊者角度有什麼不同含義

完成這個練習，你已經能用 WinDbg 看穿 LFH 的內部佈局，並且有了「randomization 存在、需要 subsegment boundary 控制」的第一手認識。Part 4 的 heap grooming 實戰（Ch 28）就建立在這個基礎上。

→ [Ch 19 — stack buffer overflow](./19-stack-overflow.md)
