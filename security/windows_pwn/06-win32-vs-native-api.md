# Ch 6 — Win32 API vs Native API (ntdll)

> **目標**：搞清楚 Windows API 的層次分工——kernel32/user32 是什麼、ntdll 是什麼、兩者為何同時存在；能追蹤一個 Win32 呼叫（`VirtualAlloc`）從 kernel32 一路走到 `NtAllocateVirtualMemory` 的真實路徑，並理解 ntdll 為何是使用者態的最後邊界。這是搞懂 syscall hook、EDR 繞過、direct syscall 的必要前置。

## 為什麼需要這個？

你在 Linux 上早就習慣了一層 API：`malloc` 呼叫 `brk`/`mmap`，`write` 呼叫 `sys_write`。glibc 是薄薄一層 wrapper，syscall number 基本上是公開、穩定的，你甚至可以不透過 glibc 直接用 `syscall()` 指令叫進 kernel。整件事在腦袋裡是一張很平的圖。

Windows 不一樣。Windows 有**三層甚至四層**明確分層的 API：

```
應用程式 (App)
    │
    ▼
kernel32.dll / user32.dll / advapi32.dll   ← Win32 API（documented）
    │ 幾乎全轉給 ▼
kernelbase.dll                             ← 2009 年起的「真實實作」層
    │ 呼叫 ▼
ntdll.dll                                  ← Native API（半 documented）
    │ syscall 指令 ▼
  kernel (ntoskrnl.exe)                    ← 真實 kernel
```

這不是冗余，是**刻意的設計**：Win32 API 是「對應用程式承諾的穩定介面」，Native API 是「OS 自己用、不保證穩定的底層介面」。這個分層有歷史演進的原因，也直接決定了：

- EDR（端點偵測與回應）會把 hook 掛在哪一層
- direct syscall 攻擊要跳過哪一層
- 逆向 Windows 元件時，文件查不到的函式在哪找

## 先建立直覺：三層門衛模型

想像系統呼叫是進一棟管制大樓的流程：

```
 你（App）
    │
    │  「我要申請一個記憶體區塊」
    ▼
┌─────────────────────────────────────────────────────┐
│  接待台（kernel32!VirtualAlloc）                     │
│  ・純轉送，jmp 到 kernelbase                         │
│  ・Win32 名字的穩定性承諾在這裡兌現                  │
└───────────────────┬─────────────────────────────────┘
                    │  jmp → kernelbase!VirtualAlloc
                    ▼
┌─────────────────────────────────────────────────────┐
│  業務窗口（kernelbase!VirtualAlloc）                 │
│  ・參數翻譯：Win32 格式 → Native 格式                │
│  ・4 個參數 → 6 個參數                               │
│  ・填好 ProcessHandle、ZeroBits 等欄位               │
└───────────────────┬─────────────────────────────────┘
                    │  call ntdll!NtAllocateVirtualMemory
                    ▼
┌─────────────────────────────────────────────────────┐
│  安全門（ntdll!NtAllocateVirtualMemory）            │
│  ・mov r10, rcx   ; 保存第一個參數                   │
│  ・mov eax, 0x18  ; 載入 SSN（系統呼叫號）           │
│  ・syscall        ; ring 3 → ring 0                 │
└───────────────────┬─────────────────────────────────┘
                    │  CPU ring 3 → ring 0
                    ▼
         kernel（ntoskrnl.exe）
         nt!NtAllocateVirtualMemory → MmAllocateVirtualMemory
```

每一層都有它的職責。接待台（kernel32）維護「對外的穩定介面名字」，業務窗口（kernelbase）做實際的參數翻譯，安全門（ntdll）執行那一道不可跳過的特權轉換。

## Win32 API：documented 的穩定層

`kernel32.dll`、`user32.dll`、`advapi32.dll`、`gdi32.dll` 是 **Win32 API** 的代表。Microsoft 在文件裡完整說明它們的行為，每個 Windows 版本要保持向後相容。你寫 Windows 程式用的 `CreateFile`、`ReadFile`、`VirtualAlloc`、`CreateProcess`，全部住在這一層。

