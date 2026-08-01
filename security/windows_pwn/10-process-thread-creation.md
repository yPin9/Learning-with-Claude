# Ch 10 — 行程與執行緒建立：CreateProcess 內部

> **目標**：學完這章你能說出 `CreateProcess` 的七個內部階段（從打開映像到 CSRSS 通知），解釋 `CreateThread`/`CreateRemoteThread`/`RtlCreateUserThread` 的差異與 exploit 上的意義，知道每個 thread 的 TEB 放在哪、初始執行流從 `RtlUserThreadStart` 到 `main` 怎麼走，並能把這些機制連到 process injection 的原語設計。

> **環境**：Windows 11 Pro x64；Python 3.12 + ctypes。本章的 `CreateProcess` 和 `CreateThread` 範例用 Python ctypes 實跑並貼真實輸出；WinDbg 除錯器相關的驗證（`dt _PEB`、`!teb`、TTD 追執行流）標注「未實測，理論預期」。

## 為什麼需要這個？

在 Linux，行程建立是 `fork` + `exec` 兩個 syscall 的組合；thread 用 `clone(2)`（pthread 在 userland 包 `clone`）。整個模型透明：`fork` 複製父行程的全部資源，`exec` 用新映像覆蓋，`clone` 共享位址空間建 thread。

Windows 的模型**根本不同**：

- 沒有 `fork`。每個新行程從零開始建立，父行程不被複製。
- `CreateProcess` 是一個複雜的多階段操作，橫跨使用者態/核心態/子系統（CSRSS），不是一個 syscall。
- Thread 建立有多個 API 入口，語意微妙不同，在 inject 技法裡各有用途。

為什麼漏洞研究者要懂這個？**Process injection 的所有原語——`CreateRemoteThread`、thread hijacking、APC injection、process hollowing——都建立在「行程/thread 建立的哪個環節可以插手」的理解上**。不懂建立流程，injection 技法只是背 API，不知道為什麼可行、防禦者為什麼能偵測。

## 先建立直覺

### Linux fork/exec vs Windows CreateProcess

```
  Linux                               Windows
  ──────                              ───────
  fork()          ← 複製父行程        CreateProcess()  ← 從頭建立，無複製
    ↓ 父行程繼續                          ↓
    ↓ 子行程執行 exec()                建映像 section（NtCreateSection）
      └─ exec 讀 ELF，替換位址空間      建行程核心物件（NtCreateUserProcess）
         載入 ld.so                    建初始 thread
         跳到 ld.so 入口              PE loader 在子行程裡執行
                                       CRT 初始化（mainCRTStartup）
                                       main()
```

Linux 的 `fork` 把問題分成「資源複製」和「映像替換」兩個乾淨步驟；Windows 把整個流程塞進一個 `CreateProcess`，好處是更高效（不複製無用資源），但實作複雜度高很多。

### 概念地圖

```
  CreateProcess("target.exe")
       │
  [User-mode, kernel32/ntdll]
       │
       ├─► 1. 打開映像，讀 PE 頭
       │
       ├─► 2. NtCreateSection：建映像 section（page-file 映射 PE）
       │
       ├─► 3. NtCreateUserProcess：建 EPROCESS、初始 ETHREAD、
       │        設定 PEB/TEB、映射 ntdll 到目標行程
       │
       ├─► 4. 設定行程參數（環境、命令列→寫入子行程 PEB）
       │
       ├─► 5. 通知 CSRSS（Win32 子系統登記新行程）
       │
       ├─► 6. ResumeThread：讓初始 thread 開始執行
       │
       └─► 7. [子行程] LdrInitializeThunk → 載入 DLL →
                RtlUserThreadStart → mainCRTStartup → main()
```

## CreateProcess 的七個內部階段

### 階段 1：打開映像、驗證 PE

`kernel32!CreateProcessW` 首先要求作業系統打開目標映像檔（`CreateFile`），讀取 PE 頭，決定：

- 是 PE32（32 位元）還是 PE32+（64 位元）？
- 有沒有 `IMAGE_DLLCHARACTERISTICS_WDM_DRIVER` 旗標（是驅動不是應用）？
- SxS（Side-by-Side Assembly）manifest 需不需要處理？

如果路徑解析後找不到執行檔，在這一步就直接失敗（`ERROR_FILE_NOT_FOUND`）。**VS Linux exec**：Linux 的 `execve` 也在 kernel 讀 ELF magic 和 interpreter 行；差異在 Windows 在使用者態（kernel32/ntdll）做更多工作。

### 階段 2：建映像 Section

