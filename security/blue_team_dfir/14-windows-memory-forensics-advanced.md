# Ch 14 — 記憶體鑑識進階：注入與 hollowing 偵測

> 目標：給定一個可疑進程，能用 Volatility3 的 malfind、dlllist、ldrmodules、handles、netscan、cmdline 組合起來，判斷它是否有注入、hollowing、reflective loading；能說清楚每個 plugin 看到什麼記憶體結構，以及攻擊者的行為在那個結構上留下什麼痕跡。
>
> 環境：Volatility3 2.x；Windows 10/11 x64 memory image；建議先完成 Ch 13 並能正確跑 windows.pslist.PsList 和 windows.psscan.PsScan。

## 從進程列表到注入偵測的距離

Ch 13 教你找進程。找到可疑進程之後，你面對的問題是：「這個 `svchost.exe` 是合法的，還是有人在裡面塞了 shellcode？」

單純的進程名稱騙不了你（你已經知道 PPID spoofing 和 DKOM）。但進程「裡面」的記憶體狀態才是決定性的。攻擊者把程式碼注入合法進程的理由很直接：讓 C2 連線、程式碼執行看起來都來自可信任的進程（`svchost.exe`、`lsass.exe`、`explorer.exe`），繞過基於進程白名單的偵測。

這章的核心問題：**記憶體裡的什麼結構，讓注入行為無所遁形？**

## 注入技術回顧（紅隊視角轉防守）

你做過這些，現在我們看防守方怎麼抓：

### Classic DLL Injection（傳統 DLL 注入）

1. `OpenProcess` 拿到目標進程 handle
2. `VirtualAllocEx` 在目標進程分配記憶體
3. `WriteProcessMemory` 把 DLL 路徑寫進去
4. `CreateRemoteThread`（或 `NtCreateThreadEx`）讓目標進程跑 `LoadLibrary`

結果：目標進程的 PEB 的模組列表（`InLoadOrderModuleList`）裡會多一個 DLL。這個 DLL 有對應的磁碟檔案（雖然可能是惡意 DLL 假裝合法名稱放在 temp 目錄）。

### Process Hollowing（進程挖空）

1. 用 `CREATE_SUSPENDED` 建立一個合法進程（例如 `svchost.exe`）
2. 用 `NtUnmapViewOfSection` 把這個進程的原始 PE 從記憶體 unmap
3. 用 `VirtualAllocEx` + `WriteProcessMemory` 把惡意 PE 寫進同一個記憶體範圍
4. 修改 Thread Context 的 `Eip`/`Rip` 指向惡意 PE 的 entry point
5. `ResumeThread`

結果：進程名稱是 `svchost.exe`，但記憶體裡跑的是惡意 PE。原始的 `svchost.exe` PE 已從 PEB 模組列表移除，但那塊記憶體現在是 private（不映射到任何磁碟檔案）、RWX、且開頭是 MZ header。

### Reflective DLL Loading（反射載入）

不呼叫 `LoadLibrary`，惡意 DLL 自己實作一個 loader，從記憶體 blob 直接把自己映射起來。好處：不留 PEB 模組列表痕跡，沒有磁碟路徑。

結果：PEB 的 `InLoadOrderModuleList` 裡找不到這個 DLL，但 VAD 裡有一塊 private、RWX 的記憶體，開頭可能是 MZ，也可能已被 shellcode 蓋掉 MZ（為了混淆 malfind）。

### Shellcode Injection（直接注入 shellcode）

最簡單的形式：遠端 `VirtualAllocEx` + `WriteProcessMemory` 把 shellcode 寫進去，`CreateRemoteThread` 執行。沒有 PE 格式，沒有 MZ header，就是一塊執行中的 RWX 記憶體。

## VAD（Virtual Address Descriptor）——理解 malfind 的基礎

每個 Windows 進程的虛擬位址空間由 OS 用 **VAD tree** 管理。VAD tree 是一棵平衡二元樹（AVL tree），每個節點（`MMVAD` 結構體）描述一段連續的虛擬記憶體範圍：

```
MMVAD 結構（簡化）：
┌──────────────────────────────────────────┐
│ StartingVpn     起始虛擬頁號             │
│ EndingVpn       結束虛擬頁號             │
│ u.VadFlags                               │
│   ├─ PrivateMemory  1=private 0=mapped   │
│   ├─ Protection     保護屬性（RWX 組合）  │
│   └─ VadType        heap/stack/mapped/...│
│ Subsection      指向 mapped file 資訊    │
│ u2.VadFlags2                             │
│   └─ NoChange / ...                      │
│ LeftChild / RightChild  AVL 子節點        │
└──────────────────────────────────────────┘
```

