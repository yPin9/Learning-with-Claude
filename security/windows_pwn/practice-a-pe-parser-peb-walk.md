# 練習 A — 手寫 PE parser + 從 PEB 走 LDR 找 API

> **目標**：把 Ch 3–5 學到的結構拼成兩個可執行程式——一個靜態解析任意 PE 檔，一個在執行期從 PEB 走 LDR 模組鏈找到 KERNEL32.DLL 並手動解析 Export Table 定位任意 API，不呼叫 `GetProcAddress`。這是 Windows shellcode 的核心原語，也是你能不能真的「讀懂 PE + 讀懂 PEB」的試金石。

---

## 背景動機

Ch 3 教了 PE 格式：DOS Header → NT Headers → Section Headers → Import/Export Directory，每個結構都講了。Ch 5 教了 PEB walk：`GS:[0x60]` → `PEB.Ldr` → `InLoadOrderModuleList` → `DllBase + BaseDllName` → Export Directory → 函式 VA。這兩個練習的目的是**讓你把書面知識變成可以跑的程式**。

為什麼重要？以下三個場景都需要這兩個技能：

1. **shellcode 開發**（Ch 25）：shellcode 是 PIC（位置無關碼），不能有 import table，所以必須在執行期用 PEB walk 找 `VirtualAlloc`/`LoadLibraryA`；找到之後要走 Export Directory，不能呼叫 `GetProcAddress`（因為你沒有它的地址）
2. **exploit 開發時確認目標狀態**：拿到一個 PE，第一件事是用 parser 確認 ASLR/DEP/CFG 開關、IAT 有哪些函式可以當 write primitive 目標
3. **逆向與 CTF**：不依賴 IDA/Ghidra，自己的 parser 讓你對 PE 結構有直覺，看到十六進位就知道在哪裡

---

## 任務規格

### Part 1 — PE 靜態 parser

寫一個程式（建議 Python，但 C 也可以），接受 PE 檔路徑作為命令列引數，解析並印出：

**必須完成的功能（驗收標準）：**

| 功能 | 驗收標準 |
|---|---|
| DOS Header | 印出 `e_magic`（驗證是 `MZ`）和 `e_lfanew` |
| FILE_HEADER | Machine（0x8664/0x014C）、NumberOfSections、Characteristics |
| OPTIONAL_HEADER | Magic（PE32/PE32+）、`ImageBase`、`EntryPoint RVA + VA`、`SizeOfImage`、`Subsystem` |
| DllCharacteristics | 以旗標名稱列出：`DYNAMIC_BASE`、`NX_COMPAT`、`GUARD_CF` 等 |
| Section Headers | 每個 section：名稱、`VirtualAddress`、`VirtualSize`、`RawOffset`、`RawSize`、rwx 屬性 |
| Import Directory | 列出所有 Import DLL 名稱，每個 DLL 下列出所有 import 函式名稱（或序號）|
| Export Directory | 印出前 8 個 export（名稱、序號、RVA） |

**輸入**：PE 檔路徑（`.exe` 或 `.dll`，PE32+，x64）

**輸出**：格式化的結構解析，可以選任何你覺得清楚的格式

**驗收**：對 `C:\Windows\System32\kernel32.dll` 跑，import 和 export 要和 `objdump -p` 一致

---

### Part 2 — Runtime PEB → LDR walk

寫一個程式，在自己的行程內：

1. 取得 PEB 基址（用 `NtQueryInformationProcess` 或直接讀 `GS:[0x60]`；Python 用 ctypes 呼叫 ntdll）
2. 從 `PEB + 0x18` 取 `Ldr`（`PEB_LDR_DATA*`）
3. 走 `InLoadOrderModuleList`（`Ldr + 0x10`），印出每個模組的名稱和 `DllBase`
4. 找到 `KERNEL32.DLL`，記下 `DllBase`
5. **手動解析 KERNEL32.DLL 的 Export Directory**，找到以下函式的 VA：
   - `GetProcAddress`
   - `LoadLibraryA`
   - `VirtualAlloc`
   - `VirtualProtect`
6. 用正規的 `GetProcAddress` + `GetModuleHandleW` 取得同樣函式的 VA，印出比對結果，驗證兩組完全一致

**關鍵限制**：步驟 5 解析函式 VA 的過程中，**不能呼叫 `GetProcAddress`**——這正是 shellcode 的工作方式。

**驗收**：每個函式的 `manual` VA 要和 `GetProcAddress` 回傳值完全一致，程式最後印出 `ALL MATCH`。

---

## 期望輸出範例

### Part 1 — 對 `kernel32.dll` 的輸出（截節）

```
=== PE Parser: kernel32.dll (836232 bytes) ===

[DOS Header]
  e_magic   = 0x5A4D  ('MZ')
  e_lfanew  = 0x00000100  (NT Headers file offset)

[NT Headers - FILE_HEADER]
  Machine           = 0x8664  (AMD64 (x86-64))
  NumberOfSections  = 8
  TimeDateStamp     = 0xB7DAF818
  SizeOfOptHeader   = 240
  Characteristics   = 0x2022
    [EXECUTABLE_IMAGE | DLL]

[NT Headers - OPTIONAL_HEADER]
  Magic             = 0x020B  (PE32+ (64-bit))
  ImageBase         = 0x0000000180000000
  EntryPoint RVA    = 0x0002E1A0  -> VA=0x000000018002E1A0
  SectionAlignment  = 0x1000
  FileAlignment     = 0x1000
  SizeOfImage       = 0xC9000
  Subsystem         = 3  (Windows CUI)
  DllCharacteristics= 0x4160
    + HIGH_ENTROPY_VA
    + DYNAMIC_BASE (ASLR)
    + NX_COMPAT (DEP)
    + GUARD_CF (CFG)

[Section Headers] (8 sections)
  Name         VirtAddr   VirtSize   RawOff  RawSize  Attr
  .text      0x00001000 0x00084B24 0x001000 0x085000  r-x
  .rdata     0x00087000 0x00037C00 0x087000 0x038000  r--
  .data      0x000BF000 0x00001648 0x0BF000 0x001000  rw-
  ...

[Import Directory]
  DLL: api-ms-win-core-rtlsupport-l1-1-0.dll
    hint=0x0005  RtlCompareMemory
    hint=0x000D  RtlRaiseException
    ...
  DLL: ntdll.dll
    hint=0x0636  RtlUnicodeStringToInteger
    ...

[Export Directory] OrdinalBase=1  Functions=1693  Names=1693
  [   0] ord=1      RVA=0x000A8F9F  AcquireSRWLockExclusive
  [   1] ord=2      RVA=0x000A8FD5  AcquireSRWLockShared
  [   2] ord=3      RVA=0x00037AF0  ActivateActCtx
  ...
```

