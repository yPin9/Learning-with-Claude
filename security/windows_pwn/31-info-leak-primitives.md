# Ch 31 — info leak 原語大全

> **目標**：理解 info leak 在現代 Windows exploit 裡為什麼是必要條件（ASLR、cookie、vtable 位址），能分辨 Windows 常見的洩漏面（未初始化記憶體、OOB read、物件欄位洩漏、error 回傳值、格式化字串），知道洩漏什麼（模組基址 / heap 位址 / stack 位址 / security cookie），以及如何把洩漏的位址轉成可靠的 ROP gadget 或 vtable 偏移計算；能建立「info leak 資料流圖」，串起後續 vtable 劫持和 ROP。

## 為什麼需要這個？

現代 Windows exploit 的最大瓶頸不是找到 bug，而是**把 bug 變成可靠的控制流劫持**。ASLR（Address Space Layout Randomization）讓幾乎所有有用的地址在執行期才能確定，這意味著：

- ROP gadget 的位址？不知道——直到 leak 出 module 基址。
- vtable 劫持後跳哪裡？不知道——直到 leak 出目標函式位址。
- heap spray 要噴到哪裡？不知道——直到 leak 出 heap 基址。
- Stack cookie？不知道——直到 leak 出 stack 上的 cookie 值。

info leak（資訊洩漏）不是附加題，是所有後續技法的**前提**。

和 Linux 的比較：Linux ASLR 的強度和 Windows 相近，info leak 在兩邊都是必要的。但 Windows 多了幾個特有的洩漏面（例如 COM 物件欄位、NT Heap 的某些 metadata），也多了幾個額外的祕密需要洩漏（security cookie、GS cookie、CFG bitmap 位址）。

## 先建立直覺：ASLR 的強度和洩漏的作用

Windows 的 ASLR（Vista+）：

```
  每次開機 / 行程建立時，以下 region 的基址都隨機化：
  - 可執行模組（.exe, .dll）的 ImageBase
  - Heap 的基址
  - Stack 的基址
  - ntdll.dll 的基址（系統 DLL）

  ASLR 的強度（Windows 10 x64）：
  - Image ASLR：17 bits 的隨機性（64KB 對齊 × 2^17 = 8GB 範圍）
  - HEASLR（High Entropy ASLR，/DYNAMICBASE /HIGHENTROPYVA）：
    → 64-bit 行程：image 可放在 0x10000 到 0x7FFF'FFFF'0000 的任意位置
    → 實際隨機 bits 約 19–24 bits（視模組對齊要求）
  - Heap ASLR：heap 基址有隨機 offset
  - Stack ASLR：每次函式呼叫的 rsp 有隨機 offset
```

一個 info leak 的作用：

```
  洩漏一個指標值（例如 vtable 指標）
  → 知道這個指標所在模組的基址：
    base_addr = leaked_ptr - known_rva (靜態分析得到的 RVA)
  → 有了 base_addr，所有 ROP gadget 的位址都能算出來：
    gadget_addr = base_addr + gadget_rva
```

一次 leak 可能解鎖整個 module 的所有內容——這就是為什麼攻擊者花大量精力找和放大 info leak primitive。

## 資料流圖：info leak 如何餵給後續步驟

```
  ┌──────────────────────────────────────────────────────────────────┐
  │  漏洞觸發 / exploit 初期                                          │
  │  - OOB read / 未初始化記憶體 / UAF dangling read                  │
  └─────────────────────────┬────────────────────────────────────────┘
                            ↓ 拿到一個位址（pointer leak）
  ┌──────────────────────────────────────────────────────────────────┐
  │  位址識別階段                                                      │
  │  Q1: 洩漏的指標指向哪個 region？                                   │
  │      0x7FFF'xxxx'xxxx → 大概率是 module / heap / stack            │
  │      0x000000xx'xxxx  → 大概率是 heap（低位址 region）            │
  │  Q2: 指針是哪個模組的 symbol？                                     │
  │      靜態分析（IDA / Ghidra / dumpbin）找已知 symbol 的 RVA        │
  └─────────────────────────┬────────────────────────────────────────┘
                            ↓ 計算 module 基址 / heap 基址
  ┌──────────────────────────────────────────────────────────────────┐
  │  位址計算階段                                                      │
  │  module_base = leaked_vtable_ptr - vtable_rva                    │
  │  heap_base   = leaked_heap_ptr   - known_offset_within_heap      │
  │  rop_gadget  = module_base + gadget_rva                          │
  │  cookie_addr = stack_base + cookie_offset（若 leak stack）        │
  └─────────────────────────┬────────────────────────────────────────┘
                            ↓ 把計算好的位址填進 exploit payload
  ┌──────────────────────────────────────────────────────────────────┐
  │  後續利用階段                                                      │
  │  - vtable 劫持（Ch 30）：fake vtable 的 slot 填入計算好的 gadget  │
  │  - ROP chain（Ch 33/34）：gadget 位址已確定                       │
  │  - heap spray：heap 基址確定，spray 目標位址計算準確              │
  │  - cookie 繞過：知道 stack cookie 值，溢位後填入正確 cookie        │
  └──────────────────────────────────────────────────────────────────┘
```