**2009 年（Windows 7）開始，kernel32 被拆了一半進 kernelbase.dll**。現代 Windows 的 kernel32 裡，大多數函式變成了簡單的 `jmp`——直接跳到 kernelbase 的對應實作。真實的邏輯在 kernelbase。

這是 MinWin 計畫（最小核心子集）的一部分。目標是讓 Windows Server Core / Nano Server 能載入最小的 DLL 集合，同時讓 ARM / ARM64 Windows 能共用同一份 kernelbase 實作，而 kernel32 只保留「Windows subsystem 的名字空間」。結果是你在 IDA 看 kernel32 裡的大多數函式，只有一個跳躍指令，不要以為那就是全部。

本機真實輸出（Python + capstone 反組譯）：

```
ntdll.dll      base: 0x7ffa859c0000
kernelbase.dll base: 0x7ffa830d0000
kernel32.dll   base: 0x7ffa849a0000

--- kernel32!VirtualAlloc @ 0x7ffa849d3d20 (first 16 bytes) ---
    Raw: 48 ff 25 21 66 05 00 cc cc cc cc cc cc cc cc cc
    0x7ffa849d3d20: jmp  qword ptr [rip + 0x56621]   ← 純轉送
    0x7ffa849d3d27: int3                              ← padding
    0x7ffa849d3d28: int3

--- kernelbase!VirtualAlloc @ 0x7ffa831850c0 (first 16 bytes) ---
    Raw: 48 83 ec 38 48 89 54 24 48 48 89 4c 24 40 48 85
    0x7ffa831850c0: sub  rsp, 0x38         ← 真正的函式 prologue
    0x7ffa831850c4: mov  qword ptr [rsp + 0x48], rdx
    0x7ffa831850c9: mov  qword ptr [rsp + 0x40], rcx
```

kernel32!VirtualAlloc 的第一個指令是 `jmp [rip+offset]`，也就是一個轉發跳躍（forwarder thunk）。`[rip+0x56621]` 是 kernel32 的 IAT slot，裡面存著 kernelbase!VirtualAlloc 的實際位址（本機是 0x7ffa831850c0）。kernelbase 才有真正的函式 prologue。

## Native API：undocumented 的系統內部層

`ntdll.dll` 是 **Native API** 的所在地。它的函式名稱以 `Nt` 或 `Zw` 開頭：`NtAllocateVirtualMemory`、`NtCreateFile`、`NtOpenProcess`……

Microsoft **沒有正式文件化** Native API 供應用程式使用（雖然驅動程式的 WDK 文件裡有部分 `Zw*` 的說明）。但這些符號都在 public symbols 裡可查，逆向社群已把結構和語意研究得相當清楚，《Windows Internals》裡也有大量討論。

Native API 和 Win32 API 的差異不只是文件化程度：

| 面向 | Win32 API（kernel32 等） | Native API（ntdll） |
|---|---|---|
| 文件化 | 完全 documented（MSDN） | 部分（WDK Zw*）；大多靠 public symbols |
| 穩定性保證 | 跨版本 ABI 穩定 | 不保證；函式簽名可能跨 build 改變 |
| 參數格式 | Win32 慣例（DWORD flags、HANDLE 等）| 較低層（OBJECT_ATTRIBUTES、IO_STATUS_BLOCK …）|
| 錯誤碼 | `GetLastError()` → Win32 Error Code | `NTSTATUS`（例如 `0xC0000005 = Access Violation`）|
| 誰在用 | App / 系統元件（公開介面） | Windows 內部元件、驅動、shell |
| EDR hook 典型位置 | 有時 hook kernel32 | **最常 hook ntdll**（最後的使用者態機會）|

### `Nt*` vs `Zw*`：一個常見誤解

ntdll 裡有兩組幾乎同名的函式：`NtAllocateVirtualMemory` 和 `ZwAllocateVirtualMemory`。從使用者態看，它們**完全等價**——本機實測兩個名字解析到同一個位址：

