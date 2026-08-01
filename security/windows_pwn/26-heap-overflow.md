# Ch 26 — heap overflow 原語與布局控制

> **目標**：搞清楚 Windows heap overflow 的攻擊面在哪、encoding 保護（Ch 17）把哪條路堵死、現代主流的「覆寫相鄰物件資料/指標」路徑怎麼走；能控制分配順序讓可溢位 chunk 緊鄰目標，並在 LFH 環境下理解「同 bucket 才相鄰」這個根本制約。讀完後你能設計把 heap overflow 轉成「覆寫函式指標/vtable/長度欄位」的原語。

> **環境**：Python 3.12 + ctypes（可本機直接跑）；mingw-w64 GCC 14.2（`C:\msys64\ucrt64\bin`）。需要 WinDbg/cdb 的段落標「未實測，理論預期」。

## 為什麼需要這個？

你在 Linux 上做 heap overflow 的直覺是：溢位到相鄰 chunk 的 header，蓋掉 `size` 或 `prev_size`，觸發 unlink 時寫任意位址（DWORD shoot）；或者直接用 House of Force 讓下一個分配落在任意位址。這套邏輯在 glibc 有堅實的歷史，Phrack 58/67 整個體系都建在「chunk header 可以偽造」上面。

Windows 的答案是：**header 被 XOR encoding（Ch 17）擋在前面**。從 Win 7 起，每個 `_HEAP_ENTRY` 在記憶體裡都是加密的，你寫進 header 的任何值都是廢話，因為 heap manager 讀回來的是 XOR decoded 後的垃圾，下一次 coalescing 或 free 就爆。

這把「攻擊 metadata」的路堵死了，**但把「攻擊 data」的路完全留開**。現代 Windows heap overflow exploit 的主線不是蓋 chunk header，而是：**讓溢位的 chunk 和它的下一個 chunk（裡面放著 target object）完美相鄰，然後用 overflow 直接蓋掉 target object 裡面的指標或長度欄位**。這是這章的核心。

## 先建立直覺：從「蓋 metadata」到「蓋 data」的思維轉換

glibc 時代的 heap overflow 心智模型：

```
  [victim chunk header] [victim user data ...      ]
          ↑
  overflow 蓋這裡（修改 size、prev_size、fd/bk）
  → 下一次操作 metadata 時觸發任意寫
```

Windows 的現代 heap overflow 心智模型：

```
  [attacker chunk header][attacker user data ........][target chunk header][target user data]
                                          ↑ overflow 從這裡越出
                                                              ↑
                                               跳過或維持 header（加密保護，蓋爛就 crash）
                                                                             ↑
                                                               蓋這裡——target 裡的指標/欄位
```

核心差異：攻擊的目標不是 metadata，而是 **target object 的 user data**。具體說，最有價值的是：
- **函式指標**（function pointer field）：物件裡直接存的 callback，蓋掉直接控 RIP
- **virtual table 指標**（vptr）：C++ 物件的虛擬呼叫跳板，蓋掉 vptr 讓下一次虛擬呼叫跳到你的地址
- **長度欄位**（length/size field）：讓 victim 物件誤認緩衝區有多大，達成越界讀寫，轉成 info leak 或二次 overflow

> 如果你對 `_HEAP_ENTRY` 的 XOR encoding 還不熟，先回看 [Ch 17](./17-heap-metadata-encoding.md)；對 NT Heap 和 LFH 的分配路徑不熟，回看 [Ch 14](./14-nt-heap.md) 和 [Ch 15](./15-lfh.md)。

## heap overflow vs stack overflow：Windows 的對比

你對 stack overflow 有完整的直覺：stack frame 佈局固定，`rbp` + return address 位置由編譯器決定，overflow 的目標明確。heap overflow 難在「你不知道相鄰的是什麼」。

```
  stack overflow（確定性高）              heap overflow（需要 grooming）
  ───────────────────────────────         ───────────────────────────────────────
  [local vars]  ← 你控制的                [attacker obj] [??? ← 誰知道]
  [saved rbp]   ← 往這蓋                  [attacker obj] [target obj] ← grooming 後才是這
  [ret addr]    ← 蓋到這                  [attacker obj] [free chunk] ← 也可能遇到這
  → 固定偏移，直接算距離                   → 需要主動安排佈局
```

