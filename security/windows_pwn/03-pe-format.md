# Ch 3 — PE 格式深挖（vs ELF）

> **目標**：把 PE 的每一層結構從二進位位元組讀到能手動解析，理解哪些欄位在 loader 流程中被讀、哪些在 exploit 裡會被改。學完能拿一個任意 PE 用 Python 自己解出 ImageBase/EntryPoint/IAT/reloc，並以「vs ELF」對照表把兩種格式的設計哲學說清楚。

> **環境**：Windows 11 Pro x64；mingw-w64 GCC 14.2（`C:\msys64\ucrt64\bin`）；Python 3.12 + struct/ctypes。本章所有程式碼與輸出均在本機**實際執行**過。MSVC 相關標記（`/guard:cf` 等影響 PE 欄位的旗標）無法在本機驗證，標注「未實測」。

## 為什麼需要這個？

你熟 ELF。你知道 `readelf -h` 印出的 `e_machine`/`e_entry`，知道 `.plt`/`.got.plt` 的作用，知道 `RELRO` 讓 GOT 變成唯讀，知道 `PT_LOAD` segment 和 section 的二層結構。

換到 Windows，你拿到一個 `.exe`，想知道：

- 這個 PE 的 ImageBase 在哪，ASLR 開沒開？
- Import 表在哪，有哪些函式？解析後 loader 放到哪裡？
- relocation 資訊長什麼樣？
- CFG 有沒有開？

全部藏在 PE 格式裡。PE 是 Windows 的 ELF，但兩者的設計哲學不同：ELF 用 segment（runtime 視角）+section（linker 視角）兩層；PE 把這兩者融合進 section header，用 Data Directory 描述特殊功能（Import、Export、Reloc、TLS...），更像是「給 loader 的一份 recipe」。

不懂 PE，你就看不懂 WinDbg 指的位址，也不知道 exploit 要改哪個位元組讓緩解失效。

## 先建立直覺：PE 的整體佈局

把一個 `.exe` 攤開，從位元組 0 到最後：

```
文件偏移 0
┌─────────────────────────────────┐
│  DOS Header  (64 bytes)         │  ← MZ magic、MS-DOS stub（擺設）
│  e_lfanew → 偏移到 NT Headers   │
├─────────────────────────────────┤
│  DOS Stub    (可變長，通常~64B) │  ← "This program cannot be run in DOS mode"
├─────────────────────────────────┤  ← e_lfanew 指到這裡
│  NT Headers                     │
│  ├─ Signature  "PE\0\0" (4B)   │
│  ├─ FILE_HEADER       (20B)    │  ← Machine, NumberOfSections, Characteristics
│  └─ OPTIONAL_HEADER   (224B)  │  ← PE32+ = 0x020B；EntryPoint, ImageBase, DataDir
├─────────────────────────────────┤
│  Section Headers (每個 40B)     │  ← 數量由 FILE_HEADER.NumberOfSections 決定
│  ├─ .text header                │
│  ├─ .data header                │
│  ├─ .idata header (.rdata 裡)  │
│  └─ .reloc header               │
├─────────────────────────────────┤
│  Section raw data               │
│  ├─ .text   (code)              │
│  ├─ .data   (rw data)           │
│  ├─ .rdata  (ro data, imports)  │
│  ├─ .idata  (import tables)     │
│  ├─ .reloc  (base relocations)  │
│  └─ ...                         │
└─────────────────────────────────┘
```

**和 ELF 最大的不同**：ELF 有 Program Header Table（PT_LOAD segment，runtime）和 Section Header Table（.text/.data，linker），是兩張獨立的表。PE 只有一張 Section Header Table，section 本身同時扮演兩個角色——`VirtualAddress`/`SizeOfRawData` 是 runtime 佈局，`PointerToRawData` 是 linker/loader 讀檔案用的。

## DOS Header 與 DOS Stub

```c
// winnt.h 節錄
typedef struct _IMAGE_DOS_HEADER {
    WORD  e_magic;     // 0x5A4D = 'MZ'（Mark Zbikowski 縮寫）
    WORD  e_cblp;      // bytes in last page
    WORD  e_cp;        // pages in file
    // ... 共 29 個 WORD 欄位，DOS 時代用，現代 loader 完全不看
    LONG  e_lfanew;    // +0x3C：NT Headers 的文件偏移 ← 唯一重要的欄位
} IMAGE_DOS_HEADER;
```

64 bytes。你只需要記住兩件事：

1. `e_magic = 0x5A4D`（小端 = `MZ`）——這是 PE 的識別碼
2. `e_lfanew`（偏移 `+0x3C`）——跳過 DOS stub 找到 NT Headers

DOS Stub 是一小段真的能在 DOS 上跑的 16 位元程式，印出「This program cannot be run in DOS mode」然後退出。現代 loader 完全跳過它。在 exploit 裡，DOS stub 區域有時被用來塞 shellcode（因為映射進記憶體後這段是可讀的），但它預設不可執行。

**vs ELF**：ELF Ident 在偏移 0，magic `\x7fELF` 佔 4 bytes，沒有 DOS stub 這種歷史包袱。`e_lfanew` 對應 ELF 裡 `e_phoff`（Program Header 偏移）的概念，但語意不同。

## NT Headers：PE 的核心地址

```
文件偏移 e_lfanew
┌────────────────────────────────────────┐
│ Signature = 0x00004550 ('PE\0\0')     │  4 bytes
├────────────────────────────────────────┤
│ IMAGE_FILE_HEADER  (FileHeader)        │  20 bytes
├────────────────────────────────────────┤
│ IMAGE_OPTIONAL_HEADER64 (OptionalHeader)│ 240 bytes (PE32+)
└────────────────────────────────────────┘
```

**真實輸出**（`objdump -p demo_stripped.exe`，本機實跑）：

