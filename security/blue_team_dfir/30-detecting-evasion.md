# Ch 30 — 對抗規避：偵測 AMSI bypass / ETW patching / Unhooking / 混淆

> 目標：從防守方視角理解每種規避技術留下什麼可偵測的痕跡，掌握為什麼「看行為而非看 signature」在對抗規避時是唯一有效策略，並知道 EDR 的盲點在哪裡以及如何補償。

## 為什麼規避偵測這麼難抓？

你身為攻擊者已經知道答案：這些規避手法的本質，是**把遙測管道本身破壞掉**，或者讓你的行為看起來和正常一樣。

- AMSI bypass → 破壞 PowerShell/VBScript 的掃描通道，讓惡意腳本在記憶體裡執行但 AV/EDR 掃不到
- ETW patching → 讓 Windows 的事件記錄機制在你的程序裡靜音
- Unhooking / 直接 syscall → 繞過 EDR 掛在 userland 的監控 hook，直接進 kernel

問題是：**破壞遙測的行為本身也是可偵測的**，只要你知道要看哪裡。這是本章的核心。

## AMSI（Anti-Malware Scan Interface）Bypass 偵測

### 你在攻擊方做了什麼

最常見的 AMSI bypass：在 `amsi.dll` 的 `AmsiScanBuffer` 函數開頭打 patch，讓它永遠回傳 `AMSI_RESULT_CLEAN`（0）：

```c
// 把 AmsiScanBuffer 的前幾個 byte 改成：
// mov rax, 0x80070057  ; E_INVALIDARG
// ret
// 或直接 xor rax, rax; ret
```

另一種：設定 `amsiContext` 裡的某個欄位讓後續呼叫跳過掃描（更隱蔽，不需要 patch 程式碼）。

PowerShell 常見的 bypass 方式也包含：`[Ref].Assembly.GetType('System.Management.Automation.AmsiUtils')` 然後修改相關欄位。

### 防守方看到什麼

**記憶體特徵**：已知 AMSI bypass patch 的 byte pattern 可以用 YARA 掃。但這是 signature 層面，攻擊者換幾個 byte 就可以繞。

**更可靠的偵測方式**：

1. **寫入 amsi.dll 程式碼段的行為**：任何程序修改自己載入的 DLL 的可執行頁面，都是高可疑行為。這對應的是把「W^X」(write-xor-execute) 原則的違反，可以用 ETW 的 `Microsoft-Windows-Kernel-Process` provider 抓到記憶體保護屬性變更。

2. **AMSI provider 回傳值異常**：正常的 AMSI 掃描對無害內容回傳 1（CLEAN），對惡意內容回傳非 1 值。如果一個 PowerShell session 大量執行腳本，但 AMSI 的掃描記錄完全消失（因為 provider 被 patch 掉），這個缺失本身就是信號。前提是你有完整的 AMSI provider 日誌 baseline。

3. **Sysmon Event 10（process access）配合 Event 8（CreateRemoteThread）**：AMSI bypass 通常需要在目標程序（如 powershell.exe）的記憶體裡做操作，會產生跨程序的 handle 存取記錄。

```kql
// KQL - 找可疑的程序間記憶體寫入操作（示意）
// PROCESS_VM_WRITE = 0x20, PROCESS_VM_OPERATION = 0x8
DeviceEvents
| where Timestamp > ago(7d)
| where ActionType == "OpenProcess"
| where AdditionalFields has_any ("0x001fffff", "PROCESS_ALL_ACCESS")
// 或 Sysmon Event 10 的 GrantedAccess 欄位
| where InitiatingProcessFileName !in~ (
    "MsMpEng.exe",  // Windows Defender
    "svchost.exe",
    "csrss.exe"
  )
| project Timestamp, DeviceName, AccountName,
          InitiatingProcessFileName,
          AdditionalFields  // 包含目標程序和 GrantedAccess
```

4. **Windows Defender AMSI Event**：Event ID 1116（Defender detection）和 1117（Defender action）。更關鍵的是：如果你的環境裡曾有大量 AMSI scan 事件，然後某台機器突然一條都沒有，這台機器可能被 bypass 了。

### 行為對比

