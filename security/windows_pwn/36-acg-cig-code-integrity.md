# Ch 36 — ACG / CIG / code integrity

> **目標**：弄清楚 Arbitrary Code Guard（ACG）和 Code Integrity Guard（CIG）怎麼在 Windows 裡把「注入可執行程式碼」這條路封死——不是靠偵測，而是靠底層 VAD（Virtual Address Descriptor）機制讓 RWX 和新的可執行區域根本建立不起來；理解這兩個緩解如何逼迫攻擊者放棄傳統「注入 shellcode 再跳」的路，轉向 data-only 或 JIT 攻擊；弄清楚 ACG 和 JIT compiler 的根本衝突與 out-of-process JIT 設計怎麼解決它。

---

走到這章，你的攻擊面已經被連削三刀：

- **CFG**（Ch 32）：間接呼叫目標必須是已知合法函式。
- **XFG**（Ch 34）：間接呼叫目標還要型別簽章相符。
- **CET**（Ch 35）：`ret` 指令的目標由硬體 shadow stack 把關，ROP 鏈的回傳位址無法竄改。

你可能以為剩下的路是「找一段記憶體，把 shellcode 寫進去，把頁面屬性從 RW 改成 RX，然後跳過去」。ACG 就是專門把這條路封掉的。

## 為什麼需要這個？

### 傳統「注入可執行程式碼」的流程

在沒有現代緩解的世界，攻擊者在有任意寫原語之後，流程大致是：

```
1. VirtualAlloc(NULL, size, MEM_COMMIT, PAGE_EXECUTE_READWRITE)
   → 拿一塊 RWX 頁面，把 shellcode 寫進去，直接跳
   
   或者：
   
2. VirtualAlloc(NULL, size, MEM_COMMIT, PAGE_READWRITE)
   → 把 shellcode 寫進去
   VirtualProtect(addr, size, PAGE_EXECUTE_READ, &old)
   → 把頁面改成 RX，然後跳

   或者（更隱蔽）：
   
3. CreateFileMapping + MapViewOfFile（兩次映射，一次 RW 寫、一次 RX 跳）
```

DEP/NX 殺掉了「直接在 stack/heap 跳」，但 VirtualAlloc + VirtualProtect 的兩步法照跑無阻——你只是多了一個 `VirtualProtect` 呼叫，把你可控的記憶體標成可執行的，然後跳。

CFG/XFG 限縮了間接跳轉目標，但如果目標位址是你剛才 `VirtualProtect` 出來的那塊 RX 區域，而那塊區域的頁面碰巧在 CFG 的合法 target 表裡（某些情境下是可能的），問題就來了。更嚴重的是，JIT compiler——比如 Edge 的 ChakraCore 或 Chrome 的 V8——本來就要在執行期建立可執行程式碼，這些 JIT 產生的頁面是合法的 RX 頁面，可以被 CFG 當成合法目標。如果攻擊者能**控制 JIT 輸出的內容**（JIT spraying），就能在合法的 RX 頁面裡塞惡意程式碼，而 CFG 完全看不出來。

ACG 要解決的核心問題是：**不是偵測惡意程式碼，而是讓「新的可執行程式碼出現」這件事本身變得不可能**。

## 先建立直覺

### 頁面生命週期的三個禁令

ACG 啟用後，作業系統在這個 process 的虛擬記憶體管理裡加了三條死規則：

```
ACG 的三條硬限制：
─────────────────────────────────────────────────────────────────────

 ❌ 規則一：禁止建立新的 RWX 頁面
    VirtualAlloc(..., PAGE_EXECUTE_READWRITE)  → 失敗（ERROR_ACCESS_DENIED）
    VirtualAlloc(..., PAGE_EXECUTE_WRITECOPY)  → 失敗

 ❌ 規則二：禁止把現有的可寫頁面升格為可執行
    VirtualProtect(可寫頁面, ..., PAGE_EXECUTE_READ) → 失敗

 ❌ 規則三：禁止修改現有的可執行頁面的保護屬性
    VirtualProtect(可執行頁面, ..., PAGE_EXECUTE_READWRITE) → 失敗

 ✅ 合法路徑只有：
    從磁碟上有數位簽章的 PE 檔映射進來（映射完之後自動是 RX，不可寫）
    → 所有可執行頁面，從一開始就是「磁碟映射的唯讀簽章程式碼」
```

