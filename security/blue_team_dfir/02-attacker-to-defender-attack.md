# Ch 2 — 攻擊者視角轉防守：MITRE ATT&CK

> 目標：把你已經會的攻擊手法對應到 ATT&CK 分類框架，理解每一個 Technique 在防守端留下什麼可見的痕跡，以及如何用 ATT&CK 作為偵測工程的骨架而不只是查詢目錄。

## 為什麼需要一套共同語言

IOC（Indicators of Compromise）死得很快。一個 C2 IP 被封鎖之後，攻擊者換個 IP 繼續跑；一個惡意 hash 被標記之後，重新打包 payload 繞過。以 IOC 為核心的防守是在追蹤攻擊者最容易改變的那一層。

David Bianco 的 Pyramid of Pain（痛苦金字塔）把這個問題說清楚：

```
              /\
             /  \
            / TTP\   ← 最難換，換了等於重寫攻擊流程
           /------\
          /  Tools  \ ← 重新編譯就能繞，但需要工程成本
         /------------\
        /  Network/Host \ ← IP/Domain/Artifact，容易換
       /------------------\
      /   Hash (MD5/SHA1)  \ ← 一個 bit 就能換
     /----------------------\
```

TTP（Tactics, Techniques, Procedures）位在金字塔頂端。攻擊者可以換 IP、換 hash、換工具名稱，但如果要做 Pass-the-Hash 橫向移動，他就一定要在某個時間點對目標機器發送 Type 3 NTLM 認證；如果要 dump LSASS，他就一定要以某種方式開啟 lsass.exe 的記憶體讀取權限。這些行為特徵比任何 IOC 都更難被攻擊者抹去。

MITRE ATT&CK（Adversarial Tactics, Techniques, and Common Knowledge）就是把這些 TTP 系統化整理的知識庫，從 2015 年的 Windows 企業環境觀察開始，現在已經涵蓋 Enterprise（Windows/Linux/macOS/Cloud）、Mobile、ICS 三大矩陣。

## ATT&CK 矩陣結構：三個層級

在看矩陣之前，先把三個層級的概念分清楚：

```
Tactic（戰術目標）
│
├── Technique（技術手段）
│       T1003 OS Credential Dumping
│
└── Sub-technique（子技術手段）
        T1003.001  LSASS Memory
        T1003.002  Security Account Manager
        T1003.003  NTDS
        T1003.004  LSA Secrets
        T1003.005  Cached Domain Credentials
        T1003.006  DCSync
        T1003.007  Proc Filesystem（Linux）
        T1003.008  /etc/passwd and /etc/shadow（Linux）
```

### Tactics：攻擊者的目標是什麼

Enterprise 矩陣現在有 14 個 Tactics，按攻擊流程排列：

| Tactic ID | 名稱 | 問的問題 |
|-----------|------|---------|
| TA0043 | Reconnaissance（偵察） | 目標有什麼情報可以收集？ |
| TA0042 | Resource Development（資源建設） | 準備哪些基礎設施/工具/帳號？ |
| TA0001 | Initial Access（初始存取） | 怎麼進入目標環境的第一個立足點？ |
| TA0002 | Execution（執行） | 怎麼讓惡意程式碼跑起來？ |
| TA0003 | Persistence（持久化） | 重開機之後怎麼維持存取？ |
| TA0004 | Privilege Escalation（提權） | 怎麼從普通使用者變 SYSTEM/root？ |
| TA0005 | Defense Evasion（防禦規避） | 怎麼避免被偵測到？ |
| TA0006 | Credential Access（憑證存取） | 怎麼取得帳號密碼或 hash？ |
| TA0007 | Discovery（探索） | 環境裡有什麼可以利用的？ |
| TA0008 | Lateral Movement（橫向移動） | 怎麼從一台機器跳到另一台？ |
| TA0009 | Collection（資料收集） | 目標資料在哪、怎麼集中？ |
| TA0011 | Command and Control（命令控制） | 怎麼維持與 C2 的通訊？ |
| TA0010 | Exfiltration（資料外洩） | 怎麼把資料傳出去？ |
| TA0040 | Impact（衝擊） | 怎麼造成可見的破壞（加密、刪除）？ |

Tactic 本身不告訴你攻擊者做了什麼，只告訴你他的目標是什麼。一個 Tactic 之下可能有幾十個 Technique 對應不同手段。

### Techniques：攻擊者用什麼方法達成目標