```
--- NtAllocateVirtualMemory  @ 0x7ffa85b20350 ---
--- ZwAllocateVirtualMemory  @ 0x7ffa85b20350 ---   ← 同一位址
```

那 `Zw*` 有什麼用？差異在**核心態呼叫端的語意**：

- 從**使用者態**呼叫 `Nt*` 或 `Zw*`：完全一樣，都走同一個 syscall stub
- 從**核心態**（驅動）呼叫：
  - `Nt*`（kernel 版）→ 直接叫 kernel 函式，**保留目前執行緒的 Previous Mode**。如果呼叫端是 user-mode 轉入的 kernel path，指標還是受 user-space 地址範圍檢查。
  - `Zw*`（kernel 版）→ 透過 wrapper 強制 **Previous Mode 設成 KernelMode**，讓參數指標被視為 kernel 位址，繞過使用者位址範圍的 ProbeForRead/Write 檢查。

對使用者態 exploiter 來說，`Nt*` = `Zw*` = 同一個 syscall stub，不必在意前綴。真正重要的是裡面那個 `mov eax, SSN`。

## ntdll 為何是使用者態的最後邊界

從架構圖看，ntdll 是**唯一的使用者態/核心態橋梁**。任何使用者態程式碼想跟 kernel 說話，必須通過 ntdll 的 syscall stub（或者自己實作 direct syscall，但那是 Ch 7 的主題）。這個性質讓 ntdll 成為**安全監控的理想 hook 點**。

EDR 產品把 user-space hook 掛在 ntdll 的函式開頭，在真正的 `syscall` 指令執行前插入自己的分析邏輯：

```
App 呼叫 NtAllocateVirtualMemory
    │
    ▼
ntdll!NtAllocateVirtualMemory 開頭
    │
    │  正常: mov r10, rcx; mov eax, 0x18 ...
    │
    │  EDR hook: 把前 5 bytes 改成 jmp <edr_trampoline>
    ▼
  edr_trampoline:
    ・記錄呼叫參數
    ・檢查記憶體區塊的保護屬性是否可疑
    ・決定放行 or 終止
    ・放行 → 跳回原 stub 繼續 syscall
```

具體手法：EDR 把 ntdll 函式開頭的 5 bytes 改寫成 `jmp <hook_trampoline>`（x86-64 的 near jump：`0xE9` + 4 bytes relative offset），hook trampoline 分析後把控制交回原本的 syscall stub。攻擊研究裡的「unhook ntdll」，就是把被 EDR 改過的 bytes 還原；「direct syscall」，就是完全跳過 ntdll，自己執行 `syscall` 指令。Ch 7 詳細討論這兩種技術及其偵測方法。

> **教育性說明**：本課從防禦設計的角度解說 hook 機制，目的是讓讀者理解 EDR 怎麼工作、監控設計的取捨、以及攻擊者為什麼試圖繞過這層。理解攻防的對稱性是資安研究的基礎。

## 底層機制：一次完整的 VirtualAlloc 呼叫鏈

用真實資料走一遍 `VirtualAlloc(NULL, 0x1000, MEM_COMMIT|MEM_RESERVE, PAGE_READWRITE)` 的整條路徑。

### 步驟 1：kernel32!VirtualAlloc（接待台，純轉送）

本機反組譯顯示 kernel32!VirtualAlloc 的第一個指令是：

```
0x7ffa849d3d20: jmp  qword ptr [rip + 0x56621]
```

這個 `[rip + 0x56621]` 指向 kernel32 的 IAT slot，裡面存著 kernelbase!VirtualAlloc 的位址（0x7ffa831850c0）。呼叫進來，一跳就到 kernelbase，kernel32 這層沒做任何參數處理。

### 步驟 2：kernelbase!VirtualAlloc（翻譯層）

kernelbase 做真正的工作：

