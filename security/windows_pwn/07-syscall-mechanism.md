# Ch 7 — syscall 機制與版本漂移

> **目標**：能完整解讀 ntdll 裡一個 `Nt*` stub 的每個 bytes；理解 SSN（System Service Number）是什麼、為什麼跨 Windows build 會漂移（對照 Linux 的穩定性）；知道 SSDT 的作用；理解 WoW64 Heaven's Gate 的概念；從防禦與研究角度認識 direct syscall / indirect syscall，以及偵測它們的方法。本章是 Ch 6 的「向下延伸」——把 syscall stub 裡那 8 個指令的每個細節都說清楚。

> **環境**：Python 3.12 + capstone；ntdll 直接從記憶體讀取。本章的反組譯輸出全部是本機（Win11 build 26200）真實實測結果。

## 為什麼需要這個？

Ch 6 說了 `NtAllocateVirtualMemory` 的 stub 長這樣：

```
mov r10, rcx
mov eax, 0x18   ← SSN
test [KSD+0x308], 1
jne int_2e_path
syscall
ret
int 0x2e
ret
```

這個 `0x18`（= 24）是什麼？它是怎麼決定的？為什麼下個 Windows build 它可能變成 `0x1a`？如果 EDR 把 `syscall` 前的 `mov r10,rcx` 改成 `jmp hook`，攻擊者怎麼繞過？繞過之後要怎麼偵測？

這些問題的答案拉出了「syscall 機制」的完整面貌，也是 EDR evasion / 反 evasion 技術競爭的核心場域。

## 先建立直覺：餐廳叫號系統

把 syscall 想成餐廳叫號：

```
 客人（App）                      廚房（Kernel）
    │                                │
    │  「我要 NtAllocateVirtualMemory」  │
    │                                │
    │  ntdll stub 說：               │
    │  「你的號碼牌是 24」             │
    ▼                                ▼
  syscall ──────── 號碼 24 ─────── SSDT[24]
                                  指向
                              nt!NtAllocateVirtualMemory
```

號碼牌（SSN）是廚房（kernel）根據它自己的菜單（SSDT）編的，每天（每個 build）菜單可能重排，同一道菜的號碼就變了。Linux 的廚房保證「`write` 永遠是 1 號」，Windows 的廚房不保證任何號碼跨版本穩定。

## Nt* stub 的解剖：8 個指令，全部說清楚

本機真實反組譯（Python + capstone，ntdll base 0x7ffa859c0000）：

```
$ python tmp_disasm.py

--- NtAllocateVirtualMemory @ 0x7ffa85b20350 ---
Raw(24 bytes): 4c 8b d1 b8 18 00 00 00 f6 04 25 08 03 fe 7f 01 75 03 0f 05 c3 cd 2e c3

  0x7ffa85b20350: mov   r10, rcx
  0x7ffa85b20353: mov   eax, 0x18
  0x7ffa85b20358: test  byte ptr [0x7ffe0308], 1
  0x7ffa85b20360: jne   0x7ffa85b20365
  0x7ffa85b20362: syscall
  0x7ffa85b20364: ret
  0x7ffa85b20365: int   0x2e
  0x7ffa85b20367: ret
```

對照另外幾個 stub（同樣本機實測）：

```
--- NtCreateFile @ 0x7ffa85b20af0 ---
Raw(24 bytes): 4c 8b d1 b8 55 00 00 00 f6 04 25 08 03 fe 7f 01 75 03 0f 05 c3 cd 2e c3
  0x7ffa85b20af0: mov   r10, rcx
  0x7ffa85b20af3: mov   eax, 0x55           ← SSN = 0x55 = 85
  0x7ffa85b20af8: test  byte ptr [0x7ffe0308], 1
  0x7ffa85b20b00: jne   0x7ffa85b20b05
  0x7ffa85b20b02: syscall
  0x7ffa85b20b04: ret
  0x7ffa85b20b05: int   0x2e
  0x7ffa85b20b07: ret

--- NtReadFile @ 0x7ffa85b20110 ---
  0x7ffa85b20110: mov   r10, rcx
  0x7ffa85b20113: mov   eax, 6             ← SSN = 0x06 = 6
  ... (其餘指令相同)

--- NtWriteFile @ 0x7ffa85b20150 ---
  0x7ffa85b20150: mov   r10, rcx
  0x7ffa85b20153: mov   eax, 8             ← SSN = 0x08 = 8
  ... (其餘指令相同)
```