### Part 2 — PEB walk 的輸出

```
[1] NtQueryInformationProcess status: 0x00000000
    PEB @ 0x00000008D7E1F000
[2] PEB.Ldr @ 0x00007FFA85B918C0
    InLoadOrderModuleList head @ 0x00007FFA85B918D0
[3] Walking InLoadOrderModuleList:
    [0] python.exe                          @ 0x00007FF6C5A60000
    [1] ntdll.dll                           @ 0x00007FFA859C0000
    [2] KERNEL32.DLL                        @ 0x00007FFA849A0000
         ^--- found KERNEL32.DLL!
    [3] KERNELBASE.dll                      @ 0x00007FFA830D0000
    ...

    KERNEL32.DLL base = 0x00007FFA849A0000

[5] Resolving exports (manual EAT walk, no GetProcAddress):
    GetProcAddress         -> 0x00007FFA849D3D00
    LoadLibraryA           -> 0x00007FFA849E2CE0
    VirtualAlloc           -> 0x00007FFA849D3D20
    VirtualProtect         -> 0x00007FFA849D8200

[6] Verification via GetProcAddress:
    GetProcAddress          manual=0x00007FFA849D3D00  GetProcAddress=0x00007FFA849D3D00  MATCH
    LoadLibraryA            manual=0x00007FFA849E2CE0  GetProcAddress=0x00007FFA849E2CE0  MATCH
    VirtualAlloc            manual=0x00007FFA849D3D20  GetProcAddress=0x00007FFA849D3D20  MATCH
    VirtualProtect          manual=0x00007FFA849D8200  GetProcAddress=0x00007FFA849D8200  MATCH

Result: ALL MATCH - PEB walk + Export parse correct!
```

---

## 如果你卡住了

1. **RVA → 文件偏移轉換**：Data Directory 裡的 RVA 不是文件偏移。必須先找到包含該 RVA 的 section（比較 `VirtualAddress` 和 `VirtualAddress + SizeOfRawData`），再用 `file_offset = RawOffset + (RVA - VirtualAddress)` 換算。忘記這步是 Part 1 最常見的卡點。
2. **LDR_DATA_TABLE_ENTRY 的正確偏移**：`InLoadOrderLinks.Flink`（list entry 指標）在 `+0x00`，但 `DllBase` 在 `+0x30`，`BaseDllName`（UNICODE_STRING）在 `+0x58`。`UNICODE_STRING.Length` 是 **bytes** 長度，不是字元數；`Buffer` 指標在 `+0x08`（不是 +0x04）——因為 x64 下指標是 8 bytes。
3. **Export Directory 查找的三張表**：EAT（`AddressOfFunctions`，DWORD[]）、名稱表（`AddressOfNames`，DWORD[]）、序號表（`AddressOfNameOrdinals`，WORD[]）是三個獨立陣列，名稱表和序號表的 index `i` 對應同一個函式：`ordinal = NameOrdinals[i]`；`func_rva = EAT[ordinal]`（注意 ordinal 是相對於 `OrdinalBase` 的索引，但 `EAT[ordinal]` 是直接用 ordinal 當 index，不需要減 OrdinalBase）。

---

## 實作步驟建議

### Part 1 步驟

**Step 1：DOS Header + e_lfanew**

```python
import struct, sys

def parse_pe(path):
    with open(path, 'rb') as f:
        data = f.read()
    assert data[:2] == b'MZ'
    e_lfanew = struct.unpack_from('<I', data, 0x3C)[0]
    assert data[e_lfanew:e_lfanew+4] == b'PE\x00\x00'
    print(f"e_lfanew = 0x{e_lfanew:X}")
```

**Step 2：FILE_HEADER + OPTIONAL_HEADER**

```python
fh = e_lfanew + 4          # NT Signature (4B) 之後就是 FILE_HEADER
oh = fh + 20               # FILE_HEADER 是 20 bytes

machine      = struct.unpack_from('<H', data, fh+0)[0]
num_sections = struct.unpack_from('<H', data, fh+2)[0]
size_opt_hdr = struct.unpack_from('<H', data, fh+16)[0]

magic      = struct.unpack_from('<H', data, oh+0)[0]   # 0x020B = PE32+
entry_rva  = struct.unpack_from('<I', data, oh+16)[0]  # 相對 ImageBase 的 RVA
image_base = struct.unpack_from('<Q', data, oh+24)[0]  # PE32+ 的 8-byte ImageBase
dll_chars  = struct.unpack_from('<H', data, oh+70)[0]  # DllCharacteristics
```

**Step 3：DataDirectory + Section Headers**

```python
dd_start  = oh + 112           # OptionalHeader + 0x70 是 DataDirectory[0] 的位置
sec_tbl   = fh + 20 + size_opt_hdr  # Section Table 緊跟在 OptionalHeader 之後

# 每個 section header 是 40 bytes
for i in range(num_sections):
    s = sec_tbl + i * 40
    name    = data[s:s+8].rstrip(b'\x00').decode('ascii', 'replace')
    vsz     = struct.unpack_from('<I', data, s+8)[0]   # VirtualSize
    rva     = struct.unpack_from('<I', data, s+12)[0]  # VirtualAddress
    raw_sz  = struct.unpack_from('<I', data, s+16)[0]  # SizeOfRawData
    raw_off = struct.unpack_from('<I', data, s+20)[0]  # PointerToRawData
    chars   = struct.unpack_from('<I', data, s+36)[0]  # Characteristics
```

**Step 4：rva_to_offset helper（關鍵）**