```c
// Win32 介面（4 參數）：
LPVOID VirtualAlloc(
    LPVOID lpAddress,        // NULL → 任意位置
    SIZE_T dwSize,           // 0x1000
    DWORD  flAllocationType, // MEM_COMMIT | MEM_RESERVE = 0x3000
    DWORD  flProtect         // PAGE_READWRITE = 0x04
);

// kernelbase 呼叫 Native API（6 參數）：
NTSTATUS NtAllocateVirtualMemory(
    HANDLE  ProcessHandle,   // (HANDLE)-1 = current process
    PVOID  *BaseAddress,     // &lpAddress（in/out）
    ULONG_PTR ZeroBits,      // 0
    PSIZE_T RegionSize,      // &dwSize（in/out）
    ULONG   AllocationType,  // MEM_COMMIT | MEM_RESERVE
    ULONG   Protect          // PAGE_READWRITE
);
```

參數格式的差異是 kernelbase 存在的理由：把 Win32 的 4 個參數翻成 Native 的 6 個，補上 `ProcessHandle = (HANDLE)-1`（current process pseudo-handle）和 `ZeroBits = 0`（不限制高位元），並把 in/out 參數改成指標形式。

### 步驟 3：ntdll!NtAllocateVirtualMemory（syscall stub）

本機真實反組譯（Python + capstone 實測）：

```
ntdll.dll base: 0x7ffa859c0000
NtAllocateVirtualMemory: 0x7ffa85b20350

Raw: 4c 8b d1 b8 18 00 00 00 f6 04 25 08 03 fe 7f 01 75 03 0f 05 c3 cd 2e c3

  0x7ffa85b20350: mov  r10, rcx            ; ① 保存第一個參數
  0x7ffa85b20353: mov  eax, 0x18           ; ② SSN = 0x18 = 24
  0x7ffa85b20358: test byte ptr [0x7ffe0308], 1   ; ③ 查 KUSER_SHARED_DATA
  0x7ffa85b20360: jne  0x7ffa85b20365      ; ④ bit 0 = 1 → int 2e 路徑
  0x7ffa85b20362: syscall                  ; ⑤ 正常路徑
  0x7ffa85b20364: ret
  0x7ffa85b20365: int  0x2e               ; ⑥ 舊式路徑（相容性）
  0x7ffa85b20367: ret
```

每個指令的意義逐一說明：

**① `mov r10, rcx`**：x64 syscall 呼叫慣例裡，`syscall` 指令執行時 CPU 把返回 RIP 寫進 `rcx`（覆蓋它）。所以在執行 `syscall` 之前必須把原本在 `rcx` 的第一個參數搬到 `r10`，kernel 讀第一個參數從 `r10` 取。

**② `mov eax, 0x18`**：這是 **SSN（System Service Number）= 24**。kernel 進入後根據 `eax` 走 SSDT 找到真正的 kernel 函式指標。「0x18 是 NtAllocateVirtualMemory」只在這個特定 Windows build 成立——Ch 7 會說明 SSN 為何隨 build 漂移。

**③ `test byte ptr [0x7ffe0308], 1`**：`0x7ffe0000` 是 `KUSER_SHARED_DATA`（KSD）的固定基址，kernel 和 user space 共用這塊 read-only 記憶體。KSD+0x308 這個 byte 的 bit 0 決定走哪條 syscall 路徑。本機實測：

```
[KSD+0x308] = 00 00 00 00
Bit 0 = 0 → syscall path (normal)
```

**⑤ `syscall`**：x86-64 的快速系統呼叫指令。CPU 從 MSR `LSTAR`（`IA32_LSTAR`）讀 kernel entry point 位址，跳進去，ring 3 → ring 0，進入 `ntoskrnl!KiSystemCall64`。

**⑥ `int 0x2e`**：舊式路徑，Windows NT 時代的系統呼叫中斷（`0x2e` = 46）。某些 hypervisor 環境或虛擬化層不支援 `syscall` 指令時才走這條。現代 Win11 正常情況下 bit 0 = 0，不走這條。

### 步驟 4：kernel