這個設計的威力在於：**可執行記憶體只能來自 PE loader 映射進來的 signed image**。攻擊者在取得任意寫原語之後，就算能寫任意位址，能改任何資料，但「讓這些資料可執行」的那一步，永遠走不通。

### 對照 Linux 的類比

Linux 沒有等價的系統級緩解，但你熟悉的 V8 sandbox（browser_pwn 課的重點）非常類似 ACG 的精神：你在 V8 heap 裡的任意讀寫被沙箱範圍限制住，逃出去的路只剩 data-only 或 pointer compression 邊界外的原語。ACG 是在作業系統層面做了類似的事：所有頁面的可執行性，由 kernel 的 VAD 強制管理，應用層的 `VirtualProtect` 無法越過這條線。

## ACG：底層機制

### 設定 ACG

ACG 透過 `SetProcessMitigationPolicy` API 啟用：

```c
// 未實測，理論預期（需 Windows 10 版本 1507 以上）
#include <windows.h>
#include <processthreadsapi.h>

PROCESS_MITIGATION_DYNAMIC_CODE_POLICY policy = { 0 };
policy.ProhibitDynamicCode = 1;      // 主開關：禁止動態程式碼
policy.AllowThreadOptOut   = 0;      // 不允許個別執行緒豁免（Edge 在 renderer 用的機制）
policy.AllowRemoteDowngrade = 0;     // 不允許父行程移除這個保護

BOOL ok = SetProcessMitigationPolicy(
    ProcessDynamicCodePolicy,
    &policy,
    sizeof(policy)
);
// ok == FALSE 且 GetLastError() == ERROR_ACCESS_DENIED 表示 ACG 不支援或已設
```

> **未實測，理論預期**：`ProcessDynamicCodePolicy` 是 `PROCESS_MITIGATION_POLICY` 的第 7 個列舉值（`= 6`，以 0 起算）。啟用後，同一 process 內的所有執行緒都受約束，且這個狀態不可逆——沒有 API 能把 `ProhibitDynamicCode` 關掉（對照 `AllowThreadOptOut = 1`，那個是允許個別執行緒豁免，用於 Edge 的 JIT 執行緒架構，見後面的 out-of-process JIT 節）。

查詢一個 process 是否已啟用 ACG：

```c
// 未實測，理論預期
PROCESS_MITIGATION_DYNAMIC_CODE_POLICY q = { 0 };
GetProcessMitigationPolicy(
    GetCurrentProcess(),
    ProcessDynamicCodePolicy,
    &q,
    sizeof(q)
);
printf("ProhibitDynamicCode = %d\n", q.ProhibitDynamicCode);
```

或者從外部查詢（PowerShell）：

```powershell
# 未實測，理論預期
Get-ProcessMitigation -Id (Get-Process msedge | Where-Object MainWindowTitle | Select-Object -First 1).Id
# 輸出裡 DynamicCode.ProhibitDynamicCode = ON 就是 ACG 啟用
```

### VAD 層面的強制：為什麼「繞過」很難

ACG 不是靠 hook、不是靠 SSDT 攔截，而是直接在 NT kernel 的 `NtAllocateVirtualMemory` 和 `NtProtectVirtualMemory` syscall 裡加了一個 check。當 process 的 `EPROCESS` 結構裡的 `DynamicCodePolicy` 旗標設為 `ProhibitDynamicCode` 時：

```
NtProtectVirtualMemory 的 ACG 檢查邏輯（簡化）：
──────────────────────────────────────────────────────────────────

  請求保護屬性 new_prot = PAGE_EXECUTE_READ（或任何 EXECUTE 屬性）？
    ├─ 是 → 查看 VAD（Virtual Address Descriptor）for 這塊範圍：
    │        ├─ VAD 類型 = VadImageMap（由 PE loader 映射的 section）？
    │        │    └─ 允許（這是 signed image 的頁面）
    │        └─ VAD 類型 = VadAwe / 一般 private 頁面？
    │             └─ 拒絕（ERROR_ACCESS_DENIED）← ACG 擋在這裡
    └─ 否 → 正常走後續流程
```