```c
// 內部呼叫（ntdll 的 Native API）
NtCreateSection(&hSection,
    SECTION_ALL_ACCESS, NULL,
    NULL,           // 最大大小 = 檔案大小
    PAGE_EXECUTE,   // 初始保護
    SEC_IMAGE,      // 關鍵：SEC_IMAGE 讓 loader 依 PE 格式映射各 section
    hFile);
```

`SEC_IMAGE` 告訴 Memory Manager 這個 section 是 PE 映像——各 section（`.text`、`.data`、`.rdata`）按 `VirtualAddress`/`VirtualSize` 映射，每個 section 的保護屬性依 `Characteristics`（`IMAGE_SCN_MEM_EXECUTE` 等）決定。

這個 section 是**共享的**：同一個 `.exe` 被多個行程啟動時，`.text` 頁只有一份實體記憶體，各行程各自映射同一份 section 的不同 view。

### 階段 3：NtCreateUserProcess — 核心重鎮

這是 Windows Vista 以後建立行程的**真正 syscall 入口**（舊版 Windows 用 `NtCreateProcess` + 分別建 thread）。這個 syscall 做了大量工作：

```
  NtCreateUserProcess
  │
  ├─► 配置 EPROCESS 結構（kernel 物件）
  │     EPROCESS 包含：PEB 位址、VAD 根、Handle table、Token...
  │
  ├─► 建初始 ETHREAD（核心 thread 物件）
  │     ETHREAD 包含：TEB 位址、等待狀態、APC 佇列...
  │
  ├─► 映射 ntdll.dll 到新行程（每個行程都要有 ntdll）
  │     ntdll 的基址在 Win8+ 是固定的（不受 ASLR 影響於同版 OS 各行程間）
  │
  ├─► 建立 PEB（Process Environment Block）
  │     設定：映像基址、命令列、環境字串、loader 資料結構
  │
  └─► 建立初始 thread 的 TEB（Thread Environment Block）
        設定：stack 邊界（StackLimit/StackBase）、TEB 自身地址（Self 欄位）
```

回傳時父行程拿到：
- `PROCESS_INFORMATION.hProcess`：子行程的 HANDLE
- `PROCESS_INFORMATION.hThread`：子行程初始 thread 的 HANDLE
- `PROCESS_INFORMATION.dwProcessId` / `dwThreadId`：PID / TID

**這個 thread 以 suspended 狀態建立**——還沒執行。父行程可以在這時修改子行程（process hollowing、patch PEB、注入 shellcode）。

### 階段 4：設定行程參數

父行程把以下資料寫入子行程的 PEB（透過 `WriteProcessMemory` 或 kernel 直接寫）：

- `ProcessParameters`（`RTL_USER_PROCESS_PARAMETERS`）：命令列（`CommandLine`）、工作目錄（`CurrentDirectory`）、環境字串（`Environment`）、標準 handle（stdin/stdout/stderr）
- `ImageBaseAddress`：映像基址（如果有 ASLR，就是 relocated 後的）
- `Ldr`（`PEB_LDR_DATA *`）：loader 資料，`DllBase` 鏈在這裡

**為什麼 exploit 關心這個**：process hollowing（把子行程的映像換成惡意 PE）在這一步之後執行——用 `NtUnmapViewOfSection` 把原映像 unmap、`VirtualAllocEx` 配新空間、`WriteProcessMemory` 寫惡意 PE、然後修 PEB 的 `ImageBaseAddress`。

### 階段 5：通知 CSRSS

Windows 的 Win32 子系統（CSRSS，Client/Server Runtime Subsystem）需要知道新行程的存在——因為視窗管理、console、偵錯訊息都走 CSRSS。

ntdll 呼叫 `CsrClientCallServer()`（透過 ALPC port）通知 CSRSS 登記新行程。這個步驟在 kernel-only 的「mini process」（用 `NtCreateProcessEx` 不觸發 CSRSS）裡可以省略，但普通使用者態行程必做。

**偵測角度**：EDR 通常掛鉤（hook）`NtCreateUserProcess` 或監視 CSRSS 的 ALPC 流量來偵測新行程建立。注入技法如果繞過 `CreateProcess`（例如手動把 shellcode 寫進現有行程再 `CreateRemoteThread`）就不觸發這一步，是常見的 EDR 繞過思路。

### 階段 6：ResumeThread — 子行程開始執行

父行程呼叫 `ResumeThread(pi.hThread)`，讓初始 thread 的 suspended count 從 1 降到 0，kernel 把這個 thread 加入排程佇列。

子行程的第一個執行位址是 `ntdll!LdrInitializeThunk`（DLL 初始化起點），**不是** `main()`。

### 階段 7：子行程的啟動序列

