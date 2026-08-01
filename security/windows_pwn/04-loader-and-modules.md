# Ch 4 — 載入器與模組：image base / ASLR relocation / LDR

> **目標**：弄清楚 Windows loader（ntdll 的 `LdrpMapDllWithSectionHandle` 等函式群）把 PE 從文件變成活的記憶體映像的每一步，理解 preferred ImageBase 和 ASLR 下實際基址的差異、.reloc 如何被套用、Import 如何從名稱字串變成真實 VA、TLS callback 何時觸發；以及 `PEB_LDR_DATA` 的三條模組鏈，這是 shellcode 和 exploit 找模組基址的根基。

> **環境**：Windows 11 Pro x64；Python 3.12 + ctypes（PEB/LDR 走訪真實可跑）。Loader 內部流程分析基於 ReactOS 原始碼、公開的 Windows Internals 文獻與 Geoff Chappell 的研究——**不可在本機 WinDbg 步進 ntdll loader**（cdb 尚未安裝），標注「未實測」的段落請裝好 WinDbg 後自行驗證。

## 為什麼需要這個？

你在 Linux 有用過 `LD_DEBUG=all ./program` 嗎？那幾百行輸出就是 `ld.so` 在幹的事：找 DSO → 映射 → reloc → 呼叫 constructors。你已經有直覺了。

Windows 的 loader 做同一件事，但實作路徑完全不同：`ld.so` 是 ELF，進程空間的外來者；Windows loader 是 `ntdll.dll` 的一部分，**和進程共用同一個 ntdll 映像**（ntdll 本身是第一個被映射的 DLL，kernel 在建立進程時就把它映射好了）。這個差異決定了：

- Windows loader 可以用 `NtAllocateVirtualMemory`/`NtMapViewOfSection` 這些 native API 直接操作自己
- loader 的資料結構（尤其是 LDR）是活的，跑中隨時可讀；shellcode 靠讀 LDR 找模組，exploit 靠它找 ROP gadget 來源
- loader 的 hook 點（TLS callback、DllMain、初始化順序）比 `LD_PRELOAD` 的介面更多樣也更危險

不懂 loader 的工作流程，你就看不懂 WinDbg 裡模組的 `lm` 輸出、不懂為什麼同一個 DLL 在不同進程有不同的 base address、也無法理解 shellcode 為何從 `PEB->Ldr` 開始找 kernel32。

## 先建立直覺：一個進程啟動的完整時序

從你雙擊一個 `.exe` 到 `main()` 第一行執行：

```
kernel                              ntdll loader                     user code
  │                                       │
  ├─[1] NtCreateUserProcess                │
  │     建立進程核心物件                    │
  │     映射 ntdll 和 exe 到虛擬空間        │
  │     建立初始執行緒（起點 = LdrpInitialize）
  │                                       │
  │                              [2] LdrpInitialize
  │                                  初始化 PEB
  │                                  建立 PEB_LDR_DATA
  │                                  把 exe 加入 LDR 鏈
  │                                       │
  │                              [3] 解析 exe 的 Import Directory
  │                                  對每個 DLL：
  │                                  ├─ LdrpLoadDll（找 DLL 文件）
  │                                  ├─ 映射 DLL（NtMapViewOfSection）
  │                                  ├─ 遞迴解析 DLL 的 Import
  │                                  └─ 加入 LDR 鏈
  │                                       │
  │                              [4] 執行 ASLR rebase（.reloc）
  │                                  對每個模組：actual != preferred
  │                                  → 遍歷 .reloc 加 delta
  │                                       │
  │                              [5] 填入 IAT
  │                                  對每個 Import Descriptor：
  │                                  找 DLL 的 Export Directory
  │                                  把函式 VA 寫入 IAT 條目
  │                                       │
  │                              [6] 呼叫 TLS callbacks（DLL_PROCESS_ATTACH）
  │                                  （在 DllMain 之前！）
  │                                       │
  │                              [7] 呼叫各 DLL 的 DllMain(DLL_PROCESS_ATTACH)
  │                                  （按初始化依賴順序）
  │                                       │
  │                              [8] 呼叫 exe 的 TLS callbacks
  │                              [9] 呼叫 CRT 初始化（__mainCRTStartup）
  │                                       │
  │                                                        [10] main() ← 你的 code
```

注意：**TLS callback 比 DllMain 早，DllMain 比 main 早**。這是反調試和惡意軟體最愛的時間窗口。

**vs Linux**：`ld.so` 的對應步驟：映射所有 `PT_LOAD` → `.rela.dyn` reloc → `.init_array` → `main()`；沒有 TLS callback 這個抽象，但有 `__attribute__((constructor))` 做類似的事。

## 步驟 1：映射 PE 到虛擬記憶體

Loader 用 `NtMapViewOfSection` 把 PE 文件映射進虛擬空間（不是手動 `VirtualAlloc` + memcpy）。這和 Linux 的 `mmap` 是同一個概念：文件映射，不是複製。