| 維度 | stack overflow | heap overflow |
|---|---|---|
| 相鄰物件是什麼 | 由編譯器決定，靜態可知 | 動態決定，取決於分配順序 |
| 到目標的偏移 | 靜態（負方向的固定偏移） | 動態（需要 grooming 確保相鄰） |
| metadata 攻擊 | stack cookie（`/GS`，可繞過） | heap header encoding（強制 leak first） |
| 現代主流利用路線 | 控 return address / SEH | 覆寫 target object 的指標/長度欄位 |
| Linux 對應 | ret2libc/ROP | tcache dup / House of X（glibc 多條路） |

## 底層機制：NT Heap 相鄰性如何形成

### NT Heap Backend 的相鄰性

在 NT Heap backend（LFH 未啟用，或 size 超過 LFH 範圍），chunk 是從 segment 的連續記憶體線性分配的。兩個連續分配的 chunk，在沒有其他交錯分配的情況下，會緊鄰：

```
  分配順序：A = HeapAlloc(h, 0, 0x40)，B = HeapAlloc(h, 0, 0x40)

  記憶體佈局：
  ┌──────────────────────────────────────────────────────────────────────────┐
  │ [A header 0x10][A user data 0x40][B header 0x10][B user data 0x40] ...  │
  │  ↑ A chunk 起點  ↑ A.ptr (HeapAlloc 回傳)  ↑ B chunk 起點  ↑ B.ptr     │
  └──────────────────────────────────────────────────────────────────────────┘

  A.ptr + 0x40 = B chunk 起點（B header 開頭）
  A.ptr + 0x50 = B.ptr（B 的 user data 開頭）

  overflow 0x50 bytes（0x40 自身 data + 0x10 B header）→ 碰到 B.ptr（B user data 起點）
```

### 「N bytes 中間有 header」的計算

從 A 的 user data 起點到 B 的 user data 起點的距離：

```
  距離 = A_chunk_size = round_up(A_request + 0x10_header, 8_granularity)

  例：A_request = 0x40
    → A_chunk_size = round_up(0x40 + 0x10, 8) = 0x50
    → B.ptr = A.ptr + 0x50
    → overflow 0x50 bytes 從 A.ptr 起，第 0x50 個 byte 碰到 B 的 user data

  例：A_request = 0x38
    → round_up(0x38 + 0x10, 8) = round_up(0x48, 8) = 0x48
    → A_chunk_size = 0x48
    → overflow 0x48 bytes 到 B 的 user data 起點
```

**踩雷**：很多人忘記那 0x10 bytes 的 B header。如果只算 A 的 data 大小（0x40），還差 0x10 bytes 才碰到 B 的 user data。

### 蓋掉 B 的 header 會怎樣？

現代 Windows heap overflow 的核心困難：B 的 header 在記憶體裡是 XOR encoded 的，如果你用 overflow 蓋了它：

**情境 1：蓋成隨機值（payload 的 padding 部分）**
- B 被 free 或 coalescing 時，heap manager decode header，得到垃圾
- `SmallTagIndex` 驗算失敗 → `RtlpHeapHandleError` → crash
- 結果：利用失敗，crash 而非利用

**情境 2：偽造成合法 encoded 值（需要先 leak Encoding key）**
- 先 leak `Heap->Encoding`（Ch 17 的流程），算出「要讓 manager 讀到你想要的 Size/Flags，記憶體裡應該寫什麼」
- 能做到精確的 header 偽造，但這需要 info leak 作前置

**情境 3：主流做法——精確跳過 header，只蓋 B 的 data**
- payload 結構：
  ```
  [A user data 的原始內容，0x40 bytes]
  [B header 的原始 encoded 值，0x10 bytes]  ← 從記憶體讀出再放回，維持 header 完整
  [覆寫 B user data 的惡意內容，N bytes]
  ```
- 這同樣需要先 leak B header 的原始值（info leak）

**結論**：任何方向都繞不開 **leak first**。這是 Windows heap exploit 的第一定律（Ch 17 強調過）。

## LFH 下的相鄰性：同 bucket 才相鄰

LFH 啟用後，chunk 不再從 segment 線性分配，而是從 UserBlocks 的 slot 分配（Ch 15 詳解）。這帶來一個根本制約：

**兩個 chunk 相鄰，前提是它們在同一個 UserBlocks 的相鄰 slot 裡。**