```
  子行程初始 thread 執行流程：

  ntdll!LdrInitializeThunk（kernel 設定的起始位址）
       │
       ▼
  ntdll!LdrpInitialize
       │  ← 載入 ImportTable 裡的所有 DLL（依序）
       │  ← 執行每個 DLL 的 DllMain(DLL_PROCESS_ATTACH)
       │  ← 初始化 TLS（Thread Local Storage）
       ▼
  ntdll!RtlUserThreadStart（真正的 thread 起始包裝器）
       │  ← 設定 SEH frame（x86），或不用（x64 table-based）
       │  ← 呼叫執行緒函式（對初始 thread，就是 entry point）
       ▼
  mainCRTStartup（mingw 的 CRT 初始化，MSVC 類似）
       │  ← 初始化全域 C++ 物件（.init_array/.ctors）
       │  ← 初始化 stdio、locale、堆
       ▼
  main() / WinMain()
```

**VS Linux**：Linux 的 `_start`（由 linker 設定） → `__libc_start_main` → `main()`；Windows 的對應是 `LdrInitializeThunk` → `RtlUserThreadStart` → `mainCRTStartup` → `main()`。深度更多（DLL 載入在這裡才發生），但概念對應清楚。

## 實跑驗證（Python ctypes）

### CreateProcess

```python
import ctypes
import ctypes.wintypes as wt

class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb",              wt.DWORD),  ("lpReserved", wt.LPWSTR),
        ("lpDesktop",       wt.LPWSTR), ("lpTitle",    wt.LPWSTR),
        ("dwX",             wt.DWORD),  ("dwY",        wt.DWORD),
        ("dwXSize",         wt.DWORD),  ("dwYSize",    wt.DWORD),
        ("dwXCountChars",   wt.DWORD),  ("dwYCountChars", wt.DWORD),
        ("dwFillAttribute", wt.DWORD),  ("dwFlags",    wt.DWORD),
        ("wShowWindow",     wt.WORD),   ("cbReserved2", wt.WORD),
        ("lpReserved2",     ctypes.c_void_p),
        ("hStdInput",  wt.HANDLE), ("hStdOutput", wt.HANDLE), ("hStdError", wt.HANDLE),
    ]

class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wt.HANDLE), ("hThread", wt.HANDLE),
        ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD),
    ]

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
# ... (設好 argtypes / restype)

si = STARTUPINFOW(); si.cb = ctypes.sizeof(si)
pi = PROCESS_INFORMATION()
cmdline = ctypes.create_unicode_buffer("cmd /c echo hello-from-child-process")

ok = kernel32.CreateProcessW(
    None, cmdline, None, None, False, 0, None, None,
    ctypes.byref(si), ctypes.byref(pi))
```

**真實輸出**（本機實跑）：

```
============================================================
Current process/thread info
  PID = 33148
  TID = 31972

============================================================
CreateProcess: spawn cmd /c echo hello-from-child-process
  CreateProcess returned: 1
  child PID  = 27304
  child TID  = 14348
  hProcess   = 176
  hThread    = 372
  child exit code = 1
```

`hProcess = 176`、`hThread = 372` 是**本行程 handle table 裡的索引**，不是 PID/TID——這是 Ch 8 講過的 Handle 機制。子行程的 echo 輸出直接印到 console（因為我們繼承了 stdout handle）。

### CreateThread

```python
THREAD_FUNC = ctypes.WINFUNCTYPE(wt.DWORD, ctypes.c_void_p)

def thread_body(param):
    tid = kernel32.GetCurrentThreadId()
    print(f"  thread ran, param=0x{param:X}, TID={tid}")
    return 0

cb = THREAD_FUNC(thread_body)
tid_out = wt.DWORD(0)
hthread = kernel32.CreateThread(None, 0, cb, 0xDEAD, 0, ctypes.byref(tid_out))
```

**真實輸出**：

```
============================================================
CreateThread: run a small function in new thread
  hThread = 372, new TID = 35564
  thread ran, param=0xDEAD, TID=35564
```

新 thread 的 TID（35564）和當前 thread（31972）不同；`param=0xDEAD` 是我們傳進去的 `lpParameter`，對應 `thread_body` 的第一個參數。

## CreateThread / CreateRemoteThread / RtlCreateUserThread

這三個 API 的差異是 process injection 的核心分野：

```
  API                      在哪個行程建立    走 CSRSS？  SEH 設定？  正常 TEB？
  ─────────────────────────────────────────────────────────────────────────────
  CreateThread             本行程            是          是          是
  CreateRemoteThread       目標行程          是          是（部分）  是
  RtlCreateUserThread      目標行程          否          否          部分
  NtCreateThreadEx         目標行程          可設旗標    可控         是
```

### CreateThread