## 洩漏面 1：未初始化記憶體

未初始化記憶體是最「意外」的洩漏面——程式分配了記憶體但沒有清零，就把它當作「乾淨」的資料結構使用。

### 原理

```
  HeapAlloc(heap, 0, size)  ← 注意：沒有 HEAP_ZERO_MEMORY flag
  → 回傳的 chunk 裡可能有上一個 allocation 殘留的資料
  → 如果之前這個 chunk 存放過指標（例如某個物件的欄位），
    那個指標值還在記憶體裡
  → 程式把這個 chunk 當新物件使用，讀 +0x00 以為是某個欄位，
    但其實讀到的是殘留的 vptr / heap 指標
```

### Windows 的具體行為

```python
import ctypes

k = ctypes.windll.kernel32
k.GetProcessHeap.restype = ctypes.c_void_p
k.HeapAlloc.restype = ctypes.c_void_p
k.HeapFree.restype = ctypes.c_bool

h = k.GetProcessHeap()

# 分配一個 chunk，填入已知值，然後 free
p1 = k.HeapAlloc(h, 0, 0x40)  # 不填 HEAP_ZERO_MEMORY
sentinel = (ctypes.c_uint64 * 8).from_address(p1)
sentinel[0] = 0xDEADBEEFCAFEBABE  # 填入識別值
sentinel[1] = 0x4141414141414141
k.HeapFree(h, 0, p1)

# 重新分配同大小的 chunk（希望拿回同一個 slot）
p2 = k.HeapAlloc(h, 0, 0x40)
data = (ctypes.c_uint64 * 8).from_address(p2)
print(f"p1 = 0x{p1:016X}, p2 = 0x{p2:016X}")
print(f"data[0] = 0x{data[0]:016X}")  # 是 0xDEADBEEFCAFEBABE 還是 0？
print(f"data[1] = 0x{data[1]:016X}")
k.HeapFree(h, 0, p2)
```

> **注意**：這段腳本在 NT Heap 路徑下（python.exe 通常是 NT Heap）執行。實際結果取決於 heap 的 free/alloc 路徑是否有 fill（Page Heap 會 fill，release 環境通常不會）。在 release 環境，預期 data[0] 仍然是 `0xDEADBEEFCAFEBABE`（殘留）。

### 典型漏洞模式

```c
// 漏洞：HeapAlloc 分配 IPC 訊息緩衝區，不清零，
// 直接把緩衝區傳給 client（包含 kernel 指標殘留）
BYTE* buf = (BYTE*)HeapAlloc(heap, 0, MSG_SIZE);
// 忘了 memset(buf, 0, MSG_SIZE)
CopyToClient(client, buf, MSG_SIZE);

// 結果：client 從 buf 讀到 kernel heap 指標（之前某個物件的欄位）
// → kernel ASLR bypass
```

## 洩漏面 2：OOB Read（越界讀）

OOB read 是最常見的 info leak primitive——如果程式對陣列/buffer 的邊界沒有嚴格驗證，讀取超出分配範圍的記憶體。

### 典型模式

```c
// 漏洞：index 沒有 bounds check
void read_field(MyObj* arr, int index) {
    // 沒有 if (index < MAX) 的 check
    return arr[index].value;  // 如果 index 超出 arr 的範圍，讀到相鄰物件
}
```

在 heap 上，「相鄰物件」可能是：

