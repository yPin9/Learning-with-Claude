# Ch 20 — 憑證竊取與橫向移動鑑識

> 目標：能從 Sysmon、Security Event Log、記憶體 artifact 中識別 LSASS dump、Mimikatz 執行痕跡、Kerberos 攻擊（Kerberoasting/Golden Ticket/Pass-the-Ticket）、PtH 的 Event 特徵，以及 PsExec/WMIC/RDP/SMB 橫向移動工具在目標機器留下的鑑識痕跡；並能把這些串成完整的 AD 攻擊鏈。
>
> 環境：Windows Domain 環境（攻擊者機器 + DC + 目標工作站）；工具 Sysmon、Volatility3、Windows Event Log；所有輸出標「（示意，依版本/樣本而異）」。

## 為什麼憑證竊取與橫向移動是攻擊鏈的核心

你在 AD 課或 OSCP 做過完整的攻擊鏈：取得初始落腳點 → 本地提權 → LSASS dump → Mimikatz 萃取 hash → Pass-the-Hash 橫向移動到 DC → DCSync 拿到所有 hash。整個過程不超過 30 分鐘。

現在換邊：防守方有什麼？

- 每個步驟都在 log 和記憶體裡留下痕跡
- 關鍵在於知道哪個 artifact 對應哪個攻擊步驟
- 光靠 AV 是不夠的——Mimikatz 過了 Defender 一樣可以跑；但 Sysmon Event 10（LSASS handle）是一定留的

這章把攻擊鏈的每一段都對映到防守方的偵測點。

## 先建立直覺：攻擊鏈全圖

```
初始落腳點（phishing/exploit）
    │
    ▼
本地 Shell → 提權（UAC bypass / token impersonation）
    │
    ▼
憑證竊取
├── LSASS dump → Mimikatz sekurlsa::logonpasswords
├── NTDS.dit dump（DCSync）
└── Kerberoasting / AS-REP Roasting
    │
    ▼
橫向移動
├── PtH（NTLM hash）
├── PtT（Kerberos ticket）
├── PsExec / WMIC / WMI / RDP / SMB
└── Golden Ticket / Silver Ticket
    │
    ▼
目標（DC、高價值服務器）
    │
    ▼
目標達成（ransomware / 資料外洩 / 長期駐留）
```

## LSASS Dump 偵測

### LSASS 是什麼目標

LSASS（Local Security Authority Subsystem Service，`lsass.exe`）是 Windows 的憑證守門員，記憶體裡常駐：

- 當前已登入使用者的 NTLM hash
- Kerberos TGT（明文 key）
- 可能的明文密碼（在舊版 WDigest 設定下）
- 域快取憑證（Domain Cached Credentials）

攻擊者有兩條路：**進程 dump**（產生 minidump 檔再在攻擊機離線 parse）或**直接注入讀記憶體**（Mimikatz 直接在目標機跑）。

### Sysmon Event 10：進程存取

當任何進程嘗試讀取 LSASS 記憶體，Sysmon Event 10（ProcessAccess）被觸發：

```xml
<EventData>
  <Data Name="SourceImage">C:\Users\attacker\Downloads\procdump64.exe</Data>
  <Data Name="TargetImage">C:\Windows\System32\lsass.exe</Data>
  <Data Name="GrantedAccess">0x1FFFFF</Data>   <!-- 或 0x1010 / 0x1038 -->
  <Data Name="CallTrace">C:\Windows\SYSTEM32\ntdll.dll+...</Data>
</EventData>
```

**GrantedAccess 的意義：**

| Access Mask | 意義 | 是否可疑 |
|---|---|---|
| `0x1FFFFF` | PROCESS_ALL_ACCESS（完全存取）| 極可疑 |
| `0x1010` | PROCESS_VM_READ + PROCESS_QUERY_LIMITED_INFORMATION | 極可疑（dump 最低需求）|
| `0x1038` | PROCESS_VM_READ + QUERY_INFO + SUSPEND_RESUME | 可疑 |
| `0x0400` | PROCESS_QUERY_INFORMATION（只查詢，不讀記憶體）| 通常合法（如工作管理員）|

