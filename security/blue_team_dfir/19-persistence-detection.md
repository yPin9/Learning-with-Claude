# Ch 19 — 持久化偵測全景

> 目標：系統化盤點 Windows 所有主要 persistence 向量（ATT&CK T1547/T1053/T1543/T1546 等），為每一種建立「植入方式 → 遺留 artifact → 偵測工具/Event ID」的完整對映，讓你面對任何一種 persistence 都知道去哪找、用什麼工具、看哪個 Event ID。
>
> 環境：Windows 10/11；工具 Sysinternals Autoruns、Velociraptor artifact、Sigma 規則；所有輸出標「（示意，依版本/樣本而異）」。

## 為什麼要系統化盤點 Persistence

你在 OSCP/AD 課裡學的每一種 persistence——Run key、Scheduled Task、Services、WMI subscription——在 MITRE ATT&CK 上都有對應的技術編號，也都有對應的偵測點。問題是，**攻擊者在一台機器上可能同時種下多種 persistence**，而防守方在 triage 時如果只知道幾種常見的，就會漏掉備份後門。

本章的目標是建立一張完整的地圖：每種 persistence 的植入位置在哪、遺留什麼 artifact、用什麼工具偵測、看哪個 Event ID。

## 先建立直覺：Autoruns 是防守方的第一把刀

**Sysinternals Autoruns** 是 Windows persistence 偵測的瑞士刀：它知道系統上所有可以讓程式在啟動或特定觸發時自動執行的位置，並一次性列出、驗簽、對比 VirusTotal。

```powershell
# 從 live system 跑 Autoruns（需管理員）
autorunsc.exe -a * -c -s -h > autoruns_output.csv

# 只看沒有簽名的條目
autorunsc.exe -a * -c -s -h -v -vt | Where-Object { $_ -match "No signature" }
```

Autoruns 涵蓋：Run key、Services、Scheduled Tasks、Browser Extensions、Boot Execute、Winlogon、AppInit、IFEO、WMI subscriptions……幾乎所有 persistence 位置。

**Autoruns 的限制**：只看即時系統。鑑識映像要用 Velociraptor 的 `Windows.Registry.Autoruns` artifact 或手動挖各個位置。

## Persistence → 偵測點 完整對照表

以下是主要 Windows persistence 技術的完整對映。

