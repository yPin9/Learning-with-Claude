# Ch 46 — token stealing / EoP 原語概觀（天梯銜接章）

> **目標**：理解從 userland 走向 SYSTEM 的核心路徑——token stealing 的概念、named pipe impersonation、Potato 家族的共同原理，以及當 userland 路徑走不通時，如何銜接到 kernel exploit。學完本章你能畫出「服務帳號 → SYSTEM」的完整攻擊圖，並把本課整條 Windows pwn 天梯收束。

## 為什麼需要這個？

前兩章把 Windows 存取控制的模型（access token、SID、privileges、IL）和 UAC 的提升機制講清楚了。但那是「從 admin Medium IL → High IL」的路徑。

本章要講另一條路：**從非 admin 帳號、或服務帳號（IIS、SQL Server、Print Spooler）走到 SYSTEM**。

這條路線的典型情境是：

```
你拿到了一台 Windows 機器的 shell，但：
  - 不是 admin 帳號（是 IIS AppPool\DefaultAppPool 或類似的服務帳號）
  - 或雖然是 admin，但在 Medium IL，UAC bypass 被偵測阻擋

目標：變成 NT AUTHORITY\SYSTEM（token 換成 S-1-5-18）
```

這是 Windows 提權的日常——並不是每次都需要 kernel exploit。userland 有幾條成熟的路線，其中最重要的是 **token stealing（token 竊取）** 和 **Potato 家族（SeImpersonatePrivilege 濫用）**。

## 先建立直覺

```
提權路線地圖（userland 部分）：

  你的行程（服務帳號 / 低權限）
       ↓
  評估持有哪些 Privileges：

  有 SeDebugPrivilege？
    → 找 SYSTEM 行程，OpenProcess + 複製 token → token stealing

  有 SeImpersonatePrivilege？
    → Potato 家族：強制 SYSTEM 連到你的 pipe，偷它的 token

  有 SeBackupPrivilege / SeRestorePrivilege？
    → 讀/寫任意檔案（SAM、SYSTEM hive → 離線讀出 hash → 橫向移動）

  以上都沒有？
    → 找本機提權漏洞（kernel exploit / 服務漏洞）
    → 這是 windows_kernel_driver 課的範疇
```

## Part 1：Token Stealing（token 竊取）

### 概念：複製別人的 token

Token stealing 的核心很直覺：

1. 找到一個以 SYSTEM 身份跑的行程（幾乎所有 Windows 服務都是）
2. 拿到那個行程的 handle（需要 `PROCESS_QUERY_INFORMATION` 或更高的存取權）
3. 從 handle 拿到它的 token handle（`OpenProcessToken`）
4. 複製這個 token（`DuplicateTokenEx`）
5. 用複製的 token 建立新行程（`CreateProcessWithTokenW`）或讓目前執行緒偽裝（`ImpersonateLoggedOnUser`）

```
  你的行程（Medium IL / 服務帳號）
        │
        │  OpenProcess(SYSTEM 行程 PID, PROCESS_QUERY_INFORMATION)
        ↓
  SYSTEM 行程 handle
        │
        │  OpenProcessToken(handle, TOKEN_DUPLICATE)
        ↓
  SYSTEM 行程的 token handle
        │
        │  DuplicateTokenEx(..., SecurityImpersonation, TokenPrimary, ...)
        ↓
  複製的 SYSTEM token（Primary level）
        │
        │  CreateProcessWithTokenW(copied_token, cmd.exe)
        ↓
  一個以 SYSTEM token 跑的 cmd.exe
```

**限制**：`OpenProcess` 需要 `SeDebugPrivilege`（對 SYSTEM 行程），或目標行程的 DACL 允許你。普通 Medium IL 行程不能直接 `OpenProcess` 對 lsass/winlogon 這種高完整性行程。這就是為什麼 token stealing 在 userland 的前提是**已有** `SeDebugPrivilege`（通常在已提權到 High IL 的 admin 帳號後才有）。

### Python 示範：讀出所有行程的 token 使用者（實跑）

以下腳本**不做 token 複製**，只列出每個行程的 token SID，展示哪些行程是 SYSTEM 身份：