```
D:/tmp_build/demo_stripped.exe:     file format pei-x86-64

Magic                 020b    (PE32+)
MajorLinkerVersion    2
MinorLinkerVersion    43
SizeOfCode            0000000000001800
SizeOfInitializedData 0000000000004000
AddressOfEntryPoint   00000000000013e0
BaseOfCode            0000000000001000
ImageBase             0000000140000000
SectionAlignment      00001000
FileAlignment         00000200
SizeOfImage           00024000
SizeOfHeaders         00000600
Subsystem             00000003    (Windows CUI)
DllCharacteristics    00000160
                      HIGH_ENTROPY_VA
                      DYNAMIC_BASE
                      NX_COMPAT
NumberOfRvaAndSizes   00000010
```

### IMAGE_FILE_HEADER（FileHeader）

```c
typedef struct _IMAGE_FILE_HEADER {
    WORD  Machine;              // 0x8664 = AMD64, 0x014C = I386
    WORD  NumberOfSections;     // section 數量
    DWORD TimeDateStamp;        // 編譯時間戳（可被偽造）
    DWORD PointerToSymbolTable; // 舊版，現代 PE 為 0
    DWORD NumberOfSymbols;      // 同上
    WORD  SizeOfOptionalHeader; // 224 = PE32+, 224 bytes；用來跳過 OptHeader 找 Section Table
    WORD  Characteristics;      // IMAGE_FILE_xxx flags（可執行/DLL/stripped 等）
} IMAGE_FILE_HEADER;  // 20 bytes
```

**exploit 視角**：
- `Machine`：exploit 一定要確認 0x8664（x64）還是 0x014C（x86），不然 ROP chain 全錯
- `SizeOfOptionalHeader`：解析時用來定位 Section Header Table 的偏移（`e_lfanew + 4 + 20 + SizeOfOptionalHeader`）

**vs ELF**：對應 ELF Header 的 `e_machine`（0x3E = x86-64）和 `e_shnum`（section 數量）。ELF 的 `e_type`（ET_EXEC/ET_DYN）對應 PE Characteristics 的 `IMAGE_FILE_DLL`（0x2000）位元。

### IMAGE_OPTIONAL_HEADER64（OptionalHeader）

名字有「Optional」是歷史包袱，其實**對所有可執行 PE 都是必須的**，只有 COFF object file 才沒有它。PE32 vs PE32+ 差在 `ImageBase` 和指標欄位的寬度：

| 欄位 | PE32（32-bit） | PE32+（64-bit） |
|---|---|---|
| Magic | 0x010B | 0x020B |
| ImageBase | DWORD（4B） | ULONGLONG（8B） |
| 指標欄位（BaseOfData 等） | 4B | 欄位不存在 |
| SizeOfOptionalHeader | 224 | 240 |

**關鍵欄位逐一解析**：

```
+0x00  Magic               0x020B = PE32+
+0x02  MajorLinkerVersion  連結器版本
+0x04  SizeOfCode          .text section 合計大小
+0x10  AddressOfEntryPoint RVA！不是 VA。loader 加上 ImageBase 才是實際入口
+0x18  BaseOfCode          .text 的起始 RVA（通常 0x1000）
+0x18  ImageBase           (PE32+ 在 +0x18) 偏好載入基址 ← ASLR 的起點
+0x20  SectionAlignment    記憶體裡 section 對齊粒度（通常 0x1000 = 4KB）
+0x24  FileAlignment       文件裡 section 對齊粒度（通常 0x200 = 512B）
+0x38  SizeOfImage         整個映像在記憶體裡佔的大小（SectionAlignment 對齊）
+0x3C  SizeOfHeaders       所有 header 合計（映射進記憶體後這些 bytes 可讀）
+0x40  CheckSum            PE 校驗和（驅動程式必須正確，一般 exe 可為 0）
+0x44  Subsystem           2=GUI, 3=CUI(console)
+0x46  DllCharacteristics  緩解旗標位元遮罩 ← exploit 最常改的欄位之一
+0x60  NumberOfRvaAndSizes Data Directory 的條目數（通常 0x10 = 16）
+0x68  DataDirectory[16]   16 個 8-byte 條目（RVA + Size）
```

**RVA（Relative Virtual Address）**：PE 裡幾乎所有位址都是 RVA——相對 ImageBase 的偏移。真正的 VA = ImageBase + RVA。loader 把映像映射到某個地址後，你看到的所有 RVA 都要加上實際 ImageBase 才能用。這是 ASLR 之所以能工作的基礎——PE 裡只存相對偏移，運行時才知道實際地址。

**vs ELF**：ELF 在 `e_entry` 直接存 VA（PIE 的話是偏移）；ELF 靠 `PT_LOAD` 的 `p_vaddr` + loader 決定載入基址。PE 的 `AddressOfEntryPoint` 永遠是 RVA。

### DllCharacteristics：緩解旗標位元遮罩

```
值      名稱                  意義
0x0020  HIGH_ENTROPY_VA      64-bit ASLR 使用高 48-bit 熵（vs 0x0040 只有低 32-bit）
0x0040  DYNAMIC_BASE         ASLR 可重定基址（loader 會套 .reloc）
0x0080  FORCE_INTEGRITY      映像載入時驗簽
0x0100  NX_COMPAT            DEP（資料頁不可執行）
0x0400  NO_SEH               通知 loader 不用 SEH（PE 不含 SAFESEH 表）
0x0800  NO_BIND              禁止 binding（提前固化 IAT）
0x4000  GUARD_CF             CFG 開啟 ← MSVC /guard:cf 才有
0x8000  TERMINAL_SERVER_AWARE 允許多使用者（Terminal Services）
```

**真實輸出**（本機 mingw 編的 demo.exe）：

```
DllCharacteristics    00000160
                      HIGH_ENTROPY_VA    (0x0020)
                      DYNAMIC_BASE       (0x0040)
                      NX_COMPAT          (0x0100)
```

`0x20 + 0x40 + 0x100 = 0x160`。注意 `GUARD_CF (0x4000)` 是 0——mingw 不支援 CFG。