攻擊者要繞過這個 check，需要在 kernel 層面找到一個洞——純粹的 userland 技法無法繞過這個強制。這就是 ACG 跟「把一個函式 hook 掉」之類的防護機制根本不同的地方：它的信任根在 kernel 的 VAD 管理，而不是 userland 的 API。

### 對 exploit 流程的影響

```
傳統 shellcode 注入路徑（ACG 啟用後的命運）：
─────────────────────────────────────────────────────────────────

傳統路徑一：VirtualAlloc(RWX) + 跳
  VirtualAlloc(..., PAGE_EXECUTE_READWRITE)
  ↓
  kernel NtAllocateVirtualMemory 拒絕請求
  → GetLastError() == 87 (ERROR_INVALID_PARAMETER) 或 5 (ERROR_ACCESS_DENIED)
  → exploit 在這裡就死了

傳統路徑二：VirtualAlloc(RW) + VirtualProtect(RX) + 跳
  VirtualAlloc(..., PAGE_READWRITE)  ← OK，這步沒問題
  memcpy(addr, shellcode, len)       ← OK，寫得進去
  VirtualProtect(addr, len, PAGE_EXECUTE_READ, &old)
  ↓
  kernel NtProtectVirtualMemory 看 VAD：這是 private 頁面 → 拒絕
  → exploit 卡在 VirtualProtect 這步

傳統路徑三：Reflective DLL injection
  把 DLL 的位元組寫進目標 process，手動走 PE relocation，再讓它可執行
  ↓
  「讓它可執行」的那一步就是 VirtualProtect → 同樣被 ACG 攔住
```

攻擊者在面對 ACG 的 process 時，唯一剩下的選項是：
1. **純 ROP/JOP**：只使用現有的合法可執行頁面（但這受到 CFG + XFG 的限制）。
2. **Data-only**：完全不劫持控制流，只改資料（Ch 37 的主題）。
3. **逃離這個 process**：找到一個沒有 ACG 的 process，透過 IPC 讓它幫你執行惡意操作。

## CIG：Code Integrity Guard

ACG 擋的是「動態建立新的可執行程式碼」；CIG 擋的是「載入沒有簽章的 DLL」。兩者配合，把程式碼注入的另一個路徑也堵死。

### 問題背景：DLL 注入

傳統的 DLL 注入手法（`LoadLibrary` 呼叫或反射式注入）不需要動態申請 RWX 頁面——它是透過 PE loader 把一個 DLL 映射進目標 process，讓 DLL 的 `.text` section（本來就是 RX）在目標 process 的位址空間裡執行。

這條路 ACG 擋不到，因為映射 PE 是合法操作。

CIG 的目標：**只允許有微軟簽章（或可信任的企業簽章）的映像（image）被載入**。

### CIG 的啟用

```c
// 未實測，理論預期
PROCESS_MITIGATION_BINARY_SIGNATURE_POLICY sig_policy = { 0 };
sig_policy.MicrosoftSignedOnly = 1;   // 只允許 Microsoft 簽章
// sig_policy.StoreSignedOnly = 1;    // 也允許 Windows Store 簽章
// sig_policy.MitigationOptIn = 1;    // opt-in 模式（某些應用用）

BOOL ok = SetProcessMitigationPolicy(
    ProcessSignaturePolicy,
    &sig_policy,
    sizeof(sig_policy)
);
```

> **未實測，理論預期**：一旦設好 `MicrosoftSignedOnly = 1`，在這個 process 裡呼叫 `LoadLibrary("evil.dll")` 載入沒有 Microsoft 簽章的 DLL，會得到 `ERROR_INVALID_IMAGE_HASH`（0xC0000428，`STATUS_INVALID_IMAGE_HASH`）。就算你有任意寫原語能手動走 `LdrLoadDll`，PE loader 在驗章這一步也會失敗。

### CIG 能擋什麼、擋不到什麼