**觀察**：除了 `mov eax, SSN` 那一個 dword 不同，所有 stub 的其餘 bytes 完全一致。24 bytes 裡，有意義的差別只有 bytes 4-7（SSN）。

本機這台 Win11 build 26200 的 SSN 調查結果：

```
NtReadFile                          SSN = 0x0006 (6)
NtWriteFile                         SSN = 0x0008 (8)
NtClose                             SSN = 0x000f (15)
NtAllocateVirtualMemory             SSN = 0x0018 (24)
NtFreeVirtualMemory                 SSN = 0x001e (30)
NtOpenProcess                       SSN = 0x0026 (38)
NtMapViewOfSection                  SSN = 0x0028 (40)
NtQuerySystemInformation            SSN = 0x0036 (54)
NtCreateSection                     SSN = 0x004a (74)
NtProtectVirtualMemory              SSN = 0x0050 (80)
NtCreateFile                        SSN = 0x0055 (85)
NtOpenThread                        SSN = 0x0139 (313)
```

### 為什麼是 `mov r10, rcx`？

x86-64 System V ABI（Linux 用）和 Windows x64 ABI 的函式呼叫慣例裡，第一個整數/指標參數都放在 `rcx`（Windows x64）或 `rdi`（System V）。`syscall` 指令執行時，CPU 自動把**返回 RIP 存進 `rcx`**（因為 `syscall` 需要記住返回位址）。

這意味著如果不先把第一個參數從 `rcx` 搬走，執行 `syscall` 後 `rcx` 就被返回位址覆蓋，kernel 就讀不到第一個參數。`r10` 是 kernel-side 讀第一個參數的慣例暫存器（在 x64 Windows syscall convention 裡，kernel 從 r10 取第一個參數）。

對照 Linux：Linux x64 的 syscall 慣例用 `rdi, rsi, rdx, r10, r8, r9` 傳參，也是因為 `rcx` 被 `syscall` 消耗，所以第四個參數用 `r10` 而不是 Windows 呼叫慣例裡的 `rcx`。**這是兩個平台 syscall 傳參差異的同一根本原因。**

### `test byte ptr [0x7ffe0308], 1` 是什麼？

`0x7ffe0000` 是 `KUSER_SHARED_DATA`（KSD）的固定 VA，在所有行程中都是這個位址，kernel 把它 map 成 user-space read-only 的共用頁面。KSD 的結構頭幾百 bytes 是公開的（`ntexapi.h` WDK）。

偏移 `0x308` 處（就是 `0x7ffe0308`）是 `SystemCallPad[0]`，存的是決定使用哪條 syscall 路徑的旗標。Bit 0：
- 0 → 用 `syscall` 指令（現代 x86-64，正常路徑）
- 1 → 用 `int 0x2e`（舊式，或某些 hypervisor 模式）

本機讀到 `0x00 00 00 00`，bit 0 = 0，走 `syscall`。

`0x7ffe0308` 這個常數是 hardcoded 在 ntdll 的 stub 機器碼裡（你在 raw bytes 裡看到 `08 03 fe 7f` 就是 `0x7ffe0308` 的 little-endian）——ntdll 依賴 KSD 永遠在這個固定位址，這是 Windows 核心 ABI 的一部分。

### `syscall` 指令做了什麼？

`syscall` 是 AMD64 引入的快速系統呼叫指令（Intel 稱 `syscall`，AMD64 是主要推手）。執行時 CPU 做：

```
1. 把 RIP（返回位址）存進 RCX
2. 把 RFLAGS 存進 R11
3. 把 CS 設成 kernel code segment（從 IA32_STAR MSR）
4. 把 RIP 設成 kernel entry（從 IA32_LSTAR MSR）
5. 清 IF（關中斷），清 RF
6. ring 3 → ring 0
```

CPU 跳進 `ntoskrnl!KiSystemCall64`（LSTAR 指向的位址），kernel 從 `rax`（= SSN）、`r10`（= 第一個參數）、`rdx, r8, r9`（後續參數）讀取呼叫資訊。

`sysret` 做相反：從 `rcx` 恢復 `rip`，從 `r11` 恢復 `rflags`，ring 0 → ring 3。

### `int 0x2e`：為何還在？