Technique 是矩陣的主體，每個 Technique 有一個 T 開頭的四位數 ID，附帶：

- **Description**：這個手法的技術說明
- **Procedure Examples**：真實攻擊組/工具用過這個手法的紀錄
- **Detection**：理論上能偵測到的資料來源和方法
- **Mitigations**：防禦建議

### Sub-techniques：同一目標的不同實作路徑

Sub-technique 用小數點延伸，格式為 `T<number>.<三位數>`。這一層非常關鍵，因為同一個 Technique 底下的子技術可能對應截然不同的偵測手段——這個問題後面會詳細討論。

### Procedures：特定組織或工具的具體操作

Procedure 是最底層的具體行為，通常來自 CTI（Cyber Threat Intelligence，網路威脅情報）報告。例如「APT29 在 2020 年 SolarWinds 行動中，使用 TEARDROP dropper 執行 BEACON payload」這句話描述的就是一個 Procedure。ATT&CK 的 Procedure Examples 欄位把這些公開案例整理進來，讓你知道這個 Technique 在野外實際長什麼樣子。

## TTP vs IOC：本質差異

這個區別值得花時間理解清楚，因為它決定你建偵測規則的策略：

| 面向 | IOC | TTP |
|------|-----|-----|
| 具體例子 | `192.168.1.100`、`mimikatz.exe`、`hash: d3adb33f` | LSASS 記憶體讀取、Registry Run Key 寫入 |
| 生命週期 | 小時到天，攻擊者一換就失效 | 月到年，換了等於換整套攻擊方法論 |
| 偵測精準度 | 高（一打一） | 低到中（有誤報，需要 context） |
| 對攻擊者的痛苦 | 低（換個工具即可） | 高（需要重新設計攻擊手法） |
| 適用場景 | 事後封鎖、快速回應 | Threat Hunting、長期偵測建設 |

IOC 和 TTP 不是非此即彼，而是互補。IOC 讓你快速識別已知攻擊，TTP 讓你在攻擊者換了所有工具之後還能抓到他。

## 把你的攻擊知識對應到 ATT&CK

這一節把幾個你應該熟悉的攻擊手法翻到防守端，逐一對照。

### 憑證存取：T1003.001 LSASS Memory

**攻擊端在做什麼**

`sekurlsa::logonpasswords` 底層呼叫 `MiniDumpWriteDump` 或直接用 `ReadProcessMemory` 對 `lsass.exe` 的記憶體做讀取。完整流程：

```
攻擊者取得 SYSTEM 或 SeDebugPrivilege
  → OpenProcess(PROCESS_VM_READ, lsass.exe PID)
  → ReadProcessMemory / MiniDumpWriteDump
  → 解析 LSASS 記憶體結構取出 NTLM hash / Kerberos ticket
```

**防守端看到什麼**

Windows Event Log：

- **EventID 4656**（Security）：Object Handle Requested — 有程序要求開啟 lsass.exe 的 handle，欄位 `ObjectName` 為 `\Device\HarddiskVolume...\Windows\System32\lsass.exe`，`AccessMask` 包含 `0x1010`（PROCESS_VM_READ + PROCESS_QUERY_INFORMATION）
- **EventID 4663**（Security）：Object Access — 實際存取發生，記錄 `ProcessName`、`SubjectUserName`
- **Sysmon EventID 10**：ProcessAccess — 更細，直接記錄 `SourceImage`、`TargetImage`（lsass.exe）、`GrantedAccess`（access mask 的 hex 值）

Sysmon 的 EventID 10 是這個場景最值錢的事件源。規則的核心邏輯是：`TargetImage` 符合 `lsass.exe` 且 `GrantedAccess` 包含讀取記憶體的 bit（如 `0x1010`、`0x1410`、`0x143a`）。

誤報來源：防毒軟體、EDR 自身、某些監控工具也會讀取 lsass.exe 記憶體做完整性驗證。偵測規則需要排除這些已知良性的 SourceImage。

**LSASS Protection 的對抗**

Windows 8.1 起有 Protected Process Light（PPL）機制，讓 lsass.exe 以 PPL 身份執行，即使 SYSTEM 也無法直接 `OpenProcess` 讀取記憶體。攻擊者的對策：