先把所有 section 存起來，實作 RVA 轉換函式：

```python
sections = [...]  # list of (name, vsz, rva, raw_sz, raw_off, chars)

def rva_to_offset(rva, sections):
    for (name, vsz, rva_start, raw_sz, raw_off, chars) in sections:
        if rva_start <= rva < rva_start + max(vsz, raw_sz):
            return raw_off + (rva - rva_start)
    return None  # RVA 在 header 範圍內或無效
```

**Step 5：Import Directory（DataDirectory[1]）**

```python
imp_rva = struct.unpack_from('<I', data, dd_start + 1*8)[0]
desc_off = rva_to_offset(imp_rva, sections)
# 走 IMAGE_IMPORT_DESCRIPTOR 陣列（每個 20 bytes，全 0 結尾）
while True:
    orig_thunk = struct.unpack_from('<I', data, desc_off + 0)[0]
    name_rva   = struct.unpack_from('<I', data, desc_off + 12)[0]
    if orig_thunk == 0 and name_rva == 0:
        break  # null terminator
    # 走 ILT（IMAGE_THUNK_DATA64，每個 8 bytes，全 0 結尾）
    ...
    desc_off += 20
```

---

### Part 2 步驟

**Step 1：取 PEB 基址**

```python
import ctypes, struct, os

class PROCESS_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ('Reserved1',       ctypes.c_ulonglong),
        ('PebBaseAddress',  ctypes.c_ulonglong),   # 就是 PEB 的 VA
        ('Reserved2',       ctypes.c_ulonglong * 2),
        ('UniqueProcessId', ctypes.c_ulonglong),
        ('Reserved3',       ctypes.c_ulonglong),
    ]

ntdll = ctypes.WinDLL('ntdll')
pbi   = PROCESS_BASIC_INFORMATION()
ntdll.NtQueryInformationProcess(hProc, 0,
    ctypes.byref(pbi), ctypes.sizeof(pbi), None)
peb_addr = pbi.PebBaseAddress
```

**Step 2：用 ReadProcessMemory 讀記憶體**

```python
kernel32 = ctypes.WinDLL('kernel32')
hProc = kernel32.OpenProcess(0x1F0FFF, False, os.getpid())

def rpmem(addr, size):
    buf = (ctypes.c_ubyte * size)()
    nr  = ctypes.c_size_t()
    kernel32.ReadProcessMemory(hProc, ctypes.c_void_p(addr),
                               buf, size, ctypes.byref(nr))
    return bytes(buf)

def u64(a): return struct.unpack_from('<Q', rpmem(a, 8))[0]
def u32(a): return struct.unpack_from('<I', rpmem(a, 4))[0]
```

**Step 3：走 LDR 模組鏈**

```python
ldr_addr  = u64(peb_addr + 0x18)     # PEB.Ldr
list_head = ldr_addr + 0x10          # InLoadOrderModuleList（LIST_ENTRY head）
entry     = u64(list_head)           # 第一個 LDR_DATA_TABLE_ENTRY 的 Flink

while entry != list_head:
    dll_base  = u64(entry + 0x30)    # DllBase
    # BaseDllName 在 +0x58 是 UNICODE_STRING: Length(2B), MaxLen(2B), pad(4B), Buffer(8B)
    name_len  = struct.unpack_from('<H', rpmem(entry + 0x58, 2))[0]
    name_ptr  = u64(entry + 0x58 + 8)
    name      = rpmem(name_ptr, name_len).decode('utf-16-le', errors='replace')
    print(f"  {name} @ 0x{dll_base:016X}")
    entry = u64(entry)               # 跟 Flink 走到下一個 entry
```

**Step 4：手動解析 Export Directory**

```python
# 用 LDR 找到的 k32_base，讀 PE header 找 Export Directory
hdr     = rpmem(k32_base, 0x400)
e_lfanew = struct.unpack_from('<I', hdr, 0x3C)[0]
oh_off   = e_lfanew + 4 + 20
exp_rva  = struct.unpack_from('<I', hdr, oh_off + 112)[0]  # DataDirectory[0]

exp      = rpmem(k32_base + exp_rva, 40)
num_names = struct.unpack_from('<I', exp, 24)[0]
eat_rva   = struct.unpack_from('<I', exp, 28)[0]
names_rva = struct.unpack_from('<I', exp, 32)[0]
ords_rva  = struct.unpack_from('<I', exp, 36)[0]

# 線性掃描名稱表，找目標函式
for i in range(num_names):
    n_rva   = u32(k32_base + names_rva + i * 4)
    nbuf    = rpmem(k32_base + n_rva, len(func_name) + 1)
    name    = nbuf[:nbuf.find(b'\x00')].decode('ascii', 'replace')
    if name == func_name:
        ordinal  = struct.unpack_from('<H', rpmem(k32_base + ords_rva + i*2, 2))[0]
        func_rva = u32(k32_base + eat_rva + ordinal * 4)
        return k32_base + func_rva
```

---

## 完整參考解答

**先自己寫！不看不學。** 卡超過 30 分鐘再開。

<details>
<summary>展開 Part 1 — pe_parser_full.py（本機 Python 3.12 實跑通過）</summary>