`syscall` 後 CPU 跳進 `ntoskrnl!KiSystemCall64`，kernel 從 `SSDT[eax]` 找到 `nt!NtAllocateVirtualMemory`，實際執行記憶體分配（`MmAllocateVirtualMemory`），完成後 `sysret` 回到使用者態，rax 存放 `NTSTATUS`（`0x00000000` = 成功）。

整條呼叫鏈圖示：

```
VirtualAlloc(NULL, 0x1000, 0x3000, 0x04)
        │
        ▼ [kernel32.dll]
jmp → kernelbase!VirtualAlloc
        │ 參數翻譯：4 → 6 個，補 ProcessHandle=-1, ZeroBits=0
        ▼ [kernelbase.dll]
call → ntdll!NtAllocateVirtualMemory
        │ mov r10,rcx; mov eax,0x18; syscall
        ▼ [ring 3 → ring 0]
ntoskrnl!KiSystemCall64
        │ SSDT[0x18]
        ▼
nt!NtAllocateVirtualMemory → MmAllocateVirtualMemory
        │ sysret, rax = NTSTATUS
        ▼ [ring 0 → ring 3]
kernelbase 轉換 NTSTATUS → SetLastError
        │
App 收到分配好的指標（或 NULL + GetLastError）
```

## 錯誤碼的兩個世界：NTSTATUS vs Win32 Error

這是 Windows API 層次最讓初學者困惑的地方之一——同一個操作失敗，你可能看到兩種不同格式的錯誤碼。

**NTSTATUS**（Native API 的錯誤碼，32 bits）：

```
高 2 bits：嚴重程度
  00 = Success
  01 = Informational
  10 = Warning
  11 = Error（以 0xC 開頭的，如 0xC0000005）

常見的 NTSTATUS：
  0x00000000  STATUS_SUCCESS
  0xC0000005  STATUS_ACCESS_VIOLATION   ← 非法記憶體存取
  0xC0000034  STATUS_OBJECT_NAME_NOT_FOUND
  0xC000000D  STATUS_INVALID_PARAMETER
  0xC0000022  STATUS_ACCESS_DENIED
  0xC00000BB  STATUS_NOT_SUPPORTED
```

**Win32 Error Code**（GetLastError() 回傳，32 bits 但只有低 16 bits 有意義）：

```
常見的 Win32 Error：
  0   ERROR_SUCCESS
  2   ERROR_FILE_NOT_FOUND
  5   ERROR_ACCESS_DENIED
  87  ERROR_INVALID_PARAMETER
  183 ERROR_ALREADY_EXISTS
```

轉換發生在 kernelbase 裡：`RtlNtStatusToDosError(NTSTATUS)` 把 NTSTATUS 對映到 Win32 error，並寫進 TEB（`GS:[TEB.LastErrorValue]`）。`GetLastError()` 只是讀 TEB。

**為何 exploiter 要懂這個**：逆向 Win32 API 的 failure path 時，你看到的是 NTSTATUS，但 API 文件說的是 Win32 Error Code。要知道哪個 Win32 error 對應哪個 NTSTATUS，可以用 `NtCurrentTeb()->LastStatusValue`（Win32 call 完後 TEB 裡還存著最後的 NTSTATUS），或直接讀函式的 `NTSTATUS` 回傳值（Native API 的回傳值在 rax，kernelbase 轉換後才寫 TEB）。

## 對比與取捨

### Windows vs Linux：API 層次對照

| 面向 | Windows | Linux（glibc） |
|---|---|---|
| 應用層 | kernel32（Win32 名字） | libc（POSIX 名字） |
| 翻譯/wrapper 層 | kernelbase（真實邏輯） | libc（直接包 syscall，薄層）|
| syscall 前最後層 | ntdll Nt* stub | libc 的 `syscall()` wrapper 或彙編 |
| syscall 指令 | `syscall`（x64）、`int 2e`（舊）| `syscall`（x64）、`int 0x80`（x86）|
| syscall number 穩定性 | **不穩定**，每個 build 可能漂移 | **穩定**，ABI 凍結 |
| 文件化程度 | Win32 documented；Native undocumented | POSIX / man page 完整 |
| EDR hook 典型位置 | ntdll Nt* stub（userland inline hook）| ptrace / seccomp / LD_PRELOAD |

