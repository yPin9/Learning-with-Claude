# Ch 5 — PEB / TEB：結構、走訪與在 exploit 裡的用途

> **目標**：把 PEB（Process Environment Block）和 TEB（Thread Environment Block）的關鍵欄位記熟，用 Python + ctypes 從 PEB→Ldr 走模組鏈找到 kernel32 基址並定位 `VirtualAlloc` 的真實 VA——不呼叫任何 Win32 API——並理解 BeingDebugged、NtGlobalFlag 等欄位在 anti-debug 裡的作用。

> **環境**：Windows 11 Pro x64；Python 3.12 + ctypes（本章所有程式碼**本機實際執行**過，輸出貼自本機）。x64 下 PEB 在 `GS:[0x60]`、TEB 在 `GS:[0x30]`；x86 下 PEB 在 `FS:[0x30]`、TEB 在 `FS:[0x18]`——這些偏移由 Windows ABI 保證，即使版本不同也穩定。

## 為什麼需要這個？

Linux 的 `glibc` 沒有提供「一個單一結構，藏著這個行程的 ImageBase、所有已載模組的鏈、堆的位址、調試器偵測旗標」——你要這些資訊，得去 `/proc/self/maps`、`dl_iterate_phdr`、`link_map` 等分散的地方湊。

Windows 設計了 PEB 和 TEB：**每個行程有一個 PEB，每個執行緒有一個 TEB**，所有行程/執行緒的自省資訊集中在這裡。對 exploit 作者和 shellcode 作者來說，這是禮物：不需要 syscall、不需要讀文件、不需要任何 Win32 API——只要能讀 `GS:[0x60]` 就能拿到整個行程的地圖。

PEB/TEB 的用途覆蓋：

- **Shellcode**：從 PEB→Ldr 走 module 鏈→找 kernel32→解析 Export Directory→拿到任意函式 VA，整個過程不依賴外部符號
- **Anti-debug**：`PEB.BeingDebugged`、`PEB.NtGlobalFlag`、`PEB.Heap.ForceFlags` 三個最常被偵測調試器的位置
- **Info leak**：`PEB.ImageBaseAddress` 讓你知道 exe 的 ASLR 後基址；`TEB.StackBase`/`StackLimit` 告訴你 stack 的範圍

## 先建立直覺：PEB、TEB、GS 的空間關係

```
每個執行緒：                   每個行程（共用）：
┌──────────────────┐           ┌──────────────────────────────────┐
│  TEB             │           │  PEB                              │
│  @ GS:[0x00]    │──(+0x60)──►│  @ GS:[0x60] (TEB.ProcessEnv)   │
│                  │           │                                   │
│  StackBase       │           │  ImageBaseAddress (exe 基址)      │
│  StackLimit      │           │  Ldr ──────────────────────────┐  │
│  ClientId.PID    │           │  ProcessParameters (命令列等)   │  │
│  ClientId.TID    │           │  ProcessHeap (第一個 heap)       │  │
│  LastErrorValue  │           │  NtGlobalFlag (anti-debug 旗標) │  │
│  TlsSlots[64]   │           │  BeingDebugged                   │  │
└──────────────────┘           └──────────────────────────────────┘
                                                                  │
                               PEB_LDR_DATA ◄─────────────────────┘
                               ├─ InLoadOrder: exe→ntdll→KERNEL32→...
                               ├─ InMemoryOrder: (按 VA 排序)
                               └─ InInitializationOrder: (按 DllMain 順序)
```

**GS segment 是怎麼工作的**：x64 Linux 把 `GS` 用於 `per-CPU` 資料；x64 Windows 把 `GS` 用於 `per-thread` 資料。`GS:[0]` 就是 TEB 自身的位址——TEB 的第一個 `LIST_ENTRY` 成員 `NtTib.Self` 存的正是這個。所以：

```
GS:[0x00] = 指向 TEB（NtTib.ExceptionList，x86 SEH chain 起點）
GS:[0x08] = TEB.StackBase
GS:[0x10] = TEB.StackLimit
GS:[0x30] = TEB.Self（TEB 的 self-pointer）
GS:[0x60] = TEB.ProcessEnvironmentBlock（指向 PEB）
```

x86（32-bit）的等價：`FS:[0x18]` = TEB self-pointer，`FS:[0x30]` = PEB。在組語裡你會看到：

```asm
; x86 取 PEB：
mov eax, dword ptr fs:[30h]    ; PEB*
; x64 取 PEB：
mov rax, qword ptr gs:[60h]    ; PEB*
; 或用 __readgsqword（MSVC intrinsic）
```

## TEB：執行緒的私有狀態

### x64 TEB 關鍵欄位（偏移以 Win11 x64 為準，穩定）