**exploit 視角**：`DllCharacteristics` 是在文件中可改的。如果你有任意寫，把 `DYNAMIC_BASE`（0x0040）那個位元清掉，loader 就不會 ASLR 這個模組；把 `NX_COMPAT`（0x0100）清掉，舊版 Windows 的 DEP 可能跟著關。但現代 Win11 的 DEP 預設對所有行程啟用（`AlwaysOn` policy），單改 PE 欄位不夠——這是細節，Ch 23 詳論。

## Data Directory：功能目錄

OptionalHeader 最後 128 bytes 是 `DataDirectory[16]`，每條目 8 bytes（RVA + Size）。它是一張「你想找什麼功能就查哪裡」的索引：

```
序號  用途                     對應 ELF 概念
  0   Export Directory         .dynsym + .gnu.version_d（匯出符號）
  1   Import Directory         .dynamic + .dynsym（匯入表）
  2   Resource Directory       無直接對應（ELF 沒有標準資源系統）
  3   Exception Directory      .pdata（x64 unwind info，SEH 的 table-based 部分）
  4   Security Directory       簽章（無 ELF 對應）
  5   Base Relocation          .rela.dyn / .rel.dyn（重定位表）
  6   Debug Directory          DWARF debug info 的入口
  9   TLS Directory            .tdata/.tbss（TLS 支援 + TLS callback）
 10   Load Config Directory    /GS stack cookie 值、CFG 函式表、SafeSEH 表
 12   IAT Directory            IAT 的範圍（用於保護）
 13   Delay-Load Import        Delay Import（DLL 用到才載）
```

**真實輸出**（本機 demo_stripped.exe，Data Directory 部分）：

```
Entry 0  0000000000000000 00000000  Export Directory         （無匯出）
Entry 1  0000000000008000 000007c4  Import Directory         .idata
Entry 2  000000000000b000 000004e8  Resource Directory       .rsrc
Entry 3  0000000000005000 00000210  Exception Directory      .pdata
Entry 4  0000000000000000 00000000  Security Directory       （未簽章）
Entry 5  000000000000c000 00000078  Base Relocation Directory .reloc
Entry 9  0000000000004040 00000028  Thread Storage Directory  .tls
Entry c  0000000000008230 00000178  Import Address Table Directory
```

**exploit 視角**：拿到一個 PE，第一件事就是看 Entry 1（Import）、Entry 5（Reloc）、Entry 10（Load Config，裡面有 Stack Cookie 的地址和 CFG 函式表）。Entry 10 的 `GuardCFCheckFunctionPointer` 在 CFG bypass 時是首要目標（Ch 32）。

## Section Headers

每個 Section Header 40 bytes：

```c
typedef struct _IMAGE_SECTION_HEADER {
    BYTE  Name[8];              // ASCII，不保證 null-terminated
    DWORD VirtualSize;          // section 在記憶體裡的實際使用大小（不含 padding）
    DWORD VirtualAddress;       // section 的起始 RVA
    DWORD SizeOfRawData;        // 在文件裡的大小（FileAlignment 對齊）
    DWORD PointerToRawData;     // 在文件裡的起始偏移
    DWORD PointerToRelocations; // COFF relocations（PE 用 Data Directory 取代，通常 0）
    DWORD PointerToLinenumbers; // 廢棄
    WORD  NumberOfRelocations;
    WORD  NumberOfLinenumbers;
    DWORD Characteristics;      // 屬性旗標：可讀/可寫/可執行/含 code/含 data
} IMAGE_SECTION_HEADER;
```

**真實輸出**（本機 demo_stripped.exe）：

```
Sections:
Idx Name     Size       VMA                LMA                File off  Algn
  0 .text    000017f8   0000000140001000   0000000140001000   00000400  2**4
             CONTENTS, ALLOC, LOAD, READONLY, CODE, DATA
  1 .data    000000a0   0000000140003000   0000000140003000   00001c00  2**4
             CONTENTS, ALLOC, LOAD, DATA
  2 .rdata   00000ad0   0000000140004000   0000000140004000   00001e00  2**4
             CONTENTS, ALLOC, LOAD, READONLY, DATA
  3 .pdata   00000210   0000000140005000   0000000140005000   00002a00  2**2
             CONTENTS, ALLOC, LOAD, READONLY, DATA
  4 .xdata   00000198   0000000140006000   0000000140006000   00002e00  2**2
             CONTENTS, ALLOC, LOAD, READONLY, DATA
  5 .bss     00000180   0000000140007000   0000000140007000   00000000  2**4
             ALLOC
  6 .idata   000007c4   0000000140008000   0000000140008000   00003000  2**2
             CONTENTS, ALLOC, LOAD, DATA
  7 .CRT     00000060   0000000140009000   0000000140009000   00003800  2**2
             CONTENTS, ALLOC, LOAD, DATA
  8 .tls     00000010   000000014000a000   000000014000a000   00003a00  2**2
             CONTENTS, ALLOC, LOAD, DATA
  9 .rsrc    000004e8   000000014000b000   000000014000b000   00003c00  2**2
             CONTENTS, ALLOC, LOAD, READONLY, DATA
 10 .reloc   00000078   000000014000c000   000000014000c000   00004200  2**2
             CONTENTS, ALLOC, LOAD, READONLY, DATA
```

幾個要認識的 section：

| Section | 對應 ELF | 屬性 | exploit 意義 |
|---|---|---|---|
| `.text` | `.text` | rx（可讀可執行） | code gadget 來源；ROP chain 在這找 |
| `.data` | `.data` | rw | 可讀寫的全域變數；堆疊溢位可能蓋到 |
| `.rdata` | `.rodata` | r（唯讀） | 字串、vtable、import 跳板（有時） |
| `.bss` | `.bss` | rw，無文件內容 | 未初始化全域；`PointerToRawData=0` |
| `.idata` | 無直接對應 | rw | IAT 所在；控制跳轉的核心 |
| `.pdata` | 無 | r | x64 exception table；SEH unwind info |
| `.reloc` | `.rela.dyn` | r | Base relocation；ASLR 重定用 |
| `.tls` | `.tdata/.tbss` | rw | TLS 變數 + TLS callback（Ch 4） |
| `.rsrc` | 無 | r | 資源（圖示/字串/版本資訊） |