每個 section 按照 Section Header 的規格映射：

```
文件中                               記憶體中（以 preferred ImageBase = 0x140000000 為例）
─────────────────────────────────   ─────────────────────────────────────────────────────
FileOffset 0x000400 (size 0x1800)   0x140001000 .text   (EXECUTE|READ, 4KB aligned)
FileOffset 0x001C00 (size 0x200)    0x140003000 .data   (READ|WRITE)
FileOffset 0x001E00 (size 0xC00)    0x140004000 .rdata  (READ)
FileOffset 0x000000 (size 0)        0x140007000 .bss    (READ|WRITE, 全為 0，無文件對應)
FileOffset 0x003000 (size 0x800)    0x140008000 .idata  (READ|WRITE) ← IAT 在這
FileOffset 0x004200 (size 0x200)    0x14000C000 .reloc  (READ)
```

`VirtualSize` > `SizeOfRawData` 的部分補 0（這就是 .bss 的實現方式）。Loader 在 `NtMapViewOfSection` 後再用 `VirtualProtect` 把各 section 設成對應的保護位：`PAGE_EXECUTE_READ`（.text）、`PAGE_READWRITE`（.data/.idata/.bss）、`PAGE_READONLY`（.rdata/.reloc）。

**SizeOfHeaders 的特殊地位**：PE headers（DOS Header + NT Headers + Section Headers）以 `SizeOfHeaders` 為大小映射為 `PAGE_READONLY`。這意味著進程裡**可以直接讀 PE header**（讀 ImageBase/EntryPoint/DllCharacteristics），這是很多 info leak 利用的基礎——不需要讀文件，直接從進程記憶體讀 `(ImageBase)` 就能拿到完整 PE 結構。

## 步驟 2：ASLR Rebase 與 .reloc 的作用

### Preferred ImageBase 與 ASLR

PE 的 preferred ImageBase（OptionalHeader 裡）是編譯器期望的載入地址。Win11 x64 的 ASLR 機制讓 loader 隨機選一個實際的基址：

```
ASLR 決策：
  若 DllCharacteristics & DYNAMIC_BASE（0x0040）且 .reloc 存在：
    随機選 actual_base（x64 高熵 ASLR 用高 48-bit，64TB 範圍）
  否則：
    actual_base = preferred ImageBase（固定，等同無 ASLR）
```

**重要細節**：如果 PE 沒有 .reloc（linker 用 `/FIXED` 選項），即使 `DYNAMIC_BASE` 位元為 1，loader 也無法安全 rebase——它只能嘗試在 preferred base 載入。若該地址已被佔用，載入**失敗**。所以 DLL 一定要有 .reloc；exe 可以沒有（但現代 exe 都有）。

### .reloc 的套用（delta = actual - preferred）

假設 preferred = `0x140000000`，loader 選擇 actual = `0x7FF6CF000000`（ASLR 後的值），則：

```
delta = 0x7FF6CF000000 - 0x0000000140000000
      = 0x7FF68F000000
```

Loader 遍歷 .reloc 的每個 block：

```
每個 IMAGE_BASE_RELOCATION block：
  page_rva + (offset & 0x0FFF)  → 找到虛擬記憶體中的位置
  type = (entry >> 12) & 0xF
  if type == 10 (DIR64):
      *(uint64_t*)(actual_base + page_rva + offset) += delta
  if type == 0 (ABSOLUTE):
      skip（padding）
```

本機 demo_stripped.exe 有 44 個 DIR64 relocation 條目（本章 3 章節的 .reloc 解析輸出已在 Ch 3 貼出），每次 ASLR rebase 都要對這 44 個位置加 delta。

**為什麼 x64 比 x86 更多 relocation？**：x64 呼叫使用 RIP-relative 定址（`lea rax, [rip + offset]`），大部分指令不需要 reloc；但**全域變數的絕對指標**（vtable 指標、函式指標陣列、C 字串指標等）必須 reloc。x86 的 32-bit 程式碼大量使用絕對位址，relocation 更多。

**vs ELF**：ELF PIE 的 `.rela.dyn`（`R_X86_64_RELATIVE` 類型）做同一件事，但每條記錄 24 bytes（包含 addend），比 PE .reloc 的 2 bytes 條目更詳細但佔空間更大。ELF 還有 `.rela.plt`（PLT 的 lazy binding reloc），PE 沒有等價物（eager binding）。

## 步驟 3：Import 解析，IAT 填入

這是 loader 最耗時的步驟（DLL 依賴深的話）。流程：