```
  ┌──────────────────────────────────────────────────────────────────┐
  │ arr[0] ... arr[N-1]（合法範圍）                                   │
  ├──────────────────────────────────────────────────────────────────┤  ← OOB 邊界
  │ chunk header（NT Heap：16 bytes _HEAP_ENTRY / Segment Heap：8B）  │  ← 讀到 size 等 metadata
  ├──────────────────────────────────────────────────────────────────┤
  │ 下一個物件的 user data                                            │
  │ +0x00 vptr（如果是 C++ 物件）→ leak vtable → leak module base    │
  │ +0x08 其他指標欄位           → leak heap 位址                    │
  └──────────────────────────────────────────────────────────────────┘
```

**常見的 heap 佈局操控**（讓目標物件緊跟在 OOB 物件後面）：

```
  exploit 前期的 grooming（heap feng shui）：
  1. spray 大量同 bucket 的物件（填滿 UserBlocks / VS subsegment）
  2. 分配 OOB 物件和目標物件，讓它們落在相鄰 slot
  3. OOB read 到目標物件的 vptr → leak module base
```

### Windows 特有的 OOB：Kernel → User 洩漏

Windows 核心的某些 API（GDI、NtQuerySystemInformation 等）會回傳結構體給使用者，如果填充不足（欄位之間的 padding 沒有清零），這個 padding 裡可能有 kernel 指標。

典型例子（歷史漏洞，已修復，僅作說明）：

```
> **注意**：以下是教育性說明，基於已公開的歷史研究。
>
> NtQuerySystemInformation(SystemBasicInformation, ...) 的某些版本
> 回傳結構體中的 padding bytes 含有 kernel 堆疊殘留資料
> → 使用者可以讀到 kernel stack 位址
> → kernel ASLR bypass（CVE 年代：Win 7 時期，現已修復）
```

## 洩漏面 3：物件欄位洩漏（pointer 殘留在可讀欄位）

C++ 物件的某些欄位本身就是指標，如果這個欄位可以從 API 讀出來，就洩漏了指標值。

### COM 物件的 vptr 洩漏

COM 物件普遍使用 C++ 虛擬函式（`IUnknown::QueryInterface/AddRef/Release`）。如果你能取得 COM 物件的指標（`IUnknown*`），讀 `*(IUnknown**)ptr` 拿到的就是 vtable 指標：

```
  IUnknown* p = CoCreateInstance(...);
  void* vtable_ptr = *(void**)p;  // p 的 offset 0 是 vptr
  // vtable_ptr 在某個 DLL 的 .rdata 裡
  // → DLL 基址 = vtable_ptr - known_rva（IDA 靜態分析得到）
  // → 有了 DLL 基址，所有 ROP gadget 位址都算得出來
```

**前提**：你能讀到 COM 物件的記憶體（UAF dangling read，或設計成可讀的欄位）。如果程式的 API 允許你讀取 COM 物件的某個欄位（例如錯誤回報裡含 object 指標），這就不需要記憶體讀漏洞。

### 物件的 linked list 指標洩漏

很多 Windows 物件（kernel 的 _LIST_ENTRY、heap 的 FreeLists、event 的 waiters 鏈）用雙向鏈表管理。如果你能讀到鏈表節點的 Flink/Blink，就洩漏了相鄰物件的位址——進而算出 heap 佈局或 kernel struct 的位址。

```
  _LIST_ENTRY：
  +0x00 Flink : 指向下一個 _LIST_ENTRY 的指標
  +0x08 Blink : 指向上一個 _LIST_ENTRY 的指標

  如果 Flink 指向某個 kernel struct 的欄位，
  知道該欄位的 struct offset，就能算出 struct 的基址
```

## 洩漏面 4：Error 回傳值側洩

某些 Windows API 在失敗時的錯誤訊息或異常資訊裡洩漏了內部狀態，包含位址。

### `GetLastError` / 結構化異常

```
> **未實測，理論預期**：以下是概念性說明，具體行為取決於 Windows 版本。

某些 API 失敗時把 NTSTATUS 放進 EXCEPTION_RECORD.ExceptionInformation[]
EXCEPTION_RECORD.ExceptionInformation 是一個 ULONG_PTR 陣列（最多 15 個元素）
→ 如果 API 把「失敗的記憶體位址」填進 ExceptionInformation
→ 呼叫者在 SEH handler 裡讀 ExceptionInformation 就洩漏了位址
```

### NTSTATUS 擴展資訊