- 載入已簽署但有漏洞的驅動（BYOVD），從 kernel 層繞過 PPL — 這時候防守端要看的不是 EventID 4656，而是驅動載入事件（EventID 7045、Sysmon EventID 6）
- 使用 `comsvcs.dll` 的 `MiniDump` export（`rundll32.exe C:\Windows\System32\comsvcs.dll MiniDump <lsass_pid> lsass.dmp full`）— 合法的 Windows 工具，WDigest 設定未改的環境仍然有效

這說明一個 Sub-technique 背後有多條攻擊路徑，每條路徑的偵測資料來源可能完全不同。

### 持久化：T1547.001 Registry Run Keys / T1053.005 Scheduled Task

**T1547.001 — Registry Run Keys**

攻擊端：寫入以下任一 registry key：
```
HKCU\Software\Microsoft\Windows\CurrentVersion\Run
HKLM\Software\Microsoft\Windows\CurrentVersion\Run
HKCU\Software\Microsoft\Windows\CurrentVersion\RunOnce
HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon\Userinit
```

防守端：
- **Sysmon EventID 13**：RegistryValueSet — 記錄 `TargetObject`（完整 key 路徑）、`Details`（寫入的值）、`Image`（寫入程序）
- **EventID 4657**（Security）：A registry value was modified — 需要先設定 SACL 才會記錄，預設沒有

偵測邏輯：監控上述 key path 的寫入，重點看 `Details` 欄位是否指向非標準位置（`%APPDATA%`、`%TEMP%`、`C:\Users\<user>\`）。

**T1053.005 — Scheduled Task**

攻擊端：`schtasks /create /tn "WindowsUpdate" /tr "C:\Users\user\AppData\Roaming\update.exe" /sc onlogon /ru SYSTEM`

防守端：
- **EventID 4698**（Security）：A scheduled task was created — 包含 `TaskName`、`TaskContent`（完整 XML），從 XML 裡可以看到 Actions/Command 和 Triggers
- **Sysmon EventID 11**：FileCreate — task 定義檔案寫入 `C:\Windows\System32\Tasks\` 目錄
- **EventID 4702**：A scheduled task was updated（攻擊者有時先建立無害任務再修改）

### 橫向移動：T1021.002 SMB / T1550.002 Pass the Hash

**攻擊端**

Pass-the-Hash 使用 NTLM hash 直接對目標機器做認證，不需要明文密碼：

```
mimikatz # sekurlsa::logonpasswords   ← 先取得 hash
mimikatz # sekurlsa::pth /user:Administrator /domain:corp.local \
           /ntlm:<hash> /run:cmd.exe
```

接著對目標機器用 SMB 連線（`net use \\target\C$ /user:corp\Administrator`）或 PsExec 做橫向移動。

**防守端**

目標機器上的 Security Event Log：
- **EventID 4624** Logon Type 3（Network logon）：`AuthenticationPackageName` 為 `NTLM`、`LogonProcessName` 為 `NtLmSsp`，`WorkstationName` 是來源機器
- **EventID 4776**：The computer attempted to validate the credentials — NTLM 認證請求，出現在目標機器或 DC（如果是 domain NTLM pass-through）
- **EventID 7045**（如果攻擊者用 PsExec）：A new service was installed — service name 通常是隨機字串，`ImagePath` 指向 `%SystemRoot%\<random>.exe`

關鍵偵測邏輯：Type 3 NTLM 認證本身合法，所以要看 context——同一個帳號在短時間內對多台機器做 NTLM Type 3 認證（fan-out 模式），或者 Admin 帳號從非標準 Workstation 登入。

### 防禦規避：T1055 Process Injection（後面詳細展開）和 T1562.001

**T1562.001 — Disable or Modify Tools**

攻擊端常見手法：
```powershell
Set-MpPreference -DisableRealtimeMonitoring $true      # 關 Defender 即時防護
netsh advfirewall set allprofiles state off            # 關防火牆
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows Defender" /v DisableAntiSpyware /t REG_DWORD /d 1
```

防守端：
- **EventID 7036**（System）：服務狀態改變，`WinDefend` service 停止
- **Sysmon EventID 13**：對 `HKLM\SOFTWARE\Policies\Microsoft\Windows Defender` 的寫入
- **EventID 4719**（Security）：System audit policy was changed — 攻擊者修改稽核政策關掉 event logging

## ATT&CK 作為偵測工程的骨架

ATT&CK 的 Detection 欄位為每個 Technique 列出 Data Sources，例如 T1003.001 列出：

```
Data Sources:
- Process: Process Access     → Sysmon EventID 10
- Process: OS API Execution   → ETW (Event Tracing for Windows)
- File: File Creation          → Sysmon EventID 11（dump 檔案落地）
- Command: Command Execution   → EventID 4688 / Sysmon EventID 1
```

這讓你可以從 Technique ID 推導出「我需要收集哪些 log 才有機會偵測這個手法」，而不是反過來從現有 log 猜測覆蓋了什麼。

建偵測工程的流程可以這樣走：

```
1. 選定要優先覆蓋的 Technique（依據威脅情報或 red team 評估）
2. 查 ATT&CK Data Sources → 確認日誌來源是否在收集
3. 設計偵測規則（SIGMA 規則或 SIEM query）
4. 用 purple team 演練驗證規則是否真的能 fire
5. 追蹤誤報率，調整條件
```

## ATT&CK Navigator：視覺化覆蓋缺口

ATT&CK Navigator（`https://mitre-attack.github.io/attack-navigator/`）是一個 web app，讓你在矩陣上標記每個 Technique 的狀態。