```
攻擊路徑 vs CIG：
────────────────────────────────────────────────────────────────────────

 ❌ CreateRemoteThread + LoadLibrary("evil.dll")
    → CIG 在 loader 驗章失敗 → STATUS_INVALID_IMAGE_HASH

 ❌ SetWindowsHookEx 注入（hook DLL 會被強制 LoadLibrary）
    → 同上

 ❌ AppInit_DLLs、IAT hijacking 讓目標 LoadLibrary 你的 DLL
    → 同上，loader 不讓沒簽章的 DLL 進來

 ✅ CIG 擋不到的：
    - 純 shellcode 注入（但 ACG 擋了這條）
    - 竄改既有已載入的 DLL 的程式碼（.text section）
      → 但這是 PAGE_EXECUTE_READ，寫入會觸發 GPF；
        如果你有把這頁改成可寫的能力 → ACG 又攔了
    - Data-only 攻擊（不需要任何 DLL 載入或新的可執行程式碼）
```

ACG + CIG 的組合，讓傳統的「注入你的程式碼讓它執行」路徑全線封死。攻擊者面對的是一個「只能在已知程式碼裡玩」的世界。

## Process Mitigation Policy：系統層面設定

除了 API，這些緩解也可以從系統層面透過 Windows Defender Exploit Guard（WDEG）設定，不需要應用程式自己呼叫 API：

```powershell
# 未實測，理論預期（需要 WDEG PowerShell module）
# 對特定應用強制啟用 ACG
Set-ProcessMitigation -Name "msedge.exe" -Enable DynamicCode

# 查詢設定
Get-ProcessMitigation -Name "msedge.exe"
```

WDEG 設定的原理是：把這些 policy 存在 registry（`HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options\<exe>`），讓 Windows loader 在行程建立時自動套用，等同於應用程式自己呼叫 `SetProcessMitigationPolicy` 的效果。這個機制讓系統管理員可以對舊版、沒有自己設定緩解的應用程式強制套上保護。

## Edge renderer sandbox 的實戰部署

Microsoft Edge（Chromium）的 renderer process 是目前公開文件最詳盡的 ACG/CIG 實戰部署案例。

### renderer sandbox 的緩解矩陣

```
Edge renderer sandbox（現代版 Chromium-based Edge）：

 緩解項目                    | renderer | browser | GPU process
─────────────────────────────|──────────|─────────|────────────
 Integrity Level             | Low IL   | Medium  | Low IL
 Job Object                  | ✅       | ✅      | ✅
 Desktop Object              | ✅       | ✅      | ✅
 CFG                         | ✅       | ✅      | ✅
 ACG（ProhibitDynamicCode）  | ✅       | ❌      | ❌
 CIG（MicrosoftSignedOnly）  | ✅       | ❌      | ❌
 ChildProcess 限制           | ✅       | ✅      | ✅
 Win32k lockdown             | ✅       | ❌      | ❌
 CET（若硬體支援）           | ✅       | ✅      | ✅
```

renderer process 是攻擊的入口（你在 browser_pwn 課裡打的 V8 漏洞就在這裡），所以它的緩解最緊。browser process（broker）本身不開 ACG，因為它需要做更多動態操作。GPU process 也沒有 ACG，這是 ACG 部署的一個重要限制——JIT 和 GPU 管線需要動態程式碼能力。

### 從 browser_pwn 的視角看 ACG 的影響

你在 browser_pwn 課程裡做過 V8 的 data-only 攻擊——改 V8 JSObject 的 `map` 指標造成 type confusion，取得任意讀寫原語，再用這個原語竄改 ArrayBuffer 的 backing store 指標讀寫沙箱外記憶體。那個攻擊的核心是：**不需要注入任何新的程式碼，不需要 RWX 頁面，不需要 VirtualProtect**——所以它完全繞過 ACG。

ACG 就是把「攻擊者必須用 data-only」這件事從「最優解之一」升級成了「唯一可行路線」（配合 CFG + XFG + CET 堵住控制流劫持之後）。你在 browser_pwn 課裡練的那套手法，正是 ACG 時代的標準攻擊框架。

## ACG 與 JIT compiler 的根本衝突

ACG 的禁令很乾淨——但它和任何需要在執行期生成機器碼的元件直接衝突。這不是理論問題，這是工程上的硬衝突。

### JIT 為什麼需要動態可執行記憶體

JIT compiler（V8 的 Maglev/Turbofan、ChakraCore 的 JIT、.NET 的 CLR JIT）的工作流程是：