```python
# 真實可跑（需要以系統管理員執行的 Python，才能 OpenProcess 更多行程）
import ctypes
import ctypes.wintypes as wt

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008

KERNEL32 = ctypes.WinDLL("kernel32")
ADVAPI32 = ctypes.WinDLL("advapi32")

class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize",              wt.DWORD),
        ("cntUsage",            wt.DWORD),
        ("th32ProcessID",       wt.DWORD),
        ("th32DefaultHeapID",   ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID",        wt.DWORD),
        ("cntThreads",          wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase",      ctypes.c_long),
        ("dwFlags",             wt.DWORD),
        ("szExeFile",           ctypes.c_char * 260),
    ]

def sid_to_str(sid_ptr):
    buf = ctypes.c_wchar_p()
    ADVAPI32.ConvertSidToStringSidW(sid_ptr, ctypes.byref(buf))
    result = buf.value
    KERNEL32.LocalFree(buf)
    return result

# 快照所有行程
snap = KERNEL32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
pe = PROCESSENTRY32()
pe.dwSize = ctypes.sizeof(PROCESSENTRY32)

system_sid = "S-1-5-18"
results = []

if KERNEL32.Process32First(snap, ctypes.byref(pe)):
    while True:
        pid = pe.th32ProcessID
        name = pe.szExeFile.decode("utf-8", errors="replace")

        hProc = KERNEL32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if hProc:
            hToken = wt.HANDLE()
            ok = ADVAPI32.OpenProcessToken(
                hProc, TOKEN_QUERY, ctypes.byref(hToken)
            )
            if ok:
                # TOKEN_INFORMATION_CLASS = 1 (TokenUser)
                needed = wt.DWORD()
                ADVAPI32.GetTokenInformation(
                    hToken, 1, None, 0, ctypes.byref(needed)
                )
                buf = ctypes.create_string_buffer(needed.value)
                if ADVAPI32.GetTokenInformation(
                    hToken, 1, buf, needed.value, ctypes.byref(needed)
                ):
                    # TOKEN_USER: SID_AND_ATTRIBUTES，頭 8 bytes 是 SID 指標
                    sid_ptr = ctypes.cast(
                        ctypes.c_char_p(buf.raw[:8]),
                        ctypes.POINTER(ctypes.c_void_p)
                    ).contents.value
                    if sid_ptr:
                        sid_addr = ctypes.cast(sid_ptr, ctypes.c_void_p)
                        try:
                            sid_str = sid_to_str(sid_addr)
                            is_sys = "*** SYSTEM ***" if sid_str == system_sid else ""
                            results.append((pid, name, sid_str, is_sys))
                        except Exception:
                            pass
                KERNEL32.CloseHandle(hToken)
            KERNEL32.CloseHandle(hProc)

        if not KERNEL32.Process32Next(snap, ctypes.byref(pe)):
            break

KERNEL32.CloseHandle(snap)

print(f"{'PID':<8} {'Name':<30} {'SID':<45} {'Note'}")
print("-" * 100)
for pid, name, sid_str, note in sorted(results, key=lambda x: x[0]):
    print(f"{pid:<8} {name:<30} {sid_str:<45} {note}")
```

以系統管理員執行後，你會看到哪些行程是 `S-1-5-18`（SYSTEM）——這些就是 token stealing 的候選目標。

### Kernel 層的 Token Stealing

這是 Ch 46 和 `windows_kernel_driver` 課的銜接點。Userland 的 token stealing 需要 `OpenProcess` 的存取權，而 kernel exploit 可以直接操作核心記憶體，不受 DACL 限制：

```
  Kernel exploit 拿到任意讀寫原語
        ↓
  遍歷 EPROCESS 鏈表（EPROCESS.ActiveProcessLinks）
        ↓
  找到 System（PID=4）的 EPROCESS.Token
        ↓
  把 System 的 Token 欄位值
  複製寫進自己的 EPROCESS.Token 欄位
        ↓
  你的行程的 primary token 變成 SYSTEM token
  → 任意 Win32 API 呼叫都以 SYSTEM 身份執行
```

這就是 Windows kernel exploit 的「標準收尾動作」——幾乎所有 Windows kernel EoP 的末尾都是這幾步（EternalBlue、CVE-2021-34527（PrintNightmare）的 kernel path 等）。Kernel token stealing 直接繞過了所有 userland 的存取控制。