常見用法：

**覆蓋熱圖（Coverage Heat Map）**：把你現有的偵測規則逐一對應到 Technique，在 Navigator 上標記顏色。紅色（未覆蓋）集中在哪個 Tactic？這就是你的偵測盲區。

**威脅組對應**：Navigator 內建每個已知攻擊組（APT28、Lazarus Group 等）使用過的 Technique 集合。把特定 APT 的 layer 疊在你的覆蓋 layer 上，一眼看出你對這個組的覆蓋率。

**Red Team 結果標記**：每次 red team 演練之後，把成功執行的 Technique 標記起來。這些就是你的「已驗證繞過」清單，優先補。

Navigator 輸出的是 JSON layer 檔案，可以版本控制，讓偵測覆蓋率的演進有歷史記錄。

## 具體案例：Cobalt Strike 典型攻擊鏈的 TTP 對應

以一個常見的 Cobalt Strike 企業滲透鏈為例，把每個步驟對應到 ATT&CK，再看防守端的觀測點：

```
Initial Access
T1566.001 — Spearphishing Attachment
  攻擊：郵件附件含有 weaponized Word 文件（VBA macro 或 CVE-2021-40444 MSHTML）
  防守：Email gateway log（附件類型、寄件者域名 reputation）
        EventID 4688 / Sysmon EID 1：winword.exe 生成 child process
        Sysmon EID 11：winword.exe 在 %TEMP% 寫入可執行檔案

         ↓

Execution
T1059.001 — PowerShell
  攻擊：macro 執行 PowerShell stager 下載 beacon
        powershell.exe -nop -w hidden -enc <base64>
  防守：Sysmon EID 1：powershell.exe 的 CommandLine 含 -enc / -EncodedCommand
        PowerShell Script Block Logging（EventID 4104）：解碼後的真實指令
        Module Logging（EventID 4103）：Import-Module 呼叫

         ↓

Persistence
T1547.001 — Registry Run Keys
  攻擊：beacon 寫入 HKCU\Software\Microsoft\Windows\CurrentVersion\Run
  防守：Sysmon EID 13：RegistryValueSet，TargetObject 符合 Run key 路徑
        EventID 4657：registry 值修改（需 SACL）

         ↓

Credential Access
T1003.001 — LSASS Memory
  攻擊：beacon 執行 Mimikatz via reflective DLL injection
        sekurlsa::logonpasswords 取得所有已登入帳號的 hash
  防守：Sysmon EID 10：ProcessAccess，TargetImage=lsass.exe，
        GrantedAccess=0x1010 或 0x143a
        注意：reflective injection 讓 Mimikatz 不落地，
        所以 hash 不會出現在磁碟上，要靠記憶體讀取事件

         ↓

Lateral Movement
T1021.002 — SMB/Windows Admin Shares
（搭配 T1550.002 Pass-the-Hash）
  攻擊：用取得的 hash 對目標機器做 PtH，透過 SMB admin share 部署 beacon
        net use \\dc01\C$ /user:CORP\Administrator <hash>
  防守：目標機器 EventID 4624 Logon Type 3，AuthenticationPackageName=NTLM
        EventID 7045：新服務安裝（PsExec style execution）
        DC 上 EventID 4776：NTLM credential validation
```

這條鏈有幾個觀察：