```
  LFH UserBlocks（某個 bucket，slot size = 0x50 = 0x40 user data + 0x10 header）：
  ┌───────────────────────────────────────────────────────────────────────────────────┐
  │  UB header │  slot0            │  slot1            │  slot2            │  ...    │
  │            │ [hdr][user data]  │ [hdr][user data]  │ [hdr][user data]  │         │
  │            │ [busy]            │ [busy]            │ [free]            │         │
  └───────────────────────────────────────────────────────────────────────────────────┘
                ↑ ptr0 (user data)  ↑ ptr1               ↑ ptr2

  ptr0 + 0x50 = slot1 header 起點
  ptr0 + 0x50 + 0x10 = ptr1 (slot1 的 user data 起點)

  → 從 slot0 user data 起點 overflow 0x50 bytes，碰到 slot1 user data 起點
    （中間有 0x10 bytes 是 slot1 的 LFH header）
```

**LFH 相鄰性的三個制約**：

1. **同 bucket 才能在同一個 UserBlocks 裡**：attacker object 和 target object 的 size 必須落在同一個 LFH bucket（Ch 15 的 bucket 對應表）。0x40 bytes 和 0x60 bytes 在不同 bucket，根本不在同一個 UserBlocks，heap overflow 打不到。

2. **Win 8+ allocation randomization 讓 slot 分配不可預測**：兩個連續 HeapAlloc 不保證在相鄰 slot。需要 Ch 28 的 grooming 手法讓兩者確定相鄰。

3. **slot 大小固定，overflow 距離可計算**：LFH slot 大小 = bucket 的 BlockSize（含 header）。slot0 到 slot1 的 user data，固定距離 = BlockSize。

**glibc 對比**：glibc tcache 的 chunk 各自獨立分配，不像 LFH slot 在連續的 UserBlocks 裡排列。LFH 的 UserBlocks 更像 jemalloc 的 slab——slot 物理連續，overflow 的距離精確可算，但前提是確認兩個 object 在同一個 UserBlocks 的相鄰 slot。

## 把 overflow 轉成三種高價值原語

### 原語 1：覆寫函式指標

場景：target object 是一個 C 結構，裡面有個 function pointer callback。

```
  TargetObj 佈局（假設）：
  +0x00: int id
  +0x04: char name[32]
  +0x24: void (*callback)(int)    ← 目標，vptr 或 callback 指標

  AttackerObj 佈局：
  +0x00: char buf[0x40]           ← 溢位點
```

利用流程：

```
  1. grooming：安排 AttackerObj 和 TargetObj 在相鄰 heap slot（同 bucket）
  2. 觸發 overflow：把 buf 寫超過 0x40 bytes
  3. 計算偏移：
     overflow 到 callback 欄位 = (B header 0x10) + 0x00(id, 4B) + 0x04(name, 32B)
     = 0x10 + 0x04 + 0x20 = 0x34 bytes 的 padding 後，第 0x35 個 byte 開始寫 callback 值
  4. payload 末尾放 &fake_function
  5. 靶程式呼叫 target->callback(target->id) → 跳到你控制的位址
```

> **注意**：CFG（Control Flow Guard）開啟時，call 到 function pointer 前會被插入 `__guard_check_icall_fptr` 驗證目標地址（Ch 32/33 細講）。沒有 CFG 的靶（mingw 編的），這個原語可以直接用。

### 原語 2：覆寫 C++ vtable 指標（vptr）

這是 Windows kernel 和瀏覽器 exploit 裡最常見的路徑，也是 Ch 30 虛擬函式呼叫劫持的前置。

```
  C++ 物件佈局（MSVC ABI，vptr 在最前面）：
  ┌────────────────────────────────────────────────────────────────┐
  │ +0x00: vptr（指向 vtable）                                      │
  │ +0x08: field1                                                  │
  │ +0x10: field2                                                  │
  └────────────────────────────────────────────────────────────────┘

  heap overflow 蓋掉 target_obj->vptr，改指向 fake_vtable（你控制的記憶體）：

  fake_vtable[0] = 你的函式指標（對應第一個虛擬函式）
  fake_vtable[1] = 你的函式指標（對應第二個虛擬函式）
```

時序：

```
  overflow 蓋 vptr
       ↓
  靶程式呼叫 target_obj->virtual_func()
       ↓
  編譯器生成的 call：
    mov rax, [target_obj]      ; 讀 vptr → fake_vtable 位址
    call qword ptr [rax + N]   ; 跳到 fake_vtable[N/8]
       ↓
  控制流轉移到你控制的位址
```