```
JIT 工作流程（ACG 啟用前）：

  解析 JavaScript 熱路徑 → 生成機器碼（位元組在記憶體裡）
        ↓
  VirtualAlloc(size, PAGE_READWRITE)  ← 申請可寫頁面，把機器碼寫進去
        ↓
  VirtualProtect(addr, PAGE_EXECUTE_READ)  ← 改成可執行唯讀
        ↓
  跳進去執行                ← 在 RX 頁面裡執行 JIT 生成的程式碼
```

這個流程裡的 `VirtualProtect(..., PAGE_EXECUTE_READ)` 正是 ACG 規則二禁止的操作。所以：**在同一個 process 裡同時開 ACG 和 JIT，根本不可能**。

### Edge 的解法：Out-of-Process JIT

Edge 的解法是把 JIT 移到另一個 process：

```
Out-of-Process JIT 架構：

  ┌────────────────────────────────┐    ┌───────────────────────────────┐
  │   Renderer process             │    │   JIT process                 │
  │   (ACG 開啟)                   │    │   (ACG 關閉)                  │
  │                                │    │                               │
  │   V8 / JIT frontend:           │    │   JIT backend:                │
  │   - 分析熱路徑                  │    │   - 生成機器碼                 │
  │   - 發送 IR 給 JIT process ─────┼───►│   - VirtualAlloc(RW)         │
  │                                │    │   - 寫機器碼                  │
  │   JIT process 把機器碼映射      │◄───┼──   - VirtualProtect(RX)      │
  │   回來（shared section）       │    │   - 建立 shared section       │
  │   ↓                            │    │     映射給 renderer            │
  │   在這個 RX shared section 執行│    │                               │
  │   JIT 程式碼                   │    │                               │
  └────────────────────────────────┘    └───────────────────────────────┘
       ↑ ACG 允許：這塊 section
         是由另一個 process 建立並映射的，
         在 renderer 的 VAD 裡是 VadImageMap 或類似合法映射類型
```

> **注意**：這個 shared section 的映射細節和 VAD 類型的精確處理方式，在不同版本的 Edge/Chromium 裡有所調整，且是資安研究的活躍目標（這個 IPC 通道本身就是一個新攻擊面）。以上是概念模型，實作細節應以 Chromium 原始碼的當前版本為準。

### AllowThreadOptOut：另一個彈性機制

早期版本 Edge 用的是 `AllowThreadOptOut = 1`，允許特定執行緒把 ACG 關掉（JIT 執行緒）。但這個設計有個大問題：**攻擊者如果能控制執行緒的 ACG 狀態，就能在 renderer process 內部創造一個可執行環境**。這就是把 out-of-process JIT 定為更安全架構的原因。

## 兩個緩解的組合效果

```
ACG + CIG 組合後，傳統程式碼注入路徑的全面封閉：

 注入技法                        | 無 ACG/CIG | ACG | CIG | ACG+CIG
─────────────────────────────────|────────────|─────|─────|────────
 VirtualAlloc(RWX) shellcode     |    ✅ 可行  | ❌  | ✅  | ❌
 VirtualAlloc+VirtualProtect     |    ✅ 可行  | ❌  | ✅  | ❌
 Reflective DLL injection        |    ✅ 可行  | ❌  | ✅  | ❌
 LoadLibrary 惡意 DLL            |    ✅ 可行  | ✅  | ❌  | ❌
 AppInit_DLLs / Hook 注入        |    ✅ 可行  | ✅  | ❌  | ❌
 JIT spraying（劫持 JIT 輸出）   |    ✅ 可行  | ❌  | ✅  | ❌
 Data-only（改資料不改程式碼）   |    ✅ 可行  | ✅  | ✅  | ✅ ← 唯一剩下的路

 攻擊者的結論：ACG+CIG 環境下，只有 data-only 還走得通
```

## 對比與取捨

| 面向 | ACG | CIG | 合用效果 |
|---|---|---|---|
| **防護對象** | 動態程式碼建立 | 未簽章 DLL 載入 | 程式碼注入全路徑封鎖 |
| **信任根** | Kernel VAD 管理 | PE loader 驗章 | 均在 kernel 層，userland 繞不過 |
| **對 JIT 的影響** | 直接衝突，需 OOP-JIT | 無直接影響 | 架構複雜度增加 |
| **攻擊面** | OOP-JIT IPC 通道 | 微軟自家簽章 DLL 的漏洞 | 轉移而非消除 |
| **部署難度** | 需應用程式適配（JIT 重構） | 相對容易（驗章機制） | 需完整的 sandbox 架構設計 |
| **Linux 對應** | 無（SELinux mprotect 限制類似但不完全等價） | 無（LD_PRELOAD 無簽章機制） | Windows 特有的組合 |
| **繞過難度（純 userland）** | 極難（kernel VAD 層） | 很難（loader 驗章） | 需 kernel exploit 才能繞過 |