1. 每個步驟留下的可觀測點分散在**不同機器**上（郵件 gateway、受害者主機、DC），需要把 log 集中到 SIEM 才能串起來。
2. 攻擊者在 PowerShell stager 用了 `-enc`（EncodedCommand）做混淆，但 PowerShell Script Block Logging 會在執行前解碼並記錄，這個機制是 T1059.001 偵測的核心。
3. Pass-the-Hash 的 NTLM Type 3 事件本身不稀有，真正有意義的是把它和前面的 LSASS dump 事件、後面的服務安裝事件串起來看——這是 Detection 工程從「點」到「鏈」的關鍵。

## 失敗/邊界案例：T1055 不是一個整體

T1055（Process Injection，程序注入）是防守端最容易被誤導的 Technique 之一，因為它在矩陣上是一個格子，但底下的子技術在偵測上幾乎沒有共通性：

### T1055.001 — DLL Injection

攻擊路徑：
```
OpenProcess → VirtualAllocEx → WriteProcessMemory → CreateRemoteThread
→ LoadLibrary(<malicious_dll_path>)
```

防守端的可觀測特徵：
- Sysmon EID 8：CreateRemoteThread — `SourceImage` 在陌生程序，`TargetImage` 在 explorer.exe 之類的合法程序
- Sysmon EID 7：ImageLoaded — 從非標準路徑載入 DLL（`%APPDATA%`、`%TEMP%`）
- DLL 一定要落地到磁碟（除非用 reflective loading），所以 Sysmon EID 11 有機會抓到寫入事件

### T1055.012 — Process Hollowing

攻擊路徑：
```
CreateProcess(SUSPENDED, svchost.exe)
→ NtUnmapViewOfSection（清空合法程序的記憶體）
→ VirtualAllocEx + WriteProcessMemory（寫入惡意程式碼）
→ SetThreadContext + ResumeThread
```

防守端的可觀測特徵：
- **完全不同**：這裡沒有 DLL 落地，也沒有 CreateRemoteThread
- 關鍵可觀測點：`CreateProcess` 建立 suspended 程序，緊接著 `NtUnmapViewOfSection` 呼叫
- Sysmon EID 1 + EID 8 組合看不夠，需要 ETW（Event Tracing for Windows）的 `Microsoft-Windows-Kernel-Process` provider 或 user-mode 的 `ntdll` API hook（EDR 的 hooking 層）
- 更可靠的偵測：Process 的 `ImageFileName` 和記憶體中實際執行的 section 不一致（memory forensics 才看得到，即時偵測需要 EDR 驅動層）

### 對比

| 子技術 | 可觀測事件 | 落地檔案？ | 偵測難度 |
|--------|-----------|-----------|---------|
| T1055.001 DLL Injection | Sysmon EID 7/8/11 | 是（DLL 落地） | 中 |
| T1055.002 Portable Executable Injection | Sysmon EID 8 + memory | 否 | 高 |
| T1055.003 Thread Execution Hijacking | 無標準 kernel event | 否 | 高 |
| T1055.012 Process Hollowing | ETW kernel process events | 否 | 高 |
| T1055.013 Process Doppelganging | NTFS TxF，幾乎無標準 event | 否（利用 TxF） | 極高 |

**結論**：把「有偵測到 T1055」當作一個整體來計算覆蓋率是誤導性的。T1055.001 你可能有 Sigma 規則覆蓋，但 T1055.012 和 T1055.013 在沒有 EDR kernel sensor 的環境下幾乎是盲點。Navigator 要分開標記每個 sub-technique 的覆蓋狀態。

## ATT&CK vs Kill Chain vs Diamond Model

| 框架 | 發展背景 | 粒度 | 最適合的用途 |
|------|---------|------|------------|
| ATT&CK | MITRE（2015）從 Windows 端點觀察歸納 | 細（Technique + Sub-technique） | 偵測規則建設、威脅 Hunting、Red Team 結果評估 |
| Cyber Kill Chain | Lockheed Martin（2011）從軍事任務步驟借鑒 | 粗（7 個步驟） | 向管理層溝通攻擊階段、事後階段分類 |
| Diamond Model | Caltagirone et al.（2013）情報分析框架 | 分析軸（4 個頂點） | CTI 分析、攻擊組屬性歸因、情報共享 |

**Kill Chain** 告訴你攻擊走到哪個階段（Reconnaissance → Weaponization → Delivery → Exploitation → Installation → Command & Control → Actions on Objectives），適合向非技術背景的管理層解釋「攻擊者現在到第幾步」。