**合法程序也會存取 LSASS：**AV、EDR、Process Monitor 自身都會 open LSASS handle，所以不能看到任何 LSASS 存取就報警。Sigma 規則的做法是：GrantedAccess mask 包含 `0x10`（VM_READ）+ SourceImage 不在白名單（`MsMpEng.exe`、`csrss.exe` 等）→ 才觸發。

### Minidump 檔案 artifact

用 ProcDump 或 Task Manager 建立的 minidump 是一個 .dmp 檔：

```powershell
procdump64.exe -accepteula -ma lsass.exe lsass.dmp
```

**$MFT 和 $UsnJrnl 會記錄 lsass.dmp 的建立**。文件系統層面幾乎無法隱藏：即使攻擊者立刻刪除，$UsnJrnl 還是有 CREATE 和 DELETE 的連續記錄，且 hash 可能在 Defender 的 quarantine log 裡。

### 4688 的進程樹

Sysmon Event 10 + 4688 的 parent-child 關係（示意，依版本/樣本而異）：

```
cmd.exe（初始 shell）
  └── procdump64.exe -ma lsass.exe lsass.dmp   [4688 + Sysmon 10]
  └── mimikatz.exe                              [4688 + Sysmon 1]
```

如果看到 cmd.exe 或 powershell.exe 下的 procdump64.exe 對 lsass.exe 發起存取，不需要 Sysmon 也能靠 4688 + 7 (Sysmon ImageLoad) 組合識別。

### Task Manager LSASS Dump（無工具）

Windows 工作管理員也可以 dump LSASS（右鍵 → 建立傾印檔案），會在 `%TEMP%\lsass.DMP` 建立一個 minidump，且用 `taskmgr.exe` 作為 SourceImage——這個在白名單上，Sigma 規則通常會例外排除，是攻擊者繞過偵測的技巧之一。

## Mimikatz 痕跡

Mimikatz 是憑證竊取工具中最著名的，也是 IoC（Indicator of Compromise）最明顯的之一——如果攻擊者不做混淆的話。

### AV / Defender 偵測

Mimikatz 原始 binary 幾乎秒被 Defender 殺。攻擊者的對策：

1. 記憶體執行（`IEX (New-Object Net.WebClient).DownloadString(...)`）
2. 混淆工具（Invoke-Obfuscation、AMSI bypass）
3. 自定義改版（改字串特徵、重新編譯）

### PowerShell 4104 Script Block

即使 Mimikatz 以 PowerShell 執行（Invoke-Mimikatz），4104 Script Block Logging 會記錄解混淆後的指令（示意，依版本/樣本而異）：

```
EventID: 4104
ScriptBlockText:
  $m = [System.Reflection.Assembly]::Load($MimikatzBytes)
  ...
  sekurlsa::logonpasswords
```

Windows 10+ 的「Protected Event Logging」對包含 `mimikatz`、`sekurlsa`、`lsadump` 等字串的 script block 強制記錄，即使沒開 Script Block Logging GPO。

### Sysmon Event 1 的進程名稱

如果攻擊者直接跑 `mimikatz.exe`，Sysmon Event 1 的 CommandLine 欄位會含有 `mimikatz`（當然，rename 後就不行了）。但更重要的是 OriginalFileName（從 PE 版本資訊萃取）：即使攻擊者把 mimikatz.exe 重命名為 `svchost.exe`，Sysmon Event 1 還是會記錄原始的 OriginalFileName = `mimikatz.exe`。

Sysmon Event 1（示意，依版本/樣本而異）：

```xml
<Data Name="Image">C:\Users\attacker\svchost.exe</Data>
<Data Name="OriginalFileName">mimikatz.exe</Data>
<Data Name="CommandLine">svchost.exe privilege::debug sekurlsa::logonpasswords</Data>
<Data Name="ParentImage">C:\Windows\System32\cmd.exe</Data>
```