某些 NTSTATUS code 伴隨著 `IoStatusBlock.Information`（kernel driver API）或 `TEB.LastStatusValue`，這些欄位有時包含位址資訊（例如失敗的記憶體存取位址）。

在 CTF 場景，題目設計者有時故意在 error path 留下洩漏管道（例如「讀取失敗時回傳讀到多少 bytes」，而 bytes 的值和記憶體內容有關）。

## 洩漏面 5：格式化字串（Windows 上較少見）

格式化字串漏洞在 Linux 上很常見（`printf(user_input)` 洩漏 stack 或 GOT），在 Windows 上：

- `printf` 的行為和 Linux 相同——`%p` 洩漏 stack 位址，`%x%x...` 洩漏 stack 內容
- 但 Windows 程式更常用 `wsprintf`、`StringCchPrintf`（STRSAFE API）等，不直接暴露格式化字串給使用者
- Win32 API 的 `FormatMessage` 不是格式化字串漏洞（它的格式字元是 `%1` 到 `%9`，不讀 stack）

**在 Windows CTF 中**，格式化字串漏洞通常出現在 C++ 程式直接使用 `printf`/`sprintf` 的場景，概念和 Linux 完全相同：

```
  對 printf(user_input) 送 "%p %p %p %p %p %p %p %p"
  → 讀 RSP 往高位址方向的 stack 內容
  → 其中可能有：呼叫者的 return address（在某個 module 裡）
                 local variables 的指標
                 保存的 rbp
  → 從 return address 可以算出 module 基址
```

> Windows x64 calling convention：前 4 個整數/指標 argument 在 rcx/rdx/r8/r9，第 5 個開始在 stack。格式化字串漏洞讀的是 RSP + shadow space 之後的 stack。

## 洩漏什麼：四類洩漏目標

### 目標 1：Module 基址（最常用）

**用途**：計算 ROP gadget 位址、計算 CFG bitmap 位址、計算 security cookie 位址。

**洩漏方法**：任何指向 module 的 .text / .rdata / .data 的指標。最常見的是 **vtable 指標**（在 .rdata）——任何 C++ 物件的 vptr 洩漏都能算出 module 基址。

```
  # Python 計算 module 基址（示意）
  leaked_ptr  = 0x7fff_a1f3_8050     # 從 OOB read 取得的 vtable slot 值
  vtable_rva  = 0x3_8050             # 靜態分析（IDA/Ghidra）找到的 RVA
  module_base = leaked_ptr - vtable_rva
  print(f"module base: 0x{module_base:016X}")

  # 驗算：module_base 通常對齊到 0x10000（Image 的對齊單位）
  assert module_base % 0x10000 == 0, "alignment check failed"
```

### 目標 2：Heap 基址

**用途**：計算 heap 上已知物件的位址（對 heap spray 很重要）、計算 NT Heap 的 cookie / Segment Heap 的 VS context XOR key。

**洩漏方法**：任何在 heap 上的物件的指標。常見的洩漏路徑：

```
  heap 指標的常見殘留位置：
  1. 未初始化 heap 分配的殘留資料（前一個 chunk 的欄位）
  2. NT Heap free chunk 的 Flink/Blink（雙向鏈表指標，在 chunk user data 的前 16 bytes）
  3. VS free chunk 的 _RTL_BALANCED_NODE（樹節點指標，在 chunk user data 的前 24 bytes）
  4. COM 物件的 reference 指標（如果物件之間有互相 ref）
```

### 目標 3：Stack 位址

**用途**：計算 stack cookie 的精確位址（在覆寫 cookie 前先洩漏）、計算 return address 的位址（ROP chain 的跳轉計算）。

**洩漏方法**：

- 格式化字串讀 stack
- 未初始化 stack 變數（函式沒有清零 local buffer，把它傳出去）
- 某些 API 的 stack frame 位址洩漏（特定 exception handling 路徑）

```python
# 示意：stack 位址通常比 heap 低（Windows 預設 stack 在低地址區）
# 在 64-bit Windows，stack 通常在 0x000000xx'xxxx0000 範圍
# heap 通常在 0x00007fxx'xxxxxxxx 範圍（HEAPRandomization）
# module 通常在 0x00007fff'xxxxxxxx 範圍（Image 的高地址端）
```

### 目標 4：Security Cookie（`/GS` cookie）