| ATT&CK 技術 | 植入位置 / 方法 | Registry artifact | Event ID | 鑑識工具 |
|---|---|---|---|---|
| T1547.001 Run/RunOnce | `HKLM\SOFTWARE\...\Run` `HKCU\...\Run` | Run key last write time | - | RegRipper run.pl、Autoruns |
| T1547.001 Run（64/32）| `...\Wow6432Node\...\Run` | 同上 | - | RegRipper、Autoruns |
| T1547.005 Security Support Provider | `HKLM\SYSTEM\...\Control\Lsa\OSConfig\Security Packages` | SSP key | 4697（若稽核開）| Autoruns → SSPs 頁 |
| T1547.009 Shortcut Modification | Startup 資料夾 .lnk 檔 | ShellBag（資料夾瀏覽痕跡）| - | ShellBagsExplorer、dir 遞歸 |
| T1547.012 Print Processors | `HKLM\SYSTEM\...\Print\Environments\...\Print Processors` | Registry key | 7045、4697 | Autoruns → Print Monitors |
| T1053.005 Scheduled Task | `%SystemRoot%\System32\Tasks\*` XML 檔 | Task XML + Registry `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Schedule\TaskCache` | 106（建立）、200（執行）| Autoruns → Scheduled Tasks、Get-ScheduledTask |
| T1543.003 Windows Service | `HKLM\SYSTEM\CurrentControlSet\Services\<name>` | Services Registry key | 7045（新服務）、4697 | Autoruns → Services、sc query |
| T1546.003 WMI Event Subscription | WMI Repository（`%SystemRoot%\System32\wbem\Repository\`）+ 3 個 WMI class | 無直接 Registry 殘跡 | 5861（subscription 建立）| `Get-WMIObject`、Autoruns → WMI |
| T1546.007 Netsh Helper DLL | `HKLM\SOFTWARE\Microsoft\Netsh` | Registry key | - | Autoruns → Netsh Helpers |
| T1546.008 Accessibility Features（IFEO）| `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options\<sticky key exe>` | IFEO key | 4688（IFEO 觸發後的進程建立）| Autoruns → Image Hijacks |
| T1546.010 AppInit DLLs | `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Windows\AppInit_DLLs` | AppInit key | 4688（DLL 注入觸發時）| Autoruns → AppInit |
| T1546.011 Application Shimming | `%SystemRoot%\AppPatch\Custom\*.sdb` + Registry `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Custom` | AppCompatFlags key | - | Autoruns → KnownDLLs/Shims |
| T1546.015 Component Object Model Hijacking | `HKCU\SOFTWARE\Classes\CLSID\<GUID>\InprocServer32` | COM hijack key（在 HKCU）| 4688（觸發時的 COM host 進程）| Autoruns → COM Hijacks |
| T1574.002 DLL Side-Loading | 與合法應用程式同目錄的惡意 DLL | $MFT（DLL 新增）、Prefetch | 4688（載入的進程）| Sysmon Event 7（ImageLoad）|
| T1574.001 DLL Search Order Hijacking | 高搜尋優先順序目錄中的惡意 DLL | $MFT | 4688 + Sysmon 7 | Sysmon Event 7 ImageLoad |
| T1037.001 Logon Scripts | `HKCU\Environment\UserInitMprLogonScript` | UserInitMprLogonScript value | 4688（script 執行）| RegRipper |
| T1136 建立帳號（後門帳號）| SAM hive 新帳號 | SAM key 更新 | 4720（建帳）、4732（加群）| `net user`、wineventlog |
| T1505.003 Web Shell | Web 目錄下的 .aspx/.php 檔 | $MFT（檔案建立）| 4688（webserver 子進程）| $MFT diff、IIS log |
| T1176 Browser Extension | 瀏覽器設定目錄 | 檔案系統 | 4688（browser 載入時）| Autoruns → Browser Extensions |

## 各向量深挖

### 向量 1：Scheduled Task（T1053.005）

Scheduled Task 是目前最被濫用的 persistence 之一，因為它：
- 不需要服務安裝
- 可以設定觸發條件（開機、使用者登入、特定時間）
- 可以以 SYSTEM 身份跑
- 在 UI 上難被注意

**植入位置：**

```
%SystemRoot%\System32\Tasks\<TaskName>  （XML 格式的 Task 定義）
%SystemRoot%\SysWOW64\Tasks\<TaskName>  （32-bit 任務）

Registry（Task Cache）：
HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Schedule\TaskCache\Tree\<TaskName>
HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Schedule\TaskCache\Tasks\{GUID}
```

**偵測點：**

- **Event 106**（TaskScheduler Operational）：Task 被登錄，包含 Task 名稱
- **Event 200/201**：Task 開始/完成執行，含 return code
- **XML 檔案本身**：`Actions` → `Exec` → `Command` 就是跑的程式，`Trigger` 告訴你什麼時候跑
- **Autoruns**：`Scheduled Tasks` 頁，非 Microsoft 簽名的 Task 全部列出

攻擊者常用的手法是偽裝成已知系統 Task 的名稱（如 `\Microsoft\Windows\WindowsUpdate\Reboot`），或把 Task 放在 `\Microsoft\Windows\` 路徑下讓它看起來像系統 Task。

**快速 triage（Velociraptor artifact）：**

```vql
SELECT FullPath, Command, Arguments, UserId, Enabled
FROM Artifact.Windows.System.ScheduledTasks()
WHERE Command != null
  AND NOT Command =~ "(?i)C:\\Windows\\System32\\.*"