## 踩雷集錦

1. **「ACG 和 NX（DEP）是同一件事」**：錯。NX/DEP 禁止在資料頁執行程式碼；ACG 禁止把資料頁升格為可執行。NX 擋的是「直接在 stack/heap 跳」，ACG 擋的是「先 VirtualProtect 再跳」。兩者互補，NX 是硬體/OS 層的屬性強制，ACG 是頁面生命週期管理的限制。

2. **「CIG 開了就萬無一失」**：錯。CIG 只擋未簽章 DLL 的載入，擋不住 data-only 攻擊，也擋不住已載入的有簽章 DLL 的記憶體內容被改寫（改 .data section、改物件欄位）。攻擊者繞到 data-only 路線後，CIG 完全無效。

3. **「ACG 開了，ROP 就不用擔心了」**：錯。ACG 擋的是注入新程式碼；ROP 靠的是已有的 gadget，完全不需要新的可執行頁面。ROP 仍然可行——但在 CFG+XFG+CET 都開的情況下，ROP 被其他緩解打壓。ACG 和 ROP 保護是正交關係。

4. **「out-of-process JIT 讓 OOP-JIT 通道不是攻擊面」**：恰恰相反。把 JIT 移到另一個 process，創造了一個 renderer → JIT process 的 IPC 通道，而這個 IPC 的兩端都是複雜的攻擊面（IPC 訊息格式、共享記憶體映射的邊界）。Pwn2Own 歷年有多個利用這個通道的 Edge 沙箱逃逸案例。

5. **「SetProcessMitigationPolicy 設了就能改回來」**：不能。`ProhibitDynamicCode = 1` 設好之後，在同一個 process 的生命週期內不能撤銷——這是設計上的有意決定，確保攻擊者就算能呼叫 API 也無法把緩解關掉。`AllowRemoteDowngrade = 0` 進一步禁止父 process 從外部移除這個設定。

## 進階：再往深一層

### VAD 結構裡的 ACG 旗標

當 ACG 啟用時，kernel 在 `EPROCESS` 的某個 Mitigation Policy 欄位設旗標。你可以在 WinDbg 裡查（kernel debug 環境）：

```
// 未實測，需 kernel debug 環境
// dt nt!_EPROCESS [addr] 查 MitigationFlags / MitigationFlagsEx 欄位
// 或透過 NtQueryInformationProcess + ProcessMitigationPolicy 從 userland 查
```

### ACG bypass 研究現狀

截至 2025 年的公開研究，ACG 的 userland 繞過幾乎不存在——現有的繞過都依賴一個前提：你已經有 kernel 層的原語（kernel exploit、驅動漏洞）。這和 browser_pwn 課裡的觀察一致：V8 sandbox escape 本身需要沙箱外的讀寫，而沙箱外到完整 OS 控制的路，最終都要經過 kernel 或 privilege escalation。

### 微軟自簽的「後門」

CIG 的「只允許 Microsoft 簽章」有一個有趣的副產品：攻擊者如果能入侵微軟的簽章基礎設施，或找到一個簽章有效但有漏洞的微軟 DLL，就可以把它注入 CIG 保護的 process。這正是 BYOVD（Bring Your Own Vulnerable Driver）在 kernel 層的類比——在 userland 是「帶一個有漏洞的已簽章 DLL」。公開案例：CVE-2021-34486（Windows 已簽章模組的漏洞）。

## 動手練習

用 Python + ctypes 直接觀察 ACG 的行為（不需要 MSVC）。

建立一個 Python 腳本，在**當前 process** 裡嘗試 `VirtualAlloc(PAGE_EXECUTE_READWRITE)`，記錄成功失敗；再嘗試啟用 ACG，然後再試一次 `VirtualAlloc(RWX)`，觀察 API 行為的差異。