```python
#!/usr/bin/env python3
"""pe_parser_full.py — 完整 PE32+ 靜態解析器"""
import struct, sys, os

DLLCHAR_FLAGS = [
    (0x0020, 'HIGH_ENTROPY_VA'),
    (0x0040, 'DYNAMIC_BASE (ASLR)'),
    (0x0080, 'FORCE_INTEGRITY'),
    (0x0100, 'NX_COMPAT (DEP)'),
    (0x0400, 'NO_SEH'),
    (0x0800, 'NO_BIND'),
    (0x4000, 'GUARD_CF (CFG)'),
    (0x8000, 'TERMINAL_SERVER_AWARE'),
]

def rva_to_offset(rva, sections):
    for (name, vsz, rva_start, raw_sz, raw_off, chars) in sections:
        if rva_start <= rva < rva_start + max(vsz, raw_sz):
            return raw_off + (rva - rva_start)
    return None

def read_cstring(data, offset, maxlen=256):
    end = data.find(b'\x00', offset, offset + maxlen)
    if end == -1:
        return data[offset:offset+maxlen].decode('ascii', 'replace')
    return data[offset:end].decode('ascii', 'replace')

def parse_pe(path):
    with open(path, 'rb') as f:
        data = f.read()

    print(f"=== PE Parser: {os.path.basename(path)} ({len(data)} bytes) ===\n")

    # DOS Header
    assert data[:2] == b'MZ', "Not a valid PE"
    e_lfanew = struct.unpack_from('<I', data, 0x3C)[0]
    print(f"[DOS Header]")
    print(f"  e_magic   = 0x{struct.unpack_from('<H', data, 0)[0]:04X}  ('MZ')")
    print(f"  e_lfanew  = 0x{e_lfanew:08X}")
    print()

    assert data[e_lfanew:e_lfanew+4] == b'PE\x00\x00'

    fh = e_lfanew + 4
    machine      = struct.unpack_from('<H', data, fh+0)[0]
    num_sections = struct.unpack_from('<H', data, fh+2)[0]
    timestamp    = struct.unpack_from('<I', data, fh+4)[0]
    size_opt_hdr = struct.unpack_from('<H', data, fh+16)[0]
    file_chars   = struct.unpack_from('<H', data, fh+18)[0]
    machine_str  = {0x8664:'AMD64 (x86-64)', 0x014C:'I386 (x86-32)',
                    0xAA64:'ARM64'}.get(machine, f'0x{machine:04X}')

    print(f"[FILE_HEADER]")
    print(f"  Machine           = 0x{machine:04X}  ({machine_str})")
    print(f"  NumberOfSections  = {num_sections}")
    print(f"  TimeDateStamp     = 0x{timestamp:08X}")
    print(f"  SizeOfOptHeader   = {size_opt_hdr}")
    fc_bits = ((['EXECUTABLE_IMAGE'] if file_chars & 0x0002 else []) +
               (['DLL']              if file_chars & 0x2000 else []) +
               (['DEBUG_STRIPPED']   if file_chars & 0x0200 else []))
    print(f"  Characteristics   = 0x{file_chars:04X}  [{' | '.join(fc_bits)}]")
    print()

    oh    = fh + 20
    magic = struct.unpack_from('<H', data, oh+0)[0]
    is64  = (magic == 0x020B)
    entry_rva  = struct.unpack_from('<I', data, oh+16)[0]
    if is64:
        image_base = struct.unpack_from('<Q', data, oh+24)[0]
        sec_align  = struct.unpack_from('<I', data, oh+32)[0]
        file_align = struct.unpack_from('<I', data, oh+36)[0]
        image_size = struct.unpack_from('<I', data, oh+56)[0]
        hdr_size   = struct.unpack_from('<I', data, oh+60)[0]
        subsystem  = struct.unpack_from('<H', data, oh+68)[0]
        dll_chars  = struct.unpack_from('<H', data, oh+70)[0]
        num_dd     = struct.unpack_from('<I', data, oh+108)[0]
        dd_start   = oh + 112
    else:
        image_base = struct.unpack_from('<I', data, oh+28)[0]
        sec_align  = struct.unpack_from('<I', data, oh+32)[0]
        file_align = struct.unpack_from('<I', data, oh+36)[0]
        image_size = struct.unpack_from('<I', data, oh+52)[0]
        hdr_size   = struct.unpack_from('<I', data, oh+56)[0]
        subsystem  = struct.unpack_from('<H', data, oh+68)[0]
        dll_chars  = struct.unpack_from('<H', data, oh+70)[0]
        num_dd     = struct.unpack_from('<I', data, oh+92)[0]
        dd_start   = oh + 96

    subsys_str = {1:'NATIVE', 2:'GUI', 3:'CUI', 10:'EFI'}.get(subsystem, str(subsystem))
    print(f"[OPTIONAL_HEADER] ({'PE32+' if is64 else 'PE32'})")
    print(f"  ImageBase         = 0x{image_base:016X}")
    print(f"  EntryPoint RVA    = 0x{entry_rva:08X}  -> VA=0x{image_base+entry_rva:016X}")
    print(f"  SectionAlignment  = 0x{sec_align:X}")
    print(f"  FileAlignment     = 0x{file_align:X}")
    print(f"  SizeOfImage       = 0x{image_size:X}")
    print(f"  SizeOfHeaders     = 0x{hdr_size:X}")
    print(f"  Subsystem         = {subsystem}  ({subsys_str})")
    print(f"  DllCharacteristics= 0x{dll_chars:04X}")
    for (bit, name) in DLLCHAR_FLAGS:
        if dll_chars & bit:
            print(f"    + {name}")
    print()

    DD_NAMES = ['Export','Import','Resource','Exception','Security',
                'BaseReloc','Debug','Architecture','GlobalPtr','TLS',
                'LoadConfig','BoundImport','IAT','DelayImport','CLR','Reserved']
    print(f"[Data Directory] ({num_dd} entries)")
    dd_entries = {}
    for i in range(min(num_dd, 16)):
        rva  = struct.unpack_from('<I', data, dd_start + i*8)[0]
        size = struct.unpack_from('<I', data, dd_start + i*8 + 4)[0]
        dd_entries[i] = (rva, size)
        if rva:
            print(f"  [{i:2d}] {DD_NAMES[i]:<12}: RVA=0x{rva:08X}  Size=0x{size:X}")
    print()

    sec_tbl  = fh + 20 + size_opt_hdr
    sections = []
    print(f"[Section Headers] ({num_sections} sections)")
    print(f"  {'Name':<10} {'VirtAddr':>10} {'VirtSize':>10} {'RawOff':>8} {'RawSize':>8}  Attr")
    for i in range(num_sections):
        s       = sec_tbl + i * 40
        name    = data[s:s+8].rstrip(b'\x00').decode('ascii', 'replace')
        vsz     = struct.unpack_from('<I', data, s+8)[0]
        rva_s   = struct.unpack_from('<I', data, s+12)[0]
        raw_sz  = struct.unpack_from('<I', data, s+16)[0]
        raw_off = struct.unpack_from('<I', data, s+20)[0]
        chars   = struct.unpack_from('<I', data, s+36)[0]
        attr    = ('r' if chars & 0x40000000 else '-') + \
                  ('w' if chars & 0x80000000 else '-') + \
                  ('x' if chars & 0x20000000 else '-')
        sections.append((name, vsz, rva_s, raw_sz, raw_off, chars))
        print(f"  {name:<10} 0x{rva_s:08X} 0x{vsz:08X} 0x{raw_off:06X} 0x{raw_sz:06X}  {attr}")
    print()

    # Import Directory
    imp_rva, _ = dd_entries.get(1, (0, 0))
    if imp_rva:
        print(f"[Import Directory] (RVA=0x{imp_rva:08X})")
        desc_off = rva_to_offset(imp_rva, sections)
        while desc_off is not None:
            orig_thunk  = struct.unpack_from('<I', data, desc_off + 0)[0]
            name_rva    = struct.unpack_from('<I', data, desc_off + 12)[0]
            first_thunk = struct.unpack_from('<I', data, desc_off + 16)[0]
            if orig_thunk == 0 and name_rva == 0:
                break
            dll_name_off = rva_to_offset(name_rva, sections)
            dll_name = read_cstring(data, dll_name_off) if dll_name_off else '???'
            print(f"  DLL: {dll_name}")
            ilt_off = rva_to_offset(orig_thunk if orig_thunk else first_thunk, sections)
            if ilt_off:
                fn_idx = 0
                while fn_idx < 500:
                    thunk = (struct.unpack_from('<Q', data, ilt_off + fn_idx*8)[0]
                             if is64 else
                             struct.unpack_from('<I', data, ilt_off + fn_idx*4)[0])
                    if thunk == 0:
                        break
                    ord_flag = (1 << 63) if is64 else (1 << 31)
                    if thunk & ord_flag:
                        print(f"    [ord #{thunk & 0xFFFF}]")
                    else:
                        ibn_rva = thunk & (0x7FFFFFFFFFFFFFFF if is64 else 0x7FFFFFFF)
                        ibn_off = rva_to_offset(ibn_rva, sections)
                        if ibn_off:
                            hint  = struct.unpack_from('<H', data, ibn_off)[0]
                            fname = read_cstring(data, ibn_off + 2)
                            print(f"    hint=0x{hint:04X}  {fname}")
                    fn_idx += 1
            desc_off += 20
        print()

    # Export Directory
    exp_rva, _ = dd_entries.get(0, (0, 0))
    if exp_rva:
        exp_off = rva_to_offset(exp_rva, sections)
        if exp_off:
            base_ord  = struct.unpack_from('<I', data, exp_off + 16)[0]
            num_fns   = struct.unpack_from('<I', data, exp_off + 20)[0]
            num_names = struct.unpack_from('<I', data, exp_off + 24)[0]
            eat_rva   = struct.unpack_from('<I', data, exp_off + 28)[0]
            names_rva = struct.unpack_from('<I', data, exp_off + 32)[0]
            ords_rva  = struct.unpack_from('<I', data, exp_off + 36)[0]
            print(f"[Export Directory] OrdinalBase={base_ord}  Functions={num_fns}  Names={num_names}")
            names_off = rva_to_offset(names_rva, sections)
            ords_off  = rva_to_offset(ords_rva, sections)
            eat_off   = rva_to_offset(eat_rva, sections)
            show = min(num_names, 8)
            for i in range(show):
                n_rva   = struct.unpack_from('<I', data, names_off + i*4)[0]
                n_off   = rva_to_offset(n_rva, sections)
                fname   = read_cstring(data, n_off) if n_off else '???'
                ordinal = struct.unpack_from('<H', data, ords_off + i*2)[0]
                fn_rva  = struct.unpack_from('<I', data, eat_off + ordinal*4)[0]
                print(f"  [{i:4d}] ord={base_ord+ordinal:<5}  RVA=0x{fn_rva:08X}  {fname}")
            if num_names > show:
                print(f"  ... ({num_names - show} more exports)")

if __name__ == '__main__':
    target = sys.argv[1] if len(sys.argv) > 1 else 'C:/Windows/System32/kernel32.dll'
    parse_pe(target)
```