`int 0x2e`（interrupt 46）是 Windows NT 3.1 時代（1993 年）就在用的系統呼叫中斷。`syscall` 指令是 AMD64 引入後（Windows XP SP2 以後）才成為主路徑。

現在 `int 0x2e` 留著是為了相容：某些 hypervisor（尤其是 VMware Workstation 舊版本）或特定除錯環境不支援 `syscall` 的正確模擬，這時 KSD+0x308 的 bit 0 就會被 kernel 設成 1，切換到 `int 0x2e` 路徑。

在 malware 分析或 evasion 研究裡，`int 0x2e` 有時被當作「alternate syscall」使用，因為部分安全工具只監控 `syscall` 指令，沒有同時監控 `int 0x2e`。

## SSN 是什麼，為何跨 build 漂移？

**SSN（System Service Number）**，也稱 **System Call Index**，是 kernel 的 SSDT（System Service Descriptor Table）裡函式指標陣列的**索引號**。

### SSDT 概念

```
SSDT（kernel 裡的陣列）:
  Index 0x06:  &nt!NtReadFile
  Index 0x08:  &nt!NtWriteFile
  Index 0x0f:  &nt!NtClose
  Index 0x18:  &nt!NtAllocateVirtualMemory
  Index 0x55:  &nt!NtCreateFile
  ...
```

`KiSystemCall64` 進來後，用 `eax` 做 SSDT 的 table lookup：

```c
// 概念性虛擬碼
NTSTATUS KiSystemCallDispatch(ULONG SSN, ...) {
    if (SSN >= ServiceTableLimit) return STATUS_INVALID_SYSTEM_SERVICE;
    return KeServiceDescriptorTable[SSN](...);
}
```

SSDT 本身在 kernel 裡的 `KeServiceDescriptorTable`，`ntdll.dll` 的 stub 裡那個 `mov eax, SSN` 的常數，是**編譯 ntdll 時決定的，必須和當時的 kernel SSDT 順序完全對齊**。

### 為什麼 SSN 會漂移？

每次 Microsoft 在 SSDT 裡**新增或移動**一個 syscall，後面所有的 SSN 都可能往後移一格。ntdll 和 kernel 是**一起編譯出來的**，一個 Windows build 對應一組固定的 SSN 對應關係。

下一個 build 加了新 syscall，如果插在中間，後面的全部 +1。沒有任何「保持 SSN 穩定」的政策。