**VirtualSize vs SizeOfRawData**：VirtualSize 是 section 的實際邏輯大小（含 .bss 那種只在記憶體中的部分）；SizeOfRawData 是文件裡占的對齊大小。`VirtualSize > SizeOfRawData` 的部分在載入時補 0 但無對應文件資料——這段有時被用來藏 shellcode（叫 code cave）。

**Characteristics 的重要位元**：

```
0x00000020  IMAGE_SCN_CNT_CODE              含代碼
0x00000040  IMAGE_SCN_CNT_INITIALIZED_DATA  含初始化資料
0x00000080  IMAGE_SCN_CNT_UNINITIALIZED_DATA .bss
0x20000000  IMAGE_SCN_MEM_EXECUTE           可執行
0x40000000  IMAGE_SCN_MEM_READ              可讀
0x80000000  IMAGE_SCN_MEM_WRITE             可寫
```

`.text` 的 Characteristics = `0x60000020`（EXECUTE|READ|CODE）。

## Import Directory、ILT、IAT：動態連結的骨架

這是 PE 裡最複雜的結構，也是 exploit 最常攻擊的位置。

### 三層結構概觀

```
.idata section
├── Import Directory Table（每個 DLL 一條 IMAGE_IMPORT_DESCRIPTOR）
│   ├── OriginalFirstThunk ──→ ILT（Import Lookup Table）
│   │   └── IMAGE_THUNK_DATA[] ── 每條：名稱 RVA 或序號
│   ├── Name ──→ DLL 名稱字串（"KERNEL32.dll\0"）
│   └── FirstThunk ──────────→ IAT（Import Address Table）← loader 寫入真實 VA
│       └── IMAGE_THUNK_DATA[] ── 載入前=ILT 的副本；載入後=真實函式 VA
└── ...（下一個 DLL）
```

```c
typedef struct _IMAGE_IMPORT_DESCRIPTOR {
    union {
        DWORD Characteristics;
        DWORD OriginalFirstThunk;  // ILT（Lookup Table）的 RVA；保存原始 thunk
    };
    DWORD TimeDateStamp;           // 0 or -1（bound import）
    DWORD ForwarderChain;          // -1 = 無轉發
    DWORD Name;                    // DLL 名稱 RVA（"kernel32.dll"）
    DWORD FirstThunk;              // IAT 的 RVA；loader 在這裡寫真實 VA
} IMAGE_IMPORT_DESCRIPTOR;
// 陣列以全 0 的 descriptor 結尾
```

```c
typedef struct _IMAGE_THUNK_DATA64 {
    union {
        ULONGLONG ForwarderString;
        ULONGLONG Function;        // 載入後填入真實函式 VA
        ULONGLONG Ordinal;         // bit 63 = 1：以序號匯入
        ULONGLONG AddressOfData;   // 名稱匯入：指向 IMAGE_IMPORT_BY_NAME RVA
    };
} IMAGE_THUNK_DATA64;

typedef struct _IMAGE_IMPORT_BY_NAME {
    WORD Hint;    // 提示序號（可加速 export table 查找，不保證正確）
    CHAR Name[1]; // 函式名稱字串（null-terminated）
} IMAGE_IMPORT_BY_NAME;
```

### 真實輸出：本機 demo_stripped.exe 的 Import

```
Import Directory:
 vma       Hint     Time     Forward  DLL       First
           Table    Stamp    Chain    Name      Thunk
 00008000  000080b8 00000000 00000000 0000864c  00008230

    DLL Name: KERNEL32.dll
    vma:     Ordinal  Hint  Member-Name
    00008230  <none>  0126  DeleteCriticalSection
    00008238  <none>  014c  EnterCriticalSection
    00008240  <none>  0285  GetLastError
    00008248  <none>  0393  InitializeCriticalSection
    00008250  <none>  03f1  LeaveCriticalSection
    00008258  <none>  0595  SetUnhandledExceptionFilter
    00008260  <none>  05a5  Sleep
    00008268  <none>  05c9  TlsGetValue
    00008270  <none>  05f8  VirtualProtect
    00008278  <none>  05fa  VirtualQuery

    DLL Name: api-ms-win-crt-stdio-l1-1-0.dll
    vma:     Ordinal  Hint  Member-Name
    00008358  <none>  0000  __acct_iob_func
    00008368  <none>  0003  __stdio_common_vfprintf
    00008370  <none>  0004  __stdio_common_vfwprintf
    00008378  <none>  00ab  fwrite
    00008380  <none>  00b4  puts

    （...共 8 個 DLL）
```

`vma: 00008230` 是 IAT 條目的 VMA（含 ImageBase 的 VA），對應到 `FirstThunk = 0x8230`（RVA）。載入後 `[0x140008230]` 裡存的就是 `KERNEL32!DeleteCriticalSection` 的真實 VA。

### ILT vs IAT：為什麼有兩份？

```
文件中（載入前）：
ILT[0] = 0x0000000000008...  → IMAGE_IMPORT_BY_NAME（"DeleteCriticalSection"）
IAT[0] = 0x0000000000008...  → 同上（副本）

記憶體中（loader 填入後）：
ILT[0] = 不變（唯讀參考）
IAT[0] = 0x00007FFA849AXXXX  → KERNEL32.dll 裡 DeleteCriticalSection 的真實 VA
```

ILT 是「我需要什麼函式」的唯讀記錄；IAT 是 loader 的寫入目標。`call [IAT + offset]` 是 PE 函式呼叫的標準模式（間接呼叫）——這也是為什麼 IAT 覆寫是 exploit 的經典技法：改 IAT 條目，下次程式呼叫那個函式就跳到你的 shellcode。