### LSASS Handle：進程特有行為

Mimikatz 的 `sekurlsa::logonpasswords` 具體動作：
1. `OpenProcess(PROCESS_ALL_ACCESS, lsass.exe)` → Sysmon 10
2. `ReadProcessMemory` 讀取 LSASS 記憶體
3. 在記憶體裡 parse WDIGEST/Kerberos/NTLM 結構

這個 ReadProcessMemory 的 CallTrace（Sysmon 10 欄位）包含 ntdll.dll 的 call stack，合法工具（如 Defender）的 CallTrace 會包含已知的 defender DLL 路徑，而 Mimikatz 的 CallTrace 更「乾淨」（只有 ntdll）。

## Kerberos 攻擊鑑識

### Kerberoasting（T1558.003）

你在 AD 課做過：`Invoke-Kerberoast` 或 `impacket-GetUserSPNs` 請求帶有 SPN 的服務帳號的 TGS，然後離線爆破。

**DC 端的 4769 特徵（Ch 18 已介紹）：**

| 正常 | Kerberoasting |
|---|---|
| TicketEncryptionType: 0x12（AES256）| TicketEncryptionType: 0x17（RC4-HMAC）|
| 少量請求 | 短時間大量 4769（掃描所有 SPN 帳號）|
| 合理的 ServiceName | 所有帶 SPN 的服務帳號 |

4769 的 `FailureCode: 0x0`（成功）+ `TicketEncryptionType: 0x17` 是最強的 Kerberoasting 指標。

**為什麼 RC4 會暴露 Kerberoasting？**

現代 AD 環境設定了「服務帳號支援 AES」後，合法客戶端會請求 AES256（0x12）ticket。Kerberoasting 工具預設請求 RC4（0x17），因為 RC4 ticket 可以用 hashcat 以每秒數百萬次的速度爆破，而 AES256 慢了一個數量級以上。如果環境是 DC 強制 AES，攻擊者也可以強制請求 AES256 ticket，但那樣就不容易爆破了——這是防禦建議的依據。

**Sigma 規則邏輯：**

```yaml
detection:
  selection:
    EventID: 4769
    TicketOptions: '0x40810000'   # Kerberoasting 常見 flag
    TicketEncryptionType: '0x17'
  condition: selection
```

### AS-REP Roasting（T1558.004）

針對沒有開啟「Kerberos pre-authentication」的帳號，直接發送 AS-REQ 而不需密碼，DC 會直接回傳加密的 AS-REP，可離線爆破。

**DC 端的 4768 特徵：**

```
EventID: 4768
ClientAddress: 10.0.0.42（攻擊機）
PreAuthType: 0（不需要 pre-auth，即這個帳號關閉了 pre-auth）
Status: 0x0（成功）
EncryptionType: 0x17（RC4）
```

`PreAuthType: 0` 意味著帳號設定了不需要 pre-auth——這本身就是一個設定異常，應該 alert。

### Golden Ticket（T1558.001）

Golden Ticket 用 krbtgt 帳號的 NTLM hash 偽造任意的 TGT，可以偽裝成任何使用者、任何群組。

**為什麼難偵測：**

- 不需要發送 AS-REQ 到 DC（所以 DC 沒有 4768）
- 偽造的 TGT 直接用於請求 TGS（4769 會有，但看不出異常）
- 可以設定任意有效期（正常 TGT 最多 10 小時，攻擊者可以設 10 年）

**偵測線索：**

1. **4769 without 4768**：在 DC 的 Security log 看到 4769 但找不到對應的 4768——說明 TGT 不是從 DC 正常取得的
2. **Ticket 有效期異常**：正常 TGT 最多 MaxTicketAge（預設 10 小時），Golden Ticket 的 TGT 有效期可能遠超過 domain policy
3. **4624 的 LogonGuid**：Golden Ticket 的 LogonGuid（在 DC 端的 4624）通常全零或不符合預期格式