**Diamond Model** 的四個頂點是 Adversary（攻擊者）、Capability（能力）、Infrastructure（基礎設施）、Victim（目標）。它是情報分析的工具，適合追問「這個攻擊組還有哪些 C2 infrastructure」、「這個能力和哪個已知組有關聯」。

**ATT&CK** 是這三個框架裡最適合工程化的：它可以直接對應到具體的 log field、寫成 Sigma 規則、標在 Navigator 上追蹤覆蓋率。

這三個框架不衝突，在實際 IR 案例中可以同時用：Kill Chain 描述事件的整體輪廓，Diamond Model 做歸因分析，ATT&CK 指導具體偵測工程。

## 進階延伸：ATT&CK 的侷限和補充

### 矩陣不能直接等同於偵測完整性

ATT&CK Detection 欄位描述的是**理論上**可以偵測的方式，不代表你實作這個規則之後就能在現實環境裡準確 fire。原因：

1. **Log 品質問題**：EventID 4656 需要先把 Object Access 的稽核政策打開，很多環境沒有開；Sysmon 需要部署且設定得當
2. **Baseline 缺乏**：NTLM Type 3 登入本身普遍，沒有 baseline 就無從判斷異常
3. **攻擊者的 living off the land 策略**：用 Windows 內建工具（WMI、certutil、bitsadmin）執行惡意行為，偵測規則的誤報率極高

### SIGMA 規則：把 ATT&CK 翻譯成可執行的規則

SIGMA（`https://github.com/SigmaHQ/sigma`）是一個平台無關的偵測規則格式，許多規則已經在 `tags` 欄位附上 ATT&CK Technique ID：

```yaml
title: LSASS Memory Access by Non-System Process
status: stable
tags:
    - attack.credential_access
    - attack.t1003.001
detection:
    selection:
        EventID: 10
        TargetImage|endswith: '\lsass.exe'
        GrantedAccess|contains:
            - '0x1010'
            - '0x1410'
            - '0x143a'
    filter:
        SourceImage|startswith:
            - 'C:\Windows\System32\'
            - 'C:\Program Files\'
    condition: selection and not filter
```

SIGMA 規則可以用 `sigmac` 或 `pySigma` 轉換成 Splunk SPL、Elastic EQL、Microsoft Sentinel KQL 等格式。維護 SIGMA 規則庫等同於維護你的 ATT&CK 覆蓋文件，兩者綁定管理。

### Sub-technique 的覆蓋必須逐一評估

一個常見錯誤是在 Navigator 上把整個 T1055 標成「已覆蓋」，因為你有一條針對 CreateRemoteThread 的規則。正確做法是逐一標記每個 sub-technique 的覆蓋狀態，並且區分「有規則」和「規則被 red team 驗證過有效」。

## 錯誤直覺 → 正確認識

**錯誤直覺**：ATT&CK 是攻擊工具的目錄，收集越多 Technique 就越安全。
**正確認識**：ATT&CK 是偵測工程的設計圖。知道 Technique 存在不等於能偵測，你需要確認對應的 log 來源有在收集、規則有寫、規則有被驗證。

**錯誤直覺**：一個 Technique 標記「有偵測」等於這個攻擊向量被覆蓋了。
**正確認識**：Sub-technique 層級才是有意義的覆蓋粒度。T1055 底下的 13 個子技術有截然不同的偵測手段，必須逐一評估。

**錯誤直覺**：把 IOC block list 做好就等同於 TTP-based 偵測。
**正確認識**：IOC 是被動式封鎖，只能阻擋已知樣本；TTP 偵測是行為式，能在攻擊者換了所有工具之後還能抓到他，但需要更多的 baseline 和調校工作。

**錯誤直覺**：EventID 4624 Type 3 NTLM 看到就代表 Pass-the-Hash 在發生。
**正確認識**：Type 3 NTLM 在任何正常 Windows 網路環境裡都很常見。有意義的偵測是把這個事件和其他 context（同帳號的多目標 fan-out、前序的憑證 dump 事件）串起來做關聯分析。

**錯誤直覺**：LSASS 被讀取一定是攻擊。
**正確認識**：EDR、防毒、監控工具本身都可能讀取 lsass.exe 記憶體做完整性驗證。偵測規則必須維護一個已知良性程序的 allowlist，否則誤報率會高到規則沒有實用價值。