當一個進程 `LoadLibrary` 一個 DLL，OS 會在 VAD tree 裡新增一個節點，type 是 mapped，指向磁碟上的 PE 檔（透過 `Subsection` 欄位）。

當攻擊者用 `VirtualAllocEx` 分配記憶體，OS 也在 VAD tree 新增節點，但 type 是 **private**，protection 是攻擊者指定的（`PAGE_EXECUTE_READWRITE` 就是 RWX），沒有磁碟檔案對應。

這就是 malfind 的原理：找 private + executable 的 VAD 節點，看裡面有沒有 PE 或 shellcode 特徵。

## windows.malfind.Malfind

```bash
python3 vol.py -f memdump.raw windows.malfind.Malfind
```

Malfind 掃描每個進程的 VAD tree，標記同時滿足這些條件的節點：

1. `PrivateMemory` = 1（不對應磁碟 mapped file）
2. Protection 包含 `PAGE_EXECUTE_*`（可執行，即 RWX 或 RX 或 EXECUTE_WRITECOPY 等）
3. VAD 節點起始的前幾個 byte 是 `MZ`（`4D 5A`）或符合 shellcode 的 pattern

輸出（示意輸出，實際依 image/版本而異）：

```
PID    Process     Start VPN          End VPN            Tag      Protection   CommitCharge  PrivVAD  File output

4872   svchost.exe 0x7ff8aa200000     0x7ff8aa20efff     VadS     PAGE_EXECUTE_READWRITE  15  True     Disabled
Bytes:
4d 5a 90 00 03 00 00 00 04 00 00 00 ff ff 00 00  MZ..............
b8 00 00 00 00 00 00 00 40 00 00 00 00 00 00 00  ........@.......
00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00  ................

4872   svchost.exe 0x1d3a0000         0x1d3aefff         VadS     PAGE_EXECUTE_READWRITE  239 True     Disabled
Bytes:
fc 48 83 e4 f0 e8 c0 00 00 00 41 51 41 50 52 51  .H........AQAPRQ
57 56 48 31 d2 65 48 8b 52 00 48 8b 52 18 48 8b  WVH1.eH.R.H.R.H.
...
```

第一個命中：MZ header 開頭，是一個 PE（可能是 hollowing 或 reflective 載入的 PE）。  
第二個命中：`fc 48 83 e4 f0` 是 Cobalt Strike Beacon 的 shellcode 特徵序列（`CLD` + `AND RSP,-0x10` + `CALL`），經典的 x64 shellcode 頭。

### Malfind 的限制

- 只找帶 MZ 或 malfind 定義的 pattern 的 private+exec 記憶體。如果攻擊者把 MZ header 的前兩個 byte 覆寫成其他值（`EP` 或 `4D 58`），malfind 認不出是 PE。
- 不是所有 private+exec 記憶體都是惡意的。JIT compiler（例如 Chrome V8、.NET CLR）也會分配 RWX 的 private 記憶體存放 JIT 編譯出來的機器碼。要結合進程名稱和 PPID 判斷。
- malfind 輸出要人工 triage，不能直接當 IOC 用。

### 把 malfind 的記憶體 dump 出來

```bash
python3 vol.py -f memdump.raw windows.malfind.Malfind --dump
```

加 `--dump` 會把每個命中的記憶體塊存成 `.dmp` 檔，然後你可以丟進 YARA 掃描、丟進 IDA/Ghidra 反組譯、或丟進 VirusTotal。

## PEB 模組列表：windows.dlllist.DllList

每個進程有一個 **PEB（Process Environment Block）** 存在 user-mode 空間。PEB 裡有三個雙向鏈結串列記錄已載入的模組（DLL）：

```
PEB.Ldr (PEB_LDR_DATA)
 ├─ InLoadOrderModuleList   （依載入順序）
 ├─ InMemoryOrderModuleList  （依記憶體位址順序）
 └─ InInitializationOrderModuleList （依初始化順序）
```

每個節點是 `LDR_DATA_TABLE_ENTRY`，包含 DLL 的 base address、大小、磁碟路徑、名稱。

`windows.dlllist.DllList` 走 `InLoadOrderModuleList` 列出每個進程載入的 DLL：

```bash
python3 vol.py -f memdump.raw windows.dlllist.DllList --pid 4872
```

