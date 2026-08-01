# Ch 38 — EMET→WDEG 緩解演進史

> **目標**：理解 Windows userland 緩解的完整軍備競賽史——從 2000 年代的 DEP/ASLR/SafeSEH 起點，經過 EMET 的「插件時代」，到緩解整併進 OS 的 WDEG/Exploit Protection 現代格局；能把 Part 3/4/5 學的所有緩解擺進這條時間線，理解每個緩解「為什麼這時候出現、為什麼這樣設計、後來被什麼研究打穿」；能向面試官描述 Windows 緩解的演進弧線。

---

Part 5 的前六章（Ch 32–37）把現代緩解一個個拆開來講：CFG 的 bitmap 查法、XFG 的型別哈希、CET 的 shadow stack、ACG 的動態程式碼禁令、data-only 的殘存空間。讀完你知道每個緩解的機制，但可能還沒有一條貫穿的時間線——「這些東西是怎麼一步步被逼出來的？」這章就是把那條線拉出來。

## 為什麼需要這個？

一個緩解的設計決策，放在歷史脈絡外看會覺得奇怪。

舉例：SEHOP（Ch 22）在 Vista SP1 引入，保護 SEH chain 最後一個 handler 必須是 `FinalExceptionHandler`。為什麼只保護最後一個？因為在那個年代，攻擊者的目標是覆蓋任意一個 handler，當時的防禦思路是「確保 chain 是合法連到 ntdll 的 handler 就好」。放進 2006 年的背景，這個決策完全合理——但如果你不知道背景，看起來像一個奇怪的半吊子防護。

另一個例子：EMET 的 EAF（Export Address Filtering）試圖偵測 shellcode 的 `GetProcAddress`-style 掃描，但用的方法是硬體中斷點（`dr0`–`dr3`）。這在 2012 年是一個合理的快速部署方案，但五年後 Offensive Security 和 FireEye 都公開了各種繞過方式，根本原因就是 EAF 不在 TCB（Trusted Computing Base）裡，它自己也是 userland 程式碼。

了解這條歷史線，你就能預測：**哪些緩解是真正的架構性防禦、哪些是臨時補丁、下一個攻擊面在哪裡**。

## 先建立直覺：一場軍備競賽的結構

每一輪緩解vs繞過的循環，結構都差不多：

```
  攻擊者發現新原語（e.g., SEH overwrite）
         │
         ▼
  防禦者加緩解（e.g., SafeSEH 白名單）
         │
         ▼
  攻擊者找例外（e.g., 用不在白名單外的模組）
         │
         ▼
  防禦者收緊（e.g., SEHOP 要求 chain 必須走到 FinalExceptionHandler）
         │
         ▼
  攻擊者轉移原語（e.g., 放棄 SEH，改打 ROP + DEP bypass）
         │
         ▼
  防禦者加下一層……
```

這個循環在每一代緩解上都重演。EMET 是「把多個緩解打包起來讓系統管理員一鍵啟用」的嘗試——它不是發明新緩解，而是把碎片化的防禦整合成一個可管理的框架，然後再加一些 OS 層還沒有的偵測邏輯（EAF、SimExecFlow、Caller Checks 等）。

## Part 1：2000 年代的地基——DEP、ASLR、SafeSEH、SEHOP、/GS

在 EMET 出現之前，Windows 已經跑過幾輪關鍵的緩解部署。

### 2003–2004：DEP（Data Execution Prevention）

Windows XP SP2（2004）引入 **資料執行防止（Data Execution Prevention, DEP）**。機制：把 stack 和 heap 頁面標記為 `PAGE_EXECUTE_DISABLE`，CPU 執行到這些頁面就觸發 `#XD`/`#NX` 例外。

硬體 DEP 依賴 Intel/AMD 的 NX bit（x64 頁表的第 63 位元）；軟體 DEP（`AlwaysOn` 政策）用 CPU 例外模擬，效果弱很多。

**攻擊者的反應**：不需要把 shellcode 放進 stack，把 stack 上的 return address 改成 `VirtualProtect`（把 stack 改成 executable）或 `LoadLibrary`（載入包含攻擊碼的 DLL）的地址就好——這就是早期的 ret2libc。然後 ROP（Return-Oriented Programming）被系統化研究，2007–2010 年 Nozomi、Checkoway、Shacham 等人把它寫成完整技術，DEP 就此被繞過。

**影響課程的哪章**：Ch 23 的 DEP+ROP。

### 2007：ASLR——Windows Vista 首次部署

Windows Vista（2007）在 OS 層引入 **位址空間佈局隨機化（ASLR）**，把 image base、stack、heap 的起始位址隨機化。

Vista 時代的 ASLR 缺陷：
- **image base 只有 8 bits 的熵**（x86，因為 PE 的 64KB 對齊限制，實際只有約 256 個可能位址）
- heap 的隨機化更弱
- **非 ASLR 模組只要一個存在，整條 chain 就有 anchor**（常見的靜態連結的舊版 DLL）
- stack 和 heap 的位址可能透過格式化字串或 info leak 洩漏