**j00ru 的 Windows syscall tables**（[vexillium.org](https://j00ru.vexillium.org/syscalls/nt/64/)）有跨版本的 SSN 漂移記錄，舉幾個例子（節錄，以就常見版本為準）：

| 函式 | Win10 20H2 | Win11 22H2 | Win11 24H2 |
|---|---|---|---|
| `NtAllocateVirtualMemory` | 0x0018 | 0x0018 | 0x0018（本機） |
| `NtCreateFile` | 0x0055 | 0x0055 | 0x0055（本機） |
| `NtOpenThread` | 0x012e | 0x012e ~ 0x0139 | 0x0139（本機）|

有些函式（如 `NtReadFile`）的 SSN 幾乎從未變過，因為它在 SSDT 早期就固定了；有些（如 `NtOpenThread`）差異就大。重點是：**沒有任何 SSN 被官方保證跨版本不動**。

### 對照 Linux：syscall number 為什麼穩定？

Linux kernel 的系統呼叫號是 **ABI（Application Binary Interface）保證**的一部分。Torvalds 有一條不成文但嚴格執行的原則：**不能破壞 userland binary 的 syscall 相容性**。一個 1993 年靜態編譯的 Linux x86 binary，`write = 4`，在今天的 6.12 kernel 上還能跑。

Windows 沒有這個政策，因為 Microsoft 的策略是「透過 ntdll 這個 intermediary 隔絕應用程式和 SSN 的直接耦合」。只要應用程式透過 ntdll 呼叫，ntdll 會有對應 build 的正確 SSN，應用程式不需要知道 SSN。問題只在繞過 ntdll 的情境——direct syscall。

## WoW64 與 Heaven's Gate（概念）

在 64 位元 Windows 上跑 32 位元行程時，有一層 **WoW64（Windows on Windows 64）** 轉換：

```
32 位元 App
    │
    ▼
wow64.dll + wow64win.dll   ← WoW64 shim 層
    │
    ▼  Heaven's Gate：far call 切換到 CS:33（x64 段）
32→64 bit 切換（far jmp 0x33:address）
    │
    ▼
x64 ntdll syscall stub
    │
    ▼
kernel（永遠是 x64 的）
```

**Heaven's Gate** 是這個機制的俗稱，特指用 `far jmp` 或 `far call` 指定 CS = 0x33（x64 code segment）把執行流從 32 位元模式切換到 64 位元模式這個技術。0x33 是 64 位元 CS 的 selector。

從 exploit 研究的角度，Heaven's Gate 有幾個有趣的面向：

- Malware 可以用它讓 32 位元行程直接執行 64 位元程式碼（繞過針對 32 位元的 API hook）
- 部分 32 位元分析工具（解碼器、hook）看不到 64 位元的程式碼執行
- WoW64 的 hook 層比純 64 位元行程多了一層可以被 unhook 的地方

Heaven's Gate 的細節（far jmp 的 `CS:RIP` 語法、TEB WoW64 結構）超出本課深度；知道它存在和它的目的即可。

## Direct Syscall / Indirect Syscall（防禦視角）

### 為什麼有人這樣做？

如果 EDR 把 ntdll 裡的 `NtAllocateVirtualMemory` 開頭改成 `jmp hook`，正常呼叫會被攔截。

**Direct syscall**：不透過 ntdll，自己在程式碼裡重建 syscall stub：

```asm
; 自己實作的 stub（假設 SSN = 0x18）
mov r10, rcx
mov eax, 0x18      ← hardcoded SSN
syscall
ret
```

這樣執行的 `syscall` 指令**在 ntdll 之外**，EDR 掛在 ntdll 上的 hook 完全被跳過。問題：SSN 0x18 是 hardcoded 的，換個 build 就壞掉。

**Indirect syscall**：把 SSN 動態從 ntdll 裡讀出來（讀那個 `mov eax, SSN` 的 dword），然後用 ntdll 裡的 `syscall` 指令位址（但跳過 ntdll 函式開頭被 hook 的部分）：

```asm
; 1. 動態讀 SSN（掃 ntdll 記憶體，找 mov r10,rcx 開頭，取偏移 4 的 dword）
; 2. 取 ntdll stub 裡 syscall 指令的位址（偏移 0x12 處）
; 3. 自己 mov r10,rcx; mov eax,[動態SSN]; 然後 jmp 到 ntdll 的 syscall 指令
```

indirect syscall 的優點：`syscall` 指令的 RIP 在 ntdll 裡，不在可疑的私有記憶體頁面。

### Hell's Gate / Halo's Gate（動態取 SSN）

**Hell's Gate**（Smelly__vx & am0nsec, 2020）的核心思想：在執行時掃描 ntdll 的 `.text` section，找到目標 `Nt*` 函式的機器碼，提取 `mov eax, SSN` 的那個 dword，動態得到 SSN。不需要 hardcode，不需要靠 syscall table。

```python
# 概念：從記憶體讀 SSN（本機實測）
# NtAllocateVirtualMemory @ 0x7ffa85b20350
# Raw bytes: 4c 8b d1 b8 18 00 00 00 ...
#                         ^^^^^^^^^^^
#                    bytes[4:8] = SSN（little-endian）

raw = bytes([0x4c, 0x8b, 0xd1, 0xb8, 0x18, 0x00, 0x00, 0x00, ...])
if raw[0:4] == b'\x4c\x8b\xd1\xb8':    # mov r10,rcx; mov eax,
    ssn = int.from_bytes(raw[4:8], 'little')   # 0x18 = 24
```

**Halo's Gate**（sektor7, 2021）是 Hell's Gate 的延伸：如果 EDR 已經把那幾個 bytes hook 掉（`jmp hook`，開頭不是 `4c 8b d1 b8`），就往**相鄰的 Nt* 函式**找，因為 stub 緊密排列，相鄰函式的 SSN 必然是連續或接近的，用差值推回來。

這是一個「EDR hook → 攻擊者繞過 → EDR 偵測繞過行為」的競爭螺旋。

### 偵測 Direct Syscall 的防禦方法

從藍隊 / EDR 設計角度，偵測 direct syscall 有幾個已公開的方法：

**方法 1：RIP 不在 ntdll**

`syscall` 進 kernel 時，`rcx` 存的是返回位址（= syscall 指令後面的 `ret` 位址）。如果那個位址不在 ntdll 的映像範圍內，就是 direct syscall 的強烈指標。Kernel callback（PsSetCreateThreadNotifyRoutine 等）或 ETW-TI（Early Launch Antimalware Telemetry Integration）可以在 syscall 進來時檢查返回 RIP。

**方法 2：Call stack 回溯**

正常的 syscall 路徑，call stack 應該從 ntdll!Nt* → kernelbase → kernel32 → 應用程式邏輯。Direct syscall 的 stack 會少掉 ntdll frame，甚至只有一個 frame。ETW + stack walk 可以偵測這個異常。

**方法 3：Memory-backed 掃描**

如果行程的私有記憶體頁面裡出現 `4c 8b d1 b8`（`mov r10,rcx; mov eax,...`）這個 pattern，高度可疑。這個 pattern 只應該出現在 ntdll 的 `.text` section，不應該出現在 heap 或匿名映射頁面。

**方法 4：ETW-TI（kernel-side）**

Windows 自 10 1511 起有 ETW Threat Intelligence（ETW-TI）Provider，可在 kernel 裡記錄系統呼叫事件，包含呼叫者的模組和位址。這不依賴 ntdll-side hook，繞不過。但需要高權限或 ELAM 驅動才能訂閱。

| 偵測方法 | 能抓 direct syscall？ | 能抓 indirect syscall？ | 攻擊者繞過難度 |
|---|---|---|---|
| ntdll inline hook | 否（被繞過的目標） | 部分（看跳回哪裡）| 低（就是要繞它）|
| RIP 不在 ntdll（kernel callback）| 是 | 否（RIP 在 ntdll）| 高 |
| Call stack 回溯（ETW）| 是 | 部分 | 高 |
| 記憶體 pattern 掃描 | 是（靜態分析）| 是（靜態）| 中（混淆 bytes）|
| ETW-TI（kernel provider）| 是 | 是 | 很高 |

現代的 EDR 競爭已經進入「kernel-side 監控」，純 ntdll hook 在這個戰場上並非主力，而是最容易被繞過的那層。

## 底層機制：`syscall` 之後 kernel 做什麼

進入 `KiSystemCall64` 之後（概念，非 kernel 逆向課）：

```
CPU 執行 syscall
    │
    ▼
KiSystemCall64 (ntoskrnl.exe)
    │
    ├─ 保存 user-mode 狀態（rip/rflags 在 rcx/r11，其餘 push 到 kernel stack）
    │
    ├─ 設好 IRQL（= DPC level if needed）
    │
    ├─ 讀取 SSDT：
    │    ULONG_PTR fn_offset = KeServiceDescriptorTable.Base[eax];
    │    fn = fn_offset >> 4;      (位址低 4 bits 存參數個數）
    │
    ├─ ProbeForRead 驗證 user-mode 指標（避免傳進 kernel 位址）
    │
    ├─ 呼叫 nt!Nt<Xxx>(...)
    │
    ├─ 結果放進 rax（NTSTATUS）
    │
    └─ KiSystemCallExit → sysret → 回 ring 3
```

**參數個數的 trick**：SSDT 的每個 entry 不只存函式指標，還在低 4 bits 存「堆疊參數個數」（超過 4 個暫存器參數時需要從 user-mode stack 複製）。這就是 `fn_offset >> 4` 的原因。

**SSDT 和 W^X**：現代 Windows 的 SSDT 本身是唯讀的（PatchGuard 保護）。任何修改 SSDT 的行為（舊式 rootkit 手法）都會被 PatchGuard 偵測並 BSOD（`0x109: CRITICAL_STRUCTURE_CORRUPTION`）。這把舊的 SSDT hooking rootkit 技法封死了。

## 對比與取捨

### Windows vs Linux syscall 機制對照

| 面向 | Windows x64 | Linux x64 |
|---|---|---|
| 快速系統呼叫指令 | `syscall` | `syscall` |
| 舊式路徑 | `int 0x2e` | `int 0x80`（x86 only）|
| 參數傳遞 | r10, rdx, r8, r9（前 4），stack（後續）| rdi, rsi, rdx, r10, r8, r9（最多 6）|
| syscall number | eax（SSN，不穩定）| rax（syscall nr，穩定）|
| 返回值 | rax（NTSTATUS）| rax（負數為 errno）|
| Kernel entry | `IA32_LSTAR`（`KiSystemCall64`）| `IA32_LSTAR`（`entry_SYSCALL_64`）|
| syscall table | SSDT（PatchGuard 保護，唯讀）| sys_call_table（也有保護，但不同機制）|
| 號碼穩定性 | 無保證（每個 build 可變）| ABI 凍結，LTS 保證 |
| Hook 機制 | ntdll inline hook（user-mode）| seccomp、ptrace（kernel-mode）|

### syscall 版本漂移：各種解法

| 解法 | 方法 | 優缺點 |
|---|---|---|
| 透過 ntdll 呼叫 | 讓 ntdll 帶正確 SSN | 標準方式，受 hook 影響 |
| Hell's Gate | 動態掃 ntdll 記憶體取 SSN | 無 hardcode，但 hook 可干擾 |
| Halo's Gate | 從鄰近函式推 SSN | 對抗 hook，較複雜 |
| Tartarus' Gate | 掃 ntdll 磁碟映像（未被 hook）| 繞過記憶體 hook |
| FreshyCalls/SysWhispers | 排序 Nt* 取 SSN（排序規律）| 特定版本有排序規律 |

## 踩雷集錦

1. **「SSN 跨版本穩定，我直接 hardcode 就好」**：這是 direct syscall 工具最常見的 bug。SSN 0x18 今天對，明天 Windows Update 後可能變 0x19 或 0x1a。要麼動態取，要麼每次 build 更新時重新驗。

2. **「direct syscall 一定比 ntdll 快」**：不一定。省掉的是幾個 function call 的 overhead，但 `syscall` 指令本身是固定的 CPU 指令，沒快多少。更重要的是 direct syscall 主要目的是**繞 hook**，不是效能最佳化。

3. **「`int 0x2e` 路徑只是舊 Windows 的東西，現代不重要」**：不對。`int 0x2e` 在現代 malware 裡有被用來規避只監控 `syscall` 指令的安全工具。只要 KSD+0x308 的 bit 0 被設成 1（可以讀 KSD 但不能寫，這個 bit 是 kernel 設的），就走 `int 0x2e`。

4. **「indirect syscall 完全安全，不會被偵測」**：錯。Indirect syscall 讓 `syscall` 的 RIP 在 ntdll，通過「RIP 不在 ntdll」的檢查，但 call stack 回溯和 ETW-TI 還是能抓到異常。沒有一種技術在所有 EDR 面前都完全透明。

5. **「SSDT 可以在 user-mode 直接讀」**：不行，SSDT 在 kernel 記憶體裡（`ntoskrnl` 的資料段），user-mode 沒有存取權。你能做到的是從 ntdll 的 stub 讀 SSN，而不是直接查 SSDT。

## 進階：再往深一層

**PatchGuard（Kernel Patch Protection）**：Windows Vista 64 位元起引入，定期掃描 SSDT、IDT、MSR（包含 LSTAR）等關鍵 kernel 結構是否被修改，發現就 BSOD（bug check 0x109）。這讓 kernel-mode SSDT hook 成為歷史，現代 rootkit 必須繞 PatchGuard 才能做類似的事，技術難度提升了一個量級。

**`IA32_LSTAR` MSR**：`rdmsr 0xC0000082` 在 kernel 模式可讀，讀到的就是 `KiSystemCall64` 的 VA。在 hypervisor 層面可以截取這個 MSR，這是某些虛擬化 EDR 的攔截點。

**`sysret` 的一個老漏洞（CVE-2012-0217）**：`sysret` 指令在 Intel 和 AMD 的行為有細微差異。Intel 的 `sysret` 在設 RFLAGS 之前會先切換到 ring 3，而 AMD 反過來。這在特定條件（非 canonical 位址）下可以讓 `sysret` 的 GP fault 在 ring 0 觸發，導致提權。這個漏洞在 2012 年被修，但展示了 syscall 機制本身的微妙之處。

## 動手練習

用 Python + capstone 對本機的 ntdll 做以下分析：

1. 枚舉所有以 `Nt` 開頭的導出函式，找出哪些是「標準 syscall stub」（開頭是 `4c 8b d1 b8`），哪些**不是**（輸出它們的反組譯）。
2. 對標準 stub，按 SSN 排序，印出完整的 SSN 對照表。
3. 對照 j00ru 的 syscall table，驗證你本機的 SSN 和 Win11 build 26200 的對應關係是否一致。
4. **進階**：嘗試寫一個函式，模擬 Hell's Gate：給定函式名，動態取 SSN。

## 本章重點整理

- `Nt*` stub 的 24 bytes：`mov r10,rcx`（保存參數）→ `mov eax,SSN`（載入系統呼叫號）→ `test [KSD+0x308],1`（決定路徑）→ `syscall`（快速路徑）或 `int 0x2e`（舊式路徑）。
- SSN 由 SSDT 的索引決定，隨每個 Windows build 重新排列，沒有跨版本穩定性保證——對照 Linux syscall number 的 ABI 凍結。
- Direct syscall 繞過 ntdll hook，但 kernel callback 和 ETW-TI 可以從 RIP、call stack 偵測到異常。
- Hell's Gate / Halo's Gate 是動態取 SSN 的技術——掃 ntdll 記憶體找 `4c 8b d1 b8` pattern 提取 SSN，應對 hook 干擾。
- SSDT 在 kernel 記憶體，PatchGuard 保護，user-mode 無法直接讀或改，只能從 ntdll stub 的 `mov eax, SSN` 推斷。

## 自我檢核

- [ ] 不看筆記，能背出 `Nt*` stub 的 8 個指令並說出每個的作用
- [ ] 能解釋「為什麼 `mov r10, rcx` 必須在 `syscall` 之前」（CPU 行為面）
- [ ] 面試被問「為什麼 Windows 的 SSN 不穩定而 Linux 的穩定」，能從設計哲學角度回答
- [ ] 能解釋 Hell's Gate 和 Halo's Gate 的差異，以及各自在什麼條件下使用
- [ ] 能說出偵測 direct syscall 的至少兩種防禦方法，並分析 indirect syscall 為何能過其中一種
- [ ] 知道 `KSD+0x308` 的 bit 0 決定什麼，以及 `0x7ffe0000` 是誰 map 的、為何可讀不可寫

## 延伸閱讀

### 官方文件 / 規格

- **[AMD64 Architecture Programmer's Manual, Vol. 2 — System Programming（syscall/sysret 指令）](https://www.amd.com/content/dam/amd/en/documents/processor-tech-docs/programmer-references/24593.pdf)**
  - **讀哪裡**：Section 6.1「SYSCALL/SYSRET Instructions」，精讀 CPU 做了什麼（LSTAR、STAR、SFMASK MSR）
  - **和本章的關聯**：本章說「CPU 做 X」的底層依據都在這裡；和 Intel SDM Vol. 3 的 `SYSCALL` 指令對照看可以發現細微差異

### 研究與工具

- **[j00ru — Windows x64 System Call Table（所有版本 SSN 對照）](https://j00ru.vexillium.org/syscalls/nt/64/)**
  - **讀哪裡**：直接用來驗證本章練習的 SSN；觀察哪些函式的 SSN 最穩定、哪些最常漂移
  - **和本章的關聯**：最直觀的「SSN 漂移」實證資料

- **[Hell's Gate（原始論文/PoC）— vx-underground](https://vxug.fakedoma.in/papers/VXUG/Exclusive/HellsGate.pdf)**
  - **讀哪裡**：前半的動機與原理；看 C 實作裡怎麼掃 ntdll 記憶體
  - **和本章的關聯**：Hell's Gate 的第一手資料，本章概念直接取自這裡
  - **前提**：x64 彙編基礎（本課讀者具備）

- **[SysWhispers3（GitHub）](https://github.com/klezVirus/SysWhispers3)**
  - **讀哪裡**：README 的「How it works」；`generate.py` 裡 SSN 的決定邏輯
  - **和本章的關聯**：direct/indirect syscall 的現代工具實作，看真實程式碼

### 部落格

- **[j00ru — Revisiting Windows Syscalls（vexillium.org）](https://j00ru.vexillium.org/)**
  - **讀哪裡**：任何標題含 syscall 的文章；尤其是 NtQuerySystemInformation 的用法
  - **和本章的關聯**：最深入的 Windows syscall 研究者，對 stub 格式有過多次演進的記錄

- **[Connor McGarr — Weaponizing Direct Syscalls（connormcgarr.github.io）](https://connormcgarr.github.io/)**
  - **讀哪裡**：direct syscall / indirect syscall 的攻防分析系列
  - **和本章的關聯**：本章「偵測 direct syscall」防禦側的實戰分析，RIP 不在 ntdll 的具體偵測方法
  - **前提**：本章讀完

→ [Ch 8 — Handle 與 Object Manager](./08-handle-object-manager.md)