## Part 2：Named Pipe Impersonation

Named pipe impersonation 是一個相對古老但在服務帳號場景仍然有效的技術，原理是利用 Windows 的 IPC 偽裝機制。

```
  你的行程（服務帳號，有 SeImpersonatePrivilege）
        │
        │  CreateNamedPipe("\\\\.\\pipe\\mypipe", ...)
        ↓
  建立一個 named pipe server，等待連線
        │
        │  （誘騙或等待 SYSTEM 行程連過來）
        ↓
  ConnectNamedPipe()  ← SYSTEM client 連進來
        │
        │  ImpersonateNamedPipeClient()
        ↓
  當前執行緒的 impersonation token 變成 SYSTEM token
        │
        │  CreateProcessWithToken(thread_token, cmd.exe)
        ↓
  SYSTEM 的 cmd.exe
```

**關鍵**：`ImpersonateNamedPipeClient` 的前提是呼叫方有 `SeImpersonatePrivilege`。IIS、SQL Server、Print Spooler 的服務帳號（`NT AUTHORITY\NETWORK SERVICE`、`IIS AppPool\...`）通常**預設有這個 privilege**——因為服務需要它才能替客戶端做 impersonation。

難點是「如何讓 SYSTEM 行程連到你的 pipe」。Potato 家族解決的正是這個問題。

## Part 3：Potato 家族——SeImpersonatePrivilege 的系統性利用

「Potato」系列工具是 Windows 服務帳號提權的代名詞，每代利用不同的 Windows 機制強制 SYSTEM 行程連到攻擊者的 named pipe，觸發 impersonation。全系列的共同前提：**持有 `SeImpersonatePrivilege` 或 `SeAssignPrimaryTokenPrivilege`**。

```
Potato 家族演進：

RottenPotato（2016）     Token Kidnapping 的自動化；
                         CLSID → DCOM Server → OXID Resolver
                         → NTLM 認證中繼到 named pipe → Impersonate
                         （Win10 / Server 2019 起被修補）

JuicyPotato（2018）      RottenPotato 的泛化，允許指定不同的 CLSID
                         （不同系統帳號有不同可用的 CLSID）
                         （Win10 1809+ / Server 2019 起 OXID 限制收緊，受影響）

PrintSpoofer（2020）     改用 Print Spooler 服務（SpoolSS pipe）
                         強制 SYSTEM 連到假的 named pipe
                         SeImpersonatePrivilege → SYSTEM
                         （itm4n 提出，利用 SpoolSS pipe 的特性）

RoguePotato（2020）      在 JuicyPotato 被修後的替代方案
                         利用 OXID Resolver 的 customized remote resolver
                         繞過 CLSID 限制

GodPotato（2023）        利用 ITaskScheduler COM interface
                         Windows 8.1 – Windows 11 全覆蓋
                         目前（2026）有效範圍最廣的 Potato 之一
```

### 共同原理（以 PrintSpoofer 為例）

```
  攻擊者行程（服務帳號，有 SeImpersonatePrivilege）
       ↓
  1. 建立 named pipe：\\.\pipe\foo\pipe\spoolss
     （spoolss 是 Print Spooler 用的 pipe 名稱，
       系統允許在使用者目錄下建立這個路徑）

  2. 呼叫 RpcOpenPrinter("\\.\pipe\foo")
     → 觸發 Print Spooler 服務嘗試連到這個路徑的 spoolss pipe

  3. Print Spooler 以 SYSTEM 身份連進攻擊者的 named pipe
     → ConnectNamedPipe() 返回

  4. ImpersonateNamedPipeClient()
     → 當前執行緒拿到 SYSTEM impersonation token

  5. CreateProcessWithToken(SYSTEM_token, cmd.exe)
     → SYSTEM shell
```

**為什麼合法**：整個流程沒有利用任何「漏洞」——它完全在 Windows API 的設計範圍內。`SeImpersonatePrivilege` 本來就是設計來讓服務偽裝的；Print Spooler 連到 named pipe 也是設計內。只是**攻擊者把這些合法機制組合起來**，達到非預期的提權效果。