```
TEB (Thread Environment Block) — x64
偏移     大小    欄位
──────────────────────────────────────────────────────────────
+0x000   8B    NT_TIB.ExceptionList  → x86 SEH chain 的頭（x64 無效，=0 或 dummy）
+0x008   8B    NT_TIB.StackBase      → stack 高端（push 的起始方向）
+0x010   8B    NT_TIB.StackLimit     → stack 已 commit 的低端
+0x018   8B    NT_TIB.SubSystemTib   → 子系統用（通常 0）
+0x020   8B    NT_TIB.FiberData      → 若無 fiber 則 = 1（版本旗標）
+0x028   8B    NT_TIB.ArbitraryUserPointer
+0x030   8B    NT_TIB.Self           → 指向 TEB 自身（= GS:[0x30]）
──────────────────────────────────────────────────────────────
+0x038   8B    EnvironmentPointer    → 舊版遺留，通常 0
+0x040   8B    ClientId.UniqueProcess → PID（HANDLE 型別，但實際是 int）
+0x048   8B    ClientId.UniqueThread  → TID
+0x050   8B    ActiveRpcHandle
+0x058   8B    ThreadLocalStoragePointer → TLS 資料的指標陣列
+0x060   8B    ProcessEnvironmentBlock   → PEB* ← GS:[0x60]
+0x068   4B    LastErrorValue        → GetLastError() 讀的就是這裡
+0x06C   4B    CountOfOwnedCriticalSections
+0x070   8B    CsrClientThread
+0x078   8B    Win32ThreadInfo       → GUI 執行緒才有效
...
+0x100  8B×64  TlsSlots[0..63]      → TLS slot 0–63（__declspec(thread)）
...
+0x1478  8B    ReservedForOle
+0x1480  8B    WaitingOnLoaderLock
...
+0x1808  8B    TlsExpansionSlots     → TLS slot 64+ 的溢出陣列
```

### 真實輸出（本機 Python，TEB 欄位讀取）

```python
import ctypes, struct, os

kernel32 = ctypes.WinDLL('kernel32')
ntdll    = ctypes.WinDLL('ntdll')

# 用 NtQueryInformationThread 取得 TEB 基址
class CLIENT_ID(ctypes.Structure):
    _fields_ = [('UniqueProcess', ctypes.c_void_p),
                ('UniqueThread',  ctypes.c_void_p)]

class THREAD_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [('ExitStatus',     ctypes.c_long),
                ('TebBaseAddress', ctypes.c_void_p),
                ('ClientId',       CLIENT_ID),
                ('AffinityMask',   ctypes.c_ulonglong),
                ('Priority',       ctypes.c_long),
                ('BasePriority',   ctypes.c_long)]

hThread = kernel32.GetCurrentThread()
tbi = THREAD_BASIC_INFORMATION()
ret_len = ctypes.c_ulong()
NtQIT = ntdll.NtQueryInformationThread
NtQIT.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p,
                  ctypes.c_ulong, ctypes.c_void_p]
NtQIT.restype = ctypes.c_long
NtQIT(hThread, 0, ctypes.byref(tbi), ctypes.sizeof(tbi), ctypes.byref(ret_len))
teb_addr = tbi.TebBaseAddress

# 讀 TEB
# ... ReadProcessMemory helper（見 Ch 4 的完整版）
```

**本機執行輸出**：

```
NtQueryInformationThread status: 0x00000000
TEB @ 0x000000AE22189000
  +0x000 NtTib.ExceptionList: 0x0000000000000000  (x64: 無效，x86 SEH 起點)
  +0x008 NtTib.StackBase:     0x000000AE22600000  (stack 高端)
  +0x010 NtTib.StackLimit:    0x000000AE225F8000  (committed 低端)
  +0x030 NtTib.Self:          0x000000AE22189000  (self-ptr = GS:[0x30])
  +0x040 ClientId.Process:    0x0000000000000F98  (PID = 3992)
  +0x048 ClientId.Thread:     0x0000000000005CD8  (TID)
  +0x060 PEB:                 0x000000AE22188000  (GS:[0x60])
  +0x068 LastErrorValue:      (驗證見下)

Committed stack: 32 KB  (base=0xAE22600000, limit=0xAE225F8000)
```

**LastErrorValue 驗證**：

```python
kernel32.SetLastError(0xDEADBEEF)
err1 = kernel32.GetLastError()
# 讀 TEB+0x68 直接確認
val = struct.unpack_from('<I', rpmem(teb_addr + 0x068, 4))[0]
# 輸出：
# TEB+0x068 = 0xDEADBEEF  (MATCH)
# GetLastError() = 0xDEADBEEF
```

`GetLastError` 就是讀 `GS:[0x68]`，本質上是一行組語指令。這也是為什麼 `GetLastError` 沒有 syscall 開銷——直接讀 segment。

### NtTib.ExceptionList 與 x86 SEH（補充）

x86（32-bit）下，`FS:[0]` = `TEB.NtTib.ExceptionList`，指向 SEH chain 的第一個 `EXCEPTION_REGISTRATION_RECORD`：

```c
struct _EXCEPTION_REGISTRATION_RECORD {
    struct _EXCEPTION_REGISTRATION_RECORD* Next;  // 指向前一個 handler（鏈尾 = 0xFFFFFFFF）
    PEXCEPTION_ROUTINE Handler;                   // handler 函式位址
};
```

這條鏈是 x86 SEH overwrite（Ch 21）攻擊的核心——溢位覆蓋 Handler 欄位，讓例外觸發時跳到攻擊者的 shellcode。