## 本章重點整理

- ATT&CK Enterprise 矩陣有 14 個 Tactics，每個 Tactic 下有多個 Techniques，Techniques 再細分為 Sub-techniques；三層各有不同用途。
- TTP 位在 Pyramid of Pain 頂端，比 IOC 更難被攻擊者繞過，是長期偵測建設的基礎。
- 每個 ATT&CK Technique 對應具體的 Data Sources（EventID / Sysmon EID / ETW provider），從 Technique ID 可以推導需要收集哪些 log。
- T1055（Process Injection）是典型的「一個 Technique，多種截然不同的偵測需求」案例——DLL Injection 有磁碟 artifact，Process Hollowing 幾乎只能靠 EDR kernel sensor。
- ATT&CK Navigator 讓覆蓋率視覺化，要在 sub-technique 層級標記，不能在 technique 層級一刀切。
- ATT&CK、Kill Chain、Diamond Model 各有適用場景；ATT&CK 最適合工程化落地，Kill Chain 適合管理層溝通，Diamond Model 適合 CTI 歸因分析。
- 一條攻擊鏈的 log 分散在多台機器，需要 SIEM 集中後做關聯分析才能從「點」到「鏈」。

## 自我檢核

- [ ] 我能說出 ATT&CK 的三層結構（Tactic / Technique / Sub-technique）各自的用途，並且舉出 T1003 / T1003.001 作為具體例子。
- [ ] 我能解釋為什麼 TTP 比 IOC 更有防守價值，並且用 Pyramid of Pain 框架說明。
- [ ] 我知道 LSASS Memory dump（T1003.001）在 Windows Event Log 和 Sysmon 裡留下哪幾個 EventID，以及各自記錄什麼欄位。
- [ ] 我理解 Pass-the-Hash（T1550.002）為什麼單靠 EventID 4624 Type 3 難以準確偵測，以及正確的偵測需要什麼額外 context。
- [ ] 我能解釋為什麼 T1055.001（DLL Injection）和 T1055.012（Process Hollowing）需要不同的偵測資料來源。
- [ ] 我知道 ATT&CK Navigator 是什麼、怎麼用它產生偵測覆蓋熱圖，以及為什麼要在 sub-technique 層級標記而不是 technique 層級。
- [ ] 我能把一條典型 Cobalt Strike 攻擊鏈的五個步驟對應到對應的 ATT&CK Technique ID，並且說出每個步驟的主要可觀測 EventID。

## 延伸閱讀

1. **MITRE ATT&CK — Getting Started**（`https://attack.mitre.org/resources/getting-started/`）：官方入門資源，包含 ATT&CK 設計哲學和 Navigator 使用教學，是理解矩陣設計思路的第一手文件。

2. **The Pyramid of Pain — David Bianco**（`http://detect-respond.blogspot.com/2013/03/the-pyramid-of-pain.html`）：原始 blog post，2013 年的文章但核心邏輯至今不過時，詳細解釋為什麼 TTP 比 IOC 更有長期價值，每個防守端工程師都應該讀過一次。

3. **Threat Detection Using SIGMA Rules — SANS Reading Room**（`https://www.sans.org/reading-room/whitepapers/detection/paper/40038`）：SANS 白皮書，說明如何用 SIGMA 把 ATT&CK Technique 轉化為可執行的偵測規則，並且做跨 SIEM 平台的移植。

4. **The DFIR Report — Threat Intel**（`https://thedfirreport.com/`）：持續更新的真實 IR 案例報告，每篇報告都有完整的 ATT&CK TTP 對應、時間軸和偵測建議，是把矩陣理論接地氣的最好閱讀材料。

5. **MITRE Cyber Analytics Repository (CAR)**（`https://car.mitre.org/`）：ATT&CK 的配套分析庫，每條分析規則對應具體的 ATT&CK Technique，提供偽程式碼（pseudo-code）和 SIGMA 等格式，比 ATT&CK 主矩陣的 Detection 欄位更具工程可落地性。

ATT&CK 給了我們一張地圖，但知道地圖上有哪些地方，和真正能在那些地方設下偵測陷阱是兩回事。下一章把視角拉高，從個別 Technique 的偵測退回到整個事件應變（Incident Response）的生命週期，理解一次 IR 行動從頭到尾的結構和各階段的工作重心。

→ [Ch 3 IR 生命週期 PICERL](./03-ir-lifecycle-picerl.md)