這正是為什麼 Microsoft 不把 Potato 系列視為需要修補的漏洞——它們利用的是「設計特性」，不是「實作缺陷」。修補的方式是在部署時**不給服務帳號 `SeImpersonatePrivilege`**，或使用 Windows 的 Managed Service Accounts / Group Managed Service Accounts（gMSA），它們的權限被系統管理。

### 防禦偵測角度

```
偵測 Potato 類攻擊的指標（IOC）：

行程建立：
  - 低權限服務帳號（AppPool / NetworkService）
    突然生出 cmd.exe / powershell.exe / 其他 shell 行程
  - 父行程（cmd.exe）的 token 使用者是 NT AUTHORITY\SYSTEM
    但父行程的父行程是服務帳號

Named Pipe：
  - Sysmon EventID 17（PipeCreated）：
    非標準路徑的 named pipe 以服務帳號建立
  - 路徑含 "pipe\spoolss"、"pipe\epmapper" 等系統 pipe 名稱
    但 Creator 是非系統帳號

Token 操作：
  - Sysmon EventID 10（ProcessAccess）：
    低權限行程對 spoolsv.exe / svchost.exe 以 PROCESS_DUP_HANDLE 存取
  - 或行程突然出現 SYSTEM SID 的 token
    但 parent 行程鏈沒有 SYSTEM 行程
```

## Part 4：Token Kidnapping（歷史背景）

在 RottenPotato 之前，Cesar Cerrudo 在 2008 年提出 Token Kidnapping：透過 Windows 的 token 複製機制，從模擬 token 建立 primary token，讓低權限行程能以 SYSTEM 跑新行程。Vista/Server 2008 以前未修補的版本上可行。

Token Kidnapping 在現代 Windows 上不直接可用，但它的核心洞察——**`SeImpersonatePrivilege` 讓持有者能偽裝任何連到它的 client**——啟發了後來所有 Potato 的設計。

## Part 5：SeBackupPrivilege / SeRestorePrivilege 路線

如果你的服務帳號屬於 Backup Operators 群組（或明確被賦予這兩個 privilege），提權路線不同：

```
  有 SeBackupPrivilege + SeRestorePrivilege
        ↓
  繞過 DACL 讀寫任意檔案
        ↓
  讀取 SYSTEM registry hive：
    reg save HKLM\SYSTEM C:\temp\sys.hive
    reg save HKLM\SAM   C:\temp\sam.hive
        ↓
  離線用 impacket / secretsdump 抽出 NTLM hash
        ↓
  Pass-the-Hash → 以 Administrator 登入
```

或者更直接：

```
  有 SeRestorePrivilege
        ↓
  覆寫 DLL 搜尋路徑優先的系統 DLL
  （以 SYSTEM 跑的服務會載入它）
        ↓
  服務重啟 → DLL 以 SYSTEM 執行
        ↓
  SYSTEM shell
```

這條路線需要能重啟服務或等待排程，但不需要 `SeImpersonatePrivilege`——在某些限制環境下是替代路線。

## 銜接 Kernel：當 Userland 走不通時

上面三條路線（token stealing、named pipe / Potato、backup privilege）加上 UAC bypass，已經涵蓋了 Windows 提權的大多數日常場景。但以下情況 userland 路線會走不通：

```
走不通的情境：
  - 標準使用者帳號（non-admin），沒有任何高風險 privilege
  - 服務帳號被 Windows 的 Managed Service Account 管理，
    `SeImpersonatePrivilege` 已被移除
  - 系統有 Credential Guard / VBS 開著，
    LSASS 在 trustlet 裡，普通 handle 拿不到 token
  - 目標是 PPL（Protected Process Light）行程，
    連 SeDebugPrivilege 都打不進去

→ 這時需要 kernel exploit：
  從 kernel 層直接讀寫 EPROCESS.Token，
  繞過所有 userland 存取控制
```

Kernel exploit 拿到 SYSTEM 的標準步驟：

```
  1. 利用 kernel 漏洞拿到任意讀寫原語
     （堆溢位 / UAF / OOB 讀寫 → pool 控制）

  2. 遍歷 EPROCESS 鏈表找到 System 行程（PID=4）
     ps_initial_system_process 或從已知 EPROCESS 往前走

  3. 讀出 System 的 _TOKEN 指標（EPROCESS.Token，EX_FAST_REF）
     低 4 bit 是引用計數，清掉再用

  4. 把這個 token 指標寫進自己的 EPROCESS.Token 欄位

  5. 從 kernel 返回後，呼叫任意 Win32 API
     都以 SYSTEM token 執行
```