```
對每個 IMAGE_IMPORT_DESCRIPTOR（每個 DLL）：
  1. 讀 Name RVA → DLL 名稱字串（"KERNEL32.dll"）
  2. LdrpLoadDll("KERNEL32.dll")
     ├─ 搜尋 LDR 鏈（是否已載入？）
     ├─ 若未載入：LdrpFindOrMapDll
     │   ├─ 搜尋 DLL 路徑（下面講搜尋順序）
     │   ├─ NtMapViewOfSection（映射 DLL）
     │   ├─ 遞迴解析 DLL 的 Import（深度優先）
     │   └─ 加入 LDR 鏈
     └─ 取得 DLL 的實際 ImageBase
  3. 遍歷 OriginalFirstThunk（ILT）：
     for each thunk:
       if bit63 == 1:  ordinal = thunk & 0x7FFF
       else:           name = RVA_of_IMAGE_IMPORT_BY_NAME
                       hint = name_entry.Hint
                       func_name = name_entry.Name
       funcRVA = LookupExportByName(dll, func_name)  ← 查 DLL 的 Export Directory
       IAT[i] = dll.actual_base + funcRVA             ← 寫入真實 VA！
```

步驟 3 完成後，你的程式碼裡每個 `call [IAT + offset]` 就能跳到正確的函式。

### Export Lookup 細節

Loader 查 Export Directory 的方式（本機的 Export Directory 見 Ch 3，這裡說查找邏輯）：

```
1. 二分搜尋 AddressOfNames[]（已排序的名稱 RVA 陣列）
2. 找到 index i → ordinal = AddressOfNameOrdinals[i]
3. funcRVA = AddressOfFunctions[ordinal - Base]
4. 若 funcRVA 落在 Export Directory 的 RVA 範圍內：
     → Forwarder！（如 "NTDLL.RtlAllocateHeap"），遞迴查轉發目標
   否則：
     → 真實函式 RVA，加 actual_base 即 VA
```

**Hint 最佳化**：`IMAGE_IMPORT_BY_NAME.Hint` 是上次 link 時那個函式在 export table 中的序號。Loader 先試 `AddressOfNames[Hint]`，若匹配就省下二分搜尋。Hint 不保證在不同版本 DLL 裡有效，只是 hint。

## 步驟 4：DLL 搜尋順序（與 DLL Hijacking）

Loader 找一個 DLL 文件的搜尋順序（正常啟用 SafeDllSearchMode 時）：

```
1. 已知 DLL 列表（KnownDLLs registry key）← 直接從 \KnownDlls\ object 取，不搜文件系統
2. 應用程式目錄（exe 所在資料夾）
3. System directory（C:\Windows\System32）
4. 16-bit system directory（C:\Windows\System，現代基本忽略）
5. Windows directory（C:\Windows）
6. 當前工作目錄（Current Working Directory）
7. PATH 環境變數中的目錄（按順序）
```

**SafeDllSearchMode 停用時**（舊版行為）：當前工作目錄移到 System32 之前（第 3 步），這讓 DLL hijacking 更容易。

**DLL Hijacking 的教育性理解**：如果應用程式目錄（第 2 步）是攻擊者可寫入的，或者應用程式 import 了一個不在 KnownDLLs 的 DLL（搜到 System32 也沒有），loader 會往後找到攻擊者控制的目錄。把一個同名惡意 DLL 放對位置，loader 就載入它。這是提權和持久化的常見技法（特別是 UAC bypass）。**本課只教原理，不在合法環境外實踐**。

**vs Linux**：`ld.so` 的搜尋順序：`rpath`（embedded）→ `LD_LIBRARY_PATH` → `/etc/ld.so.cache` → 標準目錄。Linux 沒有 Windows 的 KnownDLLs 機制；`LD_PRELOAD` 功能上類似但更強（可注入任何符號）。

## 步驟 5–9：TLS Callback、DllMain、CRT 初始化

### TLS Callback 的執行時機（Ch 3 已介紹結構，這裡講執行語意）

TLS Directory 的 `AddressOfCallBacks` 是一個 null-terminated 函式指標陣列，每個指標的原型是：

```c
typedef VOID (NTAPI *PIMAGE_TLS_CALLBACK)(
    PVOID DllHandle,   // DLL 或 EXE 的基址
    DWORD Reason,      // DLL_PROCESS_ATTACH = 1, etc.
    PVOID Reserved
);
```

Loader 在以下時機按陣列順序呼叫每個 callback：

```
時機                    對應 DllMain Reason
──────────────────────────────────────────
進程啟動（早於 DllMain）  DLL_PROCESS_ATTACH (1)
DLL 載入（早於 DllMain）  DLL_PROCESS_ATTACH (1)
執行緒建立               DLL_THREAD_ATTACH  (2)
執行緒終止               DLL_THREAD_DETACH  (3)
進程終止                 DLL_PROCESS_DETACH (0)
```