Windows 8 改進了熵（x64 提升到 17–19 bits），但 **32 位元程式的 ASLR 到今天仍然相對弱**（8–9 bits 熵 + 對齊限制）。

**影響課程的哪章**：Ch 24 的 ASLR 特性與繞法。

### 2003：`/GS` Stack Cookie

Visual Studio 2003 引入 `/GS`（Buffer Security Check），在每個函式 prologue 把一個 cookie（基址是 `__security_cookie`，初始化為隨機值 XOR 一個常數）壓入 stack，epilogue 時驗證是否被竄改。

關鍵細節（Ch 20 深挖過）：
- cookie 只保護 **return address**，不保護 handler 或任意局部變數
- 函式指標、SEH handler 在 cookie 之前（低位址），可以先改掉它們而不碰 cookie
- `__security_cookie` 的值可以用 info leak 或 brute force 洩漏

### 2003：SafeSEH（`/SAFESEH`）

MSVC 的 `/SAFESEH` linker flag 在 PE 的 Load Config 裡記下所有合法的 SEH handler 表，OS 在分派例外時先查表確認 handler 在白名單裡（Ch 11/12 的 SEH 架構細節，Ch 21 的 overwrite 打法）。

繞過方式：找一個**沒有 SafeSEH 旗標的模組**（特別是靜態連結的舊版 runtime DLL），用它裡面的 gadget 做 handler——白名單只保護有啟用 SafeSEH 的模組，舊模組是空窗。

### 2006–2007：SEHOP（SEH Overwrite Protection）

Vista SP1（2008）和 Server 2008 引入的 SEHOP 走另一條路：不做白名單，改驗整條 SEH chain 的完整性——它必須一路連結到 `ntdll!FinalExceptionHandler`（Ch 22 完整機制）。

SEHOP 的繞過比 SafeSEH 繞過難很多，但並非不可能：
- **heap spray + SEH chain 偽造**：如果你能控制 heap 上足夠多的記憶體，可以在 heap 裡偽造一條合法的 SEH chain，然後讓 `ExceptionList` 指向那條鏈
- **直接繞 chain 驗證**：某些特殊情況下，Windows 在 SEHOP 驗證前先觸發某種快速路徑而跳過它（這在 2011–2012 年有若干部落格文章記錄，但在 Win 8+ 之後已被修復）

## Part 2：EMET——緩解的「外掛時代」（2009–2018）

### EMET 是什麼

**Enhanced Mitigation Experience Toolkit（EMET）** 是 Microsoft 在 2009 年發布的安全工具，最後一版 5.52 在 2016 年，正式 EOL 在 2018 年 7 月。

EMET 的定位很特別：它不是在 OS 核心新增功能，而是一個**userland 的掛鉤框架**，透過把自己注入目標行程來攔截某些行為，並整合一些當時 OS 層還沒有預設啟用的緩解。

從攻擊者視角來看，EMET 是個有趣的對手：它自己也是 userland 程式碼，沒有 kernel 的執行環境保障，因此它的每個防護都有「從 userland 繞過 userland 防護」的根本侷限。

### EMET 引入的緩解清單

```
EMET 5.x 緩解機制概覽
─────────────────────────────────────────────────────────────────
緩解名稱               保護目標                    實作方式
─────────────────────────────────────────────────────────────────
DEP                    Stack/heap 可執行性          設定 SetProcessDEPPolicy
SEHOP                  SEH chain 完整性             在行程層強制啟用
NullPage               NULL pointer deref            保留第一個 64KB 頁面
Heap Spray Protection  堆噴射（固定地址）            預先 mmap 常見 heap spray 地址
EAF                    Export Address Filtering       DX 硬體中斷點在 ntdll/kernel32 export table
EAF+                   更強版 EAF                    擴大到更多系統 DLL
Caller Checks          確保呼叫者是合法 call 指令     驗證 return address 前面有 call
SimExecFlow            模擬執行流驗證                驗證 stack return address 鏈的合理性
StackPivot 偵測        阻止 stack pivot               偵測 RSP 脫離正常 stack 範圍
ASR                    Attack Surface Reduction       阻止特定 DLL 被特定行程載入
Mandatory ASLR         強制所有模組 ASLR             等同 PROCESS_DEP_ENABLE on image
Bottom-up randomization 由下往上隨機化               強制 heap/stack base 隨機化
─────────────────────────────────────────────────────────────────
```

### EAF 和 EAF+ 深挖

**Export Address Filtering（EAF）** 是 EMET 裡最有創意也最具爭議的緩解之一。

它試圖解決的問題：大多數 shellcode 在執行早期都需要做**動態 API 解析**——掃描 PEB 的 LdrInLoadOrderModuleList、找到 `kernel32.dll` 的 export table、用 hash 或字串比對找到 `LoadLibraryA`/`VirtualProtect` 等函式地址。這個技法在 Ch 25 講過，是 Windows shellcode 的標準開場。