實務上 Golden Ticket 極難從 log 確定識別，主要靠行為異常（使用者突然存取大量敏感資源）或 EDR 的 Kerberos ticket 解析。

### Pass-the-Ticket（T1550.003）

PtT 把一個合法的 Kerberos ticket（通常是從記憶體或 .kirbi 檔案萃取的 TGT 或 TGS）注入另一個進程的 Kerberos ticket cache，讓那個進程以 ticket 的身份存取服務。

**Mimikatz 的操作：**

```
kerberos::ptt ticket.kirbi
klist  → 確認 ticket 已注入
dir \\DC\C$  → 用注入的 ticket 存取
```

**偵測點：**

- **Sysmon Event 10**（LSASS access）：從 LSASS 萃取 ticket（`sekurlsa::tickets /export`）需要存取 LSASS
- **Windows Kerberos log（Security 4648）**：以 alternate credentials 登入的 Event
- **網路流量**：PtT 後的存取產生正常的 SMB/TGS 流量，但 source 是不尋常的帳號

## PtH（Pass-the-Hash）鑑識

Ch 18 已介紹 PtH 的 4624 特徵。這裡補完：

### DC 端的 4776

當攻擊者用 PtH 連到另一台機器，目標機器用 NTLM 向 DC（或本機 SAM）驗證。如果是域帳號，DC 的 Security log 會有 4776：

```
EventID: 4776
PackageName: MICROSOFT_AUTHENTICATION_PACKAGE_V1_0
LogonAccount: admin
Workstation: TARGET-PC
Status: 0x0（成功）
```

**PtH 的識別難點**：4776 本身看起來像正常 NTLM 認證，沒辦法只靠 4776 判斷是 PtH 還是正常密碼登入。需要交叉比對：

- 同一個帳號平時用 Kerberos（4769），突然改用 NTLM → 可疑
- 來源 IP 是工作站，但工作站那端沒有對應使用者的互動 session → 可疑
- LogonType 3 + NTLM + 沒有 4648（Run As）→ 橫向移動模式

### NTLM Relay 的特徵

NTLM Relay（用 Responder + impacket-ntlmrelayx）攔截 NTLM 認證後轉發。在目標機器看到：

```
EventID: 4624
LogonType: 3
AuthenticationPackageName: NTLM
WorkstationName: RESPONDER-PC  （不是真正的工作站名稱）
IpAddress: 10.0.0.99  （Responder 的 IP）
```

WorkstationName 和 IpAddress 對不上，或 IpAddress 是內網非預期主機，是 Relay 的信號。

## 橫向移動工具鑑識

### PsExec（T1569.002）

目標機器的痕跡（已在 Ch 18 提過，這裡補完）：

1. **7045（System）**：`PSEXESVC` 服務安裝，`ImagePath: %SystemRoot%\PSEXESVC.exe`
2. **4624（Security）**：Type 3 NTLM 登入（或 Kerberos，視環境而定）
3. **4688（Security）**：`PSEXESVC.exe` 建立，然後 `cmd.exe` 或指定命令以 SYSTEM 執行
4. **$MFT**：`%SystemRoot%\PSEXESVC.exe` 的建立時間

Sysmon 的進程樹（示意，依版本/樣本而異）：

```
services.exe
  └── PSEXESVC.exe
        └── cmd.exe /c whoami
              └── whoami.exe
```

PSEXESVC.exe 是 services.exe 的子進程，而 cmd.exe 又是 PSEXESVC.exe 的子進程——這個進程樹幾乎就是 PsExec 的指紋。

**攻擊者的變體**：使用 `-r <ServiceName>` 修改服務名稱，把 PSEXESVC 改成別的名字，但進程樹結構不變，7045 的服務安裝 Event 仍然存在。

### WMIC 橫向移動（T1047）