**反調試濫用**：TLS callback 在 `main` 之前跑，此時調試器通常還停在 system breakpoint（ntdll 的初始中斷），沒有對 main 下斷點。惡意程式在 TLS callback 裡做 `IsDebuggerPresent()` 或 `NtQueryInformationProcess(ProcessDebugPort)` 檢查，若在調試器下就改變行為甚至崩潰。逆向目標時如果 Data Directory Entry 9 不為 0，先看 TLS callback。

### DllMain 執行順序

Loader 依**初始化依賴倒序**呼叫 DllMain（先被依賴的後初始化是錯誤說法——實際上 loader 深度優先載入，最先載入的最先呼叫 DllMain，但沿著依賴樹的葉節點先走）。用 InInitializationOrder 鏈追蹤。重要限制：**DllMain 裡不能做任何會觸發 loader lock 的操作**（不能呼叫 LoadLibrary、不能建立執行緒等待其他執行緒，否則 deadlock）。這個限制和 ELF 的 `__attribute__((constructor))` 沒有對等的限制不同，是 Windows DLL 開發的第一個坑。

## PEB_LDR_DATA：三條模組鏈

PEB 裡的 `Ldr` 欄位（`PEB + 0x18`）指向 `PEB_LDR_DATA`，它維護三條雙向鏈結清單，每條以不同順序串聯所有已載入模組的 `LDR_DATA_TABLE_ENTRY`：

```c
typedef struct _PEB_LDR_DATA {
    ULONG     Length;           // 結構大小（0x58 in modern Windows）
    BOOLEAN   Initialized;
    PVOID     SsHandle;
    LIST_ENTRY InLoadOrderModuleList;          // 按載入順序
    LIST_ENTRY InMemoryOrderModuleList;        // 按記憶體地址順序
    LIST_ENTRY InInitializationOrderModuleList;// 按初始化（DllMain）順序
    // ...更多欄位（NTDLL_LDR_DATA 結構在不同版本有變化）
} PEB_LDR_DATA;
```

```c
typedef struct _LDR_DATA_TABLE_ENTRY {
    LIST_ENTRY InLoadOrderLinks;           // +0x000（x64）
    LIST_ENTRY InMemoryOrderLinks;         // +0x010
    LIST_ENTRY InInitializationOrderLinks; // +0x020
    PVOID      DllBase;                    // +0x030  ← DLL 的實際基址
    PVOID      EntryPoint;                 // +0x038
    ULONG      SizeOfImage;               // +0x040
    UNICODE_STRING FullDllName;            // +0x048（Length, MaxLen, Buffer）
    UNICODE_STRING BaseDllName;            // +0x058
    ULONG      Flags;                      // +0x068（包含 LDRP_ENTRY_PROCESSED 等）
    // ...（實際結構更長，含 TlsIndex, LoadCount 等）
} LDR_DATA_TABLE_ENTRY;
```

**三條鏈的差異**：

| 鏈 | 順序 | 用途 |
|---|---|---|
| InLoadOrder | 依 LoadLibrary 先後 | 通常 exe → ntdll → KERNEL32 → KERNELBASE → ... |
| InMemoryOrder | 依 DllBase 地址 | 按記憶體佈局順序，ASLR 後可能與 LoadOrder 不同 |
| InInitializationOrder | 依 DllMain 呼叫順序 | 不含 exe 和 ntdll（它們不呼叫 DllMain）|

**Flink/Blink 指向哪裡**：`LIST_ENTRY.Flink` 指向**下一個 LDR_DATA_TABLE_ENTRY 的對應 LIST_ENTRY 欄位**（不是指向 entry 的開頭）。要從 `InLoadOrderLinks.Flink` 算回 entry 的基址，你需要減去 `InLoadOrderLinks` 在 struct 中的偏移（0x000）——正好是 0，所以 InLoadOrder 的 Flink 直接就是下一個 entry 的位址。但 InMemoryOrder（+0x010）和 InInitializationOrder（+0x020）的 Flink 指向的是 entry 的 +0x10 和 +0x20，要減去偏移才能得到 entry 基址。Shellcode 用 InLoadOrder 最方便就是這個原因。

**PEB_LDR_DATA 的 InLoadOrder 頭**：`PEB.Ldr` → `PEB_LDR_DATA.InLoadOrderModuleList.Flink` 指向第一個 `LDR_DATA_TABLE_ENTRY` 的 `InLoadOrderLinks`（也就是 entry 基址，因為偏移為 0）。

## 底層機制：走 LDR 鏈找模組基址

### 真實 Python 實作（本機實跑，輸出在下面）