**vs ELF**：ELF 的 `.got.plt` 對應 IAT——都是 loader/dynamic linker 填入實際地址的表；`.plt` 對應 PE 的 thunk（間接跳轉包裝）；`.dynsym + .gnu.hash` 對應 ILT + `IMAGE_IMPORT_BY_NAME`。差別是 ELF 有 lazy binding（第一次呼叫才解析），PE 預設在 DLL 載入時全部解析完（沒有 PLT 延遲解析機制，但有 Delay Import）。

**exploit 視角**：
- **IAT 覆寫**：如果有任意寫（或 heap overflow 能蓋到 IAT），改 `IAT[SomeFunc]` = shellcode 地址，程式下次呼叫 SomeFunc 就進你的 code
- **ROP gadget 定位**：Call stack 裡的 call 指令都是 `call [rip+offset]` 形式，`offset` 指向 IAT——逆向時看這個能快速知道 call 的目標
- **IAT hooking**：注入 DLL 常用技法；也是 EDR 的 hook 手法（Ch 6 詳述）

## Export Directory

DLL 用它暴露函式給其他模組：

```c
typedef struct _IMAGE_EXPORT_DIRECTORY {
    DWORD Characteristics;
    DWORD TimeDateStamp;
    WORD  MajorVersion;
    WORD  MinorVersion;
    DWORD Name;                // DLL 名稱 RVA
    DWORD Base;                // 序號基數（Ordinal 加上這個才是實際序號）
    DWORD NumberOfFunctions;   // Export Address Table（EAT）的條目數
    DWORD NumberOfNames;       // 名稱匯出的函式數
    DWORD AddressOfFunctions;  // EAT RVA：DWORD[NumberOfFunctions]，每個是函式 RVA
    DWORD AddressOfNames;      // 名稱表 RVA：DWORD[NumberOfNames]，每個是字串 RVA
    DWORD AddressOfNameOrdinals; // 序號表 RVA：WORD[NumberOfNames]，與名稱表 1-to-1
} IMAGE_EXPORT_DIRECTORY;
```

查一個函式名稱的流程：

```
1. 二分搜尋 AddressOfNames[] 找到名稱匹配的 index i
2. ordinal = AddressOfNameOrdinals[i]
3. funcRVA = AddressOfFunctions[ordinal]  （注意要減去 Base）
4. VA = ImageBase + funcRVA
```

**vs ELF**：ELF 的 `.dynsym + .gnu.hash`（GNU hash bucket）對應這裡；ELF 用 hash table 加速查找，PE 用二分搜尋（名稱表必須排序）。

**exploit 視角**：shellcode 找函式地址的 PEB walk（Ch 5/25）就是用這個流程——從 Export Directory 遍歷 EAT/名稱表，找到 `LoadLibraryA`/`VirtualAlloc` 的 RVA，不依賴 GetProcAddress。

## Base Relocation Directory

PE 要能在非 preferred ImageBase 的地址執行，就需要 Base Relocation。格式：

```c
typedef struct _IMAGE_BASE_RELOCATION {
    DWORD VirtualAddress;  // 這個 block 對應的頁面 RVA（通常 0x1000 對齊）
    DWORD SizeOfBlock;     // 整個 block 的 bytes 大小（含 header = 8 bytes）
    // WORD TypeOffset[];  // (SizeOfBlock - 8) / 2 個 WORD 條目
} IMAGE_BASE_RELOCATION;
// 每個 WORD 條目：高 4 bit = type，低 12 bit = 頁內偏移
// type 10 = IMAGE_REL_BASED_DIR64（x64 絕對位址需要修正）
// type  0 = IMAGE_REL_BASED_ABSOLUTE（對齊 padding，不做事）
```

**真實輸出**（本機 Python 解析 demo_stripped.exe .reloc 段）：

```
Base Relocation RVA: 0x0000C000  Size: 0x00000078
.reloc  VMA=0x0000C000  RawOff=0x4200  Size=0x200

  Block: PageRVA=0x00002000  BlockSize=12  Entries=2
    [ 0] type=10(DIR64)  offset=0x7D8  -> RVA=0x000027D8
    [ 1] type=0(ABS/pad) offset=0x000  -> RVA=0x00002000

  Block: PageRVA=0x00003000  BlockSize=24  Entries=8
    [ 0] type=10(DIR64)  offset=0x000  -> RVA=0x00003000
    [ 1] type=10(DIR64)  offset=0x040  -> RVA=0x00003040
    [ 2] type=10(DIR64)  offset=0x050  -> RVA=0x00003050
    [ 3] type=10(DIR64)  offset=0x060  -> RVA=0x00003060
    [ 4] type=10(DIR64)  offset=0x070  -> RVA=0x00003070
    [ 5] type=10(DIR64)  offset=0x080  -> RVA=0x00003080
    ... (2 more)

  Block: PageRVA=0x00004000  BlockSize=68  Entries=30
    [ 0] type=10(DIR64)  offset=0x020  -> RVA=0x00004020
    ... (29 more)

  Block: PageRVA=0x00009000  BlockSize=16  Entries=4
    [ 0] type=10(DIR64)  offset=0x008  -> RVA=0x00009008
    ... (3 more)

Total relocation entries: 44
```

**Relocation 的實際作用**：每個 DIR64 條目說的是「我在 RVA 0xXXXX 的位置存了一個 64-bit 絕對位址，如果你的 ImageBase 和 preferred 的不同，請把差值（delta = actual_base - preferred_base）加到這個位置」。Loader 遍歷所有 block，對每個 DIR64 條目做：`*（VA_at_RVA) += delta`。

**vs ELF**：`.rela.dyn` 的 `R_X86_64_64` 類型 relocation 功能相同；ELF PIE 用 `R_X86_64_RELATIVE`（只存 addend）更省空間。PE 的 `.reloc` 強制要求所有「含有絕對指標的位置」都列出來——不像 ELF PIE 只列有「動態分配意義」的 relocation。