| 情境 | 正常行為 | 可疑行為 |
|---|---|---|
| AMSI 掃描 | 每次 PowerShell 腳本執行都有對應的 AMSI scan record | 長時間無掃描記錄但 PS 仍在執行 |
| 記憶體保護 | DLL 程式碼段為 PAGE_EXECUTE_READ | 動態改為 PAGE_EXECUTE_READWRITE |
| 程序存取 | AV/EDR 程序讀取其他程序記憶體 | 非安全工具對 powershell.exe 做 PROCESS_VM_WRITE |

## ETW（Event Tracing for Windows）Patching 偵測

### 你在攻擊方做了什麼

ETW 是 Windows 遙測的底層管道，幾乎所有 security event 都通過它。常見的 ETW 破壞手法：

1. **Patch `EtwEventWrite` in ntdll.dll**：讓這個函數直接 `ret`，所有使用這個函數寫事件的程式碼都靜音。這是 per-process 的，只影響被 patch 的程序自己。

2. **Patch kernel `NtTraceEvent`**：更激進，直接把 kernel 裡的 ETW 寫入路徑 patch 掉。需要 kernel access，但更難偵測。

3. **Provider-specific patching**：針對特定的 ETW provider（如 `Microsoft-Windows-DotNETRuntime`）做 unhook，只讓 .NET 遙測消失。

### 防守方看到什麼

**Kernel Telemetry 補漏**：userland 的 ETW 被 patch，kernel level 的遙測是獨立的。Microsoft Defender for Endpoint（MDE）等現代 EDR 透過 kernel driver 直接接收 kernel 事件，繞過 userland 的 ntdll.dll，所以即使 `EtwEventWrite` 被 patch，kernel telemetry 不受影響。

**遙測缺失偵測**：如果一個程序正在執行，正常情況下應該產生 ETW 事件（程序建立、模組載入、執行緒建立...），但突然什麼事件都沒有，這個「靜默程序」本身就是可疑信號。這需要你有足夠密集的遙測 baseline 才能偵測。

```kql
// KQL - 找長時間無任何遙測的活躍程序（示意）
// 這個查詢邏輯：找在程序列表裡存在但幾乎沒有對應 Event 的程序
let active_processes = DeviceProcessEvents
| where Timestamp > ago(1h)
| distinct DeviceName, ProcessId = InitiatingProcessId;

let processes_with_events = DeviceEvents
| where Timestamp > ago(1h)
| where ActionType != "ProcessCreate"
| distinct DeviceName, ProcessId = InitiatingProcessId;

active_processes
| join kind=leftanti processes_with_events on DeviceName, ProcessId
// 結果（示意）：在活躍但完全沒有其他事件的程序 ID
```

**直接偵測 ntdll patch**：
- Sysmon Event 7（Image Loaded）看模組載入，結合已知模組的雜湊值；但 ETW patch 是 in-memory 的，不改磁碟上的 DLL，這個方法效果有限
- 更有效：用 EDR 的記憶體掃描功能定期讀取活躍程序裡 ntdll.dll 的 `EtwEventWrite` 函數起始 byte，和已知乾淨版本比對

## Unhooking / 直接 Syscall（Direct Syscall）偵測

### 你在攻擊方做了什麼

EDR 透過把自己的 DLL 注入所有程序、或在 ntdll.dll 的關鍵函數開頭插入 hook，來監控 API 呼叫。bypass 手法：

1. **Unhooking**：從磁碟或 KnownDlls 重新載入一份乾淨的 ntdll.dll，用它覆蓋記憶體中被 hook 的版本
2. **直接 Syscall**：不通過 ntdll.dll，直接在組合語言裡寫 `syscall` 指令，用正確的 syscall number（SSN）呼叫 kernel
3. **間接 Syscall（Indirect syscall）**：借用 ntdll.dll 裡現有的 `syscall` 指令（gadget），跳進去執行，讓 call stack 看起來像從 ntdll 發出

### 防守方看到什麼

**Kernel telemetry 的重要性**：不管攻擊者怎麼繞 userland hook，系統呼叫最終都要進 kernel。現代 EDR 的 kernel driver 在 kernel level 監控 syscall，不受 unhooking 影響。這是為什麼 BYOVD（Bring Your Own Vulnerable Driver）攻擊要先把 EDR 的 kernel driver 關掉。

**Call stack 異常偵測**：正常的 Windows API 呼叫，call stack 應該是：
```
程序程式碼 → kernel32.dll → ntdll.dll → [syscall] → kernel
```