```python
# 未實測，理論預期（Python 3.12 + ctypes on Windows 11 x64）
# 期望：啟用 ACG 前 VirtualAlloc(RWX) 成功；啟用後失敗

import ctypes
import ctypes.wintypes as wt
from ctypes import windll, Structure, c_uint32, c_int, POINTER, byref, sizeof

kernel32 = windll.kernel32
ntdll    = windll.ntdll

MEM_COMMIT              = 0x1000
MEM_RESERVE             = 0x2000
PAGE_EXECUTE_READWRITE  = 0x40
PAGE_READWRITE          = 0x04
PAGE_EXECUTE_READ       = 0x20

# 1. 在 ACG 啟用前，VirtualAlloc(RWX) 應該成功
ptr = kernel32.VirtualAlloc(None, 0x1000, MEM_COMMIT | MEM_RESERVE,
                             PAGE_EXECUTE_READWRITE)
print(f"Before ACG: VirtualAlloc(RWX) = {hex(ptr) if ptr else 'NULL'}")
if ptr:
    kernel32.VirtualFree(ptr, 0, 0x8000)  # MEM_RELEASE

# 2. 啟用 ACG（ProcessDynamicCodePolicy = 6）
class PROCESS_MITIGATION_DYNAMIC_CODE_POLICY(Structure):
    _fields_ = [("Flags", c_uint32)]

policy = PROCESS_MITIGATION_DYNAMIC_CODE_POLICY()
policy.Flags = 1  # ProhibitDynamicCode = bit 0

ProcessDynamicCodePolicy = 6
ret = kernel32.SetProcessMitigationPolicy(
    ProcessDynamicCodePolicy,
    byref(policy),
    sizeof(policy)
)
print(f"SetProcessMitigationPolicy(ACG): {'OK' if ret else f'FAILED err={kernel32.GetLastError()}'}")

# 3. ACG 啟用後，VirtualAlloc(RWX) 應該失敗
ptr = kernel32.VirtualAlloc(None, 0x1000, MEM_COMMIT | MEM_RESERVE,
                             PAGE_EXECUTE_READWRITE)
err = kernel32.GetLastError()
print(f"After ACG: VirtualAlloc(RWX) = {hex(ptr) if ptr else 'NULL'}, err={err}")
# 預期：ptr = 0 (NULL)，err = 5 (ERROR_ACCESS_DENIED) 或 87 (ERROR_INVALID_PARAMETER)

# 4. ACG 啟用後，VirtualAlloc(RW) 然後 VirtualProtect(RX) 應該也失敗
ptr2 = kernel32.VirtualAlloc(None, 0x1000, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE)
print(f"After ACG: VirtualAlloc(RW) = {hex(ptr2) if ptr2 else 'NULL'}")
if ptr2:
    old_prot = c_uint32(0)
    ok = kernel32.VirtualProtect(ptr2, 0x1000, PAGE_EXECUTE_READ, byref(old_prot))
    err2 = kernel32.GetLastError()
    print(f"After ACG: VirtualProtect(RX) = {'OK' if ok else f'FAILED err={err2}'}")
    # 預期：ok = False，err2 = 5 (ERROR_ACCESS_DENIED)
    kernel32.VirtualFree(ptr2, 0, 0x8000)
```

> **注意**：這個腳本在 ACG 啟用後，當前 Python 直譯器 process 也受到 ACG 約束——如果 Python 的垃圾回收或其他元件需要動態程式碼（少數版本的 CPython 有 JIT），可能導致後續行為異常。建議用一個最小的測試腳本，在啟用 ACG 後立即觀察結果。此腳本尚未實跑，為理論預期行為。

## 本章重點整理

- **ACG** 從 kernel VAD 層禁止動態建立或升格可執行頁面，讓「注入 shellcode 再跳」在 userland 完全沒有繞過方法。
- **CIG** 從 PE loader 層禁止載入未簽章映像，封死 DLL 注入路徑。
- **兩者組合**後，攻擊者在目標 process 裡唯一可行的路是 **data-only**（不注入程式碼、不改控制流、只改資料）——下一章的主題。
- **ACG vs JIT** 是工程上的硬衝突；Edge 的解法是 out-of-process JIT，把 JIT 移到沒有 ACG 的獨立 process，透過共享記憶體映射回 renderer——但這個 IPC 通道本身成為新攻擊面。