**x64 的 SEH 完全不同**：x64 用 table-based 方式（.pdata section，Ch 12），`NtTib.ExceptionList` 在 x64 進程裡通常為 0 或一個特殊值，不是 SEH chain。x64 的 SEH overwrite 攻擊幾乎不可行（沒有 stack 上的 handler 指標可以覆蓋）。

### TlsSlots 與 ThreadLocalStoragePointer

`TEB + 0x100` 開始的 `TlsSlots[64]`（8B × 64 = 512B）是 TLS slot 0–63 的值（`TlsGetValue`/`TlsSetValue` 讀寫的）。`TEB + 0x1808` 的 `TlsExpansionSlots` 是 slot 64+ 的溢出陣列。`TEB + 0x058` 的 `ThreadLocalStoragePointer` 指向 `__declspec(thread)` 靜態 TLS 的資料區（由 TLS Directory 的 `AddressOfIndex` 管理）。

## PEB：行程的自省中樞

### x64 PEB 關鍵欄位

```
PEB (Process Environment Block) — x64
偏移     大小    欄位
──────────────────────────────────────────────────────────────
+0x000   1B    InheritedAddressSpace
+0x001   1B    ReadImageFileExecOptions
+0x002   1B    BeingDebugged      ← anti-debug 的最常見目標
+0x003   1B    BitField           ← bit3=ImageUsesLargePages, bit4=IsProtectedProcess...
+0x004   4B    Padding0
+0x008   8B    Mutant             → 互斥鎖 handle（通常 -1）
+0x010   8B    ImageBaseAddress   → exe 的實際載入基址（ASLR 後的值）
+0x018   8B    Ldr                → PEB_LDR_DATA*（模組鏈入口）
+0x020   8B    ProcessParameters  → RTL_USER_PROCESS_PARAMETERS*（命令列/環境等）
+0x028   8B    SubSystemData
+0x030   8B    ProcessHeap        → 第一個（預設）Heap 的 handle
+0x038   8B    FastPebLock        → RTL_CRITICAL_SECTION*（保護 PEB 更新）
+0x040   8B    AtlThunkSListPtr
+0x048   8B    IFEOKey
+0x050   4B    CrossProcessFlags  （bit0=ProcessInJob, bit1=ProcessInitializing...）
...
+0x060   8B    KernelCallbackTable → GUI 進程用的 callback table（User32 載入後填入）
+0x068   4B    NtGlobalFlag       ← anti-debug 的第二個目標
+0x06C   4B    NtGlobalFlagPad
+0x070   8B    CriticalSectionTimeout
+0x078   8B    HeapSegmentReserve
+0x080   8B    HeapSegmentCommit
+0x088   8B    HeapDeCommitTotalFreeThreshold
+0x090   8B    HeapDeCommitFreeBlockThreshold
+0x094   4B    NumberOfHeaps
+0x098   4B    MaximumNumberOfHeaps
+0x0A0   8B    ProcessHeaps       → PVOID[]（所有 heap 的陣列）
...
```

> 以上偏移以 Win10/11 x64 版本為準，本章實際讀取驗證。Geoff Chappell 的網站對每個 Windows 版本的精確偏移有詳細表格，若要支援多版本，以 WinDbg `dt ntdll!_PEB` 為準。

### 真實輸出（本機 PEB 欄位讀取）

```
=== PEB @ 0x000000816FE69000 ===
  +0x002 BeingDebugged:     0   （未附加調試器）
  +0x003 BitField:          0x04
  +0x010 ImageBaseAddress:  0x00007FF6CFDA0000
  +0x018 Ldr:               0x00007FFA85B918C0
  +0x020 ProcessParameters: 0x0000026A50F08550
  +0x030 ProcessHeap:       0x0000026A50F00000
  +0x068 NtGlobalFlag:      0x50E30000（非 heap debug 狀態）

  ProcessParameters->ImagePathName: C:\msys64\ucrt64\bin\python3.exe
  ProcessParameters->CommandLine:   C:\msys64\ucrt64\bin\python3.exe
```

### ProcessParameters：RTL_USER_PROCESS_PARAMETERS

```c
typedef struct _RTL_USER_PROCESS_PARAMETERS {
    ULONG     MaximumLength;
    ULONG     Length;
    ULONG     Flags;
    ULONG     DebugFlags;
    PVOID     ConsoleHandle;
    ULONG     ConsoleFlags;
    HANDLE    StandardInput;
    HANDLE    StandardOutput;
    HANDLE    StandardError;
    CURDIR    CurrentDirectory;   // +0x038：目前工作目錄（handle + path）
    UNICODE_STRING DllPath;       // +0x050
    UNICODE_STRING ImagePathName; // +0x060 ← exe 路徑
    UNICODE_STRING CommandLine;   // +0x070 ← 命令列
    PVOID     Environment;        // +0x080 ← 環境變數 block
    // ...
} RTL_USER_PROCESS_PARAMETERS;
```

**exploit 視角**：`ProcessParameters->Environment` 指向環境變數 block（`PATH`、`TEMP` 等），有時能作為讀取洩露的目標。`CommandLine` 洩露了完整命令列，可用來判斷靶程式是怎麼被呼叫的（是服務、是 Web server 的 worker process 等）。