如果 call stack 顯示 syscall 發生時，沒有 ntdll.dll 的 frame（直接 syscall），或者 ntdll frame 不在預期位置（間接 syscall 的 gadget 用法），這是 EDR 可以偵測的信號。Microsoft Defender for Endpoint 從 2023 年開始把 call stack 驗證整合進偵測邏輯。

```kql
// KQL - Defender 對 suspicious API call chain 的告警（示意）
AlertInfo
| where Timestamp > ago(7d)
| where Title has_any (
    "Suspicious API", "Direct syscall",
    "Process injection", "Suspicious memory allocation"
  )
| join AlertEvidence on AlertId
| project Timestamp, DeviceName, AccountName, Title, EvidenceRole, EntityType
```

**模組載入分析**：Unhooking 時通常需要重新載入 ntdll.dll，可能產生額外的模組載入事件。Sysmon Event 7 搭配異常的模組路徑（從 temp 目錄載入）或同一個 DLL 被載入兩次，是 indirect unhooking 的信號。

```kql
// KQL - 找從非標準路徑載入 ntdll.dll 的情況（示意）
DeviceImageLoadEvents
| where Timestamp > ago(7d)
| where FileName =~ "ntdll.dll"
| where not (FolderPath startswith @"C:\Windows\System32")
| project Timestamp, DeviceName, ProcessId = InitiatingProcessId,
          InitiatingProcessFileName, FolderPath, SHA256
```

## Obfuscation / Packer 偵測

### 常見的混淆手法

攻擊者用來規避 signature 的手法：

- **字串混淆**：把 `"powershell"` 換成 `"p"+"o"+"w"+"e"+"r"+"s"+"h"+"e"+"l"+"l"` 或字元碼拼接
- **Base64 + 多層 IEX**：`IEX(IEX([System.Text.Encoding]::Unicode.GetString([Convert]::FromBase64String(...))))`
- **AMSI bypass + 再 IEX**：先 bypass AMSI，再執行實際 payload
- **Packer**：把 PE 封裝，runtime 解壓執行，.text section 上磁碟時是加密的
- **Shellcode runner**：把 shellcode 藏在 .rsrc 或其他 section，runtime 解密到記憶體執行

### 偵測策略：看行為而非看 signature

**為什麼 signature 在對抗混淆時必敗**：每次 base64 encode 的結果不同，每個 packer 的加密密鑰不同，每個版本的混淆器輸出不同。你追著 signature 跑，攻擊者重新 encode 一次就繞了。

行為偵測在這裡勝出，原因：

1. 混淆可以讓程式碼內容難以辨認，但執行後的**行為**不變：一個下載器還是要建立 TCP 連線，一個 injector 還是要呼叫 `VirtualAllocEx` 和 `WriteProcessMemory`
2. Script Block Logging（Event 4104）在 PowerShell 引擎**解碼後、執行前**記錄腳本，所以多層混淆最後都被展開了
3. Packer 執行後，在記憶體裡的 PE 是正常的，YARA 記憶體掃描可以找到

**Entropy 分析**：高 entropy 的 PE section 是 packer 的特徵（加密內容的 entropy 接近最大值 8.0）：

```python
# Python 範例：計算 PE section entropy（非 SIEM 查詢，用於靜態分析）
import pefile, math, collections

def entropy(data):
    if not data:
        return 0
    counter = collections.Counter(data)
    length = len(data)
    return -sum((count/length) * math.log2(count/length)
                for count in counter.values())

pe = pefile.PE("sample.exe")
for section in pe.sections:
    ent = entropy(section.get_data())
    name = section.Name.decode(errors='replace').strip()
    print(f"{name}: entropy={ent:.2f}")
# .text section entropy > 7.0 高度可疑（正常 .text 大概 5-6）
```

**YARA 記憶體掃描**：對執行中的程序做記憶體掃描，找 unpacked 後的 PE 特徵，或已知的 shellcode 模式：

```yara
rule Suspicious_Memory_PE {
    meta:
        description = "在非標準記憶體區域找到 PE header"
    strings:
        $mz = { 4D 5A }  // MZ header
        $pe = { 50 45 00 00 }  // PE header
    condition:
        $mz at 0 and $pe
        // 配合記憶體 region 屬性：executable 但不是已知模組 → 可疑
}
```

## EDR 盲點與補償控制

誠實說清楚 EDR 的盲點，才能知道要補什麼：

### EDR 的典型盲點

