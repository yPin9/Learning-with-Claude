# Ch 23 — DEP + ROP on Windows

> **目標**：徹底理解 DEP（Data Execution Prevention，即 NX）在 Windows 上的實作方式與對照 Linux 的差異、為什麼 DEP 逼出 ROP、Windows 特有的 ROP 目標（`VirtualProtect` / `VirtualAlloc` / `WriteProcessMemory` 把 shellcode 頁改成 RWX 再跳）、x64 calling convention 對 ROP chain 組裝的影響（shadow space / register 傳參 / 16-byte 對齊），以及 mona.py `!mona rop` 的完整工作流。學完能設計 x86 和 x64 各一條 VirtualProtect ROP chain，並說明每個 gadget 在做什麼。

---

## 為什麼需要 DEP？它擋住了什麼？

回到 Ch 19 的最原始世界：buffer overflow 把 shellcode 寫進 stack，把 saved EIP 蓋成 shellcode 的位址，跳過去執行。這條路有一個根本前提：**stack 上的資料可以被 CPU 當作指令執行**。

DEP 的核心宣言就是打破這個前提：**資料頁不可執行，程式碼頁不可寫**。用 Linux 術語講就是 W^X（Write XOR Execute）。

在你熟悉的 Linux pwn 裡，ELF binary 的 `GNU_STACK` section 或 `mprotect` 決定 stack 能不能執行；glibc 的堆 chunk 預設是 RW 不可執行。DEP 的概念是一樣的——但 Windows 的實作細節和 Linux 不同。

---

## DEP 的 Windows 實作

### 硬體層（NX/XD bit）

DEP 的硬體基礎是 CPU 的 **NX bit**（AMD 叫 No-Execute，Intel 叫 XD，Execute Disable）。在 x86-64 的頁表 PTE（Page Table Entry）的 bit 63 就是 NX bit：

```
PTE（Page Table Entry，64 位元格式）：

bit 63   : NX (No-Execute)  ← 1 = 這頁資料不能當指令執行
bit 62-52: 各種旗標（HLAT、MPK 等）
bit 51-12: 實體頁框號（PFN）
bit 11-0 : Accessed / Dirty / US / RW / P 等標準旗標
```

CPU 在 fetch 指令時，如果目標頁的 NX bit = 1，觸發 `#PF`（page fault，status code bit 4 = I/D bit = 1），Windows kernel 把這個 fault 轉成 `EXCEPTION_ACCESS_VIOLATION`（`STATUS_ACCESS_VIOLATION`，code `0xC0000005`）。

### Windows 的 DEP 層次

| 層次 | 設定方式 | 說明 |
|---|---|---|
| **硬體 DEP** | BIOS / CPU 功能 | CPU 支援 NX/XD，Windows kernel 在 PAE 模式下啟用 |
| **系統 DEP** | BCDEdit 的 `nx` 選項 | `nx AlwaysOn`：全系統強制；`nx OptIn`：只對 opt-in 程式開（Vista+ 預設）；`nx OptOut`：除 opt-out 外全開；`nx AlwaysOff`：全關 |
| **Process DEP** | PE 的 `NX_COMPAT` 旗標 + SetProcessDEPPolicy API | Per-process 開關；`NX_COMPAT`（`DllCharacteristics bit 0x0100`）告訴 loader 這個 process 支援 DEP |
| **頁面 DEP** | `VirtualAlloc` / `VirtualProtect` 的保護旗標 | `PAGE_EXECUTE_READ`、`PAGE_EXECUTE_READWRITE`、`PAGE_READWRITE` 等 |

mingw 預設就帶 `NX_COMPAT`（Ch 0 的 `0x0160` 就包含它）。MSVC 同樣預設開。關閉系統 DEP（僅學習用）：

```bat
REM 未實測，理論預期（需系統管理員）
bcdedit /set nx AlwaysOff
REM 重新開機後生效；學習結束後改回 OptIn
bcdedit /set nx OptIn
```

### 對照 Linux

| 面向 | Linux | Windows |
|---|---|---|
| **NX 機制名稱** | NX bit（等同）| NX/XD bit（等同） |
| **ELF/PE 中的標記** | `GNU_STACK` segment flag（`RWE` 的 E bit） | `NX_COMPAT`（DllCharacteristics bit 0x0100） |
| **允許堆上執行的 API** | `mprotect(ptr, size, PROT_EXEC)` | `VirtualProtect(ptr, size, PAGE_EXECUTE_READWRITE, &old)` |
| **per-page 控制** | 每個頁可用 `mprotect` 修改 | 每個頁可用 `VirtualProtect` 修改 |
| **關掉 NX 的「緊急逃生口」** | `mprotect` 自己就能改（前提：有可執行頁的 ROP gadget） | `VirtualProtect` 是 ROP chain 的終極目標 |
| **堆預設保護** | `mmap` 回傳的頁預設 `RW`，不可執行 | `HeapAlloc` 回傳的頁預設 `RW`，不可執行 |