```

### 向量 2：WMI Event Subscription（T1546.003）

WMI subscription 是最隱蔽的 persistence 之一，理由：
- 完全在 WMI Repository 裡，沒有磁碟上的執行檔（如果 payload 也是 script）
- 不出現在大多數的 persistence 掃描工具裡（非 Autoruns 專業版）
- 重啟後仍然存在
- 觸發條件可以是任意 WMI 事件（包括進程建立、使用者登入）

**三個必要物件：**

1. `__EventFilter`：定義觸發條件（如「每次 explorer.exe 啟動」）
2. `__EventConsumer`：定義動作（ActiveScriptEventConsumer 或 CommandLineEventConsumer）
3. `__FilterToConsumerBinding`：把 Filter 和 Consumer 綁在一起

**偵測點：**

```powershell
# 列出所有 WMI 永久 subscription
Get-WMIObject -Namespace root\subscription -Class __EventFilter | Select Name, Query
Get-WMIObject -Namespace root\subscription -Class CommandLineEventConsumer | Select Name, CommandLineTemplate
Get-WMIObject -Namespace root\subscription -Class ActiveScriptEventConsumer | Select Name, ScriptText
Get-WMIObject -Namespace root\subscription -Class __FilterToConsumerBinding
```

- **Event 5861**（WMI-Activity Operational）：永久 subscription 建立，含 Filter、Consumer、Binding 的詳細資訊
- WMI Repository 檔案：`%SystemRoot%\System32\wbem\Repository\OBJECTS.DATA`（二進位，需 wbemtest 或 python-evtx 類工具解析）

Autoruns 的 `WMI` 頁（需選 `Options → Scan Options → Check VirusTotal.com`）可以顯示 WMI subscription。

### 向量 3：Services（T1543.003）

服務安裝需要管理員，但一旦安裝就能以 SYSTEM 開機自啟。

**偵測點：**

- **Event 7045**（System）：無法被繞過——新服務安裝一定產生 7045
- **Registry**：`HKLM\SYSTEM\CurrentControlSet\Services\<name>\ImagePath`
- **Autoruns → Services 頁**：以非 Microsoft 簽名排序

**特別注意的 ImagePath 格式：**

```
# 正常
C:\Windows\System32\svchost.exe -k netsvcs

# 可疑（非標準路徑）
C:\ProgramData\wuhelper\svc.exe

# 可疑（指向 UNC 路徑，可能是 NTLM relay 目標）
\\attacker\share\malware.exe

