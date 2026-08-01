# Ch 17 — heap metadata encoding 與完整性檢查

> **目標**：搞清楚 NT Heap 的 `_HEAP_ENTRY` XOR encoding 是什麼、為什麼要有、用 exploit 怎麼繞過；同時把 Windows 的這套 metadata 保護和 glibc 的 safe-linking 放到同一張比較表上，讓你一眼看懂兩個體系的共同目標（防偽造）與不同實作細節。

## 為什麼需要這個？

NT Heap 在每個 chunk 前都放了一個 `_HEAP_ENTRY` header，記錄 chunk 大小、前 chunk 大小、flags 等等。在沒有任何保護的版本（Windows XP 早期）裡，這些欄位是明文的。攻擊者溢位到相鄰 chunk 的 header，把大小改成一個假值，就能讓 `RtlFreeHeap` 誤算 chunk 邊界，把相鄰欄位當成 freelist 指標寫入——這就是經典的 `DWORD shoot`。

Windows XP SP3 / Vista 開始 Microsoft 在 heap manager 裡陸續加了：header encoding（XOR 加密）、checksum、safe unlink 指標驗證、以及後來 LFH 的 subsegment 位址比對。這些加起來不是為了讓攻擊「不可能」，而是把「可以不 leak 直接打 metadata」的路全部堵死，**強制你先 leak 某個祕密值才能偽造**。

這個思路和 glibc 2.32 引入的 safe-linking（tcache `next` 指標 XOR）一模一樣。兩個體系獨立演化，卻走到同一個設計結論。

## 先建立直覺

想像 freelist 是一張名片交換清單：每張名片上都貼著一個貼紙，貼紙用你只有你知道的 key 加密了，分發前 XOR 一次，收到後 XOR 回來驗真偽。偽造者想捏造一張假名片，必須知道那個 key——而 key 放在 heap 物件裡，不 leak 就不知道。

```
  NT Heap encoding 概念圖
  ─────────────────────────────────────────────────
  真實 _HEAP_ENTRY                 存在記憶體裡的值
  ┌──────────────┐                 ┌──────────────┐
  │ Size   = 0x8 │  XOR Encoding   │ 0x8 ^ key[0] │
  │ PvOffset=0x1 │ ─────────────►  │ 0x1 ^ key[1] │
  │ Flags  = 0x1 │                 │ 0x1 ^ key[2] │
  └──────────────┘                 └──────────────┘
          ↑                                ↑
     heap manager 裡看到的            記憶體 dump 裡看到的
  ─────────────────────────────────────────────────
```

不知道 `Encoding` key，你寫進 header 的值被 XOR 後就是垃圾。`RtlpHeapHandleError` 一跑 checksum 驗算就爆。

## `_HEAP_ENTRY` 結構與 XOR encoding

### 結構定義

`_HEAP_ENTRY` 在 x64 上是 16 bytes（`sizeof(_HEAP_ENTRY) == 0x10`）：

```
> **未實測，理論預期** — 在裝有 public symbols 的 WinDbg 裡跑：
> dt ntdll!_HEAP_ENTRY
```

典型欄位（以 64-bit NT Heap 為準；LFH 路徑下這個結構有 union 覆蓋，見下節）：

```
+0x000 Size             : Uint2B    <- 以 heap granularity（8 bytes）為單位的當前 chunk 大小
+0x002 PreviousSize     : Uint2B    <- 前一個 chunk 大小（空閒時才有意義）
+0x004 SmallTagIndex    : UChar     <- checksum 欄位（下面細講）
+0x005 Flags            : UChar     <- HEAP_ENTRY_BUSY(1), HEAP_ENTRY_EXTRA_PRESENT(2), ...
+0x006 UnusedBytes      : UChar     <- 未使用的位元組數（alignment padding）
+0x007 ExtendedBlockSignature : UChar
```

> 確切偏移因版本而異。自己裝好 symbols 後 `dt ntdll!_HEAP_ENTRY` 對照。