**最重要的差異**：Linux 的 `mprotect` 可以直接把 stack 改成可執行，讓 shellcode 跑起來（如果 stack 本身是 MAP_ANONYMOUS 的）。Windows 的 `VirtualProtect` 有同樣的能力，但在 NX_COMPAT 開啟的 process 裡，攻擊者必須用 ROP 來呼叫它——沒有可執行的 stack/heap 頁，沒辦法直接跳進 shellcode 跑 `VirtualProtect`。這是 DEP 的核心效果。

---

## 先建立直覺：DEP 逼出了 ROP

DEP 開啟之後，攻擊者面對的問題是：

```
我控制了 EIP（蓋了 saved EIP 或 SEH handler），
但我的 shellcode 放在 stack 上（RW 不可執行），
跳過去 CPU 直接 page fault。

現在怎麼辦？
```

答案：**不跳到資料，跳到既有的程式碼**。程式碼頁（`.text`、`.rdata` 裡的程式碼）是可執行的，系統 DLL（`ntdll.dll`、`kernel32.dll`）的頁也是可執行的。這些頁裡面存在大量的程式碼片段，每個片段的結尾有 `ret` 指令。

攻擊者可以把 stack 佈置成一串**返回位址的序列**，每個位址指向一個有用的程式碼片段（**gadget**），gadget 的最後一個指令是 `ret`，`ret` 彈出下一個返回位址，繼續執行下一個 gadget。

這就是 **ROP（Return-Oriented Programming）**，你在 Linux pwn 裡已經熟悉了。Windows 上的核心概念完全一樣，差異在**目標 API**和 **calling convention**。

```
DEP 開啟後的 ROP stack（x86 示意）：

  esp → [gadget_1_addr]  ← ret 彈這個，跳去 gadget 1
         [gadget_2_addr]  ← gadget 1 結尾 ret 彈這個
         [gadget_3_addr]  ← ...
         ...
         [VirtualProtect_addr]  ← 最後呼叫 VirtualProtect
         [return_addr_after]    ← VirtualProtect 的 ret 位址
         [arg1: lpAddress]
         [arg2: dwSize]
         [arg3: PAGE_EXECUTE_READWRITE]
         [arg4: &old_protect]
         [shellcode_addr]  ← VirtualProtect 返回後跳這裡
```

---

## Windows 特有的 ROP 目標

Linux pwn 用 `mprotect` 把 shellcode 頁改成可執行。Windows 的等價 API 有三個，各有用武之地：

### VirtualProtect：把既有頁改成可執行

這是 Windows DEP bypass 最常用的目標。把 shellcode 所在的頁（stack 或 heap 上）改成 `PAGE_EXECUTE_READWRITE`（0x40），然後跳過去執行。

```
BOOL VirtualProtect(
    LPVOID lpAddress,           // 要改保護的頁起始位址
    SIZE_T dwSize,              // 大小
    DWORD  flNewProtect,        // 新保護（PAGE_EXECUTE_READWRITE = 0x40）
    PDWORD lpflOldProtect       // 輸出舊保護的指標（需要一個可寫位址）
);
```

**優點**：只需要在已分配的頁上改保護，不需要分配新記憶體。如果 shellcode 已經在 stack/heap 上了，直接改那塊的保護即可。

**難點**：`lpAddress` 必須是 shellcode 的實際 stack 位址，但 ASLR 讓 stack 位址每次都變——通常需要搭配 info leak（Ch 24/Ch 31）。

### VirtualAlloc：分配一塊 RWX 頁，複製 shellcode 進去執行

```
LPVOID VirtualAlloc(
    LPVOID lpAddress,           // NULL 表示讓 OS 選位址
    SIZE_T dwSize,
    DWORD  flAllocationType,    // MEM_COMMIT | MEM_RESERVE = 0x3000
    DWORD  flProtect            // PAGE_EXECUTE_READWRITE = 0x40
);
```

**優點**：分配的新頁一開始就是 RWX，不需要先把 shellcode 放到那裡再改保護。可搭配 `WriteProcessMemory` 把 shellcode 複製進去。

**難點**：拿到 RWX 頁的位址之後，還需要把 shellcode 複製過去（可以再呼叫一個 `memcpy` 或 `WriteProcessMemory`），比 VirtualProtect 多一步。

### WriteProcessMemory：把 shellcode 複製到 RX 頁