```c
HANDLE CreateThread(
    LPSECURITY_ATTRIBUTES lpThreadAttributes, // 通常 NULL
    SIZE_T                dwStackSize,         // 0 = 預設 1 MB
    LPTHREAD_START_ROUTINE lpStartAddress,    // 函式指標
    LPVOID                lpParameter,        // 傳給函式的 LPVOID
    DWORD                 dwCreationFlags,    // 0 = 立刻執行；CREATE_SUSPENDED = 掛起
    LPDWORD               lpThreadId         // 輸出 TID
);
```

標準本行程 thread 建立。內部調 `NtCreateThreadEx`，確保 CSRSS 知道新 thread，TEB 完整初始化，SEH frame 設好（x86），CRT 的 `_beginthread` 家族在這之上再包一層（初始化 thread-local 的 CRT 狀態）。

**VS Linux pthread**：`pthread_create` 呼叫 `clone(CLONE_VM|CLONE_FS|...)` 共享位址空間；`CreateThread` 呼叫 `NtCreateThreadEx` 再通知 CSRSS。概念一致，路徑不同。

### CreateRemoteThread

```c
HANDLE CreateRemoteThread(
    HANDLE hProcess,         // 目標行程的 HANDLE（需 PROCESS_CREATE_THREAD 權限）
    ...同 CreateThread...
);
```

在**另一個行程**建立 thread，執行指定位址的函式。這是 DLL injection 最基礎的方式：

```
  1. OpenProcess(PROCESS_ALL_ACCESS, ...)
  2. VirtualAllocEx(目標行程, ..., PAGE_EXECUTE_READWRITE)
  3. WriteProcessMemory(目標行程, 配置的位址, 要注入的 DLL 路徑)
  4. CreateRemoteThread(目標行程, ..., LoadLibraryW, DLL 路徑位址)
```

第 4 步讓目標行程建一個新 thread 去呼叫 `LoadLibraryW("malicious.dll")`——合法的 Win32 API，在目標行程的 context 裡執行。

**為什麼 EDR 能偵測**：`CreateRemoteThread` 呼叫 `NtCreateThreadEx`（帶 `CREATE_THREAD_HIDE_FROM_DEBUGGER` 旗標還是回存 ETHREAD），且**跨行程**建 thread 是明顯的跡象。EDR 掛鉤 `NtCreateThreadEx` 或監視 `OpenProcess` + `CreateRemoteThread` 序列。

### RtlCreateUserThread

`RtlCreateUserThread` 是 ntdll 的 semi-undocumented API，直接調 `NtCreateThreadEx` 但**不通知 CSRSS**（少了 `CsrClientCallServer` 這步）：

```c
// ntdll.dll 匯出（無官方文件）
NTSTATUS RtlCreateUserThread(
    HANDLE  ProcessHandle,
    PSECURITY_DESCRIPTOR SecurityDescriptor,
    BOOLEAN CreateSuspended,
    ULONG   StackZeroBits,
    PULONG  StackReserved,
    PULONG  StackCommit,
    PVOID   StartAddress,
    PVOID   StartParameter,
    PHANDLE ThreadHandle,
    PCLIENT_ID ClientId
);
```

**為什麼 injection 技法用它**：少了 CSRSS 通知，對某些 EDR 和 debugger 的 thread 追蹤會「隱身」（不在 `toolhelp32` thread list 裡）。但現代 EDR 也掛鉤 `NtCreateThreadEx`，這層隱藏效果有限。

### NtCreateThreadEx（底層直接呼叫）

更底層的 API，`hProcess` 可以是目標行程（遠端），`CreateFlags` 可傳 `THREAD_CREATE_FLAGS_HIDE_FROM_DEBUGGER`（`0x04`）使新 thread 對除錯器不可見。Meterpreter 等後滲透框架用過這個技巧，但現代偵測依然有效。

## Thread 的 TEB

每個 thread 都有一個 **TEB（Thread Environment Block）**，由 OS 在建立 thread 時分配：

```
  x64 TEB（部分欄位）

  GS:[0x00] = ExceptionList         ← x86 SEH chain 起點（x64 此欄位未用）
  GS:[0x08] = StackBase             ← stack 的高地址（bottom）
  GS:[0x10] = StackLimit            ← stack 的低地址（top，committed 部分）
  GS:[0x30] = Self                  ← TEB 自身的位址（GS base）
  GS:[0x38] = Reserved1             ← PID
  GS:[0x40] = ClientId.UniqueThread ← TID
  GS:[0x60] ← x86 用此存 PEB；x64 PEB 在 GS:[0x60] 的是 ProcessEnvironmentBlock
  ...
  GS:[0x1478] = FlsData             ← Fiber Local Storage
  GS:[0x1488] = StaticUnicodeBuffer ← 262 個 WCHAR 的臨時緩衝
```