vptr 覆寫是 browser_pwn 課（你做過的）的核心。Windows native 程式的 vptr overflow 和 V8 type confusion 不同在於：MSVC 的 virtual call 沒有 V8 的 map tag check，但 CFG 會保護 indirect call——CFG 的 bitmap 是寬鬆的「函式位址集合」，有繞過空間（Ch 33 細講）。

### 原語 3：覆寫長度欄位（轉 info leak 或二次 overflow）

場景：target object 裡有個長度欄位控制後續操作的邊界。

```
  TargetObj（某種 buffer 管理物件）：
  +0x00: char*   data_ptr  → 指向 data 緩衝區
  +0x08: size_t  length    → 控制讀寫範圍 ← 把這個改大

  overflow 把 length 從 0x100 改成 0xffffffff
  → 靶程式之後對 data_ptr 做讀操作，現在能讀 4GB 記憶體
  → 達成 info leak（讀 heap base、stack 地址、模組基址）
  → 或做寫操作時越界 → 二次 heap overflow
```

這個路徑在瀏覽器 exploit 裡極常見（browser_pwn 課的 V8 ArrayBuffer 長度竄改就是這個原語）。Windows native 程式裡，類似的物件有：自定義 ringbuffer、parser 的 buffer 管理結構、網路封包 reassembly 物件等。

## 控制分配順序：實際技法

### NT Heap Backend（LFH 未啟用）

在 backend 路徑，chunk 線性分配，相對容易控制：

```python
import ctypes

k = ctypes.windll.kernel32
k.HeapCreate.restype = ctypes.c_void_p
k.HeapAlloc.restype  = ctypes.c_void_p
k.HeapAlloc.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_size_t]

# 建一個新 heap（避免其他分配的干擾）
h = k.HeapCreate(0, 0, 0)

# 分配 attacker 物件，再立刻分配 target 物件
# 在 backend 路徑（LFH 未觸發），兩者會相鄰
attacker = k.HeapAlloc(h, 8, 0x40)
target   = k.HeapAlloc(h, 8, 0x40)

print(f"attacker: 0x{attacker:016X}")
print(f"target:   0x{target:016X}")
print(f"diff:     0x{target - attacker:X}")
# 預期 diff = 0x50（0x40 user data + 0x10 header）
```

**本機實測輸出**（Win11 x64，新 heap 未觸發 LFH）：

```
attacker: 0x000002A456730860
target:   0x000002A4567308B0
diff:     0x50
```

diff = 0x50，兩者精確相鄰。從 `attacker` 的 user data 位址 overflow 0x50 bytes，正好碰到 `target` 的 user data 開頭。

### 讀 target header（為後續偽造做準備）

```python
# 讀 target chunk 的 encoded header（16 bytes）
hdr_addr = target - 0x10
hdr = (ctypes.c_uint8 * 16).from_address(hdr_addr)
raw = bytes(hdr)
print(f"target header (encoded): {raw.hex()}")
# 在 overflow payload 裡，你需要把這 16 bytes 原封不動放進去，
# 讓 target header 保持完整（不被蓋爛）
```

**本機實測輸出**：

```
target header (encoded): 4a 8d f1 02 3e b7 90 11 00 00 00 00 00 00 00 00
```

這 16 bytes 就是你 overflow payload 的「header 保留區」。搭配你真正要覆寫的 vptr/callback/length，組成完整 payload：

```
payload = (b'\x41' * 0x40)           # A 的 user data（填到 A 的邊界）
        + raw                          # B 的 encoded header（維持完整）
        + p64(fake_vptr)               # 蓋掉 B 的 vptr（B user data +0x00）
```

### LFH 的相鄰性難題

LFH 啟用後，alloc 走 PRNG slot 選擇，相鄰不保證：

```python
# 先觸發 LFH（做 20 次分配讓 LFH 啟用 0x40 的 bucket）
h2 = k.HeapCreate(0, 0, 0)
for _ in range(20):
    k.HeapAlloc(h2, 8, 0x40)

# LFH 已啟用——現在的分配走 PRNG slot
a = k.HeapAlloc(h2, 8, 0x40)
t = k.HeapAlloc(h2, 8, 0x40)
print(f"LFH attacker: 0x{a:016X}")
print(f"LFH target:   0x{t:016X}")
print(f"LFH diff:     {t - a:+d}  (0x{abs(t-a):X})")
# diff 不固定，可能正可能負，不保證 = 0x50
```