### Encoding 機制

`_HEAP` 物件（heap handle 指向的地方）有一個欄位：

```
+0x07c Encoding         : _HEAP_ENTRY  <- 這就是 XOR key，也是一個 _HEAP_ENTRY 大小的值
```

分配 / 釋放 chunk 時，heap manager 把 raw header XOR `Heap->Encoding` 後再寫入記憶體；讀回時再 XOR 一次：

```c
/* 偽碼，非真實 source */
static inline void encode_entry(HEAP *heap, HEAP_ENTRY *entry) {
    /* entry 視作 uint64_t[2]，分別 XOR encoding key 的兩個 qword */
    ((uint64_t*)entry)[0] ^= ((uint64_t*)&heap->Encoding)[0];
    ((uint64_t*)entry)[1] ^= ((uint64_t*)&heap->Encoding)[1];
}
```

記憶體裡你看到的值 = 真實 header XOR Encoding。**你不知道 Encoding，你沒辦法逆向算出「要寫什麼才能讓 manager 讀到你想要的 header」。**

### Encoding key 從哪來？

`Heap->Encoding` 在 heap 初始化時由 `RtlpInitializeHeap` 填入，用的是 `RtlpHeapGenerateRandomValue64()`——最終走到 `BCryptGenRandom` 或 RDRAND 指令（視版本）。所以：

- 每次 process 啟動 key 都不同
- 每個 heap handle 的 key 都不同（default heap vs custom heap）
- key 存在 `_HEAP` 物件的偏移 `+0x07c`（對版本），**只要你能 leak 這個位址的 0x10 bytes，你就拿到 key**

這就是為什麼 Windows heap exploit 的第一步幾乎都是「先 leak heap base → 讀 Encoding」。

## SmallTagIndex：chunk checksum

`SmallTagIndex`（偏移 +4 的 1 byte）是一個簡單的完整性 checksum，計算方式：

```c
SmallTagIndex = Size_low ^ PreviousSize_low ^ Flags;
/* 實際是 encoded header 的三個位元組的 XOR，細節依版本 */
```

`RtlpHeapFreeHeap` 在處理 chunk 前會驗算這個 checksum。偽造 header 時如果三個欄位的 XOR 算錯，這一關就爆：

```
ntdll!RtlpHeapHandleError
```

**這個 checksum 沒辦法單獨偽造**——因為你寫進記憶體的值已經先被 encoding XOR 過了；你必須同時知道 encoding key 才能讓 checksum 驗算正確。它是第二道閘，encoding 是第一道閘。

## LFH 的 subsegment 完整性檢查

LFH chunk 使用不同的 header union（`_HEAP_ENTRY` 覆蓋）：

```
LFH chunk header
+0x000 SubSegmentCode   : Ptr64  <- 編碼的 subsegment 指標
+0x008 AggrExchg        : union  <- 分配 bitmap
```

`SubSegmentCode` 儲存的是 **subsegment 指標 XOR chunk 位址本身 XOR 一個常數**（類似 safe-linking 的概念，加了 chunk address 防止純 pointer 替換）：

```
SubSegmentCode = (subsegment_ptr ^ chunk_ptr ^ RtlpLFHKey)
```

`RtlpLFHKey` 是另一個 per-process 的隨機值（比 `Heap->Encoding` 更全域）。分配 LFH chunk 時 manager 驗算這個值是否指回合法的 `_LFH_HEAP_SUBSEGMENT`。

這讓 LFH 的 house-of-force 風格攻擊（讓 manager 誤以為某個任意位址是合法 subsegment）必須同時 leak `RtlpLFHKey` 才能成立。

```
LFH subsegment 驗算流程
─────────────────────────────────────────────
chunk 被 free：
  1. 讀 chunk_ptr->SubSegmentCode
  2. XOR chunk_ptr ^ RtlpLFHKey
  3. 得到 subsegment_ptr
  4. 驗 subsegment_ptr 指向的 UserBlocks 是否合法
  5. 通過 → 從 bitmap 清 bit，chunk 回池
  失敗 → RtlpHeapHandleError
─────────────────────────────────────────────
```