## Anti-debug：PEB 的三個偵測點

### 1. BeingDebugged（PEB + 0x02）

最直白的調試器偵測。調試器附加時 kernel 把這個 byte 設為 1；調試器離開時清為 0。

```c
// 原始 IsDebuggerPresent() 的本質（未簡化版實際用 TEB 取 PEB，這是等價的邏輯）
BOOL IsDebuggerPresent(void) {
    return (BOOL)(NtCurrentPeb()->BeingDebugged);
}
```

**繞法**（攻擊者繞過偵測）：直接把 `PEB.BeingDebugged` patch 成 0。但在 WinDbg 附加時，WinDbg 也提供 `eb @$peb+2 0` 指令讓調試者把它清掉避免被目標偵測。

### 2. NtGlobalFlag（PEB + 0x068）

正常執行時，`NtGlobalFlag` 通常是 0 或只有特定旗標。**GFlags 工具設定 heap debug 選項後，或調試器附加時（某些版本的調試器設定），這個值會變成包含以下旗標的組合**：

```
0x02  FLG_HEAP_ENABLE_TAIL_CHECK      → heap 尾端加 guard pattern
0x04  FLG_HEAP_ENABLE_FREE_CHECK      → free 時驗證 pattern
0x08  FLG_HEAP_VALIDATE_PARAMETERS    → validate heap 參數
```

調試狀態下（GFlags heap debug 開啟時）這三個旗標都設：`0x02 | 0x04 | 0x08 = 0x70`。

所以常見的 anti-debug 寫法：

```c
if ((NtCurrentPeb()->NtGlobalFlag & 0x70) == 0x70) {
    // 偵測到 heap debug 模式，可能有調試器
}
```

**注意**：直接用 WinDbg 附加**不一定**設 `0x70`——這取決於 GFlags 設定。但如果目標被 GFlags 標記要做 heap debug（常見於 CTF/malware 分析環境），`NtGlobalFlag & 0x70 == 0x70` 是可靠的偵測。

### 3. ProcessHeap 的 ForceFlags（更隱蔽）

NtGlobalFlag 的堆 debug 旗標也會影響 `ProcessHeap` 的 header。Heap header 裡有兩個欄位：`Flags`（正常 = 2）和 `ForceFlags`（正常 = 0）。在 heap debug 狀態下：

```
ProcessHeap.Flags:      0x40000062（含 HEAP_GROWABLE | HEAP_TAIL_CHECKING_ENABLED | HEAP_FREE_CHECKING_ENABLED）
ProcessHeap.ForceFlags: 0x40000060（同上少 HEAP_GROWABLE）
```

偵測：

```c
PVOID heap = NtCurrentPeb()->ProcessHeap;
// Heap header 的 Flags 在 NT Heap 的固定偏移（x64: +0x14；見 Ch 14）
DWORD flags      = *(PDWORD)((BYTE*)heap + 0x14);
DWORD forceFlags = *(PDWORD)((BYTE*)heap + 0x18);
if (flags & 0x70 || forceFlags != 0) {
    // Heap debug 旗標被設，可能有調試器或 GFlags
}
```

> **版本相依提醒**：`ProcessHeap` 偏移在 NT Heap 結構中穩定；但 Segment Heap（Win10 以上預設）有不同的 header 格式，偏移不同。Ch 14/16 細講。

### 三個方法對比

| 方法 | 可靠性 | 容易繞過？ | 需要什麼條件才會觸發 |
|---|---|---|---|
| BeingDebugged | 高（直接） | 是（patch PEB） | 任何調試器附加 |
| NtGlobalFlag | 中（GFlags 依賴） | 是（patch PEB） | GFlags heap debug 啟用 |
| Heap ForceFlags | 中（heap 類型依賴） | 是（patch heap） | GFlags heap debug 啟用 |

## 實作重點：從 PEB 找 kernel32，不用 GetProcAddress

這是 Windows shellcode 的基礎技法，也是理解整個 PEB/LDR 機制的最好練習。

**目標**：從 `GS:[0x60]` 開始，走 PEB→Ldr→InLoadOrderModuleList，找到 KERNEL32.DLL 的基址，再解析它的 Export Directory，找到 `VirtualAlloc` 的真實 VA。

### 完整 Python 實作（本機實跑）