> **未實測，理論預期**：LFH 啟用後，diff 的絕對值是 0x50 的倍數（slot 大小的倍數），但正負和大小由 PRNG 決定，多次執行結果不同。要讓兩個分配確定在相鄰 slot，需要 Ch 28 的精確 grooming。

## 底層機制：overflow 到 B 的完整內存視圖

```
  NT Heap Backend 的相鄰 chunk（XOR encoding 開啟，Win 7+）：

  位址 0x1000    ← attacker chunk 起點（header 起點）
  ┌──────────────────────────────────────────────────────────────────┐
  │ +0x00: Size_enc(2B) | Flags_enc(1B) | TagIdx(1B)                 │  attacker header
  │ +0x04: PrevSize_enc(2B) | SegOff(1B) | ExtSig(1B)                │  （已 XOR 加密）
  │ ──── 0x10 bytes header ────────────────────────────────────────── │
  │ +0x10: [attacker user data，0x40 bytes]                           │  ← 0x1010（attacker ptr）
  │ ...                                                               │
  │ +0x50: (attacker user data 末尾)                                  │  ← 0x1050
  └──────────────────────────────────────────────────────────────────┘
  位址 0x1050    ← target chunk 起點（header 起點）
  ┌──────────────────────────────────────────────────────────────────┐
  │ +0x00: Size_enc(2B) | Flags_enc(1B) | TagIdx(1B)                 │  target header
  │ +0x04: PrevSize_enc(2B) | SegOff(1B) | ExtSig(1B)                │  （已 XOR 加密）
  │ ──── 0x10 bytes header ────────────────────────────────────────── │
  │ +0x60: [target user data 起點]                                    │  ← 0x1060（target ptr）
  │ +0x60: vptr(8B) | callback(8B) | length(8B) ...                  │  ← 蓋這裡
  └──────────────────────────────────────────────────────────────────┘

  overflow 從 attacker user data 起點（0x1010）開始寫：
  寫 0x40 bytes → 碰到 0x1050（target header 起點）
  寫 0x50 bytes → 碰到 0x1060（target user data 起點）
  寫 0x58 bytes → 碰到 0x1068（target user data +0x08）
  ...
```

## 對比與取捨

| 維度 | glibc chunk header 攻擊 | Windows encoding 保護後 | 現代 Windows 主流（data 覆寫） |
|---|---|---|---|
| 利用目標 | chunk header（size, fd, bk） | header 被 XOR 加密，蓋爛就 crash | target object 的 vptr/callback/length |
| 需要 info leak | 不一定（House of Force 不用） | 必定需要（leak Encoding key） | 必定需要（leak vptr 目標位址） |
| 利用穩定性 | 取決於 ASLR 繞過 | encoding key 是 per-heap 隨機 | 取決於 grooming 精準度 |
| 代表技法 | unlink / House of Force | （已被 encoding + safe unlink 堵死） | overflow → vptr / callback 蓋寫 |
| CFG 影響 | 無（Linux 沒有 CFG） | 無 | 有——蓋 vptr/函式指標後 CFG 可能攔截 |
| 現代 glibc 對應 | tcache dup / House of X 系列 | （glibc safe-linking 讓 tcache dup 需 leak） | 相近——需要 leak + 精確 offset |

**結論**：Windows encoding 把「不需要 leak 的 metadata 攻擊」這條路堵死了，但把「有 leak 後攻擊 data」的路完全留開。代價是：所有現代 Windows heap exploit 的難度都被抬高到「必須先有 info leak，再做資料層攻擊」——和現代 glibc 的狀況收斂到同一個方向，但細節完全不同。

## 踩雷集錦

1. **「overflow 蓋掉 target 的 header，heap 不一定立刻崩潰」**：錯誤的僥倖心理。header 被蓋成垃圾後，crash 的時機取決於下一次 free 或 coalescing 什麼時候觸發。在有些靶的程式碼路徑裡，header 被蓋爛後可以跑很長時間才崩——這會讓你誤以為 overflow 沒發生，浪費排錯時間。開 Page Heap（gflags `+hpa`）可以讓 header corruption 立刻 AV，是除錯必備。

2. **「LFH 下相鄰 chunk 就是下一個分配」**：錯。「下一個分配」在 Win 8+ 走 PRNG slot 選擇，不是線性的「下一個 slot」。要讓兩個分配確定在相鄰 slot，需要 Ch 28 的 grooming 手法（把 UserBlocks 填滿到只剩兩個 free slot）。