> 精確偏移以版本為準。x64：TEB 在 `GS:[0x30]`（Self 欄位指向自身），PEB 在 `GS:[0x60]`（`ProcessEnvironmentBlock`）。x86：TEB 在 `FS:[0x18]`，PEB 在 `FS:[0x30]`，SEH chain 在 `FS:[0x00]`。這些值穩定（各版本 Win32 一致），但 TEB 的更大偏移欄位可能隨版本變動——用 WinDbg `dt ntdll!_TEB` 確認你環境的值。

```
  TEB 與 stack 的空間關係（x64）

  高地址
  ┌───────────────────────┐ ← StackBase（GS:[0x08]）
  │  thread stack         │  PAGE_READWRITE
  │  （使用中的 frame）   │
  ├───────────────────────┤
  │  GUARD page           │  PAGE_READWRITE | PAGE_GUARD
  ├───────────────────────┤
  │  reserved stack        │  MEM_RESERVE
  ├───────────────────────┤ ← StackLimit（GS:[0x10]，committed 下界）
  低地址

  TEB 本身位於 ──► 獨立的 MEM_PRIVATE 頁（不在 stack 上）
                   GS 段基址（GS:[0x30]）指向它
```

**exploit 意義**：TEB 的 `StackBase`/`StackLimit` 說明了合法 stack 的範圍——在 buffer overflow 時如果能讀/改 TEB（比如 heap 裡有 TEB 地址的指標），就能算出 stack 的位址，輔助 info leak。

## CRT 初始化：從 RtlUserThreadStart 到 main

```
  RtlUserThreadStart(PTHREAD_START_ROUTINE StartAddress, PVOID Argument)
       │
       ├─► [x86] 設定 SEH frame（ch_RtlUserThreadStart 是第一個 SEH handler）
       │    ← x64 不需要，用 table-based SEH
       │
       ▼
  StartAddress(Argument)
  ─────────────── 對初始 thread，StartAddress = PE 的 AddressOfEntryPoint ───────────────
       │
       ▼  (mingw 產生的 PE)
  __tmainCRTStartup / mainCRTStartup（CRT 包裝器）
       │
       ├─► 初始化 heap（`__heap_init`）
       ├─► 初始化 locale（`_setlocale`）
       ├─► 呼叫全域 C++ 建構子（`.init_array` / `atexit` 登記解構子）
       ├─► 初始化 stdio（`__ioinit`）
       │
       ▼
  main(argc, argv, envp) / WinMain(hInstance, hPrevInstance, lpCmdLine, nCmdShow)
       │
       ▼ 返回
  exit() → atexit handlers → ExitProcess
```

**VS glibc**：glibc 的 `_start` → `__libc_start_main` 也做類似的 CRT 初始化（全域建構子、stdio）；差異在 Windows 的「全域建構子」走 `.ctors`（老 mingw）或 `.CRT$XCU`（MSVC CRT），執行時機在 `DllMain(DLL_PROCESS_ATTACH)` **之後**（主 exe 的全域建構子在所有 DLL attach 之後才跑）。

### 為什麼 main 不是第一個跑的 C 程式碼

很多人以為程式從 `main` 開始，但實際上：

1. 所有 DLL 的 `DllMain(DLL_PROCESS_ATTACH)` 先跑（依 import 順序）
2. CRT 初始化
3. 全域 C++ 物件的建構子
4. **才輪到** `main()`

這對 shellcode 和 DLL injection 有意義：注入的 DLL 的 `DllMain` 跑的時間點是在目標的 import DLL 全部 attach 之後、目標的 `main` 之前（新 LoadLibrary）或中間（運行中注入）。

## 底層機制：EPROCESS 與 ETHREAD 的關聯

```
  核心物件關係圖（簡化）

  EPROCESS（行程核心物件）
  ├── Token（安全令牌，Ch 44 主題）
  ├── VadRoot（虛擬位址空間，Ch 9 主題）
  ├── HandleTable（Handle 表，Ch 8 主題）
  ├── Peb（指向 PEB，在使用者態位址空間）
  └── ThreadListHead ──► ETHREAD 1
                    └──► ETHREAD 2
                    └──► ...

  ETHREAD（thread 核心物件）
  ├── Tcb（KTHREAD，排程資訊）
  ├── Cid（ClientId：PID + TID）
  ├── Teb（指向 TEB，在使用者態位址空間）
  ├── Win32StartAddress（StartAddress 傳入的函式指標）
  └── ApcState（APC 佇列，APC injection 從這裡下手）
```