**exploit 視角**：
- ASLR 的 rebase 依賴 .reloc。如果能篡改 .reloc 或把 `DYNAMIC_BASE` 位元清掉，就能讓目標以固定 ImageBase 載入
- 注入的 shellcode 若含有絕對位址但沒有對應 reloc，就必須寫成 PIC（Position-Independent Code）——這是 Windows shellcode 的必要條件（Ch 25）

## TLS Directory

Thread Local Storage 的入口，但在 exploit 中更重要的是 **TLS Callback**：

```c
typedef struct _IMAGE_TLS_DIRECTORY64 {
    ULONGLONG StartAddressOfRawData;    // TLS 資料的 VA
    ULONGLONG EndAddressOfRawData;
    ULONGLONG AddressOfIndex;           // DWORD* 指向 TLS index 的 VA
    ULONGLONG AddressOfCallBacks;       // TLS callback 函式指標陣列的 VA
    DWORD     SizeOfZeroFill;
    DWORD     Characteristics;
} IMAGE_TLS_DIRECTORY64;
```

`AddressOfCallBacks` 指向一個以 NULL 結尾的函式指標陣列。每個 callback 在以下時機被 loader 呼叫：

```
DLL_PROCESS_ATTACH（行程啟動）← 在 main 之前！
DLL_THREAD_ATTACH
DLL_THREAD_DETACH
DLL_PROCESS_DETACH
```

**exploit 視角**：TLS callback 在 `main` 之前執行，因此是**反調試**的好位置（調試器還沒對 main 下斷點時就先跑反調試）。惡意軟體也用它來隱藏真正的入口點。逆向時如果 Data Directory Entry 9 不是 0，一定要先看 TLS callback。

## 底層機制：用 Python 手動解析 PE

完整可執行的 Python parser（本機實際跑過）：

```python
#!/usr/bin/env python3
"""pe_parse.py — 最小 PE32+ 解析器，顯示 header 與 DataDirectory"""
import struct, sys

def parse_pe(path):
    with open(path, 'rb') as f:
        data = f.read()

    # DOS Header
    assert data[:2] == b'MZ', "Not a PE"
    e_lfanew = struct.unpack_from('<I', data, 0x3C)[0]

    # NT Signature
    assert data[e_lfanew:e_lfanew+4] == b'PE\x00\x00'

    # FILE_HEADER
    fh = e_lfanew + 4
    machine        = struct.unpack_from('<H', data, fh+0)[0]
    num_sections   = struct.unpack_from('<H', data, fh+2)[0]
    size_opt_hdr   = struct.unpack_from('<H', data, fh+16)[0]
    characteristics = struct.unpack_from('<H', data, fh+18)[0]
    print(f"Machine:           0x{machine:04X}  {'(AMD64)' if machine==0x8664 else '(I386)'}")
    print(f"NumberOfSections:  {num_sections}")
    print(f"Characteristics:   0x{characteristics:04X}")

    # OPTIONAL_HEADER (PE32+)
    oh = fh + 20
    magic      = struct.unpack_from('<H', data, oh+0)[0]
    assert magic == 0x020B, f"Expected PE32+ (0x020B), got 0x{magic:04X}"
    entry_rva  = struct.unpack_from('<I', data, oh+16)[0]
    image_base = struct.unpack_from('<Q', data, oh+24)[0]
    sec_align  = struct.unpack_from('<I', data, oh+32)[0]
    file_align = struct.unpack_from('<I', data, oh+36)[0]
    image_size = struct.unpack_from('<I', data, oh+56)[0]
    subsystem  = struct.unpack_from('<H', data, oh+68)[0]
    dll_chars  = struct.unpack_from('<H', data, oh+70)[0]
    num_dd     = struct.unpack_from('<I', data, oh+108)[0]
    print(f"\nImageBase:          0x{image_base:016X}")
    print(f"EntryPoint RVA:     0x{entry_rva:08X}  → VA=0x{image_base+entry_rva:016X}")
    print(f"SectionAlignment:   0x{sec_align:X}")
    print(f"FileAlignment:      0x{file_align:X}")
    print(f"SizeOfImage:        0x{image_size:X}")
    print(f"Subsystem:          {subsystem}  {'(CUI)' if subsystem==3 else '(GUI)'}")
    print(f"DllCharacteristics: 0x{dll_chars:04X}", end="")
    flags = {0x20:'HiEntropy',0x40:'DynBase(ASLR)',0x100:'NX',0x4000:'CFG'}
    print("  [" + " | ".join(v for k,v in flags.items() if dll_chars & k) + "]")

    # Data Directory
    dd_start = oh + 112
    dd_names = ['Export','Import','Resource','Exception','Security',
                'BaseReloc','Debug','','','TLS','LoadConfig','','IAT',
                'DelayImport','CLR','Reserved']
    print(f"\nDataDirectory (first {num_dd} entries):")
    for i in range(min(num_dd, 16)):
        rva  = struct.unpack_from('<I', data, dd_start + i*8)[0]
        size = struct.unpack_from('<I', data, dd_start + i*8 + 4)[0]
        if rva:
            print(f"  [{i:2d}] {dd_names[i]:<12}: RVA=0x{rva:08X}  Size=0x{size:X}")

    # Section Headers
    sec_tbl = fh + 20 + size_opt_hdr
    print(f"\nSections:")
    print(f"  {'Name':<10} {'VirtAddr':>10} {'VirtSize':>10} {'RawOff':>8} {'RawSize':>8} Attrs")
    for i in range(num_sections):
        s = sec_tbl + i * 40
        name     = data[s:s+8].rstrip(b'\x00').decode('ascii','replace')
        vsz      = struct.unpack_from('<I', data, s+8)[0]
        rva      = struct.unpack_from('<I', data, s+12)[0]
        raw_sz   = struct.unpack_from('<I', data, s+16)[0]
        raw_off  = struct.unpack_from('<I', data, s+20)[0]
        chars    = struct.unpack_from('<I', data, s+36)[0]
        attr = ('r' if chars & 0x40000000 else '-') + \
               ('w' if chars & 0x80000000 else '-') + \
               ('x' if chars & 0x20000000 else '-')
        print(f"  {name:<10} 0x{rva:08X} 0x{vsz:08X} 0x{raw_off:06X} 0x{raw_sz:06X} {attr}")

if __name__ == '__main__':
    parse_pe(sys.argv[1] if len(sys.argv) > 1 else 'D:/tmp_build/demo_stripped.exe')
```