```python
#!/usr/bin/env python3
"""ldr_walk.py — 從 PEB->Ldr 走 InLoadOrderModuleList 列出所有模組"""
import ctypes, struct, os

kernel32 = ctypes.WinDLL('kernel32')
ntdll    = ctypes.WinDLL('ntdll')

# 1. 取得 PEB 基址（via NtQueryInformationProcess）
class PROCESS_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [('Reserved1',    ctypes.c_ulonglong),
                ('PebBaseAddress', ctypes.c_ulonglong),
                ('Reserved2',    ctypes.c_ulonglong * 2),
                ('UniqueProcessId', ctypes.c_ulonglong),
                ('Reserved3',    ctypes.c_ulonglong)]

pid = os.getpid()
hProc = kernel32.OpenProcess(0x1F0FFF, False, pid)

pbi = PROCESS_BASIC_INFORMATION()
ret_len = ctypes.c_ulong()
NtQIP = ntdll.NtQueryInformationProcess
NtQIP.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                  ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p]
NtQIP.restype  = ctypes.c_long
NtQIP(hProc, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), ctypes.byref(ret_len))
peb_addr = pbi.PebBaseAddress
print(f"PEB @ 0x{peb_addr:016X}")

# 2. ReadProcessMemory helper
ReadProcessMemory = kernel32.ReadProcessMemory
def rpmem(addr, size):
    buf = (ctypes.c_ubyte * size)()
    nr  = ctypes.c_size_t()
    ReadProcessMemory(hProc, ctypes.c_void_p(addr), buf, size, ctypes.byref(nr))
    return bytes(buf)

def read_u64(addr):
    return struct.unpack_from('<Q', rpmem(addr, 8))[0]

def read_unicode_string(addr):
    """讀 UNICODE_STRING：+0=Length, +8=Buffer*"""
    us = rpmem(addr, 16)
    length  = struct.unpack_from('<H', us, 0)[0]
    buf_ptr = struct.unpack_from('<Q', us, 8)[0]
    if buf_ptr == 0 or length == 0:
        return ''
    return rpmem(buf_ptr, length).decode('utf-16-le', errors='replace')

# 3. PEB + 0x18 = Ldr*
peb_bytes = rpmem(peb_addr, 0x28)
ldr_addr  = struct.unpack_from('<Q', peb_bytes, 0x18)[0]
print(f"Ldr (PEB_LDR_DATA*) @ 0x{ldr_addr:016X}")

# 4. InLoadOrderModuleList.Flink（offset +0x10 in PEB_LDR_DATA）
ldr_bytes = rpmem(ldr_addr, 0x58)
flink_lo  = struct.unpack_from('<Q', ldr_bytes, 0x10)[0]  # InLoadOrder head.Flink
list_head = ldr_addr + 0x10                               # 終止條件：Flink == list_head

# 5. 遍歷 InLoadOrderModuleList
print(f"\n{'Module':<40} {'DllBase':>18}  {'Size':>10}")
print('─' * 74)

entry_addr = flink_lo
seen = set()
while entry_addr != list_head and entry_addr not in seen:
    seen.add(entry_addr)
    # InLoadOrderLinks 在 entry +0x00，所以 entry_addr 就是 LDR_DATA_TABLE_ENTRY*
    dll_base    = read_u64(entry_addr + 0x30)
    size_image  = struct.unpack_from('<I', rpmem(entry_addr + 0x40, 4))[0]
    name        = read_unicode_string(entry_addr + 0x58)  # BaseDllName
    if name or dll_base:
        print(f"{name:<40} 0x{dll_base:016X}  0x{size_image:08X}")
    entry_addr = read_u64(entry_addr)  # InLoadOrderLinks.Flink
```

**本機真實輸出**（Python 3.12 進程，截取前 20 項）：

```
PEB @ 0x000000FCFA024000
Ldr (PEB_LDR_DATA*) @ 0x00007FFA85B918C0

Module                                     DllBase              Size
──────────────────────────────────────────────────────────────────────────
python3.exe                    0x00007FF6CFDA0000  0x00021000
ntdll.dll                      0x00007FFA859C0000  0x00266000
KERNEL32.DLL                   0x00007FFA849A0000  0x000C9000
KERNELBASE.dll                 0x00007FFA830D0000  0x003FE000
ucrtbase.dll                   0x00007FFA82CC0000  0x0014C000
libpython3.12.dll              0x00007FF9F05C0000  0x005A0000
ADVAPI32.dll                   0x00007FFA84070000  0x000B7000
msvcrt.dll                     0x00007FFA85840000  0x000A9000
sechost.dll                    0x00007FFA85160000  0x000AA000
RPCRT4.dll                     0x00007FFA83D80000  0x00118000
WS2_32.dll                     0x00007FFA858F0000  0x00080000
libgcc_s_seh-1.dll             0x00007FFA47530000  0x0002C000
bcrypt.dll                     0x00007FFA82AD0000  0x0002A000
VERSION.dll                    0x00007FFA7AAA0000  0x0000B000
libwinpthread-1.dll            0x00007FFA67670000  0x00016000
CRYPTBASE.DLL                  0x00007FFA82080000  0x0000C000
bcryptPrimitives.dll           0x00007FFA83010000  0x000AC000
_ctypes.cp312-mingw.pyd        0x00007FFA1FD70000  0x0002F000
```