```
BOOL WriteProcessMemory(
    HANDLE  hProcess,           // 目標 process handle（本 process = GetCurrentProcess()）
    LPVOID  lpBaseAddress,      // 目標位址（.text 節裡的可執行頁）
    LPCVOID lpBuffer,           // 來源位址（shellcode）
    SIZE_T  nSize,
    SIZE_T* lpNumberOfBytesWritten
);
```

**用途**：直接把 shellcode 寫到既有的可執行頁（`.text` 節或 RX 的 DLL 頁）上，然後跳過去。不需要改保護，但需要找一塊可寫且可執行的空間——實際上不太常見，因為現代 OS 下 `.text` 通常是 RX（唯讀可執行），不可寫。

**更實際的用途**：把 shellcode 從 stack 複製到 VirtualAlloc 剛分配的 RWX 頁。

### 三者對比

| API | 前提 | 難點 | 適用場景 |
|---|---|---|---|
| `VirtualProtect` | shellcode 已在某個分配過的頁上 | 需要 shellcode 的精確位址（ASLR 問題） | shellcode 在 stack，有位址 leak |
| `VirtualAlloc` | 無（自己分配） | 分配完要再複製 shellcode 進去 | shellcode 位址未知，或放在可預測位置 |
| `WriteProcessMemory` | 目標是可執行頁（很罕見能寫 .text） | 找可寫可執行的目標頁 | 配合 VirtualAlloc 使用 |

實務上：**VirtualProtect 最常用**（直接改 stack/heap 上的 shellcode 頁保護），`VirtualAlloc + WriteProcessMemory` 是備案。

---

## x86 ROP Chain：VirtualProtect 佈局

### x86 Calling Convention（stdcall/cdecl 的 stack 傳參）

x86 Windows API 通常是 `__stdcall`：參數從右往左 push 到 stack，呼叫者不清 stack（stdcall 是 callee 清）。ROP chain 要偽造一個函式呼叫，就是把 stack 排列成「函式看到的 stack frame」。

```
呼叫 VirtualProtect 的 stack 佈局（x86）：

┌────────────────────────────────┐ ← 低位址
│ ... ROP gadgets ...            │   （用 gadgets 把下面的參數填好）
├────────────────────────────────┤
│ VirtualProtect 函式位址         │ ← 最後一個 gadget 的 ret 彈這個
├────────────────────────────────┤
│ shellcode_addr（作為 ret addr） │ ← VirtualProtect 返回後跳這裡
├────────────────────────────────┤
│ arg1: lpAddress（shellcode 起點）│
│ arg2: dwSize（例如 0x200）      │
│ arg3: PAGE_EXECUTE_READWRITE    │   = 0x40
│ arg4: &writable_mem（存 old）   │   需要一個可寫位址
└────────────────────────────────┘ ← 高位址
```

**完整 x86 VirtualProtect ROP chain 佈局圖**：

```
stack（從 ESP 開始，往高位址排）：

┌────────────────────────────────────────────────────────┐
│  ROP chain 開始（ESP 指向這裡）                         │
│                                                        │
│  [gadget: pop eax; ret]      ← 開始設參數              │
│  [0x40]                      ← eax = PAGE_EXECUTE_READWRITE
│  [gadget: mov [writable], eax; ret]  ← 存到可寫位址    │
│                                                        │
│  [gadget: pop eax; ret]                               │
│  [shellcode_size]            ← eax = 欲保護的大小      │
│  [gadget: push eax; pop ebx; ret]   ← ebx = size（備用）
│                                                        │
│  ... 更多 gadget 組裝參數到 stack 上 ...               │
│                                                        │
│  [VirtualProtect 位址]        ← 跳到這裡呼叫            │
│  [shellcode_start_addr]       ← VP 的返回位址 = shellcode
│  [shellcode_start_addr]       ← arg1: lpAddress       │
│  [shellcode_size]             ← arg2: dwSize           │
│  [0x40]                       ← arg3: PAGE_EXECUTE_READWRITE
│  [writable_addr_for_old]      ← arg4: lpflOldProtect  │
│                                                        │
│  [shellcode bytes ...]        ← 被保護後執行的 shellcode │
└────────────────────────────────────────────────────────┘

執行流：
1. ESP 指向 ROP chain 起點
2. gadgets 依序設好參數位置
3. 「ret」跳進 VirtualProtect
4. VirtualProtect 把 shellcode 那塊 stack 改成 PAGE_EXECUTE_READWRITE
5. VirtualProtect 返回到 shellcode_start_addr
6. shellcode 執行
```

> **未實測，理論預期**：實際 ROP chain 的 gadget 選擇依目標 binary 和載入模組而定；此佈局為教育性示意。

---

## x64 Calling Convention 對 ROP 的影響

這是 x64 ROP 和 x86 ROP 最大的實作差異，必須搞清楚，否則 chain 永遠跑不起來。