`ETHREAD.Win32StartAddress` 記錄了這個 thread 的起始函式位址（`CreateThread` 的 `lpStartAddress`）。這個欄位在 WinDbg 的 `!thread` 指令裡看得到，是識別「這個 thread 是誰建立的、跑哪個函式」的重要線索——也是 EDR 分析注入 thread 的方法之一（如果 `Win32StartAddress` 落在無 PE 支援的 RWX 頁，就是可疑跡象）。

> **未實測，理論預期**：WinDbg 的 `!thread` 輸出：
> ```
> 0:001> !thread
> THREAD ffff... Cid 1234.5678 ...
>     Win32StartAddress kernel32!BaseThreadInitThunk (...)
>     ...
> ```

## Process/Thread Injection 原語概觀（教育性視角）

以下列出主要原語的機制，目的是讓你理解**為什麼它們有效**以及**防禦者在哪個環節偵測**。

### 1. CreateRemoteThread DLL Injection

- **操作**：`OpenProcess` → `VirtualAllocEx`（RW）→ `WriteProcessMemory`（DLL 路徑）→ `CreateRemoteThread`（`LoadLibraryW`）
- **為什麼有效**：`LoadLibraryW` 在目標行程 context 執行，就像目標自己 import 了這個 DLL
- **偵測點**：跨行程的 `OpenProcess(PROCESS_VM_WRITE|PROCESS_CREATE_THREAD)`、`WriteProcessMemory`、`CreateRemoteThread` 序列；`Win32StartAddress` 落在 `LoadLibraryW` 也是明顯特徵

### 2. Thread Hijacking（APC 或暫停執行緒）

- **操作**：`OpenThread(THREAD_SUSPEND_RESUME|THREAD_GET_CONTEXT|THREAD_SET_CONTEXT)` → `SuspendThread` → `GetThreadContext` → 改 `Rip`（或插 ROP）→ `SetThreadContext` → `ResumeThread`
- **為什麼有效**：不建立新 thread，劫持已存在的 thread 去執行 shellcode；`Win32StartAddress` 看起來是原始的合法位址
- **偵測點**：`SetThreadContext` 把 `Rip` 改到無 PE 支援的頁；`SuspendThread` + `SetThreadContext` 序列

### 3. Process Hollowing

- **操作**：`CreateProcess`（`CREATE_SUSPENDED`）→ `NtUnmapViewOfSection`（解除映像）→ `VirtualAllocEx`（在原基址） → `WriteProcessMemory`（惡意 PE）→ 修 PEB `ImageBaseAddress`、thread context `Rcx`（入口點）→ `ResumeThread`
- **為什麼有效**：行程外觀（PID、parent、token）來自合法的殼；執行的是惡意 PE
- **偵測點**：`NtUnmapViewOfSection` 解映像後緊接 `VirtualAllocEx` 在同一位址；`Rcx`（入口點）和合法 PE 不符

> **認識論警告**：injection 技法的細節因工具版本（Meterpreter、Cobalt Strike）和 Windows 版本而異；上面描述的是「教科書版本」，現實工具有大量變體。防禦偵測也在持續演進。本課的立場：理解底層原理，而非提供現成工具。

## 對比與取捨

| 項目 | Linux | Windows |
|---|---|---|
| 行程建立 | `fork` + `exec`（兩個 syscall，複製父） | `CreateProcess`（單一 API，多階段，不複製） |
| Thread 建立 | `clone(CLONE_VM\|...)` / `pthread_create` | `CreateThread` / `NtCreateThreadEx` |
| 遠端 thread | 無直接 API（需 ptrace） | `CreateRemoteThread` |
| Thread 起始包裝 | `pthread_create` 的 wrapper | `RtlUserThreadStart` |
| Thread 局部儲存 | `FS` 段（x86-64）/ `pthread_key` | `GS` 段（x64）/ TLS slots in TEB |
| 行程通知子系統 | 無 CSRSS 概念 | CSRSS（ALPC port） |
| CRT 初始化 | `__libc_start_main` | `mainCRTStartup` / `__tmainCRTStartup` |
| 子行程映像 | `exec` 替換 | PE loader 在 `LdrInitializeThunk` 裡 |

## 踩雷集錦

1. **「CreateProcess 成功就代表子行程的 main() 在跑了」**：不是。`CreateProcess` 回傳時，子行程的初始 thread 可能仍在 `LdrInitializeThunk` 裡跑 DLL 初始化。你要等 `WaitForInputIdle`（GUI 行程）或 `WaitForSingleObject`（等 thread 或行程結束）才知道進度。如果傳了 `CREATE_SUSPENDED`，子行程完全沒動，要 `ResumeThread` 才開始。