| 盲點 | 原因 | 補償控制 |
|---|---|---|
| Kernel-level bypass（BYOVD）| EDR kernel driver 被合法漏洞 driver 關掉 | 用 WDAC 或 Hypervisor Protected Code Integrity（HVCI）鎖定 kernel driver 載入 |
| Encrypted C2 over HTTPS | EDR 看不到加密內容 | 網路 TLS inspection、proxy 上的 JA3 指紋分析 |
| 合法工具的合法使用 | EDR 對 LOLBin 的判斷依賴規則，不是魔法 | Hunting + 人工分析 + 行為基線 |
| Memory-only fileless | 沒有落地的 PE，disk-based 掃描無效 | 記憶體掃描（Volatility、Velociraptor 的 memory scan）|
| AMSI/ETW 被 bypass 後 | 遙測通道損毀 | Kernel telemetry（EDR kernel driver）+ network telemetry |
| Supply chain / 簽署程式碼 | 合法簽名的惡意程式碼通過 signature 驗證 | 行為分析、sandboxing、TI 整合 |

### 補償控制架構

```
       Endpoint
   ┌────────────────────────────────────┐
   │  EDR（userland + kernel driver）   │ ← 主線，但有盲點
   │  AMSI providers（AV 掃描）         │ ← bypass 可停用
   │  Script Block Logging（4104）      │ ← ETW 依賴，可被干擾
   └────────────────────────────────────┘
              │                    │
              ▼                    ▼
       Kernel Telemetry        Network Telemetry
   ┌──────────────────┐    ┌──────────────────────┐
   │ Windows ETW      │    │ Proxy / Firewall logs │
   │ (kernel providers│    │ DNS logs              │
   │  bypass-resistant│    │ NetFlow / Zeek        │
   │  in theory)      │    │ TLS JA3 fingerprint   │
   └──────────────────┘    └──────────────────────┘
              │
              ▼
       SIEM 關聯
   ┌──────────────────────────────────────┐
   │ 跨資料來源關聯：endpoint + network   │
   │ 遙測缺失告警（沉默程序）             │
   │ Threat Hunting（主動假設驅動）       │
   └──────────────────────────────────────┘
```

**關鍵原則**：沒有任何單一控制是完整的。縱深防禦不是堆工具，而是確保每個盲點有另一層能補上。

## 範例：完整的規避鏈偵測思路

**攻擊者的完整規避流程**：
1. PowerShell `-enc` 混淆命令（規避 cmdline 掃描）
2. 記憶體中 patch `AmsiScanBuffer`（bypass AMSI）
3. 下載 shellcode 到記憶體，allocate RWX page 執行
4. 從 ntdll.dll 重新映射乾淨版本 unhook EDR（規避 userland hook）
5. 直接 syscall 呼叫 `NtCreateThreadEx` 注入 svchost.exe

**防守方每步能看到什麼**：

| 步驟 | 防守端遙測 | 偵測可能性 |
|---|---|---|
| 1. `-enc` PowerShell | Sysmon Event 1（cmdline 有 `-enc`），Event 4104（解碼後腳本）| 高（4104 展開混淆）|
| 2. AMSI patch | 記憶體保護屬性變更（RW on code），程序對自身 amsi.dll 做 VM_WRITE | 中（EDR kernel driver 可見）|
| 3. RWX allocation | API 呼叫 VirtualAlloc(MEM_COMMIT, PAGE_EXECUTE_READWRITE) | 高（行為告警）|
| 4. Unhooking | ntdll.dll 從非標準路徑載入，或同一 DLL 二次載入 | 中（Sysmon Event 7）|
| 5. Syscall injection | Call stack 沒有 ntdll frame，kernel telemetry 看到 NtCreateThread | 中高（現代 EDR + kernel）|

任何一步偵測到，就有機會。全部都繞掉需要高技術，且每一個 bypass 動作本身都有對應的 artifact。

## 踩雷

1. **「偵測 AMSI bypass」本身依賴遙測完整性**：如果 AMSI 已經被 bypass，那你設計的偵測可能已經在你看到它之前就失效了。所以偵測 AMSI bypass 的最可靠方式是看 bypass 動作本身（記憶體寫入），而不是看 AMSI 的輸出是否異常。

2. **Script Block Logging 不是萬能**：Event 4104 在 Script Block Logging 被關閉時就沒有了。攻擊者可以在執行惡意腳本前先關掉這個設定（需要管理員權限）。監控 PowerShell Engine Logging 的 GPO 修改（Registry Event）可以補上這個盲點。