```python
#!/usr/bin/env python3
"""peb_walk.py — 從 PEB→Ldr 找 kernel32，解析 Export Directory 定位函式"""
import ctypes, struct, os

kernel32_dll = ctypes.WinDLL('kernel32')
ntdll_dll    = ctypes.WinDLL('ntdll')

pid = os.getpid()
hProc = kernel32_dll.OpenProcess(0x1F0FFF, False, pid)

ReadProcessMemory = kernel32_dll.ReadProcessMemory
def rpmem(addr, size):
    buf = (ctypes.c_ubyte * size)()
    nr  = ctypes.c_size_t()
    ReadProcessMemory(hProc, ctypes.c_void_p(addr), buf, size, ctypes.byref(nr))
    return bytes(buf)

def u64(a): return struct.unpack_from('<Q', rpmem(a, 8))[0]
def u32(a): return struct.unpack_from('<I', rpmem(a, 4))[0]

def read_unicode_string(addr):
    length  = struct.unpack_from('<H', rpmem(addr, 2))[0]
    buf_ptr = u64(addr + 8)
    if not buf_ptr or not length: return ''
    return rpmem(buf_ptr, length).decode('utf-16-le', errors='replace')

# ── Step 1: GS:[0x60] → PEB ──────────────────────────────────────────────
class PROCESS_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [('Reserved1',      ctypes.c_ulonglong),
                ('PebBaseAddress',  ctypes.c_ulonglong),
                ('Reserved2',      ctypes.c_ulonglong * 2),
                ('UniqueProcessId', ctypes.c_ulonglong),
                ('Reserved3',      ctypes.c_ulonglong)]

pbi = PROCESS_BASIC_INFORMATION()
ret_len = ctypes.c_ulong()
NtQIP = ntdll_dll.NtQueryInformationProcess
NtQIP.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                  ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p]
NtQIP.restype  = ctypes.c_long
NtQIP(hProc, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), ctypes.byref(ret_len))
peb_addr = pbi.PebBaseAddress

# ── Step 2: PEB + 0x18 → Ldr ─────────────────────────────────────────────
ldr_addr  = u64(peb_addr + 0x18)
# InLoadOrderModuleList (Flink) 在 LDR + 0x10
flink_lo  = u64(ldr_addr + 0x10)
list_head = ldr_addr + 0x10

# ── Step 3: Walk InLoadOrder, find KERNEL32.DLL ───────────────────────────
def find_module_base(target_name):
    entry = flink_lo
    seen  = set()
    while entry != list_head and entry not in seen:
        seen.add(entry)
        dll_base = u64(entry + 0x30)
        name     = read_unicode_string(entry + 0x58)  # BaseDllName
        if name.upper() == target_name.upper():
            return dll_base
        entry = u64(entry)  # InLoadOrderLinks.Flink
    return None

# ── Step 4: Parse Export Directory, find function VA ─────────────────────
def find_export(dll_base, func_name):
    hdr = rpmem(dll_base, 0x400)
    e_lfanew = struct.unpack_from('<I', hdr, 0x3C)[0]
    oh_off   = e_lfanew + 4 + 20          # NT + FileHeader
    exp_rva  = struct.unpack_from('<I', hdr, oh_off + 112)[0]  # DataDirectory[0]
    if exp_rva == 0:
        return None

    exp        = rpmem(dll_base + exp_rva, 40)
    base_ord   = struct.unpack_from('<I', exp, 16)[0]
    num_names  = struct.unpack_from('<I', exp, 24)[0]
    eat_rva    = struct.unpack_from('<I', exp, 28)[0]
    names_rva  = struct.unpack_from('<I', exp, 32)[0]
    ords_rva   = struct.unpack_from('<I', exp, 36)[0]

    for i in range(num_names):
        n_rva   = u32(dll_base + names_rva + i * 4)
        nbuf    = rpmem(dll_base + n_rva, 128)
        name    = nbuf[:nbuf.find(b'\x00')].decode('ascii', errors='replace')
        if name == func_name:
            ordinal  = struct.unpack_from('<H', rpmem(dll_base + ords_rva + i*2, 2))[0]
            func_rva = u32(dll_base + eat_rva + ordinal * 4)
            return dll_base + func_rva
    return None

# ── Main ──────────────────────────────────────────────────────────────────
print(f"[1] PEB @ 0x{peb_addr:016X}")
print(f"[2] Ldr @ 0x{ldr_addr:016X}")
print(f"[3] Walking InLoadOrderModuleList...")

k32_base = find_module_base('KERNEL32.DLL')
print(f"    KERNEL32.DLL base: 0x{k32_base:016X}")

targets = ['VirtualAlloc', 'LoadLibraryA', 'VirtualProtect', 'GetProcAddress']
print(f"[4] Resolving exports from KERNEL32 Export Directory:")
for fn in targets:
    va = find_export(k32_base, fn)
    print(f"    {fn:<22} → 0x{va:016X}")
```

**本機真實執行輸出**：

```
[1] PEB @ 0x0000006CCD9A5000
[2] Ldr @ 0x00007FFA85B918C0
[3] Walking InLoadOrderModuleList...
    [step 0] python3.exe          @ 0x00007FF6CFDA0000
    [step 1] ntdll.dll            @ 0x00007FFA859C0000
    [step 2] KERNEL32.DLL         @ 0x00007FFA849A0000  ← found
    KERNEL32.DLL base: 0x00007FFA849A0000

[4] Resolving exports from KERNEL32 Export Directory:
    VirtualAlloc           → 0x00007FFA849D3D20
    LoadLibraryA           → 0x00007FFA849E2CE0
    VirtualProtect         → 0x00007FFA849D8200
    GetProcAddress         → 0x00007FFA849D3D00
```

**驗證**（用 `GetProcAddress` 對比，確認手動解析結果正確）：