> **這是 windows_kernel_driver 課的主要內容**：`EPROCESS.Token` 竊取、pool 溢位、UAF 在 NonPagedPool、kernel shellcode 的設計——本課在此點到為止，不重複那門課的深度。你現在已經知道「為什麼」kernel exploit 的最後一步是改 token。

## 對比與取捨

| 路線 | 前提條件 | 難度 | 對應 Linux |
|---|---|---|---|
| Token stealing（userland）| SeDebugPrivilege（High IL admin）| 低 | ptrace + /proc/pid/status 讀 uid |
| Named Pipe Impersonation | SeImpersonatePrivilege | 中 | 無直接對應 |
| Potato 家族 | SeImpersonatePrivilege | 低（工具成熟）| 無直接對應 |
| Backup Privilege 路線 | SeBackupPrivilege + SeRestorePrivilege | 中 | CAP_DAC_READ_SEARCH |
| Kernel Token Stealing | kernel 任意讀寫 | 高 | kernel exploit → 寫 task_struct.cred.uid=0 |

Linux 對照：
- Linux kernel EoP 也是改 `task_struct.cred`（把 uid/gid 清零），和 Windows 改 `EPROCESS.Token` 邏輯相似但結構不同
- Linux 沒有 SeImpersonatePrivilege 等價物，Potato 類攻擊在 Linux 不存在
- Linux 的 SUID binary 提權（`find / -perm -u=s` 找漏洞的 SUID）在 Windows 對應的是「以 SYSTEM 跑且有漏洞的服務」

## 整條 Windows Pwn 天梯回顧

走到這裡，整門課的天梯已經爬完。回顧一下你走過的路：

```
Part 0 — 定位與環境
  環境搭建 → Windows pwn vs Linux pwn 的差異 → 心智模型遷移

Part 1 — Windows 執行環境 Internals
  PE 格式 → Loader / LDR → PEB / TEB → Win32 vs Native API
  → syscall 機制 → Handle / Object → 虛擬記憶體 → 行程建立
  → SEH x86 / x64 → 符號與逆向工具

Part 2 — Windows Heap Internals
  NT Heap → LFH → Segment Heap → metadata encoding → WinDbg 觀測

Part 3 — 基礎 Userland Exploitation
  Stack overflow（x86）→ /GS cookie → SEH overwrite → SEHOP
  → DEP + ROP → ASLR / leak / 部分覆寫 → Windows shellcode

Part 4 — Heap Exploitation
  Heap overflow 原語 → UAF → LFH grooming → Segment Heap 技法
  → C++ vtable 劫持 → info leak

Part 5 — 現代緩解與對抗（重頭戲）
  CFG → CFG bypass → XFG → CET / shadow stack
  → ACG / CIG / code integrity → data-only attacks
  → EMET → WDEG 演進 → 緩解總表與決策樹

Part 6 — 真實環境與找洞
  x64 ABI → WinDbg TTD → WinAFL fuzzing → Patch Tuesday diffing

Part 7 — 天梯銜接：碰一點提權（本章）
  Access token 模型 → UAC / IL 繞過 → Token stealing / EoP 原語
         ↓
  windows_kernel_driver 課：
  EPROCESS token 竊取（kernel 層）/ pool corruption / BYOVD / Anti-EDR / VBS / HVCI
```

這門課的起點是「你已經會 Linux userland pwn」，終點是「Windows userland exploit 的完整地基 + 提權銜接」。你現在具備了進入 Windows kernel 研究的正確準備——知道 token 是什麼、EPROCESS 結構在哪、kernel exploit 最後一步要改什麼。

## 踩雷集錦

1. **「Potato 在任何 Windows 版本都能用」**：不對。RottenPotato / JuicyPotato 在 Windows 10 1809+ / Server 2019 後因 OXID resolver 的限制而受影響；PrintSpoofer 在 Print Spooler 服務被關閉或修補後失效；GodPotato（2023）是目前覆蓋範圍最廣的，但不保證未來版本。永遠先確認目標 OS 版本再選工具。