**本機執行輸出**（直接對 demo_stripped.exe 跑）：

```
Machine:           0x8664  (AMD64)
NumberOfSections:  11
Characteristics:   0x0026

ImageBase:          0x0000000140000000
EntryPoint RVA:     0x000013E0  → VA=0x00000001400013E0
SectionAlignment:   0x1000
FileAlignment:      0x200
SizeOfImage:        0x24000
Subsystem:          3  (CUI)
DllCharacteristics: 0x0160  [HiEntropy | DynBase(ASLR) | NX]

DataDirectory (first 16 entries):
  [ 1] Import      : RVA=0x00008000  Size=0x7C4
  [ 2] Resource    : RVA=0x0000B000  Size=0x4E8
  [ 3] Exception   : RVA=0x00005000  Size=0x210
  [ 5] BaseReloc   : RVA=0x0000C000  Size=0x78
  [ 9] TLS         : RVA=0x00004040  Size=0x28
  [12] IAT         : RVA=0x00008230  Size=0x178

Sections:
  Name       VirtAddr   VirtSize   RawOff   RawSize Attrs
  .text      0x00001000 0x000017F8 0x000400 0x001800 r-x
  .data      0x00003000 0x000000A0 0x001C00 0x000200 rw-
  .rdata     0x00004000 0x00000AD0 0x001E00 0x000C00 r--
  .pdata     0x00005000 0x00000210 0x002A00 0x000400 r--
  .xdata     0x00006000 0x00000198 0x002E00 0x000200 r--
  .bss       0x00007000 0x00000180 0x000000 0x000000 rw-
  .idata     0x00008000 0x000007C4 0x003000 0x000800 rw-
  .CRT       0x00009000 0x00000060 0x003800 0x000200 rw-
  .tls       0x0000A000 0x00000010 0x003A00 0x000200 rw-
  .rsrc      0x0000B000 0x000004E8 0x003C00 0x000600 r--
  .reloc     0x0000C000 0x00000078 0x004200 0x000200 r--
```

注意 `.bss` 的 `RawOff = 0x000000`、`RawSize = 0`——它在文件中不佔空間，loader 映射時補 0。

## 對比與取捨

| 面向 | PE | ELF |
|---|---|---|
| 位址表示 | RVA（相對 ImageBase） | VA（PIE 是偏移，非 PIE 是固定 VA） |
| 動態連結索引 | IAT（直接讀函式 VA） | GOT/PLT（lazy 或 eager） |
| Lazy binding | 無（預設，有 Delay Import） | 有（預設，RTLD_LAZY） |
| 重定位格式 | .reloc（頁面分組，每條 2 bytes） | .rela.dyn（每條 24 bytes，更細） |
| 函式呼叫 | `call [IAT_entry]` 直接 | `call PLT_stub → jmp [GOT_entry]` |
| 二層結構 | Section 兼 segment（一張表） | Program Header（runtime）+ Section Header（linker）兩張 |
| 安全旗標位置 | DllCharacteristics + Load Config | `GNU_STACK`/`RELRO`/`PIE` 隱含在多個地方 |
| 資源系統 | .rsrc（內建，有標準格式） | 無標準（用 `.rodata` 或 外部） |
| Export 查找 | 二分搜尋名稱表 + EAT | GNU hash table 或 SYSV hash |

## 踩雷集錦

1. **「RVA 就是 VA」**：錯。RVA + ImageBase = VA。Image 被 ASLR 重定到 0x7FF6CF000000 後，你直接用 PE 裡的 RVA 0x13E0 去找進入點是找不到的。永遠記得 `VA = actual_ImageBase + RVA`。

2. **「IAT 裡存的是函式的 RVA」**：錯。IAT 在文件中存的是 ILT（函式名稱/序號的 RVA），載入後 loader 直接把真實的 VA 寫進去——不是 RVA。所以 `call [IAT_entry]` 裡的值是完整 64-bit VA。

3. **「改 DllCharacteristics 就能關掉所有防護」**：不完全對。Win10+ 的 DEP 有系統級「AlwaysOn」策略，`NX_COMPAT` 欄位只影響「OptIn」策略下的行為；ASLR 欄位也受系統強制 ASLR 影響（Group Policy 可強制所有 PE 啟用 ASLR）。改 PE 欄位是第一步，不是萬靈丹。

4. **「VirtualSize 就是 SizeOfRawData」**：不對。VirtualSize 是 section 在記憶體的邏輯大小（可能比 SizeOfRawData 大，多的補 0；也可能比 FileAlignment 對齊後的 SizeOfRawData 小）。兩個值一定要分開看。

5. **「Data Directory 的 RVA 是文件偏移」**：常見混淆。Data Directory 裡存的全是 RVA（相對 ImageBase），不是文件偏移（PointerToRawData 那種）。要從 RVA 換算文件偏移，要找對應 section 的 `RVA - VirtualAddress + PointerToRawData`。

## 進階：再往深一層

### Load Config Directory（Entry 10）

exploit 最重要但新手最常漏看的目錄。它有：
- `SecurityCookie`：`/GS` 的 stack canary 值的 VA（Ch 20 會繞它）
- `GuardCFCheckFunctionPointer`：CFG 的 indirect call 檢查函式指標（Ch 32 CFG bypass 的目標）
- `GuardCFFunctionTable`：所有合法 indirect call target 的 RVA 列表
- `SEHandlerTable`/`SEHandlerCount`：`/SAFESEH` 的合法 SEH handler 列表