**用途**：繞過 Stack Buffer Overrun Protection（Ch 18）——如果你要做 stack overflow，在覆寫 return address 之前要用正確的 cookie 值填回 cookie 的位置，否則函式返回時 cookie check 失敗，程式直接呼叫 `__report_gsfailure` 終止。

**洩漏方法**：

```
> **未實測，理論預期**：/GS cookie 是 per-process 的隨機值（在行程啟動時初始化），
> 儲存在 binary 的 .data 段（`__security_cookie` 符號）。

> 如果你有任意讀（例如 OOB read 讓你讀到 .data 段的位址），
> 就能讀出 __security_cookie 的值。
> 然後在 stack overflow 的 payload 裡把 cookie 位置填回正確值。

> stack 上的 cookie = __security_cookie XOR RSP（有些版本是這個公式，有些直接用 __security_cookie）
> 以你目標的 MSVC 版本的實際行為為準（/GS 的實作在不同版本略有差異）。
```

## 位址計算：從洩漏到有用位址

### 工作流程

```
  Step 1：靜態分析，找「洩漏的指標」是什麼 symbol 的位址
  ──────────────────────────────────────────────────────────
  用 IDA Pro / Ghidra / dumpbin 開目標 binary
  → 確認洩漏的指標屬於哪個 symbol
  → 取得該 symbol 的 RVA（Relative Virtual Address，相對於 module 基址的 offset）

  Step 2：runtime 拿到 leaked_ptr，計算 module_base
  ──────────────────────────────────────────────────────────
  module_base = leaked_ptr - known_rva

  Step 3：計算目標位址（ROP gadget / vtable / import 等）
  ──────────────────────────────────────────────────────────
  target_addr = module_base + target_rva

  Step 4：驗算 alignment
  ──────────────────────────────────────────────────────────
  assert module_base % 0x10000 == 0  # Image 的最小對齊單位是 0x10000（64KB）
  （某些大型 binary 可能有更大的對齊，但 0x10000 是最低保障）
```

### Python 工具：從 vptr 洩漏到 gadget 位址

```python
# 示意：假設我們從 OOB read 取得了一個 vtable 指標
# 並且靜態分析告訴我們 vtable 的 RVA

def calc_base(leaked_ptr, symbol_rva):
    """從洩漏指標和已知 RVA 算 module 基址"""
    base = leaked_ptr - symbol_rva
    if base % 0x10000 != 0:
        print(f"[!] base 0x{base:016X} 不對齊 0x10000，可能 RVA 算錯")
        return None
    return base

def calc_gadget(base, gadget_rva):
    """從 module 基址和 gadget RVA 算 gadget 位址"""
    return base + gadget_rva

# 範例（假設值，不是真實目標）：
leaked_vtable_slot = 0x7fff_a1f3_8050  # 從 OOB read 取得
vtable_rva         = 0x38050           # 靜態分析找到的 vtable RVA
rop_gadget_rva     = 0x12345           # 靜態找到的 gadget RVA

base = calc_base(leaked_vtable_slot, vtable_rva)
if base:
    gadget = calc_gadget(base, rop_gadget_rva)
    print(f"module base: 0x{base:016X}")
    print(f"gadget addr: 0x{gadget:016X}")
```

### 洩漏的位址指向哪個模組：判斷方法

洩漏一個指標之後，如何判斷它是哪個模組的？

```python
import ctypes

def get_module_of_addr(addr):
    """查詢位址屬於哪個模組（本機可執行）"""
    k = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi

    # 取得當前行程所有模組的基址
    hmodules = (ctypes.c_void_p * 256)()
    cb_needed = ctypes.c_ulong()
    psapi.EnumProcessModules(
        k.GetCurrentProcess(),
        ctypes.byref(hmodules),
        ctypes.sizeof(hmodules),
        ctypes.byref(cb_needed)
    )
    n_modules = cb_needed.value // ctypes.sizeof(ctypes.c_void_p)

    buf = ctypes.create_unicode_buffer(260)
    for i in range(n_modules):
        hmod = hmodules[i]
        if hmod:
            # 取得模組路徑
            psapi.GetModuleFileNameExW(k.GetCurrentProcess(), hmod, buf, 260)
            # 取得模組大小（從 IMAGE_DOS_HEADER）
            # 簡化：如果 addr 在 [hmod, hmod + 64MB) 範圍內，認為屬於這個模組
            if hmod <= addr < hmod + 0x4000000:
                return f"0x{hmod:016X} {buf.value}"
    return "unknown"

# 示意：查詢 ntdll 的某個函式位址屬於哪個模組
ntdll = ctypes.windll.ntdll
addr = ctypes.cast(ntdll.NtAllocateVirtualMemory, ctypes.c_void_p).value
print(f"NtAllocateVirtualMemory at 0x{addr:016X}")
print(f"belongs to: {get_module_of_addr(addr)}")
```