**本機實跑結果**（對 `C:\Windows\System32\kernel32.dll`）：

```
=== PE Parser: kernel32.dll (836232 bytes) ===

[DOS Header]
  e_magic   = 0x5A4D  ('MZ')
  e_lfanew  = 0x00000100

[FILE_HEADER]
  Machine           = 0x8664  (AMD64 (x86-64))
  NumberOfSections  = 8
  TimeDateStamp     = 0xB7DAF818
  SizeOfOptHeader   = 240
  Characteristics   = 0x2022  [EXECUTABLE_IMAGE | DLL]

[OPTIONAL_HEADER] (PE32+)
  ImageBase         = 0x0000000180000000
  EntryPoint RVA    = 0x0002E1A0  -> VA=0x000000018002E1A0
  SectionAlignment  = 0x1000
  FileAlignment     = 0x1000
  SizeOfImage       = 0xC9000
  SizeOfHeaders     = 0x1000
  Subsystem         = 3  (CUI)
  DllCharacteristics= 0x4160
    + HIGH_ENTROPY_VA
    + DYNAMIC_BASE (ASLR)
    + NX_COMPAT (DEP)
    + GUARD_CF (CFG)

[Data Directory] (16 entries)
  [ 0] Export      : RVA=0x000A4D30  Size=0xEC78
  [ 1] Import      : RVA=0x000B39A8  Size=0x834
  [ 2] Resource    : RVA=0x000C7000  Size=0x520
  [ 3] Exception   : RVA=0x000C1000  Size=0x477C
  [ 4] Security    : RVA=0x000C8000  Size=0x4288
  [ 5] BaseReloc   : RVA=0x000C8000  Size=0x5D8
  [ 6] Debug       : RVA=0x0009D944  Size=0x70
  [10] LoadConfig  : RVA=0x000890D0  Size=0x148
  [12] IAT         : RVA=0x00089218  Size=0x2B10
  [13] DelayImport : RVA=0x000A4810  Size=0x80

[Section Headers] (8 sections)
  Name         VirtAddr   VirtSize   RawOff  RawSize  Attr
  .text      0x00001000 0x00084B24 0x001000 0x085000  r-x
  fothk      0x00086000 0x00001000 0x086000 0x001000  r-x
  .rdata     0x00087000 0x00037C00 0x087000 0x038000  r--
  .data      0x000BF000 0x00001648 0x0BF000 0x001000  rw-
  .pdata     0x000C1000 0x0000477C 0x0C0000 0x005000  r--
  .didat     0x000C6000 0x000000A8 0x0C5000 0x001000  rw-
  .rsrc      0x000C7000 0x00000520 0x0C6000 0x001000  r--
  .reloc     0x000C8000 0x00000630 0x0C7000 0x001000  r--

[Import Directory] (RVA=0x000B39A8)
  DLL: api-ms-win-core-rtlsupport-l1-1-0.dll
    hint=0x0005  RtlCompareMemory
    hint=0x000D  RtlRaiseException
    hint=0x0006  RtlDeleteFunctionTable
    hint=0x0010  RtlUnwindEx
    hint=0x0009  RtlInstallFunctionTableCallback
    hint=0x0002  RtlCaptureContext
    hint=0x0000  RtlAddFunctionTable
    hint=0x0011  RtlVirtualUnwind
    hint=0x000C  RtlPcToFileHeader
    hint=0x000F  RtlUnwind
    hint=0x000E  RtlRestoreContext
    hint=0x000B  RtlLookupFunctionEntry
  DLL: ntdll.dll
    hint=0x0636  RtlUnicodeStringToInteger
    hint=0x0470  RtlGetUILanguageInfo
    ... （共 8 個 DLL）

[Export Directory] OrdinalBase=1  Functions=1693  Names=1693
  [   0] ord=1      RVA=0x000A8F9F  AcquireSRWLockExclusive
  [   1] ord=2      RVA=0x000A8FD5  AcquireSRWLockShared
  [   2] ord=3      RVA=0x00037AF0  ActivateActCtx
  [   3] ord=4      RVA=0x0000E4E0  ActivateActCtxWorker
  [   4] ord=5      RVA=0x00057FE0  ActivatePackageVirtualizationContext
  [   5] ord=6      RVA=0x00045830  AddAtomA
  [   6] ord=7      RVA=0x00037E70  AddAtomW
  [   7] ord=8      RVA=0x00057D00  AddConsoleAliasA
  ... (1685 more exports)
```

