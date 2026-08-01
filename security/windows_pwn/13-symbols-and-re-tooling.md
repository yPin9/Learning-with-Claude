# Ch 13 — 符號與逆向工具鏈：public symbols / IDA / Ghidra

> **目標**：搞懂 PDB 符號系統（RSDS、GUID/Age、symbol server URL）、`_NT_SYMBOL_PATH` 如何讓 WinDbg 自動取得 ntdll 的每個結構欄位；學會用 IDA / Ghidra 載入帶 symbols 的 PE；掌握 PE 檢視工具鏈（PE-bear / CFF Explorer / dumpbin / objdump）在 exploit 開發流程裡各自的定位。能用 Python 讀 PE debug directory 找 PDB 路徑（本機實跑）。

> **環境**：Python 3.12 + struct（本機可跑）；mingw-w64 GCC 14.2（本機可跑）；WinDbg / IDA / Ghidra / MSVC dumpbin 段落標「未實測」。

---

## 為什麼需要這個？

你在 Linux 逆向時，「有符號」和「沒符號」是天壤之別：有 `-g` 的 ELF 讓 gdb 知道 `main` 在哪、local var 叫什麼；stripped 的 ELF 什麼都沒有，只剩位址。

Windows 的情況有一個有趣的扭曲：Microsoft **對大多數系統 DLL 提供公開的 partial symbols**，放在一個全球可存取的 symbol server 上，免費下載。這意味著：

- 你永遠知道 `ntdll!RtlAllocateHeap`、`kernel32!VirtualAlloc` 的確切地址（一旦 ASLR 解決後）
- 你可以 `dt ntdll!_PEB` 直接印出 PEB 每個欄位的名字和偏移
- 你可以 `dt ntdll!_HEAP` 看 heap 管理器的內部結構

這是 Windows internals 研究和 exploit 開發的一個巨大優勢，沒有對應的 Linux 等價物（因為 glibc 是開源的你可以直接讀 source，但對 closed-source 的 Windows 系統函式庫，這些符號相當珍貴）。

這章把符號系統的機制搞清楚，並把逆向工具鏈整合進「exploit 開發流程」這個框架。

## 先建立直覺：PDB 是什麼，它從哪來

PDB（Program Database）是 MSVC 的 debug info 格式。一個 `.pdb` 檔案對應一個 PE（`.exe`/`.dll`），裡面存：
- 函式名、全域變數名、型別資訊（structures、enums）
- 原始碼檔案路徑和行號對應
- 區域變數名（需要 private symbols，Microsoft 不公開）

Microsoft 向外公開的 **public symbols** 包含：
- 函式名（如 `ntdll!LdrLoadDll`、`kernel32!CreateFileW`）
- 公開的結構型別（`_PEB`、`_TEB`、`_HEAP`、`_HEAP_ENTRY`…）
- 但**不包含**：私有 helper 函式、區域變數名、原始碼行號

Public symbols 已經足夠讓你「讀懂」ntdll 在做什麼，並在 WinDbg 裡直接查結構欄位。對 exploit 開發來說，這通常夠用了。

## PE Debug Directory：PDB 路徑存在哪裡

PE 有 16 個 data directory entries，index 6 是 `IMAGE_DIRECTORY_ENTRY_DEBUG`，它指向一個或多個 `IMAGE_DEBUG_DIRECTORY` 結構的陣列：

```c
typedef struct _IMAGE_DEBUG_DIRECTORY {
    DWORD Characteristics;  /* 保留 */
    DWORD TimeDateStamp;    /* 編譯時間戳 */
    WORD  MajorVersion;
    WORD  MinorVersion;
    DWORD Type;             /* 除錯資訊格式 */
    DWORD SizeOfData;       /* 除錯資料大小 */
    DWORD AddressOfRawData; /* 除錯資料 RVA */
    DWORD PointerToRawData; /* 除錯資料的 file offset */
} IMAGE_DEBUG_DIRECTORY;   /* 28 bytes */
```

`Type` 的重要值：
- `2` = `IMAGE_DEBUG_TYPE_CODEVIEW`：MSVC PDB 路徑（最常見）
- `16` = `IMAGE_DEBUG_TYPE_REPRO`：reproducible build 戳記
- `20` = `IMAGE_DEBUG_TYPE_EX_DLL_CHARACTERISTICS`：擴充 DLL 特性