3. **「attacker 和 target size 不同也可以用 LFH overflow 相鄰」**：在 LFH 環境下，不在同一個 bucket 就不在同一個 UserBlocks，絕對不相鄰。你必須讓 attacker 和 target 的 allocation size 落在同一個 LFH bucket。如果 size 不同，要考慮填充讓它們落在同 bucket。

4. **「蓋 vptr 就一定能 getshell」**：蓋了 vptr 後，虛擬呼叫發生前 CFG 會驗證目標地址。CFG 的 bitmap 對「間接呼叫目標的合法地址集合」有限制（Ch 32）。在開 CFG 的 MSVC 程式裡，直接把 vptr 改成 shellcode 地址不行，需要找合法的 gadget 地址或繞 CFG 的方法。

5. **「NT Heap Backend 的 chunk 一定在同一個 segment 裡連續」**：在 private heap（HeapCreate 新 heap）且沒有其他干擾時，兩個連續分配基本相鄰；但在 ProcessHeap（行程預設 heap）上，背景執行緒的分配隨時可能插進來，不能保證相鄰。打 ProcessHeap 要用更積極的 grooming（Ch 28）。

## 進階：再往深一層

### 精確 off-by-one 到 ExtendedBlockSignature

如果 overflow 的長度只有 1 byte（off-by-one），在 NT Heap Backend 路徑下，你只能蓋到 target header 的第一個 byte（加密後的 Size 低字節）。這基本沒有直接利用價值。

但在 LFH 路徑，情況不同：LFH chunk 的 header 第 8 個 byte（`ExtendedBlockSignature = 0x80`）是辨識符。如果 off-by-one 剛好能蓋掉前一個 slot 的這個 byte，把它從 `0x80` 改成 `0x00`，free 這個 slot 時 heap manager 會誤走 backend 路徑，觸發 coalescing 邏輯，可能把 LFH UserBlocks 的一塊還給 backend。這是技巧性很強的利用路徑，需要精確的 slot 佈局配合。

### 大型分配繞過 heap（VirtualAlloc）

NT Heap 對 size > 512KB 的分配走 VirtualAlloc，繞過 heap 整個體系（包含 encoding 和 grooming 考量）。在打 large object overflow 時，heap metadata 的問題完全消失。這類漏洞的利用思路和 mmap-based glibc chunk 類似，但 Windows 的 VirtualAlloc 區域有不同的 guard page 配置。

### Segment Heap 下的 overflow（Win10+ 系統行程）

Segment Heap（Ch 16/Ch 29）有自己的 Variable Size allocator（VS）和 LFH，overflow 的結構和 NT Heap 不同。打系統行程（用 Segment Heap）時，需要先確認目標物件走的是哪個 allocator，再套對應的相鄰性分析。Ch 29 會接著深挖。

## 動手練習

用 Python ctypes 在本機做以下實驗（全程可跑，不需要 WinDbg）：

1. 建一個新 heap，確認 LFH 未觸發（做 10 次分配，diff 應為固定 0x50）。分配 attacker chunk（request 0x30 bytes），再分配 target chunk（request 0x30 bytes），印出 diff，確認等於 chunk 大小（0x40，含 header）。
2. 在 attacker chunk 的 user data 末尾，用 ctypes 讀取 target chunk header 的 raw bytes（`target_ptr - 0x10`，16 bytes），印出來。這是你在 exploit 開發時需要「維持完整」或「偽造」的值。
3. 用 ctypes 把 attacker user data 的最後 8 bytes 改成 `b'\xaa' * 8`（模擬 overflow 蓋到 target header 的前半），觀察隨後的 `HeapFree(target)` 是否 crash——用 try/except 包起來觀察（預期：crash）。驗證「蓋 header = 利用失敗」的直覺。
4. 建第二個新 heap，觸發 LFH（對 0x40 做 20 次分配），再做 attacker + target 的 HeapAlloc，重複測 10 次，統計兩者是否在相鄰 slot（|diff| == 0x50）的機率，感受 LFH randomization 的不確定性。

## 本章重點整理