輸出（示意輸出，實際依 image/版本而異）：

```
PID    Process     Base             Size      Name                Path
4872   svchost.exe 0x7ff8bc000000   0x1e4000  ntdll.dll           C:\Windows\SYSTEM32\ntdll.dll
4872   svchost.exe 0x7ff8ba200000   0x9f000   KERNEL32.DLL        C:\Windows\System32\KERNEL32.DLL
4872   svchost.exe 0x7ff8b9a00000   0x24b000  KERNELBASE.dll      C:\Windows\System32\KERNELBASE.dll
4872   svchost.exe 0x7ff8aa200000   0xf000    ???                 ???
...
```

`???` 名稱和路徑的 DLL 是一個高度可疑的訊號：它出現在 PEB 的模組列表裡（所以被 dlllist 列出），但沒有對應的磁碟路徑。這是 reflective DLL loading 後，DLL 自己把自己的 `LDR_DATA_TABLE_ENTRY.BaseDllName` 清空的結果。

反過來：如果一個 DLL 在 VAD 裡有（佔了一塊記憶體），但在 PEB 的 `InLoadOrderModuleList` 裡找不到——那是 **unlinked module**，更嚴重的警訊。

## VAD vs PEB 不一致：windows.ldrmodules.LdrModules

`windows.ldrmodules.LdrModules` 專門做這件事：比對 VAD 裡的映射記憶體 和 PEB 的三個模組列表，找不一致的地方。

```bash
python3 vol.py -f memdump.raw windows.ldrmodules.LdrModules --pid 4872
```

輸出（示意輸出，實際依 image/版本而異）：

```
PID    Process     Base             InLoad  InMem   InInit  MappedPath
4872   svchost.exe 0x7ff8bc000000   True    True    False   \Windows\System32\ntdll.dll
4872   svchost.exe 0x7ff8ba200000   True    True    True    \Windows\System32\kernel32.dll
4872   svchost.exe 0x7ff8aa200000   True    True    True    \Windows\System32\KERNELBASE.dll
4872   svchost.exe 0x1d3a0000       False   False   False   
```

最後一行：`InLoad`、`InMem`、`InInit` 全 False——這塊記憶體在 VAD 裡（OS 知道它存在），但完全不在 PEB 模組列表裡。這就是 unlinked module 的特徵，是 reflective loading 或 shellcode 的強力指標。

`ntdll.dll` 的 `InInit` 是 False 是正常的，因為 ntdll.dll 在系統初始化時特殊處理，不走 `InInitializationOrderModuleList`。記住這個正常 case，不要誤報。

## windows.handles.Handles

每個進程有一個 **handle table**，記錄它開啟的 kernel 物件的 handle（file、process、thread、registry key、event 等）。`windows.handles.Handles` 讀取 handle table：

```bash
python3 vol.py -f memdump.raw windows.handles.Handles --pid 4872
```

輸出（示意輸出，實際依 image/版本而異）：

```
PID    Process     HandleValue  Type          GrantedAccess  Name
4872   svchost.exe 0x4          Process       0x1fffff       System (PID: 4)
4872   svchost.exe 0x8          Thread        0x1fffff       
4872   svchost.exe 0x18         File          0x100001       \Device\HarddiskVolume3\Windows\System32\svchost.exe
4872   svchost.exe 0x1c         Event         0x1f0003       
4872   svchost.exe 0x34         Process       0x1fffff       lsass.exe (PID: 756)
...
```

第四行：`svchost.exe` 對 `lsass.exe` 開了一個 `PROCESS_ALL_ACCESS`（`0x1fffff`）的 handle。`svchost.exe` 有什麼理由對 `lsass.exe` 持有完整存取權？沒有。這就是 credential dumping（或注入 lsass 的前置動作）的記憶體痕跡。

Handles 分析的重點：
- 不尋常的 process-to-process handle（高權限 handle 指向 lsass/csrss）
- 大量 File handle 指向臨時目錄或 unusual path
- Registry handle 指向 `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options`（常見的 persistence 點）

## windows.netscan.NetScan

記憶體裡有 OS 的 TCP/UDP 連線狀態。`windows.netscan.NetScan` 掃描記憶體找 `_TCP_ENDPOINT`、`_TCP_LISTENER`、`_UDP_ENDPOINT` 結構：

```bash
python3 vol.py -f memdump.raw windows.netscan.NetScan
```

輸出（示意輸出，實際依 image/版本而異）：