## 自我檢核

- [ ] 不看筆記，能說出 ACG 的三條禁令分別是什麼，以及它們在 kernel 的哪一層被強制執行（VAD 層，不是 API hook）
- [ ] 面試被問「ACG 和 DEP/NX 有什麼差」——能說清楚兩者防護的是不同階段（DEP 擋在資料頁執行、ACG 擋把資料頁升格為可執行）
- [ ] 能畫出 out-of-process JIT 的架構圖，說明 renderer 怎麼拿到 JIT 生成的程式碼，以及這個架構引入的攻擊面在哪
- [ ] 知道為什麼 data-only 攻擊能完全繞過 ACG+CIG（因為 data-only 不需要建立任何可執行頁面、不需要載入任何 DLL）
- [ ] 能解釋 ACG 和 ROP 之間的關係（正交：ACG 不擋 ROP；ROP 被 CFG+XFG+CET 打壓，不是被 ACG）

## 延伸閱讀

### 官方文件

- **[Process Mitigation Policies — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setprocessmitigationpolicy)**
  - **讀哪裡**：`SetProcessMitigationPolicy` 的每個 `PROCESS_MITIGATION_POLICY` 列舉值，尤其是 `ProcessDynamicCodePolicy`（ACG）和 `ProcessSignaturePolicy`（CIG）的結構欄位定義
  - **和本章關聯**：這是 ACG/CIG 啟用的 API 的官方仲裁；結構定義的位元欄位說明在這裡最準確
  - **前提知識**：本章讀完即可

- **[Mitigating arbitrary native code execution in Microsoft Edge — Microsoft Security Blog](https://blogs.windows.com/msedgedev/2017/02/23/mitigating-arbitrary-native-code-execution/)**
  - **讀哪裡**：完整文章（2017 年，ACG 首次公開說明）
  - **和本章關聯**：微軟官方對 ACG 設計動機、VAD 機制、JIT 衝突與 out-of-process JIT 架構的第一手說明；本章 OOP-JIT 節的主要參照
  - **前提知識**：了解 VirtualAlloc/VirtualProtect 基本 API 語意

### 研究論文 / 安全報告

- **[Code Integrity in Edge — Google Project Zero Blog](https://googleprojectzero.blogspot.com/2017/03/the-ring-0-prize-at-pwn2own-2017.html)**（James Forshaw 等人）
  - **讀哪裡**：針對 Pwn2Own 2017 Edge 沙箱逃逸的分析段落，其中涉及 ACG/CIG 的限制與繞過研究方向
  - **和本章關聯**：從攻擊者視角理解 ACG+CIG 的實際防護強度

- **[Windows Process Injection: Dynamic-link Library Injection — MITRE ATT&CK T1055](https://attack.mitre.org/techniques/T1055/)**
  - **讀哪裡**：T1055 的子技術列表，理解 CIG 能擋哪些、擋不到哪些
  - **和本章關聯**：對照本章的「ACG+CIG 能擋什麼、擋不到什麼」表格

### 部落格

- **[Connor McGarr — Windows Process Injection Revisited](https://connormcgarr.github.io/process-injection/)**
  - **讀哪裡**：涉及 ACG/CIG 約束下的注入技法分析段落
  - **和本章關聯**：從現代緩解的視角重新審視注入技法，補充本章的攻擊面分析
  - **前提知識**：熟悉 Windows process 記憶體管理基礎（Ch 9）

- **[Azy Sawyer — Out-of-Process JIT in Edge (2017)](https://blogs.windows.com/msedgedev/2017/03/07/improved-javascript-performance-responsiveness-compatibility/)**
  - **讀哪裡**：技術說明段落（JavaScript JIT 效能改進部分），看 OOP-JIT 的架構細節
  - **和本章關聯**：OOP-JIT 的工程視角，補充本章的安全分析

ACG 和 CIG 把「注入程式碼讓它執行」這條路封死之後，攻擊者唯一剩下的高通道是改資料而不改程式流——也就是整個 data-only 攻擊家族，它更難偵測、更難防禦，而且圖靈完備。

→ [Ch 37 — data-only attacks：繞過所有 CFI](./37-data-only-attacks.md)