Linux 的 syscall number 是 ABI 的一部分，寫進 kernel 版本保證，不會因 kernel 版本更新而變。Windows 的 SSN 沒有這個保證——它由 ntdll 在每個 Windows build 重新配置。這是 Ch 7 的核心主題。

### Win32 vs Native API 使用時機

| 情境 | 用哪個 |
|---|---|
| 寫一般應用程式 | Win32（kernel32）|
| 逆向分析 Windows 元件 | 需要認識 Native API |
| Shellcode 直接取 API | 從 PEB 走 LDR 找 kernel32/ntdll（Ch 5/25）|
| EDR unhook 研究 | 操作 ntdll 函式開頭的 bytes |
| Direct syscall / indirect syscall | 繞過 ntdll hook，自己執行 `syscall` 指令 |
| 讀系統物件屬性 | `NtQueryObject`、`NtQuerySystemInformation` |
| 跨行程注入/讀寫 | `NtWriteVirtualMemory`、`NtCreateThreadEx` |

## 踩雷集錦

1. **「kernel32.dll 是最底層」**：錯。kernel32 是**最表層**。實作在 kernelbase，syscall stub 在 ntdll，執行在 kernel。你在 IDA 看 kernel32 裡的大多數函式只有一個 jmp，那不是全部，是轉送點。

2. **「Nt* 和 Zw* 是不同的兩個函式」**：在使用者態，它們**解析到同一個位址**。差異只在核心態呼叫端的 Previous Mode 語意。exploiter 在用戶態不需要區分，但逆向驅動時要懂。

3. **「直接呼叫 ntdll 的 Nt* 比呼叫 kernel32 快很多」**：幾乎沒有。跳過 kernelbase 的翻譯層省不了幾個指令。真正的效能考量在 syscall 次數，不在這幾層 function call 的 overhead。

4. **「EDR hook 只能掛在 ntdll」**：EDR 可以 hook 任何層。部分 EDR 也 hook kernel32/kernelbase，或使用 kernel-side callback（ETW-TI、PsSetCreateProcessNotifyRoutine）。ntdll hook 是「user-space inline hook」這個手法最常見的落點，不是唯一。

5. **「KUSER_SHARED_DATA 的 0x7ffe0000 可以讀寫」**：可以**讀**（kernel 把它 map 成 read-only 給所有 user-space 用），但**不能寫**（寫入會 access violation）。它是 kernel 共用給 user-space 的唯讀快取（`TickCount`、系統時間……），目的是讓讀時間不必 syscall。

## 觀察 API Set 重定向（進階）

除了 kernel32 → kernelbase 的轉送，Windows 還有另一層：**API Set（APISetSchemaMap）**。

你有時會看到 `ext-ms-win-kernel32-process-l1-1-0.dll` 這樣的虛擬 DLL 名字出現在 PE 的 IAT 裡。這些 DLL 並不存在於磁碟，它們是 `apisetschema.dll` 定義的**映射名字**，loader 在載入時把它們重定向到真正的 DLL（通常是 kernelbase 或 ntdll）。

```
IAT 裡有：ext-ms-win-kernel32-process-l1-1-0.dll!OpenProcess
                                │
                    apisetschema.dll 的映射表
                                │ 重定向
                                ▼
                  kernelbase.dll!OpenProcess
```

目的：讓 Windows 的不同 SKU（Desktop / Server Core / Nano Server / OneCore）能夠描述自己支援哪個 API 子集，而不是依賴 DLL 是否存在。`ext-ms-*` 的 DLL 名稱是能力宣告，不是真實 DLL。

從 exploit 角度，這意味著 IAT 裡的 DLL 名字不一定等於磁碟上的 DLL——你在分析 PE 匯入時要把 API Set 重定向考慮進去。