```
Offset(P)    Proto  LocalAddr        LocalPort  ForeignAddr      ForeignPort  State       PID    Owner       Created
0x890abc1234 TCPv4  192.168.1.100    49213      203.0.113.50     443          ESTABLISHED 4872   svchost.exe 2024-06-15 13:45:33
0x890abc5678 TCPv4  0.0.0.0          445        0.0.0.0          0            LISTEN      4       System
0x890abc9abc UDPv4  0.0.0.0          5353       *                0                        692    svchost.exe 2024-06-15 10:01:05
```

關鍵看第一行：PID 4872 的 `svchost.exe` 對外建立了 TCP 443 連線到 `203.0.113.50`。HTTPS 出去是正常行為，但你要問：這個 `svchost.exe` 的 parent 是誰（從 pstree 看）？它的 DLL 列表有沒有異常（dlllist）？它的記憶體有沒有 RWX private 塊（malfind）？

如果那個 svchost.exe 的記憶體裡有 Cobalt Strike shellcode 的 malfind 命中，加上對外 443 連線，就是一個完整的 C2 beacon 的記憶體證據鏈。

`windows.netscan.NetScan` 的侷限：它和 `windows.psscan.PsScan` 一樣做 pool scanning，對 pool tag `TcpE`/`UdpA` 等掃描。已關閉的連線的結構體可能還留在 pool 裡，輸出中 State 是空白或 `CLOSE_WAIT` 的，是已關閉的連線，不代表當下活著。

## windows.cmdline.CmdLine

```bash
python3 vol.py -f memdump.raw windows.cmdline.CmdLine
```

讀每個進程的 PEB 裡的 `ProcessParameters.CommandLine`。這就是進程的完整命令列，連 argument 都有。

輸出（示意輸出，實際依 image/版本而異）：

```
PID    Process         Args
4      System          Required memory at 0x20 is not valid (returned NotPresent)
4872   svchost.exe     C:\Windows\system32\svchost.exe -k netsvcs -p
1234   powershell.exe  powershell.exe -NoProfile -NonInteractive -EncodedCommand SQBFAFgAIAAoAE...
```

第三行：`-EncodedCommand` 是 Base64 編碼的 PowerShell，`IEX`（Invoke-Expression）加 encoded command 是 cradle 的標準形態，幾乎可以確定是惡意用途。這個 cmdline 留在記憶體裡，即使 powershell 已退出，用 PsScan 可能還能找到 EPROCESS，再加上 cmdline 就有完整執行上下文。

## 完整分析流程：從可疑進程到注入確認

把前面的 plugin 串起來，這是實戰的操作順序：

```
1. windows.pslist.PsList          → 列出所有進程，看 PPID、CreateTime
2. windows.psscan.PsScan          → 比對，找 unlinked 隱藏進程
3. windows.pstree.PsTree          → 看親子關係，發現 PPID 異常
                                    ↓
   發現可疑 PID（例如 4872 的 svchost.exe parent 是 cmd.exe）
                                    ↓
4. windows.malfind.Malfind --pid 4872   → 看有沒有 RWX private 記憶體 + MZ/shellcode
5. windows.dlllist.DllList --pid 4872   → DLL 列表，找 ??? 路徑或不尋常路徑
6. windows.ldrmodules.LdrModules --pid 4872 → VAD vs PEB 不一致
7. windows.handles.Handles --pid 4872   → 有沒有持有 lsass 的高權限 handle
8. windows.netscan.NetScan         → 這個 PID 有沒有外連
9. windows.cmdline.CmdLine         → 命令列有沒有 encoded/obfuscated 參數
                                    ↓
   malfind 找到 RWX + MZ、ldrmodules 發現 unlinked module
   netscan 看到對外 443 → 確認 C2 beacon，dump 記憶體做 shellcode 分析
```

## 具體範例

### 範例 1：Process Hollowing 的記憶體 pattern

Process hollowing 後的 `svchost.exe` 在記憶體裡會呈現：

- malfind：進程的主要可執行記憶體範圍（通常在 `0x400000` 或 `0x7ff8...`）顯示 private + exec + MZ header
- dlllist：`svchost.exe` 自己的 module base 的 path 是 `???` 或整個 module entry 消失
- ldrmodules：base address 對應的 `InLoad`/`InMem`/`InInit` 全 False

Process hollowing 的特徵和 reflective loading 的差別：hollowing 的 PE 通常在正常 PE base address 範圍，reflective loading 的 DLL 通常在 heap 範圍（較低位址）。