EAF 的攔截方式：在 `ntdll.dll`、`kernel32.dll`、`kernelbase.dll` 的 **Export Address Table（EAT）起始位址**設下 **`dr0`/`dr1`/`dr2` 硬體中斷點（debug register breakpoint）**。任何讀取 EAT 的記憶體存取都觸發中斷，EMET 的處理器（透過 VEH 掛入）檢查讀取者是否是合法的呼叫鏈。

EAF+ 進一步把中斷點擴大到更多系統 DLL 和關鍵 API 入口。

**EAF 的根本缺陷**：

1. **硬體中斷點只有 4 個（`dr0`–`dr3`）**：EMET 要攔截所有目標 DLL 的 EAT，只好用一個中斷點覆蓋整個 EAT 頁面的範圍。但攻擊者只要改用「不讀 EAT」的 API 解析方法就能繞過——例如直接用預先知道的 function offset（版本綁定），或用 ROP chain 的 gadget 來 call API 而不做動態解析。

2. **自己是 VEH 在 userland**：EMET 透過 VEH（Vectored Exception Handler）攔截中斷。攻擊者如果能呼叫 `RemoveVectoredExceptionHandler` 或直接 NOP 掉 EMET 的 VEH handler，EAF 就失效。Jared DeMott（2013, DEF CON 21）和 Offensive Security（2014, kc57）都展示了類似的 VEH 繞過。

3. **讀取方式繞過**：直接讀取 EAT 以外的方式，例如掃描 module 的整個 `.text` 段、或利用另一個已知 DLL 裡的 `GetProcAddress` gadget，都能在不觸發 EAF 的情況下完成 API 解析。

### Caller Checks 與 SimExecFlow

**Caller Checks** 的想法：合法的函式呼叫，呼叫者的 `return address` 前面一定有 `call` 指令（因為 `call` 把下一條指令壓棧）。ROP gadget 跳過來的 return address 前面通常沒有 `call`。

實作：在某些敏感 API（如 `VirtualAlloc`、`VirtualProtect`、`WriteProcessMemory`）入口處，讀取 `[rsp]` 的 return address，往前讀 5 bytes 或 2 bytes，確認是否是 `call` 的操作碼。

**根本缺陷**：
- 攻擊者可以用 **`call` 結尾的 ROP gadget**（`call rbx`、`call [rax+8]` 後面接 `ret`）——chain 最後一個跳到 API 的 step 前面就是 `call`，完全合法
- 或者用 **蹦床（trampoline）**：先 ROP 到一個有 `call` 的跳板函式，再跳到目標 API

**SimExecFlow（Simulated Execution Flow）** 更雄心勃勃：嘗試沿著 stack 往下查 return address，確認每個 return address 前面都有對應的 `call`，以此驗證整條 call stack 是正常的。

缺陷：
- x64 的 stack frame 沒有 x86 那種 `ebp` chain，SimExecFlow 要用 DWARF/unwind info 或者啟發式掃描，容易有 false positive 和 false negative
- 任何用合法 `call` 結尾的 gadget 都能騙過它
- 從根本上說，「stack 上 return address 前面有 call」只是統計性的推斷，不是有保證的屬性

### StackPivot 偵測

ROP 攻擊常常需要把 `rsp` 從正常 stack 位置「pivot」到攻擊者控制的 fake stack（因為正常 stack 上的空間有限，或者攻擊者只能在 heap 上造 ROP chain）。

EMET 的 StackPivot 偵測：在某些函式入口，檢查 `rsp` 是否在合法的 stack 範圍內（即 TEB 的 `StackBase` 和 `StackLimit` 之間，Ch 5 的 TEB 結構）。

**繞過**：
- **在正常 stack 上就做完整個 ROP chain**：不 pivot，不觸發偵測
- **調整 stack 大小**：Windows 允許用 `SetThreadStackGuarantee` 或 VirtualAlloc 自行擴展 stack；攻擊者可以讓自己的 fake stack 落在「看起來像正常 stack 延伸」的範圍內
- **透過 fiber 或 coroutine**：fiber 有獨立的 stack，fiber context switch 後 TEB 的 StackBase/StackLimit 指向 fiber stack，而不是原始 thread stack

### ASR——Attack Surface Reduction（EMET 版本）

EMET 的 ASR 讓管理員定義「哪些 DLL 不允許被哪些行程載入」，例如禁止 Word 載入 Java 的 DLL，或禁止 Adobe Reader 載入 `wscript.dll`。

這個概念比機制本身更重要：它明確承認了**模組載入是 Windows userland exploit 的關鍵攻擊面**（攻擊者需要載入有 ROP gadget 的 DLL，或者靠 WScript/COM 執行程式碼）。EMET 的 ASR 是以黑名單方式實作，Windows Defender Exploit Guard 的 ASR（下面會講）改成了更結構化的規則集。

## Part 3：為什麼 EMET 的繞過研究最終導致它退場