## 進階：再往深一層

**`NtQuerySystemInformation`（Class 0x35 = SystemModuleInformation）**：可以從使用者態枚舉 kernel 載入的模組、基址、路徑，不需要特殊權限（部分 class 需要 SeDebugPrivilege）。是 exploit 裡常用的「找 kernel base」原語。

**面試題**：「`GetLastError()` 是怎麼實作的？」——kernel 回傳 NTSTATUS，kernelbase 呼叫 `RtlNtStatusToDosError` 轉成 Win32 error code，寫進 TEB 的 `LastErrorValue`（在 x64 TEB 裡的偏移是 `0x68`，可透過 `GS:[0x30]` 拿 TEB 基址後加偏移）。`GetLastError()` 只是讀 TEB 的一個欄位。這串把 Ch 5（TEB）、Ch 6（API 層次）、Ch 7（syscall 回傳）都串起來。

**`WoW64`**：32 位元行程在 64 位元 Windows 上跑時，有一個額外的 WoW64 轉換層：`wow64.dll` + `wow64win.dll` + `wow64cpu.dll`，把 32 位元的呼叫轉換成 64 位元的 syscall。其中最有趣的是 Heaven's Gate——x86 程式碼透過 far jump 切換到 x64 執行環境——Ch 7 會介紹。

**kernelbase 拆分的完整歷史**：MinWin（Windows 7 引入）把 kernel32 的依賴項梳理乾淨，讓 Server Core 跑最小核心。研究 `apisetschema.dll` 的二進位格式（`API_SET_NAMESPACE` 結構）你可以解碼出完整的虛擬 DLL → 真實 DLL 映射表，這也是部分 malware 用來躲避靜態 IAT 分析的手法之一（IAT 只放 `ext-ms-*` 虛擬名，掃描工具看不到真正的 kernelbase 依賴）。

## 動手練習

### 任務 1：追蹤呼叫鏈

用 Python + ctypes + capstone 完整驗證 `VirtualAlloc` 呼叫鏈：
1. 取得 kernel32.dll、kernelbase.dll、ntdll.dll 的基址和 `VirtualAlloc`/`NtAllocateVirtualMemory` 的 VA
2. 反組譯 kernel32!VirtualAlloc（應該看到 `jmp [rip+x]`）
3. 取出那個 jmp 的目標位址，確認它指向 kernelbase（位址在 kernelbase 的範圍內）
4. 反組譯 kernelbase!VirtualAlloc 的前 20 條指令，找到它呼叫 `NtAllocateVirtualMemory` 的那個 `call` 指令
5. 反組譯 ntdll!NtAllocateVirtualMemory，確認 SSN 是 `0x18`（本機 build 26200）

### 任務 2：找特例

枚舉本機 ntdll 所有以 `Nt` 開頭的導出函式，找出哪些**不是標準 syscall stub**（開頭不是 `4c 8b d1 b8`），印出它們的反組譯。

提示：`NtGetTickCount` 是一個好的起點——它直接從 `KUSER_SHARED_DATA` 讀時間，根本不走 syscall。預期看到的開頭：

```
; 預期（理論，未實測）
NtGetTickCount:
    mov  eax, dword ptr [0x7ffe0320]   ; KUSER_SHARED_DATA.TickCount
    ret
```

找到幾個這樣的「不走 syscall 的 Nt* 函式」，理解 Microsoft 為何這樣設計（頻繁呼叫的函式，syscall overhead 太高）。

## 本章重點整理

- Windows API 有四層：kernel32（穩定名字）→ kernelbase（真實實作）→ ntdll（syscall stub）→ kernel。現代 kernel32 的大多數函式只是一個 `jmp` 轉到 kernelbase。
- `Nt*` 和 `Zw*` 在使用者態指向**同一個位址**；差異只在核心態呼叫的 Previous Mode 語意，exploiter 在 user-space 不需要區分。
- ntdll 是使用者態的最後邊界——EDR 最常在這裡做 inline hook；direct syscall 的動機正是要跳過這層。
- 每個 syscall stub 的核心是 `mov eax, SSN`——SSN 沒有跨 Windows build 的穩定性保證，這是 Ch 7 的核心問題。
- Linux 的 API 分層是「glibc（薄）→ syscall」兩層；Windows 是「名字層→實作層→stub 層→kernel」四層，多出的兩層各有歷史原因（Win32 兼容性承諾、MinWin 模組化）。