## Safe Unlink 指標驗證

在 free 一個 backend chunk 時（非 LFH 路徑），heap manager 要把 chunk 插回 freelist doubly-linked list。Windows Vista+ 加了驗算（對應 glibc 的 safe unlinking）：

```c
/* 偽碼 */
HEAP_FREE_ENTRY *entry = ...;
/* freelist 雙向連結 */
if (entry->Flink->Blink != entry ||
    entry->Blink->Flink != entry) {
    RtlpHeapHandleError();
}
```

這和 glibc 的：

```c
if (__builtin_expect (FD->bk != P || BK->fd != P, 0))
    malloc_printerr("corrupted double-linked list");
```

**幾乎一模一樣**。兩個體系都是「把 fd/bk 或 Flink/Blink 指回自己」這個不變量當作驗證。

差異在：glibc 的 safe unlink 出現在 2003 年左右（glibc 2.3.4），Windows 的版本是 Vista（2007）。你熟悉的 House of Spirit 要在 fake chunk 的 fd/bk 都填好，Windows 對應的繞法也必須讓 Flink/Blink 能撐過驗算。

## 底層機制：完整性檢查全圖

```
 RtlFreeHeap(hHeap, 0, ptr) 呼叫鏈
 ──────────────────────────────────────────────────────────────────
 ① 先判斷走哪條路（LFH or Backend）
       │
 ② LFH 路徑：
       ├─ 讀 SubSegmentCode
       ├─ XOR chunk_ptr ^ RtlpLFHKey  → subsegment_ptr
       ├─ 驗 subsegment_ptr → UserBlocks 合法性
       └─ [失敗] → RtlpHeapHandleError

 ③ Backend 路徑：
       ├─ decode header：raw XOR Heap->Encoding → real header
       ├─ 驗 SmallTagIndex checksum
       ├─ 驗 Flink->Blink == entry && Blink->Flink == entry
       └─ [失敗] → RtlpHeapHandleError

 ④ RtlpHeapHandleError
       ├─ 呼叫 RtlpHeapRaiseError
       ├─ 觸發 heap corruption exception (STATUS_HEAP_CORRUPTION)
       └─ 預設行為：raise exception → WER → 程式 crash
 ──────────────────────────────────────────────────────────────────
```

注意 **`RtlpHeapHandleError` 不是必死的**。如果程式有個 top-level exception handler 攔截 STATUS_HEAP_CORRUPTION，理論上還是可以繼續跑。但在現代 Win11 上，process mitigation policy 通常把 heap corruption 設成「直接 terminate，不走 exception handler」（參見 `SetProcessMitigationPolicy(ProcessHeapFlags, ...)`）。

## 對比 glibc：兩個體系的共同目標

| 機制 | Windows NT Heap | glibc 2.32+ |
|---|---|---|
| **header 保護** | `_HEAP_ENTRY` XOR `Heap->Encoding`（16 bytes key） | 無對應（glibc 沒有全域 header encryption） |
| **freelist 指標保護** | Flink/Blink 雙向驗算（Vista+） | safe unlink：`FD->bk==P && BK->fd==P`（2003+） |
| **tcache-style 指標加密** | LFH `SubSegmentCode` XOR chunk_ptr XOR `RtlpLFHKey` | tcache `next` XOR `(ptr >> 12)`（2.32+） |
| **checksum** | `SmallTagIndex`（1 byte XOR） | 無直接對應 |
| **key 的生命週期** | per-heap（Encoding）+ per-process（LFH key） | per-process（`tls_rand`，存 tcache ptr 的 page 高位） |
| **key 在哪裡** | `_HEAP+0x07c`（可 leak） | `fs:[0x30]+0x18` 附近（可 leak） |
| **偽造難度** | 必須先 leak Encoding + LFH key | 必須先 leak heap address 的高位（地址 >> 12） |