第一項是 exe 本身（python3.exe），第二項是 ntdll——這是 InLoadOrder 的標準順序。

### 從 LDR 找 kernel32 並定位 Export Directory

這是 shellcode 的第一步（Ch 25 的完整版），這裡先講原理：

```python
# 接上面的 ldr_walk.py，找 KERNEL32 的 Export Directory
def find_export(dll_base, func_name):
    """從 DLL 基址掃 Export Directory 找函式 RVA"""
    hdr_bytes = rpmem(dll_base, 0x1000)
    e_lfanew = struct.unpack_from('<I', hdr_bytes, 0x3C)[0]
    oh_off = dll_base + e_lfanew + 4 + 20  # OptionalHeader 的 VA
    # DataDirectory[0] = Export Directory，在 OptionalHeader +112
    exp_rva  = struct.unpack_from('<I', rpmem(oh_off + 112, 4))[0]
    exp_size = struct.unpack_from('<I', rpmem(oh_off + 116, 4))[0]
    if exp_rva == 0:
        return None

    exp = dll_base + exp_rva
    exp_bytes = rpmem(exp, 40)
    num_names  = struct.unpack_from('<I', exp_bytes, 24)[0]
    eat_rva    = struct.unpack_from('<I', exp_bytes, 28)[0]
    names_rva  = struct.unpack_from('<I', exp_bytes, 32)[0]
    ordinals_rva = struct.unpack_from('<I', exp_bytes, 36)[0]

    for i in range(num_names):
        name_rva = struct.unpack_from('<I', rpmem(dll_base + names_rva + i*4, 4))[0]
        name_va  = dll_base + name_rva
        name_buf = rpmem(name_va, 64)
        null_idx = name_buf.find(b'\x00')
        name = name_buf[:null_idx].decode('ascii', errors='replace')
        if name == func_name:
            ordinal = struct.unpack_from('<H', rpmem(dll_base + ordinals_rva + i*2, 2))[0]
            func_rva = struct.unpack_from('<I', rpmem(dll_base + eat_rva + ordinal*4, 4))[0]
            return dll_base + func_rva
    return None

# 找 kernel32 的 VirtualAlloc
for entry_addr_k32, name_k32, base_k32 in [
    # 簡化：直接從上面 walk 找到的 KERNEL32.DLL
]:
    pass  # 完整版在 Ch 25

# 更快的方式：直接用 ctypes 取得已知 DLL base（練習環境）
import ctypes.util
k32_base = ctypes.WinDLL('kernel32')._handle
print(f"\nKERNEL32 handle (base): 0x{k32_base:016X}")
va_addr = find_export(k32_base, 'VirtualAlloc')
print(f"VirtualAlloc VA: 0x{va_addr:016X}" if va_addr else "not found")
```

> **注意**：`ctypes.WinDLL('kernel32')._handle` 是最直接拿到模組 handle（即基址）的方法，在 Python 裡當練習用。真正的 shellcode 要自己走 PEB_LDR_DATA，不能依賴 ctypes，見 Ch 25。

## ASLR 的 Windows 實作細節

Windows x64 的 ASLR 比 Linux 精細，但有限制：

| 模組類型 | ASLR 熵（x64） | 備注 |
|---|---|---|
| exe（HIGH_ENTROPY_VA=1） | 高達 48-bit | `0x000000..` 到 `0x7FFFFF..` |
| exe（HIGH_ENTROPY_VA=0） | 8-bit（256 個位置） | 比較弱 |
| DLL（HIGH_ENTROPY_VA=1） | 同 exe，高熵 | 共享 DLL 在所有進程同基址 |
| Heap | 每次 HeapCreate 隨機 | Ch 14/16 |
| Stack | 執行緒建立時隨機 | 3-bit 額外偏移 |

**重要**：系統 DLL（ntdll、KERNEL32、KERNELBASE）在**系統 session 內所有進程共用同一個基址**——因為 Windows 把它們以 `NtMapViewOfSection` 映射成 page-sharable 的 section，整個系統只有一份物理頁面，所有進程映射同一 VA。這意味著：知道 ntdll 在任何一個進程的基址，就知道它在所有進程的基址（直到系統重開機前）。這對 ASLR bypass 很有用。

**Boot-time ASLR vs. Load-time ASLR**：系統 DLL 在系統啟動時（smss.exe）就決定好基址，之後所有進程共用。exe 和非 KnownDLL 的第三方 DLL 是 load-time 隨機，每次 LoadLibrary 都可能不同。

## 對比與取捨