### Offensive Security 的研究（2014）

Offensive Security 的 kc57 在 2014 年發表了系統化的 EMET 5.0 bypass 研究，核心論點是：

1. **EMET 的每個緩解都有繞過路徑**，很多繞過可以被組合使用
2. **EMET 的 VEH 攔截架構是根本弱點**：攻擊者在 userland 有足夠的能力（透過 ROP、shellcode、甚至合法 API）去調用 `RemoveVectoredExceptionHandler` 移除 EMET 的監控
3. **先打 EMET、再打目標**的兩段式策略是可行的

具體技法：利用 `ntdll!RtlRemoveVectoredExceptionHandler` 的位址（在 ASLR 繞過後可以取得）透過 ROP 呼叫它，把 EMET 的 VEH handler 移除，然後再做傳統 exploit。

### FireEye 的研究（2015, "Bypassing EMET 5.0"）

FireEye 的研究（Xuanwu Lab, WizardOpium, 2015）展示了更精細的 bypass，包括：

- **繞 EAF**：改用 `NtQueryVirtualMemory` 枚舉模組，不直接讀 export table
- **繞 Caller Checks**：構造以 `call` 結尾的 gadget chain
- **繞 SimExecFlow**：利用 SimExecFlow 只能追蹤部分 stack 的限制
- **利用 EMET 配置機制本身的漏洞**：EMET 用 registry 存設定，在某些版本有可被攻擊的配置解析邏輯

### Jared DeMott 的 DEF CON 研究（2013）

更早的研究揭示：EMET 自己的 DLL（`EMET.dll`）注入到受保護行程後，它的 IAT（Import Address Table）並不受 EAF 保護，形成了一個「防禦者自己的武器被翻轉」的諷刺局面——攻擊者可以 corrupt EMET.dll 的 IAT 來讓 EMET 呼叫到被劫持的函式。

### EMET 退場的根本原因

**架構性限制**，不是實作問題：

1. **Userland 防禦無法保護自己**：EMET 在被保護行程的 userland 裡運行，和被攻擊的程式碼共享同一個執行環境。攻擊者一旦有了任意程式碼執行，就有能力 disable EMET 自己。真正安全的緩解需要在更高特權層（kernel、hypervisor、硬體）實作。

2. **啟發式偵測 vs 結構性防禦**：EAF、Caller Checks、SimExecFlow 都是**啟發式（heuristic）**——它們說「這種行為模式像攻擊」，而不是「這個行為在結構上不可能被攻擊者利用」。啟發式偵測可以在特定場合下被欺騙。CFG 的 bitmap 查找、CET 的 shadow stack 則是**結構性防禦**——即使攻擊者知道機制，繞過需要解決的問題是計算困難的。

3. **版本管理複雜度**：EMET 是個獨立產品，需要獨立更新、配置。Enterprise 客戶常常落後好幾個版本，而繞過是已知的。整合進 OS 才能確保所有人都在最新版本。

## Part 4：WDEG 與 Exploit Protection——緩解進 OS（2017–今）

### Windows Defender Exploit Guard（WDEG）

Windows 10 Fall Creators Update（1709，2017 年 10 月）引入 **Windows Defender Exploit Guard（WDEG）**，它把 EMET 的精華概念整合進 OS，同時加了一些新東西：

```
WDEG = EMET 的觀念精華 + OS 層整合 + 更強的 ASR 規則引擎
```

WDEG 的四大元件：

```
┌─────────────────────────────────────────────────────────────────────┐
│              Windows Defender Exploit Guard (WDEG)                  │
├──────────────────┬──────────────────┬──────────────────────────────┤
│  Exploit         │  Attack Surface  │  Network Protection          │
│  Protection      │  Reduction (ASR) │  + Controlled Folder Access  │
│  (EMET 的繼承人) │  (更強的 ASR)   │  (勒索軟體防護)              │
└──────────────────┴──────────────────┴──────────────────────────────┘
```

**Exploit Protection**（`Set-ProcessMitigation` PowerShell 或 Windows Security GUI）是本課最關心的部分，它直接繼承 EMET 的緩解框架並整合進 kernel：

| EMET 設定 | Exploit Protection 對應 | 整合層級 |
|---|---|---|
| DEP | `ProcessMitigationPolicy: DepPolicy` | kernel（`NtSetInformationProcess`） |
| SEHOP | `DisallowWin32kSystemCalls`（間接） | kernel |
| Heap Spray Protection | 現代 segment heap encoding 取代 | kernel heap |
| EAF / EAF+ | **沒有直接繼承**（架構性拋棄） | — |
| Caller Checks | **沒有直接繼承** | — |
| SimExecFlow | **沒有直接繼承** | — |
| StackPivot 偵測 | CET shadow stack 從硬體層解決 | 硬體+kernel |
| Mandatory ASLR | `ForceRelocateImages` | kernel loader |
| Bottom-up randomization | `BottomUpASLR` | kernel |
| ASR | WDEG ASR（更強的規則引擎） | kernel+Antimalware Scan Interface |