### x64 Windows ABI 的傳參規則

x64 Windows（Microsoft ABI）用 **register 傳前四個參數**：

| 參數位置 | 暫存器 |
|---|---|
| 第 1 個參數 | **RCX** |
| 第 2 個參數 | **RDX** |
| 第 3 個參數 | **R8** |
| 第 4 個參數 | **R9** |
| 第 5 個及之後 | stack（從 RSP+40 開始） |

> 對照 Linux x64 SystemV ABI：Linux 是 RDI/RSI/RDX/RCX/R8/R9（前六個），而且參數順序和 Windows 不同（Windows 第三個是 R8，Linux 第三個是 RDX）。你已經習慣的 Linux x64 gadget 用法在 Windows 要換一套。

### Shadow Space（HomeSpace）

這是 x64 Windows ABI 特有的、在 Linux 裡完全沒有的概念：

**呼叫方在呼叫函式之前，必須在 stack 上留出 32 bytes（4 × 8 bytes）的 shadow space（又叫 home space 或 register parameter area）**。

這 32 bytes 是為了讓被呼叫方可以把 register 參數（RCX/RDX/R8/R9）spill 到 stack 上——不管被呼叫方用不用。

```
x64 呼叫 VirtualProtect 前的 stack 狀態（呼叫方角度）：

                  ┌─────────────────┐ ← RSP（16-byte 對齊）
                  │  return address  │   （8 bytes）
                  ├─────────────────┤
                  │  shadow space    │   32 bytes
                  │  (home for RCX)  │   RSP+8
                  │  (home for RDX)  │   RSP+16
                  │  (home for R8)   │   RSP+24
                  │  (home for R9)   │   RSP+32
                  ├─────────────────┤
                  │  arg5（如果有）   │   RSP+40
                  │  arg6（如果有）   │   RSP+48
                  └─────────────────┘

VirtualProtect 只有 4 個參數，全用 register 傳，所以 stack 只需要：
  return address + 32 bytes shadow space
```

**對 ROP 的影響**：在 x64 ROP chain 裡，每次你要「假裝呼叫」一個 Windows API，你需要：
1. 用 gadget 把參數填進 RCX、RDX、R8、R9
2. 在呼叫前讓 RSP 對齊 16 bytes
3. 確保 RSP 上方有 32 bytes 的 shadow space
4. 然後讓 `ret` 跳到目標函式

### 16-byte 對齊

x64 ABI 要求：**在 `call` 指令執行前，RSP 必須是 16 的倍數**（通常 `call` 本身會 push 8 bytes 返回位址，所以在 `call` 前 RSP 需要是 `16n + 8` 讓 `call` 後變 `16n`）。

```
正確的對齊狀態：

  BEFORE call：  RSP = 0x00007FF... (結尾是 8，即 16n+8)
  call pushes 8 bytes → RSP = 0x00007FF... (結尾是 0，即 16n) ← 對齊
  AFTER call：   RSP 在被呼叫方裡是 16 對齊的

ROP chain 裡如果沒有正確對齊，某些 SSE 指令（movaps 等）會崩潰。
常見的補丁：在 chain 裡插一個 `ret` gadget（只有 ret，沒有其他操作）
讓 RSP 再動 8 bytes，達到對齊。
```

### 完整 x64 VirtualProtect ROP chain 佈局圖

```
stack（從 RSP 開始，往高位址排）：

┌──────────────────────────────────────────────────────────────┐
│  ROP chain 開始（RSP 指向這裡）                               │
│                                                              │
│  [gadget: pop rcx; ret]         ← 第 1 個參數               │
│  [shellcode_addr]               ← RCX = lpAddress（shellcode）
│                                                              │
│  [gadget: pop rdx; ret]         ← 第 2 個參數               │
│  [0x200]                        ← RDX = dwSize              │
│                                                              │
│  [gadget: pop r8; ret]          ← 第 3 個參數               │
│  [0x40]                         ← R8  = PAGE_EXECUTE_READWRITE
│                                                              │
│  [gadget: pop r9; ret]          ← 第 4 個參數               │
│  [writable_addr]                ← R9  = lpflOldProtect      │
│                                                              │
│  [gadget: ret]                  ← 對齊用（如需要）            │
│                                                              │
│  [gadget: sub rsp, 0x28; ...]   ← 撥出 shadow space 32 bytes │
│  OR                             （或找有等效效果的 gadget）    │
│  [VirtualProtect_addr]          ← ret 跳到 VirtualProtect   │
│  [shellcode_addr]               ← VirtualProtect 的返回位址  │
│  [shadow space × 4 × 8 bytes]  ← 32 bytes shadow（含在 ret 前）
│                                                              │
│  [shellcode bytes ...]          ← 被保護後執行               │
└──────────────────────────────────────────────────────────────┘

執行流：
1. pop rcx/rdx/r8/r9 gadgets 設好前四個參數
2. 對齊 RSP（如需要插 ret gadget）
3. ret 跳到 VirtualProtect（確保 RSP 有 shadow space）
4. VirtualProtect 把 shellcode 頁改成 RWX
5. 返回到 shellcode_addr，開始執行
```