如果你在逆向一個目標：`dumpbin /loadconfig target.exe` 是確認緩解狀態的最可靠來源。

### Bound Import / Delay Import

- **Bound Import**：loader 直接把函式 VA 寫死進文件（Windows 98 時代最佳化），現代 PE 幾乎不用，`TimeDateStamp` 會是非 0 值
- **Delay Import**（Data Directory Entry 13）：類似 ELF 的 lazy binding，第一次呼叫時才由 `__delayLoadHelper2` 解析——注入後如果函式還沒被呼叫過，IAT 裡不是真實 VA 而是跳回 delay helper 的 stub

### PE32 vs PE32+（32-bit vs 64-bit）的實作差異

`BaseOfData`（+0x1C）在 PE32 存在，PE32+ 把它刪掉；`ImageBase` 在 PE32 是 DWORD（4B，+0x1C），在 PE32+ 是 ULONGLONG（8B，+0x18）。手寫 parser 時要先看 Magic 再決定偏移——見很多人因為直接 hardcode PE32 偏移去解 PE32+ 而吐出錯誤結果。

## 動手練習

用上面的 Python parser skeleton，**擴充它解析 Import Directory**：讀 DataDirectory[1] 的 RVA，遍歷 `IMAGE_IMPORT_DESCRIPTOR` 陣列（到全 0 為止），對每個 descriptor 印出 DLL 名稱，再遍歷 ILT（`OriginalFirstThunk`）把函式名稱全部印出來。最後對比 `objdump -p` 的輸出是否一致。

進階：再加一個函式讀 DataDirectory[5]，解析 .reloc 的每個 block 和每個條目，驗證 type=10 的 RVA 是否都落在有 rw 屬性的 section 裡（提示：type=10 是需要 loader 寫入的位置，應該在 rw section 或 SizeOfHeaders 範圍內）。

## 本章重點整理

- PE = DOS Header（e_lfanew）→ NT Headers（Signature + FileHeader + OptionalHeader）→ Section Headers → Section Data。所有 DataDirectory 指標是 RVA，VA = ImageBase + RVA。
- `DllCharacteristics` 是緩解的旗標集合：0x0040 ASLR、0x0100 DEP、0x4000 CFG；exploit 第一步是確認這個欄位。
- Import 三層：Import Descriptor（每個 DLL）→ ILT（函式名稱/序號的唯讀參考）→ IAT（loader 填入真實 VA，exploit 的寫入目標）。
- .reloc 存「哪些位置有 64-bit 絕對指標需要 ASLR rebase」，loader 遍歷後加 delta；shellcode 必須是 PIC 避免這個問題。

## 自我檢核

- [ ] 不看筆記，能說出從位元組 0 到 EntryPoint VA 的完整計算路徑（要涉及哪幾個欄位）
- [ ] 能解釋為什麼 IAT 要在記憶體裡是可寫的，但 ILT 不用；這對 exploit 意味著什麼
- [ ] 被問「這個 PE 有沒有 CFG」，能說出你要看哪個欄位、值是多少代表有
- [ ] 能解釋 `.reloc` 在 ASLR rebase 時的作用——如果一個 PE 沒有 .reloc 且 DYNAMIC_BASE 是 1，loader 會怎樣
- [ ] 能說出 TLS Callback 為什麼在 exploit/反調試上很重要（在 main 之前執行這件事）

## 延伸閱讀

### 官方文件

- **[PE Format — Microsoft Learn（Win32 API reference）](https://learn.microsoft.com/en-us/windows/win32/debug/pe-format)**
  - **讀哪裡**：整份；尤其是 Optional Header Windows-Specific Fields、Section Flags、Import/Export Directory 各節
  - **和本章的關聯**：本章的所有結構定義都以這份為準；遇到任何欄位不確定，這是權威來源
  - **前提**：能讀英文 + 懂 C struct

### 深度部落格

- **[Geoff Chappell — The PE File Structure](https://www.geoffchappell.com/studies/windows/win32/apisets/index.htm)**
  - **讀哪裡**：「Win32 Programs」下的 PE 各子節，尤其 Optional Header 和 Load Configuration Directory
  - **和本章的關聯**：Geoff 對 Windows 未文件化欄位的研究是業界最深，Load Config Directory 的各個 CFG 相關欄位必看
  - **前提**：本章讀完後

- **[corkami — PE 101（逆向工程師視角的 PE 圖解）](https://github.com/corkami/pics/blob/master/binary/PE101.png)**
  - **讀哪裡**：看圖；配套有 corkami 的 PE tricks repo，各種邊界與畸形 PE 案例
  - **和本章的關聯**：本章教你「正常 PE 長什麼樣」，corkami 補你「邊界 PE 還能怎麼玩」——AV/parser bypass 技法的 mindset

### 工具 / 實作

- **[pefile（Python）](https://github.com/erocarrera/pefile)**
  - **這是什麼**：成熟的 Python PE 解析庫，比本章的手寫 parser 完整很多
  - **和本章的關聯**：本章手寫 parser 是為了理解結構；真正做 exploit 分析時用 pefile 省時間

### 書籍

- **《Windows Internals, 7th Edition》— Part 1，Ch 3（Processes），Appendix A（PE Format）** — Yosifovich/Ionescu 等
  - **讀哪裡**：Appendix A 的 PE 結構說明，以及 Ch 3 中 loader 如何讀 PE 的部分
  - **和本章的關聯**：本章只講 PE 格式，Ch 4 講 loader 行為——Windows Internals 把兩者放在一起講，讀完下一章後回頭看效果最好

下一章我們把視角從「PE 文件」轉移到「loader 把它搬進記憶體的全程」：section 如何映射、ASLR 如何決定實際 ImageBase、.reloc 如何被套用、import 如何一步步解析完成、TLS callback 何時觸發。

→ [Ch 4 — 載入器與模組：image base / ASLR relocation / LDR](./04-loader-and-modules.md)