**共同結論**：兩個體系都不依賴「metadata 藏好讓你看不見」，而是「metadata 可以看，但看到的是加密後的，你沒有 key 就算不出要寫什麼」。這是「加密防偽造」而不是「隱藏防偽造」。

從攻擊者角度：

- glibc tcache 只要 leak heap 任一位址的高 12 bits，就能解算 safe-linking key（因為 key 是 `heap_ptr >> 12`，就在那個位址本身裡）
- Windows 的 Encoding 是隨機的，你要 leak `_HEAP` 物件本身才能拿到 key——所以 Windows heap exploit 通常需要「heap base leak」作為前置

## 在 WinDbg 裡驗證 Encoding key

> **未實測，理論預期** — 裝好 symbols + cdb 後可驗：

```
cdb -c "dt ntdll!_HEAP Encoding; q" -pn target.exe
```

預期輸出（格式示意）：

```
ntdll!_HEAP
   ...
   +0x07c Encoding : _HEAP_ENTRY
      +0x000 Size             : 0xa3b2      <- 隨機，每次不同
      +0x002 PreviousSize     : 0x7f4c
      +0x004 SmallTagIndex    : 0x91
      +0x005 Flags            : 0x02
      ...
```

拿到這 16 bytes，就是 XOR key。接著找一個 busy chunk 的 header：

```
!heap -x <chunk_ptr>          <- 讓 WinDbg decode 並顯示真實 header
dq <chunk_ptr>-0x10 L2        <- 看記憶體裡存的 encoded bytes
```

手動 XOR 兩者，就能驗證「記憶體值 XOR key = 真實 header」。這是理解 encoding 最直接的方法。

## guard page 與 security cookie 補充

NT Heap 還有兩個額外機制值得一提：

**guard page**：large chunk（預設 > 512KB）分配後，heap manager 會在尾端多映射一個 `PAGE_NOACCESS` 的 guard page。越界讀寫會觸發 access violation，而不是靜悄悄污染相鄰 chunk。注意這只對「大分配」有效；堆上一般的小 chunk overflow 不受 guard page 保護（glibc 也是類似情況）。

**security cookie**：這是 stack 的 `/GS` cookie，**不是 heap 機制**。heap 裡沒有 stack cookie 對應物，不要混淆。有些文件把 `SmallTagIndex` 稱作「heap cookie」，但它本質是 header checksum，和 stack `/GS` 是完全不同層次的機制。

## 踩雷集錦

1. **「encoding 是 compile-time 決定的，我可以靜態分析找到 key」**：錯。key 是 runtime 的 CSRNG，每次跑程式都不同，靜態看 binary 拿不到。你需要 runtime leak。

2. **「SmallTagIndex 是 one-byte hash，暴力破解看看」**：暴力破解 1 byte（256 種）確實可以，但 encoding key 整個是 16 bytes 的隨機值，暴力不可行。而且你要讓 SmallTagIndex 正確，前提是你知道 encoding 後的 Size/Flags——所以問題還是繞回來：你需要 Encoding。

3. **「LFH chunk 沒有 _HEAP_ENTRY，所以沒有 encoding」**：LFH 有自己的 union，`SubSegmentCode` 就是 LFH 版本的 encoding。不是沒有保護，是用不同機制。

4. **「safe unlink 只驗一次，可以用 off-by-one 偷改 Flink 再觸發 coalesce 繞過」**：確實存在理論路徑（讓 Flink->Blink 正好還是指向 entry），但這需要非常精確的 layout 控制——這就是 Ch 28 heap grooming 的功課。知道有洞，但難度在布局。

5. **「Heap->Encoding 在固定偏移 0x07c，對所有版本都對」**：只對常見的 Win10/Win11 版本，未必對 Server 版或舊版。用 WinDbg `dt ntdll!_HEAP` 自己查。