攻擊者機器：

```
wmic /node:10.0.0.50 /user:admin /password:P@ss process call create "cmd.exe /c ..."
```

目標機器的 artifact：

1. **4688（Security）**：`WmiPrvSE.exe` 建立子進程（cmd.exe / powershell.exe）
2. **Sysmon Event 1**：完整的 parent-child 進程樹：`WmiPrvSE.exe` → 指令

WMI 遠端執行的特徵：`WmiPrvSE.exe` 是 parent，但 WmiPrvSE.exe 不應該有互動式的子 Shell。這個「WmiPrvSE.exe spawning cmd.exe/powershell.exe」是成熟的 Sigma 規則（`win_wmiprvse_spawning_process.yml`）。

4624 Type 3 登入在 WMI 遠端執行之前產生（WMI 需要先認證）。

### RDP（T1021.001）

目標機器的 artifact：

1. **4624 Type 10（Security）**：RemoteInteractive logon
2. **TerminalServices-LocalSessionManager Operational Event 21**：Session 建立，含 source IP
3. **TerminalServices-RemoteConnectionManager Operational**：更詳細的連線資訊
4. **$NTUSER.DAT / Shellbags**：使用者（攻擊者）瀏覽的資料夾
5. **Prefetch**：攻擊者在 RDP session 裡跑的程式的 Prefetch 記錄（如 mimikatz.exe-XXXXXX.pf）

**RDP 特徵 vs 本地登入：**

| 特徵 | RDP（Type 10）| 本地（Type 2）|
|---|---|---|
| LogonProcessName | User32 | User32 |
| WorkstationName | 空白（RDP session 本身）| 本機名稱 |
| IpAddress | 攻擊者 IP | 本地（127.0.0.1）|
| 對應 TermSvc Event | 21（Session 建立）| 無 |

### SMB 橫向移動（T1021.002）

直接用 SMB 存取共用資料夾複製 payload、執行遠端服務，或用 `sc.exe \\target` 建立服務。

目標機器：

```
EventID: 4624  Type 3  NTLM 或 Kerberos
EventID: 5140  （Security，Object Access）：網路共用被存取
EventID: 5145  （Security）：共用下的特定物件被存取（需開啟 Detailed File Share 稽核）
```

5140/5145 需要在 GPO 啟用「Audit Detailed File Share」，預設不開，但對 SMB 橫向移動偵測很有用。

## 完整攻擊鏈 Artifact 對映

情境：攻擊者從 phishing email 取得 foothold，逐步到 DC。

| 攻擊步驟 | ATT&CK | 目標機器 artifact | Event ID |
|---|---|---|---|
| 初始 phishing（.docm）| T1566 | 4688（winword.exe → powershell.exe）、4104 | 4688、4104 |
| Download payload | T1105 | Sysmon 3（網路連線）、$MFT（檔案建立）| Sysmon 3 |
| LSASS dump | T1003.001 | Sysmon 10（lsass 存取）、$MFT（.dmp 建立）| Sysmon 10 |
| Mimikatz parse | T1003.001 | Sysmon 1（mimikatz.exe / OriginalFileName）、4104 | Sysmon 1、4104 |
| Kerberoasting | T1558.003 | DC 端 4769（EncType 0x17）| 4769（DC）|
| PtH 橫向到 Workstation | T1550.002 | 目標機 4624 Type 3 NTLM、4776（DC）| 4624、4776 |
| PsExec 安裝 | T1569.002 | 7045（PSEXESVC）、4688（SYSTEM cmd）| 7045、4688 |
| Run key Persistence | T1547.001 | Registry Run key last write time | （Registry，無 Event）|
| DCSync | T1003.006 | DC 端 4662（Directory Service Access with Replication permission）| 4662（DC）|

**4662 – DCSync 的 DC 端信號：**

DCSync（`lsadump::dcsync`）模擬域控制器的複製行為，從 DC pull 所有帳號 hash。DC 端的 Security log 會有：