## 對照 Linux info leak

| 面向 | Linux info leak | Windows info leak |
|---|---|---|
| 主要目標 | libc 基址（`__libc_start_main` 在 stack 上） | ntdll 基址 / target DLL 基址 |
| vtable 洩漏 | `_IO_FILE` 的 vtable / C++ 物件的 vptr | COM 物件的 vptr / C++ 物件的 vptr |
| 格式化字串 | 非常常見（printf 洩漏 libc 指標） | 較少（程式少用裸 printf，多用 SafeStr） |
| 未初始化記憶體 | glibc malloc 不清零（tcache/fastbin 殘留） | HeapAlloc 不清零（LFH slot 殘留） |
| GOT 洩漏 | OOB read 到 .got.plt 段讀 libc 位址 | Windows 無 GOT，IAT（import table）功能類似 |
| 額外洩漏目標 | 無 GS cookie（Linux 有 stack canary） | /GS cookie（per-process 隨機值） |
| ASLR 強度 | PIE = 基址隨機；無 PIE = 固定基址 | 所有 image 都有 ASLR（/DYNAMICBASE 預設開） |

**Windows 特有的洩漏目標**：
- `/GS` stack cookie（繞過 stack buffer overrun protection）
- CFG bitmap 的位址（繞過 CFG，Ch 32）
- GDI 物件的 kernel 指標（歷史技法，Win 10 RS2 後已修復）

## 踩雷集錦

1. **「洩漏一個 heap 指標就夠了，有了 heap 基址就能做一切」**：不夠。heap 基址讓你知道 heap 物件的位置，但不能讓你算 ROP gadget（gadget 在 module 的 .text 段）、不能算 stack cookie（在 .data 段）。heap 基址洩漏和 module 基址洩漏要分開用，通常兩個都需要。

2. **「module_base = leaked_ptr & 0xFFFFFFFF'FFFF0000 就能算基址」**：不一定。module 的對齊單位是 0x10000（64KB），但指標本身在 module 內部的 RVA 不知道——你不能假設「低 16 bits = 0 就是基址」。正確方法是靜態分析找 leaked_ptr 對應 symbol 的 RVA，再做減法。

3. **「OOB read 只要能讀就好，讀到什麼值不重要」**：讀到什麼值決定你能算什麼。讀到全 0 或 small integer 沒用；讀到一個看起來像 `0x7fff'xxxx'xxxx` 的值才是有用的指標洩漏。exploit 開發者要能區分「這個 OOB read 讀到的值有沒有 exploit 價值」。

4. **「Windows 格式化字串漏洞和 Linux 一樣，`%p %p ...` 直接讀 libc 位址」**：Windows 沒有 libc，讀到的是 stack 上的 Windows API 返回地址（在 ntdll / kernel32 等系統 DLL 裡）。概念相同，但目標模組不同；而且 Windows 程式更常見用 Unicode（`wprintf` 等），`%p` 在 `wprintf` 下格式可能和 printf 不同（以實際測試為準）。

5. **「UAF dangling read 必須在 free 後立刻讀，否則 heap manager 會清空 slot 的內容」**：在 release 環境（沒有 Page Heap / heap checking），LFH slot free 後記憶體內容通常不會立刻清零——只有 BusyBitmap 的 bit 被清掉。dangling pointer 可以在很長一段時間內讀到殘留的 vptr 等欄位，不需要「立刻」讀。

## 進階：再往深一層

### 洩漏精度：part-of-pointer leaks

有時候洩漏的不是完整的 8 bytes 指標，而是部分 bytes（例如：錯誤訊息只顯示指標的低 4 bytes，或格式化字串只讀到部分 stack 內容）。