## 進階：再往深一層

### _HEAP_ENTRY 的 ExtendedBlockSignature

`+0x007 ExtendedBlockSignature` 這個欄位在 backend chunk 啟用「extended block」時使用，記錄 chunk 是否有 `_HEAP_ENTRY_EXTRA`。Segment Heap（Ch 16）裡這個欄位的語意又完全不同。打 Segment Heap 要單獨研究其 header 格式，不要套 NT Heap 的 encoding 假設。

### RtlpLFHKey 在哪裡

`RtlpLFHKey` 是 `ntdll` 的全域變數（在 `.data` 段），位址隨 ASLR 移動，但 ntdll 的 ASLR 在 process 生命週期內是固定的。一旦你有任意讀原語，讀 `ntdll!RtlpLFHKey` 就能拿到 LFH key。或者，如果你能 leak ntdll 基址（常見的 info leak，Ch 31 細講），就能算出 `ntdll!RtlpLFHKey` 的絕對位址。

### Heap Hardening（Windows Defenders）

現代的 Windows 有個叫做 Heap Hardening 的進一步加固（不同於上面講的 encoding/checksum）：heap manager 在 freelist entry 的 metadata 中額外插入指標 cookie，類似 glibc 的 `tcache_perthread_struct` guard。這個機制在 Win11 某些更新後出現，公開文件不多，主要靠逆向 ntdll 確認。留意你打的版本。

## 動手練習

用 Python ctypes 驗算 encoding：

```python
# 目標：從 process memory 讀 _HEAP.Encoding，手動 decode 一個 chunk header
# 步驟：
# 1. GetProcessHeap() 拿 heap handle（就是 _HEAP* 指標）
# 2. 在 offset 0x7c 讀 16 bytes（Encoding key）
# 3. 分配一個 chunk，chunk_ptr - 0x10 就是 encoded header
# 4. XOR encoded header 和 Encoding key，驗算 SmallTagIndex

import ctypes, struct

ntdll = ctypes.WinDLL("ntdll")
kernel32 = ctypes.WinDLL("kernel32")

# heap handle
heap = kernel32.GetProcessHeap()
print(f"heap handle (= _HEAP* addr): 0x{heap:016x}")

# 讀 Encoding（offset 0x7c，16 bytes）
# 注意：直接 ReadProcessMemory 自己的記憶體；在自身 process 裡用指標也行
encoding_addr = heap + 0x7c
buf = (ctypes.c_uint8 * 16)()
kernel32.ReadProcessMemory(
    kernel32.GetCurrentProcess(),
    ctypes.c_void_p(encoding_addr),
    buf, 16, None
)
encoding = bytes(buf)
print(f"Encoding key: {encoding.hex()}")

# 分配一個 chunk，讀它的 encoded header
ptr = kernel32.HeapAlloc(heap, 0, 0x20)
header_addr = ptr - 0x10
hbuf = (ctypes.c_uint8 * 16)()
kernel32.ReadProcessMemory(
    kernel32.GetCurrentProcess(),
    ctypes.c_void_p(header_addr),
    hbuf, 16, None
)
encoded_hdr = bytes(hbuf)
print(f"Encoded header: {encoded_hdr.hex()}")

# XOR decode
decoded = bytes(a ^ b for a, b in zip(encoded_hdr, encoding))
print(f"Decoded header: {decoded.hex()}")

size_units   = struct.unpack_from("<H", decoded, 0)[0]
prev_size    = struct.unpack_from("<H", decoded, 2)[0]
small_tag    = decoded[4]
flags        = decoded[5]
print(f"  Size (in 8-byte units): 0x{size_units:x} => {size_units * 8} bytes")
print(f"  PreviousSize: 0x{prev_size:x}")
print(f"  SmallTagIndex: 0x{small_tag:x}")
print(f"  Flags: 0x{flags:x}  (BUSY={flags & 1})")

# 驗 checksum
expected_tag = decoded[0] ^ decoded[2] ^ decoded[5]  # Size_lo ^ PrevSize_lo ^ Flags（近似）
print(f"  Expected tag (approx): 0x{expected_tag & 0xff:02x}, actual: 0x{small_tag:02x}")
```