> **未實測，理論預期**：此佈局為教育性示意；實際 chain 需要在目標 binary 的記憶體佈局下選合適 gadget，shadow space 的處理方式依 gadget 集合而異。x64 ROP 在 Part 6 的 Ch 40（x64 ABI 深挖）會有更完整的實作討論。

---

## Gadget 來源

### 系統 DLL

最穩定的 gadget 來源是被大量 process 載入的系統 DLL：`ntdll.dll`、`kernel32.dll`、`kernelbase.dll`。它們的 image base 在同一次開機裡（per-boot ASLR）是固定的——見 Ch 24。

但現代 Windows 10/11 的系統 DLL 全部帶 `/DYNAMICBASE`，每次開機位址都不同。要用它們作為 gadget 來源，需要先 leak 它們的 base address（Ch 24 的主題）。

### 非 ASLR 模組

如果靶 process 裡有沒有開 `/DYNAMICBASE` 的模組（老舊第三方 DLL、沒更新的 ATL/MFC），它們每次的 image base 固定（不依賴 ASLR），是最穩定的 gadget 來源。

檢查方法（mona）：

```
!mona modules
```

輸出裡找 `ASLR: False`（或 `Rebase: False`）的模組。

### 靶 Binary 本身（沒有 ASLR）

如果靶 binary 編譯時沒有 `/DYNAMICBASE`，它的 `.text` 節位址固定，可以直接用。現代靶機很少這樣，但舊 CTF 題常見。

---

## mona.py `!mona rop` 工作流

mona.py 是 Corelan 出的 Immunity Debugger / WinDbg 外掛，`!mona rop` 是 Windows ROP chain 生成的瑞士刀。

> **未實測，理論預期**：以下步驟需要 Immunity Debugger + mona.py，或 WinDbg + mona 相容版本。

### 步驟 1：確認模組列表和防護

```
!mona modules
```

輸出包含每個載入模組的：SafeSEH / ASLR / Rebase / NX / OldSEH 狀態。優先選 `ASLR: False`、`Rebase: False` 的模組作為 gadget source。

### 步驟 2：生成 ROP 建議

```
!mona rop -m kernel32.dll,ntdll.dll
```

`-m` 指定從哪些模組找 gadget。mona 會：
1. 在指定模組裡找所有 ROP gadget（以 `ret` 結尾的序列）
2. 自動識別可用於 `VirtualProtect`、`VirtualAlloc` 的 gadget 組合
3. 輸出一個 Python 骨架

### 步驟 3：閱讀 mona 輸出的骨架

mona 輸出的 `rop_chains.txt` 包含類似這樣的結構（未實測，示意）：

```python
# VirtualProtect ROP chain（mona 生成骨架，未實測）
def create_rop_chain():
    rop_gadgets = [
      #[---INFO:gadgets_to_set_esi:---]
      0x45aa6b98,  # POP ESI # RETN    [target.dll]
      0x1002dc4c,  # ptr to &VirtualProtect() [IAT target.dll]
      #[---INFO:gadgets_to_set_ebp:---]
      0x1002ab2f,  # POP EBP # RETN    [target.dll]
      0x1001c95a,  # & push esp # ret  [target.dll]
      #[---INFO:gadgets_to_set_ebx:---]
      0x1001a8b2,  # POP EBX # RETN    [target.dll]
      0x00000201,  # 0x00000201 -> ebx (size of shellcode)
      # ... 更多 gadgets ...
      0x1002e3a5,  # PUSHAD # RETN     ← 把暫存器推到 stack，配合 VP 的呼叫慣例
    ]
    return b''.join(struct.pack('<I', g) for g in rop_gadgets)
```

> mona 的 ROP chain 通常用 **`PUSHAD` + 已設好暫存器**的模式（x86）：把 EAX-EDI 的值 push 到 stack，剛好形成 stdcall 需要的 stack 參數。這是 mona 特有的技法，和手寫 chain 的邏輯不同但等效。

### 步驟 4：整合進 exploit