注意：EAF、Caller Checks、SimExecFlow 這些啟發式偵測**完全沒有被繼承**。Microsoft 的決策是：啟發式偵測不夠可靠，寧可用更強的結構性防禦（CFG、CET）來解決根本問題，而不是繼承那些已知有繞過的偵測邏輯。

### Exploit Protection UI 與 PowerShell

Windows 10 1709 起，你可以在 **Windows Security → App & browser control → Exploit protection** 裡看到、設定 system-wide 和 per-app 的緩解：

```
System settings（系統全局，影響所有行程）：
  Control flow guard (CFG)              [On by default]
  Data Execution Prevention (DEP)       [On by default]
  Force randomization for images (Mandatory ASLR)  [Off by default]
  Randomize memory allocations (Bottom-up ASLR)    [On by default]
  High-entropy ASLR                     [On by default]
  Validate exception chains (SEHOP)     [On by default]
  Validate heap integrity               [On by default]

Program settings（per-app，追加設定）：
  可額外開：
  - ACG (Arbitrary Code Guard)          [參 Ch 36]
  - CIG (Code Integrity Guard)
  - CET shadow stack
  - Export address filtering (EAF)      [注意：繼承 EMET EAF 的名字，但移到 kernel 層]
  - Import address filtering (IAF)
  - Simulate execution (SimExecFlow)    [名字沿用，但實作改進]
```

> **重要區別**：Exploit Protection 裡的 EAF 和 EMET 的 EAF **實作層級不同**。Exploit Protection 的 EAF 是透過 `NtSetInformationProcess` 設定的 kernel 層 mitigation policy，由 kernel 在 system call 層面執行，不是 EMET 那種 VEH 掛鉤。但它能攔截的攻擊面仍然有限。

PowerShell 管理：

```powershell
# 查看當前行程的緩解狀態
Get-ProcessMitigation -System

# 針對特定行程設定
Set-ProcessMitigation -Name "notepad.exe" -Enable CFG,DEP,ASLR

# 匯出/匯入設定（企業部署用）
Get-ProcessMitigation -RegistryConfigFilePath .\mitigations.xml
Set-ProcessMitigation -PolicyFilePath .\mitigations.xml
```

> **未實測，理論預期**：`Get-ProcessMitigation` 需要 Windows 10 1709 以上，本機應可跑。`Set-ProcessMitigation` 修改 registry 下的 `HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\MitigationOptions`，需要管理員權限。

### ASR 規則引擎（WDEG/MDE）

WDEG 的 ASR（Attack Surface Reduction Rules）比 EMET 的 ASR 強得多：

EMET ASR：手動指定「禁止 X.exe 載入 Y.dll」，黑名單模式，配置複雜。

WDEG ASR：預先定義的結構化規則集，由 GUID 識別，例如：
- `D4F940AB-401B-4EFC-AADC-AD5F3C50688A`：Block all Office apps from creating child processes
- `3B576869-A4EC-4529-8536-B80A7769E899`：Block Office apps from creating executable content
- `75668C1F-73B5-4CF0-BB93-3ECF5CB7CC84`：Block Office apps from injecting code into other processes

這些規則是 **behaviour-based**：不是說「禁止載入某 DLL」，而是說「禁止 Office 做出會建立子行程的行為」。背後由 Microsoft Defender ATP（現在叫 Defender for Endpoint）的 kernel driver 執行，不依賴 userland 掛鉤。

## Part 5：「從外掛到內建」的演進意義

### 結構性 vs 啟發式——緩解設計哲學的分水嶺

EMET 退場、WDEG 取而代之，這個轉變代表了一個清晰的設計哲學轉向：

```
EMET 時代：啟發式偵測（「看起來像攻擊就擋」）
  ↓ 可以被攻擊者欺騙
  ↓ 自己在 userland 裡，沒有特權保障
  ↓ 版本管理複雜，部署落後

WDEG/OS 緩解時代：結構性防禦（「讓某類攻擊在架構上不可能」）
  ↓ CFG：indirect call 的目標在編譯期就白名單化了，不是執行期猜測
  ↓ CET：shadow stack 在硬體層維護，userland 程式碼根本碰不到
  ↓ ACG：禁止動態程式碼執行，不是偵測「動態程式碼看起來像 shellcode」
```

這個分水嶺很重要。一個啟發式緩解和一個結構性緩解的繞過難度有本質差別：

- **繞啟發式**：只要讓你的 exploit 行為不符合偵測規則的 pattern，不需要真正突破防禦
- **繞結構性**：你需要找到防禦機制本身的漏洞（CFG bitmap 的精度問題、CET 的 exception 處理竄改），難度指數級提升

### 緩解從「應用程式選擇」到「OS 預設」

另一個重要趨勢：早期的緩解需要**應用程式選擇啟用**（opt-in）——需要編譯器旗標、需要應用程式特別標記。SafeSEH 需要 `/SAFESEH`，ASLR 需要 `/DYNAMICBASE`，CFG 需要 `/guard:cf`。