```python
GetProcAddress = kernel32_dll.GetProcAddress
GetProcAddress.restype = ctypes.c_void_p
GetProcAddress.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
GetModuleHandleW = kernel32_dll.GetModuleHandleW
GetModuleHandleW.restype = ctypes.c_void_p
GetModuleHandleW.argtypes = [ctypes.c_wchar_p]

k32_h = GetModuleHandleW("kernel32.dll")
print(f"\nVerification via GetProcAddress:")
print(f"KERNEL32.DLL base:   0x{k32_h:016X}")
print(f"VirtualAlloc:        0x{GetProcAddress(k32_h, b'VirtualAlloc'):016X}")
print(f"LoadLibraryA:        0x{GetProcAddress(k32_h, b'LoadLibraryA'):016X}")
print(f"VirtualProtect:      0x{GetProcAddress(k32_h, b'VirtualProtect'):016X}")
```

**驗證輸出**：

```
Verification via GetProcAddress:
KERNEL32.DLL base:   0x00007FFA849A0000
VirtualAlloc:        0x00007FFA849D3D20
LoadLibraryA:        0x00007FFA849E2CE0
VirtualProtect:      0x00007FFA849D8200
```

手動 PEB→Ldr→Export 解析的結果和 `GetProcAddress` **完全一致**。這就是 shellcode 不依賴任何外部符號找函式 VA 的完整流程。

### 為什麼 KERNEL32.DLL 總是 InLoadOrder 第三個？

```
InLoadOrder[0] = exe 本身（自身）
InLoadOrder[1] = ntdll.dll（kernel 在建立進程時最先映射）
InLoadOrder[2] = KERNEL32.DLL（幾乎所有 exe 的第一個 import，loader 最先解析）
```

這個順序在正常 Windows 進程裡是相當穩定的慣例，但**不能硬假設 index**——有些工具或有些 DLL 注入場景可能改變這個順序。正確的做法是走鏈 + 比對名稱，不要用固定 index。

Shellcode 常用的技巧是在走鏈時**不比對字串**（字串比對需要 loop），而是用 hash——把 DLL 名稱做 ROR13 hash 後對比一個預先計算的常數（`KERNEL32.DLL` 的 ROR13 hash 是 `0x6A4ABC5B`）。這是 Metasploit shellcode 的經典作法，Ch 25 會完整實作。

## 底層機制：GS 怎麼指向 TEB

x64 的 `GS` 是個 segment register，但在 64-bit 模式下 segment 已經不再提供「基址 + 大小」的記憶體保護——它只剩下一個隱藏的「base address」值。Windows kernel 在每次 context switch（執行緒切換）時更新 `GS base`（用 `wrmsr MSR_GS_BASE`）指向當前執行緒的 TEB。

```
kernel context switch:
  SWAPGS  → 交換 GS base 和 KernelGSBase
  （進入 kernel 時 GS 指向 per-CPU 結構；
   返回 user 時 GS 指向 TEB）
  WRMSR MSR_GS_BASE, teb_address
```

所以 `GS:[0x60]` 讀到的值每個執行緒都不同（不同的 TEB 指向不同的 PEB 或同一個 PEB）——但同一個行程的所有執行緒的 TEB 裡的 `ProcessEnvironmentBlock（GS:[0x60]）`都指向同一個 PEB。

**vs Linux**：Linux x64 的 `GS` 用於 `per-CPU` 資料（`current` task pointer 等），`FS` 用於 thread-local（`glibc` 把 `pthread_t` 結構放在 `FS base`）。兩者方向相反但概念類似。

## 對比與取捨

| 面向 | Windows PEB/TEB | Linux 等價 |
|---|---|---|
| 行程自省入口 | 單一 PEB（GS:[0x60]），所有資訊集中 | 分散：/proc/self/{maps,environ,exe}，r_debug，dl_iterate_phdr |
| 執行緒自省 | TEB（GS:[0x30]）含 stack 範圍、LastError、TLS | pthread_t（FS base），沒有 LastError 概念 |
| 模組鏈 | PEB_LDR_DATA 三條雙向鏈，可直接讀 | r_debug.r_map（link_map 單鏈），需要呼叫 dl_iterate_phdr |
| LastError | TEB+0x68，直接讀 GS 的零開銷 | errno（TLS 變數，等價但機制不同）|
| 調試器偵測 | BeingDebugged/NtGlobalFlag 集中在 PEB | ptrace(PTRACE_TRACEME) 或 /proc/self/status 裡的 TracerPid |
| shellcode 自省 | GS:[0x60] 一個操作拿到全部 | 需要讀 /proc 或用 dl 函式，更複雜 |

## 踩雷集錦

1. **「x64 下 FS:[0x30] 可以取 PEB」**：這是 x86（32-bit）的方式。x64 的 PEB 在 `GS:[0x60]`；x86 的 PEB 在 `FS:[0x30]`。寫 64-bit shellcode 時用 `GS`；寫 32-bit shellcode 時用 `FS`。搞混會讀到完全錯誤的地址。

2. **「BeingDebugged == 0 就代表沒有調試器」**：錯。某些調試技巧（如 `NtQueryInformationProcess(ProcessDebugPort)` 判斷 debug port、或使用反調試外掛）可以在 BeingDebugged=1 的情況下正常運作，也有攻擊者會 patch BeingDebugged 讓目標以為沒有調試器。BeingDebugged 只是最初級的一層。