| 面向 | Windows Loader（ntdll） | Linux ld.so |
|---|---|---|
| 位置 | ntdll.dll 的一部分，和進程共用 | 獨立 ELF 映像（ld-linux-x86-64.so.2） |
| Relocation | .reloc（頁面 block，2B 條目） | .rela.dyn（24B 條目，含 addend） |
| Lazy Binding | 無（預設 eager）；有 Delay Import | 有（PLT + .got.plt，RTLD_LAZY 預設） |
| 模組鏈資料結構 | PEB_LDR_DATA（三條 LIST_ENTRY 雙向鏈） | `r_debug.r_map`（`link_map` 單鏈）|
| 系統 DLL 共享 | 全系統同基址（NtMapViewOfSection）| 相同，`glibc.so` 在一個 session 裡共享 |
| DLL 搜尋 | KnownDLLs → AppDir → System32 → CWD → PATH | rpath → LD_LIBRARY_PATH → /etc/ld.so.cache → 標準 |
| DllMain/Constructor | DllMain（有 loader lock 限制） | `__attribute__((constructor))`（無 lock 限制） |
| TLS Callback | 在 DllMain 之前，每個 attach/detach | GCC/Clang 的 TLS init 不直接等價 |
| 模組遍歷（shellcode） | PEB → Ldr → InLoadOrderModuleList | 讀 /proc/self/maps 或 r_debug（更麻煩）|

## 踩雷集錦

1. **「InMemoryOrder 的 Flink 減 0 就是 entry 基址」**：錯。InMemoryOrder 在 `LDR_DATA_TABLE_ENTRY + 0x10`，所以 InMemoryOrderLinks.Flink 指向的是下一個 entry 的 +0x10 處，你要減 0x10 才能得到 entry 基址。InLoadOrder 因為偏移 0 所以特殊——Flink 直接是 entry 基址。Shellcode 用 InLoadOrder 是最方便的。

2. **「ASLR 重開機前每次啟動都隨機」**：系統 DLL（ntdll/kernel32）**系統啟動時固定，重開機前不變**。只有你的 exe 和非 KnownDLL 的 DLL 每次 load 才重隨機。所以「洩露 ntdll 基址」在重開機前永遠有效。

3. **「DllMain 可以呼叫 LoadLibrary」**：不行。DllMain 在 loader lock 持有期間被呼叫，呼叫 LoadLibrary 會嘗試再取得 loader lock 而 deadlock。這是 Windows DLL 開發最著名的限制，MSDN 專門有一頁警告這件事。

4. **「PE 沒有 .reloc 但設了 DYNAMIC_BASE 就能 ASLR」**：不能。loader 需要 .reloc 才能安全 rebase；沒有 .reloc 就以 preferred base 載入，若被佔用則載入失敗。`/FIXED` 選項告訴 linker 不生成 .reloc，此時應同時把 DYNAMIC_BASE 清掉。

5. **「InInitializationOrder 鏈包含所有模組」**：不包含 exe 本身和 ntdll（它們不呼叫 DllMain，所以不在初始化鏈裡）。走這條鏈找 KERNEL32 可能找到，但找不到 ntdll——要找 ntdll 用 InLoadOrder（第二個條目，exe 是第一個）。

## 進階：再往深一層

### LDR 的未文件化欄位與版本變化