</details>

<details>
<summary>展開 Part 2 — peb_walk_full.py（本機 Python 3.12 實跑通過，ALL MATCH 確認）</summary>

```python
#!/usr/bin/env python3
"""peb_walk_full.py — PEB->LDR->Export 解析，不呼叫 GetProcAddress"""
import ctypes, struct, os

kernel32_dll = ctypes.WinDLL('kernel32')
ntdll_dll    = ctypes.WinDLL('ntdll')

pid   = os.getpid()
hProc = kernel32_dll.OpenProcess(0x1F0FFF, False, pid)

ReadProcessMemory = kernel32_dll.ReadProcessMemory
ReadProcessMemory.restype = ctypes.c_bool

def rpmem(addr, size):
    buf = (ctypes.c_ubyte * size)()
    nr  = ctypes.c_size_t()
    ReadProcessMemory(hProc, ctypes.c_void_p(addr), buf, size, ctypes.byref(nr))
    return bytes(buf)

def u64(a): return struct.unpack_from('<Q', rpmem(a, 8))[0]
def u32(a): return struct.unpack_from('<I', rpmem(a, 4))[0]

def read_unicode_string(addr):
    length  = struct.unpack_from('<H', rpmem(addr, 2))[0]  # bytes，不是字元數
    buf_ptr = u64(addr + 8)                                  # Buffer* 在 +0x08（x64）
    if not buf_ptr or not length:
        return ''
    return rpmem(buf_ptr, length).decode('utf-16-le', errors='replace')

# ── Step 1: 取 PEB ──────────────────────────────────────────────────────────
class PROCESS_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ('Reserved1',       ctypes.c_ulonglong),
        ('PebBaseAddress',  ctypes.c_ulonglong),
        ('Reserved2',       ctypes.c_ulonglong * 2),
        ('UniqueProcessId', ctypes.c_ulonglong),
        ('Reserved3',       ctypes.c_ulonglong),
    ]

pbi     = PROCESS_BASIC_INFORMATION()
ret_len = ctypes.c_ulong()
NtQIP   = ntdll_dll.NtQueryInformationProcess
NtQIP.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                  ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p]
NtQIP.restype  = ctypes.c_long
status   = NtQIP(hProc, 0, ctypes.byref(pbi),
                 ctypes.sizeof(pbi), ctypes.byref(ret_len))
peb_addr = pbi.PebBaseAddress
print(f"[1] NtQueryInformationProcess status: 0x{status & 0xFFFFFFFF:08X}")
print(f"    PEB @ 0x{peb_addr:016X}")

# ── Step 2: PEB.Ldr (PEB + 0x18) ───────────────────────────────────────────
ldr_addr  = u64(peb_addr + 0x18)
list_head = ldr_addr + 0x10       # InLoadOrderModuleList head（not a real entry）
flink     = u64(list_head)        # 第一個 LDR_DATA_TABLE_ENTRY
print(f"[2] PEB.Ldr @ 0x{ldr_addr:016X}")
print(f"    InLoadOrderModuleList head @ 0x{list_head:016X}")

# ── Step 3: Walk InLoadOrderModuleList ──────────────────────────────────────
# LDR_DATA_TABLE_ENTRY (x64) 關鍵偏移：
#   +0x00  InLoadOrderLinks.Flink
#   +0x30  DllBase
#   +0x58  BaseDllName  (UNICODE_STRING: Length+2, MaxLen+2, pad+4, Buffer*+8)
print(f"[3] Walking InLoadOrderModuleList:")
entry    = flink
seen     = set()
step     = 0
k32_base = None

while entry != list_head and entry not in seen:
    seen.add(entry)
    dll_base  = u64(entry + 0x30)
    base_name = read_unicode_string(entry + 0x58)
    print(f"    [{step}] {base_name:<35} @ 0x{dll_base:016X}")
    if base_name.upper() == 'KERNEL32.DLL':
        k32_base = dll_base
        print(f"         ^--- found KERNEL32.DLL!")
    entry = u64(entry)   # InLoadOrderLinks.Flink
    step += 1
    if step > 64:
        print("    (truncated)")
        break

assert k32_base, "KERNEL32.DLL not found"
print(f"\n    KERNEL32.DLL base = 0x{k32_base:016X}")

# ── Step 4: Parse Export Directory ──────────────────────────────────────────
hdr      = rpmem(k32_base, 0x400)
e_lfanew = struct.unpack_from('<I', hdr, 0x3C)[0]
oh_off   = e_lfanew + 4 + 20          # NT sig(4) + FileHeader(20)
exp_rva  = struct.unpack_from('<I', hdr, oh_off + 112)[0]  # DataDirectory[0].VirtualAddress

print(f"\n[4] KERNEL32 Export Directory RVA=0x{exp_rva:08X}")
exp       = rpmem(k32_base + exp_rva, 40)
base_ord  = struct.unpack_from('<I', exp, 16)[0]
num_fns   = struct.unpack_from('<I', exp, 20)[0]
num_names = struct.unpack_from('<I', exp, 24)[0]
eat_rva   = struct.unpack_from('<I', exp, 28)[0]  # AddressOfFunctions
names_rva = struct.unpack_from('<I', exp, 32)[0]  # AddressOfNames
ords_rva  = struct.unpack_from('<I', exp, 36)[0]  # AddressOfNameOrdinals

# 讀三張表
names_raw = rpmem(k32_base + names_rva, num_names * 4)
ords_raw  = rpmem(k32_base + ords_rva,  num_names * 2)
eat_raw   = rpmem(k32_base + eat_rva,   num_fns   * 4)

def find_export_va(func_name):
    for i in range(num_names):
        n_rva  = struct.unpack_from('<I', names_raw, i * 4)[0]
        nbuf   = rpmem(k32_base + n_rva, len(func_name) + 1)
        name   = nbuf[:nbuf.find(b'\x00')].decode('ascii', 'replace') if b'\x00' in nbuf else ''
        if name == func_name:
            ordinal  = struct.unpack_from('<H', ords_raw, i * 2)[0]
            fn_rva   = struct.unpack_from('<I', eat_raw,  ordinal * 4)[0]
            return k32_base + fn_rva
    return None

# ── Step 5: Resolve targets ──────────────────────────────────────────────────
targets = ['GetProcAddress', 'LoadLibraryA', 'VirtualAlloc', 'VirtualProtect']
print(f"\n[5] Resolving exports (manual EAT walk, no GetProcAddress):")
resolved = {}
for fn in targets:
    va = find_export_va(fn)
    resolved[fn] = va
    print(f"    {fn:<22} -> 0x{va:016X}")

# ── Step 6: Verify with GetProcAddress ──────────────────────────────────────
GetProcAddress    = kernel32_dll.GetProcAddress
GetProcAddress.restype  = ctypes.c_void_p
GetProcAddress.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
GetModuleHandleW  = kernel32_dll.GetModuleHandleW
GetModuleHandleW.restype  = ctypes.c_void_p
GetModuleHandleW.argtypes = [ctypes.c_wchar_p]

print(f"\n[6] Verification via GetProcAddress:")
k32_handle = GetModuleHandleW("kernel32.dll")
match_str  = "MATCH" if k32_handle == k32_base else "MISMATCH!"
print(f"    GetModuleHandleW     = 0x{k32_handle:016X}")
print(f"    LDR-resolved base    = 0x{k32_base:016X}  {match_str}")
print()
all_match = True
for fn in targets:
    ref_va = GetProcAddress(k32_handle, fn.encode())
    our_va = resolved[fn]
    ok     = "MATCH" if ref_va == our_va else "MISMATCH!"
    if ref_va != our_va:
        all_match = False
    print(f"    {fn:<22}  manual=0x{our_va:016X}  GetProcAddress=0x{ref_va:016X}  {ok}")

print()
print("Result:", "ALL MATCH - PEB walk + Export parse correct!" if all_match
      else "MISMATCH found, check logic")
```