```python
# 完整 exploit 骨架（x86，VirtualProtect via mona chain）
# 未實測，理論預期

from pwn import *
import struct

def create_rop_chain():
    # 貼上 mona 輸出的 gadget list
    rop_gadgets = [ ... ]
    return b''.join(struct.pack('<I', g) for g in rop_gadgets)

# shellcode（這裡放 calc.exe popup 或 reverse shell）
shellcode = b"\x90" * 16 + b"..."  # 前綴 NOP sled

offset  = 100          # cyclic_find 量到的 offset
rop     = create_rop_chain()

payload  = b"A" * offset
payload += rop
payload += shellcode

print(f"[*] total payload: {len(payload)} bytes")
```

---

## Stack Pivot

有時候 overflow 只能控制很少的 bytes（例如只蓋到 saved EIP/RSP 但後面空間不夠放完整 chain）。這時候需要 **stack pivot**：用一個 gadget 把 RSP 改到攻擊者控制的記憶體區（例如 heap 上）。

常見的 stack pivot gadget：

```nasm
; x86
xchg eax, esp ; ret    ← 把 EAX 當新 ESP（EAX 如果指向 heap 上的 chain）
mov esp, ebp  ; ret    ← 有時 EBP 指向可控區域
add esp, 0x100; ret    ← 把 ESP 往後跳，跳過 overflow 的限制區

; x64
xchg rax, rsp ; ret
mov rsp, rbx  ; ret
```

mona 可以幫你找 pivot：

```
!mona stackpivot -distance 500
```

找能讓 ESP 移動大約 500 bytes 的 pivot gadget。

---

## 底層機制：DEP 的 kernel 側

> 此節為概念說明，不涉及需要 MSVC/WinDbg 的實驗。

`VirtualProtect` 在 kernel 側最終呼叫 `NtProtectVirtualMemory` syscall（對應 Linux 的 `mprotect` syscall）。kernel 會：

1. 驗證 range 在進程的 VAS 內且是已提交（committed）的頁
2. 檢查是否允許 `PAGE_EXECUTE_*`（在 DEP 強制模式下，如果 process 是 DEP-aware 的，kernel 還要看 NX bit 的 override 政策）
3. 修改頁表 PTE 的 NX bit（清 NX bit 讓頁可執行）
4. 讓 TLB 失效（`INVLPG`）讓改動生效

對 exploit 開發者的意義：`VirtualProtect(ptr, size, PAGE_EXECUTE_READWRITE, &old)` 成功返回後，ptr 那塊記憶體的 PTE NX bit 被清了，CPU 不再拒絕從那裡 fetch 指令。

---

## 對比與取捨

| 繞過方式 | 前提 | 難度 | 說明 |
|---|---|---|---|
| `VirtualProtect` via ROP | shellcode 已在可尋址的頁 | 中 | 最常見；需要 shellcode 的精確位址（通常要 leak） |
| `VirtualAlloc` via ROP | 無 | 中高 | 自己分配 RWX 頁；需要複製 shellcode 進去（多一步） |
| `WriteProcessMemory` via ROP | 目標可執行頁可寫（少見） | 高 | 不常用 |
| ret2libc（ret2plt 在 Windows 等效） | 只需要特定函式的位址 | 中 | Linux 常用，Windows 較少直接套用（calling convention 不同） |
| JIT spraying（若有 JIT 引擎） | 目標 process 有 JIT（瀏覽器等） | 高 | Ch 36 的 ACG 會講到 JIT 被限制 |

---

## 踩雷集錦

1. **「x64 ROP 把參數 push 到 stack 就好」**：x64 Windows ABI 前四個參數用 register（RCX/RDX/R8/R9）傳，不是 push 到 stack。直接 push 參數然後 ret 到 VirtualProtect，函式看到的 RCX 是垃圾值，一定失敗。先用 `pop rcx; ret` 等 gadget 設 register。

2. **「忘記 shadow space」**：x64 呼叫任何函式前，RSP 上方需要有 32 bytes 的 shadow space（home area）。在 ROP chain 裡，這通常用 `sub rsp, 0x28; ...` 或等效的 gadget 處理。忘了 shadow space 會導致被呼叫方 spill 參數時踩到 ROP chain 本身，崩潰位置很難理解。

3. **「RSP 不用對齊」**：x64 ABI 要求呼叫前 RSP 是 `16n+8`（讓 call push 後變 `16n`）。如果 chain 組好後對齊不對，含有 `movaps` 的系統函式會崩在不明位置（`STATUS_ACCESS_VIOLATION` 但位址看起來正確）。補救：在 chain 裡插一個純 `ret` gadget，讓 RSP 再動 8 bytes。

4. **「gadget 在 kernel32.dll 裡穩定，不需要 leak」**：Win10/11 的 kernel32.dll 有 ASLR（/DYNAMICBASE），每次開機 base 不同。只有**同一次開機**裡 base 固定——如果你能在同一次開機裡用同一個 binary，base 不變，但如果靶機重開機，之前量到的位址全部失效。要在不同 boot 之間穩定使用，必須動態 leak。