2. **「CreateRemoteThread 的 StartAddress 直接傳 shellcode 指標就好」**：這個指標是**目標行程**虛擬位址空間的地址，不是你這個行程的。你必須先 `VirtualAllocEx` 在目標行程配空間、`WriteProcessMemory` 寫進去，再把那個目標行程內的地址傳給 `CreateRemoteThread`。傳本行程的指標必然 crash 或行為不定。

3. **「RtlCreateUserThread 不用 CSRSS，更難被偵測」**：2010 年代或許部分有效，現在 EDR 掛的是 `NtCreateThreadEx`（更底層），`RtlCreateUserThread` 通不通知 CSRSS 對現代 EDR 影響有限。但它還是常用於特定場景（Meterpreter classic），知道原因很重要。

4. **「TEB 就在 stack 旁邊」**：不是。TEB 是獨立分配的 MEM_PRIVATE 頁（`GS:[0x30]` 指向它），不在 stack 的連續空間裡。混淆這點會讓你在計算 stack 溢出能影響到什麼區域時算錯。

5. **「子行程的 PID 就是 PROCESS_INFORMATION.hProcess」**：不是。`hProcess` 是 HANDLE（本行程 handle table 的整數索引），`dwProcessId` 才是 PID。Ch 8 講過，但這個混淆非常常見。

## 進階：再往深一層

### NtCreateUserProcess vs NtCreateProcess

Windows Vista 引入了 `NtCreateUserProcess`，把行程建立的多個步驟（建 section、建 EPROCESS、設 PEB）合進一個 syscall。Vista 之前用 `NtCreateProcess`（只建 EPROCESS）+ `NtCreateThread`（建 thread）的兩步。理解這個歷史對讀老的 injection 代碼（用兩步 API）很有幫助。

### Process Mitigation Policies

`SetProcessMitigationPolicy` 可以在行程建立後（或建立前透過 `CreateProcess` 的 `PROC_THREAD_ATTRIBUTE_MITIGATION_POLICY`）設定一批安全策略：

```c
PROCESS_MITIGATION_DYNAMIC_CODE_POLICY policy = {0};
policy.ProhibitDynamicCode = 1;   // 禁止在行程建立後 VirtualAlloc RWX 頁（ACG）
SetProcessMitigationPolicy(ProcessDynamicCodePolicy, &policy, sizeof(policy));
```

`ACG`（Arbitrary Code Guard）開啟後，`VirtualProtect` 把頁改成 RWX 會失敗——這是讓 ROP-to-VirtualProtect 失效的緩解，Ch 36 細講。

### 用 CreateProcess 設定 Attribute List（Parent Spoofing）

```c
// 讓子行程的「父行程」看起來是 explorer.exe 而不是真正的父
STARTUPINFOEX si = {};
si.StartupInfo.cb = sizeof(si);
InitializeProcThreadAttributeList(si.lpAttributeList, 1, 0, &size);
UpdateProcThreadAttribute(si.lpAttributeList, 0,
    PROC_THREAD_ATTRIBUTE_PARENT_PROCESS, &hExplorer, sizeof(hExplorer), NULL, NULL);
CreateProcess(NULL, cmdline, NULL, NULL, FALSE,
    EXTENDED_STARTUPINFO_PRESENT, NULL, NULL, &si.StartupInfo, &pi);
```

這是 "Parent PID Spoofing"——讓子行程的 `EPROCESS.InheritedFromUniqueProcessId` 是 explorer 的 PID，讓 EDR 的行程樹分析混淆。現代 EDR 已能偵測（比對 token / handle 繼承鏈），但原理值得理解。

## 動手練習

用 Python ctypes 寫一個程式，建立一個子行程（`cmd /c whoami`），用 `ReadFile` 讀取子行程的 stdout（需要 `CreatePipe` + `STARTUPINFOW.hStdOutput = hWritePipe`），印出子行程的完整輸出。

提示：
1. `CreatePipe(&hReadPipe, &hWritePipe, &sa, 0)`（`sa.bInheritHandle = TRUE`）
2. `si.hStdOutput = hWritePipe`、`si.dwFlags = STARTF_USESTDHANDLES`
3. `CreateProcess(...)`，別忘了在父行程 close `hWritePipe`（否則 `ReadFile` 永遠不返回）
4. `ReadFile(hReadPipe, buf, ...)` 讀子行程輸出

## 本章重點整理