當 `Type == 2` 時，`PointerToRawData` 指向一個 **CodeView 記錄**，現代版本的格式是 `RSDS`：

```
RSDS record 結構（以 file offset 為起點）:
Offset  Size  欄位
0       4     Signature ("RSDS")
4       16    GUID（Data1 LE 4B, Data2 LE 2B, Data3 LE 2B, Data4 BE 8B）
20      4     Age（每次 link 遞增）
24      N     PDB 路徑（null-terminated ASCII）
```

### 真實驗證：Python 讀 RSDS

以下 Python 腳本示範如何從 PE 檔案提取 PDB 路徑（本機實際執行）。對 mingw 編譯的 binary，debug directory 不存在（mingw 用 DWARF sections，不用 PDB）：

```python
# D:\tmp_build\read_debug.py
import struct

def read_pe_debug(path):
    with open(path, 'rb') as f:
        data = f.read()
    
    pe_off    = struct.unpack_from('<I', data, 0x3C)[0]
    machine   = struct.unpack_from('<H', data, pe_off+4)[0]
    num_sects = struct.unpack_from('<H', data, pe_off+6)[0]
    opt_sz    = struct.unpack_from('<H', data, pe_off+20)[0]
    opt_off   = pe_off + 24
    magic     = struct.unpack_from('<H', data, opt_off)[0]
    is64      = (magic == 0x020B)
    
    dd_base   = opt_off + (112 if is64 else 96)   # 16 個 data dir entries 從這裡開始
    
    # Data dir index 3 = Exception (.pdata)
    exc_rva = struct.unpack_from('<I', data, dd_base + 3*8)[0]
    exc_sz  = struct.unpack_from('<I', data, dd_base + 3*8 + 4)[0]
    
    # Data dir index 6 = Debug
    dbg_rva = struct.unpack_from('<I', data, dd_base + 6*8)[0]
    dbg_sz  = struct.unpack_from('<I', data, dd_base + 6*8 + 4)[0]
    
    print("  Exception Dir: RVA=0x%X, %d RUNTIME_FUNCTION entries" %
          (exc_rva, exc_sz//12 if exc_rva else 0))
    
    if not dbg_rva:
        print("  Debug Dir: NOT PRESENT (mingw DWARF? stripped?)")
        return
    
    # Map RVA to file offset via sections
    sect_off = opt_off + opt_sz
    def rva2file(rva):
        for i in range(num_sects):
            s = sect_off + i*40
            vaddr  = struct.unpack_from('<I', data, s+12)[0]
            vsz    = struct.unpack_from('<I', data, s+16)[0]
            rawoff = struct.unpack_from('<I', data, s+20)[0]
            rawsz  = struct.unpack_from('<I', data, s+24)[0]
            if vaddr <= rva < vaddr + max(vsz, rawsz):
                return rawoff + (rva - vaddr)
        return None
    
    foff = rva2file(dbg_rva)
    n = dbg_sz // 28
    for i in range(n):
        e = foff + i*28
        dbg_type = struct.unpack_from('<I', data, e+12)[0]
        data_sz  = struct.unpack_from('<I', data, e+16)[0]
        data_raw = struct.unpack_from('<I', data, e+24)[0]  # file offset
        if dbg_type == 2 and data_raw and data_raw + data_sz <= len(data):
            cv = data[data_raw:data_raw+data_sz]
            if cv[:4] == b'RSDS':
                d1 = struct.unpack_from('<I', cv, 4)[0]
                d2 = struct.unpack_from('<H', cv, 8)[0]
                d3 = struct.unpack_from('<H', cv, 10)[0]
                d4 = cv[12:20]
                guid = "%08X-%04X-%04X-%s-%s" % (
                    d1, d2, d3,
                    d4[:2].hex().upper(), d4[2:].hex().upper()
                )
                age  = struct.unpack_from('<I', cv, 20)[0]
                pdb  = cv[24:].split(b'\x00')[0].decode('ascii', 'replace')
                print("  Debug Dir: RSDS (MSVC PDB)")
                print("    GUID: %s" % guid)
                print("    Age:  %d" % age)
                print("    PDB:  %s" % pdb)
                print("  Symbol server URL:")
                pdb_name = pdb.split('\\')[-1].split('/')[-1]
                print("    https://msdl.microsoft.com/download/symbols/%s/%s%d/%s" %
                      (pdb_name, guid.replace('-',''), age, pdb_name))
```

**本機實際執行結果**（mingw x64 binary）：