## 自我檢核

- [ ] 不看筆記，能畫出 `VirtualAlloc(NULL, 0x1000, MEM_COMMIT, PAGE_READWRITE)` 從 kernel32 到 kernel 的完整四層呼叫鏈，每層各自做了什麼
- [ ] 能解釋為什麼 kernelbase 在 2009 年（Windows 7）被拆出來，而不是讓 kernel32 繼續做這件事
- [ ] 能解釋 `Nt*` vs `Zw*` 的差異，以及為什麼使用者態 exploiter 不用在意
- [ ] 面試被問「EDR 的 userland hook 為什麼掛在 ntdll 而不是 kernel32」，能給出完整理由
- [ ] 知道 `KUSER_SHARED_DATA` 在 `0x7ffe0000`，那個 byte 的 bit 0 決定 `syscall` 還是 `int 2e`，且它是唯讀的
- [ ] 能解釋 `GetLastError()` 的完整實作鏈（kernel → NTSTATUS → TEB.LastErrorValue）

## 延伸閱讀

### 官方文件

- **[Windows Data Types — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/winprog/windows-data-types)**
  - **讀哪裡**：`NTSTATUS`（`0x00000000` = success, `0xC0000xxx` = error）；搭配 WDK 的 `ntstatus.h`
  - **和本章的關聯**：Native API 的錯誤碼體系，逆向 ntdll 呼叫時必備

- **[Kernel-Mode Driver Architecture — Zw* Reference（WDK）](https://learn.microsoft.com/en-us/windows-hardware/drivers/kernel/)**
  - **讀哪裡**：`ZwAllocateVirtualMemory`、`ZwCreateFile` 的正式 kernel-mode 文件
  - **和本章的關聯**：`Zw*` 的 Previous Mode 語意的官方說明

### 書籍

- **《Windows Internals, 7th Edition》Part 1 Ch 3 — System Mechanisms**
  - **讀哪裡**：「System Service Dispatching」這節；SSDT layout 的圖示
  - **和本章的關聯**：`KiSystemCall64` / SSDT 的 kernel 端詳細運作，接 Ch 7
  - **前提**：本章讀完即可

### 研究者部落格

- **[j00ru — Windows syscall tables（vexillium.org）](https://j00ru.vexillium.org/syscalls/nt/64/)**
  - **讀哪裡**：直接看 SSN 跨 Windows 版本的漂移表，找 `NtAllocateVirtualMemory` 在各 build 的號碼
  - **和本章的關聯**：本章說「SSN 不穩定」，這裡是你親眼看到數字怎麼跳的地方；Ch 7 用到

- **[Geoff Chappell — Windows Internals（geoffchappell.com）](https://www.geoffchappell.com/studies/windows/km/ntoskrnl/api/index.htm)**
  - **讀哪裡**：Native API 的結構與語意分析；`NtQuerySystemInformation` 的 Class 列表
  - **和本章的關聯**：Native API 最完整的非官方文件，準確度高

- **[Alex Ionescu — SIMExec / Windows Internals 演講（Black Hat 2013-2018）](https://github.com/ionescu007/SimExec)**
  - **讀哪裡**：任何 Alex Ionescu 講 Windows 系統呼叫機制的演講
  - **和本章的關聯**：`KiSystemCall64`、KUSER_SHARED_DATA、syscall dispatch 最深入的公開分析之一
  - **前提**：有核心態基礎更好，但 user-space 視角讀也有收穫

→ [Ch 7 — syscall 機制與版本漂移](./07-syscall-mechanism.md)