**本機實跑輸出**（Windows 11 Pro x64，Python 3.12 mingw64）：

```
[1] NtQueryInformationProcess status: 0x00000000
    PEB @ 0x00000008D7E1F000
[2] PEB.Ldr @ 0x00007FFA85B918C0
    InLoadOrderModuleList head @ 0x00007FFA85B918D0
[3] Walking InLoadOrderModuleList:
    [0] python.exe                          @ 0x00007FF6C5A60000
    [1] ntdll.dll                           @ 0x00007FFA859C0000
    [2] KERNEL32.DLL                        @ 0x00007FFA849A0000
         ^--- found KERNEL32.DLL!
    [3] KERNELBASE.dll                      @ 0x00007FFA830D0000
    [4] ucrtbase.dll                        @ 0x00007FFA82CC0000
    ... （共 31 個模組）

    KERNEL32.DLL base = 0x00007FFA849A0000

[4] KERNEL32 Export Directory RVA=0x000A4D30
    OrdinalBase=1  Functions=1693  Names=1693
    EAT RVA=0x000A4D58  Names RVA=0x000A67CC  Ords RVA=0x000A8240

[5] Resolving exports (manual EAT walk, no GetProcAddress):
    GetProcAddress         -> 0x00007FFA849D3D00
    LoadLibraryA           -> 0x00007FFA849E2CE0
    VirtualAlloc           -> 0x00007FFA849D3D20
    VirtualProtect         -> 0x00007FFA849D8200

[6] Verification via GetProcAddress:
    GetModuleHandleW     = 0x00007FFA849A0000
    LDR-resolved base    = 0x00007FFA849A0000  MATCH

    GetProcAddress          manual=0x00007FFA849D3D00  GetProcAddress=0x00007FFA849D3D00  MATCH
    LoadLibraryA            manual=0x00007FFA849E2CE0  GetProcAddress=0x00007FFA849E2CE0  MATCH
    VirtualAlloc            manual=0x00007FFA849D3D20  GetProcAddress=0x00007FFA849D3D20  MATCH
    VirtualProtect          manual=0x00007FFA849D8200  GetProcAddress=0x00007FFA849D8200  MATCH

Result: ALL MATCH - PEB walk + Export parse correct!
```