2. **「有 SeImpersonatePrivilege 就一定能提權」**：幾乎是。但如果系統用了 Managed Service Account 且 Print Spooler / Task Scheduler 等服務被停用，Potato 的強制連線可能找不到可利用的觸發點。需要有備案。

3. **「Token stealing = 複製 token handle」**：不只是 handle。`DuplicateTokenEx` 是複製 token **物件**，不只是 handle。複製出來的 token 是完整的獨立物件，改動不影響原始 token。和 Linux 的 `fork` 後 `setuid` 是不同的思路。

4. **「Named pipe impersonation = 拿到 SYSTEM token 就結束了」**：impersonation token 在執行緒退出或 `RevertToSelf` 後就失效。要持久化，需要用 impersonation token 建一個新的 primary token 行程（`CreateProcessWithToken`），或在 impersonation 狀態下做後續操作（寫 persistence、加 registry run key 等）。

5. **「改了 EPROCESS.Token 後系統就完全穩定」**：Kernel token stealing 後行程確實能以 SYSTEM 跑，但這個操作沒有正確增加 token 物件的引用計數——技術上是 use-after-free 的邊界狀態。穩定版的 kernel EoP 要正確處理 `EX_FAST_REF` 的引用計數，否則系統可能在 token 物件被回收時崩潰。

## 進階：再往深一層

**SeCreateTokenPrivilege**：這個 privilege 允許從頭建立任意 token，不需要先找到 SYSTEM 行程。`NtCreateToken` syscall 直接傳入你想要的 SID 清單和 privileges，建出一個完整的 SYSTEM token。問題是幾乎沒有行程持有這個 privilege（只有 LSASS）——拿到它通常本身就已經是提權完成的標誌。

**Credential Guard / VBS 下的限制**：Virtual-Based Security 把 LSASS 放進 Secure World（VTL 1），普通 kernel 甚至拿不到 LSASS 的 token——因為 LSASS 跑在另一個 trustlet 裡，`EPROCESS.Token` 在 VTL 0 的 kernel 看到的是代理物件，不是真正的 LSASS token。研究 VBS 下的提權是目前 Windows 安全研究的前沿，涉及 Hypervisor / VTL 的邊界研究。

**面試題**：「一個 IIS AppPool 服務帳號（Medium IL，有 `SeImpersonatePrivilege`）在沒有任何 kernel exploit 的情況下能提權到 SYSTEM 嗎？」——能，這正是 Potato 家族的設計情境。GodPotato 在現代 Windows 11 上（Print Spooler 服務開著）通常可行。答題時要提 `SeImpersonatePrivilege` + named pipe impersonation + Print Spooler / Task Scheduler 觸發這三個元素。

## 動手練習

**目標**：在自己的機器上建立一個 low-privilege 情境，觀察 token 行為（不實際提權）。

步驟：
1. 建一個標準帳號（控制台 → 使用者帳號 → 新增）
2. 以標準帳號登入，跑 `whoami /priv`，確認沒有 `SeImpersonatePrivilege` 和 `SeDebugPrivilege`
3. 切換回 admin 帳號，查看 IIS Application Pool 的服務帳號（若有 IIS）或建立一個假的服務帳號（`New-LocalUser -Name "TestSvc"` + 給它 `SeImpersonatePrivilege`）
4. 以那個帳號開啟 PowerShell，跑 `whoami /priv`，確認 `SeImpersonatePrivilege` 存在
5. 查閱 PrivescCheck（itm4n GitHub）的工具說明，了解它怎麼自動枚舉這些提權路徑

這個練習的目的是**建立「看到 SeImpersonatePrivilege 就知道有 Potato 路線」的直覺**，而不是執行提權——在自己的測試環境理解流程，在授權的滲透測試中才使用工具。

## 本章重點整理

- Token stealing 的核心是找 SYSTEM 行程、複製其 token、以複製的 token 建新行程；userland 版需要 `SeDebugPrivilege`，kernel 版直接改 `EPROCESS.Token` 繞過一切。
- Named pipe impersonation + `SeImpersonatePrivilege` 是服務帳號提權的標準路線；Potato 家族解決的是「如何讓 SYSTEM 行程自己走進你的 pipe」。
- Potato 系列（GodPotato 為現代主力）不利用漏洞，利用的是 Windows 服務偽裝機制的設計特性——修補方式是不給服務帳號 `SeImpersonatePrivilege`，或停用相關系統服務。
- 當 userland 路線因為沒有高風險 privilege 或 VBS 保護而受阻，下一步是 kernel exploit + `EPROCESS.Token` 竊取——那是 `windows_kernel_driver` 課的主線。