5. **「mona 生成的 chain 直接能用」**：mona 的 chain 是骨架，gadget 位址是基於 mona 抓到的模組版本。如果靶機 DLL 版本不同（不同補丁的 kernel32.dll），gadget 位址全部偏移，需要重新搜尋。用 `!mona rop` 前確認靶機模組版本。

---

## 進階：再往深一層

### ret2ntdll / ret2ZwAllocateVirtualMemory

除了 `VirtualProtect`，也可以直接呼叫 `ntdll.dll` 裡的 native syscall wrapper。`ZwAllocateVirtualMemory` 是 `VirtualAlloc` 的底層，直接呼叫它可以繞過某些 `VirtualAlloc` 的上層限制（但在現代環境差異不大）。Syscall 路線（直接 syscall 指令）在 Ch 7 有背景。

### ACG（Arbitrary Code Guard）下的 ROP chain 限制

Ch 36 的 ACG 會讓 `VirtualProtect` 改保護到 `PAGE_EXECUTE_*` 失效——ACG 不允許動態生成或修改可執行頁的保護。開了 ACG 的 process 裡，「VirtualProtect → shellcode」這條路被直接封堵，攻擊者必須走 data-only attack（Ch 37）。先知道這個限制的存在。

### x64 ROP 的 gadget 密度問題

x64 binary 的 gadget 密度比 x86 低，因為 x64 使用更多的多字節指令（REX prefix + 操作碼），「湊巧」形成有用 gadget 的序列少。對 `pop rcx/rdx/r8/r9; ret` 這類 gadget，通常能在系統 DLL 找到；但一些奇特的 gadget（例如 `xchg rax, rsp` 用於 stack pivot）可能很稀少。mona 或 rp++ 搜尋時加 `-v`（verbose）可以看完整的 gadget 序列。

### EMET 和 ROP mitigation

EMET（Enhanced Mitigation Experience Toolkit）曾有一個機制嘗試偵測 ROP chain（用硬體 breakpoint 監控 `VirtualProtect`、`VirtualAlloc` 等 API 的呼叫位置——如果呼叫方的 RSP 指向非程式碼區，就判定是 ROP chain 呼叫）。這個機制有很多繞過方法，但它引出了「如何讓 API 呼叫看起來來自合法程式碼」的問題，是 CFG 之前的過渡期防禦。Ch 38 的 EMET 演進史會詳細說明。

---

## 動手練習

> **環境**：Windows 11 x64（WSL 也可）+ mingw gcc（`C:\msys64\ucrt64\bin`）+ Python 3.12 + pwntools。本練習驗 DEP 行為，不需要 MSVC。

1. 用 mingw 編一個關掉 NX 的簡單程式（測試 VirtualProtect 能不能改 stack 保護）：

```c
/* test_vp.c — 驗 VirtualProtect 是否能讓 stack 頁可執行 */
#include <windows.h>
#include <stdio.h>

int main(void) {
    char buf[64] = {0x90, 0x90, 0xC3};  /* NOP NOP RET */
    DWORD old;
    BOOL  ok;

    printf("buf @ %p\n", buf);

    ok = VirtualProtect(buf, 64, PAGE_EXECUTE_READWRITE, &old);
    printf("VirtualProtect returned: %d, old protect: 0x%x\n", ok, old);

    if (ok) {
        /* 嘗試跳到 buf 上執行（buf 裡是 NOP NOP RET）*/
        printf("Jumping to buf...\n");
        ((void(*)())buf)();
        printf("Returned from buf (success!)\n");
    }
    return 0;
}
```

```bat
gcc -o test_vp.exe test_vp.c
.\test_vp.exe
```

預期輸出：`VirtualProtect returned: 1`，且 `Returned from buf (success!)`——這驗證了 `VirtualProtect` 確實能把 stack 頁改成可執行。

2. 把 `buf` 的內容改成 `\xCC`（INT3 中斷）再跳過去，用除錯器觀察行為。

3. 在沒有除錯器的情況下跳到 `\xCC`，觀察 Windows 的錯誤報告（預期：`STATUS_BREAKPOINT` 或「應用程式已停止工作」）。

---

## 本章重點整理