但從 Windows 8 開始，系統強制要求（system-forced）或預設啟用（opt-out）的趨勢越來越明顯：

| 緩解 | 引入方式 | 當前狀態 |
|---|---|---|
| DEP | opt-in（XP SP2），強制（Win 8+ 某些場景） | 新程式幾乎全開 |
| ASLR | opt-in `/DYNAMICBASE` | Win 8+ mandatory ASLR 可強制 |
| CFG | opt-in `/guard:cf` | Win 10 起系統 DLL 幾乎全開 |
| CET | opt-in `/CETCOMPAT` | Win 11 22H2 起系統服務多數開 |
| Segment Heap | OS 預設，不需應用程式動作 | Win 10+ 所有 process 預設 |

未來方向：MSRC 公開表示，Microsoft 正在推動讓 CET 成為 system-wide 預設。這意味著幾年內，ROP-based exploit 的攻擊面會在 Windows 平台幾乎消失——攻擊者的「最後邊疆」是 data-only attacks（Ch 37）。

## 緩解時間線總表：2000s → 2020s

```
年份  緩解                        引入版本           擋什麼              後來怎麼了
────────────────────────────────────────────────────────────────────────────────────
2003  /GS stack cookie            VS 2003+           stack overflow      仍有效，但需
                                  (Win XP SP2)       →return addr        info leak繞
2003  SafeSEH (/SAFESEH)          XP SP2             SEH handler覆蓋     找無SafeSEH
                                                                         模組繞過
2004  DEP (Data Execution         XP SP2（硬體）     shellcode in        ROP出現後
      Prevention)                 Vista（普及）       stack/heap          實質被繞
2007  ASLR (/DYNAMICBASE)         Vista              確定性地址           info leak +
                                                                         32位元熵弱
2007  SEHOP                       Vista SP1/         SEH chain偽造       heap spray
                                  Server 2008                            chain 繞過
2009  EMET 1.0 發布               (獨立工具)          整合緩解框架        userland
                                                                         hook可移除
2012  EMET 3.0 (EAF/SimExec      (獨立工具)          shellcode API解析   多種已知繞
      /StackPivot)                                                        過路徑
2014  CFG                         Windows 8.1 U3     indirect call       需找非CFG
      (Control Flow Guard)        (編譯器+OS)         劫持                模組/bitmap
                                                                         精度問題
2016  EMET 5.52（最後版本）       (獨立工具)          最後一個 EMET       2018 EOL
2017  WDEG / Exploit Protection   Win10 1709          EMET 整合進OS       現行主流
      ACG (Arbitrary Code Guard)  Win10 1607（Creators）動態程式碼執行   開了幾乎無
                                                                         法繞
2018  XFG（eXtended Flow Guard）  VS 2019 16.x       CFG精度補強         型別hash
      預覽/部分部署               Win10 1903+                             仍有 bypass
2020  CET shadow stack            Win10 2004+         ROP (return addr)   需 CPL0
      (Hardware-enforced          (需第11代Intel      劫持                level bypass
      Stack Protection)           / AMD Zen3)
2022  CET 系統普及推進            Win11 22H2          ROP 殘存面          data-only
                                  (系統服務多數開)                        成主要路徑
────────────────────────────────────────────────────────────────────────────────────
```

## Linux 對照：緩解演進的差異

你有 Linux pwn 背景，所以這張對照表是加速器：

| 維度 | Windows | Linux |
|---|---|---|
| **DEP/NX** | 2004 XP SP2，`NX_COMPAT` 旗標 | 2004 kernel 2.6.8 NX bit；`-z execstack` 控制 |
| **ASLR** | 2007 Vista，`/DYNAMICBASE` | 2005 kernel 2.6.12，`/proc/sys/kernel/randomize_va_space` |
| **Stack cookie** | MSVC `/GS`（2003） | GCC `-fstack-protector`（2005） |
| **SEH 保護** | SafeSEH + SEHOP（Windows 專屬） | Linux 沒有 SEH，DWARF-based unwinding 不同機制 |
| **CFI** | CFG（OS+compiler，2014）+ XFG | clang CFI（2014，type-based）；GCC 無等價 |
| **Return protection** | CET shadow stack（硬體，2020） | Intel CET 同樣硬體；`-fcf-protection=return` |
| **動態碼禁止** | ACG（2016） | `seccomp`+`PROT_EXEC`（較早但機制不同） |
| **緩解框架** | EMET→WDEG（外掛→內建） | 無直接對應（各 distro 有 AppArmor/grsecurity/PaX） |
| **Heap encoding** | NT Heap cookie + segment heap LFH XOR | glibc 2.34+ safe-linking |
| **繞過研究聚焦** | 現代：data-only、JIT spray、CET 邊緣 | 現代：FSOP + glibc 2.35+ hook 已死、heap shape |