## 自我檢核

- [ ] 不看筆記，能畫出「服務帳號 + SeImpersonatePrivilege → SYSTEM」的完整步驟（Named pipe → ImpersonateNamedPipeClient → CreateProcessWithToken）
- [ ] 能說出 RottenPotato / JuicyPotato / PrintSpoofer / GodPotato 各自利用的是哪個 Windows 服務或機制
- [ ] 被問「沒有 SeImpersonatePrivilege，有哪些 userland 路線可以試」——能說出兩條（SeDebugPrivilege + token stealing、SeBackupPrivilege + SAM dump）
- [ ] 能解釋 kernel token stealing（改 EPROCESS.Token）為什麼比 userland 版強，以及有什麼穩定性風險
- [ ] 能把整門課的天梯（Part 0–7）用一句話描述每個 Part 的核心主題

## 延伸閱讀

### 官方文件

- **[Impersonation Levels — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/secauthz/impersonation-levels)**
  - **讀哪裡**：整篇（短），重點是 Identification vs Impersonation vs Delegation 的差異
  - **學什麼**：四種 impersonation level 的精確語意——「有 token」和「能用 token 做事」是不同的
  - **和本章關聯**：Ch 44 impersonation token 概念的延伸；理解為什麼偷到的 token level 決定能做什麼

- **[CreateProcessWithTokenW — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-createprocesswithtokenw)**
  - **讀哪裡**：Requirements 和 Remarks 兩節；特別注意「caller must have SE_IMPERSONATE_NAME privilege」
  - **學什麼**：用 token 建立行程的 API，以及它的前提 privilege——這就是為什麼 Potato 家族拿到 token 之後還需要 SeImpersonatePrivilege 才能開新行程

### 研究 / 部落格

- **[PrintSpoofer — Abusing Impersonation Privileges on Windows 10 and Server 2019（itm4n）](https://itm4n.github.io/printspoofer-abusing-impersonate-privileges/)**
  - **讀哪裡**：整篇；重點是「為什麼 JuicyPotato 在 Server 2019 不行」和 PrintSpoofer 怎麼用 SpoolSS pipe 繞過
  - **學什麼**：Potato 家族演進的關鍵節點；itm4n 是這個領域最重要的研究者之一，文章直接有觀點
  - **前提**：本章 Named pipe impersonation 概念

- **[GodPotato — Universal Potato（BeichenDream GitHub）](https://github.com/BeichenDream/GodPotato)**
  - **讀哪裡**：README 和 issue 討論；不要只看工具用法，看它利用的是哪個 COM interface（ITaskScheduler）
  - **學什麼**：2023 年之後覆蓋範圍最廣的 Potato 變體；理解它用的 Task Scheduler 觸發機制
  - **前提**：PrintSpoofer 原理

- **[PrivescCheck — Windows Privilege Escalation Enumeration Script（itm4n）](https://github.com/itm4n/PrivescCheck)**
  - **讀哪裡**：不是讀文章，是讀腳本的 check 邏輯——每個 check 函數都對應一個提權路線
  - **學什麼**：系統性的 Windows 提權枚舉方法；看別人怎麼把「有哪些 privilege → 有哪些路線」程式化
  - **前提**：本章 + Ch 44 + Ch 45 全讀

### 書籍 / 深度材料

- **《Windows Internals, 7th Edition》Part 1，Chapter 7（Security）**
  - **讀哪裡**：「Token Stealing」和「Impersonation」兩段；對照本章的概念圖
  - **學什麼**：`_TOKEN` 結構的完整欄位，以及 kernel 層 token 操作的精確 API 路徑
  - **前提**：本章是快速版；要研究 kernel token stealing 的細節（EX_FAST_REF 清位操作、引用計數）再來這本

→ [Final Project — Win11 x64 現代緩解全開下的 userland exploit chain](./final-project-windows-exploit-chain.md)