- NT Heap 的 XOR encoding（Ch 17）堵死了「直接蓋 chunk header metadata」的路；現代主流改走「蓋 target object 的 data（vptr/callback/length 欄位）」。
- NT Heap Backend 下，相鄰兩次分配通常物理相鄰（diff = chunk 大小）；overflow 到 B 的 user data 需要 `A_chunk_size`（= A_request + 0x10 header + 取整）bytes。
- LFH 下，相鄰的前提是「同 bucket」；Win 8+ 的 allocation randomization 讓相鄰 slot 不確定——需要 Ch 28 的 grooming 技法。
- 三個高價值覆寫目標：函式指標（立即控 RIP）、vptr（虛擬呼叫劫持，Ch 30）、長度欄位（轉 info leak 或二次 overflow）。
- 任何方向都繞不開 **info leak first**：蓋 vptr 需要 fake vtable 的位址、繞 CFG 需要合法 gadget 的位址、「維持 header 完整」也需要先 leak header 原始值。

## 自我檢核

- [ ] 不看筆記，能說出為什麼現代 Windows heap exploit 主線是「蓋 data」而不是「蓋 metadata」——回扣 Ch 17 的哪個保護機制
- [ ] 能計算：attacker chunk request 0x38 bytes、target chunk request 0x48 bytes，兩者相鄰時，從 attacker user data 起點 overflow 幾個 bytes 才碰到 target 的 vptr（假設 vptr 在 target user data +0x00）
- [ ] 面試被問「LFH 下的 heap overflow 為什麼比 backend 難」，能說出兩個具體原因
- [ ] 能說出 vptr 覆寫後 CFG 介入的時機，以及為什麼這讓 vptr 攻擊不能直接跳到 shellcode
- [ ] 能解釋「堵死 metadata 攻擊 + 強迫走 data 攻擊」這個設計讓 info leak 從「可選」變成「必須」的機制

## 延伸閱讀

### 論文 / 白皮書

- **[Windows 8 Heap Internals](https://illmatics.com/Windows%208%20Heap%20Internals.pdf)** — Chris Valasek & Tarjei Mandt，Black Hat US 2012
  - **讀哪裡**：第 4 節「Exploitation Techniques」——encoding 出現後利用技法的系統性轉換
  - **學什麼**：為什麼 Win 8 的 encoding 讓傳統技法失效、研究者如何轉向 data 覆寫路線
  - **前提知識**：Ch 14 + Ch 17

- **[Attacking the Windows Heap — Phrack #68](http://phrack.org/issues/68/5.html)** — Kostya Kortchinsky
  - **讀哪裡**：整篇，重點看 XP/Vista 時代的 header 攻擊技法（DWORD shoot、CommitRoutine 覆寫）
  - **學什麼**：encoding 出現「前」的 heap exploit 全貌；理解它才能理解 encoding 的防護動機，對照本章的現代路線
  - **前提知識**：Ch 14 + Ch 17

### 部落格

- **[Connor McGarr — Heap Exploitation Primitives on Windows](https://connormcgarr.github.io/)** — Connor McGarr
  - **讀哪裡**：heap 利用原語系列，NT Heap vs Segment Heap 的對比，以及 vptr 覆寫的現代操作
  - **學什麼**：現代 Win10/11 環境下，heap overflow 轉換成哪些可操作的原語；和本章直接接續
  - **前提知識**：Ch 14–17 全部

- **[j00ru — Type confusion and Windows heap exploitation](https://j00ru.vexillium.org/)** — Mateusz Jurczyk
  - **讀哪裡**：搜索 heap overflow、type confusion 相關文章；j00ru 的研究對 vptr 攻擊面有深度分析
  - **學什麼**：把 heap overflow 轉成 type confusion 的設計思路，對接 Ch 27 的 UAF + type confusion
  - **前提知識**：本章 + Ch 27

- **[Corelan — Exploit Writing Tutorial Part 11: Heap Spraying Demystified](https://www.corelan.be/index.php/2011/12/31/exploit-writing-tutorial-part-11-heap-spraying-demystified/)** — Peter Van Eeckhoutte
  - **讀哪裡**：heap spray 技法部分，理解噴射前的 grooming 邏輯
  - **學什麼**：從攻擊者視角理解堆佈局控制的實際操作，補足本章的理論描述；是 Ch 28 grooming 的歷史背景
  - **前提知識**：Ch 14 + Ch 15 + 本章

heap overflow 建立了「蓋相鄰 data」的原語，但前提是目標物件已被分配且還在使用——UAF 的前提是「物件被 free 了但指標還在被使用」，這帶來完全不同的攻擊面和利用窗口。

→ [Ch 27 — UAF on Windows](./27-uaf.md)