```
=== D:\tmp_build\debug_hello_nodebug.exe (mingw, no -g) ===
  Exception Dir: RVA=0x5000, 44 RUNTIME_FUNCTION entries
  Debug Dir: NOT PRESENT (mingw DWARF? stripped?)

=== D:\tmp_build\debug_hello_debug.exe (mingw, -g) ===
  Exception Dir: RVA=0x5000, 44 RUNTIME_FUNCTION entries
  Debug Dir: NOT PRESENT (mingw DWARF? stripped?)
```

mingw 把 DWARF 資訊直接嵌在 PE 節（`.debug_info`、`.debug_line`、`.debug_str`…），**不使用** PE debug directory。這是 mingw 和 MSVC 最顯著的差異之一：

```
MSVC 路徑：
  PE Debug Directory (index 6)
    └─ IMAGE_DEBUG_DIRECTORY[0].Type = 2 (CODEVIEW)
         └─ RSDS record → "C:\build\project.pdb"
              └─ symbol server 有就自動下載 .pdb

mingw 路徑：
  .debug_info  section  ← DWARF Compilation Unit headers
  .debug_str   section  ← 字串表（函式名、路徑）
  .debug_line  section  ← 行號資訊
  .debug_frame section  ← CFI（Call Frame Information，用於 unwinding）
  （無 PE debug directory entry，WinDbg 讀不到）
```

> **對照 Linux**：Linux ELF 的 `.debug_*` sections 和 mingw PE 裡的幾乎一樣（DWARF 格式相同），因為 GCC 在兩個平台用同一套 DWARF emitter。MSVC 的 PDB 是 Microsoft 專有格式，gdb/readelf 讀不了，需要 LLVM 的 `llvm-pdbutil` 或 Windows 的 `cvdump`。

## `_NT_SYMBOL_PATH`：symbol server 的自動下載機制

WinDbg（和 cdb）靠環境變數 `_NT_SYMBOL_PATH` 知道去哪找 PDB：

```
標準格式：
  srv*<local_cache>*<symbol_server_URL>

本課設定：
  srv*C:\symbols*https://msdl.microsoft.com/download/symbols
```

當 WinDbg 需要一個符號（例如 `dt ntdll!_PEB`）時：
1. 從 ntdll.dll 的 RSDS record 讀出 PDB GUID 和 Age
2. 拼出 symbol server URL：`https://msdl.microsoft.com/download/symbols/ntdll.pdb/{GUID}{Age}/ntdll.pdb`
3. 先查 `C:\symbols\ntdll.pdb\{GUID}{Age}\ntdll.pdb` 是否已快取
4. 沒有就下載並快取

**為什麼 GUID + Age 而不是版本號？**

因為 Windows Update 頻繁更新 DLL，同樣的 ntdll.dll 版本號可能因 patch 而有細微差異，但 GUID 保證唯一對應到一次具體的 link 操作。Age 在每次 link 時遞增，用來區分同一個 GUID 的不同 link 產物（理論上不應發生，但保險起見）。

### `dt` 命令：透視系統結構

有了 symbols，WinDbg 的 `dt`（display type）命令就是一把透視鏡：

> **未實測，理論預期**：以下命令在有 `_NT_SYMBOL_PATH` 的 cdb/WinDbg 下執行。

```
0:000> dt ntdll!_PEB
   +0x000 InheritedAddressSpace : UChar
   +0x001 ReadImageFileExecOptions : UChar
   +0x002 BeingDebugged    : UChar
   +0x003 BitField         : UChar
   +0x004 Mutant           : Ptr64 Void
   +0x010 ImageBaseAddress : Ptr64 Void
   +0x018 Ldr              : Ptr64 _PEB_LDR_DATA
   +0x020 ProcessParameters : Ptr64 _RTL_USER_PROCESS_PARAMETERS
   +0x028 SubSystemData    : Ptr64 Void
   ...（共約 60+ 欄位）

0:000> dt ntdll!_TEB
   +0x000 NtTib            : _NT_TIB
   +0x038 EnvironmentPointer : Ptr64 Void
   +0x040 ClientId         : _CLIENT_ID
   ...

0:000> dt ntdll!_HEAP
   +0x000 Segment          : _HEAP_SEGMENT
   +0x000 Entry            : _HEAP_ENTRY
   +0x010 SegmentSignature : Uint4B
   +0x014 SegmentFlags     : Uint4B
   +0x018 SegmentListEntry : _LIST_ENTRY
   ...
```