最大的差異：Windows 的緩解更多是**OS+編譯器+硬體的三方協同設計**（CFG 需要 compiler/linker/OS loader/ntdll 四方合力；CET 需要 CPU 硬體+kernel+compiler）。Linux 的緩解傾向於**OS 層獨立實作**（kernel NX、ASLR 不依賴 ELF 連結器，雖然編譯器有輔助）。這讓 Windows 的緩解在覆蓋度上更難繞過，但也讓整個生態鏈更複雜。

## 踩雷集錦

1. **「EMET 被 EOL 代表它沒用」**：錯誤的推論方向。EMET 退場不是因為它防禦力差，而是因為 Microsoft 找到了更好的架構（OS 整合 + 結構性防禦），同時維護兩套東西沒有意義。如果你在 2016 年跑的是 Win7 企業環境，EMET 5.52 仍然是當時最好的選擇。

2. **「EAF 繞了就等於 WDEG EAF 也繞了」**：EMET 的 EAF 是 VEH userland hook，WDEG 的 EAF 是 `NtSetInformationProcess` kernel mitigation policy。繞過方式完全不同。看到「EAF bypass」的 blog post 時，先確認它說的是哪一代。

3. **「強制 ASLR 和 DYNAMIC_BASE 是同一件事」**：`/DYNAMICBASE` 是 PE 標記「這個 image 支援被重定基址」；強制 ASLR（mandatory ASLR / `ForceRelocateImages`）是 OS 對**所有**映像（包括沒有 `/DYNAMICBASE` 的舊 PE）強制隨機化。兩個不同的政策，在低版本 Windows 只有前者有效。

4. **「WDEG 裡的 SimExecFlow 和 EMET 的 SimExecFlow 一樣」**：不一樣。WDEG 的版本在 kernel 層執行，不依賴 VEH；而且 Microsoft 一直在更新它，讓它的 call stack 驗證更精確。但它仍是啟發式，不是結構性防禦。

5. **「緩解打開了就一定有效」**：CFG 只保護「有 CFG 旗標的模組裡的 indirect call 目標」；如果行程裡有一個沒有 CFG 的舊 DLL，那個 DLL 裡的間接跳轉完全不受保護，可以被當作跳板。緩解的有效性依賴**覆蓋面**，一個不受保護的模組可以讓整條防禦鏈失效。

## 進階：再往深一層

**Microsoft 的研究緩解 pipeline**：Microsoft Research 和 MSRC 一直在推進比 CFG/CET 更激進的緩解，例如：
- **Virtualization-Based Security (VBS)** 和 **HVCI**：把 kernel 的程式碼完整性驗證搬進 hypervisor，讓即使 kernel 被攻破也無法載入未簽章的驅動（`windows_kernel_driver` 課會深挖）
- **Hardware-isolated processes**（HIP）：把敏感行程的記憶體空間隔離到 hypervisor 保護的 enclave
- **Retpoline / IBRS / IBPB**：Spectre/Meltdown 緩解，改變了 BTB（Branch Target Buffer）的使用方式，對 JIT spray 也有間接影響

從攻擊者視角，未來幾年 Windows userland 最有研究價值的方向：
1. **CET 的 exception 處理邊緣案例**：shadow stack 和正常 stack 的 context restore 在 exception 時有複雜的互動，Windows 的實作可能有邊緣案例
2. **data-only attacks 的自動化**（Ch 37 的路徑）：隨著控制流劫持越來越難，攻擊者必然轉向改變程式的**資料**而非**控制流**
3. **JIT spray 的現代變體**：ACG 擋了動態生成 shellcode，但某些 JIT 編譯器（Python、.NET）在 ACG 下有特殊處理路徑，可能有繞過空間

**面試高頻問題**：「你能說說為什麼 EMET 最終退場了嗎？」——答案的要點是：架構性限制（userland hook 保護不了自己）+ 啟發式 vs 結構性防禦的哲學差異 + OS 整合更優。

## 動手練習

在你的 Win11 機器上，用 PowerShell 查看系統的緩解政策狀態：

```powershell
# 查看系統全局緩解設定
Get-ProcessMitigation -System

# 查看 notepad.exe 的緩解狀態（如果有在跑）
# 先開一個 notepad，然後：
$pid = (Get-Process notepad).Id
Get-ProcessMitigation -Id $pid

# 查看一個具體的可執行檔的緩解設定（不是執行中的行程）
Get-ProcessMitigation -Name "notepad.exe"
```

接著，用 `winchecksec`（如果有安裝）或 `objdump -p` 看幾個 Windows 系統 DLL 的 `DllCharacteristics`，確認它們是否有 `GUARD_CF`（0x4000）旗標：

```bash
# 在 MSYS2 shell 裡
objdump -p /c/Windows/System32/ntdll.dll | grep -i "DllCharac"
objdump -p /c/Windows/System32/kernel32.dll | grep -i "DllCharac"
```