> 上面的 checksum 算法是近似值（真實計算涉及更多欄位細節），但方向正確。跑完這段，你就親手看到 encoding 機制是怎麼工作的。

## 本章重點整理

- `_HEAP_ENTRY` 在記憶體裡是 **encoded 的**（XOR `Heap->Encoding`），不是明文。
- LFH chunk 用 `SubSegmentCode` XOR chunk_ptr XOR `RtlpLFHKey` 保護 subsegment 指標，概念和 glibc safe-linking 類似但更複雜。
- Backend free 路徑驗算 Flink->Blink 和 Blink->Flink，和 glibc safe unlink 邏輯幾乎相同。
- 攻擊 metadata 的前提永遠是：**先 leak 才能偽造**——這是現代 heap exploit 的第一條定律。

## 自我檢核

- [ ] 不看筆記，能說出 `_HEAP_ENTRY` 在記憶體裡的值和「真實 header」是什麼關係
- [ ] 知道 `Heap->Encoding` 存在 `_HEAP` 的哪個偏移、怎麼 leak
- [ ] 能解釋 `SmallTagIndex` 怎麼算，以及它為什麼不能單獨繞過（必須先過 encoding）
- [ ] 能說出 LFH `SubSegmentCode` 的計算公式，以及它比 glibc safe-linking 多了什麼
- [ ] 不看表，能說出 Windows encoding 和 glibc safe-linking 三個共同點、兩個差異點
- [ ] 面試被問「為什麼 Windows heap exploit 通常需要 info leak？」能給出兩段回答

## 延伸閱讀

### 論文 / 白皮書

- **[Heap Exploitation on Windows](https://www.blackhat.com/docs/us-16/materials/us-16-Yason-Windows-10-Segment-Heap-Internals.pdf)** — Mark Vincent Yason，Black Hat US 2016
  - **讀哪裡**：第 2–3 節的 NT Heap 部分，encoding 機制有 pseudocode 級別的分析
  - **和本章的關聯**：本章 encoding 機制的主要參照來源，包含 `Heap->Encoding` 和 LFH key 的逆向驗證
  - **前提知識**：Ch 14–16 的 NT Heap / LFH 基礎

- **[Windows 8 Heap Internals](https://illmatics.com/Windows%208%20Heap%20Internals.pdf)** — Chris Valasek & Tarjei Mandt，BH US 2012
  - **讀哪裡**：第 3 節 encoding 加入的歷史背景，以及 LFH 的 `SubSegmentCode` 分析
  - **和本章的關聯**：把這個機制的「為什麼出現」講得最清楚的一篇
  - **前提知識**：Win7 NT Heap 基礎（Ch 14）

### 部落格

- **[Corrupting the Windows NT Heap](https://sploitfun.wordpress.com/2015/06/10/corrupting-the-windows-nt-heap/)** — sploitfun
  - **讀哪裡**：整篇，encoding 攻擊前後的 exploit 技法演進
  - **和本章的關聯**：encoding 出現「前」的 DWORD shoot 和「後」的 leak-first 思路，對照讀效果最好

- **[glibc safe-linking source code（2.32）](https://sourceware.org/git/?p=glibc.git;a=commit;h=a1a486d70ebcc47a686ff5846875eacad0940e41)**
  - **讀哪裡**：`malloc/malloc.c` 裡的 `PROTECT_PTR` / `REVEAL_PTR` 巨集定義
  - **和本章的關聯**：把 glibc 和 Windows 的「指標加密防偽造」放在同一個 commit 裡對照，一眼看出設計哲學的相同與不同

→ [Ch 18 — 用 WinDbg !heap 觀測與 heap grooming 基礎](./18-windbg-heap-grooming.md)