```
EventID: 4662
Object Type: domainDNS
Properties: {1131f6aa-...}  (Replicating Directory Changes)
             {1131f6ad-...}  (Replicating Directory Changes All)
AccountName: attacker_user   （不應該有這個權限的一般使用者）
```

一般使用者帳號對 domainDNS 物件發出 replication 請求是明確的 DCSync 指標。

## 踩雷

1. **Sysmon Event 10 誤報率高**：大量合法工具（AV、EDR、Process Monitor）存取 LSASS，如果 Sigma 規則沒有仔細白名單，Event 10 的 alert 量會讓 SOC 淹沒。正確做法：先建立「合法工具 LSASS 存取的 GrantedAccess 模式」基線，只 alert GrantedAccess mask 包含 `0x10`（VM_READ）且 source 不在白名單的。

2. **Golden Ticket 沒有確鑿的 log 證據**：現有的偵測方法（4769 無對應 4768、ticket 有效期異常）都是間接信號，且需要完整的 log 留存。如果 DC 的 Security log 已被清除或保留期太短，Golden Ticket 幾乎不可能靠 log 追溯。最有效的 Golden Ticket 偵測是 EDR 解析 Kerberos ticket 結構，而不是靠 Event Log。

3. **PtH 和正常 NTLM 登入的區分**：很多企業環境仍然大量使用 NTLM，每個 NTLM Type 3 登入都單獨看沒有意義。需要建立「這台機器 / 這個帳號的正常認證協議基線」，NTLM 比例突然升高才是信號。

4. **Kerberoasting 的 RC4 ticket 請求不一定是攻擊**：老舊系統或舊版 Kerberos 客戶端可能合法地請求 RC4 ticket。要考慮環境基線——如果一直都有 0x17 的 4769，突然增加的量才是信號，不是 0x17 本身。

5. **DCSync 的 4662 需要明確開啟稽核**：4662（Directory Service Access）需要 GPO 開啟「Audit Directory Service Access」才會記錄，而且預設只記錄 SACL（System Access Control List）有設定的物件存取。必須確認 DC 的 GPO 設定了 `domainDNS` 物件的 SACL，否則 DCSync 不留任何 4662。

## 進階延伸

- **Windows Credential Guard**：Windows 10/11 Enterprise 的 Credential Guard 把 LSASS 的憑證放進 VTL1（Secure World），Mimikatz 的 `sekurlsa::logonpasswords` 無法直接讀取——但 Kerberoasting 和 DCSync 仍然有效，因為它們不需要讀 LSASS。理解這個限制有助於設計更完整的防禦。

- **NTLM Relay 與 SMB Signing**：強制 SMB Signing 可以完全阻止 NTLM Relay 攻擊（因為 relay 後的連線無法偽造簽名）。從鑑識角度，NTLM Relay 在目標機器的 4624 的 WorkstationName 通常是 Responder 機器名，和正常 workstation 對不上。

- **Kerberos Delegation 濫用**：Unconstrained Delegation（T1558.001 的變體）允許伺服器代表任何使用者請求任意服務的 TGS，可以配合 PrinterBug 強迫 DC 連到設定了 Unconstrained Delegation 的機器，TGT 直接送上門。4769 的分析邏輯相同，但來源是 DC 帳號（`DC$`）的 TGT，是明確的異常。

- **記憶體鑑識確認憑證竊取**：Volatility3 的 `windows.lsadump` 和 `windows.hashdump` plugin 可以從記憶體 dump 萃取和 Mimikatz 相同的 hash；如果懷疑攻擊者做了 LSASS dump，可以自己跑一次相同操作確認哪些 hash 被暴露，這對後續的 credential reset scope 很重要。

## 本章重點整理