```
  part-of-pointer leak 的利用：
  - Windows 的 image 在 x64 下通常在 0x00007F??'???????? 範圍
  - 如果 leak 到低 4 bytes = 0x3_8050，高 4 bytes 未知
  - 但 module 的對齊（0x10000）+ ASLR 的強度（19-24 bits）
  - → 可能的高 4 bytes 範圍有限，暴力猜解（但成功率低）
  - 更好的做法：找第二個 leak primitive 拿完整 8 bytes
```

### 連鎖洩漏：一個 leak 解鎖另一個 leak

```
  洩漏鏈範例：

  leak 1: OOB read 讀到 heap 上的物件指標 → heap_obj_addr
  ↓ 用 heap_obj_addr 做「受控讀」（如果有任意讀 primitive）
  leak 2: 讀 heap_obj 的 vptr 欄位 → vtable_ptr（在 module 的 .rdata）
  ↓ vtable_ptr - vtable_rva = module_base
  leak 3: 讀 module_base + import_table_rva → 讀 IAT，取得 ntdll 函式位址
  ↓ ntdll_func_addr - func_rva = ntdll_base
  → 有了 ntdll_base，可以找 ntdll 的 gadget（`syscall; ret` 等）
```

### IAT（Import Address Table）洩漏

Windows 的 IAT 是 GOT 的對應。IAT 在 .rdata（唯讀），裡面存的是各 imported 函式的實際位址（執行期由 LoadLibrary 填入）：

```
  如果你能讀到 IAT 的某個條目：
  leaked_iat_entry = *(target_module_base + iat_rva + N)
  → leaked_iat_entry 是某個 imported 函式（例如 HeapAlloc）在其模組（kernel32.dll）的實際位址
  → kernel32_base = leaked_iat_entry - HeapAlloc_rva（靜態分析得到 kernel32.dll 裡的 RVA）

  和 Linux GOT 洩漏的概念完全相同，只是叫 IAT 不叫 GOT
```

## 動手練習

**練習 1（本機可執行）**：用 Python ctypes 實作「未初始化記憶體洩漏」的 demo：

```python
import ctypes
k = ctypes.windll.kernel32
k.GetProcessHeap.restype = ctypes.c_void_p
k.HeapAlloc.restype = ctypes.c_void_p
k.HeapFree.restype = ctypes.c_bool

h = k.GetProcessHeap()

# 分配 0x40 bytes，填入「vptr-like」的值
p1 = k.HeapAlloc(h, 0, 0x40)
# 用 ntdll 的某個函式位址當作「vptr」（這個值在 module .text 裡）
ntdll = ctypes.windll.ntdll
func_addr = ctypes.cast(ntdll.RtlAllocateHeap, ctypes.c_void_p).value
ctypes.c_uint64.from_address(p1).value = func_addr  # 模擬 vptr
k.HeapFree(h, 0, p1)

# 重新分配（希望拿回同一個 slot）
p2 = k.HeapAlloc(h, 0, 0x40)
leaked = ctypes.c_uint64.from_address(p2).value
print(f"leaked value: 0x{leaked:016X}")
print(f"original:     0x{func_addr:016X}")
if leaked == func_addr:
    print("未初始化洩漏成功：拿回了含 func_addr 的 slot")
    # 計算 ntdll 基址
    import ctypes.util
    ntdll_base = ctypes.windll.kernel32.GetModuleHandleW("ntdll.dll")
    print(f"ntdll base: 0x{ntdll_base:016X}")
else:
    print("slot 被清零或被其他分配插隊（重跑看看）")
k.HeapFree(h, 0, p2)
```

**練習 2（思考題）**：你有一個 OOB read primitive：`read_at_offset(obj, offset)` 讀取 `obj + offset` 的 8 bytes，offset 沒有上限 check。描述你會怎麼用它洩漏 `target.dll` 的基址，假設你知道：
- `obj` 在 heap 的某個位置
- `target.dll` 有一個 C++ 物件和 `obj` 相鄰（透過 grooming 達成）
- 你有 `target.dll` 的 symbol file（可以靜態分析）

## 本章重點整理