- `CreateProcess` 不是單一 syscall，是**七個階段**：打開映像→建 section→`NtCreateUserProcess`（建 EPROCESS+ETHREAD）→設 PEB 參數→通知 CSRSS→`ResumeThread`→子行程 `LdrInitializeThunk` 到 `main()`。
- 子行程的初始 thread 在階段 3 建立時是 **suspended** 的，這個窗口是 process hollowing / 行程注入的操作時機。
- **每個 thread 有一個 TEB**：x64 透過 `GS` 段基址存取，`GS:[0x30]` 是 TEB 自身地址，`GS:[0x60]` 是 PEB 指標。TEB 記錄 stack 邊界（`StackBase`/`StackLimit`）。
- `CreateRemoteThread` 在目標行程建 thread；`RtlCreateUserThread` 不通知 CSRSS；`NtCreateThreadEx` 最底層可控最多——三者各有在 injection 場景的使用理由，但現代 EDR 對所有路徑都有監視。

## 自我檢核

- [ ] 不看筆記，能依序說出 `CreateProcess` 的七個主要內部步驟
- [ ] 能解釋為什麼 `CREATE_SUSPENDED` 是 process hollowing 的前置條件
- [ ] 知道 TEB 在 x64 怎麼透過 `GS` 段存取、`GS:[0x30]` 和 `GS:[0x60]` 各存什麼
- [ ] 能說出 `CreateRemoteThread` 和 `RtlCreateUserThread` 的最關鍵差異（CSRSS 通知）以及這個差異對 EDR 偵測的影響（現代已影響有限）
- [ ] 知道 `PROCESS_INFORMATION.hProcess` 和 `dwProcessId` 的差異（Handle 索引 vs PID）
- [ ] 面試題：`DllMain(DLL_PROCESS_ATTACH)` 和主程式的全域 C++ 建構子，哪個先跑？（所有 DLL 的 DllMain 先，然後 CRT 初始化，然後全域建構子，最後 main）

## 延伸閱讀

### 官方文件

- **[CreateProcess function — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-createprocessw)**
  - **讀哪裡**：`dwCreationFlags`（`CREATE_SUSPENDED`、`DEBUG_PROCESS`）和 `STARTUPINFO` 的所有欄位；Remarks 段說明繼承行為
  - **和本章的關聯**：本章七個階段的 API 入口；process hollowing 和 parent spoofing 都在這裡找旗標

- **[Thread Attributes — PROC_THREAD_ATTRIBUTE_LIST](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute)**
  - **讀哪裡**：所有 `PROC_THREAD_ATTRIBUTE_*` 值，尤其 `PARENT_PROCESS`、`MITIGATION_POLICY`、`HANDLE_LIST`
  - **和本章的關聯**：Parent PID Spoofing 和 ACG 設定的 API 接口

### 書籍

- **《Windows Internals, 7th Edition》Part 1，Chapter 3：Processes, Threads, and Jobs**（Yosifovich, Ionescu 等，Microsoft Press）
  - **讀哪裡**：「Process Creation」節（NtCreateUserProcess 的七個階段分析）和「Thread Internals」節（TEB/ETHREAD 結構）
  - **和本章的關聯**：本章的七階段描述是這節的精煉版；EPROCESS/ETHREAD 的欄位細節在這裡有完整的 `dt` 輸出
  - **前提知識**：本章讀完後再進去，欄位名稱才有 context

### 部落格 / 研究

- **[Connor McGarr — Process Injection Series](https://connormcgarr.github.io/)**
  - **讀哪裡**：搜 "process injection"；他對 `CreateRemoteThread`、APC injection、thread hijacking 的底層分析有完整代碼與 WinDbg 驗證
  - **和本章的關聯**：本章的 injection 原語概觀；Part 3 exploit 開發的先修讀物
  - **前提知識**：本章讀完 + Ch 5（PEB/TEB）

- **[Alex Ionescu — Windows Internals Blog](https://ionescu007.github.io/)**
  - **讀哪裡**：關於 `NtCreateUserProcess`、`CSRSS`、子系統架構的文章；Ionescu 是 Windows Internals 書的共同作者，精確度最高
  - **和本章的關聯**：階段 3（`NtCreateUserProcess`）和階段 5（CSRSS）的深度來源

- **[Elastic Security — Process Injection Techniques](https://www.elastic.co/security-labs/ten-process-injection-techniques-technical-survey)**
  - **讀哪裡**：從防禦偵測角度系統整理了 10 種注入技法；每種技法都標出「偵測點」和「MITRE ATT&CK 對應」
  - **和本章的關聯**：本章「injection 原語概觀」的防禦視角補充；理解 EDR 如何偵測你才能設計更好的防禦（或理解為何繞過手法要那樣設計）
  - **前提知識**：本章讀完

理解了行程與 thread 的建立之後，下一章進入 Windows 例外處理架構的第一部分——x86 的 SEH chain，這是 Part 3 SEH overwrite 技法的理論基礎。

→ [Ch 11 — 例外處理架構 I：x86 SEH chain](./11-seh-x86.md)