- LSASS dump 偵測核心：Sysmon Event 10 的 TargetImage=lsass.exe + GrantedAccess 含 `0x10`（VM_READ）；OriginalFileName 可以識別重命名的 Mimikatz。
- Kerberoasting：DC 端 4769 的 `TicketEncryptionType=0x17`（RC4），短時間大量請求。
- Golden Ticket：4769 無對應 4768（TGT 是偽造的，不經 DC），ticket 有效期超過 policy。
- PtH：目標機 4624 Type 3 + NTLM + WorkstationName 異常；DC 端 4776。
- PsExec：目標機 7045（PSEXESVC）+ 4688 的 services.exe → PSEXESVC.exe → cmd.exe 進程樹。
- WMI 橫向移動：WmiPrvSE.exe spawning cmd/powershell（Sysmon 1）+ 4624 Type 3。
- DCSync：DC 端 4662 含 Replication Directory Changes，來源是非 DC 帳號。

## 自我檢核

- [ ] 我能說出 LSASS dump 在 Sysmon Event 10 裡的哪個欄位能識別存取類型，以及哪些 GrantedAccess mask 是可疑的
- [ ] 我能說出為什麼 OriginalFileName（Sysmon Event 1）可以識別改名的 Mimikatz
- [ ] 給我 DC 的 Security log，我能從 4769 的哪個欄位識別 Kerberoasting
- [ ] 我能說出 Golden Ticket 為什麼在 DC 端沒有 4768，以及用什麼替代信號識別
- [ ] 我能說出 PsExec 在目標機器產生的三個 Event 的 Channel 和時序
- [ ] 我能說出 DCSync 在 DC 端觸發哪個 Event ID，需要開啟哪個稽核設定
- [ ] 我能說出 Credential Guard 阻擋了哪種攻擊，哪種攻擊仍然有效

## 延伸閱讀

1. **SANS FOR508 — Advanced Incident Response** — Section 5 和 6 深入講 Active Directory 攻擊的鑑識，含 Golden Ticket、DCSync 的 DC log 分析，是本章的直接延伸；SANS 的 FOR508 Lab 有完整的 AD 攻擊鏈 triage 練習，強烈建議配合做。

2. **[The DFIR Report — Active Directory 攻擊鏈案例](https://thedfirreport.com/)** — 搜尋「Kerberoasting」或「mimikatz」，看職業 IR 如何從 4769 + Sysmon 10 + PsExec 7045 還原完整 AD 攻擊鏈；特別注意他們如何處理 log 被清除後的 artifact 拼湊。

3. **[SpecterOps — Kerberos Attacks Reference](https://posts.specterops.io/kerberosattacks)** — SpecterOps 的 Kerberos 攻擊技術細節；從攻擊者視角理解 Kerberoasting/Golden/Silver/Diamond Ticket 的差異，對設計偵測邏輯很有幫助。

4. **[Microsoft — Monitoring Active Directory for Compromise](https://docs.microsoft.com/en-us/windows-server/identity/ad-ds/plan/security-best-practices/monitoring-active-directory-for-signs-of-compromise)** — Microsoft 官方的 AD 監控建議，含每個 Event ID 的詳細說明和建議的監控閾值；把這份文件的 Event ID 清單對照本章，確認你的環境都有開啟對應稽核。

5. **Impacket 原始碼（secretsdump.py）** — 直接看 [impacket/examples/secretsdump.py](https://github.com/SecureAuthCorp/impacket) 的 DCSync 實作，理解它用了哪些 DRSUAPI API Call；理解攻擊的技術細節才能理解為什麼 4662 的 `Replicating Directory Changes` property 是關鍵指標。

---

Windows DFIR 的四個核心章節（Registry/Event Log/Persistence/Credential & Lateral Movement）已完整。現在把這些拼在一起，做一個完整的被入侵主機 triage 練習：從 $MFT + Event Log + Registry hive + 記憶體 artifact，重建攻擊者從初始感染到橫向移動的完整時間軸。

→ [練習 B：Windows 被入侵主機完整 triage + timeline](./practice-b-windows-triage-timeline.md)