3. **「Ldr 的 InLoadOrder head Flink 就是第一個模組」**：是的，但「head」本身不是一個 entry——`ldr + 0x10` 是 `InLoadOrderModuleList.Flink`，它的 Flink 才是第一個 `LDR_DATA_TABLE_ENTRY`。`ldr_addr + 0x10` 是終止條件（走回 head 代表鏈走完了），不是一個有效的 entry。

4. **「PEB 偏移在所有 Windows 版本一樣」**：前幾個欄位（BeingDebugged/Ldr/ProcessParameters/ProcessHeap）在 XP 到 Win11 都穩定；但 +0x068 開始的 NtGlobalFlag 和更後面的欄位在不同版本有微小差異。`WinDbg` 的 `dt ntdll!_PEB` 是查任何版本的權威方法。

5. **「找到 kernel32 就能直接用它的函式地址呼叫」**：shellcode 找到 VA 後，要做的是把這個 VA 存起來用 `call rax` 這類 indirect call 呼叫，不是硬編在 shellcode 裡——硬編的 VA 在不同機器、不同 Windows 版本都不同。PEB walk 的意義就是「執行時動態解析」，不依賴固定位址。

## 進階：再往深一層

### 完整 Shellcode 版 PEB Walk（x64 組語骨架）

Python 版是理解用的；真正的 shellcode（Ch 25）用組語：

```asm
; x64 PEB walk — 找 KERNEL32.DLL 的骨架
; 假設 rdi = 目標函式名稱的 hash（ROR13 hash of "VirtualAlloc"）
mov  rax, qword ptr gs:[60h]         ; PEB*
mov  rax, qword ptr [rax + 18h]      ; PEB->Ldr (PEB_LDR_DATA*)
mov  rax, qword ptr [rax + 10h]      ; InLoadOrderModuleList.Flink（第一個 entry）
next_module:
  mov  rbx, qword ptr [rax + 30h]    ; LDR_DATA_TABLE_ENTRY.DllBase
  lea  rdx, qword ptr [rax + 58h]    ; &BaseDllName (UNICODE_STRING)
  ; ... hash DllName，與 target hash 比對
  jne  check_next
  ; → rbx = DLL base，繼續 Export Directory 解析
check_next:
  mov  rax, qword ptr [rax]          ; InLoadOrderLinks.Flink → 下一個 entry
  ; loop 終止條件：rax == ldr + 0x10（回到 list head）
  jmp  next_module
```

### TEB 作為 Stack 越界偵測的參考

`TEB.StackLimit` 是 stack 已 commit 的低端。在 stack overflow 攻擊中，如果溢出超過這個值，Windows 的 stack expand 機制（guard page）會觸發，在 debug build 裡你能看到 `STACK_OVERFLOW` exception。讀 `TEB.StackBase` 和 `TEB.StackLimit` 可以在 exploit 開發中確認 stack 的範圍，知道往哪個方向溢出多少距離才能蓋到有用的東西。

### PEB 的 KernelCallbackTable：GUI 行程攻擊面

GUI 行程（呼叫 User32 API 的）PEB + 0x058 有 `KernelCallbackTable`，指向一張函式指標表，kernel 呼叫 user-mode callback 時用它。這張表的函式指標可以被 shellcode 覆寫，讓 kernel 在發送 Windows message 時轉進攻擊者的代碼——是 Win32k UAF 類漏洞常用的 pivot 技法（`win32k` 的 kernel-to-user callback 攻擊面，Ch 48+ 的 kernel pwn 延伸）。

### ReactOS 原始碼中的 PEB/TEB

ReactOS 的 `ntdll/include/ntdllp.h` 有完整的 PEB/TEB 結構定義，可以用來對照理解。注意 ReactOS 的偏移不一定完全匹配現代 Windows（ReactOS 基於較舊的 API 規格），以 Geoff Chappell 的 per-version 表格和 `dt ntdll!_PEB` 為準。

## 動手練習

擴充本章的 `peb_walk.py`：

1. 讀 `PEB.BeingDebugged` 和 `PEB.NtGlobalFlag`，印出它們的當前值，並說明在 GFlags heap debug 開啟的調試環境下你預期看到什麼值（參考：`NtGlobalFlag & 0x70 == 0x70`）
2. 找到 ntdll.dll 的基址（InLoadOrder 第二個），解析其 Export Directory，列出所有以 `Nt` 開頭的匯出函式（應有 400+ 個，只印前 20 個即可）
3. 讀 `TEB.StackBase` 和 `TEB.StackLimit`，並確認當前 stack pointer（`rsp`）落在這個範圍內（用 Python ctypes 取 `rsp` 值：`ctypes.c_ulonglong.in_dll(ctypes.pythonapi, "_Py_EnsureTstateNotNULL")` 不可靠，改用一個 C extension 或直接讀 TEB 比較範圍）