# 可疑（使用環境變數混淆）
%APPDATA%\Microsoft\Credentials\loader.exe
```

### 向量 4：DLL Hijacking（T1574.001/002）

DLL 劫持不在 Registry 或 Task 裡，全靠 DLL 搜尋路徑的順序：Windows 在找 DLL 時先找應用程式目錄，再找 System32、PATH 中的目錄。如果攻擊者在可寫的高優先順序目錄放了同名 DLL，就能在合法應用程式啟動時被載入。

**偵測點：**

- **Sysmon Event 7**（ImageLoaded）：記錄每次 DLL 載入，含 DLL 路徑和 hash；可以 query 「某個 process 載入了哪些 DLL？哪個 DLL 不在 System32？」
- **$MFT / $UsnJrnl**：DLL 在可疑目錄被新增的記錄
- **Autoruns → Known DLLs**：已知 DLL 的載入路徑

**常見 DLL hijacking 目標：**應用程式目錄有缺少的 DLL（用 Process Monitor 的 `NAME NOT FOUND` 篩選就能找），或 `C:\Python27\`、`C:\Perl\` 之類 PATH 裡的舊工具目錄。

### 向量 5：COM Hijacking（T1546.015）

COM Hijacking 利用 `HKCU` 覆蓋 `HKLM` 的 COM 登錄，不需要管理員。

**原理：**Windows 在解析 COM CLSID 時，先查 `HKCU\SOFTWARE\Classes\CLSID`，再查 `HKLM\SOFTWARE\Classes\CLSID`。攻擊者在 HKCU 寫入一個 CLSID 的 `InprocServer32`，指向惡意 DLL，當任何應用程式實例化這個 COM 物件時，惡意 DLL 被載入。

**偵測點：**

- **Registry**：`HKCU\SOFTWARE\Classes\CLSID\` 底下的 key（正常使用者幾乎不應該有自訂 COM 登錄）
- **Autoruns → COM Hijacks 頁**：Autoruns 有專門一頁列出在 HKCU 覆蓋 HKLM 的 COM 登錄
- **Sysmon Event 7**：DLL 載入時的路徑如果是 HKCU 對應的非標準路徑

### 向量 6：Accessibility Feature IFEO Hijacking（T1546.008）

這是在 Ch 17 提過的 Image File Execution Options 攻擊，常見 target：

| 原始程式 | 觸發條件 | 備注 |
|---|---|---|
| `sethc.exe` | 連按 Shift 五次（Sticky Keys）| 鎖定螢幕時也能觸發，不需登入 |
| `utilman.exe` | Win + U | 鎖定螢幕 Accessibility 按鈕 |
| `magnify.exe` | 鎖定螢幕放大鏡 | |
| `osk.exe` | 螢幕鍵盤 | |
| `narrator.exe` | 螢幕閱讀器 | |

這五個 exe 的 IFEO Debugger 被設定後，在登入畫面就能用，不需要任何憑證——是非常隱蔽的後門。

**偵測點：**

- **Registry**：`HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options\sethc.exe\Debugger`（值存在就是異常）
- **Autoruns → Image Hijacks 頁**：直接列出所有 IFEO Debugger 設定
- **Event 4688**：sethc.exe 被呼叫時，IFEO 讓 Debugger 先跑，4688 的 NewProcessName 是 Debugger 值裡的程式

## Persistence 偵測的系統化策略

面對一台機器，推薦的掃描順序：

### Step 1：Autoruns 快速掃（30 秒）

```powershell
autorunsc.exe -a * -c -nobanner -accepteula | ConvertFrom-Csv | 
  Where-Object { $_.Signer -notmatch "Microsoft" -and $_."Image Path" -ne "" } | 
  Select-Object Time, Entry, "Image Path", Signer | Sort-Object Time
```

輸出任何非 Microsoft 簽名的 autorun 條目。

### Step 2：Event Log 掃 7045 和 106（過去 30 天）

```powershell
Get-WinEvent -LogName System | Where-Object { $_.Id -eq 7045 } | 
  Select-Object TimeCreated, @{N="ServiceName"; E={$_.Properties[0].Value}},
                             @{N="ImagePath"; E={$_.Properties[3].Value}}
```

```powershell
Get-WinEvent -LogName "Microsoft-Windows-TaskScheduler/Operational" | 
  Where-Object { $_.Id -eq 106 } |
  Select-Object TimeCreated, @{N="TaskName"; E={$_.Properties[0].Value}}
```

### Step 3：WMI Subscription 清查

```powershell
Get-WMIObject -Namespace root\subscription -Class __FilterToConsumerBinding | 
  Select __PATH, Filter, Consumer
```

有任何輸出就是警報——正常使用者機器幾乎不應該有 WMI subscription。

### Step 4：HKCU COM Hijacks

```powershell
Get-Item "HKCU:\SOFTWARE\Classes\CLSID\*\InprocServer32" 2>$null |
  ForEach-Object { [PSCustomObject]@{Path=$_.Name; Value=(Get-ItemPropertyValue $_.PSPath "(default)")} }