</details>

---

## 測試用例表

| 目標檔案 | 預期 Machine | 預期 DllCharacteristics | 有 Export？ | 注意 |
|---|---|---|---|---|
| `C:\Windows\System32\kernel32.dll` | 0x8664 | 0x4160（含 CFG） | 1693 個匯出 | 主要測試目標 |
| `C:\Windows\System32\ntdll.dll` | 0x8664 | 0x4160 | 2000+ 個匯出 | Nt* syscall stub 全在這 |
| `C:\Windows\System32\notepad.exe` | 0x8664 | 0x0160（無 CFG） | 無（DataDir[0] RVA=0） | 純 exe 測試，含 GUI subsystem |
| `C:\Windows\SysWOW64\kernel32.dll` | 0x014C | — | 同 64-bit 版但 RVA 不同 | 32-bit PE（PE32，非 PE32+），測試 `is64` 分支 |

**Part 2 的測試**：用不同的靶模組替換步驟——在 `find_module_base` 裡改搜 `ntdll.dll`，解析其 Export Directory，找 `NtAllocateVirtualMemory`，與正規 `GetProcAddress(GetModuleHandleW("ntdll"), "NtAllocateVirtualMemory")` 比對。

---

## 延伸挑戰

### 挑戰 1：By-hash resolve（不比較字串）

真正的 shellcode 不能有字串——字串會被 AV 掃到，也增加體積。改成用 **ROR13 hash** 查找：

1. 對每個匯出函式名稱，用 ROR13 hash 演算法計算 hash 值
2. 在 `find_export_va` 的迴圈裡，把字串比對改成 hash 比對
3. 預先計算你的目標函式的 hash：

```python
def ror13_hash(name: str) -> int:
    h = 0
    for c in name:
        h = ((h >> 13) | (h << (32 - 13))) & 0xFFFFFFFF
        h = (h + ord(c)) & 0xFFFFFFFF
    return h

# kernel32!VirtualAlloc 的 ROR13 hash
print(hex(ror13_hash("VirtualAlloc")))   # 0x9DBD95A6
# kernel32!LoadLibraryA
print(hex(ror13_hash("LoadLibraryA")))   # 0xEC0E4E8E
```

把整個模組名稱 + 函式名稱合在一起 hash 的方法（Metasploit 風格）：模組名先轉大寫再 hash，加上函式名的 hash。Ch 25 的 shellcode 就用這個。

### 挑戰 2：支援 x86（32-bit）PE

`SysWOW64\kernel32.dll` 是 32-bit PE（PE32，Magic = 0x010B）。修改你的 Part 1 parser，讓它自動偵測 `is64`，在 PE32 模式下：
- `ImageBase` 在 `oh+28`，是 DWORD（4B），不是 QWORD
- ILT 的 `IMAGE_THUNK_DATA32` 是 DWORD，高位元（bit31）是 ordinal flag
- `UNICODE_STRING.Buffer` 在 32-bit 進程是 DWORD，不是 QWORD

Part 2 的 x86 版更複雜——你要啟動一個 32-bit Python 或用 `ctypes` 指定 32-bit 呼叫約定。最直接的驗證方法是 Part 1 靜態解析 `SysWOW64\kernel32.dll`，比對輸出。

### 挑戰 3：InMemoryOrder vs InLoadOrder

Part 2 走的是 `InLoadOrderModuleList`（`LDR + 0x10`）。`InMemoryOrderModuleList` 在 `LDR + 0x20`——但要注意，`InMemoryOrderLinks` 在 `LDR_DATA_TABLE_ENTRY` 裡的偏移是 `+0x10`，不是 `+0x00`（不同的鏈起點）。改寫 Part 2，走 `InMemoryOrderModuleList`，把結果和 `InLoadOrder` 對比：順序會不會不同？`DllBase` 值會不會不同？（答：應該都一樣，但順序是按 VA 而非載入順序）

---

## 自我檢核

做完之前不要看答案：

- [ ] 不看筆記，說出從 PE 檔的位元組 0 到 `EntryPoint` 的 VA，要讀哪些欄位、做什麼計算（提示：要涉及至少 4 個欄位）
- [ ] `rva_to_offset` 為什麼要用 `max(vsz, raw_sz)` 而不是直接用 `vsz` 或 `raw_sz`？什麼情況下兩者不一樣？（對照 Ch 3 的 VirtualSize vs SizeOfRawData 那節）
- [ ] Part 2 為什麼不需要 `rva_to_offset` 函式？從記憶體讀 Export Directory 和從文件讀有什麼根本差異？
- [ ] `UNICODE_STRING.Buffer` 在 x64 下是 `addr + 8`，不是 `addr + 4`——為什麼？（提示：`UNICODE_STRING` 的定義是 `Length(2B) + MaximumLength(2B) + [Padding](4B) + Buffer*(8B)`，結構對齊）
- [ ] 被面試問「shellcode 怎麼不用 `GetProcAddress` 找 `VirtualAlloc`」：你能說出五個步驟的答案嗎？（GS:[0x60] → PEB.Ldr → InLoadOrderModuleList → KERNEL32 DllBase → Export Directory 三表查找）
- [ ] 如果 `find_export_va` 找不到目標函式（回傳 `None`），可能的原因有幾個？（至少列出 3 個：函式名稱拼錯、DLL 沒有以名稱匯出只有序號、forward 匯出到另一個 DLL）

---

做完這個練習，你手頭有一個靜態 PE parser 和一個 runtime PEB walker，這兩個工具在接下來的整個課程都用得到——Part 3 分析 exploit 目標時用 Part 1 確認緩解狀態，Part 4 heap 練習時用 Part 2 找 `HeapAlloc` 的 VA，Ch 25 的 shellcode 是 Part 2 的組語版。現在進 heap——先從 NT Heap 的舊世界開始。

→ [Ch 14 — NT Heap 傳統架構](./14-nt-heap.md)