記錄它們的 `DllCharacteristics` 值，和 Part 5 各章學過的緩解對照，確認哪些 OS 核心 DLL 開了哪些防護。

## 本章重點整理

- Windows userland 緩解的演進弧線：2000s 的 DEP/ASLR/SafeSEH 地基 → 2009–2018 EMET 的「外掛時代」（啟發式偵測整合框架）→ 2017 起緩解整合進 OS（WDEG/Exploit Protection）+ 結構性防禦（CFG/CET）
- EMET 退場的根本原因是**架構性限制**：userland hook 在有任意程式碼執行的情況下無法保護自己；啟發式偵測（EAF/Caller Checks/SimExecFlow）對知道規則的攻擊者沒有保障
- 「從外掛到內建」不只是整合問題，而是防禦哲學的轉向：**從啟發式偵測到結構性防禦**（「讓這類攻擊在架構上不可能」）
- 現代 Windows 緩解（CFG+CET+ACG 組合）把傳統控制流劫持攻擊面逼到最小，攻擊者的主要殘存空間是 data-only attacks

## 自我檢核

- [ ] 不看筆記，能列出 EMET 的五個核心緩解（EAF/Caller Checks/SimExecFlow/StackPivot/ASR），並說出每個的偵測邏輯和根本繞過方式
- [ ] 能解釋為什麼「EAF 用硬體中斷點」是一個根本的架構缺陷，而不只是實作品質問題
- [ ] 能說出 WDEG Exploit Protection 相對 EMET 的三個關鍵改進
- [ ] 能說出「啟發式緩解」和「結構性緩解」的差別，並給出一個 EMET 啟發式緩解和一個 OS 結構性緩解的例子
- [ ] 能把 DEP、ASLR、SafeSEH、SEHOP、/GS、CFG、XFG、CET 放進正確的時間順序，並說出每個「被什麼攻擊技法直接催生」

## 延伸閱讀

### 官方文件

- **[Exploit Protection Reference — Microsoft Learn](https://learn.microsoft.com/en-us/microsoft-365/security/defender-endpoint/exploit-protection-reference)**
  - **讀哪裡**：每個 mitigation policy 的說明表格，特別是「System-level mitigation」和「Per-program mitigation」兩節
  - **和本章的關聯**：這是 WDEG/Exploit Protection 緩解清單的官方定義，讀完能把本章的表格和官方文字對上
  - **前提知識**：本章（Ch 38）

- **[Configure and validate exclusions for Microsoft Defender Exploit Guard](https://learn.microsoft.com/en-us/microsoft-365/security/defender-endpoint/customize-exploit-protection)**
  - **讀哪裡**：PowerShell `Set-ProcessMitigation` 的完整參數列表
  - **和本章的關聯**：動手練習的參考，也是 enterprise 部署 WDEG 的操作手冊

### 研究報告 / 白皮書

- **"Bypassing EMET 4.1" — Bromium（2014）/ "Bypassing EMET 5.0" — FireEye（2015）**
  - **讀哪裡**：搜尋 FireEye blog「Bypassing EMET 5.0」或 Bromium 白皮書；重點讀 EAF bypass 和 VEH removal 段落
  - **和本章的關聯**：理解為什麼 EMET 的 userland hook 架構是根本弱點的第一手材料
  - **前提知識**：VEH 架構（Ch 12），EAF 機制（本章 Part 2）

- **"Enhanced Mitigation Experience Toolkit: A Technical Deep Dive" — Microsoft（2012, MSRC blog）**
  - **讀哪裡**：MSRC blog 的 EMET 技術深挖，說明 EMET 各緩解的設計動機（搜尋 "EMET deep dive MSRC"）
  - **和本章的關聯**：Microsoft 自己的視角，說明他們當時為什麼這樣設計
  - **前提知識**：本章 Part 2

### 部落格文章

- **"Protecting Against Exploit Kits with EMET" — Jared DeMott, DEF CON 21（2013）**
  - **讀哪裡**：DEF CON 21 議程資料（slides 在 defcon.org），重點是 EMET.dll IAT corruption 段落
  - **和本章的關聯**：「防禦工具自己成為攻擊面」的第一手案例
  - **前提知識**：IAT 結構（Ch 3/6），VEH 架構（Ch 12）

- **[Windows Security blog — Hardware-based Stack Protection](https://www.microsoft.com/en-us/security/blog/2020/10/26/hardware-based-stack-protection-for-modern-windows/)** — Microsoft Security Blog（2020）
  - **讀哪裡**：整篇，重點是「為什麼 software-only 的 stack 保護不夠」段落
  - **和本章的關聯**：從 EMET 的 StackPivot 偵測到 CET shadow stack 的演進邏輯，Microsoft 官方的說明
  - **前提知識**：Ch 35（CET shadow stack）

Part 5 最後一章把本課學過的所有緩解整理成一張實戰查表，附上面對現代全緩解目標的繞過決策樹。

→ [Ch 39 — 緩解總表 + 繞過決策樹](./39-mitigation-decision-tree.md)