3. **Call stack 驗證有 false positive**：某些合法的 hooking 工具（Detouring、API Monitor）和某些安全工具自己也會讓 call stack 看起來非標準。在生產環境啟用 call stack 驗證前，需要充分 baseline 測試。

4. **Kernel telemetry 不等於萬無一失**：BYOVD 攻擊（用含漏洞的合法驅動載入 kernel shellcode）可以破壞 EDR 的 kernel driver。防禦這個需要 HVCI（Hypervisor Protected Code Integrity）或 Secure Boot 配合的 kernel integrity 機制，而不只是 EDR。

5. **Entropy 分析的 false positive 很高**：加密合法程式（如 .NET JIT 後的程式碼）、高度壓縮的資料 section 都有高 entropy。純靠 entropy 篩選會產生大量噪音。要配合其他行為信號一起看。

## 進階延伸

- **Process Hollowing / Doppelgänging 偵測**：這些技術讓惡意程式碼以合法程序的身分執行，偵測依賴記憶體 PE 和磁碟 PE 的 hash 比對（記憶體 scan 工具如 pe-sieve、Moneta）。
- **JA3/JA4 TLS Fingerprinting**：即使 C2 流量加密，TLS 握手的指紋仍然可以識別 C2 框架（Cobalt Strike 有已知 JA3 hash，雖然攻擊者可以修改）。在 Zeek 或 Suricata 啟用 JA3 logging。
- **Sigma 規則 for evasion**：[SigmaHQ](https://github.com/SigmaHQ/sigma) 有大量針對 AMSI bypass、ETW patching、unhooking 的現成規則，先部署這些，再在自己環境裡調整閾值。

## 本章重點整理

- AMSI bypass 最可靠的偵測是看 bypass 動作（記憶體寫入 amsi.dll 程式碼段），而非依賴 AMSI 輸出
- ETW patching 讓遙測消失，所以要偵測「遙測的缺失」，且 kernel telemetry 是對抗 userland ETW patch 的關鍵
- Unhooking / 直接 syscall 可繞過 userland EDR hook，但 kernel driver 和 call stack 驗證能補上
- 混淆規避 signature，行為在 script block 展開後依然暴露；packer 在記憶體裡展開後可被 YARA 掃到
- EDR 有盲點，縱深防禦（endpoint + network + kernel telemetry 關聯）才能補全

## 自我檢核

- [ ] 我能說出偵測 AMSI bypass 為什麼不能只看 AMSI 輸出，應該看什麼
- [ ] 我能解釋 ETW patching 對 userland telemetry 的影響，以及 kernel telemetry 為什麼是補償
- [ ] 我能說出 unhooking 和直接 syscall 在 call stack 上留下什麼異常
- [ ] 我知道 EDR 的至少三個盲點，以及對應的補償控制
- [ ] 給我一個規避技術，我能說出它在哪個遙測層留下什麼痕跡

## 延伸閱讀

1. **[MDSec - A Deep Dive into Cobalt Strike Malleable C2](https://www.mdsec.co.uk/2021/02/a-deep-dive-into-cobalt-strike-malleable-c2/)** — 從攻擊者的規避工程角度看 EDR bypass 設計，理解了攻擊方設計才知道防守方要看哪裡。

2. **[RedOps - AMSI Bypass 完整分類](https://redops.at/en/blog/a-comparison-of-common-amsi-bypasses)** — 系統化整理所有 AMSI bypass 手法，每種手法的機制和對應偵測點。

3. **[Elastic Security Labs - Detecting Process Injection](https://www.elastic.co/security-labs/detecting-process-injection-with-windows-defender-edr)** — 用 Windows kernel telemetry 偵測 process injection，包含直接 syscall 的 call stack 分析方法。

4. **[SANS - AMSI as an Anti-Exploitation Feature](https://www.sans.org/blog/amsi-how-windows-10-plans-to-stop-script-based-attacks-and-how-well-it-does-it/)** — AMSI 的設計原理和侷限，了解它能做什麼、不能做什麼。

5. **[SigmaHQ - Defense Evasion Rules](https://github.com/SigmaHQ/sigma/tree/master/rules/windows/process_creation)** — 直接用別人已經寫好的 Sigma 規則，涵蓋 AMSI bypass、ETW patching、unhooking 場景；讀規則學偵測邏輯比自己從零想更有效率。

---

→ [練習 D：hypothesis-driven 狩獵](./practice-d-threat-hunt.md)