### 範例 2：Cobalt Strike Beacon 的特徵

CS beacon 注入 `svchost.exe` 的典型記憶體 pattern：

- malfind：找到 `fc 48 83 e4 f0 e8` 開頭的 private+exec block（x64 shellcode stager）
- netscan：對應 PID 有 ESTABLISHED 連線到外部 IP，port 通常是 443 或 80（HTTPS/HTTP C2）
- handles：可能有 `C:\Windows\Temp\` 或 `C:\ProgramData\` 的 file handle（staging 暫存）

如果 beacon 是 stageless（整個 payload 一次打進去），malfind dump 出來的記憶體 blob 可能是完整的 beacon DLL，丟進 strings 或 YARA 掃描可以找到 C2 domain 和 watermark。

### 範例 3：假陽性——JIT 的 RWX 記憶體

Chrome（Renderer 進程）、Edge、任何 .NET 應用程式（CLR）都會有大量 private+exec 的 VAD 節點，這是 JIT compiler 的正常行為。這些進程的 malfind 輸出會很長，但：

- 不會有 MZ header（JIT 輸出是機器碼，不是 PE）
- 進程名稱和 PPID 符合預期（`chrome.exe` 的 parent 是另一個 `chrome.exe`）
- 沒有對應的異常外連

看到 `chrome.exe` 有 malfind 命中不要驚慌，做上下文判斷。

## 對比表格：注入技術的記憶體痕跡

| 注入技術 | malfind | dlllist | ldrmodules 不一致 | netscan | cmdline |
|---|---|---|---|---|---|
| Classic DLL Injection | 可能（若 DLL 是 private backed） | 出現可疑 DLL 路徑（temp目錄） | 通常一致（有 path） | 視 payload | 視 launcher |
| Process Hollowing | **高**（主 module 變 private+MZ） | main module path = ??? | **InLoad/InMem = False** | 視 payload | 看父進程 |
| Reflective DLL Loading | **高**（heap 範圍 private+exec+MZ） | 出現 ??? 或消失 | **InLoad/InMem = False** | 視 payload | 視 launcher |
| Shellcode Injection | **高**（private+exec，可能無MZ） | 正常 | 正常 | 視 payload | 視 launcher |
| Thread Hijacking | 中（若注入 shellcode） | 通常正常 | 可能正常 | 視 payload | 不留 |

## 踩雷清單

1. **malfind 的 MZ 命中不等於 PE**：MZ（`4D 5A`）是 PE 的 magic，但很多二進位的前兩個 byte 剛好是 `4D 5A` 只是巧合。要看後面的 PE Optional Header（偏移 `0x3c` 的 e_lfanew 指向的 `PE\0\0` 簽章）確認是不是合法 PE 結構，再決定要不要 dump 分析。

2. **ldrmodules 的 InInit = False 對 ntdll.dll 是正常的**：ntdll.dll 是第一個被映射的 DLL，在 OS loader 的 `InInitializationOrderModuleList` 裡不存在是預期行為。把這個當 IOC 就是誤報。System、smss.exe 等也有類似正常例外。

3. **netscan 找到的不一定是現在活著的連線**：pool scanning 的問題——結構體可能是已關閉連線的殘骸。State 欄位是 `CLOSED`、`TIME_WAIT`、或空白的，不算當下活躍連線。只有 `ESTABLISHED`、`LISTEN` 是當下狀態。

4. **Handles 的 PID 解析可能對不上**：如果 handle 指向的進程已退出，PID 會被 OS 回收並分配給新進程，Handles 看到的 PID 對應的進程名稱可能已經不是當初那個。要對照 CreateTime 判斷。

5. **分析時 Vol3 的 `--pid` 要精確**：直接跑全域 malfind 不加 `--pid`，輸出量很大（特別是 JIT 進程）。先用 pstree 縮小範圍，再 `--pid` 鎖定，省時間也省 false positive 的 triage 成本。

## 進階延伸

- **windows.vadinfo.VadInfo**：列出一個進程所有 VAD 節點的完整資訊（protection、type、mapped file）。比 ldrmodules 更底層，適合手動追查特定記憶體範圍的來源。

- **記憶體 dump + YARA 掃描**：`malfind --dump` 出來的記憶體 blob 搭配 YARA 規則（例如 `yara-rules/Windows/` 裡的 Cobalt Strike / Meterpreter / Sliver 規則）做自動分類，比人工看 hex dump 快很多。Volatility 也有 `windows.yarascan.YaraScan` plugin 直接在記憶體裡跑 YARA。

- **windows.ssdt.SSDT（System Service Descriptor Table）**：SSDT hook 是 rootkit 攔截 syscall 的老技術，`windows.ssdt.SSDT` 掃描 SSDT，看哪些 syscall 的 handler 被改到 kernel 以外的位址（代表有 rootkit hook）。現代 Kernel Patch Protection（KPP/PatchGuard）讓這招在 64 位元 Windows 上越來越難，但特定環境（含 Hyper-V 的 VMs、舊版 Windows）還是要查。

- **ATT&CK 對映**：
  - T1055（Process Injection）：Classic DLL Injection、shellcode injection
  - T1055.002（Portable Executable Injection）：Process Hollowing
  - T1055.001（Dynamic-link Library Injection）：Classic DLL Injection
  - T1620（Reflective Code Loading）：Reflective DLL Loading

## 本章重點整理

- 攻擊者注入合法進程是為了讓惡意行為看起來來自可信進程；記憶體結構不騙人
- VAD tree 管理進程虛擬記憶體；private+exec 的 VAD 節點是注入的最直接痕跡
- `windows.malfind.Malfind`：找 private+exec 的 VAD 節點，看 MZ header 或 shellcode pattern
- `windows.dlllist.DllList`：走 PEB `InLoadOrderModuleList`，找路徑異常的 DLL
- `windows.ldrmodules.LdrModules`：比對 VAD vs PEB 三個模組列表，找 unlinked module
- `windows.handles.Handles`：找不尋常的高權限 process handle（lsass 是重點）
- `windows.netscan.NetScan`：pool scan 找 TCP/UDP 結構，關聯可疑 PID 的外連
- `windows.cmdline.CmdLine`：讀 PEB.ProcessParameters.CommandLine，抓 encoded payload
- 實戰用 pstree → malfind → dlllist → ldrmodules → handles → netscan → cmdline 的流程逐步收斂

## 自我檢核

- [ ] 我能解釋 VAD tree 是什麼，以及 private vs mapped VAD 節點的區別
- [ ] 我能說出 process hollowing 在記憶體裡留下哪些特徵，malfind 和 ldrmodules 各抓到什麼
- [ ] 我能說出 reflective DLL loading 和 classic DLL injection 在 dlllist 輸出裡的差異
- [ ] 我知道 malfind 的 false positive 來源（JIT），以及怎麼排除
- [ ] 我能把 malfind + netscan + pstree 串成一個「確認 C2 beacon 注入」的完整分析流程
- [ ] 我知道 ldrmodules 的 ntdll.dll InInit=False 是正常情況，不要誤報

## 延伸閱讀

1. **《The Art of Memory Forensics》** Ch 7（Process Memory Internals）和 Ch 17（Windows Malware Analysis）：VAD tree、PEB 模組列表的完整結構，以及 hollowing/injection 的分析案例。這兩章是本章內容的原始出處，比這裡深一到兩個量級。

2. **Volatility3 Plugin 原始碼** — `volatility3/plugins/windows/malfind.py`、`ldrmodules.py`：直接看 plugin 怎麼掃 VAD 和比對 PEB，理解它的判斷邏輯和侷限比讀文件更有效。

3. **SANS FOR508 — Memory Forensics Section**：SANS 的標準 IR 訓練課，memory forensics 那節直接展示真實 incident 的 memory image 分析過程，有我們課裡說的每個 plugin 的 live demo。

4. **"Hunting Cobalt Strike" — Elastic Security Research**（[https://www.elastic.co/security-labs/](https://www.elastic.co/security-labs/)）：Elastic 整理的 CS beacon 在記憶體和網路上的指紋，搭配 YARA 規則。讀完你知道 malfind dump 出來的 blob 要怎麼確認是不是 CS。

5. **T1055 Process Injection — MITRE ATT&CK**（[https://attack.mitre.org/techniques/T1055/](https://attack.mitre.org/techniques/T1055/)）：所有注入子技術的 sub-technique 列表，每個有 detection 建議。把這頁和本章的 plugin 對照著看，知道偵測涵蓋度在哪。

---

進程和注入看完，下一層往下挖：磁碟上的 NTFS 檔案系統在被攻擊時留下什麼 artifact？$MFT、$UsnJrnl、timestomping——檔案系統不說謊，但你要知道怎麼問。

→ [Ch 15 NTFS 檔案系統 artifacts：$MFT/$UsnJrnl/$LogFile](./15-windows-filesystem-artifacts.md)