```

### Step 5：RegRipper 掃所有 hive

```bash
rip.pl -r NTUSER.DAT -f ntuser > ntuser_report.txt
rip.pl -r SYSTEM -f system > system_report.txt
rip.pl -r SOFTWARE -f software > software_report.txt
```

重點看：run、services、ifeo、appinit、winlogon、uac、comhijack 等 plugin。

## 對比表格：每種 Persistence 的難度與特徵

| Persistence 類型 | 需要管理員 | 是否重啟存活 | 隱蔽程度（1-5）| Autoruns 涵蓋 | 最快偵測方法 |
|---|---|---|---|---|---|
| Run/RunOnce | HKCU 不需要 | 是 | 1（最明顯）| 是 | RegRipper run.pl |
| Scheduled Task | 視設定而定 | 是 | 2 | 是 | Event 106 + Task XML |
| Service | 需要 | 是 | 2 | 是 | Event 7045 |
| Startup 資料夾 | HKCU 不需要 | 是 | 1 | 是 | 直接 dir |
| Winlogon | 需要 | 是 | 3 | 是 | RegRipper winlogon.pl |
| IFEO Hijack | 需要 | 是 | 4 | 是（Image Hijacks 頁）| Autoruns |
| AppInit_DLLs | 需要 | 是 | 3 | 是 | RegRipper appinit.pl |
| WMI Subscription | 需要 | 是 | 5（最難）| 是（進階）| Event 5861 + PowerShell |
| COM Hijacking | 不需要（HKCU）| 是 | 4 | 是（COM Hijacks 頁）| Autoruns |
| DLL Hijacking | 視目錄權限 | 是 | 4 | 部分 | Sysmon Event 7 |
| Web Shell | 需要 Web 服務 | 是 | 4 | 否 | $MFT + IIS log |

## 踩雷

1. **只掃 HKLM 的 Run key**：HKCU 的 Run key 不需要管理員就能寫，很多攻擊者在初始 compromise 後先用 HKCU persistence 撐住，再提權植入 HKLM。HKCU 在各使用者的 NTUSER.DAT 裡，不在 HKLM，要每個帳號都查。

2. **Autoruns 的 `Hide Microsoft Entries` 陷阱**：Autoruns 預設可以用 `Options → Hide Microsoft Signed Entries` 過濾合法程式，但攻擊者如果竊取了 Microsoft 的憑證簽名（雖然罕見但不是零）或偽造簽名（Sigcheck 可以驗），這個過濾器會讓惡意條目消失。不要只靠 VirusTotal 或簽名狀態做最終判斷。

3. **WMI Subscription 清查用 PowerShell 但 namespace 錯**：WMI Event Subscription 在 `root\subscription` namespace，如果用 `Get-WMIObject` 沒有指定 `-Namespace root\subscription`，預設是 `root\cimv2`，查到的結果是空的，不代表沒有 subscription。

4. **Task Scheduler 的 XML 路徑依版本不同**：Windows 10/11 的 Task XML 在 `%SystemRoot%\System32\Tasks\`，但有些 Task（特別是舊版 Vista 時代的格式）用不同的 Registry 位置。用 `schtasks /query /fo LIST /v` 是最保險的即時枚舉方式。

5. **DLL Hijacking 偵測需要 Sysmon 才完整**：4688 不記錄 DLL 載入，只有 Sysmon Event 7（ImageLoaded）才記錄每個 DLL。沒有 Sysmon 的環境要偵測 DLL hijacking 非常困難，只能靠 $MFT 的 DLL 新增記錄和對比已知基線。

## 進階延伸

- **Velociraptor 的 Windows.Persistence.Sysinternals.Autoruns artifact**：在大規模部署環境可以遠端對數百台機器同時跑 Autoruns，輸出集中到一個地方做異常比對（比基線機器多了哪些條目）。

- **ATT&CK 矩陣的 Sub-Techniques**：T1547 下有 15+ 個 sub-technique，本章涵蓋了主要的，但還有像 Port Monitors（T1547.010）、Boot Sector（T1542.003）、LSASS Driver（T1547.008）等較少見但真實存在的。熟悉 ATT&CK 頁面的每個 sub-technique，把偵測欄位的 Data Sources 映射到你環境裡的 log source。

- **Golden Image Baseline 比對**：建立一個乾淨的 Autoruns 基線（`autorunsc -a * -c > baseline.csv`），在 IR 時拿可疑機器的 Autoruns 輸出和基線 diff，瞬間找出新增的持久化。

- **Sigma 規則對每種 Persistence 的涵蓋**：`SigmaHQ/sigma` 的 `rules/windows/` 下有針對各種 Persistence 的規則，例如 `win_susp_scheduled_task.yml`（Task scheduler 異常）、`win_wmiprvse_spawning_process.yml`（WMI consumer 執行進程）。把這些規則和本章的技術對應，就知道哪些 Persistence 已有 Sigma 規則可以直接部署。

## 本章重點整理

- Windows Persistence 向量至少 15 種以上，系統化掃描必須用 Autoruns 作為起點，再用 Event Log 和 Registry 鑑識補漏。
- 難度分層：Run key 最顯而易見（Autoruns 秒找），WMI Subscription 最隱蔽（需要 Event 5861 + PowerShell 查詢）。
- 每種 Persistence 的關鍵偵測點：7045（服務）、106（Task）、5861（WMI）、IFEO Debugger key、HKCU COM CLSID key。
- 大規模偵測策略：Autoruns 快速掃 → Event Log 7045/106 → WMI subscription 清查 → HKCU COM → RegRipper 深挖。
- ATT&CK 的 T1547/T1053/T1543/T1546 是本章的骨架，每個 sub-technique 對應一個具體的遺留 artifact 和偵測方法。

## 自我檢核

- [ ] 我能不查表說出 6 種以上的 Windows Persistence 機制，以及每種的植入位置
- [ ] 我能說出 WMI Subscription 需要哪三個 WMI 物件，以及對應的 Event ID
- [ ] 我能說出 Scheduled Task 在磁碟上的 XML 路徑，以及對應的 Event 106 在哪個 Channel
- [ ] 我能說出 IFEO Hijacking 為什麼在登入畫面就能觸發，以及哪五個 exe 是常見 target
- [ ] 給我一台機器，我能依序執行 5 個步驟做 Persistence 系統掃描
- [ ] 我能說出 COM Hijacking 為什麼不需要管理員，以及 HKCU 為什麼能覆蓋 HKLM
- [ ] 我能說出 Autoruns 的兩個主要限制

## 延伸閱讀

1. **MITRE ATT&CK Persistence Techniques** — 直接進 [https://attack.mitre.org/tactics/TA0003/](https://attack.mitre.org/tactics/TA0003/) 看完所有 Persistence sub-technique 的 Procedure Examples 和 Detection Data Sources；把每個 Data Source 映射到你有哪些 log 覆蓋，知道自己的盲點在哪。

2. **[Atomic Red Team — T1053/T1543/T1546](https://github.com/redcanaryco/atomic-red-team)** — 每種 Persistence 技術都有現成的 Atomic Tests，按 ATT&CK 技術編號找；最好的學習方式是在 VM 裡執行 Atomic Test，再用本章工具去抓痕跡，親自驗證偵測邏輯。

3. **SANS FOR500 — Windows Forensic Analysis Section 4（Persistence）** — SANS 的 FOR500 課程 Section 4 專門講 Registry 和 Task Scheduler persistence 的鑑識；和本章對照，把偵測（即時）和鑑識（事後映像分析）兩種視角都學到。

4. **[The DFIR Report — Persistence 案例](https://thedfirreport.com/)** — 搜尋 DFIR Report 裡用到多種 Persistence 的 Ransomware 案例（如 Conti、BlackBasta），看職業 IR 如何在磁碟映像和 Event Log 裡找到所有植入點，特別是「攻擊者種了 Run key 同時又種了 Scheduled Task 作為備份」的案例。

5. **[Sysinternals Autoruns 文件](https://docs.microsoft.com/en-us/sysinternals/downloads/autoruns)** — 特別是 command-line options 和各個 Tab 的涵蓋範圍說明；Autoruns 不只是 GUI 工具，`autorunsc.exe` 的 CSV 輸出可以整合進 Velociraptor/SIEM 自動化流程。

---

Persistence 地圖建立完了。下一章進入攻擊鏈的最高價值段：攻擊者拿了憑證、橫向移動到另一台機器——在這個過程中，你身為紅隊知道用了哪些工具、哪些技術；現在反轉視角，看每一步在防守方的 log 和 artifact 裡留下什麼。

→ [Ch 20 憑證竊取與橫向移動鑑識](./20-credential-lateral-movement-forensics.md)