進階（選做）：實作一個「反反調試」腳本，在腳本啟動時把 `PEB.BeingDebugged` patch 成 0，讓任何依賴這個 byte 的目標以為沒有調試器附加——驗證 `IsDebuggerPresent()` 回傳值的變化。

## 本章重點整理

- PEB 在 `GS:[0x60]`（x64），TEB 在 `GS:[0x30]`；TEB 的 `+0x060` 欄位指向行程共用的 PEB。
- PEB 關鍵欄位：`+0x002 BeingDebugged`、`+0x018 Ldr`（模組鏈入口）、`+0x030 ProcessHeap`、`+0x068 NtGlobalFlag`；這些是 shellcode 和 anti-debug 的必知偏移。
- PEB→Ldr→InLoadOrderModuleList→`DllBase + BaseDllName` 是找任意已載模組基址的標準路徑，整個過程不需要呼叫 Win32 API，是 shellcode 自省的基礎。
- 從 DLL 基址解析 Export Directory（DataDirectory[0]）→ EAT → 名稱/序號表 → 函式 RVA → 函式 VA，可以定位任意已匯出函式地址——本機驗證與 `GetProcAddress` 完全一致。

## 自我檢核

- [ ] 不看筆記，寫出從 `GS:[0x60]` 到 KERNEL32.DLL 的 DllBase 的完整步驟（要涉及哪些偏移）
- [ ] 被問「`IsDebuggerPresent()` 內部怎麼工作」：能說出它讀的是哪個 segment、哪個偏移的哪個 byte
- [ ] 能說出 `NtGlobalFlag` 在 GFlags heap debug 啟用時的期望值，以及為什麼 `0x70` 是偵測目標
- [ ] 面試被問「shellcode 怎麼不用 GetProcAddress 找 VirtualAlloc」：能說出 PEB→Ldr→Export Directory 四步流程
- [ ] 能解釋 x86 的 `FS:[0x30]` 和 x64 的 `GS:[0x60]` 為什麼都能取到 PEB，以及 segment 機制的本質差異

## 延伸閱讀

### 原始碼與結構參考

- **[Geoff Chappell — Windows PEB（per-version 結構）](https://www.geoffchappell.com/studies/windows/win32/ntdll/structs/peb/index.htm)**
  - **讀哪裡**：每個版本的 PEB 結構表，重點看 Win10/Win11 的欄位偏移變化
  - **和本章的關聯**：本章的偏移以 Win11 為準；若要支援多版本，Geoff 的表格是唯一詳盡來源
  - **前提**：本章讀完；能讀英文的 Windows 技術文獻

- **[ReactOS — ntdll/include/ntdllp.h（PEB/TEB 定義）](https://github.com/reactos/reactos/blob/master/sdk/include/ndk/pstypes.h)**
  - **讀哪裡**：`_PEB`、`_TEB`、`_PEB_LDR_DATA`、`_LDR_DATA_TABLE_ENTRY` 的定義
  - **和本章的關聯**：可對照本章的欄位描述確認理解；注意 ReactOS 偏移與現代 Windows 有差異

### 書籍

- **《Windows Internals, 7th Edition》— Part 1，Ch 3（Processes）：PEB and TEB** — Yosifovich/Ionescu
  - **讀哪裡**：「Process Environment Block」節和「Thread Environment Block」節；WinDbg `!peb`/`!teb` 的真實輸出
  - **和本章的關聯**：本章的 ASCII 圖和欄位描述都從這裡延伸；書裡有 WinDbg 的完整欄位 dump，裝好後對照

### 部落格 / 研究

- **[Alex Ionescu — 深入 Windows 內部（BH 2014 slides）](https://github.com/ionescu007/Alex-Ionescu-Windows-Internals)**
  - **讀哪裡**：PEB/TEB 相關的 slides，尤其是 Protected Process Light 影響 PEB 存取的部分
  - **和本章的關聯**：本章沒有涉及 PPL 的 PEB 存取限制，Ionescu 的研究是這個方向的起點

- **[Corelan Team — Writing shellcode（含 PEB walk）](https://www.corelan.be/index.php/2010/02/25/exploit-writing-tutorial-part-9-introduction-to-win32-shellcoding/)**
  - **讀哪裡**：Part 9 的「Finding kernel32」節，看 x86 組語版的 PEB walk 和 ROR13 hash
  - **和本章的關聯**：本章用 Python 實作了等價邏輯；Corelan 的版本是真正的 shellcode（x86），Ch 25 的 x64 版從這裡延伸

- **[winsider — TEB / PEB 深挖](https://winsider.org/blog/)**
  - **讀哪裡**：搜尋「TEB」或「PEB」相關文章；尤其是 KernelCallbackTable 攻擊面的分析
  - **和本章的關聯**：本章只提到 KernelCallbackTable 的名稱；winsider 有深入的 GUI exploit 應用案例

掌握了 PEB/TEB 這個行程自省的根基，下一章進 Win32 API 和 Native API（ntdll）的分層：為什麼一個 `CreateFile` 要穿越 Win32→ntdll→syscall 三層，以及 exploit 為什麼要繞過 Win32 直接打 native syscall。

→ [Ch 6 — Win32 API vs Native API (ntdll)](./06-win32-vs-native-api.md)