這讓你不需要對 ntdll.dll 做深度逆向，就能知道 `PEB+0x02` 是 `BeingDebugged`、`PEB+0x10` 是 `ImageBaseAddress`。這正是 Ch 5 裡 PEB walk 的理論依據。

**從 exploit 開發角度的價值**：在開發針對 Windows 系統元件的 exploit 時，知道確切的結構偏移至關重要——而 public symbols 直接告訴你，省掉了大量逆向工作。

## PE 檢視工具鏈

### objdump（本機可用，真實輸出）

mingw 附帶的 `objdump` 能讀 PE 格式，是本課目前最常用的 PE 工具：

```console
# 看 DLL characteristics（緩解旗標）
$ objdump -p target.exe | grep -E "DllChar|DYNAMIC|ENTROPY|NX|GUARD"
DllCharacteristics  00000160
                    HIGH_ENTROPY_VA
                    DYNAMIC_BASE
                    NX_COMPAT

# 看 sections（含 .pdata/.xdata）
$ objdump -h target.exe
Idx Name          Size      VMA               LMA
  0 .text         000017f8  0000000140001000  ...
  3 .pdata        00000210  0000000140005000  ...  ← x64 exception table
  4 .xdata        00000198  0000000140006000  ...  ← unwind info

# 看 data directories（看 Exception Dir 在哪）
$ objdump -p target.exe | grep -A1 "Exception Directory"
Entry 3 0000000000005000 00000210 Exception Directory [.pdata]

# 看 import table（哪些 DLL、哪些函式）
$ objdump -p target.exe | grep "DLL Name"
DLL Name: KERNEL32.dll
DLL Name: api-ms-win-crt-stdio-l1-1-0.dll

# 看 raw hex（驗算結構偏移）
$ objdump --section=.pdata -s target.exe
```

**本機實際驗證**——一個 mingw x64 hello world 的 PE metadata：

```
$ objdump -p D:\tmp_build\debug_hello_nodebug.exe

pei-x86-64 format
AddressOfEntryPoint  0x00000000000013e0
ImageBase            0x0000000140000000
DllCharacteristics   0x0160
  HIGH_ENTROPY_VA    (0x0020)
  DYNAMIC_BASE       (0x0040)
  NX_COMPAT          (0x0100)

Data Directories:
  Entry 1: Import Directory  [.idata]  0x8000  size=0x7c4
  Entry 3: Exception Directory [.pdata] 0x5000  size=0x210   ← x64 必要項目
  Entry 6: Debug Directory           0x0000  size=0      ← mingw 不用這個
  Entry c: IAT Directory             0x8230  size=0x178

Sections: .text .data .rdata .pdata .xdata .bss .idata .CRT .tls .rsrc .reloc
          .debug_aranges .debug_info .debug_abbrev .debug_line .debug_frame
          .debug_str .debug_line_str .debug_loclists .debug_rnglists
```

### dumpbin（MSVC，未實測）

> **未實測**：需要 MSVC C++ workload。裝好後：

```bat
REM 看 Optional Header 包括 DLL characteristics
dumpbin /headers target.exe

REM 看 Load Config（CFG function table、SafeSEH 等）
dumpbin /loadconfig target.exe
REM 輸出包括：
REM   Guard Flags: 00010500
REM     CF Instrumented
REM     FID table present
REM   Guard CF Function Table: 00000000
REM   Guard CF Function Count: 0

REM 看所有 import
dumpbin /imports target.exe

REM 看 exports（DLL 用）
dumpbin /exports ntdll.dll

REM 看 disassembly
dumpbin /disasm target.exe

REM 搜 ROP gadgets 前先看 sections
dumpbin /sections target.exe
```

`dumpbin /loadconfig` 的輸出包含 CFG function table 的 RVA 和大小——這是判斷「這個 binary 有沒有 CFG、CFG 保護了哪些 indirect call target」的主要來源（Ch 32 深講）。

### PE-bear