- info leak 在現代 Windows exploit 裡是**必要條件**，不是可選技法——ASLR 讓所有有用位址在執行期才確定，沒有 info leak 就沒有可靠的控制流劫持
- 主要洩漏面：未初始化記憶體（前一個 allocation 的殘留）、OOB read（讀到相鄰物件的 vptr/指標欄位）、物件欄位洩漏（COM 物件 vptr 可讀）、error 回傳值側洩、格式化字串（較少見）
- 四類洩漏目標：**module 基址**（最常用，算 ROP gadget）、**heap 基址**（算 heap 物件位置）、**stack 位址**（算 stack cookie / return address）、**/GS cookie**（繞過 stack buffer overrun protection）
- 位址計算：`module_base = leaked_ptr - known_rva`；需要靜態分析找 RVA；module_base 對齊 0x10000
- 洩漏後把位址「餵給」vtable 劫持（fake vtable 的 slot 填入算好的 gadget）和 ROP（gadget 位址確定後才能構造 chain）

## 自我檢核

- [ ] 不看筆記，能說出「為什麼 ASLR 讓 info leak 成為必要」——從第一步（有 bug）到最後一步（控制流劫持）之間缺少了什麼
- [ ] 能從「洩漏一個 vtable 指標」到「計算 ROP gadget 位址」給出完整的步驟，包含靜態分析的部分（找 RVA）
- [ ] 面試被問「Windows 的 IAT 和 Linux 的 GOT 有什麼關係」，能說出兩者功能相同、洩漏用法相同，名稱和位置不同
- [ ] 知道 `/GS` cookie 洩漏的用途（繞過 stack buffer overrun protection），以及它和 vtable 劫持之間的利用先後順序
- [ ] 能解釋「未初始化記憶體洩漏」的前提（free → 不清零 → 重新分配拿回同 slot → 讀到殘留資料），並說出 release 環境下 LFH slot free 後記憶體為什麼不被立刻清零

## 延伸閱讀

### 論文 / 白皮書

- **[ASLR Smack & Laugh Reference](https://media.blackhat.com/bh-eu-12/Serna/bh-eu-12-Serna-Smashing_IE-Slides.pdf)** — Fermin Serna（Google，前 Microsoft）
  - **讀哪裡**：ASLR bypass 技法的系統整理，特別是 info leak 部分
  - **學什麼**：info leak primitive 分類方法論，以及如何從一個 primitive 擴大到可靠的 exploit
  - **前提知識**：本章 + ASLR 基礎

- **[Heap Feng Shui in JavaScript](https://www.blackhat.com/presentations/bh-europe-07/Sotirov/Presentation/bh-eu-07-sotirov-apr19.pdf)** — Alexander Sotirov，Black Hat Europe 2007
  - **讀哪裡**：Section 3–4（利用 grooming 讓 OOB 讀到目標物件）
  - **學什麼**：info leak 的 grooming 技法——如何確保 OOB read 讀到的是「有用的目標物件」而不是隨機資料
  - **前提知識**：Ch 27/28 + 本章

### 部落格

- **[j00ru — Windows Kernel Local Kernel Pointer Disclosure](https://j00ru.vexillium.org/2011/05/windows-kernel-stack-spraying-techniques/)** — Mateusz Jurczyk（j00ru）
  - **讀哪裡**：j00ru 在 Windows kernel 指標洩漏方面的系列研究
  - **學什麼**：Windows kernel 的 info leak 面（POOL、GDI 等），與 userland info leak 的區別；kernel 洩漏對 kernel pwn 的意義
  - **前提知識**：本章 + kernel pwn 基礎

- **[Connor McGarr — ASLR Bypass with Information Leaks](https://connormcgarr.github.io/)** — Connor McGarr
  - **讀哪裡**：ASLR bypass 系列文章
  - **學什麼**：Windows userland 的實際 info leak exploit 開發流程，從漏洞觸發到 module 基址計算的完整鏈
  - **前提知識**：本章全部

### 官方文件

- **[Microsoft Learn — Address Space Layout Randomization](https://learn.microsoft.com/en-us/cpp/build/reference/dynamicbase-use-address-space-layout-randomization)** — Microsoft
  - **讀哪裡**：`/DYNAMICBASE` 和 `/HIGHENTROPYVA` 的說明
  - **學什麼**：ASLR 的 official scope 和限制；哪些 binary 選項影響 ASLR 強度
  - **前提知識**：本章基礎

有了 info leak，你就有了後續所有利用技法所需的位址。把 info leak 的結果餵給 vtable 劫持（Ch 30），結合 heap grooming（Ch 28），就是完整的 Part 4 利用鏈。接下來在練習 D 把這條鏈實際走一遍。

→ [練習 D — heap UAF → 控 vtable → 轉 ROP](./practice-d-uaf-vtable.md)