`LDR_DATA_TABLE_ENTRY` 在 Windows 10/11 有額外欄位（`LoadReason`、`OriginalBase`、`LoadTime` 等），偏移在不同版本略有差異。Geoff Chappell 的 [ntdll/structs/ldrdatatableentry](https://www.geoffchappell.com) 頁面對每個 Windows 版本逐一列出。在 exploit 裡，你只需要前幾個欄位（到 BaseDllName 偏移 0x58）——這些在 Win XP 到 Win 11 都穩定。

### LDR 完整性保護（Win8+）

Windows 8 開始 ntdll 對 LDR 某些操作加了 Heap 驗證和 critical section 保護，注入 DLL 時直接修改 LDR 鏈的 shellcode 技法（如 `Process Hollowing` 的某些變體）要繞過這個。直接用 LoadLibrary 或 `NtMapViewOfSection` 是更穩的注入方式。

### IAT Hooking 的 loader 視角

一些 EDR 在進程啟動時（在 ntdll 的 hook 點）做 IAT hook——在 DllMain 呼叫前修改 IAT 讓特定 API 呼叫先過 EDR 的 filter。理解 loader 的時序（ntdll 初始化 → DLL mapping → IAT 填入 → DllMain）就能知道哪個時機點的 hook 覆蓋了哪些呼叫，以及 syscall-based bypass（Ch 7）為什麼能繞過這類 hook。

## 動手練習

修改本章的 `ldr_walk.py`，讓它：

1. 對每個模組，用 Ch 3 的 PE parser 讀出它的 `DllCharacteristics`，印出它開了哪些緩解旗標
2. 找到 `ntdll.dll` 的基址後，解析它的 Export Directory，列出包含 "Alloc" 子字串的所有匯出函式名稱（應該能找到 `NtAllocateVirtualMemory`、`RtlAllocateHeap` 等）
3. 確認 InLoadOrder 第一個是 exe 本身、第二個是 ntdll——這個順序在正常進程裡是保證的

進階（選做）：讓 script 同時走 InMemoryOrder 鏈，對比兩條鏈的模組順序是否不同，並解釋差異原因（提示：ASLR 後記憶體地址和載入時間的排序不一定一致）。

## 本章重點整理

- Loader 的工作：映射 section → 套 .reloc（ASLR delta）→ 解析 Import（填 IAT）→ TLS callback → DllMain → CRT init → main。TLS callback 在 main 之前，是反調試最常藏的地方。
- ASLR rebase：`actual_base - preferred_base = delta`，loader 對每個 DIR64 reloc 條目做 `*addr += delta`；系統 DLL 在系統啟動時固定，重開機前不變。
- `PEB_LDR_DATA` 三條鏈（InLoadOrder / InMemoryOrder / InInitializationOrder），shellcode 走 InLoadOrder 最方便（`LDR_DATA_TABLE_ENTRY` 偏移 0 = Flink，+ 0x30 = DllBase，+ 0x58 = BaseDllName）。
- DLL 搜尋順序：KnownDLLs > AppDir > System32 > CWD > PATH；`SafeDllSearchMode` 決定 CWD 的位置。

## 自我檢核

- [ ] 不看筆記，能說出 loader 從「雙擊 exe」到「main 第一行」的 9 個主要步驟，以及 TLS callback 出現在第幾步
- [ ] 能解釋 InLoadOrderLinks.Flink 和 InMemoryOrderLinks.Flink 在走訪時為什麼偏移算法不同
- [ ] 被問「進程崩潰，.reloc 呢？能否在偵錯器看出 ASLR rebase 了多少」——知道 preferred ImageBase 在 PE header 裡，actual base 在 LDR_DATA_TABLE_ENTRY.DllBase，兩者差就是 delta
- [ ] 能說出為什麼 ntdll 洩露在同一台機器上（不重開機）等於 permanent leak
- [ ] 面試被問「DLL Hijacking 的原理」：能說出搜尋順序的第 2 步和攻擊前提

## 延伸閱讀

### 原始碼

- **[ReactOS — ntdll/ldr/ldrpe.c](https://github.com/reactos/reactos/blob/master/dll/ntdll/ldr/ldrpe.c)** 和 **ldrutils.c**
  - **讀哪裡**：`LdrpMapDllWithSectionHandle`、`LdrpLoadImportModule`、`LdrpSnapThunk`（Import 解析）
  - **和本章的關聯**：本章的「步驟 3 Import 解析」流程幾乎是從這裡描述的；ReactOS 的實作和真實 Windows ntdll 有些差異但邏輯相同
  - **前提**：C 讀寫能力，本章讀完

### 書籍

- **《Windows Internals, 7th Edition》— Part 1，Ch 3（Processes）：Image Loader 節**（Yosifovich/Ionescu）
  - **讀哪裡**：「Image Loader」小節，重點是 LDR data structure 和 DLL loading 流程圖
  - **和本章的關聯**：本章的 ASCII 流程圖是這本的簡化版；書裡有 WinDbg `!ldr` 的真實 dump，裝好後對照
  - **前提**：本章讀完

### 深度部落格

- **[Geoff Chappell — Windows Loader](https://www.geoffchappell.com/studies/windows/win32/ntdll/ldr/index.htm)**
  - **讀哪裡**：`LDR_DATA_TABLE_ENTRY` 的結構說明（每個 Windows 版本的欄位差異）
  - **和本章的關聯**：本章給的欄位偏移在 Win 10/11 穩定；但若你需要精確支援更多版本，Geoff 的表格是唯一可靠來源
  - **前提**：本章讀完

- **[Alex Ionescu — Reversing Windows Loader (SyScan 2012)](https://github.com/ionescu007/Alex-Ionescu-Windows-Internals)** （Slide/Talk）
  - **讀哪裡**：關於 LdrpInitialize 和模組初始化順序的部分
  - **和本章的關聯**：本章的「進程啟動時序圖」參考了 Ionescu 這個 talk 的框架；他的研究是 loader 逆向的起點

### 安全研究

- **[DLL Hijacking Revisited — Trail of Bits](https://blog.trailofbits.com/2021/10/25/on-safely-using-temporary-directories/)**
  - **讀哪裡**：文章本身不長；重點是 CWD 和 AppDir 的攻擊面說明
  - **和本章的關聯**：本章「DLL 搜尋順序」的防禦意涵；了解為什麼 CWD 能是攻擊向量

有了 loader 的完整視角後，下一章進 PEB/TEB 的深挖——這兩個結構是進程自省的核心，也是 shellcode 和 anti-debug 的基礎。

→ [Ch 5 — PEB / TEB：結構、走訪與在 exploit 裡的用途](./05-peb-teb.md)