[PE-bear](https://github.com/hasherezade/pe-bear)（hasherezade 出品）是一個互動式 PE 檢視器，GUI 操作、可視化每個欄位：

```
功能：
- 完整的 PE header 檢視（可 hex + 結構並排）
- Data directories 展開（可直接跳轉到各目錄）
- Import/Export 完整列表（可搜索）
- Disassembly view（用 Capstone）
- 可對比兩個 PE（patch diffing 的第一步）
- Rich header 解析（MSVC 版本資訊）
```

最有用的場景：**patch diffing**。拿到 Patch Tuesday 補丁前後的兩個 DLL，用 PE-bear 比對 section sizes、import 變化、code 差異（和 BinDiff/Diaphora 配合），快速定位被修的函式。Ch 43 patch diffing 專章會用到。

### CFF Explorer

[CFF Explorer](https://ntcore.com/?page_id=388)（NTCore 出品）功能和 PE-bear 類似，但有一個殺手功能：**可以直接在 GUI 裡修改 PE 欄位並存回去**（例如把 `DllCharacteristics` 的 `NX_COMPAT` 位清掉，把 `/DYNAMICBASE` 關掉）。在開發 exploit 的早期階段，你需要一個「可控制緩解狀態的靶」，CFF Explorer 讓你直接改標頭而不用重新編譯。

## IDA 讀帶 PDB 的 PE

IDA（Interactive DisAssembler）是業界標準的逆向工具，IDA 9.x（2024 發布）的 PE/PDB 整合流程：

1. **載入 PE**：File → Open → 選 `.exe` 或 `.dll`
2. **PDB 載入提示**：IDA 會詢問「detected PDB file, download from symbol server?」，或讓你指定本地路徑
3. **`_NT_SYMBOL_PATH` 整合**：IDA 讀 `_NT_SYMBOL_PATH` 環境變數，自動從 Microsoft symbol server 下載對應 PDB
4. **結果**：函式名、全域變數名自動套用；結構偏移在 `View → Open Subviews → Local Types` 可查

> **未實測**：本機無 IDA，以下為標準操作說明。

**FLIRT（Fast Library Identification and Recognition Technology）**：

IDA 的 FLIRT 是一個 signature 庫，能識別常見 library 函式（CRT、STL、Windows SDK 的 boilerplate）並自動命名。對 stripped binary，FLIRT 能把 `sub_140001234` 自動識別為 `_printf` 或 `_malloc_impl`。這在分析沒有 PDB 的舊版 Windows 元件時很有用。

**IDA + symbol server 的價值**：分析 ntdll.dll 時，IDA 自動載入 public symbols，所有函式都有名字，結構欄位也對得上。相比之下，分析 glibc.so 就得靠 DWARF 或自己查 source code。

### IAT 自動命名

IDA 解析 Import Address Table（IAT）後，把所有 imported 函式的呼叫點都命名為 `<dll>_<func>`（例如 `KERNEL32_VirtualAlloc`）。這讓你在 disassembly 裡直接看到「這個 call 是在呼叫 VirtualAlloc」，不需要手動查 import table。

## Ghidra 讀帶 PDB 的 PE

Ghidra（NSA 開源的免費逆向工具）有完整的 Windows PE/PDB 支援：

1. **載入 PE**：File → Import File → 選 `.exe`/`.dll`
2. **PDB 載入**：有兩個路徑：
   - 直接把 `.pdb` 放在 binary 同目錄，Ghidra 自動偵測
   - File → Load PDB File，手動指定
3. **symbol server**：Ghidra 的 `SymbolServerService` 可設定 `_NT_SYMBOL_PATH`，自動下載

Ghidra 的優勢：
- 完全免費，適合分享和團隊協作
- Decompiler 品質（P-Code + decompiler）對 C 程式相當好
- 有 Python/Java scripting API（`ghidra.app.script`）
- 支援 Binary Ninja、IDA 的 idb/i64 import（有外掛）

Ghidra 的劣勢：
- Decompiler 對 C++ RTTI/vtable、SEH frame 的處理不如 IDA
- 速度比 IDA 慢（分析大型 DLL 要等較久）

### 實際工作流程建議

在 exploit 開發裡，工具的使用順序通常是：

```
1. 目標初步偵察
   objdump -p target.exe        ← 看 DllCharacteristics（開了什麼緩解）
   winchecksec target.exe       ← 一行看全部（比 objdump 快）
   PE-bear / CFF Explorer       ← 視覺化確認

2. 結構理解（需要 symbols）
   WinDbg: dt ntdll!_PEB        ← 查系統結構偏移
   WinDbg: !exchain             ← 看 SEH chain 狀態
   WinDbg: !heap                ← 看 heap 狀態

3. 功能逆向
   IDA / Ghidra 開目標 binary   ← 帶 PDB 自動命名
   IDA: FLIRT + PDB             ← 識別 CRT 函式
   Ghidra: decompiler           ← 快速理解函式邏輯

4. 漏洞確認 / exploit 開發
   WinDbg / x64dbg              ← 動態調試
   Python + ctypes              ← 寫 PoC / 驗算偏移
   rp++ / ropper                ← 找 ROP gadgets（PE 格式）
   mona.py                      ← 算 exploit offset、找 gadget
```

## 符號對 exploit 開發的具體價值

以 PEB walk shellcode 為例（Ch 25 的主題）：

```python
# 用 Python + ctypes 從 PEB 找 kernel32 base address（本機可跑邏輯）
# 實際在 shellcode 裡，你要知道哪個偏移有什麼欄位
# Public symbols 告訴你：
#   PEB+0x18: Ldr (x64) -> _PEB_LDR_DATA
#   _PEB_LDR_DATA+0x10: InLoadOrderModuleList -> _LIST_ENTRY
#   _LDR_DATA_TABLE_ENTRY+0x60: DllBase (x64)
#   _LDR_DATA_TABLE_ENTRY+0x48: FullDllName -> _UNICODE_STRING

# 沒有 symbols，你需要靠逆向 ntdll 找這些偏移
# 有了 symbols（dt ntdll!_PEB_LDR_DATA 之類），直接查
```

在 Windows shellcode 裡，「PEB walk 找 kernel32 base」是標準技法。`dt ntdll!_PEB`、`dt ntdll!_PEB_LDR_DATA`、`dt ntdll!_LDR_DATA_TABLE_ENTRY` 這三個命令給你 shellcode 需要的所有偏移。這是 public symbols 在 exploit 開發裡最直接的用途。

## 對比：Windows PDB vs Linux DWARF

| 面向 | Windows PDB（MSVC） | Linux DWARF（GCC/Clang） |
|---|---|---|
| 格式 | Microsoft 專有（`.pdb`） | 開放標準（`.debug_info` 等 sections） |
| 存放位置 | 獨立 `.pdb` 檔，PE 裡只有路徑 | 嵌在 ELF sections 裡（或 `.dSYM` bundle） |
| 識別方式 | PE debug dir → RSDS → GUID+Age | ELF `.note.gnu.build-id` section |
| symbol server | `msdl.microsoft.com`（Microsoft 官方） | `debuginfod.elfutils.org`（開源社群） |
| 公開程度 | 系統 DLL 有 public symbols（不含私有細節） | 開源軟體有完整 symbols（libglibc debug 包） |
| 工具支援 | WinDbg、IDA、dumpbin | gdb、readelf、objdump、llvm-dwarfdump |
| stripped binary | 只剩函式名（public symbols） | 通常完全 strip（ls -lh libc.so.6 看看） |

## 踩雷集錦

1. **「設了 `_NT_SYMBOL_PATH` 但 `dt _PEB` 還是說 symbol not found」**：`dt` 的語法是 `dt ntdll!_PEB`，必須加模組名前綴。只打 `dt _PEB` WinDbg 不知道去哪找。另一個常見原因：防火牆擋住 `msdl.microsoft.com`，symbols 下載失敗但沒有明確錯誤提示；`!sym noisy` 開啟 verbose symbol loading 可看到詳細錯誤。

2. **「IDA 載入 PDB 後有些函式還是沒名字」**：Public symbols 不含 private 函式。ntdll 有很多 internal helper（如 `RtlpAllocateHeapLarge`）沒有在 public symbols 裡，你看到的是它們的位址，沒有名字。這是正常的；你可以根據行為和呼叫上下文手動命名。

3. **「用 objdump 看 PE debug directory 顯示 0（no debug dir）但明明是 MSVC 編的」**：最可能原因是 strip 了。`strip.exe` 或 `link /DEBUG:NONE` 會移除 debug directory entry（但 PDB 可能還在，只是 PE 裡的路徑沒了）。另一個可能：你看的是 release build，MSVC release build 預設 debug info 設定可能不同。

4. **「cdb/WinDbg 找到了符號，但 `dt` 印出的偏移和網路文章說的不一樣」**：Windows 每次 update，系統 DLL 的結構偏移可能改變（加新欄位）。`dt` 印出的是**你這台機器的版本**的正確值。網路文章可能是舊版 Windows 的資料。永遠以 `dt` 的輸出為準，不要硬編文章裡的偏移。這對 exploit 開發來說是個坑：你寫的 exploit 可能在某個 Windows 版本有效，但在另一個版本的結構偏移不同就壞掉了。

5. **「PE-bear / CFF Explorer 改了 DllCharacteristics，靶的行為沒變」**：記得**存檔**後再跑修改過的 binary，不是改在記憶體裡的。另外，某些緩解（如 CFG）除了 `DllCharacteristics` 的旗標，還需要 Load Config 目錄裡的 Guard Flags 和 Function Table 都一起設定才會真正啟用。只改一個地方不夠。

## 進階：再往深一層

### Symbol Server 協定

Symbol server 使用的是微軟定義的 `symsrv.dll` 協定（不是標準 HTTP 目錄列表）。URL 格式：

```
https://msdl.microsoft.com/download/symbols/{filename}/{GUID}{Age}/{filename}
```

例如：
```
https://msdl.microsoft.com/download/symbols/ntdll.pdb/ABC123{...}/1/ntdll.pdb
```

你可以直接在瀏覽器裡試這個 URL（Microsoft 是真的公開的）。或者用 `symchk.exe`（WinDbg 工具包的一部分）批次下載特定版本 Windows 的所有 symbols：

```bat
REM 未實測
symchk /r "C:\Windows\System32" /s "srv*C:\symbols*https://msdl.microsoft.com/download/symbols"
```

### Private Symbols 的偵察

Microsoft 偶爾不小心（或刻意在某個 beta 版本裡）上傳了包含 private symbols 的 PDB。安全研究員有時能從這些 PDB 裡得到比 public symbols 多得多的資訊（包含部分函式的 local var 名）。Ryan Smith 和其他研究員有追蹤這些「意外的 private PDB」的方法，搜尋「windows private symbols leak」可找到相關討論。

### Export table hashing（exploit 裡的 API 解析）

Ch 25 的 shellcode 用 PEB walk 找到 `kernel32.dll` base 後，需要找特定 API（如 `WinExec`）的位址。不能直接用名字字串（太長，而且需要 kernel32），所以傳統 shellcode 用 **export table hashing**：

1. 走 `kernel32` 的 export table（PE 的 data dir index 0）
2. 對每個 exported function name 做 hash（常見算法：ROR13）
3. 比對目標 hash，找到對應的函式 RVA

`dt ntdll!_IMAGE_EXPORT_DIRECTORY` 告訴你 export table 的結構偏移，省掉你查 MSDN 的時間。

## 動手練習

**本機可跑（Python + mingw）**：

1. 用以下命令看兩個 PE 的 debug 資訊差異：

```bash
# mingw 無 PDB
objdump -p D:\tmp_build\debug_hello_nodebug.exe | grep "Debug Directory"

# 如果你有 MSVC 編的 binary（例如系統 DLL），用 Python 腳本讀 RSDS
python3 D:\tmp_build\read_debug.py
```

2. 從 `.pdata` 節驗算 `RUNTIME_FUNCTION` 數量：

```bash
objdump -h D:\tmp_build\debug_hello_debug.exe | grep pdata
# .pdata 大小 / 12 = RUNTIME_FUNCTION 數量
# 0x210 / 12 = 44（驗證和 Data Directory 的 Exception Dir 大小一致）
```

3. 查 IAT：哪些函式被 import？從 `kernel32.dll` 拿了什麼？

```bash
objdump -p D:\tmp_build\debug_hello_debug.exe | grep -A100 "Import Tables"
```

**需要 WinDbg（未實測，裝好後驗證）**：

4. 設定 `_NT_SYMBOL_PATH` 後，在 cdb 裡：
   ```
   dt ntdll!_PEB
   dt ntdll!_HEAP
   dt ntdll!_EXCEPTION_REGISTRATION_RECORD  ← 確認和 Ch 11 說的欄位吻合
   ```

5. 比對 `dt ntdll!_LDR_DATA_TABLE_ENTRY` 的 `DllBase` 偏移，和你在 Ch 5 手動計算的是否一致。

## 本章重點整理

- MSVC PDB 路徑存在 PE debug directory 的 RSDS record（GUID + Age + 路徑）；mingw 用 DWARF sections 嵌在 PE 裡，不走這個機制——WinDbg 讀不到 mingw 的 debug info。
- `_NT_SYMBOL_PATH=srv*C:\symbols*https://msdl.microsoft.com/download/symbols` 讓 WinDbg 自動從 Microsoft symbol server 下載 public symbols；之後 `dt ntdll!_XXX` 可直接透視系統結構偏移。
- PE 工具鏈的定位：objdump/dumpbin 驗 headers、PE-bear 視覺化、CFF Explorer 改 headers、IDA/Ghidra 深度逆向（帶 PDB 自動命名）、WinDbg 動態查結構。
- Public symbols 對 exploit 開發的核心價值：直接得到 `_PEB`、`_HEAP`、`_LDR_DATA_TABLE_ENTRY` 等結構的欄位偏移，省掉大量靜態逆向工作。

## 自我檢核

- [ ] 不看筆記，能說出 PE debug directory 的 RSDS record 包含哪三個識別欄位，以及 symbol server 的 URL 格式
- [ ] 能解釋為什麼 mingw 編的 binary 在 WinDbg 裡看不到函式名（即使加了 `-g`），以及 MSVC 編的 binary 為什麼能
- [ ] 知道 `_NT_SYMBOL_PATH` 沒設時 `dt ntdll!_PEB` 會怎樣，以及如何診斷 symbol 下載失敗
- [ ] 能說出 exploit 開發流程裡，objdump / WinDbg dt / IDA / PE-bear 各在哪個步驟發揮作用
- [ ] 面試被問「Windows 的 public symbols 對安全研究有什麼用？」時，能舉出至少兩個具體例子（結構偏移查詢、PEB walk 的 offset 確認、heap grooming 結構分析…）

## 延伸閱讀

### 官方文件

- **[Symbol Files and Symbol Paths — Microsoft Learn](https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/symbols-and-symbol-files)**
  - **讀哪裡**：「Deferred Symbol Loading」和「Symbol Servers and Symbol Stores」兩節
  - **學什麼**：`_NT_SYMBOL_PATH` 的完整語法（可以有多個 server、多個 cache 路徑）；`!sym noisy` 如何診斷 symbol 載入問題
  - **和本章關聯**：本章給了設定和基本原理；這份是完整規格和 troubleshooting

- **[PE Format — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/debug/pe-format)**
  - **讀哪裡**：「The .debug Section」和「Debug Directories」兩節
  - **學什麼**：`IMAGE_DEBUG_DIRECTORY` 的完整欄位語意、所有的 `Type` 值（CodeView / FPO / MISC / REPRO 等）
  - **和本章關聯**：本章的 RSDS 結構解析是這份規格的一個子集

### 工具

- **[PE-bear — hasherezade](https://github.com/hasherezade/pe-bear)**
  - **這是什麼**：開源、跨平台的 PE 視覺化工具，有 diff 功能
  - **為什麼值得裝**：Patch Tuesday patch diffing（Ch 43）的第一手工具；hasherezade 也是 PE internals 研究的知名人物，她的 blog 和工具都值得關注
  - **前提知識**：本章的 PE 結構（section table、data directories）

- **[winchecksec — Trail of Bits](https://github.com/trailofbits/winchecksec)**
  - **這是什麼**：Windows 版 `checksec`，一行輸出 PE 的所有緩解狀態
  - **為什麼值得裝**：比記 `objdump` 欄位位置快；Trail of Bits 是安全研究領域信得過的組織

### 部落格

- **hasherezade's blog — PE internals 系列** (hasherezade.github.io / hshrzd.wordpress.com)
  - **讀哪裡**：搜尋 "PE file format" 相關文章
  - **學什麼**：PE 格式的實際邊界情況（malformed PE、packers、loader quirks）；比 Microsoft 文件更接近實戰
  - **和本章關聯**：本章只講正常 PE；這個系列補充了 fuzzing 和 malware 常見的非標準 PE

- **j00ru, "Windows x64 system call table"** — j00ru.vexillium.org
  - **讀哪裡**：他關於 symbol server 和 Microsoft private symbols 的文章
  - **學什麼**：如何從 symbol server 推斷 Windows 版本差異；private symbols 偶爾 leak 的歷史案例
  - **和本章關聯**：本章說 public symbols 不含 private 函式；j00ru 的文章說明為什麼研究員有時能得到更多資訊

Part 1（Windows internals）的最後一塊——符號系統——補完了。你現在有工具把任何系統 DLL 的 internal 結構看透，這是 Part 2（heap internals）和 Part 3（exploitation）的基礎設施。

→ [練習 A — 手寫 PE parser + 從 PEB 走 LDR 找 API](./practice-a-pe-parser-peb-walk.md)