- DEP 讓 stack/heap 上的資料頁不可執行（NX bit in PTE），直接跳到 shellcode 觸發 `EXCEPTION_ACCESS_VIOLATION`。ROP 的回應是：只跳到**既有可執行頁**裡的 gadget，用 ret 鏈串起完整邏輯。
- Windows 的 ROP 終極目標：`VirtualProtect`（把 shellcode 頁改成 RWX 再跳過去）；備選是 `VirtualAlloc`（分配新 RWX 頁）+ `WriteProcessMemory`（複製 shellcode 進去）。
- **x64 vs x86 ROP 最大差異**：x64 前四個參數走 register（RCX/RDX/R8/R9），需要 pop reg; ret gadget 設好再呼叫；呼叫前要有 32 bytes shadow space；RSP 要 16-byte 對齊。三個都忘記會讓 chain 以不明原因崩潰。
- `!mona rop` 是 Windows ROP chain 的黃金起點，但 gadget 位址依賴 mona 掃描時的模組版本，靶機換版本就要重掃。

---

## 自我檢核

- [ ] 不看筆記，能說出 DEP 在 PTE 層面怎麼擋執行（NX bit 在哪個 bit）、觸發後的例外碼是什麼
- [ ] 能說出 `VirtualProtect` 的四個參數（名稱和型別）、以及在 x64 ROP chain 裡這四個參數用哪四個 register 傳
- [ ] 能說出 x64 ABI shadow space 是什麼、為什麼存在、在 ROP chain 裡忘了的後果
- [ ] 畫出 x86 VirtualProtect ROP chain 的 stack 佈局（從 ESP 開始，gadgets → VirtualProtect 位址 → ret addr → 四個參數）
- [ ] 面試被問「DEP 開著怎麼執行 shellcode」：能說出完整路線（找 gadget → 組 ROP chain → VirtualProtect 改保護 → 跳 shellcode）和每個環節的必要前提

---

## 延伸閱讀

### 部落格 / 教學

- **Corelan Team — "Exploit writing tutorial part 10: Chaining DEP with ROP"**（[corelan.be](https://www.corelan.be/index.php/2010/06/16/exploit-writing-tutorial-part-10-chaining-dep-with-rop-the-rubikstm-cube/)）
  - **讀哪裡**：全文；特別是「VirtualProtect rop chain」一節和 mona.py `!mona rop` 的使用示範
  - **學什麼**：Windows x86 ROP chain 的黃金教程，本章的工作流直接來自這裡
  - **和本章關聯**：本章是原理深挖，這篇是動手實踐；讀本章再看這篇才能懂每個 gadget 為什麼被選
  - **前提**：Ch 19（stack overflow）+ Ch 20（/GS）+ 本章

- **Connor McGarr — "ROP for Windows x64"**（[connormcgarr.github.io](https://connormcgarr.github.io/)）
  - **讀哪裡**：x64 ROP 系列（搜尋 "x64 ROP" 在他的 blog）
  - **學什麼**：x64 calling convention 對 ROP chain 的完整影響，包含 shadow space 和對齊的實際處理方式
  - **和本章關聯**：本章 x64 ROP 部分的實踐面；Ch 40 的 x64 ABI 深挖前的先修
  - **前提**：本章 + 基本 x64 組語

### 論文 / 研究

- **"The Geometry of Innocent Flesh on the Bone: Return-into-libc without Function Calls (on the x86)"** — Hovav Shacham（ACM CCS 2007）
  - **讀哪裡**：Section 3「Defining ROP」和 Section 4「Finding ROP Gadgets」
  - **學什麼**：ROP 的學術奠基論文；gadget 完備性的形式化論證（任意圖靈完備計算可用 gadget 實現）
  - **和本章關聯**：本章的 ROP 概念基礎；理解「為什麼 ROP 能做完整計算」的理論根據
  - **前提**：x86 組語 + 基本計算理論

- **"Jump-Oriented Programming: A New Class of Code-Reuse Attack"** — Bletsch et al.（ASIACCS 2011）
  - **讀哪裡**：Section 1–3（JOP 概念與和 ROP 的比較）
  - **學什麼**：JOP（Jump-Oriented Programming）是在 `ret` gadget 被監控時的替代方案，了解 ROP 防禦對抗的演進
  - **和本章關聯**：ROP 的擴展概念；和 Ch 32 CFG（防 indirect jump）的關聯

### 工具

- **[mona.py — Corelan Team（GitHub）](https://github.com/corelan/mona)**
  - **讀哪裡**：README 裡 `!mona rop` 的說明（`-m`/`-t`/`-f` 旗標）
  - **學什麼**：`!mona rop` 的輸出格式（rop_chains.txt）、`PUSHAD` pattern 的意義、如何指定 bad char 過濾
  - **和本章關聯**：本章 mona 工作流的參考文件；先讀懂輸出欄位再動手

ASLR 讓所有「穩定 gadget 位址」的假設崩潰。下一章拆 Windows ASLR 的設計細節，以及為什麼「穩定」這件事在 Windows 上比 Linux 更複雜。

→ [Ch 24 — ASLR：Windows 特性 / leak / 部分覆寫](./24-aslr.md)
